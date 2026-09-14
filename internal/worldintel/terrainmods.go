package worldintel

import (
	"bytes"
	"compress/gzip"
	"encoding/base64"
	"errors"
	"fmt"
	"io"
	"math"
	"sort"
)

// Player terrain edits, which is the only place a road exists in a Valheim save.
//
// Valheim does not store roads. It stores, per 64 m zone that anybody has ever edited, one
// _TerrainCompiler ZDO carrying a single byte array under the ZDOVars key "TCData"
// (StableHash("TCData") == 1305470367, confirmed against the 154 compilers in Vangard). A road is
// therefore recovered, not read: it is the ribbon of ground whose PAINT was changed to paved or
// dirt and whose HEIGHT was levelled, and nothing in the file says "road".
//
// TCData is gzip (assembly_utils Utils::Compress is a GZipStream) around exactly what
// TerrainComp::Save writes, in this order:
//
//	int32    version, always 1 (TerrainComp.terrainCompVersion)
//	int32    m_operations - how many edits have been applied in this zone
//	float32  m_lastOpPoint x, y, z
//	float32  m_lastOpRadius
//	int32    N = len(m_modifiedHeight)
//	N times: byte modified; if modified, float32 levelDelta then float32 smoothDelta
//	int32    M = len(m_modifiedPaint)
//	M times: byte modified; if modified, float32 r, g, b, a
//
// Measured on all 154 of Vangard's compilers: version is 1, every N and M is 4225, and the reader
// consumes the inflated buffer to the last byte in every one of them. m_lastOpPoint and
// m_lastOpRadius come back as zeroes - they are live-session fields that a reloaded world has not
// set - so they are read and discarded rather than published as if they meant something.
//
// RESOLUTION. TerrainComp::Initialize sizes all four arrays pitch*pitch with
// pitch = Heightmap.m_width + 1, and indexes them y*pitch + x (TerrainComp::LevelTerrain IL_00bc,
// <PaintCleared>g__getIndex IL_0000). 4225 = 65*65, so m_width is 64; the compilers sit on a 64 m
// lattice, so m_scale is 1 metre. ONE METRE PER SAMPLE is the hard resolution limit of any road
// this can draw, and it is also why a road is drawable at all: a paved path is 2-4 m wide, so it is
// two to four samples across.
//
// GEOMETRY. Heightmap::VertexMaskToWorld(x, y) returns
// hmapPos + ((x - width/2 - 0.5) * scale, 0, (y - width/2 - 0.5) * scale), which is the LOW CORNER
// of sample (x, y); the sample's centre is half a cell further on, i.e. hmapPos + ((x - width/2) *
// scale, 0, (y - width/2) * scale), which is exactly where Heightmap::WorldToVertex sends that
// world point back. Centres are what this records, so a sample and the height vertex under it
// agree, and the mask is not shifted half a metre south-west of the ground it describes.
//
// PAINT. The four floats are a UnityEngine.Color and the channels are the paint types, from the
// constants Heightmap's static constructor installs and Heightmap::PaintCleared lerps towards:
// Dirt (1,0,0,1), Cultivated (0,1,0,1), Paved (0,0,1,1), Reset (0,0,0,1),
// ClearVegetation (0,0,0,0), DeepSnow (1,1,1,1). So r is dirt, g is cultivated, b is paved, and a
// is vegetation still standing - a is driven DOWN to clear it. DeepSnow sets all four to 1 and is
// consequently indistinguishable from dirt+cultivated+paved together; it only occurs in the Deep
// North and none of Vangard's 98,079 painted samples has r, g and b all high at once.
const (
	// terrainDataKey is ZDOVars.s_TCData.
	terrainDataKey = "TCData"
	// terrainCompilerPrefab is the prefab that owns the data. Named rather than hashed so the
	// catalog stays the single place a hash becomes a name.
	terrainCompilerPrefab = "_TerrainCompiler"
	terrainDataVersion    = 1
	// maxTerrainSamples bounds one zone's arrays. Vanilla is 4225; a mod that widens the heightmap
	// would raise it, and 64 * 4225 is well past anything a zone could hold while still refusing a
	// corrupt length that would otherwise allocate gigabytes.
	maxTerrainSamples = 4225 * 64
	// terrainHeightEpsilon is the level delta below which a sample is recorded as modified but
	// neither raised nor lowered. Valheim writes a modified flag for every sample an operation
	// touched, including the taper at the edge of the brush where the delta rounds to nothing, and
	// a road drawn from the flag alone is four metres wider than the road. Quarter of a metre is
	// under one terrain step and over the taper.
	terrainHeightEpsilon = 0.25
	// terrainPaintThreshold is where a paint channel counts as that paint. PaintCleared lerps
	// towards the pure colour, so a half-applied brush stroke sits between 0 and 1; half is the
	// point at which the ground reads as painted in game.
	terrainPaintThreshold = 0.5
)

