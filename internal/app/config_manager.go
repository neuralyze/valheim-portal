package app

import (
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"net/url"
	"sort"
	"strconv"
	"strings"
)

// The per-world settings configuration manager: every setting a mod declares, grouped by mod, drawn
// with the widget its declared type and allowed values actually justify, and carrying the C2 policy
// control that says whether the server forces the value or a player may change it.
//
// Two measurements on the live corpus decided the shape of this page rather than taste. First the
// scale, across all four worlds on this host rather than the smallest: 17,429 keys in 108 files for
// Hrafnheim, then 28,777, 28,777 and 28,781 in 173 to 174 files for Doggerland, Storgard and
// Vangard. One file (southsil.SouthsilArmor.cfg) holds 3361 keys over 121 sections. A page that
// draws every widget is megabytes and nothing an operator would browse, so the page OPENS on what
// the portal manages - the question actually being asked is "what am I controlling on this server,
// and what did I set it to" - and reaches the rest by search and by mod, then section. Second, the
// "Setting type" token is not a fixed vocabulary: 102 distinct tokens appear, 415 keys carry none at
// all, and most of the rest are mod-defined enums. Widgets are chosen from the SHAPE of the
// acceptable values first and from the type token only where the shape says nothing, because
// switching on the token alone would fall through on thousands of keys.

// The three views. Managed is the default because it answers the operator's own question; the other
// two exist because the managed set cannot contain a setting nobody has touched yet.
const (
	configViewManaged = "managed"
	configViewMod     = "mod"
	configViewSearch  = "search"
)

// configEntryRenderCap is the hard ceiling on widgets in one view. Navigation keeps a view well
// under it - a mod lists its sections, and the largest section in the corpus is 28 keys - so this is
// the backstop for a mod that ships one enormous section, not the mechanism.
const configEntryRenderCap = 400

// configSearchCap bounds a search across the whole corpus. A two-letter query matches thousands of
// keys; the page draws this many and says how many it left out rather than truncating in silence.
const configSearchCap = 200

// The widget kinds the template draws. These are the whole mapping: nothing else may appear in
// configManagerSetting.Widget.
const (
	// widgetReadOnly is an immutable setting (C5), drawn genuinely disabled with the reason in text.
	// BepInEx's own ConfigurationManager gets this wrong - SettingEntryBase.Set is a silent no-op
	// when ReadOnly is set and nothing in its drawing path reads the flag - so a locked setting
	// looks live and snaps back when touched, with no explanation. This does not copy that.
	widgetReadOnly = "readonly"
	// widgetToggle is a Boolean.
	widgetToggle = "toggle"
	// widgetKey is a keybind: the current binding in a field, a capture button, and the declared
	// values as a searchable datalist.
	widgetKey = "key"
	// widgetSelect is one value out of a declared list.
	widgetSelect = "select"
	// widgetMultiSelect is a flags enum: any combination of the declared list.
	widgetMultiSelect = "multiselect"
	// widgetSlider is a declared numeric range with BOTH bounds, drawn as a slider over the real min
	// and max and paired with a number field so a precise value is still typable.
	widgetSlider = "slider"
	// widgetText is everything else, as a number input when the type token is numeric.
	widgetText = "text"
)

// configKeyTypes are the keybind tokens. They are the one place the type token outranks the
// acceptable-values shape, and deliberately so: AzuAutoStore's four shortcuts each declare 339
// acceptable values (the whole Unity KeyCode enum), and 62 keys corpus-wide declare 20 or more. A
// 339-option select is the mistake ConfigurationManager makes - it draws a selection grid over every
// item with no filter, which is why it also ships a separate press-a-key button as the real
// affordance. The chooser here is that button, with the declared list kept as a searchable fallback.
var configKeyTypes = map[string]bool{
	"KeyboardShortcut": true, "KeyboardShortcuts": true, "KeyCode": true,
}

// configLockKeys are the admin-lock key names, matched as a WHOLE key and case-insensitively, never
// as a substring. Measured across Hrafnheim's 108 files: "Lock Configuration" in 43 files, written
// both as On and as true, then one file each for "Lock Config", "LockConfig", "LockConfiguration",
// "LockAdminConfig", "Config Locked", "Config locked" and "Lock to Admin" - 50 files in all.
//
// The whole-key match is the point, and it is the lesson of a loose pattern earlier tonight. A
// substring match on "lock" pulls in LockGuiPositionWhenMenuOpen, which is a Boolean in exactly the
// same shape as a real lock and is not one; a looser one pulls in every "block_armor" and "Block
// Force" in the weapon files. Neither type nor shape separates them. Only the name does.
var configLockKeys = map[string]bool{
	"lock configuration": true,
	"lockconfiguration":  true,
	"lock config":        true,
	"lockconfig":         true,
	"lockadminconfig":    true,
	"config locked":      true,
	"lock to admin":      true,
}

// The three honest states of C2. A key with no record is UNMANAGED, and that is a real third state:
// the portal publishes nothing for it and the mod's own default applies. It must never be shown as
// though it had been seeded with the default, because a player sees the difference.
const (
	configStateUnmanaged = "unmanaged"
	configStateForced    = "forced"
	configStateSeeded    = "seeded"
)

// Where a setting's value can actually be delivered, which is not the same question as who decides
// it. The schema is read from the world's own config_merged/bepinex - 113 files on Hrafnheim, 179 on
// Vangard - but a published client profile ships only the handful of files under
// profiles/<name>/client-config, so for most settings on this page the value's real destination is
// the SERVER and there is no server-side write path yet.
//
// This is a separate axis from C2 on purpose. C2 says whether the server forces a value or a player
// may change it; scope says whether the portal can put the value anywhere at all. Presenting a
// server-scoped edit as live would be the exact failure that started this feature: a change that
// looks applied, is not, and gives no sign of it.
// The `source` tokens the extractor emits: which tree a file was read from. "both" is a real case
// rather than a convenience - org.bepinex.plugins.valheimvrmod.cfg is in the world's own tree with
// 145 keys AND in a profile client tree with 26, and neither of the other two tokens describes that.
const (
	configSourceMerged = "config_merged"
	configSourceClient = "client_profile"
	configSourceBoth   = "both"
)

const (
	configScopeClient  = "client"
	configScopeServer  = "server"
	configScopeUnknown = "unknown"
)

type configManagerPage struct {
	World PublicWorld
	CSRF  string
	// View is one of the three constants above.
	View string
	// Query is the search term. It spans the whole corpus, because mod-then-section navigation
	// cannot find "BlockingType" without already knowing it lives in ValheimVRMod's Motion Control.
	Query string
	// File and Section are the mod file and section being drilled into, empty when not narrowed.
	File    string
	Section string
	ModName string
	// Unavailable means the schema could not be read at all, from the host or from the cache.
	Unavailable bool
	// Verified says the schema was checked against the host's fingerprint on this view. A cached
	// schema served without that check is still worth editing against; claiming it was verified
	// would not be.
	Verified    bool
	GeneratedAt string
	// Mods is the index of config files, built in every view so the way in is always present.
	Mods []configManagerMod
	// Sections is the section index of a mod too large to draw whole.
	Sections []configManagerSectionLink
	// Groups are the drawn widgets, grouped by the section they belong to.
	Groups []configManagerGroup
	// Orphans are managed keys the schema no longer declares. The portal still publishes them, so
	// hiding them would misstate what it controls.
	Orphans []configManagerOrphan
	// Lock is the selected mod's admin lock, when it declares one. Shown at the top of a mod view
	// because it governs every synced setting underneath it.
	Lock    configLock
	HasLock bool
	// Rendered and Withheld are stated on the page in every view. A view that quietly dropped
	// settings would look identical to a mod that does not declare them.
	Rendered int
	Withheld int
	Total    int
	Forced   int
	Seeded   int
	// Applied and Pending split the managed set by scope. Two things both called "managed", one in
	// force and one waiting on a delivery path that does not exist, is the worst version of this
	// page, so the split is stated rather than left to be inferred per row.
	Applied int
	Pending int
	// ScopeKnown is false when the shipped-file set could not be determined. Then no row claims a
	// scope: an unknown scope is stated as unknown, never defaulted to client.
	ScopeKnown bool
	// Notice is the message from a refused save, shown against the widget at NoticeRef.
	Notice    string
	NoticeRef string
}

// Managed is how many keys the portal controls in this world, forced and seeded together.
func (p configManagerPage) Managed() int { return p.Forced + p.Seeded }

type configManagerMod struct {
	File    string
	ModName string
	Entries int
	Forced  int
	Seeded  int
	// Selected marks the mod being viewed, so the picker shows where you are.
	Selected bool
	Link     string
}

type configManagerSectionLink struct {
	Name      string
	Entries   int
	Forced    int
	Seeded    int
	Immutable bool
	Synced    bool
	Link      string
}

