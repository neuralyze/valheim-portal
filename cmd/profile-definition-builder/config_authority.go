package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"sort"
	"strings"

	app "github.com/neuralyze/valheim-portal/internal/app"
)

// The world's stored settings authority, layered over the profile's hand-maintained
// client-config files at build time.
//
// Layering rather than replacing is deliberate. The VR profile's client-config holds real,
// in-use configuration - neuralyze.vrfixes.cfg and org.bepinex.plugins.valheimvrmod.cfg are
// how the fleet is configured today - so a build that emitted only the portal's records
// would silently drop every setting an operator has ever hand-edited. The rule is per KEY:
// a key the portal has a record for is rewritten, and a key it has no record for is left
// exactly as the source file wrote it, byte for byte. Absence of a record is a real third
// state, not a default: it means the portal does not manage that key and the mod's own value
// stands.
const (
	configAuthoritySchema  = "world-config-authority/v1"
	settingsBaselineSchema = "settings-baseline/v1"

	// Where the baseline lives inside the profile ZIP, and why it lives THERE. Decided
	// 2026-08-21 after the root placement was measured to be a fleet-wide lockout: the
	// installer's archive allowlist admits only `profile-manifest.json` and anything under
	// `config/`, and everything else hits `default: "profile definition contains an
	// unsupported file"`. There is no self-update path in the client and sync runs before the
	// game launches, so an archive an installed client cannot parse stops players launching
	// Valheim rather than pinning them to an old profile - which is exactly what an added
	// `audience` MANIFEST FIELD did on 2026-08-17. A new top-level member would have repeated
	// that incident on the first routine republish, for every player who had not re-downloaded
	// the launcher.
	//
	// Under `config/` the existing allowlist already admits it, so no client needs changing,
	// no publish ordering applies and no feature gate is needed. The whole cost is one inert
	// .json in the player's BepInEx/config on an old client, which BepInEx never reads because
	// it globs *.cfg. Anyone adding another artifact to this archive faces the same choice and
	// the allowlist will not explain it, so it is written down here.
	settingsBaselineZIPName = "config/settings-baseline.json"

	maxConfigAuthorityBytes = 4 << 20

	policyServerForced  = "server_forced"
	policyClientDefault = "client_default"
)

// worldConfigAuthority is the `world-config-authority/v1` document produced by the portal
// store for one world.
type worldConfigAuthority struct {
	Schema      string                 `json:"schema"`
	World       string                 `json:"world"`
	GeneratedAt string                 `json:"generated_at"`
	Entries     []configAuthorityEntry `json:"entries"`
}

type configAuthorityEntry struct {
	// File is the path relative to the profile's config/ root, forward-slashed. Every .cfg
	// the portal manages today sits flat at that root, so it reads as a bare filename.
	File    string `json:"file"`
	Section string `json:"section"`
	Key     string `json:"key"`
	Value   string `json:"value"`
	Policy  string `json:"policy"`
}

// settingsBaseline is the `settings-baseline/v1` manifest shipped inside every profile that
// was built with an authority document. It is what makes the client merge implementable: the
// installer keeps the last applied copy on disk, and comparing a player's current value
// against the recorded `written` is the ONLY way to tell "the player edited this" from "the
// admin changed the default". Comparing against the current server value cannot distinguish
// them, and would wipe a player's customisation the first time an admin edited a default.
type settingsBaseline struct {
	Schema  string                  `json:"schema"`
	World   string                  `json:"world"`
	Profile string                  `json:"profile"`
	Version string                  `json:"version,omitempty"`
	Entries []settingsBaselineEntry `json:"entries"`
}

type settingsBaselineEntry struct {
	File    string `json:"file"`
	Section string `json:"section"`
	Key     string `json:"key"`
	Policy  string `json:"policy"`
	// Written is the exact string this build put into the .cfg. Server-intended, never
	// per-player: the installer stores it verbatim as the last applied baseline.
	Written string `json:"written"`
}

// cfgAddr is a section-qualified key inside one configuration file. The empty section is
// what BepInEx writes for a key before the first [Section] header, which is a real address
// rather than an error.
type cfgAddr struct {
	Section string
	Key     string
}

