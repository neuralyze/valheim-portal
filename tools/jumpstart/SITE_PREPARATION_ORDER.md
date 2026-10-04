# Site preparation order

The order in which a jumpstart site is built, and why each step has to come
where it does. This is the contract between the vegetation clearing step
(`tools/jumpstart/clearing/`), the terrain step (`tools/jumpstart/terraform/`)
and the blueprint placement step (`tools/jumpstart/blueprints/`). The vertical
datum decision lives on the blueprint side; the ORDER is owned by clearing and
terraform, and steps 0 and 1 below are measured rather than reasoned.

```
0. GENERATE THE ZONES    tools/jumpstart/clearing/   (Upgrade World zones_generate)
1. CLEAR VEGETATION      tools/jumpstart/clearing/   (Upgrade World objects_remove)
2. FLATTEN TERRAIN       tools/jumpstart/terraform/flatten.py
3. PLACE THE STRUCTURE   tools/jumpstart/terraform/sites.py
                         -> blueprints/to_rcon_plan.py --align floor-center
4. PLACE LOOSE OBJECTS   tools/jumpstart/terraform/stock.py
```

Steps 0 and 1 are both owned by `tools/jumpstart/clearing/`; they are listed
separately because the reason they are two steps is easy to get wrong.

## Every step writes THROUGH THE LEDGER

Each step appends its write to the world's ledger BEFORE sending it
(`terraform/flatten.py::install_emit` -> `ledger/live.py` `LiveBuilder.emit`),
and each takes `--actor`: `clear.py apply` (zones_generate + objects_clear),
`flatten.py apply` (one `terrain_write role=site_pad` per placement, unioned
with the ledger's claims in its zones), `sites.py --flattened --apply` (one
`spawn_plan role=site_body` per plan it could make, and rc 1 naming every one
it could not) and `stock.py` (one `spawn`/`portal role=site_prop` per fixture;
it refuses a site whose body has no live `spawn_plan`).

Why this is an ordering rule and not bookkeeping: a zone holds ONE
`_TerrainCompiler` and every terrain write deletes it before spawning its own,
keeping earlier ground only by re-reading it out of the ledger. A pad the
ledger never saw is erased by the next write in its zone. MEASURED: the
previous Fresh42 drive ran steps 0-2 and 4 as 39 unit-runs at rc 0 that appended
ZERO records, and on the current route 10 of the 36 installation-pad zones
(2,092 samples) sit under a later road write. `INSTALLATION_WRITE_PATH_DEPENDS_ON`
in `flatten.py` is the declaration; `flatten.py selftest` and `buildstage.py
selftest` neuter both of its terms on an in-memory world.

## 0. Generate the site's zones, before touching anything else

With no players online the site's zones have not been generated, so its trees
and rocks DO NOT EXIST as ZDOs yet and a delete-only clearing step has nothing
to delete. MEASURED by `SiteClearing` on a `/tmp` sandbox of the same build: a
fresh world had ghost-generated only 81 zones, every `_ZoneCtrl` of which sits at
(1000000, 0, 1000000), and zero vegetation anywhere in the save.

So clearing is two operations, in this order: pre-generate the site's zones
headlessly (Upgrade World `zones_generate`), then remove the vegetation
(`objects_remove`).

GENERATION IS NOT A CONVENIENCE, IT IS A HARD PRECONDITION OF STEP 2, and it is
the one ordering rule in this file proven by deliberately breaking it. MEASURED
on Ulfsland, 2026-09-15, zone (-18, 28) at (-1152, 1792), by
`/tmp/gt/verify_ordering.py`: a `_TerrainCompiler` carrying a valid 40 x 40 m
flatten to y = 31.40 was spawned into a zone that had never been generated, and
the zone was then generated. All 190 objects it planted inside the 30.28 m
cylinder landed on the UNMODIFIED world-generator height -- median offset from
the patchscan height 0.000 m, worst case 0.42 m -- which stood them up to
**7.68 m above the finished pad**, as real ZDOs, permanently:

```
Beech_small1  n=31  dY vs pad +3.72 [-0.16, +6.81]   dY vs generated -0.025
Beech1        n=17  dY vs pad +2.54 [-0.32, +7.68]   dY vs generated -0.001
Bush01        n=16  dY vs pad +2.90 [-0.33, +6.56]   dY vs generated +0.018
LocationProxy n= 1  dY vs pad +1.66                  dY vs generated +0.001
```

