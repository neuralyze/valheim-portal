package app

import (
	"archive/zip"
	"errors"
	"fmt"
	"io"
	"os"
	"path"
	"strings"
)

const (
	maxVRRuntimeEntryBytes = uint64(512 << 20)
	maxVRRuntimeTotalBytes = uint64(1 << 30)
)

var requiredVRRuntimeFiles = map[string]struct{}{
	"bepinex/plugins/valheimvrmod.dll":                                      {},
	"valheim_data/managed/steamvr.dll":                                      {},
	"valheim_data/managed/bhaptics.tact.dll":                                {},
	"valheim_data/plugins/x86_64/openvr_api.dll":                            {},
	"valheim_data/streamingassets/steamvr/actions.json":                     {},
	"valheim_data/unitysubsystems/xrsdkopenvr/unitysubsystemsmanifest.json": {},
}

var vrManagedFiles = map[string]struct{}{
	"amplify_occlusion.dll": {}, "bhaptics.tact.dll": {}, "final_ik.dll": {}, "ndesk.options.dll": {},
	"root_motion_demo_assets.dll": {}, "root_motion_shared.dll": {}, "steamvr.dll": {}, "steamvr_actions.dll": {},
	"unity.xr.management.dll": {}, "unity.xr.openvr.dll": {}, "unityengine.spatialtracking.dll": {},
	"unityengine.xr.legacyinputhelpers.dll": {}, "valve.newtonsoft.json.dll": {},
}

var vrPluginFiles = map[string]struct{}{
	"openvr_api.dll": {}, "ucrtbased.dll": {}, "xrsdkopenvr.dll": {},
}

var vrStreamingFiles = map[string]struct{}{
	"amplify_resources": {}, "amplify_resources.manifest": {}, "assetbundles": {}, "assetbundles.manifest": {},
	"steamvr_player_prefabs": {}, "steamvr_player_prefabs.manifest": {}, "steamvr_shaders": {}, "steamvr_shaders.manifest": {},
	"vhvr_custom": {}, "vhvr_custom.manifest": {}, "xrmanager": {}, "xrmanager.manifest": {},
}

var vrSteamVRFiles = map[string]struct{}{
	"actions.json":                          {},
	"binding_holographic_hmd.json":          {},
	"binding_index_hmd.json":                {},
	"binding_rift.json":                     {},
	"binding_vive.json":                     {},
	"binding_vive_cosmos.json":              {},
	"binding_vive_pro.json":                 {},
	"binding_vive_tracker_camera.json":      {},
	"binding_vive_tracker_left_ankle.json":  {},
	"binding_vive_tracker_left_foot.json":   {},
	"binding_vive_tracker_right_ankle.json": {},
	"binding_vive_tracker_right_foot.json":  {},
	"binding_vive_tracker_waist.json":       {},
	"bindings_holographic_controller.json":  {},
	"bindings_knuckles.json":                {},
	"bindings_logitech_stylus.json":         {},
	"bindings_oculus_touch.json":            {},
	"bindings_vive_controller.json":         {},
	"bindings_vive_cosmos_controller.json":  {},
	"openvrsettings.asset":                  {},
}

func ValidateVRRuntimeArtifact(artifactPath string) error {
	archive, err := zip.OpenReader(artifactPath)
	if err != nil {
		return err
	}
	defer archive.Close()
	seen := make(map[string]struct{}, len(archive.File))
	found := make(map[string]bool, len(requiredVRRuntimeFiles))
	var total uint64
	for _, file := range archive.File {
		name, err := vrRuntimeArchivePath(file.Name)
		if err != nil {
			return err
		}
		key := strings.ToLower(name)
		if _, exists := seen[key]; exists {
			return errors.New("VR runtime archive contains duplicate paths")
		}
		seen[key] = struct{}{}
		if file.Mode()&os.ModeSymlink != 0 {
			return errors.New("VR runtime archive contains a symbolic link")
		}
		if file.UncompressedSize64 > maxVRRuntimeEntryBytes || total > maxVRRuntimeTotalBytes-file.UncompressedSize64 {
			return errors.New("VR runtime archive is too large")
		}
		total += file.UncompressedSize64
		if key == "valheim_data/streamingassets/assetbundles" || key == "valheim_data/streamingassets/assetbundles.manifest" {
			return fmt.Errorf("VR runtime archive must not replace base-game asset bundle %q", name)
		}
		if !validVRRuntimePath(name, file.FileInfo().IsDir()) {
			return fmt.Errorf("VR runtime archive contains an unsupported path %q", name)
		}
		if !file.FileInfo().IsDir() {
			if _, required := requiredVRRuntimeFiles[key]; required {
				found[key] = true
			}
		}
	}
	for name := range requiredVRRuntimeFiles {
		if !found[name] {
			return fmt.Errorf("VR runtime archive is missing required file %q", name)
		}
	}
	return nil
}

