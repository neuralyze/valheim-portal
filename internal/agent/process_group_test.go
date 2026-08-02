//go:build !windows

package agent

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

// A cancelled operation has to take the script's children with it. Killing the
// script alone would leave steamcmd or a tar mid-write against the world
// directory, and the next operation would then run against a directory that is
// still being modified.
func TestCancellingAnOperationKillsTheChildrenTheScriptSpawned(t *testing.T) {
	dir := t.TempDir()
	pidFile := filepath.Join(dir, "helper.pid")
	script := filepath.Join(dir, "spawns-a-helper.sh")
	body := "#!/bin/sh\nsleep 300 &\necho $! > " + pidFile + "\nwait\n"
	if err := os.WriteFile(script, []byte(body), 0o700); err != nil {
		t.Fatal(err)
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go func() {
		for deadline := time.Now().Add(10 * time.Second); time.Now().Before(deadline); {
			if raw, err := os.ReadFile(pidFile); err == nil && strings.TrimSpace(string(raw)) != "" {
				break
			}
			time.Sleep(10 * time.Millisecond)
		}
		cancel()
	}()

	if _, err := combinedOutput(ctx, exec.Command(script)); err != context.Canceled {
		t.Fatalf("combinedOutput returned %v, want context.Canceled", err)
	}

	raw, err := os.ReadFile(pidFile)
	if err != nil {
		t.Fatalf("the script never recorded its helper, so the test proves nothing: %v", err)
	}
	helper, err := strconv.Atoi(strings.TrimSpace(string(raw)))
	if err != nil {
		t.Fatalf("unreadable helper pid %q: %v", raw, err)
	}
	// combinedOutput returns once the script is reaped; the helper dies from
	// the same group signal but is reaped by init, so give it a moment.
	for deadline := time.Now().Add(10 * time.Second); time.Now().Before(deadline); {
		if err := syscall.Kill(helper, 0); err == syscall.ESRCH {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	_ = syscall.Kill(helper, syscall.SIGKILL)
	t.Fatalf("helper process %d outlived the cancelled operation", helper)
}
