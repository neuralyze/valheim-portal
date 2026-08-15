package app

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

const testBridgeToken = "bridge-token-that-is-long-enough-000"

// bridgeServer is a test server with the agent bridge enabled. The token file has to exist
// before NewServer runs, which is why this cannot simply reuse testServer.
func bridgeServer(t *testing.T) *Server {
	t.Helper()
	dir := t.TempDir()
	tokenPath := filepath.Join(dir, "bridge-token")
	if err := os.WriteFile(tokenPath, []byte(testBridgeToken), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PORTAL_AGENT_BRIDGE_TOKEN_FILE", tokenPath)
	return testServer(t)
}

func bridgePost(t *testing.T, server *Server, path, body string) *httptest.ResponseRecorder {
	t.Helper()
	request := httptest.NewRequest(http.MethodPost, path, strings.NewReader(body))
	request.Header.Set("Authorization", "Bearer "+testBridgeToken)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	return response
}

func decode(t *testing.T, response *httptest.ResponseRecorder) map[string]any {
	t.Helper()
	var out map[string]any
	if err := json.Unmarshal(response.Body.Bytes(), &out); err != nil {
		t.Fatalf("response is not json: %v (%s)", err, response.Body.String())
	}
	return out
}

func TestBridgeIsDisabledUntilATokenIsConfigured(t *testing.T) {
	server := testServer(t) // no PORTAL_AGENT_BRIDGE_TOKEN_FILE
	response := bridgePost(t, server, "/api/agent/verb", `{"verb":"world_status","world":"TestWorld"}`)
	if response.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want 503 when no bridge token is configured", response.Code)
	}
	if !strings.Contains(response.Body.String(), "PORTAL_AGENT_BRIDGE_TOKEN_FILE") {
		t.Fatalf("the operator is not told how to enable it: %s", response.Body.String())
	}
}

func TestBridgeRefusesAWrongToken(t *testing.T) {
	server := bridgeServer(t)
	request := httptest.NewRequest(http.MethodPost, "/api/agent/verb", strings.NewReader(`{"verb":"world_status"}`))
	request.Header.Set("Authorization", "Bearer not-the-token")
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusUnauthorized {
		t.Fatalf("status = %d, want 401", response.Code)
	}
}

func TestAForbiddenVerbIsRefusedAndRecorded(t *testing.T) {
	server := bridgeServer(t)
	response := bridgePost(t, server, "/api/agent/verb", `{"verb":"delete_server","world":"TestWorld"}`)
	if response.Code != http.StatusForbidden {
		t.Fatalf("status = %d, want 403", response.Code)
	}
	body := decode(t, response)
	if body["status"] != VerbRefused {
		t.Fatalf("status field = %v, want %q", body["status"], VerbRefused)
	}
	calls, err := server.store.VerbCalls(t.Context(), 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(calls) != 1 || calls[0].Verb != "delete_server" || calls[0].Status != VerbRefused {
		t.Fatalf("refusal was not recorded: %+v", calls)
	}
}

func TestAnUnwiredVerbSaysSoRatherThanApproximating(t *testing.T) {
	server := bridgeServer(t)
	response := bridgePost(t, server, "/api/agent/verb", `{"verb":"world_restore","world":"TestWorld"}`)
	if response.Code != http.StatusNotImplemented {
		t.Fatalf("status = %d, want 501", response.Code)
	}
	if got := decode(t, response)["error"]; !strings.Contains(got.(string), "two-step") {
		t.Fatalf("error does not say why it is unavailable: %v", got)
	}
}

// A publish is the one verb whose arguments decide what players download, so the missing ones
// are reported before a row exists rather than discovered by the host script.
func TestPublishRefusesWithoutAClientTypeAndANote(t *testing.T) {
	server := bridgeServer(t)
	response := bridgePost(t, server, "/api/agent/verb", `{"verb":"publish_profile","world":"TestWorld","profile":"redesign-alpha"}`)
	if response.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400 (%s)", response.Code, response.Body.String())
	}
	if got := decode(t, response)["error"].(string); !strings.Contains(got, "client_type") {
		t.Fatalf("error does not name the missing argument: %v", got)
	}

	response = bridgePost(t, server, "/api/agent/verb",
		`{"verb":"publish_profile","world":"TestWorld","profile":"redesign-alpha","client_type":"vr"}`)
	if response.Code != http.StatusBadRequest {
		t.Fatalf("status without notes = %d, want 400", response.Code)
	}
	if got := decode(t, response)["error"].(string); !strings.Contains(got, "release note") {
		t.Fatalf("error does not explain the note: %v", got)
	}

	// With both, it becomes a pending request an operator must confirm.
	response = bridgePost(t, server, "/api/agent/verb",
		`{"verb":"publish_profile","world":"TestWorld","profile":"redesign-alpha","client_type":"vr","notes":"stop the label sweep"}`)
	if response.Code != http.StatusAccepted {
		t.Fatalf("status with arguments = %d, want 202 (%s)", response.Code, response.Body.String())
	}
	pending, err := server.store.PendingVerbCalls(t.Context())
	if err != nil {
		t.Fatal(err)
	}
	if len(pending) != 1 || pending[0].Notes != "stop the label sweep" || pending[0].ClientType != "vr" {
		t.Fatalf("arguments were not recorded: %+v", pending)
	}
}

