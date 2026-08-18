package main

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"

	"github.com/neuralyze/valheim-portal/internal/app"
)

// The paths the deployed portal reads. Both are the flag defaults, and the artifact root is
// additionally enforced below, because a release whose artifacts were staged anywhere else
// records a path the portal cannot resolve from inside its container.
const (
	deployedArtifactRoot = "/var/lib/valheim-portal/artifacts"
	deployedDatabase     = "/var/lib/valheim-portal/portal.sqlite"
)

func main() {
	var database, artifactRoot, world, profile, clientType, version, payload, runtime, companion, diagPlugin, actor, joinAddress, serverVersion, archiveDraft, archiveRelease, notes, audience string
	var allowForeignRoot, skipDownloadCheck bool
	flag.StringVar(&database, "database", deployedDatabase, "absolute SQLite database path")
	flag.StringVar(&artifactRoot, "artifact-root", deployedArtifactRoot, "absolute immutable artifact root")
	flag.BoolVar(&allowForeignRoot, "allow-foreign-artifact-root", false,
		"stage artifacts outside the deployed root; only correct when that prefix is mounted at the deployed path")
	flag.BoolVar(&skipDownloadCheck, "skip-download-check", false,
		"do not verify the published payload is readable at its recorded path")
	flag.StringVar(&world, "world", "", "world name (required when publishing)")
	flag.StringVar(&profile, "profile", "", "player profile identifier")
	flag.StringVar(&clientType, "client-type", "", "client type: vr or flat")
	flag.StringVar(&version, "version", "", "profile release version")
	flag.StringVar(&payload, "profile-payload", "", "absolute immutable profile-definition ZIP path")
	flag.StringVar(&runtime, "vr-runtime", "", "absolute immutable VR runtime ZIP path")
	flag.StringVar(&companion, "flat-companion", "", "absolute immutable Flat ValheimVR companion ZIP path")
	flag.StringVar(&diagPlugin, "diag-plugin", "", "absolute immutable diagnostics plugin ZIP path")
	flag.StringVar(&actor, "actor", "release-seed", "audit actor")
	flag.StringVar(&joinAddress, "join-address", "", "public join hostname and port")
	flag.StringVar(&serverVersion, "server-version", "", "compatible Valheim server version")
	flag.StringVar(&archiveDraft, "archive-draft", "", "draft release ID to archive instead of publishing")
	flag.StringVar(&archiveRelease, "archive-release", "", "published release ID to archive")
	flag.StringVar(&audience, "audience", "", "who the edition is for (player or admin); required when publishing")
	flag.StringVar(&notes, "notes", "Incremental Valheim Profile Sync definition.", "release notes")
	flag.Parse()
	if archiveDraft != "" && archiveRelease != "" {
		fatal("only one archive mode may be selected")
	}
	if archiveDraft == "" && archiveRelease == "" && (world == "" || payload == "" || profile == "" || version == "") {
		flag.Usage()
		os.Exit(2)
	}
	if archiveDraft == "" && archiveRelease == "" {
		if clientType != "vr" && clientType != "flat" {
			fatal("client-type must be vr or flat")
		}
		if clientType == "vr" && runtime == "" {
			fatal("VR releases require --vr-runtime")
		}
		if clientType == "flat" && runtime != "" {
			fatal("flat releases cannot include --vr-runtime")
		}
		if clientType == "vr" && companion != "" {
			fatal("VR releases cannot include --flat-companion")
		}
	}
	if !filepath.IsAbs(database) || !filepath.IsAbs(artifactRoot) {
		fatal("database and artifact-root must be absolute")
	}
	// The recorded artifact path is read back by the PORTAL, which serves the payload from
	// inside its container, so the only usable root is the deployed one. Passing the host
	// path of the same bytes -- the docker volume, say -- stages the files correctly, records
	// a path the portal cannot resolve, and every download 404s while the release row reads
	// `published`. That happened on 2026-08-06 to all ten profiles at once. Refuse it here
	// rather than leave it to whoever is reading the client's error message later.
	if artifactRoot != deployedArtifactRoot && !allowForeignRoot {
		fatal(fmt.Sprintf(
			"artifact-root %s is not the deployed root %s: the portal resolves recorded paths from "+
				"inside its container, so a release staged elsewhere serves 404 to every client. "+
				"Omit -artifact-root, or pass -allow-foreign-artifact-root if this is a staging prefix "+
				"whose contents are mounted at the deployed path.",
			artifactRoot, deployedArtifactRoot))
	}
	store, err := app.OpenStore(database)
	if err != nil {
		fatal(err.Error())
	}
	defer store.Close()
	if archiveRelease != "" {
		if err := store.Archive(context.Background(), archiveRelease, actor); err != nil {
			fatal(err.Error())
		}
		fmt.Printf("archived published release %s\n", archiveRelease)
		return
	}
	if archiveDraft != "" {
		if err := store.ArchiveDraft(context.Background(), archiveDraft, actor); err != nil {
			fatal(err.Error())
		}
		fmt.Printf("archived draft %s\n", archiveDraft)
		return
	}
	id := profile + "-" + version
	ctx := context.Background()
	if _, err := store.PublicWorld(ctx, world); errors.Is(err, sql.ErrNoRows) {
		if joinAddress == "" || serverVersion == "" {
			fatal("join-address and server-version are required for an unregistered world")
		}
		if err := store.UpsertPublicWorld(ctx, app.PublicWorld{Name: world, JoinAddress: joinAddress, Status: "offline", ServerVersion: serverVersion}, actor); err != nil {
			fatal(err.Error())
		}
	} else if err != nil {
		fatal(err.Error())
	}
	// Recorded on the release, never in the published definition: an installed client
	// decodes profile-manifest.json with DisallowUnknownFields, so a portal-only field
	// there fails every install.
	if audience != "player" && audience != "admin" {
		fatal("audience must be player or admin")
	}
	release := app.Release{ID: id, World: world, Profile: profile, ClientType: clientType, Version: version, Notes: notes, Audience: audience}
	existing, err := store.Release(ctx, id)
	switch {
	case errors.Is(err, sql.ErrNoRows):
		if err := store.CreateRelease(ctx, release, actor); err != nil {
			fatal(err.Error())
		}
	case err != nil:
		fatal(err.Error())
	case existing.World != release.World || existing.Profile != release.Profile || existing.ClientType != release.ClientType || existing.Version != release.Version || (existing.Status != app.Draft && existing.Status != app.Published):
		fatal("existing release does not match the requested release")
	}
	attached, err := store.Artifacts(ctx, id)
	if err != nil {
		fatal(err.Error())
	}
	attachedByKind := make(map[string]app.Artifact, len(attached))
	for _, artifact := range attached {
		attachedByKind[artifact.Kind] = artifact
	}
	sources := []struct{ kind, path string }{{"profile", payload}}
	if runtime != "" {
		sources = append(sources, struct{ kind, path string }{"vr_runtime", runtime})
	}
	if companion != "" {
		sources = append(sources, struct{ kind, path string }{"flat_companion", companion})
	}
	if diagPlugin != "" {
		sources = append(sources, struct{ kind, path string }{"diag_plugin", diagPlugin})
	}
	for _, source := range sources {
		if artifact, ok := attachedByKind[source.kind]; ok {
			if err := verifySource(artifact, source.path); err != nil {
				fatal(err.Error())
			}
			continue
		}
		artifact, err := stage(artifactRoot, id, source.kind, source.path)
		if err != nil {
			fatal(err.Error())
		}
		artifact.ReleaseID = id
		if err := store.AddArtifact(ctx, artifact, actor); err != nil {
			fatal(err.Error())
		}
	}
	if existing.Status == app.Published {
		fmt.Printf("already published %s (%s)\n", id, version)
		return
	}
	if err := store.Publish(ctx, id, actor); err != nil {
		fatal(err.Error())
	}
	// "Published" has to mean a player can fetch it. A row reading `published` beside artifacts
	// the portal cannot open is the failure this check exists for: on 2026-08-06 ten profiles
	// published cleanly and every client download returned 404, because the recorded paths were
	// host paths. Read each artifact back through its RECORDED path and compare the digest the
	// row claims, which is exactly what the portal does when serving it.
	if !skipDownloadCheck {
		if err := verifyRecordedArtifacts(ctx, store, id); err != nil {
			fatal(fmt.Sprintf("published %s but its artifacts are not readable as recorded: %v", id, err))
		}
	}
	fmt.Printf("published %s (%s)\n", id, version)
}

