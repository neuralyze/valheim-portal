package app

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strconv"
	"strings"
	"testing"
)

// configManagerWorld is the world every test in this file registers. Named apart from the shared
// describedWorld so a settings schema cached here can never be mistaken for another test's fixture.
const configManagerWorld = "Hrafnheim"

// configManagerFingerprint is a stand-in for the host's sha256 of the config file set. The store
// requires 64 hex characters, the same as the mod catalog's, so a short one would be refused.
const configManagerFingerprint = "1111111111111111111111111111111111111111111111111111111111111111"

// configManagerSchema is the fixture, and every entry in it is shaped on a real record measured in
// /media/big4/projects/game/valheim/Hrafnheim/config_merged/bepinex rather than invented:
//
//	Azumatt.AzuAutoStore.cfg      "Lock Configuration = On", type Toggle, acceptable "Off, On"
//	Azumatt.AzuAutoStore.cfg      "Player Range = 10", type Single, "Acceptable value range: From 1 to 100"
//	Avo.BackpacksVRFix.cfg        "Enable Fix = true", type Boolean, no acceptable line
//	Azumatt.AzuAntiArthritic.cfg  a description ending "[Synced with Server]" -> synced
//	org.bepinex.plugins.valheimvrmod.cfg  a section literally named Immutable
func configManagerSchema() ConfigSchema {
	return ConfigSchema{
		Schema: "world-config-schema/v1",
		World:  configManagerWorld,
		Files: []ConfigSchemaFile{{
			File: "Avo.BackpacksVRFix.cfg", ModIdentifier: "Avo.BackpacksVRFix", ModName: "Backpacks VR Fix",
			Source: configSourceClient, Shipped: true,
			Sections: []ConfigSchemaSection{{
				Name: "General",
				Entries: []ConfigSchemaEntry{{
					Key: "Enable Fix", Type: "Boolean", Default: "true",
					Description: "Keep ValheimVR's inventory wrist attachment active.",
					Acceptable:  ConfigAcceptable{Kind: "none"},
					ClientSide:  true,
				}, {
					Key: "Log Prefix", Type: "String", Default: "backpacks",
					Acceptable: ConfigAcceptable{Kind: "none"},
				}},
			}},
		}, {
			File: "Azumatt.AzuAutoStore.cfg", ModIdentifier: "Azumatt.AzuAutoStore", ModName: "AzuAutoStore",
			Source: configSourceMerged, Shipped: false,
			Sections: []ConfigSchemaSection{{
				Name: "1 - General",
				Entries: []ConfigSchemaEntry{{
					Key: "Lock Configuration", Type: "Toggle", Default: "On", Current: "On",
					Description: "If on, the configuration is locked and can be changed by server admins only.",
					Acceptable:  ConfigAcceptable{Kind: "list", Values: []string{"Off", "On"}},
					Synced:      true,
				}, {
					// The list case: a Toggle whose acceptable values are exactly Off and On.
					Key: "Dont Store to Backpacks", Type: "Toggle", Default: "Off",
					Acceptable: ConfigAcceptable{Kind: "list", Values: []string{"Off", "On"}},
					Synced:     true,
				}},
			}, {
				Name: "2 - Storage",
				Entries: []ConfigSchemaEntry{{
					// The range case, with a float type and both bounds declared.
					Key: "Player Range", Type: "Single", Default: "5", Current: "10",
					Description: "The maximum distance from the player to store items in chests.",
					Acceptable: ConfigAcceptable{
						Kind: "range",
						Min:  ConfigBound{Text: "1", Number: 1, Set: true},
						Max:  ConfigBound{Text: "100", Number: 100, Set: true},
					},
				}, {
					// A keybind that declares its whole enum, which must NOT become a select.
					Key: "Store Shortcut", Type: "KeyboardShortcut", Default: "LeftControl",
					Acceptable: ConfigAcceptable{Kind: "list", Values: []string{"LeftControl", "PageUp", "F5"}},
				}},
			}},
		}, {
			File: "org.bepinex.plugins.valheimvrmod.cfg", ModName: "ValheimVRMod",
			Source: configSourceBoth, Shipped: true,
			Sections: []ConfigSchemaSection{{
				Name: "Immutable", Immutable: true,
				Entries: []ConfigSchemaEntry{{
					Key: "UseVrControls", Type: "Boolean", Default: "true",
					Acceptable: ConfigAcceptable{Kind: "none"},
				}},
			}, {
				Name: "Motion Control",
				Entries: []ConfigSchemaEntry{{
					Key: "BlockingType", Type: "String", Default: "Gesture",
					Description: "How blocking is triggered.",
					Acceptable:  ConfigAcceptable{Kind: "list", Values: []string{"Gesture", "GrabButton", "Realistic"}},
				}},
			}},
		}},
	}
}

// configManagerTestServer registers the world and seeds the cached schema, which is what a page view
// reads. The test agent socket answers nothing, so the fingerprint check fails and the page serves
// the cache unverified - exactly the path a real portal takes when the host is unreachable, and the
// one that lets a render be asserted without a host.
func configManagerTestServer(t *testing.T) *Server {
	t.Helper()
	server := testServer(t)
	if err := server.store.UpsertPublicWorld(t.Context(), PublicWorld{
		Name: configManagerWorld, JoinAddress: "valheim.example:2456", Status: "online", ServerVersion: "test",
	}, "test"); err != nil {
		t.Fatal(err)
	}
	payload, err := json.Marshal(configManagerSchema())
	if err != nil {
		t.Fatal(err)
	}
	if err := server.store.SaveWorldConfigSchema(t.Context(), configManagerWorld, configManagerFingerprint, payload); err != nil {
		t.Fatal(err)
	}
	return server
}

// configManagerView fetches one view of the page as an operator sees it, so the assertions land on
// what the template actually emits rather than on the model behind it.
func configManagerView(t *testing.T, server *Server, query string) string {
	t.Helper()
	target := "/admin/worlds/" + configManagerWorld + "/settings"
	if query != "" {
		target += "?" + query
	}
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, adminTestRequest(http.MethodGet, target, nil))
	if response.Code != http.StatusOK {
		t.Fatalf("GET %s = %d: %s", target, response.Code, response.Body.String())
	}
	return response.Body.String()
}

