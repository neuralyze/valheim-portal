package app

import (
	"context"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"database/sql"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"
)

const (
	deviceGrantLifetime         = 10 * time.Minute
	deviceTokenLifetime         = 5 * time.Minute
	diagnosticsTokenLifetime    = 30 * 24 * time.Hour
	deviceTokenScopeProfile     = "profile"
	deviceTokenScopeDiagnostics = "diagnostics"
	// Given to the game process so the exploration reporter can send a session's map the moment the
	// player logs out, instead of it waiting for the next launch. It is deliberately the narrowest of
	// the three: everything in a Valheim process is shared with every other mod loaded beside it, so
	// what leaks from there must be able to do nothing except upload a map.
	deviceTokenScopeExploration = "exploration"
	// The user code is read off the desktop app and typed into a browser, so the
	// alphabet is the RFC 8628 section 6.1 recommendation: no vowels, so a code
	// can never spell a word, and no character pairs that look alike in a
	// browser font. Eight characters give 20^8 combinations.
	deviceUserCodeAlphabet = "BCDFGHJKLMNPQRSTVWXZ"
	deviceUserCodeLength   = 8
	// A wrong code is a typo the first few times and a guessing run after that.
	// Five submissions per device code, then the grant is destroyed: an attacker
	// gets five of 20^8 tries and has to talk the victim through a fresh flow to
	// get five more, while a player who fat-fingers the code still has slack.
	deviceUserCodeMaxAttempts = 5
	// The admin CSRF cookie is scoped to /admin, so the confirmation form needs
	// its own cookie under /client. Same nonce-plus-HMAC construction.
	deviceCSRFCookie = "portal_device_csrf"
)

