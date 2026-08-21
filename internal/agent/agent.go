package agent

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/neuralyze/valheim-portal/internal/worldintel"
)

var worldName = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$`)
var modIdentifier = regexp.MustCompile(`^[A-Za-z0-9_.]+-[A-Za-z0-9_.-]+$`)
var modVersion = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._+-]{0,79}$`)
var serverDisplayName = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9 ._:-]{2,79}$`)
var serverPassword = regexp.MustCompile(`^[A-Za-z0-9!@#$%^&*._+?-]{5,64}$`)
var worldSeed = regexp.MustCompile(`^[A-Za-z0-9]{1,64}$`)

type Config struct {
	Socket        string
	TokenFile     string
	ScriptDir     string
	WorldRoot     string
	AllowedWorlds map[string]struct{}
}
type Request struct {
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
	Timestamp      int64  `json:"timestamp"`
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
	// Lines bounds the changelog output of mod_notes. ClientType, ReleaseID and Archive are
	// the release-confirm arguments; Notes is the mandatory release note for publish_profile.
	Lines      int    `json:"lines,omitempty"`
	ClientType string `json:"client_type,omitempty"`
	ReleaseID  string `json:"release_id,omitempty"`
	Archive    string `json:"archive,omitempty"`
	// PublishedProfile is the release-confirm target, which is the published profile name
	// rather than the source profile the request already carries.
	PublishedProfile string `json:"published_profile,omitempty"`
	Notes            string `json:"notes,omitempty"`
	Signature        string `json:"signature"`
}
type Response struct {
	Status      string          `json:"status"`
	Output      string          `json:"output"`
	Data        json.RawMessage `json:"data,omitempty"`
	Error       string          `json:"error,omitempty"`
	Provisioned bool            `json:"provisioned,omitempty"`
	Ready       bool            `json:"ready,omitempty"`
}

var operations = map[string]string{
	"status": "status_valheim_server.sh", "logs": "logs_valheim_server_snapshot.sh",
	"backups": "list_valheim_world_backups.sh", "start": "start_valheim_server.sh",
	"stop": "stop_valheim_server.sh", "restart": "stop_valheim_server.sh",
	"pause": "pause_valheim_server.sh", "resume": "unpause_valheim_server.sh",
	"backup": "backup_valheim_world.sh", "build": "build_valheim_server.sh",
	"delete_server":   "portal_delete_valheim_server.sh",
	"set_port":        "configure_valheim_port.sh",
	"restore":         "restore_valheim_world.sh",
	"profile_catalog": "portal_profile_catalog.sh",
	"world_metadata":  "portal_world_metadata.sh",
	"mod_inventory":   "portal_mod_admin.sh", "mod_search": "portal_mod_admin.sh",
	"mod_custom_list": "portal_mod_admin.sh", "mod_add": "portal_mod_admin.sh",
	"mod_remove": "portal_mod_admin.sh", "mod_enable": "portal_mod_admin.sh",
	"mod_disable": "portal_mod_admin.sh", "mod_custom_add": "portal_mod_admin.sh",
	"mod_custom_remove": "portal_mod_admin.sh", "mod_custom_enable": "portal_mod_admin.sh",
	"mod_custom_disable": "portal_mod_admin.sh", "mod_deploy": "portal_mod_admin.sh",
	// Read-only mod operations, plus the two mutating ones the agent surface needs. Every one
	// runs through the same portal_mod_admin.sh action dispatch as the rest.
	"mod_check_updates":   "portal_mod_admin.sh",
	"mod_notes":           "portal_mod_admin.sh",
	"mod_release_status":  "portal_mod_admin.sh",
	"mod_deploy_plan":     "portal_mod_admin.sh",
	"mod_update":          "portal_mod_admin.sh",
	"mod_release_confirm": "portal_mod_admin.sh",
	// Publishing narrows the catalog to one target and takes no artifact paths from its
	// caller; the newest plugin and runtime are carried forward from the profile's own
	// previous release.
	"publish_profile": "portal_publish_profile.sh",
	// The collected host log, which outlives the container the snapshot operation reads.
	"world_log": "portal_world_log.sh",
	// Whether a log exists and how large it is, reading none of it.
	"world_log_info": "portal_world_log.sh",
	"world_catalog":  "@internal",
	"world_analysis": "@internal",
	"world_map":      "export_valheim_map_sources.sh",
	"provision":      "provision_valheim_server.sh",
	"health":         "wait_valheim_server_ready.sh",
	// world_create regenerates a world on a chosen seed. It carries a seed but is
	// not provisioning: the world directory already exists and only its save pair
	// is replaced.
	"world_create": "portal_create_valheim_world.sh",
	// Access lists are generated from portal membership: access_apply writes
	// them to the host, access_state reads back what is actually in place.
	"access_apply": "portal_access_lists.sh",
	"access_state": "@internal",
}

// Canonical is the signed payload. A field absent from this list travels unauthenticated, so
// every new argument has to be added here as well as to the struct - the fields below are
// appended rather than inserted so the ordering of the existing ones never shifts.
func Canonical(r Request) string {
	return strings.Join([]string{
		r.ID, r.World, r.Operation, r.Backup, fmt.Sprint(r.Port), r.Profile,
		r.Query, r.Identifier, r.Version, r.Scope, r.Reason, r.ServerName, r.Password,
		fmt.Sprint(r.Public), fmt.Sprint(r.Crossplay), fmt.Sprint(r.PlayerLimit), r.Preset,
		r.BackupInterval, fmt.Sprint(r.BackupAge), fmt.Sprint(r.BackupCount), r.Seed,
		r.SourceWorld, r.CopyFrom, fmt.Sprint(r.Start), r.Admins, r.Permitted, fmt.Sprint(r.Timestamp),
		fmt.Sprint(r.Lines), r.ClientType, r.PublishedProfile, r.ReleaseID, r.Archive, r.Notes,
	}, "\n")
}
func Sign(token []byte, r Request) string {
	m := hmac.New(sha256.New, token)
	m.Write([]byte(Canonical(r)))
	return hex.EncodeToString(m.Sum(nil))
}
func Verify(token []byte, allowed map[string]struct{}, r Request) error {
	catalog := r.Operation == "world_catalog"
	if r.ID == "" || operations[r.Operation] == "" || (catalog && r.World != "") || (!catalog && !worldName.MatchString(r.World)) {
		return errors.New("invalid agent request")
	}
	if r.Operation == "restore" {
		if !validBackupName(r.World, r.Backup) {
			return errors.New("invalid restore backup")
		}
	} else if r.Backup != "" {
		return errors.New("unexpected backup argument")
	}
	if r.Operation == "set_port" || r.Operation == "provision" {
		if r.Port < 1024 || r.Port > 65533 {
			return errors.New("invalid game port")
		}
	} else if r.Port != 0 {
		return errors.New("unexpected port argument")
	}
	if r.Operation == "access_apply" {
		if err := validateAccessLists(r); err != nil {
			return err
		}
	} else if r.Admins != "" || r.Permitted != "" {
		return errors.New("unexpected access list argument")
	}
	if err := validateAgentSurfaceArguments(r); err != nil {
		return err
	}
	if r.Operation == "provision" {
		if err := validateProvisionRequest(r); err != nil {
			return err
		}
	} else {
		if err := validateModRequest(r); err != nil {
			return err
		}
		if r.Operation == "world_create" {
			if err := validateWorldCreateRequest(r); err != nil {
				return err
			}
		} else if !provisionFieldsEmpty(r) {
			return errors.New("unexpected provisioning arguments")
		}
	}
	if !catalog {
		if _, ok := allowed[r.World]; !ok && r.Operation != "provision" {
			return fmt.Errorf("%w: world is not allowed", ErrCapability)
		}
	}
	if d := time.Since(time.Unix(r.Timestamp, 0)); d > time.Minute || d < -time.Minute {
		return fmt.Errorf("%w: stale agent request", ErrCapability)
	}
	actual, err := hex.DecodeString(r.Signature)
	if err != nil {
		return fmt.Errorf("%w: invalid signature", ErrCapability)
	}
	expected, _ := hex.DecodeString(Sign(token, r))
	if !hmac.Equal(actual, expected) {
		return fmt.Errorf("%w: invalid signature", ErrCapability)
	}
	return nil
}

// ErrCapability marks the refusals that must not explain themselves: a wrong signature, a replayed
// timestamp, a world this agent does not control. Every other refusal is about the shape of the
// request, and those the caller is told, because the vocabulary and its argument rules are already
// public at /api/agent/verbs - and a caller that cannot see why it was refused retries the same
// request or, worse, reports a JSON parse error to an operator.
var ErrCapability = errors.New("forbidden")

func argumentProblem(err error) bool { return !errors.Is(err, ErrCapability) }

func provisionFieldsEmpty(r Request) bool {
	return r.ServerName == "" && r.Password == "" && !r.Public && !r.Crossplay && r.PlayerLimit == 0 &&
		r.Preset == "" && r.BackupInterval == "" && r.BackupAge == 0 && r.BackupCount == 0 &&
		r.Seed == "" && r.SourceWorld == "" && r.CopyFrom == "" && !r.Start
}

func validateProvisionRequest(r Request) error {
	if !serverDisplayName.MatchString(r.ServerName) || !serverPassword.MatchString(r.Password) || !worldName.MatchString(r.Profile) {
		return errors.New("invalid server identity")
	}
	if r.PlayerLimit < 1 || r.PlayerLimit > 100 || r.BackupAge < 1 || r.BackupAge > 365 || r.BackupCount < 1 || r.BackupCount > 1000 {
		return errors.New("invalid server limits")
	}
	switch r.Preset {
	case "Normal", "Casual", "Easy", "Hard", "Hardcore", "Immersive", "Hammer":
	default:
		return errors.New("invalid gameplay preset")
	}
	switch r.BackupInterval {
	case "30m", "1h", "6h", "daily":
	default:
		return errors.New("invalid backup interval")
	}
	if r.Seed != "" {
		if len(r.Seed) > 64 || !regexp.MustCompile(`^[A-Za-z0-9]+$`).MatchString(r.Seed) || r.SourceWorld != "" {
			return errors.New("invalid world seed")
		}
	} else if r.SourceWorld != "" && !worldName.MatchString(r.SourceWorld) {
		return errors.New("invalid source world")
	}
	// A profile to copy is named on its own: profiles are shared, so there is no world
	// to qualify it with, and a server is never created from another server.
	if r.CopyFrom != "" && !worldName.MatchString(r.CopyFrom) {
		return errors.New("invalid profile to copy")
	}
	if r.Query != "" || r.Identifier != "" || r.Version != "" || r.Scope != "" || r.Reason != "" || r.Backup != "" {
		return errors.New("unexpected provisioning argument")
	}
	return nil
}

// validateWorldCreateRequest insists on a seed. A world_create without one would
// let Valheim generate a random seed and silently hand the operator a different
// world than the one they asked to recreate.
func validateWorldCreateRequest(r Request) error {
	if !worldSeed.MatchString(r.Seed) {
		return errors.New("invalid world seed")
	}
	if r.SourceWorld != "" || r.CopyFrom != "" ||
		r.ServerName != "" || r.Password != "" || r.Public || r.Crossplay || r.PlayerLimit != 0 ||
		r.Preset != "" || r.BackupInterval != "" || r.BackupAge != 0 || r.BackupCount != 0 || r.Start {
		return errors.New("unexpected world creation arguments")
	}
	return nil
}

// validateAgentSurfaceArguments guards the fields the agent surface added. Each one is accepted
// only by the operation that uses it and refused everywhere else, so a signed request cannot
// carry an argument the script it reaches would not expect.
// releaseID matches the portal's own release identifiers, e.g. hrafnheim-vr-2.5.90.
var releaseID = regexp.MustCompile(`^[a-z0-9][a-z0-9._-]{2,119}$`)

// validProfileArchive accepts a plain profile ZIP name under the artifact root: no absolute
// paths, no traversal, no directories. The confirmation records which archive a release shipped,
// so the value reaches a script and must not be able to name anything else on the host.
func validProfileArchive(value string) bool {
	if value == "" || len(value) > 240 || strings.ContainsAny(value, "\r\n\x00\\") || strings.HasPrefix(value, "/") {
		return false
	}
	for _, part := range strings.Split(value, "/") {
		if part == "" || part == "." || part == ".." {
			return false
		}
	}
	return strings.HasSuffix(strings.ToLower(value), ".zip")
}

func validateAgentSurfaceArguments(r Request) error {
	singleLine := func(value string, limit int) bool {
		return len(value) <= limit && !strings.ContainsAny(value, "\r\n\x00")
	}
	switch r.Operation {
	case "mod_notes":
		if r.Lines < 1 || r.Lines > 200 {
			return errors.New("notes needs a line count between 1 and 200")
		}
	case "mod_release_confirm":
		if !worldName.MatchString(r.PublishedProfile) {
			return errors.New("release confirm needs a published profile")
		}
		if r.ClientType != "vr" && r.ClientType != "flat" {
			return errors.New("release confirm needs client type vr or flat")
		}
		if !releaseID.MatchString(r.ReleaseID) {
			return errors.New("release confirm needs a release id")
		}
		if !validProfileArchive(r.Archive) {
			return errors.New("release confirm needs a profile archive path")
		}
	case "world_log":
		if r.Lines < 1 || r.Lines > 5000 {
			return errors.New("world log needs a line count between 1 and 5000")
		}
		// The filter reaches grep as a fixed string. Bounded and single-line, so it cannot
		// smuggle another argument or spend the agent's time on a pathological pattern.
		if !singleLine(r.Query, 120) {
			return errors.New("world log filter must be a single line of at most 120 characters")
		}
	case "publish_profile":
		if r.ClientType != "vr" && r.ClientType != "flat" {
			return errors.New("publish needs client type vr or flat")
		}
		// The note becomes the release note, and a release nobody can review afterwards is
		// exactly what an unexplained publish produces.
		if trimmed := strings.TrimSpace(r.Notes); len(trimmed) < 8 || !singleLine(trimmed, 500) {
			return errors.New("publish needs a single-line note of 8-500 characters")
		}
	}
	if r.Operation != "mod_notes" && r.Operation != "world_log" && r.Lines != 0 {
		return errors.New("unexpected lines argument")
	}
	if r.Operation != "mod_release_confirm" && (r.PublishedProfile != "" || r.ReleaseID != "" || r.Archive != "") {
		return errors.New("unexpected release confirmation arguments")
	}
	if r.Operation != "mod_release_confirm" && r.Operation != "publish_profile" && r.ClientType != "" {
		return errors.New("unexpected client type argument")
	}
	if r.Operation != "publish_profile" && r.Notes != "" {
		return errors.New("unexpected notes argument")
	}
	return nil
}

func validateModRequest(r Request) error {
	isMod := strings.HasPrefix(r.Operation, "mod_")
	if !isMod {
		// publish_profile is profile-scoped without being a mod action: it names the world's
		// source profile so the host can resolve the one catalog target to publish.
		if r.Operation == "world_log_info" {
			if r.Profile != "" || r.Identifier != "" || r.Version != "" || r.Scope != "" || r.Reason != "" || r.Query != "" || r.Lines != 0 {
				return errors.New("world log info takes no arguments")
			}
			return nil
		}
		if r.Operation == "world_log" {
			// World-scoped, not profile-scoped: a server's log belongs to the world.
			if r.Profile != "" || r.Identifier != "" || r.Version != "" || r.Scope != "" || r.Reason != "" {
				return errors.New("unexpected mod arguments")
			}
			return nil
		}
		if r.Operation == "publish_profile" {
			if !worldName.MatchString(r.Profile) {
				return errors.New("invalid publish profile")
			}
			if r.Query != "" || r.Identifier != "" || r.Version != "" || r.Scope != "" || r.Reason != "" {
				return errors.New("unexpected mod arguments")
			}
			return nil
		}
		if r.Profile != "" || r.Query != "" || r.Identifier != "" || r.Version != "" || r.Scope != "" || r.Reason != "" {
			return errors.New("unexpected mod arguments")
		}
		return nil
	}
	if !worldName.MatchString(r.Profile) {
		return errors.New("invalid mod profile")
	}
	noControl := func(value string, limit int) bool {
		return len(value) <= limit && !strings.ContainsAny(value, "\r\n\x00")
	}
	validCustom := func(value string) bool {
		if !noControl(value, 240) || !strings.HasSuffix(strings.ToLower(value), ".zip") || strings.HasPrefix(value, "/") || strings.Contains(value, "\\") {
			return false
		}
		for _, part := range strings.Split(value, "/") {
			if part == "" || part == "." || part == ".." {
				return false
			}
		}
		return true
	}
	switch r.Operation {
	case "mod_check_updates", "mod_release_status", "mod_deploy_plan":
		if r.Query != "" || r.Identifier != "" || r.Version != "" || r.Scope != "" || r.Reason != "" {
			return errors.New("unexpected mod arguments")
		}
	case "mod_notes":
		if r.Query != "" || r.Identifier != "" || r.Version != "" || r.Scope != "" || r.Reason != "" {
			return errors.New("unexpected mod arguments")
		}
	case "mod_update":
		if !modIdentifier.MatchString(r.Identifier) || r.Query != "" || r.Version != "" || r.Scope != "" || r.Reason != "" {
			return errors.New("invalid mod update")
		}
	case "mod_release_confirm":
		if r.Query != "" || r.Identifier != "" || r.Version != "" || r.Scope != "" || r.Reason != "" {
			return errors.New("unexpected mod arguments")
		}
	case "mod_inventory", "mod_custom_list", "mod_deploy":
		if r.Query != "" || r.Identifier != "" || r.Version != "" || r.Scope != "" || r.Reason != "" {
			return errors.New("unexpected mod arguments")
		}
	case "mod_search":
		if len(strings.TrimSpace(r.Query)) < 2 || !noControl(r.Query, 100) || r.Identifier != "" || r.Version != "" || r.Scope != "" || r.Reason != "" {
			return errors.New("invalid mod search")
		}
	case "mod_add":
		if !modIdentifier.MatchString(r.Identifier) || !modVersion.MatchString(r.Version) || (r.Scope != "shared" && r.Scope != "client-only") || r.Query != "" || r.Reason != "" {
			return errors.New("invalid mod selection")
		}
	case "mod_remove":
		if !modIdentifier.MatchString(r.Identifier) || len(strings.TrimSpace(r.Reason)) < 3 || !noControl(r.Reason, 200) || r.Query != "" || r.Version != "" || r.Scope != "" {
			return errors.New("invalid mod removal")
		}
	case "mod_enable", "mod_disable":
		if !modIdentifier.MatchString(r.Identifier) || r.Query != "" || r.Version != "" || r.Scope != "" || r.Reason != "" {
			return errors.New("invalid mod state change")
		}
	case "mod_custom_add":
		if !validCustom(r.Identifier) || (r.Scope != "shared" && r.Scope != "client-only") || r.Query != "" || r.Version != "" || r.Reason != "" {
			return errors.New("invalid custom mod selection")
		}
	case "mod_custom_remove", "mod_custom_enable", "mod_custom_disable":
		if !validCustom(r.Identifier) || r.Query != "" || r.Version != "" || r.Scope != "" || r.Reason != "" {
			return errors.New("invalid custom mod state change")
		}
	default:
		return errors.New("invalid mod operation")
	}
	return nil
}

func validBackupName(world, backup string) bool {
	return strings.HasPrefix(backup, "world-"+world+"-") && strings.HasSuffix(backup, ".tgz") && filepath.Base(backup) == backup && len(backup) <= 180
}

// maxAccessListIDs bounds a single list so one request can never rewrite a
// server's access lists into something unreviewably large.
const maxAccessListIDs = 200

var steamID = regexp.MustCompile(`^7[0-9]{16}$`)

func validateAccessLists(r Request) error {
	if r.Backup != "" || r.Port != 0 || r.Profile != "" || r.Query != "" || r.Identifier != "" || r.Version != "" || r.Scope != "" || r.Reason != "" {
		return errors.New("unexpected access list argument")
	}
	for _, list := range []string{r.Admins, r.Permitted} {
		if err := validateSteamIDList(list); err != nil {
			return err
		}
	}
	return nil
}

// validateSteamIDList accepts an empty list and otherwise a comma-separated set
// of distinct SteamID64s.
func validateSteamIDList(list string) error {
	if list == "" {
		return nil
	}
	ids := strings.Split(list, ",")
	if len(ids) > maxAccessListIDs {
		return errors.New("access list is too long")
	}
	seen := make(map[string]struct{}, len(ids))
	for _, id := range ids {
		if !steamID.MatchString(id) {
			return errors.New("invalid access list Steam ID")
		}
		if _, duplicate := seen[id]; duplicate {
			return errors.New("duplicate access list Steam ID")
		}
		seen[id] = struct{}{}
	}
	return nil
}

func NewHandler(c Config) (http.Handler, error) {
	token, err := os.ReadFile(c.TokenFile)
	if err != nil {
		return nil, err
	}
	token = []byte(strings.TrimSpace(string(token)))
	if len(token) < 32 {
		return nil, errors.New("agent token must contain at least 32 bytes")
	}
	scriptDir, err := filepath.EvalSymlinks(c.ScriptDir)
	if err != nil {
		return nil, err
	}
	worldRoot, err := filepath.EvalSymlinks(c.WorldRoot)
	if err != nil {
		return nil, err
	}
	allowed := make(map[string]struct{}, len(c.AllowedWorlds))
	for world := range c.AllowedWorlds {
		allowed[world] = struct{}{}
	}
	if entries, readErr := os.ReadDir(worldRoot); readErr == nil {
		for _, entry := range entries {
			if entry.IsDir() && worldName.MatchString(entry.Name()) {
				marker := filepath.Join(worldRoot, entry.Name(), ".portal-managed")
				if info, statErr := os.Lstat(marker); statErr == nil && info.Mode().IsRegular() {
					allowed[entry.Name()] = struct{}{}
				}
			}
		}
	}
	var allowedMu sync.RWMutex
	var operationLocksMu sync.Mutex
	operationLocks := make(map[string]*sync.Mutex)
	lockWorld := func(world string) func() {
		operationLocksMu.Lock()
		lock, ok := operationLocks[world]
		if !ok {
			lock = &sync.Mutex{}
			operationLocks[world] = lock
		}
		operationLocksMu.Unlock()
		lock.Lock()
		return lock.Unlock
	}
	snapshotAllowed := func() map[string]struct{} {
		allowedMu.RLock()
		defer allowedMu.RUnlock()
		snapshot := make(map[string]struct{}, len(allowed))
		for world := range allowed {
			snapshot[world] = struct{}{}
		}
		return snapshot
	}
	return http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		if req.Method != "POST" || req.URL.Path != "/v1/jobs" {
			http.NotFound(w, req)
			return
		}
		var r Request
		// Refusals answer in the same shape as success. A plain-text body here was decoded as
		// JSON by the caller and surfaced to an operator as
		// "invalid character 'o' in literal false (expecting 'a')" - the word "forbidden" being
		// read as the literal false. The refusal was correct; only its wrapper was unreadable.
		refuse := func(status int, reason string) {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(status)
			json.NewEncoder(w).Encode(Response{Status: "failed", Error: reason})
		}
		if json.NewDecoder(http.MaxBytesReader(w, req.Body, 4096)).Decode(&r) != nil {
			refuse(400, "invalid request")
			return
		}
		currentAllowed := snapshotAllowed()
		if err := Verify(token, currentAllowed, r); err != nil {
			// The vocabulary and its argument rules are public - the portal serves them at
			// /api/agent/verbs - so an argument complaint is not a disclosure and is the
			// difference between a caller that can fix itself and one that cannot. Anything
			// touching the capability itself stays a bare "forbidden".
			reason := "forbidden"
			if argumentProblem(err) {
				reason = err.Error()
			}
			refuse(403, reason)
			return
		}
		unlock := func() {}
		if r.World != "" {
			unlock = lockWorld(r.World)
		}
		defer unlock()
		reply := execute(req.Context(), scriptDir, worldRoot, currentAllowed, r)
		if r.Operation == "provision" {
			marker := filepath.Join(worldRoot, r.World, ".portal-managed")
			if info, markerErr := os.Lstat(marker); markerErr == nil && info.Mode().IsRegular() {
				allowedMu.Lock()
				allowed[r.World] = struct{}{}
				allowedMu.Unlock()
			}
		}
		if r.Operation == "delete_server" && reply.Status == "succeeded" {
			allowedMu.Lock()
			delete(allowed, r.World)
			allowedMu.Unlock()
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(reply)
	}), nil
}

