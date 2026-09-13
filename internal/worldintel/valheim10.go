package worldintel

import (
	"bytes"
	"compress/gzip"
	"errors"
	"fmt"
	"io"
	"sort"
	"strconv"
	"strings"
)

// Valheim 1.0.12 replaced worlds_local/<World>.db + <World>.fwl with a worlds_local/<World>/
// directory. Measured on Ulfsland after a save (save version 41, network version 40):
//
//	_main.2.fwl2      144 B   world metadata
//	_main.2.db2    145402 B   net time + the compressed ZoneSystem block
//	_main.2.chunks     21 B   index naming which .chunk files belong to this save
//	_main.2.ok          4 B   int32 41, written last as the save-complete marker
//	00_00__0_1.chunk 1504 B   83 ZDOs
//
// The N in _main.N.* is SaveSystem::GetSaveNumber(), a uint32 bumped on every save, so the highest N
// is the newest. Measured: Ulfsland rolled 1 -> 2 on a periodic autosave while this was being
// written, and the chunk file was NOT rewritten - 00_00__0_1.chunk stayed byte-identical and both
// generations' indexes named it. Chunk files therefore outlive a generation and the index is the
// only authority on which ones are current; globbing *.chunk would pick up chunks that
// ZDOMan::DeleteOldChunks has orphaned but not yet removed.
//
// What moved, against the 0.220 layout that ParseDB reads:
//
//   - The ZDO list left the world file entirely. It used to follow the db header; now it lives in
//     the per-chunk files and the db2 holds only ZoneSystem + RandEventSystem + PersistentEventSystem.
//   - The db header lost ZDOMan's three fields (int64 next-uid, uint32, int32 ZDO count). The db2
//     header is int32 version + float64 net time and nothing else, then straight into ZoneSystem.
//   - The ZoneSystem block is gzip-compressed, length-prefixed (ZoneSystem::Save writes
//     int32 len then ZPackage::GetCompressed()).
//   - Generated zones went from Vector2i to Vector2s: 4 bytes per zone, not 8.
//   - m_pgwVersion is GONE. The old file wrote pgwVersion then locationVersion; ZoneSystem::Save in
//     1.0.12 writes only m_locationVersion. Measured: Hrafnheim (v37) reads 0 then 31, Ulfsland
//     (v41) has a single 32.
//   - A location instance records its prefab as an int32 stable hash instead of a string, so a name
//     is now only recoverable through the prefab catalog.
//   - Global keys are still a counted list of strings in the same place, and are still 0 on a world
//     nobody has played. The Brotli-compressed JSON at the tail of the db2 is
//     PersistentEventSystem, not the keys.
const (
	// mainFilePrefix and the endings are SaveSystem's own literals: c_MainFileName "_main.",
	// c_WorldFwl2FileEnding ".fwl2", c_WorldDb2FileEnding ".db2", c_WorldChunksFileEnding
	// ".chunks", c_WorldOKFileEnding ".ok", c_ChunkFileEnding ".chunk".
	mainFilePrefix = "_main."
	endingFWL2     = ".fwl2"
	endingDB2      = ".db2"
	endingChunks   = ".chunks"
	endingOK       = ".ok"
	endingChunk    = ".chunk"

	// A world is 21,000 m across at 64 m per zone, so a full 1.0 world cannot need anything like
	// this many chunk files; the bound exists so a corrupt index cannot make us allocate.
	maxChunkFiles = 65_536
	maxZoneCount  = 2_000_000
	maxLocations  = 100_000
	maxGlobalKeys = 10_000
)

// Save10 is the file set of one save generation of a 1.0 world directory, keyed the way the
// directory itself keys it. Chunks holds only the files the index names.
type Save10 struct {
	Generation uint32
	FWL2       []byte
	DB2        []byte
	Index      []byte
	Chunks     map[string][]byte
}

// IsWorldDirectory reports whether a set of members, keyed by basename, is a 1.0 world directory
// rather than an old-format pair. The test is positive on the thing that only 1.0 has - a
// _main.N.fwl2 - so an old-format directory can never be mistaken for one, and neither can a
// directory holding nothing but Valheim's own *_backup_auto-* leftovers.
func IsWorldDirectory(files map[string][]byte) bool {
	for name := range files {
		if _, ok := generationOf(strings.ToLower(name), endingFWL2); ok {
			return true
		}
	}
	return false
}

