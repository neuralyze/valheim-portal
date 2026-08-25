package app

import (
	"archive/zip"
	"bytes"
	"encoding/binary"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// realWorldMetadata is a byte-for-byte reconstruction of a live .fwl. The bytes are the
// ones read from Hrafnheim's save on the deployment host on 2026-08-25:
//
//	00000000: 2e00 0000 2500 0000 0948 7261 666e 6865  ....%....Hrafnhe
//	00000010: 696d 0a71 6d72 6265 6351 4932 4b37 4729  im.qmrbecQI2K7G)
//	00000020: 8de8 d2b5 87ff ffff ff02 0000 0001 0000  ................
//	00000030: 0000                                     ..
//
// 50 bytes: a 46-byte package holding world version 37, the length-prefixed name
// "Hrafnheim", the length-prefixed seed name "qmrbecQI2K", the seed, the UID, the
// generator version, and a five-byte tail. The name is inside the file, which is why
// provisioning rewrites it rather than only renaming the two files.
var realWorldMetadata = []byte{
	0x2e, 0x00, 0x00, 0x00, 0x25, 0x00, 0x00, 0x00, 0x09, 0x48, 0x72, 0x61, 0x66, 0x6e, 0x68, 0x65,
	0x69, 0x6d, 0x0a, 0x71, 0x6d, 0x72, 0x62, 0x65, 0x63, 0x51, 0x49, 0x32, 0x4b, 0x37, 0x47, 0x29,
	0x8d, 0xe8, 0xd2, 0xb5, 0x87, 0xff, 0xff, 0xff, 0xff, 0x02, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00,
	0x00, 0x00,
}

// metadataFor rebuilds a .fwl carrying a different world name, keeping every other field
// of the real file. Used to build an archive holding two distinct worlds.
func metadataFor(t *testing.T, name string) []byte {
	t.Helper()
	body := []byte{0x25, 0x00, 0x00, 0x00}
	body = append(body, byte(len(name)))
	body = append(body, name...)
	body = append(body, realWorldMetadata[0x12:]...)
	header := make([]byte, 4)
	binary.LittleEndian.PutUint32(header, uint32(len(body)))
	return append(header, body...)
}

type member struct {
	name string
	data []byte
	mode os.FileMode
}

func buildArchive(t *testing.T, members ...member) *zip.Reader {
	t.Helper()
	var buffer bytes.Buffer
	writer := zip.NewWriter(&buffer)
	for _, item := range members {
		header := &zip.FileHeader{Name: item.name, Method: zip.Deflate}
		if item.mode != 0 {
			header.SetMode(item.mode)
		}
		part, err := writer.CreateHeader(header)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := part.Write(item.data); err != nil {
			t.Fatal(err)
		}
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	reader, err := zip.NewReader(bytes.NewReader(buffer.Bytes()), int64(buffer.Len()))
	if err != nil {
		t.Fatal(err)
	}
	return reader
}

// database stands in for a world save: incompressible so its declared ratio stays in the
// range a real Valheim database occupies, rather than accidentally tripping the bomb check.
func database(size int) []byte {
	out := make([]byte, size)
	for i := range out {
		out[i] = byte(i * 7 % 251)
	}
	return out
}

// Control for every refusal below: the archive a player actually produces, containing the
// live pair alongside the .old and _backup_auto- copies Valheim writes beside it, is
// accepted, and the automatic backup is never mistaken for the live save.
func TestWorldUploadAcceptsAPlayersSaveFolder(t *testing.T) {
	archive := buildArchive(t,
		member{name: "worlds_local/Hrafnheim.db", data: database(4096)},
		member{name: "worlds_local/Hrafnheim.fwl", data: realWorldMetadata},
		member{name: "worlds_local/Hrafnheim.db.old", data: database(2048)},
		member{name: "worlds_local/Hrafnheim.fwl.old", data: realWorldMetadata},
		member{name: "worlds_local/Hrafnheim_backup_auto-20260825.120000.db", data: database(1024)},
		member{name: "worlds_local/Hrafnheim_backup_auto-20260825.120000.fwl", data: realWorldMetadata},
	)
	dir := t.TempDir()
	staged, err := stageWorldUpload(archive, filepath.Join(dir, "staged"))
	if err != nil {
		t.Fatalf("a real save folder was refused: %v", err)
	}
	if staged.UploadedWorld != "Hrafnheim" || staged.Seed != "qmrbecQI2K" {
		t.Fatalf("staged = %+v, want the live Hrafnheim pair", staged)
	}
	if staged.DatabaseBytes != 4096 {
		t.Fatalf("placed a %d byte database; the live .db is 4096 and the automatic backup is 1024",
			staged.DatabaseBytes)
	}
	if len(staged.Ignored) != 4 {
		t.Fatalf("ignored %v, want the four .old and _backup_auto- members", staged.Ignored)
	}
	placed, err := os.ReadFile(filepath.Join(dir, "staged", stagedWorldMetadataName))
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(placed, realWorldMetadata) {
		t.Fatal("the staged .fwl is not byte-identical to the uploaded one")
	}
}

func TestWorldUploadRefusals(t *testing.T) {
	live := []member{
		{name: "Hrafnheim.db", data: database(4096)},
		{name: "Hrafnheim.fwl", data: realWorldMetadata},
	}
	for _, testCase := range []struct {
		name    string
		members []member
		want    string
	}{
		{
			name:    "path traversal",
			members: append([]member{{name: "../../etc/Hrafnheim.db", data: database(16)}}, live...),
			want:    `unsafe path "../../etc/Hrafnheim.db"`,
		},
		{
			name:    "absolute path",
			members: append([]member{{name: "/etc/cron.d/payload", data: []byte("x")}}, live...),
			want:    `unsafe path "/etc/cron.d/payload"`,
		},
		{
			// Refused even though this member would have been ignored: a symlink is
			// evidence about the archive, not about the one file beside it.
			name:    "symbolic link",
			members: append([]member{{name: "notes.txt", data: []byte("../../root"), mode: os.ModeSymlink | 0o777}}, live...),
			want:    "symbolic link",
		},
		{
			name:    "database with no world metadata",
			members: []member{{name: "Hrafnheim.db", data: database(4096)}},
			want:    "Hrafnheim.db but no Hrafnheim.fwl",
		},
		{
			name:    "world metadata with no database",
			members: []member{{name: "Hrafnheim.fwl", data: realWorldMetadata}},
			want:    "Hrafnheim.fwl but no Hrafnheim.db",
		},
		{
			name: "two different worlds",
			members: append([]member{
				{name: "Doggerland.db", data: database(2048)},
				{name: "Doggerland.fwl", data: metadataFor(t, "Doggerland")},
			}, live...),
			want: "holds 2 worlds (Doggerland, Hrafnheim)",
		},
		{
			name: "only an automatic backup pair",
			members: []member{
				{name: "Hrafnheim_backup_auto-20260825.120000.db", data: database(4096)},
				{name: "Hrafnheim_backup_auto-20260825.120000.fwl", data: realWorldMetadata},
				{name: "Hrafnheim.db.old", data: database(2048)},
				{name: "Hrafnheim.fwl.old", data: realWorldMetadata},
			},
			want: "only Valheim's own backup and .old copies",
		},
		{
			// An 8 MiB run of zeroes deflates to a few kilobytes. The declared
			// expansion, not the archive's own size, is what gives a zip bomb away.
			name: "member whose declared expansion is a bomb",
			members: append([]member{
				{name: "filler.bin", data: bytes.Repeat([]byte{0}, 8<<20)},
			}, live...),
			want: "expands 1027 times, over the 200 to 1 limit",
		},
		{
			name: "duplicate paths in different directories",
			members: append([]member{
				{name: "backup/Hrafnheim.db", data: database(512)},
			}, live...),
			want: `more than one "Hrafnheim.db"`,
		},
		{
			name: "world metadata that is not a .fwl",
			members: []member{
				{name: "Hrafnheim.db", data: database(4096)},
				{name: "Hrafnheim.fwl", data: bytes.Repeat([]byte("not a world"), 8)},
			},
			want: "not a Valheim .fwl",
		},
	} {
		t.Run(testCase.name, func(t *testing.T) {
			dir := filepath.Join(t.TempDir(), "staged")
			_, err := stageWorldUpload(buildArchive(t, testCase.members...), dir)
			if err == nil {
				t.Fatal("archive was accepted")
			}
			if !strings.Contains(err.Error(), testCase.want) {
				t.Fatalf("error %q does not contain %q; the operator is shown this text", err, testCase.want)
			}
			if _, statErr := os.Stat(dir); statErr == nil {
				t.Fatal("a refused archive left files staged on disk")
			}
		})
	}
}

// A zip directory may declare any size it likes. The copy is what has to be bounded, so a
// member whose real content exceeds its declared length must not be written whole.
func TestWorldUploadRejectsMemberLargerThanItsDeclaredMetadataSize(t *testing.T) {
	oversize := append([]byte{}, realWorldMetadata...)
	oversize = append(oversize, bytes.Repeat([]byte{0}, int(maxWorldMetadataBytes))...)
	archive := buildArchive(t,
		member{name: "Hrafnheim.db", data: database(4096)},
		member{name: "Hrafnheim.fwl", data: oversize},
	)
	dir := filepath.Join(t.TempDir(), "staged")
	if _, err := stageWorldUpload(archive, dir); err == nil {
		t.Fatal("an oversize .fwl was accepted")
	} else if !strings.Contains(err.Error(), "world metadata") {
		t.Fatalf("error %q does not name the metadata file", err)
	}
}

// The parse is what decides whether an uploaded .fwl is a world at all, so it is checked
// against the real bytes rather than against a fixture built by the same code.
func TestParseWorldMetadataReadsTheRealFile(t *testing.T) {
	parsed, err := parseWorldMetadata(realWorldMetadata)
	if err != nil {
		t.Fatal(err)
	}
	if parsed.Name != "Hrafnheim" || parsed.Seed != "qmrbecQI2K" {
		t.Fatalf("parsed = %+v, want name Hrafnheim and seed qmrbecQI2K", parsed)
	}
	// Control: the same bytes with one byte removed are no longer a world file, so the
	// check above is doing work rather than accepting anything handed to it.
	if _, err := parseWorldMetadata(realWorldMetadata[:len(realWorldMetadata)-1]); err == nil {
		t.Fatal("a truncated .fwl parsed as a world")
	}
}
