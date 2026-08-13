package main

import (
	"errors"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// Recreating Desktop shortcuts is a button, not a guess.
//
// The sync writes a shortcut once and stamps the profile, so a player who deleted theirs never got
// it back. Recreating one automatically is worse: shortcuts get filed into folders, and anything
// that watches the Desktop would keep duplicating one that had merely been moved. Neither behaviour
// is decidable without knowing what the player wants, so the player asks.
//
// It writes exactly one: the profile this session opened, or - when the app was started cold, with
// no profile link - the one most recently used, which is the profile the player is playing. Writing
// one per installed profile littered a Desktop with seven of them. Only the Desktop is written and
// nothing anywhere is searched.
func recreateDesktopShortcut(current *profileRequest) (string, error) {
	request, err := shortcutSubject(current)
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

// shortcutSubject prefers the profile in hand. A player pressing the button mid-session means the
// one they are using; a player who opened the app to fix a missing shortcut has no session, so the
// most recently synced profile is the same answer by another route.
func shortcutSubject(current *profileRequest) (profileRequest, error) {
	if current != nil && current.validate() == nil {
		return *current, nil
	}
	requests, err := installedProfileRequests()
	if err != nil {
		return profileRequest{}, err
	}
	return requests[0], nil
}

// installedProfileRequests reads every profile the player has installed, from the state file each
// sync already writes. Reading them back means the button works on a cold start, which is exactly
// when a missing shortcut is noticed.
func installedProfileRequests() ([]profileRequest, error) {
	localAppData, err := localApplicationData()
	if err != nil {
		return nil, err
	}
	storage, _, err := loadProfileStorageDirectory(localAppData)
	if err != nil {
		return nil, err
	}
	// The profiles sit under <storage>/profiles, which is what profileRoot builds. Reading the
	// storage root itself finds only that folder and the Steam directory record, which is why the
	// first version of this button reported no installed profile on a machine that had seven.
	entries, err := os.ReadDir(filepath.Join(storage, "profiles"))
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil, errors.New("no profile has been installed yet, so there is no shortcut to create")
		}
		return nil, err
	}
	var requests []profileRequest
	used := map[string]int64{}
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		state, present, stateErr := loadProfileState(filepath.Join(storage, "profiles", entry.Name()))
		if stateErr != nil || !present {
			continue
		}
		portal := profilePortal(state)
		if portal == nil {
			continue
		}
		request := profileRequest{Portal: portal, World: state.World, Profile: state.Profile, ClientType: state.ClientType}
		if request.validate() != nil {
			continue
		}
		if info, statErr := os.Stat(filepath.Join(storage, "profiles", entry.Name(), stateFilename)); statErr == nil {
			used[request.Profile] = info.ModTime().UnixNano()
		}
		requests = append(requests, request)
	}
	if len(requests) == 0 {
		return nil, errors.New("no installed profile was found, so there is no shortcut to create")
	}
	sort.Slice(requests, func(i, j int) bool { return used[requests[i].Profile] > used[requests[j].Profile] })
	return requests, nil
}

// profilePortal reports which portal installed a profile. Newer states record it outright; the ones
// already on disk when this was written only carry it inside the diagnostics endpoint, so that URL
// is trimmed back to its origin rather than asking the player to reinstall to get a shortcut.
func profilePortal(state profileState) *url.URL {
	if trimmed := strings.TrimSpace(state.Portal); trimmed != "" {
		if parsed, err := parsePortalURL(trimmed); err == nil {
			return parsed
		}
	}
	endpoint := strings.TrimSpace(state.DiagnosticsEndpoint)
	if endpoint == "" {
		return nil
	}
	parsed, err := url.Parse(endpoint)
	if err != nil || parsed.Scheme == "" || parsed.Host == "" {
		return nil
	}
	origin, err := parsePortalURL(parsed.Scheme + "://" + parsed.Host)
	if err != nil {
		return nil
	}
	return origin
}
