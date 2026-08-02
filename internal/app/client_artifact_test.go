package app

import (
	"encoding/binary"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// writePE builds the smallest PE image the inspector needs: a DOS stub whose
// 0x3c pointer reaches an NT signature, a COFF header, and an optional header
// carrying the subsystem.
func writePE(t *testing.T, subsystem uint16) string {
	t.Helper()
	const ntOffset = 0x80
	image := make([]byte, ntOffset+4+20+96)
	copy(image, "MZ")
	binary.LittleEndian.PutUint32(image[0x3c:], ntOffset)
	copy(image[ntOffset:], "PE\x00\x00")
	optional := ntOffset + 4 + 20
	binary.LittleEndian.PutUint16(image[optional:], 0x20b) // PE32+
	binary.LittleEndian.PutUint16(image[optional+68:], subsystem)
	path := filepath.Join(t.TempDir(), "ValheimProfileSync.exe")
	if err := os.WriteFile(path, image, 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestClientArtifactRecognisesTheWindowsSubsystem(t *testing.T) {
	gui, err := inspectClientExecutable(writePE(t, peSubsystemGUI))
	if err != nil || !gui.GUI() || gui.Console() {
		t.Fatalf("gui artifact = %#v, %v", gui, err)
	}
	if problem := clientArtifactProblem(gui, nil); problem != "" {
		t.Fatalf("a GUI build was rejected: %s", problem)
	}
	console, err := inspectClientExecutable(writePE(t, peSubsystemConsole))
	if err != nil || console.GUI() || !console.Console() {
		t.Fatalf("console artifact = %#v, %v", console, err)
	}
	problem := clientArtifactProblem(console, nil)
	if !strings.Contains(problem, "console subsystem") || !strings.Contains(problem, "build-windows-client.sh") {
		t.Fatalf("console problem does not name the fix: %q", problem)
	}
}

func TestClientArtifactRejectsNonPEAndMissingFiles(t *testing.T) {
	dir := t.TempDir()
	garbage := filepath.Join(dir, "garbage.exe")
	if err := os.WriteFile(garbage, []byte("this is not an executable"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := inspectClientExecutable(garbage); err == nil {
		t.Fatal("a non-PE file was accepted as a client")
	}
	artifact, err := inspectClientExecutable(filepath.Join(dir, "absent.exe"))
	if problem := clientArtifactProblem(artifact, err); !strings.Contains(problem, "no Windows client is published") {
		t.Fatalf("missing client problem = %q", problem)
	}
	// A directory is not a client, and neither is a truncated PE.
	if _, err := inspectClientExecutable(dir); err == nil {
		t.Fatal("a directory was accepted as a client")
	}
	stub := filepath.Join(dir, "stub.exe")
	if err := os.WriteFile(stub, []byte("MZ"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := inspectClientExecutable(stub); err == nil {
		t.Fatal("a truncated PE was accepted as a client")
	}
}

// The download route is the last place a mis-built client can be stopped before
// a player runs it, so it must refuse rather than serve. What the refusal says
// is asserted in TestClientDownloadRefusalNeverShowsAPlayerAFilesystemPath: the
// operator detail goes to the log, never to the player.
func TestClientInstallerRefusesAConsoleBuildAndServesAGUIBuild(t *testing.T) {
	server := testServer(t)

	server.cfg.ClientExecutable = writePE(t, peSubsystemConsole)
	refused := httptest.NewRecorder()
	server.Handler().ServeHTTP(refused, httptest.NewRequest(http.MethodGet, "/client/ValheimProfileSync.exe", nil))
	if refused.Code != http.StatusServiceUnavailable {
		t.Fatalf("console build served with %d", refused.Code)
	}
	if problem := server.clientDownloadProblem(); !strings.Contains(problem, "build-windows-client.sh") {
		t.Fatalf("the operator-facing problem does not name the fix: %q", problem)
	}

	server.cfg.ClientExecutable = writePE(t, peSubsystemGUI)
	served := httptest.NewRecorder()
	server.Handler().ServeHTTP(served, httptest.NewRequest(http.MethodGet, "/client/ValheimProfileSync.exe", nil))
	if served.Code != http.StatusOK {
		t.Fatalf("gui build = %d: %s", served.Code, served.Body.String())
	}
	if got := served.Header().Get("Content-Disposition"); !strings.Contains(got, "ValheimProfileSync.exe") {
		t.Fatalf("content disposition = %q", got)
	}
}

func TestAdminPageWarnsWhenThePublishedClientIsUnusable(t *testing.T) {
	response := httptest.NewRecorder()
	render(response, adminTemplate, map[string]any{
		"Worlds":        []adminWorld{{PublicWorld: PublicWorld{Name: "Midgard"}}},
		"Identities":    []SteamIdentity(nil),
		"CSRF":          "test-csrf",
		"IdentityCount": 0,
		"ClientProblem": "the published Windows client was built for the console subsystem",
	})
	if response.Code != http.StatusOK {
		t.Fatalf("render = %d: %s", response.Code, response.Body.String())
	}
	if !strings.Contains(response.Body.String(), "Windows client not published") {
		t.Fatal("admin page hides a broken Windows client")
	}
}
