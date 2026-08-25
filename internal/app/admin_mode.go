package app

import (
	"context"
	"database/sql"
	"errors"
	"html/template"
	"net/http"
	"strconv"
	"strings"
	"time"
)

// An admin-mode maintenance window loads JereKuusela-Structure_Tweaks and
// Azumatt-PerfectPlacement on one world, server-side.
//
// Those two mods disconnect every connected player when they are loaded server-side,
// which is why they were removed from all four servers on 2026-08-20. The capability
// they give an admin is still wanted, so they come back for a named window on a named
// world instead of living in the shared profile - measured 2026-08-25, all four worlds
// link to the same `admin` profile, so a profile-level switch would arm the whole fleet.
//
// There is no timer. The window stays open until an operator closes it, because a timer
// would decide on the operator's behalf that the work is finished.

// WorldAdminMode is one open window: which world, since when, opened by whom.
type WorldAdminMode struct {
	World string
	Since time.Time
	Actor string
}

// SetWorldAdminMode records a window as open. Repeating it on a world already in one
// keeps the original Since and actor: the window did not restart, and the operator
// asking about it wants to know when players first started being kicked.
func (s *Store) SetWorldAdminMode(ctx context.Context, world, actor string) error {
	if !validWorld(world) {
		return errors.New("invalid world")
	}
	if _, err := s.db.ExecContext(ctx,
		`INSERT INTO world_admin_mode(world, since, actor) VALUES(?,?,?) ON CONFLICT(world) DO NOTHING`,
		world, time.Now().UTC().Format(time.RFC3339Nano), actor); err != nil {
		return err
	}
	return s.Audit(ctx, actor, "world.admin_mode.open", world, "players are kicked while this is open")
}

// ClearWorldAdminMode records a window as closed. A world that is not in one is not an
// error: an operator who cannot tell whether the last attempt landed has to be able to
// press off again.
func (s *Store) ClearWorldAdminMode(ctx context.Context, world, actor string) error {
	if !validWorld(world) {
		return errors.New("invalid world")
	}
	if _, err := s.db.ExecContext(ctx, `DELETE FROM world_admin_mode WHERE world=?`, world); err != nil {
		return err
	}
	return s.Audit(ctx, actor, "world.admin_mode.close", world, "")
}

