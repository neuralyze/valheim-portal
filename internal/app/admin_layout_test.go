package app

import (
	"strings"
	"testing"
)

// adminSectionOrder is the top-level widget order an operator should meet: the
// fleet first and expanded, everything else collapsed reference material.
var adminSectionOrder = []string{
	`<details id="servers" class="admin-widget" open>`,
	`<details id="players" class="admin-widget">`,
	`<details id="mods" class="admin-widget">`,
	`<details id="releases" class="admin-widget">`,
	`<details id="activity" class="admin-widget">`,
	`<details id="audit" class="admin-widget">`,
}

func TestAdminPageLeadsWithServerOperationsAndCollapsesTheRest(t *testing.T) {
	server := testServer(t)
	page := adminPage(t, server)
	previous := -1
	for _, section := range adminSectionOrder {
		at := strings.Index(page, section)
		if at < 0 {
			t.Fatalf("admin page is missing %s", section)
		}
		if at <= previous {
			t.Fatalf("%s renders out of order (offset %d, previous %d)", section, at, previous)
		}
		previous = at
	}
}

// The jump nav is a promise about what the page looks like below it, so it has
// to follow the same order as the widgets it links to.
func TestAdminJumpNavigationMirrorsSectionOrder(t *testing.T) {
	page := adminPage(t, testServer(t))
	nav := page[strings.Index(page, `<nav class="admin-nav"`):]
	nav = nav[:strings.Index(nav, "</nav>")]
	previous := -1
	for _, section := range []string{"#servers", "#players", "#mods", "#releases", "#activity"} {
		at := strings.Index(nav, `href="`+section+`"`)
		if at <= previous {
			t.Fatalf("%s renders out of order in the jump nav: %s", section, nav)
		}
		previous = at
	}
}

func TestAdminWorldCardCollapsesToItsWorldNameAndState(t *testing.T) {
	server := testServer(t)
	describedTestWorld(t, server)
	page := adminPage(t, server)
	if count := strings.Count(page, `<details class="server-card-body">`); count != 1 {
		t.Fatalf("collapsible card bodies = %d", count)
	}
	if strings.Contains(page, `<details class="server-card-body" open>`) {
		t.Fatal("world card is expanded by default")
	}
	card := page[strings.Index(page, `id="server-`+describedWorld+`"`):]
	summary := card[:strings.Index(card, "</summary>")]
	if !strings.Contains(summary, describedWorld) {
		t.Fatalf("card summary does not name the world: %s", summary)
	}
	// A collapsed row is the only thing most of the fleet ever shows, so the
	// one fact an operator scans for lives there. The state itself is measured
	// from the server, so this pins the indicator rather than a value.
	if !strings.Contains(summary, `<span class="status-light status-`) {
		t.Fatalf("collapsed card hides the world's player-visible state: %s", summary)
	}
	// Controls stay behind the disclosure; only name and state are always on.
	for _, noise := range []string{"Disable player access", "server-card-address"} {
		if strings.Contains(summary, noise) {
			t.Fatalf("card summary carries %q: %s", noise, summary)
		}
	}
	// display:flex kills ::marker, so without this the row looked inert.
	if !strings.Contains(string(siteCSS), "#servers .server-card-body > summary::after") {
		t.Fatal("the collapsed world card has no disclosure affordance")
	}
}

func TestAdminWorldCardOffersTheWorldMapAsAButtonWithoutExternalMap(t *testing.T) {
	server := testServer(t)
	describedTestWorld(t, server)
	page := adminPage(t, server)
	if !strings.Contains(page, `<a class="button-link" href="/admin/worlds/`+describedWorld+`/map">World map and analysis</a>`) {
		t.Fatalf("the world map is not a button link: %s", page)
	}
	if strings.Contains(page, "World intelligence") {
		t.Fatalf("admin page still calls the map view intelligence: %s", page)
	}
	if strings.Contains(page, "External seed map") || strings.Contains(page, "valheim-map.world") {
		t.Fatalf("admin page still advertises the external seed map: %s", page)
	}
}
