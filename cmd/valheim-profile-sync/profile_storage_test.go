package main

import (
	"net/url"
	"path/filepath"
	"testing"
)

func TestProfileStorageDirectoryPersistsAndChangesProfileRoot(t *testing.T) {
	localAppData := t.TempDir()
	storage := filepath.Join(t.TempDir(), "Valheim mods")
	stored, err := saveProfileStorageDirectory(localAppData, storage)
	if err != nil {
		t.Fatal(err)
	}
	loaded, present, err := loadProfileStorageDirectory(localAppData)
	if err != nil || !present || loaded != stored {
		t.Fatalf("storage = %q, present:%t, err:%v", loaded, present, err)
	}
	portal, err := url.Parse("https://portal.example")
	if err != nil {
		t.Fatal(err)
	}
	request := profileRequest{Portal: portal, World: "world", Profile: "vr", ClientType: clientVR}
	root, err := profileRoot(localAppData, request)
	if err != nil {
		t.Fatal(err)
	}
	want := filepath.Join(stored, "profiles", "world--vr--vr")
	if root != want {
		t.Fatalf("profile root = %q, want %q", root, want)
	}
}
