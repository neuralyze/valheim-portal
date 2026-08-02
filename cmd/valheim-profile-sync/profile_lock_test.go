package main

import (
	"os"
	"path/filepath"
	"strconv"
	"testing"
)

// Closing the launcher mid-sync leaves the lock file behind, because an
// exclusive-create file has nothing to release it. Every later run then failed
// with "another profile synchronization is already active" until it was deleted
// by hand, which is the bug this recovers from.
func TestAcquireProfileLockClearsALockLeftByADeadLauncher(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, ".sync.lock")
	// A pid that cannot be running: nothing may claim pid 0, and the writer records
	// its own pid, so this is exactly the shape a killed launcher leaves.
	if err := os.WriteFile(path, []byte("0\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	lock, err := acquireProfileLock(root)
	if err != nil {
		t.Fatalf("a lock from a dead launcher was not recovered: %v", err)
	}
	defer lock.Close()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if got := string(data); got != strconv.Itoa(os.Getpid())+"\n" {
		t.Fatalf("lock file records %q, want this process", got)
	}
}

func TestAcquireProfileLockClearsAnUnparseableLock(t *testing.T) {
	root := t.TempDir()
	// Dying between creating the file and writing the pid leaves it empty.
	if err := os.WriteFile(filepath.Join(root, ".sync.lock"), nil, 0o600); err != nil {
		t.Fatal(err)
	}
	lock, err := acquireProfileLock(root)
	if err != nil {
		t.Fatalf("an empty lock file was not recovered: %v", err)
	}
	lock.Close()
}

// The recovery must not become a way to trample a sync that is genuinely running.
func TestAcquireProfileLockRespectsALiveHolder(t *testing.T) {
	root := t.TempDir()
	// A pid that is definitely alive but is not this process's lock bookkeeping:
	// the parent of this test process.
	alive := os.Getppid()
	if alive <= 0 || !processAliveByPID(alive) {
		t.Skip("no live pid available to stand in for another launcher")
	}
	if err := os.WriteFile(filepath.Join(root, ".sync.lock"), []byte(strconv.Itoa(alive)+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := acquireProfileLock(root); err != errProfileLocked {
		t.Fatalf("a live launcher's lock was stolen: err = %v", err)
	}
}

func TestProfileLockRoundTripReleasesTheFile(t *testing.T) {
	root := t.TempDir()
	lock, err := acquireProfileLock(root)
	if err != nil {
		t.Fatal(err)
	}
	if err := lock.Close(); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(root, ".sync.lock")); !os.IsNotExist(err) {
		t.Fatalf("lock file survived Close: %v", err)
	}
	// And the profile is immediately usable again.
	again, err := acquireProfileLock(root)
	if err != nil {
		t.Fatalf("reacquire after release: %v", err)
	}
	again.Close()
}
