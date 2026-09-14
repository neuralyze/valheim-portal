// TEMPORARY: local ServerCharacters build. Delete this file when Thunderstore publishes
// 1.4.17; the only other client-side reference is the gated branch in ensureCachedPackage.
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"strings"

	"github.com/neuralyze/valheim-portal/internal/servercharacters"
)

// writeEmbeddedPackage puts the compiled-in ServerCharacters archive where a downloaded one
// would have landed, after checking it is the build the definition was published from.
//
// The check is the point. Every other package's integrity comes from comparing downloaded
// bytes against a SHA256 the publisher computed; here both ends are ours, so the comparison
// answers a different question: does this client carry the same build as the server? A
// player running an older executable gets a named error telling them to download the
// current client, instead of installing a mismatched build and being refused by ServerSync
// mid-join with "Mod version mismatch" - which is the failure this whole arrangement exists
// to make impossible.
func writeEmbeddedPackage(path string, packageInfo packageDefinition) error {
	body, err := servercharacters.Archive()
	if err != nil {
		return err
	}
	sum := sha256.Sum256(body)
	have := hex.EncodeToString(sum[:])
	if int64(len(body)) != packageInfo.Size || !strings.EqualFold(have, packageInfo.SHA256) {
		return servercharacters.DescribeMismatch(packageInfo.SHA256, packageInfo.Size, have, int64(len(body)))
	}
	if err := writeFileAtomically(path, body); err != nil {
		return fmt.Errorf("write %s: %w", packageInfo.Filename, err)
	}
	return nil
}
