package app

import (
	"context"
	"encoding/json"
	"path/filepath"
	"strings"
	"testing"
)

// testConfigSchema mirrors real records from
// /media/big4/projects/game/valheim/Hrafnheim/config_merged/bepinex so the refusals are tested
// against the shapes that actually exist on this host, not invented ones:
//
//	Azumatt.AzuAntiArthriticCrafting.cfg, [1 - Crafting Tweaks]
//	 ## ... [Synced with Server]
//	 # Setting type: Toggle
//	 # Acceptable values: Off, On
//	 Reset Crafting Value = On
//	 # Setting type: KeyboardShortcut
//	 # Default value: LeftShift
//	 Incremental Modifier = LeftShift
//
// plus a declared range ("# Acceptable value range: From 0.5 to 2"), a flags enum carrying
// BepInEx's "Multiple values can be set at the same time" marker, and an [Immutable] section.
func testConfigSchema() ConfigSchema {
	return ConfigSchema{
		Schema: configSchemaVersion,
		World:  "Hrafnheim",
		Files: []ConfigSchemaFile{{
			File:    "Azumatt.AzuAntiArthriticCrafting.cfg",
			ModName: "AzuAntiArthriticCrafting",
			Sections: []ConfigSchemaSection{{
				Name: "1 - Crafting Tweaks",
				Entries: []ConfigSchemaEntry{
					{
						Key:        "Reset Crafting Value",
						Type:       "Toggle",
						Default:    "On",
						Acceptable: ConfigAcceptable{Kind: "list", Values: []string{"Off", "On"}},
						Synced:     true,
					},
					{
						Key:        "Paginator",
						Type:       "Toggle",
						Default:    "On",
						Acceptable: ConfigAcceptable{Kind: "list", Values: []string{"Off", "On"}},
					},
					{
						Key:        "Incremental Modifier",
						Type:       "KeyboardShortcut",
						Default:    "LeftShift",
						Acceptable: ConfigAcceptable{Kind: "none"},
					},
					{
						Key:     "Crafting Speed",
						Type:    "Single",
						Default: "1",
						Acceptable: ConfigAcceptable{
							Kind: "range",
							Min:  ConfigBound{Text: "0.5", Number: 0.5, Set: true},
							Max:  ConfigBound{Text: "2", Number: 2, Set: true},
						},
					},
					{
						Key:        "Favorting System",
						Type:       "Boolean",
						Default:    "true",
						Acceptable: ConfigAcceptable{Kind: "none"},
					},
					{
						Key:     "Effect Biomes",
						Type:    "BackpackBiomes",
						Default: "Mountains",
						Acceptable: ConfigAcceptable{
							Kind:     "list",
							Values:   []string{"None", "Meadows", "BlackForest", "Swamp", "Mountains"},
							Multiple: true,
						},
					},
				},
			}, {
				// A section BepInEx marked [Immutable]: fixed for the lifetime of the process.
				Name:      "0 - General",
				Immutable: true,
				Entries: []ConfigSchemaEntry{{
					Key:        "Nexus ID",
					Type:       "Int32",
					Default:    "2565",
					Acceptable: ConfigAcceptable{Kind: "none"},
				}},
			}},
		}},
	}
}

