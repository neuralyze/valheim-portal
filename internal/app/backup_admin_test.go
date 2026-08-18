package app

import (
	"encoding/json"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
)

func TestBackupAdminListsOnlyBoundWorldBackupsForTypedRestore(t *testing.T) {
	server := testServer(t)
	if err := server.store.UpsertPublicWorld(t.Context(), PublicWorld{Name: "Midgard-Redesign", JoinAddress: "valheim.example:2456", Status: "online", ServerVersion: "test"}, "test"); err != nil {
		t.Fatal(err)
	}
	serveAgentReply(t, server, "backups", "Midgard-Redesign", AgentReply{Status: "succeeded", Output: "world-Midgard-Redesign-20260727.tgz\nworld-Other-20260727.tgz\n../../escape.tgz\n"})
	request := adminTestRequest(http.MethodGet, "/admin/backups?world=Midgard-Redesign", nil)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("backup page = %d: %s", response.Code, response.Body.String())
	}
	body := response.Body.String()
	if !strings.Contains(body, "world-Midgard-Redesign-20260727.tgz") || !strings.Contains(body, `action="/admin/restores"`) {
		t.Fatalf("recovery action missing: %s", body)
	}
	if strings.Contains(body, "world-Other") || strings.Contains(body, "escape.tgz") {
		t.Fatalf("unbound backup was exposed: %s", body)
	}
}

func TestEmbeddedNeuralyzeLogoIsGreenAndCacheable(t *testing.T) {
	server := testServer(t)
	request := httptest.NewRequest(http.MethodGet, "/assets/neuralyze-logo.svg", nil)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusOK || response.Header().Get("Content-Type") != "image/svg+xml" || response.Header().Get("Cache-Control") == "" {
		t.Fatalf("logo response = %d %#v", response.Code, response.Header())
	}
	if !strings.Contains(response.Body.String(), "#71c492") || strings.Contains(response.Body.String(), "#3056d3") {
		t.Fatal("logo does not use the portal green palette")
	}
}

func TestNewServerPageOffersOnlyAgentCatalogProfiles(t *testing.T) {
	server := testServer(t)
	if err := server.store.UpsertPublicWorld(t.Context(), PublicWorld{Name: "Midgard-Redesign", JoinAddress: "valheim.example:2456", Status: "online", ServerVersion: "test"}, "test"); err != nil {
		t.Fatal(err)
	}
	data := json.RawMessage(`[{"world":"Midgard-Redesign","profile":"redesign-alpha","name":"Midgard Redesign","packages":63,"custom_packages":1,"disabled_packages":2}]`)
	serveAgentReply(t, server, "profile_catalog", "Midgard-Redesign", AgentReply{Status: "succeeded", Data: data})
	request := adminTestRequest(http.MethodGet, "/admin/servers/new", nil)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("new server page = %d: %s", response.Code, response.Body.String())
	}
	// The option value is the profile alone: a profile belongs to no world, and a new
	// server links to it rather than copying a world.
	for _, expected := range []string{`value="redesign-alpha"`, "63 Thunderstore", "1 custom", "2 disabled"} {
		if !strings.Contains(response.Body.String(), expected) {
			t.Fatalf("profile choice missing %q: %s", expected, response.Body.String())
		}
	}
}

func TestPlayerWorldPageDisplaysSeedReadFromFWLMetadata(t *testing.T) {
	server := testServer(t)
	release := Release{ID: "seed-release", World: "Ashlands", Profile: "flat", ClientType: "flat", Version: "1.0.0", Notes: "seed test"}
	publishProfile(t, server, release)
	if err := server.store.GrantWorldAccess(t.Context(), release.World, testSteamID, "admin"); err != nil {
		t.Fatal(err)
	}
	serveAgentReply(t, server, "world_metadata", "Ashlands", AgentReply{Status: "succeeded", Data: json.RawMessage(`{"name":"Ashlands","seed":"SafeSeed123"}`)})
	request := httptest.NewRequest(http.MethodGet, "/worlds/Ashlands", nil)
	request.AddCookie(steamCookie(t, server, testSteamID))
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusOK || !strings.Contains(response.Body.String(), "SafeSeed123") {
		t.Fatalf("world seed page = %d: %s", response.Code, response.Body.String())
	}
}

func serveAgentReply(t *testing.T, server *Server, operation, world string, reply AgentReply) {
	t.Helper()
	socket := server.agent.socket
	_ = os.Remove(socket)
	listener, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatal(err)
	}
	mock := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var request agentRequest
		if json.NewDecoder(r.Body).Decode(&request) != nil || request.Operation != operation || request.World != world {
			http.Error(w, "unexpected request", http.StatusBadRequest)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		if err := json.NewEncoder(w).Encode(reply); err != nil {
			t.Errorf("encode agent reply: %v", err)
		}
	})}
	go func() { _ = mock.Serve(listener) }()
	t.Cleanup(func() {
		_ = mock.Close()
		_ = listener.Close()
	})
}
