package app

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

// Every route under /admin, enforced as a set rather than one test per handler.
//
// 32 of the 49 admin routes were named in no test at all, and the guards they share - admin
// authentication, a valid CSRF token on writes, a validated world name - are exactly the kind of
// thing that is wired correctly 48 times and forgotten once. Reading the routes out of the source
// means a new route joins these checks by existing, not by someone remembering to add it.

var routePattern = regexp.MustCompile(`HandleFunc\("(GET|POST) (/admin[^"]*)",\s*s\.(?:admin|adminUpload)\(s\.(\w+)\)`)

type adminRoute struct {
	method string
	path   string
	// handler is the method name the route is registered with, which is how a test can ask what
	// the handler actually validates instead of assuming.
	handler string
}

// client returns a distinct address per request. The portal rate-limits by forwarded client, and
// forty requests a minute from one address answers 429 - a refusal, but not the one being tested.
func client(n int) string {
	return fmt.Sprintf("198.51.100.%d", 1+n%250)
}

// adminRoutes reads the registrations out of server.go. A route registered without s.admin or
// s.adminUpload does not match, which is itself the point: TestEveryAdminRouteIsGuarded fails when
// the counts disagree.
func adminRoutes(t *testing.T) []adminRoute {
	t.Helper()
	source, err := os.ReadFile(filepath.Join("server.go"))
	if err != nil {
		t.Fatal(err)
	}
	var routes []adminRoute
	for _, match := range routePattern.FindAllStringSubmatch(string(source), -1) {
		routes = append(routes, adminRoute{method: match[1], path: match[2], handler: match[3]})
	}
	if len(routes) < 40 {
		t.Fatalf("only %d admin routes found; the pattern has drifted from server.go", len(routes))
	}
	return routes
}

// concrete replaces path wildcards with values that are valid in shape, so a rejection is about
// authentication or CSRF rather than a malformed identifier.
func concrete(path string) string {
	replacements := map[string]string{
		"{id}": "abc123", "{world}": "TestWorld", "{profile}": "redesign-alpha",
		"{steam_id}": testSteamID, "{name}": "TestWorld", "{version}": "1.0.0",
	}
	for placeholder, value := range replacements {
		path = strings.ReplaceAll(path, placeholder, value)
	}
	// Anything still wrapped in braces gets a plain token rather than being left literal.
	return regexp.MustCompile(`\{[^}]+\}`).ReplaceAllString(path, "placeholder")
}

func TestEveryAdminRouteIsGuarded(t *testing.T) {
	source, err := os.ReadFile("server.go")
	if err != nil {
		t.Fatal(err)
	}
	all := regexp.MustCompile(`HandleFunc\("(GET|POST) (/admin[^"]*)"`).FindAllStringSubmatch(string(source), -1)
	guarded := routePattern.FindAllStringSubmatch(string(source), -1)
	if len(all) != len(guarded) {
		guardedPaths := map[string]bool{}
		for _, match := range guarded {
			guardedPaths[match[1]+" "+match[2]] = true
		}
		var missing []string
		for _, match := range all {
			if key := match[1] + " " + match[2]; !guardedPaths[key] {
				missing = append(missing, key)
			}
		}
		t.Fatalf("admin routes registered without s.admin/s.adminUpload: %v", missing)
	}
}

// Without admin authentication every admin route must refuse. A page that renders for an
// unauthenticated request leaks the control surface; a POST that acts on one is worse.
func TestNoAdminRouteAnswersWithoutAuthentication(t *testing.T) {
	server := testServer(t)
	for index, route := range adminRoutes(t) {
		target := concrete(route.path)
		request := httptest.NewRequest(route.method, target, strings.NewReader(""))
		request.RemoteAddr = "192.0.2.10:1234"
		request.Header.Set("X-Forwarded-For", client(index))
		if route.method == http.MethodPost {
			request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
		}
		response := httptest.NewRecorder()
		server.Handler().ServeHTTP(response, request)
		if response.Code != http.StatusUnauthorized {
			t.Errorf("%s %s answered %d without admin auth, want 401", route.method, target, response.Code)
		}
	}
}

// The proxy route demands the admin token as well as an identity header. An identity alone is what
// a misconfigured proxy forwards.
func TestNoAdminRouteAnswersWithAnIdentityButNoAdminToken(t *testing.T) {
	server := testServer(t)
	for index, route := range adminRoutes(t) {
		target := concrete(route.path)
		request := httptest.NewRequest(route.method, target, strings.NewReader(""))
		request.RemoteAddr = "192.0.2.10:1234"
		request.Header.Set("X-Forwarded-For", client(index))
		request.Header.Set(server.cfg.AuthHeader, "someone")
		if route.method == http.MethodPost {
			request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
		}
		response := httptest.NewRecorder()
		server.Handler().ServeHTTP(response, request)
		if response.Code != http.StatusUnauthorized {
			t.Errorf("%s %s answered %d with an identity but no admin token, want 401", route.method, target, response.Code)
		}
	}
}

