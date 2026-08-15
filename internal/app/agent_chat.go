package app

import (
	"context"
	"crypto/hmac"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"
)

// The operator's chat surface, and the bridge a local agent process talks to.
//
// The portal holds no provider configuration and no model keys: omp owns authentication, and the
// agent runs as its own process which reaches this bridge with a bearer token. What the portal
// owns is the part that must not be delegated - who may run what, who approved it, and what the
// result actually was, read back rather than described.

const agentBridgeHeader = "Authorization"

// readAgentBridgeToken loads the shared secret for the bridge. Absent means the bridge is off:
// a deployment that has not opted in cannot be driven by an agent at all.
func readAgentBridgeToken() ([]byte, error) {
	path := strings.TrimSpace(os.Getenv("PORTAL_AGENT_BRIDGE_TOKEN_FILE"))
	if path == "" {
		return nil, nil
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	token := strings.TrimSpace(string(raw))
	if len(token) < 32 {
		return nil, errors.New("agent bridge token must be at least 32 characters")
	}
	return []byte(token), nil
}

func (s *Server) bridgeAuthorised(r *http.Request) bool {
	if len(s.agentBridgeToken) == 0 {
		return false
	}
	presented := strings.TrimSpace(strings.TrimPrefix(r.Header.Get(agentBridgeHeader), "Bearer "))
	if presented == "" {
		return false
	}
	return hmac.Equal([]byte(presented), s.agentBridgeToken)
}

// bridge wraps the endpoints the agent process calls. A disabled bridge answers 503 rather than
// 401, because "not configured" and "wrong token" are different operator problems.
func (s *Server) bridge(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if len(s.agentBridgeToken) == 0 {
			writeJSON(w, http.StatusServiceUnavailable, map[string]any{
				"error": "agent bridge disabled; set PORTAL_AGENT_BRIDGE_TOKEN_FILE to enable it",
			})
			return
		}
		if !s.bridgeAuthorised(r) {
			writeJSON(w, http.StatusUnauthorized, map[string]any{"error": "bridge token required"})
			return
		}
		r.Body = http.MaxBytesReader(w, r.Body, 1<<20)
		next(w, r)
	}
}

func writeJSON(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(body)
}

// ---------------------------------------------------------------------------------------------
// Operator surface
// ---------------------------------------------------------------------------------------------

type agentChatRow struct {
	VerbCall
	Approvable bool
	Summary    string
	// Arguments is every value the call carries, so an operator approves what they can see rather
	// than a one-line summary of it.
	Arguments []agentArgument
	// LongEvidence collapses output that would otherwise bury the page. The threshold is lines,
	// not bytes: one 4 KB line is readable, forty short ones are a wall.
	LongEvidence bool
	// Live and PublishedToday sit beside a publish approval: what that world already serves, and
	// how much has gone out in the last day.
	Live           []WorldReleaseContext
	PublishedToday int
}

type agentArgument struct {
	Name  string
	Value string
}

func verbArguments(call VerbCall) []agentArgument {
	candidates := []agentArgument{
		{"world", call.World}, {"profile", call.Profile}, {"identifier", call.Identifier},
		{"version", call.Version}, {"query", call.Query}, {"client type", call.ClientType},
		{"published profile", call.PublishedProfile}, {"release", call.ReleaseRef},
		{"archive", call.Archive}, {"scope", call.Scope}, {"reason", call.Reason}, {"note", call.Notes},
	}
	if call.Lines > 0 {
		candidates = append(candidates, agentArgument{"lines", strconv.Itoa(call.Lines)})
	}
	arguments := make([]agentArgument, 0, len(candidates))
	for _, candidate := range candidates {
		if strings.TrimSpace(candidate.Value) != "" {
			arguments = append(arguments, candidate)
		}
	}
	return arguments
}

