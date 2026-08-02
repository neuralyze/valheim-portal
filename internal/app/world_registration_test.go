package app

import (
	"encoding/json"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"regexp"
	"strings"
	"sync"
	"testing"
)

func TestAdminRegistersOnlyAgentCatalogWorldAsDisabled(t *testing.T) {
	server := testServer(t)
	socket := server.agent.socket
	_ = os.Remove(socket)
	listener, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatal(err)
	}
	var mu sync.Mutex
	var requests []agentRequest
	mock := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var request agentRequest
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			http.Error(w, "bad request", http.StatusBadRequest)
			return
		}
		mu.Lock()
		requests = append(requests, request)
		mu.Unlock()
		if request.Operation != "world_catalog" || request.World != "" {
			http.Error(w, "unexpected operation", http.StatusBadRequest)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"status":"succeeded","data":[{"name":"Asgard","port":2456,"status":"online"}]}`))
	})}
	go func() { _ = mock.Serve(listener) }()
	t.Cleanup(func() {
		_ = mock.Close()
		_ = listener.Close()
	})

	pageRequest := httptest.NewRequest(http.MethodGet, "/admin", nil)
	pageRequest.RemoteAddr = "192.0.2.10:1234"
	pageRequest.Header.Set("X-Forwarded-User", "operator")
	pageRequest.Header.Set(adminTokenHeader, testAdminToken)
	page := httptest.NewRecorder()
	server.Handler().ServeHTTP(page, pageRequest)
	if page.Code != http.StatusOK {
		t.Fatalf("admin page = %d: %s", page.Code, page.Body.String())
	}
	for _, want := range []string{"Existing servers", "Asgard", "Register disabled server", "portal.example.test"} {
		if !strings.Contains(page.Body.String(), want) {
			t.Fatalf("admin page missing %q", want)
		}
	}
	csrf := regexp.MustCompile(`name="csrf" value="([^"]+)"`).FindStringSubmatch(page.Body.String())
	if len(csrf) != 2 || len(page.Result().Cookies()) == 0 {
		t.Fatal("admin page did not issue CSRF credentials")
	}
	form := url.Values{
		"csrf": {csrf[1]}, "world": {"Asgard"}, "join_host": {"valheim.example.test"}, "port": {"2456"},
	}
	registerRequest := httptest.NewRequest(http.MethodPost, "/admin/worlds/register", strings.NewReader(form.Encode()))
	registerRequest.RemoteAddr = "192.0.2.10:1234"
	registerRequest.Header.Set("X-Forwarded-User", "operator")
	registerRequest.Header.Set(adminTokenHeader, testAdminToken)
	registerRequest.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	registerRequest.AddCookie(page.Result().Cookies()[0])
	registered := httptest.NewRecorder()
	server.Handler().ServeHTTP(registered, registerRequest)
	if registered.Code != http.StatusSeeOther {
		t.Fatalf("registration = %d: %s", registered.Code, registered.Body.String())
	}
	world, err := server.store.PublicWorld(t.Context(), "Asgard")
	if err != nil {
		t.Fatal(err)
	}
	if world.Enabled || world.Status != "online" || world.JoinAddress != "valheim.example.test:2456" {
		t.Fatalf("registered world = %#v", world)
	}
	mu.Lock()
	defer mu.Unlock()
	if len(requests) != 2 {
		t.Fatalf("catalog request count = %d, want 2", len(requests))
	}
}
