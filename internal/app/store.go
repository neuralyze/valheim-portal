package app

import (
	"archive/zip"
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"time"

	_ "modernc.org/sqlite"

	"github.com/neuralyze/valheim-portal/internal/worldintel"
)

type Store struct{ db *sql.DB }

func OpenStore(path string) (*Store, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		return nil, err
	}
	db, err := sql.Open("sqlite", "file:"+path+"?_pragma=foreign_keys(1)&_pragma=journal_mode(WAL)&_pragma=busy_timeout(5000)")
	if err != nil {
		return nil, err
	}
	s := &Store{db: db}
	if err := s.Migrate(context.Background()); err != nil {
		db.Close()
		return nil, err
	}
	return s, nil
}
func (s *Store) Close() error { return s.db.Close() }

func (s *Store) Migrate(ctx context.Context) error {
	if _, err := s.db.ExecContext(ctx, `CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)`); err != nil {
		return err
	}
	var profileSchema int
	if err := s.db.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM schema_migrations WHERE version=5)`).Scan(&profileSchema); err != nil {
		return err
	}
	if profileSchema == 0 {
		if err := s.migrateProfileSchema(ctx); err != nil {
			return err
		}
	}
	var runtimeArtifactSchema int
	if err := s.db.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM schema_migrations WHERE version=6)`).Scan(&runtimeArtifactSchema); err != nil {
		return err
	}
	if runtimeArtifactSchema == 0 {
		if err := s.migrateRuntimeArtifactSchema(ctx); err != nil {
			return err
		}
	}
	var companionArtifactSchema int
	if err := s.db.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM schema_migrations WHERE version=7)`).Scan(&companionArtifactSchema); err != nil {
		return err
	}
	if companionArtifactSchema == 0 {
		if err := s.migrateFlatCompanionArtifactSchema(ctx); err != nil {
			return err
		}
	}
	if _, err := s.db.ExecContext(ctx, `
CREATE TABLE IF NOT EXISTS audit_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, world TEXT NOT NULL, operation TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed','rejected')),
 requested_by TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS public_worlds (
 name TEXT PRIMARY KEY, join_address TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('online','offline','maintenance')),
 server_version TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS steam_identities (
 steam_id TEXT PRIMARY KEY, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS world_members (
 world TEXT NOT NULL, steam_id TEXT NOT NULL REFERENCES steam_identities(steam_id) ON DELETE CASCADE,
 granted_by TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(world, steam_id)
);
INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (1, CURRENT_TIMESTAMP);
INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (2, CURRENT_TIMESTAMP);
INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (3, CURRENT_TIMESTAMP);
INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (4, CURRENT_TIMESTAMP);
INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (5, CURRENT_TIMESTAMP);
INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (6, CURRENT_TIMESTAMP);`); err != nil {
		return err
	}
	var worldEnabledSchema int
	if err := s.db.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM schema_migrations WHERE version=8)`).Scan(&worldEnabledSchema); err != nil {
		return err
	}
	if worldEnabledSchema == 0 {
		if _, err := s.db.ExecContext(ctx, `ALTER TABLE public_worlds ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)); INSERT INTO schema_migrations(version, applied_at) VALUES (8, CURRENT_TIMESTAMP);`); err != nil {
			return err
		}
	}
	var worldAnalysisSchema int
	if err := s.db.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM schema_migrations WHERE version=9)`).Scan(&worldAnalysisSchema); err != nil {
		return err
	}
	if worldAnalysisSchema == 0 {
		if _, err := s.db.ExecContext(ctx, `
CREATE TABLE world_analysis_snapshots (
 world TEXT NOT NULL,
 backup TEXT NOT NULL,
 source_sha256 TEXT NOT NULL,
 payload BLOB NOT NULL,
 analyzed_at TEXT NOT NULL,
 PRIMARY KEY(world, backup),
 UNIQUE(world, source_sha256)
);
CREATE INDEX world_analysis_latest ON world_analysis_snapshots(world, analyzed_at DESC);
INSERT INTO schema_migrations(version, applied_at) VALUES (9, CURRENT_TIMESTAMP);`); err != nil {
			return err
		}
	}
	var diagnosticsSchema int
	if err := s.db.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM schema_migrations WHERE version=10)`).Scan(&diagnosticsSchema); err != nil {
		return err
	}
	if diagnosticsSchema == 0 {
		if _, err := s.db.ExecContext(ctx, `
CREATE TABLE diagnostics (
 id TEXT PRIMARY KEY,
 world TEXT NOT NULL,
 profile TEXT NOT NULL,
 client_type TEXT NOT NULL CHECK(client_type IN ('vr','flat')),
 release_id TEXT NOT NULL,
 steam_id TEXT NOT NULL,
 name TEXT NOT NULL,
 sha256 TEXT NOT NULL,
 size INTEGER NOT NULL CHECK(size > 0),
 path TEXT NOT NULL UNIQUE,
 created_at TEXT NOT NULL
);
CREATE INDEX diagnostics_recent ON diagnostics(created_at DESC);
INSERT INTO schema_migrations(version, applied_at) VALUES (10, CURRENT_TIMESTAMP);`); err != nil {
			return err
		}
	}
	var profileSettingsSchema int
	if err := s.db.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM schema_migrations WHERE version=11)`).Scan(&profileSettingsSchema); err != nil {
		return err
	}
	if profileSettingsSchema == 0 {
		if _, err := s.db.ExecContext(ctx, `
CREATE TABLE profile_settings (
 world TEXT NOT NULL,
 profile TEXT NOT NULL,
 debug_logging INTEGER NOT NULL DEFAULT 0 CHECK(debug_logging IN (0,1)),
 updated_by TEXT NOT NULL DEFAULT '',
 updated_at TEXT NOT NULL,
 PRIMARY KEY(world, profile)
);
INSERT INTO schema_migrations(version, applied_at) VALUES (11, CURRENT_TIMESTAMP);`); err != nil {
			return err
		}
	}
	var diagnosticPluginSchema int
	if err := s.db.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM schema_migrations WHERE version=12)`).Scan(&diagnosticPluginSchema); err != nil {
		return err
	}
	if diagnosticPluginSchema == 0 {
		if err := s.migrateDiagnosticPluginArtifactSchema(ctx); err != nil {
			return err
		}
	}
	var steamIdentityNameSchema int
	if err := s.db.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM schema_migrations WHERE version=13)`).Scan(&steamIdentityNameSchema); err != nil {
		return err
	}
	if steamIdentityNameSchema == 0 {
		if _, err := s.db.ExecContext(ctx, `
ALTER TABLE steam_identities ADD COLUMN persona_name TEXT NOT NULL DEFAULT '';
ALTER TABLE steam_identities ADD COLUMN persona_synced_at TEXT NOT NULL DEFAULT '';
ALTER TABLE steam_identities ADD COLUMN label TEXT NOT NULL DEFAULT '';
INSERT INTO schema_migrations(version, applied_at) VALUES (13, CURRENT_TIMESTAMP);`); err != nil {
			return err
		}
	}
	var accessListSchema int
	if err := s.db.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM schema_migrations WHERE version=14)`).Scan(&accessListSchema); err != nil {
		return err
	}
	if accessListSchema == 0 {
		// Roles decide who lands in adminlist.txt; enforce_permitted decides
		// whether permittedlist.txt is written exclusively or left empty.
		if _, err := s.db.ExecContext(ctx, `
ALTER TABLE world_members ADD COLUMN role TEXT NOT NULL DEFAULT 'member' CHECK(role IN ('member','admin'));
ALTER TABLE public_worlds ADD COLUMN enforce_permitted INTEGER NOT NULL DEFAULT 0 CHECK(enforce_permitted IN (0,1));
CREATE TABLE world_access (
 world TEXT PRIMARY KEY,
 admins TEXT NOT NULL,
 permitted TEXT NOT NULL,
 applied_at TEXT NOT NULL,
 applied_by TEXT NOT NULL
);
INSERT INTO schema_migrations(version, applied_at) VALUES (14, CURRENT_TIMESTAMP);`); err != nil {
			return err
		}
	}
	var worldDescriptionSchema int
	if err := s.db.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM schema_migrations WHERE version=15)`).Scan(&worldDescriptionSchema); err != nil {
		return err
	}
	if worldDescriptionSchema == 0 {
		// Empty is a legitimate description, so existing worlds stay valid and the
		// player pages simply omit the blurb until an operator writes one.
		if _, err := s.db.ExecContext(ctx, `
