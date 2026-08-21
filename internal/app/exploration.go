package app

import (
	"bufio"
	"compress/gzip"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
)

// Receives what each player has actually uncovered on their own map, and the pins they placed.
//
// The portal's own fog is inferred from generated zones - ground the server loaded because somebody
// went near it. That is honest but coarse (64 m) and shared: it cannot tell one player's map from
// another's, and it credits a player for ground they flew past in a boat at night. The real thing lives
// in each player's minimap and is reported by tools/exploration-reporter.
//
// Uploads are authorised by the profile-scoped device token the launcher already holds, so nothing new
// is asked of a player. The Steam id comes from that token rather than the payload: a client may say
// which character it is, but not whose account.

const (
	maxExplorationUploadBytes = int64(8 << 20)
	// The grid the portal republishes to the map. The game's own grid is about 10 m per cell over a
	// 2048-cell square; 32 m is four times finer than the zone fog it replaces while staying small
	// enough to hand to a browser on every page load.
	explorationCellSize = 32
	explorationRadius   = 10496 // a whole number of 32 m cells, just past the playable edge
)

type explorationUpload struct {
	World       string
	SteamID     string
	PlayerID    int64
	PlayerName  string
	TextureSize int
	PixelSize   float64
	Cells       int
	Written     string
	Explored    []byte
}

// explorationMask is the union of every player's revealed map, packed for the browser: one bit per
// 32 m cell, row-major from the south-west corner.
type explorationMask struct {
	CellSize int    `json:"cell_size"`
	Size     int    `json:"size"`
	OriginX  int    `json:"origin_x"`
	OriginZ  int    `json:"origin_z"`
	Bits     string `json:"bits"`
	Players  int    `json:"players"`
}

// explorationRoot is where reports live: the portal's own artifact root, not the world root. The world
// root is mounted read-only because it belongs to the game containers, so writing there returned 500 to
// the first real upload a player's client attempted.
func (s *Server) explorationRoot(world string) string {
	return filepath.Join(s.cfg.ArtifactRoot, "exploration", world)
}

func (s *Server) clientExploration(w http.ResponseWriter, r *http.Request) {
	world, profile, clientType := r.PathValue("world"), r.PathValue("profile"), r.PathValue("clientType")
	claims, ok, err := s.validDeviceToken(r.Context(), r, world, profile, clientType)
	if err != nil {
		http.Error(w, "unavailable", http.StatusServiceUnavailable)
		return
	}
	// Two scopes may report a map: the profile scope the launcher already holds, and the narrow
	// exploration scope handed to the game so the reporter can send a session the moment it ends.
	// Diagnostics tokens cannot - that flow is operator-initiated and has its own scope.
	if !ok || (claims.Scope != deviceTokenScopeProfile && claims.Scope != deviceTokenScopeExploration) {
		http.Error(w, "client authorization required", http.StatusUnauthorized)
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, maxExplorationUploadBytes)
	defer r.Body.Close()
	if err := r.ParseMultipartForm(maxExplorationUploadBytes); err != nil {
		http.Error(w, "invalid exploration upload", http.StatusBadRequest)
		return
	}
	directory := s.explorationRoot(world)
	if err := os.MkdirAll(directory, 0o750); err != nil {
		http.Error(w, "storage failure", http.StatusInternalServerError)
		return
	}
	stored := 0
	for _, field := range []string{"explored", "pins"} {
		file, header, err := r.FormFile(field)
		if err != nil {
			continue
		}
		playerID, parseErr := playerIDFromReportName(header.Filename)
		if parseErr != nil {
			file.Close()
			http.Error(w, "exploration upload names no player", http.StatusBadRequest)
			return
		}
		suffix := ".explored"
		if field == "pins" {
			suffix = ".pins.json"
		}
		// Keyed by the account the token proves plus the character the file names, so one player's
		// report can never overwrite another's and a second character keeps its own map.
		name := claims.SteamID + "-" + strconv.FormatInt(playerID, 10) + suffix
		path := filepath.Join(directory, name)
		if err := writeUploadedFile(path, file); err != nil {
			file.Close()
			http.Error(w, "storage failure", http.StatusInternalServerError)
			return
		}
		file.Close()
		stored++
	}
	if stored == 0 {
		http.Error(w, "nothing to store", http.StatusBadRequest)
		return
	}
	_ = s.store.Audit(r.Context(), claims.SteamID, "world.exploration.reported", world, strconv.Itoa(stored)+" file(s)")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(map[string]int{"stored": stored})
}

