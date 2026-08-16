package app

import (
	"encoding/json"
	"net/http"
	"strconv"
	"strings"

	"github.com/neuralyze/valheim-portal/internal/maptiles"
	"github.com/neuralyze/valheim-portal/internal/worldintel"
)

// The players' map. Same renderer, same tiles, same overlay data as the operator's map - it is not a
// different product and nothing is redacted. Two differences only: it lives off the admin site,
// behind the Steam sign-in that already guards every world page, and it shows the world as the
// server knows the players have seen it.
//
// "Discovered" has exactly one server-side meaning: Valheim creates a zone when somebody has been
// near it, so the generated-zone list is the record of where players have gone. Each player's own
// revealed map lives in their character file on their own machine and never reaches the server, so
// this is one shared fog for the whole world rather than a per-player one.

// discoveredZones is the set of 64 m cells players have been to, keyed for lookup.
type discoveredZones map[[2]int]struct{}

func newDiscoveredZones(zones []worldintel.Vec2) discoveredZones {
	seen := make(discoveredZones, len(zones))
	for _, zone := range zones {
		seen[[2]int{zone.X, zone.Y}] = struct{}{}
	}
	return seen
}

func (d discoveredZones) has(x, z float32) bool {
	_, ok := d[[2]int{zoneIndex(x), zoneIndex(z)}]
	return ok
}

// zoneIndex mirrors the game's own ZoneSystem.GetZone: divide by the zone size and round to nearest,
// which is not the same as truncating - a piece 10 m west of the origin belongs to zone 0, not -1.
func zoneIndex(v float32) int {
	const zoneSize = 64.0
	if v < 0 {
		return int((float64(v) - zoneSize/2) / zoneSize)
	}
	return int((float64(v) + zoneSize/2) / zoneSize)
}

// discoveredArea answers one question - may a player see this coordinate - and it must be the same
// answer the fog is drawn from. The fog moved to the players' own reported maps while the markers were
// still clipped to generated zones, so settlement clusters and a boss site were drawn standing on
// ground the map had fogged over. One source, both decisions.
type discoveredArea struct {
	// Preferred: the union of what players reported from their own minimaps, at explorationCellSize.
	bits []byte
	size int
	// Fallback for a world where nobody has reported yet: the zones the server generated.
	zones discoveredZones
}

func (d discoveredArea) has(x, z float32) bool {
	if d.bits != nil {
		cellX := int((float64(x) + float64(explorationRadius)) / float64(explorationCellSize))
		cellZ := int((float64(z) + float64(explorationRadius)) / float64(explorationCellSize))
		if cellX < 0 || cellX >= d.size || cellZ < 0 || cellZ >= d.size {
			return false
		}
		index := cellZ*d.size + cellX
		return d.bits[index>>3]&(1<<(index&7)) != 0
	}
	return d.zones.has(x, z)
}

// discoveredFor prefers what the players reported and falls back to generated zones, which is what a
// world sees until somebody runs the reporter.
func (s *Server) discoveredFor(world string, snapshot worldintel.Snapshot) discoveredArea {
	if bits, size, players := s.explorationUnionBits(world); players > 0 {
		return discoveredArea{bits: bits, size: size}
	}
	return discoveredArea{zones: newDiscoveredZones(snapshot.GeneratedZones)}
}

// clipToDiscovered keeps everything the operator's map would show, minus whatever sits on ground
// nobody has visited. Fields are untouched: this decides where, never what.
func clipToDiscovered(snapshot worldintel.Snapshot, seen discoveredArea) worldintel.Snapshot {

	locations := make([]worldintel.Location, 0, len(snapshot.Locations))
	for _, location := range snapshot.Locations {
		if seen.has(location.Position.X, location.Position.Z) {
			locations = append(locations, location)
		}
	}
	snapshot.Locations = locations

	objects := make([]worldintel.Object, 0, len(snapshot.Objects))
	for _, object := range snapshot.Objects {
		if seen.has(object.Position.X, object.Position.Z) {
			objects = append(objects, object)
		}
	}
	snapshot.Objects = objects

	clusters := make([]worldintel.Cluster, 0, len(snapshot.Clusters))
	for _, cluster := range snapshot.Clusters {
		if seen.has(cluster.Center.X, cluster.Center.Z) {
			clusters = append(clusters, cluster)
		}
	}
	snapshot.Clusters = clusters

	if coverage := snapshot.ConstructionCoverage; coverage != nil {
		cells := make([]worldintel.CoverageCell, 0, len(coverage.Cells))
		for _, cell := range coverage.Cells {
			centreX := float32((float64(cell.X) + 0.5) * float64(coverage.CellSize))
			centreZ := float32((float64(cell.Z) + 0.5) * float64(coverage.CellSize))
			if seen.has(centreX, centreZ) {
				cells = append(cells, cell)
			}
		}
		clipped := *coverage
		clipped.Cells = cells
		snapshot.ConstructionCoverage = &clipped
	}
	return snapshot
}

