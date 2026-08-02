//go:build windows

package agent

import (
	"errors"
	"os/exec"
)

// The agent runs the host's bash scripts over a unix socket and is only ever
// deployed on Linux. This file exists so the module builds and vets under
// GOOS=windows for contributors working on that platform; it is not a
// supported Windows agent.
//
// Windows has no process group that one signal tears down the way setpgid plus
// kill(-pid) does. Rather than report a teardown that never happened, the two
// kill calls surface the gap and combinedOutput falls back to killing the
// child alone - which is enough to unblock its wait, but leaves any helpers
// the script spawned running.
var errProcessGroupUnsupported = errors.New("process group control is only supported on unix")

func useProcessGroup(*exec.Cmd) {}

func terminateProcessGroup(*exec.Cmd) error { return errProcessGroupUnsupported }

func killProcessGroup(*exec.Cmd) error { return errProcessGroupUnsupported }
