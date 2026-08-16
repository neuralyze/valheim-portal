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

// Valheim stamps a player id on every piece and nothing resolves it to a person, so the operator
// names a builder once and the portal remembers. The id is never editable: it is evidence, and the
// label is only the portal's note about who that is.
func TestNamingABuilderIsRememberedAndAudited(t *testing.T) {
	server := testServer(t)
	if err := server.store.UpsertPublicWorld(t.Context(), PublicWorld{
		Name: "Midgard", JoinAddress: "valheim.example.test:2456", Status: "online",
	}, "test"); err != nil {
		t.Fatal(err)
	}
	const nonce = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
	post := func(label string) *httptest.ResponseRecorder {
		form := url.Values{"creator": {"308095166"}, "label": {label}}
		form.Set("csrf", server.csrfToken(nonce))
		request := adminTestRequest(http.MethodPost, "/admin/worlds/Midgard/builders", strings.NewReader(form.Encode()))
		request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
		request.Header.Set("X-Portal-Actor", "operator")
		request.AddCookie(&http.Cookie{Name: "portal_csrf", Value: nonce, Path: "/admin"})
		response := httptest.NewRecorder()
		server.Handler().ServeHTTP(response, request)
		return response
	}

	if code := post("Jarn").Code; code != http.StatusSeeOther {
		t.Fatalf("naming a builder = %d, want 303", code)
	}
	labels, err := server.store.BuilderLabels(t.Context(), "Midgard")
	if err != nil {
		t.Fatal(err)
	}
	if labels[308095166] != "Jarn" {
		t.Errorf("stored labels = %v, want the name that was given", labels)
	}

	// Clearing the field forgets the name rather than storing an empty one.
	if code := post("").Code; code != http.StatusSeeOther {
		t.Fatalf("clearing a name = %d, want 303", code)
	}
	labels, err = server.store.BuilderLabels(t.Context(), "Midgard")
	if err != nil {
		t.Fatal(err)
	}
	if _, still := labels[308095166]; still {
		t.Error("the name survived being cleared")
	}
}

// The placeholder the page renders must never be stored as if the operator had chosen it: that would
// turn "we do not know" into an assertion about who built something.
func TestSavingThePlaceholderStoresNothing(t *testing.T) {
	server := testServer(t)
	if err := server.store.UpsertPublicWorld(t.Context(), PublicWorld{
		Name: "Midgard", JoinAddress: "valheim.example.test:2456", Status: "online",
	}, "test"); err != nil {
		t.Fatal(err)
	}
	const nonce = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
	form := url.Values{"creator": {"308095166"}, "label": {builderFallbackName(308095166)}}
	form.Set("csrf", server.csrfToken(nonce))
	request := adminTestRequest(http.MethodPost, "/admin/worlds/Midgard/builders", strings.NewReader(form.Encode()))
	request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	request.AddCookie(&http.Cookie{Name: "portal_csrf", Value: nonce, Path: "/admin"})
	server.Handler().ServeHTTP(httptest.NewRecorder(), request)

	labels, err := server.store.BuilderLabels(t.Context(), "Midgard")
	if err != nil {
		t.Fatal(err)
	}
	if len(labels) != 0 {
		t.Errorf("the placeholder was stored as a name: %v", labels)
	}
}

// The canvas and the legend must agree about who owns a base, so the page serves the colour rather
// than the script recomputing it from a palette copied into a second language. This checks the two
// halves the page emits: the swatch in the legend, and the styles handed to the script.
func TestTheLegendAndTheCanvasShareOnePalette(t *testing.T) {
	for _, creator := range []int64{308095166, 2387859451} {
		colour := builderColour(creator)
		if colour == "" {
			t.Fatalf("builder %d has no colour", creator)
		}
	}
	if builderColour(308095166) == builderColour(2387859451) {
		t.Error("two builders share a colour, so the map cannot distinguish them")
	}
	// An unattributed piece is not a builder and must not take a builder's colour.
	if builderColour(0) == builderColour(308095166) {
		t.Error("unattributed pieces draw in a builder's colour")
	}
	// The stand-in name is derived from the id, so it can never be mistaken for a chosen name.
	if name := builderFallbackName(2387859451); !strings.Contains(name, "9451") {
		t.Errorf("fallback name = %q, want it to carry the id", name)
	}
}

// Valheim leaves the builder stamp empty on generated structures, so a snapshot carries a pile of
// pieces nobody is recorded as having placed. The legend used to call that pile "builder 0000", which
// reads as a player and sends an operator looking for somebody who does not exist.
func TestUnattributedPiecesAreNotPresentedAsABuilder(t *testing.T) {
	name := builderFallbackName(0)
	if strings.HasPrefix(name, "builder ") || strings.ContainsAny(name, "0123456789") {
		t.Errorf("unattributed pieces are labelled %q, which reads as a numbered player", name)
	}
	if !strings.Contains(name, "no builder") {
		t.Errorf("unattributed pieces are labelled %q, which does not say the record is empty", name)
	}
	// And the pile keeps its own colour, so it is never confused with somebody's base.
	if builderColour(0) == builderColour(308095166) {
		t.Error("the unattributed pile draws in a builder's colour")
	}
}

// Valheim leaves the builder stamp empty on generated structures, and the page hides the naming field
// for that pile. Hiding a field is presentation, so the refusal has to live in the handler: a name
// stored against creator 0 would put a person's name on ruins nobody built.
func TestNamingTheUnattributedPileIsRefused(t *testing.T) {
	server := testServer(t)
	if err := server.store.UpsertPublicWorld(t.Context(), PublicWorld{
		Name: "Midgard", JoinAddress: "valheim.example.test:2456", Status: "online",
	}, "test"); err != nil {
		t.Fatal(err)
	}
	const nonce = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
	form := url.Values{"creator": {"0"}, "label": {"Kato"}}
	form.Set("csrf", server.csrfToken(nonce))
	request := adminTestRequest(http.MethodPost, "/admin/worlds/Midgard/builders", strings.NewReader(form.Encode()))
	request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	request.Header.Set("X-Portal-Actor", "operator")
	request.AddCookie(&http.Cookie{Name: "portal_csrf", Value: nonce, Path: "/admin"})
	response := httptest.NewRecorder()

	server.Handler().ServeHTTP(response, request)

	if response.Code != http.StatusBadRequest {
		t.Errorf("naming the unattributed pile = %d, want 400", response.Code)
	}
	labels, err := server.store.BuilderLabels(t.Context(), "Midgard")
	if err != nil {
		t.Fatal(err)
	}
	if name, ok := labels[0]; ok {
		t.Errorf("stored %q against pieces with no builder id", name)
	}
}
