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

	"github.com/neuralyze/valheim-portal/internal/valheimvr"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func TestBuildProfileDefinitionSortsManifestAndCapturesHashes(t *testing.T) {
	dir := t.TempDir()
	manifestPath := writeManagedManifest(t, dir, `{
		"schema_version": 1,
		"world_name": "world-one",
		"packages": [
			{"identifier": "Team-Zeta", "version": "1.2.3"}
		],
		"client_only_packages": [
			{"identifier": "Team-Alpha", "version": "2.0.1"}
		]
	}`)
	configDir := filepath.Join(dir, "config")
	if err := os.MkdirAll(filepath.Join(configDir, "empty"), 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(configDir, "nested"), 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(configDir, "nested", "settings.cfg"), []byte("setting=true\n"), 0600); err != nil {
		t.Fatal(err)
	}
	companion := filepath.Join(dir, "flat-companion.zip")
	if err := os.WriteFile(companion, []byte("locally-built-companion"), 0600); err != nil {
		t.Fatal(err)
	}

	packages := map[string][]byte{
		"Team-Alpha-2.0.1.zip": []byte("alpha package"),
		"Team-Zeta-1.2.3.zip":  []byte("zeta package"),
	}
	requests := make([]string, 0, len(packages))
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		name := strings.TrimPrefix(r.URL.Path, "/")
		requests = append(requests, name)
		body, ok := packages[name]
		if !ok {
			http.NotFound(w, r)
			return
		}
		_, _ = w.Write(body)
	}))
	defer server.Close()

	options := builderOptions{
		SourceManifestPath: manifestPath,
		World:              "world-one",
		Profile:            "profile-one",
		ClientType:         "flat",
		Audience:           "player",
		ConfigDir:          configDir,
		Output:             filepath.Join(dir, "profile-one.zip"),
		PackageBaseURL:     server.URL + "/",
		HTTPClient:         server.Client(),
		FlatCompanion:      companion,
	}
	if err := buildProfileDefinition(context.Background(), options); err != nil {
		t.Fatal(err)
	}

	manifestData := readZIPEntry(t, options.Output, "profile-manifest.json")
	var manifest profileManifest
	if err := json.Unmarshal(manifestData, &manifest); err != nil {
		t.Fatal(err)
	}
	if manifest.Schema != 1 || manifest.World != options.World || manifest.Profile != options.Profile || manifest.ClientType != options.ClientType {
		t.Fatalf("unexpected manifest identity: %+v", manifest)
	}
	if len(manifest.Packages) != 2 {
		t.Fatalf("package count = %d, want 2", len(manifest.Packages))
	}
	if manifest.Packages[0].Filename != "Team-Alpha-2.0.1.zip" || manifest.Packages[1].Filename != "Team-Zeta-1.2.3.zip" {
		t.Fatalf("packages are not filename sorted: %+v", manifest.Packages)
	}
	for _, pkg := range manifest.Packages {
		body := packages[pkg.Filename]
		if pkg.SHA256 != sha256Hex(body) || pkg.Size != int64(len(body)) {
			t.Fatalf("unexpected digest for %s: %+v", pkg.Filename, pkg)
		}
	}
	if manifest.Companion == nil || manifest.Companion.Filename != "flat-companion.zip" || manifest.Companion.SHA256 != sha256Hex([]byte("locally-built-companion")) || manifest.Companion.Size != int64(len("locally-built-companion")) {
		t.Fatalf("unexpected companion metadata: %+v", manifest.Companion)
	}
	if !reflect.DeepEqual(requests, []string{"Team-Alpha-2.0.1.zip", "Team-Zeta-1.2.3.zip"}) {
		t.Fatalf("unexpected package requests: %v", requests)
	}

	wantEntries := []string{"profile-manifest.json", "config/", "config/empty/", "config/nested/", "config/nested/settings.cfg"}
	if entries := zipEntries(t, options.Output); !reflect.DeepEqual(entries, wantEntries) {
		t.Fatalf("ZIP entries = %v, want %v", entries, wantEntries)
	}
	if got := string(readZIPEntry(t, options.Output, "config/nested/settings.cfg")); got != "setting=true\n" {
		t.Fatalf("config content = %q", got)
	}

	secondOutput := filepath.Join(dir, "profile-one-again.zip")
	options.Output = secondOutput
	if err := buildProfileDefinition(context.Background(), options); err != nil {
		t.Fatal(err)
	}
	first, err := os.ReadFile(filepath.Join(dir, "profile-one.zip"))
	if err != nil {
		t.Fatal(err)
	}
	second, err := os.ReadFile(secondOutput)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(first, second) {
		t.Fatal("profile ZIP is not deterministic")
	}
}

