//go:build windows

package main

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"time"

	"golang.org/x/sys/windows/registry"
)

const steamVRAppID = "250820"

var steamVRReadyTimeout = 30 * time.Second

func prepareVRRuntime() error {
	steam, err := steamExecutable()
	if err != nil {
		return err
	}
	if err := exec.Command(steam, "-applaunch", steamVRAppID).Start(); err != nil {
		return fmt.Errorf("start SteamVR: %w", err)
	}
	deadline := time.Now().Add(steamVRReadyTimeout)
	for {
		ready, err := steamVRServerRunning()
		if err != nil {
			return err
		}
		if ready {
			return nil
		}
		if time.Now().After(deadline) {
			return errors.New("SteamVR did not become ready within 30 seconds; start SteamVR, connect Link, then retry the profile shortcut")
		}
		time.Sleep(500 * time.Millisecond)
	}
}

func steamExecutable() (string, error) {
	roots := make([]string, 0, 3)
	if key, err := registry.OpenKey(registry.CURRENT_USER, `Software\Valve\Steam`, registry.QUERY_VALUE); err == nil {
		if path, _, valueErr := key.GetStringValue("SteamPath"); valueErr == nil && path != "" {
			roots = append(roots, path)
		}
		key.Close()
	}
	for _, programFiles := range []string{os.Getenv("ProgramFiles(x86)"), os.Getenv("ProgramFiles")} {
		if programFiles != "" {
			roots = append(roots, filepath.Join(programFiles, "Steam"))
		}
	}
	for _, root := range roots {
		path := filepath.Join(root, "steam.exe")
		if info, err := os.Stat(path); err == nil && !info.IsDir() {
			return path, nil
		}
	}
	return "", errors.New("Steam is not installed or could not be located")
}
