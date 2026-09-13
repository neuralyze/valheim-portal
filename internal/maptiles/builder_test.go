package maptiles

import (
	"image"
	"image/color"
	"image/png"
	"math/rand/v2"
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

// renderWebMapImage is the whole-image form of renderWebMapRows, kept here because the
// renderer itself no longer has one: at 12288x12288 a materialised output is 576 MiB and
// the production path streams instead. Six-pixel fixtures can afford it.
func renderWebMapImage(t *testing.T, biomes, heights image.Image, size int) *image.RGBA {
	t.Helper()
	sampler, err := newImageSampler(biomes, heights, size)
	if err != nil {
		t.Fatal(err)
	}
	rendered := image.NewRGBA(image.Rect(0, 0, size, size))
	if err := renderWebMapRows(sampler, size, func(y int, row []byte) error {
		copy(rendered.Pix[y*rendered.Stride:], row)
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	return rendered
}

func TestRenderWebMapUsesNativePaletteWaterAndOrientation(t *testing.T) {
	biomes := image.NewRGBA(image.Rect(0, 0, 6, 6))
	heights := image.NewGray16(image.Rect(0, 0, 6, 6))
	for y := range 6 {
		for x := range 6 {
			biome := color.RGBA{0x91, 0xa7, 0x5b, 0xff}
			if y >= 3 {
				biome = color.RGBA{0xff, 0, 0, 0xff}
			}
			biomes.SetRGBA(x, y, biome)
			heights.SetGray16(x, y, color.Gray16{Y: uint16((512 + 50) * 4)})
		}
	}

	rendered := renderWebMapImage(t, biomes, heights, 2)
	if got, want := rendered.RGBAAt(0, 0), (color.RGBA{131, 149, 82, 255}); got != want {
		t.Fatalf("south Meadows pixel = %#v, want native WebMap %#v", got, want)
	}
	if got, want := rendered.RGBAAt(0, 1), (color.RGBA{157, 44, 44, 255}); got != want {
		t.Fatalf("north Ashlands pixel = %#v, want native WebMap %#v", got, want)
	}
}

func TestRenderWebMapUsesHeightForDeepWaterAndShore(t *testing.T) {
	biomes := image.NewRGBA(image.Rect(0, 0, 6, 6))
	heights := image.NewRGBA(image.Rect(0, 0, 6, 6))
	for y := range 6 {
		for x := range 6 {
			biomes.SetRGBA(x, y, color.RGBA{0, 0, 0x99, 0xff})
			height := 0
			if x >= 3 {
				height = 30
			}
			encoded := uint32((512 + height) * 8192)
			heights.SetRGBA(x, y, color.RGBA{R: uint8(encoded >> 16), G: uint8(encoded >> 8), B: uint8(encoded), A: 255})
		}
	}

	rendered := renderWebMapImage(t, biomes, heights, 2)
	deep := rendered.RGBAAt(0, 0)
	shore := rendered.RGBAAt(1, 0)
	if deep == shore {
		t.Fatal("deep water and shore rendered identically")
	}
	if deep.B <= deep.R || shore.R <= shore.G {
		t.Fatalf("native water palette missing: deep=%#v shore=%#v", deep, shore)
	}
}

func TestRenderWebMapRejectsNonIntegralSourceScale(t *testing.T) {
	biomes := image.NewRGBA(image.Rect(0, 0, 7, 7))
	heights := image.NewGray16(image.Rect(0, 0, 7, 7))
	if _, err := newImageSampler(biomes, heights, 2); err == nil {
		t.Fatal("non-integral source scale was accepted")
	}
}

func writeTestPNG(t *testing.T, path string, img image.Image) {
	t.Helper()
	file, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := png.Encode(file, img); err != nil {
		t.Fatal(err)
	}
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}
}

// The scanline reader replaces png.Decode on the hot path, so it has to agree with it
// pixel for pixel -- including the alpha premultiplication At().RGBA() applies, which the
// biome palette lookup and the 24-bit height encoding both depend on.
func TestPNGRowsAgreesWithStdlibDecode(t *testing.T) {
	const size = 64
	random := rand.New(rand.NewPCG(7, 7))
	cases := map[string]image.Image{
		"nrgba8":  image.NewNRGBA(image.Rect(0, 0, size, size)),
		"rgba8":   image.NewRGBA(image.Rect(0, 0, size, size)),
		"gray8":   image.NewGray(image.Rect(0, 0, size, size)),
		"gray16":  image.NewGray16(image.Rect(0, 0, size, size)),
		"rgba64":  image.NewRGBA64(image.Rect(0, 0, size, size)),
		"nrgba64": image.NewNRGBA64(image.Rect(0, 0, size, size)),
	}
	for name, target := range cases {
		t.Run(name, func(t *testing.T) {
			for y := range size {
				for x := range size {
					r, g, b, a := uint8(random.IntN(256)), uint8(random.IntN(256)), uint8(random.IntN(256)), uint8(random.IntN(256))
					switch img := target.(type) {
					case *image.NRGBA:
						img.SetNRGBA(x, y, color.NRGBA{r, g, b, a})
					case *image.RGBA:
						img.SetRGBA(x, y, color.RGBA{r, g, b, 255})
					case *image.Gray:
						img.SetGray(x, y, color.Gray{Y: r})
					case *image.Gray16:
						img.SetGray16(x, y, color.Gray16{Y: uint16(r)<<8 | uint16(g)})
					case *image.RGBA64:
						img.SetRGBA64(x, y, color.RGBA64{uint16(r) << 8, uint16(g) << 8, uint16(b) << 8, 0xffff})
					case *image.NRGBA64:
						img.SetNRGBA64(x, y, color.NRGBA64{uint16(r) << 8, uint16(g) << 8, uint16(b) << 8, uint16(a) << 8})
					}
				}
			}
			path := filepath.Join(t.TempDir(), name+".png")
			writeTestPNG(t, path, target)

			file, err := os.Open(path)
			if err != nil {
				t.Fatal(err)
			}
			decoded, err := png.Decode(file)
			file.Close()
			if err != nil {
				t.Fatal(err)
			}
			rows, err := openPNGRows(path)
			if err != nil {
				t.Fatal(err)
			}
			defer rows.Close()
			_, isGray16 := decoded.(*image.Gray16)
			if rows.gray16Source() != isGray16 {
				t.Fatalf("gray16Source()=%v, png.Decode produced %T", rows.gray16Source(), decoded)
			}
			for y := range size {
				row, err := rows.next()
				if err != nil {
					t.Fatalf("row %d: %v", y, err)
				}
				for x := range size {
					r, g, b, _ := decoded.At(x, y).RGBA()
					want := uint32(r>>8)<<16 | uint32(g>>8)<<8 | uint32(b>>8)
					if got := rows.packedRGB(row, x); got != want {
						t.Fatalf("pixel (%d,%d) packedRGB=%06x, png.Decode gives %06x", x, y, got, want)
					}
					if isGray16 {
						if got, want := rows.gray16(row, x), decoded.(*image.Gray16).Gray16At(x, y).Y; got != want {
							t.Fatalf("pixel (%d,%d) gray16=%d, want %d", x, y, got, want)
						}
					}
				}
			}
		})
	}
}

// The renderer's live set must scale with the width of the map, not its area. On
// 2026-09-12 it scaled with the area -- two decoded 12288x12288 sources plus a 12-byte
// biome sample and a float32 height per pixel, 4.4 GB of live data by the time it reached
// the render loop -- and the kernel killed the portal at anon-rss 1,010,756 kB against
// compose.yaml's mem_limit: 1g. A 1024x1024 source needs 1024*1024*24 = 25 MB on that
// shape and about 40 KB on this one, so the ceiling below separates them by a wide margin
// while staying far above any plausible allocator noise.
func TestRenderWebMapRowsHoldsBoundedMemory(t *testing.T) {
	const size = 1024
	dir := t.TempDir()
	biomes := image.NewNRGBA(image.Rect(0, 0, size, size))
	heights := image.NewNRGBA(image.Rect(0, 0, size, size))
	random := rand.New(rand.NewPCG(11, 11))
	for y := range size {
		for x := range size {
			biomes.SetNRGBA(x, y, color.NRGBA{0x91, 0xa7, 0x5b, 0xff})
			encoded := uint32(random.IntN(1 << 23))
			heights.SetNRGBA(x, y, color.NRGBA{uint8(encoded >> 16), uint8(encoded >> 8), uint8(encoded), 0xff})
		}
	}
	biomePath, heightPath := filepath.Join(dir, "biome.png"), filepath.Join(dir, "height.png")
	writeTestPNG(t, biomePath, biomes)
	writeTestPNG(t, heightPath, heights)

	sampler, err := newStreamSampler(biomePath, heightPath, size)
	if err != nil {
		t.Fatal(err)
	}
	defer sampler.Close()

	var baseline, peak runtime.MemStats
	runtime.GC()
	runtime.ReadMemStats(&baseline)
	var rows int
	if err := renderWebMapRows(sampler, size, func(y int, row []byte) error {
		rows++
		if y == size/2 {
			runtime.GC()
			runtime.ReadMemStats(&peak)
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if rows != size {
		t.Fatalf("emitted %d rows, want %d", rows, size)
	}
	live := int64(peak.HeapAlloc) - int64(baseline.HeapAlloc)
	const ceiling = 4 << 20
	if live > ceiling {
		t.Fatalf("live heap while rendering a %dx%d source grew by %d bytes, want under %d; the renderer is holding the map rather than streaming it", size, size, live, ceiling)
	}
}

// Cross-check of the streaming path against the in-memory one: the tiles on disk for the
// four worlds already published were rendered by the whole-image sampler, so the two must
// produce identical pixels or a rebuild would silently change published maps.
func TestStreamSamplerMatchesImageSampler(t *testing.T) {
	const size = 96
	dir := t.TempDir()
	biomes := image.NewNRGBA(image.Rect(0, 0, size*2, size*2))
	heights := image.NewNRGBA(image.Rect(0, 0, size*2, size*2))
	random := rand.New(rand.NewPCG(13, 13))
	palette := []color.NRGBA{{0x91, 0xa7, 0x5b, 0xff}, {0xff, 0, 0, 0xff}, {0x34, 0x5e, 0x3b, 0xff}, {1, 2, 3, 0xff}}
	for y := range size * 2 {
		for x := range size * 2 {
			biomes.SetNRGBA(x, y, palette[random.IntN(len(palette))])
			encoded := uint32(random.IntN(1 << 23))
			heights.SetNRGBA(x, y, color.NRGBA{uint8(encoded >> 16), uint8(encoded >> 8), uint8(encoded), 0xff})
		}
	}
	biomePath, heightPath := filepath.Join(dir, "biome.png"), filepath.Join(dir, "height.png")
	writeTestPNG(t, biomePath, biomes)
	writeTestPNG(t, heightPath, heights)

	want := renderWebMapImage(t, biomes, heights, size)

	sampler, err := newStreamSampler(biomePath, heightPath, size)
	if err != nil {
		t.Fatal(err)
	}
	defer sampler.Close()
	if err := renderWebMapRows(sampler, size, func(y int, row []byte) error {
		for x := range size * 4 {
			if got, expected := row[x], want.Pix[y*want.Stride+x]; got != expected {
				t.Fatalf("row %d byte %d = %d, whole-image renderer gives %d", y, x, got, expected)
			}
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
}
