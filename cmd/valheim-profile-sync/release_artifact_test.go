package main

import (
	"encoding/binary"
	"errors"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

// dist/ValheimProfileSync.exe is tracked and is what the portal hands players,
// so it is part of the deliverable rather than a local build output. Two
// properties are invisible on disk and both come from scripts/build-windows-
// client.sh: the PE subsystem, which decides whether Windows attaches an empty
// console window beside the application, and the linker-stamped build identity,
// without which a support bundle cannot be matched to a release. A plain
// `go build` silently produces neither, so this test guards the artifact.

// stampPattern matches the git-describe identity the build script passes to
// -X internal/version.Version. The commit suffix is required: a bare semantic
// version also appears in dependency metadata, so accepting it would let an
// unstamped build pass.
var stampPattern = regexp.MustCompile(`v[0-9]+\.[0-9]+\.[0-9]+-[0-9]+-g([0-9a-f]{7,40})(?:-dirty)?`)

// A source tarball carries neither dist/ nor .git, and `go test ./...` is the
// first thing the development docs ask a newcomer to run. Missing evidence is
// not a failed release gate, so those two cases skip with a reason; wherever
// the evidence is present the gate below runs in full.
func TestPublishedWindowsClientIsAGUIBuildWithAStampedIdentity(t *testing.T) {
	dist := filepath.Join("..", "..", "dist")
	if _, err := os.Stat(dist); errors.Is(err, fs.ErrNotExist) {
		t.Skipf("%s does not exist, so this tree publishes no Windows client; build one with scripts/build-windows-client.sh", dist)
	}

	path := filepath.Join(dist, "ValheimProfileSync.exe")
	image, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("the published Windows client is missing; build it with scripts/build-windows-client.sh: %v", err)
	}

	subsystem, err := peSubsystemOf(image)
	if err != nil {
		t.Fatalf("%s is not a readable PE image: %v", path, err)
	}
	const guiSubsystem = 2
	if subsystem != guiSubsystem {
		t.Fatalf("%s has PE subsystem %d, want %d (GUI): a console build opens an empty console window beside the application. Rebuild with scripts/build-windows-client.sh", path, subsystem, guiSubsystem)
	}

	stamp := stampPattern.FindSubmatch(image)
	if stamp == nil {
		t.Fatalf("%s carries no git-describe build identity, so it reports version %q and cannot be matched to a release. Rebuild with scripts/build-windows-client.sh", path, "dev")
	}

	// -s -w keep the symbol table out of a player download. The linker always
	// emits the .symtab section header, so size is the signal: a stripped build
	// leaves a 512-byte stub where an unstripped one carries over a megabyte.
	const maxStrippedSymtab = 64 << 10
	sections, err := peSections(image)
	if err != nil {
		t.Fatalf("%s has an unreadable section table: %v", path, err)
	}
	if size := sections[".symtab"]; size > maxStrippedSymtab {
		t.Fatalf("%s carries a %d-byte symbol table, so it was not linked with -s -w and is %d bytes larger than a player needs. Rebuild with scripts/build-windows-client.sh", path, size, len(image))
	}

	// The stamp must name a commit that exists here, which a dependency version
	// string or a binary from another tree cannot satisfy. It runs last so that
	// a tree without git metadata still gets every check above it.
	repo := filepath.Join("..", "..")
	if _, err := exec.LookPath("git"); err != nil {
		t.Skipf("git is not installed, so the identity %q cannot be checked against this repository's history", stamp[0])
	}
	if err := exec.Command("git", "-C", repo, "rev-parse", "--git-dir").Run(); err != nil {
		t.Skipf("%s carries no git metadata, so the identity %q cannot be checked against this repository's history; this is a source tarball rather than a clone", repo, stamp[0])
	}
	commit := string(stamp[1])
	if err := exec.Command("git", "-C", repo, "cat-file", "-e", commit+"^{commit}").Run(); err != nil {
		t.Fatalf("%s is stamped %q, but %s is not a commit in this repository: the artifact was not built from this tree", path, stamp[0], commit)
	}
}

func peSubsystemOf(image []byte) (uint16, error) {
	optional, err := peOptionalHeaderOffset(image)
	if err != nil {
		return 0, err
	}
	if optional+70 > len(image) {
		return 0, errTruncatedPE
	}
	return binary.LittleEndian.Uint16(image[optional+68:]), nil
}

// peSections maps section names to their raw sizes on disk.
func peSections(image []byte) (map[string]uint32, error) {
	optional, err := peOptionalHeaderOffset(image)
	if err != nil {
		return nil, err
	}
	coff := optional - 20
	count := int(binary.LittleEndian.Uint16(image[coff+2:]))
	table := optional + int(binary.LittleEndian.Uint16(image[coff+16:]))
	sections := make(map[string]uint32, count)
	for i := range count {
		entry := table + 40*i
		if entry+40 > len(image) {
			return nil, errTruncatedPE
		}
		name := strings.TrimRight(string(image[entry:entry+8]), "\x00")
		sections[name] = binary.LittleEndian.Uint32(image[entry+16:])
	}
	return sections, nil
}

func peOptionalHeaderOffset(image []byte) (int, error) {
	if len(image) < 0x40 || image[0] != 'M' || image[1] != 'Z' {
		return 0, errNotPE
	}
	nt := int(binary.LittleEndian.Uint32(image[0x3c:]))
	if nt < 0 || nt+24 > len(image) || string(image[nt:nt+4]) != "PE\x00\x00" {
		return 0, errNotPE
	}
	return nt + 4 + 20, nil
}

var (
	errNotPE       = peError("not a PE image")
	errTruncatedPE = peError("truncated PE image")
)

type peError string

func (e peError) Error() string { return string(e) }
