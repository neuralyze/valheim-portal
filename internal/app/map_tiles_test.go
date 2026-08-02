package app

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"github.com/neuralyze/valheim-portal/internal/maptiles"
)

func TestTerrainTilesRequireAdminAndServeImmutableETag(t *testing.T) {
	server := testServer(t)
	world := PublicWorld{Name: "Asgard", JoinAddress: "valheim.example.test:2456", Status: "offline", ServerVersion: "0.221.10"}
	if err := server.store.UpsertPublicWorld(t.Context(), world, "test"); err != nil {
		t.Fatal(err)
	}

	levels := []maptiles.Level{
		{Zoom: 0, Width: 384, Height: 384, TilesWide: 1, TilesHigh: 1},
		{Zoom: 1, Width: 768, Height: 768, TilesWide: 2, TilesHigh: 2},
		{Zoom: 2, Width: 1536, Height: 1536, TilesWide: 3, TilesHigh: 3},
		{Zoom: 3, Width: 3072, Height: 3072, TilesWide: 6, TilesHigh: 6},
		{Zoom: 4, Width: 6144, Height: 6144, TilesWide: 12, TilesHigh: 12},
		{Zoom: 5, Width: 12288, Height: 12288, TilesWide: 24, TilesHigh: 24},
	}
	manifest := maptiles.Manifest{
		Schema: maptiles.SchemaVersion, World: "Asgard", Seed: "SeedTest01", WorldGenVersion: 37,
		Renderer: maptiles.RendererVersion, Key: "test-map-key", SourceSHA256: "source", HeightSHA256: "height", TextureSHA256: "texture", SourceWidth: 512, SourceHeight: 512,
		Width: 12288, Height: 12288, TileSize: 512, MaxZoom: 5, Format: "png",
		Bounds: maptiles.Bounds{MinX: -12288, MinZ: -12288, MaxX: 12288, MaxZ: 12288}, Levels: levels,
		TileETags: map[string]string{"0/0/0": "tile-sha256"},
	}
	manifestPath, err := maptiles.CurrentManifestPath(server.cfg.MapRoot, "Asgard")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Dir(manifestPath), 0o750); err != nil {
		t.Fatal(err)
	}
	manifestJSON, err := json.Marshal(manifest)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(manifestPath, manifestJSON, 0o640); err != nil {
		t.Fatal(err)
	}
	tilePath, err := maptiles.TilePath(server.cfg.MapRoot, manifest, 0, 0, 0)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Dir(tilePath), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(tilePath, []byte("tile-body"), 0o640); err != nil {
		t.Fatal(err)
	}

	unauthorized := httptest.NewRecorder()
	server.Handler().ServeHTTP(unauthorized, httptest.NewRequest(http.MethodGet, "/admin/worlds/Asgard/map/manifest.json", nil))
	if unauthorized.Code != http.StatusUnauthorized {
		t.Fatalf("manifest without admin = %d", unauthorized.Code)
	}

	manifestResponse := httptest.NewRecorder()
	server.Handler().ServeHTTP(manifestResponse, adminTestRequest(http.MethodGet, "/admin/worlds/Asgard/map/manifest.json", nil))
	if manifestResponse.Code != http.StatusOK {
		t.Fatalf("manifest = %d: %s", manifestResponse.Code, manifestResponse.Body.String())
	}

	tileResponse := httptest.NewRecorder()
	server.Handler().ServeHTTP(tileResponse, adminTestRequest(http.MethodGet, "/admin/worlds/Asgard/map/tiles/test-map-key/0/0/0.png", nil))
	if tileResponse.Code != http.StatusOK || tileResponse.Header().Get("ETag") != `"tile-sha256"` {
		t.Fatalf("tile response = %d ETag %q", tileResponse.Code, tileResponse.Header().Get("ETag"))
	}
	if got := tileResponse.Header().Get("Cache-Control"); got != "private, max-age=31536000, immutable" {
		t.Fatalf("Cache-Control = %q", got)
	}

	notModified := adminTestRequest(http.MethodGet, "/admin/worlds/Asgard/map/tiles/test-map-key/0/0/0.png", nil)
	notModified.Header.Set("If-None-Match", `"tile-sha256"`)
	notModifiedResponse := httptest.NewRecorder()
	server.Handler().ServeHTTP(notModifiedResponse, notModified)
	if notModifiedResponse.Code != http.StatusNotModified {
		t.Fatalf("conditional tile = %d", notModifiedResponse.Code)
	}
}
