package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

// Managed settings: a three-way merge so an admin can publish a starting value
// without destroying the value a player chose.
//
// The problem this solves is structural, not cosmetic. Every generation is built
// from scratch and the release's config files are copied over the player's
// (sync.go: copyDirectory into next/BepInEx/config), so before this the shipped
// value always won for any file the release ships. preserveUnmanagedConfig only
// rescues whole files the release has no opinion about; it cannot rescue one key
// inside a file the release does ship.
//
// A merge that compares only the player's current value against the current
// server value cannot tell "the player changed it" from "the admin changed the
// default" - both look like a difference. So the value the portal last wrote is
// recorded on disk, in the profile's active directory, and the comparison is
// against that. Without the recorded copy the first time an admin edits a
// default would silently wipe every player's customisation of that setting.
const (
	settingsBaselineSchema = "settings-baseline/v1"
	// Where the baseline arrives, and where the last-applied copy is kept, are two
	// different paths. It ships inside the config payload because an older client
	// refuses an unrecognised top-level archive member and then cannot launch the
	// game at all; it is stored beside the generation because the stored copy has
	// to ride the same rename as the configs it describes.
	settingsBaselineArchivePath = "config/settings-baseline.json"
	settingsBaselineFilename    = "settings-baseline.json"
	settingsDivergenceSchema    = "settings-divergence/v1"
	settingsDivergenceFile      = "settings-divergence.json"

	// server_forced: the portal's value is written on every sync.
	// client_default: the portal's value is a starting value only.
	// A key the portal does not manage appears in no baseline at all. Absence is
	// a third state and must never be read as client_default.
	policyServerForced  = "server_forced"
	policyClientDefault = "client_default"

	// Sized against the largest world, not the one that is convenient to test on.
	// Measured: Vangard 174 config files and 28,781 keys, Doggerland and Storgard
	// 28,777, Hrafnheim only 17,429. A settings-baseline/v1 entry is roughly 130
	// bytes of JSON, so a fully managed world is about 3.7 MiB, and one mod -
	// southsil.SouthsilArmor.cfg, 3,361 keys - is about 440 KiB on its own. A cap
	// that fits Hrafnheim would refuse a legitimate archive at sync time, in front
	// of a player.
	maxSettingsBaselineBytes = int64(16 << 20)
	maxManagedConfigBytes    = int64(16 << 20)
)

type settingsBaselineEntry struct {
	File    string `json:"file"`
	Section string `json:"section"`
	Key     string `json:"key"`
	Policy  string `json:"policy"`
	Written string `json:"written"`
}

// settingsUnshippedEntry is a managed setting the publish path deliberately did
// NOT write, because the profile ships no config file for it. Around 95 of the
// 113 schema files belong to server-side plugins, so writing their values into a
// player's BepInEx/config would put them where they have no effect while never
// putting them where they would - the wrist-keybind failure again, a change that
// looks like it landed. The list is informational: the installer never acts on
// it, and it exists so nothing is silently dropped between the page and the file.
type settingsUnshippedEntry struct {
	File    string `json:"file"`
	Section string `json:"section"`
	Key     string `json:"key"`
	Policy  string `json:"policy"`
	// Value, not Written: nothing was written, which is the point of the list.
	Value  string `json:"value"`
	Reason string `json:"reason"`
}

type settingsBaseline struct {
	Schema  string                  `json:"schema"`
	World   string                  `json:"world"`
	Profile string                  `json:"profile"`
	Version string                  `json:"version"`
	Entries []settingsBaselineEntry `json:"entries"`
	// Entries holds only keys the profile really wrote. Unshipped holds the ones
	// it refused to write and why, and the merge acts on neither more nor less
	// than Entries. The field is decoded rather than ignored because the baseline
	// is parsed with DisallowUnknownFields - deliberately, so a malformed
	// document is refused whole rather than half applied - which means every
	// field the publish path may emit has to exist here.
	Unshipped []settingsUnshippedEntry `json:"unshipped,omitempty"`
	// raw is the document exactly as published. It is what gets stored as the
	// last-applied copy, so a field this build does not understand survives to
	// the build that does.
	raw []byte
}

// settingsDivergenceRecord answers "why does this player have a different
// value" without a headset or a support call.
type settingsDivergenceRecord struct {
	File        string `json:"file"`
	Section     string `json:"section"`
	Key         string `json:"key"`
	Policy      string `json:"policy"`
	ServerValue string `json:"server_value"`
	PlayerValue string `json:"player_value"`
	Reason      string `json:"reason"`
}

