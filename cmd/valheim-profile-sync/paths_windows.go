//go:build windows

package main

import (
	"errors"
	"os"
	"path/filepath"
	"syscall"

	"github.com/lxn/walk"
	"github.com/lxn/win"
	"golang.org/x/sys/windows/registry"
)

func localApplicationData() (string, error) {
	return walk.LocalAppDataPath()
}

func likelySteamValheimDirectories() []string {
	steamRoots := make([]string, 0, 6)
	if key, err := registry.OpenKey(registry.CURRENT_USER, `Software\Valve\Steam`, registry.QUERY_VALUE); err == nil {
		if steamPath, _, valueErr := key.GetStringValue("SteamPath"); valueErr == nil {
			steamRoots = append(steamRoots, steamPath)
		}
		key.Close()
	}
	for _, programFiles := range []string{os.Getenv("ProgramFiles(x86)"), os.Getenv("ProgramFiles")} {
		if programFiles != "" {
			steamRoots = append(steamRoots, filepath.Join(programFiles, "Steam"))
		}
	}
	if systemDrive := os.Getenv("SystemDrive"); systemDrive != "" {
		steamRoots = append(steamRoots, filepath.Join(systemDrive+string(filepath.Separator), "Steam"))
		steamRoots = append(steamRoots, filepath.Join(systemDrive+string(filepath.Separator), "Games", "Steam"))
	}
	candidates := make([]string, 0, len(steamRoots))
	for _, root := range steamRoots {
		candidates = append(candidates, filepath.Join(root, "steamapps", "common", "Valheim"))
	}
	return candidates
}

func desktopDirectory() (string, error) {
	key, err := registry.OpenKey(registry.CURRENT_USER, `Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders`, registry.QUERY_VALUE)
	if err == nil {
		defer key.Close()
		if directory, _, valueErr := key.GetStringValue("Desktop"); valueErr == nil && directory != "" {
			return filepath.Clean(directory), nil
		}
	}
	var path [win.MAX_PATH]uint16
	if !win.SHGetSpecialFolderPath(0, &path[0], win.CSIDL_DESKTOPDIRECTORY, false) {
		return "", errors.New("cannot determine the Explorer Desktop directory")
	}
	return syscall.UTF16ToString(path[:]), nil
}
