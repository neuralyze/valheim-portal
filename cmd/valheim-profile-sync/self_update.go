package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/neuralyze/valheim-portal/internal/version"
)

// Keeping the installed copy current.
//
// The portal's profile buttons do not launch whatever the player last downloaded. They
// launch %LOCALAPPDATA%\Programs\ValheimProfileSync\ValheimProfileSync.exe, because that
// is the path install.go registers under HKCU\Software\Classes. So the installed copy is
// frozen at whichever build was first run by hand, and downloading a newer one changes
// nothing until somebody double-clicks the download - a step no player knows about.
//
// Measured on 2026-09-14: four client builds shipped inside an hour, the operator clicked
// the portal link after each one, and every click ran the same pre-existing binary. Its
// progress output had no build label, which is the only reason the diagnosis was possible
// at all, and it took an hour. One of those builds was genuinely broken; a frozen copy
// would have kept a broken build alive indefinitely.
//
// So the launcher now checks, on the profile path, whether the bytes it is running are
// the bytes the portal publishes, and replaces itself when they are not.

// updateCheckTimeout bounds the whole freshness question. It is deliberately short: a
// player on a dead connection, or one whose portal is down, must still get the profile
// work they clicked for. A self-update that turns a working offline client into a dead
// one is strictly worse than staleness, so every failure here is swallowed and the run
// continues on the build already installed. A variable so the fail-open path can be
// exercised without a six-second test.
var updateCheckTimeout = 6 * time.Second

const (
	// updateDownloadTimeout bounds the replacement download. The executable is ~16 MB,
	// so this is generous for a slow connection and still finite.
	updateDownloadTimeout = 4 * time.Minute
	// maxClientDownload is the ceiling on what will be read from the portal, so a
	// misconfigured or hostile endpoint cannot exhaust memory.
	maxClientDownload = 96 << 20
	// retiredSuffix names the running image after it is moved aside. Windows permits
	// renaming a mapped executable but not deleting one, so the old file is removed on
	// a LATER run rather than this one.
	retiredSuffix = ".outdated-"
	// incomingSuffix names the verified replacement before it is swapped in.
	incomingSuffix = ".incoming-"
	// updateAttemptName records the digest this copy last tried to become. It is what
	// stops an update loop: see decideClientUpdate.
	updateAttemptName = ".update-attempt"
)

// clientBuild is the portal's description of the client executable it is serving.
type clientBuild struct {
	Version string `json:"version"`
	SHA256  string `json:"sha256"`
	Size    int64  `json:"size"`
}

// label names a build for a human. The version string is preferred, but it is only a
// label and may be absent; the digest prefix always identifies the bytes.
func (build clientBuild) label() string {
	if build.Version != "" {
		return build.Version
	}
	return "build " + shortDigest(build.SHA256)
}

func shortDigest(digest string) string {
	if len(digest) > 12 {
		return digest[:12]
	}
	return digest
}

// validate rejects an answer that cannot be acted on. A portal that predates this
// endpoint answers 404 and never reaches here; one that answers with nonsense must not
// be able to make the launcher download and install it.
func (build clientBuild) validate() error {
	if !validSHA256(build.SHA256) {
		return errors.New("the portal did not report a usable client digest")
	}
	if build.Size <= 0 || build.Size > maxClientDownload {
		return fmt.Errorf("the portal reported an implausible client size (%d bytes)", build.Size)
	}
	if len(build.Version) > 128 {
		return errors.New("the portal reported an implausible client version")
	}
	for _, r := range build.Version {
		if r < 0x20 || r == 0x7f {
			return errors.New("the portal reported an unprintable client version")
		}
	}
	return nil
}

// updateVerdict is the decision this whole mechanism turns on.
type updateVerdict int

const (
	// updateCurrent means the running bytes are the published bytes.
	updateCurrent updateVerdict = iota
	// updateAvailable means they are not, and a replacement should be fetched.
	updateAvailable
	// updateAlreadyAttempted means this copy already swapped in bytes claiming to be
	// this digest and is still not running them, so trying again would loop.
	updateAlreadyAttempted
)