// configManagerSetting isolates one setting's rendered markup. Assertions have to be scoped to a row:
// the page draws several settings and a page-wide Contains would pass on a neighbour's markup, which
// is how a widget assertion silently stops testing anything.
func configManagerRow(t *testing.T, page, key string) string {
	t.Helper()
	marker := `<span class="config-key">` + key + `</span>`
	start := strings.Index(page, marker)
	if start < 0 {
		t.Fatalf("the page does not render %q: %s", key, page)
	}
	// Back up to the row's own element, then stop at the end of it.
	open := strings.LastIndex(page[:start], `<article class="config-setting`)
	if open < 0 {
		t.Fatalf("%q is not inside a setting row", key)
	}
	end := strings.Index(page[open:], "</article>")
	if end < 0 {
		t.Fatalf("the row for %q is not closed", key)
	}
	return page[open : open+end]
}

func configManagerSectionView(t *testing.T, server *Server, file, section string) string {
	t.Helper()
	values := url.Values{"file": {file}, "section": {section}}
	return configManagerView(t, server, values.Encode())
}

// TestConfigManagerListSettingOffersExactlyTheAllowedValues is the first render contract: a setting
// that declares a list becomes a select whose options are its declared values and nothing else.
// Anything extra would be a value the mod refuses, and anything missing would be a value the
// operator cannot set from the page that exists to set it.
func TestConfigManagerListSettingOffersExactlyTheAllowedValues(t *testing.T) {
	server := configManagerTestServer(t)
	row := configManagerRow(t,
		configManagerSectionView(t, server, "org.bepinex.plugins.valheimvrmod.cfg", "Motion Control"),
		"BlockingType")
	if !strings.Contains(row, `<select id="setting-0-value" name="value"`) {
		t.Fatalf("a list-typed setting is not a select: %s", row)
	}
	var options []string
	for rest := row; ; {
		open := strings.Index(rest, `<option value="`)
		if open < 0 {
			break
		}
		rest = rest[open+len(`<option value="`):]
		close := strings.Index(rest, `"`)
		options = append(options, rest[:close])
	}
	want := []string{"Gesture", "GrabButton", "Realistic"}
	if len(options) != len(want) {
		t.Fatalf("options = %v, want exactly %v: %s", options, want, row)
	}
	for index, option := range options {
		if option != want[index] {
			t.Fatalf("option %d = %q, want %q (order is the mod's own)", index, option, want[index])
		}
	}
	// A control that also accepted free text would make the option list decorative.
	if strings.Contains(row, `<input type="text"`) {
		t.Fatalf("a list-typed setting also offers a free-text field: %s", row)
	}
}

// TestConfigManagerRangeSettingCarriesTheSchemaBounds is the second contract: a declared range
// becomes a slider over the real min and max, paired with a number field so a precise value is still
// typable. Measured cause for the pairing: 51 ranges in this corpus are wider than 1000, where a
// slider cannot express a change under 25 units, and 113 are narrower than 1.0.
func TestConfigManagerRangeSettingCarriesTheSchemaBounds(t *testing.T) {
	server := configManagerTestServer(t)
	row := configManagerRow(t,
		configManagerSectionView(t, server, "Azumatt.AzuAutoStore.cfg", "2 - Storage"),
		"Player Range")
	if !strings.Contains(row, `<input type="range" min="1" max="100"`) {
		t.Fatalf("the slider does not carry the schema's own bounds: %s", row)
	}
	if !strings.Contains(row, `type="number"`) || !strings.Contains(row, `min="1" max="100"`) {
		t.Fatalf("the range has no paired number field with the same bounds: %s", row)
	}
	// A float range must step finely enough to reach a value between whole units. 1 to 100 with a
	// step of 1 could never express 10.5, which this mod accepts.
	if !strings.Contains(row, `step="0.5"`) {
		t.Fatalf("a Single range steps too coarsely to reach a fractional value: %s", row)
	}
	// The slider must not be a submitting field: two inputs of the same name and the stale one could
	// win, and with scripts off the slider's rendered position would beat what was typed.
	slider := row[strings.Index(row, `<input type="range"`):]
	slider = slider[:strings.Index(slider, ">")]
	if strings.Contains(slider, "name=") {
		t.Fatalf("the slider submits its own value alongside the number field: %s", slider)
	}
}

// TestConfigManagerSyncedSettingIsServerAuthoritativeWithNoOverride is C4 as a render contract. A
// synced key is overwritten by the server in memory at runtime whatever the client's file says -
// ServerSync's prefix on GetSerializedValue means the server's value is never even persisted to the
// client's .cfg - so offering a player override is a promise the runtime breaks. The page must refuse
// it AND say why, rather than hiding a control with no explanation.
func TestConfigManagerSyncedSettingIsServerAuthoritativeWithNoOverride(t *testing.T) {
	server := configManagerTestServer(t)
	page := configManagerSectionView(t, server, "Azumatt.AzuAutoStore.cfg", "1 - General")
	row := configManagerRow(t, page, "Dont Store to Backpacks")
	if !strings.Contains(row, "Server-authoritative") {
		t.Fatalf("a synced setting is not marked server-authoritative: %s", row)
	}
	if strings.Contains(row, `value="client_default"`) {
		t.Fatalf("a synced setting offers a player override that cannot hold: %s", row)
	}
	if !strings.Contains(row, `<input type="hidden" name="policy" value="server_forced">`) {
		t.Fatalf("a synced setting posts no policy at all: %s", row)
	}
	// The reason has to be visible. A vanished control with no explanation is the failure mode of
	// BepInEx's own ConfigurationManager, whose only lock signal is a missing Reset button.
	if !strings.Contains(row, "Synced with the server") {
		t.Fatalf("a synced setting states no reason: %s", row)
	}
	// This mod declares Lock Configuration, so the reason must name that lock rather than dead-end.
	if !strings.Contains(row, "Lock Configuration") {
		t.Fatalf("the reason does not name the admin lock causing it: %s", row)
	}
	// The lock's OWN row must not claim the mod has no lock. Measured live before this was fixed:
	// AzuAutoStore's "Lock Configuration" row said "this mod declares no admin lock" while being it.
	lock := configManagerRow(t, page, "Lock Configuration")
	if strings.Contains(lock, "declares no admin lock") {
		t.Fatalf("the admin lock's own row denies that the mod has one: %s", lock)
	}
	if !strings.Contains(lock, "This IS this mod") {
		t.Fatalf("the admin lock's own row does not say what it is: %s", lock)
	}
	// Control, and the point of the whole assertion: a setting in the same world that is NOT synced
	// does offer the choice. Without this, a page that never offered an override would pass above.
	unsynced := configManagerRow(t,
		configManagerSectionView(t, server, "org.bepinex.plugins.valheimvrmod.cfg", "Motion Control"),
		"BlockingType")
	if !strings.Contains(unsynced, `value="client_default"`) {
		t.Fatalf("an unsynced setting does not offer a player override either: %s", unsynced)
	}
}

