package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strings"

	portalapp "github.com/neuralyze/valheim-portal/internal/app"
)

const runtimeOverlayStateFilename = ".valheim-profile-sync-vr-runtime.json"
const nativeBhapticsRuntimePath = "Valheim_Data/Managed/Bhaptics.Tact.dll"

type runtimeOverlayFile struct {
	Path   string `json:"path"`
	SHA256 string `json:"sha256"`
}
type runtimeOverlayBackup struct {
	Path string `json:"path"`
}

type runtimeOverlayState struct {
	Schema    int                    `json:"schema"`
	ReleaseID string                 `json:"release_id"`
	World     string                 `json:"world"`
	Profile   string                 `json:"profile"`
	Source    string                 `json:"source"`
	Files     []runtimeOverlayFile   `json:"files"`
	Backups   []runtimeOverlayBackup `json:"backups,omitempty"`
}

func extractVRRuntime(source, profileDestination, runtimeDestination string) error {
	if err := portalapp.ValidateVRRuntimeArtifact(source); err != nil {
		return fmt.Errorf("validate VR runtime artifact: %w", err)
	}
	if err := extractSelectedZip(source, profileDestination, func(name string) bool {
		return name == "BepInEx" || strings.HasPrefix(name, "BepInEx/")
	}, true); err != nil {
		return err
	}
	return extractSelectedZip(source, runtimeDestination, func(name string) bool {
		return name == "Valheim_Data" || strings.HasPrefix(name, "Valheim_Data/")
	}, false)
}

func extractFlatCompanion(source, profileDestination string) error {
	if err := portalapp.ValidateFlatCompanionArtifact(source); err != nil {
		return fmt.Errorf("validate Flat companion artifact: %w", err)
	}
	return extractSelectedZip(source, profileDestination, func(name string) bool {
		return name == "BepInEx" || strings.HasPrefix(name, "BepInEx/")
	}, true)
}

// extractDiagnosticsPlugin installs the portal-hosted diagnostics plugin into the
// profile's BepInEx tree. It is deliberately not client-type scoped: the same
// assembly ships to VR and Flat, and the artifact validator constrains the archive
// to BepInEx/plugins/ValheimDiagnostics.
func extractDiagnosticsPlugin(source, profileDestination string) error {
	if err := portalapp.ValidateDiagnosticPluginArtifact(source); err != nil {
		return fmt.Errorf("validate diagnostics plugin artifact: %w", err)
	}
	return extractSelectedZip(source, profileDestination, func(name string) bool {
		return name == "BepInEx" || strings.HasPrefix(name, "BepInEx/")
	}, true)
}

func runtimeFiles(root string) ([]runtimeOverlayFile, error) {
	files := make([]runtimeOverlayFile, 0, 64)
	err := filepath.WalkDir(root, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.Type()&os.ModeSymlink != 0 {
			return errors.New("runtime staging contains a symbolic link")
		}
		if entry.IsDir() {
			return nil
		}
		if !entry.Type().IsRegular() {
			return errors.New("runtime staging contains a non-regular file")
		}
		relative, err := filepath.Rel(root, path)
		if err != nil || relative == "." || strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
			return errors.New("runtime staging path escapes its root")
		}
		relativePath := filepath.ToSlash(relative)
		// Bhaptics ships with Valheim itself. The VR archive must contain it so
		// releases are complete, but the runtime overlay must not own, replace,
		// or delete the game's native copy.
		if relativePath == nativeBhapticsRuntimePath {
			return nil
		}
		digest, err := sha256File(path)
		if err != nil {
			return err
		}
		files = append(files, runtimeOverlayFile{Path: relativePath, SHA256: digest})
		return nil
	})
	if err != nil {
		return nil, err
	}
	sort.Slice(files, func(i, j int) bool { return files[i].Path < files[j].Path })
	if len(files) == 0 {
		return nil, errors.New("runtime staging is empty")
	}
	return files, nil
}

