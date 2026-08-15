package app

import (
	"context"
	"crypto/hmac"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"strconv"
	"strings"
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
	for _, call := range calls {
		rows = append(rows, agentChatRow{VerbCall: call, Approvable: call.Status == VerbPending, Summary: verbSummary(call)})
	}
	render(w, agentChatTemplate, map[string]any{
		"Messages": messages, "Calls": rows, "CSRF": s.csrfCookie(w, r),
		"IsAdmin": true, "SourceURL": s.cfg.SourceURL,
		"BridgeEnabled": len(s.agentBridgeToken) > 0,
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
	http.Redirect(w, r, "/admin/agent", http.StatusSeeOther)
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
	http.Redirect(w, r, "/admin/agent", http.StatusSeeOther)
}

// executeApproved runs a verb an operator just confirmed and records what came back. The
// conversation gets the outcome as a system turn, so the agent reads the result from the record
// rather than assuming its request succeeded.
func (s *Server) executeApproved(ctx context.Context, call VerbCall, actor string) {
	reply, err := s.runVerb(ctx, call)
	status, evidence, detail := VerbSucceeded, reply.Output, ""
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
		RequestedBy:      "agent",
	}

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
	_ = s.store.FinishVerbCall(r.Context(), call.ID, status, "", reply.Output, detail)
	_ = s.store.Audit(r.Context(), "agent", "agent.verb."+status, verb.ID, verbSummary(call))
	writeJSON(w, http.StatusOK, map[string]any{
		"status": status, "id": call.ID, "verb": verb.ID, "evidence": reply.Output, "detail": detail,
	})
}

const agentChatTemplate = `<!doctype html><html><head><meta charset="utf-8"><title>Agent - Neuralyze Valheim</title></head>
<body class="portal-body">
<header class="portal-header"><a class="portal-nav-button" href="/admin">Administration</a>` + adminNavigation + `</header>
<main class="portal-main">
<h1>Agent</h1>
{{if not .BridgeEnabled}}<p class="portal-warning">The agent bridge is disabled. Set <code>PORTAL_AGENT_BRIDGE_TOKEN_FILE</code> to let an agent process connect.</p>{{end}}

{{if .Calls}}<h2>Requests</h2>
<table class="portal-table"><tr><th>Verb</th><th>Class</th><th>Status</th><th>Decided by</th><th>Evidence</th><th></th></tr>
{{range .Calls}}<tr>
<td>{{.Summary}}</td><td>{{.Class}}</td><td>{{.Status}}</td><td>{{.DecidedBy}}</td>
<td>{{if .Detail}}<div class="portal-warning">{{.Detail}}</div>{{end}}{{if .Evidence}}<pre class="portal-pre">{{.Evidence}}</pre>{{end}}</td>
<td>{{if .Approvable}}
<form method="post" action="/admin/agent/decide" class="portal-inline"><input type="hidden" name="csrf" value="{{$.CSRF}}"><input type="hidden" name="id" value="{{.ID}}"><input type="hidden" name="decision" value="approve"><button>Approve</button></form>
<form method="post" action="/admin/agent/decide" class="portal-inline"><input type="hidden" name="csrf" value="{{$.CSRF}}"><input type="hidden" name="id" value="{{.ID}}"><input type="hidden" name="decision" value="deny"><button class="danger">Deny</button></form>
{{end}}</td></tr>{{end}}</table>{{end}}

<h2>Conversation</h2>
{{range .Messages}}<article class="portal-message portal-message-{{.Role}}"><h3>{{.Role}}</h3><pre class="portal-pre">{{.Body}}</pre></article>{{end}}

<form method="post" action="/admin/agent/message">
<input type="hidden" name="csrf" value="{{.CSRF}}">
<label>Message <textarea name="body" rows="4" required></textarea></label>
<button>Send</button>
</form>
</main></body></html>`
