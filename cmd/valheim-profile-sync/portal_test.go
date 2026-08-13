package main

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
)

func TestDeviceAuthorizationUsesServerGeneratedCode(t *testing.T) {
	var portalURL string
	server := httptest.NewTLSServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		switch request.URL.Path {
		case "/client/device":
			if request.Method != http.MethodPost {
				t.Fatalf("device method = %s", request.Method)
			}
			var body deviceRequest
			if err := json.NewDecoder(request.Body).Decode(&body); err != nil {
				t.Fatal(err)
			}
			if body.World != "world" || body.Profile != "alpha" || body.ClientType != clientFlat {
				t.Fatalf("device request = %#v", body)
			}
			json.NewEncoder(writer).Encode(deviceResponse{DeviceCode: "0123456789abcdef", UserCode: "BCDF-GHJK", AuthorizeURL: portalURL + "/client/authorize/0123456789abcdef", ExpiresIn: 30})
		case "/client/token/0123456789abcdef":
			if request.Method != http.MethodPost {
				t.Fatalf("token method = %s", request.Method)
			}
			json.NewEncoder(writer).Encode(tokenResponse{Token: "scoped-device-token"})
		default:
			writer.WriteHeader(http.StatusNotFound)
		}
	}))
	defer server.Close()
	portalURL = server.URL
	parsed, err := url.Parse(server.URL)
	if err != nil {
		t.Fatal(err)
	}
	request := profileRequest{Portal: parsed, World: "world", Profile: "alpha", ClientType: clientFlat}
	client, err := newPortalClient(request, server.Client())
	if err != nil {
		t.Fatal(err)
	}
	var stages []string
	client.Progress = func(update progressUpdate) { stages = append(stages, update.Stage+" | "+update.Detail) }
	opened, beforeOpen := "", []string(nil)
	client.openBrowser = func(target string) error {
		opened, beforeOpen = target, append([]string(nil), stages...)
		return nil
	}
	token, err := client.authorize(context.Background(), request)
	if err != nil {
		t.Fatal(err)
	}
	if token != "scoped-device-token" || opened != portalURL+"/client/authorize/0123456789abcdef" {
		t.Fatalf("authorization token=%q opened=%q", token, opened)
	}
	// A player cannot approve a code they were never shown, so it has to be on
	// screen before the browser takes the focus.
	if !strings.Contains(strings.Join(beforeOpen, "\n"), "BCDF-GHJK") {
		t.Fatalf("the confirmation code was not reported before the browser opened: %#v", beforeOpen)
	}
	if !strings.Contains(strings.Join(stages, "\n"), "Waiting for confirmation code BCDF-GHJK") {
		t.Fatalf("the confirmation code was dropped while polling: %#v", stages)
	}
}

func TestDeviceAuthorizationRefusesAPortalWithNoUserCode(t *testing.T) {
	server := httptest.NewTLSServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/client/device" {
			writer.WriteHeader(http.StatusNotFound)
			return
		}
		json.NewEncoder(writer).Encode(deviceResponse{DeviceCode: "0123456789abcdef", AuthorizeURL: "https://portal.example.test/client/authorize/0123456789abcdef", ExpiresIn: 30})
	}))
	defer server.Close()
	parsed, err := url.Parse(server.URL)
	if err != nil {
		t.Fatal(err)
	}
	request := profileRequest{Portal: parsed, World: "world", Profile: "alpha", ClientType: clientFlat}
	client, err := newPortalClient(request, server.Client())
	if err != nil {
		t.Fatal(err)
	}
	client.openBrowser = func(string) error {
		t.Fatal("the browser opened for a sign-in the player could never confirm")
		return nil
	}
	if _, err := client.authorize(context.Background(), request); err == nil {
		t.Fatal("a portal that issues no confirmation code was accepted")
	}
}

func TestRequireOnlineRejectsOfflineWorld(t *testing.T) {
	server := httptest.NewTLSServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/api/status" {
			writer.WriteHeader(http.StatusNotFound)
			return
		}
		json.NewEncoder(writer).Encode(portalStatus{Worlds: []worldStatus{{
			World: "world", Profile: "flat", ClientType: clientFlat, Status: "offline",
		}}})
	}))
	defer server.Close()
	portalURL, err := url.Parse(server.URL + "/client")
	if err != nil {
		t.Fatal(err)
	}
	request := profileRequest{Portal: portalURL, World: "world", Profile: "flat", ClientType: clientFlat}
	client, err := newPortalClient(request, server.Client())
	if err != nil {
		t.Fatal(err)
	}
	err = client.requireOnline(context.Background(), request)
	var unavailable *serverUnavailableError
	if !errors.As(err, &unavailable) || unavailable.Status != "offline" {
		t.Fatalf("offline check error = %v", err)
	}
}

func TestRequireOnlineAcceptsMatchingOnlineProfile(t *testing.T) {
	server := httptest.NewTLSServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		json.NewEncoder(writer).Encode(portalStatus{Worlds: []worldStatus{{
			World: "world", Profile: "flat", ClientType: clientFlat, Status: "online",
		}}})
	}))
	defer server.Close()
	portalURL, err := url.Parse(server.URL + "/client")
	if err != nil {
		t.Fatal(err)
	}
	request := profileRequest{Portal: portalURL, World: "world", Profile: "flat", ClientType: clientFlat}
	client, err := newPortalClient(request, server.Client())
	if err != nil {
		t.Fatal(err)
	}
	if err := client.requireOnline(context.Background(), request); err != nil {
		t.Fatal(err)
	}
}

// A portal that authorizes on the Steam sign-in alone sends no code and says so. The client must
// stop telling players to type one - and must still refuse a portal that wants a code, sends none,
// or is too old to say either way, because that leaves the player at a page they cannot complete.
func TestDeviceAuthorizationFollowsThePortalsStatement(t *testing.T) {
	no, yes := false, true
	for _, tc := range []struct {
		name     string
		required *bool
		code     string
		wantErr  bool
		wantCode string
	}{
		{"single operator: no code, says so", &no, "", false, ""},
		{"confirmation required, code sent", &yes, "BCDF-GHJK", false, "BCDF-GHJK"},
		{"confirmation required, code missing", &yes, "", true, ""},
		{"portal too old to say, no code", nil, "", true, ""},
		{"portal too old to say, code sent", nil, "BCDF-GHJK", false, "BCDF-GHJK"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			device := deviceResponse{DeviceCode: "0123456789abcdef0123456789abcdef", UserCode: tc.code, ConfirmationRequired: tc.required, ExpiresIn: 600}
			required := device.ConfirmationRequired == nil || *device.ConfirmationRequired
			err := error(nil)
			if required && !validUserCode(device.UserCode) {
				err = errNoConfirmationCode
			} else if !required {
				device.UserCode = ""
			}
			if (err != nil) != tc.wantErr {
				t.Fatalf("error = %v, wantErr %v", err, tc.wantErr)
			}
			if err == nil && device.UserCode != tc.wantCode {
				t.Fatalf("displayed code %q, want %q", device.UserCode, tc.wantCode)
			}
		})
	}
}