type configManagerGroup struct {
	File      string
	ModName   string
	Section   string
	Immutable bool
	// Link reaches this section on its own, which is the only way into a mod whose sections are
	// drawn as an index.
	Link    string
	Entries []configManagerSetting
}

// configManagerOrphan is a managed key that has left the schema - a mod removed, renamed, or
// upgraded past the setting. The record still publishes, so the page names it and offers the only
// honest action: stop managing it.
type configManagerOrphan struct {
	File, Section, Key string
	Value              string
	Policy             string
	State              string
	StateKind          string
}

// configLock is a mod's own admin-lock setting. Naming it is what turns C4's refusal from a dead end
// into something an operator can act on: a synced key is server-authoritative, and where a lock is
// the cause, the lock is itself a setting. The page points at it rather than only refusing.
//
// Changing the lock stays a separate, deliberate act on the lock itself. Its blast radius is every
// synced setting in that mod and every player on the server, so it must never be a side effect of
// toggling one setting's policy - and the presence of a lock never softens the C4 refusal.
type configLock struct {
	File, Section, Key string
	// Value is the lock's effective value: what the portal publishes for it, else what the world's
	// own file holds, else the mod's default.
	Value string
	// On and Known are separate because a lock whose value is neither on/true nor off/false must not
	// be reported as off. Locks are written both ways in this corpus.
	On    bool
	Known bool
	Link  string
}

type configManagerOption struct {
	Value    string
	Selected bool
	// Unlisted is a value already held that the mod does not declare. It is kept as an option
	// deliberately, the way r2modman keeps it rather than the way Gale coerces to the first entry:
	// without it, opening the page and pressing save rewrote a hand-edited or newer-mod value.
	Unlisted bool
}

type configManagerSetting struct {
	File, Section, Key string
	ModName            string
	// ID is a per-view DOM id. It only has to be unique inside one page, so the render index is
	// enough and no hashing of a key full of spaces and non-ASCII is needed.
	ID          string
	Type        string
	Description string
	Default     string
	// Current is what the world's own config file holds now, which is not necessarily what the
	// portal manages.
	Current string
	// Value is what the widget shows: the managed value where there is one, else the mod's default.
	Value  string
	Widget string
	// Options is the declared list for a select or a multi-select, and the datalist for a keybind.
	Options []configManagerOption
	// Min, Max and Step are raw tokens, so an integer bound stays "2" and a float bound keeps the
	// precision the mod wrote rather than becoming 2.000000.
	Min, Max, Step string
	Numeric        bool
	Pattern        string
	PatternHint    string
	Checked        bool
	Managed        bool
	Policy         string
	State          string
	StateKind      string
	// Synced, ClientSide and neither are three states, not a boolean. Synced is the positive
	// [Synced with Server] annotation - 990 of 19,866 entries on Hrafnheim, in 43 of its 113 files -
	// so C4 is a hard constraint on a minority and the page must not be shaped as though override
	// were usually impossible. ClientSide is the explicit [Not Synced with Server] annotation, 237
	// entries, the mod author declaring the setting client-side. Neither means the file said nothing.
	Synced     bool
	ClientSide bool
	Immutable  bool
	Advanced   bool
	// OverrideAllowed is false for a synced key. C4: the server overwrites such a key in memory at
	// runtime whatever the client's file says, so offering a player override would be a promise the
	// runtime breaks. The page states the reason rather than hiding the control unexplained.
	OverrideAllowed bool
	Reason          string
	// Lock and HasLock name the mod's admin lock where the mod declares one, so a synced key says
	// what is causing it and where to act, and says plainly when there is no lock to go hunting for.
	Lock    configLock
	HasLock bool
	// IsLock marks the mod's admin lock itself, so its own row explains what it governs instead of
	// claiming the mod has no lock.
	IsLock bool
	// Scope is one of the configScope constants: where a value for this setting can actually be
	// delivered. ScopeReason carries the sentence the page shows, because a scope word on its own
	// tells an operator nothing about what to expect.
	Scope       string
	ScopeReason string
	// Deliverable is false when the portal has no path to put this value where the game would read
	// it. The edit is still recorded - the intent is worth keeping - but the row must never look as
	// though it were in force.
	Deliverable bool
	// Where names the mod and section a search hit lives in, since a hit arrives with no context.
	Where string
	// Search is the lowercased haystack the filter-as-you-type box matches against.
	Search string
	// Notice is the validation message when this is the setting a save was refused for.
	Notice string
}

// settingsManager renders the page. Beyond the schema it needs only the world's authority record, so
// a view costs one host fingerprint check, one cached read and one small query.
func (s *Server) settingsManager(w http.ResponseWriter, r *http.Request) {
	world := r.PathValue("world")
	if !validWorld(world) {
		http.NotFound(w, r)
		return
	}
	info, err := s.store.PublicWorld(r.Context(), world)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	s.renderSettingsManager(w, r, info, "", configSettingRef{})
}

// renderSettingsManager is shared by the GET and by a POST whose value was refused, so a rejected
// save shows its message against the widget it belongs to instead of replacing the page with an
// error the operator has to navigate back from.
func (s *Server) renderSettingsManager(w http.ResponseWriter, r *http.Request, info PublicWorld, notice string, against configSettingRef) {
	world := info.Name
	page := configManagerPage{
		World:   info,
		CSRF:    s.csrfCookie(w, r),
		Query:   strings.TrimSpace(r.FormValue("q")),
		File:    strings.TrimSpace(r.FormValue("file")),
		Section: r.FormValue("section"),
		Notice:  notice,
	}
	schema, verified, ok := s.worldConfigSchema(r.Context(), world)
	page.Verified = verified
	if !ok {
		page.Unavailable = true
		render(w, configManagerTemplate, page)
		return
	}
	authority, err := s.store.WorldConfigAuthority(r.Context(), world)
	if err != nil {
		// Without the authority record the page cannot tell forced from seeded from unmanaged, and
		// those three states are the whole feature. Drawing widgets with the states guessed would be
		// worse than saying the page is unavailable.
		slog.Error("cannot read the world config authority", "world", world, "error", err)
		page.Unavailable = true
		render(w, configManagerTemplate, page)
		return
	}
	page.GeneratedAt = schema.GeneratedAt
	buildConfigManagerPage(&page, schema, authority, notice, against)
	render(w, configManagerTemplate, page)
}

// buildConfigManagerPage is the whole view decision, kept clear of the request and the store so the
// rendering can be tested against a schema directly.
func buildConfigManagerPage(page *configManagerPage, schema ConfigSchema, authority ConfigAuthority, notice string, against configSettingRef) {
	page.Mods = make([]configManagerMod, 0, len(schema.Files))
	scopes := make(map[string]configFileScope, len(schema.Files))
	for _, file := range schema.Files {
		scope := configFileScopeOf(file)
		scopes[file.File] = scope
		// One file carrying scope information is enough to say the page has it: a schema is emitted
		// whole, so a mixture would mean a payload half-written, not a partially knowable world.
		if scope.Scope != configScopeUnknown {
			page.ScopeKnown = true
		}
	}
	locks := make(map[string]configLock, len(schema.Files))
	selected := -1
	for index, file := range schema.Files {
		mod := configManagerMod{
			File: file.File, ModName: configModLabel(file), Selected: file.File == page.File,
			Link: configManagerLink(page.World.Name, file.File, "", ""),
		}
		if lock, found := configFileLock(page.World.Name, file, authority); found {
			locks[file.File] = lock
		}
		for _, section := range file.Sections {
			mod.Entries += len(section.Entries)
			for _, entry := range section.Entries {
				switch setting, managed := authority.Setting(file.File, section.Name, entry.Key); {
				case !managed:
				case setting.Policy == PolicyClientDefault:
					mod.Seeded++
				default:
					mod.Forced++
				}
			}
		}
		// Applied against pending, so the managed set never reads as uniformly in force. A
		// server-scoped value is recorded and waiting on a write path that does not exist yet.
		if scopes[file.File].Deliverable {
			page.Applied += mod.Forced + mod.Seeded
		} else {
			page.Pending += mod.Forced + mod.Seeded
		}
		page.Total += mod.Entries
		page.Forced += mod.Forced
		page.Seeded += mod.Seeded
		if mod.Selected {
			selected = index
		}
		page.Mods = append(page.Mods, mod)
	}
	sort.SliceStable(page.Mods, func(a, b int) bool {
		return lowerASCII(page.Mods[a].ModName) < lowerASCII(page.Mods[b].ModName)
	})
	switch {
	case page.Query != "":
		page.View = configViewSearch
		buildConfigManagerSearch(page, schema, authority, locks, scopes, notice, against)
	case selected >= 0:
		page.View = configViewMod
		page.Lock, page.HasLock = locks[page.File]
		buildConfigManagerMod(page, schema.Files[selected], authority, locks, scopes, notice, against)
	default:
		page.View = configViewManaged
		buildConfigManagerManaged(page, schema, authority, locks, scopes, notice, against)
	}
	page.NoticeRef = configNoticeRef(page, against)
}