func (s *Server) agentChat(w http.ResponseWriter, r *http.Request) {
	messages, err := s.store.AgentMessages(r.Context(), 100)
	if err != nil {
		http.Error(w, "conversation unavailable", http.StatusInternalServerError)
		return
	}
	calls, err := s.store.VerbCalls(r.Context(), 25)
	if err != nil {
		http.Error(w, "verb history unavailable", http.StatusInternalServerError)
		return
	}
	rows := make([]agentChatRow, 0, len(calls))
	pending := 0
	for _, call := range calls {
		row := agentChatRow{
			VerbCall:     call,
			Approvable:   call.Status == VerbPending,
			Summary:      verbSummary(call),
			Arguments:    verbArguments(call),
			LongEvidence: strings.Count(call.Evidence, "\n") > 12,
		}
		if row.Approvable {
			pending++
		}
		// A publish is the one approval that changes what players download, so it is shown against
		// what that world already serves.
		if row.Approvable && call.Verb == "publish_profile" {
			if live, today, err := s.store.WorldReleaseContext(r.Context(), call.World); err == nil {
				row.Live, row.PublishedToday = live, today
			}
		}
		rows = append(rows, row)
	}
	latest, _, err := s.store.AgentActivity(r.Context())
	if err != nil {
		http.Error(w, "conversation unavailable", http.StatusInternalServerError)
		return
	}
	waiting, since, _ := s.store.AgentAwaitedTurn(r.Context())
	render(w, agentChatTemplate, map[string]any{
		"Messages": messages, "Calls": rows, "CSRF": s.csrfCookie(w, r),
		"IsAdmin": true, "SourceURL": s.cfg.SourceURL,
		"BridgeEnabled": len(s.agentBridgeToken) > 0,
		"Pending":       pending,
		"State":         fmt.Sprintf("%d/%d/%t", latest, pending, waiting),
		// Busy drives the fast poll: either the operator owes a decision, or the agent owes a turn.
		"Busy":         pending > 0 || waiting,
		"Waiting":      waiting,
		"WaitedSecond": int(since.Seconds()),
		// Long enough that an ordinary model turn never trips it, short enough that a runner which
		// is not running is named while the operator is still looking at the page.
		"WaitStalled": waiting && since > 90*time.Second,
		"Shown":       len(messages),
	})
}

func verbSummary(call VerbCall) string {
	parts := []string{call.Verb}
	if call.World != "" {
		parts = append(parts, "world="+call.World)
	}
	if call.Identifier != "" {
		parts = append(parts, call.Identifier)
	}
	if call.Version != "" {
		parts = append(parts, call.Version)
	}
	if call.ClientType != "" {
		parts = append(parts, call.ClientType)
	}
	if call.PublishedProfile != "" {
		parts = append(parts, call.PublishedProfile)
	}
	if call.ReleaseRef != "" {
		parts = append(parts, call.ReleaseRef)
	}
	if call.Notes != "" {
		parts = append(parts, "note="+call.Notes)
	}
	return strings.Join(parts, " ")
}

func (s *Server) agentChatMessage(w http.ResponseWriter, r *http.Request) {
	body := strings.TrimSpace(r.FormValue("body"))
	if body == "" {
		http.Error(w, "empty message", http.StatusBadRequest)
		return
	}
	if _, err := s.store.AppendAgentMessage(r.Context(), "operator", body); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	s.wakeRunner("operator message")
	http.Redirect(w, r, "/admin/agent", http.StatusSeeOther)
}

// wakeRunner writes the file a systemd path unit watches, which starts one runner pass.
//
// The portal cannot start a unit itself: it holds no Docker socket and no host access, which is
// the whole point of the split. So the signal is a file in its own data directory, and systemd
// owns the reaction - `PathModified` on the host side of that volume, triggering
// valheim-agent-runner-once.service. Nothing here waits for the runner, and a deployment with no
// wake file configured, or with the poller running instead, is unaffected: an absent path means
// the operator triggers passes by hand, exactly as before.
//
// It writes rather than touching timestamps because `PathModified` is an inotify write watch: a
// timestamp change alone is not reliably a trigger, and a wake that silently does nothing would
// be indistinguishable from an agent that has nothing to say.
//
// Failures are deliberately quiet in the response and loud in the log. The message is already
// stored; refusing the request because a convenience trigger failed would lose the operator's
// text over something they can retry with one command.
func (s *Server) wakeRunner(reason string) {
	path := strings.TrimSpace(s.cfg.AgentWakeFile)
	if path == "" {
		return
	}
	payload := fmt.Sprintf("%s %s\n", time.Now().UTC().Format(time.RFC3339Nano), reason)
	if err := os.WriteFile(path, []byte(payload), 0o640); err != nil {
		log.Printf("WARN could not wake the agent runner (%s): %v", reason, err)
	}
}