// TestConfigManagerImmutableSettingRendersReadOnly is C5. VHVR's Immutable section is read once at
// startup and then overridden from the command line, so a value set here would not take effect until
// the client restarted. It is drawn genuinely disabled with that said out loud - not as a live widget
// that silently refuses writes.
func TestConfigManagerImmutableSettingRendersReadOnly(t *testing.T) {
	server := configManagerTestServer(t)
	row := configManagerRow(t,
		configManagerSectionView(t, server, "org.bepinex.plugins.valheimvrmod.cfg", "Immutable"),
		"UseVrControls")
	if !strings.Contains(row, ` disabled `) && !strings.Contains(row, ` disabled>`) {
		t.Fatalf("an immutable setting is not really disabled: %s", row)
	}
	if strings.Contains(row, "<button type=\"submit\"") {
		t.Fatalf("an immutable setting offers a save button: %s", row)
	}
	if strings.Contains(row, `name="value"`) {
		t.Fatalf("an immutable setting submits a value: %s", row)
	}
	if !strings.Contains(row, "takes effect on restart") {
		t.Fatalf("an immutable setting does not say a restart is needed: %s", row)
	}
	// Control: a Boolean OUTSIDE the immutable section is a live toggle, so the assertion above is
	// about the section and not about Booleans.
	live := configManagerRow(t,
		configManagerSectionView(t, server, "Avo.BackpacksVRFix.cfg", "General"),
		"Enable Fix")
	if !strings.Contains(live, `type="checkbox"`) {
		t.Fatalf("a Boolean outside an immutable section is not a toggle: %s", live)
	}
	if strings.Contains(live, " disabled") {
		t.Fatalf("a Boolean outside an immutable section is disabled: %s", live)
	}
}

// TestConfigManagerResetReturnsASettingToUnmanaged is C2's third state. Reset DELETES the record; it
// does not write the default as an explicit value. The difference reaches the player: an unmanaged
// key is absent from the published baseline entirely, so the mod's own default applies and nothing
// the portal publishes touches it.
func TestConfigManagerResetReturnsASettingToUnmanaged(t *testing.T) {
	server := configManagerTestServer(t)
	ref := ConfigSettingRef{
		File: "org.bepinex.plugins.valheimvrmod.cfg", Section: "Motion Control", Key: "BlockingType",
	}
	form := url.Values{
		"file": {ref.File}, "section": {ref.Section}, "key": {ref.Key},
		"value": {"Realistic"}, "policy": {string(PolicyClientDefault)},
		"view_file": {ref.File}, "view_section": {ref.Section},
	}
	configManagerPost(t, server, "/settings", form)

	managed, err := server.store.WorldConfigAuthority(t.Context(), configManagerWorld)
	if err != nil {
		t.Fatal(err)
	}
	stored, ok := managed.Setting(ref.File, ref.Section, ref.Key)
	if !ok || stored.Value != "Realistic" || stored.Policy != PolicyClientDefault {
		t.Fatalf("the setting was not recorded: %#v ok=%v", stored, ok)
	}
	row := configManagerRow(t, configManagerSectionView(t, server, ref.File, ref.Section), ref.Key)
	if !strings.Contains(row, "Seeded") {
		t.Fatalf("a seeded setting does not say so: %s", row)
	}

	configManagerPost(t, server, "/settings/reset", url.Values{
		"file": {ref.File}, "section": {ref.Section}, "key": {ref.Key},
		"view_file": {ref.File}, "view_section": {ref.Section},
	})

	after, err := server.store.WorldConfigAuthority(t.Context(), configManagerWorld)
	if err != nil {
		t.Fatal(err)
	}
	if _, still := after.Setting(ref.File, ref.Section, ref.Key); still {
		t.Fatal("reset left a record behind, so the setting is still managed rather than unmanaged")
	}
	// The store having no row is half of it. The other half is that the page says so, because the
	// wrong implementation - writing the default as a value - would also leave the row looking
	// settled while publishing something the mod default would not.
	reset := configManagerRow(t, configManagerSectionView(t, server, ref.File, ref.Section), ref.Key)
	if !strings.Contains(reset, "Not managed") {
		t.Fatalf("a reset setting does not read as unmanaged: %s", reset)
	}
	for _, forbidden := range []string{"Seeded", "Server-forced"} {
		if strings.Contains(reset, forbidden) {
			t.Fatalf("a reset setting still claims %q: %s", forbidden, reset)
		}
	}
	if strings.Contains(reset, "Stop managing this setting") {
		t.Fatalf("an unmanaged setting still offers to stop being managed: %s", reset)
	}
}

