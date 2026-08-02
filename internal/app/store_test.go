package app

import (
	"archive/zip"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestPublishRequiresVerifiedProfileDefinition(t *testing.T) {
	ctx := context.Background()
	dir := t.TempDir()
	store, err := OpenStore(filepath.Join(dir, "portal.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	release := Release{ID: "release-one", World: "Ashlands", Profile: "raiders", ClientType: "flat", Version: "1.0.0", Notes: "test"}
	if err := store.CreateRelease(ctx, release, "admin"); err != nil {
		t.Fatal(err)
	}
	if err := store.Publish(ctx, release.ID, "admin"); err == nil {
		t.Fatal("published release without a profile artifact")
	}
	path := addProfileArtifact(t, store, dir, release)
	original, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("tampered"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := store.Publish(ctx, release.ID, "admin"); err == nil {
		t.Fatal("published tampered profile artifact")
	}
	if err := os.WriteFile(path, original, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := store.Publish(ctx, release.ID, "admin"); err != nil {
		t.Fatal(err)
	}
	if err := store.AddArtifact(ctx, Artifact{ID: "installer", ReleaseID: release.ID, Kind: "installer_windows", Name: "installer.exe", SHA256: strings.Repeat("0", 64), Size: 1, Path: filepath.Join(dir, "installer.exe")}, "admin"); err == nil {
		t.Fatal("accepted retired installer artifact")
	}
}

func TestReleaseReturnsDraftWithNullablePublicationFields(t *testing.T) {
	ctx := context.Background()
	store, err := OpenStore(filepath.Join(t.TempDir(), "portal.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	want := Release{ID: "draft-one", World: "Ashlands", Profile: "raiders", ClientType: "vr", Version: "1.0.0", Notes: "draft"}
	if err := store.CreateRelease(ctx, want, "admin"); err != nil {
		t.Fatal(err)
	}
	got, err := store.Release(ctx, want.ID)
	if err != nil {
		t.Fatal(err)
	}
	if got.ID != want.ID || got.Status != Draft || got.PublishedAt != nil || got.PublishedBy != "" {
		t.Fatalf("draft release = %#v", got)
	}
}

func TestArchiveDraftPreservesAuditTrail(t *testing.T) {
	ctx := context.Background()
	store, err := OpenStore(filepath.Join(t.TempDir(), "portal.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	release := Release{ID: "discard-me", World: "Ashlands", Profile: "raiders", ClientType: "flat", Version: "1.0.0", Notes: "draft"}
	if err := store.CreateRelease(ctx, release, "admin"); err != nil {
		t.Fatal(err)
	}
	if err := store.ArchiveDraft(ctx, release.ID, "admin"); err != nil {
		t.Fatal(err)
	}
	got, err := store.Release(ctx, release.ID)
	if err != nil || got.Status != Archived {
		t.Fatalf("archived draft = %#v, %v", got, err)
	}
	if err := store.ArchiveDraft(ctx, release.ID, "admin"); err == nil {
		t.Fatal("archived an already archived release")
	}
}

func TestPublishKeepsProfilesIndependentlyCurrent(t *testing.T) {
	ctx := context.Background()
	dir := t.TempDir()
	store, err := OpenStore(filepath.Join(dir, "portal.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	oldAlpha := Release{ID: "alpha-old", World: "Ashlands", Profile: "alpha", ClientType: "flat", Version: "1.0.0", Notes: "old"}
	beta := Release{ID: "beta-current", World: "Ashlands", Profile: "beta", ClientType: "flat", Version: "1.0.0", Notes: "independent"}
	newAlpha := Release{ID: "alpha-new", World: "Ashlands", Profile: "alpha", ClientType: "flat", Version: "1.1.0", Notes: "new"}
	for _, release := range []Release{oldAlpha, beta, newAlpha} {
		if err := store.CreateRelease(ctx, release, "admin"); err != nil {
			t.Fatal(err)
		}
		addProfileArtifact(t, store, dir, release)
		if err := store.Publish(ctx, release.ID, "admin"); err != nil {
			t.Fatal(err)
		}
	}
	current, err := store.WorldReleases(ctx, "Ashlands")
	if err != nil {
		t.Fatal(err)
	}
	if len(current) != 2 {
		t.Fatalf("current releases = %#v, want two independent profiles", current)
	}
	currentIDs := map[string]bool{}
	for _, release := range current {
		currentIDs[release.ID] = true
	}
	if !currentIDs["alpha-new"] || !currentIDs["beta-current"] || currentIDs["alpha-old"] {
		t.Fatalf("current release IDs = %#v", currentIDs)
	}
	if _, err := store.CurrentRelease(ctx, "Ashlands", "alpha", "flat"); err != nil {
		t.Fatalf("alpha current release unavailable: %v", err)
	}
	if _, err := store.CurrentRelease(ctx, "Ashlands", "beta", "flat"); err != nil {
		t.Fatalf("beta current release unavailable: %v", err)
	}
	archived, err := store.ArchivedWorldReleases(ctx, "Ashlands")
	if err != nil {
		t.Fatal(err)
	}
	if len(archived) != 1 || archived[0].ID != "alpha-old" {
		t.Fatalf("archived releases = %#v, want only previous alpha", archived)
	}
}

func TestWorldReleasesOrdersProfileVariants(t *testing.T) {
	ctx := context.Background()
	dir := t.TempDir()
	store, err := OpenStore(filepath.Join(dir, "portal.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	releases := []Release{
		{ID: "vr", World: "Ashlands", Profile: "ashlands-vr", ClientType: "flat", Version: "1.0.0", Notes: "test"},
		{ID: "flatvr", World: "Ashlands", Profile: "ashlands-flatvr", ClientType: "flat", Version: "1.0.0", Notes: "test"},
		{ID: "nonvr", World: "Ashlands", Profile: "ashlands-nonvr", ClientType: "flat", Version: "1.0.0", Notes: "test"},
	}
	for _, release := range releases {
		if err := store.CreateRelease(ctx, release, "admin"); err != nil {
			t.Fatal(err)
		}
		addProfileArtifact(t, store, dir, release)
		if err := store.Publish(ctx, release.ID, "admin"); err != nil {
			t.Fatal(err)
		}
	}
	got, err := store.WorldReleases(ctx, "Ashlands")
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"nonvr", "flatvr", "vr"}
	for index, id := range want {
		if len(got) != len(want) || got[index].ID != id {
			t.Fatalf("profile order = %#v, want %v", got, want)
		}
	}
}

func TestPublishRejectsMalformedProfileDefinition(t *testing.T) {
	for _, test := range []struct {
		name    string
		mutate  func(*ProfileManifest)
		entries func(*zip.Writer) error
	}{
		{name: "wrong release scope", mutate: func(manifest *ProfileManifest) { manifest.Profile = "other" }},
		{name: "unsorted packages", mutate: func(manifest *ProfileManifest) {
			manifest.Packages = []ProfilePackage{
				{Namespace: "Acme", Name: "Second", Version: "1.0.0", Filename: "z.zip", SHA256: strings.Repeat("a", 64), Size: 1},
				{Namespace: "Acme", Name: "First", Version: "1.0.0", Filename: "a.zip", SHA256: strings.Repeat("b", 64), Size: 1},
			}
		}},
		{name: "missing config", entries: func(archive *zip.Writer) error { return nil }},
	} {
		t.Run(test.name, func(t *testing.T) {
			ctx := context.Background()
			dir := t.TempDir()
			store, err := OpenStore(filepath.Join(dir, "portal.sqlite"))
			if err != nil {
				t.Fatal(err)
			}
			defer store.Close()
			release := Release{ID: "release", World: "Ashlands", Profile: "raiders", ClientType: "vr", Version: "1.0.0", Notes: "test"}
			if err := store.CreateRelease(ctx, release, "admin"); err != nil {
				t.Fatal(err)
			}
			content := testProfileArtifact(t, release, test.mutate)
			if test.entries != nil {
				content = testProfileArtifactWithoutConfig(t, release)
			}
			artifactPath := filepath.Join(dir, "profile.zip")
			if err := os.WriteFile(artifactPath, content, 0o600); err != nil {
				t.Fatal(err)
			}
			sum := sha256.Sum256(content)
			if err := store.AddArtifact(ctx, Artifact{ID: "profile", ReleaseID: release.ID, Kind: "profile", Name: "profile.zip", SHA256: hex.EncodeToString(sum[:]), Size: int64(len(content)), Path: artifactPath}, "admin"); err != nil {
				t.Fatal(err)
			}
			if err := store.Publish(ctx, release.ID, "admin"); err == nil {
				t.Fatal("published malformed profile definition")
			}
		})
	}
}

func addProfileArtifact(t *testing.T, store *Store, dir string, release Release) string {
	t.Helper()
	content := testProfileArtifact(t, release, nil)
	path := filepath.Join(dir, release.ID+"-profile.zip")
	if err := os.WriteFile(path, content, 0o600); err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(content)
	if err := store.AddArtifact(context.Background(), Artifact{ID: release.ID + "-profile", ReleaseID: release.ID, Kind: "profile", Name: release.ID + ".zip", SHA256: hex.EncodeToString(sum[:]), Size: int64(len(content)), Path: path}, "admin"); err != nil {
		t.Fatal(err)
	}
	return path
}

func testProfileArtifact(t *testing.T, release Release, mutate func(*ProfileManifest)) []byte {
	t.Helper()
	manifest := ProfileManifest{
		Schema:     1,
		World:      release.World,
		Profile:    release.Profile,
		ClientType: release.ClientType,
		Packages: []ProfilePackage{
			{Namespace: "Acme", Name: "First", Version: "1.0.0", Filename: "Acme-First-1.0.0.zip", SHA256: strings.Repeat("a", 64), Size: 1},
			{Namespace: "Acme", Name: "Second", Version: "1.0.0", Filename: "Acme-Second-1.0.0.zip", SHA256: strings.Repeat("b", 64), Size: 1},
		},
	}
	if mutate != nil {
		mutate(&manifest)
	}
	return profileArtifactZIP(t, manifest, true)
}

func testProfileArtifactWithoutConfig(t *testing.T, release Release) []byte {
	t.Helper()
	manifest := ProfileManifest{Schema: 1, World: release.World, Profile: release.Profile, ClientType: release.ClientType}
	return profileArtifactZIP(t, manifest, false)
}

func profileArtifactZIP(t *testing.T, manifest ProfileManifest, includeConfig bool) []byte {
	t.Helper()
	var data bytes.Buffer
	archive := zip.NewWriter(&data)
	entry, err := archive.Create("profile-manifest.json")
	if err != nil {
		t.Fatal(err)
	}
	if err := json.NewEncoder(entry).Encode(manifest); err != nil {
		t.Fatal(err)
	}
	if includeConfig {
		entry, err = archive.Create("config/BepInEx.cfg")
		if err != nil {
			t.Fatal(err)
		}
		if _, err := entry.Write([]byte("enabled=true\n")); err != nil {
			t.Fatal(err)
		}
	}
	if err := archive.Close(); err != nil {
		t.Fatal(err)
	}
	return data.Bytes()
}
