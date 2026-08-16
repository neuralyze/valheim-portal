package app

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/neuralyze/valheim-portal/internal/maptiles"
	"github.com/neuralyze/valheim-portal/internal/worldintel"
)

type worldAnalysisPage struct {
	World        PublicWorld
	HaveAnalysis bool
	Backup       string
	AnalyzedAt   string
	CSRF         string
}

func (s *Server) worldAnalysisMap(w http.ResponseWriter, r *http.Request) {
	world := r.PathValue("world")
	if !validWorld(world) {
		http.NotFound(w, r)
		return
	}
	info, err := s.store.PublicWorld(r.Context(), world)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	snapshots, err := s.store.LatestWorldAnalyses(r.Context(), world, 1)
	if err != nil {
		http.Error(w, "world analysis unavailable", http.StatusServiceUnavailable)
		return
	}
	page := worldAnalysisPage{World: info, HaveAnalysis: len(snapshots) > 0, CSRF: s.csrfCookie(w, r)}
	if len(snapshots) > 0 {
		page.Backup = snapshots[0].Source.Backup
		page.AnalyzedAt = snapshots[0].Source.ModifiedAt.Format("2006-01-02 15:04 UTC")
	}
	render(w, worldAnalysisTemplate, page)
}

type worldAnalysisFailure struct {
	status int
	client string
	detail string
}

type worldMapPublisher func(context.Context, worldintel.Snapshot, string) *worldAnalysisFailure

func (s *Server) runWorldAnalysis(w http.ResponseWriter, r *http.Request) {
	world := r.PathValue("world")
	if !validWorld(world) {
		http.NotFound(w, r)
		return
	}
	if _, err := s.store.PublicWorld(r.Context(), world); err != nil {
		http.NotFound(w, r)
		return
	}
	// A fresh backup first, because the point of pressing this is to see what was just built and
	// the newest archive on disk can be weeks old - which is exactly how a map came to show
	// structures from eighteen days earlier while reporting success.
	forceFull := r.FormValue("rebuild") == "terrain"
	if failure := s.runWorldAnalysisJob(r.Context(), world, r.Header.Get("X-Portal-Actor"), true, forceFull); failure != nil {
		http.Error(w, failure.client, failure.status)
		return
	}
	http.Redirect(w, r, "/admin/worlds/"+world+"/map", http.StatusSeeOther)
}

func (s *Server) ensureInitialWorldMap(ctx context.Context, world, actor string) *worldAnalysisFailure {
	snapshots, err := s.store.LatestWorldAnalyses(ctx, world, 1)
	if err != nil {
		return &worldAnalysisFailure{status: http.StatusServiceUnavailable, client: "unable to check existing world map", detail: "analysis lookup failed"}
	}
	if len(snapshots) != 0 {
		return nil
	}
	return s.runWorldAnalysisJob(ctx, world, actor, true, true)
}

func (s *Server) runWorldAnalysisJob(ctx context.Context, world, actor string, backupFirst, forceFull bool) *worldAnalysisFailure {
	jobID := randomID()
	if err := s.store.CreateJob(ctx, Job{ID: jobID, World: world, Operation: "world_analysis", Status: "queued", RequestedBy: actor}, actor); err != nil {
		return &worldAnalysisFailure{status: http.StatusInternalServerError, client: "unable to queue analysis", detail: "analysis queue failed"}
	}
	if backupFirst {
		reply, err := s.agent.Run(ctx, jobID+"-backup", world, "backup")
		if err != nil || reply.Status != "succeeded" {
			_ = s.store.FinishJob(ctx, jobID, "failed", "initial backup failed", actor)
			status := http.StatusConflict
			if err != nil {
				status = http.StatusBadGateway
			}
			return &worldAnalysisFailure{status: status, client: "unable to create the first complete world backup", detail: "initial backup failed"}
		}
	}
	return s.executeWorldAnalysisJob(ctx, world, actor, jobID, forceFull)
}

