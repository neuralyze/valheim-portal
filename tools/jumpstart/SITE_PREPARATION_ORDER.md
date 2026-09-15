# Site preparation order

The order in which a jumpstart site is built, and why each step has to come
where it does. This is the contract between the vegetation clearing step
(`tools/jumpstart/clearing/`), the terrain step (`tools/jumpstart/terraform/`)
and the blueprint placement step (`tools/jumpstart/blueprints/`). Owned by the
blueprint side because the vertical datum decision lives there.

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

## 0. Generate the site's zones, before trying to clear anything

With no players online the site's zones have not been generated, so its trees
and rocks DO NOT EXIST as ZDOs yet and a delete-only clearing step has nothing
to delete. MEASURED by `SiteClearing` on a `/tmp` sandbox of the same build: a
fresh world had ghost-generated only 81 zones, every `_ZoneCtrl` of which sits at
(1000000, 0, 1000000), and zero vegetation anywhere in the save.

So clearing is two operations, in this order: pre-generate the site's zones
headlessly (Upgrade World `zones_generate`), then remove the vegetation
(`objects_remove`). A side benefit worth knowing about: after step 0 the flatten
and placement steps write into zones that already exist, instead of relying on
the zone being created later.

## 1. Clear vegetation, before the terrain is cut

Flattening terrain does not remove trees. The terrain step writes a `TCData`
byte array into the zone's `_TerrainCompiler` ZDO, which changes the heightmap
and nothing else; every `Beech1`, `FirTree`, `Pinetree_01` and rock in the
footprint is a separate ZDO that survives untouched and ends up standing through
whatever gets built there. MEASURED in live play on Ulfsland on 2026-09-15: the
`pre-bonemass/iron-era-workshop` pad flattened correctly and the trees were
still standing in it.

Clearing must come before flattening rather than after, for two reasons:

* A tree removed after the pad is cut leaves its stump geometry and any
  attached `TreeLog` at the pre-flatten altitude, so the cleanup is visible.
* The clearing step decides what to remove from the footprint recorded in
  `placements.yaml` (`requirement.footprint_m`, `requirement.max_flat_m`,
  `requirement.location_clearance_m`, `rotation.yaw`). Those are seed-independent
  declarations, available before any terrain is touched, so there is no ordering
  dependency in the other direction.

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
