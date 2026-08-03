package app

import (
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
