package main

import (
	"bytes"
	"crypto/sha256"
	"embed"
	"errors"
	"os"
	"path/filepath"
	"strings"
)

// Desktop shortcut icons, and why this file exists.
//
// The client used to copy itself into LocalAppData, which gave the shortcut a path that never moved.
// Defender then classified an unsigned binary that duplicates itself, registers a handler aimed at
// the duplicate, and downloads more executables as Trojan:Win32/Bearfoos.A!ml and deleted it. Dropping
// the self-copy was the right fix, but the shortcut's icon path went with it: IconFile now pointed at
// wherever the player happened to save the download, so replacing, moving, or quarantining that file
// left every shortcut drawing a blank icon with nothing to repair it.
//
// The icon is not the executable. Writing a 100 KB .ico into the application's own data directory is
// not dropper behaviour - nothing executes it and no handler points at it - and it restores the one
// property the shortcut needs: a path that stays put across updates.
//
//go:embed neuralyze.ico
var iconAsset embed.FS

const iconFileName = "ValheimProfileSync.ico"

// stableIconPath materialises the icon beside the profile store and returns its path. It is called on
// every run, so a deleted or truncated icon repairs itself by starting the application - the property
// the self-copy used to provide and nothing replaced.
func stableIconPath() (string, error) {
	localAppData, err := localApplicationData()
	if err != nil {
		return "", err
	}
	root, _, err := loadProfileStorageDirectory(localAppData)
	if err != nil {
		return "", err
	}
	if strings.TrimSpace(root) == "" {
		return "", errors.New("no profile storage directory")
	}
	want, err := iconAsset.ReadFile("neuralyze.ico")
	if err != nil {
		return "", err
	}
	path := filepath.Join(root, iconFileName)
	if current, readErr := os.ReadFile(path); readErr == nil && bytes.Equal(sha256sum(current), sha256sum(want)) {
		return path, nil
	}
	if err := os.MkdirAll(root, 0o755); err != nil {
		return "", err
	}
	if err := writeTextAtomically(path, string(want)); err != nil {
		return "", err
	}
	return path, nil
}

func sha256sum(data []byte) []byte {
	sum := sha256.Sum256(data)
	return sum[:]
}

// repairShortcutIcons rewrites the IconFile line of shortcuts this application wrote, so a Desktop
// full of blank icons fixes itself on the next run rather than needing every shortcut recreated.
//
// It only ever edits a file that is one of ours - an [InternetShortcut] whose URL uses our scheme -
// and it only touches the icon lines. A shortcut the player moved elsewhere is not searched for,
// because searching a filesystem for shortcuts is not something this application should do.
func repairShortcutIcons(desktop, icon string) (int, error) {
	if desktop == "" || icon == "" {
		return 0, errors.New("cannot repair shortcut icons")
	}
	entries, err := os.ReadDir(desktop)
	if err != nil {
		return 0, err
	}
	repaired := 0
	for _, entry := range entries {
		if entry.IsDir() || !strings.EqualFold(filepath.Ext(entry.Name()), ".url") {
			continue
		}
		path := filepath.Join(desktop, entry.Name())
		body, readErr := os.ReadFile(path)
		if readErr != nil {
			continue
		}
		text := string(body)
		if !strings.Contains(text, "[InternetShortcut]") || !strings.Contains(text, protocolScheme+"://") {
			continue
		}
		updated, changed := replaceIconLines(text, icon)
		if !changed {
			continue
		}
		if writeErr := writeTextAtomically(path, updated); writeErr != nil {
			continue
		}
		repaired++
	}
	return repaired, nil
}

// replaceIconLines sets IconFile and IconIndex, adding them when absent. Line endings stay CRLF
// because that is what Explorer writes and what the rest of this file produces.
func replaceIconLines(text, icon string) (string, bool) {
	lines := strings.Split(strings.ReplaceAll(text, "\r\n", "\n"), "\n")
	out := make([]string, 0, len(lines)+2)
	seenIcon, changed := false, false
	for _, line := range lines {
		lower := strings.ToLower(strings.TrimSpace(line))
		switch {
		case strings.HasPrefix(lower, "iconfile="):
			seenIcon = true
			want := "IconFile=" + icon
			if line != want {
				changed = true
			}
			out = append(out, want)
		case strings.HasPrefix(lower, "iconindex="):
			if line != "IconIndex=0" {
				changed = true
			}
			out = append(out, "IconIndex=0")
		default:
			out = append(out, line)
		}
	}
	if !seenIcon {
		// An older shortcut with no icon at all: give it one rather than leaving it blank.
		body := strings.Join(out, "\n")
		body = strings.TrimRight(body, "\n")
		out = append(strings.Split(body, "\n"), "IconFile="+icon, "IconIndex=0")
		changed = true
	}
	joined := strings.Join(out, "\r\n")
	if !strings.HasSuffix(joined, "\r\n") {
		joined += "\r\n"
	}
	return joined, changed
}
