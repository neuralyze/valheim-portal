package app

import (
	"archive/zip"
	"errors"
	"fmt"
	"os"
	"path"
	"strings"
)

const (
	maxDiagnosticPluginEntryBytes = uint64(8 << 20)
	maxDiagnosticPluginTotalBytes = uint64(32 << 20)
	pluginParent                  = "bepinex/plugins/"
	diagnosticPluginRoot          = "bepinex/plugins/valheimdiagnostics/"
	diagnosticPluginAssembly      = "bepinex/plugins/valheimdiagnostics/valheimdiagnostics.dll"
	// The same artifact also delivers NeuralyzeVRFixes, which corrects real VR defects
	// and has to stay installed whether or not verbose logging is wanted. Requiring the
	// diagnostics assembly specifically meant the two could never be separated: an
	// archive carrying only the fixes was rejected at publish time, so every VR client
	// that needed the fixes also got a plugin that logged about 1,500 lines a session
	// and kept its Harmony patches installed even when silenced.
	vrFixesPluginAssembly = "bepinex/plugins/neuralyzevrfixes/neuralyzevrfixes.dll"
	vrFixPluginRoot       = "bepinex/plugins/neuralyzevrfixes/"
	// The exploration reporter records what each player has uncovered on their own map and the pins
	// they placed, so the shared map can show where people have actually been rather than where the
	// server happened to load ground. It ships in the same artifact because it is a portal plugin like
	// the other two, and it gets its own directory: a map reporter filed under "NeuralyzeVRFixes"
	// would mislead whoever next reads a profile's plugin list.
	explorationReporterRoot = "bepinex/plugins/neuralyzeexplorationreporter/"
)

// portalPluginRoots are the plugin directories the portal is permitted to publish
// into. This is a publish-time policy, enforced by validatePortalOwnedPluginRoots,
// deliberately NOT by the structural validator below.
//
// The distinction matters because the structural validator also runs on the client,
// inside an installed executable that updates on its own schedule. When this list was
// enforced client-side, adding a second plugin directory broke every already-installed
// client: the portal published a valid artifact and the old client rejected it as
// containing an "unsupported directory". Publish-time policy may evolve freely;
// install-time validation must only enforce properties that never change.
var portalPluginRoots = []string{diagnosticPluginRoot, vrFixPluginRoot, explorationReporterRoot}

// ValidateDiagnosticPluginArtifact enforces the structural guarantees of a
// portal-hosted client plugin archive. It runs on the portal at publish time and on
// the client at install time, so it checks only invariants that hold for all time:
// every entry lands in exactly one directory directly beneath BepInEx/plugins, carries
// only assemblies or configuration, and cannot escape the destination.
//
// It deliberately does not restrict WHICH directory, so a newly published plugin
// installs on clients built before that plugin existed.
func ValidateDiagnosticPluginArtifact(artifactPath string) error {
	archive, err := zip.OpenReader(artifactPath)
	if err != nil {
		return err
	}
	defer archive.Close()
	seen := make(map[string]struct{}, len(archive.File))
	var total uint64
	assembly := false
	for _, file := range archive.File {
		name, err := diagnosticPluginEntryPath(file.Name)
		if err != nil {
			return err
		}
		key := strings.ToLower(name)
		if _, exists := seen[key]; exists {
			return errors.New("diagnostics plugin archive contains duplicate paths")
		}
		seen[key] = struct{}{}
		if file.Mode()&os.ModeSymlink != 0 {
			return errors.New("diagnostics plugin archive contains a symbolic link")
		}
		if file.UncompressedSize64 > maxDiagnosticPluginEntryBytes || total > maxDiagnosticPluginTotalBytes-file.UncompressedSize64 {
			return errors.New("diagnostics plugin archive is too large")
		}
		total += file.UncompressedSize64
		if file.FileInfo().IsDir() {
			if key == "bepinex" || key == strings.TrimSuffix(pluginParent, "/") || pluginDirectoryName(key+"/") != "" {
				continue
			}
			return fmt.Errorf("diagnostics plugin archive contains an unsupported directory %q", name)
		}
		directory := pluginDirectoryName(key)
		if directory == "" {
			return fmt.Errorf("diagnostics plugin archive contains an unsupported path %q", name)
		}
		entry := strings.TrimPrefix(key, pluginParent+directory+"/")
		if entry == "" || strings.Contains(entry, "/") {
			return fmt.Errorf("diagnostics plugin archive contains an unsupported path %q", name)
		}
		if !strings.HasSuffix(entry, ".dll") && !strings.HasSuffix(entry, ".cfg") {
			return fmt.Errorf("diagnostics plugin archive contains an unsupported file %q", name)
		}
		if key == diagnosticPluginAssembly || key == vrFixesPluginAssembly {
			assembly = true
		}
	}
	if !assembly {
		return errors.New("plugin archive carries neither " +
			"BepInEx/plugins/ValheimDiagnostics/ValheimDiagnostics.dll nor " +
			"BepInEx/plugins/NeuralyzeVRFixes/NeuralyzeVRFixes.dll")
	}
	return nil
}

// validatePortalOwnedPluginRoots is the publish-time policy: the portal only ships
// into directories it owns, so a published artifact can never shadow a Thunderstore
// mod's files. Enforced when an artifact is added to a release, not when a client
// installs one.
func validatePortalOwnedPluginRoots(artifactPath string) error {
	archive, err := zip.OpenReader(artifactPath)
	if err != nil {
		return err
	}
	defer archive.Close()
	for _, file := range archive.File {
		name, err := diagnosticPluginEntryPath(file.Name)
		if err != nil {
			return err
		}
		key := strings.ToLower(name)
		if key == "bepinex" || key == strings.TrimSuffix(pluginParent, "/") {
			continue
		}
		owned := false
		for _, root := range portalPluginRoots {
			if key == strings.TrimSuffix(root, "/") || strings.HasPrefix(key, root) {
				owned = true
				break
			}
		}
		if !owned {
			return fmt.Errorf("diagnostics plugin archive writes outside the portal's plugin directories: %q", name)
		}
	}
	return nil
}

// pluginDirectoryName returns the single directory segment directly beneath
// BepInEx/plugins that owns an entry, or "" when the entry is not shaped that way.
func pluginDirectoryName(key string) string {
	if !strings.HasPrefix(key, pluginParent) {
		return ""
	}
	remainder := strings.TrimPrefix(key, pluginParent)
	slash := strings.Index(remainder, "/")
	if slash < 1 {
		return ""
	}
	directory := remainder[:slash]
	if directory == "." || directory == ".." {
		return ""
	}
	return directory
}

func diagnosticPluginEntryPath(value string) (string, error) {
	if value == "" || strings.ContainsAny(value, "\\\x00") || strings.HasPrefix(value, "/") || strings.Contains(value, ":") {
		return "", errors.New("diagnostics plugin archive contains an unsafe path")
	}
	trimmed := strings.TrimSuffix(value, "/")
	if trimmed == "" || path.Clean(trimmed) != trimmed || strings.HasPrefix(trimmed, "../") {
		return "", errors.New("diagnostics plugin archive contains an unsafe path")
	}
	return trimmed, nil
}
