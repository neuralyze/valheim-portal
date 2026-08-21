package app

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/neuralyze/valheim-portal/internal/agent"
)

type AgentClient struct {
	socket string
	token  []byte
}
type agentRequest struct {
	ID             string `json:"id"`
	World          string `json:"world"`
	Operation      string `json:"operation"`
	Backup         string `json:"backup,omitempty"`
	Port           int    `json:"port,omitempty"`
	Profile        string `json:"profile,omitempty"`
	Query          string `json:"query,omitempty"`
	Identifier     string `json:"identifier,omitempty"`
	Version        string `json:"version,omitempty"`
	Scope          string `json:"scope,omitempty"`
	Reason         string `json:"reason,omitempty"`
	ServerName     string `json:"server_name,omitempty"`
	Password       string `json:"password,omitempty"`
	Public         bool   `json:"public,omitempty"`
	Crossplay      bool   `json:"crossplay,omitempty"`
	PlayerLimit    int    `json:"player_limit,omitempty"`
	Preset         string `json:"preset,omitempty"`
	BackupInterval string `json:"backup_interval,omitempty"`
	BackupAge      int    `json:"backup_age,omitempty"`
	BackupCount    int    `json:"backup_count,omitempty"`
	Seed           string `json:"seed,omitempty"`
	SourceWorld    string `json:"source_world,omitempty"`
	CopyFrom       string `json:"copy_from,omitempty"`
	Start          bool   `json:"start,omitempty"`
	Admins         string `json:"admins,omitempty"`
	Permitted      string `json:"permitted,omitempty"`
	Timestamp      int64  `json:"timestamp"`
	// Arguments the agent surface added. They are part of the signed payload on the agent
	// side, so anything added here must also be added to agent.Canonical.
	Lines            int    `json:"lines,omitempty"`
	ClientType       string `json:"client_type,omitempty"`
	PublishedProfile string `json:"published_profile,omitempty"`
	ReleaseID        string `json:"release_id,omitempty"`
	Archive          string `json:"archive,omitempty"`
	Notes            string `json:"notes,omitempty"`
	Signature        string `json:"signature"`
}
type AgentReply struct {
	Status      string          `json:"status"`
	Output      string          `json:"output"`
	Data        json.RawMessage `json:"data,omitempty"`
	Error       string          `json:"error"`
	Provisioned bool            `json:"provisioned,omitempty"`
	Ready       bool            `json:"ready,omitempty"`
}
type ModAgentRequest struct {
	Operation  string
	Profile    string
	Query      string
	Identifier string
	Version    string
	Scope      string
	Reason     string
	// Lines bounds mod_notes; the rest are the release-confirm arguments.
	Lines            int
	ClientType       string
	PublishedProfile string
	ReleaseID        string
	Archive          string
}
type ProvisionAgentRequest struct {
	ServerName     string
	Password       string
	Port           int
	Public         bool
	Crossplay      bool
	PlayerLimit    int
	Preset         string
	BackupInterval string
	BackupAge      int
	BackupCount    int
	Profile        string
	Seed           string
	SourceWorld    string
	CopyFrom       string
	Start          bool
}

func NewAgentClient(socket, tokenFile string) (*AgentClient, error) {
	b, err := os.ReadFile(tokenFile)
	if err != nil {
		return nil, err
	}
	b = []byte(strings.TrimSpace(string(b)))
	if len(b) < 32 {
		return nil, errors.New("agent token must contain at least 32 bytes")
	}
	return &AgentClient{socket: socket, token: b}, nil
}
func (a *AgentClient) Run(ctx context.Context, id, world, operation string) (AgentReply, error) {
	return a.run(ctx, id, world, operation, "", 0)
}
func (a *AgentClient) RunWithBackup(ctx context.Context, id, world, operation, backup string) (AgentReply, error) {
	return a.run(ctx, id, world, operation, backup, 0)
}
func (a *AgentClient) RunWithPort(ctx context.Context, id, world string, port int) (AgentReply, error) {
	return a.run(ctx, id, world, "set_port", "", port)
}