ALTER TABLE public_worlds ADD COLUMN description TEXT NOT NULL DEFAULT '';
INSERT INTO schema_migrations(version, applied_at) VALUES (15, CURRENT_TIMESTAMP);`); err != nil {
			return err
		}
	}
	var agentChatSchema int
	if err := s.db.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM schema_migrations WHERE version=16)`).Scan(&agentChatSchema); err != nil {
		return err
	}
	if agentChatSchema == 0 {
		// The operator/agent conversation, and one row per verb an agent asked to run. The
		// call row is written before execution and updated after, so a request that fails
		// halfway still leaves a record of what was asked and how far it got.
		if _, err := s.db.ExecContext(ctx, `
CREATE TABLE agent_messages (
 id INTEGER PRIMARY KEY AUTOINCREMENT, conversation TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('operator','agent','system')),
 body TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX agent_messages_conversation ON agent_messages(conversation, id);
CREATE TABLE agent_verb_calls (
 id TEXT PRIMARY KEY, conversation TEXT NOT NULL, verb TEXT NOT NULL, class TEXT NOT NULL,
 world TEXT NOT NULL DEFAULT '', profile TEXT NOT NULL DEFAULT '', identifier TEXT NOT NULL DEFAULT '',
 version TEXT NOT NULL DEFAULT '', query TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL CHECK(status IN ('pending_approval','denied','refused','succeeded','failed')),
 requested_by TEXT NOT NULL DEFAULT '', decided_by TEXT NOT NULL DEFAULT '',
 evidence TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL, finished_at TEXT
);
CREATE INDEX agent_verb_calls_status ON agent_verb_calls(conversation, status);
INSERT INTO schema_migrations(version, applied_at) VALUES (16, CURRENT_TIMESTAMP);`); err != nil {
			return err
		}
	}
	var agentVerbArgsSchema int
	if err := s.db.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM schema_migrations WHERE version=17)`).Scan(&agentVerbArgsSchema); err != nil {
		return err
	}
	if agentVerbArgsSchema == 0 {
		// Publishing and release confirmation carry arguments the first cut of the verb-call
		// table had no place for. Recording them matters as much as the verb name: a release
		// nobody can trace back to its note and its client type is the thing that went wrong.
		if _, err := s.db.ExecContext(ctx, `
ALTER TABLE agent_verb_calls ADD COLUMN client_type TEXT NOT NULL DEFAULT '';
ALTER TABLE agent_verb_calls ADD COLUMN published_profile TEXT NOT NULL DEFAULT '';
ALTER TABLE agent_verb_calls ADD COLUMN release_ref TEXT NOT NULL DEFAULT '';
ALTER TABLE agent_verb_calls ADD COLUMN archive TEXT NOT NULL DEFAULT '';
ALTER TABLE agent_verb_calls ADD COLUMN notes TEXT NOT NULL DEFAULT '';
ALTER TABLE agent_verb_calls ADD COLUMN lines INTEGER NOT NULL DEFAULT 0;
INSERT INTO schema_migrations(version, applied_at) VALUES (17, CURRENT_TIMESTAMP);`); err != nil {
			return err
		}
	}

	// mod_add and mod_custom_add are refused by the agent without a scope of exactly "shared" or
	// "client-only", and the verb-call table had nowhere to put one - so those two verbs could not
	// succeed through the bridge at all, and the refusal said only "invalid mod selection".
	var agentScopeSchema int
	if err := s.db.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM schema_migrations WHERE version=18)`).Scan(&agentScopeSchema); err != nil {
		return err
	}
	if agentScopeSchema == 0 {
		if _, err := s.db.ExecContext(ctx, `
ALTER TABLE agent_verb_calls ADD COLUMN scope TEXT NOT NULL DEFAULT '';
INSERT INTO schema_migrations(version, applied_at) VALUES (18, CURRENT_TIMESTAMP);`); err != nil {
			return err
		}
	}
	return nil
}

func (s *Store) migrateProfileSchema(ctx context.Context) (err error) {
	conn, err := s.db.Conn(ctx)
	if err != nil {
		return err
	}
	defer conn.Close()
	if _, err = conn.ExecContext(ctx, `PRAGMA foreign_keys=OFF`); err != nil {
		return err
	}
	foreignKeysDisabled := true
	defer func() {
		if !foreignKeysDisabled {
			return
		}
		if _, reenableErr := conn.ExecContext(context.Background(), `PRAGMA foreign_keys=ON`); err == nil && reenableErr != nil {
			err = reenableErr
		}
	}()

	var haveReleases int
	if err = conn.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='releases')`).Scan(&haveReleases); err != nil {
		return err
	}
	if haveReleases == 0 {
		if _, err = conn.ExecContext(ctx, profileSchemaDDL); err != nil {
			return err
		}
		if _, err = conn.ExecContext(ctx, `PRAGMA foreign_keys=ON`); err != nil {
			return err
		}
		foreignKeysDisabled = false
		return nil
	}

	var haveArtifacts int
	if err = conn.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='artifacts')`).Scan(&haveArtifacts); err != nil {
		return err
	}
	tx, err := conn.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err = tx.ExecContext(ctx, `
CREATE TABLE releases_profile_v5 (
 id TEXT PRIMARY KEY, world TEXT NOT NULL, profile TEXT NOT NULL, client_type TEXT NOT NULL CHECK(client_type IN ('vr','flat')),
 version TEXT NOT NULL, notes TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('draft','published','archived')),
 maintenance INTEGER NOT NULL DEFAULT 0, published_at TEXT, published_by TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
 UNIQUE(world, profile, client_type, version)
);
CREATE TABLE artifacts_profile_v5 (
 id TEXT PRIMARY KEY, release_id TEXT NOT NULL REFERENCES releases(id) ON DELETE RESTRICT,
 kind TEXT NOT NULL CHECK(kind='profile'),
 name TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL CHECK(size >= 0), path TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
 UNIQUE(release_id, kind), UNIQUE(release_id, name)
);`); err != nil {
		return err
	}
	if _, err = tx.ExecContext(ctx, `INSERT INTO releases_profile_v5(id,world,profile,client_type,version,notes,status,maintenance,published_at,published_by,created_at) SELECT id,world,profile,client_type,version,notes,status,maintenance,published_at,published_by,created_at FROM releases`); err != nil {
		return err
	}
	if _, err = tx.ExecContext(ctx, `UPDATE releases_profile_v5 SET status='archived' WHERE status='published'`); err != nil {
		return err
	}
	if haveArtifacts != 0 {
		if _, err = tx.ExecContext(ctx, `INSERT INTO artifacts_profile_v5(id,release_id,kind,name,sha256,size,path,created_at) SELECT id,release_id,kind,name,sha256,size,path,created_at FROM artifacts WHERE kind='profile'`); err != nil {
			return err
		}
		if _, err = tx.ExecContext(ctx, `DROP TABLE artifacts`); err != nil {
			return err
		}
	}
	if _, err = tx.ExecContext(ctx, `DROP TABLE releases; ALTER TABLE releases_profile_v5 RENAME TO releases; ALTER TABLE artifacts_profile_v5 RENAME TO artifacts;`); err != nil {
		return err
	}
	if err = tx.Commit(); err != nil {
		return err
	}
	if _, err = conn.ExecContext(ctx, `PRAGMA foreign_keys=ON`); err != nil {
		return err
	}
	foreignKeysDisabled = false
	rows, err := conn.QueryContext(ctx, `PRAGMA foreign_key_check`)
	if err != nil {
		return err
	}
	defer rows.Close()
	if rows.Next() {
		return errors.New("profile schema migration failed foreign key check")
	}
	return rows.Err()
}
func (s *Store) migrateRuntimeArtifactSchema(ctx context.Context) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err := tx.ExecContext(ctx, `
CREATE TABLE artifacts_runtime_v6 (
 id TEXT PRIMARY KEY, release_id TEXT NOT NULL REFERENCES releases(id) ON DELETE RESTRICT,
 kind TEXT NOT NULL CHECK(kind IN ('profile','vr_runtime')),
 name TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL CHECK(size >= 0), path TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
 UNIQUE(release_id, kind), UNIQUE(release_id, name)
);
INSERT INTO artifacts_runtime_v6(id,release_id,kind,name,sha256,size,path,created_at)
 SELECT id,release_id,kind,name,sha256,size,path,created_at FROM artifacts;
DROP TABLE artifacts;
ALTER TABLE artifacts_runtime_v6 RENAME TO artifacts;
INSERT INTO schema_migrations(version, applied_at) VALUES (6, CURRENT_TIMESTAMP);`); err != nil {
		return err
	}
	return tx.Commit()
}

func (s *Store) migrateFlatCompanionArtifactSchema(ctx context.Context) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err := tx.ExecContext(ctx, `
CREATE TABLE artifacts_companion_v7 (
 id TEXT PRIMARY KEY, release_id TEXT NOT NULL REFERENCES releases(id) ON DELETE RESTRICT,
 kind TEXT NOT NULL CHECK(kind IN ('profile','vr_runtime','flat_companion')),
 name TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL CHECK(size >= 0), path TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
 UNIQUE(release_id, kind), UNIQUE(release_id, name)
);
INSERT INTO artifacts_companion_v7(id,release_id,kind,name,sha256,size,path,created_at)
 SELECT id,release_id,kind,name,sha256,size,path,created_at FROM artifacts;
DROP TABLE artifacts;
ALTER TABLE artifacts_companion_v7 RENAME TO artifacts;
INSERT INTO schema_migrations(version, applied_at) VALUES (7, CURRENT_TIMESTAMP);`); err != nil {
		return err
	}
	return tx.Commit()
}

