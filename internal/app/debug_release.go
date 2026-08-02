package app

import (
	"archive/zip"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"

	"github.com/neuralyze/valheim-portal/internal/profilecfg"
)

// republishWithDebugLogging publishes a new release of the currently published
// profile with client diagnostics toggled.
//
// Toggling debug logging never changes the package set, only the two configuration
// files inside the profile definition, so the existing artifact is rewritten in
// place instead of rebuilding the profile from Thunderstore. That keeps the admin
// action self-contained: no builder, no network, no subprocess.
//
// Every other artifact is carried over unchanged. The diagnostics plugin is
// attached only while debug logging is on, and only if the current release already
// carries one, because the portal cannot invent that artifact.
func (s *Server) republishWithDebugLogging(ctx context.Context, world, profile, clientType, actor string, debug bool) (string, error) {
	release, err := s.store.CurrentRelease(ctx, world, profile, clientType)
	if err != nil {
		return "", err
	}
	artifacts, err := s.store.PublishedArtifacts(ctx, release.ID)
	if err != nil {
		return "", err
	}
	byKind := make(map[string]Artifact, len(artifacts))
	for _, artifact := range artifacts {
		byKind[artifact.Kind] = artifact
	}
	profileArtifact, ok := byKind["profile"]
	if !ok {
		return "", errors.New("published release has no profile artifact")
	}

	version, err := bumpPatchVersion(release.Version)
	if err != nil {
		return "", err
	}
	next := Release{
		ID: profile + "-" + version, World: world, Profile: profile, ClientType: clientType,
		Version: version,
		Notes:   fmt.Sprintf("Debug logging %s.", enabledWord(debug)),
	}
	if _, err := s.store.Release(ctx, next.ID); err == nil {
		return "", fmt.Errorf("release %s already exists", next.ID)
	}

	rewritten, err := rewriteProfileConfig(profileArtifact.Path, profilecfg.Patches(debug))
	if err != nil {
		return "", err
	}
	if err := s.store.CreateRelease(ctx, next, actor); err != nil {
		return "", err
	}

	staged := make([]string, 0, 4)
	fail := func(err error) (string, error) {
		for _, path := range staged {
			os.Remove(path)
		}
		return "", err
	}
	if err := s.stageArtifactBytes(ctx, next.ID, "profile", profileArtifact.Name, rewritten, actor, &staged); err != nil {
		return fail(err)
	}
	// Every artifact is carried over unconditionally, including diag_plugin. That
	// artifact no longer holds only diagnostics: it also delivers NeuralyzeVRFixes,
	// which corrects real VR defects and must stay installed regardless of whether
	// verbose logging is on. Dropping it when debug was switched off silently
	// uninstalled those fixes from the client.
	carry := []string{"vr_runtime", "flat_companion", "diag_plugin"}
	for _, kind := range carry {
		source, present := byKind[kind]
		if !present {
			continue
		}
		body, err := os.ReadFile(source.Path)
		if err != nil {
			return fail(err)
		}
		if err := s.stageArtifactBytes(ctx, next.ID, kind, source.Name, body, actor, &staged); err != nil {
			return fail(err)
		}
	}
	if err := s.store.Publish(ctx, next.ID, actor); err != nil {
		return fail(err)
	}
	return next.ID, nil
}

func enabledWord(enabled bool) string {
	if enabled {
		return "enabled"
	}
	return "disabled"
}

// stageArtifactBytes writes an artifact into the draft staging area and registers
// it, mirroring the administrative upload path so validation and auditing are
// identical for generated and uploaded artifacts.
func (s *Server) stageArtifactBytes(ctx context.Context, releaseID, kind, name string, body []byte, actor string, staged *[]string) error {
	if len(body) < 1 {
		return fmt.Errorf("refusing to stage an empty %s artifact", kind)
	}
	directory := filepath.Join(s.cfg.ArtifactRoot, "drafts", releaseID)
	if err := os.MkdirAll(directory, 0o750); err != nil {
		return err
	}
	path := filepath.Join(directory, randomID()+"-"+name)
	if err := os.WriteFile(path, body, 0o640); err != nil {
		return err
	}
	*staged = append(*staged, path)
	sum := sha256.Sum256(body)
	artifact := Artifact{
		ID: randomID(), ReleaseID: releaseID, Kind: kind, Name: name,
		SHA256: hex.EncodeToString(sum[:]), Size: int64(len(body)), Path: path,
	}
	return s.store.AddArtifact(ctx, artifact, actor)
}

// rewriteProfileConfig returns a new profile definition ZIP with the named
// configuration entries patched. Entry order and every untouched entry are
// preserved so the only difference between releases is the configuration itself.
func rewriteProfileConfig(source string, patches map[string]map[string]string) ([]byte, error) {
	reader, err := zip.OpenReader(source)
	if err != nil {
		return nil, err
	}
	defer reader.Close()

	var out bytes.Buffer
	writer := zip.NewWriter(&out)
	seen := map[string]bool{}
	for _, file := range reader.File {
		body, err := readZipEntry(file)
		if err != nil {
			return nil, err
		}
		if keys, ok := patches[file.Name]; ok {
			body = profilecfg.PatchINI(body, keys)
			seen[file.Name] = true
		}
		header := &zip.FileHeader{Name: file.Name, Method: file.Method, Modified: file.Modified}
		header.SetMode(file.Mode())
		entry, err := writer.CreateHeader(header)
		if err != nil {
			return nil, err
		}
		if _, err := entry.Write(body); err != nil {
			return nil, err
		}
	}
	for _, name := range sortedPatchNames(patches) {
		if seen[name] {
			continue
		}
		entry, err := writer.Create(name)
		if err != nil {
			return nil, err
		}
		if _, err := entry.Write(profilecfg.PatchINI(nil, patches[name])); err != nil {
			return nil, err
		}
	}
	if err := writer.Close(); err != nil {
		return nil, err
	}
	return out.Bytes(), nil
}

func sortedPatchNames(patches map[string]map[string]string) []string {
	names := make([]string, 0, len(patches))
	for name := range patches {
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}

func readZipEntry(file *zip.File) ([]byte, error) {
	if file.FileInfo().IsDir() {
		return nil, nil
	}
	if file.UncompressedSize64 > uint64(maxProfileConfigEntryBytes) {
		return nil, fmt.Errorf("profile entry %q is too large", file.Name)
	}
	handle, err := file.Open()
	if err != nil {
		return nil, err
	}
	defer handle.Close()
	return io.ReadAll(io.LimitReader(handle, maxProfileConfigEntryBytes+1))
}

const maxProfileConfigEntryBytes = int64(8 << 20)

// bumpPatchVersion increments the final dotted component. Publishing reuses the
// version in the release identifier, so a malformed version must fail loudly
// rather than produce a colliding or nonsensical release.
func bumpPatchVersion(version string) (string, error) {
	parts := strings.Split(strings.TrimSpace(version), ".")
	if len(parts) < 2 {
		return "", fmt.Errorf("cannot bump version %q", version)
	}
	patch, err := strconv.Atoi(parts[len(parts)-1])
	if err != nil || patch < 0 {
		return "", fmt.Errorf("cannot bump version %q", version)
	}
	parts[len(parts)-1] = strconv.Itoa(patch + 1)
	return strings.Join(parts, "."), nil
}
