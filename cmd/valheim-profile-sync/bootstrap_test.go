package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestInstallBootstrapOwnsOnlyKnownLoaderFiles(t *testing.T) {
	game := t.TempDir()
	active := filepath.Join(t.TempDir(), "active")
	if err := os.MkdirAll(active, 0o700); err != nil {
		t.Fatal(err)
	}
	for name, body := range map[string]string{"winhttp.dll": "loader-one", "doorstop_config.ini": "config-one"} {
		if err := os.WriteFile(filepath.Join(active, name), []byte(body), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	if err := installBootstrap(game, active); err != nil {
		t.Fatal(err)
	}
	for name, want := range map[string]string{"winhttp.dll": "loader-one", "doorstop_config.ini": "config-one"} {
		got, err := os.ReadFile(filepath.Join(game, name))
		if err != nil || string(got) != want {
			t.Fatalf("%s = %q, %v", name, got, err)
		}
	}
	if err := os.WriteFile(filepath.Join(game, "unmanaged.dll"), []byte("keep"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := installBootstrap(game, active); err != nil {
		t.Fatal(err)
	}
	if got, err := os.ReadFile(filepath.Join(game, "unmanaged.dll")); err != nil || string(got) != "keep" {
		t.Fatalf("unmanaged file changed: %q, %v", got, err)
	}
}

func TestInstallBootstrapRefusesUnmanagedLoader(t *testing.T) {
	game := t.TempDir()
	active := filepath.Join(t.TempDir(), "active")
	if err := os.MkdirAll(active, 0o700); err != nil {
		t.Fatal(err)
	}
	for name := range map[string]bool{"winhttp.dll": true, "doorstop_config.ini": true} {
		if err := os.WriteFile(filepath.Join(active, name), []byte("owned"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.WriteFile(filepath.Join(game, "winhttp.dll"), []byte("other-manager"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := installBootstrap(game, active); err == nil {
		t.Fatal("accepted unmanaged loader")
	}
}