The paint's vegetation-cleared alpha was ignored in the same breath: `Bush01`,
`RaspberryBush`, `Beech1`, `Pickable_*` and eleven more prefabs were placed on
ground painted alpha 0. **Paint alpha is NOT honoured on a zone that has never
been generated.**

One cause explains both, from IL (`assembly_valheim.dll`, Valheim l-1.0.12,
md5 `89ffdb64fefebc011f5a9a826f2968bf`):

* `Heightmap::Generate` ends by calling `Heightmap::ApplyModifiers`, which finds
  its compiler through `TerrainComp::FindTerrainCompiler` -- a scan of the
  static `TerrainComp::s_instances` list of INSTANTIATED components.
* A dedicated server with no peers instantiates no `_TerrainCompiler` ZNetView,
  so that list is empty, and neither the height deltas nor the cleared mask ever
  reach the heightmap.
* `ZoneSystem::SpawnZone` instantiates the zone root and then, in the same call,
  runs `PlaceLocations`, `PlaceVegetation` and `PlaceZoneCtrl` against that
  heightmap. `PlaceVegetation` takes its ground Y from
  `ZoneSystem::GetGroundData`, a downward `Physics.Raycast` against the
  collider, and its cleared test from `Heightmap::GetVegetationMask`, which
  returns `m_paintMask.GetPixel(...).a`. Both see unmodified terrain.

`terraform/flatten.py apply` therefore REFUSES to write a compiler into a zone
with no `_ZoneCtrl` at its centre, and refuses before writing even the
data-entry file, because a stale entry in the watched data directory is a loaded
gun for the next run. MEASURED: `PlaceZoneCtrl` plants the `_ZoneCtrl` exactly
at the zone centre -- (-1152, 0, 1408) for zone (-18, 22) -- and
`objects_count`'s `pos`/`max` filter is a vertical cylinder on
`Utils.DistanceXZ`, so a 1 m probe at the zone centre is an exact
generated/not-generated test with a two-line answer. The refusal is measured to
fire: a pad at (0, 0) in ungenerated zone (0, 0) exited 1 and wrote nothing.

The generate reach always covers the flattened zones, so the precondition is
satisfiable by construction. `clearing/clear.py` generates with
`max = half_diagonal + margin + 32*sqrt(2)`, and Upgrade World selects zones by
distance to their CENTRE; `flatten.py`'s `zones_for` selects any zone whose
65-sample lattice touches the pad, i.e. centre within `32.5 + pad_half_extent`.
Since `half_diagonal >= pad_half_extent`, the clearing reach exceeds the flatten
set by at least 14.75 m on every axis.

## 0b. For a ROAD: census the zones you just generated, BEFORE routing

A site's zones are generated for its own pad, and the next thing that happens
to them is a clearing pass that deletes vegetation. A ROAD's zones are
different in one way that turned out to matter: the road is PLANNED before its
zones exist, so the plan cannot see anything generating those zones creates.

Generating a zone is the moment a `More_World_Locations_AIO` structure is
CREATED. `ribbon.py`'s live path generated every zone the finished road
crosses (its STEP 0) and then asked exactly one question about each --
`objects_count id=_ZoneCtrl`, i.e. "did a controller appear" -- before writing
terrain through it. It never asked what was standing there, and the router
could not have known either: it planned against the world GENERATOR's location
dump, which is mod-free by construction.

MEASURED on Ulfsland: the gap from a zone's creation to its paving is a median
of 6 ledger steps and ranges from 1 to 206; 5,318 non-clearable objects stand
in the paved width plus a 1 m shoulder over 122 bodies; 641 pieces in 48 bodies
block a walker; and 42 of those 48 bodies were ALREADY STANDING when their road
was written.

So a road has two extra steps, and they go between 0 and 1:

```
0.  GENERATE THE ZONES   roads/precensus.py survey   (per zone, then...)
0b. CENSUS THEM          roads/precensus.py survey   (...read that zone, now)
0c. ROUTE                roads/network.py plan --census <artefact>
1.  CLEAR / PAVE         roads/clear.py, roads/ribbon.py build --census
```

Each step refuses to run out of order, and the refusals are the point:

* `network.py plan` with no `--census` REFUSES and names the three commands.
  `--poi-source {all,generator}` is gone; neither of its populations was a
  reading of the world the road will cross.
