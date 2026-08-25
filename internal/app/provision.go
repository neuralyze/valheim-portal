package app

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"time"
)

var serverNamePattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9 ._:-]{2,79}$`)
var serverPasswordPattern = regexp.MustCompile(`^[A-Za-z0-9!@#$%^&*._+?-]{5,64}$`)
var serverHostPattern = regexp.MustCompile(`^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$`)
var worldSeedPattern = regexp.MustCompile(`^[A-Za-z0-9]{1,64}$`)

type profileCatalogChoice struct {
	// Servers is how many servers run this profile. Not from the agent: counted per request.
	Servers          int    `json:"-"`
	World            string `json:"world"`
	Profile          string `json:"profile"`
	Name             string `json:"name"`
	Packages         int    `json:"packages"`
	CustomPackages   int    `json:"custom_packages"`
	DisabledPackages int    `json:"disabled_packages"`
	// Linked marks the profile this row's world runs today. It is the only per-world fact in a
	// row - profiles are shared, so every profile is offered to every world - and it is what
	// tells the admin site which servers a mod change reaches.
	Linked bool `json:"linked"`
}

type newServerPage struct {
	// Suggested is the profile the most servers already run; LinksRead is false when the
	// links could not be read, so the form says so rather than implying nothing runs.
	Suggested string
	LinksRead bool
	Worlds    []PublicWorld
	Profiles  []profileCatalogChoice
	Defaults  ProvisioningDefaults
	CSRF      string
}

type serverReviewPage struct {
	ID      string
	Pending provisionRequest
	Plan    []string
	CSRF    string
	// Confirmation is the exact phrase confirmServer will compare against, rendered from
	// the same function it uses. A page that showed a phrase the handler did not expect
	// would be an unfixable dead end for the operator.
	Confirmation string
	Password     string
}

// anyProvisionedWorld names a server whose agent can answer a profile question. The
// profile store is shared, so which one it is does not matter - only that it exists.
func (s *Server) anyProvisionedWorld(ctx context.Context) (string, error) {
	worlds, err := s.store.PublicWorlds(ctx)
	if err != nil {
		return "", err
	}
	if len(worlds) == 0 {
		return "", errors.New("no provisioned world")
	}
	return worlds[0].Name, nil
}

func (s *Server) newServer(w http.ResponseWriter, r *http.Request) {
	worlds, err := s.store.PublicWorlds(r.Context())
	if err != nil {
		http.Error(w, "world catalog unavailable", http.StatusServiceUnavailable)
		return
	}
	// One query. Every world's agent lists the same shared store, so asking each of them
	// would render the same profile once per world and read as several different profiles.
	var profiles []profileCatalogChoice
	for _, world := range worlds {
		reply, queryErr := s.agent.Run(r.Context(), randomID(), world.Name, "profile_catalog")
		if queryErr != nil || reply.Status != "succeeded" {
			continue
		}
		var choices []profileCatalogChoice
		if json.Unmarshal(reply.Data, &choices) == nil {
			profiles = choices
		}
		break
	}
	// Which profiles servers actually RUN, and how many run each. A profile is used in two
	// unrelated roles - the mod set a server runs, and the source a published client edition
	// is built from - and only the first is what this form chooses. `flat` and `vr` exist
	// solely as edition sources: linking a server to one would strip the admin tools that
	// have to be server-side. Showing the count makes the difference visible instead of
	// leaving three equal-looking names.
	linkage := s.profileLinks(r.Context())
	running := make(map[string]int, len(profiles))
	for _, profile := range linkage.byWorld {
		running[profile]++
	}
	suggested := ""
	for index := range profiles {
		profiles[index].Servers = running[profiles[index].Profile]
		if profiles[index].Servers > running[suggested] {
			suggested = profiles[index].Profile
		}
	}
	render(w, newServerTemplate, newServerPage{
		Worlds: worlds, Profiles: profiles, Suggested: suggested,
		LinksRead: linkage.Read, Defaults: s.cfg.Provisioning, CSRF: s.csrfCookie(w, r),
	})
}

func (s *Server) reviewServer(w http.ResponseWriter, r *http.Request) {
	// The admin upload guard already capped this body and parsed it, spilling anything
	// past maxUploadMemoryBytes to a temporary file. Re-parsing here would be a
	// documented no-op and installing a second ceiling would be too late to matter.
	if err := r.ParseForm(); err != nil {
		http.Error(w, "invalid form", http.StatusBadRequest)
		return
	}
	defer func() {
		if r.MultipartForm != nil {
			_ = r.MultipartForm.RemoveAll()
		}
	}()
	world := strings.TrimSpace(r.FormValue("world"))
	serverName := strings.TrimSpace(r.FormValue("server_name"))
	password := r.FormValue("password")
	if password != r.FormValue("password_confirm") {
		http.Error(w, "password confirmation does not match", http.StatusBadRequest)
		return
	}
	port, ok := boundedFormInt(r, "port", 1024, 65533)
	playerLimit, playersOK := boundedFormInt(r, "player_limit", 1, 100)
	backupAge, ageOK := boundedFormInt(r, "backup_age", 1, 365)
	backupCount, countOK := boundedFormInt(r, "backup_count", 1, 1000)
	profile := strings.TrimSpace(r.FormValue("profile"))
	preset := strings.TrimSpace(r.FormValue("preset"))
	backupInterval := strings.TrimSpace(r.FormValue("backup_interval"))
	joinHost := strings.TrimSpace(r.FormValue("join_host"))
	if !validWorld(world) || !validWorld(profile) || !serverNamePattern.MatchString(serverName) || !serverPasswordPattern.MatchString(password) ||
		!ok || !playersOK || !ageOK || !countOK || !validJoinHost(joinHost) {
		http.Error(w, "invalid server identity, password, address, or numeric setting", http.StatusBadRequest)
		return
	}
	if _, err := s.store.PublicWorld(r.Context(), world); err == nil {
		http.Error(w, "world already exists", http.StatusConflict)
		return
	} else if err != sql.ErrNoRows {
		http.Error(w, "unable to verify world identity", http.StatusServiceUnavailable)
		return
	}
	request := ProvisionAgentRequest{
		ServerName: serverName, Password: password, Port: port, Public: r.FormValue("public") == "true",
		Crossplay: r.FormValue("crossplay") == "true", PlayerLimit: playerLimit, Preset: preset,
		BackupInterval: backupInterval, BackupAge: backupAge, BackupCount: backupCount, Profile: profile,
		Start: r.FormValue("start") == "true",
	}
	// One switch, four exclusive sources, and every field belongs to exactly one of them.
	// A value supplied for a mode that is not selected is refused rather than dropped:
	// silently ignoring a typed seed hands the operator a different world than the one
	// they asked for and nothing in the resulting server records that it happened.
	mode := r.FormValue("world_mode")
	seed := strings.TrimSpace(r.FormValue("seed"))
	sourceWorld := strings.TrimSpace(r.FormValue("source_world"))
	upload, uploadHeader, uploadErr := r.FormFile("world_archive")
	if upload != nil {
		defer upload.Close()
	}
	uploaded := uploadErr == nil && uploadHeader != nil && uploadHeader.Size > 0
	for _, unused := range []struct {
		mode, field string
		supplied    bool
	}{
		{"seed", "a seed", seed != ""},
		{"import", "a source world", sourceWorld != ""},
		{"upload", "a world archive", uploaded},
	} {
		if unused.supplied && mode != unused.mode {
			http.Error(w, "the form supplied "+unused.field+", which belongs to a world source that is not selected; "+
				"choose that source or clear the field", http.StatusBadRequest)
			return
		}
	}
	var staged worldSaveUpload
	switch mode {
	case "random":
	case "seed":
		request.Seed = seed
		if !worldSeedPattern.MatchString(request.Seed) {
			http.Error(w, "seed must contain 1 to 64 letters or digits", http.StatusBadRequest)
			return
		}
	case "import":
		request.SourceWorld = sourceWorld
		if !validWorld(request.SourceWorld) || request.SourceWorld == world {
			http.Error(w, "select a different existing source world", http.StatusBadRequest)
			return
		}
	case "upload":
		if !uploaded {
			http.Error(w, "choose a .zip holding one world's .db and .fwl save pair", http.StatusBadRequest)
			return
		}
		id, result, err := s.stageUploadedWorld(upload, uploadHeader.Size)
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		request.WorldUpload, staged = id, result
	default:
		http.Error(w, "select a world source", http.StatusBadRequest)
		return
	}
	copyFrom := strings.TrimSpace(r.FormValue("template"))
	if copyFrom != "" {
		if !validWorld(copyFrom) {
			http.Error(w, "select a profile to copy", http.StatusBadRequest)
			return
		}
		request.CopyFrom = copyFrom
	}
	publish := r.FormValue("publish") == "true"
	if publish && !request.Start {
		http.Error(w, "a server can only be published after a successful readiness check", http.StatusBadRequest)
		return
	}
	var packages []installedMod
	if request.CopyFrom != "" {
		// Profiles are shared, so every server's agent reads the same store and any of
		// them answers this. The world being provisioned cannot: it does not exist yet.
		host, err := s.anyProvisionedWorld(r.Context())
		if err != nil {
			http.Error(w, "no server is available to read the profile", http.StatusBadGateway)
			return
		}
		var inventory modInventoryResponse
		if err := s.readModData(r.Context(), host, ModAgentRequest{Operation: "mod_inventory", Profile: request.CopyFrom}, &inventory); err != nil {
			http.Error(w, "the profile to copy is unavailable", http.StatusBadGateway)
			return
		}
		packages = append(packages, inventory.Packages...)
		packages = append(packages, inventory.DisabledPackages...)
	}
	pending := provisionRequest{
		Actor: r.Header.Get("X-Portal-Actor"), World: world, JoinHost: joinHost, Publish: publish,
		Request: request, Packages: packages, Upload: staged, ExpiresAt: time.Now().Add(10 * time.Minute),
	}
	id := randomID()
	s.provisionMu.Lock()
	for key, item := range s.provisions {
		if time.Now().After(item.ExpiresAt) {
			delete(s.provisions, key)
		}
	}
	s.provisions[id] = pending
	s.provisionMu.Unlock()
	plan := []string{
		"Reserve UDP ports " + strconv.Itoa(port) + "–" + strconv.Itoa(port+2) + " and unique auxiliary ports under a host lock.",
		"Create the world, environment, mod profile, and save metadata in a staging directory; rename it into place only after every file validates.",
		"Keep the new public-world record disabled until creation completes.",
	}
	switch {
	case request.WorldUpload != "":
		plan = append(plan,
			"Place the uploaded "+staged.UploadedWorld+" save pair ("+strconv.FormatInt(staged.DatabaseBytes, 10)+
				" byte database, seed "+staged.Seed+") and rewrite only its world name to "+world+
				", preserving the seed, UID and generator version.",
			"Refuse the whole creation if a world directory named "+world+" already exists on the host; an upload never replaces a save.",
		)
	case request.SourceWorld != "":
		plan = append(plan, "Copy the existing "+request.SourceWorld+" save pair and rewrite only its world name while preserving seed and UID metadata.")
	case request.Seed != "":
		plan = append(plan, "Generate current-format FWL metadata for seed "+request.Seed+"; Valheim creates the database on first start.")
	default:
		plan = append(plan, "Let Valheim generate a random seed and save database on first start.")
	}
	if playerLimit != 10 {
		plan = append(plan, "Install the server-only Azumatt-MaxPlayerCount package and pin its configuration to "+strconv.Itoa(playerLimit)+" players.")
	}
	if request.Start {
		plan = append(plan,
			"Start the container and require the official “Game server connected” readiness signal within 10 minutes.",
			"Create the first complete backup, analyze it, and publish the 12288 world map before server creation completes.",
		)
	} else {
		plan = append(plan, "Defer automatic map generation until the server is first started successfully from the portal.")
	}
	if publish {
		plan = append(plan, "Publish the connection card only after readiness succeeds.")
	}
	render(w, serverReviewTemplate, serverReviewPage{ID: id, Pending: pending, Plan: plan, CSRF: s.csrfCookie(w, r), Confirmation: provisionConfirmation(pending), Password: fmt.Sprintf("set (%d characters; never stored in the portal database)", len(password))})
}

func boundedFormInt(r *http.Request, name string, minimum, maximum int) (int, bool) {
	value, err := strconv.Atoi(strings.TrimSpace(r.FormValue(name)))
	return value, err == nil && value >= minimum && value <= maximum
}

func validJoinHost(host string) bool {
	if len(host) > 253 || strings.ContainsAny(host, "/:@?#[]\r\n\x00") {
		return false
	}
	return net.ParseIP(host) != nil || serverHostPattern.MatchString(host)
}

func (s *Server) pendingProvision(id, actor string, consume bool) (provisionRequest, bool) {
	s.provisionMu.Lock()
	defer s.provisionMu.Unlock()
	pending, ok := s.provisions[id]
	if !ok || pending.Actor != actor || time.Now().After(pending.ExpiresAt) {
		delete(s.provisions, id)
		return provisionRequest{}, false
	}
	if consume {
		delete(s.provisions, id)
	}
	return pending, true
}

// provisionConfirmation is the phrase the operator retypes. An upload names the world the
// archive actually carries as well as the server being created: the review page is the
// only place the two are shown together, and confirming a page prepared for a different
// archive has to be impossible rather than merely unlikely.
func provisionConfirmation(pending provisionRequest) string {
	if pending.Request.WorldUpload != "" && pending.Upload.UploadedWorld != "" {
		return "CREATE " + pending.World + " FROM " + pending.Upload.UploadedWorld
	}
	return "CREATE " + pending.World
}

func (s *Server) confirmServer(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, 8<<10)
	if err := r.ParseForm(); err != nil {
		http.Error(w, "invalid form", http.StatusBadRequest)
		return
	}
	actor := r.Header.Get("X-Portal-Actor")
	pending, ok := s.pendingProvision(r.PathValue("id"), actor, false)
	if !ok {
		http.NotFound(w, r)
		return
	}
	if r.FormValue("confirmation") != provisionConfirmation(pending) {
		http.Error(w, "confirmation does not match", http.StatusBadRequest)
		return
	}
	pending, ok = s.pendingProvision(r.PathValue("id"), actor, true)
	if !ok {
		http.Error(w, "creation request expired", http.StatusConflict)
		return
	}
	jobID := randomID()
	if err := s.store.CreateJob(r.Context(), Job{ID: jobID, World: pending.World, Operation: "provision", Status: "queued", RequestedBy: actor}, actor); err != nil {
		http.Error(w, "unable to queue server creation", http.StatusInternalServerError)
		return
	}
	reply, err := s.agent.RunProvision(r.Context(), jobID, pending.World, pending.Request)
	if err != nil {
		_ = s.store.FinishJob(r.Context(), jobID, "failed", "agent request failed", actor)
		http.Error(w, "provisioning agent unavailable", http.StatusBadGateway)
		return
	}
	_ = s.store.FinishJob(r.Context(), jobID, reply.Status, reply.Output, actor)
	if reply.Provisioned {
		status := "offline"
		if reply.Ready {
			status = "online"
		}
		world := PublicWorld{Name: pending.World, JoinAddress: net.JoinHostPort(pending.JoinHost, strconv.Itoa(pending.Request.Port)), Status: status, ServerVersion: "unknown"}
		if err := s.store.CreateProvisionedWorld(r.Context(), world, actor); err != nil {
			http.Error(w, "server files were created but the portal record failed; inspect the audit log", http.StatusInternalServerError)
			return
		}
		if reply.Ready {
			if failure := s.ensureInitialWorldMap(r.Context(), pending.World, actor); failure != nil {
				http.Error(w, "server is ready but automatic map generation failed: "+failure.client, failure.status)
				return
			}
		}
		if pending.Publish && reply.Ready {
			if err := s.store.SetPublicWorldEnabled(r.Context(), pending.World, true, actor); err != nil {
				http.Error(w, "server is ready but publication failed", http.StatusInternalServerError)
				return
			}
		}
	}
	if reply.Status != "succeeded" {
		http.Error(w, "server creation did not complete; the recoverable draft and job output were retained", http.StatusConflict)
		return
	}
	http.Redirect(w, r, "/admin", http.StatusSeeOther)
}

