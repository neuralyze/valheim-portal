package app

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
	"time"
)

// Storage for the operator/agent conversation and for every verb an agent asked to run.
//
// The verb-call row is the record of what was requested, who approved it, and what came back -
// written before execution and updated after. A session where the only trace of an action was
// the agent's own account of it cost an operator four test runs against a build that could not
// contain the fix it was said to contain.

// AgentMessage is one turn in the conversation.
type AgentMessage struct {
	ID        int64
	Role      string // operator | agent | system
	Body      string
	CreatedAt time.Time
}

// VerbCall records one request from the agent, its approval, and its outcome.
type VerbCall struct {
	ID          string
	Verb        string
	Class       string
	World       string
	Profile     string
	Identifier  string
	Version     string
	Query       string
	Reason      string
	Status      string
	RequestedBy string
	DecidedBy   string
	Evidence    string
	Detail      string
	CreatedAt   time.Time
	FinishedAt  *time.Time
}

const (
	VerbPending   = "pending_approval"
	VerbDenied    = "denied"
	VerbRefused   = "refused"
	VerbSucceeded = "succeeded"
	VerbFailed    = "failed"
)

const agentConversation = "operator"

func validAgentRole(role string) bool {
	return role == "operator" || role == "agent" || role == "system"
}

// AppendAgentMessage stores one turn and returns its id, which doubles as the inbox cursor.
func (s *Store) AppendAgentMessage(ctx context.Context, role, body string) (int64, error) {
	body = strings.TrimSpace(body)
	if !validAgentRole(role) {
		return 0, fmt.Errorf("invalid role %q", role)
	}
	if body == "" {
		return 0, errors.New("empty message")
	}
	if len(body) > 20000 {
		return 0, errors.New("message too long")
	}
	result, err := s.db.ExecContext(ctx,
		`INSERT INTO agent_messages(conversation, role, body, created_at) VALUES(?,?,?,?)`,
		agentConversation, role, body, time.Now().UTC().Format(time.RFC3339Nano))
	if err != nil {
		return 0, err
	}
	return result.LastInsertId()
}

