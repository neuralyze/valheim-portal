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
// It writes one for every installed profile, because a player with several has no way to say which
// one they lost, and picking "the most recent" answers a question they did not ask. Only the
// Desktop is written and nothing anywhere is searched.
func recreateDesktopShortcuts() ([]string, error) {
	requests, err := installedProfileRequests()
	if err != nil {
		return nil, err
	}
	desktop, err := desktopDirectory()
	if err != nil {
		return nil, err
	}
	executable, err := os.Executable()
	if err != nil {
		return nil, err
	}
	icon := shortcutIconPath(executable)
	written := make([]string, 0, len(requests))
	for _, request := range requests {
		path, writeErr := writeShortcut(desktop, request, icon)
		if writeErr != nil {
			return written, writeErr
		}
		written = append(written, filepath.Base(path))
	}
	return written, nil
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
		requests = append(requests, request)
	}
	if len(requests) == 0 {
		return nil, errors.New("no installed profile was found, so there is no shortcut to create")
	}
	sort.Slice(requests, func(i, j int) bool { return requests[i].Profile < requests[j].Profile })
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
