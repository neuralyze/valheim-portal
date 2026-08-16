package worldintel

import (
	"bytes"
	"encoding/binary"
	"encoding/json"
	"math"
	"reflect"
	"testing"
)

func writeString(b *bytes.Buffer, value string) {
	for n := len(value); ; n >>= 7 {
		v := byte(n & 0x7f)
		if n >= 0x80 {
			v |= 0x80
		}
		b.WriteByte(v)
		if n < 0x80 {
			break
		}
	}
	b.WriteString(value)
}

func writeVec3(b *bytes.Buffer, x, y, z float32) {
	for _, value := range []float32{x, y, z} {
		_ = binary.Write(b, binary.LittleEndian, math.Float32bits(value))
	}
}

func version37Fixture(t *testing.T) []byte {
	t.Helper()
	var b bytes.Buffer
	for _, value := range []any{int32(37), float64(172800), int64(42), uint32(8), int32(1)} {
		if err := binary.Write(&b, binary.LittleEndian, value); err != nil {
			t.Fatal(err)
		}
	}
	flags := uint16(256 | 64)
	_ = binary.Write(&b, binary.LittleEndian, flags)
	_ = binary.Write(&b, binary.LittleEndian, int16(2))
	_ = binary.Write(&b, binary.LittleEndian, int16(-3))
	writeVec3(&b, 128, 40, -192)
	_ = binary.Write(&b, binary.LittleEndian, StableHash("piece_portal"))
	b.WriteByte(1)
	_ = binary.Write(&b, binary.LittleEndian, StableHash("tag"))
	writeString(&b, "home")
	_ = binary.Write(&b, binary.LittleEndian, int32(1))
	_ = binary.Write(&b, binary.LittleEndian, int32(2))
	_ = binary.Write(&b, binary.LittleEndian, int32(-3))
	_ = binary.Write(&b, binary.LittleEndian, int32(2))
	_ = binary.Write(&b, binary.LittleEndian, int32(31))
	_ = binary.Write(&b, binary.LittleEndian, int32(1))
	writeString(&b, "defeated_eikthyr")
	b.WriteByte(1)
	_ = binary.Write(&b, binary.LittleEndian, int32(1))
	writeString(&b, "StartTemple")
	writeVec3(&b, 0, 30, 0)
	b.WriteByte(1)
	return b.Bytes()
}

func TestParseCurrentVersion37Contract(t *testing.T) {
	snapshot, err := ParseDB(bytes.NewReader(version37Fixture(t)), knownCatalog())
	if err != nil {
		t.Fatal(err)
	}
	if snapshot.WorldVersion != 37 || snapshot.WorldAgeDays != 0 || snapshot.Summary.Objects != 1 {
		t.Fatalf("snapshot=%#v", snapshot)
	}
	if len(snapshot.GeneratedZones) != 1 || snapshot.GeneratedZones[0] != (Vec2{2, -3}) {
		t.Fatalf("zones=%#v", snapshot.GeneratedZones)
	}
	if len(snapshot.Locations) != 1 || snapshot.Locations[0].Name != "StartTemple" || !snapshot.Locations[0].Generated {
		t.Fatalf("locations=%#v", snapshot.Locations)
	}
	if len(snapshot.Objects) != 1 || snapshot.Objects[0].Category != "portal" || snapshot.Objects[0].Properties[0].Value != "home" {
		t.Fatalf("objects=%#v", snapshot.Objects)
	}
}

func TestParserRejectsFutureWorldVersion(t *testing.T) {
	data := version37Fixture(t)
	binary.LittleEndian.PutUint32(data, uint32(MaxWorldVersion+1))
	if _, err := ParseDB(bytes.NewReader(data), knownCatalog()); err == nil {
		t.Fatal("accepted unverified future save version")
	}
}

func TestStableHashMatchesValheimContract(t *testing.T) {
	if got := StableHash("SeedTest01"); got != -682541416 {
		t.Fatalf("stable hash=%d", got)
	}
}

