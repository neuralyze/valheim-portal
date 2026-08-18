package main

import (
	"archive/zip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"io/fs"
	"log"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/neuralyze/valheim-portal/internal/valheimvr"
)

const (
	thunderstorePackagesURL       = "https://gcdn.thunderstore.io/live/repository/packages/"
	maxProfileManifestBytes int64 = 1 << 20
	maxPackageBytes         int64 = 512 << 20
)

var deterministicZIPTime = time.Date(1980, time.January, 1, 0, 0, 0, 0, time.UTC)

type builderOptions struct {
	SourceManifestPath string
	World              string
	Profile            string
	ClientType         string
	ConfigDir          string
	Output             string
	PackageBaseURL     string
	HTTPClient         *http.Client
	FlatCompanion      string
	TrueNonVR          bool
	Audience           string
	DebugLogging       bool
}

type profileManifest struct {
	Schema     int    `json:"schema"`
	World      string `json:"world"`
	Profile    string `json:"profile"`
	ClientType string `json:"client_type"`
	// Who the edition is for. "player" is the ordinary download; "admin" carries the
	// console and world-editing tools, and the portal only offers it to a world's admins.
	Audience  string             `json:"audience"`
	Packages  []packageManifest  `json:"packages"`
	Companion *companionManifest `json:"companion,omitempty"`
}

type packageManifest struct {
	Namespace string `json:"namespace"`
	Name      string `json:"name"`
	Version   string `json:"version"`
	Filename  string `json:"filename"`
	SHA256    string `json:"sha256"`
	Size      int64  `json:"size"`
}

type managedProfileManifest struct {
	SchemaVersion      int              `json:"schema_version"`
	WorldName          string           `json:"world_name"`
	Packages           []managedPackage `json:"packages"`
	ClientOnlyPackages []managedPackage `json:"client_only_packages"`
}

type managedPackage struct {
	Identifier string `json:"identifier"`
	Version    string `json:"version"`
}

type companionManifest struct {
	Filename string `json:"filename"`
	SHA256   string `json:"sha256"`
	Size     int64  `json:"size"`
}

type configEntry struct {
	zipName string
	source  string
	body    []byte
	isDir   bool
}

func main() {
	var options builderOptions
	flag.StringVar(&options.SourceManifestPath, "source-manifest", "", "path to managed profile-manifest.json")
	flag.StringVar(&options.World, "world", "", "world identifier")
	flag.StringVar(&options.Profile, "profile", "", "profile identifier")
	flag.StringVar(&options.ClientType, "client-type", "", "client type (flat or vr)")
	flag.StringVar(&options.Audience, "audience", "", "who the edition is for (player or admin)")
	flag.StringVar(&options.ConfigDir, "config-dir", "", "directory containing client configuration")
	flag.StringVar(&options.Output, "output", "", "output profile definition ZIP")
	flag.StringVar(&options.FlatCompanion, "flat-companion", "", "path to the locally built Flat ValheimVR companion ZIP")
	flag.StringVar(&options.PackageBaseURL, "package-base-url", os.Getenv("VALHEIM_PACKAGE_BASE_URL"), "Thunderstore package archive base URL")
	flag.BoolVar(&options.TrueNonVR, "true-nonvr", false, "build a Flat profile without ValheimVR or its companion")
	flag.BoolVar(&options.DebugLogging, "debug-logging", false, "force verbose client diagnostics and startup profiling")
	flag.Parse()
	if flag.NArg() != 0 {
		flag.Usage()
		os.Exit(2)
	}

	if err := buildProfileDefinition(context.Background(), options); err != nil {
		log.Fatal(err)
	}
	fmt.Println(options.Output)
}

