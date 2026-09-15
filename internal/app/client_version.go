package app

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"
)

// Why the portal has to be able to state its own client build.
//
// The installed copy of the Windows client lives at a fixed path and is launched by the
// registered protocol, so replacing the file the portal serves changes nothing for a
// player who never runs the download by hand. On 2026-09-14 that cost an hour: four
// client builds shipped inside an hour, the operator clicked the portal's profile link
// after every one of them, and every click ran the SAME frozen binary. Nothing on either
// side could see the disagreement, because nothing on either side published an identity
// cheap enough to compare.
//
// This endpoint is that identity. It is unauthenticated on purpose - the launcher checks
// before it has a device token, and the same bytes are already downloadable from
// /client/ValheimProfileSync.exe without one, so nothing is disclosed that was private.
//
// The SHA-256 is computed from the file the portal would actually serve, never from a
// record of what somebody meant to publish. The version string is a label only: it comes
// from a sidecar written by scripts/build-windows-client.sh and is used ONLY when its
// recorded digest matches the digest of the file on disk, so a sidecar left behind by an
// earlier build can never put a wrong name on these bytes.

// clientBuildSidecarSuffix names the file the build script writes beside the executable.
const clientBuildSidecarSuffix = ".build.json"

// clientBuildDescriptor is the published identity of the client executable.
type clientBuildDescriptor struct {
	// Version is the linker-stamped build identity, or empty when it cannot be
	// established from bytes that match the executable.
	Version string `json:"version"`
	SHA256  string `json:"sha256"`
	Size    int64  `json:"size"`
}

// clientBuildCache avoids re-hashing sixteen megabytes on every poll. The key is the
// executable's size and modification time, which is what changes when the file is
// replaced; a same-size same-mtime replacement is not something a build produces, and the
// launcher verifies the bytes it downloads against this digest anyway, so a stale entry
// costs a refused update rather than a wrong one.
type clientBuildCache struct {
	mu       sync.Mutex
	size     int64
	modified time.Time
	value    clientBuildDescriptor
	valid    bool
}

func (cache *clientBuildCache) lookup(size int64, modified time.Time) (clientBuildDescriptor, bool) {
	cache.mu.Lock()
	defer cache.mu.Unlock()
	if !cache.valid || cache.size != size || !cache.modified.Equal(modified) {
		return clientBuildDescriptor{}, false
	}
	return cache.value, true
}

func (cache *clientBuildCache) store(size int64, modified time.Time, value clientBuildDescriptor) {
	cache.mu.Lock()
	defer cache.mu.Unlock()
	cache.size, cache.modified, cache.value, cache.valid = size, modified, value, true
}

// describeClientExecutable reports what the portal is serving at path.
func describeClientExecutable(path string, cache *clientBuildCache) (clientBuildDescriptor, error) {
	info, err := os.Stat(path)
	if err != nil {
		return clientBuildDescriptor{}, err
	}
	if !info.Mode().IsRegular() || info.Size() == 0 {
		return clientBuildDescriptor{}, errors.New("the published client is not a regular file")
	}
	if cached, ok := cache.lookup(info.Size(), info.ModTime()); ok {
		return cached, nil
	}
	digest, err := sha256File(path)
	if err != nil {
		return clientBuildDescriptor{}, err
	}
	descriptor := clientBuildDescriptor{
		Version: clientBuildLabel(path+clientBuildSidecarSuffix, digest),
		SHA256:  digest,
		Size:    info.Size(),
	}
	cache.store(info.Size(), info.ModTime(), descriptor)
	return descriptor, nil
}

// clientBuildLabel reads the build script's sidecar and returns its version only when the
// digest it records is the digest of the executable that was just hashed. Anything else -
// no sidecar, unreadable sidecar, sidecar from a previous build - yields an empty label,
// and the launcher then names the build by its digest instead.
func clientBuildLabel(sidecarPath, digest string) string {
	payload, err := os.ReadFile(sidecarPath)
	if err != nil || len(payload) > 4<<10 {
		return ""
	}
	var sidecar struct {
		Version string `json:"version"`
		SHA256  string `json:"sha256"`
	}
	if err := json.Unmarshal(payload, &sidecar); err != nil {
		return ""
	}
	if !strings.EqualFold(strings.TrimSpace(sidecar.SHA256), digest) {
		return ""
	}
	version := strings.TrimSpace(sidecar.Version)
	if len(version) > 128 {
		return ""
	}
	for _, r := range version {
		if r < 0x20 || r == 0x7f {
			return ""
		}
	}
	return version
}

// sha256File is spelled out here rather than shared with the artifact inspector because
// that one deliberately reads only the first few kilobytes.
func sha256File(path string) (string, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer file.Close()
	hash := sha256.New()
	if _, err := io.Copy(hash, file); err != nil {
		return "", err
	}
	return hex.EncodeToString(hash.Sum(nil)), nil
}

func (s *Server) clientVersion(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Type", "application/json")
	if problem := s.clientDownloadProblem(); problem != "" {
		slog.Error("refusing to publish the Windows client identity", "path", s.cfg.ClientExecutable, "problem", problem)
		w.WriteHeader(http.StatusServiceUnavailable)
		_ = json.NewEncoder(w).Encode(map[string]string{"error": "no Windows client is published"})
		return
	}
	descriptor, err := describeClientExecutable(s.cfg.ClientExecutable, &s.clientBuild)
	if err != nil {
		slog.Error("cannot describe the published Windows client", "path", s.cfg.ClientExecutable, "error", err)
		w.WriteHeader(http.StatusServiceUnavailable)
		_ = json.NewEncoder(w).Encode(map[string]string{"error": "no Windows client is published"})
		return
	}
	_ = json.NewEncoder(w).Encode(descriptor)
}
