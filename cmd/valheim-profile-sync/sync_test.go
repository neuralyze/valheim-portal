package main

import (
	"archive/zip"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type testRoundTripper func(*http.Request) (*http.Response, error)

func (roundTrip testRoundTripper) RoundTrip(request *http.Request) (*http.Response, error) {
	return roundTrip(request)
}

type zipEntry struct {
	Name string
	Body string
}

type testPortal struct {
	server       *httptest.Server
	request      profileRequest
	httpClient   *http.Client
	payload      []byte
	manifest     remoteManifest
	runtime      []byte
	companion    []byte
	diagPlugin   []byte
	packages     map[string][]byte
	payloadCalls int
	packageCalls int
}

func makeTestZip(t *testing.T, entries []zipEntry) []byte {
	t.Helper()
	var output bytes.Buffer
	archive := zip.NewWriter(&output)
	for _, entry := range entries {
		file, err := archive.Create(entry.Name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := file.Write([]byte(entry.Body)); err != nil {
			t.Fatal(err)
		}
	}
	if err := archive.Close(); err != nil {
		t.Fatal(err)
	}
	return output.Bytes()
}

func testPackage(t *testing.T, filename, plugin, body string) (packageDefinition, []byte) {
	t.Helper()
	archive := makeTestZip(t, []zipEntry{{Name: "BepInEx/plugins/" + plugin, Body: body}})
	sum := sha256.Sum256(archive)
	return packageDefinition{
		Namespace: "Author",
		Name:      "Package",
		Version:   "1.0.0",
		Filename:  filename,
		SHA256:    hex.EncodeToString(sum[:]),
		Size:      int64(len(archive)),
	}, archive
}

func testProfileArchive(t *testing.T, request profileRequest, packages []packageDefinition, config []zipEntry, extra []zipEntry) []byte {
	t.Helper()
	definition, err := json.Marshal(profileDefinition{
		Schema:     1,
		World:      request.World,
		Profile:    request.Profile,
		ClientType: request.ClientType,
		Packages:   packages,
	})
	if err != nil {
		t.Fatal(err)
	}
	entries := []zipEntry{{Name: "profile-manifest.json", Body: string(definition)}}
	entries = append(entries, config...)
	entries = append(entries, extra...)
	return makeTestZip(t, entries)
}

func testProfileArchiveWithCompanion(t *testing.T, request profileRequest, packages []packageDefinition, companion companionDefinition, config []zipEntry) []byte {
	t.Helper()
	definition, err := json.Marshal(profileDefinition{
		Schema:     1,
		World:      request.World,
		Profile:    request.Profile,
		ClientType: request.ClientType,
		Packages:   packages,
		Companion:  &companion,
	})
	if err != nil {
		t.Fatal(err)
	}
	entries := []zipEntry{{Name: "profile-manifest.json", Body: string(definition)}}
	entries = append(entries, config...)
	return makeTestZip(t, entries)
}

func testRemoteManifest(request profileRequest, release string, payload []byte) remoteManifest {
	sum := sha256.Sum256(payload)
	return remoteManifest{
		ReleaseID:     release,
		World:         request.World,
		Profile:       request.Profile,
		ClientType:    request.ClientType,
		Version:       "1.0.0",
		ProfileSHA256: hex.EncodeToString(sum[:]),
		ProfileSize:   int64(len(payload)),
	}
}

func newTestPortal(t *testing.T, payload []byte, packages map[string][]byte) *testPortal {
	t.Helper()
	portal := &testPortal{payload: payload, packages: packages}
	portal.server = httptest.NewTLSServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.Header.Get("Authorization") != "Bearer test-token-123456" {
			writer.WriteHeader(http.StatusUnauthorized)
			return
		}
		switch {
		case strings.HasPrefix(request.URL.Path, "/client/manifest/"):
			if err := json.NewEncoder(writer).Encode(portal.manifest); err != nil {
				t.Fatal(err)
			}
		case strings.HasPrefix(request.URL.Path, "/client/payload/"):
			portal.payloadCalls++
			writer.Write(portal.payload)
		case strings.HasPrefix(request.URL.Path, "/client/runtime/"):
			writer.Write(portal.runtime)
		case strings.HasPrefix(request.URL.Path, "/client/companion/"):
			writer.Write(portal.companion)
		case strings.HasPrefix(request.URL.Path, "/client/diagnostics-plugin/"):
			writer.Write(portal.diagPlugin)
		default:
			writer.WriteHeader(http.StatusNotFound)
		}
	}))
	parsed, err := url.Parse(portal.server.URL)
	if err != nil {
		t.Fatal(err)
	}
	portal.request = profileRequest{Portal: parsed, World: "world", Profile: "alpha", ClientType: clientFlat}
	portal.manifest = testRemoteManifest(portal.request, "release-one", payload)
	baseTransport := portal.server.Client().Transport
	if baseTransport == nil {
		baseTransport = http.DefaultTransport
	}
	portal.httpClient = &http.Client{Transport: testRoundTripper(func(request *http.Request) (*http.Response, error) {
		if request.URL.Host == "gcdn.thunderstore.io" {
			portal.packageCalls++
			data, found := portal.packages[filepath.Base(request.URL.Path)]
			if !found {
				return &http.Response{StatusCode: http.StatusNotFound, Status: "404 Not Found", Body: io.NopCloser(strings.NewReader("missing")), Request: request}, nil
			}
			return &http.Response{StatusCode: http.StatusOK, Status: "200 OK", Body: io.NopCloser(bytes.NewReader(data)), Request: request}, nil
		}
		return baseTransport.RoundTrip(request)
	})}
	return portal
}

