package main

import (
	"os"
	"path/filepath"
	"testing"
)

// These defend the rule that a release owns the configs it ships and nothing else.
// Before this, every generation was built from scratch, so a mod-authored config was
// deleted and regenerated from defaults on the next launch - which is how EpicLoot's
// welcome screen came back after every sync.
func TestPreserveUnmanagedConfigKeepsUnshippedFiles(t *testing.T) {
	previous := t.TempDir()
	next := t.TempDir()
	if err := os.WriteFile(filepath.Join(previous, "randyknapp.mods.epicloot.cfg"), []byte("welcome = false\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(next, "BepInEx.cfg"), []byte("shipped\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := preserveUnmanagedConfig(previous, next); err != nil {
		t.Fatalf("preserve: %v", err)
	}
	data, err := os.ReadFile(filepath.Join(next, "randyknapp.mods.epicloot.cfg"))
	if err != nil {
		t.Fatalf("unshipped config was not preserved: %v", err)
	}
	if string(data) != "welcome = false\n" {
		t.Fatalf("preserved content wrong: %q", data)
	}
}

func TestPreserveUnmanagedConfigNeverOverwritesShipped(t *testing.T) {
	previous := t.TempDir()
	next := t.TempDir()
	// A stale local copy must lose to the release's own copy.
	if err := os.WriteFile(filepath.Join(previous, "BepInEx.cfg"), []byte("stale local\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(next, "BepInEx.cfg"), []byte("shipped\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := preserveUnmanagedConfig(previous, next); err != nil {
		t.Fatalf("preserve: %v", err)
	}
	data, err := os.ReadFile(filepath.Join(next, "BepInEx.cfg"))
	if err != nil {
		t.Fatal(err)
	}
	if string(data) != "shipped\n" {
		t.Fatalf("shipped config was clobbered by local copy: %q", data)
	}
}

func TestPreserveUnmanagedConfigCarriesSubdirectories(t *testing.T) {
	previous := t.TempDir()
	next := t.TempDir()
	nested := filepath.Join(previous, "LoadTimeProfiler")
	if err := os.MkdirAll(nested, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(nested, "latest.log"), []byte("x\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := preserveUnmanagedConfig(previous, next); err != nil {
		t.Fatalf("preserve: %v", err)
	}
	if _, err := os.Stat(filepath.Join(next, "LoadTimeProfiler", "latest.log")); err != nil {
		t.Fatalf("nested config directory not preserved: %v", err)
	}
}

func TestPreserveUnmanagedConfigToleratesMissingSource(t *testing.T) {
	next := t.TempDir()
	if err := preserveUnmanagedConfig(filepath.Join(t.TempDir(), "absent"), next); err != nil {
		t.Fatalf("a first install has no previous config and must not error: %v", err)
	}
}

func TestPreserveUnmanagedConfigSkipsSymlinks(t *testing.T) {
	previous := t.TempDir()
	next := t.TempDir()
	outside := filepath.Join(t.TempDir(), "secret.cfg")
	if err := os.WriteFile(outside, []byte("secret\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(outside, filepath.Join(previous, "link.cfg")); err != nil {
		t.Skipf("symlinks unavailable: %v", err)
	}
	if err := preserveUnmanagedConfig(previous, next); err != nil {
		t.Fatalf("preserve: %v", err)
	}
	if _, err := os.Lstat(filepath.Join(next, "link.cfg")); err == nil {
		t.Fatal("a symlink was carried into the profile tree")
	}
}
