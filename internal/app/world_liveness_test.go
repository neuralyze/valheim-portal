package app

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func writeStatusJSON(t *testing.T, root, world, body string) {
	t.Helper()
	dir := filepath.Join(root, world, "data", "htdocs")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "status.json"), []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

func liveStatusReport(observed time.Time, failure string) string {
	errField := "null"
	if failure != "" {
		errField = fmt.Sprintf("%q", failure)
	}
	return fmt.Sprintf(`{"last_status_update": %q, "error": %s, "server_name": "test",
		"player_count": 3, "keywords": "g=0.221.12,n=36,m=", "players": []}`,
		observed.Format(time.RFC3339Nano), errField)
}

// A server is only "online" when it answered a game-protocol query. Each of these
// is a distinct way that can fail, and every one of them used to render green.
func TestWorldLivenessDistinguishesReadyFromMerelyPresent(t *testing.T) {
	root := t.TempDir()
	now := time.Now()

	writeStatusJSON(t, root, "Ready", liveStatusReport(now.Add(-5*time.Second), ""))
	// Container up, game not answering yet: mods still loading, or a broken startup.
	writeStatusJSON(t, root, "Loading", liveStatusReport(now.Add(-5*time.Second), "TimeoutError('timed out')"))
	// The container died and left its last healthy report on disk. This is the case
	// that kept a dead server showing green.
	writeStatusJSON(t, root, "Abandoned", liveStatusReport(now.Add(-30*time.Minute), ""))

	for _, tc := range []struct {
		world string
		ready bool
	}{
		{"Ready", true},
		{"Loading", false},
		{"Abandoned", false},
		{"NeverStarted", false},
	} {
		live := readWorldLiveness(root, tc.world, now)
		if live.Ready != tc.ready {
			t.Errorf("%s: ready = %v, want %v", tc.world, live.Ready, tc.ready)
		}
	}

	if live := readWorldLiveness(root, "Ready", now); live.GameVersion != "0.221.12" || live.PlayerCount != 3 {
		t.Fatalf("running build and player count must come from the server: %#v", live)
	}
	if live := readWorldLiveness(root, "NeverStarted", now); live.Known {
		t.Fatal("a world with no status file has reported nothing")
	}
}

func TestWorldLivenessIgnoresUnreadableAndHostileNames(t *testing.T) {
	root := t.TempDir()
	writeStatusJSON(t, root, "Broken", "{not json")
	if readWorldLiveness(root, "Broken", time.Now()).Ready {
		t.Fatal("unparseable status must not read as ready")
	}
	if readWorldLiveness(root, "../../etc", time.Now()).Known {
		t.Fatal("a path-traversing world name must be rejected before any read")
	}
	if readWorldLiveness("", "Ready", time.Now()).Known {
		t.Fatal("no source root means nothing can be known")
	}
}

// Maintenance is an editorial statement no probe can infer, so it is the one stored
// value that survives. Everything else is the server's own answer.
func TestLiveStatusOverridesStoredLabelExceptMaintenance(t *testing.T) {
	server := testServer(t)
	root := t.TempDir()
	server.cfg.MapSourceRoot = root
	now := time.Now()
	writeStatusJSON(t, root, "Running", liveStatusReport(now.Add(-time.Second), ""))

	// Stored "offline" on a server that is actually up: the case that locked players
	// out of a healthy world.
	stored := PublicWorld{Name: "Running", Status: "offline", ServerVersion: "0.220.5"}
	live := server.withLiveStatus(stored, now, nil)
	if live.Status != "online" {
		t.Fatalf("a running server must read online regardless of the stored label: %q", live.Status)
	}
	if live.ServerVersion != "0.221.12" {
		t.Fatalf("server version = %q, want the build the server reports", live.ServerVersion)
	}

	// Stored "online" on a server that is not running: the green light on a dead server.
	stale := PublicWorld{Name: "Stopped", Status: "online", ServerVersion: "0.220.5"}
	if got := server.withLiveStatus(stale, now, nil).Status; got != "offline" {
		t.Fatalf("a server that is not answering must read offline, got %q", got)
	}

	maintenance := PublicWorld{Name: "Running", Status: "maintenance"}
	if got := server.withLiveStatus(maintenance, now, nil).Status; got != "maintenance" {
		t.Fatalf("maintenance is an operator announcement and must survive, got %q", got)
	}

	// An open admin-mode window is stamped above the maintenance shortcut, because a world
	// can be both and the one that kicks players is the one an operator must not miss.
	windows := map[string]WorldAdminMode{"Running": {World: "Running", Since: now, Actor: "operator"}}
	if marked := server.withLiveStatus(maintenance, now, windows); !marked.AdminMode || marked.Status != "maintenance" {
		t.Fatalf("admin mode = %t and status = %q, want the window marked and maintenance kept", marked.AdminMode, marked.Status)
	}
}

// The launcher gates play on /api/status, so the probe has to reach it. A stale
// "offline" there is not cosmetic: it refuses to start the game.
func TestStatusAPIReportsMeasuredStateToTheLauncher(t *testing.T) {
	server := testServer(t)
	root := t.TempDir()
	server.cfg.MapSourceRoot = root
	release := Release{ID: "live-1", World: "Ashlands", Profile: "raiders", ClientType: "flat", Version: "1.0.0"}
	publishProfile(t, server, release)
	if err := server.store.UpsertPublicWorld(t.Context(), PublicWorld{
		Name: release.World, JoinAddress: "valheim.example.test:2456",
		Status: "offline", ServerVersion: "0.220.5", Enabled: true,
	}, "operator"); err != nil {
		t.Fatal(err)
	}
	writeStatusJSON(t, root, release.World, liveStatusReport(time.Now().Add(-time.Second), ""))

	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/api/status", nil))
	if response.Code != http.StatusOK {
		t.Fatalf("status API = %d", response.Code)
	}
	body := response.Body.String()
	if !strings.Contains(body, `"status":"online"`) {
		t.Fatalf("the launcher must see the running server as online: %s", body)
	}
	if !strings.Contains(body, `"server_version":"0.221.12"`) {
		t.Fatalf("the launcher must see the build the server reports: %s", body)
	}
}
