# network — a connected, lived-in world layout per progression tier

`tools/jumpstart/worlds/<World>/<preset>/placements.yaml` gives each preset **one**
base. This directory adds the three things a lived-in world needs beyond that:

1. an **installation SET** per tier — farms, kilns, smelting yards, animal pens,
   boathouses, a mountain furnace camp, portal hubs, roadside shelters;
2. a **portal GRAPH** — nodes, tagged pairwise edges, and an honest piece count;
3. **ROADS** — routed over real terrain, bridging water where it must.

Nothing here places anything in a live world. It produces a design, data and a plan.
Putting the plan in the ground is a separate, operator-approved step **from the
`ulfsland-admin` client seat**.

## Layout

| File | Role |
| --- | --- |
| `data/installations.yaml` | **The model.** 35 installations, `first_tier` each, blueprint by manifest name + sha256, biome/terrain requirement, station levels, purpose and justification. Versioned data only. |
| `data/road_policy.yaml` | Water datum, grade budget, the bridge span ladder, the roadway cost model, location keep-outs, portal topology. Every number carries its provenance. |
| `catalogue.py` | Loads and **validates** the model: resolves blueprints against the library manifest, derives the per-tier set by accumulation, checks station levels against the presets' own ladder, checks crops against their biome. |
| `portals.py` | Builds the portal graph and reports the piece shortfall where a blueprint does not contain the portals the graph needs. |
| `terrain.py` | Thin adapter over `tools/jumpstart/blueprints/run_patchscan.sh` + `site_finder.load_patches`. River-inclusive, point-exact. **The only sampler.** |
| `sites.py` | Picks and verifies a concrete point for an installation inside a sampled corridor; derives the `doorstep`. |
| `router.py` | A* road router with bridge-aware water cost, grade budget and location avoidance. |
| `plan.py` | Piece-by-piece plan, RCON `spawn` lines, and the ZDO cost model. |
| `cli.py` | `tiers`, `installations`, `portals`, `route`, `network`. |
| `data/<World>/network-*.json` | Generated. Disposable: re-run the CLI. |

Tests: `tools/test_jumpstart_network.py` (30 cases, no game install needed).

## Usage

```bash
# the per-tier set and portal summary
tools/jumpstart/network/cli.py tiers

# what exists at pre-moder
tools/jumpstart/network/cli.py installations --tier 4

# the portal graph at the Deep North sandbox
tools/jumpstart/network/cli.py portals --tier 9

# the operator's acceptance case: farm -> boathouse -> mountain furnace camp
tools/jumpstart/network/cli.py route --world Ulfsland \
  --chain meadows-farm,early-dock,mountain-furnace-camp \
  --box=-1080,120,-300,500 \
  --hint=meadows-farm=-300,150 \
  --hint=early-dock=-940,-40 \
  --hint=mountain-furnace-camp=-780,120

# the same, as the RCON spawn plan
... --rcon

# everything for one tier, written to data/<World>/
tools/jumpstart/network/cli.py network --tier 4 --world Ulfsland --chain ... --write
```

`route` and `network --chain` boot a **sandbox copy** of the dedicated server on port
3457 with its own savedir to sample terrain, and kill it. No live mod, config, world
or profile is touched. It takes about 20 s for a 1.92 M-sample corridor.

## The four numbers that matter

### 1. The water datum is 30, not 0

Valheim's water plane is `y = 30.0` in `WorldGenerator.GetHeight` units, **absolute**.
A solver in this tree used `0.0` and put 10 of 13 placements under the sea. So:

* the constant is **imported** from `tools/jumpstart/blueprints/site_finder.WATER_LEVEL`
  and never restated in code;
* comparisons are expressed as **freeboard**, not "above water": a road node needs
  `freeboard >= 1.5 m`, a structure or doorstep `>= 2.0 m`;
* `test_water_datum_is_thirty_not_zero` and `test_crosses_water_with_a_bridge` both
  fail if it regresses — a 0 datum makes a river bed look like a hill and the router
  emits no bridge at all.

