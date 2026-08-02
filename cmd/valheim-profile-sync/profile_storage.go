package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

const profileStorageStateFilename = "profile-storage.json"

type profileStorageState struct {
	Schema    int    `json:"schema"`
	Directory string `json:"directory"`
}

func profileStorageStatePath(localAppData string) (string, error) {
	if strings.TrimSpace(localAppData) == "" {
		return "", errors.New("cannot determine the Windows local application-data folder")
	}
	return filepath.Join(filepath.Clean(localAppData), "ValheimProfileSync", profileStorageStateFilename), nil
}

func defaultProfileStorageDirectory(localAppData string) string {
	return filepath.Join(filepath.Clean(localAppData), "ValheimProfileSync")
}

func loadProfileStorageDirectory(localAppData string) (string, bool, error) {
	path, err := profileStorageStatePath(localAppData)
	if err != nil {
		return "", false, err
	}
	data, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return defaultProfileStorageDirectory(localAppData), false, nil
	}
	if err != nil {
		return "", false, fmt.Errorf("read profile storage location: %w", err)
	}
	var state profileStorageState
	if err := json.Unmarshal(data, &state); err != nil || state.Schema != 1 {
		return "", false, errors.New("saved profile storage location is invalid")
	}
	directory, err := filepath.Abs(strings.TrimSpace(state.Directory))
	if err != nil || directory == "" || directory == "." {
		return "", false, errors.New("saved profile storage location is invalid")
	}
	return filepath.Clean(directory), true, nil
}

func saveProfileStorageDirectory(localAppData, directory string) (string, error) {
	directory, err := filepath.Abs(strings.TrimSpace(directory))
	if err != nil || directory == "" || directory == "." {
		return "", errors.New("choose a folder for Valheim Profile Sync profiles")
	}
	directory = filepath.Clean(directory)
	if err := os.MkdirAll(directory, 0o700); err != nil {
		return "", fmt.Errorf("create profile storage folder: %w", err)
	}
	path, err := profileStorageStatePath(localAppData)
	if err != nil {
		return "", err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return "", err
	}
	data, err := json.Marshal(profileStorageState{Schema: 1, Directory: directory})
	if err != nil {
		return "", err
	}
	if err := os.WriteFile(path, data, 0o600); err != nil {
		return "", fmt.Errorf("save profile storage location: %w", err)
	}
	return directory, nil
}