// RunWithSeed drives world_create. The seed is part of the signed canonical
// string, so a seed altered in transit fails verification rather than quietly
// generating a different world.
func (a *AgentClient) RunWithSeed(ctx context.Context, id, world, seed string) (AgentReply, error) {
	return a.do(ctx, agentRequest{ID: id, World: world, Operation: "world_create", Seed: seed, Timestamp: time.Now().Unix()})
}
func (a *AgentClient) run(ctx context.Context, id, world, operation, backup string, port int) (AgentReply, error) {
	return a.do(ctx, agentRequest{ID: id, World: world, Operation: operation, Backup: backup, Port: port, Timestamp: time.Now().Unix()})
}
func (a *AgentClient) RunMod(ctx context.Context, id, world string, request ModAgentRequest) (AgentReply, error) {
	return a.do(ctx, agentRequest{
		ID: id, World: world, Operation: request.Operation, Profile: request.Profile,
		Query: request.Query, Identifier: request.Identifier, Version: request.Version,
		Scope: request.Scope, Reason: request.Reason, Lines: request.Lines,
		ClientType: request.ClientType, PublishedProfile: request.PublishedProfile,
		ReleaseID: request.ReleaseID, Archive: request.Archive, Timestamp: time.Now().Unix(),
	})
}

// RunPublish publishes one profile for one world. Artifact inputs are deliberately absent: the
// host script carries the newest plugin and VR runtime forward from that profile's own previous
// release, so a caller cannot aim a release at an arbitrary file.
func (a *AgentClient) RunPublish(ctx context.Context, id, world, profile, clientType, notes string) (AgentReply, error) {
	return a.do(ctx, agentRequest{
		ID: id, World: world, Operation: "publish_profile", Profile: profile,
		ClientType: clientType, Notes: notes, Timestamp: time.Now().Unix(),
	})
}

// RunLog reads the tail of a world's collected host log. Lines and filter are bounded here as well
// as in the script: the agent refuses an out-of-range request, and refusing early keeps a mistyped
// URL from becoming an error three layers down.
func (a *AgentClient) RunLog(ctx context.Context, id, world string, lines int, filter string) (AgentReply, error) {
	if lines < 1 {
		lines = 1
	}
	if lines > 5000 {
		lines = 5000
	}
	return a.do(ctx, agentRequest{
		ID: id, World: world, Operation: "world_log", Lines: lines, Query: filter,
		Timestamp: time.Now().Unix(),
	})
}

// RunLogInfo asks only whether a log exists and how large it is, reading none of it.
func (a *AgentClient) RunLogInfo(ctx context.Context, id, world string) (AgentReply, error) {
	return a.do(ctx, agentRequest{
		ID: id, World: world, Operation: "world_log_info", Timestamp: time.Now().Unix(),
	})
}

func (a *AgentClient) RunProvision(ctx context.Context, id, world string, request ProvisionAgentRequest) (AgentReply, error) {
	return a.do(ctx, agentRequest{
		ID: id, World: world, Operation: "provision", Port: request.Port, Profile: request.Profile,
		ServerName: request.ServerName, Password: request.Password, Public: request.Public,
		Crossplay: request.Crossplay, PlayerLimit: request.PlayerLimit, Preset: request.Preset,
		BackupInterval: request.BackupInterval, BackupAge: request.BackupAge, BackupCount: request.BackupCount,
		Seed: request.Seed, SourceWorld: request.SourceWorld, CopyFrom: request.CopyFrom,
		Start: request.Start, Timestamp: time.Now().Unix(),
	})
}

// RunAccessApply writes the generated access lists for one world. RunAccessState
// reads back what is actually in force, so the admin page can prove the host
// matches the portal instead of assuming it.
func (a *AgentClient) RunAccessApply(ctx context.Context, id, world string, admins, permitted []string) (AgentReply, error) {
	return a.do(ctx, agentRequest{
		ID: id, World: world, Operation: "access_apply",
		Admins: strings.Join(admins, ","), Permitted: strings.Join(permitted, ","),
		Timestamp: time.Now().Unix(),
	})
}

