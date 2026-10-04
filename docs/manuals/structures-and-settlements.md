# Structures and settlements: placing buildings that are grounded, reachable, and not in the road

A manual for an agent with no memory of the session that produced it. Every rule below
carries the measurement that produced it and the defect it prevents. Where a technique was
tried and failed, it is recorded as a refusal with its numbers, because several of those
failures cost hours and are cheaper to read than to rediscover.

Written 2026-09-18 against Ulfsland (seed `Pirate68`), Valheim `l-1.0.12`
(`assembly_valheim.dll` md5 `89ffdb64fefebc011f5a9a826f2968bf`), Jotunn 2.30.0 installed.

---

## 0. How to read this: three classes of fact

This distinction is the spine of the whole document. Get it wrong and you will carry a
coordinate into a regenerated world and build in the sea.

| Class | Survives a world regeneration? | Survives a save wipe? | Instrument |
| --- | --- | --- | --- |
| **SEED-DERIVED** | **yes** — it is a function of the seed string | yes | `WorldGenerator.GetHeight` / `GetBiome` via `blueprints/PatchScan.cs`; `ZoneSystem` location dump via `tools/seedscan/run_locscan.sh` |
| **SAVE-DERIVED** | **no** | **no** | live RCON (`findObjects`, `objects_count`), the ledger's own `terrain_write` blobs via `roads/applied.py` |
| **BUILD-DERIVED** | yes | yes — but dies on a game patch or a mod change | `blueprints/PieceGeometry.cs` collider dump, IL of `assembly_valheim.dll` and plugin DLLs, live `ZNetScene.m_prefabs` |

SEED-DERIVED: terrain height and biome at any XZ, the water plane `y = 30.0`, the location
REQUEST list and its per-type radii, where a natural harbour is, whether a wall line closes
a basin, how much cut a pad needs.

SAVE-DERIVED: every applied terrain delta, every pad, every placed piece, every portal tag
and every portal connection, the whole ledger. **None of it survives a re-roll.** A
settlement is therefore recorded as a *requirement* and re-solved, never as a coordinate
(see §10).

BUILD-DERIVED: every collider box, every pivot offset, every component flag
(`Door`, `WearNTear`, `m_keyItem`), whether a prefab is in `ZNetScene` at all.

**Never write a coordinate down as an answer.** MEASURED, the worst instance in the
session: a spawn coordinate `(-4673, 73, -350)` was derived from a *plan* to place a base
there, the placement then failed on the `--align floor-center` bug (§9.2), the build never
landed, and the coordinate stayed in the tooling for days with a comment claiming it was
"6.5 m outside iron-era-workshop's footprint" — a footprint that never existed.

---

## 1. Seat on the surface a player stands on, plus the prefab's OWN measured pivot offset

### 1.1 The rule

```
place_y = (the Y of the surface a player stands on) + (this prefab's own measured offset
                                                       from its pivot to its lowest
                                                       STRUCTURAL solid, negated)
```

Both terms are per-object measurements. **Neither may be inherited from a sibling prefab,
a visually identical prefab, or the same prefab in another kit.**

Instrument: `tools/jumpstart/blueprints/PieceGeometry.cs`, run by `run_piecegeometry.sh`,
output `tools/jumpstart/blueprints/data/piece_geometry.json` (generated
2026-09-15T05:52:38Z, game `1.0.12`). Totals in that file: 11,726 colliders over 4,644
prefabs, 8,766 axis-aligned, 2,960 oblique, 11 unmeasurable, **246 triggers dropped and
19,236 render meshes dropped**. Those last two numbers are the rule made mechanical: the
dump is solid non-trigger colliders only, because a trigger volume and a render mesh are
not things a player stands on.

### 1.2 The per-prefab pivot table, MEASURED

All spans are prefab-local Y, from `piece_geometry.json`.

| prefab | collider Y span | pivot sits | seat as |
| --- | --- | --- | --- |
| `fb_fortress_wall` and all 10 `fb_fortress_wall*` variants | `0.008 … 10.010` walkway box, body to `11.866` | **at the foot** (`pivot_is_foot=true`) | `place_y = surface_y` |
| `fb_house_01…04`, `fb_viking_house_01…08`, `fb_shelter_01` | min Y within 0.25 m of 0 | **at the foot** | `place_y = surface_y` |
| `piece_groundtorch` | `-0.6536 … +0.8226` | **0.65359 m UP THE SHAFT** | `place_y = surface_y + 0.65359` |
| `CastleKit_groundtorch` | `-0.0516 … +1.4246` | **at the foot** | `place_y = surface_y + 0.0516` |
| `piece_dvergr_lantern_pole` | lowest solid `-2.991` | **~3.0 m above its foot** | `place_y = surface_y + 2.991` |
| `wood_floor` | `-0.0329 … +0.0969` | **near the TOP face** | a deck whose walking surface is 30.6 is authored at **30.503** |
| `stone_floor_2x2` | `-0.5 … +0.5` | **mid-thickness** of a 2 × 2 × 1 m slab | walkable top is `pivot + 0.500` |
| `iron_floor_1x1` | `-0.55 … -0.45` | **entirely above its own slab** | top is `pivot − 0.450` |
| `woodwall` | `-1.0 … +1.0` | **centre** | a course on a deck is authored `+1.0` above it |
| `wood_pole_log_4` | `-2.0 … +2.0` | **centre** of a 4 m log | a pile from the seabed is authored 2 m above it |
| `portal_wood` | `-0.007 … +3.2868` | **at the foot** | `place_y = surface_y` |
| `sign` | `-0.275 … +0.275` | **centre** | the project seats guideposts at `ground + 1.4` |
| `piece_workbench` | `+0.025 … +1.055` | just below the frame | rests correctly when spawned AT pad height |
| `forge` / `hearth` / `piece_chest_wood` / `smelter` / `charcoal_kiln` | `+0.035` / `−0.017` / `+0.0003` / `0.000` / `−0.023` | at or within 35 mm of the pad | spawn AT pad height |

### 1.3 The 0.602 m that is pure authoring

`piece_groundtorch` and `CastleKit_groundtorch` are **the identical mesh** — both span
exactly 1.4762 m of collider height, and for `piece_groundtorch` the render mesh spans
exactly the same box as the collider. They differ only in where the author put the root:

```
piece_groundtorch      y[-0.6536, +0.8226]   pivot 0.6536 m up the shaft
CastleKit_groundtorch  y[-0.0516, +1.4246]   pivot 0.0516 m above the foot
                                             ---------
difference                                   0.602 m of nothing but prefab authoring
```

**Defect prevented:** a lamp seated as though its pivot were its foot floats 0.654 m.
Recorded in `roads/lamps.py` as "the portal defect at ten times the size". Note also that
`blueprints/base_geometry.py`'s ground-resting guard *lists* `groundtorch` and then DROPS
it, because it fails the pivot-at-base check — that is the module refusing to guess, not a
gap. Ask `fixtures.fixture_box` (the same box builder the placement audit uses), never a
literal.

### 1.4 The counter-rule, stated because inheriting it is the recorded defect

`settlements/harbour_city.py::house_seat` puts the pivot Y at the pad floor **exactly**,
with no shaft offset, because every `fb_` body measured has its pivot at the foot. Its
docstring names the hazard verbatim: *"adding the ground-torch's +0.65359 here is precisely
the inheritance that put three beacons in the air."*

### 1.5 A pivot is not centred either — the horizontal half of the same defect

MEASURED: `fb_house_01`'s AABB reaches `x[-11.317, 10.151]`, `z[-10.734, 1.128]`. Its pivot
is therefore **0.583 m east and 4.803 m north of its own footprint centre**. A pad sized
from the AABB's width and then centred on the pivot hangs **a third of the building off the
platform**.

`settlements/harbour_city.py::HOUSES` carries both numbers per prefab — the extent AND the
pivot's offset inside it (`ox`, `oz`) — and `house_seat` rotates the offset into world axes
with the yaw before placing:

| prefab | w × d × h (m) | pivot offset (ox, oz) |
| --- | --- | --- |
| `fb_house_01` | 21.469 × 11.862 × 7.489 | (−0.583, +4.803) |
| `fb_house_02` | 13.624 × 9.376 × 7.394 | (−1.229, +3.892) |
| `fb_house_03` | 18.057 × 14.902 × 7.824 | (−1.297, +0.004) |
| `fb_house_04` | 26.071 × 22.661 × 13.907 | (+0.608, +8.737) |
| `fb_shelter_01` | 11.421 × 12.072 × 6.006 | (+0.012, +4.772) |
| `fb_viking_house_01` | 19.690 × 13.970 × 7.251 | (+0.990, +1.650) |
| `fb_viking_house_02` | 15.416 × 20.260 × 11.373 | (+1.355, +7.470) |
| `fb_viking_house_03` | 15.715 × 38.815 × 10.908 | (−5.126, −1.060) |
| `fb_viking_house_04` | 17.818 × 14.453 × 7.751 | (+0.012, +4.780) |
| `fb_viking_house_05` | 12.458 × 12.858 × 9.262 | (−0.010, +4.818) |
| `fb_viking_house_06` | 17.457 × 21.998 × 13.565 | (+1.489, +3.851) |
| `fb_viking_house_07` | 25.083 × 20.914 × 13.856 | (+0.442, +3.404) |
| `fb_viking_house_08` | 15.016 × 15.425 × 12.225 | (+0.001, +2.764) |

### 1.6 Placement flags are a separate refusal class, and the column order is a trap

A prefab can be correctly seated and still be refused, or accepted where you did not expect
it. Read the flags per prefab from the same component dump: `Piece.m_notOnWood`,
`m_notOnTiltingSurface`, `m_noInWater`, `m_waterPiece`, `m_groundOnly`.

MEASURED on the `fb_` kit: `no_in_water = 0`, `water_piece = 0`, `ground_only = 0` on
`fb_fortress_portcullis` and `fb_fortress_gates` — **the kit is not refused over water**,
which is what allows a curtain wall on a seabed (§8.5) and free-standing barrier panels in a
ship channel (§11.3).

MEASURED correction, and the reason to name the instrument: `piece_groundtorch` was reported
as `m_notOnWood = true` and it is **`m_notOnWood = 0`**. The field that IS 1 is
**`m_notOnTiltingSurface`**. Three instruments agree and the original claim was retracted.
Root cause of the misread: a TSV column **HEADER** was correct while the position it resolved
to was not, so `m_notOnTiltingSurface` was read in the `m_notOnWood` slot. Practical
consequence: a standing iron torch **may** stand on a wooden deck, and **may not** stand on a
sloped surface — the opposite of the briefed constraint in both halves.

---

## 2. A mesh AABB is not a platform

### 2.1 The tower, and the 5.173 m of air

`fb_fortress_wall_tower` solid AABB: **20.009 × 15.402 × 9.639 m**. Its **walkable
platform is a collider at local y = 10.229**; its cap is at 14.269; 15.402 is the mesh top.

```
15.402  mesh AABB top      <- NOT a surface
14.269  cap
10.229  walkable platform  <- the surface a player stands on
------
 5.173  m of air that three tower beacons were placed in
```

Confirmed twice with independent arithmetic. The Ulfsland lighthouse was solved onto the
driest headland seat at `(1006, 768)`, ground `h = 31.786`, freeboard `+1.786`:

```
31.786 + 10.229 = 42.015   beacon on the fighting platform   <- built here
31.786 + 15.402 = 47.188   mesh top                          <- recorded as the thing it is not
```

`harbour_city.py::lighthouse` returns `platform_y`, `cap_y` AND `mesh_top_y` in the same
record with `mesh_top_is_not_the_surface=True`, so the wrong number is present and labelled
rather than absent and re-derivable.

### 2.2 The bonfire, and the union AABB that reaches BELOW the pivot

`bonfire` carries **three** solid boxes (`piece_geometry.json`):

| box | Y span | height | what it is |
| --- | --- | --- | --- |
| 1 | `−0.4962 … +0.5307` | 1.027 m | **the pyre's log ring — the structural solid** |
| 2 | `−2.130 … +2.670` | 4.800 m | the fire volume |
| 3 | `+0.1685 … +4.1486` | 3.980 m | the flame/light column, 1.66 m wide |

The **union** base is `−2.130`. Seat on the union and the bonfire rises 1.634 m.

MEASURED on the lh-north lighthouse, 2026-09-17T13:24Z: beacon pyre base **72.968** on a
plinth top of **72.980** — **12 mm of engagement**. The union-AABB reading would have
reported **1.645 m of air** there and "corrected" a correct placement.

`fire_pit` has the identical shape — a 0.2 m hearth pan (`0.0 … 0.2`) plus a 2.0 m fire
volume (`−0.73 … +1.27`) — so this is the family's convention, not a one-off.

**The mechanical rule**, implemented in `settlements/waterfront.py::lh_height`: the
structural solid is **the SHORTEST of the prefab's solids in Y**. The tall ones are fire
and light volumes. Every beacon row emits `structural_base_y`, `union_aabb_base_y`,
`plinth_top_y`, `engagement_m` and **`false_air_gap_if_union_used_m`**, so the wrong answer
is printed beside the right one.