// decideClientUpdate compares IDENTITY, never recency.
//
// There is no ordering to be had here. "v1.0.1-301-g2b1fc1f" is a git description, not a
// version number, and a build can legitimately be re-cut at the same description; parsing
// it into a comparison would invent an ordering the string does not carry. So the question
// is only ever "are these the bytes the portal publishes", answered by SHA-256 of the file
// on disk against the SHA-256 the portal computes from the file it serves.
//
// That also settles the player who runs a NEWER exe by hand against an older portal: the
// portal is the authority on what its players should run, so that copy is replaced by the
// portal's, once. It cannot loop, for two independent reasons. First, after a successful
// swap the digests agree and the next run decides updateCurrent. Second, the attempt
// record: before swapping, the target digest is written beside the executable, and a copy
// that finds itself still mismatched against a digest it has already attempted refuses to
// attempt it again. Without that record, a swap that silently did not take effect - a
// path that is not what we think it is, a copy restored behind our back by other software -
// would relaunch into the same decision forever.
func decideClientUpdate(runningDigest string, remote clientBuild, lastAttempt string) (updateVerdict, error) {
	if err := remote.validate(); err != nil {
		return updateCurrent, err
	}
	if !validSHA256(runningDigest) {
		return updateCurrent, errors.New("the running client could not be identified")
	}
	if strings.EqualFold(runningDigest, remote.SHA256) {
		return updateCurrent, nil
	}
	if strings.EqualFold(strings.TrimSpace(lastAttempt), remote.SHA256) {
		return updateAlreadyAttempted, nil
	}
	return updateAvailable, nil
}

// clientRouteURL builds an absolute URL for one of the portal's /client routes, tolerating
// a portal base that already ends in /client - the same allowance statusEndpoint makes,
// because the value comes from a link the portal itself wrote.
func clientRouteURL(base *url.URL, leaf string) string {
	u := *base
	path := strings.TrimSuffix(strings.TrimRight(u.Path, "/"), "/client")
	u.Path = path + "/client/" + leaf
	u.RawPath = ""
	u.RawQuery = ""
	u.ForceQuery = false
	u.Fragment = ""
	return u.String()
}

// fetchClientBuild asks the portal what it is serving.
func fetchClientBuild(ctx context.Context, httpClient *http.Client, base *url.URL) (clientBuild, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, clientRouteURL(base, "version"), nil)
	if err != nil {
		return clientBuild{}, err
	}
	request.Header.Set("Accept", "application/json")
	request.Header.Set("Cache-Control", "no-cache")
	response, err := httpClient.Do(request)
	if err != nil {
		return clientBuild{}, err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return clientBuild{}, fmt.Errorf("the portal answered %s", response.Status)
	}
	var build clientBuild
	if err := json.NewDecoder(io.LimitReader(response.Body, 64<<10)).Decode(&build); err != nil {
		return clientBuild{}, fmt.Errorf("read the portal's client version: %w", err)
	}
	if err := build.validate(); err != nil {
		return clientBuild{}, err
	}
	return build, nil
}

// downloadClientExecutable fetches the replacement and proves it is the advertised build
// before a single byte is committed to disk.
//
// This is what makes the whole check immune to a cache. The freshness answer could be a
// stale JSON body, the executable could come from a proxy that ignored every header, and
// it would still not matter: the bytes are hashed, and a digest that does not match the
// one the portal reported is discarded. Nothing here trusts ETag, Last-Modified or
// Cache-Control to be honest - they only save a round trip when they are.
func downloadClientExecutable(ctx context.Context, httpClient *http.Client, base *url.URL, build clientBuild) ([]byte, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, clientRouteURL(base, installedExecutableName), nil)
	if err != nil {
		return nil, err
	}
	request.Header.Set("Cache-Control", "no-cache")
	response, err := httpClient.Do(request)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("the portal answered %s", response.Status)
	}
	payload, err := io.ReadAll(io.LimitReader(response.Body, build.Size+1))
	if err != nil {
		return nil, err
	}
	if int64(len(payload)) != build.Size {
		return nil, fmt.Errorf("the download was %d bytes, not the published %d", len(payload), build.Size)
	}
	if digest := digestBytes(payload); !strings.EqualFold(digest, build.SHA256) {
		return nil, fmt.Errorf("the download was not the published build (%s, not %s)", shortDigest(digest), shortDigest(build.SHA256))
	}
	// A cheap last sanity check on the shape of the thing we are about to make the
	// player's launcher. The portal already refuses to publish a non-GUI PE.
	if len(payload) < 2 || payload[0] != 'M' || payload[1] != 'Z' {
		return nil, errors.New("the download is not a Windows executable")
	}
	return payload, nil
}