const deviceAuthorizationCompletePage = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark">
<title>Sign-in complete · Valheim Profile Sync</title>
<style>
:root{color-scheme:dark;--ink:#eef7f1;--muted:#afc4b5;--moss:#71c492;--line:#ffffff20;--panel:#153a2b}
*{box-sizing:border-box}
body{min-height:100vh;margin:0;display:grid;place-items:center;padding:1.5rem;background:radial-gradient(circle at 82% -10%,#368b6199,transparent 38rem),linear-gradient(140deg,#081911,#123728 48%,#07140e);color:var(--ink);font:16px/1.55 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.shell{width:min(34rem,100%)}
.brand{display:flex;align-items:center;justify-content:center;gap:.65rem;margin-bottom:1.25rem;color:var(--moss);font-size:.78rem;font-weight:800;letter-spacing:.13em;text-transform:uppercase}
.rune{display:grid;place-items:center;width:2rem;height:2rem;border:1px solid #86d8a6;border-radius:50%;font-size:1.1rem}
.card{position:relative;overflow:hidden;padding:clamp(2rem,7vw,3.25rem);border:1px solid var(--line);border-radius:1.25rem;background:linear-gradient(155deg,#1b4a37e8,#10281fe8);box-shadow:0 1.5rem 5rem #0007;text-align:center}
.card:before{content:"";position:absolute;inset:0 0 auto;height:3px;background:linear-gradient(90deg,transparent,var(--moss),transparent)}
.check{display:grid;place-items:center;width:4.5rem;height:4.5rem;margin:0 auto 1.5rem;border:1px solid #8de0ad66;border-radius:50%;background:#71c49218;color:#9ce1b5;font-size:2.1rem;font-weight:800;box-shadow:0 0 0 .65rem #71c4920b}
h1{margin:0;font-size:clamp(2rem,8vw,3rem);line-height:1.05;letter-spacing:-.045em}
.lead{margin:1.25rem auto .35rem;color:var(--ink);font-size:1.08rem;font-weight:650}
.detail{max-width:29rem;margin:.35rem auto 0;color:var(--muted)}
.next{display:flex;align-items:center;justify-content:center;gap:.6rem;margin:1.75rem 0 0;padding:1rem;border:1px solid var(--line);border-radius:.8rem;background:#08191166;color:#d9ebe0;font-size:.92rem}
.dot{width:.55rem;height:.55rem;border-radius:50%;background:var(--moss);box-shadow:0 0 0 .3rem #71c4921f}
.close{margin:1.25rem 0 0;color:#8eaa98;font-size:.82rem}
</style>
<link rel="stylesheet" href="/assets/site.css">
</head>
<body>
<main class="shell">
  <div class="brand"><span class="rune" aria-hidden="true">ᛉ</span>Neuralyze gaming</div>
  <section class="card" aria-labelledby="complete-title">
    <div class="check" aria-hidden="true">✓</div>
    <h1 id="complete-title">Sign-in complete</h1>
    <p class="lead">Your Steam account is connected.</p>
    <p class="detail">Valheim Profile Sync is continuing securely in the desktop application.</p>
    <p class="next"><span class="dot" aria-hidden="true"></span>Return to Valheim Profile Sync</p>
    <p class="close">You can safely close this browser tab.</p>
  </section>
</main>
</body>
</html>`

// deviceAuthorizationConfirmPage is what the authorize link actually opens. It
// reuses the sign-in-complete page's tokens so the two pages of the same flow
// look like one, and adds the form controls this one needs.
const deviceAuthorizationConfirmPage = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark">
<title>Confirm sign-in · Valheim Profile Sync</title>
<style>
:root{color-scheme:dark;--ink:#eef7f1;--muted:#afc4b5;--moss:#71c492;--line:#ffffff20;--panel:#153a2b}
*{box-sizing:border-box}
body{min-height:100vh;margin:0;display:grid;place-items:center;padding:1.5rem;background:radial-gradient(circle at 82% -10%,#368b6199,transparent 38rem),linear-gradient(140deg,#081911,#123728 48%,#07140e);color:var(--ink);font:16px/1.55 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.shell{width:min(34rem,100%)}
.brand{display:flex;align-items:center;justify-content:center;gap:.65rem;margin-bottom:1.25rem;color:var(--moss);font-size:.78rem;font-weight:800;letter-spacing:.13em;text-transform:uppercase}
.rune{display:grid;place-items:center;width:2rem;height:2rem;border:1px solid #86d8a6;border-radius:50%;font-size:1.1rem}
.card{position:relative;overflow:hidden;padding:clamp(2rem,7vw,3.25rem);border:1px solid var(--line);border-radius:1.25rem;background:linear-gradient(155deg,#1b4a37e8,#10281fe8);box-shadow:0 1.5rem 5rem #0007;text-align:center}
.card:before{content:"";position:absolute;inset:0 0 auto;height:3px;background:linear-gradient(90deg,transparent,var(--moss),transparent)}
h1{margin:0;font-size:clamp(1.8rem,7vw,2.6rem);line-height:1.05;letter-spacing:-.045em}
.lead{margin:1.25rem auto .35rem;color:var(--ink);font-size:1.08rem;font-weight:650}
.detail{max-width:29rem;margin:.35rem auto 0;color:var(--muted)}
.facts{display:grid;gap:.55rem;margin:1.75rem 0 0;padding:1rem 1.15rem;border:1px solid var(--line);border-radius:.8rem;background:#08191166;text-align:left}
.facts div{display:flex;align-items:baseline;justify-content:space-between;gap:1rem;margin:0}
.facts dt{color:var(--muted);font-size:.8rem;letter-spacing:.09em;text-transform:uppercase}
.facts dd{margin:0;color:#d9ebe0;font-weight:700;word-break:break-word}
.problem{margin:1.25rem 0 0;padding:.85rem 1rem;border:1px solid #e2907066;border-radius:.8rem;background:#48201a80;color:#f4c9b8;font-size:.92rem}
form{display:grid;gap:.55rem;margin:1.5rem 0 0;text-align:left}
label{color:var(--muted);font-size:.82rem;letter-spacing:.07em;text-transform:uppercase}
input[type=text]{padding:.8rem .95rem;border:1px solid var(--line);border-radius:.6rem;background:#0819118c;color:var(--ink);font:700 1.3rem/1.2 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;letter-spacing:.22em;text-align:center;text-transform:uppercase}
input[type=text]:focus{outline:2px solid var(--moss);outline-offset:2px}
button{margin-top:.55rem;padding:.85rem 1rem;border:0;border-radius:.6rem;background:var(--moss);color:#082116;font:inherit;font-weight:800;cursor:pointer}
button:hover{background:#9ee3b7}
.close{margin:1.5rem 0 0;color:#8eaa98;font-size:.82rem}
</style>
</head>
<body>
<main class="shell">
  <div class="brand"><span class="rune" aria-hidden="true">ᛉ</span>Neuralyze gaming</div>
  <section class="card" aria-labelledby="confirm-title">
    <h1 id="confirm-title">Confirm this sign-in</h1>
    <p class="lead">Valheim Profile Sync wants to sync a profile as you.</p>
    <p class="detail">Approve it only if you started this yourself, and only if the details below match what the desktop application is showing.</p>
    <dl class="facts">
      <div><dt>World</dt><dd>{{.World}}</dd></div>
      <div><dt>Profile</dt><dd>{{.Profile}}</dd></div>
      <div><dt>Client</dt><dd>{{.Mode}}</dd></div>
      <div><dt>Steam ID</dt><dd>{{.SteamID}}</dd></div>
    </dl>
    {{if .Problem}}<p class="problem" role="alert">{{.Problem}}</p>{{end}}
    <form method="post" action="/client/authorize/{{.Code}}" autocomplete="off">
      <input type="hidden" name="csrf" value="{{.CSRF}}">
      <label for="user_code">Confirmation code shown in Valheim Profile Sync</label>
      <input id="user_code" type="text" name="user_code" placeholder="XXXX-XXXX" maxlength="16" spellcheck="false" autocapitalize="characters" autocomplete="off" autofocus required>
      <button type="submit">Approve this sign-in</button>
    </form>
    <p class="close">Did not start this? Close this tab. Nothing is authorized until you approve it here.</p>
  </section>
</main>
</body>
</html>`

type deviceConfirmationPage struct {
	Code, World, Profile, Mode, SteamID, CSRF, Problem string
}

type deviceGrant struct {
	SteamID, World, Profile, ClientType, ReleaseID string
	// UserCode is the short code the desktop app shows and the player types back
	// on the confirmation page. It is what makes an unsolicited authorize link
	// useless: whoever opens it cannot approve without the desktop app's screen.
	UserCode string
	// Attempts counts wrong UserCode submissions against this grant.
	Attempts               int
	ExpiresAt              time.Time
	Authorized, Exchanging bool
}

type deviceTokenClaims struct {
	SteamID, World, Profile, ClientType, ReleaseID, Scope string
	ExpiresAt                                             time.Time
}

type deviceStartRequest struct {
	World      string `json:"world"`
	Profile    string `json:"profile"`
	ClientType string `json:"client_type"`
}

func (s *Server) deviceStart(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, 8<<10)
	defer r.Body.Close()
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	var request deviceStartRequest
	if err := decoder.Decode(&request); err != nil {
		http.Error(w, "invalid device request", http.StatusBadRequest)
		return
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		http.Error(w, "invalid device request", http.StatusBadRequest)
		return
	}
	if !validWorld(request.World) || !validProfile(request.Profile) || (request.ClientType != "flat" && request.ClientType != "vr") {
		http.Error(w, "invalid device request", http.StatusBadRequest)
		return
	}
	if _, err := s.store.CurrentRelease(r.Context(), request.World, request.Profile, request.ClientType); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			http.NotFound(w, r)
			return
		}
		http.Error(w, "unavailable", http.StatusServiceUnavailable)
		return
	}
	code := randomID()
	userCode := randomUserCode()
	now := time.Now()
	s.authMu.Lock()
	s.cleanExpiredDeviceGrantsLocked(now)
	s.deviceCodes[code] = deviceGrant{World: request.World, Profile: request.Profile, ClientType: request.ClientType, UserCode: userCode, ExpiresAt: now.Add(deviceGrantLifetime)}
	s.authMu.Unlock()
	authorizeURL, err := s.portalURL("/client/authorize/" + code)
	if err != nil {
		s.authMu.Lock()
		delete(s.deviceCodes, code)
		s.authMu.Unlock()
		http.Error(w, "portal configuration unavailable", http.StatusServiceUnavailable)
		return
	}
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Type", "application/json")
	// A single-operator install authorizes on the Steam sign-in alone, so no code is sent: the
	// client used to display one anyway and instruct the player to type it on a page that never
	// asks, which reads as a broken sign-in. The grant keeps its code for the confirmation path.
	body := map[string]any{
		"device_code":           code,
		"authorize_url":         authorizeURL,
		"expires_in":            int(deviceGrantLifetime.Seconds()),
		"confirmation_required": !s.cfg.SkipDeviceCode,
	}
	if !s.cfg.SkipDeviceCode {
		body["user_code"] = userCode
	}
	json.NewEncoder(w).Encode(body)
}

// deviceAuthorize renders the confirmation page. Opening this link authorizes
// nothing on its own: a link sent to a signed-in player used to be a one-click
// account takeover, so approval now needs the POST below, carrying the user
// code that only the desktop application displays.
func (s *Server) deviceAuthorize(w http.ResponseWriter, r *http.Request) {
	code := r.PathValue("code")
	if !validID(code) {
		http.NotFound(w, r)
		return
	}
	grant, ok := s.deviceGrant(code)
	if !ok {
		http.NotFound(w, r)
		return
	}
	steamID, ok := s.steamID(r)
	if !ok {
		s.beginSteamLogin(w, r, code)
		return
	}
	if grant.Authorized {
		// Re-opening the link after approving, typically a browser restoring the
		// tab. completeDeviceAuthorization stops at the ownership check.
		s.completeDeviceAuthorization(w, r, code, steamID)
		return
	}
	if s.cfg.SkipDeviceCode {
		// Single-operator install: the Steam sign-in above already proved who this is,
		// and completeDeviceAuthorization still enforces that this Steam account owns
		// the grant's world and profile. Retyping a code the same person is looking at
		// on the same machine adds no second party to check, so skip it.
		s.completeDeviceAuthorization(w, r, code, steamID)
		return
	}
	s.renderDeviceConfirmation(w, r, http.StatusOK, code, grant, steamID, "")
}

func (s *Server) deviceAuthorizeConfirm(w http.ResponseWriter, r *http.Request) {
	code := r.PathValue("code")
	if !validID(code) {
		http.NotFound(w, r)
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, 8<<10)
	if err := r.ParseForm(); err != nil {
		http.Error(w, "invalid confirmation", http.StatusBadRequest)
		return
	}
	grant, ok := s.deviceGrant(code)
	if !ok {
		http.NotFound(w, r)
		return
	}
	steamID, ok := s.steamID(r)
	if !ok {
		// The Steam session lapsed while the page sat open. Send the player back
		// to the page, which starts the same Steam handoff the link does.
		http.Redirect(w, r, "/client/authorize/"+code, http.StatusSeeOther)
		return
	}
	if !s.validDeviceCSRF(r) {
		http.Error(w, "invalid confirmation", http.StatusForbidden)
		return
	}
	if grant.Authorized {
		s.completeDeviceAuthorization(w, r, code, steamID)
		return
	}
	remaining, matched := s.recordUserCodeAttempt(code, r.PostFormValue("user_code"))
	if !matched {
		if remaining <= 0 {
			http.Error(w, "too many incorrect codes; start the sign-in again from Valheim Profile Sync", http.StatusTooManyRequests)
			return
		}
		problem := fmt.Sprintf("That code does not match the one in Valheim Profile Sync. %d attempts left before this sign-in is cancelled.", remaining)
		if remaining == 1 {
			problem = "That code does not match the one in Valheim Profile Sync. One attempt left before this sign-in is cancelled."
		}
		s.renderDeviceConfirmation(w, r, http.StatusBadRequest, code, grant, steamID, problem)
		return
	}
	s.completeDeviceAuthorization(w, r, code, steamID)
}

func (s *Server) renderDeviceConfirmation(w http.ResponseWriter, r *http.Request, status int, code string, grant deviceGrant, steamID, problem string) {
	mode := "Flat (non-VR)"
	if grant.ClientType == "vr" {
		mode = "VR"
	}
	// Both headers have to be set before the status line: render() sets its own
	// Content-Type, but that is a no-op once WriteHeader has run.
	page := deviceConfirmationPage{Code: code, World: grant.World, Profile: grant.Profile, Mode: mode, SteamID: steamID, CSRF: s.deviceCSRFCookie(w, r), Problem: problem}
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.WriteHeader(status)
	render(w, deviceAuthorizationConfirmPage, page)
}

// recordUserCodeAttempt checks a submitted user code and counts the misses. It
// reports the attempts left; a grant that runs out is deleted outright, so a
// guessing run destroys the very code it was guessing at.
func (s *Server) recordUserCodeAttempt(code, submitted string) (remaining int, matched bool) {
	s.authMu.Lock()
	defer s.authMu.Unlock()
	s.cleanExpiredDeviceGrantsLocked(time.Now())
	grant, ok := s.deviceCodes[code]
	if !ok {
		return 0, false
	}
	if subtleEqual(normalizeUserCode(submitted), normalizeUserCode(grant.UserCode)) {
		return deviceUserCodeMaxAttempts - grant.Attempts, true
	}
	grant.Attempts++
	if grant.Attempts >= deviceUserCodeMaxAttempts {
		delete(s.deviceCodes, code)
		return 0, false
	}
	s.deviceCodes[code] = grant
	return deviceUserCodeMaxAttempts - grant.Attempts, false
}

func (s *Server) deviceCSRFCookie(w http.ResponseWriter, r *http.Request) string {
	var nonce string
	if c, err := r.Cookie(deviceCSRFCookie); err == nil && len(c.Value) == 64 {
		nonce = c.Value
	} else {
		nonce = randomHex(32)
		http.SetCookie(w, &http.Cookie{Name: deviceCSRFCookie, Value: nonce, Path: "/client", HttpOnly: true, Secure: s.cfg.CookieSecure, SameSite: http.SameSiteStrictMode, MaxAge: int(deviceGrantLifetime.Seconds())})
	}
	return s.csrfToken(nonce)
}

func (s *Server) validDeviceCSRF(r *http.Request) bool {
	c, err := r.Cookie(deviceCSRFCookie)
	if err != nil || len(c.Value) != 64 {
		return false
	}
	return subtleEqual(s.csrfToken(c.Value), r.PostFormValue("csrf"))
}

func randomUserCode() string {
	// Rejection sampling: 256 is not a multiple of the alphabet size, so folding
	// a raw byte with % alone would make its leading letters more likely.
	const ceiling = 256 - 256%len(deviceUserCodeAlphabet)
	letters := make([]byte, 0, deviceUserCodeLength)
	buf := make([]byte, 1)
	for len(letters) < deviceUserCodeLength {
		if _, err := rand.Read(buf); err != nil {
			panic(err)
		}
		if int(buf[0]) >= ceiling {
			continue
		}
		letters = append(letters, deviceUserCodeAlphabet[int(buf[0])%len(deviceUserCodeAlphabet)])
	}
	half := deviceUserCodeLength / 2
	return string(letters[:half]) + "-" + string(letters[half:])
}

// normalizeUserCode reduces a code to the characters that carry it, so the
// grouping dash, stray spaces and lower case a player typed all still match.
func normalizeUserCode(value string) string {
	if len(value) > 64 {
		return ""
	}
	var out strings.Builder
	out.Grow(len(value))
	for _, r := range strings.ToUpper(value) {
		if (r >= 'A' && r <= 'Z') || (r >= '0' && r <= '9') {
			out.WriteRune(r)
		}
	}
	return out.String()
}

func (s *Server) completeDeviceAuthorization(w http.ResponseWriter, r *http.Request, code, steamID string) {
	grant, ok := s.deviceGrant(code)
	if !ok {
		http.NotFound(w, r)
		return
	}
	if grant.Authorized {
		if grant.SteamID != steamID {
			http.Error(w, "device authorization belongs to another Steam account", http.StatusForbidden)
			return
		}
		s.deviceAuthorizationComplete(w)
		return
	}
	release, err := s.store.CurrentRelease(r.Context(), grant.World, grant.Profile, grant.ClientType)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			s.deleteDeviceGrant(code)
			http.NotFound(w, r)
			return
		}
		http.Error(w, "unavailable", http.StatusServiceUnavailable)
		return
	}
	allowed, err := s.store.CanAccessWorld(r.Context(), grant.World, steamID)
	if err != nil {
		http.Error(w, "unavailable", http.StatusServiceUnavailable)
		return
	}
	if !allowed {
		http.Error(w, "world access denied", http.StatusForbidden)
		return
	}
	s.authMu.Lock()
	defer s.authMu.Unlock()
	s.cleanExpiredDeviceGrantsLocked(time.Now())
	grant, ok = s.deviceCodes[code]
	if !ok {
		http.NotFound(w, r)
		return
	}
	if grant.Authorized && grant.SteamID != steamID {
		http.Error(w, "device authorization belongs to another Steam account", http.StatusForbidden)
		return
	}
	grant.SteamID = steamID
	grant.ReleaseID = release.ID
	grant.Authorized = true
	s.deviceCodes[code] = grant
	s.deviceAuthorizationComplete(w)
}

func (s *Server) deviceAuthorizationComplete(w http.ResponseWriter) {
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	io.WriteString(w, deviceAuthorizationCompletePage)
}

func (s *Server) deviceToken(w http.ResponseWriter, r *http.Request) {
	code := r.PathValue("code")
	if !validID(code) {
		http.NotFound(w, r)
		return
	}
	grant, ok := s.beginDeviceExchange(code)
	if !ok {
		http.Error(w, "authorization pending", http.StatusUnauthorized)
		return
	}
	release, err := s.store.CurrentRelease(r.Context(), grant.World, grant.Profile, grant.ClientType)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			s.deleteDeviceGrant(code)
			http.Error(w, "authorization is no longer current", http.StatusUnauthorized)
			return
		}
		s.releaseDeviceExchange(code)
		http.Error(w, "unavailable", http.StatusServiceUnavailable)
		return
	}
	allowed, err := s.store.CanAccessWorld(r.Context(), grant.World, grant.SteamID)
	if err != nil {
		s.releaseDeviceExchange(code)
		http.Error(w, "unavailable", http.StatusServiceUnavailable)
		return
	}
	if !allowed || release.ID != grant.ReleaseID {
		s.deleteDeviceGrant(code)
		http.Error(w, "authorization is no longer valid", http.StatusUnauthorized)
		return
	}
	s.deleteDeviceGrant(code)
	expiresAt := time.Now().Add(deviceTokenLifetime)
	claims := deviceTokenClaims{SteamID: grant.SteamID, World: grant.World, Profile: grant.Profile, ClientType: grant.ClientType, ReleaseID: grant.ReleaseID, Scope: deviceTokenScopeProfile, ExpiresAt: expiresAt}
	diagnosticsClaims := claims
	diagnosticsClaims.Scope = deviceTokenScopeDiagnostics
	diagnosticsClaims.ExpiresAt = time.Now().Add(diagnosticsTokenLifetime)
	explorationClaims := claims
	explorationClaims.Scope = deviceTokenScopeExploration
	explorationClaims.ExpiresAt = time.Now().Add(diagnosticsTokenLifetime)
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{
		"token":             s.mintDeviceToken(claims),
		"diagnostics_token": s.mintDeviceToken(diagnosticsClaims),
		"exploration_token": s.mintDeviceToken(explorationClaims),
	})
}

// clientManifest and the payload, runtime, companion and plugin endpoints below all serve the profile
// itself, so they require the profile scope by name. Before the exploration scope existed this was
// invisible: every token in circulation could do everything, so nothing distinguished "a valid token"
// from "a token allowed to do this".
func (s *Server) clientManifest(w http.ResponseWriter, r *http.Request) {
	world, profile, clientType := r.PathValue("world"), r.PathValue("profile"), r.PathValue("clientType")
	claims, ok, err := s.validDeviceToken(r.Context(), r, world, profile, clientType)
	if err != nil {
		http.Error(w, "unavailable", http.StatusServiceUnavailable)
		return
	}
	if !ok || claims.Scope != deviceTokenScopeProfile {
		http.Error(w, "client authorization required", http.StatusUnauthorized)
		return
	}
	release, err := s.store.CurrentRelease(r.Context(), claims.World, claims.Profile, claims.ClientType)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			http.NotFound(w, r)
			return
		}
		http.Error(w, "unavailable", http.StatusServiceUnavailable)
		return
	}
	profileArtifact, err := s.publishedProfileArtifact(r.Context(), claims.ReleaseID)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			http.NotFound(w, r)
			return
		}
		http.Error(w, "unavailable", http.StatusServiceUnavailable)
		return
	}
	out := map[string]any{
		"release_id":     claims.ReleaseID,
		"world":          claims.World,
		"profile":        claims.Profile,
		"client_type":    claims.ClientType,
		"version":        release.Version,
		"profile_sha256": profileArtifact.SHA256,
		"profile_size":   profileArtifact.Size,
	}
	if claims.ClientType == "vr" {
		runtimeArtifact, err := s.publishedVRRuntimeArtifact(r.Context(), claims.ReleaseID)
		if err != nil {
			if errors.Is(err, sql.ErrNoRows) {
				http.NotFound(w, r)
				return
			}
			http.Error(w, "unavailable", http.StatusServiceUnavailable)
			return
		}
		out["runtime_sha256"] = runtimeArtifact.SHA256
		out["runtime_size"] = runtimeArtifact.Size
	}
	if claims.ClientType == "flat" {
		companionArtifact, err := s.publishedFlatCompanionArtifact(r.Context(), claims.ReleaseID)
		if err == nil {
			out["companion_sha256"] = companionArtifact.SHA256
			out["companion_size"] = companionArtifact.Size
		} else if !errors.Is(err, sql.ErrNoRows) {
			http.Error(w, "unavailable", http.StatusServiceUnavailable)
			return
		}
	}
	// The diagnostics plugin is optional and applies to both client types, so an
	// absent artifact simply omits the fields rather than failing the manifest.
	diagnosticsArtifact, err := s.publishedDiagnosticsPluginArtifact(r.Context(), claims.ReleaseID)
	if err == nil {
		out["diagnostics_plugin_sha256"] = diagnosticsArtifact.SHA256
		out["diagnostics_plugin_size"] = diagnosticsArtifact.Size
	} else if !errors.Is(err, sql.ErrNoRows) {
		http.Error(w, "unavailable", http.StatusServiceUnavailable)
		return
	}
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(out)
}

