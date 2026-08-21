package app

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strconv"
	"strings"
	"time"
)

// The per-world setting authority: which BepInEx settings this server decides, what value it
// decides, and whether a player may override it.
//
// There is deliberately no cached rendered payload here, unlike the mod catalog next door. The
// catalog is derived on the host from 100 plugin manifests and costs a script run, so caching it
// with a freshness fingerprint earns its keep. This table is the operator's own typed input: a few
// hundred rows at most, edited one row at a time through a form, read once per page view by primary
// key prefix. A cache would add an invalidation path with nothing to gain, and a stale one would
// show an admin a value the server is not actually publishing.

const (
	// configSchemaVersion is C1, the schema document the host extractor emits.
	configSchemaVersion = "world-config-schema/v1"
	// configAuthorityVersion is what the publish path consumes.
	configAuthorityVersion = "world-config-authority/v1"
)

// ConfigPolicy is the two-way choice an admin makes per setting. A key with NO record is a third
// state - unmanaged - and is represented by the absence of a map entry, never by a policy value.
// Collapsing that to a boolean would make "the mod's own default applies, we write nothing" look
// identical to "we write the default and let the player change it", which are different files on
// disk and different behaviour after a sync.
type ConfigPolicy string

const (
	// PolicyServerForced means the profile carries the value and the client sync always writes it.
	PolicyServerForced ConfigPolicy = "server_forced"
	// PolicyClientDefault means the profile carries the value as a starting point only. The sync
	// seeds it once and thereafter leaves a player's own edit alone.
	PolicyClientDefault ConfigPolicy = "client_default"
)

// ConfigSettingRef identifies one setting the way a .cfg file does: which file, which section
// (raw name, no brackets), which key.
type ConfigSettingRef struct {
	File    string `json:"file"`
	Section string `json:"section"`
	Key     string `json:"key"`
}

// ConfigSetting is one stored decision. Value is the exact string that would be written into the
// .cfg - it is never normalised, because a keybind chord ("LeftShift + PageUp") and a flags enum
// ("Swamp, Mountains") both carry spacing that the game reads back literally.
type ConfigSetting struct {
	ConfigSettingRef
	Value  string       `json:"value"`
	Policy ConfigPolicy `json:"policy"`
	Actor  string       `json:"actor,omitempty"`
	SetAt  time.Time    `json:"set_at,omitempty"`
}

// ConfigAuthority is every decision recorded for one world. A missing key is unmanaged; callers
// must treat the second return of Setting as the third state and not seed placeholders.
type ConfigAuthority map[ConfigSettingRef]ConfigSetting

// Setting answers whether this world decides that key at all, and with what.
func (a ConfigAuthority) Setting(file, section, key string) (ConfigSetting, bool) {
	setting, ok := a[ConfigSettingRef{File: file, Section: section, Key: key}]
	return setting, ok
}

// Sorted orders the records the way a .cfg tree reads: by file, then section, then key. Stable
// ordering is what makes two exports diffable.
func (a ConfigAuthority) Sorted() []ConfigSetting {
	out := make([]ConfigSetting, 0, len(a))
	for _, setting := range a {
		out = append(out, setting)
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].File != out[j].File {
			return out[i].File < out[j].File
		}
		if out[i].Section != out[j].Section {
			return out[i].Section < out[j].Section
		}
		return out[i].Key < out[j].Key
	})
	return out
}

// ConfigSchema is C1 as decoded. It is produced on the host by reading each .cfg file's own comment
// block, which is the only machine-readable description of what a setting will accept.
type ConfigSchema struct {
	Schema      string             `json:"schema"`
	World       string             `json:"world"`
	GeneratedAt string             `json:"generated_at"`
	Files       []ConfigSchemaFile `json:"files"`
}

type ConfigSchemaFile struct {
	File          string                `json:"file"`
	ModIdentifier string                `json:"mod_identifier,omitempty"`
	ModName       string                `json:"mod_name,omitempty"`
	Sections      []ConfigSchemaSection `json:"sections"`
}

// ConfigSchemaSection carries the section-level annotations. BepInEx writes them on the section, so
// they apply to every key inside it.
type ConfigSchemaSection struct {
	Name      string              `json:"name"`
	Synced    bool                `json:"synced"`
	Immutable bool                `json:"immutable"`
	Entries   []ConfigSchemaEntry `json:"entries"`
}

