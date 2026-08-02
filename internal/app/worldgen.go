package app

import (
	"encoding/json"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/neuralyze/valheim-portal/internal/maptiles"
)

type worldgenRequest struct {
	Actor, World, Seed string
	ExpiresAt          time.Time
}

// worldgenResult is the one JSON line portal_create_valheim_world.sh prints after
// it has re-read the world file Valheim generated.
type worldgenResult struct {
	World    string `json:"world"`
	SeedName string `json:"seed_name"`
	Seed     int64  `json:"seed"`
	DBBytes  int64  `json:"db_bytes"`
	Archive  string `json:"archive"`
	Verified bool   `json:"verified"`
}

func (s *Server) prepareWorldgen(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, 16<<10)
	if err := r.ParseForm(); err != nil {
		http.Error(w, "invalid form", http.StatusBadRequest)
		return
	}
	world, seed := r.FormValue("world"), strings.TrimSpace(r.FormValue("seed"))
	if !validWorld(world) || !worldSeedPattern.MatchString(seed) {
		http.Error(w, "invalid world regeneration request", http.StatusBadRequest)
		return
	}
	if _, err := s.store.PublicWorld(r.Context(), world); err != nil {
		http.NotFound(w, r)
		return
	}
	id := randomID()
	actor := r.Header.Get("X-Portal-Actor")
	s.worldgenMu.Lock()
	for key, request := range s.worldgens {
		if time.Now().After(request.ExpiresAt) {
			delete(s.worldgens, key)
		}
	}
	s.worldgens[id] = worldgenRequest{Actor: actor, World: world, Seed: seed, ExpiresAt: time.Now().Add(10 * time.Minute)}
	s.worldgenMu.Unlock()
	if err := s.store.Audit(r.Context(), actor, "worldgen.prepare", id, world+":"+seed); err != nil {
		http.Error(w, "unable to record world regeneration request", http.StatusInternalServerError)
		return
	}
	http.Redirect(w, r, "/admin/worldgen/"+id, http.StatusSeeOther)
}

func (s *Server) worldgenConfirmation(w http.ResponseWriter, r *http.Request) {
	request, ok := s.worldgenRequest(r.PathValue("id"), r.Header.Get("X-Portal-Actor"), false)
	if !ok {
		http.NotFound(w, r)
		return
	}
	// One world, one deliberate click: the authoritative seed is worth an agent
	// round-trip here so an accidental re-seed is visible before the phrase is typed.
	current, _ := s.worldSeed(r.Context(), request.World)
	render(w, worldgenTemplate, map[string]any{"ID": r.PathValue("id"), "Worldgen": request, "CurrentSeed": current, "CSRF": s.csrfCookie(w, r)})
}

func (s *Server) confirmWorldgen(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, 16<<10)
	if err := r.ParseForm(); err != nil {
		http.Error(w, "invalid form", http.StatusBadRequest)
		return
	}
	actor := r.Header.Get("X-Portal-Actor")
	request, ok := s.worldgenRequest(r.PathValue("id"), actor, true)
	if !ok {
		http.NotFound(w, r)
		return
	}
	if r.FormValue("confirmation") != "RECREATE "+request.World+" "+request.Seed {
		http.Error(w, "confirmation does not match", http.StatusBadRequest)
		return
	}
	jobID := randomID()
	if err := s.store.CreateJob(r.Context(), Job{
		ID: jobID, World: request.World, Operation: "world_create", Status: "queued", RequestedBy: actor,
		Detail: "archive the current save, then regenerate at day 0 on seed " + request.Seed,
	}, actor); err != nil {
		http.Error(w, "unable to queue world regeneration", http.StatusInternalServerError)
		return
	}
	if err := s.store.Audit(r.Context(), actor, "worldgen.confirm", jobID, request.World+":"+request.Seed); err != nil {
		http.Error(w, "unable to record world regeneration", http.StatusInternalServerError)
		return
	}
	reply, err := s.agent.RunWithSeed(r.Context(), jobID, request.World, request.Seed)
	if err != nil {
		_ = s.store.FinishJob(r.Context(), jobID, "failed", "agent request failed", actor)
		http.Error(w, "agent unavailable", http.StatusBadGateway)
		return
	}
	status, detail := worldgenOutcome(request, reply)
	if err := s.store.FinishJob(r.Context(), jobID, status, detail, actor); err != nil {
		http.Error(w, "unable to finish world regeneration job", http.StatusInternalServerError)
		return
	}
	if status != "succeeded" {
		http.Error(w, detail, http.StatusConflict)
		return
	}
	http.Redirect(w, r, "/admin", http.StatusSeeOther)
}

