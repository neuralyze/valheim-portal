package maptiles

import (
	"fmt"
	"math"
	"sort"

	"github.com/neuralyze/valheim-portal/internal/worldintel"
)

const OverlaySchemaVersion = 2

const MaxOverlayFeatures = 4096

type OverlayMarker struct {
	Category string          `json:"category"`
	Position worldintel.Vec3 `json:"position"`
	Count    int             `json:"count"`
}

type OverlayTile struct {
	Schema         int                              `json:"schema"`
	World          string                           `json:"world"`
	SourceSHA256   string                           `json:"source_sha256"`
	Zoom           int                              `json:"zoom"`
	X              int                              `json:"x"`
	Y              int                              `json:"y"`
	Bounds         Bounds                           `json:"bounds"`
	GeneratedZones []worldintel.Vec2                `json:"generated_zones,omitempty"`
	Locations      []worldintel.Location            `json:"locations,omitempty"`
	Clusters       []worldintel.Cluster             `json:"clusters,omitempty"`
	Coverage       *worldintel.ConstructionCoverage `json:"construction_coverage,omitempty"`
	Objects        []worldintel.Object              `json:"objects,omitempty"`
	Markers        []OverlayMarker                  `json:"markers,omitempty"`
	Truncated      bool                             `json:"truncated,omitempty"`
}

func BoundsForTile(level Level, x, y int) (Bounds, bool) {
	if x < 0 || y < 0 || x >= level.TilesWide || y >= level.TilesHigh || level.Width <= 0 {
		return Bounds{}, false
	}
	metresPerPixel := 2 * WorldRadius / float64(level.Width)
	minX := -WorldRadius + float64(x*TileSize)*metresPerPixel
	minZ := -WorldRadius + float64(y*TileSize)*metresPerPixel
	maxX := min(WorldRadius, minX+float64(TileSize)*metresPerPixel)
	maxZ := min(WorldRadius, minZ+float64(TileSize)*metresPerPixel)
	return Bounds{MinX: minX, MinZ: minZ, MaxX: maxX, MaxZ: maxZ}, true
}

