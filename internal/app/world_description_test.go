package app

import (
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
)

const describedWorld = "Midgard"

func describedTestWorld(t *testing.T, server *Server) {
	t.Helper()
	if err := server.store.UpsertPublicWorld(t.Context(), PublicWorld{
		Name: describedWorld, JoinAddress: "valheim.example:2456", Status: "online", ServerVersion: "test",
	}, "test"); err != nil {
		t.Fatal(err)
	}
	if err := server.store.GrantWorldAccess(t.Context(), describedWorld, testSteamID, "test"); err != nil {
		t.Fatal(err)
	}
}

// playerPage fetches a signed-in player page, which is where a description has
// to be visible for the feature to mean anything.
func playerPage(t *testing.T, server *Server, target string) string {
	t.Helper()
	request := httptest.NewRequest(http.MethodGet, target, nil)
	request.AddCookie(steamCookie(t, server, testSteamID))
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("GET %s = %d: %s", target, response.Code, response.Body.String())
	}
	return response.Body.String()
}

func TestWorldDescriptionSavedByAdminAppearsOnPlayerPages(t *testing.T) {
	server := testServer(t)
	describedTestWorld(t, server)
	// A browser posts textarea line breaks as CRLF; the stored blurb must be the
	// operator's text with plain newlines, not carriage-return debris.
	adminPost(t, server, "/admin/worlds/"+describedWorld+"/description", url.Values{
		"description": {"Rolling plains and a longship.\r\nBring a shield."},
	}, http.StatusSeeOther)
	stored, err := server.store.PublicWorld(t.Context(), describedWorld)
	if err != nil {
		t.Fatal(err)
	}
	if stored.Description != "Rolling plains and a longship.\nBring a shield." {
		t.Fatalf("stored description = %q", stored.Description)
	}
	for _, page := range []string{"/", "/worlds/" + describedWorld} {
		body := playerPage(t, server, page)
		if !strings.Contains(body, `<p class="world-description">Rolling plains and a longship.`) {
			t.Fatalf("%s omits the world description: %s", page, body)
		}
		if !strings.Contains(body, "Bring a shield.</p>") {
			t.Fatalf("%s truncates the world description: %s", page, body)
		}
	}
	// The operator must be able to see and revise what players are shown.
	if !strings.Contains(adminPage(t, server), `<textarea name="description" maxlength="500" rows="3" placeholder="What players should know about this world.">Rolling plains and a longship.`) {
		t.Fatal("admin card does not offer the stored description for editing")
	}
}

func TestWorldWithoutDescriptionRendersNoDescriptionMarkup(t *testing.T) {
	server := testServer(t)
	describedTestWorld(t, server)
	for _, page := range []string{"/", "/worlds/" + describedWorld} {
		if body := playerPage(t, server, page); strings.Contains(body, `<p class="world-description">`) {
			t.Fatalf("%s renders an empty description element: %s", page, body)
		}
	}
}

func TestWorldDescriptionIsEscapedInRenderedPages(t *testing.T) {
	server := testServer(t)
	describedTestWorld(t, server)
	adminPost(t, server, "/admin/worlds/"+describedWorld+"/description", url.Values{
		"description": {"<script>alert(1)</script>"},
	}, http.StatusSeeOther)
	for _, page := range []string{"/", "/worlds/" + describedWorld} {
		body := playerPage(t, server, page)
		if !strings.Contains(body, "&lt;script&gt;alert(1)&lt;/script&gt;") {
			t.Fatalf("%s does not escape the description: %s", page, body)
		}
		if strings.Contains(body, "<script>alert(1)</script>") {
			t.Fatalf("%s renders the description as live markup: %s", page, body)
		}
	}
}

func TestStoreRejectsUnusableWorldDescriptions(t *testing.T) {
	server := testServer(t)
	describedTestWorld(t, server)
	world, err := server.store.PublicWorld(t.Context(), describedWorld)
	if err != nil {
		t.Fatal(err)
	}
	for name, description := range map[string]string{
		"too long":         strings.Repeat("a", 501),
		"control notation": "Midgard\x00alert",
	} {
		world.Description = description
		if err := server.store.UpsertPublicWorld(t.Context(), world, "test"); err == nil {
			t.Fatalf("%s description was accepted", name)
		}
		stored, err := server.store.PublicWorld(t.Context(), describedWorld)
		if err != nil {
			t.Fatal(err)
		}
		if stored.Description != "" {
			t.Fatalf("%s description was stored as %q", name, stored.Description)
		}
	}
	// The cap is a limit, not a rejection of long-but-reasonable copy.
	world.Description = strings.Repeat("a", 500)
	if err := server.store.UpsertPublicWorld(t.Context(), world, "test"); err != nil {
		t.Fatalf("500 character description rejected: %v", err)
	}
}
