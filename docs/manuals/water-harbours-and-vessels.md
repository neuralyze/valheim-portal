# Water, harbours and vessels

**A manual for building anything that touches water in a programmatically-built
Valheim world.** Written for an agent with no memory of the session that
produced it. Every rule below carries the measurement that produced it and the
defect it prevents. Where a technique was tried and failed, it is recorded as a
refusal with its numbers, because the failures cost more than the successes.

- **World:** Ulfsland, seed string `Pirate68`, Valheim 1.0.12 / network 40,
  worldGenVersion 2, world version 41.
- **Measurement window:** 2026-09-16 through 2026-09-18. Where two numbers
  conflict, the later one wins and both are shown.
- **Live tooling this manual documents:**
  `tools/jumpstart/settlements/waterfront.py` (4,671 lines, the authority on
  berths and vessels), `tools/jumpstart/settlements/harbour_city.py` (2,056
  lines, sea walls, gates, boathouses, lighthouses),
  `tools/jumpstart/crossings/water.py` (the water datums and the `VHPATCH1`
  raster), `tools/jumpstart/crossings/assemble.py` (pier/slip/pile geometry),
  `tools/jumpstart/crossings/survey.py` (bay scoring),
  `tools/jumpstart/terraform/WaterProof.cs` + `run_waterproof.sh` (the water
  instrument behind section 8.1's measurement) and
  `tools/jumpstart/terraform/tcdata.py` (the `TCData` encoder both the live
  transport and that instrument write through).
- **Ledger of record:** `tools/jumpstart/ledger/runs/Ulfsland/ledger.jsonl`,
  2,483 lines, append-only, `seq` is NOT a total order (the chain forked at
  file line 47 and duplicate `seq` labels exist — index by FILE LINE).

---

## 0. The instruments, and what each one cannot answer

| # | Instrument | Answers | Does NOT answer |
|---|---|---|---|
| I1 | `VHPATCH1` PatchScan raster (`crossings/water.py::Field`), seed `Pirate68`, run in a sandbox mirror with the live `BepInEx` excluded | GENERATED height and biome, rivers included | anything placed; any terrain delta |
| I2 | `roads/applied.py::Applied` over the ledger's `terrain_write` blobs, unioned in FILE ORDER | the APPLIED surface a player stands on | pieces |
| I3 | the ledger itself | what we built, where, and its postconditions | live world state |
| I4 | `/tmp/harbour/piece_geometry_modded.json` — modded-sandbox collider dump, solid non-trigger boxes only | per-prefab solid AABBs, pivots, `Piece`/`WearNTear` flags | live `ZNetScene` membership |
| I5 | live RCON `objects_count` / `findObjects`, always SCOPED | what actually stands, per prefab, in an XZ cylinder | y — `objects_count` is y-blind |
| I6 | `tools/jumpstart/terraform/WaterProof.cs` + `run_waterproof.sh` — a BepInEx plugin in a sandbox mirror of the dedicated server, against a COPY of the world, writing through `terraform/tcdata.py::Compiler` (the encoder the live transport uses) and then reading the GAME's own `Heightmap.GetHeight`, `Floating.GetLiquidLevel`, `Floating.IsUnderWater`, `WaterVolume.GetWaterSurface`, `WaterVolume.Depth`, `Heightmap.GetOceanDepth`, plus real hulls left to Unity physics | what a terrain write does to WATER: applied bed, water surface, wetness, and whether a real hull floats over it | anything a CLIENT draws — a headless server does not render the water surface mesh |

**Rule: the instrument is the oracle.** A planned profile, a nearest sample, or
this run's own compilers each answer a different question and each has produced
a false clear on this world. The surface a hull floats over is I1; the surface a
player walks on is I1 unioned with I2.

**I6 is the instrument this manual's water rule now rests on** (§8.1), and it
carries two traps of its own, both recorded because each returns a confident WRONG
answer: `Floating::GetWaterLevel` reads `s_waterVolumeMask` with **no lazy init**
while `GetLiquidLevel` initialises it, so on a headless server every point in the
world returns **−10000**, including 6.7 m of undisturbed natural water — use
`GetLiquidLevel`; and `Game::FixedUpdate` calls
`ZNet.SetReferencePosition(1000000, 0, 1000000)` **unconditionally every physics
tick**, so a dedicated server holds no active zones and `ZNetScene::RemoveObjects`
culls anything a harness spawns — three float tests returned
`DESTROYED_BEFORE_SETTLE`, which READS as "the hull sank" and MEANS "the harness
was deleted". Full tables and the reproduce command: `local://dredge-experiment.md`.

**Field alignment is a hard gate.** `waterfront.Surface` REFUSES a patch whose
`cx - half + step/2` is not an integer, because only then does every sample sit
exactly on a `TerrainComp` sample centre. `/tmp/roads/h1m.bin` was refused for
this reason (its `mainland` patch has `x0 = -801.5`). Regenerate with
`half = k + 0.5`.

**The noise floor is 0.0275 m.** Two independently-scanned aligned 1 m fields
were cross-checked at six ferry control points (two pier heads, two pier roots,
two planned berths); the worst disagreement was 0.0275 m. Any berth verdict
decided inside that band is not a measurement. This is why
`BERTH_MARGIN_FLOOR_M = 0.25`.

### SEED-DERIVED vs SAVE-DERIVED vs MOD-DERIVED

This distinction is the spine of the whole exercise. Get it wrong and a world
regeneration silently invalidates half your numbers.

| Class | Instrument | Survives regeneration? | Survives a mod change? | Examples from this manual |
|---|---|---|---|---|
| **SEED-DERIVED** | I1 | **YES** | yes | every depth, every cross-section span, the closing sweep and the spit, the wall line and each module's foot height, gate clear width and the water under it, bay depths, berth depths, navigable areas, biome coverage, the pad census against the +/-8 m clamp |
| **SAVE-DERIVED** | I2, I3, I5 | **NO — must be re-measured** | n/a | the applied road surface and every deck/road step computed from it, live `LocationProxy` markers and stand-offs, every piece census, the existing waterfront sites and their berthed hulls, the ledger's terrain blobs |
| **MOD-DERIVED** | I4 | yes | **NO** | `VESSELS` draughts (`Ship.m_waterLevelOffset`) and hull AABBs, `fb_` ZNetScene membership, every prefab pivot and clear opening |

A regenerated world has NO roads, NO pieces and NO deltas. The geometric design
re-derives from the seed and can be re-run unchanged; every road junction,
marker stand-off and piece count must be retaken.

---

## 1. The water plane is y = 30.0. Every depth is measured against it, never against zero

```python
# crossings/water.py
WATER_LEVEL = 30.0   # MEASURED: ZoneSystem::c_WaterLevel = static literal
                     # float32(30.) in assembly_valheim.dll 1.0.12
LAND_LEVEL  = 30.5   # land threshold, 0.5 m above the plane, so a shoreline
                     # sample is never mistaken for buildable dry ground
```

`depth(x, z) = 30.0 - surface(x, z)`. Positive is water, negative is dry ground.
`h > 0` is never used as a wetness test anywhere in this tooling.

**The defect this prevents:** the operator's own "great natural harbor at
1050, 720" reads generated `h = 26.81`. Against zero that looks like 26.81 m of
land. Against y = 30.0 it is **3.19 m of water** and the coordinate is a point
on the WEST BANK of the inlet, not the middle of it — its clearance to the
nearest dry ground is 7.8 m and the real channel centreline at that latitude is
`x = 1084`. The harbour was real; the coordinate was not, and the operator was
told so with the number (2026-09-17).

**Second defect, same class.** The default existence-probe position in
`waterfront.py` was described in a record as "open meadow". Measured on the
composed applied surface it is **15.241 m, i.e. 14.759 m BELOW c_WaterLevel
30.0** — the seabed — and all three 24 m-spaced probe slots were underwater. The
default was moved to (56, -288), where the slots read 35.00 / 34.90 / 35.97 m,
about 5 m of freeboard.

Derived datums, all against the plane:

| Datum | Value | Source |
|---|---|---|
| `c_WaterLevel` | 30.0 | `assembly_valheim.dll` literal |
| `LAND_LEVEL` | 30.5 | project threshold, plane + 0.5 |
| `DECK_DATUM_Y` (`A.DECK_Y`) | **30.6** | walking surface of every over-water deck on this world |
| `wood_floor` authored y | 30.5031 | so that 30.5031 + 0.097 = 30.597 ~= 30.6 |
| `FLOOR_TOP_OFFSET` | 0.097 | `wood_floor` top face above its own origin |
| ocean-floor clamp | 30.0 m of depth | the generator bottoms out here; relevant to pile reach |

The deck datum gives **60 cm of headroom beside open water**. That is why no
waterfront structure in this tooling writes terrain UNDER OR BESIDE ITSELF: there
is no room for a cut, and a levelling flatten at a deck datum RAISES the bed past
the plane and takes the water away — the one direction that cannot be undone
(section 8.1). Cutting the bed DOWNWARD, away from the plane, is a different
operation with the opposite effect and is measured in section 8.1 as well.

---

## 2. Depth must be measured under the hull's OWN elliptical footprint, flood-fill connected to open sea — never at a point

This is the single most expensive lesson on this world.

### The defect, with its full measurement

`Crossings` placed a `VikingShip` at `ferry-terminal-eastisle`, ledger **seq 46,
file line 51, 2026-09-16T03:04:26Z**, at (1943.79, 30.0, 568.37). The berth
record that justified it measured **ONE POINT at the spawn position and read
4.21 m**. The point is in deep water. The hull is not.

`FerryPair` re-measured it at its own live ZDO pose on 2026-09-18T03:52:08Z
(ledger seq 2474) before retiring it:

```
VikingShip, live pose (1943.55, 29.75, 568.46), yaw 296.0 deg
hull 9.16 x 21.57 m, 153 samples inside the ellipse
  min_depth_under_hull_m   -0.465      <- SEABED ABOVE THE WATER PLANE
  max_depth_under_hull_m    8.308
  required_depth_m          2.5        (draught 1.5 + 1.0 keel clearance)
  margin_over_requirement  -2.965
  water_under_keel_m       -1.965
  every_sample_open_sea     false
  y_vs_water_plane_m       -0.25       <- the hull itself sat below the plane
also measured at the pier bearing (335 deg, not its live 296 deg):
  min_depth_under_hull_m    0.244      margin -2.256  -- still aground
```

There was **dry land under the bow**, against 2.5 m required. It was placed
2026-09-16T03:04:26Z and retired 2026-09-18T03:52:08Z: **48 h 47 m 42 s
aground**, through several sessions and one operator inspection. (A verbal
report of "weeks" circulated; the ledger timestamps are the authority and they
say two days. Recorded because a manual that inflates its own evidence is worse
than one that has less.)

Note the second line of that measurement: measuring at the PIER's bearing
instead of the hull's own live yaw changes the answer by 0.709 m. **Measure at
the object's own rotation**, read off its ZDO, not at the bearing you think it
should have.

### The same defect, caught before placement

At harbour B, `(1196, 470)` read **3.657 m at the point** while a `VikingShip`'s
own ellipse touched **1.30 m short** of its 2.5 m requirement. A neighbouring
hand-picked berth `(1185, 458)` read 1.96 m and every hull failed under its
footprint. And a first hand-picked pass at a boathouse produced a boat bay whose
shallowest sample was **-0.755 m — dry land under a boat bay**.

**Rule: solve positions, never hand-pick them.** Every hand-picked berth and bay
attempted on this world failed under its own footprint. Every solved one passed.

### The measurement, as code

```python
# settlements/waterfront.py::hull_depths
def hull_depths(surf, bearing, bx, bz, vessel):
    """(min, max, connected, samples, field) water depth under a hull here.

    The footprint is an ELLIPSE of the vessel's solid-collider half-extents
    rotated onto the pier bearing -- not a disc and not a point: a point check
    passes on a 3 m pothole inside a 2 m shoal and a 21 m hull does not fit in
    a pothole.  The binding number is the SHALLOWEST sample, because that is
    what the keel touches.
    """
```

Three things make it a measurement rather than a guess:

1. **An ellipse of the SOLID collider half-extents**, rotated onto the vessel's
   yaw. Not a disc: a disc of radius `hull_l/2` samples water the hull never
   occupies and rejects good berths; a disc of radius `hull_w/2` misses the bow.
2. **The SHALLOWEST sample decides.** `min(depth[hull])`, not the mean, not the
   max, not the centre. The keel touches the shallowest point.
3. **EVERY sample must be in water connected to the open sea**, and the mask
   must be indexed correctly. This subtlety is written into the source because
   it produced a false clear:

```python
# `open_sea[ii, jj][hull]`, NOT `(hull & open_sea).all()`: the second form
# is False wherever the hull mask is False, i.e. everywhere outside the hull.
```

`(hull & open_sea).all()` is `False` for every berth in the world. It looks like
a strict test and is in fact a test that always fails.

### Connectivity: the flood-fill

```python
# settlements/waterfront.py::sea_mask
def sea_mask(fld, min_depth_m):
    """(deep, open) where `deep` is depth >= min_depth and `open` is the
    subset of `deep` that is 4-connected to the patch BOUNDARY."""
    from scipy import ndimage
    depth = W.WATER_LEVEL - fld.heights         # against 30.0, never zero
    deep = depth >= min_depth_m
    lab, _ = ndimage.label(deep)
    edge = set(lab[0, :].tolist())  | set(lab[-1, :].tolist()) \
         | set(lab[:, 0].tolist()) | set(lab[:, -1].tolist())
    edge.discard(0)
    return deep, np.isin(lab, list(edge))
```

The boundary test is what makes this a NAVIGABILITY measurement rather than a
depth measurement. Note the mask is computed **at the vessel's own required
depth**, so connectivity means "connected by water this hull can actually sail
through", not "connected by any wet cell".

**The defect it prevents:** a 9 m hole behind a sandbar berths nothing. On this
world the concrete case is the **35.5 ha body at (1086, 1110)**, which scored as
a candidate port until the flood-fill rejected it: it is an INLAND LAKE, not
4-connected to the patch boundary, so nothing can sail out of it. A sheltered
pool that is not connected to open sea is not a berth.

### The full berth verdict

```
required_depth_m        = VESSELS[v]["draught_m"] + KEEL_CLEARANCE_M
floats                  = min_depth_under_hull >= required_depth_m
                          AND every hull sample in open_sea
clears_margin_floor     = min_depth_under_hull >= required + 0.25
berthable_here          = floats AND admitted by every gate on the route
```

`floats` is reported; `clears_margin_floor` is what allows a berth to be
CHOSEN. Candidates that float but miss the floor are reported anyway, because a
guard that narrows a request has to say what it narrowed. Measured instance: at
`ferry-terminal-east` the plain `floats` test accepted a planned berth at
**3.050 m against 3.0 m required — a 0.050 m margin, 1.8x the instrument's own
0.0275 m disagreement**. Moving the berth **3.0 m seaward** bought **4.647 m and
a +1.647 m margin** (ledger seq 2468, 2026-09-18T03:50:31Z, 99 candidates
swept, 110 hull samples).

---

## 3. A `mesh_render` bound is not a hull

```
VikingShip, same prefab, two instruments:
  solid non-trigger collider AABB   9.16 x 21.57 m     <- the hull
  mesh_render bound                11.64 x 35.43 m     <- a water-ripple PLANE
```

The `mesh_render` bound is **27% wider and 64% longer**. Using it would have
**oversized every slip on this world by 60%**. The hull filter (solid,
non-trigger colliders only, `mesh_render` excluded) is CALIBRATED: it reproduces
the 9.16 x 21.56 m recorded for `VikingShip` in ledger seq 33 to 0.01 m.

The same error class recurs on architecture:

- `fb_fortress_wall_gateway`: the `mesh`/`mesh_render` rows report the whole
  **20 x 11.87 x 6.8 m envelope**. The BOX colliders report solid boxes at
  `x[-10.002, -1.725]` and `x[1.704, 10.004]`, i.e. a **3.429 m clear
  opening** — against a 9.16 m minimum large-hull beam. A mesh bound says a
  ship fits; the colliders say it cannot.
- `fb_fortress_wall_portcullis`: boxes at `x[-10.002, -3.656]` and
  `x[3.195, 10.004]` -> **6.851 m clear**. Also too narrow.
- `fb_fortress_wall_tower`: **15.402 m** mesh AABB, **10.229 m** walkable
  fighting platform. See section 11.

**Rule: for any opening, any walkable surface, and any hull, read the SOLID
BOX COLLIDERS. The mesh bound answers a different question and is useless for
all three.**

---

## 4. Per-vessel draughts, beams and clearances

`VESSELS` in `settlements/waterfront.py:121-143`. MEASURED, not read off a
mod README: dumped from `ZNetScene.m_prefabs` in a sandbox copy of the Ulfsland
server with Jotunn + JsonDotNET + OdinShip + RavenwoodVikingHouses +
RavenwoodRestorations staged — 4,872 prefabs, all 25 documented OdinShip
prefabs present. A name not in `ZNetScene` cannot appear in the table, so
`spawn_object` cannot silently place nothing. Every vessel has
`ZNetView.m_persistent = 1`, so a berthed ship is written to the database and
survives a restart.

- `draught_m` = `Ship.m_waterLevelOffset` — how far the float collider centre
  sits below the water plane. A berth shallower than this grounds.
- `hull_w` / `hull_l` = solid non-trigger collider AABB — the envelope a slip,
  a bay and a gate must clear.
- `KEEL_CLEARANCE_M = 1.0` — freeboard under the keel. Derived, not chosen: the
  project's own `MOOR_DEPTH_M` (`crossings/survey.py:55`) is 2.5 m for a 1.5 m
  draught, so the margin that number encodes is 1.0 m, spelled out once rather
  than re-derived per vessel.

| Prefab | Class | Draught (m) | Required depth = draught + 1.0 | Beam / hull W (m) | Hull L (m) | Mass |
|---|---|---|---|---|---|---|
| `DoubleRowingCanoe` | small | 0.8 | **1.8** | 3.84 | 8.20 | 600 |
| `RowingCanoe` | small | 1.0 | **2.0** | 7.68 | 6.59 | 500 |
| `LittleBoat` | small | 1.2 | **2.2** | 4.23 | 10.37 | 1000 |
| `Karve` (vanilla) | small | 1.2 | **2.2** | 7.68 | 10.12 | 1000 |
| `VikingShip` (vanilla Longship) | large | 1.5 | **2.5** | 9.16 | 21.57 | 2000 |
| `MercantShip` | large | 1.5 | **2.5** | 9.16 | 21.57 | 2000 |
| `BigCargoShip` | large | 1.5 | **2.5** | **14.10** | 20.05 | 2000 |
| `WarShip` | large | 1.7 | **2.7** | 10.07 | 20.08 | 2000 |
| `CargoShip` | large | **2.0** | **3.0** | 9.16 | 15.71 | 2000 |

Two gates, not one. **Draught gates depth; BEAM gates passage.**
`BigCargoShip` has the SHALLOWEST large-hull requirement (2.5 m) and the
WIDEST beam (14.10 m) — it floats where a `CargoShip` grounds and is refused by
gates a `CargoShip` sails through. Never rank vessels on a single number.

`CargoShip` at 2.0 m is the deepest-draught vessel available, so **3.0 m of
water under the whole footprint is the "any vessel in the world floats here"
threshold.**

### Measured berth results — the table that matters

| Site | Vessel | Shallowest under hull | Required | Verdict | Date |
|---|---|---|---|---|---|
| `boathouse-temple-strait` | `LittleBoat` | **2.384 m** | 2.2 | **FLOATS — built** (margin +0.184 m; this decision predates `BERTH_MARGIN_FLOOR_M`, added 2026-09-18, which it would not clear) | 2026-09-17 |
| `boathouse-temple-strait` | `CargoShip` | **0.038 m** | 3.0 | **REJECTED** — 0.038 m of water is the waterline; short by 2.962 m | 2026-09-17 |
| `boathouse-temple-strait` | `VikingShip` (planned) | **-0.888 m** | 2.5 | **REJECTED — WOULD HAVE GROUNDED** | 2026-09-17 |
| `harbour-temple-south` | `CargoShip` | 4.822 m | 3.0 | floats | 2026-09-17 |
| `harbour-vestvik` | `CargoShip` | 4.632 m | 3.0 | floats | 2026-09-17 |
| `dock-dockshore` | `CargoShip` | 4.148 m | 3.0 | floats | 2026-09-17 |
| `ferry-terminal-east` | `CargoShip` | 4.647 m (2.647 under keel) | 3.0 | floats, +1.647 margin | 2026-09-18 |
| `ferry-terminal-eastisle` | `CargoShip` | 3.389 m (1.389 under keel) | 3.0 | floats | 2026-09-18 |
| `ferry-terminal-eastisle` | `VikingShip` (standing) | **-0.465 m** | 2.5 | **AGROUND — retired** | 2026-09-18 |
| `dock-stationhub` | best of all 9, `DoubleRowingCanoe` | 0.540 m | 1.8 | **NO VESSEL FLOATS — NOT BUILT** | 2026-09-17 |

**The `boathouse-temple-strait` cause, stated plainly: the slip was sized off
the Longship's 11 m HULL WIDTH and nobody ever measured its WATER.** Getting
the hull geometry right and the depth wrong builds a slip shaped exactly for a
boat that cannot enter it.

**Read the three `boathouse-temple-strait` rows together — they are the same
pool of water measured by three different footprints, and the shallowest
sample falls MONOTONICALLY with hull length:** `LittleBoat` 10.37 m long reads
2.384 m, `CargoShip` 15.71 m reads 0.038 m, `VikingShip` 21.57 m reads
-0.888 m. A longer hull reaches further into the shoal, so **the depth
available is a property of the (position, footprint) PAIR, never of the
position alone.** One number cannot be cached per berth and reused for another
vessel; re-run `hull_depths` per vessel.

### Choosing the vessel for a berth

```python
# settlements/waterfront.py::pick_vessel -- the rule, verbatim from the record
"largest by draught whose own elliptical hull footprint proves it floats by at
 least the margin floor, and that fits the slip's inner width less 1 m of
 fender and its length. A large boathouse berthing a rowboat is the same
 defect as a slip that cannot float a boat."
```

Sort by `-draught_m`, reject on beam first (`hull_w > beam_cap - 1.0`), then on
footprint depth. If nothing fits, **return `None` and refuse** — never narrow
silently to a canoe. `ferry_pair()` raises `SystemExit` rather than berth
something that grounds: "A ferry terminal that cannot float a boat is a jetty."

### What a slip is FOR, since Valheim has no mooring mechanic

MEASURED from `Ship::CustomFixedUpdate`: it returns immediately unless
`m_nview.IsOwner()`; it forces `m_speed = Stop` and `m_rudderValue = 0` when
`m_players.Count == 0`; it then still applies buoyancy and damping through
`Rigidbody::AddForceAtPosition` plus `ApplyEdgeForce` every tick. So an unmanned
boat is inert while nobody is in its active area and subject to wave forces
while somebody is. There is nothing to tie a boat to and no field that pins it.
**Three solid sides bound that drift physically, with colliders, instead of
hoping.** Do not write "moored" on a sign — the word promises a mechanic that
does not exist; write "berthed".

---

## 5. Floatable water starts 45-60 m out from shore

MEASURED at `dock-stationhub` (root (510.41, 1058.09), bearing 115, planned
20 m jetty): **1,681 positions brute-forced within 60 m of the head.** The best
result across all nine vessels was a `DoubleRowingCanoe` at **0.540 m against
1.8 m needed**. Floatable water starts **45-60 m from the head**.

The site is structurally buildable. It berths NOTHING. **It was refused and the
refusal recorded with its numbers, rather than lengthened on the tool's own
authority.** That is the "a slip that cannot float a boat is scenery" case.

The corollary is a pier-length sweep, not a pier length:

- `dock-dockshore`: `crossings/plan.py::aim_seaward` **REFUSED a 24 m pier at
  every one of 72 bearings and at every head depth from 2.2 to 3.0 m** — the
  shelf is shallow and a short pier cannot reach water a boat floats in. Swept
  by length, **40 m** is the shortest the aimer accepts: root (691, -94),
  bearing 130, wet fraction 0.512 under the deck, head depth **4.33 m**. 48 m
  would reach 6.05 m and is not needed, because 4.33 m already clears
  `CargoShip`'s 2.0 m draught by 1.33 m. **A 24 m boathouse here would have
  been scenery.**
- `dock-northcape`: **20 / 24 / 28 / 32 / 40 m were ALL REFUSED.** Only 48 m is
  accepted, from root (-192, 2612) at bearing 60: wet fraction 0.816, head
  depth 3.25 m, clearing a `CargoShip` by 1.25 m. **This pier cannot be
  shortened.**

**Rule: sweep pier length against the aimer and take the shortest accepted
length. Never pick a length and then look for a bearing.**

### The shelter metric, and its limit

`crossings/survey.py::cmd_bays` scores four things separately rather than
rolling them into one opaque number:

| Metric | Definition | Failure it catches |
|---|---|---|
| `shelter` | fraction of 24 rays at `shelter_r` m that end on land | an exposed roadstead |
| `water_frac` | fraction of a `basin_r` m offshore disc that is wet | "a sheltered point with no water in front of it is a ditch" |
| `berth_depth` | deepest water within `basin_r` m; must clear `MOOR_DEPTH_M = 2.5` | a Longship grounds |
| `bank_y` | land height at the sample — the height the deck must reconcile with | "a 12 m cliff is not a dock site" |

Measured shelter values on this world: `boathouse-temple-strait` **0.96** (the
most enclosed water on either island), the Stenvik/Stathub creek **0.792**
(highest of all 14 key places), `dock-dockshore` 0.542, Vestvik 0.500,
`dock-northcape`'s viable seat 0.750, the absolute northern tip 0.417.

**High shelter is not sufficient and this was measured.** The Stenvik/Stathub
creek has the world's best shelter (0.792) and 5.03 m at its head, and was
REFUSED as a large port: it is **TOO NARROW — a 15.71 m `CargoShip` hull
straddles the banks at 0.558 m**. It is a sheltered SMALL-boat harbour beside a
town, not a port. The only site on this world satisfying BOTH "floats a large
ship" and "next to a town" is `harbour-vestvik` (village 227 m, `CargoShip`
4.632 m).

