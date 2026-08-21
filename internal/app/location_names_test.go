package app

import (
	"encoding/json"
	"html"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"unicode"

	"github.com/neuralyze/valheim-portal/internal/worldintel"
)

// Every id in these tests is a real location name taken from the Hrafnheim snapshot
// (world_analysis_snapshots, 13,279 locations over 326 distinct names). Nothing here is invented:
// an id that is not in a world is not a case worth defending.
func TestLocationDisplayNameDerivesRealNames(t *testing.T) {
	for _, testCase := range []struct {
		raw     string
		display string
		mod     string
	}{
		// Already-readable and single-word ids pass through untouched. Bonemass is the control: a
		// deriver that "improves" this one is broken.
		{raw: "Bonemass", display: "Bonemass"},
		{raw: "Dragonqueen", display: "Dragonqueen"},
		{raw: "StartTemple", display: "Start Temple"},
		{raw: "SulfurArch", display: "Sulfur Arch"},
		{raw: "StoneCircle", display: "Stone Circle"},

		// PascalCase splits at the boundary the author wrote, and the variant number goes.
		{raw: "AshlandRuins", display: "Ashland Ruins"},
		{raw: "InfestedTree01", display: "Infested Tree"},
		{raw: "AbandonedLogCabin02", display: "Abandoned Log Cabin"},
		{raw: "WoodHouse10", display: "Wood House"},
		{raw: "StoneTowerRuins03", display: "Stone Tower Ruins"},
		{raw: "Crypt2", display: "Crypt"},
		{raw: "Ruin1", display: "Ruin"},
		{raw: "SunkenCrypt4", display: "Sunken Crypt"},
		{raw: "MorgenHole1", display: "Morgen Hole"},
		{raw: "Waymarker01", display: "Waymarker"},

		// Compounds the author wrote as one lowercase run stay one word.
		{raw: "VoltureNest", display: "Volture Nest"},
		{raw: "Greydwarf_camp1", display: "Greydwarf Camp"},
		{raw: "Runestone_Greydwarfs", display: "Runestone Greydwarfs"},
		{raw: "Mistlands_RoadPost1", display: "Mistlands Road Post"},
		{raw: "Hildir_plainsfortress", display: "Hildir Plainsfortress"},
		{raw: "Hildir_cave", display: "Hildir Cave"},
		{raw: "Hildir_crypt", display: "Hildir Crypt"},

		// A lowercase segment before any variant number is the thing itself; BogWitch carries the
		// author's own boundary, and Valheim's own update ships the name as two words.
		{raw: "BogWitch_Camp", display: "Bog Witch Camp"},
		{raw: "CharredStone_Spawner", display: "Charred Stone Spawner"},
		{raw: "Vendor_BlackForest", display: "Vendor Black Forest"},

		// A lowercase segment after a variant number is a qualifier and keeps its meaning.
		{raw: "CharredTowerRuins1_dvergr", display: "Charred Tower Ruins (Dvergr)"},
		{raw: "Mistlands_GuardTower1_ruined_new", display: "Mistlands Guard Tower (Ruined)"},
		{raw: "Mistlands_GuardTower1_ruined_new2", display: "Mistlands Guard Tower (Ruined)"},
		// "_new" alone marks a re-authored asset: no un-suffixed sibling exists in the corpus, so it
		// separates this prefab from nothing.
		{raw: "Mistlands_GuardTower1_new", display: "Mistlands Guard Tower"},
		{raw: "Mistlands_Lighthouse1_new", display: "Mistlands Lighthouse"},

		// A mod tag is provenance, not the first word of the name.
		{raw: "MWL_AshlandsFort1", display: "Ashlands Fort", mod: "MWL"},
		{raw: "MWL_AbandonedHouse1", display: "Abandoned House", mod: "MWL"},
		{raw: "MWL_AshWallPost1", display: "Ash Wall Post", mod: "MWL"},
		{raw: "MWL_AncientShrine1", display: "Ancient Shrine", mod: "MWL"},
		{raw: "MWL_DvergrEitrSingularity1", display: "Dvergr Eitr Singularity", mod: "MWL"},
		{raw: "MWL_RuinedRootTower5", display: "Ruined Root Tower", mod: "MWL"},
		{raw: "MWL_MountainsAedicule1", display: "Mountains Aedicule", mod: "MWL"},
		{raw: "MWL_StoreHouseStone1", display: "Store House Stone", mod: "MWL"},
		{raw: "BFD_Exterior", display: "Exterior", mod: "BFD"},
		{raw: "CD_Exterior1", display: "Exterior", mod: "CD"},

		// The acronym's tail starts the next word.
		{raw: "GDKing", display: "GD King"},

		// The one id in the corpus whose own boundaries are not enough, and the only table entry.
		{raw: "PlaceofMystery1", display: "Place of Mystery"},

		// No name to derive means no name is shown.
		{raw: "", display: ""},
		{raw: "   ", display: ""},
	} {
		got := locationDisplayName(testCase.raw)
		if got.Display != testCase.display || got.Mod != testCase.mod {
			t.Errorf("locationDisplayName(%q) = {display:%q mod:%q}, want {display:%q mod:%q}",
				testCase.raw, got.Display, got.Mod, testCase.display, testCase.mod)
		}
	}
}