func SelectOverlay(snapshot worldintel.Snapshot, level Level, x, y int) (OverlayTile, bool) {
	bounds, ok := BoundsForTile(level, x, y)
	if !ok {
		return OverlayTile{}, false
	}
	tile := OverlayTile{
		Schema: OverlaySchemaVersion, World: snapshot.World, SourceSHA256: snapshot.Source.SHA256,
		Zoom: level.Zoom, X: x, Y: y, Bounds: bounds,
	}
	inside := func(px, pz float64) bool {
		return px >= bounds.MinX && px <= bounds.MaxX && pz >= bounds.MinZ && pz <= bounds.MaxZ
	}
	for _, zone := range snapshot.GeneratedZones {
		if inside(float64(zone.X*64), float64(zone.Y*64)) {
			tile.GeneratedZones = append(tile.GeneratedZones, zone)
		}
	}
	for _, location := range snapshot.Locations {
		if inside(float64(location.Position.X), float64(location.Position.Z)) {
			if location.Category == "" {
				location.Category = worldintel.LocationCategory(location.Name)
			}
			tile.Locations = append(tile.Locations, location)
		}
	}
	for _, cluster := range snapshot.Clusters {
		radius := float64(cluster.Radius)
		if float64(cluster.Center.X)+radius >= bounds.MinX && float64(cluster.Center.X)-radius <= bounds.MaxX &&
			float64(cluster.Center.Z)+radius >= bounds.MinZ && float64(cluster.Center.Z)-radius <= bounds.MaxZ {
			tile.Clusters = append(tile.Clusters, cluster)
		}
	}
	if coverage := snapshot.ConstructionCoverage; coverage != nil {
		selected := &worldintel.ConstructionCoverage{CellSize: coverage.CellSize, TotalPieces: coverage.TotalPieces, MaxPieces: coverage.MaxPieces}
		for _, cell := range coverage.Cells {
			minX, minZ := float64(cell.X*coverage.CellSize), float64(cell.Z*coverage.CellSize)
			if minX+float64(coverage.CellSize) >= bounds.MinX && minX <= bounds.MaxX &&
				minZ+float64(coverage.CellSize) >= bounds.MinZ && minZ <= bounds.MaxZ {
				selected.Cells = append(selected.Cells, cell)
			}
		}
		if len(selected.Cells) > 0 {
			tile.Coverage = selected
		}
	}
	if level.Zoom >= 3 {
		for _, object := range snapshot.Objects {
			if inside(float64(object.Position.X), float64(object.Position.Z)) {
				if len(tile.Objects) >= MaxOverlayFeatures {
					tile.Truncated = true
					break
				}
				tile.Objects = append(tile.Objects, object)
			}
		}
	} else {
		type aggregate struct {
			category string
			x, y, z  float64
			count    int
		}
		cellSize := math.Max(64, (bounds.MaxX-bounds.MinX)/16)
		groups := make(map[string]*aggregate)
		for _, object := range snapshot.Objects {
			if !inside(float64(object.Position.X), float64(object.Position.Z)) {
				continue
			}
			cellX := int(math.Floor(float64(object.Position.X) / cellSize))
			cellZ := int(math.Floor(float64(object.Position.Z) / cellSize))
			key := fmt.Sprintf("%s/%d/%d", object.Category, cellX, cellZ)
			group := groups[key]
			if group == nil {
				group = &aggregate{category: object.Category}
				groups[key] = group
			}
			group.x += float64(object.Position.X)
			group.y += float64(object.Position.Y)
			group.z += float64(object.Position.Z)
			group.count++
		}
		keys := make([]string, 0, len(groups))
		for key := range groups {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		for _, key := range keys {
			group := groups[key]
			tile.Markers = append(tile.Markers, OverlayMarker{
				Category: group.category,
				Position: worldintel.Vec3{X: float32(group.x / float64(group.count)), Y: float32(group.y / float64(group.count)), Z: float32(group.z / float64(group.count))},
				Count:    group.count,
			})
		}
	}
	tile.GeneratedZones, tile.Truncated = bounded(tile.GeneratedZones, 512, tile.Truncated)
	tile.Locations, tile.Truncated = boundedLocations(tile.Locations, 1024, tile.Truncated)
	tile.Clusters, tile.Truncated = bounded(tile.Clusters, 512, tile.Truncated)
	if tile.Coverage != nil {
		tile.Coverage.Cells, tile.Truncated = bounded(tile.Coverage.Cells, 1024, tile.Truncated)
	}
	tile.Objects, tile.Truncated = bounded(tile.Objects, 1024, tile.Truncated)
	tile.Markers, tile.Truncated = bounded(tile.Markers, 1024, tile.Truncated)
	return tile, true
}

func locationPriority(category string) int {
	switch category {
	case "spawn":
		return 0
	case "boss":
		return 1
	case "trader":
		return 2
	case "dungeon":
		return 3
	case "fortress":
		return 4
	case "settlement":
		return 5
	case "resource":
		return 6
	case "landmark":
		return 7
	default:
		return 8
	}
}

func boundedLocations(values []worldintel.Location, maximum int, alreadyTruncated bool) ([]worldintel.Location, bool) {
	sort.SliceStable(values, func(left, right int) bool {
		return locationPriority(values[left].Category) < locationPriority(values[right].Category)
	})
	if len(values) <= maximum {
		return values, alreadyTruncated
	}
	buckets := make([][]worldintel.Location, 9)
	for _, location := range values {
		priority := locationPriority(location.Category)
		buckets[priority] = append(buckets[priority], location)
	}
	result := make([]worldintel.Location, 0, maximum)
	for _, priority := range []int{0, 1} {
		result = append(result, buckets[priority]...)
		if len(result) >= maximum {
			return result[:maximum], true
		}
	}
	active := 0
	for priority := 2; priority < len(buckets); priority++ {
		if len(buckets[priority]) > 0 {
			active++
		}
	}
	remaining := maximum - len(result)
	for priority := 2; priority < len(buckets) && active > 0; priority++ {
		bucket := buckets[priority]
		if len(bucket) == 0 {
			continue
		}
		quota := remaining / active
		if quota > len(bucket) {
			quota = len(bucket)
		}
		for index := 0; index < quota; index++ {
			result = append(result, bucket[index*len(bucket)/quota])
		}
		remaining -= quota
		active--
	}
	return result, true
}

func bounded[T any](values []T, maximum int, alreadyTruncated bool) ([]T, bool) {
	if len(values) <= maximum {
		return values, alreadyTruncated
	}
	return values[:maximum], true
}
