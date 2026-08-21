package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The bytes here are the shape of a real file: a UTF-8 BOM (26 of 100 plugin
// manifests on this host carry one), CRLF on the managed setting's block and LF
// elsewhere - mixed endings inside one file, as in
// /media/big4/projects/game/valheim/Hrafnheim/config_merged/bepinex/
// ZenDragon.ZenBreeding.cfg - a key containing spaces ("Reset Crafting Value",
// from Azumatt.AzuAntiArthriticCrafting.cfg:10), and no final newline.
const mixedConfigDocument = "\ufeff## Settings file was created by plugin Example v1.0\n" +
	"\n" +
	"[General]\r\n" +
	"## If enabled, the crafting amount will be reset [Synced with Server]\r\n" +
	"# Setting type: Toggle\r\n" +
	"# Default value: On\r\n" +
	"# Acceptable values: Off, On\r\n" +
	"Reset Crafting Value = On\r\n" +
	"\n" +
	"[Advanced]\n" +
	"Threshold=4\n" +
	"Trailing = last"

func TestConfigDocumentRoundTripsByteForByte(t *testing.T) {
	document := parseConfigDocument([]byte(mixedConfigDocument))
	if got := string(document.bytes()); got != mixedConfigDocument {
		t.Fatalf("round trip changed the file:\n%q", got)
	}
}

func TestConfigDocumentReplacesOnlyTheValue(t *testing.T) {
	document := parseConfigDocument([]byte(mixedConfigDocument))
	changed, err := document.setValue("General", "Reset Crafting Value", "Off")
	if err != nil {
		t.Fatal(err)
	}
	if !changed {
		t.Fatal("setValue reported no change")
	}
	want := strings.Replace(mixedConfigDocument, "Reset Crafting Value = On\r\n", "Reset Crafting Value = Off\r\n", 1)
	if got := string(document.bytes()); got != want {
		t.Fatalf("edit touched more than the value:\n%q", got)
	}
}

// An unchanged value must not rewrite the file: a spurious rewrite would show up
// as a modified config on every sync and mask a real one.
func TestConfigDocumentReportsNoChangeForTheSameValue(t *testing.T) {
	document := parseConfigDocument([]byte(mixedConfigDocument))
	changed, err := document.setValue("General", "Reset Crafting Value", "On")
	if err != nil {
		t.Fatal(err)
	}
	if changed {
		t.Fatal("setValue rewrote an unchanged value")
	}
	if got := string(document.bytes()); got != mixedConfigDocument {
		t.Fatalf("no-op edit changed the file:\n%q", got)
	}
}

// Spacing around the "=" is the file's own, not ours. BepInEx writes "Key = Value"
// but a hand-edited file may not, and reformatting a line nobody asked about
// turns a one-value change into a whole-file diff.
func TestConfigDocumentKeepsExistingSpacing(t *testing.T) {
	document := parseConfigDocument([]byte(mixedConfigDocument))
	if _, err := document.setValue("Advanced", "Threshold", "9"); err != nil {
		t.Fatal(err)
	}
	if _, err := document.setValue("Advanced", "Trailing", "final"); err != nil {
		t.Fatal(err)
	}
	got := string(document.bytes())
	if !strings.Contains(got, "Threshold=9\n") {
		t.Fatalf("tight spacing was reformatted:\n%q", got)
	}
	if !strings.HasSuffix(got, "Trailing = final") {
		t.Fatalf("a final line without a terminator gained one:\n%q", got)
	}
}

func TestConfigDocumentReadsValuesBySection(t *testing.T) {
	document := parseConfigDocument([]byte(mixedConfigDocument))
	for _, testCase := range []struct {
		section, key, want string
		found              bool
	}{
		{"General", "Reset Crafting Value", "On", true},
		{"Advanced", "Threshold", "4", true},
		// Same key name, wrong section: a value read from the wrong section
		// would be compared against the wrong baseline entry.
		{"General", "Threshold", "", false},
		{"Missing", "Threshold", "", false},
		{"Advanced", "Absent", "", false},
	} {
		value, found := document.value(testCase.section, testCase.key)
		if value != testCase.want || found != testCase.found {
			t.Fatalf("value(%q,%q) = %q,%t want %q,%t", testCase.section, testCase.key, value, found, testCase.want, testCase.found)
		}
	}
}

// A comment line that happens to contain an "=" is documentation, not a setting.
// Editing one would silently corrupt the schema the extractor reads.
func TestConfigDocumentIgnoresCommentedSettings(t *testing.T) {
	source := "[General]\n## Threshold = 4 by default\n# Threshold = 7\nThreshold = 9\n"
	document := parseConfigDocument([]byte(source))
	if value, found := document.value("General", "Threshold"); value != "9" || !found {
		t.Fatalf("value = %q,%t", value, found)
	}
	if _, err := document.setValue("General", "Threshold", "12"); err != nil {
		t.Fatal(err)
	}
	if got := string(document.bytes()); got != strings.Replace(source, "Threshold = 9", "Threshold = 12", 1) {
		t.Fatalf("edited a comment:\n%q", got)
	}
}

// A value carrying a newline would inject a second setting into the file. It
// arrives over the network, so this is a safety check, not a tidiness one.
func TestConfigDocumentRejectsMultilineValues(t *testing.T) {
	document := parseConfigDocument([]byte(mixedConfigDocument))
	if _, err := document.setValue("Advanced", "Threshold", "9\nInjected = true"); err == nil {
		t.Fatal("accepted a value spanning two lines")
	}
	if got := string(document.bytes()); got != mixedConfigDocument {
		t.Fatalf("a rejected edit still changed the file:\n%q", got)
	}
}

func TestConfigDocumentWriteKeepsTheBOM(t *testing.T) {
	path := filepath.Join(t.TempDir(), "example.cfg")
	if err := os.WriteFile(path, []byte(mixedConfigDocument), 0o600); err != nil {
		t.Fatal(err)
	}
	document, found, err := readConfigDocument(path)
	if err != nil || !found {
		t.Fatalf("read = found:%t err:%v", found, err)
	}
	if !document.bom {
		t.Fatal("byte order mark was not detected")
	}
	if _, err := document.setValue("Advanced", "Threshold", "9"); err != nil {
		t.Fatal(err)
	}
	if err := document.write(path); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	want := strings.Replace(mixedConfigDocument, "Threshold=4", "Threshold=9", 1)
	if string(data) != want {
		t.Fatalf("written file =\n%q\nwant\n%q", data, want)
	}
}

// A missing file is the player who has never run this mod. It is the state that
// gets seeded, so it must not read as an error.
func TestReadConfigDocumentReportsAMissingFile(t *testing.T) {
	document, found, err := readConfigDocument(filepath.Join(t.TempDir(), "absent.cfg"))
	if err != nil || found || document != nil {
		t.Fatalf("read = %v found:%t err:%v", document, found, err)
	}
}
