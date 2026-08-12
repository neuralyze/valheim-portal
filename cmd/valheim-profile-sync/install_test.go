package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The executable is registered where it is, and never duplicated.
//
// Copying it into %LOCALAPPDATA% is what Defender's heuristic matched, and the copy it deleted was
// the one the protocol pointed at - so the shortcut broke while looking intact.
func TestInstallApplicationRegistersWithoutCopying(t *testing.T) {
	directory := t.TempDir()
	source := filepath.Join(directory, "ValheimProfileSync.exe")
	if err := os.WriteFile(source, []byte("binary"), 0o700); err != nil {
		t.Fatal(err)
	}
	storage := filepath.Join(t.TempDir(), "local-app-data", "ValheimProfileSync")
	if err := os.MkdirAll(storage, 0o700); err != nil {
		t.Fatal(err)
	}

	var commands [][]string
	run := func(name string, arguments ...string) error {
		commands = append(commands, append([]string{name}, arguments...))
		return nil
	}

	registered, err := installApplication(source, storage, run)
	if err != nil {
		t.Fatal(err)
	}
	if registered != source {
		t.Fatalf("registered %q, want the running executable %q", registered, source)
	}

	entries, err := os.ReadDir(storage)
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 0 {
		t.Fatalf("storage directory gained %d entries; nothing may be copied into it", len(entries))
	}

	var registeredCommand string
	for _, command := range commands {
		for _, argument := range command {
			if strings.Contains(argument, source) {
				registeredCommand = argument
			}
		}
	}
	if registeredCommand == "" {
		t.Fatalf("protocol was not registered against %q: %#v", source, commands)
	}
}

// Running it again must repair the registration rather than fail, which is how a restored or moved
// executable fixes its own shortcut.
func TestInstallApplicationIsRepeatable(t *testing.T) {
	directory := t.TempDir()
	source := filepath.Join(directory, "ValheimProfileSync.exe")
	if err := os.WriteFile(source, []byte("binary"), 0o700); err != nil {
		t.Fatal(err)
	}
	run := func(string, ...string) error { return nil }

	for attempt := 0; attempt < 2; attempt++ {
		if _, err := installApplication(source, "", run); err != nil {
			t.Fatalf("attempt %d: %v", attempt, err)
		}
	}
}

func TestInstallApplicationRejectsMissingExecutable(t *testing.T) {
	run := func(string, ...string) error { return nil }
	if _, err := installApplication(filepath.Join(t.TempDir(), "absent.exe"), "", run); err == nil {
		t.Fatal("accepted an executable that does not exist")
	}
}
