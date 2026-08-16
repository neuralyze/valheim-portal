package app

import (
	"bytes"
	"context"
	"encoding/json"
	"image/png"
	"io"

	"html"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"github.com/neuralyze/valheim-portal/internal/worldintel"
)

const testSteamID = "76561198000000000"

// testAdminToken is the value every test server's PORTAL_ADMIN_TOKEN_FILE
// holds. A real deployment's reverse proxy injects it as adminTokenHeader on
// the routes it authenticates; tests set the header themselves.
const testAdminToken = "0123456789abcdef0123456789abcdef"

func testServer(t *testing.T) *Server {
	t.Helper()
	dir := t.TempDir()
	secret := filepath.Join(dir, "csrf")
	token := filepath.Join(dir, "token")
	if err := os.WriteFile(secret, []byte("12345678901234567890123456789012"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(token, []byte("12345678901234567890123456789012"), 0o600); err != nil {
		t.Fatal(err)
	}
	adminToken := filepath.Join(dir, "admin-token")
	if err := os.WriteFile(adminToken, []byte(testAdminToken), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PORTAL_ADMIN_TOKEN_FILE", adminToken)
	store, err := OpenStore(filepath.Join(dir, "db.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { store.Close() })
	agent, err := NewAgentClient(filepath.Join(dir, "agent.sock"), token)
	if err != nil {
		t.Fatal(err)
	}
	server, err := NewServer(Config{
		DatabasePath: filepath.Join(dir, "db.sqlite"), ArtifactRoot: dir, MapRoot: filepath.Join(dir, "maps"), CSRFSecretFile: secret,
		AgentTokenFile: token, AgentSocket: filepath.Join(dir, "agent.sock"), AuthHeader: "X-Forwarded-User",
		CookieSecure: false, TrustedProxyCIDR: "192.0.2.0/24", PublicBaseURL: "https://portal.example.test",
		Provisioning: ProvisioningDefaults{JoinHost: "valheim.example.test", GamePort: 2456, PlayerLimit: 10, BackupInterval: "1h", BackupAge: 7, BackupCount: 168},
	}, store, agent)
	if err != nil {
		t.Fatal(err)
	}
	return server
}
func TestFaviconServesEmbeddedNeuralyzeIcon(t *testing.T) {
	server := testServer(t)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/favicon.ico", nil))
	if response.Code != http.StatusOK {
		t.Fatalf("favicon status = %d", response.Code)
	}
	if got := response.Header().Get("Content-Type"); got != "image/x-icon" {
		t.Fatalf("favicon content type = %q", got)
	}
	if !bytes.Equal(response.Body.Bytes(), neuralyzeIcon) {
		t.Fatal("favicon response does not match embedded Neuralyze icon")
	}
}
func TestWebManifestServesHighResolutionAndroidIcons(t *testing.T) {
	server := testServer(t)
	manifest := httptest.NewRecorder()
	server.Handler().ServeHTTP(manifest, httptest.NewRequest(http.MethodGet, "/site.webmanifest", nil))
	if manifest.Code != http.StatusOK || manifest.Header().Get("Content-Type") != "application/manifest+json" {
		t.Fatalf("manifest response = %d %q", manifest.Code, manifest.Header().Get("Content-Type"))
	}
	if !bytes.Contains(manifest.Body.Bytes(), []byte(`"purpose": "maskable"`)) {
		t.Fatal("manifest omits a maskable Android launcher icon")
	}
	for _, icon := range []struct {
		path string
		size int
	}{
		{"/icons/neuralyze-192.png", 192},
		{"/icons/neuralyze-512.png", 512},
		{"/icons/neuralyze-512-maskable.png", 512},
	} {
		if !bytes.Contains(manifest.Body.Bytes(), []byte(icon.path)) {
			t.Fatalf("manifest omits %s", icon.path)
		}
		response := httptest.NewRecorder()
		server.Handler().ServeHTTP(response, httptest.NewRequest(http.MethodGet, icon.path, nil))
		if response.Code != http.StatusOK || response.Header().Get("Content-Type") != "image/png" || response.Body.Len() == 0 {
			t.Fatalf("icon %s response = %d %q bytes=%d", icon.path, response.Code, response.Header().Get("Content-Type"), response.Body.Len())
		}
		image, err := png.Decode(bytes.NewReader(response.Body.Bytes()))
		if err != nil {
			t.Fatalf("decode %s: %v", icon.path, err)
		}
		if image.Bounds().Dx() != icon.size || image.Bounds().Dy() != icon.size {
			t.Fatalf("icon %s dimensions = %v", icon.path, image.Bounds())
		}
		_, _, _, alpha := image.At(0, 0).RGBA()
		if alpha != 0xffff {
			t.Fatalf("icon %s has a transparent corner", icon.path)
		}
	}
}
func TestSharedStylesProvideMobilePortalLayout(t *testing.T) {
	for _, expected := range []string{
		"@media (max-width: 640px)",
		".portal-account-actions",
		".worlds, .profile-list, .profiles",
		".sync, .install .button",
	} {
		if !bytes.Contains(siteCSS, []byte(expected)) {
			t.Fatalf("mobile stylesheet omits %q", expected)
		}
	}
}

func TestSharedStylesProvideMobileAdminLayout(t *testing.T) {
	for _, expected := range []string{
		"@media (max-width: 520px)",
		"body:has(.admin-nav) > a[href=\"/\"]",
		".admin-nav { grid-template-columns: minmax(0, 1fr)",
		".admin-widget > summary",
	} {
		if !bytes.Contains(siteCSS, []byte(expected)) {
			t.Fatalf("mobile admin stylesheet omits %q", expected)
		}
	}
}

func steamCookie(t *testing.T, server *Server, steamID string) *http.Cookie {
	t.Helper()
	response := httptest.NewRecorder()
	server.setSteamSession(response, steamID)
	cookies := response.Result().Cookies()
	if len(cookies) != 1 {
		t.Fatalf("session cookies = %#v", cookies)
	}
	return cookies[0]
}

func publishProfile(t *testing.T, server *Server, release Release) {
	t.Helper()
	if err := server.store.CreateRelease(context.Background(), release, "admin"); err != nil {
		t.Fatal(err)
	}
	addProfileArtifact(t, server.store, server.cfg.ArtifactRoot, release)
	if release.ClientType == "vr" {
		addVRRuntimeArtifact(t, server.store, server.cfg.ArtifactRoot, release)
	}
	if err := server.store.Publish(context.Background(), release.ID, "admin"); err != nil {
		t.Fatal(err)
	}
}

func TestAdminBatchPublishesFlatDraftsAndCanResume(t *testing.T) {
	server := testServer(t)
	ctx := context.Background()
	for _, release := range []Release{
		{ID: "midgard-flat-2.1.3", World: "Midgard", Profile: "midgard-flat", ClientType: "flat", Version: "2.1.3", Notes: "test"},
		{ID: "asgard-flat-2.1.3", World: "Asgard", Profile: "asgard-flat", ClientType: "flat", Version: "2.1.3", Notes: "test"},
	} {
		if err := server.store.CreateRelease(ctx, release, "admin"); err != nil {
			t.Fatal(err)
		}
		addProfileArtifact(t, server.store, server.cfg.ArtifactRoot, release)
	}
	form := url.Values{"release_id": {"midgard-flat-2.1.3", "asgard-flat-2.1.3"}}
	adminPost(t, server, "/admin/releases/batch-publish", form, http.StatusSeeOther)
	adminPost(t, server, "/admin/releases/batch-publish", form, http.StatusSeeOther)
	for _, id := range form["release_id"] {
		release, err := server.store.Release(ctx, id)
		if err != nil || release.Status != Published {
			t.Fatalf("%s status = %#v, %v", id, release, err)
		}
	}
}

func TestAdminBatchPublishRequiresTrustedAuthenticatedRequest(t *testing.T) {
	server := testServer(t)
	request := httptest.NewRequest(http.MethodPost, "/admin/releases/batch-publish", strings.NewReader("release_id=midgard-flat-2.1.3"))
	request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusUnauthorized {
		t.Fatalf("untrusted batch publish = %d", response.Code)
	}
}

func TestAdminDiscardDraftRejectsPublishedRelease(t *testing.T) {
	server := testServer(t)
	ctx := context.Background()
	draft := Release{ID: "discard-draft", World: "Midgard", Profile: "midgard-flat", ClientType: "flat", Version: "2.1.3", Notes: "test"}
	if err := server.store.CreateRelease(ctx, draft, "admin"); err != nil {
		t.Fatal(err)
	}
	adminPost(t, server, "/admin/releases/"+draft.ID+"/discard", url.Values{}, http.StatusSeeOther)
	archived, err := server.store.Release(ctx, draft.ID)
	if err != nil || archived.Status != Archived {
		t.Fatalf("discarded draft = %#v, %v", archived, err)
	}
}

func adminPost(t *testing.T, server *Server, target string, form url.Values, want int) {
	t.Helper()
	nonce := strings.Repeat("a", 64)
	form.Set("csrf", server.csrfToken(nonce))
	request := httptest.NewRequest(http.MethodPost, target, strings.NewReader(form.Encode()))
	request.RemoteAddr = "192.0.2.10:1234"
	request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	request.Header.Set(server.cfg.AuthHeader, "admin")
	request.Header.Set(adminTokenHeader, testAdminToken)
	request.AddCookie(&http.Cookie{Name: "portal_csrf", Value: nonce, Path: "/admin"})
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != want {
		t.Fatalf("POST %s = %d: %s", target, response.Code, response.Body.String())
	}
}

// adminPage renders the live admin page the way an operator sees it, so tests can
// assert on what the template actually emits rather than on its data.
func adminPage(t *testing.T, server *Server) string {
	t.Helper()
	request := adminTestRequest(http.MethodGet, "/admin", nil)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("GET /admin = %d: %s", response.Code, response.Body.String())
	}
	return response.Body.String()
}

func startDevice(t *testing.T, server *Server, world, profile, clientType string) deviceStartResponse {
	t.Helper()
	body, err := json.Marshal(deviceStartRequest{World: world, Profile: profile, ClientType: clientType})
	if err != nil {
		t.Fatal(err)
	}
	request := httptest.NewRequest(http.MethodPost, "/client/device", bytes.NewReader(body))
	request.Header.Set("Content-Type", "application/json")
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("device start = %d: %s", response.Code, response.Body.String())
	}
	var result deviceStartResponse
	if err := json.NewDecoder(response.Body).Decode(&result); err != nil {
		t.Fatal(err)
	}
	if !validID(result.DeviceCode) || result.AuthorizeURL == "" || result.ExpiresIn <= 0 {
		t.Fatalf("invalid device response: %#v", result)
	}
	return result
}

type deviceStartResponse struct {
	DeviceCode   string `json:"device_code"`
	UserCode     string `json:"user_code"`
	AuthorizeURL string `json:"authorize_url"`
	ExpiresIn    int    `json:"expires_in"`
}

type deviceTokenResponse struct {
	Token string `json:"token"`
}

func authorizeDevice(t *testing.T, server *Server, device deviceStartResponse) string {
	t.Helper()
	confirmDevice(t, server, device, device.UserCode, http.StatusOK)
	code := device.DeviceCode
	request := httptest.NewRequest(http.MethodPost, "/client/token/"+code, nil)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("device token = %d: %s", response.Code, response.Body.String())
	}
	var result deviceTokenResponse
	if err := json.NewDecoder(response.Body).Decode(&result); err != nil {
		t.Fatal(err)
	}
	if result.Token == "" {
		t.Fatal("empty device token")
	}
	return result.Token
}

func TestProfileScopedDeviceFlowDeliversOnlyCurrentProfile(t *testing.T) {
	server := testServer(t)
	release := Release{ID: "flat-release", World: "Ashlands", Profile: "raiders", ClientType: "flat", Version: "1.0.0", Notes: "client only"}
	publishProfile(t, server, release)
	if err := server.store.GrantWorldAccess(context.Background(), release.World, testSteamID, "admin"); err != nil {
		t.Fatal(err)
	}
	device := startDevice(t, server, release.World, release.Profile, release.ClientType)
	token := authorizeDevice(t, server, device)

	manifestRequest := httptest.NewRequest(http.MethodGet, "/client/manifest/Ashlands/raiders/flat", nil)
	manifestRequest.Header.Set("Authorization", "Bearer "+token)
	manifestResponse := httptest.NewRecorder()
	server.Handler().ServeHTTP(manifestResponse, manifestRequest)
	if manifestResponse.Code != http.StatusOK || strings.Contains(manifestResponse.Body.String(), "/tmp/") {
		t.Fatalf("manifest = %d: %s", manifestResponse.Code, manifestResponse.Body.String())
	}
	for _, want := range []string{`"release_id":"flat-release"`, `"world":"Ashlands"`, `"profile":"raiders"`, `"client_type":"flat"`, `"profile_sha256"`} {
		if !strings.Contains(manifestResponse.Body.String(), want) {
			t.Fatalf("manifest missing %q: %s", want, manifestResponse.Body.String())
		}
	}
	wrongScope := httptest.NewRequest(http.MethodGet, "/client/manifest/Ashlands/other/flat", nil)
	wrongScope.Header.Set("Authorization", "Bearer "+token)
	wrongScopeResponse := httptest.NewRecorder()
	server.Handler().ServeHTTP(wrongScopeResponse, wrongScope)
	if wrongScopeResponse.Code != http.StatusUnauthorized {
		t.Fatalf("wrong profile scope = %d", wrongScopeResponse.Code)
	}
	payloadRequest := httptest.NewRequest(http.MethodGet, "/client/payload/Ashlands/raiders/flat", nil)
	payloadRequest.Header.Set("Authorization", "Bearer "+token)
	payloadResponse := httptest.NewRecorder()
	server.Handler().ServeHTTP(payloadResponse, payloadRequest)
	if payloadResponse.Code != http.StatusOK || payloadResponse.Body.Len() == 0 {
		t.Fatalf("profile payload = %d", payloadResponse.Code)
	}
	again := httptest.NewRequest(http.MethodPost, "/client/token/"+device.DeviceCode, nil)
	againResponse := httptest.NewRecorder()
	server.Handler().ServeHTTP(againResponse, again)
	if againResponse.Code != http.StatusUnauthorized {
		t.Fatalf("used device code = %d", againResponse.Code)
	}
}

func TestDeviceFlowRechecksMembershipAtExchangeAndUse(t *testing.T) {
	server := testServer(t)
	release := Release{ID: "profile-release", World: "Ashlands", Profile: "builders", ClientType: "vr", Version: "1.0.0", Notes: "client only"}
	publishProfile(t, server, release)
	if err := server.store.GrantWorldAccess(context.Background(), release.World, testSteamID, "admin"); err != nil {
		t.Fatal(err)
	}
	pending := startDevice(t, server, release.World, release.Profile, release.ClientType)
	confirmDevice(t, server, pending, pending.UserCode, http.StatusOK)
	if err := server.store.RevokeWorldAccess(context.Background(), release.World, testSteamID, "admin"); err != nil {
		t.Fatal(err)
	}
	exchange := httptest.NewRequest(http.MethodPost, "/client/token/"+pending.DeviceCode, nil)
	exchangeResponse := httptest.NewRecorder()
	server.Handler().ServeHTTP(exchangeResponse, exchange)
	if exchangeResponse.Code != http.StatusUnauthorized {
		t.Fatalf("revoked exchange = %d", exchangeResponse.Code)
	}

	if err := server.store.GrantWorldAccess(context.Background(), release.World, testSteamID, "admin"); err != nil {
		t.Fatal(err)
	}
	current := startDevice(t, server, release.World, release.Profile, release.ClientType)
	token := authorizeDevice(t, server, current)
	if err := server.store.RevokeWorldAccess(context.Background(), release.World, testSteamID, "admin"); err != nil {
		t.Fatal(err)
	}
	request := httptest.NewRequest(http.MethodGet, "/client/manifest/Ashlands/builders/vr", nil)
	request.Header.Set("Authorization", "Bearer "+token)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusUnauthorized {
		t.Fatalf("revoked token use = %d", response.Code)
	}
}

func TestDeviceAuthorizationResumesAfterSteamLoginWithoutCallbackInput(t *testing.T) {
	server := testServer(t)
	release := Release{ID: "resume-release", World: "Ashlands", Profile: "raiders", ClientType: "flat", Version: "1.0.0", Notes: "test"}
	publishProfile(t, server, release)
	device := startDevice(t, server, release.World, release.Profile, release.ClientType)
	request := httptest.NewRequest(http.MethodGet, "/client/authorize/"+device.DeviceCode, nil)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusFound || !strings.HasPrefix(response.Header().Get("Location"), "https://steamcommunity.com/openid/login?") {
		t.Fatalf("Steam handoff = %d %q", response.Code, response.Header().Get("Location"))
	}
	server.authMu.Lock()
	deferredCode := ""
	for _, pending := range server.steamStates {
		deferredCode = pending.DeviceCode
	}
	server.authMu.Unlock()
	if deferredCode != device.DeviceCode {
		t.Fatalf("Steam state did not retain device handoff: %q", deferredCode)
	}
	bad := httptest.NewRequest(http.MethodPost, "/client/device", strings.NewReader(`{"world":"Ashlands","profile":"raiders","client_type":"flat","callback_url":"https://attacker.example"}`))
	bad.Header.Set("Content-Type", "application/json")
	badResponse := httptest.NewRecorder()
	server.Handler().ServeHTTP(badResponse, bad)
	if badResponse.Code != http.StatusBadRequest {
		t.Fatalf("callback URL input = %d", badResponse.Code)
	}
}

func TestProfileSyncLinkUsesOnlyNeutralScopeFields(t *testing.T) {
	server := testServer(t)
	release := Release{ID: "private-release", World: "Ashlands", Profile: "raiders", ClientType: "flat", Version: "1.0.0", Notes: "test"}
	publishProfile(t, server, release)
	if err := server.store.GrantWorldAccess(context.Background(), release.World, testSteamID, "admin"); err != nil {
		t.Fatal(err)
	}
	request := httptest.NewRequest(http.MethodGet, "/worlds/Ashlands", nil)
	request.AddCookie(steamCookie(t, server, testSteamID))
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("world page = %d: %s", response.Code, response.Body.String())
	}
	if body := response.Body.String(); !strings.Contains(body, "The app makes a shortcut on your Desktop") {
		t.Fatalf("world profile process documentation missing: %s", body)
	}
	body := response.Body.String()
	if strings.Contains(body, "Profile type") {
		t.Fatalf("world profile repeats the client type: %s", body)
	}
	if !strings.Contains(body, `.profile-card .notes{color:#a6a6a6;font-style:italic}`) {
		t.Fatalf("world profile change notes are not styled as muted italics: %s", body)
	}
	if strings.Contains(body, "Valheim Profile Sync verifies and synchronizes the selected profile") || strings.Contains(body, "profile-guidance") {
		t.Fatalf("world page retains redundant profile guidance: %s", body)
	}
	if !strings.Contains(body, `<link rel="icon" href="/favicon.ico" sizes="any">`) {
		t.Fatalf("world page is missing the Neuralyze favicon: %s", body)
	}
	if !strings.Contains(body, "Install, update, and play") {
		t.Fatalf("world page is missing the installation section: %s", body)
	}
	homeRequest := httptest.NewRequest(http.MethodGet, "/", nil)
	homeRequest.AddCookie(steamCookie(t, server, testSteamID))
	homeResponse := httptest.NewRecorder()
	server.Handler().ServeHTTP(homeResponse, homeRequest)
	if homeResponse.Code != http.StatusOK {
		t.Fatalf("worlds page = %d: %s", homeResponse.Code, homeResponse.Body.String())
	}
	if body := homeResponse.Body.String(); !strings.Contains(body, "A shortcut is made on your Desktop") || !strings.Contains(body, "Use that shortcut whenever you play") {
		t.Fatalf("worlds/profile process documentation missing: %s", body)
	}
	if body := homeResponse.Body.String(); !strings.Contains(body, `.section-head{margin-inline:clamp(.75rem,3vw,2.5rem)}`) {
		t.Fatalf("world and profile section headings are missing side margins: %s", body)
	}
	match := regexp.MustCompile(`href="(valheim-profile-sync:[^"]+)"`).FindStringSubmatch(html.UnescapeString(response.Body.String()))
	if len(match) != 2 {
		t.Fatalf("profile sync link missing: %s", response.Body.String())
	}
	link, err := url.Parse(match[1])
	if err != nil {
		t.Fatal(err)
	}
	if link.Scheme != "valheim-profile-sync" || link.Host != "sync" || strings.Contains(match[1], release.ID) || strings.Contains(match[1], "token") || strings.Contains(match[1], "artifact") {
		t.Fatalf("unsafe custom link: %q", match[1])
	}
	query := link.Query()
	if len(query) != 4 || query.Get("portal") != "https://portal.example.test" || query.Get("world") != release.World || query.Get("profile") != release.Profile || query.Get("client_type") != release.ClientType {
		t.Fatalf("custom link scope = %#v", query)
	}
}

func TestSteamCallbackRejectsMissingState(t *testing.T) {
	server := testServer(t)
	request := httptest.NewRequest(http.MethodGet, "/auth/steam/callback?openid.mode=id_res", nil)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusForbidden {
		t.Fatalf("callback response = %d", response.Code)
	}
}

func TestVRRuntimeDeliveryRequiresMatchingDeviceScope(t *testing.T) {
	server := testServer(t)
	release := Release{ID: "vr-release", World: "Ashlands", Profile: "builders", ClientType: "vr", Version: "1.0.0", Notes: "VR"}
	publishProfile(t, server, release)
	if err := server.store.GrantWorldAccess(context.Background(), release.World, testSteamID, "admin"); err != nil {
		t.Fatal(err)
	}
	device := startDevice(t, server, release.World, release.Profile, release.ClientType)
	token := authorizeDevice(t, server, device)
	manifest := httptest.NewRequest(http.MethodGet, "/client/manifest/Ashlands/builders/vr", nil)
	manifest.Header.Set("Authorization", "Bearer "+token)
	manifestResponse := httptest.NewRecorder()
	server.Handler().ServeHTTP(manifestResponse, manifest)
	if manifestResponse.Code != http.StatusOK || !strings.Contains(manifestResponse.Body.String(), `"runtime_sha256"`) {
		t.Fatalf("VR manifest = %d: %s", manifestResponse.Code, manifestResponse.Body.String())
	}
	runtime := httptest.NewRequest(http.MethodGet, "/client/runtime/Ashlands/builders/vr", nil)
	runtime.Header.Set("Authorization", "Bearer "+token)
	runtimeResponse := httptest.NewRecorder()
	server.Handler().ServeHTTP(runtimeResponse, runtime)
	if runtimeResponse.Code != http.StatusOK || runtimeResponse.Body.Len() == 0 {
		t.Fatalf("VR runtime = %d", runtimeResponse.Code)
	}
	wrongScope := httptest.NewRequest(http.MethodGet, "/client/runtime/Ashlands/builders/flat", nil)
	wrongScope.Header.Set("Authorization", "Bearer "+token)
	wrongScopeResponse := httptest.NewRecorder()
	server.Handler().ServeHTTP(wrongScopeResponse, wrongScope)
	if wrongScopeResponse.Code != http.StatusUnauthorized {
		t.Fatalf("wrong runtime scope = %d", wrongScopeResponse.Code)
	}
}

func TestAdminMembershipAcceptsArbitrarySteamIDWithoutKnownIdentities(t *testing.T) {
	response := httptest.NewRecorder()
	render(response, adminTemplate, map[string]any{
		"Worlds":        []adminWorld{{PublicWorld: PublicWorld{Name: "Midgard"}}},
		"Identities":    []SteamIdentity(nil),
		"CSRF":          "test-csrf",
		"IdentityCount": 0,
	})
	if response.Code != http.StatusOK {
		t.Fatalf("render = %d: %s", response.Code, response.Body.String())
	}
	page := response.Body.String()
	if !strings.Contains(page, `name="steam_id" list="known-steam-ids"`) {
		t.Fatal("admin page does not offer arbitrary Steam ID entry")
	}
	if strings.Contains(page, `<select name="steam_id"`) {
		t.Fatal("admin page still restricts membership to known Steam IDs")
	}
	if !strings.Contains(page, `pattern="7[0-9]{16}"`) {
		t.Fatal("admin page does not constrain Steam ID format")
	}
}

func TestWorldAnalysisCoordinatesRequireAdminProxy(t *testing.T) {
	server := testServer(t)
	snapshot := worldintel.Snapshot{
		Schema: worldintel.SchemaVersion,
		World:  "Midgard",
		Source: worldintel.Source{
			Backup: "world-Midgard-test.tgz",
			SHA256: strings.Repeat("a", 64),
		},
		Summary: worldintel.Summary{Categories: map[string]int{}},
		Objects: []worldintel.Object{{
			ID:       1,
			Category: "portal",
			Position: worldintel.Vec3{X: 123, Z: 456},
		}},
	}
	if err := server.store.SaveWorldAnalysis(t.Context(), snapshot, "test"); err != nil {
		t.Fatal(err)
	}

	request := httptest.NewRequest(http.MethodGet, "/admin/worlds/Midgard/analysis.json", nil)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusUnauthorized {
		t.Fatalf("untrusted response = %d", response.Code)
	}

	request = httptest.NewRequest(http.MethodGet, "/admin/worlds/Midgard/analysis.json", nil)
	request.RemoteAddr = "192.0.2.10:12345"
	request.Header.Set("X-Forwarded-User", "admin@example.test")
	request.Header.Set(adminTokenHeader, testAdminToken)
	response = httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusOK || !strings.Contains(response.Body.String(), `"x":123`) {
		t.Fatalf("admin response = %d: %s", response.Code, response.Body.String())
	}
}

func TestSaveWorldAnalysisRefreshesBackupForIdenticalSource(t *testing.T) {
	server := testServer(t)
	snapshot := worldintel.Snapshot{
		Schema: worldintel.SchemaVersion,
		World:  "Midgard",
		Seed:   "first",
		Source: worldintel.Source{
			Backup: "world-Midgard-first.tgz",
			SHA256: strings.Repeat("a", 64),
		},
		Summary: worldintel.Summary{Categories: map[string]int{}},
	}
	if err := server.store.SaveWorldAnalysis(t.Context(), snapshot, "test"); err != nil {
		t.Fatal(err)
	}
	snapshot.Seed = "second"
	snapshot.Source.Backup = "world-Midgard-second.tgz"
	if err := server.store.SaveWorldAnalysis(t.Context(), snapshot, "test"); err != nil {
		t.Fatal(err)
	}
	snapshots, err := server.store.LatestWorldAnalyses(t.Context(), "Midgard", 20)
	if err != nil {
		t.Fatal(err)
	}
	if len(snapshots) != 1 || snapshots[0].Source.Backup != snapshot.Source.Backup || snapshots[0].Seed != snapshot.Seed {
		t.Fatalf("snapshots = %#v", snapshots)
	}
}

func TestWorldAnalysisMapExposesDetailedLayerAndZoomControls(t *testing.T) {
	response := httptest.NewRecorder()
	render(response, worldAnalysisTemplate, worldAnalysisPage{
		World:        PublicWorld{Name: "Midgard"},
		HaveAnalysis: true,
		Backup:       "world-Midgard-test.tgz",
		AnalyzedAt:   "2026-07-27 12:00 UTC",
		CSRF:         "test-csrf",
	})
	if response.Code != http.StatusOK {
		t.Fatalf("render = %d: %s", response.Code, response.Body.String())
	}
	page := response.Body.String()
	for _, expected := range []string{
		`href="/assets/site.css"`,
		`data-layer="terrain"`,
		`data-layer="zones"`,
		`data-layer="locations"`,
		`data-layer="clusters"`,
		`data-layer="portal"`,
		`data-layer="container"`,
		`data-layer="production"`,
		`data-layer="creature"`,
		`data-layer="terrain-risk"`,
		`data-layer="other"`,
		`data-location-category="spawn"`,
		`data-location-category="boss"`,
		`data-location-category="trader"`,
		`data-location-category="dungeon"`,
		`data-location-category="fortress"`,
		`data-location-category="settlement"`,
		`data-location-category="resource"`,
		`data-location-category="landmark"`,
		`data-location-category="other"`,
		`id="zoom-out"`,
		`id="zoom-in"`,
		`tabindex="0"`,
		`id="category-summary"`,
		`Player-build coverage appears at close zoom.`,
		`Terrain detail follows the zoom level automatically.`,
	} {
		if !strings.Contains(page, expected) {
			t.Fatalf("world analysis page missing %q", expected)
		}
	}
	if strings.Contains(page, `data-layer="zones" checked`) {
		t.Fatal("generated zones default to enabled")
	}
	if strings.Contains(page, `id="detail"`) {
		t.Fatal("world analysis page still exposes the removed manual terrain detail control")
	}
}

func TestPlayerHomeStatusLightsUseServerStateClasses(t *testing.T) {
	response := httptest.NewRecorder()
	render(response, playerHomeTemplate, map[string]any{
		"Worlds": []playerHomeWorld{
			{PublicWorld: PublicWorld{Name: "OnlineWorld", Status: "online"}},
			{PublicWorld: PublicWorld{Name: "OfflineWorld", Status: "offline"}},
		},
	})
	if response.Code != http.StatusOK {
		t.Fatalf("render = %d: %s", response.Code, response.Body.String())
	}
	page := response.Body.String()
	for _, expected := range []string{
		`class="status status-online">online</span>`,
		`class="status status-offline">offline</span>`,
	} {
		if !strings.Contains(page, expected) {
			t.Fatalf("player home missing status indicator %q", expected)
		}
	}
}

func TestLogoutClearsSteamAndAdminSessions(t *testing.T) {
	server := testServer(t)
	request := httptest.NewRequest(http.MethodPost, "/logout", nil)
	request.AddCookie(&http.Cookie{Name: steamSessionCookie, Value: "steam-session"})
	request.AddCookie(&http.Cookie{Name: adminSessionCookie, Value: "admin-session"})
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusSeeOther || response.Header().Get("Location") != "/" {
		t.Fatalf("logout response = %d location %q", response.Code, response.Header().Get("Location"))
	}
	cleared := map[string]bool{}
	for _, cookie := range response.Result().Cookies() {
		if cookie.Name == steamSessionCookie || cookie.Name == adminSessionCookie {
			if cookie.Value != "" || cookie.MaxAge >= 0 {
				t.Fatalf("logout cookie = %#v", cookie)
			}
			cleared[cookie.Name] = true
		}
	}
	if !cleared[steamSessionCookie] || !cleared[adminSessionCookie] {
		t.Fatalf("logout cleared sessions = %#v", cleared)
	}
}

func TestAdminHomeEstablishesSignedAdminSession(t *testing.T) {
	server := testServer(t)
	request := httptest.NewRequest(http.MethodGet, "/admin", nil)
	request.RemoteAddr = "192.0.2.10:1234"
	request.Header.Set(server.cfg.AuthHeader, "admin")
	request.Header.Set(adminTokenHeader, testAdminToken)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("authenticated admin page = %d: %s", response.Code, response.Body.String())
	}
	for _, cookie := range response.Result().Cookies() {
		if cookie.Name != adminSessionCookie {
			continue
		}
		publicRequest := httptest.NewRequest(http.MethodGet, "/", nil)
		publicRequest.AddCookie(cookie)
		if !server.isAdmin(publicRequest) {
			t.Fatal("signed admin session was not recognized on a public page")
		}
		return
	}
	t.Fatal("authenticated admin page did not establish an admin session")
}

