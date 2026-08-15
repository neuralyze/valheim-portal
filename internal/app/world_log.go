package app

import (
	"fmt"
	"net/http"
	"strconv"
	"strings"
)

// A world's collected server log, on the admin site.
//
// Until now the only way to see a log was the "Capture logs" job, whose output landed in a job
// detail and read the live container - so it died with the container. This reads the file the host
// collector appends outside the container, which is what a crash is diagnosed from afterwards.
//
// The whole file is never rendered: it passes 12 MB on the busiest world here and only grows. What
// an operator means by "the log" is the end of it, optionally filtered.

const (
	defaultLogLines = 200
	maxLogLines     = 5000
)

// logLines keeps the request inside what the host script will accept, so a bad value is a clamped
// page rather than an error from three layers down.
func logLines(raw string) int {
	value, err := strconv.Atoi(strings.TrimSpace(raw))
	if err != nil || value <= 0 {
		return defaultLogLines
	}
	if value > maxLogLines {
		return maxLogLines
	}
	return value
}

func logFilter(raw string) string {
	filter := strings.TrimSpace(raw)
	if len(filter) > 120 {
		filter = filter[:120]
	}
	// The script refuses a multi-line filter; collapsing here means a pasted line ending is not an
	// error the operator has to understand.
	filter = strings.ReplaceAll(strings.ReplaceAll(filter, "\r", " "), "\n", " ")
	return filter
}

func (s *Server) worldLog(w http.ResponseWriter, r *http.Request) {
	world := r.PathValue("world")
	if !validWorld(world) {
		http.NotFound(w, r)
		return
	}
	lines := logLines(r.URL.Query().Get("lines"))
	filter := logFilter(r.URL.Query().Get("filter"))

	reply, err := s.agent.RunLog(r.Context(), randomID(), world, lines, filter)
	body, failure := "", ""
	switch {
	case err != nil:
		failure = "the host agent is unreachable: " + err.Error()
	case reply.Status != "succeeded":
		failure = "the agent could not read the log"
		if reply.Error != "" {
			failure += ": " + reply.Error
		}
	default:
		body = reply.Output
	}

	// Info is a second, cheap call: it answers "is there a log at all, and how big" without
	// reading any of it, which is what makes an absent log a sentence rather than an empty box.
	size := ""
	if info, infoErr := s.agent.RunLogInfo(r.Context(), randomID(), world); infoErr == nil && info.Status == "succeeded" {
		size = strings.TrimSpace(info.Output)
	}

	render(w, worldLogTemplate, map[string]any{
		"World": world, "Lines": lines, "Filter": filter, "Body": body, "Failure": failure,
		"Info": size, "IsAdmin": true, "SourceURL": s.cfg.SourceURL,
		"Empty": failure == "" && strings.TrimSpace(body) == "",
	})
}

// worldLogDownload serves the same tail as a file. It carries the admin guard like every other
// admin route: a server log names players, their Steam IDs and their join addresses, so a plain
// static file would be a disclosure with a convenient URL.
func (s *Server) worldLogDownload(w http.ResponseWriter, r *http.Request) {
	world := r.PathValue("world")
	if !validWorld(world) {
		http.NotFound(w, r)
		return
	}
	lines := logLines(r.URL.Query().Get("lines"))
	filter := logFilter(r.URL.Query().Get("filter"))
	reply, err := s.agent.RunLog(r.Context(), randomID(), world, lines, filter)
	if err != nil || reply.Status != "succeeded" {
		http.Error(w, "log unavailable", http.StatusServiceUnavailable)
		return
	}
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.Header().Set("Content-Disposition", fmt.Sprintf("attachment; filename=%q", world+"-last-"+strconv.Itoa(lines)+".log"))
	_, _ = w.Write([]byte(reply.Output))
}

const worldLogTemplate = `<!doctype html><html><head><meta charset="utf-8"><title>{{.World}} log - Neuralyze Valheim</title></head>
<body class="admin">
<header class="admin-nav">` + adminNavigation + `</header>
<main class="shell">
<h1>{{.World}} server log</h1>
<p class="install-note">The collected host log, which survives a restart and a removed container.
{{if .Info}}<br><code>{{.Info}}</code>{{end}}</p>

<form method="get" action="/admin/worlds/{{.World}}/log" class="agent-compose">
<label>Lines <input type="number" name="lines" value="{{.Lines}}" min="1" max="5000"></label>
<label>Contains <input type="text" name="filter" value="{{.Filter}}" maxlength="120" placeholder="e.g. Chainloader, or a Steam ID"></label>
<button type="submit">Show</button>
<a class="button-link secondary" href="/admin/worlds/{{.World}}/log.txt?lines={{.Lines}}&amp;filter={{.Filter}}">Download this view</a>
</form>

{{if .Failure}}<p class="notes warning">{{.Failure}}</p>{{end}}
{{if .Empty}}<p class="notes warning">Nothing to show. A world that has not run since the collector started has no log, and a filter that matches nothing says so above.</p>{{end}}
{{if .Body}}<pre class="notes">{{.Body}}</pre>{{end}}

<p class="install-note">Valheim's startup markers - <code>Load world</code>, <code>DungeonDB Start</code> -
are logged at Info level, and Info is trimmed from these servers. Their absence is not a fault.</p>
</main></body></html>`