// loadConfigAuthority reads the authority document for a build, either from a file or
// straight out of the portal store.
//
// Two sources because there are two callers. republish-profiles.sh runs on the host with the
// deployed sqlite path already in hand, and going through the portal's HTTP API would add an
// auth surface plus a new way for a publish to fail (portal down, no publish) for no gain -
// cmd/seed-release already opens that same database. A file is what a hand build and the
// tests use. Neither flag means: no layering, no baseline, byte-identical to today's artifact.
func loadConfigAuthority(ctx context.Context, options builderOptions) (*worldConfigAuthority, error) {
	switch {
	case options.ConfigAuthorityPath != "":
		raw, err := os.ReadFile(options.ConfigAuthorityPath)
		if err != nil {
			return nil, fmt.Errorf("read config authority: %w", err)
		}
		return decodeConfigAuthority(raw, options.World)
	case options.ConfigAuthorityDatabase != "":
		store, err := app.OpenStore(options.ConfigAuthorityDatabase)
		if err != nil {
			return nil, fmt.Errorf("open config authority database: %w", err)
		}
		defer store.Close()
		raw, err := store.ExportWorldConfigAuthority(ctx, options.World)
		if err != nil {
			return nil, fmt.Errorf("export config authority: %w", err)
		}
		return decodeConfigAuthority(raw, options.World)
	default:
		return nil, nil
	}
}

func decodeConfigAuthority(raw []byte, world string) (*worldConfigAuthority, error) {
	if int64(len(raw)) > maxConfigAuthorityBytes {
		return nil, fmt.Errorf("config authority is larger than %d bytes", maxConfigAuthorityBytes)
	}
	// A UTF-8 BOM defeats strict JSON decoding, and 26 of the 100 plugin manifests on this
	// host carry one, so a hand-saved authority file plausibly will too.
	raw = bytes.TrimPrefix(raw, []byte("\xef\xbb\xbf"))

	var document worldConfigAuthority
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&document); err != nil {
		return nil, fmt.Errorf("decode config authority: %w", err)
	}
	if document.Schema != configAuthoritySchema {
		return nil, fmt.Errorf("config authority schema is %q, want %q", document.Schema, configAuthoritySchema)
	}
	// Publishing one world's forced values into another is a mistake that only shows up in
	// play, so it is a build failure rather than a warning.
	if document.World != world {
		return nil, fmt.Errorf("config authority is for world %q, building %q", document.World, world)
	}
	seen := make(map[string]struct{}, len(document.Entries))
	for _, entry := range document.Entries {
		if entry.File == "" || entry.Key == "" {
			return nil, fmt.Errorf("config authority entry has an empty file or key")
		}
		if _, err := configZIPPath(entry.File, false); err != nil {
			return nil, fmt.Errorf("config authority entry file %q: %w", entry.File, err)
		}
		// A policy the builder cannot interpret must not be guessed at: writing a value the
		// installer will then treat under the wrong rule is the exact failure this feature
		// exists to prevent.
		if entry.Policy != policyServerForced && entry.Policy != policyClientDefault {
			return nil, fmt.Errorf("config authority entry %s/%s/%s has policy %q", entry.File, entry.Section, entry.Key, entry.Policy)
		}
		if strings.ContainsAny(entry.Key, "=[]\r\n") {
			return nil, fmt.Errorf("config authority key %q is not writable into a cfg file", entry.Key)
		}
		if strings.ContainsAny(entry.Value, "\r\n") || strings.ContainsAny(entry.Section, "[]\r\n") {
			return nil, fmt.Errorf("config authority entry %s/%s/%s spans lines", entry.File, entry.Section, entry.Key)
		}
		identity := entry.File + "\x00" + entry.Section + "\x00" + entry.Key
		if _, exists := seen[identity]; exists {
			return nil, fmt.Errorf("config authority names %s/%s/%s twice", entry.File, entry.Section, entry.Key)
		}
		seen[identity] = struct{}{}
	}
	return &document, nil
}

