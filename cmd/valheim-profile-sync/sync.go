package main

import (
	"archive/zip"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"net/http"
	"net/url"
	"os"
	stdpath "path"
	"path/filepath"
	"strings"
	"time"
)

const (
	maxProfileArchiveBytes = int64(512 << 20)
	maxPackageArchiveBytes = int64(512 << 20)
	maxArchiveEntryBytes   = int64(512 << 20)
	maxArchiveTotalBytes   = int64(1 << 30)
	packageRepositoryURL   = "https://gcdn.thunderstore.io/live/repository/packages/"
	stateFilename          = "state.json"
	generationStateFile    = ".valheim-profile-sync-state.json"
)

type packageDefinition struct {
	Namespace string `json:"namespace"`
	Name      string `json:"name"`
	Version   string `json:"version"`
	Filename  string `json:"filename"`
	SHA256    string `json:"sha256"`
	Size      int64  `json:"size"`
}

type companionDefinition struct {
	Filename string `json:"filename"`
	SHA256   string `json:"sha256"`
	Size     int64  `json:"size"`
}

type profileDefinition struct {
	Schema     int                  `json:"schema"`
	World      string               `json:"world"`
	Profile    string               `json:"profile"`
	ClientType string               `json:"client_type"`
	Packages   []packageDefinition  `json:"packages"`
	Companion  *companionDefinition `json:"companion,omitempty"`
}

type profileState struct {
	Schema                  int                 `json:"schema"`
	World                   string              `json:"world"`
	Profile                 string              `json:"profile"`
	ClientType              string              `json:"client_type"`
	ReleaseID               string              `json:"release_id"`
	ProfileSHA256           string              `json:"profile_sha256"`
	ProfileSize             int64               `json:"profile_size"`
	ConfigSHA256            string              `json:"config_sha256"`
	ConfigLayout            string              `json:"config_layout"`
	Packages                []packageDefinition `json:"packages"`
	RuntimeSHA256           string              `json:"runtime_sha256,omitempty"`
	RuntimeSize             int64               `json:"runtime_size,omitempty"`
	CompanionSHA256         string              `json:"companion_sha256,omitempty"`
	CompanionSize           int64               `json:"companion_size,omitempty"`
	DiagnosticsPluginSHA256 string              `json:"diagnostics_plugin_sha256,omitempty"`
	DiagnosticsPluginSize   int64               `json:"diagnostics_plugin_size,omitempty"`
	DiagnosticsToken        string              `json:"diagnostics_token,omitempty"`
	DiagnosticsEndpoint     string              `json:"diagnostics_endpoint,omitempty"`
}

type packageChanges struct {
	Added     int
	Updated   int
	Removed   int
	Unchanged int
}

func (changes packageChanges) Detail() string {
	return fmt.Sprintf("Mods: %d added, %d updated, %d removed, %d unchanged.", changes.Added, changes.Updated, changes.Removed, changes.Unchanged)
}

type profileSyncer struct {
	HTTPClient          *http.Client
	LocalAppData        string
	GameDir             string
	Progress            progressReporter
	DiagnosticsToken    string
	DiagnosticsEndpoint string
}

func newProfileSyncer(httpClient *http.Client) *profileSyncer {
	if httpClient == nil {
		httpClient = &http.Client{Timeout: time.Minute}
	}
	return &profileSyncer{HTTPClient: httpClient}
}

func (syncer *profileSyncer) synchronize(ctx context.Context, request profileRequest) (bool, error) {
	portal, err := newPortalClient(request, syncer.HTTPClient)
	if err != nil {
		return false, err
	}
	portal.Progress = syncer.Progress
	token, err := portal.authorize(ctx, request)
	if err != nil {
		return false, err
	}
	syncer.DiagnosticsToken = portal.diagnosticsToken
	syncer.DiagnosticsEndpoint = portal.endpoint("client", "diagnostics", request.World, request.Profile, request.ClientType)
	return syncer.syncAuthorized(ctx, request, token)
}