func TestReleaseConfirmNeedsItsFourArguments(t *testing.T) {
	server := bridgeServer(t)
	response := bridgePost(t, server, "/api/agent/verb",
		`{"verb":"release_confirm","world":"TestWorld","profile":"redesign-alpha","client_type":"vr"}`)
	if response.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400", response.Code)
	}
	if got := decode(t, response)["error"].(string); !strings.Contains(got, "published_profile") {
		t.Fatalf("error does not name the missing arguments: %v", got)
	}
}

// A mod verb without a profile fails on the host, so the requirement is enforced before dispatch.
func TestAModVerbWithoutAProfileIsRejectedBeforeItReachesTheHost(t *testing.T) {
	server := bridgeServer(t)
	_, err := server.runVerb(t.Context(), VerbCall{ID: "x", Verb: "mod_inventory", World: "TestWorld"})
	if err == nil || !strings.Contains(err.Error(), "needs a profile") {
		t.Fatalf("error = %v, want a profile requirement", err)
	}
}

func TestAnUnknownVerbIsRejectedWithTheVocabulary(t *testing.T) {
	server := bridgeServer(t)
	response := bridgePost(t, server, "/api/agent/verb", `{"verb":"rm_minus_rf","world":"TestWorld"}`)
	if response.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400", response.Code)
	}
	body := decode(t, response)
	known, ok := body["known_verbs"].([]any)
	if !ok || len(known) != len(VerbIDs()) {
		t.Fatalf("known verbs not returned: %v", body["known_verbs"])
	}
}

