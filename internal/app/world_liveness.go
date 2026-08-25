package app

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// The dedicated-server container runs `valheim-status --update`, which queries the
// game over Steam A2S every 10 seconds and writes the answer to htdocs/status.json.
// That file is bind-mounted onto the world root the portal already reads, so
// liveness costs one small file read and needs no agent round-trip.
const livenessStaleAfter = time.Minute

// worldLiveness is what the game server itself last reported.
//
// Ready means the server answered an A2S query: the process is up, the world is
// loaded, and it is accepting players. A container that is running but still
// loading its mods reports an error instead, which is the distinction the portal
// has to make - "the container exists" is not "players can join".
type worldLiveness struct {
	Ready       bool
	PlayerCount int
	GameVersion string
	Observed    time.Time
	Known       bool
}

type statusReport struct {
	LastStatusUpdate time.Time `json:"last_status_update"`
	Error            *string   `json:"error"`
	PlayerCount      int       `json:"player_count"`
	Keywords         string    `json:"keywords"`
}

// readWorldLiveness reports what the world's own status file says. Known is false
// when there is nothing to read, which is the normal state for a world that has
// never been started.
//
// Three independent things each mean "not ready", and all of them matter:
// a missing file (never started), a non-null error (up but not answering), and a
// timestamp that stopped advancing (the container died, leaving its last healthy
// report behind - the case that would otherwise keep a dead server showing green).
func readWorldLiveness(sourceRoot, world string, now time.Time) worldLiveness {
	if sourceRoot == "" || !validWorld(world) {
		return worldLiveness{}
	}
	raw, err := os.ReadFile(filepath.Join(sourceRoot, world, "data", "htdocs", "status.json"))
	if err != nil {
		return worldLiveness{}
	}
	var report statusReport
	if err := json.Unmarshal(raw, &report); err != nil {
		return worldLiveness{}
	}
	live := worldLiveness{
		Known:       true,
		PlayerCount: report.PlayerCount,
		GameVersion: gameVersionFromKeywords(report.Keywords),
		Observed:    report.LastStatusUpdate,
	}
	if report.Error != nil && *report.Error != "" {
		return live
	}
	if report.LastStatusUpdate.IsZero() || now.Sub(report.LastStatusUpdate) > livenessStaleAfter {
		return live
	}
	live.Ready = true
	return live
}

// gameVersionFromKeywords pulls the running build out of the A2S keywords field,
// which looks like "g=0.221.12,n=36,m=". Reporting the version the server actually
// runs beats the one an operator typed when the world was registered.
func gameVersionFromKeywords(keywords string) string {
	for _, field := range strings.Split(keywords, ",") {
		if version, ok := strings.CutPrefix(strings.TrimSpace(field), "g="); ok {
			return strings.TrimSpace(version)
		}
	}
	return ""
}

// withLiveStatus replaces the stored status with what the server is actually doing, and
// marks the world if an admin-mode maintenance window is open on it.
//
// Only maintenance survives: it is an editorial statement ("we are working on it,
// do not try") that no probe can infer. Every other stored value was a human's
// guess about a running process, and the process can answer for itself.
//
// The admin-mode mark is stamped here, above the maintenance shortcut, because this is the
// one function every world listing already passes through - the admin home, the player
// home, one world's page and the releases JSON. A world in admin mode kicks every player
// who joins it, and a warning that has to be pasted into four templates is a warning that
// will be missing from the fifth. `windows` is the whole open set, read once per render.
func (s *Server) withLiveStatus(info PublicWorld, now time.Time, windows map[string]WorldAdminMode) PublicWorld {
	if window, open := windows[info.Name]; open {
		info.AdminMode, info.AdminModeSince, info.AdminModeBy = true, window.Since, window.Actor
	}
	if info.Status == "maintenance" {
		return info
	}
	live := readWorldLiveness(s.cfg.MapSourceRoot, info.Name, now)
	if live.Ready {
		info.Status = "online"
		if live.GameVersion != "" {
			info.ServerVersion = live.GameVersion
		}
		return info
	}
	info.Status = "offline"
	return info
}
