package app

import (
	"archive/zip"
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// A runtime carrying a diagnostic plugin, which is what the portal actually shipped before
// the mod was stripped out of the archive by hand.
func testVRRuntimeWithSpawnProbe(t *testing.T) string {
	t.Helper()
	base := testVRRuntimeArtifact(t)
	reader, err := zip.NewReader(bytes.NewReader(base), int64(len(base)))
	if err != nil {
		t.Fatal(err)
	}
	var out bytes.Buffer
	writer := zip.NewWriter(&out)
	for _, file := range reader.File {
		entry, err := writer.Create(file.Name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := entry.Write([]byte(file.Name)); err != nil {
			t.Fatal(err)
		}
	}
	probe, err := writer.Create("BepInEx/plugins/SpawnProbe/SpawnProbe.dll")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := probe.Write([]byte("probe")); err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "runtime-with-probe.zip")
	if err := os.WriteFile(path, out.Bytes(), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func archiveNames(t *testing.T, data []byte) []string {
	t.Helper()
	reader, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
	if err != nil {
		t.Fatal(err)
	}
	names := make([]string, 0, len(reader.File))
	for _, file := range reader.File {
		names = append(names, file.Name)
	}
	return names
}

func TestCopyVRRuntimeExcludingDropsThePluginAndStaysValid(t *testing.T) {
	input := testVRRuntimeWithSpawnProbe(t)
	if err := ValidateVRRuntimeArtifact(input); err != nil {
		t.Fatalf("input should be a valid runtime: %v", err)
	}
	var out bytes.Buffer
	dropped, err := CopyVRRuntimeExcluding(&out, input, []string{"SpawnProbe"})
	if err != nil {
		t.Fatal(err)
	}
	if dropped != 1 {
		t.Fatalf("dropped = %d, want 1", dropped)
	}
	for _, name := range archiveNames(t, out.Bytes()) {
		if strings.Contains(strings.ToLower(name), "spawnprobe") {
			t.Fatalf("output still carries %q", name)
		}
	}
	path := filepath.Join(t.TempDir(), "stripped.zip")
	if err := os.WriteFile(path, out.Bytes(), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := ValidateVRRuntimeArtifact(path); err != nil {
		t.Fatalf("stripped runtime must still validate: %v", err)
	}
}

func TestCopyVRRuntimeExcludingRejectsAnExclusionThatMatchesNothing(t *testing.T) {
	input := testVRRuntimeWithSpawnProbe(t)
	var out bytes.Buffer
	if _, err := CopyVRRuntimeExcluding(&out, input, []string{"SpwanProbe"}); err == nil {
		t.Fatal("a typo must fail rather than silently ship the mod")
	} else if !strings.Contains(err.Error(), "matched no entry") {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestCopyVRRuntimeExcludingRejectsPathsAndRequiredFiles(t *testing.T) {
	input := testVRRuntimeWithSpawnProbe(t)
	var out bytes.Buffer
	if _, err := CopyVRRuntimeExcluding(&out, input, []string{"plugins/SpawnProbe"}); err == nil {
		t.Fatal("an exclusion with a path separator must be refused")
	}
	out.Reset()
	if _, err := CopyVRRuntimeExcluding(&out, input, []string{"ValheimVRMod.dll"}); err == nil {
		t.Fatal("excluding the VR mod itself must fail")
	}
}
