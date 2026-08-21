package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const managedConfigName = "neuralyze.vrfixes.cfg"

// managedConfigBody is shaped like a real BepInEx config: a comment block above
// the key carrying the setting's type and its acceptable values, mixed line
// endings, and a second setting the portal does not manage.
//
// Mixed endings are not contrived. /media/big4/projects/game/valheim/Hrafnheim/
// config_merged/bepinex/ZenDragon.ZenBreeding.cfg is LF on line 10 and CRLF on
// line 14, in one file. Comparing the whole file against this template is what
// proves an edit replaced one value and nothing else - the comment block is the
// only machine-readable schema the extractor has.
func managedConfigBody(value string) string {
	return "## Settings file was created by plugin VRFixes v1.0.0\n" +
		"## Plugin GUID: neuralyze.vrfixes\n" +
		"\n" +
		"[11 - Hover actions]\r\n" +
		"## Which modifier must be held for a hover action.\r\n" +
		"# Setting type: String\r\n" +
		"# Default value: RightGrip\r\n" +
		"# Acceptable values: RightGrip, LeftGrip, None\r\n" +
		"Modifier = " + value + "\r\n" +
		"\n" +
		"[12 - Other]\n" +
		"Unmanaged = keep\n"
}

// managedConfigWithoutKey is the same file before the mod ever wrote the key,
// which is the state that gets seeded rather than protected.
const managedConfigWithoutKey = "## Settings file was created by plugin VRFixes v1.0.0\n" +
	"## Plugin GUID: neuralyze.vrfixes\n" +
	"\n" +
	"[12 - Other]\n" +
	"Unmanaged = keep\n"

func managedBaseline(policy, written string) *settingsBaseline {
	return &settingsBaseline{
		Schema:  settingsBaselineSchema,
		World:   "world",
		Profile: "alpha",
		Version: "1.0.0",
		Entries: []settingsBaselineEntry{{
			File:    managedConfigName,
			Section: "11 - Hover actions",
			Key:     "Modifier",
			Policy:  policy,
			Written: written,
		}},
	}
}

func emptyBaseline() *settingsBaseline {
	return &settingsBaseline{Schema: settingsBaselineSchema, World: "world", Profile: "alpha", Version: "1.0.0"}
}

type settingsHarness struct {
	t       *testing.T
	request profileRequest
	portal  *testPortal
	syncer  *profileSyncer
	root    string
	release int
	updates []progressUpdate
}

func newSettingsHarness(t *testing.T) *settingsHarness {
	t.Helper()
	portal := newTestPortal(t, nil, map[string][]byte{})
	t.Cleanup(portal.Close)
	request := profileRequest{World: "world", Profile: "alpha", ClientType: clientFlat, Portal: portal.request.Portal}
	harness := &settingsHarness{t: t, request: request, portal: portal}
	harness.syncer = newProfileSyncer(portal.httpClient)
	harness.syncer.LocalAppData = t.TempDir()
	harness.syncer.Progress = func(update progressUpdate) { harness.updates = append(harness.updates, update) }
	root, err := profileRoot(harness.syncer.LocalAppData, request)
	if err != nil {
		t.Fatal(err)
	}
	harness.root = root
	return harness
}

// publish installs one release carrying the given config body and baseline. A
// nil baseline is a release from before managed settings existed.
func (harness *settingsHarness) publish(body string, baseline *settingsBaseline) {
	harness.t.Helper()
	var extra []zipEntry
	if baseline != nil {
		data, err := json.Marshal(baseline)
		if err != nil {
			harness.t.Fatal(err)
		}
		extra = append(extra, zipEntry{Name: settingsBaselineFilename, Body: string(data)})
	}
	payload := testProfileArchive(harness.t, harness.request, nil, []zipEntry{{Name: "config/" + managedConfigName, Body: body}}, extra)
	harness.release++
	harness.portal.payload = payload
	harness.portal.manifest = testRemoteManifest(harness.request, fmt.Sprintf("release-%d", harness.release), payload)
	harness.updates = nil
	changed, err := harness.syncer.syncAuthorized(context.Background(), harness.request, "test-token-123456")
	if err != nil || !changed {
		harness.t.Fatalf("sync %d = changed:%t err:%v", harness.release, changed, err)
	}
}

