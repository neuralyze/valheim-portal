package main

import (
	"errors"
	"io/fs"
	"os"
	"path/filepath"
	"testing"
)

// These tests defend the destructive fallback in activateGeneration, which deletes
// the active generation when Windows denies renaming it aside. That path can destroy
// a working profile, so each guard is exercised directly. The real trigger - a rename
// denied by an ACL while RemoveAll on the same directory succeeds - cannot be
// reproduced on Linux, so the rename is injected through the renameGeneration seam.

func stubRename(t *testing.T, stub func(from, to string) error) {
	t.Helper()
	original := renameGeneration
	renameGeneration = stub
	t.Cleanup(func() { renameGeneration = original })
}

func seedGeneration(t *testing.T, root, name, marker string) string {
	t.Helper()
	dir := filepath.Join(root, name)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "marker"), []byte(marker), 0o600); err != nil {
		t.Fatal(err)
	}
	return dir
}

func markerOf(t *testing.T, dir string) string {
	t.Helper()
	data, err := os.ReadFile(filepath.Join(dir, "marker"))
	if err != nil {
		return ""
	}
	return string(data)
}

// A transient lock must never cost the user their installed profile: retrying a
// failed sync is free, recovering a deleted generation is not.
func TestActivateGenerationKeepsActiveWhenRenameFailsWithoutPermissionDenial(t *testing.T) {
	root := t.TempDir()
	active := seedGeneration(t, root, "active", "working")
	next := seedGeneration(t, root, ".next-1", "candidate")

	stubRename(t, func(from, to string) error {
		if from == active {
			return errors.New("the process cannot access the file because it is being used by another process")
		}
		return os.Rename(from, to)
	})

	err := activateGeneration(root, next, profileState{Schema: 1})
	if err == nil {
		t.Fatal("activation reported success despite a failed rename")
	}
	if got := markerOf(t, active); got != "working" {
		t.Fatalf("active generation was destroyed by a non-permission error: marker=%q", got)
	}
	if _, statErr := os.Stat(next); statErr != nil {
		t.Fatalf("staged generation was discarded: %v", statErr)
	}
}

// On a genuine permission denial the fallback deletes active. If the follow-up move
// then fails too - the likely case, since both need the same directory access - the
// staged tree must survive rather than leaving no profile at all.
func TestActivateGenerationPreservesStagedTreeWhenActivationFailsAfterDelete(t *testing.T) {
	root := t.TempDir()
	active := seedGeneration(t, root, "active", "working")
	next := seedGeneration(t, root, ".next-1", "candidate")

	stubRename(t, func(from, to string) error {
		if from == active {
			return &os.LinkError{Op: "rename", Old: from, New: to, Err: fs.ErrPermission}
		}
		if from == next && to == filepath.Join(root, "active") {
			return &os.LinkError{Op: "rename", Old: from, New: to, Err: fs.ErrPermission}
		}
		return os.Rename(from, to)
	})

	err := activateGeneration(root, next, profileState{Schema: 1})
	if err == nil {
		t.Fatal("activation reported success despite a failed move into place")
	}
	// The staged tree is parked under the rollback name, so a complete copy survives.
	if got := markerOf(t, filepath.Join(root, "previous")); got != "candidate" {
		t.Fatalf("staged generation was not preserved: previous marker=%q, err=%v", got, err)
	}
	entries, readErr := os.ReadDir(root)
	if readErr != nil {
		t.Fatal(readErr)
	}
	if len(entries) == 0 {
		t.Fatal("profile directory was left completely empty")
	}
}

// After a destructive fallback there is no fresh rollback copy, so pruning must not
// remove the last older generation.
func TestActivateGenerationKeepsOlderRollbackAfterDestructiveFallback(t *testing.T) {
	root := t.TempDir()
	active := seedGeneration(t, root, "active", "working")
	next := seedGeneration(t, root, ".next-1", "candidate")
	older := seedGeneration(t, root, "previous-1700000000", "older")

	stubRename(t, func(from, to string) error {
		if from == active {
			return &os.LinkError{Op: "rename", Old: from, New: to, Err: fs.ErrPermission}
		}
		return os.Rename(from, to)
	})

	if err := activateGeneration(root, next, profileState{Schema: 1}); err != nil {
		t.Fatalf("activation failed: %v", err)
	}
	if got := markerOf(t, filepath.Join(root, "active")); got != "candidate" {
		t.Fatalf("new generation is not active: marker=%q", got)
	}
	if got := markerOf(t, older); got != "older" {
		t.Fatalf("pruning removed the last rollback source after a destructive fallback: marker=%q", got)
	}
}

// The happy path must still prune superseded generations, otherwise they accumulate.
func TestActivateGenerationPrunesOlderGenerationsOnNormalActivation(t *testing.T) {
	root := t.TempDir()
	seedGeneration(t, root, "active", "working")
	next := seedGeneration(t, root, ".next-1", "candidate")
	stale := seedGeneration(t, root, "previous-1700000000", "stale")

	if err := activateGeneration(root, next, profileState{Schema: 1}); err != nil {
		t.Fatalf("activation failed: %v", err)
	}
	if got := markerOf(t, filepath.Join(root, "active")); got != "candidate" {
		t.Fatalf("new generation is not active: marker=%q", got)
	}
	if got := markerOf(t, filepath.Join(root, "previous")); got != "working" {
		t.Fatalf("rollback copy missing: marker=%q", got)
	}
	if _, err := os.Stat(stale); !os.IsNotExist(err) {
		t.Fatalf("stale generation was not pruned: %v", err)
	}
	if _, err := os.Stat(filepath.Join(root, stateFilename)); err != nil {
		t.Fatalf("state file was not written: %v", err)
	}
}