### 2. Heights must be river-inclusive

`tools/seedscan/run_scan.sh` omits `Pregenerate()`, so rivers and lakes are invisible
to it. MEASURED on Pirate68 at `(-308, 172)`: the 8 m seedscan grid says **48.29 m**,
the river-inclusive 1 m sample says **35.43 m**. A 12.9 m error, and the difference
between a meadow and a river valley. Every decision here goes through `terrain.py`,
which drives PatchScan with `World.m_menu` false so `Pregenerate()` runs.

Point exactness: step `1.0`, integer `half`, half-integer centre (`round(x) + 0.5`),
so every sample lands exactly on an integer world coordinate. `terrain.Window`
enforces it rather than documenting it.

### 3. The road grade budget is 0.30 m/m, and it is measured

MEASURED on Pirate68, river-inclusive, 1 m lattice, across 8 ZoneSystem-placed
settlement locations — the ground **the game itself** chose to put a settlement on.
Absolute 1 m rise/run within 20 m of each placement:

| location | median | p90 | p99 | max |
| --- | --- | --- | --- | --- |
| StoneHouse3 (526, 249) | 0.180 | 0.487 | 1.884 | 2.201 |
| StoneHouse4 (381, −459) | 0.155 | 0.359 | 0.557 | 0.717 |
| StoneHouse3 (−748, −14) | 0.186 | 0.430 | 0.604 | 0.805 |
| StoneHouse3 (−498, 570) | 0.177 | 0.388 | 0.589 | 0.761 |
| StoneHouse4 (279, 791) | 0.168 | 0.417 | 0.591 | 0.742 |
| Greydwarf_camp1 (13, −886) | 0.155 | 0.360 | 0.529 | 0.665 |
| Greydwarf_camp1 (−636, 953) | 0.140 | 0.349 | 0.526 | 0.715 |
| Greydwarf_camp1 (−1150, 143) | 0.181 | 0.415 | 0.594 | 0.762 |

