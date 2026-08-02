//go:build !windows

package agent

import (
	"os/exec"
	"syscall"
)

// An operation script backgrounds its own helpers - docker, steamcmd, tar - so
// signalling the script alone leaves those holding the world directory open
// long after the agent has given up on the job. Giving the script its own
// process group turns the teardown into one signal instead of a process tree
// walk that races the children it is trying to enumerate.
func useProcessGroup(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
}

func terminateProcessGroup(cmd *exec.Cmd) error {
	return syscall.Kill(-cmd.Process.Pid, syscall.SIGTERM)
}

func killProcessGroup(cmd *exec.Cmd) error {
	return syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
}