---

## 6. Harbour siting: what makes a natural harbour usable

Six things, each measured separately. A single score hides which one failed.

1. **Mouth width and the depth profile** — cross-sections, first-dry sample
   each side (selection `h > 30.5`), walked OUTWARD from the measured
   centreline at 0.5 m rather than read off a straight line. On harbour A both
   banks are irregular: between z = 716 and z = 728 the west shore moves
   **24 m east**. A quay laid on a straight line would be 24 m inland at one
   end and 24 m out to sea at the other.
2. **Depth percentiles against y = 30.0**, not a max. A max depth is one cell.
3. **Navigable area at each draught**, flood-fill connected to open sea — not a
   bounding box, and computed per draught because the answer changes.
4. **The navigable bottleneck**: twice the max-min clearance-to-shore over all
   water paths from basin to open sea. This is the number that says whether the
   natural geometry restricts a hull at all.
5. **Biome coverage in rings** about the basin centre, land only.
6. **Relief against the +/-8 m terrain clamp**, per candidate pad size — never
   per site.

### Worked example A — the 130 m fjord inlet at (1050, 720)

SEED-DERIVED throughout (I1, aligned 1 m patch `cityA` (1080,700) half 250.5
step 1 -> x0 830.0, z0 450.0). Surveyed 2026-09-17.