func buildProfileDefinition(ctx context.Context, options builderOptions) error {
	if err := validateBuilderOptions(options); err != nil {
		return err
	}

	packages, err := profilePackages(options.SourceManifestPath, options.World)
	if err != nil {
		return err
	}
	if options.TrueNonVR {
		// -true-nonvr means "a Flat profile without ValheimVR or its companion", so the VR
		// integration packages are STRIPPED rather than treated as an error. Refusing the build
		// instead forced a second, hand-maintained manifest per world purely to omit a couple of
		// packages, which then drifted from the real one.
		//
		// Every removal is named on stderr: dropping packages silently would be worse than the
		// original error.
		stripped, removed := stripValheimVRPackages(packages)
		for _, name := range removed {
			fmt.Fprintf(os.Stderr, "true-nonvr: excluding ValheimVR package %s\n", name)
		}
		packages = stripped
		// Post-condition, and the reason validateTrueNonVRPackages still exists: the stripped set
		// must contain no VR integration package at all.
		if err := validateTrueNonVRPackages(packages); err != nil {
			return err
		}
	}
	config, err := collectConfigEntries(options.ConfigDir)
	if err != nil {
		return err
	}
	if options.DebugLogging {
		config, err = applyDebugLogging(config)
		if err != nil {
			return err
		}
	}
	var companion *companionManifest
	if options.ClientType == "flat" && options.FlatCompanion != "" {
		filename, checksum, size, err := localArtifactMetadata(options.FlatCompanion)
		if err != nil {
			return err
		}
		companion = &companionManifest{Filename: filename, SHA256: checksum, Size: size}
	}

	baseURL := options.PackageBaseURL
	if baseURL == "" {
		baseURL = thunderstorePackagesURL
	}
	client := options.HTTPClient
	if client == nil {
		client = newPackageHTTPClient()
	}
	for i := range packages {
		checksum, size, err := downloadPackage(ctx, client, baseURL, packages[i].Filename)
		if err != nil {
			return fmt.Errorf("download %s: %w", packages[i].Filename, err)
		}
		packages[i].SHA256 = checksum
		packages[i].Size = size
	}

	manifest, err := json.Marshal(profileManifest{
		Schema:     1,
		World:      options.World,
		Profile:    options.Profile,
		ClientType: options.ClientType,
		Audience:   options.Audience,
		Packages:   packages,
		Companion:  companion,
	})
	if err != nil {
		return fmt.Errorf("encode profile manifest: %w", err)
	}
	manifest = append(manifest, '\n')

	if err := writeProfileDefinition(options.Output, manifest, config); err != nil {
		return err
	}
	return nil
}

func validateBuilderOptions(options builderOptions) error {
	if options.SourceManifestPath == "" {
		return fmt.Errorf("source-manifest is required")
	}
	if !validIdentifier(options.World) {
		return fmt.Errorf("invalid world identifier")
	}
	if !validIdentifier(options.Profile) {
		return fmt.Errorf("invalid profile identifier")
	}
	// No default: an admin edition that silently published as a player one would put the
	// console in front of every player, and a player edition marked admin would hide the
	// download everyone needs.
	if options.Audience != "player" && options.Audience != "admin" {
		return errors.New("audience must be player or admin")
	}
	if options.ClientType != "flat" && options.ClientType != "vr" {
		return fmt.Errorf("client type must be flat or vr")
	}
	if options.ClientType == "flat" && options.FlatCompanion == "" && !options.TrueNonVR {
		return fmt.Errorf("flat-companion is required for Flat profiles unless true-nonvr is set")
	}
	if options.ClientType == "vr" && options.FlatCompanion != "" {
		return fmt.Errorf("flat-companion is not valid for VR profiles")
	}
	if options.TrueNonVR && options.ClientType != "flat" {
		return fmt.Errorf("true-nonvr is only valid for Flat profiles")
	}
	if options.TrueNonVR && options.FlatCompanion != "" {
		return fmt.Errorf("true-nonvr profiles cannot include a flat-companion")
	}
	if options.ConfigDir == "" {
		return fmt.Errorf("config-dir is required")
	}
	if options.Output == "" {
		return fmt.Errorf("output is required")
	}

	configDir, err := filepath.Abs(options.ConfigDir)
	if err != nil {
		return fmt.Errorf("resolve config directory: %w", err)
	}
	output, err := filepath.Abs(options.Output)
	if err != nil {
		return fmt.Errorf("resolve output path: %w", err)
	}
	rel, err := filepath.Rel(configDir, output)
	if err != nil {
		return fmt.Errorf("compare config directory and output: %w", err)
	}
	if rel == "." || (rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator))) {
		return fmt.Errorf("output must not be inside config-dir")
	}
	return nil
}

