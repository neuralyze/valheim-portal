package main

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// A minimal thing that looks enough like a Windows executable to be accepted.
func fakeExecutable(body string) []byte { return append([]byte("MZ"), body...) }

func writeExecutable(t *testing.T, path string, payload []byte) {
	t.Helper()
	if err := os.WriteFile(path, payload, 0o700); err != nil {
		t.Fatal(err)
	}
}

func readFile(t *testing.T, path string) string {
	t.Helper()
	payload, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	return string(payload)
}

type recordedProgress struct{ updates []progressUpdate }

func (log *recordedProgress) report(update progressUpdate) { log.updates = append(log.updates, update) }

func (log *recordedProgress) text() string {
	var out strings.Builder
	for _, update := range log.updates {
		out.WriteString(update.Stage + ": " + update.Detail + "\n")
	}
	return out.String()
}

func (log *recordedProgress) stage(name string) bool {
	for _, update := range log.updates {
		if update.Stage == name {
			return true
		}
	}
	return false
}

// The decision is the whole safety argument, so it is pinned as a table rather than
// inferred from a run. Identity, never recency: there is no ordering in a git describe,
// and inventing one is how a client ends up refusing the build its portal is serving.
func TestClientUpdateDecisionComparesIdentity(t *testing.T) {
	const running = "1111111111111111111111111111111111111111111111111111111111111111"
	const published = "2222222222222222222222222222222222222222222222222222222222222222"
	good := clientBuild{Version: "v1.0.1-301-g2b1fc1f", SHA256: published, Size: 16649728}

	for _, testCase := range []struct {
		name       string
		running    string
		remote     clientBuild
		attempt    string
		want       updateVerdict
		wantErr    bool
		errorNames string
	}{
		{name: "matching bytes are current", running: published, remote: good, want: updateCurrent},
		{name: "digest case is not identity", running: strings.ToUpper(published), remote: good, want: updateCurrent},
		{name: "different bytes update", running: running, remote: good, want: updateAvailable},
		{
			// The player who runs a newer build by hand against an older portal. The
			// portal is the authority on what its players run, so this converges on the
			// portal's build in one swap rather than oscillating.
			name: "an unrecognised local build is replaced by the published one", running: running,
			remote: clientBuild{Version: "v1.0.0-1-gaaaaaaa", SHA256: published, Size: 1024}, want: updateAvailable,
		},
		{name: "a target already attempted is not attempted again", running: running, remote: good, attempt: published, want: updateAlreadyAttempted},
		{name: "attempt records are compared case-insensitively", running: running, remote: good, attempt: strings.ToUpper(published) + "\n", want: updateAlreadyAttempted},
		{name: "an unrelated attempt record does not block", running: running, remote: good, attempt: running, want: updateAvailable},
		{name: "a malformed digest is refused", running: running, remote: clientBuild{SHA256: "nope", Size: 10}, wantErr: true},
		{name: "a zero size is refused", running: running, remote: clientBuild{SHA256: published, Size: 0}, wantErr: true},
		{name: "an absurd size is refused", running: running, remote: clientBuild{SHA256: published, Size: maxClientDownload + 1}, wantErr: true},
		{name: "a control character in the version is refused", running: running, remote: clientBuild{Version: "v1\x00", SHA256: published, Size: 10}, wantErr: true},
		{name: "an unidentifiable running copy never updates", running: "", remote: good, wantErr: true},
	} {
		t.Run(testCase.name, func(t *testing.T) {
			verdict, err := decideClientUpdate(testCase.running, testCase.remote, testCase.attempt)
			if testCase.wantErr {
				if err == nil {
					t.Fatalf("verdict %d accepted without error", verdict)
				}
				if verdict != updateCurrent {
					t.Fatalf("a refused answer must not ask for an update, got %d", verdict)
				}
				return
			}
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if verdict != testCase.want {
				t.Fatalf("verdict = %d, want %d", verdict, testCase.want)
			}
		})
	}
}

