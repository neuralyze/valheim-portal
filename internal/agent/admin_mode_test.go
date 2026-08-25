package agent

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// adminModeScripts stubs every host script an admin-mode window composes, each logging the
// name it was invoked as and its arguments, so a test asserts on the real ordering rather
// than on a description of it. `failing` is the one script that exits non-zero.
func adminModeScripts(t *testing.T, failing string) (scripts, worldRoot, log string) {
	t.Helper()
	root := t.TempDir()
	// execute refuses a world whose directory does not resolve under the configured root,
	// so the world has to exist for the sequence to be reachable at all.
	worldRoot = filepath.Join(root, "worlds")
	if err := os.MkdirAll(filepath.Join(worldRoot, "Hrafnheim"), 0o700); err != nil {
		t.Fatal(err)
	}
	scripts = filepath.Join(root, "scripts")
	if err := os.Mkdir(scripts, 0o700); err != nil {
		t.Fatal(err)
	}
	log = filepath.Join(root, "operations.log")
	t.Setenv("LOG", log)
	names := []string{
		"backup_valheim_world.sh", "stop_valheim_server.sh", "portal_admin_mode.sh",
		"portal_mod_admin.sh", "start_valheim_server.sh", "wait_valheim_server_ready.sh",
	}
	for _, name := range names {
		body := "#!/bin/sh\nprintf '%s:%s\\n' \"$(basename \"$0\")\" \"$*\" >> \"$LOG\"\n"
		if name == failing {
			body += "exit 3\n"
		}
		if err := os.WriteFile(filepath.Join(scripts, name), []byte(body), 0o700); err != nil {
			t.Fatal(err)
		}
	}
	return scripts, worldRoot, log
}

// The ordering is the feature. Back up before touching a world whose mod set is about to
// gain two mods that have never run on it server-side, stop before deploying because the
// deploy replaces the plugin tree, and end on wait-for-ready because "the container came
// up" is not "players can join" - which is exactly what the operator is being told.
func TestAdminModeOnBacksUpStopsArmsDeploysStartsAndWaits(t *testing.T) {
	scripts, worldRoot, log := adminModeScripts(t, "")

	response := execute(context.Background(), scripts, worldRoot, map[string]struct{}{"Hrafnheim": {}},
		Request{ID: "abc", World: "Hrafnheim", Operation: "admin_mode_on", Profile: "admin"})

	if response.Status != "succeeded" {
		t.Fatalf("response = %#v", response)
	}
	got, err := os.ReadFile(log)
	if err != nil {
		t.Fatal(err)
	}
	want := "backup_valheim_world.sh:Hrafnheim\n" +
		"stop_valheim_server.sh:Hrafnheim\n" +
		"portal_admin_mode.sh:Hrafnheim admin on\n" +
		"portal_mod_admin.sh:Hrafnheim admin deploy\n" +
		"start_valheim_server.sh:Hrafnheim\n" +
		"wait_valheim_server_ready.sh:Hrafnheim\n"
	if string(got) != want {
		t.Fatalf("ordering =\n%s\nwant\n%s", got, want)
	}
}

// Leaving a window takes no backup, and that is deliberate: this is the path that gets
// players back in, so it carries the fewest steps that can refuse it. A backup here would
// add a failure mode to the recovery itself.
func TestAdminModeOffTakesNoBackupAndStillWaitsForReady(t *testing.T) {
	scripts, worldRoot, log := adminModeScripts(t, "")

	response := execute(context.Background(), scripts, worldRoot, map[string]struct{}{"Hrafnheim": {}},
		Request{ID: "abc", World: "Hrafnheim", Operation: "admin_mode_off", Profile: "admin"})

	if response.Status != "succeeded" {
		t.Fatalf("response = %#v", response)
	}
	got, err := os.ReadFile(log)
	if err != nil {
		t.Fatal(err)
	}
	want := "stop_valheim_server.sh:Hrafnheim\n" +
		"portal_admin_mode.sh:Hrafnheim admin off\n" +
		"portal_mod_admin.sh:Hrafnheim admin deploy\n" +
		"start_valheim_server.sh:Hrafnheim\n" +
		"wait_valheim_server_ready.sh:Hrafnheim\n"
	if string(got) != want {
		t.Fatalf("ordering =\n%s\nwant\n%s", got, want)
	}
}

