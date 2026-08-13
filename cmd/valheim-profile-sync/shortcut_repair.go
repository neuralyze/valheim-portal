package main

import (
	"errors"
	"os"
	"path/filepath"
	"sort"
)

// Recreating a Desktop shortcut is a button, not a guess.
//
// The sync writes a shortcut once and records a stamp beside the profile, so a player who deletes
// theirs never gets it back. Recreating it automatically is worse: shortcuts get filed into folders,
// and anything that checks only the Desktop would keep making duplicates of one that was simply
// moved. Neither behaviour can be right without knowing what the player wants, so the player says.
//
// Only the Desktop is written and only the Desktop is read. Nothing is searched.
func recreateDesktopShortcut() (string, error) {
	request, err := mostRecentProfileRequest()
	if err != nil {
		return "", err
	}
	desktop, err := desktopDirectory()
	if err != nil {
		return "", err
	}
	executable, err := os.Executable()
	if err != nil {
		return "", err
	}
	return writeShortcut(desktop, request, shortcutIconPath(executable))
}

// mostRecentProfileRequest reads the profile the player used last, from the state file each sync
// already writes. Reading it back means the button works on a cold start, which is exactly when a
// missing shortcut is noticed.
func mostRecentProfileRequest() (profileRequest, error) {
	localAppData, err := localApplicationData()
	if err != nil {
		return profileRequest{}, err
	}
	storage, _, err := loadProfileStorageDirectory(localAppData)
	if err != nil {
		return profileRequest{}, err
	}
	entries, err := os.ReadDir(storage)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return profileRequest{}, errors.New("no profile has been installed yet, so there is no shortcut to create")
		}
		return profileRequest{}, err
	}

	type candidate struct {
		request  profileRequest
		modified int64
	}
	var found []candidate
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		root := filepath.Join(storage, entry.Name())
		state, present, stateErr := loadProfileState(root)
		if stateErr != nil || !present {
			continue
		}
		request := profileRequest{World: state.World, Profile: state.Profile, ClientType: state.ClientType}
		if request.validate() != nil {
			continue
		}
		info, statErr := os.Stat(filepath.Join(root, stateFilename))
		if statErr != nil {
			continue
		}
		found = append(found, candidate{request: request, modified: info.ModTime().UnixNano()})
	}
	if len(found) == 0 {
		return profileRequest{}, errors.New("no installed profile was found, so there is no shortcut to create")
	}
	sort.Slice(found, func(i, j int) bool { return found[i].modified > found[j].modified })
	return found[0].request, nil
}