// The swap sequence, against a real filesystem: the running image is renamed aside rather
// than overwritten, because Windows will not let a mapped image be written, and the
// canonical path ends up holding the new bytes.
func TestSelfUpdateSwapRenamesTheRunningImageAside(t *testing.T) {
	directory := t.TempDir()
	canonical := filepath.Join(directory, "ValheimProfileSync.exe")
	writeExecutable(t, canonical, fakeExecutable("old"))
	updater := &selfUpdater{Executable: canonical, Now: func() time.Time { return time.Unix(0, 7) }}

	if err := updater.swap(fakeExecutable("new")); err != nil {
		t.Fatalf("swap: %v", err)
	}
	if got := readFile(t, canonical); got != string(fakeExecutable("new")) {
		t.Fatalf("canonical path holds %q", got)
	}
	retired := canonical + retiredSuffix + "7"
	if got := readFile(t, retired); got != string(fakeExecutable("old")) {
		t.Fatalf("the retired copy holds %q", got)
	}
	// The staged copy must not survive: it would be swept later, but a leftover
	// half-name beside an executable is exactly the litter this must not produce.
	if _, err := os.Stat(canonical + incomingSuffix + "7"); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("a staged copy was left behind: %v", err)
	}

	// And the retired copy is cleaned up by a LATER run, never this one - it is still
	// the mapped image of the running process at that moment.
	updater.sweepRetired()
	if _, err := os.Stat(retired); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("a later run did not sweep the retired copy: %v", err)
	}
	if got := readFile(t, canonical); got != string(fakeExecutable("new")) {
		t.Fatalf("the sweep damaged the canonical path: %q", got)
	}
}

// Every partial failure has to end with a working binary at the canonical path. This is
// the part no Windows machine is available to prove, so it is proven here against
// injected failures at each step.
func TestSelfUpdateSwapAlwaysLeavesAWorkingExecutable(t *testing.T) {
	old := string(fakeExecutable("old"))
	replacement := fakeExecutable("new")

	for _, testCase := range []struct {
		name      string
		configure func(updater *selfUpdater, canonical string)
		wantErr   bool
		wantBytes string
	}{
		{
			name: "the staging write fails",
			configure: func(updater *selfUpdater, _ string) {
				updater.WriteFile = func(string, []byte) error { return errors.New("disk full") }
			},
			wantErr: true, wantBytes: old,
		},
		{
			name: "the running image cannot be moved aside",
			configure: func(updater *selfUpdater, canonical string) {
				updater.Rename = func(from, to string) error {
					if from == canonical {
						return errors.New("access denied")
					}
					return os.Rename(from, to)
				}
			},
			wantErr: true, wantBytes: old,
		},
		{
			name: "the replacement cannot be moved into place and the old copy comes back",
			configure: func(updater *selfUpdater, canonical string) {
				updater.Rename = func(from, to string) error {
					if to == canonical && strings.Contains(from, incomingSuffix) {
						return errors.New("access denied")
					}
					return os.Rename(from, to)
				}
			},
			wantErr: true, wantBytes: old,
		},
		{
			name: "the replacement cannot be moved into place and neither can the old copy",
			configure: func(updater *selfUpdater, canonical string) {
				updater.Rename = func(from, to string) error {
					if to == canonical {
						return errors.New("access denied")
					}
					return os.Rename(from, to)
				}
			},
			// The verified bytes are written straight to the canonical path, so the
			// player still has a launcher - and it is the new one.
			wantErr: false, wantBytes: string(replacement),
		},
	} {
		t.Run(testCase.name, func(t *testing.T) {
			directory := t.TempDir()
			canonical := filepath.Join(directory, "ValheimProfileSync.exe")
			writeExecutable(t, canonical, fakeExecutable("old"))
			updater := &selfUpdater{Executable: canonical, Now: func() time.Time { return time.Unix(0, 11) }}
			testCase.configure(updater, canonical)

			err := updater.swap(replacement)
			if testCase.wantErr != (err != nil) {
				t.Fatalf("swap error = %v, wanted an error: %v", err, testCase.wantErr)
			}
			info, statErr := os.Stat(canonical)
			if statErr != nil {
				t.Fatalf("the canonical path is gone: %v", statErr)
			}
			if info.Size() == 0 {
				t.Fatal("the canonical path was left empty")
			}
			if got := readFile(t, canonical); got != testCase.wantBytes {
				t.Fatalf("canonical path holds %q, want %q", got, testCase.wantBytes)
			}
			if _, err := os.Stat(canonical + incomingSuffix + "11"); !errors.Is(err, os.ErrNotExist) {
				t.Fatalf("a staged copy was left behind: %v", err)
			}
		})
	}
}