func (syncer *profileSyncer) syncAuthorized(ctx context.Context, request profileRequest, token string) (bool, error) {
	if err := request.validate(); err != nil {
		return false, err
	}
	if len(token) < 16 || strings.ContainsAny(token, "\r\n") {
		return false, errors.New("invalid authorization token")
	}
	localAppData := syncer.LocalAppData
	if localAppData == "" {
		var err error
		localAppData, err = localApplicationData()
		if err != nil {
			return false, err
		}
	}
	root, err := profileRoot(localAppData, request)
	if err != nil {
		return false, err
	}
	lock, err := acquireProfileLock(root)
	if err != nil {
		return false, err
	}
	defer lock.Close()

	portal, err := newPortalClient(request, syncer.HTTPClient)
	if err != nil {
		return false, err
	}
	report(syncer.Progress, progressUpdate{Stage: "Checking the published profile", Detail: "Comparing your local profile with the approved release.", Percent: 18})
	manifest, err := portal.fetchManifest(ctx, request, token)
	if err != nil {
		return false, err
	}
	// Name the release in the activity log the moment the portal reports it, so a
	// player reading the log (or pasting it into a bug report) can see which
	// version this run is working with before anything is downloaded or replaced.
	report(syncer.Progress, progressUpdate{
		Stage:   "Approved release " + manifest.Version,
		Detail:  fmt.Sprintf("Installing %s for %s (%s). Release ID %s.", manifest.Profile, manifest.World, manifest.ClientType, manifest.ReleaseID),
		Percent: 20,
	})
	current, present, err := loadProfileState(root)
	if err != nil {
		return false, err
	}
	if present && validProfileState(current, request) && stateMatchesManifest(current, manifest) && activeGenerationMatches(root, current, request) && activeProfileRuntimeReady(root, current) && activeProfileConfigMatches(root, current) {
		if current.DiagnosticsToken != syncer.DiagnosticsToken || current.DiagnosticsEndpoint != syncer.DiagnosticsEndpoint {
			current.DiagnosticsToken = syncer.DiagnosticsToken
			current.DiagnosticsEndpoint = syncer.DiagnosticsEndpoint
			if err := writeProfileState(root, current); err != nil {
				return false, err
			}
		}
		if err := repairLoadTimeProfilerPatcher(root); err != nil {
			return false, fmt.Errorf("repair LoadTimeProfiler patcher: %w", err)
		}
		if err := removeRetiredDragonRiders(root); err != nil {
			return false, fmt.Errorf("remove retired DragonRiders package: %w", err)
		}
		gameDir, gameErr := validateSteamValheimDirectory(syncer.GameDir)
		if request.ClientType == clientVR && gameErr != nil {
			return false, gameErr
		}
		if gameErr == nil {
			runtimeSource := ""
			if request.ClientType == clientVR {
				runtimeSource = filepath.Join(root, "active", "runtime")
			}
			if err := reconcileRuntimeOverlay(gameDir, localAppData, request, manifest.ReleaseID, runtimeSource); err != nil {
				return false, err
			}
		}
		report(syncer.Progress, progressUpdate{Stage: "Profile is already current", Detail: "No files need to be downloaded.", Percent: 72})
		return false, nil
	}

	report(syncer.Progress, progressUpdate{Stage: "Downloading profile definition", Detail: "Getting the approved profile details.", Percent: 28})
	payload, err := portal.profilePayloadRequest(ctx, request, token)
	if err != nil {
		return false, err
	}
	profileArchive, downloadErr := downloadVerified(payload.Body, root, ".profile-", manifest.ProfileSize, manifest.ProfileSHA256, maxProfileArchiveBytes)
	payload.Body.Close()
	if downloadErr != nil {
		return false, downloadErr
	}
	defer os.Remove(profileArchive)

	configStage, err := os.MkdirTemp(root, ".config-")
	if err != nil {
		return false, err
	}
	defer os.RemoveAll(configStage)
	report(syncer.Progress, progressUpdate{Stage: "Verifying profile definition", Detail: "Checking the selected profile before applying it.", Percent: 42})
	definition, err := unpackProfileDefinition(profileArchive, configStage, request)
	if err != nil {
		return false, err
	}
	if err := validateCompanionDefinition(definition, manifest); err != nil {
		return false, err
	}

	next, err := os.MkdirTemp(root, ".next-")
	if err != nil {
		return false, err
	}
	defer os.RemoveAll(next)
	cache := filepath.Join(root, "packages")
	changes := classifyPackageChanges(current.Packages, definition.Packages)
	companionChanged := request.ClientType == clientFlat && (!present || !strings.EqualFold(current.CompanionSHA256, manifest.CompanionSHA256) || current.CompanionSize != manifest.CompanionSize)
	detail := changes.Detail()
	if companionChanged {
		detail += " Flat ValheimVR companion changed."
	}
	report(syncer.Progress, progressUpdate{Stage: "Changes detected", Detail: detail, Percent: 48})
	downloadedPackages := 0
	for _, packageInfo := range definition.Packages {
		packagePath, downloaded, err := syncer.ensureCachedPackage(ctx, cache, packageInfo)
		if err != nil {
			return false, err
		}
		if err := extractPackageArchive(packagePath, next, packageInfo); err != nil {
			return false, fmt.Errorf("extract package %s: %w", packageInfo.Filename, err)
		}
		if downloaded {
			downloadedPackages++
			report(syncer.Progress, progressUpdate{Stage: "Downloaded mod", Detail: packageInfo.Name, Percent: 48})
		}
	}
	report(syncer.Progress, progressUpdate{Stage: "Rebuilding profile", Detail: fmt.Sprintf("Reused %d cached mod archives; downloaded %d.", len(definition.Packages)-downloadedPackages, downloadedPackages), Percent: 74})
	if request.ClientType == clientFlat && manifest.CompanionSHA256 != "" {
		report(syncer.Progress, progressUpdate{Stage: "Downloading Flat companion", Detail: "Verifying the profile-scoped non-VR ValheimVR companion.", Percent: 76})
		companionPayload, err := portal.companionPayloadRequest(ctx, request, token)
		if err != nil {
			return false, err
		}
		companionArchive, downloadErr := downloadVerified(companionPayload.Body, root, ".flat-companion-", manifest.CompanionSize, manifest.CompanionSHA256, maxProfileArchiveBytes)
		companionPayload.Body.Close()
		if downloadErr != nil {
			return false, downloadErr
		}
		defer os.Remove(companionArchive)
		if err := extractFlatCompanion(companionArchive, next); err != nil {
			return false, err
		}
	}
	if manifest.DiagnosticsPluginSHA256 != "" {
		report(syncer.Progress, progressUpdate{Stage: "Downloading diagnostics plugin", Detail: "Verifying the portal-hosted diagnostics plugin.", Percent: 76})
		diagnosticsPayload, err := portal.diagnosticsPluginPayloadRequest(ctx, request, token)
		if err != nil {
			return false, err
		}
		diagnosticsArchive, downloadErr := downloadVerified(diagnosticsPayload.Body, root, ".diagnostics-plugin-", manifest.DiagnosticsPluginSize, manifest.DiagnosticsPluginSHA256, maxProfileArchiveBytes)
		diagnosticsPayload.Body.Close()
		if downloadErr != nil {
			return false, downloadErr
		}
		defer os.Remove(diagnosticsArchive)
		if err := extractDiagnosticsPlugin(diagnosticsArchive, next); err != nil {
			return false, err
		}
	}
	if request.ClientType == clientVR {
		report(syncer.Progress, progressUpdate{Stage: "Downloading VR runtime", Detail: "Verifying the profile-scoped ValheimVR runtime.", Percent: 76})
		runtimePayload, err := portal.runtimePayloadRequest(ctx, request, token)
		if err != nil {
			return false, err
		}
		runtimeArchive, downloadErr := downloadVerified(runtimePayload.Body, root, ".vr-runtime-", manifest.RuntimeSize, manifest.RuntimeSHA256, maxProfileArchiveBytes)
		runtimePayload.Body.Close()
		if downloadErr != nil {
			return false, downloadErr
		}
		defer os.Remove(runtimeArchive)
		if err := extractVRRuntime(runtimeArchive, next, filepath.Join(next, "runtime")); err != nil {
			return false, err
		}
	}
	report(syncer.Progress, progressUpdate{Stage: "Applying profile", Detail: "Building the updated profile beside the one you have now.", Percent: 80})
	if err := copyDirectory(filepath.Join(configStage, "config"), filepath.Join(next, "config")); err != nil {
		return false, err
	}
	configSHA256, err := hashConfigDirectory(filepath.Join(next, "config"))
	if err != nil {
		return false, fmt.Errorf("hash profile configuration: %w", err)
	}
	if err := copyDirectory(filepath.Join(next, "config"), filepath.Join(next, "BepInEx", "config")); err != nil {
		return false, fmt.Errorf("apply BepInEx configuration: %w", err)
	}
	// Each generation is built from scratch, so any config the release does not ship
	// would simply vanish and be regenerated from mod defaults on the next launch -
	// silently discarding every in-game setting the player changed. Observed with
	// EpicLoot, whose welcome screen returned after each sync because the flag it
	// writes to its own config went with the file. 8 configs are shipped against 40
	// present on a real client, so 32 were being reset every time.
	//
	// Only next/BepInEx/config gains the carried-over files. next/config stays exactly
	// what the release shipped, so ConfigSHA256 keeps describing release content and a
	// mod rewriting its own config cannot make the profile look drifted.
	if err := preserveUnmanagedConfig(filepath.Join(root, "active", "BepInEx", "config"), filepath.Join(next, "BepInEx", "config")); err != nil {
		return false, fmt.Errorf("preserve local configuration: %w", err)
	}
	newState := profileState{
		Schema:                  1,
		World:                   definition.World,
		Profile:                 definition.Profile,
		ClientType:              definition.ClientType,
		ReleaseID:               manifest.ReleaseID,
		ProfileSHA256:           manifest.ProfileSHA256,
		ProfileSize:             manifest.ProfileSize,
		ConfigSHA256:            configSHA256,
		ConfigLayout:            "bepinex",
		RuntimeSHA256:           manifest.RuntimeSHA256,
		RuntimeSize:             manifest.RuntimeSize,
		CompanionSHA256:         manifest.CompanionSHA256,
		CompanionSize:           manifest.CompanionSize,
		DiagnosticsPluginSHA256: manifest.DiagnosticsPluginSHA256,
		DiagnosticsPluginSize:   manifest.DiagnosticsPluginSize,
		DiagnosticsToken:        syncer.DiagnosticsToken,
		DiagnosticsEndpoint:     syncer.DiagnosticsEndpoint,
		Packages:                definition.Packages,
	}
	if err := writeJSONAtomically(filepath.Join(next, generationStateFile), newState); err != nil {
		return false, err
	}
	report(syncer.Progress, progressUpdate{Stage: "Activating profile", Detail: "Keeping your previous profile available for recovery.", Percent: 85})
	if err := activateGeneration(root, next, newState); err != nil {
		return false, err
	}
	gameDir, gameErr := validateSteamValheimDirectory(syncer.GameDir)
	if request.ClientType == clientVR && gameErr != nil {
		if rollbackErr := rollbackGeneration(root); rollbackErr != nil {
			return false, fmt.Errorf("locate Steam Valheim: %w; rollback profile: %v", gameErr, rollbackErr)
		}
		return false, gameErr
	}
	if gameErr == nil {
		runtimeSource := ""
		if request.ClientType == clientVR {
			runtimeSource = filepath.Join(root, "active", "runtime")
		}
		if err := reconcileRuntimeOverlay(gameDir, localAppData, request, manifest.ReleaseID, runtimeSource); err != nil {
			if rollbackErr := rollbackGeneration(root); rollbackErr != nil {
				return false, fmt.Errorf("activate runtime: %w; rollback profile: %v", err, rollbackErr)
			}
			return false, err
		}
	}
	if err := prunePackageCache(cache, newState.Packages); err != nil {
		report(syncer.Progress, progressUpdate{Stage: "Profile updated", Detail: "Your profile is ready. Some cached downloads will be cleaned up later.", Percent: 87})
	}
	return true, nil
}