func (s *Server) clientPayload(w http.ResponseWriter, r *http.Request) {
	world, profile, clientType := r.PathValue("world"), r.PathValue("profile"), r.PathValue("clientType")
	claims, ok, err := s.validDeviceToken(r.Context(), r, world, profile, clientType)
	if err != nil {
		http.Error(w, "unavailable", http.StatusServiceUnavailable)
		return
	}
	if !ok || claims.Scope != deviceTokenScopeProfile {
		http.Error(w, "client authorization required", http.StatusUnauthorized)
		return
	}
	artifact, err := s.publishedProfileArtifact(r.Context(), claims.ReleaseID)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			http.NotFound(w, r)
			return
		}
		http.Error(w, "unavailable", http.StatusServiceUnavailable)
		return
	}
	s.serveArtifact(w, r, artifact)
}

func (s *Server) publishedProfileArtifact(ctx context.Context, releaseID string) (Artifact, error) {
	return s.publishedArtifact(ctx, releaseID, "profile")
}

func (s *Server) publishedVRRuntimeArtifact(ctx context.Context, releaseID string) (Artifact, error) {
	return s.publishedArtifact(ctx, releaseID, "vr_runtime")
}

func (s *Server) publishedFlatCompanionArtifact(ctx context.Context, releaseID string) (Artifact, error) {
	return s.publishedArtifact(ctx, releaseID, "flat_companion")
}

