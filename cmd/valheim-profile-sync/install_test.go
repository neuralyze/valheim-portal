package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestInstallApplicationCopiesAndRegistersInstalledExecutable(t *testing.T) {
	source := filepath.Join(t.TempDir(), "downloaded.exe")
	if err := os.WriteFile(source, []byte("first-build"), 0o600); err != nil {
		t.Fatal(err)
	}
	var commands [][]string
	run := func(name string, arguments ...string) error {
		commands = append(commands, append([]string{name}, arguments...))
		return nil
	}
	installed, err := installApplication(source, filepath.Join(t.TempDir(), "local-app-data", "ValheimProfileSync"), run)
	if err != nil {
		t.Fatal(err)
	}
	if filepath.Base(installed) != installedExecutableName {
		t.Fatalf("installed executable = %q", installed)
	}
	data, err := os.ReadFile(installed)
	if err != nil || string(data) != "first-build" {
		t.Fatalf("installed contents = %q, %v", data, err)
	}
	if len(commands) != 3 || commands[0][0] != "reg.exe" || !strings.Contains(strings.Join(commands[2], " "), installed) {
		t.Fatalf("registry commands = %#v", commands)
	}
}

func TestInstallApplicationReplacesChangedDownload(t *testing.T) {
	source := filepath.Join(t.TempDir(), "downloaded.exe")
	root := filepath.Join(t.TempDir(), "local-app-data", "ValheimProfileSync")
	run := func(string, ...string) error { return nil }
	if err := os.WriteFile(source, []byte("first-build"), 0o600); err != nil {
		t.Fatal(err)
	}
	installed, err := installApplication(source, root, run)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(source, []byte("second-build"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := installApplication(source, root, run); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(installed)
	if err != nil || string(data) != "second-build" {
		t.Fatalf("replaced contents = %q, %v", data, err)
	}
}

// TestShortcutIconPathPrefersInstalledCopy defends the Desktop icon against the
// downloaded executable being replaced. Creating a shortcut while running straight
// from a browser download records that download as the icon source, so the art
// disappears once the file is cleaned up or superseded - which reads to the user as
// the shortcut having been deleted.
func TestShortcutIconPathPrefersInstalledCopy(t *testing.T) {
	root := t.TempDir()
	localAppData := filepath.Join(root, "AppData", "Local")
	t.Setenv("LOCALAPPDATA", localAppData)
	t.Setenv("HOME", root)

	storage, _, err := loadProfileStorageDirectory(localAppData)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(storage, 0o700); err != nil {
		t.Fatal(err)
	}
	download := filepath.Join(root, "Downloads", "ValheimProfileSync (1).exe")
	if err := os.MkdirAll(filepath.Dir(download), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(download, []byte("downloaded"), 0o600); err != nil {
		t.Fatal(err)
	}

	if got := shortcutIconPath(download); got != download {
		t.Fatalf("without an installed copy the running executable must be used: got %q, want %q", got, download)
	}

	installed := filepath.Join(storage, installedExecutableName)
	if err := os.WriteFile(installed, []byte("installed"), 0o600); err != nil {
		t.Fatal(err)
	}
	if got := shortcutIconPath(download); got != installed {
		t.Fatalf("icon path = %q, want the stable installed copy %q", got, installed)
	}

	if err := os.Remove(installed); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(installed, 0o700); err != nil {
		t.Fatal(err)
	}
	if got := shortcutIconPath(download); got != download {
		t.Fatalf("a directory must not be mistaken for the executable: got %q", got)
	}
}
