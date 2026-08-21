package main

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

// A real record from Hrafnheim's merged config, ZenDragon.ZenBreeding.cfg, which is the file
// that proved the mixed-terminator case: the two comment lines below end CRLF while every
// other line in the same block ends LF. Anything that reads with bufio.ScanLines or Python's
// splitlines() normalises the lot, which is a hundred-line diff for a one-line change.
const mixedTerminatorCFG = "## Settings file was created by plugin ZenBreeding v0.6.1\n" +
	"## Plugin GUID: ZenDragon.ZenBreeding\n" +
	"\n" +
	"[_Debug]\n" +
	"\n" +
	"## Generate additional debug information in the logs for this mod.\r\n" +
	"## This will cause a performance hit and should only be used for debugging.\n" +
	"# Setting type: Boolean\n" +
	"# Default value: false\n" +
	"Enable Logging = false\n" +
	"\n" +
	"[Breeding]\n" +
	"\n" +
	"## [Admin] Prevent exponential reproduction of tamed creatures.\r\n" +
	"# Setting type: String\n" +
	"# Default value: All\n" +
	"# Acceptable values: None, All, Excess\n" +
	"Protection = All\n"

func TestAuthorityRewritesOnlyTheManagedValue(t *testing.T) {
	patched := patchCFGAuthority([]byte(mixedTerminatorCFG), map[cfgAddr]configAuthorityEntry{
		{Section: "Breeding", Key: "Protection"}: {
			File: "ZenDragon.ZenBreeding.cfg", Section: "Breeding", Key: "Protection",
			Value: "Excess", Policy: policyServerForced,
		},
	})

	// The comment metadata is the only machine-readable schema BepInEx publishes, and the
	// extractor parses exactly these lines. Compare them line for line, terminators included.
	wantComments := commentLines(mixedTerminatorCFG)
	gotComments := commentLines(string(patched))
	if !reflect.DeepEqual(gotComments, wantComments) {
		t.Fatalf("comment lines changed\n got: %q\nwant: %q", gotComments, wantComments)
	}
	if strings.Count(string(patched), "\r\n") != 2 {
		t.Fatalf("CRLF count = %d, want the source's 2", strings.Count(string(patched), "\r\n"))
	}
	if !strings.Contains(string(patched), "Protection = Excess\n") {
		t.Fatalf("managed value was not written: %q", string(patched))
	}
	// The one unmanaged key in the file is untouched, spacing and all.
	if !strings.Contains(string(patched), "Enable Logging = false\n") {
		t.Fatalf("unmanaged key was rewritten: %q", string(patched))
	}
	// Exactly one line differs from the source. Anything more is a reflow.
	if changed := changedLines(mixedTerminatorCFG, string(patched)); len(changed) != 1 || changed[0] != "Protection = Excess" {
		t.Fatalf("changed lines = %q, want just the managed one", changed)
	}
}