// migrateDiagnosticPluginArtifactSchema widens the artifact kind CHECK constraint to
// allow the portal-hosted diagnostics plugin, which ships to both VR and Flat clients.
func (s *Store) migrateDiagnosticPluginArtifactSchema(ctx context.Context) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err := tx.ExecContext(ctx, `
CREATE TABLE artifacts_diagnostic_v12 (
 id TEXT PRIMARY KEY, release_id TEXT NOT NULL REFERENCES releases(id) ON DELETE RESTRICT,
 kind TEXT NOT NULL CHECK(kind IN ('profile','vr_runtime','flat_companion','diag_plugin')),
 name TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL CHECK(size >= 0), path TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
 UNIQUE(release_id, kind), UNIQUE(release_id, name)
);
INSERT INTO artifacts_diagnostic_v12(id,release_id,kind,name,sha256,size,path,created_at)
 SELECT id,release_id,kind,name,sha256,size,path,created_at FROM artifacts;
DROP TABLE artifacts;
ALTER TABLE artifacts_diagnostic_v12 RENAME TO artifacts;
INSERT INTO schema_migrations(version, applied_at) VALUES (12, CURRENT_TIMESTAMP);`); err != nil {
		return err
	}
	return tx.Commit()
}

const profileSchemaDDL = `
CREATE TABLE IF NOT EXISTS releases (
 id TEXT PRIMARY KEY, world TEXT NOT NULL, profile TEXT NOT NULL, client_type TEXT NOT NULL CHECK(client_type IN ('vr','flat')),
 version TEXT NOT NULL, notes TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('draft','published','archived')),
 maintenance INTEGER NOT NULL DEFAULT 0, published_at TEXT, published_by TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
 UNIQUE(world, profile, client_type, version)
);
CREATE TABLE IF NOT EXISTS artifacts (
 id TEXT PRIMARY KEY, release_id TEXT NOT NULL REFERENCES releases(id) ON DELETE RESTRICT,
 kind TEXT NOT NULL CHECK(kind='profile'),
 name TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL CHECK(size >= 0), path TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
 UNIQUE(release_id, kind), UNIQUE(release_id, name)
);`

func (s *Store) UpsertPublicWorld(ctx context.Context, world PublicWorld, actor string) error {
	if !validWorld(world.Name) || world.JoinAddress == "" || len(world.JoinAddress) > 255 || len(world.ServerVersion) > 100 || !validWorldDescription(world.Description) {
		return errors.New("invalid public world metadata")
	}
	switch world.Status {
	case "online", "offline", "maintenance":
	default:
		return errors.New("invalid public world status")
	}
	now := time.Now().UTC().Format(time.RFC3339Nano)
	_, err := s.db.ExecContext(ctx, `INSERT INTO public_worlds(name,join_address,status,server_version,description,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET join_address=excluded.join_address,status=excluded.status,server_version=excluded.server_version,description=excluded.description,updated_at=excluded.updated_at`, world.Name, world.JoinAddress, world.Status, world.ServerVersion, world.Description, now)
	if err == nil {
		err = s.Audit(ctx, actor, "world.public_metadata", world.Name, "status="+world.Status)
	}
	return err
}

func (s *Store) CreateProvisionedWorld(ctx context.Context, world PublicWorld, actor string) error {
	if !validWorld(world.Name) || world.JoinAddress == "" || len(world.JoinAddress) > 255 || len(world.ServerVersion) > 100 || !validWorldDescription(world.Description) {
		return errors.New("invalid provisioned world metadata")
	}
	if world.Status != "online" && world.Status != "offline" && world.Status != "maintenance" {
		return errors.New("invalid provisioned world status")
	}
	now := time.Now().UTC().Format(time.RFC3339Nano)
	_, err := s.db.ExecContext(ctx, `INSERT INTO public_worlds(name,join_address,status,server_version,description,enabled,updated_at) VALUES(?,?,?,?,?,0,?)`,
		world.Name, world.JoinAddress, world.Status, world.ServerVersion, world.Description, now)
	if err == nil {
		err = s.Audit(ctx, actor, "world.provision", world.Name, "status="+world.Status)
	}
	return err
}
func (s *Store) SetPublicWorldEnabled(ctx context.Context, name string, enabled bool, actor string) error {
	if !validWorld(name) {
		return errors.New("invalid world")
	}
	result, err := s.db.ExecContext(ctx, `UPDATE public_worlds SET enabled=?, updated_at=? WHERE name=?`, enabled, time.Now().UTC().Format(time.RFC3339Nano), name)
	if err != nil {
		return err
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return err
	}
	if affected != 1 {
		return sql.ErrNoRows
	}
	return s.Audit(ctx, actor, "world.enabled", name, fmt.Sprintf("enabled=%t", enabled))
}

// ProfileDebugLogging reports whether verbose client diagnostics are enabled for
// a world/profile pair. Absent rows mean disabled, so callers never need to seed.
func (s *Store) ProfileDebugLogging(ctx context.Context, world, profile string) (bool, error) {
	if !validWorld(world) || !validProfile(profile) {
		return false, errors.New("invalid profile scope")
	}
	var enabled bool
	err := s.db.QueryRowContext(ctx, `SELECT debug_logging FROM profile_settings WHERE world=? AND profile=?`, world, profile).Scan(&enabled)
	if errors.Is(err, sql.ErrNoRows) {
		return false, nil
	}
	return enabled, err
}

// DebugLoggingProfiles returns every world/profile pair with debug logging on,
// keyed as "world/profile", so the admin view can render state in one query.
func (s *Store) DebugLoggingProfiles(ctx context.Context) (map[string]bool, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT world, profile FROM profile_settings WHERE debug_logging=1`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	enabled := make(map[string]bool)
	for rows.Next() {
		var world, profile string
		if err := rows.Scan(&world, &profile); err != nil {
			return nil, err
		}
		enabled[world+"/"+profile] = true
	}
	return enabled, rows.Err()
}

func (s *Store) SetProfileDebugLogging(ctx context.Context, world, profile string, enabled bool, actor string) error {
	if !validWorld(world) || !validProfile(profile) {
		return errors.New("invalid profile scope")
	}
	if _, err := s.db.ExecContext(ctx, `
INSERT INTO profile_settings(world, profile, debug_logging, updated_by, updated_at) VALUES (?,?,?,?,?)
ON CONFLICT(world, profile) DO UPDATE SET debug_logging=excluded.debug_logging, updated_by=excluded.updated_by, updated_at=excluded.updated_at`,
		world, profile, enabled, actor, time.Now().UTC().Format(time.RFC3339Nano)); err != nil {
		return err
	}
	return s.Audit(ctx, actor, "profile.debug_logging", world+"/"+profile, fmt.Sprintf("enabled=%t", enabled))
}
func (s *Store) UnregisterPublicWorld(ctx context.Context, name, actor string) error {
	return s.removePublicWorld(ctx, name, actor, false)
}

func (s *Store) RetirePublicWorld(ctx context.Context, name, actor string) error {
	return s.removePublicWorld(ctx, name, actor, true)
}

func (s *Store) removePublicWorld(ctx context.Context, name, actor string, archiveReleases bool) error {
	if !validWorld(name) || strings.TrimSpace(actor) == "" || len(actor) > 200 {
		return errors.New("invalid world removal")
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var exists int
	if err = tx.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM public_worlds WHERE name=?)`, name).Scan(&exists); err != nil {
		return err
	}
	if exists == 0 {
		return sql.ErrNoRows
	}
	members, err := tx.ExecContext(ctx, `DELETE FROM world_members WHERE world=?`, name)
	if err != nil {
		return err
	}
	memberCount, err := members.RowsAffected()
	if err != nil {
		return err
	}
	var releaseCount int64
	if archiveReleases {
		releases, updateErr := tx.ExecContext(ctx, `UPDATE releases SET status=? WHERE world=? AND status=?`, Archived, name, Published)
		if updateErr != nil {
			return updateErr
		}
		releaseCount, err = releases.RowsAffected()
		if err != nil {
			return err
		}
	}
	result, err := tx.ExecContext(ctx, `DELETE FROM public_worlds WHERE name=?`, name)
	if err != nil {
		return err
	}
	if affected, rowsErr := result.RowsAffected(); rowsErr != nil {
		return rowsErr
	} else if affected != 1 {
		return sql.ErrNoRows
	}
	action, detail := "world.unregister", fmt.Sprintf("members_revoked=%d; server files and releases retained", memberCount)
	if archiveReleases {
		action = "world.delete"
		detail = fmt.Sprintf("members_revoked=%d; releases_archived=%d; external backups retained", memberCount, releaseCount)
	}
	if _, err = tx.ExecContext(ctx, `INSERT INTO audit_events(actor,action,target,detail,created_at) VALUES(?,?,?,?,?)`,
		actor, action, name, detail, time.Now().UTC().Format(time.RFC3339Nano)); err != nil {
		return err
	}
	return tx.Commit()
}

