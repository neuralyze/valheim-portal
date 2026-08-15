package agent

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// A refusal must answer in the same shape as a success. This one was plain text, the caller decoded
// it as JSON, and an operator was shown "invalid character 'o' in literal false (expecting 'a')" -
// the word "forbidden" being read as the literal false.
func TestARefusedRequestAnswersJSON(t *testing.T) {
	handler := testHandler(t)

	for _, tc := range []struct {
		name    string
		request Request
		wants   string
	}{
		{
			// An argument problem: the vocabulary is public, so the caller is told what is wrong.
			name:    "argument",
			request: Request{ID: "a1", World: "Hrafnheim", Operation: "world_log"},
			wants:   "line count",
		},
		{
			// A capability problem: no explanation, because the answer would describe the fence.
			name:    "capability",
			request: Request{ID: "a2", World: "NotMyWorld", Operation: "status"},
			wants:   "forbidden",
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			body, _ := json.Marshal(signed(tc.request))
			response := httptest.NewRecorder()
			handler.ServeHTTP(response, httptest.NewRequest(http.MethodPost, "/v1/jobs", strings.NewReader(string(body))))

			if response.Code == http.StatusOK {
				t.Fatalf("the request was accepted: %s", response.Body.String())
			}
			var reply Response
			if err := json.Unmarshal(response.Body.Bytes(), &reply); err != nil {
				t.Fatalf("refusal body is not JSON (%v): %q", err, response.Body.String())
			}
			if reply.Status != "failed" {
				t.Errorf("status = %q, want failed", reply.Status)
			}
			if !strings.Contains(reply.Error, tc.wants) {
				t.Errorf("error = %q, want it to mention %q", reply.Error, tc.wants)
			}
		})
	}
}

// A capability refusal must not name the fence it hit.
func TestACapabilityRefusalExplainsNothing(t *testing.T) {
	handler := testHandler(t)
	stale := Request{ID: "s1", World: "Hrafnheim", Operation: "status"}
	stale.Timestamp = time.Now().Add(-2 * time.Hour).Unix()
	stale.Signature = Sign([]byte(surfaceToken), stale)

	body, _ := json.Marshal(stale)
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, httptest.NewRequest(http.MethodPost, "/v1/jobs", strings.NewReader(string(body))))

	var reply Response
	if err := json.Unmarshal(response.Body.Bytes(), &reply); err != nil {
		t.Fatalf("refusal body is not JSON: %q", response.Body.String())
	}
	if reply.Error != "forbidden" {
		t.Errorf("error = %q, want exactly \"forbidden\": a stale signature must not describe itself", reply.Error)
	}
}

func testHandler(t *testing.T) http.Handler {
	t.Helper()
	dir := t.TempDir()
	scripts := filepath.Join(dir, "scripts")
	worlds := filepath.Join(dir, "worlds", "Hrafnheim")
	for _, d := range []string{scripts, worlds} {
		if err := os.MkdirAll(d, 0o750); err != nil {
			t.Fatal(err)
		}
	}
	tokenFile := filepath.Join(dir, "token")
	if err := os.WriteFile(tokenFile, []byte(surfaceToken), 0o600); err != nil {
		t.Fatal(err)
	}
	handler, err := NewHandler(Config{
		TokenFile:     tokenFile,
		ScriptDir:     scripts,
		WorldRoot:     filepath.Dir(worlds),
		AllowedWorlds: allowOne("Hrafnheim"),
	})
	if err != nil {
		t.Fatal(err)
	}
	return handler
}