// A key with no record is the third state of C2: not forced, not a client default, simply not
// managed. The file must come out byte-identical, because the profile's client-config files
// are the fleet's real configuration and rewriting one is a regression an operator feels.
func TestAuthorityLeavesUnmanagedFilesByteIdentical(t *testing.T) {
	dir := t.TempDir()
	configDir := filepath.Join(dir, "config")
	if err := os.MkdirAll(configDir, 0o755); err != nil {
		t.Fatal(err)
	}
	untouched := "[8 - Input bridge]\r\nGripPlusJumpDodges = false\r\nAttackReachMetres = 3\r\n"
	if err := os.WriteFile(filepath.Join(configDir, "neuralyze.vrfixes.cfg"), []byte(untouched), 0o600); err != nil {
		t.Fatal(err)
	}
	managedName := "managed.cfg"
	if err := os.WriteFile(filepath.Join(configDir, managedName), []byte("[S]\nKey = old\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	entries, err := collectConfigEntries(configDir)
	if err != nil {
		t.Fatal(err)
	}
	options := builderOptions{World: "Hrafnheim", Profile: "hrafnheim-vr", ProfileVersion: "2.5.112"}
	layered, baseline, err := applyConfigAuthority(entries, &worldConfigAuthority{
		Schema: configAuthoritySchema, World: "Hrafnheim",
		Entries: []configAuthorityEntry{{File: managedName, Section: "S", Key: "Key", Value: "new", Policy: policyClientDefault}},
	}, options)
	if err != nil {
		t.Fatal(err)
	}

	for _, entry := range layered {
		switch entry.zipName {
		case "config/neuralyze.vrfixes.cfg":
			// Still streamed straight from the source file: not read, not re-encoded.
			if entry.body != nil || entry.source == "" {
				t.Fatalf("unmanaged file was rewritten: body=%q source=%q", entry.body, entry.source)
			}
			data, err := os.ReadFile(entry.source)
			if err != nil {
				t.Fatal(err)
			}
			if string(data) != untouched {
				t.Fatalf("unmanaged file content = %q, want %q", data, untouched)
			}
		case "config/" + managedName:
			if string(entry.body) != "[S]\nKey = new\n" {
				t.Fatalf("managed file = %q", entry.body)
			}
		}
	}
	if len(baseline.Entries) != 1 || baseline.Entries[0].Key != "Key" || baseline.Entries[0].Written != "new" {
		t.Fatalf("baseline = %+v", baseline.Entries)
	}
	if baseline.Entries[0].Policy != policyClientDefault {
		t.Fatalf("policy = %q", baseline.Entries[0].Policy)
	}
	if baseline.Schema != settingsBaselineSchema || baseline.World != "Hrafnheim" || baseline.Version != "2.5.112" {
		t.Fatalf("baseline identity = %+v", baseline)
	}
}

// Two publishes of the same authority must produce the same bytes, or every republish looks
// like a change to whoever diffs the artifacts. Both lists are sorted, since an unshipped
// record is as much a published fact as a written one.
func TestBaselineEntriesAreSortedAndStable(t *testing.T) {
	authority := &worldConfigAuthority{Schema: configAuthoritySchema, World: "Hrafnheim", Entries: []configAuthorityEntry{
		{File: "b.cfg", Section: "S", Key: "Two", Value: "2", Policy: policyServerForced},
		{File: "a.cfg", Section: "Z", Key: "One", Value: "1", Policy: policyClientDefault},
		{File: "a.cfg", Section: "A", Key: "Zed", Value: "z", Policy: policyServerForced},
		{File: "a.cfg", Section: "A", Key: "Abe", Value: "a", Policy: policyServerForced},
		// Not shipped by the profile below, so it lands in the other list.
		{File: "c.cfg", Section: "S", Key: "Three", Value: "3", Policy: policyServerForced},
	}}
	shipped := []configEntry{
		{zipName: "config/", isDir: true},
		{zipName: "config/a.cfg", body: []byte("[A]\nAbe = 0\nZed = 0\n[Z]\nOne = 0\n")},
		{zipName: "config/b.cfg", body: []byte("[S]\nTwo = 0\n")},
	}
	_, baseline, err := applyConfigAuthority(shipped, authority, builderOptions{World: "Hrafnheim", Profile: "p"})
	if err != nil {
		t.Fatal(err)
	}
	got := make([]string, 0, len(baseline.Entries))
	for _, entry := range baseline.Entries {
		got = append(got, entry.File+"/"+entry.Section+"/"+entry.Key)
	}
	want := []string{"a.cfg/A/Abe", "a.cfg/A/Zed", "a.cfg/Z/One", "b.cfg/S/Two"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("baseline order = %v, want %v", got, want)
	}
	if len(baseline.Unshipped) != 1 || baseline.Unshipped[0].File != "c.cfg" ||
		baseline.Unshipped[0].Value != "3" || baseline.Unshipped[0].Reason != reasonConfigFileNotShipped {
		t.Fatalf("unshipped = %+v", baseline.Unshipped)
	}
}

// A record for a key the file does not have still has to reach the file: a forced value that
// never lands does nothing, and the failure is invisible.
func TestAuthorityAppendsMissingKeysAndSections(t *testing.T) {
	patched := string(patchCFGAuthority([]byte("[Known]\r\nPresent = 1\r\n"), map[cfgAddr]configAuthorityEntry{
		{Section: "Known", Key: "Added"}:   {Key: "Added", Value: "yes", Policy: policyServerForced},
		{Section: "Fresh", Key: "Another"}: {Key: "Another", Value: "no", Policy: policyClientDefault},
	}))
	// A CRLF file gets CRLF appends, so the file does not end up half and half.
	want := "[Known]\r\nPresent = 1\r\n## portal-managed (server_forced)\r\nAdded = yes\r\n" +
		"\r\n[Fresh]\r\n## portal-managed (client_default)\r\nAnother = no\r\n"
	if patched != want {
		t.Fatalf("appended output =\n%q\nwant\n%q", patched, want)
	}
}

func TestAuthorityRewritesEveryOccurrenceAndKeepsSpacing(t *testing.T) {
	// BepInEx takes the last assignment, so rewriting only the first would leave a stale line
	// that still wins. Spacing around '=' is whatever the file wrote.
	patched := string(patchCFGAuthority([]byte("[S]\nKey=old\nKey  =  old\n## Default value: Key = old\n"),
		map[cfgAddr]configAuthorityEntry{{Section: "S", Key: "Key"}: {Key: "Key", Value: "new", Policy: policyServerForced}}))
	want := "[S]\nKey=new\nKey  =  new\n## Default value: Key = old\n"
	if patched != want {
		t.Fatalf("patched = %q, want %q", patched, want)
	}
}

// Verbatim from Azumatt.WardIsLove.cfg, lines 4-14 of the VR profile's client-config. Mod
// authors put whatever they like in section and key names, and this one carries a '#' in the
// MIDDLE of the section header. A writer that treated '#' as a comment marker anywhere but at
// line start would drop the section and reattach every key below it to the wrong one; a writer
// that restricted names to a character class would drop the key. Both mistakes were made
// tonight by grep patterns that returned plausible numbers.
func TestAuthorityHandlesHostileRealSectionAndEmptyValues(t *testing.T) {
	original := "[<color#00FFFF>Thor</color>]\n" +
		"\n" +
		"## Build Category where <color=#00FFFF>Thor</color> is available.\n" +
		"# Setting type: BuildPieceCategory\n" +
		"# Default value: Misc\n" +
		"# Acceptable values: Misc, Crafting, BuildingWorkbench\n" +
		"Build Table Category = Misc\n" +
		"\n" +
		"# Setting type: String\n" +
		"# Default value: \n" +
		"Custom Build Category = \n"
	patched := string(patchCFGAuthority([]byte(original), map[cfgAddr]configAuthorityEntry{
		{Section: "<color#00FFFF>Thor</color>", Key: "Build Table Category"}: {Key: "Build Table Category", Value: "Crafting", Policy: policyServerForced},
		// An empty value getting a real one: the trailing space after '=' is the file's, so it
		// stays and the value follows it.
		{Section: "<color#00FFFF>Thor</color>", Key: "Custom Build Category"}: {Key: "Custom Build Category", Value: "Thor", Policy: policyClientDefault},
	}))
	want := strings.Replace(original, "Build Table Category = Misc", "Build Table Category = Crafting", 1)
	want = strings.Replace(want, "Custom Build Category = \n", "Custom Build Category = Thor\n", 1)
	if patched != want {
		t.Fatalf("patched =\n%q\nwant\n%q", patched, want)
	}
	// The comment carrying '#' mid-line is untouched, and so is the metadata block.
	if !strings.Contains(patched, "## Build Category where <color=#00FFFF>Thor</color> is available.\n") {
		t.Fatalf("mid-line '#' comment was mangled: %q", patched)
	}
}

// Five real config files live in SUBDIRECTORIES under a world's config root -
// ItemStacksRewrite/ and shudnal.ConditionalConfigSync/ - holding 2,437 entries between them,
// and every count taken with a top-level *.cfg glob missed all five. So `file` genuinely
// carries a slash, and flattening it to a basename would write two mods' settings to one path.
func TestAuthorityKeepsNestedConfigPaths(t *testing.T) {
	nested := "ItemStacksRewrite/fortis.mods.itemstacksrewrite.weights.cfg"
	shipped := configEntry{zipName: "config/" + nested, body: []byte("[Weights]\r\nWood = 1\r\n")}
	layered, baseline, err := applyConfigAuthority(
		[]configEntry{{zipName: "config/", isDir: true}, shipped},
		&worldConfigAuthority{Schema: configAuthoritySchema, World: "Hrafnheim", Entries: []configAuthorityEntry{
			{File: nested, Section: "Weights", Key: "Wood", Value: "2", Policy: policyServerForced},
			// A nested file the profile does not ship: refused, and recorded with its
			// subdirectory intact rather than flattened to the config root.
			{File: "shudnal.ConditionalConfigSync/ConditionalConfigSync.SyncPolicy.cfg", Section: "S", Key: "K", Value: "v", Policy: policyServerForced},
		}}, builderOptions{World: "Hrafnheim", Profile: "hrafnheim-vr"})
	if err != nil {
		t.Fatal(err)
	}
	found := map[string]string{}
	for _, entry := range layered {
		if !entry.isDir {
			found[entry.zipName] = string(entry.body)
		}
	}
	if got := found["config/"+nested]; got != "[Weights]\r\nWood = 2\r\n" {
		t.Fatalf("nested shipped file = %q", got)
	}
	unshipped := "shudnal.ConditionalConfigSync/ConditionalConfigSync.SyncPolicy.cfg"
	if _, ok := found["config/"+unshipped]; ok {
		t.Fatalf("a file the profile does not ship was created: %v", found)
	}
	if len(baseline.Unshipped) != 1 || baseline.Unshipped[0].File != unshipped {
		t.Fatalf("unshipped record = %+v", baseline.Unshipped)
	}
	// No entry landed at the config root under a flattened name.
	for name := range found {
		if strings.Contains(name, "itemstacksrewrite") && name != "config/"+nested {
			t.Fatalf("nested path was flattened to %q", name)
		}
	}
	for _, entry := range baseline.Entries {
		if !strings.Contains(entry.File, "/") {
			t.Fatalf("baseline lost the subdirectory: %+v", entry)
		}
	}
}

func TestAuthorityKeepsTheBOMAndAMissingFinalNewline(t *testing.T) {
	// 26 of the 100 plugin manifests on this host carry a BOM; configs are no different, and
	// dropping one is a change nobody asked for.
	patched := string(patchCFGAuthority([]byte("\xef\xbb\xbf[S]\nKey = old"),
		map[cfgAddr]configAuthorityEntry{{Section: "S", Key: "Key"}: {Key: "Key", Value: "new", Policy: policyServerForced}}))
	if patched != "\xef\xbb\xbf[S]\nKey = new" {
		t.Fatalf("patched = %q", patched)
	}
}

func TestAuthorityHandlesKeysBeforeAnySection(t *testing.T) {
	// BepInEx writes bare keys for a file with no sections. The empty section is a real
	// address, not an error.
	patched := string(patchCFGAuthority([]byte("Loose = old\n[S]\nOther = 1\n"),
		map[cfgAddr]configAuthorityEntry{{Section: "", Key: "Loose"}: {Key: "Loose", Value: "new", Policy: policyServerForced}}))
	if patched != "Loose = new\n[S]\nOther = 1\n" {
		t.Fatalf("patched = %q", patched)
	}
}

// The refusal that keeps the feature honest. The schema an admin edits comes from the world's
// config_merged/bepinex, which is what the SERVER reads, and only 22 of Hrafnheim's 113 config
// files belong to a plugin the client installs. Creating a client .cfg for one of the other 91
// would write the value where the plugin is never loaded and the file never read, while never
// writing it where it would take effect - a value that looks applied and is not, which is the
// wrist-keybind failure. So it is refused, and recorded rather than dropped.
func TestAuthorityRefusesAFileTheProfileDoesNotShip(t *testing.T) {
	layered, baseline, err := applyConfigAuthority(
		[]configEntry{{zipName: "config/", isDir: true}},
		&worldConfigAuthority{Schema: configAuthoritySchema, World: "Hrafnheim", Entries: []configAuthorityEntry{
			{File: "server.only.cfg", Section: "S", Key: "Key", Value: "on", Policy: policyServerForced},
		}}, builderOptions{World: "Hrafnheim", Profile: "p"})
	if err != nil {
		t.Fatal(err)
	}
	for _, entry := range layered {
		if entry.zipName == "config/server.only.cfg" {
			t.Fatalf("a file the profile does not ship was created: %q", entry.body)
		}
	}
	// Nothing claims to have been written.
	if len(baseline.Entries) != 0 {
		t.Fatalf("baseline claims a write that did not happen: %+v", baseline.Entries)
	}
	// And nothing was silently lost.
	want := settingsUnshippedEntry{File: "server.only.cfg", Section: "S", Key: "Key",
		Policy: policyServerForced, Value: "on", Reason: reasonConfigFileNotShipped}
	if len(baseline.Unshipped) != 1 || baseline.Unshipped[0] != want {
		t.Fatalf("unshipped = %+v, want %+v", baseline.Unshipped, want)
	}
	// The document still carries the record, and `value` is deliberately not `written`.
	encoded, err := encodeSettingsBaseline(baseline)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(encoded), `"unshipped":[{"file":"server.only.cfg"`) ||
		strings.Contains(string(encoded), `"written":"on"`) {
		t.Fatalf("encoded baseline = %s", encoded)
	}
}

// An empty list is omitted, so a profile with nothing unshipped emits the document it always
// did rather than growing a field the installer would have to reason about.
func TestBaselineOmitsAnEmptyUnshippedList(t *testing.T) {
	_, baseline, err := applyConfigAuthority(
		[]configEntry{{zipName: "config/", isDir: true}, {zipName: "config/mod.cfg", body: []byte("[S]\nKey = old\n")}},
		&worldConfigAuthority{Schema: configAuthoritySchema, World: "Hrafnheim", Entries: []configAuthorityEntry{
			{File: "mod.cfg", Section: "S", Key: "Key", Value: "new", Policy: policyServerForced},
		}}, builderOptions{World: "Hrafnheim", Profile: "p"})
	if err != nil {
		t.Fatal(err)
	}
	encoded, err := encodeSettingsBaseline(baseline)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(encoded), "unshipped") {
		t.Fatalf("encoded baseline carries an empty unshipped list: %s", encoded)
	}
}

