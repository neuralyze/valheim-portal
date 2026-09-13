package worldintel

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"encoding/binary"
	"io"
	"os"
	"path/filepath"
	"testing"
)

// ulfslandFixture is the real 1.0.12 world directory, tarred exactly the way
// hostops/backup_valheim_world.sh tars one: `tar czf ARCHIVE -C <worlds_local> Ulfsland`, so the
// members are Ulfsland/ plus the five files Valheim itself wrote. It was copied off the deployment
// host on 2026-09-12 after the server had been stopped, so the directory is quiesced and the save
// is complete - generation 2, with its _main.2.ok marker present.
//
// Nothing is trimmed out of it. Four of the five files total 1,673 bytes, and the fifth,
// _main.2.db2, is the file under test: its 145,402 bytes are a gzip stream holding 12,228 location
// records at 17 bytes each. Removing any file would make the save incomplete and would stop
// exercising the generation-selection and chunk-index paths, which are the actual 1.0 change.
const ulfslandFixture = "testdata/world-Ulfsland-1.0.12-20260912-200308.tgz"

// TestAnalyzeArchiveReadsRealValheim10World is the whole 1.0 contract against real bytes. Every
// expected value here was measured by hand off the files before any of this code existed.
func TestAnalyzeArchiveReadsRealValheim10World(t *testing.T) {
	snapshot, err := AnalyzeArchive(ulfslandFixture, "Ulfsland", knownCatalog())
	if err != nil {
		t.Fatal(err)
	}
	if snapshot.WorldVersion != 41 {
		t.Fatalf("world version=%d, the db2 header holds 0x29", snapshot.WorldVersion)
	}
	// The seed name comes out of _main.2.fwl2, which proves the .fwl decoder reads the 1.0
	// container unchanged.
	if snapshot.Seed != "8JiFcknsJd" {
		t.Fatalf("seed=%q", snapshot.Seed)
	}
	if snapshot.NetTime != 2040 {
		t.Fatalf("net time=%v", snapshot.NetTime)
	}
	if snapshot.Source.FWLBytes != 144 {
		t.Fatalf("fwl bytes=%d", snapshot.Source.FWLBytes)
	}
	// db2 + .chunks + the one chunk file: 145402 + 21 + 1504.
	if snapshot.Source.DBBytes != 146927 {
		t.Fatalf("db bytes=%d", snapshot.Source.DBBytes)
	}
	// Every generated zone on this world is in the 9x9 block around the sentinel zone, which is
	// where Valheim parks global objects. Zones are Vector2s in 1.0, so a parser still reading
	// Vector2i would produce 40 garbage pairs instead of 81 real ones.
	if len(snapshot.GeneratedZones) != 81 {
		t.Fatalf("zones=%d", len(snapshot.GeneratedZones))
	}
	for _, zone := range snapshot.GeneratedZones {
		if zone.X < 15621 || zone.X > 15629 || zone.Y < 15621 || zone.Y > 15629 {
			t.Fatalf("zone %v is outside the sentinel block the save actually holds", zone)
		}
	}
	if snapshot.Summary.SentinelZones != 81 || snapshot.Summary.ExploredZones != 0 {
		t.Fatalf("sentinel=%d explored=%d", snapshot.Summary.SentinelZones, snapshot.Summary.ExploredZones)
	}
	// m_pgwVersion is not in the 1.0 save at all, and must stay zero rather than borrow the
	// location version: reusableTerrain keys cached terrain on it.
	if snapshot.PGWVersion != 0 || snapshot.LocationVersion != 32 {
		t.Fatalf("pgw=%d locationVersion=%d", snapshot.PGWVersion, snapshot.LocationVersion)
	}
	if !snapshot.LocationsGenerated || len(snapshot.GlobalKeys) != 0 {
		t.Fatalf("locationsGenerated=%v keys=%v", snapshot.LocationsGenerated, snapshot.GlobalKeys)
	}
	if len(snapshot.Locations) != 12228 {
		t.Fatalf("locations=%d", len(snapshot.Locations))
	}
	// The ZDOs live in 00_00__0_1.chunk, not in the db2, so a non-zero count here is the proof
	// that the chunk index was read and the chunk was parsed.
	if snapshot.Summary.Objects != 83 {
		t.Fatalf("objects=%d", snapshot.Summary.Objects)
	}
	if snapshot.Summary.Persistent != 83 {
		t.Fatalf("persistent=%d; every record in this chunk has flag 256", snapshot.Summary.Persistent)
	}
}

