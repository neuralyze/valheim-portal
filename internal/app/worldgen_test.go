package app

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/neuralyze/valheim-portal/internal/maptiles"
)

const (
	worldgenWorld = "Midgard"
	worldgenSeed  = "SeedTest01"
)

// worldgenReply is the shape the host script promises: one JSON line proving it
// read the requested seed back out of the world Valheim generated.
const worldgenReply = `starting valheim-server-Midgard
{"world":"Midgard","seed_name":"SeedTest01","seed":-682541416,"db_bytes":365,"archive":"world-archive-20260730T055000-worldgen","verified":true}
world is up`

func worldgenServer(t *testing.T) *Server {
	t.Helper()
	server := testServer(t)
	if err := server.store.UpsertPublicWorld(t.Context(), PublicWorld{
		Name: worldgenWorld, JoinAddress: "valheim.example:2456", Status: "online", ServerVersion: "test",
	}, "test"); err != nil {
		t.Fatal(err)
	}
	return server
}

// worldgenAs drives one admin request as a named operator, so tests can prove a
// pending regeneration belongs to the actor who prepared it.
func worldgenAs(t *testing.T, server *Server, actor, method, target string, form url.Values) *httptest.ResponseRecorder {
	t.Helper()
	nonce := strings.Repeat("a", 64)
	var body io.Reader
	if form != nil {
		form.Set("csrf", server.csrfToken(nonce))
		body = strings.NewReader(form.Encode())
	}
	request := httptest.NewRequest(method, target, body)
	request.RemoteAddr = "192.0.2.10:1234"
	if form != nil {
		request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	}
	request.Header.Set(server.cfg.AuthHeader, actor)
	request.Header.Set(adminTokenHeader, testAdminToken)
	request.AddCookie(&http.Cookie{Name: "portal_csrf", Value: nonce, Path: "/admin"})
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	return response
}

func prepareWorldgen(t *testing.T, server *Server, actor string) string {
	t.Helper()
	response := worldgenAs(t, server, actor, http.MethodPost, "/admin/worldgen", url.Values{"world": {worldgenWorld}, "seed": {worldgenSeed}})
	if response.Code != http.StatusSeeOther {
		t.Fatalf("prepare worldgen = %d: %s", response.Code, response.Body.String())
	}
	location := response.Header().Get("Location")
	id := strings.TrimPrefix(location, "/admin/worldgen/")
	if id == "" || id == location {
		t.Fatalf("prepare worldgen redirected to %q", location)
	}
	return id
}

func captureWorldgenAgent(t *testing.T, server *Server, reply AgentReply) func() []agentRequest {
	t.Helper()
	var mu sync.Mutex
	var requests []agentRequest
	serveMockAgent(t, server, func(w http.ResponseWriter, r *http.Request) {
		var request agentRequest
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			http.Error(w, "bad request", http.StatusBadRequest)
			return
		}
		mu.Lock()
		requests = append(requests, request)
		mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(reply)
	})
	return func() []agentRequest {
		mu.Lock()
		defer mu.Unlock()
		return append([]agentRequest(nil), requests...)
	}
}

