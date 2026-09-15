package main

import (
	"errors"
	"os"
	"path/filepath"
)

// The package cache is shared by every profile in one installation, at
// <storage>/packages, rather than kept per profile at <storage>/profiles/<edition>/packages.
//
// A world publishes several editions - Ulfsland has four - and they overlap almost
// completely: measured 2026-09-14, all four Ulfsland editions pin
// 95Shade-CarryWeightSkill-1.0.1.zip, and three of them pin around a hundred packages
// each. A per-profile cache therefore downloaded and stored the same bytes four times,
// and that is exactly how the operator hit a dead Thunderstore edge: the flat edition
// had already fetched that file successfully, but the freshly installed admin edition
// could not see it and had to fetch all 105 packages itself.
//
// Sharing is safe because a package is content-identified: the definition carries a
// SHA-256 and a size for every package, and ensureCachedPackage verifies BOTH on a cache
// HIT, not only after a download. That check is what makes a shared store no more
// dangerous than a private one - a corrupt or truncated entry is re-fetched by whichever
// profile notices, instead of poisoning every profile that reads it.
func sharedPackageCache(localAppData string) (string, error) {
	storage, _, err := loadProfileStorageDirectory(localAppData)
	if err != nil {
		return "", err
	}
	return filepath.Join(storage, "packages"), nil
}

// adoptPackageCache moves a profile's private cache into the shared store and reports how
// many archives it took over.
//
// Migration matters more than tidiness here: a player upgrading to this client already has
// up to four private caches holding real, verified bytes, and orphaning them would make the
// first sync after the upgrade re-download a hundred packages to GAIN a cache - the exact
// download storm this change exists to remove.
//
// A hard link is tried first: the legacy directory and the shared store are always on the
// same volume (both are under the one storage root), NTFS supports hard links, and linking
// leaves the bytes reachable under both names, so an interruption between the link and the
// removal below loses nothing. Rename is the fallback for a filesystem without hard links -
// FAT32 or exFAT on a chosen storage folder - and a copy is the last resort, which is still
// cheaper than a download. A file already present in the shared store is simply dropped,
// because the shared copy is verified on use and a second copy of the same name adds nothing.
func adoptPackageCache(legacy, shared string) (int, error) {
	entries, err := os.ReadDir(legacy)
	if errors.Is(err, os.ErrNotExist) {
		return 0, nil
	}
	if err != nil {
		return 0, err
	}
	if err := os.MkdirAll(shared, 0o700); err != nil {
		return 0, err
	}
	adopted := 0
	for _, entry := range entries {
		if !entry.Type().IsRegular() {
			continue
		}
		source := filepath.Join(legacy, entry.Name())
		target := filepath.Join(shared, entry.Name())
		if _, err := os.Lstat(target); err == nil {
			continue
		}
		if err := os.Link(source, target); err != nil {
			if err := os.Rename(source, target); err != nil {
				if err := copyFileAtomically(source, target); err != nil {
					return adopted, err
				}
			}
		}
		adopted++
	}
	if err := os.RemoveAll(legacy); err != nil {
		return adopted, err
	}
	return adopted, nil
}

// referencedPackageFilenames is every package filename any installed profile still needs.
//
// Pruning a SHARED store against one profile's package list would delete the other
// editions' archives on every sync, turning the disk saving into a download storm - so the
// question the shared store has to ask is "does any installed profile reference this?".
// The answer is assembled from the state.json of every profile in the store, plus the list
// being installed right now, which is not on disk yet the first time a profile is created.
//
// Any unreadable or unparsable state file gives up and returns an error instead of a
// partial answer: the caller treats that as "do not prune", and a deferred cleanup is
// always better than deleting an archive a profile still wants.
func referencedPackageFilenames(localAppData string, current []packageDefinition) (map[string]struct{}, error) {
	storage, _, err := loadProfileStorageDirectory(localAppData)
	if err != nil {
		return nil, err
	}
	keep := make(map[string]struct{}, len(current))
	for _, packageInfo := range current {
		keep[packageInfo.Filename] = struct{}{}
	}
	profiles := filepath.Join(storage, "profiles")
	entries, err := os.ReadDir(profiles)
	if errors.Is(err, os.ErrNotExist) {
		return keep, nil
	}
	if err != nil {
		return nil, err
	}
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		state, present, err := loadProfileState(filepath.Join(profiles, entry.Name()))
		if err != nil {
			return nil, err
		}
		if !present {
			continue
		}
		for _, packageInfo := range state.Packages {
			keep[packageInfo.Filename] = struct{}{}
		}
	}
	return keep, nil
}

func prunePackageCache(cache string, keep map[string]struct{}) error {
	entries, err := os.ReadDir(cache)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	for _, entry := range entries {
		if _, found := keep[entry.Name()]; !found {
			if err := os.RemoveAll(filepath.Join(cache, entry.Name())); err != nil {
				return err
			}
		}
	}
	return nil
}