* `precensus.py survey` generates a zone and reads it IN THE SAME CALL, so no
  cell is ever censused before it exists. The artefact records both timestamps.
* the router is CONFINED to the censused cells, and `coverage_gate` then proves
  the finished line never left them. The constraint and the check share one
  cell set, so they cannot drift.
* `ribbon.py build` REFUSES a plan carrying no census verdict, re-checks
  coverage at the write, and re-checks FRESHNESS -- a zone regenerated after
  the census holds objects the census never saw. `--census-waiver "<reason>"`
  is the only way past, the reason is required, and it goes in the ledger.

The coverage gate's reference is a socket census, which is a DIFFERENT artefact
from the PatchScan field the route was planned on. That is deliberate: the
walkability check that declared 84 impassable locations WALKABLE composed its
reference from the same field as the thing under test, and could not fail.

The bill is visible before any call is made: `precensus.py cells` prints cells,
zones and boxes per segment. For Ulfsland's 39 built segments at the default
16 m band half it is 5,003 cells, 622 zones, 25,015 boxes.

## 1. Clear vegetation, after generating and either side of the flatten

Flattening terrain does not remove trees. The terrain step writes a `TCData`
byte array into the zone's `_TerrainCompiler` ZDO, which changes the heightmap
and nothing else; every `Beech1`, `FirTree`, `Pinetree_01` and rock in the
footprint is a separate ZDO that survives untouched and ends up standing through
whatever gets built there. MEASURED in live play on Ulfsland on 2026-09-15: the
`pre-bonemass/iron-era-workshop` pad flattened correctly and the trees were
still standing in it. MEASURED again headlessly at zone (-18, 26): writing the
compiler moved no object at all -- `Rock_4` n=12 sat at dY vs pad -0.43
[-2.78, +4.58] both before and after the terrain write.

CLEAR BEFORE OR AFTER THE FLATTEN: BOTH WERE MEASURED, AND THEY ARE EQUIVALENT.
Two pads were prepared the same day, 256 m apart, from the same code:

| pad | zone | ordering | left in the 30.28 m cylinder |
| --- | --- | --- | --- |
| A at (-1152, 1408) | (-18, 22) | generate -> clear -> flatten | 2 (`_TerrainCompiler`, `_ZoneCtrl`) |
| B at (-1152, 1664) | (-18, 26) | generate -> flatten -> clear | 2 (`_TerrainCompiler`, `_ZoneCtrl`) |

There is no difference because `objects_remove`'s `pos`/`max` filter is a
vertical cylinder on `Utils.DistanceXZ` with NO height bound, so an object
standing 7 m in the air over a cut pad is exactly as removable as one standing
on it. The earlier claim in this file that clearing had to precede flattening
"because a tree removed after the pad is cut leaves its stump geometry at the
pre-flatten altitude" was never measured and is not true of a ZDO deletion:
nothing is left behind to leave anywhere.

The diagram keeps clearing first anyway, for one reason that IS load-bearing:
the clearing step is where `zones_generate` lives, and the flatten step now
refuses to run until it has. Keeping them adjacent means the precondition is
satisfied by the step immediately before it, not by something three commands
earlier.

The clearing step decides what to remove from the footprint recorded in
`placements.yaml` (`requirement.footprint_m`, `requirement.max_flat_m`,
`requirement.location_clearance_m`, `rotation.yaw`). Those are seed-independent
declarations, available before any terrain is touched, so there is no ordering
dependency in the other direction.

Nothing needs adding to the removal list. `objects_remove id=* ignore=_*` takes
every prefab `ZNetScene` knows except the `_`-prefixed ones, so `Bush01`,
`RaspberryBush`, `Pickable_*`, `shrub_2`, `vines` and the small `Rock_*` were
never exempt from it. MEASURED at pad C, where clearing was deliberately not
run: all 22 prefab classes present are classes that cylinder would have taken.

## Grass is not vegetation, and it had a separate cause

The operator's report -- "all the grass is still floating in the air where the
ground is flattened" -- is a THIRD defect, in neither the ordering nor the
removal list, and it cannot be measured from a dedicated server at all because
`ClutterSystem` is client-only and `ClutterSystem.instance` is null here.