func (harness *settingsHarness) livePath() string {
	return filepath.Join(harness.root, "active", "BepInEx", "config", managedConfigName)
}

// live is the file BepInEx actually loads, and the file the game rewrites when a
// player changes a setting in a mod's own menu.
func (harness *settingsHarness) live() string {
	harness.t.Helper()
	data, err := os.ReadFile(harness.livePath())
	if err != nil {
		harness.t.Fatal(err)
	}
	return string(data)
}

func (harness *settingsHarness) playerEdits(body string) {
	harness.t.Helper()
	if err := os.WriteFile(harness.livePath(), []byte(body), 0o600); err != nil {
		harness.t.Fatal(err)
	}
}

func (harness *settingsHarness) divergence() settingsDivergenceReport {
	harness.t.Helper()
	data, err := os.ReadFile(filepath.Join(harness.root, "active", settingsDivergenceFile))
	if err != nil {
		harness.t.Fatal(err)
	}
	var report settingsDivergenceReport
	if err := json.Unmarshal(data, &report); err != nil {
		harness.t.Fatal(err)
	}
	return report
}

func (harness *settingsHarness) storedBaseline() (settingsBaseline, bool) {
	harness.t.Helper()
	baseline, found, err := loadSettingsBaselineFile(filepath.Join(harness.root, "active", settingsBaselineFilename))
	if err != nil {
		harness.t.Fatal(err)
	}
	return baseline, found
}

// reportedLines is what the player reads in the activity log and pastes into a
// bug report, so a retained value has an answer that needs no headset.
func (harness *settingsHarness) reportedLines() []string {
	var lines []string
	for _, update := range harness.updates {
		lines = append(lines, update.LogLines...)
	}
	return lines
}

func requireDivergence(t *testing.T, report settingsDivergenceReport, key, reason, playerValue string) {
	t.Helper()
	for _, record := range report.Records {
		if record.Key != key {
			continue
		}
		if record.Reason != reason || record.PlayerValue != playerValue {
			t.Fatalf("divergence for %s = reason:%s player:%s, want reason:%s player:%s", key, record.Reason, record.PlayerValue, reason, playerValue)
		}
		return
	}
	t.Fatalf("no divergence recorded for %s in %+v", key, report.Records)
}

// Branch 1: server_forced. The admin has taken the setting away from the player,
// so their edit is deliberately replaced on the next sync.
func TestForcedSettingIsWrittenOverAPlayerEdit(t *testing.T) {
	harness := newSettingsHarness(t)
	harness.publish(managedConfigBody("RightGrip"), managedBaseline(policyServerForced, "RightGrip"))
	harness.playerEdits(managedConfigBody("LeftGrip"))

	harness.publish(managedConfigBody("RightGrip"), managedBaseline(policyServerForced, "RightGrip"))

	if live := harness.live(); live != managedConfigBody("RightGrip") {
		t.Fatalf("forced setting was not restored:\n%q", live)
	}
	if records := harness.divergence().Records; len(records) != 0 {
		t.Fatalf("a forced setting cannot diverge: %+v", records)
	}
}

// The same branch where it can actually be observed: the release's config file
// disagrees with the baseline. The merge, not the file copy, is what applies the
// forced value - a forced setting that silently does nothing is the failure this
// guards.
func TestMergeForcesValueWhenTheReleaseConfigIsStale(t *testing.T) {
	staged, player := t.TempDir(), t.TempDir()
	if err := os.WriteFile(filepath.Join(staged, managedConfigName), []byte(managedConfigBody("Stale")), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(player, managedConfigName), []byte(managedConfigBody("LeftGrip")), 0o600); err != nil {
		t.Fatal(err)
	}
	result, err := mergeManagedSettings(managedBaseline(policyServerForced, "RightGrip"), nil, player, staged)
	if err != nil {
		t.Fatal(err)
	}
	if result.Forced != 1 || result.Files != 1 {
		t.Fatalf("merge result = %+v", result)
	}
	data, err := os.ReadFile(filepath.Join(staged, managedConfigName))
	if err != nil {
		t.Fatal(err)
	}
	if string(data) != managedConfigBody("RightGrip") {
		t.Fatalf("forced value not written:\n%q", data)
	}
}

