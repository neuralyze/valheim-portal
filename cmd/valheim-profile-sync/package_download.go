package main

import (
	"context"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"
)

// Why a package download retries at all: on 2026-09-14 one installation of
// ulfsland-vr-flat-admin - 105 packages, an empty cache - died on the FIRST failure, a dial
// timeout to 92.38.145.145:443 for one Thunderstore file. Measured from the server at the
// same minute, that exact URL returned 200 in 0.065 s over IPv6. One broken route to one
// CDN edge, on the player's path only, and it abandoned an otherwise complete install.
//
// The numbers: four attempts per package, waiting 2s, 5s then 10s between them, so one
// recoverable edge costs at most 17 s of waiting plus the failed attempts themselves.
// A dial timeout is expensive to observe - Windows connectex gives up after roughly 21 s,
// and the client's own HTTP timeout caps an attempt at 60 s - so retrying four times can
// cost a minute and a half for a single genuinely dead package. With 105 packages that is
// the whole point of packageRetryBudget: retry WAITING and retry ATTEMPTS are drawn from
// one 6-minute allowance for the entire install, so a fleet-wide outage still fails in
// minutes rather than hours, while the single-edge case this exists for is absorbed in
// seconds. First attempts are never charged to the budget - every package always gets one.
const (
	packageDownloadAttempts = 4
	packageRetryBudget      = 6 * time.Minute
	// The mirror is the portal we are already talking to, on a path already proven to
	// work this session, so it does not need the primary's patience - and it is not
	// charged to the retry budget, because it is the recovery rather than the failure.
	packageMirrorAttempts = 2
)

func packageRetryBackoff(attempt int) time.Duration {
	switch attempt {
	case 1:
		return 2 * time.Second
	case 2:
		return 5 * time.Second
	default:
		return 10 * time.Second
	}
}

// retryAllowance is the install-wide budget for time spent retrying, shared by every
// package in one sync. Only retry time is charged: a failed attempt that is about to be
// retried, and the wait before it.
type retryAllowance struct{ remaining time.Duration }

func newRetryAllowance() *retryAllowance { return &retryAllowance{remaining: packageRetryBudget} }

func (allowance *retryAllowance) exhausted() bool {
	return allowance == nil || allowance.remaining <= 0
}

func (allowance *retryAllowance) spend(spent time.Duration) {
	if allowance != nil {
		allowance.remaining -= spent
	}
}

// errDownloadIncomplete and errDownloadWrongBytes are the two halves of a failed verified
// download, and the distinction decides whether retrying can possibly help.
//
// Incomplete means the body ended early or ran long - a truncated transfer, which is a
// transport fault and is worth another attempt. Wrong bytes means a COMPLETE body of the
// expected length whose SHA-256 is not the published one: the source is serving different
// content, so every retry would fetch the same wrong archive. That one fails immediately.
var (
	errDownloadIncomplete = errors.New("download ended early")
	errDownloadWrongBytes = errors.New("download does not match the published checksum")
)

// packageHTTPError carries the status of a refused download so the retry decision can be
// made on the code rather than on a formatted string.
type packageHTTPError struct {
	status string
	code   int
	host   string
}

func (err *packageHTTPError) Error() string {
	return fmt.Sprintf("%s returned %s", err.host, err.status)
}

// packageDownloadError is what a package that could not be fetched from any source
// finally returns. It names the package and the host, because that is precisely what the
// old failure message could not say: the operator was told to check Steam and their world
// access, both of which were fine, while the actual fault was one CDN edge.
type packageDownloadError struct {
	Package  string
	Host     string
	Attempts int
	Mirrored bool
	Network  bool
	err      error
}

func (err *packageDownloadError) Error() string {
	sources := "Thunderstore"
	if err.Mirrored {
		sources = "Thunderstore and this world's own server"
	}
	return fmt.Sprintf("download %s from %s: gave up after %d attempts against %s: %v",
		err.Package, sources, err.Attempts, err.Host, err.err)
}

func (err *packageDownloadError) Unwrap() error { return err.err }

