package valheimvr

import (
	_ "embed"
	"encoding/json"
	"fmt"
)

//go:embed packages.json
var packageConfigJSON []byte

type packageConfig struct {
	Schema   int      `json:"schema"`
	Packages []string `json:"packages"`
}

var integrationPackages = loadIntegrationPackages()

func IsIntegrationPackage(identifier string) bool {
	_, found := integrationPackages[identifier]
	return found
}

func loadIntegrationPackages() map[string]struct{} {
	var config packageConfig
	if err := json.Unmarshal(packageConfigJSON, &config); err != nil {
		panic(fmt.Sprintf("parse ValheimVR package config: %v", err))
	}
	if config.Schema != 1 || len(config.Packages) == 0 {
		panic("invalid ValheimVR package config")
	}
	packages := make(map[string]struct{}, len(config.Packages))
	for _, identifier := range config.Packages {
		if identifier == "" {
			panic("invalid ValheimVR package identifier")
		}
		if _, duplicate := packages[identifier]; duplicate {
			panic("duplicate ValheimVR package identifier")
		}
		packages[identifier] = struct{}{}
	}
	return packages
}