func newConfigStore(t *testing.T) *Store {
	t.Helper()
	store, err := OpenStore(filepath.Join(t.TempDir(), "portal.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { store.Close() })
	return store
}

func craftingRef(key string) ConfigSettingRef {
	return ConfigSettingRef{File: "Azumatt.AzuAntiArthriticCrafting.cfg", Section: "1 - Crafting Tweaks", Key: key}
}

// A synced key is overwritten in memory by the server at runtime whatever the client's file says
// (C4), so offering a client override is a promise the runtime breaks. The refusal has to be in the
// store: a UI-only check is one form POST away from being bypassed.
func TestConfigAuthorityRefusesClientOverrideOfSyncedKey(t *testing.T) {
	ctx := context.Background()
	store := newConfigStore(t)
	schema := testConfigSchema()
	synced := ConfigSetting{ConfigSettingRef: craftingRef("Reset Crafting Value"), Value: "Off", Policy: PolicyClientDefault}
	err := store.SetWorldConfigSetting(ctx, "Hrafnheim", schema, synced, "admin")
	if err == nil {
		t.Fatal("stored a client-overridable policy for a key the server overrides at runtime")
	}
	if !strings.Contains(err.Error(), "synced with the server") {
		t.Fatalf("refusal does not say why: %v", err)
	}
	// Control: the same key, same value, forced by the server, is the policy that CAN hold.
	forced := synced
	forced.Policy = PolicyServerForced
	if err := store.SetWorldConfigSetting(ctx, "Hrafnheim", schema, forced, "admin"); err != nil {
		t.Fatalf("refused a server-forced value for a synced key: %v", err)
	}
	// And an unsynced neighbour in the same section takes client_default, so the refusal is about
	// this key's annotation and not about the policy being unusable everywhere.
	neighbour := ConfigSetting{ConfigSettingRef: craftingRef("Paginator"), Value: "Off", Policy: PolicyClientDefault}
	if err := store.SetWorldConfigSetting(ctx, "Hrafnheim", schema, neighbour, "admin"); err != nil {
		t.Fatalf("refused client_default on an unsynced key: %v", err)
	}
}

// An [Immutable] section cannot change after the game has loaded it, so writing one would publish a
// value the game ignores (C5). Neither policy is acceptable.
func TestConfigAuthorityRefusesImmutableKey(t *testing.T) {
	ctx := context.Background()
	store := newConfigStore(t)
	schema := testConfigSchema()
	ref := ConfigSettingRef{File: "Azumatt.AzuAntiArthriticCrafting.cfg", Section: "0 - General", Key: "Nexus ID"}
	for _, policy := range []ConfigPolicy{PolicyServerForced, PolicyClientDefault} {
		err := store.SetWorldConfigSetting(ctx, "Hrafnheim", schema,
			ConfigSetting{ConfigSettingRef: ref, Value: "1234", Policy: policy}, "admin")
		if err == nil {
			t.Fatalf("stored an immutable key with policy %s", policy)
		}
		if !strings.Contains(err.Error(), "immutable") {
			t.Fatalf("refusal does not say why: %v", err)
		}
	}
	authority, err := store.WorldConfigAuthority(ctx, "Hrafnheim")
	if err != nil {
		t.Fatal(err)
	}
	if len(authority) != 0 {
		t.Fatalf("refused writes still landed rows: %v", authority)
	}
}

// Never store a value the game would refuse. Each case carries its own control: the same entry with
// a value that IS legal must be accepted, otherwise the test proves only that writes fail.
func TestConfigAuthorityRefusesValuesTheGameWouldReject(t *testing.T) {
	ctx := context.Background()
	store := newConfigStore(t)
	schema := testConfigSchema()
	cases := []struct {
		name    string
		key     string
		bad     string
		good    string
		message string
	}{
		{name: "above range", key: "Crafting Speed", bad: "2.5", good: "1.75", message: "between 0.5 and 2"},
		{name: "below range", key: "Crafting Speed", bad: "0.25", good: "0.5", message: "between 0.5 and 2"},
		{name: "not a number", key: "Crafting Speed", bad: "fast", good: "2", message: "between 0.5 and 2"},
		{name: "off the list", key: "Paginator", bad: "Maybe", good: "Off", message: "choose one of: Off, On"},
		{name: "not a boolean", key: "Favorting System", bad: "On", good: "false", message: "enter true or false"},
		{name: "unlisted flag", key: "Effect Biomes", bad: "Swamp, Atlantis", good: "Swamp, Mountains", message: "one or more of"},
		{name: "edge space", key: "Paginator", bad: " On", good: "On", message: "begin or end with a space"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			ref := craftingRef(tc.key)
			err := store.SetWorldConfigSetting(ctx, "Hrafnheim", schema,
				ConfigSetting{ConfigSettingRef: ref, Value: tc.bad, Policy: PolicyServerForced}, "admin")
			if err == nil {
				t.Fatalf("stored %q for %s", tc.bad, tc.key)
			}
			if !strings.Contains(err.Error(), tc.message) {
				t.Fatalf("message does not name the allowed set: %v", err)
			}
			if err := store.SetWorldConfigSetting(ctx, "Hrafnheim", schema,
				ConfigSetting{ConfigSettingRef: ref, Value: tc.good, Policy: PolicyServerForced}, "admin"); err != nil {
				t.Fatalf("control value %q refused: %v", tc.good, err)
			}
		})
	}
}

// A key that is not in the world's schema cannot be validated, so it is not stored. Guessing would
// mean publishing a value no installed mod reads.
func TestConfigAuthorityRefusesKeyOutsideSchema(t *testing.T) {
	ctx := context.Background()
	store := newConfigStore(t)
	err := store.SetWorldConfigSetting(ctx, "Hrafnheim", testConfigSchema(),
		ConfigSetting{ConfigSettingRef: craftingRef("Invented Setting"), Value: "On", Policy: PolicyServerForced}, "admin")
	if err == nil {
		t.Fatal("stored a key the schema does not describe")
	}
	if !strings.Contains(err.Error(), "not in the world's configuration schema") {
		t.Fatalf("unexpected refusal: %v", err)
	}
	// A schema for another world is a wiring mistake, not a validation source.
	other := testConfigSchema()
	other.World = "Doggerland"
	if err := store.SetWorldConfigSetting(ctx, "Hrafnheim", other,
		ConfigSetting{ConfigSettingRef: craftingRef("Paginator"), Value: "Off", Policy: PolicyServerForced}, "admin"); err == nil {
		t.Fatal("validated one world's value against another world's schema")
	}
}

// Clearing must DELETE, returning the key to UNMANAGED. That is C2's third state and it is not the
// same as client_default: unmanaged means the portal writes nothing at all and the mod's own default
// applies, while client_default means the profile carries a starting value.
func TestConfigAuthorityClearReturnsKeyToUnmanaged(t *testing.T) {
	ctx := context.Background()
	store := newConfigStore(t)
	schema := testConfigSchema()
	ref := craftingRef("Paginator")
	if err := store.SetWorldConfigSetting(ctx, "Hrafnheim", schema,
		ConfigSetting{ConfigSettingRef: ref, Value: "Off", Policy: PolicyClientDefault}, "admin"); err != nil {
		t.Fatal(err)
	}
	authority, err := store.WorldConfigAuthority(ctx, "Hrafnheim")
	if err != nil {
		t.Fatal(err)
	}
	managed, ok := authority.Setting(ref.File, ref.Section, ref.Key)
	if !ok {
		t.Fatal("stored setting is not readable")
	}
	if managed.Policy != PolicyClientDefault || managed.Value != "Off" {
		t.Fatalf("unexpected record: %+v", managed)
	}
	if managed.Actor != "admin" || managed.SetAt.IsZero() {
		t.Fatalf("record does not say who set it and when: %+v", managed)
	}
	if err := store.ClearWorldConfigSetting(ctx, "Hrafnheim", ref, "admin"); err != nil {
		t.Fatal(err)
	}
	authority, err = store.WorldConfigAuthority(ctx, "Hrafnheim")
	if err != nil {
		t.Fatal(err)
	}
	cleared, ok := authority.Setting(ref.File, ref.Section, ref.Key)
	if ok {
		t.Fatalf("clearing left a record behind: %+v", cleared)
	}
	if cleared.Policy == PolicyClientDefault {
		t.Fatal("an unmanaged key read back as client_default")
	}
	// The export is the other place the distinction has to survive: an unmanaged key is absent from
	// the document, so the publish path never writes it.
	payload, err := store.ExportWorldConfigAuthority(ctx, "Hrafnheim")
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(payload), "Paginator") {
		t.Fatalf("cleared key still exported: %s", payload)
	}
	// Clearing twice is the state the caller asked for, not a failure.
	if err := store.ClearWorldConfigSetting(ctx, "Hrafnheim", ref, "admin"); err != nil {
		t.Fatalf("clearing an unmanaged key failed: %v", err)
	}
}

