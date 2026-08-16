package main

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
)

const applicationName = "Valheim Profile Sync"

type commandRunner func(string, ...string) error

type progressUpdate struct {
	Stage    string
	Detail   string
	Percent  int
	Terminal bool
	Failure  bool
	// Quiet updates the headline and the bar without adding an activity-log
	// line. Used by the wait for the game window, which ticks often enough that
	// logging every tick would bury the entries that matter.
	Quiet bool
	// Indeterminate switches the bar to a marquee, for a wait whose length
	// depends on how many mods a profile patches rather than on work completed.
	Indeterminate bool
	// LogLines is output from the game's own log, appended to the activity log
	// verbatim rather than as "stage: detail". With the BepInEx console disabled
	// this is the only place a player can watch a profile load.
	LogLines []string
}

type progressReporter func(progressUpdate)

func report(reporter progressReporter, update progressUpdate) {
	if reporter != nil {
		reporter(update)
	}
}

func synchronizeProfile(ctx context.Context, request profileRequest, gameDir string, launch bool, reporter progressReporter) (bool, error) {
	if launch {
		var err error
		gameDir, err = validateSteamValheimDirectory(gameDir)
		if err != nil {
			return false, err
		}
	}
	report(reporter, progressUpdate{Stage: "Preparing profile", Detail: request.Profile, Percent: 5})
	syncer := newProfileSyncer(nil)
	syncer.GameDir = gameDir
	syncer.Progress = reporter
	changed, err := syncer.synchronize(ctx, request)
	if err != nil {
		return false, err
	}
	executable, err := os.Executable()
	if err != nil {
		return false, err
	}
	desktop, err := desktopDirectory()
	if err != nil {
		return false, err
	}
	localAppData, err := localApplicationData()
	if err != nil {
		return false, err
	}
	root, err := profileRoot(localAppData, request)
	if err != nil {
		return false, err
	}
	// Once, not on every update. Writing it on the sync path meant every
	// "Install or update" recreated the file, so a shortcut the player had
	// deliberately deleted came back, and its timestamp churned for no reason.
	// Installers do not work that way: they place shortcuts during install,
	// record that they did, and leave them alone afterwards - resurrecting a
	// deleted one is what "Repair" is for. The stamp below is this app's
	// equivalent of the registry value an installer would write, kept beside the
	// profile so it is removed when the profile is.
	//
	// Players who already have a shortcut have no stamp yet, so it is written
	// once more on the next sync and left alone from then on.
	shortcutWritten, err := shortcutAlreadyCreated(root)
	if err != nil {
		return false, err
	}
	if !shortcutWritten {
		report(reporter, progressUpdate{Stage: "Creating Desktop shortcut", Detail: request.Profile, Percent: 6})
		if _, err := writeShortcut(desktop, request, shortcutIconPath(executable)); err != nil {
			return false, err
		}
		if err := recordShortcutCreated(root); err != nil {
			return false, err
		}
	}
	if !launch {
		report(reporter, progressUpdate{Stage: "Profile is ready", Detail: request.Profile + " is up to date. Use the Desktop shortcut whenever you play this profile.", Percent: 100, Terminal: true})
		return changed, nil
	}
	if gameDir == "" {
		return false, errors.New("select the Steam Valheim folder before starting Valheim")
	}
	gameDir, err = validateSteamValheimDirectory(gameDir)
	if err != nil {
		return false, err
	}
	portal, err := newPortalClient(request, nil)
	if err != nil {
		return false, err
	}
	report(reporter, progressUpdate{Stage: "Checking server status", Detail: "Confirming that " + request.World + " is online before launching Valheim.", Percent: 94})
	if err := portal.requireOnline(ctx, request); err != nil {
		return false, err
	}
	if err := launchProfile(request.ClientType, gameDir, filepath.Join(root, "active"), reporter); err != nil {
		if changed {
			if rollbackErr := rollbackGeneration(root); rollbackErr == nil {
				return false, errors.New("Valheim could not start. The previous profile was restored.")
			}
		}
		return false, err
	}
	report(reporter, progressUpdate{Stage: "Valheim started", Detail: request.Profile + " is ready. A shortcut was created on your Desktop; use it next time to check for updates and launch this profile.", Percent: 100, Terminal: true})
	return changed, nil
}

func registerCurrentProtocol() error {
	executable, err := os.Executable()
	if err != nil {
		return err
	}
	if err := registerProtocol(executable, runCommand); err != nil {
		return err
	}
	// Repair the shortcuts already on the Desktop, because the whole point of the icon living at a
	// stable path is that a blank one heals by running the application - the property the removed
	// self-copy used to provide. Failures here are not the player's problem: the protocol is
	// registered and the app works, so a shortcut that could not be rewritten is a cosmetic loss.
	if icon, iconErr := stableIconPath(); iconErr == nil {
		if desktop, desktopErr := desktopDirectory(); desktopErr == nil {
			_, _ = repairShortcutIcons(desktop, icon)
		}
	}
	return nil
}

