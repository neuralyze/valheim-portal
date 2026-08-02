package maptiles

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

// testManifest builds a manifest that passes Validate, varying only world and key.
func testManifest(world, key string) Manifest {
	return Manifest{Schema: SchemaVersion, World: world, Seed: "seed", WorldGenVersion: 37, Renderer: RendererVersion, Key: key, SourceSHA256: "source", HeightSHA256: "height", TextureSHA256: "texture", SourceWidth: 12288, SourceHeight: 12288, Width: 12288, Height: 12288, TileSize: TileSize, MaxZoom: 0, Format: "png", Bounds: Bounds{-WorldRadius, -WorldRadius, WorldRadius, WorldRadius}, Levels: []Level{{Zoom: 0, Width: 12288, Height: 12288, TilesWide: 24, TilesHigh: 24}}, TileETags: map[string]string{}}
}

// mkObjects creates object dirs with ascending mtimes, so names[0] is oldest.
func mkObjects(t *testing.T, root string, names ...string) {
	t.Helper()
	for index, name := range names {
		path := filepath.Join(root, "objects", name)
		if err := os.MkdirAll(path, 0o750); err != nil {
			t.Fatal(err)
		}
		stamp := time.Unix(int64(index+1), 0)
		if err := os.Chtimes(path, stamp, stamp); err != nil {
			t.Fatal(err)
		}
	}
}

// The live object is the OLDEST here, so mtime ranking alone cannot save it - only reading the world's
// live pointer can. The original prune looked for pointers under <root>/current/, a directory this
// layout never had, so its protection set was always empty and this case deleted a live map.
func TestPruneRetainsLiveObjectEvenWhenOldest(t *testing.T) {
	root := t.TempDir()
	mkObjects(t, root, "live-key", "newer-a", "newer-b")
	if err := publishCurrent(root, testManifest("Midgard", "live-key")); err != nil {
		t.Fatal(err)
	}
	if err := Prune(root, 2); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(root, "objects", "live-key")); err != nil {
		t.Fatalf("live object was pruned: %v", err)
	}
}

// Every world's pointer must be honoured, not just the first one read.
func TestPruneRetainsLiveObjectsForAllWorlds(t *testing.T) {
	root := t.TempDir()
	mkObjects(t, root, "live-midgard", "live-vanaheim", "newest")
	for world, key := range map[string]string{"Midgard": "live-midgard", "Vanaheim": "live-vanaheim"} {
		if err := publishCurrent(root, testManifest(world, key)); err != nil {
			t.Fatal(err)
		}
	}
	// A limit of 1 keeps only "newest" among non-live objects; both live keys must survive regardless.
	if err := Prune(root, 1); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"live-midgard", "live-vanaheim", "newest"} {
		if _, err := os.Stat(filepath.Join(root, "objects", name)); err != nil {
			t.Fatalf("object %q missing: %v", name, err)
		}
	}
}

// A pointer that exists but will not parse must fail loudly. Treating it as absent is precisely how a
// live object gets collected.
func TestPruneFailsOnUnparseableLivePointer(t *testing.T) {
	root := t.TempDir()
	mkObjects(t, root, "live-key", "newest")
	path, err := CurrentManifestPath(root, "Midgard")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("{not json"), 0o640); err != nil {
		t.Fatal(err)
	}
	if err := Prune(root, 1); err == nil {
		t.Fatal("Prune succeeded despite an unreadable live pointer")
	}
	if _, err := os.Stat(filepath.Join(root, "objects", "live-key")); err != nil {
		t.Fatalf("object deleted while the live set was unknown: %v", err)
	}
}

func TestPruneRetainsCurrentAndNewestObjects(t *testing.T) {
	root := t.TempDir()
	for index, name := range []string{"old", "new", "current-key"} {
		path := filepath.Join(root, "objects", name)
		if err := os.MkdirAll(path, 0o750); err != nil {
			t.Fatal(err)
		}
		stamp := time.Unix(int64(index+1), 0)
		if err := os.Chtimes(path, stamp, stamp); err != nil {
			t.Fatal(err)
		}
	}
	manifest := Manifest{Schema: SchemaVersion, World: "Midgard", Seed: "seed", WorldGenVersion: 37, Renderer: RendererVersion, Key: "current-key", SourceSHA256: "source", HeightSHA256: "height", TextureSHA256: "texture", SourceWidth: 12288, SourceHeight: 12288, Width: 12288, Height: 12288, TileSize: TileSize, MaxZoom: 0, Format: "png", Bounds: Bounds{-WorldRadius, -WorldRadius, WorldRadius, WorldRadius}, Levels: []Level{{Zoom: 0, Width: 12288, Height: 12288, TilesWide: 24, TilesHigh: 24}}, TileETags: map[string]string{}}
	if err := publishCurrent(root, manifest); err != nil {
		t.Fatal(err)
	}
	if err := Prune(root, 2); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"new", "current-key"} {
		if _, err := os.Stat(filepath.Join(root, "objects", name)); err != nil {
			t.Fatalf("retained object %q missing: %v", name, err)
		}
	}
	if _, err := os.Stat(filepath.Join(root, "objects", "old")); !os.IsNotExist(err) {
		t.Fatalf("old object was not pruned: %v", err)
	}
}
