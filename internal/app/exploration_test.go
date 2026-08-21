package app

import (
	"bytes"
	"compress/gzip"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/neuralyze/valheim-portal/internal/worldintel"
)

// buildReport writes what the plugin writes: a readable header line, then the player's grid as a
// gzipped bitset indexed [y * textureSize + x].
func buildReport(t *testing.T, world string, playerID int64, textureSize int, pixelSize float64, set [][2]int) []byte {
	t.Helper()
	bits := make([]byte, (textureSize*textureSize+7)/8)
	for _, cell := range set {
		index := cell[1]*textureSize + cell[0]
		bits[index>>3] |= 1 << (index & 7)
	}
	var body bytes.Buffer
	fmt.Fprintf(&body, "neuralyze-exploration 1 world=%s player_id=%d player_name=Kato texture_size=%d pixel_size=%g cells_uncovered=%d written=2026-08-16T00:00:00Z\n",
		world, playerID, textureSize, pixelSize, len(set))
	compressed := gzip.NewWriter(&body)
	if _, err := compressed.Write(bits); err != nil {
		t.Fatal(err)
	}
	if err := compressed.Close(); err != nil {
		t.Fatal(err)
	}
	return body.Bytes()
}

// The portal must place a reported cell where the game says it is. The game maps a world position to a
// grid cell with x = round(worldX / pixelSize) + textureSize/2, so the centre cell is the origin: get
// this inverse wrong and the fog is drawn over the wrong half of the world.
func TestAReportedGridIsPlacedWhereTheGameSaysItIs(t *testing.T) {
	server := testServer(t)
	world := "Midgard"
	directory := server.explorationRoot(world)
	if err := os.MkdirAll(directory, 0o755); err != nil {
		t.Fatal(err)
	}
	const textureSize, pixelSize = 64, 10.0
	half := textureSize / 2
	// The origin, and one cell 200 m north-east of it.
	report := buildReport(t, world, 111, textureSize, pixelSize, [][2]int{{half, half}, {half + 20, half + 20}})
	if err := os.WriteFile(filepath.Join(directory, "765611-111.explored"), report, 0o644); err != nil {
		t.Fatal(err)
	}

	mask := server.explorationUnion(world)

	if mask == nil {
		t.Fatal("no mask was produced from a valid report")
	}
	if mask.Players != 1 {
		t.Errorf("players = %d, want 1", mask.Players)
	}
	bits, err := base64.StdEncoding.DecodeString(mask.Bits)
	if err != nil {
		t.Fatal(err)
	}
	isSet := func(worldX, worldZ float64) bool {
		cellX := int((worldX - float64(mask.OriginX)) / float64(mask.CellSize))
		cellZ := int((worldZ - float64(mask.OriginZ)) / float64(mask.CellSize))
		index := cellZ*mask.Size + cellX
		return bits[index>>3]&(1<<(index&7)) != 0
	}
	if !isSet(0, 0) {
		t.Error("the origin cell the player uncovered is not in the mask")
	}
	if !isSet(200, 200) {
		t.Error("the cell 200 m north-east is not in the mask")
	}
	// And nothing else got set: a bad inverse would smear cells across the map.
	if isSet(-200, -200) || isSet(5000, 5000) {
		t.Error("the mask claims ground nobody reported")
	}
}

// Two players' maps are one shared map. This is the whole point of the union: what the server draws is
// everywhere anybody has been, not the last person to upload.
func TestReportsFromTwoPlayersAreUnioned(t *testing.T) {
	server := testServer(t)
	world := "Midgard"
	directory := server.explorationRoot(world)
	if err := os.MkdirAll(directory, 0o755); err != nil {
		t.Fatal(err)
	}
	const textureSize, pixelSize = 64, 10.0
	half := textureSize / 2
	if err := os.WriteFile(filepath.Join(directory, "765611-111.explored"),
		buildReport(t, world, 111, textureSize, pixelSize, [][2]int{{half, half}}), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(directory, "765622-222.explored"),
		buildReport(t, world, 222, textureSize, pixelSize, [][2]int{{half + 20, half}}), 0o644); err != nil {
		t.Fatal(err)
	}

	mask := server.explorationUnion(world)

	if mask == nil || mask.Players != 2 {
		t.Fatalf("mask = %+v, want two players", mask)
	}
	bits, _ := base64.StdEncoding.DecodeString(mask.Bits)
	isSet := func(worldX, worldZ float64) bool {
		cellX := int((worldX - float64(mask.OriginX)) / float64(mask.CellSize))
		cellZ := int((worldZ - float64(mask.OriginZ)) / float64(mask.CellSize))
		index := cellZ*mask.Size + cellX
		return bits[index>>3]&(1<<(index&7)) != 0
	}
	if !isSet(0, 0) || !isSet(200, 0) {
		t.Error("the union is missing ground one of the two players uncovered")
	}
}