func (s *Server) publishedDiagnosticsPluginArtifact(ctx context.Context, releaseID string) (Artifact, error) {
	return s.publishedArtifact(ctx, releaseID, "diag_plugin")
}

func (s *Server) publishedArtifact(ctx context.Context, releaseID, kind string) (Artifact, error) {
	artifacts, err := s.store.PublishedArtifacts(ctx, releaseID)
	if err != nil {
		return Artifact{}, err
	}
	for _, artifact := range artifacts {
		if artifact.Kind == kind {
			return artifact, nil
		}
	}
	return Artifact{}, sql.ErrNoRows
}

func (s *Server) clientRuntime(w http.ResponseWriter, r *http.Request) {
	world, profile, clientType := r.PathValue("world"), r.PathValue("profile"), r.PathValue("clientType")
	claims, ok, err := s.validDeviceToken(r.Context(), r, world, profile, clientType)
	if err != nil {
		http.Error(w, "unavailable", http.StatusServiceUnavailable)
		return
	}
	if !ok || claims.Scope != deviceTokenScopeProfile || claims.ClientType != "vr" {
		http.Error(w, "client authorization required", http.StatusUnauthorized)
		return
	}
	artifact, err := s.publishedVRRuntimeArtifact(r.Context(), claims.ReleaseID)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			http.NotFound(w, r)
			return
		}
		http.Error(w, "unavailable", http.StatusServiceUnavailable)
		return
	}
	s.serveArtifact(w, r, artifact)
}

