package app

import (
	"archive/zip"
	"cmp"
	"context"
	"errors"
	"html/template"
	"log/slog"
	"slices"

	"github.com/neuralyze/valheim-portal/internal/valheimvr"
)

// A world publishes three profiles and two of them are client_type "flat":
// "<world>-nonvr" strips ValheimVR, "<world>-flatvr" installs it and runs it in
// flat mode, "<world>-vr" installs it and drives it from a headset. A player who
// picks the wrong one silently gets the wrong game, so the card copy must never
// be derived from client_type: it cannot tell the first two apart.
type profileKind int

const (
	// profileUnverified is deliberately first: it is what an unreadable or
	// self-contradictory release falls back to, and guessing there is exactly
	// the failure this type exists to prevent.
	profileUnverified profileKind = iota
	profileDesktop
	profileDesktopVR
	profileHeadset
	// profileAdmin is the same monitor build as profileDesktopVR plus the console and
	// the world-editing tools. It is a separate kind because two cards reading
	// "Desktop, VR-compatible" left a player picking between them by slug alone.
	profileAdmin
)

func (k profileKind) Title() string {
	switch k {
	case profileDesktop:
		return "Desktop"
	case profileDesktopVR:
		return "Desktop, VR-compatible"
	case profileHeadset:
		return "VR headset"
	case profileAdmin:
		return "Desktop, admin tools"
	}
	return "Profile unavailable"
}

func (k profileKind) Summary() string {
	switch k {
	case profileDesktop:
		return "Play on a monitor. No VR mods installed."
	case profileDesktopVR:
		return "Play on a monitor alongside VR players. Installs ValheimVR in flat mode."
	case profileHeadset:
		return "Requires SteamVR and a headset."
	case profileAdmin:
		return "The VR-compatible desktop build plus the console and world-editing tools. Only useful to a world admin."
	}
	return "The portal could not read this profile. Ask the world owner before installing it."
}

type profileReleaseCard struct {
	Release
	SyncURL     template.URL
	GuideURL    template.URL
	GuideName   string
	Kind        profileKind
	Title       string
	Summary     string
	Recommended bool
}

// guideAudienceFor decides which of the two guides a card points at. It keys off
// profileKind rather than client_type, for the same reason the card copy does:
// client_type cannot tell a desktop profile from a VR-compatible desktop profile,
// and both of those players read the desktop guide. Only the headset profile gets
// the VR one. An unverified release also gets the desktop guide, which is the
// harmless default -- keyboard instructions a headset player cannot follow are a
// worse first impression than nothing, but a broken link is worse still.
func guideAudienceFor(kind profileKind) string {
	if kind == profileHeadset {
		return guideVR
	}
	return guideFlat
}

// profileReleaseCards turns a world's published releases into the cards a player
// chooses between, ordered so the safe default comes first and marked so exactly
// one card carries the recommendation.
//
// The admin edition is offered only when “admin“ is set - an admin login. It is not a
// secret (its tools do nothing without server-side admin rights) but it is one more
// identical-looking download beside the one a player wants, and the whole reason for a
// separate card kind was that two of them could not be told apart.
func (s *Server) profileReleaseCards(ctx context.Context, releases []Release, admin bool) ([]profileReleaseCard, error) {
	cards := make([]profileReleaseCard, 0, len(releases))
	for _, release := range releases {
		link, err := s.profileSyncURL(release)
		if err != nil {
			return nil, err
		}
		kind := s.profileKindOf(ctx, release)
		if kind == profileAdmin && !admin {
			continue
		}
		audience := guideAudienceFor(kind)
		cards = append(cards, profileReleaseCard{
			Release: release, SyncURL: template.URL(link),
			GuideURL:  template.URL("/worlds/" + template.URLQueryEscaper(release.World) + "/guide/" + audience),
			GuideName: guideAudienceTitle(audience) + " guide",
			Kind:      kind, Title: kind.Title(), Summary: kind.Summary(),
		})
	}
	slices.SortStableFunc(cards, func(a, b profileReleaseCard) int {
		if order := cmp.Compare(kindOrder(a.Kind), kindOrder(b.Kind)); order != 0 {
			return order
		}
		return cmp.Compare(a.Profile, b.Profile)
	})
	for index, card := range cards {
		if card.Kind == profileDesktop {
			cards[index].Recommended = true
			break
		}
	}
	return cards, nil
}