Water body containing the inlet: **177,104 m2**, deepest connected cell
`h = -6.63` (**36.63 m of water**) at (1055.5, 1021.5) — which is NORTH of the
basin, so **the mouth faces +z / north** and the head is at z ~= 640. Derive the
mouth direction from the deepest connected cell; do not assume it.

Cross-sections through `x = 1084`:

| z | west shore x | east shore x | span (m) | wet (m) | max depth (m) | mean wet depth (m) |
|---|---|---|---|---|---|---|
| 666 | 1065.5 | 1122.5 | **57.0** | — | — | — |
| 700 | 1001.5 | 1133.5 | 132.0 | 96.0 | 5.56 | 2.92 |
| 716 | 1019.5 | 1130.5 | 111.0 | 100.0 | 6.38 | 3.79 |
| 726 | 1033.5 | 1126.5 | 93.0 | 90.0 | 6.94 | 4.05 |
| 744 | 971.5 | 1122.5 | 151.0 | 140.0 | 6.63 | 2.68 |
| 752 | 1010.5 | 1122.5 | **112.0** | 107.0 | 6.65 | 3.48 |
| 766 | 1011.5 | 1138.5 | 127.0 | 123.0 | 7.78 | 4.46 |
| 768 | 1011.5 | 1174.5 | 163.0 | 152.0 | 8.06 | 4.07 |
| 780 | 962.5 | 1265.5 | 303.0 | 290.0 | 9.69 | 4.45 |
| 790 | 951.5 | 1270.5 | 319.0 | 314.0 | 12.98 | 6.31 |

**Verdict: a ~130 m long, 57-130 m wide basin that blows out to open coast past
z ~= 768.** A genuine natural harbour.

Depth percentiles over the whole inlet, against y = 30.0:
p10 **0.37**, p25 **1.03**, p50 **3.21**, p75 **5.49**, p90 **6.85**,
max **10.76 m**.

Navigable water, flood-fill connected to open sea:

| Required draught | connected navigable water (whole patch) | inside the basin |
|---|---|---|
| 0.60 m | 168,993 m2 | **11,859 m2** |
| 1.20 m | 164,091 m2 | 10,500 m2 |
| 1.60 m | 160,896 m2 | 9,883 m2 |

Navigable water reaches **z = 650 at 0.6 m** and **z = 672 at 1.6 m** — deep
water runs almost to the head. The navigable bottleneck between basin and open
sea is **74.21 m of clear passage**, so nothing about the natural geometry
restricts a hull.

Biome and relief, rings about (1084, 716), land only:

| ring | land | biome | h range | relief | slope med / p90 |
|---|---|---|---|---|---|
| 0-60 m | 1,850 m2 (16% dry) | **Meadows 100%** | 30.00-35.23 | 5.23 m | 13.3 / 22.9 deg |
| 60-120 m | 21,131 m2 (62%) | **Meadows 100%** | 30.00-38.85 | 8.85 m | 12.2 / 22.0 deg |
| 120-200 m | 48,867 m2 (61%) | **Meadows 100%** | 30.00-49.11 | 19.11 m | 15.0 / 29.4 deg |
| 200-300 m | 95,819 m2 (61%) | Meadows 90.1%, BlackForest 9.9% | 30.00-82.79 | 52.79 m | 17.7 / 32.1 deg |

Pad census against the +/-8 m clamp, measured per pad size against each pad's
own median floor:

| pad | candidates >=97% dry | fit +/-8 m | cut needed med / p90 / max |
|---|---|---|---|
| 16x16 m | 1401 | **1390 (99%)** | 2.15 / 4.69 / 10.25 m |
| 24x24 m | 556 | **527 (95%)** | 2.98 / 6.66 / 12.75 m |
| 32x32 m | 283 | 242 (86%) | 3.77 / 8.94 / 12.98 m |
| 48x48 m | 113 | 68 (60%) | 6.26 / 13.53 / 16.17 m |

**Verdict: terrace on 16 m and 24 m pads, never one platform. A 48 m pad fails
the clamp 40% of the time here, and a single large levelled plaza is exactly the
shape that refuses MID-WRITE** — which is the worst moment for a terrain
compiler to refuse.

### Worked example B — the larger southern fjord at (1190, 444)

`(1190, 444)`: `h = 26.20`, **3.80 m of water**, Meadows, clearance to shore
22.8 m — dead centre of the channel this time. Its open-sea probe is
(1316.5, 94.5), so **B opens SOUTH — the opposite hand to A**. Derive the hand
per harbour.

Cross-sections through `x = 1196`:

| z | west x | east x | span (m) | max depth (m) |
|---|---|---|---|---|
| 390 | 1099.5 | 1308.5 | 209.0 | 7.21 |
| 420 | 1117.5 | 1268.5 | 151.0 | 5.14 |
| 440 | 1146.5 | 1259.5 | 113.0 | 6.45 |
| 460 | 1169.5 | 1248.5 | 79.0 | 4.29 |
| 470 | 1178.5 | 1245.5 | 67.0 | 3.58 |
| 480 | 1172.5 | 1219.5 | 47.0 | 1.96 |
| 498 | 1175.5 | 1209.5 | **34.0** | 0.84 |

Basin **93,639 m2** within the window; depth **p50 6.73 m**, p90 11.23.
Floatable water connected to open sea: **87,744 m2 inside the basin at 1.2 m**.
Rings about (1196, 470): **Meadows 100% out to 200 m** (91.7% / BlackForest
8.3% at 200-300 m). Relief 5.87 m at 0-60 m, 14.78 m at 60-120 m, 33.81 m at
120-200 m. Pad census: 16 m **1480/1506 (98%)**, 24 m **546/588 (93%)**,
32 m 250/314 (80%), 48 m 64/126 (51%). Same verdict: terrace.