func validIdentifier(value string) bool {
	if len(value) == 0 || len(value) > 80 {
		return false
	}
	for _, c := range value {
		if !((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.') {
			return false
		}
	}
	return true
}

// stripValheimVRPackages removes the ValheimVR integration packages from a package set and returns
// the filtered set alongside the names removed, so a true-nonvr build can be produced from the same
// manifest every other client type uses.
func stripValheimVRPackages(packages []packageManifest) ([]packageManifest, []string) {
	kept := make([]packageManifest, 0, len(packages))
	removed := make([]string, 0, 2)
	for _, packageInfo := range packages {
		name := packageInfo.Namespace + "-" + packageInfo.Name
		if valheimvr.IsIntegrationPackage(name) {
			removed = append(removed, name)
			continue
		}
		kept = append(kept, packageInfo)
	}
	return kept, removed
}

func validateTrueNonVRPackages(packages []packageManifest) error {
	for _, packageInfo := range packages {
		if valheimvr.IsIntegrationPackage(packageInfo.Namespace + "-" + packageInfo.Name) {
			return fmt.Errorf("true-nonvr profile contains ValheimVR package %s-%s", packageInfo.Namespace, packageInfo.Name)
		}
	}
	return nil
}

func profilePackages(manifestPath, world string) ([]packageManifest, error) {
	file, err := os.Open(manifestPath)
	if err != nil {
		return nil, fmt.Errorf("open managed profile manifest: %w", err)
	}
	metadata, err := readAtMost(file, maxProfileManifestBytes)
	closeErr := file.Close()
	if err != nil {
		return nil, fmt.Errorf("read managed profile manifest: %w", err)
	}
	if closeErr != nil {
		return nil, fmt.Errorf("close managed profile manifest: %w", closeErr)
	}

	var source managedProfileManifest
	if err := json.Unmarshal(metadata, &source); err != nil {
		return nil, fmt.Errorf("parse managed profile manifest: %w", err)
	}
	// Schema 2 is a shared profile: it belongs to no world, so it carries no
	// world_name to check. Schema 1 is a per-world copy, where a mismatch meant the
	// wrong world's mod set was about to be published under this world's name.
	switch source.SchemaVersion {
	case 1:
		if source.WorldName != world {
			return nil, fmt.Errorf("managed profile manifest world %q does not match %q", source.WorldName, world)
		}
	case 2:
		if source.WorldName != "" {
			return nil, fmt.Errorf("shared profile manifest must not name a world, got %q", source.WorldName)
		}
	default:
		return nil, fmt.Errorf("unsupported managed profile manifest schema %d", source.SchemaVersion)
	}

	entries := append(append([]managedPackage{}, source.Packages...), source.ClientOnlyPackages...)
	packages := make([]packageManifest, 0, len(entries))
	seenFilenames := make(map[string]struct{}, len(entries))
	for index, entry := range entries {
		pkg, err := managedManifestPackage(entry)
		if err != nil {
			return nil, fmt.Errorf("invalid managed package %d: %w", index+1, err)
		}
		if _, exists := seenFilenames[pkg.Filename]; exists {
			return nil, fmt.Errorf("duplicate package filename %q", pkg.Filename)
		}
		seenFilenames[pkg.Filename] = struct{}{}
		packages = append(packages, pkg)
	}
	sort.Slice(packages, func(i, j int) bool {
		return packages[i].Filename < packages[j].Filename
	})
	return packages, nil
}

func validPackageIdentity(value string) bool {
	namespace, name, found := strings.Cut(value, "-")
	return found && validIdentifier(namespace) && validIdentifier(name)
}

func managedManifestPackage(entry managedPackage) (packageManifest, error) {
	namespace, name, found := strings.Cut(entry.Identifier, "-")
	if !found || !validPackageIdentity(entry.Identifier) {
		return packageManifest{}, fmt.Errorf("invalid package identity %q", entry.Identifier)
	}
	parts := strings.Split(entry.Version, ".")
	if len(parts) != 3 {
		return packageManifest{}, fmt.Errorf("invalid package version for %q", entry.Identifier)
	}
	for _, part := range parts {
		value, err := strconv.Atoi(part)
		if err != nil || value < 0 {
			return packageManifest{}, fmt.Errorf("invalid package version for %q", entry.Identifier)
		}
	}
	filename := fmt.Sprintf("%s-%s-%s.zip", namespace, name, entry.Version)
	if !validPackageFilename(filename) {
		return packageManifest{}, fmt.Errorf("invalid package filename %q", filename)
	}
	return packageManifest{
		Namespace: namespace,
		Name:      name,
		Version:   entry.Version,
		Filename:  filename,
	}, nil
}

func validPackageFilename(filename string) bool {
	return filename != "" && len(filename) <= 180 && filepath.Base(filename) == filename && !strings.Contains(filename, "\\")
}

func newPackageHTTPClient() *http.Client {
	dialer := &net.Dialer{Timeout: 5 * time.Second, KeepAlive: 30 * time.Second}
	transport := &http.Transport{
		Proxy:                 http.ProxyFromEnvironment,
		DialContext:           dialer.DialContext,
		ForceAttemptHTTP2:     true,
		MaxIdleConns:          2,
		MaxIdleConnsPerHost:   2,
		IdleConnTimeout:       30 * time.Second,
		TLSHandshakeTimeout:   5 * time.Second,
		ResponseHeaderTimeout: 15 * time.Second,
		ExpectContinueTimeout: time.Second,
	}
	return &http.Client{
		Transport: transport,
		Timeout:   45 * time.Second,
		CheckRedirect: func(_ *http.Request, _ []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}
}

func downloadPackage(ctx context.Context, client *http.Client, baseURL, filename string) (string, int64, error) {
	if !validPackageFilename(filename) {
		return "", 0, fmt.Errorf("invalid package filename")
	}
	requestURL, err := packageURL(baseURL, filename)
	if err != nil {
		return "", 0, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, requestURL, nil)
	if err != nil {
		return "", 0, err
	}
	response, err := client.Do(req)
	if err != nil {
		return "", 0, err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return "", 0, fmt.Errorf("unexpected HTTP status %s", response.Status)
	}
	if response.ContentLength > maxPackageBytes {
		return "", 0, fmt.Errorf("package exceeds %d byte limit", maxPackageBytes)
	}

	hash := sha256.New()
	size, err := io.Copy(hash, io.LimitReader(response.Body, maxPackageBytes+1))
	if err != nil {
		return "", 0, err
	}
	if size > maxPackageBytes {
		return "", 0, fmt.Errorf("package exceeds %d byte limit", maxPackageBytes)
	}
	return hex.EncodeToString(hash.Sum(nil)), size, nil
}

func localArtifactMetadata(source string) (string, string, int64, error) {
	info, err := os.Stat(source)
	if err != nil {
		return "", "", 0, fmt.Errorf("stat Flat companion: %w", err)
	}
	if !info.Mode().IsRegular() || info.Size() < 1 || info.Size() > maxPackageBytes || !validPackageFilename(filepath.Base(source)) {
		return "", "", 0, fmt.Errorf("invalid Flat companion archive")
	}
	file, err := os.Open(source)
	if err != nil {
		return "", "", 0, err
	}
	defer file.Close()
	hash := sha256.New()
	if _, err := io.Copy(hash, file); err != nil {
		return "", "", 0, err
	}
	return filepath.Base(source), hex.EncodeToString(hash.Sum(nil)), info.Size(), nil
}

func packageURL(baseURL, filename string) (string, error) {
	parsed, err := url.Parse(baseURL)
	if err != nil || parsed.Scheme == "" || parsed.Host == "" || (parsed.Scheme != "https" && parsed.Scheme != "http") || parsed.RawQuery != "" || parsed.Fragment != "" {
		return "", fmt.Errorf("invalid package base URL")
	}
	if !strings.HasSuffix(parsed.Path, "/") {
		parsed.Path += "/"
	}
	parsed.Path += url.PathEscape(filename)
	return parsed.String(), nil
}

func readAtMost(reader io.Reader, limit int64) ([]byte, error) {
	data, err := io.ReadAll(io.LimitReader(reader, limit+1))
	if err != nil {
		return nil, err
	}
	if int64(len(data)) > limit {
		return nil, fmt.Errorf("input exceeds %d byte limit", limit)
	}
	return data, nil
}

func collectConfigEntries(root string) ([]configEntry, error) {
	info, err := os.Lstat(root)
	if err != nil {
		return nil, fmt.Errorf("stat config-dir: %w", err)
	}
	if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return nil, fmt.Errorf("config-dir must be a real directory")
	}

	entries := []configEntry{{zipName: "config/", isDir: true}}
	seenNames := map[string]struct{}{"config/": {}}
	err = filepath.WalkDir(root, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if path == root {
			return nil
		}
		if entry.Type()&os.ModeSymlink != 0 {
			return fmt.Errorf("config contains symlink %q", path)
		}
		relative, err := filepath.Rel(root, path)
		if err != nil {
			return err
		}
		zipName, err := configZIPPath(relative, entry.IsDir())
		if err != nil {
			return err
		}
		if _, exists := seenNames[zipName]; exists {
			return fmt.Errorf("duplicate config ZIP path %q", zipName)
		}
		seenNames[zipName] = struct{}{}

		if entry.IsDir() {
			entries = append(entries, configEntry{zipName: zipName, source: path, isDir: true})
			return nil
		}
		fileInfo, err := entry.Info()
		if err != nil {
			return err
		}
		if !fileInfo.Mode().IsRegular() {
			return fmt.Errorf("config contains non-regular file %q", path)
		}
		entries = append(entries, configEntry{zipName: zipName, source: path})
		return nil
	})
	if err != nil {
		return nil, fmt.Errorf("walk config-dir: %w", err)
	}
	sort.Slice(entries, func(i, j int) bool {
		return entries[i].zipName < entries[j].zipName
	})
	return entries, nil
}

