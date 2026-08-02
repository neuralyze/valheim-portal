package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"

	"github.com/neuralyze/valheim-portal/internal/worldintel"
)

func main() {
	archive := flag.String("archive", "", "completed world backup archive")
	world := flag.String("world", "", "world name")
	catalog := flag.String("catalog", "", "optional managed assembly or plugin DLL")
	flag.Parse()
	if *archive == "" || *world == "" {
		flag.Usage()
		os.Exit(2)
	}
	paths := []string{}
	if *catalog != "" {
		paths = append(paths, *catalog)
	}
	snapshot, err := worldintel.AnalyzeArchive(*archive, *world, worldintel.CatalogFromFiles(paths...))
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if err := json.NewEncoder(os.Stdout).Encode(snapshot); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
