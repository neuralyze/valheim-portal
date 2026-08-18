package agent

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestVerifyRejectsTamperingUnknownWorldAndStaleRequest(t *testing.T) {
	token := []byte("12345678901234567890123456789012")
	allowed := map[string]struct{}{"Asgard-Redesign": {}}
	r := Request{ID: "job1", World: "Asgard-Redesign", Operation: "backup", Timestamp: time.Now().Unix()}
	r.Signature = Sign(token, r)
	if err := Verify(token, allowed, r); err != nil {
		t.Fatal(err)
	}
	r.World = "other"
	if err := Verify(token, allowed, r); err == nil {
		t.Fatal("accepted changed world")
	}
	r.World = "Asgard-Redesign"
	r.Timestamp = time.Now().Add(-2 * time.Minute).Unix()
	r.Signature = Sign(token, r)
	if err := Verify(token, allowed, r); err == nil {
		t.Fatal("accepted stale request")
	}
}

func TestRestoreVerificationBindsBackupName(t *testing.T) {
	token := []byte("12345678901234567890123456789012")
	allowed := map[string]struct{}{"Asgard-Redesign": {}}
	r := Request{ID: "restore1", World: "Asgard-Redesign", Operation: "restore", Backup: "world-Asgard-Redesign-before-change.tgz", Timestamp: time.Now().Unix()}
	r.Signature = Sign(token, r)
	if err := Verify(token, allowed, r); err != nil {
		t.Fatal(err)
	}
	r.Backup = "../world-Asgard-Redesign-before-change.tgz"
	r.Signature = Sign(token, r)
	if err := Verify(token, allowed, r); err == nil {
		t.Fatal("accepted a traversal restore backup")
	}
	r = Request{ID: "status1", World: "Asgard-Redesign", Operation: "status", Backup: "world-Asgard-Redesign-before-change.tgz", Timestamp: time.Now().Unix()}
	r.Signature = Sign(token, r)
	if err := Verify(token, allowed, r); err == nil {
		t.Fatal("accepted a backup argument for status")
	}
}

