package app

import (
	"context"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strconv"
	"strings"
	"testing"
	"time"
)

const secondSteamID = "76561198000000001"

func TestAdminPageNamesEveryKnownSteamIdentity(t *testing.T) {
	server := testServer(t)
	if err := server.store.UpsertPublicWorld(t.Context(), PublicWorld{Name: "Midgard", JoinAddress: "valheim.example:2456", Status: "online", ServerVersion: "test"}, "test"); err != nil {
		t.Fatal(err)
	}
	if err := server.store.GrantWorldAccess(t.Context(), "Midgard", testSteamID, "admin"); err != nil {
		t.Fatal(err)
	}
	if err := server.store.SetSteamPersonaNames(t.Context(), map[string]string{testSteamID: "Odinsson", secondSteamID: "Freyja"}); err != nil {
		t.Fatal(err)
	}
	if err := server.store.SetSteamLabel(t.Context(), secondSteamID, "Dave from work", "admin"); err != nil {
		t.Fatal(err)
	}
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, adminTestRequest(http.MethodGet, "/admin", nil))
	if response.Code != http.StatusOK {
		t.Fatalf("admin page = %d: %s", response.Code, response.Body.String())
	}
	page := response.Body.String()
	for _, expected := range []string{
		// Suggestion list carries the name, so typing "Odin" finds the ID.
		`<option value="` + testSteamID + `" label="Odinsson · ` + testSteamID + `">`,
		`<option value="` + secondSteamID + `" label="Dave from work · ` + secondSteamID + `">`,
		// The player card leads with the person, not the number, and names every
		// world that account may join.
		`<h3 class="player-card-name">Odinsson</h3>`,
		`href="#server-Midgard">Midgard</a>`,
		// The identity list still shows the Steam name behind an override.
		`<td>Dave from work</td><td><code>` + secondSteamID + `</code></td><td>Freyja</td>`,
	} {
		if !strings.Contains(page, expected) {
			t.Fatalf("admin page missing %q", expected)
		}
	}
	if !strings.Contains(page, `action="/admin/steam-identities/label"`) {
		t.Fatal("admin page offers no way to set a player name")
	}
	// The lookup action has to sit in the open part of the widget: a control
	// buried inside the collapsed identity list is a control nobody finds.
	refresh := strings.Index(page, `action="/admin/steam-identities/refresh"`)
	if refresh < 0 || refresh > strings.Index(page, `<details id="steam-identities"`) {
		t.Fatal("the Steam name lookup is hidden inside the collapsed identity list")
	}
}

func TestAdminPageNamesUnresolvedIdentityWithoutHidingItsSteamID(t *testing.T) {
	server := testServer(t)
	if err := server.store.RecordSteamIdentity(t.Context(), testSteamID); err != nil {
		t.Fatal(err)
	}
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, adminTestRequest(http.MethodGet, "/admin", nil))
	if response.Code != http.StatusOK {
		t.Fatalf("admin page = %d: %s", response.Code, response.Body.String())
	}
	page := response.Body.String()
	if !strings.Contains(page, `<td>Unnamed player</td><td><code>`+testSteamID+`</code></td><td>unknown</td>`) {
		t.Fatalf("unresolved identity is not listed as unnamed: %s", page)
	}
}

func TestGrantWorldMemberStoresOperatorNameAndFetchedPersona(t *testing.T) {
	server := testServer(t)
	if err := server.store.UpsertPublicWorld(t.Context(), PublicWorld{Name: "Midgard", JoinAddress: "valheim.example:2456", Status: "online", ServerVersion: "test"}, "test"); err != nil {
		t.Fatal(err)
	}
	var asked []string
	server.personas = func(_ context.Context, steamIDs []string) map[string]string {
		asked = steamIDs
		return map[string]string{testSteamID: "Odinsson"}
	}
	adminPost(t, server, "/admin/world-members", url.Values{
		"world": {"Midgard"}, "steam_id": {testSteamID}, "label": {"Dave from work"},
	}, http.StatusSeeOther)
	if len(asked) != 1 || asked[0] != testSteamID {
		t.Fatalf("granting did not resolve the granted account: %v", asked)
	}
	members, err := server.store.WorldMembers(t.Context())
	if err != nil || len(members) != 1 {
		t.Fatalf("members = %#v, %v", members, err)
	}
	if members[0].Label != "Dave from work" || members[0].PersonaName != "Odinsson" || members[0].DisplayName() != "Dave from work" {
		t.Fatalf("granted member = %#v", members[0])
	}
}

func TestGrantWorldMemberRejectsUnprintableName(t *testing.T) {
	server := testServer(t)
	if err := server.store.UpsertPublicWorld(t.Context(), PublicWorld{Name: "Midgard", JoinAddress: "valheim.example:2456", Status: "online", ServerVersion: "test"}, "test"); err != nil {
		t.Fatal(err)
	}
	adminPost(t, server, "/admin/world-members", url.Values{
		"world": {"Midgard"}, "steam_id": {testSteamID}, "label": {"Dave\x00from\nwork"},
	}, http.StatusBadRequest)
	members, err := server.store.WorldMembers(t.Context())
	if err != nil || len(members) != 0 {
		t.Fatalf("rejected grant still stored: %#v, %v", members, err)
	}
}