// A shared profile is schema 2 and belongs to no world, so there is no world_name to
// match. Schema 1 kept that check because a per-world copy carrying the wrong world's
// name meant the wrong mod set was about to be published under this world's slug; the
// check has to survive for those, and a schema 2 manifest that still names a world is a
// half-migrated file rather than a shared one.
func TestBuildAcceptsASharedProfileAndStillGuardsPerWorldCopies(t *testing.T) {
	dir := t.TempDir()
	configDir := filepath.Join(dir, "config")
	if err := os.Mkdir(configDir, 0755); err != nil {
		t.Fatal(err)
	}
	build := func(manifest string, output string) error {
		return buildProfileDefinition(context.Background(), builderOptions{
			SourceManifestPath: writeManagedManifest(t, dir, manifest),
			World:              "world-one",
			Profile:            "world-one-non-vr",
			ClientType:         "flat",
			Audience:           "player",
			ConfigDir:          configDir,
			Output:             filepath.Join(dir, output),
			TrueNonVR:          true,
		})
	}

	if err := build(`{"schema_version":2,"packages":[]}`, "shared.zip"); err != nil {
		t.Fatalf("shared profile = %v", err)
	}
	if err := build(`{"schema_version":2,"world_name":"world-one","packages":[]}`, "named.zip"); err == nil ||
		!strings.Contains(err.Error(), "must not name a world") {
		t.Fatalf("shared profile naming a world = %v", err)
	}
	if err := build(`{"schema_version":1,"world_name":"other-world","packages":[]}`, "mismatch.zip"); err == nil ||
		!strings.Contains(err.Error(), "does not match") {
		t.Fatalf("per-world copy for another world = %v", err)
	}
	if err := build(`{"schema_version":3,"packages":[]}`, "future.zip"); err == nil ||
		!strings.Contains(err.Error(), "unsupported managed profile manifest schema 3") {
		t.Fatalf("unknown schema = %v", err)
	}
}

