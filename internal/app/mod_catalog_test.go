package app

import (
	"context"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"testing"
)

// A fingerprint is 64 hex characters, and the portal refuses anything else, so the fixtures use
// real-shaped ones rather than short strings that would be rejected for the wrong reason.
const (
	firstFingerprint  = "1111111111111111111111111111111111111111111111111111111111111111"
	secondFingerprint = "2222222222222222222222222222222222222222222222222222222222222222"
)

// catalogPayload is one host reply. The mods are real identifiers from this fleet: PlantEasily is
// on every player edition, and Backpacks is the entry that a bare Libraries-category rule deleted.
func catalogPayload(world, fingerprint string, mods ...playerModEntry) []byte {
	catalog := playerModCatalog{
		World: world, Fingerprint: fingerprint, MetadataComplete: true,
		Editions: []string{"vr", "flat"}, Installed: len(mods), Mods: mods,
	}
	payload, err := json.Marshal(catalog)
	if err != nil {
		panic(err)
	}
	return payload
}

// catalogAgent serves the two catalog operations and records every operation it was asked for, so
// a test can prove the expensive build was NOT run as well as that it was.
func catalogAgent(t *testing.T, server *Server, fingerprint string, payload []byte) func() []string {
	t.Helper()
	socket := server.agent.socket
	_ = os.Remove(socket)
	listener, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatal(err)
	}
	var mu sync.Mutex
	var seen []string
	mock := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var request agentRequest
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			http.Error(w, "bad request", http.StatusBadRequest)
			return
		}
		mu.Lock()
		seen = append(seen, request.Operation)
		mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		switch request.Operation {
		case "world_mod_catalog_state":
			_, _ = fmt.Fprintf(w, `{"status":"succeeded","data":{"fingerprint":%q}}`, fingerprint)
		case "world_mod_catalog":
			if payload == nil {
				_, _ = w.Write([]byte(`{"status":"failed","error":"catalog unavailable"}`))
				return
			}
			_, _ = fmt.Fprintf(w, `{"status":"succeeded","data":%s}`, payload)
		case "world_metadata":
			_, _ = w.Write([]byte(`{"status":"failed","error":"metadata unavailable in test"}`))
		default:
			http.Error(w, "unexpected operation", http.StatusBadRequest)
		}
	})}
	go func() { _ = mock.Serve(listener) }()
	t.Cleanup(func() {
		_ = mock.Close()
		_ = listener.Close()
	})
	return func() []string {
		mu.Lock()
		defer mu.Unlock()
		return append([]string(nil), seen...)
	}
}

func registerWorld(t *testing.T, server *Server, world string) {
	t.Helper()
	ctx := context.Background()
	if err := server.store.UpsertPublicWorld(ctx, PublicWorld{
		Name: world, JoinAddress: "valheim.example.test:2456", Status: "offline",
		ServerVersion: "unknown", Enabled: true,
	}, "operator"); err != nil {
		t.Fatal(err)
	}
	if err := server.store.GrantWorldAccess(ctx, world, testSteamID, "admin"); err != nil {
		t.Fatal(err)
	}
}

func worldPage(t *testing.T, server *Server, world string) string {
	t.Helper()
	request := httptest.NewRequest(http.MethodGet, "/worlds/"+world, nil)
	request.AddCookie(steamCookie(t, server, testSteamID))
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("world page returned %d", response.Code)
	}
	return response.Body.String()
}

