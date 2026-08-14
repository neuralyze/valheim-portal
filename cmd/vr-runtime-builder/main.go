package main

import (
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	"github.com/neuralyze/valheim-portal/internal/app"
)

type excludeList []string

func (e *excludeList) String() string { return strings.Join(*e, ",") }

func (e *excludeList) Set(value string) error {
	*e = append(*e, value)
	return nil
}

func main() {
	input := flag.String("input", "", "validated ValheimVR release ZIP")
	output := flag.String("output", "", "immutable portal VR runtime ZIP")
	var excludes excludeList
	flag.Var(&excludes, "exclude", "BepInEx plugin folder to leave out; repeatable")
	flag.Parse()
	if *input == "" || *output == "" || flag.NArg() != 0 {
		fatal(errors.New("usage: vr-runtime-builder --input <vhvr-release.zip> --output <vr-runtime.zip> [--exclude <plugin-folder>]"))
	}
	if filepath.Clean(*input) == filepath.Clean(*output) {
		fatal(errors.New("input and output must differ"))
	}
	if err := app.ValidateVRRuntimeArtifact(*input); err != nil {
		fatal(fmt.Errorf("validate input: %w", err))
	}
	if err := os.MkdirAll(filepath.Dir(*output), 0o750); err != nil {
		fatal(err)
	}
	temporary, err := os.CreateTemp(filepath.Dir(*output), ".vr-runtime-")
	if err != nil {
		fatal(err)
	}
	temporaryName := temporary.Name()
	defer os.Remove(temporaryName)
	dropped := 0
	if len(excludes) == 0 {
		in, openErr := os.Open(*input)
		if openErr != nil {
			temporary.Close()
			fatal(openErr)
		}
		defer in.Close()
		if _, err := io.Copy(temporary, in); err != nil {
			temporary.Close()
			fatal(err)
		}
	} else if dropped, err = app.CopyVRRuntimeExcluding(temporary, *input, excludes); err != nil {
		temporary.Close()
		fatal(err)
	}
	if err := temporary.Chmod(0o640); err != nil {
		temporary.Close()
		fatal(err)
	}
	if err := temporary.Close(); err != nil {
		fatal(err)
	}
	// The rewritten archive is validated as bytes on disk rather than as the entries just
	// written, so an exclusion cannot produce a runtime the portal would later refuse.
	if err := app.ValidateVRRuntimeArtifact(temporaryName); err != nil {
		fatal(fmt.Errorf("validate output: %w", err))
	}
	if err := os.Rename(temporaryName, *output); err != nil {
		fatal(err)
	}
	if len(excludes) > 0 {
		fmt.Printf("excluded %s: dropped %d entries\n", strings.Join(excludes, ", "), dropped)
	}
}

func fatal(err error) {
	fmt.Fprintln(os.Stderr, err)
	os.Exit(1)
}