func TestBuildProfileDefinitionRejectsInvalidInput(t *testing.T) {
	dir := t.TempDir()
	configDir := filepath.Join(dir, "config")
	if err := os.Mkdir(configDir, 0755); err != nil {
		t.Fatal(err)
	}
	validManifest := writeManagedManifest(t, dir, `{"schema_version":1,"world_name":"world-one","packages":[]}`)
	companion := filepath.Join(dir, "flat-companion.zip")
	if err := os.WriteFile(companion, []byte("companion"), 0600); err != nil {
		t.Fatal(err)
	}

	invalidProfile := builderOptions{
		SourceManifestPath: validManifest,
		World:              "world-one",
		Profile:            "not/a-profile",
		ClientType:         "flat",
		Audience:           "player",
		ConfigDir:          configDir,
		Output:             filepath.Join(dir, "output.zip"),
		FlatCompanion:      companion,
	}
	if err := buildProfileDefinition(context.Background(), invalidProfile); err == nil || !strings.Contains(err.Error(), "invalid profile identifier") {
		t.Fatalf("invalid profile error = %v", err)
	}

	duplicateManifest := writeManagedManifest(t, dir, `{
		"schema_version": 1,
		"world_name": "world-one",
		"packages": [
			{"identifier": "Team-Duplicate", "version": "1.0.0"},
			{"identifier": "Team-Duplicate", "version": "1.0.0"}
		]
	}`)
	duplicatePackage := invalidProfile
	duplicatePackage.SourceManifestPath = duplicateManifest
	duplicatePackage.Profile = "valid-profile"
	if err := buildProfileDefinition(context.Background(), duplicatePackage); err == nil || !strings.Contains(err.Error(), "duplicate package filename") {
		t.Fatalf("duplicate package error = %v", err)
	}

	missingCompanion := duplicatePackage
	missingCompanion.FlatCompanion = ""
	if err := validateBuilderOptions(missingCompanion); err == nil || !strings.Contains(err.Error(), "flat-companion is required") {
		t.Fatalf("missing Flat companion error = %v", err)
	}

	trueNonVR := missingCompanion
	trueNonVR.TrueNonVR = true
	if err := validateBuilderOptions(trueNonVR); err != nil {
		t.Fatalf("true nonVR validation error = %v", err)
	}
	trueNonVR.FlatCompanion = companion
	if err := validateBuilderOptions(trueNonVR); err == nil || !strings.Contains(err.Error(), "cannot include a flat-companion") {
		t.Fatalf("true nonVR companion error = %v", err)
	}
	if err := validateTrueNonVRPackages([]packageManifest{{Namespace: "geekstreet", Name: "BackpacksVRFix"}}); err == nil || !strings.Contains(err.Error(), "contains ValheimVR package") {
		t.Fatalf("true nonVR package error = %v", err)
	}

	vrWithCompanion := duplicatePackage
	vrWithCompanion.ClientType = "vr"
	if err := validateBuilderOptions(vrWithCompanion); err == nil || !strings.Contains(err.Error(), "flat-companion is not valid") {
		t.Fatalf("VR Flat companion error = %v", err)
	}
}

func writeManagedManifest(t *testing.T, dir, content string) string {
	t.Helper()
	file, err := os.CreateTemp(dir, "profile-manifest-*.json")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := io.WriteString(file, content); err != nil {
		t.Fatal(err)
	}
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}
	return file.Name()
}

func readZIPEntry(t *testing.T, path, name string) []byte {
	t.Helper()
	archive, err := zip.OpenReader(path)
	if err != nil {
		t.Fatal(err)
	}
	defer archive.Close()
	for _, entry := range archive.File {
		if entry.Name != name {
			continue
		}
		reader, err := entry.Open()
		if err != nil {
			t.Fatal(err)
		}
		data, err := io.ReadAll(reader)
		closeErr := reader.Close()
		if err != nil {
			t.Fatal(err)
		}
		if closeErr != nil {
			t.Fatal(closeErr)
		}
		return data
	}
	t.Fatalf("ZIP entry %q not found", name)
	return nil
}

func zipEntries(t *testing.T, path string) []string {
	t.Helper()
	archive, err := zip.OpenReader(path)
	if err != nil {
		t.Fatal(err)
	}
	defer archive.Close()
	entries := make([]string, 0, len(archive.File))
	for _, entry := range archive.File {
		entries = append(entries, entry.Name)
	}
	return entries
}

func sha256Hex(data []byte) string {
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}

func TestStripValheimVRPackagesRemovesOnlyIntegrationPackages(t *testing.T) {
	packages := []packageManifest{
		{Namespace: "geekstreet", Name: "BackpacksVRFix"},
		{Namespace: "Advize", Name: "PlantEasily"},
		{Namespace: "Azumatt", Name: "AzuAutoStore"},
	}
	kept, removed := stripValheimVRPackages(packages)

	if len(removed) != 1 || removed[0] != "geekstreet-BackpacksVRFix" {
		t.Fatalf("removed = %v, want just the VR integration package", removed)
	}
	if len(kept) != 2 {
		t.Fatalf("kept %d packages, want 2 non-VR packages", len(kept))
	}
	for _, packageInfo := range kept {
		if valheimvr.IsIntegrationPackage(packageInfo.Namespace + "-" + packageInfo.Name) {
			t.Fatalf("kept a VR integration package: %s-%s", packageInfo.Namespace, packageInfo.Name)
		}
	}
	// The stripped set must satisfy the same invariant the builder asserts after stripping.
	if err := validateTrueNonVRPackages(kept); err != nil {
		t.Fatalf("stripped set still fails the true-nonvr invariant: %v", err)
	}
}

