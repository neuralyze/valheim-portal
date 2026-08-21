package app

import (
	"encoding/json"
	"strings"
	"unicode"

	"github.com/neuralyze/valheim-portal/internal/worldintel"
)

// A location's name in a snapshot is the prefab id Valheim placed there: MWL_AshlandsFort1,
// CharredTowerRuins1_dvergr, Greydwarf_camp1, GDKing. The map readout printed that id verbatim, so
// the world read as a list of a mod's asset keys rather than a list of places. These functions
// derive a readable name from the id.
//
// The id is never rewritten. The snapshot keeps it, every payload keeps it, and the readout prints
// it on its own line next to the derived name, because an operator chasing a spawn needs
// MWL_AshlandsFort1 and no prettier string will do.
//
// Everything here derives from what the prefab author actually wrote. It does not invent lore: a
// vanilla Crypt2 becomes "Crypt", not "Sunken Crypt", because nothing in this repo can cite the
// game's own name for it.

// locationName is one prefab id split into the parts a reader needs and the part that is only
// provenance.
type locationName struct {
	Display string `json:"display"`
	// Mod is the location pack's tag, e.g. MWL. Empty for a vanilla prefab.
	Mod string `json:"mod,omitempty"`
}

// derivedNameFixes are the ids where splitting on the boundaries the prefab author wrote lands on
// the wrong words. Exactly one id in the 326-name Hrafnheim corpus qualifies: PlaceofMystery omits
// the capital before "of", so a case-boundary split can only ever see "Placeof". The table is
// keyed on the name after the mod tag and the variant number are removed.
//
// This table is not a place to invent names. If a split is merely plain rather than wrong, it stays
// derived.
var derivedNameFixes = map[string]string{
	"PlaceofMystery": "Place of Mystery",
}

// assetRevisionQualifiers describe the asset rather than the place, so they are dropped. Iron Gate
// re-authored the Mistlands guard towers and lighthouse and marked the replacements "_new"; no
// un-suffixed sibling exists anywhere in the corpus, so "new" separates a prefab from something
// that is not in the world. "_ruined" is deliberately not in here: Mistlands_GuardTower1_new and
// Mistlands_GuardTower1_ruined_new both exist, so that qualifier does separate two real places.
var assetRevisionQualifiers = map[string]bool{
	"new": true,
}

// locationDisplayName derives a readable place name from a prefab id.
//
// The one rule everything else obeys: words are split only at boundaries the prefab author encoded
// - an underscore, a case change, or a digit. A run of lowercase letters is never broken up. That
// is what keeps Greydwarf, Dvergr, Volture, Mistlands, Dragonqueen and plainsfortress intact; a
// dictionary-driven splitter would turn the first into "Grey Dwarf" and has no way to be right
// about the last.
func locationDisplayName(raw string) locationName {
	trimmed := strings.TrimSpace(raw)
	if trimmed == "" {
		return locationName{}
	}
	segments := strings.Split(trimmed, "_")
	derived := locationName{}
	// A leading all-caps segment is a location pack's tag - MWL (More World Locations AIO), BFD, CD
	// in this corpus. It is matched by shape rather than by list because a pack that is not on the
	// list would otherwise print its tag as the first word of every name it contributes.
	if len(segments) > 1 && isModTag(segments[0]) {
		derived.Mod = segments[0]
		segments = segments[1:]
	}

	words := make([]string, 0, 8)
	// Digits end the name: everything past a variant number is a qualifier, not part of the place.
	// That is the difference between Greydwarf_camp1, where "camp" is the thing itself, and
	// CharredTowerRuins1_dvergr, where "dvergr" says which faction's version of it this is.
	numbered := false
	qualifiers := make([]string, 0, 2)
	for _, segment := range segments {
		if segment == "" {
			continue
		}
		segmentWords, hasVariant := splitPrefabWords(segment)
		if len(segmentWords) == 0 {
			continue
		}
		if numbered && isLower(segment) {
			for _, word := range segmentWords {
				if assetRevisionQualifiers[strings.ToLower(word)] {
					continue
				}
				qualifiers = append(qualifiers, capitalise(word))
			}
			continue
		}
		for _, word := range segmentWords {
			words = append(words, capitalise(word))
		}
		numbered = numbered || hasVariant
	}

	base := strings.Join(words, " ")
	if fixed, ok := derivedNameFixes[strings.Join(words, "")]; ok {
		base = fixed
	}
	if base == "" {
		// Nothing survived - an id that is only a tag and a number, say. The id itself beats an
		// empty label.
		derived.Display = trimmed
		return derived
	}
	if len(qualifiers) > 0 {
		base += " (" + strings.Join(qualifiers, ", ") + ")"
	}
	derived.Display = base
	return derived
}

