package app

import (
	"path/filepath"
	"strings"
	"testing"
)

func TestLoadConfigReadsProvisioningDefaults(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("PORTAL_CSRF_SECRET_FILE", filepath.Join(dir, "csrf"))
	t.Setenv("PORTAL_AGENT_TOKEN_FILE", filepath.Join(dir, "agent"))
	t.Setenv("PORTAL_TRUSTED_PROXY_CIDR", "192.0.2.0/24")
	t.Setenv("PORTAL_PUBLIC_BASE_URL", "https://portal.example.test")
	t.Setenv("PORTAL_DEFAULT_JOIN_HOST", "play.example.test")
	t.Setenv("PORTAL_DEFAULT_GAME_PORT", "24600")
	t.Setenv("PORTAL_DEFAULT_PLAYER_LIMIT", "12")
	t.Setenv("PORTAL_DEFAULT_BACKUP_INTERVAL", "6h")
	t.Setenv("PORTAL_DEFAULT_BACKUP_AGE_DAYS", "14")
	t.Setenv("PORTAL_DEFAULT_BACKUP_COUNT", "56")

	config, err := LoadConfig()
	if err != nil {
		t.Fatal(err)
	}
	want := ProvisioningDefaults{JoinHost: "play.example.test", GamePort: 24600, PlayerLimit: 12, BackupInterval: "6h", BackupAge: 14, BackupCount: 56}
	if config.Provisioning != want {
		t.Fatalf("provisioning defaults = %#v, want %#v", config.Provisioning, want)
	}
}

func TestLoadConfigRejectsInvalidProvisioningDefaults(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("PORTAL_CSRF_SECRET_FILE", filepath.Join(dir, "csrf"))
	t.Setenv("PORTAL_AGENT_TOKEN_FILE", filepath.Join(dir, "agent"))
	t.Setenv("PORTAL_TRUSTED_PROXY_CIDR", "192.0.2.0/24")
	t.Setenv("PORTAL_PUBLIC_BASE_URL", "https://portal.example.test")
	t.Setenv("PORTAL_DEFAULT_BACKUP_INTERVAL", "weekly")

	_, err := LoadConfig()
	if err == nil || !strings.Contains(err.Error(), "PORTAL_DEFAULT_BACKUP_INTERVAL") {
		t.Fatalf("configuration error = %v", err)
	}
}

// A missing public base URL used to fall back to the operator's own hostname,
// so an unconfigured deployment emitted links and redirects to a foreign host.
func TestLoadConfigRequiresPublicBaseURL(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("PORTAL_CSRF_SECRET_FILE", filepath.Join(dir, "csrf"))
	t.Setenv("PORTAL_AGENT_TOKEN_FILE", filepath.Join(dir, "agent"))
	t.Setenv("PORTAL_TRUSTED_PROXY_CIDR", "192.0.2.0/24")
	t.Setenv("PORTAL_PUBLIC_BASE_URL", "")

	_, err := LoadConfig()
	if err == nil || !strings.Contains(err.Error(), "PORTAL_PUBLIC_BASE_URL") {
		t.Fatalf("configuration error = %v", err)
	}
}
