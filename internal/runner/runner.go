// Package runner drives the portal's agent surface: it reads the operator conversation, asks a
// model what to do, and turns the answer into verb calls.
//
// Everything it may do is decided by the portal, not here. The runner cannot widen the lane: it
// has no shell, no credential for any remote, and the only actions available to it are the verbs
// the bridge reports. A mutating verb comes back as "awaiting approval" and stays there until an
// operator confirms it on /admin/agent, so the runner's job at that point is to wait and say so -
// not to retry, and not to find another way round.
package runner

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"sort"
	"strconv"
	"strings"
	"time"
)

// Decision is what the model is asked for: something to say, a verb to request, or both. Anything
// else is a parse failure, reported to the operator rather than guessed at.
type Decision struct {
	Say  string         `json:"say"`
	Verb string         `json:"verb"`
	Args map[string]any `json:"args"`
}

// Decider turns a prompt into a Decision. Production uses omp; tests use a stub, so the loop is
// testable without a model and without spending anything.
type Decider interface {
	Decide(ctx context.Context, prompt string) (Decision, error)
}

type Verb struct {
	ID            string   `json:"verb"`
	Class         string   `json:"class"`
	NeedsApproval bool     `json:"needs_approval"`
	Available     bool     `json:"available"`
	Needs         []string `json:"needs"`
	// Accepts are the optional arguments. Decoding them is not enough - they have to reach the
	// prompt, or the model states with confidence that a verb "takes no line-count parameter"
	// while the portal is advertising exactly that.
	Accepts []string `json:"accepts"`
	Because string   `json:"unavailable_because"`
}

type message struct {
	ID   int64  `json:"id"`
	Role string `json:"role"`
	Body string `json:"body"`
}

type inbox struct {
	Messages []message        `json:"messages"`
	Cursor   int64            `json:"cursor"`
	Awaiting []map[string]any `json:"awaiting_approval"`
}

type Config struct {
	BaseURL   string
	Token     string
	StatePath string
	Poll      time.Duration
	// MaxVerbsPerTurn stops a runaway chain. One operator message earns one action; anything
	// further needs the operator to speak again. Forty publishes in one evening is why.
	MaxVerbsPerTurn int
}

type Runner struct {
	cfg     Config
	client  *http.Client
	decider Decider
	cursor  int64
	vocab   []Verb
}

func New(cfg Config, decider Decider) *Runner {
	if cfg.Poll <= 0 {
		cfg.Poll = 3 * time.Second
	}
	if cfg.MaxVerbsPerTurn <= 0 {
		cfg.MaxVerbsPerTurn = 1
	}
	r := &Runner{cfg: cfg, client: &http.Client{Timeout: 20 * time.Minute}, decider: decider}
	r.loadCursor()
	return r
}

func (r *Runner) request(ctx context.Context, method, path string, body any) (*http.Response, error) {
	var reader io.Reader
	if body != nil {
		encoded, err := json.Marshal(body)
		if err != nil {
			return nil, err
		}
		reader = bytes.NewReader(encoded)
	}
	request, err := http.NewRequestWithContext(ctx, method, strings.TrimRight(r.cfg.BaseURL, "/")+path, reader)
	if err != nil {
		return nil, err
	}
	request.Header.Set("Authorization", "Bearer "+r.cfg.Token)
	if body != nil {
		request.Header.Set("Content-Type", "application/json")
	}
	return r.client.Do(request)
}

// Vocabulary is read from the portal rather than compiled in, so the runner cannot believe it has
// a verb the portal has since withdrawn.
func (r *Runner) Vocabulary(ctx context.Context) ([]Verb, error) {
	response, err := r.request(ctx, http.MethodGet, "/api/agent/verbs", nil)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("verbs: %s", response.Status)
	}
	var payload struct{ Verbs []Verb }
	if err := json.NewDecoder(response.Body).Decode(&payload); err != nil {
		return nil, err
	}
	r.vocab = payload.Verbs
	return payload.Verbs, nil
}

func (r *Runner) Say(ctx context.Context, body string) error {
	response, err := r.request(ctx, http.MethodPost, "/api/agent/message", map[string]string{"body": body})
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusCreated {
		return fmt.Errorf("message: %s", response.Status)
	}
	return nil
}

// VerbOutcome is what the portal answered, kept verbatim: the evidence it read back, or the reason
// it refused. The runner reports this rather than its own account of what happened.
type VerbOutcome struct {
	Status   string
	HTTP     int
	Evidence string
	Detail   string
	Error    string
	ID       string
}

