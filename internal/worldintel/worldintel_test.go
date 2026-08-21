package worldintel

import (
	"bytes"
	"encoding/binary"
	"encoding/json"
	"math"
	"reflect"
	"strings"
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

// Real prefab names from the Hrafnheim corpus, chosen because each one matches more than one
// keyword: the classifier is an ordered switch, so these are the cases that pin the order down.
func TestLocationCategoryClassifiesWorldFeaturesAndEveryBoss(t *testing.T) {
	tests := map[string]string{
		"Eikthyrnir": "boss", "GDKing": "boss", "Bonemass": "boss", "Dragonqueen": "boss",
		"GoblinKing": "boss", "Mistlands_DvergrBossEntrance1": "boss", "FaderLocation": "boss",
		"Vendor_BlackForest": "trader", "Hildir_camp": "trader", "BogWitch_Camp": "trader",
		"SunkenCrypt4": "dungeon", "MountainCave02": "dungeon", "MorgenHole1": "dungeon",
		// Controls that must not regress: the world spawn contains "temple" and the crypts and
		// caves are 1056 locations that were already right before shrines existed.
		"StartTemple": "spawn", "Crypt2": "dungeon", "TrollCave02": "dungeon",
		// Tombs are enterable, so they belong with the crypts rather than in "other".
		"MWL_MeadowsTomb4": "dungeon",
		// The church that started this: it contains "ruin", and read as a landmark until shrine was
		// evaluated ahead of the ruins rule.
		"MWL_RuinsChurch1": "shrine", "MWL_AncientShrine1": "shrine", "MWL_FulingTemple3": "shrine",
		"MWL_SwampAltar1": "shrine",
		// Tower beats fortress and ruins; fortress keeps the strongholds even when ruined.
		"Mistlands_GuardTower1_new": "tower", "StoneTowerRuins03": "tower",
		"CharredTowerRuins2": "tower", "MWL_SwampBrokenTower3": "tower",
		"CharredFortress": "fortress", "FortressRuins": "fortress",
		"MWL_RuinsArena1": "arena", "MWL_PlainsArena2": "arena",
		"Mistlands_Excavation1": "mine",
		"Mistlands_Harbour1":    "port", "MWL_Port3": "port",
		"Runestone_Meadows": "monument", "Dolmen01": "monument", "Mistlands_Statue1": "monument",
		"StoneCircle":  "monument",
		"WoodVillage1": "settlement", "GoblinCamp2": "settlement", "Greydwarf_camp1": "settlement",
		"TarPit3": "resource", "VoltureNest": "resource", "InfestedTree01": "resource",
		"AshlandRuins": "ruins", "CombatRuin01": "ruins", "Ruin1": "ruins",
		"MWL_RuinsCastle1": "ruins",
		// What is left in landmark once monuments, mines and ruins have been taken out of it.
		"ShipWreck01": "landmark", "Mistlands_RoadPost1": "landmark", "Grave1": "landmark",
		"StoneHenge5": "monument", "PlaceofMystery1": "landmark",
		"MWL_MistHut1": "other",
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

// Boats, ships and carts were absent from the map because category() had no case for a hull: every
// one of these names returned "world", which retain() drops. The prefab names resolved the whole
// time - Hrafnheim's Raft came back named "Raft" - so these are the real strings the classifier has
// to get right, taken from assembly_valheim plus the installed BoatAdditions, LongshipUpgrades,
// Shipwright and CraftyCartsRemake DLLs.
func TestCategoryClassifiesHullsWithoutShadowingContainersOrBuildPieces(t *testing.T) {
	for _, testCase := range []struct {
		prefab string
		want   string
	}{
		// Vanilla hulls.
		{"Raft", "vehicle"},
		{"Karve", "vehicle"},
		{"VikingShip", "vehicle"},
		{"Cart", "vehicle"},
		{"longship_ashlands", "vehicle"},
		// BoatAdditions hulls, named from the literals in BoatAdditions.dll.
		{"BBA_Knarr", "vehicle"},
		{"BBA_LargeRaft", "vehicle"},
		{"BBA_OutriggerKarve", "vehicle"},
		{"Knarr", "vehicle"},
		{"LargeRaft", "vehicle"},
		{"OutriggerKarve", "vehicle"},
		// CraftyCartsRemake hulls, named from the literals in CraftyCartsRemake.dll.
		{"workbench_cart", "vehicle"},
		{"forge_cart", "vehicle"},
		{"stone_cart", "vehicle"},
		{"artisan_cart", "vehicle"},
		{"blackforge_cart", "vehicle"},
		{"cauldron_cart", "vehicle"},

		// The control: a wreck chest carries "karve" in its name and must stay a container, because
		// the container case is ordered ahead of the vehicle case precisely so loot stays loot.
		{"shipwreck_karve_chest", "container"},
		// Build pieces that contain a hull noun. "cartographytable" contains "cart".
		{"piece_cartographytable", "construction"},
		{"piece_upgradecart", "construction"},
		// "crafting" contains "raft" and "cartography" contains "cart": 571 names in Hrafnheim's
		// catalog contain "raft" and nearly all are Crafting symbols, so a bare strings.Contains
		// would have turned half the mod surface into a boat.
		{"CraftingStation", "world"},
		{"AzuAntiArthriticCrafting", "world"},
		{"AddCrafterName", "world"},
		{"CRAFT", "world"},
		{"Cartography", "world"},
		{"Draft", "world"},
		// A boat mod's furniture and tent parts are not hulls.
		{"LongshipUpgrades_MapTable", "world"},
		{"BBA_Boatyard", "world"},
		{"ShipTen2_beam", "world"},
		// Untouched neighbours, so the new case cannot be passing by accident.
		{"piece_chest_wood", "container"},
		{"piece_workbench", "construction"},
		{"portal_wood", "portal"},
		{"", "unknown"},
	} {
		if got := category(testCase.prefab); got != testCase.want {
			t.Errorf("category(%q) = %q, want %q", testCase.prefab, got, testCase.want)
		}
	}
}

func TestRetainKeepsVehiclesAndSemanticCategoryDoesNotDemoteALoadedHull(t *testing.T) {
	if !retain(Object{Category: "vehicle"}, valueMaps{}) {
		t.Fatal("dropped a vehicle: a boat classified correctly but never reached the snapshot")
	}
	// A loaded Karve holds an "items" map exactly like a chest, and the container rule used to
	// relabel it, so a boat was drawn as a chest even once it classified as a hull.
	loaded := Object{Category: category("Karve")}
	values := valueMaps{s: map[int32]string{StableHash("items"): "AAAA"}}
	semanticCategory(&loaded, values)
	if loaded.Category != "vehicle" {
		t.Fatalf("a Karve carrying cargo was relabelled %q, want vehicle", loaded.Category)
	}
	// The same rule must still catch an actual chest, which is what makes the case above meaningful.
	chest := Object{Category: category("unnamed_thing")}
	semanticCategory(&chest, values)
	if chest.Category != "container" {
		t.Fatalf("an object carrying cargo was classified %q, want container", chest.Category)
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

// "How much of the map have we discovered" has a server-side answer, but only if the game's far-away
// bookkeeping zones are kept out of it. Valheim parks global objects in a zone at 1,000,000 metres:
// real entries in the save, nowhere anybody walked. Hrafnheim had 81 of them among 474.
func TestExploredAreaExcludesSentinelZones(t *testing.T) {
	snapshot := Snapshot{GeneratedZones: []Vec2{
		{0, 0}, {1, 0}, {0, 1}, {-3, 5}, // visited
		{sentinelZoneIndex, sentinelZoneIndex}, // the game's far-away zone
		{sentinelZoneIndex, 4},
		{9000, 9000}, // anything outside the playable grid is not a place either
	}}

	finalize(&snapshot)

	if snapshot.Summary.ExploredZones != 4 {
		t.Errorf("explored zones = %d, want 4", snapshot.Summary.ExploredZones)
	}
	if snapshot.Summary.SentinelZones != 3 {
		t.Errorf("sentinel zones = %d, want 3", snapshot.Summary.SentinelZones)
	}
	// Four zones is 4 x 64 x 64 = 16384 m2, against a playable circle of radius 10000.
	wantKm := 16384.0 / 1_000_000
	if diff := snapshot.Summary.ExploredSquareKm - wantKm; diff > 1e-9 || diff < -1e-9 {
		t.Errorf("explored area = %.6f km2, want %.6f", snapshot.Summary.ExploredSquareKm, wantKm)
	}
	if snapshot.Summary.ExploredPercent <= 0 || snapshot.Summary.ExploredPercent > 1 {
		t.Errorf("explored percent = %f, which is not a plausible fraction of the map", snapshot.Summary.ExploredPercent)
	}
	// The operator is told what was set aside rather than left to wonder why the number is small.
	found := false
	for _, finding := range snapshot.Health.Findings {
		if strings.Contains(finding, "sentinel") {
			found = true
		}
	}
	if !found {
		t.Error("the sentinel zones were excluded silently")
	}
}

// The coverage layer is what an operator sees as "the constructions": one filled cell per patch of
// building. It used to carry no builder at all, so every cell drew in one colour and colouring the
// cluster glyphs changed nothing anybody could see.
func TestCoverageCellsCarryTheirDominantBuilder(t *testing.T) {
	// Two cells far enough apart to stay separate: one all Kato's, one mostly Jarn's.
	points := []constructionPoint{
		{Position: Vec3{X: 10, Z: 10}, Creator: 111},
		{Position: Vec3{X: 12, Z: 11}, Creator: 111},
		{Position: Vec3{X: 4000, Z: 4000}, Creator: 222},
		{Position: Vec3{X: 4002, Z: 4001}, Creator: 222},
		{Position: Vec3{X: 4004, Z: 4002}, Creator: 111},
	}

	coverage := aggregateConstructionCoverage(points)

	if coverage == nil || len(coverage.Cells) != 2 {
		t.Fatalf("cells = %v, want 2", coverage)
	}
	byCreator := map[int64]CoverageCell{}
	for _, cell := range coverage.Cells {
		byCreator[cell.Creator] = cell
	}
	if cell, ok := byCreator[111]; !ok || cell.Builders != 1 || cell.Pieces != 2 {
		t.Errorf("the single-builder cell = %+v, want 2 pieces from 1 builder", cell)
	}
	// The mixed cell belongs to the majority, and says it was not the only one there.
	if cell, ok := byCreator[222]; !ok || cell.Builders != 2 || cell.Pieces != 3 {
		t.Errorf("the mixed cell = %+v, want 3 pieces from 2 builders", cell)
	}
}

// Coarsening merges cells when there are too many. A builder's pieces have to survive that merge, or
// a zoomed-out map would take its colour from whichever child cell happened to win.
func TestCoarseningKeepsTheMajorityBuilder(t *testing.T) {
	points := []constructionPoint{}
	// One builder with a lot of pieces, spread over enough cells to force several coarsening rounds.
	for i := 0; i < maxConstructionCoverageCells+50; i++ {
		x := float32((i % 200) * constructionCoverageBaseCell)
		z := float32((i / 200) * constructionCoverageBaseCell)
		points = append(points, constructionPoint{Position: Vec3{X: x, Z: z}, Creator: 999})
	}
	// One interloper, outnumbered wherever it lands.
	points = append(points, constructionPoint{Position: Vec3{X: 0, Z: 0}, Creator: 1})

	coverage := aggregateConstructionCoverage(points)

	if coverage == nil || len(coverage.Cells) > maxConstructionCoverageCells {
		t.Fatalf("cells = %d, want at most %d", len(coverage.Cells), maxConstructionCoverageCells)
	}
	total := 0
	for _, cell := range coverage.Cells {
		total += cell.Pieces
		if cell.Creator != 999 {
			t.Errorf("cell %+v is credited to the interloper", cell)
		}
	}
	if total != len(points) {
		t.Errorf("coarsening lost pieces: %d of %d", total, len(points))
	}
}