func classifyPackageChanges(previous, next []packageDefinition) packageChanges {
	previousByIdentity := make(map[string]packageDefinition, len(previous))
	for _, packageInfo := range previous {
		previousByIdentity[packageIdentity(packageInfo)] = packageInfo
	}
	changes := packageChanges{}
	for _, packageInfo := range next {
		key := packageIdentity(packageInfo)
		previousPackage, exists := previousByIdentity[key]
		delete(previousByIdentity, key)
		if !exists {
			changes.Added++
		} else if samePackage(previousPackage, packageInfo) {
			changes.Unchanged++
		} else {
			changes.Updated++
		}
	}
	changes.Removed = len(previousByIdentity)
	return changes
}

func packageIdentity(packageInfo packageDefinition) string {
	return packageInfo.Namespace + "\x00" + packageInfo.Name
}

func samePackage(left, right packageDefinition) bool {
	return left.Filename == right.Filename && left.Size == right.Size && strings.EqualFold(left.SHA256, right.SHA256)
}
func (syncer *profileSyncer) ensureCachedPackage(ctx context.Context, cache string, packageInfo packageDefinition) (string, bool, error) {
	if err := os.MkdirAll(cache, 0o700); err != nil {
		return "", false, err
	}
	path := filepath.Join(cache, packageInfo.Filename)
	if err := verifyFile(path, packageInfo.Size, packageInfo.SHA256); err == nil {
		return path, false, nil
	}
	httpRequest, err := http.NewRequestWithContext(ctx, http.MethodGet, packageRepositoryURL+url.PathEscape(packageInfo.Filename), nil)
	if err != nil {
		return "", false, err
	}
	response, err := syncer.HTTPClient.Do(httpRequest)
	if err != nil {
		return "", false, err
	}
	if response.StatusCode != http.StatusOK {
		response.Body.Close()
		return "", false, fmt.Errorf("download package %s: %s", packageInfo.Filename, response.Status)
	}
	temporary, downloadErr := downloadVerified(response.Body, cache, ".package-", packageInfo.Size, packageInfo.SHA256, maxPackageArchiveBytes)
	response.Body.Close()
	if downloadErr != nil {
		return "", false, downloadErr
	}
	if err := replaceFile(temporary, path); err != nil {
		os.Remove(temporary)
		return "", false, err
	}
	return path, true, nil
}