// agentChatDecide is the confirmation gate. Approval runs the verb immediately and records the
// evidence beside the approver; denial records that nothing ran.
func (s *Server) agentChatDecide(w http.ResponseWriter, r *http.Request) {
	id, decision := strings.TrimSpace(r.FormValue("id")), r.FormValue("decision")
	actor := r.Header.Get("X-Portal-Actor")
	if actor == "" {
		if steamID, ok := s.operatorSteamID(r); ok {
			actor = steamID
		}
	}
	call, err := s.store.VerbCall(r.Context(), id)
	if err != nil {
		http.Error(w, "no such verb call", http.StatusNotFound)
		return
	}
	if call.Status != VerbPending {
		http.Error(w, "verb call is not awaiting a decision", http.StatusConflict)
		return
	}
	switch decision {
	case "deny":
		if err := s.store.FinishVerbCall(r.Context(), id, VerbDenied, actor, "", "operator denied the request"); err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		_ = s.store.Audit(r.Context(), actor, "agent.verb.denied", call.Verb, verbSummary(call))
		_, _ = s.store.AppendAgentMessage(r.Context(), "system", "Denied: "+verbSummary(call))
	case "approve":
		s.executeApproved(r.Context(), call, actor)
	default:
		http.Error(w, "decision must be approve or deny", http.StatusBadRequest)
		return
	}
	// A decision is the other thing the agent is waiting on: approving a verb should let the work
	// continue without the operator also triggering a pass by hand.
	s.wakeRunner("operator decision")
	http.Redirect(w, r, "/admin/agent", http.StatusSeeOther)
}

// executeApproved runs a verb an operator just confirmed and records what came back. The
// conversation gets the outcome as a system turn, so the agent reads the result from the record
// rather than assuming its request succeeded.
func (s *Server) executeApproved(ctx context.Context, call VerbCall, actor string) {
	reply, err := s.runVerb(ctx, call)
	status, evidence, detail := VerbSucceeded, replyEvidence(reply), ""
	if err != nil {
		status, evidence, detail = VerbFailed, "", err.Error()
	} else if reply.Status != "succeeded" {
		status, detail = VerbFailed, "agent reported status "+reply.Status
	}
	if err := s.store.FinishVerbCall(ctx, call.ID, status, actor, evidence, detail); err != nil {
		return
	}
	_ = s.store.Audit(ctx, actor, "agent.verb."+status, call.Verb, verbSummary(call))
	note := fmt.Sprintf("%s: %s", status, verbSummary(call))
	if detail != "" {
		note += "\n" + detail
	}
	if evidence != "" {
		note += "\n" + evidence
	}
	_, _ = s.store.AppendAgentMessage(ctx, "system", note)
}

// agentChatStatus is the smallest thing that answers "has anything changed": the newest message id
// and how many calls await a decision. The page reloads itself only when this differs from what it
// was rendered with, and never while the operator is typing.
func (s *Server) agentChatStatus(w http.ResponseWriter, r *http.Request) {
	latest, pending, err := s.store.AgentActivity(r.Context())
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": "unavailable"})
		return
	}
	waiting, since, _ := s.store.AgentAwaitedTurn(r.Context())
	// waiting joins the state token so the page notices the flip in both directions: it starts
	// showing that the agent is working, and stops when the answer lands.
	writeJSON(w, http.StatusOK, map[string]any{
		"state": fmt.Sprintf("%d/%d/%t", latest, pending, waiting), "latest": latest, "pending": pending,
		"waiting": waiting, "waited_seconds": int(since.Seconds()),
	})
}

