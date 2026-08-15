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
	response := bridgePost(t, server, "/api/agent/verb", `{"verb":"publish_profile","world":"TestWorld"}`)
	if response.Code != http.StatusNotImplemented {
		t.Fatalf("status = %d, want 501", response.Code)
	}
	if got := decode(t, response)["error"]; !strings.Contains(got.(string), "republish-profiles.sh") {
		t.Fatalf("error does not name what is missing: %v", got)
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
