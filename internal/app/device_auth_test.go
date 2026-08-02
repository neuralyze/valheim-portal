package app

import (
	"context"
	"net/http"
	"net/http/httptest"
	"net/url"
	"regexp"
	"strings"
	"testing"
)

var deviceCSRFPattern = regexp.MustCompile(`name="csrf" value="([^"]+)"`)

// deviceConfirmationForm opens the confirmation page as the given Steam account
// and returns the credentials its form carries.
func deviceConfirmationForm(t *testing.T, server *Server, code string) (csrf string, cookie *http.Cookie) {
	t.Helper()
	request := httptest.NewRequest(http.MethodGet, "/client/authorize/"+code, nil)
	request.AddCookie(steamCookie(t, server, testSteamID))
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("confirmation page = %d: %s", response.Code, response.Body.String())
	}
	match := deviceCSRFPattern.FindStringSubmatch(response.Body.String())
	if len(match) != 2 {
		t.Fatalf("confirmation page carries no CSRF token: %s", response.Body.String())
	}
	for _, issued := range response.Result().Cookies() {
		if issued.Name == deviceCSRFCookie {
			return match[1], issued
		}
	}
	t.Fatalf("confirmation page issued no %s cookie", deviceCSRFCookie)
	return "", nil
}

// submitDeviceConfirmation posts the confirmation form with an arbitrary user
// code, reusing credentials the page already issued.
func submitDeviceConfirmation(t *testing.T, server *Server, code, userCode, csrf string, cookie *http.Cookie) *httptest.ResponseRecorder {
	t.Helper()
	form := url.Values{"csrf": {csrf}, "user_code": {userCode}}
	request := httptest.NewRequest(http.MethodPost, "/client/authorize/"+code, strings.NewReader(form.Encode()))
	request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	request.AddCookie(steamCookie(t, server, testSteamID))
	request.AddCookie(cookie)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	return response
}

// confirmDevice walks the whole browser half of the flow: open the page, then
// approve it with a user code. Shared with the device tests in server_test.go.
func confirmDevice(t *testing.T, server *Server, device deviceStartResponse, userCode string, want int) *httptest.ResponseRecorder {
	t.Helper()
	csrf, cookie := deviceConfirmationForm(t, server, device.DeviceCode)
	response := submitDeviceConfirmation(t, server, device.DeviceCode, userCode, csrf, cookie)
	if response.Code != want {
		t.Fatalf("device confirmation = %d, want %d: %s", response.Code, want, response.Body.String())
	}
	if want == http.StatusOK && !strings.Contains(response.Body.String(), "Sign-in complete") {
		t.Fatalf("device confirmation did not complete: %s", response.Body.String())
	}
	return response
}

func devicePending(t *testing.T, server *Server, code string) bool {
	t.Helper()
	request := httptest.NewRequest(http.MethodPost, "/client/token/"+code, nil)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code == http.StatusOK {
		return false
	}
	if response.Code != http.StatusUnauthorized {
		t.Fatalf("token exchange = %d: %s", response.Code, response.Body.String())
	}
	return true
}

func deviceTestServer(t *testing.T, release Release) *Server {
	t.Helper()
	server := testServer(t)
	publishProfile(t, server, release)
	if err := server.store.GrantWorldAccess(context.Background(), release.World, testSteamID, "admin"); err != nil {
		t.Fatal(err)
	}
	return server
}

func TestDeviceAuthorizationPageDoesNotAuthorizeOnItsOwn(t *testing.T) {
	release := Release{ID: "confirm-release", World: "Ashlands", Profile: "raiders", ClientType: "flat", Version: "1.0.0", Notes: "test"}
	server := deviceTestServer(t, release)
	device := startDevice(t, server, release.World, release.Profile, release.ClientType)

	page := httptest.NewRequest(http.MethodGet, "/client/authorize/"+device.DeviceCode, nil)
	page.AddCookie(steamCookie(t, server, testSteamID))
	pageResponse := httptest.NewRecorder()
	server.Handler().ServeHTTP(pageResponse, page)
	if pageResponse.Code != http.StatusOK {
		t.Fatalf("confirmation page = %d: %s", pageResponse.Code, pageResponse.Body.String())
	}
	body := pageResponse.Body.String()
	if strings.Contains(body, "Sign-in complete") {
		t.Fatalf("the authorize link reported a completed sign-in: %s", body)
	}
	// The page has to name what is being approved, and must never leak the code
	// the player is meant to read off the desktop application.
	for _, want := range []string{release.World, release.Profile, testSteamID, `name="user_code"`} {
		if !strings.Contains(body, want) {
			t.Fatalf("confirmation page omits %q: %s", want, body)
		}
	}
	if strings.Contains(body, device.UserCode) {
		t.Fatalf("confirmation page leaks the user code %q", device.UserCode)
	}

	if !devicePending(t, server, device.DeviceCode) {
		t.Fatal("opening the confirmation link authorized the device on its own")
	}
}

func TestDeviceStartIssuesATypeableUserCode(t *testing.T) {
	release := Release{ID: "usercode-release", World: "Ashlands", Profile: "raiders", ClientType: "flat", Version: "1.0.0", Notes: "test"}
	server := deviceTestServer(t, release)
	device := startDevice(t, server, release.World, release.Profile, release.ClientType)
	if len(device.UserCode) != deviceUserCodeLength+1 || device.UserCode[deviceUserCodeLength/2] != '-' {
		t.Fatalf("user code = %q", device.UserCode)
	}
	for _, letter := range strings.ReplaceAll(device.UserCode, "-", "") {
		if !strings.ContainsRune(deviceUserCodeAlphabet, letter) {
			t.Fatalf("user code %q uses %q, which is outside the readable alphabet", device.UserCode, letter)
		}
	}
	second := startDevice(t, server, release.World, release.Profile, release.ClientType)
	if second.UserCode == device.UserCode {
		t.Fatalf("two device flows shared the user code %q", device.UserCode)
	}
}

