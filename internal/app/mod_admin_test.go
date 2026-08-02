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

func TestModAdminSearchAndSelectionUseTypedAgentOperations(t *testing.T) {
	server := testServer(t)
	socket := server.agent.socket
	_ = os.Remove(socket)
	listener, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatal(err)
	}
	if err := server.store.UpsertPublicWorld(t.Context(), PublicWorld{
		Name: "Midgard-Redesign", JoinAddress: "valheim.example.test:2456", Status: "offline", ServerVersion: "test",
	}, "test"); err != nil {
		t.Fatal(err)
	}
	var mu sync.Mutex
	var operations []agentRequest
	mock := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var request agentRequest
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			http.Error(w, "bad request", http.StatusBadRequest)
			return
		}
		mu.Lock()
		operations = append(operations, request)
		mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		switch request.Operation {
		case "mod_inventory":
			_, _ = w.Write([]byte(`{"status":"succeeded","output":"","data":{"world":"Midgard-Redesign","profile":"Redesign","packages":[{"identifier":"Azumatt-AzuCraftyBoxes","version":"1.8.14","scope":"shared","enabled":true,"source":"thunderstore"}],"disabled_packages":[],"custom_packages":[],"excluded_packages":[]}}`))
		case "mod_custom_list":
			_, _ = w.Write([]byte(`{"status":"succeeded","output":"","data":[{"id":"fixes/BackpacksVRFix.zip","filename":"BackpacksVRFix.zip","size":27180,"sha256":"abc123","description":"VR body tracking compatibility fix","dlls":["BepInEx/plugins/BackpacksVRFix.dll"],"selected":false,"scope":"client-only","enabled":false}]}`))
		case "mod_search":
			_, _ = w.Write([]byte(`{"status":"succeeded","output":"","data":[{"identifier":"Azumatt-AzuCraftyBoxes","name":"AzuCraftyBoxes","owner":"Azumatt","description":"Craft from nearby containers","version":"1.8.14","versions":["1.8.14","1.8.13"],"dependencies":["denikson-BepInExPack_Valheim-5.4.2202"],"categories":["Server-side"],"icon":"https://gcdn.thunderstore.io/live/repository/icons/Azumatt-AzuCraftyBoxes-1.8.14.png","website":"https://example.test/mod","downloads":100,"rating":5,"deprecated":false}]}`))
		case "mod_add":
			_, _ = w.Write([]byte(`{"status":"succeeded","output":"added=Azumatt-AzuCraftyBoxes"}`))
		default:
			http.Error(w, "unexpected operation", http.StatusBadRequest)
		}
	})}
	go func() { _ = mock.Serve(listener) }()
	t.Cleanup(func() {
		_ = mock.Close()
		_ = listener.Close()
	})

	request := httptest.NewRequest(http.MethodGet, "/admin/mods?world=Midgard-Redesign&profile=redesign-alpha&q=craft", nil)
	request.RemoteAddr = "192.0.2.10:1234"
	request.Header.Set("X-Forwarded-User", "operator")
	request.Header.Set(adminTokenHeader, testAdminToken)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("mod admin = %d: %s", response.Code, response.Body.String())
	}
	for _, want := range []string{"AzuCraftyBoxes", "Craft from nearby containers", "denikson-BepInExPack_Valheim", "BackpacksVRFix.zip", "VR body tracking compatibility fix"} {
		if !strings.Contains(response.Body.String(), want) {
			t.Fatalf("mod admin page missing %q", want)
		}
	}
	if !strings.Contains(response.Header().Get("Content-Security-Policy"), "img-src 'self' data: https://gcdn.thunderstore.io") {
		t.Fatalf("Thunderstore image origin missing from CSP: %q", response.Header().Get("Content-Security-Policy"))
	}
	if !strings.Contains(response.Body.String(), `src="https://gcdn.thunderstore.io/live/repository/icons/Azumatt-AzuCraftyBoxes-1.8.14.png"`) {
		t.Fatal("Thunderstore search result icon URL missing")
	}
	match := regexp.MustCompile(`name="csrf" value="([^"]+)"`).FindStringSubmatch(response.Body.String())
	if len(match) != 2 || len(response.Result().Cookies()) == 0 {
		t.Fatal("mod admin response did not issue CSRF credentials")
	}
	form := url.Values{
		"csrf": {match[1]}, "world": {"Midgard-Redesign"}, "profile": {"redesign-alpha"},
		"action": {"add"}, "identifier": {"Azumatt-AzuCraftyBoxes"}, "version": {"1.8.14"}, "scope": {"shared"},
	}
	selection := httptest.NewRequest(http.MethodPost, "/admin/mods/action", strings.NewReader(form.Encode()))
	selection.RemoteAddr = "192.0.2.10:1234"
	selection.Header.Set("X-Forwarded-User", "operator")
	selection.Header.Set(adminTokenHeader, testAdminToken)
	selection.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	selection.AddCookie(response.Result().Cookies()[0])
	selected := httptest.NewRecorder()
	server.Handler().ServeHTTP(selected, selection)
	if selected.Code != http.StatusSeeOther {
		t.Fatalf("mod selection = %d: %s", selected.Code, selected.Body.String())
	}
	mu.Lock()
	defer mu.Unlock()
	if len(operations) != 4 || operations[2].Operation != "mod_search" || operations[3].Operation != "mod_add" || operations[3].Identifier != "Azumatt-AzuCraftyBoxes" {
		t.Fatalf("agent operations = %#v", operations)
	}
}
