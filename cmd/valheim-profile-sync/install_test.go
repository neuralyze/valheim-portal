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
func TestInstallApplicationInstallsAndRegisters(t *testing.T) {
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

	installed, err := installApplication(source, storage, run)
	if err != nil {
		t.Fatal(err)
	}

	// One click means the player never places the file themselves: it lands at a fixed path and
	// the shortcut and protocol both point there.
	want := filepath.Join(storage, installedExecutableName)
	if installed != want {
		t.Fatalf("installed to %q, want %q", installed, want)
	}
	if _, err := os.Stat(want); err != nil {
		t.Fatalf("nothing was installed: %v", err)
	}

	var registered bool
	for _, command := range commands {
		for _, argument := range command {
			if strings.Contains(argument, want) {
				registered = true
			}
		}
	}
	if !registered {
		t.Fatalf("protocol was not registered against %q: %#v", want, commands)
	}
}

// Running the installed copy again must not fail or duplicate anything.
func TestInstallApplicationFromTheInstalledCopyIsANoOp(t *testing.T) {
	storage := t.TempDir()
	installed := filepath.Join(storage, installedExecutableName)
	if err := os.WriteFile(installed, []byte("binary"), 0o700); err != nil {
		t.Fatal(err)
	}
	run := func(string, ...string) error { return nil }

	got, err := installApplication(installed, storage, run)
	if err != nil {
		t.Fatal(err)
	}
	if got != installed {
		t.Fatalf("got %q, want %q", got, installed)
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
		if _, err := installApplication(source, t.TempDir(), run); err != nil {
			t.Fatalf("attempt %d: %v", attempt, err)
		}
	}
}

func TestInstallApplicationRejectsMissingExecutable(t *testing.T) {
	run := func(string, ...string) error { return nil }
	if _, err := installApplication(filepath.Join(t.TempDir(), "absent.exe"), t.TempDir(), run); err == nil {
		t.Fatal("accepted an executable that does not exist")
	}
}
