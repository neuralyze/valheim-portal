package main

import (
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
)

// The bug this replaces: the launcher preferred the profile's copy because it
// existed, attached to it, and mirrored nothing for the whole startup while BepInEx
// wrote to the Steam directory instead. Existence is not evidence; growth is.
func TestGrowingLogIgnoresAStaleFileAndPicksTheOneBeingWritten(t *testing.T) {
	active, game := t.TempDir(), t.TempDir()
	stale := filepath.Join(active, "BepInEx", "LogOutput.log")
	live := filepath.Join(game, "BepInEx", "LogOutput.log")
	for _, path := range []string{stale, live} {
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte("history from an earlier session\n"), 0o644); err != nil {
			t.Fatal(err)
		}
	}

	baselines := measureLogBaselines(active, game)
	if _, ok := baselines.growing(); ok {
		t.Fatal("nothing has been written since launch, so no log is live yet")
	}

	// Only the Steam directory copy is appended to, and it is second in preference
	// order: the stale profile copy must not win.
	file, err := os.OpenFile(live, os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := file.WriteString("this run\n"); err != nil {
		t.Fatal(err)
	}
	file.Close()

	candidate, ok := baselines.growing()
	if !ok {
		t.Fatal("the log being written was not detected")
	}
	if candidate.Path != live {
		t.Fatalf("followed %q, want the log that grew (%q)", candidate.Path, live)
	}
	if candidate.Baseline != int64(len("history from an earlier session\n")) {
		t.Fatalf("baseline = %d, want the pre-launch length so history is not replayed", candidate.Baseline)
	}
}