func execute(parent context.Context, scriptDir, worldRoot string, allowed map[string]struct{}, r Request) Response {
	if r.Operation == "world_catalog" {
		catalog, err := worldCatalog(parent, worldRoot, allowed)
		if err != nil {
			return Response{Status: "failed", Error: "world catalog unavailable"}
		}
		data, err := json.Marshal(catalog)
		if err != nil {
			return Response{Status: "failed", Error: "world catalog unavailable"}
		}
		return Response{Status: "succeeded", Data: data}
	}
	if r.Operation == "provision" {
		worldPath := filepath.Join(worldRoot, r.World)
		if !within(worldRoot, worldPath) {
			return Response{Status: "rejected", Error: "world escapes configured root"}
		}
		if _, err := os.Lstat(worldPath); !os.IsNotExist(err) {
			return Response{Status: "rejected", Error: "world already exists"}
		}
	} else {
		if _, ok := allowed[r.World]; !ok {
			return Response{Status: "rejected", Error: "world is not allowed"}
		}
		worldPath, err := filepath.EvalSymlinks(filepath.Join(worldRoot, r.World))
		if err != nil || !within(worldRoot, worldPath) {
			return Response{Status: "rejected", Error: "world escapes configured root"}
		}
		if info, err := os.Stat(worldPath); err != nil || !info.IsDir() {
			return Response{Status: "rejected", Error: "world unavailable"}
		}
	}
	if r.Operation == "world_analysis" {
		snapshot, err := analyzeWorldBackup(worldRoot, r.World)
		if err != nil {
			return Response{Status: "failed", Error: "world analysis unavailable", Output: Sanitize(err.Error())}
		}
		data, err := json.Marshal(snapshot)
		if err != nil || len(data) > 4<<20 {
			return Response{Status: "failed", Error: "world analysis exceeded response limit"}
		}
		return Response{Status: "succeeded", Data: data}
	}
	if r.Operation == "access_state" {
		state, err := accessState(worldRoot, r.World)
		if err != nil {
			return Response{Status: "failed", Error: "access lists unavailable", Output: Sanitize(err.Error())}
		}
		data, err := json.Marshal(state)
		if err != nil {
			return Response{Status: "failed", Error: "access lists unavailable"}
		}
		return Response{Status: "succeeded", Data: data}
	}
	sequence := []string{r.Operation}
	switch r.Operation {
	case "restart":
		sequence = []string{"backup", "stop", "start"}
	case "stop", "build":
		sequence = []string{"backup", r.Operation}
	case "restore":
		sequence = []string{"backup", "stop", "restore"}
	case "set_port":
		sequence = []string{"backup", "stop", "set_port", "start"}
	case "mod_deploy":
		sequence = []string{"backup", "stop", "mod_deploy", "start"}
	case "delete_server":
		sequence = []string{"backup", "stop", "delete_server"}
	case "provision":
		if r.Start {
			sequence = []string{"provision", "start", "health"}
		}
	}
	timeout := 10 * time.Minute
	if r.Operation == "provision" {
		timeout = 20 * time.Minute
	} else if r.Operation == "world_map" {
		timeout = 35 * time.Minute
	}
	ctx, cancel := context.WithTimeout(parent, timeout)
	defer cancel()
	var output strings.Builder
	provisioned := false
	ready := false
	for _, operation := range sequence {
		resolved, err := safeScript(scriptDir, operations[operation])
		if err != nil {
			return Response{Status: "failed", Output: Sanitize(output.String()), Error: "operation unavailable", Provisioned: provisioned, Ready: ready}
		}
		args := []string{r.World}
		switch {
		case operation == "restore":
			args = append(args, r.Backup)
		case operation == "set_port":
			args = append(args, fmt.Sprint(r.Port))
		case operation == "world_create":
			args = append(args, r.Seed)
		case operation == "access_apply":
			args = append(args, accessListArg(r.Admins), accessListArg(r.Permitted))
		case operation == "provision":
			args = append(args,
				r.ServerName, fmt.Sprint(r.Port), fmt.Sprint(r.Public), fmt.Sprint(r.Crossplay),
				fmt.Sprint(r.PlayerLimit), r.Preset, r.BackupInterval, fmt.Sprint(r.BackupAge),
				fmt.Sprint(r.BackupCount), r.Profile, r.Seed, r.SourceWorld, r.CopyFrom,
			)
		case operation == "world_log":
			args = append(args, fmt.Sprint(r.Lines), r.Query)
		case operation == "world_log_info":
			args = append(args, "info")
		case operation == "publish_profile":
			// The world is already args[0]; the script resolves the single catalog target and
			// carries the previous release's artifacts forward, so no paths come from here.
			args = append(args, r.Profile, r.ClientType, r.Notes)
		case strings.HasPrefix(operation, "mod_"):
			action := strings.ReplaceAll(strings.TrimPrefix(operation, "mod_"), "_", "-")
			args = append(args, r.Profile, action)
			switch operation {
			case "mod_search":
				args = append(args, r.Query)
			case "mod_add":
				args = append(args, r.Identifier, r.Version, r.Scope)
			case "mod_remove":
				args = append(args, r.Identifier, r.Reason)
			case "mod_enable", "mod_disable", "mod_custom_remove", "mod_custom_enable", "mod_custom_disable":
				args = append(args, r.Identifier)
			case "mod_custom_add":
				args = append(args, r.Identifier, r.Scope)
			case "mod_notes":
				args = append(args, fmt.Sprint(r.Lines))
			case "mod_update":
				args = append(args, r.Identifier)
			case "mod_release_confirm":
				args = append(args, r.PublishedProfile, r.ClientType, r.ReleaseID, r.Archive)
			}
		}
		cmd := exec.Command(resolved, args...)
		cmd.Dir = scriptDir
		if operation == "provision" {
			cmd.Env = append(os.Environ(), "PORTAL_SERVER_PASSWORD="+r.Password)
		}
		out, err := combinedOutput(ctx, cmd)
		output.WriteString(out)
		if err != nil {
			return Response{Status: "failed", Output: Sanitize(output.String()), Error: "operation failed", Provisioned: provisioned, Ready: ready}
		}
		if operation == "provision" {
			provisioned = true
		}
		if operation == "health" {
			ready = true
		}
	}
	if r.Operation == "mod_inventory" || r.Operation == "mod_search" || r.Operation == "mod_custom_list" || r.Operation == "profile_catalog" || r.Operation == "world_metadata" {
		raw := strings.TrimSpace(output.String())
		if len(raw) > 4<<20 || !json.Valid([]byte(raw)) {
			return Response{Status: "failed", Error: "operation returned invalid data"}
		}
		return Response{Status: "succeeded", Data: json.RawMessage(raw)}
	}
	return Response{Status: "succeeded", Output: Sanitize(output.String()), Provisioned: provisioned, Ready: ready}
}