// buildConfigManagerManaged draws the default view: every key the portal actually controls, in the
// order the store sorts them, grouped by mod and section. This is the answer to the operator's real
// question, and on a fresh world it is deliberately empty rather than accidentally so - the template
// says nothing is managed yet and offers the way in.
func buildConfigManagerManaged(page *configManagerPage, schema ConfigSchema, authority ConfigAuthority, locks map[string]configLock, scopes map[string]configFileScope, notice string, against configSettingRef) {
	labels := make(map[string]string, len(schema.Files))
	for _, file := range schema.Files {
		labels[file.File] = configModLabel(file)
	}
	var group configManagerGroup
	for _, setting := range authority.Sorted() {
		entry, known := schema.Entry(setting.File, setting.Section, setting.Key)
		if !known {
			// Still published, so still shown. A record the schema has lost is exactly the case an
			// operator needs told about, since nothing else on the page would mention it.
			state, kind := configSettingState(true, setting.Policy)
			page.Orphans = append(page.Orphans, configManagerOrphan{
				File: setting.File, Section: setting.Section, Key: setting.Key,
				Value: setting.Value, Policy: string(setting.Policy), State: state, StateKind: kind,
			})
			continue
		}
		if page.Rendered >= configEntryRenderCap {
			page.Withheld++
			continue
		}
		if group.File != setting.File || group.Section != setting.Section {
			if len(group.Entries) > 0 {
				page.Groups = append(page.Groups, group)
			}
			group = configManagerGroup{
				File: setting.File, ModName: labels[setting.File], Section: setting.Section,
				Immutable: entry.Immutable,
				Link:      configManagerLink(page.World.Name, setting.File, setting.Section, ""),
			}
		}
		group.Entries = append(group.Entries, configSettingView(
			setting.File, group.ModName, setting.Section, entry, setting, true,
			locks, scopes, page.Rendered, notice, against,
		))
		page.Rendered++
	}
	if len(group.Entries) > 0 {
		page.Groups = append(page.Groups, group)
	}
}

// buildConfigManagerMod draws one mod. Choosing a mod lists its SECTIONS; entries are drawn once a
// section is chosen. That third level is not a nicety at this scale: the four worlds on this host
// carry 17,429, 28,777, 28,777 and 28,781 keys across 108 to 174 files, one file
// (southsil.SouthsilArmor.cfg) holds 3361 keys over 121 sections, and a flat mod page is megabytes
// of widgets nobody reads. Gale reached the same answer from the same problem - its config editor
// renders section-name buttons and nothing else until one is picked.
//
// The single exception is a mod with exactly one section, where there is no choice to make and a
// click that has only one destination is pure friction, so its entries are drawn straight away.
func buildConfigManagerMod(page *configManagerPage, file ConfigSchemaFile, authority ConfigAuthority, locks map[string]configLock, scopes map[string]configFileScope, notice string, against configSettingRef) {
	page.ModName = configModLabel(file)
	if page.Section == "" && len(file.Sections) > 1 {
		for _, section := range file.Sections {
			link := configManagerSectionLink{
				Name: section.Name, Entries: len(section.Entries),
				Immutable: section.Immutable, Synced: section.Synced,
				Link: configManagerLink(page.World.Name, file.File, section.Name, ""),
			}
			for _, entry := range section.Entries {
				if entry.Synced {
					link.Synced = true
				}
				switch setting, managed := authority.Setting(file.File, section.Name, entry.Key); {
				case !managed:
				case setting.Policy == PolicyClientDefault:
					link.Seeded++
				default:
					link.Forced++
				}
			}
			page.Sections = append(page.Sections, link)
			page.Withheld += link.Entries
		}
		return
	}
	for _, section := range file.Sections {
		if page.Section != "" && section.Name != page.Section {
			page.Withheld += len(section.Entries)
			continue
		}
		group := configManagerGroup{
			File: file.File, ModName: page.ModName, Section: section.Name, Immutable: section.Immutable,
			Link: configManagerLink(page.World.Name, file.File, section.Name, ""),
		}
		for _, entry := range section.Entries {
			if page.Rendered >= configEntryRenderCap {
				page.Withheld++
				continue
			}
			setting, managed := authority.Setting(file.File, section.Name, entry.Key)
			group.Entries = append(group.Entries, configSettingView(
				file.File, page.ModName, section.Name, foldSection(section, entry), setting, managed,
				locks, scopes, page.Rendered, notice, against,
			))
			page.Rendered++
		}
		if len(group.Entries) > 0 {
			page.Groups = append(page.Groups, group)
		}
	}
}

// buildConfigManagerSearch answers a query by key, section, mod and description - the set of things
// an operator remembers a setting by. Unscoped it walks every key in the world, which is the one
// place the page is allowed to do that and the only way to reach a setting whose mod you do not
// already know.
//
// Scoped to one file it is also the only way to reach the tail of an enormous section. Measured:
// ItemStacksRewrite/...weights.cfg declares 1587 entries in a SINGLE section, [Item Weights], and
// ...stacks.cfg 847 in [Item Stacks] - 57 times the largest section in any top-level file - so
// mod-then-section navigation bottoms out there with more entries than any view will draw. They are
// per-item settings (Acorn_weight, Amber_weight) and search is the only thing that finds one.
func buildConfigManagerSearch(page *configManagerPage, schema ConfigSchema, authority ConfigAuthority, locks map[string]configLock, scopes map[string]configFileScope, notice string, against configSettingRef) {
	needle := lowerASCII(page.Query)
	for _, file := range schema.Files {
		if page.File != "" && file.File != page.File {
			continue
		}
		label := configModLabel(file)
		for _, section := range file.Sections {
			group := configManagerGroup{
				File: file.File, ModName: label, Section: section.Name, Immutable: section.Immutable,
				Link: configManagerLink(page.World.Name, file.File, section.Name, ""),
			}
			for _, entry := range section.Entries {
				if !configSettingMatches(label, section.Name, entry, needle) {
					continue
				}
				if page.Rendered >= configSearchCap {
					page.Withheld++
					continue
				}
				setting, managed := authority.Setting(file.File, section.Name, entry.Key)
				view := configSettingView(
					file.File, label, section.Name, foldSection(section, entry), setting, managed,
					locks, scopes, page.Rendered, notice, against,
				)
				view.Where = label + " · " + section.Name
				group.Entries = append(group.Entries, view)
				page.Rendered++
			}
			if len(group.Entries) > 0 {
				page.Groups = append(page.Groups, group)
			}
		}
	}
}

// configFileLock finds a mod's admin lock, if it declares one. The key name is matched whole and
// case-insensitively against configLockKeys; nothing about a key's type or its section is used,
// because a real lock and LockGuiPositionWhenMenuOpen are the same Boolean in the same shape.
func configFileLock(world string, file ConfigSchemaFile, authority ConfigAuthority) (configLock, bool) {
	for _, section := range file.Sections {
		for _, entry := range section.Entries {
			if !configLockKeys[lowerASCII(strings.TrimSpace(entry.Key))] {
				continue
			}
			lock := configLock{
				File: file.File, Section: section.Name, Key: entry.Key,
				Link: configManagerLink(world, file.File, section.Name, ""),
			}
			// The effective value, in the order the runtime would see it: what the portal publishes
			// for the lock, then what the world's own file holds, then the mod's default.
			switch setting, managed := authority.Setting(file.File, section.Name, entry.Key); {
			case managed:
				lock.Value = setting.Value
			case strings.TrimSpace(entry.Current) != "":
				lock.Value = entry.Current
			default:
				lock.Value = entry.Default
			}
			lock.On, lock.Known = configLockOn(lock.Value)
			return lock, true
		}
	}
	return configLock{}, false
}

// configLockOn reads a lock's value. Locks are written both ways in this corpus - "Lock Configuration
// = On" in most of the files and "= true" in the rest - and the second return exists so a value that
// is neither is reported as unknown rather than silently as off.
func configLockOn(value string) (bool, bool) {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "on", "true", "1", "yes":
		return true, true
	case "off", "false", "0", "no":
		return false, true
	}
	return false, false
}

// foldSection ORs the section's annotations onto an entry exactly the way ConfigSchema.Entry does for
// a single lookup, including the rule that a genuinely synced key LOSES the client-side hint: the
// server overrides it in memory whatever any annotation says. Iterating the schema directly bypasses
// that fold, and forgetting it is how the page would offer a client override that cannot hold.
func foldSection(section ConfigSchemaSection, entry ConfigSchemaEntry) ConfigSchemaEntry {
	entry.Synced = entry.Synced || section.Synced
	entry.Immutable = entry.Immutable || section.Immutable
	entry.ClientSide = (entry.ClientSide || section.ClientSide) && !entry.Synced
	return entry
}