// ConfigSchemaEntry is one setting's declared type and allowed values. Fields the file did not
// state are absent rather than guessed, so an empty Default means "the file did not say".
type ConfigSchemaEntry struct {
	Key         string           `json:"key"`
	Type        string           `json:"type"`
	Default     string           `json:"default,omitempty"`
	Current     string           `json:"current,omitempty"`
	Description string           `json:"description,omitempty"`
	Acceptable  ConfigAcceptable `json:"acceptable"`
	Synced      bool             `json:"synced"`
	Immutable   bool             `json:"immutable"`
	Advanced    bool             `json:"advanced"`
}

// ConfigAcceptable is exactly one of a list of values, a numeric range, or nothing declared.
//
// Multiple mirrors BepInEx's flags-enum marker line, "# Multiple values can be set at the same time
// by separating them with ,". Without it a comma-separated value cannot be told apart from
// nonsense: "Swamp, Mountains" is legal for a BackpackBiomes flags enum and "Off, On" is not legal
// for a Toggle, and both are commas between listed names.
type ConfigAcceptable struct {
	Kind     string      `json:"kind"`
	Values   []string    `json:"values,omitempty"`
	Multiple bool        `json:"multiple,omitempty"`
	Min      ConfigBound `json:"min,omitempty"`
	Max      ConfigBound `json:"max,omitempty"`
}

// ConfigBound is one end of a declared range. Set distinguishes an absent bound from a bound of
// zero - "From -0.05 to 0" is a real range in these files, so a zero max is not a missing max.
// Text keeps the raw token so an integer bound renders as "2" and a float keeps its precision.
type ConfigBound struct {
	Text   string
	Number float64
	Set    bool
}

// UnmarshalJSON accepts the bound as a JSON string, which is what the extractor emits to keep the
// raw token, or as a JSON number, so a future emitter that writes numbers does not break decoding.
func (b *ConfigBound) UnmarshalJSON(data []byte) error {
	trimmed := strings.TrimSpace(string(data))
	if trimmed == "null" || trimmed == `""` {
		*b = ConfigBound{}
		return nil
	}
	text := trimmed
	if strings.HasPrefix(trimmed, `"`) {
		var quoted string
		if err := json.Unmarshal(data, &quoted); err != nil {
			return err
		}
		text = strings.TrimSpace(quoted)
	}
	if text == "" {
		*b = ConfigBound{}
		return nil
	}
	number, err := strconv.ParseFloat(text, 64)
	if err != nil {
		return fmt.Errorf("configuration schema bound %q is not a number", text)
	}
	*b = ConfigBound{Text: text, Number: number, Set: true}
	return nil
}

// MarshalJSON writes the bound back as the raw token, so a decode/encode round trip does not turn
// "2" into 2.000000.
func (b ConfigBound) MarshalJSON() ([]byte, error) {
	if !b.Set {
		return []byte("null"), nil
	}
	return json.Marshal(b.Text)
}

// Entry finds one setting in the schema and folds the section's annotations into it. Callers must
// never OR the section flags themselves: a key inside a section marked synced is server-authoritative
// even when its own line says nothing, and forgetting that is exactly how the UI would offer a
// client override that cannot hold at runtime.
func (s ConfigSchema) Entry(file, section, key string) (ConfigSchemaEntry, bool) {
	for _, f := range s.Files {
		if f.File != file {
			continue
		}
		for _, sec := range f.Sections {
			if sec.Name != section {
				continue
			}
			for _, entry := range sec.Entries {
				if entry.Key != key {
					continue
				}
				entry.Synced = entry.Synced || sec.Synced
				entry.Immutable = entry.Immutable || sec.Immutable
				return entry, true
			}
		}
	}
	return ConfigSchemaEntry{}, false
}

// integerConfigTypes and floatConfigTypes are BepInEx's own type tokens for numbers. Every other
// token seen on this host - Toggle, DamageModifier, CraftingTable, Trader, Color, Vector2 - is a
// custom converter whose legal values are only knowable from the acceptable-values line, so the
// type name alone is never used to reject those.
var integerConfigTypes = map[string]bool{
	"Byte": true, "SByte": true, "Int16": true, "UInt16": true,
	"Int32": true, "UInt32": true, "Int64": true, "UInt64": true,
}

var floatConfigTypes = map[string]bool{"Single": true, "Double": true, "Decimal": true}