func TestConfigAuthorityDocumentIsValidated(t *testing.T) {
	valid := `{"schema":"world-config-authority/v1","world":"Hrafnheim","generated_at":"2026-08-21T00:00:00Z",` +
		`"entries":[{"file":"a.cfg","section":"S","key":"K","value":"v","policy":"server_forced"}]}`
	if _, err := decodeConfigAuthority([]byte(valid), "Hrafnheim"); err != nil {
		t.Fatalf("valid document rejected: %v", err)
	}
	// Control: the same document with a BOM, which strict JSON decoding refuses.
	if _, err := decodeConfigAuthority(append([]byte("\xef\xbb\xbf"), valid...), "Hrafnheim"); err != nil {
		t.Fatalf("BOM document rejected: %v", err)
	}
	// An empty world is a clean no-op, not a failure.
	empty := `{"schema":"world-config-authority/v1","world":"Hrafnheim","generated_at":"2026-08-21T00:00:00Z","entries":[]}`
	if document, err := decodeConfigAuthority([]byte(empty), "Hrafnheim"); err != nil || len(document.Entries) != 0 {
		t.Fatalf("empty document = %v, %v", document, err)
	}

	for name, cases := range map[string]struct {
		body string
		want string
	}{
		"wrong world":   {strings.Replace(valid, `"Hrafnheim"`, `"Doggerland"`, 1), "is for world"},
		"wrong schema":  {strings.Replace(valid, configAuthoritySchema, "world-config-authority/v2", 1), "schema is"},
		"bad policy":    {strings.Replace(valid, policyServerForced, "maybe", 1), "has policy"},
		"escaping file": {strings.Replace(valid, `"a.cfg"`, `"../a.cfg"`, 1), "unsafe config path"},
		"duplicate": {`{"schema":"world-config-authority/v1","world":"Hrafnheim","generated_at":"x","entries":[` +
			`{"file":"a.cfg","section":"S","key":"K","value":"1","policy":"server_forced"},` +
			`{"file":"a.cfg","section":"S","key":"K","value":"2","policy":"server_forced"}]}`, "twice"},
	} {
		if _, err := decodeConfigAuthority([]byte(cases.body), "Hrafnheim"); err == nil || !strings.Contains(err.Error(), cases.want) {
			t.Fatalf("%s = %v, want an error containing %q", name, err, cases.want)
		}
	}
}