// TestConfigManagerRefusesAValueOutsideTheDeclaredList proves the server-side half of the validation
// exists. The page constrains the widget, but a form is a form: the refusal has to happen on the
// write path and be shown against the widget it belongs to.
func TestConfigManagerRefusesAValueOutsideTheDeclaredList(t *testing.T) {
	server := configManagerTestServer(t)
	form := url.Values{
		"file": {"org.bepinex.plugins.valheimvrmod.cfg"}, "section": {"Motion Control"},
		"key": {"BlockingType"}, "value": {"Telekinesis"}, "policy": {string(PolicyServerForced)},
		"view_file": {"org.bepinex.plugins.valheimvrmod.cfg"}, "view_section": {"Motion Control"},
	}
	body := configManagerPostExpecting(t, server, "/settings", form, http.StatusOK)
	if !strings.Contains(body, "Gesture") || !strings.Contains(body, "choose one of") {
		t.Fatalf("a refused value does not name the allowed set: %s", body)
	}
	managed, err := server.store.WorldConfigAuthority(t.Context(), configManagerWorld)
	if err != nil {
		t.Fatal(err)
	}
	if _, stored := managed.Setting("org.bepinex.plugins.valheimvrmod.cfg", "Motion Control", "BlockingType"); stored {
		t.Fatal("a value outside the declared list was stored anyway")
	}
}

// TestConfigManagerOpensOnWhatItManages is the default view. The operator's own question is what the
// portal is controlling, not what 19,866 settings exist, and an untouched world has to read as a
// deliberate "nothing yet" rather than as a broken page.
func TestConfigManagerOpensOnWhatItManages(t *testing.T) {
	server := configManagerTestServer(t)
	empty := configManagerView(t, server, "")
	if !strings.Contains(empty, "Nothing is managed yet") {
		t.Fatalf("a world with no managed settings does not say so: %s", empty)
	}
	if strings.Contains(empty, `<span class="config-key">BlockingType</span>`) {
		t.Fatal("the default view draws settings the portal does not manage")
	}
	configManagerPost(t, server, "/settings", url.Values{
		"file": {"org.bepinex.plugins.valheimvrmod.cfg"}, "section": {"Motion Control"},
		"key": {"BlockingType"}, "value": {"GrabButton"}, "policy": {string(PolicyServerForced)},
	})
	managed := configManagerView(t, server, "")
	if strings.Contains(managed, "Nothing is managed yet") {
		t.Fatalf("the managed view still claims nothing is managed: %s", managed)
	}
	row := configManagerRow(t, managed, "BlockingType")
	if !strings.Contains(row, "Server-forced") {
		t.Fatalf("the managed view does not show the setting's state: %s", row)
	}
}

// TestConfigManagerSearchSpansEveryModAndSaysWhatItWithheld covers the one way to reach a setting
// whose mod the operator does not know. With one file holding 3361 keys over 121 sections,
// mod-then-section navigation alone cannot find BlockingType without knowing it lives in
// ValheimVRMod's Motion Control.
func TestConfigManagerSearchSpansEveryModAndSaysWhatItWithheld(t *testing.T) {
	server := configManagerTestServer(t)
	page := configManagerView(t, server, url.Values{"q": {"blockingtype"}}.Encode())
	if !strings.Contains(page, `<span class="config-key">BlockingType</span>`) {
		t.Fatalf("search did not find a key by name: %s", page)
	}
	// A hit arrives with no context, so it has to carry where it lives.
	row := configManagerRow(t, page, "BlockingType")
	if !strings.Contains(row, "ValheimVRMod") || !strings.Contains(row, "Motion Control") {
		t.Fatalf("a search hit does not say where it lives: %s", row)
	}
	// Searching a description reaches a key whose name does not contain the term at all.
	byDescription := configManagerView(t, server, url.Values{"q": {"wrist attachment"}}.Encode())
	if !strings.Contains(byDescription, `<span class="config-key">Enable Fix</span>`) {
		t.Fatalf("search does not match description text: %s", byDescription)
	}
	nothing := configManagerView(t, server, url.Values{"q": {"zzzznosuchsetting"}}.Encode())
	if !strings.Contains(nothing, "Nothing matched") {
		t.Fatalf("an empty search does not say so: %s", nothing)
	}
}

// TestConfigManagerKeybindIsNotADropdown guards the one place the type token outranks the
// acceptable-values shape. AzuAutoStore's shortcuts each declare 339 acceptable values - the whole
// Unity KeyCode enum - and a 339-option select is the mistake ConfigurationManager makes. The
// declared values are kept as a searchable datalist, so the constraint is not discarded.
func TestConfigManagerKeybindIsNotADropdown(t *testing.T) {
	server := configManagerTestServer(t)
	row := configManagerRow(t,
		configManagerSectionView(t, server, "Azumatt.AzuAutoStore.cfg", "2 - Storage"),
		"Store Shortcut")
	if strings.Contains(row, "<select") {
		t.Fatalf("a keybind became a dropdown over its whole enum: %s", row)
	}
	if !strings.Contains(row, "data-key-capture=") {
		t.Fatalf("a keybind offers no press-a-key control: %s", row)
	}
	if !strings.Contains(row, "<datalist") || !strings.Contains(row, `value="PageUp"`) {
		t.Fatalf("a keybind's declared values are not offered at all: %s", row)
	}
}

// TestConfigManagerKeepsAValueItsModNoLongerDeclares is r2modman's behaviour rather than Gale's.
// Gale coerces such a value to option 0, which rewrites it the moment anyone presses save; keeping it
// as a flagged option is the difference between preserving a hand-edited value and destroying it.
func TestConfigManagerKeepsAValueItsModNoLongerDeclares(t *testing.T) {
	server := configManagerTestServer(t)
	// A value the mod does not list can only arrive from outside the page, so it is written straight
	// to the store the way an older mod version or a hand edit would have left it.
	if err := server.store.SetWorldConfigSetting(t.Context(), configManagerWorld, ConfigSchema{}, ConfigSetting{
		ConfigSettingRef: ConfigSettingRef{
			File: "org.bepinex.plugins.valheimvrmod.cfg", Section: "Motion Control", Key: "BlockingType",
		},
		Value: "Telekinesis", Policy: PolicyServerForced,
	}, "test"); err == nil {
		t.Fatal("the store accepted a value against an empty schema; this fixture needs rethinking")
	}
	// The store refuses it, correctly, so the render is exercised through the page model instead -
	// which is the same code path the template consumes.
	page := configManagerPage{World: PublicWorld{Name: configManagerWorld}}
	authority := ConfigAuthority{
		ConfigSettingRef{File: "org.bepinex.plugins.valheimvrmod.cfg", Section: "Motion Control", Key: "BlockingType"}: {
			ConfigSettingRef: ConfigSettingRef{
				File: "org.bepinex.plugins.valheimvrmod.cfg", Section: "Motion Control", Key: "BlockingType",
			},
			Value: "Telekinesis", Policy: PolicyServerForced,
		},
	}
	buildConfigManagerPage(&page, configManagerSchema(), authority, "", configSettingRef{})
	var found bool
	for _, group := range page.Groups {
		for _, entry := range group.Entries {
			if entry.Key != "BlockingType" {
				continue
			}
			found = true
			if entry.Widget != widgetSelect {
				t.Fatalf("BlockingType is not a select: %q", entry.Widget)
			}
			if len(entry.Options) != 4 {
				t.Fatalf("options = %#v, want the three declared plus the held one", entry.Options)
			}
			if !entry.Options[0].Unlisted || entry.Options[0].Value != "Telekinesis" || !entry.Options[0].Selected {
				t.Fatalf("the held off-list value was not preserved and flagged: %#v", entry.Options[0])
			}
		}
	}
	if !found {
		t.Fatal("the managed view did not render the managed setting at all")
	}
}