func TestLocationCategoryClassifiesWorldFeaturesAndEveryBoss(t *testing.T) {
	tests := map[string]string{
		"Eikthyrnir": "boss", "GDKing": "boss", "Bonemass": "boss", "Dragonqueen": "boss",
		"GoblinKing": "boss", "Mistlands_DvergrBossEntrance1": "boss", "FaderLocation": "boss",
		"Vendor_BlackForest": "trader", "Hildir_camp": "trader", "BogWitch_Camp": "trader",
		"SunkenCrypt4": "dungeon", "MountainCave02": "dungeon", "MorgenHole1": "dungeon",
		"CharredFortress": "fortress", "Mistlands_GuardTower1_new": "fortress",
		"StartTemple":  "spawn",
		"WoodVillage1": "settlement", "GoblinCamp2": "settlement",
		"TarPit3": "resource", "VoltureNest": "resource", "InfestedTree01": "resource",
		"Runestone_Meadows": "landmark", "ShipWreck01": "landmark", "AshlandRuins": "landmark",
		"CombatRuin01": "landmark", "Greydwarf_camp1": "settlement",
	}
	for name, want := range tests {
		if got := LocationCategory(name); got != want {
			t.Errorf("LocationCategory(%q) = %q, want %q", name, got, want)
		}
	}
}

func TestFinalizeBackfillsLocationCategoriesAndSummary(t *testing.T) {
	snapshot := Snapshot{Locations: []Location{{Name: "StartTemple"}, {Name: "Bonemass"}, {Name: "SunkenCrypt4"}, {Name: "Crypt2", Category: "dungeon"}}}
	finalize(&snapshot)
	if got := snapshot.Summary.LocationCategories["spawn"]; got != 1 {
		t.Fatalf("spawn location count = %d, want 1", got)
	}
	if got := snapshot.Summary.LocationCategories["boss"]; got != 1 {
		t.Fatalf("boss location count = %d, want 1", got)
	}
	if got := snapshot.Summary.LocationCategories["dungeon"]; got != 2 {
		t.Fatalf("dungeon location count = %d, want 2", got)
	}
	for _, location := range snapshot.Locations {
		if location.Category == "" {
			t.Fatalf("location category was not backfilled: %#v", location)
		}
	}
}

func TestRetainDropsUnrelatedWorldObjectsWithStringProperties(t *testing.T) {
	values := valueMaps{s: map[int32]string{StableHash("unrelated"): "value"}}
	if retain(Object{Category: "world"}, values) {
		t.Fatal("retained an unrelated world object solely because it had a string property")
	}
	if !retain(Object{Category: "container"}, valueMaps{}) {
		t.Fatal("dropped a map-relevant container")
	}
}

func TestConstructionCoverageIsBoundedCompleteAndDeterministic(t *testing.T) {
	buildSnapshot := func() Snapshot {
		const pieces = 2_500
		objects := make([]Object, 0, pieces+1)
		for index := range pieces {
			objects = append(objects, Object{
				ID:       uint32(index + 1),
				Category: "construction",
				Position: Vec3{
					X: float32(-16_000 + (index%50)*64),
					Y: 40,
					Z: float32(-16_000 + (index/50)*64),
				},
			})
		}
		objects = append(objects, Object{
			ID:       pieces + 1,
			Category: "portal",
			Position: Vec3{X: 12, Y: 4, Z: -8},
		})
		return Snapshot{
			Summary: Summary{Categories: map[string]int{"construction": pieces, "portal": 1}},
			Objects: objects,
		}
	}

	first, second := buildSnapshot(), buildSnapshot()
	finalize(&first)
	finalize(&second)
	if !reflect.DeepEqual(first.ConstructionCoverage, second.ConstructionCoverage) {
		t.Fatal("construction coverage is not deterministic")
	}
	coverage := first.ConstructionCoverage
	if coverage == nil {
		t.Fatal("construction coverage was not produced")
	}
	if coverage.TotalPieces != 2_500 {
		t.Fatalf("total pieces = %d", coverage.TotalPieces)
	}
	if len(coverage.Cells) > maxConstructionCoverageCells {
		t.Fatalf("coverage cells = %d, max = %d", len(coverage.Cells), maxConstructionCoverageCells)
	}
	if coverage.CellSize <= constructionCoverageBaseCell {
		t.Fatalf("dense fixture was not coarsened: cell size = %d", coverage.CellSize)
	}
	total := 0
	for index, cell := range coverage.Cells {
		total += cell.Pieces
		if index > 0 {
			previous := coverage.Cells[index-1]
			if previous.X > cell.X || previous.X == cell.X && previous.Z >= cell.Z {
				t.Fatalf("coverage cells are not stably sorted at %d: %#v then %#v", index, previous, cell)
			}
		}
	}
	if total != coverage.TotalPieces {
		t.Fatalf("coverage represents %d of %d pieces", total, coverage.TotalPieces)
	}
	if len(first.Objects) != 1 || first.Objects[0].Category != "portal" {
		t.Fatalf("construction was not aggregated independently of retained markers: %#v", first.Objects)
	}
}