// generationOf pulls N out of "_main.N.<ending>". Returns false for anything else, which is how
// per-chunk files (00_00__0_1.chunk) and the game's own backup directories are skipped.
func generationOf(name, ending string) (uint32, bool) {
	if !strings.HasPrefix(name, mainFilePrefix) || !strings.HasSuffix(name, ending) {
		return 0, false
	}
	digits := name[len(mainFilePrefix) : len(name)-len(ending)]
	if digits == "" {
		return 0, false
	}
	n, err := strconv.ParseUint(digits, 10, 32)
	if err != nil {
		return 0, false
	}
	return uint32(n), true
}

// chunkFileName rebuilds the name ChunkSaveMapping::GetChunkFilename builds, which is the only way
// to get from an index entry to a file: two hex digits of chunk X, two of chunk Y, then the chunk
// size and the chunk's own version in decimal. Verified against Ulfsland's index, whose single
// entry (chunk 0x0000, size 0, version 1) names 00_00__0_1.chunk.
func chunkFileName(chunk uint16, size uint8, version uint32) string {
	return fmt.Sprintf("%02x_%02x__%d_%d%s", chunk>>8, chunk&0xff, size, version, endingChunk)
}

// CollectSave10 picks the newest complete generation out of a 1.0 world directory. Completeness is
// the .ok file: ZNet::SaveWorldThread writes the chunks, then the db2, and names the .ok path
// alongside them, so a generation without one is a torn save and must not be read as if it were
// whole. Keys are basenames; values are the file contents.
func CollectSave10(files map[string][]byte) (Save10, error) {
	type generation struct {
		fwl2, db2, index []byte
		committed        bool
	}
	generations := map[uint32]*generation{}
	at := func(n uint32) *generation {
		if generations[n] == nil {
			generations[n] = &generation{}
		}
		return generations[n]
	}
	for name, data := range files {
		lower := strings.ToLower(name)
		if n, ok := generationOf(lower, endingFWL2); ok {
			at(n).fwl2 = data
		} else if n, ok := generationOf(lower, endingDB2); ok {
			at(n).db2 = data
		} else if n, ok := generationOf(lower, endingChunks); ok {
			at(n).index = data
		} else if n, ok := generationOf(lower, endingOK); ok {
			// The marker's own four bytes are the world version, not a length, so presence is the
			// whole signal and the content is deliberately not consulted.
			at(n).committed = true
		}
	}
	best := Save10{}
	found := false
	for n, g := range generations {
		if !g.committed || len(g.fwl2) == 0 || len(g.db2) == 0 || len(g.index) == 0 {
			continue
		}
		if !found || n > best.Generation {
			best = Save10{Generation: n, FWL2: g.fwl2, DB2: g.db2, Index: g.index}
			found = true
		}
	}
	if !found {
		return Save10{}, errors.New("world directory has no complete save generation (need _main.N.fwl2, .db2, .chunks and .ok together)")
	}
	wanted, err := indexedChunks(best.Index)
	if err != nil {
		return Save10{}, err
	}
	best.Chunks = make(map[string][]byte, len(wanted))
	for _, name := range wanted {
		data, ok := files[name]
		if !ok {
			return Save10{}, fmt.Errorf("save generation %d names chunk %s, which is not in the world directory", best.Generation, name)
		}
		best.Chunks[name] = data
	}
	return best, nil
}

// chunkIndex is the _main.N.chunks header: ChunkSaveMapping::Save writes int16 world version, int32
// total ZDOs across every chunk, int32 number of saved chunks, then one 11-byte entry per chunk of
// uint16 ChunkIndex.Chunk, uint8 m_chunkSize, uint32 m_version, int32 m_numZDOs. Ulfsland's file is
// exactly 2+4+4+11 = 21 bytes.
type chunkIndex struct {
	version   int16
	totalZDOs int32
	names     []string
}