// A report the portal cannot understand must be refused, not approximated: fog drawn from a misread
// grid would show ground nobody has visited, which is the one thing this feature must not do.
func TestAnUnreadableReportIsRefused(t *testing.T) {
	server := testServer(t)
	world := "Midgard"
	directory := server.explorationRoot(world)
	if err := os.MkdirAll(directory, 0o755); err != nil {
		t.Fatal(err)
	}
	cases := map[string][]byte{
		"not-a-report.explored": []byte("hello\n"),
		"truncated.explored":    buildReport(t, world, 1, 64, 10, nil)[:20],
		"implausible.explored":  buildReport(t, world, 1, 99999, 10, nil),
		"zero-pixel.explored":   buildReport(t, world, 1, 64, 0, nil),
	}
	for name, payload := range cases {
		if err := os.WriteFile(filepath.Join(directory, name), payload, 0o644); err != nil {
			t.Fatal(err)
		}
	}

	if mask := server.explorationUnion(world); mask != nil {
		t.Errorf("mask = %+v, want nothing from unreadable reports", mask)
	}
}

// The file name carries which character a report belongs to. Two characters on one account each keep
// their own map, and a name without an id is refused rather than filed under a guess.
func TestPlayerIDComesFromTheReportName(t *testing.T) {
	if id, err := playerIDFromReportName("Hrafnheim-308095166.explored"); err != nil || id != 308095166 {
		t.Errorf("id = %d, %v; want 308095166", id, err)
	}
	if id, err := playerIDFromReportName("Hrafnheim--322254472.pins.json"); err != nil || id != -322254472 {
		t.Errorf("negative id = %d, %v; want -322254472", id, err)
	}
	for _, bad := range []string{"Hrafnheim.explored", "Hrafnheim-.explored", "Hrafnheim-0.explored", "Hrafnheim-abc.explored"} {
		if _, err := playerIDFromReportName(bad); err == nil {
			t.Errorf("%q was accepted", bad)
		}
	}
}

