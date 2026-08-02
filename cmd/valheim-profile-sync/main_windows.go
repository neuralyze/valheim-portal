//go:build windows

package main

import (
	"context"
	"errors"
	"fmt"
	"image"
	"image/color"
	"os"
	"strings"
	"time"

	"github.com/lxn/walk"
	. "github.com/lxn/walk/declarative"
)

func main() {
	if len(os.Args) > 1 && os.Args[1] == "--collect-diagnostics" {
		_ = runDiagnosticsCollector(os.Args[2:])
		return
	}
	runWindowsApplication(os.Args[1:])
}

type playerWindow struct {
	window           *walk.MainWindow
	headline         *walk.Label
	versionLabel     *walk.Label
	detail           *walk.TextEdit
	progress         *walk.ProgressBar
	action           *walk.PushButton
	choose           *walk.PushButton
	folderCheckmark  *walk.Bitmap
	pendingRequest   *profileRequest
	gameDirectory    string
	profileDirectory string
	doneNeedsGame    bool
	busy             bool
	complete         bool
	activityLines    int
}

func runWindowsApplication(args []string) {
	ui := &playerWindow{}
	window := MainWindow{
		AssignTo: &ui.window,
		Title:    applicationName,
		MinSize:  Size{Width: 620, Height: 580},
		Size:     Size{Width: 720, Height: 660},
		Layout:   VBox{MarginsZero: false, Spacing: 12},
		Children: []Widget{
			Label{Text: applicationName, Font: Font{PointSize: 19, Bold: true}},
			Label{Text: "Your approved Valheim profiles, installed safely and kept up to date."},
			Label{AssignTo: &ui.versionLabel, Text: currentInstalledProfileSummary(), Font: Font{PointSize: 13, Bold: true}},
			GroupBox{Title: "How it works", Layout: VBox{MarginsZero: false, Spacing: 4}, Children: []Widget{
				Label{Text: "1. Install this app once, then open a world on the portal and choose how you play."},
				Label{Text: "2. The app verifies and updates only that profile, then launches your existing Steam Valheim."},
				Label{Text: "3. A shortcut is created on your Desktop. Use that shortcut whenever you play this profile."},
				Label{Text: "4. The shortcut checks for profile updates before starting Valheim."},
			}},
			Composite{Layout: VBox{MarginsZero: true, Spacing: 5}, Children: []Widget{
				Label{AssignTo: &ui.headline, Text: "Preparing Valheim Profile Sync", Font: Font{PointSize: 11, Bold: true}},
				ProgressBar{AssignTo: &ui.progress, MinValue: 0, MaxValue: 100, Value: 0},
				TextEdit{AssignTo: &ui.detail, ReadOnly: true, VScroll: true, HScroll: true, Font: Font{PointSize: 10}, MinSize: Size{Height: 170}},
			}},
			Composite{Layout: HBox{MarginsZero: true, Spacing: 8}, Children: []Widget{
				PushButton{AssignTo: &ui.choose, Text: "Choose Valheim folder…", OnClicked: ui.chooseSteamValheimDirectory},
				PushButton{Text: "Choose profile storage…", OnClicked: ui.chooseProfileStorageDirectory},
				PushButton{AssignTo: &ui.action, Text: "Install Valheim Profile Sync", OnClicked: ui.handleAction},
			}},
			Label{Text: "This app uses your existing Steam Valheim installation. It never copies the game."},
		},
	}
	if err := window.Create(); err != nil {
		walk.MsgBox(nil, applicationName, "Valheim Profile Sync could not start. "+err.Error(), walk.MsgBoxIconError)
		return
	}
	checkmark, err := newFolderCheckmark()
	if err != nil {
		walk.MsgBox(ui.window, applicationName, "Valheim Profile Sync could not create its folder status indicator. "+err.Error(), walk.MsgBoxIconError)
		return
	}
	ui.folderCheckmark = checkmark
	defer checkmark.Dispose()
	go func() {
		time.Sleep(50 * time.Millisecond)
		ui.window.Synchronize(func() {
			ui.initializeSteamValheimDirectory()
			startWindowsFlow(ui, args)
		})
	}()
	ui.window.Run()
}