const newServerTemplate = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Create Valheim server</title><style>body{font:16px/1.5 system-ui,sans-serif;max-width:1000px;margin:2rem auto;padding:0 1rem;color:#173321}fieldset{border:1px solid #b9cdbf;border-radius:.6rem;margin:1rem 0;padding:1rem}label{display:grid;gap:.25rem;margin:.6rem 0;max-width:34rem}input,select,button{font:inherit;padding:.55rem}button{background:#285c35;color:white;border:0;border-radius:.3rem;font-weight:700}small{color:#526d5b}</style></head><body><p><a href="/admin">Back to administration</a></p><h1>Create a Valheim server</h1><p>The wizard creates a disabled, recoverable server definition. Nothing is published until readiness succeeds and you explicitly request publication.</p><form method="post" action="/admin/servers/review" enctype="multipart/form-data"><input type="hidden" name="csrf" value="{{.CSRF}}">
<fieldset><legend>Identity</legend><label>Immutable world slug <input name="world" required pattern="[A-Za-z0-9][A-Za-z0-9._-]{0,79}"></label><label>Server display name <input name="server_name" required maxlength="80" placeholder="Neuralyze Valheim: New World"></label><label>Server password <input type="password" name="password" required minlength="5" maxlength="64" autocomplete="new-password"></label><label>Confirm password <input type="password" name="password_confirm" required minlength="5" maxlength="64" autocomplete="new-password"></label><small>The password is transmitted only to the local privileged agent and written to the world-owned environment file. It is never stored in the portal database or review token.</small></fieldset>
<fieldset id="world-source"><legend>World source</legend><p>Pick exactly one. The fields belonging to the sources you did not pick are disabled and are not submitted; supplying one anyway is refused rather than ignored.</p>
<label><input type="radio" name="world_mode" value="random" checked> Generate a new world with a random seed on first start</label>
<label data-world-mode="seed"><input type="radio" name="world_mode" value="seed"> Generate a new world from a seed <input name="seed" maxlength="64" pattern="[A-Za-z0-9]{1,64}" placeholder="1 to 64 letters or digits" disabled></label>
<label data-world-mode="import"><input type="radio" name="world_mode" value="import"> Copy a world already on this host <select name="source_world" disabled><option value="">Select source</option>{{range .Worlds}}<option value="{{.Name}}">{{.Name}}</option>{{end}}</select></label>
<label data-world-mode="upload"><input type="radio" name="world_mode" value="upload"> Upload an existing world <input type="file" name="world_archive" accept=".zip,application/zip" disabled></label>
<small>The upload is one <code>.zip</code> holding a single world's <code>&lt;Name&gt;.db</code> and <code>&lt;Name&gt;.fwl</code> pair, up to 512 MiB. Valheim's own <code>.old</code> and <code>_backup_auto-</code> copies may be in the archive; they are listed on the next page and never used as the live save. The world name inside the <code>.fwl</code> is rewritten to this server's slug, preserving the seed, UID and generator version.</small>
<label>Gameplay preset <select name="preset"><option>Normal</option><option>Casual</option><option>Easy</option><option>Hard</option><option>Hardcore</option><option>Immersive</option><option>Hammer</option></select></label></fieldset>
<fieldset><legend>Network and gameplay</legend><label>Public join hostname <input name="join_host" required value="{{.Defaults.JoinHost}}"></label><label>Game base port <input type="number" name="port" value="{{.Defaults.GamePort}}" min="1024" max="65533" required></label><label>Player limit <input type="number" name="player_limit" value="{{.Defaults.PlayerLimit}}" min="1" max="100" required></label><small>Limits other than vanilla 10 install and pin the server-only MaxPlayerCount dependency.</small><label><input type="checkbox" name="public" value="true" checked> List in Valheim's server browser</label><label><input type="checkbox" name="crossplay" value="true"> Enable crossplay / PlayFab relay</label></fieldset>
<fieldset><legend>Mods</legend><label>Profile this server runs <input name="profile" list="server-profiles" value="{{if .Suggested}}{{.Suggested}}{{else}}default{{end}}" required pattern="[A-Za-z0-9][A-Za-z0-9._-]{0,79}"></label><p>Name a profile that already exists and this server runs it. Name a new one and it is created, empty or copied from the profile selected below. {{if .LinksRead}}The counts below are how many servers already run each profile: a profile no server runs is normally an edition source, not a server's mod set.{{else}}The portal could not read which profiles the existing servers run, so the counts below are unavailable.{{end}}</p><datalist id="server-profiles">{{range .Profiles}}<option value="{{.Profile}}" label="{{.Packages}} packages · {{if .Servers}}{{.Servers}} server(s){{else}}no servers{{end}}"></option>{{end}}</datalist><label>Copy from profile <select name="template"><option value="">Empty profile</option>{{range .Profiles}}<option value="{{.Profile}}">{{.Name}} ({{.Packages}} Thunderstore, {{.CustomPackages}} custom, {{.DisabledPackages}} disabled, {{if .Servers}}{{.Servers}} server(s){{else}}no servers{{end}})</option>{{end}}</select></label></fieldset>
<fieldset><legend>Backups and launch</legend><label>Backup schedule <select name="backup_interval"><option value="30m"{{if eq .Defaults.BackupInterval "30m"}} selected{{end}}>Every 30 minutes</option><option value="1h"{{if eq .Defaults.BackupInterval "1h"}} selected{{end}}>Hourly</option><option value="6h"{{if eq .Defaults.BackupInterval "6h"}} selected{{end}}>Every 6 hours</option><option value="daily"{{if eq .Defaults.BackupInterval "daily"}} selected{{end}}>Daily</option></select></label><label>Retention age in days <input type="number" name="backup_age" value="{{.Defaults.BackupAge}}" min="1" max="365" required></label><label>Maximum backup count <input type="number" name="backup_count" value="{{.Defaults.BackupCount}}" min="1" max="1000" required></label><label><input type="checkbox" name="start" value="true"> Start after transactional creation and wait for readiness</label><label><input type="checkbox" name="publish" value="true"> Publish on the player site after readiness succeeds</label></fieldset><button>Review exact plan</button></form>
<script src="/assets/new-server-world-source.js"></script></body></html>`

const serverReviewTemplate = `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Review server creation</title><style>body{font:16px/1.5 system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem;color:#173321}section{border:1px solid #b9cdbf;border-radius:.6rem;padding:1rem;margin:1rem 0}button{font:inherit;padding:.6rem;background:#9d3030;color:#fff;border:0;border-radius:.3rem;font-weight:700}input{font:inherit;padding:.5rem}code{word-break:break-all}</style></head><body><p><a href="/admin/servers/new">Discard and restart</a></p><h1>Review server creation</h1><section><h2>{{.Pending.World}}</h2><dl><dt>Display name</dt><dd>{{.Pending.Request.ServerName}}</dd><dt>Join address</dt><dd>{{.Pending.JoinHost}}:{{.Pending.Request.Port}}</dd><dt>Password</dt><dd>{{.Password}}</dd><dt>Profile</dt><dd>{{.Pending.Request.Profile}}</dd><dt>World source</dt><dd>{{if .Pending.Request.WorldUpload}}Uploaded archive carrying world <b>{{.Pending.Upload.UploadedWorld}}</b>, seed <code>{{.Pending.Upload.Seed}}</code>: {{.Pending.Upload.DatabaseBytes}} byte database and {{.Pending.Upload.MetadataBytes}} byte world metadata. It will be placed as <code>{{.Pending.World}}.db</code> and <code>{{.Pending.World}}.fwl</code> with the name inside the metadata rewritten to {{.Pending.World}}.{{else if .Pending.Request.SourceWorld}}Copy of the existing {{.Pending.Request.SourceWorld}} save.{{else if .Pending.Request.Seed}}A new world generated from seed <code>{{.Pending.Request.Seed}}</code>.{{else}}A new world on a random seed.{{end}}</dd><dt>Visibility</dt><dd>Valheim public={{.Pending.Request.Public}}, crossplay={{.Pending.Request.Crossplay}}, publish after readiness={{.Pending.Publish}}</dd><dt>Players</dt><dd>{{.Pending.Request.PlayerLimit}}</dd><dt>Backups</dt><dd>{{.Pending.Request.BackupInterval}}, {{.Pending.Request.BackupAge}} days, max {{.Pending.Request.BackupCount}}</dd></dl></section><section><h2>Filesystem and container plan</h2><ol>{{range .Plan}}<li>{{.}}</li>{{end}}</ol></section>{{if .Pending.Upload.Ignored}}<section><h2>Archive members not used</h2><p>These were in the archive and are not part of the live save pair. Valheim writes <code>.old</code> and <code>_backup_auto-</code> copies beside the world it is playing; they are never placed, so an automatic backup cannot be mistaken for the current world.</p><ul>{{range .Pending.Upload.Ignored}}<li><code>{{.}}</code></li>{{end}}</ul></section>{{end}}<section><h2>Approved profile packages</h2><ul>{{range .Pending.Packages}}<li><code>{{.Identifier}}@{{.Version}}</code> · {{.Scope}} · {{if .Enabled}}enabled{{else}}disabled{{end}}</li>{{else}}<li>Clean profile; no selected Thunderstore dependencies.</li>{{end}}</ul></section><section><h2>Typed confirmation</h2><form method="post" action="/admin/servers/{{.ID}}"><input type="hidden" name="csrf" value="{{.CSRF}}"><label>Type <code>{{.Confirmation}}</code> <input name="confirmation" required autocomplete="off"></label><button>Create server</button></form></section></body></html>`
