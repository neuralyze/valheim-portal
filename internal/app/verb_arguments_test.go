package app

import (
	"strings"
	"testing"
)

// world_log_tail reached the agent with no line count, which the agent refuses - correctly - and
// the refusal then arrived as "invalid character 'o' in literal false (expecting 'a')". Two bugs in
// one message: a verb that could never succeed, and a refusal that could not be read. This covers
// the first: every verb whose operation needs arguments must be given them here, because the
// generic call sends only the world.
func TestTheLogVerbCarriesALineCount(t *testing.T) {
	server := testServer(t)

	// No agent is listening, so the call fails at the socket - after the arguments are built. The
	// assertion is on which failure happens: an argument complaint means the request was malformed
	// before it ever left, which is the defect.
	_, err := server.runVerb(t.Context(), VerbCall{
		ID: "log1", Verb: "world_log_tail", World: "Hrafnheim", Status: VerbPending,
	})
	if err == nil {
		t.Skip("an agent answered; this test only inspects how the request is built")
	}
	if strings.Contains(err.Error(), "line count") {
		t.Errorf("the portal sent no line count: %v", err)
	}
	if strings.Contains(err.Error(), "literal false") {
		t.Errorf("a refusal is still being decoded as JSON: %v", err)
	}
}

// A caller that asks for an absurd count must not have it forwarded: the host script would refuse
// it, and the operator would see a refusal about a number they never chose.
func TestAnOutOfRangeLineCountIsReplacedRatherThanForwarded(t *testing.T) {
	server := testServer(t)

	for _, lines := range []int{0, -5, 999999} {
		_, err := server.runVerb(t.Context(), VerbCall{
			ID: "log2", Verb: "world_log_tail", World: "Hrafnheim", Lines: lines, Status: VerbPending,
		})
		if err != nil && strings.Contains(err.Error(), "line count") {
			t.Errorf("lines=%d was forwarded to the agent: %v", lines, err)
		}
	}
}