type settingsDivergenceReport struct {
	Schema     string                     `json:"schema"`
	World      string                     `json:"world"`
	Profile    string                     `json:"profile"`
	Version    string                     `json:"version"`
	RecordedAt string                     `json:"recorded_at"`
	Records    []settingsDivergenceRecord `json:"records"`
}

const (
	// The player edited a setting the admin left overridable. Their value stands.
	divergenceReasonPlayerEdit = "player_edit"
	// No last-applied baseline was on disk, so a value already in the player's
	// file cannot be attributed to the portal. Left alone: seeding over an
	// existing file on first sight would destroy exactly the settings this
	// feature exists to protect, and every player already has files.
	divergenceReasonNoBaseline = "no_recorded_baseline"
	// The key became managed while the player already had a value for it. Same
	// reasoning as no_recorded_baseline, one key at a time.
	divergenceReasonNewlyManaged = "newly_managed"
	// The admin stopped managing the key. The portal has relinquished it, so the
	// player's own value is what stays in the file.
	divergenceReasonUnmanaged = "no_longer_managed"
	// The baseline names a key in a config file the release does not ship. The
	// portal cannot write a value into a file that is not there; reported so the
	// publish side is visible rather than silently ineffective.
	divergenceReasonMissingFile = "config_file_not_shipped"
	// The named file is not shaped like a BepInEx config - a mod's own data file
	// that merely ends in .cfg - so the key cannot be added to it without
	// corrupting the file. Reported and skipped, never fatal: one bad entry must
	// not stop a player launching the game.
	divergenceReasonNotAConfig = "not_a_configuration_file"
)

type settingsMergeResult struct {
	Forced   int
	Seeded   int
	Updated  int
	Diverged int
	Released int
	Files    int
	Records  []settingsDivergenceRecord
}

// Detail is the line a player reads in the activity log and pastes into a bug
// report.
func (result settingsMergeResult) Detail() string {
	return fmt.Sprintf("Managed settings: %d forced by the server, %d started at the server value, %d updated, %d left as you set them, %d no longer managed.",
		result.Forced, result.Seeded, result.Updated, result.Diverged, result.Released)
}

func loadSettingsBaselineBytes(data []byte) (settingsBaseline, error) {
	var baseline settingsBaseline
	decoder := json.NewDecoder(bytes.NewReader(trimUTF8BOM(data)))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&baseline); err != nil {
		return settingsBaseline{}, fmt.Errorf("decode settings baseline: %w", err)
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return settingsBaseline{}, errors.New("settings baseline has trailing JSON")
	}
	if baseline.Schema != settingsBaselineSchema {
		return settingsBaseline{}, fmt.Errorf("unsupported settings baseline schema %q", baseline.Schema)
	}
	for _, entry := range baseline.Entries {
		if err := validateSettingsBaselineEntry(entry); err != nil {
			return settingsBaseline{}, err
		}
	}
	baseline.raw = data
	return baseline, nil
}

// loadSettingsBaselineFile reads a stored baseline. A missing file is the
// first-install case and reports found=false rather than an error: that is a
// normal state, and it is the state that must leave the player's file alone.
func loadSettingsBaselineFile(path string) (settingsBaseline, bool, error) {
	data, err := readBoundedFile(path, maxSettingsBaselineBytes)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return settingsBaseline{}, false, nil
		}
		return settingsBaseline{}, false, err
	}
	baseline, err := loadSettingsBaselineBytes(data)
	if err != nil {
		return settingsBaseline{}, false, err
	}
	return baseline, true, nil
}

func validateSettingsBaselineEntry(entry settingsBaselineEntry) error {
	if _, err := archivePath(entry.File); err != nil {
		return fmt.Errorf("settings baseline names an unsafe config file %q", entry.File)
	}
	// An empty section is legitimate: BepInEx writes "[]" for an entry bound with
	// no section, and TastyChickenLegs.AutomaticFuel.cfg:4 is exactly that, with
	// IsOn at :9 under it. Rejecting the whole baseline over one such entry would
	// fail the sync for the world; refusing to manage those keys would make the
	// portal silently ineffective on them.
	if strings.ContainsAny(entry.Section, "[]\r\n\x00") {
		return fmt.Errorf("settings baseline has an invalid section %q", entry.Section)
	}
	if entry.Key == "" || strings.ContainsAny(entry.Key, "=\r\n\x00") || strings.TrimSpace(entry.Key) != entry.Key {
		return fmt.Errorf("settings baseline has an invalid key %q", entry.Key)
	}
	// A newline in a value would inject a second line into the .cfg, so this is
	// a safety check on network input, not a tidiness one.
	if strings.ContainsAny(entry.Written, "\r\n\x00") {
		return fmt.Errorf("settings baseline value for %s is not a single line", entry.Key)
	}
	switch entry.Policy {
	case policyServerForced, policyClientDefault:
		return nil
	default:
		return fmt.Errorf("settings baseline has an unknown policy %q", entry.Policy)
	}
}