// corpusCompounds are words the prefab authors wrote as a single lowercase run. A splitter that
// consults a dictionary rather than the boundaries in the id would break every one of these -
// "Greydwarf" into "Grey Dwarf", "Dragonqueen" into "Dragon Queen" - so each is asserted to survive
// whole.
func TestLocationDisplayNameKeepsCompoundsWhole(t *testing.T) {
	for _, testCase := range []struct{ raw, compound string }{
		{raw: "Greydwarf_camp1", compound: "Greydwarf"},
		{raw: "Runestone_Greydwarfs", compound: "Greydwarfs"},
		{raw: "MWL_DvergrHouseWood2", compound: "Dvergr"},
		{raw: "CharredTowerRuins1_dvergr", compound: "Dvergr"},
		{raw: "VoltureNest", compound: "Volture"},
		{raw: "Mistlands_Viaduct2", compound: "Mistlands"},
		{raw: "Runestone_Mistlands", compound: "Mistlands"},
		{raw: "Dragonqueen", compound: "Dragonqueen"},
		{raw: "Bonemass", compound: "Bonemass"},
		{raw: "Eikthyrnir", compound: "Eikthyrnir"},
		{raw: "Hildir_plainsfortress", compound: "Plainsfortress"},
		{raw: "MWL_MinddripHallow1", compound: "Minddrip"},
		{raw: "MWL_FulingTarPit1", compound: "Fuling"},
		{raw: "MWL_FortBakkarhalt1", compound: "Bakkarhalt"},
	} {
		display := locationDisplayName(testCase.raw).Display
		if !containsWord(display, testCase.compound) {
			t.Errorf("locationDisplayName(%q) = %q, want %q kept as one whole word",
				testCase.raw, display, testCase.compound)
		}
	}
}

// containsWord reports whether the compound survives as its own space-delimited word, so a display
// name that merely happens to contain the letters does not pass.
func containsWord(display, compound string) bool {
	for _, field := range strings.Fields(strings.NewReplacer("(", " ", ")", " ", ",", " ").Replace(display)) {
		if field == compound {
			return true
		}
	}
	return false
}

