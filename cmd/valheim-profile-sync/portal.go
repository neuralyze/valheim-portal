package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os/exec"
	"strings"
	"time"

	"github.com/neuralyze/valheim-portal/internal/version"
)

// userAgentTransport stamps the client build identity on outbound requests so
// portal logs record which synchronizer version a player is running.
type userAgentTransport struct{ next http.RoundTripper }

func (t *userAgentTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	next := t.next
	if next == nil {
		next = http.DefaultTransport
	}
	if request.Header.Get("User-Agent") == "" {
		request = request.Clone(request.Context())
		request.Header.Set("User-Agent", version.UserAgent("profile-sync"))
	}
	return next.RoundTrip(request)
}

const devicePollInterval = 2 * time.Second

type portalClient struct {
	base             *url.URL
	httpClient       *http.Client
	openBrowser      func(string) error
	now              func() time.Time
	wait             func(time.Duration)
	Progress         progressReporter
	diagnosticsToken string
	explorationToken string
}

// errNoConfirmationCode is shared with the test that pins the sign-in decision table.
var errNoConfirmationCode = errors.New("portal returned no confirmation code for this sign-in")

type deviceRequest struct {
	World      string `json:"world"`
	Profile    string `json:"profile"`
	ClientType string `json:"client_type"`
}

type deviceResponse struct {
	DeviceCode string `json:"device_code"`
	// UserCode is the short code the player has to type on the portal's
	// confirmation page. The portal authorizes nothing without it, so it has to
	// be on screen here before the browser opens.
	UserCode string `json:"user_code"`
	// ConfirmationRequired is the portal's own statement about its sign-in: a single-operator
	// install authorizes on the Steam sign-in alone and asks for no code. A pointer because absent
	// and false must differ - a portal too old to send the field still wants its code typed, and
	// reading that silence as "no confirmation needed" would strand the player on a page whose
	// field never gets filled.
	ConfirmationRequired *bool  `json:"confirmation_required"`
	AuthorizeURL         string `json:"authorize_url"`
	ExpiresIn            int    `json:"expires_in"`
}

type tokenResponse struct {
	Token            string `json:"token"`
	DiagnosticsToken string `json:"diagnostics_token"`
	ExplorationToken string `json:"exploration_token"`
}

type remoteManifest struct {
	ReleaseID       string `json:"release_id"`
	World           string `json:"world"`
	Profile         string `json:"profile"`
	ClientType      string `json:"client_type"`
	Version         string `json:"version"`
	ProfileSHA256   string `json:"profile_sha256"`
	ProfileSize     int64  `json:"profile_size"`
	RuntimeSHA256   string `json:"runtime_sha256,omitempty"`
	RuntimeSize     int64  `json:"runtime_size,omitempty"`
	CompanionSHA256 string `json:"companion_sha256,omitempty"`
	CompanionSize   int64  `json:"companion_size,omitempty"`
	// DiagnosticsPlugin is served to both client types, so it is validated
	// independently of the VR/Flat artifact scoping below.
	DiagnosticsPluginSHA256 string `json:"diagnostics_plugin_sha256,omitempty"`
	DiagnosticsPluginSize   int64  `json:"diagnostics_plugin_size,omitempty"`
}

type portalStatus struct {
	Worlds []worldStatus `json:"worlds"`
}

type worldStatus struct {
	World       string `json:"world"`
	Profile     string `json:"profile"`
	ClientType  string `json:"client_type"`
	Status      string `json:"status"`
	Maintenance bool   `json:"maintenance"`
}

type serverUnavailableError struct {
	World  string
	Status string
}

func (err *serverUnavailableError) Error() string {
	if err.Status == "maintenance" {
		return fmt.Sprintf("%s is in maintenance. Valheim was not launched.", err.World)
	}
	return fmt.Sprintf("%s is offline. Valheim was not launched.", err.World)
}

func newPortalClient(request profileRequest, httpClient *http.Client) (*portalClient, error) {
	if err := request.validate(); err != nil {
		return nil, err
	}
	if httpClient == nil {
		httpClient = &http.Client{Timeout: time.Minute}
	}
	// Identify the client build on every portal request. The caller's client is
	// copied rather than mutated, so a supplied client keeps its own transport.
	stamped := *httpClient
	stamped.Transport = &userAgentTransport{next: httpClient.Transport}
	base := *request.Portal
	return &portalClient{
		base:        &base,
		httpClient:  &stamped,
		openBrowser: openSystemBrowser,
		now:         time.Now,
		wait:        time.Sleep,
	}, nil
}

func openSystemBrowser(target string) error {
	return exec.Command("rundll32.exe", "url.dll,FileProtocolHandler", target).Start()
}

