package runner

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
)

// stubDecider answers with a fixed decision and records what it was asked, so the prompt can be
// asserted without a model.
type stubDecider struct {
	decision Decision
	err      error
	prompts  []string
}

func (s *stubDecider) Decide(_ context.Context, prompt string) (Decision, error) {
	s.prompts = append(s.prompts, prompt)
	return s.decision, s.err
}

// fakePortal is the bridge as the runner sees it, recording every call.
type fakePortal struct {
	server   *httptest.Server
	said     []string
	verbs    []map[string]any
	inbox    inbox
	verbCode int
	verbBody map[string]any
	vocab    []Verb
}

func newFakePortal(t *testing.T) *fakePortal {
	t.Helper()
	portal := &fakePortal{
		verbCode: http.StatusOK,
		verbBody: map[string]any{"status": "succeeded", "evidence": "check-updates: updates=0", "id": "call1"},
		vocab: []Verb{
			{ID: "mod_check_updates", Class: "read", Available: true, Needs: []string{"world", "profile"}},
			{ID: "deploy_apply", Class: "world_state", NeedsApproval: true, Available: true, Needs: []string{"world", "profile"}},
			{ID: "delete_server", Class: "forbidden", Available: false, Because: "forbidden by policy"},
			{ID: "world_restore", Class: "world_state", NeedsApproval: true, Available: false, Because: "keeps its typed two-step confirmation"},
		},
	}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /api/agent/verbs", func(w http.ResponseWriter, _ *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{"verbs": portal.vocab})
	})
	mux.HandleFunc("GET /api/agent/inbox", func(w http.ResponseWriter, _ *http.Request) {
		_ = json.NewEncoder(w).Encode(portal.inbox)
	})
	mux.HandleFunc("POST /api/agent/message", func(w http.ResponseWriter, r *http.Request) {
		var body struct{ Body string }
		_ = json.NewDecoder(r.Body).Decode(&body)
		portal.said = append(portal.said, body.Body)
		w.WriteHeader(http.StatusCreated)
		_ = json.NewEncoder(w).Encode(map[string]any{"id": len(portal.said)})
	})
	mux.HandleFunc("POST /api/agent/verb", func(w http.ResponseWriter, r *http.Request) {
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		portal.verbs = append(portal.verbs, body)
		w.WriteHeader(portal.verbCode)
		_ = json.NewEncoder(w).Encode(portal.verbBody)
	})
	portal.server = httptest.NewServer(mux)
	t.Cleanup(portal.server.Close)
	return portal
}

func newRunner(t *testing.T, portal *fakePortal, decider Decider) *Runner {
	t.Helper()
	r := New(Config{BaseURL: portal.server.URL, Token: strings.Repeat("t", 32), StatePath: filepath.Join(t.TempDir(), "cursor")}, decider)
	if _, err := r.Vocabulary(context.Background()); err != nil {
		t.Fatal(err)
	}
	return r
}

