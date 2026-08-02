package main

import (
	"bufio"
	"os"
	"path/filepath"
	"strings"
)

// consoleSection is the BepInEx.cfg section that decides whether a console
// window appears alongside the game.
const consoleSection = "logging.console"

// bepinexConsoleEnabled reports whether this profile will show the BepInEx
// console when the game starts.
//
// It matters because the console is what a player watches while a heavily
// modded profile loads. With it enabled they can see mods being patched; with it
// disabled the desktop is empty until the game window finally appears, so the
// launcher has to be the thing that says something is still happening.
//
// Anything unreadable, missing, or ambiguous counts as disabled. A false
// negative only costs a progress indicator nobody needed; a false positive
// leaves the player staring at nothing, which is the bug this answers.
func bepinexConsoleEnabled(active string) bool {
	for _, candidate := range []string{
		filepath.Join(active, "BepInEx", "config", "BepInEx.cfg"),
		filepath.Join(active, "config", "BepInEx.cfg"),
	} {
		enabled, found := consoleEnabledInFile(candidate)
		if found {
			return enabled
		}
	}
	return false
}

func consoleEnabledInFile(path string) (enabled bool, found bool) {
	file, err := os.Open(path)
	if err != nil {
		return false, false
	}
	defer file.Close()
	scanner := bufio.NewScanner(file)
	section := ""
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") || strings.HasPrefix(line, ";") {
			continue
		}
		if strings.HasPrefix(line, "[") && strings.HasSuffix(line, "]") {
			section = strings.ToLower(strings.TrimSpace(line[1 : len(line)-1]))
			continue
		}
		if section != consoleSection {
			continue
		}
		name, value, split := strings.Cut(line, "=")
		if !split || !strings.EqualFold(strings.TrimSpace(name), "Enabled") {
			continue
		}
		return strings.EqualFold(strings.TrimSpace(value), "true"), true
	}
	if scanner.Err() != nil {
		return false, false
	}
	return false, false
}