func stage(root, releaseID, kind, source string) (app.Artifact, error) {
	if !filepath.IsAbs(source) {
		return app.Artifact{}, fmt.Errorf("%s source must be absolute", kind)
	}
	in, err := os.Open(source)
	if err != nil {
		return app.Artifact{}, err
	}
	defer in.Close()
	info, err := in.Stat()
	if err != nil || !info.Mode().IsRegular() {
		return app.Artifact{}, fmt.Errorf("%s is not a regular file", source)
	}
	// Shared with the profile-definition builder, which records the companion's filename
	// into the definition the store cross-checks against this row: both sides must reach
	// the same answer for a file carried forward from a previous release.
	name := app.StagedArtifactName(kind, source)
	prefix := kind + "-"
	dir := filepath.Join(root, "releases", releaseID)
	if err := os.MkdirAll(dir, 0o750); err != nil {
		return app.Artifact{}, err
	}
	dest := filepath.Join(dir, prefix+name)
	out, err := os.OpenFile(dest, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o640)
	if err != nil {
		return app.Artifact{}, err
	}
	h := sha256.New()
	n, copyErr := io.Copy(io.MultiWriter(out, h), in)
	closeErr := out.Close()
	if copyErr != nil || closeErr != nil {
		os.Remove(dest)
		if copyErr != nil {
			return app.Artifact{}, copyErr
		}
		return app.Artifact{}, closeErr
	}
	return app.Artifact{ID: kind + "-" + releaseID, Kind: kind, Name: name, Path: dest, Size: n, SHA256: hex.EncodeToString(h.Sum(nil))}, nil
}
func verifySource(artifact app.Artifact, source string) error {
	file, err := os.Open(source)
	if err != nil {
		return err
	}
	defer file.Close()
	hash := sha256.New()
	size, err := io.Copy(hash, file)
	if err != nil {
		return err
	}
	if size != artifact.Size || hex.EncodeToString(hash.Sum(nil)) != artifact.SHA256 {
		return fmt.Errorf("attached %s artifact does not match %s", artifact.Kind, source)
	}
	return nil
}

