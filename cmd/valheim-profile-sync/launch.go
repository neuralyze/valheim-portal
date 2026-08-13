package main

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

type launchSpec struct {
	Executable  string
	Arguments   []string
	Directory   string
	Environment []string
}

func launchCommand(gameDir, active string) (string, []string, error) {
	gameDir = filepath.Clean(gameDir)
	active = filepath.Clean(active)
	if gameDir == "." || active == "." {
		return "", nil, errors.New("Steam game directory and installed profile are required")
	}
	executable := filepath.Join(gameDir, "valheim.exe")
	if info, err := os.Stat(executable); err != nil || info.IsDir() {
		return "", nil, errors.New("Steam Valheim is missing valheim.exe")
	}
	if info, err := os.Stat(active); err != nil || !info.IsDir() {
		return "", nil, errors.New("the selected profile is not installed")
	}
	if info, err := os.Stat(filepath.Join(active, "config")); err != nil || !info.IsDir() {
		return "", nil, errors.New("selected profile is missing its config directory")
	}
	for _, path := range []string{
		filepath.Join(active, "BepInEx", "core", "BepInEx.Preloader.dll"),
		filepath.Join(active, "winhttp.dll"),
		filepath.Join(active, "doorstop_config.ini"),
	} {
		if info, err := os.Stat(path); err != nil || info.IsDir() {
			return "", nil, fmt.Errorf("selected profile is missing required loader file %q", filepath.Base(path))
		}
	}
	return executable, []string{
		"--doorstop-enable", "true",
		"--doorstop-target-assembly", filepath.Join(active, "BepInEx", "core", "BepInEx.Preloader.dll"),
	}, nil
}

func buildLaunchSpec(gameDir, active string, inherited []string) (launchSpec, error) {
	executable, arguments, err := launchCommand(gameDir, active)
	if err != nil {
		return launchSpec{}, err
	}
	return launchSpec{
		Executable:  executable,
		Arguments:   arguments,
		Directory:   filepath.Clean(gameDir),
		Environment: profileEnvironment(inherited, active),
	}, nil
}

func profileEnvironment(inherited []string, active string) []string {
	overrides := []string{
		"BEPINEX_PLUGIN_PATH=" + filepath.Join(active, "BepInEx", "plugins"),
		"VALHEIM_PROFILE_SYNC_ROOT=" + active,
	}
	result := make([]string, 0, len(inherited)+len(overrides))
	for _, value := range inherited {
		name, _, found := strings.Cut(value, "=")
		if !found || !profileEnvironmentName(name) {
			result = append(result, value)
		}
	}
	return append(result, overrides...)
}

func profileEnvironmentName(name string) bool {
	return strings.EqualFold(name, "BEPINEX_CONFIG_PATH") || strings.EqualFold(name, "BEPINEX_PLUGIN_PATH") || strings.EqualFold(name, "VALHEIM_PROFILE_SYNC_ROOT")
}

var startVRRuntime = prepareVRRuntime
var launchGame = startGame
var valheimRunning = checkValheimRunning
var gameWindowVisible = checkGameWindowVisible
var consoleEnabled = bepinexConsoleEnabled

// How long to wait for valheim.exe to appear before saying so. Startup on a
// 114-package profile is slow, so this is generous; it only affects the message.
var gameAppearTimeout = 45 * time.Second
var gameAppearPoll = 500 * time.Millisecond

// How long to keep reporting that the game is loading after the process exists
// but no window has been drawn yet. Bounded so the launcher always reaches a
// resting state, generous because patching 114 packages genuinely takes this
// long on a cold start.
var gameWindowTimeout = 4 * time.Minute
var gameWindowPoll = time.Second

// The game window appears before the profile has finished loading, so mirroring
// continues past it rather than stopping the moment a window exists. logQuietPeriod
// only decides when a run that never showed a window has gone idle enough to stop
// reporting; it deliberately does not end a mirror after the window is up, because
// BepInEx buffers its disk writes and goes quiet for seconds while healthy.
var logQuietPeriod = 6 * time.Second
var windowSettleMax = 3 * time.Minute

// Absolute ceiling, so a log that never goes quiet still reaches a resting state.
var gameWaitCeiling = 12 * time.Minute

// How often to re-check that valheim.exe is still alive while waiting for its
// window. Slower than the log poll because each check spawns a helper process.
var gameProcessRecheck = 5 * time.Second

// How long to wait before telling the player, in the log itself, that BepInEx
// has not created its log file.
var logAppearNotice = 15 * time.Second