### 2.3 The same defect in two more places, for calibration

- **A gate opening.** `mesh`/`mesh_render` rows report the whole 20 × 11.87 × 6.8 m
  envelope for every wall variant and are useless for an opening. From the BOX colliders:
  `fb_fortress_wall_gateway` has solids at `x[1.704, 10.004]` and `x[−10.002, −1.725]` →
  **3.429 m clear**; `fb_fortress_wall_portcullis` at `x[3.195, 10.004]` and
  `x[−10.002, −3.656]` → **6.851 m clear**. Both are narrower than every large hull's
  9.16 m beam, which is why the harbour ship gate had to be a **gap between modules**
  barred by free-standing panels rather than a gateway module.
- **A hull.** A `mesh_render` bound read **11.64 × 35.43 m** for a ship whose hull is
  **9.16 × 21.57 m**, because the bound included a water-ripple plane. It would have
  oversized every slip by 60%.
- **A location.** `clearance.py` dumps `rendererReachM` and **deliberately does not use
  it**: `AbandonedLogCabin04` reports **528.7 m** against a **5.0 m** ZNetView reach,
  because `Renderer.bounds` on an un-instantiated prefab is not a world measurement.

---

## 3. The grounding census

### 3.1 What it measures

`verify_placement.bottoming(objs, geom)` returns, per plan-space column, **which object is
the lowest solid and at what Y** — rotation and scale applied to the real collider corners.
Columns are the cells of `base_geometry.BASE_PROFILE_CELL_M = 2.0` m. (The `lh_grounding`
docstring says "1 m columns"; the constant is 2.0. Trust the constant.)

Two instruments, one convention:

- `settlements/build.py::grounding(plan_path, pad_y)` — `GROUND_TOL_M = 0.10`. A column is
  **grounded when its lowest solid sits at or BELOW `pad + 0.10`**, i.e. on the pad or
  embedded in it. Returns `columns`, `grounded_fraction`, `worst_air_gap_m`,
  `p95_air_gap_m`, `deepest_below_pad_m`.
- `settlements/waterfront.py::lh_grounding(spec)` — the same convention plus the
  **SUNK / FLUSH / OVERHANG split**, which is the part an operator can act on.

The split exists because the bare fraction lies about a masonry building. Quoting the
module: *"A bare 'grounded fraction 0.03' on this body would read as a floating building
when what it describes is a foundation ring seated 2.00 m into its own pad."* Defining a
second, stricter "flush only" fraction was explicitly refused — it would give the project
two grounding instruments that disagree by 0.73 on the same body, which is how two
clearance instruments once happened.

### 3.2 The measured census: lh-north lighthouse, Ulfsland, 2026-09-17T13:25Z

```
columns                           60
tolerance_m                        0.10
grounded_fraction                  0.767
sunk_columns                      44      deepest_below_pad_m  -2.000
overhang_columns                  14      lowest_overhang_above_pad_m  34.787
worst_air_gap_m                   36.967
floating_near_ground_columns       0
```

Reading, verbatim from the run: *"the 44 sunk columns are the foundation ring embedded
2.000 m in its own pad; the 14 overhangs are the lantern gallery and the eaves, all above
34.8 m; NOTHING floats near the ground. Identical by construction at lh-south/east/west,
which carry the same blueprint."*

So: **worst burial −2.000 m is by construction; worst air gap 36.967 m is a lantern gallery
34.787 m up.** Neither is a defect.

### 3.3 Legitimate overhang versus defect

`floating_near_ground_columns` is the discriminator actually used:

```python
floating_near_ground_columns = sum(1 for g in over if g <= 4.0)
```

An overhang 34.8 m up is a gallery. An overhang 0.4 m up is a building that missed the
ground. **The number to report and to gate on is the count of overhang columns within 4.0 m
of the pad, and on this body it is zero.**

For bodies not yet placed, `library/audit_bases.py` answers the harder version — is the air
BETWEEN SUPPORTS or under a whole unsupported wing:

```
air_isolation_max_m = max over floating columns of the distance to the nearest
                      GROUNDED column
```

MEASURED over 528 resolvable bodies, the distribution is strongly bimodal and the two
populations barely touch: structural air (docks, 12 bridges, the stilt swamp town, portal
arcades) runs **2.00–6.00 m, n=102, median 4.00**; slope and cliff captures run
**6.32–100.58 m, n=99, median 16.49**. Hence `ISOLATION_TOL_M = 6.0` — it admits a 4 m pile
grid with its 5.66 m diagonal and rejects any column more than three 2 m cells from
support. The pair that proves the number has nearly the same air fraction and opposite
structure:

```
drake-lighthouse    33.3% floating, isolation  2.00 m  -> a tower on legs
BjOrN_blueprint001  34.1% floating, isolation 33.29 m  -> needs a hillside that is not here
```

`PLINTH_TOL_M = 1.0` (one wall course, from `base_geometry.MAJOR_LEVEL_CLIMB_M`) excludes
furniture: without it exactly **1** body of 528 was condemned —
`hs_blackforest_kitchenbench_brigames`, 110 pieces, 86.7% "floating", worst gap **0.77 m**
— while the 10th percentile of worst-gap among the other 89 condemned bodies is **4.62 m**.
The gate separates exactly that one case.

**Geometry cannot tell a harbour from a hillside castle.** MEASURED: Olivanderr's `dock` is
**70.1% floating with 13.42 m isolation**, numerically indistinguishable from a slope
capture, because a pier's open basin IS a large unsupported span. So intent is a separate
field derived from the body's curated `category`, never from geometry, and only
`DEFECT_on_land` (needs_support AND meant for land) condemns a body.

### 3.4 Why the census is a deliverable and not a nicety

`wnt = 0` on the whole `fb_` kit: **no `WearNTear` at all**. Nothing needs a structural
footing — and **nothing will ever announce a floating or buried segment through weather
damage or collapse.** The grounding census is the only instrument that will catch it.

The vanilla deck kit is the opposite: `wood_pole_log_4`/`wood_floor` DO have `WearNTear`, so
`harbour_city.py::piles` sizes a pile stack to **REACH** the deck and never to stop short —
a deck tile with no pile under it really does fall.

### 3.5 The censusing trap that produced 64 duplicates

MEASURED, NorthLight, 2026-09-17: an `audit` `y_levels` parameter was added and **never
used**, which made a box census blind above 72.98 m and caused **64 duplicate pieces** (all
repaired per position, top-down, delete-both-respawn-one with exact column-count
postconditions). Also: a y-blind cylinder over a stacked prefab reported `absent` for
something standing.

**The per-piece probe replaced the box census as the diff instrument for good.** The
verified build reads: 1255/1255 pieces inside 7.28 m, all 24 prefabs exact at tolerance 0,
0 strays at 14 m, confirmed by **1255 individual per-piece cube probes: 0 missing, 0
duplicates**. The count radius 7.28 m is not a taste pick — it is `build.check_radius`'s
gap-midpoint radius, chosen because `objects_count`'s comparison MEASURED **2 of 4 pieces
at exactly 1.000 m** as inside, so no ring may sit ON the boundary.

### 3.6 Two builders emitting one shared element each emit it IN FULL

MEASURED 2026-09-19 at Ulfsgard, predicted from source and then confirmed on the
standing city: **52 duplicate pieces**, cube probes at `r = 0.4` with controls in
both directions.

```
DUPLICATED, standing
  wood_floor        20    x 1067-1081, z 685-709, y 30.503
  wood_pole_log_4   19    y 26.0 and 28.55-28.93
  wood_roof         13    x 1080.5, z 685-709, y 34.6
                    --
                    52
CONTROLS
  8 singly-emitted positions   -> 1 each
  3 empty points               -> 0 each
```

The cause is structural and it will recur in any builder composed of units:
`settlements/harbour_city.py::boathouse()` gives bay 1's EAST apron and bay 2's
WEST apron **the same** `deck_strip(x_centre-1, x_centre+1)`. Both sheds emit it,
in full, and neither is wrong on its own. **Any element on a boundary between two
sub-builders is a duplication candidate.**

**Use a CUBE, not a cylinder.** `objects_count` is an XZ cylinder with **no height
bound** (§3.5), so it reads a legitimate pile stack — `wood_pole_log_4` under a
deck is exactly that — as a duplicate. The cube probe's own resolution was
measured rather than assumed: **6 positions read 4 at `r = 0.4`** because two
stacks sit **0.373 m apart in y**, and **at `r = 0.1` all six read 2**. The
instrument discriminates 0 / 1 / 2, which is what makes the 52 a measurement.

**Remedy, all three together, because each one leaks on its own:**

1. **Pitch adjacent units** so shared runs become distinct tile columns rather
   than one column two builders both claim.
2. **De-duplicate the shared line explicitly at the seam**, in the builder that
   owns the boundary.
3. **Run ONE global exact-row de-duplication over the whole plan before staging** —
   the only step that catches a seam nobody predicted.

**Repair what is standing; do not wait for a rebuild to do it.** The repair is the
§3.5 one: remove BOTH copies at each duplicated position and re-send the plan's own
line. A regeneration does collapse these 52 for free — and MEASURED 2026-09-19 the
same fold destroys **130 legitimate pieces at this same site**, because its key omits
y: Ulfsgard projects **1,004 rows to 828**, `woodwall` 164 -> 68, `wood_pole_log_4`
183 -> 133, `fb_fortress_portcullis` **6 -> 3** (lower row y 23.774, upper y 29.481,
same three xz — the sea gate rebuilds halved). The full verdict, and the three-class
model that replaced "a regeneration repairs this for free", is
`world-generation-and-replay.md` §5.5.4.

### 3.7 A check whose reference is the thing under test is not a check

**Rule: a grounding census compares the body against the surface the ENGINE
RENDERS. Never against the datum the builder chose.**

`settlements/build.py::grounding(plan_path, pad_y)` compares every body to
`pad_y` — **the scalar the pad was levelled to**. A body seated on that number is
grounded against it *by construction*, so the census could not have returned
anything but zero. Four separate censuses reported a clean world while a
212-piece tower stood in the air, and none of them was buggy: **they measured the
build against its own intent.**

The defect they could not see, MEASURED 2026-09-19: `wt-peak`'s pad was levelled
to **102.310** while the rendered ground under it is **93.885 .. 99.427**, so the
tower, the `u-wtpeak` portal and its guidepost stand in **3.17 to 13.54 m of air,
median 4.92 m**. The cause is that the pad was solved against
`WorldGenerator::GetHeight` and the engine draws `HeightmapBuilder::Build` —
stated with the IL, the zone's biome split and the 1,186-object validation in
`terrain-and-roads.md` §1.6.

**The fix was proved on the real function, in all three directions, over
`wt-peak`'s own 212-piece plan** — which is what makes it a repair rather than a
new opinion:

```
reference = pad_y scalar      (what four censuses ran)
    grounded 0.875   floating_near_ground 0        <- the false clear, reproduced
reference = applied surface   (the fix)
    grounded 0.000   floating_near_ground 10   minimum air 3.173 m
reference = applied surface, after the staged repair
    grounded 0.875   floating_near_ground 0
```

A guard that only fires is a refusal wearing a guard's name; this one fires on
the defect, passes the repair, and reproduces the original false clear on
demand. And the degraded mode now **labels itself** `reference: pad_y_scalar` in
its own output, so a clean verdict from it can never again be quoted as a
grounding result.

Two consequences for this document's own instruments:

- **§3.1's bottoming census is only as good as the surface handed to it.** Feed
  it the rendered surface (`PATCHSCAN_SURFACE=heightmap`), not the pad's target
  and not a worldgen sample.
- **Burial is the worse half and it is invisible.** A floating body is something
  the operator can report — he reported this one. A body sunk into the rendered
  surface shows nothing from outside and is not repaired by re-seating alone.
  Report FLOATING and BURIED as separate counts, never as one absolute residual.

### 3.8 Orientation is not geometry — verify the SIGNED quantity, never the volume

**Rule: for any oriented piece, verify the ORIENTATION as a signed quantity.
Never verify it by the volume the piece occupies.** A census of where mass is
cannot see which way that mass points.

MEASURED 2026-09-21 across three boathouses: **all 480 `wood_roof` panels were
pitched INWARD** — measured gradient **−0.5654 m/m toward the ridge**, where
correct is **+0.5654**. The panels shed rain into the building.

**A 180-degree yaw flip moves NEITHER the footprint NOR the AABB**, so every
volume-shaped instrument this document already recommends returned the identical
answer on the defect and on the repair:

```
count census                        identical before and after
position census, tolerance 0        identical before and after
portal-hall plane-coverage test     open-to-sky 0 of 3,705 samples BOTH ways
the only difference anywhere        a 6.1 mm sliver at the eave, because the
                                    prism is asymmetric: low edge +1.0031,
                                    high edge −0.9971
```

**THE ONLY DISCRIMINATOR IS THE SIGNED GRADIENT TOWARD THE RIDGE.** Two
consequences for the instruments named in this manual:

- `terminus_camp.py::cover` answers *"is something overhead"*. It can never
  answer *"which way does it face"*, and reading it as a roof check is the §3.7
  defect in a different axis.