**B is the deeper and bigger harbour** (p50 6.73 vs 3.21). A is the more
defensible one. Those are different questions and the survey answers both
rather than ranking.

---

## 7. A span read off a cross-section is not a proof. The topological closure test

### The defect

The **three NARROWEST east-west sections in the whole of inlet A** — z = 728,
730, 732, spans **84.0, 85.0 and 88.0 m** — **DO NOT CLOSE IT.** The first dry
sample west of the centreline at those latitudes is **the tip of a SPIT with
water on both sides**. A wall abutting it leaves the sea a way round the end.

Three cheaper walls — 84, 85 and 88 m instead of the chosen 132 m — were
refused by this test. Without it, the world would have a 84 m curtain wall with
open water flowing past its western end, and no instrument in the build chain
would ever have said so: the wall would stand, every module would be grounded,
every piece count would be exact, and raiding ships would sail straight in.

### The test, written so it can be re-implemented

```
CLOSURE TEST -- for each candidate wall line L:

 1. Build the water mask on the SEED-DERIVED height field:
        wet = (30.0 - heights) > 0
    (Use depth > 0, not the vessel draught: a raider's hull is not yours, and
     a shallow channel round the end is still a way round the end.)

 2. Label connected components of `wet` with 4-connectivity.
    Identify:
        BASIN     = the component label at the harbour basin probe point
        OPEN_SEA  = the set of labels touching the patch BOUNDARY

 3. Remove the candidate line from the mask:
        wet_cut = wet AND NOT dilate(L, r)
    where L is rasterised along its full length INCLUDING its landward
    extensions, and r is at least the wall module's measured depth/2
    (`fb_fortress_wall` is 6.800 m deep, so r >= 3.4 m).

 4. RE-LABEL wet_cut from scratch.  Do not reuse step-2 labels.

 5. L CLOSES the harbour if and only if:
        label_of(basin_probe) in wet_cut   AND
        label_of(basin_probe) NOT IN labels_touching_boundary(wet_cut)

 6. Record for each accepted L: enclosed area, enclosed area at each draught,
    the max depth under the line, the applied delta under the line (must be
    none -- virgin ground), and the first-dry sample each side.

 7. Sweep ALL candidate lines and take the SHORTEST that closes, not the
    shortest cross-section.  These are different lines.  On inlet A the
    shortest cross-section is 84.0 m and the shortest CLOSING line is 132.0 m
    -- a 57% difference.
```

Two things that look like refinements and are actually required:

- **Step 4 is not optional.** Re-using the labels from step 2 keeps the basin
  and the sea in one label because the label array was computed before the cut.
- **Step 3 must dilate.** A one-cell-wide line through a 4-connected mask can
  leak diagonally. Dilating to the module's own footprint depth also makes the
  test answer the question you are actually asking: "does the structure I will
  build close this?"

### The chosen line for inlet A

**z = 752, x 1000.5 -> 1132.5, span 132.0 m.** The measured waterline is
1010.5...1122.5 = **112.0 m**; the line is extended **10 m onto dry ground at
each end**, so the wall dies into the banks instead of stopping at the water's
edge where the sea could round it.

```
applied h along the line   min 23.402   max 31.962
depth vs y=30.0            max 6.598 m, mean over wet 3.425 m
                           107 of 133 samples wet
ledger delta under line    NONE -- virgin generated ground (I2)
closes the basin           TRUE
encloses                   10,313 m2, of which 6,000 m2 is >= 1.2 m deep
west abutment (1010.5,752) h 30.435  dry, Meadows
east abutment (1122.5,752) h 30.522  dry, Meadows
```

For completeness, harbour B's shortest verified closing line is **z = 440, span
113.0 m**, enclosing 4,326 m2 (2,762 m2 >= 1.2 m), max depth under the wall
6.45 m; lines z = 450...498 do NOT close B's inner basin. **113 m of curtain to
enclose 4,326 m2 was refused as a poor trade** and B got quays, boathouses and
berths instead. A refusal with a number is a finding; a silent omission is not.

---

## 8. Sea walls and ship gates with ZERO terrain writes — and which terrain writes near water are actually safe

### 8.1 RAISING the bed removes water. LOWERING it adds water. The rule is about DIRECTION, not about the plane

**This section stated the wrong rule until 2026-09-18.** It said, in capitals,
*"NEVER WRITE TERRAIN THAT LOWERS GROUND TOWARDS `c_WaterLevel` UNDER OR BESIDE A
STRUCTURE"*, and gave as its mechanism *"draining water out from under a pier or a
shoreline"*. **Every refusal it produced was correct and none of them is being
relaxed. The mechanism it named is false**, and a correct refusal resting on a
false reason is how a later session argues it away.

**Where the rule came from, and it is worth reading before the correction**: the
`early-dock` defect, recorded verbatim in section 9 — *"the pad is the `early-dock`
defect, where the flatten removed the water from under the footprint and the pier
then read as floating"*. That observation is TRUE and was correctly measured. What
it measured was a **flatten to a deck datum**, which RAISES a seabed from below
y = 30.0 to above it. The generalisation from *"raising a bed past the plane
removes the water"* to *"never write terrain below the plane"* is a statement about
the PLANE made from evidence about the DIRECTION, and it was enforced as an
absolute for weeks.

**Both directions, measured in ONE run by I6** — five patches, 13 x 13 m, 169
samples each, `clamped` 0 on every blob, every |delta| <= 7.0 m against the
measured +/-8.0 m apply clamp (a 1.000 m held-back margin):

| operation | position | generated bed | applied bed | delta | depth before | depth after | hull? |
|---|---|---|---|---|---|---|---|
| **LOWER 7 m (dredge)** | (1088, 720) | 23.726 | 16.726 | **−7.0000** | 6.274 | **13.274 m of water** | floats |
| **RAISE to 30.9 (the recorded defect, reproduced)** | (1064, 686) | 27.208 | 30.900 | **+3.6917** | 2.792 | **−0.900, i.e. DRY GROUND** (`is_under_water` false) | n/a |
| **LOWER a DRY bank** | (1140, 720) | 33.493 | 26.493 | −7.0000 | −3.493 | **+3.507** | — |
| control, zone (20,10), **0.0 % naturally wet** | (1249, 665) | 34.567 | 27.567 | −7.0000 | −4.567 | **+2.434**, surface exactly **30.0000** | — |
| untouched natural control | (1088, 740) | 23.370 | 23.370 | 0.0000 | 6.630 | 6.630 | floats |

Water surface over the 7 m dredge read **30.2091**, and it read the SAME value at
all three altitudes sampled (0.1 m under the plane, mid-column, and 0.25 m off the
newly cut bed): there is no unflooded pocket at the bottom of a cut.

**Adjacent cells lose NOTHING.** Ring samples at **1, 2, 4 and 8 m** outside all
five patches, in both +x and +z: `applied − generated` is **EXACTLY 0.0000** and
`depth after == depth before` to 4 dp. A dredge does not drain its neighbours.

Re-measured on the REAL harbour A cut rather than a 13 m test patch, at build time
`[SAVE]`: game-read bed against computed write over **2,680 cut samples — max
0.0001 m (one print quantum), RMS 2.6e-05 m**, applied delta identical to 4 dp at
every sample; and at the **512 points whose whole bilinear support is unwritten,
max 0.0000 m, RMS 0.0, none nonzero.** The same readings scored against the bed
reflected across the basin diagonal give RMS **0.935 m**, a ~37,000x margin, which
is how that check rules out a transposition rather than merely agreeing with itself.
**Note the instrument fault found and recorded there**: a first collateral test
sampled the NEAREST lattice point and reported 235 of 955 positions moved, worst
2.9584 m. The blob was right and the test was wrong — piece positions are not on the
1 m lattice and `Heightmap` interpolates bilinearly over the four surrounding
samples, so a pile inside the basin is SUPPOSED to drop 2.96 m. Score collateral
against the four samples that actually carry the point.

**A real hull floats over it.** A `VikingShip` instantiated over the dredge and left
to Unity physics settled at **y = 30.1068**, bobbing 30.068…30.117 over 20 s,
**13.381 m above the new bed** — indistinguishable from the natural-water control,
which settled at 29.7723, 6.403 m above its own bed.

### 8.2 Why, from the assembly — so the next case can be predicted instead of memorised

`WaterVolume::GetWaterSurface` `[ENGINE]` returns
`transform.position.y + wave + m_surfaceOffset`. **The heightmap appears nowhere in
it.** `Depth()` returns a NORMALISED depth (`Heightmap::GetOceanDepth / 10`, clamped
0..1) and only scales the WAVE. Measured per zone: `transform.y` **30.0000**,
`m_surfaceOffset` **0.0000**, `m_forceDepth` −1, liquid type Water, and a TRIGGER
collider of **64.000 x 60.000 x 64.000 m** spanning **y −20 … +40** — present in
**every** zone, including the one measured at 0.0 % naturally wet, which is why a
cut made in dry ground in that zone still holds water.

`TerrainComp::ApplyToHeightmap` adds `levelDelta + smoothDelta` **per sample with
no spatial falloff**, and `tcdata.Compiler` writes `smoothDelta = 0` — which is why
the ring samples lose exactly 0.0000. The only spreading is the heightmap MESH
interpolating between adjacent 1 m samples.

Two bounds worth carrying: the +/-8 m clamp cannot cut deeper than 8 m below
generated, and harbour A's seabed bottoms out near 22.6, so **the deepest cut this
project can physically make is still ~35 m inside the water volume's own floor** —
a dredge cannot leave the volume. And `Heightmap::GetOceanDepth` is
GENERATOR-derived and **unchanged by a dredge** (1.5685 at the cut both before and
after), so a dredged basin keeps the wave state of its generated depth.

`[INFERRED]`, and tagged because no instrument here can answer it: the visible
water SURFACE MESH is a client-side `MeshRenderer` and **a headless server does not
draw it**, so it was not measured. It reads the same `m_normalizedDepth` shown above
to be unchanged, so it is expected to render over a dredged basin exactly as over
natural water of the same `GetOceanDepth`. **The operator's eyes are the instrument
for that one** — check it on the live build rather than inheriting this paragraph.

### 8.3 What is still forbidden, and why each refusal stands