// Terrain-modification flags, one byte per sample. Paint and height are separate bits because they
// answer different questions: paint is what the ground was turned into, height is whether it was
// moved, and a road is usually both while a levelled building platform is only the second.
const (
	TerrainDirt       = 1 << 0
	TerrainCultivated = 1 << 1
	TerrainPaved      = 1 << 2
	TerrainCleared    = 1 << 3
	TerrainHeight     = 1 << 4
	TerrainRaised     = 1 << 5
	TerrainLowered    = 1 << 6
	// TerrainRoad is not stored; it is the query the roads layer runs. Paved or dirt paint is what
	// a player road is made of - cultivated is a field and cleared vegetation is a lawn.
	TerrainRoad = TerrainDirt | TerrainPaved
)

// TerrainZone is one edited 64 m zone: the counts an operator can read at a glance, and the
// per-sample mask the map rasterises.
type TerrainZone struct {
	// X and Z are the _TerrainCompiler's own world position, which is the zone centre and always a
	// whole multiple of the 64 m zone size.
	X int32 `json:"x"`
	Z int32 `json:"z"`
	// Pitch is samples per side and Scale is metres per sample, both read out of the data rather
	// than assumed, so a modded heightmap cannot silently render at the wrong size.
	Pitch      int     `json:"pitch"`
	Scale      float32 `json:"scale"`
	Operations int32   `json:"operations"`
	Paved      int     `json:"paved,omitempty"`
	Dirt       int     `json:"dirt,omitempty"`
	Cultivated int     `json:"cultivated,omitempty"`
	Cleared    int     `json:"cleared,omitempty"`
	Raised     int     `json:"raised,omitempty"`
	Lowered    int     `json:"lowered,omitempty"`
	Modified   int     `json:"modified"`
	// Road is samples carrying paved or dirt paint, counted once even where a stretch was painted
	// dirt and then paved over: adding Paved and Dirt would count that ground twice and report a
	// road longer than the one in the world.
	Road int `json:"road,omitempty"`
	// MinDelta and MaxDelta bound the level change in metres, so "levelled a platform" and "dug a
	// 44 m pit" are distinguishable without shipping every delta.
	MinDelta float32 `json:"min_delta,omitempty"`
	MaxDelta float32 `json:"max_delta,omitempty"`
	// Mask is Pitch*Pitch flag bytes in row-major order, y*Pitch + x, run-length encoded and then
	// base64'd. Raw is 4225 bytes a zone and 650,650 for Vangard; the runs are 50,698 bytes, 7.8%
	// of raw, because an edited zone is large flat regions of one value. base64 of the runs is
	// 67.6 KB for the whole world, which is what makes a 1 m layer shippable as JSON at all.
	Mask string `json:"mask"`
}

// TerrainMods is every edited zone plus the totals.
type TerrainMods struct {
	Zones      []TerrainZone `json:"zones"`
	ZoneCount  int           `json:"zone_count"`
	Paved      int           `json:"paved"`
	Dirt       int           `json:"dirt"`
	Cultivated int           `json:"cultivated"`
	Cleared    int           `json:"cleared"`
	Modified   int           `json:"modified"`
	Raised     int           `json:"raised"`
	Lowered    int           `json:"lowered"`
	Road       int           `json:"road"`
	// RoadSquareMetres is the paved-or-dirt sample count times the area of one sample. At 1 m per
	// sample the numbers coincide, which is a coincidence of the vanilla grid and not the
	// definition, so the multiply stays.
	RoadSquareMetres float64 `json:"road_square_metres"`
}

