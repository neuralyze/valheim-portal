package worldintel

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"math"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
)

const (
	SchemaVersion                = 1
	MinWorldVersion              = 31
	MaxWorldVersion              = 37
	maxObjects                   = 2_000_000
	maxPropertyItems             = 32_767
	maxBlob                      = 64 << 20
	maxArchiveMember             = 512 << 20
	constructionCoverageBaseCell = 32
	constructionClusterCell      = 128
	maxConstructionCoverageCells = 2_048
	maxConstructionClusters      = 1_024
)

type Vec2 struct {
	X int `json:"x"`
	Y int `json:"y"`
}
type Vec3 struct {
	X float32 `json:"x"`
	Y float32 `json:"y"`
	Z float32 `json:"z"`
}
type Property struct {
	Hash  int32  `json:"hash"`
	Name  string `json:"name,omitempty"`
	Value any    `json:"value"`
}
type InventoryItem struct {
	Name       string `json:"name"`
	Stack      int32  `json:"stack"`
	Quality    int32  `json:"quality,omitempty"`
	WorldLevel int32  `json:"world_level,omitempty"`
	PickedUp   bool   `json:"picked_up,omitempty"`
}
type Inventory struct {
	Version int32           `json:"version"`
	Items   []InventoryItem `json:"items"`
}
type Object struct {
	ID             uint32 `json:"id"`
	PrefabHash     int32  `json:"prefab_hash"`
	Prefab         string `json:"prefab,omitempty"`
	Category       string `json:"category"`
	Position       Vec3   `json:"position"`
	Persistent     bool   `json:"persistent"`
	Distant        bool   `json:"distant"`
	Type           uint8  `json:"type"`
	ConnectionType uint8  `json:"connection_type,omitempty"`
	// Creator is the player id Valheim stamps on a piece somebody built. Generated locations -
	// Meadows ruins, crypts, villages - carry the same field with the value 0, so presence alone
	// classified every ruin on the map as player construction and put "our builds" in places
	// nobody had visited. The value is what separates them, so the value is kept.
	Creator          int64      `json:"creator,omitempty"`
	ConnectionHash   int32      `json:"connection_hash,omitempty"`
	Inventory        *Inventory `json:"inventory,omitempty"`
	InventoryWarning string     `json:"inventory_warning,omitempty"`
	Properties       []Property `json:"properties,omitempty"`
}
type Location struct {
	Name      string `json:"name"`
	Category  string `json:"category"`
	Position  Vec3   `json:"position"`
	Generated bool   `json:"generated"`
}

// Valheim generates a zone only when somebody has been near it, so the generated-zone list is the
// server's own record of where players have gone - the closest thing to map discovery that exists
// server-side, since a player's revealed map lives in their own character file.
//
// zoneSize is ZoneSystem.m_zoneSize; playableRadius is where the world ends for play. Sentinel zones
// sit at 1,000,000 metres, which is where the game parks global objects: real entries, but nowhere
// anybody has walked, so they are counted separately rather than smuggled into an explored area.
const (
	zoneSize          = 64.0
	playableRadius    = 10000.0
	maxZoneIndex      = 164
	sentinelZoneIndex = 15625
)

type Cluster struct {
	ID     int     `json:"id"`
	Center Vec3    `json:"center"`
	Radius float32 `json:"radius"`
	Pieces int     `json:"pieces"`
	// Creator is the player id Valheim stamped on the pieces. Clustering used to discard it, so a
	// map could show that somebody had built something and never which somebody - which is how an
	// operator ends up asking whether a stranger has been on the server.
	Creator int64 `json:"creator,omitempty"`
}
type CoverageCell struct {
	X      int `json:"x"`
	Z      int `json:"z"`
	Pieces int `json:"pieces"`
	// Creator is whoever placed the most pieces in this cell, and Builders is how many people placed
	// anything in it. A cell drawn in one builder's colour is the majority, not a claim of sole
	// ownership - Builders is what stops that reading as a lie.
	Creator  int64 `json:"creator,omitempty"`
	Builders int   `json:"builders,omitempty"`
}
type ConstructionCoverage struct {
	CellSize    int            `json:"cell_size"`
	TotalPieces int            `json:"total_pieces"`
	MaxPieces   int            `json:"max_pieces"`
	Cells       []CoverageCell `json:"cells"`
}
type Source struct {
	Backup     string    `json:"backup"`
	SHA256     string    `json:"sha256"`
	DBBytes    int64     `json:"db_bytes"`
	FWLBytes   int64     `json:"fwl_bytes"`
	ModifiedAt time.Time `json:"modified_at"`
}
type Health struct {
	Level              string   `json:"level"`
	Findings           []string `json:"findings"`
	UnknownPrefabs     int      `json:"unknown_prefabs"`
	InvalidCoordinates int      `json:"invalid_coordinates"`
}
type Summary struct {
	Objects        int `json:"objects"`
	Persistent     int `json:"persistent"`
	GeneratedZones int `json:"generated_zones"`
	// ExploredZones counts only zones inside the playable grid; SentinelZones counts the far-away
	// bookkeeping zones. ExploredSquareKm and ExploredPercent are what an operator actually asked
	// for: how much of the map has been visited.
	ExploredZones      int            `json:"explored_zones"`
	SentinelZones      int            `json:"sentinel_zones"`
	ExploredSquareKm   float64        `json:"explored_square_km"`
	ExploredPercent    float64        `json:"explored_percent"`
	Locations          int            `json:"locations"`
	InventoryObjects   int            `json:"inventory_objects"`
	InventoryStacks    int            `json:"inventory_stacks"`
	InventoryItems     int64          `json:"inventory_items"`
	TamedCreatures     int            `json:"tamed_creatures"`
	NamedCreatures     int            `json:"named_creatures"`
	Categories         map[string]int `json:"categories"`
	LocationCategories map[string]int `json:"location_categories"`
	Bounds             [4]float32     `json:"bounds"`
}
type Snapshot struct {
	Schema               int                   `json:"schema"`
	World                string                `json:"world"`
	Seed                 string                `json:"seed,omitempty"`
	WorldVersion         int32                 `json:"world_version"`
	NetTime              float64               `json:"net_time"`
	WorldAgeDays         float64               `json:"world_age_days"`
	PGWVersion           int32                 `json:"pgw_version"`
	LocationVersion      int32                 `json:"location_version"`
	LocationsGenerated   bool                  `json:"locations_generated"`
	GlobalKeys           []string              `json:"global_keys"`
	Source               Source                `json:"source"`
	Summary              Summary               `json:"summary"`
	Health               Health                `json:"health"`
	GeneratedZones       []Vec2                `json:"generated_zones"`
	Locations            []Location            `json:"locations"`
	Clusters             []Cluster             `json:"clusters"`
	ConstructionCoverage *ConstructionCoverage `json:"construction_coverage,omitempty"`
	Objects              []Object              `json:"objects"`
}
type Diff struct {
	Older         string         `json:"older"`
	Newer         string         `json:"newer"`
	ObjectDelta   int            `json:"object_delta"`
	ZoneDelta     int            `json:"zone_delta"`
	CategoryDelta map[string]int `json:"category_delta"`
	NewLocations  []Location     `json:"new_locations,omitempty"`
	Findings      []string       `json:"findings"`
}
type Report struct {
	Snapshot        Snapshot  `json:"snapshot"`
	Previous        *Snapshot `json:"previous,omitempty"`
	Diff            *Diff     `json:"diff,omitempty"`
	Recommendations []string  `json:"recommendations"`
}