// This is a guard against a fleet-wide lockout, not a style check. An installed client admits
// only `profile-manifest.json` and anything under `config/` (unpackProfileDefinition, the
// `default:` branch returning "profile definition contains an unsupported file"), there is no
// self-update path, and sync runs before the game launches - so a definition an installed
// client cannot parse stops players launching Valheim. Moving this name back to the archive
// root would do that on the first routine republish.
func TestTheBaselineLivesWhereAnOldClientAlreadyAcceptsIt(t *testing.T) {
	if !strings.HasPrefix(settingsBaselineZIPName, "config/") {
		t.Fatalf("settings baseline is at %q; a top-level member is rejected by every already-installed client", settingsBaselineZIPName)
	}
	if strings.Count(settingsBaselineZIPName, "/") != 1 || strings.HasSuffix(settingsBaselineZIPName, "/") {
		t.Fatalf("settings baseline path %q is not a file directly under config/", settingsBaselineZIPName)
	}
}

// The portal owns this name. Two archive entries with one path is a corrupt ZIP, and an
// authority record aimed at it would be the portal overwriting its own manifest.
func TestTheBaselineNameCannotBeCollidedWith(t *testing.T) {
	dir := t.TempDir()
	configDir := filepath.Join(dir, "config")
	if err := os.MkdirAll(configDir, 0o755); err != nil {
		t.Fatal(err)
	}
	name := strings.TrimPrefix(settingsBaselineZIPName, "config/")
	if err := os.WriteFile(filepath.Join(configDir, name), []byte("{}"), 0o600); err != nil {
		t.Fatal(err)
	}
	entries, err := collectConfigEntries(configDir)
	if err != nil {
		t.Fatal(err)
	}
	authority := &worldConfigAuthority{Schema: configAuthoritySchema, World: "Hrafnheim"}
	if _, _, err := applyConfigAuthority(entries, authority, builderOptions{World: "Hrafnheim", Profile: "p"}); err == nil ||
		!strings.Contains(err.Error(), "already contains") {
		t.Fatalf("shipped collision = %v", err)
	}
	if _, _, err := applyConfigAuthority(nil, &worldConfigAuthority{
		Schema: configAuthoritySchema, World: "Hrafnheim",
		Entries: []configAuthorityEntry{{File: name, Section: "S", Key: "K", Value: "v", Policy: policyServerForced}},
	}, builderOptions{World: "Hrafnheim", Profile: "p"}); err == nil ||
		!strings.Contains(err.Error(), "which the portal writes itself") {
		t.Fatalf("authority collision = %v", err)
	}
}