// transientDownloadFailure decides whether another attempt could plausibly succeed.
//
// Retried: every transport failure (dial timeout, connection reset, TLS handshake
// failure, DNS hiccup), a truncated body, and the server statuses that mean "not now" -
// 408, 425, 429 and anything 5xx.
//
// Not retried: a definite answer. 404 means the file is not there and will not appear;
// 401/403 mean we are not allowed; a complete body with the wrong SHA-256 means the bytes
// are WRONG rather than missing, which is a publish or a tamper, not a network problem.
// A cancelled context is not retried either - the player closed the window.
func transientDownloadFailure(err error) bool {
	if err == nil {
		return false
	}
	if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
		return false
	}
	if errors.Is(err, errDownloadWrongBytes) {
		return false
	}
	if errors.Is(err, errDownloadIncomplete) {
		return true
	}
	var status *packageHTTPError
	if errors.As(err, &status) {
		return status.code == http.StatusRequestTimeout ||
			status.code == http.StatusTooEarly ||
			status.code == http.StatusTooManyRequests ||
			status.code >= 500
	}
	var netErr net.Error
	if errors.As(err, &netErr) {
		return true
	}
	// Local faults - out of disk, a permission problem in the cache - are not helped by
	// waiting, and anything unrecognised is left alone rather than spun on.
	return false
}

// packageMirror is this world's own portal, used as a SECOND source for package bytes.
//
// The server already holds every package it published, under its profile's manager cache,
// so a player whose route to one Thunderstore edge is broken does not have to be stuck:
// the portal serves the same archive, addressed by the SHA-256 the definition already
// carries, over the connection that just authorised this sync.
//
// It is a fallback and nothing more. Thunderstore is tried to exhaustion first, so normal
// installs do not move a byte through the portal and we do not become a CDN by accident.
// An empty endpoint - a client talking to a portal that predates the mirror, or a caller
// that has no token - simply means there is no second source, which is how this client
// behaves against every older deployment.
type packageMirror struct {
	endpoint string
	token    string
}

func (mirror packageMirror) available() bool {
	return mirror.endpoint != "" && mirror.token != ""
}

func (mirror packageMirror) urlFor(packageInfo packageDefinition) string {
	return mirror.endpoint + "/" + strings.ToLower(packageInfo.SHA256)
}

type packageSource struct {
	label    string
	url      string
	token    string
	attempts int
	// budgeted charges this source's retries to the install-wide allowance. The primary
	// is; the portal mirror is not.
	budgeted bool
}

// packageSources is the ordered list of places one package's bytes may come from.
func packageSources(packageInfo packageDefinition, mirror packageMirror) ([]packageSource, error) {
	primary, err := packageDownloadURL(packageInfo)
	if err != nil {
		return nil, err
	}
	sources := []packageSource{{label: downloadHost(primary), url: primary, attempts: packageDownloadAttempts, budgeted: true}}
	if mirror.available() {
		sources = append(sources, packageSource{
			label:    "this world's own server",
			url:      mirror.urlFor(packageInfo),
			token:    mirror.token,
			attempts: packageMirrorAttempts,
		})
	}
	return sources, nil
}

func downloadHost(target string) string {
	parsed, err := url.Parse(target)
	if err != nil || parsed.Host == "" {
		return "the download server"
	}
	return parsed.Hostname()
}

