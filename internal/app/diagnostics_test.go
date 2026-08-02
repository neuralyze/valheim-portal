package app

import (
	"bytes"
	"context"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestClientDiagnosticsRequiresScopedTokenAndStoresBundle(t *testing.T) {
	server := testServer(t)
	release := Release{ID: "diagnostics-release", World: "Ashlands", Profile: "raiders", ClientType: "flat", Version: "1.0.0", Notes: "diagnostics"}
	publishProfile(t, server, release)
	if err := server.store.GrantWorldAccess(context.Background(), release.World, testSteamID, "admin"); err != nil {
		t.Fatal(err)
	}
	claims := deviceTokenClaims{SteamID: testSteamID, World: release.World, Profile: release.Profile, ClientType: release.ClientType, ReleaseID: release.ID, ExpiresAt: time.Now().Add(time.Hour)}
	profileToken := server.mintDeviceToken(deviceTokenClaims{SteamID: claims.SteamID, World: claims.World, Profile: claims.Profile, ClientType: claims.ClientType, ReleaseID: claims.ReleaseID, Scope: deviceTokenScopeProfile, ExpiresAt: claims.ExpiresAt})
	diagnosticsToken := server.mintDeviceToken(deviceTokenClaims{SteamID: claims.SteamID, World: claims.World, Profile: claims.Profile, ClientType: claims.ClientType, ReleaseID: claims.ReleaseID, Scope: deviceTokenScopeDiagnostics, ExpiresAt: claims.ExpiresAt})

	body := newDiagnosticsUpload(t, []byte("zip bytes"))
	denied := httptest.NewRequest(http.MethodPost, "/client/diagnostics/Ashlands/raiders/flat", body)
	denied.Header.Set("Content-Type", "multipart/form-data; boundary="+diagnosticBoundary)
	denied.Header.Set("Authorization", "Bearer "+profileToken)
	deniedResponse := httptest.NewRecorder()
	server.Handler().ServeHTTP(deniedResponse, denied)
	if deniedResponse.Code != http.StatusUnauthorized {
		t.Fatalf("profile-scoped token upload = %d", deniedResponse.Code)
	}

	body = newDiagnosticsUpload(t, []byte("zip bytes"))
	upload := httptest.NewRequest(http.MethodPost, "/client/diagnostics/Ashlands/raiders/flat", body)
	upload.Header.Set("Content-Type", "multipart/form-data; boundary="+diagnosticBoundary)
	upload.Header.Set("Authorization", "Bearer "+diagnosticsToken)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, upload)
	if response.Code != http.StatusCreated {
		t.Fatalf("diagnostics upload = %d: %s", response.Code, response.Body.String())
	}
	bundles, err := server.store.DiagnosticBundles(context.Background(), 10)
	if err != nil || len(bundles) != 1 {
		t.Fatalf("stored bundles = %#v, %v", bundles, err)
	}
	if bundles[0].SteamID != testSteamID || bundles[0].ReleaseID != release.ID || bundles[0].Name != "valheim-diagnostics.zip" {
		t.Fatalf("stored bundle = %#v", bundles[0])
	}
}

const diagnosticBoundary = "diagnostics-boundary"

func newDiagnosticsUpload(t *testing.T, content []byte) *bytes.Buffer {
	t.Helper()
	var body bytes.Buffer
	writer := multipart.NewWriter(&body)
	if err := writer.SetBoundary(diagnosticBoundary); err != nil {
		t.Fatal(err)
	}
	part, err := writer.CreateFormFile("bundle", "valheim-diagnostics.zip")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := part.Write(content); err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	return &body
}
