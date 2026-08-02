//go:build windows

package main

import (
	"os/exec"
	"syscall"

	"golang.org/x/sys/windows"
)

// hiddenCommand runs a console helper without letting it put a window on screen.
//
// The launcher is linked with -H=windowsgui and so owns no console. Every console
// child it starts therefore allocates a console of its own, which Windows shows as
// a command window. That is invisible for a one-shot call but not for a poll: the
// wait for the game window checks whether valheim.exe is alive on a timer, and
// without this each check flashed a fresh window over whatever the player was
// looking at.
//
// CREATE_NO_WINDOW suppresses the console for console subsystem programs;
// HideWindow covers a child that asks for a window of its own anyway. Output
// redirection is unaffected, so callers still read stdout normally.
func hiddenCommand(name string, arguments ...string) *exec.Cmd {
	command := exec.Command(name, arguments...)
	command.SysProcAttr = &syscall.SysProcAttr{
		HideWindow:    true,
		CreationFlags: windows.CREATE_NO_WINDOW,
	}
	return command
}