func (portal *testPortal) Close() {
	portal.server.Close()
}

// The activity log is what a player reads while waiting, and what they paste
// into a bug report. It has to name the release being installed, and name it
// before any bytes move, so the log explains what a later failure was working on.
func TestSyncLogsApprovedReleaseVersionBeforeDownloadingAnything(t *testing.T) {
	request := profileRequest{World: "world", Profile: "alpha", ClientType: clientFlat}
	onlyPackage, archive := testPackage(t, "author-first-1.0.0.zip", "first.dll", "first")
	payload := testProfileArchive(t, request, []packageDefinition{onlyPackage}, []zipEntry{{Name: "config/first.cfg", Body: "first-config"}}, nil)
	portal := newTestPortal(t, payload, map[string][]byte{onlyPackage.Filename: archive})
	defer portal.Close()
	request.Portal = portal.request.Portal
	syncer := newProfileSyncer(portal.httpClient)
	syncer.LocalAppData = t.TempDir()
	var updates []progressUpdate
	syncer.Progress = func(update progressUpdate) { updates = append(updates, update) }

	if changed, err := syncer.syncAuthorized(context.Background(), request, "test-token-123456"); err != nil || !changed {
		t.Fatalf("sync = changed:%t err:%v", changed, err)
	}

	version, download := -1, -1
	for i, update := range updates {
		if version < 0 && strings.Contains(update.Stage, "1.0.0") {
			version = i
		}
		if download < 0 && strings.HasPrefix(update.Stage, "Downloading") {
			download = i
		}
	}
	if version < 0 {
		t.Fatalf("no progress update named the release version: %#v", updates)
	}
	if !strings.Contains(updates[version].Detail, request.Profile) {
		t.Fatalf("version update does not identify the profile: %#v", updates[version])
	}
	if download >= 0 && version > download {
		t.Fatalf("release version logged after downloading started: version at %d, download at %d", version, download)
	}
}

