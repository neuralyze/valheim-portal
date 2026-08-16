package app

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"sort"
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
	// Explored is how much of the playable map players have actually visited, phrased for a legend.
	// Valheim generates a zone only when somebody has been near it, and a player's own revealed map
	// lives in their character file on their machine - so generated zones are the only server-side
	// answer to "how much have we discovered".
	Explored string
	// Builders is every creator id the snapshot found, with the name an operator gave it, how many
	// pieces it placed, and the colour the map draws it in. Valheim stamps a player id on each
	// piece and nothing resolves that to a person - names live in client character files - so the
	// operator names each one once and the portal remembers.
	Builders []pageBuilder
	// LabelsJSON carries each builder's name and colour as JSON, so the canvas can label and colour a
	// cluster without a
	// second request.
	LabelsJSON string
	// DataBase is where the renderer fetches tiles, overlays and the analysis from: the admin route
	// for the operator's map, the world route for the players'. One renderer, two hosts - a second
	// copy of it would drift from this one the first time either changed.
	DataBase string
	// Fog covers ground nobody has visited. The operator's map shows the whole world; the players'
	// map shows what the server knows they have seen.
	Fog bool
	// Admin gates the controls that act on the world - refreshing, rebuilding, naming a builder.
	// The players' map is the same view without the levers.
	Admin bool
}

type pageBuilder struct {
	// Named separates a name an operator chose from the id-derived stand-in. Valheim stamps only a
	// number on a piece, nothing on the server maps it to a person, so a stand-in must never be
	// presented, stored or exported as if somebody had confirmed it.
	Named    bool
	Creator  int64
	Label    string
	Pieces   int
	Clusters int
	Colour   string
	// FocusX and FocusZ are the centre of this builder's largest site. A colour on a 20 km map is
	// not findable by eye, so the legend can take the operator there instead.
	FocusX  float32
	FocusZ  float32
	Locable bool
	// Nameable is false for creator 0. That row is the absence of a record, not a person, so the
	// legend shows what it is instead of a text field.
	Nameable bool
	// Reported marks a name the game itself supplied through the identity plugin, rather than one an
	// operator typed. Worth showing: it is the difference between evidence and somebody's note.
	Reported bool
}

// builderColours is the only palette. The page serves each builder's colour to the canvas alongside
// its name, so the legend and the map cannot disagree about who owns a base; the script keeps a fold
// of its own only for an id the page never listed.
var builderColours = []string{"#6f9ad6", "#71c492", "#d9a514", "#c46f9a", "#7ad6cf", "#d6a06f", "#a58fd6", "#8fd66f"}

func builderColour(creator int64) string {
	if creator == 0 {
		return "#9aa8a0"
	}
	n := creator % 100000
	if n < 0 {
		n = -n
	}
	return builderColours[n%int64(len(builderColours))]
}

func builderFallbackName(creator int64) string {
	if creator == 0 {
		// Valheim leaves the stamp empty on generated structures and on pieces whose builder was
		// never recorded. There is no id to name, so the row says so rather than inventing one.
		return "no builder recorded"
	}
	n := creator % 10000
	if n < 0 {
		n = -n
	}
	return fmt.Sprintf("builder %04d", n)
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
	page := worldAnalysisPage{
		World:        info,
		HaveAnalysis: len(snapshots) > 0,
		CSRF:         s.csrfCookie(w, r),
		LabelsJSON:   "{}",
		DataBase:     "/admin/worlds/" + world,
		Admin:        true,
	}
	if len(snapshots) > 0 {
		page.Backup = snapshots[0].Source.Backup
		page.AnalyzedAt = snapshots[0].Source.ModifiedAt.Format("2006-01-02 15:04 UTC")
		page.Explored = formatExplored(snapshots[0].Summary)
		page.Builders, page.LabelsJSON = s.builderLegend(r.Context(), world, snapshots[0])
	}
	render(w, worldAnalysisTemplate, page)
}

// formatExplored phrases the explored measure for a legend. Empty when nothing has been analysed, so
// the page says nothing rather than claiming 0%.
func formatExplored(summary worldintel.Summary) string {
	if summary.ExploredZones == 0 {
		return ""
	}
	return fmt.Sprintf("%.1f%% (%.1f km²)", summary.ExploredPercent, summary.ExploredSquareKm)
}

