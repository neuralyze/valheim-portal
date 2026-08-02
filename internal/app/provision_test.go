package app

import (
	"context"
	"encoding/json"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"regexp"
	"strings"
	"sync"
	"testing"

	"github.com/neuralyze/valheim-portal/internal/worldintel"
)

func TestServerWizardCreatesFirstBackupMapAndPublishes(t *testing.T) {
	server := testServer(t)
	var mu sync.Mutex
	var requests []agentRequest
	mapPublished := false
	server.mapPublisher = func(ctx context.Context, snapshot worldintel.Snapshot, actor string) *worldAnalysisFailure {
		mu.Lock()
		mapPublished = true
		mu.Unlock()
		if err := server.store.SaveWorldAnalysis(ctx, snapshot, actor); err != nil {
			return &worldAnalysisFailure{status: http.StatusInternalServerError, client: "unable to persist analysis", detail: "analysis persistence failed"}
		}
		return nil
	}
	serveMockAgent(t, server, func(w http.ResponseWriter, r *http.Request) {
		var request agentRequest
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			http.Error(w, "bad request", http.StatusBadRequest)
			return
		}
		mu.Lock()
		requests = append(requests, request)
		mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		switch request.Operation {
		case "provision":
			_ = json.NewEncoder(w).Encode(AgentReply{Status: "succeeded", Output: "provisioned and ready", Provisioned: true, Ready: true})
		case "backup", "world_map":
			_ = json.NewEncoder(w).Encode(AgentReply{Status: "succeeded"})
		case "world_analysis":
			_ = json.NewEncoder(w).Encode(AgentReply{Status: "succeeded", Data: json.RawMessage(`{"schema":1,"world":"PortalTestWorld","seed":"AutomaticMap","world_version":37,"source":{"backup":"world-PortalTestWorld-first.tgz","sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}`)})
		default:
			http.Error(w, "unexpected operation", http.StatusBadRequest)
		}
	})

	pageRequest := adminTestRequest(http.MethodGet, "/admin/servers/new", nil)
	pageResponse := httptest.NewRecorder()
	server.Handler().ServeHTTP(pageResponse, pageRequest)
	if pageResponse.Code != http.StatusOK {
		t.Fatalf("new server page = %d: %s", pageResponse.Code, pageResponse.Body.String())
	}
	csrfMatch := regexp.MustCompile(`name="csrf" value="([^"]+)"`).FindStringSubmatch(pageResponse.Body.String())
	cookies := pageResponse.Result().Cookies()
	if len(csrfMatch) != 2 || len(cookies) == 0 {
		t.Fatal("server wizard did not issue CSRF credentials")
	}
	form := url.Values{
		"csrf": {csrfMatch[1]}, "world": {"PortalTestWorld"}, "server_name": {"Neuralyze Portal Test"},
		"password": {"SafePass-123"}, "password_confirm": {"SafePass-123"}, "port": {"26000"},
		"player_limit": {"10"}, "backup_age": {"7"}, "backup_count": {"168"}, "profile": {"default"},
		"preset": {"Normal"}, "backup_interval": {"1h"}, "join_host": {"valheim.example.test"},
		"world_mode": {"random"}, "public": {"true"}, "crossplay": {"true"}, "start": {"true"}, "publish": {"true"},
	}
	reviewRequest := adminTestRequest(http.MethodPost, "/admin/servers/review", strings.NewReader(form.Encode()))
	reviewRequest.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	reviewRequest.AddCookie(cookies[0])
	reviewResponse := httptest.NewRecorder()
	server.Handler().ServeHTTP(reviewResponse, reviewRequest)
	if reviewResponse.Code != http.StatusOK {
		t.Fatalf("server review = %d: %s", reviewResponse.Code, reviewResponse.Body.String())
	}
	if strings.Contains(reviewResponse.Body.String(), "SafePass-123") || !strings.Contains(reviewResponse.Body.String(), "never stored in the portal database") {
		t.Fatal("review exposed the password or omitted its storage guarantee")
	}
	if !strings.Contains(reviewResponse.Body.String(), "publish the 12288 world map") {
		t.Fatal("review omitted automatic map generation")
	}
	idMatch := regexp.MustCompile(`/admin/servers/([a-f0-9]+)`).FindStringSubmatch(reviewResponse.Body.String())
	if len(idMatch) != 2 {
		t.Fatal("review did not contain a creation request ID")
	}
	confirm := url.Values{"csrf": {csrfMatch[1]}, "confirmation": {"CREATE PortalTestWorld"}}
	confirmRequest := adminTestRequest(http.MethodPost, "/admin/servers/"+idMatch[1], strings.NewReader(confirm.Encode()))
	confirmRequest.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	confirmRequest.AddCookie(cookies[0])
	confirmResponse := httptest.NewRecorder()
	server.Handler().ServeHTTP(confirmResponse, confirmRequest)
	if confirmResponse.Code != http.StatusSeeOther {
		t.Fatalf("server creation = %d: %s", confirmResponse.Code, confirmResponse.Body.String())
	}
	mu.Lock()
	gotRequests := append([]agentRequest(nil), requests...)
	gotMapPublished := mapPublished
	mu.Unlock()
	var provision agentRequest
	var operations []string
	for _, request := range gotRequests {
		operations = append(operations, request.Operation)
		if request.Operation == "provision" {
			provision = request
		}
	}
	if got := strings.Join(operations, ","); got != "provision,backup,world_analysis,world_map" {
		t.Fatalf("agent operations = %q, want provision,backup,world_analysis,world_map", got)
	}
	if provision.World != "PortalTestWorld" || provision.Port != 26000 || provision.Password != "SafePass-123" || !provision.Start || !provision.Public || !provision.Crossplay {
		t.Fatalf("agent provision request = %#v", provision)
	}
	if !gotMapPublished {
		t.Fatal("automatic map was not published")
	}
	world, err := server.store.PublicWorld(t.Context(), "PortalTestWorld")
	if err != nil {
		t.Fatal(err)
	}
	if !world.Enabled || world.Status != "online" || world.JoinAddress != "valheim.example.test:26000" {
		t.Fatalf("published world = %#v", world)
	}
	snapshots, err := server.store.LatestWorldAnalyses(t.Context(), "PortalTestWorld", 1)
	if err != nil || len(snapshots) != 1 || snapshots[0].Seed != "AutomaticMap" {
		t.Fatalf("automatic map snapshot = %#v, err=%v", snapshots, err)
	}
	jobs, err := server.store.RecentJobs(t.Context(), 10)
	if err != nil {
		t.Fatal(err)
	}
	for _, job := range jobs {
		if strings.Contains(job.Detail, "SafePass-123") {
			t.Fatal("job history stored the server password")
		}
	}
}