// Branch 2: client_default with the key absent from the player's file. This is
// the control for the first-install rule below - identical situation, no stored
// baseline, and the outcome differs only because the key was not there.
func TestClientDefaultIsSeededWhenThePlayerFileLacksTheKey(t *testing.T) {
	harness := newSettingsHarness(t)
	harness.publish(managedConfigWithoutKey, nil)
	if live := harness.live(); strings.Contains(live, "Modifier") {
		t.Fatalf("precondition failed, the key is already present:\n%q", live)
	}
	if _, found := harness.storedBaseline(); found {
		t.Fatal("precondition failed, a baseline was stored by a release that shipped none")
	}

	harness.publish(managedConfigBody("RightGrip"), managedBaseline(policyClientDefault, "RightGrip"))

	if live := harness.live(); live != managedConfigBody("RightGrip") {
		t.Fatalf("overridable setting was not seeded:\n%q", live)
	}
	if records := harness.divergence().Records; len(records) != 0 {
		t.Fatalf("seeding a missing key is not divergence: %+v", records)
	}
}

// Branch 3: client_default the player never touched. The value still equals what
// the portal last wrote, so an admin changing the default reaches them.
func TestClientDefaultFollowsTheAdminWhenThePlayerNeverTouchedIt(t *testing.T) {
	harness := newSettingsHarness(t)
	harness.publish(managedConfigBody("RightGrip"), managedBaseline(policyClientDefault, "RightGrip"))
	stored, found := harness.storedBaseline()
	if !found || len(stored.Entries) != 1 || stored.Entries[0].Written != "RightGrip" {
		t.Fatalf("stored baseline = %+v found:%t", stored, found)
	}

	harness.publish(managedConfigBody("LeftGrip"), managedBaseline(policyClientDefault, "LeftGrip"))

	if live := harness.live(); live != managedConfigBody("LeftGrip") {
		t.Fatalf("new default did not reach an untouched setting:\n%q", live)
	}
	if records := harness.divergence().Records; len(records) != 0 {
		t.Fatalf("an untouched setting cannot diverge: %+v", records)
	}
}

// Branch 4: the whole point of the feature. The player changed an overridable
// setting, so the admin's new default must not reach it - and the stored
// baseline is what makes "the player changed it" distinguishable from "the admin
// changed it", which look identical without it.
func TestClientDefaultKeepsThePlayerEditWhenTheAdminChangesTheDefault(t *testing.T) {
	harness := newSettingsHarness(t)
	harness.publish(managedConfigBody("RightGrip"), managedBaseline(policyClientDefault, "RightGrip"))
	harness.playerEdits(managedConfigBody("None"))

	harness.publish(managedConfigBody("LeftGrip"), managedBaseline(policyClientDefault, "LeftGrip"))

	if live := harness.live(); live != managedConfigBody("None") {
		t.Fatalf("the player's own value was destroyed:\n%q", live)
	}
	requireDivergence(t, harness.divergence(), "Modifier", divergenceReasonPlayerEdit, "None")
	lines := strings.Join(harness.reportedLines(), "\n")
	if !strings.Contains(lines, "Modifier") || !strings.Contains(lines, "None") {
		t.Fatalf("the installer did not report the kept value:\n%s", lines)
	}

	// And it stays kept. The stored baseline holds the server's value, never the
	// player's: storing theirs would compare equal next time and the merge would
	// overwrite the edit it had just protected.
	harness.publish(managedConfigBody("RightGrip"), managedBaseline(policyClientDefault, "RightGrip"))
	if live := harness.live(); live != managedConfigBody("None") {
		t.Fatalf("the player's value was destroyed on the following sync:\n%q", live)
	}
}

