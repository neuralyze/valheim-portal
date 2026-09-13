package main

import (
	"crypto/sha256"
	"embed"
	"errors"
	"os"
	"path/filepath"
)

// EverybodyShim is a BepInEx PRELOADER patcher, and it is ours rather than a mod from
// Thunderstore. Valheim 1.0 turned ZRoutedRpc.Everybody from a static field into a const
// and changed ZDO.GetSector and ZoneSystem.GetZone to return Vector2s, and the community
// patcher we otherwise rely on - Wubarrk-Valheim10Compatibility - declines the GetSector
// and GetZone bridges on principle, because they would differ from the shipped methods by
// return type alone. Its own log says so:
//
//	BLOCKED ZDO.GetSector() -> Vector2i: this would differ from the existing
//	Vector2s GetSector(...) by RETURN TYPE ALONE
//
// Without that bridge a 1.0 client throws MissingMethodException: Method not found:
// Vector2i .ZDO.GetSector() out of Minimap.Update, mid-join, and quits. Measured
// 2026-09-13: the server had the shim in BepInEx/patchers and the client did not, so the
// join reached the handshake, exchanged nothing, and the client sent a disconnect.
//
// It ships embedded in this executable rather than as an artifact because no publishing
// channel can carry a preloader patcher: the diagnostics-plugin validator permits only
// BepInEx/plugins/<dir>/<file>, the VR runtime validator is an exact path allowlist with
// no patchers entry, and the published profile definition carries Thunderstore packages
// only - custom_packages never reach a client. Those validators also run INSIDE installed
// clients, so widening one breaks every client built before the change. Embedding couples
// the shim to the client version instead, which is the honest coupling: both are our own
// compatibility layer for one game version.
//
//go:embed EverybodyShim.dll
var everybodyShimAsset embed.FS

const everybodyShimFileName = "EverybodyShim.dll"

// installEverybodyShim places the shim in the profile's patcher directory, replacing a
// copy that differs. It runs on every sync, so a profile rebuild that wipes the patcher
// directory repairs itself on the next run rather than leaving a client that cannot join.
func installEverybodyShim(root string) error {
	want, err := everybodyShimAsset.ReadFile(everybodyShimFileName)
	if err != nil {
		return err
	}
	destination := filepath.Join(root, "active", "BepInEx", "patchers", everybodyShimFileName)
	existing, err := os.ReadFile(destination)
	switch {
	case err == nil:
		if sha256.Sum256(existing) == sha256.Sum256(want) {
			return nil
		}
	case !errors.Is(err, os.ErrNotExist):
		return err
	}
	if err := os.MkdirAll(filepath.Dir(destination), 0o755); err != nil {
		return err
	}
	return writeFileAtomically(destination, want)
}