// A display name is what a reader sees, so none of the id's machinery may reach it: no underscore,
// no asset index, and never an empty label for an id that had letters in it.
func TestLocationDisplayNameLeavesNoIDMachinery(t *testing.T) {
	for _, raw := range []string{
		"MWL_AshlandsFort4", "MWL_Port6", "MWL_Ruins8", "MWL_RuinsTower8", "MWL_SwampAltar3",
		"MWL_FulingStoneHouse5", "MWL_MeadowsTomb4", "MWL_ForestTower2", "MWL_DeerShrine2",
		"CharredStone_Spawner", "CharredTowerRuins1_dvergr", "Greydwarf_camp1", "GoblinCamp2",
		"Hildir_camp", "Hildir_plainsfortress", "InfestedTree01", "Crypt3", "Ruin2", "GDKing",
		"Mistlands_DvergrTownEntrance2", "Mistlands_GuardTower3_ruined_new", "Mistlands_Swords3",
		"MorgenHole3", "MountainCave02", "PlaceofMystery2", "Runestone_BlackForest",
		"ShipWreck04", "StoneHenge6", "StoneTowerRuins10", "SwampHut5", "TarPit3", "TrollCave02",
		"Vendor_BlackForest", "VoltureNest", "Waymarker02", "WoodVillage1", "BFD_Exterior",
		"CD_Exterior1", "StartTemple", "Bonemass",
	} {
		display := locationDisplayName(raw).Display
		if display == "" {
			t.Errorf("locationDisplayName(%q) produced no display name", raw)
			continue
		}
		if strings.Contains(display, "_") {
			t.Errorf("locationDisplayName(%q) = %q, still carries an underscore", raw, display)
		}
		for _, r := range display {
			if unicode.IsDigit(r) {
				t.Errorf("locationDisplayName(%q) = %q, still carries an asset index", raw, display)
				break
			}
		}
	}
}

// The legend is keyed on the raw prefab id, because that is the only thing the payloads carry and
// the only thing an operator can search a spawn for.
func TestLocationNameLegendKeepsRawIDs(t *testing.T) {
	legend := locationNameLegend([]worldintel.Location{
		{Name: "MWL_AshlandsFort1"},
		{Name: "MWL_AshlandsFort1"},
		{Name: "Greydwarf_camp1"},
		{Name: ""},
	})
	var decoded map[string]locationName
	if err := json.Unmarshal([]byte(legend), &decoded); err != nil {
		t.Fatalf("legend %q is not valid json: %v", legend, err)
	}
	if len(decoded) != 2 {
		t.Fatalf("legend = %#v, want the two distinct named locations", decoded)
	}
	if entry := decoded["MWL_AshlandsFort1"]; entry.Display != "Ashlands Fort" || entry.Mod != "MWL" {
		t.Errorf("legend[MWL_AshlandsFort1] = %#v", entry)
	}
	if entry := decoded["Greydwarf_camp1"]; entry.Display != "Greydwarf Camp" || entry.Mod != "" {
		t.Errorf("legend[Greydwarf_camp1] = %#v", entry)
	}
}

// The legend reaches the canvas on the page, not in the payloads, because overlay tiles are built
// once and served from disk: a tile written before this existed still carries only the prefab id.
// So the page has to be the thing that arrives with the names on it.
func TestTheOperatorsMapPageCarriesTheDerivedLocationNames(t *testing.T) {
	server := testServer(t)
	const world = "Midgard"
	if err := server.store.UpsertPublicWorld(t.Context(), PublicWorld{
		Name: world, JoinAddress: "valheim.example.test:2456", Status: "online",
	}, "test"); err != nil {
		t.Fatal(err)
	}
	if err := server.store.SaveWorldAnalysis(t.Context(), worldintel.Snapshot{
		Schema: worldintel.SchemaVersion,
		World:  world,
		Source: worldintel.Source{Backup: "world-Midgard-test.tgz", SHA256: strings.Repeat("a", 64)},
		Locations: []worldintel.Location{
			{Name: "MWL_AshlandsFort1", Category: "fortress", Position: worldintel.Vec3{X: 4, Z: 4}},
			{Name: "Greydwarf_camp1", Category: "settlement", Position: worldintel.Vec3{X: 8, Z: 8}},
		},
	}, "test"); err != nil {
		t.Fatal(err)
	}

	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, adminTestRequest(http.MethodGet, "/admin/worlds/"+world+"/map", nil))
	if response.Code != http.StatusOK {
		t.Fatalf("the operators map = %d: %s", response.Code, response.Body.String())
	}
	legend := locationNamesAttribute(t, response.Body.String())
	if entry := legend["MWL_AshlandsFort1"]; entry.Display != "Ashlands Fort" || entry.Mod != "MWL" {
		t.Errorf("the page's legend for MWL_AshlandsFort1 = %#v", entry)
	}
	if entry := legend["Greydwarf_camp1"]; entry.Display != "Greydwarf Camp" {
		t.Errorf("the page's legend for Greydwarf_camp1 = %#v", entry)
	}
}