Grass is `ClutterSystem` clutter: no ZDO, instantiated client-side, and MEASURED
from `ClutterSystem::GenerateVegPatch` -> `GetGroundInfo`, its Y comes from a
downward `Physics.Raycast` baked into a GameObject transform at patch-generation
time and never re-evaluated. `GeneratePatch` regenerates an already-cached patch
ONLY when `PatchData.m_reset` is set, and `ClutterSystem::ResetGrass` is the only
thing that sets it.

The one call that resets grass after a terrain-data load is in
`TerrainComp::CheckLoad`, and it chooses between a narrow and a wide reset using
the blob's own `m_operations`:

```
int before = m_operations;                     // 0 on a fresh component
if (!Load()) return;                           // reads the blob
m_hmap.Poke(0, false);                         // terrain now correct
if (!ClutterSystem.instance) return;           // dedicated server stops here
if (m_operations == before + 1) {
    ClutterSystem.instance.ResetGrass(m_lastOpPoint, m_lastOpRadius);
    return;
}
ClutterSystem.instance.ResetGrass(m_hmap.transform.position,
                                  m_hmap.m_width * m_hmap.m_scale / 2f);
```

`tcdata.py` wrote `operations = 1` with a zeroed `m_lastOpPoint` and
`m_lastOpRadius`, on the measured but wrongly generalised grounds that saved
compilers on Vangard had those fields zero. `1 == 0 + 1`, so every first load
took the narrow branch and reset a ZERO-SIZED box at the world origin. The pad's
grass was never invalidated. Because `m_lastDataRevision` starts at 0 again on
each newly instantiated component, it recurred on every reload -- which is why
the operator still found it floating after teleporting back.

`tcdata.Compiler` now defaults `operations` to 2, so a first load cannot equal
`before + 1`, and writes `m_lastOpPoint` = the zone centre at the pad height
with `m_lastOpRadius` = 32 m, so a client that does take the narrow branch
resets exactly the grass the wide branch would. Proven against the real bytes by
`/tmp/gt/verify_blob.py`; the op record is printed in every `flatten.py`
self-check line. `m_operations` has no other meaning: it is written only by
`InternalDoOperation` and read only by `Save` and `CheckLoad`.

This fix CANNOT be verified from this host -- there is no Valheim client here.
Two witness pads are left in the world for the operator to confirm it, both
carrying the corrected blob, both cleared and flat, on empty west-isle meadow
128 m apart and far from every installation:

* (-1152, 17.60, 1408), zone (-18, 22)
* (-1152, 24.56, 1664), zone (-18, 26)

## 2. Flatten terrain, second

`terraform/flatten.py` levels the pad to `solved.flatten_cost.target_y`, the
median terrain height over the footprint. That number is the PAD HEIGHT and
everything after it is expressed relative to it.

Note for anyone reading `solved.y`: that field is the HIGHEST terrain cell in the
footprint, not the pad. It is the raw ground drop from the site search and is not
the altitude anything should be placed at once the pad exists.

## 3. Place the structure, third

`terraform/sites.py` calls `blueprints/to_rcon_plan.py --align floor-center`,
which places the blueprint origin at

```
placement_y = flatten_cost.target_y - blueprint_datum.base_y
```

`base_y` is the blueprint's FLOOR PLANE in blueprint-local metres, measured by
`blueprints/base_geometry.py` from the game's own piece colliders and recorded
per placement in `placements.yaml` under `blueprint_datum`. Read
`base_geometry.py`'s header for what it measures and what it refuses; the short
version is that a piece's PIVOT is not a surface (`stone_floor_2x2` is a
2 x 2 x 1 m solid pivoted at mid-thickness) and the previous rule aligned pivots.

Structures go up before loose objects because:

* The loose objects are positioned relative to the pad centre and are meant to
  sit on the finished ground beside and inside the building. Placing them first
  means a structure piece can be spawned into the same volume.
* `to_rcon_plan.py` emits its `spawn` commands bottom-up in Y so that
  `WearNTear` support exists below each piece as it appears. That ordering is
  internal to the structure and is undone if unrelated objects are interleaved.

## 4. Place loose objects, last

