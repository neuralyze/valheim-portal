package valheimvr

import "testing"

func TestIntegrationPackagesAreConfigured(t *testing.T) {
	if len(integrationPackages) != 4 {
		t.Fatalf("configured package count = %d, want 4", len(integrationPackages))
	}
	for _, identifier := range []string{
		"ValheimVR-ValheimVR",
		"geekstreet-BackpacksVRFix",
		"geekstreet-EpicLootVRFix",
		// CLLC needs a VR fix too; its absence let a VR package into a true-nonvr build.
		"geekstreet-CLLCVRFix",
	} {
		if !IsIntegrationPackage(identifier) {
			t.Fatalf("%q is missing from the configured VR package list", identifier)
		}
	}
	if IsIntegrationPackage("Azumatt-AzuCraftyBoxes") {
		t.Fatal("ordinary package is classified as a VR integration package")
	}
}