func worldgenJob(t *testing.T, server *Server) Job {
	t.Helper()
	jobs, err := server.store.RecentJobs(t.Context(), 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(jobs) != 1 {
		t.Fatalf("jobs = %#v, want exactly one", jobs)
	}
	return jobs[0]
}

func TestWorldgenPrepareRejectsBadSeedsAndUnknownWorlds(t *testing.T) {
	server := worldgenServer(t)
	for _, refused := range []struct {
		name string
		form url.Values
		want int
	}{
		{"empty seed", url.Values{"world": {worldgenWorld}, "seed": {""}}, http.StatusBadRequest},
		{"punctuation", url.Values{"world": {worldgenWorld}, "seed": {"qmrbec-QI2K"}}, http.StatusBadRequest},
		{"whitespace", url.Values{"world": {worldgenWorld}, "seed": {"two words"}}, http.StatusBadRequest},
		{"too long", url.Values{"world": {worldgenWorld}, "seed": {strings.Repeat("a", 65)}}, http.StatusBadRequest},
		{"path traversal world", url.Values{"world": {"../escape"}, "seed": {worldgenSeed}}, http.StatusBadRequest},
		{"unregistered world", url.Values{"world": {"NotAWorld"}, "seed": {worldgenSeed}}, http.StatusNotFound},
	} {
		response := worldgenAs(t, server, "operator", http.MethodPost, "/admin/worldgen", refused.form)
		if response.Code != refused.want {
			t.Fatalf("%s = %d, want %d: %s", refused.name, response.Code, refused.want, response.Body.String())
		}
	}
	server.worldgenMu.Lock()
	pending := len(server.worldgens)
	server.worldgenMu.Unlock()
	if pending != 0 {
		t.Fatalf("refused requests left %d pending regenerations", pending)
	}
}

func TestWorldgenConfirmationPageBindsToItsActorAndExpires(t *testing.T) {
	server := worldgenServer(t)
	id := prepareWorldgen(t, server, "operator")

	page := worldgenAs(t, server, "operator", http.MethodGet, "/admin/worldgen/"+id, nil)
	if page.Code != http.StatusOK {
		t.Fatalf("confirmation page = %d: %s", page.Code, page.Body.String())
	}
	body := page.Body.String()
	for _, promise := range []string{
		"RECREATE " + worldgenWorld + " " + worldgenSeed,
		"archived, not deleted",
		"regenerated at day 0",
		"terrain modification in the current save will be gone",
	} {
		if !strings.Contains(body, promise) {
			t.Fatalf("confirmation page omits %q: %s", promise, body)
		}
	}

	intruder := worldgenAs(t, server, "intruder", http.MethodGet, "/admin/worldgen/"+id, nil)
	if intruder.Code != http.StatusNotFound {
		t.Fatalf("another operator read the pending regeneration = %d: %s", intruder.Code, intruder.Body.String())
	}

	unknown := worldgenAs(t, server, "operator", http.MethodGet, "/admin/worldgen/deadbeef", nil)
	if unknown.Code != http.StatusNotFound {
		t.Fatalf("unknown regeneration id = %d", unknown.Code)
	}

	server.worldgenMu.Lock()
	server.worldgens["expired"] = worldgenRequest{Actor: "operator", World: worldgenWorld, Seed: worldgenSeed, ExpiresAt: time.Now().Add(-time.Minute)}
	server.worldgenMu.Unlock()
	stale := worldgenAs(t, server, "operator", http.MethodGet, "/admin/worldgen/expired", nil)
	if stale.Code != http.StatusNotFound {
		t.Fatalf("expired regeneration id = %d: %s", stale.Code, stale.Body.String())
	}
}

func TestWorldgenWrongConfirmationNeverReachesTheAgent(t *testing.T) {
	server := worldgenServer(t)
	dispatched := captureWorldgenAgent(t, server, AgentReply{Status: "succeeded", Output: worldgenReply})
	for _, typed := range []string{"", "RECREATE " + worldgenWorld, "recreate " + worldgenWorld + " " + worldgenSeed, "RECREATE " + worldgenWorld + " OtherSeed"} {
		id := prepareWorldgen(t, server, "operator")
		response := worldgenAs(t, server, "operator", http.MethodPost, "/admin/worldgen/"+id, url.Values{"confirmation": {typed}})
		if response.Code != http.StatusBadRequest {
			t.Fatalf("confirmation %q = %d: %s", typed, response.Code, response.Body.String())
		}
	}
	if requests := dispatched(); len(requests) != 0 {
		t.Fatalf("mistyped confirmation dispatched %#v", requests)
	}
	jobs, err := server.store.RecentJobs(t.Context(), 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(jobs) != 0 {
		t.Fatalf("mistyped confirmation queued %#v", jobs)
	}
}

func TestWorldgenConfirmationDispatchesTheRequestedSeed(t *testing.T) {
	server := worldgenServer(t)
	dispatched := captureWorldgenAgent(t, server, AgentReply{Status: "succeeded", Output: worldgenReply})
	id := prepareWorldgen(t, server, "operator")
	response := worldgenAs(t, server, "operator", http.MethodPost, "/admin/worldgen/"+id, url.Values{"confirmation": {"RECREATE " + worldgenWorld + " " + worldgenSeed}})
	if response.Code != http.StatusSeeOther || response.Header().Get("Location") != "/admin" {
		t.Fatalf("confirm worldgen = %d %q: %s", response.Code, response.Header().Get("Location"), response.Body.String())
	}
	requests := dispatched()
	if len(requests) != 1 {
		t.Fatalf("agent requests = %#v, want exactly one", requests)
	}
	if requests[0].Operation != "world_create" || requests[0].World != worldgenWorld || requests[0].Seed != worldgenSeed {
		t.Fatalf("agent request = %#v", requests[0])
	}
	job := worldgenJob(t, server)
	if job.Operation != "world_create" || job.Status != "succeeded" {
		t.Fatalf("job = %#v", job)
	}
	for _, reported := range []string{worldgenSeed, "-682541416", "365", "world-archive-20260730T055000-worldgen"} {
		if !strings.Contains(job.Detail, reported) {
			t.Fatalf("job detail omits %q: %s", reported, job.Detail)
		}
	}
}

// An unverified seed is the entire failure mode this flow exists to prevent, so a
// success from the agent that carries no read-back proof is a failed job.
func TestWorldgenUnverifiedSeedFailsTheJobDespiteAgentSuccess(t *testing.T) {
	for _, unproven := range []struct {
		name   string
		output string
	}{
		{"verified false", `{"world":"Midgard","seed_name":"SeedTest01","seed":-682541416,"db_bytes":365,"archive":"a","verified":false}`},
		{"verified absent", `{"world":"Midgard","seed_name":"SeedTest01","seed":-682541416,"db_bytes":365,"archive":"a"}`},
		{"different seed read back", `{"world":"Midgard","seed_name":"SomeOtherSeed","seed":12,"db_bytes":365,"archive":"a","verified":true}`},
		{"different world read back", `{"world":"Asgard","seed_name":"SeedTest01","seed":-682541416,"db_bytes":365,"archive":"a","verified":true}`},
		{"no json at all", "recreated the world"},
	} {
		t.Run(unproven.name, func(t *testing.T) {
			server := worldgenServer(t)
			dispatched := captureWorldgenAgent(t, server, AgentReply{Status: "succeeded", Output: unproven.output})
			id := prepareWorldgen(t, server, "operator")
			response := worldgenAs(t, server, "operator", http.MethodPost, "/admin/worldgen/"+id, url.Values{"confirmation": {"RECREATE " + worldgenWorld + " " + worldgenSeed}})
			if response.Code != http.StatusConflict {
				t.Fatalf("unverified regeneration = %d: %s", response.Code, response.Body.String())
			}
			if requests := dispatched(); len(requests) != 1 {
				t.Fatalf("agent requests = %#v", requests)
			}
			job := worldgenJob(t, server)
			if job.Status != "failed" || !strings.Contains(job.Detail, "did not verify seed "+worldgenSeed) {
				t.Fatalf("job = %#v", job)
			}
		})
	}
}

func TestWorldgenPendingRequestIsSingleUse(t *testing.T) {
	server := worldgenServer(t)
	dispatched := captureWorldgenAgent(t, server, AgentReply{Status: "succeeded", Output: worldgenReply})
	id := prepareWorldgen(t, server, "operator")
	confirmation := url.Values{"confirmation": {"RECREATE " + worldgenWorld + " " + worldgenSeed}}
	if first := worldgenAs(t, server, "operator", http.MethodPost, "/admin/worldgen/"+id, confirmation); first.Code != http.StatusSeeOther {
		t.Fatalf("first confirmation = %d: %s", first.Code, first.Body.String())
	}
	replay := worldgenAs(t, server, "operator", http.MethodPost, "/admin/worldgen/"+id, confirmation)
	if replay.Code != http.StatusNotFound {
		t.Fatalf("replayed confirmation = %d: %s", replay.Code, replay.Body.String())
	}
	if requests := dispatched(); len(requests) != 1 {
		t.Fatalf("replay dispatched %d agent requests", len(requests))
	}
	worldgenJob(t, server)
}

func TestAdminWorldCardPrefillsTheSeedItsMapWasBuiltFor(t *testing.T) {
	server := worldgenServer(t)
	blank := adminPage(t, server)
	if !strings.Contains(blank, `<input type="hidden" name="world" value="`+worldgenWorld+`">`) ||
		!strings.Contains(blank, `action="/admin/worldgen"`) {
		t.Fatalf("world card offers no regeneration entry point: %s", blank)
	}
	if !strings.Contains(blank, `name="seed" value=""`) {
		t.Fatalf("world without a map should prefill nothing: %s", blank)
	}
	writeCurrentMapManifest(t, server, worldgenWorld, worldgenSeed)
	if page := adminPage(t, server); !strings.Contains(page, `name="seed" value="`+worldgenSeed+`"`) {
		t.Fatalf("world card did not prefill the current map seed: %s", page)
	}
}

func writeCurrentMapManifest(t *testing.T, server *Server, world, seed string) {
	t.Helper()
	manifest := maptiles.Manifest{
		Schema: maptiles.SchemaVersion, World: world, Seed: seed, WorldGenVersion: 37,
		Renderer: maptiles.RendererVersion, Key: "worldgen-test-key", SourceSHA256: "source", HeightSHA256: "height", TextureSHA256: "texture",
		SourceWidth: maptiles.MinimumSize, SourceHeight: maptiles.MinimumSize,
		Width: maptiles.MinimumSize, Height: maptiles.MinimumSize, TileSize: maptiles.TileSize, MaxZoom: 0, Format: "png",
		Levels: []maptiles.Level{{Zoom: 0, Width: maptiles.MinimumSize, Height: maptiles.MinimumSize, TilesWide: 24, TilesHigh: 24}},
	}
	path, err := maptiles.CurrentManifestPath(server.cfg.MapRoot, world)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		t.Fatal(err)
	}
	body, err := json.Marshal(manifest)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, body, 0o640); err != nil {
		t.Fatal(err)
	}
}