`terraform/stock.py` spawns stations, props and chests at
`flatten_cost.target_y`, i.e. ON the pad. This is the step that was already
correct in live play, and it is the reason the structure datum is defined the way
it is: a ground-resting prefab's solid collider starts at its own pivot
(MEASURED: `piece_workbench` +0.025, `forge` +0.035, `hearth` -0.017,
`piece_chest_wood` +0.0003, `smelter` 0.000, `charcoal_kiln` -0.023 m), so a prop
spawned at the pad height rests on the pad. Aligning the blueprint's lowest
walkable floor surface to the same pad height makes the building agree with the
props placed beside it.

## What the order does NOT fix

A blueprint captured across a slope has floor levels at several heights and a
flat pad has one. Only the lowest walkable level can be flush. The residual for
every other level is reported per placement as
`blueprint_datum.walkable_levels[].above_pad_m` — on
`pre-bonemass/iron-era-workshop` the lowest level lands flush and the main floor
stands 0.34 m proud. That is geometry, not a defect, and no ordering of the four
steps changes it.

## The second gate: a flatten inside a generated POI

Found while restoring the experiment, and now fixed. Pad C's zone held a
`LocationProxy` with a `TreasureChest_meadows_buried`, and the flatten cut
6.65 m of ground out from under it and reported nothing, because `flatten.py`
had no location check of its own and relied on `clearing/area.py`'s stand-off,
which only runs when CLEARING runs. That is the same class as the POI damage
recorded as unrepairable in the pre-wipe world, where marker-based stand-off
deleted location content because a location's PIECES were measured reaching
43 m past their own marker. Deleting the compiler restored that zone exactly,
because its objects were already sitting on the unmodified generated height.

`terraform/locations.py` is the gate, run by `flatten.py apply` beside the
zone-generated check, and like it, refusing before anything at all is written.
It uses two sources with an explicit authority ordering, because neither is
sufficient:

* The LIVE world is authoritative for POSITION. Every generated location plants
  a prefab literally named `LocationProxy`; the type lives in the ZDO's
  `location` int hash, which `findObjects` does not print. So a live query gives
  a position and nothing else -- but it DOES see the ~190 POIs
  More_World_Locations injects, which no mod-free dump can.
* The mod-free `tools/seedscan` dump is authoritative for the per-type RADIUS,
  and is used ONLY to identify a live marker by exact position and thereby earn
  it a SMALLER stand-off. A marker the dump cannot identify gets the world's
  worst case, taken from the dump's own maximum rather than a hand-picked
  number: MEASURED, 32 m is the largest declared exterior radius among its
  12,301 instances.

MEASURED that the cross-read is both necessary and effective, over the 24 zones
this experiment generated. Four live markers exist in them:

```
(-1081.88, 1792.22)  WoodHouse2, d = 0.00 m  -> identified, radius 8 m, stand-off 34 m
(-1146.27, 1797.76)  nearest at 15.16 m      -> unidentified, worst case, stand-off 58 m
(-1150.16, 1853.63)  nearest at 29.69 m      -> unidentified
(-1223.76, 1777.60)  nearest at 71.77 m      -> unidentified
```

One in four matched EXACTLY, which is why the identity tolerance is half a metre
rather than a search radius; three are invisible to a mod-free dump. Do NOT read
the dump's `placed` field as "this one is in the world" -- MEASURED, it is false
on all 12,301 records, including `StartTemple`.

The stand-off carries its provenance per term, in the code and in every line it
prints, because one of these three numbers is a measurement and two are not:

```
standoff = max(exteriorRadius, interiorRadius)   MEASURED per type, from ZoneLocation
         + 11 m piece overshoot                  INFERRED from ONE observation
         + 15 m safety margin                    a taste pick
```

Distance is measured to the pad RECTANGLE, not to the pad centre: that is
strictly tighter than a half-diagonal circle and needs no margin for its own
shape. The refusal is measured to fire on the pad that suffered the defect --
re-running pad C's flatten now reports the marker at 0.0 m against a 58 m
stand-off and writes nothing -- and a clean pad passes. There is no override:
the pad goes through the ledger, and the ledger's schema refuses any
`terrain_write` whose `location_check.verdict` is not `clear`, for every writer.

Re-measuring the 11 m overshoot for more location types is the honest
improvement still outstanding, and it belongs to whoever owns the dump.