// A truncated log means BepInEx started a fresh file, so all of it is this run's.
func TestGrowingLogTreatsATruncatedFileAsEntirelyNew(t *testing.T) {
	active := t.TempDir()
	path := filepath.Join(active, "BepInEx", "LogOutput.log")
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("a long previous session\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	baselines := measureLogBaselines(active, "")
	if err := os.WriteFile(path, []byte("new\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	candidate, ok := baselines.growing()
	if !ok {
		t.Fatal("a truncated log is still a live log")
	}
	if candidate.Baseline != 0 {
		t.Fatalf("baseline = %d, want 0 so the whole new file is mirrored", candidate.Baseline)
	}
}

func TestLogFollowerYieldsOnlyNewCompleteLines(t *testing.T) {
	path := filepath.Join(t.TempDir(), "LogOutput.log")
	if err := os.WriteFile(path, []byte("first\r\nsecond\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	follower := newLogFollowerAt(path, 0)
	if got := follower.next(); strings.Join(got, "|") != "first|second" {
		t.Fatalf("first poll = %#v", got)
	}
	if got := follower.next(); len(got) != 0 {
		t.Fatalf("an idle log produced %#v", got)
	}

	// A half-written line is withheld until its newline arrives, so no line is
	// ever shown twice.
	file, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := file.WriteString("thi"); err != nil {
		t.Fatal(err)
	}
	if got := follower.next(); len(got) != 0 {
		t.Fatalf("a partial line was emitted: %#v", got)
	}
	if _, err := file.WriteString("rd\n"); err != nil {
		t.Fatal(err)
	}
	file.Close()
	if got := follower.next(); strings.Join(got, "|") != "third" {
		t.Fatalf("completed line = %#v", got)
	}
}

func TestLogFollowerRestartsWhenTheGameRewritesItsLog(t *testing.T) {
	path := filepath.Join(t.TempDir(), "LogOutput.log")
	if err := os.WriteFile(path, []byte("run-one-a\nrun-one-b\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	follower := newLogFollowerAt(path, 0)
	if got := follower.next(); len(got) != 2 {
		t.Fatalf("first run = %#v", got)
	}
	// BepInEx truncates the log on the next start; following must not sit past the
	// new end of file and report nothing forever.
	if err := os.WriteFile(path, []byte("run-two\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if got := follower.next(); strings.Join(got, "|") != "run-two" {
		t.Fatalf("after truncation = %#v", got)
	}
}

func TestLogFollowerStopsAtItsLineBudget(t *testing.T) {
	path := filepath.Join(t.TempDir(), "LogOutput.log")
	var builder strings.Builder
	for range maxStreamedLogLines + 50 {
		builder.WriteString("line\n")
	}
	if err := os.WriteFile(path, []byte(builder.String()), 0o644); err != nil {
		t.Fatal(err)
	}
	follower := newLogFollowerAt(path, 0)
	total := 0
	for range 200 {
		lines := follower.next()
		if len(lines) == 0 {
			break
		}
		total += len(lines)
	}
	if total != maxStreamedLogLines {
		t.Fatalf("streamed %d lines, want the %d budget", total, maxStreamedLogLines)
	}
	if !follower.Truncated {
		t.Fatal("hitting the budget was not recorded")
	}
}

// A real startup on the 114-package profile writes about 11,900 lines. The first
// budget was 2,000, which silently stopped mirroring a sixth of the way in and
// looked like the launcher losing touch with BepInEx. Guard the real-world size.
func TestLogFollowerMirrorsAFullRealWorldStartup(t *testing.T) {
	const realStartupLines = 11900
	path := filepath.Join(t.TempDir(), "LogOutput.log")
	var builder strings.Builder
	for i := range realStartupLines {
		builder.WriteString("[Info   :   BepInEx] Loading [Package ")
		builder.WriteString(strconv.Itoa(i))
		builder.WriteString(" 1.0.0]\n")
	}
	if err := os.WriteFile(path, []byte(builder.String()), 0o644); err != nil {
		t.Fatal(err)
	}
	follower := newLogFollowerAt(path, 0)
	total := 0
	for range 10000 {
		lines := follower.next()
		if len(lines) == 0 {
			break
		}
		total += len(lines)
	}
	if total != realStartupLines {
		t.Fatalf("mirrored %d of %d lines from a realistic startup log", total, realStartupLines)
	}
	if follower.Truncated {
		t.Fatal("a realistic startup log must not hit the line budget")
	}
}

// The profiles set AppendLog = true, so LogOutput.log carries every previous
// session. Replaying that history spent the whole line budget in seconds and then
// stopped, which is what "the log hangs up after a few seconds" actually was.
func TestLogFollowerSkipsHistoryFromEarlierSessions(t *testing.T) {
	path := filepath.Join(t.TempDir(), "LogOutput.log")
	var history strings.Builder
	for i := range 30000 {
		history.WriteString("[Info   :   BepInEx] previous session line ")
		history.WriteString(strconv.Itoa(i))
		history.WriteString("\n")
	}
	if err := os.WriteFile(path, []byte(history.String()), 0o644); err != nil {
		t.Fatal(err)
	}

	// What the launcher records immediately before starting the game: this log is
	// outside the profile layout, so the probe finds nothing there.
	if _, ok := measureLogBaselines(filepath.Dir(filepath.Dir(path)), "").growing(); ok {
		t.Fatal("baseline probe found a live log outside the profile layout")
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	follower := newLogFollowerAt(path, info.Size())

	if lines := follower.next(); len(lines) != 0 {
		t.Fatalf("history was replayed: %d lines, first %q", len(lines), lines[0])
	}
	file, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := file.WriteString("[Info   :   BepInEx] this run only\n"); err != nil {
		t.Fatal(err)
	}
	file.Close()
	if got := follower.next(); len(got) != 1 || !strings.Contains(got[0], "this run only") {
		t.Fatalf("current run output = %#v", got)
	}
	if follower.Truncated {
		t.Fatal("the line budget was spent on history")
	}
}

func TestMeasureLogBaselinesRecordsTheCurrentLength(t *testing.T) {
	active := t.TempDir()
	baselines := measureLogBaselines(active, "")
	if len(baselines) != 1 || baselines[0].Baseline != 0 {
		t.Fatalf("a missing log must record zero so following starts at the beginning: %#v", baselines)
	}
	path := filepath.Join(active, "BepInEx", "LogOutput.log")
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("abcdef\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if got := measureLogBaselines(active, "")[0].Baseline; got != 7 {
		t.Fatalf("baseline = %d, want 7", got)
	}
	// Both places are probed, so a log in either is caught.
	if paths := measureLogBaselines(active, t.TempDir()).Paths(); len(paths) != 2 {
		t.Fatalf("candidate paths = %v, want the profile and the Steam directory", paths)
	}
}
