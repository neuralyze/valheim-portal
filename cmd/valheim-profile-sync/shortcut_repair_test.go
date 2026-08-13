package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

// A machine with several installed profiles must offer a shortcut for each: the first version of
// this read the storage root instead of the profiles directory inside it, found nothing, and told a
// player with seven profiles that none were installed.
func TestInstalledProfileRequestsReadsEveryProfile(t *testing.T) {
	storage := t.TempDir()
	profiles := filepath.Join(storage, "profiles")
	for _, p := range []struct{ dir, world, profile, client, portal, diagnostics string }{
		{"Hrafnheim--hrafnheim-vr--vr", "Hrafnheim", "hrafnheim-vr", "vr", "https://valheim.example/", ""},
		// Installed before the portal was recorded: only the diagnostics endpoint carries it.
		{"Hrafnheim--hrafnheim-flatvr--flat", "Hrafnheim", "hrafnheim-flatvr", "flat", "", "https://valheim.example/client/diagnostics/Hrafnheim/hrafnheim-flatvr/flat"},
		{"Vangard--vangard-vr--vr", "Vangard", "vangard-vr", "vr", "https://valheim.example/", ""},
	} {
		root := filepath.Join(profiles, p.dir)
		if err := os.MkdirAll(root, 0o700); err != nil {
			t.Fatal(err)
		}
		body, err := json.Marshal(profileState{World: p.world, Profile: p.profile, ClientType: p.client,
			Portal: p.portal, DiagnosticsEndpoint: p.diagnostics})
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(root, stateFilename), body, 0o600); err != nil {
			t.Fatal(err)
		}
	}
	// A directory without a state file is not an installed profile and must be skipped rather than
	// failing the whole call.
	if err := os.MkdirAll(filepath.Join(profiles, "half-written"), 0o700); err != nil {
		t.Fatal(err)
	}

	t.Setenv("LOCALAPPDATA", t.TempDir())
	t.Setenv("XDG_DATA_HOME", t.TempDir())
	localAppData, err := localApplicationData()
	if err != nil {
		t.Fatal(err)
	}
	if _, err := saveProfileStorageDirectory(localAppData, storage); err != nil {
		t.Fatal(err)
	}

	got, err := installedProfileRequests()
	if err != nil {
		t.Fatalf("installedProfileRequests: %v", err)
	}
	if len(got) != 3 {
		t.Fatalf("got %d profiles, want 3: %+v", len(got), got)
	}
	if got[0].Profile != "hrafnheim-flatvr" || got[2].Profile != "vangard-vr" {
		t.Fatalf("not sorted by profile: %+v", got)
	}
}
