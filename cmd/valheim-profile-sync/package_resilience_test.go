package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// dialTimeout is the failure the operator actually hit: no response from the CDN edge, as
// a net.Error that reports a timeout, which is what connectex produces on Windows.
type dialTimeout struct{}

func (dialTimeout) Error() string   { return "dial tcp 92.38.145.145:443: connection timed out" }
func (dialTimeout) Timeout() bool   { return true }
func (dialTimeout) Temporary() bool { return true }

func newSyncerWithoutBackoff(t *testing.T, portal *testPortal) (*profileSyncer, *[]progressUpdate) {
	t.Helper()
	syncer := newProfileSyncer(portal.httpClient)
	syncer.LocalAppData = t.TempDir()
	// The retry policy is being pinned, not the clock: sleeping the real 2s+5s+10s would
	// make this file the slowest in the package for no added evidence.
	syncer.Wait = func(waited time.Duration) {}
	updates := []progressUpdate{}
	syncer.Progress = func(update progressUpdate) { updates = append(updates, update) }
	return syncer, &updates
}

// A transient failure must not abandon the install. Before this change the first failed
// package download returned straight out of syncAuthorized, which is how one unreachable
// CDN edge killed an install of 105 packages that was otherwise complete.
func TestPackageDownloadSurvivesTransientFailures(t *testing.T) {
	request := profileRequest{World: "world", Profile: "alpha", ClientType: clientFlat}
	onlyPackage, archive := testPackage(t, "author-first-1.0.0.zip", "first.dll", "first")
	payload := testProfileArchive(t, request, []packageDefinition{onlyPackage}, []zipEntry{{Name: "config/first.cfg", Body: "first-config"}}, nil)
	portal := newTestPortal(t, payload, map[string][]byte{onlyPackage.Filename: archive})
	defer portal.Close()
	request.Portal = portal.request.Portal
	// Attempt 1 cannot reach the host, attempt 2 is refused with a 503, attempt 3 is
	// served. All three are failures a retry can fix.
	portal.packageFault = func(_ string, call int) (*http.Response, error) {
		switch call {
		case 1:
			return nil, &net.OpError{Op: "dial", Net: "tcp", Err: dialTimeout{}}
		case 2:
			return &http.Response{StatusCode: http.StatusServiceUnavailable, Status: "503 Service Unavailable", Body: io.NopCloser(strings.NewReader("busy"))}, nil
		}
		return nil, nil
	}
	syncer, updates := newSyncerWithoutBackoff(t, portal)

	changed, err := syncer.syncAuthorized(context.Background(), request, "test-token-123456")
	if err != nil || !changed {
		t.Fatalf("sync = changed:%t err:%v", changed, err)
	}
	if portal.packageCalls != 3 {
		t.Fatalf("package fetched in %d attempts, want 3", portal.packageCalls)
	}
	root, err := profileRoot(syncer.LocalAppData, request)
	if err != nil {
		t.Fatal(err)
	}
	if data, err := os.ReadFile(filepath.Join(root, "active", "BepInEx", "plugins", "first.dll")); err != nil || string(data) != "first" {
		t.Fatalf("installed plugin = %q, %v", data, err)
	}
	// A silent minute reads as a hang, so every retry is announced with the attempt
	// number and the reason.
	retries := 0
	for _, update := range *updates {
		if strings.HasPrefix(update.Stage, "Retrying ") {
			retries++
			if !strings.Contains(update.Detail, "Attempt ") {
				t.Fatalf("retry update does not say which attempt: %#v", update)
			}
		}
	}
	if retries != 2 {
		t.Fatalf("retries announced = %d, want 2: %#v", retries, *updates)
	}
}