// stateMatchesManifest decides whether the installed profile can be left alone.
// ReleaseID participates deliberately: two releases may ship byte-identical
// artifacts, and treating those as interchangeable silently skips the reinstall
// that a republished release exists to perform.
func stateMatchesManifest(state profileState, manifest remoteManifest) bool {
	return state.ReleaseID == manifest.ReleaseID &&
		strings.EqualFold(state.ProfileSHA256, manifest.ProfileSHA256) && state.ProfileSize == manifest.ProfileSize &&
		strings.EqualFold(state.RuntimeSHA256, manifest.RuntimeSHA256) && state.RuntimeSize == manifest.RuntimeSize &&
		strings.EqualFold(state.CompanionSHA256, manifest.CompanionSHA256) && state.CompanionSize == manifest.CompanionSize &&
		strings.EqualFold(state.DiagnosticsPluginSHA256, manifest.DiagnosticsPluginSHA256) && state.DiagnosticsPluginSize == manifest.DiagnosticsPluginSize
}

func validProfileState(state profileState, request profileRequest) bool {
	if state.Schema != 1 || state.ConfigLayout != "bepinex" || state.ReleaseID == "" || !validSHA256(state.ProfileSHA256) || !validSHA256(state.ConfigSHA256) || state.ProfileSize < 1 || state.ProfileSize > maxProfileArchiveBytes {
		return false
	}
	if request.ClientType == clientVR {
		if !validSHA256(state.RuntimeSHA256) || state.RuntimeSize < 1 || state.RuntimeSize > maxProfileArchiveBytes {
			return false
		}
	} else {
		if state.RuntimeSHA256 != "" || state.RuntimeSize != 0 {
			return false
		}
		if (state.CompanionSHA256 == "") != (state.CompanionSize == 0) {
			return false
		}
		if state.CompanionSHA256 != "" && (!validSHA256(state.CompanionSHA256) || state.CompanionSize < 1 || state.CompanionSize > maxProfileArchiveBytes) {
			return false
		}
	}
	if (state.DiagnosticsPluginSHA256 == "") != (state.DiagnosticsPluginSize == 0) {
		return false
	}
	if state.DiagnosticsPluginSHA256 != "" && (!validSHA256(state.DiagnosticsPluginSHA256) || state.DiagnosticsPluginSize < 1 || state.DiagnosticsPluginSize > maxProfileArchiveBytes) {
		return false
	}
	return validateProfileDefinition(profileDefinition{
		Schema:     state.Schema,
		World:      state.World,
		Profile:    state.Profile,
		ClientType: state.ClientType,
		Packages:   state.Packages,
	}, request) == nil
}

func loadProfileState(root string) (profileState, bool, error) {
	data, err := os.ReadFile(filepath.Join(root, stateFilename))
	if errors.Is(err, os.ErrNotExist) {
		return profileState{}, false, nil
	}
	if err != nil {
		return profileState{}, false, err
	}
	var state profileState
	if err := json.Unmarshal(data, &state); err != nil {
		return profileState{}, false, nil
	}
	return state, true, nil
}

func writeProfileState(root string, state profileState) error {
	return writeJSONAtomically(filepath.Join(root, stateFilename), state)
}

func activeGenerationMatches(root string, state profileState, request profileRequest) bool {
	data, err := os.ReadFile(filepath.Join(root, "active", generationStateFile))
	if err != nil {
		return false
	}
	var generation profileState
	if json.Unmarshal(data, &generation) != nil || !validProfileState(generation, request) {
		return false
	}
	return strings.EqualFold(generation.ProfileSHA256, state.ProfileSHA256) && generation.ProfileSize == state.ProfileSize && strings.EqualFold(generation.ConfigSHA256, state.ConfigSHA256) &&
		generation.ReleaseID == state.ReleaseID
}
func activeProfileRuntimeReady(root string, state profileState) bool {
	bepInExPackFound := false
	for _, packageInfo := range state.Packages {
		if strings.EqualFold(packageInfo.Namespace, "denikson") && strings.EqualFold(packageInfo.Name, "BepInExPack_Valheim") {
			bepInExPackFound = true
			break
		}
	}
	if !bepInExPackFound {
		return true
	}
	for _, name := range []string{
		filepath.Join("BepInEx", "core", "BepInEx.Preloader.dll"),
		"winhttp.dll",
		"doorstop_config.ini",
	} {
		info, err := os.Stat(filepath.Join(root, "active", name))
		if err != nil || !info.Mode().IsRegular() {
			return false
		}
	}
	return true
}

func activeProfileConfigMatches(root string, state profileState) bool {
	hash, err := hashConfigDirectory(filepath.Join(root, "active", "config"))
	return err == nil && strings.EqualFold(hash, state.ConfigSHA256)
}

func hashConfigDirectory(root string) (string, error) {
	hash := sha256.New()
	err := filepath.WalkDir(root, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.Type()&os.ModeSymlink != 0 {
			return errors.New("profile configuration contains a symbolic link")
		}
		relative, err := filepath.Rel(root, path)
		if err != nil {
			return err
		}
		if relative == "." {
			return nil
		}
		if entry.IsDir() {
			_, err := io.WriteString(hash, "directory\x00"+relative+"\x00")
			return err
		}
		if !entry.Type().IsRegular() {
			return errors.New("profile configuration contains a non-regular file")
		}
		if _, err := io.WriteString(hash, "file\x00"+relative+"\x00"); err != nil {
			return err
		}
		input, err := os.Open(path)
		if err != nil {
			return err
		}
		_, copyErr := io.Copy(hash, input)
		closeErr := input.Close()
		if copyErr != nil {
			return copyErr
		}
		return closeErr
	})
	if err != nil {
		return "", err
	}
	return hex.EncodeToString(hash.Sum(nil)), nil
}