// TestParseSave10ResolvesPrefabHashesWithUnchangedHashing is acceptance for the classifier
// guarantee: 1.0 did not change StableHash or the prefab field, so the same hash function that was
// verified against Hrafnheim's 1,780,660-entry catalog still names 1.0 objects. _ZoneCtrl and
// Leviathan are the only two prefabs in this world, and both resolve from their name alone.
func TestParseSave10ResolvesPrefabHashesWithUnchangedHashing(t *testing.T) {
	catalog := map[int32]string{
		StableHash("_ZoneCtrl"): "_ZoneCtrl",
		StableHash("Leviathan"): "Leviathan",
	}
	snapshot, err := AnalyzeArchive(ulfslandFixture, "Ulfsland", catalog)
	if err != nil {
		t.Fatal(err)
	}
	if snapshot.Health.UnknownPrefabs != 0 {
		t.Fatalf("unknown prefabs=%d; the two names in this catalog should cover all 83 records", snapshot.Health.UnknownPrefabs)
	}
	if snapshot.Summary.Categories["world"] != 83 {
		t.Fatalf("categories=%v", snapshot.Summary.Categories)
	}
	// 1.0 stores a location by hash instead of by name, so an incomplete catalog leaves names
	// empty. That has to be counted rather than hidden: an unnamed location cannot be classified,
	// and a map that quietly labelled all 12,228 of them "other" would be lying about coverage.
	if snapshot.Health.UnresolvedLocations != 12228 {
		t.Fatalf("unresolved locations=%d", snapshot.Health.UnresolvedLocations)
	}
}