// The upload is authorised by the profile-scoped token an ordinary sync already holds, and the account
// comes from that token rather than the payload: a client may say which character it is, never whose
// account. The character id comes from the file name, so two characters on one account keep two maps.
func TestExplorationUploadStoresPerAccountAndCharacter(t *testing.T) {
	server := testServer(t)
	release := Release{ID: "exploration-release", World: "Ashlands", Profile: "raiders", ClientType: "flat", Version: "1.0.0", Notes: "exploration"}
	publishProfile(t, server, release)
	if err := server.store.GrantWorldAccess(context.Background(), release.World, testSteamID, "admin"); err != nil {
		t.Fatal(err)
	}
	base := deviceTokenClaims{SteamID: testSteamID, World: release.World, Profile: release.Profile, ClientType: release.ClientType, ReleaseID: release.ID, ExpiresAt: time.Now().Add(time.Hour)}
	profileToken := server.mintDeviceToken(deviceTokenClaims{SteamID: base.SteamID, World: base.World, Profile: base.Profile, ClientType: base.ClientType, ReleaseID: base.ReleaseID, Scope: deviceTokenScopeProfile, ExpiresAt: base.ExpiresAt})
	diagnosticsToken := server.mintDeviceToken(deviceTokenClaims{SteamID: base.SteamID, World: base.World, Profile: base.Profile, ClientType: base.ClientType, ReleaseID: base.ReleaseID, Scope: deviceTokenScopeDiagnostics, ExpiresAt: base.ExpiresAt})
	target := "/client/exploration/" + release.World + "/" + release.Profile + "/" + release.ClientType

	post := func(token, field, filename string, payload []byte) int {
		var body bytes.Buffer
		form := multipart.NewWriter(&body)
		part, err := form.CreateFormFile(field, filename)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := part.Write(payload); err != nil {
			t.Fatal(err)
		}
		if err := form.Close(); err != nil {
			t.Fatal(err)
		}
		request := httptest.NewRequest(http.MethodPost, target, &body)
		request.Header.Set("Content-Type", form.FormDataContentType())
		request.Header.Set("Authorization", "Bearer "+token)
		response := httptest.NewRecorder()
		server.Handler().ServeHTTP(response, request)
		return response.Code
	}

	report := buildReport(t, release.World, 111, 64, 10, [][2]int{{32, 32}})
	if code := post(profileToken, "explored", release.World+"-111.explored", report); code != http.StatusCreated {
		t.Fatalf("upload with the profile token = %d, want 201", code)
	}
	// The diagnostics scope is for a different, operator-initiated flow and must not carry a map.
	if code := post(diagnosticsToken, "explored", release.World+"-111.explored", report); code != http.StatusUnauthorized {
		t.Errorf("upload with the diagnostics token = %d, want 401", code)
	}
	// A name that carries no character is refused rather than filed under a guess.
	if code := post(profileToken, "explored", "nonsense.explored", report); code != http.StatusBadRequest {
		t.Errorf("upload with no player id in the name = %d, want 400", code)
	}

	stored := filepath.Join(server.explorationRoot(release.World), testSteamID+"-111.explored")
	if _, err := os.Stat(stored); err != nil {
		t.Fatalf("report not stored under the account and character: %v", err)
	}
	// And it is readable as what it claims to be, which is what the map will do with it.
	if mask := server.explorationUnion(release.World); mask == nil || mask.Players != 1 {
		t.Errorf("mask = %+v, want one player's map", mask)
	}
}

// The world root is mounted read-only in the portal container - it is the game's data, owned by the
// server containers - so a report written there fails with a 500 the moment a real client uploads one.
// Reports are the portal's own state and belong in its writable artifact root.
func TestReportsAreStoredWhereThePortalCanWrite(t *testing.T) {
	server := testServer(t)
	root := server.explorationRoot("Midgard")
	if !strings.HasPrefix(root, server.cfg.ArtifactRoot) {
		t.Errorf("reports go to %q, which is outside the writable artifact root %q", root, server.cfg.ArtifactRoot)
	}
	if server.cfg.MapSourceRoot != "" && strings.HasPrefix(root, server.cfg.MapSourceRoot) {
		t.Errorf("reports go to %q, inside the read-only world root", root)
	}
}