// selfUpdater replaces the running executable in place. Every filesystem and process
// operation is a field so the partial-failure paths can be exercised on a machine that is
// not Windows - which is every machine this code is built on.
type selfUpdater struct {
	Portal     *url.URL
	HTTPClient *http.Client
	// Executable is both the running image and the canonical installed path. The two
	// are the same file on the path this runs on; see applyClientSelfUpdate.
	Executable string
	Args       []string
	Progress   progressReporter

	Digest    func(path string) (string, error)
	Download  func(ctx context.Context, build clientBuild) ([]byte, error)
	WriteFile func(path string, data []byte) error
	Rename    func(from, to string) error
	Remove    func(path string) error
	Launch    func(executable string, args []string) error
	Now       func() time.Time
}

func (updater *selfUpdater) digest(path string) (string, error) {
	if updater.Digest != nil {
		return updater.Digest(path)
	}
	return fileDigest(path)
}

func (updater *selfUpdater) writeFile(path string, data []byte) error {
	if updater.WriteFile != nil {
		return updater.WriteFile(path, data)
	}
	return os.WriteFile(path, data, 0o700)
}

func (updater *selfUpdater) rename(from, to string) error {
	if updater.Rename != nil {
		return updater.Rename(from, to)
	}
	return os.Rename(from, to)
}

func (updater *selfUpdater) remove(path string) error {
	if updater.Remove != nil {
		return updater.Remove(path)
	}
	return os.Remove(path)
}

func (updater *selfUpdater) launch(executable string, args []string) error {
	if updater.Launch != nil {
		return updater.Launch(executable, args)
	}
	return launchReplacement(executable, args)
}

func (updater *selfUpdater) now() time.Time {
	if updater.Now != nil {
		return updater.Now()
	}
	return time.Now()
}

func (updater *selfUpdater) download(ctx context.Context, build clientBuild) ([]byte, error) {
	if updater.Download != nil {
		return updater.Download(ctx, build)
	}
	return downloadClientExecutable(ctx, updater.HTTPClient, updater.Portal, build)
}