// TestConfigManagerButtonSitsBesideTheWorldMapButton is the operator's own request: the link to this
// page is a button next to the World map button on the world's admin card.
func TestConfigManagerButtonSitsBesideTheWorldMapButton(t *testing.T) {
	server := testServer(t)
	describedTestWorld(t, server)
	page := adminPage(t, server)
	mapButton := `<a class="button-link" href="/admin/worlds/` + describedWorld + `/map">World map and analysis</a>`
	settingsButton := `<a class="button-link" href="/admin/worlds/` + describedWorld + `/settings">Settings configuration</a>`
	if !strings.Contains(page, settingsButton) {
		t.Fatalf("the settings manager is not a button link on the world card: %s", page)
	}
	// Beside, not merely present. Adjacency is what was asked for, so the two are asserted as one
	// run of markup rather than as two independent Contains that would pass from opposite ends of
	// the page.
	if !strings.Contains(page, mapButton+"\n"+settingsButton) {
		t.Fatalf("the settings button is not next to the world map button: %s", page)
	}
	// Inside the world card's own nav, so it travels with the world it belongs to.
	nav := page[strings.Index(page, `<nav class="server-card-links"`):]
	nav = nav[:strings.Index(nav, "</nav>")]
	if !strings.Contains(nav, settingsButton) {
		t.Fatalf("the settings button is outside the world card's admin views nav: %s", nav)
	}
}

// TestConfigManagerRejectsAnotherWorldsSchema guards the decode. A cached payload naming a different
// world is a wiring mistake, and rendering it would let one server's values be set from another's
// page.
func TestConfigManagerRejectsAnotherWorldsSchema(t *testing.T) {
	if _, ok := decodeConfigSchema(mustJSON(t, configManagerSchema()), "Doggerland"); ok {
		t.Fatal("a schema for Hrafnheim was accepted as Doggerland's")
	}
	if _, ok := decodeConfigSchema(mustJSON(t, configManagerSchema()), configManagerWorld); !ok {
		t.Fatal("control failed: the world's own schema was rejected")
	}
	wrongVersion := configManagerSchema()
	wrongVersion.Schema = "world-config-schema/v2"
	if _, ok := decodeConfigSchema(mustJSON(t, wrongVersion), configManagerWorld); ok {
		t.Fatal("a payload declaring an unknown schema version was accepted")
	}
}

// TestConfigManagerLockDetectionMatchesWholeKeyNames is the anchored-match guard. A substring match
// on "lock" pulls in LockGuiPositionWhenMenuOpen, which is the same Boolean shape as a real lock and
// is not one; a looser pattern pulls in every "Block Force" in the weapon files. Only the name
// separates them, so only whole names are matched.
func TestConfigManagerLockDetectionMatchesWholeKeyNames(t *testing.T) {
	file := ConfigSchemaFile{
		File: "probe.cfg",
		Sections: []ConfigSchemaSection{{
			Name: "General",
			Entries: []ConfigSchemaEntry{
				{Key: "LockGuiPositionWhenMenuOpen", Type: "Boolean", Default: "true"},
				{Key: "Block Force", Type: "Single", Default: "1"},
				{Key: "Timed block bonus per tier", Type: "Single", Default: "1"},
			},
		}},
	}
	if lock, found := configFileLock("Hrafnheim", file, ConfigAuthority{}); found {
		t.Fatalf("a lock was invented from %q", lock.Key)
	}
	// Control: the real spellings measured in the corpus must all be found, and their value read
	// whether the mod writes On/Off or true/false.
	for _, probe := range []struct {
		key, value string
		on         bool
	}{
		{"Lock Configuration", "On", true},
		{"Lock Configuration", "true", true},
		{"LockConfig", "false", false},
		{"Config Locked", "true", true},
		{"Lock to Admin", "Off", false},
	} {
		file.Sections[0].Entries = append(file.Sections[0].Entries, ConfigSchemaEntry{
			Key: probe.key, Type: "Boolean", Default: probe.value,
		})
		lock, found := configFileLock("Hrafnheim", file, ConfigAuthority{})
		if !found {
			t.Fatalf("%q was not recognised as a lock", probe.key)
		}
		if lock.Key != probe.key || !lock.Known || lock.On != probe.on {
			t.Fatalf("%q = %#v, want on=%v known", probe.key, lock, probe.on)
		}
		file.Sections[0].Entries = file.Sections[0].Entries[:len(file.Sections[0].Entries)-1]
	}
	// A value that is neither on nor off must read as unknown rather than silently as off.
	file.Sections[0].Entries = append(file.Sections[0].Entries, ConfigSchemaEntry{
		Key: "Lock Configuration", Type: "String", Default: "Sometimes",
	})
	if lock, found := configFileLock("Hrafnheim", file, ConfigAuthority{}); !found || lock.Known {
		t.Fatalf("an unparsable lock value was reported as known: %#v", lock)
	}
}

