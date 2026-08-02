//go:build !windows

package main

import (
	"context"
	"flag"
	"fmt"
	"os"
)

func main() {
	if len(os.Args) > 1 && os.Args[1] == "--collect-diagnostics" {
		if err := runDiagnosticsCollector(os.Args[2:]); err != nil {
			fmt.Fprintln(os.Stderr, err)
		}
		return
	}
	flags := flag.NewFlagSet(applicationName, flag.ExitOnError)
	launch := flags.Bool("launch", false, "launch Valheim after synchronization")
	gameDir := flags.String("game-dir", "", "Steam Valheim directory")
	_ = flags.Parse(os.Args[1:])
	if flags.NArg() != 1 {
		fmt.Fprintln(os.Stderr, "Valheim Profile Sync is a Windows desktop application.")
		return
	}
	request, err := parseProfileURL(flags.Arg(0))
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return
	}
	_, err = synchronizeProfile(context.Background(), request, *gameDir, *launch, func(update progressUpdate) {
		fmt.Fprintln(os.Stderr, update.Stage+": "+update.Detail)
	})
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
	}
}