So settled ground has a median grade of ~0.17 and a p90 of 0.35–0.49. A road at
**0.30 m/m** is therefore flatter than 90 % of the ground the game considers
settleable, which is what "reads as built" means numerically. **0.45 m/m** is the
mountain-spur limit (foot and pack only — a cart jams), sitting at roughly the
measured p95. **1.0 m/m** is refused outright; the harness's own impassability
threshold (`worlds/derive.py`'s walk check) is 1.5 m/m, so anything between 1.0 and
1.5 would be a cliff that merely happens to be climbable.

Note what the budget does NOT mean: a steep hillside is **switchbacked**, not
refused. `earthworks_weight` prices excess grade as cut-and-fill, so A* zigzags
across a 0.5 m/m slope to hold 0.30, and only goes over budget where there is no room
— in which case `over_budget_m` and `cut_fill_vertical_m` say how much.

### 4. Terrain costs zero ZDOs; pieces cost one each

MEASURED 2026-09-14 with `internal/worldintel.ParseDB` over read-only copies of three
real fleet-world backups:

| world | objects | construction | zones | TerrainModifier | `_TerrainCompiler` | `piece_pavedroad` |
| --- | --- | --- | --- | --- | --- | --- |
| Vangard | 34,871 | 32,704 | 3,880 | **0** | **0** | **0** |
| Storgard | 75,547 | 72,326 | 5,852 | **0** | **0** | **0** |
| Hrafnheim | 1,366 | 794 | 798 | **0** | **0** | **0** |

111,784 persistent objects and 105,824 construction pieces across 10,530 generated
zones, and not one terrain object. Storgard's 732 distinct prefabs contain nothing
matching terrain / paved / path / road / dirt / cultivate.

**Hoe work — levelling, paving, pathing, cultivating — costs zero persistent ZDOs.**
The deltas live in the zone's own heightmap and paint arrays, a fixed per-zone cost
whether you edit one square metre or all 4,096. Pieces cost one ZDO each.

That inverts the naive plan. A 3 km carriageway decked in `stone_floor_2x2`, two
pieces wide, is ~3,000 ZDOs — more construction than the whole of Hrafnheim, for a
road. The same 3 km hoed as a paved ribbon is **zero**, and the only ZDOs are the
marker posts, the decking at bridges and junctions, and the bridges.

So the recommendation is: **the road is terrain.** `pave_spacing_m: 4.0` hoe stamps
along the centreline (`piece_pavedroad` paints ~2 m radius, so 4 m spacing gives a
continuous 4 m ribbon), a `wood_pole` every 40 m with a `piece_groundtorch_wood`
every third one so it reads as a road at night and at distance, `stone_floor_2x2`
aprons only at bridge abutments and junctions, and a `PiNoKi_SmallHut` shelter every
400 m.

**Consequence, inferred from the same measurement:** `piece_pavedroad` has no
persistent `ZNetView`, which is why it never appears in a save — so
`spawn piece_pavedroad x y z` over RCON cannot pave. There is nothing for the spawn
to leave behind. **Road terrain work is admin-client-seat only.** That is the
operator's hard rule anyway; it is now also a technical fact. RCON can place the
markers, the aprons and the piers, and it cannot place the road.

## Bridges

The interesting decision is that **water is not a wall**. A crossing is priced as
`2 × abutment_cost + span × cost_per_m + depth`, so the search pays a large fixed
price to enter the water and a steep price per metre of it. A* therefore walks up to
a kilometre out of its way to find a **narrows** — which is what a road builder does,
and it is why the emitted spans are short enough for the library's 26 m bridge to
actually reach.

Span selection is from the ladder in `road_policy.yaml`. `span_m` is derived by rule,
not by eye: with `aspect = long/short >= 2.0` the deck clearly runs along the long
axis, so the span is the long axis; below 2.0 the deck's orientation inside a
near-square bounding box is **not determinable from the manifest**, and the entry is
marked unverified and excluded from automatic selection.

Only **two** rungs survive that test:

| blueprint | span | width | pieces | why |
| --- | --- | --- | --- | --- |
| `speeds-bridge2.blueprint` | 25.9 m | 4.0 m | 63 | the workhorse: 3 prefabs, PLACES_CLEAN, pier it for longer gaps |
| `long-bridge.blueprint` | 89.3 m | 41.6 m | 814 | the only verified span above 26 m |

Excluded, with the reason recorded in the policy: `bridge.vbuild` and
`salty-dick-bridge-curved-final` (near-square bounding box, deck orientation
unknown), `god-ponte-plus` (`discarded_rows: 2` and an unknown `#Terrain` section —
what places is not what the manifest measured), `brokkr-broken-bridge-2-short`
(`PLACES_WITH_GAPS` — a bridge with missing pieces is a hole).

Selection rule: the smallest verified span `>= crossing + 2 × abutment_margin`.
Overshooting is free; undershooting is a hole. Beyond 89.3 m, pier out with
`speeds-bridge2` where the water is within the 12 m pier limit. Where neither works,
the crossing carries a `problem`, `Route.audit()` fails and `Plan.blockers` reports
it — **the router refuses rather than emitting a deck that does not reach.**

## The doorstep

Each installation the router solves gets a `doorstep`: the named point a player of
that tier spawns at. Contract agreed with `SpawnOnLand`:

```json
"doorstep": {
  "x": -784, "y": 88, "z": 75,
  "freeboard_m": 57.62,
  "preset": "pre-moder",
  "for_placement": "pre-moder#mountain-furnace-camp",
  "installation": "mountain-furnace-camp",
  "source": "network-router",
  "status": "preferred candidate; NOT consumed by worlds/derive.py as of 2026-09-14",
  "approach": {"ok": true, "length_m": 10.0, "min_freeboard_m": 57.6, "max_step_m": 0.4,
               "dry_to_footprint_edge": true},
  "approach_yaw_deg": 21.8,
  "approach_yaw_note": "router-only; do NOT feed to a spawn template"
}
```

* `x`/`y`/`z` are **integers**, Valheim world metres, `GetHeight` frame. Integers at
  the point of truth: a float that rounds into a bog is the failure mode.
* `freeboard_m = y − 30.0`, so `freeboard >= 2.0` and `y >= 32.0` are the same
  statement.
* **No `yaw` in the block.** ServerCharacters' `CharacterTemplate.yml` `spawn:` is a
  list of `{x, y, z}` integers and an unknown key is fatal to the whole template.
  `approach_yaw_deg` is a separate, router-only field.
* `for_placement` is **preset-qualified** as `<preset>#<id>`. `early-dock` is a
  placement id in *both* `pre-elder` and `deepnorth-sandbox`, and they solve to
  different sites, so a bare id is ambiguous across the tree.
* It is a **preferred candidate, and it is NOT CONSUMED YET.** As of 2026-09-14
  `worlds/derive.py` chooses the spawn itself from `placements.yaml` and reads
  nothing under this directory; wiring it in is a follow-up on `derive.py`'s
  `probe_spawn` (a candidate-priority pass that tries the doorstep first and falls
  through to the ring). Whatever happens, that 1 m probe stays authoritative.
* `approach` is the **dry walk** from the doorstep to the building's near edge,
  under the same rule as `derive.py`'s `dry_approach` (any water fails, >1.5 m rise
  per metre fails). This is the one part of the verdict a dry route cannot imply: if
  the building's near edge sits across a ditch from the road, the corridor is dry
  and the walk still fails.