- `roof_y_at` previously raised `SystemExit` on any yaw but 0/180, so it could
  not measure these roofs **at all** — an instrument that refuses the population
  it was written for reports nothing and looks like silence.

**And keep the corroboration, because it is the cheapest check available and it
needs no tooling: the closed-end gable stack NARROWS UPWARD — 7 walls, 7, 7,
then 5 — so the building's own end wall says the roof peaks at the centre. The
roof contradicted its own gable.** Where a body carries a second element that
encodes the same intent, read it: it costs one census and it is independent of
the orientation you are trying to prove.

---

## 4. Piece registration is the gate before any placement

A piece you cannot place is not a design constraint you can work around — it is a build
that silently produces nothing. Measure registration BEFORE laying out a settlement.

### 4.1 Two registration paths, and they fail differently

| Path | API | Installed examples |
| --- | --- | --- |
| **Jotunn** | `Jotunn.Managers.PieceManager::AddPiece` with a `PieceConfig` | `RavenwoodVikingHouses 7.7.8` (131 sites), `RavenwoodPieces 2.0.5` (131), `RavenwoodDwarvenKingdoms 1.1.0` (50) |
| **the BUNDLED `PieceManager`/`BuildPiece` helper library** | `BuildPiece::.ctor`, patching `PieceTable.UpdateAvailable` | `OdinsKingdom 1.5.8` (278 ctor sites, **0** Jotunn sites), `RavenwoodRestorations 1.0.1` (113, **0**) |

MEASURED by IL. `RavenwoodRestorations` does **not** register through Jotunn — a
Jotunn-only survey will report its 113 pieces as fine when they are behind a dead patch.

### 4.2 The 1-byte `ret`: a live plugin that registers nothing

`RavenwoodVikingHouses 7.7.8`, installed at
`Ulfsland/config_merged/bepinex/plugins/RavenwoodVikingHouses/RavenwoodVikingHouses.dll`.
`RavenwoodPiecesPlugin::Awake` (139 IL bytes) subscribes three entry points to
`PrefabManager::add_OnVanillaPrefabsAvailable`:

| entry point | IL bytes | body | effect |
| --- | --- | --- | --- |
| `AddRWEffects` | **1** | `2a` = `ret` | dead |
| `AddRWPieces` | **1** | `2a` = `ret` | **dead — 102 `RW_`/`mm_` sites never register** |
| `AddFBPieces` | **65** | `call FB_Pieces::AddPieces` in try/catch + `remove_OnVanillaPrefabsAvailable` | **live — 29 `fb_` sites register** |

| class | `AddPiece` sites | reachable |
| --- | --- | --- |
| `FB_Pieces` | 29 | **yes** |
| `RW_Pieces` | 102 | no |
| total | 131 | 29 |

Bundles, from `RavenwoodPiecesPlugin::.cctor`: `piecesBundle` = `ravenwoodpieces` (dead),
`piecesBundleNew` = `ravenwoodpiecesnew` (dead), `piecesBundleFB` = **`villagestuff`**
(live). All 131 sites use `_HammerPieceTable` and the single category `Ravenwood-Pieces`.

Three artefacts, one DLL: installed 7.7.8, freshly downloaded 7.7.8, and freshly downloaded
7.7.7 all carry md5 `7a85c9fcb086633d8dc8fcd49a33f46b`, 64,162,816 bytes. **7.7.8 is a
documentation-only republish; `AddRWPieces` was already `ret` in 7.7.7.** Rule: *compare the
DLL hash, never the version string.*

Positive control worth copying: the author's own installed `README.md` itemises
10 Medieval Walls + 2 Gates + 2 Bridges + 1 Door + 1 Stairs + 13 Viking Houses = **29**,
independently matching the IL-derived count. Two unrelated instruments, one number.

### 4.3 The hardest variant: a name that survived onto different storage

Valheim 1.0 did not delete `PieceTable.m_availablePieces`. It **reused the name** for a new
field and moved the old one. MEASURED by decoding field signatures in
`valheim_server_Data/Managed/assembly_valheim.dll`:

| field | signature |
| --- | --- |
| `m_enabledPieces` | `HashSet<Piece>` |
| **`m_availablePieces`** | **`HashSet<Piece>`** — new in 1.0 |
| **`m_availablePiecesByCategory`** | **`List<List<Piece>>`** — the pre-1.0 `m_availablePieces`, renamed |

So **by-name reflection SUCCEEDS and only the signature fails**:

```
System.MissingFieldException: Field not found:
  List`1<List`1<Piece>> PieceTable.m_availablePieces
from (wrapper dynamic-method) PieceTable.DMD<PieceTable::UpdateAvailable>
```

**Four of ten bundled `PieceManager` copies** read the old signature — 5 IL operand
references each, all in `PiecePrefabManager::UpdateAvailable_Prefix` (3) and
`::UpdateAvailable_Postfix` (2):

| package | installed | dead refs | newest | fixed upstream? |
| --- | --- | --- | --- | --- |
| `OdinPlus-Basements` | 1.4.1 | 5 | **2.0.0** | **yes** — all 5 moved to `m_availablePiecesByCategory` |
| `OdinPlus-OdinsUndercroft` | 1.3.3 | 5 | 1.3.3 | no release exists |
| `OdinPlus-OdinsHorsePen` | 1.1.0 | 5 | 1.1.0 | no release exists |
| `JamesJonesTV-RavenwoodRestorations` | 1.0.1 | 5 | 1.0.1 | no release exists |

Correct copies, for contrast: `OdinsKingdom 1.5.8` (2 refs to the renamed field),
`OdinCampsite 1.6.4` (2).

### 4.4 One broken prefix at patch index 2 ABORTS the chain behind it

The broken prefix sat **second in the patch order** on `PieceTable.UpdateAvailable`, so it
aborted the chain for everything behind it — including three correct copies and the local
compatibility shim's own correct sizing. Result: **`m_availablePiecesByCategory.Count = 0`
on all 8 piece tables, every call, every table.** The build menu never populated.

**The failure is not additive, it is a chain abort.** One surviving broken carrier keeps
`UpdateAvailable` dead for **all ten** copies; removing one of four changes nothing.
`RavenwoodRestorations` alone puts **113** registrations behind that dead pass
(`OdinsUndercroft` 2, `OdinsHorsePen` 3, `Basements` 2).

A by-name alias is NOT the fix, and the installed `Valheim10Compatibility 1.4.1` already
tried and blocked itself, logging why:

```
[Warning:Valheim10Compatibility] BLOCKED PieceTable.m_availablePieces
(List<List<Piece>>, alias of m_availablePiecesByCategory): ...
look this field up by name through Type.GetField / Traverse.Field, which throws
AmbiguousMatchException the moment two fields share the name. Left unaliased deliberately.
```

The workable mechanism is the one Basements 2.0.0 used: **retarget the 5 `MemberRef`
operands per carrier from `m_availablePieces` to `m_availablePiecesByCategory`** (identical
`List<List<Piece>>` signature) as a preloader IL rewrite — metadata-level, no reflection
ambiguity.

### 4.5 Registered is not the same as reachable, and the grid has 90 cells

Installed Jotunn **2.30.0**, quoting the `CHANGELOG.md` shipped inside that very package:

> * **Piece categories are not updated yet, Valheim overhauled the system completely.
>   Custom pieces show up in the build menu, but don't have a category associated**

So a piece can be in `ZNetScene`, placeable by `spawn_object`, and still unreachable with a
hammer. Jotunn **2.30.1** fixes it ("Added custom piece categories to the new build menu").

The grid arithmetic, MEASURED: **stock 1.0.12 with zero mods registers 90 pieces in
`BuildingWorkbench`, and 91 available at full recipe knowledge, against a 15 × 6 = 90-cell
grid.** So vanilla itself is already at the ceiling.

| configuration | pieces in one category | vs 90 cells |
| --- | --- | --- |
| current `RavenwoodVikingHouses 7.7.8`, only `FB_Pieces` live | 29 | fits |
| swap to `RavenwoodPieces 2.0.5` (all 131 live) | 131 | **overflows by 41 — requires a build-menu mod** |

MEASURED: **no build-menu mod is present on the live world or on any of the 5 server plugin
trees** (Ulfsland 99 dirs + 4 others), nor on the 3 Ulfsland client profiles. The only two
copies on the fleet are a pre-1.0 `Azumatt-SearchableBuildMenu 1.0.4` on two
deliberately-stopped worlds.

**Consequence for a programmatic build: none of this blocks you.** RCON `spawn_object` does
not consult the build menu. The build menu matters only for what the operator can build by
hand afterwards — but it is what "the pieces are missing" will mean when they report it.

---

## 5. How to verify a prefab is placeable, in order of authority

Run down this ladder. **A higher rung never overrides a lower one, and you may not stop
early for anything load-bearing.**

| # | rung | answers | does NOT answer | measured example |
| --- | --- | --- | --- | --- |
| 1 | **the package's asset-bundle container index** (every prefab path ends `.prefab`) | what the package SHIPS | whether anything registers it | **94 prefab paths**, `mm_`/`rp_` prefixed, incl. `fb_viking_house_01…08`, `fb_fortress_gates`, `fb_fortress_portcullis`, `fb_bridge_01/02`, `fb_bridge_stairs`, `mm_large_wall` + corner/cross/end/gateway/tower. 14 of the 94 sit under `assets/_customprefabs/_todo/` — the author's own unfinished work; do not build anything load-bearing from those |
| 2 | **the DLL's `#US` user-string heap** (`monodis --userstrings`, or `strings -el`) + the IL registration sites | which names the CODE mentions, and which registration call sites are reachable | whether the runtime accepted them | **53 `fb_` strings** in the installed DLL; only **29** are registered pieces — the rest are sfx and sub-assets |
| 3 | **a live `ZNetScene.m_prefabs` dump** | what this running server actually knows | whether it can be reached in the build menu | **4,872 prefabs** in the sandbox mirror; component list dumped per prefab |
| 4 | **an actual `spawn_object` + read-back** | the truth | nothing | see below |

Rung 4, verbatim from the Ulfsland ledger, **seq 2118–2123, actor `WaterfrontRun`,
2026-09-17T11:07Z**:

| prefab | `spawn_object` | `objects_count` | verdict |
| --- | --- | --- | --- |
| `mm_large_wall` | `Object reference not set to an instance of an object` (NRE from a null `ZNetScene` lookup), placed **nothing** | `Error: Entity id mm_large_wall not recognized` | **NOT registered** |
| `fb_bridge_01` | placed | `1` | registered |
| `fb_bridge_02` | placed | `1` | registered |

Seq 2102–2117 then actually built `fb_fortress_wall`, `fb_fortress_wall_tower` and
`fb_fortress_wall_gateway` into `harbour-vestvik`.

### 5.1 Two instruments that are INVALID, with their numbers

- **`strings` over an asset-bundle mod is invalid.** Plain `strings` over the DLL produced
  **95 "piece-relevant removals"** that were ALL compression noise from the embedded 17 MB
  asset bundle. It would have held a safe update on a phantom defect. Use the `#US` heap.
- **A prefix filter over a bundle is invalid.** Guessed prefixes (`RW`, `Ravenwood`,
  `Wall`…) returned **31 hits out of 55,789 strings, all noise**. The names are not
  prefixed that way at all. Read the bundle's own index.
- **A package instrument is not a runtime instrument.** 53 DLL strings, 29 registered. The
  gap is not noise; it is sfx and sub-assets that would fail exactly like `mm_large_wall`.
- **A negative search proves nothing unless the search term could have matched.** The live
  `ZNetScene` dump was searched for `mm_`, `RW_` and `jj_` — three prefixes that all belong
  to the *stubbed* families. The village prefabs are `fb_`, which was never a search term.
  The zero result said nothing about them either way.
- **A timeout MUST NOT be able to produce a measurement.** A registration probe waited
  **6000 FRAMES** for `ZNetScene` instead of real time. The server had not loaded a world,
  `ZNetScene.instance` was null, `m_prefabs` read **−1**, and the tool wrote a **complete,
  well-formed, fully-populated TSV in which every prefab read `znet=0`** — including
  `fb_fortress_wall`, which was demonstrably registered and already standing in the world.
  Caught only because the author also printed the prefab total and saw `−1`. Fix both ways:
  wait on a real-time deadline, and **on timeout write NOTHING**. Print the population size
  beside every result so an empty world is distinguishable from an empty answer.

### 5.2 The Ulfsland `fb_` vocabulary, registered as of 2026-09-18T03:28Z

Registered (`in ZNetScene`, `Piece` present, `ZNetView.persistent = 1`, `WearNTear = 0`):
all 10 `fb_fortress_wall*` variants, `fb_fortress_gates`, `fb_fortress_portcullis`,
`fb_fortress_wall_stair_left`/`_right`, `fb_house_01…04`, `fb_viking_house_01…08`,
`fb_shelter_01`, `fb_bridge_01`, `fb_bridge_02`, `fb_bridge_stairs`, `fb_wood_door_01`.

**NOT registered** — these are `fb_`-prefixed strings in the DLL that would fail exactly
like `mm_large_wall`: `fb_door`, `fb_portcullis`, `fb_window`, `fb_wood_door_02`,
`fb_wood_door_03`, `fb_wood_doorway_01`.

