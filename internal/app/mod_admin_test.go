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
		case "profile_catalog":
			// The page reads which servers run the profile before it renders anything about it.
			_, _ = w.Write([]byte(`{"status":"succeeded","output":"","data":[{"world":"Midgard-Redesign","profile":"redesign-alpha","name":"Midgard Redesign","packages":63,"custom_packages":1,"disabled_packages":2,"linked":true}]}`))
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
	if len(operations) != 5 || operations[0].Operation != "profile_catalog" || operations[3].Operation != "mod_search" || operations[4].Operation != "mod_add" || operations[4].Identifier != "Azumatt-AzuCraftyBoxes" {
		t.Fatalf("agent operations = %#v", operations)
	}
}

// A mod change lands in a shared profile, so the page names the profile and every server running
// it. The world in the address is only the host route: an operator who read "Mods · Hrafnheim"
// and edited the profile all four worlds are linked to was told the wrong scope.
func TestModAdminPageNamesTheProfileAndEveryServerLinkedToIt(t *testing.T) {
	server := testServer(t)
	socket := server.agent.socket
	_ = os.Remove(socket)
	listener, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatal(err)
	}
	// Hrafnheim and Doggerland run admin; Storgard runs flat and must not appear as affected.
	running := map[string]string{"Hrafnheim": "admin", "Doggerland": "admin", "Storgard": "flat"}
	for world := range running {
		if err := server.store.UpsertPublicWorld(t.Context(), PublicWorld{
			Name: world, JoinAddress: "valheim.example.test:2456", Status: "offline", ServerVersion: "test",
		}, "test"); err != nil {
			t.Fatal(err)
		}
	}
	mock := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var request agentRequest
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			http.Error(w, "bad request", http.StatusBadRequest)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		switch request.Operation {
		case "profile_catalog":
			rows := make([]map[string]any, 0, 2)
			for _, profile := range []string{"admin", "flat"} {
				rows = append(rows, map[string]any{
					"world": request.World, "profile": profile, "name": profile,
					"packages": 111, "custom_packages": 0, "disabled_packages": 0,
					"linked": running[request.World] == profile,
				})
			}
			data, err := json.Marshal(rows)
			if err != nil {
				t.Errorf("encode catalog: %v", err)
			}
			_, _ = w.Write([]byte(`{"status":"succeeded","output":"","data":` + string(data) + `}`))
		case "mod_inventory":
			_, _ = w.Write([]byte(`{"status":"succeeded","output":"","data":{"world":"Hrafnheim","profile":"admin","packages":[],"disabled_packages":[],"custom_packages":[],"excluded_packages":[]}}`))
		case "mod_custom_list":
			_, _ = w.Write([]byte(`{"status":"succeeded","output":"","data":[]}`))
		default:
			http.Error(w, "unexpected operation", http.StatusBadRequest)
		}
	})}
	go func() { _ = mock.Serve(listener) }()
	t.Cleanup(func() {
		_ = mock.Close()
		_ = listener.Close()
	})

	request := httptest.NewRequest(http.MethodGet, "/admin/mods?world=Hrafnheim&profile=admin", nil)
	request.RemoteAddr = "192.0.2.10:1234"
	request.Header.Set("X-Forwarded-User", "operator")
	request.Header.Set(adminTokenHeader, testAdminToken)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("mod admin = %d: %s", response.Code, response.Body.String())
	}
	page := response.Body.String()
	for _, want := range []string{
		"Mods · profile admin", "Linked servers (2)", "<b>Doggerland</b>", "<b>Hrafnheim</b>",
	} {
		if !strings.Contains(page, want) {
			t.Fatalf("page does not say %q: %s", want, page)
		}
	}
	if strings.Contains(page, "Storgard") {
		t.Fatal("page lists a server that runs another profile as affected")
	}
}

