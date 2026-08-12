package main

import (
	"crypto/sha256"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
)

// Register where the application already is. Do not copy it anywhere.
//
// The previous version copied the running executable into %LOCALAPPDATA% and pointed the URL
// protocol at that copy. Windows Defender classified the result as Trojan:Win32/Bearfoos.A!ml and
// deleted it, which left the protocol registered against a path that no longer existed - the
// Desktop shortcut then reported "Application Not Found" while looking perfectly intact.
//
// That detection is a machine-learning heuristic, and self-copying into AppData is the loudest
// signal in the profile it matches: an unsigned binary that duplicates itself into a user data
// directory, registers a handler aimed at the duplicate, downloads more executables and launches
// them. Every part is legitimate here and the duplication is the only part that buys nothing. The
// executable now stays wherever the player put it, and the protocol points there.
//
// Re-registering on every run makes this self-healing: if the file is moved, restored from
// quarantine, or replaced by a newer download, running it once repairs the registration.
func installCurrentApplication() (string, error) {
	source, err := os.Executable()
	if err != nil {
		return "", err
	}
	return installApplication(source, "", runCommand)
}

// installedApplicationPath reports the executable the shortcut and protocol should point at, which
// is simply where this build is running from.
func installedApplicationPath() (string, error) {
	return os.Executable()
}

// shortcutIconPath uses the running executable's own icon.
func shortcutIconPath(current string) string {
	if current != "" {
		return current
	}
	if executable, err := os.Executable(); err == nil {
		return executable
	}
	return current
}

// installApplication registers the URL protocol against an executable. The root argument is
// retained for callers that still pass a storage directory; nothing is written into it.
func installApplication(source, root string, run commandRunner) (string, error) {
	if source == "" || run == nil {
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
	if err := registerProtocol(source, run); err != nil {
		return "", err
	}
	return source, nil
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