// AgentMessagesSince returns turns newer than a cursor, oldest first, so the agent bridge can
// poll without replaying the whole conversation.
func (s *Store) AgentMessagesSince(ctx context.Context, since int64, limit int) ([]AgentMessage, error) {
	if limit <= 0 || limit > 200 {
		limit = 50
	}
	rows, err := s.db.QueryContext(ctx,
		`SELECT id, role, body, created_at FROM agent_messages WHERE conversation=? AND id>? ORDER BY id LIMIT ?`,
		agentConversation, since, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []AgentMessage
	for rows.Next() {
		var message AgentMessage
		var created string
		if err := rows.Scan(&message.ID, &message.Role, &message.Body, &created); err != nil {
			return nil, err
		}
		message.CreatedAt, _ = time.Parse(time.RFC3339Nano, created)
		out = append(out, message)
	}
	return out, rows.Err()
}

// AgentMessages returns the newest turns for the page, oldest first.
func (s *Store) AgentMessages(ctx context.Context, limit int) ([]AgentMessage, error) {
	if limit <= 0 || limit > 500 {
		limit = 100
	}
	rows, err := s.db.QueryContext(ctx,
		`SELECT id, role, body, created_at FROM (
                   SELECT id, role, body, created_at FROM agent_messages WHERE conversation=? ORDER BY id DESC LIMIT ?
                 ) ORDER BY id`, agentConversation, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []AgentMessage
	for rows.Next() {
		var message AgentMessage
		var created string
		if err := rows.Scan(&message.ID, &message.Role, &message.Body, &created); err != nil {
			return nil, err
		}
		message.CreatedAt, _ = time.Parse(time.RFC3339Nano, created)
		out = append(out, message)
	}
	return out, rows.Err()
}

// CreateVerbCall writes the request before anything runs, so a crash mid-flight still leaves
// evidence that it was asked for.
func (s *Store) CreateVerbCall(ctx context.Context, call VerbCall) error {
	if strings.TrimSpace(call.ID) == "" || strings.TrimSpace(call.Verb) == "" {
		return errors.New("verb call needs an id and a verb")
	}
	_, err := s.db.ExecContext(ctx, `
INSERT INTO agent_verb_calls(id, conversation, verb, class, world, profile, identifier, version, query, reason,
                             status, requested_by, decided_by, evidence, detail, created_at)
VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'','','',?)`,
		call.ID, agentConversation, call.Verb, call.Class, call.World, call.Profile, call.Identifier,
		call.Version, call.Query, call.Reason, call.Status, call.RequestedBy,
		time.Now().UTC().Format(time.RFC3339Nano))
	return err
}

// FinishVerbCall records the outcome. Evidence is what was read back, not what was claimed.
func (s *Store) FinishVerbCall(ctx context.Context, id, status, decidedBy, evidence, detail string) error {
	if len(evidence) > 20000 {
		evidence = evidence[:20000] + "\n[truncated]"
	}
	result, err := s.db.ExecContext(ctx, `
UPDATE agent_verb_calls SET status=?, decided_by=CASE WHEN ?='' THEN decided_by ELSE ? END,
       evidence=?, detail=?, finished_at=? WHERE id=?`,
		status, decidedBy, decidedBy, evidence, detail, time.Now().UTC().Format(time.RFC3339Nano), id)
	if err != nil {
		return err
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return err
	}
	if affected == 0 {
		return fmt.Errorf("no such verb call %q", id)
	}
	return nil
}

func (s *Store) VerbCall(ctx context.Context, id string) (VerbCall, error) {
	row := s.db.QueryRowContext(ctx, `
SELECT id, verb, class, world, profile, identifier, version, query, reason, status, requested_by,
       decided_by, evidence, detail, created_at, finished_at
FROM agent_verb_calls WHERE id=?`, id)
	return scanVerbCall(row)
}

type rowScanner interface {
	Scan(dest ...any) error
}

func scanVerbCall(row rowScanner) (VerbCall, error) {
	var call VerbCall
	var created string
	var finished sql.NullString
	if err := row.Scan(&call.ID, &call.Verb, &call.Class, &call.World, &call.Profile, &call.Identifier,
		&call.Version, &call.Query, &call.Reason, &call.Status, &call.RequestedBy, &call.DecidedBy,
		&call.Evidence, &call.Detail, &created, &finished); err != nil {
		return VerbCall{}, err
	}
	call.CreatedAt, _ = time.Parse(time.RFC3339Nano, created)
	if finished.Valid && finished.String != "" {
		if parsed, err := time.Parse(time.RFC3339Nano, finished.String); err == nil {
			call.FinishedAt = &parsed
		}
	}
	return call, nil
}

// VerbCalls returns the newest calls, newest first, for the operator page.
func (s *Store) VerbCalls(ctx context.Context, limit int) ([]VerbCall, error) {
	if limit <= 0 || limit > 200 {
		limit = 25
	}
	rows, err := s.db.QueryContext(ctx, `
SELECT id, verb, class, world, profile, identifier, version, query, reason, status, requested_by,
       decided_by, evidence, detail, created_at, finished_at
FROM agent_verb_calls WHERE conversation=? ORDER BY created_at DESC, rowid DESC LIMIT ?`,
		agentConversation, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []VerbCall
	for rows.Next() {
		call, err := scanVerbCall(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, call)
	}
	return out, rows.Err()
}

// PendingVerbCalls are the ones waiting on an operator, oldest first.
func (s *Store) PendingVerbCalls(ctx context.Context) ([]VerbCall, error) {
	calls, err := s.VerbCalls(ctx, 200)
	if err != nil {
		return nil, err
	}
	var pending []VerbCall
	for i := len(calls) - 1; i >= 0; i-- {
		if calls[i].Status == VerbPending {
			pending = append(pending, calls[i])
		}
	}
	return pending, nil
}