func configZIPPath(relative string, isDir bool) (string, error) {
	clean := filepath.Clean(relative)
	if clean == "." || filepath.IsAbs(clean) {
		return "", fmt.Errorf("unsafe config path %q", relative)
	}
	path := filepath.ToSlash(clean)
	if strings.Contains(path, "\\") {
		return "", fmt.Errorf("unsafe config path %q", relative)
	}
	for _, component := range strings.Split(path, "/") {
		if component == "" || component == "." || component == ".." || strings.Contains(component, ":") {
			return "", fmt.Errorf("unsafe config path %q", relative)
		}
	}
	zipName := "config/" + path
	if isDir {
		zipName += "/"
	}
	return zipName, nil
}

func writeProfileDefinition(output string, manifest []byte, config []configEntry) error {
	outputDir := filepath.Dir(output)
	if err := os.MkdirAll(outputDir, 0700); err != nil {
		return fmt.Errorf("create output directory: %w", err)
	}
	temporary, err := os.CreateTemp(outputDir, "."+filepath.Base(output)+".tmp-*")
	if err != nil {
		return fmt.Errorf("create output: %w", err)
	}
	temporaryName := temporary.Name()
	committed := false
	defer func() {
		if !committed {
			_ = temporary.Close()
			_ = os.Remove(temporaryName)
		}
	}()

	archive := zip.NewWriter(temporary)
	if err := writeZIPFile(archive, "profile-manifest.json", manifest); err != nil {
		return err
	}
	for _, entry := range config {
		if entry.isDir {
			if err := writeZIPDirectory(archive, entry.zipName); err != nil {
				return err
			}
			continue
		}
		if entry.body != nil {
			if err := writeZIPFile(archive, entry.zipName, entry.body); err != nil {
				return err
			}
			continue
		}
		if err := writeZIPSourceFile(archive, entry.zipName, entry.source); err != nil {
			return err
		}
	}
	if err := archive.Close(); err != nil {
		return fmt.Errorf("finish output ZIP: %w", err)
	}
	if err := temporary.Close(); err != nil {
		return fmt.Errorf("close output ZIP: %w", err)
	}
	if err := os.Chmod(temporaryName, 0644); err != nil {
		return fmt.Errorf("set output permissions: %w", err)
	}
	if err := os.Rename(temporaryName, output); err != nil {
		return fmt.Errorf("publish output ZIP: %w", err)
	}
	committed = true
	return nil
}