func combinedOutput(ctx context.Context, cmd *exec.Cmd) (string, error) {
	var output bytes.Buffer
	cmd.Stdout = &output
	cmd.Stderr = &output
	useProcessGroup(cmd)
	if err := cmd.Start(); err != nil {
		return output.String(), err
	}
	done := make(chan error, 1)
	go func() {
		done <- cmd.Wait()
	}()
	select {
	case err := <-done:
		return output.String(), err
	case <-ctx.Done():
		// The wait below is not optional: cmd.Wait still owns output, so
		// returning before it finishes would race the buffer. Where the group
		// signal cannot be delivered, fall back to the child itself so that
		// wait is still guaranteed to end.
		if err := terminateProcessGroup(cmd); err != nil {
			_ = cmd.Process.Kill()
		}
		timer := time.NewTimer(30 * time.Second)
		defer timer.Stop()
		select {
		case <-done:
		case <-timer.C:
			if err := killProcessGroup(cmd); err != nil {
				_ = cmd.Process.Kill()
			}
			<-done
		}
		return output.String(), ctx.Err()
	}
}

func resolveBackupRoot(worldRoot string) (string, error) {
	backupRoot, err := filepath.EvalSymlinks(filepath.Join(worldRoot, "world_backups"))
	if err != nil {
		return "", err
	}
	if !within(worldRoot, backupRoot) {
		return "", errors.New("backup directory escapes configured root")
	}
	return backupRoot, nil
}