// A primary-styled Administration button rendered for logged-out visitors too,
// and led to a bare 401. The flag that decides it was already on every page.
func TestPortalNavigationLinksToAdministrationOnlyForAdmins(t *testing.T) {
	const link = `<a class="portal-nav-button portal-admin-link" href="/admin">Administration</a>`
	templates := map[string]string{
		"login": loginPageTemplate,
		"home":  playerHomeTemplate,
		"world": playerWorldTemplate,
	}
	for templateName, pageTemplate := range templates {
		visitor := httptest.NewRecorder()
		render(visitor, pageTemplate, map[string]any{"IsAdmin": false})
		if strings.Contains(visitor.Body.String(), link) {
			t.Fatalf("%s page offers administration to a non-admin", templateName)
		}
		administrator := httptest.NewRecorder()
		render(administrator, pageTemplate, map[string]any{"IsAdmin": true})
		if !strings.Contains(administrator.Body.String(), link) {
			t.Fatalf("%s page hides administration from an admin", templateName)
		}
	}
}

func TestMapResourcesDoNotExhaustGeneralRateLimit(t *testing.T) {
	server := testServer(t)
	for index := 0; index < 100; index++ {
		response := httptest.NewRecorder()
		server.Handler().ServeHTTP(response, adminTestRequest(http.MethodGet, "/admin/worlds/Midgard/map/tiles/key/0/0/0.png", nil))
		if response.Code == http.StatusTooManyRequests {
			t.Fatalf("map tile request %d was rate limited", index+1)
		}
	}

	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/healthz", nil))
	if response.Code != http.StatusOK {
		t.Fatalf("general rate limit was consumed by map tiles: %d", response.Code)
	}
}