* It is the road's first node on the door side, clamped to the stand-off band
  (`derive.py` `SPAWN_FOOTPRINT_CLEAR_M` .. `SPAWN_SEARCH_MAX_M`), so it sits on
  ground the router has already proven walkable end-to-end.
* **Every threshold is imported from `worlds/derive.py` by name** —
  `SPAWN_FOOTPRINT_CLEAR_M`, `SPAWN_SEARCH_MAX_M`, `SPAWN_FREEBOARD_M`,
  `SPAWN_DISC_R_M`, `SPAWN_DISC_FREEBOARD_M`, `SPAWN_PATH_FREEBOARD_M`,
  `SPAWN_PATH_STEP_M` — and `derive.SEA_LEVEL_M` is asserted equal to
  `site_finder.WATER_LEVEL` at import. The walk is a second *implementation* (a
  Corridor mosaic is not a VHPATCH1 patch) but not a second set of *numbers*:
  duplicating `0.5` and `1.5` as literals would mean raising the bar in `derive.py`
  leaves these doorsteps passing at the old one and silently disagreeing with its
  verdict — a smaller copy of the `WATER_LEVEL` story.
  `test_thresholds_come_from_derive_not_from_literals` raises the constants and
  asserts a previously-passing doorstep is refused.

## Blueprint licence discipline

This repo is published, and the fleet's blueprint corpus came from valheimians.com,
whose terms **expressly forbid redistribution**. Bodies are referenced by manifest
name and sha256 only; nothing here reads or vendors a body. `catalogue.py` resolves
the hash from `tools/jumpstart/library/data/library_manifest.json` so no human
maintains one, and `test_every_blueprint_reference_resolves_to_a_manifest_sha256`
asserts that a committed body appears only under a licence that permits it (MEASURED:
exactly 5 of the 176 rows are committed, all MIT or Unlicense).

## What this does not do

* It does not solve world-wide placements. `tools/jumpstart/blueprints/solve_placements.py`
  owns that. `sites.solve` only picks a point inside an already-sampled corridor,
  because a route needs correct endpoints and three of the installations the operator
  named (farm, boathouse, mountain furnace camp) are not in `placements.yaml` at all.
* It does not write `placements.yaml` or anything else outside this directory.
* It does not place anything in a live world.