// builderLegend turns the clusters in a snapshot into legend rows and the styles the canvas draws
// with. Both maps call it, so the players' map cannot end up with a different palette or a different
// set of builders from the operator's: it is the same function over a differently clipped snapshot.
func (s *Server) builderLegend(ctx context.Context, world string, snapshot worldintel.Snapshot) ([]pageBuilder, string) {
	labels, labelErr := s.store.BuilderLabels(ctx, world)
	if labelErr != nil {
		labels = map[int64]string{}
	}
	// What the game itself reported, if the identity plugin is running on that server. An operator's
	// own label still wins: they may want a different name than the character currently uses, and a
	// character can be renamed while the id on its pieces stays the same.
	reported := s.reportedPlayerNames(world)
	pieces, clusters := map[int64]int{}, map[int64]int{}
	// The largest site is where a builder is most worth looking at, and it is the one place a legend
	// row can send the view that is certain to have something on it.
	largest := map[int64]worldintel.Cluster{}
	for _, cluster := range snapshot.Clusters {
		pieces[cluster.Creator] += cluster.Pieces
		clusters[cluster.Creator]++
		if best, seen := largest[cluster.Creator]; !seen || cluster.Pieces > best.Pieces {
			largest[cluster.Creator] = cluster
		}
	}
	// Both halves of what the canvas needs: what to call a builder and what colour to draw it.
	styles := map[string]map[string]string{}
	rows := make([]pageBuilder, 0, len(pieces))
	for creator, count := range pieces {
		entry := pageBuilder{
			Creator:  creator,
			Pieces:   count,
			Clusters: clusters[creator],
			Colour:   builderColour(creator),
			Nameable: creator != 0,
		}
		if site, ok := largest[creator]; ok {
			entry.FocusX, entry.FocusZ, entry.Locable = site.Center.X, site.Center.Z, true
		}
		switch label, chosen := labels[creator]; {
		case chosen:
			entry.Label = label
			entry.Named = true
		default:
			if name, ok := reported[creator]; ok {
				entry.Label = name
				entry.Named = true
				entry.Reported = true
			} else {
				entry.Label = builderFallbackName(creator)
			}
		}
		// The fallback goes to the canvas too, but flagged, so the map can draw "builder 9451"
		// without the script pretending that is a name somebody chose.
		style := map[string]string{"colour": entry.Colour, "name": entry.Label}
		if !entry.Named {
			style["unnamed"] = "1"
		}
		styles[strconv.FormatInt(creator, 10)] = style
		rows = append(rows, entry)
	}
	sort.Slice(rows, func(i, j int) bool {
		if rows[i].Pieces != rows[j].Pieces {
			return rows[i].Pieces > rows[j].Pieces
		}
		return rows[i].Creator < rows[j].Creator
	})
	encoded, err := json.Marshal(styles)
	if err != nil {
		return rows, "{}"
	}
	return rows, string(encoded)
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
<title>{{.World.Name}} {{if .Admin}}world map and analysis{{else}}map{{end}}</title>
<link rel="stylesheet" href="/assets/site.css">
</head>
<body class="world-map-page" data-world="{{.World.Name}}" data-map-base="{{.DataBase}}"{{if .Fog}} data-map-fog="1"{{end}}>
<header class="map-header">
<a class="map-back" href="{{if .Admin}}/admin{{else}}/worlds/{{.World.Name}}{{end}}">{{if .Admin}}Administration{{else}}{{.World.Name}}{{end}}</a>
<div class="map-heading">
<h1>{{.World.Name}} {{if .Admin}}world map and analysis{{else}}map{{end}}</h1>
{{if .HaveAnalysis}}<div class="map-snapshot">{{if .Admin}}<span class="pill">{{.Backup}}</span>{{end}}<span class="muted">{{.AnalyzedAt}}</span>{{if not .Admin}}<span class="muted">· {{.Explored}} of the world visited</span>{{end}}</div>{{end}}
</div>
{{if .Admin}}<form class="map-analysis-form" method="post" action="/admin/worlds/{{.World.Name}}/analysis">
<input type="hidden" name="csrf" value="{{.CSRF}}">
<button type="submit">Update constructions</button>
<button type="submit" name="rebuild" value="terrain" class="secondary">Rebuild terrain too</button>
</form>{{end}}
</header>
<main class="map-layout">
<aside class="map-sidebar map-controls" aria-label="Map controls">
{{if .Builders}}<fieldset class="map-builders">
<legend>Builders</legend>
{{if .Admin}}<p class="map-hint">Valheim stamps a player id on every piece, and nothing resolves that to a person - character names live on each player's own machine. Name one here and the map remembers it.</p>{{end}}
{{range .Builders}}<details class="map-builder"{{if not .Nameable}} data-unnameable="1"{{end}}>
<summary>
<span class="map-builder-swatch" style="background:{{.Colour}}"></span>
<span class="map-builder-name{{if not .Named}} map-builder-unnamed{{end}}"{{if .Reported}} title="the character name the game reported for this player id"{{end}}>{{.Label}}{{if .Reported}} <span class="map-builder-source">from the server</span>{{end}}</span>
<span class="map-builder-count">{{.Pieces}} pieces · {{.Clusters}} site(s)</span>
{{if .Locable}}<button type="button" class="map-builder-locate" data-locate="{{.Creator}}" data-locate-x="{{.FocusX}}" data-locate-z="{{.FocusZ}}" title="Take the map to this builder's largest site">Show</button>{{end}}
</summary>
{{if and .Nameable $.Admin}}<form method="post" action="/admin/worlds/{{$.World.Name}}/builders">
<input type="hidden" name="csrf" value="{{$.CSRF}}">
<input type="hidden" name="creator" value="{{.Creator}}">
<label>{{if .Reported}}Call this player something else{{else}}Name for player id {{.Creator}}{{end}}
<input type="text" name="label" value="{{if .Named}}{{.Label}}{{end}}" maxlength="40" placeholder="nobody has named this builder">
</label>
<button type="submit">Save</button>
</form>{{else if not .Nameable}}<p class="map-hint">These pieces carry no player id at all - Valheim leaves the stamp empty on generated structures and on anything whose builder was never recorded.{{if $.Admin}} There is nobody here to name.{{end}}</p>{{end}}
</details>{{end}}
</fieldset>{{end}}
<fieldset>
<legend>Map layers</legend>
<label class="map-layer"><input type="checkbox" data-layer="terrain" checked {{if not .HaveAnalysis}}disabled{{end}}><span>Terrain and biomes</span></label>
<label class="map-layer"><input type="checkbox" data-layer="zones" {{if not .HaveAnalysis}}disabled{{end}}><span>Explored area{{if .Explored}} · {{.Explored}}{{end}}</span></label>
<label class="map-layer"><input type="checkbox" data-layer="locations" checked {{if not .HaveAnalysis}}disabled{{end}}><span>Locations</span></label>
{{if not .Admin}}<label class="map-layer"><input type="checkbox" data-layer="pins" checked {{if not .HaveAnalysis}}disabled{{end}}><span>Player pins</span></label>{{end}}
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
{{if .HaveAnalysis}}<script src="/assets/builder-labels.js" data-labels="{{.LabelsJSON}}"></script>
<script src="/assets/world-map.js?v=map-pyramid-v1" defer></script>{{end}}
</body>
</html>`

// nameBuilder records what the operator calls one builder, or clears the name when the field is
// emptied. The id itself is never editable: it is what Valheim stamped on the pieces, and the label is
// only the portal's note about who that is.
func (s *Server) nameBuilder(w http.ResponseWriter, r *http.Request) {
	world := r.PathValue("world")
	if !validWorld(world) {
		http.NotFound(w, r)
		return
	}
	if _, err := s.store.PublicWorld(r.Context(), world); err != nil {
		http.NotFound(w, r)
		return
	}
	if err := r.ParseForm(); err != nil {
		http.Error(w, "invalid form", http.StatusBadRequest)
		return
	}
	creator, err := strconv.ParseInt(strings.TrimSpace(r.FormValue("creator")), 10, 64)
	if err != nil {
		http.Error(w, "invalid builder id", http.StatusBadRequest)
		return
	}
	// Creator 0 is the absence of a stamp, not a player. Hiding the field on the page is presentation;
	// this is the fence, because a stored name here would put a person's name on generated structures.
	if creator == 0 {
		http.Error(w, "those pieces carry no player id, so there is nobody to name", http.StatusBadRequest)
		return
	}
	actor := r.Header.Get("X-Portal-Actor")
	label := r.FormValue("label")
	// A label the operator did not change is the fallback the page rendered, and storing that would
	// turn a placeholder into an assertion about who built something.
	if strings.TrimSpace(label) == builderFallbackName(creator) {
		label = ""
	}
	if err := s.store.SetBuilderLabel(r.Context(), world, creator, label, actor); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	_ = s.store.Audit(r.Context(), actor, "world.builder.named", world, fmt.Sprintf("%d = %q", creator, strings.TrimSpace(label)))
	http.Redirect(w, r, "/admin/worlds/"+world+"/map", http.StatusSeeOther)
}