func validateProfileDefinition(definition profileDefinition, request profileRequest) error {
	if definition.Schema != 1 || definition.World != request.World || definition.Profile != request.Profile || definition.ClientType != request.ClientType {
		return errors.New("profile definition does not match the selected profile")
	}
	lastFilename := ""
	for _, packageInfo := range definition.Packages {
		if !validPackageText(packageInfo.Namespace) || !validPackageText(packageInfo.Name) || !validPackageText(packageInfo.Version) || !validPackageFilename(packageInfo.Filename) || !validSHA256(packageInfo.SHA256) || packageInfo.Size < 1 || packageInfo.Size > maxPackageArchiveBytes || (lastFilename != "" && packageInfo.Filename <= lastFilename) {
			return errors.New("profile definition contains an invalid package")
		}
		lastFilename = packageInfo.Filename
	}
	return nil
}

func validateCompanionDefinition(definition profileDefinition, manifest remoteManifest) error {
	if definition.ClientType == clientVR {
		if definition.Companion != nil {
			return errors.New("VR profile definition declares a Flat companion")
		}
		return nil
	}
	if definition.Companion == nil {
		if manifest.CompanionSHA256 != "" || manifest.CompanionSize != 0 {
			return errors.New("Flat profile definition is missing its companion metadata")
		}
		return nil
	}
	companion := definition.Companion
	if !validPackageFilename(companion.Filename) || !validSHA256(companion.SHA256) || companion.Size < 1 || companion.Size > maxProfileArchiveBytes {
		return errors.New("Flat profile definition contains invalid companion metadata")
	}
	if !strings.EqualFold(companion.SHA256, manifest.CompanionSHA256) || companion.Size != manifest.CompanionSize {
		return errors.New("Flat companion metadata does not match the published release")
	}
	return nil
}

func validPackageText(value string) bool {
	if len(value) == 0 || len(value) > 180 {
		return false
	}
	for _, c := range value {
		if !((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.' || c == '+') {
			return false
		}
	}
	return true
}

func validPackageFilename(value string) bool {
	return len(value) > 4 && len(value) <= 180 && strings.HasSuffix(strings.ToLower(value), ".zip") && filepath.Base(value) == value && !strings.ContainsAny(value, "\\/:\r\n\x00")
}

func validSHA256(value string) bool {
	if len(value) != sha256.Size*2 {
		return false
	}
	_, err := hex.DecodeString(value)
	return err == nil
}

func unpackProfileDefinition(source, destination string, request profileRequest) (profileDefinition, error) {
	archive, err := zip.OpenReader(source)
	if err != nil {
		return profileDefinition{}, err
	}
	defer archive.Close()
	var manifestData []byte
	configFound := false
	var total int64
	for _, file := range archive.File {
		name, err := validateArchiveEntry(file, &total)
		if err != nil {
			return profileDefinition{}, err
		}
		switch {
		case name == "profile-manifest.json":
			if archiveEntryIsDirectory(file) || manifestData != nil {
				return profileDefinition{}, errors.New("profile definition has an invalid manifest")
			}
			manifestData, err = readArchiveFile(file, 1<<20)
			if err != nil {
				return profileDefinition{}, err
			}
		case name == "config" || strings.HasPrefix(name, "config/"):
			configFound = true
		default:
			return profileDefinition{}, errors.New("profile definition contains an unsupported file")
		}
	}
	if manifestData == nil || !configFound {
		return profileDefinition{}, errors.New("profile definition must contain profile-manifest.json and config")
	}
	var definition profileDefinition
	decoder := json.NewDecoder(bytes.NewReader(manifestData))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&definition); err != nil {
		return profileDefinition{}, fmt.Errorf("decode profile definition: %w", err)
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return profileDefinition{}, errors.New("profile definition has trailing JSON")
	}
	if err := validateProfileDefinition(definition, request); err != nil {
		return profileDefinition{}, err
	}
	if err := extractSelectedZip(source, destination, func(name string) bool {
		return name == "config" || strings.HasPrefix(name, "config/")
	}, false); err != nil {
		return profileDefinition{}, err
	}
	return definition, nil
}

func extractPackageArchive(source, destination string, packageInfo packageDefinition) error {
	pluginRoot := stdpath.Join("BepInEx", "plugins", packageInfo.Namespace+"-"+packageInfo.Name)
	bepInExPack := strings.EqualFold(packageInfo.Namespace, "denikson") && strings.EqualFold(packageInfo.Name, "BepInExPack_Valheim")
	return extractMappedZip(source, destination, func(name string) (string, bool) {
		runtimeName := name
		if bepInExPack {
			const prefix = "BepInExPack_Valheim/"
			if !strings.HasPrefix(runtimeName, prefix) {
				return "", false
			}
			runtimeName = strings.TrimPrefix(runtimeName, prefix)
		}
		if strings.HasPrefix(runtimeName, "patchers/") {
			return stdpath.Join("BepInEx", runtimeName), true
		}
		// Thunderstore packages may nest their payload under a leading "plugins/"
		// directory, expecting the installer to flatten it into the package's own
		// plugin folder. Preserving the segment still loads assemblies, because
		// BepInEx scans plugins/ recursively, but it breaks every mod that resolves
		// assets relative to its own directory (asset bundles, translations).
		if runtimeName == "plugins" {
			return "", false
		}
		if strings.HasPrefix(runtimeName, "plugins/") {
			return stdpath.Join(pluginRoot, strings.TrimPrefix(runtimeName, "plugins/")), true
		}
		if packageRuntimePath(runtimeName) {
			return runtimeName, true
		}
		if bepInExPack || packageMetadataPath(name) {
			return "", false
		}
		return stdpath.Join(pluginRoot, name), true
	}, true)
}

func packageMetadataPath(name string) bool {
	if strings.Contains(name, "/") {
		return false
	}
	switch strings.ToLower(name) {
	case "manifest.json", "icon.png", "readme.md", "changelog.md", "license", "license.md":
		return true
	default:
		return false
	}
}

func packageRuntimePath(name string) bool {
	return name == "BepInEx" || strings.HasPrefix(name, "BepInEx/") || name == "winhttp.dll" || name == "doorstop_config.ini" || name == "doorstop_libs" || strings.HasPrefix(name, "doorstop_libs/")
}

