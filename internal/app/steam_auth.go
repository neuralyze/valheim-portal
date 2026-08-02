package app

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"
)

const (
	steamStateCookie   = "portal_steam_state"
	steamSessionCookie = "portal_steam_session"
	adminSessionCookie = "portal_admin_session"
)

type steamState struct {
	ExpiresAt  time.Time
	DeviceCode string
}

var steamClaimedID = regexp.MustCompile(`^https://steamcommunity\.com/openid/id/(7[0-9]{16})$`)

func (s *Server) login(w http.ResponseWriter, r *http.Request) {
	s.beginSteamLogin(w, r, "")
}

func (s *Server) beginSteamLogin(w http.ResponseWriter, r *http.Request, deviceCode string) {
	if deviceCode != "" && !validID(deviceCode) {
		http.NotFound(w, r)
		return
	}
	realm, err := s.portalURL("")
	if err != nil {
		s.playerError(w, r, http.StatusServiceUnavailable, errorPage{
			Title:   "Sign-in is unavailable",
			Message: "This portal is not configured for Steam sign-in yet. Ask the world owner to check its public address.",
		})
		return
	}
	state := randomID()
	returnTo, err := s.steamReturnURL(state)
	if err != nil {
		s.playerError(w, r, http.StatusServiceUnavailable, errorPage{
			Title:   "Sign-in is unavailable",
			Message: "This portal is not configured for Steam sign-in yet. Ask the world owner to check its public address.",
		})
		return
	}
	s.authMu.Lock()
	s.cleanExpiredSteamStatesLocked(time.Now())
	s.steamStates[state] = steamState{ExpiresAt: time.Now().Add(10 * time.Minute), DeviceCode: deviceCode}
	s.authMu.Unlock()
	http.SetCookie(w, &http.Cookie{Name: steamStateCookie, Value: state, Path: "/", MaxAge: 600, HttpOnly: true, Secure: s.cfg.CookieSecure, SameSite: http.SameSiteLaxMode})
	params := url.Values{
		"openid.ns":         {"http://specs.openid.net/auth/2.0"},
		"openid.mode":       {"checkid_setup"},
		"openid.identity":   {"http://specs.openid.net/auth/2.0/identifier_select"},
		"openid.claimed_id": {"http://specs.openid.net/auth/2.0/identifier_select"},
		"openid.realm":      {realm},
		"openid.return_to":  {returnTo},
	}
	http.Redirect(w, r, "https://steamcommunity.com/openid/login?"+params.Encode(), http.StatusFound)
}

func (s *Server) steamCallback(w http.ResponseWriter, r *http.Request) {
	// One page for every way a Steam handoff can go stale: the player only ever
	// needs to start again, and naming the internal distinction helps nobody.
	expired := errorPage{
		Title:   "This sign-in expired",
		Message: "Steam sign-ins are only valid for a few minutes. Start again and you will be back where you were.",
		Action:  "Sign in with Steam", Href: "/login",
	}
	state := r.URL.Query().Get("state")
	stateCookie, err := r.Cookie(steamStateCookie)
	if err != nil || !hmac.Equal([]byte(state), []byte(stateCookie.Value)) {
		s.playerError(w, r, http.StatusForbidden, expired)
		return
	}
	pending, ok := s.consumeSteamState(state)
	if !ok {
		s.playerError(w, r, http.StatusForbidden, expired)
		return
	}
	steamID, err := s.verifySteamOpenID(r)
	if err != nil {
		s.playerError(w, r, http.StatusUnauthorized, errorPage{
			Title:   "Steam did not confirm this sign-in",
			Message: "Steam could not verify who you are. Start the sign-in again.",
			Action:  "Sign in with Steam", Href: "/login",
		})
		return
	}
	if err := s.store.RecordSteamIdentity(r.Context(), steamID); err != nil {
		s.playerError(w, r, http.StatusServiceUnavailable, errorPage{
			Title:   "The portal is not answering",
			Message: "Your sign-in could not be recorded. Try again in a moment.",
			Action:  "Sign in with Steam", Href: "/login",
		})
		return
	}
	// Record the account name while we know the login is genuine, so the
	// operator approving this player sees who it is instead of 17 digits.
	s.syncSteamPersonas(r.Context(), []string{steamID})
	s.setSteamSession(w, steamID)
	http.SetCookie(w, &http.Cookie{Name: steamStateCookie, Value: "", Path: "/", MaxAge: -1, HttpOnly: true, Secure: s.cfg.CookieSecure, SameSite: http.SameSiteLaxMode})
	if pending.DeviceCode == "" {
		http.Redirect(w, r, "/", http.StatusSeeOther)
		return
	}
	if !s.hasDeviceGrant(pending.DeviceCode) {
		s.playerError(w, r, http.StatusGone, errorPage{
			Title:   "This app sign-in expired",
			Message: "Valheim Profile Sync waited too long for approval. Start the sign-in again from the app.",
		})
		return
	}
	http.Redirect(w, r, "/client/authorize/"+pending.DeviceCode, http.StatusSeeOther)
}