// playerIDFromReportName reads the character id out of the name the plugin wrote:
// "<world>-<playerid>.explored" or "<world>-<playerid>.pins.json". The id decides which file this
// replaces, so a name that does not carry one is refused rather than guessed at.
func playerIDFromReportName(name string) (int64, error) {
	base := filepath.Base(name)
	base = strings.TrimSuffix(base, ".explored")
	base = strings.TrimSuffix(base, ".pins.json")
	// Player ids are signed, and a negative one puts two dashes in the name: "Hrafnheim--322254472".
	// Splitting on the last dash silently dropped the sign and filed that player's map under a
	// different, positive id - a map attributed to somebody who does not exist.
	end := len(base)
	cut := end - 1
	for cut >= 0 && base[cut] >= '0' && base[cut] <= '9' {
		cut--
	}
	digits := base[cut+1:]
	if digits == "" || cut < 0 {
		return 0, errors.New("no player id in report name")
	}
	sign := ""
	if base[cut] == '-' && cut-1 >= 0 && base[cut-1] == '-' {
		sign = "-"
		cut--
	}
	// Whatever is left must end at the separator, or this is not "<world>-<id>" at all.
	if base[cut] != '-' {
		return 0, errors.New("no player id in report name")
	}
	id, err := strconv.ParseInt(sign+digits, 10, 64)
	if err != nil || id == 0 {
		return 0, errors.New("invalid player id in report name")
	}
	return id, nil
}

func writeUploadedFile(path string, source io.Reader) error {
	temporary := path + ".tmp"
	out, err := os.OpenFile(temporary, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o640)
	if err != nil {
		return err
	}
	if _, err := io.Copy(out, io.LimitReader(source, maxExplorationUploadBytes)); err != nil {
		out.Close()
		os.Remove(temporary)
		return err
	}
	if err := out.Close(); err != nil {
		os.Remove(temporary)
		return err
	}
	return os.Rename(temporary, path)
}

// explorationUnion reads every report for a world and returns one mask. Nothing is stored derived: a
// world with no reports returns nil and the map falls back to the zone fog, which is what every world
// gets until a player runs the reporter.
// explorationReporter is one character that has reported a map, for the map page's selector.
type explorationReporter struct {
	PlayerID int64   `json:"player_id"`
	Name     string  `json:"name"`
	SquareKm float64 `json:"square_km"`
	Written  string  `json:"written"`
	Selected bool    `json:"selected,omitempty"`
}

// explorationReporters lists the characters that have reported, newest report first. The name comes out
// of the report the game wrote, so the selector shows "westar" rather than a signed integer.
func (s *Server) explorationReporters(world string) []explorationReporter {
	if !validWorld(world) {
		return nil
	}
	directory := s.explorationRoot(world)
	entries, err := os.ReadDir(directory)
	if err != nil {
		return nil
	}
	reported := s.reportedPlayerNames(world)
	var reporters []explorationReporter
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".explored") {
			continue
		}
		report, err := readExplorationReport(filepath.Join(directory, entry.Name()))
		if err != nil || report.PlayerID == 0 {
			continue
		}
		// The reporter sanitises the name into a filename-safe form, so "Ai Test" arrives as "Ai_Test".
		// The server plugin recorded the real one when that character connected, so prefer it and keep
		// the report's version as the fallback for a character the server has not seen.
		name := reported[report.PlayerID]
		if name == "" {
			name = report.PlayerName
		}
		if name == "" {
			name = "character " + strconv.FormatInt(report.PlayerID, 10)
		}
		reporters = append(reporters, explorationReporter{
			PlayerID: report.PlayerID,
			Name:     name,
			SquareKm: float64(report.Cells) * report.PixelSize * report.PixelSize / 1_000_000,
			Written:  report.Written,
		})
	}
	sort.Slice(reporters, func(i, j int) bool {
		if reporters[i].SquareKm != reporters[j].SquareKm {
			return reporters[i].SquareKm > reporters[j].SquareKm
		}
		return reporters[i].PlayerID < reporters[j].PlayerID
	})
	return reporters
}