// A portal for the full startup path: it publishes an identity and serves bytes, and each
// half can be made to misbehave independently.
type fakePortal struct {
	build   clientBuild
	payload []byte
	status  int
	body    string
	hang    chan struct{}
}

func (portal *fakePortal) start(t *testing.T) (*url.URL, *http.Client) {
	t.Helper()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/client/version":
			if portal.hang != nil {
				<-portal.hang
				return
			}
			if portal.status != 0 {
				w.WriteHeader(portal.status)
				_, _ = w.Write([]byte(portal.body))
				return
			}
			_ = json.NewEncoder(w).Encode(portal.build)
		case "/client/ValheimProfileSync.exe":
			_, _ = w.Write(portal.payload)
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	t.Cleanup(server.Close)
	base, err := url.Parse(server.URL)
	if err != nil {
		t.Fatal(err)
	}
	return base, server.Client()
}

type updateHarness struct {
	updater   *selfUpdater
	canonical string
	progress  *recordedProgress
	launched  []string
	launchErr error
}

func newUpdateHarness(t *testing.T, portal *fakePortal, installed []byte) *updateHarness {
	t.Helper()
	base, httpClient := portal.start(t)
	directory := t.TempDir()
	canonical := filepath.Join(directory, "ValheimProfileSync.exe")
	writeExecutable(t, canonical, installed)
	harness := &updateHarness{canonical: canonical, progress: &recordedProgress{}}
	harness.updater = &selfUpdater{
		Portal:     base,
		HTTPClient: httpClient,
		Executable: canonical,
		Args:       []string{"valheim-profile-sync://sync?portal=https%3A%2F%2Fportal.example&world=ulfsland&profile=admin&client_type=flat"},
		Progress:   harness.progress.report,
		Now:        func() time.Time { return time.Unix(0, 3) },
		Launch: func(executable string, args []string) error {
			harness.launched = append([]string{executable}, args...)
			return harness.launchErr
		},
	}
	return harness
}

// The end-to-end behaviour the operator is blocked on: a frozen installed copy notices it
// is not what the portal publishes, replaces itself, and re-opens the profile link the
// player actually clicked.
func TestSelfUpdateReplacesAStaleCopyAndReopensTheSameLink(t *testing.T) {
	replacement := fakeExecutable("the build the portal is serving")
	portal := &fakePortal{
		build:   clientBuild{Version: "v1.0.1-302-gabcdef0", SHA256: digestBytes(replacement), Size: int64(len(replacement))},
		payload: replacement,
	}
	harness := newUpdateHarness(t, portal, fakeExecutable("the build installed an hour ago"))

	if !harness.updater.apply(context.Background()) {
		t.Fatalf("a stale copy did not update:\n%s", harness.progress.text())
	}
	if got := readFile(t, harness.canonical); got != string(replacement) {
		t.Fatalf("the installed copy was not replaced: %q", got)
	}
	if len(harness.launched) != 2 || harness.launched[0] != harness.canonical || harness.launched[1] != harness.updater.Args[0] {
		t.Fatalf("the replacement was not started with the same arguments: %q", harness.launched)
	}
	log := harness.progress.text()
	for _, expected := range []string{"Updating Valheim Profile Sync", "v1.0.1-302-gabcdef0", "Restarting Valheim Profile Sync"} {
		if !strings.Contains(log, expected) {
			t.Fatalf("the player was not told about %q:\n%s", expected, log)
		}
	}

	// Running the replacement now agrees with the portal, so the next run does nothing
	// and forgets the attempt - which is what keeps the loop stop from calcifying.
	second := &selfUpdater{Portal: harness.updater.Portal, HTTPClient: harness.updater.HTTPClient, Executable: harness.canonical, Progress: harness.progress.report}
	if second.apply(context.Background()) {
		t.Fatal("an up-to-date copy tried to update itself")
	}
	if second.readAttempt() != "" {
		t.Fatalf("the attempt record survived a successful update: %q", second.readAttempt())
	}
}