func (s *Server) clientCompanion(w http.ResponseWriter, r *http.Request) {
	world, profile, clientType := r.PathValue("world"), r.PathValue("profile"), r.PathValue("clientType")
	claims, ok, err := s.validDeviceToken(r.Context(), r, world, profile, clientType)
	if err != nil {
		http.Error(w, "unavailable", http.StatusServiceUnavailable)
		return
	}
	if !ok || claims.Scope != deviceTokenScopeProfile || claims.ClientType != "flat" {
		http.Error(w, "client authorization required", http.StatusUnauthorized)
		return
	}
	artifact, err := s.publishedFlatCompanionArtifact(r.Context(), claims.ReleaseID)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			http.NotFound(w, r)
			return
		}
		http.Error(w, "unavailable", http.StatusServiceUnavailable)
		return
	}
	s.serveArtifact(w, r, artifact)
}

// clientDiagnosticsPlugin serves the portal-hosted diagnostics plugin. Unlike the
// VR runtime and Flat companion it is not scoped to one client type, because the
// same assembly is valid for both.
func (s *Server) clientDiagnosticsPlugin(w http.ResponseWriter, r *http.Request) {
	world, profile, clientType := r.PathValue("world"), r.PathValue("profile"), r.PathValue("clientType")
	claims, ok, err := s.validDeviceToken(r.Context(), r, world, profile, clientType)
	if err != nil {
		http.Error(w, "unavailable", http.StatusServiceUnavailable)
		return
	}
	if !ok || claims.Scope != deviceTokenScopeProfile {
		http.Error(w, "client authorization required", http.StatusUnauthorized)
		return
	}
	artifact, err := s.publishedDiagnosticsPluginArtifact(r.Context(), claims.ReleaseID)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			http.NotFound(w, r)
			return
		}
		http.Error(w, "unavailable", http.StatusServiceUnavailable)
		return
	}
	s.serveArtifact(w, r, artifact)
}