func parseChunkIndex(b []byte) (chunkIndex, error) {
	r := &reader{r: bytes.NewReader(b)}
	version, e := r.i16()
	if e != nil {
		return chunkIndex{}, errors.New("chunk index is truncated")
	}
	total, e := r.i32()
	if e != nil {
		return chunkIndex{}, errors.New("chunk index is truncated")
	}
	count, e := r.i32()
	if e != nil || count < 0 || count > maxChunkFiles {
		return chunkIndex{}, errors.New("invalid chunk count")
	}
	index := chunkIndex{version: version, totalZDOs: total, names: make([]string, 0, count)}
	for i := int32(0); i < count; i++ {
		chunk, e := r.u16()
		if e != nil {
			return chunkIndex{}, e
		}
		size, e := r.u8()
		if e != nil {
			return chunkIndex{}, e
		}
		chunkVersion, e := r.u32()
		if e != nil {
			return chunkIndex{}, e
		}
		if _, e = r.i32(); e != nil {
			return chunkIndex{}, e
		}
		index.names = append(index.names, chunkFileName(chunk, size, chunkVersion))
	}
	return index, nil
}

func indexedChunks(b []byte) ([]string, error) {
	index, err := parseChunkIndex(b)
	if err != nil {
		return nil, err
	}
	return index.names, nil
}

// ParseSave10 reads one 1.0 save generation into the same Snapshot the old format produces.
func ParseSave10(save Save10, catalog map[int32]string) (Snapshot, error) {
	s, err := parseDB2(save.DB2, catalog)
	if err != nil {
		return Snapshot{}, err
	}
	index, err := parseChunkIndex(save.Index)
	if err != nil {
		return Snapshot{}, err
	}
	// Sorted so a snapshot is reproducible: object ids are positional, and map iteration is not.
	names := make([]string, 0, len(save.Chunks))
	for name := range save.Chunks {
		names = append(names, name)
	}
	sort.Strings(names)
	var id uint32
	for _, name := range names {
		read, err := parseChunk(save.Chunks[name], s.WorldVersion, catalog, &s, id)
		if err != nil {
			return Snapshot{}, fmt.Errorf("chunk %s: %w", name, err)
		}
		id += read
	}
	s.Summary.Objects = int(id)
	if int32(id) != index.totalZDOs {
		s.Health.Findings = append(s.Health.Findings,
			fmt.Sprintf("chunk files hold %d objects but the save index claims %d", id, index.totalZDOs))
	}
	return s, nil
}

// parseChunk reads one <x>_<y>__<size>_<version>.chunk: ZDOMan::SaveChunk writes int16 world
// version, int32 ZDO count, then ZDO::Save per object. Returns how many objects it read so ids stay
// unique across the chunk set.
func parseChunk(b []byte, version int32, catalog map[int32]string, s *Snapshot, first uint32) (uint32, error) {
	r := &reader{r: bytes.NewReader(b)}
	chunkVersion, e := r.i16()
	if e != nil {
		return 0, errors.New("chunk is truncated")
	}
	// A chunk carries its own copy of the world version. Disagreement means the file belongs to a
	// different save than the db2 does, and its records may not have this layout.
	if int32(chunkVersion) != version {
		return 0, fmt.Errorf("chunk is world version %d but the save is %d", chunkVersion, version)
	}
	count, e := r.i32()
	if e != nil || count < 0 || count > maxObjects {
		return 0, errors.New("invalid ZDO count")
	}
	for i := int32(0); i < count; i++ {
		o, vals, e := readObject(r, version, catalog, first+uint32(i)+1)
		if e != nil {
			return 0, fmt.Errorf("ZDO %d at byte %d: %w", i, r.n, e)
		}
		s.absorbObject(o, vals, catalog)
	}
	return uint32(count), nil
}

