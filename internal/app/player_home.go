package app

import "slices"

// playerHomeWorld is one world tile on the signed-in player home page.
// ProfileCount is how many distinct profiles that world currently publishes.
// The tile deliberately does not name them: "flat" covers two profiles that
// differ in whether they install ValheimVR, and only the world page has read
// the definitions that can tell them apart.
type playerHomeWorld struct {
	PublicWorld
	ProfileCount int
}

// playerHomeWorlds counts each world's published profiles for its tile.
// Releases must already be filtered to the worlds this player may see.
func playerHomeWorlds(worlds []PublicWorld, releases []Release) []playerHomeWorld {
	profiles := make(map[string][]string, len(worlds))
	for _, release := range releases {
		if release.Profile == "" {
			continue
		}
		byWorld := profiles[release.World]
		if !slices.Contains(byWorld, release.Profile) {
			profiles[release.World] = append(byWorld, release.Profile)
		}
	}
	out := make([]playerHomeWorld, 0, len(worlds))
	for _, world := range worlds {
		out = append(out, playerHomeWorld{PublicWorld: world, ProfileCount: len(profiles[world.Name])})
	}
	return out
}
