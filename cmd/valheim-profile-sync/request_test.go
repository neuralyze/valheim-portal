package main

import (
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestParseProfileURLAcceptsOnlyContract(t *testing.T) {
	values := url.Values{
		"portal":      {"https://portal.example/client"},
		"world":       {"world-one"},
		"profile":     {"alpha"},
		"client_type": {clientFlat},
	}
	request, err := parseProfileURL(protocolScheme + "://" + protocolAction + "?" + values.Encode())
	if err != nil {
		t.Fatal(err)
	}
	if request.Portal.String() != "https://portal.example/client" || request.World != "world-one" || request.Profile != "alpha" || request.ClientType != clientFlat {
		t.Fatalf("parsed request = %#v", request)
	}
	for _, raw := range []string{
		"other://sync?" + values.Encode(),
		protocolScheme + "://other?" + values.Encode(),
		protocolScheme + "://sync/other?" + values.Encode(),
		protocolScheme + "://sync?portal=http%3A%2F%2Fportal.example&world=world-one&profile=alpha&client_type=flat",
		protocolScheme + "://sync?portal=https%3A%2F%2Fportal.example%3Ftoken%3Dsecret&world=world-one&profile=alpha&client_type=flat",
		protocolScheme + "://sync?portal=https%3A%2F%2Fportal.example&world=..&profile=alpha&client_type=flat",
		protocolScheme + "://sync?portal=https%3A%2F%2Fportal.example&world=world-one&profile=alpha&client_type=flat&token=secret",
	} {
		if _, err := parseProfileURL(raw); err == nil {
			t.Fatalf("accepted invalid URL %q", raw)
		}
	}
}

func TestProfileRootsAreIsolated(t *testing.T) {
	portal, err := url.Parse("https://portal.example")
	if err != nil {
		t.Fatal(err)
	}
	local := t.TempDir()
	flat := profileRequest{Portal: portal, World: "world", Profile: "alpha", ClientType: clientFlat}
	vr := profileRequest{Portal: portal, World: "world", Profile: "alpha", ClientType: clientVR}
	other := profileRequest{Portal: portal, World: "world", Profile: "beta", ClientType: clientFlat}
	flatRoot, err := profileRoot(local, flat)
	if err != nil {
		t.Fatal(err)
	}
	vrRoot, err := profileRoot(local, vr)
	if err != nil {
		t.Fatal(err)
	}
	otherRoot, err := profileRoot(local, other)
	if err != nil {
		t.Fatal(err)
	}
	if flatRoot == vrRoot || flatRoot == otherRoot || vrRoot == otherRoot {
		t.Fatalf("profile roots overlap: %q %q %q", flatRoot, vrRoot, otherRoot)
	}
	want := filepath.Join(local, "ValheimProfileSync", "profiles", "world--alpha--flat")
	if flatRoot != want {
		t.Fatalf("profile root = %q, want %q", flatRoot, want)
	}
}

func TestShortcutUsesOnlyProfileProtocol(t *testing.T) {
	portal, err := url.Parse("https://portal.example")
	if err != nil {
		t.Fatal(err)
	}
	request := profileRequest{Portal: portal, World: "world", Profile: "alpha", ClientType: clientFlat}
	desktop := t.TempDir()
	obsolete := [...]string{
		filepath.Join(desktop, "world - alpha.url"),
		filepath.Join(desktop, "Valheim Profile Sync - world - alpha - flat.url"),
	}
	for _, oldPath := range obsolete {
		if err := os.WriteFile(oldPath, []byte("old"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	shortcut, err := writeShortcut(desktop, request, filepath.Join(t.TempDir(), "ValheimProfileSync.exe"))
	if err != nil {
		t.Fatal(err)
	}
	if filepath.Base(shortcut) != "alpha.url" {
		t.Fatalf("shortcut name = %q", filepath.Base(shortcut))
	}
	for _, oldPath := range obsolete {
		if _, err := os.Stat(oldPath); !os.IsNotExist(err) {
			t.Fatalf("old shortcut still exists: %v", err)
		}
	}
	content, err := os.ReadFile(shortcut)
	if err != nil {
		t.Fatal(err)
	}
	text := string(content)
	if !strings.Contains(text, "URL="+protocolScheme+"://"+protocolAction+"?") || strings.Contains(strings.ToLower(text), "token") || strings.Contains(strings.ToLower(text), "payload") {
		t.Fatalf("unexpected shortcut content: %q", text)
	}
	if !strings.Contains(text, "IconFile=") || !strings.HasSuffix(shortcut, ".url") {
		t.Fatalf("invalid shortcut: %q", shortcut)
	}
}

func TestProtocolCommandQuotesTheCustomURLArgument(t *testing.T) {
	if got, want := protocolCommand(`C:\Program Files\Valheim Profile Sync\ValheimProfileSync.exe`), `"C:\Program Files\Valheim Profile Sync\ValheimProfileSync.exe" "%1"`; got != want {
		t.Fatalf("protocol command = %q, want %q", got, want)
	}
}