A location with an INTERIOR is not bounded by any radius about its marker.
Its interior is built in the sky (y ~5000) and fills its marker's whole 64 m
zone, and `objects_remove`/`objects_count` filter by `Utils.DistanceXZ`, so a
clearing cylinder that reaches the zone reaches the interior. MEASURED on
Fresh42 drive 5: 992 of 992 objects above y 1000 lie in the zone square of a
location declaring `interiorRadius` > 0, up to 45.0 m from the marker and
1.24 m from the zone edge; wt-south and wt-east were refused at step 5 on
Crypt4's and Crypt3's interior doors and torches after siting and the dump
gate had both passed them. `clearance.Locations.cylinder_room`
(`CLEARING_POPULATION_DEPENDS_ON`: pieces, interior_zone) is now the one rule,
asked through `settlements/build.py::clearing_gate`. `settlements/plan.py`
sites every searched outlier pad and every town plot by the full population
(`CLEARING_GATE_DEPENDS_ON`), so such a pad is never placed; a DECLARED pad is
not moved and carries the verdict (`pad.clearing_gate`). The build's own
pre-generation gate stays on `pieces` (`BUILD_ROOM_DEPENDS_ON`) and records
the full room, because the live cylinder census after it is exact and the
zone is only an upper bound (on Ulfsland's save it would refuse six standing
units whose cylinders hold no non-natural interior object).
`plan.py --selftest` neuters it to pieces-only on wt-south's drive-5 spot.
Stage 7 asks the same rule (`blueprints/solve_placements.py::
clearing_room_refusal`, `CLEARING_ROOM_DEPENDS_ON`): a candidate whose body
pad's step-1 cylinder reaches one is recorded as a `clearing_room_m`
violation, and a ring pad that does is dropped as a neighbour's.

## The third gate: a pad that does not stand under its whole body

A pad was sized on the body UNTURNED (`settlements/layout.py`: yaw-0 footprint
plus 2 x 3 m) and stamped axis-aligned, while the body is placed turned to its
street. `Heightmap.GetHeight` answers every query with its nearest 1 m vertex,
so the ground a body stands on is the set of vertices under its footprint AT
ITS YAW, and a vertex outside the pad keeps its generated height under a wall.
MEASURED 2026-09-24, piece-exact: on `plan.Fresh42.json` 1 of 49 units
(`vestvik-hall-1`, yaw 50, 1.740 m, 13 of 172 vertices); on Ulfsland's
`plan.json` 3 of 50, all standing (`vestvik-hall-1` yaw 70 1.920 m 19/169,
`stenvik-longhouse-1`/`-2` yaw 30 0.482 m 4/169 and 6/170). The gate passed
all three: `pad_coverage` is populated by the pad's own rectangle and
`pad_flatness` by the samples the pad wrote, and neither contains ground
outside the pad.

`terraform/flatten.py::pad_extent_at_yaw` derives the pad from the body's
footprint at its yaw (`PAD_EXTENT_DEPENDS_ON`: declared pad as a floor, body
footprint plus margin, the emitter's own `xz_footprint` at the yaw, completed
on the lattice the writer stamps). `settlements/build.py::unit_records` hands
every consumer that pad. `flatten.placed_body_cover` is the check:
`build.py emit_pad` and `flatten.py apply` REFUSE a pad that leaves any
vertex under the body unstamped, before anything is written.
`flatten.py selftest` neuters each term on geometry built in memory.

Every tool that decides a pad under a body asks that one rule; none restates
it. The installation solver (`blueprints/solve_placements.py::written_pad`,
stage 7) and the installation re-costs (`network/recost_pad.py`,
`relocate_pad.py`) go through `flatten.placed_pad_at_yaw` at the preset
path's zero margin, with the rectangle the search assumed as the floor; the
block's `footprint_orientation` is that pad, `pad_extent` records the
derivation, and `site_finder.engine_bill` bills it. Before this, stage 7
wrote the searched rectangle axis-aligned and the live Fresh42 build's step 2
REFUSED 8 of 13 installations on it (worst `mountain-outpost`, searched on
the swapped rectangle: 308 of 748 vertices, 6.588 m); Ulfsland's set holds
the same 8 (worst `sandbox-harbour` 7.348 m). The harbour terraces
(`settlements/harbour_city.py::house_seat`) and the portal hall
(`settlements/portal_hall.py::hall_pad`) are on it too.
`solve_placements.py --selftest` neuters it on the emitter itself.