func repairLoadTimeProfilerPatcher(root string) error {
	pluginRoot := filepath.Join(root, "active", "BepInEx", "plugins", "sighsorry-LoadTimeProfiler")
	source := filepath.Join(pluginRoot, "patchers", "LoadTimeProfiler.dll")
	if _, err := os.Stat(source); errors.Is(err, os.ErrNotExist) {
		return nil
	} else if err != nil {
		return err
	}
	target := filepath.Join(root, "active", "BepInEx", "patchers", "LoadTimeProfiler.dll")
	if _, err := os.Stat(target); err == nil {
		same, err := sameFileContents(source, target)
		if err != nil {
			return err
		}
		if !same {
			return errors.New("existing patcher differs from the managed LoadTimeProfiler package")
		}
	} else if errors.Is(err, os.ErrNotExist) {
		if err := copyFileAtomically(source, target); err != nil {
			return err
		}
	} else {
		return err
	}
	return os.RemoveAll(pluginRoot)
}

func removeRetiredDragonRiders(root string) error {
	return os.RemoveAll(filepath.Join(root, "active", "BepInEx", "plugins", "Yggdrah-DragonRiders"))
}

func extractMappedZip(source, destination string, mapName func(string) (string, bool), overwrite bool) error {
	archive, err := zip.OpenReader(source)
	if err != nil {
		return err
	}
	defer archive.Close()
	if err := os.MkdirAll(destination, 0o700); err != nil {
		return err
	}
	seen := make(map[string]struct{})
	var total int64
	for _, file := range archive.File {
		name, err := validateArchiveEntry(file, &total)
		if err != nil {
			return err
		}
		mapped, include := mapName(name)
		if !include {
			continue
		}
		key := strings.ToLower(mapped)
		if _, duplicate := seen[key]; duplicate {
			return errors.New("archive contains duplicate paths")
		}
		seen[key] = struct{}{}
		target, err := archiveDestination(destination, mapped)
		if err != nil {
			return err
		}
		if archiveEntryIsDirectory(file) {
			if err := os.MkdirAll(target, 0o700); err != nil {
				return err
			}
			continue
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o700); err != nil {
			return err
		}
		input, err := file.Open()
		if err != nil {
			return err
		}
		flags := os.O_CREATE | os.O_WRONLY
		if overwrite {
			flags |= os.O_TRUNC
		} else {
			flags |= os.O_EXCL
		}
		output, err := os.OpenFile(target, flags, 0o600)
		if err == nil {
			written, copyErr := io.Copy(output, io.LimitReader(input, maxArchiveEntryBytes+1))
			closeErr := output.Close()
			if copyErr != nil {
				err = copyErr
			} else if written > maxArchiveEntryBytes {
				err = errors.New("archive entry is too large")
			} else {
				err = closeErr
			}
		}
		input.Close()
		if err != nil {
			return err
		}
	}
	return nil
}

func extractSelectedZip(source, destination string, include func(string) bool, overwrite bool) error {
	archive, err := zip.OpenReader(source)
	if err != nil {
		return err
	}
	defer archive.Close()
	if err := os.MkdirAll(destination, 0o700); err != nil {
		return err
	}
	seen := make(map[string]struct{})
	var total int64
	for _, file := range archive.File {
		name, err := validateArchiveEntry(file, &total)
		if err != nil {
			return err
		}
		if !include(name) {
			continue
		}
		key := strings.ToLower(name)
		if _, duplicate := seen[key]; duplicate {
			return errors.New("archive contains duplicate paths")
		}
		seen[key] = struct{}{}
		target, err := archiveDestination(destination, name)
		if err != nil {
			return err
		}
		if archiveEntryIsDirectory(file) {
			if err := os.MkdirAll(target, 0o700); err != nil {
				return err
			}
			continue
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o700); err != nil {
			return err
		}
		input, err := file.Open()
		if err != nil {
			return err
		}
		flags := os.O_CREATE | os.O_WRONLY
		if overwrite {
			flags |= os.O_TRUNC
		} else {
			flags |= os.O_EXCL
		}
		output, err := os.OpenFile(target, flags, 0o600)
		if err == nil {
			written, copyErr := io.Copy(output, io.LimitReader(input, maxArchiveEntryBytes+1))
			closeErr := output.Close()
			if copyErr != nil {
				err = copyErr
			} else if written > maxArchiveEntryBytes {
				err = errors.New("archive entry is too large")
			} else {
				err = closeErr
			}
		}
		input.Close()
		if err != nil {
			return err
		}
	}
	return nil
}

func archiveEntryIsDirectory(file *zip.File) bool {
	return file.FileInfo().IsDir() || strings.HasSuffix(file.Name, "/") || strings.HasSuffix(file.Name, "\\")
}

func validateArchiveEntry(file *zip.File, total *int64) (string, error) {
	name, err := archivePath(file.Name)
	if err != nil {
		return "", err
	}
	if file.Mode()&os.ModeSymlink != 0 {
		return "", errors.New("archive contains a symbolic link")
	}
	if archiveEntryIsDirectory(file) && file.UncompressedSize64 != 0 {
		return "", errors.New("archive directory contains data")
	}
	if file.UncompressedSize64 > uint64(maxArchiveEntryBytes) || *total > maxArchiveTotalBytes-int64(file.UncompressedSize64) {
		return "", errors.New("archive is too large")
	}
	*total += int64(file.UncompressedSize64)
	return name, nil
}

func archivePath(value string) (string, error) {
	if value == "" || strings.ContainsRune(value, '\x00') {
		return "", errors.New("archive contains an invalid path")
	}
	raw := strings.ReplaceAll(value, "\\", "/")
	if strings.HasPrefix(raw, "/") || strings.Contains(raw, ":") {
		return "", errors.New("archive contains an unsafe path")
	}
	for _, part := range strings.Split(raw, "/") {
		if part == ".." {
			return "", errors.New("archive contains an unsafe path")
		}
	}
	clean := stdpath.Clean(raw)
	if clean == "." || clean == ".." || strings.HasPrefix(clean, "../") {
		return "", errors.New("archive contains an unsafe path")
	}
	return clean, nil
}

func archiveDestination(root, name string) (string, error) {
	target := filepath.Join(root, filepath.FromSlash(name))
	relative, err := filepath.Rel(root, target)
	if err != nil || relative == "." || relative == ".." || strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
		return "", errors.New("archive destination escapes its staging root")
	}
	return target, nil
}

