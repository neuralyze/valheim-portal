package app

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/neuralyze/valheim-portal/internal/maptiles"

	"github.com/neuralyze/valheim-portal/internal/worldintel"
)

// The operator asked the map to refresh, the portal answered "succeeded", and nothing changed: the
// generic job path sent world_analysis to the agent and recorded reply.Output, while the snapshot
// arrives in reply.Data. So the click analysed the world and threw the result away, and the map kept
// showing structures from whenever a server had last started - eighteen days stale in this case.
//
// The agent is unreachable in this fixture, so the request must fail loudly. What is asserted is the
// pairing: a world_analysis job may never be recorded as succeeded unless a snapshot was published.
func TestRefreshingTheMapNeverSucceedsWithoutPublishing(t *testing.T) {
	server := testServer(t)
	published := 0
	server.mapPublisher = func(ctx context.Context, snapshot worldintel.Snapshot, actor string) *worldAnalysisFailure {
		published++
		return nil
	}

	// A POST through the admin guard needs the CSRF pair, or the handler never runs.
	const nonce = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
	form := url.Values{"world": {"Midgard"}, "operation": {"world_analysis"}}
	form.Set("csrf", server.csrfToken(nonce))
	request := adminTestRequest(http.MethodPost, "/admin/jobs", strings.NewReader(form.Encode()))
	request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	request.AddCookie(&http.Cookie{Name: "portal_csrf", Value: nonce, Path: "/admin"})
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)

	jobs, err := server.store.RecentJobs(t.Context(), 10)
	if err != nil {
		t.Fatal(err)
	}
	for _, job := range jobs {
		if job.Operation != "world_analysis" {
			continue
		}
		if job.Status == "succeeded" && published == 0 {
			t.Fatalf("the refresh reported %q while publishing nothing: the map would stay stale and say it was updated", job.Status)
		}
		return
	}
	t.Fatal("no world_analysis job was recorded at all, so the operator's refresh did not even try")
}

// Terrain is a function of the seed and the worldgen version. When tiles already describe this
// world, a refresh must not re-render a 12288px world and recut the pyramid to arrive at the same
// pixels: that is the difference between "show me what we just built" taking seconds and taking
// minutes. This holds the decision, not the timing.
func TestTerrainIsReusedWhenTheSeedAndVersionMatch(t *testing.T) {
	server := testServer(t)
	snapshot := worldintel.Snapshot{World: "Midgard", Seed: "qmrbecQI2K", WorldVersion: 37}

	if _, reusable := server.reusableTerrain("Midgard", snapshot); reusable {
		t.Fatal("terrain was called reusable with no tiles on disk")
	}

	path, err := maptiles.CurrentManifestPath(server.cfg.MapRoot, "Midgard")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		t.Fatal(err)
	}
	// A manifest that passes maptiles.Validate, because LoadManifest refuses a partial one - and a
	// refused manifest would look like "no terrain" and quietly take the slow path.
	write := func(seed string, version int) {
		manifest := maptiles.Manifest{
			Schema: maptiles.SchemaVersion, World: "Midgard", Seed: seed, WorldGenVersion: version,
			Renderer: "test-renderer", Key: strings.Repeat("a", 64),
			SourceSHA256: strings.Repeat("b", 64), HeightSHA256: strings.Repeat("c", 64),
			TextureSHA256: strings.Repeat("d", 64),
			Width:         maptiles.MinimumSize, Height: maptiles.MinimumSize,
			TileSize: maptiles.TileSize, Format: "png", MaxZoom: 0,
			Levels: []maptiles.Level{{Zoom: 0, Width: maptiles.MinimumSize, Height: maptiles.MinimumSize, TilesWide: 24, TilesHigh: 24}},
		}
		body, marshalErr := json.Marshal(manifest)
		if marshalErr != nil {
			t.Fatal(marshalErr)
		}
		if writeErr := os.WriteFile(path, body, 0o640); writeErr != nil {
			t.Fatal(writeErr)
		}
	}

	write("qmrbecQI2K", 37)
	if _, reusable := server.reusableTerrain("Midgard", snapshot); !reusable {
		t.Error("matching terrain was rebuilt, which is the slow path nobody asked for")
	}

	// A different seed or a worldgen bump means the tiles really are wrong.
	write("adifferentseed", 37)
	if _, reusable := server.reusableTerrain("Midgard", snapshot); reusable {
		t.Error("terrain from another seed was reused")
	}
	write("qmrbecQI2K", 36)
	if _, reusable := server.reusableTerrain("Midgard", snapshot); reusable {
		t.Error("terrain from an older worldgen version was reused")
	}
}
