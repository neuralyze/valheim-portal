package maptiles

import (
	"context"
	"os"
	"testing"

	"github.com/neuralyze/valheim-portal/internal/worldintel"
)

func TestSelectOverlayFiltersAndClustersByZoom(t *testing.T) {
	snapshot := worldintel.Snapshot{
		World: "Midgard", Source: worldintel.Source{SHA256: "analysis-hash"},
		Locations: []worldintel.Location{
			{Name: "inside", Position: worldintel.Vec3{X: -11000, Z: -11000}},
			{Name: "outside", Position: worldintel.Vec3{X: 11000, Z: 11000}},
		},
		Objects: []worldintel.Object{
			{ID: 1, Category: "portal", Position: worldintel.Vec3{X: -11000, Z: -11000}},
			{ID: 2, Category: "portal", Position: worldintel.Vec3{X: -11010, Z: -11010}},
			{ID: 3, Category: "container", Position: worldintel.Vec3{X: 11000, Z: 11000}},
		},
	}
	overview := Level{Zoom: 0, Width: 384, Height: 384, TilesWide: 1, TilesHigh: 1}
	overviewTile, ok := SelectOverlay(snapshot, overview, 0, 0)
	if !ok {
		t.Fatal("overview tile rejected")
	}
	if len(overviewTile.Objects) != 0 || len(overviewTile.Markers) != 2 {
		t.Fatalf("overview objects=%d markers=%d, want 0 and 2", len(overviewTile.Objects), len(overviewTile.Markers))
	}
	if overviewTile.SourceSHA256 != "analysis-hash" {
		t.Fatalf("source hash = %q", overviewTile.SourceSHA256)
	}

	closeLevel := Level{Zoom: 5, Width: 12288, Height: 12288, TilesWide: 24, TilesHigh: 24}
	closeTile, ok := SelectOverlay(snapshot, closeLevel, 1, 1)
	if !ok {
		t.Fatal("close tile rejected")
	}
	if len(closeTile.Locations) != 1 || closeTile.Locations[0].Name != "inside" {
		t.Fatalf("close locations = %#v", closeTile.Locations)
	}
	if len(closeTile.Objects) != 2 || len(closeTile.Markers) != 0 {
		t.Fatalf("close objects=%d markers=%d, want 2 and 0", len(closeTile.Objects), len(closeTile.Markers))
	}
}
func TestOverviewOverlayRetainsEveryBossBeforeLocationTruncation(t *testing.T) {
	snapshot := worldintel.Snapshot{World: "Midgard"}
	for index := 0; index < 1100; index++ {
		snapshot.Locations = append(snapshot.Locations, worldintel.Location{
			Name: "Ruin1", Position: worldintel.Vec3{X: float32(index%100 - 50), Z: float32(index/100 - 50)},
		})
	}
	snapshot.Locations = append(snapshot.Locations, worldintel.Location{
		Name: "StartTemple", Position: worldintel.Vec3{},
	})
	bosses := []string{"Eikthyrnir", "GDKing", "Bonemass", "Dragonqueen", "GoblinKing", "Mistlands_DvergrBossEntrance1", "FaderLocation"}
	for index, name := range bosses {
		snapshot.Locations = append(snapshot.Locations, worldintel.Location{
			Name: name, Position: worldintel.Vec3{X: float32(index), Z: float32(index)},
		})
	}
	// "Ruin1" above is the bulk that gets truncated, and it classifies as "ruins" now that a ruined
	// wall and a runestone are no longer the same thing. The explicit list is every other category a
	// truncated overview still has to carry one of.
	for _, category := range []string{"trader", "dungeon", "shrine", "tower", "fortress", "arena", "mine", "port", "monument", "settlement", "resource", "landmark", "other"} {
		for index := 0; index < 200; index++ {
			snapshot.Locations = append(snapshot.Locations, worldintel.Location{
				Name: category, Category: category, Position: worldintel.Vec3{X: float32(index - 100), Z: float32(index - 100)},
			})
		}
	}
	tile, ok := SelectOverlay(snapshot, Level{Zoom: 0, Width: 384, Height: 384, TilesWide: 1, TilesHigh: 1}, 0, 0)
	if !ok {
		t.Fatal("overview tile rejected")
	}
	if len(tile.Locations) != 1024 || !tile.Truncated {
		t.Fatalf("overview locations=%d truncated=%t, want 1024 and true", len(tile.Locations), tile.Truncated)
	}
	if tile.Locations[0].Name != "StartTemple" || tile.Locations[0].Category != "spawn" {
		t.Fatalf("spawn was not prioritized: %#v", tile.Locations[0])
	}
	for index, name := range bosses {
		if tile.Locations[index+1].Name != name || tile.Locations[index+1].Category != "boss" {
			t.Fatalf("boss %d was not prioritized with its category: %#v", index, tile.Locations[index+1])
		}
	}
	categoryCounts := make(map[string]int)
	for _, location := range tile.Locations {
		categoryCounts[location.Category]++
	}
	for _, category := range []string{"spawn", "boss", "trader", "dungeon", "shrine", "tower", "fortress", "arena", "mine", "port", "monument", "settlement", "resource", "ruins", "landmark", "other"} {
		if categoryCounts[category] == 0 {
			t.Errorf("overview truncation omitted the %q location category", category)
		}
	}
}

