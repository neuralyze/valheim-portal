package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"strings"

	"github.com/neuralyze/valheim-portal/internal/worldintel"
)

// catalogPaths collects every -catalog flag. A 1.0 world needs MORE THAN ONE: the
// game assembly names vanilla prefabs, the SoftRef manifests name locations, and a
// mod-built world needs a name dump from its MOD BUNDLES as well - measured on
// Ulfsland, 90,740 of 91,734 objects carried one of only 563 prefab hashes that
// assembly_valheim.dll alone cannot name. A single-valued flag made that
// unexpressible, so the tool could not be pointed at the thing that fixes it.
type catalogPaths []string

func (c *catalogPaths) String() string { return strings.Join(*c, ",") }

func (c *catalogPaths) Set(value string) error {
	*c = append(*c, value)
	return nil
}

func main() {
	archive := flag.String("archive", "", "completed world backup archive")
	world := flag.String("world", "", "world name")
	var catalog catalogPaths
	flag.Var(&catalog, "catalog", "managed assembly, plugin DLL, prefab-name dump or directory; repeatable")
	flag.Parse()
	if *archive == "" || *world == "" {
		flag.Usage()
		os.Exit(2)
	}
	snapshot, err := worldintel.AnalyzeArchive(*archive, *world, worldintel.CatalogFromFiles(catalog...))
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if err := json.NewEncoder(os.Stdout).Encode(snapshot); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
