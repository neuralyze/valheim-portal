//go:build !windows

package main

import (
	"errors"
	"syscall"
)

// processAliveByPID reports whether a process id still belongs to a running
// process. Signal 0 performs the permission and existence checks without
// delivering anything, so ESRCH is the only answer that means "gone": EPERM says
// the process exists but belongs to someone else.
func processAliveByPID(pid int) bool {
	if pid <= 0 {
		return false
	}
	err := syscall.Kill(pid, 0)
	return err == nil || errors.Is(err, syscall.EPERM)
}