// The stored value is the exact string that goes into the .cfg. A keybind chord and a flags enum
// both carry spacing and punctuation the game reads literally, so anything normalising them would
// publish a different keybind from the one the admin chose.
func TestConfigAuthorityValueRoundTripsExactly(t *testing.T) {
	ctx := context.Background()
	store := newConfigStore(t)
	schema := testConfigSchema()
	values := map[string]string{
		"Incremental Modifier": "LeftShift+F",
		"Effect Biomes":        "Swamp, Mountains",
		"Crafting Speed":       "1.50",
	}
	for key, value := range values {
		if err := store.SetWorldConfigSetting(ctx, "Hrafnheim", schema,
			ConfigSetting{ConfigSettingRef: craftingRef(key), Value: value, Policy: PolicyServerForced}, "admin"); err != nil {
			t.Fatalf("%s: %v", key, err)
		}
	}
	// The chord BepInEx itself writes, spaces round the plus, must survive as written too.
	if err := store.SetWorldConfigSetting(ctx, "Hrafnheim", schema,
		ConfigSetting{ConfigSettingRef: craftingRef("Incremental Modifier"), Value: "LeftShift + PageUp", Policy: PolicyServerForced}, "admin"); err != nil {
		t.Fatal(err)
	}
	values["Incremental Modifier"] = "LeftShift + PageUp"
	authority, err := store.WorldConfigAuthority(ctx, "Hrafnheim")
	if err != nil {
		t.Fatal(err)
	}
	for key, want := range values {
		got, ok := authority.Setting("Azumatt.AzuAntiArthriticCrafting.cfg", "1 - Crafting Tweaks", key)
		if !ok {
			t.Fatalf("%s missing", key)
		}
		if got.Value != want {
			t.Fatalf("%s round-tripped as %q, wanted %q", key, got.Value, want)
		}
	}
	var export struct {
		Schema  string `json:"schema"`
		World   string `json:"world"`
		Entries []struct {
			File    string `json:"file"`
			Section string `json:"section"`
			Key     string `json:"key"`
			Value   string `json:"value"`
			Policy  string `json:"policy"`
		} `json:"entries"`
	}
	payload, err := store.ExportWorldConfigAuthority(ctx, "Hrafnheim")
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(payload, &export); err != nil {
		t.Fatal(err)
	}
	if export.Schema != configAuthorityVersion || export.World != "Hrafnheim" {
		t.Fatalf("unexpected export header: %s", payload)
	}
	if len(export.Entries) != len(values) {
		t.Fatalf("exported %d entries, wanted %d: %s", len(export.Entries), len(values), payload)
	}
	// Entries are ordered so two publishes diff cleanly.
	for i := 1; i < len(export.Entries); i++ {
		if export.Entries[i-1].Key > export.Entries[i].Key {
			t.Fatalf("export is not ordered by key: %s", payload)
		}
	}
	for _, entry := range export.Entries {
		if entry.Value != values[entry.Key] {
			t.Fatalf("export mangled %s: %q", entry.Key, entry.Value)
		}
		if entry.Policy != string(PolicyServerForced) {
			t.Fatalf("export lost the policy of %s: %q", entry.Key, entry.Policy)
		}
	}
}