// The fingerprint is the whole reason this cache is safe to keep. Mods are changed by
// tools/valheim_mods.py directly on the host, where the portal is not involved at all, so a cache
// that trusted its own invalidation events would serve a list that is quietly wrong.
func TestAModChangedOutsideThePortalForcesARebuildOnRead(t *testing.T) {
	server := testServer(t)
	registerWorld(t, server, "Hrafnheim")
	ctx := context.Background()

	stale := catalogPayload("Hrafnheim", firstFingerprint,
		playerModEntry{Identifier: "Advize-PlantEasily", Name: "PlantEasily", Version: "2.1.1", Description: "Plant evenly spaced crops."})
	if err := server.store.SaveWorldModCatalog(ctx, "Hrafnheim", firstFingerprint, stale); err != nil {
		t.Fatal(err)
	}

	// The host now reports a different installed set, and a fresh list that names a mod the cached
	// one never carried.
	fresh := catalogPayload("Hrafnheim", secondFingerprint,
		playerModEntry{Identifier: "Advize-PlantEasily", Name: "PlantEasily", Version: "2.1.1", Description: "Plant evenly spaced crops."},
		playerModEntry{Identifier: "Smoothbrain-Backpacks", Name: "Backpacks", Version: "1.3.9", Description: "Adds a very nice backpack."})
	operations := catalogAgent(t, server, secondFingerprint, fresh)

	body := worldPage(t, server, "Hrafnheim")
	if !strings.Contains(body, "Backpacks") {
		t.Fatalf("the rebuilt list was not rendered: %s", body)
	}
	if !contains(operations(), "world_mod_catalog") {
		t.Fatalf("a changed fingerprint did not trigger a rebuild: %v", operations())
	}
	// The rebuild must also have been persisted, or every later view pays for it again.
	fingerprint, payload, err := server.store.WorldModCatalog(ctx, "Hrafnheim")
	if err != nil {
		t.Fatal(err)
	}
	if fingerprint != secondFingerprint || !strings.Contains(string(payload), "Smoothbrain-Backpacks") {
		t.Fatalf("the rebuilt list was not cached: %q %s", fingerprint, payload)
	}
}

// The other half of the same contract: an unchanged installed set must NOT pay for a rebuild.
// Static until modified is the requirement, and the Thunderstore index fetch the build performs is
// what makes it worth having.
func TestAnUnchangedInstalledSetServesTheCacheWithoutRebuilding(t *testing.T) {
	server := testServer(t)
	registerWorld(t, server, "Hrafnheim")
	cached := catalogPayload("Hrafnheim", firstFingerprint,
		playerModEntry{Identifier: "Advize-PlantEasily", Name: "PlantEasily", Version: "2.1.1", Description: "Plant evenly spaced crops."})
	if err := server.store.SaveWorldModCatalog(context.Background(), "Hrafnheim", firstFingerprint, cached); err != nil {
		t.Fatal(err)
	}
	// The full build would answer with something else entirely; if it is called, the assertion on
	// the rendered body catches it as well as the operation log.
	other := catalogPayload("Hrafnheim", firstFingerprint,
		playerModEntry{Identifier: "Azumatt-FastLink", Name: "FastLink", Version: "1.0.4"})
	operations := catalogAgent(t, server, firstFingerprint, other)

	body := worldPage(t, server, "Hrafnheim")
	if !strings.Contains(body, "PlantEasily") || strings.Contains(body, "FastLink") {
		t.Fatalf("the cached list was not the one served: %s", body)
	}
	if contains(operations(), "world_mod_catalog") {
		t.Fatalf("an unchanged fingerprint still triggered a rebuild: %v", operations())
	}
}

// A note is the operator's own sentence and nothing else may write one. An entry without a note
// renders its name, version and description and stops there - it does not acquire a sentence the
// portal made up, which is the defect class this whole surface exists to avoid.
func TestAModWithoutAPlayerNoteRendersWithoutInventingOne(t *testing.T) {
	server := testServer(t)
	registerWorld(t, server, "Hrafnheim")
	ctx := context.Background()
	payload := catalogPayload("Hrafnheim", firstFingerprint,
		playerModEntry{Identifier: "Advize-PlantEasily", Name: "PlantEasily", Version: "2.1.1", Description: "Plant evenly spaced crops."},
		playerModEntry{Identifier: "Smoothbrain-Backpacks", Name: "Backpacks", Version: "1.3.9", Description: "Adds a very nice backpack."})
	if err := server.store.SaveWorldModCatalog(ctx, "Hrafnheim", firstFingerprint, payload); err != nil {
		t.Fatal(err)
	}
	if err := server.store.SetModPlayerNote(ctx, "Smoothbrain-Backpacks", "Craft one at the workbench, then equip it in the shoulder slot.", "operator"); err != nil {
		t.Fatal(err)
	}
	catalogAgent(t, server, firstFingerprint, nil)

	body := worldPage(t, server, "Hrafnheim")
	if !strings.Contains(body, "Craft one at the workbench") {
		t.Fatalf("the operator's note was not rendered: %s", body)
	}
	if !strings.Contains(body, "PlantEasily") || !strings.Contains(body, "2.1.1") || !strings.Contains(body, "Plant evenly spaced crops.") {
		t.Fatalf("the un-noted entry lost its own facts: %s", body)
	}
	// Exactly one note element: the mod nobody wrote about must not have acquired one.
	if notes := strings.Count(body, `<p class="note">`); notes != 1 {
		t.Fatalf("expected exactly one player note in the page, found %d: %s", notes, body)
	}
}

