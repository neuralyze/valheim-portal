package app

import (
	"os"

	"github.com/neuralyze/valheim-portal/internal/worldintel"
	"path/filepath"
	"testing"
)

// The server plugin writes down the pairing the portal could never work out for itself: which player
// id, stamped on every piece, belongs to which character name. This checks the portal reads it, and
// that it refuses the two values that would turn a real name into a false claim.
func TestNamesReportedByTheServerAreRead(t *testing.T) {
	server := testServer(t)
	world := "Midgard"
	directory := filepath.Join(server.cfg.MapSourceRoot, world, "config_merged")
	if err := os.MkdirAll(directory, 0o755); err != nil {
		t.Fatal(err)
	}
	identities := `{"schema":1,"updated":"2026-08-16T00:00:00Z","players":[
		{"player_id":308095166,"name":"Kato"},
		{"player_id":0,"name":"nobody"},
		{"player_id":2387859451,"name":""}
	]}`
	if err := os.WriteFile(filepath.Join(directory, "player_identities.json"), []byte(identities), 0o644); err != nil {
		t.Fatal(err)
	}

	names := server.reportedPlayerNames(world)

	if names[308095166] != "Kato" {
		t.Errorf("reported name = %q, want Kato", names[308095166])
	}
	// Player id 0 is the absence of a stamp, so a name against it would put a person on generated ruins.
	if _, ok := names[0]; ok {
		t.Error("a name was accepted for pieces that carry no player id")
	}
	// An empty name is nothing to show, and would render as a blank legend row.
	if _, ok := names[2387859451]; ok {
		t.Error("an empty name was accepted")
	}
	// A world with no plugin deployed is the normal case, not a failure.
	if got := server.reportedPlayerNames("Asgard"); got != nil {
		t.Errorf("a world without the plugin returned %v, want nil", got)
	}
}

// An operator's label outranks the reported name: a character can be renamed while its id stays put,
// and the operator may deliberately want to call somebody something else.
func TestAnOperatorLabelBeatsTheReportedName(t *testing.T) {
	server := testServer(t)
	world := "Midgard"
	if err := server.store.UpsertPublicWorld(t.Context(), PublicWorld{
		Name: world, JoinAddress: "valheim.example.test:2456", Status: "online",
	}, "test"); err != nil {
		t.Fatal(err)
	}
	directory := filepath.Join(server.cfg.MapSourceRoot, world, "config_merged")
	if err := os.MkdirAll(directory, 0o755); err != nil {
		t.Fatal(err)
	}
	identities := `{"schema":1,"players":[{"player_id":111,"name":"Kato"},{"player_id":222,"name":"Yngaelir"}]}`
	if err := os.WriteFile(filepath.Join(directory, "player_identities.json"), []byte(identities), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := server.store.SetBuilderLabel(t.Context(), world, 111, "The smith", "operator"); err != nil {
		t.Fatal(err)
	}
	snapshot := analysedSnapshotWithBuilders(world, []int64{111, 222})

	rows, _ := server.builderLegend(t.Context(), world, snapshot)

	byCreator := map[int64]pageBuilder{}
	for _, row := range rows {
		byCreator[row.Creator] = row
	}
	if row := byCreator[111]; row.Label != "The smith" || row.Reported {
		t.Errorf("row = %+v, want the operator's label and no reported flag", row)
	}
	// And where the operator said nothing, the game's own answer is used and marked as such.
	if row := byCreator[222]; row.Label != "Yngaelir" || !row.Reported {
		t.Errorf("row = %+v, want the reported name marked as reported", row)
	}
}

// analysedSnapshotWithBuilders is the smallest snapshot that produces one legend row per builder: the
// legend is built from clusters, so each builder needs one.
func analysedSnapshotWithBuilders(world string, creators []int64) worldintel.Snapshot {
	snapshot := worldintel.Snapshot{World: world}
	for index, creator := range creators {
		snapshot.Clusters = append(snapshot.Clusters, worldintel.Cluster{
			ID:      index + 1,
			Center:  worldintel.Vec3{X: float32(index * 100), Z: float32(index * 100)},
			Pieces:  10 + index,
			Creator: creator,
		})
	}
	return snapshot
}