func (r *Runner) CallVerb(ctx context.Context, verb string, args map[string]any) (VerbOutcome, error) {
	payload := map[string]any{"verb": verb}
	for key, value := range args {
		payload[key] = value
	}
	response, err := r.request(ctx, http.MethodPost, "/api/agent/verb", payload)
	if err != nil {
		return VerbOutcome{}, err
	}
	defer response.Body.Close()
	var body struct {
		Status   string `json:"status"`
		Evidence string `json:"evidence"`
		Detail   string `json:"detail"`
		Error    string `json:"error"`
		ID       string `json:"id"`
	}
	_ = json.NewDecoder(response.Body).Decode(&body)
	return VerbOutcome{
		Status: body.Status, HTTP: response.StatusCode, Evidence: body.Evidence,
		Detail: body.Detail, Error: body.Error, ID: body.ID,
	}, nil
}

func (r *Runner) loadCursor() {
	if r.cfg.StatePath == "" {
		return
	}
	raw, err := os.ReadFile(r.cfg.StatePath)
	if err != nil {
		return
	}
	if parsed, err := strconv.ParseInt(strings.TrimSpace(string(raw)), 10, 64); err == nil {
		r.cursor = parsed
	}
}

// saveCursor is what stops a restart from replaying the conversation and acting twice on a request
// an operator already saw answered.
func (r *Runner) saveCursor() {
	if r.cfg.StatePath == "" {
		return
	}
	_ = os.WriteFile(r.cfg.StatePath, []byte(strconv.FormatInt(r.cursor, 10)), 0o600)
}

func (r *Runner) Cursor() int64 { return r.cursor }

// Run polls until the context is cancelled.
func (r *Runner) Run(ctx context.Context) error {
	if _, err := r.Vocabulary(ctx); err != nil {
		return fmt.Errorf("cannot read the verb vocabulary: %w", err)
	}
	for {
		if err := r.Step(ctx); err != nil && !errors.Is(err, context.Canceled) {
			// A failing step must not end the loop: the operator is mid-conversation, and the
			// usual cause is the portal restarting underneath.
			fmt.Fprintf(os.Stderr, "runner: %v\n", err)
		}
		select {
		case <-ctx.Done():
			return nil
		case <-time.After(r.cfg.Poll):
		}
	}
}

// Step reads the inbox once and answers any operator turn it finds.
func (r *Runner) Step(ctx context.Context) error {
	response, err := r.request(ctx, http.MethodGet, "/api/agent/inbox?since="+strconv.FormatInt(r.cursor, 10), nil)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return fmt.Errorf("inbox: %s", response.Status)
	}
	var box inbox
	if err := json.NewDecoder(response.Body).Decode(&box); err != nil {
		return err
	}
	if len(box.Messages) == 0 {
		return nil
	}
	// The cursor advances over everything read, including system turns, so a verb outcome is
	// context for the next answer rather than a new request to answer.
	if box.Cursor > r.cursor {
		r.cursor = box.Cursor
		r.saveCursor()
	}
	operatorSpoke := false
	for _, m := range box.Messages {
		if m.Role == "operator" {
			operatorSpoke = true
		}
	}
	if !operatorSpoke {
		return nil
	}
	if len(box.Awaiting) > 0 {
		// Something is already waiting on the operator. Acting again here would queue a second
		// approval behind the first, which is how an operator ends up approving in the dark.
		return r.Say(ctx, "Waiting on your decision for "+summarise(box.Awaiting)+" before doing anything else.")
	}
	return r.answer(ctx, box.Messages)
}

func summarise(awaiting []map[string]any) string {
	parts := make([]string, 0, len(awaiting))
	for _, entry := range awaiting {
		if summary, ok := entry["summary"].(string); ok {
			parts = append(parts, summary)
		}
	}
	if len(parts) == 0 {
		return "a pending request"
	}
	return strings.Join(parts, "; ")
}

func (r *Runner) answer(ctx context.Context, conversation []message) error {
	prompt := r.Prompt(conversation)
	decision, err := r.decider.Decide(ctx, prompt)
	if err != nil {
		return r.Say(ctx, "I could not reach the model to answer that: "+err.Error())
	}
	if decision.Say == "" && decision.Verb == "" {
		return r.Say(ctx, "I did not produce a usable answer to that, so I have done nothing.")
	}
	if decision.Say != "" {
		if err := r.Say(ctx, decision.Say); err != nil {
			return err
		}
	}
	if decision.Verb == "" {
		return nil
	}
	if known, ok := r.known(decision.Verb); !ok {
		return r.Say(ctx, "I asked for a verb that does not exist: "+decision.Verb)
	} else if !known.Available {
		reason := known.Because
		if reason == "" {
			reason = "not available"
		}
		return r.Say(ctx, "I cannot use "+decision.Verb+": "+reason)
	}
	outcome, err := r.CallVerb(ctx, decision.Verb, decision.Args)
	if err != nil {
		return r.Say(ctx, "The verb call failed to reach the portal: "+err.Error())
	}
	return r.report(ctx, decision.Verb, outcome)
}