// Two characters on one Steam account are two maps. The storage key is the account AND the character, so
// they never overwrote each other - but the map unioned them, which is exactly what an operator does not
// want when one of those characters is an admin who has uncovered half the world.
func TestOneCharacterSeesOnlyItsOwnMap(t *testing.T) {
	server := testServer(t)
	world := "Midgard"
	if err := os.MkdirAll(server.explorationRoot(world), 0o755); err != nil {
		t.Fatal(err)
	}
	const textureSize, pixelSize = 64, 10.0
	half := textureSize / 2
	// Same Steam account, two characters: an admin who has been far afield, and a newcomer at spawn.
	admin := buildReport(t, world, 111, textureSize, pixelSize, [][2]int{{half, half}, {half + 20, half + 20}})
	newcomer := buildReport(t, world, 222, textureSize, pixelSize, [][2]int{{half, half}})
	if err := os.WriteFile(filepath.Join(server.explorationRoot(world), "765611-111.explored"), admin, 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(server.explorationRoot(world), "765611-222.explored"), newcomer, 0o644); err != nil {
		t.Fatal(err)
	}

	// Both characters are offered, and both are listed separately despite sharing an account.
	reporters := server.explorationReporters(world)
	if len(reporters) != 2 {
		t.Fatalf("reporters = %+v, want both characters", reporters)
	}

	snapshot := worldintel.Snapshot{
		GeneratedZones: []worldintel.Vec2{{X: 0, Y: 0}, {X: 10, Y: 10}},
		Locations: []worldintel.Location{
			{Name: "StartTemple", Position: worldintel.Vec3{X: 4, Z: 4}},
			{Name: "Eikthyrnir", Position: worldintel.Vec3{X: 200, Z: 200}},
		},
	}

	// The newcomer's view must not include the ground only the admin character has walked.
	asNewcomer := clipToDiscovered(snapshot, server.discoveredFor(world, snapshot, 222))
	if len(asNewcomer.Locations) != 1 || asNewcomer.Locations[0].Name != "StartTemple" {
		t.Errorf("as the newcomer: %+v, want only what that character found", asNewcomer.Locations)
	}
	// The admin character sees both, because that character really has been there.
	asAdmin := clipToDiscovered(snapshot, server.discoveredFor(world, snapshot, 111))
	if len(asAdmin.Locations) != 2 {
		t.Errorf("as the admin character: %+v, want both", asAdmin.Locations)
	}
	// A character that has never reported has discovered nothing - falling back to the union here would
	// hand over everybody else's map, which is the leak this whole selector exists to prevent.
	asStranger := clipToDiscovered(snapshot, server.discoveredFor(world, snapshot, 999))
	if len(asStranger.Locations) != 0 {
		t.Errorf("as a character with no report: %+v, want nothing", asStranger.Locations)
	}
}

// The reporter writes a filename-safe name, so "Ai Test" arrives as "Ai_Test". The server plugin
// recorded the real one when that character connected, and the selector should show that instead.
func TestTheSelectorPrefersTheNameTheServerRecorded(t *testing.T) {
	server := testServer(t)
	world := "Midgard"
	if err := os.MkdirAll(server.explorationRoot(world), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(server.explorationRoot(world), "765611-484206363.explored"),
		buildReport(t, world, 484206363, 64, 10, [][2]int{{32, 32}}), 0o644); err != nil {
		t.Fatal(err)
	}
	identities := filepath.Join(server.cfg.MapSourceRoot, world, "config_merged")
	if err := os.MkdirAll(identities, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(identities, "player_identities.json"),
		[]byte(`{"schema":1,"players":[{"player_id":484206363,"name":"Ai Test"}]}`), 0o644); err != nil {
		t.Fatal(err)
	}

	reporters := server.explorationReporters(world)

	if len(reporters) != 1 || reporters[0].Name != "Ai Test" {
		t.Errorf("reporters = %+v, want the server's spelling", reporters)
	}
}

