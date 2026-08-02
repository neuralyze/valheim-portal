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
)

func (k profileKind) Title() string {
	switch k {
	case profileDesktop:
		return "Desktop"
	case profileDesktopVR:
		return "Desktop, VR-compatible"
	case profileHeadset:
		return "VR headset"
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
	}
	return "The portal could not read this profile. Ask the world owner before installing it."
}

type profileReleaseCard struct {
	Release
	SyncURL     template.URL
	Kind        profileKind
	Title       string
	Summary     string
	Recommended bool
}

// profileReleaseCards turns a world's published releases into the cards a player
// chooses between, ordered so the safe default comes first and marked so exactly
// one card carries the recommendation.
func (s *Server) profileReleaseCards(ctx context.Context, releases []Release) ([]profileReleaseCard, error) {
	cards := make([]profileReleaseCard, 0, len(releases))
	for _, release := range releases {
		link, err := s.profileSyncURL(release)
		if err != nil {
			return nil, err
		}
		kind := s.profileKindOf(ctx, release)
		cards = append(cards, profileReleaseCard{
			Release: release, SyncURL: template.URL(link),
			Kind: kind, Title: kind.Title(), Summary: kind.Summary(),
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
	}
	return 3
}

// profileKindOf classifies one release. The ValheimVR fact comes from the
// published profile definition; client_type only says whether a headset drives
// the mod stack the definition installs. A release whose two halves disagree is
// mis-built, and the card says so rather than picking one of them.
func (s *Server) profileKindOf(ctx context.Context, release Release) profileKind {
	installsVR, err := s.releaseInstallsValheimVR(ctx, release)
	if err != nil {
		slog.Error("cannot classify a published profile",
			"release", release.ID, "world", release.World, "profile", release.Profile, "error", err)
		return profileUnverified
	}
	switch {
	case release.ClientType == "vr" && !installsVR:
		slog.Error("VR release does not install ValheimVR",
			"release", release.ID, "world", release.World, "profile", release.Profile)
		return profileUnverified
	case release.ClientType == "vr":
		return profileHeadset
	case installsVR:
		return profileDesktopVR
	}
	return profileDesktop
}

// releaseInstallsValheimVR reads the answer out of the immutable profile
// definition the player will actually install. internal/valheimvr owns the
// package list, so adding a VR integration package there is enough to keep this
// honest; nothing here restates which packages are VR.
func (s *Server) releaseInstallsValheimVR(ctx context.Context, release Release) (bool, error) {
	artifacts, err := s.store.PublishedArtifacts(ctx, release.ID)
	if err != nil {
		return false, err
	}
	for _, artifact := range artifacts {
		if artifact.Kind == "profile" {
			return profileDefinitionInstallsValheimVR(artifact.Path)
		}
	}
	return false, errors.New("published release has no profile definition")
}

func profileDefinitionInstallsValheimVR(path string) (bool, error) {
	archive, err := zip.OpenReader(path)
	if err != nil {
		return false, err
	}
	defer archive.Close()
	for _, file := range archive.File {
		if file.Name != "profile-manifest.json" {
			continue
		}
		manifest, err := readProfileManifest(file)
		if err != nil {
			return false, err
		}
		for _, installed := range manifest.Packages {
			if valheimvr.IsIntegrationPackage(installed.Namespace + "-" + installed.Name) {
				return true, nil
			}
		}
		return false, nil
	}
	return false, errors.New("profile definition has no manifest")
}
