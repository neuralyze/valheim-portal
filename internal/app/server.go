package app

import (
	"context"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"database/sql"
	_ "embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"html/template"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/netip"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/neuralyze/valheim-portal/internal/version"
)

//go:embed assets/neuralyze-logo.svg
var neuralyzeLogo []byte

//go:embed assets/neuralyze.ico
var neuralyzeIcon []byte

//go:embed assets/neuralyze-192.png
var neuralyzeIcon192 []byte

//go:embed assets/neuralyze-512.png
var neuralyzeIcon512 []byte

//go:embed assets/neuralyze-512-maskable.png
var neuralyzeMaskableIcon512 []byte

//go:embed assets/site.webmanifest
var siteWebManifest []byte

//go:embed assets/world-map.js
var worldMapJS []byte

//go:embed assets/admin-profile-autofill.js
var adminProfileAutofillJS []byte

//go:embed assets/copy-value.js
var copyValueJS []byte

//go:embed assets/admin-agent.js
var adminAgentJS []byte

//go:embed assets/admin-dock.js
var adminDockJS []byte

//go:embed assets/builder-labels.js
var builderLabelsJS []byte

//go:embed assets/site.css
var siteCSS []byte

type Server struct {
	cfg          Config
	store        *Store
	agent        *AgentClient
	mapPublisher worldMapPublisher
	personas     personaResolver
	csrf         []byte
	adminToken   []byte
	// agentBridgeToken authenticates the local agent process on /api/agent/*. Empty means the
	// bridge is off, which is the default: a deployment must opt in before an agent can drive
	// anything, and the portal holds no model credentials either way.
	agentBridgeToken []byte
	trustedProxy     netip.Prefix
	mux              *http.ServeMux
	limiter          *rateLimiter
	// deviceLimiter is separate because the Windows client polls the device
	// token route every two seconds while a sign-in is pending; sharing the
	// general bucket meant one normal sign-in spent most of it.
	deviceLimiter *rateLimiter
	// sharedBucketWarning fires once when the trusted proxy forwards no client
	// address, which collapses every rate-limit key onto the proxy itself.
	sharedBucketWarning sync.Once
	// Per-route request body ceilings. Fields rather than constants so a
	// deployment can tune them and tests can exercise the limit without
	// pushing gigabytes through the parser.
	formBodyLimit   int64
	uploadBodyLimit int64
	restoreMu       sync.Mutex
	restores        map[string]restoreRequest
	worldgenMu      sync.Mutex
	worldgens       map[string]worldgenRequest
	provisionMu     sync.Mutex
	provisions      map[string]provisionRequest
	authMu          sync.Mutex
	steamStates     map[string]steamState
	deviceCodes     map[string]deviceGrant
}

type restoreRequest struct {
	Actor, World, Backup string
	ExpiresAt            time.Time
}
type worldCatalogEntry struct {
	Name    string `json:"name"`
	Port    int    `json:"port"`
	EndPort int    `json:"-"`
	Status  string `json:"status"`
}
type adminWorld struct {
	PublicWorld
	Port string
}
type provisionRequest struct {
	Actor     string
	World     string
	JoinHost  string
	Publish   bool
	Request   ProvisionAgentRequest
	Packages  []installedMod
	ExpiresAt time.Time
}

// adminTokenHeader carries the shared secret the reverse proxy injects on the
// routes it authenticates. The identity header alone is not authentication:
// under the shipped compose deployment Docker NATs every request to the bridge
// gateway, which is the configured trusted-proxy range, so the network half of
// the admin check is unconditionally true and a browser-supplied header would
// be the entire control.
const adminTokenHeader = "X-Portal-Admin-Token"

// Request body ceilings. Multipart uploads spill past maxUploadMemoryBytes to
// temporary files instead of being held whole in memory.
const (
	defaultFormBodyBytes = 64 << 10
	// 512 MiB is far above any real profile bundle and, unlike the previous
	// 2 GiB, survives the container memory ceiling once a spill lands in the
	// /tmp tmpfs. See compose.yaml.
	maxArtifactBodyBytes  = 512 << 20
	maxUploadMemoryBytes  = 16 << 20
	minAdminTokenBytes    = 32
	deviceTokenPollBudget = 120
)

// readAdminToken loads PORTAL_ADMIN_TOKEN_FILE. Every failure is fatal on
// purpose: a portal that starts without a token falls back to header-only
// admin authentication, which is the bug this token exists to close.
func readAdminToken() ([]byte, error) {
	path := strings.TrimSpace(os.Getenv("PORTAL_ADMIN_TOKEN_FILE"))
	if path == "" {
		return nil, errors.New("PORTAL_ADMIN_TOKEN_FILE is required")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("admin token: %w", err)
	}
	token := strings.TrimSpace(string(raw))
	if len(token) < minAdminTokenBytes {
		return nil, fmt.Errorf("admin token in %s must contain at least %d bytes", path, minAdminTokenBytes)
	}
	return []byte(token), nil
}

func NewServer(cfg Config, store *Store, agent *AgentClient) (*Server, error) {
	secret, err := os.ReadFile(cfg.CSRFSecretFile)
	if err != nil {
		return nil, err
	}
	if len(strings.TrimSpace(string(secret))) < 32 {
		return nil, errors.New("CSRF secret must contain at least 32 bytes")
	}
	if cfg.TrustedProxyCIDR == "" {
		return nil, errors.New("PORTAL_TRUSTED_PROXY_CIDR is required")
	}
	trustedProxy, err := netip.ParsePrefix(cfg.TrustedProxyCIDR)
	if err != nil {
		return nil, fmt.Errorf("invalid PORTAL_TRUSTED_PROXY_CIDR: %w", err)
	}
	if _, err := portalURL(cfg.PublicBaseURL, ""); err != nil {
		return nil, fmt.Errorf("invalid PORTAL_PUBLIC_BASE_URL: %w", err)
	}
	adminToken, err := readAdminToken()
	if err != nil {
		return nil, err
	}
	agentBridgeToken, err := readAgentBridgeToken()
	if err != nil {
		return nil, fmt.Errorf("invalid PORTAL_AGENT_BRIDGE_TOKEN_FILE: %w", err)
	}
	s := &Server{cfg: cfg, store: store, agent: agent, csrf: []byte(strings.TrimSpace(string(secret))), adminToken: adminToken, agentBridgeToken: agentBridgeToken, trustedProxy: trustedProxy, mux: http.NewServeMux(), limiter: newRateLimiter(40, time.Minute), deviceLimiter: newRateLimiter(deviceTokenPollBudget, time.Minute), formBodyLimit: defaultFormBodyBytes, uploadBodyLimit: maxArtifactBodyBytes, restores: map[string]restoreRequest{}, worldgens: map[string]worldgenRequest{}, provisions: map[string]provisionRequest{}, steamStates: map[string]steamState{}, deviceCodes: map[string]deviceGrant{}}
	s.mapPublisher = s.publishWorldAnalysis
	s.personas = s.fetchSteamPersonas
	s.routes()
	return s, nil
}
func (s *Server) routes() {
	s.mux.HandleFunc("GET /assets/neuralyze-logo.svg", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "image/svg+xml")
		w.Header().Set("Cache-Control", "public, max-age=86400")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Write(neuralyzeLogo)
	})
	s.mux.HandleFunc("GET /assets/world-map.js", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "text/javascript; charset=utf-8")
		w.Header().Set("Cache-Control", "no-cache")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Write(worldMapJS)
	})
	s.mux.HandleFunc("GET /assets/admin-profile-autofill.js", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "text/javascript; charset=utf-8")
		w.Header().Set("Cache-Control", "no-cache")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Write(adminProfileAutofillJS)
	})
	s.mux.HandleFunc("GET /assets/copy-value.js", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "text/javascript; charset=utf-8")
		w.Header().Set("Cache-Control", "no-cache")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Write(copyValueJS)
	})
	s.mux.HandleFunc("GET /assets/admin-agent.js", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "text/javascript; charset=utf-8")
		w.Header().Set("Cache-Control", "no-cache")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Write(adminAgentJS)
	})
	s.mux.HandleFunc("GET /assets/builder-labels.js", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "text/javascript; charset=utf-8")
		w.Header().Set("Cache-Control", "no-cache")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Write(builderLabelsJS)
	})
	s.mux.HandleFunc("GET /assets/admin-dock.js", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "text/javascript; charset=utf-8")
		w.Header().Set("Cache-Control", "no-cache")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Write(adminDockJS)
	})
	s.mux.HandleFunc("GET /favicon.ico", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "image/x-icon")
		w.Header().Set("Cache-Control", "public, max-age=86400")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Write(neuralyzeIcon)
	})
	s.mux.HandleFunc("GET /icons/neuralyze-192.png", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "image/png")
		w.Header().Set("Cache-Control", "public, max-age=86400")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Write(neuralyzeIcon192)
	})
	s.mux.HandleFunc("GET /icons/neuralyze-512.png", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "image/png")
		w.Header().Set("Cache-Control", "public, max-age=86400")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Write(neuralyzeIcon512)
	})
	s.mux.HandleFunc("GET /icons/neuralyze-512-maskable.png", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "image/png")
		w.Header().Set("Cache-Control", "public, max-age=86400")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Write(neuralyzeMaskableIcon512)
	})
	s.mux.HandleFunc("GET /site.webmanifest", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/manifest+json")
		w.Header().Set("Cache-Control", "no-cache")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Write(siteWebManifest)
	})
	s.mux.HandleFunc("GET /assets/site.css", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "text/css; charset=utf-8")
		w.Header().Set("Cache-Control", "no-cache")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Write(siteCSS)
	})
	s.mux.HandleFunc("GET /healthz", s.health)
	s.mux.HandleFunc("GET /readyz", s.ready)
	// Unauthenticated on purpose: operators and the installer need to identify
	// a running deployment, and the build identity is not a secret.
	s.mux.HandleFunc("GET /version", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Cache-Control", "no-store")
		json.NewEncoder(w).Encode(map[string]string{"version": version.Version})
	})
	s.mux.HandleFunc("GET /api/status", s.status)
	s.mux.HandleFunc("GET /login", s.login)
	s.mux.HandleFunc("GET /auth/steam/callback", s.steamCallback)
	s.mux.HandleFunc("POST /logout", s.logout)
	s.mux.HandleFunc("POST /client/device", s.deviceStart)
	s.mux.HandleFunc("GET /client/authorize/{code}", s.deviceAuthorize)
	s.mux.HandleFunc("POST /client/authorize/{code}", s.deviceAuthorizeConfirm)
	s.mux.HandleFunc("POST /client/token/{code}", s.deviceToken)
	s.mux.HandleFunc("GET /client/manifest/{world}/{profile}/{clientType}", s.clientManifest)
	s.mux.HandleFunc("GET /client/payload/{world}/{profile}/{clientType}", s.clientPayload)
	s.mux.HandleFunc("GET /client/runtime/{world}/{profile}/{clientType}", s.clientRuntime)
	s.mux.HandleFunc("GET /client/companion/{world}/{profile}/{clientType}", s.clientCompanion)
	s.mux.HandleFunc("GET /client/diagnostics-plugin/{world}/{profile}/{clientType}", s.clientDiagnosticsPlugin)
	s.mux.HandleFunc("GET /client/ValheimProfileSync.exe", s.clientInstaller)
	s.mux.HandleFunc("POST /client/diagnostics/{world}/{profile}/{clientType}", s.clientDiagnostics)
	s.mux.HandleFunc("GET /", s.home)
	s.mux.HandleFunc("GET /worlds/{world}", s.world)
	s.mux.HandleFunc("GET /worlds/{world}/history", s.worldHistory)
	s.mux.HandleFunc("GET /releases/{id}", s.release)
	s.mux.HandleFunc("GET /releases/{id}/manifest.json", s.manifest)
	s.mux.HandleFunc("GET /artifacts/{id}", s.download)
	s.mux.HandleFunc("GET /history/releases/{id}", s.historyRelease)
	s.mux.HandleFunc("GET /history/releases/{id}/manifest.json", s.historyManifest)
	s.mux.HandleFunc("GET /history/artifacts/{id}", s.historyDownload)
	s.mux.HandleFunc("GET /admin", s.admin(s.adminHome))
	s.mux.HandleFunc("GET /admin/backups", s.admin(s.backupAdmin))
	s.mux.HandleFunc("GET /admin/mods", s.admin(s.modAdmin))
	s.mux.HandleFunc("GET /admin/worlds/{world}/map", s.admin(s.worldAnalysisMap))
	s.mux.HandleFunc("GET /admin/worlds/{world}/analysis.json", s.admin(s.worldAnalysisJSON))
	s.mux.HandleFunc("POST /admin/worlds/{world}/analysis", s.admin(s.runWorldAnalysis))
	s.mux.HandleFunc("POST /admin/worlds/{world}/builders", s.admin(s.nameBuilder))
	s.mux.HandleFunc("GET /admin/worlds/{world}/map/manifest.json", s.admin(s.worldTerrainManifest))
	s.mux.HandleFunc("GET /admin/worlds/{world}/map/tiles/{key}/{zoom}/{x}/{y}", s.admin(s.worldTerrainTile))
	s.mux.HandleFunc("GET /admin/worlds/{world}/map/overlays/{source}/{zoom}/{x}/{y}", s.admin(s.worldOverlayTile))
	s.mux.HandleFunc("POST /admin/mods/action", s.admin(s.mutateMod))
	s.mux.HandleFunc("POST /admin/mods/deploy", s.admin(s.deployMods))
	s.mux.HandleFunc("GET /admin/servers/new", s.admin(s.newServer))
	s.mux.HandleFunc("POST /admin/servers/review", s.admin(s.reviewServer))
	s.mux.HandleFunc("POST /admin/servers/{id}", s.admin(s.confirmServer))
	s.mux.HandleFunc("POST /admin/releases", s.admin(s.createRelease))
	s.mux.HandleFunc("POST /admin/artifacts", s.adminUpload(s.uploadArtifact))
	s.mux.HandleFunc("POST /admin/releases/{id}/publish", s.admin(s.publish))
	s.mux.HandleFunc("POST /admin/releases/batch-publish", s.admin(s.batchPublishFlat))
	s.mux.HandleFunc("POST /admin/releases/{id}/discard", s.admin(s.discardDraft))
	s.mux.HandleFunc("POST /admin/releases/{id}/archive", s.admin(s.archive))
	s.mux.HandleFunc("POST /admin/jobs", s.admin(s.runJob))
	s.mux.HandleFunc("POST /admin/worlds/{world}/status", s.admin(s.setWorldStatus))
	s.mux.HandleFunc("POST /admin/worlds/{world}/description", s.admin(s.setWorldDescription))
	s.mux.HandleFunc("POST /admin/worlds/{world}/enabled", s.admin(s.setWorldEnabled))
	s.mux.HandleFunc("POST /admin/worlds/{world}/port", s.admin(s.setWorldPort))
	s.mux.HandleFunc("POST /admin/worlds/{world}/profiles/{profile}/debug-logging", s.admin(s.setProfileDebugLogging))
	s.mux.HandleFunc("POST /admin/worlds/register", s.admin(s.registerWorld))
	s.mux.HandleFunc("GET /admin/worlds/{world}/remove", s.admin(s.worldRemovalConfirmation))
	s.mux.HandleFunc("POST /admin/worlds/{world}/remove", s.admin(s.confirmWorldRemoval))
	s.mux.HandleFunc("GET /admin/diagnostics", s.admin(s.listDiagnostics))
	s.mux.HandleFunc("GET /admin/diagnostics/{id}", s.admin(s.downloadDiagnostics))
	s.mux.HandleFunc("POST /admin/world-members", s.admin(s.grantWorldMember))
	s.mux.HandleFunc("POST /admin/world-members/revoke", s.admin(s.revokeWorldMember))
	s.mux.HandleFunc("POST /admin/steam-identities/label", s.admin(s.labelSteamIdentity))
	s.mux.HandleFunc("POST /admin/steam-identities/refresh", s.admin(s.refreshSteamIdentities))
	s.mux.HandleFunc("POST /admin/world-members/role", s.admin(s.setWorldMemberRole))
	s.mux.HandleFunc("GET /admin/access", s.admin(s.verifyWorldAccess))
	s.mux.HandleFunc("POST /admin/access-apply", s.admin(s.applyAllWorldAccess))
	s.mux.HandleFunc("POST /admin/worlds/{world}/access-apply", s.admin(s.applyWorldAccess))
	s.mux.HandleFunc("POST /admin/worlds/{world}/permitted-enforcement", s.admin(s.setPermittedEnforcement))
	s.mux.HandleFunc("POST /admin/restores", s.admin(s.prepareRestore))
	s.mux.HandleFunc("GET /admin/restores/{id}", s.admin(s.restoreConfirmation))
	s.mux.HandleFunc("POST /admin/restores/{id}", s.admin(s.confirmRestore))
	s.mux.HandleFunc("POST /admin/worldgen", s.admin(s.prepareWorldgen))
	s.mux.HandleFunc("GET /admin/worldgen/{id}", s.admin(s.worldgenConfirmation))
	s.mux.HandleFunc("POST /admin/worldgen/{id}", s.admin(s.confirmWorldgen))
	// The operator's agent surface, and the bridge its process talks to. The chat is admin-only
	// like every other control; the bridge authenticates with its own token and is disabled
	// unless a deployment configures one.
	s.mux.HandleFunc("GET /admin/worlds/{world}/log", s.admin(s.worldLog))
	s.mux.HandleFunc("GET /admin/worlds/{world}/log.txt", s.admin(s.worldLogDownload))
	s.mux.HandleFunc("GET /admin/agent", s.admin(s.agentChat))
	s.mux.HandleFunc("POST /admin/agent/message", s.admin(s.agentChatMessage))
	s.mux.HandleFunc("POST /admin/agent/decide", s.admin(s.agentChatDecide))
	s.mux.HandleFunc("GET /admin/agent/status.json", s.admin(s.agentChatStatus))
	s.mux.HandleFunc("GET /admin/agent/tail.json", s.admin(s.agentChatTail))
	s.mux.HandleFunc("GET /api/agent/inbox", s.bridge(s.agentInbox))
	s.mux.HandleFunc("GET /api/agent/verbs", s.bridge(s.agentVerbs))
	s.mux.HandleFunc("POST /api/agent/message", s.bridge(s.agentSay))
	s.mux.HandleFunc("POST /api/agent/verb", s.bridge(s.agentVerb))
}
func (s *Server) Handler() http.Handler { return s.secure(s.mux) }
func (s *Server) secure(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id := randomID()
		w.Header().Set("X-Request-ID", id)
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("Referrer-Policy", "same-origin")
		w.Header().Set("X-Frame-Options", "DENY")
		w.Header().Set("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
		w.Header().Set("Content-Security-Policy", "default-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'; object-src 'none'; img-src 'self' data: https://gcdn.thunderstore.io; style-src 'self' 'unsafe-inline'")
		if !s.rateLimitExempt(r) && !s.bucketFor(r).Allow(s.rateKey(r)) {
			http.Error(w, "rate limit exceeded", http.StatusTooManyRequests)
			return
		}
		next.ServeHTTP(w, r)
	})
}

