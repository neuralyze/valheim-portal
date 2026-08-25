package app

import (
	"archive/zip"
	"bytes"
	"encoding/json"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"testing"
)

// worldSourceForm is the server-creation form with every field except the world source
// filled in, so each case below differs only in the source it selects.
func worldSourceForm(csrf string) map[string]string {
	return map[string]string{
		"csrf": csrf, "world": "UploadTestWorld", "server_name": "Neuralyze Upload Test",
		"password": "SafePass-123", "password_confirm": "SafePass-123", "port": "26100",
		"player_limit": "10", "backup_age": "7", "backup_count": "168", "profile": "default",
		"preset": "Normal", "backup_interval": "1h", "join_host": "valheim.example.test",
	}
}

func multipartReview(t *testing.T, fields map[string]string, archive []byte) (string, string) {
	t.Helper()
	var body bytes.Buffer
	form := multipart.NewWriter(&body)
	for name, value := range fields {
		if err := form.WriteField(name, value); err != nil {
			t.Fatal(err)
		}
	}
	if archive != nil {
		part, err := form.CreateFormFile("world_archive", "save.zip")
		if err != nil {
			t.Fatal(err)
		}
		if _, err := part.Write(archive); err != nil {
			t.Fatal(err)
		}
	}
	if err := form.Close(); err != nil {
		t.Fatal(err)
	}
	return body.String(), form.FormDataContentType()
}

func newServerCredentials(t *testing.T, server *Server) (string, *http.Cookie) {
	t.Helper()
	request := adminTestRequest(http.MethodGet, "/admin/servers/new", nil)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("new server page = %d", response.Code)
	}
	match := regexp.MustCompile(`name="csrf" value="([^"]+)"`).FindStringSubmatch(response.Body.String())
	cookies := response.Result().Cookies()
	if len(match) != 2 || len(cookies) == 0 {
		t.Fatal("wizard issued no CSRF credentials")
	}
	return match[1], cookies[0]
}

func postReview(t *testing.T, server *Server, cookie *http.Cookie, fields map[string]string, archive []byte) *httptest.ResponseRecorder {
	t.Helper()
	body, contentType := multipartReview(t, fields, archive)
	request := adminTestRequest(http.MethodPost, "/admin/servers/review", strings.NewReader(body))
	request.Header.Set("Content-Type", contentType)
	request.AddCookie(cookie)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	return response
}