func (s *Server) mintDeviceToken(claims deviceTokenClaims) string {
	payload := strings.Join([]string{claims.SteamID, claims.World, claims.Profile, claims.ClientType, claims.ReleaseID, claims.Scope, strconv.FormatInt(claims.ExpiresAt.Unix(), 10)}, "|")
	mac := hmac.New(sha256.New, s.csrf)
	mac.Write([]byte(payload))
	return base64.RawURLEncoding.EncodeToString([]byte(payload + "|" + base64.RawURLEncoding.EncodeToString(mac.Sum(nil))))
}

func (s *Server) validDeviceToken(ctx context.Context, r *http.Request, world, profile, clientType string) (deviceTokenClaims, bool, error) {
	parts := strings.Fields(r.Header.Get("Authorization"))
	if len(parts) != 2 || parts[0] != "Bearer" || len(parts[1]) > 2048 {
		return deviceTokenClaims{}, false, nil
	}
	raw, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		return deviceTokenClaims{}, false, nil
	}
	fields := strings.Split(string(raw), "|")
	if len(fields) != 8 || !validSteamID(fields[0]) || !validWorld(fields[1]) || !validProfile(fields[2]) || (fields[3] != "flat" && fields[3] != "vr") || !validIdentifier(fields[4]) || (fields[5] != deviceTokenScopeProfile && fields[5] != deviceTokenScopeDiagnostics && fields[5] != deviceTokenScopeExploration) || fields[1] != world || fields[2] != profile || fields[3] != clientType {
		return deviceTokenClaims{}, false, nil
	}
	expires, err := strconv.ParseInt(fields[6], 10, 64)
	if err != nil || time.Now().Unix() >= expires {
		return deviceTokenClaims{}, false, nil
	}
	signature, err := base64.RawURLEncoding.DecodeString(fields[7])
	if err != nil {
		return deviceTokenClaims{}, false, nil
	}
	payload := strings.Join(fields[:7], "|")
	mac := hmac.New(sha256.New, s.csrf)
	mac.Write([]byte(payload))
	if !hmac.Equal(signature, mac.Sum(nil)) {
		return deviceTokenClaims{}, false, nil
	}
	allowed, err := s.store.CanAccessWorld(ctx, fields[1], fields[0])
	if err != nil {
		return deviceTokenClaims{}, false, err
	}
	if !allowed {
		return deviceTokenClaims{}, false, nil
	}
	release, err := s.store.CurrentRelease(ctx, fields[1], fields[2], fields[3])
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return deviceTokenClaims{}, false, nil
		}
		return deviceTokenClaims{}, false, err
	}
	if release.ID != fields[4] {
		return deviceTokenClaims{}, false, nil
	}
	return deviceTokenClaims{SteamID: fields[0], World: fields[1], Profile: fields[2], ClientType: fields[3], ReleaseID: fields[4], Scope: fields[5], ExpiresAt: time.Unix(expires, 0)}, true, nil
}

