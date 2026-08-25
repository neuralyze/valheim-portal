package app

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The source offer exists to satisfy AGPL-3.0 section 13, which is owed to the
// people who use the service over a network. Those are the player-facing pages.
func TestPlayerPagesOfferTheSource(t *testing.T) {
	server := testServer(t)
	server.cfg.SourceURL = "https://example.invalid/fork/valheim-portal"

	for _, page := range []struct {
		name    string
		request *http.Request
	}{
		{"login", httptest.NewRequest(http.MethodGet, "/", nil)},
		{"home", signedIn(t, server, http.MethodGet, "/", playerSteam)},
	} {
		response := httptest.NewRecorder()
		server.Handler().ServeHTTP(response, page.request)
		body := response.Body.String()
		if !strings.Contains(body, server.cfg.SourceURL) {
			t.Fatalf("%s page does not offer the configured source URL", page.name)
		}
		if !strings.Contains(body, "portal-source-link") {
			t.Fatalf("%s page has no source link", page.name)
		}
	}
}

// A fork must be able to point the offer at its own modified source, otherwise
// the link is a false statement about what is running.
func TestSourceOfferUsesTheConfiguredURL(t *testing.T) {
	server := testServer(t)
	server.cfg.SourceURL = "https://git.example.invalid/someone/their-fork"

	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/", nil))
	body := response.Body.String()
	if !strings.Contains(body, "https://git.example.invalid/someone/their-fork") {
		t.Fatal("the source offer ignored the configured URL")
	}
	if strings.Contains(body, "github.com/neuralyze/valheim-portal") {
		t.Fatal("the source offer leaked the upstream default over the configured URL")
	}
}

// The administration pages are operator tooling and carry their own header. The
// offer belongs on the pages remote users actually reach.
func TestAdministrationPagesDoNotCarryTheSourceLink(t *testing.T) {
	server := testServer(t)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, adminTestRequest(http.MethodGet, "/admin", nil))
	if strings.Contains(response.Body.String(), "portal-source-link") {
		t.Fatal("the administration page carries the player-facing source link")
	}
}

func TestSourceURLDefaultsToTheCanonicalRepository(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("PORTAL_CSRF_SECRET_FILE", filepath.Join(dir, "csrf"))
	t.Setenv("PORTAL_AGENT_TOKEN_FILE", filepath.Join(dir, "agent"))
	t.Setenv("PORTAL_TRUSTED_PROXY_CIDR", "192.0.2.0/24")
	t.Setenv("PORTAL_PUBLIC_BASE_URL", "https://portal.example.test")
	t.Setenv("PORTAL_SOURCE_URL", "")

	config, err := LoadConfig()
	if err != nil {
		t.Fatal(err)
	}
	if config.SourceURL != "https://github.com/neuralyze/valheim-portal" {
		t.Fatalf("default source URL = %q", config.SourceURL)
	}
}

// An offer a browser cannot follow looks discharged while telling the reader
// nothing, so a malformed value has to stop the portal rather than ship.
func TestLoadConfigRejectsAnUnusableSourceURL(t *testing.T) {
	for _, value := range []string{"not a url", "/relative/path", "ftp://example.invalid/repo", "example.invalid/repo"} {
		dir := t.TempDir()
		t.Setenv("PORTAL_CSRF_SECRET_FILE", filepath.Join(dir, "csrf"))
		t.Setenv("PORTAL_AGENT_TOKEN_FILE", filepath.Join(dir, "agent"))
		t.Setenv("PORTAL_TRUSTED_PROXY_CIDR", "192.0.2.0/24")
		t.Setenv("PORTAL_PUBLIC_BASE_URL", "https://portal.example.test")
		t.Setenv("PORTAL_SOURCE_URL", value)

		if _, err := LoadConfig(); err == nil || !strings.Contains(err.Error(), "PORTAL_SOURCE_URL") {
			t.Fatalf("source URL %q was accepted, error = %v", value, err)
		}
	}
}

// The mark is Octicons' mark-github, MIT licensed and owned by GitHub. Shipping
// it without recording that is the mistake this project already made once with
// the VR runtime assemblies.
func TestNoticeRecordsTheGitHubMark(t *testing.T) {
	notice, err := os.ReadFile("../../NOTICE")
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"Octicons", "MIT"} {
		if !strings.Contains(string(notice), want) {
			t.Fatalf("NOTICE does not record %q for the inlined GitHub mark", want)
		}
	}
}