// The trusted-proxy range is not a secret and the identity header is one line
// of proxy configuration away from being browser-supplied. Under the shipped
// compose deployment every request arrives from the bridge gateway, which is
// the trusted range, so the token is the only factor an attacker on the host
// cannot produce.
func TestAdminRejectsATrustedIdentityWithoutTheAdminToken(t *testing.T) {
	server := testServer(t)
	for _, token := range []struct {
		name  string
		value string
	}{
		{"absent", ""},
		{"wrong", "ffffffffffffffffffffffffffffffff"},
		{"truncated", testAdminToken[:16]},
		{"prefixed", testAdminToken + "extra"},
	} {
		request := httptest.NewRequest(http.MethodGet, "/admin", nil)
		request.RemoteAddr = "192.0.2.10:1234"
		request.Header.Set(server.cfg.AuthHeader, "operator")
		if token.value != "" {
			request.Header.Set(adminTokenHeader, token.value)
		}
		response := httptest.NewRecorder()
		server.Handler().ServeHTTP(response, request)
		if response.Code != http.StatusUnauthorized {
			t.Fatalf("admin token %s = %d, want 401", token.name, response.Code)
		}
	}

	request := httptest.NewRequest(http.MethodGet, "/admin", nil)
	request.RemoteAddr = "192.0.2.10:1234"
	request.Header.Set(server.cfg.AuthHeader, "operator")
	request.Header.Set(adminTokenHeader, testAdminToken)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("admin with the injected token = %d: %s", response.Code, response.Body.String())
	}
}