func TestSyncNoChangeChangedAndRemoved(t *testing.T) {
	request := profileRequest{World: "world", Profile: "alpha", ClientType: clientFlat}
	firstPackage, firstArchive := testPackage(t, "author-first-1.0.0.zip", "first.dll", "first")
	firstPayload := testProfileArchive(t, request, []packageDefinition{firstPackage}, []zipEntry{{Name: "config/first.cfg", Body: "first-config"}}, nil)
	portal := newTestPortal(t, firstPayload, map[string][]byte{firstPackage.Filename: firstArchive})
	defer portal.Close()
	request.Portal = portal.request.Portal
	syncer := newProfileSyncer(portal.httpClient)
	syncer.LocalAppData = t.TempDir()
	var updates []progressUpdate
	syncer.Progress = func(update progressUpdate) {
		updates = append(updates, update)
	}

	changed, err := syncer.syncAuthorized(context.Background(), request, "test-token-123456")
	if err != nil || !changed {
		t.Fatalf("first sync = changed:%t err:%v", changed, err)
	}
	foundPackageLog := false
	for _, update := range updates {
		if update.Stage == "Downloaded mod" && update.Detail == "Package" {
			foundPackageLog = true
			break
		}
	}
	if !foundPackageLog {
		t.Fatalf("downloaded-package progress missing from %#v", updates)
	}
	root, err := profileRoot(syncer.LocalAppData, request)
	if err != nil {
		t.Fatal(err)
	}
	if data, err := os.ReadFile(filepath.Join(root, "active", "config", "first.cfg")); err != nil || string(data) != "first-config" {
		t.Fatalf("first config = %q, %v", data, err)
	}
	if data, err := os.ReadFile(filepath.Join(root, "active", "BepInEx", "plugins", "first.dll")); err != nil || string(data) != "first" {
		t.Fatalf("first package = %q, %v", data, err)
	}
	payloadCalls, packageCalls := portal.payloadCalls, portal.packageCalls
	changed, err = syncer.syncAuthorized(context.Background(), request, "test-token-123456")
	if err != nil || changed || portal.payloadCalls != payloadCalls || portal.packageCalls != packageCalls {
		t.Fatalf("no-change sync = changed:%t err:%v payload:%d package:%d", changed, err, portal.payloadCalls, portal.packageCalls)
	}

	secondPackage, secondArchive := testPackage(t, "author-second-1.0.0.zip", "second.dll", "second")
	secondPayload := testProfileArchive(t, request, []packageDefinition{secondPackage}, []zipEntry{{Name: "config/second.cfg", Body: "second-config"}}, nil)
	portal.payload = secondPayload
	portal.packages = map[string][]byte{secondPackage.Filename: secondArchive}
	portal.manifest = testRemoteManifest(request, "release-two", secondPayload)
	changed, err = syncer.syncAuthorized(context.Background(), request, "test-token-123456")
	if err != nil || !changed {
		t.Fatalf("changed sync = changed:%t err:%v", changed, err)
	}
	if _, err := os.Stat(filepath.Join(root, "active", "config", "first.cfg")); !os.IsNotExist(err) {
		t.Fatalf("stale config survived: %v", err)
	}
	if _, err := os.Stat(filepath.Join(root, "active", "BepInEx", "plugins", "first.dll")); !os.IsNotExist(err) {
		t.Fatalf("stale package survived: %v", err)
	}
	if data, err := os.ReadFile(filepath.Join(root, "active", "config", "second.cfg")); err != nil || string(data) != "second-config" {
		t.Fatalf("second config = %q, %v", data, err)
	}
	if _, err := os.Stat(filepath.Join(root, "packages", firstPackage.Filename)); !os.IsNotExist(err) {
		t.Fatalf("stale cache package survived: %v", err)
	}
	if data, err := os.ReadFile(filepath.Join(root, "previous", "config", "first.cfg")); err != nil || string(data) != "first-config" {
		t.Fatalf("previous generation = %q, %v", data, err)
	}
}

func TestSyncRejectsChecksumMismatchesWithoutChangingActiveGeneration(t *testing.T) {
	request := profileRequest{World: "world", Profile: "alpha", ClientType: clientFlat}
	goodPackage, goodArchive := testPackage(t, "author-good-1.0.0.zip", "good.dll", "good")
	goodPayload := testProfileArchive(t, request, []packageDefinition{goodPackage}, []zipEntry{{Name: "config/value.cfg", Body: "good"}}, nil)
	portal := newTestPortal(t, goodPayload, map[string][]byte{goodPackage.Filename: goodArchive})
	defer portal.Close()
	request.Portal = portal.request.Portal
	syncer := newProfileSyncer(portal.httpClient)
	syncer.LocalAppData = t.TempDir()
	if changed, err := syncer.syncAuthorized(context.Background(), request, "test-token-123456"); err != nil || !changed {
		t.Fatalf("initial sync = changed:%t err:%v", changed, err)
	}
	root, err := profileRoot(syncer.LocalAppData, request)
	if err != nil {
		t.Fatal(err)
	}
	badOuter := portal.manifest
	badOuter.ProfileSHA256 = strings.Repeat("0", 64)
	portal.manifest = badOuter
	if _, err := syncer.syncAuthorized(context.Background(), request, "test-token-123456"); err == nil {
		t.Fatal("accepted a profile payload with the wrong checksum")
	}
	if data, err := os.ReadFile(filepath.Join(root, "active", "config", "value.cfg")); err != nil || string(data) != "good" {
		t.Fatalf("outer checksum changed active generation: %q %v", data, err)
	}

	badPackage, badArchive := testPackage(t, "author-bad-1.0.0.zip", "bad.dll", "bad")
	badPackage.SHA256 = strings.Repeat("1", 64)
	badPayload := testProfileArchive(t, request, []packageDefinition{badPackage}, []zipEntry{{Name: "config/value.cfg", Body: "bad"}}, nil)
	portal.payload = badPayload
	portal.packages = map[string][]byte{badPackage.Filename: badArchive}
	portal.manifest = testRemoteManifest(request, "release-bad-package", badPayload)
	if _, err := syncer.syncAuthorized(context.Background(), request, "test-token-123456"); err == nil {
		t.Fatal("accepted a package with the wrong checksum")
	}
	if data, err := os.ReadFile(filepath.Join(root, "active", "config", "value.cfg")); err != nil || string(data) != "good" {
		t.Fatalf("package checksum changed active generation: %q %v", data, err)
	}
}