func (s *Server) worldgenRequest(id, actor string, consume bool) (worldgenRequest, bool) {
	s.worldgenMu.Lock()
	defer s.worldgenMu.Unlock()
	request, ok := s.worldgens[id]
	if !ok || request.Actor != actor || time.Now().After(request.ExpiresAt) {
		delete(s.worldgens, id)
		return worldgenRequest{}, false
	}
	if consume {
		delete(s.worldgens, id)
	}
	return request, true
}

// worldgenOutcome refuses to call a regeneration successful on the agent's word.
// A .fwl whose .db is missing is not a world to Valheim, so it regenerates the
// seed and overwrites the file: the only evidence a chosen seed survived is the
// host reading it back out of the world it just created.
func worldgenOutcome(request worldgenRequest, reply AgentReply) (string, string) {
	detail := strings.TrimSpace(reply.Output)
	if reply.Status != "succeeded" {
		if reply.Error != "" {
			detail = strings.TrimSpace(reply.Error + "\n" + detail)
		}
		return "failed", detail
	}
	result, ok := parseWorldgenResult(reply.Output)
	if !ok || !result.Verified || result.World != request.World || result.SeedName != request.Seed {
		return "failed", strings.TrimSpace("the host did not verify seed " + request.Seed + " in the generated world; the previous save was left restored\n" + detail)
	}
	return "succeeded", "seed " + result.SeedName + " (" + strconv.FormatInt(result.Seed, 10) + "), fresh database " +
		strconv.FormatInt(result.DBBytes, 10) + " bytes, previous save archived as " + result.Archive
}

// The agent merges the script's stdout and stderr, so the contract's single JSON
// line arrives somewhere among its diagnostics.
func parseWorldgenResult(output string) (worldgenResult, bool) {
	for _, line := range strings.Split(output, "\n") {
		line = strings.TrimSpace(line)
		if !strings.HasPrefix(line, "{") {
			continue
		}
		var result worldgenResult
		if json.Unmarshal([]byte(line), &result) == nil && result.SeedName != "" {
			return result, true
		}
	}
	return worldgenResult{}, false
}

// worldgenSeedDefaults prefills each world's form with the seed its current map
// was built for, so resetting a world to day 0 on the seed it already has needs
// no typing. A map not yet rebuilt after a re-seed lags the host, so this is a
// default the operator overwrites, never an authority.
func (s *Server) worldgenSeedDefaults(worlds []adminWorld) map[string]string {
	seeds := make(map[string]string, len(worlds))
	for _, world := range worlds {
		path, err := maptiles.CurrentManifestPath(s.cfg.MapRoot, world.Name)
		if err != nil {
			continue
		}
		manifest, err := maptiles.LoadManifest(path)
		if err != nil || !worldSeedPattern.MatchString(manifest.Seed) {
			continue
		}
		seeds[world.Name] = manifest.Seed
	}
	return seeds
}

const worldgenTemplate = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Recreate {{.Worldgen.World}} from a seed</title></head><body>
<p><a href="/admin">Back to administration</a></p>
<h1>Recreate {{.Worldgen.World}} at day 0</h1>
<p>Requested seed <code>{{.Worldgen.Seed}}</code>{{if .CurrentSeed}}, current seed on the host <code>{{.CurrentSeed}}</code>{{end}}.</p>
{{if and .CurrentSeed (ne .CurrentSeed .Worldgen.Seed)}}<p><b>The requested seed is not the seed this world runs on now.</b> Confirming replaces its map with a different one.</p>{{end}}
<ul>
<li>The current save is <b>archived, not deleted</b>: every <code>{{.Worldgen.World}}</code> save file is moved into a timestamped <code>world-archive-…-worldgen</code> directory on the host.</li>
<li>The world is then <b>regenerated at day 0</b> on seed <code>{{.Worldgen.Seed}}</code> with a pristine database.</li>
<li><b>Every structure, chest, portal, and terrain modification in the current save will be gone.</b> Player characters live on their own clients and are untouched.</li>
<li>Nothing is reported as succeeded until the host reads the generated world file back and finds seed <code>{{.Worldgen.Seed}}</code>.</li>
</ul>
<form method="post" action="/admin/worldgen/{{.ID}}"><input type="hidden" name="csrf" value="{{.CSRF}}">
<label>Type <code>RECREATE {{.Worldgen.World}} {{.Worldgen.Seed}}</code> <input name="confirmation" autocomplete="off" required></label>
<button class="danger">Archive the save and recreate the world</button>
</form>
</body></html>`