func (s *Server) executeWorldAnalysisJob(ctx context.Context, world, actor, jobID string, forceFull bool) *worldAnalysisFailure {
	reply, err := s.agent.Run(ctx, jobID, world, "world_analysis")
	if err != nil || reply.Status != "succeeded" {
		_ = s.store.FinishJob(ctx, jobID, "failed", "analysis agent failed", actor)
		return &worldAnalysisFailure{status: http.StatusBadGateway, client: "world analysis failed", detail: "analysis agent failed"}
	}
	var snapshot worldintel.Snapshot
	if len(reply.Data) > 4<<20 || json.Unmarshal(reply.Data, &snapshot) != nil || snapshot.World != world || snapshot.Schema != worldintel.SchemaVersion {
		_ = s.store.FinishJob(ctx, jobID, "failed", "invalid analysis result", actor)
		return &worldAnalysisFailure{status: http.StatusBadGateway, client: "invalid analysis result", detail: "invalid analysis result"}
	}
	// Terrain is a function of the seed and the worldgen version, so building a house cannot change
	// it. When the tiles on disk already describe this world at this seed and version, the two
	// expensive steps - rendering biome.png and height.png at 12288px, then cutting the terrain
	// pyramid - are recomputing an identical answer. Skipping them is what makes "show me what we
	// just built" seconds instead of minutes; only the object overlay is rebuilt.
	if terrain, reusable := s.reusableTerrain(world, snapshot); reusable && !forceFull {
		if failure := s.publishObjectsOnly(ctx, terrain, snapshot, actor); failure != nil {
			_ = s.store.FinishJob(ctx, jobID, "failed", failure.detail, actor)
			return failure
		}
		_ = s.store.FinishJob(ctx, jobID, "succeeded", "constructions updated from "+snapshot.Source.Backup+"; terrain unchanged", actor)
		return nil
	}
	mapReply, mapErr := s.agent.Run(ctx, jobID+"-map", world, "world_map")
	if mapErr != nil || mapReply.Status != "succeeded" {
		_ = s.store.FinishJob(ctx, jobID, "failed", "map source generation failed", actor)
		return &worldAnalysisFailure{status: http.StatusBadGateway, client: "world analysis succeeded but map source generation failed", detail: "map source generation failed"}
	}
	if failure := s.mapPublisher(ctx, snapshot, actor); failure != nil {
		_ = s.store.FinishJob(ctx, jobID, "failed", failure.detail, actor)
		return failure
	}
	_ = s.store.FinishJob(ctx, jobID, "succeeded", "full rebuild from "+snapshot.Source.Backup, actor)
	return nil
}

// reusableTerrain reports the tile set already on disk when it describes this exact world, so the
// terrain half of a refresh can be skipped. A different seed, a different worldgen version, or a
// renderer change all fall through to the full rebuild, because then the tiles really are wrong.
func (s *Server) reusableTerrain(world string, snapshot worldintel.Snapshot) (maptiles.Manifest, bool) {
	path, err := maptiles.CurrentManifestPath(s.cfg.MapRoot, world)
	if err != nil {
		return maptiles.Manifest{}, false
	}
	manifest, err := maptiles.LoadManifest(path)
	if err != nil {
		return maptiles.Manifest{}, false
	}
	if manifest.World != world || manifest.Seed != snapshot.Seed || manifest.WorldGenVersion != int(snapshot.WorldVersion) {
		return maptiles.Manifest{}, false
	}
	return manifest, true
}

// publishObjectsOnly saves the snapshot and rebuilds the overlay against terrain that is already
// correct. The order matters: the overlay is what an operator is waiting to see, but the snapshot is
// the record, so it is written first and a failed overlay is reported rather than swallowed.
func (s *Server) publishObjectsOnly(ctx context.Context, manifest maptiles.Manifest, snapshot worldintel.Snapshot, actor string) *worldAnalysisFailure {
	if err := s.store.SaveWorldAnalysis(ctx, snapshot, actor); err != nil {
		slog.Error("world analysis persistence failed", "world", snapshot.World, "backup", snapshot.Source.Backup, "error", err)
		return &worldAnalysisFailure{status: http.StatusInternalServerError, client: "unable to persist analysis", detail: "analysis persistence failed"}
	}
	if _, err := maptiles.BuildOverlayPyramid(ctx, s.cfg.MapRoot, manifest, snapshot); err != nil {
		return &worldAnalysisFailure{status: http.StatusInternalServerError, client: "analysis persisted but overlay tile generation failed", detail: "overlay tile generation failed"}
	}
	return nil
}

