package main

import (
	"crypto/sha256"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
)

// Install once, then run from the shortcut.
//
// The player downloads one file, runs it, and never thinks about it again: it copies itself to a
// fixed per-user location, writes the Desktop shortcut, registers the protocol the portal's buttons
// use, and launches. That single click is the product. A build that asks the player to choose a
// folder, or to move the file somewhere permanent, has failed at the thing it exists to do.
//
// The destination is %LOCALAPPDATA%\Programs, which is where Windows expects a per-user
// application to live - the same place VS Code, Discord and GitHub Desktop install - rather than
// the AppData root, which is a data directory and a favourite of things that are not applications.
// No elevation, so no UAC prompt.
const installedExecutableName = "ValheimProfileSync.exe"

func installCurrentApplication() (string, error) {
	source, err := os.Executable()
	if err != nil {
		return "", err
	}
	root, err := programsDirectory()
	if err != nil {
		return "", err
	}
	return installApplication(source, root, runCommand)
}

// programsDirectory reports %LOCALAPPDATA%\Programs\ValheimProfileSync.
func programsDirectory() (string, error) {
	localAppData, err := localApplicationData()
	if err != nil {
		return "", err
	}
	return filepath.Join(localAppData, "Programs", "ValheimProfileSync"), nil
}

// installedApplicationPath reports where the installed copy lives, without installing anything.
func installedApplicationPath() (string, error) {
	root, err := programsDirectory()
	if err != nil {
		return "", err
	}
	return filepath.Join(root, installedExecutableName), nil
}

// shortcutIconPath prefers the installed copy, so the shortcut's icon survives the download being
// cleaned up or replaced by a newer one.
// shortcutIconPath returns the path a shortcut should draw its icon from: the stable .ico beside the
// profile store when it can be written, and the running executable only as a fallback. The executable
// carries a perfectly good icon; what it does not carry is a path that survives the player replacing
// their download, which is what left shortcuts blank.
func shortcutIconPath(current string) string {
	if icon, err := stableIconPath(); err == nil {
		return icon
	}
	return shortcutIconPathFallback(current)
}

func shortcutIconPathFallback(current string) string {
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

	// Registered every run, so a copy that is moved, replaced or restored repairs its own shortcut
	// by being run once - the previous design could not recover from that at all.
	if err := registerProtocol(destination, run); err != nil {
		return "", err
	}
	return destination, nil
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

// sameFileContents reports whether two paths hold identical bytes. Still used by the sync path to
// avoid rewriting files that have not changed.
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