// The whole point of the archive change: one new member UNDER config/, where the archive
// allowlist of an already-installed client already admits it, and only when the build was
// given an authority source. A top-level member would be rejected by every client that has
// not replaced its launcher, and a rejected definition stops the game launching.
func TestBuiltProfileCarriesTheBaselineOnlyWhenAuthorityIsGiven(t *testing.T) {
	dir := t.TempDir()
	configDir := filepath.Join(dir, "config")
	if err := os.MkdirAll(configDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(configDir, "mod.cfg"), []byte("[S]\r\nForced = old\r\nMine = old\r\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	authorityPath := filepath.Join(dir, "authority.json")
	if err := os.WriteFile(authorityPath, []byte(`{"schema":"world-config-authority/v1","world":"world-one",`+
		`"generated_at":"2026-08-21T00:00:00Z","entries":[`+
		`{"file":"mod.cfg","section":"S","key":"Forced","value":"server","policy":"server_forced"},`+
		`{"file":"mod.cfg","section":"S","key":"Mine","value":"seed","policy":"client_default"}]}`), 0o600); err != nil {
		t.Fatal(err)
	}

	options := builderOptions{
		SourceManifestPath:  writeManagedManifest(t, dir, `{"schema_version":2,"packages":[]}`),
		World:               "world-one",
		Profile:             "world-one-vr",
		ClientType:          "flat",
		Audience:            "player",
		ConfigDir:           configDir,
		Output:              filepath.Join(dir, "with-authority.zip"),
		TrueNonVR:           true,
		ConfigAuthorityPath: authorityPath,
		ProfileVersion:      "2.5.112",
	}
	if err := buildProfileDefinition(context.Background(), options); err != nil {
		t.Fatal(err)
	}
	want := []string{"profile-manifest.json", "config/", "config/mod.cfg", settingsBaselineZIPName}
	if entries := zipEntries(t, options.Output); !reflect.DeepEqual(entries, want) {
		t.Fatalf("ZIP entries = %v, want %v", entries, want)
	}
	if got := string(readZIPEntry(t, options.Output, "config/mod.cfg")); got != "[S]\r\nForced = server\r\nMine = seed\r\n" {
		t.Fatalf("shipped config = %q", got)
	}
	var baseline settingsBaseline
	if err := json.Unmarshal(readZIPEntry(t, options.Output, settingsBaselineZIPName), &baseline); err != nil {
		t.Fatal(err)
	}
	if baseline.Schema != settingsBaselineSchema || baseline.Profile != "world-one-vr" || baseline.Version != "2.5.112" {
		t.Fatalf("baseline identity = %+v", baseline)
	}
	if len(baseline.Entries) != 2 || baseline.Entries[0].Written != "server" || baseline.Entries[1].Written != "seed" {
		t.Fatalf("baseline entries = %+v", baseline.Entries)
	}

	// Control: the same build with no authority source is the artifact this builder produced
	// before the feature, with no baseline member at all.
	options.ConfigAuthorityPath = ""
	options.Output = filepath.Join(dir, "without-authority.zip")
	if err := buildProfileDefinition(context.Background(), options); err != nil {
		t.Fatal(err)
	}
	if entries := zipEntries(t, options.Output); !reflect.DeepEqual(entries, []string{"profile-manifest.json", "config/", "config/mod.cfg"}) {
		t.Fatalf("no-authority ZIP entries = %v", entries)
	}
	if got := string(readZIPEntry(t, options.Output, "config/mod.cfg")); got != "[S]\r\nForced = old\r\nMine = old\r\n" {
		t.Fatalf("no-authority config = %q", got)
	}
}

func TestBuildRefusesBothAuthoritySources(t *testing.T) {
	dir := t.TempDir()
	configDir := filepath.Join(dir, "config")
	if err := os.MkdirAll(configDir, 0o755); err != nil {
		t.Fatal(err)
	}
	err := buildProfileDefinition(context.Background(), builderOptions{
		SourceManifestPath:      writeManagedManifest(t, dir, `{"schema_version":2,"packages":[]}`),
		World:                   "world-one",
		Profile:                 "world-one-vr",
		ClientType:              "flat",
		Audience:                "player",
		ConfigDir:               configDir,
		Output:                  filepath.Join(dir, "profile.zip"),
		TrueNonVR:               true,
		ConfigAuthorityPath:     filepath.Join(dir, "authority.json"),
		ConfigAuthorityDatabase: filepath.Join(dir, "portal.sqlite"),
	})
	if err == nil || !strings.Contains(err.Error(), "mutually exclusive") {
		t.Fatalf("err = %v", err)
	}
}

func commentLines(text string) []string {
	out := []string{}
	for _, line := range splitCFGLines([]byte(text)) {
		if strings.HasPrefix(strings.TrimSpace(line.text), "#") {
			out = append(out, line.text+line.term)
		}
	}
	return out
}

func changedLines(before, after string) []string {
	old := map[string]int{}
	for _, line := range splitCFGLines([]byte(before)) {
		old[line.text+line.term]++
	}
	changed := []string{}
	for _, line := range splitCFGLines([]byte(after)) {
		key := line.text + line.term
		if old[key] > 0 {
			old[key]--
			continue
		}
		changed = append(changed, line.text)
	}
	return changed
}