// The other half of the policy: a definite answer is not retried. A 404 means the archive
// is not there, and a complete body with the wrong SHA-256 means the bytes are WRONG rather
// than missing - retrying either one just spends a player's time.
func TestPackageDownloadDoesNotRetryDefiniteFailures(t *testing.T) {
	for _, testCase := range []struct {
		name  string
		fault func(archive []byte) func(string, int) (*http.Response, error)
	}{
		{
			name: "not found",
			fault: func([]byte) func(string, int) (*http.Response, error) {
				return func(string, int) (*http.Response, error) {
					return &http.Response{StatusCode: http.StatusNotFound, Status: "404 Not Found", Body: io.NopCloser(strings.NewReader("missing"))}, nil
				}
			},
		},
		{
			// Exactly the published length, so only the hash can tell this apart from a
			// good download - and the hash means the source is serving other content.
			name: "wrong bytes at the right length",
			fault: func(archive []byte) func(string, int) (*http.Response, error) {
				return func(string, int) (*http.Response, error) {
					return &http.Response{StatusCode: http.StatusOK, Status: "200 OK", Body: io.NopCloser(bytes.NewReader(bytes.Repeat([]byte("x"), len(archive))))}, nil
				}
			},
		},
	} {
		t.Run(testCase.name, func(t *testing.T) {
			request := profileRequest{World: "world", Profile: "alpha", ClientType: clientFlat}
			onlyPackage, archive := testPackage(t, "author-first-1.0.0.zip", "first.dll", "first")
			payload := testProfileArchive(t, request, []packageDefinition{onlyPackage}, []zipEntry{{Name: "config/first.cfg", Body: "first-config"}}, nil)
			portal := newTestPortal(t, payload, map[string][]byte{onlyPackage.Filename: archive})
			defer portal.Close()
			request.Portal = portal.request.Portal
			portal.packageFault = testCase.fault(archive)
			syncer, _ := newSyncerWithoutBackoff(t, portal)

			if _, err := syncer.syncAuthorized(context.Background(), request, "test-token-123456"); err == nil {
				t.Fatal("a definite failure was reported as success")
			}
			if portal.packageCalls != 1 {
				t.Fatalf("attempts = %d, want 1: a definite failure must not be retried", portal.packageCalls)
			}
		})
	}
}

// A truncated body is the opposite case at the same length check: fewer bytes than
// published means the transfer broke, which is worth another attempt.
func TestPackageDownloadRetriesTruncatedBody(t *testing.T) {
	request := profileRequest{World: "world", Profile: "alpha", ClientType: clientFlat}
	onlyPackage, archive := testPackage(t, "author-first-1.0.0.zip", "first.dll", "first")
	payload := testProfileArchive(t, request, []packageDefinition{onlyPackage}, []zipEntry{{Name: "config/first.cfg", Body: "first-config"}}, nil)
	portal := newTestPortal(t, payload, map[string][]byte{onlyPackage.Filename: archive})
	defer portal.Close()
	request.Portal = portal.request.Portal
	portal.packageFault = func(_ string, call int) (*http.Response, error) {
		if call == 1 {
			return &http.Response{StatusCode: http.StatusOK, Status: "200 OK", Body: io.NopCloser(bytes.NewReader(archive[:len(archive)/2]))}, nil
		}
		return nil, nil
	}
	syncer, _ := newSyncerWithoutBackoff(t, portal)

	if changed, err := syncer.syncAuthorized(context.Background(), request, "test-token-123456"); err != nil || !changed {
		t.Fatalf("sync = changed:%t err:%v", changed, err)
	}
	if portal.packageCalls != 2 {
		t.Fatalf("attempts = %d, want 2", portal.packageCalls)
	}
}

// The mirror, with the primary deliberately dead: every Thunderstore attempt fails to
// connect, and the install still completes from the portal's own copy - verified against
// the SHA-256 in the definition exactly as a CDN download would be.
func TestPackageDownloadFallsBackToPortalMirror(t *testing.T) {
	request := profileRequest{World: "world", Profile: "alpha", ClientType: clientFlat}
	onlyPackage, archive := testPackage(t, "author-first-1.0.0.zip", "first.dll", "first")
	payload := testProfileArchive(t, request, []packageDefinition{onlyPackage}, []zipEntry{{Name: "config/first.cfg", Body: "first-config"}}, nil)
	portal := newTestPortal(t, payload, map[string][]byte{onlyPackage.Filename: archive})
	defer portal.Close()
	portal.mirror = true
	request.Portal = portal.request.Portal
	portal.packageFault = func(string, int) (*http.Response, error) {
		return nil, &net.OpError{Op: "dial", Net: "tcp", Err: dialTimeout{}}
	}
	syncer, updates := newSyncerWithoutBackoff(t, portal)

	changed, err := syncer.syncAuthorized(context.Background(), request, "test-token-123456")
	if err != nil || !changed {
		t.Fatalf("sync = changed:%t err:%v", changed, err)
	}
	if portal.packageCalls != packageDownloadAttempts {
		t.Fatalf("Thunderstore attempts = %d, want %d: the primary must be exhausted before the mirror is used", portal.packageCalls, packageDownloadAttempts)
	}
	if portal.mirrorCalls != 1 {
		t.Fatalf("mirror calls = %d, want 1", portal.mirrorCalls)
	}
	root, err := profileRoot(syncer.LocalAppData, request)
	if err != nil {
		t.Fatal(err)
	}
	if data, err := os.ReadFile(filepath.Join(root, "active", "BepInEx", "plugins", "first.dll")); err != nil || string(data) != "first" {
		t.Fatalf("installed plugin = %q, %v", data, err)
	}
	cached := filepath.Join(filepath.Dir(filepath.Dir(root)), "packages", onlyPackage.Filename)
	if err := verifyFile(cached, onlyPackage.Size, onlyPackage.SHA256); err != nil {
		t.Fatalf("mirrored archive failed the published checksum: %v", err)
	}
	announced := false
	for _, update := range *updates {
		if strings.HasPrefix(update.Stage, "Trying another source") {
			announced = true
		}
	}
	if !announced {
		t.Fatalf("the switch to the mirror was not reported: %#v", *updates)
	}
}

