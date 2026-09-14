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
	MaxWorldVersion              = 41
	maxObjects                   = 2_000_000
	maxPropertyItems             = 32_767
	maxBlob                      = 64 << 20
	maxArchiveMember             = 512 << 20
	constructionCoverageBaseCell = 32
	constructionClusterCell      = 128
	maxConstructionCoverageCells = 2_048
	maxConstructionClusters      = 1_024
)

// Valheim 1.0.12 (save version 41) replaced the single <World>.db with a <World>/ directory. Two
// version thresholds separate the two record layouts, both read out of the 1.0.12 server assembly
// rather than guessed (monodis of
// Ulfsland/data/server/valheim_server_Data/Managed/assembly_valheim.dll):
//
//   - ZDO::Load IL_0000 computes `version >= 40` once and uses it twice: at IL_0091 it reads and
//     throws away a Vector2s - the ZDO's own sector index, 4 bytes - only below 40, and at IL_00e4
//     it picks ReadVector3 (12 bytes) below 40 versus ReadSmallRotation (2 or 4) at or above it.
//     So a version-40+ ZDO record is 8 to 12 bytes shorter than the same object was at 37.
//   - ZoneSystem::LoadOld IL_00f3 reads a location's prefab as a string below 40 and as a bare
//     int32 stable hash at or above it.
//
// legacyMaxWorldVersion is the last version that shipped as one monolithic .db file, so it is what
// ParseDB accepts; 40 and 41 only ever appear inside a 1.0 world directory, which ParseSave10 reads.
const (
	worldVersionCompactZDO = 40
	legacyMaxWorldVersion  = 39
)

