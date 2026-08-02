//go:build !windows

package main

import "errors"

// The launcher only ever starts the game on Windows. These return an error rather
// than a plausible-looking `false`, so callers skip the check instead of believing
// that nothing is running - a false negative here would make awaitGameVisible poll
// for its whole timeout on every non-Windows build.
var errProcessInspectionUnsupported = errors.New("process inspection is only supported on Windows")

func processRunning(string) (bool, error) { return false, errProcessInspectionUnsupported }

func checkValheimRunning() (bool, error) { return false, errProcessInspectionUnsupported }
