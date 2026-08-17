package main

import (
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

func TestBuildLaunchSpecUsesOriginalSteamValheim(t *testing.T) {
	steam := filepath.Join(t.TempDir(), "Steam", "steamapps", "common", "Valheim")
	active := filepath.Join(t.TempDir(), "profile", "active")
	if err := os.MkdirAll(filepath.Join(active, "config"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(active, "BepInEx", "core"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(steam, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(steam, "valheim.exe"), []byte("steam-game"), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{
		filepath.Join(active, "BepInEx", "core", "BepInEx.Preloader.dll"),
		filepath.Join(active, "winhttp.dll"),
		filepath.Join(active, "doorstop_config.ini"),
	} {
		if err := os.WriteFile(path, []byte("loader"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	specification, err := buildLaunchSpec(steam, active, []string{"PATH=test", "BEPINEX_CONFIG_PATH=old", "VALHEIM_PROFILE_SYNC_ROOT=old"})
	if err != nil {
		t.Fatal(err)
	}
	if specification.Executable != filepath.Join(steam, "valheim.exe") || specification.Directory != steam {
		t.Fatalf("launch spec = %#v", specification)
	}
	// -console is part of the contract, not incidental: this launcher bypasses Steam, so it is
	// the only place the game's console gate can be set for a player.
	if want := []string{"--doorstop-enable", "true", "--doorstop-target-assembly", filepath.Join(active, "BepInEx", "core", "BepInEx.Preloader.dll"), "-console"}; strings.Join(specification.Arguments, "\x00") != strings.Join(want, "\x00") {
		t.Fatalf("launch arguments = %#v", specification.Arguments)
	}
	if _, err := os.Stat(filepath.Join(active, "valheim.exe")); !os.IsNotExist(err) {
		t.Fatalf("profile unexpectedly contains a copied game executable: %v", err)
	}
	environment := strings.Join(specification.Environment, "\n")
	if strings.Contains(environment, "BEPINEX_CONFIG_PATH=") || !strings.Contains(environment, "BEPINEX_PLUGIN_PATH="+filepath.Join(active, "BepInEx", "plugins")) || !strings.Contains(environment, "VALHEIM_PROFILE_SYNC_ROOT="+active) {
		t.Fatalf("launch environment = %q", environment)
	}
}

func TestStartGameReturnsAfterSpawning(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("the test fixture is a POSIX executable")
	}
	steam, active := t.TempDir(), t.TempDir()
	for _, path := range []string{
		filepath.Join(active, "BepInEx", "core", "BepInEx.Preloader.dll"),
		filepath.Join(active, "winhttp.dll"),
		filepath.Join(active, "doorstop_config.ini"),
	} {
		if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte("loader"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.MkdirAll(filepath.Join(active, "config"), 0o700); err != nil {
		t.Fatal(err)
	}
	game := filepath.Join(steam, "valheim.exe")
	if err := os.WriteFile(game, []byte("#!/bin/sh\nsleep 2\n"), 0o700); err != nil {
		t.Fatal(err)
	}
	started := time.Now()
	if err := startGame(steam, active); err != nil {
		t.Fatal(err)
	}
	if elapsed := time.Since(started); elapsed >= time.Second {
		t.Fatalf("startGame waited for the child process: %s", elapsed)
	}
}

func TestLaunchProfileStartsSteamVRBeforeValheim(t *testing.T) {
	// The wait now keeps mirroring after the window check, so a test that leaves
	// the real timers in place would sit through the whole settle window.
	fastGameWait(t)
	originalRuntime, originalGame := startVRRuntime, launchGame
	t.Cleanup(func() {
		startVRRuntime = originalRuntime
		launchGame = originalGame
	})
	events := make([]string, 0, 2)
	startVRRuntime = func() error {
		events = append(events, "steamvr")
		return nil
	}
	// valheimRunning is stubbed so the wait resolves on its own terms rather than
	// on whether this platform can inspect processes: not running for the
	// pre-flight check, then visible once the game has been asked to start.
	// Without it the test only finished quickly because the non-Windows stub
	// errors out, and a Windows run would poll for the whole gameAppearTimeout.
	launched := false
	launchGame = func(_, _ string) error {
		events = append(events, "valheim")
		launched = true
		return nil
	}
	valheimRunning = func() (bool, error) { return launched, nil }
	updates := make([]progressUpdate, 0, 2)
	if err := launchProfile(clientVR, "game", "profile", func(update progressUpdate) {
		updates = append(updates, update)
	}); err != nil {
		t.Fatal(err)
	}
	if got, want := strings.Join(events, ","), "steamvr,valheim"; got != want {
		t.Fatalf("launch order = %q, want %q", got, want)
	}
	// The first two stages are the contract this test was written for. The wait
	// stages follow, because a launcher that reports nothing after starting the
	// game is what led to it being started twice.
	if len(updates) < 4 || updates[0].Stage != "Starting SteamVR" || updates[1].Stage != "Launching Valheim" {
		t.Fatalf("launch updates = %#v", updates)
	}
	if updates[2].Stage != "Waiting for Valheim" {
		t.Fatalf("expected a wait stage after launch, got %q", updates[2].Stage)
	}
	// Window inspection is unavailable on this platform, so the launcher reports the
	// game as running and frees the player while continuing to mirror the log.
	stages := make([]string, 0, len(updates))
	running := -1
	for i, update := range updates {
		stages = append(stages, update.Stage)
		if running < 0 && update.Stage == "Valheim is running" {
			running = i
		}
	}
	if running < 0 || !updates[running].Terminal || updates[running].Percent != 100 {
		t.Fatalf("expected the game to be reported running and closable: %v", stages)
	}
}

func TestLaunchProfileDoesNotStartSteamVRForFlatProfile(t *testing.T) {
	fastGameWait(t)
	originalRuntime, originalGame, originalRunning := startVRRuntime, launchGame, valheimRunning
	t.Cleanup(func() {
		startVRRuntime = originalRuntime
		launchGame = originalGame
		valheimRunning = originalRunning
	})
	startVRRuntime = func() error {
		t.Fatal("SteamVR started for a Flat profile")
		return nil
	}
	launched := false
	launchGame = func(_, _ string) error { launched = true; return nil }
	valheimRunning = func() (bool, error) { return launched, nil }
	if err := launchProfile(clientFlat, "game", "profile", nil); err != nil {
		t.Fatal(err)
	}
}

func TestLaunchProfileRefusesWhenValheimAlreadyRunning(t *testing.T) {
	originalRunning := valheimRunning
	originalLaunch := launchGame
	originalVR := startVRRuntime
	defer func() { valheimRunning = originalRunning; launchGame = originalLaunch; startVRRuntime = originalVR }()

	valheimRunning = func() (bool, error) { return true, nil }
	launched := false
	launchGame = func(string, string) error { launched = true; return nil }
	startVRRuntime = func() error { t.Fatal("SteamVR must not be started when the game is already running"); return nil }

	err := launchProfile(clientVR, "game", "profile", nil)
	if err == nil {
		t.Fatal("expected a refusal while valheim.exe is live")
	}
	if launched {
		t.Fatal("must not replace the Doorstop shim while the game holds it mapped")
	}
	if !strings.Contains(err.Error(), "already running") {
		t.Fatalf("error must name the cause, got %q", err.Error())
	}
}

func TestLaunchProfileReportsGameRunning(t *testing.T) {
	fastGameWait(t)
	originalRunning := valheimRunning
	originalLaunch := launchGame
	originalPoll := gameAppearPoll
	defer func() { valheimRunning = originalRunning; launchGame = originalLaunch; gameAppearPoll = originalPoll }()

	gameAppearPoll = time.Millisecond
	calls := 0
	// Not running for the pre-flight, still absent on the first poll, then up.
	valheimRunning = func() (bool, error) {
		calls++
		return calls > 2, nil
	}
	launchGame = func(string, string) error { return nil }

	var stages []string
	if err := launchProfile(clientFlat, "game", "profile", func(update progressUpdate) {
		stages = append(stages, update.Stage)
	}); err != nil {
		t.Fatalf("launch: %v", err)
	}
	joined := strings.Join(stages, "|")
	for _, want := range []string{"Waiting for Valheim", "Valheim is running"} {
		if !strings.Contains(joined, want) {
			t.Fatalf("expected stage %q in %q", want, joined)
		}
	}
}

func TestAwaitGameVisibleReportsWhenGameNeverAppears(t *testing.T) {
	originalRunning := valheimRunning
	originalPoll := gameAppearPoll
	originalTimeout := gameAppearTimeout
	defer func() {
		valheimRunning = originalRunning
		gameAppearPoll = originalPoll
		gameAppearTimeout = originalTimeout
	}()

	valheimRunning = func() (bool, error) { return false, nil }
	gameAppearPoll = time.Millisecond
	gameAppearTimeout = 5 * time.Millisecond

	var last string
	awaitGameVisible(func(update progressUpdate) { last = update.Stage }, "", t.TempDir(), nil)
	if last != "Valheim did not appear" {
		t.Fatalf("a silent desktop is the bug being fixed; got final stage %q", last)
	}
}

// writeConsoleConfig puts a BepInEx.cfg where the launcher looks for it.
func writeConsoleConfig(t *testing.T, active string, enabled string) {
	t.Helper()
	dir := filepath.Join(active, "BepInEx", "config")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	body := "[Logging.Disk]\nEnabled = true\n\n[Logging.Console]\n## comment\nEnabled = " + enabled + "\nPreventClose = false\n"
	if err := os.WriteFile(filepath.Join(dir, "BepInEx.cfg"), []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestBepInExConsoleEnabledReadsOnlyTheConsoleSection(t *testing.T) {
	active := t.TempDir()
	if bepinexConsoleEnabled(active) {
		t.Fatal("a profile with no BepInEx.cfg must count as console-disabled")
	}
	writeConsoleConfig(t, active, "false")
	if bepinexConsoleEnabled(active) {
		t.Fatal("Enabled = false read as enabled")
	}
	writeConsoleConfig(t, active, "true")
	if !bepinexConsoleEnabled(active) {
		t.Fatal("Enabled = true read as disabled")
	}
	// A matching key outside the console section must not decide it.
	other := t.TempDir()
	dir := filepath.Join(other, "BepInEx", "config")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "BepInEx.cfg"), []byte("[Logging.Disk]\nEnabled = true\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if bepinexConsoleEnabled(other) {
		t.Fatal("Enabled from another section decided the console")
	}
}

// fastGameWait shrinks every timer awaitGameWindow consults so a test exercises
// the state machine rather than the clock, and restores them afterwards.
func fastGameWait(t *testing.T) {
	t.Helper()
	poll, quiet, settle, timeout, ceiling, recheck := gameWindowPoll, logQuietPeriod, windowSettleMax, gameWindowTimeout, gameWaitCeiling, gameProcessRecheck
	running, window := valheimRunning, gameWindowVisible
	t.Cleanup(func() {
		gameWindowPoll, logQuietPeriod, windowSettleMax = poll, quiet, settle
		gameWindowTimeout, gameWaitCeiling, gameProcessRecheck = timeout, ceiling, recheck
		valheimRunning, gameWindowVisible = running, window
	})
	gameWindowPoll = time.Millisecond
	logQuietPeriod = 5 * time.Millisecond
	windowSettleMax = 25 * time.Millisecond
	gameWindowTimeout = 20 * time.Millisecond
	gameWaitCeiling = 2 * time.Second
	gameProcessRecheck = time.Hour
}

// With no console the launcher is the only thing on screen, so it has to keep
// reporting until the game window is drawn, and keep mirroring past that until
// loading actually settles.
func TestAwaitGameWindowHoldsLoadingIndicatorUntilTheWindowAppears(t *testing.T) {
	fastGameWait(t)
	valheimRunning = func() (bool, error) { return true, nil }
	polls := 0
	gameWindowVisible = func() (bool, error) {
		polls++
		return polls > 3, nil
	}

	active := t.TempDir()
	writeConsoleConfig(t, active, "false")
	var updates []progressUpdate
	awaitGameWindow(func(update progressUpdate) { updates = append(updates, update) }, "", active, measureLogBaselines(active, ""))

	if len(updates) < 3 {
		t.Fatalf("expected loading ticks before the window appeared: %#v", updates)
	}
	// updates[0] explains, once and visibly, that the log is being mirrored.
	if updates[0].Quiet || updates[0].Stage != "Loading Valheim" {
		t.Fatalf("first update should announce the mirrored log: %#v", updates[0])
	}
	// The window opening frees the player to close, and is NOT the end of the
	// mirror: cutting off there is what made the log look like it stopped.
	opened := -1
	for i, update := range updates {
		if update.Stage == "Valheim is on screen" {
			opened = i
			break
		}
	}
	if opened < 0 {
		t.Fatalf("the window opening was never reported: %#v", updates)
	}
	if !updates[opened].Terminal || updates[opened].Percent != 100 {
		t.Fatalf("the window opening must let the player close: %#v", updates[opened])
	}
	if opened == len(updates)-1 {
		t.Fatal("mirroring stopped the moment the window appeared")
	}
	final := updates[len(updates)-1]
	if final.Stage != "Valheim is on screen" || final.Indeterminate || final.Quiet || final.Percent != 100 {
		t.Fatalf("final update = %#v", final)
	}
	for i, update := range updates[1:] {
		if strings.HasPrefix(update.Stage, "Loading Valheim, ") && (!update.Quiet || !update.Indeterminate) {
			t.Fatalf("tick %d should be a quiet marquee update: %#v", i, update)
		}
	}
}

// With the console on, it is the player's feedback and the launcher can rest.
func TestAwaitGameWindowDoesNotWaitWhenTheConsoleIsEnabled(t *testing.T) {
	originalWindow := gameWindowVisible
	t.Cleanup(func() { gameWindowVisible = originalWindow })
	gameWindowVisible = func() (bool, error) {
		t.Fatal("waited for a window even though the console reports progress")
		return false, nil
	}
	active := t.TempDir()
	writeConsoleConfig(t, active, "true")
	var updates []progressUpdate
	awaitGameWindow(func(update progressUpdate) { updates = append(updates, update) }, "", active, nil)
	if len(updates) != 1 || updates[0].Stage != "Valheim is running" {
		t.Fatalf("updates = %#v", updates)
	}
}

func TestAwaitGameWindowReportsAGameThatExitsBeforeDrawing(t *testing.T) {
	fastGameWait(t)
	gameProcessRecheck = time.Millisecond
	gameWindowVisible = func() (bool, error) { return false, nil }
	valheimRunning = func() (bool, error) { return false, nil }

	active := t.TempDir()
	writeConsoleConfig(t, active, "false")
	var last progressUpdate
	awaitGameWindow(func(update progressUpdate) { last = update }, "", active, nil)
	if last.Stage != "Valheim closed before opening" {
		t.Fatalf("final stage = %q", last.Stage)
	}
}

func TestAwaitGameWindowStopsAtItsDeadline(t *testing.T) {
	fastGameWait(t)
	gameWindowVisible = func() (bool, error) { return false, nil }
	valheimRunning = func() (bool, error) { return true, nil }

	active := t.TempDir()
	writeConsoleConfig(t, active, "false")
	var last progressUpdate
	awaitGameWindow(func(update progressUpdate) { last = update }, "", active, nil)
	if last.Stage != "Valheim is still loading" || last.Indeterminate {
		t.Fatalf("final update = %#v", last)
	}
}

// The point of the whole feature: with the console off, BepInEx's own output has
// to reach the window, because it is the only place a player can see it.
func TestAwaitGameWindowMirrorsTheBepInExLog(t *testing.T) {
	fastGameWait(t)
	valheimRunning = func() (bool, error) { return true, nil }

	active := t.TempDir()
	writeConsoleConfig(t, active, "false")
	logPath := filepath.Join(active, "BepInEx", "LogOutput.log")
	appendLine := func(line string) {
		file, err := os.OpenFile(logPath, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := file.WriteString(line + "\n"); err != nil {
			t.Fatal(err)
		}
		file.Close()
	}
	baselines := measureLogBaselines(active, "")
	// Written while the wait runs, which is what makes it this run's output rather
	// than history from an earlier session.
	polls := 0
	gameWindowVisible = func() (bool, error) {
		polls++
		switch polls {
		case 2:
			appendLine("[Info   :   BepInEx] Loading [Jotunn 2.29.2]")
		case 3:
			appendLine("[Warning:   BepInEx] Skipping [Broken 1.0.0]")
		}
		return polls > 4, nil
	}

	var mirrored []string
	awaitGameWindow(func(update progressUpdate) {
		mirrored = append(mirrored, update.LogLines...)
	}, "", active, baselines)

	joined := strings.Join(mirrored, "\n")
	for _, want := range []string{"Loading [Jotunn 2.29.2]", "Skipping [Broken 1.0.0]"} {
		if !strings.Contains(joined, want) {
			t.Fatalf("game log line %q never reached the window; mirrored %#v", want, mirrored)
		}
	}
}

// Process and window inspection are helpers, not preconditions. When they fail the
// log is the only feedback left, so it must still be mirrored: a tasklist that
// errors used to abandon every signal and leave the player with an empty box.
func TestAwaitGameVisibleMirrorsTheLogWhenInspectionIsUnavailable(t *testing.T) {
	fastGameWait(t)
	inspectionFailed := errors.New("process inspection unavailable")
	valheimRunning = func() (bool, error) { return false, inspectionFailed }
	gameWindowVisible = func() (bool, error) { return false, inspectionFailed }

	active := t.TempDir()
	writeConsoleConfig(t, active, "false")
	baselines := measureLogBaselines(active, "")
	logPath := filepath.Join(active, "BepInEx", "LogOutput.log")
	if err := os.WriteFile(logPath, []byte("[Info   :   BepInEx] Loading [Jotunn 2.29.2]\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	var mirrored []string
	var stages []string
	awaitGameVisible(func(update progressUpdate) {
		mirrored = append(mirrored, update.LogLines...)
		stages = append(stages, update.Stage)
	}, "", active, baselines)

	if !strings.Contains(strings.Join(mirrored, "\n"), "Loading [Jotunn 2.29.2]") {
		t.Fatalf("the log was not mirrored when inspection failed; stages %v", stages)
	}
}

// The log actually being followed is named, so "nothing appeared" is checkable and
// a wrong-directory guess is visible rather than silent.
func TestAwaitGameWindowNamesTheLogItIsFollowing(t *testing.T) {
	fastGameWait(t)
	valheimRunning = func() (bool, error) { return true, nil }
	gameWindowVisible = func() (bool, error) { return true, nil }

	active := t.TempDir()
	writeConsoleConfig(t, active, "false")
	baselines := measureLogBaselines(active, "")
	logPath := filepath.Join(active, "BepInEx", "LogOutput.log")
	if err := os.WriteFile(logPath, []byte("[Info   :   BepInEx] Loading\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	var details []string
	awaitGameWindow(func(update progressUpdate) { details = append(details, update.Detail) }, "", active, baselines)
	joined := strings.Join(details, "\n")
	if !strings.Contains(joined, logPath) {
		t.Fatalf("the log being followed was never named: %q", joined)
	}
}

// And when BepInEx never writes a log, the launcher says so instead of sitting mute.
func TestAwaitGameWindowWarnsWhenNoLogAppears(t *testing.T) {
	fastGameWait(t)
	notice := logAppearNotice
	t.Cleanup(func() { logAppearNotice = notice })
	logAppearNotice = time.Millisecond
	valheimRunning = func() (bool, error) { return true, nil }
	gameWindowVisible = func() (bool, error) { return false, nil }

	active := t.TempDir()
	writeConsoleConfig(t, active, "false")
	var stages, details []string
	awaitGameWindow(func(update progressUpdate) {
		stages = append(stages, update.Stage)
		details = append(details, update.Detail)
	}, "", active, measureLogBaselines(active, ""))
	if !strings.Contains(strings.Join(stages, "|"), "Waiting for the BepInEx log") {
		t.Fatalf("a missing log was never reported: %v", stages)
	}
	// Naming every place that was checked is what turns the next bug report into
	// evidence instead of another round of guessing.
	if !strings.Contains(strings.Join(details, "\n"), filepath.Join(active, "BepInEx", "LogOutput.log")) {
		t.Fatalf("the paths that were checked were not named: %v", details)
	}
}

// Valheim has been started by the time the mirror begins, so closing the launcher
// is always safe from that point. Tying the Close button to a window probe made an
// unverified Win32 class-name match a precondition for the player being allowed to
// leave: when it never matched, the window offered no way out for twelve minutes.
func TestAwaitGameWindowFreesThePlayerBeforeAnyWindowIsFound(t *testing.T) {
	fastGameWait(t)
	valheimRunning = func() (bool, error) { return true, nil }
	// Never found, exactly as a wrong window-class guess behaves.
	gameWindowVisible = func() (bool, error) { return false, nil }

	active := t.TempDir()
	writeConsoleConfig(t, active, "false")
	var updates []progressUpdate
	awaitGameWindow(func(update progressUpdate) {
		updates = append(updates, update)
	}, "", active, measureLogBaselines(active, ""))

	if len(updates) == 0 {
		t.Fatal("the mirror phase reported nothing at all")
	}
	if !updates[0].Terminal {
		t.Fatalf("the player must be free to close as soon as the game is launched: %#v", updates[0])
	}
	if !updates[0].Indeterminate {
		t.Fatalf("work continues after the button appears, so the bar must keep moving: %#v", updates[0])
	}
}
