package agent

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"
)

const surfaceToken = "12345678901234567890123456789012"

func signed(r Request) Request {
	r.Timestamp = time.Now().Unix()
	r.Signature = Sign([]byte(surfaceToken), r)
	return r
}

func allowOne(world string) map[string]struct{} { return map[string]struct{}{world: {}} }

// The new arguments have to be inside the signed payload. A field left out of Canonical travels
// unauthenticated, which would let anything that can reach the socket rewrite it.
func TestNewArgumentsAreCoveredByTheSignature(t *testing.T) {
	base := signed(Request{ID: "notes1", World: "Asgard", Operation: "mod_notes", Profile: "redesign", Lines: 20})
	if err := Verify([]byte(surfaceToken), allowOne("Asgard"), base); err != nil {
		t.Fatal(err)
	}
	for name, mutate := range map[string]func(Request) Request{
		"lines":             func(r Request) Request { r.Lines = 200; return r },
		"client type":       func(r Request) Request { r.ClientType = "flat"; return r },
		"published profile": func(r Request) Request { r.PublishedProfile = "asgard-vr"; return r },
		"release id":        func(r Request) Request { r.ReleaseID = "asgard-vr-9.9.9"; return r },
		"archive":           func(r Request) Request { r.Archive = "profile.zip"; return r },
		"notes":             func(r Request) Request { r.Notes = "quietly different"; return r },
	} {
		if err := Verify([]byte(surfaceToken), allowOne("Asgard"), mutate(base)); err == nil {
			t.Fatalf("a tampered %s was accepted; it is outside the signature", name)
		}
	}
}

func TestReadOnlyModOperationsRejectStrayArguments(t *testing.T) {
	for _, operation := range []string{"mod_check_updates", "mod_release_status", "mod_deploy_plan"} {
		clean := signed(Request{ID: "r", World: "Asgard", Operation: operation, Profile: "redesign"})
		if err := Verify([]byte(surfaceToken), allowOne("Asgard"), clean); err != nil {
			t.Fatalf("%s: %v", operation, err)
		}
		dirty := signed(Request{ID: "r", World: "Asgard", Operation: operation, Profile: "redesign", Identifier: "Some-Mod"})
		if err := Verify([]byte(surfaceToken), allowOne("Asgard"), dirty); err == nil {
			t.Fatalf("%s accepted an identifier it does not use", operation)
		}
	}
}

func TestNotesRequiresABoundedLineCount(t *testing.T) {
	for _, lines := range []int{0, -1, 201, 10000} {
		r := signed(Request{ID: "n", World: "Asgard", Operation: "mod_notes", Profile: "redesign", Lines: lines})
		if err := Verify([]byte(surfaceToken), allowOne("Asgard"), r); err == nil {
			t.Fatalf("accepted %d lines; the changelog of every crossed version is fetched", lines)
		}
	}
	r := signed(Request{ID: "n", World: "Asgard", Operation: "mod_notes", Profile: "redesign", Lines: 20})
	if err := Verify([]byte(surfaceToken), allowOne("Asgard"), r); err != nil {
		t.Fatal(err)
	}
	// Lines on an operation that does not read changelogs is refused rather than ignored.
	stray := signed(Request{ID: "n", World: "Asgard", Operation: "mod_inventory", Profile: "redesign", Lines: 20})
	if err := Verify([]byte(surfaceToken), allowOne("Asgard"), stray); err == nil {
		t.Fatal("accepted a line count for mod_inventory")
	}
}

func TestPublishRequiresAClientTypeAndASingleLineNote(t *testing.T) {
	good := signed(Request{ID: "p", World: "Asgard", Operation: "publish_profile", Profile: "redesign",
		ClientType: "vr", Notes: "stop the label sweep"})
	if err := Verify([]byte(surfaceToken), allowOne("Asgard"), good); err != nil {
		t.Fatal(err)
	}
	for name, r := range map[string]Request{
		"no client type":   {ID: "p", World: "Asgard", Operation: "publish_profile", Profile: "redesign", Notes: "a real note"},
		"bad client type":  {ID: "p", World: "Asgard", Operation: "publish_profile", Profile: "redesign", ClientType: "console", Notes: "a real note"},
		"note too short":   {ID: "p", World: "Asgard", Operation: "publish_profile", Profile: "redesign", ClientType: "vr", Notes: "fix"},
		"multi-line note":  {ID: "p", World: "Asgard", Operation: "publish_profile", Profile: "redesign", ClientType: "vr", Notes: "first line\nsecond line"},
		"no note at all":   {ID: "p", World: "Asgard", Operation: "publish_profile", Profile: "redesign", ClientType: "vr"},
		"note on a status": {ID: "p", World: "Asgard", Operation: "status", Notes: "unexpected here"},
	} {
		if err := Verify([]byte(surfaceToken), allowOne("Asgard"), signed(r)); err == nil {
			t.Fatalf("accepted %s", name)
		}
	}
}

