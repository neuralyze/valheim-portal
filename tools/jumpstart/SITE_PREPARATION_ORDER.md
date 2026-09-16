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
stand-off and writes nothing -- and a clean pad passes. `--allow-near-location`
overrides it and prints the full violation list; there is no silent override.

Re-measuring the 11 m overshoot for more location types is the honest
improvement still outstanding, and it belongs to whoever owns the dump.
