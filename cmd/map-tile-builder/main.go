package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"runtime"

	"github.com/neuralyze/valheim-portal/internal/maptiles"
)

func main() {
	var options maptiles.BuildOptions
	var root, input string
	flag.StringVar(&root, "root", "", "map artifact root")
	flag.StringVar(&input, "input", "", "authoritative terrain PNG")
	flag.StringVar(&options.HeightPath, "height-input", "", "authoritative 16-bit height PNG")
	flag.StringVar(&options.World, "world", "", "world name")
	flag.StringVar(&options.Seed, "seed", "", "world seed")
	flag.IntVar(&options.WorldGenVersion, "worldgen-version", 0, "Valheim world generation version")
	flag.IntVar(&options.Size, "size", maptiles.DefaultSize, "maximum pyramid width and height")
	flag.IntVar(&options.Workers, "workers", min(runtime.GOMAXPROCS(0), 8), "bounded rendering workers")
	flag.Parse()
	if root == "" || input == "" || options.HeightPath == "" || options.World == "" || options.Seed == "" || options.WorldGenVersion <= 0 {
		flag.Usage()
		os.Exit(2)
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	defer stop()
	manifest, err := maptiles.Build(ctx, root, input, options)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if err := json.NewEncoder(os.Stdout).Encode(manifest); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
