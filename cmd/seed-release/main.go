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

func main() {
	var database, artifactRoot, world, profile, clientType, version, payload, runtime, companion, diagPlugin, actor, joinAddress, serverVersion, archiveDraft, archiveRelease, notes string
	flag.StringVar(&database, "database", "/var/lib/valheim-portal/portal.sqlite", "absolute SQLite database path")
	flag.StringVar(&artifactRoot, "artifact-root", "/var/lib/valheim-portal/artifacts", "absolute immutable artifact root")
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
	release := app.Release{ID: id, World: world, Profile: profile, ClientType: clientType, Version: version, Notes: notes}
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
	name := filepath.Base(source)
	dir := filepath.Join(root, "releases", releaseID)
	if err := os.MkdirAll(dir, 0o750); err != nil {
		return app.Artifact{}, err
	}
	dest := filepath.Join(dir, kind+"-"+name)
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