// Regenerating a world destroys the current save, so it must be reachable only through the typed
// confirmation flow - never through the generic job runner, which needs no confirmation at all.
// world_create is deliberately absent from allowedOperation to enforce that; recordableOperation
// exists so CreateJob can still log the job. This test fails if anyone collapses the two.
func TestWorldCreateIsNotDispatchableThroughTheGenericJobRunner(t *testing.T) {
	server := worldgenServer(t)
	captured := captureWorldgenAgent(t, server, AgentReply{Status: "succeeded", Output: worldgenReply})

	response := worldgenAs(t, server, "operator", http.MethodPost, "/admin/jobs", url.Values{
		"world": {worldgenWorld}, "operation": {"world_create"},
	})
	if response.Code != http.StatusBadRequest {
		t.Fatalf("POST /admin/jobs world_create = %d, want 400", response.Code)
	}
	if dispatched := captured(); len(dispatched) != 0 {
		t.Fatalf("generic job runner dispatched world_create: %+v", dispatched)
	}
	if allowedOperation("world_create") {
		t.Fatal("world_create is in allowedOperation; the generic runner can now destroy a world save")
	}
	// The split is only worth having if the log side still accepts it.
	if !recordableOperation("world_create") {
		t.Fatal("world_create is not recordable; CreateJob will reject the confirmed flow")
	}
}