func reconcileRuntimeOverlay(gameDir, localAppData string, request profileRequest, releaseID, source string) error {
	lock, err := acquireProfileLock(gameDir)
	if err != nil {
		return err
	}
	defer lock.Close()
	statePath := filepath.Join(gameDir, runtimeOverlayStateFilename)
	current, present, err := loadRuntimeOverlayState(statePath, localAppData)
	if err != nil {
		return err
	}
	if source != "" {
		if request.ClientType != clientVR || releaseID == "" {
			return errors.New("invalid desired VR runtime")
		}
		files, err := runtimeFiles(source)
		if err != nil {
			return err
		}
		if present && current.ReleaseID == releaseID && current.Source == source && sameRuntimeFiles(current.Files, files) {
			return verifyRuntimeOverlay(gameDir, current)
		}
		desired := runtimeOverlayState{Schema: 1, ReleaseID: releaseID, World: request.World, Profile: request.Profile, Source: source, Files: files}
		if present {
			if err := removeRuntimeOverlay(gameDir, current); err != nil {
				if discardErr := discardModifiedRuntimeOverlay(gameDir, current); discardErr != nil {
					return fmt.Errorf("discard current VR runtime: %w", discardErr)
				}
			}
		}
		copied, err := applyRuntimeOverlay(gameDir, &desired)
		if err != nil {
			if present {
				if _, restoreErr := applyRuntimeOverlay(gameDir, &current); restoreErr != nil {
					return fmt.Errorf("activate VR runtime: %w; restore previous runtime: %v", err, restoreErr)
				}
			}
			return err
		}
		if err := writeJSONAtomically(statePath, desired); err != nil {
			rollbackRuntimeApplication(gameDir, desired, copied)
			if present {
				_, _ = applyRuntimeOverlay(gameDir, &current)
			}
			return err
		}
		return nil
	}
	if !present {
		return nil
	}
	if err := removeRuntimeOverlay(gameDir, current); err != nil {
		return err
	}
	return os.Remove(statePath)
}

func loadRuntimeOverlayState(path, localAppData string) (runtimeOverlayState, bool, error) {
	data, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return runtimeOverlayState{}, false, nil
	}
	if err != nil {
		return runtimeOverlayState{}, false, err
	}
	var state runtimeOverlayState
	if err := jsonUnmarshalStrict(data, &state); err != nil || !validRuntimeOverlayState(state, localAppData) {
		return runtimeOverlayState{}, false, errors.New("invalid ValheimVR runtime ownership state")
	}
	return state, true, nil
}

func validRuntimeOverlayState(state runtimeOverlayState, localAppData string) bool {
	storage, _, err := loadProfileStorageDirectory(localAppData)
	if err != nil || state.Schema != 1 || state.ReleaseID == "" || !validPackageText(state.World) || !validPackageText(state.Profile) || state.Source == "" || len(state.Files) == 0 || !pathInside(filepath.Join(storage, "profiles"), state.Source) {
		return false
	}
	paths := make(map[string]struct{}, len(state.Files))
	previous := ""
	for _, file := range state.Files {
		if file.Path == "" || strings.Contains(file.Path, "\\") || filepath.Clean(file.Path) != filepath.FromSlash(file.Path) || strings.HasPrefix(file.Path, "../") || file.Path <= previous || !validSHA256(file.SHA256) {
			return false
		}
		paths[file.Path] = struct{}{}
		previous = file.Path
	}
	for _, backup := range state.Backups {
		if _, ok := paths[backup.Path]; !ok {
			return false
		}
	}
	return true
}

func pathInside(root, candidate string) bool {
	relative, err := filepath.Rel(root, candidate)
	return err == nil && relative != ".." && !strings.HasPrefix(relative, ".."+string(filepath.Separator))
}

func sameRuntimeFiles(left, right []runtimeOverlayFile) bool {
	if len(left) != len(right) {
		return false
	}
	for index := range left {
		if left[index] != right[index] {
			return false
		}
	}
	return true
}

func discardModifiedRuntimeOverlay(gameDir string, state runtimeOverlayState) error {
	backups := make(map[string]struct{}, len(state.Backups))
	for _, backup := range state.Backups {
		backups[backup.Path] = struct{}{}
	}
	for _, file := range state.Files {
		if !portalapp.IsVRRuntimeArtifactPath(file.Path) {
			return fmt.Errorf("refusing to discard foreign or unknown runtime file %q", file.Path)
		}
		destination := filepath.Join(gameDir, filepath.FromSlash(file.Path))
		if _, backedUp := backups[file.Path]; backedUp {
			backup := runtimeBackupPath(gameDir, file.Path)
			if err := copyFileAtomically(backup, destination); err != nil {
				return fmt.Errorf("restore runtime collision %q: %w", file.Path, err)
			}
		} else if err := os.Remove(destination); err != nil && !errors.Is(err, os.ErrNotExist) {
			return err
		}
	}
	for _, backup := range state.Backups {
		if err := os.Remove(runtimeBackupPath(gameDir, backup.Path)); err != nil && !errors.Is(err, os.ErrNotExist) {
			return err
		}
	}
	return nil
}

func verifyRuntimeOverlay(gameDir string, state runtimeOverlayState) error {
	for _, file := range state.Files {
		if err := verifyFile(filepath.Join(gameDir, filepath.FromSlash(file.Path)), fileSize(filepath.Join(state.Source, filepath.FromSlash(file.Path))), file.SHA256); err != nil {
			return fmt.Errorf("runtime overlay file %q is modified or missing", file.Path)
		}
	}
	return nil
}

