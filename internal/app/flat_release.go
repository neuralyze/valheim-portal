package app

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
)

// ValidateFlatReleaseSources validates the complete artifact relationship before
// any draft is created. The profile definition remains the authority for the
// companion filename, digest, and size.
func ValidateFlatReleaseSources(release Release, profilePath, companionPath string) error {
	if release.ClientType != "flat" {
		return errors.New("Flat source validation requires a Flat release")
	}
	var manifest ProfileManifest
	if err := validateProfileArtifactPayload(profilePath, release, &manifest); err != nil {
		return err
	}
	if manifest.Companion == nil {
		return errors.New("Flat profile definition must declare a companion artifact")
	}
	if err := ValidateFlatCompanionArtifact(companionPath); err != nil {
		return err
	}
	name, sum, size, err := artifactMetadata(companionPath)
	if err != nil {
		return err
	}
	if name != manifest.Companion.Filename || size != manifest.Companion.Size || !strings.EqualFold(sum, manifest.Companion.SHA256) {
		return errors.New("Flat companion artifact does not match the profile manifest")
	}
	return nil
}

func artifactMetadata(path string) (string, string, int64, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", "", 0, err
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return "", "", 0, err
	}
	if !info.Mode().IsRegular() || info.Size() < 1 {
		return "", "", 0, errors.New("artifact must be a non-empty regular file")
	}
	hash := sha256.New()
	if _, err := io.Copy(hash, file); err != nil {
		return "", "", 0, err
	}
	return filepath.Base(path), hex.EncodeToString(hash.Sum(nil)), info.Size(), nil
}