func TestRollbackRestoresPreviousProfileGeneration(t *testing.T) {
	request := profileRequest{World: "world", Profile: "alpha", ClientType: clientFlat}
	firstPayload := testProfileArchive(t, request, nil, []zipEntry{{Name: "config/value.cfg", Body: "first"}}, nil)
	portal := newTestPortal(t, firstPayload, map[string][]byte{})
	defer portal.Close()
	request.Portal = portal.request.Portal
	syncer := newProfileSyncer(portal.httpClient)
	syncer.LocalAppData = t.TempDir()
	if _, err := syncer.syncAuthorized(context.Background(), request, "test-token-123456"); err != nil {
		t.Fatal(err)
	}
	secondPayload := testProfileArchive(t, request, nil, []zipEntry{{Name: "config/value.cfg", Body: "second"}}, nil)
	portal.payload = secondPayload
	portal.manifest = testRemoteManifest(request, "release-two", secondPayload)
	if _, err := syncer.syncAuthorized(context.Background(), request, "test-token-123456"); err != nil {
		t.Fatal(err)
	}
	root, err := profileRoot(syncer.LocalAppData, request)
	if err != nil {
		t.Fatal(err)
	}
	if err := rollbackGeneration(root); err != nil {
		t.Fatal(err)
	}
	if data, err := os.ReadFile(filepath.Join(root, "active", "config", "value.cfg")); err != nil || string(data) != "first" {
		t.Fatalf("rolled back config = %q, %v", data, err)
	}
}