// Every admin write must reject a missing or wrong CSRF token. This is the check that stops a page
// on another origin from driving the control surface through an operator's browser.
func TestEveryAdminWriteRejectsABadCSRFToken(t *testing.T) {
	server := testServer(t)
	for index, route := range adminRoutes(t) {
		if route.method != http.MethodPost {
			continue
		}
		target := concrete(route.path)
		for _, name := range []string{"absent", "wrong"} {
			body := "world=TestWorld"
			if name == "wrong" {
				body += "&csrf=" + strings.Repeat("f", 64)
			}
			request := httptest.NewRequest(http.MethodPost, target, strings.NewReader(body))
			request.RemoteAddr = "192.0.2.10:1234"
			request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
			request.Header.Set(server.cfg.AuthHeader, "admin")
			request.Header.Set(adminTokenHeader, testAdminToken)
			request.Header.Set("X-Forwarded-For", client(index))
			if name == "wrong" {
				request.AddCookie(&http.Cookie{Name: "portal_csrf", Value: strings.Repeat("a", 64), Path: "/admin"})
			}
			response := httptest.NewRecorder()
			server.Handler().ServeHTTP(response, request)
			switch response.Code {
			case http.StatusForbidden, http.StatusBadRequest:
				// Refused, which is the requirement.
			default:
				t.Errorf("POST %s answered %d with a %s CSRF token; a write must refuse", target, response.Code, name)
			}
		}
	}
}

// A world name arriving from a form reaches shell scripts and filesystem paths, so every route that
// takes one must validate it. Two shapes: traversal, and a name with a shell metacharacter.
// validatesWorld reports whether a handler reads and validates a world name, read out of the
// package source. A route that never looks at the field cannot be expected to reject it.
func validatesWorld(t *testing.T, handler string) bool {
	t.Helper()
	entries, err := os.ReadDir(".")
	if err != nil {
		t.Fatal(err)
	}
	for _, entry := range entries {
		if !strings.HasSuffix(entry.Name(), ".go") || strings.HasSuffix(entry.Name(), "_test.go") {
			continue
		}
		source, err := os.ReadFile(entry.Name())
		if err != nil {
			continue
		}
		text := string(source)
		start := strings.Index(text, "func (s *Server) "+handler+"(")
		if start < 0 {
			continue
		}
		end := strings.Index(text[start:], "\nfunc ")
		body := text[start:]
		if end > 0 {
			body = body[:end]
		}
		return strings.Contains(body, "validWorld(")
	}
	return false
}

func TestAdminRoutesTakingAWorldRejectAMalformedOne(t *testing.T) {
	server := testServer(t)
	nonce := strings.Repeat("a", 64)
	checked := 0
	for index, route := range adminRoutes(t) {
		if route.method != http.MethodPost || !validatesWorld(t, route.handler) {
			continue
		}
		checked++
		for _, bad := range []string{"../../etc/passwd", "Test World; rm -rf /", strings.Repeat("w", 200)} {
			body := "world=" + bad + "&csrf=" + server.csrfToken(nonce)
			request := httptest.NewRequest(http.MethodPost, concrete(route.path), strings.NewReader(body))
			request.RemoteAddr = "192.0.2.10:1234"
			request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
			request.Header.Set(server.cfg.AuthHeader, "admin")
			request.Header.Set(adminTokenHeader, testAdminToken)
			request.AddCookie(&http.Cookie{Name: "portal_csrf", Value: nonce, Path: "/admin"})
			request.Header.Set("X-Forwarded-For", client(index))
			response := httptest.NewRecorder()
			server.Handler().ServeHTTP(response, request)
			// A 2xx or a redirect would mean the malformed name was accepted and acted upon.
			if response.Code >= 200 && response.Code < 400 {
				t.Errorf("POST %s (%s) accepted world=%q with %d", concrete(route.path), route.handler, bad, response.Code)
			}
		}
	}
	if checked == 0 {
		t.Fatal("no admin route was found to validate a world; the source scan has drifted")
	}
	t.Logf("checked %d world-taking admin routes", checked)
}

// The admin pages themselves must render for an authenticated operator. A 500 here is a template
// that only breaks in production, which is precisely what nobody notices until an operator looks.
// downloadRoutes stream a file rather than render a page. When the host agent is unreachable they
// answer 503 on purpose: a 200 carrying an error message would save to disk as a "log" whose
// contents are an apology, which is worse than a failed download.
var downloadRoutes = map[string]bool{"/admin/worlds/{world}/log.txt": true}

func TestEveryAdminPageRendersForAnOperator(t *testing.T) {
	server := testServer(t)
	for index, route := range adminRoutes(t) {
		if route.method != http.MethodGet {
			continue
		}
		target := concrete(route.path)
		request := httptest.NewRequest(http.MethodGet, target, nil)
		request.RemoteAddr = "192.0.2.10:1234"
		request.Header.Set(server.cfg.AuthHeader, "admin")
		request.Header.Set(adminTokenHeader, testAdminToken)
		request.Header.Set("X-Forwarded-For", client(index))
		response := httptest.NewRecorder()
		server.Handler().ServeHTTP(response, request)
		switch {
		case response.Code == http.StatusInternalServerError:
			t.Errorf("GET %s answered 500: %s", target, strings.TrimSpace(response.Body.String()))
		case response.Code == http.StatusServiceUnavailable && downloadRoutes[route.path]:
			// Documented above: a download with no upstream must fail, not succeed emptily.
		case response.Code >= 500:
			t.Errorf("GET %s answered %d", target, response.Code)
		}
		// 404 and 303 are legitimate for routes keyed by an id that does not exist here.
	}
}