// A keybind that DECLARES its acceptable values declares the ~140-member KeyCode enum, and a chord
// is never a member of it. Real records, Hrafnheim/config_merged/bepinex:
//
//	Azumatt.WardIsLove.cfg:101-105   # Setting type: KeyboardShortcut
//	                                 # Acceptable values: None, Backspace, Tab, ... F, G, ... LeftShift, ...
//	                                 WardHotKey = G
//	Azumatt.AzuAutoStore.cfg:104-107 same list, Pause Shortcut = Period + LeftShift
//
// 8 of the 131 KeyboardShortcut entries carry that list and 36 values in the corpus contain " + ",
// so strict membership would refuse a value the file already holds - an admin pressing save on an
// untouched setting would be told it is invalid. The chord is ONE binding, checked component by
// component, and it is never routed through the flags-enum path: "Period + LeftShift" is a single
// value, not two.
func TestConfigAuthorityAcceptsKeybindChordsAgainstAKeyCodeList(t *testing.T) {
	ctx := context.Background()
	store := newConfigStore(t)
	keycodes := []string{"None", "Backspace", "Tab", "Return", "Pause", "Escape", "Space", "Period", "F", "G", "LeftShift", "RightShift", "PageUp"}
	schema := ConfigSchema{
		Schema: configSchemaVersion,
		World:  "Hrafnheim",
		Files: []ConfigSchemaFile{{
			File: "Azumatt.AzuAutoStore.cfg",
			Sections: []ConfigSchemaSection{{
				Name: "1 - General",
				Entries: []ConfigSchemaEntry{{
					Key:        "Pause Shortcut",
					Type:       "KeyboardShortcut",
					Default:    "Period + LeftShift",
					Acceptable: ConfigAcceptable{Kind: "list", Values: keycodes},
				}, {
					// 415 of 17429 keys carry no "# Setting type" comment at all. Omitting the type
					// is the extractor refusing to invent metadata, so an empty type must stay
					// editable rather than becoming unknown-and-refused.
					Key:        "Untyped Setting",
					Acceptable: ConfigAcceptable{Kind: "none"},
				}},
			}},
		}},
	}
	ref := ConfigSettingRef{File: "Azumatt.AzuAutoStore.cfg", Section: "1 - General", Key: "Pause Shortcut"}
	for _, value := range []string{"G", "Period + LeftShift", "LeftShift+F", "None"} {
		if err := store.SetWorldConfigSetting(ctx, "Hrafnheim", schema,
			ConfigSetting{ConfigSettingRef: ref, Value: value, Policy: PolicyServerForced}, "admin"); err != nil {
			t.Fatalf("refused the keybind %q: %v", value, err)
		}
		authority, err := store.WorldConfigAuthority(ctx, "Hrafnheim")
		if err != nil {
			t.Fatal(err)
		}
		stored, ok := authority.Setting(ref.File, ref.Section, ref.Key)
		if !ok || stored.Value != value {
			t.Fatalf("%q round-tripped as %q (present=%v)", value, stored.Value, ok)
		}
	}
	// Control: a component that is not a key name is still refused, so the chord handling is not a
	// blanket exemption from the list.
	if err := store.SetWorldConfigSetting(ctx, "Hrafnheim", schema,
		ConfigSetting{ConfigSettingRef: ref, Value: "Period + Atlantis", Policy: PolicyServerForced}, "admin"); err == nil {
		t.Fatal("accepted a chord naming a key that does not exist")
	}
	untyped := ConfigSettingRef{File: "Azumatt.AzuAutoStore.cfg", Section: "1 - General", Key: "Untyped Setting"}
	if err := store.SetWorldConfigSetting(ctx, "Hrafnheim", schema,
		ConfigSetting{ConfigSettingRef: untyped, Value: "whatever the mod reads", Policy: PolicyServerForced}, "admin"); err != nil {
		t.Fatalf("a setting whose file states no type became uneditable: %v", err)
	}
	if err := store.SetWorldConfigSetting(ctx, "Hrafnheim", schema,
		ConfigSetting{ConfigSettingRef: untyped, Value: "", Policy: PolicyServerForced}, "admin"); err == nil {
		t.Fatal("accepted a blank as a decision")
	}
}

