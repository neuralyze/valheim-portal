package app

import (
	"context"
	"errors"
	"fmt"
	"sort"
	"strings"
)

// The verb table is the executable half of policy.yaml. Both exist on purpose: the YAML is what
// a harness and a human read, this is what the portal enforces, and tools/check_agent_policy.py
// fails CI when the two disagree. A policy that lives only in prose was tried here and did not
// survive a single working day.

type VerbClass string

const (
	ClassRead         VerbClass = "read"
	ClassRepoWrite    VerbClass = "repo_write"
	ClassWorldState   VerbClass = "world_state"
	ClassPlayerFacing VerbClass = "player_facing"
	ClassForbidden    VerbClass = "forbidden"
)

// Verb is one entry an agent may ask for by name. Operation is the portal operation that
// performs it; an empty Operation means the portal has no wiring for this verb yet, which is
// reported as such rather than approximated.
type Verb struct {
	ID         string
	Class      VerbClass
	Operation  string
	NeedsWorld bool
	// NeedsIdentifier marks the verbs that act on one package.
	NeedsIdentifier bool
	// NeedsClientType, NeedsNotes and NeedsRelease mark the arguments a publish or a release
	// confirmation cannot be performed without. A publish with no note is the thing nobody
	// could review afterwards, so the note is required rather than optional.
	NeedsClientType bool
	NeedsNotes      bool
	NeedsRelease    bool
	// Accepts lists the optional arguments a caller may set. Advertising only the required ones
	// left the agent unable to ask for "the last 20 lines": it could not know that `lines` exists,
	// so the portal's default answered instead - 200 lines, over the message limit, and the pass
	// died reporting its own answer. An argument the caller cannot see is an argument it cannot use.
	Accepts []string
	// Unwired explains what is missing, for verbs the policy declares but the portal cannot
	// yet run. Saying so is the point: a plausible substitute is worse than a refusal.
	Unwired string
}

// NeedsApproval reports whether an operator must confirm this call. Both mutating classes
// require it on every invocation, including repeats against other worlds, because each one
// implies downtime or a download for players.
func (v Verb) NeedsApproval() bool {
	return v.Class == ClassWorldState || v.Class == ClassPlayerFacing
}

var verbTable = map[string]Verb{
	// read
	"world_status": {ID: "world_status", Class: ClassRead, Operation: "status", NeedsWorld: true},
	"world_logs":   {ID: "world_logs", Class: ClassRead, Operation: "logs", NeedsWorld: true},
	// The collected host log rather than the live container: it survives a restart and a removed
	// container, which is the only way to read what happened before a crash.
	"world_log_tail":    {ID: "world_log_tail", Class: ClassRead, Operation: "world_log", NeedsWorld: true, Accepts: []string{"lines", "query"}},
	"mod_inventory":     {ID: "mod_inventory", Class: ClassRead, Operation: "mod_inventory", NeedsWorld: true},
	"mod_search":        {ID: "mod_search", Class: ClassRead, Operation: "mod_search", NeedsWorld: true},
	"mod_check_updates": {ID: "mod_check_updates", Class: ClassRead, Operation: "mod_check_updates", NeedsWorld: true},
	"mod_notes":         {ID: "mod_notes", Class: ClassRead, Operation: "mod_notes", NeedsWorld: true, Accepts: []string{"lines"}},
	"release_status":    {ID: "release_status", Class: ClassRead, Operation: "mod_release_status", NeedsWorld: true},
	"deploy_plan":       {ID: "deploy_plan", Class: ClassRead, Operation: "mod_deploy_plan", NeedsWorld: true},

	// repo_write - work inside the checkout, which the portal deliberately cannot perform.
	"repo_edit":    {ID: "repo_edit", Class: ClassRepoWrite, Unwired: "the portal never writes to a source checkout; the agent process edits files itself"},
	"plugin_build": {ID: "plugin_build", Class: ClassRepoWrite, Unwired: "tools/vrfixes/build.sh runs in the agent's own workspace, not through the portal"},

	// world_state
	"mod_add":       {ID: "mod_add", Class: ClassWorldState, Operation: "mod_add", NeedsWorld: true, NeedsIdentifier: true},
	"mod_remove":    {ID: "mod_remove", Class: ClassWorldState, Operation: "mod_remove", NeedsWorld: true, NeedsIdentifier: true},
	"mod_update":    {ID: "mod_update", Class: ClassWorldState, Operation: "mod_update", NeedsWorld: true, NeedsIdentifier: true},
	"deploy_apply":  {ID: "deploy_apply", Class: ClassWorldState, Operation: "mod_deploy", NeedsWorld: true},
	"world_start":   {ID: "world_start", Class: ClassWorldState, Operation: "start", NeedsWorld: true},
	"world_stop":    {ID: "world_stop", Class: ClassWorldState, Operation: "stop", NeedsWorld: true},
	"world_backup":  {ID: "world_backup", Class: ClassWorldState, Operation: "backup", NeedsWorld: true},
	"world_restore": {ID: "world_restore", Class: ClassWorldState, NeedsWorld: true, Unwired: "restore keeps its typed two-step operator confirmation and is not reachable by verb"},

	// player_facing
	"publish_profile": {ID: "publish_profile", Class: ClassPlayerFacing, Operation: "publish_profile", NeedsWorld: true, NeedsClientType: true, NeedsNotes: true},
	"release_confirm": {ID: "release_confirm", Class: ClassPlayerFacing, Operation: "mod_release_confirm", NeedsWorld: true, NeedsClientType: true, NeedsRelease: true},

	// forbidden - refused by name, and unreachable anyway because no wiring exists.
	"upstream_push": {ID: "upstream_push", Class: ClassForbidden},
	"delete_server": {ID: "delete_server", Class: ClassForbidden},
	"provision":     {ID: "provision", Class: ClassForbidden},
	"secrets_read":  {ID: "secrets_read", Class: ClassForbidden},
}

