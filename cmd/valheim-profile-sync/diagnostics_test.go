package main

import (
	"archive/zip"
	"os"
	"path/filepath"
	"testing"
)

func TestBuildDiagnosticsBundleIncludesBoundedLogsAndProfilerReport(t *testing.T) {
	gameDir := t.TempDir()
	profileRoot := t.TempDir()
	if err := os.MkdirAll(filepath.Join(gameDir, "BepInEx", "config", "LoadTimeProfiler"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(gameDir, "Player.log"), []byte("player log"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(gameDir, "BepInEx", "LogOutput.log"), []byte("bepinex log"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(gameDir, "BepInEx", "config", "LoadTimeProfiler", "latest.txt"), []byte("profiler report"), 0o600); err != nil {
		t.Fatal(err)
	}
	bundle, err := buildDiagnosticsBundle(gameDir, profileRoot, profileState{World: "Midgard", Profile: "vr", ClientType: clientVR, ReleaseID: "release"})
	if err != nil {
		t.Fatal(err)
	}
	defer os.Remove(bundle)
	for _, name := range []string{"metadata.json", "Player.log", "BepInEx/LogOutput.log", "LoadTimeProfiler/latest.log"} {
		if !bundleContains(t, bundle, name) {
			t.Fatalf("bundle omitted %s", name)
		}
	}
}

// The launcher points Doorstop at <profileRoot>/active/BepInEx and never passes
// -logfile, so in production BepInEx logs live under the profile generation and
// the profiler writes .log reports. Collecting only from the game directory
// silently produced bundles containing nothing but metadata.json.
func TestBuildDiagnosticsBundlePrefersProfileGenerationLogs(t *testing.T) {
	gameDir := t.TempDir()
	profileRoot := t.TempDir()
	active := filepath.Join(profileRoot, "active")
	if err := os.MkdirAll(filepath.Join(active, "BepInEx", "config", "LoadTimeProfiler"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(active, "BepInEx", "LogOutput.log"), []byte("profile bepinex log"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(active, "BepInEx", "config", "LoadTimeProfiler", "2026-07-28_21-02-10.log"), []byte("profiler report"), 0o600); err != nil {
		t.Fatal(err)
	}
	bundle, err := buildDiagnosticsBundle(gameDir, profileRoot, profileState{World: "Asgard", Profile: "asgard-vr", ClientType: clientVR, ReleaseID: "release"})
	if err != nil {
		t.Fatal(err)
	}
	defer os.Remove(bundle)
	for _, name := range []string{"BepInEx/LogOutput.log", "LoadTimeProfiler/latest.log"} {
		if !bundleContains(t, bundle, name) {
			t.Fatalf("bundle omitted %s from the profile generation", name)
		}
	}
}

func bundleContains(t *testing.T, bundle, name string) bool {
	t.Helper()
	archive, err := zip.OpenReader(bundle)
	if err != nil {
		t.Fatal(err)
	}
	defer archive.Close()
	for _, file := range archive.File {
		if file.Name == name {
			return true
		}
	}
	return false
}

// TestBuildDiagnosticsBundleIncludesEffectiveConfigs defends the ability to see
// what configuration was actually in force on the client. Shipping a profile
// setting is not the same as that setting reaching the game: mods rewrite these
// files at runtime and ServerSync overwrites synced entries at connect.
func TestBuildDiagnosticsBundleIncludesEffectiveConfigs(t *testing.T) {
	gameDir := t.TempDir()
	profileRoot := t.TempDir()
	configDir := filepath.Join(profileRoot, "active", "BepInEx", "config")
	if err := os.MkdirAll(configDir, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(configDir, "Azumatt.AzuExtendedPlayerInventory.cfg"), []byte("[7]\nHUD Position = {\"x\":1.0,\"y\":1.0}\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(configDir, "BepInEx.cfg"), []byte("[Logging.Disk]\nAppendLog = true\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	// Not a .cfg: must be ignored rather than swept in.
	if err := os.WriteFile(filepath.Join(configDir, "notes.txt"), []byte("ignore"), 0o600); err != nil {
		t.Fatal(err)
	}
	bundle, err := buildDiagnosticsBundle(gameDir, profileRoot, profileState{World: "Midgard", Profile: "vr", ClientType: clientVR, ReleaseID: "release"})
	if err != nil {
		t.Fatal(err)
	}
	defer os.Remove(bundle)
	archive, err := zip.OpenReader(bundle)
	if err != nil {
		t.Fatal(err)
	}
	defer archive.Close()
	found := map[string]bool{}
	for _, file := range archive.File {
		found[file.Name] = true
	}
	for _, want := range []string{"config/Azumatt.AzuExtendedPlayerInventory.cfg", "config/BepInEx.cfg"} {
		if !found[want] {
			t.Fatalf("bundle omitted %s; entries=%v", want, found)
		}
	}
	if found["config/notes.txt"] {
		t.Fatal("bundle swept in a non-config file")
	}
}
