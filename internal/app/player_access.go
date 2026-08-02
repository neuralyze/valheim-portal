package app

import (
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
)

// adminListSetting matches the ADMINLIST_IDS assignment in a world's
// valheim.env. The container regenerates config_merged/adminlist.txt from this
// value on every boot, so the env file is the authoritative in-game adminlist.
var adminListSetting = regexp.MustCompile(`(?m)^[ \t]*ADMINLIST_IDS[ \t]*=[ \t]*(.*)$`)

// maxWorldEnvSize bounds the read of an untrusted world env file.
const maxWorldEnvSize = 64 << 10

// playerWorldAccess is one world a player may join. IsAdmin is what the portal
// intends (the grant's role); HostAdmin is what the world's ADMINLIST_IDS
// actually says right now. They differ until the access lists are applied.
type playerWorldAccess struct {
	World     string
	IsAdmin   bool
	HostAdmin bool
	GrantedBy string
}

// Pending reports that this world's in-game adminlist does not yet match the
// portal's intent for this player.
func (a playerWorldAccess) Pending() bool { return a.IsAdmin != a.HostAdmin }

// playerCard aggregates every portal fact about one Steam ID so the admin
// surface can present a player rather than a row per world membership.
type playerCard struct {
	SteamID     string
	DisplayName string
	PersonaName string
	Label       string
	LastSeenAt  time.Time
	SignedIn    bool
	Worlds      []playerWorldAccess
	OtherWorlds []string
	AdminWorlds int
}

// worldAdminIDs reads the world's ADMINLIST_IDS. The world root is mounted
// read-only, so this reports the real in-game adminlist without involving the
// privileged agent. A missing or unreadable env file yields no admins rather
// than an error, because an unregistered or half-provisioned world must not
// break the admin page.
func worldAdminIDs(worldRoot, world string) map[string]struct{} {
	admins := make(map[string]struct{})
	if worldRoot == "" || !validWorld(world) {
		return admins
	}
	path := filepath.Join(worldRoot, world, "valheim.env")
	info, err := os.Lstat(path)
	if err != nil || !info.Mode().IsRegular() || info.Size() > maxWorldEnvSize {
		return admins
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return admins
	}
	match := adminListSetting.FindSubmatch(raw)
	if match == nil {
		return admins
	}
	value := strings.TrimSpace(string(match[1]))
	if len(value) >= 2 && value[0] == value[len(value)-1] && (value[0] == '"' || value[0] == '\'') {
		value = value[1 : len(value)-1]
	}
	for _, id := range strings.Fields(value) {
		if validSteamID(id) {
			admins[id] = struct{}{}
		}
	}
	return admins
}

// playerCards pivots world membership into one card per Steam ID. Players known
// only from a sign-in and players granted access before ever signing in both
// appear, so the list never hides an account an operator has touched.
func playerCards(worlds []adminWorld, members []WorldMember, identities []SteamIdentity, worldRoot string) []playerCard {
	admins := make(map[string]map[string]struct{}, len(worlds))
	names := make([]string, 0, len(worlds))
	for _, world := range worlds {
		admins[world.Name] = worldAdminIDs(worldRoot, world.Name)
		names = append(names, world.Name)
	}
	cards := make(map[string]*playerCard, len(identities)+len(members))
	order := make([]string, 0, len(identities)+len(members))
	touch := func(steamID string) *playerCard {
		card, seen := cards[steamID]
		if !seen {
			card = &playerCard{SteamID: steamID}
			cards[steamID] = card
			order = append(order, steamID)
		}
		return card
	}
	for _, identity := range identities {
		card := touch(identity.SteamID)
		card.SignedIn = true
		card.PersonaName = identity.PersonaName
		card.Label = identity.Label
		card.LastSeenAt = identity.LastSeenAt
	}
	for _, member := range members {
		card := touch(member.SteamID)
		if card.PersonaName == "" {
			card.PersonaName = member.PersonaName
		}
		if card.Label == "" {
			card.Label = member.Label
		}
		_, hostAdmin := admins[member.World][member.SteamID]
		card.Worlds = append(card.Worlds, playerWorldAccess{
			World: member.World, IsAdmin: member.IsAdmin(), HostAdmin: hostAdmin, GrantedBy: member.GrantedBy,
		})
		if member.IsAdmin() {
			card.AdminWorlds++
		}
	}
	out := make([]playerCard, 0, len(order))
	for _, steamID := range order {
		card := cards[steamID]
		card.DisplayName = displayName(card.Label, card.PersonaName)
		sort.Slice(card.Worlds, func(i, j int) bool { return card.Worlds[i].World < card.Worlds[j].World })
		granted := make(map[string]struct{}, len(card.Worlds))
		for _, access := range card.Worlds {
			granted[access.World] = struct{}{}
		}
		for _, name := range names {
			if _, ok := granted[name]; !ok {
				card.OtherWorlds = append(card.OtherWorlds, name)
			}
		}
		out = append(out, *card)
	}
	sort.SliceStable(out, func(i, j int) bool {
		if a, b := len(out[i].Worlds), len(out[j].Worlds); a != b {
			return a > b
		}
		return strings.ToLower(out[i].DisplayName) < strings.ToLower(out[j].DisplayName)
	})
	return out
}