// SampleWorld returns the world position of sample (x, y) in this zone - the sample's centre, per
// the geometry note above.
func (z TerrainZone) SampleWorld(x, y int) (float32, float32) {
	half := (z.Pitch - 1) / 2
	return float32(z.X) + float32(x-half)*z.Scale, float32(z.Z) + float32(y-half)*z.Scale
}

// Mask decodes the run-length mask back to Pitch*Pitch bytes.
func (z TerrainZone) Decode() ([]byte, error) {
	return decodeRuns(z.Mask, z.Pitch*z.Pitch)
}

// parseTerrainData decodes one TCData blob into a flag mask plus its counts. The returned zone has
// no X/Z; the caller supplies them from the owning ZDO, which is the only thing that knows where
// the compiler is.
func parseTerrainData(raw []byte) (TerrainZone, []byte, error) {
	zip, err := gzip.NewReader(bytes.NewReader(raw))
	if err != nil {
		return TerrainZone{}, nil, fmt.Errorf("TCData is not gzip: %w", err)
	}
	defer zip.Close()
	plain, err := io.ReadAll(io.LimitReader(zip, maxBlob+1))
	if err != nil {
		return TerrainZone{}, nil, fmt.Errorf("inflate TCData: %w", err)
	}
	r := &reader{r: bytes.NewReader(plain)}
	version, err := r.i32()
	if err != nil {
		return TerrainZone{}, nil, errors.New("TCData is truncated")
	}
	if version != terrainDataVersion {
		return TerrainZone{}, nil, fmt.Errorf("unsupported TCData version %d", version)
	}
	operations, err := r.i32()
	if err != nil {
		return TerrainZone{}, nil, errors.New("TCData is truncated")
	}
	// m_lastOpPoint and m_lastOpRadius: live-session fields, zero in every saved compiler measured.
	if err := r.skip(16); err != nil {
		return TerrainZone{}, nil, errors.New("TCData is truncated")
	}
	heights, err := r.i32()
	if err != nil || heights < 0 || heights > maxTerrainSamples {
		return TerrainZone{}, nil, errors.New("invalid TCData height array length")
	}
	zone := TerrainZone{Operations: operations}
	mask := make([]byte, heights)
	for index := int32(0); index < heights; index++ {
		modified, err := r.u8()
		if err != nil {
			return TerrainZone{}, nil, fmt.Errorf("TCData height %d: %w", index, err)
		}
		if modified == 0 {
			continue
		}
		level, err := r.f32()
		if err != nil {
			return TerrainZone{}, nil, fmt.Errorf("TCData level %d: %w", index, err)
		}
		if _, err := r.f32(); err != nil {
			return TerrainZone{}, nil, fmt.Errorf("TCData smooth %d: %w", index, err)
		}
		mask[index] |= TerrainHeight
		zone.Modified++
		switch {
		case level > terrainHeightEpsilon:
			mask[index] |= TerrainRaised
			zone.Raised++
		case level < -terrainHeightEpsilon:
			mask[index] |= TerrainLowered
			zone.Lowered++
		}
		zone.MinDelta = min(zone.MinDelta, level)
		zone.MaxDelta = max(zone.MaxDelta, level)
	}
	paints, err := r.i32()
	if err != nil || paints < 0 || paints > maxTerrainSamples {
		return TerrainZone{}, nil, errors.New("invalid TCData paint array length")
	}
	if paints != heights {
		// TerrainComp::Initialize allocates both at pitch*pitch, so a disagreement means the
		// record is not the layout this reads and the mask indices would not line up.
		return TerrainZone{}, nil, fmt.Errorf("TCData height array is %d but paint array is %d", heights, paints)
	}
	for index := int32(0); index < paints; index++ {
		modified, err := r.u8()
		if err != nil {
			return TerrainZone{}, nil, fmt.Errorf("TCData paint %d: %w", index, err)
		}
		if modified == 0 {
			continue
		}
		var channels [4]float32
		for channel := range channels {
			channels[channel], err = r.f32()
			if err != nil {
				return TerrainZone{}, nil, fmt.Errorf("TCData paint %d channel %d: %w", index, channel, err)
			}
		}
		if channels[0] > terrainPaintThreshold {
			mask[index] |= TerrainDirt
			zone.Dirt++
		}
		if channels[1] > terrainPaintThreshold {
			mask[index] |= TerrainCultivated
			zone.Cultivated++
		}
		if channels[2] > terrainPaintThreshold {
			mask[index] |= TerrainPaved
			zone.Paved++
		}
		if channels[3] < terrainPaintThreshold {
			mask[index] |= TerrainCleared
			zone.Cleared++
		}
	}
	for _, flags := range mask {
		if flags&TerrainRoad != 0 {
			zone.Road++
		}
	}
	side := int(math.Round(math.Sqrt(float64(heights))))
	if side*side != int(heights) || side < 2 {
		return TerrainZone{}, nil, fmt.Errorf("TCData array of %d samples is not square", heights)
	}
	zone.Pitch = side
	// Scale comes from the lattice, not from a constant: the compilers sit one zone apart and cover
	// (pitch-1) samples of ground between neighbouring centres.
	zone.Scale = float32(zoneSize) / float32(side-1)
	zone.Mask = encodeRuns(mask)
	return zone, mask, nil
}