type blob struct {
	size int
	data []byte
}
type valueMaps struct {
	f map[int32]float32
	v map[int32]Vec3
	i map[int32]int32
	l map[int32]int64
	s map[int32]string
	b map[int32]blob
}
type reader struct {
	r io.Reader
	n int64
}

func (r *reader) read(p []byte) error { n, err := io.ReadFull(r.r, p); r.n += int64(n); return err }
func (r *reader) u8() (uint8, error)  { var b [1]byte; e := r.read(b[:]); return b[0], e }
func (r *reader) u16() (uint16, error) {
	var b [2]byte
	e := r.read(b[:])
	return binary.LittleEndian.Uint16(b[:]), e
}
func (r *reader) i16() (int16, error) { v, e := r.u16(); return int16(v), e }
func (r *reader) u32() (uint32, error) {
	var b [4]byte
	e := r.read(b[:])
	return binary.LittleEndian.Uint32(b[:]), e
}
func (r *reader) i32() (int32, error) { v, e := r.u32(); return int32(v), e }
func (r *reader) i64() (int64, error) {
	var b [8]byte
	e := r.read(b[:])
	return int64(binary.LittleEndian.Uint64(b[:])), e
}
func (r *reader) f32() (float32, error) { v, e := r.u32(); return math.Float32frombits(v), e }
func (r *reader) f64() (float64, error) {
	var b [8]byte
	e := r.read(b[:])
	return math.Float64frombits(binary.LittleEndian.Uint64(b[:])), e
}
func (r *reader) vec3() (Vec3, error) {
	x, e := r.f32()
	if e != nil {
		return Vec3{}, e
	}
	y, e := r.f32()
	if e != nil {
		return Vec3{}, e
	}
	z, e := r.f32()
	return Vec3{x, y, z}, e
}
func (r *reader) skip(n int64) error {
	if n < 0 || n > maxBlob {
		return errors.New("invalid length")
	}
	_, e := io.CopyN(io.Discard, r.r, n)
	if e == nil {
		r.n += n
	}
	return e
}
func (r *reader) str() (string, error) {
	var n uint64
	for shift := uint(0); shift < 35; shift += 7 {
		b, e := r.u8()
		if e != nil {
			return "", e
		}
		n |= uint64(b&127) << shift
		if b&128 == 0 {
			if n > 1<<20 {
				return "", errors.New("string too large")
			}
			p := make([]byte, n)
			e = r.read(p)
			return string(p), e
		}
	}
	return "", errors.New("invalid string length")
}
func (r *reader) num() (int, error) {
	a, e := r.u8()
	if e != nil {
		return 0, e
	}
	if a&128 == 0 {
		return int(a), nil
	}
	b, e := r.u8()
	return int(a&127)<<8 | int(b), e
}

func StableHash(s string) int32 {
	var h1 int32 = 5381
	var h2 int32 = h1
	for i := 0; i < len(s); i += 2 {
		h1 = ((h1 << 5) + h1) ^ int32(s[i])
		if i+1 < len(s) {
			h2 = ((h2 << 5) + h2) ^ int32(s[i+1])
		}
	}
	return h1 + h2*1566083941
}

var tokenRE = regexp.MustCompile(`[A-Za-z][A-Za-z0-9_$.:+-]{2,119}`)

