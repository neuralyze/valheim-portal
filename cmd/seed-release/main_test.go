package main

import (
	app "github.com/neuralyze/valheim-portal/internal/app"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// stage names the copy it keeps. A republish hands it the file it staged last time, so
// prefixing unconditionally grew the name by "flat_companion-" on every publish: nine
// republishes produced a 205-character filename, and the profile builder refuses any
// artifact filename over 180 characters. The fleet reached that limit on 2026-08-17 and
// no Flat edition could be published at all until the prefix stopped accreting.
func TestStageDoesNotAccumulateItsOwnPrefix(t *testing.T) {
	root := t.TempDir()
	source := filepath.Join(root, "ValheimVR-a10f24fc6099-flat-companion.zip")
	if err := os.WriteFile(source, []byte("companion bytes"), 0o600); err != nil {
		t.Fatal(err)
	}

	first, err := stage(root, "world-flatvr-1.0.0", "flat_companion", source)
	if err != nil {
		t.Fatal(err)
	}
	if want := "flat_companion-ValheimVR-a10f24fc6099-flat-companion.zip"; filepath.Base(first.Path) != want {
		t.Fatalf("first staged name = %q, want %q", filepath.Base(first.Path), want)
	}

	// The second publish carries the first one's file forward, which is the real path
	// republish-profiles.sh takes when it reads the newest artifact out of the database.
	second, err := stage(root, "world-flatvr-1.0.1", "flat_companion", first.Path)
	if err != nil {
		t.Fatal(err)
	}
	if filepath.Base(second.Path) != filepath.Base(first.Path) {
		t.Fatalf("second staged name = %q, want %q", filepath.Base(second.Path), filepath.Base(first.Path))
	}
	if strings.Count(filepath.Base(second.Path), "flat_companion-") != 1 {
		t.Fatalf("prefix accumulated: %q", filepath.Base(second.Path))
	}

	// Ten more rounds stay put, so the name cannot drift toward the builder's cap again.
	current := second.Path
	for round := 0; round < 10; round++ {
		next, err := stage(root, "world-flatvr-2."+string(rune('0'+round))+".0", "flat_companion", current)
		if err != nil {
			t.Fatal(err)
		}
		current = next.Path
	}
	if base := filepath.Base(current); base != filepath.Base(first.Path) || len(base) > 180 {
		t.Fatalf("name after twelve publishes = %q (%d chars)", base, len(base))
	}
}

// The other kinds share the same staging path, so they inherit the same guarantee.
func TestStageKeepsEachKindsOwnPrefixExactlyOnce(t *testing.T) {
	root := t.TempDir()
	for _, kind := range []string{"profile", "vr_runtime", "diag_plugin"} {
		source := filepath.Join(root, kind+"-payload.zip")
		if err := os.WriteFile(source, []byte(kind), 0o600); err != nil {
			t.Fatal(err)
		}
		staged, err := stage(root, "release-"+kind, kind, source)
		if err != nil {
			t.Fatal(err)
		}
		if want := kind + "-payload.zip"; filepath.Base(staged.Path) != want {
			t.Fatalf("%s staged as %q, want %q", kind, filepath.Base(staged.Path), want)
		}
	}
}

// The builder records the companion's filename into the definition and the store compares
// that string against the staged artifact's Name. A republish hands both sides the file
// staged last time, whose basename already carries the prefix, so the two must reach the
// same answer or the publish is refused with "does not match the profile manifest".
func TestStagedNameMatchesWhatTheBuilderRecords(t *testing.T) {
	root := t.TempDir()
	carried := filepath.Join(root, "flat_companion-ValheimVR-flat-companion.zip")
	if err := os.WriteFile(carried, []byte("companion"), 0o600); err != nil {
		t.Fatal(err)
	}

	staged, err := stage(root, "world-vr-flat-1.0.2", "flat_companion", carried)
	if err != nil {
		t.Fatal(err)
	}
	recorded := app.StagedArtifactName("flat_companion", carried)

	if staged.Name != recorded {
		t.Fatalf("artifact name %q, definition records %q", staged.Name, recorded)
	}
	if filepath.Base(staged.Path) != "flat_companion-"+recorded {
		t.Fatalf("on-disk name %q does not carry exactly one prefix", filepath.Base(staged.Path))
	}
}