// A portal that starts without a token silently falls back to header-only
// administration, which is the failure this token exists to prevent.
func TestNewServerRefusesToStartWithoutAnAdminToken(t *testing.T) {
	for _, tokenFile := range []struct {
		name     string
		contents string
		write    bool
		set      bool
	}{
		{name: "unset"},
		{name: "missing file", set: true},
		{name: "empty", set: true, write: true},
		{name: "whitespace", set: true, write: true, contents: "   \n\t\n"},
		{name: "too short", set: true, write: true, contents: "0123456789abcdef"},
	} {
		t.Run(tokenFile.name, func(t *testing.T) {
			dir := t.TempDir()
			secret := filepath.Join(dir, "csrf")
			token := filepath.Join(dir, "token")
			for _, path := range []string{secret, token} {
				if err := os.WriteFile(path, []byte("12345678901234567890123456789012"), 0o600); err != nil {
					t.Fatal(err)
				}
			}
			adminToken := filepath.Join(dir, "admin-token")
			if tokenFile.write {
				if err := os.WriteFile(adminToken, []byte(tokenFile.contents), 0o600); err != nil {
					t.Fatal(err)
				}
			}
			t.Setenv("PORTAL_ADMIN_TOKEN_FILE", "")
			if tokenFile.set {
				t.Setenv("PORTAL_ADMIN_TOKEN_FILE", adminToken)
			}
			store, err := OpenStore(filepath.Join(dir, "db.sqlite"))
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() { store.Close() })
			agent, err := NewAgentClient(filepath.Join(dir, "agent.sock"), token)
			if err != nil {
				t.Fatal(err)
			}
			server, err := NewServer(Config{
				DatabasePath: filepath.Join(dir, "db.sqlite"), ArtifactRoot: dir, MapRoot: filepath.Join(dir, "maps"), CSRFSecretFile: secret,
				AgentTokenFile: token, AgentSocket: filepath.Join(dir, "agent.sock"), AuthHeader: "X-Forwarded-User",
				CookieSecure: false, TrustedProxyCIDR: "192.0.2.0/24", PublicBaseURL: "https://portal.example.test",
			}, store, agent)
			if err == nil {
				t.Fatalf("NewServer started with %s admin token file; server=%p", tokenFile.name, server)
			}
		})
	}
}