// The cache-immunity proof. The freshness answer and the download are two separate
// responses and either can be served from a cache; only the bytes decide. A download that
// is not the advertised build is discarded and the working copy is untouched.
func TestSelfUpdateRefusesBytesThatAreNotThePublishedBuild(t *testing.T) {
	// Same length as the published build, deliberately: three builds on 2026-09-14 were
	// within 25 KB of each other and shared a filename, so length is not identity. The
	// digest is the only thing that can tell these two apart.
	published := fakeExecutable("the build the portal means to serve")
	stale := fakeExecutable("a cached build from an earlier hour")
	if len(stale) != len(published) {
		t.Fatalf("this test is only meaningful with equal lengths: %d and %d", len(stale), len(published))
	}
	portal := &fakePortal{
		build:   clientBuild{Version: "v1.0.1-302-gabcdef0", SHA256: digestBytes(published), Size: int64(len(published))},
		payload: stale,
	}
	harness := newUpdateHarness(t, portal, fakeExecutable("installed"))

	if harness.updater.apply(context.Background()) {
		t.Fatal("bytes that were not the published build were installed")
	}
	if got := readFile(t, harness.canonical); got != string(fakeExecutable("installed")) {
		t.Fatalf("the working copy was damaged: %q", got)
	}
	if len(harness.launched) != 0 {
		t.Fatalf("something was launched: %q", harness.launched)
	}
	if !harness.progress.stage("Update postponed") {
		t.Fatalf("the player was not told the update was postponed:\n%s", harness.progress.text())
	}
}

// Offline, portal down, or a portal too old to answer at all: the run continues on the
// build already installed. This is the property that must never regress - a self-update
// that turns a working offline client into a dead one is worse than staleness.
func TestSelfUpdateFailsOpenWhenThePortalCannotAnswer(t *testing.T) {
	installed := fakeExecutable("installed")

	t.Run("no such endpoint", func(t *testing.T) {
		harness := newUpdateHarness(t, &fakePortal{status: http.StatusNotFound, body: "not found"}, installed)
		if harness.updater.apply(context.Background()) {
			t.Fatal("a 404 was treated as an update")
		}
		if got := readFile(t, harness.canonical); got != string(installed) {
			t.Fatalf("the working copy was damaged: %q", got)
		}
		if !harness.progress.stage("Update check skipped") {
			t.Fatalf("the skip was not reported:\n%s", harness.progress.text())
		}
	})

	t.Run("malformed answer", func(t *testing.T) {
		harness := newUpdateHarness(t, &fakePortal{status: http.StatusOK, body: "{not json"}, installed)
		if harness.updater.apply(context.Background()) {
			t.Fatal("a malformed answer was treated as an update")
		}
		if got := readFile(t, harness.canonical); got != string(installed) {
			t.Fatalf("the working copy was damaged: %q", got)
		}
	})

	t.Run("a portal that never answers is abandoned on the timeout", func(t *testing.T) {
		hang := make(chan struct{})
		defer close(hang)
		previous := updateCheckTimeout
		updateCheckTimeout = 80 * time.Millisecond
		defer func() { updateCheckTimeout = previous }()

		harness := newUpdateHarness(t, &fakePortal{hang: hang}, installed)
		started := time.Now()
		if harness.updater.apply(context.Background()) {
			t.Fatal("a hung portal was treated as an update")
		}
		if elapsed := time.Since(started); elapsed > 3*time.Second {
			t.Fatalf("the check did not honour its timeout: %s", elapsed)
		}
		if got := readFile(t, harness.canonical); got != string(installed) {
			t.Fatalf("the working copy was damaged: %q", got)
		}
	})
}