func CatalogFromFiles(paths ...string) map[int32]string {
	out := knownCatalog()
	const maxCatalogBytes int64 = 1 << 30
	var scanned int64
	var files int
	add := func(path string, info fs.FileInfo) {
		if files >= 20_000 || scanned >= maxCatalogBytes || info.Size() <= 0 || info.Size() > 256<<20 {
			return
		}
		switch strings.ToLower(filepath.Ext(path)) {
		case ".dll", ".assets", ".json", ".cfg", ".yml", ".yaml", ".txt":
		default:
			return
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return
		}
		files++
		scanned += int64(len(data))
		for _, raw := range tokenRE.FindAll(data, -1) {
			s := string(raw)
			if strings.Contains(s, "::") || strings.Contains(s, "System.") {
				continue
			}
			h := StableHash(s)
			if _, ok := out[h]; !ok {
				out[h] = s
			}
		}
	}
	for _, path := range paths {
		info, err := os.Stat(path)
		if err != nil {
			continue
		}
		if !info.IsDir() {
			add(path, info)
			continue
		}
		_ = filepath.WalkDir(path, func(candidate string, entry fs.DirEntry, walkErr error) error {
			if walkErr != nil || files >= 20_000 || scanned >= maxCatalogBytes {
				return nil
			}
			if entry.IsDir() {
				return nil
			}
			info, err := entry.Info()
			if err == nil {
				add(candidate, info)
			}
			return nil
		})
	}
	return out
}
func knownCatalog() map[int32]string {
	names := []string{"portal_wood", "portal", "piece_portal", "piece_portal_stone", "piece_workbench", "piece_stonecutter", "piece_artisanstation", "forge", "smelter", "blastfurnace", "charcoal_kiln", "windmill", "spinningwheel", "fermenter", "piece_cookingstation", "piece_oven", "piece_chest_wood", "piece_chest", "piece_chest_blackmetal", "TreasureChest_meadows", "Player", "Boar", "Wolf", "Lox", "Hen", "Chicken", "Asksvin", "TerrainModifier", "Pickable", "Beehive", "sign", "bed", "piece_bed02", "creator", "items", "tag", "tamed", "TamedName", "fuel", "ore", "queued", "done", "level", "health", "spawn_time", "lastWorldTime", "alive_time", "lovePoints", "pregnant", "procreation", "content", "StartTime", "SpawnPoint"}
	m := make(map[int32]string, len(names))
	for _, n := range names {
		m[StableHash(n)] = n
	}
	return m
}

