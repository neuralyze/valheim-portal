package agent

import (
	"os"
	"os/exec"
	"runtime"
	"strings"
	"testing"
)

// The agent is the only package in the module that reaches for process group
// control, so it is the only one that can quietly stop cross-compiling. A
// contributor on Windows discovers that as a broken `go build ./...` on their
// first checkout, which no other test would catch, since the whole test suite
// runs on Linux.
func TestAgentCrossCompilesForWindows(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("the host build already covers GOOS=windows")
	}
	if _, err := exec.LookPath("go"); err != nil {
		t.Skip("no go toolchain on PATH to cross-compile with")
	}

	// Vet rather than build: it reports the same type errors and additionally
	// catches a fallback whose signature has drifted from its unix twin.
	cmd := exec.Command("go", "vet", ".")
	cmd.Env = append(os.Environ(), "GOOS=windows", "GOARCH=amd64")
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("GOOS=windows go vet failed, so the module does not build for Windows contributors: %v\n%s", err, strings.TrimSpace(string(out)))
	}
}