func analyzeWorldBackup(worldRoot, world string) (worldintel.Snapshot, error) {
	backupRoot, err := resolveBackupRoot(worldRoot)
	if err != nil {
		return worldintel.Snapshot{}, err
	}
	entries, err := os.ReadDir(backupRoot)
	if err != nil {
		return worldintel.Snapshot{}, err
	}
	prefix := "world-" + world + "-"
	type candidate struct {
		path string
		mod  time.Time
	}
	var backups []candidate
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasPrefix(entry.Name(), prefix) || !strings.HasSuffix(entry.Name(), ".tgz") {
			continue
		}
		info, statErr := entry.Info()
		if statErr == nil && info.Mode().IsRegular() {
			backups = append(backups, candidate{filepath.Join(backupRoot, entry.Name()), info.ModTime()})
		}
	}
	if len(backups) == 0 {
		return worldintel.Snapshot{}, errors.New("no completed world backup")
	}
	sort.Slice(backups, func(i, j int) bool {
		if backups[i].mod.Equal(backups[j].mod) {
			return backups[i].path > backups[j].path
		}
		return backups[i].mod.After(backups[j].mod)
	})
	worldPath, err := filepath.EvalSymlinks(filepath.Join(worldRoot, world))
	if err != nil || !within(worldRoot, worldPath) {
		return worldintel.Snapshot{}, errors.New("world unavailable")
	}
	// resources.assets and assembly_valheim.dll carry every vanilla prefab name, so they are listed
	// first and are never subject to the plugin budget below: without them nothing resolves at all.
	catalogPaths := []string{
		filepath.Join(worldPath, "data/server/valheim_server_Data/resources.assets"),
		filepath.Join(worldPath, "data/server/valheim_server_Data/Managed/assembly_valheim.dll"),
	}
	// The two plugin roots are mirrors of each other - measured on Hrafnheim, 118 DLLs each, 236
	// listings for 118 distinct mods - and the walk reads every file it lists. The old flat cap of 130
	// total paths spent its whole budget on the first root and then truncated 108 paths, and because
	// the cap counted duplicates rather than mods it was luck, not design, that no mod was lost:
	// walking the mirror first would have dropped real ones. Reading the mirror anyway cost real time
	// (34s capped versus 67s uncapped for a catalog identical to the byte, 1,780,660 entries).
	//
	// So the budget now counts distinct DLL names. 512 leaves better than four times today's headroom
	// while still bounding the walk; the real guard on work is CatalogFromFiles, which stops at 20,000
	// files or 1 GiB scanned whatever it is handed.
	const maxPluginDLLs = 512
	seen := make(map[string]struct{}, maxPluginDLLs)
	for _, root := range []string{filepath.Join(worldPath, "config_merged/bepinex/plugins"), filepath.Join(worldPath, "data/bepinex/BepInEx/plugins")} {
		_ = filepath.WalkDir(root, func(path string, entry fs.DirEntry, walkErr error) error {
			if walkErr != nil || len(seen) >= maxPluginDLLs {
				return nil
			}
			if entry.IsDir() || !strings.EqualFold(filepath.Ext(entry.Name()), ".dll") {
				return nil
			}
			name := strings.ToLower(entry.Name())
			if _, duplicate := seen[name]; duplicate {
				return nil
			}
			seen[name] = struct{}{}
			catalogPaths = append(catalogPaths, path)
			return nil
		})
	}
	return worldintel.AnalyzeArchive(backups[0].path, world, worldintel.CatalogFromFiles(catalogPaths...))
}