func vrRuntimeArchivePath(value string) (string, error) {
	if value == "" || strings.ContainsAny(value, "\\\x00") || strings.HasPrefix(value, "/") || strings.Contains(value, ":") {
		return "", errors.New("VR runtime archive contains an unsafe path")
	}
	trimmed := strings.TrimSuffix(value, "/")
	if trimmed == "" || path.Clean(trimmed) != trimmed || strings.HasPrefix(trimmed, "../") {
		return "", errors.New("VR runtime archive contains an unsafe path")
	}
	return trimmed, nil
}

func validVRRuntimePath(name string, directory bool) bool {
	key := strings.ToLower(name)
	if directory {
		return key == "bepinex" || key == "bepinex/plugins" || key == "bepinex/plugins/backpacksvrfix" || key == "bepinex/plugins/bhaptics" || key == "bepinex/plugins/spawnprobe" || key == "valheim_data" || key == "valheim_data/managed" || key == "valheim_data/plugins" || key == "valheim_data/plugins/x86_64" || key == "valheim_data/streamingassets" || key == "valheim_data/streamingassets/steamvr" || key == "valheim_data/unitysubsystems" || key == "valheim_data/unitysubsystems/xrsdkopenvr"
	}
	switch {
	case key == "bepinex/plugins/valheimvrmod.dll":
		return true
	case key == "bepinex/plugins/spawnprobe/spawnprobe.dll":
		return true
	case key == "bepinex/plugins/backpacksvrfix/backpacksvrfix.dll":
		return true
	case strings.HasPrefix(key, "bepinex/plugins/bhaptics/"):
		return strings.HasSuffix(key, ".tact") && !strings.Contains(strings.TrimPrefix(key, "bepinex/plugins/bhaptics/"), "/")
	case strings.HasPrefix(key, "valheim_data/managed/"):
		_, found := vrManagedFiles[strings.TrimPrefix(key, "valheim_data/managed/")]
		return found
	case strings.HasPrefix(key, "valheim_data/plugins/x86_64/"):
		_, found := vrPluginFiles[strings.TrimPrefix(key, "valheim_data/plugins/x86_64/")]
		return found
	case strings.HasPrefix(key, "valheim_data/streamingassets/steamvr/"):
		_, found := vrSteamVRFiles[strings.TrimPrefix(key, "valheim_data/streamingassets/steamvr/")]
		return found
	case strings.HasPrefix(key, "valheim_data/streamingassets/"):
		entry := strings.TrimPrefix(key, "valheim_data/streamingassets/")
		if strings.Contains(entry, "/") {
			return false
		}
		_, found := vrStreamingFiles[entry]
		return found
	case key == "valheim_data/unitysubsystems/xrsdkopenvr/unitysubsystemsmanifest.json":
		return true
	default:
		return false
	}
}

func IsVRRuntimeArtifactPath(value string) bool {
	name, err := vrRuntimeArchivePath(value)
	return err == nil && validVRRuntimePath(name, false)
}

func copyVRRuntimeArchiveEntry(file *zip.File, destination string) error {
	input, err := file.Open()
	if err != nil {
		return err
	}
	defer input.Close()
	output, err := os.OpenFile(destination, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	written, copyErr := io.Copy(output, io.LimitReader(input, int64(maxVRRuntimeEntryBytes)+1))
	closeErr := output.Close()
	if copyErr != nil {
		return copyErr
	}
	if written > int64(maxVRRuntimeEntryBytes) {
		return errors.New("VR runtime archive entry is too large")
	}
	return closeErr
}