func TestInitialMapFailureLeavesStartedWorldUnpublished(t *testing.T) {
	server := testServer(t)
	serveMockAgent(t, server, func(w http.ResponseWriter, r *http.Request) {
		var request agentRequest
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			http.Error(w, "bad request", http.StatusBadRequest)
			return
		}
		if request.Operation != "backup" {
			http.Error(w, "unexpected operation", http.StatusBadRequest)
			return
		}
		_ = json.NewEncoder(w).Encode(AgentReply{Status: "failed", Error: "save pair unavailable"})
	})
	if err := server.store.CreateProvisionedWorld(t.Context(), PublicWorld{Name: "MapFailure", JoinAddress: "valheim.example.test:26000", Status: "online", ServerVersion: "unknown"}, "operator"); err != nil {
		t.Fatal(err)
	}
	failure := server.ensureInitialWorldMap(t.Context(), "MapFailure", "operator")
	if failure == nil || failure.status != http.StatusConflict || failure.client != "unable to create the first complete world backup" {
		t.Fatalf("initial map failure = %#v", failure)
	}
	world, err := server.store.PublicWorld(t.Context(), "MapFailure")
	if err != nil {
		t.Fatal(err)
	}
	if world.Enabled {
		t.Fatal("world was published despite automatic map failure")
	}
	jobs, err := server.store.RecentJobs(t.Context(), 1)
	if err != nil || len(jobs) != 1 || jobs[0].Status != "failed" || jobs[0].Detail != "initial backup failed" {
		t.Fatalf("map failure job = %#v, err=%v", jobs, err)
	}
}

