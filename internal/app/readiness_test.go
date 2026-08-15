package app

import (
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// A readiness check that reports what it did not test is worse than no check: the
// installer printed "readyz responded; the portal reached the agent socket" against a
// deployment whose /run/agent was empty, because systemd had recreated the agent's
// RuntimeDirectory beneath a running bind mount. Every operator action failed with
// "no such file or directory" and the only thing that noticed was an operator typing
// into the chat page.
func TestReadinessFailsWhenTheAgentSocketIsAbsent(t *testing.T) {
	server := testServer(t)
	server.cfg.AgentSocket = filepath.Join(t.TempDir(), "absent.sock")

	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/readyz", nil))

	if response.Code != http.StatusServiceUnavailable {
		t.Fatalf("readyz answered %d with no agent socket, want 503", response.Code)
	}
	if body := response.Body.String(); !strings.Contains(body, "agent socket") || !strings.Contains(body, "absent") {
		t.Errorf("the refusal does not name the missing socket: %q", body)
	}
}

func TestReadinessFailsWhenTheSocketPathIsNotASocket(t *testing.T) {
	server := testServer(t)
	// A plain file is what a bind mount of a deleted directory leaves behind when
	// something else recreates the path, so it is worth telling apart from absence.
	path := filepath.Join(t.TempDir(), "agent.sock")
	if err := os.WriteFile(path, []byte("not a socket"), 0o600); err != nil {
		t.Fatal(err)
	}
	server.cfg.AgentSocket = path

	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/readyz", nil))

	if response.Code != http.StatusServiceUnavailable {
		t.Fatalf("readyz answered %d for a non-socket path, want 503", response.Code)
	}
	if body := response.Body.String(); !strings.Contains(body, "not a socket") {
		t.Errorf("the refusal does not say what is wrong: %q", body)
	}
}

func TestReadinessPassesWithALiveAgentSocket(t *testing.T) {
	server := testServer(t)
	path := filepath.Join(t.TempDir(), "agent.sock")
	listener, err := net.Listen("unix", path)
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	server.cfg.AgentSocket = path

	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/readyz", nil))

	if response.Code != http.StatusOK {
		t.Fatalf("readyz answered %d with a live socket, want 200: %s", response.Code, response.Body.String())
	}
	if body := response.Body.String(); !strings.Contains(body, `"agent":"ok"`) {
		t.Errorf("readyz does not report the agent it checked: %q", body)
	}
}