// runCommand runs reg.exe for URL-protocol registration. Hidden because a
// windowsgui binary gives every console child its own visible window.
func runCommand(name string, arguments ...string) error {
	return hiddenCommand(name, arguments...).Run()
}

func registerProtocol(executable string, run commandRunner) error {
	if executable == "" || run == nil {
		return errors.New("cannot register profile sync URL protocol")
	}
	absolute, err := filepath.Abs(executable)
	if err != nil {
		return err
	}
	if len(absolute) == 0 || containsQuote(absolute) {
		return errors.New("profile sync executable path is invalid")
	}
	key := `HKCU\Software\Classes\` + protocolScheme
	commands := [][]string{
		{"ADD", key, "/ve", "/t", "REG_SZ", "/d", "URL: Valheim Profile Sync Protocol", "/f"},
		{"ADD", key, "/v", "URL Protocol", "/t", "REG_SZ", "/d", "", "/f"},
		{"ADD", key + `\shell\open\command`, "/ve", "/t", "REG_SZ", "/d", protocolCommand(absolute), "/f"},
	}
	// Windows draws a .url from the protocol handler's DefaultIcon on some builds and from the file's
	// own IconFile on others. Setting both is one registry write and removes the difference; setting
	// neither is how a shortcut ends up blank no matter what the file says.
	if icon, iconErr := stableIconPath(); iconErr == nil && !containsQuote(icon) {
		commands = append(commands, []string{"ADD", key + `\DefaultIcon`, "/ve", "/t", "REG_SZ", "/d", icon + ",0", "/f"})
	}
	for _, arguments := range commands {
		if err := run("reg.exe", arguments...); err != nil {
			return fmt.Errorf("register %s URL protocol: %w", protocolScheme, err)
		}
	}
	return nil
}

func containsQuote(value string) bool {
	for _, c := range value {
		if c == '"' {
			return true
		}
	}
	return false
}

func protocolCommand(executable string) string {
	return `"` + executable + `" "%1"`
}

func writeShortcut(desktop string, request profileRequest, executable string) (string, error) {
	if err := request.validate(); err != nil {
		return "", err
	}
	if executable == "" || containsQuote(executable) {
		return "", errors.New("shortcut executable path is invalid")
	}
	name := request.Profile + ".url"
	path := filepath.Join(desktop, name)
	content := "[InternetShortcut]\r\nURL=" + profileShortcutURL(request) + "\r\nIconFile=" + executable + "\r\nIconIndex=0\r\n"
	if err := writeTextAtomically(path, content); err != nil {
		return "", err
	}
	obsolete := [...]string{
		request.World + " - " + request.Profile + ".url",
		applicationName + " - " + request.World + " - " + request.Profile + " - " + request.ClientType + ".url",
	}
	for _, oldName := range obsolete {
		oldPath := filepath.Join(desktop, oldName)
		if oldPath == path {
			continue
		}
		if err := os.Remove(oldPath); err != nil && !errors.Is(err, os.ErrNotExist) {
			return "", fmt.Errorf("remove old Desktop shortcut: %w", err)
		}
	}
	return path, nil
}

func writeTextAtomically(path, content string) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	temporary, err := os.CreateTemp(filepath.Dir(path), ".shortcut-")
	if err != nil {
		return err
	}
	name := temporary.Name()
	if _, err := temporary.WriteString(content); err != nil {
		temporary.Close()
		os.Remove(name)
		return err
	}
	if err := temporary.Chmod(0o600); err != nil {
		temporary.Close()
		os.Remove(name)
		return err
	}
	if err := temporary.Close(); err != nil {
		os.Remove(name)
		return err
	}
	if err := replaceFile(name, path); err != nil {
		os.Remove(name)
		return err
	}
	return nil
}

func fatal(message string) {
	fmt.Fprintln(os.Stderr, message)
	os.Exit(1)
}

// shortcutStampName records that this profile's Desktop shortcut has been
// created. Its presence is the only thing consulted; its contents are for a
// human reading the folder.
const shortcutStampName = ".desktop-shortcut-created"

// shortcutAlreadyCreated reports whether the Desktop shortcut for this profile
// has been written before. A missing profile directory counts as "not yet",
// because the first sync creates it.
func shortcutAlreadyCreated(root string) (bool, error) {
	if root == "" {
		return false, errors.New("profile directory is required")
	}
	_, err := os.Stat(filepath.Join(root, shortcutStampName))
	if err == nil {
		return true, nil
	}
	if errors.Is(err, os.ErrNotExist) {
		return false, nil
	}
	return false, err
}

func recordShortcutCreated(root string) error {
	if err := os.MkdirAll(root, 0o700); err != nil {
		return err
	}
	return writeTextAtomically(filepath.Join(root, shortcutStampName),
		"This profile's Desktop shortcut has been created.\r\n"+
			"Delete this file to have it recreated on the next update.\r\n")
}