// The world argument stays in the request because host operations are routed per world, and the
// page has to say that rather than read as the scope of the change.
func TestModAdminPageSaysTheWorldArgumentIsARouteNotTheScope(t *testing.T) {
	server := testServer(t)
	socket := server.agent.socket
	_ = os.Remove(socket)
	listener, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatal(err)
	}
	if err := server.store.UpsertPublicWorld(t.Context(), PublicWorld{
		Name: "Hrafnheim", JoinAddress: "valheim.example.test:2456", Status: "offline", ServerVersion: "test",
	}, "test"); err != nil {
		t.Fatal(err)
	}
	mock := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var request agentRequest
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			http.Error(w, "bad request", http.StatusBadRequest)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		switch request.Operation {
		case "profile_catalog":
			// Hrafnheim runs admin, and the page is editing flat.
			_, _ = w.Write([]byte(`{"status":"succeeded","output":"","data":[{"world":"Hrafnheim","profile":"admin","name":"admin","packages":111,"custom_packages":0,"disabled_packages":0,"linked":true},{"world":"Hrafnheim","profile":"flat","name":"flat","packages":101,"custom_packages":0,"disabled_packages":0,"linked":false}]}`))
		case "mod_inventory":
			_, _ = w.Write([]byte(`{"status":"succeeded","output":"","data":{"world":"Hrafnheim","profile":"flat","packages":[],"disabled_packages":[],"custom_packages":[],"excluded_packages":[]}}`))
		case "mod_custom_list":
			_, _ = w.Write([]byte(`{"status":"succeeded","output":"","data":[]}`))
		default:
			http.Error(w, "unexpected operation", http.StatusBadRequest)
		}
	})}
	go func() { _ = mock.Serve(listener) }()
	t.Cleanup(func() {
		_ = mock.Close()
		_ = listener.Close()
	})

	request := httptest.NewRequest(http.MethodGet, "/admin/mods?world=Hrafnheim&profile=flat", nil)
	request.RemoteAddr = "192.0.2.10:1234"
	request.Header.Set("X-Forwarded-User", "operator")
	request.Header.Set(adminTokenHeader, testAdminToken)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("mod admin = %d: %s", response.Code, response.Body.String())
	}
	page := response.Body.String()
	for _, want := range []string{
		"host route this request takes", "No server is linked to this profile",
		"Hrafnheim does not run flat", "Hrafnheim itself runs <b>admin</b>",
	} {
		if !strings.Contains(page, want) {
			t.Fatalf("page does not say %q: %s", want, page)
		}
	}
}

// The operator approving a mod change is approving it for every server linked to the profile.
// "mod_add world=Hrafnheim ..." named one world out of four and read as the scope of the change.
func TestModVerbApprovalSummaryNamesTheProfileAndTheServersItReaches(t *testing.T) {
	server := bridgeServer(t)
	socket := server.agent.socket
	_ = os.Remove(socket)
	listener, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatal(err)
	}
	running := map[string]string{"Hrafnheim": "admin", "Doggerland": "admin", "Storgard": "flat"}
	for world := range running {
		if err := server.store.UpsertPublicWorld(t.Context(), PublicWorld{
			Name: world, JoinAddress: "valheim.example.test:2456", Status: "offline", ServerVersion: "test",
		}, "test"); err != nil {
			t.Fatal(err)
		}
	}
	mock := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var request agentRequest
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil || request.Operation != "profile_catalog" {
			http.Error(w, "unexpected operation", http.StatusBadRequest)
			return
		}
		rows := make([]map[string]any, 0, 2)
		for _, profile := range []string{"admin", "flat"} {
			rows = append(rows, map[string]any{
				"world": request.World, "profile": profile, "name": profile,
				"packages": 111, "custom_packages": 0, "disabled_packages": 0,
				"linked": running[request.World] == profile,
			})
		}
		data, err := json.Marshal(rows)
		if err != nil {
			t.Errorf("encode catalog: %v", err)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"status":"succeeded","output":"","data":` + string(data) + `}`))
	})}
	go func() { _ = mock.Serve(listener) }()
	t.Cleanup(func() {
		_ = mock.Close()
		_ = listener.Close()
	})

	response := bridgePost(t, server, "/api/agent/verb",
		`{"verb":"mod_remove","world":"Hrafnheim","profile":"admin","identifier":"MSchmoecker-VNEI","reason":"needs a typed search box, unusable in a headset"}`)
	if response.Code != http.StatusAccepted {
		t.Fatalf("verb = %d: %s", response.Code, response.Body.String())
	}
	messages, err := server.store.AgentMessages(t.Context(), 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(messages) != 1 {
		t.Fatalf("messages = %+v", messages)
	}
	summary := messages[0].Body
	if !strings.Contains(summary, "Awaiting approval: mod_remove profile=admin servers=Doggerland,Hrafnheim MSchmoecker-VNEI") {
		t.Fatalf("summary does not name the profile and the servers it reaches: %q", summary)
	}
	if strings.Contains(summary, "world=") {
		t.Fatalf("summary still presents the routing world as the scope: %q", summary)
	}
}