// kindOrder puts the plain desktop profile first because it is the one a player
// who does not know the difference should take, and the unverified card last so
// a broken release never heads the grid.
func kindOrder(kind profileKind) int {
	switch kind {
	case profileDesktop:
		return 0
	case profileDesktopVR:
		return 1
	case profileHeadset:
		return 2
	// After the three a player picks from: an admin who is also a player still wants
	// their own build first, and the admin edition is the one they take deliberately.
	case profileAdmin:
		return 3
	}
	return 4
}

// profileKindOf classifies one release. The ValheimVR fact comes from the
// published profile definition; client_type only says whether a headset drives
// the mod stack the definition installs. A release whose two halves disagree is
// mis-built, and the card says so rather than picking one of them.
func (s *Server) profileKindOf(ctx context.Context, release Release) profileKind {
	definition, err := s.releaseDefinition(ctx, release)
	if err != nil {
		slog.Error("cannot classify a published profile",
			"release", release.ID, "world", release.World, "profile", release.Profile, "error", err)
		return profileUnverified
	}
	installsVR := definition.Companion != nil
	for _, installed := range definition.Packages {
		if valheimvr.IsIntegrationPackage(installed.Namespace + "-" + installed.Name) {
			installsVR = true
		}
	}
	switch {
	case release.ClientType == "vr" && !installsVR:
		slog.Error("VR release does not install ValheimVR",
			"release", release.ID, "world", release.World, "profile", release.Profile)
		return profileUnverified
	case release.ClientType == "vr" && definition.Audience == "admin":
		// An admin build for a headset is not a shape the catalog produces; treating it
		// as an ordinary headset card would put the console in front of every VR player.
		slog.Error("admin audience on a VR release",
			"release", release.ID, "world", release.World, "profile", release.Profile)
		return profileUnverified
	case release.ClientType == "vr":
		return profileHeadset
	case definition.Audience == "admin":
		return profileAdmin
	case installsVR:
		return profileDesktopVR
	}
	return profileDesktop
}

// releaseDefinition reads the immutable profile definition the player will actually
// install. Every classification fact comes from this one read: which packages it
// carries, whether it carries the ValheimVR companion, and who it is for. Asking for
// those separately meant opening the archive once per question and, worse, letting the
// answers disagree.
//
// internal/valheimvr owns the VR package list, so adding an integration package there
// is enough to keep the VR half honest; nothing here restates which packages are VR.
func (s *Server) releaseDefinition(ctx context.Context, release Release) (ProfileManifest, error) {
	artifacts, err := s.store.PublishedArtifacts(ctx, release.ID)
	if err != nil {
		return ProfileManifest{}, err
	}
	for _, artifact := range artifacts {
		if artifact.Kind != "profile" {
			continue
		}
		archive, err := zip.OpenReader(artifact.Path)
		if err != nil {
			return ProfileManifest{}, err
		}
		defer archive.Close()
		for _, file := range archive.File {
			if file.Name == "profile-manifest.json" {
				return readProfileManifest(file)
			}
		}
		return ProfileManifest{}, errors.New("profile definition has no manifest")
	}
	return ProfileManifest{}, errors.New("published release has no profile definition")
}

// releaseInstallsValheimVR answers the single question the release-detail surfaces ask.
// The companion IS ValheimVRMod.dll - ValidateFlatCompanionArtifact refuses an archive
// without it - so a definition carrying one installs ValheimVR even though no package
// says so. Before the split, every VR-capable Flat edition also carried the geekstreet
// VR fixes, so reading packages alone was accidentally right; the fixes now ship to
// headsets only, and a vr-flat edition would have read as a plain Desktop profile.
func (s *Server) releaseInstallsValheimVR(ctx context.Context, release Release) (bool, error) {
	definition, err := s.releaseDefinition(ctx, release)
	if err != nil {
		return false, err
	}
	if definition.Companion != nil {
		return true, nil
	}
	for _, installed := range definition.Packages {
		if valheimvr.IsIntegrationPackage(installed.Namespace + "-" + installed.Name) {
			return true, nil
		}
	}
	return false, nil
}