func fatal(message string) { fmt.Fprintln(os.Stderr, message); os.Exit(1) }

// verifyRecordedArtifacts reads every artifact of a published release back through the path and
// digest RECORDED IN THE DATABASE, which is what the portal does when a client asks for a payload.
// Staging succeeded and the row says published, so nothing before this point can tell the operator
// whether a download works; this can, and it costs one read per artifact.
func verifyRecordedArtifacts(ctx context.Context, store *app.Store, releaseID string) error {
	artifacts, err := store.PublishedArtifacts(ctx, releaseID)
	if err != nil {
		return err
	}
	if len(artifacts) == 0 {
		return fmt.Errorf("no artifacts recorded")
	}
	for _, artifact := range artifacts {
		file, err := os.Open(artifact.Path)
		if err != nil {
			return fmt.Errorf("%s at recorded path %s: %w", artifact.Kind, artifact.Path, err)
		}
		digest := sha256.New()
		n, copyErr := io.Copy(digest, file)
		file.Close()
		if copyErr != nil {
			return fmt.Errorf("%s at %s: %w", artifact.Kind, artifact.Path, copyErr)
		}
		if n != artifact.Size {
			return fmt.Errorf("%s at %s: recorded %d bytes, read %d", artifact.Kind, artifact.Path, artifact.Size, n)
		}
		if got := hex.EncodeToString(digest.Sum(nil)); got != artifact.SHA256 {
			return fmt.Errorf("%s at %s: recorded sha256 %s, read %s", artifact.Kind, artifact.Path, artifact.SHA256, got)
		}
	}
	return nil
}
