package app

import (
	"archive/zip"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"os"
	"path"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

// A world upload is the one admin payload that is measured in hundreds of megabytes and
// cannot travel over the agent socket: internal/agent caps a JSON operation payload at
// 32 MiB, and Hrafnheim's live database is already 4,014,941 bytes with nothing stopping a
// long-running world from reaching two orders of magnitude more. The archive therefore
// lands in a spool directory both halves can see, and the agent is handed a short opaque
// id instead of any bytes.
const (
	// The whole request body. Same ceiling as the release-artifact route, which is what
	// compose.yaml sizes the portal's 640 MiB /tmp tmpfs for; a multipart spill of one
	// upload has to fit there.
	maxWorldUploadBodyBytes = int64(512 << 20)
	// One member, decompressed. The database is normally the entire archive, so this is
	// deliberately the same number as the body ceiling rather than a fraction of it.
	maxWorldUploadMemberBytes = uint64(512 << 20)
	// Every member together, decompressed.
	maxWorldUploadTotalBytes = uint64(1 << 30)
	// A .fwl is world metadata, never bulk: the four live worlds on this host are 48 to 51
	// bytes. Anything approaching this is not the file it claims to be.
	maxWorldMetadataBytes = uint64(64 << 10)
	// Decompressed-to-compressed ratio a single member may declare. A Valheim database is
	// mostly zone data and deflates around 3:1; 200:1 is far outside that and is the shape
	// of a zip bomb. Applied only above the floor below, because a 50-byte .fwl that
	// deflates to 40 bytes has a meaningless ratio.
	maxWorldUploadRatio     = uint64(200)
	worldUploadRatioFloor   = uint64(1 << 20)
	stagedWorldDatabaseName = "world.db"
	stagedWorldMetadataName = "world.fwl"
)

// worldSaveUpload is one validated archive: exactly one live save pair, and the metadata
// read out of its .fwl so the review page can show the operator what they actually
// uploaded rather than what they believe they uploaded.
type worldSaveUpload struct {
	// UploadedWorld is the name the archive's own file names carry, which is generally
	// not the name of the server being created.
	UploadedWorld string
	Seed          string
	DatabaseBytes int64
	MetadataBytes int64
	// Ignored names the game-written files skipped on the way in, so the review page can
	// say they were seen and not used instead of leaving the operator to wonder.
	Ignored []string
}

// worldUploadArchivePath rejects anything that could escape the destination directory
// before the name is used for anything at all.
func worldUploadArchivePath(value string) (string, error) {
	if value == "" || strings.ContainsAny(value, "\\\x00") || strings.HasPrefix(value, "/") || strings.Contains(value, ":") {
		return "", fmt.Errorf("world archive contains an unsafe path %q", value)
	}
	trimmed := strings.TrimSuffix(value, "/")
	if trimmed == "" || path.Clean(trimmed) != trimmed || trimmed == ".." || strings.HasPrefix(trimmed, "../") {
		return "", fmt.Errorf("world archive contains an unsafe path %q", value)
	}
	return trimmed, nil
}

// gameWrittenSave reports whether a save file name is one Valheim wrote beside the live
// pair. A player who zips their worlds_local folder ships all of them, so these are
// ignored rather than refused - but they are never allowed to stand in for the live pair,
// which is why the classification happens here and not by trimming an extension.
func gameWrittenSave(base string) bool {
	lower := strings.ToLower(base)
	if strings.HasSuffix(lower, ".old") {
		return true
	}
	// "<Name>_backup_auto-<timestamp>.db". Trimming ".db" from that yields a stem that
	// looks like a perfectly valid, and entirely wrong, world name.
	return strings.Contains(lower, "_backup_auto-")
}

// saveStem returns the world name a live save file name carries, and whether the name is
// a live save file at all.
func saveStem(base string) (string, string, bool) {
	lower := strings.ToLower(base)
	switch {
	case gameWrittenSave(base):
		return "", "", false
	case strings.HasSuffix(lower, ".db"):
		return base[:len(base)-len(".db")], "db", true
	case strings.HasSuffix(lower, ".fwl"):
		return base[:len(base)-len(".fwl")], "fwl", true
	}
	return "", "", false
}

// inspectWorldUpload validates every member and resolves the single live save pair. It
// reads no member content: the archive directory is enough to refuse a hostile or
// ambiguous upload, and refusing before the first byte is decompressed is the point.
func inspectWorldUpload(archive *zip.Reader) (*zip.File, *zip.File, []string, error) {
	seen := make(map[string]struct{}, len(archive.File))
	databases := map[string]*zip.File{}
	metadata := map[string]*zip.File{}
	var ignored []string
	var total uint64
	for _, file := range archive.File {
		name, err := worldUploadArchivePath(file.Name)
		if err != nil {
			return nil, nil, nil, err
		}
		key := strings.ToLower(name)
		if _, exists := seen[key]; exists {
			return nil, nil, nil, fmt.Errorf("world archive contains duplicate paths (%q)", name)
		}
		seen[key] = struct{}{}
		// Checked even for members that will be ignored: a symlink or a bomb is evidence
		// about the archive, not about the one file it happens to sit next to.
		if file.Mode()&os.ModeSymlink != 0 {
			return nil, nil, nil, fmt.Errorf("world archive contains a symbolic link (%q)", name)
		}
		if file.FileInfo().IsDir() {
			continue
		}
		if file.UncompressedSize64 > maxWorldUploadMemberBytes {
			return nil, nil, nil, fmt.Errorf("world archive member %q declares %d bytes, over the %d byte limit for one file",
				name, file.UncompressedSize64, maxWorldUploadMemberBytes)
		}
		if total > maxWorldUploadTotalBytes-file.UncompressedSize64 {
			return nil, nil, nil, fmt.Errorf("world archive declares more than the %d byte total limit", maxWorldUploadTotalBytes)
		}
		total += file.UncompressedSize64
		if file.UncompressedSize64 >= worldUploadRatioFloor && file.CompressedSize64 > 0 &&
			file.UncompressedSize64/file.CompressedSize64 > maxWorldUploadRatio {
			return nil, nil, nil, fmt.Errorf("world archive member %q expands %d times, over the %d to 1 limit",
				name, file.UncompressedSize64/file.CompressedSize64, maxWorldUploadRatio)
		}
		base := path.Base(name)
		stem, kind, ok := saveStem(base)
		if !ok || !validWorld(stem) {
			ignored = append(ignored, name)
			continue
		}
		// Two directories carrying the same base name are ambiguous in a flat
		// worlds_local, so they are a refusal rather than a silent pick.
		target := databases
		if kind == "fwl" {
			target = metadata
		}
		if _, clash := target[stem]; clash {
			return nil, nil, nil, fmt.Errorf("world archive contains more than one %q", base)
		}
		target[stem] = file
	}
	var paired []string
	for stem := range databases {
		if _, ok := metadata[stem]; ok {
			paired = append(paired, stem)
		}
	}
	sort.Strings(paired)
	switch len(paired) {
	case 1:
		return databases[paired[0]], metadata[paired[0]], ignored, nil
	case 0:
		return nil, nil, nil, unpairedWorldUpload(databases, metadata, ignored)
	default:
		return nil, nil, nil, fmt.Errorf("world archive holds %d worlds (%s); upload one world at a time",
			len(paired), strings.Join(paired, ", "))
	}
}

// unpairedWorldUpload explains the absence rather than reporting a bare failure: the three
// ways an archive arrives without a live pair need three different fixes from the operator.
func unpairedWorldUpload(databases, metadata map[string]*zip.File, ignored []string) error {
	if len(databases) == 1 && len(metadata) == 0 {
		for stem := range databases {
			return fmt.Errorf("world archive has %s.db but no %s.fwl; a database without its world metadata is not a world", stem, stem)
		}
	}
	if len(metadata) == 1 && len(databases) == 0 {
		for stem := range metadata {
			return fmt.Errorf("world archive has %s.fwl but no %s.db; the world metadata alone carries no map", stem, stem)
		}
	}
	if len(databases) == 0 && len(metadata) == 0 && len(ignored) > 0 {
		return errors.New("world archive contains only Valheim's own backup and .old copies, which are never used as the live save; export the current " +
			"<world>.db and <world>.fwl pair instead")
	}
	names := append(mapKeys(databases), mapKeys(metadata)...)
	sort.Strings(names)
	if len(names) == 0 {
		return errors.New("world archive contains no Valheim save files")
	}
	return fmt.Errorf("world archive has no matching .db and .fwl pair (saw %s)", strings.Join(uniqueStrings(names), ", "))
}

func mapKeys(files map[string]*zip.File) []string {
	names := make([]string, 0, len(files))
	for _, file := range files {
		names = append(names, path.Base(file.Name))
	}
	return names
}

func uniqueStrings(values []string) []string {
	out := values[:0:0]
	for index, value := range values {
		if index == 0 || value != values[index-1] {
			out = append(out, value)
		}
	}
	return out
}

// worldMetadata is the subset of a .fwl this portal reads. The authoritative writer stays
// in tools/valheim_world.py, which provisioning already uses to rewrite the world name;
// this is a read so the upload can be refused at the door instead of failing inside the
// privileged half, and so the review page can name the world actually being uploaded.
type worldMetadata struct {
	Name string
	Seed string
}

func parseWorldMetadata(raw []byte) (worldMetadata, error) {
	if len(raw) < 30 || uint64(len(raw)) > maxWorldMetadataBytes {
		return worldMetadata{}, errors.New("world metadata file is not a Valheim .fwl")
	}
	declared := int32(binary.LittleEndian.Uint32(raw[0:4]))
	version := int32(binary.LittleEndian.Uint32(raw[4:8]))
	if int(declared) != len(raw)-4 || version < 20 || version > 100 {
		return worldMetadata{}, errors.New("world metadata header is not a Valheim .fwl")
	}
	name, offset, err := readMetadataString(raw, 8)
	if err != nil {
		return worldMetadata{}, err
	}
	seed, offset, err := readMetadataString(raw, offset)
	if err != nil {
		return worldMetadata{}, err
	}
	// int32 seed value, int64 uid, int32 world-generator version.
	if offset+16 > len(raw) {
		return worldMetadata{}, errors.New("world metadata file is truncated")
	}
	generator := int32(binary.LittleEndian.Uint32(raw[offset+12 : offset+16]))
	if name == "" || len(name) > 80 || seed == "" || len(seed) > 80 || generator < 1 || generator > 20 {
		return worldMetadata{}, errors.New("world metadata file does not describe a Valheim world")
	}
	return worldMetadata{Name: name, Seed: seed}, nil
}

// readMetadataString reads Valheim's 7-bit length-prefixed string encoding.
func readMetadataString(raw []byte, offset int) (string, int, error) {
	length := 0
	shift := 0
	for range 5 {
		if offset >= len(raw) {
			return "", 0, errors.New("world metadata file is truncated")
		}
		b := raw[offset]
		offset++
		length |= int(b&0x7f) << shift
		if b&0x80 == 0 {
			if length > 1024 || offset+length > len(raw) {
				return "", 0, errors.New("world metadata file is truncated")
			}
			return string(raw[offset : offset+length]), offset + length, nil
		}
		shift += 7
	}
	return "", 0, errors.New("world metadata file is not a Valheim .fwl")
}

// stageWorldUpload validates the archive and writes the live pair into dir under fixed
// names. Nothing is decompressed until inspectWorldUpload has accepted every member, and
// each member is copied through a bounded reader so a lying archive directory cannot
// write more than it declared.
func stageWorldUpload(archive *zip.Reader, dir string) (worldSaveUpload, error) {
	database, metadata, ignored, err := inspectWorldUpload(archive)
	if err != nil {
		return worldSaveUpload{}, err
	}
	if metadata.UncompressedSize64 > maxWorldMetadataBytes {
		return worldSaveUpload{}, fmt.Errorf("world metadata %q declares %d bytes; a .fwl is world metadata, never bulk",
			path.Base(metadata.Name), metadata.UncompressedSize64)
	}
	raw, err := readArchiveMember(metadata, int64(maxWorldMetadataBytes))
	if err != nil {
		return worldSaveUpload{}, err
	}
	parsed, err := parseWorldMetadata(raw)
	if err != nil {
		return worldSaveUpload{}, err
	}
	if err := os.MkdirAll(dir, 0o750); err != nil {
		return worldSaveUpload{}, err
	}
	databaseBytes, err := copyArchiveMember(database, filepath.Join(dir, stagedWorldDatabaseName))
	if err != nil {
		return worldSaveUpload{}, err
	}
	if err := os.WriteFile(filepath.Join(dir, stagedWorldMetadataName), raw, 0o640); err != nil {
		return worldSaveUpload{}, err
	}
	stem, _, _ := saveStem(path.Base(database.Name))
	return worldSaveUpload{
		UploadedWorld: stem, Seed: parsed.Seed,
		DatabaseBytes: databaseBytes, MetadataBytes: int64(len(raw)), Ignored: ignored,
	}, nil
}

func readArchiveMember(file *zip.File, limit int64) ([]byte, error) {
	input, err := file.Open()
	if err != nil {
		return nil, err
	}
	defer input.Close()
	raw, err := io.ReadAll(io.LimitReader(input, limit+1))
	if err != nil {
		return nil, err
	}
	if int64(len(raw)) > limit {
		return nil, fmt.Errorf("world archive member %q is larger than its directory entry declared", path.Base(file.Name))
	}
	return raw, nil
}

// copyArchiveMember streams one member to disk. The database is the reason this route
// exists at all, so it is never held in memory.
func copyArchiveMember(file *zip.File, destination string) (int64, error) {
	input, err := file.Open()
	if err != nil {
		return 0, err
	}
	defer input.Close()
	output, err := os.OpenFile(destination, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o640)
	if err != nil {
		return 0, err
	}
	written, copyErr := io.Copy(output, io.LimitReader(input, int64(maxWorldUploadMemberBytes)+1))
	closeErr := output.Close()
	if copyErr != nil {
		os.Remove(destination)
		return 0, copyErr
	}
	if written > int64(maxWorldUploadMemberBytes) {
		os.Remove(destination)
		return 0, fmt.Errorf("world archive member %q is larger than the %d byte limit for one file",
			path.Base(file.Name), maxWorldUploadMemberBytes)
	}
	if closeErr != nil {
		os.Remove(destination)
		return 0, closeErr
	}
	return written, nil
}

// worldUploadStaleAfter bounds how long an unclaimed staging directory survives. A review
// token expires after ten minutes and a provisioning run is capped at twenty, so anything
// this old was abandoned rather than in flight.
const worldUploadStaleAfter = 2 * time.Hour

// stageUploadedWorld validates the archive and writes the live pair into a fresh directory
// under the shared spool, returning the staging id the agent will be handed.
//
// The archive is read through the multipart spill file itself: net/http has already put
// anything over maxUploadMemoryBytes on disk, and archive/zip only needs an io.ReaderAt,
// so the upload is never held whole in memory and never copied a second time.
func (s *Server) stageUploadedWorld(file io.ReaderAt, size int64) (string, worldSaveUpload, error) {
	if size <= 0 || size > maxWorldUploadBodyBytes {
		return "", worldSaveUpload{}, fmt.Errorf("a world archive must be between 1 byte and %d bytes", maxWorldUploadBodyBytes)
	}
	archive, err := zip.NewReader(file, size)
	if err != nil {
		return "", worldSaveUpload{}, errors.New("the uploaded file is not a readable .zip archive")
	}
	s.sweepWorldUploads()
	id := randomID()
	dir := filepath.Join(s.cfg.WorldUploadRoot, id)
	staged, err := stageWorldUpload(archive, dir)
	if err != nil {
		os.RemoveAll(dir)
		return "", worldSaveUpload{}, err
	}
	return id, staged, nil
}

// sweepWorldUploads removes staging directories nobody claimed. Provisioning removes the
// one it consumes; this is for the review that was prepared and then abandoned, which
// would otherwise leave a multi-hundred-megabyte save in the spool forever.
func (s *Server) sweepWorldUploads() {
	entries, err := os.ReadDir(s.cfg.WorldUploadRoot)
	if err != nil {
		return
	}
	for _, entry := range entries {
		info, err := entry.Info()
		if err != nil || !entry.IsDir() || time.Since(info.ModTime()) < worldUploadStaleAfter {
			continue
		}
		os.RemoveAll(filepath.Join(s.cfg.WorldUploadRoot, entry.Name()))
	}
}

// discardWorldUpload drops a staged pair the operator will never confirm.
func (s *Server) discardWorldUpload(id string) {
	if id == "" {
		return
	}
	os.RemoveAll(filepath.Join(s.cfg.WorldUploadRoot, id))
}
