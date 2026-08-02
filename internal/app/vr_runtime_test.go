package app

import (
	"archive/zip"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"os"
	"path/filepath"
	"testing"
)

func testVRRuntimeArtifact(t *testing.T) []byte {
	t.Helper()
	var data bytes.Buffer
	archive := zip.NewWriter(&data)
	for _, name := range []string{
		"BepInEx/plugins/ValheimVRMod.dll",
		"BepInEx/plugins/BackpacksVRFix/BackpacksVRFix.dll",
		"Valheim_Data/Managed/SteamVR.dll",
		"Valheim_Data/Managed/Bhaptics.Tact.dll",
		"Valheim_Data/Plugins/x86_64/openvr_api.dll",
		"Valheim_Data/StreamingAssets/SteamVR/actions.json",
		"Valheim_Data/UnitySubsystems/XRSDKOpenVR/UnitySubsystemsManifest.json",
	} {
		entry, err := archive.Create(name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := entry.Write([]byte(name)); err != nil {
			t.Fatal(err)
		}
	}
	if err := archive.Close(); err != nil {
		t.Fatal(err)
	}
	return data.Bytes()
}

func addVRRuntimeArtifact(t *testing.T, store *Store, dir string, release Release) string {
	t.Helper()
	content := testVRRuntimeArtifact(t)
	path := filepath.Join(dir, release.ID+"-vr-runtime.zip")
	if err := os.WriteFile(path, content, 0o600); err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(content)
	if err := store.AddArtifact(context.Background(), Artifact{ID: release.ID + "-runtime", ReleaseID: release.ID, Kind: "vr_runtime", Name: release.ID + "-vr-runtime.zip", SHA256: hex.EncodeToString(sum[:]), Size: int64(len(content)), Path: path}, "admin"); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestValidateVRRuntimeArtifactRejectsUnsafeArchives(t *testing.T) {
	for _, test := range []struct {
		name  string
		build func(*zip.Writer) error
	}{
		{name: "unknown top-level path", build: func(archive *zip.Writer) error {
			entry, err := archive.Create("valheim.exe")
			if err != nil {
				return err
			}
			_, err = entry.Write([]byte("unsafe"))
			return err
		}},
		{name: "unknown managed assembly", build: func(archive *zip.Writer) error {
			entry, err := archive.Create("Valheim_Data/Managed/Unknown.dll")
			if err != nil {
				return err
			}
			_, err = entry.Write([]byte("unsafe"))
			return err
		}},
		{name: "traversal", build: func(archive *zip.Writer) error {
			entry, err := archive.Create("Valheim_Data/Managed/../evil.dll")
			if err != nil {
				return err
			}
			_, err = entry.Write([]byte("unsafe"))
			return err
		}},
		{name: "case-insensitive duplicate", build: func(archive *zip.Writer) error {
			for _, name := range []string{"BepInEx/plugins/ValheimVRMod.dll", "bepinex/plugins/valheimvrmod.dll"} {
				entry, err := archive.Create(name)
				if err != nil {
					return err
				}
				if _, err := entry.Write([]byte(name)); err != nil {
					return err
				}
			}
			return nil
		}},
		{name: "symbolic link", build: func(archive *zip.Writer) error {
			header := &zip.FileHeader{Name: "BepInEx/plugins/ValheimVRMod.dll", Method: zip.Store}
			header.SetMode(os.ModeSymlink | 0o777)
			entry, err := archive.CreateHeader(header)
			if err != nil {
				return err
			}
			_, err = entry.Write([]byte("target"))
			return err
		}},
	} {
		t.Run(test.name, func(t *testing.T) {
			var data bytes.Buffer
			archive := zip.NewWriter(&data)
			if err := test.build(archive); err != nil {
				t.Fatal(err)
			}
			if err := archive.Close(); err != nil {
				t.Fatal(err)
			}
			path := filepath.Join(t.TempDir(), "runtime.zip")
			if err := os.WriteFile(path, data.Bytes(), 0o600); err != nil {
				t.Fatal(err)
			}
			if err := ValidateVRRuntimeArtifact(path); err == nil {
				t.Fatal("accepted unsafe VR runtime archive")
			}
		})
	}
	path := filepath.Join(t.TempDir(), "broken.zip")
	if err := os.WriteFile(path, []byte("not a ZIP archive"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := ValidateVRRuntimeArtifact(path); err == nil {
		t.Fatal("accepted malformed VR runtime archive")
	}
}

func TestValidateVRRuntimeArtifactAcceptsCanonicalRuntime(t *testing.T) {
	path := filepath.Join(t.TempDir(), "runtime.zip")
	if err := os.WriteFile(path, testVRRuntimeArtifact(t), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := ValidateVRRuntimeArtifact(path); err != nil {
		t.Fatalf("rejected valid VR runtime archive: %v", err)
	}
}

func TestVRRuntimeDoesNotOverwriteBaseGameAssetBundle(t *testing.T) {
	for _, name := range []string{
		"Valheim_Data/StreamingAssets/AssetBundles",
		"Valheim_Data/StreamingAssets/AssetBundles.manifest",
	} {
		if !validVRRuntimePath(name, false) {
			t.Fatalf("legacy runtime cleanup path rejected: %q", name)
		}
	}
}

func TestValidateVRRuntimeArtifactRejectsBaseGameAssetBundle(t *testing.T) {
	var data bytes.Buffer
	archive := zip.NewWriter(&data)
	for _, name := range []string{
		"BepInEx/plugins/ValheimVRMod.dll",
		"Valheim_Data/Managed/SteamVR.dll",
		"Valheim_Data/Managed/Bhaptics.Tact.dll",
		"Valheim_Data/Plugins/x86_64/openvr_api.dll",
		"Valheim_Data/StreamingAssets/SteamVR/actions.json",
		"Valheim_Data/StreamingAssets/AssetBundles",
		"Valheim_Data/UnitySubsystems/XRSDKOpenVR/UnitySubsystemsManifest.json",
	} {
		entry, err := archive.Create(name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := entry.Write([]byte(name)); err != nil {
			t.Fatal(err)
		}
	}
	if err := archive.Close(); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "runtime.zip")
	if err := os.WriteFile(path, data.Bytes(), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := ValidateVRRuntimeArtifact(path); err == nil {
		t.Fatal("accepted VR runtime that replaces the base-game AssetBundles")
	}
}
