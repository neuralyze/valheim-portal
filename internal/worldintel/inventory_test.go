package worldintel

import (
	"encoding/hex"
	"testing"
)

// chest109 is the real "items" byte array of one chest in Ulfsland's 1.0.12 save (world version 41),
// lifted verbatim out of chunk 20_20__1_9.chunk at byte 201365. It is the smallest useful fixture in
// that world: four vanilla stacks of 50, quality 1, full durability, crafted by "Server", at grid
// row 3 columns 0-3. Hand-writing a blob would only test this package's decoder against this
// package's idea of the format; this is what the game actually wrote.
const chest109 = "6d000000040010270000000300683200ffffffffffffffff0653657276657205c82e0c" +
	"0010270000010300683200ffffffffffffffff06536572766572f70f73d4" +
	"0010270000020300683200ffffffffffffffff06536572766572576e14e0" +
	"0010270000030300683200ffffffffffffffff06536572766572c324f3f600"

// TestParseInventoryCompact109 is the regression for the measurement that made 112 filled containers
// on Ulfsland report as empty: every one of them stores inventory version 109, the reader accepted
// only 100-106, and so inventory_objects, inventory_stacks and inventory_items all read zero while
// 34,701 items sat in the chunk files.
func TestParseInventoryCompact109(t *testing.T) {
	data, err := hex.DecodeString(chest109)
	if err != nil {
		t.Fatalf("fixture is not hex: %v", err)
	}
	catalog := map[int32]string{
		StableHash("Coal"):  "Coal",
		StableHash("Resin"): "Resin",
		StableHash("Stone"): "Stone",
		StableHash("Wood"):  "Wood",
	}
	inventory, err := parseInventory(data, catalog)
	if err != nil {
		t.Fatalf("parseInventory: %v", err)
	}
	if inventory.Version != 109 {
		t.Errorf("version = %d, want 109", inventory.Version)
	}
	want := []InventoryItem{
		{Name: "Coal", PrefabHash: StableHash("Coal"), Stack: 50, Quality: 1},
		{Name: "Resin", PrefabHash: StableHash("Resin"), Stack: 50, Quality: 1},
		{Name: "Stone", PrefabHash: StableHash("Stone"), Stack: 50, Quality: 1},
		{Name: "Wood", PrefabHash: StableHash("Wood"), Stack: 50, Quality: 1},
	}
	if len(inventory.Items) != len(want) {
		t.Fatalf("got %d stacks, want %d", len(inventory.Items), len(want))
	}
	for i, item := range inventory.Items {
		if item != want[i] {
			t.Errorf("item %d = %+v, want %+v", i, item, want[i])
		}
	}
}

// TestParseInventoryRejectsUnknownVersion keeps the ceiling honest. 109 is the newest version
// assembly_valheim 1.0.12 can write (Version.Item.ChunksNCheats); a save claiming 110 has a layout
// nothing here has measured, and guessing at it would put invented item counts on the map.
func TestParseInventoryRejectsUnknownVersion(t *testing.T) {
	data := []byte{110, 0, 0, 0, 0, 0}
	if _, err := parseInventory(data, nil); err == nil {
		t.Fatal("parseInventory accepted inventory version 110")
	}
}