// explorationBitsFor is one character's own map rather than everybody's. Selecting a character is how an
// operator sees the world as that character knows it, which is the whole point: an admin character's
// discoveries must not leak into what a non-admin character has found.
func (s *Server) explorationBitsFor(world string, playerID int64) (bits []byte, size int, found bool) {
	if !validWorld(world) || playerID == 0 {
		return nil, 0, false
	}
	directory := s.explorationRoot(world)
	entries, err := os.ReadDir(directory)
	if err != nil {
		return nil, 0, false
	}
	size = explorationRadius * 2 / explorationCellSize
	bits = make([]byte, (size*size+7)/8)
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".explored") {
			continue
		}
		report, err := readExplorationReport(filepath.Join(directory, entry.Name()))
		if err != nil || report.PlayerID != playerID {
			continue
		}
		if addReportToMask(report, bits, size) {
			found = true
		}
	}
	if !found {
		return nil, 0, false
	}
	return bits, size, true
}

// explorationUnionBits is the union as raw bits, for deciding what a player may see. The JSON form
// below wraps the same result: if these two ever came from different code, the map would hide ground
// while still drawing the markers standing on it - which is exactly the bug that made it necessary.
func (s *Server) explorationUnionBits(world string) (bits []byte, size, players int) {
	if !validWorld(world) {
		return nil, 0, 0
	}
	entries, err := os.ReadDir(s.explorationRoot(world))
	if err != nil {
		return nil, 0, 0
	}
	directory := s.explorationRoot(world)
	size = explorationRadius * 2 / explorationCellSize
	bits = make([]byte, (size*size+7)/8)
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".explored") {
			continue
		}
		report, err := readExplorationReport(filepath.Join(directory, entry.Name()))
		if err != nil {
			continue
		}
		if addReportToMask(report, bits, size) {
			players++
		}
	}
	if players == 0 {
		return nil, 0, 0
	}
	return bits, size, players
}

func (s *Server) explorationUnion(world string) *explorationMask {
	bits, size, players := s.explorationUnionBits(world)
	if players == 0 {
		return nil
	}
	return &explorationMask{
		CellSize: explorationCellSize,
		Size:     size,
		OriginX:  -explorationRadius,
		OriginZ:  -explorationRadius,
		Bits:     base64.StdEncoding.EncodeToString(bits),
		Players:  players,
	}
}

// addReportToMask folds one player's grid into the shared one. The game indexes its grid
// [y * textureSize + x] with x = round(worldX / pixelSize) + textureSize/2, so the inverse is applied
// per set cell rather than trusting any coordinates in the file.
func addReportToMask(report *explorationUpload, bits []byte, size int) bool {
	if report.TextureSize <= 0 || report.PixelSize <= 0 {
		return false
	}
	half := report.TextureSize / 2
	added := false
	for index := range report.TextureSize * report.TextureSize {
		if index>>3 >= len(report.Explored) {
			break
		}
		if report.Explored[index>>3]&(1<<(index&7)) == 0 {
			continue
		}
		x := index % report.TextureSize
		y := index / report.TextureSize
		worldX := float64(x-half) * report.PixelSize
		worldZ := float64(y-half) * report.PixelSize
		cellX := int((worldX + float64(explorationRadius)) / float64(explorationCellSize))
		cellZ := int((worldZ + float64(explorationRadius)) / float64(explorationCellSize))
		if cellX < 0 || cellX >= size || cellZ < 0 || cellZ >= size {
			continue
		}
		target := cellZ*size + cellX
		bits[target>>3] |= 1 << (target & 7)
		added = true
	}
	return added
}

