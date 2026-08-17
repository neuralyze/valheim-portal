package app

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// One document, two audiences. The failure that matters is silent: a marker mistake
// drops a section from one guide while the other still reads correctly, so nobody
// notices until a player follows instructions that were never there.

const guideFixture = `# Guide

Shared opening.

<!-- guide:flat -->
## Keyboard

Hold LeftAlt and interact.
<!-- /guide:flat -->

<!-- guide:vr -->
## Controllers

Point and hold the off-hand trigger.
<!-- /guide:vr -->

| key | action |
|---|---|
| F5 | console |

Shared closing.
`

func TestEachAudienceGetsItsOwnRegionsAndNotTheOthers(t *testing.T) {
	flat := filterGuide(guideFixture, guideFlat)
	vr := filterGuide(guideFixture, guideVR)

	for _, want := range []string{"Shared opening.", "Shared closing.", "| F5 | console |"} {
		if !strings.Contains(flat, want) || !strings.Contains(vr, want) {
			t.Errorf("shared content %q missing from one of the guides", want)
		}
	}
	if !strings.Contains(flat, "Hold LeftAlt") || strings.Contains(flat, "off-hand trigger") {
		t.Errorf("desktop guide has the wrong regions:\n%s", flat)
	}
	if !strings.Contains(vr, "off-hand trigger") || strings.Contains(vr, "Hold LeftAlt") {
		t.Errorf("VR guide has the wrong regions:\n%s", vr)
	}
	// A marker that reaches the reader is a rendering bug in its own right.
	for _, guide := range []string{flat, vr} {
		if strings.Contains(guide, "<!-- guide:") || strings.Contains(guide, "<!-- /guide:") {
			t.Error("a region marker survived filtering")
		}
	}
}

func TestFilteringLeavesUnmarkedDocumentsAlone(t *testing.T) {
	const plain = "# Title\n\nJust prose.\n"
	for _, audience := range []string{guideFlat, guideVR} {
		if got := filterGuide(plain, audience); got != plain {
			t.Errorf("%s: filtered plain markdown changed it:\n%q", audience, got)
		}
	}
}

func TestBrokenRegionsAreRejectedBeforeTheyShip(t *testing.T) {
	cases := map[string]string{
		"never closed":    "a\n<!-- guide:vr -->\nb\n",
		"closer alone":    "a\n<!-- /guide:vr -->\nb\n",
		"nested":          "<!-- guide:vr -->\n<!-- guide:flat -->\nb\n<!-- /guide:flat -->\n<!-- /guide:vr -->\n",
		"crossed closers": "<!-- guide:vr -->\nb\n<!-- /guide:flat -->\n",
	}
	for name, source := range cases {
		if err := validateGuideRegions(source); err == nil {
			t.Errorf("%s: accepted a malformed document", name)
		}
	}
	if err := validateGuideRegions(guideFixture); err != nil {
		t.Errorf("the fixture is well formed but was rejected: %v", err)
	}
}

// The guide that actually ships has to be well formed, or one of the two audiences
// quietly loses text. This is the check that makes the marker contract binding.
func TestTheShippedGuideIsWellFormedAndSplitsBothWays(t *testing.T) {
	if err := validateGuideRegions(playerGuideSource); err != nil {
		t.Fatalf("assets/player-guide.md: %v", err)
	}
	flat, vr := filterGuide(playerGuideSource, guideFlat), filterGuide(playerGuideSource, guideVR)
	if len(flat) == 0 || len(vr) == 0 {
		t.Fatal("one of the audiences renders an empty guide")
	}
	// If the two are identical the markers are doing nothing, which means the split
	// silently is not happening.
	if flat == vr {
		t.Error("both audiences get an identical document; no regions are marked")
	}
	for _, guide := range []string{flat, vr} {
		if strings.Contains(guide, "<!-- guide:") {
			t.Error("a marker survived into a rendered guide")
		}
	}
}

func TestTablesSurviveRendering(t *testing.T) {
	html, _, err := renderGuide("| key | action |\n|---|---|\n| F5 | console |\n")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(html), "<table>") || !strings.Contains(string(html), "<td>F5</td>") {
		t.Errorf("the key table did not render as a table: %s", html)
	}
}