// ValidateConfigSetting refuses anything the game or the runtime would refuse. The messages are UI
// copy: the page shows them beside the widget, so they name the allowed set rather than describing
// a Go failure.
//
// It is exported so the page can pre-check without a write, but the store calls it too. UI-only
// enforcement would leave the refusals a form POST away from being bypassed.
func ValidateConfigSetting(entry ConfigSchemaEntry, value string, policy ConfigPolicy) error {
	switch policy {
	case PolicyServerForced, PolicyClientDefault:
	default:
		return errors.New("choose whether the server forces this value or players may override it")
	}
	// C5: an [Immutable] section is fixed for the lifetime of the process. Writing it would publish
	// a value the game ignores.
	if entry.Immutable {
		return errors.New("this setting is in an immutable section and cannot be changed")
	}
	// C4: a synced key is overwritten in memory by the server at runtime whatever the client's file
	// says. Measured on this host: a client file held the correct wrist keybind, the server pushed
	// its own, the file was never rewritten, and the change appeared to do nothing. Offering a
	// client override here would be a promise the runtime breaks.
	if entry.Synced && policy == PolicyClientDefault {
		return errors.New("this setting is synced with the server, so the server always wins at runtime and players cannot be allowed to override it")
	}
	if err := validConfigValueShape(value); err != nil {
		return err
	}
	switch entry.Acceptable.Kind {
	case "list":
		return validateAgainstList(entry, value)
	case "range":
		return validateAgainstRange(entry, value)
	}
	// Nothing declared. A number still has to be a number, and a Boolean is written by BepInEx as
	// exactly "true" or "false" - 1153 Boolean records on this host, 781 true and 372 false, no
	// other spelling - so anything else would not parse back.
	return validateByType(entry, value)
}

// validConfigValueShape rejects values that would not survive the .cfg round trip. BepInEx writes
// "key = value" on one line and trims the surrounding spaces when reading it back, so a value with
// a newline or an edge space is not the value that would come out.
func validConfigValueShape(value string) error {
	if strings.ContainsAny(value, "\r\n\x00") {
		return errors.New("a value cannot span lines")
	}
	if value != strings.TrimSpace(value) {
		return errors.New("a value cannot begin or end with a space")
	}
	if len(value) > 4000 {
		return errors.New("a value cannot be longer than 4000 characters")
	}
	return nil
}

func validateAgainstList(entry ConfigSchemaEntry, value string) error {
	allowed := entry.Acceptable.Values
	if len(allowed) == 0 {
		return validateByType(entry, value)
	}
	// A keybind is a chord: BepInEx writes "LeftShift + PageUp", and when it declares acceptable
	// values they are the individual key names, never the chords. Each part is checked separately
	// and the value is stored with its original spacing.
	if entry.Type == "KeyboardShortcut" {
		for _, part := range strings.Split(value, "+") {
			if !listContains(allowed, strings.TrimSpace(part)) {
				return fmt.Errorf("use key names joined with +, each one of: %s", strings.Join(allowed, ", "))
			}
		}
		return nil
	}
	if listContains(allowed, value) {
		return nil
	}
	if entry.Acceptable.Multiple {
		parts := strings.Split(value, ",")
		for _, part := range parts {
			if !listContains(allowed, strings.TrimSpace(part)) {
				return fmt.Errorf("choose one or more of: %s, separated by commas", strings.Join(allowed, ", "))
			}
		}
		return nil
	}
	return fmt.Errorf("choose one of: %s", strings.Join(allowed, ", "))
}

func listContains(allowed []string, value string) bool {
	if value == "" {
		return false
	}
	for _, candidate := range allowed {
		if candidate == value {
			return true
		}
	}
	return false
}

func validateAgainstRange(entry ConfigSchemaEntry, value string) error {
	whole := integerConfigTypes[entry.Type]
	number, err := parseConfigNumber(value, whole)
	if err != nil {
		return errors.New(rangeMessage(entry, whole))
	}
	min, max := entry.Acceptable.Min, entry.Acceptable.Max
	if min.Set && number < min.Number {
		return errors.New(rangeMessage(entry, whole))
	}
	if max.Set && number > max.Number {
		return errors.New(rangeMessage(entry, whole))
	}
	return nil
}

// rangeMessage names the bounds using their raw tokens, so a range of 0 to 100 does not read as
// "0.000000 and 100.000000" in front of an operator.
func rangeMessage(entry ConfigSchemaEntry, whole bool) string {
	noun := "a number"
	if whole {
		noun = "a whole number"
	}
	min, max := entry.Acceptable.Min, entry.Acceptable.Max
	switch {
	case min.Set && max.Set:
		return fmt.Sprintf("enter %s between %s and %s", noun, min.Text, max.Text)
	case min.Set:
		return fmt.Sprintf("enter %s of at least %s", noun, min.Text)
	case max.Set:
		return fmt.Sprintf("enter %s of at most %s", noun, max.Text)
	default:
		return fmt.Sprintf("enter %s", noun)
	}
}

