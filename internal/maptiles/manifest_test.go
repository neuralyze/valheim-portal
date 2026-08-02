package maptiles

import (
	"context"
	"image"
	"image/color"
	"image/png"
	"os"
	"path/filepath"
	"testing"
)

func TestPyramidMeetsMinimumResolution(t *testing.T) {
	levels := pyramidLevels(MinimumSize)
	if got := levels[len(levels)-1].Width; got != 12288 {
		t.Fatalf("maximum level width = %d, want 12288", got)
	}
	if got := levels[len(levels)-1].TilesWide; got != 24 {
		t.Fatalf("maximum level tiles = %d, want 24", got)
	}
	for index, level := range levels {
		if level.Zoom != index || level.Width != level.Height {
			t.Fatalf("invalid level %d: %#v", index, level)
		}
	}
}

func TestDefaultPyramidMatchesRequestedResolution(t *testing.T) {
	levels := pyramidLevels(DefaultSize)
	maximum := levels[len(levels)-1]
	if maximum.Width != 12288 || maximum.Height != 12288 {
		t.Fatalf("default maximum level dimensions = %dx%d, want 12288x12288", maximum.Width, maximum.Height)
	}
	if maximum.TilesWide != 24 || maximum.TilesHigh != 24 {
		t.Fatalf("default maximum level tiles = %dx%d, want 24x24", maximum.TilesWide, maximum.TilesHigh)
	}
}

func TestBuildRejectsUndersizedRaster(t *testing.T) {
	_, err := Build(context.Background(), t.TempDir(), "unused.png", BuildOptions{World: "Asgard", Seed: "seed", WorldGenVersion: 37, Size: MinimumSize - 1})
	if err == nil {
		t.Fatal("undersized raster was accepted")
	}
}

func TestBuildRejectsUpscaledSourceRaster(t *testing.T) {
	root := t.TempDir()
	source := filepath.Join(root, "source.png")
	height := filepath.Join(root, "height.png")
	for _, fixture := range []struct {
		path  string
		image image.Image
	}{
		{source, image.NewRGBA(image.Rect(0, 0, 64, 64))},
		{height, image.NewGray16(image.Rect(0, 0, 64, 64))},
	} {
		file, err := os.Create(fixture.path)
		if err != nil {
			t.Fatal(err)
		}
		if err := png.Encode(file, fixture.image); err != nil {
			t.Fatal(err)
		}
		if err := file.Close(); err != nil {
			t.Fatal(err)
		}
	}
	_, err := Build(context.Background(), root, source, BuildOptions{
		World: "Asgard", Seed: "seed", WorldGenVersion: 37, Size: MinimumSize, HeightPath: height,
	})
	if err == nil {
		t.Fatal("undersized source raster was upscaled")
	}
}

func TestBuildPublishesImmutableDeterministicPyramid(t *testing.T) {
	root := t.TempDir()
	source := filepath.Join(root, "source.png")
	file, err := os.Create(source)
	if err != nil {
		t.Fatal(err)
	}
	imageSource := image.NewRGBA(image.Rect(0, 0, MinimumSize, MinimumSize))
	imageSource.SetRGBA(0, 0, color.RGBA{0x91, 0xa7, 0x5b, 0xff})
	imageSource.SetRGBA(1, 0, color.RGBA{0x34, 0x5e, 0x3b, 0xff})
	imageSource.SetRGBA(0, 1, color.RGBA{0, 0, 0x99, 0xff})
	imageSource.SetRGBA(1, 1, color.RGBA{0xff, 0, 0, 0xff})
	if err := png.Encode(file, imageSource); err != nil {
		t.Fatal(err)
	}
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}
	height := filepath.Join(root, "height.png")
	heightFile, err := os.Create(height)
	if err != nil {
		t.Fatal(err)
	}
	heightSource := image.NewGray16(image.Rect(0, 0, MinimumSize, MinimumSize))
	heightSource.SetGray16(0, 0, color.Gray16{Y: uint16((512 - 10) * 4)})
	heightSource.SetGray16(1, 0, color.Gray16{Y: uint16((512 + 20) * 4)})
	heightSource.SetGray16(0, 1, color.Gray16{Y: uint16((512 + 40) * 4)})
	heightSource.SetGray16(1, 1, color.Gray16{Y: uint16((512 + 80) * 4)})
	if err := png.Encode(heightFile, heightSource); err != nil {
		t.Fatal(err)
	}
	if err := heightFile.Close(); err != nil {
		t.Fatal(err)
	}

	options := BuildOptions{World: "Asgard", Seed: "SeedTest01", WorldGenVersion: 37, Size: MinimumSize, Workers: 2, HeightPath: height}
	first, err := Build(context.Background(), root, source, options)
	if err != nil {
		t.Fatal(err)
	}
	second, err := Build(context.Background(), root, source, options)
	if err != nil {
		t.Fatal(err)
	}
	if first.Key != second.Key || first.Width != MinimumSize || first.Height != MinimumSize {
		t.Fatalf("non-deterministic or undersized manifests: %#v %#v", first, second)
	}
	if first.HeightSHA256 == "" || first.TextureSHA256 == "" {
		t.Fatalf("height and texture identities are required: %#v", first)
	}
	path, err := TilePath(root, first, first.MaxZoom, 23, 23)
	if err != nil {
		t.Fatal(err)
	}
	if info, err := os.Stat(path); err != nil || info.Size() == 0 {
		t.Fatalf("maximum-resolution edge tile missing: info=%v err=%v", info, err)
	}
	if len(first.TileETags) != 770 {
		t.Fatalf("tile ETags = %d, want 770", len(first.TileETags))
	}
	current, err := LoadManifest(filepath.Join(root, "worlds", "Asgard", "current.json"))
	if err != nil {
		t.Fatal(err)
	}
	if current.Key != first.Key {
		t.Fatalf("published key = %q, want %q", current.Key, first.Key)
	}
}

func TestTilePathRejectsTraversalAndOutOfBounds(t *testing.T) {
	if _, err := CurrentManifestPath("/maps", "../Asgard"); err == nil {
		t.Fatal("world path traversal was accepted")
	}
	manifest := Manifest{MaxZoom: 0, Levels: []Level{{Zoom: 0, TilesWide: 1, TilesHigh: 1}}}
	if _, err := TilePath("/maps", manifest, 0, 1, 0); err == nil {
		t.Fatal("out-of-bounds tile was accepted")
	}
}
