package app

import (
	"path/filepath"
	"strings"
)

// StagedArtifactName is the filename an artifact is recorded under, given the file it
// was built or carried forward from.
//
// Two places have to agree on it and did not: seed-release names the copy it keeps, and
// the profile-definition builder writes the companion's filename into the definition the
// store then cross-checks against the artifact row. A republish hands both of them the
// file staged last time, whose basename already carries the kind prefix - so one side
// stripped it and the other did not, and the store's own check
// (`Flat companion artifact does not match the profile manifest`) would have refused the
// next publish that carried a companion forward instead of being handed an explicit one.
//
// Stripping repeatedly rather than once is deliberate: the fleet reached nine stacked
// `flat_companion-` prefixes and a 205-character filename before anyone noticed, which
// crossed the 180-character cap installed clients enforce.
func StagedArtifactName(kind, source string) string {
	name := filepath.Base(source)
	prefix := kind + "-"
	for strings.HasPrefix(name, prefix) {
		name = strings.TrimPrefix(name, prefix)
	}
	return name
}