// A note outlives the list it is shown on. The payload is replaced wholesale by a rebuild, so a
// note stored inside it would be destroyed by the next mod change.
func TestAPlayerNoteSurvivesARebuildOfTheList(t *testing.T) {
	server := testServer(t)
	registerWorld(t, server, "Hrafnheim")
	ctx := context.Background()
	if err := server.store.SetModPlayerNote(ctx, "Smoothbrain-Backpacks", "Craft one at the workbench.", "operator"); err != nil {
		t.Fatal(err)
	}
	if err := server.store.SaveWorldModCatalog(ctx, "Hrafnheim", firstFingerprint,
		catalogPayload("Hrafnheim", firstFingerprint, playerModEntry{Identifier: "Smoothbrain-Backpacks", Name: "Backpacks", Version: "1.3.8"})); err != nil {
		t.Fatal(err)
	}
	rebuilt := catalogPayload("Hrafnheim", secondFingerprint,
		playerModEntry{Identifier: "Smoothbrain-Backpacks", Name: "Backpacks", Version: "1.3.9"})
	catalogAgent(t, server, secondFingerprint, rebuilt)

	body := worldPage(t, server, "Hrafnheim")
	if !strings.Contains(body, "1.3.9") {
		t.Fatalf("the list was not rebuilt: %s", body)
	}
	if !strings.Contains(body, "Craft one at the workbench.") {
		t.Fatalf("a rebuild lost the operator's note: %s", body)
	}
}

// No cached list and no reachable host is a real state - the agent is down, or a world has never
// been read. It has to say so. An empty section is indistinguishable from a world with no mods,
// and a broken page loses the profiles a player actually came for.
func TestAWorldWithNoListSaysSoInsteadOfRenderingNothing(t *testing.T) {
	server := testServer(t)
	registerWorld(t, server, "Hrafnheim")
	catalogAgent(t, server, firstFingerprint, nil)

	body := worldPage(t, server, "Hrafnheim")
	if !strings.Contains(body, "The mod list cannot be read from the server right now") {
		t.Fatalf("an unavailable list was not reported honestly: %s", body)
	}
	// The rest of the page is untouched by the failure.
	if !strings.Contains(body, "Hrafnheim") || !strings.Contains(body, "Mods on this server") {
		t.Fatalf("the failure took the page with it: %s", body)
	}
}

// A successful mod mutation drops every cached list, so the next reader rebuilds rather than being
// served the set that was installed before the change.
func TestASuccessfulMutationDropsTheCachedLists(t *testing.T) {
	server := testServer(t)
	ctx := context.Background()
	if err := server.store.SaveWorldModCatalog(ctx, "Hrafnheim", firstFingerprint,
		catalogPayload("Hrafnheim", firstFingerprint, playerModEntry{Identifier: "Advize-PlantEasily", Name: "PlantEasily", Version: "2.1.1"})); err != nil {
		t.Fatal(err)
	}
	if err := server.store.DiscardModCatalogs(ctx); err != nil {
		t.Fatal(err)
	}
	fingerprint, payload, err := server.store.WorldModCatalog(ctx, "Hrafnheim")
	if err != nil {
		t.Fatal(err)
	}
	if fingerprint != "" || payload != nil {
		t.Fatalf("a cached list survived invalidation: %q %s", fingerprint, payload)
	}
}

// A payload naming another world is a wiring mistake, and rendering it would tell players about a
// server they are not looking at.
func TestACatalogForAnotherWorldIsRefused(t *testing.T) {
	if _, ok := decodeModCatalog(catalogPayload("Doggerland", firstFingerprint), "Hrafnheim"); ok {
		t.Fatal("a catalog for another world was accepted")
	}
	if _, ok := decodeModCatalog(catalogPayload("Hrafnheim", firstFingerprint), "Hrafnheim"); !ok {
		t.Fatal("the control catalog for the right world was refused")
	}
}

func contains(values []string, want string) bool {
	for _, value := range values {
		if value == want {
			return true
		}
	}
	return false
}