Measured kit geometry to build against (solid non-trigger colliders only):

| prefab | solid AABB x × y × z | notes |
| --- | --- | --- |
| `fb_fortress_wall` | 20.009 × 11.866 × 6.800 | pivot at foot; walkway box top y 10.010 |
| `fb_fortress_wall_tower` | 20.009 × 15.402 × 9.639 | **walkable platform y 10.229**, cap y 14.269 |
| `fb_fortress_wall_gateway` | 20.007 × 11.871 × 6.801 | clear opening only **3.429 m** |
| `fb_fortress_wall_portcullis` | 20.007 × 11.866 × 6.800 | clear opening **6.851 m** |
| `fb_fortress_wall_stair_left`/`_right` | 20.009 × 11.866 × 9.940 | gets defenders onto the walkway |
| `fb_fortress_portcullis` | 5.704 × 5.857 × 0.216 | free-placing barrier panel |
| `fb_fortress_gates` | 3.558 × 6.006 × 0.232 | hinged leaf pair, land gate |
| `fb_bridge_01` / `fb_bridge_02` | 18.614 m one-prefab modules | `wnt = 0`, so the 96 m span ceiling in `roads/route.py` — a `WearNTear` property — does not bind them |

---

## 6. Blueprints: the format our tooling reads and writes

### 6.1 Corpus scale, MEASURED

164 of 176 manifest rows resolve to bodies on disk by SHA-256 (10 are 0-byte, 2 are
duplicate names) → **407,860 piece rows across 157 `.blueprint` and 7 `.vbuild` files**.

### 6.2 `.blueprint` (PlanBuild / Infinity Hammer lineage), MEASURED from `InfinityHammer.dll`

`;`-separated, any `,` rewritten to `.` first so decimal-comma locales parse, floats
invariant `0.###`:

| # | field | # | field |
| --- | --- | --- | --- |
| 0 | prefab name | 9 | extra info / text (default `""`) |
| 1 | PlanBuild category — parsed, unused; IH writes `""` | 10–12 | scale x, y, z (default 1) |
| 2–4 | position x, y, z | 13 | base64 ZDO data (only with `data=true`) |
| 5–8 | rotation quaternion x, y, z, w | 14 | placement chance (default 1) |

IH writes `prefab;;x;y;z;rx;ry;rz;rw;info;sx;sy;sz;data` — empty category column, no
`chance`.

**Header vocabulary is closed, and an unknown header is destructive.** From the deployed
DLL's user-string heap the complete list is
`#` `#name:` `#creator:` `#description:` `#category:` `#center:` `#coordinates:`
`#rotation:` `#pieces` `#height:` `#paint:`, plus `#SnapPoints` / `#TerrainHeight:` /
`#TerrainPaint:` matched case-insensitively. There is **no `#objects` literal anywhere in
the assembly**. The section machine is five-state and starts at Pieces, so a headerless
file is all pieces. Then:

- an **unrecognised** header whose second character is not whitespace sets the section to
  **None**, and every row after it is discarded until the next recognised header;
- a header of length 1, or with whitespace at index 1, is a **comment** and the section
  survives it;
- `#Height:` / `#Paint:` are recognised and then **rejected** — the file does not load.

**Defect prevented:** `inventory.py` and `to_rcon_plan.py` used to keep the current section
across an unknown header, which read PlanBuild's `#Terrain` block — rows shaped
`shape;x;y;z;radius;rotation;smooth;` — as **pieces named `circle` and `square`**. Both are
genuine tokens in the game's asset bundles and both resolve `vanilla` in
`data/prefab_evidence.json`, so the viability check could not catch it. Downstream,
`to_rcon_plan.py` would have emitted **`spawn circle`** at a live server. Corrected counts
over the committed corpus: 7 files carry a `#Terrain` block, 16 rows total; e.g.
`god-house33` 2950 → 2949 pieces and a footprint Y of 47.2 → **29.1 m**. None of the 12
blueprints referenced by `placements.yaml` was affected (`discarded_rows: 0` on all).

### 6.3 `.vbuild` (legacy BuildShare) is a strict capability subset

Split on a **single space with empty entries KEPT** — fixed arity, a zero component
serialises as an empty field: `0` prefab, `1–4` rotation quaternion, `5–7` position,
`8` extra info, `9` placement chance. No scale column; IH forces `Vector3.one`.

All 7 `.vbuild` files in the corpus are 8-field throughout: **no scale, no extra info, no
terrain, no snap points.**

**Splitting on whitespace RUNS is the trap** — it drops empty fields and shifts every
column: it silently discarded **1,615 of `berserkers-keep.vbuild`'s 10,686 pieces** and
produced a nonsense **10,152 m** vertical footprint. Validation that caught the correct
layout: across 12,137 rows the four rotation components satisfy `x²+y²+z²+w² = 1` to within
**1.5e-6**.

### 6.4 The 14th column: 125,272 rows of state our spawn path discards

**MEASURED: column 13 is present on 125,314 of 407,860 piece rows and non-empty on
125,272.** It is base64 of `WorldEditCommands.Data.DataEntry`, read by
`DataEntry::Load(ZPackage)` in the deployed `World_Edit_Commands/WorldEditCommands.dll`
1.77.0. `DataEntry(string base64)` is literally `new ZPackage(base64)` → `Load(ZPackage)`.

```
int32 flags
0x0001 floats      byte n, n x (int32 hash, float32)
0x0002 vector3     byte n, n x (int32 hash, 3 x float32)
0x0004 quaternion  byte n, n x (int32 hash, 4 x float32)
0x0008 ints        byte n, n x (int32 hash, int32)
0x0040 longs       byte n, n x (int32 hash, int64)
0x0010 strings     byte n, n x (int32 hash, 7-bit-length UTF-8)
0x0080 byteArrays  byte n, n x (int32 hash, int32 len, bytes)
0x0100 connection  byte type, int32 hash
0x0200 persistent / 0x0400 distant / 0x0800 priority
```

**Longs (0x40) are read BEFORE strings (0x10)** — not bit order, and that is what defeats a
naive decoder. Keys are `StringExtensionMethods.GetStableHashCode` of the key name.

Decode result: **125,576 of 125,580 data-bearing rows decode cleanly and consume exactly
their byte length (100.00%)**. The 4 failures are not base64 at all — column 13 holds the
literal string `ShieldWood` (an item-stand payload in plain form).

What it carries, by rows: `support` 123,234 · `health` 85,346 · `HasFieldsWearNTear`
84,776 · `WearNTear.m_health` 84,555 · `HasFields` 86,759 · `creator` 65,547 · `scale`
59,536 (+ `scaleScalar` 2,178) · `WearNTear.m_supports` 56,485 ·
`Piece.m_primaryTarget`/`m_randomTarget` 56,264 each · `steamID`/`steamName` 8,620 each ·
`crafterName` 2,146 · item quality/durability/stack 898.

Also: **column 9** (`additionalInfo` — sign text, item-stand spec, PlanBuild inventory) is
non-empty on **11,006 rows, 595 distinct values**; **scale** is non-unit on **7,836 rows**.

**Our spawn path reads all three and discards them at emit.** So a blueprint can natively
carry chest contents, piece quality, sign text, crafter name and per-piece health — which
the pipeline currently hand-builds separately through `chest-manifest.yaml`. Treat this as
capability available and unwired, not as a missing feature.

### 6.5 Rotation convention, validated rather than assumed

`placements.yaml` records `{units: euler_degrees_yxz, yaw, pitch, roll}`. MEASURED:
ValheimRcon's `spawn ... -rotation <x> <y> <z>` passes the vector to `Quaternion::Euler`,
which composes `Ry(y) * Rx(x) * Rz(z)`. Yaw is the **Y** component in degrees; `+yaw` turns
the building clockwise viewed from above. `findObjects -detailed` reports the same way.

Blueprints store a quaternion, so `to_rcon_plan.py` converts — round-tripping to a worst
error of **3.4e-6 degrees** over 300,000 random quaternions, every exact gimbal-lock case,
and the 22.5-degree grid Valheim building actually uses. A naive `asin` decomposition
silently produces a **~52-degree** error on roughly one rotation in twenty thousand.

---

## 7. Alignment: the floor datum, and the bug that landed pieces 40 m away

### 7.1 A pivot is not a surface, and `min(pivot Y)` is not a floor

Of the 157 non-empty `.blueprint` bodies, **99 are normalised so `min(pivot Y)` is exactly
0** and **58 are not**, the latter spanning `min(pivot Y)` from **−5064.42** to **+4.49** m.
A bare coordinate is ambiguous and so is the minimum pivot.

The OLD rule (`ground` / `ground-center`) computed `dy = -min(pivot Y)`. Two defects:

- **a pivot is not a surface** — see §1.2 (`stone_floor_2x2` mid-thickness,
  `iron_floor_1x1` entirely above its own slab);
- **on 99 of 157 bodies the shift did nothing at all** — `dy` was exactly 0.0.

MEASURED in live play on Ulfsland: the `pre-bonemass/iron-era-workshop` pad was flattened to
**70.91** and the blueprint's local Y = 0 plane dropped there, which put its lowest walkable
floor **1.50 m** above the pad, its main floor (124 m² at local 1.834) **1.83 m** above, and
the **median air gap under the structure 2.05 m** over 337 occupied columns, modal column
2.1 m, rear terrace **6.02 m**. Loose props spawned by `terraform/stock.py` at the same
70.91 sat correctly — **that is the tell: the props used the pad datum and the structure did
not.**

The current rule: `base_geometry.floor_datum(objects)` returns a `FloorDatum` whose `base_y`
is the blueprint-local Y of the **lowest walkable floor surface**, computed from each
piece's real collider with the row's own rotation and scale applied. Then

```
placement_y = pad_height - base_y
```

Across the 174 catalogued bodies: **162 resolve, 12 refuse (all zero-piece files), and 101
have supports correctly left below the chosen plane.** `align: floor-center` (the default)
centres on the target X/Z and drops the floor plane onto the target Y; `floor` does the Y
half only; `raw` and `center` exist; `ground`/`ground-center` are kept **only so the
difference can be measured**.

**What alignment does NOT fix:** a blueprint captured across a slope has floor levels at
several heights and a flat pad has one. Only the lowest walkable level can be flush; the
residual per level is reported as `blueprint_datum.walkable_levels[].above_pad_m`. On
`iron-era-workshop` the lowest level lands flush and the main floor stands **0.34 m** proud.
That is geometry, not a defect.

### 7.2 REFUSAL: `--align floor-center` combined with `--rotate 180` DOUBLES the corner offset

MEASURED, 2026-09-15. A 65.5 × 66.0 m body was placed at pad centre `(-4673.5, -310.5)`.
`floor-center` computed the right *magnitude* — `origin shift (−32.73, −32.99)`, exactly
half the footprint — but **it is applied in blueprint space and then rotated by
`--rotate 180`, so instead of cancelling the corner offset it doubles it**:

```
spawn echoes:  x -4685..-4704   z -339..-359
pad centre:    x -4673.5        z -310.5
a centred 65x66 body should span  x -4706..-4640,  z -343..-277
```

The pieces landed **~40 m south** of where a centred body belongs.

**Before combining a centring alignment with a rotation, verify the emitted coordinate plan
against the intended span, arithmetically, before sending anything.** `--align floor`
corrects Y only and leaves X/Z on the blueprint's own origin — which for a captured build is
wherever the player stood, not the centre, so a corner-origin body can be off by up to half
its own footprint (~33 m here).

---

## 8. Site solving: solve positions, never hand-pick them

### 8.1 The principle, and the price of ignoring it

Three hand-picked placements failed in one design pass, each caught only because the tool
was then asked to solve:

1. **A boat bay with dry land under it.** The first hand-picked boathouse attempt produced
   three bays that fitted **NOTHING**, one with a shallowest sample of **−0.755 m** — i.e.
   dry land under a boat bay. Solved instead: 3 boathouses × 2 bays, each **11.5 m clear ×
   26.0 m**, shallowest-sample depths **3.542 / 4.449 / 4.045 / 3.898 / 4.116 / 4.100 m**,
   all six fitting up to `CargoShip`.
2. **A berth that reads deep at a point and grounds under the hull.** Hand-picked B berths:
   `(1185, 458)` read **1.96 m** and every hull failed under its footprint; `(1196, 470)`
   read **3.657 m at the point** while a `VikingShip`'s own **elliptical footprint touched
   1.30 m short**. Solved instead: 4 berths at A and 3 at B, each verified **under that
   specific hull's ellipse**, worst margin **+0.11 m** (`WarShip` at B berth 3), best
   **+2.92 m**.
3. **A lighthouse seat.** `harbour_city.py::lighthouse` raises `SystemExit` if the seat
   stands at or under `WATER_LEVEL + 0.5`, with the reason stated: *"a beacon in the surf is
   the early-dock defect wearing a lamp; move the seat rather than writing terrain to raise
   it."*

**Rule: a depth at a point is not a depth under a footprint.** Test the actual footprint
shape — a hull's ellipse, a pad's rectangle at its own yaw.