func TestExtractionRejectsTraversal(t *testing.T) {
	unsafePackage := makeTestZip(t, []zipEntry{{Name: "../outside.dll", Body: "bad"}})
	packagePath := filepath.Join(t.TempDir(), "unsafe.zip")
	if err := os.WriteFile(packagePath, unsafePackage, 0o600); err != nil {
		t.Fatal(err)
	}
	destination := t.TempDir()
	if err := extractPackageArchive(packagePath, destination, packageDefinition{Namespace: "Author", Name: "Unsafe"}); err == nil {
		t.Fatal("accepted traversal in a package archive")
	}
	if _, err := os.Stat(filepath.Join(filepath.Dir(destination), "outside.dll")); !os.IsNotExist(err) {
		t.Fatalf("package traversal wrote outside staging: %v", err)
	}

	portal, err := url.Parse("https://portal.example")
	if err != nil {
		t.Fatal(err)
	}
	request := profileRequest{Portal: portal, World: "world", Profile: "alpha", ClientType: clientFlat}
	unsafeProfile := testProfileArchive(t, request, nil, []zipEntry{{Name: "config/value.cfg", Body: "safe"}}, []zipEntry{{Name: "config/../outside.cfg", Body: "bad"}})
	profilePath := filepath.Join(t.TempDir(), "unsafe-profile.zip")
	if err := os.WriteFile(profilePath, unsafeProfile, 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := unpackProfileDefinition(profilePath, t.TempDir(), request); err == nil {
		t.Fatal("accepted traversal in a profile archive")
	}
}

func TestPackageExtractionNormalizesThunderstoreLayouts(t *testing.T) {
	destination := t.TempDir()
	bepInExArchive := makeTestZip(t, []zipEntry{
		{Name: "BepInExPack_Valheim/manifest.json", Body: "{}"},
		{Name: "BepInExPack_Valheim/winhttp.dll", Body: "loader"},
		{Name: "BepInExPack_Valheim/doorstop_config.ini", Body: "config"},
		{Name: "BepInExPack_Valheim/BepInEx/core/BepInEx.Preloader.dll", Body: "preloader"},
	})
	bepInExPath := filepath.Join(t.TempDir(), "bepinex.zip")
	if err := os.WriteFile(bepInExPath, bepInExArchive, 0o600); err != nil {
		t.Fatal(err)
	}
	bepInEx := packageDefinition{Namespace: "denikson", Name: "BepInExPack_Valheim"}
	if err := extractPackageArchive(bepInExPath, destination, bepInEx); err != nil {
		t.Fatal(err)
	}
	for name, want := range map[string]string{
		"winhttp.dll":                        "loader",
		"doorstop_config.ini":                "config",
		"BepInEx/core/BepInEx.Preloader.dll": "preloader",
	} {
		got, err := os.ReadFile(filepath.Join(destination, filepath.FromSlash(name)))
		if err != nil || string(got) != want {
			t.Fatalf("%s = %q, %v", name, got, err)
		}
	}
	if _, err := os.Stat(filepath.Join(destination, "manifest.json")); !os.IsNotExist(err) {
		t.Fatalf("BepInEx package metadata was installed: %v", err)
	}

	modArchive := makeTestZip(t, []zipEntry{
		{Name: "manifest.json", Body: "{}"},
		{Name: "plugins\\Translations\\", Body: ""},
		{Name: "plugins\\Translations\\English.json", Body: "translation"},
		{Name: "plugins\\DragoonCapes.dll", Body: "plugin"},
		{Name: "patchers/LoadTimeProfiler.dll", Body: "patcher"},
	})
	modPath := filepath.Join(t.TempDir(), "mod.zip")
	if err := os.WriteFile(modPath, modArchive, 0o600); err != nil {
		t.Fatal(err)
	}
	mod := packageDefinition{Namespace: "HappyDragoon", Name: "DragoonCapes"}
	if err := extractPackageArchive(modPath, destination, mod); err != nil {
		t.Fatal(err)
	}
	pluginRoot := filepath.Join(destination, "BepInEx", "plugins", "HappyDragoon-DragoonCapes")
	for name, want := range map[string]string{"DragoonCapes.dll": "plugin", "Translations/English.json": "translation"} {
		got, err := os.ReadFile(filepath.Join(pluginRoot, filepath.FromSlash(name)))
		if err != nil || string(got) != want {
			t.Fatalf("%s = %q, %v", name, got, err)
		}
	}
	patcher, err := os.ReadFile(filepath.Join(destination, "BepInEx", "patchers", "LoadTimeProfiler.dll"))
	if err != nil || string(patcher) != "patcher" {
		t.Fatalf("patcher = %q, %v", patcher, err)
	}
	if _, err := os.Stat(filepath.Join(pluginRoot, "manifest.json")); !os.IsNotExist(err) {
		t.Fatalf("mod package metadata was installed: %v", err)
	}
}

// TestPackageExtractionKeepsAssetsBesideAssembly reproduces the More_World_Locations_AIO
// failure: the assembly sits at the archive root while its asset bundles ship under
// "plugins/". Mods resolve bundles relative to their own directory, so a surviving
// "plugins/" segment makes every bundle unreadable even though the assembly loads.
func TestPackageExtractionKeepsAssetsBesideAssembly(t *testing.T) {
	destination := t.TempDir()
	archive := makeTestZip(t, []zipEntry{
		{Name: "manifest.json", Body: "{}"},
		{Name: "More_World_Locations_AIO.dll", Body: "assembly"},
		{Name: "assetBundleManifest_full", Body: "manifest"},
		{Name: "plugins", Body: ""},
		{Name: "plugins/Bundles/mwl_ruinswell1", Body: "bundle"},
	})
	archivePath := filepath.Join(t.TempDir(), "mwl.zip")
	if err := os.WriteFile(archivePath, archive, 0o600); err != nil {
		t.Fatal(err)
	}
	pkg := packageDefinition{Namespace: "warpalicious", Name: "More_World_Locations_AIO"}
	if err := extractPackageArchive(archivePath, destination, pkg); err != nil {
		t.Fatal(err)
	}
	pluginRoot := filepath.Join(destination, "BepInEx", "plugins", "warpalicious-More_World_Locations_AIO")
	for name, want := range map[string]string{
		"More_World_Locations_AIO.dll": "assembly",
		"assetBundleManifest_full":     "manifest",
		"Bundles/mwl_ruinswell1":       "bundle",
	} {
		got, err := os.ReadFile(filepath.Join(pluginRoot, filepath.FromSlash(name)))
		if err != nil || string(got) != want {
			t.Fatalf("%s = %q, %v", name, got, err)
		}
	}
	if _, err := os.Stat(filepath.Join(pluginRoot, "plugins")); !os.IsNotExist(err) {
		t.Fatalf("stale plugins/ segment survived extraction: %v", err)
	}
}

func TestRepairLoadTimeProfilerPatcherMigratesLegacyPluginLayout(t *testing.T) {
	root := t.TempDir()
	source := filepath.Join(root, "active", "BepInEx", "plugins", "sighsorry-LoadTimeProfiler", "patchers", "LoadTimeProfiler.dll")
	if err := os.MkdirAll(filepath.Dir(source), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(source, []byte("patcher"), 0o600); err != nil {
		t.Fatal(err)
	}
	target := filepath.Join(root, "active", "BepInEx", "patchers", "LoadTimeProfiler.dll")
	if err := os.MkdirAll(filepath.Dir(target), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := repairLoadTimeProfilerPatcher(root); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(target)
	if err != nil || string(data) != "patcher" {
		t.Fatalf("repaired patcher = %q, %v", data, err)
	}
	if _, err := os.Stat(filepath.Dir(filepath.Dir(source))); !os.IsNotExist(err) {
		t.Fatalf("legacy plugin root remains: %v", err)
	}
}

func TestRemoveRetiredDragonRidersRemovesOnlyItsManagedDirectory(t *testing.T) {
	root := t.TempDir()
	retired := filepath.Join(root, "active", "BepInEx", "plugins", "Yggdrah-DragonRiders", "DragonRiders.dll")
	keep := filepath.Join(root, "active", "BepInEx", "plugins", "Yggdrah-BetterRiding", "BetterRiding.dll")
	for _, path := range []string{retired, keep} {
		if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte("plugin"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	if err := removeRetiredDragonRiders(root); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Dir(retired)); !os.IsNotExist(err) {
		t.Fatalf("retired package remains: %v", err)
	}
	if _, err := os.Stat(keep); err != nil {
		t.Fatalf("unrelated plugin removed: %v", err)
	}
}

func TestIncompleteBepInExProfileRequiresRepair(t *testing.T) {
	root := t.TempDir()
	state := profileState{Packages: []packageDefinition{{Namespace: "denikson", Name: "BepInExPack_Valheim"}}}
	if activeProfileRuntimeReady(root, state) {
		t.Fatal("accepted a profile without its loader")
	}
	for _, name := range []string{
		filepath.Join("BepInEx", "core", "BepInEx.Preloader.dll"),
		"winhttp.dll",
		"doorstop_config.ini",
	} {
		path := filepath.Join(root, "active", name)
		if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte("runtime"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	if !activeProfileRuntimeReady(root, state) {
		t.Fatal("rejected a complete BepInEx profile")
	}
}

func TestSyncRepairsIncompleteThunderstoreProfile(t *testing.T) {
	request := profileRequest{World: "world", Profile: "alpha", ClientType: clientFlat}
	definePackage := func(namespace, name, filename string, archive []byte) packageDefinition {
		sum := sha256.Sum256(archive)
		return packageDefinition{
			Namespace: namespace,
			Name:      name,
			Version:   "1.0.0",
			Filename:  filename,
			SHA256:    hex.EncodeToString(sum[:]),
			Size:      int64(len(archive)),
		}
	}
	modArchive := makeTestZip(t, []zipEntry{
		{Name: "manifest.json", Body: "{}"},
		{Name: "PlantEasily.dll", Body: "plugin"},
	})
	bepInExArchive := makeTestZip(t, []zipEntry{
		{Name: "BepInExPack_Valheim/winhttp.dll", Body: "loader"},
		{Name: "BepInExPack_Valheim/doorstop_config.ini", Body: "config"},
		{Name: "BepInExPack_Valheim/BepInEx/core/BepInEx.Preloader.dll", Body: "preloader"},
	})
	mod := definePackage("Advize", "PlantEasily", "Advize-PlantEasily-1.0.0.zip", modArchive)
	bepInEx := definePackage("denikson", "BepInExPack_Valheim", "denikson-BepInExPack_Valheim-1.0.0.zip", bepInExArchive)
	payload := testProfileArchive(t, request, []packageDefinition{mod, bepInEx}, []zipEntry{{Name: "config/settings.cfg", Body: "settings"}}, nil)
	portal := newTestPortal(t, payload, map[string][]byte{
		mod.Filename:     modArchive,
		bepInEx.Filename: bepInExArchive,
	})
	defer portal.Close()
	request.Portal = portal.request.Portal
	syncer := newProfileSyncer(portal.httpClient)
	syncer.LocalAppData = t.TempDir()

	if changed, err := syncer.syncAuthorized(context.Background(), request, "test-token-123456"); err != nil || !changed {
		t.Fatalf("initial sync = changed:%t err:%v", changed, err)
	}
	root, err := profileRoot(syncer.LocalAppData, request)
	if err != nil {
		t.Fatal(err)
	}
	loader := filepath.Join(root, "active", "winhttp.dll")
	plugin := filepath.Join(root, "active", "BepInEx", "plugins", "Advize-PlantEasily", "PlantEasily.dll")
	if data, err := os.ReadFile(loader); err != nil || string(data) != "loader" {
		t.Fatalf("loader = %q, %v", data, err)
	}
	if data, err := os.ReadFile(plugin); err != nil || string(data) != "plugin" {
		t.Fatalf("plugin = %q, %v", data, err)
	}

	if err := os.Remove(loader); err != nil {
		t.Fatal(err)
	}
	payloadCalls, packageCalls := portal.payloadCalls, portal.packageCalls
	if changed, err := syncer.syncAuthorized(context.Background(), request, "test-token-123456"); err != nil || !changed {
		t.Fatalf("repair sync = changed:%t err:%v", changed, err)
	}
	if portal.payloadCalls != payloadCalls+1 {
		t.Fatalf("repair payload calls = %d, want %d", portal.payloadCalls, payloadCalls+1)
	}
	if portal.packageCalls != packageCalls {
		t.Fatalf("repair redownloaded cached packages: %d, want %d", portal.packageCalls, packageCalls)
	}
	if data, err := os.ReadFile(loader); err != nil || string(data) != "loader" {
		t.Fatalf("repaired loader = %q, %v", data, err)
	}

	config := filepath.Join(root, "active", "config", "settings.cfg")
	if err := os.WriteFile(config, []byte("modified"), 0o600); err != nil {
		t.Fatal(err)
	}
	payloadCalls, packageCalls = portal.payloadCalls, portal.packageCalls
	if changed, err := syncer.syncAuthorized(context.Background(), request, "test-token-123456"); err != nil || !changed {
		t.Fatalf("config repair sync = changed:%t err:%v", changed, err)
	}
	if portal.payloadCalls != payloadCalls+1 || portal.packageCalls != packageCalls {
		t.Fatalf("config repair calls payload:%d packages:%d", portal.payloadCalls, portal.packageCalls)
	}
	if data, err := os.ReadFile(config); err != nil || string(data) != "settings" {
		t.Fatalf("repaired config = %q, %v", data, err)
	}
	if data, err := os.ReadFile(filepath.Join(root, "active", "BepInEx", "config", "settings.cfg")); err != nil || string(data) != "settings" {
		t.Fatalf("BepInEx config = %q, %v", data, err)
	}
}

func TestSyncInstallsFlatCompanionAfterPackages(t *testing.T) {
	request := profileRequest{World: "world", Profile: "flat", ClientType: clientFlat}
	packageInfo, packageArchive := testPackage(t, "author-package.zip", "ValheimVRMod.dll", "generic")
	companionArchive := makeTestZip(t, []zipEntry{
		{Name: "BepInEx/plugins/ValheimVRMod.dll", Body: "local"},
		{Name: "BepInEx/config/org.bepinex.plugins.valheimvrmod.cfg", Body: "[Immutable]\nnonVrPlayer = true\n"},
	})
	companionSum := sha256.Sum256(companionArchive)
	companion := companionDefinition{
		Filename: "flat-companion.zip",
		SHA256:   hex.EncodeToString(companionSum[:]),
		Size:     int64(len(companionArchive)),
	}
	payload := testProfileArchiveWithCompanion(t, request, []packageDefinition{packageInfo}, companion, []zipEntry{{Name: "config/value.cfg", Body: "value"}})
	portal := newTestPortal(t, payload, map[string][]byte{packageInfo.Filename: packageArchive})
	request.Portal = portal.request.Portal
	defer portal.Close()
	portal.request = request
	portal.manifest = testRemoteManifest(request, "flat-release", payload)
	portal.companion = companionArchive
	portal.manifest.CompanionSHA256 = companion.SHA256
	portal.manifest.CompanionSize = companion.Size
	syncer := newProfileSyncer(portal.httpClient)
	syncer.LocalAppData = t.TempDir()
	if changed, err := syncer.syncAuthorized(context.Background(), request, "test-token-123456"); err != nil || !changed {
		t.Fatalf("sync = changed:%t err:%v", changed, err)
	}
	root, err := profileRoot(syncer.LocalAppData, request)
	if err != nil {
		t.Fatal(err)
	}
	value, err := os.ReadFile(filepath.Join(root, "active", "BepInEx", "plugins", "ValheimVRMod.dll"))
	if err != nil || string(value) != "local" {
		t.Fatalf("companion did not replace generic plugin: %q, %v", value, err)
	}
}

func TestSyncReportsCompanionOnlyChangeWithoutModDownloads(t *testing.T) {
	request := profileRequest{World: "world", Profile: "flat", ClientType: clientFlat}
	packageInfo, packageArchive := testPackage(t, "author-package.zip", "example.dll", "package")
	makeCompanion := func(body string) ([]byte, companionDefinition) {
		archive := makeTestZip(t, []zipEntry{
			{Name: "BepInEx/plugins/ValheimVRMod.dll", Body: body},
			{Name: "BepInEx/config/org.bepinex.plugins.valheimvrmod.cfg", Body: "[Immutable]\nnonVrPlayer = true\n"},
		})
		sum := sha256.Sum256(archive)
		return archive, companionDefinition{Filename: "flat-companion.zip", SHA256: hex.EncodeToString(sum[:]), Size: int64(len(archive))}
	}
	firstCompanion, firstDefinition := makeCompanion("first")
	firstPayload := testProfileArchiveWithCompanion(t, request, []packageDefinition{packageInfo}, firstDefinition, []zipEntry{{Name: "config/value.cfg", Body: "value"}})
	portal := newTestPortal(t, firstPayload, map[string][]byte{packageInfo.Filename: packageArchive})
	defer portal.Close()
	request.Portal, portal.request = portal.request.Portal, request
	portal.manifest = testRemoteManifest(request, "flat-one", firstPayload)
	portal.companion, portal.manifest.CompanionSHA256, portal.manifest.CompanionSize = firstCompanion, firstDefinition.SHA256, firstDefinition.Size
	syncer := newProfileSyncer(portal.httpClient)
	syncer.LocalAppData = t.TempDir()
	if changed, err := syncer.syncAuthorized(context.Background(), request, "test-token-123456"); err != nil || !changed {
		t.Fatalf("first sync = changed:%t err:%v", changed, err)
	}
	packageCalls := portal.packageCalls
	secondCompanion, secondDefinition := makeCompanion("second")
	secondPayload := testProfileArchiveWithCompanion(t, request, []packageDefinition{packageInfo}, secondDefinition, []zipEntry{{Name: "config/value.cfg", Body: "value"}})
	portal.payload = secondPayload
	portal.manifest = testRemoteManifest(request, "flat-two", secondPayload)
	portal.companion, portal.manifest.CompanionSHA256, portal.manifest.CompanionSize = secondCompanion, secondDefinition.SHA256, secondDefinition.Size
	var updates []progressUpdate
	syncer.Progress = func(update progressUpdate) { updates = append(updates, update) }
	if changed, err := syncer.syncAuthorized(context.Background(), request, "test-token-123456"); err != nil || !changed {
		t.Fatalf("companion update = changed:%t err:%v", changed, err)
	}
	if portal.packageCalls != packageCalls {
		t.Fatalf("unchanged package was downloaded again: %d -> %d", packageCalls, portal.packageCalls)
	}
	wantChanges := "Mods: 0 added, 0 updated, 0 removed, 1 unchanged. Flat ValheimVR companion changed."
	wantRebuild := "Reused 1 cached mod archives; downloaded 0."
	foundChanges, foundRebuild := false, false
	for _, update := range updates {
		foundChanges = foundChanges || (update.Stage == "Changes detected" && update.Detail == wantChanges)
		foundRebuild = foundRebuild || (update.Stage == "Rebuilding profile" && update.Detail == wantRebuild)
	}
	if !foundChanges || !foundRebuild {
		t.Fatalf("companion-only progress = %#v", updates)
	}
}

func TestValidateCompanionDefinitionRejectsPublishedArtifactMismatch(t *testing.T) {
	definition := profileDefinition{
		ClientType: clientFlat,
		Companion: &companionDefinition{
			Filename: "flat-companion.zip",
			SHA256:   strings.Repeat("a", 64),
			Size:     42,
		},
	}
	manifest := remoteManifest{
		ClientType:      clientFlat,
		CompanionSHA256: strings.Repeat("b", 64),
		CompanionSize:   42,
	}
	if err := validateCompanionDefinition(definition, manifest); err == nil {
		t.Fatal("accepted companion metadata for a different published artifact")
	}
}
