package app

import (
	"context"
	"encoding/json"
	"errors"
	"html/template"
	"net/http"
	"strings"
)

type modInventoryResponse struct {
	World            string                `json:"world"`
	Profile          string                `json:"profile"`
	Packages         []installedMod        `json:"packages"`
	DisabledPackages []installedMod        `json:"disabled_packages"`
	CustomPackages   []customPackageChoice `json:"custom_packages"`
	ExcludedPackages []excludedMod         `json:"excluded_packages"`
}

type installedMod struct {
	Identifier string `json:"identifier"`
	Version    string `json:"version"`
	Scope      string `json:"scope"`
	Enabled    bool   `json:"enabled"`
	Source     string `json:"source"`
}

type excludedMod struct {
	Identifier string `json:"identifier"`
	Version    string `json:"version"`
	Reason     string `json:"reason"`
}

type customPackageChoice struct {
	ID          string   `json:"id"`
	Filename    string   `json:"filename"`
	Size        int64    `json:"size"`
	SHA256      string   `json:"sha256"`
	Description string   `json:"description"`
	DLLs        []string `json:"dlls"`
	Scope       string   `json:"scope"`
	Enabled     bool     `json:"enabled"`
	Selected    bool     `json:"selected"`
}

type thunderstoreMod struct {
	Identifier   string   `json:"identifier"`
	Name         string   `json:"name"`
	Owner        string   `json:"owner"`
	Description  string   `json:"description"`
	Version      string   `json:"version"`
	Versions     []string `json:"versions"`
	Dependencies []string `json:"dependencies"`
	Categories   []string `json:"categories"`
	Icon         string   `json:"icon"`
	Website      string   `json:"website"`
	Downloads    int64    `json:"downloads"`
	Rating       int64    `json:"rating"`
	Deprecated   bool     `json:"deprecated"`
}

type modAdminPage struct {
	World         string
	Profile       string
	Query         string
	Searched      bool
	Inventory     modInventoryResponse
	CustomCatalog []customPackageChoice
	SearchResults []thunderstoreMod
	CSRF          string
}

func (s *Server) modAdmin(w http.ResponseWriter, r *http.Request) {
	world, profile := strings.TrimSpace(r.URL.Query().Get("world")), strings.TrimSpace(r.URL.Query().Get("profile"))
	if !validWorld(world) || !validWorld(profile) {
		http.Error(w, "valid world and profile are required", http.StatusBadRequest)
		return
	}
	page := modAdminPage{World: world, Profile: profile, CSRF: s.csrfCookie(w, r)}
	if err := s.readModData(r.Context(), world, ModAgentRequest{Operation: "mod_inventory", Profile: profile}, &page.Inventory); err != nil {
		http.Error(w, "mod inventory unavailable", http.StatusBadGateway)
		return
	}
	if err := s.readModData(r.Context(), world, ModAgentRequest{Operation: "mod_custom_list", Profile: profile}, &page.CustomCatalog); err != nil {
		http.Error(w, "custom package catalog unavailable", http.StatusBadGateway)
		return
	}
	page.Query = strings.TrimSpace(r.URL.Query().Get("q"))
	page.Searched = page.Query != ""
	if page.Searched {
		if len(page.Query) < 2 || len(page.Query) > 100 || strings.ContainsAny(page.Query, "\r\n\x00") {
			http.Error(w, "search must contain 2 to 100 characters", http.StatusBadRequest)
			return
		}
		if err := s.readModData(r.Context(), world, ModAgentRequest{Operation: "mod_search", Profile: profile, Query: page.Query}, &page.SearchResults); err != nil {
			http.Error(w, "Thunderstore search unavailable", http.StatusBadGateway)
			return
		}
	}
	render(w, modAdminTemplate, page)
}

func (s *Server) readModData(ctx context.Context, world string, request ModAgentRequest, destination any) error {
	reply, err := s.agent.RunMod(ctx, randomID(), world, request)
	if err != nil || reply.Status != "succeeded" || len(reply.Data) == 0 {
		return errors.New("mod agent query failed")
	}
	if err := json.Unmarshal(reply.Data, destination); err != nil {
		return err
	}
	return nil
}

