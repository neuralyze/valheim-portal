package main

import (
	"crypto/sha256"
	"embed"
	"errors"
	"os"
	"path/filepath"
)

// HammerFix is a BepInEx PLUGIN of ours - not a patcher, and not a mod from Thunderstore.
// It repairs the hammer build menu on Valheim 1.0, where the whole availability pass is
// aborted for every mod by one mod's copy of a bundled library.
//
// MEASURED (Mono.Cecil over the live Ulfsland plugin set, field layout read from that
// install's assembly_valheim.dll): Valheim 1.0 renamed PieceTable's
// List<List<Piece>> m_availablePieces to m_availablePiecesByCategory and gave the old NAME
// to a new HashSet<Piece>. Ten loaded assemblies define a PiecePrefabManager, each of whose
// .cctor patches PieceTable.UpdateAvailable; four of those copies - RavenwoodRestorations,
// Basements, OdinUndercroft, OdinsHorsePen - still read the pre-1.0 field and throw
//
//	System.MissingFieldException: Field not found:
//	  List<List<Piece>> PieceTable.m_availablePieces
//
// A Harmony prefix chain runs every prefix, so the broken one at patch index 2 aborts the
// call for all of them - the four correct copies and the original included. Result:
// m_availablePiecesByCategory.Count == 0 on all eight piece tables, and nothing is ever
// available to build. HammerFix classifies each installed patch by ASKING THE RUNTIME
// (invoking it once against a throwaway PieceTable it owns), Harmony.Unpatches only the
// member-resolution failures by their exact MethodInfo, then does the sizing itself at
// Priority.First. The full diagnosis is at the top of tools/hammerfix/HammerFix.cs.
//
// It ships embedded in this executable for the same reason EverybodyShim does, one line
// over in everybody_shim.go: no publishing channel can carry a DLL of ours. The published
// profile definition carries Thunderstore packages ONLY, client_only_packages takes a
// Thunderstore identifier plus a publisher SHA256 with no URL for a file of ours, and
// sync.go's packageRepositoryURL is hardcoded to Thunderstore. Unlike the embedded
// ServerCharacters archive, HammerFix stands in for no Thunderstore identity and is gated
// on no manifest selecting it: it belongs on EVERY client regardless of profile, exactly
// like the shim.
//
// It declares NO BepInDependency and needs no load order. It patches by TYPE NAME across
// loaded assemblies, and its sweep is anchored on Player.UpdateAvailablePiecesList, which
// cannot run until a local Player exists - long after every .cctor. MEASURED on a bare boot
// (BepInEx + HammerFix only): 0 PiecePrefabManager types found, 0 removals, 0 errors, pass
// OK on all four tables. It is safe where the broken copies are absent.
//
//go:embed HammerFix.dll
var hammerFixAsset embed.FS

const hammerFixFileName = "HammerFix.dll"

// installHammerFix places the plugin in the profile's plugin directory, replacing a copy
// that differs. Same shape as installEverybodyShim: it runs on every sync, so a profile
// rebuild that wipes the tree repairs itself on the next run, a rebuilt DLL replaces a
// stale one, and an identical one writes nothing.
//
// BepInEx/plugins/HammerFix/HammerFix.dll - a PLUGIN path, because HammerFix needs Harmony
// and live runtime state and is not a Cecil preloader. Deliberately no patchers/
// subdirectory anywhere under it: hoistPackagePatchers moves plugins/<pkg>/patchers/*.dll
// up into BepInEx/patchers, which for this plugin would be a silent misinstall.
func installHammerFix(root string) error {
	want, err := hammerFixAsset.ReadFile(hammerFixFileName)
	if err != nil {
		return err
	}
	destination := filepath.Join(root, "active", "BepInEx", "plugins", "HammerFix", hammerFixFileName)
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