// A world_state verb must never reach the agent on the strength of the agent asking.
func TestAWorldStateVerbWaitsForAnOperatorAndDoesNotRun(t *testing.T) {
	server := bridgeServer(t)
	response := bridgePost(t, server, "/api/agent/verb", `{"verb":"deploy_apply","world":"TestWorld"}`)
	if response.Code != http.StatusAccepted {
		t.Fatalf("status = %d, want 202 pending approval (body %s)", response.Code, response.Body.String())
	}
	body := decode(t, response)
	if body["status"] != VerbPending {
		t.Fatalf("status = %v, want %q", body["status"], VerbPending)
	}
	pending, err := server.store.PendingVerbCalls(t.Context())
	if err != nil {
		t.Fatal(err)
	}
	if len(pending) != 1 || pending[0].Verb != "deploy_apply" {
		t.Fatalf("call is not waiting for approval: %+v", pending)
	}
	// The conversation carries the request, so an operator sees it without polling the API.
	messages, err := server.store.AgentMessages(t.Context(), 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(messages) != 1 || !strings.Contains(messages[0].Body, "Awaiting approval") {
		t.Fatalf("operator was not told: %+v", messages)
	}
}

func TestDenyingAPendingCallRunsNothing(t *testing.T) {
	server := bridgeServer(t)
	body := decode(t, bridgePost(t, server, "/api/agent/verb", `{"verb":"world_stop","world":"TestWorld"}`))
	id := body["id"].(string)

	adminPost(t, server, "/admin/agent/decide", url.Values{"id": {id}, "decision": {"deny"}}, http.StatusSeeOther)
	call, err := server.store.VerbCall(t.Context(), id)
	if err != nil {
		t.Fatal(err)
	}
	if call.Status != VerbDenied {
		t.Fatalf("status = %q, want %q", call.Status, VerbDenied)
	}
	if call.Evidence != "" {
		t.Fatalf("a denied call has no evidence, got %q", call.Evidence)
	}
}

// Approval runs the verb. With no agent listening on the socket the run fails, which is the
// useful assertion here: the failure is recorded against the approver rather than swallowed.
func TestApprovalExecutesAndRecordsTheOutcomeAgainstTheApprover(t *testing.T) {
	server := bridgeServer(t)
	body := decode(t, bridgePost(t, server, "/api/agent/verb", `{"verb":"world_backup","world":"TestWorld"}`))
	id := body["id"].(string)

	adminPost(t, server, "/admin/agent/decide", url.Values{"id": {id}, "decision": {"approve"}}, http.StatusSeeOther)
	call, err := server.store.VerbCall(t.Context(), id)
	if err != nil {
		t.Fatal(err)
	}
	if call.Status != VerbFailed {
		t.Fatalf("status = %q; with no agent socket the run must fail, not appear to succeed", call.Status)
	}
	if call.DecidedBy == "" {
		t.Fatal("the approver was not recorded")
	}
	if call.Detail == "" {
		t.Fatal("the failure reason was not recorded")
	}
}

func TestADecisionOnAnAlreadyDecidedCallIsRejected(t *testing.T) {
	server := bridgeServer(t)
	id := decode(t, bridgePost(t, server, "/api/agent/verb", `{"verb":"world_stop","world":"TestWorld"}`))["id"].(string)
	deny := url.Values{"id": {id}, "decision": {"deny"}}
	adminPost(t, server, "/admin/agent/decide", deny, http.StatusSeeOther)
	adminPost(t, server, "/admin/agent/decide", deny, http.StatusConflict)
}

func TestTheOperatorPageShowsPendingWorkAndTheConversation(t *testing.T) {
	server := bridgeServer(t)
	bridgePost(t, server, "/api/agent/verb", `{"verb":"mod_remove","world":"TestWorld","identifier":"OdinPlus-OdinHorse"}`)
	bridgePost(t, server, "/api/agent/message", `{"body":"Proposing to remove OdinHorse; it implements no riding in VR."}`)

	request := httptest.NewRequest(http.MethodGet, "/admin/agent", nil)
	request.Header.Set("X-Forwarded-User", "operator")
	request.Header.Set(adminTokenHeader, testAdminToken)
	request.RemoteAddr = "192.0.2.10:1234"
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("page status = %d", response.Code)
	}
	page := response.Body.String()
	for _, want := range []string{"OdinPlus-OdinHorse", "pending_approval", "Approve", "Deny", "implements no riding"} {
		if !strings.Contains(page, want) {
			t.Fatalf("page is missing %q", want)
		}
	}
}