// One NATed source address for every visitor meant one shared bucket: sixty
// anonymous /healthz requests locked out every operator and every player.
func TestRateLimitBucketsAreKeyedPerForwardedClient(t *testing.T) {
	server := testServer(t)
	spend := func(remote, forwarded string) int {
		request := httptest.NewRequest(http.MethodGet, "/healthz", nil)
		request.RemoteAddr = remote
		if forwarded != "" {
			request.Header.Set("X-Forwarded-For", forwarded)
		}
		response := httptest.NewRecorder()
		server.Handler().ServeHTTP(response, request)
		return response.Code
	}

	// One client behind the proxy exhausts its own budget.
	var exhausted bool
	for range 60 {
		if spend("192.0.2.10:1234", "198.51.100.7") == http.StatusTooManyRequests {
			exhausted = true
			break
		}
	}
	if !exhausted {
		t.Fatal("a single forwarded client never hit its own rate limit")
	}
	if code := spend("192.0.2.10:1234", "198.51.100.8, 203.0.113.9"); code != http.StatusOK {
		t.Fatalf("second forwarded client = %d, want 200: the proxy's own address is still the key", code)
	}

	// A direct client is not behind the proxy, so its X-Forwarded-For is a
	// claim about someone else's quota and must be ignored.
	direct := testServer(t)
	spendDirect := func() int {
		request := httptest.NewRequest(http.MethodGet, "/healthz", nil)
		request.RemoteAddr = "203.0.113.5:4321"
		request.Header.Set("X-Forwarded-For", randomHex(8))
		response := httptest.NewRecorder()
		direct.Handler().ServeHTTP(response, request)
		return response.Code
	}
	limited := false
	for range 60 {
		if spendDirect() == http.StatusTooManyRequests {
			limited = true
			break
		}
	}
	if !limited {
		t.Fatal("an untrusted client rotated X-Forwarded-For to escape its own bucket")
	}
}

