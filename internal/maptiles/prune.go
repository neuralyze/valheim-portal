package maptiles

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"time"
)

type cacheObject struct {
	name    string
	modTime time.Time
}

// Prune deletes cached tile objects, keeping the maximumObjects newest by mtime. An object a world
// currently points at is always retained, whatever its age.
func Prune(root string, maximumObjects int) error {
	if maximumObjects < 1 {
		return errors.New("maximum map cache objects must be positive")
	}
	live, err := liveKeys(root)
	if err != nil {
		return err
	}
	objectRoot := filepath.Join(root, "objects")
	entries, err := os.ReadDir(objectRoot)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	objects := make([]cacheObject, 0, len(entries))
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		info, err := entry.Info()
		if err != nil {
			return err
		}
		objects = append(objects, cacheObject{name: entry.Name(), modTime: info.ModTime()})
	}
	sort.Slice(objects, func(i, j int) bool { return objects[i].modTime.After(objects[j].modTime) })
	retained := 0
	for _, object := range objects {
		if live[object.name] || retained < maximumObjects {
			retained++
			continue
		}
		if err := os.RemoveAll(filepath.Join(objectRoot, object.name)); err != nil {
			return err
		}
	}
	return nil
}

// liveKeys collects the object key each world currently points at.
//
// These pointers live at <root>/worlds/<world>/current.json - see CurrentManifestPath. An earlier
// version read <root>/current/*, a path this layout never had, so the protection set was always empty
// and any maximumObjects below the number of worlds silently deleted a live map. At the time of the fix
// Midgard's live map was the 5th newest of 15 objects, so Prune(root, 4) would have destroyed it.
//
// A world with no pointer contributes nothing, which is correct - there is no key to protect. A pointer
// that exists but will not parse is fatal, because treating it as absent is exactly how a live object
// gets collected.
func liveKeys(root string) (map[string]bool, error) {
	keys := make(map[string]bool)
	worlds, err := os.ReadDir(filepath.Join(root, "worlds"))
	if errors.Is(err, os.ErrNotExist) {
		return keys, nil
	}
	if err != nil {
		return nil, err
	}
	for _, world := range worlds {
		if !world.IsDir() {
			continue
		}
		path, err := CurrentManifestPath(root, world.Name())
		if err != nil {
			continue
		}
		manifest, err := LoadManifest(path)
		if errors.Is(err, os.ErrNotExist) {
			continue
		}
		if err != nil {
			return nil, fmt.Errorf("read live pointer for world %q: %w", world.Name(), err)
		}
		keys[manifest.Key] = true
	}
	return keys, nil
}