### 8.2 The pad solver

`harbour_city.py::pad_fits(s, cx, cz, w, d)`:

```
floor = MEDIAN of the footprint samples     (not mean, not max)
need  = max |sample - floor|
wet   = count of samples <= WATER_LEVEL + PAD_FREEBOARD_M
ok    = need <= CUT_MAX_M and wet == 0
```

Constants, each with its provenance:

| constant | value | provenance |
| --- | --- | --- |
| `CLAMP_M` | 8.0 | **MEASURED**: `TerrainComp::ApplyToHeightmap` clamps applied height to generated ± 8 m |
| `CLAMP_MARGIN_M` | 1.0 | held back because a write landing exactly on the clamp has no room for the bilinear blend at the pad edge |
| `CUT_MAX_M` | 7.0 | derived |
| `PAD_FREEBOARD_M` | 0.8 | a pad must stand clear of the water plane before a house goes on it, so no house is seated on surf |
| `STREET_CLEAR_M` | 3.0 | between two bodies' ROTATED footprints |
| `WATER_LEVEL` | 30.0 | **MEASURED** three independent ways on `Pirate68`; `WorldGenerator.GetHeight` returns ABSOLUTE heights |

The median is chosen because it minimises the worst cut on a monotone slope, which keeps
`need` inside the clamp for the largest possible pad. A refusal always carries its number
(`cut_needed_m`, `wet_samples`).

**A pad running off the sampled patch raises `SystemExit`** — *"widen the field rather than
guessing a height"*. Never extrapolate a height.

### 8.3 The clamp decides the pad size, not the site

Harbour A pad census, ±8 m clamp against each pad's own median floor:

| pad | candidates ≥97% dry | fit ±8 m | cut needed med / p90 / max |
| --- | --- | --- | --- |
| 16 × 16 m | 1401 | **1390 (99%)** | 2.15 / 4.69 / 10.25 m |
| 24 × 24 m | 556 | **527 (95%)** | 2.98 / 6.66 / 12.75 m |
| 32 × 32 m | 283 | 242 (86%) | 3.77 / 8.94 / 12.98 m |
| 48 × 48 m | 113 | **68 (60%)** | 6.26 / 13.53 / 16.17 m |

Harbour B: 16 m **1480/1506 (98%)**, 24 m **546/588 (93%)**, 32 m 250/314 (80%), 48 m
64/126 (51%).

**Verdict: a city is built as terraces of 16 m and 24 m pads, not as one platform.** A 48 m
pad fails the clamp 40% of the time here, and a single large levelled plaza is exactly the
shape that refuses mid-write.

### 8.4 Reading water: a span from a cross-section is not a closure

MEASURED, and the single most useful guard in the harbour survey: sweeping every east–west
line `z = 700…790`, the lines at **z = 728, 730 and 732 do NOT close the harbour** even
though their spans (84.0, 85.0, 88.0 m) are the **shortest in the whole inlet** — the first
dry sample west of centre there is the tip of a **spit with water on both sides**, so a
wall abutting it leaves the water a way round.

**Test closure topologically**: remove the candidate line from the water mask and
re-label the connected components. The chosen line was `z = 752`, `x 1000.5 → 1132.5`, span
**132.0 m** (measured waterline 1010.5…1122.5 = 112.0 m, **extended 10 m onto dry ground at
each end** so the wall dies into the banks), enclosing **10,313 m²** of which 6,000 m² is
≥1.2 m deep.

Similarly, **floatable water must be flood-fill connected to open sea**, not merely deep:
at 1.20 m draught the basin holds 10,500 m² of *connected* navigable water, and the
navigable bottleneck to open sea is **74.21 m of clear passage**.

### 8.5 A structure can stand on the seabed instead of a terrain write

MEASURED and it is the whole verdict for the harbour wall: `fb_fortress_wall` is **11.866 m
tall with its pivot at the foot**; seated on the **deepest** sample of the chosen line
(`h = 23.402`) it tops out at **35.268**, i.e. **+5.268 m above the water plane**. Six
modules, feet at 30.435 / 26.992 / 26.111 / 24.430 / 25.547 / 30.522, tops
45.837…36.296, worst freeboard **+6.296 m**, two modules on dry ground and four on the
seabed — **ZERO terrain writes.**

Why that matters: a terrain write below the water plane drains water out from under the
shoreline and is **the one damage class this project cannot undo.** And the kit permits it:
`no_in_water = 0`, `water_piece = 0`, `ground_only = 0` on `fb_fortress_portcullis` and
`fb_fortress_gates`, so the kit is not refused over water.

### 8.6 Declared radii versus real footprints

**`pad_radius_m: 100.0` on two town records is a DISTRICT radius.** Read as a building
footprint it made a road refuse to write for **294 m** and leave a town behind a **10.4 m**
step. The foundations are the **per-building rectangles** (`pad_w × pad_d` at pad yaw),
worst half-extent **13.05 m**. Keep-out for a router is the **union of the actual pads**
(39 + 15 of them at the harbour city), never one disc.

### 8.7 Stand-off from world locations: per type, from the game's own data

`settlements/clearance.py`. The naive fix failed first and its number is the reason the
tool exists: **a single 43 m + town-half-diagonal (177 m) stand-off against all 12,301
instances of `Pirate68` clears ZERO cells anywhere in the world**, because location density
puts a marker within ~155 m of nearly everywhere on land.

So `LocScan.cs` dumps per type: `exteriorRadius`, `interiorRadius`, `znviewReachM` (the
furthest horizontal distance from the location prefab's root to any child carrying a
`ZNetView` — exactly the objects that become ZDOs and that a clearing step would delete) and
`rendererReachM` (dumped and **deliberately not used**, see §2.3).

Over all 177 types of this seed:

- the largest **declared** exterior radius in the whole world is **32 m**;
- the largest **measured** ZNetView reach is **35.77 m** (`TrollCave02`, declared 12);
- **12 of 177** types reach further than their declared radius, worst excess **+11.8 m**;
- **28 of 177** carry ≤ 2 `ZNetView` children, so their content is non-networked static
  geometry or generator-spawned — for those the declared radius is the only usable number.

Hence `standoff_m = max(declared, measured)` rather than preferring the measurement. The
earlier `+11 m piece overshoot` constant, INFERRED from one observation, is **gone**.

There is **no "protected" flag** on `ZoneSystem.ZoneLocation` — checked in IL. `unique` is
FALSE even on `bosslocation` and must not be used. The proxy actually used is how the
generator TREATS an instance: `prioritized OR centerFirst OR quantity <= 5` → PROTECTED,
which correctly captures `StartTemple` (qty 1, prioritized, centerFirst), `bosslocation`
(3, prioritized), `GoblinKing` (4), `Mistlands_DvergrBossEntrance1` (5), against
`InfestedTree01` 700, `Mistlands_RoadPost1` 500, `GoblinCamp2` 200.

Labelled taste picks, not measurements: `MARGIN_M = 12.0`, `PROTECTED_EXTRA_M = 90.0`,
`DELTA_TOL_M = 0.10`. Labelled DERIVED: `TERRAIN_SPREAD_M = 1.0` — `ApplyToHeightmap`
applies each sample's delta to that sample only with no spatial falloff, so the only
spreading is the heightmap mesh interpolating at `Heightmap`'s 1 m pitch (pitch =
`m_width + 1` = 65 over a 64 m zone, SCALE 1.0). **A written sample tilts the surface out to
the next UNWRITTEN sample and no further: exactly one sample pitch.**

### 8.8 REFUSAL: the location REQUEST list is not the placed world

**`plan.json`'s `locations_dump` holds 12,301 entries and every one reports
`placed: false`** — including `StartTemple`. It is the world generator's REQUEST list.
Resolving against it put the nearest named location **20–74 m** from clusters that sit
**0.3–6.9 m** from a real placed marker, i.e. it answered LOOSE for eleven mod houses.

The placed instances are `LocationProxy` ZDOs (plus `DG_MeadowsFarm`, `MWL_Shrine`, …) in a
live census, **deduped by POSITION, never by ZDO id** — ZDO ids are reassigned on load.

Authority ordering, from `terraform/locations.py`:

- the **live world** is authoritative for POSITION. Every generated location plants a prefab
  literally named `LocationProxy`; the type lives in the ZDO's `location` int hash, which
  `findObjects` does not print. So a live query gives position and nothing else — but it
  **does** see the ~190 POIs `More_World_Locations` injects, which no mod-free dump can.
- the mod-free `tools/seedscan` dump is authoritative for the per-type RADIUS, and is used
  ONLY to identify a live marker by exact position (tolerance **0.5 m**) and thereby earn it
  a SMALLER stand-off. An unidentified marker gets the world's worst case.

MEASURED over 24 generated zones: **1 of 4** live markers matched exactly; three are
invisible to a mod-free dump.

**And a `plan.json` figure is not a DATUM either — a pad may not be levelled to one,
and a body may not be seated on one.** MEASURED: `wt-peak`'s pad was written to
`target_y 102.31 m FROM PLAN.JSON` and the body was then anchored to that same
number — **seq 545 / 546**. The number came from the generator's request list, so
nothing in the world was ever measured at it, and the body inherited the error whole
because it took its seat from the pad's declared target rather than from the pad's
levelled ground.

**The rule this is an instance of — a road, a pad or a body terminates on the DATUM
of the thing it meets, measured from what is STANDING — is stated once, with the
road-side instances and their numbers, in `terrain-and-roads.md` §4.6.** The
structures-side form of it: seat a body on the pad's MEASURED floor after levelling
(§1.1), and re-measure the pad rather than quoting its `target_y`.

### 8.9 REFUSAL: two instruments that agreed because one was disarmed

A survey needed placement radii for MODDED locations, so it ran a modded location dump to
compare against the mod-free one. The two agreed exactly: **12,301 locations, 177 types,
identical hash.** That reads as corroboration. It was not —
`More_World_Locations_AIO`'s `Awake` had THROWN in the sandbox
(`LocationQuantityManager.EnsureDefaultsLoaded -> LoadOrMigrateConfigs`, wanting a config
the sandbox did not stage), so its ~178 locations never registered and the "modded" dump was
mod-free too.

**Agreement between two instruments is evidence ONLY if they were capable of disagreeing.**

And the cheapest available tell, from the same survey: an attempt to refine a 32 m blanket
stand-off into a measured per-marker reach returned **57.64–71.45 m — LARGER than the worst
case it was meant to refine** — because a bounded census of a marker's neighbourhood holds
218–707 objects that are overwhelmingly birch, fir and rock. The number was a function of
the census BOX SIZE. **When a measurement intended to TIGHTEN a bound instead LOOSENS it,
the instrument is measuring something else.**

### 8.10 A sandbox with a subset of plugins measures a different game

A "439 pieces collapsed onto one category id" finding was an artefact of a sandbox staged
with six mods. Measured with all **121 DLLs** loaded, every id is distinct and the real
defect was one mod and one tab. Stage the full plugin set, or state the subset.

---

## 9. Mix failure is a real failure class

**No geometric check will ever flag it.** MEASURED, harbour city A: the first town pass
seated **10 × `fb_house_04` and 4 × `fb_viking_house_06` and nothing else in the whole
city** — a skyline of **ten identical 26.071 m halls**. Every one was grounded, dry, inside
the clamp, clear of the streets and clear of the keep-outs. The layout was geometrically
perfect and visually worthless.

The cause is structural, not accidental: the fitter walks the mix **largest-first** so that
good ground is spent on the buildings that need it, and a greedy largest-first fit with no
cap spends *every* good pad on the largest body.

The fix is a **per-prefab cap per quarter**, in `harbour_city.py`:

```python
MIX_CITY = ["fb_house_04", "fb_viking_house_07", "fb_viking_house_06",
            "fb_house_01", "fb_viking_house_02", "fb_house_03",
            "fb_viking_house_01", "fb_viking_house_08", "fb_viking_house_04",
            "fb_house_02", "fb_viking_house_05", "fb_shelter_01"]
CAP_CITY = {"fb_house_04": 2, "fb_viking_house_07": 2, "fb_viking_house_06": 3,
            "fb_house_01": 3, "fb_viking_house_02": 3, "fb_house_03": 3,
            "fb_viking_house_01": 4, "fb_viking_house_08": 4,
            "fb_viking_house_04": 4, "fb_house_02": 5,
            "fb_viking_house_05": 6, "fb_shelter_01": 8}
```

Result: **39 bodies at A across 5 quarters, 15 at B across 3** — 1,381 and 662 pieces.

Two further layout rules from the same tool, both stated so they can be argued with:

- **Quarters are RECTANGLES OF INTEREST with a facing yaw, not a list of seats.** A
  hand-written seat list is a guess; a rectangle plus a greedy fit against the measured
  surface is a decision the ground gets to make. Each quarter declares which way its
  buildings face, *"because a house should address the water or the street and not the
  hillside."*
- **REFUSALS ARE COUNTED, never swallowed.** `fit_quarter` returns
  `why = dict(clamp, wet, collide, keepout, offpatch, placed)`, so a quarter that seats
  nothing reports **which** of the causes did it. Three different things rejecting a seat
  are three different facts.