// fetchPackage downloads one package into the cache, trying each source in order and
// retrying only what retrying can fix. Every attempt after the first is announced, because
// a progress display that goes quiet for a minute reads as a hang - which is how the
// operator described this failure before anyone knew it was a download.
func (syncer *profileSyncer) fetchPackage(ctx context.Context, cache string, packageInfo packageDefinition, mirror packageMirror, allowance *retryAllowance) (string, error) {
	sources, err := packageSources(packageInfo, mirror)
	if err != nil {
		return "", err
	}
	attempted := 0
	// The failure a player is told about is the PRIMARY source's, not the last one tried.
	// A mirror that is simply not configured answers 404, and reporting that would blame
	// this world's own server for a fault that belongs to the CDN edge - which is the
	// exact class of misdirection this change exists to remove.
	var primaryErr error
	primaryHost := downloadHost(sources[0].url)
	var lastErr error
	lastHost := primaryHost
	for index, source := range sources {
		if index > 0 {
			report(syncer.Progress, progressUpdate{
				Stage:   "Trying another source for " + packageInfo.Name,
				Detail:  fmt.Sprintf("%s could not be reached (%v). Fetching the same file from %s, and checking it against the published checksum.", lastHost, lastErr, source.label),
				Percent: 48,
			})
		}
		for attempt := 1; attempt <= source.attempts; attempt++ {
			started := time.Now()
			path, err := syncer.downloadPackageOnce(ctx, cache, packageInfo, source)
			attempted++
			if err == nil {
				if attempted > 1 {
					report(syncer.Progress, progressUpdate{
						Stage:   "Recovered " + packageInfo.Name,
						Detail:  fmt.Sprintf("Downloaded from %s on attempt %d, and it matches the published checksum.", source.label, attempted),
						Percent: 48,
					})
				}
				return path, nil
			}
			lastErr, lastHost = err, downloadHost(source.url)
			if index == 0 {
				primaryErr = err
			}
			if !transientDownloadFailure(err) || attempt == source.attempts {
				break
			}
			if source.budgeted && allowance.exhausted() {
				report(syncer.Progress, progressUpdate{
					Stage:   "No time left for retries",
					Detail:  fmt.Sprintf("%s has spent this update's retry allowance; %s failed with: %v", source.label, packageInfo.Name, err),
					Percent: 48,
				})
				break
			}
			wait := packageRetryBackoff(attempt)
			report(syncer.Progress, progressUpdate{
				Stage:   "Retrying " + packageInfo.Name,
				Detail:  fmt.Sprintf("Attempt %d of %d against %s failed: %v. Waiting %s and trying again.", attempt, source.attempts, source.label, err, wait),
				Percent: 48,
			})
			if err := syncer.pause(ctx, wait); err != nil {
				return "", err
			}
			if source.budgeted {
				allowance.spend(time.Since(started))
			}
		}
	}
	cause, host := primaryErr, primaryHost
	if cause == nil {
		cause, host = lastErr, lastHost
	}
	return "", &packageDownloadError{
		Package:  packageInfo.Filename,
		Host:     host,
		Attempts: attempted,
		Mirrored: len(sources) > 1,
		Network:  isNetworkFailure(cause),
		err:      cause,
	}
}

// isNetworkFailure separates "we could not reach the host" from "the host answered and the
// answer was no". Only the first justifies telling a player their network path is at fault.
func isNetworkFailure(err error) bool {
	if err == nil {
		return false
	}
	var status *packageHTTPError
	if errors.As(err, &status) {
		return false
	}
	var netErr net.Error
	return errors.As(err, &netErr)
}

func (syncer *profileSyncer) downloadPackageOnce(ctx context.Context, cache string, packageInfo packageDefinition, source packageSource) (string, error) {
	httpRequest, err := http.NewRequestWithContext(ctx, http.MethodGet, source.url, nil)
	if err != nil {
		return "", err
	}
	if source.token != "" {
		httpRequest.Header.Set("Authorization", "Bearer "+source.token)
	}
	response, err := syncer.HTTPClient.Do(httpRequest)
	if err != nil {
		return "", err
	}
	if response.StatusCode != http.StatusOK {
		response.Body.Close()
		return "", &packageHTTPError{status: response.Status, code: response.StatusCode, host: downloadHost(source.url)}
	}
	// The SHA-256 discipline is identical whichever source answered: downloadVerified
	// rejects anything whose length or hash is not the one the definition published, and
	// only then is the file allowed into the cache. A mirror hit is not trusted more than
	// a CDN hit, and a mirror hit with the wrong bytes is a hard failure, not a retry.
	temporary, downloadErr := downloadVerified(response.Body, cache, ".package-", packageInfo.Size, packageInfo.SHA256, maxPackageArchiveBytes)
	response.Body.Close()
	if downloadErr != nil {
		return "", downloadErr
	}
	path := packageCachePath(cache, packageInfo)
	if err := replaceFile(temporary, path); err != nil {
		os.Remove(temporary)
		return "", err
	}
	return path, nil
}

func (syncer *profileSyncer) pause(ctx context.Context, wait time.Duration) error {
	if syncer.Wait != nil {
		syncer.Wait(wait)
		return ctx.Err()
	}
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-time.After(wait):
		return nil
	}
}
