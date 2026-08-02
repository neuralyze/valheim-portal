//go:build !windows

package main

import (
	"errors"
	"os"
	"path/filepath"
)

func localApplicationData() (string, error) {
	path := os.Getenv("LOCALAPPDATA")
	if path == "" {
		return "", errors.New("cannot determine the local application-data folder")
	}
	return path, nil
}

func desktopDirectory() (string, error) {
	home, err := os.UserHomeDir()
	if err != nil || home == "" {
		return "", errors.New("cannot determine the desktop directory")
	}
	return filepath.Join(home, "Desktop"), nil
}
