package app

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync"
	"testing"
	"time"
)

const adminModeWorld = "Hrafnheim"

// registerAdminModeWorld puts one world in the portal and answers the two agent
// operations the toggle needs: the profile catalog it reads the world's linked profile
// from, and the admin-mode operation itself. The reply for the latter is the caller's, so
// a test can make the host succeed or fail.
func registerAdminModeWorld(t *testing.T, server *Server, reply AgentReply) func() []agentRequest {
	t.Helper()
	if err := server.store.UpsertPublicWorld(t.Context(), PublicWorld{
		Name: adminModeWorld, JoinAddress: "valheim.example:2456", Status: "online", ServerVersion: "0.221.12",
	}, "test"); err != nil {
		t.Fatal(err)
	}
	var mu sync.Mutex
	var seen []agentRequest
	serveMockAgent(t, server, func(w http.ResponseWriter, r *http.Request) {
		var request agentRequest
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			http.Error(w, "bad request", http.StatusBadRequest)
			return
		}
		mu.Lock()
		seen = append(seen, request)
		mu.Unlock()
		switch request.Operation {
		case "profile_catalog":
			catalog := fmt.Sprintf(`[{"world":%q,"profile":"admin","name":"admin","packages":106,"custom_packages":0,"disabled_packages":1,"linked":true}]`, adminModeWorld)
			_ = json.NewEncoder(w).Encode(AgentReply{Status: "succeeded", Data: json.RawMessage(catalog)})
		case "admin_mode_on", "admin_mode_off":
			_ = json.NewEncoder(w).Encode(reply)
		default:
			http.Error(w, "unexpected operation "+request.Operation, http.StatusBadRequest)
		}
	})
	return func() []agentRequest {
		mu.Lock()
		defer mu.Unlock()
		return append([]agentRequest(nil), seen...)
	}
}

// statusWithPlayers writes the world's own status file, which is where liveness and the
// player count are measured from. Separate from liveStatusReport because that one pins
// the count at 3 and the refusal being tested here turns on exactly that number.
func statusWithPlayers(t *testing.T, server *Server, world string, players int, observed time.Time) {
	t.Helper()
	writeStatusJSON(t, server.cfg.MapSourceRoot, world, fmt.Sprintf(
		`{"last_status_update": %q, "error": null, "server_name": "test", "player_count": %d,
		  "keywords": "g=0.221.12,n=36,m=", "players": []}`,
		observed.Format(time.RFC3339Nano), players))
}

func adminModeForm(on bool) url.Values {
	return url.Values{"admin_mode": {fmt.Sprint(on)}}
}

// The refusal that the whole feature exists to make impossible to forget. These two mods
// disconnect every connected player when they load server-side - that is why they were
// pulled from all four servers on 2026-08-20 - so a window opened on an occupied world
// would kick the people it was opened to work around.
func TestAdminModeIsRefusedWhilePlayersAreConnected(t *testing.T) {
	server := testServer(t)
	requests := registerAdminModeWorld(t, server, AgentReply{Status: "succeeded"})
	statusWithPlayers(t, server, adminModeWorld, 2, time.Now())

	adminPost(t, server, "/admin/worlds/"+adminModeWorld+"/admin-mode", adminModeForm(true), http.StatusConflict)

	for _, request := range requests() {
		if strings.HasPrefix(request.Operation, "admin_mode_") {
			t.Fatalf("the host was asked to %s despite players being connected", request.Operation)
		}
	}
	if _, open, err := server.store.WorldAdminModeState(t.Context(), adminModeWorld); err != nil || open {
		t.Fatalf("a refused window must not be recorded as open: open=%t err=%v", open, err)
	}
}