// The reporter uploads at logout with a scope of its own, because that token lives inside a game process
// shared with a hundred other mods. It may send a map and do nothing else: the profile scope can fetch
// profile payloads, and the diagnostics scope belongs to an operator-initiated flow.
func TestTheNarrowExplorationScopeMayOnlyUploadMaps(t *testing.T) {
	server := testServer(t)
	release := Release{ID: "scope-release", World: "Ashlands", Profile: "raiders", ClientType: "flat", Version: "1.0.0", Notes: "scope"}
	publishProfile(t, server, release)
	if err := server.store.GrantWorldAccess(context.Background(), release.World, testSteamID, "admin"); err != nil {
		t.Fatal(err)
	}
	base := deviceTokenClaims{SteamID: testSteamID, World: release.World, Profile: release.Profile, ClientType: release.ClientType, ReleaseID: release.ID, ExpiresAt: time.Now().Add(time.Hour)}
	mint := func(scope string) string {
		claims := base
		claims.Scope = scope
		return server.mintDeviceToken(claims)
	}

	post := func(token string) int {
		var body bytes.Buffer
		form := multipart.NewWriter(&body)
		part, err := form.CreateFormFile("explored", release.World+"-111.explored")
		if err != nil {
			t.Fatal(err)
		}
		if _, err := part.Write(buildReport(t, release.World, 111, 64, 10, [][2]int{{32, 32}})); err != nil {
			t.Fatal(err)
		}
		if err := form.Close(); err != nil {
			t.Fatal(err)
		}
		request := httptest.NewRequest(http.MethodPost, "/client/exploration/"+release.World+"/"+release.Profile+"/"+release.ClientType, &body)
		request.Header.Set("Content-Type", form.FormDataContentType())
		request.Header.Set("Authorization", "Bearer "+token)
		response := httptest.NewRecorder()
		server.Handler().ServeHTTP(response, request)
		return response.Code
	}

	if code := post(mint(deviceTokenScopeExploration)); code != http.StatusCreated {
		t.Errorf("the exploration scope = %d, want 201", code)
	}
	if code := post(mint(deviceTokenScopeDiagnostics)); code != http.StatusUnauthorized {
		t.Errorf("the diagnostics scope = %d, want 401", code)
	}
	// And the narrow token cannot do the things the profile token can.
	for _, target := range []string{
		"/client/manifest/" + release.World + "/" + release.Profile + "/" + release.ClientType,
		"/client/payload/" + release.World + "/" + release.Profile + "/" + release.ClientType,
	} {
		request := httptest.NewRequest(http.MethodGet, target, nil)
		request.Header.Set("Authorization", "Bearer "+mint(deviceTokenScopeExploration))
		response := httptest.NewRecorder()
		server.Handler().ServeHTTP(response, request)
		if response.Code == http.StatusOK {
			t.Errorf("%s answered 200 to a token that may only upload maps", target)
		}
	}
}

// The six pins the operator's own client uploaded for Hrafnheim on 2026-08-21, verbatim, including
// the duplicate "Shipwreck Chest". Real data, so the numeric Minimap.PinType values are the ones the
// map has to be able to draw.
const operatorPinsFile = `{"schema":1,"world":"Midgard","player_id":-322254472,"written":"2026-08-21T06:45:27Z","pins":[
{"name":"Crypt","type":3,"type_name":"icon3","x":122,"z":502.9,"crossed_off":false,"owner_id":0},
{"name":"","type":0,"type_name":"icon0","x":53.46,"z":229.72,"crossed_off":false,"owner_id":0},
{"name":"","type":0,"type_name":"icon0","x":242.59,"z":740.88,"crossed_off":false,"owner_id":0},
{"name":"Shipwreck Chest","type":3,"type_name":"icon3","x":340.4,"z":1110.8,"crossed_off":false,"owner_id":0},
{"name":"Shipwreck Chest","type":3,"type_name":"icon3","x":340.4,"z":1110.8,"crossed_off":false,"owner_id":0},
{"name":"BASE 1","type":1,"type_name":"icon1","x":3.83,"z":323.49,"crossed_off":true,"owner_id":0}]}`

func writeOperatorPins(t *testing.T, server *Server, world string) {
	t.Helper()
	directory := server.explorationRoot(world)
	if err := os.MkdirAll(directory, 0o755); err != nil {
		t.Fatal(err)
	}
	name := filepath.Join(directory, "76561197987967077--322254472.pins.json")
	if err := os.WriteFile(name, []byte(operatorPinsFile), 0o644); err != nil {
		t.Fatal(err)
	}
}

