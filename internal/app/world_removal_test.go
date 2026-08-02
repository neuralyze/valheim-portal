package app

import (
	"net/http"
	"net/http/httptest"
	"net/url"
	"regexp"
	"strings"
	"testing"
)

const removalWorld = "RemovalTest"

func setupRemovalWorld(t *testing.T, server *Server) Release {
	t.Helper()
	if err := server.store.UpsertPublicWorld(t.Context(), PublicWorld{
		Name: removalWorld, JoinAddress: "valheim.example:27500", Status: "online", ServerVersion: "test",
	}, "test"); err != nil {
		t.Fatal(err)
	}
	release := Release{ID: "removal-release", World: removalWorld, Profile: "flat", ClientType: "flat", Version: "1.0.0", Notes: "removal test"}
	publishProfile(t, server, release)
	if err := server.store.GrantWorldAccess(t.Context(), removalWorld, testSteamID, "test"); err != nil {
		t.Fatal(err)
	}
	return release
}

func removalPage(t *testing.T, server *Server) (string, *http.Cookie) {
	t.Helper()
	request := adminTestRequest(http.MethodGet, "/admin/worlds/"+removalWorld+"/remove", nil)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("removal page = %d: %s", response.Code, response.Body.String())
	}
	for _, want := range []string{"UNREGISTER " + removalWorld, "DELETE " + removalWorld, "final world-save backup", "External backups"} {
		if !strings.Contains(response.Body.String(), want) {
			t.Fatalf("removal page missing %q: %s", want, response.Body.String())
		}
	}
	match := regexp.MustCompile(`name="csrf" value="([^"]+)"`).FindStringSubmatch(response.Body.String())
	cookies := response.Result().Cookies()
	if len(match) != 2 || len(cookies) != 1 {
		t.Fatal("removal page did not issue CSRF credentials")
	}
	return match[1], cookies[0]
}

func postRemoval(t *testing.T, server *Server, csrf string, cookie *http.Cookie, mode, confirmation string) *httptest.ResponseRecorder {
	t.Helper()
	form := url.Values{"csrf": {csrf}, "mode": {mode}, "confirmation": {confirmation}}
	request := adminTestRequest(http.MethodPost, "/admin/worlds/"+removalWorld+"/remove", strings.NewReader(form.Encode()))
	request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	request.AddCookie(cookie)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	return response
}

func TestWorldRemovalRejectsInexactTypedConfirmation(t *testing.T) {
	server := testServer(t)
	setupRemovalWorld(t, server)
	csrf, cookie := removalPage(t, server)
	response := postRemoval(t, server, csrf, cookie, "delete", "DELETE WRONG")
	if response.Code != http.StatusBadRequest {
		t.Fatalf("inexact confirmation = %d: %s", response.Code, response.Body.String())
	}
	if _, err := server.store.PublicWorld(t.Context(), removalWorld); err != nil {
		t.Fatalf("world changed after rejected confirmation: %v", err)
	}
}

func TestWorldUnregisterRevokesAccessButRetainsRelease(t *testing.T) {
	server := testServer(t)
	release := setupRemovalWorld(t, server)
	csrf, cookie := removalPage(t, server)
	response := postRemoval(t, server, csrf, cookie, "unregister", "UNREGISTER "+removalWorld)
	if response.Code != http.StatusSeeOther {
		t.Fatalf("unregister = %d: %s", response.Code, response.Body.String())
	}
	if _, err := server.store.PublicWorld(t.Context(), removalWorld); err == nil {
		t.Fatal("unregistered world remains public")
	}
	if allowed, err := server.store.CanAccessWorld(t.Context(), removalWorld, testSteamID); err != nil || allowed {
		t.Fatalf("unregistered access = %t, %v", allowed, err)
	}
	stored, err := server.store.Release(t.Context(), release.ID)
	if err != nil || stored.Status != Published {
		t.Fatalf("unregistered release = %#v, %v", stored, err)
	}
}

func TestPermanentWorldDeletionArchivesReleaseAfterAgentSuccess(t *testing.T) {
	server := testServer(t)
	release := setupRemovalWorld(t, server)
	draft := Release{ID: "removal-draft", World: removalWorld, Profile: "draft", ClientType: "flat", Version: "1.0.0", Notes: "unpublished"}
	if err := server.store.CreateRelease(t.Context(), draft, "test"); err != nil {
		t.Fatal(err)
	}
	serveAgentReply(t, server, "delete_server", removalWorld, AgentReply{Status: "succeeded", Output: "final backup created; server stopped; directory deleted"})
	csrf, cookie := removalPage(t, server)
	response := postRemoval(t, server, csrf, cookie, "delete", "DELETE "+removalWorld)
	if response.Code != http.StatusSeeOther {
		t.Fatalf("permanent deletion = %d: %s", response.Code, response.Body.String())
	}
	if _, err := server.store.PublicWorld(t.Context(), removalWorld); err == nil {
		t.Fatal("deleted world remains public")
	}
	if allowed, err := server.store.CanAccessWorld(t.Context(), removalWorld, testSteamID); err != nil || allowed {
		t.Fatalf("deleted world access = %t, %v", allowed, err)
	}
	stored, err := server.store.Release(t.Context(), release.ID)
	if err != nil || stored.Status != Archived {
		t.Fatalf("deleted world release = %#v, %v", stored, err)
	}
	storedDraft, err := server.store.Release(t.Context(), draft.ID)
	if err != nil || storedDraft.Status != Draft {
		t.Fatalf("deleted world draft = %#v, %v", storedDraft, err)
	}
	jobs, err := server.store.RecentJobs(t.Context(), 10)
	if err != nil || len(jobs) != 1 || jobs[0].Operation != "delete_server" || jobs[0].Status != "succeeded" {
		t.Fatalf("deletion jobs = %#v, %v", jobs, err)
	}
}

func TestPermanentWorldDeletionFailureRetainsDisabledRegistration(t *testing.T) {
	server := testServer(t)
	release := setupRemovalWorld(t, server)
	serveAgentReply(t, server, "delete_server", removalWorld, AgentReply{Status: "failed", Error: "operation failed", Output: "final backup failed"})
	csrf, cookie := removalPage(t, server)
	response := postRemoval(t, server, csrf, cookie, "delete", "DELETE "+removalWorld)
	if response.Code != http.StatusConflict {
		t.Fatalf("failed deletion = %d: %s", response.Code, response.Body.String())
	}
	world, err := server.store.PublicWorld(t.Context(), removalWorld)
	if err != nil || world.Enabled {
		t.Fatalf("failed deletion world = %#v, %v", world, err)
	}
	stored, err := server.store.Release(t.Context(), release.ID)
	if err != nil || stored.Status != Published {
		t.Fatalf("failed deletion release = %#v, %v", stored, err)
	}
}