func newFolderCheckmark() (*walk.Bitmap, error) {
	icon := image.NewRGBA(image.Rect(0, 0, 18, 18))
	green := color.RGBA{R: 0x18, G: 0x9a, B: 0x48, A: 0xff}
	paint := func(x, y int) {
		for offsetX := range 3 {
			for offsetY := range 3 {
				if point := image.Pt(x+offsetX-1, y+offsetY-1); point.In(icon.Bounds()) {
					icon.Set(point.X, point.Y, green)
				}
			}
		}
	}
	for step := range 5 {
		paint(2+step, 8+step)
	}
	for step := range 9 {
		paint(6+step, 12-step)
	}
	return walk.NewBitmapFromImageForDPI(icon, 96)
}
func (ui *playerWindow) ensureProfileStorageDirectory() error {
	localAppData, err := localApplicationData()
	if err != nil {
		return err
	}
	directory, present, err := loadProfileStorageDirectory(localAppData)
	if err != nil {
		return err
	}
	if present {
		ui.profileDirectory = directory
		return nil
	}
	legacy := defaultProfileStorageDirectory(localAppData)
	if info, err := os.Stat(legacy); err == nil && info.IsDir() {
		ui.profileDirectory = legacy
		return nil
	}
	directory, err = pickWindowsFolder(ui.window.Handle(), "")
	if err != nil {
		return err
	}
	directory, err = saveProfileStorageDirectory(localAppData, directory)
	if err != nil {
		return err
	}
	ui.profileDirectory = directory
	return nil
}

func (ui *playerWindow) chooseProfileStorageDirectory() {
	if ui.busy {
		return
	}
	localAppData, err := localApplicationData()
	if err != nil {
		ui.finishFailure("Profile storage folder was not accepted", err)
		return
	}
	directory, err := pickWindowsFolder(ui.window.Handle(), ui.profileDirectory)
	if err != nil {
		if !errors.Is(err, errFolderSelectionCanceled) {
			ui.finishFailure("Profile storage folder was not accepted", err)
		}
		return
	}
	directory, err = saveProfileStorageDirectory(localAppData, directory)
	if err != nil {
		ui.finishFailure("Profile storage folder was not accepted", err)
		return
	}
	ui.profileDirectory = directory
	ui.headline.SetText("Profile storage folder saved")
	ui.detail.SetText("Profiles, mods, and Valheim Profile Sync will use:\r\n\r\n" + directory)
	ui.refreshInstalledVersion()
}

func (ui *playerWindow) handleAction() {
	if ui.complete {
		ui.window.Close()
		return
	}
	if err := ui.ensureProfileStorageDirectory(); err != nil {
		if !errors.Is(err, errFolderSelectionCanceled) {
			ui.finishFailure("Profile storage folder was not accepted", err)
		}
		return
	}
	ui.install()
}
func (ui *playerWindow) install() {
	if ui.busy {
		return
	}
	ui.busy = true
	ui.action.SetEnabled(false)
	ui.choose.SetEnabled(false)
	ui.resetActivityLog()
	ui.setProgress(progressUpdate{Stage: "Installing Valheim Profile Sync", Detail: "Registering your browser profile links.", Percent: 15})
	go func() {
		installed, err := installCurrentApplication()
		if err != nil {
			ui.finishFailure("Installation could not finish", err)
			return
		}
		ui.window.Synchronize(func() {
			ui.busy = false
			ui.headline.SetText("Valheim Profile Sync is installed")
			ui.progress.SetValue(100)
			ui.appendActivityLog("Installed successfully", "Application: "+installed)
			ui.appendActivityLog("Next steps", "1. Return to the Valheim portal.\r\n2. Open your world and choose Desktop, Desktop VR-compatible, or VR headset.\r\n3. Select Install or update.\r\n4. The app will synchronize that profile and create a shortcut on your Desktop.\r\n5. Use the Desktop shortcut whenever you play; it checks for updates, then launches Valheim.")
			if !ui.hasValidSteamValheimDirectory() {
				ui.appendActivityLog("Valheim folder required", "Choose the Valheim folder before closing this window. Done remains disabled until the selected folder contains valheim.exe.")
			}
			ui.showDone()
		})
	}()
}

func (ui *playerWindow) synchronize(request profileRequest) {
	if ui.busy {
		return
	}
	if err := ui.ensureProfileStorageDirectory(); err != nil {
		if !errors.Is(err, errFolderSelectionCanceled) {
			ui.finishFailure("Profile storage folder was not accepted", err)
		}
		return
	}
	ui.pendingRequest = &request
	gameDir, err := ui.resolveSteamValheimDirectory()
	if errors.Is(err, errFolderSelectionCanceled) {
		ui.showFolderSelectionNeeded()
		return
	}
	if err != nil {
		ui.finishFailure("Steam Valheim folder picker failed", err)
		return
	}
	ui.pendingRequest = nil
	ui.complete = false
	ui.busy = true
	ui.action.SetEnabled(false)
	ui.choose.SetEnabled(false)
	ui.resetActivityLog()
	ui.setProgress(progressUpdate{Stage: "Opening selected profile", Detail: request.Profile, Percent: 2})
	go func() {
		if _, err := installCurrentApplication(); err != nil {
			ui.finishFailure("Valheim Profile Sync could not register", err)
			return
		}
		_, err := synchronizeProfile(context.Background(), request, gameDir, true, ui.setProgress)
		if err != nil {
			var unavailable *serverUnavailableError
			if errors.As(err, &unavailable) {
				ui.finishFailure("Server unavailable", err)
				return
			}
			ui.finishFailure("Profile update stopped", err)
			return
		}
		ui.window.Synchronize(func() {
			ui.window.Close()
		})
	}()
}