type worldCatalogEntry struct {
	Name   string `json:"name"`
	Port   int    `json:"port"`
	Status string `json:"status"`
}

func worldCatalog(parent context.Context, worldRoot string, allowed map[string]struct{}) ([]worldCatalogEntry, error) {
	names := make([]string, 0, len(allowed))
	for name := range allowed {
		names = append(names, name)
	}
	sort.Strings(names)
	catalog := make([]worldCatalogEntry, 0, len(names))
	for _, name := range names {
		worldPath, err := filepath.EvalSymlinks(filepath.Join(worldRoot, name))
		if err != nil || !within(worldRoot, worldPath) {
			continue
		}
		info, err := os.Stat(worldPath)
		if err != nil || !info.IsDir() {
			continue
		}
		port := 2456
		if value := envSetting(filepath.Join(worldPath, "valheim.env"), "SERVER_PORT"); value != "" {
			parsed, parseErr := strconv.Atoi(value)
			if parseErr != nil || parsed < 1024 || parsed > 65533 {
				continue
			}
			port = parsed
		}
		status := "offline"
		ctx, cancel := context.WithTimeout(parent, 5*time.Second)
		output, inspectErr := exec.CommandContext(ctx, "docker", "inspect", "-f", "{{.State.Running}}", "valheim-server-"+name).Output()
		cancel()
		if inspectErr == nil && strings.TrimSpace(string(output)) == "true" {
			status = "online"
		}
		catalog = append(catalog, worldCatalogEntry{Name: name, Port: port, Status: status})
	}
	return catalog, nil
}