// agentChatTail is what the dock on the admin home reads: the last few turns plus the same waiting
// state the full page shows. It exists because the bridge endpoints need the bridge token, which a
// browser must never hold - the dock is an operator surface and authenticates as one.
func (s *Server) agentChatTail(w http.ResponseWriter, r *http.Request) {
	const dockTurns = 8
	messages, err := s.store.AgentMessages(r.Context(), dockTurns)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": "conversation unavailable"})
		return
	}
	latest, pending, err := s.store.AgentActivity(r.Context())
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": "unavailable"})
		return
	}
	waiting, since, _ := s.store.AgentAwaitedTurn(r.Context())
	turns := make([]map[string]any, 0, len(messages))
	for _, message := range messages {
		turns = append(turns, map[string]any{"role": message.Role, "body": message.Body})
	}
	// Every argument of every waiting call, not a summary of it. The dock may approve, so the dock
	// must show what approval means - the same values the full page lists, from the same builder.
	calls, err := s.store.PendingVerbCalls(r.Context())
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": "requests unavailable"})
		return
	}
	awaiting := make([]map[string]any, 0, len(calls))
	for _, call := range calls {
		if call.Status != VerbPending {
			continue
		}
		arguments := make([]map[string]string, 0, 8)
		for _, argument := range verbArguments(call) {
			arguments = append(arguments, map[string]string{"name": argument.Name, "value": argument.Value})
		}
		entry := map[string]any{
			"id": call.ID, "verb": call.Verb, "class": call.Class, "arguments": arguments,
		}
		// A publish approval is judged against what that world already serves and how much has gone
		// out today; without those two numbers the decision is uninformed wherever it is made.
		if call.Verb == "publish_profile" {
			if live, today, contextErr := s.store.WorldReleaseContext(r.Context(), call.World); contextErr == nil {
				serving := make([]string, 0, len(live))
				for _, release := range live {
					serving = append(serving, release.Profile+" "+release.Version)
				}
				entry["live"] = serving
				entry["published_today"] = today
			}
		}
		awaiting = append(awaiting, entry)
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"state": fmt.Sprintf("%d/%d/%t", latest, pending, waiting), "latest": latest, "pending": pending,
		"waiting": waiting, "waited_seconds": int(since.Seconds()),
		"bridge_enabled": len(s.agentBridgeToken) > 0,
		"turns":          turns,
		"awaiting":       awaiting,
	})
}

// ---------------------------------------------------------------------------------------------
// Bridge surface
// ---------------------------------------------------------------------------------------------

func (s *Server) agentInbox(w http.ResponseWriter, r *http.Request) {
	since, _ := strconv.ParseInt(r.URL.Query().Get("since"), 10, 64)
	messages, err := s.store.AgentMessagesSince(r.Context(), since, 50)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": "conversation unavailable"})
		return
	}
	cursor := since
	out := make([]map[string]any, 0, len(messages))
	for _, message := range messages {
		cursor = message.ID
		out = append(out, map[string]any{
			"id": message.ID, "role": message.Role, "body": message.Body,
			"created_at": message.CreatedAt.Format("2006-01-02T15:04:05Z07:00"),
		})
	}
	pending, err := s.store.PendingVerbCalls(r.Context())
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": "verb history unavailable"})
		return
	}
	waiting := make([]map[string]any, 0, len(pending))
	for _, call := range pending {
		waiting = append(waiting, map[string]any{"id": call.ID, "verb": call.Verb, "summary": verbSummary(call)})
	}
	writeJSON(w, http.StatusOK, map[string]any{"messages": out, "cursor": cursor, "awaiting_approval": waiting})
}

// agentVerbs is how a runner learns its own vocabulary instead of carrying a copy that can drift
// from the portal's. Read-only, and it reports the refusals too: a runner that cannot see why a
// verb is unavailable will keep asking for it.
func (s *Server) agentVerbs(w http.ResponseWriter, r *http.Request) {
	out := make([]map[string]any, 0, len(VerbIDs()))
	for _, id := range VerbIDs() {
		verb, err := VerbByID(id)
		if err != nil {
			continue
		}
		entry := map[string]any{
			"verb": verb.ID, "class": string(verb.Class),
			"needs_approval": verb.NeedsApproval(), "available": verb.Class != ClassForbidden && verb.Unwired == "",
		}
		// Advertised from the same declaration the portal checks, so what a caller is told and what
		// it is held to cannot drift.
		entry["needs"] = argumentNames(verb)
		if len(verb.Accepts) > 0 {
			entry["accepts"] = verb.Accepts
		}
		if verb.Unwired != "" {
			entry["unavailable_because"] = verb.Unwired
		}
		if verb.Class == ClassForbidden {
			entry["unavailable_because"] = "forbidden by policy"
		}
		out = append(out, entry)
	}
	writeJSON(w, http.StatusOK, map[string]any{"verbs": out})
}

func (s *Server) agentSay(w http.ResponseWriter, r *http.Request) {
	var request struct{ Body string }
	if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid json"})
		return
	}
	id, err := s.store.AppendAgentMessage(r.Context(), "agent", request.Body)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusCreated, map[string]any{"id": id})
}

