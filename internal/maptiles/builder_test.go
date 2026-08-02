package maptiles

import (
	"image"
	"image/color"
	"testing"
)

func TestRenderWebMapUsesNativePaletteWaterAndOrientation(t *testing.T) {
	biomes := image.NewRGBA(image.Rect(0, 0, 6, 6))
	heights := image.NewGray16(image.Rect(0, 0, 6, 6))
	for y := 0; y < 6; y++ {
		for x := 0; x < 6; x++ {
			biome := color.RGBA{0x91, 0xa7, 0x5b, 0xff}
			if y >= 3 {
				biome = color.RGBA{0xff, 0, 0, 0xff}
			}
			biomes.SetRGBA(x, y, biome)
			heights.SetGray16(x, y, color.Gray16{Y: uint16((512 + 50) * 4)})
		}
	}

	rendered, err := renderWebMap(biomes, heights, 2)
	if err != nil {
		t.Fatal(err)
	}
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
	for y := 0; y < 6; y++ {
		for x := 0; x < 6; x++ {
			biomes.SetRGBA(x, y, color.RGBA{0, 0, 0x99, 0xff})
			height := 0
			if x >= 3 {
				height = 30
			}
			encoded := uint32((512 + height) * 8192)
			heights.SetRGBA(x, y, color.RGBA{R: uint8(encoded >> 16), G: uint8(encoded >> 8), B: uint8(encoded), A: 255})
		}
	}

	rendered, err := renderWebMap(biomes, heights, 2)
	if err != nil {
		t.Fatal(err)
	}
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
	if _, err := renderWebMap(biomes, heights, 2); err == nil {
		t.Fatal("non-integral source scale was accepted")
	}
}