func TestTheInboxAdvancesACursorAndReportsPendingApprovals(t *testing.T) {
	server := bridgeServer(t)
	if _, err := server.store.AppendAgentMessage(t.Context(), "operator", "first"); err != nil {
		t.Fatal(err)
	}
	bridgePost(t, server, "/api/agent/verb", `{"verb":"world_start","world":"TestWorld"}`)

	request := httptest.NewRequest(http.MethodGet, "/api/agent/inbox?since=0", nil)
	request.Header.Set("Authorization", "Bearer "+testBridgeToken)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	body := decode(t, response)
	messages := body["messages"].([]any)
	if len(messages) != 2 {
		t.Fatalf("expected the operator turn and the awaiting-approval note, got %d", len(messages))
	}
	cursor := int64(body["cursor"].(float64))
	if cursor == 0 {
		t.Fatal("cursor did not advance")
	}
	if len(body["awaiting_approval"].([]any)) != 1 {
		t.Fatalf("pending approvals not reported: %v", body["awaiting_approval"])
	}

	// Polling from the cursor returns nothing rather than replaying the conversation.
	second := httptest.NewRequest(http.MethodGet, "/api/agent/inbox?since="+itoa(cursor), nil)
	second.Header.Set("Authorization", "Bearer "+testBridgeToken)
	secondResponse := httptest.NewRecorder()
	server.Handler().ServeHTTP(secondResponse, second)
	if got := decode(t, secondResponse)["messages"].([]any); len(got) != 0 {
		t.Fatalf("cursor did not suppress replay: %v", got)
	}
}

func TestEveryPolicyClassIsRepresentedAndMutatingOnesNeedApproval(t *testing.T) {
	seen := map[VerbClass]int{}
	for _, id := range VerbIDs() {
		verb, err := VerbByID(id)
		if err != nil {
			t.Fatalf("%s: %v", id, err)
		}
		seen[verb.Class]++
		switch verb.Class {
		case ClassWorldState, ClassPlayerFacing:
			if !verb.NeedsApproval() {
				t.Fatalf("%s is %s but needs no approval", id, verb.Class)
			}
		case ClassRead, ClassRepoWrite:
			if verb.NeedsApproval() {
				t.Fatalf("%s is %s but demands approval", id, verb.Class)
			}
		case ClassForbidden:
			if verb.Operation != "" {
				t.Fatalf("%s is forbidden but is wired to operation %q", id, verb.Operation)
			}
		}
	}
	for _, class := range []VerbClass{ClassRead, ClassRepoWrite, ClassWorldState, ClassPlayerFacing, ClassForbidden} {
		if seen[class] == 0 {
			t.Fatalf("no verb has class %s", class)
		}
	}
}

func itoa(v int64) string { return fmtInt(v) }

// The verb surface must not widen the one-click job form. Those operations need arguments and a
// class check; a button that posts an operation name is neither.
func TestTheGenericJobFormStillRefusesTheVerbOnlyOperations(t *testing.T) {
	for _, operation := range []string{"publish_profile", "mod_release_confirm", "mod_update", "mod_notes"} {
		if allowedOperation(operation) {
			t.Fatalf("%s is reachable through POST /admin/jobs; it must only be reachable as a verb", operation)
		}
	}
}

func TestTheStatusEndpointReportsActivityAndIsAdminOnly(t *testing.T) {
	server := bridgeServer(t)
	bridgePost(t, server, "/api/agent/verb", `{"verb":"world_stop","world":"TestWorld"}`)

	// Admin-only: the control surface must not report its state to an unauthenticated caller.
	plain := httptest.NewRecorder()
	server.Handler().ServeHTTP(plain, httptest.NewRequest(http.MethodGet, "/admin/agent/status.json", nil))
	if plain.Code != http.StatusUnauthorized {
		t.Fatalf("unauthenticated status = %d, want 401", plain.Code)
	}

	request := httptest.NewRequest(http.MethodGet, "/admin/agent/status.json", nil)
	request.RemoteAddr = "192.0.2.10:1234"
	request.Header.Set("X-Forwarded-User", "operator")
	request.Header.Set(adminTokenHeader, testAdminToken)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("status = %d", response.Code)
	}
	body := decode(t, response)
	if body["pending"].(float64) != 1 {
		t.Fatalf("pending = %v, want 1", body["pending"])
	}
	if body["state"] == "" {
		t.Fatal("no state token for the page to compare against")
	}
}

