package main

import (
	"context"
	"crypto/tls"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// A session recorded by a launcher that predates VALHEIM_EXPLORATION_DIR lands in the plugin's own
// config directory - inside active/, which the next sync replaces. That report has to be collected
// before it is lost, so both locations are swept.
func TestReportsAreCollectedFromBothLocations(t *testing.T) {
	var received []string
	server := httptest.NewTLSServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.Header.Get("Authorization") != "Bearer test-token-123456" {
			writer.WriteHeader(http.StatusUnauthorized)
			return
		}
		if err := request.ParseMultipartForm(1 << 20); err != nil {
			writer.WriteHeader(http.StatusBadRequest)
			return
		}
		for _, field := range []string{"explored", "pins"} {
			if _, header, err := request.FormFile(field); err == nil {
				received = append(received, field+":"+header.Filename)
			}
		}
		writer.WriteHeader(http.StatusCreated)
	}))
	defer server.Close()

	portalURL, err := url.Parse(server.URL)
	if err != nil {
		t.Fatal(err)
	}
	request := profileRequest{Portal: portalURL, World: "Hrafnheim", Profile: "hrafnheim-nonvr", ClientType: "flat"}
	httpClient := &http.Client{Transport: &http.Transport{TLSClientConfig: &tls.Config{InsecureSkipVerify: true}}}
	client, err := newPortalClient(request, httpClient)
	if err != nil {
		t.Fatal(err)
	}

	active := filepath.Join(t.TempDir(), "profile", "active")
	// One report where the current launcher puts it, one where an older launcher's fallback puts it.
	beside := explorationDirectory(active)
	fallback := filepath.Join(active, "BepInEx", "config", "exploration")
	for _, directory := range []string{beside, fallback} {
		if err := os.MkdirAll(directory, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.WriteFile(filepath.Join(beside, "Hrafnheim-111.explored"), []byte("current"), 0o644); err != nil {
		t.Fatal(err)
	}
	// A negative player id, which is what the game actually stamps for some characters.
	if err := os.WriteFile(filepath.Join(fallback, "Hrafnheim--322254472.explored"), []byte("older"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(fallback, "Hrafnheim--322254472.pins.json"), []byte("{}"), 0o644); err != nil {
		t.Fatal(err)
	}
	// Another world's report is not this profile's to send.
	if err := os.WriteFile(filepath.Join(beside, "Vangard-999.explored"), []byte("elsewhere"), 0o644); err != nil {
		t.Fatal(err)
	}

	uploadExplorationReports(context.Background(), client, request, "test-token-123456", active)

	got := strings.Join(received, " ")
	for _, want := range []string{"explored:Hrafnheim-111.explored", "explored:Hrafnheim--322254472.explored", "pins:Hrafnheim--322254472.pins.json"} {
		if !strings.Contains(got, want) {
			t.Errorf("missing upload %q; got %v", want, received)
		}
	}
	if strings.Contains(got, "Vangard") {
		t.Errorf("another world's report was uploaded: %v", received)
	}
	// Accepted reports are marked, so the next launch does not send an unchanged map again.
	if _, err := os.Stat(filepath.Join(fallback, "Hrafnheim--322254472.explored.sent")); err != nil {
		t.Errorf("no marker written for an accepted report: %v", err)
	}
	before := len(received)
	uploadExplorationReports(context.Background(), client, request, "test-token-123456", active)
	if len(received) != before {
		t.Errorf("unchanged reports were re-sent: %d then %d", before, len(received))
	}
}