// TestCatalogNamesUnderscorePrefabsAndBundlePaths pins the two catalog sources 1.0 needs. A
// SoftReferenceableAssets manifest names assets by path, and the internal prefabs start with an
// underscore, so neither resolved before: Ulfsland's 81 zone controllers all counted as unknown.
func TestCatalogNamesUnderscorePrefabsAndBundlePaths(t *testing.T) {
	dir := t.TempDir()
	manifest := filepath.Join(dir, "manifest_extended")
	// The literal shape of a SoftRef manifest entry, copied from Ulfsland's own file at offset
	// 1372698.
	body := "- asset ID: 69b465453d2674c898b8b7449ebdd86b\r\n  bundle: c4210710\r\n" +
		"  path in bundle: Assets/Systems/_ZoneCtrl.prefab\r\n"
	if err := os.WriteFile(manifest, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	catalog := CatalogFromFiles(manifest)
	if got := catalog[StableHash("_ZoneCtrl")]; got != "_ZoneCtrl" {
		t.Fatalf("_ZoneCtrl resolved to %q; the manifest has no extension and the token carries .prefab", got)
	}
}

// TestAnalyzeArchiveStillReadsOldFormatPair is the control. The four worlds on this host are still
// 0.220 and are the rollback path, so the old two-member archive has to keep routing to ParseDB and
// producing the same snapshot it always did.
func TestAnalyzeArchiveStillReadsOldFormatPair(t *testing.T) {
	path := filepath.Join(t.TempDir(), "world-Midgard-control.tgz")
	writeArchive(t, path, map[string][]byte{
		"Midgard.db":  version37Fixture(t),
		"Midgard.fwl": oldFormatFWL(t),
	})
	snapshot, err := AnalyzeArchive(path, "Midgard", knownCatalog())
	if err != nil {
		t.Fatal(err)
	}
	if snapshot.WorldVersion != 37 || snapshot.Seed != "qmrbecQI2K" {
		t.Fatalf("version=%d seed=%q", snapshot.WorldVersion, snapshot.Seed)
	}
	if len(snapshot.Objects) != 1 || snapshot.Objects[0].Category != "portal" {
		t.Fatalf("objects=%#v", snapshot.Objects)
	}
	if snapshot.Objects[0].Position != (Vec3{X: 128, Y: 40, Z: -192}) {
		t.Fatalf("position=%#v; the 0.220 record still has its 4-byte sector index", snapshot.Objects[0].Position)
	}
	if len(snapshot.GeneratedZones) != 1 || snapshot.GeneratedZones[0] != (Vec2{2, -3}) {
		t.Fatalf("zones=%#v; 0.220 zones are Vector2i", snapshot.GeneratedZones)
	}
	if snapshot.Locations[0].Name != "StartTemple" {
		t.Fatalf("locations=%#v; 0.220 stores the location name as a string", snapshot.Locations)
	}
	if snapshot.Health.UnresolvedLocations != 0 {
		t.Fatalf("unresolved locations=%d; the old format has nothing to resolve", snapshot.Health.UnresolvedLocations)
	}
}

// TestReadObjectKeepsRotatedOldFormatRecordsAligned is the regression that the version gate has to
// hold: a 0.220 record with flag 4096 carries a 12-byte Vector3 rotation, and reading it as 1.0's
// 2-byte quantised form would desync the stream for every object after it.
func TestReadObjectKeepsRotatedOldFormatRecordsAligned(t *testing.T) {
	var b bytes.Buffer
	for _, value := range []any{int32(37), float64(0), int64(0), uint32(0), int32(2)} {
		_ = binary.Write(&b, binary.LittleEndian, value)
	}
	for index := range 2 {
		_ = binary.Write(&b, binary.LittleEndian, uint16(256|flagRotation))
		_ = binary.Write(&b, binary.LittleEndian, int16(0))
		_ = binary.Write(&b, binary.LittleEndian, int16(0))
		writeVec3(&b, float32(index), 10, float32(index))
		_ = binary.Write(&b, binary.LittleEndian, StableHash("piece_portal"))
		writeVec3(&b, 0, 90, 0)
	}
	for _, value := range []int32{0, 1, 31, 0} {
		_ = binary.Write(&b, binary.LittleEndian, value)
	}
	b.WriteByte(1)
	_ = binary.Write(&b, binary.LittleEndian, int32(0))

	snapshot, err := ParseDB(bytes.NewReader(b.Bytes()), knownCatalog())
	if err != nil {
		t.Fatal(err)
	}
	if len(snapshot.Objects) != 2 {
		t.Fatalf("objects=%#v", snapshot.Objects)
	}
	if snapshot.Objects[1].Position != (Vec3{X: 1, Y: 10, Z: 1}) {
		t.Fatalf("second object at %#v; the first record's 12-byte rotation was mis-sized", snapshot.Objects[1].Position)
	}
}

func TestParseDBRefusesDirectoryEraWorldVersion(t *testing.T) {
	data := version37Fixture(t)
	binary.LittleEndian.PutUint32(data, uint32(worldVersionCompactZDO))
	// A version-40 save keeps its ZDOs in chunk files, so this header is followed by the
	// ZoneSystem block, not by objects. Parsing it as a .db would read zone data as positions.
	if _, err := ParseDB(bytes.NewReader(data), knownCatalog()); err == nil {
		t.Fatal("ParseDB accepted a world version that is never stored as a .db")
	}
}

func TestCollectSave10RequiresACommittedGeneration(t *testing.T) {
	real := worldDirectoryMembers(t)
	newest, err := CollectSave10(real)
	if err != nil {
		t.Fatal(err)
	}
	if newest.Generation != 2 {
		t.Fatalf("generation=%d", newest.Generation)
	}

	// The .ok marker is written last, so a set without one is a save that was interrupted. Reading
	// it would silently report a half-written world as the current state of the map.
	torn := map[string][]byte{}
	for name, data := range real {
		if name != "_main.2.ok" {
			torn[name] = data
		}
	}
	if _, err := CollectSave10(torn); err == nil {
		t.Fatal("accepted a save generation with no .ok marker")
	}

	// A world directory whose only contents are the game's own incomplete auto-backup is not a
	// readable world either; it is the shape that sits in worlds_local beside the live save.
	if _, err := CollectSave10(map[string][]byte{"_main.0.fwl2": real["_main.2.fwl2"]}); err == nil {
		t.Fatal("accepted a generation with metadata but no database")
	}
}

// TestCollectSave10FollowsTheChunkIndex is why the index exists. Chunk files outlive a generation:
// measured on Ulfsland, the save rolled from generation 1 to 2 and 00_00__0_1.chunk was not
// rewritten, so a parser that globbed *.chunk would also pick up chunks that a later save has
// orphaned. The index is the only statement of which files are current.
func TestCollectSave10FollowsTheChunkIndex(t *testing.T) {
	members := worldDirectoryMembers(t)
	members["01_ff__0_7.chunk"] = []byte("orphaned by a later save")
	save, err := CollectSave10(members)
	if err != nil {
		t.Fatal(err)
	}
	if len(save.Chunks) != 1 {
		t.Fatalf("chunks=%d", len(save.Chunks))
	}
	if _, ok := save.Chunks["00_00__0_1.chunk"]; !ok {
		t.Fatal("the chunk the index names was not collected")
	}

	// The other direction: an index naming a file the directory does not hold means objects are
	// missing, and a map drawn from it would look complete while being short a whole zone.
	missing := worldDirectoryMembers(t)
	delete(missing, "00_00__0_1.chunk")
	if _, err := CollectSave10(missing); err == nil {
		t.Fatal("accepted a save whose index names a chunk file that is not present")
	}
}

func worldDirectoryMembers(t *testing.T) map[string][]byte {
	t.Helper()
	f, err := os.Open(ulfslandFixture)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	zip, err := gzip.NewReader(f)
	if err != nil {
		t.Fatal(err)
	}
	defer zip.Close()
	out := map[string][]byte{}
	reader := tar.NewReader(zip)
	for {
		header, err := reader.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			t.Fatal(err)
		}
		if header.Typeflag != tar.TypeReg {
			continue
		}
		data, err := io.ReadAll(reader)
		if err != nil {
			t.Fatal(err)
		}
		out[filepath.Base(header.Name)] = data
	}
	return out
}

