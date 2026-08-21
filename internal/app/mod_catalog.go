package app

import (
	"context"
	"encoding/json"
	"log/slog"
	"sort"
)

// playerModCatalog is the player-visible mod list of one world, exactly as the host derives it.
//
// The derivation lives in tools/valheim_mods.py because everything it needs is on the host: the
// profile manifests of the player editions, Thunderstore's categories, and the installed plugins'
// own manifests. Nothing in this file decides which mods a player sees; it caches the answer,
// checks it has not gone stale, and joins the operator's notes onto it.
type playerModCatalog struct {
	World string `json:"world"`
	// Fingerprint is the sorted identifier@version set of the player editions, hashed. It is the
	// only thing a read compares, because mods are also changed by tools/valheim_mods.py directly
	// on the host, where the portal never sees the mutation.
	Fingerprint string `json:"fingerprint"`
	// MetadataComplete is false when Thunderstore could not be reached during the build, so the
	// descriptions came from the installed plugins' manifests or are missing. The page says so
	// rather than presenting a thin list as the whole truth.
	MetadataComplete bool             `json:"metadata_complete"`
	Editions         []string         `json:"editions"`
	Installed        int              `json:"installed"`
	Mods             []playerModEntry `json:"mods"`
}

type playerModEntry struct {
	Identifier  string   `json:"identifier"`
	Name        string   `json:"name"`
	Version     string   `json:"version"`
	Description string   `json:"description"`
	Categories  []string `json:"categories"`
	URL         string   `json:"url"`
	Source      string   `json:"source"`
	// Note is the operator's own line about this mod. It is joined in at render time from the
	// portal's own table and never stored in the cached payload: a rebuild replaces that payload
	// wholesale and must not be able to lose something a person wrote by hand.
	Note string `json:"-"`
}

// worldModSection is what the world page renders. Its three states are deliberately distinct:
// a list, a list that could not be checked for freshness, and no list at all. Collapsing them
// would mean an empty section looked the same as a working one with nothing installed.
type worldModSection struct {
	Mods             []playerModEntry
	MetadataComplete bool
	// Verified is false when the freshness check could not run and a previously cached list is
	// being served anyway. The list is still the best available answer, but the page must not
	// imply it was confirmed current.
	Verified bool
	// Unavailable is true when there is no cached list and one could not be built. The section
	// says that instead of rendering as though the world has no mods.
	Unavailable bool
}

// playerModList answers the world page: the cached list when the installed set has not moved, a
// fresh one when it has, and an honest failure when neither is possible.
//
// The cheap fingerprint read runs on every page view. That is the point of it: event invalidation
// alone would serve a stale list silently, because the host tool changes mods without the portal
// being involved at all.
func (s *Server) playerModList(ctx context.Context, world string) worldModSection {
	cachedFingerprint, payload, err := s.store.WorldModCatalog(ctx, world)
	if err != nil {
		slog.Error("cannot read the cached mod list", "world", world, "error", err)
	}
	current, checked := s.installedModFingerprint(ctx, world)
	if len(payload) > 0 && (!checked || current == cachedFingerprint) {
		if catalog, ok := decodeModCatalog(payload, world); ok {
			return s.renderableModSection(ctx, catalog, checked)
		}
		slog.Error("the cached mod list is corrupt and will be rebuilt", "world", world)
	}
	catalog, ok := s.rebuildModCatalog(ctx, world)
	if !ok {
		// Nothing fresh. A previously cached list is still worth serving - it was true when it
		// was built - so long as the page does not claim it was verified.
		if catalog, cachedOK := decodeModCatalog(payload, world); cachedOK {
			return s.renderableModSection(ctx, catalog, false)
		}
		return worldModSection{Unavailable: true}
	}
	return s.renderableModSection(ctx, catalog, true)
}

// installedModFingerprint asks the host what is installed now. The second return says whether the
// question was answered at all, which is not the same as an empty answer.
func (s *Server) installedModFingerprint(ctx context.Context, world string) (string, bool) {
	reply, err := s.agent.Run(ctx, randomID(), world, "world_mod_catalog_state")
	if err != nil || reply.Status != "succeeded" || len(reply.Data) == 0 {
		return "", false
	}
	var state struct {
		Fingerprint string `json:"fingerprint"`
	}
	if json.Unmarshal(reply.Data, &state) != nil || len(state.Fingerprint) != 64 {
		return "", false
	}
	return state.Fingerprint, true
}

// rebuildModCatalog builds the list on the host and caches it. A build that cannot be stored is
// still returned: the page the operator is looking at should not fail because a write failed.
func (s *Server) rebuildModCatalog(ctx context.Context, world string) (playerModCatalog, bool) {
	reply, err := s.agent.Run(ctx, randomID(), world, "world_mod_catalog")
	if err != nil || reply.Status != "succeeded" || len(reply.Data) == 0 {
		slog.Error("cannot build the player mod list", "world", world, "error", err)
		return playerModCatalog{}, false
	}
	catalog, ok := decodeModCatalog(reply.Data, world)
	if !ok {
		return playerModCatalog{}, false
	}
	if err := s.store.SaveWorldModCatalog(ctx, world, catalog.Fingerprint, reply.Data); err != nil {
		slog.Error("cannot cache the player mod list", "world", world, "error", err)
	}
	return catalog, true
}

// decodeModCatalog refuses a payload that is not this world's list. A catalog carrying another
// world's name is a wiring mistake, and rendering it would tell players about the wrong server.
func decodeModCatalog(payload []byte, world string) (playerModCatalog, bool) {
	if len(payload) == 0 {
		return playerModCatalog{}, false
	}
	var catalog playerModCatalog
	if json.Unmarshal(payload, &catalog) != nil || catalog.World != world || len(catalog.Fingerprint) != 64 {
		return playerModCatalog{}, false
	}
	return catalog, true
}

// renderableModSection joins the operator's notes onto the list and orders it by the name a player
// reads. Ordering here rather than on the host keeps the display order out of the cached payload,
// so changing it never requires a rebuild.
func (s *Server) renderableModSection(ctx context.Context, catalog playerModCatalog, verified bool) worldModSection {
	notes, err := s.store.ModPlayerNotes(ctx)
	if err != nil {
		// A note that cannot be read is a missing note, which the entry already renders honestly.
		slog.Error("cannot read the mod player notes", "error", err)
		notes = nil
	}
	mods := make([]playerModEntry, len(catalog.Mods))
	copy(mods, catalog.Mods)
	for index := range mods {
		mods[index].Note = notes[mods[index].Identifier]
	}
	sort.SliceStable(mods, func(a, b int) bool { return modSortKey(mods[a]) < modSortKey(mods[b]) })
	return worldModSection{Mods: mods, MetadataComplete: catalog.MetadataComplete, Verified: verified}
}

// modSortKey sorts on the displayed name, falling back to the identifier when a build could not
// resolve one, so an entry never sorts on a blank.
func modSortKey(mod playerModEntry) string {
	if mod.Name != "" {
		return lowerASCII(mod.Name)
	}
	return lowerASCII(mod.Identifier)
}

// lowerASCII folds case for ordering only. Mod names are ASCII in practice and this never leaves
// the comparison, so a locale-aware fold would buy nothing.
func lowerASCII(value string) string {
	out := []byte(value)
	for index, character := range out {
		if character >= 'A' && character <= 'Z' {
			out[index] = character + ('a' - 'A')
		}
	}
	return string(out)
}
