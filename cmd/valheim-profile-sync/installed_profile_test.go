package main

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestDescribeInstalledProfile(t *testing.T) {
	cases := []struct {
		name  string
		state profileState
		found bool
		want  string
	}{
		{
			name:  "fully populated",
			state: profileState{Schema: 1, World: "Asgard", Profile: "asgard-vr", ClientType: clientVR, ReleaseID: "2.1.73"},
			found: true,
			want:  "Installed: Asgard · asgard-vr · vr · 2.1.73",
		},
		{
			name:  "missing state",
			state: profileState{},
			found: false,
			want:  noInstalledProfileText,
		},
		{
			name:  "state present but release id empty",
			state: profileState{Schema: 1, World: "Asgard", Profile: "asgard-vr", ClientType: clientVR},
			found: true,
			want:  noInstalledProfileText,
		},
		{
			name:  "release id is only whitespace",
			state: profileState{Schema: 1, World: "Asgard", ReleaseID: "   "},
			found: true,
			want:  noInstalledProfileText,
		},
		{
			name:  "release id without world or client type",
			state: profileState{Schema: 1, Profile: "flat", ReleaseID: "release-two"},
			found: true,
			want:  "Installed: flat · release-two",
		},
		{
			name:  "release id only",
			state: profileState{Schema: 1, ReleaseID: "release-two"},
			found: true,
			want:  "Installed: release-two",
		},
		{
			name:  "surrounding whitespace is trimmed",
			state: profileState{Schema: 1, World: " Midgard ", Profile: "vr", ClientType: " vr ", ReleaseID: " r9 "},
			found: true,
			want:  "Installed: Midgard · vr · vr · r9",
		},
		{
			name:  "found is false even with a populated state",
			state: profileState{Schema: 1, World: "Midgard", ReleaseID: "r9"},
			found: false,
			want:  noInstalledProfileText,
		},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			if got := describeInstalledProfile(testCase.state, testCase.found); got != testCase.want {
				t.Fatalf("describeInstalledProfile = %q, want %q", got, testCase.want)
			}
		})
	}
}

func TestInstalledProfileSummaryDegradesWithoutStorage(t *testing.T) {
	if got := installedProfileSummary(t.TempDir()); got != noInstalledProfileText {
		t.Fatalf("summary without a profile store = %q, want %q", got, noInstalledProfileText)
	}
	if got := installedProfileSummary(""); got != noInstalledProfileText {
		t.Fatalf("summary without local app data = %q, want %q", got, noInstalledProfileText)
	}
}

func TestInstalledProfileSummaryReportsNewestProfile(t *testing.T) {
	localAppData := t.TempDir()
	storage, err := saveProfileStorageDirectory(localAppData, filepath.Join(t.TempDir(), "store"))
	if err != nil {
		t.Fatal(err)
	}
	older := writeInstalledProfileFixture(t, storage, "midgard--vr--vr", profileState{Schema: 1, World: "Midgard", Profile: "vr", ClientType: clientVR, ReleaseID: "1.0.0"})
	newer := writeInstalledProfileFixture(t, storage, "asgard--asgard-vr--vr", profileState{Schema: 1, World: "Asgard", Profile: "asgard-vr", ClientType: clientVR, ReleaseID: "2.1.73"})
	stamp := time.Now()
	if err := os.Chtimes(older, stamp.Add(-2*time.Hour), stamp.Add(-2*time.Hour)); err != nil {
		t.Fatal(err)
	}
	if err := os.Chtimes(newer, stamp, stamp); err != nil {
		t.Fatal(err)
	}
	want := "Installed: Asgard · asgard-vr · vr · 2.1.73"
	if got := installedProfileSummary(localAppData); got != want {
		t.Fatalf("summary = %q, want %q", got, want)
	}
}

func TestInstalledProfileSummaryIgnoresCorruptState(t *testing.T) {
	localAppData := t.TempDir()
	storage, err := saveProfileStorageDirectory(localAppData, filepath.Join(t.TempDir(), "store"))
	if err != nil {
		t.Fatal(err)
	}
	root := filepath.Join(storage, "profiles", "midgard--vr--vr")
	if err := os.MkdirAll(root, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, stateFilename), []byte("{not json"), 0o600); err != nil {
		t.Fatal(err)
	}
	if got := installedProfileSummary(localAppData); got != noInstalledProfileText {
		t.Fatalf("summary with corrupt state = %q, want %q", got, noInstalledProfileText)
	}
}

func writeInstalledProfileFixture(t *testing.T, storage, name string, state profileState) string {
	t.Helper()
	root := filepath.Join(storage, "profiles", name)
	if err := os.MkdirAll(root, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := writeProfileState(root, state); err != nil {
		t.Fatal(err)
	}
	return filepath.Join(root, stateFilename)
}