// isModTag reports whether a leading segment is a location pack's tag: two to four capitals, which
// is the shape of every one in the corpus (MWL, BFD, CD).
func isModTag(segment string) bool {
	if len(segment) < 2 || len(segment) > 4 {
		return false
	}
	for _, r := range segment {
		if !unicode.IsUpper(r) {
			return false
		}
	}
	return true
}

func isLower(segment string) bool {
	for _, r := range segment {
		if unicode.IsUpper(r) {
			return false
		}
	}
	return true
}

// splitPrefabWords breaks one underscore segment into words and reports whether it ended in a
// variant number.
//
// The variant number is dropped. It indexes the mod's or the game's own asset set, not anything a
// player could count: the corpus holds StoneTowerRuins03 through 10 with no 01, 02 or 06, and
// MWL_Ruins1, 2, 3, 6, 7, 8 with no 4 or 5. "Ruins 6" would send a reader looking for five others
// that are nowhere in the world. The number stays reachable in the prefab id printed beside the
// name, which is the form an operator needs anyway.
func splitPrefabWords(segment string) (words []string, hasVariant bool) {
	runes := []rune(segment)
	words = make([]string, 0, 4)
	start := 0
	for i := 1; i < len(runes); i++ {
		previous, current := runes[i-1], runes[i]
		boundary := false
		switch {
		case unicode.IsDigit(current) != unicode.IsDigit(previous):
			boundary = true
		case unicode.IsUpper(current) && !unicode.IsUpper(previous):
			boundary = true
		case unicode.IsUpper(current) && unicode.IsUpper(previous) && i+1 < len(runes) && unicode.IsLower(runes[i+1]):
			// The tail of an acronym starts the next word: GDKing is "GD King", not "GDKing" or
			// "G D King". One id in the corpus needs this, and getting it wrong is very visible.
			boundary = true
		}
		if boundary {
			words = append(words, string(runes[start:i]))
			start = i
		}
	}
	words = append(words, string(runes[start:]))
	for len(words) > 0 {
		last := words[len(words)-1]
		if last == "" || !unicode.IsDigit([]rune(last)[0]) {
			break
		}
		words = words[:len(words)-1]
		hasVariant = true
	}
	return words, hasVariant
}

func capitalise(word string) string {
	runes := []rune(word)
	if len(runes) == 0 {
		return word
	}
	// Only the first rune is touched. Upper-casing the rest would flatten GD to Gd and would break
	// every compound the split rule exists to protect.
	runes[0] = unicode.ToUpper(runes[0])
	return string(runes)
}

// locationNameLegend maps every distinct prefab id in a snapshot to its derived name, as JSON for
// the map canvas.
//
// It is built from a snapshot rather than derived in the browser so that one function decides what
// a place is called, and it is built from whatever snapshot the caller hands over. The players' map
// passes its clipped snapshot, so the legend names only what that player has actually found -
// naming every location in the world on a fogged map would hand over the answers. Overlay tiles
// are clipped the same way before they are cut, so every id a tile can deliver is in the legend.
func locationNameLegend(locations []worldintel.Location) string {
	names := make(map[string]locationName, len(locations))
	for _, location := range locations {
		if location.Name == "" {
			continue
		}
		if _, seen := names[location.Name]; seen {
			continue
		}
		names[location.Name] = locationDisplayName(location.Name)
	}
	encoded, err := json.Marshal(names)
	if err != nil {
		return "{}"
	}
	return string(encoded)
}
