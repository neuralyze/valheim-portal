package app

import (
	"testing"

	"github.com/neuralyze/valheim-portal/internal/worldintel"
)

// The players' map shows the world as the server knows they have seen it. Valheim creates a zone when
// somebody has been near it, so anything standing in a zone that was never generated is somewhere
// nobody has been - and must not appear.
func TestTheMapKeepsOnlyWhatPlayersHaveFound(t *testing.T) {
	// Zone (0,0) covers roughly -32..32 m; zone (10,10) sits 640 m away and was never generated.
	snapshot := worldintel.Snapshot{
		GeneratedZones: []worldintel.Vec2{{X: 0, Y: 0}},
		Locations: []worldintel.Location{
			{Name: "StartTemple", Position: worldintel.Vec3{X: 5, Z: 5}},
			{Name: "Crypt", Position: worldintel.Vec3{X: 640, Z: 640}},
		},
		Objects: []worldintel.Object{
			{Prefab: "portal_wood", Position: worldintel.Vec3{X: -10, Z: 12}},
			{Prefab: "piece_chest", Position: worldintel.Vec3{X: 700, Z: 700}},
		},
		Clusters: []worldintel.Cluster{
			{ID: 1, Center: worldintel.Vec3{X: 8, Z: -8}, Pieces: 12},
			{ID: 2, Center: worldintel.Vec3{X: 645, Z: 645}, Pieces: 30},
		},
		ConstructionCoverage: &worldintel.ConstructionCoverage{
			CellSize: 32,
			Cells: []worldintel.CoverageCell{
				{X: 0, Z: 0, Pieces: 12},
				{X: 20, Z: 20, Pieces: 30},
			},
		},
	}

	clipped := clipToDiscovered(snapshot)

	if len(clipped.Locations) != 1 || clipped.Locations[0].Name != "StartTemple" {
		t.Errorf("locations = %+v, want only the one in a discovered zone", clipped.Locations)
	}
	if len(clipped.Objects) != 1 || clipped.Objects[0].Prefab != "portal_wood" {
		t.Errorf("objects = %+v, want only the one in a discovered zone", clipped.Objects)
	}
	if len(clipped.Clusters) != 1 || clipped.Clusters[0].ID != 1 {
		t.Errorf("clusters = %+v, want only the one in a discovered zone", clipped.Clusters)
	}
	if len(clipped.ConstructionCoverage.Cells) != 1 || clipped.ConstructionCoverage.Cells[0].X != 0 {
		t.Errorf("coverage = %+v, want only the cell in a discovered zone", clipped.ConstructionCoverage.Cells)
	}
	// The zone list itself is what the fog is cut from, so it survives untouched.
	if len(clipped.GeneratedZones) != 1 {
		t.Errorf("generated zones = %+v, want the mask kept", clipped.GeneratedZones)
	}
	// Clipping decides where, not what: a kept object keeps every field it had.
	if clipped.Objects[0].Position.X != -10 || clipped.Objects[0].Position.Z != 12 {
		t.Errorf("kept object was altered: %+v", clipped.Objects[0])
	}
	// And the operator's own snapshot is untouched by the clip.
	if len(snapshot.Locations) != 2 || len(snapshot.Objects) != 2 {
		t.Error("clipping mutated the snapshot it was given")
	}
}

// A zone is indexed the way the game indexes it - rounded, not truncated - or everything within half
// a zone of the origin lands in the wrong cell and vanishes from the players' map.
func TestZonesAreIndexedTheWayTheGameDoes(t *testing.T) {
	cases := []struct {
		metres float32
		want   int
	}{{0, 0}, {31, 0}, {-31, 0}, {33, 1}, {-33, -1}, {64, 1}, {-64, -1}, {640, 10}}
	for _, c := range cases {
		if got := zoneIndex(c.metres); got != c.want {
			t.Errorf("zoneIndex(%v) = %d, want %d", c.metres, got, c.want)
		}
	}
}
