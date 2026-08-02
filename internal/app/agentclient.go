package app

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"
)

type AgentClient struct {
	socket string
	token  []byte
}
type agentRequest struct {
	ID              string `json:"id"`
	World           string `json:"world"`
	Operation       string `json:"operation"`
	Backup          string `json:"backup,omitempty"`
	Port            int    `json:"port,omitempty"`
	Profile         string `json:"profile,omitempty"`
	Query           string `json:"query,omitempty"`
	Identifier      string `json:"identifier,omitempty"`
	Version         string `json:"version,omitempty"`
	Scope           string `json:"scope,omitempty"`
	Reason          string `json:"reason,omitempty"`
	ServerName      string `json:"server_name,omitempty"`
	Password        string `json:"password,omitempty"`
	Public          bool   `json:"public,omitempty"`
	Crossplay       bool   `json:"crossplay,omitempty"`
	PlayerLimit     int    `json:"player_limit,omitempty"`
	Preset          string `json:"preset,omitempty"`
	BackupInterval  string `json:"backup_interval,omitempty"`
	BackupAge       int    `json:"backup_age,omitempty"`
	BackupCount     int    `json:"backup_count,omitempty"`
	Seed            string `json:"seed,omitempty"`
	SourceWorld     string `json:"source_world,omitempty"`
	TemplateWorld   string `json:"template_world,omitempty"`
	TemplateProfile string `json:"template_profile,omitempty"`
	Start           bool   `json:"start,omitempty"`
	Admins          string `json:"admins,omitempty"`
	Permitted       string `json:"permitted,omitempty"`
	Timestamp       int64  `json:"timestamp"`
	Signature       string `json:"signature"`
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
}
type ProvisionAgentRequest struct {
	ServerName      string
	Password        string
	Port            int
	Public          bool
	Crossplay       bool
	PlayerLimit     int
	Preset          string
	BackupInterval  string
	BackupAge       int
	BackupCount     int
	Profile         string
	Seed            string
	SourceWorld     string
	TemplateWorld   string
	TemplateProfile string
	Start           bool
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
		Scope: request.Scope, Reason: request.Reason, Timestamp: time.Now().Unix(),
	})
}
func (a *AgentClient) RunProvision(ctx context.Context, id, world string, request ProvisionAgentRequest) (AgentReply, error) {
	return a.do(ctx, agentRequest{
		ID: id, World: world, Operation: "provision", Port: request.Port, Profile: request.Profile,
		ServerName: request.ServerName, Password: request.Password, Public: request.Public,
		Crossplay: request.Crossplay, PlayerLimit: request.PlayerLimit, Preset: request.Preset,
		BackupInterval: request.BackupInterval, BackupAge: request.BackupAge, BackupCount: request.BackupCount,
		Seed: request.Seed, SourceWorld: request.SourceWorld, TemplateWorld: request.TemplateWorld,
		TemplateProfile: request.TemplateProfile, Start: request.Start, Timestamp: time.Now().Unix(),
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
	m := hmac.New(sha256.New, a.token)
	m.Write([]byte(strings.Join([]string{
		r.ID, r.World, r.Operation, r.Backup, strconv.Itoa(r.Port), r.Profile,
		r.Query, r.Identifier, r.Version, r.Scope, r.Reason, r.ServerName, r.Password,
		strconv.FormatBool(r.Public), strconv.FormatBool(r.Crossplay), strconv.Itoa(r.PlayerLimit), r.Preset,
		r.BackupInterval, strconv.Itoa(r.BackupAge), strconv.Itoa(r.BackupCount), r.Seed,
		r.SourceWorld, r.TemplateWorld, r.TemplateProfile, strconv.FormatBool(r.Start), r.Admins, r.Permitted, fmtInt(r.Timestamp),
	}, "\n")))
	r.Signature = hex.EncodeToString(m.Sum(nil))
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
	var result AgentReply
	if err = json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return AgentReply{}, err
	}
	if resp.StatusCode != http.StatusOK {
		return result, errors.New("agent rejected request")
	}
	return result, nil
}
func fmtInt(v int64) string { return strconv.FormatInt(v, 10) }