func writeArchive(t *testing.T, path string, members map[string][]byte) {
	t.Helper()
	f, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	zip := gzip.NewWriter(f)
	writer := tar.NewWriter(zip)
	for name, data := range members {
		if err := writer.WriteHeader(&tar.Header{Name: name, Mode: 0o644, Size: int64(len(data)), Typeflag: tar.TypeReg}); err != nil {
			t.Fatal(err)
		}
		if _, err := writer.Write(data); err != nil {
			t.Fatal(err)
		}
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	if err := zip.Close(); err != nil {
		t.Fatal(err)
	}
}

// oldFormatFWL is Hrafnheim's real .fwl, the bytes read off the deployment host: int32 declared
// length 0x2e, int32 world version 0x25, then "Hrafnheim" and seed name "qmrbecQI2K".
func oldFormatFWL(t *testing.T) []byte {
	t.Helper()
	var b bytes.Buffer
	_ = binary.Write(&b, binary.LittleEndian, int32(37))
	writeString(&b, "Hrafnheim")
	writeString(&b, "qmrbecQI2K")
	for _, value := range []int32{-1926674633, -2018127128, -1, 2} {
		_ = binary.Write(&b, binary.LittleEndian, value)
	}
	b.WriteByte(1)
	_ = binary.Write(&b, binary.LittleEndian, int32(0))
	payload := b.Bytes()
	out := make([]byte, 0, len(payload)+4)
	out = binary.LittleEndian.AppendUint32(out, uint32(len(payload)))
	return append(out, payload...)
}
