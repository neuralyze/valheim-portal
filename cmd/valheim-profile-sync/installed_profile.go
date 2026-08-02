package main

import (
	"os"
	"path/filepath"
	"strings"
	"time"
)

// noInstalledProfileText is the honest placeholder for the version line. It is
// used for every degenerate case — no storage directory, no state file, corrupt
// state, empty release id — so the player never sees a blank line or "unknown".
const noInstalledProfileText = "No profile installed yet"

// describeInstalledProfile renders the single line shown at the top of the
// player window, for example:
//
//	Installed: Asgard · asgard-vr · vr · 2.1.73
//
// It is deliberately pure so the display text can be tested without Windows.
func describeInstalledProfile(state profileState, found bool) string {
	if !found || strings.TrimSpace(state.ReleaseID) == "" {
		return noInstalledProfileText
	}
	parts := make([]string, 0, 4)
	for _, field := range [...]string{state.World, state.Profile, state.ClientType, state.ReleaseID} {
		if trimmed := strings.TrimSpace(field); trimmed != "" {
			parts = append(parts, trimmed)
		}
	}
	return "Installed: " + strings.Join(parts, " · ")
}

// currentInstalledProfileSummary describes the most recently installed profile
// found in the configured profile store. Every failure degrades to
// noInstalledProfileText: the version line must never block or crash the UI.
func currentInstalledProfileSummary() string {
	localAppData, err := localApplicationData()
	if err != nil {
		return noInstalledProfileText
	}
	return installedProfileSummary(localAppData)
}

func installedProfileSummary(localAppData string) string {
	state, found := newestInstalledProfileState(localAppData)
	return describeInstalledProfile(state, found)
}

// newestInstalledProfileState picks the profile whose state file was written
// last, because a player may keep several worlds or client types installed and
// the version line has room for exactly one of them.
func newestInstalledProfileState(localAppData string) (profileState, bool) {
	storage, _, err := loadProfileStorageDirectory(localAppData)
	if err != nil {
		return profileState{}, false
	}
	profiles := filepath.Join(storage, "profiles")
	entries, err := os.ReadDir(profiles)
	if err != nil {
		return profileState{}, false
	}
	var (
		newest   profileState
		newestAt time.Time
		found    bool
	)
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		root := filepath.Join(profiles, entry.Name())
		state, present, err := loadProfileState(root)
		if err != nil || !present || strings.TrimSpace(state.ReleaseID) == "" {
			continue
		}
		info, err := os.Stat(filepath.Join(root, stateFilename))
		if err != nil {
			continue
		}
		if found && !info.ModTime().After(newestAt) {
			continue
		}
		newest, newestAt, found = state, info.ModTime(), true
	}
	return newest, found
}