// ErrUnknownVerb is returned for a name the policy does not define. An agent gets this
// vocabulary and no shell, so an unrecognised name is a hard stop rather than an improvisation.
var ErrUnknownVerb = errors.New("unknown verb")

func VerbByID(id string) (Verb, error) {
	verb, ok := verbTable[strings.TrimSpace(id)]
	if !ok {
		return Verb{}, fmt.Errorf("%w: %q", ErrUnknownVerb, id)
	}
	return verb, nil
}

// VerbIDs lists every declared verb, sorted, for the policy consistency check and the UI.
func VerbIDs() []string {
	ids := make([]string, 0, len(verbTable))
	for id := range verbTable {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	return ids
}

// runVerb performs a verb that is already authorised: read and repo_write verbs go straight
// through, mutating ones only arrive here after an operator approved the call.
func (s *Server) runVerb(ctx context.Context, call VerbCall) (AgentReply, error) {
	verb, err := VerbByID(call.Verb)
	if err != nil {
		return AgentReply{}, err
	}
	switch {
	case verb.Class == ClassForbidden:
		return AgentReply{}, fmt.Errorf("verb %s is forbidden", verb.ID)
	case verb.Unwired != "":
		return AgentReply{}, fmt.Errorf("verb %s is not available through the portal: %s", verb.ID, verb.Unwired)
	case verb.NeedsWorld && !validWorld(call.World):
		return AgentReply{}, fmt.Errorf("verb %s needs a valid world", verb.ID)
	}
	if verb.NeedsIdentifier && strings.TrimSpace(call.Identifier) == "" {
		return AgentReply{}, fmt.Errorf("verb %s needs an identifier", verb.ID)
	}
	if verb.NeedsClientType && call.ClientType != "vr" && call.ClientType != "flat" {
		return AgentReply{}, fmt.Errorf("verb %s needs client type vr or flat", verb.ID)
	}
	if verb.NeedsNotes && len(strings.TrimSpace(call.Notes)) < 8 {
		return AgentReply{}, fmt.Errorf("verb %s needs a note of at least 8 characters saying why", verb.ID)
	}
	if verb.NeedsRelease && (call.PublishedProfile == "" || call.ReleaseRef == "" || call.Archive == "") {
		return AgentReply{}, fmt.Errorf("verb %s needs a published profile, a release id and an archive", verb.ID)
	}
	if verb.Operation == "publish_profile" {
		if !validProfileName(call.Profile) {
			return AgentReply{}, fmt.Errorf("verb %s needs the world's source profile", verb.ID)
		}
		return s.agent.RunPublish(ctx, call.ID, call.World, call.Profile, call.ClientType, call.Notes)
	}
	// Every mod_* operation is profile-scoped on the host: the agent refuses one without a
	// valid profile name, so the requirement belongs here rather than in a failure downstream.
	if strings.HasPrefix(verb.Operation, "mod_") {
		if !validProfileName(call.Profile) {
			return AgentReply{}, fmt.Errorf("verb %s needs a profile", verb.ID)
		}
		lines := call.Lines
		if verb.Operation == "mod_notes" && lines == 0 {
			lines = 20
		}
		return s.agent.RunMod(ctx, call.ID, call.World, ModAgentRequest{
			Operation:        verb.Operation,
			Profile:          call.Profile,
			Query:            call.Query,
			Identifier:       call.Identifier,
			Version:          call.Version,
			Reason:           call.Reason,
			Lines:            lines,
			ClientType:       call.ClientType,
			PublishedProfile: call.PublishedProfile,
			ReleaseID:        call.ReleaseRef,
			Archive:          call.Archive,
		})
	}
	// The log verb is world-scoped rather than profile-scoped, so it does not take the mod path
	// above, but it still carries arguments: without a line count the agent refuses the request,
	// and the generic call below sends none. 200 is the host script's own default - the end of the
	// log, which is what "show me the log" means.
	if verb.Operation == "world_log" {
		return s.agent.RunLog(ctx, call.ID, call.World, logLinesFor(call), call.Query)
	}
	return s.agent.Run(ctx, call.ID, call.World, verb.Operation)
}

// logLinesFor bounds what a log request asks for. The answer goes into a conversation whose own
// limit is 20000 bytes, so the default is a readable tail rather than the host script's 200 lines:
// 200 came back at 20012 bytes and the message carrying it was refused, failing the pass on its own
// answer. A caller that wants more can say so - `lines` is advertised in the vocabulary.
func logLinesFor(call VerbCall) int {
	if call.Lines < 1 || call.Lines > 5000 {
		return 40
	}
	return call.Lines
}

// validProfileName matches what the host scripts accept for a profile directory name.
func validProfileName(value string) bool {
	if len(value) == 0 || len(value) > 80 {
		return false
	}
	for i, r := range value {
		switch {
		case r >= 'a' && r <= 'z', r >= 'A' && r <= 'Z', r >= '0' && r <= '9':
		case (r == '.' || r == '_' || r == '-') && i > 0:
		default:
			return false
		}
	}
	return true
}
