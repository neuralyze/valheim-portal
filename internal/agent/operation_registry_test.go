package agent

import (
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"testing"
	"time"
)

// The portal and the agent are separate processes, so an operation the portal asks for that the
// agent never registered fails only at runtime - and quietly, because a page that cannot reach
// its agent is a legitimate state that the portal is written to survive. That is exactly how
// world_log_info shipped unnoticed through a passing suite: the log page rendered without its
// size line, and every test still passed because "no size line" and "no agent" look the same.
//
// So the registry is checked against its callers rather than trusted. Every Operation literal in
// internal/app must name a registered operation.
func TestEveryOperationThePortalAsksForIsRegistered(t *testing.T) {
	literal := regexp.MustCompile(`Operation:\s*"([a-z_]+)"`)

	sources, err := filepath.Glob(filepath.Join("..", "app", "*.go"))
	if err != nil {
		t.Fatal(err)
	}
	if len(sources) == 0 {
		t.Fatal("no portal sources found; this test would pass by finding nothing")
	}

	asked := map[string][]string{}
	for _, source := range sources {
		if strings.HasSuffix(source, "_test.go") {
			continue
		}
		body, err := os.ReadFile(source)
		if err != nil {
			t.Fatal(err)
		}
		for _, match := range literal.FindAllStringSubmatch(string(body), -1) {
			asked[match[1]] = append(asked[match[1]], filepath.Base(source))
		}
	}
	if len(asked) == 0 {
		t.Fatal("no operations found in the portal; the pattern no longer matches the code")
	}

	var names []string
	for name := range asked {
		names = append(names, name)
	}
	sort.Strings(names)

	for _, name := range names {
		if operations[name] == "" {
			t.Errorf("%s asks for operation %q, which is not in the agent's registry: the request fails Verify and the page reports an unreachable agent", strings.Join(asked[name], ", "), name)
		}
	}
	t.Logf("checked %d operations the portal asks for against %d registered", len(names), len(operations))
}

// Both log operations run the same script, and the info one carries no line count. A request that
// does not verify is indistinguishable, from the page, from an agent that is not running.
func TestBothLogOperationsVerify(t *testing.T) {
	token := []byte("12345678901234567890123456789012")
	allowed := map[string]struct{}{"Hrafnheim": {}}

	for _, tc := range []struct {
		operation string
		lines     int
	}{
		{"world_log", 200},
		{"world_log_info", 0},
	} {
		request := Request{ID: "log1", World: "Hrafnheim", Operation: tc.operation, Lines: tc.lines, Timestamp: time.Now().Unix()}
		request.Signature = Sign(token, request)
		if err := Verify(token, allowed, request); err != nil {
			t.Errorf("%s: %v", tc.operation, err)
		}
	}
}
