// TEMPORARY: local ServerCharacters build. Delete this package when Thunderstore
// publishes 1.4.17; see the retirement note below.
//
// Package servercharacters carries our own build of ServerCharacters 1.4.17.1 and hands the
// same bytes to both sides of the fleet: cmd/profile-definition-builder hashes them into a
// published profile definition, and cmd/valheim-profile-sync writes them into the player's
// profile instead of downloading anything.
//
// Why this exists. Valheim 1.0 moved eight members this mod uses, so 1.4.16 - still
// Thunderstore's newest, from 2025-05-02 - throws MissingMethodException out of
// FejdStartup.Awake and must never be installed on 1.0.12. Upstream fixed all eight in
// blaxxun-boop/ServerCharacters@bb7d3cd6 and called it 1.4.17, but has not released it.
// The operator decided on 2026-09-14 to run our own compile of that commit on their own
// server and their own clients until Thunderstore carries it, and on 2026-09-15 to modify
// it: upstream's template code equips nothing and hardcodes item quality to 1, so a
// templated character spawned unarmoured holding its own gear. tools/servercharacters/
// patches/ is the whole divergence, and it is why the version here is 1.4.17.1 rather than
// 1.4.17 - these bytes must never be mistaken for the author's.
//
// Why an embed rather than a download. ServerSync declares MinimumRequiredVersion equal to
// its own version with ModRequired = true, and VersionCheck.IsVersionOk is symmetric, so a
// client whose build differs from the server's is refused at the handshake in BOTH
// directions. Server and clients are installed from this one archive, which removes version
// skew as a category rather than checking for it. It also means we publish no URL for it and
// no client has to trust a second CDN - the Hexium backend in cmd/profile-definition-builder
// stays where it is, unused by this path and ready for the next mod that lives off
// Thunderstore.
//
// The archive itself is NOT in git. See embedded/README.md for why, and for how to build
// it. This package compiles without it; only a profile that selects the mod needs it.
//
// RETIREMENT: when Thunderstore publishes 1.4.17, delete this package, its embedded/
// directory, and the four blocks marked "TEMPORARY: local ServerCharacters build" in
// cmd/profile-definition-builder/main.go, cmd/valheim-profile-sync/sync.go and
// tools/valheim_mods.py. A plain `valheim_mods.py add Smoothbrain-ServerCharacters`
// replaces all of it.
package servercharacters

import (
	"crypto/sha256"
	"embed"
	"encoding/hex"
	"errors"
	"fmt"
	"io/fs"
	"strings"
)

const (
	// Namespace and Name are the Thunderstore identity this build stands in for, and the
	// identity a profile manifest selects. Both install paths gate on it, so nothing lands
	// on a profile - Hrafnheim's, Doggerland's, Storgard's, Vangard's - that does not ask
	// for the mod.
	Namespace = "Smoothbrain"
	Name      = "ServerCharacters"
	// Version is the ModVersion the build actually produces, read off the patched source by
	// tools/servercharacters/build.sh and mirrored here because the profile definition, the
	// package cache filename and the archive's own manifest.json must agree. It is
	// upstream's 1.4.17 plus a fourth component, because this build is NOT upstream's: see
	// tools/servercharacters/patches/ for the exact divergence. Upstream only ever
	// publishes three components, so the fourth can never collide with a real release, and
	// System.Version - which is what ServerSync.VersionCheck.IsVersionOk parses these
	// strings with - orders 1.4.17.1 above 1.4.17.
	Version = "1.4.17.1"
	// ArchiveFileName is the name the archive carries inside the package cache. It matches
	// what cmd/profile-definition-builder derives for every other package, so the client's
	// cache and its definition entries stay one shape.
	ArchiveFileName = Namespace + "-" + Name + "-" + Version + ".zip"

	embeddedName = "embedded/ServerCharacters.zip"
)

// ErrArchiveMissing is what every caller gets when the build has not been run. It names the
// path and the script, because the alternative - a build-time //go:embed failure on a file
// that is deliberately not in git - would break `go build` for everyone who never touches
// this mod.
var ErrArchiveMissing = errors.New(
	"ServerCharacters archive is missing: run tools/servercharacters/build.sh to write " +
		"internal/servercharacters/embedded/ServerCharacters.zip")

// The directory rather than the file: embedding a missing file is a compile error, and this
// file is gitignored. embedded/README.md is committed, so the pattern always matches
// something and the build never depends on whether the archive was produced.
//
//go:embed embedded
var embedded embed.FS

// Selects reports whether a package entry is the one this build stands in for.
func Selects(namespace, name string) bool {
	return strings.EqualFold(namespace, Namespace) && strings.EqualFold(name, Name)
}

// Archive returns the embedded package bytes, or ErrArchiveMissing.
func Archive() ([]byte, error) {
	body, err := embedded.ReadFile(embeddedName)
	if errors.Is(err, fs.ErrNotExist) {
		return nil, ErrArchiveMissing
	}
	if err != nil {
		return nil, err
	}
	if len(body) == 0 {
		return nil, ErrArchiveMissing
	}
	return body, nil
}

// ArchiveDigest is what a profile definition publishes for this package: the SHA256 and size
// of the exact bytes a client will write. Computed from the embed rather than recorded as a
// constant, so a rebuilt archive cannot disagree with what was published.
func ArchiveDigest() (string, int64, error) {
	body, err := Archive()
	if err != nil {
		return "", 0, err
	}
	sum := sha256.Sum256(body)
	return hex.EncodeToString(sum[:]), int64(len(body)), nil
}

// DescribeMismatch is the error a client shows when the definition asks for a build it does
// not carry. That is the version-skew case made visible: the operator published from a tree
// whose archive differs from the one compiled into this executable, so downloading the
// current client is the fix - and saying so beats a bare checksum failure.
func DescribeMismatch(wantSHA256 string, wantSize int64, haveSHA256 string, haveSize int64) error {
	return fmt.Errorf(
		"this client carries ServerCharacters build %s (%d bytes) but %s published %s (%d bytes); "+
			"download the current client from the portal", haveSHA256, haveSize, Name, wantSHA256, wantSize)
}
