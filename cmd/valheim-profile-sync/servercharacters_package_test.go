// TEMPORARY: local ServerCharacters build. Delete this file with the feature.
package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/neuralyze/valheim-portal/internal/servercharacters"
)

func embeddedDefinition(t *testing.T) (packageDefinition, []byte) {
	t.Helper()
	body, err := servercharacters.Archive()
	if err != nil {
		t.Skipf("archive not built: %v", err)
	}
	sum := sha256.Sum256(body)
	return packageDefinition{
		Namespace: servercharacters.Namespace,
		Name:      servercharacters.Name,
		Version:   servercharacters.Version,
		Filename:  servercharacters.ArchiveFileName,
		SHA256:    hex.EncodeToString(sum[:]),
		Size:      int64(len(body)),
	}, body
}

// The package the client carries is installed from the executable, with no request to any
// CDN: the transport here fails the test on any request at all. That is the property the
// whole arrangement rests on - the server and every client run bytes that came from one
// build, so ServerSync's MinimumRequiredVersion cannot be unsatisfiable.
func TestEmbeddedServerCharactersIsInstalledWithoutADownload(t *testing.T) {
	definition, want := embeddedDefinition(t)
	cache := filepath.Join(t.TempDir(), "packages")
	syncer := &profileSyncer{HTTPClient: &http.Client{Transport: refusingRoundTripper{t}}}

	path, downloaded, err := syncer.ensureCachedPackage(context.Background(), cache, definition, packageMirror{}, newRetryAllowance())
	if err != nil {
		t.Fatal(err)
	}
	if !downloaded {
		t.Fatal("install was reported as a cache hit on an empty cache")
	}
	if filepath.Base(path) != servercharacters.ArchiveFileName {
		t.Fatalf("cached as %q, want the definition's filename", filepath.Base(path))
	}
	got, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if sha256.Sum256(got) != sha256.Sum256(want) {
		t.Fatal("installed bytes are not the embedded archive")
	}

	// Where the assembly ends up decides whether BepInEx loads the mod from its own folder.
	// The archive is Thunderstore-shaped for exactly this reason: extractPackageArchive maps
	// a root-level file into BepInEx/plugins/<namespace>-<name>/, and a build.sh that
	// packaged the DLL under some other prefix would install a tree the mod cannot resolve
	// its own directory from.
	installed := t.TempDir()
	if err := extractPackageArchive(path, installed, definition); err != nil {
		t.Fatal(err)
	}
	plugin := filepath.Join(installed, "BepInEx", "plugins",
		servercharacters.Namespace+"-"+servercharacters.Name, "ServerCharacters.dll")
	if _, err := os.Stat(plugin); err != nil {
		t.Fatalf("ServerCharacters.dll is not at BepInEx/plugins/%s-%s/: %v",
			servercharacters.Namespace, servercharacters.Name, err)
	}
}

// A client whose embedded build is not the one the definition was published from must say
// so. Silently installing its own build would put a different assembly on the client than
// the server runs, which ServerSync refuses at the handshake - the failure that is
// indistinguishable, from the player's side, from the server being broken.
func TestEmbeddedServerCharactersRefusesADefinitionItCannotSatisfy(t *testing.T) {
	definition, _ := embeddedDefinition(t)
	definition.SHA256 = strings.Repeat("a", 64)
	cache := filepath.Join(t.TempDir(), "packages")
	syncer := &profileSyncer{HTTPClient: &http.Client{Transport: refusingRoundTripper{t}}}

	_, _, err := syncer.ensureCachedPackage(context.Background(), cache, definition, packageMirror{}, newRetryAllowance())
	if err == nil {
		t.Fatal("a definition naming a different build was installed anyway")
	}
	if !strings.Contains(err.Error(), "download the current client") {
		t.Fatalf("err = %v, want the message that names the fix", err)
	}
}

type refusingRoundTripper struct{ t *testing.T }

func (transport refusingRoundTripper) RoundTrip(request *http.Request) (*http.Response, error) {
	transport.t.Errorf("unexpected request to %s", request.URL)
	return nil, fmt.Errorf("no requests expected")
}
