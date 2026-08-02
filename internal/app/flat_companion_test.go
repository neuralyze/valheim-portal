package app

import (
	"archive/zip"
	"bytes"
	"os"
	"path/filepath"
	"testing"
)

func writeFlatCompanion(t *testing.T, entries map[string]string) string {
	t.Helper()
	var data bytes.Buffer
	archive := zip.NewWriter(&data)
	for name, body := range entries {
		writer, err := archive.Create(name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := writer.Write([]byte(body)); err != nil {
			t.Fatal(err)
		}
	}
	if err := archive.Close(); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "companion.zip")
	if err := os.WriteFile(path, data.Bytes(), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestValidateFlatCompanionArtifact(t *testing.T) {
	path := writeFlatCompanion(t, map[string]string{
		"BepInEx/plugins/ValheimVRMod.dll":                    "fixed",
		"BepInEx/plugins/SteamVR.dll":                         "dependency",
		"BepInEx/config/org.bepinex.plugins.valheimvrmod.cfg": "[Immutable]\nnonVrPlayer = true\n",
		"INSTALL.txt": "player instructions",
	})
	if err := ValidateFlatCompanionArtifact(path); err != nil {
		t.Fatal(err)
	}
}

func TestValidateFlatCompanionArtifactRejectsGameRuntime(t *testing.T) {
	path := writeFlatCompanion(t, map[string]string{
		"BepInEx/plugins/ValheimVRMod.dll": "fixed",
		"Valheim_Data/Managed/SteamVR.dll": "forbidden",
	})
	if err := ValidateFlatCompanionArtifact(path); err == nil {
		t.Fatal("accepted game runtime")
	}
}

func TestValidateFlatCompanionArtifactRejectsUnknownPlugin(t *testing.T) {
	path := writeFlatCompanion(t, map[string]string{
		"BepInEx/plugins/ValheimVRMod.dll": "fixed",
		"BepInEx/plugins/unknown.dll":      "unexpected",
	})
	if err := ValidateFlatCompanionArtifact(path); err == nil {
		t.Fatal("accepted an unknown Flat companion plugin")
	}
}