// The mirror is not trusted more than the CDN. A mirror answer of the right length and the
// wrong content fails the install rather than being retried or installed.
func TestPortalMirrorWrongBytesAreFatal(t *testing.T) {
	request := profileRequest{World: "world", Profile: "alpha", ClientType: clientFlat}
	onlyPackage, archive := testPackage(t, "author-first-1.0.0.zip", "first.dll", "first")
	payload := testProfileArchive(t, request, []packageDefinition{onlyPackage}, []zipEntry{{Name: "config/first.cfg", Body: "first-config"}}, nil)
	portal := newTestPortal(t, payload, map[string][]byte{onlyPackage.Filename: archive})
	defer portal.Close()
	portal.mirror, portal.mirrorCorrupt = true, true
	request.Portal = portal.request.Portal
	portal.packageFault = func(string, int) (*http.Response, error) {
		return nil, &net.OpError{Op: "dial", Net: "tcp", Err: dialTimeout{}}
	}
	syncer, _ := newSyncerWithoutBackoff(t, portal)

	if _, err := syncer.syncAuthorized(context.Background(), request, "test-token-123456"); err == nil {
		t.Fatal("a mirror answer that fails the published checksum was accepted")
	}
	if portal.mirrorCalls != 1 {
		t.Fatalf("mirror calls = %d, want 1: wrong bytes must not be retried", portal.mirrorCalls)
	}
}

// The shared cache, which is the reason this failure was possible at all: the operator's
// flat edition had already downloaded the file the admin edition then failed to fetch, and
// could not share it. Two editions of one world, one download.
func TestPackageCacheIsSharedBetweenProfiles(t *testing.T) {
	shared, sharedArchive := testPackage(t, "author-shared-1.0.0.zip", "shared.dll", "shared")
	localAppData := t.TempDir()

	flatRequest := profileRequest{World: "world", Profile: "alpha", ClientType: clientFlat}
	flatPayload := testProfileArchive(t, flatRequest, []packageDefinition{shared}, []zipEntry{{Name: "config/first.cfg", Body: "flat"}}, nil)
	flatPortal := newTestPortal(t, flatPayload, map[string][]byte{shared.Filename: sharedArchive})
	defer flatPortal.Close()
	flatRequest.Portal = flatPortal.request.Portal
	flatSyncer := newProfileSyncer(flatPortal.httpClient)
	flatSyncer.LocalAppData = localAppData
	if changed, err := flatSyncer.syncAuthorized(context.Background(), flatRequest, "test-token-123456"); err != nil || !changed {
		t.Fatalf("flat sync = changed:%t err:%v", changed, err)
	}
	if flatPortal.packageCalls != 1 {
		t.Fatalf("flat edition downloads = %d, want 1", flatPortal.packageCalls)
	}

	vrRequest := profileRequest{World: "world", Profile: "beta", ClientType: clientFlat}
	vrPayload := testProfileArchive(t, vrRequest, []packageDefinition{shared}, []zipEntry{{Name: "config/first.cfg", Body: "admin"}}, nil)
	vrPortal := newTestPortal(t, vrPayload, map[string][]byte{shared.Filename: sharedArchive})
	defer vrPortal.Close()
	// A second edition of the same world, so it needs its own release scope rather than
	// the harness default.
	vrRequest.Portal = vrPortal.request.Portal
	vrPortal.request = vrRequest
	vrPortal.manifest = testRemoteManifest(vrRequest, "release-two", vrPayload)
	vrSyncer := newProfileSyncer(vrPortal.httpClient)
	vrSyncer.LocalAppData = localAppData
	if changed, err := vrSyncer.syncAuthorized(context.Background(), vrRequest, "test-token-123456"); err != nil || !changed {
		t.Fatalf("second edition sync = changed:%t err:%v", changed, err)
	}
	if vrPortal.packageCalls != 0 {
		t.Fatalf("second edition downloads = %d, want 0: the cache is not shared", vrPortal.packageCalls)
	}

	// And the first edition's archive survives the second edition's prune, which is the
	// trap a naive shared cache walks into: pruning against one profile's package list
	// deletes every other profile's archives on every sync.
	firstRoot, err := profileRoot(localAppData, flatRequest)
	if err != nil {
		t.Fatal(err)
	}
	cache := filepath.Join(filepath.Dir(filepath.Dir(firstRoot)), "packages")
	if err := verifyFile(filepath.Join(cache, shared.Filename), shared.Size, shared.SHA256); err != nil {
		t.Fatalf("shared archive missing after the second sync pruned: %v", err)
	}
}