// report states what the portal answered. The evidence it read back is quoted; nothing is
// summarised into "done".
func (r *Runner) report(ctx context.Context, verb string, outcome VerbOutcome) error {
	switch {
	case outcome.HTTP == http.StatusAccepted:
		// The portal already wrote the awaiting-approval turn, so this only says what it is for.
		return r.Say(ctx, "Requested "+verb+"; it needs your approval before it runs.")
	case outcome.HTTP == http.StatusForbidden:
		return r.Say(ctx, verb+" is forbidden by policy, so I have stopped rather than looking for another way.")
	case outcome.HTTP == http.StatusNotImplemented:
		return r.Say(ctx, verb+" is not available through the portal: "+outcome.Error)
	case outcome.HTTP == http.StatusBadRequest:
		return r.Say(ctx, "I called "+verb+" wrongly: "+outcome.Error)
	case outcome.Status == "succeeded":
		evidence := strings.TrimSpace(outcome.Evidence)
		if evidence == "" {
			return r.Say(ctx, verb+" ran and returned no output.")
		}
		return r.Say(ctx, verb+" ran. What the portal read back:\n"+fitForConversation(evidence))
	default:
		detail := outcome.Detail
		if detail == "" {
			detail = outcome.Error
		}
		return r.Say(ctx, verb+" failed: "+detail)
	}
}

// conversationBudget is what one turn may carry. The portal caps a message at 20000 bytes and caps
// the evidence it records at the same number, so quoting evidence verbatim with any prefix at all
// could exceed the limit - and did: a 200-line log tail came back at 20012 bytes, the report was
// refused with 400, and the whole pass failed on its own answer. A conversation is also simply the
// wrong place for 20 KB: the log page exists for that.
const conversationBudget = 4000

// fitForConversation keeps the end of long evidence, because for a log tail, a status dump, or a
// mod list, the end is the part that answers the question. It says what it dropped rather than
// trimming silently, so the operator knows to look at the page for the rest.
func fitForConversation(evidence string) string {
	if len(evidence) <= conversationBudget {
		return evidence
	}
	tail := evidence[len(evidence)-conversationBudget:]
	// Start at a line boundary so the first line is not half a line.
	if cut := strings.IndexByte(tail, '\n'); cut >= 0 && cut < len(tail)-1 {
		tail = tail[cut+1:]
	}
	return fmt.Sprintf("[showing the last %d of %d bytes; the rest is on the world's log page]\n%s",
		len(tail), len(evidence), tail)
}

func (r *Runner) known(id string) (Verb, bool) {
	for _, verb := range r.vocab {
		if verb.ID == id {
			return verb, true
		}
	}
	return Verb{}, false
}

// Prompt is deliberately explicit about the two things that went wrong here historically: claiming
// something shipped without reading it back, and answering before measuring.
func (r *Runner) Prompt(conversation []message) string {
	var b strings.Builder
	b.WriteString("You manage a modded Valheim deployment through a fixed set of verbs. You have no shell.\n")
	b.WriteString("Reply with ONE JSON object and nothing else:\n")
	b.WriteString(`  {"say": "text for the operator", "verb": "<verb id or empty>", "args": {"world": "...", ...}}` + "\n\n")
	b.WriteString("Rules that are not negotiable:\n")
	b.WriteString("- One verb per reply. If more is needed, say so and wait.\n")
	b.WriteString("- Never claim something happened. The portal reports what it read back; that is the evidence.\n")
	b.WriteString("- Say \"unmeasured\" rather than \"negligible\" when you have not measured.\n")
	b.WriteString("- A verb needing approval will wait for the operator. That is normal, not a failure.\n")
	b.WriteString("- Narrow a read with the arguments a verb accepts rather than trimming the answer yourself: " +
		"asking for 5 lines is 5 lines of evidence, while asking for the default and quoting part of it " +
		"is a claim about what you left out.\n")
	b.WriteString("- If a question is outstanding, ask it and request no verb.\n\n")
	b.WriteString("Verbs available to you:\n")
	vocab := append([]Verb(nil), r.vocab...)
	sort.Slice(vocab, func(i, j int) bool { return vocab[i].ID < vocab[j].ID })
	for _, verb := range vocab {
		if !verb.Available {
			continue
		}
		line := "  " + verb.ID + " (" + verb.Class + ")"
		if len(verb.Needs) > 0 {
			line += " needs: " + strings.Join(verb.Needs, ", ")
		}
		if len(verb.Accepts) > 0 {
			line += " accepts: " + strings.Join(verb.Accepts, ", ")
		}
		if verb.NeedsApproval {
			line += " [operator must approve]"
		}
		b.WriteString(line + "\n")
	}
	unavailable := make([]string, 0, len(vocab))
	for _, verb := range vocab {
		if !verb.Available {
			unavailable = append(unavailable, verb.ID)
		}
	}
	if len(unavailable) > 0 {
		b.WriteString("Not available, do not ask: " + strings.Join(unavailable, ", ") + "\n")
	}
	b.WriteString("\nConversation, oldest first:\n")
	for _, m := range conversation {
		b.WriteString(m.Role + ": " + strings.TrimSpace(m.Body) + "\n")
	}
	return b.String()
}

