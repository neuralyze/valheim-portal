//go:build !windows

package main

// The launcher only ever starts the game on Windows, so there is no window to
// look for here. Returning the same error the process check uses keeps callers
// on the single "inspection unsupported" path instead of waiting for a window
// that can never appear.
func checkGameWindowVisible() (bool, error) { return false, errProcessInspectionUnsupported }