func TestASignedInPlayerReadsTheGuideForTheirClient(t *testing.T) {
	server := testServer(t)
	const world, steamID = "Midgard", "76561190000000002"
	if err := server.store.UpsertPublicWorld(t.Context(), PublicWorld{
		Name: world, JoinAddress: "valheim.example.test:2456", Status: "online",
	}, "test"); err != nil {
		t.Fatal(err)
	}
	if err := server.store.RecordSteamIdentity(t.Context(), steamID); err != nil {
		t.Fatal(err)
	}
	if err := server.store.GrantWorldAccess(t.Context(), world, steamID, "operator"); err != nil {
		t.Fatal(err)
	}

	for _, audience := range []string{guideFlat, guideVR} {
		response := httptest.NewRecorder()
		server.Handler().ServeHTTP(response, signedIn(t, server, http.MethodGet,
			"/worlds/"+world+"/guide/"+audience, steamID))
		if response.Code != http.StatusOK {
			t.Fatalf("%s guide = %d, want 200", audience, response.Code)
		}
		page := response.Body.String()
		if !strings.Contains(page, guideAudienceTitle(audience)) {
			t.Errorf("%s guide does not say which audience it is for", audience)
		}
		// Each guide offers the other one, so a player who took the wrong link is not stuck.
		if !strings.Contains(page, "/guide/"+otherGuideAudience(audience)) {
			t.Errorf("%s guide does not link the other guide", audience)
		}
	}

	// An unknown client type is a 404 rather than a silent fallback to one of them.
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, signedIn(t, server, http.MethodGet,
		"/worlds/"+world+"/guide/holodeck", steamID))
	if response.Code != http.StatusNotFound {
		t.Errorf("unknown guide audience = %d, want 404", response.Code)
	}
}

// The guide names this deployment's mods, ports and admin tooling, so it is gated
// exactly like the world page it hangs off.
func TestTheGuideIsNotPublic(t *testing.T) {
	server := testServer(t)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/worlds/Midgard/guide/flat", nil))
	if response.Code == http.StatusOK {
		t.Errorf("an anonymous request read the guide: %d", response.Code)
	}
}

func TestHeadsetProfilesLinkTheVRGuideAndDesktopProfilesDoNot(t *testing.T) {
	cases := map[profileKind]string{
		profileDesktop:    guideFlat,
		profileDesktopVR:  guideFlat,
		profileHeadset:    guideVR,
		profileUnverified: guideFlat,
	}
	for kind, want := range cases {
		if got := guideAudienceFor(kind); got != want {
			t.Errorf("%s profile points at the %s guide, want %s", kind.Title(), got, want)
		}
	}
}

// A contents list whose links do not match the page's own anchors is worse than
// none: every entry looks clickable and silently does nothing. So the anchors come
// from the same render that produces the headings, and this proves they agree.
func TestTheContentsLinkToAnchorsThatExistOnThePage(t *testing.T) {
	html, sections, err := renderGuide("# Title\n\n## First section\n\ntext\n\n### Sub\n\nmore\n\n## Second section\n\ntext\n")
	if err != nil {
		t.Fatal(err)
	}
	if len(sections) != 2 {
		t.Fatalf("sections = %+v, want the two level-2 headings only", sections)
	}
	if sections[0].Title != "First section" || sections[1].Title != "Second section" {
		t.Errorf("section titles are wrong: %+v", sections)
	}
	for _, section := range sections {
		if section.Anchor == "" {
			t.Fatalf("section %q has no anchor", section.Title)
		}
		if !strings.Contains(string(html), `id="`+section.Anchor+`"`) {
			t.Errorf("contents links #%s but the page has no such anchor", section.Anchor)
		}
	}
}

func TestTheShippedGuideHasContentsForBothAudiences(t *testing.T) {
	for _, audience := range []string{guideFlat, guideVR} {
		html, sections, err := renderGuide(filterGuide(playerGuideSource, audience))
		if err != nil {
			t.Fatalf("%s: %v", audience, err)
		}
		if len(sections) < 5 {
			t.Fatalf("%s guide lists only %d sections; the contents would be useless", audience, len(sections))
		}
		for _, section := range sections {
			if !strings.Contains(string(html), `id="`+section.Anchor+`"`) {
				t.Errorf("%s guide: contents entry %q points at a missing anchor", audience, section.Title)
			}
		}
	}
}