func TestStripValheimVRPackagesLeavesCleanSetUntouched(t *testing.T) {
	packages := []packageManifest{{Namespace: "Advize", Name: "PlantEasily"}}
	kept, removed := stripValheimVRPackages(packages)
	if len(removed) != 0 {
		t.Fatalf("removed = %v, want nothing removed from an already VR-free set", removed)
	}
	if len(kept) != 1 {
		t.Fatalf("kept %d, want 1", len(kept))
	}
}

// The audience has no default: a wrong value either hides the download every player needs
// or offers the console to all of them, so an unset one has to fail loudly. It is
// validated here and recorded by seed-release; it must NOT reach the archive, because an
// installed client decodes profile-manifest.json with DisallowUnknownFields.
func TestBuildRefusesAMissingOrUnknownAudience(t *testing.T) {
	dir := t.TempDir()
	configDir := filepath.Join(dir, "config")
	if err := os.Mkdir(configDir, 0755); err != nil {
		t.Fatal(err)
	}
	manifest := writeManagedManifest(t, dir, `{"schema_version":2,"packages":[]}`)
	build := func(audience, output string) error {
		return buildProfileDefinition(context.Background(), builderOptions{
			SourceManifestPath: manifest,
			World:              "world-one",
			Profile:            "world-one-non-vr",
			ClientType:         "flat",
			Audience:           audience,
			ConfigDir:          configDir,
			Output:             filepath.Join(dir, output),
			TrueNonVR:          true,
		})
	}
	for _, audience := range []string{"", "operator", "Admin"} {
		if err := build(audience, "refused.zip"); err == nil || !strings.Contains(err.Error(), "audience must be player or admin") {
			t.Fatalf("audience %q = %v", audience, err)
		}
	}
	if err := build("admin", "admin.zip"); err != nil {
		t.Fatalf("admin audience = %v", err)
	}
}

// The definition is read by clients that reject any key they do not know, so the archive
// must contain exactly the keys those clients expect. On 2026-08-17 an "audience" field
// shipped here and every player's install failed with `unknown field "audience"`.
func TestTheBuiltDefinitionCarriesNoPortalOnlyFields(t *testing.T) {
	dir := t.TempDir()
	configDir := filepath.Join(dir, "config")
	if err := os.Mkdir(configDir, 0755); err != nil {
		t.Fatal(err)
	}
	output := filepath.Join(dir, "profile.zip")
	if err := buildProfileDefinition(context.Background(), builderOptions{
		SourceManifestPath: writeManagedManifest(t, dir, `{"schema_version":2,"packages":[]}`),
		World:              "world-one",
		Profile:            "world-one-vr-flat-admin",
		ClientType:         "flat",
		Audience:           "admin",
		ConfigDir:          configDir,
		Output:             output,
		TrueNonVR:          true,
	}); err != nil {
		t.Fatal(err)
	}

	archive, err := zip.OpenReader(output)
	if err != nil {
		t.Fatal(err)
	}
	defer archive.Close()
	var manifest map[string]any
	for _, file := range archive.File {
		if file.Name != "profile-manifest.json" {
			continue
		}
		reader, err := file.Open()
		if err != nil {
			t.Fatal(err)
		}
		if err := json.NewDecoder(reader).Decode(&manifest); err != nil {
			t.Fatal(err)
		}
		reader.Close()
	}
	if manifest == nil {
		t.Fatal("no profile-manifest.json in the built definition")
	}
	for key := range manifest {
		switch key {
		case "schema", "world", "profile", "client_type", "packages", "companion":
		default:
			t.Fatalf("definition carries %q, which an installed client will reject", key)
		}
	}
	if manifest["schema"] != float64(1) {
		t.Fatalf("schema = %v; a bump also breaks every installed client (sync.go rejects != 1)", manifest["schema"])
	}
}
