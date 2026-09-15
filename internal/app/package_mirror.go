package app

import (
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
)

// The package mirror: this portal as a SECOND source for the mod archives a profile pins.
//
// Every package in every definition is fetched by the client from Thunderstore's CDN, so a
// player whose route to one CDN edge is broken cannot install at all. That is not
// hypothetical - on 2026-09-14 an install of ulfsland-vr-flat-admin died on a dial timeout
// to 92.38.145.145:443 for one 31 KB file, while the same URL answered 200 in 0.065 s from
// this host. Meanwhile this server already holds every one of those archives: publishing
// downloads them to build the definition, and they stay in the profile's manager cache.
//
// So the bytes are here, the hash for them is already in the definition the client has, and
// the client is already authenticated to this portal. The mirror is just the last missing
// hop - and it is a FALLBACK: the client exhausts Thunderstore first, so a healthy install
// still moves no package bytes through the portal.
//
// Scope, deliberately narrow:
//   - The route requires the same profile-scoped device token as the payload and runtime
//     routes, so an unauthenticated request can enumerate nothing at all.
//   - A package is addressed by its SHA-256, and served only if that hash appears in the
//     definition of the release THIS token was issued for. A token for one world cannot
//     read another world's packages, and no token can reach a file that is not published.
//   - The bytes are hashed before they are served, so the portal cannot hand out anything
//     that does not match the published hash even if the cache on disk is wrong.
//
// PORTAL_PACKAGE_CACHE_ROOT empty - which is every deployment that has not mounted the
// cache - disables the route with a 404, and the client simply has no second source.
func (s *Server) clientPackage(w http.ResponseWriter, r *http.Request) {
	world, profile, clientType := r.PathValue("world"), r.PathValue("profile"), r.PathValue("clientType")
	claims, ok, err := s.validDeviceToken(r.Context(), r, world, profile, clientType)
	if err != nil {
		http.Error(w, "unavailable", http.StatusServiceUnavailable)
		return
	}
	if !ok || claims.Scope != deviceTokenScopeProfile {
		http.Error(w, "client authorization required", http.StatusUnauthorized)
		return
	}
	digest := strings.ToLower(r.PathValue("sha256"))
	if !validSHA256(digest) {
		http.NotFound(w, r)
		return
	}
	if s.cfg.PackageCacheRoot == "" {
		http.NotFound(w, r)
		return
	}
	release, err := s.store.CurrentRelease(r.Context(), claims.World, claims.Profile, claims.ClientType)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			http.NotFound(w, r)
			return
		}
		http.Error(w, "unavailable", http.StatusServiceUnavailable)
		return
	}
	definition, err := s.releaseDefinition(r.Context(), release)
	if err != nil {
		http.Error(w, "unavailable", http.StatusServiceUnavailable)
		return
	}
	wanted, found := ProfilePackage{}, false
	for _, pkg := range definition.Packages {
		if strings.EqualFold(pkg.SHA256, digest) {
			wanted, found = pkg, true
			break
		}
	}
	if !found {
		http.NotFound(w, r)
		return
	}
	path, err := locateCachedPackage(s.cfg.PackageCacheRoot, wanted)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	file, err := os.Open(path)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	defer file.Close()
	w.Header().Set("Content-Type", "application/zip")
	w.Header().Set("Content-Length", fmt.Sprintf("%d", wanted.Size))
	w.Header().Set("X-Checksum-SHA256", strings.ToLower(wanted.SHA256))
	// Content-addressed and immutable: the URL names the hash of what it returns, so a
	// re-download by the same player can legitimately be answered from their own cache.
	w.Header().Set("Cache-Control", "private, max-age=3600")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	if r.Method == http.MethodHead {
		return
	}
	io.Copy(w, file)
}

// locateCachedPackage finds one published package in the server-side mod cache and proves
// it is that package before returning its path.
//
// The cache is laid out by the mod manager as <root>/<profile>/manager-cache/packages, and
// it names files <Name>-<Version>.zip - NOT the Thunderstore <Namespace>-<Name>-<Version>.zip
// the definition pins (measured: ulfsland-admin holds CarryWeightSkill-1.0.1.zip for
// 95Shade-CarryWeightSkill-1.0.1.zip, byte-identical to the CDN copy). A published profile
// name is not the source profile name either, so which cache directory holds a given
// package is not derivable - every profile directory is tried, and the SHA-256 decides.
//
// The size check comes first because it is a stat, and rejects the common near-miss - an
// older or newer build of the same mod under the same name - without reading a file.
func locateCachedPackage(root string, pkg ProfilePackage) (string, error) {
	if !validIdentifier(pkg.Name) || !validIdentifier(pkg.Version) || !validFilename(pkg.Filename) {
		return "", errors.New("package identity is not addressable")
	}
	profiles, err := os.ReadDir(root)
	if err != nil {
		return "", err
	}
	candidates := []string{pkg.Name + "-" + pkg.Version + ".zip", pkg.Filename}
	for _, profile := range profiles {
		if !profile.IsDir() {
			continue
		}
		for _, candidate := range candidates {
			path := filepath.Join(root, profile.Name(), "manager-cache", "packages", candidate)
			if !inside(root, path) {
				continue
			}
			info, err := os.Stat(path)
			if err != nil || !info.Mode().IsRegular() || info.Size() != pkg.Size {
				continue
			}
			if packageFileMatches(path, pkg.SHA256) {
				return path, nil
			}
		}
	}
	return "", errors.New("package is not in the server-side cache")
}

func packageFileMatches(path, expected string) bool {
	file, err := os.Open(path)
	if err != nil {
		return false
	}
	defer file.Close()
	hash := sha256.New()
	if _, err := io.Copy(hash, file); err != nil {
		return false
	}
	return strings.EqualFold(hex.EncodeToString(hash.Sum(nil)), expected)
}