1. **NEVER RAISE A BED FROM BELOW y = 30.0 TO OR PAST IT under or beside anything
   that stands over water.** This is the measured defect, reproduced on purpose:
   2.792 m of water became 0.900 m of dry ground and `Floating.IsUnderWater` went
   false. The water does not come back and the structure reads as floating forever.
   A levelling **flatten** does exactly this whenever its datum is above the bed,
   which is why every op in `waterfront.py` and `harbour_city.py` declares
   `flatten = "FORBIDDEN"` — the declaration is unchanged, and now it has a true
   reason. `harbour_city.py::dredge_plan` is the one permitted terrain write and it
   is structurally lowering-only: `target = min(applied, reg["bed"])`, and a delta
   that still comes out positive is REPLACED by the delta already in the ledger at
   that sample (`delta, clipped = prior, False`, commented "never fill; keep what is
   there"). **The fill direction is unreachable rather than guarded** — verified
   against the source 2026-09-18 — which is the stronger arrangement, because a
   guard can be deleted and an arithmetic impossibility cannot.
2. **NEVER RUN A BATTER AT A WATER EDGE. Absolute, everywhere** (`roads/ribbon.py`
   returns `water_edge`). A batter beside a shoreline cuts the bank **DOWN FROM
   ABOVE the plane** to the waterline — it removes the ground the water was standing
   against, which is the removing direction, reached from above rather than below.
   It is not "never write below the plane"; it is "never take a bank down to the
   waterline beside standing water". A batter is never load-bearing, so the refusal
   costs a recorded wall and nothing else. The carriageway's water rule is scoped to
   protected structures instead, because a CAUSEWAY has to be possible — and a
   causeway RAISES a bed, which is why that one is scoped and audited rather than
   free.
3. **Dredging being safe does not make it preferable.** A structure that can stand
   on the seabed should stand on it. The harbour A sea wall closes a 132.0 m line
   over a **6.598 m** maximum depth at **zero terrain writes**, and that remains the
   better design — an achievement to keep, not a constraint to relax now that
   cutting is permitted. Cut the bed only to make water DEEPER, and only where a
   hull needs the depth.

### 8.4 Sizing a dredge: the same run measured its limit

**A dredged pocket smaller than the hull floats nothing.** Two `VikingShip`s dropped
into 13 x 13 m pockets holding 6 m of water went **AGROUND ON THE RIM**, settling at
y **32.825** and **33.902** and freezing there: the hull is **9.16 x 21.57 m**, so
bow and stern rested on the uncut ground outside (ring bed **35.806**). Neither sank
and neither was dry — the water was there, the hole was not big enough.

- **Size a dredge to hull + swing + fenders**, never to the hull's box.
- **Verify every berth against the POST-dredge surface**, with `hull_depths` over
  the hull's own rotated ellipse at its ACTUAL yaw — never at the centre of the cut
  and never on the generated surface. This is section 2's lesson re-proved in a new
  setting, which is the reason to state it twice.
- **Give every zone the footprint touches its own compiler, edge samples included in
  both.** Neighbouring zones SHARE their edge sample row, so a dredge that stops on
  a zone line and writes only one side leaves a one-sample ridge.

### Why the wall needs no terrain write at all — the whole verdict

```
fb_fortress_wall   20.009 (x) x 11.866 (y) x 6.800 (z) m, PIVOT AT THE FOOT
deepest sample on the chosen line   h = 23.402
a module seated there tops out at   23.402 + 11.866 = 35.268
                                  = +5.268 m ABOVE the water plane
```

Every module therefore stands on the seabed and still has freeboard. Nothing is
propped and nothing is written into the ground, so no water is taken away from
anything (§8.3).

The kit carries `wnt = 0` — **no `WearNTear`** — so nothing needs a structural
footing and nothing can collapse for want of one. **State the flip side rather
than glossing it: no `WearNTear` also means a floating or buried segment will
never announce itself.** The grounding census is the only instrument that can
catch one, so it is a deliverable, not a nicety.

### Solving the module count — arithmetic, not taste

```python
span  = 132.0                       # the chosen closing line
pitch = 20.009                      # fb_fortress_wall MEASURED length
sol = [(n, span - n * pitch) for n in range(3, 10)
       if 9.5 <= span - n * pitch <= 20.0]
# lower bound 9.5 m: wider than the largest admitted beam plus slack
# upper bound 20.0 m: wider than that is not a gate
# At 132.0 m EXACTLY ONE solution exists: n = 6, gate = 11.946 m
# and it closes the line to the millimetre: 6 * 20.009 + 11.946 = 132.000
```

If no module count divides the line leaving a 9.5-20 m gate, **the line has to
move, and moving it means re-running the closing sweep — not fudging the
pitch.**

### The seated wall, module by module (SEED-DERIVED)

| # | prefab | x | foot y | depth | top y | freeboard | seated on |
|---|---|---|---|---|---|---|---|
| 0 | `fb_fortress_wall_tower` | 1010.50 | 30.435 | -0.435 | 45.837 | +15.837 | dry ground |
| 1 | `fb_fortress_wall_stair_left` | 1030.51 | 26.992 | +3.008 | 38.858 | +8.858 | seabed |
| 2 | `fb_fortress_wall` | 1050.52 | 26.111 | +3.889 | 37.977 | +7.977 | seabed |
| — | **GATE** x 1060.53...1072.47 | | | | | | |
| 3 | `fb_fortress_wall` | 1082.48 | 24.430 | +5.570 | 36.296 | **+6.296 (worst)** | seabed |
| 4 | `fb_fortress_wall_stair_right` | 1102.49 | 25.547 | +4.453 | 37.413 | +7.413 | seabed |
| 5 | `fb_fortress_wall_tower` | 1122.50 | 30.522 | -0.522 | 45.924 | +15.924 | dry ground |

**2 modules on dry ground, 4 on the seabed, ZERO terrain writes, worst
freeboard +6.296 m.** Towers at both ends (they are 15.402 m tall and give
15.8 m of freeboard on the dry abutments); stairs one in from each end, because
a curtain wall with no way onto the walkway is a wall nobody can defend.

Each module's foot is the APPLIED surface at **its own xz**, sampled
individually. Do not seat a run of modules off one height.

### The gate, and what fits through it

```
GATE CLEAR OPENING   11.946 m
water under the gate min 2.704   mean 3.789   max 5.095 m
```

Admitted (beam <= 11.946 AND draught + 1.0 <= 5.095): `CargoShip`,
`MercantShip`, `WarShip`, `VikingShip`, `Karve`, `LittleBoat`, `RowingCanoe`,
`DoubleRowingCanoe`.

**REFUSED: `BigCargoShip` — beam 14.10 m > gate 11.946 m.** The only way to
admit it is a 2-bay gate of about 31.96 m, which is not a gate that keeps
raiders out. The trade was stated to the operator with the number and he ruled
that no `BigCargoShip` is berthed inside the wall.

### The barrier: how a gate is BARRED

The 20 m gateway modules cannot pass a ship (3.429 m and 6.851 m clear,
section 3), so the ship gate is a **GAP between modules, barred by
free-standing panels**.

```
fb_fortress_portcullis   5.704 (w) x 5.857 (h) x 0.216 (thick) m
                         panel_foot_off 0.226
                         Door PRESENT, m_keyItem = null, m_canNotBeClosed = 0
                         no_in_water = 0, water_piece = 0, ground_only = 0
```

The `Door` component is what makes an 11.946 m opening a gate rather than a
wall with extra steps. **This was an OPEN RISK carried explicitly until it was
measured**: if the panel had been a static prop, barring the gate would have
SEALED the operator's own harbour. The suggestive evidence (separately
registered `sfx_open_portcullis_fb` / `sfx_close_portcullis_fb` prefabs) was
declared insufficient, and the decision was: measure it by component dump in
the modded sandbox, and **if static, hang the panels clear of the navigable
width and report the narrowing with the number**, rather than deliver a sealed
harbour. It measured as a real `Door`.

The solved barrier:

```
columns  ncols = ceil(gate / (panel_w - 1.7)) = ceil(11.946 / 4.004) = 3
pitch    step  = gate / ncols = 3.982 m
overlap  panel_w - step = 5.704 - 3.982 = 1.722 m   -> NO residual gap at all
rows     2, stacked at (panel_h - 0.15) = 5.707 m
panels   6 total (3 columns x 2 rows)
foot     min(seabed_y, 30.0 - 3.0)  -- seated on the seabed where the seabed is
         within one panel height of the plane, otherwise at plane - 3.0 m
covers   y 25.46 .. 38.564
         = 4.54 m BELOW the water plane to 8.564 m ABOVE it
```

**The 4.54 m of reach below the plane is the load-bearing number**: it is
deeper than the deepest admitted draught (`CargoShip` 2.0 m), so nothing
slips UNDER the barrier. A barrier that stops at the plane bars nothing — every
hull in the table floats with its keel below it. Note also the deliberate
overlap: `pw - 1.7` in the column count, not `pw`, so adjacent panels overlap
by 1.722 m and there is no seam a hull can thread.

### THE GATE IS A CONSTRAINT ON THE BERTHS, NOT A NOTE

```python
# settlements/harbour_city.py::cmd_design
admitted = set(a["sea_wall"]["gate"]["hull_classes_admitted"])
for row in a["berths"]:
    for v, h in row["hulls"].items():
        h["admitted_by_gate"] = v in admitted
        h["berthable_here"]   = bool(h["floats"] and v in admitted)
```

**A hull that floats at a berth but cannot pass the gate is not berthable
there.** On harbour A, `BigCargoShip` FLOATS at four berths and is BERTHABLE at
none of them — the accepted trade made mechanical instead of documentary. Every
berth row carries both fields.

Generalise it: `berthable_here = floats AND admitted by every gate on the route
from open sea to this berth`. A gate, a bridge with a deck soffit, a narrowing
strait — each is a constraint that must be applied to the berth table, not
recorded beside it.

### The landward curtain, and refusing rather than propping

`port_curtain()` lays a curtain tangentially on the LANDWARD arc behind a quay
at the kit's own 20.009 m pitch, with a GATEWAY on the bearing the road arrives
from and a TOWER at each end of the arc. **Every segment is refused unless its
own centre stands on land** (`g > LAND_LEVEL 30.5`), and the reason is freeboard,
not drainage: a 20 m module whose foot is well under the plane runs out of height
before it clears the water, and the only ways to seat it are propping it or writing
terrain to meet it. **Both are refused; the footing being under the plane is not
itself the defect** (§8.1-8.3), and the write that WOULD be the defect is the one
that raises the bed up to the footing.

At `harbour-vestvik` (R = 26 m, arc 200 deg) **4 of 5 segments stand on land
and the 5th was DROPPED at -3.727 m freeboard rather than propped.** Record the
drop; do not prop.

### `mm_` vs `fb_`: a package instrument is not a runtime instrument

MEASURED twice over, live (ledger seq 2102-2123, `WaterfrontRun`,
2026-09-17T11:04-11:07Z):

- `spawn_object mm_large_wall` -> *"Object reference not set to an instance of
  an object"* — a NullReferenceException from a null `ZNetScene` lookup — and
  **placed nothing**. `objects_count` -> *"Error: Entity id mm_large_wall not
  recognized"*. The `mm_` family never reaches `ZNetScene` because
  `RavenwoodPiecesPlugin::AddRWPieces` has a one-byte `ret` body.
- In the same probe `fb_bridge_01` and `fb_bridge_02` **both placed AND read
  back at count 1**. The `fb_` family registers through Jotunn
  `PieceManager::AddPiece`.

**53 `fb_` strings exist in the installed DLL and only 29 are registered
pieces.** A package/IL instrument is not a runtime instrument. **Re-probe the
exact subset you intend to place, LIVE, before anything load-bearing is
built — and re-take it after any restart**, because a registration measured
under Jotunn 2.30.0 is not evidence about 2.30.1.