// apply reports whether a replacement was started and this process must stop.
//
// It returns false for every failure without exception. Offline, portal down, portal too
// old to have the endpoint, malformed answer, failed download, failed swap: all of them
// leave the player on the build they already had, which still works.
func (updater *selfUpdater) apply(ctx context.Context) bool {
	updater.sweepRetired()

	checkCtx, cancel := context.WithTimeout(ctx, updateCheckTimeout)
	build, err := fetchClientBuild(checkCtx, updater.HTTPClient, updater.Portal)
	cancel()
	if err != nil {
		report(updater.Progress, progressUpdate{Stage: "Update check skipped", Detail: "The portal did not answer the version check, so this run continues on installer " + version.Version + ". " + err.Error(), Percent: 2})
		return false
	}
	running, err := updater.digest(updater.Executable)
	if err != nil {
		report(updater.Progress, progressUpdate{Stage: "Update check skipped", Detail: "This copy could not identify its own bytes, so it was left alone. " + err.Error(), Percent: 2})
		return false
	}
	verdict, err := decideClientUpdate(running, build, updater.readAttempt())
	if err != nil {
		report(updater.Progress, progressUpdate{Stage: "Update check skipped", Detail: "The portal's answer could not be used, so this run continues on installer " + version.Version + ". " + err.Error(), Percent: 2})
		return false
	}
	switch verdict {
	case updateCurrent:
		updater.clearAttempt()
		return false
	case updateAlreadyAttempted:
		report(updater.Progress, progressUpdate{Stage: "Update check skipped", Detail: "This copy already tried to become " + build.label() + " and did not, so it will not try again. Report this line.", Percent: 2})
		return false
	}

	report(updater.Progress, progressUpdate{Stage: "Updating Valheim Profile Sync", Detail: "A newer app is published: " + build.label() + ". Downloading it now.", Percent: 3})
	downloadCtx, cancelDownload := context.WithTimeout(ctx, updateDownloadTimeout)
	payload, err := updater.download(downloadCtx, build)
	cancelDownload()
	if err != nil {
		report(updater.Progress, progressUpdate{Stage: "Update postponed", Detail: "The newer app could not be downloaded, so this run continues on installer " + version.Version + ". " + err.Error(), Percent: 3})
		return false
	}
	// Recorded BEFORE the swap, so a copy that swaps and somehow keeps running the old
	// bytes still counts as having tried. This is the loop stop.
	updater.recordAttempt(build.SHA256)
	if err := updater.swap(payload); err != nil {
		report(updater.Progress, progressUpdate{Stage: "Update postponed", Detail: "The app could not be replaced, so this run continues on installer " + version.Version + ". " + err.Error(), Percent: 3})
		return false
	}
	if err := updater.launch(updater.Executable, updater.Args); err != nil {
		// The new bytes are in place and verified; only the restart failed. Carrying on
		// in this process gives the player the profile they clicked for, and the next
		// click runs the new build.
		report(updater.Progress, progressUpdate{Stage: "Update installed", Detail: build.label() + " is installed and starts next time. Continuing now on installer " + version.Version + ". " + err.Error(), Percent: 4})
		return false
	}
	report(updater.Progress, progressUpdate{Stage: "Restarting Valheim Profile Sync", Detail: build.label() + " is installed. Reopening your profile in the new app now.", Percent: 4})
	return true
}

// swap puts payload at the canonical path without ever leaving that path missing or
// half-written.
//
// A running executable cannot be overwritten on Windows: the image is mapped and the
// filesystem refuses the write. It CAN be renamed, which is the whole trick - the mapping
// follows the file, not the name. So:
//
//  1. Write the verified bytes to a sibling temp name. Nothing is at risk yet; the
//     canonical path still holds the working build. A failure here deletes the temp.
//  2. Rename the canonical path to a sibling ".outdated-<stamp>" name. The process keeps
//     running from it. A failure here deletes the temp and changes nothing.
//  3. Rename the temp onto the canonical path. This is the only moment the canonical path
//     does not exist, and it spans two metadata operations in one directory. A failure
//     here renames the retired copy straight back, and if even that fails the verified
//     bytes are copied into place instead - the canonical path is never left missing.
//
// The retired copy is NOT deleted here. It is still mapped by this very process, so the
// delete would fail; sweepRetired removes it on a later run.
func (updater *selfUpdater) swap(payload []byte) error {
	stamp := fmt.Sprintf("%d", updater.now().UnixNano())
	incoming := updater.Executable + incomingSuffix + stamp
	retired := updater.Executable + retiredSuffix + stamp

	if err := updater.writeFile(incoming, payload); err != nil {
		_ = updater.remove(incoming)
		return fmt.Errorf("stage the new app: %w", err)
	}
	if err := updater.rename(updater.Executable, retired); err != nil {
		_ = updater.remove(incoming)
		return fmt.Errorf("move the running app aside: %w", err)
	}
	if err := updater.rename(incoming, updater.Executable); err != nil {
		if restoreErr := updater.rename(retired, updater.Executable); restoreErr == nil {
			_ = updater.remove(incoming)
			return fmt.Errorf("install the new app: %w", err)
		}
		// The canonical path is empty and the old copy will not come back. The bytes in
		// hand are verified, so writing them there directly is both the repair and the
		// update.
		if writeErr := updater.writeFile(updater.Executable, payload); writeErr != nil {
			return fmt.Errorf("install the new app: %w; the app may need to be downloaded again from the portal", err)
		}
		_ = updater.remove(incoming)
		return nil
	}
	return nil
}