// Branch 5: no stored baseline and the key is already in the player's file.
// Nothing can attribute that value to the portal, so it is treated as the
// player's. Every player already has config files, so seeding on first sight
// would destroy exactly what this feature promises to protect.
func TestFirstBaselineLeavesAnExistingPlayerValueAlone(t *testing.T) {
	harness := newSettingsHarness(t)
	harness.publish(managedConfigBody("RightGrip"), nil)
	harness.playerEdits(managedConfigBody("None"))
	if _, found := harness.storedBaseline(); found {
		t.Fatal("precondition failed, a baseline was stored before one was published")
	}

	harness.publish(managedConfigBody("LeftGrip"), managedBaseline(policyClientDefault, "LeftGrip"))

	if live := harness.live(); live != managedConfigBody("None") {
		t.Fatalf("a value present before the first baseline was overwritten:\n%q", live)
	}
	requireDivergence(t, harness.divergence(), "Modifier", divergenceReasonNoBaseline, "None")
}

// Branch 6: the admin stopped managing the key. The portal has relinquished it,
// so the value in the player's file is theirs and the release's copy of the
// config file must not reintroduce a value nobody is managing.
func TestRetiredSettingIsNoLongerForced(t *testing.T) {
	harness := newSettingsHarness(t)
	harness.publish(managedConfigBody("RightGrip"), managedBaseline(policyServerForced, "RightGrip"))
	harness.playerEdits(managedConfigBody("None"))

	harness.publish(managedConfigBody("RightGrip"), emptyBaseline())

	if live := harness.live(); live != managedConfigBody("None") {
		t.Fatalf("a setting the admin stopped managing was still forced:\n%q", live)
	}
	requireDivergence(t, harness.divergence(), "Modifier", divergenceReasonUnmanaged, "None")
	stored, found := harness.storedBaseline()
	if !found || len(stored.Entries) != 0 {
		t.Fatalf("stored baseline = %+v found:%t", stored, found)
	}
}

// The player and the admin arriving at the same value is not a divergence to
// report. The file is still not written from the server side, but there is no
// difference to explain, and a record here would bury the ones that answer a
// question.
func TestNoDivergenceIsRecordedWhenThePlayerAndAdminAgree(t *testing.T) {
	harness := newSettingsHarness(t)
	harness.publish(managedConfigBody("RightGrip"), managedBaseline(policyClientDefault, "RightGrip"))
	harness.playerEdits(managedConfigBody("None"))

	harness.publish(managedConfigBody("None"), managedBaseline(policyClientDefault, "None"))

	if live := harness.live(); live != managedConfigBody("None") {
		t.Fatalf("live config = \n%q", live)
	}
	if records := harness.divergence().Records; len(records) != 0 {
		t.Fatalf("recorded a divergence between equal values: %+v", records)
	}
}

// A retired key the player never had a value for leaves the release's own file
// untouched: there is nothing of theirs to protect, and inventing a line would
// be the portal writing a key it has stopped managing.
func TestRetiredSettingAbsentFromThePlayerFileIsLeftToTheRelease(t *testing.T) {
	staged, player := t.TempDir(), t.TempDir()
	if err := os.WriteFile(filepath.Join(staged, managedConfigName), []byte(managedConfigBody("RightGrip")), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(player, managedConfigName), []byte(managedConfigWithoutKey), 0o600); err != nil {
		t.Fatal(err)
	}
	result, err := mergeManagedSettings(emptyBaseline(), managedBaseline(policyServerForced, "RightGrip"), player, staged)
	if err != nil {
		t.Fatal(err)
	}
	if result.Released != 0 || result.Files != 0 || len(result.Records) != 0 {
		t.Fatalf("merge result = %+v", result)
	}
	data, err := os.ReadFile(filepath.Join(staged, managedConfigName))
	if err != nil {
		t.Fatal(err)
	}
	if string(data) != managedConfigBody("RightGrip") {
		t.Fatalf("the release's file was modified:\n%q", data)
	}
}

// A key newly added to the baseline has no recorded value either, so it gets the
// same protection as branch 5 rather than being seeded over.
func TestNewlyManagedSettingLeavesAnExistingPlayerValueAlone(t *testing.T) {
	harness := newSettingsHarness(t)
	harness.publish(managedConfigBody("RightGrip"), emptyBaseline())
	harness.playerEdits(managedConfigBody("None"))

	harness.publish(managedConfigBody("LeftGrip"), managedBaseline(policyClientDefault, "LeftGrip"))

	if live := harness.live(); live != managedConfigBody("None") {
		t.Fatalf("a newly managed key overwrote the player's value:\n%q", live)
	}
	requireDivergence(t, harness.divergence(), "Modifier", divergenceReasonNewlyManaged, "None")
}