// bucketFor keeps device-token polling off the general bucket. A pending
// sign-in polls every two seconds, which alone used most of the shared budget.
func (s *Server) bucketFor(r *http.Request) *rateLimiter {
	if r.Method == http.MethodPost && strings.HasPrefix(r.URL.Path, "/client/token/") {
		return s.deviceLimiter
	}
	return s.limiter
}

// rateKey identifies the client a request is counted against. Behind the
// trusted proxy every request carries the proxy's own address, so counting
// RemoteAddr there gives one global bucket: any anonymous visitor could 429
// the whole portal. X-Forwarded-For is only believed from the trusted proxy,
// because a direct client can set it to anything it likes.
func (s *Server) rateKey(r *http.Request) string {
	if s.fromTrustedProxy(r) {
		forwarded, _, _ := strings.Cut(r.Header.Get("X-Forwarded-For"), ",")
		if addr, err := netip.ParseAddr(strings.TrimSpace(forwarded)); err == nil {
			return addr.Unmap().String()
		}
		s.sharedBucketWarning.Do(func() {
			slog.Error("every client shares one rate-limit bucket",
				"reason", "the trusted proxy forwards no usable X-Forwarded-For",
				"remote", clientIP(r),
				"fix", "set X-Forwarded-For on every proxied route")
		})
	}
	return clientIP(r)
}

// rateLimitExempt keeps the limiter off the surfaces where it only ever hurts the operator.
//
// The limiter exists for anonymous traffic: a stranger guessing device codes, hammering profile
// downloads, or spending someone else's budget. It was applied to every route from the first commit,
// including /admin - and an administration page is not one request. It is the page, five assets, a
// status poll, and a dock that polls every two seconds, all from one address. 40 requests a minute
// cannot hold that, so the operator met "rate limit exceeded" while doing their job. Map tiles were
// carved out for exactly this reason once already; carving out one path at a time was the mistake.
//
// Administration is already gated by three independent facts - the trusted proxy, the identity
// header, and the admin token - plus CSRF on writes. A limiter behind all of that protects nothing
// and blocks the only person who is supposed to be there.
func (s *Server) rateLimitExempt(r *http.Request) bool {
	if strings.HasPrefix(r.URL.Path, "/admin") {
		return true
	}
	// The pages the operator's own browser loads to render administration.
	switch r.URL.Path {
	case "/assets/site.css", "/assets/admin-agent.js", "/assets/admin-dock.js",
		"/assets/admin-profile-autofill.js", "/assets/copy-value.js", "/favicon.ico":
		return true
	}
	return isMapResourceRequest(r)
}

func isMapResourceRequest(r *http.Request) bool {
	if r.Method != http.MethodGet || !strings.HasPrefix(r.URL.Path, "/admin/worlds/") {
		return false
	}
	return strings.Contains(r.URL.Path, "/map/tiles/") || strings.Contains(r.URL.Path, "/map/overlays/")
}
func (s *Server) health(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	io.WriteString(w, `{"status":"ok"}`)
}

// ready is what the installer and an operator use to decide a deployment is usable,
// so it checks the agent socket rather than only the database. It used to ping SQLite
// alone while the installer reported "the portal reached the agent socket" - and that
// sentence was printed on a deployment where /run/agent was empty inside the
// container, because systemd had recreated the agent's RuntimeDirectory under a
// running bind mount. Every operator action failed; the readiness check said ready.
//
// A missing socket is 503: a portal that cannot reach the agent cannot start a
// server, publish a release, or read a log, which is what this deployment is for.
func (s *Server) ready(w http.ResponseWriter, r *http.Request) {
	if err := s.store.db.PingContext(r.Context()); err != nil {
		http.Error(w, "not ready: database", 503)
		return
	}
	if problem := s.agentSocketProblem(); problem != "" {
		http.Error(w, "not ready: "+problem, 503)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	io.WriteString(w, `{"status":"ready","database":"ok","agent":"ok"}`)
}

// agentSocketProblem names why the agent is unreachable, or is empty when it is
// reachable. Stat rather than dial: a dial would enqueue work on every health check,
// and the failure this exists to catch is a socket that is not there at all.
func (s *Server) agentSocketProblem() string {
	info, err := os.Stat(s.cfg.AgentSocket)
	if err != nil {
		return "agent socket " + s.cfg.AgentSocket + " is absent"
	}
	if info.Mode()&os.ModeSocket == 0 {
		return "agent socket " + s.cfg.AgentSocket + " is not a socket"
	}
	return ""
}

// clientDownloadProblem is the operator-facing reason the published Windows
// client must not be served, or empty when it is publishable. It names a host
// path and a build script, so it is for the admin page and the log only.
func (s *Server) clientDownloadProblem() string {
	return clientArtifactProblem(inspectClientExecutable(s.cfg.ClientExecutable))
}

// clientUnavailableMessage is the whole of what a player is told. The previous
// answer was the operator text, which put a filesystem path in front of anyone
// who clicked Download for Windows.
const clientUnavailableMessage = "The Windows app is not available to download right now. Ask the world owner to publish it."

func (s *Server) clientInstaller(w http.ResponseWriter, r *http.Request) {
	if problem := s.clientDownloadProblem(); problem != "" {
		// Refusing is the point: a console-subsystem build is visibly broken for
		// every player who downloads it, and a silent 200 hides that until
		// someone complains.
		slog.Error("refusing to publish the Windows client", "path", s.cfg.ClientExecutable, "problem", problem)
		s.playerError(w, r, http.StatusServiceUnavailable, errorPage{
			Title:   "Download unavailable",
			Message: clientUnavailableMessage,
		})
		return
	}
	file, err := os.Open(s.cfg.ClientExecutable)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() {
		http.NotFound(w, r)
		return
	}
	w.Header().Set("Content-Type", "application/vnd.microsoft.portable-executable")
	w.Header().Set("Content-Disposition", `attachment; filename="ValheimProfileSync.exe"`)
	http.ServeContent(w, r, "ValheimProfileSync.exe", info.ModTime(), file)
}
func (s *Server) status(w http.ResponseWriter, r *http.Request) {
	rs, err := s.store.CurrentReleases(r.Context())
	if err != nil {
		http.Error(w, "unavailable", 503)
		return
	}
	type safe struct {
		World         string     `json:"world"`
		Profile       string     `json:"profile"`
		ClientType    string     `json:"client_type"`
		Version       string     `json:"version"`
		Status        string     `json:"status"`
		JoinAddress   string     `json:"join_address,omitempty"`
		ServerVersion string     `json:"server_version"`
		Maintenance   bool       `json:"maintenance"`
		PublishedAt   *time.Time `json:"published_at"`
	}
	out := make([]safe, 0, len(rs))
	now := time.Now()
	for _, v := range rs {
		info, err := s.store.PublicWorld(r.Context(), v.World)
		if err != nil {
			info = PublicWorld{Status: "offline", ServerVersion: "unknown"}
		}
		if !info.Enabled {
			continue
		}
		// The launcher gates play on this field, so it has to be the server's own
		// answer rather than a stored label: a stale "online" sends a player into a
		// dead server, and a stale "offline" locks them out of a healthy one.
		info = s.withLiveStatus(info, now)
		out = append(out, safe{
			World: v.World, Profile: v.Profile, ClientType: v.ClientType, Version: v.Version,
			Status: info.Status, JoinAddress: info.JoinAddress, ServerVersion: info.ServerVersion,
			Maintenance: v.Maintenance, PublishedAt: v.PublishedAt,
		})
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]any{"worlds": out})
}
func (s *Server) home(w http.ResponseWriter, r *http.Request) {
	steamID, ok := s.steamID(r)
	if !ok {
		render(w, loginPageTemplate, map[string]any{"IsAdmin": s.isAdmin(r), "SourceURL": s.cfg.SourceURL})
		return
	}
	worlds, err := s.store.PublicWorldsForSteam(r.Context(), steamID)
	if err != nil {
		s.playerError(w, r, http.StatusServiceUnavailable, errorPage{
			Title:   "The portal is not answering",
			Message: "Your worlds could not be loaded. Try again in a moment.",
		})
		return
	}
	// Each tile's dot is the server's own answer, not a label somebody last typed.
	now := time.Now()
	for i, world := range worlds {
		worlds[i] = s.withLiveStatus(world, now)
	}
	rs, err := s.store.CurrentReleases(r.Context())
	if err != nil {
		s.playerError(w, r, http.StatusServiceUnavailable, errorPage{
			Title:   "The portal is not answering",
			Message: "Your worlds could not be loaded. Try again in a moment.",
		})
		return
	}
	allowed := make(map[string]bool, len(worlds))
	for _, world := range worlds {
		allowed[world.Name] = true
	}
	filtered := rs[:0]
	for _, release := range rs {
		if allowed[release.World] {
			filtered = append(filtered, release)
		}
	}
	render(w, playerHomeTemplate, map[string]any{
		"Worlds": playerHomeWorlds(worlds, filtered), "IsAdmin": s.isAdmin(r), "SourceURL": s.cfg.SourceURL,
		"SteamID": steamID, "ClientUnavailable": s.clientDownloadProblem() != "",
	})
}
func (s *Server) world(w http.ResponseWriter, r *http.Request) {
	world := r.PathValue("world")
	if !s.requireWorldAccess(w, r, world) {
		return
	}
	rs, err := s.store.WorldReleases(r.Context(), world)
	if err != nil {
		http.Error(w, "unavailable", 503)
		return
	}
	// Either kind of evidence makes the world real: a registration row, or something
	// published for it. Gating the page on releases alone 404ed worlds that were
	// provisioned, registered, and enabled but had nothing published yet - and the
	// signed-in home page links every world the player may see, so the portal linked
	// to its own missing page. Only a name with neither is a 404.
	info, err := s.store.PublicWorld(r.Context(), world)
	if err != nil {
		if len(rs) == 0 {
			http.NotFound(w, r)
			return
		}
		// Published but never registered: it has no join address either, so the live
		// probe below is the only thing that can say anything true about it.
		info = PublicWorld{Name: world, Status: "offline", ServerVersion: "unknown"}
	}
	info = s.withLiveStatus(info, time.Now())
	cards, err := s.profileReleaseCards(r.Context(), rs)
	if err != nil {
		http.Error(w, "portal configuration unavailable", http.StatusServiceUnavailable)
		return
	}
	seed := "unavailable"
	if metadataSeed, ok := s.worldSeed(r.Context(), world); ok {
		seed = metadataSeed
	}
	render(w, playerWorldTemplate, map[string]any{
		"World": info, "Profiles": cards, "Seed": seed, "IsAdmin": s.isAdmin(r), "SourceURL": s.cfg.SourceURL,
		"ClientUnavailable": s.clientDownloadProblem() != "",
	})
}
func (s *Server) worldSeed(ctx context.Context, world string) (string, bool) {
	reply, err := s.agent.Run(ctx, randomID(), world, "world_metadata")
	if err != nil || reply.Status != "succeeded" {
		return "", false
	}
	var metadata struct {
		Seed string `json:"seed"`
	}
	if json.Unmarshal(reply.Data, &metadata) != nil || !worldSeedPattern.MatchString(metadata.Seed) {
		return "", false
	}
	return metadata.Seed, true
}

func (s *Server) worldHistory(w http.ResponseWriter, r *http.Request) {
	world := r.PathValue("world")
	if !s.requireWorldAccess(w, r, world) {
		return
	}
	releases, err := s.store.ArchivedWorldReleases(r.Context(), world)
	if err != nil {
		http.Error(w, "unavailable", 503)
		return
	}
	if len(releases) == 0 {
		http.NotFound(w, r)
		return
	}
	render(w, historyTemplate, map[string]any{"World": world, "Releases": releases})
}
func (s *Server) release(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	if !s.requireReleaseAccess(w, r, id, false) {
		return
	}
	as, err := s.store.PublishedArtifacts(r.Context(), id)
	if err != nil {
		http.Error(w, "unavailable", 503)
		return
	}
	if len(as) == 0 {
		http.NotFound(w, r)
		return
	}
	render(w, releaseTemplate, map[string]any{"ID": id, "Artifacts": as})
}