func TestSelectOverlayPayloadIsBounded(t *testing.T) {
	snapshot := worldintel.Snapshot{World: "Midgard"}
	for index := 0; index < 6000; index++ {
		x := float32(-12000 + index%100)
		z := float32(-12000 + index/100)
		snapshot.GeneratedZones = append(snapshot.GeneratedZones, worldintel.Vec2{X: int(x / 64), Y: int(z / 64)})
		snapshot.Locations = append(snapshot.Locations, worldintel.Location{Name: "location", Position: worldintel.Vec3{X: x, Z: z}})
		snapshot.Clusters = append(snapshot.Clusters, worldintel.Cluster{ID: index, Center: worldintel.Vec3{X: x, Z: z}})
		snapshot.Objects = append(snapshot.Objects, worldintel.Object{ID: uint32(index), Category: "portal", Position: worldintel.Vec3{X: x, Z: z}})
	}
	level := Level{Zoom: 5, Width: 12288, Height: 12288, TilesWide: 24, TilesHigh: 24}
	tile, ok := SelectOverlay(snapshot, level, 0, 0)
	if !ok {
		t.Fatal("tile rejected")
	}
	features := len(tile.GeneratedZones) + len(tile.Locations) + len(tile.Clusters) + len(tile.Objects) + len(tile.Markers)
	if features > MaxOverlayFeatures || !tile.Truncated {
		t.Fatalf("features=%d truncated=%v", features, tile.Truncated)
	}
}

func TestBuildOverlayPyramidPublishesAndReusesSourceHash(t *testing.T) {
	root := t.TempDir()
	levels := pyramidLevels(MinimumSize)
	terrain := Manifest{
		Schema: SchemaVersion, World: "Midgard", Seed: "seed", WorldGenVersion: 37,
		Renderer: RendererVersion, Key: "terrain-key", SourceSHA256: "terrain-source", HeightSHA256: "height-source", TextureSHA256: "texture-source",
		SourceWidth: 12288, SourceHeight: 12288, Width: MinimumSize, Height: MinimumSize,
		TileSize: TileSize, MaxZoom: len(levels) - 1, Format: "png",
		Bounds: Bounds{MinX: -WorldRadius, MinZ: -WorldRadius, MaxX: WorldRadius, MaxZ: WorldRadius},
		Levels: levels, TileETags: map[string]string{},
	}
	snapshot := worldintel.Snapshot{
		World: "Midgard", Source: worldintel.Source{SHA256: "analysis-hash"},
		Objects: []worldintel.Object{{ID: 1, Category: "portal", Position: worldintel.Vec3{X: 0, Z: 0}}},
	}
	first, err := BuildOverlayPyramid(context.Background(), root, terrain, snapshot)
	if err != nil {
		t.Fatal(err)
	}
	second, err := BuildOverlayPyramid(context.Background(), root, terrain, snapshot)
	if err != nil {
		t.Fatal(err)
	}
	if len(first.TileETags) != 770 || len(second.TileETags) != 770 {
		t.Fatalf("overlay tile ETags = %d and %d, want 770", len(first.TileETags), len(second.TileETags))
	}
	path, err := OverlayPath(root, terrain, snapshot.Source.SHA256, 5, 23, 23)
	if err != nil {
		t.Fatal(err)
	}
	if info, err := os.Stat(path); err != nil || info.Size() == 0 {
		t.Fatalf("published overlay missing: info=%v err=%v", info, err)
	}
}
