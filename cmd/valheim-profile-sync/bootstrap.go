package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
)

const bootstrapStateFilename = ".valheim-profile-sync-bootstrap.json"

type bootstrapFile struct {
	Name   string `json:"name"`
	SHA256 string `json:"sha256"`
}

type bootstrapState struct {
	Schema int             `json:"schema"`
	Files  []bootstrapFile `json:"files"`
}

// installBootstrap installs only Doorstop's two loader files beside Steam's
// valheim.exe. Mods and BepInEx live exclusively in the selected profile.
func installBootstrap(gameDir, active string) error {
	statePath := filepath.Join(gameDir, bootstrapStateFilename)
	previous, err := readBootstrapState(statePath)
	if err != nil {
		return err
	}
	known := make(map[string]string, len(previous.Files))
	for _, file := range previous.Files {
		known[file.Name] = file.SHA256
	}

	files := make([]bootstrapFile, 0, 2)
	for _, name := range []string{"winhttp.dll", "doorstop_config.ini"} {
		source := filepath.Join(active, name)
		info, err := os.Stat(source)
		if err != nil || !info.Mode().IsRegular() {
			return fmt.Errorf("selected profile is missing required loader file %q", name)
		}
		digest, err := sha256File(source)
		if err != nil {
			return err
		}
		destination := filepath.Join(gameDir, name)
		if existing, statErr := os.Stat(destination); statErr == nil && existing.Mode().IsRegular() {
			existingDigest, err := sha256File(destination)
			if err != nil {
				return err
			}
			if existingDigest != digest && known[name] != existingDigest {
				return fmt.Errorf("refusing to replace unmanaged Valheim loader file %q", name)
			}
		} else if statErr != nil && !errors.Is(statErr, os.ErrNotExist) {
			return statErr
		}
		if err := copyFileAtomically(source, destination); err != nil {
			return err
		}
		files = append(files, bootstrapFile{Name: name, SHA256: digest})
	}
	return writeJSONAtomically(statePath, bootstrapState{Schema: 1, Files: files})
}

func readBootstrapState(path string) (bootstrapState, error) {
	data, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return bootstrapState{Schema: 1}, nil
	}
	if err != nil {
		return bootstrapState{}, err
	}
	var state bootstrapState
	if err := json.Unmarshal(data, &state); err != nil || state.Schema != 1 {
		return bootstrapState{}, errors.New("invalid Valheim Profile Sync bootstrap state")
	}
	for _, file := range state.Files {
		if (file.Name != "winhttp.dll" && file.Name != "doorstop_config.ini") || !validSHA256(file.SHA256) {
			return bootstrapState{}, errors.New("invalid Valheim Profile Sync bootstrap state")
		}
	}
	return state, nil
}

func copyFileAtomically(source, destination string) error {
	in, err := os.Open(source)
	if err != nil {
		return err
	}
	defer in.Close()
	temporary, err := os.CreateTemp(filepath.Dir(destination), ".valheim-profile-sync-bootstrap-")
	if err != nil {
		return err
	}
	name := temporary.Name()
	defer os.Remove(name)
	if _, err := io.Copy(temporary, in); err != nil {
		temporary.Close()
		return err
	}
	if err := temporary.Chmod(0o600); err != nil {
		temporary.Close()
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	return replaceFile(name, destination)
}

func sha256File(path string) (string, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer file.Close()
	hash := sha256.New()
	if _, err := io.Copy(hash, file); err != nil {
		return "", err
	}
	return hex.EncodeToString(hash.Sum(nil)), nil
}