// WorldAdminModes is every open window, in one query, so a page that lists worlds can
// mark them all without asking per world.
func (s *Store) WorldAdminModes(ctx context.Context) (map[string]WorldAdminMode, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT world, since, actor FROM world_admin_mode`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	windows := make(map[string]WorldAdminMode)
	for rows.Next() {
		var window WorldAdminMode
		var since string
		if err := rows.Scan(&window.World, &since, &window.Actor); err != nil {
			return nil, err
		}
		window.Since, _ = time.Parse(time.RFC3339Nano, since)
		windows[window.World] = window
	}
	return windows, rows.Err()
}

// WorldAdminModeState answers for one world. The bool is whether a window is open, which
// is not the same question as whether the read succeeded.
func (s *Store) WorldAdminModeState(ctx context.Context, world string) (WorldAdminMode, bool, error) {
	if !validWorld(world) {
		return WorldAdminMode{}, false, errors.New("invalid world")
	}
	var window WorldAdminMode
	var since string
	err := s.db.QueryRowContext(ctx, `SELECT world, since, actor FROM world_admin_mode WHERE world=?`, world).
		Scan(&window.World, &since, &window.Actor)
	if errors.Is(err, sql.ErrNoRows) {
		return WorldAdminMode{}, false, nil
	}
	if err != nil {
		return WorldAdminMode{}, false, err
	}
	window.Since, _ = time.Parse(time.RFC3339Nano, since)
	return window, true, nil
}

// RunAdminMode opens or closes a window on one world.
//
// The agent composes the whole ordering from the host scripts that already exist - backup,
// stop, stage the overlay, deploy, start, wait for ready - so nothing here reimplements a
// step. The profile travels because the overlay is built from the archives that profile
// already pins, which is what keeps the window off the network.
func (a *AgentClient) RunAdminMode(ctx context.Context, id, world, profile string, on bool) (AgentReply, error) {
	operation := "admin_mode_off"
	if on {
		operation = "admin_mode_on"
	}
	return a.do(ctx, agentRequest{
		ID: id, World: world, Operation: operation, Profile: profile, Timestamp: time.Now().Unix(),
	})
}

// setWorldAdminMode is the toggle. Per world and explicitly untoggled, both by decision.
func (s *Server) setWorldAdminMode(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, 16<<10)
	if err := r.ParseForm(); err != nil {
		http.Error(w, "invalid form", http.StatusBadRequest)
		return
	}
	world := r.PathValue("world")
	on, err := strconv.ParseBool(r.FormValue("admin_mode"))
	if !validWorld(world) || err != nil {
		http.Error(w, "invalid admin mode request", http.StatusBadRequest)
		return
	}
	actor := r.Header.Get("X-Portal-Actor")
	_, open, err := s.store.WorldAdminModeState(r.Context(), world)
	if err != nil {
		http.Error(w, "unable to read the admin mode state", http.StatusInternalServerError)
		return
	}
	if on == open {
		// Already in the asked-for state. Turning off a world that is not in a window is
		// the case that matters: it must be a no-op and not an error, because it is what
		// an operator presses when they cannot tell whether the last attempt landed, and
		// because turning it into an error would make the recovery path refusable.
		http.Redirect(w, r, "/admin#server-"+template.URLQueryEscaper(world), http.StatusSeeOther)
		return
	}
	if on {
		// Refused before anything is touched. Arming loads mods that disconnect every
		// connected player, so a window opened on an occupied world would kick the people
		// it was opened to help.
		live := readWorldLiveness(s.cfg.MapSourceRoot, world, time.Now())
		if live.PlayerCount > 0 {
			stale := ""
			if !live.Ready {
				stale = " That count is the world's last report and the world is not answering now," +
					" so stop it first and try again."
			}
			s.adminModeRefusal(w, world, "Admin mode was refused: "+world+" has "+
				strconv.Itoa(live.PlayerCount)+" player(s) connected, and arming these mods"+
				" disconnects every one of them."+stale)
			return
		}
	}
	profile, ok := s.worldLinkedProfile(r.Context(), world)
	if !ok {
		s.adminModeRefusal(w, world, "Admin mode was refused: the profile "+world+
			" is linked to could not be read, and the overlay is built from the package"+
			" archives that profile pins. Nothing was touched.")
		return
	}
	jobID := randomID()
	operation := "admin_mode_off"
	if on {
		operation = "admin_mode_on"
	}
	if err := s.store.CreateJob(r.Context(), Job{ID: jobID, World: world, Operation: operation, Status: "queued", RequestedBy: actor, Detail: profile}, actor); err != nil {
		http.Error(w, "unable to queue the admin mode change", http.StatusInternalServerError)
		return
	}
	// Opening is recorded BEFORE the host runs, closing only AFTER it succeeds. Both err in
	// the same direction on purpose: a world recorded as dangerous when it is fine costs one
	// more toggle, while a world recorded as fine when it is armed kicks every player who
	// joins and nothing on the portal says why. A failure part-way through an open leaves the
	// overlay staged, so "open" is the honest reading of an open that did not finish.
	if on {
		if err := s.store.SetWorldAdminMode(r.Context(), world, actor); err != nil {
			_ = s.store.FinishJob(r.Context(), jobID, "failed", "admin mode state not recorded", actor)
			http.Error(w, "unable to record the admin mode state", http.StatusInternalServerError)
			return
		}
	}
	reply, err := s.agent.RunAdminMode(r.Context(), jobID, world, profile, on)
	if err != nil {
		_ = s.store.FinishJob(r.Context(), jobID, "failed", "agent request failed", actor)
		s.adminModeRefusal(w, world, adminModeUnknownState(world, on))
		return
	}
	_ = s.store.FinishJob(r.Context(), jobID, reply.Status, reply.Output, actor)
	if reply.Status != "succeeded" {
		detail := strings.TrimSpace(reply.Error)
		if detail == "" {
			detail = "the host reported no reason"
		}
		s.adminModeRefusal(w, world, adminModeFailure(world, on)+" "+detail)
		return
	}
	if !on {
		if err := s.store.ClearWorldAdminMode(r.Context(), world, actor); err != nil {
			// The host has already disarmed and restarted the world, so the world is safe
			// and only the record is wrong. Say exactly that rather than implying the
			// world is still armed.
			s.adminModeRefusal(w, world, world+" left admin mode on the host and is running"+
				" normally, but the portal could not clear its record of the window. It will"+
				" keep warning about "+world+" until that record is cleared; press the button"+
				" again to retry.")
			return
		}
	}
	http.Redirect(w, r, "/admin#server-"+template.URLQueryEscaper(world), http.StatusSeeOther)
}

// adminModeFailure says what a half-applied toggle left behind. The step that failed and
// the recovery command come from the agent, which is the only place that knows how far the
// sequence got; this is the sentence that frames it.
func adminModeFailure(world string, on bool) string {
	if on {
		return "Admin mode was NOT completed on " + world + ", and " + world + " is recorded as" +
			" in admin mode because the overlay may already be staged. The world is not" +
			" running. Do not tell players it is available."
	}
	return "Admin mode was NOT lifted from " + world + ", so " + world + " is still recorded as" +
		" in admin mode and still kicks every player who joins. The world is not running."
}

// adminModeUnknownState is the reply when the host agent could not be reached at all. The
// request may have run to completion, half of it, or none of it, and saying so is the only
// honest answer - a world stopped with no message is how a maintenance window becomes an
// outage nobody is looking for.
func adminModeUnknownState(world string, on bool) string {
	verb := "leave"
	if on {
		verb = "enter"
	}
	return "The host agent did not answer, so whether " + world + " managed to " + verb +
		" admin mode is unknown, and it may be stopped. " + world + " is recorded as in admin" +
		" mode. Check it on the host with \"hostops/status_valheim_server.sh " + world +
		"\", and recover with \"hostops/manage_mods.sh " + world + " deploy --apply\" then" +
		" \"hostops/start_valheim_server.sh " + world + "\" and" +
		" \"hostops/wait_valheim_server_ready.sh " + world + "\"."
}

// adminModeRefusal answers 409: the request was understood and refused, or it failed and
// left state the operator has to see. It is a page rather than a redirect because a
// redirect back to /admin would drop the sentence that says what the world is doing now.
func (s *Server) adminModeRefusal(w http.ResponseWriter, world, message string) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.WriteHeader(http.StatusConflict)
	render(w, adminModeRefusalTemplate, map[string]any{"World": world, "Message": message})
}

// worldLinkedProfile is the profile this world actually runs, read from the host rather
// than assumed. Reuses the profile catalog the mod admin page already reads links from.
func (s *Server) worldLinkedProfile(ctx context.Context, world string) (string, bool) {
	for _, row := range s.adminProfileCatalog(ctx, []PublicWorld{{Name: world}}) {
		if row.World == world && row.Linked && validWorld(row.Profile) {
			return row.Profile, true
		}
	}
	return "", false
}

const adminModeRefusalTemplate = `<!doctype html><html lang="en"><head><meta charset="utf-8">` +
	`<meta name="viewport" content="width=device-width,initial-scale=1"><title>Admin mode - {{.World}}</title><style>
body{margin:0;background:#150d0d;color:#f6eaea;font:16px/1.6 system-ui,sans-serif}
main{width:min(760px,calc(100% - 2rem));margin:3rem auto}
h1{margin:0 0 1rem}
.notice{background:#3a1414;border:1px solid #b84b4b;border-radius:.8rem;padding:1rem 1.25rem}
a{color:#71c492}
</style></head><body><main>
<h1>{{.World}}: admin mode</h1>
<div class="notice"><p>{{.Message}}</p></div>
<p><a href="/admin#server-{{.World}}">Back to administration</a></p>
</main></body></html>`
