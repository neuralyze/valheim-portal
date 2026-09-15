package app

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
)

func decodeClientVersion(t *testing.T, server *Server) (int, clientBuildDescriptor, http.Header) {
	t.Helper()
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/client/version", nil))
	var descriptor clientBuildDescriptor
	if response.Code == http.StatusOK {
		if err := json.Unmarshal(response.Body.Bytes(), &descriptor); err != nil {
			t.Fatalf("decode %q: %v", response.Body.String(), err)
		}
	}
	return response.Code, descriptor, response.Header()
}

// The identity the portal publishes has to be the identity of the bytes it would serve,
// because that is the only comparison the launcher can make that a cache cannot fool.
func TestClientVersionReportsTheDigestOfTheServedBytes(t *testing.T) {
	server := testServer(t)
	server.cfg.ClientExecutable = writePE(t, peSubsystemGUI)

	code, descriptor, header := decodeClientVersion(t, server)
	if code != http.StatusOK {
		t.Fatalf("version endpoint = %d", code)
	}
	if header.Get("Cache-Control") != "no-store" {
		t.Fatalf("freshness answer is cacheable: %q", header.Get("Cache-Control"))
	}

	served := httptest.NewRecorder()
	served.Body.Reset()
	server.Handler().ServeHTTP(served, httptest.NewRequest(http.MethodGet, "/client/ValheimProfileSync.exe", nil))
	sum := sha256.Sum256(served.Body.Bytes())
	if descriptor.SHA256 != hex.EncodeToString(sum[:]) {
		t.Fatalf("published digest %s is not the digest of the served bytes %s", descriptor.SHA256, hex.EncodeToString(sum[:]))
	}
	if descriptor.Size != int64(served.Body.Len()) {
		t.Fatalf("published size %d, served %d", descriptor.Size, served.Body.Len())
	}
	// No sidecar was written, so there is no version to report - and reporting one
	// anyway would be the portal guessing at bytes it did not produce.
	if descriptor.Version != "" {
		t.Fatalf("a version was invented without a sidecar: %q", descriptor.Version)
	}
}

// The version label is only ever as trustworthy as its link to the bytes. A sidecar left
// behind by the previous build must not put the previous build's name on this one - that
// is precisely the mistake that made the 2026-09-14 investigation take an hour.
func TestClientVersionTrustsASidecarOnlyWhenItsDigestMatches(t *testing.T) {
	server := testServer(t)
	executable := writePE(t, peSubsystemGUI)
	server.cfg.ClientExecutable = executable

	payload, err := os.ReadFile(executable)
	if err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(payload)
	digest := hex.EncodeToString(sum[:])

	write := func(body string) {
		t.Helper()
		if err := os.WriteFile(executable+clientBuildSidecarSuffix, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
		// Defeat the size+mtime memo, which is not what this test is about.
		server.clientBuild = clientBuildCache{}
	}

	write(`{"version":"v1.0.1-301-g2b1fc1f","sha256":"` + digest + `"}`)
	if _, descriptor, _ := decodeClientVersion(t, server); descriptor.Version != "v1.0.1-301-g2b1fc1f" {
		t.Fatalf("a matching sidecar was ignored: %q", descriptor.Version)
	}

	write(`{"version":"v0.0.0-stale","sha256":"` + hex.EncodeToString(make([]byte, 32)) + `"}`)
	code, descriptor, _ := decodeClientVersion(t, server)
	if code != http.StatusOK {
		t.Fatalf("a stale sidecar broke the endpoint: %d", code)
	}
	if descriptor.Version != "" {
		t.Fatalf("a sidecar from other bytes named this build: %q", descriptor.Version)
	}
	if descriptor.SHA256 != digest {
		t.Fatalf("digest came from the sidecar rather than the file: %s", descriptor.SHA256)
	}
}

// A client the portal refuses to serve must not be advertised either, or a launcher would
// keep trying to update to bytes it can never fetch.
func TestClientVersionRefusesWhatTheDownloadRouteRefuses(t *testing.T) {
	server := testServer(t)
	server.cfg.ClientExecutable = writePE(t, peSubsystemConsole)
	if code, _, _ := decodeClientVersion(t, server); code != http.StatusServiceUnavailable {
		t.Fatalf("a console build was advertised with %d", code)
	}

	server.cfg.ClientExecutable = writePE(t, peSubsystemGUI) + ".absent"
	if code, _, _ := decodeClientVersion(t, server); code != http.StatusServiceUnavailable {
		t.Fatalf("a missing client was advertised with %d", code)
	}
}

// curl -O ignores Content-Disposition, and curl -O is what the operator used. The bytes
// are therefore also reachable under a path that carries the name, and a request for a
// build that is no longer published is refused rather than silently served something else.
func TestClientNamedDownloadServesOnlyTheBuildItNames(t *testing.T) {
	server := testServer(t)
	server.cfg.ClientExecutable = writePE(t, peSubsystemGUI)

	stable := httptest.NewRecorder()
	server.Handler().ServeHTTP(stable, httptest.NewRequest(http.MethodGet, "/client/ValheimProfileSync.exe", nil))
	sum := sha256.Sum256(stable.Body.Bytes())
	name := "ValheimProfileSync-" + hex.EncodeToString(sum[:])[:12] + ".exe"

	named := httptest.NewRecorder()
	server.Handler().ServeHTTP(named, httptest.NewRequest(http.MethodGet, "/client/download/"+name, nil))
	if named.Code != http.StatusOK {
		t.Fatalf("named download = %d", named.Code)
	}
	if !bytes.Equal(named.Body.Bytes(), stable.Body.Bytes()) {
		t.Fatal("the named path served different bytes from the stable path")
	}

	stale := httptest.NewRecorder()
	server.Handler().ServeHTTP(stale, httptest.NewRequest(http.MethodGet, "/client/download/ValheimProfileSync-000000000000.exe", nil))
	if stale.Code != http.StatusNotFound {
		t.Fatalf("a link naming another build was served with %d", stale.Code)
	}
}