func (client *portalClient) endpoint(parts ...string) string {
	u := *client.base
	path := strings.TrimRight(u.Path, "/")
	for _, part := range parts {
		path += "/" + part
	}
	u.Path = path
	u.RawPath = ""
	u.RawQuery = ""
	u.ForceQuery = false
	u.Fragment = ""
	return u.String()
}

func (client *portalClient) statusEndpoint() string {
	u := *client.base
	path := strings.TrimSuffix(strings.TrimRight(u.Path, "/"), "/client")
	u.Path = path + "/api/status"
	u.RawPath = ""
	u.RawQuery = ""
	u.ForceQuery = false
	u.Fragment = ""
	return u.String()
}

func (client *portalClient) requireOnline(ctx context.Context, request profileRequest) error {
	httpRequest, err := http.NewRequestWithContext(ctx, http.MethodGet, client.statusEndpoint(), nil)
	if err != nil {
		return err
	}
	response, err := client.httpClient.Do(httpRequest)
	if err != nil {
		return fmt.Errorf("check server status: %w", err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return fmt.Errorf("check server status: %s", response.Status)
	}
	var status portalStatus
	if err := json.NewDecoder(io.LimitReader(response.Body, 1<<20)).Decode(&status); err != nil {
		return fmt.Errorf("read server status: %w", err)
	}
	for _, world := range status.Worlds {
		if world.World != request.World || world.Profile != request.Profile || world.ClientType != request.ClientType {
			continue
		}
		if world.Status == "online" && !world.Maintenance {
			return nil
		}
		if world.Maintenance {
			return &serverUnavailableError{World: world.World, Status: "maintenance"}
		}
		return &serverUnavailableError{World: world.World, Status: world.Status}
	}
	return fmt.Errorf("server status for %s is unavailable", request.World)
}

func (client *portalClient) authorize(ctx context.Context, request profileRequest) (string, error) {
	report(client.Progress, progressUpdate{Stage: "Requesting secure Steam sign-in", Detail: "Preparing authorization for this selected profile.", Percent: 8})
	payload, err := json.Marshal(deviceRequest{World: request.World, Profile: request.Profile, ClientType: request.ClientType})
	if err != nil {
		return "", err
	}
	httpRequest, err := http.NewRequestWithContext(ctx, http.MethodPost, client.endpoint("client", "device"), bytes.NewReader(payload))
	if err != nil {
		return "", err
	}
	httpRequest.Header.Set("Content-Type", "application/json")
	response, err := client.httpClient.Do(httpRequest)
	if err != nil {
		return "", err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK && response.StatusCode != http.StatusCreated {
		return "", fmt.Errorf("start device authorization: %s", response.Status)
	}
	var device deviceResponse
	if err := decodeLimitedJSON(response.Body, &device); err != nil {
		return "", fmt.Errorf("decode device authorization: %w", err)
	}
	if !validOpaqueCode(device.DeviceCode) || device.ExpiresIn < 1 || device.ExpiresIn > 900 {
		return "", errors.New("portal returned an invalid device authorization")
	}
	// The portal says whether it will ask for a code; the code itself is not the signal. A portal
	// that wants one and sends none would strand the player on a page they cannot complete, so that
	// stays an error - the case this refusal was written for.
	confirmationRequired := device.ConfirmationRequired == nil || *device.ConfirmationRequired
	if confirmationRequired && !validUserCode(device.UserCode) {
		return "", errNoConfirmationCode
	}
	if !confirmationRequired {
		device.UserCode = ""
	}
	authorizeURL, err := url.Parse(device.AuthorizeURL)
	if err != nil || !strings.EqualFold(authorizeURL.Scheme, "https") || authorizeURL.Host == "" || authorizeURL.User != nil || authorizeURL.Fragment != "" {
		return "", errors.New("portal returned an invalid authorization URL")
	}
	// The code goes out before the browser opens, and again while polling: where one is required
	// the portal authorizes nothing until the player types it, so a player who never saw it just
	// watches a page they cannot complete.
	openStage, openDetail := "Approving this sign-in", "Your browser is opening the Valheim portal. Sign in with Steam if asked, and this profile is approved."
	waitStage, waitDetail := "Waiting for approval", "Approve the sign-in in your browser. This window continues automatically."
	if device.UserCode != "" {
		openStage = "Confirmation code " + device.UserCode
		openDetail = "Your browser is opening the Valheim portal. Sign in with Steam if asked, then type " + device.UserCode + " there to approve this profile."
		waitStage = "Waiting for confirmation code " + device.UserCode
		waitDetail = "Type " + device.UserCode + " on the portal page in your browser and approve the sign-in. This window continues automatically."
	}
	report(client.Progress, progressUpdate{Stage: openStage, Detail: openDetail, Percent: 12})
	if err := client.openBrowser(authorizeURL.String()); err != nil {
		return "", fmt.Errorf("open Steam authorization page: %w", err)
	}
	report(client.Progress, progressUpdate{Stage: waitStage, Detail: waitDetail, Percent: 14})
	deadline := client.now().Add(time.Duration(device.ExpiresIn) * time.Second)
	for client.now().Before(deadline) {
		token, pending, err := client.exchangeDeviceCode(ctx, device.DeviceCode)
		if err != nil {
			return "", err
		}
		if !pending {
			return token, nil
		}
		client.wait(devicePollInterval)
	}
	return "", errors.New("Steam authorization timed out")
}

func (client *portalClient) exchangeDeviceCode(ctx context.Context, deviceCode string) (token string, pending bool, err error) {
	httpRequest, err := http.NewRequestWithContext(ctx, http.MethodPost, client.endpoint("client", "token", deviceCode), nil)
	if err != nil {
		return "", false, err
	}
	response, err := client.httpClient.Do(httpRequest)
	if err != nil {
		return "", false, err
	}
	defer response.Body.Close()
	if response.StatusCode == http.StatusAccepted || response.StatusCode == http.StatusUnauthorized {
		return "", true, nil
	}
	if response.StatusCode != http.StatusOK {
		return "", false, fmt.Errorf("exchange device authorization: %s", response.Status)
	}
	var result tokenResponse
	if err := decodeLimitedJSON(response.Body, &result); err != nil {
		return "", false, fmt.Errorf("decode authorization token: %w", err)
	}
	if len(result.Token) < 16 || len(result.Token) > 8192 || strings.ContainsAny(result.Token, "\r\n") {
		return "", false, errors.New("portal returned an invalid authorization token")
	}
	if result.DiagnosticsToken != "" {
		if len(result.DiagnosticsToken) < 16 || len(result.DiagnosticsToken) > 8192 || strings.ContainsAny(result.DiagnosticsToken, "\r\n") {
			return "", false, errors.New("portal returned an invalid diagnostics token")
		}
		client.diagnosticsToken = result.DiagnosticsToken
	}
	if result.ExplorationToken != "" {
		if len(result.ExplorationToken) < 16 || len(result.ExplorationToken) > 8192 || strings.ContainsAny(result.ExplorationToken, "\r\n") {
			return "", false, errors.New("portal returned an invalid exploration token")
		}
		client.explorationToken = result.ExplorationToken
	}
	return result.Token, false, nil
}

func (client *portalClient) fetchManifest(ctx context.Context, request profileRequest, token string) (remoteManifest, error) {
	httpRequest, err := http.NewRequestWithContext(ctx, http.MethodGet, client.endpoint("client", "manifest", request.World, request.Profile, request.ClientType), nil)
	if err != nil {
		return remoteManifest{}, err
	}
	httpRequest.Header.Set("Authorization", "Bearer "+token)
	response, err := client.httpClient.Do(httpRequest)
	if err != nil {
		return remoteManifest{}, err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return remoteManifest{}, fmt.Errorf("profile manifest request: %s", response.Status)
	}
	var manifest remoteManifest
	if err := decodeLimitedJSON(response.Body, &manifest); err != nil {
		return remoteManifest{}, fmt.Errorf("decode profile manifest: %w", err)
	}
	if err := manifest.validate(request); err != nil {
		return remoteManifest{}, err
	}
	return manifest, nil
}

func (client *portalClient) profilePayloadRequest(ctx context.Context, request profileRequest, token string) (*http.Response, error) {
	httpRequest, err := http.NewRequestWithContext(ctx, http.MethodGet, client.endpoint("client", "payload", request.World, request.Profile, request.ClientType), nil)
	if err != nil {
		return nil, err
	}
	httpRequest.Header.Set("Authorization", "Bearer "+token)
	response, err := client.httpClient.Do(httpRequest)
	if err != nil {
		return nil, err
	}
	if response.StatusCode != http.StatusOK {
		response.Body.Close()
		return nil, fmt.Errorf("profile payload request: %s", response.Status)
	}
	return response, nil
}

func (client *portalClient) runtimePayloadRequest(ctx context.Context, request profileRequest, token string) (*http.Response, error) {
	httpRequest, err := http.NewRequestWithContext(ctx, http.MethodGet, client.endpoint("client", "runtime", request.World, request.Profile, request.ClientType), nil)
	if err != nil {
		return nil, err
	}
	httpRequest.Header.Set("Authorization", "Bearer "+token)
	response, err := client.httpClient.Do(httpRequest)
	if err != nil {
		return nil, err
	}
	if response.StatusCode != http.StatusOK {
		response.Body.Close()
		return nil, fmt.Errorf("VR runtime payload request: %s", response.Status)
	}
	return response, nil
}

func (client *portalClient) companionPayloadRequest(ctx context.Context, request profileRequest, token string) (*http.Response, error) {
	httpRequest, err := http.NewRequestWithContext(ctx, http.MethodGet, client.endpoint("client", "companion", request.World, request.Profile, request.ClientType), nil)
	if err != nil {
		return nil, err
	}
	httpRequest.Header.Set("Authorization", "Bearer "+token)
	response, err := client.httpClient.Do(httpRequest)
	if err != nil {
		return nil, err
	}
	if response.StatusCode != http.StatusOK {
		response.Body.Close()
		return nil, fmt.Errorf("Flat companion payload request: %s", response.Status)
	}
	return response, nil
}

func (client *portalClient) diagnosticsPluginPayloadRequest(ctx context.Context, request profileRequest, token string) (*http.Response, error) {
	httpRequest, err := http.NewRequestWithContext(ctx, http.MethodGet, client.endpoint("client", "diagnostics-plugin", request.World, request.Profile, request.ClientType), nil)
	if err != nil {
		return nil, err
	}
	httpRequest.Header.Set("Authorization", "Bearer "+token)
	response, err := client.httpClient.Do(httpRequest)
	if err != nil {
		return nil, err
	}
	if response.StatusCode != http.StatusOK {
		response.Body.Close()
		return nil, fmt.Errorf("diagnostics plugin payload request: %s", response.Status)
	}
	return response, nil
}

func (manifest remoteManifest) validate(request profileRequest) error {
	if manifest.ReleaseID == "" || len(manifest.ReleaseID) > 256 || strings.ContainsAny(manifest.ReleaseID, "\r\n") || manifest.World != request.World || manifest.Profile != request.Profile || manifest.ClientType != request.ClientType || !validSHA256(manifest.ProfileSHA256) || manifest.ProfileSize < 1 || manifest.ProfileSize > maxProfileArchiveBytes {
		return errors.New("portal returned an invalid profile manifest")
	}
	if request.ClientType == clientVR {
		if !validSHA256(manifest.RuntimeSHA256) || manifest.RuntimeSize < 1 || manifest.RuntimeSize > maxProfileArchiveBytes {
			return errors.New("portal returned an invalid VR runtime manifest")
		}
		if manifest.CompanionSHA256 != "" || manifest.CompanionSize != 0 {
			return errors.New("portal returned a Flat companion for a VR profile")
		}
	} else {
		if manifest.RuntimeSHA256 != "" || manifest.RuntimeSize != 0 {
			return errors.New("portal returned a VR runtime for a flat profile")
		}
		if (manifest.CompanionSHA256 == "") != (manifest.CompanionSize == 0) {
			return errors.New("portal returned an incomplete Flat companion manifest")
		}
		if manifest.CompanionSHA256 != "" && (!validSHA256(manifest.CompanionSHA256) || manifest.CompanionSize < 1 || manifest.CompanionSize > maxProfileArchiveBytes) {
			return errors.New("portal returned an invalid Flat companion manifest")
		}
	}
	if (manifest.DiagnosticsPluginSHA256 == "") != (manifest.DiagnosticsPluginSize == 0) {
		return errors.New("portal returned an incomplete diagnostics plugin manifest")
	}
	if manifest.DiagnosticsPluginSHA256 != "" && (!validSHA256(manifest.DiagnosticsPluginSHA256) || manifest.DiagnosticsPluginSize < 1 || manifest.DiagnosticsPluginSize > maxProfileArchiveBytes) {
		return errors.New("portal returned an invalid diagnostics plugin manifest")
	}
	return nil
}

func validOpaqueCode(value string) bool {
	if len(value) < 16 || len(value) > 512 {
		return false
	}
	return !strings.ContainsAny(value, "/\\?#\r\n\x00")
}

// validUserCode keeps a hostile portal response out of the window headline: the
// code is rendered verbatim, so it has to be short and free of control
// characters. Grouping dashes are the only punctuation the portal uses.
func validUserCode(value string) bool {
	if len(value) < 4 || len(value) > 32 {
		return false
	}
	for _, r := range value {
		if r == '-' || (r >= 'A' && r <= 'Z') || (r >= '0' && r <= '9') {
			continue
		}
		return false
	}
	return true
}

func decodeLimitedJSON(reader io.Reader, target any) error {
	decoder := json.NewDecoder(io.LimitReader(reader, 1<<20))
	if err := decoder.Decode(target); err != nil {
		return err
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return errors.New("unexpected trailing JSON")
	}
	return nil
}
