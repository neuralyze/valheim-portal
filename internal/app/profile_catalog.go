package app

import (
	"context"
	"encoding/json"
	"net"
	"sort"
	"strconv"
)

// debugProfileView is one row of the admin Debug Logging control, scoped to a
// published world/profile pair because that is what a release is scoped to.
type debugProfileView struct {
	World   string
	Profile string
	Enabled bool
}

func debugProfileViews(releases []Release, enabled map[string]bool) []debugProfileView {
	seen := make(map[string]struct{}, len(releases))
	views := make([]debugProfileView, 0, len(releases))
	for _, release := range releases {
		key := release.World + "/" + release.Profile
		if _, exists := seen[key]; exists {
			continue
		}
		seen[key] = struct{}{}
		views = append(views, debugProfileView{World: release.World, Profile: release.Profile, Enabled: enabled[key]})
	}
	sort.Slice(views, func(i, j int) bool {
		if views[i].World != views[j].World {
			return views[i].World < views[j].World
		}
		return views[i].Profile < views[j].Profile
	})
	return views
}

func (s *Server) adminProfileCatalog(ctx context.Context, worlds []PublicWorld) []profileCatalogChoice {
	var profiles []profileCatalogChoice
	for _, world := range worlds {
		reply, err := s.agent.Run(ctx, randomID(), world.Name, "profile_catalog")
		if err != nil || reply.Status != "succeeded" {
			continue
		}
		var choices []profileCatalogChoice
		if json.Unmarshal(reply.Data, &choices) != nil {
			continue
		}
		for _, choice := range choices {
			if choice.World != world.Name || !validWorld(choice.Profile) || choice.Packages < 0 || choice.CustomPackages < 0 || choice.DisabledPackages < 0 {
				continue
			}
			profiles = append(profiles, choice)
		}
	}
	return profiles
}

func currentJoinPort(joinAddress string) string {
	_, port, err := net.SplitHostPort(joinAddress)
	if err != nil {
		return ""
	}
	value, err := strconv.Atoi(port)
	if err != nil || value < 1024 || value > 65533 {
		return ""
	}
	return port
}

func draftReleaseChoices(releases []Release) []Release {
	var drafts []Release
	for _, release := range releases {
		if release.Status == Draft {
			drafts = append(drafts, release)
		}
	}
	return drafts
}