// A world with nothing managed still exports a well-formed document, so pointing the profile
// builder at it is a no-op rather than a parse failure.
func TestConfigAuthorityExportsEmptyWorld(t *testing.T) {
	ctx := context.Background()
	store := newConfigStore(t)
	payload, err := store.ExportWorldConfigAuthority(ctx, "Doggerland")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(payload), `"entries":[]`) {
		t.Fatalf("empty world exported as %s", payload)
	}
}

// The section's own annotations apply to every key inside it, so Entry folds them in. A caller that
// had to remember to OR them would eventually forget, and the forgetting looks exactly like C4's
// failure: a client override offered for a key the server overrides anyway.
func TestConfigSchemaEntryFoldsSectionFlags(t *testing.T) {
	schema := testConfigSchema()
	schema.Files[0].Sections[0].Synced = true
	entry, ok := schema.Entry("Azumatt.AzuAntiArthriticCrafting.cfg", "1 - Crafting Tweaks", "Paginator")
	if !ok {
		t.Fatal("entry not found")
	}
	if !entry.Synced {
		t.Fatal("a key in a synced section did not inherit the annotation")
	}
	if err := ValidateConfigSetting(entry, "Off", PolicyClientDefault); err == nil {
		t.Fatal("client override accepted for a key in a synced section")
	}
	// Control: with the section unannotated the same key takes the same policy.
	schema.Files[0].Sections[0].Synced = false
	entry, _ = schema.Entry("Azumatt.AzuAntiArthriticCrafting.cfg", "1 - Crafting Tweaks", "Paginator")
	if err := ValidateConfigSetting(entry, "Off", PolicyClientDefault); err != nil {
		t.Fatalf("control refused: %v", err)
	}
}

