//go:build windows

package main

import (
	"fmt"
	"strings"
)

// processRunning reports whether a process with the given image name is live.
//
// tasklist is used rather than the Win32 snapshot API because the SteamVR check
// already relies on it, so there is one mechanism to reason about rather than two.
func processRunning(imageName string) (bool, error) {
	output, err := hiddenCommand("tasklist", "/FI", "IMAGENAME eq "+imageName, "/FO", "CSV", "/NH").Output()
	if err != nil {
		return false, fmt.Errorf("check whether %s is running: %w", imageName, err)
	}
	return strings.Contains(strings.ToLower(string(output)), `"`+strings.ToLower(imageName)+`"`), nil
}

func steamVRServerRunning() (bool, error) {
	return processRunning("vrserver.exe")
}

func checkValheimRunning() (bool, error) {
	return processRunning("valheim.exe")
}