func (s *Server) manifest(w http.ResponseWriter, r *http.Request) {
	s.releaseManifest(w, r, s.store.PublishedArtifacts, false)
}
func (s *Server) historyRelease(w http.ResponseWriter, r *http.Request) {
	if !s.requireReleaseAccess(w, r, r.PathValue("id"), true) {
		return
	}
	artifacts, err := s.store.HistoricalArtifacts(r.Context(), r.PathValue("id"))
	if err != nil {
		http.Error(w, "unavailable", 503)
		return
	}
	if len(artifacts) == 0 {
		http.NotFound(w, r)
		return
	}
	render(w, releaseTemplate, map[string]any{"ID": r.PathValue("id"), "Artifacts": artifacts, "History": true})
}
func (s *Server) historyManifest(w http.ResponseWriter, r *http.Request) {
	s.releaseManifest(w, r, s.store.HistoricalArtifacts, true)
}
func (s *Server) releaseManifest(w http.ResponseWriter, r *http.Request, artifacts func(context.Context, string) ([]Artifact, error), historical bool) {
	id := r.PathValue("id")
	release, err := s.store.PublicRelease(r.Context(), id, historical)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	if !s.requireWorldAccess(w, r, release.World) {
		return
	}
	as, err := artifacts(r.Context(), id)
	if err != nil {
		http.Error(w, "unavailable", 503)
		return
	}
	if len(as) == 0 {
		http.NotFound(w, r)
		return
	}
	type item struct {
		Kind, Name, SHA256 string
		Size               int64
	}
	out := make([]item, 0, len(as))
	for _, artifact := range as {
		out = append(out, item{artifact.Kind, artifact.Name, artifact.SHA256, artifact.Size})
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]any{
		"release_id": id, "world": release.World, "profile": release.Profile,
		"client_type": release.ClientType, "version": release.Version, "notes": release.Notes,
		"published_at": release.PublishedAt, "published_by": release.PublishedBy,
		"historical": historical, "artifacts": out,
	})
}
func (s *Server) download(w http.ResponseWriter, r *http.Request) {
	a, err := s.store.PublishedArtifact(r.Context(), r.PathValue("id"))
	if err != nil {
		http.NotFound(w, r)
		return
	}
	if !s.requireReleaseAccess(w, r, a.ReleaseID, false) {
		return
	}
	s.serveArtifact(w, r, a)
}
func (s *Server) historyDownload(w http.ResponseWriter, r *http.Request) {
	a, err := s.store.HistoricalArtifact(r.Context(), r.PathValue("id"))
	if err != nil {
		http.NotFound(w, r)
		return
	}
	if !s.requireReleaseAccess(w, r, a.ReleaseID, true) {
		return
	}
	s.serveArtifact(w, r, a)
}
func (s *Server) serveArtifact(w http.ResponseWriter, r *http.Request, a Artifact) {
	root, err := filepath.EvalSymlinks(s.cfg.ArtifactRoot)
	if err != nil {
		http.Error(w, "unavailable", 503)
		return
	}
	path, err := filepath.EvalSymlinks(a.Path)
	if err != nil || !inside(root, path) {
		http.NotFound(w, r)
		return
	}
	f, err := os.Open(path)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil || info.Size() != a.Size {
		http.Error(w, "artifact integrity failure", 503)
		return
	}
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil || hex.EncodeToString(h.Sum(nil)) != a.SHA256 {
		http.Error(w, "artifact integrity failure", 503)
		return
	}
	f.Seek(0, 0)
	w.Header().Set("Content-Type", "application/octet-stream")
	w.Header().Set("Content-Disposition", fmt.Sprintf("attachment; filename=%q", a.Name))
	w.Header().Set("X-Checksum-SHA256", a.SHA256)
	http.ServeContent(w, r, a.Name, info.ModTime(), f)
}

// isAdmin reports whether the administration surface should be offered to this
// request. The operator arm is what lets a player page know, on its first
// render, that the signed-in Steam account may administer: the proxy blanks the
// identity and token headers on player routes by design, so provenAdmin is
// always false there and the answer has to come from the session instead.
func (s *Server) isAdmin(r *http.Request) bool {
	if _, ok := s.operatorSteamID(r); ok {
		return true
	}
	return s.provenAdmin(r) || s.hasAdminSession(r)
}

// operatorSteamID returns the SteamID64 of a signed-in portal operator.
//
// The identity is the Steam session the portal verified itself, so it does not
// depend on the proxy asserting anything. Membership is the explicit
// PORTAL_ADMIN_STEAM_IDS allowlist and nothing else: world_members.role='admin'
// is the in-game adminlist written into adminlist.txt, and treating it as
// portal authorisation would hand the control surface to every in-game admin.
func (s *Server) operatorSteamID(r *http.Request) (string, bool) {
	if len(s.cfg.AdminSteamIDs) == 0 {
		return "", false
	}
	steamID, ok := s.steamID(r)
	if !ok {
		return "", false
	}
	_, allowed := s.cfg.AdminSteamIDs[steamID]
	if !allowed {
		return "", false
	}
	return steamID, true
}

// provenAdmin demands all three independent factors: the request reached the
// portal from the trusted proxy, the proxy attached a verified identity, and
// the proxy attached the admin token. The first two are properties of a
// deployment the portal cannot check, which is why the third exists.
func (s *Server) provenAdmin(r *http.Request) bool {
	if !s.fromTrustedProxy(r) {
		return false
	}
	actor := strings.TrimSpace(r.Header.Get(s.cfg.AuthHeader))
	if actor == "" || len(actor) > 200 {
		return false
	}
	return hmac.Equal([]byte(strings.TrimSpace(r.Header.Get(adminTokenHeader))), s.adminToken)
}

func (s *Server) admin(next http.HandlerFunc) http.HandlerFunc {
	return s.guardAdmin(next, func() int64 { return s.formBodyLimit })
}

// adminUpload guards the one admin route that legitimately carries a large
// body, so the artifact ceiling never applies to a plain form POST.
func (s *Server) adminUpload(next http.HandlerFunc) http.HandlerFunc {
	return s.guardAdmin(next, func() int64 { return s.uploadBodyLimit })
}

func (s *Server) guardAdmin(next http.HandlerFunc, limit func() int64) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		// The ceiling has to be installed before anything touches the body.
		// validCSRF reads r.FormValue, and ParseForm and ParseMultipartForm are
		// documented no-ops on a second call, so a MaxBytesReader installed
		// inside the handler never sees a byte of the request.
		r.Body = http.MaxBytesReader(w, r.Body, limit())
		// Two independent routes in. The proxy route is retained as break-glass
		// for a deployment with no allowlist, or an operator locked out of Steam.
		operator, viaSteam := s.operatorSteamID(r)
		if !viaSteam && !s.provenAdmin(r) {
			http.Error(w, "admin authentication required", http.StatusUnauthorized)
			return
		}
		if r.Method != http.MethodGet {
			if err := parseAdminBody(r); err != nil {
				var tooLarge *http.MaxBytesError
				if errors.As(err, &tooLarge) {
					http.Error(w, "request body too large", http.StatusRequestEntityTooLarge)
					return
				}
				http.Error(w, "invalid form", http.StatusBadRequest)
				return
			}
			if !s.validCSRF(r) {
				http.Error(w, "invalid CSRF token", 403)
				return
			}
		}
		// Attribution must never be empty: the audit trail is the only record of
		// who did what, and the store rejects a blank actor.
		actor := strings.TrimSpace(r.Header.Get(s.cfg.AuthHeader))
		if viaSteam {
			actor = "steam:" + operator
		}
		r.Header.Set("X-Portal-Actor", actor)
		next(w, r)
	}
}

// parseAdminBody consumes the request body once, under the ceiling the guard
// installed. Multipart uploads spill past maxUploadMemoryBytes to temporary
// files rather than being held whole in memory.
func parseAdminBody(r *http.Request) error {
	if strings.HasPrefix(strings.ToLower(r.Header.Get("Content-Type")), "multipart/") {
		return r.ParseMultipartForm(maxUploadMemoryBytes)
	}
	return r.ParseForm()
}

func (s *Server) adminHome(w http.ResponseWriter, r *http.Request) {
	releases, err := s.store.allReleases(r.Context())
	if err != nil {
		http.Error(w, "unavailable", 503)
		return
	}
	jobs, err := s.store.RecentJobs(r.Context(), 20)
	if err != nil {
		http.Error(w, "unavailable", 503)
		return
	}
	audit, err := s.store.RecentAudit(r.Context(), 50)
	if err != nil {
		http.Error(w, "unavailable", 503)
		return
	}
	worlds, err := s.store.PublicWorlds(r.Context())
	if err != nil {
		http.Error(w, "unavailable", 503)
		return
	}
	identities, err := s.store.RecentSteamIdentities(r.Context(), 100)
	if err != nil {
		http.Error(w, "unavailable", 503)
		return
	}
	identityCount, err := s.store.SteamIdentityCount(r.Context())
	if err != nil {
		http.Error(w, "unavailable", 503)
		return
	}
	members, err := s.store.WorldMembers(r.Context())
	if err != nil {
		http.Error(w, "unavailable", 503)
		return
	}
	profiles := s.adminProfileCatalog(r.Context(), worlds)
	debugEnabled, err := s.store.DebugLoggingProfiles(r.Context())
	if err != nil {
		http.Error(w, "unavailable", 503)
		return
	}
	catalog, catalogErr := s.agentWorldCatalog(r.Context())
	registered := make(map[string]struct{}, len(worlds))
	for _, world := range worlds {
		registered[world.Name] = struct{}{}
	}
	unregistered := make([]worldCatalogEntry, 0, len(catalog))
	for _, entry := range catalog {
		if _, exists := registered[entry.Name]; !exists {
			unregistered = append(unregistered, entry)
		}
	}
	adminWorlds := make([]adminWorld, 0, len(worlds))
	// Operators see the same measured state as players; the stored field only
	// decides whether maintenance is being announced over the top of it.
	now := time.Now()
	for _, world := range worlds {
		live := s.withLiveStatus(world, now)
		adminWorlds = append(adminWorlds, adminWorld{PublicWorld: live, Port: currentJoinPort(world.JoinAddress)})
	}
	csrf := s.csrfCookie(w, r)
	s.setAdminSession(w)
	publicURL, _ := url.Parse(s.cfg.PublicBaseURL)
	players := playerCards(adminWorlds, members, identities, s.cfg.MapSourceRoot)
	accessPlans, err := s.store.WorldAccessPlans(r.Context())
	if err != nil {
		http.Error(w, "unavailable", 503)
		return
	}
	accessByWorld := make(map[string]WorldAccessPlan, len(accessPlans))
	pendingAccess := 0
	for _, plan := range accessPlans {
		accessByWorld[plan.World] = plan
		if !plan.InSync() {
			pendingAccess++
		}
	}
	pendingAgentRequests := 0
	if _, pending, err := s.store.AgentActivity(r.Context()); err == nil {
		pendingAgentRequests = pending
	}
	render(w, adminTemplate, map[string]any{
		"Releases": releases, "DraftReleases": draftReleaseChoices(releases), "Jobs": jobs, "Audit": audit, "Worlds": adminWorlds, "Identities": identities, "Members": members,
		"WorldCount": len(adminWorlds), "MemberCount": len(members), "ReleaseCount": len(releases), "JobCount": len(jobs),
		"UnregisteredWorlds": unregistered, "WorldCatalogError": catalogErr != nil, "SuggestedJoinHost": publicURL.Hostname(), "Profiles": profiles,
		"CSRF": csrf, "OperationPlan": operationPlan(), "DebugProfiles": debugProfileViews(releases, debugEnabled),
		"SteamAPIKeyConfigured": s.cfg.SteamAPIKey != "", "IdentityCount": identityCount,
		"Players": players, "PlayerCount": len(players),
		"Access": accessByWorld, "AccessPlans": accessPlans, "PendingAccess": pendingAccess,
		"ClientProblem": clientArtifactProblem(inspectClientExecutable(s.cfg.ClientExecutable)),
		"Seeds":         s.worldgenSeedDefaults(adminWorlds),
		// An approval nobody sees is an approval nobody gives: the count travels to the admin
		// home so a waiting request is visible without knowing the agent page exists.
		"PendingAgentRequests": pendingAgentRequests,
	})
}
func (s *Server) agentWorldCatalog(ctx context.Context) ([]worldCatalogEntry, error) {
	reply, err := s.agent.Run(ctx, randomID(), "", "world_catalog")
	if err != nil || reply.Status != "succeeded" || len(reply.Data) == 0 {
		return nil, errors.New("agent world catalog unavailable")
	}
	var catalog []worldCatalogEntry
	if err := json.Unmarshal(reply.Data, &catalog); err != nil {
		return nil, err
	}
	for index := range catalog {
		entry := &catalog[index]
		if !validWorld(entry.Name) || entry.Port < 1024 || entry.Port > 65533 ||
			(entry.Status != "online" && entry.Status != "offline") {
			return nil, errors.New("agent returned invalid world catalog")
		}
		entry.EndPort = entry.Port + 2
	}
	return catalog, nil
}

func (s *Server) registerWorld(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseForm(); err != nil {
		http.Error(w, "invalid form", http.StatusBadRequest)
		return
	}
	name := strings.TrimSpace(r.FormValue("world"))
	host := strings.TrimSpace(r.FormValue("join_host"))
	port, err := strconv.Atoi(strings.TrimSpace(r.FormValue("port")))
	if !validWorld(name) || !validJoinHost(host) || err != nil || port < 1024 || port > 65533 {
		http.Error(w, "invalid existing world registration", http.StatusBadRequest)
		return
	}
	catalog, err := s.agentWorldCatalog(r.Context())
	if err != nil {
		http.Error(w, "agent world catalog unavailable", http.StatusBadGateway)
		return
	}
	status := ""
	for _, entry := range catalog {
		if entry.Name == name && entry.Port == port {
			status = entry.Status
			break
		}
	}
	if status == "" {
		http.Error(w, "world is not in the current agent catalog", http.StatusConflict)
		return
	}
	if err := s.store.CreateProvisionedWorld(r.Context(), PublicWorld{
		Name: name, JoinAddress: net.JoinHostPort(host, strconv.Itoa(port)), Status: status, ServerVersion: "unknown",
	}, r.Header.Get("X-Portal-Actor")); err != nil {
		if strings.Contains(strings.ToLower(err.Error()), "unique") {
			http.Error(w, "world is already registered", http.StatusConflict)
			return
		}
		http.Error(w, "unable to register world", http.StatusInternalServerError)
		return
	}
	http.Redirect(w, r, "/admin", http.StatusSeeOther)
}

func (s *Server) createRelease(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseForm(); err != nil {
		http.Error(w, "invalid form", 400)
		return
	}
	release := Release{ID: randomID(), World: r.FormValue("world"), Profile: r.FormValue("profile"), ClientType: r.FormValue("client_type"), Version: r.FormValue("version"), Notes: r.FormValue("notes")}
	if err := s.store.CreateRelease(r.Context(), release, r.Header.Get("X-Portal-Actor")); err != nil {
		http.Error(w, err.Error(), 400)
		return
	}
	http.Redirect(w, r, "/admin", 303)
}
func (s *Server) grantWorldMember(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseForm(); err != nil {
		http.Error(w, "invalid form", http.StatusBadRequest)
		return
	}
	steamID, actor := r.FormValue("steam_id"), r.Header.Get("X-Portal-Actor")
	label := strings.TrimSpace(r.FormValue("label"))
	if !validSteamLabel(label) {
		http.Error(w, "invalid player name", http.StatusBadRequest)
		return
	}
	if err := s.store.GrantWorldAccess(r.Context(), r.FormValue("world"), steamID, actor); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	if label != "" {
		if err := s.store.SetSteamLabel(r.Context(), steamID, label, actor); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
	}
	// Name the account during the grant so the operator can confirm on the
	// next page load that they approved the person they meant to.
	s.syncSteamPersonas(r.Context(), []string{steamID})
	http.Redirect(w, r, "/admin", http.StatusSeeOther)
}

func (s *Server) labelSteamIdentity(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseForm(); err != nil {
		http.Error(w, "invalid form", http.StatusBadRequest)
		return
	}
	label := strings.TrimSpace(r.FormValue("label"))
	if err := s.store.SetSteamLabel(r.Context(), r.FormValue("steam_id"), label, r.Header.Get("X-Portal-Actor")); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	http.Redirect(w, r, "/admin", http.StatusSeeOther)
}

func (s *Server) refreshSteamIdentities(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseForm(); err != nil {
		http.Error(w, "invalid form", http.StatusBadRequest)
		return
	}
	steamIDs, err := s.store.SteamIdentitiesToName(r.Context())
	if err != nil {
		http.Error(w, "unavailable", http.StatusServiceUnavailable)
		return
	}
	s.syncSteamPersonas(r.Context(), steamIDs)
	http.Redirect(w, r, "/admin", http.StatusSeeOther)
}