// AccessState is what is actually in force on the host for one world: the two
// list files Valheim reads at runtime, and the env values the server image
// regenerates them from when the container next starts.
type AccessState struct {
	Admins       []string `json:"admins"`
	Permitted    []string `json:"permitted"`
	EnvAdmins    []string `json:"env_admins"`
	EnvPermitted []string `json:"env_permitted"`
	EnvPresent   bool     `json:"env_present"`
}

func accessState(worldRoot, world string) (AccessState, error) {
	worldPath, err := filepath.EvalSymlinks(filepath.Join(worldRoot, world))
	if err != nil || !within(worldRoot, worldPath) {
		return AccessState{}, errors.New("world escapes configured root")
	}
	configDir := filepath.Join(worldPath, "config_merged")
	admins, err := readAccessList(filepath.Join(configDir, "adminlist.txt"))
	if err != nil {
		return AccessState{}, err
	}
	permitted, err := readAccessList(filepath.Join(configDir, "permittedlist.txt"))
	if err != nil {
		return AccessState{}, err
	}
	envPath := filepath.Join(worldPath, "valheim.env")
	state := AccessState{Admins: admins, Permitted: permitted}
	if info, err := os.Lstat(envPath); err == nil && info.Mode().IsRegular() {
		state.EnvPresent = true
		state.EnvAdmins = splitEnvIDs(envSetting(envPath, "ADMINLIST_IDS"))
		state.EnvPermitted = splitEnvIDs(envSetting(envPath, "PERMITTEDLIST_IDS"))
	}
	return state, nil
}