func readArchiveFile(file *zip.File, limit int64) ([]byte, error) {
	reader, err := file.Open()
	if err != nil {
		return nil, err
	}
	defer reader.Close()
	data, err := io.ReadAll(io.LimitReader(reader, limit+1))
	if err != nil {
		return nil, err
	}
	if int64(len(data)) > limit {
		return nil, errors.New("archive file is too large")
	}
	return data, nil
}

func copyDirectory(source, destination string) error {
	if err := os.MkdirAll(destination, 0o700); err != nil {
		return err
	}
	return filepath.WalkDir(source, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.Type()&os.ModeSymlink != 0 {
			return errors.New("profile configuration contains a symbolic link")
		}
		relative, err := filepath.Rel(source, path)
		if err != nil {
			return err
		}
		if relative == "." {
			return nil
		}
		target := filepath.Join(destination, relative)
		if entry.IsDir() {
			return os.MkdirAll(target, 0o700)
		}
		input, err := os.Open(path)
		if err != nil {
			return err
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o700); err != nil {
			input.Close()
			return err
		}
		output, err := os.OpenFile(target, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o600)
		if err != nil {
			input.Close()
			return err
		}
		_, copyErr := io.Copy(output, input)
		outputErr := output.Close()
		inputErr := input.Close()
		if copyErr != nil {
			return copyErr
		}
		if outputErr != nil {
			return outputErr
		}
		return inputErr
	})
}

// preserveUnmanagedConfig copies entries from the live config directory into a staged
// one when the staged directory has no entry of that name. Shipped configuration
// always wins; this only rescues files the release has no opinion about.
func preserveUnmanagedConfig(previous, next string) error {
	entries, err := os.ReadDir(previous)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil
		}
		return err
	}
	if err := os.MkdirAll(next, 0o700); err != nil {
		return err
	}
	for _, entry := range entries {
		name := entry.Name()
		if entry.Type()&os.ModeSymlink != 0 {
			// Never follow a link out of the profile tree.
			continue
		}
		target := filepath.Join(next, name)
		if _, statErr := os.Lstat(target); statErr == nil {
			continue
		} else if !errors.Is(statErr, os.ErrNotExist) {
			return statErr
		}
		source := filepath.Join(previous, name)
		if entry.IsDir() {
			if err := copyDirectory(source, target); err != nil {
				return err
			}
			continue
		}
		if !entry.Type().IsRegular() {
			continue
		}
		if err := copyFileAtomically(source, target); err != nil {
			return err
		}
	}
	return nil
}

func downloadVerified(body io.Reader, directory, pattern string, expectedSize int64, expectedSHA256 string, limit int64) (string, error) {
	if expectedSize < 1 || expectedSize > limit || !validSHA256(expectedSHA256) {
		return "", errors.New("invalid expected download checksum or size")
	}
	if err := os.MkdirAll(directory, 0o700); err != nil {
		return "", err
	}
	temporary, err := os.CreateTemp(directory, pattern)
	if err != nil {
		return "", err
	}
	path := temporary.Name()
	defer func() {
		if temporary != nil {
			temporary.Close()
		}
	}()
	hash := sha256.New()
	written, copyErr := io.Copy(io.MultiWriter(temporary, hash), io.LimitReader(body, expectedSize+1))
	if closeErr := temporary.Close(); copyErr == nil {
		copyErr = closeErr
	}
	temporary = nil
	if copyErr != nil {
		os.Remove(path)
		return "", copyErr
	}
	if written != expectedSize || !strings.EqualFold(hex.EncodeToString(hash.Sum(nil)), expectedSHA256) {
		os.Remove(path)
		return "", errors.New("download checksum or size mismatch")
	}
	return path, nil
}

func verifyFile(path string, expectedSize int64, expectedSHA256 string) error {
	file, err := os.Open(path)
	if err != nil {
		return err
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return err
	}
	if !info.Mode().IsRegular() || info.Size() != expectedSize {
		return errors.New("cached package size mismatch")
	}
	hash := sha256.New()
	if _, err := io.Copy(hash, file); err != nil {
		return err
	}
	if !strings.EqualFold(hex.EncodeToString(hash.Sum(nil)), expectedSHA256) {
		return errors.New("cached package checksum mismatch")
	}
	return nil
}

func prunePackageCache(cache string, packages []packageDefinition) error {
	entries, err := os.ReadDir(cache)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	keep := make(map[string]struct{}, len(packages))
	for _, packageInfo := range packages {
		keep[packageInfo.Filename] = struct{}{}
	}
	for _, entry := range entries {
		if _, found := keep[entry.Name()]; !found {
			if err := os.RemoveAll(filepath.Join(cache, entry.Name())); err != nil {
				return err
			}
		}
	}
	return nil
}

func writeJSONAtomically(path string, value any) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	temporary, err := os.CreateTemp(filepath.Dir(path), ".state-")
	if err != nil {
		return err
	}
	name := temporary.Name()
	encoder := json.NewEncoder(temporary)
	if err := encoder.Encode(value); err != nil {
		temporary.Close()
		os.Remove(name)
		return err
	}
	if err := temporary.Chmod(0o600); err != nil {
		temporary.Close()
		os.Remove(name)
		return err
	}
	if err := temporary.Close(); err != nil {
		os.Remove(name)
		return err
	}
	if err := replaceFile(name, path); err != nil {
		os.Remove(name)
		return err
	}
	return nil
}

func replaceFile(source, destination string) error {
	if err := os.Rename(source, destination); err == nil {
		return nil
	} else if !errors.Is(err, os.ErrExist) {
		return err
	}
	if err := os.Remove(destination); err != nil {
		return err
	}
	return os.Rename(source, destination)
}

// renameWithRetry works around Windows denying a directory rename while another
// handle is still closing: an antivirus scan, a shell/indexer walk, an SMB
// session, or a delete-pending entry from a just-removed directory. The failure
// is transient, so a bounded retry converts a hard sync failure into a short wait.
// renameGeneration indirects the rename so activation's failure handling can be
// tested. The destructive fallback below only triggers on a permission denial while
// deletion of the same directory succeeds, which is a Windows-specific condition
// that cannot be reproduced on the platforms this package is tested on.
var renameGeneration = renameWithRetry