func settingsEntryIdentity(entry settingsBaselineEntry) string {
	return entry.File + "\x00" + entry.Section + "\x00" + entry.Key
}

// mergeManagedSettings applies the published baseline to the staged config tree.
//
// stagedConfig holds what the release shipped, already copied into the new
// generation. playerConfig is the player's live tree from the active generation,
// which is the only record of what they changed in game. applied is the baseline
// this profile last wrote, or nil on a first install.
func mergeManagedSettings(published *settingsBaseline, applied *settingsBaseline, playerConfig, stagedConfig string) (settingsMergeResult, error) {
	var result settingsMergeResult
	if published == nil && applied == nil {
		return result, nil
	}
	appliedByIdentity := map[string]settingsBaselineEntry{}
	if applied != nil {
		for _, entry := range applied.Entries {
			appliedByIdentity[settingsEntryIdentity(entry)] = entry
		}
	}
	publishedByIdentity := map[string]settingsBaselineEntry{}
	byFile := map[string][]settingsBaselineEntry{}
	if published != nil {
		for _, entry := range published.Entries {
			publishedByIdentity[settingsEntryIdentity(entry)] = entry
			byFile[entry.File] = append(byFile[entry.File], entry)
		}
	}
	// A key dropped from the baseline still needs a decision, and its file may
	// carry no published entry at all, so the retired keys join the walk.
	retiredByFile := map[string][]settingsBaselineEntry{}
	for identity, entry := range appliedByIdentity {
		if _, still := publishedByIdentity[identity]; still {
			continue
		}
		retiredByFile[entry.File] = append(retiredByFile[entry.File], entry)
	}
	files := make([]string, 0, len(byFile)+len(retiredByFile))
	for file := range byFile {
		files = append(files, file)
	}
	for file := range retiredByFile {
		if _, seen := byFile[file]; !seen {
			files = append(files, file)
		}
	}
	sort.Strings(files)

	for _, file := range files {
		stagedPath, err := archiveDestination(stagedConfig, file)
		if err != nil {
			return settingsMergeResult{}, err
		}
		playerPath, err := archiveDestination(playerConfig, file)
		if err != nil {
			return settingsMergeResult{}, err
		}
		staged, stagedFound, err := readConfigDocument(stagedPath)
		if err != nil {
			return settingsMergeResult{}, err
		}
		if !stagedFound {
			for _, entry := range byFile[file] {
				result.Records = append(result.Records, settingsDivergenceRecord{
					File: entry.File, Section: entry.Section, Key: entry.Key,
					Policy: entry.Policy, ServerValue: entry.Written,
					Reason: divergenceReasonMissingFile,
				})
			}
			continue
		}
		player, playerFound, err := readConfigDocument(playerPath)
		if err != nil {
			return settingsMergeResult{}, err
		}
		changed := false
		decide := func(entry settingsBaselineEntry, retired bool) error {
			playerValue, playerHas := "", false
			if playerFound {
				playerValue, playerHas = player.value(entry.Section, entry.Key)
			}
			// record notes an entry the merge acted on or declined to act on.
			// serverValue is what the file would have held had the merge not
			// intervened, and a divergence is only worth recording when it differs
			// from the player's: an identical pair explains nothing and would bury
			// the records that answer a question.
			record := func(reason, serverValue string) {
				result.Records = append(result.Records, settingsDivergenceRecord{
					File: entry.File, Section: entry.Section, Key: entry.Key,
					Policy: entry.Policy, ServerValue: serverValue, PlayerValue: playerValue,
					Reason: reason,
				})
			}
			apply := func(value string) error {
				wrote, err := staged.setValue(entry.Section, entry.Key, value)
				if err != nil {
					// The file is not shaped like a config at all, so the key
					// cannot be added to it. One bad entry must not stop a player
					// launching the game, so it is reported and skipped.
					if errors.Is(err, errConfigNotAppendable) {
						record(divergenceReasonNotAConfig, value)
						return nil
					}
					return err
				}
				changed = changed || wrote
				return nil
			}
			keep := func(reason, serverValue string) error {
				if serverValue != playerValue {
					record(reason, serverValue)
				}
				return apply(playerValue)
			}
			write := func() error {
				return apply(entry.Written)
			}
			if retired {
				// The portal no longer writes this key. Whatever the player's file
				// holds is theirs now; the release's copy of the file must not
				// reintroduce a value the admin has stopped managing.
				if !playerHas {
					return nil
				}
				result.Released++
				current, _ := staged.value(entry.Section, entry.Key)
				if current == playerValue {
					return nil
				}
				// The shipped value, not the retired baseline's: what the release
				// would have written is the thing that did not happen.
				return keep(divergenceReasonUnmanaged, current)
			}
			if entry.Policy == policyServerForced {
				result.Forced++
				return write()
			}
			switch {
			case !playerHas:
				result.Seeded++
				return write()
			case applied == nil:
				result.Diverged++
				return keep(divergenceReasonNoBaseline, entry.Written)
			default:
				last, managed := appliedByIdentity[settingsEntryIdentity(entry)]
				if !managed {
					result.Diverged++
					return keep(divergenceReasonNewlyManaged, entry.Written)
				}
				if playerValue == last.Written {
					result.Updated++
					return write()
				}
				result.Diverged++
				return keep(divergenceReasonPlayerEdit, entry.Written)
			}
		}
		for _, entry := range byFile[file] {
			if err := decide(entry, false); err != nil {
				return settingsMergeResult{}, fmt.Errorf("merge %s: %w", file, err)
			}
		}
		for _, entry := range retiredByFile[file] {
			if err := decide(entry, true); err != nil {
				return settingsMergeResult{}, fmt.Errorf("merge %s: %w", file, err)
			}
		}
		if !changed {
			continue
		}
		if err := staged.write(stagedPath); err != nil {
			return settingsMergeResult{}, err
		}
		result.Files++
	}
	sort.Slice(result.Records, func(left, right int) bool {
		first, second := result.Records[left], result.Records[right]
		if first.File != second.File {
			return first.File < second.File
		}
		if first.Section != second.Section {
			return first.Section < second.Section
		}
		return first.Key < second.Key
	})
	return result, nil
}