// A legend naming every location in the world would undo the fog on the one map built to hold it:
// the names alone would tell a player which places exist and how many of each. So the players' page
// names only what that character has actually found.
func TestThePlayersLocationLegendNamesOnlyWhatTheyFound(t *testing.T) {
	server := testServer(t)
	const world, steamID = "Midgard", "76561190000000001"
	if err := os.MkdirAll(server.explorationRoot(world), 0o755); err != nil {
		t.Fatal(err)
	}
	const textureSize, pixelSize = 64, 10.0
	half := textureSize / 2
	report := buildReport(t, world, 222, textureSize, pixelSize, [][2]int{{half, half}})
	if err := os.WriteFile(filepath.Join(server.explorationRoot(world), "765611-222.explored"), report, 0o644); err != nil {
		t.Fatal(err)
	}
	if err := server.store.UpsertPublicWorld(t.Context(), PublicWorld{
		Name: world, JoinAddress: "valheim.example.test:2456", Status: "online",
	}, "test"); err != nil {
		t.Fatal(err)
	}
	if err := server.store.RecordSteamIdentity(t.Context(), steamID); err != nil {
		t.Fatal(err)
	}
	if err := server.store.GrantWorldAccess(t.Context(), world, steamID, "operator"); err != nil {
		t.Fatal(err)
	}
	if err := server.store.SaveWorldAnalysis(t.Context(), worldintel.Snapshot{
		Schema: worldintel.SchemaVersion,
		World:  world,
		Source: worldintel.Source{Backup: "world-Midgard-test.tgz", SHA256: strings.Repeat("b", 64)},
		Locations: []worldintel.Location{
			{Name: "StartTemple", Category: "spawn", Position: worldintel.Vec3{X: 4, Z: 4}},
			{Name: "Bonemass", Category: "boss", Position: worldintel.Vec3{X: 4000, Z: 4000}},
		},
	}, "test"); err != nil {
		t.Fatal(err)
	}

	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, signedIn(t, server, http.MethodGet, "/worlds/"+world+"/map?player=222", steamID))
	if response.Code != http.StatusOK {
		t.Fatalf("the players map = %d: %s", response.Code, response.Body.String())
	}
	legend := locationNamesAttribute(t, response.Body.String())
	if entry := legend["StartTemple"]; entry.Display != "Start Temple" {
		t.Errorf("the page's legend for StartTemple = %#v", entry)
	}
	if _, leaked := legend["Bonemass"]; leaked {
		t.Errorf("the legend names a boss site this character has never been near: %#v", legend)
	}
}

// locationNamesAttribute pulls the legend back off the rendered page, so the test reads what the
// canvas reads: through the template's attribute escaping, not around it.
func locationNamesAttribute(t *testing.T, page string) map[string]locationName {
	t.Helper()
	const marker = ` data-location-names="`
	start := strings.Index(page, marker)
	if start < 0 {
		t.Fatal("the page carries no data-location-names attribute")
	}
	rest := page[start+len(marker):]
	end := strings.Index(rest, `"`)
	if end < 0 {
		t.Fatal("the data-location-names attribute is unterminated")
	}
	var decoded map[string]locationName
	if err := json.Unmarshal([]byte(html.UnescapeString(rest[:end])), &decoded); err != nil {
		t.Fatalf("data-location-names is not json: %v", err)
	}
	return decoded
}
