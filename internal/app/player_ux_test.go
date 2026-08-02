package app

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// Without a viewport meta a phone lays the page out at 980px, which makes every
// max-width media query false: the mobile CSS these pages already ship never
// ran. Charset and lang are the rest of a real document head.
func TestEveryAdminTemplateDeclaresADocumentHead(t *testing.T) {
	for name, page := range map[string]string{
		"admin":   adminTemplate,
		"history": historyTemplate,
		"release": releaseTemplate,
		"restore": restoreTemplate,
	} {
		for _, want := range []string{
			`<html lang="en">`,
			`<meta charset="utf-8">`,
			`<meta name="viewport" content="width=device-width,initial-scale=1">`,
			`</head>`,
		} {
			if !strings.Contains(page, want) {
				t.Fatalf("%s template omits %s", name, want)
			}
		}
		// render() branches on </head>, so the stylesheet must land inside the
		// head it now has rather than after the title.
		response := httptest.NewRecorder()
		render(response, page, map[string]any{
			"World": "Midgard", "ID": "release-id", "CSRF": "csrf",
			"Restore":    restoreRequest{World: "Midgard", Backup: "world-Midgard-1.tgz"},
			"Releases":   []Release(nil),
			"Artifacts":  []Artifact(nil),
			"Worlds":     []adminWorld(nil),
			"Identities": []SteamIdentity(nil),
		})
		body := response.Body.String()
		link := strings.Index(body, `<link rel="stylesheet" href="/assets/site.css">`)
		head := strings.Index(body, "</head>")
		if link < 0 || head < 0 || link > head {
			t.Fatalf("%s stylesheet is not inside the head: link=%d head=%d", name, link, head)
		}
	}
}

// The client launches Valheim at its main menu with no connection arguments, so
// the page has to say what to do there.
func TestWorldPageTellsThePlayerHowToJoin(t *testing.T) {
	server := testServer(t)
	threeProfileWorld(t, server)
	page := playerPage(t, server, "/worlds/"+describedWorld)
	for _, want := range []string{
		"Join Game",
		"Join IP",
		`<code class="copy-value">valheim.example:2456</code>`,
		`data-copy="valheim.example:2456"`,
	} {
		if !strings.Contains(page, want) {
			t.Fatalf("world page omits join instruction %q: %s", want, page)
		}
	}
	// Whether granted players may see the password is the owner's call, so the
	// page points at the owner instead of printing it.
	if !strings.Contains(page, "ask the world owner for it") {
		t.Fatalf("world page does not account for the server password: %s", page)
	}
}

// The refusal used to be the operator text, which names the host path of the
// executable and the build script that produces it.
func TestClientDownloadRefusalNeverShowsAPlayerAFilesystemPath(t *testing.T) {
	server := testServer(t)
	executable := writePE(t, peSubsystemConsole)
	server.cfg.ClientExecutable = executable

	for _, accept := range []string{"text/html,application/xhtml+xml", "*/*"} {
		request := httptest.NewRequest(http.MethodGet, "/client/ValheimProfileSync.exe", nil)
		request.Header.Set("Accept", accept)
		response := httptest.NewRecorder()
		server.Handler().ServeHTTP(response, request)
		if response.Code != http.StatusServiceUnavailable {
			t.Fatalf("console build served with %d", response.Code)
		}
		body := response.Body.String()
		for _, leak := range []string{executable, "build-windows-client.sh", "subsystem", "/tmp/"} {
			if strings.Contains(body, leak) {
				t.Fatalf("player download refusal (Accept %s) leaks %q: %s", accept, leak, body)
			}
		}
		if !strings.Contains(body, "The Windows app is not available to download right now. Ask the world owner to publish it.") {
			t.Fatalf("player download refusal (Accept %s) is not the player message: %s", accept, body)
		}
	}

	// The browser answer is a page in the site's language, not text/plain.
	htmlRequest := httptest.NewRequest(http.MethodGet, "/client/ValheimProfileSync.exe", nil)
	htmlRequest.Header.Set("Accept", "text/html")
	htmlResponse := httptest.NewRecorder()
	server.Handler().ServeHTTP(htmlResponse, htmlRequest)
	if got := htmlResponse.Header().Get("Content-Type"); !strings.HasPrefix(got, "text/html") {
		t.Fatalf("browser refusal content type = %q", got)
	}
	if !strings.Contains(htmlResponse.Body.String(), `href="/"`) {
		t.Fatalf("browser refusal offers no way back: %s", htmlResponse.Body.String())
	}

	// A player must not be able to click into that refusal from the home page.
	describedTestWorld(t, server)
	home := playerPage(t, server, "/")
	if strings.Contains(home, `<a class="button" href="/client/ValheimProfileSync.exe">`) {
		t.Fatalf("home page still offers a download that cannot work: %s", home)
	}
	if !strings.Contains(home, `<button class="button" type="button" disabled>Download for Windows</button>`) {
		t.Fatalf("home page does not disable the download: %s", home)
	}
}

// The signed-in Steam ID is exactly what the operator needs in order to grant
// access, and the player is the only one who can read it.
func TestEmptyWorldListOffersTheSteamIDToCopy(t *testing.T) {
	server := testServer(t)
	page := playerPage(t, server, "/")
	if !strings.Contains(page, "Ask the world owner to grant access to this Steam ID") {
		t.Fatalf("empty world list gives no way forward: %s", page)
	}
	if !strings.Contains(page, `<code class="copy-value">`+testSteamID+"</code>") {
		t.Fatalf("empty world list hides the signed-in Steam ID: %s", page)
	}
	if !strings.Contains(page, `data-copy="`+testSteamID+`"`) {
		t.Fatalf("the Steam ID is not click-to-copy: %s", page)
	}
}

// A refusal a player can reach has to be a page, and it has to name the ID the
// owner needs. The client polls the same routes, so plain text stays for it.
func TestWorldAccessRefusalIsAPageForBrowsersAndTextForClients(t *testing.T) {
	server := testServer(t)
	if err := server.store.UpsertPublicWorld(t.Context(), PublicWorld{
		Name: "Forbidden", JoinAddress: "valheim.example:2456", Status: "online", ServerVersion: "test",
	}, "test"); err != nil {
		t.Fatal(err)
	}
	browser := httptest.NewRequest(http.MethodGet, "/worlds/Forbidden", nil)
	browser.Header.Set("Accept", "text/html")
	browser.AddCookie(steamCookie(t, server, testSteamID))
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, browser)
	if response.Code != http.StatusForbidden {
		t.Fatalf("refusal status = %d", response.Code)
	}
	body := response.Body.String()
	if !strings.HasPrefix(response.Header().Get("Content-Type"), "text/html") {
		t.Fatalf("browser refusal content type = %q", response.Header().Get("Content-Type"))
	}
	if !strings.Contains(body, testSteamID) || !strings.Contains(body, `href="/"`) {
		t.Fatalf("browser refusal is a dead end: %s", body)
	}

	plain := httptest.NewRequest(http.MethodGet, "/worlds/Forbidden", nil)
	plain.AddCookie(steamCookie(t, server, testSteamID))
	plainResponse := httptest.NewRecorder()
	server.Handler().ServeHTTP(plainResponse, plain)
	if strings.Contains(plainResponse.Body.String(), "<html") {
		t.Fatalf("a non-browser client was answered with a page: %s", plainResponse.Body.String())
	}
}