---

## 9. Piers, docks and boathouses

### `DECK_DATUM_Y` and why a road terminates ON it

```python
FLOOR_TOP_OFFSET = 0.097          # wood_floor top face above its own origin
DECK_DATUM_Y     = A.DECK_Y       # 30.6
# a deck authored at y = 30.5031 has a walking surface at 30.597 == the datum
```

**The defect, measured 2026-09-17 at `harbour-temple-south`:**

```
deck datum                             30.6   (39 wood_floor all at y = 30.5)
T13-temple-harboursouth carriageway    31.195 .. 32.230
closest written road sample to a deck tile, 1.09 m away, applied y = 31.248
                                    -> STEP +0.648 m
samples (11,-256) and (12,-256) both at 32.230 (deltas +1.670 / +1.336,
authored by ledger seqs 338 and 1579)
                                    -> +1.633 m THREE METRES INLAND
cause: T13's own observe seq 336 records endpoint_standoff harbour-south 0.7 m
       and a ford f01 at (12,-258)/(13.48,-259.48) BUILT AT ROAD FREEBOARD,
       not at the deck datum
```

`DECK_DATUM_Y` exists precisely so that no join has a step in it. **The datum
existed and the terminus never read it.** Contrast the ferry terminal, where it
was read: the landward deck tile at (1746.84, 656.44) stands where the
generated ground is 30.559 — a **0.041 m step**, already walkable.

**Rule: a road that arrives at an over-water structure terminates on
`DECK_DATUM_Y`, not on terrain and not at road freeboard. It is the same number
at every such structure on the world, so this is checkable.**

Two further faults found in the same census, worth naming because they are
cheap to check and invisible otherwise:

- **Two of 39 deck tiles buried in the bank:** (12.91, -259.91) ground 31.321 =
  **+0.721 m above the deck top**; (11.50, -261.33) **+0.147 m**. A deck tile
  under the ground is not a deck tile.
- **13 rail posts with no rail**, and 39 floor tiles unroofed.

### Fixing a step with a STRUCTURE, not a cut

`stair_run()` stairs the bank between a deck top and the road above it. **The
bottom-up version was WRONG and the corpse is kept in the docstring:**

```
wood_stair  solid AABB 2.000 (x) x 2.029 (z) x 1.121 (y), 26 deg pitch
            TREADS DESCEND WITH +z: top tread box y[0.86,1.05] at z[-1.02,-0.93]
                                    bottom tread box y[-0.07,0.10] at z[0.75,1.00]
            so the piece rises towards -z; a run climbing towards bearing B is
            placed at yaw = B + 180
```

Built from the deck upwards at 1.05 m rise per 2.029 m run, the treads march
out over ground that MEASURES 32.230 at (11, -256), only 1.6 m from a deck top
of 30.597. The road's levelled platform is effectively a **1.63 m retaining
face AT the deck edge**: no 26 deg run reaches it inside the space available,
and the stairs it placed would have been **buried 1.7 m in the bank**. Seating
a structure on an ASSUMED slope is the same defect as walking a planned profile.

So the run is anchored at the TOP — found by measurement, walking up-slope at
0.25 m until the applied surface stops climbing — and descends to the deck. Two
numbers are reported and never hidden:

- `residual_m` — deck top minus the lowest tread. Positive is a kerb, walkable
  either way. **Negative means the tread is above the deck and the step
  survives, which the caller must refuse.**
- `max_burial_m` — the worst amount by which applied ground stands ABOVE a
  tread's walking surface. Stairs cut into a bank are expected to be partly
  embedded; **a tread buried deeper than its own 0.26 m riser is not a stair,
  it is landfill.**

### What a pier is made of, and why it is not a pad

```python
# crossings/assemble.py::jetty  -- 2 m stations, deck at deck_y - 0.097
#   wood_floor        deck tiles, width_tiles across
#   wood_pole_log_4   pile stacks at bent_spacing_m = 4.0 m
#   wood_pole         single-sided rail post, OUTBOARD side only, so a boat can
#                     be boarded from the deck
```

"This is the structure the operator called a dock. It is deliberately NOT a
flattened pad with a building on it: the pad is the `early-dock` defect, where
the flatten removed the water from under the footprint and the pier then read
as floating. Every station here stands on piles driven into the bed and there is
no terrain write anywhere in its footprint."

**That record is the origin of this manual's water rule and its direction is the
whole point** (§8.1): a `flatten` LEVELS ground to a datum, and at a deck datum of
30.6 over a submerged bed that means RAISING the bed from below y = 30.0 to above
it. The water it removed was removed by a RAISE. The same operation reproduced
under instrument in 2026-09-18's run took a bed from 27.208 to 30.900 and turned
2.792 m of water into 0.900 m of dry ground. What is forbidden here is the pad's
direction, not the pad's depth.

The pile stack, and why `embed` exists:

```python
# crossings/assemble.py::_pile_stack
# wood_pole_log_4 measured height = 4.0 m
top_target    = deck_y - 0.13          # just under a wood_floor's underside
bottom_target = ground_y - embed       # embed = 1.0 m, one full 1 m sample
n = ceil((top_target - bottom_target) / 4.0)
```

MEASURED from `WearNTear::UpdateSupport`: a piece whose collider overlaps the
terrain layer is pinned to `maxSupport`. **A pile that stops at the bed is a
pile with no foundation; a pile that is buried is a foundation.** 1.0 m of
overlap is one full sample at 1 m field resolution.

Practical pile ceiling: **~20 m of reach** for HardWood log piles
(material 3, 140/10 at 0.166667 horizontal / 0.1 vertical). Past that a pier
cannot reach the bed, which is one half of the ferry decision in section 10.

### What a real boathouse is, versus decking

**MEASURED on this world: every structure called a "boathouse" was decking.**
`crossings/assemble.py::slip` emits exactly three prefabs —
`wood_floor` (deck and head walkway), `wood_pole_log_4` (piles) and `wood_pole`
(rail posts) — **and no roof, no walls and no bays at all.** The live census at
`boathouse-temple-strait` (three ledger records, 97 pieces) is precisely that
set, plus a missing sign and a missing boat. A slip is two finger piers and a
closed head; it is open on three planes.

What `slip()` DOES get right, and it is the reason to keep it: three solid sides
with real colliders, sized off the measured hulls rather than chosen —
`VikingShip` colliders span `x[-4.58, 4.58]` and `z[-10.24, 11.32]`, i.e.
9.16 x 21.56 m, so an **11 m inner width and a 24 m length** berth a Longship
with a margin each side, and anything narrower is a slip a Longship cannot
enter. That is the correct way to size a slip and it is still not a boathouse.

**A real boathouse** — built at harbour A/B, 3 buildings, 2 bays each, 6 bays
total, solved rather than hand-picked:

```
bay clear opening        11.5 m clear x 26.0 m
bays                     walls on THREE sides + gabled roof
eaves                    34.6      (= plane + 4.6)
ridge                    39.124    (= plane + 9.124)
SEAWARD GABLE            DELIBERATELY OPEN -- it is the BAY MOUTH
shallowest sample per bay  3.542 / 4.449 / 4.045 / 3.898 / 4.116 / 4.100 m
                           all six fit up to CargoShip (needs 3.0)
```

Note the first hand-picked pass: **three bays that fitted NOTHING, one with a
shallowest sample of -0.755 m — dry land under a boat bay.** Solve bay
positions with `hull_depths` exactly as you solve berths.

**A census counting four walls per bay would be counting a defect.** This was
pre-empted deliberately and written into the record so that a later audit does
not "fix" the open gable and wall a boat in. The general rule: **when a
deliberate absence is load-bearing, record it as an invariant with its reason,
or the next instrument will repair it.**

Sizing check for 11.5 x 26.0 m against the table: clears `BigCargoShip`'s
14.10 m beam? **No** — 11.5 m clear admits every hull EXCEPT `BigCargoShip`,
which is the same constraint the 11.946 m gate imposes, and the two agree. It
clears `VikingShip`/`MercantShip` at 9.16 x 21.57 with 2.34 m of beam margin and
4.43 m of length margin.

---

## 10. Ferries need TWO terminals

> The operator, verbatim: *"i investigated a ferry and i couldnt find a
> corresponding dock on the other side of the water. ferries are supposed to
> have two points that the ferries sail between. a ferry isnt just a boat on a
> dock."*

### How a one-ended ferry happens

`ferry-terminal-eastisle` stood from ledger seq 43 (2026-09-16T03:02-03:04Z):
22 `wood_floor`, 11 `wood_pole`, 15 `wood_pole_log_4`, a sign post, a sign and
portal `x-ferry-e`, all verified live at tolerance 0 — **with a sign naming a
destination that did not exist.** `ferry-terminal-east` was planned and
structurally verified by `Crossings` (ledger seq 39, zones
`[[27,10],[28,10]]`) and **never built**: measured live at (1745.2, 655.6) over
a 40 m radius, `wood_floor` 0 / `wood_pole` 0 / `wood_pole_log_4` 0.

And it blocked a road: segment `S14-eastlobe-ferryeast` was validated but
deliberately not built, for the stated reason that it *"terminates at
ferry-terminal-east, which has zero pieces in the world"*. **A missing far
terminal is a missing road as well as a missing dock.**

**The structural fix: the route is ONE object.** Both terminals live in the
same `FERRY_ROUTES` entry; `ferry_pair()` measures both ends in one pass;
`cmd_complete` refuses if either end cannot float the vessel its own sign will
promise. A ferry terminal cannot be finished singly again.

### Both ends share the deck datum

```
ferry-terminal-east      deck_top_y 30.597   root (1746.84, 656.44)  head (1764.95, 647.72)  bearing 155
ferry-terminal-eastisle  deck_top_y 30.597   root (1951.08, 555.59)  head (1933.80, 565.86)  bearing 335
deck_datum_shared = TRUE
```

**Identical at both ends, so a road arriving at either has no step.** This is an
invariant worth asserting (`ends[a]["deck_top_y"] == ends[b]["deck_top_y"]`),
not a coincidence to observe.

Note also how the seaward bearing was derived. `crossings/plan.py`'s `bearing`
field is the ASSEMBLY AXIS, and at both terminals the deck marches out along its
NEGATIVE direction. Taking it at face value put the "head" on dry land at
30.559 and the "root" in 9.4 m of water — **exactly backwards**. The seaward
bearing is derived here from WHICH END OF THE DECK LINE IS WET. Never inherit a
bearing from a field whose meaning you have not measured.

### Three DISTINCT distances that were being used interchangeably

| Name | Value | What it is | Belongs on |
|---|---|---|---|
| `shore_gap_m` | **223.65 m** | nearest land to nearest land, at (1752, 662) and (1954, 566) | the bridge-vs-ferry decision |
| `head_to_head_m` | **187.65 m** | pier head to pier head | what a bridge would have had to span |
| `sail_m` | **196.82 m** | berth to berth | the GUIDEPOST — it is what a passenger travels |