// applyConfigAuthority layers the world's authority over the collected config entries and
// appends the settings baseline manifest as one more entry under config/.
//
// Every record reaches the file, so every record reaches the baseline: the manifest states
// what this build actually wrote, and the installer relies on that being true.
func applyConfigAuthority(entries []configEntry, authority *worldConfigAuthority, options builderOptions) ([]configEntry, *settingsBaseline, error) {
	wanted := make(map[string]map[cfgAddr]configAuthorityEntry)
	for _, entry := range authority.Entries {
		zipName, err := configZIPPath(entry.File, false)
		if err != nil {
			return nil, nil, err
		}
		if zipName == settingsBaselineZIPName {
			return nil, nil, fmt.Errorf("config authority names %s, which the portal writes itself", entry.File)
		}
		if _, ok := wanted[zipName]; !ok {
			wanted[zipName] = map[cfgAddr]configAuthorityEntry{}
		}
		wanted[zipName][cfgAddr{Section: entry.Section, Key: entry.Key}] = entry
	}

	baseline := &settingsBaseline{
		Schema:  settingsBaselineSchema,
		World:   options.World,
		Profile: options.Profile,
		Version: options.ProfileVersion,
		Entries: []settingsBaselineEntry{},
	}

	layered := make([]configEntry, 0, len(entries)+len(wanted)+1)
	for _, entry := range entries {
		// The profile config must not carry this name itself: the portal owns it, and two
		// entries with one ZIP path is a corrupt archive rather than a merge.
		if entry.zipName == settingsBaselineZIPName {
			return nil, nil, fmt.Errorf("profile config already contains %s, which the portal writes", settingsBaselineZIPName)
		}
		keys, managed := wanted[entry.zipName]
		if entry.isDir || !managed {
			layered = append(layered, entry)
			continue
		}
		original := entry.body
		if original == nil {
			read, err := os.ReadFile(entry.source)
			if err != nil {
				return nil, nil, fmt.Errorf("read %s: %w", entry.zipName, err)
			}
			original = read
		}
		patched := patchCFGAuthority(original, keys)
		entry.source = ""
		entry.body = patched
		layered = append(layered, entry)
		baseline.Entries = append(baseline.Entries, baselineEntries(keys)...)
		delete(wanted, entry.zipName)
	}

	// A record naming a file the profile does not ship. Creating it is the lesser evil: an
	// admin's forced value that never reaches a file does nothing at all, and that failure is
	// invisible, whereas an extra .cfg for a mod the client does not load is inert - BepInEx
	// only reads the configs of plugins that are present. Named on stderr either way, because
	// creating a file the operator did not write should never be silent.
	created := make([]string, 0, len(wanted))
	for zipName := range wanted {
		created = append(created, zipName)
	}
	sort.Strings(created)
	for _, zipName := range created {
		keys := wanted[zipName]
		fmt.Fprintf(os.Stderr, "config authority: %s is not in the profile config, creating it for %d managed key(s)\n", zipName, len(keys))
		layered = append(layered, configEntry{zipName: zipName, body: newAuthorityCFG(options.World, keys)})
		baseline.Entries = append(baseline.Entries, baselineEntries(keys)...)
	}

	// Sorted so two publishes of the same authority produce identical bytes and a diff
	// between publishes is readable.
	sort.Slice(baseline.Entries, func(i, j int) bool {
		left, right := baseline.Entries[i], baseline.Entries[j]
		if left.File != right.File {
			return left.File < right.File
		}
		if left.Section != right.Section {
			return left.Section < right.Section
		}
		return left.Key < right.Key
	})

	encoded, err := encodeSettingsBaseline(baseline)
	if err != nil {
		return nil, nil, err
	}
	layered = append(layered, configEntry{zipName: settingsBaselineZIPName, body: encoded})
	sort.Slice(layered, func(i, j int) bool { return layered[i].zipName < layered[j].zipName })
	return layered, baseline, nil
}

