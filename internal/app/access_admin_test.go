package app

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"

	"github.com/neuralyze/valheim-portal/internal/agent"
)

func accessTestWorld(t *testing.T, server *Server, world string) {
	t.Helper()
	if err := server.store.UpsertPublicWorld(t.Context(), PublicWorld{Name: world, JoinAddress: "valheim.example:2456", Status: "online", ServerVersion: "test"}, "test"); err != nil {
		t.Fatal(err)
	}
}

// The generated lists are the whole point: admins come from the role, and the
// permitted list stays empty until a world opts into enforcement, because a
// non-empty permitted list refuses everyone who is not on it.
func TestAccessListsFollowRolesAndEnforcementFlag(t *testing.T) {
	server := testServer(t)
	accessTestWorld(t, server, "Midgard")
	for _, steamID := range []string{testSteamID, secondSteamID} {
		if err := server.store.GrantWorldAccess(t.Context(), "Midgard", steamID, "admin"); err != nil {
			t.Fatal(err)
		}
	}
	if err := server.store.SetWorldMemberRole(t.Context(), "Midgard", testSteamID, "admin", "operator"); err != nil {
		t.Fatal(err)
	}

	plan, err := server.store.WorldAccessPlanFor(t.Context(), "Midgard")
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Admins) != 1 || plan.Admins[0] != testSteamID {
		t.Fatalf("admins = %#v", plan.Admins)
	}
	if len(plan.Permitted) != 0 {
		t.Fatalf("permitted list is exclusive and must stay empty until enforced: %#v", plan.Permitted)
	}

	if err := server.store.SetPermittedEnforcement(t.Context(), "Midgard", true, "operator"); err != nil {
		t.Fatal(err)
	}
	plan, err = server.store.WorldAccessPlanFor(t.Context(), "Midgard")
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Permitted) != 2 {
		t.Fatalf("enforced permitted list = %#v", plan.Permitted)
	}
	if plan.InSync() {
		t.Fatal("a world that was never applied reported itself in sync")
	}
}

func TestAccessPlanTracksPendingChangesAfterEveryMutation(t *testing.T) {
	server := testServer(t)
	accessTestWorld(t, server, "Midgard")
	if err := server.store.GrantWorldAccess(t.Context(), "Midgard", testSteamID, "admin"); err != nil {
		t.Fatal(err)
	}
	plan, err := server.store.WorldAccessPlanFor(t.Context(), "Midgard")
	if err != nil {
		t.Fatal(err)
	}
	// A member-only world with enforcement off intends two empty lists, which
	// an unapplied world already matches.
	if !plan.InSync() {
		t.Fatalf("empty intent reported pending: %#v", plan)
	}
	if err := server.store.SetWorldMemberRole(t.Context(), "Midgard", testSteamID, "admin", "operator"); err != nil {
		t.Fatal(err)
	}
	if plan, err = server.store.WorldAccessPlanFor(t.Context(), "Midgard"); err != nil || plan.InSync() {
		t.Fatalf("promotion did not become pending: %#v %v", plan, err)
	}
	if err := server.store.RecordAccessApplied(t.Context(), "Midgard", plan.Admins, plan.Permitted, "operator"); err != nil {
		t.Fatal(err)
	}
	if plan, err = server.store.WorldAccessPlanFor(t.Context(), "Midgard"); err != nil || !plan.InSync() {
		t.Fatalf("applying did not clear pending: %#v %v", plan, err)
	}
	if !plan.Applied || plan.AppliedBy != "operator" {
		t.Fatalf("applied record = %#v", plan)
	}
	// Revoking is a change like any other and must show as pending again.
	if err := server.store.RevokeWorldAccess(t.Context(), "Midgard", testSteamID, "operator"); err != nil {
		t.Fatal(err)
	}
	if plan, err = server.store.WorldAccessPlanFor(t.Context(), "Midgard"); err != nil || plan.InSync() {
		t.Fatalf("revoke did not become pending: %#v %v", plan, err)
	}
}