func renameWithRetry(from, to string) error {
	var err error
	for attempt := range 12 {
		if err = os.Rename(from, to); err == nil {
			return nil
		}
		time.Sleep(time.Duration(50*(attempt+1)) * time.Millisecond)
	}
	return err
}

// previousGeneration returns the rollback directory, tolerating the unique
// previous-<nanos> names activateGeneration falls back to.
func previousGeneration(root string) (string, error) {
	fixed := filepath.Join(root, "previous")
	if _, err := os.Stat(fixed); err == nil {
		return fixed, nil
	}
	matches, err := filepath.Glob(filepath.Join(root, "previous-*"))
	if err != nil {
		return "", err
	}
	newest, newestTime := "", time.Time{}
	for _, candidate := range matches {
		info, statErr := os.Stat(candidate)
		if statErr != nil || !info.IsDir() {
			continue
		}
		if newest == "" || info.ModTime().After(newestTime) {
			newest, newestTime = candidate, info.ModTime()
		}
	}
	if newest == "" {
		return "", os.ErrNotExist
	}
	return newest, nil
}

// pruneGenerations keeps only the rollback directory currently in use.
func pruneGenerations(root, keep string) {
	matches, err := filepath.Glob(filepath.Join(root, "previous-*"))
	if err != nil {
		return
	}
	for _, candidate := range matches {
		if candidate != keep {
			_ = os.RemoveAll(candidate)
		}
	}
}

func activateGeneration(root, next string, state profileState) error {
	stateTemp, err := os.CreateTemp(root, ".state-next-")
	if err != nil {
		return err
	}
	stateTempPath := stateTemp.Name()
	encoder := json.NewEncoder(stateTemp)
	if err := encoder.Encode(state); err != nil {
		stateTemp.Close()
		os.Remove(stateTempPath)
		return err
	}
	if err := stateTemp.Close(); err != nil {
		os.Remove(stateTempPath)
		return err
	}
	defer os.Remove(stateTempPath)

	active := filepath.Join(root, "active")
	previous := filepath.Join(root, "previous")
	// A best-effort removal is enough: if the name survives because a handle is
	// still open, fall back to a unique target rather than failing activation.
	_ = os.RemoveAll(previous)
	if _, statErr := os.Lstat(previous); statErr == nil {
		previous = filepath.Join(root, fmt.Sprintf("previous-%d", time.Now().UnixNano()))
	}
	_, activeErr := os.Stat(active)
	hadActive := activeErr == nil
	if activeErr != nil && !errors.Is(activeErr, os.ErrNotExist) {
		return activeErr
	}
	// destroyedActive records that the rollback copy no longer exists, so later
	// failure handling must preserve the staged tree instead of discarding it.
	destroyedActive := false
	if hadActive {
		if err := renameGeneration(active, previous); err != nil {
			// Windows can deny renaming a directory while still permitting its
			// deletion: a rename needs the directory itself handle-free, while
			// RemoveAll unlinks children individually. Observed in the field -
			// RemoveAll(previous) succeeded on an identical tree in the same run
			// that this rename was denied.
			//
			// Only a permission denial gets this treatment. Deleting the working
			// profile in response to a transient lock would be strictly worse than
			// failing: the caller can always retry a failed sync, but it cannot
			// recover a generation that is already gone.
			if !errors.Is(err, fs.ErrPermission) {
				return fmt.Errorf("set the active profile aside (close Valheim and SteamVR, then retry): %w", err)
			}
			if removeErr := os.RemoveAll(active); removeErr != nil {
				return fmt.Errorf("replace the active profile (close Valheim and SteamVR, then retry): rename: %w; remove: %v", err, removeErr)
			}
			hadActive = false
			destroyedActive = true
		}
	}
	if err := renameGeneration(next, active); err != nil {
		if hadActive {
			_ = renameGeneration(previous, active)
		}
		if destroyedActive {
			// The previous generation is already deleted and the caller is about to
			// remove the staging directory, which would leave no profile at all.
			// Parking the fully built tree under the rollback name keeps a complete
			// copy on disk instead. Nothing reads it back automatically -
			// rollbackGeneration needs an existing "active" and the next activation
			// clears "previous" - so its only job is to prevent total loss. The
			// rerun is cheap because the package cache survives.
			if parkErr := renameGeneration(next, previous); parkErr == nil {
				return fmt.Errorf("activate the updated profile: %w; it was preserved for recovery, run the update again", err)
			}
		}
		return fmt.Errorf("activate the updated profile: %w", err)
	}
	if err := replaceFile(stateTempPath, filepath.Join(root, stateFilename)); err != nil {
		if hadActive {
			if rollbackErr := rollbackGeneration(root); rollbackErr != nil {
				return fmt.Errorf("activate profile state: %w; rollback: %v", err, rollbackErr)
			}
		} else if !destroyedActive {
			// Only discard the just-activated tree when it was a first install and
			// there is nothing to lose. After a destructive fallback this tree is
			// the only copy of the profile, so it stays put.
			_ = os.RemoveAll(active)
		}
		return fmt.Errorf("activate profile state: %w", err)
	}
	// Pruning is safe only when this activation produced a fresh rollback copy.
	// After a destructive fallback no new "previous" exists, so pruning would
	// delete the last older generation and leave nothing to roll back to.
	if !destroyedActive {
		pruneGenerations(root, previous)
	}
	return nil
}

func rollbackGeneration(root string) error {
	active := filepath.Join(root, "active")
	previous, err := previousGeneration(root)
	if err != nil {
		return fmt.Errorf("no known-good profile is available to roll back to: %w", err)
	}
	failed := filepath.Join(root, ".failed")
	if err := os.RemoveAll(failed); err != nil {
		return err
	}
	if err := renameWithRetry(active, failed); err != nil {
		return err
	}
	if err := renameWithRetry(previous, active); err != nil {
		_ = renameWithRetry(failed, active)
		return err
	}
	data, err := os.ReadFile(filepath.Join(active, generationStateFile))
	if err != nil {
		return err
	}
	var state profileState
	if err := json.Unmarshal(data, &state); err != nil {
		return err
	}
	if err := writeProfileState(root, state); err != nil {
		return err
	}
	return os.RemoveAll(failed)
}