// configSettingMatches is the server-side search predicate.
func configSettingMatches(modName, section string, entry ConfigSchemaEntry, needle string) bool {
	if needle == "" {
		return true
	}
	for _, field := range [4]string{modName, section, entry.Key, entry.Description} {
		if strings.Contains(lowerASCII(field), needle) {
			return true
		}
	}
	return false
}

// configNoticeRef finds the drawn id of the setting a refused save belongs to, so the message sits
// against the widget rather than alone at the top of the page.
func configNoticeRef(page *configManagerPage, against configSettingRef) string {
	if against.Key == "" {
		return ""
	}
	for _, group := range page.Groups {
		for _, entry := range group.Entries {
			if entry.File == against.File && entry.Section == against.Section && entry.Key == against.Key {
				return entry.ID
			}
		}
	}
	return ""
}

// configSettingState phrases one of the three C2 states. The wording of the unmanaged case matters:
// it says the mod's own default applies, not that the default was written, because those are
// different states and a player can tell them apart.
func configSettingState(managed bool, policy ConfigPolicy) (string, string) {
	switch {
	case !managed:
		return "Not managed - the mod's own default applies", configStateUnmanaged
	case policy == PolicyClientDefault:
		return "Seeded - players may change it, and the sync leaves their change alone", configStateSeeded
	default:
		return "Server-forced - every player gets this value", configStateForced
	}
}

// configSettingView is the widget mapping table. The order of the cases IS the contract:
//
//  1. immutable          -> read-only, drawn genuinely disabled with the reason (C5)
//  2. keybind type       -> key chooser, ahead of the list shape on purpose (see configKeyTypes)
//  3. list, multiple     -> multi-select over exactly the declared values
//  4. list               -> select over exactly the declared values
//  5. range, both bounds -> slider over the real min and max, paired with a number field
//  6. Boolean            -> toggle
//  7. anything else      -> text, as a number input when the type token is numeric
//
// Nothing here reads the key's NAME to choose a widget, and nothing re-derives whether a key is
// synced: entry.Synced arrives resolved by the extractor and folded from its section, and a
// contains-check on the description would sweep in every key ending "[Not Synced with Server]" -
// 224 of them, the exact opposite of hazardous - which is the lying-UI failure C4 exists to prevent.
//
// Nothing here validates either. ValidateConfigSetting is for the value an admin submits; running it
// over a schema's own current value on page load would light up every drifted or off-list value in
// the corpus as an error it is not.
func configSettingView(
	file, modName, section string,
	entry ConfigSchemaEntry,
	setting ConfigSetting,
	managed bool,
	locks map[string]configLock,
	scopes map[string]configFileScope,
	index int,
	notice string,
	against configSettingRef,
) configManagerSetting {
	view := configManagerSetting{
		File: file, Section: section, Key: entry.Key, ModName: modName,
		ID:      "setting-" + strconv.Itoa(index),
		Type:    entry.Type,
		Default: entry.Default, Description: entry.Description, Current: entry.Current,
		Synced: entry.Synced, ClientSide: entry.ClientSide,
		Immutable: entry.Immutable, Advanced: entry.Advanced,
		Managed: managed,
	}
	view.State, view.StateKind = configSettingState(managed, setting.Policy)
	if managed {
		view.Policy, view.Value = string(setting.Policy), setting.Value
	} else {
		view.Value = entry.Default
	}
	// C4. A synced key is overwritten by the server at runtime, in memory, however the client's file
	// reads, so client_default cannot hold for it and the page must not offer the choice - not even
	// when the mod's lock is off. Unlocking is a per-mod decision whose blast radius is every synced
	// setting in the mod and every player on the server, so it stays a deliberate act on the lock.
	view.OverrideAllowed = !entry.Synced && !entry.Immutable
	view.Lock, view.HasLock = locks[file]
	// A lock does not point at itself - but it must not then read as a mod with NO lock, which is a
	// different and false statement. Measured live: AzuAutoStore's own "Lock Configuration" row said
	// "this mod declares no admin lock" while being that lock.
	if view.HasLock && view.Lock.Key == entry.Key && view.Lock.Section == section {
		view.HasLock, view.IsLock = false, true
	}
	view.Reason = configOverrideReason(entry, view.Lock, view.HasLock, view.IsLock)
	if scope, known := scopes[file]; known {
		view.Scope, view.ScopeReason, view.Deliverable = scope.Scope, scope.Reason, scope.Deliverable
	} else {
		view.Scope = configScopeUnknown
	}
	if against.Key != "" && against.File == file && against.Section == section && against.Key == entry.Key {
		// Show the operator their own rejected input, not the stored value: correcting a typo is
		// impossible if the field silently reverts to what was already there.
		view.Notice, view.Value = notice, against.Value
		if against.Policy != "" {
			view.Policy = against.Policy
		}
	}
	configApplyWidget(&view, entry)
	view.Search = lowerASCII(strings.Join([]string{
		modName, section, entry.Key, entry.Type, entry.Description, view.StateKind,
	}, " "))
	return view
}

// configFileScope is one file's delivery facts, resolved once per file from the schema and shared by
// every setting in it. Scope is a property of the FILE - which tree it was read from and whether a
// published profile carries it - never of the individual key.
type configFileScope struct {
	Scope       string
	Reason      string
	Deliverable bool
}

// configFileScopeOf says where a value for one file could actually be delivered, and what to tell the
// operator. Both facts come from the schema: `source` is the tree the file was read from and
// `shipped` is whether a published profile carries it.
//
// This is read rather than derived on purpose. Deriving it from the portal's own authority records
// would only work for keys somebody had already managed, so two keys in the SAME file would report
// different scopes depending on whether anyone happened to have edited one - a scope that moves
// because of unrelated activity is worse than no scope at all.
//
// An empty Source means the cached payload predates these fields, which is a real state while a
// world still holds a schema extracted by the previous build. It is stated as unknown; defaulting to
// the friendlier answer would be the same class of lie C4 exists to prevent.
func configFileScopeOf(file ConfigSchemaFile) configFileScope {
	if strings.TrimSpace(file.Source) == "" {
		return configFileScope{
			Scope:  configScopeUnknown,
			Reason: "This world's cached configuration was read before the portal recorded where each file comes from, so this page cannot say whether a value set here would reach players. The record is kept either way; whether it is in force is not something this view can claim.",
		}
	}
	if file.Shipped {
		return configFileScope{
			Scope:       configScopeClient,
			Reason:      "A published profile carries this file, so a value set here reaches players on their next sync.",
			Deliverable: true,
		}
	}
	reason := "No published profile carries this file, so nothing the portal publishes can deliver it. A value set here is recorded and waits: the portal has no server-side write path yet, and until that and a world restart exist it will not be in force."
	if file.Source == configSourceMerged {
		reason = "This file was read from the world's own configuration, which the SERVER reads and no profile ships to players. A value set here is recorded and waits: the portal has no server-side write path yet, and until that and a world restart exist it will not be in force."
	}
	return configFileScope{Scope: configScopeServer, Reason: reason}
}

// configOverrideReason says why a setting offers no player-override choice, and where to act. The two
// synced cases are deliberately different sentences: a mod WITH a lock has a cause the operator can
// go and change, and a mod WITHOUT one has none, so saying so saves a hunt for a lock that is not
// there. A key that is not synced gets nothing: the choice is offered, so there is nothing to explain.
func configOverrideReason(entry ConfigSchemaEntry, lock configLock, hasLock, isLock bool) string {
	switch {
	case entry.Immutable:
		return "This is in a section the mod reads once when the game starts and then overrides from the command line, so a value set here would not take effect until the client restarted. The portal does not manage it: editing it on the client is the only route that behaves predictably."
	case !entry.Synced:
		return ""
	case isLock:
		return "This IS this mod's admin lock. It governs whether players' own changes to every synced setting in the mod are accepted at all, across every player on the server, which is why it is a deliberate decision here rather than a side effect of editing one setting. It is itself synced, so the server's value for it wins at runtime."
	case hasLock:
		state := "its value reads as neither on nor off"
		if lock.Known && lock.On {
			state = "currently on"
		} else if lock.Known {
			state = "currently off"
		}
		return "Synced with the server, and this mod has an admin lock: " + lock.Key + " under [" + lock.Section + "] in " + lock.File + ", " + state + ". Whether a player's own change to this mod's synced settings is accepted is that lock's decision, not this setting's, and it covers every synced setting in the mod and every player on the server. Change it there if that is what you mean to do."
	default:
		return "Synced with the server, and this mod declares no admin lock, so there is nothing to unlock. The server's value wins at runtime whatever a player's file says."
	}
}