// The device token route is polled every two seconds by the Windows client, so
// a pending sign-in used to spend most of the general budget before the player
// had done anything wrong.
func TestDeviceTokenPollingDoesNotSpendTheGeneralBucket(t *testing.T) {
	server := testServer(t)
	for poll := range 60 {
		request := httptest.NewRequest(http.MethodPost, "/client/token/"+randomID(), nil)
		request.RemoteAddr = "198.51.100.20:5555"
		response := httptest.NewRecorder()
		server.Handler().ServeHTTP(response, request)
		if response.Code == http.StatusTooManyRequests {
			t.Fatalf("device poll %d was rate limited", poll+1)
		}
	}
	request := httptest.NewRequest(http.MethodGet, "/healthz", nil)
	request.RemoteAddr = "198.51.100.20:5555"
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("general bucket after device polling = %d", response.Code)
	}
}

// countingReader reports how much of a request body the server actually read.
type countingReader struct {
	remaining int64
	read      int64
}

func (c *countingReader) Read(p []byte) (int, error) {
	if c.remaining <= 0 {
		return 0, io.EOF
	}
	if int64(len(p)) > c.remaining {
		p = p[:c.remaining]
	}
	for i := range p {
		p[i] = 'a'
	}
	c.remaining -= int64(len(p))
	c.read += int64(len(p))
	return len(p), nil
}