func (ui *playerWindow) initializeSteamValheimDirectory() {
	localAppData, localErr := localApplicationData()
	if localErr == nil {
		if saved, present, err := loadSteamValheimDirectory(localAppData); err == nil && present {
			ui.setSteamValheimDirectory(saved)
			return
		}
	}
	if directory, present := findSteamValheimDirectory(likelySteamValheimDirectories()); present {
		if localErr == nil {
			_ = saveSteamValheimDirectory(localAppData, directory)
		}
		ui.setSteamValheimDirectory(directory)
		return
	}
	ui.setSteamValheimDirectory("")
}

func (ui *playerWindow) setSteamValheimDirectory(directory string) bool {
	validated, err := validateSteamValheimDirectory(directory)
	if err != nil {
		ui.gameDirectory = ""
		ui.choose.SetText("Choose Valheim folder…")
		ui.choose.SetImage(nil)
		ui.choose.SetToolTipText("Select the Steam Valheim folder containing valheim.exe.")
		if ui.doneNeedsGame {
			ui.action.SetEnabled(false)
		}
		return false
	}
	ui.gameDirectory = validated
	ui.choose.SetImage(ui.folderCheckmark)
	ui.choose.SetText("Valheim folder selected")
	ui.choose.SetToolTipText(validated)
	if ui.doneNeedsGame {
		ui.action.SetEnabled(true)
	}
	return true
}

func (ui *playerWindow) hasValidSteamValheimDirectory() bool {
	directory, err := validateSteamValheimDirectory(ui.gameDirectory)
	if err != nil {
		ui.setSteamValheimDirectory("")
		return false
	}
	ui.gameDirectory = directory
	return true
}

func (ui *playerWindow) showDone() {
	ui.complete = true
	ui.doneNeedsGame = true
	ui.choose.SetEnabled(true)
	ui.action.SetText("Done — close this window")
	ui.action.SetEnabled(ui.hasValidSteamValheimDirectory())
	ui.refreshInstalledVersion()
}

// refreshInstalledVersion re-reads the profile store so the version line does
// not keep advertising the release that was installed before this run.
func (ui *playerWindow) refreshInstalledVersion() {
	if ui.versionLabel == nil {
		return
	}
	_ = ui.versionLabel.SetText(currentInstalledProfileSummary())
}

func (ui *playerWindow) chooseSteamValheimDirectory() {
	if ui.busy {
		return
	}
	directory, err := ui.selectSteamValheimDirectory()
	if err != nil {
		if !errors.Is(err, errFolderSelectionCanceled) {
			ui.finishFailure("Steam Valheim folder was not accepted", err)
		}
		return
	}
	ui.setSteamValheimDirectory(directory)
	ui.headline.SetText("Steam Valheim folder saved")
	ui.detail.SetText("✅ Valheim installation verified.\r\n\r\nValheim Profile Sync will use:\r\n\r\n" + directory)
	if ui.pendingRequest != nil {
		request := *ui.pendingRequest
		ui.pendingRequest = nil
		ui.synchronize(request)
	}
}

func (ui *playerWindow) showFolderSelectionNeeded() {
	ui.busy = false
	ui.headline.SetText("Choose your Steam Valheim folder")
	ui.progress.SetValue(0)
	ui.detail.SetText("Nothing was changed.\r\n\r\nChoose Valheim folder to continue this profile update. The folder must contain valheim.exe. You can browse through This PC and select any drive.")
	ui.complete = true
	ui.doneNeedsGame = false
	ui.action.SetText("Close")
	ui.action.SetEnabled(true)
	ui.choose.SetEnabled(true)
}

func (ui *playerWindow) resolveSteamValheimDirectory() (string, error) {
	if ui.hasValidSteamValheimDirectory() {
		return ui.gameDirectory, nil
	}
	localAppData, err := localApplicationData()
	if err != nil {
		return "", err
	}
	directory, present, err := loadSteamValheimDirectory(localAppData)
	if err != nil {
		return "", err
	}
	if present {
		ui.setSteamValheimDirectory(directory)
		return directory, nil
	}
	if directory, present = findSteamValheimDirectory(likelySteamValheimDirectories()); present {
		if err := saveSteamValheimDirectory(localAppData, directory); err != nil {
			return "", err
		}
		ui.setSteamValheimDirectory(directory)
		return directory, nil
	}
	return ui.selectSteamValheimDirectory()
}