// The loop stop. A copy that has already swapped in bytes claiming to be this digest, and
// is still not running them, must not try again - it would relaunch into the same
// decision forever.
func TestSelfUpdateRefusesToRetryATargetItAlreadyAttempted(t *testing.T) {
	replacement := fakeExecutable("published")
	portal := &fakePortal{
		build:   clientBuild{Version: "v1.0.1-302-gabcdef0", SHA256: digestBytes(replacement), Size: int64(len(replacement))},
		payload: replacement,
	}
	harness := newUpdateHarness(t, portal, fakeExecutable("installed"))
	harness.updater.recordAttempt(digestBytes(replacement))

	if harness.updater.apply(context.Background()) {
		t.Fatal("the same failed target was attempted again")
	}
	if got := readFile(t, harness.canonical); got != string(fakeExecutable("installed")) {
		t.Fatalf("the working copy was replaced anyway: %q", got)
	}
	if !strings.Contains(harness.progress.text(), "already tried") {
		t.Fatalf("the loop stop is invisible to a support report:\n%s", harness.progress.text())
	}
}

// The restart is the one step that can fail after the bytes are already committed. The
// player must still get the profile they clicked for, on the build in memory, and the new
// build takes over on the next click.
func TestSelfUpdateContinuesInThisProcessWhenTheRestartFails(t *testing.T) {
	replacement := fakeExecutable("published")
	portal := &fakePortal{
		build:   clientBuild{SHA256: digestBytes(replacement), Size: int64(len(replacement))},
		payload: replacement,
	}
	harness := newUpdateHarness(t, portal, fakeExecutable("installed"))
	harness.launchErr = errors.New("CreateProcess failed")

	if harness.updater.apply(context.Background()) {
		t.Fatal("a failed restart reported that a replacement was running")
	}
	if got := readFile(t, harness.canonical); got != string(replacement) {
		t.Fatalf("the verified replacement was not kept: %q", got)
	}
	if !harness.progress.stage("Update installed") {
		t.Fatalf("the player was not told what happened:\n%s", harness.progress.text())
	}
}

// Only the copy the protocol launches self-updates. A freshly downloaded executable run
// by hand is about to install its own bytes, and replacing it with the portal's copy
// first would discard exactly the build the player went and fetched.
func TestOnlyTheInstalledCopySelfUpdates(t *testing.T) {
	installed := filepath.Join("C:\\", "Users", "p", "AppData", "Local", "Programs", "ValheimProfileSync", "ValheimProfileSync.exe")
	if !runningFromInstalledCopy(strings.ToUpper(installed), installed) {
		t.Fatal("Windows paths must compare case-insensitively")
	}
	if runningFromInstalledCopy(filepath.Join("C:\\", "Users", "p", "Downloads", "ValheimProfileSync.exe"), installed) {
		t.Fatal("a downloaded copy claimed to be the installed one")
	}
	if runningFromInstalledCopy("", installed) || runningFromInstalledCopy(installed, "") {
		t.Fatal("an unknown path claimed to be the installed one")
	}
}

// The line that would have ended the 2026-09-14 investigation in a minute.
func TestTheFirstProgressLineIdentifiesBuildPortalAndFile(t *testing.T) {
	portal, err := url.Parse("https://portal.example/base")
	if err != nil {
		t.Fatal(err)
	}
	update := clientIdentityUpdate(`C:\Users\p\AppData\Local\Programs\ValheimProfileSync\ValheimProfileSync.exe`, portal)
	for _, expected := range []string{"portal.example", `\Programs\ValheimProfileSync\ValheimProfileSync.exe`} {
		if !strings.Contains(update.Detail, expected) {
			t.Fatalf("the identity line omits %q: %q", expected, update.Detail)
		}
	}
	if !strings.HasPrefix(update.Detail, "Build ") {
		t.Fatalf("the identity line does not start with the build: %q", update.Detail)
	}
	// A failure report that cannot name its own build is not a report.
	if !strings.Contains(failureGuidance("Profile update stopped", errors.New("boom")), "Build ") {
		t.Fatal("the failure report does not identify the build")
	}
}

// The portal's own links may or may not carry a /client suffix, and the check must reach
// the same routes either way.
func TestClientRouteURLTolerantOfAPortalBase(t *testing.T) {
	for _, base := range []string{"https://portal.example", "https://portal.example/", "https://portal.example/client"} {
		parsed, err := url.Parse(base)
		if err != nil {
			t.Fatal(err)
		}
		if got := clientRouteURL(parsed, "version"); got != "https://portal.example/client/version" {
			t.Fatalf("base %q resolved to %q", base, got)
		}
	}
}
