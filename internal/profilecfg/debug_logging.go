// Package profilecfg rewrites BepInEx-style configuration files that ship inside a
// profile definition. The portal uses it to republish a profile with client
// diagnostics toggled without rebuilding the package set, and the profile
// definition builder uses it to emit the same configuration at build time, so both
// paths agree on exactly what "debug logging" means.
package profilecfg

import (
	"bufio"
	"bytes"
	"fmt"
	"sort"
	"strings"
)

// BepInExConfigName and ProfilerConfigName are the profile ZIP entries the toggle
// rewrites. ProfilerConfigName is the file sighsorry-LoadTimeProfiler reads, as
// documented by the package; the package itself is pinned by the managed profile,
// so only its configuration is touched here.
const (
	BepInExConfigName  = "config/BepInEx.cfg"
	ProfilerConfigName = "config/sighsorry.LoadTimeProfiler.cfg"
)

// DebugLoggingPatches force verbose client diagnostics. Absent keys are appended,
// present keys are rewritten, and every unrelated setting is preserved.
var DebugLoggingPatches = map[string]map[string]string{
	BepInExConfigName: {
		"Harmony.Logger|LogChannels": "Warn, Error, Debug",
		"Logging|UnityLogListening":  "true",
		"Logging.Console|Enabled":    "true",
		"Logging.Console|LogLevels":  "All",
		"Logging.Disk|Enabled":       "true",
		"Logging.Disk|WriteUnityLog": "true",
		"Logging.Disk|AppendLog":     "true",
		"Logging.Disk|LogLevels":     "All",
	},
	ProfilerConfigName: {"General|ProfilingEnabled": "true"},
}

// QuietLoggingPatches are the explicit counterpart used when the toggle is turned
// off. Disabling deliberately writes concrete non-verbose values rather than trying
// to restore whatever the profile shipped originally: the previous content is not
// recoverable from a published artifact, and a deterministic result is what makes
// the toggle reversible.
var QuietLoggingPatches = map[string]map[string]string{
	BepInExConfigName: {
		"Harmony.Logger|LogChannels": "Warn, Error",
		"Logging.Console|LogLevels":  "Fatal, Error, Warning, Message, Info",
		"Logging.Disk|AppendLog":     "false",
		"Logging.Disk|LogLevels":     "Fatal, Error, Warning, Message, Info",
	},
	ProfilerConfigName: {"General|ProfilingEnabled": "false"},
}

// Patches selects the patch set for a debug-logging state.
func Patches(debug bool) map[string]map[string]string {
	if debug {
		return DebugLoggingPatches
	}
	return QuietLoggingPatches
}

// PatchINI applies "Section|Key" overrides to a BepInEx configuration file,
// preserving comments, ordering, and unrelated settings. Sections and keys that do
// not exist are appended. A nil original yields a file containing only the patches.
func PatchINI(original []byte, patches map[string]string) []byte {
	remaining := make(map[string]map[string]string)
	for qualified, value := range patches {
		section, key := splitQualified(qualified)
		if _, ok := remaining[section]; !ok {
			remaining[section] = map[string]string{}
		}
		remaining[section][key] = value
	}

	var out bytes.Buffer
	section := ""
	flush := func(name string) {
		keys, ok := remaining[name]
		if !ok || len(keys) == 0 {
			return
		}
		for _, key := range sortedKeys(keys) {
			fmt.Fprintf(&out, "%s = %s\n", key, keys[key])
		}
		delete(remaining, name)
	}

	scanner := bufio.NewScanner(bytes.NewReader(original))
	scanner.Buffer(make([]byte, 0, 64*1024), 4*1024*1024)
	for scanner.Scan() {
		line := scanner.Text()
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "[") && strings.HasSuffix(trimmed, "]") {
			flush(section)
			section = strings.TrimSpace(trimmed[1 : len(trimmed)-1])
			out.WriteString(line)
			out.WriteString("\n")
			continue
		}
		if key, ok := iniKey(trimmed); ok {
			if value, pending := remaining[section][key]; pending {
				fmt.Fprintf(&out, "%s = %s\n", key, value)
				delete(remaining[section], key)
				continue
			}
		}
		out.WriteString(line)
		out.WriteString("\n")
	}
	flush(section)

	for _, name := range sortedSections(remaining) {
		keys := remaining[name]
		if len(keys) == 0 {
			continue
		}
		if out.Len() > 0 && !bytes.HasSuffix(out.Bytes(), []byte("\n\n")) {
			out.WriteString("\n")
		}
		fmt.Fprintf(&out, "[%s]\n", name)
		for _, key := range sortedKeys(keys) {
			fmt.Fprintf(&out, "%s = %s\n", key, keys[key])
		}
	}
	return out.Bytes()
}

func splitQualified(qualified string) (string, string) {
	index := strings.Index(qualified, "|")
	if index < 0 {
		return "", qualified
	}
	return qualified[:index], qualified[index+1:]
}

func iniKey(trimmed string) (string, bool) {
	if trimmed == "" || strings.HasPrefix(trimmed, "#") || strings.HasPrefix(trimmed, ";") {
		return "", false
	}
	index := strings.Index(trimmed, "=")
	if index < 1 {
		return "", false
	}
	return strings.TrimSpace(trimmed[:index]), true
}

func sortedKeys(keys map[string]string) []string {
	names := make([]string, 0, len(keys))
	for key := range keys {
		names = append(names, key)
	}
	sort.Strings(names)
	return names
}

func sortedSections(sections map[string]map[string]string) []string {
	names := make([]string, 0, len(sections))
	for name := range sections {
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}