// TestConfigManagerModViewListsSectionsBeforeEntries is the third navigation level. The four worlds
// here carry up to 28,781 keys across 174 files, so choosing a mod lists its sections rather than
// drawing every widget the mod declares.
func TestConfigManagerModViewListsSectionsBeforeEntries(t *testing.T) {
	server := configManagerTestServer(t)
	page := configManagerView(t, server, url.Values{"file": {"Azumatt.AzuAutoStore.cfg"}}.Encode())
	for _, section := range []string{"1 - General", "2 - Storage"} {
		if !strings.Contains(page, section) {
			t.Fatalf("the mod view does not list section %q: %s", section, page)
		}
	}
	if strings.Contains(page, `<span class="config-key">Player Range</span>`) {
		t.Fatalf("the mod view drew entries before a section was chosen: %s", page)
	}
	// A mod with a single section has no choice to make, so its entries are drawn straight away.
	single := configManagerView(t, server, url.Values{"file": {"Avo.BackpacksVRFix.cfg"}}.Encode())
	if !strings.Contains(single, `<span class="config-key">Enable Fix</span>`) {
		t.Fatalf("a single-section mod hides its only section behind a click: %s", single)
	}
}

// TestConfigManagerTogglePostsFalseWhenCleared covers the one form quirk that could silently break a
// Boolean: an unchecked checkbox submits nothing at all, so without the hidden field beneath it a
// toggle could be turned on and never off again.
func TestConfigManagerTogglePostsFalseWhenCleared(t *testing.T) {
	server := configManagerTestServer(t)
	row := configManagerRow(t,
		configManagerSectionView(t, server, "Avo.BackpacksVRFix.cfg", "General"),
		"Enable Fix")
	if !strings.Contains(row, `<input type="hidden" name="value" value="false">`) {
		t.Fatalf("a toggle has no false fallback, so it could never be cleared: %s", row)
	}
	hidden := strings.Index(row, `name="value" value="false"`)
	checkbox := strings.Index(row, `type="checkbox"`)
	if hidden > checkbox {
		t.Fatalf("the false fallback comes after the checkbox, so the last value is always false: %s", row)
	}
	// And the reader takes the last of the two, which is what makes the ordering meaningful.
	request := httptest.NewRequest(http.MethodPost, "/x", strings.NewReader("value=false&value=true"))
	request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	if err := request.ParseForm(); err != nil {
		t.Fatal(err)
	}
	if got := configPostedValue(request); got != "true" {
		t.Fatalf("a checked toggle posts %q, want the checkbox's own value", got)
	}
	// The client-side hint is a real distinction: this key is explicitly [Not Synced with Server].
	if !strings.Contains(row, "marked this one as not synced") {
		t.Fatalf("an explicitly client-side setting is not marked as such: %s", row)
	}
}

func configManagerPost(t *testing.T, server *Server, route string, form url.Values) {
	t.Helper()
	configManagerPostExpecting(t, server, route, form, http.StatusSeeOther)
}

// configManagerPostExpecting posts through the real admin guard, so every mutation here is also
// proving that CSRF is enforced the way the other admin forms enforce it.
func configManagerPostExpecting(t *testing.T, server *Server, route string, form url.Values, want int) string {
	t.Helper()
	target := "/admin/worlds/" + configManagerWorld + route
	nonce := randomHex(32)
	body := url.Values{}
	for key, values := range form {
		body[key] = values
	}
	body.Set("csrf", server.csrfToken(nonce))
	request := adminTestRequest(http.MethodPost, target, strings.NewReader(body.Encode()))
	request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	request.AddCookie(&http.Cookie{Name: "portal_csrf", Value: nonce, Path: "/admin"})
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != want {
		t.Fatalf("POST %s = %d, want %d: %s", target, response.Code, want, response.Body.String())
	}
	// Without a token the same post has to be refused, or nothing above proves the guard is on.
	unguarded := adminTestRequest(http.MethodPost, target, strings.NewReader(form.Encode()))
	unguarded.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	refused := httptest.NewRecorder()
	server.Handler().ServeHTTP(refused, unguarded)
	if refused.Code != http.StatusForbidden {
		t.Fatalf("POST %s without a CSRF token = %d, want 403", target, refused.Code)
	}
	return response.Body.String()
}

func mustJSON(t *testing.T, value any) []byte {
	t.Helper()
	payload, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	return payload
}

// configManagerHugeFile is the shape that broke the assumption a section is a tractable unit.
// Measured: ItemStacksRewrite/fortis.mods.itemstacksrewrite.weights.cfg declares 1587 entries in a
// SINGLE section, [Item Weights], with a range of "From 0 to 2147484" - a type bound, not a design
// range - against real values whose median is 1. Its path carries a slash because the file lives in a
// subdirectory, which a top-level *.cfg glob never saw.
func configManagerHugeFile(entries int) ConfigSchemaFile {
	rows := make([]ConfigSchemaEntry, 0, entries)
	for index := 0; index < entries; index++ {
		rows = append(rows, ConfigSchemaEntry{
			Key: "Item" + strconv.Itoa(index) + "_weight", Type: "Single", Default: "1", Current: "1",
			Acceptable: ConfigAcceptable{
				Kind: "range",
				Min:  ConfigBound{Text: "0", Number: 0, Set: true},
				Max:  ConfigBound{Text: "2147484", Number: 2147484, Set: true},
			},
		})
	}
	return ConfigSchemaFile{
		File:     "ItemStacksRewrite/fortis.mods.itemstacksrewrite.weights.cfg",
		Source:   configSourceMerged,
		Sections: []ConfigSchemaSection{{Name: "Item Weights", Entries: rows}},
	}
}