func TestApplyWorldAccessSendsGeneratedListsToTheAgent(t *testing.T) {
	server := testServer(t)
	accessTestWorld(t, server, "Midgard")
	for _, steamID := range []string{testSteamID, secondSteamID} {
		if err := server.store.GrantWorldAccess(t.Context(), "Midgard", steamID, "admin"); err != nil {
			t.Fatal(err)
		}
	}
	if err := server.store.SetWorldMemberRole(t.Context(), "Midgard", secondSteamID, "admin", "operator"); err != nil {
		t.Fatal(err)
	}
	if err := server.store.SetPermittedEnforcement(t.Context(), "Midgard", true, "operator"); err != nil {
		t.Fatal(err)
	}

	var seen agent.Request
	serveMockAgent(t, server, func(w http.ResponseWriter, r *http.Request) {
		json.NewDecoder(r.Body).Decode(&seen)
		json.NewEncoder(w).Encode(agent.Response{Status: "succeeded", Output: "Applied Midgard access lists: 1 admin, 2 permitted"})
	})

	adminPost(t, server, "/admin/worlds/Midgard/access-apply", url.Values{}, http.StatusSeeOther)

	if seen.Operation != "access_apply" || seen.World != "Midgard" {
		t.Fatalf("agent request = %#v", seen)
	}
	if seen.Admins != secondSteamID {
		t.Fatalf("admins sent = %q", seen.Admins)
	}
	if want := testSteamID + "," + secondSteamID; seen.Permitted != want {
		t.Fatalf("permitted sent = %q, want %q", seen.Permitted, want)
	}
	plan, err := server.store.WorldAccessPlanFor(t.Context(), "Midgard")
	if err != nil || !plan.InSync() {
		t.Fatalf("apply did not record the applied lists: %#v %v", plan, err)
	}
	jobs, err := server.store.RecentJobs(t.Context(), 5)
	if err != nil || len(jobs) == 0 || jobs[0].Operation != "access_apply" || jobs[0].Status != "succeeded" {
		t.Fatalf("jobs = %#v, %v", jobs, err)
	}
}

// A failing host must not be recorded as applied, or the admin page would claim
// the servers are in sync when they are not.
func TestApplyWorldAccessKeepsPendingWhenTheHostRejects(t *testing.T) {
	server := testServer(t)
	accessTestWorld(t, server, "Midgard")
	if err := server.store.GrantWorldAccess(t.Context(), "Midgard", testSteamID, "admin"); err != nil {
		t.Fatal(err)
	}
	if err := server.store.SetWorldMemberRole(t.Context(), "Midgard", testSteamID, "admin", "operator"); err != nil {
		t.Fatal(err)
	}
	serveMockAgent(t, server, func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(agent.Response{Status: "failed", Error: "permission denied"})
	})
	adminPost(t, server, "/admin/worlds/Midgard/access-apply", url.Values{}, http.StatusBadGateway)
	plan, err := server.store.WorldAccessPlanFor(t.Context(), "Midgard")
	if err != nil || plan.InSync() {
		t.Fatalf("a rejected apply was recorded as applied: %#v %v", plan, err)
	}
}

func TestVerifyPageReportsFileAndEnvDriftSeparately(t *testing.T) {
	server := testServer(t)
	accessTestWorld(t, server, "Midgard")
	if err := server.store.GrantWorldAccess(t.Context(), "Midgard", testSteamID, "admin"); err != nil {
		t.Fatal(err)
	}
	if err := server.store.SetWorldMemberRole(t.Context(), "Midgard", testSteamID, "admin", "operator"); err != nil {
		t.Fatal(err)
	}
	// The host has the right file but a stale env, which a container recreate
	// would turn back into the wrong adminlist.
	serveMockAgent(t, server, func(w http.ResponseWriter, r *http.Request) {
		state := AccessState{Admins: []string{testSteamID}, Permitted: []string{}, EnvAdmins: []string{}, EnvPermitted: []string{}, EnvPresent: true}
		data, _ := json.Marshal(state)
		json.NewEncoder(w).Encode(agent.Response{Status: "succeeded", Data: data})
	})
	request := adminTestRequest(http.MethodGet, "/admin/access", nil)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("verify page = %d: %s", response.Code, response.Body.String())
	}
	page := response.Body.String()
	if !strings.Contains(page, "valheim.env does not match the portal") {
		t.Fatalf("env drift not reported: %s", page)
	}
	if strings.Contains(page, "The live list files do not match the portal") {
		t.Fatal("file drift reported when the files matched")
	}
}

func TestRoleChangeRejectsUnknownRoleAndMissingGrant(t *testing.T) {
	server := testServer(t)
	accessTestWorld(t, server, "Midgard")
	if err := server.store.GrantWorldAccess(context.Background(), "Midgard", testSteamID, "admin"); err != nil {
		t.Fatal(err)
	}
	if err := server.store.SetWorldMemberRole(context.Background(), "Midgard", testSteamID, "owner", "operator"); err == nil {
		t.Fatal("an unknown role was accepted")
	}
	if err := server.store.SetWorldMemberRole(context.Background(), "Midgard", secondSteamID, "admin", "operator"); err == nil {
		t.Fatal("a role was set for an account without a grant")
	}
	adminPost(t, server, "/admin/world-members/role", url.Values{
		"world": {"Midgard"}, "steam_id": {testSteamID}, "role": {"admin"},
	}, http.StatusSeeOther)
	members, err := server.store.WorldMembers(context.Background())
	if err != nil || len(members) != 1 || !members[0].IsAdmin() {
		t.Fatalf("members = %#v, %v", members, err)
	}
}