func writeZIPDirectory(archive *zip.Writer, name string) error {
	header := &zip.FileHeader{Name: name, Method: zip.Store}
	header.SetModTime(deterministicZIPTime)
	header.SetMode(0755)
	if _, err := archive.CreateHeader(header); err != nil {
		return fmt.Errorf("add ZIP directory %q: %w", name, err)
	}
	return nil
}

func writeZIPFile(archive *zip.Writer, name string, data []byte) error {
	header := &zip.FileHeader{Name: name, Method: zip.Store}
	header.SetModTime(deterministicZIPTime)
	header.SetMode(0644)
	writer, err := archive.CreateHeader(header)
	if err != nil {
		return fmt.Errorf("add ZIP file %q: %w", name, err)
	}
	if _, err := writer.Write(data); err != nil {
		return fmt.Errorf("write ZIP file %q: %w", name, err)
	}
	return nil
}

func writeZIPSourceFile(archive *zip.Writer, name, source string) error {
	info, err := os.Lstat(source)
	if err != nil {
		return fmt.Errorf("stat config file %q: %w", source, err)
	}
	if !info.Mode().IsRegular() {
		return fmt.Errorf("config file is not regular %q", source)
	}

	sourceFile, err := os.Open(source)
	if err != nil {
		return fmt.Errorf("open config file %q: %w", source, err)
	}
	defer sourceFile.Close()

	header := &zip.FileHeader{Name: name, Method: zip.Deflate}
	header.SetModTime(deterministicZIPTime)
	header.SetMode(0644)
	writer, err := archive.CreateHeader(header)
	if err != nil {
		return fmt.Errorf("add ZIP file %q: %w", name, err)
	}
	if _, err := io.Copy(writer, sourceFile); err != nil {
		return fmt.Errorf("write ZIP file %q: %w", name, err)
	}
	return nil
}
