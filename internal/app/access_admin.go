package app

import (
	"database/sql"
	"encoding/json"
	"errors"
	"net/http"
	"slices"
	"strconv"
)

// Access lists are generated, never hand-maintained: the portal owns who is a
// member and who is an admin, and applying pushes both lists to the host. The
// admin page reports pending work by comparing the intended lists against the
// ones last applied, so a grant, revoke, role change, or enforcement toggle is
// visibly unapplied until an operator applies it.

func (s *Server) setWorldMemberRole(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, 16<<10)
	if err := r.ParseForm(); err != nil {
		http.Error(w, "invalid form", http.StatusBadRequest)
		return
	}
	if err := s.store.SetWorldMemberRole(r.Context(), r.FormValue("world"), r.FormValue("steam_id"), r.FormValue("role"), r.Header.Get("X-Portal-Actor")); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	http.Redirect(w, r, "/admin#players", http.StatusSeeOther)
}

func (s *Server) setPermittedEnforcement(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseForm(); err != nil {
		http.Error(w, "invalid form", http.StatusBadRequest)
		return
	}
	world := r.PathValue("world")
	enforce, err := strconv.ParseBool(r.FormValue("enforce"))
	if !validWorld(world) || err != nil {
		http.Error(w, "invalid permitted list state", http.StatusBadRequest)
		return
	}
	if err := s.store.SetPermittedEnforcement(r.Context(), world, enforce, r.Header.Get("X-Portal-Actor")); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			http.NotFound(w, r)
			return
		}
		http.Error(w, "unable to update the permitted list", http.StatusInternalServerError)
		return
	}
	http.Redirect(w, r, "/admin#players", http.StatusSeeOther)
}

func (s *Server) applyWorldAccess(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseForm(); err != nil {
		http.Error(w, "invalid form", http.StatusBadRequest)
		return
	}
	world := r.PathValue("world")
	if !validWorld(world) {
		http.NotFound(w, r)
		return
	}
	plan, err := s.store.WorldAccessPlanFor(r.Context(), world)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	if failure := s.pushAccessLists(r, plan); failure != nil {
		http.Error(w, failure.client, failure.status)
		return
	}
	http.Redirect(w, r, "/admin#players", http.StatusSeeOther)
}

func (s *Server) applyAllWorldAccess(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseForm(); err != nil {
		http.Error(w, "invalid form", http.StatusBadRequest)
		return
	}
	plans, err := s.store.WorldAccessPlans(r.Context())
	if err != nil {
		http.Error(w, "unavailable", http.StatusServiceUnavailable)
		return
	}
	for _, plan := range plans {
		if plan.InSync() {
			continue
		}
		if failure := s.pushAccessLists(r, plan); failure != nil {
			http.Error(w, plan.World+": "+failure.client, failure.status)
			return
		}
	}
	http.Redirect(w, r, "/admin#players", http.StatusSeeOther)
}

// pushAccessLists writes one world's lists through the agent and records what
// was applied. The job row keeps the script output next to every other
// privileged operation.
func (s *Server) pushAccessLists(r *http.Request, plan WorldAccessPlan) *worldAnalysisFailure {
	actor := r.Header.Get("X-Portal-Actor")
	id := randomID()
	if err := s.store.CreateJob(r.Context(), Job{ID: id, World: plan.World, Operation: "access_apply", Status: "queued", RequestedBy: actor}, actor); err != nil {
		return &worldAnalysisFailure{status: http.StatusInternalServerError, client: "unable to queue the access list update"}
	}
	reply, err := s.agent.RunAccessApply(r.Context(), id, plan.World, plan.Admins, plan.Permitted)
	if err != nil {
		s.store.FinishJob(r.Context(), id, "failed", "agent request failed", actor)
		return &worldAnalysisFailure{status: http.StatusBadGateway, client: "agent unavailable"}
	}
	s.store.FinishJob(r.Context(), id, reply.Status, reply.Output, actor)
	if reply.Status != "succeeded" {
		return &worldAnalysisFailure{status: http.StatusBadGateway, client: "the host rejected the access lists"}
	}
	if err := s.store.RecordAccessApplied(r.Context(), plan.World, plan.Admins, plan.Permitted, actor); err != nil {
		return &worldAnalysisFailure{status: http.StatusInternalServerError, client: "access lists applied but the record failed"}
	}
	return nil
}

// accessReport is one world's three-way comparison: what the portal intends,
// what it last applied, and what the host actually has in place.
type accessReport struct {
	World            string
	EnforcePermitted bool
	Intended         WorldAccessPlan
	Live             AccessState
	Reachable        bool
	Error            string
}

// FileDrift reports that the live list files disagree with the portal's intent,
// which is what Valheim enforces right now.
func (r accessReport) FileDrift() bool {
	return r.Reachable && !(slices.Equal(r.Intended.Admins, r.Live.Admins) && slices.Equal(r.Intended.Permitted, r.Live.Permitted))
}

// EnvDrift reports that the env the container regenerates its lists from
// disagrees with the portal's intent, which is what a container recreate would
// restore.
func (r accessReport) EnvDrift() bool {
	return r.Reachable && !(slices.Equal(r.Intended.Admins, r.Live.EnvAdmins) && slices.Equal(r.Intended.Permitted, r.Live.EnvPermitted))
}