func (s *Server) mutateMod(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, 32<<10)
	if err := r.ParseForm(); err != nil {
		http.Error(w, "invalid form", http.StatusBadRequest)
		return
	}
	world := strings.TrimSpace(r.FormValue("world"))
	profile := strings.TrimSpace(r.FormValue("profile"))
	action := strings.TrimSpace(r.FormValue("action"))
	identifier := strings.TrimSpace(r.FormValue("identifier"))
	version := strings.TrimSpace(r.FormValue("version"))
	scope := strings.TrimSpace(r.FormValue("scope"))
	reason := strings.TrimSpace(r.FormValue("reason"))
	if !validWorld(world) || !validWorld(profile) || !validModFormValue(identifier, 240) || !validModFormValue(version, 80) || !validModFormValue(reason, 200) {
		http.Error(w, "invalid mod request", http.StatusBadRequest)
		return
	}
	request := ModAgentRequest{Profile: profile, Identifier: identifier, Version: version, Scope: scope, Reason: reason}
	switch action {
	case "add":
		request.Operation = "mod_add"
		if identifier == "" || version == "" || (scope != "shared" && scope != "client-only") {
			http.Error(w, "package, version, and scope are required", http.StatusBadRequest)
			return
		}
	case "remove":
		request.Operation = "mod_remove"
		if identifier == "" || len(reason) < 3 {
			http.Error(w, "package and removal reason are required", http.StatusBadRequest)
			return
		}
	case "enable", "disable":
		request.Operation = "mod_" + action
		if identifier == "" {
			http.Error(w, "package is required", http.StatusBadRequest)
			return
		}
	case "custom-add":
		request.Operation = "mod_custom_add"
		if identifier == "" || (scope != "shared" && scope != "client-only") {
			http.Error(w, "custom package and scope are required", http.StatusBadRequest)
			return
		}
	case "custom-remove", "custom-enable", "custom-disable":
		request.Operation = "mod_" + strings.ReplaceAll(action, "-", "_")
		if identifier == "" {
			http.Error(w, "custom package is required", http.StatusBadRequest)
			return
		}
	default:
		http.Error(w, "unsupported mod action", http.StatusBadRequest)
		return
	}
	s.runModJob(w, r, world, profile, request)
}

func validModFormValue(value string, limit int) bool {
	return len(value) <= limit && !strings.ContainsAny(value, "\r\n\x00")
}

func (s *Server) deployMods(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, 16<<10)
	if err := r.ParseForm(); err != nil {
		http.Error(w, "invalid form", http.StatusBadRequest)
		return
	}
	world, profile := strings.TrimSpace(r.FormValue("world")), strings.TrimSpace(r.FormValue("profile"))
	if !validWorld(world) || !validWorld(profile) {
		http.Error(w, "invalid world or profile", http.StatusBadRequest)
		return
	}
	s.runModJob(w, r, world, profile, ModAgentRequest{Operation: "mod_deploy", Profile: profile})
}

func (s *Server) runModJob(w http.ResponseWriter, r *http.Request, world, profile string, request ModAgentRequest) {
	actor, jobID := r.Header.Get("X-Portal-Actor"), randomID()
	detail := profile
	if request.Identifier != "" {
		detail += ":" + request.Identifier
	}
	if err := s.store.CreateJob(r.Context(), Job{ID: jobID, World: world, Operation: request.Operation, Status: "queued", RequestedBy: actor, Detail: detail}, actor); err != nil {
		http.Error(w, "unable to queue mod operation", http.StatusInternalServerError)
		return
	}
	reply, err := s.agent.RunMod(r.Context(), jobID, world, request)
	if err != nil {
		_ = s.store.FinishJob(r.Context(), jobID, "failed", "agent request failed", actor)
		http.Error(w, "mod agent unavailable", http.StatusBadGateway)
		return
	}
	_ = s.store.FinishJob(r.Context(), jobID, reply.Status, reply.Output, actor)
	if reply.Status != "succeeded" {
		http.Error(w, "mod operation failed; see recent jobs", http.StatusConflict)
		return
	}
	http.Redirect(w, r, "/admin/mods?world="+template.URLQueryEscaper(world)+"&profile="+template.URLQueryEscaper(profile), http.StatusSeeOther)
}