func TestAPendingCallShowsEveryArgumentNotASummary(t *testing.T) {
	server := bridgeServer(t)
	bridgePost(t, server, "/api/agent/verb",
		`{"verb":"publish_profile","world":"TestWorld","profile":"redesign-alpha","client_type":"vr","notes":"stop the label sweep"}`)
	page := agentPage(t, server)
	// An operator approving a publish must see the note and the client type, not "publish_profile".
	for _, want := range []string{
		">client type<", ">vr<", ">note<", "stop the label sweep", ">profile<", "redesign-alpha",
		"This publishes what players download",
	} {
		if !strings.Contains(page, want) {
			t.Fatalf("page is missing %q", want)
		}
	}
}

func TestAPublishApprovalShowsWhatIsAlreadyLive(t *testing.T) {
	server := bridgeServer(t)
	ctx := t.Context()
	// Two published releases for this world: the context the policy asks for beside a publish -
	// what players have now, and how much has already gone out. Written straight to the table
	// because the real publish path demands a verified profile definition, which is a different
	// test's subject.
	for index, version := range []string{"2.5.90", "2.5.91"} {
		// Distinct timestamps, so "newest" is unambiguous and the assertion is about the page
		// rather than about which row SQLite happened to pick.
		when := time.Now().UTC().Add(time.Duration(index) * time.Minute).Format(time.RFC3339Nano)
		if _, err := server.store.db.ExecContext(ctx, `
INSERT INTO releases(id, world, profile, client_type, version, notes, status, created_at, published_at, published_by)
VALUES(?,?,?,?,?,?,'published',?,?,'operator')`,
			"testworld-vr-"+version, "TestWorld", "testworld-vr", "vr", version, "seeded", when, when); err != nil {
			t.Fatal(err)
		}
	}
	bridgePost(t, server, "/api/agent/verb",
		`{"verb":"publish_profile","world":"TestWorld","profile":"redesign-alpha","client_type":"vr","notes":"another attempt at the same thing"}`)
	page := agentPage(t, server)
	if !strings.Contains(page, "Currently live") || !strings.Contains(page, "2.5.91") {
		t.Fatalf("the approval does not show what is live:\n%s", page)
	}
	if !strings.Contains(page, "release(s) for this world in the last day") {
		t.Fatal("the approval does not show how much has already been published")
	}
}

func TestLongEvidenceIsCollapsedSoTheButtonsStayReachable(t *testing.T) {
	server := bridgeServer(t)
	ctx := t.Context()
	call := VerbCall{ID: "long1", Verb: "world_logs", Class: string(ClassRead), World: "TestWorld", Status: VerbPending}
	if err := server.store.CreateVerbCall(ctx, call); err != nil {
		t.Fatal(err)
	}
	if err := server.store.FinishVerbCall(ctx, call.ID, VerbSucceeded, "", strings.Repeat("a log line\n", 40), ""); err != nil {
		t.Fatal(err)
	}
	page := agentPage(t, server)
	if !strings.Contains(page, "<details><summary>Evidence read back by the portal</summary>") {
		t.Fatal("40 lines of evidence were not collapsed")
	}

	short := VerbCall{ID: "short1", Verb: "world_status", Class: string(ClassRead), World: "TestWorld", Status: VerbPending}
	if err := server.store.CreateVerbCall(ctx, short); err != nil {
		t.Fatal(err)
	}
	if err := server.store.FinishVerbCall(ctx, short.ID, VerbSucceeded, "", "up 2 minutes", ""); err != nil {
		t.Fatal(err)
	}
	page = agentPage(t, server)
	if !strings.Contains(page, "up 2 minutes") {
		t.Fatal("short evidence should be shown directly")
	}
}

