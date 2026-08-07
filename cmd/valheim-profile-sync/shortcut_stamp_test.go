package main

import (
	"os"
	"path/filepath"
	"testing"
)

// The shortcut is created once and then left alone. It used to be rewritten on
// every "Install or update", which resurrected shortcuts players had deleted.
func TestShortcutStampReportsCreationOnce(t *testing.T) {
	root := filepath.Join(t.TempDir(), "profile")

	created, err := shortcutAlreadyCreated(root)
	if err != nil {
		t.Fatalf("checking an absent profile directory: %v", err)
	}
	if created {
		t.Fatal("a profile that has never synced must report no shortcut")
	}

	if err := recordShortcutCreated(root); err != nil {
		t.Fatalf("recording: %v", err)
	}

	created, err = shortcutAlreadyCreated(root)
	if err != nil {
		t.Fatalf("checking after recording: %v", err)
	}
	if !created {
		t.Fatal("after recording, the shortcut must not be written again")
	}

	// Recording twice is what a repeated sync does, and it must not fail.
	if err := recordShortcutCreated(root); err != nil {
		t.Fatalf("recording twice: %v", err)
	}
}

// Deleting the stamp is the documented way to get the shortcut back, and is the
// only route that recreates it.
func TestShortcutStampRemovalAllowsRecreation(t *testing.T) {
	root := filepath.Join(t.TempDir(), "profile")
	if err := recordShortcutCreated(root); err != nil {
		t.Fatalf("recording: %v", err)
	}
	if err := os.Remove(filepath.Join(root, shortcutStampName)); err != nil {
		t.Fatalf("removing the stamp: %v", err)
	}
	created, err := shortcutAlreadyCreated(root)
	if err != nil {
		t.Fatalf("checking after removal: %v", err)
	}
	if created {
		t.Fatal("with the stamp gone the shortcut must be written again")
	}
}

func TestShortcutStampRejectsEmptyRoot(t *testing.T) {
	if _, err := shortcutAlreadyCreated(""); err == nil {
		t.Fatal("an empty profile directory must be rejected rather than treated as unwritten")
	}
}