// readExplorationReport parses what the plugin wrote: one line of readable header, then the player's
// grid as a gzipped bitset. The header is parsed strictly - a field this cannot read is a report from a
// version this portal does not understand, and guessing at its geometry would put fog in the wrong
// places rather than fail visibly.
func readExplorationReport(path string) (*explorationUpload, error) {
	file, err := os.Open(filepath.Clean(path))
	if err != nil {
		return nil, err
	}
	defer file.Close()
	reader := bufio.NewReader(io.LimitReader(file, maxExplorationUploadBytes))
	header, err := reader.ReadString('\n')
	if err != nil {
		return nil, err
	}
	if !strings.HasPrefix(header, "neuralyze-exploration ") {
		return nil, errors.New("not an exploration report")
	}
	report := &explorationUpload{}
	for _, field := range strings.Fields(strings.TrimSpace(header)) {
		name, value, found := strings.Cut(field, "=")
		if !found {
			continue
		}
		switch name {
		case "world":
			report.World = value
		case "player_id":
			report.PlayerID, _ = strconv.ParseInt(value, 10, 64)
		case "player_name":
			report.PlayerName = value
		case "texture_size":
			report.TextureSize, _ = strconv.Atoi(value)
		case "pixel_size":
			report.PixelSize, _ = strconv.ParseFloat(value, 64)
		case "cells_uncovered":
			report.Cells, _ = strconv.Atoi(value)
		case "written":
			report.Written = value
		}
	}
	if report.TextureSize <= 0 || report.TextureSize > 8192 || report.PixelSize <= 0 || report.PixelSize > 1000 {
		return nil, errors.New("exploration report declares an implausible grid")
	}
	decompressed, err := gzip.NewReader(reader)
	if err != nil {
		return nil, err
	}
	defer decompressed.Close()
	expected := (report.TextureSize*report.TextureSize + 7) / 8
	bits, err := io.ReadAll(io.LimitReader(decompressed, int64(expected)+1))
	if err != nil {
		return nil, err
	}
	if len(bits) < expected {
		return nil, errors.New("exploration report is shorter than the grid it declares")
	}
	report.Explored = bits
	return report, nil
}

// explorationPins is every saved pin every player reported, with whose it is. Type is Valheim's own
// Minimap.PinType ordinal (assembly_valheim.dll: Icon0=0, Icon1=1, Icon2=2, Icon3=3, Death=4, Bed=5,
// Icon4=6, Shout=7, None=8, Boss=9, Player=10, RandomEvent=11, Ping=12, EventArea=13, Hildir1..3=
// 14..16) and it is carried through as the number so the map can draw a bed as a bed. TypeName is
// what the client wrote alongside it, which for the vanilla marker icons is "icon0".."icon4".
type explorationPins struct {
	PlayerID int64   `json:"player_id"`
	Name     string  `json:"name"`
	Type     int     `json:"type"`
	TypeName string  `json:"type_name"`
	X        float64 `json:"x"`
	Z        float64 `json:"z"`
	Crossed  bool    `json:"crossed_off"`
}

// reportedPins reads the pin files for a world. A player's pins are the part of the map they made
// themselves, so they are shown as they were written - names included, unchanged.
func (s *Server) reportedPins(world string) []explorationPins {
	if !validWorld(world) {
		return nil
	}
	directory := s.explorationRoot(world)
	entries, err := os.ReadDir(directory)
	if err != nil {
		return nil
	}
	var all []explorationPins
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".pins.json") {
			continue
		}
		raw, err := os.ReadFile(filepath.Join(directory, entry.Name()))
		if err != nil {
			continue
		}
		var file struct {
			PlayerID int64             `json:"player_id"`
			Pins     []explorationPins `json:"pins"`
		}
		if err := json.Unmarshal(raw, &file); err != nil {
			continue
		}
		for _, pin := range file.Pins {
			pin.PlayerID = file.PlayerID
			all = append(all, pin)
		}
	}
	return all
}