// readAccessList returns the Steam IDs in a Valheim list file, ignoring the
// comment header. A missing file reads as an empty list: that is how a world
// that has never been synchronized legitimately looks.
func readAccessList(path string) ([]string, error) {
	data, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return []string{}, nil
	}
	if err != nil {
		return nil, errors.New("access list is unreadable")
	}
	if len(data) > 64<<10 {
		return nil, errors.New("access list is too large")
	}
	ids := []string{}
	for _, line := range strings.Split(string(data), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "//") {
			continue
		}
		if steamID.MatchString(line) {
			ids = append(ids, line)
		}
	}
	return ids, nil
}

func splitEnvIDs(value string) []string {
	ids := []string{}
	for _, field := range strings.FieldsFunc(value, func(r rune) bool { return r == ' ' || r == ',' || r == '\t' }) {
		if steamID.MatchString(field) {
			ids = append(ids, field)
		}
	}
	return ids
}

// accessListArg renders a list for argv, using "-" for empty so an empty list
// is never mistaken for a missing argument.
func accessListArg(list string) string {
	if list == "" {
		return "-"
	}
	return list
}

func envSetting(path, key string) string {
	data, err := os.ReadFile(path)
	if err != nil || len(data) > 64<<10 {
		return ""
	}
	prefix := key + "="
	for _, line := range strings.Split(string(data), "\n") {
		line = strings.TrimSpace(line)
		if !strings.HasPrefix(line, prefix) {
			continue
		}
		value := strings.TrimSpace(strings.TrimPrefix(line, prefix))
		if len(value) >= 2 && ((value[0] == '"' && value[len(value)-1] == '"') || (value[0] == '\'' && value[len(value)-1] == '\'')) {
			value = value[1 : len(value)-1]
		}
		return value
	}
	return ""
}

