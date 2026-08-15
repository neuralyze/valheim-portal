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
	"world_status":      {ID: "world_status", Class: ClassRead, Operation: "status", NeedsWorld: true},
	"world_logs":        {ID: "world_logs", Class: ClassRead, Operation: "logs", NeedsWorld: true},
	"mod_inventory":     {ID: "mod_inventory", Class: ClassRead, Operation: "mod_inventory", NeedsWorld: true},
	"mod_search":        {ID: "mod_search", Class: ClassRead, Operation: "mod_search", NeedsWorld: true},
	"mod_check_updates": {ID: "mod_check_updates", Class: ClassRead, NeedsWorld: true, Unwired: "the host agent exposes no check-updates operation; tools/valheim_mods.py check-updates runs on the host only"},
	"mod_notes":         {ID: "mod_notes", Class: ClassRead, NeedsWorld: true, Unwired: "the host agent exposes no notes operation; tools/valheim_mods.py notes runs on the host only"},
	"release_status":    {ID: "release_status", Class: ClassRead, NeedsWorld: true, Unwired: "the host agent exposes no release-status operation"},
	"deploy_plan":       {ID: "deploy_plan", Class: ClassRead, NeedsWorld: true, Unwired: "mod_deploy applies; the host agent has no plan-only operation to read a diff from"},

	// repo_write - work inside the checkout, which the portal deliberately cannot perform.
	"repo_edit":    {ID: "repo_edit", Class: ClassRepoWrite, Unwired: "the portal never writes to a source checkout; the agent process edits files itself"},
	"plugin_build": {ID: "plugin_build", Class: ClassRepoWrite, Unwired: "tools/vrfixes/build.sh runs in the agent's own workspace, not through the portal"},

	// world_state
	"mod_add":       {ID: "mod_add", Class: ClassWorldState, Operation: "mod_add", NeedsWorld: true, NeedsIdentifier: true},
	"mod_remove":    {ID: "mod_remove", Class: ClassWorldState, Operation: "mod_remove", NeedsWorld: true, NeedsIdentifier: true},
	"mod_update":    {ID: "mod_update", Class: ClassWorldState, NeedsWorld: true, NeedsIdentifier: true, Unwired: "the host agent exposes no update operation"},
	"deploy_apply":  {ID: "deploy_apply", Class: ClassWorldState, Operation: "mod_deploy", NeedsWorld: true},
	"world_start":   {ID: "world_start", Class: ClassWorldState, Operation: "start", NeedsWorld: true},
	"world_stop":    {ID: "world_stop", Class: ClassWorldState, Operation: "stop", NeedsWorld: true},
	"world_backup":  {ID: "world_backup", Class: ClassWorldState, Operation: "backup", NeedsWorld: true},
	"world_restore": {ID: "world_restore", Class: ClassWorldState, NeedsWorld: true, Unwired: "restore keeps its typed two-step operator confirmation and is not reachable by verb"},

	// player_facing
	"publish_profile": {ID: "publish_profile", Class: ClassPlayerFacing, NeedsWorld: true, Unwired: "scripts/republish-profiles.sh runs on the host with artifact inputs; no agent operation exists"},
	"release_confirm": {ID: "release_confirm", Class: ClassPlayerFacing, NeedsWorld: true, Unwired: "the host agent exposes no release-confirm operation"},

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
	if strings.HasPrefix(verb.Operation, "mod_") && verb.Operation != "mod_deploy" {
		request := ModAgentRequest{
			Operation:  verb.Operation,
			Profile:    call.Profile,
			Query:      call.Query,
			Identifier: call.Identifier,
			Version:    call.Version,
			Reason:     call.Reason,
		}
		if verb.NeedsIdentifier && strings.TrimSpace(call.Identifier) == "" {
			return AgentReply{}, fmt.Errorf("verb %s needs an identifier", verb.ID)
		}
		return s.agent.RunMod(ctx, call.ID, call.World, request)
	}
	return s.agent.Run(ctx, call.ID, call.World, verb.Operation)
}