func TestAnOperatorTurnProducesAnAnswerAndAVerbCall(t *testing.T) {
	portal := newFakePortal(t)
	portal.inbox = inbox{Messages: []message{{ID: 1, Role: "operator", Body: "any mod updates?"}}, Cursor: 1}
	decider := &stubDecider{decision: Decision{
		Say:  "Checking Thunderstore for newer versions.",
		Verb: "mod_check_updates",
		Args: map[string]any{"world": "Hrafnheim", "profile": "redesign-alpha"},
	}}
	r := newRunner(t, portal, decider)
	if err := r.Step(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(portal.verbs) != 1 || portal.verbs[0]["verb"] != "mod_check_updates" {
		t.Fatalf("verb calls = %+v", portal.verbs)
	}
	if portal.verbs[0]["world"] != "Hrafnheim" {
		t.Fatalf("args were not forwarded: %+v", portal.verbs[0])
	}
	// The evidence the portal read back is quoted, not summarised into "done".
	if len(portal.said) != 2 || !strings.Contains(portal.said[1], "updates=0") {
		t.Fatalf("messages = %q", portal.said)
	}
	if r.Cursor() != 1 {
		t.Fatalf("cursor = %d, want 1", r.Cursor())
	}
}

func TestSystemTurnsAloneProvokeNoAction(t *testing.T) {
	// Verb outcomes arrive as system turns. Treating one as a new request is how a loop starts.
	portal := newFakePortal(t)
	portal.inbox = inbox{Messages: []message{{ID: 4, Role: "system", Body: "succeeded: mod_check_updates"}}, Cursor: 4}
	decider := &stubDecider{decision: Decision{Say: "should not be asked"}}
	r := newRunner(t, portal, decider)
	if err := r.Step(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(decider.prompts) != 0 || len(portal.said) != 0 || len(portal.verbs) != 0 {
		t.Fatalf("acted on a system turn: prompts=%d said=%q verbs=%d", len(decider.prompts), portal.said, len(portal.verbs))
	}
	if r.Cursor() != 4 {
		t.Fatalf("cursor did not advance past the system turn: %d", r.Cursor())
	}
}

func TestAPendingApprovalStopsFurtherWork(t *testing.T) {
	portal := newFakePortal(t)
	portal.inbox = inbox{
		Messages: []message{{ID: 7, Role: "operator", Body: "also deploy the mods"}},
		Cursor:   7,
		Awaiting: []map[string]any{{"id": "abc", "verb": "deploy_apply", "summary": "deploy_apply world=Hrafnheim"}},
	}
	decider := &stubDecider{decision: Decision{Verb: "mod_check_updates", Args: map[string]any{"world": "Hrafnheim"}}}
	r := newRunner(t, portal, decider)
	if err := r.Step(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(portal.verbs) != 0 {
		t.Fatalf("queued work behind a pending approval: %+v", portal.verbs)
	}
	if len(decider.prompts) != 0 {
		t.Fatal("asked the model while an approval was outstanding")
	}
	if len(portal.said) != 1 || !strings.Contains(portal.said[0], "deploy_apply world=Hrafnheim") {
		t.Fatalf("did not say what it is waiting for: %q", portal.said)
	}
}

func TestAwaitingApprovalIsReportedAsWaitingNotFailure(t *testing.T) {
	portal := newFakePortal(t)
	portal.inbox = inbox{Messages: []message{{ID: 2, Role: "operator", Body: "deploy the mods"}}, Cursor: 2}
	portal.verbCode = http.StatusAccepted
	portal.verbBody = map[string]any{"status": "pending_approval", "id": "call2"}
	decider := &stubDecider{decision: Decision{Verb: "deploy_apply", Args: map[string]any{"world": "Hrafnheim", "profile": "redesign-alpha"}}}
	r := newRunner(t, portal, decider)
	if err := r.Step(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(portal.said) != 1 || !strings.Contains(portal.said[0], "needs your approval") {
		t.Fatalf("messages = %q", portal.said)
	}
	if strings.Contains(strings.ToLower(strings.Join(portal.said, " ")), "failed") {
		t.Fatal("reported a pending approval as a failure")
	}
}

func TestAForbiddenVerbIsNotRetriedAndSaysSo(t *testing.T) {
	portal := newFakePortal(t)
	portal.inbox = inbox{Messages: []message{{ID: 3, Role: "operator", Body: "delete the server"}}, Cursor: 3}
	decider := &stubDecider{decision: Decision{Verb: "delete_server", Args: map[string]any{"world": "Hrafnheim"}}}
	r := newRunner(t, portal, decider)
	if err := r.Step(context.Background()); err != nil {
		t.Fatal(err)
	}
	// Refused from the vocabulary, so the portal is never even asked.
	if len(portal.verbs) != 0 {
		t.Fatalf("called a forbidden verb: %+v", portal.verbs)
	}
	if len(portal.said) != 1 || !strings.Contains(portal.said[0], "forbidden by policy") {
		t.Fatalf("messages = %q", portal.said)
	}
}

func TestAnUnavailableVerbReportsWhy(t *testing.T) {
	portal := newFakePortal(t)
	portal.inbox = inbox{Messages: []message{{ID: 5, Role: "operator", Body: "restore last night's backup"}}, Cursor: 5}
	decider := &stubDecider{decision: Decision{Verb: "world_restore", Args: map[string]any{"world": "Hrafnheim"}}}
	r := newRunner(t, portal, decider)
	if err := r.Step(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(portal.verbs) != 0 {
		t.Fatalf("called an unavailable verb: %+v", portal.verbs)
	}
	if len(portal.said) != 1 || !strings.Contains(portal.said[0], "two-step") {
		t.Fatalf("messages = %q", portal.said)
	}
}

func TestAnInventedVerbIsRejected(t *testing.T) {
	portal := newFakePortal(t)
	portal.inbox = inbox{Messages: []message{{ID: 6, Role: "operator", Body: "just fix it"}}, Cursor: 6}
	decider := &stubDecider{decision: Decision{Verb: "rm_minus_rf", Args: map[string]any{}}}
	r := newRunner(t, portal, decider)
	if err := r.Step(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(portal.verbs) != 0 {
		t.Fatalf("forwarded an invented verb: %+v", portal.verbs)
	}
	if len(portal.said) != 1 || !strings.Contains(portal.said[0], "does not exist") {
		t.Fatalf("messages = %q", portal.said)
	}
}

func TestAModelFailureIsReportedRatherThanSwallowed(t *testing.T) {
	portal := newFakePortal(t)
	portal.inbox = inbox{Messages: []message{{ID: 8, Role: "operator", Body: "status please"}}, Cursor: 8}
	decider := &stubDecider{err: context.DeadlineExceeded}
	r := newRunner(t, portal, decider)
	if err := r.Step(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(portal.said) != 1 || !strings.Contains(portal.said[0], "could not reach the model") {
		t.Fatalf("messages = %q", portal.said)
	}
}

func TestTheCursorSurvivesARestart(t *testing.T) {
	portal := newFakePortal(t)
	portal.inbox = inbox{Messages: []message{{ID: 11, Role: "system", Body: "noise"}}, Cursor: 11}
	state := filepath.Join(t.TempDir(), "cursor")
	first := New(Config{BaseURL: portal.server.URL, Token: strings.Repeat("t", 32), StatePath: state}, &stubDecider{})
	if _, err := first.Vocabulary(context.Background()); err != nil {
		t.Fatal(err)
	}
	if err := first.Step(context.Background()); err != nil {
		t.Fatal(err)
	}
	second := New(Config{BaseURL: portal.server.URL, Token: strings.Repeat("t", 32), StatePath: state}, &stubDecider{})
	second.loadCursor()
	if second.Cursor() != 11 {
		t.Fatalf("cursor after restart = %d, want 11; a replay would answer the same request twice", second.Cursor())
	}
}

func TestThePromptCarriesTheVocabularyAndTheRules(t *testing.T) {
	portal := newFakePortal(t)
	r := newRunner(t, portal, &stubDecider{})
	prompt := r.Prompt([]message{{Role: "operator", Body: "what is pending?"}})
	for _, want := range []string{
		"mod_check_updates (read)",
		"deploy_apply (world_state)",
		"[operator must approve]",
		"Not available, do not ask: delete_server, world_restore",
		"One verb per reply",
		"unmeasured",
		"operator: what is pending?",
	} {
		if !strings.Contains(prompt, want) {
			t.Fatalf("prompt is missing %q\n---\n%s", want, prompt)
		}
	}
}

func TestAssistantTextTakesTheFinalMessageFromOmpEvents(t *testing.T) {
	events := strings.Join([]string{
		`{"type":"session","id":"x"}`,
		`{"type":"message_start","message":{"role":"assistant","content":[{"type":"text","text":"partial"}]}}`,
		`{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"{\"say\":\"hello\"}"}]}}`,
		`{"type":"agent_end","messages":[]}`,
	}, "\n")
	text, err := AssistantText([]byte(events))
	if err != nil {
		t.Fatal(err)
	}
	decision, err := ParseDecision(text)
	if err != nil {
		t.Fatal(err)
	}
	if decision.Say != "hello" {
		t.Fatalf("decision = %+v", decision)
	}
}

func TestParseDecisionHandlesFencesAndRefusesProse(t *testing.T) {
	fenced := "Here you go:\n```json\n{\"verb\":\"mod_notes\",\"args\":{\"lines\":20}}\n```\n"
	decision, err := ParseDecision(fenced)
	if err != nil {
		t.Fatal(err)
	}
	if decision.Verb != "mod_notes" || decision.Args["lines"].(float64) != 20 {
		t.Fatalf("decision = %+v", decision)
	}
	if _, err := ParseDecision("I think we should probably deploy the mods now."); err == nil {
		t.Fatal("prose was accepted as a decision")
	}
}

func TestAnEmptyDecisionDoesNothingAndSaysSo(t *testing.T) {
	portal := newFakePortal(t)
	portal.inbox = inbox{Messages: []message{{ID: 9, Role: "operator", Body: "?"}}, Cursor: 9}
	r := newRunner(t, portal, &stubDecider{decision: Decision{}})
	if err := r.Step(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(portal.verbs) != 0 {
		t.Fatalf("acted on an empty decision: %+v", portal.verbs)
	}
	if len(portal.said) != 1 || !strings.Contains(portal.said[0], "done nothing") {
		t.Fatalf("messages = %q", portal.said)
	}
}

// A single pass must respect the persisted cursor. It did not: -once called Step directly while the
// cursor was loaded only inside Run, so every pass restarted from zero and answered the same
// operator question again - three passes over one question produced three answers.
func TestASinglePassDoesNotReanswerAnAlreadyAnsweredTurn(t *testing.T) {
	portal := newFakePortal(t)
	portal.inbox = inbox{Messages: []message{{ID: 21, Role: "operator", Body: "any mod updates?"}}, Cursor: 21}
	state := filepath.Join(t.TempDir(), "cursor")
	decider := &stubDecider{decision: Decision{Verb: "mod_check_updates", Args: map[string]any{"world": "Hrafnheim"}}}

	first := New(Config{BaseURL: portal.server.URL, Token: strings.Repeat("t", 32), StatePath: state}, decider)
	if _, err := first.Vocabulary(context.Background()); err != nil {
		t.Fatal(err)
	}
	if err := first.Step(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(portal.verbs) != 1 {
		t.Fatalf("first pass verb calls = %d, want 1", len(portal.verbs))
	}

	// A second process, same state file, and the inbox has moved past that turn.
	portal.inbox = inbox{Messages: []message{{ID: 22, Role: "agent", Body: "already answered"}}, Cursor: 22}
	second := New(Config{BaseURL: portal.server.URL, Token: strings.Repeat("t", 32), StatePath: state}, decider)
	if _, err := second.Vocabulary(context.Background()); err != nil {
		t.Fatal(err)
	}
	if second.Cursor() != 21 {
		t.Fatalf("a fresh runner starts at cursor %d; it must load the persisted 21", second.Cursor())
	}
	if err := second.Step(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(portal.verbs) != 1 {
		t.Fatalf("verb calls after the second pass = %d; the question was answered twice", len(portal.verbs))
	}
}
