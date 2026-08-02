package app

import (
	"net/http"
	"strings"
)

type backupAdminPage struct {
	Worlds  []PublicWorld
	World   string
	Backups []string
	Error   string
	CSRF    string
}

func (s *Server) backupAdmin(w http.ResponseWriter, r *http.Request) {
	worlds, err := s.store.PublicWorlds(r.Context())
	if err != nil {
		http.Error(w, "world catalog unavailable", http.StatusServiceUnavailable)
		return
	}
	page := backupAdminPage{Worlds: worlds, World: strings.TrimSpace(r.URL.Query().Get("world")), CSRF: s.csrfCookie(w, r)}
	if page.World != "" {
		if !validWorld(page.World) {
			http.Error(w, "invalid world", http.StatusBadRequest)
			return
		}
		if _, err := s.store.PublicWorld(r.Context(), page.World); err != nil {
			http.NotFound(w, r)
			return
		}
		reply, err := s.agent.Run(r.Context(), randomID(), page.World, "backups")
		if err != nil || reply.Status != "succeeded" {
			page.Error = "Backup inventory is currently unavailable."
		} else {
			for _, backup := range strings.Fields(reply.Output) {
				if validRestoreBackup(page.World, backup) {
					page.Backups = append(page.Backups, backup)
				}
			}
		}
	}
	render(w, backupAdminTemplate, page)
}

const backupAdminTemplate = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Valheim backup recovery</title><style>body{font:16px/1.5 system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem;color:#173321}section{border:1px solid #b9cdbf;border-radius:.6rem;padding:1rem;margin:1rem 0}form{display:flex;flex-wrap:wrap;align-items:end;gap:.75rem}label{display:grid;gap:.25rem}select,button{font:inherit;padding:.55rem}button{background:#285c35;color:white;border:0;border-radius:.3rem;font-weight:700}.danger{background:#9d3030}code{word-break:break-all}</style></head><body><p><a href="/admin">Back to administration</a></p><h1>Backup recovery</h1><p>Inspect one world's isolated backups. Restore uses a typed confirmation, creates a fresh backup, stops the server, and replaces only the selected save pair.</p><section><form method="get" action="/admin/backups"><label>World <select name="world" required><option value="">Select a world</option>{{range .Worlds}}<option value="{{.Name}}" {{if eq $.World .Name}}selected{{end}}>{{.Name}}</option>{{end}}</select></label><button>List backups</button></form></section>{{if .Error}}<p>{{.Error}}</p>{{end}}{{if .World}}<section><h2>{{.World}}</h2>{{range .Backups}}<form method="post" action="/admin/restores"><input type="hidden" name="csrf" value="{{$.CSRF}}"><input type="hidden" name="world" value="{{$.World}}"><input type="hidden" name="backup" value="{{.}}"><code>{{.}}</code><button class="danger">Review restore</button></form>{{else}}<p>No matching backups are available.</p>{{end}}</section>{{end}}</body></html>`