// configApplyWidget picks the control and fills in everything it needs to draw itself.
func configApplyWidget(view *configManagerSetting, entry ConfigSchemaEntry) {
	integer := integerConfigTypes[entry.Type]
	view.Numeric = integer || floatConfigTypes[entry.Type]
	list := entry.Acceptable.Kind == "list" && len(entry.Acceptable.Values) > 0
	span := entry.Acceptable.Kind == "range" && entry.Acceptable.Min.Set && entry.Acceptable.Max.Set &&
		!configDegenerateRange(entry.Acceptable.Min, entry.Acceptable.Max, view.Value)
	if entry.Acceptable.Kind == "range" {
		// Whichever bounds the schema declared travel to the control verbatim, so an integer bound
		// is not reformatted and a float bound keeps the precision the mod wrote.
		view.Min, view.Max = entry.Acceptable.Min.Text, entry.Acceptable.Max.Text
	}
	switch {
	case view.Immutable:
		view.Widget = widgetReadOnly
	case configKeyTypes[entry.Type]:
		view.Widget = widgetKey
		view.Options = configOptions(entry.Acceptable.Values, view.Value, false)
	case list && entry.Acceptable.Multiple:
		view.Widget = widgetMultiSelect
		view.Options = configOptions(entry.Acceptable.Values, view.Value, true)
	case list:
		view.Widget = widgetSelect
		view.Options = configOptions(entry.Acceptable.Values, view.Value, false)
	case span:
		view.Widget = widgetSlider
		view.Step = configSliderStep(entry.Acceptable.Min, entry.Acceptable.Max, integer)
	case entry.Type == "Boolean":
		view.Widget = widgetToggle
		view.Checked = strings.EqualFold(strings.TrimSpace(view.Value), "true")
	default:
		view.Widget = widgetText
		configApplyTextValidation(view, integer)
	}
}

// configDegenerateRange says whether a declared range is a TYPE bound rather than a design range,
// which is when a slider over it is decorative and must not be drawn.
//
// The test is ConfigurationManager's own deadzone arithmetic - a slider cannot resolve better than
// about a thousandth of its span - applied to the value being edited. If the smallest change the
// slider could express is larger than the value itself, dragging it can only ever destroy that
// value, so the number field is the whole control.
//
// Measured, and this is 2,397 of the corpus's 4,985 ranges, 48%:
//
//	ItemStacksRewrite/...stacks.cfg    810 entries, "From 1 to 2147483647" (Int32.MaxValue),
//	                                  real values min 1, median 30, max 999; deadzone 2,147,484
//	ItemStacksRewrite/...weights.cfg  1587 entries, "From 0 to 2147484",
//	                                  real values min 0, median 1, max 918.96; deadzone 2,147
//
// In both files every value that actually occurs is below the slider's smallest expressible change.
// A sentinel list of magic maxima would catch these two and miss the next mod's; the deadzone is the
// property that actually makes the control useless, so that is what is tested.
func configDegenerateRange(min, max ConfigBound, value string) bool {
	span := max.Number - min.Number
	if span <= 0 {
		return true
	}
	// An unparsable or empty value is judged against 1, the smallest change anyone would care about,
	// so a range whose thousandth exceeds even that is degenerate whatever it currently holds.
	scale := 1.0
	if parsed, err := strconv.ParseFloat(strings.TrimSpace(value), 64); err == nil {
		if parsed < 0 {
			parsed = -parsed
		}
		if parsed > scale {
			scale = parsed
		}
	}
	return span/1000 > scale
}

// configApplyTextValidation is the client-side half of the validation the store enforces on write.
// Both halves have to exist: the browser one so a mistake is caught before a round trip, the store
// one because a form is not a trustworthy source of anything.
func configApplyTextValidation(view *configManagerSetting, integer bool) {
	switch {
	case integer:
		view.Step, view.PatternHint = "1", "enter a whole number"
	case view.Numeric:
		// Any fraction is legal, so nothing constrains the field's step; the store's own message for
		// this case is "enter a number".
		view.Step, view.PatternHint = "any", "enter a number"
	default:
		// The store refuses a value that begins or ends with a space, because a .cfg reads it back
		// trimmed and so would not return what was written. This is that rule stated to the browser.
		// An empty value is still allowed: HTML skips pattern on an empty field, and an empty setting
		// is legitimate.
		view.Pattern = `\S|\S.*\S`
		view.PatternHint = "a value cannot begin or end with a space"
	}
}

// configSliderStep gives a float range enough stops to be worth dragging. An integer range steps by
// one; a float range takes the largest round step that still leaves at least a hundred positions, so
// 0.5 to 2 steps by 0.01 rather than by a whole unit it could never land between. The paired number
// field carries no step at all, so any precise value the mod would accept is still typable.
func configSliderStep(min, max ConfigBound, integer bool) string {
	if integer {
		return "1"
	}
	span := max.Number - min.Number
	if span <= 0 {
		return "any"
	}
	for _, step := range [...]float64{1, 0.5, 0.25, 0.1, 0.05, 0.01, 0.005, 0.001, 0.0001} {
		if span/step >= 100 {
			return strconv.FormatFloat(step, 'f', -1, 64)
		}
	}
	return strconv.FormatFloat(span/100, 'f', -1, 64)
}

// configOptions builds a list widget's options: exactly the values the mod declared, in the order it
// declared them. A value already held that the mod does NOT declare is kept as well, flagged - the
// way r2modman keeps it rather than the way Gale coerces to the first entry. Without that, opening
// the page and pressing save silently rewrote a hand-edited or newer-mod value.
func configOptions(values []string, current string, multiple bool) []configManagerOption {
	chosen := map[string]bool{}
	if multiple {
		for _, part := range strings.Split(current, ",") {
			if part = strings.TrimSpace(part); part != "" {
				chosen[part] = true
			}
		}
	} else if trimmed := strings.TrimSpace(current); trimmed != "" {
		chosen[trimmed] = true
	}
	declared := make(map[string]struct{}, len(values))
	for _, value := range values {
		declared[value] = struct{}{}
	}
	unlisted := make([]string, 0, len(chosen))
	for value := range chosen {
		if _, known := declared[value]; !known {
			unlisted = append(unlisted, value)
		}
	}
	sort.Strings(unlisted)
	options := make([]configManagerOption, 0, len(values)+len(unlisted))
	for _, value := range unlisted {
		options = append(options, configManagerOption{Value: value, Selected: true, Unlisted: true})
	}
	for _, value := range values {
		options = append(options, configManagerOption{Value: value, Selected: chosen[value]})
	}
	return options
}

// configModLabel names a file by its mod where the extractor resolved one, and by the file itself
// where it did not. A blank heading would be worse than a filename.
func configModLabel(file ConfigSchemaFile) string {
	if name := strings.TrimSpace(file.ModName); name != "" {
		return name
	}
	if id := strings.TrimSpace(file.ModIdentifier); id != "" {
		return id
	}
	return file.File
}

// configManagerLink builds a view URL. Sections carry spaces and non-ASCII - "11 - Hover actions",
// "Ódr Skirt" - so every part is escaped.
func configManagerLink(world, file, section, query string) string {
	values := url.Values{}
	if file != "" {
		values.Set("file", file)
	}
	if section != "" {
		values.Set("section", section)
	}
	if query != "" {
		values.Set("q", query)
	}
	route := "/admin/worlds/" + url.PathEscape(world) + "/settings"
	if len(values) == 0 {
		return route
	}
	return route + "?" + values.Encode()
}

// configSettingRef is one setting plus what a POST carried for it, which is what a refused save has
// to echo back so the operator corrects their own input rather than the stored value.
type configSettingRef struct {
	File, Section, Key, Value, Policy string
}

