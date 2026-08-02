package main

import (
	"context"
	"crypto/tls"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"testing"
)

func TestSynchronizeProfileInstallsAndCreatesShortcutBeforeOfflineLaunchRefusal(t *testing.T) {
	request := profileRequest{World: "world", Profile: "flat", ClientType: clientFlat}
	payload := testProfileArchive(t, request, nil, []zipEntry{{Name: "config/profile.cfg", Body: "configured"}}, nil)
	manifest := testRemoteManifest(request, "release-one", payload)
	server := httptest.NewTLSServer(http.HandlerFunc(func(writer http.ResponseWriter, incoming *http.Request) {
		if incoming.URL.Path == "/api/status" {
			_, _ = writer.Write([]byte(`{"worlds":[{"world":"world","profile":"flat","client_type":"flat","status":"offline"}]}`))
			return
		}
		switch incoming.URL.Path {
		case "/client/device":
			_, _ = writer.Write([]byte(`{"device_code":"0123456789abcdef","user_code":"BCDF-GHJK","authorize_url":"https://example.test/authorize","expires_in":30}`))
		case "/client/token/0123456789abcdef":
			_, _ = writer.Write([]byte(`{"token":"test-token-123456"}`))
		case "/client/manifest/world/flat/flat":
			if incoming.Header.Get("Authorization") != "Bearer test-token-123456" {
				writer.WriteHeader(http.StatusUnauthorized)
				return
			}
			_ = json.NewEncoder(writer).Encode(manifest)
		case "/client/payload/world/flat/flat":
			_, _ = writer.Write(payload)
		default:
			writer.WriteHeader(http.StatusNotFound)
		}
	}))
	defer server.Close()
	portal, err := url.Parse(server.URL)
	if err != nil {
		t.Fatal(err)
	}
	request.Portal = portal
	root := t.TempDir()
	t.Setenv("LOCALAPPDATA", filepath.Join(root, "AppData"))
	t.Setenv("HOME", root)
	if err := os.Mkdir(filepath.Join(root, "Desktop"), 0o700); err != nil {
		t.Fatal(err)
	}
	launcher := filepath.Join(root, "rundll32.exe")
	if err := os.WriteFile(launcher, []byte("#!/bin/sh\nexit 0\n"), 0o700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", root+string(os.PathListSeparator)+os.Getenv("PATH"))
	transport := http.DefaultTransport
	http.DefaultTransport = &http.Transport{TLSClientConfig: &tls.Config{InsecureSkipVerify: true}}
	t.Cleanup(func() { http.DefaultTransport = transport })
	gameDir := filepath.Join(root, "SteamValheim")
	if err := os.Mkdir(gameDir, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(gameDir, "valheim.exe"), []byte("placeholder"), 0o700); err != nil {
		t.Fatal(err)
	}
	_, err = synchronizeProfile(context.Background(), request, gameDir, true, nil)
	var unavailable *serverUnavailableError
	if !errors.As(err, &unavailable) || unavailable.Status != "offline" {
		t.Fatalf("offline launch error = %v", err)
	}
	profileRoot, err := profileRoot(filepath.Join(root, "AppData"), request)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(profileRoot, "active", "config", "profile.cfg")); err != nil {
		t.Fatalf("installed profile = %v", err)
	}
	if _, err := os.Stat(filepath.Join(root, "Desktop", "flat.url")); err != nil {
		t.Fatalf("desktop shortcut = %v", err)
	}
}

func TestSynchronizeProfilePreservesExistingShortcutOnProfileFailure(t *testing.T) {
	root := t.TempDir()
	t.Setenv("LOCALAPPDATA", filepath.Join(root, "AppData"))
	t.Setenv("HOME", root)
	desktop := filepath.Join(root, "Desktop")
	if err := os.Mkdir(desktop, 0o700); err != nil {
		t.Fatal(err)
	}
	legacyShortcut := filepath.Join(desktop, "world - flat.url")
	if err := os.WriteFile(legacyShortcut, []byte("existing shortcut"), 0o600); err != nil {
		t.Fatal(err)
	}
	server := httptest.NewTLSServer(http.HandlerFunc(func(writer http.ResponseWriter, incoming *http.Request) {
		writer.WriteHeader(http.StatusInternalServerError)
	}))
	defer server.Close()
	portal, err := url.Parse(server.URL)
	if err != nil {
		t.Fatal(err)
	}
	transport := http.DefaultTransport
	http.DefaultTransport = &http.Transport{TLSClientConfig: &tls.Config{InsecureSkipVerify: true}}
	t.Cleanup(func() { http.DefaultTransport = transport })
	request := profileRequest{World: "world", Profile: "flat", ClientType: clientFlat, Portal: portal}
	_, syncErr := synchronizeProfile(context.Background(), request, "", false, nil)
	if syncErr == nil {
		t.Fatal("profile synchronization unexpectedly succeeded")
	}
	content, err := os.ReadFile(legacyShortcut)
	if err != nil {
		t.Fatalf("legacy shortcut after profile failure = %v; synchronization error = %v", err, syncErr)
	}
	if string(content) != "existing shortcut" {
		t.Fatalf("legacy shortcut changed after profile failure: %q", content)
	}
	if _, err := os.Stat(filepath.Join(desktop, "flat.url")); !os.IsNotExist(err) {
		t.Fatalf("new shortcut exists after profile failure: %v", err)
	}
}