func TestThePageAsksForFastPollingOnlyWhileSomethingWaits(t *testing.T) {
	server := bridgeServer(t)
	if page := agentPage(t, server); !strings.Contains(page, `data-agent-busy="false"`) || !strings.Contains(page, "nothing pending") {
		t.Fatal("an idle page should not ask to be polled every five seconds")
	}
	bridgePost(t, server, "/api/agent/verb", `{"verb":"world_stop","world":"TestWorld"}`)
	page := agentPage(t, server)
	if !strings.Contains(page, `data-agent-busy="true"`) || !strings.Contains(page, "awaiting your decision") {
		t.Fatal("a pending approval should mark the page busy")
	}
	if !strings.Contains(page, `<script src="/assets/admin-agent.js"></script>`) {
		t.Fatal("the refresh script is not loaded")
	}
}

func TestTheAdminHomeLinksToTheAgentAndCountsWhatWaits(t *testing.T) {
	server := bridgeServer(t)
	bridgePost(t, server, "/api/agent/verb", `{"verb":"world_stop","world":"TestWorld"}`)
	page := adminPage(t, server)
	if !strings.Contains(page, `href="/admin/agent"`) {
		t.Fatal("an operator cannot reach the agent page from the admin home")
	}
	if !strings.Contains(page, "1 awaiting you") {
		t.Fatalf("the admin home does not say a request is waiting")
	}
}

// agentPage renders /admin/agent the way an operator sees it.
func agentPage(t *testing.T, server *Server) string {
	t.Helper()
	request := httptest.NewRequest(http.MethodGet, "/admin/agent", nil)
	request.RemoteAddr = "192.0.2.10:1234"
	request.Header.Set("X-Forwarded-User", "operator")
	request.Header.Set(adminTokenHeader, testAdminToken)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("/admin/agent = %d: %s", response.Code, response.Body.String())
	}
	return response.Body.String()
}

// Both of these were found by looking at the page in a browser, not by any assertion: the agent
// page used the dashboard's four-column grid, which scattered headings beside cards and broke the
// conversation into columns, and the header rendered "Administration" twice.
func TestTheAgentPageUsesTheSingleColumnLayoutAndOneNavLink(t *testing.T) {
	server := bridgeServer(t)
	page := agentPage(t, server)
	if !strings.Contains(page, `<main class="shell"`) {
		t.Error(`the agent page must use class="shell"; admin-overview is a four-column grid meant for dashboard tiles`)
	}
	if strings.Contains(page, "admin-overview") {
		t.Error("the agent page still references the dashboard grid layout")
	}
	if count := strings.Count(page, ">Administration</a>"); count != 1 {
		t.Errorf("the header renders %d Administration links, want 1", count)
	}
}

// The operator's real question while nothing is on screen is "is this still alive". The page answers
// it from the conversation - the newest turn is theirs, so the agent owes a reply - rather than from
// a flag some process sets and a killed process never clears.
func TestThePageSaysTheAgentIsWorkingWhileItOwesAReply(t *testing.T) {
	server := bridgeServer(t)

	if page := agentPage(t, server); strings.Contains(page, "The agent is working") {
		t.Fatal("an empty conversation should not claim the agent is working")
	}

	if _, err := server.store.AppendAgentMessage(t.Context(), "operator", "any mod updates?"); err != nil {
		t.Fatal(err)
	}
	page := agentPage(t, server)
	if !strings.Contains(page, `data-agent-waiting="true"`) || !strings.Contains(page, "The agent is working") {
		t.Fatal("an unanswered operator turn should show the working indicator")
	}
	if !strings.Contains(page, `class="spinner"`) || !strings.Contains(page, "data-agent-elapsed") {
		t.Error("the indicator has neither motion nor an elapsed counter, so it cannot show the page is live")
	}

	// The agent answering is what clears it, not a timer and not a reload.
	if _, err := server.store.AppendAgentMessage(t.Context(), "agent", "updates=0"); err != nil {
		t.Fatal(err)
	}
	if page := agentPage(t, server); strings.Contains(page, "The agent is working") || !strings.Contains(page, `data-agent-waiting="false"`) {
		t.Error("the indicator survived the reply it was waiting for")
	}
}