func TestSteamIdentityLabelOverridesAndClears(t *testing.T) {
	server := testServer(t)
	if err := server.store.SetSteamPersonaNames(t.Context(), map[string]string{testSteamID: "Odinsson"}); err != nil {
		t.Fatal(err)
	}
	adminPost(t, server, "/admin/steam-identities/label", url.Values{"steam_id": {testSteamID}, "label": {"Dave from work"}}, http.StatusSeeOther)
	identities, err := server.store.RecentSteamIdentities(t.Context(), 100)
	if err != nil || len(identities) != 1 || identities[0].DisplayName() != "Dave from work" {
		t.Fatalf("labelled identity = %#v, %v", identities, err)
	}
	adminPost(t, server, "/admin/steam-identities/label", url.Values{"steam_id": {testSteamID}, "label": {""}}, http.StatusSeeOther)
	identities, err = server.store.RecentSteamIdentities(t.Context(), 100)
	if err != nil || len(identities) != 1 || identities[0].DisplayName() != "Odinsson" {
		t.Fatalf("cleared identity = %#v, %v", identities, err)
	}
}

func TestRefreshSteamIdentitiesNamesEveryKnownAccount(t *testing.T) {
	server := testServer(t)
	for _, steamID := range []string{testSteamID, secondSteamID} {
		if err := server.store.RecordSteamIdentity(t.Context(), steamID); err != nil {
			t.Fatal(err)
		}
	}
	server.personas = func(_ context.Context, steamIDs []string) map[string]string {
		personas := map[string]string{}
		for _, steamID := range steamIDs {
			// A private profile resolves to nothing and must not clear a name.
			if steamID == testSteamID {
				personas[steamID] = "Odinsson"
			}
		}
		return personas
	}
	adminPost(t, server, "/admin/steam-identities/refresh", url.Values{}, http.StatusSeeOther)
	identities, err := server.store.RecentSteamIdentities(t.Context(), 100)
	if err != nil || len(identities) != 2 {
		t.Fatalf("identities = %#v, %v", identities, err)
	}
	// Ordering is by displayed name, so the resolved account sorts first.
	if identities[0].SteamID != testSteamID || identities[0].PersonaName != "Odinsson" {
		t.Fatalf("resolved identity = %#v", identities[0])
	}
	if identities[1].SteamID != secondSteamID || identities[1].PersonaName != "" {
		t.Fatalf("unresolved identity = %#v", identities[1])
	}
}

// A deployment that already collected Steam IDs is the whole point of the
// refresh action: it must reach accounts the admin page does not have room for.
func TestRefreshSteamIdentitiesReachesAccountsBeyondTheDisplayedPage(t *testing.T) {
	server := testServer(t)
	stored := seedIdentities(t, server, 130)
	var asked []string
	server.personas = func(_ context.Context, steamIDs []string) map[string]string {
		asked = steamIDs
		return map[string]string{steamIDs[0]: "Odinsson"}
	}
	adminPost(t, server, "/admin/steam-identities/refresh", url.Values{}, http.StatusSeeOther)
	if len(asked) != len(stored) {
		t.Fatalf("refresh looked up %d of %d stored accounts", len(asked), len(stored))
	}
	page, err := server.store.RecentSteamIdentities(t.Context(), 100)
	if err != nil || len(page) != 100 {
		t.Fatalf("identity page = %d rows, %v", len(page), err)
	}
	count, err := server.store.SteamIdentityCount(t.Context())
	if err != nil || count != len(stored) {
		t.Fatalf("identity count = %d, %v", count, err)
	}
}

// The page is capped, so the cap must drop the stalest accounts and never the
// newcomer waiting to be approved.
func TestIdentityPageKeepsTheNewestUnnamedAccounts(t *testing.T) {
	server := testServer(t)
	stored := seedIdentities(t, server, 130)
	newest := stored[len(stored)-1]
	// Name an account inside the page: naming must not change who is shown.
	if err := server.store.SetSteamPersonaNames(t.Context(), map[string]string{stored[len(stored)-5]: "Aardvark"}); err != nil {
		t.Fatal(err)
	}
	page, err := server.store.RecentSteamIdentities(t.Context(), 100)
	if err != nil {
		t.Fatal(err)
	}
	shown := make(map[string]struct{}, len(page))
	for _, identity := range page {
		shown[identity.SteamID] = struct{}{}
	}
	if _, ok := shown[newest]; !ok {
		t.Fatal("the most recently seen account is missing from the identity page")
	}
	if _, ok := shown[stored[0]]; ok {
		t.Fatal("the stalest account was kept in place of newer ones")
	}
	// Named accounts still lead the page an operator reads.
	if page[0].DisplayName() != "Aardvark" {
		t.Fatalf("first row = %#v", page[0])
	}
}

