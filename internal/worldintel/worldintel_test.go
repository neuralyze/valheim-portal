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
	// relabel it, so a boat was drawn as a chest even once it classified as a hull. The fixture
	// carries the "rudder" float Ship::UpdateControlls writes on every physics tick, because that
	// is the evidence a real persisted boat has and it is what the classifier now leads on - a hull
	// NAME alone no longer makes something a boat, or Valheim's generated shipwreck_karve_bow props
	// would be boats.
	loaded := Object{Category: category("Karve")}
	values := valueMaps{
		s: map[int32]string{StableHash("items"): "AAAA"},
		f: map[int32]float32{StableHash("rudder"): 0},
	}
	semanticCategory(&loaded, values, nil)
	if loaded.Category != "vehicle" {
		t.Fatalf("a Karve carrying cargo was relabelled %q, want vehicle", loaded.Category)
	}
	// The same rule must still catch an actual chest, which is what makes the case above meaningful.
	chest := Object{Category: category("unnamed_thing")}
	semanticCategory(&chest, valueMaps{s: map[int32]string{StableHash("items"): "AAAA"}}, nil)
	if chest.Category != "container" {
		t.Fatalf("an object carrying cargo was classified %q, want container", chest.Category)
	}
}

// A sign is the only thing in a Valheim world that names a place in a player's own
// words, and before this it was the one thing guaranteed not to reach the map. Two
// ways at once: a server-spawned sign has creator 0 and falls to category "world"
// on its prefab name, which retain() drops; a player-built one carries a creator and
// became "construction", which finalize folds into the coverage layers and deletes
// from the object list. Both paths are exercised here because fixing one and not the
// other looks identical on a world where only the other kind exists.
func TestSignsAndPortalsCarryTheirInWorldLabel(t *testing.T) {
	const text = "Iron Era Workshop"
	for _, builder := range []struct {
		name    string
		creator int64
	}{{"server-spawned", 0}, {"player-built", 76561198000000000}} {
		sign := Object{Category: category("sign")}
		values := valueMaps{s: map[int32]string{StableHash("text"): text}}
		if builder.creator != 0 {
			values.l = map[int32]int64{StableHash("creator"): builder.creator}
		}
		semanticCategory(&sign, values, nil)
		if sign.Category != "waypoint" {
			t.Fatalf("%s sign classified %q, want waypoint", builder.name, sign.Category)
		}
		if sign.Label != text {
			t.Fatalf("%s sign label %q, want %q", builder.name, sign.Label, text)
		}
		if !retain(sign, values) {
			t.Fatalf("%s sign was dropped before it reached the snapshot", builder.name)
		}
	}
	// A portal's tag is promoted to the same field, because the map draws one label
	// and a renderer that has to know which of two fields holds it will pick wrong.
	// MEASURED on the pre-wipe Ulfsland save: 22 tagged portals, and the operator
	// spawned 3.54 m from one of them and could not tell what it was.
	portal := Object{Category: category("portal_wood")}
	semanticCategory(&portal, valueMaps{s: map[int32]string{StableHash("tag"): "u-workshop"}}, nil)
	if portal.Category != "portal" || portal.Label != "u-workshop" {
		t.Fatalf("portal classified %q with label %q, want portal / u-workshop",
			portal.Category, portal.Label)
	}
	// An untagged portal must NOT gain a label. A blank tag is the defect - it pairs
	// at random with every other blank-tag portal - and labelling it with the empty
	// string would draw an empty chip on the map that reads as a named place.
	blank := Object{Category: category("portal_wood")}
	semanticCategory(&blank, valueMaps{}, nil)
	if blank.Label != "" {
		t.Fatalf("an untagged portal gained the label %q", blank.Label)
	}
	// And a construction piece still loses to the creator branch, which is what keeps
	// 72,846 walls out of the object list.
	wall := Object{Category: category("wood_wall")}
	semanticCategory(&wall, valueMaps{l: map[int32]int64{StableHash("creator"): 7}}, nil)
	if wall.Category != "construction" {
		t.Fatalf("a built wall classified %q, want construction", wall.Category)
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
				// A builder id, because only player-placed pieces reach the coverage layer now:
				// pieces with creator 0 are what a generated crypt is made of, and drawing those
				// as "player construction" is the defect this split changed.
				Creator: 4242,
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

// Two people extending the same building have built ONE structure, and the map has to say so while
// still answering "who built that". Clustering used to be keyed per builder, which drew two
// overlapping circles on the same roof; it is now spatial, and the shared structure carries the
// majority builder plus the number of people who worked on it.
func TestSharedStructureIsOneClusterNamingItsMajorityBuilder(t *testing.T) {
	points := []constructionPoint{}
	for i := range 6 {
		points = append(points, constructionPoint{Position: Vec3{X: float32(10 + i), Z: 10}, Creator: 111})
	}
	for i := range 9 {
		points = append(points, constructionPoint{Position: Vec3{X: float32(10 + i), Z: 12}, Creator: 222})
	}

	clusters := aggregateConstructionClusters(points)

	if len(clusters) != 1 {
		t.Fatalf("two builders two metres apart produced %d structures, want 1: %+v", len(clusters), clusters)
	}
	if clusters[0].Pieces != 15 {
		t.Fatalf("structure holds %d pieces, want 15", clusters[0].Pieces)
	}
	if clusters[0].Builders != 2 {
		t.Fatalf("structure reports %d builders, want 2", clusters[0].Builders)
	}
	if clusters[0].Creator != 222 {
		t.Fatalf("structure names builder %d, want 222 - the one that placed 9 of the 15 pieces", clusters[0].Creator)
	}
}

// The other half of the same contract: two people building in different valleys are two structures,
// each named for whoever built it. Without this the test above would pass on a classifier that
// merged the whole world into one cluster.
func TestDistantBuildersAreSeparateStructures(t *testing.T) {
	points := []constructionPoint{}
	for i := range 6 {
		points = append(points, constructionPoint{Position: Vec3{X: float32(10 + i), Z: 10}, Creator: 111})
		points = append(points, constructionPoint{Position: Vec3{X: float32(600 + i), Z: 900}, Creator: 222})
	}

	clusters := aggregateConstructionClusters(points)

	creators := map[int64]int{}
	for _, cluster := range clusters {
		if cluster.Builders != 1 {
			t.Errorf("structure at (%.0f, %.0f) reports %d builders, want 1", cluster.Center.X, cluster.Center.Z, cluster.Builders)
		}
		creators[cluster.Creator] += cluster.Pieces
	}
	if len(clusters) != 2 {
		t.Fatalf("two distant builders produced %d structures, want 2: %+v", len(clusters), clusters)
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

// Valheim's generated shipwrecks are static props called shipwreck_karve_bow, _stern, _sternpost and
// _dragonhead, and the hull-name heuristic matches every one of them on "karve". Measured on
// Vangard, 25 of the 36 objects the name rule called vehicles were wreck parts, so the boats layer
// was three quarters scenery. A wreck has no vehicle component key and no builder; a real boat has
// at least one of the two, and the classifier must lead on that.
func TestOnlyComponentEvidenceOrABuilderMakesAVehicle(t *testing.T) {
	for _, testCase := range []struct {
		label   string
		prefab  string
		values  valueMaps
		catalog map[int32]string
		want    string
	}{
		{
			label:  "a generated shipwreck prop is scenery, not a boat",
			prefab: "shipwreck_karve_bow",
			want:   "world",
		},
		{
			label:  "a Ship writes ZDOVars.s_rudder every physics tick",
			prefab: "Karve",
			values: valueMaps{f: map[int32]float32{StableHash("rudder"): 0.25}},
			want:   "vehicle",
		},
		{
			label:  "a Ship also writes ZDOVars.s_forward",
			prefab: "Raft",
			values: valueMaps{i: map[int32]int32{StableHash("forward"): 1}},
			want:   "vehicle",
		},
		{
			label:  "Vagon keys its attach state under ZDOVars.s_attachJointHash",
			prefab: "Cart",
			values: valueMaps{i: map[int32]int32{StableHash("attachJoint"): 0}},
			want:   "vehicle",
		},
		{
			// The whole point of classifying on the component rather than the name: Vangard holds
			// mod vehicles whose prefab hash the catalog cannot even resolve, and they are found.
			label:  "a mod vehicle with no resolvable name is still a vehicle",
			prefab: "",
			values: valueMaps{f: map[int32]float32{StableHash("rudder"): 0}},
			want:   "vehicle",
		},
		{
			label:  "a hull somebody placed but never sailed is admitted on the builder",
			prefab: "Raft",
			values: valueMaps{l: map[int32]int64{StableHash("creator"): 4242}},
			want:   "vehicle",
		},
		{
			// Measured on Ulfsland: the Cart at (-2312, 63, 1918) has no builder and none of the
			// component keys, because Vagon only writes attachJoint once somebody attaches it. The
			// game files it under Assets/GameElements/Cart/, and that is what saves it.
			label:   "a never-used cart is admitted on the game's own asset taxonomy",
			prefab:  "Cart",
			catalog: map[int32]string{StableHash("vehicle:Cart"): "vehicle:Cart"},
			want:    "vehicle",
		},
		{
			// The taxonomy must not rescue the wreck: the game files those props under
			// Assets/world/Props/ShipwreckKarve/, so no marker is registered for them.
			label:   "the taxonomy does not admit a wreck that shares a hull noun",
			prefab:  "shipwreck_karve_bow",
			catalog: map[int32]string{StableHash("vehicle:Cart"): "vehicle:Cart"},
			want:    "world",
		},
	} {
		object := Object{Prefab: testCase.prefab, Category: category(testCase.prefab)}
		semanticCategory(&object, testCase.values, testCase.catalog)
		if object.Category != testCase.want {
			t.Errorf("%s: %q classified %q, want %q", testCase.label, testCase.prefab, object.Category, testCase.want)
		}
	}
}

// Generated location pieces are what a crypt, a ruin and a village are made of, and Valheim stamps
// no builder on them. Clustering them as "player construction" put bases in valleys nobody had
// visited; the location pins already mark those places.
func TestOnlyPlayerPlacedPiecesBecomeStructures(t *testing.T) {
	snapshot := Snapshot{Summary: Summary{Categories: map[string]int{}}}
	for index := range 40 {
		// A generated ruin: construction-shaped, no builder.
		snapshot.Objects = append(snapshot.Objects, Object{
			ID: uint32(index + 1), Category: "construction",
			Position: Vec3{X: float32(100 + index%5), Y: 30, Z: float32(100 + index/5)},
		})
	}
	for index := range 12 {
		snapshot.Objects = append(snapshot.Objects, Object{
			ID: uint32(index + 100), Category: "construction", Creator: 777,
			Position: Vec3{X: float32(-500 + index%4), Y: 30, Z: float32(-500 + index/4)},
		})
	}
	finalize(&snapshot)

	if snapshot.Summary.GeneratedPieces != 40 {
		t.Fatalf("generated pieces = %d, want 40", snapshot.Summary.GeneratedPieces)
	}
	if snapshot.Summary.PlayerPieces != 12 {
		t.Fatalf("player pieces = %d, want 12", snapshot.Summary.PlayerPieces)
	}
	if len(snapshot.Clusters) != 1 {
		t.Fatalf("structures = %d, want 1 - only the player's: %+v", len(snapshot.Clusters), snapshot.Clusters)
	}
	if snapshot.Clusters[0].Creator != 777 || snapshot.Clusters[0].Pieces != 12 {
		t.Fatalf("structure = %+v, want 12 pieces by builder 777", snapshot.Clusters[0])
	}
	if snapshot.Clusters[0].Center.X > -400 {
		t.Fatalf("the one structure is centred at x=%v, which is the generated ruin's ground, not the player's",
			snapshot.Clusters[0].Center.X)
	}
	if snapshot.ConstructionCoverage.TotalPieces != 12 {
		t.Fatalf("coverage totals %d pieces, want 12", snapshot.ConstructionCoverage.TotalPieces)
	}
	// A structure's footprint is what the map draws its outline from, so it has to be the real
	// extent and not a circle's bounding box.
	bounds := snapshot.Clusters[0].Bounds
	if bounds[0] != -500 || bounds[1] != -500 || bounds[2] != -497 || bounds[3] != -498 {
		t.Fatalf("footprint = %v, want [-500 -500 -497 -498] for a 4x3 block of pieces", bounds)
	}
}

// A boat drawn without a heading is a dot, and the quantised rotation has two wire forms. Getting
// the packed form's bit field wrong would leave every rotated-in-three-axes object pointing the
// wrong way, which is invisible until somebody checks a boat against the game.
func TestSmallRotationDecodesBothWireForms(t *testing.T) {
	// Y-only: ZPackage::WriteSmallRotation stores (y*2) | 0x8000 as a single int16. The two rotated
	// ZDOs in Ulfsland's 00_00__0_1.chunk hold 0x8198 and 0x82a6.
	for _, testCase := range []struct {
		packed uint16
		want   float32
	}{
		{0x8198, 204},
		{0x82a6, 339},
		{0x8000, 0},
	} {
		r := &reader{r: bytes.NewReader([]byte{byte(testCase.packed), byte(testCase.packed >> 8)})}
		got, err := r.smallRotation()
		if err != nil {
			t.Fatal(err)
		}
		if got != testCase.want {
			t.Errorf("Y-only rotation %#04x decoded %v degrees, want %v", testCase.packed, got, testCase.want)
		}
	}
	// All three: packed = x | y<<10 | z<<20 in half-degrees, written as the high uint16 then the low
	// one. 30 degrees of pitch, 150 of yaw, 200 of roll.
	const halfX, halfY, halfZ = 60, 300, 400
	packed := uint32(halfX) | uint32(halfY)<<10 | uint32(halfZ)<<20
	high, low := uint16(packed>>16), uint16(packed)
	r := &reader{r: bytes.NewReader([]byte{byte(high), byte(high >> 8), byte(low), byte(low >> 8)})}
	got, err := r.smallRotation()
	if err != nil {
		t.Fatal(err)
	}
	if got != 150 {
		t.Fatalf("packed rotation decoded %v degrees of yaw, want 150 - the Y field is bits 10..19", got)
	}
}