type verbRequest struct {
	Verb             string `json:"verb"`
	World            string `json:"world"`
	Profile          string `json:"profile"`
	Identifier       string `json:"identifier"`
	Version          string `json:"version"`
	Query            string `json:"query"`
	Reason           string `json:"reason"`
	ClientType       string `json:"client_type"`
	PublishedProfile string `json:"published_profile"`
	ReleaseRef       string `json:"release_id"`
	Archive          string `json:"archive"`
	Notes            string `json:"notes"`
	Lines            int    `json:"lines"`
	Scope            string `json:"scope"`
}

// verbCreatesWorld marks the two operations whose world does not exist yet, so a name that matches
// nothing is correct rather than a mistake.
func verbCreatesWorld(verb Verb) bool {
	return verb.Operation == "provision" || verb.Operation == "world_create"
}

// canonicalWorld returns the registered spelling of a world, the names this portal serves, and
// whether the given name matched one of them case-insensitively.
func (s *Server) canonicalWorld(ctx context.Context, name string) (string, []string, bool) {
	worlds, err := s.store.PublicWorlds(ctx)
	if err != nil {
		// Unknowable rather than unmatched: forward what was asked for and let the agent answer.
		return name, nil, true
	}
	known := make([]string, 0, len(worlds))
	for _, world := range worlds {
		known = append(known, world.Name)
		if strings.EqualFold(world.Name, name) {
			return world.Name, known, true
		}
	}
	if len(known) == 0 {
		// Nothing registered yet: the agent's own allowlist is the only authority, so do not invent
		// a refusal the deployment has not asked for.
		return name, nil, true
	}
	return name, known, false
}

// replyEvidence is what the host actually read back, whichever field it arrived in.
//
// Operations that return structured data - mod_search, mod_inventory, mod_custom_list,
// profile_catalog, world_metadata, world_catalog, world_analysis - answer in Data as raw JSON, and
// the portal read only Output. Every one of them therefore "succeeded" with empty evidence: a search
// that found packages reported nothing, and the agent could only say it ran. The data was present at
// every step and nobody carried it to the caller.
func replyEvidence(reply AgentReply) string {
	if strings.TrimSpace(reply.Output) != "" {
		return reply.Output
	}
	if len(reply.Data) > 0 {
		return string(reply.Data)
	}
	return ""
}

