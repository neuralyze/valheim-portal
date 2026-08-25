package app

import (
	"archive/zip"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// valheimVRPackage is one of the packages internal/valheimvr declares to be part
// of the ValheimVR mod stack. A profile carrying it installs VR whatever its
// release calls itself.
var valheimVRPackage = ProfilePackage{
	Namespace: "ValheimVR", Name: "ValheimVR", Version: "1.0.0",
	Filename: "ValheimVR-ValheimVR-1.0.0.zip", SHA256: strings.Repeat("c", 64), Size: 1,
}

var ordinaryPackage = ProfilePackage{
	Namespace: "Azumatt", Name: "AzuCraftyBoxes", Version: "1.0.0",
	Filename: "Azumatt-AzuCraftyBoxes-1.0.0.zip", SHA256: strings.Repeat("d", 64), Size: 1,
}

// writeFlatCompanionArtifact stores a minimal valid companion and returns the manifest
// block that names it. ValidateFlatCompanionArtifact insists on ValheimVRMod.dll, which
// is exactly why the companion is proof that an edition installs ValheimVR.
func writeFlatCompanionArtifact(t *testing.T, server *Server, release Release) *ProfileCompanion {
	t.Helper()
	var buffer bytes.Buffer
	archive := zip.NewWriter(&buffer)
	entry, err := archive.Create("BepInEx/plugins/ValheimVRMod.dll")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := entry.Write([]byte("MZ")); err != nil {
		t.Fatal(err)
	}
	if err := archive.Close(); err != nil {
		t.Fatal(err)
	}
	name := release.ID + "-companion.zip"
	path := filepath.Join(server.cfg.ArtifactRoot, name)
	if err := os.WriteFile(path, buffer.Bytes(), 0o600); err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(buffer.Bytes())
	digest := hex.EncodeToString(sum[:])
	artifact := Artifact{
		ID: release.ID + "-companion", ReleaseID: release.ID, Kind: "flat_companion",
		Name: name, SHA256: digest, Size: int64(buffer.Len()), Path: path,
	}
	if err := server.store.AddArtifact(context.Background(), artifact, "admin"); err != nil {
		t.Fatal(err)
	}
	return &ProfileCompanion{Filename: name, SHA256: digest, Size: int64(buffer.Len())}
}

func publishProfileWithPackages(t *testing.T, server *Server, release Release, packages []ProfilePackage) {
	t.Helper()
	publishProfileDefinition(t, server, release, func(manifest *ProfileManifest) { manifest.Packages = packages })
}

// publishProfileDefinition publishes one release whose packaged manifest is whatever
// the caller makes it. The companion field matters as much as the package list now:
// a Flat edition can be VR-capable through the companion alone.
func publishProfileDefinition(t *testing.T, server *Server, release Release, shape func(*ProfileManifest)) {
	t.Helper()
	ctx := context.Background()
	if err := server.store.CreateRelease(ctx, release, "admin"); err != nil {
		t.Fatal(err)
	}
	content := testProfileArtifact(t, release, shape)
	path := filepath.Join(server.cfg.ArtifactRoot, release.ID+"-profile.zip")
	if err := os.WriteFile(path, content, 0o600); err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(content)
	artifact := Artifact{
		ID: release.ID + "-profile", ReleaseID: release.ID, Kind: "profile", Name: release.ID + ".zip",
		SHA256: hex.EncodeToString(sum[:]), Size: int64(len(content)), Path: path,
	}
	if err := server.store.AddArtifact(ctx, artifact, "admin"); err != nil {
		t.Fatal(err)
	}
	if release.ClientType == "vr" {
		addVRRuntimeArtifact(t, server.store, server.cfg.ArtifactRoot, release)
	}
	if err := server.store.Publish(ctx, release.ID, "admin"); err != nil {
		t.Fatal(err)
	}
}

// threeProfileWorld publishes the shape every real world has: two client_type
// "flat" profiles that differ only in whether their definition carries
// ValheimVR, plus the headset profile.
func threeProfileWorld(t *testing.T, server *Server) {
	t.Helper()
	describedTestWorld(t, server)
	publishProfileWithPackages(t, server,
		Release{ID: "nonvr", World: describedWorld, Profile: "midgard-nonvr", ClientType: "flat", Version: "2.1.3"},
		[]ProfilePackage{ordinaryPackage})
	publishProfileWithPackages(t, server,
		Release{ID: "flatvr", World: describedWorld, Profile: "midgard-flatvr", ClientType: "flat", Version: "2.1.3"},
		[]ProfilePackage{ordinaryPackage, valheimVRPackage})
	publishProfileWithPackages(t, server,
		Release{ID: "vr", World: describedWorld, Profile: "midgard-vr", ClientType: "vr", Version: "2.1.3"},
		[]ProfilePackage{ordinaryPackage, valheimVRPackage})
}

// The two flat profiles are identical to the release row: same world, same
// client type, same version. Only the packaged definition says that one of them
// installs the whole ValheimVR stack, so the card copy has to come from there.
func TestWorldPageDistinguishesTheThreeProfilesFromTheirDefinitions(t *testing.T) {
	server := testServer(t)
	threeProfileWorld(t, server)
	page := playerPage(t, server, "/worlds/"+describedWorld)

	for _, want := range []string{
		"<h2>Desktop</h2>",
		"<h2>Desktop, VR-compatible</h2>",
		"<h2>VR headset</h2>",
		"Play on a monitor. No VR mods installed.",
		"Play on a monitor alongside VR players. Installs ValheimVR in flat mode.",
		"Requires SteamVR and a headset.",
		"Not sure? Choose Desktop.",
	} {
		if !strings.Contains(page, want) {
			t.Fatalf("world page omits %q: %s", want, page)
		}
	}

	// The slug stays visible for support, but never as the card heading.
	for _, slug := range []string{"midgard-nonvr", "midgard-flatvr", "midgard-vr"} {
		if !strings.Contains(page, `<code class="profile-slug">`+slug+`</code>`) {
			t.Fatalf("world page drops the %s slug: %s", slug, page)
		}
		if strings.Contains(page, "<h2>"+slug+"</h2>") {
			t.Fatalf("%s is still a card heading: %s", slug, page)
		}
	}

	// Exactly one card is recommended, and it is the one a player who does not
	// know the difference should take.
	if count := strings.Count(page, "profile-card-recommended"); count != 1 {
		t.Fatalf("recommended cards = %d: %s", count, page)
	}
	if !strings.Contains(page[:strings.Index(page, "<h2>Desktop</h2>")], "profile-card-recommended") {
		t.Fatalf("the Desktop card is not the recommended one: %s", page)
	}
	// Every install button looks equally usable, because it is. A filled button beside
	// two transparent outlined ones does not read as "recommended" -- operators reported
	// players taking it to mean the other two profiles were unavailable to them, which
	// on a mostly-VR server hid the profile most of them wanted. The recommendation is
	// carried by the card, not by demoting the action.
	if secondary := strings.Count(page, "sync-secondary"); secondary != 0 {
		t.Fatalf("install buttons are still demoted in %d places: %s", secondary, page)
	}

	// The old chips restated client_type, which called two different profiles
	// the same thing.
	if strings.Contains(page, `<span class="chip">flat</span>`) {
		t.Fatalf("world page still labels profiles by client type: %s", page)
	}
}

// Same release rows, opposite definitions: flipping only the packaged manifest
// has to flip the card. Nothing else in the release differs.
func TestProfileClassificationFollowsTheManifestNotTheClientType(t *testing.T) {
	server := testServer(t)
	describedTestWorld(t, server)
	release := Release{ID: "flat-with-vr", World: describedWorld, Profile: "midgard-flatvr", ClientType: "flat", Version: "1.0.0"}
	publishProfileWithPackages(t, server, release, []ProfilePackage{ordinaryPackage, valheimVRPackage})

	installsVR, err := server.releaseInstallsValheimVR(context.Background(), release)
	if err != nil || !installsVR {
		t.Fatalf("flat release carrying ValheimVR = %v, %v", installsVR, err)
	}
	if kind, installsVHVR := server.profileKindOf(context.Background(), release); kind != profileDesktopVR || !installsVHVR {
		t.Fatalf("flat release carrying ValheimVR classified as %q, installs = %v", kind.Title(), installsVHVR)
	}

	plain := Release{ID: "flat-without-vr", World: describedWorld, Profile: "midgard-nonvr", ClientType: "flat", Version: "1.0.0"}
	publishProfileWithPackages(t, server, plain, []ProfilePackage{ordinaryPackage})
	if kind, installsVHVR := server.profileKindOf(context.Background(), plain); kind != profileDesktop || installsVHVR {
		t.Fatalf("flat release without ValheimVR classified as %q, installs = %v", kind.Title(), installsVHVR)
	}
}

// The split into flat/vr/admin primaries moved the geekstreet VR fixes to the headset
// edition only, so a vr-flat release carries ValheimVR solely as the companion. Reading
// packages alone classified it as a plain Desktop profile and hid VR from the players it
// was built for.
func TestFlatEditionWithOnlyTheCompanionStillCountsAsVRCapable(t *testing.T) {
	server := testServer(t)
	describedTestWorld(t, server)
	release := Release{ID: "vr-flat", World: describedWorld, Profile: "midgard-vr-flat", ClientType: "flat", Version: "1.0.0"}
	// The store cross-checks the declared companion against a real artifact, and an
	// artifact needs its release row, so the archive is written from inside the shape
	// callback - which runs after the release exists.
	publishProfileDefinition(t, server, release, func(manifest *ProfileManifest) {
		manifest.Packages = []ProfilePackage{ordinaryPackage}
		manifest.Companion = writeFlatCompanionArtifact(t, server, release)
	})

	if kind, installsVHVR := server.profileKindOf(context.Background(), release); kind != profileDesktopVR || !installsVHVR {
		t.Fatalf("vr-flat edition classified as %q, installs = %v, want Desktop VR carrying ValheimVR", kind.Title(), installsVHVR)
	}
}

// A release whose client type and definition disagree is mis-built. Guessing
// which half is right is how a VR player ends up on a stripped profile.
func TestMisbuiltVRReleaseIsNotPresentedAsAWorkingChoice(t *testing.T) {
	server := testServer(t)
	describedTestWorld(t, server)
	release := Release{ID: "vr-without-vr", World: describedWorld, Profile: "midgard-vr", ClientType: "vr", Version: "1.0.0"}
	publishProfileWithPackages(t, server, release, []ProfilePackage{ordinaryPackage})
	if kind, installsVHVR := server.profileKindOf(context.Background(), release); kind != profileUnverified || installsVHVR {
		t.Fatalf("a VR release without ValheimVR was presented as %q, installs = %v", kind.Title(), installsVHVR)
	}
}

// The admin edition is a fourth card kind because two cards both reading
// "Desktop, VR-compatible" left a player choosing between them by slug. It is offered
// only to an admin login: its tools do nothing without server-side admin rights, but an
// extra identical-looking download beside the one a player wants is the whole problem.
func TestAdminEditionIsItsOwnKindAndOnlyShownToAdmins(t *testing.T) {
	server := testServer(t)
	describedTestWorld(t, server)
	release := Release{ID: "vr-flat-admin", World: describedWorld, Profile: "midgard-vr-flat-admin", ClientType: "flat", Version: "1.0.0"}
	release.Audience = "admin"
	publishProfileDefinition(t, server, release, func(manifest *ProfileManifest) {
		manifest.Packages = []ProfilePackage{ordinaryPackage}
		manifest.Companion = writeFlatCompanionArtifact(t, server, release)
	})

	// The admin edition carries the companion, so it owes the GPL offer even though its
	// kind is decided by audience and says nothing about ValheimVR either way.
	if kind, installsVHVR := server.profileKindOf(context.Background(), release); kind != profileAdmin || !installsVHVR {
		t.Fatalf("admin edition classified as %q, installs = %v", kind.Title(), installsVHVR)
	}

	hidden, err := server.profileReleaseCards(context.Background(), []Release{release}, false)
	if err != nil {
		t.Fatal(err)
	}
	if len(hidden) != 0 {
		t.Fatalf("admin edition offered to a player: %+v", hidden)
	}

	shown, err := server.profileReleaseCards(context.Background(), []Release{release}, true)
	if err != nil {
		t.Fatal(err)
	}
	if len(shown) != 1 || shown[0].Title != "Desktop, admin tools" {
		t.Fatalf("admin login sees %+v", shown)
	}
}

// A player edition and the admin edition differ only in that field, so the field is the
// only thing that may decide the card.
func TestTheSameBuildWithoutTheAdminAudienceIsAnOrdinaryCard(t *testing.T) {
	server := testServer(t)
	describedTestWorld(t, server)
	release := Release{ID: "vr-flat-player", World: describedWorld, Profile: "midgard-vr-flat", ClientType: "flat", Version: "1.0.0"}
	release.Audience = "player"
	publishProfileDefinition(t, server, release, func(manifest *ProfileManifest) {
		manifest.Packages = []ProfilePackage{ordinaryPackage}
		manifest.Companion = writeFlatCompanionArtifact(t, server, release)
	})

	cards, err := server.profileReleaseCards(context.Background(), []Release{release}, false)
	if err != nil {
		t.Fatal(err)
	}
	if len(cards) != 1 || cards[0].Kind != profileDesktopVR {
		t.Fatalf("player edition = %+v", cards)
	}
}

// An admin build for a headset is not a shape the catalog produces. Rendering it as an
// ordinary headset card would hand the console to every VR player.
func TestAdminAudienceOnAHeadsetReleaseIsRefused(t *testing.T) {
	server := testServer(t)
	describedTestWorld(t, server)
	// Set on the caller's copy too: profileKindOf classifies the Release it is given, and
	// Release is a value, so the helper's copy is not the one under test.
	release := Release{ID: "vr-admin", World: describedWorld, Profile: "midgard-vr", ClientType: "vr", Version: "1.0.0", Audience: "admin"}
	publishProfileWithPackagesAudience(t, server, release, []ProfilePackage{ordinaryPackage, valheimVRPackage}, "admin")

	// Refused as a card, but the VR fact stands on its own: the definition does carry
	// ValheimVR, so the release still owes the source offer wherever it is handed out.
	if kind, installsVHVR := server.profileKindOf(context.Background(), release); kind != profileUnverified || !installsVHVR {
		t.Fatalf("admin headset release classified as %q, installs = %v", kind.Title(), installsVHVR)
	}
}

func publishProfileWithPackagesAudience(t *testing.T, server *Server, release Release, packages []ProfilePackage, audience string) {
	t.Helper()
	release.Audience = audience
	publishProfileDefinition(t, server, release, func(manifest *ProfileManifest) {
		manifest.Packages = packages
	})
}