// validCSRF calls r.FormValue, so the admin guard parses the body before any
// handler runs; a MaxBytesReader installed inside the handler is a documented
// no-op by then. The upload route therefore accepted an unbounded multipart
// body until the limit moved into the guard.
func TestOversizedAdminUploadIsRejectedBeforeTheBodyIsBuffered(t *testing.T) {
	server := testServer(t)
	server.uploadBodyLimit = 8 << 10
	const boundary = "0f1e2d3c"
	filler := &countingReader{remaining: 4 << 20}
	part := "--" + boundary + "\r\n" +
		"Content-Disposition: form-data; name=\"artifact\"; filename=\"oversized.bin\"\r\n" +
		"Content-Type: application/octet-stream\r\n\r\n"
	request := httptest.NewRequest(http.MethodPost, "/admin/artifacts", io.MultiReader(strings.NewReader(part), filler))
	request.ContentLength = -1
	request.RemoteAddr = "192.0.2.10:1234"
	request.Header.Set(server.cfg.AuthHeader, "operator")
	request.Header.Set(adminTokenHeader, testAdminToken)
	request.Header.Set("Content-Type", "multipart/form-data; boundary="+boundary)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("oversized upload = %d, want 413: %s", response.Code, response.Body.String())
	}
	if filler.read > server.uploadBodyLimit {
		t.Fatalf("server read %d bytes of a body it capped at %d", filler.read, server.uploadBodyLimit)
	}
	artifacts, err := server.store.allReleases(context.Background())
	if err != nil || len(artifacts) != 0 {
		t.Fatalf("the upload handler ran on a rejected body: %v, %v", artifacts, err)
	}
}

