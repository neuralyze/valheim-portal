package app

import (
	"errors"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

type Config struct {
	ListenAddr     string
	DatabasePath   string
	ArtifactRoot   string
	CSRFSecretFile string
	MapRoot        string
	MapSourceRoot  string
	AuthHeader     string
	CookieSecure   bool
	AgentSocket    string
	// Touched when the operator sends a message or decides a verb. A systemd path unit
	// watches it and starts one runner pass, because the portal holds no host access of
	// its own. Empty means passes are triggered by hand.
	AgentWakeFile    string
	AgentTokenFile   string
	PublicBaseURL    string
	ClientExecutable string
	TrustedProxyCIDR string
	// SkipDeviceCode disables the "Confirm this sign-in" step, where the player
	// retypes a code that only the desktop application shows. It defends a PUBLIC
	// deployment, where a stranger's browser session must not be able to authorize
	// somebody else's app. On a single-operator install -- one person, their own
	// proxy, their own machine -- there is no second party to defend against, and the
	// step is a login tax on the only user. Phrased as SKIP, not REQUIRE, so the zero
	// value is the safe one: a Config built in a test, or by a future caller that has
	// not heard of this field, keeps the confirmation. Set PORTAL_REQUIRE_DEVICE_CODE
	// =false to go straight to authorization once the Steam sign-in has succeeded.
	SkipDeviceCode bool
	// SteamAPIKey is optional. With a key the portal resolves Steam persona
	// names through the official Web API; without one it falls back to public
	// community profiles, which only cover accounts with a public profile.
	SteamAPIKey  string
	Provisioning ProvisioningDefaults
	// AdminSteamIDs are the SteamID64s permitted to administer the portal after
	// signing in with Steam. It is deliberately separate from the per-world
	// world_members.role='admin' value, which is the in-game adminlist and
	// confers nothing here. Empty means no Steam operator may administer, which
	// leaves the reverse-proxy path as the only way in.
	AdminSteamIDs map[string]struct{}
	// SourceURL is where the player-facing pages offer this program's source.
	//
	// AGPL-3.0 section 13 obliges anyone running a modified version as a network
	// service to offer its users the corresponding source, so a deployment that
	// carries local changes must point this at its own repository. The default
	// is correct only for an unmodified build.
	SourceURL string
}

type ProvisioningDefaults struct {
	JoinHost       string
	GamePort       int
	PlayerLimit    int
	BackupInterval string
	BackupAge      int
	BackupCount    int
}

func LoadConfig() (Config, error) {
	provisioning, err := loadProvisioningDefaults()
	if err != nil {
		return Config{}, err
	}
	c := Config{
		ListenAddr:       getenv("PORTAL_LISTEN_ADDR", ":8080"),
		DatabasePath:     getenv("PORTAL_DB_PATH", "/var/lib/valheim-portal/portal.sqlite"),
		ArtifactRoot:     getenv("PORTAL_ARTIFACT_ROOT", "/var/lib/valheim-portal/artifacts"),
		MapRoot:          getenv("PORTAL_MAP_ROOT", "/var/lib/valheim-portal/maps"),
		MapSourceRoot:    getenv("PORTAL_MAP_SOURCE_ROOT", "/var/lib/valheim-worlds"),
		CSRFSecretFile:   os.Getenv("PORTAL_CSRF_SECRET_FILE"),
		AuthHeader:       getenv("PORTAL_AUTH_HEADER", "X-Forwarded-User"),
		CookieSecure:     getenv("PORTAL_COOKIE_SECURE", "true") != "false",
		AgentSocket:      getenv("PORTAL_AGENT_SOCKET", "/run/valheim-agent/agent.sock"),
		AgentWakeFile:    getenv("PORTAL_AGENT_WAKE_FILE", ""),
		AgentTokenFile:   os.Getenv("PORTAL_AGENT_TOKEN_FILE"),
		PublicBaseURL:    os.Getenv("PORTAL_PUBLIC_BASE_URL"),
		ClientExecutable: getenv("PORTAL_CLIENT_EXECUTABLE", "/srv/client/ValheimProfileSync.exe"),
		TrustedProxyCIDR: os.Getenv("PORTAL_TRUSTED_PROXY_CIDR"),
		// Opt OUT explicitly, like PORTAL_COOKIE_SECURE: only the exact string "false"
		// disables the step, so a typo cannot silently switch off a public deployment's
		// protection.
		SkipDeviceCode: getenv("PORTAL_REQUIRE_DEVICE_CODE", "true") == "false",
		SteamAPIKey:    strings.TrimSpace(os.Getenv("PORTAL_STEAM_API_KEY")),
		AdminSteamIDs:  map[string]struct{}{},
		SourceURL:      getenv("PORTAL_SOURCE_URL", "https://github.com/neuralyze/valheim-portal"),
		Provisioning:   provisioning,
	}
	// PORTAL_PUBLIC_BASE_URL has no safe default: guessing one silently emits
	// links and redirects pointing at someone else's host.
	if c.CSRFSecretFile == "" || c.AgentTokenFile == "" || c.TrustedProxyCIDR == "" || c.PublicBaseURL == "" {
		return Config{}, errors.New("PORTAL_CSRF_SECRET_FILE, PORTAL_AGENT_TOKEN_FILE, PORTAL_TRUSTED_PROXY_CIDR, and PORTAL_PUBLIC_BASE_URL are required")
	}
	if c.AuthHeader == "" || strings.ContainsAny(c.AuthHeader, "\r\n") {
		return Config{}, errors.New("PORTAL_AUTH_HEADER must be a single header name")
	}
	// A source offer pointing somewhere a browser cannot follow is worse than
	// none: it looks discharged while telling the reader nothing.
	if source, err := url.Parse(c.SourceURL); err != nil ||
		(source.Scheme != "https" && source.Scheme != "http") || source.Host == "" {
		return Config{}, errors.New("PORTAL_SOURCE_URL must be an absolute http or https URL: " + c.SourceURL)
	}
	// An unparseable entry is a hard error rather than a silent omission: the
	// operator would otherwise be locked out with no indication why.
	for _, id := range strings.Split(os.Getenv("PORTAL_ADMIN_STEAM_IDS"), ",") {
		if id = strings.TrimSpace(id); id == "" {
			continue
		}
		if !validSteamID(id) {
			return Config{}, errors.New("PORTAL_ADMIN_STEAM_IDS holds a value that is not a 17-digit SteamID64: " + id)
		}
		c.AdminSteamIDs[id] = struct{}{}
	}
	for _, p := range []string{c.DatabasePath, c.ArtifactRoot, c.MapRoot, c.MapSourceRoot, c.CSRFSecretFile, c.AgentTokenFile, c.ClientExecutable} {
		if !filepath.IsAbs(p) {
			return Config{}, errors.New("portal paths must be absolute")
		}
	}
	return c, nil
}

func loadProvisioningDefaults() (ProvisioningDefaults, error) {
	defaults := ProvisioningDefaults{
		JoinHost:       strings.TrimSpace(os.Getenv("PORTAL_DEFAULT_JOIN_HOST")),
		GamePort:       getenvInt("PORTAL_DEFAULT_GAME_PORT", 2456),
		PlayerLimit:    getenvInt("PORTAL_DEFAULT_PLAYER_LIMIT", 10),
		BackupInterval: getenv("PORTAL_DEFAULT_BACKUP_INTERVAL", "1h"),
		BackupAge:      getenvInt("PORTAL_DEFAULT_BACKUP_AGE_DAYS", 7),
		BackupCount:    getenvInt("PORTAL_DEFAULT_BACKUP_COUNT", 168),
	}
	if defaults.JoinHost != "" && !validJoinHost(defaults.JoinHost) {
		return ProvisioningDefaults{}, errors.New("PORTAL_DEFAULT_JOIN_HOST must be a valid hostname or IP address")
	}
	if defaults.GamePort < 1024 || defaults.GamePort > 65533 {
		return ProvisioningDefaults{}, errors.New("PORTAL_DEFAULT_GAME_PORT must be between 1024 and 65533")
	}
	if defaults.PlayerLimit < 1 || defaults.PlayerLimit > 100 {
		return ProvisioningDefaults{}, errors.New("PORTAL_DEFAULT_PLAYER_LIMIT must be between 1 and 100")
	}
	if defaults.BackupAge < 1 || defaults.BackupAge > 365 {
		return ProvisioningDefaults{}, errors.New("PORTAL_DEFAULT_BACKUP_AGE_DAYS must be between 1 and 365")
	}
	if defaults.BackupCount < 1 || defaults.BackupCount > 1000 {
		return ProvisioningDefaults{}, errors.New("PORTAL_DEFAULT_BACKUP_COUNT must be between 1 and 1000")
	}
	switch defaults.BackupInterval {
	case "30m", "1h", "6h", "daily":
	default:
		return ProvisioningDefaults{}, errors.New("PORTAL_DEFAULT_BACKUP_INTERVAL must be one of 30m, 1h, 6h, or daily")
	}
	return defaults, nil
}

func getenvInt(key string, fallback int) int {
	value := os.Getenv(key)
	if value == "" {
		return fallback
	}
	parsed, err := strconv.Atoi(value)
	if err != nil {
		return 0
	}
	return parsed
}

func getenv(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return fallback
}