// TestConfigManagerSuppressesASliderOverATypeBound is the degenerate-range guard. A slider cannot
// resolve better than about a thousandth of its span, so over "From 0 to 2147484" its smallest
// expressible change is 2147 - larger than every value that actually occurs in that file. Drawing one
// would ship 2,397 controls, 48% of the corpus's ranges, that can only destroy the value they show.
func TestConfigManagerSuppressesASliderOverATypeBound(t *testing.T) {
	degenerate := configManagerHugeFile(1).Sections[0].Entries[0]
	view := configManagerSetting{Value: degenerate.Default}
	configApplyWidget(&view, degenerate)
	if view.Widget != widgetText {
		t.Fatalf("a type-bound range drew a %q instead of a number field", view.Widget)
	}
	if !view.Numeric || view.Min != "0" || view.Max != "2147484" {
		t.Fatalf("the number field lost the schema's bounds: %#v", view)
	}
	// Control: a real design range in the same corpus still gets its slider, so the rule discriminates
	// rather than suppressing every range.
	design := ConfigSchemaEntry{
		Key: "Player Range", Type: "Single", Default: "10",
		Acceptable: ConfigAcceptable{
			Kind: "range",
			Min:  ConfigBound{Text: "1", Number: 1, Set: true},
			Max:  ConfigBound{Text: "100", Number: 100, Set: true},
		},
	}
	live := configManagerSetting{Value: design.Default}
	configApplyWidget(&live, design)
	if live.Widget != widgetSlider {
		t.Fatalf("a genuine design range lost its slider: %q", live.Widget)
	}
	// And the Int32.MaxValue form from stacks.cfg, whose real values top out at 999.
	int32Bound := ConfigSchemaEntry{
		Key: "Wood_stack", Type: "Int32", Default: "30",
		Acceptable: ConfigAcceptable{
			Kind: "range",
			Min:  ConfigBound{Text: "1", Number: 1, Set: true},
			Max:  ConfigBound{Text: "2147483647", Number: 2147483647, Set: true},
		},
	}
	stacks := configManagerSetting{Value: int32Bound.Default}
	configApplyWidget(&stacks, int32Bound)
	if stacks.Widget != widgetText {
		t.Fatalf("Int32.MaxValue was treated as a design range: %q", stacks.Widget)
	}
}

// TestConfigManagerCapsAnEnormousSectionAndSaysSo covers the level below the navigation. A section is
// not automatically small: one real file holds 1587 entries in a single section, so the section view
// needs the same discipline as the mod view - cap, state the count, and point at search.
func TestConfigManagerCapsAnEnormousSectionAndSaysSo(t *testing.T) {
	schema := configManagerSchema()
	schema.Files = append(schema.Files, configManagerHugeFile(1587))
	page := configManagerPage{
		World: PublicWorld{Name: configManagerWorld},
		File:  "ItemStacksRewrite/fortis.mods.itemstacksrewrite.weights.cfg",
	}
	buildConfigManagerPage(&page, schema, ConfigAuthority{}, "", configSettingRef{})
	if page.Rendered != configEntryRenderCap {
		t.Fatalf("rendered = %d, want the cap %d", page.Rendered, configEntryRenderCap)
	}
	if page.Rendered+page.Withheld != 1587 {
		t.Fatalf("rendered %d + withheld %d does not account for all 1587 entries", page.Rendered, page.Withheld)
	}
	// Withholding silently is the failure. The page has to say how many it kept back.
	recorder := httptest.NewRecorder()
	render(recorder, configManagerTemplate, page)
	body := recorder.Body.String()
	if !strings.Contains(body, "1187 more settings are not drawn") {
		t.Fatalf("the page does not state what it withheld: %s", body[:min(len(body), 4000)])
	}
	// Scoped search is what reaches the rest, so it must actually narrow to this one file.
	scoped := configManagerPage{
		World: PublicWorld{Name: configManagerWorld},
		File:  "ItemStacksRewrite/fortis.mods.itemstacksrewrite.weights.cfg",
		Query: "item9_weight",
	}
	buildConfigManagerPage(&scoped, schema, ConfigAuthority{}, "", configSettingRef{})
	if scoped.View != configViewSearch || scoped.Rendered == 0 {
		t.Fatalf("a scoped search found nothing: %#v", scoped.Rendered)
	}
	for _, group := range scoped.Groups {
		if group.File != "ItemStacksRewrite/fortis.mods.itemstacksrewrite.weights.cfg" {
			t.Fatalf("a mod-scoped search reached into %q", group.File)
		}
	}
	// Control, and the sharper half: the SAME query scoped to this file finds nothing, while
	// unscoped it reaches the mod it really lives in. Without this pair, a scope that silently
	// ignored the file parameter would pass the assertions above.
	elsewhere := configManagerPage{
		World: PublicWorld{Name: configManagerWorld},
		File:  "ItemStacksRewrite/fortis.mods.itemstacksrewrite.weights.cfg",
		Query: "BlockingType",
	}
	buildConfigManagerPage(&elsewhere, schema, ConfigAuthority{}, "", configSettingRef{})
	if elsewhere.Rendered != 0 {
		t.Fatalf("a mod-scoped search found %d hits outside that mod", elsewhere.Rendered)
	}
	global := configManagerPage{World: PublicWorld{Name: configManagerWorld}, Query: "BlockingType"}
	buildConfigManagerPage(&global, schema, ConfigAuthority{}, "", configSettingRef{})
	if global.Rendered != 1 || global.Groups[0].File != "org.bepinex.plugins.valheimvrmod.cfg" {
		t.Fatalf("an unscoped search did not reach the mod the key lives in: %#v", global.Groups)
	}
}

