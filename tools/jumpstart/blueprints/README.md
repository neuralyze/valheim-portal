# Jumpstart base blueprints

Tiered base placement for the Ulfsland jumpstart. Claims are marked
**MEASURED** (observed on this box) or **INFERRED**.

## The headline: placements are solved, not hard-coded

A blueprint coordinate is **seed-specific**. The flat coastal meadow that suits
a Meadows starter hall in one seed is open ocean in the next, so a list of
coordinates is worthless the moment the world is re-rolled.

So `worlds/<World>/<preset>/placements.yaml` never treats a coordinate as the
source of truth. It records a **requirement** — biome, footprint, flatness,
minimum freeboard above sea level, coastal or not, which ZoneSystem anchor to
sit near, what feature the spot must serve — and one command turns every
requirement in the tree into concrete coordinates for whatever seed the world is
rolled onto:

```bash
VH_SRC=/media/big4/projects/game/valheim/Ulfsland/data/bepinex \
WORLD=tools/jumpstart/worlds/Ulfsland SEED=Pirate68 WRITE=1 \
  tools/jumpstart/blueprints/resolve.sh
```

That is the whole re-roll procedure. `resolve.sh` runs three stages and caches
the first two by seed:

1. `tools/seedscan/run_scan.sh` — 8 m biome + height grid (MEASURED: 15 s, 34 MB)
2. `tools/seedscan/run_locscan.sh` — ZoneSystem location dump (MEASURED: 75 s)
3. `solve_placements.py` — coarse search, 1 m patch scan, verdict (MEASURED: 28 s)

MEASURED end to end on Pirate68 from a cold cache: **116 s** for 13 placements
across 9 presets. Drop `WRITE=1` for a dry run that prints the same verdicts and
touches nothing. `--label alternative` records a seed that is not live under
`solved_alternatives[<seed>]` instead of overwriting `solved`.

The solver refuses to run if `--seed` disagrees with the seed baked into the
grid file, because mismatched coordinates are worse than none.

Every `solved` block carries a `satisfies_requirement` flag and, when that is
false, a `violations:` list naming each broken constraint with its required
value, its actual value and the size of the miss. A file that states a
requirement and a solution that disagree with each other is a bug in itself.
`--verify` re-checks every recorded block against its own requirement and the
seed's artefacts, and writes nothing:

```bash
tools/jumpstart/blueprints/solve_placements.py --verify \
  --world tools/jumpstart/worlds/Ulfsland --seed Pirate68 \
  --grid /tmp/jumpstart-solve/grid-<hash>/00000.biome \
  --locations /tmp/jumpstart-solve/loc/<hash>.json
```

## Compatibility: can Infinity Hammer read PlanBuild blueprints?