func (r accessReport) InSync() bool { return r.Reachable && !r.FileDrift() && !r.EnvDrift() }

// AccessState mirrors the agent's read-back of one world's live access lists.
type AccessState struct {
	Admins       []string `json:"admins"`
	Permitted    []string `json:"permitted"`
	EnvAdmins    []string `json:"env_admins"`
	EnvPermitted []string `json:"env_permitted"`
	EnvPresent   bool     `json:"env_present"`
}

func (s *Server) verifyWorldAccess(w http.ResponseWriter, r *http.Request) {
	plans, err := s.store.WorldAccessPlans(r.Context())
	if err != nil {
		http.Error(w, "unavailable", http.StatusServiceUnavailable)
		return
	}
	reports := make([]accessReport, 0, len(plans))
	for _, plan := range plans {
		report := accessReport{World: plan.World, EnforcePermitted: plan.EnforcePermitted, Intended: plan}
		reply, err := s.agent.RunAccessState(r.Context(), randomID(), plan.World)
		switch {
		case err != nil:
			report.Error = "agent unavailable"
		case reply.Status != "succeeded":
			report.Error = firstNonEmpty(reply.Error, "the host could not read its access lists")
		default:
			if err := json.Unmarshal(reply.Data, &report.Live); err != nil {
				report.Error = "the host returned unreadable access lists"
			} else {
				report.Reachable = true
			}
		}
		reports = append(reports, report)
	}
	csrf := s.csrfCookie(w, r)
	render(w, accessTemplate, map[string]any{"Reports": reports, "CSRF": csrf})
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if value != "" {
			return value
		}
	}
	return ""
}

const accessTemplate = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Access lists</title><style>
body{font:16px system-ui,sans-serif;max-width:1000px;margin:2rem auto;padding:0 1rem;color:#18251d}
section{border:1px solid #d7e2d8;border-radius:.6rem;padding:1rem;margin:1rem 0}
code{word-break:break-all}table{border-collapse:collapse;width:100%}th,td{border-bottom:1px solid #d7e2d8;padding:.35rem;text-align:left;vertical-align:top}
button{font:inherit;padding:.45rem;background:#285c35;color:#fff;border:0;border-radius:.25rem;cursor:pointer}
.tag{display:inline-block;padding:.1rem .5rem;border-radius:999px;font-size:.8rem;font-weight:700}
.ok{background:#e8f7ec;color:#187a36}.drift{background:#fdefed;color:#b42318}.warn{background:#fff4d6;color:#946200}
.muted{color:#52665a}
</style></head><body>
<p><a href="/admin">Back to administration</a></p>
<h1>Access lists</h1>
<p>Generated from portal membership. <b>Intended</b> is what the portal wants, <b>files</b> are what Valheim enforces right now, and <b>env</b> is what the container would regenerate on its next start.</p>
{{range .Reports}}
<section>
<h2>{{.World}} {{if not .Reachable}}<span class="tag warn">unreachable</span>{{else if .InSync}}<span class="tag ok">in sync</span>{{else}}<span class="tag drift">drift</span>{{end}}</h2>
{{if .Error}}<p class="drift">{{.Error}}</p>{{end}}
<p class="muted">Permitted list enforcement is {{if .EnforcePermitted}}on: only granted players may join{{else}}off: every player with the password may join{{end}}.</p>
<table><thead><tr><th>List</th><th>Intended</th><th>Files on host</th><th>valheim.env</th></tr></thead><tbody>
<tr><td>admins</td><td>{{range .Intended.Admins}}<code>{{.}}</code> {{else}}<span class="muted">none</span>{{end}}</td>
<td>{{if .Reachable}}{{range .Live.Admins}}<code>{{.}}</code> {{else}}<span class="muted">none</span>{{end}}{{else}}<span class="muted">unknown</span>{{end}}</td>
<td>{{if .Reachable}}{{if .Live.EnvPresent}}{{range .Live.EnvAdmins}}<code>{{.}}</code> {{else}}<span class="muted">none</span>{{end}}{{else}}<span class="muted">no env file</span>{{end}}{{else}}<span class="muted">unknown</span>{{end}}</td></tr>
<tr><td>permitted</td><td>{{range .Intended.Permitted}}<code>{{.}}</code> {{else}}<span class="muted">none</span>{{end}}</td>
<td>{{if .Reachable}}{{range .Live.Permitted}}<code>{{.}}</code> {{else}}<span class="muted">none</span>{{end}}{{else}}<span class="muted">unknown</span>{{end}}</td>
<td>{{if .Reachable}}{{if .Live.EnvPresent}}{{range .Live.EnvPermitted}}<code>{{.}}</code> {{else}}<span class="muted">none</span>{{end}}{{else}}<span class="muted">no env file</span>{{end}}{{else}}<span class="muted">unknown</span>{{end}}</td></tr>
</tbody></table>
{{if .FileDrift}}<p class="drift">The live list files do not match the portal. Valheim is enforcing the host copy until you apply.</p>{{end}}
{{if .EnvDrift}}<p class="drift">valheim.env does not match the portal. A container recreate would restore the stale list.</p>{{end}}
<form method="post" action="/admin/worlds/{{.World}}/access-apply"><input type="hidden" name="csrf" value="{{$.CSRF}}"><button>Apply access lists to {{.World}}</button></form>
</section>
{{else}}<p>No worlds are registered.</p>{{end}}
</body></html>`