// The GPL-3.0 offer for ValheimVRMod.dll is owed by whoever conveys the binary, so it
// has to be on the surfaces that convey it: the release page, whose links ARE the
// download, and the world page, whose "Install or update" starts the sync that fetches
// the same bytes. An offer on a page nobody downloading it will see is not compliance.
func TestPagesThatHandOutValheimVROfferItsSource(t *testing.T) {
	server := testServer(t)
	server.cfg.VHVRSourceURL = "https://git.example.invalid/neuralyze/vhvr-fork"
	threeProfileWorld(t, server)
	// threeProfileWorld's "flatvr" carries ValheimVR as a Thunderstore package, which is a
	// shape no live Flat edition has: read from the published hrafnheim-vr-flat 1.0.6
	// definition, it names 105 packages, none of them ValheimVR, and reaches the mod
	// through companion-normalised.zip. Our compiled DLL travels in the companion and the
	// VR runtime, so the release fixture has to be a release that carries one.
	companion := Release{ID: "flatvr-companion", World: describedWorld, Profile: "midgard-vr-flat", ClientType: "flat", Version: "2.1.4"}
	publishProfileDefinition(t, server, companion, func(manifest *ProfileManifest) {
		manifest.Packages = []ProfilePackage{ordinaryPackage}
		manifest.Companion = writeFlatCompanionArtifact(t, server, companion)
	})

	for _, page := range []struct{ name, target string }{
		{"world", "/worlds/" + describedWorld},
		{"flat release", "/releases/" + companion.ID},
		// publishProfileDefinition attaches a vr_runtime to every client_type "vr" release.
		{"vr release", "/releases/vr"},
	} {
		body := playerPage(t, server, page.target)
		if !strings.Contains(body, server.cfg.VHVRSourceURL) {
			t.Fatalf("%s page hands out ValheimVRMod.dll without offering its source: %s", page.name, body)
		}
		for _, want := range []string{"GPL-3.0", "neuralyze/local", "SHA-256"} {
			if !strings.Contains(body, want) {
				t.Fatalf("%s page's offer does not say %q: %s", page.name, want, body)
			}
		}
	}

	// The archived copy of the same release is the same download by another route, and
	// GPL-3.0 does not stop applying because a release was superseded.
	if err := server.store.Archive(t.Context(), companion.ID, "admin"); err != nil {
		t.Fatal(err)
	}
	if body := playerPage(t, server, "/history/releases/"+companion.ID); !strings.Contains(body, server.cfg.VHVRSourceURL) {
		t.Fatalf("the archived release still hands out the DLL and drops the offer: %s", body)
	}
}

// The pages are only half the surface. Four routes move the actual bytes -
// /artifacts/{id}, /history/artifacts/{id}, /client/companion/... and
// /client/runtime/... - and the last two are fetched by the Windows launcher with no
// page anywhere in the exchange. They all funnel through serveArtifact, so the offer
// rides on the response that carries the object code, which is what GPL-3.0 section 6
// asks for. Enumerated from the routing table rather than from whichever page failed a
// test: a surface we miss is an obligation we fail while believing we have met it.
func TestTheArtifactDownloadItselfCarriesTheOffer(t *testing.T) {
	server := testServer(t)
	server.cfg.VHVRSourceURL = "https://git.example.invalid/neuralyze/vhvr-fork"
	describedTestWorld(t, server)
	release := Release{ID: "vr-flat-dl", World: describedWorld, Profile: "midgard-vr-flat", ClientType: "flat", Version: "1.0.0"}
	publishProfileDefinition(t, server, release, func(manifest *ProfileManifest) {
		manifest.Packages = []ProfilePackage{ordinaryPackage}
		manifest.Companion = writeFlatCompanionArtifact(t, server, release)
	})

	response := playerResponse(t, server, "/artifacts/"+release.ID+"-companion")
	if got := response.Header().Get("X-ValheimVR-Source"); got != server.cfg.VHVRSourceURL {
		t.Fatalf("the companion download carries X-ValheimVR-Source = %q", got)
	}
	if got := response.Header().Get("X-ValheimVR-License"); got != "GPL-3.0" {
		t.Fatalf("the companion download carries X-ValheimVR-License = %q", got)
	}

	// Control: the same route, the same code path, an artifact with no GPL binary in it.
	// Without this the assertion above would also pass if the header were unconditional.
	profile := playerResponse(t, server, "/artifacts/"+release.ID+"-profile")
	if got := profile.Header().Get("X-ValheimVR-Source"); got != "" {
		t.Fatalf("a profile definition download claims to contain ValheimVR: %q", got)
	}
	if got := profile.Header().Get("X-Checksum-SHA256"); got == "" {
		t.Fatal("the control download did not go through serveArtifact at all")
	}
}