// CommandDecider runs an external command - in production `omp -p <prompt> --mode json` - and takes
// the assistant's final text as the decision. omp owns authentication, so no provider credential
// ever reaches this process.
type CommandDecider struct {
	Argv    []string
	Timeout time.Duration
}

func (c CommandDecider) Decide(ctx context.Context, prompt string) (Decision, error) {
	if len(c.Argv) == 0 {
		return Decision{}, errors.New("no decider command configured")
	}
	timeout := c.Timeout
	if timeout <= 0 {
		timeout = 10 * time.Minute
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	argv := append([]string(nil), c.Argv...)
	argv = append(argv, prompt)
	cmd := exec.CommandContext(ctx, argv[0], argv[1:]...)
	var out, errOut bytes.Buffer
	cmd.Stdout, cmd.Stderr = &out, &errOut
	if err := cmd.Run(); err != nil {
		return Decision{}, fmt.Errorf("%s: %w: %s", argv[0], err, strings.TrimSpace(errOut.String()))
	}
	text, err := AssistantText(out.Bytes())
	if err != nil {
		return Decision{}, err
	}
	return ParseDecision(text)
}

// AssistantText pulls the final assistant message out of omp's JSONL event stream. Plain text is
// accepted too, so a simpler decider needs no event format.
func AssistantText(raw []byte) (string, error) {
	scanner := bufio.NewScanner(bytes.NewReader(raw))
	scanner.Buffer(make([]byte, 0, 1<<20), 8<<20)
	latest := ""
	sawEvent := false
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if !strings.HasPrefix(line, "{") {
			continue
		}
		var event struct {
			Type    string `json:"type"`
			Message *struct {
				Role    string `json:"role"`
				Content []struct {
					Type string `json:"type"`
					Text string `json:"text"`
				} `json:"content"`
			} `json:"message"`
		}
		if json.Unmarshal([]byte(line), &event) != nil {
			continue
		}
		sawEvent = true
		if event.Message == nil || event.Message.Role != "assistant" {
			continue
		}
		if event.Type != "message_end" && event.Type != "turn_end" {
			continue
		}
		var text strings.Builder
		for _, part := range event.Message.Content {
			if part.Type == "text" {
				text.WriteString(part.Text)
			}
		}
		if text.Len() > 0 {
			latest = text.String()
		}
	}
	if latest != "" {
		return latest, nil
	}
	if sawEvent {
		return "", errors.New("no assistant message in the model's output")
	}
	if trimmed := strings.TrimSpace(string(raw)); trimmed != "" {
		return trimmed, nil
	}
	return "", errors.New("the model produced no output")
}

// ParseDecision accepts the JSON object, with or without a fenced code block around it, and
// refuses anything else rather than guessing what was meant.
func ParseDecision(text string) (Decision, error) {
	trimmed := strings.TrimSpace(text)
	if fence := strings.Index(trimmed, "```"); fence >= 0 {
		rest := trimmed[fence+3:]
		if newline := strings.Index(rest, "\n"); newline >= 0 {
			rest = rest[newline+1:]
		}
		if close := strings.Index(rest, "```"); close >= 0 {
			rest = rest[:close]
		}
		trimmed = strings.TrimSpace(rest)
	}
	start, end := strings.Index(trimmed, "{"), strings.LastIndex(trimmed, "}")
	if start < 0 || end <= start {
		return Decision{}, fmt.Errorf("no JSON object in the model's answer: %.120q", trimmed)
	}
	var decision Decision
	if err := json.Unmarshal([]byte(trimmed[start:end+1]), &decision); err != nil {
		return Decision{}, fmt.Errorf("unreadable decision: %w", err)
	}
	return decision, nil
}