func (s *Server) publishWorldAnalysis(ctx context.Context, snapshot worldintel.Snapshot, actor string) *worldAnalysisFailure {
	sourceRoot := filepath.Join(s.cfg.MapSourceRoot, snapshot.World, "map_sources", "current")
	manifest, err := maptiles.Build(ctx, s.cfg.MapRoot, filepath.Join(sourceRoot, "biome.png"), maptiles.BuildOptions{
		World: snapshot.World, Seed: snapshot.Seed, WorldGenVersion: int(snapshot.WorldVersion),
		Size: maptiles.DefaultSize, Workers: 8, HeightPath: filepath.Join(sourceRoot, "height.png"),
	})
	if err != nil {
		return &worldAnalysisFailure{status: http.StatusInternalServerError, client: "world analysis succeeded but terrain tile generation failed", detail: "terrain tile generation failed"}
	}
	if err := s.store.SaveWorldAnalysis(ctx, snapshot, actor); err != nil {
		slog.Error("world analysis persistence failed", "world", snapshot.World, "backup", snapshot.Source.Backup, "error", err)
		return &worldAnalysisFailure{status: http.StatusInternalServerError, client: "unable to persist analysis", detail: "analysis persistence failed"}
	}
	if _, err := maptiles.BuildOverlayPyramid(ctx, s.cfg.MapRoot, manifest, snapshot); err != nil {
		return &worldAnalysisFailure{status: http.StatusInternalServerError, client: "analysis persisted but overlay tile generation failed", detail: "overlay tile generation failed"}
	}
	return nil
}

func (s *Server) worldAnalysisJSON(w http.ResponseWriter, r *http.Request) {
	world := r.PathValue("world")
	if !validWorld(world) {
		http.NotFound(w, r)
		return
	}
	snapshots, err := s.store.LatestWorldAnalyses(r.Context(), world, 2)
	if err != nil {
		http.Error(w, "world analysis unavailable", 503)
		return
	}
	if len(snapshots) == 0 {
		http.NotFound(w, r)
		return
	}
	snapshot := snapshots[0]
	if r.URL.Query().Get("summary") == "1" {
		snapshot.GeneratedZones = nil
		snapshot.Locations = nil
		snapshot.Clusters = nil
		snapshot.ConstructionCoverage = nil
		snapshot.Objects = nil
	}
	var diff *worldintel.Diff
	if len(snapshots) > 1 {
		diff = worldintel.Compare(snapshots[1], snapshots[0])
	}
	response := struct {
		Snapshot        worldintel.Snapshot `json:"snapshot"`
		Diff            *worldintel.Diff    `json:"diff,omitempty"`
		Recommendations []string            `json:"recommendations"`
	}{snapshot, diff, worldintel.Recommendations(snapshots[0], diff)}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	json.NewEncoder(w).Encode(response)
}
func (s *Server) worldTerrainManifest(w http.ResponseWriter, r *http.Request) {
	world := r.PathValue("world")
	if !validWorld(world) {
		http.NotFound(w, r)
		return
	}
	if _, err := s.store.PublicWorld(r.Context(), world); err != nil {
		http.NotFound(w, r)
		return
	}
	path, err := maptiles.CurrentManifestPath(s.cfg.MapRoot, world)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	manifest, err := maptiles.LoadManifest(path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			http.NotFound(w, r)
		} else {
			http.Error(w, "terrain manifest unavailable", http.StatusServiceUnavailable)
		}
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "private, no-cache")
	_ = json.NewEncoder(w).Encode(manifest)
}

