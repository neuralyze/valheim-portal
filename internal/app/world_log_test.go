package app

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func logPage(t *testing.T, server *Server, query string) *httptest.ResponseRecorder {
	t.Helper()
	request := httptest.NewRequest(http.MethodGet, "/admin/worlds/TestWorld/log"+query, nil)
	request.RemoteAddr = "192.0.2.10:1234"
	request.Header.Set("X-Forwarded-User", "operator")
	request.Header.Set(adminTokenHeader, testAdminToken)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	return response
}

// With no agent reachable the page must still render and say why it is empty. An operator opening a
// log during an incident is the worst moment to be shown a blank 500.
func TestTheLogPageRendersAndExplainsWhenTheAgentIsUnreachable(t *testing.T) {
	server := testServer(t)
	response := logPage(t, server, "")
	if response.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", response.Code)
	}
	page := response.Body.String()
	if !strings.Contains(page, "the host agent is unreachable") {
		t.Fatalf("page does not explain the failure:\n%s", page)
	}
	// And it still explains the Info-level trap, which is the thing that would otherwise be read
	// as a broken server.
	if !strings.Contains(page, "Their absence is not a fault") {
		t.Error("the page does not warn that startup markers are Info-level and trimmed")
	}
}

func TestTheLogPageClampsTheLineCountAndFilter(t *testing.T) {
	server := testServer(t)
	page := logPage(t, server, "?lines=999999&filter="+strings.Repeat("x", 300)).Body.String()
	if !strings.Contains(page, `value="5000"`) {
		t.Error("a line count above the ceiling was not clamped to 5000")
	}
	if strings.Contains(page, strings.Repeat("x", 121)) {
		t.Error("an over-long filter was not truncated")
	}

	page = logPage(t, server, "?lines=notanumber").Body.String()
	if !strings.Contains(page, `value="200"`) {
		t.Error("a non-numeric line count should fall back to the default rather than erroring")
	}
}

func TestLogHelpersBoundWhatReachesTheHost(t *testing.T) {
	for raw, want := range map[string]int{"": 200, "0": 200, "-5": 200, "abc": 200, "50": 50, "5000": 5000, "5001": 5000, "999999": 5000} {
		if got := logLines(raw); got != want {
			t.Errorf("logLines(%q) = %d, want %d", raw, got, want)
		}
	}
	if got := logFilter("  Chainloader  "); got != "Chainloader" {
		t.Errorf("filter = %q, want trimmed", got)
	}
	// The script refuses a multi-line filter; collapsing here means a pasted line ending is not an
	// error an operator has to understand.
	if got := logFilter("first\nsecond"); strings.Contains(got, "\n") {
		t.Errorf("filter = %q, want newlines collapsed", got)
	}
	if got := logFilter(strings.Repeat("y", 200)); len(got) != 120 {
		t.Errorf("filter length = %d, want 120", len(got))
	}
}

// The download must not answer 200 with an error message: that saves to disk as a log whose
// contents are an apology.
func TestTheLogDownloadFailsRatherThanServingAnApology(t *testing.T) {
	server := testServer(t)
	request := httptest.NewRequest(http.MethodGet, "/admin/worlds/TestWorld/log.txt", nil)
	request.RemoteAddr = "192.0.2.10:1234"
	request.Header.Set("X-Forwarded-User", "operator")
	request.Header.Set(adminTokenHeader, testAdminToken)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want 503 when the agent cannot produce the log", response.Code)
	}
	if strings.Contains(response.Header().Get("Content-Type"), "text/plain") && response.Body.Len() > 40 {
		t.Error("a failed download should not carry a body that looks like a log")
	}
}

func TestTheLogRoutesAreAdminOnly(t *testing.T) {
	server := testServer(t)
	for _, target := range []string{"/admin/worlds/TestWorld/log", "/admin/worlds/TestWorld/log.txt"} {
		response := httptest.NewRecorder()
		server.Handler().ServeHTTP(response, httptest.NewRequest(http.MethodGet, target, nil))
		if response.Code != http.StatusUnauthorized {
			t.Errorf("GET %s without admin auth = %d, want 401; server logs name players and their Steam IDs", target, response.Code)
		}
	}
}