func (s *Server) logout(w http.ResponseWriter, r *http.Request) {
	http.SetCookie(w, &http.Cookie{Name: steamSessionCookie, Value: "", Path: "/", MaxAge: -1, HttpOnly: true, Secure: s.cfg.CookieSecure, SameSite: http.SameSiteLaxMode})
	http.SetCookie(w, &http.Cookie{Name: adminSessionCookie, Value: "", Path: "/", MaxAge: -1, HttpOnly: true, Secure: s.cfg.CookieSecure, SameSite: http.SameSiteStrictMode})
	http.Redirect(w, r, "/", http.StatusSeeOther)
}

func (s *Server) steamReturnURL(state string) (string, error) {
	callback, err := s.portalURL("/auth/steam/callback")
	if err != nil {
		return "", err
	}
	return callback + "?state=" + url.QueryEscape(state), nil
}

func (s *Server) consumeSteamState(state string) (steamState, bool) {
	if !validID(state) {
		return steamState{}, false
	}
	s.authMu.Lock()
	defer s.authMu.Unlock()
	s.cleanExpiredSteamStatesLocked(time.Now())
	pending, ok := s.steamStates[state]
	delete(s.steamStates, state)
	return pending, ok
}

func (s *Server) cleanExpiredSteamStatesLocked(now time.Time) {
	for state, pending := range s.steamStates {
		if !now.Before(pending.ExpiresAt) {
			delete(s.steamStates, state)
		}
	}
}

func (s *Server) verifySteamOpenID(r *http.Request) (string, error) {
	if r.URL.Query().Get("openid.mode") != "id_res" || r.URL.Query().Get("openid.op_endpoint") != "https://steamcommunity.com/openid/login" {
		return "", fmt.Errorf("invalid OpenID response")
	}
	claim := r.URL.Query().Get("openid.claimed_id")
	identity := r.URL.Query().Get("openid.identity")
	match := steamClaimedID.FindStringSubmatch(claim)
	if len(match) != 2 || identity != claim {
		return "", fmt.Errorf("invalid Steam identity")
	}
	expectedReturn, err := s.steamReturnURL(r.URL.Query().Get("state"))
	if err != nil || r.URL.Query().Get("openid.return_to") != expectedReturn {
		return "", fmt.Errorf("unexpected callback URL")
	}
	form := url.Values{}
	for key, values := range r.URL.Query() {
		if strings.HasPrefix(key, "openid.") && len(values) == 1 {
			form.Set(key, values[0])
		}
	}
	form.Set("openid.mode", "check_authentication")
	client := &http.Client{Timeout: 10 * time.Second}
	response, err := client.PostForm("https://steamcommunity.com/openid/login", form)
	if err != nil {
		return "", err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return "", fmt.Errorf("Steam returned %s", response.Status)
	}
	body, err := io.ReadAll(io.LimitReader(response.Body, 8<<10))
	if err != nil || !strings.Contains(string(body), "is_valid:true") {
		return "", fmt.Errorf("Steam rejected assertion")
	}
	return match[1], nil
}

func (s *Server) setSteamSession(w http.ResponseWriter, steamID string) {
	expires := time.Now().Add(12 * time.Hour).Unix()
	payload := steamID + "." + strconv.FormatInt(expires, 10)
	mac := hmac.New(sha256.New, s.csrf)
	mac.Write([]byte(payload))
	value := base64.RawURLEncoding.EncodeToString([]byte(payload + "." + base64.RawURLEncoding.EncodeToString(mac.Sum(nil))))
	http.SetCookie(w, &http.Cookie{Name: steamSessionCookie, Value: value, Path: "/", MaxAge: 12 * 60 * 60, HttpOnly: true, Secure: s.cfg.CookieSecure, SameSite: http.SameSiteLaxMode})
}
func (s *Server) setAdminSession(w http.ResponseWriter) {
	expires := strconv.FormatInt(time.Now().Add(12*time.Hour).Unix(), 10)
	mac := hmac.New(sha256.New, s.csrf)
	mac.Write([]byte("admin-session." + expires))
	value := expires + "." + base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
	http.SetCookie(w, &http.Cookie{Name: adminSessionCookie, Value: value, Path: "/", MaxAge: 12 * 60 * 60, HttpOnly: true, Secure: s.cfg.CookieSecure, SameSite: http.SameSiteStrictMode})
}