func launchProfile(clientType, gameDir, active string, reporter progressReporter) error {
	// Refuse to touch the install while a copy of the game is live. The Doorstop shim
	// (winhttp.dll) is mapped into valheim.exe for the whole session, so replacing it
	// fails with a bare "Access is denied" from the middle of the bootstrap - which is
	// exactly what happened when the game had been started twice.
	if running, err := valheimRunning(); err == nil && running {
		return errors.New("Valheim is already running. Close it completely (check the taskbar and Task Manager for valheim.exe) and then use the profile link again")
	}

	if clientType == clientVR {
		report(reporter, progressUpdate{Stage: "Starting SteamVR", Detail: "Waiting for SteamVR before launching Valheim.", Percent: 95})
		if err := startVRRuntime(); err != nil {
			return err
		}
	}
	report(reporter, progressUpdate{Stage: "Launching Valheim", Detail: "Starting your Steam installation.", Percent: 96})
	// Captured before launch: BepInEx appends to these logs, so this is the line
	// between the previous session's output and this run's.
	baselines := measureLogBaselines(active, gameDir)
	if err := launchGame(gameDir, active); err != nil {
		return err
	}
	awaitGameVisible(reporter, gameDir, active, baselines)
	return nil
}

// awaitGameVisible turns "the launcher vanished and nothing happened" into a
// reported state. It is the only signal a player gets that a heavily modded
// profile is loading rather than stalled.
//
// Never fails the launch: the game either started or startGame already errored.
func awaitGameVisible(reporter progressReporter, gameDir, active string, baselines logBaselines) {
	report(reporter, progressUpdate{
		Stage:   "Waiting for Valheim",
		Detail:  "Valheim is loading. With this many mods the first window can take a minute - do not start it again.",
		Percent: 97,
	})
	deadline := time.Now().Add(gameAppearTimeout)
	for {
		running, err := valheimRunning()
		if err != nil {
			// Process inspection is unavailable. The game was just launched, so fall
			// through to the log mirror rather than abandoning every signal: a
			// tasklist that fails must not be the reason a player sees nothing.
			awaitGameWindow(reporter, gameDir, active, baselines)
			return
		}
		if running {
			awaitGameWindow(reporter, gameDir, active, baselines)
			return
		}
		if time.Now().After(deadline) {
			report(reporter, progressUpdate{
				Stage:   "Valheim did not appear",
				Detail:  "Steam was asked to start Valheim but no valheim.exe was seen. Check Steam, then try the profile link again.",
				Percent: 100,
			})
			return
		}
		time.Sleep(gameAppearPoll)
	}
}