// The extractor emits range bounds as raw string tokens to avoid float mangling; a number is
// tolerated too. A bound of zero must stay distinguishable from a missing bound, because
// "From -0.05 to 0" is a real range in these files.
func TestConfigBoundDecoding(t *testing.T) {
	var acceptable ConfigAcceptable
	if err := json.Unmarshal([]byte(`{"kind":"range","min":"-0.05","max":0}`), &acceptable); err != nil {
		t.Fatal(err)
	}
	if !acceptable.Min.Set || acceptable.Min.Text != "-0.05" || acceptable.Min.Number != -0.05 {
		t.Fatalf("min decoded as %+v", acceptable.Min)
	}
	if !acceptable.Max.Set || acceptable.Max.Number != 0 {
		t.Fatalf("a max of zero read as absent: %+v", acceptable.Max)
	}
	var absent ConfigAcceptable
	if err := json.Unmarshal([]byte(`{"kind":"none"}`), &absent); err != nil {
		t.Fatal(err)
	}
	if absent.Min.Set || absent.Max.Set {
		t.Fatalf("absent bounds read as set: %+v", absent)
	}
	entry := ConfigSchemaEntry{Key: "Offset", Type: "Single", Acceptable: acceptable}
	if err := ValidateConfigSetting(entry, "0.01", PolicyServerForced); err == nil {
		t.Fatal("a value above a zero max was accepted")
	}
	if err := ValidateConfigSetting(entry, "0", PolicyServerForced); err != nil {
		t.Fatalf("the zero bound itself was refused: %v", err)
	}
}

// The cached schema payload is derived and replaceable, and no row is an empty answer rather than a
// failure - a world whose schema has never been extracted is a state the page renders.
func TestWorldConfigSchemaPayloadRoundTrip(t *testing.T) {
	ctx := context.Background()
	store := newConfigStore(t)
	fingerprint, payload, err := store.WorldConfigSchemaPayload(ctx, "Hrafnheim")
	if err != nil {
		t.Fatal(err)
	}
	if fingerprint != "" || payload != nil {
		t.Fatalf("unset schema read back as %q/%q", fingerprint, payload)
	}
	first := strings.Repeat("a", 64)
	if err := store.SaveWorldConfigSchema(ctx, "Hrafnheim", first, []byte(`{"schema":"world-config-schema/v1"}`)); err != nil {
		t.Fatal(err)
	}
	second := strings.Repeat("b", 64)
	if err := store.SaveWorldConfigSchema(ctx, "Hrafnheim", second, []byte(`{"schema":"world-config-schema/v1","files":[]}`)); err != nil {
		t.Fatal(err)
	}
	fingerprint, payload, err = store.WorldConfigSchemaPayload(ctx, "Hrafnheim")
	if err != nil {
		t.Fatal(err)
	}
	if fingerprint != second || !strings.Contains(string(payload), `"files"`) {
		t.Fatalf("rebuild did not replace the cached schema: %q/%s", fingerprint, payload)
	}
	if err := store.SaveWorldConfigSchema(ctx, "Hrafnheim", "short", []byte(`{}`)); err == nil {
		t.Fatal("accepted a schema without a real fingerprint")
	}
}