func TestConstructionCoverageExtendsSnapshotJSONCompatibly(t *testing.T) {
	var legacy Snapshot
	if err := json.Unmarshal([]byte(`{"schema":1,"world":"Asgard","summary":{"categories":{}},"clusters":[],"objects":[]}`), &legacy); err != nil {
		t.Fatal(err)
	}
	if legacy.ConstructionCoverage != nil {
		t.Fatalf("legacy snapshot unexpectedly has coverage: %#v", legacy.ConstructionCoverage)
	}
	legacyJSON, err := json.Marshal(legacy)
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(legacyJSON, []byte(`"construction_coverage"`)) {
		t.Fatalf("empty compatibility field was serialized: %s", legacyJSON)
	}

	current := legacy
	current.ConstructionCoverage = &ConstructionCoverage{
		CellSize:    32,
		TotalPieces: 7,
		MaxPieces:   7,
		Cells:       []CoverageCell{{X: -1, Z: 2, Pieces: 7}},
	}
	currentJSON, err := json.Marshal(current)
	if err != nil {
		t.Fatal(err)
	}
	for _, field := range [][]byte{
		[]byte(`"construction_coverage"`),
		[]byte(`"cell_size":32`),
		[]byte(`"total_pieces":7`),
		[]byte(`"max_pieces":7`),
		[]byte(`"cells":[{"x":-1,"z":2,"pieces":7}]`),
	} {
		if !bytes.Contains(currentJSON, field) {
			t.Fatalf("coverage JSON missing %s: %s", field, currentJSON)
		}
	}
	var roundTrip Snapshot
	if err := json.Unmarshal(currentJSON, &roundTrip); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(roundTrip.ConstructionCoverage, current.ConstructionCoverage) {
		t.Fatalf("coverage JSON round trip = %#v", roundTrip.ConstructionCoverage)
	}
}

// Two people building in one valley must be two clusters, or colouring them by builder means nothing
// and the map cannot answer "who built that".
func TestClustersSeparatePerBuilder(t *testing.T) {
	points := []constructionPoint{}
	for i := range 6 {
		points = append(points, constructionPoint{Position: Vec3{X: float32(10 + i), Z: 10}, Creator: 111})
		points = append(points, constructionPoint{Position: Vec3{X: float32(10 + i), Z: 12}, Creator: 222})
	}

	clusters := aggregateConstructionClusters(points)

	creators := map[int64]int{}
	for _, cluster := range clusters {
		creators[cluster.Creator] += cluster.Pieces
	}
	if len(creators) != 2 {
		t.Fatalf("clusters carry %d creators, want 2: %+v", len(creators), clusters)
	}
	for creator, pieces := range creators {
		if pieces != 6 {
			t.Errorf("creator %d has %d pieces, want 6", creator, pieces)
		}
	}
}

// One builder in two distant places is still two sites, which is what makes a legend count useful.
func TestOneBuilderInTwoPlacesIsTwoClusters(t *testing.T) {
	points := []constructionPoint{}
	for i := range 4 {
		points = append(points, constructionPoint{Position: Vec3{X: float32(i), Z: 0}, Creator: 777})
		points = append(points, constructionPoint{Position: Vec3{X: float32(2000 + i), Z: 2000}, Creator: 777})
	}

	clusters := aggregateConstructionClusters(points)

	if len(clusters) != 2 {
		t.Fatalf("one builder in two valleys produced %d clusters, want 2", len(clusters))
	}
}
