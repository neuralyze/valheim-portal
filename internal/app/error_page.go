package app

import (
	"net/http"
	"strings"
)

// errorPage is a dead end a player can reach: a lapsed sign-in, a world they
// were never granted, a download that is not publishable. Before this they were
// answered with unstyled text/plain and no way back, which reads as a broken
// site rather than a refusal.
type errorPage struct {
	Title   string
	Message string
	Action  string
	Href    string
}

// playerError answers browsers with the styled page and everything else with the
// plain text a program can read. The client polls several of these routes, so
// content negotiation is the difference, not the route.
func (s *Server) playerError(w http.ResponseWriter, r *http.Request, status int, page errorPage) {
	if page.Href == "" {
		page.Action, page.Href = "Back to your worlds", "/"
	}
	if !acceptsHTML(r) {
		http.Error(w, page.Message, status)
		return
	}
	// Both headers have to be set before the status line: render sets its own
	// Content-Type, but that is a no-op once WriteHeader has run.
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.WriteHeader(status)
	render(w, errorPageTemplate, page)
}

func acceptsHTML(r *http.Request) bool {
	return strings.Contains(r.Header.Get("Accept"), "text/html")
}

const errorPageTemplate = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark">
<link rel="icon" href="/favicon.ico" sizes="any">
<title>{{.Title}} · Valheim Profile Sync</title>
<style>
:root{color-scheme:dark;--ink:#eef7f1;--muted:#afc4b5;--moss:#71c492;--line:#ffffff20}
*{box-sizing:border-box}
body{min-height:100vh;margin:0;display:grid;place-items:center;padding:1.5rem;background:radial-gradient(circle at 82% -10%,#368b6199,transparent 38rem),linear-gradient(140deg,#081911,#123728 48%,#07140e);color:var(--ink);font:16px/1.55 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.shell{width:min(34rem,100%)}
.brand{display:flex;align-items:center;justify-content:center;gap:.65rem;margin-bottom:1.25rem;color:var(--moss);font-size:.78rem;font-weight:800;letter-spacing:.13em;text-transform:uppercase}
.rune{display:grid;place-items:center;width:2rem;height:2rem;border:1px solid #86d8a6;border-radius:50%;font-size:1.1rem}
.card{position:relative;overflow:hidden;padding:clamp(2rem,7vw,3.25rem);border:1px solid var(--line);border-radius:1.25rem;background:linear-gradient(155deg,#1b4a37e8,#10281fe8);box-shadow:0 1.5rem 5rem #0007}
.card:before{content:"";position:absolute;inset:0 0 auto;height:3px;background:linear-gradient(90deg,transparent,#e2a07a,transparent)}
h1{margin:0;font-size:clamp(1.8rem,7vw,2.6rem);line-height:1.05;letter-spacing:-.045em}
.detail{margin:1.1rem 0 0;color:var(--muted);font-size:1.02rem}
.action{display:inline-flex;align-items:center;min-height:2.8rem;margin:1.75rem 0 0;padding:.7rem 1.1rem;border-radius:.6rem;background:var(--moss);color:#082116;font-weight:800;text-decoration:none}
.action:hover{background:#9ee3b7}
</style>
</head>
<body>
<main class="shell">
  <div class="brand"><span class="rune" aria-hidden="true">ᛉ</span>Neuralyze gaming</div>
  <section class="card" aria-labelledby="error-title">
    <h1 id="error-title">{{.Title}}</h1>
    <p class="detail">{{.Message}}</p>
    <a class="action" href="{{.Href}}">{{.Action}}</a>
  </section>
</main>
</body>
</html>`