func validateByType(entry ConfigSchemaEntry, value string) error {
	switch {
	case entry.Type == "Boolean":
		if value != "true" && value != "false" {
			return errors.New("enter true or false")
		}
	case integerConfigTypes[entry.Type]:
		if _, err := parseConfigNumber(value, true); err != nil {
			return errors.New("enter a whole number")
		}
	case floatConfigTypes[entry.Type]:
		if _, err := parseConfigNumber(value, false); err != nil {
			return errors.New("enter a number")
		}
	case value == "":
		// Everything else - String, StringList, Color, Vector2, a mod's own converter - has no
		// declared shape, so the only thing that can be said is that a blank is not a decision.
		return errors.New("enter a value")
	}
	return nil
}

func parseConfigNumber(value string, whole bool) (float64, error) {
	if whole {
		parsed, err := strconv.ParseInt(value, 10, 64)
		return float64(parsed), err
	}
	return strconv.ParseFloat(value, 64)
}

// WorldConfigAuthority reads every decision recorded for one world. An empty map is a world whose
// settings are all unmanaged, which is a normal state and not a failure.
func (s *Store) WorldConfigAuthority(ctx context.Context, world string) (ConfigAuthority, error) {
	if !validWorld(world) {
		return nil, errors.New("invalid world")
	}
	rows, err := s.db.QueryContext(ctx, `SELECT file, section, "key", value, policy, actor, set_at
FROM world_config_settings WHERE world=?`, world)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	authority := ConfigAuthority{}
	for rows.Next() {
		var setting ConfigSetting
		var policy, setAt string
		if err := rows.Scan(&setting.File, &setting.Section, &setting.Key, &setting.Value, &policy, &setting.Actor, &setAt); err != nil {
			return nil, err
		}
		setting.Policy = ConfigPolicy(policy)
		setting.SetAt, _ = time.Parse(time.RFC3339Nano, setAt)
		authority[setting.ConfigSettingRef] = setting
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	return authority, nil
}

// SetWorldConfigSetting records one decision, refusing anything the runtime or the game would not
// honour. It takes the world's decoded schema rather than a pre-resolved entry so that the refusals
// live here: a caller cannot skip them by looking the entry up carelessly, and a key that is not in
// the schema at all cannot be validated and is therefore not stored.
func (s *Store) SetWorldConfigSetting(ctx context.Context, world string, schema ConfigSchema, want ConfigSetting, actor string) error {
	if !validWorld(world) {
		return errors.New("invalid world")
	}
	if strings.TrimSpace(actor) == "" || len(actor) > 200 {
		return errors.New("invalid actor")
	}
	if err := validConfigRef(want.ConfigSettingRef); err != nil {
		return err
	}
	// A schema carrying another world's name is a wiring mistake, and validating one world's value
	// against another world's mods would let a value through that this server refuses.
	if schema.World != "" && schema.World != world {
		return errors.New("this configuration schema belongs to another world")
	}
	entry, ok := schema.Entry(want.File, want.Section, want.Key)
	if !ok {
		return errors.New("this setting is not in the world's configuration schema")
	}
	if err := ValidateConfigSetting(entry, want.Value, want.Policy); err != nil {
		return err
	}
	now := time.Now().UTC().Format(time.RFC3339Nano)
	if _, err := s.db.ExecContext(ctx, `
INSERT INTO world_config_settings(world, file, section, "key", value, policy, actor, set_at)
VALUES(?,?,?,?,?,?,?,?)
ON CONFLICT(world, file, section, "key") DO UPDATE SET
 value=excluded.value, policy=excluded.policy, actor=excluded.actor, set_at=excluded.set_at`,
		world, want.File, want.Section, want.Key, want.Value, string(want.Policy), actor, now); err != nil {
		return err
	}
	return s.Audit(ctx, actor, "world.config.set", configAuditTarget(world, want.ConfigSettingRef),
		fmt.Sprintf("policy=%s; value=%s", want.Policy, want.Value))
}

// ClearWorldConfigSetting returns one key to UNMANAGED by deleting the row. It must never write the
// default as a value instead: an unmanaged key means the portal writes nothing and the mod's own
// default applies, which is a different file on disk from a key seeded with that same default.
func (s *Store) ClearWorldConfigSetting(ctx context.Context, world string, ref ConfigSettingRef, actor string) error {
	if !validWorld(world) {
		return errors.New("invalid world")
	}
	if strings.TrimSpace(actor) == "" || len(actor) > 200 {
		return errors.New("invalid actor")
	}
	if err := validConfigRef(ref); err != nil {
		return err
	}
	result, err := s.db.ExecContext(ctx, `DELETE FROM world_config_settings
WHERE world=? AND file=? AND section=? AND "key"=?`, world, ref.File, ref.Section, ref.Key)
	if err != nil {
		return err
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return err
	}
	// Clearing a key that was already unmanaged is the state the caller asked for, so it is not an
	// error. It is not audited either, because nothing changed.
	if affected == 0 {
		return nil
	}
	return s.Audit(ctx, actor, "world.config.clear", configAuditTarget(world, ref), "returned to the mod's own default")
}

func configAuditTarget(world string, ref ConfigSettingRef) string {
	return world + "/" + ref.File + "/" + ref.Section + "/" + ref.Key
}

// validConfigRef keeps a reference to something a .cfg file could actually name. The bounds are
// generous because section names on this host are free text ("11 - Hover actions") and keys carry
// spaces ("Limit recipes to station with nocost mode").
func validConfigRef(ref ConfigSettingRef) error {
	for _, part := range []string{ref.File, ref.Section, ref.Key} {
		if strings.TrimSpace(part) == "" || len(part) > 400 {
			return errors.New("invalid setting reference")
		}
		if strings.ContainsAny(part, "\r\n\x00") {
			return errors.New("invalid setting reference")
		}
	}
	if strings.Contains(ref.File, `\`) || strings.HasPrefix(ref.File, "/") || strings.Contains(ref.File, "..") {
		return errors.New("invalid configuration file name")
	}
	return nil
}

// configAuthorityExport is the document the profile builder consumes at publish time. It is a file
// rather than a query because the builder is a standalone binary run by republish-profiles.sh with
// no store connection.
type configAuthorityExport struct {
	Schema      string                 `json:"schema"`
	World       string                 `json:"world"`
	GeneratedAt string                 `json:"generated_at"`
	Entries     []configAuthorityEntry `json:"entries"`
}

type configAuthorityEntry struct {
	File    string       `json:"file"`
	Section string       `json:"section"`
	Key     string       `json:"key"`
	Value   string       `json:"value"`
	Policy  ConfigPolicy `json:"policy"`
}

// ExportWorldConfigAuthority renders the world's decisions as world-config-authority/v1. A world
// with no records yields a valid document with an empty entry list, never null and never an error,
// so pointing the builder at an unmanaged world is a well-formed no-op instead of a parse failure.
func (s *Store) ExportWorldConfigAuthority(ctx context.Context, world string) ([]byte, error) {
	authority, err := s.WorldConfigAuthority(ctx, world)
	if err != nil {
		return nil, err
	}
	export := configAuthorityExport{
		Schema:      configAuthorityVersion,
		World:       world,
		GeneratedAt: time.Now().UTC().Format(time.RFC3339),
		Entries:     []configAuthorityEntry{},
	}
	for _, setting := range authority.Sorted() {
		export.Entries = append(export.Entries, configAuthorityEntry{
			File:    setting.File,
			Section: setting.Section,
			Key:     setting.Key,
			Value:   setting.Value,
			Policy:  setting.Policy,
		})
	}
	return json.Marshal(export)
}

// SaveWorldConfigSchema caches the extracted schema of one world. Unlike the authority rows this IS
// derived and disposable - the host rebuilds it whenever the installed configs move - so it is
// keyed by world alone and replaced wholesale, exactly like world_mod_catalogs.
func (s *Store) SaveWorldConfigSchema(ctx context.Context, world, fingerprint string, payload []byte) error {
	if !validWorld(world) || len(fingerprint) != 64 {
		return errors.New("invalid configuration schema")
	}
	if len(payload) == 0 {
		return errors.New("empty configuration schema payload")
	}
	_, err := s.db.ExecContext(ctx, `
INSERT INTO world_config_schemas(world, fingerprint, payload, built_at) VALUES(?,?,?,?)
ON CONFLICT(world) DO UPDATE SET fingerprint=excluded.fingerprint, payload=excluded.payload, built_at=excluded.built_at`,
		world, fingerprint, payload, time.Now().UTC().Format(time.RFC3339Nano))
	return err
}

// WorldConfigSchemaPayload returns the cached schema and the fingerprint it was built for. No row
// is an empty answer rather than a failure: a world whose schema has never been extracted is a
// state the page renders.
func (s *Store) WorldConfigSchemaPayload(ctx context.Context, world string) (string, []byte, error) {
	if !validWorld(world) {
		return "", nil, errors.New("invalid world")
	}
	var fingerprint string
	var payload []byte
	err := s.db.QueryRowContext(ctx, `SELECT fingerprint, payload FROM world_config_schemas WHERE world=?`, world).Scan(&fingerprint, &payload)
	if errors.Is(err, sql.ErrNoRows) {
		return "", nil, nil
	}
	if err != nil {
		return "", nil, err
	}
	return fingerprint, payload, nil
}