func (a *AgentClient) RunAccessState(ctx context.Context, id, world string) (AgentReply, error) {
	return a.do(ctx, agentRequest{ID: id, World: world, Operation: "access_state", Timestamp: time.Now().Unix()})
}

func (a *AgentClient) do(ctx context.Context, r agentRequest) (AgentReply, error) {
	// Signed with the agent's own canonical form rather than a copy of it. This used to be a
	// duplicated field list, which meant every new argument had to be added in two places and
	// a missed one shipped as a signature failure at runtime.
	r.Signature = agent.Sign(a.token, agent.Request{
		ID: r.ID, World: r.World, Operation: r.Operation, Backup: r.Backup, Port: r.Port,
		Profile: r.Profile, Query: r.Query, Identifier: r.Identifier, Version: r.Version,
		Scope: r.Scope, Reason: r.Reason, ServerName: r.ServerName, Password: r.Password,
		Public: r.Public, Crossplay: r.Crossplay, PlayerLimit: r.PlayerLimit, Preset: r.Preset,
		BackupInterval: r.BackupInterval, BackupAge: r.BackupAge, BackupCount: r.BackupCount,
		Seed: r.Seed, SourceWorld: r.SourceWorld, CopyFrom: r.CopyFrom,
		Start: r.Start, Admins: r.Admins, Permitted: r.Permitted,
		Timestamp: r.Timestamp, Lines: r.Lines, ClientType: r.ClientType,
		PublishedProfile: r.PublishedProfile, ReleaseID: r.ReleaseID, Archive: r.Archive, Notes: r.Notes,
	})
	body, err := json.Marshal(r)
	if err != nil {
		return AgentReply{}, err
	}
	transport := &http.Transport{DialContext: func(ctx context.Context, network, address string) (net.Conn, error) {
		return net.Dial("unix", a.socket)
	}}
	defer transport.CloseIdleConnections()
	req, err := http.NewRequestWithContext(ctx, "POST", "http://agent/v1/jobs", bytes.NewReader(body))
	if err != nil {
		return AgentReply{}, err
	}
	resp, err := (&http.Client{Transport: transport, Timeout: 37 * time.Minute}).Do(req)
	if err != nil {
		return AgentReply{}, err
	}
	defer resp.Body.Close()
	// Read first, decode second. Decoding straight from the body turned every non-JSON refusal
	// into "invalid character 'o' in literal false", because the agent's plain-text "forbidden"
	// was parsed as the literal false. The operator then saw a JSON complaint about a request the
	// agent had correctly rejected, with nothing naming the actual reason.
	// 32 MiB matches the agent's own JSON cap. The world config schema is 4.5 MiB for the
	// smallest world and grows with the mod set, so an 8 MiB reader here would have become the
	// next ceiling the moment the agent's was raised on 2026-08-21.
	raw, readErr := io.ReadAll(io.LimitReader(resp.Body, 32<<20))
	if readErr != nil {
		return AgentReply{}, fmt.Errorf("agent reply unreadable: %w", readErr)
	}
	var result AgentReply
	if decodeErr := json.Unmarshal(raw, &result); decodeErr != nil {
		text := strings.TrimSpace(string(raw))
		if len(text) > 200 {
			text = text[:200] + "…"
		}
		if text == "" {
			text = "empty body"
		}
		return AgentReply{}, fmt.Errorf("agent answered %d with a non-JSON body: %s", resp.StatusCode, text)
	}
	if resp.StatusCode != http.StatusOK {
		if result.Error != "" {
			return result, fmt.Errorf("agent refused the request: %s", result.Error)
		}
		return result, fmt.Errorf("agent refused the request with status %d", resp.StatusCode)
	}
	return result, nil
}
func fmtInt(v int64) string { return strconv.FormatInt(v, 10) }