And one census rule that mix work needs: **a deliberately open side is not a missing wall.**
Each boathouse bay is walls-on-three-sides with a gabled roof, eaves 34.6, ridge 39.124, and
the **seaward gable is DELIBERATELY OPEN because that is the bay mouth** — *"a census
counting four walls would be counting a defect."*

---

## 10. Buildings in the carriageway

### 10.1 The report and the object

The operator walked the finished network: *"there still are some building structures in the
road in some places. stenvik for instance has a circle of huts in the road at the far end."*

MEASURED, identified: **`stenvik-beehive-1`**, body
`hs_meadows_aodi_the_hive_1_0.blueprint` ("Aodi the Hive", a ring of round huts), at
**(593.3, 959.5)**, 111 m NE of the town centre, ledger `site_pad` **seq 286**. **OURS** —
not mod content, not a treehouse hamlet. **102 of its pieces stood in
`road_T4_meadhall_stathub`'s carriageway over 21.8 m of blocked span at 0.02 m minimum
lateral.** Four more of ours stood in the same stretch: `stenvik-longhouse-2` 78 pieces /
19.5 m, `cottage-3` 42 / 9.9 m, `cottage-1` 30 / 9.0 m, `hut-1` 2 pieces; plus two
`More_World_Locations` houses (16 and 6 pieces).

Network-wide before the fix: **carriageway 1,325 pieces, shoulder 555, earthwork 1,166**; of
the 464 carriageway pieces on a THROUGH stretch, **294 ours and 170 mod**.

### 10.2 Three instruments, three different reasons for missing it

This is the house defect in its purest form and it is why
`tools/jumpstart/roads/structures.py` had to be written. Each existing instrument answered
a **different question** and answered it correctly:

| instrument | what it actually measures | why a building is invisible to it |
| --- | --- | --- |
| `roads/clear.py` | corridor census and REMOVAL plan; its closing audit counts `in_clear_width_clearable` | **a building piece is not on `CLEARABLE`**, so `blocks()` is true and the whole cylinder is skipped — correctly, because furnished mod content is the one unrepairable class. It reports a building as *a reason a tree survived*, never as a defect. Closing audit **seq 1999–2006: 18 objects network-wide and NOT ONE is a building** |
| `roads/applied.py` | the applied surface, finding foreign **TERRAIN** claims | a body whose **pad sits somewhere else entirely while its ROOF overhangs the carriageway makes no terrain claim at all** |
| `roads/network.py::poi_within_keepout` | the router's keep-out audit, fed from `roads/poi.py` | **it is fed from the world GENERATOR's location instances.** Our own settlement bodies are not location instances, so a pass-through segment has no keep-out against them. MEASURED: **T4 reports `poi_within_keepout: []`** while its centreline passes **0.10 m** from `stenvik-cottage-1`'s centre, 0.68 m from cottage-3's and 1.53 m from beehive-1's |

**Rule: before trusting a clean audit, state the question the audit actually asks.** Three
green instruments over one 102-piece defect.

### 10.3 What `structures.py` measures instead

A "building in the road" is defined so it can be argued with:

1. **A STRUCTURE piece** — classification read off the census corpus's own prefab index
   (**168 distinct names** over all 15 written segments), never guessed from a name shape,
   and `classify()` **raises on an unknown name**. `STRUCTURE` = walls, roofs, floors,
   beams, poles, doors, gates, stairs, arches, pillars. Furniture/fixtures/lights are
   `FIXTURE`, reported beside it because a workbench in a carriageway is the same routing
   defect.
2. **Inside the ROAD SURFACE, in three separately named bands** because they are three
   different defects: `CARRIAGEWAY` (`|lat| <= width/2`), `SHOULDER`
   (`<= width/2 + 1.0`), `EARTHWORK` (`<= that station's own written footprint` =
   carriageway + shoulder + batter run + terrain spread) — *ground the ribbon MOVED, which
   is how a piece ends up buried or floating.*
3. **Not on a bridged station** — a bridge deck is Crossings' and the ribbon does not write
   there.

Provenance is resolved **geometrically**, because the ledger holds no per-piece provenance
for a town: `settlements/build.py` spawns a body from a blueprint over RCON, so **the 2,606
pieces of Stenvik have no `spawn` record between them** (MEASURED: 144 `spawn` records in
the whole chain, none of them a town piece). Hence `OURS` = inside the building's own
written `site_pad` rectangle from `settlements/plan.json`; `MOD` = within `exteriorRadius`
of a generator location instance; `LOOSE` = neither — reported, never touched.

`TERMINUS` versus `THROUGH` is measured, not assumed: a cluster is TERMINUS when every piece
is within 30 m of an end NODE in **straight line, not along-track** (T9 curls around
`wttown`, so its own watchtower sits 28–62 m ALONG the ribbon while standing 3–19 m from the
node). MEASURED separation: terminus clusters reach at most **15.0 m** from a node, through
clusters start at **33.6 m** — the band between is empty, so the threshold is not
load-bearing. **A watchtower door, a portal-hall ramp and a harbour are DESTINATIONS, not
defects.**

### 10.4 The ordering cause, and the fix

MEASURED from the chain: **T4's first `terrain_write` is file line 217 / seq 213
(`RoadBuild`); Stenvik's pads are seq 286 (`SiteFinish`).** The town was laid out ON TOP of
an already-built road. Consequence recorded in `applied.py`'s own docstring: **89 of T4's
1,443 centreline metres sit on samples seq 286 overwrote, and the applied surface jumps
45.05 → 47.11 in one metre mid-carriageway.**

The fix was **routing, not demolition**: the re-route pulled 252 of our own pieces and 22
mod-house pieces out of the carriageway, **all 102 hive pieces intact, no building
removed**, T4 shortened 1525.1 → 1363 m. Four carriageway pieces remain (a
`More_World_Locations` house at (440, 397), 2 m of blocked span at 2.05 m lateral) and are
**recorded as accepted**.

**Rule for a fresh world: both pads go into the router's keep-out set BEFORE anything is
routed, and the keep-out is the union of the ACTUAL pad rectangles.** And a road must
ARRIVE: terminate on the settlement's own datum, not on terrain. MEASURED failures of that
rule — T4 stopped **10.4 m** below Stenvik's floor; a road terminated at road freeboard
**0.648 m** above a dock's `DECK_DATUM_Y` (+1.633 m three metres inland), a datum that
exists precisely so a join has no step; and `step_across_m -7.644` sat in a segment's own
metadata, recorded at build time, with nothing consulting it. At the harbour city the same
trap was caught **in the plan**: the unpinned profile landed at 36.478 against a junction
carriageway datum of **39.608**, i.e. **3.130 m below it**, so the terminal window was
pinned before anything was cut.

### 10.5 Prove the placer by prefab histogram, and mind the hole in the allowlist

§10.3 resolves provenance **geometrically**, because a town's pieces carry no
`spawn` records. That is still the right method and it has two measured failure
modes, both of which made this project's own numbers wrong on 2026-09-21.

**FOUR OF THE 14 "mod structures" WERE OURS**, MEASURED 2026-09-21. The largest
was the bridge `N0-brgs2-wmid-x01`, carried in `mod_keepout.json` as a **13.18 m
KEEPOUT** — so the router was told to avoid the crossing the road was built to
make. Which classification rule let a bridge through is `[UNMEASURED]`; what is
measured is that a proximity-shaped provenance test misfiled four of our own
bodies, and that the misfiling direction is the expensive one, because a
keep-out silently re-routes a road away from its own purpose.

**Rule: a placer must be proved by an EXACT PREFAB HISTOGRAM against the plan
that built it — never by proximity.** Proximity answers "is something of ours
near here"; a histogram answers "is this that body", and only the second can
refuse a bridge that is being nominated as somebody else's ruin.

**CLEARABLE HAS A HOLE, and it inflates a structure count.** MEASURED
2026-09-21: **131 pieces** in the corridor census are off
`roads/clear.py::CLEARABLE` **and are not structures
either**: `YggaShoot*`, `cliff_mistlands2`, `GuckSack`, `lox_ribs`,
`giant_sword1`, `Beehive`. The allowlist is deliberately default-deny
(`terrain-and-roads.md` §7.2) and that is correct for a REMOVAL decision; reused
as a structure test it classifies scenery as buildings. **Counting those 131 as
structures turned 14 real bodies on N4 into 60.**

So the two lists are not interchangeable: `CLEARABLE` decides whether a removal
may proceed, `roads/structures.py::classify` decides whether a thing is a
building, and `classify` raises on an unknown name precisely so a third
population cannot be invented by omission (§10.3). The road-side half of the
same day's population errors — what "184 objects left standing" actually
counted, and why 1,676 pieces in the road reduce to 641 that block a walker — is
in `terrain-and-roads.md` §7.8.

---

## 11. Doors and closable structures

### 11.1 How to prove a piece is a working door rather than scenery

**Dump the component list per prefab from a live `ZNetScene`** and read, per prefab:
`Piece`, `Door`, `Door.m_keyItem`, `Door.m_canNotBeClosed`, `ZNetView.persistent`,
`WearNTear`. Nothing else settles it. Suggestive evidence is not sufficient: the fact that
`sfx_open_portcullis_fb` and `sfx_close_portcullis_fb` are separately registered prefabs in
the same bundle **only means anything if something plays them on open/close**.

MEASURED 2026-09-18T03:28Z in a sandbox mirror of the Ulfsland install (live `BepInEx`
excluded; Jotunn + JsonDotNET + OdinShip + RavenwoodVikingHouses + RavenwoodRestorations
staged; `ZNetScene` **4,872 prefabs**):

| prefab | in ZNetScene | Piece | Door | `m_keyItem` | `m_canNotBeClosed` | `ZNetView.persistent` | WearNTear |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `fb_fortress_portcullis` | yes | yes | **YES** | `null` | `0` | `1` | **NO** |
| `fb_fortress_gates` | yes | yes | **YES** | `null` | `0` | `1` | **NO** |
| `fb_wood_door_01` | yes | yes | **YES** | — | — | — | — |
| all 10 `fb_fortress_wall*`, `fb_house_01…04`, `fb_viking_house_01…08`, `fb_shelter_01`, `fb_bridge_01/02/stairs` | yes | `1` | — | — | — | `1` | `0` |
| `mm_large_wall` | **NO** | — | — | — | — | — | — |

So: `fb_fortress_portcullis` and `fb_fortress_gates` are **real animated Doors with no key
requirement**, and they can be closed AND reopened. That measurement is what allowed a
11.946 m ship gate to be barred without sealing the harbour — the fallback, stated in
advance, was to *hang the panels clear of the navigable width and report the narrowing with
its number*.

### 11.2 `wnt = 0` cuts both ways

**Nothing in the `fb_` kit has `WearNTear`.**

- **Benefit:** nothing needs a structural footing, nothing collapses for want of support,
  nothing decays, nothing takes weather damage. That is what lets a 132 m curtain wall stand
  on the seabed with zero terrain writes (§8.5).
- **Cost, and say it plainly:** **a floating or buried segment will never announce itself.**
  With `WearNTear`, an unsupported piece falls or turns red and tells you. With `wnt = 0` it
  hangs in the air silently forever. **The grounding census (§3) is therefore a deliverable,
  not a nicety.**

### 11.3 The barred gate, as built

MEASURED: gate clear opening **11.946 m** (6 wall modules × 20.009 = 120.054 m + gate =
132.000 m against a 132.0 m line — it closes exactly). Water under the gate: min 2.704,
mean 3.789, **max 5.095 m**.

