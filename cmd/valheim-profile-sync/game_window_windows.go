//go:build windows

package main

import (
	"github.com/lxn/win"
	"golang.org/x/sys/windows"
)

// unityWindowClass is the class Unity gives its player window. Valheim is a
// Unity game, so its main window carries it.
const unityWindowClass = "UnityWndClass"

// checkGameWindowVisible reports whether a Unity player window is on screen.
//
// The process appearing is not the signal a player cares about: valheim.exe
// exists within a second of launch, while a 114-package profile can spend a
// minute patching before anything is drawn. Only a visible, non-minimized window
// means "the game is up".
//
// Callers only consult this after valheim.exe is known to be running, which is
// what keeps a class-name match from picking up an unrelated Unity title.
func checkGameWindowVisible() (bool, error) {
	class, err := windows.UTF16PtrFromString(unityWindowClass)
	if err != nil {
		return false, err
	}
	handle := win.FindWindow(class, nil)
	if handle == 0 {
		return false, nil
	}
	return win.IsWindowVisible(handle) && !win.IsIconic(handle), nil
}
