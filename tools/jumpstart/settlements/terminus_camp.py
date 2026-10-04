#!/usr/bin/env python3
"""The shelter at the northern road terminus: a bed, a fire and a roof beside
the `u-nterm` arch, on POSTS, with ZERO terrain writes.

WHY IT EXISTS.  The northern trunk road ends at (-846, 7730) and `u-nterm`
lands a player at (-845.50, 33.556, 7730.00) after a 7,535 m hop.  The
terminus was bare Mountain: `objects_count id=* at (-846, 7730) r=20` answered
19 objects and ZERO built pieces, so an arriving player had no shelter, no fire
and no bed, and death there returned them 7.5 km south.

-----------------------------------------------------------------------------
1. THE SPAWN QUESTION, SETTLED BEFORE THE DESIGN
-----------------------------------------------------------------------------
`BedRules` 2.0.6 is installed with `doNotSetSpawnPoint = true`, server-synced,
which reads as "a bed here cannot claim a spawn" and would have made this whole
build pointless.  It does not mean that.  MEASURED by `monodis` on the live
`BedRules.dll` (60,416 B, 2026-09-12, `/config/bepinex/plugins/BedRules/`) with
BepInEx, 0Harmony, assembly_valheim, assembly_guiutils and
UnityEngine.CoreModule made resolvable:

`HarmonyPrefix` on `Bed.Interact`, priority 600, two branches --

  BRANCH A  modEnabled && doNotSetSpawnPoint && Input.GetKey(304 = LeftShift)
            && Input.GetKeyDown(claimkeybin = E)
            -> the five gates, each with its override (IsAfternoon||IsNight||
               sleepAnyTime, CheckEnemies||ignoreEnemies, CheckExposure||
               ignoreExposure, CheckFire||ignoreFire, CheckWet||ignoreWet),
               then `Character.AttachStart(m_spawnPoint, ..., "attach_bed",
               (0, 0.5, 0))` and `return false`.
               SLEEP.  Never touches the spawn point, never checks ownership.

  BRANCH B  plain E, three states --
            GetOwnerName() == ""      -> SetOwner(playerID, name).  Claim only.
            IsMine() && !IsCurrent()  -> Game.instance.GetPlayerProfile()
                                         .SetCustomSpawnPoint(Bed.GetSpawnPoint())
                                         + "$msg_spawnpointset".
                                         THE SPAWN CLAIM.
            IsMine() && IsCurrent()   -> sleep, via the same five gates.

So `doNotSetSpawnPoint` does not DISABLE spawn-setting, it SEPARATES it from
sleeping, and the ladder is THREE presses of E, not one: claim, set spawn,
sleep -- with `LShift+E` sleeping without ever setting a spawn.  Corroborated
independently by the mod's own `Bed.GetHoverText` prefix, which in this exact
config emits `"\\n<color=yellow><b>Spawn Point Bed</b></color>" `when
`IsMine() && IsCurrent()`: a mod that suppressed spawn points would have no
such state to render.  `sleepinclaimedbeds = true` shows up as Branch A
checking no ownership at all.

TWO CONSEQUENCES THAT SHAPE THIS FILE.

  * THE GATES ARE ALL OFF.  `ignorExposurRestrictions`, `noFireRequirements`,
    `ignoreNearbyEnemies` and `Ignoretimerestrictions` are true, and the
    spawn-set path has NO gates whatsoever -- it is reached directly off
    `IsMine() && !IsCurrent()`.  Walls, a roof and a fire are therefore NOT
    mechanically required for sleeping or for claiming a spawn on this fleet.
    They are here for the cold, the dark and the wolves, and this module does
    not claim the enclosure satisfies a requirement that is switched off.
  * THE SPAWN CANNOT BE PRE-CLAIMED.  Spawn-set runs in a prefix on the
    ARRIVING PLAYER'S OWN `Input`, client-side.  Nothing placed server-side can
    press E for them.  This build delivers a bed that CAN claim a spawn,
    verified by mechanism; it cannot deliver an already-claimed spawn.  The
    operator instruction is: press E on the bed twice.

A DISASSEMBLER THAT CANNOT SEE IS NOT A DISASSEMBLER THAT FOUND NOTHING.
Without those reference assemblies `monodis` emitted `Bed.Interact` as
"Code size 592 (0x250)" with an EMPTY body and a BROKEN CLASS token -- a
confident negative produced by an instrument that could not read, which is the
same shape as a native verb routed through the wrong transport returning zero
rows.  Only the second pass is trusted here.

-----------------------------------------------------------------------------
2. THE SEAT: SOLVED OVER POSITION, YAW *AND* CONSTRUCTION
-----------------------------------------------------------------------------
Instrument: `roads/applied.py::Applied.surface_at` over `flatten.run_patchscan`
generated heights -- the APPLIED surface, not the delta.  Bootstrapped BOTH
directions before it was trusted: the arch seat reads applied 33.55600 /
delta +0.09768 (PRESENT, and equal to the arch's own published 33.556), while
(-815, 7730) and (-846, 7760) read delta exactly +0.00000 (ABSENT).

WHAT THE BRIEF SAID AND WHAT THE GROUND IS.  The site was handed over as "a
wide, flat bench -- 65 seats inside +/-8 m at five yaws all came back spread
< 0.05 m".  MEASURED, that is true of the ARCH's 1.179 x 4.234 m footprint and
false of the site: the exactly-level ground is a **29-sample lozenge** written
by `RoadNorthLand` seq 2754 `road_N4_mid2_terminus` (role `road_segment`), and
the arch stands in the middle of it --

    z=7726  x -846..-846      z=7731  x -846..-843
    z=7727  x -846..-844      z=7732  x -846..-843
    z=7728  x -846..-843      z=7733  x -846..-844
    z=7729  x -846..-843      z=7734  x -846..-846
    z=7730  x -846..-842      ... all 29 at applied 33.55600, author seq 2754

A 1.18 m-wide footprint anywhere in a 5 m-wide strip has all four corners in
the strip; that does not make the strip a bench.  Off the lozenge the terminus
is a spur crest falling ~0.7 m per metre of z.

NO ZERO-SPREAD SEAT EXISTS WITHOUT A WRITE.  40,704 candidates over 9
footprints x 4 yaws x a 0.5 m grid: **zero** achieved exact 0.0000 m spread.
The best was 0.0001 m, and it is a knife edge rather than a seat --

    8 x 4 yaw 90 at (-855.00, 7730.0)   spread 0.0001 m   <- best of 40,704
      nudged to cx -855.25              spread 0.0125 m
      nudged to cx -854.75              spread 0.0148 m
      nudged to cz 7731.0               spread 0.7814 m
      nudged to cz 7729.0               spread 3.8016 m
      same seat at yaw   0              spread 0.1252 m
      same seat at yaw  45              spread 1.1528 m
      same seat at yaw 135              spread 1.1239 m

The yaw ranking reproduces the arch's own story (yaw 0 spread 0.2922 -> yaw 90
spread 0.0000) for the same reason: the road pad is a longitudinal profile, so
it is EXACTLY constant in z and varies only in x, and yaw 90 reduces the
spread to the difference between two x samples.  At x = -855.0 those two
samples happen to agree to 0.11 mm.  Move 0.25 m and it is gone.

REJECTED, EACH WITH THE NUMBER THAT REJECTED IT.

  (a) A PAD ON THE ROAD BENCH, WEST OF THE ARCH.  Cheapest earthwork of all
      (the pad itself needs <= 0.32 m of cut) and it was refused anyway: an
      8x4 pad at (-853, 7728) re-authors **66 of its 77 samples** away from
      `road_N4_mid2_terminus`, and 8x8 re-authors 110 of 121.  That is the
      stenvik-beehive clobber exactly -- a settlement laid on top of a
      finished road, which `roads/applied.py` exists to detect.  Its 1 m apron
      additionally cuts 4.154 m into the road's own cut face at z=7725.
  (b) A PAD EAST OF THE ARCH, ON THE SPUR.  Zero road clobber, but the relief
      is Mountain: with the target FREE (solved, not assumed) the cheapest of
      193 candidates within 12 m is **2.240 m** of worst cut-or-fill, and with
      the target pinned to 33.556 the cheapest is 2.234 cut / 2.711 fill.
  (c) A 6x6 OR LARGER BODY ANYWHERE NEAR.  Relief over the footprint scales
      with its z-extent: 6x4 -> 0.081 m, 6x6 -> 0.081, 6x8/6x10 -> 2.582.  The
      spur crest runs E-W and is ~2 m wide in z.

CHOSEN: A POSTED BODY, FLOOR AUTHORED AT ONE Y, ZERO TERRAIN WRITES.
`docs/manuals/structures-and-settlements.md` §8.5 -- "a structure can stand on
the seabed instead of a terrain write" -- applies to a mountain spur unchanged.
Corner spread is then **0.0000 m by construction**, because the floor is a
single authored Y rather than a reading of the ground, and it is the only
option that CANNOT disturb the arch: there is no write to disturb it with, so
PortalTriage's hazard #2 (ground moving under a pivot-at-foot arch) and the
`flatten: FORBIDDEN` site_id interaction both cease to exist rather than being
managed.

    footprint     6.0 (x) x 4.0 (z), yaw 0, centre (-839.50, 7732.50)
                  x -842.50 .. -836.50    z 7730.50 .. 7734.50
    floor top     33.556
    clear of arch 2.263 m in x (arch AABB ends x -844.763)
    terrain       ZERO writes, ZERO samples re-authored, ZERO objects removed
    posts         12 columns, ground 30.700 .. 33.532, post 0.024 .. 2.856 m

AND THE FLOOR HEIGHT IS NOT A TASTE PICK.  The minimum floor that needs no cut
at this footprint is max(node ground) = **33.53195**.  The chosen 33.556 is
**0.024 m** above that floor and is simultaneously the arch's own datum, so the
cheapest possible floor and the arrival datum coincide to 24 mm -- which is
§10.4's arrival rule ("terminate on the settlement's own datum, not on
terrain") satisfied by arithmetic rather than by compromise.

-----------------------------------------------------------------------------
3. THE FIRE, AND WHY IT IS IN AN OPEN PORCH RATHER THAN INSIDE
-----------------------------------------------------------------------------
The player arrives OUTSIDE, at the arch.  A fire inside four walls lights the
interior.  So the west 2 m of the body is a roofed porch, open west and north,
with the fire in it and the bed in the closed room behind.

MEASURED light ranges, `roads/LampProbe.cs` via `run_lampprobe.sh` (235 rows,
prefab asset values read inside a loaded game process -- these are serialized
asset values, not in the managed assembly and not derivable from the name):

    prefab              lights  high range  low range  pivot/mesh y
    piece_groundtorch   1       15.0 m      --         -0.65359 .. 0.82260
    hearth              2       13.0 m      3.0 m      -0.05005 .. 0.75307
    fire_pit            2       10.0 m      2.5 m      -0.09116 .. 0.33329
    bonfire             2       20.0 m      20.0 m     -0.50952 .. 4.14864

WHICH OF THE TWO LIGHTS IS ON IS DECIDED BY `m_wet`, NOT BY FUEL.  MEASURED in
`Fireplace::UpdateState` IL: when `IsBurning()` and both `m_enabledObjectHigh`
and `m_enabledObjectLow` exist, it is exactly
`High.SetActive(!m_wet); Low.SetActive(m_wet)`.  The fuel half-threshold is
computed into a local and then POPPED -- it drives the fuel VISUALS, not the
lights.  `Fireplace::CheckWet` sets `m_wet` from
`Cover.GetCoverForPoint(pos + up*m_coverCheckOffset)`: wet if windy and
cover < 0.7, or if `EnvMan.IsWet()` and not under a roof.  Hence the porch
roof: it is what keeps `fire_pit` on its 10.0 m light instead of its 2.5 m one
through a Mountain blizzard.

`piece_groundtorch` has `m_enabledObjectHigh` and `m_enabledObjectLow` BOTH
null, so that whole branch is skipped and its single 15.0 m light is on
whenever it burns, wet or dry.

AND A FIRE CAN BE SWITCHED OFF BY GEOMETRY, WHICH IS A GROUNDING CONSTRAINT
WITH TEETH.  `Fireplace::CheckUnderTerrain` sets `m_blocked`, and
`IsBurning()` returns false at the first line if `m_blocked` -- so a badly
seated fire emits NOTHING.  It blocks when any of:

    Heightmap.GetHeight(pos) > pos.y + m_checkTerrainOffset   (buried)
        fire_pit 0.20   hearth 0.40   piece_groundtorch 0.57
    Physics.Raycast(pos + up*m_coverCheckOffset, up, 0.5, solid)  (capped)
        fire_pit 0.50   hearth 0.70   piece_groundtorch 0.76
    SmokeSpawner.IsBlocked()

Both distances are checked for every flame this module places; see `fires()`.

A CORRECTION TO THE INHERITED "NAKED IT BURNS OUT".  `IsBurning()` never
consults fuel, and `m_canTurnOff` is **false** on all of `fire_pit`, `hearth`,
`bonfire`, `piece_brazierfloor01` and `piece_groundtorch`, so the only
`RPC_ToggleOn` path in `UpdateState` -- the one guarded by
`m_canTurnOff && m_wet` -- can never fire.  On THIS build a spawned fireplace
with the default `s_state = 1` burns indefinitely regardless of fuel.  The
everburning ZDO payload is still sent, from `roads/lamps.everburning_payload`
and round-tripped through `data_entry.decode` before it goes: it stops
`UpdateFireplace` decrementing `s_fuel` (that decrement is gated on
`IsBurning() && !m_infiniteFuel`), so the fuel state says what the world does
instead of drifting to zero under it.  What it does NOT do on this build is
keep the flame alive, and claiming otherwise would be inheriting a number.

`TimedTorchesStayLit` 1.4.0 is live and server-synced with `Wood Torch Stay Lit
= true` and `Keep on when wet = true`, and also `Wood Torch on Timer = true`
with on 17:00 / off 05:30 -- so the two ground torches are a NIGHT light by
config.  That is reported rather than hidden: it is the hours a dark arrival
point needs.

-----------------------------------------------------------------------------
4. WHAT IS *NOT* HERE
-----------------------------------------------------------------------------
NO ROAD LAMPS.  The follow-up in ledger seq 2876 asked for "two or three lamps
closing the last 26.65 m"; that item was WITHDRAWN in seq 2882 and withdrawn
again by this session's parent, and this module does not re-open it.  The
refusal is INHERITED AND ATTRIBUTED, not re-measured here: per `RoadNorthLand`
off `roads/lamps.py` BIOME_POLICY, Mountain `min_m` is 40.0 m against a 15.0 m
point light, so a 25 m annulus around every Mountain lamp is unlit by design
and a road END has nothing on the far side to close it.  `lamps.py::site` was
NOT run at the terminus.

The two `piece_groundtorch` this module DOES place are STRUCTURE lamps of the
camp under role `site_prop` -- the harbour's quay-torch precedent -- seated on
the road's own level lozenge east of the arch.  They never went through the
road gate and are not `road_lighting`.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
for _p in (JUMPSTART, JUMPSTART / "terraform", JUMPSTART / "roads",
           JUMPSTART / "ledger", JUMPSTART / "blueprints", HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import worldseed  # noqa: E402

# RESOLVED, not literal.  `main()` rebinds both from `--seed`/`--world`.
WORLD = worldseed.world()
SEED = worldseed.seed()
SITE_ID = "road-north-terminus-camp"

# THE ARCH.  Read off its ZDO, not assumed: `findObjects -prefab portal_wood`
# answers Id 434478:1820465111 at (-845.5, 33.56, 7730) yaw 90 tag `u-nterm`.
ARCH = (-845.50, 33.556, 7730.00)
ARCH_AABB = (-845.942, 7727.898, -844.763, 7732.132)   # x0, z0, x1, z1

# THE SEAT, solved -- see §2 of the module docstring.
CX, CZ = -839.50, 7732.50
BODY_W, BODY_D = 6.0, 4.0            # x, z
BODY_YAW = 0.0
FLOOR_Y = 33.556                     # = the arch's own applied datum
POST_PITCH = 2.0

# The porch is the WEST 2 m: open west and north so the fire lights the arch.
PORCH_X_MAX = CX - BODY_W / 2 + 2.0          # -840.50

# MEASURED collider spans, `blueprints/data/piece_geometry.json` (solid
# non-trigger colliders only; 246 triggers and 19,236 render meshes dropped).
# Every seat offset below is derived from these and from nothing else.
GEOM = {
    "wood_floor":       (-1.0096, -0.0329, -1.0148, 0.9904, 0.0969, 0.9852),
    "woodwall":         (-1.0, -1.0, -0.15, 1.0, 1.0, 0.15),
    "wood_door":        (-1.0, -1.0100, -0.25, 1.0, 1.0099, 0.25),
    "wood_pole":        (-0.2, -0.5, -0.2, 0.2, 0.5, 0.2),
    "wood_roof_top":    (-1.0, 0.1252, -0.2, 1.0, 0.8128, 0.2),
    "stone_floor_2x2":  (-1.0, -0.5, -1.0, 1.0, 0.5, 1.0),
    "fire_pit":         (-1.0, -0.73, -1.0, 1.0, 1.27, 1.0),
    "bed":              (-0.6, -0.02, -1.4, 0.6, 0.38, 1.4),
    "piece_groundtorch": (-0.1212, -0.6536, -0.1194, 0.1120, 0.8226, 0.1174),
}
# `wood_roof` has NO axis-aligned collider -- it is a single 8-vertex prism.
# Its low edge is local (z=+1.0031, y=-0.0485) and its high edge local
# (z=-0.9971, y=+1.0825), so it rises 1.1310 m over 2.0012 m of z.
ROOF_LOW_Z, ROOF_LOW_Y = 1.0031, -0.0485
ROOF_HIGH_Z, ROOF_HIGH_Y = -0.9971, 1.0825

# AND A LOOKUP THAT REFUSES RATHER THAN SKIPS.  MEASURED by this module's own
# defect harness: `clashes()` originally read `GEOM.get(prefab)` and
# `continue`d on a miss, so every natural object -- none of which is in the
# table above -- was silently skipped and a boulder planted inside the deck
# came back as ZERO clashes.  That is the session's defect in this file: an
# instrument returning a confident negative because it could not see.  The
# precedent for the fix is `roads/structures.py::classify()`, which RAISES on
# an unknown prefab name rather than guessing from its shape.
_GEOM_JSON: dict | None = None


def geom_of(prefab: str) -> tuple | None:
    """The prefab's solid AABB in its own local frame, or None for a body with
    no axis-aligned collider (`wood_roof`, a bare prism).  RAISES when the
    prefab is unknown to the geometry dump entirely."""
    global _GEOM_JSON
    if prefab in GEOM:
        return GEOM[prefab]
    if _GEOM_JSON is None:
        _GEOM_JSON = json.loads(
            (JUMPSTART / "blueprints/data/piece_geometry.json").read_text()
        )["prefabs"]
    if prefab not in _GEOM_JSON:
        raise SystemExit(
            f"REFUSING: {prefab!r} is not in piece_geometry.json, so its "
            f"collider extent is UNKNOWN. Skipping it would make every check "
            f"that touches it answer 'clean' for the wrong reason.")
    boxes = [c for c in _GEOM_JSON[prefab] if c[0] == "b"]
    if not boxes:
        return None
    return (min(c[1] for c in boxes), min(c[2] for c in boxes),
            min(c[3] for c in boxes), max(c[4] for c in boxes),
            max(c[5] for c in boxes), max(c[6] for c in boxes))


def world_box(p: dict) -> tuple[float, float, float, float, float, float]:
    """A piece's world AABB WITH ITS YAW APPLIED: (x0, y0, z0, x1, y1, z1).

    MEASURED by this module's defect harness: the first version of the
    grounding census used the UNROTATED half-extents, so the two `woodwall`
    panels standing at yaw 90 -- 2.0 m along their own x, 0.3 m thick -- were
    rasterised 2.0 m wide in WORLD x and 0.3 m in z, i.e. turned through 90
    degrees.  That invented two columns at x = -835.0, a metre and a half
    BEYOND the body's east edge at -836.5, and reported them as floating 0.211
    and 1.347 m.  Same family as the y-blind box census that produced 64
    duplicate pieces: a census whose raster does not match the geometry
    answers confidently about ground that carries nothing.

    Only multiples of 90 degrees appear in this body, and anything else is
    refused rather than approximated -- an OBB rasterised as an AABB would
    over-state the footprint of a diagonal piece and quietly re-introduce the
    same class of error.
    """
    g = geom_of(p["prefab"])
    if g is None:                              # `wood_roof`: prism low..high
        return (p["x"] - 1.0, p["y"] + ROOF_LOW_Y, p["z"] - 1.0,
                p["x"] + 1.0, p["y"] + ROOF_HIGH_Y, p["z"] + 1.0)
    yaw = round(p.get("yaw_deg", 0.0)) % 360
    if yaw in (0, 180):
        dx0, dx1, dz0, dz1 = g[0], g[3], g[2], g[5]
    elif yaw in (90, 270):
        dx0, dx1, dz0, dz1 = g[2], g[5], g[0], g[3]
    else:
        raise SystemExit(
            f"REFUSING to rasterise {p['prefab']} at yaw {yaw}: only 90-degree "
            f"multiples are exact as an AABB, and approximating an OBB here "
            f"over-states the footprint.")
    if yaw in (180, 270):
        dx0, dx1 = -dx1, -dx0
        dz0, dz1 = -dz1, -dz0
    return (p["x"] + dx0, p["y"] + g[1], p["z"] + dz0,
            p["x"] + dx1, p["y"] + g[4], p["z"] + dz1)

# Derived seats.  `wood_floor`'s pivot sits near its TOP face, so a walking
# surface at FLOOR_Y is authored 0.0969 m BELOW it -- the manual's worked
# example is a deck whose surface is 30.6 authored at 30.503.
FLOOR_PIVOT_Y = FLOOR_Y - GEOM["wood_floor"][4]
SLAB_BOTTOM_Y = FLOOR_PIVOT_Y + GEOM["wood_floor"][1]
WALL_H = 2.0                                   # one `woodwall` course
WALL_TOP_Y = FLOOR_Y + WALL_H
ROOF_PIVOT_Y = WALL_TOP_Y - ROOF_LOW_Y
RIDGE_Z = CZ
RIDGE_TOP_Y = ROOF_PIVOT_Y + ROOF_HIGH_Y

# MEASURED `Fireplace` fields per prefab, `roads/LampProbe.cs`.  Used to CHECK
# each flame rather than to describe it: see `fires()`.
FIRE = {
    "fire_pit":          dict(high=10.0, low=2.5, terrain_off=0.20, cover_off=0.50,
                              light_local_y=0.92),
    "piece_groundtorch": dict(high=15.0, low=None, terrain_off=0.57, cover_off=0.76,
                              light_local_y=0.746),
}
GROUNDTORCH_SEAT_OFF = 0.65359                 # pivot is 0.65359 m UP the shaft

# The two structure lamps, on the road's own exactly-level lozenge east of the
# arch (x=-843 is inside it for z 7728..7732, applied 33.55600, author seq
# 2754), 1.763 m clear of the arch AABB.
TORCHES = [(-843.0, 7729.0), (-843.0, 7731.0)]
TORCH_SURFACE_Y = 33.556

# THE LIVE NATURAL CENSUS, read with the NATIVE verb.
# `findObjects -prefab P -near x y z r -detailed` is a CUBE in all three axes
# and it is a NATIVE RCON verb: routed through the `consoleCommand` bridge it
# answers `Unknown command 'findObjects'`, i.e. zero rows, which reads as
# absence.  Paired with `objects_count pos=x,z` (an XZ CYLINDER with NO height
# bound) on orthogonal blindness -- the cylinder at r=12 answered Total 10
# while the r=16 cube found 11 Rock_4 alone; two shapes, two populations, and
# neither is wrong.
# Measured 2026-09-18 at (-839.5, 32.0, 7732.5) r=16:
LIVE_NATURALS = [
    ("Rock_3", -839.79, 30.28, 7734.73),
    ("Rock_4", -835.61, 30.56, 7734.50),
    ("Rock_4", -831.81, 31.46, 7731.01),
    ("Rock_4", -842.85, 38.66, 7723.22),
    ("Rock_4", -839.08, 28.27, 7739.83),
    ("Rock_4", -835.29, 27.55, 7740.29),
]
# MEASURED spans: `Rock_3` is 1.05 x 1.04 x 0.94 m and `Rock_4` 1.15 x 0.69 x
# 0.90 m -- these are boulders, not cliffs.  The nearest, `Rock_3` at
# (-839.79, 7734.73), reaches z 7734.21..7735.25 and so laps the deck's north
# edge by 0.29 m in XZ, but its top is y 31.13 against a floor at 33.556 --
# 2.43 m BELOW.  Nothing intersects, so NOTHING IS REMOVED, and that is the
# point: with no `objects_clear` and no `objects_remove` in this module there
# is no path by which the `u-nterm` arch can be deleted by a clearing aimed at
# vegetation.  `Rock_4` is not vegetation and `portal_wood` is not in any
# natural set, but `id=*` does not know that.

GROUND_TOL_M = 0.10                            # `build.py::GROUND_TOL_M`


# ---------------------------------------------------------------------------
# the instrument
# ---------------------------------------------------------------------------

def surface():
    """`(Applied, patches)` for the terminus zones.

    `PATCHSCAN_SANDBOX` is set per-agent on purpose: `run_patchscan.sh` opens
    with `rm -rf "$SANDBOX/BepInEx"`, so a shared `/tmp/patchscan/vh` staged by
    an earlier uid makes every later caller fail with nothing but `Permission
    denied` -- MEASURED here on the first attempt.
    """
    import os
    os.environ.setdefault("PATCHSCAN_SANDBOX", "/tmp/patchscan-terminus/vh")
    import flatten as FL
    import applied as AP
    zones = sorted(set(FL.zones_for(CX, CZ, 64.0, 64.0)))
    patches = FL.run_patchscan(zones, SEED, FL.SCRATCH / "patch_terminus.bin")
    return AP.Applied(actor="TerminusCamp"), patches


def bootstrap(ap, patches) -> dict:
    """The read-back proved BOTH directions before anything is trusted: the
    arch's written seat must be PRESENT, and ground nobody wrote must be
    ABSENT.  A one-directional check cannot tell a working instrument from one
    that returns zero for everything."""
    present = ap.surface_at(patches, ARCH[0], ARCH[2])
    absent = [ap.surface_at(patches, x, z) for x, z in ((-815.0, 7730.0),
                                                        (-846.0, 7760.0))]
    ok = (present is not None and abs(present[0] - 33.556) < 1e-4
          and abs(present[2]) > 0.05
          and all(a is not None and abs(a[2]) < 1e-9 for a in absent))
    return {"present_arch_seat": {"applied": round(present[0], 5),
                                  "delta": round(present[2], 5)},
            "absent_unwritten": [{"delta": round(a[2], 9)} for a in absent],
            "both_directions_ok": ok}


def post_nodes() -> list[tuple[float, float]]:
    """The 2 m lattice nodes of the footprint: one post column per node, so
    every floor tile has all four corners carried.  `harbour_city::piles`
    sizes a stack to REACH the deck and never to stop short, because
    `wood_floor` HAS `WearNTear` and a tile with nothing under it really
    falls -- the `fb_` kit's `wnt = 0` forgiveness does not apply here."""
    nx = int(round(BODY_W / POST_PITCH))
    nz = int(round(BODY_D / POST_PITCH))
    return [(CX - BODY_W / 2 + i * POST_PITCH, CZ - BODY_D / 2 + k * POST_PITCH)
            for i in range(nx + 1) for k in range(nz + 1)]