// sweepRetired deletes the copies earlier runs moved aside. Failures are expected and
// ignored: a copy that is still mapped by a process that has not exited yet cannot be
// deleted, and it will be gone on the run after that.
func (updater *selfUpdater) sweepRetired() {
	directory := filepath.Dir(updater.Executable)
	entries, err := os.ReadDir(directory)
	if err != nil {
		return
	}
	prefix := filepath.Base(updater.Executable)
	for _, entry := range entries {
		name := entry.Name()
		if !strings.HasPrefix(name, prefix+retiredSuffix) && !strings.HasPrefix(name, prefix+incomingSuffix) {
			continue
		}
		_ = updater.remove(filepath.Join(directory, name))
	}
}

func (updater *selfUpdater) attemptPath() string {
	return filepath.Join(filepath.Dir(updater.Executable), updateAttemptName)
}

func (updater *selfUpdater) readAttempt() string {
	payload, err := os.ReadFile(updater.attemptPath())
	if err != nil || len(payload) > 128 {
		return ""
	}
	return strings.TrimSpace(string(payload))
}

func (updater *selfUpdater) recordAttempt(digest string) {
	_ = os.WriteFile(updater.attemptPath(), []byte(digest), 0o600)
}

func (updater *selfUpdater) clearAttempt() {
	if _, err := os.Stat(updater.attemptPath()); err == nil {
		_ = os.Remove(updater.attemptPath())
	}
}

func digestBytes(payload []byte) string {
	sum := sha256.Sum256(payload)
	return hex.EncodeToString(sum[:])
}

// launchReplacement starts the new executable with the arguments this process was given,
// so the profile link the player clicked is honoured by the build that replaced us.
func launchReplacement(executable string, args []string) error {
	command := exec.Command(executable, args...)
	command.Dir = filepath.Dir(executable)
	return command.Start()
}

// runningFromInstalledCopy decides whether this process is the copy the portal's protocol
// registration launches. Only that copy self-updates: a freshly downloaded executable run
// by hand is about to install its own bytes over the installed path a moment later, and
// replacing it with the portal's copy first would throw away exactly the build the player
// went and fetched.
//
// Compared case-insensitively, because Windows paths are.
func runningFromInstalledCopy(executable, installed string) bool {
	if executable == "" || installed == "" {
		return false
	}
	return strings.EqualFold(filepath.Clean(executable), filepath.Clean(installed))
}

// applyClientSelfUpdate is the whole mechanism as the profile flow uses it: returns true
// when a replacement has been started and this process must stop doing anything else.
func applyClientSelfUpdate(ctx context.Context, portal *url.URL, args []string, reporter progressReporter) bool {
	if portal == nil {
		return false
	}
	executable, err := os.Executable()
	if err != nil {
		return false
	}
	if resolved, absErr := filepath.Abs(executable); absErr == nil {
		executable = resolved
	}
	installed, err := installedApplicationPath()
	if err != nil || !runningFromInstalledCopy(executable, installed) {
		return false
	}
	updater := &selfUpdater{
		Portal:     portal,
		HTTPClient: &http.Client{Timeout: updateDownloadTimeout},
		Executable: executable,
		Args:       args,
		Progress:   reporter,
	}
	return updater.apply(ctx)
}

// clientIdentityUpdate is the first line of every profile run, and it exists because the
// operator pasted the same failure four times and not one copy said which build produced
// it, which portal it was talking to, or which file was running. Those three facts are
// what finally ended the 2026-09-14 investigation, and they cost one line.
func clientIdentityUpdate(executable string, portal *url.URL) progressUpdate {
	host := "an unknown portal"
	if portal != nil && portal.Host != "" {
		host = portal.Host
	}
	if executable == "" {
		executable = "an unknown location"
	}
	return progressUpdate{
		Stage:   "Valheim Profile Sync",
		Detail:  "Build " + version.Version + " | portal " + host + " | running from " + executable,
		Percent: 1,
	}
}

// clientEnvironmentLine is the same identity, shortened, for the bottom of a failure
// report - which is the part a player actually copies.
func clientEnvironmentLine() string {
	executable, err := os.Executable()
	if err != nil || executable == "" {
		executable = "an unknown location"
	}
	return "Build " + version.Version + ", running from " + executable
}
