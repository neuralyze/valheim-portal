package main

import (
	"os"
	"path/filepath"
	"testing"
)

func writeRuntimeSource(t *testing.T, local, name string, files map[string]string) string {
	t.Helper()
	root := filepath.Join(local, "ValheimProfileSync", "profiles", name, "active", "runtime")
	for relative, body := range files {
		path := filepath.Join(root, filepath.FromSlash(relative))
		if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	return root
}

func TestRuntimeOverlayRefusesForeignCollision(t *testing.T) {
	local, game := t.TempDir(), t.TempDir()
	const runtimePath = "Valheim_Data/Managed/UnknownVRRuntime.dll"
	source := writeRuntimeSource(t, local, "vr", map[string]string{runtimePath: "portal"})
	foreign := filepath.Join(game, filepath.FromSlash(runtimePath))
	if err := os.MkdirAll(filepath.Dir(foreign), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(foreign, []byte("foreign"), 0o600); err != nil {
		t.Fatal(err)
	}
	request := profileRequest{World: "world", Profile: "vr", ClientType: clientVR}
	if err := reconcileRuntimeOverlay(game, local, request, "release-one", source); err == nil {
		t.Fatal("accepted a foreign runtime collision")
	}
	if value, err := os.ReadFile(foreign); err != nil || string(value) != "foreign" {
		t.Fatalf("foreign file changed: %q, %v", value, err)
	}
}

func TestRuntimeOverlayLeavesNativeBhapticsFileUntouched(t *testing.T) {
	local, game := t.TempDir(), t.TempDir()
	source := writeRuntimeSource(t, local, "asgard-vr", map[string]string{
		nativeBhapticsRuntimePath:          "VR archive copy",
		"Valheim_Data/Managed/SteamVR.dll": "runtime",
	})
	destination := filepath.Join(game, filepath.FromSlash(nativeBhapticsRuntimePath))
	if err := os.MkdirAll(filepath.Dir(destination), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(destination, []byte("native game copy"), 0o600); err != nil {
		t.Fatal(err)
	}
	request := profileRequest{World: "Asgard", Profile: "asgard-vr", ClientType: clientVR}
	if err := reconcileRuntimeOverlay(game, local, request, "release-one", source); err != nil {
		t.Fatalf("apply VR runtime while retaining native Bhaptics file: %v", err)
	}
	if value, err := os.ReadFile(destination); err != nil || string(value) != "native game copy" {
		t.Fatalf("native Bhaptics file = %q, %v", value, err)
	}
	state, present, err := loadRuntimeOverlayState(filepath.Join(game, runtimeOverlayStateFilename), local)
	if err != nil || !present || len(state.Files) != 1 || state.Files[0].Path != "Valheim_Data/Managed/SteamVR.dll" {
		t.Fatalf("runtime ownership state = %#v, present:%t err:%v", state, present, err)
	}
}

func TestRuntimeOverlayRestoresApprovedCollisionAfterVRRemoval(t *testing.T) {
	local, game := t.TempDir(), t.TempDir()
	const runtimePath = "Valheim_Data/Managed/SteamVR.dll"
	source := writeRuntimeSource(t, local, "asgard-vr", map[string]string{runtimePath: "approved VR runtime"})
	destination := filepath.Join(game, filepath.FromSlash(runtimePath))
	if err := os.MkdirAll(filepath.Dir(destination), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(destination, []byte("pre-existing runtime"), 0o600); err != nil {
		t.Fatal(err)
	}
	request := profileRequest{World: "Asgard", Profile: "asgard-vr", ClientType: clientVR}
	if err := reconcileRuntimeOverlay(game, local, request, "release-one", source); err != nil {
		t.Fatalf("apply approved runtime collision: %v", err)
	}
	if value, err := os.ReadFile(destination); err != nil || string(value) != "approved VR runtime" {
		t.Fatalf("active VR runtime = %q, %v", value, err)
	}
	if err := reconcileRuntimeOverlay(game, local, request, "", ""); err != nil {
		t.Fatalf("remove VR runtime: %v", err)
	}
	if value, err := os.ReadFile(destination); err != nil || string(value) != "pre-existing runtime" {
		t.Fatalf("restored pre-existing runtime = %q, %v", value, err)
	}
}

func TestRuntimeOverlayUpdateBacksUpApprovedCollision(t *testing.T) {
	local, game := t.TempDir(), t.TempDir()
	request := profileRequest{World: "world", Profile: "vr", ClientType: clientVR}
	first := writeRuntimeSource(t, local, "first", map[string]string{"Valheim_Data/Managed/SteamVR.dll": "first"})
	if err := reconcileRuntimeOverlay(game, local, request, "release-one", first); err != nil {
		t.Fatal(err)
	}
	second := writeRuntimeSource(t, local, "second", map[string]string{
		"Valheim_Data/Managed/SteamVR.dll":         "second",
		"Valheim_Data/Managed/SteamVR_Actions.dll": "second-actions",
	})
	foreign := filepath.Join(game, "Valheim_Data", "Managed", "SteamVR_Actions.dll")
	if err := os.WriteFile(foreign, []byte("pre-existing actions"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := reconcileRuntimeOverlay(game, local, request, "release-two", second); err != nil {
		t.Fatalf("apply runtime update over approved collision: %v", err)
	}
	if value, err := os.ReadFile(foreign); err != nil || string(value) != "second-actions" {
		t.Fatalf("updated runtime action file = %q, %v", value, err)
	}
	if err := reconcileRuntimeOverlay(game, local, request, "", ""); err != nil {
		t.Fatalf("remove VR runtime: %v", err)
	}
	if value, err := os.ReadFile(foreign); err != nil || string(value) != "pre-existing actions" {
		t.Fatalf("restored runtime action file = %q, %v", value, err)
	}
}

func TestRuntimeOverlayDiscardsModifiedOwnedFileWithStaleSourceBeforeUpdate(t *testing.T) {
	local, game := t.TempDir(), t.TempDir()
	request := profileRequest{World: "Asgard", Profile: "asgard-vr", ClientType: clientVR}
	const runtimePath = "Valheim_Data/StreamingAssets/AssetBundles"
	destination := filepath.Join(game, filepath.FromSlash(runtimePath))
	if err := os.MkdirAll(filepath.Dir(destination), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(destination, []byte("base-game-runtime"), 0o600); err != nil {
		t.Fatal(err)
	}
	first := writeRuntimeSource(t, local, "first", map[string]string{runtimePath: "first runtime"})
	if err := reconcileRuntimeOverlay(game, local, request, "release-one", first); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(destination, []byte("modified outside the profile sync"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(first, filepath.FromSlash(runtimePath)), []byte("stale runtime source"), 0o600); err != nil {
		t.Fatal(err)
	}
	second := writeRuntimeSource(t, local, "second", map[string]string{runtimePath: "second runtime"})
	if err := reconcileRuntimeOverlay(game, local, request, "release-two", second); err != nil {
		t.Fatalf("repair and update runtime overlay: %v", err)
	}
	if value, err := os.ReadFile(destination); err != nil || string(value) != "second runtime" {
		t.Fatalf("updated runtime = %q, %v", value, err)
	}
}

func TestRuntimeOverlayUpdateRestoresBaseAssetBundleWhenReplacementOmitsIt(t *testing.T) {
	local, game := t.TempDir(), t.TempDir()
	request := profileRequest{World: "Asgard", Profile: "asgard-vr", ClientType: clientVR}
	const baseBundlePath = "Valheim_Data/StreamingAssets/AssetBundles"
	baseBundle := filepath.Join(game, filepath.FromSlash(baseBundlePath))
	if err := os.MkdirAll(filepath.Dir(baseBundle), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(baseBundle, []byte("base-game-asset-bundle"), 0o600); err != nil {
		t.Fatal(err)
	}
	first := writeRuntimeSource(t, local, "first", map[string]string{
		baseBundlePath:                     "obsolete-vr-asset-bundle",
		"Valheim_Data/Managed/SteamVR.dll": "first runtime",
	})
	if err := reconcileRuntimeOverlay(game, local, request, "release-one", first); err != nil {
		t.Fatal(err)
	}
	second := writeRuntimeSource(t, local, "second", map[string]string{
		"Valheim_Data/Managed/SteamVR.dll": "second runtime",
	})
	if err := reconcileRuntimeOverlay(game, local, request, "release-two", second); err != nil {
		t.Fatalf("remove obsolete base bundle override: %v", err)
	}
	if value, err := os.ReadFile(baseBundle); err != nil || string(value) != "base-game-asset-bundle" {
		t.Fatalf("base asset bundle = %q, %v", value, err)
	}
}

func TestRuntimeOverlayUpdateReplacesOnlyOwnedFiles(t *testing.T) {
	local, game := t.TempDir(), t.TempDir()
	request := profileRequest{World: "world", Profile: "vr", ClientType: clientVR}
	first := writeRuntimeSource(t, local, "first", map[string]string{"Valheim_Data/Managed/SteamVR.dll": "first"})
	if err := reconcileRuntimeOverlay(game, local, request, "release-one", first); err != nil {
		t.Fatal(err)
	}
	unrelated := filepath.Join(game, "unrelated.txt")
	if err := os.WriteFile(unrelated, []byte("keep"), 0o600); err != nil {
		t.Fatal(err)
	}
	second := writeRuntimeSource(t, local, "second", map[string]string{"Valheim_Data/Managed/SteamVR.dll": "second"})
	if err := reconcileRuntimeOverlay(game, local, request, "release-two", second); err != nil {
		t.Fatal(err)
	}
	if value, err := os.ReadFile(filepath.Join(game, "Valheim_Data", "Managed", "SteamVR.dll")); err != nil || string(value) != "second" {
		t.Fatalf("updated runtime = %q, %v", value, err)
	}
	if value, err := os.ReadFile(unrelated); err != nil || string(value) != "keep" {
		t.Fatalf("unrelated file changed: %q, %v", value, err)
	}
}