// ZDO.ExtraDataFlags bits that are not property maps. 256 is Persistent and 512 is Distant; bits 10
// and 11 carry ObjectType. These two are the ones that change how long the record is.
const (
	flagRotation      = 4096
	flagSmallPosition = 8192
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
	// UnresolvedLocations only ever rises on a 1.0 world: the old format wrote a location's prefab
	// name as a string, so there was nothing to resolve. Carries omitempty so an old-format
	// snapshot serialises exactly as it did before.
	UnresolvedLocations int `json:"unresolved_locations,omitempty"`
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

// position reads a ZDO's position. Flag bit 13 means Valheim wrote it as a Vector2s - two int16
// with y implied zero - instead of a Vector3, which is four bytes rather than twelve
// (Utils::SmallPosition on the write side, ZDO::Load IL_0091 on the read side).
//
// The bit is honoured at every version, deliberately without a version gate, because the 1.0.12
// assembly is what reads a version-37 save today and its ZDO::Load tests bit 13 before it tests
// the version. Nothing in the four old worlds on this host exercises it: the flag needs y to be
// exactly 0 and x/z to be whole numbers inside int16, and y is terrain height.
func (r *reader) position(flags uint16) (Vec3, error) {
	if flags&flagSmallPosition == 0 {
		return r.vec3()
	}
	x, e := r.i16()
	if e != nil {
		return Vec3{}, e
	}
	z, e := r.i16()
	return Vec3{X: float32(x), Z: float32(z)}, e
}

// skipSmallRotation consumes the quantised rotation 1.0 writes in place of a Vector3. Per
// ZPackage::ReadSmallRotation, the first uint16 carries bit 15 when only the Y angle was stored, and
// otherwise it is the high half of a 32-bit triple of 10-bit angles, so the record is two bytes or
// four. Measured on Ulfsland: the two rotated ZDOs in 00_00__0_1.chunk hold 0x8198 and 0x82a6,
// both with bit 15 set, i.e. the two-byte form for Y = 204 and 339 degrees.
func (r *reader) skipSmallRotation() error {
	packed, e := r.u16()
	if e != nil || packed&0x8000 != 0 {
		return e
	}
	_, e = r.u16()
	return e
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

// tokenRE matches an identifier that could be a prefab name. The optional leading underscore is
// there for Valheim's internal prefabs - _ZoneCtrl, _TerrainCompiler, _NetScene. Without it the
// match started at the first letter and cataloged "ZoneCtrl", which is not a prefab and hashes to
// something no save contains, so all 81 _ZoneCtrl objects in a fresh 1.0 world counted as unknown
// prefabs and raised a health finding about a purely internal object.
var tokenRE = regexp.MustCompile(`_?[A-Za-z][A-Za-z0-9_$.:+-]{2,119}`)

// IsSoftRefManifest reports whether a file name is a SoftReferenceableAssets text manifest - the
// plain-text index of what lives in an asset bundle, and on a 1.0 world the only way a location's
// prefab hash gets a name at all.
//
// The test is positive on the two things every one of them has: no extension, and "manifest" in the
// name. Valheim's own pair are StreamingAssets/SoftRef/manifest and manifest_extended. A mod that
// adds locations ships its own alongside its bundles under whatever name it chose - More World
// Locations AIO calls its one assetBundleManifest_full, and matching only the two vanilla names
// left every location that mod places with an empty name. Measured on Ulfsland at seed Pirate68:
// 2,116 of 14,040 location instances across 182 prefab hashes were nameless, and that one file
// names all 2,116. The name is matched rather than the content because the file has to be chosen
// before it is read, and an extensionless name containing "manifest" matches nothing else in a
// game install or a plugin tree - a Thunderstore manifest.json is already admitted by extension.
func IsSoftRefManifest(name string) bool {
	base := strings.ToLower(filepath.Base(name))
	return filepath.Ext(base) == "" && strings.Contains(base, "manifest")
}

func CatalogFromFiles(paths ...string) map[int32]string {
	out := knownCatalog()
	const maxCatalogBytes int64 = 1 << 30
	var scanned int64
	var files int
	add := func(path string, info fs.FileInfo) {
		if files >= 20_000 || scanned >= maxCatalogBytes || info.Size() <= 0 || info.Size() > 256<<20 {
			return
		}
		base := strings.ToLower(filepath.Base(path))
		switch {
		// A SoftRef manifest has no extension at all, and 1.0 needs it: Valheim 1.0 moved much of
		// its prefab and location naming into SoftReferenceableAssets bundles, and the manifest is
		// the only plain-text index of what is in them. This is the difference between a usable 1.0
		// map and an unlabelled one. Measured on Ulfsland: resources.assets plus assembly_valheim.dll
		// give 384,755 entries and name 275 of its 12,228 location instances across 5 distinct
		// prefabs; adding the game's two manifests gives 437,939 entries and names all 12,228 across
		// 177 prefabs, and takes unresolved object prefab hashes from 81 to 0.
		case IsSoftRefManifest(base):
		default:
			switch strings.ToLower(filepath.Ext(path)) {
			case ".dll", ".assets", ".json", ".cfg", ".yml", ".yaml", ".txt":
			default:
				return
			}
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return
		}
		files++
		scanned += int64(len(data))
		register := func(s string) {
			if strings.Contains(s, "::") || strings.Contains(s, "System.") {
				return
			}
			if h := StableHash(s); out[h] == "" {
				out[h] = s
			}
		}
		for _, raw := range tokenRE.FindAll(data, -1) {
			s := string(raw)
			register(s)
			// Register the underscore-stripped form too, so widening tokenRE can only add
			// resolutions: without the leading underscore this is the name the old pattern would
			// have cataloged, and dropping it could silently un-name a prefab that resolves today.
			if s[0] == '_' && len(s) > 4 {
				register(s[1:])
			}
			// A SoftRef manifest names assets by path - "path in bundle:
			// Assets/Systems/_ZoneCtrl.prefab" - and a prefab's name is that file's stem. The dot
			// is inside tokenRE's character class, so without this the token is "_ZoneCtrl.prefab"
			// and the prefab hash never resolves. Measured: manifest_extended holds 5,839 .prefab
			// paths and 821 .asset paths.
			for _, ending := range [...]string{".prefab", ".asset"} {
				if strings.HasSuffix(s, ending) && len(s) > len(ending)+2 {
					stem := s[:len(s)-len(ending)]
					register(stem)
					if stem[0] == '_' && len(stem) > 4 {
						register(stem[1:])
					}
				}
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

// AnalyzeArchive reads one world backup, in either of the two shapes hostops/backup_valheim_world.sh
// produces. A 0.220 backup holds exactly two members, <stem>.db and <stem>.fwl. A 1.0.12 backup
// holds the world directory instead - <stem>/_main.N.fwl2, .db2, .chunks, .ok and the per-chunk
// files - because that is what worlds_local/<stem> now is.
//
// The shape is decided by what is in the archive, never by a version guess: the 1.0 branch is taken
// only when a member is a _main.N.fwl2, an ending that did not exist before 1.0. The old branch is
// left exactly as strict as it was, because the four 0.220 worlds on this host are the rollback path.
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
	// Members of a 1.0 world directory, by basename, which is how the directory itself keys them
	// and how the .chunks index names the chunk files.
	directory := map[string][]byte{}
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
		var member []byte
		switch {
		case strings.HasSuffix(base, ".db"):
			db, err = io.ReadAll(io.LimitReader(tr, maxArchiveMember+1))
		case strings.HasSuffix(base, ".fwl"):
			fwl, err = io.ReadAll(io.LimitReader(tr, 1<<20))
		case strings.HasSuffix(base, endingDB2), strings.HasSuffix(base, endingChunk):
			member, err = io.ReadAll(io.LimitReader(tr, maxArchiveMember+1))
		case strings.HasSuffix(base, endingFWL2), strings.HasSuffix(base, endingChunks), strings.HasSuffix(base, endingOK):
			member, err = io.ReadAll(io.LimitReader(tr, 1<<20))
		default:
			continue
		}
		if member != nil {
			if len(member) > maxArchiveMember {
				return Snapshot{}, errors.New("database too large")
			}
			directory[base] = member
		}
		if err != nil {
			return Snapshot{}, err
		}
	}
	source := Source{Backup: filepath.Base(path), SHA256: hex.EncodeToString(h.Sum(nil)), ModifiedAt: info.ModTime().UTC()}
	var s Snapshot
	var fwlBytes []byte
	switch {
	case IsWorldDirectory(directory):
		save, err := CollectSave10(directory)
		if err != nil {
			return Snapshot{}, err
		}
		if s, err = ParseSave10(save, catalog); err != nil {
			return Snapshot{}, err
		}
		fwlBytes = save.FWL2
		// DBBytes is the whole world database, which in 1.0 is the db2 plus its index plus every
		// chunk file the index named - not just the db2, or the number would understate a big
		// world by however much of it lives in chunks.
		source.DBBytes = int64(len(save.DB2) + len(save.Index))
		for _, chunk := range save.Chunks {
			source.DBBytes += int64(len(chunk))
		}
	default:
		if len(db) == 0 || len(fwl) == 0 {
			return Snapshot{}, errors.New("backup must contain one db/fwl pair or one 1.0 world directory")
		}
		if len(db) > maxArchiveMember {
			return Snapshot{}, errors.New("database too large")
		}
		if s, err = ParseDB(bytes.NewReader(db), catalog); err != nil {
			return Snapshot{}, err
		}
		fwlBytes = fwl
		source.DBBytes = int64(len(db))
	}
	source.FWLBytes = int64(len(fwlBytes))
	s.Schema = SchemaVersion
	s.World = world
	// parseFWLSeed reads an .fwl2 unchanged: measured byte for byte, the 1.0 header is the same
	// container - int32 declared length, int32 world version, then the name and the seed name as
	// 7-bit-prefixed strings. Hrafnheim's .fwl is 2e000000 25000000 09"Hrafnheim" 0a"qmrbecQI2K",
	// Ulfsland's .fwl2 is 8c000000 29000000 08"Ulfsland" 0a"8JiFcknsJd"; only the trailer differs.
	s.Seed = parseFWLSeed(fwlBytes)
	s.Source = source
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
	// ParseDB reads the monolithic <World>.db only. A version-40-or-later save is a directory and
	// its ZDOs are not in this file at all, so accepting one here would parse the ZoneSystem block
	// as objects; ParseSave10 is the entry point for those.
	if version < MinWorldVersion || version > legacyMaxWorldVersion {
		return Snapshot{}, fmt.Errorf("unsupported world version %d (supported %d-%d)", version, MinWorldVersion, legacyMaxWorldVersion)
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
		s.absorbObject(o, vals, catalog)
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

// absorbObject folds one parsed ZDO into the snapshot: semantic classification, inventory decode,
// the counters, and the retain decision. Both save formats call it, so a 1.0 world and a 0.220 world
// are classified by exactly the same code - which is the point, because the vehicle and category
// classifiers were verified against Hrafnheim's 1,780,660-entry catalog and must not fork.
func (s *Snapshot) absorbObject(o Object, vals valueMaps, catalog map[int32]string) {
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

func readObject(r *reader, version int32, catalog map[int32]string, id uint32) (Object, valueMaps, error) {
	flags, e := r.u16()
	if e != nil {
		return Object{}, valueMaps{}, e
	}
	// The ZDO's own sector index, which 1.0 dropped because the chunk file name now carries the
	// zone (ChunkSaveMapping::GetChunkFilename). Measured on Ulfsland's 00_00__0_1.chunk: 83
	// records in 1498 bytes, which only closes as 81*18 + 2*20 once these four bytes are gone.
	if version < worldVersionCompactZDO {
		if e = r.skip(4); e != nil {
			return Object{}, valueMaps{}, e
		}
	}
	p, e := r.position(flags)
	if e != nil {
		return Object{}, valueMaps{}, e
	}
	ph, e := r.i32()
	if e != nil {
		return Object{}, valueMaps{}, e
	}
	o := Object{ID: id, PrefabHash: ph, Prefab: catalog[ph], Position: p, Persistent: flags&256 != 0, Distant: flags&512 != 0, Type: uint8(flags >> 10 & 3)}
	o.Category = category(o.Prefab)
	if flags&flagRotation != 0 {
		if version < worldVersionCompactZDO {
			e = r.skip(12)
		} else {
			e = r.skipSmallRotation()
		}
		if e != nil {
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
	// Boats and carts sit below the container and construction cases on purpose. shipwreck_karve_chest
	// is loot furniture rather than a hull, and piece_cartographytable and piece_upgradecart are build
	// pieces, so the earlier cases must keep them - that ordering is the whole reason a wreck chest
	// stays a container.
	case vehicleHull(n):
		return "vehicle"
	case n == "":
		return "unknown"
	default:
		return "world"
	}
}
func retain(o Object, _ valueMaps) bool {
	return (o.Category != "world" && o.Category != "unknown") || o.ConnectionType != 0
}

// hullWords are the nouns Valheim and the boat mods installed on Hrafnheim use for a hull:
// vanilla Raft, Karve, VikingShip and Cart, BoatAdditions' BBA_Knarr, BBA_LargeRaft and
// BBA_OutriggerKarve, CraftyCartsRemake's workbench_cart family, and longship_ashlands.
var hullWords = [...]string{"raft", "karve", "knarr", "vikingship", "longship", "cart"}

// vehicleHull reports whether a prefab name names a boat or a cart.
//
// Boats, ships and carts were missing from the map entirely until 2026-08-21: every hull name fell
// through category() to "world", and retain() drops "world". The prefab hashes resolved perfectly -
// measured on Hrafnheim, Raft came back named "Raft" at (284, 30, -431) and was then thrown away -
// so the missing classifier was the whole defect.
//
// The match is anchored instead of a bare strings.Contains because the obvious keywords hide inside
// unrelated words: "crafting" contains "raft" and "cartography" contains "cart". Measured against
// Hrafnheim's 1,780,660-entry catalog, 571 names contain "raft" and very nearly all of them are
// Crafting symbols, so a bare Contains would have relabelled half the mod surface as a boat.
//
// A hull noun therefore has to end the name or be followed by a separator ("Cart", "workbench_cart",
// "BBA_LargeRaft", "longship_ashlands"). That right-hand edge is what rejects "Cartography" and
// "LongshipUpgrades_MapTable" without maintaining an exclusion list. The left edge is looser - start
// of name, a separator, or a camelCase hump - because the modifier in "LargeRaft" is part of the
// name, and the original casing is consulted there since lowercasing hides the hump.
func vehicleHull(name string) bool {
	for _, word := range hullWords {
		// range over a negative count simply does not iterate, so a word longer than the name is safe.
		for at := range len(name) - len(word) + 1 {
			if !strings.EqualFold(name[at:at+len(word)], word) {
				continue
			}
			if end := at + len(word); end != len(name) && asciiLetter(name[end]) {
				continue
			}
			// A camelCase hump is a lowercase-to-uppercase transition. Requiring the transition rather
			// than merely an uppercase letter is what stops "CRAFT" and "SECTION_CRAFT_TELEPORT" from
			// reading as hulls: inside an all-caps run the capital is an acronym letter, not a new word.
			if at == 0 || !asciiLetter(name[at-1]) || (asciiUpper(name[at]) && !asciiUpper(name[at-1])) {
				return true
			}
		}
	}
	return false
}

func asciiLetter(b byte) bool { c := b | 0x20; return c >= 'a' && c <= 'z' }
func asciiUpper(b byte) bool  { return b >= 'A' && b <= 'Z' }
func semanticCategory(o *Object, v valueMaps) {
	// A Karve, a longship and a cart all carry cargo, so they hold an "items" map exactly like a chest
	// does, and the container rule below would relabel every loaded hull a container - a boat would
	// then be retained but drawn as a chest, which is the second half of why boats never looked like
	// boats. The hull name is the stronger signal, so it wins; the inventory is attached by the caller
	// regardless of category, so a boat keeps its cargo either way.
	if o.Category == "vehicle" {
		return
	}
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
	// Order is the whole contract. Prefab ids stack words, so the interesting names match more than
	// one rule: MWL_RuinsChurch1 is a church that happens to be ruined, StoneTowerRuins03 is a
	// tower, StartTemple is the world spawn that happens to be a temple. Every case claims the most
	// specific noun and the generic condition word ("ruin") is left for last. On 2026-08-21 a church
	// 100 m from a player's raft was indistinguishable from a runestone, because everything carrying
	// "ruin" landed in one blue landmark bucket; this ordering is what separates them.
	switch {
	case contains("starttemple"):
		// Ahead of shrine: the spawn altar contains "temple", and there is exactly one of it.
		return "spawn"
	case contains("eikthyr", "gdking", "bonemass", "dragonqueen", "goblinking", "bossentrance", "faderlocation"):
		return "boss"
	case contains("vendor", "hildir_camp", "bogwitch", "tavern", "blacksmith"):
		return "trader"
	case contains("crypt", "cave", "trollcave", "morgenhole", "firehole", "bfd_exterior", "tomb"):
		// Tombs belong with the crypts: they are enterable structures, not scenery.
		return "dungeon"
	case contains("church", "chapel", "temple", "altar", "shrine"):
		// Ahead of tower and ruins. A sacred building is what the player sees whatever state it is
		// in, and 31 of these 322 also match "ruin".
		return "shrine"
	case contains("tower"):
		// Ahead of fortress, or the 1127 GuardTower/StoneTower locations already reading "fortress"
		// never move. A guard tower is a tower; fortress below keeps the actual strongholds.
		return "tower"
	case contains("fortress"):
		// FortressRuins is a fortress in a state of disrepair, so the noun beats "ruin" below.
		return "fortress"
	case contains("arena"):
		// Ahead of ruins: three of the four arena prefabs are named MWL_RuinsArena*.
		return "arena"
	case contains("mine", "quarry", "excavat"):
		return "mine"
	case contains("port", "harbour", "harbor", "dock", "pier"):
		// Ahead of settlement, which used to file Mistlands_Harbour1 as a village.
		return "port"
	case contains("statue", "monument", "obelisk", "dolmen", "stonecircle", "stonehenge", "runestone"):
		return "monument"
	case contains("camp", "village", "farm", "woodhouse", "stonehouse", "swamphut", "dvergrtown"):
		return "settlement"
	case contains("tarpit", "volturenest", "drakenest", "leviathan", "spawner", "sulfur", "infestedtree"):
		return "resource"
	case contains("ruin"):
		// Deliberately the last structure rule: "ruin" is a condition, not a building, so every rule
		// above gets first refusal on the 2009 names carrying the word.
		return "ruins"
	case contains("grave", "ship", "giant", "sword", "viaduct", "lighthouse", "roadpost", "waymarker", "well", "arch", "placeofmystery"):
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
