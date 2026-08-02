package main

import (
	"fmt"
	"os"
	"sort"

	"github.com/neuralyze/valheim-portal/internal/profilecfg"
)

// applyDebugLogging rewrites the configuration entries so verbose client
// diagnostics are guaranteed. Building without the flag ships the profile's own
// configuration untouched, which keeps the toggle idempotent in both directions.
func applyDebugLogging(entries []configEntry) ([]configEntry, error) {
	targets := profilecfg.DebugLoggingPatches
	patched := make([]configEntry, 0, len(entries)+len(targets))
	seen := map[string]bool{}
	for _, entry := range entries {
		patches, ok := targets[entry.zipName]
		if !ok || entry.isDir {
			patched = append(patched, entry)
			continue
		}
		original, err := os.ReadFile(entry.source)
		if err != nil {
			return nil, fmt.Errorf("read %s: %w", entry.zipName, err)
		}
		entry.source = ""
		entry.body = profilecfg.PatchINI(original, patches)
		seen[entry.zipName] = true
		patched = append(patched, entry)
	}
	for name, patches := range targets {
		if seen[name] {
			continue
		}
		patched = append(patched, configEntry{zipName: name, body: profilecfg.PatchINI(nil, patches)})
	}
	sort.Slice(patched, func(i, j int) bool { return patched[i].zipName < patched[j].zipName })
	return patched, nil
}