// A release that ships no baseline manages nothing. It must still install, and
// it must not leave a stale baseline behind claiming otherwise.
func TestReleaseWithoutABaselineStoresNothing(t *testing.T) {
	harness := newSettingsHarness(t)
	harness.publish(managedConfigBody("RightGrip"), managedBaseline(policyClientDefault, "RightGrip"))
	if _, found := harness.storedBaseline(); !found {
		t.Fatal("precondition failed, no baseline was stored")
	}

	harness.publish(managedConfigBody("LeftGrip"), nil)

	if _, found := harness.storedBaseline(); found {
		t.Fatal("a release shipping no baseline left one behind")
	}
}

// Seeding through the merge rather than through the file copy: the release's
// config file does not carry the key at all, so the value has to be inserted
// into its own section. Every comment already in the file survives, because the
// comment block is the schema the portal's extractor reads back.
func TestClientDefaultIsInsertedWhenTheReleaseConfigLacksTheKey(t *testing.T) {
	staged, player := t.TempDir(), t.TempDir()
	if err := os.WriteFile(filepath.Join(staged, managedConfigName), []byte(managedConfigWithoutKey), 0o600); err != nil {
		t.Fatal(err)
	}
	result, err := mergeManagedSettings(managedBaseline(policyClientDefault, "RightGrip"), nil, player, staged)
	if err != nil {
		t.Fatal(err)
	}
	if result.Seeded != 1 || result.Files != 1 || len(result.Records) != 0 {
		t.Fatalf("merge result = %+v", result)
	}
	data, err := os.ReadFile(filepath.Join(staged, managedConfigName))
	if err != nil {
		t.Fatal(err)
	}
	want := managedConfigWithoutKey + "\n[11 - Hover actions]\nModifier = RightGrip\n"
	if string(data) != want {
		t.Fatalf("seeded file =\n%q\nwant\n%q", data, want)
	}
}

func TestBaselineNamingAnUnshippedConfigFileIsReported(t *testing.T) {
	staged, player := t.TempDir(), t.TempDir()
	result, err := mergeManagedSettings(managedBaseline(policyServerForced, "RightGrip"), nil, player, staged)
	if err != nil {
		t.Fatal(err)
	}
	if len(result.Records) != 1 || result.Records[0].Reason != divergenceReasonMissingFile {
		t.Fatalf("merge result = %+v", result)
	}
	if entries, err := os.ReadDir(staged); err != nil || len(entries) != 0 {
		t.Fatalf("the merge invented a config file: %v %v", entries, err)
	}
}

func TestBaselineRejectsUnsafeAndUnknownEntries(t *testing.T) {
	for name, entry := range map[string]settingsBaselineEntry{
		"traversal":     {File: "../escape.cfg", Section: "A", Key: "K", Policy: policyServerForced},
		"absolute":      {File: "/etc/passwd", Section: "A", Key: "K", Policy: policyServerForced},
		"unknownPolicy": {File: "a.cfg", Section: "A", Key: "K", Policy: "advisory"},
		"multilineValue": {File: "a.cfg", Section: "A", Key: "K", Policy: policyServerForced,
			Written: "one\nInjected = two"},
		"bracketSection": {File: "a.cfg", Section: "A]\n[B", Key: "K", Policy: policyServerForced},
	} {
		baseline := settingsBaseline{Schema: settingsBaselineSchema, Entries: []settingsBaselineEntry{entry}}
		data, err := json.Marshal(baseline)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := loadSettingsBaselineBytes(data); err == nil {
			t.Fatalf("%s: accepted %+v", name, entry)
		}
	}
	valid, err := json.Marshal(managedBaseline(policyClientDefault, "RightGrip"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := loadSettingsBaselineBytes(valid); err != nil {
		t.Fatalf("rejected a valid baseline: %v", err)
	}
}