func fileSize(path string) int64 {
	info, err := os.Stat(path)
	if err != nil {
		return -1
	}
	return info.Size()
}

func runtimeBackupPath(gameDir, runtimePath string) string {
	return filepath.Join(gameDir, ".valheim-profile-sync-vr-runtime-backups", filepath.FromSlash(runtimePath))
}

func applyRuntimeOverlay(gameDir string, state *runtimeOverlayState) ([]runtimeOverlayFile, error) {
	state.Backups = state.Backups[:0]
	for _, file := range state.Files {
		destination := filepath.Join(gameDir, filepath.FromSlash(file.Path))
		if info, err := os.Lstat(destination); err == nil {
			if !info.Mode().IsRegular() {
				return nil, fmt.Errorf("refusing to replace non-regular runtime collision %q", file.Path)
			}
			if !portalapp.IsVRRuntimeArtifactPath(file.Path) {
				return nil, fmt.Errorf("refusing to replace foreign or unknown runtime file %q", file.Path)
			}
			backup := runtimeBackupPath(gameDir, file.Path)
			if err := os.MkdirAll(filepath.Dir(backup), 0o700); err != nil {
				return nil, err
			}
			if err := copyFileAtomically(destination, backup); err != nil {
				return nil, err
			}
			state.Backups = append(state.Backups, runtimeOverlayBackup{Path: file.Path})
		} else if !errors.Is(err, os.ErrNotExist) {
			return nil, err
		}
	}

	copied := make([]runtimeOverlayFile, 0, len(state.Files))
	for _, file := range state.Files {
		source := filepath.Join(state.Source, filepath.FromSlash(file.Path))
		destination := filepath.Join(gameDir, filepath.FromSlash(file.Path))
		if err := os.MkdirAll(filepath.Dir(destination), 0o700); err != nil {
			rollbackRuntimeApplication(gameDir, *state, copied)
			return nil, err
		}
		if err := copyFileAtomically(source, destination); err != nil {
			rollbackRuntimeApplication(gameDir, *state, copied)
			return nil, err
		}
		copied = append(copied, file)
	}
	return copied, nil
}

func removeRuntimeOverlay(gameDir string, state runtimeOverlayState) error {
	if err := verifyRuntimeOverlay(gameDir, state); err != nil {
		return err
	}
	backups := make(map[string]struct{}, len(state.Backups))
	for _, backup := range state.Backups {
		backups[backup.Path] = struct{}{}
	}
	removed := make([]runtimeOverlayFile, 0, len(state.Files))
	for _, file := range state.Files {
		destination := filepath.Join(gameDir, filepath.FromSlash(file.Path))
		if _, backedUp := backups[file.Path]; backedUp {
			if err := copyFileAtomically(runtimeBackupPath(gameDir, file.Path), destination); err != nil {
				rollbackRemovedRuntimeFiles(gameDir, state.Source, removed)
				return err
			}
		} else if err := os.Remove(destination); err != nil {
			rollbackRemovedRuntimeFiles(gameDir, state.Source, removed)
			return err
		}
		removed = append(removed, file)
	}
	for _, backup := range state.Backups {
		_ = os.Remove(runtimeBackupPath(gameDir, backup.Path))
	}
	return nil
}

func rollbackRuntimeApplication(gameDir string, state runtimeOverlayState, copied []runtimeOverlayFile) {
	rollbackRuntimeFiles(gameDir, copied)
	for _, backup := range state.Backups {
		destination := filepath.Join(gameDir, filepath.FromSlash(backup.Path))
		if err := os.MkdirAll(filepath.Dir(destination), 0o700); err == nil {
			_ = copyFileAtomically(runtimeBackupPath(gameDir, backup.Path), destination)
		}
		_ = os.Remove(runtimeBackupPath(gameDir, backup.Path))
	}
}

func rollbackRuntimeFiles(gameDir string, files []runtimeOverlayFile) {
	for _, file := range files {
		_ = os.Remove(filepath.Join(gameDir, filepath.FromSlash(file.Path)))
	}
}

func rollbackRemovedRuntimeFiles(gameDir, source string, files []runtimeOverlayFile) {
	for _, file := range files {
		destination := filepath.Join(gameDir, filepath.FromSlash(file.Path))
		_ = os.MkdirAll(filepath.Dir(destination), 0o700)
		_ = copyFileAtomically(filepath.Join(source, filepath.FromSlash(file.Path)), destination)
	}
}

func jsonUnmarshalStrict(data []byte, target any) error {
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return err
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return errors.New("unexpected trailing JSON")
	}
	return nil
}
