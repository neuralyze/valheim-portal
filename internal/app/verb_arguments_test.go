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

// An argument the caller cannot see is an argument it cannot use: the agent asked for "the last 20
// lines" and had no way to say 20, so the portal's default answered instead.
func TestTheVocabularyAdvertisesOptionalArguments(t *testing.T) {
	verb, err := VerbByID("world_log_tail")
	if err != nil {
		t.Fatal(err)
	}
	if len(verb.Accepts) == 0 {
		t.Fatal("world_log_tail advertises no optional arguments, so a caller cannot choose a line count")
	}
	found := false
	for _, argument := range verb.Accepts {
		if argument == "lines" {
			found = true
		}
	}
	if !found {
		t.Errorf("accepts = %v, want it to include lines", verb.Accepts)
	}
}

// The chat verb's default has to fit a conversation. 200 lines produced a 20 KB message that the
// portal's own limit refused, which failed the pass rather than the message.
func TestTheChatLogDefaultFitsAMessage(t *testing.T) {
	server := testServer(t)
	// Sending no line count must not produce a request for hundreds of lines. The agent is absent,
	// so the call fails at the socket; what matters is the argument the portal built.
	_, err := server.runVerb(t.Context(), VerbCall{ID: "log3", Verb: "world_log_tail", World: "Hrafnheim"})
	if err != nil && strings.Contains(err.Error(), "line count") {
		t.Fatalf("the portal still sends no line count: %v", err)
	}
	// 40 lines of Valheim log is roughly 5 KB, comfortably inside the 20000-byte message limit.
	const chatDefault = 40
	if got := logLinesFor(VerbCall{Verb: "world_log_tail"}); got != chatDefault {
		t.Errorf("default line count = %d, want %d so the answer fits a conversation", got, chatDefault)
	}
}

// An operator asked the agent to search Thunderstore and got "invalid mod search". The vocabulary
// never said mod_search needs a query, so the agent could not know; and the host's refusal names no
// argument, so it could not learn. Both halves are fixed here: the contract is advertised, and it is
// checked before the request leaves with a message naming what is missing.
func TestSearchWithoutAQueryIsRefusedByName(t *testing.T) {
	verb, err := VerbByID("mod_search")
	if err != nil {
		t.Fatal(err)
	}
	if !verb.NeedsQuery {
		t.Fatal("mod_search does not declare that it needs a query")
	}
	advertised := strings.Join(argumentNames(verb), ", ")
	if !strings.Contains(advertised, "query") {
		t.Errorf("the vocabulary does not advertise the query: %s", advertised)
	}
	missing := missingArguments(verb, VerbCall{Verb: "mod_search", World: "Hrafnheim", Profile: "redesign-alpha"})
	if len(missing) == 0 || !strings.Contains(strings.Join(missing, ", "), "query") {
		t.Errorf("a search with no query is not reported as missing one: %v", missing)
	}
}

// mod_add is refused by the agent without a scope of exactly "shared" or "client-only", and the
// bridge had no field to carry one - so the verb could never succeed, which is the same defect
// world_log_tail had. The declaration and the field now exist together.
func TestAddDeclaresTheArgumentsTheAgentEnforces(t *testing.T) {
	verb, err := VerbByID("mod_add")
	if err != nil {
		t.Fatal(err)
	}
	advertised := strings.Join(argumentNames(verb), ", ")
	for _, want := range []string{"identifier", "version", "scope"} {
		if !strings.Contains(advertised, want) {
			t.Errorf("mod_add does not advertise %s: %s", want, advertised)
		}
	}
	complete := VerbCall{
		Verb: "mod_add", World: "Hrafnheim", Profile: "redesign-alpha",
		Identifier: "Azumatt-AzuExtendedPlayerInventory", Version: "2.4.5", Scope: "shared",
	}
	if missing := missingArguments(verb, complete); len(missing) > 0 {
		t.Errorf("a complete mod_add is reported as missing %v", missing)
	}
	for _, bad := range []string{"", "both", "server-only"} {
		partial := complete
		partial.Scope = bad
		if missing := missingArguments(verb, partial); len(missing) == 0 {
			t.Errorf("scope %q was accepted; the agent takes only shared or client-only", bad)
		}
	}
}

// The agent refuses a request carrying an argument its operation has no use for, so a model that
// explains itself by adding a reason to a search must not have the search refused for it.
func TestUndeclaredArgumentsAreDroppedNotForwarded(t *testing.T) {
	verb, err := VerbByID("mod_search")
	if err != nil {
		t.Fatal(err)
	}
	chatty := VerbCall{
		Verb: "mod_search", World: "Hrafnheim", Profile: "redesign-alpha", Query: "torch",
		Reason: "the operator asked me to look", Version: "1.0.0", Scope: "shared",
		Identifier: "Some-Mod", Notes: "a note nobody asked for",
	}

	filtered := onlyDeclaredArguments(verb, chatty)

	if filtered.Query != "torch" {
		t.Error("the argument the verb declares was dropped")
	}
	for name, value := range map[string]string{
		"reason": filtered.Reason, "version": filtered.Version, "scope": filtered.Scope,
		"identifier": filtered.Identifier, "notes": filtered.Notes,
	} {
		if value != "" {
			t.Errorf("%s was forwarded to an operation that refuses it: %q", name, value)
		}
	}
}