func (s *Server) revokeWorldMember(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseForm(); err != nil {
		http.Error(w, "invalid form", http.StatusBadRequest)
		return
	}
	if err := s.store.RevokeWorldAccess(r.Context(), r.FormValue("world"), r.FormValue("steam_id"), r.Header.Get("X-Portal-Actor")); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	http.Redirect(w, r, "/admin", http.StatusSeeOther)
}
func (s *Server) uploadArtifact(w http.ResponseWriter, r *http.Request) {
	// The admin guard already capped this body at s.uploadBodyLimit and parsed
	// the multipart form under that cap, spilling anything past
	// maxUploadMemoryBytes to a temporary file.
	releaseID, kind := r.FormValue("release_id"), r.FormValue("kind")
	file, header, err := r.FormFile("artifact")
	if err != nil {
		http.Error(w, "artifact required", 400)
		return
	}
	defer file.Close()
	if !validArtifactKind(kind) || !validFilename(header.Filename) || !validID(releaseID) {
		http.Error(w, "invalid artifact", 400)
		return
	}
	dstDir := filepath.Join(s.cfg.ArtifactRoot, "drafts", releaseID)
	if err := os.MkdirAll(dstDir, 0o750); err != nil {
		http.Error(w, "storage failure", 500)
		return
	}
	dst := filepath.Join(dstDir, randomID()+"-"+header.Filename)
	out, err := os.OpenFile(dst, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o640)
	if err != nil {
		http.Error(w, "storage failure", 500)
		return
	}
	h := sha256.New()
	size, copyErr := io.Copy(io.MultiWriter(out, h), file)
	closeErr := out.Close()
	if copyErr != nil || closeErr != nil || size < 1 {
		os.Remove(dst)
		http.Error(w, "upload failed", 400)
		return
	}
	a := Artifact{ID: randomID(), ReleaseID: releaseID, Kind: kind, Name: header.Filename, SHA256: hex.EncodeToString(h.Sum(nil)), Size: size, Path: dst}
	if err = s.store.AddArtifact(r.Context(), a, r.Header.Get("X-Portal-Actor")); err != nil {
		os.Remove(dst)
		http.Error(w, err.Error(), 400)
		return
	}
	http.Redirect(w, r, "/admin", 303)
}
func (s *Server) publish(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	if err := s.store.Publish(r.Context(), id, r.Header.Get("X-Portal-Actor")); err != nil {
		http.Error(w, err.Error(), 400)
		return
	}
	http.Redirect(w, r, "/admin", 303)
}
func (s *Server) archive(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	if err := s.store.Archive(r.Context(), id, r.Header.Get("X-Portal-Actor")); err != nil {
		http.Error(w, err.Error(), 400)
		return
	}
	http.Redirect(w, r, "/admin", 303)
}

// batchPublishFlat publishes a scoped Flat release set. Repeating the same
// request is safe: already-published matching releases are skipped.
func (s *Server) batchPublishFlat(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseForm(); err != nil {
		http.Error(w, "invalid form", http.StatusBadRequest)
		return
	}
	ids := r.Form["release_id"]
	if len(ids) == 0 || len(ids) > 16 {
		http.Error(w, "one to sixteen release IDs are required", http.StatusBadRequest)
		return
	}
	seen := make(map[string]struct{}, len(ids))
	releases := make([]Release, 0, len(ids))
	for _, id := range ids {
		if !validIdentifier(id) {
			http.Error(w, "invalid release ID", http.StatusBadRequest)
			return
		}
		if _, duplicate := seen[id]; duplicate {
			http.Error(w, "duplicate release ID", http.StatusBadRequest)
			return
		}
		seen[id] = struct{}{}
		release, err := s.store.Release(r.Context(), id)
		if err != nil || release.ClientType != "flat" || (release.Status != Draft && release.Status != Published) {
			http.Error(w, "all releases must be Flat drafts or prior published releases", http.StatusBadRequest)
			return
		}
		releases = append(releases, release)
	}
	actor := r.Header.Get("X-Portal-Actor")
	for _, release := range releases {
		if release.Status == Published {
			continue
		}
		if err := s.store.Publish(r.Context(), release.ID, actor); err != nil {
			http.Error(w, fmt.Sprintf("release %s: %v", release.ID, err), http.StatusBadRequest)
			return
		}
	}
	http.Redirect(w, r, "/admin", http.StatusSeeOther)
}

func (s *Server) discardDraft(w http.ResponseWriter, r *http.Request) {
	if err := s.store.ArchiveDraft(r.Context(), r.PathValue("id"), r.Header.Get("X-Portal-Actor")); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	http.Redirect(w, r, "/admin", http.StatusSeeOther)
}
func (s *Server) runJob(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseForm(); err != nil {

		http.Error(w, "invalid form", 400)
		return
	}
	world, op := r.FormValue("world"), r.FormValue("operation")
	if !validWorld(world) || !allowedOperation(op) || op == "delete_server" {
		http.Error(w, "invalid operation", 400)
		return
	}
	actor := r.Header.Get("X-Portal-Actor")
	// world_analysis is not a script the agent runs and forgets: it returns a parsed snapshot that
	// has to be saved and rendered into map tiles. Sent through the generic path below it reported
	// "succeeded" with an empty detail and published nothing, because that path records reply.Output
	// and the snapshot arrives in reply.Data - so the operator's refresh did nothing at all and said
	// it worked. The map then showed structures from whenever a server last started.
	if op == "world_analysis" {
		// The job form asks for the same thing the map page's button does: fresh data, reusing
		// terrain when it is already correct.
		if failure := s.runWorldAnalysisJob(r.Context(), world, actor, true, false); failure != nil {
			http.Error(w, failure.client, failure.status)
			return
		}
		http.Redirect(w, r, "/admin/worlds/"+world+"/map", 303)
		return
	}
	id := randomID()
	if err := s.store.CreateJob(r.Context(), Job{ID: id, World: world, Operation: op, Status: "queued", RequestedBy: actor}, actor); err != nil {
		http.Error(w, "unable to queue job", 500)
		return
	}
	reply, err := s.agent.Run(r.Context(), id, world, op)
	if err != nil {
		s.store.FinishJob(r.Context(), id, "failed", "agent request failed", actor)
		http.Error(w, "agent unavailable", 502)
		return
	}
	s.store.FinishJob(r.Context(), id, reply.Status, reply.Output, actor)
	if reply.Status == "succeeded" && (op == "start" || op == "restart") {
		if failure := s.ensureInitialWorldMap(r.Context(), world, actor); failure != nil {
			http.Error(w, "server started but automatic map generation failed: "+failure.client, failure.status)
			return
		}
	}
	http.Redirect(w, r, "/admin", 303)
}

func (s *Server) setWorldStatus(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseForm(); err != nil {
		http.Error(w, "invalid form", http.StatusBadRequest)
		return
	}
	world, status := r.PathValue("world"), r.FormValue("status")
	if !validWorld(world) {
		http.NotFound(w, r)
		return
	}
	info, err := s.store.PublicWorld(r.Context(), world)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	info.Status = status
	if err := s.store.UpsertPublicWorld(r.Context(), info, r.Header.Get("X-Portal-Actor")); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	http.Redirect(w, r, "/admin", http.StatusSeeOther)
}

// setWorldDescription stores the operator's player-facing blurb for one world.
// Browsers submit textarea line breaks as CRLF, which the store rejects as a
// control character, so the carriage returns are folded away before saving.
func (s *Server) setWorldDescription(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseForm(); err != nil {
		http.Error(w, "invalid form", http.StatusBadRequest)
		return
	}
	world := r.PathValue("world")
	if !validWorld(world) {
		http.NotFound(w, r)
		return
	}
	info, err := s.store.PublicWorld(r.Context(), world)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	info.Description = strings.TrimSpace(descriptionLineBreaks.Replace(r.FormValue("description")))
	if err := s.store.UpsertPublicWorld(r.Context(), info, r.Header.Get("X-Portal-Actor")); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	http.Redirect(w, r, "/admin", http.StatusSeeOther)
}

var descriptionLineBreaks = strings.NewReplacer("\r\n", "\n", "\r", "\n")

func (s *Server) setWorldEnabled(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseForm(); err != nil {
		http.Error(w, "invalid form", http.StatusBadRequest)
		return
	}
	world := r.PathValue("world")
	enabled, err := strconv.ParseBool(r.FormValue("enabled"))
	if !validWorld(world) || err != nil {
		http.Error(w, "invalid world state", http.StatusBadRequest)
		return
	}
	if err := s.store.SetPublicWorldEnabled(r.Context(), world, enabled, r.Header.Get("X-Portal-Actor")); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			http.NotFound(w, r)
			return
		}
		http.Error(w, "unable to update world state", http.StatusInternalServerError)
		return
	}
	http.Redirect(w, r, "/admin", http.StatusSeeOther)
}

// setProfileDebugLogging toggles verbose client diagnostics for one world/profile
// and immediately republishes the affected profiles with a bumped patch version,
// because a published profile definition is immutable: the flag can only reach a
// client through a new release.
func (s *Server) setProfileDebugLogging(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseForm(); err != nil {
		http.Error(w, "invalid form", http.StatusBadRequest)
		return
	}
	world := r.PathValue("world")
	profile := r.PathValue("profile")
	enabled, err := strconv.ParseBool(r.FormValue("enabled"))
	if !validWorld(world) || !validProfile(profile) || err != nil {
		http.Error(w, "invalid debug logging request", http.StatusBadRequest)
		return
	}
	actor := r.Header.Get("X-Portal-Actor")
	if err := s.store.SetProfileDebugLogging(r.Context(), world, profile, enabled, actor); err != nil {
		http.Error(w, "unable to update debug logging", http.StatusInternalServerError)
		return
	}
	// A profile is bound to a client type by its releases, and the admin view is
	// keyed by world and profile alone, so every published client type is
	// republished. Absent client types are skipped rather than treated as errors.
	published := 0
	for _, clientType := range []string{"vr", "flat"} {
		if _, err := s.store.CurrentRelease(r.Context(), world, profile, clientType); err != nil {
			continue
		}
		if _, err := s.republishWithDebugLogging(r.Context(), world, profile, clientType, actor, enabled); err != nil {
			http.Error(w, "debug logging saved, but publishing a release failed: "+err.Error(), http.StatusInternalServerError)
			return
		}
		published++
	}
	if published == 0 {
		http.Error(w, "debug logging saved, but this profile has no published release to update", http.StatusConflict)
		return
	}
	http.Redirect(w, r, "/admin", http.StatusSeeOther)
}

func (s *Server) setWorldPort(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseForm(); err != nil {
		http.Error(w, "invalid form", http.StatusBadRequest)
		return
	}
	world := r.PathValue("world")
	port, err := strconv.Atoi(r.FormValue("port"))
	if !validWorld(world) || err != nil || port < 1024 || port > 65533 {
		http.Error(w, "game port must be between 1024 and 65533", http.StatusBadRequest)
		return
	}
	info, err := s.store.PublicWorld(r.Context(), world)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	host, _, err := net.SplitHostPort(info.JoinAddress)
	if err != nil || host == "" {
		http.Error(w, "world join address is not host:port", http.StatusConflict)
		return
	}
	actor, jobID := r.Header.Get("X-Portal-Actor"), randomID()
	if err := s.store.CreateJob(r.Context(), Job{ID: jobID, World: world, Operation: "set_port", Status: "queued", RequestedBy: actor, Detail: strconv.Itoa(port)}, actor); err != nil {
		http.Error(w, "unable to queue port change", http.StatusInternalServerError)
		return
	}
	reply, err := s.agent.RunWithPort(r.Context(), jobID, world, port)
	if err != nil {
		_ = s.store.FinishJob(r.Context(), jobID, "failed", "agent request failed", actor)
		http.Error(w, "agent unavailable", http.StatusBadGateway)
		return
	}
	_ = s.store.FinishJob(r.Context(), jobID, reply.Status, reply.Output, actor)
	if reply.Status != "succeeded" {
		http.Error(w, "port change failed; see the job output", http.StatusConflict)
		return
	}
	info.JoinAddress = net.JoinHostPort(host, strconv.Itoa(port))
	if err := s.store.UpsertPublicWorld(r.Context(), info, actor); err != nil {
		http.Error(w, "port changed but public join address update failed", http.StatusInternalServerError)
		return
	}
	http.Redirect(w, r, "/admin", http.StatusSeeOther)
}
func (s *Server) worldRemovalConfirmation(w http.ResponseWriter, r *http.Request) {
	world := r.PathValue("world")
	if !validWorld(world) {
		http.NotFound(w, r)
		return
	}
	info, err := s.store.PublicWorld(r.Context(), world)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	render(w, worldRemovalTemplate, map[string]any{"World": info, "CSRF": s.csrfCookie(w, r)})
}

func (s *Server) confirmWorldRemoval(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseForm(); err != nil {
		http.Error(w, "invalid form", http.StatusBadRequest)
		return
	}
	world, mode := r.PathValue("world"), r.FormValue("mode")
	if !validWorld(world) {
		http.NotFound(w, r)
		return
	}
	actor := r.Header.Get("X-Portal-Actor")
	switch mode {
	case "unregister":
		if r.FormValue("confirmation") != "UNREGISTER "+world {
			http.Error(w, "confirmation does not match", http.StatusBadRequest)
			return
		}
		if err := s.store.UnregisterPublicWorld(r.Context(), world, actor); err != nil {
			if errors.Is(err, sql.ErrNoRows) {
				http.NotFound(w, r)
				return
			}
			http.Error(w, "unable to unregister world", http.StatusInternalServerError)
			return
		}
	case "delete":
		if r.FormValue("confirmation") != "DELETE "+world {
			http.Error(w, "confirmation does not match", http.StatusBadRequest)
			return
		}
		if err := s.store.SetPublicWorldEnabled(r.Context(), world, false, actor); err != nil {
			if errors.Is(err, sql.ErrNoRows) {
				http.NotFound(w, r)
				return
			}
			http.Error(w, "unable to disable world before deletion", http.StatusInternalServerError)
			return
		}
		jobID := randomID()
		if err := s.store.CreateJob(r.Context(), Job{
			ID: jobID, World: world, Operation: "delete_server", Status: "queued", RequestedBy: actor,
			Detail: "final backup, stop, and permanent server directory deletion",
		}, actor); err != nil {
			http.Error(w, "unable to queue server deletion; player access remains disabled", http.StatusInternalServerError)
			return
		}
		reply, err := s.agent.Run(r.Context(), jobID, world, "delete_server")
		if err != nil {
			_ = s.store.FinishJob(r.Context(), jobID, "failed", "agent request failed", actor)
			http.Error(w, "agent unavailable; server registration retained and player access remains disabled", http.StatusBadGateway)
			return
		}
		_ = s.store.FinishJob(r.Context(), jobID, reply.Status, reply.Output, actor)
		if reply.Status != "succeeded" {
			http.Error(w, "server deletion stopped safely; inspect the job output before retrying", http.StatusConflict)
			return
		}
		if err := s.store.RetirePublicWorld(r.Context(), world, actor); err != nil {
			http.Error(w, "server files were deleted but portal cleanup failed; unregister the retained entry", http.StatusInternalServerError)
			return
		}
	default:
		http.Error(w, "invalid removal mode", http.StatusBadRequest)
		return
	}
	http.Redirect(w, r, "/admin", http.StatusSeeOther)
}

func (s *Server) prepareRestore(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseForm(); err != nil {
		http.Error(w, "invalid form", http.StatusBadRequest)
		return
	}
	world, backup := r.FormValue("world"), r.FormValue("backup")
	if !validWorld(world) || !validRestoreBackup(world, backup) {
		http.Error(w, "invalid restore request", http.StatusBadRequest)
		return
	}
	id := randomID()
	actor := r.Header.Get("X-Portal-Actor")
	s.restoreMu.Lock()
	for key, request := range s.restores {
		if time.Now().After(request.ExpiresAt) {
			delete(s.restores, key)
		}
	}
	s.restores[id] = restoreRequest{Actor: actor, World: world, Backup: backup, ExpiresAt: time.Now().Add(10 * time.Minute)}
	s.restoreMu.Unlock()
	if err := s.store.Audit(r.Context(), actor, "restore.prepare", id, world+":"+backup); err != nil {
		http.Error(w, "unable to record restore request", http.StatusInternalServerError)
		return
	}
	http.Redirect(w, r, "/admin/restores/"+id, http.StatusSeeOther)
}