// setWorldConfigSetting records one setting's value and its policy. Validation happens twice on
// purpose: here, so the message lands beside the widget the operator is looking at, and again in the
// store, because a form is not a trustworthy source and the store is the only thing that can refuse
// a write.
func (s *Server) setWorldConfigSetting(w http.ResponseWriter, r *http.Request) {
	world := r.PathValue("world")
	if !validWorld(world) {
		http.NotFound(w, r)
		return
	}
	info, err := s.store.PublicWorld(r.Context(), world)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	ref := configSettingRef{
		File:    strings.TrimSpace(r.FormValue("file")),
		Section: r.FormValue("section"),
		Key:     r.FormValue("key"),
		Value:   configPostedValue(r),
		Policy:  strings.TrimSpace(r.FormValue("policy")),
	}
	if ref.File == "" || ref.Key == "" {
		http.Error(w, "invalid setting", http.StatusBadRequest)
		return
	}
	schema, _, ok := s.worldConfigSchema(r.Context(), world)
	if !ok {
		http.Error(w, "the world's configuration schema is unavailable", http.StatusServiceUnavailable)
		return
	}
	entry, known := schema.Entry(ref.File, ref.Section, ref.Key)
	if !known {
		s.renderSettingsManager(w, r, info, "this setting is not in the world's configuration schema", ref)
		return
	}
	policy := ConfigPolicy(ref.Policy)
	if err := ValidateConfigSetting(entry, ref.Value, policy); err != nil {
		s.renderSettingsManager(w, r, info, err.Error(), ref)
		return
	}
	want := ConfigSetting{
		ConfigSettingRef: ConfigSettingRef{File: ref.File, Section: ref.Section, Key: ref.Key},
		Value:            ref.Value,
		Policy:           policy,
	}
	if err := s.store.SetWorldConfigSetting(r.Context(), world, schema, want, r.Header.Get("X-Portal-Actor")); err != nil {
		s.renderSettingsManager(w, r, info, err.Error(), ref)
		return
	}
	http.Redirect(w, r, configManagerReturn(r, world), http.StatusSeeOther)
}

// resetWorldConfigSetting returns a setting to UNMANAGED. It DELETES the record; it does not write
// the default as a value. Those are different states under C2 and the difference reaches the player:
// an unmanaged key is absent from the published baseline entirely, so the mod's own default applies
// and nothing the portal publishes can touch it.
func (s *Server) resetWorldConfigSetting(w http.ResponseWriter, r *http.Request) {
	world := r.PathValue("world")
	if !validWorld(world) {
		http.NotFound(w, r)
		return
	}
	if _, err := s.store.PublicWorld(r.Context(), world); err != nil {
		http.NotFound(w, r)
		return
	}
	ref := ConfigSettingRef{
		File:    strings.TrimSpace(r.FormValue("file")),
		Section: r.FormValue("section"),
		Key:     r.FormValue("key"),
	}
	if ref.File == "" || ref.Key == "" {
		http.Error(w, "invalid setting", http.StatusBadRequest)
		return
	}
	if err := s.store.ClearWorldConfigSetting(r.Context(), world, ref, r.Header.Get("X-Portal-Actor")); err != nil {
		http.Error(w, "unable to stop managing that setting", http.StatusInternalServerError)
		return
	}
	http.Redirect(w, r, configManagerReturn(r, world), http.StatusSeeOther)
}

// worldConfigAuthorityJSON serves the world's authority record as the publish path consumes it. It
// is a GET on the settings route because the profile builder needs the bytes as a file.
func (s *Server) worldConfigAuthorityJSON(w http.ResponseWriter, r *http.Request) {
	world := r.PathValue("world")
	if !validWorld(world) {
		http.NotFound(w, r)
		return
	}
	payload, err := s.store.ExportWorldConfigAuthority(r.Context(), world)
	if err != nil {
		http.Error(w, "world config authority unavailable", http.StatusServiceUnavailable)
		return
	}
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.Write(payload)
}

// configPostedValue reads the value out of a form whose widgets submit differently.
//
// A multi-select posts one "values" per selection, joined the way a .cfg writes a flags enum. A
// toggle posts a hidden "false" followed by the checkbox's "true", so the LAST value is the answer:
// an unchecked checkbox submits nothing at all, and without the hidden field a toggle could never be
// turned off. Everything else posts a single "value".
func configPostedValue(r *http.Request) string {
	if selected, multiple := r.Form["values"]; multiple {
		return strings.Join(selected, ", ")
	}
	values := r.Form["value"]
	if len(values) == 0 {
		return ""
	}
	return values[len(values)-1]
}

// configManagerReturn sends the operator back to the view they were in, rather than to the top of a
// world with thousands of settings in it. The view fields are named apart from the setting's own file
// and section, because a search hit is edited from a view that is not that setting's mod.
func configManagerReturn(r *http.Request, world string) string {
	return configManagerLink(
		world,
		strings.TrimSpace(r.FormValue("view_file")),
		r.FormValue("view_section"),
		strings.TrimSpace(r.FormValue("q")),
	)
}

// worldConfigSchema answers with the world's config schema: the cached one when the host says the
// files have not moved, a fresh one when they have, and the cached one unverified when the host
// cannot be reached at all. The second return says whether the freshness question was answered,
// which the page states rather than implies.
//
// The cheap fingerprint read runs on every view for the same reason the mod list's does: config
// files change on the host without the portal being involved, so event invalidation alone would
// serve a stale schema in silence - and a stale schema means editing against allowed values that
// have since changed.
func (s *Server) worldConfigSchema(ctx context.Context, world string) (ConfigSchema, bool, bool) {
	cachedFingerprint, payload, err := s.store.WorldConfigSchemaPayload(ctx, world)
	if err != nil {
		slog.Error("cannot read the cached config schema", "world", world, "error", err)
	}
	current, checked := s.worldConfigSchemaFingerprint(ctx, world)
	if len(payload) > 0 && (!checked || current == cachedFingerprint) {
		if schema, ok := decodeConfigSchema(payload, world); ok {
			return schema, checked, true
		}
		slog.Error("the cached config schema is corrupt and will be rebuilt", "world", world)
	}
	schema, ok := s.rebuildWorldConfigSchema(ctx, world, current, checked)
	if !ok {
		// Nothing fresh. A cached schema was true when it was built and is still worth editing
		// against, so long as the page does not claim it was verified.
		if cached, cachedOK := decodeConfigSchema(payload, world); cachedOK {
			return cached, false, true
		}
		return ConfigSchema{}, false, false
	}
	return schema, checked, true
}

// worldConfigSchemaFingerprint asks the host whether the config files have moved. The second return
// says whether the question was answered at all, which is not the same as an unchanged answer.
func (s *Server) worldConfigSchemaFingerprint(ctx context.Context, world string) (string, bool) {
	reply, err := s.agent.Run(ctx, randomID(), world, "world_config_schema_state")
	if err != nil || reply.Status != "succeeded" || len(reply.Data) == 0 {
		return "", false
	}
	var state struct {
		Fingerprint string `json:"fingerprint"`
	}
	if json.Unmarshal(reply.Data, &state) != nil || len(state.Fingerprint) != 64 {
		return "", false
	}
	return state.Fingerprint, true
}

// rebuildWorldConfigSchema extracts the schema on the host and caches it against the fingerprint the
// state call returned. With no fingerprint the schema is served but NOT cached: a row stored under a
// fingerprint nobody measured could never be shown to be current, and the store requires a real one.
func (s *Server) rebuildWorldConfigSchema(ctx context.Context, world, fingerprint string, checked bool) (ConfigSchema, bool) {
	reply, err := s.agent.Run(ctx, randomID(), world, "world_config_schema")
	if err != nil || reply.Status != "succeeded" || len(reply.Data) == 0 {
		// All three conditions are reported, not just err. This logged `error=<nil>` on
		// 2026-08-21 while the real cause was the agent refusing a 4.5 MiB payload against a
		// 4 MiB cap, and a failure that says nothing sends the diagnosis to the wrong layer.
		slog.Error("cannot extract the world config schema", "world", world, "error", err,
			"status", reply.Status, "agent_error", reply.Error, "bytes", len(reply.Data))
		return ConfigSchema{}, false
	}
	schema, ok := decodeConfigSchema(reply.Data, world)
	if !ok {
		return ConfigSchema{}, false
	}
	if checked {
		if err := s.store.SaveWorldConfigSchema(ctx, world, fingerprint, reply.Data); err != nil {
			slog.Error("cannot cache the world config schema", "world", world, "error", err)
		}
	}
	return schema, true
}

// decodeConfigSchema refuses a payload that is not this world's schema. One naming another world is
// a wiring mistake, and editing against it would set one server's values from another's page.
func decodeConfigSchema(payload []byte, world string) (ConfigSchema, bool) {
	if len(payload) == 0 {
		return ConfigSchema{}, false
	}
	var schema ConfigSchema
	if json.Unmarshal(payload, &schema) != nil {
		return ConfigSchema{}, false
	}
	if schema.Schema != "world-config-schema/v1" || schema.World != world {
		return ConfigSchema{}, false
	}
	return schema, true
}

