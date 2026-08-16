package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The icon path has to stop moving. Shortcuts pointed at wherever the player saved the download, so
// replacing that file left every icon blank with nothing to repair it - the cost of dropping the
// self-copy that Defender flagged.
func TestTheIconIsWrittenToAStablePathAndRepairsItself(t *testing.T) {
	root := t.TempDir()
	t.Setenv("LOCALAPPDATA", root)

	first, err := stableIconPath()
	if err != nil {
		t.Fatalf("no stable icon: %v", err)
	}
	if !strings.HasSuffix(first, iconFileName) {
		t.Errorf("icon path %q does not name the icon", first)
	}
	body, err := os.ReadFile(first)
	if err != nil {
		t.Fatal(err)
	}
	// A real .ico begins with the 6-byte ICONDIR: reserved 0, type 1.
	if len(body) < 6 || body[0] != 0 || body[1] != 0 || body[2] != 1 || body[3] != 0 {
		t.Errorf("the written file is not an icon: % x", body[:min(6, len(body))])
	}

	// Deleting it must not be permanent: starting the application writes it back.
	if err := os.Remove(first); err != nil {
		t.Fatal(err)
	}
	second, err := stableIconPath()
	if err != nil || second != first {
		t.Fatalf("the icon did not repair itself: %q, %v", second, err)
	}
	if _, err := os.Stat(second); err != nil {
		t.Errorf("icon missing after repair: %v", err)
	}
}

// A Desktop full of blank icons must heal by running the app, and nothing else on the Desktop may be
// touched - not a shortcut to something else, not a file that merely ends in .url.
func TestRepairRewritesOurShortcutsAndLeavesOthersAlone(t *testing.T) {
	desktop := t.TempDir()
	ours := filepath.Join(desktop, "redesign-alpha.url")
	stale := "[InternetShortcut]\r\nURL=" + protocolScheme + "://sync?world=Hrafnheim\r\nIconFile=C:\\Users\\a\\Downloads\\gone.exe\r\nIconIndex=0\r\n"
	if err := os.WriteFile(ours, []byte(stale), 0o600); err != nil {
		t.Fatal(err)
	}
	foreign := filepath.Join(desktop, "somebody-elses.url")
	foreignBody := "[InternetShortcut]\r\nURL=https://example.test\r\nIconFile=C:\\other.exe\r\nIconIndex=3\r\n"
	if err := os.WriteFile(foreign, []byte(foreignBody), 0o600); err != nil {
		t.Fatal(err)
	}

	repaired, err := repairShortcutIcons(desktop, `C:\icon\ValheimProfileSync.ico`)
	if err != nil {
		t.Fatal(err)
	}
	if repaired != 1 {
		t.Fatalf("repaired %d shortcuts, want exactly ours", repaired)
	}
	updated, err := os.ReadFile(ours)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(updated), `IconFile=C:\icon\ValheimProfileSync.ico`) {
		t.Errorf("our shortcut still points at the old icon: %q", updated)
	}
	if !strings.Contains(string(updated), "URL="+protocolScheme+"://sync?world=Hrafnheim") {
		t.Error("the repair damaged the URL, which is the part that actually launches the profile")
	}
	untouched, err := os.ReadFile(foreign)
	if err != nil {
		t.Fatal(err)
	}
	if string(untouched) != foreignBody {
		t.Errorf("somebody else's shortcut was rewritten: %q", untouched)
	}
}

// A shortcut written before icons existed has no IconFile line at all; it must gain one rather than
// being left blank forever.
func TestRepairAddsAMissingIconLine(t *testing.T) {
	desktop := t.TempDir()
	path := filepath.Join(desktop, "old.url")
	if err := os.WriteFile(path, []byte("[InternetShortcut]\r\nURL="+protocolScheme+"://sync\r\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	if _, err := repairShortcutIcons(desktop, `C:\icon\app.ico`); err != nil {
		t.Fatal(err)
	}
	body, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(body), `IconFile=C:\icon\app.ico`) || !strings.Contains(string(body), "IconIndex=0") {
		t.Errorf("no icon was added: %q", body)
	}
}