func TestDeviceConfirmationRequiresTheUserCodeAndCSRFToken(t *testing.T) {
	release := Release{ID: "match-release", World: "Ashlands", Profile: "raiders", ClientType: "flat", Version: "1.0.0", Notes: "test"}
	server := deviceTestServer(t, release)
	device := startDevice(t, server, release.World, release.Profile, release.ClientType)
	csrf, cookie := deviceConfirmationForm(t, server, device.DeviceCode)

	wrong := submitDeviceConfirmation(t, server, device.DeviceCode, "ZZZZ-ZZZZ", csrf, cookie)
	if wrong.Code != http.StatusBadRequest {
		t.Fatalf("wrong user code = %d: %s", wrong.Code, wrong.Body.String())
	}
	if !devicePending(t, server, device.DeviceCode) {
		t.Fatal("a wrong user code authorized the device")
	}

	forged := submitDeviceConfirmation(t, server, device.DeviceCode, device.UserCode, csrf+"0", cookie)
	if forged.Code != http.StatusForbidden {
		t.Fatalf("forged CSRF token = %d: %s", forged.Code, forged.Body.String())
	}
	if !devicePending(t, server, device.DeviceCode) {
		t.Fatal("a forged CSRF token authorized the device")
	}

	noCookie := url.Values{"csrf": {csrf}, "user_code": {device.UserCode}}
	request := httptest.NewRequest(http.MethodPost, "/client/authorize/"+device.DeviceCode, strings.NewReader(noCookie.Encode()))
	request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	request.AddCookie(steamCookie(t, server, testSteamID))
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusForbidden {
		t.Fatalf("confirmation without the CSRF cookie = %d: %s", response.Code, response.Body.String())
	}
	if !devicePending(t, server, device.DeviceCode) {
		t.Fatal("a confirmation without the CSRF cookie authorized the device")
	}

	// A player who types the code without its dash, in lower case, still gets in.
	approved := submitDeviceConfirmation(t, server, device.DeviceCode, strings.ToLower(strings.ReplaceAll(device.UserCode, "-", "")), csrf, cookie)
	if approved.Code != http.StatusOK || !strings.Contains(approved.Body.String(), "Sign-in complete") {
		t.Fatalf("correct user code = %d: %s", approved.Code, approved.Body.String())
	}
	if devicePending(t, server, device.DeviceCode) {
		t.Fatal("an approved confirmation left the device unauthorized")
	}
}

func TestDeviceConfirmationLocksOutUserCodeGuessing(t *testing.T) {
	release := Release{ID: "bruteforce-release", World: "Ashlands", Profile: "raiders", ClientType: "flat", Version: "1.0.0", Notes: "test"}
	server := deviceTestServer(t, release)
	device := startDevice(t, server, release.World, release.Profile, release.ClientType)
	csrf, cookie := deviceConfirmationForm(t, server, device.DeviceCode)

	for attempt := 1; attempt < deviceUserCodeMaxAttempts; attempt++ {
		response := submitDeviceConfirmation(t, server, device.DeviceCode, "ZZZZ-ZZZZ", csrf, cookie)
		if response.Code != http.StatusBadRequest {
			t.Fatalf("guess %d = %d: %s", attempt, response.Code, response.Body.String())
		}
	}
	final := submitDeviceConfirmation(t, server, device.DeviceCode, "ZZZZ-ZZZZ", csrf, cookie)
	if final.Code != http.StatusTooManyRequests {
		t.Fatalf("guess %d = %d, want the lockout: %s", deviceUserCodeMaxAttempts, final.Code, final.Body.String())
	}
	// The lockout destroys the grant, so even the real code no longer works.
	recovered := submitDeviceConfirmation(t, server, device.DeviceCode, device.UserCode, csrf, cookie)
	if recovered.Code != http.StatusNotFound {
		t.Fatalf("correct code after lockout = %d: %s", recovered.Code, recovered.Body.String())
	}
	if !devicePending(t, server, device.DeviceCode) {
		t.Fatal("a locked-out device code was still authorized")
	}
}

func TestDeviceConfirmationRejectsAnotherSteamAccount(t *testing.T) {
	release := Release{ID: "owner-release", World: "Ashlands", Profile: "raiders", ClientType: "flat", Version: "1.0.0", Notes: "test"}
	server := deviceTestServer(t, release)
	device := startDevice(t, server, release.World, release.Profile, release.ClientType)
	// The CSRF credentials are not bound to a Steam account, so reusing them is
	// what forces this POST past the CSRF check and onto the ownership check.
	csrf, cookie := deviceConfirmationForm(t, server, device.DeviceCode)
	approved := submitDeviceConfirmation(t, server, device.DeviceCode, device.UserCode, csrf, cookie)
	if approved.Code != http.StatusOK {
		t.Fatalf("owner confirmation = %d: %s", approved.Code, approved.Body.String())
	}

	form := url.Values{"csrf": {csrf}, "user_code": {device.UserCode}}
	request := httptest.NewRequest(http.MethodPost, "/client/authorize/"+device.DeviceCode, strings.NewReader(form.Encode()))
	request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	request.AddCookie(steamCookie(t, server, "76561198000000001"))
	request.AddCookie(cookie)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusForbidden || !strings.Contains(response.Body.String(), "another Steam account") {
		t.Fatalf("second Steam account = %d: %s", response.Code, response.Body.String())
	}
}