// The operator's map had a pins layer that could never draw anything: the checkbox was hidden behind
// {{if not .Admin}} and the admin payload did not carry pins at all. Both halves are asserted here,
// because either one alone leaves the layer invisible - the same trap the boats fell into.
func TestAdminAnalysisPayloadCarriesPinsWithTheirValheimType(t *testing.T) {
	server := testServer(t)
	world := "Midgard"
	writeOperatorPins(t, server, world)
	snapshot := worldintel.Snapshot{
		Schema:  worldintel.SchemaVersion,
		World:   world,
		Source:  worldintel.Source{Backup: "world-Midgard-test.tgz", SHA256: strings.Repeat("b", 64)},
		Summary: worldintel.Summary{Categories: map[string]int{}},
	}
	if err := server.store.SaveWorldAnalysis(t.Context(), snapshot, "test"); err != nil {
		t.Fatal(err)
	}
	request := httptest.NewRequest(http.MethodGet, "/admin/worlds/"+world+"/analysis.json", nil)
	request.RemoteAddr = "192.0.2.10:12345"
	request.Header.Set("X-Forwarded-User", "admin@example.test")
	request.Header.Set(adminTokenHeader, testAdminToken)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("admin analysis = %d: %s", response.Code, response.Body.String())
	}
	var payload struct {
		Pins []explorationPins `json:"pins"`
	}
	if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if len(payload.Pins) != 6 {
		t.Fatalf("admin payload carried %d pins, want 6", len(payload.Pins))
	}
	types := map[int]int{}
	for _, pin := range payload.Pins {
		types[pin.Type]++
		if pin.PlayerID != -322254472 {
			t.Errorf("pin %q lost its placer: player_id=%d", pin.Name, pin.PlayerID)
		}
	}
	// Icon3 x3, Icon0 x2, Icon1 x1: the ordinal is what picks the glyph, so it has to survive the
	// round trip rather than being flattened to the "icon3" string beside it.
	for ordinal, want := range map[int]int{3: 3, 0: 2, 1: 1} {
		if types[ordinal] != want {
			t.Errorf("pin type %d appeared %d times, want %d", ordinal, types[ordinal], want)
		}
	}
	// The duplicate is carried through, not deduplicated: two markers on one spot is a fact about
	// what the player did, and the canvas counts them into one label instead of dropping one.
	duplicates := 0
	for _, pin := range payload.Pins {
		if pin.Name == "Shipwreck Chest" && pin.X == 340.4 && pin.Z == 1110.8 {
			duplicates++
		}
	}
	if duplicates != 2 {
		t.Errorf("the duplicate Shipwreck Chest pin arrived %d time(s), want 2", duplicates)
	}
}

// One contributor is the state tonight, so the legend has to be right with exactly one row - and the
// colour it shows has to be the colour the canvas draws, or the legend is lying about the map.
func TestPinLegendAttributesEachContributorLikeABuilder(t *testing.T) {
	server := testServer(t)
	world := "Midgard"
	writeOperatorPins(t, server, world)
	styles := map[string]map[string]string{}
	owners := server.pinLegend(t.Context(), world, server.reportedPins(world), styles)
	if len(owners) != 1 {
		t.Fatalf("pin owners = %d, want 1", len(owners))
	}
	owner := owners[0]
	if owner.PlayerID != -322254472 || owner.Pins != 6 || owner.Struck != 1 {
		t.Fatalf("owner row = %+v, want the 6 pins with 1 crossed off", owner)
	}
	if owner.Colour != builderColour(-322254472) {
		t.Errorf("pin colour %q is not the builder colour %q for the same character", owner.Colour, builderColour(-322254472))
	}
	// No identity plugin file in this world, so the row must say what it knows rather than invent a
	// name; the Steam id in the file name is not a character name and is never used as one.
	if owner.Named || owner.Label != builderFallbackName(-322254472) {
		t.Errorf("unknown character row = %+v, want the id-derived stand-in", owner)
	}
	// The canvas colours and names a pin through the same fold as a cluster, so a contributor who
	// placed markers and never built has to be in it.
	style := styles["-322254472"]
	if style == nil || style["colour"] != owner.Colour || style["name"] != owner.Label || style["unnamed"] != "1" {
		t.Errorf("canvas style fold = %+v, want the legend's colour and stand-in name", style)
	}

	// With the identity plugin deployed the same row carries the name the game reported.
	identities := filepath.Join(server.cfg.MapSourceRoot, world, "config_merged")
	if err := os.MkdirAll(identities, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(identities, "player_identities.json"),
		[]byte(`{"schema":1,"players":[{"player_id":-322254472,"name":"westar"}]}`), 0o644); err != nil {
		t.Fatal(err)
	}
	named := server.pinLegend(t.Context(), world, server.reportedPins(world), map[string]map[string]string{})
	if len(named) != 1 || named[0].Label != "westar" || !named[0].Named || !named[0].Reported {
		t.Fatalf("named row = %+v, want westar reported by the server", named)
	}
}