Barring: **6 × `fb_fortress_portcullis` in 3 columns × 2 rows**, panel 5.704 m wide, pitch
**3.982 m**, so panels **OVERLAP by 1.722 m and there is no residual gap at all**. The
barrier covers `y 25.46…38.564` — **4.54 m below the water plane** (deeper than
`CargoShip`'s 2.0 m draught) to **8.564 m above** it.

Hull admission is stated, never hidden. Fits (beam ≤ 11.95 m AND draught + 1.0 m keel
clearance ≤ 5.095 m): `CargoShip`, `MercantShip`, `WarShip`, `VikingShip`, `Karve`,
`LittleBoat`, `RowingCanoe`, `DoubleRowingCanoe`. **REFUSED: `BigCargoShip` — beam 14.10 m >
gate 11.946 m.** The only way to admit it is a 2-bay gate ≈ 31.96 m wide, *which is not a
gate that keeps raiders out*. So the refusal is carried as a **constraint on the berths**:
every A berth row carries `admitted_by_gate` and `berthable_here`, so `BigCargoShip` floats
at four A berths and is berthable at none of them.

---

## 12. Portals

### 12.1 Pairing is exact string equality plus a random draw

MEASURED from `TeleportWorld`: pairing is **exact string equality** against a **uniform
random draw among equally-tagged UNCONNECTED portals**. Consequences:

- **A tag with one end pairs with nothing.** The operator walks into the arch and stays
  where they are. MEASURED live: `x-harbour` (one end at `(16.45, 32.22, -253.55)`),
  `x-ferry-e` (one end at `(1957.84, 30.95, 553.54)`), `u-stenvik` (one end at
  `(-292.5, 36.7, 213.5)`).
- **The empty string is a tag like any other.** One blank portal at a site plus **26 blank
  `portal_wood` in mod world locations** = the destination was **one of 26 drawn at
  random**, re-drawn whenever either end loses its partner. Not merely one-way —
  **NON-DETERMINISTIC**.
- **Every tag must have exactly two ends. Verify it live, never from history.** MEASURED
  correction: counting `portal` ledger records per tag reported **19 of 20 tags as
  one-ended**. Nineteen of them had **three** records and **two live ends** — the third
  record was a relocation. Subtracting retirements gives the right answer: every tag is
  two-ended. **A ledger record is not live world state.**

### 12.2 The 10-character ceiling

MEASURED: `TeleportWorld::Interact` calls
`TextInput::RequestText(this, "$piece_portal_tag", 10)` — `ldc.i4.s 0x0a`. **This is the UI
INPUT cap, not a storage or pairing cap.** A longer tag written through `data=` pairs fine
but **can NEVER be retyped**, so a broken pair becomes unrepairable by hand. The tags
previously in `network/data/Ulfsland/network-tier4.json` — `uls-ironworksh` (14) and
`uls-trunk-centhub-mounshrine` (28) — were unrepairable and were changed.

**Keep every tag ≤ 10 characters.** And note a tag is not a place name: `x-ferry-e` is a
tag, "East Isle Ferry" is what a sign must say.

### 12.3 A portal's tag is invisible beyond five metres

MEASURED from `assembly_valheim.dll` 1.0.12: **`TeleportWorld::GetHoverText` is the ONLY
place a portal's tag is ever shown**, and `Player::FindHoverObject` resolves a hover only
when

```
Distance(eye, hit.point) < m_maxInteractDistance + hoverable.GetHoverOffset()
```

with `m_maxInteractDistance` initialised `ldc.r4 5.` in `Player`'s own constructor. **Five
metres, crosshair on the arch. Beyond that a portal is anonymous.**

The defect this explains: **the operator spawned 3.54 m horizontally and 1.09 m below a
CONNECTED portal tagged `u-workshop` and reported that they could not find any portals.**

Corrections the measurement forced on the brief, recorded because they are the numbers:

| brief claim | MEASURED |
| --- | --- |
| "18 tagged portals forming 9 exact pairs" | **33 `portal_wood` ZDOs; 22 tagged forming 11 exact pairs; 11 blank-tag.** Every tagged pair shares exactly one ZDO connection hash, types {17 source, 1 target} — all 11 genuinely CONNECTED |
| "12 blank-tag portals" | **11**, sharing 5 distinct connection hashes plus some unpaired — several had paired with each other at random, exactly as predicted |
| "a new character spawns roughly 7 km from the hub" | **4,413.2 m** straight line from the pre-bonemass template spawn (−4674, 72, −329) to the hub centroid (−294.7, 217.5) |
| "the hub is 340 m from the start temple" | **314.3 m** from `StartTemple` (−64.68, 3.31), bearing 312.7° (NW) |

`settlements/portal_hall.py` moves **18 hub ends** into a built hall. Before it, MEASURED:
the 18 hub-end `portal_wood` stood on UNLEVELLED GENERATED GROUND over
`x −309.5…−274.5`, `z 196.5…239.5` — a **35 × 43 m scatter** whose recorded Y values run
**32.63 to 40.26, a 7.6 m spread** — with **95 trees, bushes, rocks and pickables inside the
same 26 m cylinder** and no floor, wall or roof anywhere. *"The portal hub just seems to be
a bunch of portals spread throughout the woods"* was a literally accurate description.

### 12.4 The fix was signage, and the refusals matter

**DO NOT move the spawn, do not add a spawn portal, do not shorten the walk.** All three
were already satisfied and none of them was the defect: the spawn stood 3.54 m from a live,
paired portal to the hall, and *"a second portal at the spawn would have been a second
invisible arch."*

What to build instead: **a guidepost beside every portal naming its destination**, a hall
board at the portal-hall door, and a pointer at the `StartTemple` reading
`Sandbox Portal Hub / NW 310 m` (the one genuine gap — a respawn without a bed appears there
with nothing pointing at the hall 314 m away). **Cost: 24 boards + 24 posts = 48 pieces,
0.52% of the pre-bonemass tier's 9,290 pieces.** No terrain write, no clearing, no flatten,
no new tags, no change to any existing portal.

### 12.5 The guidepost offset is SOLVED, not declared

The first version used a constant **2.9 m**, derived from `portal_wood`'s **axis-aligned**
half-width of 2.14 m. A verification pass measured the post **INSIDE the arch at yaw 45, 225
and 315** — three of the yaws this world's own placements use
(`meadows-starter-hall` 225, `plains-farm-base` 315) — because `fixtures.fixture_box` builds
the world AABB of the yaw-**ROTATED** corners, and at 45° the arch's x-extent is **1.91 m,
not 0.59 m**. `network/waypoints.py::portal_guidepost` now walks the post outward against
the same box builder `fixtures.audit()` uses, so the two cannot disagree; re-verified across
16 placements.

`portal_wood`'s AABB starts at **y −0.007**, so **its origin IS its foot** — the arch's Y and
the surface the post is driven into are the same surface. That is an assumption about the
CALLER's placement, which is why the function takes the arch's transform and not a bare
coordinate.

### 12.6 Arches on natural ground: level the footprint or don't place it

Where there is no pad (no terrain write permitted), `settlements/pair_orphans.py` caps
`FOOTPRINT_LEVEL_M = 0.621` — corner-to-corner spread of the generated ground under one
arch's own footprint. The number is **the best standing arch**, not a pick: over the sixteen
reserved cells the spread runs **0.621 m** (`u-wtpeak`, most level) to **2.467 m**
(`u-treewest`), median **1.610** (`u-stenvik`); across the 2,246 land cells of the 12–30 m
annulus the median is **1.626 m**. **44 annulus cells qualify, 15 clear of the reserved
cells** — a real constraint with real room. Other constants there:
`CLEARANCE_PAD_M = 0.5` (reused from `fixtures.free_spot`'s own default, deliberately: *two
gates with two margins is how a placement passes one check and fails the other*),
`COMFORT_CLEARANCE_M = 1.0` applied as a **preference tier, not a veto** (*a 0.08 m gap to a
Beech is a portal in a hedge*).

**Cached spots are extended, never re-solved.** `fixtures.free_spot` is order-dependent — it
returns the nearest legal cell to `prefer` that no box in `taken` occupies — so a fresh
16-tag solve with a different `taken` list would legitimately reassign ends that are already
standing and already in the ledger.

---

## 13. The ordered procedure: siting and building a settlement on a fresh world

Preconditions: a world rolled on a known seed string; RCON reachable; the ledger in place.
**Every write goes through the `consoleCommand` bridge** — ValheimRcon's verb registry is
closed over its own assembly with no console fallback, so a bare verb is rejected by design.
An hour was lost reading `Unknown command` from a hand-written probe as a regression in a
healthy world with 443 registered commands; `terraform/place.py:33` and
`blueprints/to_rcon_plan.py:65` already carried the warning.

### Phase A — establish the vocabulary (BUILD-derived; do this once per mod set)

1. **Dump collider geometry.** `blueprints/run_piecegeometry.sh` →
   `blueprints/data/piece_geometry.json`. Solids only; triggers and render meshes dropped.
   Record the game version in the file.
2. **Climb the registration ladder (§5) for every prefab you intend to place.** Bundle
   index → `#US` heap + IL call sites → live `ZNetScene` dump → `spawn_object` + read-back.
   Do not skip rung 4 for anything load-bearing. Never use `strings` over an asset-bundle
   mod; never trust a prefix filter; print the population size beside every result.
3. **Dump components per prefab** for every door, gate, portal and station: `Piece`,
   `Door`, `m_keyItem`, `m_canNotBeClosed`, `ZNetView.persistent`, `WearNTear`. Record
   `wnt = 0` kits explicitly — they will never announce a floating segment.
4. **Build the per-prefab seat table (§1).** Extent, pivot offset inside the extent
   (`ox`, `oz`), lowest STRUCTURAL solid (shortest solid in Y for fire pieces), walkable
   platform Y for anything a player stands on top of, clear openings from BOX colliders.
   **Inherit nothing.**

### Phase B — survey the seed (SEED-derived; survives everything)

5. **Raster the candidate region** with PatchScan: a coarse context patch plus 1 m patches
   over each candidate site, **lattice-aligned** (record `x0`/`z0`, e.g. centre (1080,700)
   half 250.5 → x0 830.0 z0 450.0).
6. **Dump locations mod-free** (`tools/seedscan/run_locscan.sh`) for per-type
   `exteriorRadius` / `interiorRadius` / `znviewReachM`. **Do not read `placed`** — it is
   false on all 12,301 records (§8.8). Compute per-type stand-off as
   `max(declared, measured)` + labelled margins (§8.7).
7. **Measure the water body properly, against `y = 30.0`.** Cross-sections; depth
   percentiles; **flood-fill connectivity to open sea** per required draught; the navigable
   bottleneck. Never use `h > 0` as a wetness test.
8. **Test any closing line topologically**, not by span (§8.4). Extend 10 m onto dry ground
   at each end.
9. **Census pads at several sizes** against the ±8 m clamp with a 1 m margin (§8.2–8.3).
   The answer decides terraces versus a platform. Expect 16/24 m to fit and 48 m to fail
   ~40% of the time.
10. **Verify biome coverage by ring**, land-only, out to the district radius.

### Phase C — reconcile with what already exists (SAVE-derived; re-take after any restart)

11. **Query the live `LocationProxy` set** at each site: `findObjects -prefab LocationProxy
    -near <x> 30 <z> <r>`, **bounded** so it does not trip the log-sink guard. Cross-read
    against the mod-free dump by exact position (0.5 m) for identity; an unidentified marker
    gets the world's worst case. This is the only instrument that sees the ~190
    `More_World_Locations` POIs. **It gates the first write.**
12. **Read the applied surface** (`roads/applied.py` over the ledger's own `terrain_write`
    blobs) for every road and pad near the site: does a carriageway already arrive, and at
    what datum? MEASURED at the harbour: nearest carriageway `road_T9_wttown_dockshore` at
    **200.9 m**, nearest node `wttown` (820, 738) pad radius 12 m, `pad_y` 39.86.
13. **Census our own builds** nearby, deduped by POSITION.

### Phase D — design offline, with refusals counted

14. **Solve every position. Hand-pick nothing** (§8.1). Test each candidate against the
    real footprint shape — a pad rectangle at its own yaw, a hull's ellipse.
15. **Lay out quarters as rectangles with a facing yaw**, fill greedily largest-first, and
    **cap per prefab** (§9). Count refusals by cause: `clamp`, `wet`, `collide`, `keepout`,
    `offpatch`.
16. **Feed every pad into the router's keep-out set BEFORE routing anything** (§10.4), as
    the union of actual pad rectangles, never a district radius.
17. **Route the roads, and pin each terminus to the datum of the thing it meets** — a
    settlement's `pad_y`, a dock's `DECK_DATUM_Y` — not to terrain and not to road
    freeboard. Check corridor separation against the shared-corridor width (MEASURED 29.0 m
    = trunk reach 6.0/2 + shoulder 1.0 + batter 10.5, per side 14.5 m) before adding a
    parallel ribbon; prefer a junction.
18. **Prefer structures over earthworks near water.** If a wall or pier can stand on the
    seabed with freeboard, write no terrain — a write below the water plane is the one
    unrepairable damage class (§8.5).
19. **Write the plan out and check it arithmetically before sending anything**: the spanned
    extent versus the intended extent (§7.2), the grounding census versus the pad (§3), the
    per-prefab counts, the total piece count.

### Phase E — build, in this order, per site

Order from `tools/jumpstart/SITE_PREPARATION_ORDER.md`. Steps 0 and 1 are measured, not
reasoned.

```
0. GENERATE THE ZONES    clearing/        (Upgrade World zones_generate)
1. CLEAR VEGETATION      clearing/        (Upgrade World objects_remove)
2. FLATTEN TERRAIN       terraform/flatten.py
3. PLACE THE STRUCTURE   terraform/sites.py -> blueprints/to_rcon_plan.py --align floor-center
4. PLACE LOOSE OBJECTS   terraform/stock.py
```

20. **Take a backup before the first write**, and a ledger token. MEASURED example:
    `config_merged/backups/worlds-HutsInRoad-pre-reroute-20260917T100951Z.tgz`, 4,130,400
    bytes, sha256 recorded in the ledger.
21. **`zones_generate` FIRST. This is a hard precondition, proven by deliberately breaking
    it.** With no players online the site's zones are not generated, so its trees and rocks
    do not exist as ZDOs and a delete-only clearing step has nothing to delete (MEASURED: a
    fresh world had ghost-generated only 81 zones, every `_ZoneCtrl` at
    (1000000, 0, 1000000), and zero vegetation in the save). Worse — MEASURED on Ulfsland
    2026-09-15, zone (−18, 28) at (−1152, 1792): a `_TerrainCompiler` carrying a valid
    40 × 40 m flatten to y = 31.40 was spawned into an ungenerated zone, and the zone was
    then generated. **All 190 objects it planted inside the 30.28 m cylinder landed on the
    UNMODIFIED generator height** — median offset from the patchscan height 0.000 m, worst
    0.42 m — which stood them **up to 7.68 m above the finished pad, as real ZDOs,
    permanently.** Paint alpha was ignored in the same breath: `Bush01`, `RaspberryBush`,
    `Beech1`, `Pickable_*` and eleven more were placed on ground painted alpha 0.
    Mechanism: `Heightmap::Generate` → `ApplyModifiers` → `TerrainComp::FindTerrainCompiler`
    scans `TerrainComp::s_instances` of **instantiated** components; a dedicated server with
    no peers instantiates none, so neither height deltas nor the cleared mask reach the
    heightmap, while `ZoneSystem::SpawnZone` runs `PlaceLocations`/`PlaceVegetation`/
    `PlaceZoneCtrl` against it in the same call. `flatten.py apply` therefore REFUSES to
    write into a zone with no `_ZoneCtrl` at its centre, and refuses **before** writing even
    the data-entry file. The test is exact: `PlaceZoneCtrl` plants `_ZoneCtrl` exactly at the
    zone centre, and `objects_count`'s `pos`/`max` filter is a vertical cylinder on
    `Utils.DistanceXZ`, so a 1 m probe at the zone centre is a two-line generated/not test.
22. **Clear vegetation.** `objects_remove id=* ignore=_*` already takes every prefab
    `ZNetScene` knows except `_`-prefixed ones, so nothing needs adding to the list
    (MEASURED at an uncleared pad: all 22 prefab classes present were classes that cylinder
    would take). **Where a bridge, building, mod house or portal is anywhere near, use an
    EXPLICIT vegetation prefab list from a live census of the exact cylinder, never `id=*`**
    — MEASURED on the portal orphan ring (`settlements/pair_orphans.py`): its zones include
    (−5, 4), which carries Crossings' S2 bridge declared `flatten: FORBIDDEN`, and an
    `objects_remove` at ring radius would have deleted **45 of its pieces**. The portal hall
    (`settlements/portal_hall.py`) uses the same explicit-list rule and additionally probes
    all **156** piece positions of `bridge-s2-portalhall-strait` — read out of its own
    `spawn_plan` blob — with `ribbon.delta_at` against the bytes about to be written; the
    nearest is **15.2 m OUTSIDE** the written region, so every probe is exactly **0.0** —
    not "small", zero in float. MEASURED and equivalent: clearing before or after the
    flatten makes no difference (both orderings left exactly 2 objects — `_TerrainCompiler`,
    `_ZoneCtrl` — in the 30.28 m cylinder), because the removal filter has **no height
    bound**: an object 7 m in the air over a cut pad is as removable as one on it. The
    diagram keeps clearing first only because `zones_generate` lives there and the flatten
    refuses to run until it has.
    **Any guard that narrows a request rather than refusing it MUST record what it narrowed
    and by how much** — MEASURED failure: a blocked removal disc was shrunk
    CONCENTRICALLY, so a blocker near its centre erased it; a 12.57 m disc became 3.84 m,
    removed 4 objects, abandoned 14, and recorded **no skip reason because nothing was
    skipped**. A shrink is silent.
23. **Flatten to the pad target.** `flatten.py` levels to
    `solved.flatten_cost.target_y` = the median terrain height over the footprint. **That
    number is the PAD HEIGHT and everything after is relative to it.** Do not read
    `solved.y` as a pad height — it is the HIGHEST terrain cell in the footprint, from the
    site search. Union each zone's prior blobs per sample (later claim wins), and set
    `tcdata.Compiler` `operations = 2` with `m_lastOpPoint` = zone centre at pad height and
    `m_lastOpRadius = 32 m` — MEASURED: `operations = 1` with zeroed op point makes
    `TerrainComp::CheckLoad` take the narrow branch (`m_operations == before + 1`) and
    `ResetGrass` a **zero-sized box at the world origin**, so the pad's grass is never
    invalidated and floats, recurring on every reload because `m_lastDataRevision` restarts
    at 0 on each newly instantiated component.
24. **Place the structure**, `--align floor-center`, `placement_y = target_y - base_y`.
    Emit bottom-up in Y so `WearNTear` support exists below each piece as it appears (this
    matters for vanilla kits; the `fb_` kit has none). **Batch by wire size, not by taste:**
    MEASURED, `deleteObjects`/console echo is **155 B per object**, so 64 pieces = **9,920 B
    = 15% of the 65,536 B pipe**; the record that wedged the sink was 1,255 commands ≈
    **194 KB** with no drain window inside it, and 34 consecutive removal records at ~105 KB
    had wedged the main thread before. Keep each round trip ≈ 1,800 B against the 4,096 B
    reply buffer (~45%, not ~88%). Probe the sink before **every** record. MEASURED on a
    1,255-piece body: 20 records, 44 gates, 5 drains, every drain the documented
    `circular_wait` (supervisord `unix_wait_for_peer` + syslogd `pipe_write`) released in
    ~1.2–1.3 s by a sustained host-side read — **no SIGKILL, no restart, the game process
    never signalled.**
25. **Place loose objects last**, at `target_y` — a ground-resting prefab's solid starts at
    or within 35 mm of its own pivot (§1.2), so a prop spawned at pad height rests on the
    pad, and aligning the blueprint's lowest walkable floor to the same height makes the
    building agree with the props beside it.
26. **Place portals and doors, then sign them.** Tags ≤ 10 characters, exactly two ends per
    tag, no blank tags ever. **A guidepost beside every arch naming its destination**, solved
    against `fixtures.fixture_box` with the arch's yaw — never a constant offset (§12.5).
27. **`save`, with a verified postcondition**, not an exit code. MEASURED counter-example:
    `update --apply` returned `rc=0 / updated=1` while the pin never moved, because the
    download timed out and the failure was swallowed. **Verify the artefact, never the exit
    code.**

### Phase F — verify what the operator will see

28. **Per-piece probes, not a box census.** Probe all N planned positions individually:
    expect N standing, 0 missing, 0 duplicates. A y-blind cylinder over a stacked prefab
    reports `absent` for something standing, and a census blind above one level produced
    **64 duplicates** (§3.5).
29. **Exact per-prefab counts inside a radius the count can be exact in** —
    `build.check_radius`'s gap-midpoint radius, because `objects_count`'s comparison
    MEASURED 2 of 4 pieces at exactly 1.000 m as inside. Then strays at a wider radius,
    **minus the foreign objects the pre-build census already measured in the annulus**, so a
    neighbour's pile head is not reported as a flung piece.
30. **Run the grounding census (§3)** and read the SUNK / OVERHANG split, not the bare
    fraction. The gate is `floating_near_ground_columns == 0` (overhang ≤ 4.0 m above pad).
31. **Read back heights off the ZDOs**, one row each with a 0.25 m cube: roof cap, gallery
    platform, entry floor, every beacon's plinth engagement. Print
    `false_air_gap_if_union_used_m` beside every beacon.
32. **Run the carriageway census** (`roads/structures.py`) over the WRITTEN segments — read
    off the ledger's own `terrain_write` `role=road_segment` records, **not the plan** — and
    classify by band. Accept what you accept, with its numbers.
33. **Verify both portal ends of every tag live**, against the world, not the ledger.
34. **Append every measurement to the ledger, never edit a record.** Corrections are later
    appends; annotate your own defects with their causes. `observe` rows begin with
    `MEASURED` or `INFERRED` and name their instrument.

---

## 14. The refusal register: what was tried and did not work

Each of these cost real time. They are recorded so the next agent does not buy them again.

| refusal | number |
| --- | --- |
| seat on a mesh AABB | tower beacons **5.173 m** in the air (15.402 mesh vs 10.229 platform) |
| seat on a union AABB | `bonfire` union reaches **2.130 m below its pivot**; would have reported **1.645 m of air** where engagement was **12 mm** |
| inherit a pivot offset between visually identical prefabs | **0.602 m** of pure authoring difference |
| centre a pad on a pivot | `fb_house_01`'s pivot is 0.583 m / 4.803 m off centre — a third of the building off the platform |
| read a gate opening off a mesh row | 20 m envelope reported; real clear openings **3.429 m** and **6.851 m** |
| size a slip from a `mesh_render` hull bound | 11.64 × 35.43 m read for a 9.16 × 21.57 m hull — 60% oversize |
| hand-pick a boat bay | shallowest sample **−0.755 m** — dry land under a boat bay |
| test depth at a point | berth read **3.657 m** at the point while the hull's ellipse touched **1.30 m short** |
| pick a wall line by shortest span | z = 728/730/732 have the shortest spans (84.0/85.0/88.0 m) and **do not close the harbour** — a spit with water on both sides |
| one large levelled plaza | 48 m pads fail the ±8 m clamp **40%** of the time at this site |
| greedy largest-first fit with no per-prefab cap | **ten identical 26.071 m halls** |
| trust a clearing audit for buildings | **18 objects network-wide, not one a building**, over a 102-piece defect |
| trust an applied-surface audit for buildings | a roof overhanging a lane makes **no terrain claim** |
| trust the router's keep-out for our own bodies | `poi_within_keepout: []` with the centreline **0.10 m** from a cottage centre |
| `strings` over an asset-bundle mod | **95** phantom piece removals, all compression noise |
| guess a prefab-name prefix over a bundle | **31** hits of 55,789 strings, all noise; the index holds **94** |
| treat a package instrument as a runtime instrument | **53** DLL strings, **29** registered |
| search a live dump for the wrong prefixes | `mm_`/`RW_`/`jj_` searched; the live family is `fb_` |
| wait on frame counts | a clean, fully-populated TSV with **every** prefab `znet=0`, including one standing in the world |
| trust a version bump as evidence code moved | 7.7.7 and 7.7.8 are **byte-identical** (md5 `7a85c9fc…`) |
| fix a renamed field by name alias | `AmbiguousMatchException` — two 1.0 fields share the name |
| remove one of four broken carriers | **chain abort**: one survivor keeps `UpdateAvailable` dead for all ten copies |
| `--align floor-center` with `--rotate 180` | centring offset doubled instead of cancelling — pieces **~40 m** off |
| split `.vbuild` on whitespace runs | **1,615 of 10,686** pieces silently discarded, 10,152 m footprint |
| keep the current section across an unknown blueprint header | would have emitted **`spawn circle`** at a live server |
| read `plan.json` locations as placed | **12,301 entries, all `placed: false`**; resolution off by 20–74 m |
| read `pad_radius_m: 100.0` as a footprint | a road refused to write for **294 m** and left a **10.4 m** step |
| a single global location stand-off | 43 + 177 m against 12,301 instances clears **ZERO cells** in the whole world |
| `Renderer.bounds` on an un-instantiated prefab | **528.7 m** reach reported against a **5.0 m** real one |
| a bounded neighbourhood census as a per-marker reach | **57.64–71.45 m**, LARGER than the 32 m it was meant to refine |
| two dumps agreeing | identical 12,301/177/hash because one instrument's `Awake` had thrown |
| a sandbox with six of 121 DLLs | "439 pieces on one category id" — an artefact |
| shrink a blocked disc concentrically | 12.57 m → 3.84 m, 4 removed, **14 abandoned, no skip reason recorded** |
| count ledger records as live portal ends | **19 of 20 tags** reported one-ended; all 20 were two-ended |
| add a portal at the spawn to fix "no portals" | a live paired portal already stood **3.54 m** away — the tag is invisible past **5.0 m** |
| a constant guidepost offset | 2.9 m from a 2.14 m axis-aligned half-width put the post **inside** the arch at yaw 45/225/315 |
| a bare RCON verb without the `consoleCommand` bridge | `Unknown command` read as a broken world; **443 commands** were registered |
| trust an exit code | `rc=0 / updated=1` while the artefact never moved |
| rely on a global surviving between eval cells | the Python eval kernel is **SHARED between subagents**; two agents had globals silently rebound mid-measurement. Anything load-bearing runs as a standalone script in ONE invocation |
| let two sub-builders each emit a shared boundary element | **52 duplicate pieces** standing at Ulfsgard: 20 `wood_floor`, 19 `wood_pole_log_4`, 13 `wood_roof` (§3.6) |
| detect duplicates with a cylinder | `objects_count` has no height bound and reads a legitimate pile stack as a duplicate; the cube probe read 4 at r=0.4 and 2 at r=0.1 where two stacks sit **0.373 m** apart in y (§3.6) |
| level a pad to a `plan.json` figure | `wt-peak` written to `target_y 102.31 m FROM PLAN.JSON`, body anchored to the same number, seq 545/546 (§8.8) |

---

## 15. The open item this document cannot close

Three subjects here are about what a player PERCEIVES — road lighting, the build-menu grid,
and which tabs exist — and no agent in the producing session had a client session. Every
claim about them is read off prefab assets, IL and object counts. **One night walk and one
hammer-open closes all three.** That is not a gap in the measurements; it is the boundary of
what this kind of measurement can reach.