**Yes.** PlanBuild is not installed on this fleet — only its stale
`marcopogo.PlanBuild.cfg` remains — but only the file *format* matters.
All MEASURED from `InfinityHammer.dll` 1.83.0 (disassembled with `monodis`
against the game's own `assembly_valheim.dll` and BepInEx core):

* `HammerBlueprintCommand::LoadFiles` enumerates `*.blueprint` **and**
  `*.vbuild` with `SearchOption.AllDirectories`, from **both**
  `<game>/BepInEx/config/<folder>` and `Paths.ConfigPath/<folder>`.
* `<folder>` is the config entry *"6. Blueprints" / "Blueprint folder"*, whose
  default value is the literal string **`PlanBuild`**. Infinity Hammer reads the
  very directory PlanBuild uses — this is deliberate interop, not a coincidence.
* `GetPlanBuild` / `GetPlanBuildObject` parse the PlanBuild header keys
  `#Name:`, `#Creator:`, `#Description:`, `#Category:`, `#Center:`,
  `#Coordinates:`, `#Rotation:`, `#SnapPoints`, `#Pieces` (case-insensitive),
  plus Infinity Hammer's own `#TerrainHeight:` / `#TerrainPaint:`.
* Its changelog corroborates the intent: v1.79 and v1.80 both list *"Improves
  compatibility with PlanBuild mod"*.
* An unknown extension throws `Unknown file format.`; the legacy
  `#Height`/`#Paint` terrain sections are explicitly rejected with *"Legacy
  #Height/#Paint terrain format is no longer supported."*

Proof beyond the strings: **112 files from the fleet's own corpus parse
correctly** with a reader written from that disassembly, and the `.vbuild` field
layout I derived empirically is confirmed by Infinity Hammer's own
`GetBuildShareObject` — see *File formats* below.

### Server-side or admin-client only?

**Admin-client only, and that matches the operator's rule anyway.** MEASURED:
`hammer_restore`'s handler calls `InfinityHammer.Hammer::Equip()` as its first
action; `Equip` calls `ServerDevcommands.Helper::GetPlayer()`, searches that
player's inventory for a hammer, throws `Unable to find the hammer.` if there is
none, and otherwise calls `Humanoid::EquipItem`. There is no headless path
through Infinity Hammer. All building happens from the `ulfsland-admin` seat,
which is exactly the intended workflow.

For placement with **nobody online**, use `to_rcon_plan.py` (below).

## The two placement routes, and when each is right

| | Admin client (Infinity Hammer) | Headless (ValheimRcon `spawn`) |
|---|---|---|
| Command | `hammer_blueprint <file>` then place, or `hammer_restore <file>` at its saved position | one `spawn` per object, from `to_rcon_plan.py` |
| Fidelity | full — quaternion rotation, scale, object **data** (chest contents, sign text, ward permissions) with `data=true` | position + rotation only |
| Needs a player | **yes** — hammer equipped | **no** |
| Terrain | can apply the blueprint's captured `#TerrainHeight` / `#TerrainPaint` | none |
| WearNTear support | computed normally by the game | **not computed** — ghost-init writes the ZDO directly |
| Use when | the operator is building or fine-tuning interactively | seeding a freshly re-rolled world before anyone joins |

MEASURED, `spawn` resolves the prefab from `ZNetScene`, instantiates it, then
calls `ZNetView::FinishGhostInit()` and `Object::Destroy` — a persistent ZDO
with no live GameObject, which is why it works with an empty server.

### Deploying to the admin client

Infinity Hammer uses the **containing folder name as the blueprint's category**
(MEASURED: `GetFolderNameFromPath`). So copy a preset's directory across whole:

```
<preset>/blueprints/*  ->  <client>/BepInEx/config/PlanBuild/<preset>/
```

and the whole preset appears as one category in `hammer_menu blueprints`.

## Rotation convention

`placements.yaml` records rotation as `{units: euler_degrees_yxz, yaw, pitch, roll}`.

MEASURED: ValheimRcon's `spawn ... -rotation <x> <y> <z>` passes the vector to
`UnityEngine.Quaternion::Euler`, which composes as `Ry(y) * Rx(x) * Rz(z)` —
the YXZ convention. Yaw is the **Y** component, in degrees, and `+yaw` turns the
building clockwise viewed from above. Only yaw is used in the placements; pitch
and roll stay 0 so buildings sit level.

`findObjects -detailed` reports rotation the same way — `Quaternion.eulerAngles`
formatted `0.##` — so the two agree.

Blueprints store a full **quaternion**, so `to_rcon_plan.py` converts. That
conversion is validated, not assumed: quaternion → Unity euler → quaternion
round-trips to a worst error of **3.4e-6 degrees** over 300 000 random
quaternions, over every exact gimbal-lock case, and over the 22.5-degree grid
Valheim building actually uses. (The gimbal-lock fold is a real trap: a naive
`asin` decomposition silently produces a ~52-degree error on roughly one
rotation in twenty thousand.)

## Origin alignment and the floor datum

Corpus blueprints disagree about their own origin. Of the 157 non-empty
`.blueprint` bodies, **99 are normalised so that `min(pivot Y)` is exactly 0**
and 58 are not, the latter spanning `min(pivot Y)` from **−5064.42** to
**+4.49** m. So a bare coordinate is ambiguous, and so is the minimum pivot.

`align: floor-center` (the default) centres the blueprint on the target X/Z and
drops its **floor plane** exactly onto the target Y. `floor` does the Y half
only; `raw` and `center` are also available. `ground` and `ground-center` are
the OLD rule and are kept only so the difference can be measured.

### Why the old rule was wrong

`ground-center` computed `dy = -min(pivot Y)` and called it "drop the lowest
piece onto the pad". Two defects, both MEASURED:

* **A pivot is not a surface.** From the game's own colliders, dumped by
  `PieceGeometry.cs` into `data/piece_geometry.json`, `stone_floor_2x2` is a
  2 × 2 × 1 m solid whose pivot sits at **mid-thickness** — its walkable top is
  `pivot + 0.500` and its underside `pivot − 0.500`. `stone_wall_1x1` is a 1 m
  cube pivoted at its centre. `wood_floor` is 0.130 m thick with its top at
  `pivot + 0.097`. `iron_floor_1x1`'s slab sits **below** its pivot entirely,
  top at `pivot − 0.450`. None of that is guessable.
* **On most of the corpus the shift did nothing at all.** Where
  `min(pivot Y) == 0` — 99 of 157 bodies — `dy` was exactly `0.0`, every time.

In live play on Ulfsland the `pre-bonemass/iron-era-workshop` pad was flattened
to 70.91 and the blueprint's local Y = 0 plane was dropped there, which put its
lowest walkable floor **1.50 m** above the pad, its main floor (124 m² at local
1.834) **1.83 m** above, and the median air gap under the structure — the
lowest solid in each 2 m column, over 337 occupied columns — **2.05 m**, with
the most common column value 2.1 m and the rear terrace at 6.02 m. Loose props
spawned by `terraform/stock.py` at the same 70.91 sat correctly, which is the
tell: the props used the pad datum and the structure did not.

### What the datum is

`base_geometry.floor_datum(objects)` returns a `FloorDatum` whose `base_y` is
the blueprint-local Y of the **lowest walkable floor surface**, computed from
each piece's real collider with the row's own rotation and scale applied. The
placement Y is then `pad_height − base_y`. Read the module header for the
alternatives considered (`lowest solid`, `support bottom`), what each would do
instead, the cases where the rule defers to a buried crafting station or skips a
sub-cellar slab, and what it refuses. Across the 174 catalogued bodies: 162
resolve, 12 refuse (all of them zero-piece files), and 101 have supports
correctly left below the chosen plane.

## Coordinates, the water plane, and freeboard

**Valheim's water plane is y = 30**, and `WorldGenerator.GetHeight` returns
ABSOLUTE heights, not heights relative to the surface. This section previously
said sea level was y = 0, and the solver believed it; `SpawnOnLand` caught it.
The mistake was reading the ocean FLOOR as the surface: Ocean-biome cells clamp
at max exactly 4.00, roughly 26 m below the water, and their median of 0.0 is
the bottom of that clamp.

Three independent MEASUREMENTS on Pirate68 put the plane at 30:

* Swamp heights span 27.46…33.84, median 29.84 — a narrow band straddling 30,
  which is exactly what a swamp is.
* Ocean clamps at 4.00; Meadows median is 30.61 and Plains 28.10.
* In the 12 301-instance ZoneSystem dump every land location type floors just
  above 30 — `Grave1` 30.50, `Ruin1` 31.02, `StoneHouse4` 31.04, `Crypt2`
  31.11 — because Valheim places land locations with `minAltitude` 1, while
  `ShipWreck01`…`04` sit at 29.0…30.9. The dump agrees with the grid to a
  median of 0.02 m, so both share this datum.

**What it cost.** Under the old datum "on land" meant "not 30 m under water",
and 10 of 13 solved sites were below the surface — `sandbox-harbour` by 22 m.
Any earlier height-derived claim in this project is suspect for the same reason;
biome-mask results are unaffected.

**Freeboard, not "above water".** A pad at +0.01 m is the waterline.
`min_height_m` (default `DEFAULT_FREEBOARD` = **1.0 m**) is the metres the
footprint's LOWEST cell must clear y = 30 by, and 1.0 is not a taste pick: it is
where Iron Gate itself draws the line, per the `minAltitude` 1 measurement
above. Every solved block records `freeboard_m`, the **minimum over the whole
footprint** measured on the 1 m river-inclusive patch. `y` is the footprint
**maximum** (the blueprint's lowest piece is dropped onto it), so the two differ
by the pad's relief and neither is a single-point height. `SpawnOnLand`'s
`spawn_freeboard_m` is a different quantity again: the margin at the one integer
coordinate a character wakes on.

Freeboard is **not** traded against flatness. A slope can be hoed; a lake bed
cannot be built on. The compromise search opens `min_height_m` only when the
seed contains no dry footprint of the required size at all, and records
`freeboard_ceiling_m` — the best freeboard available and how many dry footprints
exist — to prove it. MEASURED on Pirate68: 3 of 13 sites are unavoidably wet
(`complete-station-hub`, `iron-era-workshop`, `meadows-starter-hall`, all with
zero dry footprints available), down from 10 under the old datum.

**Rivers.** `tools/seedscan/run_scan.sh` defaults `SEEDSCAN_PREGEN=0`, and
`WorldGenerator.AddRivers` reads the dictionary `Pregenerate()` fills, so without
it the height plane has no rivers or lakes at all — MEASURED, the river-free grid
reports 33.97 m at (−275, 260) where the truth is 28.90 m, calling a river bed
dry land by 5 m. `resolve.sh` therefore scans with `PREGEN=1` (21 s against 15 s
for the whole world), and `solve_placements.py` PROBES the grid it is given:
256 points in the 25…60 m band are compared against 1 m river-inclusive patches
and the solve is refused if more than 2 % disagree by over a metre. MEASURED
separation: a river-free grid fails 40 of 256, a pregenerated one fails 0.

Grid orientation was verified the same way rather than assumed: `grid[z, x]`
agrees with the location dump's own biome field on **99.90 %** of 11 951
single-biome instances; `grid[x, z]` agrees on 16.82 %.

## How a site is picked

Search coarse, decide fine, rank rather than filter, and never claim more than
was measured.

### 1. Resolution: what each criterion actually needs

A whole-world 1 m scan is 440 M generator calls, so the search plane is 8 m.
That plane cannot decide flatness. MEASURED against a 1 m ground truth over
2048×2048 m of Pirate68 — the fraction of windows a coarse grid calls flat that
really are flat:

| footprint / budget | 8 m grid | 4 m grid | 2 m grid |
|---|---|---|---|
| 32 m / 2.5 m | 0.18 | 0.44 | 0.62 |
| 60 m / 3.0 m | 0.74 | 0.82 | 0.83 |
| 70 m / 3.5 m | 0.83 | 0.90 | 0.90 |
| 80 m / 4.0 m | 0.85 | 0.94 | 0.95 |

At a 32 m footprint the 8 m grid is wrong four times out of five, and no
whole-world step anyone would pay for fixes it. So:

| criterion | resolution needed | how it is served |
|---|---|---|
| flatness | 1 m | `PatchScan.cs` cuts a `footprint + 32 m` window at 1 m around every shortlisted candidate and slides the footprint to its flattest whole-metre offset |
| biome purity | 1 m | same patch |
| location clearance | exact | KD-tree against the ZoneSystem dump's real coordinates |
| anchor distance | exact | both endpoints are known points |
| water distance | ~8 m | 8 m EDT; the coastal test is a 120 m threshold |
| overland routing | 32 m | 32 m land graph; a detour factor does not need metres |

MEASURED: 426 patches (881 k samples) cost **2.5 s** of generator time inside
one server boot, so refinement is effectively free and is on by default.
`--no-refine` exists for debugging and stamps `flat_resolution_m: 8.0` so a
coarse answer can never be mistaken for a verdict.

The coarse grid is also **river-free**: `run_scan.sh` defaults
`SEEDSCAN_PREGEN=0`, and `WorldGenerator.AddRivers` reads the dictionary
`Pregenerate()` fills. `run_patchscan.sh` always pregenerates, so the 1 m pass
sees river beds the search pass cannot.

### 2. Two classes of constraint: HARD and COSTED

This is the central design decision, and it was wrong until now.

**HARD** — nothing we build fixes it, so a site failing one is REJECTED:
biome, `min_biome_purity`, `min_height_m` (freeboard over the *whole* footprint),
`location_clearance_m`, `coastal`/`coastal_within_m`, `within_m`, and
`require_overland` when a requirement declares it. All of them are full-grid
masks evaluated *before* anything is truncated or sorted. (Clearance and
coastality used to be filtered *after* trimming to the flattest few hundred
cells, which quietly turned two hard constraints into "whatever survived the
flatness sort".)

**COSTED** — terrain work fixes it, so it is a bill, not a verdict. Flatness is
the only one. Every placement declares `flatten_required: true` and is placed
from the `ulfsland-admin` seat with Infinity Hammer and World Edit Commands,
both of which level ground, and the blueprints carry their own captured
`#TerrainHeight` data. So `max_flat_m` is **the tolerance that defines a cheap
site, not a gate**. MEASURED: treating it as a gate rejected 8 of 13 placements
on Pirate68 for ground a hoe fixes in minutes — Valheim's generator almost never
produces a naturally flat 70 m pad. With flatness costed instead, **10 of 13
placements satisfy every hard constraint**.

`flatten_cost` prices the work from the 1 m patch against the footprint's
**median** height, the plane that minimises material moved:

| field | meaning |
|---|---|
| `cut_m3` / `fill_m3` | material above / below the target plane, m³ |
| `moved_m3` | the whole bill |
| `mean_move_m` | `moved_m3` / area — average depth of work, comparable across footprint sizes |
| `rms_m` | RMS deviation from the target plane |
| `max_cut_m` / `max_fill_m` | worst single cut and fill |
| `resolution_m`, `estimated` | 1.0/false when measured on the patch, grid step/true when still a coarse estimate |

Read `max_fill_m` with suspicion: filling is *raising* terrain, and raising it
over water is a different job from raising it over land. That asymmetry is why
freeboard stayed HARD while flatness became costed — the game's tools can raise
a slope, but they cannot drain a lake.

MEASURED bills on Pirate68 range from 409 m³ (`deepnorth-landing-camp`, 2.24 m
spread) to 23 419 m³ (`sandbox-harbour`, 43.5 m spread). The harbour number is
the useful kind of ugly: its hard constraints — `coastal` within 120 m plus 1 m
of freeboard within 1400 m of the start temple — leave only clifftops, so the
requirement needs rewording rather than the solver needing fixing.

### 2c. Waterside: freeboard is a BAND, and the footprint is a RECTANGLE

Expressed as a floor alone, `min_height_m` + `coastal_within_m` reads as *"high
ground near the sea"*, and on a real coastline that means a **clifftop**. It cost
`sandbox-harbour` a 43.5 m spread and a **23 419 m³** bill for something you walk
a boat up to. The requirement was upside down, not the building.

So anything whose purpose is meeting the water declares a band:

| knob | harbour / early-dock | deepnorth landings |
|---|---|---|
| `min_height_m` | **−2.0** | 0.5 |
| `max_height_m` | 3.0 | 3.0 |
| `max_grade_pct` | 10 | 10 |

`max_height_m` (HARD) caps the *usable* freeboard so the site is a shore and not
a headland. `max_grade_pct` (HARD) is spread ÷ the footprint's long side, so one
number means the same thing for a 32 m jetty and a 48 m harbour.

The docks' floor is **negative on purpose**: `dock.blueprint` is a harbour *with
a jetty*, and a jetty's waterward end is meant to be over water. MEASURED
feasible pads at grade 10 %: 0 at `min_height 0.5`, 0 at 0.0, 0 at −1.0, **10 at
−2.0**. The spawn is derived 6 m outside the footprint and verified dry
separately by `derive.py`, so a submerged jetty foot does not put a character in
the water.

`grade_cap` is floored at **twice** `max_flat_m`. Flooring it at exactly
`max_flat_m` was the first attempt and was wrong in the mirror-image way: the
hard gate became identical to the costed cheap-threshold and went back to
re-litigating flatness, rejecting three sites by 0.2–0.31 m. This gate exists to
exclude banks and cliffs — ground that is the wrong shape at any price — not to
re-price slopes, which `flatten_cost` already does.

**Footprints are rectangles.** `footprint_xz_m` declares the building's real
extent; a square window is the wrong shape for a shoreline because a shoreline
is a *line*. MEASURED for the harbour band: a 48 × 48 m square finds **0**
qualifying shelves within 1400 m — and still 0 at 2, 3, 4 and 6 km, so widening
the radius never helps — while the real **42 × 29 m** rectangle finds **2**, and
the transposed 29 × 42 m finds 0 again. Shape *and* orientation both matter, so
the solver tries both orientations and records which it assumed in
`footprint_orientation`; set the yaw to match.

The coarse window is never fewer than **3 cells**. A footprint smaller than the
grid step degenerates to a one-cell window where max == min, so the coarse pass
reports zero spread and is simply blind — MEASURED, `pre-kall`'s 3.9 × 6.5 m
portal reported 9687 "feasible" cells that way, every one of which failed at
1 m. Three cells is conservative in the right direction: it can over-reject,
never over-admit.

Result — the bills collapsed exactly as predicted:

| placement | bill before | after | spread before | after |
|---|---|---|---|---|
| `sandbox-harbour` | 23 419 m³ | **1 797 m³** | 43.50 m | 4.42 m |
| `early-dock` | 5 798 m³ | **1 797 m³** | 12.12 m | 4.42 m |
| `deepnorth-landing-portal` | 494 m³ | 382 m³ | 2.21 m | 2.81 m |
| `deepnorth-landing-camp` | 409 m³ | 402 m³ | 2.24 m | 2.14 m |

The two Deep North landings barely moved, and that is the honest answer for
them: they were never clifftops, so there was nothing for the band to fix.

### 2a. Serving a biome without being in it: `near_biome`

`near_biome` + `within_biome_m` is a HARD constraint that puts a site within a
stated distance of a biome **without requiring it to be in that biome**. It
exists because of a measurement that invalidated a requirement:

> MEASURED on Pirate68, the largest square entirely above the water plane
> anywhere in the **Swamp** is **0 m** at any purity — the biome simply does not
> contain a dry footprint. Every other biome hosts 80 m at purity 1.0.

**Swamp is water by construction. Never declare a Swamp footprint — declare
`near_biome: Swamp` instead.** That is the single most reusable fact in this
document, and the next person to write `biome: Swamp` with a `footprint_m` will
otherwise rediscover it the hard way.

#### What each biome can actually offer

Two biomes have a *character* that a requirement has to accept rather than
search around. MEASURED on Pirate68, levelling bill for the placement sited in
each, against the flattest thing the 1 m pass could find anywhere under the same
hard constraints:

| biome | placement | bill | 1 m spread | at the measured floor? |
|---|---|---|---|---|
| Meadows | `meadows-starter-hall` | 230 m³ | 2.12 m | yes |
| DeepNorth | `deepnorth-landing-camp` | 402 m³ | 2.14 m | yes |
| Mountain | `mountain-outpost` | 1 781 m³ | 6.17 m | yes |
| Plains | `plains-farm-base` | 3 475 m³ | 5.18 m | yes |
| AshLands | `ashlands-forward-base` | 3 553 m³ | 6.18 m | yes |
| BlackForest | `blackforest-walled-base` | 5 786 m³ | 10.45 m | yes |
| **Mistlands** | `mistlands-blackforge-base` | **21 550 m³** | **22.09 m** | **yes** |

**Mistlands is rugged the way Swamp is wet.** 22.09 m of spread over an
80 m-class footprint is not a poor pick — it is the flattest pad measured at 1 m
anywhere the hard constraints allow, and *tripling* the refinement pool from 64
to 192 candidates (947 patches) changed not one of the 13 chosen sites. Expect a
five-figure earthwork for anything large in Mistlands and budget for it, or pick
a smaller building.

The coarse grid's own floor for that placement reads 13.49 m, which looks like
9 m of headroom and is not: it is a grid-aligned LOWER BOUND and the 1 m pass
cannot generally reach it. That is why `flatten_floor` reports
`pool_best_1m_spread_m` — a measurement — beside it, and judges the bill against
that rather than against the bound.

So `pre-bonemass`'s "70 m dry pad in a Swamp" was never satisfiable, on this
seed or plausibly any. Its own `facing` text had said the answer all along —
*"workshop mouth faces the swamp it serves"* — it needed to **serve** the swamp,
not sit in it. The requirement is now `biome: BlackForest` +
`near_biome: Swamp` + `within_biome_m: 150`, and the placement satisfies every
hard constraint with 45 m of freeboard, 100 m from the swamp edge.

`BlackForest` over the alternatives on two measured grounds: a pre-bonemass
party has no Plains gear, and at a 150 m budget Black Forest offers 177
qualifying cells against Meadows' 43. 150 m is the smallest budget with a
healthy candidate set — about 2.3 Valheim zones, a workshop *overlooking* the
swamp rather than a different base.

`facing_check` runs for **every** placement, in one of three modes read off the
requirement: a served biome if `near_biome` names one, open water for anything
`coastal`, otherwise the bearing to the anchor. A yaw is inherited from wherever
the blueprint was captured and means nothing at a re-solved site, so it has to
be re-checked against the world every time. MEASURED: **9 of 13** declared yaws
were wrong, all silently — `complete-station-hub` 144° off its anchor,
`sandbox-harbour`/`early-dock`/both Deep North landings facing away from the
water they exist to meet. All nine corrected from the measurement; no site
moved, because yaw only changes the facing and the spawn offset.

`facing_check` verifies the claim rather than trusting the file: it walks a ray
along the declared yaw and reports what fraction lands in the served biome,
against all four cardinals. It paid for itself on its first run — the inherited
`yaw: 90` crossed **0 %** swamp from the new site, while `yaw: 270` crosses
46 % (180: 28 %, 0: 0 %). The yaw was a number that made sense wherever the
blueprint was captured and meant nothing here.

### 2b. Is the declared pad the building, or padding?

Check `footprint_m` against the blueprint's own extent before believing a
placement is infeasible. MEASURED, every placement declared a pad larger than
its building, by 4 to 25 m, and `footprint_cells` rounds up to an odd cell count
so `footprint_m: 60` at an 8 m step actually reserved a **72 m** window. Two
placements were failing on padding alone.

Trimming it is not always enough, and the honest threshold has to come from the
**exact** clearance check, not the grid-quantised one — the full-grid mask is up
to half a cell diagonal (5.66 m at 8 m) looser than the truth, which made an
earlier `remedy` advertise a 56 m footprint where all three candidate pads
failed the exact check by 4 to 6 m. `hard_feasible` now re-checks exactly.

With that fixed, the two placements resolved in opposite directions, each on its
own preset's evidence:

* **`meadows-starter-hall`** — `within_m: 700` is the intent ("within easy
  walking distance of the start temple"), so the *blueprint* gave way.
  `small_long_house` (50.2 × 33.6 m) has no legal pad inside 700 m: MEASURED 0
  exact-clearance pads at 56 m, 3 at 48 m, 33 at 40 m, 294 at 24 m. Swapped to
  `PuP_house10` (19.1 × 15.5 m), which also honours this preset's own rule that
  the starter building must not advertise an unreachable tier — no forge, no
  artisan station, timber only. Result: 2.12 m spread and a **230 m³** bill, the
  cheapest site in the tree.
* **`complete-station-hub`** — the *requirement* gave way instead, because this
  preset's own text is "the sandbox hub everyone **portals** to", which makes
  distance cosmetic and the station set functional. MEASURED: `Ultimate_OutPost`
  covers 19 of 21 station types; the best library blueprint fitting the 48 m pad
  available within 1200 m covers 13 and drops forge, smelter, kiln, blast
  furnace, windmill, eitr refinery and portal. `within_m` 1200 → 1500 (0
  feasible pads at 1200, 0 at 1400, 15 at 1500).

Clearance is `location_clearance_m` plus the footprint's **half-diagonal**, not
its half-side: the corner of a square pad reaches further than its edge.
MEASURED on Pirate68, for `pre-kall`'s 32 m Deep North landing, 239 of 305
otherwise-qualifying cells are rejected on clearance alone — e.g. (2356, 8596),
dead flat and in pure DeepNorth, sits 16.2 m from `Shipwreck02_DN` against a
82.6 m requirement. In Swamp crypt country a candidate at (−4596, −1076) is
15.1 m from `SunkenCrypt4`. ZoneSystem re-cuts terrain inside a location's clear
area when it spawns, so those sites would fight the generator. The dump carries
no exterior radius (MEASURED: its fields are name/prefab/biome/group/unique/
icon/x/y/z/placed), so the stand-off is a conservative fixed figure —
**INFERRED**, not per-prefab exact.

**Every instance of an anchor is tried.** Valheim places five Bonemass altars,
four GoblinKings, three Dragonqueens. Measuring against whichever one happens to
be nearest the world origin is how this tool used to report a site as 3629 m
from "the" Bonemass when it was 484 m from another one.

### 3. Scoring, not just filtering

Survivors are scored, each term normalised to 0..1 with 1 = best, weights
summing to 1 (`site_finder.WEIGHTS` — the one place the solver expresses taste):

| term | weight | meaning |
|---|---|---|
| `flat` | 0.35 | `1 − spread / max_flat_m`; how much hoe work is left |
| `anchor` | 0.20 | `1 − distance / within_m` |
| `reach` | 0.15 | 1 if the site is on the spawn's landmass, else 0 |
| `clearance` | 0.12 | margin beyond the required stand-off |
| `purity` | 0.08 | margin beyond `min_biome_purity` |
| `water` | 0.10 | coastal only; folded into `flat` otherwise |

Terms are capped at 1 but not floored at 0, so an over-budget candidate scores
negative and still orders correctly against its peers.

Ranked candidates are thinned by non-maximum suppression — half a footprint
apart for the refinement pool (coverage), a footprint and a half for the
recorded `shortlist` (genuinely different options). Each `solved` block records
up to five alternatives with their own scores, distances and violations, so an
operator can overrule the solver on taste without re-running anything.

### 4. Overland versus boat

MEASURED on Pirate68: 17 land components, the two largest holding 137 k and 92 k
of 279 k coarse land cells, their closest approach anywhere 64 m of open water.
Every site records `route_from_spawn` and `route_to_anchor` with
`reachable`/`straight_m`/`path_m`/`detour` from a Dijkstra over the 32 m land
graph. MEASURED: the Deep North landing at (−1588, 9828) is 9943 m from spawn in
a straight line and 10 447 m on foot — **detour 1.05**, corroborating SeedHunt's
walkable-north-corridor finding for this seed. The `pre-bonemass` workshop at
(4519, −231) is 626 m from its altar and says `needs a boat`: there is a 130 m
channel between them.

### 5. When nothing satisfies the requirement

The search widens **once**, into a bounded window (`COMPROMISE` in
`solve_placements.py`: distance ×3, coastal ×3, purity ×0.75, and freeboard only
under the rule above — flatness is not in it, because flatness no longer gates
anything), and then **minimises the actual miss against the declared
requirement** — `site_finder.miss_vector` in the coarse search,
`site_finder.miss` for the final choice and the 1 m slide, all the same quantity
so the three cannot disagree. The miss counts HARD constraints only.

This replaced a single-axis ladder that tried each knob at 1.25×, 1.5×, 2×, 3×,
5× in turn and took the first hit. That shape is wrong whenever two constraints
must both give, which is the normal case once the water datum is correct: no
single-axis variant exists, so the ladder escalated until one axis was absurd.
MEASURED: it answered `pre-queen` with a 26.7 m cliff rather than a 4 m slope
1.2 m under water, and with purity unbounded it returned a mountainside for a
Swamp workshop. "Smallest multiplier" is not the goal; smallest departure from
what was asked for is.

The 1 m slide minimises the same miss rather than flatness alone, for the same
reason: a patch around a dry candidate contains ground that is flatter and
wetter, and a flatness-only objective slides the site straight off its dry
ground. MEASURED: `pre-elder`'s dry 7 m slope became a 6.2 m pad 2.7 m under
water before this was fixed.

Whatever the search did, the chosen site is re-checked against the **declared**
requirement and the file records the truth: `satisfies_requirement`, a
`violations:` list of the HARD constraints it broke, a `relaxed:` record naming
every knob that was opened, `freeboard_ceiling_m` (the best freeboard available
and how many dry footprints exist at all), and — the useful part for an
operator — a **`remedy:`** block saying what *would* work. A violation record
says the answer is bad; the remedy says what to change. Two levers are searched,
because they are the two an operator actually has:

* `largest_workable_footprint_m` — a smaller blueprint. The sibling
  `tools/jumpstart/library/` catalogues 176, many with small footprints.
  MEASURED: `complete-station-hub` and `meadows-starter-hall` both become
  hard-feasible at 56 m against their declared 60 m.
* `within_m_needed` — a wider radius. MEASURED: `complete-station-hub` needs
  1800 m against 1200, `meadows-starter-hall` 2100 m against 700.
* `min_height_m_needed` / `foundations_note` — accept that the pad is partly
  submerged and build on foundations. This is the only lever for a Swamp:
  MEASURED, the largest swamp square entirely above y = 30 anywhere in Pirate68
  is **16 m**, against `iron-era-workshop`'s 70 m footprint, so no smaller
  blueprint and no wider radius helps and the honest answer is −1.39 m of
  freeboard plus foundations.

Biome is deliberately never offered as a remedy: changing it changes which
preset this is.

`flat_spread_m` is max-minus-min, kept because it is what the schema declares,
but one boulder in one corner dominates it — so `flat_p5_p95_m` is recorded
beside it and `flatten_cost` is what actually prices the site. MEASURED:
`pre-moder`'s mountain outpost is 6.17 m worst-case, 4.06 m across the middle
90 %, and 1781 m³ of work.

### 6. Intra-preset exclusion

Each solved site excludes the next by 1.5 footprints, enforced against the final
refined coordinates rather than the coarse ones. Presets do *not* exclude each
other: they are alternative world states that never coexist, so two presets
resolving to the same meadow is correct. Without this, `pre-kall`'s two
placements resolved to the identical cell and the camp landed inside the portal
shelter.

## File formats, both MEASURED from InfinityHammer.dll

`.blueprint` piece row — `;`-separated, any `,` rewritten to `.` first so
decimal-comma locales parse, floats invariant `0.###`:

| # | field | # | field |
|---|---|---|---|
| 0 | prefab name | 9 | extra info / text (default `""`) |
| 1 | PlanBuild category — parsed, unused; IH writes `""` | 10–12 | scale x, y, z (default 1) |
| 2–4 | position x, y, z | 13 | base64 ZDO data (only with `data=true`) |
| 5–8 | rotation quaternion x, y, z, w | 14 | placement chance (default 1) |

IH writes the row as `prefab;;x;y;z;rx;ry;rz;rw;info;sx;sy;sz;data` — note the
empty category column and that it does not emit `chance`.

### Sections: an unknown header discards what follows it

MEASURED from the user-string heap of the **deployed**
`Infinity_Hammer/InfinityHammer.dll` (`monodis --userstrings`), the reader's
complete header vocabulary is

```
#  #name:  #creator:  #description:  #category:  #center:  #coordinates:
#rotation:  #pieces  #height:  #paint:
```

plus `#SnapPoints` / `#TerrainHeight:` / `#TerrainPaint:` matched
case-insensitively. There is **no `#objects` literal anywhere in the assembly**.
The section machine is five-state — None / Pieces / SnapPoints / TerrainHeight /
TerrainPaint — and starts at Pieces, so a headerless file is all pieces. Then:

* an **unrecognised** header whose second character is not whitespace sets the
  section to **None**, and every row after it is discarded until the next
  recognised header;
* a header of length 1, or with whitespace at index 1, is a **comment** and the
  section survives it;
* `#Height:` / `#Paint:` are recognised and then **rejected** — *"Legacy
  #Height/#Paint terrain format is no longer supported."* — so the file does not
  load at all.

This is not a detail. `inventory.py` and `to_rcon_plan.py` used to keep the
current section across an unknown header, which read PlanBuild's `#Terrain`
block — rows shaped `shape;x;y;z;radius;rotation;smooth;` — as **pieces named
`circle` and `square`**. The bundle-token viability check cannot catch that:
both words are genuine ASCII tokens in the game's asset bundles and both resolve
`vanilla` in `data/prefab_evidence.json`. Downstream, `to_rcon_plan.py` would
have emitted `spawn circle …` at a live server.

MEASURED over the committed 76-file corpus: 7 files carry a `#Terrain` block,
16 rows in total, and the corrected figures are

| file | pieces | distinct prefabs | snap points | footprint x,z,y |
|---|---|---|---|---|
| `aodi-skardvakt-the-black-tower` | 366 → **358** | 8 → **7** | — | 9.3, 9.3, 13.5 → **10.5** |
| `god-house33` | 2950 → **2949** | 81 → **80** | — | 29.7, **47.2 → 29.1**, 18.0 |
| `god-shadowstone-fortress` | 8284 → **8283** | 87 → **86** | — | — |
| `14464-Valheimian` | 2330 → **2329** | 101 → **100** | — | — |
| `gnome_house` | 2330 → **2329** | 101 → **100** | — | — |
| `god-ponte-plus` | — | — | 4 → **2** | — |
| `15643-Valheimian` | — | — | 9 → **7** | — |

No real pieces are lost anywhere — every `#Terrain` block sits immediately
before `#Pieces` — so the fix only stops the tool INVENTING content. None of the
12 blueprints referenced by `placements.yaml` is affected (all report
`discarded_rows: 0`), so every `pieces:` and `footprint_xzy_m:` already recorded
there was already correct. `data/inventory.json` has been regenerated.

`to_rcon_plan.py` additionally refuses to emit a plan at all when a surviving
prefab is `MISSING` from the evidence or absent from it — MEASURED: it now
rejects `BjOrN_blueprint001.blueprint` on `piece_Sundial` until that prefab is
dropped with `--drop-prefab`. A converter that can emit `spawn circle` is a
converter that will eventually be run.

`.vbuild` (legacy BuildShare) — split on a **single space with empty entries
KEPT**, so the layout is fixed-arity and a zero component serialises as an empty
field:

| # | field |
|---|---|
| 0 | prefab name |
| 1–4 | rotation quaternion x, y, z, w (default 0) |
| 5–7 | position x, y, z (default 0) |
| 8 | extra info | 
| 9 | placement chance (default 1) |

There is no scale column; IH forces `Vector3.one`.

**Splitting `.vbuild` on whitespace *runs* is the trap** — it drops the empty
fields and shifts every column. I derived this layout empirically before finding
IH's parser, and validated it independently: across 12 137 rows the four
rotation components satisfy `x²+y²+z²+w² = 1` to within **1.5e-6**. Splitting on
runs instead silently discarded 1 615 of `berserkers-keep.vbuild`'s 10 686
pieces and produced a nonsense 10 152 m vertical footprint.

## Viability: which blueprints actually work on 1.0.12

The real risk with community blueprints is that a file authored for 0.217
references pieces that were renamed or removed, or belongs to a build mod this
fleet does not have. So every prefab name in the corpus is resolved against
evidence rather than judged by eye:

```bash
./scan_piece_prefabs.py --out data/piece_prefabs.json    # ~97 s
./inventory.py <corpus dirs...> --json data/inventory.json \
    --prune-evidence data/prefab_evidence.json
```

`scan_piece_prefabs.py` harvests **668 073** prefab-shaped tokens from the 796
UnityFS bundles in `valheim_server_Data/StreamingAssets/SoftRef/Bundles` (the
bundle reader is reused, unmodified, from the sibling `extract_prefab_names.py`),
plus the tokens unique to each of the 95 deployed mod plugin directories. That
distinguishes *removed by Iron Gate* from *supplied by a mod we happen to have*.

Verdicts across all 112 files:

| verdict | count |
|---|---|
| `PLACES_CLEAN` — every prefab resolves | **88** |
| `PLACES_WITH_GAPS` — <1 % of objects missing | 12 |
| `EMPTY` — 0-byte truncated download | 12 |

Only **31 distinct** prefab names in the whole corpus fail to resolve, and they
are almost all from build mods this fleet lacks — `MarketPlaceNPC`,
`MS_PressurePlate_BlackMarble`, `emberwood_pillar4_bal`, `MMFrostCannon`,
`BasicTurret`, `Odins_Alchemy_Book`, `opalchemy`, `opcauldron`, `h_chain`,
`h_window_0x`, `Hayze_gate_0x`, `$custompiece_*`, `Totem2`/`Totem3`,
`piece_Sundial`, `Thorward`.

The evidence set discriminates correctly rather than answering yes to
everything: fabricated names (`wood_wall_zzz`, `grausten_flimflam`,
`blackmarble_notreal_9x9`, `piece_totally_fake`) are all absent, while real
1.0-era Ashlands and Mistlands pieces (`Piece_grausten_pillarbeam_medium`,
`Piece_flametal_pillar`, `ashwood_wall_2x2`, `crystal_wall_1x1`,
`piece_dvergr_metal_wall_2x2`, `dvergrprops_bed`) are all present.

One trap worth recording: `wood_wall_log_4x0.5` is the **single most common
piece in the entire corpus** (1 135 objects) and a token regex without `.` in
its character class reports it as removed. Dotted prefab names get a second
scanning pass for exactly this reason.

## The tiered shortlist

Materialise with `./materialise.py --world ../worlds/Ulfsland --all --strict`.

| Preset | Blueprint | Author | Pieces | Footprint x·z·y m | Stations it brings | Verdict |
|---|---|---|---|---|---|---|
| `pre-eikthyr` | `small_long_house` | Пипкин | 1 444 | 50·34·15 | workbench, cooking, chests — **deliberately no forge** | CLEAN |
| `pre-elder` | `fox-0123456` | Fox- | 1 229 | 47·39·12 | forge, kiln, smelter, beehive, cauldron, cooking, workbench, portal | CLEAN |
| `pre-elder` | `dock` | Olivanderr | 2 036 | 42·29·24 | harbour, chests | CLEAN |
| `pre-bonemass` | `BjOrN_blueprint001` | BjOrN | 1 908 | 66·66·17 | forge, smelter, kiln, stonecutter, cauldron, prep, portal, workbench | GAPS 0.05 % (`piece_Sundial` ×1) |
| `pre-moder` | `jaik-mistvale-keep` | Jaik | 3 472 | 28·27·**48** | bed, chest, cooking, forge, oven, prep, portal, workbench | CLEAN |
| `pre-yagluth` | `cozy_house` | Пипкин | 6 034 | 52·53·24 | **windmill, blast furnace**, artisan, forge, smelter, kiln, oven, cauldron, cooking | CLEAN (black forge dropped) |
| `pre-queen` | `PuP_black_house_full` | PuP | 6 262 | 41·73·78 | **black forge**, blast furnace, galdr table, artisan, smelter, spinning wheel, oven, prep | CLEAN |
| `pre-fader` | `deardly-casa-drly` | Deardly | 1 901 | 31·30·13 | black forge, eitr refinery, galdr table, artisan — grausten/ashwood/flametal | CLEAN |
| `pre-kall` | `Portal13.vbuild` | — | 133 | 4·7·7 | portal shelter | CLEAN |
| `pre-kall` | `wagon-camp` | Cooties | 160 | 14·10·5 | cauldron, cooking, barrel, chests — **no forge** | CLEAN |
| `deepnorth-sandbox` | `Ultimate_OutPost` | Randominis | 1 820 | 53·49·14 | **every station in the game** (see below) | CLEAN |
| `deepnorth-sandbox` | `god-portaaoo` | God | 2 191 | 36·12·23 | stone portal arcade | CLEAN |
| `deepnorth-sandbox` | `dock` | Olivanderr | 2 036 | 42·29·24 | harbour | CLEAN |

`Ultimate_OutPost` is the find of the corpus: at only 1 820 pieces it carries
workbench, forge, stonecutter, artisan table, black forge, blast furnace,
smelter, charcoal kiln, windmill, spinning wheel, eitr refinery, galdr table,
oven, prep table, cauldron, cooking station, beehive, barber, a portal and 32
chests — and resolves **100 % clean** with zero missing prefabs.

Tier bleed is handled rather than tolerated: community bases tend to ship the
whole station set, so `cozy_house` would hand a Plains-tier party a Mistlands
black forge. `drop_prefabs` in the placement strips named prefabs
(`to_rcon_plan.py --drop-prefab`).

### What I would NOT use, and why

* `15643-Valheimian`, `14464-Valheimian`, `gnome_house` — **loot-spam
  exploits**, 361–491 `TreasureChest_*` objects each. Not bases.
* `vcastle` (102 `Pickable_DragonEgg`) and `PuP_megaCastle` (28 core stands) —
  place cleanly but hand out free progression.
* `citadel` (33 655 pieces), `PuP_megaCastle` (25 207), `vcastle` (28 297),
  `gold_castle` (18 036), `meadhall` (20 213) — too large. At one RCON `spawn`
  per object these are 18 000–34 000 commands, and `citadel` brings no stations
  at all.
* `s-ren-dockhouse` (22 % missing), `long-bridge` (19 %), `PuP_Capitol` (7.7 %),
  `small_long_house` in its `old_Storgard` copy, `PuP_house13`, `PuP_house11`,
  `bridge.vbuild`, `altar` — these were the pre-fix `BROKEN` set; after the
  dotted-prefab correction all but their genuine mod dependencies resolve. The
  remaining true failures are mod-piece dependencies, not version rot.
* The 12 zero-byte files.
* `halvar-master-refinery` — a better *thematic* iron workshop than the one
  chosen, but it ships an artisan table, which needs a Dragon Tear and so leaks
  a tier into `pre-bonemass`.

## Downloaded blueprints vs build-once-and-export: the honest comparison

The parent's leading option was to hand-build one base per tier from the admin
seat and replay it as data. Having measured both:

**Neither is what I would recommend. The corpus already on disk beats both**,
and the export machinery is worth building anyway — because it is the same
machinery.

| | Downloaded blueprints | Build-once-and-export |
|---|---|---|
| Cost to obtain | Zero, already on disk: 100 non-empty files | ~1–2 h of admin-seat building **per tier**, so 9–18 h |
| Licence | **No licence stated anywhere.** Local-use only, cannot be vendored | Fully ours |
| 1.0 viability | MEASURED: 88/112 place clean | Clean by construction |
| Quality | Genuinely good community builds, including post-Ashlands grausten/flametal work | Whatever the operator builds |
| Tier fit | Approximate; needs `drop_prefabs` to stop tier bleed | Exact |
| Export fidelity | n/a | `hammer_save` writes full quaternions at `0.###`, plus scale, extra info and optional base64 object data — **lossless** |

The decisive finding is about the *export channel*, and it inverts the premise
of the question. `findObjects`/`spawn` is **not** a good round-trip:

* `findObjects -detailed` reports position as `({0:0.##} {1:0.##} {2:0.##})` —
  1 cm quantisation — and rotation as `Quaternion.eulerAngles`, also `0.##`.
  Euler is a lossy, ambiguous encoding to re-derive a quaternion from.
* It emits **one line per object with no pagination**, and MEASURED,
  `ValheimRcon.Core.RconCommandReceiver::ValidatePayloadLength` hard-truncates
  every response at **4050 payload bytes**. A detailed line is ~120 bytes, so
  about **32 objects per response** — against a 1 820-piece base. It also scans
  all of `ZDOMan` on the main thread, which is consistent with the two
  observed server wedges.
* `spawn` cannot set scale, cannot set object data, and never sets
  `Piece.m_creator`.

Whereas `hammer_save` on the admin client writes the *same PlanBuild format* the
corpus uses, at full fidelity. So "build once and export" and "use downloaded
blueprints" converge on one artefact: **a `.blueprint` file**. The right pipeline
is therefore:

1. Take a corpus base as the starting point — it is free and it works.
2. Fix it up from the `ulfsland-admin` seat: strip out-of-tier stations, stock
   the chests, adjust for the actual site.
3. `hammer_save <name> data=true` — now it is **our** file, licence-clean,
   lossless, and the fleet owns it.
4. Replay it either interactively (`hammer_blueprint`) or headlessly
   (`to_rcon_plan.py` → RCON `spawn`, one command per object, bottom-up).

Step 3 is what retires the licence problem permanently, and it costs minutes per
tier instead of hours, because step 1 did the building.

`to_rcon_plan.py` emits pieces in **ascending Y** so that each piece's support
already exists beneath it — necessary because ghost-init writes the ZDO without
running WearNTear support propagation, so an unsupported piece can be destroyed
when the zone first loads. Scale loss is negligible in practice: MEASURED, the
shortlist blueprints carry non-unit scale on 0–24 objects out of thousands.

### Stocking chests headlessly

RCON `spawn` cannot pre-fill a container, but ValheimRcon has the rest of the
chain:

```
findObjects -prefab piece_chest -near <x> <y> <z> <r> -detailed
  -> -Prefab: <name> Id: <id>:<userid> Position: (x y z) Zone: (zx zy) Rotation: (ex ey ez)Creator: <n> Health: <h>
addItemToContainer <id:userid> <item> -count <n> [-quality q] [-variant v] [-durability d] [-force]
showContainer <id:userid>   removeItemFromContainer ...   clearContainer ...
```

Keep the `findObjects` filter tight — the 4050-byte cap applies.

## Files

```
scan_piece_prefabs.py   harvest prefab-name evidence from bundles + mod DLLs
inventory.py            parse, measure and rate blueprints; flags loot payloads
build_manifest.py       identity + verdict manifest for the corpus
materialise.py          copy-from-source with checksum verification
resolve.sh              THE re-roll command: scan, locate, solve, write
site_finder.py          search/score/refine one requirement against a seed
solve_placements.py     re-solve or --verify every placement in a world
PatchScan.cs            BepInEx plugin: 1 m height+biome patches around candidates
run_patchscan.sh        build and run PatchScan in a server sandbox
to_rcon_plan.py         blueprint -> RCON `spawn` plan (headless placement)
PROVENANCE.md           authors, licence position, exploit payloads
data/corpus_manifest.*  the committed manifest (json + tsv)
data/inventory.json     full per-file measurements
data/prefab_evidence.json  resolution for every prefab the corpus uses
data/piece_prefabs.json 28 MB evidence cache -- gitignored, regenerate on demand
```

Per-preset placement data lives in
`tools/jumpstart/worlds/Ulfsland/<preset>/placements.yaml`, with bodies
materialised into `<preset>/blueprints/` and gitignored there.