func TestReleaseConfirmBindsEveryArgument(t *testing.T) {
	good := signed(Request{ID: "c", World: "Asgard", Operation: "mod_release_confirm", Profile: "redesign",
		PublishedProfile: "asgard-vr", ClientType: "vr", ReleaseID: "asgard-vr-2.5.90", Archive: "asgard-vr-profile.zip"})
	if err := Verify([]byte(surfaceToken), allowOne("Asgard"), good); err != nil {
		t.Fatal(err)
	}
	for name, mutate := range map[string]func(Request) Request{
		"traversal archive":  func(r Request) Request { r.Archive = "../../etc/passwd.zip"; return r },
		"absolute archive":   func(r Request) Request { r.Archive = "/etc/shadow.zip"; return r },
		"non-zip archive":    func(r Request) Request { r.Archive = "profile.tar"; return r },
		"missing release id": func(r Request) Request { r.ReleaseID = ""; return r },
		"bad client type":    func(r Request) Request { r.ClientType = "vr2"; return r },
	} {
		if err := Verify([]byte(surfaceToken), allowOne("Asgard"), signed(mutate(good))); err == nil {
			t.Fatalf("accepted %s", name)
		}
	}
}

// The action names the script sees are what make or break this wiring, so assert on them.
func TestNewOperationsCallTheHostScriptWithTheRightAction(t *testing.T) {
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
	script := []byte("#!/bin/sh\nprintf '%s:%s\\n' \"$(basename \"$0\")\" \"$*\" >> \"$LOG\"\n")
	for _, name := range []string{"portal_mod_admin.sh", "portal_publish_profile.sh", "backup_valheim_world.sh", "stop_valheim_server.sh", "start_valheim_server.sh"} {
		if err := os.WriteFile(filepath.Join(scripts, name), script, 0o700); err != nil {
			t.Fatal(err)
		}
	}
	cases := []struct {
		request Request
		want    string
	}{
		{Request{World: "Asgard", Operation: "mod_check_updates", Profile: "redesign"},
			"portal_mod_admin.sh:Asgard redesign check-updates\n"},
		{Request{World: "Asgard", Operation: "mod_notes", Profile: "redesign", Lines: 12},
			"portal_mod_admin.sh:Asgard redesign notes 12\n"},
		{Request{World: "Asgard", Operation: "mod_release_status", Profile: "redesign"},
			"portal_mod_admin.sh:Asgard redesign release-status\n"},
		{Request{World: "Asgard", Operation: "mod_deploy_plan", Profile: "redesign"},
			"portal_mod_admin.sh:Asgard redesign deploy-plan\n"},
		{Request{World: "Asgard", Operation: "mod_update", Profile: "redesign", Identifier: "Azumatt-AzuEPI"},
			"portal_mod_admin.sh:Asgard redesign update Azumatt-AzuEPI\n"},
		{Request{World: "Asgard", Operation: "mod_release_confirm", Profile: "redesign",
			PublishedProfile: "asgard-vr", ClientType: "vr", ReleaseID: "asgard-vr-2.5.90", Archive: "p.zip"},
			"portal_mod_admin.sh:Asgard redesign release-confirm asgard-vr vr asgard-vr-2.5.90 p.zip\n"},
		{Request{World: "Asgard", Operation: "publish_profile", Profile: "redesign", ClientType: "vr", Notes: "a real note"},
			"portal_publish_profile.sh:Asgard redesign vr a real note\n"},
	}
	for _, testCase := range cases {
		if err := os.WriteFile(log, nil, 0o600); err != nil {
			t.Fatal(err)
		}
		response := execute(context.Background(), scripts, root, allowOne("Asgard"), testCase.request)
		if response.Status != "succeeded" {
			t.Fatalf("%s: response = %#v", testCase.request.Operation, response)
		}
		got, err := os.ReadFile(log)
		if err != nil {
			t.Fatal(err)
		}
		if string(got) != testCase.want {
			t.Fatalf("%s invoked %q, want %q", testCase.request.Operation, got, testCase.want)
		}
	}
}

// Publishing must not stop a world. That was fixed once already: a client-side change took the
// server down for every player, and a sequence regression here would reintroduce it.
func TestPublishDoesNotTouchTheServer(t *testing.T) {
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
	script := []byte("#!/bin/sh\nprintf '%s\\n' \"$(basename \"$0\")\" >> \"$LOG\"\n")
	for _, name := range []string{"portal_publish_profile.sh", "backup_valheim_world.sh", "stop_valheim_server.sh", "start_valheim_server.sh"} {
		if err := os.WriteFile(filepath.Join(scripts, name), script, 0o700); err != nil {
			t.Fatal(err)
		}
	}
	execute(context.Background(), scripts, root, allowOne("Asgard"),
		Request{World: "Asgard", Operation: "publish_profile", Profile: "redesign", ClientType: "vr", Notes: "a real note"})
	got, err := os.ReadFile(log)
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != "portal_publish_profile.sh\n" {
		t.Fatalf("publishing ran %q; it must not stop or start a server", got)
	}
}