func AnalyzeArchive(path, world string, catalog map[int32]string) (Snapshot, error) {
	f, err := os.Open(path)
	if err != nil {
		return Snapshot{}, err
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil {
		return Snapshot{}, err
	}
	h := sha256.New()
	gz, err := gzip.NewReader(io.TeeReader(io.LimitReader(f, maxArchiveMember), h))
	if err != nil {
		return Snapshot{}, fmt.Errorf("open backup: %w", err)
	}
	defer gz.Close()
	tr := tar.NewReader(gz)
	var db []byte
	var fwl []byte
	for {
		hdr, e := tr.Next()
		if e == io.EOF {
			break
		}
		if e != nil {
			return Snapshot{}, fmt.Errorf("read backup: %w", e)
		}
		base := strings.ToLower(filepath.Base(hdr.Name))
		if hdr.Typeflag != tar.TypeReg || hdr.Size < 0 || hdr.Size > maxArchiveMember {
			continue
		}
		switch {
		case strings.HasSuffix(base, ".db"):
			db, err = io.ReadAll(io.LimitReader(tr, maxArchiveMember+1))
		case strings.HasSuffix(base, ".fwl"):
			fwl, err = io.ReadAll(io.LimitReader(tr, 1<<20))
		default:
			continue
		}
		if err != nil {
			return Snapshot{}, err
		}
	}
	if len(db) == 0 || len(fwl) == 0 {
		return Snapshot{}, errors.New("backup must contain one db/fwl pair")
	}
	if len(db) > maxArchiveMember {
		return Snapshot{}, errors.New("database too large")
	}
	s, err := ParseDB(bytes.NewReader(db), catalog)
	if err != nil {
		return Snapshot{}, err
	}
	s.Schema = SchemaVersion
	s.World = world
	s.Seed = parseFWLSeed(fwl)
	s.Source = Source{Backup: filepath.Base(path), SHA256: hex.EncodeToString(h.Sum(nil)), DBBytes: int64(len(db)), FWLBytes: int64(len(fwl)), ModifiedAt: info.ModTime().UTC()}
	s.WorldAgeDays = s.NetTime / 86400
	finalize(&s)
	return s, nil
}

func ParseDB(src io.Reader, catalog map[int32]string) (Snapshot, error) {
	r := &reader{r: src}
	version, e := r.i32()
	if e != nil {
		return Snapshot{}, e
	}
	if version < MinWorldVersion || version > MaxWorldVersion {
		return Snapshot{}, fmt.Errorf("unsupported world version %d (supported %d-%d)", version, MinWorldVersion, MaxWorldVersion)
	}
	net, e := r.f64()
	if e != nil {
		return Snapshot{}, e
	}
	if _, e = r.i64(); e != nil {
		return Snapshot{}, e
	}
	if _, e = r.u32(); e != nil {
		return Snapshot{}, e
	}
	count, e := r.i32()
	if e != nil || count < 0 || count > maxObjects {
		return Snapshot{}, errors.New("invalid ZDO count")
	}
	s := Snapshot{WorldVersion: version, NetTime: net, Summary: Summary{Objects: int(count), Categories: map[string]int{}}, Health: Health{Level: "healthy"}}
	for id := 0; id < int(count); id++ {
		o, vals, e := readObject(r, version, catalog, uint32(id+1))
		if e != nil {
			return Snapshot{}, fmt.Errorf("ZDO %d at byte %d: %w", id, r.n, e)
		}
		semanticCategory(&o, vals)
		if encoded, ok := vals.s[StableHash("items")]; ok && encoded != "" {
			raw, err := base64.StdEncoding.DecodeString(encoded)
			if err != nil {
				o.InventoryWarning = "invalid inventory base64"
			} else if inventory, err := parseInventory(raw); err != nil {
				o.InventoryWarning = err.Error()
			} else {
				o.Inventory = inventory
			}
		} else if raw, ok := vals.b[StableHash("items")]; ok && len(raw.data) > 0 {
			inventory, err := parseInventory(raw.data)
			if err != nil {
				o.InventoryWarning = err.Error()
			} else {
				o.Inventory = inventory
			}
		}
		if o.Inventory != nil {
			s.Summary.InventoryObjects++
			s.Summary.InventoryStacks += len(o.Inventory.Items)
			for _, item := range o.Inventory.Items {
				s.Summary.InventoryItems += int64(item.Stack)
			}
		}
		if vals.i[StableHash("tamed")] != 0 {
			s.Summary.TamedCreatures++
		}
		if vals.s[StableHash("TamedName")] != "" {
			s.Summary.NamedCreatures++
		}
		if o.Persistent {
			s.Summary.Persistent++
		}
		s.Summary.Categories[o.Category]++
		if o.PrefabHash != 0 && o.Prefab == "" {
			s.Health.UnknownPrefabs++
		}
		if !validPos(o.Position) {
			s.Health.InvalidCoordinates++
		}
		if retain(o, vals) {
			o.Properties = properties(vals, catalog)
			s.Objects = append(s.Objects, o)
		}
	}
	zones, e := r.i32()
	if e != nil || zones < 0 || zones > 2_000_000 {
		return Snapshot{}, errors.New("invalid generated zone count")
	}
	s.GeneratedZones = make([]Vec2, 0, zones)
	for i := int32(0); i < zones; i++ {
		x, e := r.i32()
		if e != nil {
			return Snapshot{}, e
		}
		y, e := r.i32()
		if e != nil {
			return Snapshot{}, e
		}
		s.GeneratedZones = append(s.GeneratedZones, Vec2{int(x), int(y)})
	}
	s.PGWVersion, e = r.i32()
	if e != nil {
		return Snapshot{}, e
	}
	s.LocationVersion, e = r.i32()
	if e != nil {
		return Snapshot{}, e
	}
	keys, e := r.i32()
	if e != nil || keys < 0 || keys > 10000 {
		return Snapshot{}, errors.New("invalid global key count")
	}
	for i := int32(0); i < keys; i++ {
		v, e := r.str()
		if e != nil {
			return Snapshot{}, e
		}
		s.GlobalKeys = append(s.GlobalKeys, v)
	}
	b, e := r.u8()
	if e != nil {
		return Snapshot{}, e
	}
	s.LocationsGenerated = b != 0
	lc, e := r.i32()
	if e != nil || lc < 0 || lc > 100000 {
		return Snapshot{}, errors.New("invalid location count")
	}
	for i := int32(0); i < lc; i++ {
		n, e := r.str()
		if e != nil {
			return Snapshot{}, e
		}
		p, e := r.vec3()
		if e != nil {
			return Snapshot{}, e
		}
		g, e := r.u8()
		if e != nil {
			return Snapshot{}, e
		}
		s.Locations = append(s.Locations, Location{Name: n, Category: LocationCategory(n), Position: p, Generated: g != 0})
	}
	return s, nil
}

func readObject(r *reader, version int32, catalog map[int32]string, id uint32) (Object, valueMaps, error) {
	flags, e := r.u16()
	if e != nil {
		return Object{}, valueMaps{}, e
	}
	if _, e = r.i16(); e != nil {
		return Object{}, valueMaps{}, e
	}
	if _, e = r.i16(); e != nil {
		return Object{}, valueMaps{}, e
	}
	p, e := r.vec3()
	if e != nil {
		return Object{}, valueMaps{}, e
	}
	ph, e := r.i32()
	if e != nil {
		return Object{}, valueMaps{}, e
	}
	o := Object{ID: id, PrefabHash: ph, Prefab: catalog[ph], Position: p, Persistent: flags&256 != 0, Distant: flags&512 != 0, Type: uint8(flags >> 10 & 3)}
	o.Category = category(o.Prefab)
	if flags&4096 != 0 {
		if e = r.skip(12); e != nil {
			return o, valueMaps{}, e
		}
	}
	v := valueMaps{}
	if flags&255 == 0 {
		return o, v, nil
	}
	if flags&1 != 0 {
		o.ConnectionType, e = r.u8()
		if e != nil {
			return o, v, e
		}
		o.ConnectionHash, e = r.i32()
		if e != nil {
			return o, v, e
		}
	}
	if flags&2 != 0 {
		v.f, e = readMapF(r, version)
		if e != nil {
			return o, v, e
		}
	}
	if flags&4 != 0 {
		v.v, e = readMapV(r, version)
		if e != nil {
			return o, v, e
		}
	}
	if flags&8 != 0 {
		e = skipMap(r, version, 16)
		if e != nil {
			return o, v, e
		}
	}
	if flags&16 != 0 {
		v.i, e = readMapI(r, version)
		if e != nil {
			return o, v, e
		}
	}
	if flags&32 != 0 {
		v.l, e = readMapL(r, version)
		if e != nil {
			return o, v, e
		}
	}
	if flags&64 != 0 {
		v.s, e = readMapS(r, version)
		if e != nil {
			return o, v, e
		}
	}
	if flags&128 != 0 {
		v.b, e = readMapB(r, version)
		if e != nil {
			return o, v, e
		}
	}
	return o, v, nil
}
func count(r *reader, version int32) (int, error) {
	n, e := r.num()
	if e != nil {
		return 0, e
	}
	if n < 0 || n > maxPropertyItems {
		return 0, errors.New("invalid property count")
	}
	return n, nil
}
func readMapF(r *reader, v int32) (map[int32]float32, error) {
	n, e := count(r, v)
	m := make(map[int32]float32, n)
	for i := 0; i < n && e == nil; i++ {
		var k int32
		k, e = r.i32()
		if e == nil {
			m[k], e = r.f32()
		}
	}
	return m, e
}
func readMapV(r *reader, v int32) (map[int32]Vec3, error) {
	n, e := count(r, v)
	m := make(map[int32]Vec3, n)
	for i := 0; i < n && e == nil; i++ {
		var k int32
		k, e = r.i32()
		if e == nil {
			m[k], e = r.vec3()
		}
	}
	return m, e
}
func readMapI(r *reader, v int32) (map[int32]int32, error) {
	n, e := count(r, v)
	m := make(map[int32]int32, n)
	for i := 0; i < n && e == nil; i++ {
		var k int32
		k, e = r.i32()
		if e == nil {
			m[k], e = r.i32()
		}
	}
	return m, e
}
func readMapL(r *reader, v int32) (map[int32]int64, error) {
	n, e := count(r, v)
	m := make(map[int32]int64, n)
	for i := 0; i < n && e == nil; i++ {
		var k int32
		k, e = r.i32()
		if e == nil {
			m[k], e = r.i64()
		}
	}
	return m, e
}
func readMapS(r *reader, v int32) (map[int32]string, error) {
	n, e := count(r, v)
	m := make(map[int32]string, n)
	for i := 0; i < n && e == nil; i++ {
		var k int32
		k, e = r.i32()
		if e == nil {
			m[k], e = r.str()
		}
	}
	return m, e
}
func readMapB(r *reader, v int32) (map[int32]blob, error) {
	n, e := count(r, v)
	m := make(map[int32]blob, n)
	itemsHash := StableHash("items")
	for i := 0; i < n && e == nil; i++ {
		var k, l int32
		k, e = r.i32()
		if e == nil {
			l, e = r.i32()
		}
		if e != nil || l < 0 || l > maxBlob {
			if e == nil {
				e = errors.New("invalid byte array")
			}
			continue
		}
		entry := blob{size: int(l)}
		if k == itemsHash {
			entry.data = make([]byte, l)
			e = r.read(entry.data)
		} else {
			e = r.skip(int64(l))
		}
		m[k] = entry
	}
	return m, e
}
func skipMap(r *reader, v int32, size int64) error {
	n, e := count(r, v)
	for i := 0; i < n && e == nil; i++ {
		e = r.skip(4 + size)
	}
	return e
}
func category(n string) string {
	l := strings.ToLower(n)
	switch {
	case strings.Contains(l, "portal"):
		return "portal"
	case strings.Contains(l, "terrain") || strings.Contains(l, "dig") || strings.Contains(l, "raise"):
		return "terrain"
	case strings.Contains(l, "chest") || strings.Contains(l, "container") || strings.Contains(l, "barrel"):
		return "container"
	case strings.Contains(l, "smelter") || strings.Contains(l, "kiln") || strings.Contains(l, "furnace") || strings.Contains(l, "windmill") || strings.Contains(l, "spinning") || strings.Contains(l, "ferment") || strings.Contains(l, "oven") || strings.Contains(l, "cooking"):
		return "production"
	case strings.Contains(l, "boar") || strings.Contains(l, "wolf") || strings.Contains(l, "lox") || strings.Contains(l, "hen") || strings.Contains(l, "chicken") || strings.Contains(l, "asksvin"):
		return "creature"
	case strings.HasPrefix(l, "piece_") || strings.Contains(l, "wall") || strings.Contains(l, "floor") || strings.Contains(l, "roof"):
		return "construction"
	case n == "":
		return "unknown"
	default:
		return "world"
	}
}
func retain(o Object, _ valueMaps) bool {
	return (o.Category != "world" && o.Category != "unknown") || o.ConnectionType != 0
}
func semanticCategory(o *Object, v valueMaps) {
	if has(v.s, "tag") {
		o.Category = "portal"
		return
	}
	if has(v.s, "items") || has(v.b, "items") {
		o.Category = "container"
		return
	}
	if hasAny(v.f, "fuel", "ore", "bakeTimer") || hasAny(v.i, "queued", "done") {
		o.Category = "production"
		return
	}
	if hasAny(v.i, "tamed", "pregnant", "lovePoints") || hasAny(v.s, "TamedName") {
		o.Category = "creature"
		return
	}
	if creator, ok := v.l[StableHash("creator")]; ok {
		o.Creator = creator
		// A zero creator is the game's own handiwork: the field exists on every piece a generated
		// location placed. Only a real player id means somebody built this.
		if creator != 0 {
			o.Category = "construction"
		}
		return
	}
}
func has[T any](m map[int32]T, name string) bool { _, ok := m[StableHash(name)]; return ok }
func hasAny[T any](m map[int32]T, names ...string) bool {
	for _, name := range names {
		if has(m, name) {
			return true
		}
	}
	return false
}
func properties(v valueMaps, c map[int32]string) []Property {
	p := make([]Property, 0, len(v.f)+len(v.v)+len(v.i)+len(v.l)+len(v.s)+len(v.b))
	for k, x := range v.f {
		var value any = x
		if floatBad(x) {
			value = "invalid float"
		}
		p = append(p, Property{k, c[k], value})
	}
	for k, x := range v.v {
		var value any = x
		if !validFinite(x) {
			value = "invalid vector"
		}
		p = append(p, Property{k, c[k], value})
	}
	for k, x := range v.i {
		p = append(p, Property{k, c[k], x})
	}
	for k, x := range v.l {
		p = append(p, Property{k, c[k], x})
	}
	for k, x := range v.s {
		if k == StableHash("items") {
			decoded, err := base64.StdEncoding.DecodeString(x)
			if err == nil {
				p = append(p, Property{k, c[k], fmt.Sprintf("%d bytes inventory", len(decoded))})
				continue
			}
		}
		p = append(p, Property{k, c[k], x})
	}
	for k, x := range v.b {
		p = append(p, Property{k, c[k], fmt.Sprintf("%d bytes", x.size)})
	}
	sort.Slice(p, func(i, j int) bool { return p[i].Hash < p[j].Hash })
	return p
}
func parseInventory(data []byte) (*Inventory, error) {
	r := &reader{r: bytes.NewReader(data)}
	version, err := r.i32()
	if err != nil || version < 100 || version > 106 {
		return nil, fmt.Errorf("unsupported inventory version %d", version)
	}
	count, err := r.i32()
	if err != nil || count < 0 || count > 4096 {
		return nil, errors.New("invalid inventory item count")
	}
	out := &Inventory{Version: version, Items: make([]InventoryItem, 0, count)}
	for i := int32(0); i < count; i++ {
		name, err := r.str()
		if err != nil {
			return nil, fmt.Errorf("item %d name: %w", i, err)
		}
		stack, err := r.i32()
		if err != nil {
			return nil, err
		}
		if _, err = r.f32(); err != nil {
			return nil, err
		}
		if _, err = r.i32(); err != nil {
			return nil, err
		}
		if _, err = r.i32(); err != nil {
			return nil, err
		}
		if _, err = r.u8(); err != nil {
			return nil, err
		}
		quality := int32(1)
		if version >= 101 {
			quality, err = r.i32()
			if err != nil {
				return nil, err
			}
		}
		if version >= 102 {
			if _, err = r.i32(); err != nil {
				return nil, err
			}
		}
		if version >= 103 {
			if _, err = r.i64(); err != nil {
				return nil, err
			}
			if _, err = r.str(); err != nil {
				return nil, err
			}
		}
		if version >= 104 {
			custom, err := r.i32()
			if err != nil || custom < 0 || custom > 4096 {
				return nil, errors.New("invalid item custom-data count")
			}
			for j := int32(0); j < custom; j++ {
				if _, err = r.str(); err != nil {
					return nil, err
				}
				if _, err = r.str(); err != nil {
					return nil, err
				}
			}
		}
		var worldLevel int32
		if version >= 105 {
			worldLevel, err = r.i32()
			if err != nil {
				return nil, err
			}
		}
		var picked bool
		if version >= 106 {
			value, err := r.u8()
			if err != nil {
				return nil, err
			}
			picked = value != 0
		}
		out.Items = append(out.Items, InventoryItem{Name: name, Stack: stack, Quality: quality, WorldLevel: worldLevel, PickedUp: picked})
	}
	if r.n != int64(len(data)) {
		return nil, fmt.Errorf("inventory has %d trailing bytes", int64(len(data))-r.n)
	}
	return out, nil
}
func validPos(p Vec3) bool {
	return !floatBad(p.X) && !floatBad(p.Y) && !floatBad(p.Z) && math.Abs(float64(p.X)) < 20000 && math.Abs(float64(p.Z)) < 20000
}
func validFinite(p Vec3) bool { return !floatBad(p.X) && !floatBad(p.Y) && !floatBad(p.Z) }
func floatBad(v float32) bool { return math.IsNaN(float64(v)) || math.IsInf(float64(v), 0) }
func parseFWLSeed(b []byte) string {
	r := &reader{r: bytes.NewReader(b)}
	declared, e := r.i32()
	if e != nil || int(declared) != len(b)-4 {
		return ""
	}
	version, e := r.i32()
	if e != nil || version < 20 || version > 100 {
		return ""
	}
	if _, e = r.str(); e != nil {
		return ""
	}
	s, e := r.str()
	if e != nil || len(s) > 64 {
		return ""
	}
	return s
}
func LocationCategory(name string) string {
	name = strings.ToLower(name)
	contains := func(parts ...string) bool {
		for _, part := range parts {
			if strings.Contains(name, part) {
				return true
			}
		}
		return false
	}
	switch {
	case contains("starttemple"):
		return "spawn"
	case contains("eikthyr", "gdking", "bonemass", "dragonqueen", "goblinking", "bossentrance", "faderlocation"):
		return "boss"
	case contains("vendor", "hildir_camp", "bogwitch", "tavern", "blacksmith"):
		return "trader"
	case contains("crypt", "cave", "trollcave", "morgenhole", "firehole", "bfd_exterior"):
		return "dungeon"
	case contains("fortress", "guardtower", "stonetower", "charredtower"):
		return "fortress"
	case contains("camp", "village", "farm", "woodhouse", "stonehouse", "swamphut", "dvergrtown", "harbour"):
		return "settlement"
	case contains("tarpit", "volturenest", "drakenest", "leviathan", "spawner", "sulfur", "infestedtree"):
		return "resource"
	case contains("runestone", "ruin", "grave", "dolmen", "ship", "statue", "giant", "sword", "viaduct", "lighthouse", "roadpost", "waymarker", "well", "excavation", "arch", "stonecircle", "stonehenge", "placeofmystery"):
		return "landmark"
	default:
		return "other"
	}
}

func finalize(s *Snapshot) {
	s.Summary.GeneratedZones = len(s.GeneratedZones)
	explored, sentinel := 0, 0
	for _, zone := range s.GeneratedZones {
		switch {
		case zone.X == sentinelZoneIndex || zone.Y == sentinelZoneIndex:
			sentinel++
		case zone.X >= -maxZoneIndex && zone.X <= maxZoneIndex && zone.Y >= -maxZoneIndex && zone.Y <= maxZoneIndex:
			explored++
		default:
			sentinel++
		}
	}
	s.Summary.ExploredZones = explored
	s.Summary.SentinelZones = sentinel
	area := float64(explored) * zoneSize * zoneSize
	s.Summary.ExploredSquareKm = area / 1_000_000
	// Against the playable circle, not the square the tiles cover: a player cannot walk the corners.
	s.Summary.ExploredPercent = area / (math.Pi * playableRadius * playableRadius) * 100
	if sentinel > 0 {
		s.Health.Findings = append(s.Health.Findings,
			fmt.Sprintf("%d generated zones sit at the game's far-away sentinel position and are not counted as explored", sentinel))
	}
	s.Summary.Locations = len(s.Locations)
	s.Summary.LocationCategories = make(map[string]int)
	for index := range s.Locations {
		if s.Locations[index].Category == "" {
			s.Locations[index].Category = LocationCategory(s.Locations[index].Name)
		}
		s.Summary.LocationCategories[s.Locations[index].Category]++
	}
	b := [4]float32{0, 0, 0, 0}
	first := true
	construction := make([]constructionPoint, 0, s.Summary.Categories["construction"])
	kept := s.Objects[:0]
	for _, o := range s.Objects {
		if validPos(o.Position) {
			if first {
				b = [4]float32{o.Position.X, o.Position.Z, o.Position.X, o.Position.Z}
				first = false
			} else {
				b[0] = min(b[0], o.Position.X)
				b[1] = min(b[1], o.Position.Z)
				b[2] = max(b[2], o.Position.X)
				b[3] = max(b[3], o.Position.Z)
			}
		}
		if o.Category == "construction" && validPos(o.Position) {
			construction = append(construction, constructionPoint{Position: o.Position, Creator: o.Creator})
		} else {
			kept = append(kept, o)
		}
	}
	s.Objects = kept
	s.ConstructionCoverage = aggregateConstructionCoverage(construction)
	s.Clusters = aggregateConstructionClusters(construction)
	s.Summary.Bounds = b
	if s.Health.InvalidCoordinates > 0 {
		s.Health.Findings = append(s.Health.Findings, "objects with sentinel or invalid coordinates were excluded from map bounds")
	}
	if s.Health.UnknownPrefabs > 0 {
		s.Health.Findings = append(s.Health.Findings, fmt.Sprintf("%d prefab hashes are unresolved; treat them as vanilla-or-mod-unknown until cataloged", s.Health.UnknownPrefabs))
	}
	sort.Strings(s.GlobalKeys)
}

func aggregateConstructionCoverage(points []constructionPoint) *ConstructionCoverage {
	if len(points) == 0 {
		return nil
	}
	cellSize := constructionCoverageBaseCell
	// Per cell, a tally per builder: the coarsening below merges cells, and a builder's pieces have to
	// survive that merge or the colour of a zoomed-out cell would be decided by whichever child won.
	cells := make(map[[2]int]map[int64]int, min(len(points), maxConstructionCoverageCells+1))
	for _, point := range points {
		key := constructionCell(point.Position, cellSize)
		tally := cells[key]
		if tally == nil {
			tally = map[int64]int{}
			cells[key] = tally
		}
		tally[point.Creator]++
	}
	for len(cells) > maxConstructionCoverageCells {
		coarser := make(map[[2]int]map[int64]int, min(len(cells), maxConstructionCoverageCells+1))
		for key, tally := range cells {
			parent := [2]int{floorHalf(key[0]), floorHalf(key[1])}
			merged := coarser[parent]
			if merged == nil {
				merged = map[int64]int{}
				coarser[parent] = merged
			}
			for creator, pieces := range tally {
				merged[creator] += pieces
			}
		}
		cells = coarser
		cellSize *= 2
	}
	keys := sortedConstructionCells(cells)
	coverage := &ConstructionCoverage{
		CellSize:    cellSize,
		TotalPieces: len(points),
		Cells:       make([]CoverageCell, 0, len(keys)),
	}
	for _, key := range keys {
		tally := cells[key]
		pieces, dominant, dominantPieces := 0, int64(0), 0
		for creator, count := range tally {
			pieces += count
			// Ties resolve on the id so the same world always draws the same colours; a cell that
			// flickered between two builders between refreshes would be worse than one flat green.
			if count > dominantPieces || (count == dominantPieces && creator > dominant) {
				dominant, dominantPieces = creator, count
			}
		}
		coverage.MaxPieces = max(coverage.MaxPieces, pieces)
		coverage.Cells = append(coverage.Cells, CoverageCell{
			X: key[0], Z: key[1], Pieces: pieces, Creator: dominant, Builders: len(tally),
		})
	}
	return coverage
}

type constructionClusterAccumulator struct {
	x      float64
	y      float64
	z      float64
	pieces int
	center Vec3
	radius float32
}

// constructionPoint is a piece and whoever placed it, so clusters can be grouped per builder: two
// people building in the same valley are two clusters, which is the whole point of colouring them.
type constructionPoint struct {
	Position Vec3
	Creator  int64
}

func aggregateConstructionClusters(points []constructionPoint) []Cluster {
	type clusterKey struct {
		cell    [2]int
		creator int64
	}
	accumulators := make(map[clusterKey]*constructionClusterAccumulator)
	for _, item := range points {
		point := item.Position
		key := clusterKey{cell: constructionCell(point, constructionClusterCell), creator: item.Creator}
		accumulator := accumulators[key]
		if accumulator == nil {
			accumulator = &constructionClusterAccumulator{}
			accumulators[key] = accumulator
		}
		accumulator.x += float64(point.X)
		accumulator.y += float64(point.Y)
		accumulator.z += float64(point.Z)
		accumulator.pieces++
	}
	keys := make([]clusterKey, 0, len(accumulators))
	for key, accumulator := range accumulators {
		if accumulator.pieces >= 3 {
			keys = append(keys, key)
		}
	}
	sort.Slice(keys, func(i, j int) bool {
		left, right := accumulators[keys[i]], accumulators[keys[j]]
		if left.pieces != right.pieces {
			return left.pieces > right.pieces
		}
		if keys[i].cell[0] != keys[j].cell[0] {
			return keys[i].cell[0] < keys[j].cell[0]
		}
		if keys[i].cell[1] != keys[j].cell[1] {
			return keys[i].cell[1] < keys[j].cell[1]
		}
		return keys[i].creator < keys[j].creator
	})
	if len(keys) > maxConstructionClusters {
		keys = keys[:maxConstructionClusters]
	}
	selected := make(map[clusterKey]*constructionClusterAccumulator, len(keys))
	for _, key := range keys {
		accumulator := accumulators[key]
		divisor := float64(accumulator.pieces)
		accumulator.center = Vec3{
			X: float32(accumulator.x / divisor),
			Y: float32(accumulator.y / divisor),
			Z: float32(accumulator.z / divisor),
		}
		selected[key] = accumulator
	}
	for _, item := range points {
		point := item.Position
		accumulator := selected[clusterKey{cell: constructionCell(point, constructionClusterCell), creator: item.Creator}]
		if accumulator == nil {
			continue
		}
		distance := float32(math.Hypot(float64(point.X-accumulator.center.X), float64(point.Z-accumulator.center.Z)))
		accumulator.radius = max(accumulator.radius, distance)
	}
	sort.Slice(keys, func(i, j int) bool {
		if keys[i].cell[0] != keys[j].cell[0] {
			return keys[i].cell[0] < keys[j].cell[0]
		}
		if keys[i].cell[1] != keys[j].cell[1] {
			return keys[i].cell[1] < keys[j].cell[1]
		}
		return keys[i].creator < keys[j].creator
	})
	clusters := make([]Cluster, 0, len(keys))
	for _, key := range keys {
		accumulator := selected[key]
		clusters = append(clusters, Cluster{
			ID:      len(clusters) + 1,
			Center:  accumulator.center,
			Radius:  accumulator.radius,
			Pieces:  accumulator.pieces,
			Creator: key.creator,
		})
	}
	return clusters
}

func constructionCell(point Vec3, cellSize int) [2]int {
	size := float64(cellSize)
	return [2]int{
		int(math.Floor(float64(point.X) / size)),
		int(math.Floor(float64(point.Z) / size)),
	}
}

func floorHalf(value int) int {
	if value >= 0 {
		return value / 2
	}
	return -((-value + 1) / 2)
}

func sortedConstructionCells[T any](cells map[[2]int]T) [][2]int {
	keys := make([][2]int, 0, len(cells))
	for key := range cells {
		keys = append(keys, key)
	}
	sort.Slice(keys, func(i, j int) bool {
		if keys[i][0] != keys[j][0] {
			return keys[i][0] < keys[j][0]
		}
		return keys[i][1] < keys[j][1]
	})
	return keys
}
func Compare(old, new Snapshot) *Diff {
	d := &Diff{Older: old.Source.Backup, Newer: new.Source.Backup, ObjectDelta: new.Summary.Objects - old.Summary.Objects, ZoneDelta: new.Summary.GeneratedZones - old.Summary.GeneratedZones, CategoryDelta: map[string]int{}}
	for k, v := range new.Summary.Categories {
		d.CategoryDelta[k] = v - old.Summary.Categories[k]
	}
	seen := map[string]bool{}
	for _, l := range old.Locations {
		seen[fmt.Sprintf("%s:%.0f:%.0f", l.Name, l.Position.X, l.Position.Z)] = true
	}
	for _, l := range new.Locations {
		if !seen[fmt.Sprintf("%s:%.0f:%.0f", l.Name, l.Position.X, l.Position.Z)] {
			d.NewLocations = append(d.NewLocations, l)
		}
	}
	if math.Abs(float64(d.ObjectDelta)) > float64(max(1000, old.Summary.Objects/5)) {
		d.Findings = append(d.Findings, "large object-count change between consecutive backups")
	}
	if d.ZoneDelta < 0 {
		d.Findings = append(d.Findings, "generated-zone count decreased")
	}
	return d
}
func Recommendations(s Snapshot, d *Diff) []string {
	r := []string{}
	if s.WorldVersion < MaxWorldVersion {
		r = append(r, fmt.Sprintf("Save format %d predates the parser target %d; preserve this backup before first load after updating.", s.WorldVersion, MaxWorldVersion))
	}
	if s.Health.Level != "healthy" {
		r = append(r, "Resolve critical save findings and unknown prefab ownership before removing or changing mods.")
	}
	if d != nil && len(d.Findings) > 0 {
		r = append(r, "Inspect the backup diff findings before upgrade or regeneration work.")
	}
	if s.Summary.Categories["terrain"] > 10000 {
		r = append(r, "High terrain-edit density: avoid location regeneration near developed zones and retain rollback backups.")
	}
	if len(r) == 0 {
		r = append(r, "No blocking save-health signal was detected; keep this immutable backup as the rollback point.")
	}
	return r
}
func MarshalReport(current Snapshot, previous *Snapshot) ([]byte, error) {
	var d *Diff
	if previous != nil {
		d = Compare(*previous, current)
	}
	return json.Marshal(Report{Snapshot: current, Previous: previous, Diff: d, Recommendations: Recommendations(current, d)})
}