The existing signs read *"223 m to the East Isle. Longship moored here."* The
223 m was the SHORE gap and was **accurate** (the ledger claimed 223.2 m;
measured 223.65 m; agreement 0.45 m) — it was simply **not the sail**. Two of
the other three claims failed measurement: the vessel is chosen by the berth,
not by the record, and "moored" names a mechanic Valheim does not have.

Replacement text, read back off the live ZDOs after placement
(2026-09-18T03:52Z):

```
FERRY EAST - to the East Isle terminal, 197 m across open water. CargoShip berthed here.
FERRY WEST - to the mainland terminal, 197 m across open water. CargoShip berthed here.
```

**Both guideposts name the other end.** A guidepost at one end only is how a
one-ended ferry survives inspection. The sail line itself was measured:
196.82 m, wet fraction 1.0, ONE continuous wet run, shallowest sample 5.16 m.

### The decision rule: ferry or bridge

```
shore gap                        223.65 m
absolute iron-stringer free span  96.0 m   (MEASURED ceiling)
ratio                            2.33x    -> SPAN CEILING EXCEEDED
water depth on the line          reaches the 30.0 m ocean-floor clamp
HardWood log pile reach           ~20 m    (material 3, 140/10 @ 0.1 vertical)
                                          -> PILE CEILING EXCEEDED
BOTH ceilings exceeded  ->  it is a ferry, and not a bridge anyone declined
                            to build
```

Both halves matter. A 223 m gap over 8 m of water is a piled causeway. A 60 m
gap over 30 m of water is neither a free span nor a piled crossing. **Test span
against the free-span ceiling AND depth against pile reach, and say which one
refused.** Recording "it is a ferry" without the two numbers is how a later
session re-litigates the bridge.

Note the span ceiling is a **`WearNTear` property**, so kits with `wnt = 0`
(like `fb_bridge_01`/`_02`, measured 18.6 m single-prefab modules) are not
bound by it. If your kit has no `WearNTear`, re-derive the ceiling; do not
inherit 96 m.

### Why retire-and-replace, not nudge

**There is no verb on this surface that moves a ZDO**, and a second boat spawned
beside the first is two boats. So a wrong berth is repaired as a RETIRE plus a
PLACE, inside one token hold:

- Each retire is **scoped to ONE prefab inside a cube sized to that object**
  (`cube_half_m = max(hull_l, hull_w)/2 + 1.0` = 11.79 m for a `VikingShip`),
  with the postcondition that the prefab is **ABSENT** there.
- A retire **must name what it supersedes.** `placed_by()` scans the live
  ledger for the record(s) that placed the object within 6 m and refuses if
  there are none: "refusing to delete an object this log did not write."
  Superseded records carry their **FILE LINE as well as their `seq`**, because
  ZDO ids are reassigned on world load and `seq` is not unique on this forked
  log.
- **The grounding measurement gets its OWN ledger record, BEFORE the retire**,
  because after the retire the evidence is gone from the world and the only
  place it can still be read is the log. The operator had been told there were
  berthed ships at that site; one was on the bottom, and **the number that
  proves it must outlive the hull.**
- A sign is likewise **replaced rather than edited** — no verb on this surface
  writes a ZDO string in place.
- And a safety interlock in the other direction: `cmd_ferry` raises rather than
  retiring a vessel that MEASURES AFLOAT. "Refusing to retire a vessel that is
  not the defect; if it should be a different class, that is a separate
  decision and it needs its own reason."

---

## 11. Lighthouses

### Seat the beacon on the FIGHTING PLATFORM collider, not the mesh top

```
fb_fortress_wall_tower
  mesh AABB                    20.009 x 15.402 x 9.639 m
  WALKABLE FIGHTING PLATFORM   y = 10.229   (three boxes: 2.00 x 0.55 and
                               0.52 x 2.00, the parapet ring)
  small cap                    y = 13.734 .. 14.269 (2 x 2)
  two 0.4 x 0.4 posts between
```

**15.402 - 10.229 = 5.173 m.** Three beacons were placed **5.173 m in the air**
because the mesh AABB was read as the tower's height. A lamp seated on the
platform takes `ground + 10.229`; a lamp seated on the cap takes
`ground + 14.269`. The error was caught and corrected by the lamppost work,
which owned the datum class.

The lamp's own pivot runs the OPPOSITE way and compounds the error if taken
from the same convention:

```
piece_groundtorch
  pivot is 0.65359 m ABOVE its own base   (MEASURED y[-0.6536, +0.8226])
  solid collider 0.233 x 0.237 m  -- fits inside one 2 x 2 m deck tile
  Piece.m_notOnWood = 0  -- placing it on a wood deck breaks no build rule
  infinite fuel: ints {HasFields:1, HasFieldsFireplace:1,
                       Fireplace.m_infiniteFuel:1}
y = surface_y + 0.65359
```

`fb_` kit: **pivot at the FOOT**. `piece_groundtorch`: **pivot 0.654 m ABOVE
the base**. Getting the sense wrong on a tower is the difference between a lamp
on the parapet and a lamp hovering over it.

Worked seat, harbour A lighthouse: `fb_fortress_wall_tower` solved onto the
driest headland seat (1006, 768), `h = 31.786`, freeboard **+1.786 m**; beacon
on the FIGHTING PLATFORM at **y = 42.015**, NOT the **47.188** mesh top.

For a lighthouse assembled from a blueprint body rather than the fortress kit,
the height report names the four numbers that matter separately and none of
them is the mesh bound: `roof_cap_top_y`, `height_above_sea_m` (against
c_WaterLevel 30.0), `height_above_entry_floor_m`, and
`gallery_platform_top_y` / `gallery_above_sea_m`. The beacon's
`structural_base_y` is checked against its plinth's `plinth_top_y` — "the
beacon sits on its plinth, not in the air."

### The northern-tip trap

The operator asked for *"a tall lighthouse at the very northernmost point next
to the dock"*, with a large OdinShip vessel berthed.

```
ABSOLUTE northernmost land of the two bridged halves: comp278's tip at (-292, 2720)
  a ONE-CELL SPIT
  nearest sea-connected water at or over 2.5 m:  112 m away
  shelter:                                        0.417  (exposed)
  -> unreachable by any pier.  UNUSABLE.

The viable seat, 138 m south:  w-north (-186, 2582)
  shelter 0.750, sea at 53.7 m
  aimer accepts a 48 m pier from root (-192, 2612) at bearing 60
  wet fraction 0.816, head depth 3.25 m
  -> clears CargoShip's 2.0 m draught by 1.25 m -- floats, but only just
  20 / 24 / 28 / 32 / 40 m ALL REFUSED -- this pier cannot be shortened
  PORTAL-ONLY: ~2.8 km from the built west-isle road network
```

**The rule this teaches: "northernmost", "highest" and "most dramatic" are map
properties, not port properties.** A point can be the extremity of a continent
and still be 112 m from water a boat floats in, which makes it unusable for a
port however good it looks on a map. Measure `distance to nearest
sea-connected water at the required depth` as a first-class siting number and
refuse on it.

Second consequence, recorded because it is an access decision and not a
geometry one: at 2.8 km from any road, **the portal IS the access — a one-ended
portal tag strands the operator at the top of the map.** Pair the tag at both
ends or the site is unreachable. Related near-miss at the same site: an arch was
moved onto the deck because its first seat stood **0.675 m UNDER c_WaterLevel
30.0** — an arch standing in the surf, on the only access to a site 2.8 km from
the nearest built road.

---

## 12. Procedure: siting and building a harbour on a fresh world

Ordered. Each step names its instrument and its refusal condition. Do not
reorder: several steps exist only to gate the one after them.

### Phase A — measure (read-only, no world writes)

1. **Fix the datums.** `WATER_LEVEL = 30.0`, `LAND_LEVEL = 30.5`,
   `DECK_DATUM_Y = 30.6`. Never test wetness with `h > 0`.
2. **Raster the candidate area (I1).** `blueprints/run_patchscan.sh`, seed
   string as the generator consumes it, rivers included, **lattice-aligned**:
   `half = k + 0.5` so `cx - half + step/2` is an integer. Step 1 m for any
   berth or bay question; a coarse overview patch may not answer a 1 m
   question. *Refuse* an unaligned patch — do not interpolate around it.
3. **Scan a SECOND independent field over the same control points** and record
   the worst disagreement. That number is your noise floor and your margin
   floor must beat it. Measured here: 0.0275 m -> `BERTH_MARGIN_FLOOR_M = 0.25`.
4. **Dump prefab geometry in a modded sandbox mirror (I4)**: solid non-trigger
   collider AABBs, pivots, `Piece`/`WearNTear`/placement flags, and
   `Ship.m_waterLevelOffset` for every vessel. **Exclude `mesh` and
   `mesh_render`.** Calibrate the hull filter against a known recorded hull
   before trusting it (target: reproduce a known 9.16 x 21.56 m to 0.01 m).
5. **Locate the water body and its mouth.** Label the water mask; find the
   deepest connected cell; **the mouth faces the deepest connected cell**, do
   not assume a compass direction. Record the body's total area and whether it
   is 4-connected to the patch boundary. *Refuse* an inland lake as a port
   however large — 35.5 ha of landlocked water sails nothing.
6. **Cross-section the inlet.** Walk OUTWARD from the measured centreline at
   0.5 m to the first dry sample each side; do not read a straight line. Record
   span, wet width, max depth and mean wet depth per section.
7. **Profile the depth as percentiles against y = 30.0** (p10/p25/p50/p75/p90/
   max), and compute **navigable area at each candidate draught** via
   `sea_mask`. Record the **navigable bottleneck** (2x max-min
   clearance-to-shore over all water paths basin->sea).
8. **Ring the biome and relief** about the basin centre, land only, at
   0-60 / 60-120 / 120-200 / 200-300 m.
9. **Census pads against the +/-8 m clamp** at 16 / 24 / 32 / 48 m against each
   pad's own median floor. *Refuse* a single large platform if the large pad
   fails; terrace instead. A plaza that fails the clamp fails MID-WRITE.
10. **Check what already exists (I2, I3).** Every applied terrain sample on a
    2 m lattice over the area; nearest carriageway and nearest node with its
    pad radius; our own builds within 400 m. Record distances — they decide
    whether a road is a new route or a junction, and whether there is corridor
    conflict.
11. **Probe live `LocationProxy` markers (I5)**, bounded
    (`findObjects -prefab LocationProxy -near <x> 30 <z> <r>`) so the log sink
    is not tripped, cross-read against a locscan dump for identity, with any
    **unidentified marker getting the worst-case stand-off**. This is
    SAVE-DERIVED: take it as the first live action after any restart, and let
    it **gate the first write**. A generator `plan.json` with `placed: false`
    on every entry is a REQUEST list, not an authority.

### Phase B — solve (still read-only)

12. **Solve berths, do not pick them.** Sweep a grid at 1-2 m over the basin;
    for each candidate run `hull_depths` for the wanted vessel; reject on
    `no_footprint`, `not_connected`, `too_shallow`, and count each refusal
    class. Rank by depth, then enforce a minimum separation (`apart_m`
    26 m at a large harbour, 24 m at a village). *Refuse* the site if nothing
    clears the margin floor.
