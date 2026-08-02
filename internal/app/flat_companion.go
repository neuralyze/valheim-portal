package app

import (
	"archive/zip"
	"errors"
	"fmt"
	"strings"
)

const (
	maxFlatCompanionEntryBytes = uint64(512 << 20)
	maxFlatCompanionTotalBytes = uint64(1 << 30)
)

var flatCompanionPlugins = map[string]struct{}{
	"bepinex/plugins/amplify_occlusion.dll":                 {},
	"bepinex/plugins/bhaptics.tact.dll":                     {},
	"bepinex/plugins/final_ik.dll":                          {},
	"bepinex/plugins/ndesk.options.dll":                     {},
	"bepinex/plugins/root_motion_demo_assets.dll":           {},
	"bepinex/plugins/root_motion_shared.dll":                {},
	"bepinex/plugins/steamvr.dll":                           {},
	"bepinex/plugins/steamvr_actions.dll":                   {},
	"bepinex/plugins/unity.xr.management.dll":               {},
	"bepinex/plugins/unity.xr.openvr.dll":                   {},
	"bepinex/plugins/unityengine.spatialtracking.dll":       {},
	"bepinex/plugins/unityengine.xr.legacyinputhelpers.dll": {},
	"bepinex/plugins/valheimvrflatdodgepatchfix.dll":        {},
	"bepinex/plugins/valheimvrmod.dll":                      {},
	"bepinex/plugins/valve.newtonsoft.json.dll":             {},
}

func ValidateFlatCompanionArtifact(artifactPath string) error {
	archive, err := zip.OpenReader(artifactPath)
	if err != nil {
		return err
	}
	defer archive.Close()

	seen := make(map[string]struct{}, len(archive.File))
	foundMod := false
	var total uint64
	for _, file := range archive.File {
		name, err := flatCompanionArchivePath(file.Name)
		if err != nil {
			return err
		}
		key := strings.ToLower(name)
		if _, exists := seen[key]; exists {
			return errors.New("Flat companion archive contains duplicate paths")
		}
		seen[key] = struct{}{}
		if file.Mode()&0o120000 != 0 {
			return errors.New("Flat companion archive contains a symbolic link")
		}
		if file.UncompressedSize64 > maxFlatCompanionEntryBytes || total > maxFlatCompanionTotalBytes-file.UncompressedSize64 {
			return errors.New("Flat companion archive is too large")
		}
		total += file.UncompressedSize64
		if !validFlatCompanionPath(name, file.FileInfo().IsDir()) {
			return fmt.Errorf("Flat companion archive contains an unsupported path %q", name)
		}
		if key == "bepinex/plugins/valheimvrmod.dll" {
			foundMod = true
		}
	}
	if !foundMod {
		return errors.New("Flat companion archive is missing BepInEx/plugins/ValheimVRMod.dll")
	}
	return nil
}

func flatCompanionArchivePath(value string) (string, error) {
	return vrRuntimeArchivePath(value)
}

func validFlatCompanionPath(name string, directory bool) bool {
	key := strings.ToLower(name)
	if directory {
		return key == "bepinex" || key == "bepinex/plugins" || key == "bepinex/config"
	}
	// The locally built companion carries its human-readable installation note
	// at the archive root. Synchronization ignores it; permitting this exact
	// inert file keeps the executable payload allowlist closed.
	if key == "install.txt" || key == "bepinex/config/org.bepinex.plugins.valheimvrmod.cfg" {
		return true
	}
	_, allowed := flatCompanionPlugins[key]
	return allowed
}