// seedIdentities records count identities with distinct, increasing last-seen
// times, oldest first, mimicking a deployment that has collected logins.
func seedIdentities(t *testing.T, server *Server, count int) []string {
	t.Helper()
	steamIDs := make([]string, 0, count)
	for index := range count {
		steamID := "7656119" + strconv.Itoa(1000000000+index)
		if err := server.store.RecordSteamIdentity(t.Context(), steamID); err != nil {
			t.Fatal(err)
		}
		seen := time.Now().UTC().Add(time.Duration(index) * time.Second).Format(time.RFC3339Nano)
		if _, err := server.store.db.ExecContext(t.Context(), `UPDATE steam_identities SET last_seen_at=? WHERE steam_id=?`, seen, steamID); err != nil {
			t.Fatal(err)
		}
		steamIDs = append(steamIDs, steamID)
	}
	return steamIDs
}

func TestEmptyPersonaNeverOverwritesAStoredName(t *testing.T) {
	server := testServer(t)
	if err := server.store.SetSteamPersonaNames(t.Context(), map[string]string{testSteamID: "Odinsson"}); err != nil {
		t.Fatal(err)
	}
	if err := server.store.SetSteamPersonaNames(t.Context(), map[string]string{testSteamID: ""}); err != nil {
		t.Fatal(err)
	}
	identities, err := server.store.RecentSteamIdentities(t.Context(), 100)
	if err != nil || len(identities) != 1 || identities[0].PersonaName != "Odinsson" {
		t.Fatalf("identities = %#v, %v", identities, err)
	}
}

func TestSteamLabelRejectsOversizedAndUntrimmedNames(t *testing.T) {
	server := testServer(t)
	for _, label := range []string{strings.Repeat("a", 65), " padded", "trailing "} {
		if err := server.store.SetSteamLabel(t.Context(), testSteamID, label, "admin"); err == nil {
			t.Fatalf("label %q was accepted", label)
		}
	}
	if err := server.store.SetSteamLabel(t.Context(), "not-a-steam-id", "Dave", "admin"); err == nil {
		t.Fatal("label accepted for an invalid Steam ID")
	}
}

func TestWebAPIResolvesPersonasInBatchesOfOneHundred(t *testing.T) {
	steamIDs := make([]string, 0, 150)
	for index := range 150 {
		steamIDs = append(steamIDs, "7656119"+strconv.Itoa(1000000000+index))
	}
	var batches []int
	steam := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("key") != "test-key" {
			http.Error(w, "forbidden", http.StatusForbidden)
			return
		}
		requested := strings.Split(r.URL.Query().Get("steamids"), ",")
		batches = append(batches, len(requested))
		players := make([]string, 0, len(requested))
		for _, steamID := range requested {
			players = append(players, `{"steamid":"`+steamID+`","personaname":"Player `+steamID[len(steamID)-2:]+`"}`)
		}
		w.Write([]byte(`{"response":{"players":[` + strings.Join(players, ",") + `]}}`))
	}))
	defer steam.Close()

	personas := steamPersonasFromWebAPI(context.Background(), steam.Client(), steam.URL, "test-key", steamIDs)
	if len(personas) != 150 {
		t.Fatalf("resolved %d of 150 personas", len(personas))
	}
	if len(batches) != 2 || batches[0] != 100 || batches[1] != 50 {
		t.Fatalf("request batches = %v", batches)
	}
	if personas[steamIDs[0]] != "Player 00" {
		t.Fatalf("persona = %q", personas[steamIDs[0]])
	}
}

func TestPublicProfilesResolveOnlyReadableAccounts(t *testing.T) {
	steam := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.Contains(r.URL.Path, testSteamID) {
			http.Error(w, "private", http.StatusForbidden)
			return
		}
		w.Header().Set("Content-Type", "text/xml")
		w.Write([]byte(`<?xml version="1.0" encoding="UTF-8"?><profile><steamID64>` + testSteamID + `</steamID64><steamID><![CDATA[Odinsson]]></steamID></profile>`))
	}))
	defer steam.Close()

	personas := steamPersonasFromPublicProfiles(context.Background(), steam.Client(), steam.URL+"/profiles/", []string{testSteamID, secondSteamID})
	if len(personas) != 1 || personas[testSteamID] != "Odinsson" {
		t.Fatalf("personas = %#v", personas)
	}
}

func TestPersonaNamesAreSingleLineAndBounded(t *testing.T) {
	if got := sanitizePersonaName("  Odin\nsson\t "); got != "Odinsson" {
		t.Fatalf("sanitized = %q", got)
	}
	if got := sanitizePersonaName(strings.Repeat("v", 200)); len(got) != steamPersonaMaxName {
		t.Fatalf("sanitized length = %d", len(got))
	}
	if got := parseSteamPlayerSummaries([]byte(`{"response":{"players":[{"steamid":"7","personaname":"Nope"}]}}`)); len(got) != 0 {
		t.Fatalf("invalid Steam ID accepted: %#v", got)
	}
	if got := parseSteamProfileXML([]byte("not xml")); got != "" {
		t.Fatalf("garbage profile resolved to %q", got)
	}
}