// A world with nobody on it is the case that must work, and it is the control for the
// refusal above: same world, same route, same form, only the player count differs.
func TestAdminModeOpensOnAnEmptyWorldAndOrdersTheHostSteps(t *testing.T) {
	server := testServer(t)
	requests := registerAdminModeWorld(t, server, AgentReply{Status: "succeeded", Output: "admin_mode=on\ndeployed=true"})
	statusWithPlayers(t, server, adminModeWorld, 0, time.Now())

	adminPost(t, server, "/admin/worlds/"+adminModeWorld+"/admin-mode", adminModeForm(true), http.StatusSeeOther)

	seen := requests()
	var armed *agentRequest
	for i := range seen {
		if seen[i].Operation == "admin_mode_on" {
			armed = &seen[i]
		}
	}
	if armed == nil {
		t.Fatal("the host was never asked to open the window")
	}
	// The profile travels because the overlay is built from the archives that profile
	// already pins; without it the host cannot arm without reaching Thunderstore.
	if armed.World != adminModeWorld || armed.Profile != "admin" {
		t.Fatalf("admin_mode_on world=%q profile=%q, want %q and the world's linked profile", armed.World, armed.Profile, adminModeWorld)
	}
	window, open, err := server.store.WorldAdminModeState(t.Context(), adminModeWorld)
	if err != nil || !open {
		t.Fatalf("window state open=%t err=%v, want recorded open", open, err)
	}
	if window.Actor != "admin" || window.Since.IsZero() {
		t.Fatalf("window = %+v, want the actor and the time it opened", window)
	}
}

// The state is the whole reason for a table. A portal restart during a window must not
// turn a world that kicks every player who joins it back into a normal-looking world.
func TestAdminModeStateSurvivesAPortalRestart(t *testing.T) {
	server := testServer(t)
	requests := registerAdminModeWorld(t, server, AgentReply{Status: "succeeded"})
	statusWithPlayers(t, server, adminModeWorld, 0, time.Now())
	adminPost(t, server, "/admin/worlds/"+adminModeWorld+"/admin-mode", adminModeForm(true), http.StatusSeeOther)
	_ = requests

	// A restart is a new Store over the same file, which is exactly what the process does.
	reopened, err := OpenStore(server.cfg.DatabasePath)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { reopened.Close() })
	if err := reopened.Migrate(t.Context()); err != nil {
		t.Fatal(err)
	}
	window, open, err := reopened.WorldAdminModeState(t.Context(), adminModeWorld)
	if err != nil || !open {
		t.Fatalf("after a restart open=%t err=%v, want the window still recorded", open, err)
	}
	if window.World != adminModeWorld {
		t.Fatalf("window world = %q, want %q", window.World, adminModeWorld)
	}
	windows, err := reopened.WorldAdminModes(t.Context())
	if err != nil || len(windows) != 1 {
		t.Fatalf("open windows = %v, err = %v, want exactly the one world", windows, err)
	}
}

// Turning off a world that is not in a window is what an operator presses when they
// cannot tell whether the last attempt landed. It has to be a no-op: an error here would
// make the recovery path itself refusable, and it must not stop a running world.
func TestAdminModeOffOnAWorldThatIsNotInItIsANoOp(t *testing.T) {
	server := testServer(t)
	requests := registerAdminModeWorld(t, server, AgentReply{Status: "succeeded"})
	// Four players on it, deliberately: the off path must not consult the player count and
	// must not stop a world that was never armed.
	statusWithPlayers(t, server, adminModeWorld, 4, time.Now())

	adminPost(t, server, "/admin/worlds/"+adminModeWorld+"/admin-mode", adminModeForm(false), http.StatusSeeOther)

	if reached := requests(); len(reached) != 0 {
		t.Fatalf("a no-op must not reach the host at all: it ran %v", reached)
	}
	if _, open, err := server.store.WorldAdminModeState(t.Context(), adminModeWorld); err != nil || open {
		t.Fatalf("state open=%t err=%v, want still closed", open, err)
	}
}

