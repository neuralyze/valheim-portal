#!/usr/bin/env python3
"""ROAD LIGHTING: site a lamp where the TERRAIN gives the longest sightline in
BOTH directions, dense where the biome hides the road and sparing where it does
not.

THE OPERATOR'S RULE, VERBATIM: "the roads need to have lit lamposts. use the
terrain to pick a good spot where it is visible from both directions the
farthest each within 30-60m of each other. the lamposts only really need to be
in the forests or other biomes with limited visibility. the meadows and other
biomes with high visibility they can be used sparringly."

That is a SITING rule with a spacing BOUND, and the two halves are not the same
instruction.  30-60 m is the window a lamp may land in; WHERE inside that
window it lands is decided by measuring the ground.  A tool that divides the
length by 45 and stamps satisfies the bound and answers a different question.

--------------------------------------------------------------------------
THE LAMP, AND EVERY CLAIM HERE IS MEASURED BY `roads/LampProbe.cs`
--------------------------------------------------------------------------
`piece_groundtorch`, the Standing iron torch, spawned with a ZDO payload that
turns its own `Fireplace` everburning.

  * EMITS LIGHT: one `Light`, type Point, range 15.0 m, intensity 1.5, colour
    (1, 0.621, 0.482), at local y +0.746 -- ground + 1.400 once seated.  Point
    and not Spot is the whole reason it wins.  `piece_hoodedlantern` has NO
    Fireplace at all and a 30 m light, and it was the front-runner until the
    probe measured the light's aim: `type Spot`, `spot_angle 30`,
    `aim_local [0, 0, 1]` -- a 30 degree cone fired horizontally along the
    prefab's +Z.  Its omnidirectional component is a 2 m point light.  A road
    is walked in both directions, so 15 m of omnidirectional beats 30 m of
    one-way wedge.  For the record, every other buildable lit candidate:
    bonfire 20 m (and 4.1 m tall, a bonfire is not a lamppost), hearth 13 m,
    `piece_Lavalantern` 12 m point and fuel-free but `m_notOnWood` and an
    Ashlands lava cage on a Meadows road, `piece_walltorch` 12 m (needs a
    wall), the green/blue/mist standing torches 10 m,
    `piece_dvergr_lantern_pole` 6 m with its pivot 3.0 m above its foot.

  * NEEDS NO FUEL AND CANNOT BURN OUT, and NOT because a mod says so.  The
    prefab ships `m_infiniteFuel: false, m_startFuel: 2, m_maxFuel: 6,
    m_secPerFuel: 20000, m_fuelItem: Resin` -- about 11 hours and then it wants
    resin, once per lamp, forever.  `TimedTorchesStayLit` v1.4.0 IS installed
    (DLL checked, not merely its config -- four orphaned configs have been
    found in this session alone) and its server-synced, admin-locked config
    sets `Metal Torch Stay Lit = true`, `Keep on when wet = true` and a
    17:00-05:30 night timer, and its own DLL names `piece_groundtorch(Clone)`
    and patches `Fireplace.IsBurning` / `Interact` / `GetHoverText`.  That is
    good, and it is NOT what this module relies on: a mod is one uninstall away
    from 9 km of dark road plus a refuelling chore.

    So each lamp carries its own everburning switch, in VANILLA.  MEASURED in
    `assembly_valheim` IL (`/tmp/roads/av.il`, `ZNetView::LoadFields`):
    `Awake` calls `LoadFields`, which returns immediately unless the ZDO holds
    bool `HasFields`; then per `MonoBehaviour` in the hierarchy it requires
    bool `HasFields<TypeName>`; then it reflects that type's fields and, for
    each, reads ZDO key `"<TypeName>.<fieldName>"` typed by the field --
    `GetInt`, `GetFloat`, `GetBool`, `GetVec3`, `GetString`, prefab, ItemDrop.
    `Fireplace.m_infiniteFuel` is a bool field read by `UpdateFireplace`,
    `IsBurning`, `GetHoverText`, `Interact`, `UseItem`, `TryGetItems` and
    `CanUseItems`.  And `ZDO::GetBool(int32, bool)` is
    `ZDOExtraData::GetInt(...) != 0`, so these three go in the DataEntry INTS
    section as 1 -- a bool section would be read as something else entirely.

  * SERVER-SYNCED AND PERSISTED: `ZNetView` present, `m_persistent true`.
    It is an ordinary piece ZDO; it survives save and load like the roads do.

  * DOES NOT DECAY: `WearNTear` material Wood, health 100, `m_noSupportWear
    true` so it needs no support, `m_noRoofWear false` so in VANILLA an
    exposed torch would take weather damage.  `AzuWearNTearPatches` v1.0.9 is
    installed (DLL checked) with `No Weather Damage to buildings = On`,
    `No Damage to player buildings = On` and `Disable Structural Integrity
    system = On`, all server-synced and admin-locked.

  * PIVOT: local y -0.65359 .. +0.82260, and the RENDER MESH spans exactly the
    same as the collider, so the pivot is 0.654 m up the shaft and the visible
    foot is 0.654 m BELOW it.  This is the trap: `base_geometry`'s
    ground-resting guard lists `groundtorch` and then DROPS it because it fails
    the pivot-at-base check, which is that module refusing to guess -- and
    `CastleKit_groundtorch` is the identical mesh with its root at the foot
    (-0.0516 .. 1.4246), so the two differ by 0.602 m of nothing but prefab
    authoring.  A lamp seated as though its pivot were its foot floats 0.654 m,
    which is the portal defect at ten times the size.  The offset is not
    restated as a literal here: `seat_offset_m()` asks
    `fixtures.fixture_box`, the SAME box builder the placement audit uses.

--------------------------------------------------------------------------
THE SIGHTLINE, AS A MEASURED MAXIMISATION
--------------------------------------------------------------------------
For a candidate station on the centreline, the sightline in one direction is
how far a walker can get before the APPLIED SURFACE rises into the line between
their eye and the lamp's flame.  Both endpoints are read off
`applied.Applied.surface_at` -- the live union of the ledger's own terrain
blobs plus generated height, bilinear, which is the only instrument in this
project that measures what a player actually stands on.  Reading the PLANNED
profile instead would answer a question about the plan: 89 m of T4's centreline
sits on samples a settlement pad overwrote after the road was written.

Visibility is the CONTIGUOUS run, not the farthest visible station.  From the
flame at height `FLAME_M` above the surface at the lamp's own XZ, for each
station `j` ahead at distance `t`:

    blocked when   max over k in (i, j) of (surf[k] - flame) / (k - i)
                   >=  (surf[j] + EYE_M - flame) / t

and the sightline is the last `t` before the first block.  A far hillside above
an intervening ridge is genuinely visible, but it is not what "a lamp in view
as you walk" means, so the run stops at the ridge.  The running maximum makes
this O(cap) per direction rather than O(cap^2).

The score of a candidate is `min(forward, backward)`.  MIN, not sum and not
mean: a lamp seen 80 m one way and 5 m the other lights one approach and
ambushes the other.  Maximising the minimum is what puts lamps on crests and on
the approaches to bends, and the report prints the profile of the window so the
claim is visible rather than asserted.

--------------------------------------------------------------------------
BIOME DENSITY
--------------------------------------------------------------------------
The biome is READ, per candidate, from the same PatchScan zone patches the
heights come from: `heights.Patch.biomes` is `WorldGenerator.GetBiome` sampled
on the zone's own 1 m lattice.  It is not inferred from coordinates and not
taken from the segment's name.

--------------------------------------------------------------------------
WHAT THIS MODULE WILL NOT DO
--------------------------------------------------------------------------
It refuses rather than shrinks, and it records what it refused and why.  A
guard that narrows a request instead of declining it is silent, and this
project has paid for four of those.  Keep-outs are asked of the instruments
that own them: `ribbon.pad_keepouts_from_chain` (the rectangles
`writer._pad_footprint_check` will judge a write against),
`ribbon.poi_standoff_discs`, `ribbon.protected_piece_positions` for bridge and
harbour decks and their recorded extents, and `applied.Applied.foreign_at` for
per-sample authorship, which is blind to radii by design.

Note the one guard that is NOT armed for this op: `_pad_footprint_check` fires
on `terrain_write` only.  A lamp writes no terrain, so nothing in `writer.py`
stops it landing on a foundation.  That is why the keep-out is enforced here,
explicitly, against the same rectangles.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
for _p in (JUMPSTART, HERE, JUMPSTART / "ledger", JUMPSTART / "terraform",
           JUMPSTART / "blueprints", JUMPSTART / "settlements"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import applied as appliedmod   # noqa: E402
import ribbon                  # noqa: E402
import tcdata                  # noqa: E402

WORLD = "Ulfsland"
SEED = "Pirate68"
ACTOR = "Lampposts"
SCRATCH = Path("/tmp/roads/lamps")

LAMP_PREFAB = "piece_groundtorch"

# `ribbon.WATER_LEVEL_M`, restated with its provenance rather than imported
# blind: a lamp at the water plane is a lamp in the surf.  The freeboard is the
# same 0.5 m `field.Field.land` uses and for the same reason.
WATER_LEVEL_M = ribbon.WATER_LEVEL_M
FREEBOARD_M = 0.5

# MEASURED by roads/LampProbe.cs: the Point light sits at local y +0.746 and the
# pivot is 0.65359 above the visible foot, so the flame is 1.39959 m above the
# ground the lamp stands on.  Restated rather than re-derived at runtime
# because the probe writes to /tmp and /tmp does not survive a reboot; the
# module docstring carries the provenance and `--probe` re-measures it.
FLAME_ABOVE_SURFACE_M = 0.65359 + 0.746

# A standing player's eye height.  DESIGN, not measured -- there is no
# instrument on this host that reads `Player.m_eyePoint` without a client, and
# inventing a measurement is worse than declaring a constant.  `--eye-sweep`
# re-runs the siting at 1.5 and 1.9 m and prints how much the answer moves, so
# the sensitivity is a number rather than a hope.
EYE_M = 1.7

# How far the sightline search looks.  DESIGN: the lamp ILLUMINATES 15 m (its
# measured light range) and is VISIBLE as a beacon much further, but a 1.5 m
# torch flame at 80 m is a pixel, and a cap that flatters the report is worse
# than a short one.  Candidates that hit the cap are counted in the report so a
# reader can see when the terrain, rather than the cap, stopped the answer.
SIGHT_CAP_M = 80.0

# Sightline sampling along the road.  1 m, the lattice the terrain is written
# on: a coarser step steps over the ridge that does the blocking.
SIGHT_STEP_M = 1.0

# Where the lamp stands relative to the centreline.  The carriageway is
# `half_width_m` each side and `ribbon.SHOULDER_M` of shoulder beyond it, so
# this offset puts the lamp's 0.24 m footprint wholly on the shoulder: at
# half_width + 0.55 the shaft spans half_width+0.43 .. half_width+0.67, inside
# a 1.0 m shoulder with clear air both sides.  A cart uses the carriageway; the
# shoulder is graded ground that nothing drives on.
SHOULDER_STANDOFF_M = 0.55

# Clearance the lamp keeps from any piece already in the world.  `PLACED_CLEAR_M`
# is what the batter uses against a placed object; a lamp is a placed object
# itself, so it keeps the same distance plus its own half-width.
PIECE_CLEAR_M = ribbon.PLACED_CLEAR_M + 0.15

# How much the ground may rise and fall ACROSS the lamp's own 0.23 x 0.24 m
# footprint before the spot is refused.  Seating on the deepest corner means a
# lamp never floats, but on broken ground it buys that by burying the base:
# the burial equals the footprint's relief.  MEASURED on S1, the 30.6 m ramp
# off the portal hall onto the bridge, the worst footprint relief is 0.118 m,
# so a lamp there stands with 12 cm of its base in the bank -- fine.  At some
# point it stops being fine, and rather than silently accepting whatever the
# ground gives, a spot above this is REFUSED and recorded with its measured
# relief.  The window then picks a neighbouring station, which is what the
# window is for.
#
# AND THE GAME AGREES WITH THIS GATE, which is the best kind of corroboration
# because it was found after the gate was written and for a different reason.
# MEASURED by `LampProbe.cs` off the prefab's own `Piece` fields:
# `piece_groundtorch` is `m_notOnTiltingSurface = True`.  So the build menu's
# own rule for this exact prefab is "not on a slope", and `spawn_object`
# bypasses it -- a headless placement would put a torch on a bank the game
# would have refused a player.  This gate is that rule, expressed against the
# applied surface rather than against a collider normal.
#
# (`m_notOnWood` is FALSE, and an earlier draft of this file said otherwise on
# the strength of a hand-written column list that omitted `distant` and so
# read `not_on_tilting` in the `not_on_wood` slot.  Three instruments --
# `LampProbe`, `crossings/data/piece_material.tsv` and a modded re-dump --
# agree it is 0.  Recorded because the shape of that mistake is the one this
# tree keeps paying for: a display bug read as a measurement.)
FOOTPRINT_RELIEF_MAX_M = 0.15

# --------------------------------------------------------------------------
# biome policy
# --------------------------------------------------------------------------
# PUBLISHED PER BIOME, with the reason, because "dense in forests, sparing in
# meadows" is a design decision and it belongs where it can be argued with.
#
# `min_m`/`max_m` bound the gap to the PREVIOUS lamp.  The operator's 30-60 m
# is the bound for a low-visibility biome; for an open one they said "used
# sparingly", and the task's reading of that is "near the 60 m bound or beyond
# it where sightlines are long".  So an open biome may stretch to `max_m`
# ABOVE 60, and only under the overlap condition in `_biome_allows`: the lamp
# must be visible backwards at least as far as the gap it just left, so the
# previous lamp is still in view when this one comes into view.  Without that
# condition "sparingly" would just mean "dark".
BIOME_POLICY: dict[str, dict] = {
    "BlackForest": {"min_m": 30.0, "max_m": 42.0, "why":
                    "dense conifer forest, the road is a corridor with 5-10 m "
                    "of visibility off it; the operator named forests as the "
                    "place lamps are actually needed"},
    "Swamp":       {"min_m": 30.0, "max_m": 42.0, "why":
                    "permanent fog and standing trees; the darkest walkable "
                    "biome in the game"},
    "Mistlands":   {"min_m": 30.0, "max_m": 36.0, "why":
                    "mist halves visibility again; tightest spacing in the set"},
    "Mountain":    {"min_m": 40.0, "max_m": 60.0, "why":
                    "open ground but broken relief and blizzards; the terrain "
                    "does the occluding rather than the vegetation, which is "
                    "exactly what the sightline search reads"},
    "DeepNorth":   {"min_m": 40.0, "max_m": 60.0, "why":
                    "as Mountain, plus permanent low light"},
    "Meadows":     {"min_m": 55.0, "max_m": 90.0, "why":
                    "long open sightlines; the operator said sparingly, so the "
                    "gap runs to 60 m and past it wherever the lamp is "
                    "measurably still in view of its neighbour"},
    "Plains":      {"min_m": 55.0, "max_m": 90.0, "why":
                    "open and flat; tall grass hides ground detail, not the "
                    "horizon, so a lamp is visible a long way"},
    "AshLands":    {"min_m": 55.0, "max_m": 90.0, "why":
                    "open, and lit by its own lava glow"},
    "Ocean":       {"min_m": 30.0, "max_m": 60.0, "why":
                    "a road sample reading Ocean is a causeway or a ford; "
                    "spacing is the trunk default and the water guard decides "
                    "whether a lamp can stand there at all"},
}
# A biome the policy does not name is refused rather than defaulted, because a
# default here is a silent decision about where the operator's lamps go.
DEFAULT_POLICY = None


# --------------------------------------------------------------------------
# geometry off the instruments
# --------------------------------------------------------------------------

def seat_offset_m(prefab: str = LAMP_PREFAB) -> float:
    """How far ABOVE the walking surface the prefab's pivot goes, asked of
    `fixtures.fixture_box` rather than restated.

    `fixture_box(prefab, x, y, z, yaw)` returns the world AABB with `y` treated
    as the surface it stands on, so `ymin - y` is the solid's own offset from
    the pivot and its negation is the lift that puts the solid's underside on
    the ground.  Asking the audit's own box builder is the rule: a closed form
    over half-extents was wrong at yaw 45 in `waypoints.py` and a restated
    literal is a copy that can go stale.
    """
    import fixtures  # noqa: PLC0415
    box = fixtures.fixture_box(prefab, 0.0, 0.0, 0.0, 0.0)
    return -box[2]


def lamp_unit(x: float, z: float, surface_y: float, yaw_deg: float) -> dict:
    """One lamp: prefab, world position, yaw, and the seat arithmetic shown."""
    off = seat_offset_m()
    return {"prefab": LAMP_PREFAB, "x": round(x, 4),
            "y": round(surface_y + off, 4), "z": round(z, 4),
            "yaw_deg": round(yaw_deg % 360.0, 3),
            "surface_y": round(surface_y, 4), "seat_offset_m": round(off, 5),
            "flame_y": round(surface_y + FLAME_ABOVE_SURFACE_M, 4)}


def everburning_payload() -> tuple[str, dict]:
    """`(base64, the decoded map)` that makes this lamp's own Fireplace
    everburning.

    Three ZDO INTs, and each one is load-bearing: `LoadFields` returns at once
    without `HasFields`, skips the component without `HasFieldsFireplace`, and
    has nothing to assign without `Fireplace.m_infiniteFuel`.  INTs and not a
    bool section because `ZDO::GetBool` is `ZDOExtraData::GetInt(...) != 0` --
    MEASURED in IL, see the module docstring.
    """
    import data_entry  # noqa: PLC0415
    keys = {"HasFields": 1, "HasFieldsFireplace": 1,
            "Fireplace.m_infiniteFuel": 1}
    entry = data_entry.DataEntry()
    for name, value in keys.items():
        entry.ints[data_entry.stable_hash(name)] = value
    blob = entry.encode()
    # ROUND-TRIP THE PAYLOAD BEFORE IT IS SENT.  `data_entry.decode` is the
    # read side of the same wire format the server will use, so a payload that
    # does not decode to what was asked for is a payload the game will parse
    # into something else -- and an unparseable `data=` is accepted silently by
    # `spawn_object`, which places the piece with default fields.
    back = data_entry.decode(blob)
    got = {data_entry.HASH_TO_NAME.get(h, f"0x{h & 0xFFFFFFFF:08x}"): v
           for h, v in back.ints.items()}
    if got != keys:
        raise SystemExit(
            f"REFUSING: the everburning payload does not round-trip. asked "
            f"{keys}, decoded {got}. A `data=` the game cannot read is spawned "
            f"as a torch with default fields, i.e. one that burns out, and "
            f"nothing reports it.")
    return blob, keys


# --------------------------------------------------------------------------
# the segments, out of the ledger
# --------------------------------------------------------------------------

@dataclass
class Segment:
    """One built road segment as the LEDGER holds it."""

    name: str
    short: str
    file_line: int
    seq: int
    half_width_m: float
    nodes: list[tuple[float, float]]      # (x, z), centreline order
    cum: list[float]                      # arclength at each node
    length_m: float


def segments_from_ledger(world: str = WORLD,
                         actor: str = ACTOR) -> dict[str, Segment]:
    """Every built segment's LATEST centreline, keyed by short id.

    LATEST IN FILE ORDER, and both halves matter.  A segment is re-written when
    it is re-graded -- T4 has four `terrain_write` records -- and only the last
    one describes the ground that is there now.  File order rather than `seq`
    because the chain forked at file line 47 and carries duplicate seq labels,
    so `seq` is not a total order on this artefact; `applied.py` makes the same
    choice for the same reason.

    This is also the answer to T4 being re-routed under me: the centreline is
    read here, at the moment of siting, from the record that is currently last.
    """
    from writer import Ledger  # noqa: PLC0415
    led = Ledger.open(world, actor=actor)
    latest: dict[str, tuple[int, dict]] = {}
    for line, rec in enumerate(led.records()):
        if rec.get("op") != "terrain_write":
            continue
        p = rec["params"]
        if p.get("role") != "road_segment":
            continue
        latest[p["name"]] = (line, rec)
    out: dict[str, Segment] = {}
    for name, (line, rec) in latest.items():
        prof = rec["params"]["profile"]
        nodes = [(float(n[0]), float(n[1])) for n in prof["nodes"]]
        cum = [0.0]
        for k in range(1, len(nodes)):
            cum.append(cum[-1] + math.dist(nodes[k - 1], nodes[k]))
        short = name.split("_")[1] if name.startswith("road_") else name
        out[short] = Segment(name=name, short=short, file_line=line,
                             seq=rec["seq"],
                             half_width_m=float(prof["half_width_m"]),
                             nodes=nodes, cum=cum, length_m=cum[-1])
    return out


def resample(seg: Segment, step: float = SIGHT_STEP_M) -> list[tuple[float, float, float]]:
    """`(s, x, z)` every `step` metres along the centreline.

    Linear in arclength, which is what the record's own
    `interp: linear_arclength` says the profile is.  Sampling the NODES instead
    would sample unevenly -- node spacing runs from 0.3 m on a bend to 2 m on a
    straight -- and an uneven station list makes "30 m apart" a lie.
    """
    out: list[tuple[float, float, float]] = []
    total = seg.length_m
    k = 0
    s = 0.0
    while s <= total + 1e-9:
        while k + 2 < len(seg.cum) and seg.cum[k + 1] < s:
            k += 1
        span = seg.cum[k + 1] - seg.cum[k]
        t = 0.0 if span <= 0 else (s - seg.cum[k]) / span
        x0, z0 = seg.nodes[k]
        x1, z1 = seg.nodes[k + 1]
        out.append((s, x0 + (x1 - x0) * t, z0 + (z1 - z0) * t))
        s += step
    return out


def zones_for(stations: list[tuple[float, float, float]],
              pad_m: float = 12.0) -> list[tuple[int, int]]:
    """Every zone the corridor plus `pad_m` of slack touches.

    The slack covers the lateral offset, the shoulder and the neighbouring
    zone's shared lattice row.  `applied.surface_at` needs the patch for EVERY
    zone one of the four samples under a point lands in, and a missing patch
    makes it return None -- which reads as "no ground here" and is not.
    """
    zs: set[tuple[int, int]] = set()
    for _s, x, z in stations:
        for dx in (-pad_m, 0.0, pad_m):
            for dz in (-pad_m, 0.0, pad_m):
                zs.add(tcdata.zone_of(x + dx, z + dz))
    # The lattice's boundary row is SHARED between two zones, so a sample on a
    # boundary lives in both and `zone_of` picks one. Ask for the ring.
    ring = set(zs)
    for zx, zz in zs:
        for dzx in (-1, 0, 1):
            for dzz in (-1, 0, 1):
                ring.add((zx + dzx, zz + dzz))
    return sorted(ring)


# --------------------------------------------------------------------------
# the surface, the biome, and the sightline
# --------------------------------------------------------------------------

class Ground:
    """The applied surface and the biome, both read off PatchScan zone patches
    plus the ledger's own blobs.

    One object because the two questions share the patch set and decoding it
    twice costs a Unity boot.
    """

    def __init__(self, zones: list[tuple[int, int]], out: Path,
                 live: appliedmod.Applied):
        self.patches = zone_patches_own(zones, out)
        self.live = live
        self._surf: dict[tuple[float, float], float | None] = {}

    def surface(self, x: float, z: float) -> float | None:
        key = (round(x, 3), round(z, 3))
        got = self._surf.get(key, "miss")
        if got != "miss":
            return got  # type: ignore[return-value]
        res = self.live.surface_at(self.patches, x, z)
        val = None if res is None else float(res[0])
        self._surf[key] = val
        return val

    def biome(self, x: float, z: float) -> str:
        """`WorldGenerator.GetBiome` at the NEAREST 1 m sample.

        Nearest and not blended, because a biome is a label: the mean of
        Meadows and BlackForest is not a biome and averaging their codes would
        produce Mountain.
        """
        from field import BIOME_NAMES  # noqa: PLC0415
        sx, sz = int(round(x)), int(round(z))
        zx, zz = tcdata.zone_of(sx, sz)
        patch = self.patches.get(f"z_{zx}_{zz}")
        if patch is None:
            return "Unknown"
        cx, cz = tcdata.zone_centre(zx, zz)
        gx, gy = tcdata.vertex_mask_index(cx, cz, sx, sz)
        if not (0 <= gx < tcdata.PITCH and 0 <= gy < tcdata.PITCH):
            return "Unknown"
        return BIOME_NAMES.get(int(patch.biomes[gy * patch.n + gx]), "Unknown")


def zone_patches_own(zones: list[tuple[int, int]], out: Path) -> dict:
    """`ribbon.zone_patches`' request geometry, in THIS agent's sandbox.

    Not a call to `ribbon.zone_patches` because that function hard-codes
    `SANDBOX=/tmp/patchscan/roademit`, and its own docstring records what a
    shared sandbox did: the clean step failed on another uid's BepInEx tree,
    the scan never ran, the stale non-empty output passed the existence check
    and the caller silently got the previous run's zones.  Three writers are
    live on this ledger tonight, so the sandbox is this actor's and the
    completeness check is re-asserted after the scan -- a silent fallback to
    old ground is worse than a crash.
    """
    import os
    import subprocess

    out.parent.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(JUMPSTART / "terraform"))
    import heights as patchheights  # noqa: PLC0415

    if out.exists() and out.stat().st_size:
        try:
            cached = patchheights.load(out)
            if all(f"z_{zx}_{zz}" in cached for zx, zz in zones):
                return cached
        except Exception:
            pass
    req = out.with_suffix(".req.tsv")
    lines = []
    for zx, zz in zones:
        cx, cz = tcdata.zone_centre(zx, zz)
        lines.append(f"z_{zx}_{zz}\t{cx:g}\t{cz:g}\t32.5\t1")
    req.write_text("\n".join(lines) + "\n")
    env = {**os.environ,
           "VH_SRC": "/media/big4/projects/game/valheim/Ulfsland/data/bepinex",
           "SEED": SEED, "REQ": str(req), "OUT": str(out),
           "SANDBOX": f"/tmp/patchscan/{ACTOR.lower()}"}
    proc = subprocess.run(["bash", str(JUMPSTART / "blueprints" / "run_patchscan.sh")],
                          env=env, capture_output=True, text=True)
    if not out.exists() or out.stat().st_size == 0:
        raise SystemExit(f"patchscan produced nothing:\n{proc.stderr[-4000:]}")
    got = patchheights.load(out)
    missing = [(zx, zz) for zx, zz in zones if f"z_{zx}_{zz}" not in got]
    if missing:
        raise SystemExit(
            f"patchscan did not produce {len(missing)} requested zones "
            f"{missing[:8]}: the output file exists and is non-empty, so it is "
            f"a PREVIOUS run's data. Refusing to site lamps against ground "
            f"that does not cover them.\n{proc.stderr[-2000:]}")
    return got


def sightline(surf: list[float | None], i: int, direction: int,
              flame_above: float = FLAME_ABOVE_SURFACE_M,
              eye: float = EYE_M, cap_m: float = SIGHT_CAP_M,
              step: float = SIGHT_STEP_M) -> tuple[float, str]:
    """`(metres, why it stopped)` -- the CONTIGUOUS run over which a lamp at
    station `i` stays in view walking `direction`.

    The running maximum of the ground's slope from the flame is the horizon.  A
    station is visible while the slope to the WALKER'S EYE beats it; the first
    station that does not ends the run, because what a walker experiences is
    the stretch over which the lamp is continuously in view, not the farthest
    point from which some line of sight exists.

    `>=` and not `>` on the block test: a station exactly on the horizon is
    grazing, and calling a grazing sightline visible is the kind of rounding
    that turns into "the report said 60 m and I could not see it".
    """
    base = surf[i]
    if base is None:
        return 0.0, "lamp station has no measured surface"
    flame = base + flame_above
    horizon = -math.inf
    reached = 0.0
    n = len(surf)
    steps = int(cap_m / step)
    for t in range(1, steps + 1):
        j = i + direction * t
        if j < 0 or j >= n:
            return reached, "segment end"
        sj = surf[j]
        if sj is None:
            return reached, "unmeasured surface ahead"
        dist = t * step
        if (sj + eye - flame) / dist < horizon:
            return reached, "terrain"
        reached = dist
        # The OCCLUDER is the ground, with no eye height added: the ridge
        # blocks at its own height. Updated after the visibility test so the
        # station being tested cannot occlude itself.
        horizon = max(horizon, (sj - flame) / dist)
    return reached, "cap"


# --------------------------------------------------------------------------
# keep-outs
# --------------------------------------------------------------------------

class KeepOut:
    """Everywhere a lamp may not stand, with the reason it may not.

    Every member is asked of the instrument that owns it.  Nothing here is a
    radius this module invented.
    """

    def __init__(self, zones: list[tuple[int, int]], live: appliedmod.Applied,
                 actor: str = ACTOR):
        import yaml  # noqa: PLC0415
        from writer import Ledger  # noqa: PLC0415

        sites_path = JUMPSTART / "settlements" / "sites.yaml"
        site_list = ([dict(s) for s in
                      yaml.safe_load(sites_path.read_text())["sites"]]
                     if sites_path.exists() else [])
        led = Ledger.open(WORLD, actor=actor)
        self.pads = ribbon.pad_keepouts_from_chain(site_list, led.records(),
                                                   ledger=led)
        if self.pads.undecodable:
            raise SystemExit(
                f"REFUSING: {len(self.pads.undecodable)} pad terrain_write "
                f"entry(ies) could not be decoded, so the ground those pads "
                f"levelled is unknown and a lamp could land on a floor. "
                f"{self.pads.undecodable[:4]}")
        self.poi = ribbon.poi_standoff_discs(zones)
        prot = ribbon.protected_piece_positions(zones)
        self.pieces = prot["pieces"]
        self.discs = prot["discs"]
        self.prot_by_site = prot["by_site"]
        self.without_pieces = prot["without_pieces"]
        self.live = live

    def why_not(self, x: float, z: float) -> str | None:
        """The reason this spot is unusable, or None.

        A string and not a bool: "refused" is not something an operator can
        act on, and the refusal list in the report is only useful if each row
        names its cause.
        """
        sid = self.pads.contains(x, z)
        if sid is not None:
            return f"inside site pad footprint {sid}"
        for cx, cz, r in self.poi:
            if (x - cx) ** 2 + (z - cz) ** 2 <= r * r:
                return f"inside POI stand-off {r:.1f} m of ({cx:.1f}, {cz:.1f})"
        for cx, cz, r in self.discs:
            if (x - cx) ** 2 + (z - cz) ** 2 <= r * r:
                return (f"inside a protected structure's own recorded extent "
                        f"{r:.1f} m of ({cx:.1f}, {cz:.1f})")
        for px, pz in self.pieces:
            if (x - px) ** 2 + (z - pz) ** 2 <= PIECE_CLEAR_M ** 2:
                return (f"within {PIECE_CLEAR_M:.2f} m of an existing piece at "
                        f"({px:.1f}, {pz:.1f})")
        foreign = self.live.foreign_at(x, z)
        if foreign is not None:
            return (f"ground here is authored by {foreign.get('name')} "
                    f"seq {foreign.get('seq')} role {foreign.get('role')}, "
                    f"not the road")
        return None


# --------------------------------------------------------------------------
# siting
# --------------------------------------------------------------------------

@dataclass
class Candidate:
    s: float
    x: float
    z: float
    side: int
    lamp_x: float
    lamp_z: float
    surface_y: float
    yaw_deg: float
    biome: str
    fwd_m: float
    back_m: float
    fwd_why: str
    back_why: str
    relief_m: float = 0.0

    @property
    def score(self) -> float:
        return min(self.fwd_m, self.back_m)


@dataclass
class Plan:
    seg: str
    lamps: list[dict] = field(default_factory=list)
    refusals: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _tangent(stations: list[tuple[float, float, float]], i: int) -> tuple[float, float]:
    a = max(0, i - 2)
    b = min(len(stations) - 1, i + 2)
    dx = stations[b][1] - stations[a][1]
    dz = stations[b][2] - stations[a][2]
    n = math.hypot(dx, dz)
    return (1.0, 0.0) if n == 0 else (dx / n, dz / n)


def site(seg: Segment, ground: Ground, keep: KeepOut,
         eye: float = EYE_M) -> Plan:
    """Lamps along one segment, each at the best-sightline spot in its window.

    The walk is greedy from the start of the segment and that is deliberate:
    the alternative, a global optimum over the whole segment, buys a few metres
    of average sightline and makes every lamp's position depend on every other
    one, so a re-route of the far end moves the near end.  A greedy walk is
    also what a road builder does.
    """
    plan = Plan(seg=seg.short)
    stations = resample(seg)
    surf = [ground.surface(x, z) for _s, x, z in stations]
    holes = sum(1 for v in surf if v is None)
    if holes:
        plan.notes.append(
            f"{holes} of {len(surf)} centreline stations have no measured "
            f"applied surface; sightlines terminate there rather than "
            f"assuming flat ground")

    # Per-station candidate on each side, built once.  The sightline is
    # computed from the LAMP's own surface, so the two sides can score
    # differently -- and on a side-cut road they do.
    def build(i: int, side: int) -> tuple[Candidate | None, str | None]:
        s, cx, cz = stations[i]
        tx, tz = _tangent(stations, i)
        # Left of travel is (-tz, tx); side=+1 is left, -1 is right.
        off = seg.half_width_m + SHOULDER_STANDOFF_M
        lx = cx + side * (-tz) * off
        lz = cz + side * (tx) * off
        # SEAT ON THE DEEPEST GROUND UNDER THE FOOTPRINT, NOT ON THE CENTRE.
        # The lamp's foot is a 0.23 x 0.24 m rectangle and the shoulder is
        # graded, not flat: MEASURED on S1, seating on the centre sample left
        # 4.93 cm of air under the downhill corner. Air under a foot is the
        # floating defect in miniature; a centimetre of burial is invisible.
        # So the seat takes the minimum over the centre and the four corners
        # and the residual is reported either way by `seat_check`.
        sy, relief = seat_surface(ground, lx, lz)
        if sy is None:
            return None, "footprint reaches ground with no measured surface"
        if sy <= WATER_LEVEL_M + FREEBOARD_M:
            return None, (f"surface {sy:.3f} m is at or under the water plane "
                          f"+ {FREEBOARD_M} m freeboard")
        if relief > FOOTPRINT_RELIEF_MAX_M:
            return None, (f"ground rises and falls {relief:.3f} m across the "
                          f"lamp's own footprint, over the "
                          f"{FOOTPRINT_RELIEF_MAX_M} m limit; a lamp here "
                          f"stands in a bank")
        biome = ground.biome(lx, lz)
        # Yaw: face the lamp across the road, so its shaft's flat faces read
        # as a roadside post rather than a random rotation. The light is a
        # POINT light, so yaw does not change what it lights -- measured, and
        # that is why this is a cosmetic choice and is allowed to be one.
        yaw = math.degrees(math.atan2(side * -tz, side * tx))
        fwd, fwhy = sightline(surf, i, +1, eye=eye)
        back, bwhy = sightline(surf, i, -1, eye=eye)
        return Candidate(s=s, x=cx, z=cz, side=side, lamp_x=lx, lamp_z=lz,
                         surface_y=sy, yaw_deg=yaw, biome=biome,
                         fwd_m=fwd, back_m=back, fwd_why=fwhy, back_why=bwhy,
                         relief_m=relief), None

    last_s: float | None = None
    # The arclength the current window is measured FROM.  It is the previous
    # lamp when there is one and the window's own start when there is not --
    # which matters after an advance past a pad or a bridge deck, because an
    # absolute test would then measure the first lamp's window from the start
    # of the segment and refuse the whole remainder.
    i_from = 0
    guard = 0
    while True:
        guard += 1
        if guard > len(stations) + 8:
            plan.notes.append("siting walk hit its own iteration guard")
            break
        # The window: every station whose gap from the previous lamp is legal
        # for the biome AT THAT STATION.  The biome is read per candidate, so a
        # segment that crosses from Meadows into Black Forest tightens as it
        # goes rather than using the segment's opening biome throughout.
        window: list[Candidate] = []
        refused_here: list[dict] = []
        base_s = last_s if last_s is not None else stations[i_from][0]
        for i in range(i_from, len(stations)):
            s = stations[i][0]
            for side in (+1, -1):
                cand, why = build(i, side)
                if cand is None:
                    if why:
                        refused_here.append(
                            {"s_m": round(s, 1), "side": side, "reason": why})
                    continue
                pol = BIOME_POLICY.get(cand.biome, DEFAULT_POLICY)
                if pol is None:
                    refused_here.append(
                        {"s_m": round(s, 1), "x": round(cand.lamp_x, 2),
                         "z": round(cand.lamp_z, 2),
                         "reason": f"biome {cand.biome} has no published "
                                   f"spacing policy; refusing rather than "
                                   f"defaulting"})
                    continue
                if not _biome_allows(last_s, base_s, s, cand, pol):
                    continue
                bad = keep.why_not(cand.lamp_x, cand.lamp_z)
                if bad:
                    refused_here.append(
                        {"s_m": round(s, 1), "x": round(cand.lamp_x, 2),
                         "z": round(cand.lamp_z, 2), "side": side,
                         "reason": bad})
                    continue
                window.append(cand)
            # Stop widening once past the loosest max for any biome present.
            if s - base_s > 90.0:
                break
        if not window:
            # NOTHING LEGAL IN THE WINDOW.  Advance past it rather than
            # stopping the whole segment: a settlement pad or 120 m of ground
            # under the water plane must not cost the kilometre behind it.
            #
            # AND BREAK THE CHAIN, WHICH IS THE BUG THIS COMMENT EXISTS FOR.
            # The first version advanced `i_from` and left `last_s` pointing
            # at the lamp it had just failed to chain from, so the window's
            # 90 m ceiling stayed nailed to that lamp: once `i_from` passed
            # `last_s + 90` the scan range was EMPTY and every further pass
            # examined nothing while dutifully logging that it had advanced.
            # MEASURED on S8, whose last 212.8 m went unlit with ONE recorded
            # refusal to explain it -- a walk that reports progress it is not
            # making is worse than one that stops.
            #
            # A break is the honest model: the run of lamps ENDS here, and the
            # next lamp starts a fresh run whose first gap is not a spacing
            # decision and is recorded as None rather than as a 212 m gap.
            step_from = i_from
            i_from = _advance(stations, last_s, i_from)
            if i_from <= step_from or i_from >= len(stations):
                plan.refusals.extend(refused_here)
                break
            plan.refusals.extend(refused_here)
            plan.notes.append(
                f"no legal lamp spot in the window from "
                f"{'the start' if last_s is None else f'{last_s:.0f} m'}; "
                f"chain broken, window restarted at "
                f"{stations[i_from][0]:.0f} m")
            last_s = None
            continue
        plan.refusals.extend(refused_here)
        # MAXIMISE THE MINIMUM OF THE TWO DIRECTIONS.  Ties broken by the sum
        # (a genuinely better spot), then by the LATER station, which spends
        # fewer lamps for the same visibility.
        best = max(window, key=lambda c: (round(c.score, 2),
                                          round(c.fwd_m + c.back_m, 2), c.s))
        rec = lamp_unit(best.lamp_x, best.lamp_z, best.surface_y, best.yaw_deg)
        rec.update({
            "s_m": round(best.s, 2),
            "gap_m": None if last_s is None else round(best.s - last_s, 2),
            "side": "left" if best.side > 0 else "right",
            "biome": best.biome,
            "sight_fwd_m": round(best.fwd_m, 1),
            "sight_back_m": round(best.back_m, 1),
            "sight_min_m": round(best.score, 1),
            # A DIRECTION THAT RAN OUT OF SEGMENT IS A LOWER BOUND, NOT A
            # MEASUREMENT.  MEASURED: the last lamp on T12 sits 2 m from the
            # segment's end, so its forward sightline is 1 m -- and the road
            # does not end there, it becomes T3.  Scoring that as a 1 m
            # sightline is the instrument answering a question about the
            # segment's extent while appearing to answer one about the
            # terrain, which is this project's signature defect. The number is
            # kept because it is what was measured; the flag is what stops it
            # being read as terrain.
            "sight_min_is_lower_bound":
                best.fwd_why == "segment end" or best.back_why == "segment end",
            "sight_fwd_limit": best.fwd_why,
            "sight_back_limit": best.back_why,
            "window_size": len(window),
            "footprint_relief_m": round(best.relief_m, 4),
            "window_best_minus_median": round(
                best.score - _median([c.score for c in window]), 2),
        })
        plan.lamps.append(rec)
        last_s = best.s
        i_from = int(best.s / SIGHT_STEP_M) + 1
        if i_from >= len(stations):
            break
        # The tail: if what is left is shorter than the biome minimum, there is
        # no legal spot and the segment ends. Recorded so the report can say
        # how much road follows the last lamp.
        if seg.length_m - best.s < 30.0:
            break
    if plan.lamps:
        plan.notes.append(
            f"tail after the last lamp: "
            f"{seg.length_m - plan.lamps[-1]['s_m']:.1f} m")
    return plan


def _biome_allows(last_s: float | None, base_s: float, s: float,
                  cand: Candidate, pol: dict) -> bool:
    """Is a lamp at `s` legal given the previous one and this biome's policy?

    THE STRETCH RULE IS THE HONEST HALF.  An open biome may exceed the
    operator's 60 m upper bound -- they asked for lamps to be used sparingly
    there -- but only where the lamp can still SEE back to its neighbour:
    `sight_back >= gap`.  Otherwise "sparingly" is a word for a dark road.  A
    gap at or under 60 m needs no such proof; that is inside the operator's own
    bound.
    """
    if last_s is None:
        # The first lamp: anywhere in the first `max_m`, and it still has to
        # earn its place on sightline like the rest.
        return s - base_s <= pol["max_m"]
    gap = s - last_s
    if gap < pol["min_m"]:
        return False
    if gap <= 60.0:
        return gap <= pol["max_m"]
    if gap > pol["max_m"]:
        return False
    return cand.back_m >= gap


def _advance(stations, last_s: float | None, i_from: int) -> int:
    """Skip the window that held nothing legal."""
    base = 0.0 if last_s is None else last_s
    target = base + 30.0
    nxt = max(i_from + 30, int(target / SIGHT_STEP_M) + 1)
    return min(nxt, len(stations))


def _median(vals: list[float]) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


# --------------------------------------------------------------------------
# verification: what a player walking at night actually sees
# --------------------------------------------------------------------------

def _causes(refusals: list[dict]) -> dict[str, int]:
    """Refusals grouped by CAUSE rather than listed one by one.

    A refusal list with 687 rows is not readable, and "687 refused" without
    causes is not actionable.  The cause is the leading clause of the reason,
    which is why every reason in this module is written cause-first.
    """
    out: dict[str, int] = {}
    for r in refusals:
        reason = str(r.get("reason", ""))
        for marker in ("inside site pad footprint", "inside POI stand-off",
                       "inside a protected structure's own recorded extent",
                       "within", "ground here is authored by",
                       "ground rises and falls",
                       "surface", "footprint reaches ground",
                       "biome"):
            if reason.startswith(marker):
                out[marker] = out.get(marker, 0) + 1
                break
        else:
            out["other"] = out.get("other", 0) + 1
    return out


def dark_stretches(seg: Segment, plan: Plan, ground: Ground,
                   eye: float = EYE_M) -> dict:
    """The longest stretch of centreline with NO lamp in view, MEASURED.

    Not derived from the spacing.  A station has a lamp in view when some lamp
    is within its own measured sightline of that station, tested against the
    applied surface between the two -- the same occlusion test the siting used,
    asked the other way round.  Spacing arithmetic would answer a question
    about the plan; this answers the operator's question, which is about
    walking the road.
    """
    stations = resample(seg)
    surf = [ground.surface(x, z) for _s, x, z in stations]
    lit = [False] * len(stations)
    for lamp in plan.lamps:
        i = min(int(round(lamp["s_m"] / SIGHT_STEP_M)), len(stations) - 1)
        # The lamp's flame height is measured at the LAMP's own surface, which
        # is the shoulder, not the centreline station it is indexed by.
        flame = lamp["surface_y"] + FLAME_ABOVE_SURFACE_M
        for direction in (+1, -1):
            horizon = -math.inf
            for t in range(0, int(SIGHT_CAP_M / SIGHT_STEP_M) + 1):
                j = i + direction * t
                if j < 0 or j >= len(stations):
                    break
                sj = surf[j]
                if sj is None:
                    break
                if t == 0:
                    lit[j] = True
                    continue
                dist = t * SIGHT_STEP_M
                if (sj + eye - flame) / dist < horizon:
                    break
                lit[j] = True
                horizon = max(horizon, (sj - flame) / dist)
    runs = []
    run = 0
    start = 0.0
    for idx, ok in enumerate(lit):
        if ok:
            if run:
                runs.append((start, run * SIGHT_STEP_M))
            run = 0
        else:
            if not run:
                start = stations[idx][0]
            run += 1
    if run:
        runs.append((start, run * SIGHT_STEP_M))
    # WHERE A DARK RUN FALLS CHANGES WHAT IT MEANS, and reporting one worst
    # number hides the difference.  A dark run at a segment's END is the
    # approach into a settlement, whose own pads refused the lamp and whose
    # buildings carry their own light; a dark run in the MIDDLE of a segment
    # is unlit road and is the defect the operator asked to have removed.  So
    # both are reported and the middle one is the honest headline.
    def where(a: float, b: float) -> str:
        if a <= 1.0:
            return "segment start (junction approach)"
        if a + b >= seg.length_m - 1.0:
            return "segment end (junction approach)"
        return "mid-segment"

    tagged = [(a, b, where(a, b)) for a, b in runs]
    worst = max(tagged, key=lambda r: r[1]) if tagged else None
    mids = [r for r in tagged if r[2] == "mid-segment"]
    worst_mid = max(mids, key=lambda r: r[1]) if mids else None
    return {"stations": len(stations),
            "stations_with_a_lamp_in_view": sum(1 for v in lit if v),
            "dark_runs": len(runs),
            "worst_dark_run_m": round(worst[1], 1) if worst else 0.0,
            "worst_dark_run_starts_at_m": round(worst[0], 1) if worst else None,
            "worst_dark_run_where": worst[2] if worst else None,
            "worst_mid_segment_dark_run_m":
                round(worst_mid[1], 1) if worst_mid else 0.0,
            "worst_mid_segment_starts_at_m":
                round(worst_mid[0], 1) if worst_mid else None,
            "dark_runs_over_20m": [
                {"from_m": round(a, 1), "length_m": round(b, 1), "where": w}
                for a, b, w in tagged if b > 20.0][:12]}


def footprint_half_extents(prefab: str = LAMP_PREFAB) -> tuple[float, float]:
    """The prefab's own XZ half-extents, from the audit's box builder."""
    import fixtures  # noqa: PLC0415
    box = fixtures.fixture_box(prefab, 0.0, 0.0, 0.0, 0.0)
    return max(abs(box[0]), abs(box[1])), max(abs(box[4]), abs(box[5]))


def seat_surface(ground: "Ground", x: float,
                 z: float) -> tuple[float | None, float]:
    """`(lowest applied surface under the footprint, its relief)`.

    Nine probes -- centre plus the eight points of the footprint rectangle --
    each at its own XZ, which is the rule that got 32 of 36 portal ends flush:
    probe where the object actually is, not where its station is.  `None` when
    any probe is uncovered, because a footprint partly off the measured ground
    is a seat nobody has measured, and the relief comes back with it because
    seating on the lowest point buys "never floats" at the price of exactly
    that much burial -- so the caller has to be able to see the price.
    """
    hx, hz = footprint_half_extents()
    lo: float | None = None
    hi: float | None = None
    for dx in (-hx, 0.0, hx):
        for dz in (-hz, 0.0, hz):
            sy = ground.surface(x + dx, z + dz)
            if sy is None:
                return None, 0.0
            lo = sy if lo is None else min(lo, sy)
            hi = sy if hi is None else max(hi, sy)
    return lo, (hi - lo if lo is not None and hi is not None else 0.0)


def seat_check(plan: Plan, ground: Ground) -> dict:
    """How far every lamp's FOOT is from the applied surface under its own
    footprint.

    Probed at the lamp's own XZ and at the four corners of its measured
    footprint, and the seat uses the DEEPEST of those: a lamp seated on the
    centre of a sloping sample shows a sliver of air on the downhill corner,
    and a sliver of air is the floating defect in miniature.  Reported as the
    worst gap either way so the number is a measurement and not a target.
    """
    import fixtures  # noqa: PLC0415
    box = fixtures.fixture_box(LAMP_PREFAB, 0.0, 0.0, 0.0, 0.0)
    hx, hz = footprint_half_extents()
    rows = []
    for lamp in plan.lamps:
        foot = lamp["y"] + box[2]
        gaps = []
        for dx in (-hx, 0.0, hx):
            for dz in (-hz, 0.0, hz):
                sy = ground.surface(lamp["x"] + dx, lamp["z"] + dz)
                if sy is not None:
                    gaps.append(foot - sy)
        rows.append({"s_m": lamp["s_m"], "x": lamp["x"], "z": lamp["z"],
                     "gap_at_centre_m": round(foot - lamp["surface_y"], 4),
                     "max_air_under_foot_m": round(max(gaps), 4) if gaps else None,
                     "max_burial_m": round(-min(gaps), 4) if gaps else None})
    airs = [r["max_air_under_foot_m"] for r in rows
            if r["max_air_under_foot_m"] is not None]
    buried = [r["max_burial_m"] for r in rows if r["max_burial_m"] is not None]
    return {"lamps": len(rows),
            "worst_air_under_foot_m": round(max(airs), 4) if airs else 0.0,
            "worst_burial_m": round(max(buried), 4) if buried else 0.0,
            "footprint_half_extents_m": [round(hx, 4), round(hz, 4)],
            "rows": rows}


# --------------------------------------------------------------------------
# the wire, and the live write
# --------------------------------------------------------------------------

def wire_lines(plan: Plan, payload: str) -> list[str]:
    """One `spawn_object` per lamp.

    `pos=` is z,x,y and `rot=` is euler y,x,z -- three component orders in one
    command, all MEASURED from IL, and the argument builders are
    `to_rcon_plan`'s rather than this module's f-strings.
    """
    import to_rcon_plan  # noqa: PLC0415
    out = []
    for lamp in plan.lamps:
        out.append(
            f"spawn_object {lamp['prefab']}"
            f" pos={to_rcon_plan.pos_arg(lamp['x'], lamp['y'], lamp['z'])}"
            f" rot={to_rcon_plan.rot_arg(0.0, lamp['yaw_deg'], 0.0)}"
            f" {to_rcon_plan.FROM_ORIGIN} data={payload}")
    return out


def expect_for(plan: Plan) -> dict:
    """One `objects_count` probe per lamp: exactly 1 of this prefab within
    1.5 m of where it was asked for.

    Per lamp and not one big cylinder: a segment is hundreds of metres long, a
    radius that covered it would also cover the neighbouring segment's lamps at
    every junction, and a count that can be satisfied by somebody else's object
    is not a postcondition.  1.5 m because the lamps are 30 m apart, so the
    discs cannot overlap and each probe answers about one lamp.
    """
    return {"prefab_count": [
        {"prefab": LAMP_PREFAB, "pos": [lamp["x"], lamp["z"]],
         "max": 1.5, "count": 1, "tolerance": 0}
        for lamp in plan.lamps]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--segments", default="",
                    help="comma-separated short ids, e.g. T1,T3; default all")
    ap.add_argument("--plan", action="store_true", help="site and report only")
    ap.add_argument("--place", action="store_true",
                    help="site and WRITE to the live world, one record per "
                         "segment plus a save with a verified postcondition")
    ap.add_argument("--dry", action="store_true",
                    help="build and validate the record without appending")
    ap.add_argument("--eye-sweep", action="store_true",
                    help="re-site at eye 1.5/1.7/1.9 m and print the spread")
    ap.add_argument("--json", default="", help="write the report here")
    ap.add_argument(
        "--refuse-file-line", action="append", default=[], metavar="SEG=LINE",
        help="REFUSE if a segment's latest centreline is still this ledger "
             "file line. Use it to make a peer's 'that record is dead' "
             "mechanical instead of remembered: `--refuse-file-line T4=1783` "
             "aborts rather than siting T4 against the pre-reroute geometry.")
    args = ap.parse_args()

    segs = segments_from_ledger()
    want = ([s.strip() for s in args.segments.split(",") if s.strip()]
            or sorted(segs))
    missing = [w for w in want if w not in segs]
    if missing:
        raise SystemExit(f"no such built segment(s): {missing}; "
                         f"have {sorted(segs)}")

    # THE DEAD-RECORD GUARD.  `HutsInRoad` re-routed T4 and told me file line
    # 1783 was dead; "I will remember not to use it" is the class of promise
    # that fails, so the promise is an assertion instead.  It reads the same
    # LATEST-IN-FILE-ORDER record the siting will use, so it cannot disagree
    # with the thing it is guarding.
    for spec in args.refuse_file_line:
        if "=" not in spec:
            raise SystemExit(f"--refuse-file-line wants SEG=LINE, got {spec!r}")
        short, line = spec.split("=", 1)
        seg = segs.get(short.strip())
        if seg is None:
            raise SystemExit(f"--refuse-file-line names {short!r}, which is "
                             f"not a built segment; have {sorted(segs)}")
        if seg.file_line == int(line):
            raise SystemExit(
                f"REFUSING: {short}'s latest centreline is still ledger file "
                f"line {seg.file_line} (seq {seg.seq}), which was declared "
                f"superseded. Siting against a re-routed segment's OLD "
                f"geometry would put lamps where the road no longer is -- and "
                f"on T4 specifically, inside buildings the re-route exists to "
                f"avoid. Wait for the new terrain_write.")
        print(f"dead-record guard: {short} is at file line {seg.file_line} "
              f"(seq {seg.seq}), not the refused {line}")

    live = appliedmod.Applied(actor=ACTOR)
    payload, keys = everburning_payload()
    print(f"lamp {LAMP_PREFAB}, seat offset +{seat_offset_m():.5f} m, "
          f"flame +{FLAME_ABOVE_SURFACE_M:.5f} m, everburning payload "
          f"{keys} = {payload}")

    report = {"lamp": LAMP_PREFAB, "seat_offset_m": seat_offset_m(),
              "flame_above_surface_m": FLAME_ABOVE_SURFACE_M,
              "eye_m": EYE_M, "sight_cap_m": SIGHT_CAP_M,
              "everburning_zdo_ints": keys, "data_b64": payload,
              "biome_policy": BIOME_POLICY, "segments": {}}

    for short in want:
        seg = segs[short]
        stations = resample(seg)
        zones = zones_for(stations)
        print(f"\n=== {short} ({seg.name}) {seg.length_m:.1f} m, "
              f"half-width {seg.half_width_m} m, ledger file line "
              f"{seg.file_line} seq {seg.seq}, {len(zones)} zones")
        ground = Ground(zones, SCRATCH / f"{short}.bin", live)
        keep = KeepOut(zones, live)
        print(f"  keep-outs: {keep.pads.describe()}; "
              f"{len(keep.poi)} POI disc(s); {len(keep.pieces)} protected "
              f"piece(s) {keep.prot_by_site or '{}'}; "
              f"{len(keep.discs)} recorded structure extent(s)")
        plan = site(seg, ground, keep)
        gaps = [l["gap_m"] for l in plan.lamps if l["gap_m"] is not None]
        mins = [l["sight_min_m"] for l in plan.lamps]
        tmins = [l["sight_min_m"] for l in plan.lamps
                 if not l["sight_min_is_lower_bound"]]
        biomes: dict[str, int] = {}
        for l in plan.lamps:
            biomes[l["biome"]] = biomes.get(l["biome"], 0) + 1
        dark = dark_stretches(seg, plan, ground)
        seats = seat_check(plan, ground)
        entry = {
            "name": seg.name, "ledger_file_line": seg.file_line,
            "ledger_seq": seg.seq, "length_m": round(seg.length_m, 1),
            "half_width_m": seg.half_width_m,
            "lamps": plan.lamps, "refusals": plan.refusals,
            "notes": plan.notes,
            "spacing_m": {"n": len(gaps),
                          "min": round(min(gaps), 1) if gaps else None,
                          "median": round(_median(gaps), 1) if gaps else None,
                          "max": round(max(gaps), 1) if gaps else None},
            # TWO SUMMARIES, because they are two different facts.
            # `sight_min_m` is every lamp, including the terminal one whose
            # forward view ran out of segment rather than out of ground.
            # `sight_min_m_terrain_limited` is only the lamps whose BOTH
            # directions ended on terrain or on the cap, which is the set the
            # operator's rule is actually about.  MEASURED: reporting the
            # first alone made T12 read as a 1 m sightline when the terrain
            # there is flat to the horizon and the segment simply ends.
            "sight_min_m": {"min": round(min(mins), 1) if mins else None,
                            "median": round(_median(mins), 1) if mins else None,
                            "max": round(max(mins), 1) if mins else None},
            "sight_min_m_terrain_limited": {
                "n": len(tmins),
                "min": round(min(tmins), 1) if tmins else None,
                "median": round(_median(tmins), 1) if tmins else None,
                "max": round(max(tmins), 1) if tmins else None},
            "lamps_truncated_by_segment_end": len(mins) - len(tmins),
            "biomes": biomes, "dark": dark, "seating": seats,
            # DID THE TERRAIN ACTUALLY CHOOSE THE SPOT?  Where every candidate
            # in the window sees 80 m both ways -- which is what Meadows looks
            # like -- the sightline score is saturated and gives no signal, so
            # the tie-break (the later station, i.e. fewer lamps) decides. That
            # is a legitimate answer and a DIFFERENT answer from "the crest
            # won", and a report that does not separate them is claiming the
            # terrain picked spots it did not pick.
            "sightline_chose_the_spot": sum(
                1 for l in plan.lamps
                if l["window_best_minus_median"] > 0.5),
            "sightline_saturated_at_cap": sum(
                1 for l in plan.lamps
                if l["sight_fwd_limit"] == "cap"
                and l["sight_back_limit"] == "cap"),
            "refusal_causes": _causes(plan.refusals),
        }
        report["segments"][short] = entry
        print(f"  {len(plan.lamps)} lamp(s); spacing "
              f"{entry['spacing_m']}; two-direction sightline min "
              f"{entry['sight_min_m_terrain_limited']} over the "
              f"{entry['sight_min_m_terrain_limited']['n']} terrain-limited "
              f"lamp(s) ({entry['lamps_truncated_by_segment_end']} truncated "
              f"by the segment end); biomes {biomes}")
        print(f"  seating: worst air under a foot "
              f"{seats['worst_air_under_foot_m']} m, worst burial "
              f"{seats['worst_burial_m']} m")
        print(f"  night walk: worst stretch with NO lamp in view "
              f"{dark['worst_dark_run_m']} m at "
              f"{dark['worst_dark_run_starts_at_m']} m "
              f"({dark['worst_dark_run_where']}); worst MID-SEGMENT "
              f"{dark['worst_mid_segment_dark_run_m']} m at "
              f"{dark['worst_mid_segment_starts_at_m']} m")
        print(f"  terrain chose the spot for "
              f"{entry['sightline_chose_the_spot']}/{len(plan.lamps)} lamp(s); "
              f"{entry['sightline_saturated_at_cap']} saturated at the "
              f"{SIGHT_CAP_M:.0f} m cap")
        if plan.refusals:
            print(f"  refused {len(plan.refusals)} candidate spot(s); "
                  f"first: {plan.refusals[0]['reason']}")

        if args.place or args.dry:
            _write_segment(seg, plan, payload, keys, entry, dry=args.dry)

    if args.eye_sweep:
        report["eye_sweep"] = _eye_sweep(segs, want, live)

    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1))
        print(f"\nreport -> {args.json}")
    return 0


def _eye_sweep(segs, want, live) -> dict:
    """How much the siting moves if the eye-height DESIGN constant is wrong.

    The one number in this module that is declared rather than measured, so it
    gets a sensitivity rather than a footnote.
    """
    out = {}
    for short in want:
        seg = segs[short]
        stations = resample(seg)
        ground = Ground(zones_for(stations), SCRATCH / f"{short}.bin", live)
        keep = KeepOut(zones_for(stations), live)
        rows = {}
        for eye in (1.5, 1.7, 1.9):
            plan = site(seg, ground, keep, eye=eye)
            rows[f"eye_{eye}"] = {
                "lamps": len(plan.lamps),
                "positions_s_m": [l["s_m"] for l in plan.lamps][:40]}
        base = rows["eye_1.7"]["positions_s_m"]
        for k, v in rows.items():
            moved = sum(1 for a, b in zip(base, v["positions_s_m"]) if a != b)
            v["stations_differing_from_eye_1.7"] = moved
        out[short] = rows
    return out


def _show(res: dict, what: str) -> None:
    """Print one emit result, INCLUDING the thing a dry run exists to show.

    `schema_problems` is only populated on a dry run, and a dry run that
    prints `status` alone is a check that confidently reports success for a
    record the schema would refuse.
    """
    bad = res.get("schema_problems")
    print(f"  {what}: {res['status']} seq {res['seq']}")
    if bad:
        print(f"  {what}: SCHEMA PROBLEMS ({len(bad)}):")
        for problem in bad:
            print(f"    - {problem}")
    for check in res.get("checks", []) or []:
        if not check.get("ok"):
            print(f"  {what}: FAILED CHECK {check}")


def _write_segment(seg: Segment, plan: Plan, payload: str, keys: dict,
                   entry: dict, dry: bool) -> None:
    """One `spawn_plan` for the segment's lamps, then a `save`.

    A `spawn_plan` and not one `spawn` per lamp because the postcondition is
    the same either way -- `expect.prefab_count` carries one probe per lamp --
    and 200 chain appends for one decision is a log nobody can read.  The plan
    body goes in as a blob so the exact command list is recoverable, which is
    what `protected_piece_positions` reads for everybody else's structures.
    """
    from live import LiveBuilder  # noqa: PLC0415

    if not plan.lamps:
        print("  nothing to write")
        return
    wire = wire_lines(plan, payload)
    body = "\n".join(wire) + "\n"
    import hashlib
    sha = hashlib.sha256(body.encode()).hexdigest()
    with LiveBuilder(actor=ACTOR, dry=dry) as b:
        blob = b.blob(body.encode(),
                      note=f"lamp spawn commands for {seg.short}")
        b.observe(
            f"{seg.short} two-direction sightline at the chosen lamp spots",
            "MEASURED: min(forward, backward) contiguous visibility of the "
            "flame at +1.39959 m against the LIVE APPLIED SURFACE "
            "(applied.Applied.surface_at over the ledger's own blobs plus "
            "PatchScan generated height, bilinear), eye 1.7 m DESIGN, 1 m "
            "stations, 80 m cap",
            "tools/jumpstart/roads/lamps.py::sightline",
            {"all_lamps": entry["sight_min_m"],
             "terrain_limited_only": entry["sight_min_m_terrain_limited"],
             "truncated_by_segment_end":
                 entry["lamps_truncated_by_segment_end"]}, units="m")
        b.observe(
            f"{seg.short} worst stretch with no lamp in view at night",
            "MEASURED: per-station occlusion test against the same applied "
            "surface, asked from the station rather than from the lamp",
            "tools/jumpstart/roads/lamps.py::dark_stretches",
            entry["dark"]["worst_dark_run_m"], units="m")
        b.observe(
            f"{seg.short} worst air under a lamp foot",
            "MEASURED: fixtures.fixture_box solid underside minus "
            "applied.Applied.surface_at, probed at the lamp's own XZ and at "
            "the four corners of its measured footprint",
            "tools/jumpstart/roads/lamps.py::seat_check",
            entry["seating"]["worst_air_under_foot_m"], units="m")
        res = b.emit("spawn_plan", params={
            "role": "road_lighting",
            "site_id": f"lighting-{seg.short}",
            "plan_sha256": sha,
            "anchor": {"x": plan.lamps[0]["x"], "y": plan.lamps[0]["y"],
                       "z": plan.lamps[0]["z"], "yaw": plan.lamps[0]["yaw_deg"]},
            "command_count": len(wire),
            "prefabs": [LAMP_PREFAB],
            "datum": ("each lamp's own applied surface + "
                      f"{seat_offset_m():.5f} m, the prefab's measured pivot "
                      "offset from fixtures.fixture_box"),
            "flatten": "FORBIDDEN",
            "flatten_reason":
                "lamps stand ON the road the segment already graded. A "
                "terrain_write here would re-level ground the road owns and "
                "re-open the clobber this network spent a night closing.",
        }, wire=wire, requires={"blobs": [blob],
                                "mods": ["WorldEditCommands", "ServerDevcommands"],
                                "prefabs": [LAMP_PREFAB]},
            expect=expect_for(plan),
            meta={"lamps": len(plan.lamps),
                  "spacing_m": entry["spacing_m"],
                  "biomes": entry["biomes"],
                  "sight_min_m": entry["sight_min_m"],
                  "sight_min_m_terrain_limited":
                      entry["sight_min_m_terrain_limited"],
                  "worst_mid_segment_dark_run_m":
                      entry["dark"]["worst_mid_segment_dark_run_m"],
                  "worst_dark_run_m": entry["dark"]["worst_dark_run_m"],
                  "worst_air_under_foot_m":
                      entry["seating"]["worst_air_under_foot_m"],
                  "everburning_zdo_ints": keys,
                  "refused": len(plan.refusals),
                  "why": ("sited by maximising min(forward, backward) "
                          "terrain sightline inside a per-biome spacing "
                          "window; see lamps.py")})
        # A DRY RUN'S WHOLE VALUE IS `schema_problems`, and printing only the
        # status hides it: `status` says "validated, NOT appended" either way.
        _show(res, "spawn_plan")
        sv = b.emit("save", params={"role": "road_lighting"},
                    wire=["save"],
                    expect=expect_for(plan))
        _show(sv, "save")
        print(f"  {b.close()}")


if __name__ == "__main__":
    raise SystemExit(main())