// playerSaveArchive is the shape a player actually uploads: the live pair plus one of the
// automatic backup pairs Valheim writes beside it.
func playerSaveArchive(t *testing.T) []byte {
	t.Helper()
	var buffer bytes.Buffer
	writer := zip.NewWriter(&buffer)
	for _, item := range []struct {
		name string
		data []byte
	}{
		{"worlds_local/Hrafnheim.db", database(4096)},
		{"worlds_local/Hrafnheim.fwl", realWorldMetadata},
		{"worlds_local/Hrafnheim_backup_auto-20260825.031100.db", database(2048)},
		{"worlds_local/Hrafnheim_backup_auto-20260825.031100.fwl", realWorldMetadata},
	} {
		part, err := writer.Create(item.name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := part.Write(item.data); err != nil {
			t.Fatal(err)
		}
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	return buffer.Bytes()
}

// The switch is one exclusive choice, and a field belonging to an unselected source is
// refused. Dropping it instead would build the operator a different world than the form
// showed them, with nothing in the result recording the substitution.
func TestWorldSourceRefusesFieldsBelongingToAnUnselectedSource(t *testing.T) {
	server := testServer(t)
	csrf, cookie := newServerCredentials(t, server)
	for _, testCase := range []struct {
		name    string
		mode    string
		seed    string
		source  string
		archive []byte
		want    string
	}{
		{name: "seed typed then random chosen", mode: "random", seed: "Midgard01", want: "supplied a seed"},
		{name: "seed typed then upload chosen", mode: "upload", seed: "Midgard01", want: "supplied a seed"},
		{name: "archive attached then random chosen", mode: "random", archive: playerSaveArchive(t), want: "supplied a world archive"},
		{name: "archive attached then seed chosen", mode: "seed", seed: "Midgard01", archive: playerSaveArchive(t), want: "supplied a world archive"},
		{name: "source world picked then random chosen", mode: "random", source: "Hrafnheim", want: "supplied a source world"},
		{name: "upload chosen with no archive", mode: "upload", want: "one world's .db and .fwl save pair"},
		{name: "no source chosen at all", mode: "", want: "select a world source"},
	} {
		t.Run(testCase.name, func(t *testing.T) {
			fields := worldSourceForm(csrf)
			fields["world_mode"] = testCase.mode
			if testCase.seed != "" {
				fields["seed"] = testCase.seed
			}
			if testCase.source != "" {
				fields["source_world"] = testCase.source
			}
			response := postReview(t, server, cookie, fields, testCase.archive)
			if response.Code != http.StatusBadRequest {
				t.Fatalf("response = %d, want 400: %s", response.Code, response.Body.String())
			}
			if !strings.Contains(response.Body.String(), testCase.want) {
				t.Fatalf("body %q does not contain %q", strings.TrimSpace(response.Body.String()), testCase.want)
			}
		})
	}
}

// Control for the refusals above: the same form with a source selected and only that
// source's field populated is accepted, so the gate is not simply rejecting everything.
func TestWorldSourceAcceptsTheFieldBelongingToTheSelectedSource(t *testing.T) {
	server := testServer(t)
	csrf, cookie := newServerCredentials(t, server)
	fields := worldSourceForm(csrf)
	fields["world_mode"] = "seed"
	fields["seed"] = "Midgard01"
	if response := postReview(t, server, cookie, fields, nil); response.Code != http.StatusOK {
		t.Fatalf("seed source = %d: %s", response.Code, response.Body.String())
	}
}

// The whole upload path through the admin surface: the archive is staged on disk, the
// review page names the world the archive actually carries, and the agent is handed a
// staging id rather than any part of the save.
func TestUploadedWorldReachesTheAgentAsAStagingID(t *testing.T) {
	server := testServer(t)
	var mu sync.Mutex
	var provision agentRequest
	serveMockAgent(t, server, func(w http.ResponseWriter, r *http.Request) {
		var request agentRequest
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			http.Error(w, "bad request", http.StatusBadRequest)
			return
		}
		mu.Lock()
		if request.Operation == "provision" {
			provision = request
		}
		mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		if request.Operation == "provision" {
			_ = json.NewEncoder(w).Encode(AgentReply{Status: "succeeded", Output: "provisioned", Provisioned: true})
			return
		}
		_ = json.NewEncoder(w).Encode(AgentReply{Status: "succeeded"})
	})
	csrf, cookie := newServerCredentials(t, server)
	fields := worldSourceForm(csrf)
	fields["world_mode"] = "upload"
	response := postReview(t, server, cookie, fields, playerSaveArchive(t))
	if response.Code != http.StatusOK {
		t.Fatalf("review = %d: %s", response.Code, response.Body.String())
	}
	page := response.Body.String()
	for _, want := range []string{
		"Uploaded archive carrying world <b>Hrafnheim</b>",
		"qmrbecQI2K",
		"CREATE UploadTestWorld FROM Hrafnheim",
		"Hrafnheim_backup_auto-20260825.031100.db",
		"cannot be mistaken for the current world",
	} {
		if !strings.Contains(page, want) {
			t.Fatalf("review page omits %q", want)
		}
	}

	// Staged on disk under the spool, not carried in the review token.
	entries, err := os.ReadDir(server.cfg.WorldUploadRoot)
	if err != nil || len(entries) != 1 {
		t.Fatalf("upload root entries = %v, err = %v", entries, err)
	}
	staged := filepath.Join(server.cfg.WorldUploadRoot, entries[0].Name())
	placed, err := os.ReadFile(filepath.Join(staged, stagedWorldMetadataName))
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(placed, realWorldMetadata) {
		t.Fatal("staged .fwl does not match the uploaded one")
	}

	id := regexp.MustCompile(`/admin/servers/([a-f0-9]+)`).FindStringSubmatch(page)
	if len(id) != 2 {
		t.Fatal("review page carried no creation request id")
	}
	// The phrase names the uploaded world, so a page prepared for a different archive
	// cannot be confirmed from muscle memory.
	stale := confirmProvision(t, server, cookie, id[1], "csrf="+csrf+"&confirmation=CREATE+UploadTestWorld")
	if stale != http.StatusBadRequest {
		t.Fatalf("confirmation omitting the uploaded world name = %d, want 400", stale)
	}
	done := confirmProvision(t, server, cookie, id[1], "csrf="+csrf+"&confirmation=CREATE+UploadTestWorld+FROM+Hrafnheim")
	if done != http.StatusSeeOther {
		t.Fatalf("creation = %d", done)
	}

	mu.Lock()
	got := provision
	mu.Unlock()
	if got.WorldUpload != entries[0].Name() {
		t.Fatalf("agent received world_upload %q, want the staging id %q", got.WorldUpload, entries[0].Name())
	}
	if got.Seed != "" || got.SourceWorld != "" {
		t.Fatalf("agent received a second world source: seed %q, source world %q", got.Seed, got.SourceWorld)
	}
	// The save must never travel over the socket: internal/agent caps a payload at
	// 32 MiB and a real world save is far larger than this fixture.
	body, err := json.Marshal(got)
	if err != nil {
		t.Fatal(err)
	}
	if len(body) > 4096 {
		t.Fatalf("provision request is %d bytes; the save is travelling through the agent", len(body))
	}
}

func confirmProvision(t *testing.T, server *Server, cookie *http.Cookie, id, form string) int {
	t.Helper()
	request := adminTestRequest(http.MethodPost, "/admin/servers/"+id, strings.NewReader(form))
	request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	request.AddCookie(cookie)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	return response.Code
}