func TestFirstPortalStartGeneratesDeferredWorldMap(t *testing.T) {
	server := testServer(t)
	var operations []string
	server.mapPublisher = func(ctx context.Context, snapshot worldintel.Snapshot, actor string) *worldAnalysisFailure {
		if err := server.store.SaveWorldAnalysis(ctx, snapshot, actor); err != nil {
			return &worldAnalysisFailure{status: http.StatusInternalServerError, client: "unable to persist analysis", detail: "analysis persistence failed"}
		}
		return nil
	}
	serveMockAgent(t, server, func(w http.ResponseWriter, r *http.Request) {
		var request agentRequest
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			http.Error(w, "bad request", http.StatusBadRequest)
			return
		}
		operations = append(operations, request.Operation)
		w.Header().Set("Content-Type", "application/json")
		switch request.Operation {
		case "start", "backup", "world_map":
			_ = json.NewEncoder(w).Encode(AgentReply{Status: "succeeded"})
		case "world_analysis":
			_ = json.NewEncoder(w).Encode(AgentReply{Status: "succeeded", Data: json.RawMessage(`{"schema":1,"world":"DeferredMap","seed":"DeferredSeed","world_version":37,"source":{"backup":"world-DeferredMap-first.tgz","sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}}`)})
		default:
			http.Error(w, "unexpected operation", http.StatusBadRequest)
		}
	})
	if err := server.store.CreateProvisionedWorld(t.Context(), PublicWorld{Name: "DeferredMap", JoinAddress: "valheim.example.test:26000", Status: "offline", ServerVersion: "unknown"}, "operator"); err != nil {
		t.Fatal(err)
	}
	form := url.Values{"world": {"DeferredMap"}, "operation": {"start"}}
	request := httptest.NewRequest(http.MethodPost, "/admin/jobs", strings.NewReader(form.Encode()))
	request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	request.Header.Set("X-Portal-Actor", "operator")
	response := httptest.NewRecorder()
	server.runJob(response, request)
	if response.Code != http.StatusSeeOther {
		t.Fatalf("start with automatic map = %d: %s", response.Code, response.Body.String())
	}
	if got := strings.Join(operations, ","); got != "start,backup,world_analysis,world_map" {
		t.Fatalf("deferred map operations = %q, want start,backup,world_analysis,world_map", got)
	}
	snapshots, err := server.store.LatestWorldAnalyses(t.Context(), "DeferredMap", 1)
	if err != nil || len(snapshots) != 1 || snapshots[0].Seed != "DeferredSeed" {
		t.Fatalf("deferred map snapshot = %#v, err=%v", snapshots, err)
	}
}

func serveMockAgent(t *testing.T, server *Server, handler http.HandlerFunc) {
	t.Helper()
	socket := server.agent.socket
	_ = os.Remove(socket)
	listener, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatal(err)
	}
	mock := &http.Server{Handler: handler}
	go func() { _ = mock.Serve(listener) }()
	t.Cleanup(func() {
		_ = mock.Close()
		_ = listener.Close()
	})
}

func adminTestRequest(method, target string, body *strings.Reader) *http.Request {
	var request *http.Request
	if body == nil {
		request = httptest.NewRequest(method, target, nil)
	} else {
		request = httptest.NewRequest(method, target, body)
	}
	request.RemoteAddr = "192.0.2.10:1234"
	request.Header.Set("X-Forwarded-User", "operator")
	request.Header.Set(adminTokenHeader, testAdminToken)
	return request
}