func TestRestoreExecutionUsesFixedBackupStopRestoreSequence(t *testing.T) {
	root := t.TempDir()
	scripts := filepath.Join(root, "scripts")
	if err := os.MkdirAll(filepath.Join(root, "Asgard-Redesign"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(scripts, 0o700); err != nil {
		t.Fatal(err)
	}
	log := filepath.Join(root, "operations.log")
	t.Setenv("LOG", log)
	script := []byte("#!/bin/sh\nprintf '%s:%s\\n' \"$(basename \"$0\")\" \"$*\" >> \"$LOG\"\n")
	for _, name := range []string{"backup_valheim_world.sh", "stop_valheim_server.sh", "restore_valheim_world.sh"} {
		if err := os.WriteFile(filepath.Join(scripts, name), script, 0o700); err != nil {
			t.Fatal(err)
		}
	}
	r := Request{World: "Asgard-Redesign", Operation: "restore", Backup: "world-Asgard-Redesign-known-good.tgz"}
	response := execute(context.Background(), scripts, root, map[string]struct{}{"Asgard-Redesign": {}}, r)
	if response.Status != "succeeded" {
		t.Fatalf("response = %#v", response)
	}
	got, err := os.ReadFile(log)
	if err != nil {
		t.Fatal(err)
	}
	want := "backup_valheim_world.sh:Asgard-Redesign\nstop_valheim_server.sh:Asgard-Redesign\nrestore_valheim_world.sh:Asgard-Redesign world-Asgard-Redesign-known-good.tgz\n"
	if string(got) != want {
		t.Fatalf("operations = %q, want %q", got, want)
	}
}

func TestDeleteServerExecutionUsesFixedFinalBackupStopDeleteSequence(t *testing.T) {
	root := t.TempDir()
	scripts := filepath.Join(root, "scripts")
	if err := os.MkdirAll(filepath.Join(root, "Asgard-Redesign"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(scripts, 0o700); err != nil {
		t.Fatal(err)
	}
	log := filepath.Join(root, "operations.log")
	t.Setenv("LOG", log)
	script := []byte("#!/bin/sh\nprintf '%s:%s\\n' \"$(basename \"$0\")\" \"$*\" >> \"$LOG\"\n")
	for _, name := range []string{"backup_valheim_world.sh", "stop_valheim_server.sh", "portal_delete_valheim_server.sh"} {
		if err := os.WriteFile(filepath.Join(scripts, name), script, 0o700); err != nil {
			t.Fatal(err)
		}
	}
	request := Request{World: "Asgard-Redesign", Operation: "delete_server"}
	response := execute(context.Background(), scripts, root, map[string]struct{}{"Asgard-Redesign": {}}, request)
	if response.Status != "succeeded" {
		t.Fatalf("response = %#v", response)
	}
	got, err := os.ReadFile(log)
	if err != nil {
		t.Fatal(err)
	}
	want := "backup_valheim_world.sh:Asgard-Redesign\nstop_valheim_server.sh:Asgard-Redesign\nportal_delete_valheim_server.sh:Asgard-Redesign\n"
	if string(got) != want {
		t.Fatalf("operations = %q, want %q", got, want)
	}
}

func TestDeleteServerDoesNotStopOrDeleteWhenFinalBackupFails(t *testing.T) {
	root := t.TempDir()
	scripts := filepath.Join(root, "scripts")
	if err := os.MkdirAll(filepath.Join(root, "Asgard"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(scripts, 0o700); err != nil {
		t.Fatal(err)
	}
	log := filepath.Join(root, "operations.log")
	t.Setenv("LOG", log)
	if err := os.WriteFile(filepath.Join(scripts, "backup_valheim_world.sh"), []byte("#!/bin/sh\nprintf 'backup\\n' >> \"$LOG\"\nexit 9\n"), 0o700); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"stop_valheim_server.sh", "portal_delete_valheim_server.sh"} {
		if err := os.WriteFile(filepath.Join(scripts, name), []byte("#!/bin/sh\nprintf 'unsafe\\n' >> \"$LOG\"\n"), 0o700); err != nil {
			t.Fatal(err)
		}
	}
	response := execute(context.Background(), scripts, root, map[string]struct{}{"Asgard": {}}, Request{World: "Asgard", Operation: "delete_server"})
	if response.Status != "failed" {
		t.Fatalf("response = %#v", response)
	}
	got, err := os.ReadFile(log)
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != "backup\n" {
		t.Fatalf("unsafe operations ran after failed backup: %q", got)
	}
}

func TestPortChangeUsesFixedBackupStopConfigureStartSequence(t *testing.T) {
	root := t.TempDir()
	scripts := filepath.Join(root, "scripts")
	if err := os.MkdirAll(filepath.Join(root, "Asgard-Redesign"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(scripts, 0o700); err != nil {
		t.Fatal(err)
	}
	log := filepath.Join(root, "operations.log")
	t.Setenv("LOG", log)
	script := []byte("#!/bin/sh\nprintf '%s:%s\\n' \"$(basename \"$0\")\" \"$*\" >> \"$LOG\"\n")
	for _, name := range []string{"backup_valheim_world.sh", "stop_valheim_server.sh", "configure_valheim_port.sh", "start_valheim_server.sh"} {
		if err := os.WriteFile(filepath.Join(scripts, name), script, 0o700); err != nil {
			t.Fatal(err)
		}
	}
	request := Request{World: "Asgard-Redesign", Operation: "set_port", Port: 24600}
	response := execute(context.Background(), scripts, root, map[string]struct{}{"Asgard-Redesign": {}}, request)
	if response.Status != "succeeded" {
		t.Fatalf("response = %#v", response)
	}
	got, err := os.ReadFile(log)
	if err != nil {
		t.Fatal(err)
	}
	want := "backup_valheim_world.sh:Asgard-Redesign\nstop_valheim_server.sh:Asgard-Redesign\nconfigure_valheim_port.sh:Asgard-Redesign 24600\nstart_valheim_server.sh:Asgard-Redesign\n"
	if string(got) != want {
		t.Fatalf("operations = %q, want %q", got, want)
	}
}

func TestVerifyBindsGamePort(t *testing.T) {
	token := []byte("12345678901234567890123456789012")
	allowed := map[string]struct{}{"Asgard-Redesign": {}}
	request := Request{ID: "port1", World: "Asgard-Redesign", Operation: "set_port", Port: 24600, Timestamp: time.Now().Unix()}
	request.Signature = Sign(token, request)
	if err := Verify(token, allowed, request); err != nil {
		t.Fatal(err)
	}
	request.Port++
	if err := Verify(token, allowed, request); err == nil {
		t.Fatal("accepted a modified port")
	}
}

func TestWorldCreateVerificationBindsAndRequiresASeed(t *testing.T) {
	token := []byte("12345678901234567890123456789012")
	allowed := map[string]struct{}{"Asgard-Redesign": {}}
	request := Request{ID: "worldgen1", World: "Asgard-Redesign", Operation: "world_create", Seed: "SeedTest01", Timestamp: time.Now().Unix()}
	request.Signature = Sign(token, request)
	if err := Verify(token, allowed, request); err != nil {
		t.Fatal(err)
	}
	request.Seed = "different01"
	if err := Verify(token, allowed, request); err == nil {
		t.Fatal("accepted a modified seed")
	}
	for _, seed := range []string{"", "has space", "dash-dash", strings.Repeat("a", 65)} {
		seeded := Request{ID: "worldgen2", World: "Asgard-Redesign", Operation: "world_create", Seed: seed, Timestamp: time.Now().Unix()}
		seeded.Signature = Sign(token, seeded)
		if err := Verify(token, allowed, seeded); err == nil {
			t.Fatalf("accepted world_create with seed %q", seed)
		}
	}
	extra := Request{ID: "worldgen3", World: "Asgard-Redesign", Operation: "world_create", Seed: "SeedTest01", SourceWorld: "Asgard", Timestamp: time.Now().Unix()}
	extra.Signature = Sign(token, extra)
	if err := Verify(token, allowed, extra); err == nil {
		t.Fatal("accepted world_create carrying provisioning arguments")
	}
}

func TestWorldCreateExecutionPassesOnlyTheWorldAndSeed(t *testing.T) {
	root := t.TempDir()
	scripts := filepath.Join(root, "scripts")
	if err := os.MkdirAll(filepath.Join(root, "Asgard-Redesign"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(scripts, 0o700); err != nil {
		t.Fatal(err)
	}
	log := filepath.Join(root, "operations.log")
	t.Setenv("LOG", log)
	script := []byte("#!/bin/sh\nprintf '%s:%s\\n' \"$(basename \"$0\")\" \"$*\" >> \"$LOG\"\n")
	if err := os.WriteFile(filepath.Join(scripts, "portal_create_valheim_world.sh"), script, 0o700); err != nil {
		t.Fatal(err)
	}
	request := Request{World: "Asgard-Redesign", Operation: "world_create", Seed: "SeedTest01"}
	response := execute(context.Background(), scripts, root, map[string]struct{}{"Asgard-Redesign": {}}, request)
	if response.Status != "succeeded" {
		t.Fatalf("response = %#v", response)
	}
	got, err := os.ReadFile(log)
	if err != nil {
		t.Fatal(err)
	}
	want := "portal_create_valheim_world.sh:Asgard-Redesign SeedTest01\n"
	if string(got) != want {
		t.Fatalf("operations = %q, want %q", got, want)
	}
}

func TestExecuteRejectsWorldSymlinkEscape(t *testing.T) {
	root := t.TempDir()
	outside := t.TempDir()
	scripts := filepath.Join(root, "scripts")
	if err := os.Mkdir(scripts, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(scripts, "status_valheim_server.sh"), []byte("#!/bin/sh\nexit 0\n"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(outside, filepath.Join(root, "Escaped")); err != nil {
		t.Fatal(err)
	}

	response := execute(context.Background(), scripts, root, map[string]struct{}{"Escaped": {}}, Request{World: "Escaped", Operation: "status"})
	if response.Status != "rejected" || response.Error != "world escapes configured root" {
		t.Fatalf("response = %#v", response)
	}
}
func TestSanitize(t *testing.T) {
	got := Sanitize("ok\nPASSWORD=secret\n/media/host/path")
	if got != "ok\n[redacted]\n[redacted]" {
		t.Fatalf("unexpected %q", got)
	}
}

func TestVerifyBindsAndValidatesModArguments(t *testing.T) {
	token := []byte("12345678901234567890123456789012")
	allowed := map[string]struct{}{"Asgard-Redesign": {}}
	request := Request{
		ID: "mod1", World: "Asgard-Redesign", Operation: "mod_add", Profile: "redesign-alpha",
		Identifier: "Azumatt-AzuCraftyBoxes", Version: "1.8.14", Scope: "shared", Timestamp: time.Now().Unix(),
	}
	request.Signature = Sign(token, request)
	if err := Verify(token, allowed, request); err != nil {
		t.Fatal(err)
	}
	request.Version = "1.8.15"
	if err := Verify(token, allowed, request); err == nil {
		t.Fatal("accepted a modified package version")
	}
	request = Request{
		ID: "custom1", World: "Asgard-Redesign", Operation: "mod_custom_add", Profile: "redesign-alpha",
		Identifier: "../unapproved.zip", Scope: "client-only", Timestamp: time.Now().Unix(),
	}
	request.Signature = Sign(token, request)
	if err := Verify(token, allowed, request); err == nil {
		t.Fatal("accepted a custom package traversal")
	}
}

func TestModInventoryReturnsStructuredDataWithFixedArguments(t *testing.T) {
	root := t.TempDir()
	scripts := filepath.Join(root, "scripts")
	if err := os.MkdirAll(filepath.Join(root, "Asgard-Redesign"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(scripts, 0o700); err != nil {
		t.Fatal(err)
	}
	script := []byte("#!/bin/sh\n[ \"$*\" = \"Asgard-Redesign redesign-alpha inventory\" ] || exit 9\nprintf '%s' '{\"world\":\"Asgard-Redesign\",\"packages\":[]}'\n")
	if err := os.WriteFile(filepath.Join(scripts, "portal_mod_admin.sh"), script, 0o700); err != nil {
		t.Fatal(err)
	}
	response := execute(context.Background(), scripts, root, map[string]struct{}{"Asgard-Redesign": {}}, Request{
		World: "Asgard-Redesign", Operation: "mod_inventory", Profile: "redesign-alpha",
	})
	if response.Status != "succeeded" || string(response.Data) != `{"world":"Asgard-Redesign","packages":[]}` || response.Output != "" {
		t.Fatalf("response = %#v", response)
	}
}

func TestModDeployUsesBackupStopDeployStartSequence(t *testing.T) {
	root := t.TempDir()
	scripts := filepath.Join(root, "scripts")
	if err := os.MkdirAll(filepath.Join(root, "Asgard-Redesign"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(scripts, 0o700); err != nil {
		t.Fatal(err)
	}
	log := filepath.Join(root, "operations.log")
	t.Setenv("LOG", log)
	script := []byte("#!/bin/sh\nprintf '%s:%s\\n' \"$(basename \"$0\")\" \"$*\" >> \"$LOG\"\n")
	for _, name := range []string{"backup_valheim_world.sh", "stop_valheim_server.sh", "portal_mod_admin.sh", "start_valheim_server.sh"} {
		if err := os.WriteFile(filepath.Join(scripts, name), script, 0o700); err != nil {
			t.Fatal(err)
		}
	}
	response := execute(context.Background(), scripts, root, map[string]struct{}{"Asgard-Redesign": {}}, Request{
		World: "Asgard-Redesign", Operation: "mod_deploy", Profile: "redesign-alpha",
	})
	if response.Status != "succeeded" {
		t.Fatalf("response = %#v", response)
	}
	got, err := os.ReadFile(log)
	if err != nil {
		t.Fatal(err)
	}
	want := "backup_valheim_world.sh:Asgard-Redesign\nstop_valheim_server.sh:Asgard-Redesign\nportal_mod_admin.sh:Asgard-Redesign redesign-alpha deploy\nstart_valheim_server.sh:Asgard-Redesign\n"
	if string(got) != want {
		t.Fatalf("operations = %q, want %q", got, want)
	}
}

func TestProvisionVerificationBindsConfigurationAndAllowsNewWorld(t *testing.T) {
	token := []byte("12345678901234567890123456789012")
	request := Request{
		ID: "provision1", World: "NewWorld", Operation: "provision", Port: 26000, Profile: "default",
		ServerName: "Neuralyze New World", Password: "SafePass-123", Public: true, Crossplay: true,
		PlayerLimit: 12, Preset: "Hard", BackupInterval: "1h", BackupAge: 7, BackupCount: 168,
		Seed: "SafeSeed123", Start: true, Timestamp: time.Now().Unix(),
	}
	request.Signature = Sign(token, request)
	if err := Verify(token, map[string]struct{}{}, request); err != nil {
		t.Fatal(err)
	}
	request.PlayerLimit = 13
	if err := Verify(token, map[string]struct{}{}, request); err == nil {
		t.Fatal("accepted a modified player limit")
	}
	request.PlayerLimit = 12
	request.Signature = Sign(token, request)
	request.Password = "bad password"
	request.Signature = Sign(token, request)
	if err := Verify(token, map[string]struct{}{}, request); err == nil {
		t.Fatal("accepted an invalid server password")
	}
}

func TestProvisionUsesFixedCreateStartHealthSequence(t *testing.T) {
	root := t.TempDir()
	scripts := filepath.Join(root, "scripts")
	if err := os.Mkdir(scripts, 0o700); err != nil {
		t.Fatal(err)
	}
	log := filepath.Join(root, "operations.log")
	t.Setenv("LOG", log)
	t.Setenv("ROOT", root)
	provision := []byte(`#!/bin/sh
[ "$#" = 14 ] || exit 8
[ "$PORTAL_SERVER_PASSWORD" = "SafePass-123" ] || exit 9
printf '%s:%s\n' "$(basename "$0")" "$*" >> "$LOG"
mkdir "$ROOT/$1"
printf 'schema=1\n' > "$ROOT/$1/.portal-managed"
`)
	if err := os.WriteFile(filepath.Join(scripts, "provision_valheim_server.sh"), provision, 0o700); err != nil {
		t.Fatal(err)
	}
	fixed := []byte("#!/bin/sh\nprintf '%s:%s\\n' \"$(basename \"$0\")\" \"$*\" >> \"$LOG\"\n")
	for _, name := range []string{"start_valheim_server.sh", "wait_valheim_server_ready.sh"} {
		if err := os.WriteFile(filepath.Join(scripts, name), fixed, 0o700); err != nil {
			t.Fatal(err)
		}
	}
	request := Request{
		World: "NewWorld", Operation: "provision", Port: 26000, Profile: "default",
		ServerName: "Neuralyze New World", Password: "SafePass-123", Public: true, Crossplay: false,
		PlayerLimit: 10, Preset: "Normal", BackupInterval: "1h", BackupAge: 7, BackupCount: 168,
		Seed: "SafeSeed123", Start: true,
	}
	response := execute(context.Background(), scripts, root, map[string]struct{}{}, request)
	if response.Status != "succeeded" || !response.Provisioned || !response.Ready {
		t.Fatalf("response = %#v", response)
	}
	got, err := os.ReadFile(log)
	if err != nil {
		t.Fatal(err)
	}
	want := "provision_valheim_server.sh:NewWorld Neuralyze New World 26000 true false 10 Normal 1h 7 168 default SafeSeed123  \n" +
		"start_valheim_server.sh:NewWorld\nwait_valheim_server_ready.sh:NewWorld\n"
	if string(got) != want {
		t.Fatalf("operations = %q, want %q", got, want)
	}
}

func TestReadOnlyCatalogOperationsReturnValidatedJSON(t *testing.T) {
	root := t.TempDir()
	scripts := filepath.Join(root, "scripts")
	if err := os.MkdirAll(filepath.Join(root, "Asgard-Redesign"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(scripts, 0o700); err != nil {
		t.Fatal(err)
	}
	for operation, name := range map[string]string{
		"profile_catalog": "portal_profile_catalog.sh",
		"world_metadata":  "portal_world_metadata.sh",
	} {
		if err := os.WriteFile(filepath.Join(scripts, name), []byte("#!/bin/sh\nprintf '%s\\n' '{\"safe\":true}'\n"), 0o700); err != nil {
			t.Fatal(err)
		}
		response := execute(context.Background(), scripts, root, map[string]struct{}{"Asgard-Redesign": {}}, Request{World: "Asgard-Redesign", Operation: operation})
		if response.Status != "succeeded" || string(response.Data) != `{"safe":true}` || response.Output != "" {
			t.Fatalf("%s response = %#v", operation, response)
		}
	}
}

func TestWorldCatalogVerificationUsesNoWorldOrMutableArguments(t *testing.T) {
	token := []byte("12345678901234567890123456789012")
	request := Request{ID: "catalog1", Operation: "world_catalog", Timestamp: time.Now().Unix()}
	request.Signature = Sign(token, request)
	if err := Verify(token, map[string]struct{}{"Midgard": {}}, request); err != nil {
		t.Fatal(err)
	}
	request.World = "Midgard"
	request.Signature = Sign(token, request)
	if err := Verify(token, map[string]struct{}{"Midgard": {}}, request); err == nil {
		t.Fatal("accepted a catalog request bound to a caller-selected world")
	}
	request.World = ""
	request.Port = 2456
	request.Signature = Sign(token, request)
	if err := Verify(token, map[string]struct{}{"Midgard": {}}, request); err == nil {
		t.Fatal("accepted an unexpected catalog argument")
	}
}

func TestWorldCatalogReturnsOnlyValidatedAllowedDirectories(t *testing.T) {
	root := t.TempDir()
	for name, port := range map[string]string{"Midgard": "2456", "Asgard": "26000"} {
		if err := os.Mkdir(filepath.Join(root, name), 0o700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(root, name, "valheim.env"), []byte("SERVER_PASS='secret'\nSERVER_PORT='"+port+"'\n"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	response := execute(context.Background(), root, root, map[string]struct{}{
		"Midgard": {}, "Asgard": {}, "Missing": {},
	}, Request{Operation: "world_catalog"})
	if response.Status != "succeeded" || response.Output != "" {
		t.Fatalf("response = %#v", response)
	}
	var catalog []worldCatalogEntry
	if err := json.Unmarshal(response.Data, &catalog); err != nil {
		t.Fatal(err)
	}
	// The catalog is name-ordered, so Asgard precedes Midgard.
	if len(catalog) != 2 || catalog[0].Name != "Asgard" || catalog[0].Port != 26000 ||
		catalog[1].Name != "Midgard" || catalog[1].Port != 2456 {
		t.Fatalf("catalog = %#v", catalog)
	}
}

func TestResolveBackupRootUsesWorldRootAndRejectsEscape(t *testing.T) {
	root := t.TempDir()
	expected := filepath.Join(root, "world_backups")
	if err := os.Mkdir(expected, 0755); err != nil {
		t.Fatal(err)
	}
	got, err := resolveBackupRoot(root)
	if err != nil {
		t.Fatal(err)
	}
	if got != expected {
		t.Fatalf("backup root = %q, want %q", got, expected)
	}

	escapedRoot := t.TempDir()
	if err := os.Symlink(t.TempDir(), filepath.Join(escapedRoot, "world_backups")); err != nil {
		t.Fatal(err)
	}
	if _, err := resolveBackupRoot(escapedRoot); err == nil {
		t.Fatal("accepted backup directory outside the configured world root")
	}
}
