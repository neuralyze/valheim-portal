package app

import (
	"archive/zip"
	"os"
	"path/filepath"
	"testing"
)

func writeDiagZip(t *testing.T, entries map[string]string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "diag.zip")
	file, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	archive := zip.NewWriter(file)
	for name, body := range entries {
		w, err := archive.Create(name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := w.Write([]byte(body)); err != nil {
			t.Fatal(err)
		}
	}
	if err := archive.Close(); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestDiagnosticPluginArtifactAcceptsCanonicalLayout(t *testing.T) {
	path := writeDiagZip(t, map[string]string{
		"BepInEx/plugins/ValheimDiagnostics/ValheimDiagnostics.dll": "dll",
		"BepInEx/plugins/ValheimDiagnostics/config.cfg":             "cfg",
	})
	if err := ValidateDiagnosticPluginArtifact(path); err != nil {
		t.Fatalf("rejected canonical layout: %v", err)
	}
}

func TestDiagnosticPluginArtifactRejectsUnsafePayloads(t *testing.T) {
	cases := map[string]map[string]string{
		"missing assembly":    {"BepInEx/plugins/ValheimDiagnostics/notes.cfg": "x"},
		"steam runtime file":  {"BepInEx/plugins/ValheimDiagnostics/ValheimDiagnostics.dll": "d", "Valheim_Data/Managed/evil.dll": "e"},
		"nested subdirectory": {"BepInEx/plugins/ValheimDiagnostics/ValheimDiagnostics.dll": "d", "BepInEx/plugins/ValheimDiagnostics/sub/x.dll": "x"},
		"traversal":           {"BepInEx/plugins/ValheimDiagnostics/ValheimDiagnostics.dll": "d", "../escape.dll": "e"},
		"unsupported ext":     {"BepInEx/plugins/ValheimDiagnostics/ValheimDiagnostics.dll": "d", "BepInEx/plugins/ValheimDiagnostics/run.exe": "x"},
	}
	for name, entries := range cases {
		if err := ValidateDiagnosticPluginArtifact(writeDiagZip(t, entries)); err == nil {
			t.Fatalf("%s: expected rejection", name)
		}
	}
}

// TestDiagnosticPluginArtifactAcceptsUnknownPluginDirectory is the regression guard
// for a real incident: a second plugin directory was added to the portal's allowlist,
// and every already-installed client rejected the next release with "contains an
// unsupported directory", because the same validator runs inside the client
// executable. Install-time validation must therefore accept any correctly shaped
// plugin directory, including ones that did not exist when the client was built.
func TestDiagnosticPluginArtifactAcceptsUnknownPluginDirectory(t *testing.T) {
	future := writeDiagZip(t, map[string]string{
		"BepInEx/plugins/ValheimDiagnostics/ValheimDiagnostics.dll": "diag",
		"BepInEx/plugins/SomePluginAddedLater/Thing.dll":            "future",
		"BepInEx/plugins/SomePluginAddedLater/thing.cfg":            "cfg",
	})
	if err := ValidateDiagnosticPluginArtifact(future); err != nil {
		t.Fatalf("install-time validation rejected a future plugin directory: %v", err)
	}
	// Publish-time policy is stricter and still refuses it.
	if err := validatePortalOwnedPluginRoots(future); err == nil {
		t.Fatal("publish-time policy accepted a directory the portal does not own")
	}
	owned := writeDiagZip(t, map[string]string{
		"BepInEx/plugins/ValheimDiagnostics/ValheimDiagnostics.dll": "diag",
		"BepInEx/plugins/NeuralyzeVRFixes/NeuralyzeVRFixes.dll":     "fixes",
	})
	if err := validatePortalOwnedPluginRoots(owned); err != nil {
		t.Fatalf("publish-time policy rejected the portal's own directories: %v", err)
	}
}

// Structural invariants must hold regardless of directory name.
func TestDiagnosticPluginArtifactStillRejectsUnsafeShapes(t *testing.T) {
	cases := map[string]map[string]string{
		"nested subdirectory": {"BepInEx/plugins/ValheimDiagnostics/ValheimDiagnostics.dll": "d", "BepInEx/plugins/Other/sub/x.dll": "x"},
		"directly in plugins": {"BepInEx/plugins/ValheimDiagnostics/ValheimDiagnostics.dll": "d", "BepInEx/plugins/loose.dll": "x"},
		"outside plugins":     {"BepInEx/plugins/ValheimDiagnostics/ValheimDiagnostics.dll": "d", "BepInEx/core/x.dll": "x"},
		"steam runtime":       {"BepInEx/plugins/ValheimDiagnostics/ValheimDiagnostics.dll": "d", "Valheim_Data/Managed/evil.dll": "e"},
		"traversal":           {"BepInEx/plugins/ValheimDiagnostics/ValheimDiagnostics.dll": "d", "../escape.dll": "e"},
		"executable":          {"BepInEx/plugins/ValheimDiagnostics/ValheimDiagnostics.dll": "d", "BepInEx/plugins/Other/run.exe": "x"},
	}
	for name, entries := range cases {
		if err := ValidateDiagnosticPluginArtifact(writeDiagZip(t, entries)); err == nil {
			t.Fatalf("%s: accepted an unsafe archive", name)
		}
	}
}

// TestDiagnosticPluginArtifactAcceptsVRFixPlugin covers the second portal-owned plugin
// directory, and confirms the allowlist still refuses anything outside those two roots.
func TestDiagnosticPluginArtifactAcceptsVRFixPlugin(t *testing.T) {
	both := writeDiagZip(t, map[string]string{
		"BepInEx/plugins/ValheimDiagnostics/ValheimDiagnostics.dll": "diag",
		"BepInEx/plugins/NeuralyzeVRFixes/NeuralyzeVRFixes.dll":     "fixes",
		"BepInEx/plugins/NeuralyzeVRFixes/neuralyze.vrfixes.cfg":    "cfg",
	})
	if err := ValidateDiagnosticPluginArtifact(both); err != nil {
		t.Fatalf("rejected a valid two-plugin artifact: %v", err)
	}
	// The diagnostics assembly stays mandatory.
	if err := ValidateDiagnosticPluginArtifact(writeDiagZip(t, map[string]string{
		"BepInEx/plugins/NeuralyzeVRFixes/NeuralyzeVRFixes.dll": "fixes",
	})); err == nil {
		t.Fatal("accepted an artifact with no diagnostics assembly")
	}
	// Writing into another mod's directory is refused by publish-time policy, which is
	// where that rule belongs: the structural validator also runs inside already-built
	// clients and must not reject directories it has never heard of.
	if err := validatePortalOwnedPluginRoots(writeDiagZip(t, map[string]string{
		"BepInEx/plugins/ValheimDiagnostics/ValheimDiagnostics.dll": "diag",
		"BepInEx/plugins/Jotunn/Jotunn.dll":                         "hijack",
	})); err == nil {
		t.Fatal("publish-time policy accepted an artifact shadowing another mod")
	}
}
