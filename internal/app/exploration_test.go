package app

import (
	"bytes"
	"compress/gzip"
	"context"
	"encoding/base64"
	"fmt"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"
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
	directory := filepath.Join(server.cfg.MapSourceRoot, world, "exploration")
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
	directory := filepath.Join(server.cfg.MapSourceRoot, world, "exploration")
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
	directory := filepath.Join(server.cfg.MapSourceRoot, world, "exploration")
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

	stored := filepath.Join(server.cfg.MapSourceRoot, release.World, "exploration", testSteamID+"-111.explored")
	if _, err := os.Stat(stored); err != nil {
		t.Fatalf("report not stored under the account and character: %v", err)
	}
	// And it is readable as what it claims to be, which is what the map will do with it.
	if mask := server.explorationUnion(release.World); mask == nil || mask.Players != 1 {
		t.Errorf("mask = %+v, want one player's map", mask)
	}
}