const modAdminTemplate = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Mod administration</title><style>
:root{color-scheme:dark;--ink:#eef7f1;--muted:#afc4b5;--moss:#71c492;--line:#ffffff20;--panel:#153a2b;--danger:#b84b4b}*{box-sizing:border-box}body{margin:0;background:linear-gradient(145deg,#07140e,#123728);color:var(--ink);font:15px/1.5 system-ui,sans-serif}.shell{width:min(1240px,calc(100% - 2rem));margin:2rem auto}a{color:var(--moss)}section{background:#10281fcc;border:1px solid var(--line);border-radius:1rem;padding:1rem;margin:1rem 0}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:1rem}.card{background:var(--panel);border:1px solid var(--line);border-radius:.8rem;padding:1rem;overflow:hidden}.card header{display:flex;gap:.8rem;align-items:center}.icon{width:64px;height:64px;object-fit:cover;border-radius:.6rem;background:#0a2017}.muted{color:var(--muted)}form{display:flex;flex-wrap:wrap;gap:.6rem;align-items:end;margin:.7rem 0}label{display:grid;gap:.2rem}input,select,button{font:inherit;padding:.5rem;border-radius:.35rem;border:1px solid var(--line)}button{background:var(--moss);color:#07140e;font-weight:800;cursor:pointer}.danger{background:var(--danger);color:white}code{overflow-wrap:anywhere}details{margin:.5rem 0}.description{white-space:pre-wrap;max-height:12rem;overflow:auto}
</style></head><body><main class="shell"><p><a href="/admin">Back to administration</a></p><h1>Mods · {{.World}} / {{.Profile}}</h1><p>Changes update the immutable desired profile. They do not touch the running server until you explicitly deploy.</p>
<section><h2>Thunderstore search</h2><form method="get" action="/admin/mods"><input type="hidden" name="world" value="{{.World}}"><input type="hidden" name="profile" value="{{.Profile}}"><label>Find mods <input name="q" value="{{.Query}}" minlength="2" maxlength="100" required></label><button>Search Thunderstore</button></form>{{if .Searched}}<h3>Results</h3><div class="grid">{{range .SearchResults}}<article class="card"><header>{{if .Icon}}<img class="icon" src="{{.Icon}}" alt="">{{end}}<div><strong>{{.Name}}</strong><br><span class="muted">{{.Owner}} · {{.Downloads}} downloads · rating {{.Rating}}</span></div></header><p class="description">{{.Description}}</p><p>{{range .Categories}}<small>{{.}} </small>{{end}}</p><details><summary>Dependencies ({{len .Dependencies}})</summary><ul>{{range .Dependencies}}<li><code>{{.}}</code></li>{{else}}<li>None declared</li>{{end}}</ul></details><form method="post" action="/admin/mods/action"><input type="hidden" name="csrf" value="{{$.CSRF}}"><input type="hidden" name="world" value="{{$.World}}"><input type="hidden" name="profile" value="{{$.Profile}}"><input type="hidden" name="action" value="add"><input type="hidden" name="identifier" value="{{.Identifier}}"><label>Version <select name="version">{{range .Versions}}<option value="{{.}}">{{.}}</option>{{end}}</select></label><label>Scope <select name="scope"><option value="shared">Server and clients</option><option value="client-only">Clients only</option></select></label><button {{if .Deprecated}}class="danger"{{end}}>Select{{if .Deprecated}} deprecated package{{end}}</button></form>{{if .Website}}<a href="{{.Website}}" rel="noreferrer">Project page</a>{{end}}</article>{{else}}<p>No matching Thunderstore packages.</p>{{end}}</div>{{end}}</section>
<section><h2>Installed Thunderstore mods</h2><div class="grid">{{range .Inventory.Packages}}<article class="card"><strong>{{.Identifier}}</strong><p>{{.Version}} · {{.Scope}}</p><form method="post" action="/admin/mods/action"><input type="hidden" name="csrf" value="{{$.CSRF}}"><input type="hidden" name="world" value="{{$.World}}"><input type="hidden" name="profile" value="{{$.Profile}}"><input type="hidden" name="identifier" value="{{.Identifier}}"><button name="action" value="disable">Disable</button><label>Removal reason <input name="reason" minlength="3"></label><button class="danger" name="action" value="remove">Remove</button></form></article>{{else}}<p>No enabled Thunderstore mods.</p>{{end}}</div><h3>Disabled</h3>{{range .Inventory.DisabledPackages}}<article><code>{{.Identifier}}</code> {{.Version}} <form method="post" action="/admin/mods/action"><input type="hidden" name="csrf" value="{{$.CSRF}}"><input type="hidden" name="world" value="{{$.World}}"><input type="hidden" name="profile" value="{{$.Profile}}"><input type="hidden" name="identifier" value="{{.Identifier}}"><button name="action" value="enable">Enable</button><label>Removal reason <input name="reason" minlength="3"></label><button class="danger" name="action" value="remove">Remove</button></form></article>{{else}}<p>No disabled Thunderstore mods.</p>{{end}}</section>
<section><h2>Approved local custom packages</h2><p>These files are discovered separately from Thunderstore under the world's controlled custom-package directory. Their checksums are verified again during installation.</p><div class="grid">{{range .CustomCatalog}}<article class="card"><strong>{{.Filename}}</strong><p><code>{{.ID}}</code></p><p>{{.Size}} bytes · SHA-256 <code>{{.SHA256}}</code></p>{{if .Description}}<p class="description">{{.Description}}</p>{{end}}<details><summary>Detected DLLs ({{len .DLLs}})</summary><ul>{{range .DLLs}}<li><code>{{.}}</code></li>{{end}}</ul></details>{{if .Selected}}<p>Already selected · {{.Scope}} · {{if .Enabled}}enabled{{else}}disabled{{end}}</p>{{else}}<form method="post" action="/admin/mods/action"><input type="hidden" name="csrf" value="{{$.CSRF}}"><input type="hidden" name="world" value="{{$.World}}"><input type="hidden" name="profile" value="{{$.Profile}}"><input type="hidden" name="action" value="custom-add"><input type="hidden" name="identifier" value="{{.ID}}"><label>Scope <select name="scope"><option value="client-only">Clients only</option><option value="shared">Server and clients</option></select></label><button>Select custom package</button></form>{{end}}</article>{{else}}<p>No valid custom ZIP packages were found.</p>{{end}}</div><h3>Selected custom packages</h3>{{range .Inventory.CustomPackages}}<article><code>{{.ID}}</code> · {{.Scope}} · {{if .Enabled}}enabled{{else}}disabled{{end}}<form method="post" action="/admin/mods/action"><input type="hidden" name="csrf" value="{{$.CSRF}}"><input type="hidden" name="world" value="{{$.World}}"><input type="hidden" name="profile" value="{{$.Profile}}"><input type="hidden" name="identifier" value="{{.ID}}">{{if .Enabled}}<button name="action" value="custom-disable">Disable</button>{{else}}<button name="action" value="custom-enable">Enable</button>{{end}}<button class="danger" name="action" value="custom-remove">Remove</button></form></article>{{else}}<p>No custom packages selected.</p>{{end}}</section>
<section><h2>Deploy selected server mods</h2><p>This creates a world backup, stops the server, atomically replaces the deployed server plugin set, then starts the server.</p><form method="post" action="/admin/mods/deploy"><input type="hidden" name="csrf" value="{{.CSRF}}"><input type="hidden" name="world" value="{{.World}}"><input type="hidden" name="profile" value="{{.Profile}}"><button class="danger">Back up and deploy this profile</button></form></section>
</main></body></html>`