// agentVerb is the whole security surface in one handler: an agent names a verb, and what
// happens next is decided by the verb's class, not by the agent's argument for it.
func (s *Server) agentVerb(w http.ResponseWriter, r *http.Request) {
	var request verbRequest
	if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid json"})
		return
	}
	verb, err := VerbByID(request.Verb)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{
			"error": err.Error(), "known_verbs": VerbIDs(),
		})
		return
	}
	call := VerbCall{
		ID: randomID(), Verb: verb.ID, Class: string(verb.Class), World: strings.TrimSpace(request.World),
		Profile: strings.TrimSpace(request.Profile), Identifier: strings.TrimSpace(request.Identifier),
		Version: strings.TrimSpace(request.Version), Query: strings.TrimSpace(request.Query),
		Reason: strings.TrimSpace(request.Reason), ClientType: strings.TrimSpace(request.ClientType),
		PublishedProfile: strings.TrimSpace(request.PublishedProfile),
		ReleaseRef:       strings.TrimSpace(request.ReleaseRef),
		Archive:          strings.TrimSpace(request.Archive),
		Notes:            strings.TrimSpace(request.Notes),
		Lines:            request.Lines,
		Scope:            strings.TrimSpace(request.Scope),
		RequestedBy:      "agent",
	}

	// The agent's allowed-worlds check is an exact string match, so "hrafnheim" is refused as
	// "forbidden" while "Hrafnheim" runs - and "forbidden" tells the caller nothing it can act on.
	// The portal knows which worlds are registered, so it fixes the spelling here rather than
	// forwarding a name that can only fail. When nothing matches it says which worlds exist, which
	// is the difference between a caller that can correct itself and one that retries forever.
	if call.World != "" && !verbCreatesWorld(verb) {
		canonical, known, found := s.canonicalWorld(r.Context(), call.World)
		if !found {
			writeJSON(w, http.StatusBadRequest, map[string]any{
				"status": VerbFailed, "verb": verb.ID,
				"error": fmt.Sprintf("no world named %q; this portal serves %s", call.World, strings.Join(known, ", ")),
			})
			return
		}
		call.World = canonical
	}

	// The host agent refuses any mod operation that arrives without the arguments it uses, or with
	// arguments it does not - and the refusal it can give is "invalid mod search", which names
	// nothing. The contract is declared on the verb, so it is checked and reported here instead.
	if missing := missingArguments(verb, call); len(missing) > 0 {
		writeJSON(w, http.StatusBadRequest, map[string]any{
			"status": VerbFailed, "verb": verb.ID,
			"error": fmt.Sprintf("%s needs %s", verb.ID, strings.Join(missing, ", ")),
			"needs": argumentNames(verb),
		})
		return
	}
	// Arguments the verb does not declare are dropped rather than forwarded. A model that explains
	// itself by adding a reason to a search should not have the search refused for it.
	call = onlyDeclaredArguments(verb, call)

	if verb.Class == ClassForbidden {
		call.Status = VerbRefused
		if err := s.store.CreateVerbCall(r.Context(), call); err == nil {
			_ = s.store.FinishVerbCall(r.Context(), call.ID, VerbRefused, "", "", "forbidden by policy")
			_ = s.store.Audit(r.Context(), "agent", "agent.verb.refused", verb.ID, "forbidden by policy")
		}
		writeJSON(w, http.StatusForbidden, map[string]any{
			"status": VerbRefused, "verb": verb.ID,
			"error": "forbidden by policy; this is not negotiable and no argument changes it",
		})
		return
	}
	if verb.Unwired != "" {
		call.Status = VerbRefused
		if err := s.store.CreateVerbCall(r.Context(), call); err == nil {
			_ = s.store.FinishVerbCall(r.Context(), call.ID, VerbRefused, "", "", "unwired: "+verb.Unwired)
		}
		writeJSON(w, http.StatusNotImplemented, map[string]any{
			"status": VerbRefused, "verb": verb.ID, "error": "not available through the portal: " + verb.Unwired,
		})
		return
	}
	if verb.NeedsWorld && !validWorld(call.World) {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "verb " + verb.ID + " needs a valid world"})
		return
	}
	if verb.NeedsIdentifier && call.Identifier == "" {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "verb " + verb.ID + " needs an identifier"})
		return
	}
	if verb.NeedsClientType && call.ClientType != "vr" && call.ClientType != "flat" {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "verb " + verb.ID + " needs client_type vr or flat"})
		return
	}
	if verb.NeedsNotes && len(call.Notes) < 8 {
		writeJSON(w, http.StatusBadRequest, map[string]any{
			"error": "verb " + verb.ID + " needs notes of at least 8 characters; the note becomes the release note",
		})
		return
	}
	if verb.NeedsRelease && (call.PublishedProfile == "" || call.ReleaseRef == "" || call.Archive == "") {
		writeJSON(w, http.StatusBadRequest, map[string]any{
			"error": "verb " + verb.ID + " needs published_profile, release_id and archive",
		})
		return
	}

	if verb.NeedsApproval() {
		call.Status = VerbPending
		if err := s.store.CreateVerbCall(r.Context(), call); err != nil {
			writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
			return
		}
		_, _ = s.store.AppendAgentMessage(r.Context(), "system", "Awaiting approval: "+verbSummary(call))
		writeJSON(w, http.StatusAccepted, map[string]any{
			"status": VerbPending, "id": call.ID, "verb": verb.ID,
			"note": "an operator must confirm this on every invocation",
		})
		return
	}

	call.Status = VerbPending // a row exists before execution; the outcome overwrites it
	if err := s.store.CreateVerbCall(r.Context(), call); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	reply, runErr := s.runVerb(r.Context(), call)
	if runErr != nil {
		_ = s.store.FinishVerbCall(r.Context(), call.ID, VerbFailed, "", "", runErr.Error())
		writeJSON(w, http.StatusBadGateway, map[string]any{"status": VerbFailed, "id": call.ID, "error": runErr.Error()})
		return
	}
	status := VerbSucceeded
	detail := ""
	if reply.Status != "succeeded" {
		status, detail = VerbFailed, "agent reported status "+reply.Status
	}
	_ = s.store.FinishVerbCall(r.Context(), call.ID, status, "", replyEvidence(reply), detail)
	_ = s.store.Audit(r.Context(), "agent", "agent.verb."+status, verb.ID, verbSummary(call))
	writeJSON(w, http.StatusOK, map[string]any{
		"status": status, "id": call.ID, "verb": verb.ID, "evidence": replyEvidence(reply), "detail": detail,
	})
}