func (s *Server) playerWorldMap(w http.ResponseWriter, r *http.Request) {
	world := r.PathValue("world")
	if !s.requireWorldAccess(w, r, world) {
		return
	}
	info, err := s.store.PublicWorld(r.Context(), world)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	snapshots, err := s.store.LatestWorldAnalyses(r.Context(), world, 1)
	if err != nil {
		http.Error(w, "world map unavailable", http.StatusServiceUnavailable)
		return
	}
	page := worldAnalysisPage{
		World:        info,
		HaveAnalysis: len(snapshots) > 0,
		LabelsJSON:   "{}",
		DataBase:     "/worlds/" + world,
		Fog:          true,
	}
	if len(snapshots) > 0 {
		clipped := clipToDiscovered(snapshots[0], s.discoveredFor(world, snapshots[0]))
		page.AnalyzedAt = snapshots[0].Source.ModifiedAt.Format("2006-01-02 15:04 UTC")
		page.Explored = formatExplored(snapshots[0].Summary)
		page.Builders, page.LabelsJSON = s.builderLegend(r.Context(), world, clipped)
	}
	render(w, worldAnalysisTemplate, page)
}

func (s *Server) playerAnalysisJSON(w http.ResponseWriter, r *http.Request) {
	world := r.PathValue("world")
	if !s.requireWorldAccess(w, r, world) {
		return
	}
	snapshots, err := s.store.LatestWorldAnalyses(r.Context(), world, 1)
	if err != nil {
		http.Error(w, "world map unavailable", http.StatusServiceUnavailable)
		return
	}
	if len(snapshots) == 0 {
		http.NotFound(w, r)
		return
	}
	snapshot := clipToDiscovered(snapshots[0], s.discoveredFor(world, snapshots[0]))
	if r.URL.Query().Get("summary") == "1" {
		// The renderer asks for the light version once it holds the terrain manifest. The zone list
		// stays whatever happens: it is what the fog is drawn from.
		snapshot.Locations = nil
		snapshot.Clusters = nil
		snapshot.ConstructionCoverage = nil
		snapshot.Objects = nil
	}
	response := struct {
		Snapshot        worldintel.Snapshot `json:"snapshot"`
		Recommendations []string            `json:"recommendations"`
		ExploredMask    *explorationMask    `json:"explored_mask,omitempty"`
		Pins            []explorationPins   `json:"pins,omitempty"`
	}{snapshot, nil, s.explorationUnion(world), s.reportedPins(world)}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	_ = json.NewEncoder(w).Encode(response)
}

func (s *Server) playerTerrainManifest(w http.ResponseWriter, r *http.Request) {
	if !s.requireWorldAccess(w, r, r.PathValue("world")) {
		return
	}
	s.worldTerrainManifest(w, r)
}

// playerTerrainTile hands over the same terrain image the operator sees. The tile is the world as
// generated from the seed, and the fog is drawn over it in the browser from the zone list - so a
// player sees only where they have been, without a second tile pyramid to build and keep fresh.
func (s *Server) playerTerrainTile(w http.ResponseWriter, r *http.Request) {
	if !s.requireWorldAccess(w, r, r.PathValue("world")) {
		return
	}
	s.worldTerrainTile(w, r)
}

// playerOverlayTile builds the tile from the clipped snapshot rather than filtering the operator's
// prebuilt file, so the two can never drift: one projection decides what is on the players' map.
func (s *Server) playerOverlayTile(w http.ResponseWriter, r *http.Request) {
	world := r.PathValue("world")
	if !s.requireWorldAccess(w, r, world) {
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
		http.Error(w, "world map unavailable", http.StatusServiceUnavailable)
		return
	}
	if len(snapshots) == 0 || r.PathValue("source") != snapshots[0].Source.SHA256 {
		http.NotFound(w, r)
		return
	}
	tile, ok := maptiles.SelectOverlay(clipToDiscovered(snapshots[0], s.discoveredFor(world, snapshots[0])), manifest.Levels[zoom], x, y)
	if !ok {
		http.NotFound(w, r)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "private, no-cache")
	_ = json.NewEncoder(w).Encode(tile)
}