func (ui *playerWindow) selectSteamValheimDirectory() (string, error) {
	localAppData, err := localApplicationData()
	if err != nil {
		return "", err
	}
	initialDirectory := ui.gameDirectory
	if initialDirectory == "" {
		if saved, present, loadErr := loadSteamValheimDirectory(localAppData); loadErr != nil {
			return "", loadErr
		} else if present {
			initialDirectory = saved
		}
	}
	for {
		directory, pickErr := pickWindowsFolder(ui.window.Handle(), initialDirectory)
		if pickErr != nil {
			return "", pickErr
		}
		directory, validationErr := validateSteamValheimDirectory(directory)
		if validationErr != nil {
			walk.MsgBox(
				ui.window,
				"Choose the Valheim game folder",
				"That folder does not contain valheim.exe.\r\n\r\nChoose the existing Steam Valheim folder, usually inside steamapps\\common\\Valheim.",
				walk.MsgBoxIconWarning,
			)
			initialDirectory = directory
			continue
		}
		if err := saveSteamValheimDirectory(localAppData, directory); err != nil {
			return "", err
		}
		return directory, nil
	}
}

func (ui *playerWindow) setProgress(update progressUpdate) {
	ui.window.Synchronize(func() {
		// The marquee is the whole point of an indeterminate stage: a bar parked at
		// 97% for two minutes reads as frozen, a moving one reads as working.
		if ui.progress.MarqueeMode() != update.Indeterminate {
			_ = ui.progress.SetMarqueeMode(update.Indeterminate)
		}
		if !update.Indeterminate && update.Percent >= 0 && update.Percent <= 100 {
			ui.progress.SetValue(update.Percent)
		}
		ui.headline.SetText(update.Stage)
		if !update.Quiet {
			ui.appendActivityLog(update.Stage, update.Detail)
		}
		// One batch per poll, so mirroring the game log costs a single UI update
		// rather than one per line.
		if len(update.LogLines) > 0 {
			ui.appendActivityLines(update.LogLines)
		}
		if update.Terminal {
			ui.busy = false
			ui.showDone()
		}
	})
}

func (ui *playerWindow) resetActivityLog() {
	ui.detail.SetText("")
	ui.activityLines = 0
}

func (ui *playerWindow) appendActivityLog(stage, detail string) {
	stage = strings.TrimSpace(stage)
	detail = strings.TrimSpace(detail)
	if stage == "" && detail == "" {
		return
	}
	line := stage
	if line != "" && detail != "" {
		line += ": " + detail
	} else if detail != "" {
		line = detail
	}
	ui.appendActivityLines([]string{line})
}

// appendActivityLines writes lines to the activity log exactly as given and keeps
// the newest visible.
func (ui *playerWindow) appendActivityLines(lines []string) {
	if len(lines) == 0 {
		return
	}
	var builder strings.Builder
	for _, line := range lines {
		if ui.activityLines > 0 || builder.Len() > 0 {
			builder.WriteString("\r\n")
		}
		builder.WriteString(line)
		ui.activityLines++
	}
	ui.detail.AppendText(builder.String())
	end := ui.detail.TextLength()
	ui.detail.SetTextSelection(end, end)
	ui.detail.ScrollToCaret()
}

func (ui *playerWindow) finishFailure(title string, err error) {
	ui.window.Synchronize(func() {
		ui.busy = false
		ui.headline.SetText(title)
		ui.progress.SetValue(0)
		ui.appendActivityLog("Failed", fmt.Sprintf("%s\r\n\r\nNo profile was replaced. Your previous working profile remains available.\r\n\r\nWhat to do next:\r\n• Confirm Steam is signed in.\r\n• Confirm your account has access to this world.\r\n• Check your internet connection and try the profile link again.\r\n\r\nTechnical detail: %v", title, err))
		ui.complete = true
		ui.doneNeedsGame = false
		ui.action.SetText("Close")
		ui.action.SetEnabled(true)
		ui.choose.SetEnabled(true)
	})
}

func startWindowsFlow(ui *playerWindow, args []string) {
	if len(args) == 0 || (len(args) == 1 && args[0] == "--install") {
		ui.handleAction()
		return
	}
	if len(args) != 1 {
		ui.finishFailure("This profile link is invalid", fmt.Errorf("Valheim Profile Sync received an unexpected command"))
		return
	}
	request, err := parseProfileURL(args[0])
	if err != nil {
		ui.finishFailure("This profile link is invalid", err)
		return
	}
	ui.synchronize(request)
}
