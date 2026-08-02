// Package version carries the build identity stamped into the portal and the
// profile synchronizer at link time.
//
// Nothing here is derived at runtime. Go's own VCS stamping is unavailable
// because the checkout can sit inside an unrelated repository, so the build
// scripts pass the identity explicitly:
//
//	go build -ldflags "-X github.com/neuralyze/valheim-portal/internal/version.Version=$(git describe --tags --always --dirty)"
//
// An unstamped build reports "dev", which is a deliberate signal that the
// binary did not come from a tagged release.
package version

// Version is the release identity of this binary. Overridden at link time.
var Version = "dev"

// Stamped reports whether a build identity was supplied at link time.
func Stamped() bool { return Version != "dev" && Version != "" }

// UserAgent returns the value the named component sends to the portal, so a
// server-side log identifies which client build made a request.
func UserAgent(component string) string {
	return "valheim-portal/" + Version + " (" + component + ")"
}
