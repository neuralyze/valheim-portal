package worldintel

import (
	"bytes"
	"compress/gzip"
	"encoding/binary"
	"math"
	"testing"
)

// writeTerrainData builds a TCData blob exactly the way TerrainComp::Save does, so the decoder is
// tested against the game's byte layout rather than against itself. paint is indexed the same way
// the game indexes it, y*pitch + x.
func writeTerrainData(t *testing.T, pitch int, operations int32, level map[int]float32, paint map[int][4]float32) []byte {
	t.Helper()
	samples := pitch * pitch
	var body bytes.Buffer
	put32 := func(v int32) { _ = binary.Write(&body, binary.LittleEndian, v) }
	putF := func(v float32) { _ = binary.Write(&body, binary.LittleEndian, v) }
	put32(1)
	put32(operations)
	putF(0)
	putF(0)
	putF(0)
	putF(0)
	put32(int32(samples))
	for index := 0; index < samples; index++ {
		delta, modified := level[index]
		if !modified {
			body.WriteByte(0)
			continue
		}
		body.WriteByte(1)
		putF(delta)
		putF(0)
	}
	put32(int32(samples))
	for index := 0; index < samples; index++ {
		colour, modified := paint[index]
		if !modified {
			body.WriteByte(0)
			continue
		}
		body.WriteByte(1)
		for _, channel := range colour {
			putF(channel)
		}
	}
	var packed bytes.Buffer
	zip := gzip.NewWriter(&packed)
	if _, err := zip.Write(body.Bytes()); err != nil {
		t.Fatal(err)
	}
	if err := zip.Close(); err != nil {
		t.Fatal(err)
	}
	return packed.Bytes()
}

// The roads layer stands entirely on this decode, and the four paint channels are the one place a
// silent swap would be invisible: the map would still draw a road, just the wrong KIND everywhere.
// Heightmap's static constructor sets Dirt (1,0,0,1), Cultivated (0,1,0,1), Paved (0,0,1,1) and
// ClearVegetation (0,0,0,0), so each channel is asserted on its own sample.
func TestTerrainDataDecodesEachPaintChannelToItsOwnFlag(t *testing.T) {
	const pitch = 65
	paint := map[int][4]float32{
		0:  {1, 0, 0, 1}, // dirt
		1:  {0, 1, 0, 1}, // cultivated
		2:  {0, 0, 1, 1}, // paved
		3:  {0, 0, 0, 0}, // vegetation cleared
		4:  {0, 0, 0, 1}, // reset: modified, but no paint and vegetation intact
		64: {0, 0, 0.49, 1},
	}
	level := map[int]float32{
		10: 2.5,   // raised
		11: -3.25, // lowered
		12: 0.1,   // modified, under the epsilon: neither raised nor lowered
	}
	zone, mask, err := parseTerrainData(writeTerrainData(t, pitch, 42, level, paint))
	if err != nil {
		t.Fatal(err)
	}
	if zone.Pitch != pitch {
		t.Fatalf("pitch = %d, want %d", zone.Pitch, pitch)
	}
	if zone.Scale != 1 {
		t.Fatalf("scale = %v, want 1 metre per sample for a 65-sample zone on a 64 m lattice", zone.Scale)
	}
	if zone.Operations != 42 {
		t.Fatalf("operations = %d, want 42", zone.Operations)
	}
	for _, want := range []struct {
		index int
		flags byte
		label string
	}{
		{0, TerrainDirt, "dirt"},
		{1, TerrainCultivated, "cultivated"},
		{2, TerrainPaved, "paved"},
		{3, TerrainCleared, "vegetation cleared"},
		{4, 0, "reset"},
		{64, 0, "a half-applied paved brush below the threshold"},
		{10, TerrainHeight | TerrainRaised, "raised"},
		{11, TerrainHeight | TerrainLowered, "lowered"},
		{12, TerrainHeight, "modified under the height epsilon"},
	} {
		if mask[want.index] != want.flags {
			t.Errorf("%s sample %d decoded flags %d, want %d", want.label, want.index, mask[want.index], want.flags)
		}
	}
	if zone.Dirt != 1 || zone.Cultivated != 1 || zone.Paved != 1 || zone.Cleared != 1 {
		t.Errorf("paint counts dirt=%d cultivated=%d paved=%d cleared=%d, want 1 each", zone.Dirt, zone.Cultivated, zone.Paved, zone.Cleared)
	}
	// Road is paved-or-dirt counted once. Cultivated ground is a field and cleared ground is a lawn,
	// and counting either as road is how a farm becomes a motorway.
	if zone.Road != 2 {
		t.Errorf("road samples = %d, want 2 (one dirt, one paved)", zone.Road)
	}
	if zone.Raised != 1 || zone.Lowered != 1 || zone.Modified != 3 {
		t.Errorf("height counts raised=%d lowered=%d modified=%d, want 1, 1, 3", zone.Raised, zone.Lowered, zone.Modified)
	}
	if zone.MinDelta != -3.25 || zone.MaxDelta != 2.5 {
		t.Errorf("delta range = [%v, %v], want [-3.25, 2.5]", zone.MinDelta, zone.MaxDelta)
	}
}