// The same ceiling applies to the ordinary form routes, which is what makes the
// deleted per-handler MaxBytesReader lines redundant rather than merely dead.
func TestOversizedAdminFormIsRejected(t *testing.T) {
	server := testServer(t)
	server.formBodyLimit = 4 << 10
	form := "csrf=" + strings.Repeat("a", 64<<10)
	request := httptest.NewRequest(http.MethodPost, "/admin/jobs", strings.NewReader(form))
	request.RemoteAddr = "192.0.2.10:1234"
	request.Header.Set(server.cfg.AuthHeader, "operator")
	request.Header.Set(adminTokenHeader, testAdminToken)
	request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("oversized admin form = %d, want 413: %s", response.Code, response.Body.String())
	}
}

// A world is registered and reachable before anything has been published for it.
// Vanaheim and Jotunheim were provisioned, registered, enabled, and access-granted,
// yet their pages returned 404 because the handler gated the page on the release
// list. The signed-in home page links every world the player may see, so the portal
// was linking to its own missing page.
func TestWorldPageIsServedBeforeAnyReleaseIsPublished(t *testing.T) {
	server := testServer(t)
	ctx := context.Background()
	world := PublicWorld{
		Name: "Jotunheim", JoinAddress: "valheim.example.com:2466",
		Status: "offline", ServerVersion: "unknown", Enabled: true,
	}
	if err := server.store.UpsertPublicWorld(ctx, world, "operator"); err != nil {
		t.Fatal(err)
	}
	if err := server.store.GrantWorldAccess(ctx, world.Name, testSteamID, "admin"); err != nil {
		t.Fatal(err)
	}

	request := httptest.NewRequest(http.MethodGet, "/worlds/Jotunheim", nil)
	request.AddCookie(steamCookie(t, server, testSteamID))
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("a registered world with no releases must still have a page; got %d", response.Code)
	}
	body := response.Body.String()
	if !strings.Contains(body, "Jotunheim") {
		t.Fatalf("the world page does not name the world: %s", body)
	}
	// The template already carries this empty state; the handler was the only thing
	// standing between a fresh world and its own page.
	if !strings.Contains(body, "No current client profile is available.") {
		t.Fatalf("the empty profile state was not rendered: %s", body)
	}

	// A name that was never registered is still a genuine 404.
	missing := httptest.NewRequest(http.MethodGet, "/worlds/NotAWorld", nil)
	missing.AddCookie(steamCookie(t, server, testSteamID))
	missingResponse := httptest.NewRecorder()
	server.Handler().ServeHTTP(missingResponse, missing)
	if missingResponse.Code == http.StatusOK {
		t.Fatalf("an unregistered world must not render a page; got %d", missingResponse.Code)
	}
}

// The operator met "rate limit exceeded" while reading an administration page. The limiter has
// applied to every route since the first commit, and an admin page is not one request: it is the
// page, its assets, a status poll, and a dock that polls while open - all from one address, against
// a bucket of 40 a minute. Administration is already gated by the trusted proxy, the identity
// header, the admin token and CSRF; a limiter behind all four protects nothing and blocks the only
// person entitled to be there.
func TestAdministrationIsNotRateLimited(t *testing.T) {
	server := testServer(t)

	for index := range 200 {
		response := httptest.NewRecorder()
		server.Handler().ServeHTTP(response, adminTestRequest(http.MethodGet, "/admin", nil))
		if response.Code == http.StatusTooManyRequests {
			t.Fatalf("admin request %d was rate limited", index+1)
		}
	}
	for _, asset := range []string{"/assets/site.css", "/assets/admin-agent.js", "/assets/admin-dock.js"} {
		for range 60 {
			response := httptest.NewRecorder()
			server.Handler().ServeHTTP(response, httptest.NewRequest(http.MethodGet, asset, nil))
			if response.Code == http.StatusTooManyRequests {
				t.Fatalf("%s was rate limited; the operator's own page cannot load", asset)
			}
		}
	}
}

// And the limiter must still hold where it was meant to: anonymous traffic on a player route.
func TestAnonymousTrafficIsStillLimited(t *testing.T) {
	server := testServer(t)
	limited := false
	for range 200 {
		request := httptest.NewRequest(http.MethodGet, "/", nil)
		request.RemoteAddr = "203.0.113.9:5555"
		response := httptest.NewRecorder()
		server.Handler().ServeHTTP(response, request)
		if response.Code == http.StatusTooManyRequests {
			limited = true
			break
		}
	}
	if !limited {
		t.Error("anonymous requests are unbounded; the limiter now protects nothing")
	}
}