func (s *Server) hasDeviceGrant(code string) bool {
	_, ok := s.deviceGrant(code)
	return ok
}

func (s *Server) deviceGrant(code string) (deviceGrant, bool) {
	s.authMu.Lock()
	defer s.authMu.Unlock()
	s.cleanExpiredDeviceGrantsLocked(time.Now())
	grant, ok := s.deviceCodes[code]
	return grant, ok
}

func (s *Server) beginDeviceExchange(code string) (deviceGrant, bool) {
	s.authMu.Lock()
	defer s.authMu.Unlock()
	s.cleanExpiredDeviceGrantsLocked(time.Now())
	grant, ok := s.deviceCodes[code]
	if !ok || !grant.Authorized || grant.Exchanging {
		return deviceGrant{}, false
	}
	grant.Exchanging = true
	s.deviceCodes[code] = grant
	return grant, true
}

func (s *Server) releaseDeviceExchange(code string) {
	s.authMu.Lock()
	defer s.authMu.Unlock()
	grant, ok := s.deviceCodes[code]
	if ok && grant.Exchanging {
		grant.Exchanging = false
		s.deviceCodes[code] = grant
	}
}

func (s *Server) deleteDeviceGrant(code string) {
	s.authMu.Lock()
	defer s.authMu.Unlock()
	delete(s.deviceCodes, code)
}

func (s *Server) cleanExpiredDeviceGrantsLocked(now time.Time) {
	for code, grant := range s.deviceCodes {
		if !now.Before(grant.ExpiresAt) {
			delete(s.deviceCodes, code)
		}
	}
}
