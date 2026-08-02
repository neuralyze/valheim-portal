package main

import (
	"context"
	"os"
	"path/filepath"
	"testing"
)

// TestSyncReinstallsRepublishedReleaseWithIdenticalArtifacts pins the failure that
// made a client installer fix silently not apply: a new release whose profile and
// runtime archives are byte-identical to the installed one must still be
// reinstalled. Staleness cannot be decided from artifact digests alone, because a
// release is republished precisely when the installer behaviour changed rather
// than the payload.
func TestSyncReinstallsRepublishedReleaseWithIdenticalArtifacts(t *testing.T) {
	request := profileRequest{World: "world", Profile: "alpha", ClientType: clientFlat}
	packageInfo, packageArchive := testPackage(t, "author-package.zip", "example.dll", "package")
	payload := testProfileArchive(t, request, []packageDefinition{packageInfo}, []zipEntry{{Name: "config/value.cfg", Body: "value"}}, nil)
	portal := newTestPortal(t, payload, map[string][]byte{packageInfo.Filename: packageArchive})
	request.Portal = portal.request.Portal
	defer portal.Close()
	portal.request = request
	portal.manifest = testRemoteManifest(request, "release-one", payload)

	syncer := newProfileSyncer(portal.httpClient)
	syncer.LocalAppData = t.TempDir()
	if changed, err := syncer.syncAuthorized(context.Background(), request, "test-token-123456"); err != nil || !changed {
		t.Fatalf("initial sync = changed:%t err:%v", changed, err)
	}
	root, err := profileRoot(syncer.LocalAppData, request)
	if err != nil {
		t.Fatal(err)
	}
	plugin := filepath.Join(root, "active", "BepInEx", "plugins", "example.dll")
	if _, err := os.Stat(plugin); err != nil {
		t.Fatalf("initial install missing plugin: %v", err)
	}

	// Simulate a corrected installer: the payload is unchanged, but the extracted
	// tree is wrong and only a genuine reinstall can repair it.
	stray := filepath.Join(root, "active", "BepInEx", "plugins", "stray-marker")
	if err := os.WriteFile(stray, []byte("stale"), 0o600); err != nil {
		t.Fatal(err)
	}

	// Same artifacts, new release identity.
	portal.manifest = testRemoteManifest(request, "release-two", payload)
	if portal.manifest.ProfileSHA256 == "" {
		t.Fatal("test manifest lost its profile digest")
	}
	changed, err := syncer.syncAuthorized(context.Background(), request, "test-token-123456")
	if err != nil {
		t.Fatalf("republished sync failed: %v", err)
	}
	if !changed {
		t.Fatal("republished release with identical artifacts was reported as already current")
	}
	if _, err := os.Stat(stray); !os.IsNotExist(err) {
		t.Fatalf("profile was not rebuilt from scratch; stale file survived: %v", err)
	}
	if _, err := os.Stat(plugin); err != nil {
		t.Fatalf("reinstall lost the plugin: %v", err)
	}
	state, present, err := loadProfileState(root)
	if err != nil || !present {
		t.Fatalf("state unreadable after reinstall: present=%t err=%v", present, err)
	}
	if state.ReleaseID != "release-two" {
		t.Fatalf("state release = %q, want release-two", state.ReleaseID)
	}
}