// Upgrading must not cost a player their existing downloads. A per-profile cache left by
// an older client is adopted into the shared store, so the first sync after the upgrade
// downloads nothing it already has.
func TestExistingProfileCacheIsAdoptedNotOrphaned(t *testing.T) {
	request := profileRequest{World: "world", Profile: "alpha", ClientType: clientFlat}
	onlyPackage, archive := testPackage(t, "author-first-1.0.0.zip", "first.dll", "first")
	payload := testProfileArchive(t, request, []packageDefinition{onlyPackage}, []zipEntry{{Name: "config/first.cfg", Body: "first-config"}}, nil)
	portal := newTestPortal(t, payload, map[string][]byte{onlyPackage.Filename: archive})
	defer portal.Close()
	request.Portal = portal.request.Portal
	syncer := newProfileSyncer(portal.httpClient)
	syncer.LocalAppData = t.TempDir()
	root, err := profileRoot(syncer.LocalAppData, request)
	if err != nil {
		t.Fatal(err)
	}
	legacy := filepath.Join(root, "packages")
	if err := os.MkdirAll(legacy, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(legacy, onlyPackage.Filename), archive, 0o600); err != nil {
		t.Fatal(err)
	}

	if changed, err := syncer.syncAuthorized(context.Background(), request, "test-token-123456"); err != nil || !changed {
		t.Fatalf("sync = changed:%t err:%v", changed, err)
	}
	if portal.packageCalls != 0 {
		t.Fatalf("downloads = %d, want 0: the existing per-profile cache was orphaned", portal.packageCalls)
	}
	shared := filepath.Join(filepath.Dir(filepath.Dir(root)), "packages", onlyPackage.Filename)
	if err := verifyFile(shared, onlyPackage.Size, onlyPackage.SHA256); err != nil {
		t.Fatalf("adopted archive is not in the shared store: %v", err)
	}
	if _, err := os.Stat(legacy); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("the per-profile cache survived adoption: %v", err)
	}
}