func baselineEntries(keys map[cfgAddr]configAuthorityEntry) []settingsBaselineEntry {
	out := make([]settingsBaselineEntry, 0, len(keys))
	for _, entry := range keys {
		out = append(out, settingsBaselineEntry{
			File:    entry.File,
			Section: entry.Section,
			Key:     entry.Key,
			Policy:  entry.Policy,
			Written: entry.Value,
		})
	}
	return out
}

func encodeSettingsBaseline(baseline *settingsBaseline) ([]byte, error) {
	encoded, err := json.Marshal(baseline)
	if err != nil {
		return nil, fmt.Errorf("encode settings baseline: %w", err)
	}
	return append(encoded, '\n'), nil
}

// cfgLine is one line of a configuration file with the terminator it actually carried.
//
// Terminators are tracked per line rather than per file because BepInEx configs on this host
// are genuinely MIXED: Hrafnheim's ZenDragon.ZenBreeding.cfg has CRLF comment lines sitting
// beside LF ones in the same section. Anything that reads with bufio.ScanLines or Python's
// splitlines() silently normalises the lot to LF, which rewrites hundreds of lines nobody
// asked to touch and makes the real one-line change unreadable in a diff.
type cfgLine struct {
	text string
	term string
}

func splitCFGLines(data []byte) []cfgLine {
	if len(data) == 0 {
		return nil
	}
	lines := make([]cfgLine, 0, bytes.Count(data, []byte("\n"))+1)
	for len(data) > 0 {
		index := bytes.IndexByte(data, '\n')
		if index < 0 {
			// A final line with no terminator: preserved as such, so a file that did not end
			// in a newline does not gain one.
			lines = append(lines, cfgLine{text: string(data)})
			break
		}
		text := data[:index]
		term := "\n"
		if len(text) > 0 && text[len(text)-1] == '\r' {
			text = text[:len(text)-1]
			term = "\r\n"
		}
		lines = append(lines, cfgLine{text: string(text), term: term})
		data = data[index+1:]
	}
	return lines
}

// dominantTerminator is the line ending used for lines this build APPENDS. Existing lines
// keep their own, so this only decides what a new key looks like in a file that already
// leans one way.
func dominantTerminator(lines []cfgLine) string {
	crlf, lf := 0, 0
	for _, line := range lines {
		switch line.term {
		case "\r\n":
			crlf++
		case "\n":
			lf++
		}
	}
	if crlf > lf {
		return "\r\n"
	}
	return "\n"
}

