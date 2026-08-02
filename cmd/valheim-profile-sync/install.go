package main

import (
	"crypto/sha256"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
)

const installedExecutableName = "ValheimProfileSync.exe"

func installCurrentApplication() (string, error) {
	source, err := os.Executable()
	if err != nil {
		return "", err
	}
	localAppData, err := localApplicationData()
	if err != nil {
		return "", err
	}
	root, _, err := loadProfileStorageDirectory(localAppData)
	if err != nil {
		return "", err
	}
	return installApplication(source, root, runCommand)
}

// installedApplicationPath reports where the application keeps its own copy,
// without installing anything. The Desktop shortcut records this rather than
// os.Executable() so its icon survives the downloaded copy being replaced or
// cleaned up.
func installedApplicationPath() (string, error) {
	localAppData, err := localApplicationData()
	if err != nil {
		return "", err
	}
	root, _, err := loadProfileStorageDirectory(localAppData)
	if err != nil {
		return "", err
	}
	return filepath.Join(root, installedExecutableName), nil
}

// shortcutIconPath prefers the installed copy for the shortcut's icon. A shortcut
// created while running straight from a browser download points its icon at that
// download, so the art disappears as soon as the file is cleaned up or superseded by
// a newer download - which looks exactly like the shortcut being deleted. The
// shortcut still launches either way, because it stores a protocol URL rather than a
// path to the executable.
func shortcutIconPath(current string) string {
	installed, err := installedApplicationPath()
	if err != nil {
		return current
	}
	if info, statErr := os.Stat(installed); statErr == nil && info.Mode().IsRegular() {
		return installed
	}
	return current
}

func installApplication(source, root string, run commandRunner) (string, error) {
	if source == "" || root == "" || run == nil {
		return "", errors.New("cannot install Valheim Profile Sync")
	}
	source, err := filepath.Abs(source)
	if err != nil {
		return "", err
	}
	info, err := os.Stat(source)
	if err != nil || !info.Mode().IsRegular() {
		return "", errors.New("the selected Valheim Profile Sync executable is unavailable")
	}
	root, err = filepath.Abs(root)
	if err != nil {
		return "", err
	}
	if err := os.MkdirAll(root, 0o700); err != nil {
		return "", err
	}
	destination := filepath.Join(root, installedExecutableName)
	if filepath.Clean(source) != filepath.Clean(destination) {
		same, err := sameFileContents(source, destination)
		if err != nil {
			return "", err
		}
		if !same {
			if err := copyFileAtomically(source, destination); err != nil {
				return "", fmt.Errorf("install Valheim Profile Sync: %w", err)
			}
		}
	}
	if err := registerProtocol(destination, run); err != nil {
		return "", err
	}
	return destination, nil
}

func sameFileContents(first, second string) (bool, error) {
	firstInfo, err := os.Stat(first)
	if err != nil {
		return false, err
	}
	secondInfo, err := os.Stat(second)
	if errors.Is(err, os.ErrNotExist) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	if !secondInfo.Mode().IsRegular() || firstInfo.Size() != secondInfo.Size() {
		return false, nil
	}
	firstDigest, err := fileDigest(first)
	if err != nil {
		return false, err
	}
	secondDigest, err := fileDigest(second)
	if err != nil {
		return false, err
	}
	return firstDigest == secondDigest, nil
}

func fileDigest(path string) (string, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer file.Close()
	hash := sha256.New()
	if _, err := io.Copy(hash, file); err != nil {
		return "", err
	}
	return fmt.Sprintf("%x", hash.Sum(nil)), nil
}
