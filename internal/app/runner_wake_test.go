package app

import (
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// "Hit send and it answers" rests entirely on this file being written, and a wake that silently
// does nothing is indistinguishable from an agent with nothing to say - so the write is asserted
// rather than assumed. A timestamp-only touch was the first implementation and would have passed
// a weaker test while never triggering systemd's write watch.
func TestSendingAMessageWakesTheRunner(t *testing.T) {
	server := testServer(t)
	wake := filepath.Join(t.TempDir(), "agent-wake")
	server.cfg.AgentWakeFile = wake

	adminPost(t, server, "/admin/agent/message", url.Values{"body": {"any mod updates?"}}, http.StatusSeeOther)

	content, err := os.ReadFile(wake)
	if err != nil {
		t.Fatalf("no wake file after a message: %v", err)
	}
	if !strings.Contains(string(content), "operator message") {
		t.Errorf("the wake file does not say what woke it: %q", content)
	}
}

// Approving is the other thing the agent waits on: the work should continue without the operator
// also triggering a pass by hand.
func TestDecidingAVerbWakesTheRunner(t *testing.T) {
	server := testServer(t)
	wake := filepath.Join(t.TempDir(), "agent-wake")
	server.cfg.AgentWakeFile = wake

	id := "wake-decide-1"
	if err := server.store.CreateVerbCall(t.Context(), VerbCall{
		ID: id, Verb: "mod_check_updates", Class: "read", World: "Hrafnheim",
		Profile: "redesign-alpha", Status: VerbPending, RequestedBy: "agent",
	}); err != nil {
		t.Fatal(err)
	}
	adminPost(t, server, "/admin/agent/decide", url.Values{"id": {id}, "decision": {"deny"}}, http.StatusSeeOther)

	content, err := os.ReadFile(wake)
	if err != nil {
		t.Fatalf("no wake file after a decision: %v", err)
	}
	if !strings.Contains(string(content), "operator decision") {
		t.Errorf("the wake file does not say what woke it: %q", content)
	}
}

// A deployment that has not configured a wake file must behave exactly as before: the message is
// stored and the operator triggers passes by hand. This is the state of every deployment that
// predates the feature, so it cannot be allowed to error.
func TestAnAbsentWakeFileIsNotAnError(t *testing.T) {
	server := testServer(t)
	server.cfg.AgentWakeFile = ""

	// adminPost fails the test unless the redirect happens, which is the assertion.
	adminPost(t, server, "/admin/agent/message", url.Values{"body": {"still stored"}}, http.StatusSeeOther)

	messages, err := server.store.AgentMessages(t.Context(), 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(messages) == 0 {
		t.Error("the message was not stored")
	}
}

// An unwritable wake path must not cost the operator their message: they can trigger a pass with
// one command, and losing typed text to a failed convenience is a worse outcome.
func TestAnUnwritableWakeFileStillStoresTheMessage(t *testing.T) {
	server := testServer(t)
	server.cfg.AgentWakeFile = filepath.Join(t.TempDir(), "no-such-directory", "agent-wake")

	// The redirect is the assertion: a failed wake must not turn into a failed request.
	adminPost(t, server, "/admin/agent/message", url.Values{"body": {"kept"}}, http.StatusSeeOther)

	messages, err := server.store.AgentMessages(t.Context(), 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(messages) == 0 {
		t.Error("the message was lost because the wake failed")
	}
}