const agentChatTemplate = `<!doctype html><html><head><meta charset="utf-8"><title>Agent - Neuralyze Valheim</title></head>
<body class="admin">
<header class="admin-nav">` + adminNavigation + `</header>
<main class="shell" data-agent-status="{{.State}}" data-agent-busy="{{if .Busy}}true{{else}}false{{end}}" data-agent-waiting="{{if .Waiting}}true{{else}}false{{end}}">
<h1>Agent</h1>
<p class="install-note"><span data-agent-indicator>{{if .Pending}}{{.Pending}} request(s) awaiting your decision{{else}}nothing pending{{end}}</span></p>
{{if not .BridgeEnabled}}<p class="notes warning">The agent bridge is disabled. Set <code>PORTAL_ENABLE_AGENT_BRIDGE=true</code> in <code>deploy/install.conf</code> and reinstall; the installer generates the token and mounts it.</p>{{end}}

{{if .Calls}}<h2>Requests</h2>
{{range .Calls}}<section class="admin-widget{{if .Approvable}} warning{{end}}">
<h3>{{.Verb}} <small>{{.Class}} - {{.Status}}{{if .DecidedBy}}, decided by {{.DecidedBy}}{{end}}</small></h3>

{{if .Arguments}}<table class="facts">
{{range .Arguments}}<tr><th>{{.Name}}</th><td><code>{{.Value}}</code></td></tr>{{end}}
</table>{{end}}

{{if .Approvable}}{{if eq .Verb "publish_profile"}}
<div class="notes warning">
<p>This publishes what players download.{{if .PublishedToday}} <strong>{{.PublishedToday}} release(s) for this world in the last day.</strong>{{end}}</p>
{{if .Live}}<p>Currently live:</p><ul>{{range .Live}}<li><code>{{.Profile}}</code> {{.Version}}</li>{{end}}</ul>{{end}}
</div>
{{end}}{{end}}

{{if .Detail}}<p class="notes warning">{{.Detail}}</p>{{end}}
{{if .Evidence}}{{if .LongEvidence}}<details><summary>Evidence read back by the portal</summary><pre class="notes">{{.Evidence}}</pre></details>
{{else}}<pre class="notes">{{.Evidence}}</pre>{{end}}{{end}}

{{if .Approvable}}<div class="server-card-controls">
<form method="post" action="/admin/agent/decide" class="server-card-control"><input type="hidden" name="csrf" value="{{$.CSRF}}"><input type="hidden" name="id" value="{{.ID}}"><input type="hidden" name="decision" value="approve"><button type="submit">Approve</button></form>
<form method="post" action="/admin/agent/decide" class="server-card-control"><input type="hidden" name="csrf" value="{{$.CSRF}}"><input type="hidden" name="id" value="{{.ID}}"><input type="hidden" name="decision" value="deny"><button type="submit" class="danger">Deny</button></form>
</div>{{end}}
</section>{{end}}{{end}}

<h2>Conversation</h2>
{{if .Messages}}<p class="install-note">Showing the last {{.Shown}} turn(s), oldest first.</p>{{end}}
{{range .Messages}}<article class="agent-turn agent-turn-{{.Role}}"><h3>{{.Role}}</h3><pre class="notes">{{.Body}}</pre></article>{{end}}
{{if .Waiting}}<p class="notes{{if .WaitStalled}} warning{{end}}" data-agent-working="{{.WaitedSecond}}">
{{if .WaitStalled}}No reply after <span data-agent-elapsed>{{.WaitedSecond}}</span>s. The runner may not be running: check <code>systemctl status valheim-agent-runner-wake.path</code>, or run one pass with <code>sudo systemctl start valheim-agent-runner-once</code>.
{{else}}<span class="spinner" aria-hidden="true"></span> The agent is working - <span data-agent-elapsed>{{.WaitedSecond}}</span>s. This page updates itself; there is nothing to reload.{{end}}
</p>{{end}}

<form method="post" action="/admin/agent/message">
<input type="hidden" name="csrf" value="{{.CSRF}}">
<label class="agent-compose">Message
<textarea name="body" rows="4" required placeholder="Ask for something, or answer a question the agent asked."></textarea>
</label>
<button type="submit">Send</button>
<span class="install-note">Ctrl+Enter sends.</span>
</form>
</main>
<script src="/assets/admin-agent.js"></script>
</body></html>`
