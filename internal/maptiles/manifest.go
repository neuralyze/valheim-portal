package maptiles

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
)

const (
	SchemaVersion   = 1
	RendererVersion = "valheim-0.221.10-webmap-2.7.1-12288-v6"
	MinimumSize     = 12288
	DefaultSize     = MinimumSize
	TileSize        = 512
	WorldRadius     = 12288.0
)

type Bounds struct {
	MinX float64 `json:"min_x"`
	MinZ float64 `json:"min_z"`
	MaxX float64 `json:"max_x"`
	MaxZ float64 `json:"max_z"`
}

type Level struct {
	Zoom      int `json:"zoom"`
	Width     int `json:"width"`
	Height    int `json:"height"`
	TilesWide int `json:"tiles_wide"`
	TilesHigh int `json:"tiles_high"`
}

type Manifest struct {
	Schema          int               `json:"schema"`
	World           string            `json:"world"`
	Seed            string            `json:"seed"`
	WorldGenVersion int               `json:"worldgen_version"`
	Renderer        string            `json:"renderer"`
	Key             string            `json:"key"`
	SourceSHA256    string            `json:"source_sha256"`
	HeightSHA256    string            `json:"height_sha256"`
	TextureSHA256   string            `json:"texture_sha256"`
	SourceWidth     int               `json:"source_width"`
	SourceHeight    int               `json:"source_height"`
	Width           int               `json:"width"`
	Height          int               `json:"height"`
	TileSize        int               `json:"tile_size"`
	MaxZoom         int               `json:"max_zoom"`
	Format          string            `json:"format"`
	Bounds          Bounds            `json:"bounds"`
	Levels          []Level           `json:"levels"`
	TileETags       map[string]string `json:"tile_etags"`
}

func (m Manifest) Validate() error {
	if m.Schema != SchemaVersion || m.World == "" || m.Key == "" || m.Renderer == "" ||
		m.SourceSHA256 == "" || m.HeightSHA256 == "" || m.TextureSHA256 == "" {
		return errors.New("invalid map manifest identity")
	}
	if m.Width < MinimumSize || m.Height < MinimumSize || m.TileSize != TileSize || m.Format != "png" {
		return errors.New("invalid map manifest dimensions or format")
	}
	if m.MaxZoom < 0 || len(m.Levels) != m.MaxZoom+1 {
		return errors.New("invalid map manifest levels")
	}
	return nil
}

func LoadManifest(path string) (Manifest, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return Manifest{}, err
	}
	var manifest Manifest
	if err := json.Unmarshal(data, &manifest); err != nil {
		return Manifest{}, err
	}
	if err := manifest.Validate(); err != nil {
		return Manifest{}, err
	}
	return manifest, nil
}

func CurrentManifestPath(root, world string) (string, error) {
	if !validName(world) {
		return "", errors.New("invalid world name")
	}
	return filepath.Join(root, "worlds", world, "current.json"), nil
}

func TilePath(root string, manifest Manifest, zoom, x, y int) (string, error) {
	if zoom < 0 || zoom > manifest.MaxZoom || x < 0 || y < 0 {
		return "", errors.New("invalid tile coordinate")
	}
	level := manifest.Levels[zoom]
	if x >= level.TilesWide || y >= level.TilesHigh {
		return "", errors.New("tile coordinate outside level")
	}
	return filepath.Join(root, "objects", manifest.Key, "terrain", fmt.Sprint(zoom), fmt.Sprint(x), fmt.Sprintf("%d.png", y)), nil
}

func validName(value string) bool {
	if value == "" || value == "." || value == ".." {
		return false
	}
	for _, r := range value {
		if r != '-' && r != '_' && r != '.' && (r < '0' || r > '9') && (r < 'A' || r > 'Z') && (r < 'a' || r > 'z') {
			return false
		}
	}
	return true
}