// The classic failure this repo has already been bitten by: a transform that is right at the origin
// and wrong at the edge. Heightmap::VertexMaskToWorld puts sample (x, y) at
// hmapPos + ((x - width/2 - 0.5) * scale, 0, (y - width/2 - 0.5) * scale), whose cell CENTRE - what
// SampleWorld returns - is hmapPos + ((x - width/2) * scale, ...). So a zone's samples span exactly
// the 64 m centred on the compiler, and sample 32 sits on the compiler itself.
func TestSampleWorldCoversTheZoneAtTheOriginAndAtTheEdge(t *testing.T) {
	for _, zone := range []TerrainZone{
		{X: 0, Z: 0, Pitch: 65, Scale: 1},
		{X: -2496, Z: -1472, Pitch: 65, Scale: 1},
		{X: 9984, Z: -9984, Pitch: 65, Scale: 1},
	} {
		centreX, centreZ := zone.SampleWorld(32, 32)
		if centreX != float32(zone.X) || centreZ != float32(zone.Z) {
			t.Errorf("zone (%d, %d): the middle sample is at (%v, %v), want the compiler's own position",
				zone.X, zone.Z, centreX, centreZ)
		}
		lowX, lowZ := zone.SampleWorld(0, 0)
		highX, highZ := zone.SampleWorld(64, 64)
		if lowX != float32(zone.X)-32 || lowZ != float32(zone.Z)-32 {
			t.Errorf("zone (%d, %d): first sample at (%v, %v), want 32 m south-west of the centre", zone.X, zone.Z, lowX, lowZ)
		}
		if highX != float32(zone.X)+32 || highZ != float32(zone.Z)+32 {
			t.Errorf("zone (%d, %d): last sample at (%v, %v), want 32 m north-east of the centre", zone.X, zone.Z, highX, highZ)
		}
		if highX-lowX != float32(zoneSize) || highZ-lowZ != float32(zoneSize) {
			t.Errorf("zone (%d, %d) spans %v x %v metres, want %v", zone.X, zone.Z, highX-lowX, highZ-lowZ, zoneSize)
		}
	}
}

// The mask ships as base64 run-length pairs with a one-byte length, so a run longer than 255 has to
// split. An off-by-one there would truncate or overrun every large flat region - which is most of an
// edited zone - and the browser draws whatever it is handed.
func TestRunLengthMaskRoundTripsRunsLongerThanAByte(t *testing.T) {
	mask := make([]byte, 65*65)
	for index := range mask {
		switch {
		case index < 600:
			mask[index] = TerrainPaved
		case index == 700:
			mask[index] = TerrainDirt | TerrainHeight
		}
	}
	zone := TerrainZone{Pitch: 65, Mask: encodeRuns(mask)}
	decoded, err := zone.Decode()
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(decoded, mask) {
		t.Fatal("run-length mask did not round trip")
	}
	// The encoding has to be a real saving or the layer is not shippable as JSON: measured on
	// Vangard the runs are 7.8% of raw.
	if len(zone.Mask) >= len(mask) {
		t.Fatalf("encoded mask is %d bytes for %d samples, which is no better than raw", len(zone.Mask), len(mask))
	}
}

