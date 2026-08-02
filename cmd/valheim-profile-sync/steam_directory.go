package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

const steamDirectoryStateFilename = "steam-valheim-directory.json"

type steamDirectoryState struct {
	Schema    int    `json:"schema"`
	Directory string `json:"directory"`
}

func steamDirectoryStatePath(localAppData string) (string, error) {
	if strings.TrimSpace(localAppData) == "" {
		return "", errors.New("cannot determine the Windows local application-data folder")
	}
	return filepath.Join(filepath.Clean(localAppData), "ValheimProfileSync", steamDirectoryStateFilename), nil
}

func validateSteamValheimDirectory(directory string) (string, error) {
	directory = filepath.Clean(strings.TrimSpace(directory))
	if directory == "." || directory == "" {
		return "", errors.New("select the Steam Valheim folder that contains valheim.exe")
	}
	info, err := os.Stat(filepath.Join(directory, "valheim.exe"))
	if err != nil || info.IsDir() {
		return "", errors.New("the selected folder does not contain Steam's valheim.exe")
	}
	return directory, nil
}

func findSteamValheimDirectory(candidates []string) (string, bool) {
	seen := make(map[string]struct{}, len(candidates))
	for _, candidate := range candidates {
		candidate = filepath.Clean(strings.TrimSpace(candidate))
		if candidate == "." || candidate == "" {
			continue
		}
		key := strings.ToLower(candidate)
		if _, exists := seen[key]; exists {
			continue
		}
		seen[key] = struct{}{}
		if directory, err := validateSteamValheimDirectory(candidate); err == nil {
			return directory, true
		}
	}
	return "", false
}

func loadSteamValheimDirectory(localAppData string) (string, bool, error) {
	path, err := steamDirectoryStatePath(localAppData)
	if err != nil {
		return "", false, err
	}
	data, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return "", false, nil
	}
	if err != nil {
		return "", false, fmt.Errorf("read saved Steam Valheim folder: %w", err)
	}
	var state steamDirectoryState
	if err := json.Unmarshal(data, &state); err != nil || state.Schema != 1 {
		return "", false, errors.New("saved Steam Valheim folder is invalid")
	}
	directory, err := validateSteamValheimDirectory(state.Directory)
	if err != nil {
		return "", false, nil
	}
	return directory, true, nil
}

func saveSteamValheimDirectory(localAppData, directory string) error {
	directory, err := validateSteamValheimDirectory(directory)
	if err != nil {
		return err
	}
	path, err := steamDirectoryStatePath(localAppData)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return fmt.Errorf("create Valheim Profile Sync folder: %w", err)
	}
	data, err := json.Marshal(steamDirectoryState{Schema: 1, Directory: directory})
	if err != nil {
		return err
	}
	if err := os.WriteFile(path, data, 0o600); err != nil {
		return fmt.Errorf("save Steam Valheim folder: %w", err)
	}
	return nil
}
