package maptiles

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"

	"github.com/neuralyze/valheim-portal/internal/worldintel"
)

type OverlayManifest struct {
	Schema       int               `json:"schema"`
	World        string            `json:"world"`
	TerrainKey   string            `json:"terrain_key"`
	SourceSHA256 string            `json:"source_sha256"`
	TileETags    map[string]string `json:"tile_etags"`
}

func OverlayPath(root string, terrain Manifest, sourceSHA256 string, zoom, x, y int) (string, error) {
	if !validName(sourceSHA256) || sourceSHA256 == "" {
		return "", errors.New("invalid overlay source hash")
	}
	if _, err := TilePath(root, terrain, zoom, x, y); err != nil {
		return "", err
	}
	return filepath.Join(root, "objects", terrain.Key, "overlays", fmt.Sprintf("v%d", OverlaySchemaVersion), sourceSHA256, fmt.Sprint(zoom), fmt.Sprint(x), fmt.Sprintf("%d.json", y)), nil
}

func BuildOverlayPyramid(ctx context.Context, root string, terrain Manifest, snapshot worldintel.Snapshot) (OverlayManifest, error) {
	if err := terrain.Validate(); err != nil {
		return OverlayManifest{}, err
	}
	if snapshot.World != terrain.World || !validName(snapshot.Source.SHA256) {
		return OverlayManifest{}, errors.New("overlay snapshot identity does not match terrain")
	}
	finalRoot := filepath.Join(root, "objects", terrain.Key, "overlays", fmt.Sprintf("v%d", OverlaySchemaVersion), snapshot.Source.SHA256)
	manifestPath := filepath.Join(finalRoot, "manifest.json")
	if data, err := os.ReadFile(manifestPath); err == nil {
		var existing OverlayManifest
		if json.Unmarshal(data, &existing) == nil && existing.Schema == OverlaySchemaVersion && existing.SourceSHA256 == snapshot.Source.SHA256 {
			return existing, nil
		}
	}
	if err := os.MkdirAll(filepath.Join(root, "staging"), 0o750); err != nil {
		return OverlayManifest{}, err
	}
	stage, err := os.MkdirTemp(filepath.Join(root, "staging"), "overlay-")
	if err != nil {
		return OverlayManifest{}, err
	}
	defer os.RemoveAll(stage)
	manifest := OverlayManifest{Schema: OverlaySchemaVersion, World: snapshot.World, TerrainKey: terrain.Key, SourceSHA256: snapshot.Source.SHA256, TileETags: make(map[string]string)}
	for _, level := range terrain.Levels {
		for y := 0; y < level.TilesHigh; y++ {
			for x := 0; x < level.TilesWide; x++ {
				if err := ctx.Err(); err != nil {
					return OverlayManifest{}, err
				}
				tile, ok := SelectOverlay(snapshot, level, x, y)
				if !ok {
					return OverlayManifest{}, errors.New("invalid overlay tile coordinate")
				}
				data, err := json.Marshal(tile)
				if err != nil {
					return OverlayManifest{}, err
				}
				relative := filepath.Join(fmt.Sprint(level.Zoom), fmt.Sprint(x), fmt.Sprintf("%d.json", y))
				path := filepath.Join(stage, relative)
				if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
					return OverlayManifest{}, err
				}
				if err := os.WriteFile(path, data, 0o640); err != nil {
					return OverlayManifest{}, err
				}
				hash := sha256.Sum256(data)
				manifest.TileETags[fmt.Sprintf("%d/%d/%d", level.Zoom, x, y)] = hex.EncodeToString(hash[:])
			}
		}
	}
	manifestJSON, err := json.Marshal(manifest)
	if err != nil {
		return OverlayManifest{}, err
	}
	if err := os.WriteFile(filepath.Join(stage, "manifest.json"), manifestJSON, 0o640); err != nil {
		return OverlayManifest{}, err
	}
	if err := os.MkdirAll(filepath.Dir(finalRoot), 0o750); err != nil {
		return OverlayManifest{}, err
	}
	if err := os.Rename(stage, finalRoot); err != nil {
		if _, statErr := os.Stat(manifestPath); statErr != nil {
			return OverlayManifest{}, err
		}
	}
	return manifest, nil
}
