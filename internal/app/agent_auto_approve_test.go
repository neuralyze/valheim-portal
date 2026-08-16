package app

import (
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// Auto-approval is the one setting that removes a human from the loop, so what it may and may
// not cover is asserted here rather than described. With no agent socket a run fails, which is
// what the existing approval tests rely on too: a failed run still proves the call was not
// parked waiting for a click.

func TestAnAutoApprovedVerbRunsWithoutAnOperator(t *testing.T) {
	server := bridgeServer(t)
	server.cfg.AgentAutoApprove = map[string]struct{}{"deploy_apply": {}}

	response := bridgePost(t, server, "/api/agent/verb", `{"verb":"deploy_apply","world":"TestWorld"}`)
	if response.Code == http.StatusAccepted {
		t.Fatalf("status = 202: the call was parked despite being pre-approved (body %s)", response.Body.String())
	}
	body := decode(t, response)
	if body["status"] == VerbPending {
		t.Fatalf("status = %v, want an executed outcome", body["status"])
	}

	pending, err := server.store.PendingVerbCalls(t.Context())
	if err != nil {
		t.Fatal(err)
	}
	if len(pending) != 0 {
		t.Fatalf("call is still waiting for a decision: %+v", pending)
	}

	call, err := server.store.VerbCall(t.Context(), body["id"].(string))
	if err != nil {
		t.Fatal(err)
	}
	// The record must not read as though a person looked at it.
	if call.DecidedBy != autoApproveActor {
		t.Fatalf("decided_by = %q, want %q", call.DecidedBy, autoApproveActor)
	}

	messages, err := server.store.AgentMessages(t.Context(), 10)
	if err != nil {
		t.Fatal(err)
	}
	var announced bool
	for _, message := range messages {
		if strings.Contains(message.Body, "Auto-approved by policy") {
			announced = true
		}
		if strings.Contains(message.Body, "Awaiting approval") {
			t.Fatalf("the conversation asked for a decision anyway: %q", message.Body)
		}
	}
	if !announced {
		t.Fatalf("the conversation does not record the auto-approval: %+v", messages)
	}
}

func TestAVerbTheDeploymentDidNotNameStillWaits(t *testing.T) {
	server := bridgeServer(t)
	server.cfg.AgentAutoApprove = map[string]struct{}{"deploy_apply": {}}

	response := bridgePost(t, server, "/api/agent/verb", `{"verb":"world_stop","world":"TestWorld"}`)
	if response.Code != http.StatusAccepted {
		t.Fatalf("status = %d, want 202: only the named verb is pre-approved", response.Code)
	}
}

func TestTheWorldStateTokenCoversTheWholeClass(t *testing.T) {
	server := bridgeServer(t)
	server.cfg.AgentAutoApprove = map[string]struct{}{string(ClassWorldState): {}}

	response := bridgePost(t, server, "/api/agent/verb", `{"verb":"world_stop","world":"TestWorld"}`)
	if response.Code == http.StatusAccepted {
		t.Fatalf("status = 202: the class token did not cover world_stop (body %s)", response.Body.String())
	}
}

func TestPublishingToPlayersIsNeverAutoApproved(t *testing.T) {
	server := bridgeServer(t)
	// The broadest setting a deployment can express, plus the verb named outright.
	server.cfg.AgentAutoApprove = map[string]struct{}{
		string(ClassWorldState): {}, "publish_profile": {},
	}

	response := bridgePost(t, server, "/api/agent/verb",
		`{"verb":"publish_profile","world":"TestWorld","client_type":"flat","notes":"a release note long enough"}`)
	if response.Code != http.StatusAccepted {
		t.Fatalf("status = %d, want 202: a release players download must wait for a person", response.Code)
	}
	pending, err := server.store.PendingVerbCalls(t.Context())
	if err != nil {
		t.Fatal(err)
	}
	if len(pending) != 1 || pending[0].Verb != "publish_profile" {
		t.Fatalf("publish did not park for approval: %+v", pending)
	}
}

func TestNothingIsAutoApprovedByDefault(t *testing.T) {
	server := bridgeServer(t)
	if summary := server.autoApproveSummary(); summary != "" {
		t.Fatalf("summary = %q, want empty: the default must gate everything", summary)
	}
	for _, id := range VerbIDs() {
		verb, err := VerbByID(id)
		if err != nil {
			t.Fatal(err)
		}
		if server.autoApproves(verb) {
			t.Fatalf("%s runs unattended with no setting", id)
		}
	}
}

func TestOnlyWorldStateVerbsAreEligible(t *testing.T) {
	for _, id := range VerbIDs() {
		verb, err := VerbByID(id)
		if err != nil {
			t.Fatal(err)
		}
		if !verb.AutoApprovable() {
			continue
		}
		if verb.Class != ClassWorldState {
			t.Fatalf("%s is %s but eligible for auto-approval", id, verb.Class)
		}
		if neverAutoApprove[id] {
			t.Fatalf("%s is on the never list and eligible anyway", id)
		}
	}
	// Destruction and restoration stay human whatever else changes around them.
	for _, id := range []string{"delete_server", "world_restore"} {
		verb, err := VerbByID(id)
		if err != nil {
			t.Fatal(err)
		}
		if verb.AutoApprovable() {
			t.Fatalf("%s must never be auto-approvable", id)
		}
	}
}

func TestTheSummaryNamesWhatRunsUnattended(t *testing.T) {
	server := bridgeServer(t)
	server.cfg.AgentAutoApprove = map[string]struct{}{"mod_add": {}, "deploy_apply": {}}
	if summary := server.autoApproveSummary(); summary != "deploy_apply, mod_add" {
		t.Fatalf("summary = %q, want the named verbs in a stable order", summary)
	}
	server.cfg.AgentAutoApprove = map[string]struct{}{string(ClassWorldState): {}}
	if summary := server.autoApproveSummary(); summary != "every world_state verb" {
		t.Fatalf("summary = %q, want the class phrasing", summary)
	}
}

// A name nobody enforces is the dangerous typo: the operator believes the gate is lifted and
// only finds out when a click they are not expecting never comes.
func TestLoadConfigRefusesAnAutoApproveNameItCannotEnforce(t *testing.T) {
	for name, want := range map[string]string{
		"deploy-apply":    "no such verb",
		"publish_profile": "keeps its confirmation",
		"delete_server":   "keeps its confirmation",
	} {
		t.Setenv("PORTAL_AGENT_AUTO_APPROVE", name)
		if _, err := loadConfigForTest(t); err == nil || !strings.Contains(err.Error(), want) {
			t.Fatalf("%s: error = %v, want one naming %q", name, err, want)
		}
	}
}

func TestLoadConfigAcceptsEligibleNamesAndTheClassToken(t *testing.T) {
	t.Setenv("PORTAL_AGENT_AUTO_APPROVE", "mod_add, deploy_apply world_state")
	cfg, err := loadConfigForTest(t)
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"mod_add", "deploy_apply", "world_state"} {
		if _, ok := cfg.AgentAutoApprove[want]; !ok {
			t.Fatalf("%q was dropped: %v", want, cfg.AgentAutoApprove)
		}
	}
}

// loadConfigForTest supplies the settings LoadConfig requires of any deployment, so a test can
// exercise one variable without restating the whole environment.
func loadConfigForTest(t *testing.T) (Config, error) {
	t.Helper()
	dir := t.TempDir()
	secret := filepath.Join(dir, "secret")
	if err := os.WriteFile(secret, []byte(strings.Repeat("s", 32)), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PORTAL_CSRF_SECRET_FILE", secret)
	t.Setenv("PORTAL_AGENT_TOKEN_FILE", secret)
	t.Setenv("PORTAL_TRUSTED_PROXY_CIDR", "192.0.2.0/24")
	t.Setenv("PORTAL_PUBLIC_BASE_URL", "https://portal.example.test")
	return LoadConfig()
}