// storeAppliedSettingsBaseline records the baseline verbatim inside the staged
// generation, which activation renames into place as the active directory. It
// lands beside the profile rather than inside config/ so it cannot alter the
// shipped-config hash, and it rides the same rename as the files it describes -
// a rollback therefore restores the baseline that matches the restored configs.
//
// The stored copy holds the SERVER's value, never a player's. A stored player
// value would compare equal on the next sync and the merge would then overwrite
// the very edit it had just protected.
func storeAppliedSettingsBaseline(generation string, published *settingsBaseline) error {
	if published == nil {
		// A release that ships no baseline manages nothing, so there is nothing
		// to remember. The next baseline-carrying sync sees no stored copy and
		// leaves every value the player already has, which is the safe direction.
		return nil
	}
	return writeFileAtomically(filepath.Join(generation, settingsBaselineFilename), published.raw)
}

func writeSettingsDivergenceReport(generation string, published *settingsBaseline, records []settingsDivergenceRecord) error {
	report := settingsDivergenceReport{
		Schema:     settingsDivergenceSchema,
		RecordedAt: time.Now().UTC().Format(time.RFC3339),
		Records:    records,
	}
	if published != nil {
		report.World, report.Profile, report.Version = published.World, published.Profile, published.Version
	}
	if report.Records == nil {
		report.Records = []settingsDivergenceRecord{}
	}
	return writeJSONAtomically(filepath.Join(generation, settingsDivergenceFile), report)
}

// settingsDivergenceLines names each retained value so the installer's own
// output answers the operator's question. Bounded, because a profile with
// hundreds of overridable settings would otherwise bury everything else.
func settingsDivergenceLines(records []settingsDivergenceRecord) []string {
	const limit = 20
	lines := make([]string, 0, len(records)+1)
	for i, record := range records {
		if i == limit {
			lines = append(lines, fmt.Sprintf("... and %d more kept settings; see %s.", len(records)-limit, settingsDivergenceFile))
			break
		}
		switch record.Reason {
		case divergenceReasonMissingFile:
			lines = append(lines, fmt.Sprintf("Setting %s / [%s] %s cannot be applied: the release does not ship that config file.", record.File, record.Section, record.Key))
		default:
			lines = append(lines, fmt.Sprintf("Kept your %s / [%s] %s = %s (server suggests %s, reason %s).",
				record.File, record.Section, record.Key, record.PlayerValue, record.ServerValue, record.Reason))
		}
	}
	return lines
}