// status.json carries the same fact, because the page polls it rather than re-rendering to find out.
func TestTheStatusEndpointReportsWhetherTheAgentOwesATurn(t *testing.T) {
	server := bridgeServer(t)
	if _, err := server.store.AppendAgentMessage(t.Context(), "operator", "hello"); err != nil {
		t.Fatal(err)
	}

	request := httptest.NewRequest(http.MethodGet, "/admin/agent/status.json", nil)
	request.RemoteAddr = "192.0.2.10:1234"
	request.Header.Set("X-Forwarded-User", "operator")
	request.Header.Set(adminTokenHeader, testAdminToken)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)

	var state struct {
		State   string `json:"state"`
		Waiting bool   `json:"waiting"`
	}
	if err := json.Unmarshal(response.Body.Bytes(), &state); err != nil {
		t.Fatalf("status is not JSON: %q", response.Body.String())
	}
	if !state.Waiting {
		t.Error("status.json does not report that the agent owes a turn")
	}
	// The flag must be inside the state token, or the page never notices it changing.
	if !strings.HasSuffix(state.State, "/true") {
		t.Errorf("state token %q does not carry the waiting flag, so a flip goes unnoticed", state.State)
	}
}

// The dock is the agent where the operator already is. It reads its own endpoint because the bridge
// endpoints need the bridge token, and a browser must never hold that.
func TestTheAdminHomeCarriesTheAgentDock(t *testing.T) {
	server := bridgeServer(t)
	page := adminPage(t, server)

	for _, want := range []string{
		"data-agent-dock",             // the element the script binds to
		`src="/assets/admin-dock.js"`, // and the script itself
		`href="/admin/agent"`,         // approvals happen on the full page
		"Ctrl+Enter sends",            // the shortcut is discoverable
		`name="csrf"`,                 // posting from the dock is CSRF-protected
	} {
		if !strings.Contains(page, want) {
			t.Errorf("the admin home is missing %q", want)
		}
	}
	// The dock must not carry Approve buttons: a decision made from a corner summary, without the
	// arguments in front of you, is the habit the full page exists to prevent.
	dock := page[strings.Index(page, "data-agent-dock"):]
	if strings.Contains(dock, `value="approve"`) {
		t.Error("the dock offers approval without showing what is being approved")
	}
}

func TestTheDockEndpointServesTheConversationTail(t *testing.T) {
	server := bridgeServer(t)
	if _, err := server.store.AppendAgentMessage(t.Context(), "operator", "back up Hrafnheim"); err != nil {
		t.Fatal(err)
	}

	request := httptest.NewRequest(http.MethodGet, "/admin/agent/tail.json", nil)
	request.RemoteAddr = "192.0.2.10:1234"
	request.Header.Set("X-Forwarded-User", "operator")
	request.Header.Set(adminTokenHeader, testAdminToken)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)

	if response.Code != http.StatusOK {
		t.Fatalf("tail.json = %d: %s", response.Code, response.Body.String())
	}
	var payload struct {
		Waiting bool `json:"waiting"`
		Bridge  bool `json:"bridge_enabled"`
		Turns   []struct {
			Role string `json:"role"`
			Body string `json:"body"`
		} `json:"turns"`
	}
	if err := json.Unmarshal(response.Body.Bytes(), &payload); err != nil {
		t.Fatalf("not JSON: %q", response.Body.String())
	}
	if len(payload.Turns) == 0 || payload.Turns[len(payload.Turns)-1].Body != "back up Hrafnheim" {
		t.Errorf("tail does not end with the newest turn: %+v", payload.Turns)
	}
	if !payload.Waiting {
		t.Error("tail.json does not report that the agent owes a turn")
	}
	if !payload.Bridge {
		t.Error("tail.json does not report the bridge state, so the dock cannot explain silence")
	}
}

func TestTheDockEndpointIsAdminOnly(t *testing.T) {
	server := bridgeServer(t)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/admin/agent/tail.json", nil))
	if response.Code != http.StatusUnauthorized {
		t.Errorf("unauthenticated tail.json = %d, want 401", response.Code)
	}
}
