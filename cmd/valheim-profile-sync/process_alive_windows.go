//go:build windows

package main

import "golang.org/x/sys/windows"

// stillActive is the exit code Windows reports for a process that has not exited
// (STATUS_PENDING). x/sys/windows does not export it.
const stillActive = 259

// processAliveByPID reports whether a process id still belongs to a running
// process. Used to tell a lock held by a live launcher from one left behind by a
// launcher that was closed mid-sync.
//
// A handle can still be opened for a process that has exited but is not yet
// reaped, so the exit code is what actually decides it.
func processAliveByPID(pid int) bool {
	if pid <= 0 {
		return false
	}
	handle, err := windows.OpenProcess(windows.PROCESS_QUERY_LIMITED_INFORMATION, false, uint32(pid))
	if err != nil {
		return false
	}
	defer windows.CloseHandle(handle)
	var code uint32
	if err := windows.GetExitCodeProcess(handle, &code); err != nil {
		// The process exists but its state cannot be read. Treat it as alive so a
		// lock is never stolen from a launcher that might still be working.
		return true
	}
	return code == stillActive
}