func (s *Server) worldTerrainTile(w http.ResponseWriter, r *http.Request) {
	world := r.PathValue("world")
	if !validWorld(world) {
		http.NotFound(w, r)
		return
	}
	manifestPath, err := maptiles.CurrentManifestPath(s.cfg.MapRoot, world)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	manifest, err := maptiles.LoadManifest(manifestPath)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	if r.PathValue("key") != manifest.Key {
		http.NotFound(w, r)
		return
	}
	zoom, zoomErr := strconv.Atoi(r.PathValue("zoom"))
	x, xErr := strconv.Atoi(r.PathValue("x"))
	yValue := r.PathValue("y")
	if !strings.HasSuffix(yValue, ".png") {
		http.NotFound(w, r)
		return
	}
	y, yErr := strconv.Atoi(strings.TrimSuffix(yValue, ".png"))
	if zoomErr != nil || xErr != nil || yErr != nil {
		http.NotFound(w, r)
		return
	}
	path, err := maptiles.TilePath(s.cfg.MapRoot, manifest, zoom, x, y)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	etag := `"` + manifest.TileETags[strconv.Itoa(zoom)+"/"+strconv.Itoa(x)+"/"+strconv.Itoa(y)] + `"`
	if etag != `""` {
		w.Header().Set("ETag", etag)
		if r.Header.Get("If-None-Match") == etag {
			w.WriteHeader(http.StatusNotModified)
			return
		}
	}
	w.Header().Set("Cache-Control", "private, max-age=31536000, immutable")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	http.ServeFile(w, r, path)
}
func (s *Server) worldOverlayTile(w http.ResponseWriter, r *http.Request) {
	world := r.PathValue("world")
	if !validWorld(world) {
		http.NotFound(w, r)
		return
	}
	manifestPath, err := maptiles.CurrentManifestPath(s.cfg.MapRoot, world)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	manifest, err := maptiles.LoadManifest(manifestPath)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	zoom, zoomErr := strconv.Atoi(r.PathValue("zoom"))
	x, xErr := strconv.Atoi(r.PathValue("x"))
	yValue := r.PathValue("y")
	if !strings.HasSuffix(yValue, ".json") {
		http.NotFound(w, r)
		return
	}
	y, yErr := strconv.Atoi(strings.TrimSuffix(yValue, ".json"))
	if zoomErr != nil || xErr != nil || yErr != nil || zoom < 0 || zoom > manifest.MaxZoom {
		http.NotFound(w, r)
		return
	}
	snapshots, err := s.store.LatestWorldAnalyses(r.Context(), world, 1)
	if err != nil {
		http.Error(w, "world analysis unavailable", http.StatusServiceUnavailable)
		return
	}
	if len(snapshots) == 0 {
		http.NotFound(w, r)
		return
	}
	if r.PathValue("source") != snapshots[0].Source.SHA256 {
		http.NotFound(w, r)
		return
	}
	etag := `"` + snapshots[0].Source.SHA256 + "-" + strconv.Itoa(zoom) + "-" + strconv.Itoa(x) + "-" + strconv.Itoa(y) + `"`
	w.Header().Set("ETag", etag)
	w.Header().Set("Cache-Control", "private, no-cache")
	if r.Header.Get("If-None-Match") == etag {
		w.WriteHeader(http.StatusNotModified)
		return
	}
	if path, err := maptiles.OverlayPath(s.cfg.MapRoot, manifest, snapshots[0].Source.SHA256, zoom, x, y); err == nil {
		if _, err := os.Stat(path); err == nil {
			w.Header().Set("X-Content-Type-Options", "nosniff")
			http.ServeFile(w, r, path)
			return
		}
	}
	tile, ok := maptiles.SelectOverlay(snapshots[0], manifest.Levels[zoom], x, y)
	if !ok {
		http.NotFound(w, r)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(tile)
}

const worldAnalysisTemplate = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{.World.Name}} world map and analysis</title>
<link rel="stylesheet" href="/assets/site.css">
</head>
<body class="world-map-page" data-world="{{.World.Name}}">
<header class="map-header">
<a class="map-back" href="/admin">Administration</a>
<div class="map-heading">
<h1>{{.World.Name}} world map and analysis</h1>
{{if .HaveAnalysis}}<div class="map-snapshot"><span class="pill">{{.Backup}}</span><span class="muted">{{.AnalyzedAt}}</span></div>{{end}}
</div>
<form class="map-analysis-form" method="post" action="/admin/worlds/{{.World.Name}}/analysis">
<input type="hidden" name="csrf" value="{{.CSRF}}">
<button type="submit">Update constructions</button>
<button type="submit" name="rebuild" value="terrain" class="secondary">Rebuild terrain too</button>
</form>
</header>
<main class="map-layout">
<aside class="map-sidebar map-controls" aria-label="Map controls">
<fieldset>
<legend>Map layers</legend>
<label class="map-layer"><input type="checkbox" data-layer="terrain" checked {{if not .HaveAnalysis}}disabled{{end}}><span>Terrain and biomes</span></label>
<label class="map-layer"><input type="checkbox" data-layer="zones" {{if not .HaveAnalysis}}disabled{{end}}><span>Generated zones</span></label>
<label class="map-layer"><input type="checkbox" data-layer="locations" checked {{if not .HaveAnalysis}}disabled{{end}}><span>Locations</span></label>
<label class="map-layer"><input type="checkbox" data-layer="clusters" checked {{if not .HaveAnalysis}}disabled{{end}}><span>Player construction</span></label>
<label class="map-layer"><input type="checkbox" data-layer="portal" checked {{if not .HaveAnalysis}}disabled{{end}}><span>Portals</span></label>
<label class="map-layer"><input type="checkbox" data-layer="container" {{if not .HaveAnalysis}}disabled{{end}}><span>Containers</span></label>
<label class="map-layer"><input type="checkbox" data-layer="production" {{if not .HaveAnalysis}}disabled{{end}}><span>Production</span></label>
<label class="map-layer"><input type="checkbox" data-layer="creature" {{if not .HaveAnalysis}}disabled{{end}}><span>Persistent creatures</span></label>
<label class="map-layer"><input type="checkbox" data-layer="terrain-risk" checked {{if not .HaveAnalysis}}disabled{{end}}><span>Terrain / upgrade risk</span></label>
<label class="map-layer"><input type="checkbox" data-layer="other" {{if not .HaveAnalysis}}disabled{{end}}><span>Connected / uncatalogued</span></label>
</fieldset>
<fieldset class="map-location-categories" {{if not .HaveAnalysis}}disabled{{end}}>
<legend>Location categories</legend>
<label class="map-layer"><input type="checkbox" data-location-category="spawn" checked><span>World spawn</span></label>
<label class="map-layer"><input type="checkbox" data-location-category="boss" checked><span>Bosses</span></label>
<label class="map-layer"><input type="checkbox" data-location-category="trader" checked><span>Traders</span></label>
<label class="map-layer"><input type="checkbox" data-location-category="dungeon"><span>Dungeons</span></label>
<label class="map-layer"><input type="checkbox" data-location-category="fortress"><span>Fortresses</span></label>
<label class="map-layer"><input type="checkbox" data-location-category="settlement"><span>Settlements</span></label>
<label class="map-layer"><input type="checkbox" data-location-category="resource"><span>Resources</span></label>
<label class="map-layer"><input type="checkbox" data-location-category="landmark"><span>Landmarks</span></label>
<label class="map-layer"><input type="checkbox" data-location-category="other"><span>Other locations</span></label>
</fieldset>
<fieldset>
<legend>Display</legend>
<p class="muted">Terrain detail follows the zoom level automatically.</p>
<div class="map-button-group" aria-label="Map zoom">
<button id="zoom-out" type="button" aria-label="Zoom out" {{if not .HaveAnalysis}}disabled{{end}}>−</button>
<button id="fit" type="button" {{if not .HaveAnalysis}}disabled{{end}}>Fit world</button>
<button id="zoom-in" type="button" aria-label="Zoom in" {{if not .HaveAnalysis}}disabled{{end}}>+</button>
</div>
<output id="zoom-level" class="map-scale">Map scale</output>
<p id="coverage-note" class="muted">Player-build coverage appears at close zoom.</p>
</fieldset>
<div class="map-legend" aria-labelledby="map-legend-title">
<h2 id="map-legend-title">Legend</h2>
<ul>
<li><span class="map-key map-key--terrain" aria-hidden="true">≈</span>Biome texture</li>
<li><span class="map-key map-key--zone" aria-hidden="true">▦</span>Generated zone</li>
<li><span class="map-key map-key--location-boss" aria-hidden="true">★</span>Boss</li>
<li><span class="map-key map-key--location-trader" aria-hidden="true">¤</span>Trader</li>
<li><span class="map-key map-key--location-dungeon" aria-hidden="true">∩</span>Dungeon</li>
<li><span class="map-key map-key--location-fortress" aria-hidden="true">▥</span>Fortress</li>
<li><span class="map-key map-key--location-settlement" aria-hidden="true">⌂</span>Settlement</li>
<li><span class="map-key map-key--location-resource" aria-hidden="true">◈</span>Resource</li>
<li><span class="map-key map-key--location-landmark" aria-hidden="true">◆</span>Landmark</li>
<li><span class="map-key map-key--location-other" aria-hidden="true">•</span>Other location</li>
<li><span class="map-key map-key--build" aria-hidden="true">⌂</span>Build cluster / coverage</li>
<li><span class="map-key map-key--portal" aria-hidden="true">◎</span>Portal</li>
<li><span class="map-key map-key--container" aria-hidden="true">▣</span>Container</li>
<li><span class="map-key map-key--production" aria-hidden="true">▲</span>Production</li>
<li><span class="map-key map-key--creature" aria-hidden="true">●</span>Persistent creature</li>
<li><span class="map-key map-key--risk" aria-hidden="true">!</span>Terrain risk</li>
<li><span class="map-key map-key--other" aria-hidden="true">?</span>Connected / unknown</li>
</ul>
</div>
<div class="map-category-summary" aria-labelledby="category-summary-title">
<h2 id="category-summary-title">Analysis coverage</h2>
<dl id="category-summary"><div><dt>Status</dt><dd>Waiting for analysis</dd></div></dl>
</div>
</aside>
<section class="map" aria-labelledby="map-title">
<h2 id="map-title" class="visually-hidden">{{.World.Name}} interactive map</h2>
<canvas id="world-map" tabindex="0" aria-label="Interactive Valheim world map" aria-describedby="map-help map-status"></canvas>
<p id="map-help" class="visually-hidden">Drag or use arrow keys to pan. Use the mouse wheel, plus, and minus keys to zoom around the pointer or map center. Select a glyph for exact coordinates and save-state details.</p>
<output class="coords" id="coords">x 0 · z 0</output>
<div id="map-status" class="map-status" role="status" aria-live="polite">{{if .HaveAnalysis}}Loading world analysis…{{else}}No persisted analysis. Run the read-only analysis above.{{end}}</div>
</section>
<aside class="map-sidebar map-inspector" aria-label="World analysis details">
<h2>Selection</h2>
<div id="details" class="details muted">Select a marker for exact coordinates and save-state details.</div>
<h2>Health</h2>
<div id="health" class="details muted">Waiting for analysis.</div>
<h2>Backup diff</h2>
<div id="diff" class="details muted">No consecutive persisted snapshot yet.</div>
<h2>Upgrade plan</h2>
<ul id="recommendations"></ul>
</aside>
</main>
{{if .HaveAnalysis}}<script src="/assets/world-map.js?v=map-pyramid-v1" defer></script>{{end}}
</body>
</html>`