// configManagerTemplate is the page. The vocabulary is the admin pages' own - .shell, .card, .grid,
// .muted, .hint, .empty - and the style block is inline the way mod_admin.go's is, because the
// Content-Security-Policy at server.go:407 allows 'unsafe-inline' for styles and NOT for scripts.
// That is also why the filter and the key chooser live in an external asset rather than in a
// <script> here: an inline one would be blocked outright and the page would look inert.
const configManagerTemplate = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{.World.Name}} settings</title><style>
.shell{width:min(1240px,calc(100% - 2rem));margin:2rem auto}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:.6rem}
.card{background:#12261c;border:1px solid var(--site-line);border-radius:var(--site-panel-radius);padding:1rem}
.hint{margin:.3rem 0 0;color:var(--site-muted) !important;font-size:.85rem}
.empty{border:1px dashed var(--site-line-strong);border-radius:var(--site-panel-radius);padding:1.2rem;text-align:center}
.config-toolbar{display:flex;flex-wrap:wrap;gap:.8rem;align-items:flex-end;margin:1rem 0}
.config-toolbar form{display:flex;gap:.5rem;align-items:flex-end;flex:1 1 24rem}
.config-toolbar label{display:flex;flex-direction:column;gap:.25rem;flex:1 1 auto}
.config-tally{display:flex;flex-wrap:wrap;gap:.4rem;margin:.6rem 0}
.config-pill{border:1px solid var(--site-line-strong);border-radius:999px;padding:.15rem .7rem;font-size:.82rem}
.config-pill[data-state=forced]{border-color:#e6c15a66;color:#e6c15a}
.config-pill[data-state=seeded]{border-color:#74c99166;color:var(--site-accent-strong)}
.config-pill[data-scope=client]{border-color:#74c99166;color:var(--site-accent-strong)}
.config-pill[data-scope=server]{border-color:#e6c15a66;color:#e6c15a}
.config-pill[data-scope=unknown]{border-color:var(--site-line);color:var(--site-muted)}
.config-setting[data-deliverable=no]{border-left:3px solid #e6c15a66}
.config-pill[data-state=unmanaged]{border-color:var(--site-line);color:var(--site-muted)}
.config-mods{display:grid;grid-template-columns:repeat(auto-fit,minmax(15rem,1fr));gap:.4rem;margin:.6rem 0}
.config-mod{display:flex;justify-content:space-between;gap:.6rem;border:1px solid var(--site-line);border-radius:var(--site-control-radius);padding:.5rem .7rem;text-decoration:none}
.config-mod[data-selected]{border-color:var(--site-accent);background:#193326}
.config-section{margin:1.2rem 0 0}
.config-section > h3{margin:0 0 .2rem;font-size:1rem}
.config-setting{border:1px solid var(--site-line);border-radius:var(--site-panel-radius);padding:.8rem 1rem;margin:.5rem 0;background:#0f2118}
.config-setting[hidden]{display:none}
.config-setting-head{display:flex;flex-wrap:wrap;gap:.5rem;align-items:baseline}
.config-key{font-family:var(--site-mono);font-size:.95rem}
.config-controls{display:flex;flex-wrap:wrap;gap:.8rem;align-items:flex-end;margin:.6rem 0 0}
.config-value{display:flex;gap:.4rem;align-items:center;flex:1 1 20rem;min-width:0}
.config-value input[type=text],.config-value input[type=number],.config-value select{flex:1 1 auto;min-width:0}
.config-value input[type=range]{flex:1 1 12rem}
.config-policy{display:flex;flex-wrap:wrap;gap:.6rem;border:1px solid var(--site-line);border-radius:var(--site-control-radius);padding:.4rem .6rem;margin:0}
.config-policy legend{padding:0 .3rem;font-size:.8rem}
.config-policy label{display:flex;gap:.35rem;align-items:center;font-size:.88rem}
.config-authoritative{margin:0;padding:.35rem .7rem;border:1px solid #e6c15a66;border-radius:var(--site-control-radius);color:#e6c15a;font-size:.85rem}
.config-clientside{border-color:#74c99166;color:var(--site-accent-strong)}
.config-actions{display:flex;gap:.4rem;align-items:flex-end}
.config-notice{margin:.4rem 0 0;padding:.35rem .7rem;border:1px solid var(--site-danger-line);border-radius:var(--site-control-radius);background:var(--site-danger-surface);color:var(--site-danger) !important;font-size:.86rem}
.config-readonly{opacity:.75}
.config-lock{border:1px solid #e6c15a66;border-radius:var(--site-panel-radius);padding:.7rem 1rem;margin:1rem 0;background:#1d1a10}
.config-withheld{margin:1rem 0 0}
</style></head><body class="config-manager-page">
<main class="shell">
<p><a href="/admin">Back to administration</a> · <a href="/admin/worlds/{{.World.Name}}/map">World map and analysis</a></p>
<h1>{{.World.Name}} settings</h1>
{{if .Unavailable}}
<div class="empty"><h2>Settings are unavailable</h2>
<p class="muted">The world's configuration could not be read from the host, and nothing was cached from an earlier view. Nothing here has changed; the page simply has nothing true to show.</p></div>
{{else}}
<p>Every setting the installed mods declare for {{.World.Name}}, grouped by mod. Choose per setting whether the server forces the value or players may change it for themselves.</p>
<div class="config-tally">
<span class="config-pill" data-state="forced">{{.Forced}} server-forced</span>
<span class="config-pill" data-state="seeded">{{.Seeded}} player-overridable</span>
{{if .ScopeKnown}}<span class="config-pill" data-scope="client">{{.Applied}} reach players on their next sync</span>
<span class="config-pill" data-scope="server">{{.Pending}} recorded but not in force</span>
{{else}}<span class="config-pill" data-scope="unknown">whether these reach players could not be read</span>{{end}}
<span class="config-pill" data-state="unmanaged">{{.Total}} settings declared in {{len .Mods}} mods</span>
{{if .GeneratedAt}}<span class="config-pill">read {{.GeneratedAt}}</span>{{end}}
</div>
{{if not .Verified}}<p class="hint">The host did not answer when asked whether these files have changed, so this is the last configuration the portal read rather than a checked one. Values can still be set; they are checked against this schema on save.</p>{{end}}
{{if and .Notice (not .NoticeRef)}}<p class="config-notice">{{.Notice}}</p>{{end}}
<div class="config-toolbar">
<form method="get" action="/admin/worlds/{{.World.Name}}/settings">
<label>Search settings
<input type="search" name="q" value="{{.Query}}" placeholder="key, mod, section or description" minlength="2" maxlength="120">
</label>
{{if .File}}<button type="submit" name="file" value="{{.File}}">Search this mod</button>
<button type="submit" class="secondary">Search every mod</button>
{{else}}<button type="submit">Search every mod</button>{{end}}
{{if or .Query .File}}<a class="button-link secondary" href="/admin/worlds/{{.World.Name}}/settings">Clear</a>{{end}}
</form>
<label>Filter what is shown
<input type="search" data-config-filter placeholder="filter these rows as you type" autocomplete="off">
</label>
</div>
<h2>Mods</h2>
<p class="hint">A mod's own count is settings it declares; the two numbers after it are what the portal controls in that mod.</p>
<div class="config-mods">
{{range .Mods}}<a class="config-mod" href="{{.Link}}"{{if .Selected}} data-selected="1"{{end}}><span>{{.ModName}}</span><span class="muted">{{.Entries}} · {{.Forced}}/{{.Seeded}}</span></a>{{end}}
</div>
{{if eq .View "managed"}}
<h2>What the portal controls</h2>
{{if and (not .Groups) (not .Orphans)}}
<div class="empty"><h3>Nothing is managed yet</h3>
<p class="muted">The portal is not setting any value for {{.World.Name}}, so every mod's own default applies and published profiles carry no configuration. Search above, or open a mod, and set the first one.</p></div>
{{else}}<p class="hint">Only settings the portal is actually controlling. Everything else is left to the mod's own default and is reached by search or by opening a mod.{{if and .ScopeKnown .Pending}} {{.Pending}} of these are read by the server rather than shipped to players, so they are recorded and waiting: the portal has no server-side write path yet.{{end}}</p>{{end}}
{{else if eq .View "search"}}
<h2>{{.Rendered}} matching {{if eq .Rendered 1}}setting{{else}}settings{{end}} for "{{.Query}}"</h2>
{{if not .Groups}}<div class="empty"><h3>Nothing matched</h3><p class="muted">No key, section, mod name or description in {{.World.Name}} contains that text.</p></div>{{end}}
{{else}}
<h2>{{.ModName}}{{if .Section}} · {{.Section}}{{end}}</h2>
{{if .HasLock}}<div class="config-lock"><strong>This mod has an admin lock.</strong>
<p class="hint">{{.Lock.Key}} under [{{.Lock.Section}}], {{if .Lock.Known}}{{if .Lock.On}}currently on{{else}}currently off{{end}}{{else}}a value that reads as neither on nor off{{end}}. It governs whether players' own changes to this mod's synced settings are accepted at all, across every player on the server. It is a setting like any other: <a href="{{.Lock.Link}}">open its section</a> to change it.</p></div>{{end}}
{{if .Sections}}<p class="hint">Choose a section. Sections are listed rather than opened all at once because a single mod here can declare thousands of settings, and drawing them together would be megabytes of controls nobody reads. The search box above reaches any setting directly.</p>
<div class="config-mods">
{{range .Sections}}<a class="config-mod" href="{{.Link}}"><span>{{.Name}}{{if .Immutable}} <span class="muted">read-only</span>{{end}}</span><span class="muted">{{.Entries}} · {{.Forced}}/{{.Seeded}}</span></a>{{end}}
</div>{{end}}
{{end}}
{{range .Groups}}
<section class="config-section">
<h3>{{if $.HasLock}}{{.Section}}{{else}}<a href="{{.Link}}">{{.ModName}} · {{.Section}}</a>{{end}}{{if .Immutable}} <span class="muted">read-only</span>{{end}}</h3>
{{range .Entries}}
<article class="config-setting{{if .Immutable}} config-readonly{{end}}" id="{{.ID}}" data-search="{{.Search}}" data-state="{{.StateKind}}" data-scope="{{.Scope}}" data-deliverable="{{if .Deliverable}}yes{{else}}no{{end}}">
<div class="config-setting-head">
<span class="config-key">{{.Key}}</span>
<span class="config-pill" data-state="{{.StateKind}}">{{.State}}</span>
<span class="config-pill" data-scope="{{.Scope}}">{{if eq .Scope "client"}}reaches players{{else if eq .Scope "server"}}server-side, not in force{{else}}delivery unknown{{end}}</span>
{{if .Type}}<span class="muted">{{.Type}}</span>{{end}}
{{if .Advanced}}<span class="muted">advanced</span>{{end}}
{{if .Where}}<span class="muted">{{.Where}}</span>{{end}}
</div>
{{if .Description}}<p class="hint">{{.Description}}</p>{{end}}
{{if not .Deliverable}}<p class="hint">{{.ScopeReason}}</p>{{end}}
{{if eq .Widget "readonly"}}
<div class="config-controls">
<div class="config-value"><input type="text" value="{{.Value}}" disabled aria-label="{{.Key}}, read-only"></div>
<p class="config-authoritative">Read-only · takes effect on restart</p>
</div>
<p class="hint">{{.Reason}}</p>
{{else}}
<form method="post" action="/admin/worlds/{{$.World.Name}}/settings">
<input type="hidden" name="csrf" value="{{$.CSRF}}">
<input type="hidden" name="file" value="{{.File}}">
<input type="hidden" name="section" value="{{.Section}}">
<input type="hidden" name="key" value="{{.Key}}">
<input type="hidden" name="view_file" value="{{$.File}}">
<input type="hidden" name="view_section" value="{{$.Section}}">
<input type="hidden" name="q" value="{{$.Query}}">
<div class="config-controls">
<div class="config-value">
{{if eq .Widget "toggle"}}
<input type="hidden" name="value" value="false">
<input type="checkbox" id="{{.ID}}-value" name="value" value="true" role="switch"{{if .Checked}} checked{{end}}>
<label for="{{.ID}}-value">on</label>
{{else if eq .Widget "select"}}
<select id="{{.ID}}-value" name="value" aria-label="{{.Key}}">
{{range .Options}}<option value="{{.Value}}"{{if .Selected}} selected{{end}}>{{.Value}}{{if .Unlisted}} (not one of the mod's listed values){{end}}</option>{{end}}
</select>
{{else if eq .Widget "multiselect"}}
<input type="hidden" name="value" value="">
<select id="{{.ID}}-value" name="values" multiple size="4" aria-label="{{.Key}}">
{{range .Options}}<option value="{{.Value}}"{{if .Selected}} selected{{end}}>{{.Value}}{{if .Unlisted}} (not one of the mod's listed values){{end}}</option>{{end}}
</select>
{{else if eq .Widget "slider"}}
<input type="range" min="{{.Min}}" max="{{.Max}}"{{if .Step}} step="{{.Step}}"{{end}} value="{{.Value}}" data-slider-for="{{.ID}}-value" aria-label="{{.Key}} slider" tabindex="-1">
<input type="number" id="{{.ID}}-value" name="value" value="{{.Value}}" min="{{.Min}}" max="{{.Max}}" aria-label="{{.Key}}">
{{else if eq .Widget "key"}}
<input type="text" id="{{.ID}}-value" name="value" value="{{.Value}}"{{if .Options}} list="{{.ID}}-keys"{{end}} autocomplete="off" aria-label="{{.Key}}">
<button type="button" data-key-capture="{{.ID}}-value">Press a key</button>
{{if .Options}}<datalist id="{{.ID}}-keys">{{range .Options}}<option value="{{.Value}}"></option>{{end}}</datalist>{{end}}
{{else if .Numeric}}
<input type="number" id="{{.ID}}-value" name="value" value="{{.Value}}"{{if .Min}} min="{{.Min}}"{{end}}{{if .Max}} max="{{.Max}}"{{end}}{{if .Step}} step="{{.Step}}"{{end}} aria-label="{{.Key}}">
{{else}}
<input type="text" id="{{.ID}}-value" name="value" value="{{.Value}}"{{if .Pattern}} pattern="{{.Pattern}}"{{end}}{{if .PatternHint}} title="{{.PatternHint}}"{{end}} maxlength="2000" aria-label="{{.Key}}">
{{end}}
</div>
{{if .OverrideAllowed}}
<fieldset class="config-policy">
<legend>Who decides</legend>
<label><input type="radio" name="policy" value="server_forced"{{if ne .Policy "client_default"}} checked{{end}}>the server forces it</label>
<label><input type="radio" name="policy" value="client_default"{{if eq .Policy "client_default"}} checked{{end}}>players may change it</label>
</fieldset>
{{else}}
<p class="config-authoritative">Server-authoritative</p>
<input type="hidden" name="policy" value="server_forced">
{{end}}
<div class="config-actions"><button type="submit">Save</button></div>
</div>
</form>
{{if not .OverrideAllowed}}<p class="hint">{{.Reason}}</p>{{end}}
{{if .ClientSide}}<p class="hint config-clientside">The mod's author marked this one as not synced with the server, so a player's own value is the one that applies. It is a good candidate for letting players choose.</p>{{end}}
{{if .Managed}}
<form method="post" action="/admin/worlds/{{$.World.Name}}/settings/reset">
<input type="hidden" name="csrf" value="{{$.CSRF}}">
<input type="hidden" name="file" value="{{.File}}">
<input type="hidden" name="section" value="{{.Section}}">
<input type="hidden" name="key" value="{{.Key}}">
<input type="hidden" name="view_file" value="{{$.File}}">
<input type="hidden" name="view_section" value="{{$.Section}}">
<input type="hidden" name="q" value="{{$.Query}}">
<button type="submit" class="secondary">Stop managing this setting</button>
<span class="hint">Removes the portal's record so the mod's own default ({{if .Default}}{{.Default}}{{else}}whatever the mod compiles in{{end}}) applies again. It does not write the default as a value: an unmanaged setting is absent from published profiles entirely, which is not the same thing and players can tell.</span>
</form>
{{end}}
{{end}}
{{if .Notice}}<p class="config-notice">{{.Notice}}</p>{{end}}
</article>
{{end}}
</section>
{{end}}
{{if .Orphans}}
<h2>Managed settings no mod declares any more</h2>
<p class="hint">The portal still publishes each of these, so they still reach players, but nothing in the installed configuration declares them - a mod removed, renamed, or upgraded past the setting. Stopping management is the only honest action.</p>
{{range .Orphans}}<article class="config-setting" data-state="{{.StateKind}}">
<div class="config-setting-head"><span class="config-key">{{.Key}}</span><span class="config-pill" data-state="{{.StateKind}}">{{.State}}</span><span class="muted">{{.File}} · {{.Section}}</span></div>
<p class="hint">Published as <code>{{.Key}} = {{.Value}}</code>.</p>
<form method="post" action="/admin/worlds/{{$.World.Name}}/settings/reset">
<input type="hidden" name="csrf" value="{{$.CSRF}}">
<input type="hidden" name="file" value="{{.File}}">
<input type="hidden" name="section" value="{{.Section}}">
<input type="hidden" name="key" value="{{.Key}}">
<button type="submit" class="secondary">Stop managing this setting</button>
</form>
</article>{{end}}
{{end}}
{{if .Withheld}}<p class="config-withheld muted">{{.Withheld}} more {{if eq .Withheld 1}}setting is{{else}}settings are{{end}} not drawn on this view. Nothing was dropped. Some mods declare a single section of well over a thousand per-item settings, more than any one page should render, so search is the way to reach them - the box above will search inside one mod as well as across all of them.</p>{{end}}
{{end}}
</main>
<script src="/assets/config-manager.js" defer></script>
</body></html>`
