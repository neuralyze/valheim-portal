package app

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func writeWorldEnv(t *testing.T, root, world, contents string) {
	t.Helper()
	dir := filepath.Join(root, world)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "valheim.env"), []byte(contents), 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestWorldAdminIDsReadsQuotedListAndRejectsMalformedEntries(t *testing.T) {
	root := t.TempDir()
	writeWorldEnv(t, root, "Asgard", "WORLD_NAME=\"Asgard\"\nADMINLIST_IDS=\"76561198000000010 76561198000000011 nonsense 123\"\nBEPINEX=true\n")
	writeWorldEnv(t, root, "Quiet", "WORLD_NAME=\"Quiet\"\n")

	admins := worldAdminIDs(root, "Asgard")
	if len(admins) != 2 {
		t.Fatalf("admins = %#v", admins)
	}
	for _, id := range []string{"76561198000000010", "76561198000000011"} {
		if _, ok := admins[id]; !ok {
			t.Fatalf("admin %s missing from %#v", id, admins)
		}
	}
	if len(worldAdminIDs(root, "Quiet")) != 0 {
		t.Fatal("a world without ADMINLIST_IDS reported admins")
	}
	if len(worldAdminIDs(root, "Missing")) != 0 {
		t.Fatal("a world without an env file reported admins")
	}
	if len(worldAdminIDs(root, "../escape")) != 0 {
		t.Fatal("an invalid world name was read")
	}
}

func TestPlayerCardsPivotMembershipAndMarkAdminPerWorld(t *testing.T) {
	root := t.TempDir()
	// The host currently grants in-game admin on Asgard to the first ID, and
	// nothing on Vanaheim. The portal's roles below disagree on purpose.
	writeWorldEnv(t, root, "Asgard", "ADMINLIST_IDS=\"76561198000000010\"\n")
	writeWorldEnv(t, root, "Vanaheim", "ADMINLIST_IDS=\"\"\n")

	worlds := []adminWorld{
		{PublicWorld: PublicWorld{Name: "Asgard"}},
		{PublicWorld: PublicWorld{Name: "Vanaheim"}},
	}
	members := []WorldMember{
		{World: "Asgard", SteamID: "76561198000000010", Role: "admin", GrantedBy: "operator"},
		{World: "Vanaheim", SteamID: "76561198000000010", Role: "admin", GrantedBy: "operator"},
		{World: "Asgard", SteamID: "76561198000000011", Role: "member", GrantedBy: "operator"},
	}
	identities := []SteamIdentity{
		{SteamID: "76561198000000010", PersonaName: "Odinsson", LastSeenAt: time.Now()},
	}

	cards := playerCards(worlds, members, identities, root)
	if len(cards) != 2 {
		t.Fatalf("cards = %#v", cards)
	}

	// Most-granted player sorts first.
	first := cards[0]
	if first.SteamID != "76561198000000010" || first.DisplayName != "Odinsson" || !first.SignedIn {
		t.Fatalf("first card = %#v", first)
	}
	// Both grants intend admin, so both count, regardless of the host state.
	if len(first.Worlds) != 2 || first.AdminWorlds != 2 {
		t.Fatalf("first card worlds = %#v adminWorlds=%d", first.Worlds, first.AdminWorlds)
	}
	// Asgard already matches the host: intended admin, host admin, applied.
	if asgard := first.Worlds[0]; asgard.World != "Asgard" || !asgard.IsAdmin || !asgard.HostAdmin || asgard.Pending() {
		t.Fatalf("expected applied admin on Asgard: %#v", asgard)
	}
	// Vanaheim intends admin but the host has not been given the list yet.
	if vanaheim := first.Worlds[1]; vanaheim.World != "Vanaheim" || !vanaheim.IsAdmin || vanaheim.HostAdmin || !vanaheim.Pending() {
		t.Fatalf("expected pending admin on Vanaheim: %#v", vanaheim)
	}
	if len(first.OtherWorlds) != 0 {
		t.Fatalf("a fully granted player offered more worlds: %#v", first.OtherWorlds)
	}

	// A member who never signed in still gets a card, and the world it lacks is
	// offered for a one-click grant.
	second := cards[1]
	if second.SteamID != "76561198000000011" || second.SignedIn {
		t.Fatalf("second card = %#v", second)
	}
	if second.DisplayName != "Unnamed player" {
		t.Fatalf("unnamed player display = %q", second.DisplayName)
	}
	if second.AdminWorlds != 0 {
		t.Fatalf("second card claimed admin: %#v", second)
	}
	// A plain member matches a host that does not list them: nothing pending.
	if asgard := second.Worlds[0]; asgard.IsAdmin || asgard.HostAdmin || asgard.Pending() {
		t.Fatalf("plain member reported drift: %#v", asgard)
	}
	if len(second.OtherWorlds) != 1 || second.OtherWorlds[0] != "Vanaheim" {
		t.Fatalf("second card other worlds = %#v", second.OtherWorlds)
	}
}

func TestPlayerCardsIncludeSignedInAccountWithoutAnyWorldGrant(t *testing.T) {
	root := t.TempDir()
	writeWorldEnv(t, root, "Asgard", "ADMINLIST_IDS=\"\"\n")
	cards := playerCards(
		[]adminWorld{{PublicWorld: PublicWorld{Name: "Asgard"}}},
		nil,
		[]SteamIdentity{{SteamID: "76561198000000000", Label: "Dave from work"}},
		root,
	)
	if len(cards) != 1 {
		t.Fatalf("cards = %#v", cards)
	}
	if cards[0].DisplayName != "Dave from work" || len(cards[0].Worlds) != 0 {
		t.Fatalf("card = %#v", cards[0])
	}
	if len(cards[0].OtherWorlds) != 1 || cards[0].OtherWorlds[0] != "Asgard" {
		t.Fatalf("a player with no access was not offered a world: %#v", cards[0].OtherWorlds)
	}
}