func safeScript(scriptDir, name string) (string, error) {
	resolved, err := filepath.EvalSymlinks(filepath.Join(scriptDir, name))
	if err != nil || filepath.Dir(resolved) != scriptDir {
		return "", errors.New("script escapes configured directory")
	}
	info, err := os.Stat(resolved)
	if err != nil || !info.Mode().IsRegular() || info.Mode()&0o111 == 0 {
		return "", errors.New("script is not executable")
	}
	return resolved, nil
}
func within(root, path string) bool {
	rel, err := filepath.Rel(root, path)
	return err == nil && rel != ".." && !strings.HasPrefix(rel, ".."+string(os.PathSeparator))
}

func Serve(c Config) error {
	h, err := NewHandler(c)
	if err != nil {
		return err
	}
	if err = os.Remove(c.Socket); err != nil && !os.IsNotExist(err) {
		return err
	}
	if err = os.MkdirAll(filepath.Dir(c.Socket), 0o750); err != nil {
		return err
	}
	l, err := net.Listen("unix", c.Socket)
	if err != nil {
		return err
	}
	defer l.Close()
	if err = os.Chmod(c.Socket, 0o660); err != nil {
		return err
	}
	return http.Serve(l, h)
}
func Sanitize(s string) string {
	lines := strings.Split(s, "\n")
	for i, line := range lines {
		lower := strings.ToLower(line)
		if strings.Contains(lower, "password") || strings.Contains(lower, "token") || strings.Contains(lower, "webhook") || strings.Contains(lower, "secret") || strings.Contains(line, "/media/") {
			lines[i] = "[redacted]"
		}
	}
	return strings.Join(lines, "\n")
}
