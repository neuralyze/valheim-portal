package profilecfg

import (
	"strings"
	"testing"
)

func TestPatchINIRewritesOnlyTheTargetedSection(t *testing.T) {
	original := `# leading comment
[Logging.Console]
## describes the level
LogLevels = Info
Enabled = false

[Logging.Disk]
LogLevels = Info
AppendLog = false
`
	patched := string(PatchINI([]byte(original), map[string]string{
		"Logging.Disk|LogLevels": "All", "Logging.Disk|AppendLog": "true",
	}))
	console := section(t, patched, "Logging.Console")
	disk := section(t, patched, "Logging.Disk")
	if !strings.Contains(console, "LogLevels = Info") {
		t.Fatalf("console section was modified: %q", console)
	}
	if !strings.Contains(disk, "LogLevels = All") || !strings.Contains(disk, "AppendLog = true") {
		t.Fatalf("disk section not patched: %q", disk)
	}
	if !strings.Contains(patched, "# leading comment") || !strings.Contains(patched, "## describes the level") {
		t.Fatalf("comments were dropped: %q", patched)
	}
	if strings.Count(patched, "AppendLog") != 1 {
		t.Fatalf("key was duplicated rather than rewritten: %q", patched)
	}
}

func TestPatchINIAppendsMissingSectionsAndKeys(t *testing.T) {
	patched := string(PatchINI([]byte("[Logging.Disk]\nAppendLog = false\n"), map[string]string{
		"Logging.Disk|AppendLog": "true", "Logging.Disk|LogLevels": "All",
		"Harmony.Logger|LogChannels": "Warn, Error, Debug",
	}))
	disk := section(t, patched, "Logging.Disk")
	if !strings.Contains(disk, "AppendLog = true") || !strings.Contains(disk, "LogLevels = All") {
		t.Fatalf("missing key not appended to existing section: %q", disk)
	}
	harmony := section(t, patched, "Harmony.Logger")
	if !strings.Contains(harmony, "LogChannels = Warn, Error, Debug") {
		t.Fatalf("missing section not appended: %q", patched)
	}
}

func TestPatchINIFromEmptyProducesEveryPatch(t *testing.T) {
	patched := string(PatchINI(nil, DebugLoggingPatches[ProfilerConfigName]))
	if !strings.Contains(patched, "[General]") || !strings.Contains(patched, "ProfilingEnabled = true") {
		t.Fatalf("empty input did not produce patches: %q", patched)
	}
}

// section returns the body of a single INI section so assertions cannot be
// satisfied by a matching key in a different section.
func section(t *testing.T, content, name string) string {
	t.Helper()
	lines := strings.Split(content, "\n")
	var out []string
	inside := false
	for _, line := range lines {
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "[") && strings.HasSuffix(trimmed, "]") {
			inside = strings.TrimSpace(trimmed[1:len(trimmed)-1]) == name
			continue
		}
		if inside {
			out = append(out, line)
		}
	}
	if len(out) == 0 {
		t.Fatalf("section %q not found in %q", name, content)
	}
	return strings.Join(out, "\n")
}