func (s *Server) restoreConfirmation(w http.ResponseWriter, r *http.Request) {
	request, ok := s.restoreRequest(r.PathValue("id"), r.Header.Get("X-Portal-Actor"), false)
	if !ok {
		http.NotFound(w, r)
		return
	}
	render(w, restoreTemplate, map[string]any{"ID": r.PathValue("id"), "Restore": request, "CSRF": s.csrfCookie(w, r)})
}

func (s *Server) confirmRestore(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseForm(); err != nil {
		http.Error(w, "invalid form", http.StatusBadRequest)
		return
	}
	actor := r.Header.Get("X-Portal-Actor")
	request, ok := s.restoreRequest(r.PathValue("id"), actor, true)
	if !ok {
		http.NotFound(w, r)
		return
	}
	if r.FormValue("confirmation") != "RESTORE "+request.World+" "+request.Backup {
		http.Error(w, "confirmation does not match", http.StatusBadRequest)
		return
	}
	jobID := randomID()
	if err := s.store.CreateJob(r.Context(), Job{ID: jobID, World: request.World, Operation: "restore", Status: "queued", RequestedBy: actor}, actor); err != nil {
		http.Error(w, "unable to queue restore", http.StatusInternalServerError)
		return
	}
	if err := s.store.Audit(r.Context(), actor, "restore.confirm", jobID, request.World+":"+request.Backup); err != nil {
		http.Error(w, "unable to record restore", http.StatusInternalServerError)
		return
	}
	reply, err := s.agent.RunWithBackup(r.Context(), jobID, request.World, "restore", request.Backup)
	if err != nil {
		_ = s.store.FinishJob(r.Context(), jobID, "failed", "agent request failed", actor)
		http.Error(w, "agent unavailable", http.StatusBadGateway)
		return
	}
	if err := s.store.FinishJob(r.Context(), jobID, reply.Status, reply.Output, actor); err != nil {
		http.Error(w, "unable to finish restore job", http.StatusInternalServerError)
		return
	}
	http.Redirect(w, r, "/admin", http.StatusSeeOther)
}

func (s *Server) restoreRequest(id, actor string, consume bool) (restoreRequest, bool) {
	s.restoreMu.Lock()
	defer s.restoreMu.Unlock()
	request, ok := s.restores[id]
	if !ok || request.Actor != actor || time.Now().After(request.ExpiresAt) {
		delete(s.restores, id)
		return restoreRequest{}, false
	}
	if consume {
		delete(s.restores, id)
	}
	return request, true
}

func validRestoreBackup(world, backup string) bool {
	return strings.HasPrefix(backup, "world-"+world+"-") && strings.HasSuffix(backup, ".tgz") && filepath.Base(backup) == backup && len(backup) <= 180
}

// allowedOperation gates the generic POST /admin/jobs runner. "world_create" is
// deliberately absent and must stay absent: recreating a world destroys the
// current save, so it is reachable only through the typed confirmation flow in
// worldgen.go. Adding it here would expose it as a one-click form post.
func allowedOperation(v string) bool {
	switch v {
	case "status", "logs", "backups", "start", "stop", "restart", "pause", "resume", "backup", "build", "restore", "provision",
		"mod_inventory", "mod_search", "mod_custom_list", "mod_add", "mod_remove", "mod_enable", "mod_disable",
		"mod_custom_add", "mod_custom_remove", "mod_custom_enable", "mod_custom_disable", "mod_deploy", "delete_server", "world_analysis",
		"access_apply", "set_port":
		return true
	}
	return false
}

// recordableOperation is every operation the job log may record, including the
// ones the generic runner refuses to dispatch.
func recordableOperation(v string) bool { return v == "world_create" || allowedOperation(v) }

func operationPlan() string {
	return "status → status_valheim_server.sh; logs → logs_valheim_server_snapshot.sh; backups → list_valheim_world_backups.sh; start → start_valheim_server.sh; stop → backup then stop; restart → backup, stop, then start; pause → pause_valheim_server.sh; resume → unpause_valheim_server.sh; backup → backup_valheim_world.sh; build → backup then build_valheim_server.sh; restore → explicit two-step confirmation, fresh backup, stop, then restore_valheim_world.sh; delete server → separate typed confirmation, final backup, stop, then portal_delete_valheim_server.sh; recreate world from a seed → separate typed confirmation, then portal_create_valheim_world.sh, which archives the save, regenerates on the forced seed, and reads the seed back out of the new world file; access lists → portal_access_lists.sh, generated from portal membership and applied without a restart"
}
func (s *Server) csrfCookie(w http.ResponseWriter, r *http.Request) string {
	var nonce string
	if c, err := r.Cookie("portal_csrf"); err == nil && len(c.Value) == 64 {
		nonce = c.Value
	} else {
		nonce = randomHex(32)
		http.SetCookie(w, &http.Cookie{Name: "portal_csrf", Value: nonce, Path: "/admin", HttpOnly: true, Secure: s.cfg.CookieSecure, SameSite: http.SameSiteStrictMode, MaxAge: 3600})
	}
	return s.csrfToken(nonce)
}
func (s *Server) csrfToken(nonce string) string {
	mac := hmac.New(sha256.New, s.csrf)
	mac.Write([]byte(nonce))
	return hex.EncodeToString(mac.Sum(nil))
}
func (s *Server) validCSRF(r *http.Request) bool {
	c, err := r.Cookie("portal_csrf")
	if err != nil || len(c.Value) != 64 {
		return false
	}
	return subtleEqual(s.csrfToken(c.Value), r.FormValue("csrf"))
}
func render(w http.ResponseWriter, text string, data any) {
	const styleLink = `<link rel="stylesheet" href="/assets/site.css">`
	if strings.Contains(text, "</head>") {
		text = strings.Replace(text, "</head>", styleLink+"</head>", 1)
	} else {
		text = strings.Replace(text, "</title>", "</title>"+styleLink, 1)
	}
	t, err := template.New("page").Parse(text)
	if err != nil {
		http.Error(w, "template error", 500)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	if err = t.Execute(w, data); err != nil {
		http.Error(w, "render failed", 500)
	}
}
func randomID() string { return randomHex(16) }
func randomHex(n int) string {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		panic(err)
	}
	return hex.EncodeToString(b)
}

func (s *Server) portalURL(route string) (string, error) {
	return portalURL(s.cfg.PublicBaseURL, route)
}

func portalURL(baseURL, route string) (string, error) {
	base, err := url.Parse(baseURL)
	if err != nil || base.Scheme != "https" || base.Host == "" || base.User != nil || base.RawQuery != "" || base.Fragment != "" {
		return "", errors.New("portal URL must be an HTTPS origin or base path")
	}
	if route != "" && (!strings.HasPrefix(route, "/") || strings.ContainsAny(route, "?#")) {
		return "", errors.New("invalid portal route")
	}
	base.Path = strings.TrimRight(base.Path, "/") + route
	base.RawPath = ""
	return base.String(), nil
}

func (s *Server) profileSyncURL(release Release) (string, error) {
	if !validWorld(release.World) || !validProfile(release.Profile) || (release.ClientType != "flat" && release.ClientType != "vr") {
		return "", errors.New("invalid profile release")
	}
	portal, err := s.portalURL("")
	if err != nil {
		return "", err
	}
	link := &url.URL{Scheme: "valheim-profile-sync", Host: "sync"}
	query := link.Query()
	query.Set("portal", portal)
	query.Set("world", release.World)
	query.Set("profile", release.Profile)
	query.Set("client_type", release.ClientType)
	link.RawQuery = query.Encode()
	return link.String(), nil
}
func inside(root, path string) bool {
	rel, err := filepath.Rel(root, path)
	return err == nil && rel != ".." && !strings.HasPrefix(rel, ".."+string(os.PathSeparator))
}
func clientIP(r *http.Request) string {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err == nil {
		return host
	}
	return r.RemoteAddr
}
func (s *Server) fromTrustedProxy(r *http.Request) bool {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		host = r.RemoteAddr
	}
	addr, err := netip.ParseAddr(host)
	return err == nil && s.trustedProxy.Contains(addr)
}
func subtleEqual(a, b string) bool {
	if len(a) != len(b) {
		return false
	}
	var v byte
	for i := range a {
		v |= a[i] ^ b[i]
	}
	return v == 0
}

type rateLimiter struct {
	mu   sync.Mutex
	hits map[string][]time.Time
	n    int
	span time.Duration
}

func newRateLimiter(n int, span time.Duration) *rateLimiter {
	return &rateLimiter{hits: map[string][]time.Time{}, n: n, span: span}
}
func (l *rateLimiter) Allow(key string) bool {
	l.mu.Lock()
	defer l.mu.Unlock()
	now := time.Now()
	old := l.hits[key]
	keep := old[:0]
	for _, t := range old {
		if now.Sub(t) < l.span {
			keep = append(keep, t)
		}
	}
	if len(keep) >= l.n {
		l.hits[key] = keep
		return false
	}
	l.hits[key] = append(keep, now)
	return true
}