13. **Sweep pier LENGTH against the aimer**, not the other way round. Take the
    shortest accepted length and record every refused length. *Refuse* the
    structure if no length reaches floatable water — "a slip that cannot float
    a boat is scenery" — and do not lengthen past what the measurement supports
    on your own authority.
14. **Solve boathouse bay positions the same way** as berths, with
    `hull_depths` over the bay footprint. Hand-picked bays produced dry land
    under a boat bay.
15. **If the mouth is to be closed: run the closure test of section 7 over ALL
    candidate lines.** Take the shortest line that verifiably disconnects the
    basin from open sea. *Refuse* the shortest cross-section. Extend the chosen
    line ~10 m onto dry ground at each end.
16. **Solve the module count arithmetically** against the kit's measured pitch,
    with a gate between (largest admitted beam + slack) and 20 m. *If no count
    fits, move the LINE and re-run the closure sweep — never fudge the pitch.*
17. **Seat every module on the applied surface at its OWN xz.** Verify
    `foot + module_height - 30.0 > 0` for every one. *Refuse* the design if any
    module needs terrain RAISED to meet it, or drop that module and record the drop
    with its negative freeboard. Never prop, and never level a submerged bed up to
    a footing — that is the one write that cannot be undone (§8.3). A DREDGE, which
    lowers a bed, is a different operation and is permitted where a hull needs the
    depth (§8.4).
18. **Design the barrier to reach BELOW the deepest admitted draught.**
    Columns = `ceil(gate / (panel_w - overlap))`, two rows, feet at
    `min(seabed, plane - 3.0)`. Verify the covered y-range spans from below the
    deepest draught to well above the plane. **Verify the panel carries a real
    `Door`** by component dump; if it does not, hang the panels clear of the
    navigable width and report the narrowing with its number rather than
    sealing the harbour.
19. **Apply every gate as a CONSTRAINT ON THE BERTH TABLE.** Add
    `admitted_by_gate` and `berthable_here` to every (berth, vessel) row.
20. **Pick each vessel by the berth**: largest by draught that proves it floats
    by the margin floor AND fits the beam cap less 1 m of fender AND fits the
    length. Return `None` and refuse rather than narrowing silently.
21. **If the water cannot be bridged, design a FERRY AS A PAIR.** Both
    terminals in one route object; identical `deck_top_y`; derive each seaward
    bearing from which end of the deck line is WET; compute all three distances
    separately and put `sail_m` on the guideposts; record the span-ceiling and
    pile-reach numbers that refused a bridge.
22. **Seat beacons on collider platforms**, checking the pivot sense of each
    prefab family separately (`fb_` foot-pivot vs `piece_groundtorch`
    +0.65359 m).
23. **Probe the exact prefab subset you intend to place, LIVE, and re-probe
    after any restart or mod bump.** A package/IL instrument is not a runtime
    instrument. `spawn_object` + scoped `objects_count` read-back at count 1 is
    the evidence; a null-reference answer means the prefab is not in
    `ZNetScene` and placed nothing.

### Phase C — build

24. **Route the road LAST and terminate it on `DECK_DATUM_Y`**, not on terrain
    and not at road freeboard. Feed the union of the ACTUAL pads into the
    router's keep-out set BEFORE routing — not one big disc (a 100 m
    `pad_radius_m` read as a footprint made a road refuse to write for 294 m).
    Order matters: on a previous site the road went in at ledger seq 213 and the
    pads at seq 286, and 102 pieces ended up in a traffic lane.
25. **Declare `flatten = "FORBIDDEN"` on every waterfront op.** A flatten LEVELS to
    a datum, so beside or under anything over water it RAISES the bed and takes the
    water away (§8.3). The one permitted terrain write near water is a
    lowering-only dredge through `harbour_city.py::dredge_plan`
    (`target = min(applied, design_bed)`), sized to hull + swing + fenders and
    verified against the POST-dredge surface (§8.4). Prefer zero writes: harbour A's
    132 m sea wall closes a 6.598 m-deep line with none.
26. **Batch the placement.** Piece batches sized by the console sink, not by
    taste: at ~155 echoed bytes per placed object, a 48-piece record costs
    ~7.4 KB of a 65,536-byte pipe, while a single 1,255-command record cost
    ~194 KB with no drain window inside it and **wedged the sink**. Probe the
    main thread and drain between records.
27. **Make placement IDEMPOTENT.** Census the site and place only absent
    pieces, matched PER PIECE. A replay against an empty site places
    everything; against a finished site, nothing; an interrupted run resumes.
    A `spawn_plan` re-emit doubles every piece and then fails its own
    postcondition.
28. **Never edit a ledger record.** Annotate with a later record.

### Phase D — verify

29. **Grounding census, per piece.** With `wnt = 0` kits nothing announces a
    floating or buried segment, so this census IS the instrument. Report the
    sunk/overhang split, not one "grounded fraction".
30. **Piece counts per prefab inside a radius where the count can be EXACT**,
    at tolerance 0, plus strays. Remember `objects_count` measures an XZ
    cylinder and is **y-blind** — it will count a piece 15 m below you.
31. **Re-measure every berthed hull at its own live ZDO position AND its own
    live ZDO rotation.** Not at the pier bearing: the two differed by 0.709 m
    on the one hull that was aground.
32. **Read every sign and portal tag back off the live ZDO**, both ends of
    every pair. Compare as TEXT, once — `"\n".join()` on a string joins its
    CHARACTERS and happened to give the right answer once.
33. **Confirm saves LANDED, not just that they were acknowledged** — look for
    the container's own `World save (N/N) done` line.
34. **Classify every number you recorded as SEED- / SAVE- / MOD-DERIVED**, and
    say explicitly what a world regeneration invalidates. Everything geometric
    re-derives; every piece census, marker stand-off and road junction does
    not.

---

## 13. Refusals, for reuse

The most valuable rows in this manual. Each cost real time and each is a
decision a fresh session should not re-litigate.

| Refused | Measurement | Why it matters |
|---|---|---|
| `dock-stationhub` (planned 20 m jetty) | best of 9 vessels, 1,681 positions within 60 m: `DoubleRowingCanoe` 0.540 m vs 1.8 m needed; floatable water starts 45-60 m from the head | structurally buildable, berths nothing, would have been pure scenery |
| `VikingShip` at `boathouse-temple-strait` | -0.888 m under its own footprint vs 2.5 m required; `CargoShip` +0.038 m | the slip was sized off the 11 m HULL WIDTH and nobody measured the WATER |
| Wall lines z = 728 / 730 / 732 (84.0 / 85.0 / 88.0 m) | do not close the basin — the "dry" sample west of centre is a SPIT with water on both sides | three cheaper walls that would have left the sea a way round |
| B's closing line z = 440, 113.0 m | closes, encloses only 4,326 m2 | a poor trade; B got quays instead, and the refusal was recorded with the number |
| `BigCargoShip` inside harbour A | beam 14.10 m > gate 11.946 m; admitting it needs a ~31.96 m 2-bay gate | not a gate that keeps raiders out; floats at 4 berths, berthable at 0 |
| 24 m pier at `dock-dockshore` | refused at all 72 bearings and all head depths 2.2-3.0 m; 40 m is the shortest accepted | short piers cannot reach water a boat floats in |
| 20/24/28/32/40 m piers at `dock-northcape` | all refused; only 48 m accepted | the pier cannot be shortened |
| The absolute northern tip (-292, 2720) | one-cell spit, 112 m from sea-connected water >= 2.5 m, shelter 0.417 | "northernmost" is a map property, not a port property |
| Stenvik/Stathub creek as a large port | best shelter on the world (0.792), 5.03 m at the head, but a 15.71 m hull straddles the banks at 0.558 m | high shelter is not sufficient; width is a separate gate |
| The 35.5 ha body at (1086, 1110) as a port | not 4-connected to the patch boundary — an INLAND LAKE | nothing can sail out of it |
| 5th `port_curtain` segment at `harbour-vestvik` | -3.727 m freeboard | dropped and recorded rather than propped or dug in |
| `mm_large_wall` and the whole `mm_` family | NullReferenceException on `spawn_object`, "Entity id not recognized" on `objects_count`; `AddRWPieces` has a one-byte `ret` body | placed NOTHING while appearing to run |
| `mesh_render` as a hull or an opening | 11.64 x 35.43 m for a 9.16 x 21.57 m hull; 20 x 11.87 m for a 3.429 m opening | 60% oversizing, and a "ship fits" verdict on a gate no ship fits |
| Bottom-up stair run from the deck | treads march into ground at 32.230 only 1.6 m from a 30.597 deck; stairs buried 1.7 m | an assumed slope is the same defect as a planned profile |
| A 13 x 13 m dredged pocket as a berth | two `VikingShip`s aground on the rim at y 32.825 and 33.902 in 6 m of water; hull is 9.16 x 21.57 m, ring bed 35.806 | a pocket smaller than the hull floats nothing — size to hull + swing + fenders and verify on the POST-dredge surface |
| "Never write terrain below the water plane" as an absolute | one run, both directions: LOWER 7 m at (1088,720) gives 6.274 -> 13.274 m of water and a hull settling 13.381 m above the new bed; RAISE at (1064,686) gives 2.792 m of water -> 0.900 m of DRY ground; ring cells at 1/2/4/8 m lose EXACTLY 0.0000 | the rule was generalised from the RAISE direction to both; the refusals it produced were right, the mechanism it named was false (§8.1) |
| A bridge across the east-isle gap | 223.65 m = 2.33x the 96 m free-span ceiling, over water at the 30.0 m clamp vs ~20 m pile reach | both ceilings exceeded — a ferry, not a declined bridge |
| The straight A->B "road" line | 100.7 m of it below the water plane; 47.5% max gradient over an 8 m baseline vs an 8% built standard and a 25% routing ceiling | a straight line between two water points is not a road |

## 14. One-line invariants

- Depth is `30.0 - surface`. Never `h > 0`.
- The shallowest sample under the hull's own rotated ellipse is the only depth
  that means anything.
- Every hull sample must be in water connected to open sea at THAT hull's
  required depth.
- Solid box colliders for hulls, openings and walkable surfaces. Never the mesh
  bound.
- Draught gates depth; beam gates passage. Two numbers, two gates.
- Solve positions; never hand-pick them.
- Never RAISE a bed from below y = 30.0 to or past it under or beside anything over
  water; a `flatten` at a deck datum does exactly that. Lowering a bed adds water
  and is a dredge.
- A batter at a water edge is refused absolutely: it takes a bank DOWN TO the
  waterline from above, which is the removing direction.
- Zero terrain writes is still the better design, not a limitation.
- A road arriving at an over-water structure lands on `DECK_DATUM_Y` = 30.6.
- A gate is a constraint on the berth table, not a note beside it.
- A ferry is a pair or it is not a ferry.
- A beacon sits on a collider platform, never on a mesh top.
- Record refusals with their numbers; a silent omission is not a finding.
- When a deliberate absence is load-bearing — an open seaward gable — write it
  down as an invariant, or the next audit will "fix" it.
- Measure the evidence BEFORE you destroy it; the number must outlive the
  object.
