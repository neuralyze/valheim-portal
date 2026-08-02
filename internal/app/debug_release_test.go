package app

import (
	"archive/zip"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"io"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func seedRelease(t *testing.T, server *Server, world, profile, clientType, version string) Release {
	t.Helper()
	release := Release{ID: profile + "-" + version, World: world, Profile: profile, ClientType: clientType, Version: version, Notes: "seed"}
	if err := server.store.CreateRelease(context.Background(), release, "admin"); err != nil {
		t.Fatal(err)
	}
	addProfileArtifact(t, server.store, server.cfg.ArtifactRoot, release)
	if clientType == "vr" {
		addVRRuntimeArtifact(t, server.store, server.cfg.ArtifactRoot, release)
	}
	if err := server.store.Publish(context.Background(), release.ID, "admin"); err != nil {
		t.Fatal(err)
	}
	return release
}

func TestBumpPatchVersion(t *testing.T) {
	for input, want := range map[string]string{"2.1.34": "2.1.35", "1.0": "1.1", "2.1.9": "2.1.10"} {
		got, err := bumpPatchVersion(input)
		if err != nil || got != want {
			t.Fatalf("bump(%q) = %q, %v; want %q", input, got, err, want)
		}
	}
	for _, bad := range []string{"", "2", "2.1.x", "latest", "2.1.-1"} {
		if got, err := bumpPatchVersion(bad); err == nil {
			t.Fatalf("bump(%q) = %q, want error", bad, got)
		}
	}
}

func zipEntries(t *testing.T, body []byte) map[string]string {
	t.Helper()
	reader, err := zip.NewReader(bytes.NewReader(body), int64(len(body)))
	if err != nil {
		t.Fatal(err)
	}
	out := map[string]string{}
	for _, file := range reader.File {
		handle, err := file.Open()
		if err != nil {
			t.Fatal(err)
		}
		data, err := io.ReadAll(handle)
		handle.Close()
		if err != nil {
			t.Fatal(err)
		}
		out[file.Name] = string(data)
	}
	return out
}

// TestDebugLoggingToggleRepublishesProfile is the contract behind the admin
// button: toggling must produce a new published release whose profile definition
// carries the rewritten configuration, with the package set untouched.
func TestDebugLoggingToggleRepublishesProfile(t *testing.T) {
	server := testServer(t)
	release := seedRelease(t, server, "Ashlands", "builders", "vr", "2.1.4")
	sourceArtifact, err := server.publishedProfileArtifact(context.Background(), release.ID)
	if err != nil {
		t.Fatal(err)
	}
	sourceBody, err := os.ReadFile(sourceArtifact.Path)
	if err != nil {
		t.Fatal(err)
	}
	sourceEntries := zipEntries(t, sourceBody)

	id, err2 := server.republishWithDebugLogging(context.Background(), "Ashlands", "builders", "vr", "admin", true)
	if err2 != nil {
		t.Fatalf("republish: %v", err2)
	}
	if id != "builders-2.1.5" {
		t.Fatalf("release id = %q, want builders-2.1.5", id)
	}
	published, err := server.store.CurrentRelease(context.Background(), "Ashlands", "builders", "vr")
	if err != nil {
		t.Fatal(err)
	}
	if published.ID != id || published.Version != "2.1.5" {
		t.Fatalf("current release = %+v", published)
	}
	if published.ID == release.ID {
		t.Fatal("republish reused the previous release")
	}

	artifact, err := server.publishedProfileArtifact(context.Background(), id)
	if err != nil {
		t.Fatal(err)
	}
	body, err := os.ReadFile(artifact.Path)
	if err != nil {
		t.Fatal(err)
	}
	entries := zipEntries(t, body)
	cfg := entries["config/BepInEx.cfg"]
	for _, want := range []string{"AppendLog = true", "LogLevels = All", "[Logging.Disk]"} {
		if !strings.Contains(cfg, want) {
			t.Fatalf("debug config missing %q: %q", want, cfg)
		}
	}
	// The package set must be carried over untouched: toggling diagnostics is a
	// configuration change, never a mod change.
	if entries["profile-manifest.json"] != sourceEntries["profile-manifest.json"] {
		t.Fatalf("manifest changed:\n old=%q\n new=%q", sourceEntries["profile-manifest.json"], entries["profile-manifest.json"])
	}
	if _, ok := byKindOf(t, server, id)["vr_runtime"]; !ok {
		t.Fatal("VR runtime artifact was not carried over")
	}
	if _, ok := byKindOf(t, server, id)["diag_plugin"]; ok {
		t.Fatal("diagnostics plugin was invented despite the source release not carrying one")
	}

	// The plugin artifact also delivers VR fixes, so it must survive a toggle in either
	// direction. Dropping it when debug logging was switched off silently uninstalled
	// those fixes on the client.
	seedReleaseWithPlugin(t, server, "Ashlands", "keepers", "vr", "5.0.0")
	offID, err := server.republishWithDebugLogging(context.Background(), "Ashlands", "keepers", "vr", "admin", false)
	if err != nil {
		t.Fatalf("republish with debug off: %v", err)
	}
	if _, ok := byKindOf(t, server, offID)["diag_plugin"]; !ok {
		t.Fatal("disabling debug logging dropped the plugin artifact, uninstalling the VR fixes")
	}
	if _, ok := entries[profilerConfigEntry]; !ok {
		t.Fatalf("profiler config was not added; entries=%v", keysOf(entries))
	}

	// Turning it back off must be deterministic rather than a no-op.
	off, err := server.republishWithDebugLogging(context.Background(), "Ashlands", "builders", "vr", "admin", false)
	if err != nil {
		t.Fatalf("republish off: %v", err)
	}
	if off != "builders-2.1.6" {
		t.Fatalf("second release id = %q", off)
	}
	quietArtifact, err := server.publishedProfileArtifact(context.Background(), off)
	if err != nil {
		t.Fatal(err)
	}
	quietBody, err := os.ReadFile(quietArtifact.Path)
	if err != nil {
		t.Fatal(err)
	}
	quiet := zipEntries(t, quietBody)["config/BepInEx.cfg"]
	if !strings.Contains(quiet, "AppendLog = false") || strings.Contains(quiet, "LogLevels = All") {
		t.Fatalf("disabling did not restore quiet logging: %q", quiet)
	}
}

func TestDebugLoggingToggleRejectsProfileWithoutRelease(t *testing.T) {
	server := testServer(t)
	if _, err := server.republishWithDebugLogging(context.Background(), "Ashlands", "builders", "vr", "admin", true); err == nil {
		t.Fatal("republished a profile that has no published release")
	}
}

// TestDebugLoggingHandlerPublishes drives the real HTTP route so the auth, CSRF,
// and redirect behaviour are exercised alongside the publish.
func TestDebugLoggingHandlerPublishes(t *testing.T) {
	server := testServer(t)
	seedRelease(t, server, "Ashlands", "builders", "vr", "3.0.1")
	form := url.Values{"enabled": {"true"}}
	adminPost(t, server, "/admin/worlds/Ashlands/profiles/builders/debug-logging", form, 303)
	published, err := server.store.CurrentRelease(context.Background(), "Ashlands", "builders", "vr")
	if err != nil {
		t.Fatal(err)
	}
	if published.Version != "3.0.2" {
		t.Fatalf("handler did not publish a bumped release: %+v", published)
	}
	enabled, err := server.store.ProfileDebugLogging(context.Background(), "Ashlands", "builders")
	if err != nil || !enabled {
		t.Fatalf("flag not persisted: %t %v", enabled, err)
	}
}

const profilerConfigEntry = "config/sighsorry.LoadTimeProfiler.cfg"

func keysOf(m map[string]string) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}