// TestConfigManagerStatesWhereAValueCanActuallyGo is the scope contract. The schema is a UNION of the
// world's own config_merged tree and the profile client trees, and each file carries where it came
// from and whether a published profile ships it. Most settings here are server-side and there is no
// server-side write path yet, so presenting such an edit as live would be the exact failure that
// started this feature: a change that looks applied and is not.
func TestConfigManagerStatesWhereAValueCanActuallyGo(t *testing.T) {
	// Measured shape: neuralyze.vrfixes.cfg is client_profile-only, and it is the file the operator
	// has actually been editing.
	client := configFileScopeOf(ConfigSchemaFile{
		File: "neuralyze.vrfixes.cfg", Source: configSourceClient, Shipped: true,
	})
	if client.Scope != configScopeClient || !client.Deliverable || !strings.Contains(client.Reason, "next sync") {
		t.Fatalf("a shipped file is not client scope: %#v", client)
	}
	// In both trees and shipped: valheimvrmod, 145 keys in config_merged and 26 in client-config-vr.
	both := configFileScopeOf(ConfigSchemaFile{
		File: "org.bepinex.plugins.valheimvrmod.cfg", Source: configSourceBoth, Shipped: true,
	})
	if both.Scope != configScopeClient || !both.Deliverable {
		t.Fatalf("a file in both trees that a profile ships is not deliverable: %#v", both)
	}
	// The world's own tree, unshipped: recorded, and honestly not in force. The reason must name the
	// cause, because "not in force" alone sends an operator hunting for a mistake.
	server := configFileScopeOf(ConfigSchemaFile{
		File: "southsil.SouthsilArmor.cfg", Source: configSourceMerged, Shipped: false,
	})
	if server.Scope != configScopeServer || server.Deliverable {
		t.Fatalf("an unshipped file claims it is deliverable: %#v", server)
	}
	for _, phrase := range []string{"the SERVER reads", "no server-side write path yet"} {
		if !strings.Contains(server.Reason, phrase) {
			t.Fatalf("the server-scope reason does not say %q: %q", phrase, server.Reason)
		}
	}
	// A cached payload written before these fields existed is a real state while a world still holds
	// one. Unknown stays unknown: defaulting to client would be the friendlier answer and a lie.
	stale := configFileScopeOf(ConfigSchemaFile{File: "anything.cfg"})
	if stale.Scope != configScopeUnknown || stale.Deliverable {
		t.Fatalf("a payload with no scope information was resolved anyway: %#v", stale)
	}
	// Control, and the reason neither tag carries omitempty: an explicit shipped:false must decode
	// differently from an absent one. Both come from real JSON rather than a struct literal, because
	// a literal cannot express the difference at all.
	var decoded ConfigSchema
	if err := json.Unmarshal([]byte(`{"schema":"world-config-schema/v1","world":"Hrafnheim","files":[`+
		`{"file":"a.cfg","source":"config_merged","shipped":false,"sections":[]},`+
		`{"file":"b.cfg","sections":[]}]}`), &decoded); err != nil {
		t.Fatal(err)
	}
	if got := configFileScopeOf(decoded.Files[0]); got.Scope != configScopeServer {
		t.Fatalf("an explicit shipped:false decoded as %q", got.Scope)
	}
	if got := configFileScopeOf(decoded.Files[1]); got.Scope != configScopeUnknown {
		t.Fatalf("a file with no scope fields decoded as %q", got.Scope)
	}
}

// TestConfigManagerSplitsAppliedFromPending is the at-a-glance half of the same requirement. Two
// things both called "managed", one in force and one waiting, is the worst version of this page.
func TestConfigManagerSplitsAppliedFromPending(t *testing.T) {
	authority := ConfigAuthority{}
	// One from a file a profile ships and one from a file only the server reads, which is the split
	// the page has to make visible.
	for _, ref := range []ConfigSettingRef{
		{File: "Avo.BackpacksVRFix.cfg", Section: "General", Key: "Enable Fix"},
		{File: "Azumatt.AzuAutoStore.cfg", Section: "2 - Storage", Key: "Player Range"},
	} {
		authority[ref] = ConfigSetting{ConfigSettingRef: ref, Value: "true", Policy: PolicyServerForced}
	}
	page := configManagerPage{World: PublicWorld{Name: configManagerWorld}}
	buildConfigManagerPage(&page, configManagerSchema(), authority, "", configSettingRef{})
	if page.Applied != 1 || page.Pending != 1 {
		t.Fatalf("applied = %d pending = %d, want one of each", page.Applied, page.Pending)
	}
	recorder := httptest.NewRecorder()
	render(recorder, configManagerTemplate, page)
	body := recorder.Body.String()
	if !strings.Contains(body, "1 reach players on their next sync") {
		t.Fatalf("the page does not say how many managed settings are in force: %s", body)
	}
	if !strings.Contains(body, "1 recorded but not in force") {
		t.Fatalf("the page does not say how many are pending: %s", body)
	}
	if !strings.Contains(body, `data-deliverable="no"`) {
		t.Fatalf("a pending row is not marked as undeliverable: %s", body)
	}
	if !strings.Contains(body, `data-deliverable="yes"`) {
		t.Fatalf("an applied row is not marked as deliverable: %s", body)
	}
}

// TestConfigManagerScopeReachesTheRenderedRow is the end-to-end half: a file's scope has to arrive on
// the row an operator is looking at, not merely be computed correctly out of sight.
func TestConfigManagerScopeReachesTheRenderedRow(t *testing.T) {
	server := configManagerTestServer(t)
	shipped := configManagerRow(t,
		configManagerSectionView(t, server, "Avo.BackpacksVRFix.cfg", "General"),
		"Enable Fix")
	if !strings.Contains(shipped, `data-scope="client"`) || !strings.Contains(shipped, `data-deliverable="yes"`) {
		t.Fatalf("a shipped file's row does not say it reaches players: %s", shipped)
	}
	unshipped := configManagerRow(t,
		configManagerSectionView(t, server, "Azumatt.AzuAutoStore.cfg", "2 - Storage"),
		"Player Range")
	if !strings.Contains(unshipped, `data-scope="server"`) || !strings.Contains(unshipped, `data-deliverable="no"`) {
		t.Fatalf("an unshipped file's row does not say it is not in force: %s", unshipped)
	}
	// The reason has to be on the page, not only in a data attribute.
	if !strings.Contains(unshipped, "no server-side write path yet") {
		t.Fatalf("an undeliverable row states no reason: %s", unshipped)
	}
	// A world whose cached schema predates the scope fields says so rather than guessing.
	stale := configManagerSchema()
	for index := range stale.Files {
		stale.Files[index].Source, stale.Files[index].Shipped = "", false
	}
	if err := server.store.SaveWorldConfigSchema(t.Context(), configManagerWorld,
		configManagerFingerprint, mustJSON(t, stale)); err != nil {
		t.Fatal(err)
	}
	if page := configManagerView(t, server, ""); !strings.Contains(page, "whether these reach players could not be read") {
		t.Fatalf("a scope-less schema still claims a scope: %s", page)
	}
}
