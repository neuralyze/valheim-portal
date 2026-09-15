package app

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// mirroredPackage writes one archive into a server-side cache laid out the way the mod
// manager lays it out - <root>/<source profile>/manager-cache/packages/<Name>-<Version>.zip,
// which is NOT the Thunderstore filename the definition pins - and returns the published
// package that names it.
func mirroredPackage(t *testing.T, root string, body []byte) ProfilePackage {
	t.Helper()
	directory := filepath.Join(root, "ulfsland-admin", "manager-cache", "packages")
	if err := os.MkdirAll(directory, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(directory, "CarryWeightSkill-1.0.1.zip"), body, 0o644); err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(body)
	return ProfilePackage{
		Namespace: "95Shade", Name: "CarryWeightSkill", Version: "1.0.1",
		Filename: "95Shade-CarryWeightSkill-1.0.1.zip",
		SHA256:   hex.EncodeToString(sum[:]), Size: int64(len(body)),
	}
}

func mirrorTestRelease(t *testing.T, server *Server, root string, body []byte) (Release, ProfilePackage) {
	t.Helper()
	release := Release{ID: "ulfsland-admin-1.0.11", World: "Ulfsland", Profile: "ulfsland-vr-flat-admin", ClientType: "flat", Version: "1.0.11"}
	pkg := mirroredPackage(t, root, body)
	publishProfileWithPackages(t, server, release, []ProfilePackage{pkg})
	if err := server.store.GrantWorldAccess(context.Background(), release.World, testSteamID, "member"); err != nil {
		t.Fatal(err)
	}
	return release, pkg
}

func mirrorToken(server *Server, release Release, scope string) string {
	return server.mintDeviceToken(deviceTokenClaims{
		SteamID: testSteamID, World: release.World, Profile: release.Profile, ClientType: release.ClientType,
		ReleaseID: release.ID, Scope: scope, ExpiresAt: time.Now().Add(time.Hour),
	})
}

func mirrorRequest(t *testing.T, server *Server, release Release, digest, token string) *httptest.ResponseRecorder {
	t.Helper()
	target := "/client/package/" + release.World + "/" + release.Profile + "/" + release.ClientType + "/" + digest
	request := httptest.NewRequest(http.MethodGet, target, nil)
	if token != "" {
		request.Header.Set("Authorization", "Bearer "+token)
	}
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	return response
}

// The mirror serves a published package from this host's own cache, so a player whose route
// to one Thunderstore edge is dead can still install. The bytes must be the published ones
// and the response must say which hash they are.
func TestPackageMirrorServesPublishedPackage(t *testing.T) {
	server := testServer(t)
	root := t.TempDir()
	server.cfg.PackageCacheRoot = root
	body := bytes.Repeat([]byte("mod-archive"), 64)
	release, pkg := mirrorTestRelease(t, server, root, body)

	response := mirrorRequest(t, server, release, pkg.SHA256, mirrorToken(server, release, deviceTokenScopeProfile))

	if response.Code != http.StatusOK {
		t.Fatalf("mirror = %d: %s", response.Code, response.Body.String())
	}
	if !bytes.Equal(response.Body.Bytes(), body) {
		t.Fatalf("mirror served %d bytes, want the %d published ones", response.Body.Len(), len(body))
	}
	if got := response.Header().Get("X-Checksum-SHA256"); got != pkg.SHA256 {
		t.Fatalf("checksum header = %q, want %q", got, pkg.SHA256)
	}
}

// What the mirror must refuse. The endpoint is a second source for one release's packages
// and nothing else: without the profile-scoped device token it serves nothing at all, and
// with one it will not hand over bytes that release did not publish - so an unauthenticated
// caller cannot enumerate this host's mod cache, and an authorised one cannot walk out of
// its own release.
func TestPackageMirrorRefusesWhatItShouldNotServe(t *testing.T) {
	server := testServer(t)
	root := t.TempDir()
	server.cfg.PackageCacheRoot = root
	body := bytes.Repeat([]byte("mod-archive"), 64)
	release, pkg := mirrorTestRelease(t, server, root, body)

	// An archive that is in the cache but in no published definition. It is reachable on
	// disk and must still be unreachable over HTTP.
	unpublished := bytes.Repeat([]byte("private"), 32)
	unpublishedSum := sha256.Sum256(unpublished)
	directory := filepath.Join(root, "hrafnheim-admin", "manager-cache", "packages")
	if err := os.MkdirAll(directory, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(directory, "Secret-9.9.9.zip"), unpublished, 0o644); err != nil {
		t.Fatal(err)
	}

	for _, testCase := range []struct {
		name   string
		digest string
		token  string
		want   int
	}{
		{name: "no token", digest: pkg.SHA256, token: "", want: http.StatusUnauthorized},
		{name: "wrong scope", digest: pkg.SHA256, token: mirrorToken(server, release, deviceTokenScopeExploration), want: http.StatusUnauthorized},
		{name: "cached but unpublished", digest: hex.EncodeToString(unpublishedSum[:]), token: mirrorToken(server, release, deviceTokenScopeProfile), want: http.StatusNotFound},
	} {
		t.Run(testCase.name, func(t *testing.T) {
			if response := mirrorRequest(t, server, release, testCase.digest, testCase.token); response.Code != testCase.want {
				t.Fatalf("mirror = %d, want %d: %s", response.Code, testCase.want, response.Body.String())
			}
		})
	}

	// And with no cache mounted the route is simply absent, which is every deployment
	// that has not opted in.
	server.cfg.PackageCacheRoot = ""
	if response := mirrorRequest(t, server, release, pkg.SHA256, mirrorToken(server, release, deviceTokenScopeProfile)); response.Code != http.StatusNotFound {
		t.Fatalf("unconfigured mirror = %d, want 404", response.Code)
	}
}

// The cache on disk is not authority. A file sitting where a published package should be,
// with the right size but the wrong content, must not be served - the portal proves the
// hash before it hands bytes to a client that is about to trust them.
func TestPackageMirrorRefusesCacheThatFailsTheHash(t *testing.T) {
	server := testServer(t)
	root := t.TempDir()
	server.cfg.PackageCacheRoot = root
	body := bytes.Repeat([]byte("mod-archive"), 64)
	release, pkg := mirrorTestRelease(t, server, root, body)
	corrupt := bytes.Repeat([]byte("x"), len(body))
	if err := os.WriteFile(filepath.Join(root, "ulfsland-admin", "manager-cache", "packages", "CarryWeightSkill-1.0.1.zip"), corrupt, 0o644); err != nil {
		t.Fatal(err)
	}

	if response := mirrorRequest(t, server, release, pkg.SHA256, mirrorToken(server, release, deviceTokenScopeProfile)); response.Code != http.StatusNotFound {
		t.Fatalf("mirror served unverified bytes: %d %q", response.Code, response.Body.String())
	}
}