const homeTemplate = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="/favicon.ico" sizes="any"><link rel="manifest" href="/site.webmanifest"><meta name="theme-color" content="#123728"><title>Valheim Profile Sync</title>
<style>
:root{color-scheme:dark;--ink:#eef7f1;--muted:#afc4b5;--forest:#102a20;--pine:#1b4d39;--moss:#71c492;--line:#ffffff1e;--panel:#173a2c}
*{box-sizing:border-box}body{min-height:100vh;margin:0;background:radial-gradient(circle at 82% -10%,#368b6199,transparent 38rem),linear-gradient(140deg,#081911,#123728 48%,#07140e);color:var(--ink);font:16px/1.55 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}a{color:inherit}.shell{width:min(1120px,calc(100% - 2rem));margin:auto}.hero{padding:clamp(3.5rem,10vw,7rem) 0 3.5rem;display:grid;grid-template-columns:1.3fr .7fr;gap:2rem;align-items:end}.brand{display:flex;align-items:center;gap:.65rem;color:var(--moss);font-size:.78rem;font-weight:800;letter-spacing:.13em;text-transform:uppercase}.rune{display:grid;place-items:center;width:2rem;height:2rem;border:1px solid #86d8a6;border-radius:50%;font-size:1.1rem}.hero h1{max-width:11ch;margin:.7rem 0 1rem;font-size:clamp(3rem,7vw,5.7rem);line-height:.94;letter-spacing:-.065em}.hero p{max-width:48ch;margin:0;color:var(--muted);font-size:1.08rem}.install{padding:1.35rem;border:1px solid var(--line);border-radius:1rem;background:#ffffff0b;backdrop-filter:blur(12px)}.install h2{margin:0 0 .4rem;font-size:1rem}.install p{margin:0 0 1rem;font-size:.92rem}.button{display:inline-flex;align-items:center;justify-content:center;min-height:2.7rem;padding:.65rem 1rem;border-radius:.55rem;background:var(--moss);color:#082116;font-weight:800;text-decoration:none}.button:hover{background:#9ee3b7}.content{padding:2.5rem 0 2.5rem;margin-bottom:3rem}.section-head{display:flex;align-items:baseline;justify-content:space-between;gap:1rem;margin:0 0 1rem}.section-head h2{margin:0;font-size:1.35rem}.section-head span{color:var(--muted);font-size:.9rem}.worlds{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:1rem}.world{display:flex;min-height:13rem;flex-direction:column;justify-content:space-between;padding:1.35rem;border:1px solid var(--line);border-radius:1rem;background:linear-gradient(155deg,#214c39,#143728);text-decoration:none;transition:transform .15s ease,border-color .15s ease}.world:hover{transform:translateY(-3px);border-color:#94e3b3}.world h3{margin:0;font-size:1.5rem;letter-spacing:-.035em}.world p{margin:.5rem 0 0;color:#c7dccd}.meta{display:flex;justify-content:space-between;gap:.75rem;align-items:end;font-size:.84rem;color:var(--muted)}.status{display:inline-flex;align-items:center;gap:.4rem;color:#d9f6e3}.status::before{content:"";width:.55rem;height:.55rem;border-radius:50%;background:#f1ba62}.profile-types{display:flex;flex-wrap:wrap;gap:.4rem}.chip-empty{border-color:var(--line);color:var(--muted)}.chip{flex:none;padding:.25rem .55rem;border:1px solid #9bdfb266;border-radius:999px;color:#aaf0c1;font-size:.72rem;font-weight:800;text-transform:uppercase}.empty{padding:1.5rem;border:1px dashed var(--line);border-radius:.8rem;color:var(--muted)}@media(max-width:700px){.hero{grid-template-columns:1fr;padding-top:3rem}.hero h1{max-width:13ch}.install{max-width:30rem}.profile{align-items:flex-start;flex-direction:column}.meta{align-items:flex-start;flex-direction:column;gap:.25rem}}
.brand{font-size:0}.brand::after{content:"Gaming";font-size:.78rem}.brand .rune{width:10rem;height:2.2rem;border:0;border-radius:0;background:url("/assets/neuralyze-logo.svg") center/contain no-repeat;font-size:0}
.world .world-description{display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:3;overflow:hidden;color:var(--muted);font-size:.92rem}
</style></head><body><main class="shell"><header class="hero"><div><div class="brand"><span class="rune">ᛉ</span>Neuralyze gaming</div><h1>Your Valheim profiles, kept in sync.</h1><p>Choose an approved world and profile. Valheim Profile Sync verifies the selected files, updates only what changed, creates a Desktop shortcut for that profile, and launches your existing Steam Valheim installation.</p></div><aside class="install"><h2>Install once, then use your shortcut</h2><ol><li>Download and run Valheim Profile Sync once.</li><li>Open a world below and choose how you play: Desktop, Desktop VR-compatible, or VR headset.</li><li>Choose <strong>Install or update</strong>. The app synchronizes and launches it.</li></ol><p><strong>A shortcut is made on your Desktop.</strong> Use that shortcut whenever you play; it checks for profile updates before starting Valheim.</p>{{if .ClientUnavailable}}<button class="button" type="button" disabled>Download for Windows</button><p class="install-note">The Windows app is not available to download right now. Ask the world owner to publish it.</p>{{else}}<a class="button" href="/client/ValheimProfileSync.exe">Download for Windows</a>{{end}}</aside></header><section class="content"><div class="section-head"><h2>Your worlds</h2><span>Choose a world, then how you play</span></div><div class="worlds">{{range .Worlds}}<a class="world" href="/worlds/{{.Name}}"><div><h3>{{.Name}}</h3><p>{{if .JoinAddress}}{{.JoinAddress}}{{else}}Connection details pending{{end}}</p>{{if .Description}}<p class="world-description">{{.Description}}</p>{{end}}</div><div class="profile-types">{{if .ProfileCount}}<span class="chip">{{.ProfileCount}} profile{{if ne .ProfileCount 1}}s{{end}}</span>{{else}}<span class="chip chip-empty">no profile yet</span>{{end}}</div><div class="meta"><span class="status">{{.Status}}</span><span>Valheim {{.ServerVersion}}</span></div></a>{{else}}<div class="empty empty-access"><h3>No worlds yet</h3><p>Ask the world owner to grant access to this Steam ID:</p><p class="copy-row"><code class="copy-value">{{.SteamID}}</code><button class="copy-button" type="button" data-copy="{{.SteamID}}" hidden>Copy</button></p></div>{{end}}</div></section></main><script src="/assets/copy-value.js" defer></script></body></html>`

const portalNavigationStyles = `<style>.portal-account-actions{position:fixed;top:1rem;right:1rem;z-index:2;display:flex;align-items:center;gap:.55rem}.portal-nav-button{display:inline-flex;align-items:center;min-height:2.45rem;padding:.55rem .85rem;border:0;border-radius:.45rem;font:inherit;font-weight:700;text-decoration:none;cursor:pointer}.portal-admin-link{background:#71c492;color:#081911}.portal-logout-button{border:1px solid #ffffff35;background:#173a2c;color:#eef7f1}.portal-source-link{border:1px solid #ffffff35;background:#173a2c;color:#eef7f1;padding:.55rem}.portal-source-link svg{display:block;width:1.15rem;height:1.15rem;fill:currentColor}.portal-account-actions form{margin:0}</style>`
const adminNavigation = `{{if .IsAdmin}}<a class="portal-nav-button portal-admin-link" href="/admin">Administration</a>{{end}}`

// sourceNavigation is the AGPL section 13 source offer. The mark is Octicons'
// mark-github-16 (MIT, (c) GitHub), inlined rather than served as an image so
// fill:currentColor matches it to the surrounding navigation.
const sourceNavigation = `<a class="portal-nav-button portal-source-link" href="{{.SourceURL}}" rel="noopener noreferrer external" target="_blank" title="Valheim Portal source code" aria-label="Valheim Portal source code">` +
	`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M6.766 11.328c-2.063-.25-3.516-1.734-3.516-3.656 0-.781.281-1.625.75-2.188-.203-.515-.172-1.609.063-2.062.625-.078 1.468.25 1.968.703.594-.187 1.219-.281 1.985-.281.765 0 1.39.094 1.953.265.484-.437 1.344-.765 1.969-.687.218.422.25 1.515.046 2.047.5.593.766 1.39.766 2.203 0 1.922-1.453 3.375-3.547 3.64.531.344.89 1.094.89 1.954v1.625c0 .468.391.734.86.547C13.781 14.359 16 11.53 16 8.03 16 3.61 12.406 0 7.984 0 3.563 0 0 3.61 0 8.031a7.88 7.88 0 0 0 5.172 7.422c.422.156.828-.125.828-.547v-1.25c-.219.094-.5.156-.75.156-1.031 0-1.64-.562-2.078-1.609-.172-.422-.36-.672-.719-.719-.187-.015-.25-.093-.25-.187 0-.188.313-.328.625-.328.453 0 .844.281 1.25.86.313.452.64.655 1.031.655s.641-.14 1-.5c.266-.265.47-.5.657-.656"/></svg></a>`
const loginAdminNavigation = portalNavigationStyles + `<nav class="portal-account-actions" aria-label="Account">` + sourceNavigation + adminNavigation + `</nav>`
const playerAccountNavigation = portalNavigationStyles + `<nav class="portal-account-actions" aria-label="Account">` + sourceNavigation + adminNavigation + `<form method="post" action="/logout"><button class="portal-nav-button portal-logout-button" type="submit">Log out</button></form></nav>`

var loginPageTemplate = strings.Replace(strings.Replace(loginTemplate, `<head>`, `<head><link rel="icon" href="/favicon.ico" sizes="any"><link rel="manifest" href="/site.webmanifest"><meta name="theme-color" content="#123728">`, 1), `<body>`, `<body>`+loginAdminNavigation, 1)

var playerHomeTemplate = strings.NewReplacer(
	`<h1>Your Valheim profiles, kept in sync.</h1>`,
	`<h1>Fight trolls, not mods.</h1>`,
	`<p>Choose an approved world and profile. Valheim Profile Sync verifies the selected files, updates only what changed, creates a Desktop shortcut for that profile, and launches your existing Steam Valheim installation.</p>`,
	`<p>Choose an approved profile, keep its mods isolated on the drive you chose, and launch with confidence. Valheim Profile Sync verifies every update, refreshes only what changed, and checks your world before it opens Valheim.</p>`,
).Replace(strings.Replace(
	strings.Replace(
		strings.Replace(
			strings.Replace(homeTemplate, `class="status"`, `class="status status-{{.Status}}"`, 1),
			`<main class="shell">`,
			`<main class="shell">`+playerAccountNavigation,
			1,
		),
		`<section class="content">`,
		`<section class="content" style="margin-inline:clamp(.75rem,3vw,2.5rem)">`,
		1,
	),
	`</style>`,
	`.status.status-online::before{content:"";display:inline-block;width:.55rem;height:.55rem;margin-right:.4rem;border-radius:50%;background:#8fffb1;box-shadow:0 0 .8rem #4ade80;vertical-align:-.06em}.section-head{margin-inline:clamp(.75rem,3vw,2.5rem)}</style>`,
	1,
))

const worldTemplate = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{{.World.Name}} · Valheim Profile Sync</title><style>:root{color-scheme:dark;--ink:#eef7f1;--muted:#afc4b5;--moss:#71c492;--line:#ffffff20;--panel:#153a2b}*{box-sizing:border-box}body{min-height:100vh;margin:0;background:radial-gradient(circle at 82% -10%,#368b6199,transparent 38rem),linear-gradient(140deg,#081911,#123728 48%,#07140e);color:var(--ink);font:16px/1.55 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}a{color:inherit}.shell{width:min(1120px,calc(100% - 2rem));margin:auto}.back{display:inline-block;margin:1.5rem 0 0;color:var(--muted);font-size:.9rem;text-decoration:none}.back:hover{color:#fff}.hero{padding:2.4rem 0 2rem;border-bottom:1px solid var(--line)}.eyebrow{color:var(--moss);font-size:.76rem;font-weight:800;letter-spacing:.13em;text-transform:uppercase}.hero h1{margin:.35rem 0 1rem;font-size:clamp(2.6rem,6vw,4.8rem);line-height:.96;letter-spacing:-.06em}.facts{display:flex;flex-wrap:wrap;gap:.65rem}.fact{padding:.48rem .7rem;border:1px solid var(--line);border-radius:.55rem;background:#ffffff0a;color:#dcece1;font-size:.88rem}.fact span{color:var(--muted)}.intro{max-width:72ch;margin:1.7rem 0 2.2rem;padding:1.25rem;border:1px solid var(--line);border-radius:.9rem;background:#ffffff0a;color:var(--muted);font-size:1rem}.intro h2{margin:0 0 .5rem;color:var(--ink);font-size:1.25rem}.intro ol{margin:.6rem 0;padding-left:1.3rem}.intro li{margin:.35rem 0}.intro strong{color:#eaf7ef}.intro a{color:#a8e7bf;font-weight:700}.profiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:1rem;padding-bottom:4rem}.profile-card{position:relative;display:flex;min-height:22rem;flex-direction:column;padding:1.4rem;border:1px solid var(--line);border-radius:1rem;background:linear-gradient(155deg,#1b4c38,#123326);box-shadow:0 1rem 2rem #0002}.profile-card header{display:flex;align-items:start;justify-content:space-between;gap:1rem}.profile-card h2{margin:.3rem 0 0;font-size:1.45rem;letter-spacing:-.035em}.chip{padding:.28rem .55rem;border:1px solid #9bdfb266;border-radius:999px;color:#aaf0c1;font-size:.72rem;font-weight:800;text-transform:uppercase}.release{display:grid;gap:.7rem;margin:1.4rem 0;color:var(--muted);font-size:.9rem}.release strong{display:block;color:#eaf7ef;font-size:1rem}.notes{margin:0;color:#c4d9cb}.sync{display:flex;align-items:center;justify-content:center;min-height:2.8rem;margin-top:auto;padding:.7rem 1rem;border-radius:.6rem;background:var(--moss);color:#082116;font-weight:800;text-align:center;text-decoration:none}.sync:hover{background:#9ee3b7}.hint{margin:.8rem 0 0;color:#a9c2b1;font-size:.82rem}.empty{padding:1.5rem;border:1px dashed var(--line);border-radius:.8rem;color:var(--muted)}@media(max-width:600px){.hero{padding-top:1.5rem}.profiles{grid-template-columns:1fr}.profile-card{min-height:19rem}}.world-description{max-width:72ch;margin:1.3rem 0 0;color:var(--muted);white-space:pre-line}</style></head><body><main class="shell"><a class="back" href="/">← All worlds</a><header class="hero"><div class="eyebrow">Valheim Profile Sync</div><h1>{{.World.Name}}</h1><div class="facts"><div class="fact"><span>Status</span> · {{.World.Status}}</div><div class="fact"><span>Join</span> · {{if .World.JoinAddress}}{{.World.JoinAddress}}{{else}}Pending{{end}}</div><div class="fact"><span>Valheim</span> · {{.World.ServerVersion}}</div><div class="fact"><span>World seed</span> · <code>{{.Seed}}</code></div></div>{{if .World.Description}}<p class="world-description">{{.World.Description}}</p>{{end}}</header><section class="intro"><h2>Install, update, and play</h2><ol><li>{{if .ClientUnavailable}}The Windows app is not available to download right now. Ask the world owner to publish it.{{else}}If needed, <a href="/client/ValheimProfileSync.exe">download Valheim Profile Sync for Windows</a> and run it once.{{end}}</li><li>Choose the card below that matches how you play, then select <strong>Install or update</strong>.</li><li>The app verifies access and checksums, downloads only missing or changed files, keeps this profile separate, and launches your existing Steam Valheim.</li><li><strong>The app makes a shortcut on your Desktop for the selected profile.</strong> Use that shortcut whenever you play; it checks for updates and then launches Valheim with the correct profile.</li><li><strong>Valheim opens at its main menu, not in the world.</strong> Choose <strong>Start game</strong>, pick or create a character, then <strong>Join Game</strong> and the <strong>Join IP</strong> tab. Enter this address:{{if .World.JoinAddress}}<span class="copy-row"><code class="copy-value">{{.World.JoinAddress}}</code><button class="copy-button" type="button" data-copy="{{.World.JoinAddress}}" hidden>Copy</button></span>{{else}}<span class="copy-row"><code class="copy-value">not published yet</code></span>{{end}} Valheim then asks for the server password. It is not shown on this page: ask the world owner for it.</li></ol><p>Do not manually copy mods into Steam or import these portal profiles into r2modman.</p></section><section class="choose"><h2>Choose how you play</h2><p class="choose-hint">Not sure? Choose Desktop.</p></section><section class="profiles">{{range .Profiles}}<article class="profile-card{{if .Recommended}} profile-card-recommended{{end}}"><header><div><div class="eyebrow">{{if .Recommended}}Recommended{{else}}Also available{{end}}</div><h2>{{.Title}}</h2></div></header><p class="profile-summary">{{.Summary}}</p><div class="release"><div><span>Version</span><strong>{{.Version}}</strong></div><div><span>Profile</span><code class="profile-slug">{{.Profile}}</code></div></div>{{if .Notes}}<p class="notes">{{.Notes}}</p>{{end}}<a class="sync" href="{{.SyncURL}}">Install or update</a></article>{{else}}<p class="empty">No current client profile is available.</p>{{end}}</section></main><script src="/assets/copy-value.js" defer></script></body></html>`

var playerWorldTemplate = strings.Replace(
	strings.Replace(
		strings.Replace(worldTemplate, `<head>`, `<head><link rel="icon" href="/favicon.ico" sizes="any"><link rel="manifest" href="/site.webmanifest"><meta name="theme-color" content="#123728">`, 1),
		`</style>`,
		`.profile-card .notes{color:#a6a6a6;font-style:italic}</style>`,
		1,
	),
	`<main class="shell">`,
	`<main class="shell">`+playerAccountNavigation,
	1,
)

const historyTemplate = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="/favicon.ico" sizes="any"><title>{{.World}} profile history</title></head><body><h1>{{.World}} profile history</h1><p>Archived profile releases remain available for recovery of a known-good client configuration.</p>{{range .Releases}}<article><h2>{{.Profile}} · {{.Version}} · {{.ClientType}}</h2><p>{{.Notes}}</p><a href="/history/releases/{{.ID}}">Archived profile details and checksums</a></article>{{end}}</body></html>`
const releaseTemplate = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="/favicon.ico" sizes="any"><title>Verified profile release</title></head><body><h1>{{if .History}}Archived {{end}}verified profile release</h1><p>Download the immutable profile definition and verify its SHA-256 checksum before use with Valheim Profile Sync.</p><p><a href="{{if .History}}/history/releases/{{.ID}}/manifest.json{{else}}/releases/{{.ID}}/manifest.json{{end}}">Download release manifest</a></p><section><h2>Profile definition</h2><ul>{{range .Artifacts}}<li>{{.Kind}}: <a href="{{if $.History}}/history/artifacts/{{.ID}}{{else}}/artifacts/{{.ID}}{{end}}">{{.Name}}</a> <code>SHA-256 {{.SHA256}}</code></li>{{end}}</ul></section></body></html>`
const adminTemplate = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="/favicon.ico" sizes="any">
<meta name="color-scheme" content="dark">
<title>Valheim administration</title>
<style>
body{font:16px system-ui,sans-serif;max-width:1000px;margin:2rem auto;padding:0 1rem;color:#18251d}h1{margin-bottom:.25rem}.brand-logo{display:block;width:min(15rem,60vw);height:auto;margin-bottom:1.25rem;padding:.6rem;background:#173321;border-radius:.5rem}section{border:1px solid #d7e2d8;border-radius:.5rem;padding:1rem;margin:1rem 0}form{display:flex;flex-wrap:wrap;gap:.75rem;align-items:end;margin:.75rem 0}label{display:grid;gap:.25rem;font-weight:600}select,input,textarea,button{font:inherit;padding:.45rem}select{min-width:16rem}textarea{min-width:20rem;min-height:4rem}button{cursor:pointer;background:#285c35;color:#fff;border:0;border-radius:.25rem}.danger{background:#9d3030}article{padding:.5rem 0;border-top:1px solid #d7e2d8}code{word-break:break-all}
.status-light{display:inline-flex;align-items:center;gap:.4rem;padding:.2rem .55rem;border:1px solid currentColor;border-radius:999px;font-size:.82rem;font-weight:700;text-transform:capitalize}.status-light::before{content:"";width:.65rem;height:.65rem;border-radius:50%;background:currentColor;box-shadow:0 0 .4rem currentColor}.status-online{color:#187a36;background:#e8f7ec}.status-offline{color:#b42318;background:#fdefed}.status-maintenance{color:#946200;background:#fff4d6}
.warning{background:#d9a514;color:#2b2100}.button-link{display:inline-block;padding:.45rem;background:#285c35;color:#fff;border:0;border-radius:.25rem;text-decoration:none}.button-link.danger{background:#9d3030}.button-link.warning{background:#d9a514;color:#2b2100}
.server-card-worldgen{margin-right:auto;text-align:left}.server-card-worldgen>summary{cursor:pointer;list-style:none}.server-card-worldgen>summary::-webkit-details-marker{display:none}.server-card-worldgen>p{max-width:34rem;font-size:.85rem}
*,*::before,*::after{box-sizing:border-box}
.admin-nav{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:.65rem;margin:1.25rem 0}.admin-nav a{display:grid;gap:.2rem;padding:.85rem;border:1px solid #c9d9cc;border-radius:.6rem;background:#f5faf6;color:#173321;text-decoration:none}.admin-nav a:hover,.admin-nav a:focus-visible{border-color:#285c35;box-shadow:0 0 0 2px #71c49255}.admin-nav strong{font-size:.95rem}.admin-nav span{color:#52665a;font-size:.78rem}.admin-widget{scroll-margin-top:1rem;border:1px solid #c9d9cc;border-radius:.65rem;margin:1rem 0;padding:0 1rem 1rem;background:#fff;box-shadow:0 2px 10px #1733210d}.admin-widget:not([open]){padding-bottom:0}.admin-widget>summary{display:flex;justify-content:space-between;align-items:center;gap:1rem;margin:0 -1rem;padding:1rem;cursor:pointer;border-radius:.6rem;font-size:1.2rem;font-weight:750;list-style:none}.admin-widget>summary::-webkit-details-marker{display:none}.admin-widget>summary::after{content:"+";display:grid;place-items:center;flex:0 0 1.75rem;height:1.75rem;border-radius:50%;background:#e7f2e9;color:#285c35;font-size:1.3rem}.admin-widget[open]>summary{margin-bottom:.25rem;border-bottom:1px solid #d7e2d8;border-radius:.6rem .6rem 0 0;background:#f5faf6}.admin-widget[open]>summary::after{content:"−"}.admin-widget>summary small{margin-left:auto;color:#607267;font-size:.78rem;font-weight:500}table{width:100%;border-collapse:collapse}th,td{padding:.45rem;text-align:left;border-bottom:1px solid #e3ebe4}pre{max-width:100%;overflow:auto;background:#f5f7f5;padding:.65rem;border-radius:.4rem}@media(max-width:760px){body{margin:1rem auto}.admin-nav{grid-template-columns:repeat(2,minmax(0,1fr))}.admin-widget>summary{align-items:flex-start}.admin-widget>summary small{display:none}select,input,textarea{min-width:0;width:100%}form{align-items:stretch}table{display:block;overflow-x:auto}}@media(max-width:420px){.admin-nav{grid-template-columns:1fr}}
@media(max-width:760px){table th,table td{white-space:nowrap}}
.agent-dock{position:fixed;right:1rem;bottom:1rem;z-index:20;width:min(26rem,calc(100vw - 2rem));border:1px solid #b7d0bf26;border-radius:.6rem;background:#102219;color:#edf6ef;box-shadow:0 10px 30px #00000059}.agent-dock>summary{display:flex;justify-content:space-between;align-items:center;gap:.5rem;padding:.6rem .85rem;background:#2c7048;color:#fff;border-radius:.55rem;font-weight:700;cursor:pointer;list-style:none}.agent-dock[open]>summary{border-radius:.55rem .55rem 0 0}.agent-dock>summary::-webkit-details-marker{display:none}.dock-badge{display:inline-flex;align-items:center;gap:.3rem;padding:.1rem .5rem;border-radius:999px;background:#d9a514;color:#2b2100;font-size:.72rem;font-weight:700}.dock-badge .spinner{width:.6rem;height:.6rem;margin:0;border-width:2px;border-color:#2b210055;border-top-color:#2b2100}.dock-awaiting:empty{display:none}.dock-awaiting{padding:.6rem .85rem;border-bottom:1px solid #b7d0bf26;background:#1a2a13}.dock-request{padding:.1rem 0 .3rem}.dock-request+.dock-request{margin-top:.5rem;border-top:1px solid #b7d0bf26}.dock-request h4{margin:.2rem 0 .35rem;color:#f4dd8a;font-size:.82rem}.dock-args{display:grid;grid-template-columns:auto 1fr;gap:.1rem .5rem;margin:0 0 .4rem}.dock-args dt{color:#a7baad;font-size:.72rem}.dock-args dd{margin:0;font:12px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;word-break:break-word}.dock-context{margin:.2rem 0 .45rem;color:#f4dd8a;font-size:.74rem}.dock-log{max-height:min(22rem,45vh);overflow-y:auto;padding:.6rem .85rem;border-bottom:1px solid #b7d0bf26}.dock-turn{padding:.4rem 0 .5rem;border-top:1px solid #b7d0bf1a}.dock-turn:first-child{border-top:0}.dock-turn h4{margin:0;color:#a7baad;font-size:.68rem;text-transform:uppercase;letter-spacing:.04em}.dock-turn pre{margin:.2rem 0 0;padding:.4rem .55rem;white-space:pre-wrap;word-break:break-word;border-radius:.3rem;font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}.dock-turn-operator pre{border-left:3px solid #6f9ad6}.dock-turn-agent pre{border-left:3px solid #71c492}.dock-turn-system pre{border-left:3px solid #d9a514}.dock-note{margin:0;padding:.4rem .85rem;color:#a7baad;font-size:.78rem}.dock-note:empty{display:none}.agent-dock form{display:block;margin:0;padding:.6rem .85rem .85rem}.agent-dock textarea{width:100%;min-width:0;min-height:3.4rem;background:#0a1711;color:#edf6ef;border:1px solid #b7d0bf40;border-radius:.3rem}.dock-actions{display:flex;gap:.5rem;align-items:center;margin:.55rem 0 .4rem}.agent-dock small{color:#a7baad;font-size:.72rem}@media(max-width:760px){.agent-dock{right:.5rem;bottom:.5rem;width:calc(100vw - 1rem)}}
</style>
</head>
<body>
<img class="brand-logo" src="/assets/neuralyze-logo.svg" alt="Neuralyze">
<a href="/" style="position:fixed;top:1rem;right:1rem;padding:.55rem .85rem;border-radius:.45rem;background:#285c35;color:#fff;font-weight:700;text-decoration:none">Back to portal</a>
<h1>Valheim administration</h1>
<p>Operate player access, worlds, profiles, releases, and recovery from one control surface.</p>
<nav class="admin-nav" aria-label="Administration sections">
<a href="#servers"><strong>Servers</strong><span>Lifecycle and network</span></a>
<a href="#players"><strong>Players</strong><span>Access and identities</span></a>
<a href="#mods"><strong>Mods</strong><span>Profiles and packages</span></a>
<a href="#releases"><strong>Releases</strong><span>Client artifacts</span></a>
<a href="#activity"><strong>Activity</strong><span>Jobs and audit trail</span></a>
</nav>
<section class="admin-overview" aria-label="Operations overview">
<article><strong>{{.WorldCount}}</strong><span>registered worlds</span></article>
<article><strong>{{.MemberCount}}</strong><span>access grants</span></article>
<article><strong>{{.ReleaseCount}}</strong><span>client releases</span></article>
<article><strong>{{.JobCount}}</strong><span>recent jobs</span></article>
</section>
{{if .ClientProblem}}<section class="admin-overview" aria-label="Windows client problem"><article><strong>Windows client not published</strong><span>{{.ClientProblem}}</span></article></section>{{end}}
<details id="servers" class="admin-widget" open>
<summary>Server operations <small>Lifecycle, access, releases, mods, and activity</small></summary>
<div class="server-toolbar">
<div class="server-toolbar-intro">
<p>Every action is sent to the privileged local agent as a fixed, signed operation. Stop, restart, and build create a world backup first.</p>
<p class="muted">The wizard creates a disabled world transactionally: seed or import source, mods and dependencies, reserved ports, and backup policy.</p>
</div>
<div class="server-toolbar-actions">
<a class="button-link" href="/admin/servers/new">Create a new server</a>
<a class="button-link secondary" href="/admin/backups">Backups and recovery</a>
<a class="button-link secondary" href="/admin/agent">Agent{{if .PendingAgentRequests}} ({{.PendingAgentRequests}} awaiting you){{end}}</a>
</div>
</div>
<details class="server-adopt">
<summary>Existing servers <small>Discover and register controlled worlds</small></summary>
<p>Agent-controlled server directories are never published automatically. Register one as a disabled portal world before managing it; enable player access separately after verifying its address and state.</p>
{{if .WorldCatalogError}}<p>Existing server discovery is temporarily unavailable.</p>{{else}}{{range .UnregisteredWorlds}}<form class="server-adopt-form" method="post" action="/admin/worlds/register"><input type="hidden" name="csrf" value="{{$.CSRF}}"><input type="hidden" name="world" value="{{.Name}}"><input type="hidden" name="port" value="{{.Port}}"><strong>{{.Name}}</strong> <span class="status-light status-{{.Status}}">{{.Status}}</span> <code>UDP {{.Port}}-{{.EndPort}}</code><label>Public join host <input name="join_host" value="{{$.SuggestedJoinHost}}" required></label><button>Register disabled server</button></form>{{else}}<p>Every agent-controlled server is registered.</p>{{end}}{{end}}
</details>
{{range .Worlds}}{{$w := .}}<article class="server-card" id="server-{{.Name}}">
<details class="server-card-body">
<summary><h3 class="server-card-name">{{.Name}}</h3><span class="status-light status-{{.Status}}">{{.Status}}</span></summary>
<header class="server-card-head">
<code class="server-card-address">{{.JoinAddress}}</code>
<nav class="server-card-links" aria-label="{{.Name}} admin views">
<a class="button-link" href="/admin/worlds/{{.Name}}/map">World map and analysis</a>
</nav>
</header>
<div class="server-card-controls">
<form class="server-card-control" method="post" action="/admin/jobs">
<input type="hidden" name="csrf" value="{{$.CSRF}}"><input type="hidden" name="world" value="{{.Name}}">
<label>Action <select name="operation" required><option value="status">Refresh status</option><option value="start">Start</option><option value="stop">Back up and stop</option><option value="restart">Back up and restart</option><option value="pause">Pause</option><option value="resume">Resume</option><option value="backup">Back up now</option><option value="logs">Capture logs</option><option value="backups">List backups</option><option value="build">Back up and update image</option></select></label>
<button>Run</button>
</form>
<form class="server-card-control" method="post" action="/admin/worlds/{{.Name}}/status">
<input type="hidden" name="csrf" value="{{$.CSRF}}">
<label>Player-visible state <select name="status"><option value="online"{{if ne .Status "maintenance"}} selected{{end}}>Automatic - live server check</option><option value="maintenance"{{if eq .Status "maintenance"}} selected{{end}}>Maintenance</option></select></label>
<button>Update state</button>
</form>
<form class="server-card-control" method="post" action="/admin/worlds/{{.Name}}/port">
<input type="hidden" name="csrf" value="{{$.CSRF}}">
<label>Game base port <input type="number" name="port"{{if .Port}} value="{{.Port}}"{{end}} min="1024" max="65533" required></label>
<button>Back up, apply port, and restart</button>
</form>
<form class="server-card-control server-card-control-wide" method="post" action="/admin/worlds/{{.Name}}/enabled">
<input type="hidden" name="csrf" value="{{$.CSRF}}"><input type="hidden" name="enabled" value="{{if .Enabled}}false{{else}}true{{end}}">
<span class="server-card-control-title">Player access</span>
<button class="{{if .Enabled}}warning{{end}}">{{if .Enabled}}Disable player access{{else}}Enable player access{{end}}</button>
</form>
<form class="server-card-control server-card-control-wide" method="post" action="/admin/worlds/{{.Name}}/description">
<input type="hidden" name="csrf" value="{{$.CSRF}}">
<span class="server-card-control-title">Player-visible description</span>
<textarea name="description" maxlength="500" rows="3" placeholder="What players should know about this world.">{{.Description}}</textarea>
<button>Save description</button>
</form>
</div>
<div class="server-card-sections">
<details class="server-card-section">
<summary>Player access <small>Who may see and join this world</small></summary>
<p class="muted">A world appears on the player home page only when player access is enabled above and the signed-in Steam ID is granted here.</p>
<form class="server-card-form" method="post" action="/admin/world-members">
<input type="hidden" name="csrf" value="{{$.CSRF}}"><input type="hidden" name="world" value="{{$w.Name}}">
<label>Steam ID <input name="steam_id" list="known-steam-ids" inputmode="numeric" autocomplete="off" required pattern="7[0-9]{16}" minlength="17" maxlength="17" placeholder="7656119…"></label>
<label>Player name <input name="label" maxlength="64" autocomplete="off" placeholder="Optional"></label>
<button>Grant access</button>
</form>
<table><thead><tr><th>Player</th><th>Steam ID</th><th>Role</th><th>Granted by</th><th></th></tr></thead><tbody>
{{range $.Members}}{{if eq .World $w.Name}}<tr><td>{{.DisplayName}}</td><td><code>{{.SteamID}}</code></td><td>{{if .IsAdmin}}admin{{else}}player{{end}}</td><td>{{.GrantedBy}}</td><td><form method="post" action="/admin/world-members/revoke"><input type="hidden" name="csrf" value="{{$.CSRF}}"><input type="hidden" name="world" value="{{.World}}"><input type="hidden" name="steam_id" value="{{.SteamID}}"><button class="danger">Revoke</button></form></td></tr>{{end}}{{end}}
</tbody></table>
{{with index $.Access $w.Name}}
<h4>Generated access lists</h4>
<p class="muted">{{.AdminCount}} in <code>adminlist.txt</code>, {{.PermittedCount}} in <code>permittedlist.txt</code>. {{if .InSync}}Applied {{if .Applied}}{{.AppliedAt.Format "2006-01-02 15:04"}} by {{.AppliedBy}}{{else}}nothing to apply{{end}}.{{else}}<b>Pending:</b> the servers still have the previous lists.{{end}}</p>
<div class="server-card-controls">
<form class="server-card-control" method="post" action="/admin/worlds/{{$w.Name}}/access-apply">
<input type="hidden" name="csrf" value="{{$.CSRF}}">
<span class="server-card-control-title">Access lists</span>
<button class="{{if not .InSync}}warning{{end}}">Apply to this server</button>
</form>
<form class="server-card-control" method="post" action="/admin/worlds/{{$w.Name}}/permitted-enforcement">
<input type="hidden" name="csrf" value="{{$.CSRF}}"><input type="hidden" name="enforce" value="{{if .EnforcePermitted}}false{{else}}true{{end}}">
<span class="server-card-control-title">Permitted list</span>
<button class="{{if not .EnforcePermitted}}warning{{end}}">{{if .EnforcePermitted}}Stop restricting joins{{else}}Restrict joins to granted players{{end}}</button>
</form>
</div>
<p class="muted">{{if .EnforcePermitted}}Only the {{.PermittedCount}} granted player(s) may join once applied; everyone else is refused even with the password.{{else}}Every player with the password may join. Enable restriction to make portal grants authoritative on the server.{{end}}</p>
{{end}}
</details>
<details class="server-card-section">
<summary>Releases <small>Drafts, publication, and artifacts</small></summary>
{{range $.Releases}}{{if and (eq .World $w.Name) (ne .Status "archived")}}<div class="server-subrow">
<b>{{.Profile}}</b> <span class="muted">{{.ClientType}} {{.Version}}</span> <span class="server-tag">{{.Status}}</span>
<form method="post" action="/admin/releases/{{.ID}}/publish"><input type="hidden" name="csrf" value="{{$.CSRF}}"><button>Validate and publish</button></form>
<form method="post" action="/admin/releases/{{.ID}}/archive"><input type="hidden" name="csrf" value="{{$.CSRF}}"><button class="danger">Archive</button></form>
</div>{{end}}{{end}}
<h4>Create release draft</h4>
<form class="server-card-form" method="post" action="/admin/releases">
<input type="hidden" name="csrf" value="{{$.CSRF}}"><input type="hidden" name="world" value="{{$w.Name}}">
<label>Profile slug <input name="profile" list="release-profiles-{{$w.Name}}" autocomplete="off" required pattern="[A-Za-z0-9][A-Za-z0-9._-]{0,79}"></label>
<datalist id="release-profiles-{{$w.Name}}">{{range $.Profiles}}{{if eq .World $w.Name}}<option value="{{.Profile}}" label="{{.Name}}"></option>{{end}}{{end}}</datalist>
<label>Client <select name="client_type"><option>flat</option><option>vr</option></select></label>
<label>Version <input name="version" required></label>
<label>Notes <textarea name="notes"></textarea></label>
<button>Create draft</button>
</form>
<h4>Upload release artifact</h4>
<p class="muted">One profile ZIP containing <code>profile-manifest.json</code> and <code>config/</code>. Flat drafts may additionally receive one validated <code>flat_companion</code> ZIP. VR drafts additionally require one validated <code>vr_runtime</code> ZIP.</p>
<form class="server-card-form" method="post" action="/admin/artifacts" enctype="multipart/form-data">
<input type="hidden" name="csrf" value="{{$.CSRF}}">
<label>Release ID <input name="release_id" list="draft-releases-{{$w.Name}}" autocomplete="off" required></label>
<datalist id="draft-releases-{{$w.Name}}">{{range $.DraftReleases}}{{if eq .World $w.Name}}<option value="{{.ID}}" label="{{.Profile}} / {{.ClientType}} / {{.Version}}"></option>{{end}}{{end}}</datalist>
<label>Artifact kind <select name="kind" required><option value="profile">Profile definition</option><option value="flat_companion">Flat ValheimVR companion (Flat releases only)</option><option value="vr_runtime">VR runtime (VR releases only)</option></select></label>
<label>File <input type="file" name="artifact" required></label>
<button>Upload immutable artifact</button>
</form>
</details>
<details class="server-card-section">
<summary>Mods <small>Profiles, packages, and diagnostics</small></summary>
{{range $.Profiles}}{{if eq .World $w.Name}}<div class="server-subrow">
<b>{{.Profile}}</b> <span class="muted">{{.Packages}} Thunderstore, {{.CustomPackages}} custom</span>
<a class="button-link" href="/admin/mods?world={{.World}}&amp;profile={{.Profile}}">Manage mods</a>
</div>{{end}}{{end}}
<form class="server-card-form" method="get" action="/admin/mods">
<input type="hidden" name="world" value="{{$w.Name}}">
<label>Other profile slug <input name="profile" autocomplete="off" required pattern="[A-Za-z0-9][A-Za-z0-9._-]{0,79}"></label>
<button>Manage mods</button>
</form>
<h4>Debug logging</h4>
<p class="muted">Enables BepInEx debug and Harmony logging, the startup-time profiler, and the diagnostics plugin. Toggling publishes a new release with a bumped patch version, so players receive it on their next sync.</p>
{{range $.DebugProfiles}}{{if eq .World $w.Name}}<div class="server-subrow">
<b>{{.Profile}}</b> <span class="server-tag">{{if .Enabled}}enabled{{else}}disabled{{end}}</span>
<form method="post" action="/admin/worlds/{{.World}}/profiles/{{.Profile}}/debug-logging"><input type="hidden" name="csrf" value="{{$.CSRF}}"><input type="hidden" name="enabled" value="{{if .Enabled}}false{{else}}true{{end}}"><button{{if .Enabled}} class="danger"{{end}}>{{if .Enabled}}Disable{{else}}Enable{{end}} debug logging</button></form>
</div>{{end}}{{end}}
</details>
<details class="server-card-section">
<summary>Activity <small>Recent privileged operations for this world</small></summary>
{{range $.Jobs}}{{if eq .World $w.Name}}<div class="server-subrow server-subrow-stacked">
<b>{{.Operation}}</b> <span class="server-tag">{{.Status}}</span> <small class="muted">{{.CreatedAt.Format "2006-01-02 15:04"}} UTC · {{.RequestedBy}}</small>
{{if .Detail}}<pre>{{.Detail}}</pre>{{end}}
</div>{{end}}{{end}}
</details>
</div>
<footer class="server-card-footer">
<details class="server-card-worldgen">
<summary class="button-link warning">Recreate world from a seed…</summary>
<form method="post" action="/admin/worldgen">
<input type="hidden" name="csrf" value="{{$.CSRF}}"><input type="hidden" name="world" value="{{.Name}}">
<label>Seed <input name="seed" value="{{index $.Seeds .Name}}" required pattern="[A-Za-z0-9]{1,64}" maxlength="64" autocomplete="off" placeholder="SeedTest01"><small>Prefilled with the seed this world's current map was built for.</small></label>
<button class="danger">Review regeneration…</button>
</form>
<p class="muted">Archives the current save, then regenerates {{.Name}} at day 0 on that seed. Every structure and terrain change in the current save is gone. A typed confirmation follows.</p>
</details>
<a class="button-link danger" href="/admin/worlds/{{.Name}}/remove">Delete or unregister server…</a>
</footer>
</details>
</article>{{else}}<p>No configured worlds are available.</p>{{end}}
<details><summary>Exact operation plan</summary><p>{{.OperationPlan}}</p></details>
</details>
<details id="players" class="admin-widget">
<summary>Player access <small>{{.PlayerCount}} players and {{.MemberCount}} world grants</small></summary>
<p>One card per Steam ID, listing every world that account may join and whether that world grants it in-game admin. The portal owns both access lists: <code>adminlist.txt</code> comes from the admin role, <code>permittedlist.txt</code> from membership on worlds that enforce it. Changes here are intended state until you apply them to the servers. Select a world to jump to its server card.</p>
<div class="access-actions">
<form method="post" action="/admin/access-apply"><input type="hidden" name="csrf" value="{{.CSRF}}"><button class="{{if .PendingAccess}}warning{{end}}">Apply access lists to all servers</button><small>{{if .PendingAccess}}{{.PendingAccess}} world(s) have changes that no server has yet.{{else}}Every world matches the lists last applied to it.{{end}}</small></form>
<p><a href="/admin/access">Verify what each host actually has</a> reads the live list files and <code>valheim.env</code> back through the agent.</p>
</div>
<form method="post" action="/admin/steam-identities/refresh"><input type="hidden" name="csrf" value="{{.CSRF}}"><button>Refresh names from Steam</button><small>Looks up every one of the {{.IdentityCount}} Steam IDs on record, including any this page does not list, and never clears a name it cannot resolve.</small></form>
<datalist id="known-steam-ids">{{range .Identities}}<option value="{{.SteamID}}" label="{{.DisplayName}} · {{.SteamID}}"></option>{{end}}</datalist>
{{if .Worlds}}
<details class="player-invite">
<summary>Add a player <small>Grant a Steam ID access to a world</small></summary>
<form method="post" action="/admin/world-members">
<input type="hidden" name="csrf" value="{{.CSRF}}">
<label>World <select name="world" required>{{range .Worlds}}<option value="{{.Name}}">{{.Name}}</option>{{end}}</select></label>
<label>Steam ID <input name="steam_id" list="known-steam-ids" inputmode="numeric" autocomplete="off" required pattern="7[0-9]{16}" minlength="17" maxlength="17" placeholder="7656119…"><small>Start typing a name to pick a known account.</small></label>
<label>Player name <input name="label" maxlength="64" autocomplete="off" placeholder="Optional"><small>Your own name for this account. Wins over the Steam name.</small></label>
<button>Grant access</button>
</form>
</details>
<div class="player-grid">
{{range .Players}}{{$p := .}}<article class="player-card">
<header class="player-card-head">
<h3 class="player-card-name">{{.DisplayName}}</h3>
{{if .AdminWorlds}}<span class="player-tag admin">admin on {{.AdminWorlds}}</span>{{end}}
{{if not .SignedIn}}<span class="player-tag">never signed in</span>{{end}}
<code class="player-card-id">{{.SteamID}}</code>
</header>
{{if .SignedIn}}<p class="player-card-meta muted">Steam name {{if .PersonaName}}{{.PersonaName}}{{else}}unknown{{end}}, last seen {{.LastSeenAt.Format "2006-01-02"}}</p>{{end}}
<ul class="player-worlds">
{{range .Worlds}}<li class="player-world">
<a class="player-world-name" href="#server-{{.World}}">{{.World}}</a>
{{if .IsAdmin}}<span class="player-tag admin">admin</span>{{else}}<span class="player-tag">player</span>{{end}}
{{if .Pending}}<span class="player-tag warning">not applied</span>{{end}}
<small class="muted">granted by {{.GrantedBy}}</small>
<form method="post" action="/admin/world-members/role"><input type="hidden" name="csrf" value="{{$.CSRF}}"><input type="hidden" name="world" value="{{.World}}"><input type="hidden" name="steam_id" value="{{$p.SteamID}}"><input type="hidden" name="role" value="{{if .IsAdmin}}member{{else}}admin{{end}}"><button>{{if .IsAdmin}}Remove admin{{else}}Make admin{{end}}</button></form>
<form method="post" action="/admin/world-members/revoke"><input type="hidden" name="csrf" value="{{$.CSRF}}"><input type="hidden" name="world" value="{{.World}}"><input type="hidden" name="steam_id" value="{{$p.SteamID}}"><button class="danger">Revoke</button></form>
</li>{{end}}
{{if not .Worlds}}<li class="player-world"><span class="muted">No world access yet.</span></li>{{end}}
</ul>
<div class="player-card-actions">
{{if .OtherWorlds}}<form method="post" action="/admin/world-members">
<input type="hidden" name="csrf" value="{{$.CSRF}}"><input type="hidden" name="steam_id" value="{{.SteamID}}">
<label>Add to world <select name="world" required>{{range .OtherWorlds}}<option value="{{.}}">{{.}}</option>{{end}}</select></label>
<button>Grant access</button>
</form>{{end}}
<form method="post" action="/admin/steam-identities/label"><input type="hidden" name="csrf" value="{{$.CSRF}}"><input type="hidden" name="steam_id" value="{{.SteamID}}"><label>Your name for them <input name="label" value="{{.Label}}" maxlength="64" autocomplete="off" placeholder="Add a name"></label><button>Save name</button></form>
</div>
</article>{{else}}<p>No players yet. Grant a Steam ID above, or wait for a first sign-in.</p>{{end}}
</div>
{{else}}
<p>No worlds are available yet. Publish or register a world first.</p>
{{end}}
<details id="steam-identities"><summary>Steam identities ({{.IdentityCount}})</summary>
<p>A player's Steam name is recorded when they sign in{{if .SteamAPIKeyConfigured}} and is read through the Steam Web API.{{else}} and is read from their public Steam profile, so private profiles stay unnamed until you set PORTAL_STEAM_API_KEY.{{end}} Your own name for an account never depends on Steam.</p>
{{if .Identities}}<table><thead><tr><th>Player</th><th>Steam ID</th><th>Steam name</th><th>Last seen</th><th>Your name for them</th></tr></thead><tbody>
{{range .Identities}}<tr><td>{{.DisplayName}}</td><td><code>{{.SteamID}}</code></td><td>{{if .PersonaName}}{{.PersonaName}}{{else}}unknown{{end}}</td><td>{{.LastSeenAt.Format "2006-01-02"}}</td><td><form method="post" action="/admin/steam-identities/label"><input type="hidden" name="csrf" value="{{$.CSRF}}"><input type="hidden" name="steam_id" value="{{.SteamID}}"><input name="label" value="{{.Label}}" maxlength="64" autocomplete="off" placeholder="Add a name"><button>Save name</button></form></td></tr>{{end}}
</tbody></table>{{else}}<p>No verified or imported IDs yet. You can still enter a Steam ID above.</p>{{end}}
</details>
</details>
<details id="mods" class="admin-widget">
<summary>Mod profiles <small>Thunderstore and approved custom packages</small></summary>
<p>Inspect installed packages, search Thunderstore, select approved local custom packages, and deploy an immutable profile.</p>
{{if .Worlds}}<form method="get" action="/admin/mods" data-profile-autofill>
<label>World <select name="world" data-profile-world required>{{range .Worlds}}<option value="{{.Name}}">{{.Name}}</option>{{end}}</select></label>
<label>Profile slug <input name="profile" list="mod-profile-slugs" data-profile-slug autocomplete="off" placeholder="redesign-alpha" required pattern="[A-Za-z0-9][A-Za-z0-9._-]{0,79}"><small>Choose a profile from this world or type a new slug.</small></label>
<datalist id="mod-profile-slugs"></datalist>
<button>Manage mods</button>
</form>{{else}}<p>No configured worlds are available.</p>{{end}}
</details>
<details id="releases" class="admin-widget">
<summary>Releases <small>Publish or archive client profiles</small></summary>
{{range .Releases}}<article><b>{{.World}} · {{.Profile}} · {{.ClientType}} · {{.Version}} · {{.Status}}</b>
<form method="post" action="/admin/releases/{{.ID}}/publish"><input type="hidden" name="csrf" value="{{$.CSRF}}"><button>Validate and publish</button></form>
<form method="post" action="/admin/releases/{{.ID}}/archive"><input type="hidden" name="csrf" value="{{$.CSRF}}"><button class="danger">Archive</button></form>
</article>{{else}}<p>No releases.</p>{{end}}
</details>
<details id="activity" class="admin-widget">
<summary>Recent jobs <small>Privileged operation results</small></summary>
{{range .Jobs}}<article><b>{{.World}} · {{.Operation}} · {{.Status}}</b><br><small>{{.CreatedAt}} · {{.RequestedBy}}</small><pre>{{.Detail}}</pre></article>{{else}}<p>No jobs have run.</p>{{end}}
</details>
<details id="audit" class="admin-widget">
<summary>Audit log <small>Administrative actions and actors</small></summary>
{{range .Audit}}<article>{{.CreatedAt}} · {{.Actor}} · {{.Action}} · {{.Target}} · {{.Detail}}</article>{{else}}<p>No audit events.</p>{{end}}
</details>
<template id="admin-profile-catalog"><select aria-hidden="true" tabindex="-1">{{range .Profiles}}<option data-world="{{.World}}" value="{{.Profile}}" label="{{if .Name}}{{.Name}} · {{end}}{{.World}} / {{.Profile}}"></option>{{end}}</select></template>
<details data-agent-dock class="agent-dock">
<summary>Agent <span data-dock-badge class="dock-badge" hidden></span></summary>
<div data-dock-awaiting class="dock-awaiting"></div>
<div data-dock-log class="dock-log"></div>
<p data-dock-note class="dock-note"></p>
<form>
<input type="hidden" name="csrf" value="{{.CSRF}}">
<textarea name="body" rows="3" placeholder="Ask the agent about this deployment."></textarea>
<div class="dock-actions">
<button type="submit">Send</button>
<a class="button-link" href="/admin/agent">Open the full page</a>
</div>
<small>Ctrl+Enter sends. <a href="/admin/agent">The full page</a> shows every turn and the evidence read back.</small>
</form>
</details>
<script src="/assets/admin-dock.js"></script>
<script src="/assets/admin-profile-autofill.js"></script>
</body>
</html>`

const restoreTemplate = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="/favicon.ico" sizes="any"><title>Confirm world restore</title></head><body><h1>Confirm restore</h1><p>World: {{.Restore.World}}</p><p>Backup: {{.Restore.Backup}}</p><p>This creates a fresh backup, stops the world, then replaces only its save pair. The server remains stopped.</p><form method="post" action="/admin/restores/{{.ID}}"><input type="hidden" name="csrf" value="{{.CSRF}}"><label>Type <code>RESTORE {{.Restore.World}} {{.Restore.Backup}}</code> <input name="confirmation" required></label><button>Confirm restore</button></form></body></html>`

const worldRemovalTemplate = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Remove {{.World.Name}} server</title><style>*{box-sizing:border-box}body{font:16px system-ui,sans-serif;max-width:760px;margin:2rem auto;padding:0 1rem;color:#18251d}.brand-logo{display:block;width:min(15rem,60vw);height:auto;margin-bottom:1.25rem;padding:.6rem;background:#173321;border-radius:.5rem}section{border:1px solid #d7e2d8;border-radius:.6rem;padding:1rem;margin:1rem 0}section.danger{border-color:#b42318;background:#fff7f6}form{display:grid;gap:.75rem}label{display:grid;gap:.35rem;font-weight:650}input,button{font:inherit;padding:.55rem}button{width:max-content;cursor:pointer;border:0;border-radius:.3rem;background:#285c35;color:#fff}.danger button{background:#9d3030}code{word-break:break-all}</style></head><body><img class="brand-logo" src="/assets/neuralyze-logo.svg" alt="Neuralyze"><p><a href="/admin#servers">← Back to server operations</a></p><h1>Remove {{.World.Name}}</h1><p>Choose carefully. These operations have different effects and require an exact typed confirmation.</p><section><h2>Unregister from portal</h2><p>Removes the world from this portal and revokes every player membership. The running server, files, backups, release artifacts, and release history remain intact. The agent will discover the existing server again so it can be re-registered later.</p><form method="post" action="/admin/worlds/{{.World.Name}}/remove"><input type="hidden" name="csrf" value="{{.CSRF}}"><input type="hidden" name="mode" value="unregister"><label>Type <code>UNREGISTER {{.World.Name}}</code><input name="confirmation" required autocomplete="off"></label><button>Unregister only</button></form></section><section class="danger"><h2>Permanently delete server and saves</h2><p>This immediately disables player access, creates a final world-save backup, stops the server, and then deletes the complete server directory including its live saves, configuration, and mods. External backups and immutable release artifacts remain. Current releases are archived.</p><p>If backup or stop fails, deletion does not run and the portal registration remains for recovery.</p><form method="post" action="/admin/worlds/{{.World.Name}}/remove"><input type="hidden" name="csrf" value="{{.CSRF}}"><input type="hidden" name="mode" value="delete"><label>Type <code>DELETE {{.World.Name}}</code><input name="confirmation" required autocomplete="off"></label><button>Permanently delete server</button></form></section></body></html>`