func (s *Server) hasAdminSession(r *http.Request) bool {
	cookie, err := r.Cookie(adminSessionCookie)
	if err != nil || len(cookie.Value) > 200 {
		return false
	}
	expiresValue, providedValue, ok := strings.Cut(cookie.Value, ".")
	if !ok {
		return false
	}
	expires, err := strconv.ParseInt(expiresValue, 10, 64)
	if err != nil || time.Now().Unix() >= expires {
		return false
	}
	provided, err := base64.RawURLEncoding.DecodeString(providedValue)
	if err != nil {
		return false
	}
	mac := hmac.New(sha256.New, s.csrf)
	mac.Write([]byte("admin-session." + expiresValue))
	return hmac.Equal(provided, mac.Sum(nil))
}

func (s *Server) steamID(r *http.Request) (string, bool) {
	cookie, err := r.Cookie(steamSessionCookie)
	if err != nil || len(cookie.Value) > 300 {
		return "", false
	}
	decoded, err := base64.RawURLEncoding.DecodeString(cookie.Value)
	if err != nil {
		return "", false
	}
	parts := strings.Split(string(decoded), ".")
	if len(parts) != 3 || !validSteamID(parts[0]) {
		return "", false
	}
	expires, err := strconv.ParseInt(parts[1], 10, 64)
	if err != nil || time.Now().Unix() >= expires {
		return "", false
	}
	provided, err := base64.RawURLEncoding.DecodeString(parts[2])
	if err != nil {
		return "", false
	}
	mac := hmac.New(sha256.New, s.csrf)
	mac.Write([]byte(parts[0] + "." + parts[1]))
	if !hmac.Equal(provided, mac.Sum(nil)) {
		return "", false
	}
	return parts[0], true
}

func (s *Server) requireSteam(w http.ResponseWriter, r *http.Request) (string, bool) {
	steamID, ok := s.steamID(r)
	if !ok {
		s.login(w, r)
	}
	return steamID, ok
}

func (s *Server) requireWorldAccess(w http.ResponseWriter, r *http.Request, world string) bool {
	if !validWorld(world) {
		http.NotFound(w, r)
		return false
	}
	steamID, ok := s.requireSteam(w, r)
	if !ok {
		return false
	}
	allowed, err := s.store.CanAccessWorld(r.Context(), world, steamID)
	if err != nil {
		s.playerError(w, r, http.StatusServiceUnavailable, errorPage{
			Title:   "The portal is not answering",
			Message: "Access to this world could not be checked. Try again in a moment.",
		})
		return false
	}
	if !allowed {
		// The Steam ID is the one thing the owner needs in order to fix this, and
		// the player is the only one who can read it off their own session.
		s.playerError(w, r, http.StatusForbidden, errorPage{
			Title:   "You do not have access to this world",
			Message: "Ask the world owner to grant access to Steam ID " + steamID + ".",
		})
		return false
	}
	return true
}

func (s *Server) requireReleaseAccess(w http.ResponseWriter, r *http.Request, releaseID string, historical bool) bool {
	release, err := s.store.PublicRelease(r.Context(), releaseID, historical)
	if err != nil {
		http.NotFound(w, r)
		return false
	}
	return s.requireWorldAccess(w, r, release.World)
}

const loginTemplate = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Valheim Profile Sync</title><style>:root{color-scheme:dark}*{box-sizing:border-box}body{min-height:100vh;margin:0;display:grid;place-items:center;padding:1.25rem;background:radial-gradient(circle at 80% 0,#368b6199,transparent 38rem),linear-gradient(140deg,#081911,#123728 48%,#07140e);color:#eef7f1;font:16px/1.5 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.card{width:min(100%,32rem);padding:clamp(2rem,7vw,4rem);border:1px solid #ffffff25;border-radius:1.25rem;background:#143728d9;box-shadow:0 2rem 6rem #0007}.brand{display:block;width:min(15rem,70vw);height:auto;margin-bottom:1.5rem}h1{margin:.9rem 0;font-size:clamp(2.5rem,9vw,4.3rem);line-height:.95;letter-spacing:-.06em}p{margin:0;color:#b9d1c0;font-size:1.05rem}.button{display:flex;justify-content:center;margin-top:2rem;padding:.82rem 1rem;border-radius:.6rem;background:#71c492;color:#082116;font-weight:800;text-decoration:none}.button:hover{background:#9ee3b7}.note{margin-top:1rem;color:#96ad9e;font-size:.83rem}</style></head><body><main class="card"><img class="brand" src="/assets/neuralyze-logo.svg" alt="Neuralyze"><h1>Valheim Profile Sync</h1><p>Sign in with Steam to view your approved worlds, keep profiles current, and launch with your existing Steam installation.</p><a class="button" href="/login">Continue with Steam</a><p class="note">Your Steam account is used only to verify world access.</p></main></body></html>`