func TestRunLengthMaskRejectsAMaskThatDoesNotFillTheGrid(t *testing.T) {
	short := TerrainZone{Pitch: 65, Mask: encodeRuns(make([]byte, 10))}
	if _, err := short.Decode(); err == nil {
		t.Fatal("a mask covering 10 of 4225 samples decoded without error; the browser would draw 4215 samples of whatever was in memory")
	}
}

func TestTerrainDataRejectsMismatchedArrays(t *testing.T) {
	// TerrainComp::Initialize allocates the height and paint arrays at the same pitch*pitch, so a
	// disagreement means this is not the record layout being read and every mask index is suspect.
	var body bytes.Buffer
	put32 := func(v int32) { _ = binary.Write(&body, binary.LittleEndian, v) }
	put32(1)
	put32(0)
	for range 4 {
		_ = binary.Write(&body, binary.LittleEndian, float32(0))
	}
	put32(4)
	body.Write([]byte{0, 0, 0, 0})
	put32(9)
	body.Write([]byte{0, 0, 0, 0, 0, 0, 0, 0, 0})
	var packed bytes.Buffer
	zip := gzip.NewWriter(&packed)
	_, _ = zip.Write(body.Bytes())
	_ = zip.Close()
	if _, _, err := parseTerrainData(packed.Bytes()); err == nil {
		t.Fatal("a 4-sample height array beside a 9-sample paint array parsed without error")
	}
}

// Totals must come from the zones present, because the players' map hands a clipped set of zones to
// the same aggregator and then prints its numbers beside a fogged map.
func TestTerrainModsFromZonesTotalsOnlyTheZonesGiven(t *testing.T) {
	mods := TerrainModsFromZones([]TerrainZone{
		{X: 640, Z: 0, Pitch: 65, Scale: 1, Paved: 100, Road: 100, Modified: 120, Lowered: 40},
		{X: -64, Z: 128, Pitch: 65, Scale: 1, Dirt: 30, Road: 30, Modified: 30, Raised: 30},
	})
	if mods.ZoneCount != 2 {
		t.Fatalf("zone count = %d, want 2", mods.ZoneCount)
	}
	if mods.Road != 130 || mods.RoadSquareMetres != 130 {
		t.Fatalf("road = %d samples / %v m2, want 130 and 130 at one metre per sample", mods.Road, mods.RoadSquareMetres)
	}
	if mods.Raised != 30 || mods.Lowered != 40 || mods.Modified != 150 {
		t.Fatalf("height totals raised=%d lowered=%d modified=%d", mods.Raised, mods.Lowered, mods.Modified)
	}
	// Sorted west to east so the same world always serialises identically; the overlay pyramid is
	// cached on the save hash and a reordered array would churn every tile ETag.
	if mods.Zones[0].X != -64 {
		t.Fatalf("zones are not sorted by position: first is x=%d", mods.Zones[0].X)
	}
	if TerrainModsFromZones(nil) != nil {
		t.Fatal("a world nobody has edited must produce no terrain layer at all, not an empty one")
	}
}

// A zone whose scale is not one metre has to report its own area, or a modded heightmap would be
// measured on the vanilla grid. The lattice is what fixes the scale: (pitch-1) samples span one
// 64 m zone.
func TestTerrainScaleComesFromTheLatticeNotAConstant(t *testing.T) {
	const pitch = 33
	zone, _, err := parseTerrainData(writeTerrainData(t, pitch, 1, nil, map[int][4]float32{0: {0, 0, 1, 1}}))
	if err != nil {
		t.Fatal(err)
	}
	if math.Abs(float64(zone.Scale)-2) > 1e-6 {
		t.Fatalf("a %d-sample zone reported %v metres per sample, want 2", pitch, zone.Scale)
	}
	mods := TerrainModsFromZones([]TerrainZone{zone})
	if mods.RoadSquareMetres != 4 {
		t.Fatalf("one 2 m sample of paved ground measured %v m2, want 4", mods.RoadSquareMetres)
	}
}