// patchCFGAuthority rewrites the managed keys of one configuration file in place.
//
// What it preserves, and why: the comment blocks above each key are the ONLY machine-readable
// schema BepInEx publishes - `## Setting type:`, `## Default value:`, `## Acceptable values:`
// - and the settings extractor parses exactly those lines to know a setting is a Boolean or
// an enum with three allowed values. Reflowing the file, dropping its comments or rewriting
// its line endings would leave the next extraction with nothing to read. So the only bytes
// that change are the value on a managed key's own line, and whatever is appended for a key
// the file did not have.
func patchCFGAuthority(original []byte, wanted map[cfgAddr]configAuthorityEntry) []byte {
	// The BOM is carried through rather than stripped: it is part of the file the profile
	// ships, and dropping it is a change nobody asked for.
	bom := ""
	if bytes.HasPrefix(original, []byte("\xef\xbb\xbf")) {
		bom = "\xef\xbb\xbf"
		original = original[3:]
	}

	pending := make(map[cfgAddr]configAuthorityEntry, len(wanted))
	for addr, entry := range wanted {
		pending[addr] = entry
	}

	lines := splitCFGLines(original)
	terminator := dominantTerminator(lines)

	var out bytes.Buffer
	out.WriteString(bom)
	section := ""
	appendPending := func(name string) {
		addrs := make([]cfgAddr, 0, len(pending))
		for addr := range pending {
			if addr.Section == name {
				addrs = append(addrs, addr)
			}
		}
		sort.Slice(addrs, func(i, j int) bool { return addrs[i].Key < addrs[j].Key })
		for _, addr := range addrs {
			entry := pending[addr]
			writeAuthorityKey(&out, entry, terminator)
			delete(pending, addr)
		}
	}

	for _, line := range lines {
		trimmed := strings.TrimSpace(line.text)
		if strings.HasPrefix(trimmed, "[") && strings.HasSuffix(trimmed, "]") {
			appendPending(section)
			section = strings.TrimSpace(trimmed[1 : len(trimmed)-1])
			out.WriteString(line.text)
			out.WriteString(line.term)
			continue
		}
		key, ok := cfgLineKey(line.text)
		if !ok {
			out.WriteString(line.text)
			out.WriteString(line.term)
			continue
		}
		entry, managed := wanted[cfgAddr{Section: section, Key: key}]
		if !managed {
			out.WriteString(line.text)
			out.WriteString(line.term)
			continue
		}
		// Every occurrence is rewritten, not just the first. A file with the same key twice
		// in one section is legal text, and BepInEx takes the last one, so rewriting only the
		// first would leave a stale line that still wins.
		out.WriteString(replaceCFGValue(line.text, entry.Value))
		out.WriteString(line.term)
		delete(pending, cfgAddr{Section: section, Key: key})
	}
	appendPending(section)

	// Sections the file never had.
	remaining := make([]cfgAddr, 0, len(pending))
	for addr := range pending {
		remaining = append(remaining, addr)
	}
	sort.Slice(remaining, func(i, j int) bool {
		if remaining[i].Section != remaining[j].Section {
			return remaining[i].Section < remaining[j].Section
		}
		return remaining[i].Key < remaining[j].Key
	})
	lastSection := "\x00"
	for _, addr := range remaining {
		if addr.Section != lastSection {
			if out.Len() > 0 {
				out.WriteString(terminator)
			}
			if addr.Section != "" {
				fmt.Fprintf(&out, "[%s]%s", addr.Section, terminator)
			}
			lastSection = addr.Section
		}
		writeAuthorityKey(&out, pending[addr], terminator)
	}
	return out.Bytes()
}

// writeAuthorityKey appends a key the file did not have, with a marker naming the policy. The
// marker is for whoever opens the file on a client and wonders where the line came from - and
// whether they are allowed to change it. BepInEx regenerates its own metadata comments on
// next load, so this does not pretend to be one.
func writeAuthorityKey(out *bytes.Buffer, entry configAuthorityEntry, terminator string) {
	fmt.Fprintf(out, "## portal-managed (%s)%s", entry.Policy, terminator)
	fmt.Fprintf(out, "%s = %s%s", entry.Key, entry.Value, terminator)
}

func newAuthorityCFG(world string, keys map[cfgAddr]configAuthorityEntry) []byte {
	return patchCFGAuthority([]byte(fmt.Sprintf(
		"## Written by the portal settings configuration manager for %s.\n"+
			"## The profile shipped no file for these keys, so this file carries only the ones\n"+
			"## the portal manages. BepInEx will add its own metadata on next load.\n", world)), keys)
}

// cfgLineKey is the key a configuration line assigns to, if it assigns to one. Comments are
// not keys even when they contain an '=' - `## Default value: x = y` is a comment.
func cfgLineKey(text string) (string, bool) {
	trimmed := strings.TrimSpace(text)
	if trimmed == "" || strings.HasPrefix(trimmed, "#") || strings.HasPrefix(trimmed, ";") || strings.HasPrefix(trimmed, "[") {
		return "", false
	}
	index := strings.Index(text, "=")
	if index < 1 {
		return "", false
	}
	key := strings.TrimSpace(text[:index])
	if key == "" {
		return "", false
	}
	return key, true
}

// replaceCFGValue swaps the value on an assignment line, keeping the key text and the spacing
// around the '=' exactly as the file wrote them. A cfg value has no inline-comment syntax in
// BepInEx - a '#' inside a value is part of the value - so there is nothing after the value
// to preserve.
func replaceCFGValue(text, value string) string {
	index := strings.Index(text, "=")
	gap := index + 1
	for gap < len(text) && (text[gap] == ' ' || text[gap] == '\t') {
		gap++
	}
	return text[:gap] + value
}