// A deploy that fails after the stop leaves the world down carrying a plugin set nobody
// chose. Every failure in this loop reported the bare string "operation failed" until
// 2026-08-25, which an operator cannot act on: it named neither the step, nor the fact
// that the world was still down, nor how to put it back.
func TestAFailedDeployReportsTheWorldIsStoppedAndNamesTheRecovery(t *testing.T) {
	scripts, worldRoot, _ := adminModeScripts(t, "portal_mod_admin.sh")

	response := execute(context.Background(), scripts, worldRoot, map[string]struct{}{"Hrafnheim": {}},
		Request{ID: "abc", World: "Hrafnheim", Operation: "admin_mode_on", Profile: "admin"})

	if response.Status != "failed" {
		t.Fatalf("a failed deploy must not report success: %#v", response)
	}
	for _, want := range []string{
		"mod_deploy failed",
		"Hrafnheim is STOPPED",
		"deployed server mod set is not the one this operation intended",
		"hostops/manage_mods.sh Hrafnheim deploy --apply",
		"hostops/start_valheim_server.sh Hrafnheim",
		"hostops/wait_valheim_server_ready.sh Hrafnheim",
	} {
		if !strings.Contains(response.Error, want) {
			t.Fatalf("the failure does not say %q: %q", want, response.Error)
		}
	}
}

// The control for the test above: a step that fails BEFORE the stop leaves the world
// running, so claiming it is down would send an operator to restart a healthy server.
func TestAFailureBeforeTheStopDoesNotClaimTheWorldIsDown(t *testing.T) {
	scripts, worldRoot, _ := adminModeScripts(t, "backup_valheim_world.sh")

	response := execute(context.Background(), scripts, worldRoot, map[string]struct{}{"Hrafnheim": {}},
		Request{ID: "abc", World: "Hrafnheim", Operation: "admin_mode_on", Profile: "admin"})

	if response.Status != "failed" {
		t.Fatalf("response = %#v", response)
	}
	if !strings.Contains(response.Error, "backup failed") {
		t.Fatalf("the failed step is not named: %q", response.Error)
	}
	if strings.Contains(response.Error, "STOPPED") {
		t.Fatalf("the world was never stopped, so nothing may say it is: %q", response.Error)
	}
}

// A start that fails leaves the world down with the RIGHT mod set, so the recovery is a
// start rather than another deploy. The distinction matters: a needless deploy is another
// chance for the plugin tree to end up somewhere nobody chose.
func TestAFailedStartOnASequenceWithoutADeployAsksOnlyForAStart(t *testing.T) {
	scripts, worldRoot, _ := adminModeScripts(t, "stop_valheim_server.sh")

	response := execute(context.Background(), scripts, worldRoot, map[string]struct{}{"Hrafnheim": {}},
		Request{ID: "abc", World: "Hrafnheim", Operation: "stop"})

	if response.Status != "failed" {
		t.Fatalf("response = %#v", response)
	}
	if !strings.Contains(response.Error, "stop failed") {
		t.Fatalf("the failed step is not named: %q", response.Error)
	}
	// `stop` is ["backup","stop"]: the stop script itself failed, so the world's state is
	// whatever that script left, and no deploy was ever part of this sequence.
	if strings.Contains(response.Error, "manage_mods.sh") {
		t.Fatalf("a sequence with no deploy must not recommend one: %q", response.Error)
	}
}

// A signed request for a window carries the profile the overlay is built from. Without it
// the host would have to reach Thunderstore to find the archives, which is the one thing a
// maintenance window must not depend on.
func TestAdminModeRequestsRequireTheProfileTheOverlayIsBuiltFrom(t *testing.T) {
	token := []byte("0123456789abcdef0123456789abcdef")
	allowed := map[string]struct{}{"Hrafnheim": {}}

	withProfile := Request{ID: "a", World: "Hrafnheim", Operation: "admin_mode_on", Profile: "admin", Timestamp: time.Now().Unix()}
	withProfile.Signature = Sign(token, withProfile)
	if err := Verify(token, allowed, withProfile); err != nil {
		t.Fatalf("a well-formed window request was refused: %v", err)
	}

	without := Request{ID: "a", World: "Hrafnheim", Operation: "admin_mode_on", Timestamp: time.Now().Unix()}
	without.Signature = Sign(token, without)
	if err := Verify(token, allowed, without); err == nil {
		t.Fatal("a window request with no profile was accepted")
	}
}
