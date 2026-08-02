package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"os"
	"path/filepath"
	"testing"
)

func TestFlatAndVRProfilesRemainIsolatedThroughSync(t *testing.T) {
	sharedArchive := makeTestZip(t, []zipEntry{{Name: "plugins/ValheimVRMod.dll", Body: "shared-runtime"}})
	sharedSum := sha256.Sum256(sharedArchive)
	sharedPackage := packageDefinition{Namespace: "ValheimVR", Name: "ValheimVR", Version: "0.9.2100", Filename: "shared-valheimvr-0.9.2100.zip", SHA256: hex.EncodeToString(sharedSum[:]), Size: int64(len(sharedArchive))}
	flatPackage, flatArchive := testPackage(t, "zz-flat-1.0.0.zip", "flat.dll", "flat")
	flatPackage.Name = "Flat"
	flatRequest := profileRequest{World: "world", Profile: "midgard-redesign-flat", ClientType: clientFlat}
	flatPayload := testProfileArchive(t, flatRequest, []packageDefinition{sharedPackage, flatPackage}, []zipEntry{{Name: "config/org.bepinex.plugins.valheimvrmod.cfg", Body: "[Immutable]\nnonVrPlayer = true\n"}}, nil)
	flatPortal := newTestPortal(t, flatPayload, map[string][]byte{sharedPackage.Filename: sharedArchive, flatPackage.Filename: flatArchive})
	defer flatPortal.Close()
	flatRequest.Portal = flatPortal.request.Portal
	flatPortal.manifest = testRemoteManifest(flatRequest, "flat-release", flatPayload)

	vrPackage, vrArchive := testPackage(t, "zz-vr-1.0.0.zip", "vr.dll", "vr")
	vrPackage.Name = "VR"
	vrRequest := profileRequest{World: "world", Profile: "midgard-redesign-vr", ClientType: clientVR}
	vrPayload := testProfileArchive(t, vrRequest, []packageDefinition{sharedPackage, vrPackage}, []zipEntry{{Name: "config/org.bepinex.plugins.valheimvrmod.cfg", Body: "[Immutable]\nnonVrPlayer = false\n"}}, nil)
	vrPortal := newTestPortal(t, vrPayload, map[string][]byte{sharedPackage.Filename: sharedArchive, vrPackage.Filename: vrArchive})
	defer vrPortal.Close()
	vrRequest.Portal = vrPortal.request.Portal
	vrPortal.manifest = testRemoteManifest(vrRequest, "vr-release", vrPayload)
	runtime := makeTestZip(t, []zipEntry{
		{Name: "BepInEx/plugins/ValheimVRMod.dll", Body: "runtime"},
		{Name: "Valheim_Data/Managed/SteamVR.dll", Body: "runtime"},
		{Name: "Valheim_Data/Managed/Bhaptics.Tact.dll", Body: "runtime"},
		{Name: "Valheim_Data/Plugins/x86_64/openvr_api.dll", Body: "runtime"},
		{Name: "Valheim_Data/StreamingAssets/SteamVR/actions.json", Body: "runtime"},
		{Name: "Valheim_Data/UnitySubsystems/XRSDKOpenVR/UnitySubsystemsManifest.json", Body: "runtime"},
	})
	runtimeSum := sha256.Sum256(runtime)
	vrPortal.runtime = runtime
	vrPortal.manifest.RuntimeSHA256 = hex.EncodeToString(runtimeSum[:])
	vrPortal.manifest.RuntimeSize = int64(len(runtime))
	steam := filepath.Join(t.TempDir(), "Valheim")
	if err := os.MkdirAll(steam, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(steam, "valheim.exe"), []byte("steam"), 0o600); err != nil {
		t.Fatal(err)
	}

	localAppData := t.TempDir()
	flatSyncer := newProfileSyncer(flatPortal.httpClient)
	flatSyncer.LocalAppData = localAppData
	flatSyncer.GameDir = steam
	vrSyncer := newProfileSyncer(vrPortal.httpClient)
	vrSyncer.LocalAppData = localAppData
	vrSyncer.GameDir = steam
	if changed, err := flatSyncer.syncAuthorized(context.Background(), flatRequest, "test-token-123456"); err != nil || !changed {
		t.Fatalf("flat sync = changed:%t err:%v", changed, err)
	}
	if changed, err := vrSyncer.syncAuthorized(context.Background(), vrRequest, "test-token-123456"); err != nil || !changed {
		t.Fatalf("vr sync = changed:%t err:%v", changed, err)
	}
	flatRoot, err := profileRoot(localAppData, flatRequest)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(steam, "Valheim_Data", "Managed", "SteamVR.dll")); err != nil {
		t.Fatalf("VR runtime was not activated: %v", err)
	}
	if changed, err := flatSyncer.syncAuthorized(context.Background(), flatRequest, "test-token-123456"); err != nil || changed {
		t.Fatalf("flat switch = changed:%t err:%v", changed, err)
	}
	if _, err := os.Stat(filepath.Join(steam, "Valheim_Data", "Managed", "SteamVR.dll")); !os.IsNotExist(err) {
		t.Fatalf("VR runtime survived flat switch: %v", err)
	}
	vrRoot, err := profileRoot(localAppData, vrRequest)
	if err != nil {
		t.Fatal(err)
	}
	if flatRoot == vrRoot {
		t.Fatal("Flat and VR profiles share a profile root")
	}
	if value, err := os.ReadFile(filepath.Join(flatRoot, "active", "config", "org.bepinex.plugins.valheimvrmod.cfg")); err != nil || string(value) != "[Immutable]\nnonVrPlayer = true\n" {
		t.Fatalf("flat ValheimVR config = %q, %v", value, err)
	}
	if value, err := os.ReadFile(filepath.Join(vrRoot, "active", "config", "org.bepinex.plugins.valheimvrmod.cfg")); err != nil || string(value) != "[Immutable]\nnonVrPlayer = false\n" {
		t.Fatalf("VR ValheimVR config = %q, %v", value, err)
	}
	for _, root := range []string{flatRoot, vrRoot} {
		if value, err := os.ReadFile(filepath.Join(root, "active", "BepInEx", "plugins", "ValheimVR-ValheimVR", "ValheimVRMod.dll")); err != nil || string(value) != "shared-runtime" {
			t.Fatalf("shared ValheimVR compatibility plugin in %q = %q, %v", root, value, err)
		}
	}
	if _, err := os.Stat(filepath.Join(flatRoot, "active", "BepInEx", "plugins", "Author-VR", "vr.dll")); !os.IsNotExist(err) {
		t.Fatalf("VR-only package leaked into Flat profile: %v", err)
	}
}