// awaitGameWindow keeps the launcher informative for the gap between "valheim.exe
// exists" and "the game is on screen", which is where a modded profile spends
// most of its startup.
//
// With the BepInEx console enabled that gap is already visible to the player, so
// this says the game is running and stops. With it disabled there is nothing on
// screen at all, so the launcher becomes the console: it mirrors BepInEx's own log
// into the activity box, which both proves work is happening and names the mod
// being patched when a profile stalls.
func awaitGameWindow(reporter progressReporter, gameDir, active string, baselines logBaselines) {
	if consoleEnabled(active) {
		report(reporter, progressUpdate{
			Stage:   "Valheim is running",
			Detail:  "The game is loading its mods. The BepInEx console shows its progress; it is safe to close this window.",
			Percent: 100,
		})
		return
	}
	// Terminal from the outset: Valheim has been started, so closing this window is
	// always safe from here on. Withholding the Close button until a window probe
	// succeeds made an unverified Win32 class-name match a precondition for the
	// player being allowed to leave, which is not a decision this wait should own.
	// Indeterminate keeps the bar moving to show the mirror is still working.
	report(reporter, progressUpdate{
		Stage:         "Loading Valheim",
		Detail:        "The BepInEx console is off for this profile, so its log is mirrored here. It is safe to close this window at any time. Do not start Valheim again.",
		Percent:       100,
		Terminal:      true,
		Indeterminate: true,
	})
	started := time.Now()
	warnedNoLog := false
	var follower *logFollower
	// The process check spawns a helper, so it runs on its own slower cadence than
	// the log poll rather than once per tick.
	// Backdated so the first iteration checks immediately: a profile that dies on
	// startup should be reported at once, not after one recheck interval.
	lastProcessCheck := started.Add(-gameProcessRecheck)
	lastLogLine := started
	var windowSeenAt time.Time
	pending := []string{}
	finish := func(stage, detail string) {
		report(reporter, progressUpdate{
			Stage: stage, Detail: detail, Percent: 100,
			LogLines: append(pending, drainLog(follower)...),
		})
	}
	for {
		if follower == nil {
			if candidate, ok := baselines.growing(); ok {
				follower = newLogFollowerAt(candidate.Path, candidate.Baseline)
				report(reporter, progressUpdate{
					Stage:         "Loading Valheim",
					Detail:        "Following " + candidate.Path,
					Percent:       100,
					Indeterminate: true,
				})
			}
		}
		lines := drainLog(follower)
		if len(lines) > 0 {
			lastLogLine = time.Now()
		}
		quiet := time.Since(lastLogLine)
		if follower == nil && !warnedNoLog && time.Since(started) >= logAppearNotice {
			warnedNoLog = true
			report(reporter, progressUpdate{
				Stage:         "Waiting for the BepInEx log",
				Detail:        "No log has been written yet. Checked: " + strings.Join(baselines.Paths(), " and ") + ". If nothing ever appears, this profile's BepInEx.cfg may have [Logging.Disk] Enabled = false.",
				Percent:       100,
				Indeterminate: true,
				LogLines:      lines,
			})
		}

		if windowSeenAt.IsZero() {
			visible, err := gameWindowVisible()
			if err != nil {
				// No way to look for a window on this build. Keep mirroring anyway:
				// the log is the point, and window detection is only how the wait
				// decides it is finished.
				windowSeenAt = time.Now()
				report(reporter, progressUpdate{
					Stage:    "Valheim is running",
					Detail:   "The game is loading its mods. It is safe to close this window; its log continues below.",
					Percent:  100,
					Terminal: true,
					LogLines: lines,
				})
				time.Sleep(gameWindowPoll)
				continue
			}
			if visible {
				windowSeenAt = time.Now()
				// Terminal so the player is free to close immediately, but the loop
				// keeps going: the window opens well before the profile has finished
				// loading, and ending the mirror here is what made the log look like
				// it had stopped following BepInEx.
				report(reporter, progressUpdate{
					Stage:    "Valheim is on screen",
					Detail:   "The game window opened after " + elapsedPhrase(time.Since(started)) + ". It is safe to close this window; its log continues below while the profile finishes loading.",
					Percent:  100,
					Terminal: true,
					LogLines: lines,
				})
				time.Sleep(gameWindowPoll)
				continue
			}
		}

		if time.Since(lastProcessCheck) >= gameProcessRecheck {
			lastProcessCheck = time.Now()
			if running, err := valheimRunning(); err == nil && !running {
				pending = lines
				if windowSeenAt.IsZero() {
					finish("Valheim closed before opening", "valheim.exe started and then exited before a window appeared. The log above usually names the mod that failed; use Collect diagnostics and try the profile link again.")
				} else {
					finish("Valheim closed", "The game exited after "+elapsedPhrase(time.Since(started))+". The log above is the full startup output.")
				}
				return
			}
		}

		switch {
		// Deliberately no "the log went quiet" exit once the window is up: BepInEx
		// buffers its disk writes (the profiles do not set FlushOnWrite), so a
		// perfectly healthy startup goes silent for seconds at a time and a
		// quiet-based exit cut the mirror off mid-load.
		case !windowSeenAt.IsZero() && time.Since(windowSeenAt) >= windowSettleMax:
			pending = lines
			finish("Valheim is on screen", "The game window is open and still logging after "+elapsedPhrase(time.Since(started))+". It is safe to close this window.")
			return
		case windowSeenAt.IsZero() && time.Since(started) >= gameWindowTimeout && quiet >= logQuietPeriod:
			pending = lines
			finish("Valheim is still loading", "No window after "+elapsedPhrase(time.Since(started))+" and the log has gone quiet, but valheim.exe is still running. Give it longer before starting it again.")
			return
		case time.Since(started) >= gameWaitCeiling:
			pending = lines
			finish("Valheim is still loading", "Still loading after "+elapsedPhrase(time.Since(started))+". Use Collect diagnostics if it never opens.")
			return
		}

		// Quiet keeps the elapsed headline moving without adding a line of its own;
		// the log lines are the visible output.
		report(reporter, progressUpdate{
			Stage:         "Loading Valheim, " + elapsedPhrase(time.Since(started)),
			Percent:       -1,
			Quiet:         true,
			Indeterminate: true,
			LogLines:      lines,
		})
		time.Sleep(gameWindowPoll)
	}
}

// drainLog returns whatever the game has written since the last poll, plus a
// one-time notice when the mirror has hit its line budget.
func drainLog(follower *logFollower) []string {
	if follower == nil {
		return nil
	}
	lines := follower.next()
	if follower.Truncated && !follower.noticed {
		follower.noticed = true
		lines = append(lines, "... further log output omitted; use Collect diagnostics for the full log.")
	}
	return lines
}

func elapsedPhrase(since time.Duration) string {
	seconds := int(since.Round(time.Second) / time.Second)
	if seconds < 60 {
		return strconv.Itoa(seconds) + "s"
	}
	minutes := seconds / 60
	return strconv.Itoa(minutes) + "m" + strconv.Itoa(seconds%60) + "s"
}

func startGame(gameDir, active string) error {
	if err := installBootstrap(gameDir, active); err != nil {
		return err
	}
	specification, err := buildLaunchSpec(gameDir, active, os.Environ())
	if err != nil {
		return err
	}
	command := exec.Command(specification.Executable, specification.Arguments...)
	command.Dir = specification.Directory
	command.Env = specification.Environment
	if err := command.Start(); err != nil {
		return err
	}
	// No automatic diagnostics collection. Every launch used to start a second process that waited
	// for the game to exit, packaged the session's logs and uploaded them to the portal - fine for
	// a handful of known players, wrong to do silently to the public. The --collect-diagnostics
	// entry point remains for an operator asking a player for a bundle deliberately.
	return nil
}