// The launcher reads the manifest before it fetches anything, so the machine-readable
// description of a release that contains ValheimVRMod.dll says where its source is.
func TestTheReleaseManifestNamesTheValheimVRSource(t *testing.T) {
	server := testServer(t)
	server.cfg.VHVRSourceURL = "https://git.example.invalid/neuralyze/vhvr-fork"
	threeProfileWorld(t, server)

	var carrying map[string]any
	if err := json.Unmarshal([]byte(playerPage(t, server, "/releases/vr/manifest.json")), &carrying); err != nil {
		t.Fatal(err)
	}
	if carrying["valheimvr_source"] != server.cfg.VHVRSourceURL || carrying["valheimvr_license"] != "GPL-3.0" {
		t.Fatalf("a release carrying the VR runtime does not name its source: %#v", carrying)
	}

	// Control: same handler, same shape, a release with no ValheimVR binary. The key is
	// absent rather than empty, so a reader cannot read it as an unconfigured deployment.
	var without map[string]any
	if err := json.Unmarshal([]byte(playerPage(t, server, "/releases/nonvr/manifest.json")), &without); err != nil {
		t.Fatal(err)
	}
	if _, present := without["valheimvr_source"]; present {
		t.Fatalf("a release with no ValheimVR binary names a ValheimVR source: %#v", without)
	}
	if without["release_id"] != "nonvr" {
		t.Fatalf("the control manifest is not the one under test: %#v", without)
	}
}

// A licence notice about somebody else's program, on a download that does not contain
// it, is a false statement rather than an abundance of caution. "midgard-nonvr" strips
// ValheimVR, and its release carries only a profile definition.
func TestAReleaseWithoutValheimVRCarriesNoOffer(t *testing.T) {
	server := testServer(t)
	server.cfg.VHVRSourceURL = "https://git.example.invalid/neuralyze/vhvr-fork"
	threeProfileWorld(t, server)

	body := playerPage(t, server, "/releases/nonvr")
	if strings.Contains(body, server.cfg.VHVRSourceURL) {
		t.Fatalf("a release with no ValheimVR binary offers ValheimVR source: %s", body)
	}
	// Control: the same page does render, and does list its artifact.
	if !strings.Contains(body, "Profile definition") {
		t.Fatalf("the control page did not render at all: %s", body)
	}
}

// The two offers are for two different programs under two different licences. Pointing
// the ValheimVR offer at the portal's own repository would send a player looking for VR
// source to a Go web application, which is worse than silence: it looks discharged.
func TestTheValheimVROfferIsNotThePortalsOwnSourceOffer(t *testing.T) {
	server := testServer(t)
	server.cfg.SourceURL = "https://git.example.invalid/someone/portal-fork"
	server.cfg.VHVRSourceURL = "https://git.example.invalid/neuralyze/vhvr-fork"
	threeProfileWorld(t, server)

	body := playerPage(t, server, "/releases/vr")
	if strings.Contains(body, server.cfg.SourceURL) {
		t.Fatalf("the release page's ValheimVR offer leaked the portal's own source URL: %s", body)
	}
	if !strings.Contains(body, server.cfg.VHVRSourceURL) {
		t.Fatalf("the release page does not carry the ValheimVR offer at all: %s", body)
	}
}

func TestVHVRSourceURLDefaultsToTheFork(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("PORTAL_CSRF_SECRET_FILE", filepath.Join(dir, "csrf"))
	t.Setenv("PORTAL_AGENT_TOKEN_FILE", filepath.Join(dir, "agent"))
	t.Setenv("PORTAL_TRUSTED_PROXY_CIDR", "192.0.2.0/24")
	t.Setenv("PORTAL_PUBLIC_BASE_URL", "https://portal.example.test")
	t.Setenv("PORTAL_VHVR_SOURCE_URL", "")

	config, err := LoadConfig()
	if err != nil {
		t.Fatal(err)
	}
	if config.VHVRSourceURL != "https://github.com/neuralyze/vhvr-mod" {
		t.Fatalf("default ValheimVR source URL = %q", config.VHVRSourceURL)
	}
}

// Same reasoning as the portal's own offer: a link a browser cannot follow looks
// discharged while telling the reader nothing, so it stops the portal rather than ships.
func TestLoadConfigRejectsAnUnusableVHVRSourceURL(t *testing.T) {
	for _, value := range []string{"not a url", "/relative/path", "ftp://example.invalid/repo", "example.invalid/repo"} {
		dir := t.TempDir()
		t.Setenv("PORTAL_CSRF_SECRET_FILE", filepath.Join(dir, "csrf"))
		t.Setenv("PORTAL_AGENT_TOKEN_FILE", filepath.Join(dir, "agent"))
		t.Setenv("PORTAL_TRUSTED_PROXY_CIDR", "192.0.2.0/24")
		t.Setenv("PORTAL_PUBLIC_BASE_URL", "https://portal.example.test")
		t.Setenv("PORTAL_VHVR_SOURCE_URL", value)

		if _, err := LoadConfig(); err == nil || !strings.Contains(err.Error(), "PORTAL_VHVR_SOURCE_URL") {
			t.Fatalf("ValheimVR source URL %q was accepted, error = %v", value, err)
		}
	}
}
