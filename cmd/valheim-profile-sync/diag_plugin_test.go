package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func testDiagnosticsPlugin(t *testing.T, body string) ([]byte, string, int64) {
	t.Helper()
	archive := makeTestZip(t, []zipEntry{
		{Name: "BepInEx/plugins/ValheimDiagnostics/ValheimDiagnostics.dll", Body: body},
	})
	sum := sha256.Sum256(archive)
	return archive, hex.EncodeToString(sum[:]), int64(len(archive))
}

// TestSyncInstallsDiagnosticsPlugin installs the portal-hosted plugin end to end.
// Client-type independence is asserted at the manifest layer below, because the
// VR path additionally requires a full allowlist-conforming runtime archive.
func TestSyncInstallsDiagnosticsPlugin(t *testing.T) {
	request := profileRequest{World: "world", Profile: "alpha", ClientType: clientFlat}
	packageInfo, packageArchive := testPackage(t, "author-package.zip", "example.dll", "package")
	payload := testProfileArchive(t, request, []packageDefinition{packageInfo}, []zipEntry{{Name: "config/value.cfg", Body: "value"}}, nil)
	portal := newTestPortal(t, payload, map[string][]byte{packageInfo.Filename: packageArchive})
	request.Portal = portal.request.Portal
	defer portal.Close()
	portal.request = request
	portal.manifest = testRemoteManifest(request, "release-one", payload)
	archive, sum, size := testDiagnosticsPlugin(t, "probe")
	portal.diagPlugin = archive
	portal.manifest.DiagnosticsPluginSHA256 = sum
	portal.manifest.DiagnosticsPluginSize = size
	syncer := newProfileSyncer(portal.httpClient)
	syncer.LocalAppData = t.TempDir()
	if changed, err := syncer.syncAuthorized(context.Background(), request, "test-token-123456"); err != nil || !changed {
		t.Fatalf("sync = changed:%t err:%v", changed, err)
	}
	root, err := profileRoot(syncer.LocalAppData, request)
	if err != nil {
		t.Fatal(err)
	}
	installed := filepath.Join(root, "active", "BepInEx", "plugins", "ValheimDiagnostics", "ValheimDiagnostics.dll")
	if data, err := os.ReadFile(installed); err != nil || string(data) != "probe" {
		t.Fatalf("diagnostics plugin not installed: %q, %v", data, err)
	}
}

// TestSyncReinstallsWhenDiagnosticsPluginChanges is the load-bearing case for the
// admin toggle: enabling or disabling debug logging must not be mistaken for an
// already-current profile.
func TestSyncReinstallsWhenDiagnosticsPluginChanges(t *testing.T) {
	request := profileRequest{World: "world", Profile: "alpha", ClientType: clientFlat}
	packageInfo, packageArchive := testPackage(t, "author-package.zip", "example.dll", "package")
	payload := testProfileArchive(t, request, []packageDefinition{packageInfo}, []zipEntry{{Name: "config/value.cfg", Body: "value"}}, nil)
	portal := newTestPortal(t, payload, map[string][]byte{packageInfo.Filename: packageArchive})
	request.Portal = portal.request.Portal
	defer portal.Close()
	portal.request = request
	portal.manifest = testRemoteManifest(request, "release-one", payload)
	first, firstSum, firstSize := testDiagnosticsPlugin(t, "first")
	portal.diagPlugin, portal.manifest.DiagnosticsPluginSHA256, portal.manifest.DiagnosticsPluginSize = first, firstSum, firstSize
	syncer := newProfileSyncer(portal.httpClient)
	syncer.LocalAppData = t.TempDir()
	if changed, err := syncer.syncAuthorized(context.Background(), request, "test-token-123456"); err != nil || !changed {
		t.Fatalf("initial sync = changed:%t err:%v", changed, err)
	}
	if changed, err := syncer.syncAuthorized(context.Background(), request, "test-token-123456"); err != nil || changed {
		t.Fatalf("unchanged sync = changed:%t err:%v", changed, err)
	}
	root, err := profileRoot(syncer.LocalAppData, request)
	if err != nil {
		t.Fatal(err)
	}
	installed := filepath.Join(root, "active", "BepInEx", "plugins", "ValheimDiagnostics", "ValheimDiagnostics.dll")

	second, secondSum, secondSize := testDiagnosticsPlugin(t, "second")
	portal.diagPlugin, portal.manifest.DiagnosticsPluginSHA256, portal.manifest.DiagnosticsPluginSize = second, secondSum, secondSize
	if changed, err := syncer.syncAuthorized(context.Background(), request, "test-token-123456"); err != nil || !changed {
		t.Fatalf("plugin update sync = changed:%t err:%v", changed, err)
	}
	if data, err := os.ReadFile(installed); err != nil || string(data) != "second" {
		t.Fatalf("plugin was not updated: %q, %v", data, err)
	}

	portal.diagPlugin, portal.manifest.DiagnosticsPluginSHA256, portal.manifest.DiagnosticsPluginSize = nil, "", 0
	if changed, err := syncer.syncAuthorized(context.Background(), request, "test-token-123456"); err != nil || !changed {
		t.Fatalf("plugin removal sync = changed:%t err:%v", changed, err)
	}
	if _, err := os.Stat(installed); !os.IsNotExist(err) {
		t.Fatalf("plugin survived removal: %v", err)
	}
}

func TestRemoteManifestRejectsInconsistentDiagnosticsPlugin(t *testing.T) {
	base := func() remoteManifest {
		request := profileRequest{World: "world", Profile: "alpha", ClientType: clientFlat}
		return remoteManifest{
			ReleaseID: "r1", World: request.World, Profile: request.Profile, ClientType: request.ClientType,
			ProfileSHA256: strings.Repeat("a", 64), ProfileSize: 10,
		}
	}
	request := profileRequest{World: "world", Profile: "alpha", ClientType: clientFlat}
	if err := base().validate(request); err != nil {
		t.Fatalf("baseline manifest rejected: %v", err)
	}
	cases := map[string]func(m *remoteManifest){
		"digest without size": func(m *remoteManifest) { m.DiagnosticsPluginSHA256 = strings.Repeat("b", 64) },
		"size without digest": func(m *remoteManifest) { m.DiagnosticsPluginSize = 42 },
		"malformed digest":    func(m *remoteManifest) { m.DiagnosticsPluginSHA256, m.DiagnosticsPluginSize = "zz", 42 },
		"oversized": func(m *remoteManifest) {
			m.DiagnosticsPluginSHA256, m.DiagnosticsPluginSize = strings.Repeat("b", 64), maxProfileArchiveBytes+1
		},
	}
	for name, mutate := range cases {
		manifest := base()
		mutate(&manifest)
		if err := manifest.validate(request); err == nil {
			t.Fatalf("%s: accepted invalid diagnostics plugin manifest", name)
		}
	}
	valid := base()
	valid.DiagnosticsPluginSHA256, valid.DiagnosticsPluginSize = strings.Repeat("b", 64), 42
	if err := valid.validate(request); err != nil {
		t.Fatalf("valid diagnostics plugin manifest rejected: %v", err)
	}
}
