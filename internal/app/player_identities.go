package app

import (
	"encoding/json"
	"os"
	"path/filepath"
)

// Names the game itself reported for the player ids stamped on pieces.
//
// The map could show that somebody built a base and never who, because a dedicated server never
// learns a character's name: the name lives in the player's own character file. Inside the running
// game both facts sit on the same object, so a server-side plugin - tools/player-identities - writes
// the pairing down as the players connect. This reads it.
//
// These names are evidence, not guesses: the plugin records a pair only when the game supplies the id
// and the name for the same character. An operator's own label still wins, because they may want to
// call somebody something other than their current character name, and because a character can be
// renamed while the id stays put.

type playerIdentityFile struct {
	Schema  int `json:"schema"`
	Players []struct {
		PlayerID int64  `json:"player_id"`
		Name     string `json:"name"`
	} `json:"players"`
}

// reportedPlayerNames reads the identities the server plugin recorded for one world. A missing file is
// not an error: it means the plugin is not deployed there yet, and the legend falls back to the id.
func (s *Server) reportedPlayerNames(world string) map[int64]string {
	if !validWorld(world) {
		return nil
	}
	path := filepath.Join(s.cfg.MapSourceRoot, world, "config_merged", "player_identities.json")
	raw, err := os.ReadFile(filepath.Clean(path))
	if err != nil {
		return nil
	}
	var file playerIdentityFile
	if err := json.Unmarshal(raw, &file); err != nil {
		return nil
	}
	names := make(map[int64]string, len(file.Players))
	for _, player := range file.Players {
		// Player id 0 is the absence of a stamp, never a person, and an empty name is nothing to show.
		if player.PlayerID == 0 || player.Name == "" {
			continue
		}
		names[player.PlayerID] = player.Name
	}
	return names
}
