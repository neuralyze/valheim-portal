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

func TestAdminHomeUsesWorldBoundProfileAutofillAndAuthoritativeDefaults(t *testing.T) {
	server := testServer(t)
	for _, world := range []PublicWorld{
		{Name: "Other", JoinAddress: "valheim.example:3456", Status: "offline", ServerVersion: "test"},
		{Name: "Midgard", JoinAddress: "valheim.example:2456", Status: "maintenance", ServerVersion: "test"},
	} {
		if err := server.store.UpsertPublicWorld(t.Context(), world, "test"); err != nil {
			t.Fatal(err)
		}
	}
	for _, release := range []Release{
		{ID: "draft-midgard-flat", World: "Midgard", Profile: "redesign-alpha", ClientType: "flat", Version: "1.0.0"},
		{ID: "archived-other-flat", World: "Other", Profile: "other-flat", ClientType: "flat", Version: "1.0.0"},
	} {
		if err := server.store.CreateRelease(t.Context(), release, "test"); err != nil {
			t.Fatal(err)
		}
	}
	if err := server.store.ArchiveDraft(t.Context(), "archived-other-flat", "test"); err != nil {
		t.Fatal(err)
	}

	serveAdminAgentCatalog(t, server, map[string]json.RawMessage{
		"Other": json.RawMessage(`[{"world":"Other","profile":"other-flat","name":"Other Flat","packages":4,"custom_packages":0,"disabled_packages":1}]`),
		"Midgard": json.RawMessage(`[
			{"world":"Midgard","profile":"redesign-alpha","name":"Midgard Redesign","packages":63,"custom_packages":1,"disabled_packages":2},
			{"world":"Other","profile":"wrong-world","name":"Wrong World","packages":1,"custom_packages":0,"disabled_packages":0},
			{"world":"Midgard","profile":"invalid slug","name":"Invalid","packages":1,"custom_packages":0,"disabled_packages":0}
		]`),
	})

	request := adminTestRequest(http.MethodGet, "/admin", nil)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("admin page = %d: %s", response.Code, response.Body.String())
	}
	page := response.Body.String()
	for _, expected := range []string{
		`data-world="Other" value="other-flat"`,
		`data-world="Midgard" value="redesign-alpha"`,
		`name="profile" list="mod-profile-slugs" data-profile-slug`,
		`name="profile" list="release-profiles-Midgard"`,
		`<input type="hidden" name="world" value="Midgard">`,
		// A profile is shared now: the copy must not imply it belongs to the world.
		`A shared profile: every server linked to it runs this mod set.`,
		`pattern="[A-Za-z0-9][A-Za-z0-9._-]{0,79}"`,
		`src="/assets/admin-profile-autofill.js"`,
		`value="draft-midgard-flat" label="redesign-alpha / flat / 1.0.0"`,
		// online/offline are measured now, so the operator's only choice is whether
		// to announce maintenance over the top of the live state.
		`<option value="online" selected>Automatic - live server check</option>`,
		`<option value="maintenance" selected>Maintenance</option>`,
		`name="port" value="3456"`,
		`name="port" value="2456"`,
	} {
		if !strings.Contains(page, expected) {
			t.Fatalf("admin page missing %q: %s", expected, page)
		}
	}
	for _, excluded := range []string{"wrong-world", "invalid slug", `<select name="profile"`} {
		if strings.Contains(page, excluded) {
			t.Fatalf("admin page exposed or restricted profile value %q: %s", excluded, page)
		}
	}
	draftListStart := strings.Index(page, `<datalist id="draft-releases-Midgard">`)
	if draftListStart < 0 {
		t.Fatalf("draft release choices missing: %s", page)
	}
	draftListEnd := strings.Index(page[draftListStart:], `</datalist>`)
	if draftListEnd < 0 {
		t.Fatalf("draft release choices are incomplete: %s", page)
	}
	if draftList := page[draftListStart : draftListStart+draftListEnd]; strings.Contains(draftList, "archived-other-flat") {
		t.Fatalf("archived release offered for artifact upload: %s", draftList)
	}
}

func TestAdminProfileAutofillAssetUpdatesChoicesWithoutOverwritingEntry(t *testing.T) {
	server := testServer(t)
	request := httptest.NewRequest(http.MethodGet, "/assets/admin-profile-autofill.js", nil)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusOK || response.Header().Get("Content-Type") != "text/javascript; charset=utf-8" {
		t.Fatalf("autofill asset = %d %#v", response.Code, response.Header())
	}
	body := response.Body.String()
	for _, behavior := range []string{
		"choice.dataset.world === world.value",
		"world.addEventListener('change', updateChoices)",
		"list.replaceChildren(matching)",
	} {
		if !strings.Contains(body, behavior) {
			t.Fatalf("autofill asset missing behavior %q: %s", behavior, body)
		}
	}
	if strings.Contains(body, "profile.value =") {
		t.Fatalf("autofill asset overwrites free profile entry: %s", body)
	}
}

func serveAdminAgentCatalog(t *testing.T, server *Server, catalogs map[string]json.RawMessage) {
	t.Helper()
	socket := server.agent.socket
	_ = os.Remove(socket)
	listener, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatal(err)
	}
	mock := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var request agentRequest
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			http.Error(w, "invalid request", http.StatusBadRequest)
			return
		}
		var reply AgentReply
		switch request.Operation {
		case "profile_catalog":
			data, ok := catalogs[request.World]
			if !ok {
				http.Error(w, "unexpected profile world", http.StatusBadRequest)
				return
			}
			reply = AgentReply{Status: "succeeded", Data: data}
		case "world_catalog":
			if request.World != "" {
				http.Error(w, "unexpected catalog world", http.StatusBadRequest)
				return
			}
			reply = AgentReply{Status: "succeeded", Data: json.RawMessage(`[]`)}
		case "world_metadata":
			reply = AgentReply{Status: "failed", Error: "metadata unavailable in test"}
		default:
			http.Error(w, "unexpected operation", http.StatusBadRequest)
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