// One corrupt entry in a SHARED store could otherwise be handed to every profile that
// reads it, which is strictly worse than a private cache. So a hit is verified, not
// trusted: a cached file that does not match the published hash is re-downloaded.
func TestCorruptSharedCacheEntryIsRefetched(t *testing.T) {
	request := profileRequest{World: "world", Profile: "alpha", ClientType: clientFlat}
	onlyPackage, archive := testPackage(t, "author-first-1.0.0.zip", "first.dll", "first")
	payload := testProfileArchive(t, request, []packageDefinition{onlyPackage}, []zipEntry{{Name: "config/first.cfg", Body: "first-config"}}, nil)
	portal := newTestPortal(t, payload, map[string][]byte{onlyPackage.Filename: archive})
	defer portal.Close()
	request.Portal = portal.request.Portal
	syncer := newProfileSyncer(portal.httpClient)
	syncer.LocalAppData = t.TempDir()
	cache, err := sharedPackageCache(syncer.LocalAppData)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(cache, 0o700); err != nil {
		t.Fatal(err)
	}
	// Right length, wrong content: the case a size-only check would install.
	if err := os.WriteFile(filepath.Join(cache, onlyPackage.Filename), bytes.Repeat([]byte("x"), len(archive)), 0o600); err != nil {
		t.Fatal(err)
	}

	if changed, err := syncer.syncAuthorized(context.Background(), request, "test-token-123456"); err != nil || !changed {
		t.Fatalf("sync = changed:%t err:%v", changed, err)
	}
	if portal.packageCalls != 1 {
		t.Fatalf("downloads = %d, want 1: a corrupt cache entry was trusted", portal.packageCalls)
	}
	if data, err := os.ReadFile(filepath.Join(cache, onlyPackage.Filename)); err != nil || !bytes.Equal(data, archive) {
		t.Fatalf("cache still holds the corrupt archive (err %v)", err)
	}
	sum := sha256.Sum256(archive)
	if hex.EncodeToString(sum[:]) != onlyPackage.SHA256 {
		t.Fatal("test fixture hash does not match its definition")
	}
}

// What the player is told after an exhausted download. The old text advised checking Steam
// and world access for every failure; on 2026-09-14 both were fine and the cause was one
// unreachable CDN host, which the message never named.
func TestFailureGuidanceNamesTheUnreachableHost(t *testing.T) {
	err := &packageDownloadError{
		Package:  "95Shade-CarryWeightSkill-1.0.1.zip",
		Host:     "gcdn.thunderstore.io",
		Attempts: 4,
		Network:  true,
		err:      &net.OpError{Op: "dial", Net: "tcp", Err: dialTimeout{}},
	}
	guidance := failureGuidance("Profile update stopped", err)

	if !strings.Contains(guidance, "No profile was replaced. Your previous working profile remains available.") {
		t.Fatalf("the survivable-failure promise is gone: %s", guidance)
	}
	for _, wanted := range []string{"gcdn.thunderstore.io", "95Shade-CarryWeightSkill-1.0.1.zip", "network failure", "Try the profile link again"} {
		if !strings.Contains(guidance, wanted) {
			t.Fatalf("guidance does not mention %q: %s", wanted, guidance)
		}
	}
	for _, misleading := range []string{"Steam is signed in", "has access to this world"} {
		if strings.Contains(guidance, misleading) {
			t.Fatalf("guidance still blames %q for a CDN failure: %s", misleading, guidance)
		}
	}
}

// With the primary dead and no mirror answering - a portal that has not mounted the cache,
// which is every deployment until the mount lands - the failure a player reads must still
// blame the CDN host that would not connect, not this world's own server for its 404.
func TestExhaustedDownloadBlamesThePrimaryHost(t *testing.T) {
	request := profileRequest{World: "world", Profile: "alpha", ClientType: clientFlat}
	onlyPackage, archive := testPackage(t, "author-first-1.0.0.zip", "first.dll", "first")
	payload := testProfileArchive(t, request, []packageDefinition{onlyPackage}, []zipEntry{{Name: "config/first.cfg", Body: "first-config"}}, nil)
	portal := newTestPortal(t, payload, map[string][]byte{onlyPackage.Filename: archive})
	defer portal.Close()
	request.Portal = portal.request.Portal
	portal.packageFault = func(string, int) (*http.Response, error) {
		return nil, &net.OpError{Op: "dial", Net: "tcp", Err: dialTimeout{}}
	}
	syncer, _ := newSyncerWithoutBackoff(t, portal)

	_, err := syncer.syncAuthorized(context.Background(), request, "test-token-123456")
	if err == nil {
		t.Fatal("an exhausted download reported success")
	}
	if portal.mirrorCalls != 1 {
		t.Fatalf("mirror calls = %d, want 1: the fallback must be attempted", portal.mirrorCalls)
	}
	var download *packageDownloadError
	if !errors.As(err, &download) {
		t.Fatalf("error does not identify the package download: %v", err)
	}
	if download.Host != "gcdn.thunderstore.io" || !download.Network {
		t.Fatalf("failure blames %q (network:%t), want the unreachable CDN host", download.Host, download.Network)
	}
	if !strings.Contains(failureGuidance("Profile update stopped", err), "gcdn.thunderstore.io") {
		t.Fatalf("guidance does not name the unreachable host: %s", failureGuidance("Profile update stopped", err))
	}
}
