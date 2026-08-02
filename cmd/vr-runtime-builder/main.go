package main

import (
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"

	"github.com/neuralyze/valheim-portal/internal/app"
)

func main() {
	input := flag.String("input", "", "validated ValheimVR release ZIP")
	output := flag.String("output", "", "immutable portal VR runtime ZIP")
	flag.Parse()
	if *input == "" || *output == "" || flag.NArg() != 0 {
		fatal(errors.New("usage: vr-runtime-builder --input <vhvr-release.zip> --output <vr-runtime.zip>"))
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
	in, err := os.Open(*input)
	if err != nil {
		fatal(err)
	}
	defer in.Close()
	temporary, err := os.CreateTemp(filepath.Dir(*output), ".vr-runtime-")
	if err != nil {
		fatal(err)
	}
	temporaryName := temporary.Name()
	defer os.Remove(temporaryName)
	if _, err := io.Copy(temporary, in); err != nil {
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
	if err := os.Rename(temporaryName, *output); err != nil {
		fatal(err)
	}
}

func fatal(err error) {
	fmt.Fprintln(os.Stderr, err)
	os.Exit(1)
}