func (s *Store) PublicWorld(ctx context.Context, name string) (PublicWorld, error) {
	var world PublicWorld
	var updated string
	err := s.db.QueryRowContext(ctx, `SELECT name,join_address,status,server_version,description,enabled,updated_at FROM public_worlds WHERE name=?`, name).Scan(&world.Name, &world.JoinAddress, &world.Status, &world.ServerVersion, &world.Description, &world.Enabled, &updated)
	if err != nil {
		return PublicWorld{}, err
	}
	world.UpdatedAt, err = time.Parse(time.RFC3339Nano, updated)
	return world, err
}

func (s *Store) PublicWorlds(ctx context.Context) ([]PublicWorld, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT name,join_address,status,server_version,description,enabled,updated_at FROM public_worlds ORDER BY name`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var worlds []PublicWorld
	for rows.Next() {
		var world PublicWorld
		var updated string
		if err := rows.Scan(&world.Name, &world.JoinAddress, &world.Status, &world.ServerVersion, &world.Description, &world.Enabled, &updated); err != nil {
			return nil, err
		}
		if world.UpdatedAt, err = time.Parse(time.RFC3339Nano, updated); err != nil {
			return nil, err
		}
		worlds = append(worlds, world)
	}
	return worlds, rows.Err()
}
func (s *Store) RecordSteamIdentity(ctx context.Context, steamID string) error {
	if !validSteamID(steamID) {
		return errors.New("invalid Steam ID")
	}
	now := time.Now().UTC().Format(time.RFC3339Nano)
	_, err := s.db.ExecContext(ctx, `INSERT INTO steam_identities(steam_id,first_seen_at,last_seen_at) VALUES(?,?,?) ON CONFLICT(steam_id) DO UPDATE SET last_seen_at=excluded.last_seen_at`, steamID, now, now)
	return err
}
func (s *Store) GrantWorldAccess(ctx context.Context, world, steamID, actor string) error {
	if !validWorld(world) || !validSteamID(steamID) || actor == "" {
		return errors.New("invalid world membership")
	}
	if err := s.RecordSteamIdentity(ctx, steamID); err != nil {
		return err
	}
	_, err := s.db.ExecContext(ctx, `INSERT OR IGNORE INTO world_members(world,steam_id,granted_by,created_at) VALUES(?,?,?,?)`, world, steamID, actor, time.Now().UTC().Format(time.RFC3339Nano))
	if err == nil {
		err = s.Audit(ctx, actor, "world.member.grant", world, "steam_id="+steamID)
	}
	return err
}

func (s *Store) RevokeWorldAccess(ctx context.Context, world, steamID, actor string) error {
	if !validWorld(world) || !validSteamID(steamID) || actor == "" {
		return errors.New("invalid world membership")
	}
	result, err := s.db.ExecContext(ctx, `DELETE FROM world_members WHERE world=? AND steam_id=?`, world, steamID)
	if err != nil {
		return err
	}
	if count, err := result.RowsAffected(); err != nil {
		return err
	} else if count != 1 {
		return errors.New("world membership not found")
	}
	return s.Audit(ctx, actor, "world.member.revoke", world, "steam_id="+steamID)
}
func (s *Store) CanAccessWorld(ctx context.Context, world, steamID string) (bool, error) {
	if !validWorld(world) || !validSteamID(steamID) {
		return false, nil
	}
	var allowed int
	err := s.db.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM world_members m LEFT JOIN public_worlds p ON p.name=m.world WHERE m.world=? AND m.steam_id=? AND COALESCE(p.enabled,1)=1)`, world, steamID).Scan(&allowed)
	return allowed == 1, err
}
func (s *Store) PublicWorldsForSteam(ctx context.Context, steamID string) ([]PublicWorld, error) {
	if !validSteamID(steamID) {
		return nil, errors.New("invalid Steam ID")
	}
	rows, err := s.db.QueryContext(ctx, `SELECT p.name,p.join_address,p.status,p.server_version,p.description,p.enabled,p.updated_at FROM public_worlds p JOIN world_members m ON m.world=p.name WHERE m.steam_id=? AND p.enabled=1 ORDER BY p.name`, steamID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var worlds []PublicWorld
	for rows.Next() {
		var world PublicWorld
		var updated string
		if err := rows.Scan(&world.Name, &world.JoinAddress, &world.Status, &world.ServerVersion, &world.Description, &world.Enabled, &updated); err != nil {
			return nil, err
		}
		if world.UpdatedAt, err = time.Parse(time.RFC3339Nano, updated); err != nil {
			return nil, err
		}
		worlds = append(worlds, world)
	}
	return worlds, rows.Err()
}

// displayOrder sorts grants the way an operator scans them: named accounts
// first and alphabetically, then accounts that are still only a number. The
// alias lets a joined query address the identity columns.
func displayOrder(alias string) string {
	return `CASE WHEN ` + alias + `label!='' OR ` + alias + `persona_name!='' THEN 0 ELSE 1 END,
lower(CASE WHEN ` + alias + `label!='' THEN ` + alias + `label ELSE ` + alias + `persona_name END)`
}

// RecentSteamIdentities returns the most recently seen identities, presented in
// display order. Recency decides what the page shows and naming only decides
// how it is sorted, so a brand new unnamed player is never the row that a full
// identity list drops.
func (s *Store) RecentSteamIdentities(ctx context.Context, limit int) ([]SteamIdentity, error) {
	if limit < 1 || limit > 100 {
		return nil, errors.New("invalid Steam identity limit")
	}
	rows, err := s.db.QueryContext(ctx, `SELECT steam_id,persona_name,label,first_seen_at,last_seen_at FROM steam_identities ORDER BY last_seen_at DESC, steam_id LIMIT ?`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var identities []SteamIdentity
	for rows.Next() {
		var identity SteamIdentity
		var firstSeen, lastSeen string
		if err := rows.Scan(&identity.SteamID, &identity.PersonaName, &identity.Label, &firstSeen, &lastSeen); err != nil {
			return nil, err
		}
		if identity.FirstSeenAt, err = time.Parse(time.RFC3339Nano, firstSeen); err != nil {
			return nil, err
		}
		if identity.LastSeenAt, err = time.Parse(time.RFC3339Nano, lastSeen); err != nil {
			return nil, err
		}
		identities = append(identities, identity)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	slices.SortStableFunc(identities, func(a, b SteamIdentity) int {
		if named := boolCompare(b.PersonaName != "" || b.Label != "", a.PersonaName != "" || a.Label != ""); named != 0 {
			return named
		}
		return strings.Compare(strings.ToLower(a.DisplayName()), strings.ToLower(b.DisplayName()))
	})
	return identities, nil
}

func boolCompare(a, b bool) int {
	switch {
	case a == b:
		return 0
	case a:
		return 1
	}
	return -1
}

// SteamIdentityCount is the total on record, which can exceed the page the
// admin view renders.
func (s *Store) SteamIdentityCount(ctx context.Context) (int, error) {
	var count int
	err := s.db.QueryRowContext(ctx, `SELECT COUNT(*) FROM steam_identities`).Scan(&count)
	return count, err
}

// SteamIdentitiesToName lists every stored account for a name lookup, the
// never-synced ones first, so backfilling a deployment that already collected
// Steam IDs is not limited to whatever the admin page currently displays.
func (s *Store) SteamIdentitiesToName(ctx context.Context) ([]string, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT steam_id FROM steam_identities ORDER BY persona_synced_at, last_seen_at DESC LIMIT 1000`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var steamIDs []string
	for rows.Next() {
		var steamID string
		if err := rows.Scan(&steamID); err != nil {
			return nil, err
		}
		steamIDs = append(steamIDs, steamID)
	}
	return steamIDs, rows.Err()
}

// SetSteamPersonaNames records names fetched from Steam. Unknown Steam IDs are
// inserted so an operator can label and grant an account before it ever signs
// in, and an empty name never overwrites a name already on record.
func (s *Store) SetSteamPersonaNames(ctx context.Context, personas map[string]string) error {
	if len(personas) == 0 {
		return nil
	}
	now := time.Now().UTC().Format(time.RFC3339Nano)
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	for steamID, persona := range personas {
		if !validSteamID(steamID) || persona == "" {
			continue
		}
		if _, err := tx.ExecContext(ctx, `INSERT INTO steam_identities(steam_id,first_seen_at,last_seen_at,persona_name,persona_synced_at) VALUES(?,?,?,?,?)
ON CONFLICT(steam_id) DO UPDATE SET persona_name=excluded.persona_name, persona_synced_at=excluded.persona_synced_at`, steamID, now, now, persona, now); err != nil {
			return err
		}
	}
	return tx.Commit()
}

// SetSteamLabel stores the operator's own name for an account. An empty label
// clears the override and falls back to the Steam persona name.
func (s *Store) SetSteamLabel(ctx context.Context, steamID, label, actor string) error {
	if !validSteamID(steamID) || !validSteamLabel(label) || actor == "" {
		return errors.New("invalid Steam identity label")
	}
	now := time.Now().UTC().Format(time.RFC3339Nano)
	if _, err := s.db.ExecContext(ctx, `INSERT INTO steam_identities(steam_id,first_seen_at,last_seen_at,label) VALUES(?,?,?,?)
ON CONFLICT(steam_id) DO UPDATE SET label=excluded.label`, steamID, now, now, label); err != nil {
		return err
	}
	detail := "steam_id=" + steamID + " label=" + label
	if label == "" {
		detail = "steam_id=" + steamID + " label cleared"
	}
	return s.Audit(ctx, actor, "steam.identity.label", steamID, detail)
}

func (s *Store) WorldMembers(ctx context.Context) ([]WorldMember, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT m.world,m.steam_id,COALESCE(i.persona_name,''),COALESCE(i.label,''),m.role,m.granted_by,m.created_at
FROM world_members m LEFT JOIN steam_identities i ON i.steam_id=m.steam_id
ORDER BY `+displayOrder("i.")+`, m.steam_id, m.world`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var members []WorldMember
	for rows.Next() {
		var member WorldMember
		var created string
		if err := rows.Scan(&member.World, &member.SteamID, &member.PersonaName, &member.Label, &member.Role, &member.GrantedBy, &created); err != nil {
			return nil, err
		}
		if member.CreatedAt, err = time.Parse(time.RFC3339Nano, created); err != nil {
			return nil, err
		}
		members = append(members, member)
	}
	return members, rows.Err()
}

// SetWorldMemberRole promotes or demotes an existing grant. Only an admin role
// reaches adminlist.txt, so this is the one control that hands out in-game
// admin powers.
func (s *Store) SetWorldMemberRole(ctx context.Context, world, steamID, role, actor string) error {
	if !validWorld(world) || !validSteamID(steamID) || actor == "" || (role != "member" && role != "admin") {
		return errors.New("invalid world member role")
	}
	result, err := s.db.ExecContext(ctx, `UPDATE world_members SET role=? WHERE world=? AND steam_id=?`, role, world, steamID)
	if err != nil {
		return err
	}
	if count, err := result.RowsAffected(); err != nil {
		return err
	} else if count != 1 {
		return errors.New("world membership not found")
	}
	return s.Audit(ctx, actor, "world.member.role", world, "steam_id="+steamID+" role="+role)
}

// SetPermittedEnforcement decides whether permittedlist.txt is written as an
// exclusive allowlist. Enabling it makes portal membership authoritative on the
// server and locks out every account without a grant.
func (s *Store) SetPermittedEnforcement(ctx context.Context, world string, enforce bool, actor string) error {
	if !validWorld(world) || actor == "" {
		return errors.New("invalid permitted list enforcement")
	}
	value := 0
	if enforce {
		value = 1
	}
	result, err := s.db.ExecContext(ctx, `UPDATE public_worlds SET enforce_permitted=? WHERE name=?`, value, world)
	if err != nil {
		return err
	}
	if count, err := result.RowsAffected(); err != nil {
		return err
	} else if count != 1 {
		return sql.ErrNoRows
	}
	return s.Audit(ctx, actor, "world.access.enforcement", world, "enforce_permitted="+strconv.FormatBool(enforce))
}

// WorldAccessPlan is the access lists a world should have, next to the lists
// that were last written to it. Divergence means the host is stale.
type WorldAccessPlan struct {
	World            string
	EnforcePermitted bool
	Admins           []string
	Permitted        []string
	AppliedAdmins    []string
	AppliedPermitted []string
	AppliedAt        time.Time
	AppliedBy        string
	Applied          bool
}

// InSync reports whether the last applied lists still match the intended ones.
// A world that was never applied is out of sync unless both lists are empty.
func (p WorldAccessPlan) InSync() bool {
	return slices.Equal(p.Admins, p.AppliedAdmins) && slices.Equal(p.Permitted, p.AppliedPermitted)
}

func (p WorldAccessPlan) AdminCount() int     { return len(p.Admins) }
func (p WorldAccessPlan) PermittedCount() int { return len(p.Permitted) }

func (s *Store) WorldAccessPlans(ctx context.Context) ([]WorldAccessPlan, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT p.name, p.enforce_permitted,
 COALESCE(a.admins,''), COALESCE(a.permitted,''), COALESCE(a.applied_at,''), COALESCE(a.applied_by,''), a.world IS NOT NULL
FROM public_worlds p LEFT JOIN world_access a ON a.world=p.name ORDER BY p.name`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	plans := []WorldAccessPlan{}
	for rows.Next() {
		var plan WorldAccessPlan
		var enforce int
		var admins, permitted, appliedAt string
		if err := rows.Scan(&plan.World, &enforce, &admins, &permitted, &appliedAt, &plan.AppliedBy, &plan.Applied); err != nil {
			return nil, err
		}
		plan.EnforcePermitted = enforce == 1
		plan.AppliedAdmins = splitAccessList(admins)
		plan.AppliedPermitted = splitAccessList(permitted)
		if appliedAt != "" {
			if plan.AppliedAt, err = time.Parse(time.RFC3339Nano, appliedAt); err != nil {
				return nil, err
			}
		}
		plans = append(plans, plan)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	for index := range plans {
		plan := &plans[index]
		if plan.Admins, plan.Permitted, err = s.intendedAccessLists(ctx, plan.World, plan.EnforcePermitted); err != nil {
			return nil, err
		}
	}
	return plans, nil
}

func (s *Store) WorldAccessPlanFor(ctx context.Context, world string) (WorldAccessPlan, error) {
	plans, err := s.WorldAccessPlans(ctx)
	if err != nil {
		return WorldAccessPlan{}, err
	}
	for _, plan := range plans {
		if plan.World == world {
			return plan, nil
		}
	}
	return WorldAccessPlan{}, sql.ErrNoRows
}

// intendedAccessLists derives both lists from membership. Admins are always
// listed; members only when the world enforces its permitted list, because a
// non-empty permitted list is exclusive.
func (s *Store) intendedAccessLists(ctx context.Context, world string, enforce bool) ([]string, []string, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT steam_id, role FROM world_members WHERE world=? ORDER BY steam_id`, world)
	if err != nil {
		return nil, nil, err
	}
	defer rows.Close()
	admins, members := []string{}, []string{}
	for rows.Next() {
		var steamID, role string
		if err := rows.Scan(&steamID, &role); err != nil {
			return nil, nil, err
		}
		members = append(members, steamID)
		if role == "admin" {
			admins = append(admins, steamID)
		}
	}
	if err := rows.Err(); err != nil {
		return nil, nil, err
	}
	if !enforce {
		members = []string{}
	}
	return admins, members, nil
}

func (s *Store) RecordAccessApplied(ctx context.Context, world string, admins, permitted []string, actor string) error {
	if !validWorld(world) || actor == "" {
		return errors.New("invalid access list record")
	}
	now := time.Now().UTC().Format(time.RFC3339Nano)
	_, err := s.db.ExecContext(ctx, `INSERT INTO world_access(world,admins,permitted,applied_at,applied_by) VALUES(?,?,?,?,?)
ON CONFLICT(world) DO UPDATE SET admins=excluded.admins, permitted=excluded.permitted, applied_at=excluded.applied_at, applied_by=excluded.applied_by`,
		world, strings.Join(admins, ","), strings.Join(permitted, ","), now, actor)
	if err != nil {
		return err
	}
	return s.Audit(ctx, actor, "world.access.apply", world,
		"admins="+strconv.Itoa(len(admins))+" permitted="+strconv.Itoa(len(permitted)))
}

func splitAccessList(value string) []string {
	if value == "" {
		return []string{}
	}
	return strings.Split(value, ",")
}
func (s *Store) CreateRelease(ctx context.Context, r Release, actor string) error {
	if err := validateRelease(r); err != nil {
		return err
	}
	now := time.Now().UTC().Format(time.RFC3339Nano)
	_, err := s.db.ExecContext(ctx, `INSERT INTO releases(id,world,profile,client_type,version,notes,status,created_at) VALUES(?,?,?,?,?,?,?,?)`, r.ID, r.World, r.Profile, r.ClientType, r.Version, r.Notes, Draft, now)
	if err == nil {
		err = s.Audit(ctx, actor, "release.create", r.ID, fmt.Sprintf("world=%s version=%s", r.World, r.Version))
	}
	return err
}
func (s *Store) AddArtifact(ctx context.Context, a Artifact, actor string) error {
	if a.ID == "" || !validArtifactKind(a.Kind) || !validFilename(a.Name) || !validSHA256(a.SHA256) || a.Size < 1 || a.Path == "" {
		return errors.New("invalid artifact metadata")
	}
	var status, clientType string
	if err := s.db.QueryRowContext(ctx, "SELECT status,client_type FROM releases WHERE id=?", a.ReleaseID).Scan(&status, &clientType); err != nil {
		return err
	}
	if status != string(Draft) {
		return errors.New("published releases are immutable")
	}
	if a.Kind == "vr_runtime" && clientType != "vr" {
		return errors.New("VR runtime artifacts require a VR release")
	}
	if a.Kind == "flat_companion" && clientType != "flat" {
		return errors.New("Flat companion artifacts require a Flat release")
	}
	_, err := s.db.ExecContext(ctx, `INSERT INTO artifacts(id,release_id,kind,name,sha256,size,path,created_at) VALUES(?,?,?,?,?,?,?,?)`, a.ID, a.ReleaseID, a.Kind, a.Name, a.SHA256, a.Size, a.Path, time.Now().UTC().Format(time.RFC3339Nano))
	if err == nil {
		err = s.Audit(ctx, actor, "artifact.add", a.ID, a.Kind+":"+a.Name)
	}
	return err
}
func (s *Store) Publish(ctx context.Context, id, actor string) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	release := Release{ID: id}
	var status string
	if err = tx.QueryRowContext(ctx, "SELECT status,world,profile,client_type FROM releases WHERE id=?", id).Scan(&status, &release.World, &release.Profile, &release.ClientType); err != nil {
		return err
	}
	if status != string(Draft) {
		return errors.New("only drafts may be published")
	}
	rows, err := tx.QueryContext(ctx, "SELECT kind,name,sha256,size,path FROM artifacts WHERE release_id=?", id)
	if err != nil {
		return err
	}
	defer rows.Close()
	artifacts := map[string]Artifact{}
	var profileManifest ProfileManifest
	for rows.Next() {
		var artifact Artifact
		if err = rows.Scan(&artifact.Kind, &artifact.Name, &artifact.SHA256, &artifact.Size, &artifact.Path); err != nil {
			return err
		}
		if _, exists := artifacts[artifact.Kind]; exists {
			return fmt.Errorf("duplicate %s artifact", artifact.Kind)
		}
		if !validSHA256(artifact.SHA256) || artifact.Size < 1 || artifact.Path == "" {
			return errors.New("invalid artifact")
		}
		if err := verifyArtifact(artifact.Path, artifact.Size, artifact.SHA256); err != nil {
			return fmt.Errorf("artifact verification failed: %w", err)
		}
		if err := validateArtifactPayload(artifact.Kind, artifact.Path, release, &profileManifest); err != nil {
			return fmt.Errorf("artifact payload validation failed: %w", err)
		}
		artifacts[artifact.Kind] = artifact
	}
	if err = rows.Err(); err != nil {
		return err
	}
	if _, exists := artifacts["profile"]; !exists {
		return errors.New("missing required profile artifact")
	}
	if release.ClientType == "vr" {
		if _, exists := artifacts["vr_runtime"]; !exists {
			return errors.New("VR releases require a VR runtime artifact")
		}
		if profileManifest.Companion != nil {
			return errors.New("VR releases cannot declare a Flat companion")
		}
	} else if profileManifest.Companion == nil {
		if _, exists := artifacts["flat_companion"]; exists {
			return errors.New("Flat companion artifact is not declared by the profile manifest")
		}
	} else {
		companion, exists := artifacts["flat_companion"]
		if !exists {
			return errors.New("profile manifest declares a missing Flat companion artifact")
		}
		if companion.Name != profileManifest.Companion.Filename || companion.Size != profileManifest.Companion.Size || !strings.EqualFold(companion.SHA256, profileManifest.Companion.SHA256) {
			return errors.New("Flat companion artifact does not match the profile manifest")
		}
	}
	if release.ClientType == "flat" {
		if _, exists := artifacts["vr_runtime"]; exists {
			return errors.New("flat releases cannot include a VR runtime artifact")
		}
	}
	if _, err = tx.ExecContext(ctx, `UPDATE releases SET status=? WHERE world=? AND profile=? AND client_type=? AND status=?`, Archived, release.World, release.Profile, release.ClientType, Published); err != nil {
		return err
	}
	now := time.Now().UTC().Format(time.RFC3339Nano)
	if _, err = tx.ExecContext(ctx, "UPDATE releases SET status=?, published_at=?, published_by=? WHERE id=?", Published, now, actor, id); err != nil {
		return err
	}
	if _, err = tx.ExecContext(ctx, "INSERT INTO audit_events(actor,action,target,detail,created_at) VALUES(?,?,?,?,?)", actor, "release.publish", id, "validated immutable profile definition", now); err != nil {
		return err
	}
	return tx.Commit()
}
func verifyArtifact(path string, expectedSize int64, expectedHash string) error {
	file, err := os.Open(path)
	if err != nil {
		return err
	}

	defer file.Close()
	info, err := file.Stat()
	if err != nil || info.Size() != expectedSize {
		return errors.New("size mismatch")
	}
	hash := sha256.New()
	if _, err = io.Copy(hash, file); err != nil {
		return err
	}
	if hex.EncodeToString(hash.Sum(nil)) != expectedHash {
		return errors.New("checksum mismatch")
	}
	return nil
}

func validateArtifactPayload(kind, artifactPath string, release Release, profileManifest *ProfileManifest) error {
	switch kind {
	case "profile":
		return validateProfileArtifactPayload(artifactPath, release, profileManifest)
	case "vr_runtime":
		if release.ClientType != "vr" {
			return errors.New("VR runtime artifacts require a VR release")
		}
		return ValidateVRRuntimeArtifact(artifactPath)
	case "flat_companion":
		if release.ClientType != "flat" {
			return errors.New("Flat companion artifacts require a Flat release")
		}
		return ValidateFlatCompanionArtifact(artifactPath)
	case "diag_plugin":
		if err := ValidateDiagnosticPluginArtifact(artifactPath); err != nil {
			return err
		}
		// Publish-time only. Clients validate structure, not this policy, so that
		// adding a plugin directory does not break already-installed clients.
		return validatePortalOwnedPluginRoots(artifactPath)
	default:
		return errors.New("unsupported artifact kind")
	}
}

func validateProfileArtifactPayload(artifactPath string, release Release, output *ProfileManifest) error {
	archive, err := zip.OpenReader(artifactPath)
	if err != nil {
		return err
	}
	defer archive.Close()

	var manifest ProfileManifest
	haveManifest, haveConfig := false, false
	for _, file := range archive.File {
		if !validProfileArchiveEntry(file.Name) {
			return errors.New("profile archive contains an unsafe path")
		}
		info := file.FileInfo()
		if info.Mode()&os.ModeSymlink != 0 {
			return errors.New("profile archive contains a symbolic link")
		}
		switch {
		case file.Name == "profile-manifest.json":
			if haveManifest || info.IsDir() {
				return errors.New("profile archive has an invalid manifest entry")
			}
			manifest, err = readProfileManifest(file)
			if err != nil {
				return fmt.Errorf("invalid profile manifest: %w", err)
			}
			haveManifest = true
		case strings.HasPrefix(file.Name, "config/"):
			if !info.IsDir() {
				haveConfig = true
			}
		default:
			return errors.New("profile archive contains an unsupported entry")
		}
	}
	if !haveManifest || !haveConfig {
		return errors.New("profile archive requires profile-manifest.json and a config tree")
	}
	if err := validateProfileManifest(manifest, release); err != nil {
		return err
	}
	*output = manifest
	return nil
}

func validProfileArchiveEntry(name string) bool {
	if name == "" || strings.Contains(name, `\`) || strings.HasPrefix(name, "/") {
		return false
	}
	trimmed := strings.TrimSuffix(name, "/")
	return trimmed != "" && path.Clean(trimmed) == trimmed && !strings.HasPrefix(trimmed, "../")
}

func readProfileManifest(file *zip.File) (ProfileManifest, error) {
	if file.UncompressedSize64 > 1<<20 {
		return ProfileManifest{}, errors.New("manifest is too large")
	}
	reader, err := file.Open()
	if err != nil {
		return ProfileManifest{}, err
	}
	defer reader.Close()
	decoder := json.NewDecoder(io.LimitReader(reader, 1<<20))
	decoder.DisallowUnknownFields()
	var manifest ProfileManifest
	if err := decoder.Decode(&manifest); err != nil {
		return ProfileManifest{}, err
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		if err == nil {
			return ProfileManifest{}, errors.New("manifest contains multiple JSON values")
		}
		return ProfileManifest{}, err
	}
	return manifest, nil
}

func validateProfileManifest(manifest ProfileManifest, release Release) error {
	if manifest.Schema != 1 || manifest.World != release.World || manifest.Profile != release.Profile || manifest.ClientType != release.ClientType {
		return errors.New("profile manifest does not match its release")
	}
	if release.ClientType == "vr" && manifest.Companion != nil {
		return errors.New("VR profile manifest cannot declare a Flat companion")
	}
	if manifest.Companion != nil && (!validFilename(manifest.Companion.Filename) || !strings.HasSuffix(manifest.Companion.Filename, ".zip") || !validSHA256(manifest.Companion.SHA256) || manifest.Companion.Size < 1) {
		return errors.New("profile manifest has an invalid Flat companion")
	}
	previousFilename := ""
	for _, pkg := range manifest.Packages {
		if !validIdentifier(pkg.Namespace) || !validIdentifier(pkg.Name) || !validIdentifier(pkg.Version) || !validFilename(pkg.Filename) || !strings.HasSuffix(pkg.Filename, ".zip") || !validSHA256(pkg.SHA256) || pkg.Size < 1 {
			return errors.New("profile manifest has an invalid package")
		}
		if previousFilename >= pkg.Filename {
			return errors.New("profile manifest packages are not sorted by filename")
		}
		previousFilename = pkg.Filename
	}
	return nil
}

func (s *Store) Archive(ctx context.Context, id, actor string) error {
	_, err := s.db.ExecContext(ctx, "UPDATE releases SET status=? WHERE id=? AND status=?", Archived, id, Published)
	if err == nil {
		err = s.Audit(ctx, actor, "release.archive", id, "release removed from current listings")
	}
	return err
}

// ArchiveDraft makes an invalid or abandoned staging release permanently
// unavailable without deleting its immutable audit trail or artifacts.
func (s *Store) ArchiveDraft(ctx context.Context, id, actor string) error {
	result, err := s.db.ExecContext(ctx, "UPDATE releases SET status=? WHERE id=? AND status=?", Archived, id, Draft)
	if err != nil {
		return err
	}
	changed, err := result.RowsAffected()
	if err != nil {
		return err
	}
	if changed != 1 {
		return errors.New("only drafts may be archived")
	}
	return s.Audit(ctx, actor, "release.archive_draft", id, "invalid or abandoned draft archived")
}
func (s *Store) Audit(ctx context.Context, actor, action, target, detail string) error {
	_, err := s.db.ExecContext(ctx, "INSERT INTO audit_events(actor,action,target,detail,created_at) VALUES(?,?,?,?,?)", actor, action, target, detail, time.Now().UTC().Format(time.RFC3339Nano))
	return err
}
func (s *Store) Release(ctx context.Context, id string) (Release, error) {
	if id == "" {
		return Release{}, errors.New("release ID is required")
	}
	releases, err := s.releases(ctx, "WHERE id=?", id)
	if err != nil {
		return Release{}, err
	}
	if len(releases) != 1 {
		return Release{}, sql.ErrNoRows
	}
	return releases[0], nil
}

func (s *Store) CurrentReleases(ctx context.Context) ([]Release, error) {
	return s.releases(ctx, "WHERE status='published' ORDER BY world,profile,client_type,published_at DESC")
}
func (s *Store) CurrentRelease(ctx context.Context, world, profile, clientType string) (Release, error) {
	if !validWorld(world) || !validProfile(profile) || (clientType != "flat" && clientType != "vr") {
		return Release{}, errors.New("invalid profile selector")
	}
	releases, err := s.releases(ctx, "WHERE world=? AND profile=? AND client_type=? AND status='published'", world, profile, clientType)
	if err != nil {
		return Release{}, err
	}
	if len(releases) != 1 {
		return Release{}, sql.ErrNoRows
	}
	return releases[0], nil
}
func (s *Store) WorldReleases(ctx context.Context, world string) ([]Release, error) {
	return s.releases(ctx, "WHERE world=? AND status='published' ORDER BY CASE WHEN profile LIKE '%-nonvr' THEN 0 WHEN profile LIKE '%-flatvr' THEN 1 WHEN profile LIKE '%-vr' THEN 2 ELSE 3 END, profile, client_type, published_at DESC", world)
}
func (s *Store) ArchivedWorldReleases(ctx context.Context, world string) ([]Release, error) {
	return s.releases(ctx, "WHERE world=? AND status='archived' ORDER BY CASE WHEN profile LIKE '%-nonvr' THEN 0 WHEN profile LIKE '%-flatvr' THEN 1 WHEN profile LIKE '%-vr' THEN 2 ELSE 3 END, profile, client_type, published_at DESC", world)
}
func (s *Store) releases(ctx context.Context, where string, args ...any) ([]Release, error) {
	rows, err := s.db.QueryContext(ctx, "SELECT id,world,profile,client_type,version,notes,status,maintenance,published_at,published_by,created_at FROM releases "+where, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Release
	for rows.Next() {
		var r Release
		var publishedAt, publishedBy sql.NullString
		var c string
		var m int
		if err = rows.Scan(&r.ID, &r.World, &r.Profile, &r.ClientType, &r.Version, &r.Notes, &r.Status, &m, &publishedAt, &publishedBy, &c); err != nil {
			return nil, err
		}
		r.Maintenance = m == 1
		r.CreatedAt, _ = time.Parse(time.RFC3339Nano, c)
		r.PublishedBy = publishedBy.String
		if publishedAt.Valid {
			v, _ := time.Parse(time.RFC3339Nano, publishedAt.String)
			r.PublishedAt = &v
		}
		out = append(out, r)
	}
	return out, rows.Err()
}
func (s *Store) Artifacts(ctx context.Context, id string) ([]Artifact, error) {
	return s.artifacts(ctx, "SELECT id,release_id,kind,name,sha256,size,path,created_at FROM artifacts WHERE release_id=? ORDER BY kind", id)
}
func (s *Store) PublishedArtifacts(ctx context.Context, id string) ([]Artifact, error) {
	return s.artifacts(ctx, "SELECT a.id,a.release_id,a.kind,a.name,a.sha256,a.size,a.path,a.created_at FROM artifacts a JOIN releases r ON r.id=a.release_id WHERE a.release_id=? AND r.status='published' ORDER BY a.kind", id)
}
func (s *Store) HistoricalArtifacts(ctx context.Context, id string) ([]Artifact, error) {
	return s.artifacts(ctx, "SELECT a.id,a.release_id,a.kind,a.name,a.sha256,a.size,a.path,a.created_at FROM artifacts a JOIN releases r ON r.id=a.release_id WHERE a.release_id=? AND r.status IN ('published','archived') ORDER BY a.kind", id)
}
func (s *Store) artifacts(ctx context.Context, query string, args ...any) ([]Artifact, error) {
	rows, err := s.db.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Artifact
	for rows.Next() {
		var a Artifact
		var c string
		if err = rows.Scan(&a.ID, &a.ReleaseID, &a.Kind, &a.Name, &a.SHA256, &a.Size, &a.Path, &c); err != nil {
			return nil, err
		}
		a.CreatedAt, _ = time.Parse(time.RFC3339Nano, c)
		out = append(out, a)
	}
	return out, rows.Err()
}
func (s *Store) Artifact(ctx context.Context, id string) (Artifact, error) {
	return s.artifact(ctx, "SELECT id,release_id,kind,name,sha256,size,path,created_at FROM artifacts WHERE id=?", id)
}
func (s *Store) PublishedArtifact(ctx context.Context, id string) (Artifact, error) {
	return s.artifact(ctx, "SELECT a.id,a.release_id,a.kind,a.name,a.sha256,a.size,a.path,a.created_at FROM artifacts a JOIN releases r ON r.id=a.release_id WHERE a.id=? AND r.status='published'", id)
}
func (s *Store) HistoricalArtifact(ctx context.Context, id string) (Artifact, error) {
	return s.artifact(ctx, "SELECT a.id,a.release_id,a.kind,a.name,a.sha256,a.size,a.path,a.created_at FROM artifacts a JOIN releases r ON r.id=a.release_id WHERE a.id=? AND r.status IN ('published','archived')", id)
}
func (s *Store) artifact(ctx context.Context, query string, id string) (Artifact, error) {
	var a Artifact
	var c string
	err := s.db.QueryRowContext(ctx, query, id).Scan(&a.ID, &a.ReleaseID, &a.Kind, &a.Name, &a.SHA256, &a.Size, &a.Path, &c)
	a.CreatedAt, _ = time.Parse(time.RFC3339Nano, c)
	return a, err
}
func validArtifactKind(k string) bool {
	return k == "profile" || k == "vr_runtime" || k == "flat_companion" || k == "diag_plugin"
}
func validFilename(n string) bool {
	return n != "" && n != "." && n != ".." && filepath.Base(n) == n && len(n) <= 180
}
func validSHA256(value string) bool {
	if len(value) != 64 || strings.ToLower(value) != value {
		return false
	}
	_, err := hex.DecodeString(value)
	return err == nil
}
func validID(id string) bool {
	if len(id) != 32 {
		return false
	}
	_, err := hex.DecodeString(id)
	return err == nil
}
func validateRelease(r Release) error {
	if !validIdentifier(r.ID) || !validWorld(r.World) || !validProfile(r.Profile) || !validIdentifier(r.Version) || (r.ClientType != "vr" && r.ClientType != "flat") || len(r.Notes) > 10000 {
		return errors.New("invalid release")
	}
	return nil
}
func validWorld(s string) bool   { return validIdentifier(s) }
func validProfile(s string) bool { return validIdentifier(s) }
func validIdentifier(s string) bool {
	if len(s) == 0 || len(s) > 80 {
		return false
	}
	for _, c := range s {
		if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '-' || c == '_' || c == '.') {
			return false
		}
	}
	return true
}
func validSteamID(steamID string) bool {
	if len(steamID) != 17 || steamID[0] != '7' {
		return false
	}
	for _, c := range steamID {
		if c < '0' || c > '9' {
			return false
		}
	}
	return true
}

// validSteamLabel accepts an empty label (clearing the override) and otherwise
// a short single-line display name. Control characters are rejected so a label
// can never smuggle markup or log-injection payloads into the admin page.
func validSteamLabel(label string) bool {
	if len(label) > 64 || label != strings.TrimSpace(label) {
		return false
	}
	for _, c := range label {
		if c < 0x20 || c == 0x7f {
			return false
		}
	}
	return true
}

// validWorldDescription accepts an empty blurb (no description set) and
// otherwise a short multi-line paragraph. Newlines and tabs stay allowed so an
// operator can shape the text in the admin textarea; every other control
// character is rejected so a description can never smuggle markup-adjacent
// junk or break the textarea it is edited in. The cap keeps the home page tile
// grid from being distorted by an essay.
func validWorldDescription(description string) bool {
	if len(description) > 500 {
		return false
	}
	for _, c := range description {
		if c == '\n' || c == '\t' {
			continue
		}
		if c < 0x20 || c == 0x7f {
			return false
		}
	}
	return true
}

func (s *Store) allReleases(ctx context.Context) ([]Release, error) {
	return s.releases(ctx, "ORDER BY created_at DESC")
}

func (s *Store) PublicRelease(ctx context.Context, id string, historical bool) (Release, error) {
	statuses := "status='published'"
	if historical {
		statuses = "status IN ('published','archived')"
	}
	row := s.db.QueryRowContext(ctx, "SELECT id,world,profile,client_type,version,notes,status,maintenance,published_at,published_by,created_at FROM releases WHERE id=? AND "+statuses, id)
	var r Release
	var publishedAt, createdAt string
	var maintenance int
	if err := row.Scan(&r.ID, &r.World, &r.Profile, &r.ClientType, &r.Version, &r.Notes, &r.Status, &maintenance, &publishedAt, &r.PublishedBy, &createdAt); err != nil {
		return Release{}, err
	}
	r.Maintenance = maintenance == 1
	r.CreatedAt, _ = time.Parse(time.RFC3339Nano, createdAt)
	if publishedAt != "" {
		v, _ := time.Parse(time.RFC3339Nano, publishedAt)
		r.PublishedAt = &v
	}
	return r, nil
}

func (s *Store) CreateJob(ctx context.Context, j Job, actor string) error {
	if j.ID == "" || !validWorld(j.World) || !recordableOperation(j.Operation) {
		return errors.New("invalid job")
	}
	now := time.Now().UTC().Format(time.RFC3339Nano)
	if _, err := s.db.ExecContext(ctx, `INSERT INTO jobs(id,world,operation,status,requested_by,detail,created_at) VALUES(?,?,?,?,?,?,?)`,
		j.ID, j.World, j.Operation, "queued", j.RequestedBy, "", now); err != nil {
		return err
	}
	return s.Audit(ctx, actor, "job.queue", j.ID, j.World+":"+j.Operation)
}

func (s *Store) FinishJob(ctx context.Context, id, status, detail, actor string) error {
	if status != "succeeded" && status != "failed" && status != "rejected" {
		return errors.New("invalid job status")
	}
	now := time.Now().UTC().Format(time.RFC3339Nano)
	if _, err := s.db.ExecContext(ctx, `UPDATE jobs SET status=?, detail=?, finished_at=? WHERE id=?`,
		status, detail, now, id); err != nil {
		return err
	}
	return s.Audit(ctx, actor, "job."+status, id, detail)
}

func (s *Store) RecentJobs(ctx context.Context, limit int) ([]Job, error) {
	if limit < 1 || limit > 100 {
		return nil, errors.New("invalid job limit")
	}
	rows, err := s.db.QueryContext(ctx, `SELECT id,world,operation,status,requested_by,detail,created_at,finished_at FROM jobs ORDER BY created_at DESC LIMIT ?`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var jobs []Job
	for rows.Next() {
		var j Job
		var createdAt string
		var finishedAt sql.NullString
		if err := rows.Scan(&j.ID, &j.World, &j.Operation, &j.Status, &j.RequestedBy, &j.Detail, &createdAt, &finishedAt); err != nil {
			return nil, err
		}
		j.CreatedAt, _ = time.Parse(time.RFC3339Nano, createdAt)
		if finishedAt.Valid {
			v, _ := time.Parse(time.RFC3339Nano, finishedAt.String)
			j.FinishedAt = &v
		}
		jobs = append(jobs, j)
	}
	return jobs, rows.Err()
}

func (s *Store) RecentAudit(ctx context.Context, limit int) ([]AuditEvent, error) {
	if limit < 1 || limit > 100 {
		return nil, errors.New("invalid audit limit")
	}
	rows, err := s.db.QueryContext(ctx, `SELECT id,actor,action,target,detail,created_at FROM audit_events ORDER BY id DESC LIMIT ?`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var events []AuditEvent
	for rows.Next() {
		var event AuditEvent
		var createdAt string
		if err := rows.Scan(&event.ID, &event.Actor, &event.Action, &event.Target, &event.Detail, &createdAt); err != nil {
			return nil, err
		}
		event.CreatedAt, _ = time.Parse(time.RFC3339Nano, createdAt)
		events = append(events, event)
	}
	return events, rows.Err()
}

func (s *Store) SaveWorldAnalysis(ctx context.Context, snapshot worldintel.Snapshot, actor string) error {
	if !validWorld(snapshot.World) || snapshot.Schema != worldintel.SchemaVersion || snapshot.Source.Backup == "" || len(snapshot.Source.SHA256) != 64 {
		return errors.New("invalid world analysis")
	}
	payload, err := json.Marshal(snapshot)
	if err != nil || len(payload) > 4<<20 {
		return errors.New("world analysis payload is invalid or too large")
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	now := time.Now().UTC().Format(time.RFC3339Nano)
	if _, err = tx.ExecContext(ctx, `INSERT INTO world_analysis_snapshots(world,backup,source_sha256,payload,analyzed_at)
VALUES(?,?,?,?,?)
ON CONFLICT(world,backup) DO UPDATE SET source_sha256=excluded.source_sha256,payload=excluded.payload,analyzed_at=excluded.analyzed_at
ON CONFLICT(world,source_sha256) DO UPDATE SET backup=excluded.backup,payload=excluded.payload,analyzed_at=excluded.analyzed_at`,
		snapshot.World, snapshot.Source.Backup, snapshot.Source.SHA256, payload, now); err != nil {
		return err
	}
	if _, err = tx.ExecContext(ctx, `DELETE FROM world_analysis_snapshots WHERE world=? AND backup NOT IN
(SELECT backup FROM world_analysis_snapshots WHERE world=? ORDER BY analyzed_at DESC LIMIT 20)`, snapshot.World, snapshot.World); err != nil {
		return err
	}
	if err = tx.Commit(); err != nil {
		return err
	}
	return s.Audit(ctx, actor, "world.analysis", snapshot.World, snapshot.Source.Backup)
}

func (s *Store) LatestWorldAnalyses(ctx context.Context, world string, limit int) ([]worldintel.Snapshot, error) {
	if !validWorld(world) || limit < 1 || limit > 20 {
		return nil, errors.New("invalid world analysis query")
	}
	rows, err := s.db.QueryContext(ctx, `SELECT payload FROM world_analysis_snapshots WHERE world=? ORDER BY analyzed_at DESC LIMIT ?`, world, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var snapshots []worldintel.Snapshot
	for rows.Next() {
		var payload []byte
		if err := rows.Scan(&payload); err != nil {
			return nil, err
		}
		var snapshot worldintel.Snapshot
		if len(payload) > 4<<20 || json.Unmarshal(payload, &snapshot) != nil || snapshot.World != world || snapshot.Schema != worldintel.SchemaVersion {
			return nil, errors.New("stored world analysis is corrupt")
		}
		snapshots = append(snapshots, snapshot)
	}
	return snapshots, rows.Err()
}
