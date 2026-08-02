//go:build !windows

package main

import "os/exec"

// There is no console window to suppress off Windows, so this is a plain spawn.
func hiddenCommand(name string, arguments ...string) *exec.Cmd {
	return exec.Command(name, arguments...)
}