// parseDB2 reads _main.N.db2. Everything after the ZoneSystem block - RandEventSystem's event timer,
// name, time and position, then PersistentEventSystem's Brotli-compressed JSON - is deliberately not
// read: none of it is map or inventory data, and on Ulfsland it is 40 bytes of which 39 are a zero
// float, an empty string and an empty event list.
func parseDB2(b []byte, catalog map[int32]string) (Snapshot, error) {
	r := &reader{r: bytes.NewReader(b)}
	version, e := r.i32()
	if e != nil {
		return Snapshot{}, errors.New("db2 is truncated")
	}
	if version < worldVersionCompactZDO || version > MaxWorldVersion {
		return Snapshot{}, fmt.Errorf("unsupported world version %d (supported %d-%d)", version, worldVersionCompactZDO, MaxWorldVersion)
	}
	net, e := r.f64()
	if e != nil {
		return Snapshot{}, errors.New("db2 is truncated")
	}
	packedLen, e := r.i32()
	if e != nil || packedLen < 0 || packedLen > maxArchiveMember {
		return Snapshot{}, errors.New("invalid compressed zone block length")
	}
	packed := make([]byte, packedLen)
	if e = r.read(packed); e != nil {
		return Snapshot{}, fmt.Errorf("read zone block: %w", e)
	}
	zip, e := gzip.NewReader(bytes.NewReader(packed))
	if e != nil {
		return Snapshot{}, fmt.Errorf("zone block is not gzip: %w", e)
	}
	defer zip.Close()
	plain, e := io.ReadAll(io.LimitReader(zip, maxArchiveMember+1))
	if e != nil {
		return Snapshot{}, fmt.Errorf("inflate zone block: %w", e)
	}
	if len(plain) > maxArchiveMember {
		return Snapshot{}, errors.New("zone block too large")
	}
	s := Snapshot{WorldVersion: version, NetTime: net, Summary: Summary{Categories: map[string]int{}}, Health: Health{Level: "healthy"}}
	if err := readZoneSystem(plain, catalog, &s); err != nil {
		return Snapshot{}, err
	}
	return s, nil
}

// readZoneSystem decodes the inflated ZoneSystem block, which is ZoneSystem::Save in order.
func readZoneSystem(b []byte, catalog map[int32]string, s *Snapshot) error {
	r := &reader{r: bytes.NewReader(b)}
	zones, e := r.i32()
	if e != nil || zones < 0 || zones > maxZoneCount {
		return errors.New("invalid generated zone count")
	}
	s.GeneratedZones = make([]Vec2, 0, zones)
	for i := int32(0); i < zones; i++ {
		x, e := r.i16()
		if e != nil {
			return e
		}
		y, e := r.i16()
		if e != nil {
			return e
		}
		s.GeneratedZones = append(s.GeneratedZones, Vec2{int(x), int(y)})
	}
	// PGWVersion is left at zero: 1.0 stopped writing it, and a fabricated value would make
	// reusableTerrain in the analysis path believe terrain it has never rendered is current.
	locationVersion, e := r.i32()
	if e != nil {
		return e
	}
	s.LocationVersion = locationVersion
	keys, e := r.i32()
	if e != nil || keys < 0 || keys > maxGlobalKeys {
		return errors.New("invalid global key count")
	}
	for i := int32(0); i < keys; i++ {
		v, e := r.str()
		if e != nil {
			return e
		}
		s.GlobalKeys = append(s.GlobalKeys, v)
	}
	generated, e := r.u8()
	if e != nil {
		return e
	}
	s.LocationsGenerated = generated != 0
	count, e := r.i32()
	if e != nil || count < 0 || count > maxLocations {
		return errors.New("invalid location count")
	}
	s.Locations = make([]Location, 0, count)
	for i := int32(0); i < count; i++ {
		hash, e := r.i32()
		if e != nil {
			return e
		}
		p, e := r.vec3()
		if e != nil {
			return e
		}
		placed, e := r.u8()
		if e != nil {
			return e
		}
		// The name is the prefab catalog's answer for the hash. An unresolved hash leaves the name
		// empty, which LocationCategory turns into "other" - honest, and counted below so the
		// operator sees that the catalog, not the world, is what is short.
		name := catalog[hash]
		if name == "" {
			s.Health.UnresolvedLocations++
		}
		s.Locations = append(s.Locations, Location{Name: name, Category: LocationCategory(name), Position: p, Generated: placed != 0})
	}
	if s.Health.UnresolvedLocations > 0 {
		s.Health.Findings = append(s.Health.Findings,
			fmt.Sprintf("%d of %d location prefab hashes are unresolved; 1.0 stores locations by hash, so their names need a prefab catalog", s.Health.UnresolvedLocations, count))
	}
	return nil
}