def ground_at(ap, patches, x: float, z: float) -> float:
    s = ap.surface_at(patches, x, z)
    if s is None:
        raise SystemExit(f"no patch cover at ({x}, {z}): refusing to seat "
                         f"anything on an unmeasured sample")
    return s[0]


# ---------------------------------------------------------------------------
# the body
# ---------------------------------------------------------------------------

def pieces(ap, patches) -> list[dict]:
    """Every piece with its seat arithmetic shown, in build order."""
    out: list[dict] = []

    def add(prefab, x, y, z, yaw, role, why):
        out.append({"prefab": prefab, "x": round(x, 4), "y": round(y, 4),
                    "z": round(z, 4), "yaw_deg": round(yaw % 360.0, 3),
                    "role": role, "why": why})

    x0, x1 = CX - BODY_W / 2, CX + BODY_W / 2
    z0, z1 = CZ - BODY_D / 2, CZ + BODY_D / 2
    xs = [x0 + 1.0 + 2.0 * i for i in range(int(BODY_W // 2))]     # tile centres
    zs = [z0 + 1.0 + 2.0 * k for k in range(int(BODY_D // 2))]

    # -- posts, sized to reach ------------------------------------------------
    for (nx, nz) in post_nodes():
        g = ground_at(ap, patches, nx, nz)
        need = FLOOR_Y - g
        # A stack of n 1 m poles hung under the slab reaches SLAB_BOTTOM - n.
        n = max(0, math.ceil(need - (FLOOR_Y - SLAB_BOTTOM_Y)))
        for k in range(n):
            add("wood_pole", nx, SLAB_BOTTOM_Y - 0.5 - k, nz, 0.0, "site_body",
                f"post column ({nx:.1f},{nz:.1f}) ground {g:.3f} needs "
                f"{need:.3f} m; pole {k + 1}/{n}")
        if n == 0:
            out.append({"note": f"node ({nx:.1f},{nz:.1f}) ground {g:.3f} is "
                                f"within the slab -- floor rests on ground, "
                                f"no pole", "prefab": None})

    # -- floor ---------------------------------------------------------------
    for tx in xs:
        for tz in zs:
            add("wood_floor", tx, FLOOR_PIVOT_Y, tz, 0.0, "site_body",
                f"2x2 deck tile; pivot near TOP face so surface {FLOOR_Y} is "
                f"authored at {FLOOR_PIVOT_Y:.4f}")

    # -- the hearthstone, because `fire_pit.m_notOnWood` is TRUE -------------
    # MEASURED on the prefab: `fire_pit` carries `m_notOnWood: true` while
    # `hearth` carries false.  `spawn_object` does not enforce a placement
    # flag, so this is respected by construction rather than by permission: a
    # 2x2 stone slab set flush in the deck, top at FLOOR_Y.
    fx, fz = x0 + 1.0, CZ
    add("stone_floor_2x2", fx, FLOOR_Y - GEOM["stone_floor_2x2"][4], fz, 0.0,
        "site_body", "hearthstone: fire_pit.m_notOnWood is TRUE, so the fire "
                     "does not stand on the deck; slab top flush at FLOOR_Y")

    # -- walls ---------------------------------------------------------------
    wall_y = FLOOR_Y + WALL_H / 2               # `woodwall` pivot is its centre
    for tx in xs[1:]:                           # room only: the porch is open
        add("woodwall", tx, wall_y, z0, 0.0, "site_body", "room south wall")
        add("woodwall", tx, wall_y, z1, 0.0, "site_body", "room north wall")
    for tz in zs:
        add("woodwall", x1, wall_y, tz, 90.0, "site_body", "room east wall")
    # The porch keeps its SOUTH wall as the windbreak on the uphill side and
    # stays open west and north -- west is where the arch is.
    add("woodwall", xs[0], wall_y, z0, 0.0, "site_body",
        "porch south wall: windbreak on the uphill side, porch stays open "
        "WEST so the fire's light reaches the arch unoccluded")
    # The room's west wall carries the door, facing the porch and the arch.
    add("wood_door", PORCH_X_MAX, FLOOR_Y + 1.0100, zs[0], 90.0, "site_body",
        "door into the room; a player off the arch crosses the porch past the "
        "fire and through this")
    add("woodwall", PORCH_X_MAX, wall_y, zs[1], 90.0, "site_body",
        "room west wall, north bay")

    # -- roof, over the porch as well ---------------------------------------
    for tx in xs:
        # THE EAVE PIVOT IS SET FROM THE WALL, NOT FROM THE RIDGE, and getting
        # that backwards is the defect `cover()` exists to catch.  The panel's
        # low edge sits ROOF_LOW_Z from its own pivot, so for an eave on the
        # wall at z1 the pivot is z1 - ROOF_LOW_Z -- which is HALF THE BODY
        # from the ridge, not one metre.  Placing it one metre in put all six
        # panels at the ridge, crossed, covering half the deck.
        add("wood_roof", tx, ROOF_PIVOT_Y, z1 - ROOF_LOW_Z, 0.0,
            "site_body", f"north slope: low edge on the wall top at z {z1}, "
                         f"high edge at the ridge z {RIDGE_Z}")
        add("wood_roof", tx, ROOF_PIVOT_Y, z0 + ROOF_LOW_Z, 180.0,
            "site_body", f"south slope, mirrored: low edge at z {z0}")
        # The cap's own skirt is at local (z 1.0219, y -0.0933).  Seat it on
        # the panel surface 1.0219 m DOWN-SLOPE from the ridge -- ridge height
        # minus the slope's magnitude over that run, then lift by the skirt's
        # own negative offset.
        ridge_top = ROOF_PIVOT_Y + ROOF_HIGH_Y
        add("wood_roof_top", tx,
            ridge_top - abs(ROOF_SLOPE) * 1.0219 + 0.0933, RIDGE_Z, 0.0,
            "site_body", "ridge cap seated on the panel surface 1.0219 m "
                         "down-slope from the ridge")

    # -- fixtures ------------------------------------------------------------
    add("bed", x1 - 0.8, FLOOR_Y + 0.02, CZ, 0.0, "site_prop",
        "THE BED. 1.2 x 2.8 m, long axis along z, against the east wall, "
        "inside the closed room")
    add("fire_pit", fx, FLOOR_Y + 0.09116, fz, 0.0, "site_prop",
        "THE FIRE, in the roofed porch: roofed so CheckWet keeps its 10.0 m "
        "light instead of its 2.5 m one, open west so it lights the arch")
    for (tx, tz) in TORCHES:
        add("piece_groundtorch", tx, TORCH_SURFACE_Y + GROUNDTORCH_SEAT_OFF, tz,
            0.0, "site_prop",
            "structure lamp on the road's own level lozenge; pivot is 0.65359 "
            "m up the shaft, so a torch seated at the surface would float")
    return [p for p in out if p.get("prefab")]


# ---------------------------------------------------------------------------
# the checks
# ---------------------------------------------------------------------------

def post_reach(ap, patches, ps: list[dict]) -> dict:
    """THE LOAD-BEARING CHECK: does every post column actually reach ground?

    This is separate from the 2 m column census on purpose, and it is the one
    that decides whether the deck falls.  `wood_floor` and `wood_pole` HAVE
    `WearNTear`, so a tile whose posts stop short really does collapse --
    unlike the `fb_` kit, where `wnt = 0` means a floating segment hangs
    silently forever.  Measured at each post's OWN xz, never at a cell centre:
    the ground here falls up to 0.9 m per metre, so a reading taken 0.5 m away
    is a different height.
    """
    rows = []
    for (nx, nz) in post_nodes():
        g = ground_at(ap, patches, nx, nz)
        poles = [p for p in ps if p["prefab"] == "wood_pole"
                 and abs(p["x"] - nx) < 1e-6 and abs(p["z"] - nz) < 1e-6]
        foot = (min(p["y"] for p in poles) - 0.5) if poles else SLAB_BOTTOM_Y
        rows.append({"node": [nx, nz], "ground_y": round(g, 3),
                     "poles": len(poles), "foot_y": round(foot, 3),
                     "buried_m": round(g - foot, 3),
                     "reaches": g - foot >= -1e-9})
    short = [r for r in rows if not r["reaches"]]
    return {"columns": len(rows), "stopping_short": len(short),
            "short_columns": short,
            "worst_burial_m": round(max(r["buried_m"] for r in rows), 3),
            "least_burial_m": round(min(r["buried_m"] for r in rows), 3),
            "by_post": rows}


ROOF_SLOPE = (ROOF_HIGH_Y - ROOF_LOW_Y) / (ROOF_HIGH_Z - ROOF_LOW_Z)


def roof_y_at(p: dict, x: float, z: float) -> float | None:
    """The EXACT roof-surface Y over (x, z) for one `wood_roof` panel, or None
    when the point is not under it.

    Modelled as the sloping plane the prism actually is, NOT as its bounding
    box.  That distinction is the whole check: a bounding box cannot tell a
    gable from an X, because two panels crossing at the ridge and two panels
    meeting at the ridge have very nearly the same union AABB.  MEASURED, and
    this is the defect it caught on the live world: the first build placed the
    eave pivots one metre in from the walls, so all six panels crossed at the
    ridge, the roof covered only z 7731.5..7733.5 of a 7730.5..7734.5 body,
    and BOTH eaves were open to the sky with a 1 m gap between wall-top and
    roof.  Every other check passed -- grounding, post reach, per-prefab count
    at tolerance 0, the arch guard, illumination -- because none of them asks
    what is overhead.

    AND HERE IS THE HALF IT STILL CANNOT SEE, MEASURED ON THE BOATHOUSES.
    A 180 degree yaw flip moves neither the footprint nor the AABB nor this
    function's own covered/open answer: `shed-dockshore`, `shed-vestvik` and
    the `harbour-village-sudrvik` boathouse stood with all 480 panels pitched
    INWARD -- both slopes falling 0.5654 m/m into the ridge, a butterfly --
    and `cover()` returned 0 open cells before and after the correction, to
    within a 6.1 mm sliver at the eave (the prism is asymmetric: low edge
    +1.0031, high edge -0.9971).  Coverage answers "is something overhead",
    never "which way does it face".  Use `pitch_sense` below for that.
    """
    # The inverse of `crossings/assemble.py::Placed.aabb`'s forward rotation:
    # wx = lx*cos + lz*sin, wz = -lx*sin + lz*cos, i.e. yaw 0 = +Z, 90 = +X,
    # clockwise from above.  All four cardinals are modelled because the
    # boathouse sheds run their ridge along z and place at 90 / 270.
    yaw = round(p.get("yaw_deg", 0.0)) % 360
    wx, wz = x - p["x"], z - p["z"]
    if yaw == 0:
        lx, lz = wx, wz
    elif yaw == 90:
        lx, lz = -wz, wx
    elif yaw == 180:
        lx, lz = -wx, -wz
    elif yaw == 270:
        lx, lz = wz, -wx
    else:
        raise SystemExit(f"roof panel at yaw {yaw} is not modelled")
    if not -1.0 <= lx <= 1.0:
        return None
    lo, hi = sorted((ROOF_LOW_Z, ROOF_HIGH_Z))
    if not lo <= lz <= hi:
        return None
    return p["y"] + ROOF_LOW_Y + ROOF_SLOPE * (lz - ROOF_LOW_Z)


def pitch_sense(ps: list[dict], ridge_x: float | None = None,
                ridge_z: float | None = None) -> dict:
    """Does every panel RISE toward the ridge?  The signed check `cover()`
    cannot make.

    Exactly one of `ridge_x` / `ridge_z` names the ridge line.  For each panel
    the surface is sampled 0.9 m either side of its own pivot along the slope
    axis and the gradient is taken TOWARD the ridge; a gable is every panel
    positive.  MEASURED on the boathouses before correction: -0.5654 m/m on
    all 480, i.e. both slopes falling into the ridge, while the count census,
    the position census at tolerance 0 and `cover()` all read clean.
    """
    if (ridge_x is None) == (ridge_z is None):
        raise SystemExit("pitch_sense: name exactly one of ridge_x / ridge_z")
    rows, bad = [], []
    for p in ps:
        if p["prefab"] != "wood_roof":
            continue
        if ridge_x is not None:
            a = roof_y_at(p, p["x"] - 0.9, p["z"])
            b = roof_y_at(p, p["x"] + 0.9, p["z"])
            toward = 1.0 if p["x"] < ridge_x else -1.0
            off = p["x"] - ridge_x
        else:
            a = roof_y_at(p, p["x"], p["z"] - 0.9)
            b = roof_y_at(p, p["x"], p["z"] + 0.9)
            toward = 1.0 if p["z"] < ridge_z else -1.0
            off = p["z"] - ridge_z
        if a is None or b is None or off == 0.0:
            continue                      # a panel astride the ridge line
        g = round(toward * (b - a) / 1.8, 4)
        rows.append(g)
        if g <= 0.0:
            bad.append({"at": [p["x"], p["y"], p["z"]],
                        "yaw_deg": p.get("yaw_deg"), "gradient": g})
    return {"panels": len(rows), "falling_toward_ridge": len(bad),
            "gradients": sorted(set(rows)), "inverted": bad,
            "ok": not bad,
            "why": "positive = rising toward the ridge, which is what a gable "
                   "is; a 180 degree yaw flip changes this sign and nothing "
                   "else a footprint-shaped check can measure"}


def cover(ps: list[dict], step: float = 0.5) -> dict:
    """Is the deck actually under a roof, cell by cell -- and is the BED?

    `Cover.GetCoverForPoint` is what `Bed.CheckExposure` and
    `Fireplace.CheckWet` consult, so "there are roof pieces in the build" is
    not the same claim as "this square metre is sheltered".  Reported per
    sample over the deck's own footprint, plus the two samples that matter by
    name: the bed's and the fire's.
    """
    x0, x1 = CX - BODY_W / 2, CX + BODY_W / 2
    z0, z1 = CZ - BODY_D / 2, CZ + BODY_D / 2
    roofs = [p for p in ps if p["prefab"] == "wood_roof"]
    flats = [p for p in ps if p["prefab"] in ("wood_floor", "wood_roof_top")
             and p["y"] > FLOOR_Y + 1.0]
    samples, open_cells = [], []
    n = 0
    x = x0 + step / 2
    while x < x1:
        z = z0 + step / 2
        while z < z1:
            ys = [y for y in (roof_y_at(p, x, z) for p in roofs)
                  if y is not None]
            for q in flats:
                bx0, _b, bz0, bx1, _t, bz1 = world_box(q)
                if bx0 <= x <= bx1 and bz0 <= z <= bz1:
                    ys.append(world_box(q)[1])
            n += 1
            if ys:
                samples.append({"at": [round(x, 2), round(z, 2)],
                                "cover_y": round(min(ys), 3),
                                "headroom_m": round(min(ys) - FLOOR_Y, 3)})
            else:
                open_cells.append([round(x, 2), round(z, 2)])
            z += step
        x += step
    def at(px: float, pz: float) -> dict:
        ys = [y for y in (roof_y_at(p, px, pz) for p in roofs) if y is not None]
        return {"at": [px, pz], "covered": bool(ys),
                "cover_y": round(min(ys), 3) if ys else None,
                "headroom_m": round(min(ys) - FLOOR_Y, 3) if ys else None}
    bed = next((p for p in ps if p["prefab"] == "bed"), None)
    fire = next((p for p in ps if p["prefab"] == "fire_pit"), None)
    return {"deck_samples": n, "covered": len(samples),
            "open_to_sky": len(open_cells),
            "covered_fraction": round(len(samples) / n, 3) if n else None,
            "open_cells": open_cells,
            "min_headroom_m": min((s["headroom_m"] for s in samples),
                                  default=None),
            "bed": at(bed["x"], bed["z"]) if bed else None,
            "fire": at(fire["x"], fire["z"]) if fire else None,
            "fully_roofed": not open_cells}


def grounding(ap, patches, ps: list[dict]) -> dict:
    """Grounded fraction, worst air gap and worst burial, GROUPED BY COLUMN.

    Columns are `base_geometry.BASE_PROFILE_CELL_M = 2.0` m cells, and the
    reduction is `bottoming`'s: per column, the LOWEST solid and its Y.  A
    column over a roof is not floating -- its lowest solid is the floor.

    AND THE GROUND UNDER A COLUMN IS A RANGE, NOT A POINT.  `bottoming`
    compares against a flat `pad_y`; there is no pad here, and over a 2 m cell
    on this spur the ground moves up to 1.8 m.  So each row carries the cell's
    ground MIN and MAX as well as its centre, and `gap_m` -- taken against the
    centre, the like-for-like number -- is reported beside `gap_vs_cell_max_m`,
    which is the honest float test: a foot below the highest ground in its own
    cell has ground beside it, and only a foot above ALL of the cell's ground
    is unsupported.  Reporting only the centre gap over-states float on every
    downhill cell, and `post_reach` is the exact answer regardless.
    """
    cells: dict[tuple[int, int], tuple[str, float]] = {}
    for p in ps:
        bx0, by0, bz0, bx1, _by1, bz1 = world_box(p)
        lo = by0
        # Every 2 m cell the box actually covers, from the ROTATED box.
        kx0, kx1 = math.floor(bx0 / 2.0), math.floor(bx1 / 2.0)
        kz0, kz1 = math.floor(bz0 / 2.0), math.floor(bz1 / 2.0)
        for kx in range(int(kx0), int(kx1) + 1):
            for kz in range(int(kz0), int(kz1) + 1):
                cur = cells.get((kx, kz))
                if cur is None or lo < cur[1]:
                    cells[(kx, kz)] = (p["prefab"], lo)
    rows = []
    for (kx, kz), (pf, lo) in sorted(cells.items()):
        wx, wz = kx * 2.0 + 1.0, kz * 2.0 + 1.0
        g = ground_at(ap, patches, wx, wz)
        corner = [ground_at(ap, patches, wx + dx, wz + dz)
                  for dx in (-1.0, 0.0, 1.0) for dz in (-1.0, 0.0, 1.0)]
        rows.append({"column": [wx, wz], "lowest_solid": pf,
                     "lowest_y": round(lo, 3), "ground_y": round(g, 3),
                     "cell_ground_min": round(min(corner), 3),
                     "cell_ground_max": round(max(corner), 3),
                     "gap_m": round(lo - g, 3),
                     "gap_vs_cell_max_m": round(lo - max(corner), 3)})
    gaps = sorted(r["gap_m"] for r in rows)
    hon = sorted(r["gap_vs_cell_max_m"] for r in rows)
    grounded = sum(1 for g in gaps if g <= GROUND_TOL_M)
    grounded_h = sum(1 for g in hon if g <= GROUND_TOL_M)
    near = [r for r in rows if 0 < r["gap_vs_cell_max_m"] <= 4.0]
    return {"columns": len(rows), "tolerance_m": GROUND_TOL_M,
            "grounded_fraction": round(grounded / len(rows), 3),
            "grounded_fraction_vs_cell_max": round(grounded_h / len(rows), 3),
            "worst_air_gap_m": gaps[-1], "worst_burial_m": gaps[0],
            "worst_air_gap_vs_cell_max_m": hon[-1],
            "worst_burial_vs_cell_max_m": hon[0],
            "floating_near_ground_columns": len(near),
            "by_column": rows}


def fires(ap, patches, ps: list[dict]) -> dict:
    """Every flame: distance to the ARRIVAL POINT against ITS OWN measured
    `Light.range`, plus the two geometry tests that can switch it off.

    The metric reported is `distance_m` vs `range_m`.  `in_view` is NOT
    reported and is not computed: a sightline at an 80 m cap is not
    illumination at a 15 m range, and reporting the former as the latter is
    what produced this ticket.
    """
    out = []
    for p in ps:
        spec = FIRE.get(p["prefab"])
        if spec is None:
            continue
        d = math.dist((p["x"], p["z"]), (ARCH[0], ARCH[2]))
        d3 = math.dist((p["x"], p["y"] + spec["light_local_y"], p["z"]),
                       (ARCH[0], ARCH[1] + 1.0, ARCH[2]))
        g = ground_at(ap, patches, p["x"], p["z"])
        # `CheckUnderTerrain`: blocked when Heightmap > pos.y + offset.
        buried_by = round(g - (p["y"] + spec["terrain_off"]), 3)
        # The 0.5 m up-ray from pos + up*cover_off must miss every solid.
        ray0 = p["y"] + spec["cover_off"]
        ray1 = ray0 + 0.5
        hit = []
        for q in ps:
            if q is p:
                continue
            qx0, qy0, qz0, qx1, qy1, qz1 = world_box(q)
            if (qx0 <= p["x"] <= qx1 and qz0 <= p["z"] <= qz1
                    and qy1 > ray0 and qy0 < ray1):
                hit.append(q["prefab"])
        out.append({
            "prefab": p["prefab"], "at": [p["x"], p["y"], p["z"]],
            "range_m": spec["high"], "wet_range_m": spec["low"],
            "distance_to_arrival_m": round(d, 3),
            "distance_3d_to_player_head_m": round(d3, 3),
            "illuminates_arrival": d <= spec["high"],
            "margin_m": round(spec["high"] - d, 3),
            "buried_beyond_offset_m": max(0.0, buried_by),
            "blocked_by_terrain": buried_by > 0,
            "cover_ray_hits": hit, "blocked_by_cover_ray": bool(hit),
        })
    lit = [f for f in out if f["illuminates_arrival"]
           and not f["blocked_by_terrain"] and not f["blocked_by_cover_ray"]]
    return {"flames": out,
            "nearest_flame_m": min((f["distance_to_arrival_m"] for f in out),
                                   default=None),
            "nearest_working_flame_m": min(
                (f["distance_to_arrival_m"] for f in lit), default=None),
            "arrival_point": list(ARCH)}


def clashes(ps: list[dict], naturals: list[tuple], ap=None,
            patches=None) -> list[dict]:
    """Natural objects whose measured box intersects a placed piece IN 3D,
    each classified BELOW_GRADE or ABOVE_GRADE.

    In 3D deliberately: a padded two-dimensional version of this test flagged
    `Rock_3` at (-839.79, 7734.73) -- whose top is y 31.13 against a floor at
    33.556, i.e. 2.43 m BELOW the deck -- and would have ordered a clearing
    that is not needed.

    And the grade split is the difference between a defect and a detail.  A
    foundation post driven past a boulder UNDERGROUND is what a foundation
    post does; the same overlap above ground is two objects visibly inside
    each other.  Both are reported with their overlap volume, and neither is
    silently dropped -- which is how the first version of this function
    managed to answer ZERO for a boulder planted in the middle of the deck.
    """
    out = []
    for (pf, nx, ny, nz) in naturals:
        g = geom_of(pf)
        if g is None:
            # NOT `continue`: a natural with no axis-aligned collider is a
            # thing this test CANNOT answer about, and that is a different
            # statement from "it does not clash". It goes in the report.
            out.append({"natural": pf, "at": [nx, ny, nz],
                        "hits": "UNMEASURABLE -- prism-only collider",
                        "piece_at": None})
            continue
        nb = (nx + g[0], ny + g[1], nz + g[2], nx + g[3], ny + g[4], nz + g[5])
        for p in ps:
            pb = world_box(p)
            if not (nb[0] < pb[3] and nb[3] > pb[0] and nb[1] < pb[4]
                    and nb[4] > pb[1] and nb[2] < pb[5] and nb[5] > pb[2]):
                continue
            ox = min(nb[3], pb[3]) - max(nb[0], pb[0])
            oy = min(nb[4], pb[4]) - max(nb[1], pb[1])
            oz = min(nb[5], pb[5]) - max(nb[2], pb[2])
            top = min(nb[4], pb[4])
            grade = None
            if ap is not None:
                gx = (max(nb[0], pb[0]) + min(nb[3], pb[3])) / 2
                gz = (max(nb[2], pb[2]) + min(nb[5], pb[5])) / 2
                grade = ground_at(ap, patches, gx, gz)
            out.append({
                "natural": pf, "at": [nx, ny, nz], "hits": p["prefab"],
                "piece_at": [p["x"], p["y"], p["z"]],
                "overlap_xyz_m": [round(ox, 4), round(oy, 4), round(oz, 4)],
                "overlap_volume_m3": round(ox * oy * oz, 5),
                "overlap_top_y": round(top, 3),
                "ground_y": None if grade is None else round(grade, 3),
                "class": None if grade is None else
                         ("BELOW_GRADE" if top <= grade + 1e-6
                          else "ABOVE_GRADE"),
            })
    return out


def arch_guard(ps: list[dict]) -> dict:
    """No piece of this body may enter the arch's AABB, and the module writes
    no terrain, so the arch's seat cannot move."""
    ax0, az0, ax1, az1 = ARCH_AABB
    inside, gap = [], math.inf
    for p in ps:
        px0, _py0, pz0, px1, _py1, pz1 = world_box(p)
        if px0 < ax1 and px1 > ax0 and pz0 < az1 and pz1 > az0:
            inside.append(p["prefab"])
        if pz0 < az1 and pz1 > az0:
            gap = min(gap, px0 - ax1)
    return {"pieces_inside_arch_aabb": inside,
            "closest_approach_m": round(gap, 3),
            "terrain_writes": 0, "samples_reauthored": 0,
            "objects_removed": 0}


# ---------------------------------------------------------------------------
# stages
# ---------------------------------------------------------------------------

def report(ap, patches) -> dict:
    ps = pieces(ap, patches)
    tally: dict[str, int] = {}
    for p in ps:
        tally[p["prefab"]] = tally.get(p["prefab"], 0) + 1
    nodes = []
    for (nx, nz) in post_nodes():
        g = ground_at(ap, patches, nx, nz)
        nodes.append({"node": [nx, nz], "ground": round(g, 3),
                      "post_m": round(FLOOR_Y - g, 3)})
    return {
        "site_id": SITE_ID,
        "bootstrap": bootstrap(ap, patches),
        "seat": {"centre": [CX, CZ], "w": BODY_W, "d": BODY_D,
                 "yaw_deg": BODY_YAW, "floor_y": FLOOR_Y,
                 "corner_spread_m": 0.0,
                 "corner_spread_basis": "the floor is a single authored Y, not "
                                        "a reading of the ground, so the "
                                        "spread is 0.0000 by construction",
                 "min_no_cut_floor_y": round(max(n["ground"] for n in nodes), 5),
                 "floor_above_cheapest_m": round(
                     FLOOR_Y - max(n["ground"] for n in nodes), 5)},
        "posts": nodes,
        "pieces": len(ps), "prefabs": tally,
        "arch_guard": arch_guard(ps),
        "post_reach": post_reach(ap, patches, ps),
        "cover": cover(ps),
        "grounding": grounding(ap, patches, ps),
        "natural_clashes": clashes(ps, LIVE_NATURALS, ap, patches),
        "illumination": fires(ap, patches, ps),
    }


def main() -> int:
    a = argparse.ArgumentParser()
    a.add_argument("stage", choices=["survey", "plan", "build", "save", "reroof", "verify"])
    a.add_argument("--actor", required=True,
                   help="the agent actually running: never defaulted")
    a.add_argument("--out", default="/tmp/terminus")
    worldseed.add_arguments(a)
    args = a.parse_args()

    global SEED, WORLD  # noqa: PLW0603
    SEED, WORLD = worldseed.apply(args)

    ap, patches = surface()
    rep = report(ap, patches)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    if args.stage in ("survey", "plan"):
        (out / "terminus_camp_plan.json").write_text(
            json.dumps({"report": rep, "pieces": pieces(ap, patches)},
                       indent=1, sort_keys=True))
        print(json.dumps(rep, indent=1, sort_keys=True))
        return 0

    import emit_terminus_camp as EM          # the live writer, kept separate
    return EM.run(args.stage, args.actor, ap, patches, rep,
                  pieces(ap, patches), out)


if __name__ == "__main__":
    raise SystemExit(main())