func byKindOf(t *testing.T, server *Server, releaseID string) map[string]Artifact {
	t.Helper()
	artifacts, err := server.store.PublishedArtifacts(context.Background(), releaseID)
	if err != nil {
		t.Fatal(err)
	}
	out := map[string]Artifact{}
	for _, a := range artifacts {
		out[a.Kind] = a
	}
	return out
}

func addDiagPluginArtifact(t *testing.T, store *Store, dir string, release Release) {
	t.Helper()
	content := testDiagnosticPluginZip(t)
	path := filepath.Join(dir, release.ID+"-diag.zip")
	if err := os.WriteFile(path, content, 0o600); err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(content)
	artifact := Artifact{
		ID: release.ID + "-diag", ReleaseID: release.ID, Kind: "diag_plugin",
		Name: release.ID + "-diag.zip", SHA256: hex.EncodeToString(sum[:]),
		Size: int64(len(content)), Path: path,
	}
	if err := store.AddArtifact(context.Background(), artifact, "admin"); err != nil {
		t.Fatal(err)
	}
}

func testDiagnosticPluginZip(t *testing.T) []byte {
	t.Helper()
	var buffer bytes.Buffer
	archive := zip.NewWriter(&buffer)
	entry, err := archive.Create("BepInEx/plugins/ValheimDiagnostics/ValheimDiagnostics.dll")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := entry.Write([]byte("diag")); err != nil {
		t.Fatal(err)
	}
	if err := archive.Close(); err != nil {
		t.Fatal(err)
	}
	return buffer.Bytes()
}

// seedReleaseWithPlugin publishes a release that already carries a diag_plugin
// artifact. Artifacts cannot be attached after publication, so the plugin has to be
// staged before Publish is called.
func seedReleaseWithPlugin(t *testing.T, server *Server, world, profile, clientType, version string) Release {
	t.Helper()
	release := Release{ID: profile + "-" + version, World: world, Profile: profile, ClientType: clientType, Version: version, Notes: "seed"}
	if err := server.store.CreateRelease(context.Background(), release, "admin"); err != nil {
		t.Fatal(err)
	}
	addProfileArtifact(t, server.store, server.cfg.ArtifactRoot, release)
	if clientType == "vr" {
		addVRRuntimeArtifact(t, server.store, server.cfg.ArtifactRoot, release)
	}
	addDiagPluginArtifact(t, server.store, server.cfg.ArtifactRoot, release)
	if err := server.store.Publish(context.Background(), release.ID, "admin"); err != nil {
		t.Fatal(err)
	}
	return release
}
