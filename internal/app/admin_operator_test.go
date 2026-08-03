package app

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

const (
	operatorSteam = "76561198000000001"
	playerSteam   = "76561198000000002"
)

// operatorServer is a portal with one allowlisted Steam operator.
func operatorServer(t *testing.T) *Server {
	t.Helper()
	server := testServer(t)
	server.cfg.AdminSteamIDs = map[string]struct{}{operatorSteam: {}}
	return server
}

// signedIn returns a request carrying a genuine Steam session for steamID and
// nothing else: no proxy identity, no admin token, and a remote address outside
// the trusted range. Anything it is allowed to do is allowed on the strength of
// the session alone.
func signedIn(t *testing.T, server *Server, method, target, steamID string) *http.Request {
	t.Helper()
	issued := httptest.NewRecorder()
	server.setSteamSession(issued, steamID)
	request := httptest.NewRequest(method, target, nil)
	request.RemoteAddr = "203.0.113.5:9999"
	for _, cookie := range issued.Result().Cookies() {
		if cookie.Name == steamSessionCookie {
			request.AddCookie(cookie)
			return request
		}
	}
	t.Fatal("no Steam session cookie was issued")
	return nil
}

func administrationLinkOffered(t *testing.T, server *Server, request *http.Request) bool {
	t.Helper()
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("home status = %d", response.Code)
	}
	return strings.Contains(response.Body.String(), `href="/admin"`)
}

// The reported defect: the link used to require a prior manual visit to /admin,
// because that page was the only thing that issued the session cookie the link
// depended on. An operator's very first page load must now offer it.
func TestOperatorIsOfferedAdministrationOnFirstLoad(t *testing.T) {
	server := operatorServer(t)
	if !administrationLinkOffered(t, server, signedIn(t, server, http.MethodGet, "/", operatorSteam)) {
		t.Fatal("an allowlisted operator was not offered administration on first load")
	}
}

func TestSignedInPlayerIsNotOfferedAdministration(t *testing.T) {
	server := operatorServer(t)
	if administrationLinkOffered(t, server, signedIn(t, server, http.MethodGet, "/", playerSteam)) {
		t.Fatal("a player outside the allowlist was offered administration")
	}
}

// The Steam session alone must reach the administration surface, with no help
// from the proxy: that is the whole point of the allowlist.
func TestOperatorMayAdministerWithoutProxyHeaders(t *testing.T) {
	server := operatorServer(t)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, signedIn(t, server, http.MethodGet, "/admin", operatorSteam))
	if response.Code == http.StatusUnauthorized {
		t.Fatal("an allowlisted operator was refused administration")
	}
}

func TestSignedInPlayerIsRefusedAdministration(t *testing.T) {
	server := operatorServer(t)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, signedIn(t, server, http.MethodGet, "/admin", playerSteam))
	if response.Code != http.StatusUnauthorized {
		t.Fatalf("player admin status = %d, want 401", response.Code)
	}
}

// world_members.role='admin' is the in-game adminlist written into
// adminlist.txt. Treating it as portal authorisation would hand the control
// surface, including world deletion, to every in-game admin.
func TestInGameAdminRoleGrantsNoPortalAdministration(t *testing.T) {
	server := operatorServer(t)
	ctx := t.Context()
	if err := server.store.RecordSteamIdentity(ctx, playerSteam); err != nil {
		t.Fatal(err)
	}
	if err := server.store.GrantWorldAccess(ctx, "TestWorld", playerSteam, "operator"); err != nil {
		t.Fatal(err)
	}
	if err := server.store.SetWorldMemberRole(ctx, "TestWorld", playerSteam, "admin", "operator"); err != nil {
		t.Fatal(err)
	}

	// Guard against the test passing vacuously: the role must really be stored,
	// otherwise this proves only that an ordinary player is refused.
	admins, _, err := server.store.intendedAccessLists(ctx, "TestWorld", true)
	if err != nil {
		t.Fatal(err)
	}
	if len(admins) != 1 || admins[0] != playerSteam {
		t.Fatalf("in-game admin list = %v, want [%s]", admins, playerSteam)
	}

	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, signedIn(t, server, http.MethodGet, "/admin", playerSteam))
	if response.Code != http.StatusUnauthorized {
		t.Fatalf("in-game admin reached portal administration: status = %d", response.Code)
	}
	if administrationLinkOffered(t, server, signedIn(t, server, http.MethodGet, "/", playerSteam)) {
		t.Fatal("in-game admin was offered portal administration")
	}
}

// The proxy route stays usable so a deployment with no allowlist, or an operator
// locked out of Steam, still has a way in.
func TestProxyBreakGlassStillAdministersAlongsideTheAllowlist(t *testing.T) {
	server := operatorServer(t)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, adminTestRequest(http.MethodGet, "/admin", nil))
	if response.Code == http.StatusUnauthorized {
		t.Fatal("the proxy break-glass path was refused")
	}
}

// Every privileged action is recorded against an actor, and the store rejects a
// blank one, so the Steam route has to supply its own attribution.
func TestOperatorActionsAreAttributedToTheSteamAccount(t *testing.T) {
	server := operatorServer(t)
	handler := server.admin(func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprint(w, r.Header.Get("X-Portal-Actor"))
	})

	viaSteam := httptest.NewRecorder()
	handler(viaSteam, signedIn(t, server, http.MethodGet, "/admin", operatorSteam))
	if got := viaSteam.Body.String(); got != "steam:"+operatorSteam {
		t.Fatalf("actor via Steam = %q, want %q", got, "steam:"+operatorSteam)
	}

	viaProxy := httptest.NewRecorder()
	handler(viaProxy, adminTestRequest(http.MethodGet, "/admin", nil))
	if got := viaProxy.Body.String(); got != "operator" {
		t.Fatalf("actor via proxy = %q, want %q", got, "operator")
	}
}

// A deployment that configures no operators keeps exactly the old behaviour.
func TestEmptyAllowlistRefusesEverySteamSession(t *testing.T) {
	server := testServer(t)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, signedIn(t, server, http.MethodGet, "/admin", operatorSteam))
	if response.Code != http.StatusUnauthorized {
		t.Fatalf("admin status with no allowlist = %d, want 401", response.Code)
	}
}