// A step that fails after the stop leaves the world down carrying a mod set nobody chose.
// Reporting that as success is the failure mode this guards: the reply has to say the
// world is stopped, that it is still recorded as in admin mode, and how to recover.
func TestAdminModeReportsTheRealStateWhenTheDeployFails(t *testing.T) {
	server := testServer(t)
	registerAdminModeWorld(t, server, AgentReply{
		Status: "failed",
		Error: "mod_deploy failed; " + adminModeWorld + " is STOPPED and did not restart." +
			" Its deployed server mod set is not the one this operation intended.",
		Output: "error: Profile deployment directories are incomplete",
	})
	statusWithPlayers(t, server, adminModeWorld, 0, time.Now())

	response := adminModePost(t, server, adminModeForm(true), http.StatusConflict)
	for _, want := range []string{"was NOT completed", "recorded as in admin mode", "The world is not running", "Do not tell players", "mod_deploy failed", "STOPPED"} {
		if !strings.Contains(response, want) {
			t.Fatalf("the failure page does not say %q:\n%s", want, response)
		}
	}
	// Recorded as open even though the open did not finish: the overlay may already be
	// staged, and a world wrongly marked safe kicks players with nothing on the portal
	// explaining why. The other direction only costs one more toggle.
	if _, open, err := server.store.WorldAdminModeState(t.Context(), adminModeWorld); err != nil || !open {
		t.Fatalf("state open=%t err=%v, want the window recorded despite the failure", open, err)
	}
}

// A failure that leaves a world stopped must never end in a redirect to a page that says
// nothing. The operator has to be handed the command that puts the world back.
func TestAdminModeFailureNamesTheRecoveryCommand(t *testing.T) {
	server := testServer(t)
	registerAdminModeWorld(t, server, AgentReply{
		Status: "failed",
		Error:  "start failed; " + adminModeWorld + " is STOPPED and did not restart.",
	})
	statusWithPlayers(t, server, adminModeWorld, 0, time.Now())

	response := adminModePost(t, server, adminModeForm(true), http.StatusConflict)
	if !strings.Contains(response, "start failed") {
		t.Fatalf("the host's own reason was dropped:\n%s", response)
	}
	if !strings.Contains(response, "STOPPED") {
		t.Fatalf("the page does not say the world is down:\n%s", response)
	}
}

// The window is not merely stored: an operator scrolling the admin home has to see which
// world kicks players, on the collapsed card, with the consequence spelled out. A label
// reading "admin mode" alone assumes the operator remembers what that does.
func TestAdminModeIsVisibleOnTheAdminWorldCard(t *testing.T) {
	server := testServer(t)
	registerAdminModeWorld(t, server, AgentReply{Status: "succeeded"})
	statusWithPlayers(t, server, adminModeWorld, 0, time.Now())

	before := adminPage(t, server)
	if strings.Contains(before, "ADMIN MODE") {
		t.Fatal("a world that is not in a window must not be marked")
	}
	if !strings.Contains(before, "/admin-mode") {
		t.Fatalf("the card offers no way to open a window:\n%s", before)
	}

	adminPost(t, server, "/admin/worlds/"+adminModeWorld+"/admin-mode", adminModeForm(true), http.StatusSeeOther)

	after := adminPage(t, server)
	// The badge lives in the <summary>, which is the only part of the card an operator
	// sees while the <details> is closed.
	summary := after[strings.Index(after, `id="server-`+adminModeWorld):]
	summary = summary[:strings.Index(summary, "</summary>")]
	if !strings.Contains(summary, "ADMIN MODE") || !strings.Contains(summary, "PLAYERS KICKED") {
		t.Fatalf("the collapsed card does not warn:\n%s", summary)
	}
	if !strings.Contains(after, "every player who joins "+adminModeWorld+" is disconnected") {
		t.Fatalf("the card does not state the consequence:\n%s", after)
	}
	if !strings.Contains(after, "There is no timer") {
		t.Fatalf("the card does not say the window stays open until it is closed:\n%s", after)
	}
}

// adminModePost posts the toggle and returns the body, which is what the refusal and
// failure tests assert on: a redirect would drop the sentence describing the world.
func adminModePost(t *testing.T, server *Server, form url.Values, want int) string {
	t.Helper()
	nonce := strings.Repeat("a", 64)
	form.Set("csrf", server.csrfToken(nonce))
	request := adminTestRequest(http.MethodPost, "/admin/worlds/"+adminModeWorld+"/admin-mode", strings.NewReader(form.Encode()))
	request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	request.AddCookie(&http.Cookie{Name: "portal_csrf", Value: nonce, Path: "/admin"})
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != want {
		t.Fatalf("POST admin-mode = %d, want %d: %s", response.Code, want, response.Body.String())
	}
	return response.Body.String()
}