A pad that grows changes step 1 as well: `clearing/area.py` sizes the
clearing cylinder from the same `pad_extent` (Fresh42 `mountain-outpost`
20.44 -> 24.50 m), so a unit cleared on the old pad has an uncleared ring.
`buildstage --resume` keys a step on its argv, which names the placements
file and not its content: re-run step 1 for a re-solved unit explicitly.

## The fourth gate: a fixture that stands off the ground levelled for it

Step 6 (`terraform/stock.py`) stands stations, props, chests and portals at
the PAD height, and step 5 (`settlements/build.py::hub_spots`) stands the
hall's settlement arches and their boards there too. The search reached the
site square (`requirement.footprint_m / 2`) while step 2 levelled the body's
rectangle. MEASURED on Fresh42 drive 2 against the drive-2 applied ground:
`mistlands-blackforge-base` 28 of 32 fixtures off its 18.8 x 12.8 m pad (the
cartography table, seq 1978, 1.6 m past its edge with its top 1.98 m under the
bank); the portal hall 76 of 106 fixtures, arches and board pieces off its
34.8 m pad; over the other 10 installations 93 of 164.

Two things now hold it:

* `PAD_EXTENT_DEPENDS_ON` `fixture_ring`: stage 7 sizes the pad for what will
  stand on it. `stock.fixture_ring` is step 6's own solve (and, for the hub,
  step 5's arch-and-board packing over the declared settlement tags) as box
  offsets from the centre, so one solve serves every candidate; the block
  records it under `solved.fixture_ring`. A ring pad the clamp or a
  neighbour (the step-1 clearing cylinder reaching a location) forbids is
  dropped for the body's pad and recorded `refused` with its numbers; the
  site is never lost to its fixtures.
* `stock.SEAT_DEPENDS_ON` `written_pad`: every fixture search asks
  `stock.PadSeat`, the writer's own `stamps` over `answering_vertices`, and a
  fixture the written pad does not reach is REFUSED with its number (the
  metres its site-square spot stands past the pad's edge) and the rest of the
  site is stocked. `settlements/build.py` seats the hall's arches and boards
  and each settlement's site end and board the same way.

Seating each fixture on the natural ground under it was measured and lost:
at a 0.5 m level tolerance it seats blackforge's 32 but leaves its station
row 2.2-2.9 m below the pad across a bank, and seats 2 of the hall's 16
settlement arches. The ring pads at the drive-2 sites seat all of them
(blackforge 44.8 x 34.8 m, cut 7.14 m; the hall 56.8 m, cut 6.25 m).
`stock.py selftest`, `flatten.py selftest` and `build.py selftest` neuter
both. A declared duplicate (`flatten.declared_duplicate`, the one predicate
steps 1, 2 and 6 ask) is neither cleared, levelled nor stocked: drive 2
levelled `sandbox-harbour`'s pad twice (seqs 565 and 568).

### The yard is not worth the site (`LEVELLABLE_PAD_DEPENDS_ON`)

The declared `footprint_m` pad is a floor under the body, and the ring fallback
above drops to "the body's pad" -- which carries the same floor. Where the
floor is the whole problem that is the same rectangle again. MEASURED on Fresh42
drive 11: `pre-bonemass#iron-era-workshop` declares 70 m over a 22.3 x 23.9 m
body, all 13 candidates were past the +/-8 m clamp on it (chosen site: 442 of
5041 samples, cut 10.54 m, fill 9.569 m), stage 7 recorded the block REFUSED,
and build step 1 refused the unit -- while the same centre levels a 50 m pad.

`blueprints/solve_placements.py::level_pool` bills the pool on the declared
pad first; only where NO candidate levels it does the floor give way, 5 m at a
time, the whole pool re-billed at each step and never below the body at its yaw
plus its fixture ring. The pad written is the largest any candidate levels and
the block records `pad_extent.floor` and `pad_ladder` (every rung with its
numbers); a candidate that levels its declared pad is untouched. Where nothing
levels on any rung `rank_pool` takes the candidate least past the clamp and the
block is REFUSED with every rung's numbers, as before. Fresh42's
iron-era-workshop now stands at the same centre on 55 x 55 m (cut 7.869 m, fill
7.58 m); `clearing/area.py` sizes its cylinder from that pad (51.50 -> 40.89 m),
so step 1 clears the smaller area. A re-solved unit's step 1 must be re-run
against the new placements file: `buildstage --resume` keys a step on its argv.