// encodeRuns writes value/length pairs as base64. Lengths are one byte, so a run longer than 255
// becomes several pairs; that costs two bytes per 255 samples and keeps the decoder a five-line
// loop in the browser, which a varint would not.
func encodeRuns(mask []byte) string {
	runs := make([]byte, 0, len(mask)/8+2)
	for index := 0; index < len(mask); {
		end := index + 1
		for end < len(mask) && mask[end] == mask[index] && end-index < 255 {
			end++
		}
		runs = append(runs, mask[index], byte(end-index))
		index = end
	}
	return base64.StdEncoding.EncodeToString(runs)
}

func decodeRuns(encoded string, size int) ([]byte, error) {
	runs, err := base64.StdEncoding.DecodeString(encoded)
	if err != nil {
		return nil, err
	}
	if len(runs)%2 != 0 {
		return nil, errors.New("run-length mask has a dangling pair")
	}
	mask := make([]byte, 0, size)
	for index := 0; index < len(runs); index += 2 {
		if len(mask)+int(runs[index+1]) > size {
			return nil, errors.New("run-length mask overruns the grid")
		}
		for repeat := byte(0); repeat < runs[index+1]; repeat++ {
			mask = append(mask, runs[index])
		}
	}
	if len(mask) != size {
		return nil, fmt.Errorf("run-length mask decodes to %d of %d samples", len(mask), size)
	}
	return mask, nil
}

// TerrainModsFromZones orders a zone set and totals it. Exported because the players' map clips the
// zone list to the ground its players have visited and then has to report the totals for THAT set:
// carrying the whole world's road area beside a fogged map would advertise roads nobody has found.
func TerrainModsFromZones(zones []TerrainZone) *TerrainMods {
	return aggregateTerrainMods(zones)
}

// aggregateTerrainMods orders the zones and totals them. Sorted by position so the same world
// always serialises identically - the overlay pyramid is cached on the save hash, and a reordered
// array would churn every tile ETag for no change in content.
func aggregateTerrainMods(zones []TerrainZone) *TerrainMods {
	if len(zones) == 0 {
		return nil
	}
	sort.Slice(zones, func(left, right int) bool {
		if zones[left].X != zones[right].X {
			return zones[left].X < zones[right].X
		}
		return zones[left].Z < zones[right].Z
	})
	mods := &TerrainMods{Zones: zones, ZoneCount: len(zones)}
	for _, zone := range zones {
		mods.Paved += zone.Paved
		mods.Dirt += zone.Dirt
		mods.Cultivated += zone.Cultivated
		mods.Cleared += zone.Cleared
		mods.Modified += zone.Modified
		mods.Raised += zone.Raised
		mods.Lowered += zone.Lowered
		mods.Road += zone.Road
		area := float64(zone.Scale) * float64(zone.Scale)
		mods.RoadSquareMetres += float64(zone.Road) * area
	}
	return mods
}
