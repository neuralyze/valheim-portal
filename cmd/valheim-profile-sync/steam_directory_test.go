package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestSteamValheimDirectoryPersistsOnlyValidGameFolders(t *testing.T) {
	localAppData := t.TempDir()
	gameDir := filepath.Join(t.TempDir(), "Valheim")
	if err := os.MkdirAll(gameDir, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(gameDir, "valheim.exe"), []byte("steam"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := saveSteamValheimDirectory(localAppData, gameDir); err != nil {
		t.Fatal(err)
	}
	loaded, present, err := loadSteamValheimDirectory(localAppData)
	if err != nil || !present || loaded != gameDir {
		t.Fatalf("saved directory = %q, present:%t, err:%v", loaded, present, err)
	}
	if err := os.Remove(filepath.Join(gameDir, "valheim.exe")); err != nil {
		t.Fatal(err)
	}
	if _, present, err := loadSteamValheimDirectory(localAppData); err != nil || present {
		t.Fatalf("stale directory should be ignored: present:%t err:%v", present, err)
	}
}

func TestSteamValheimDirectoryRejectsFolderWithoutGame(t *testing.T) {
	if err := saveSteamValheimDirectory(t.TempDir(), t.TempDir()); err == nil {
		t.Fatal("expected folder without valheim.exe to be rejected")
	}
}

func TestFindSteamValheimDirectoryChecksOnlyCandidates(t *testing.T) {
	missing := filepath.Join(t.TempDir(), "missing")
	gameDir := filepath.Join(t.TempDir(), "Valheim")
	if err := os.MkdirAll(gameDir, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(gameDir, "valheim.exe"), []byte("steam"), 0o600); err != nil {
		t.Fatal(err)
	}
	found, present := findSteamValheimDirectory([]string{"", missing, gameDir, gameDir})
	if !present || found != gameDir {
		t.Fatalf("discovered directory = %q, present:%t", found, present)
	}
}
