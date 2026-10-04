#!/usr/bin/env python3
"""The walled harbour city at (1050,720) and the harbour village at (1190,444).

WHAT THE OPERATOR ASKED FOR, verbatim:
  "there is a great natural harbor at 1050, 720. just put a city near there in
   the meadows. it should be a large city which wraps around the natural
   harbor. the harbour should have a wall with a gate to keep raiding ships
   out. i have explained this previously. this is an ideal location to use the
   ravenwood villages stuff."
  "another harbor at 1190, 444 which is below the first one.. another village
   here and a road connecting through the meadows."

WHAT THE SURVEY MEASURED, and why the numbers here are not the ones he gave.

  * (1050,720) IS UNDER WATER.  Generated h = 26.81, so 3.19 m of water stands
    over it, and its clearance to dry ground is 7.8 m -- it is a point on the
    WEST BANK of the inlet, not the middle.  The channel centreline at that
    latitude is x = 1084.  He picked the harbour by eye off the map and he was
    right about the harbour; the coordinate moved and he is told so.
  * The inlet opens NORTH.  Its deepest connected cell is h = -6.63 at
    (1055.5, 1021.5), which is north of the basin, so the mouth faces +z.
  * A SPAN READ OFF A CROSS-SECTION IS NOT A PROOF.  The three NARROWEST
    east-west sections in the whole inlet -- z = 728, 730, 732, spans 84.0,
    85.0 and 88.0 m -- DO NOT CLOSE IT.  The first dry sample west of the
    centreline there is the tip of a SPIT with water on both sides, so a wall
    abutting it leaves the sea a way round the end.  Every candidate line in
    this module was removed from the water mask and the components re-labelled;
    the chosen one is the shortest that verifiably disconnects the basin from
    the open sea while still enclosing a harbour worth the name.

WHY THE WALL NEEDS NO TERRAIN WRITE AT ALL, which is the whole reason this is
buildable.  `fb_fortress_wall` is 11.866 m tall measured foot-to-parapet with
its pivot AT THE FOOT, and the deepest sample on the chosen line is h = 23.402.
A module seated there tops out at 35.268, i.e. +5.268 m above the water plane.
So every module stands on the seabed and still has freeboard, and NOTHING is
written into the ground.  Draining water out from under a shoreline is the one
damage class this project cannot undo, and a wall that does not need a terrain
write cannot cause it.  Every op in this module declares
`flatten = "FORBIDDEN"` for that reason.

The kit carries `wnt = 0` -- no `WearNTear` -- so nothing needs a structural
footing and nothing collapses for want of one.  The flip side is stated rather
than glossed: no WearNTear also means a floating or buried segment will never
announce itself, so the grounding census at the end is the only instrument that
can catch one, and it is a deliverable.

WHY `fb_` AND NOT `mm_`.  Measured twice over, live, on this world: ledger
seq 2118-2123 sent `spawn_object mm_large_wall`, which answered "Object
reference not set to an instance of an object" -- a NullReferenceException from
a null ZNetScene lookup -- and placed nothing, while `objects_count` answered
"Error: Entity id mm_large_wall not recognized".  In the same probe
`fb_bridge_01` and `fb_bridge_02` both placed AND read back at count 1.  The
`mm_` family never reaches ZNetScene because `RavenwoodPiecesPlugin::
AddRWPieces` has a one-byte `ret` body; the `fb_` family registers through
Jotunn `PieceManager::AddPiece` and is already standing at `harbour-vestvik`.

53 `fb_` strings exist in the installed DLL and only 29 are registered pieces.
A package instrument is not a runtime instrument, so `cmd_probe` re-measures
the exact subset this module places, LIVE, before anything load-bearing is
built -- and it is re-taken after any restart, because a registration measured
under Jotunn 2.30.0 is not evidence about 2.30.1.

This module OWNS `settlements/harbour_city.py` and imports
`settlements/waterfront.py` as a library.  It never edits it: that file belongs
to another agent tonight, and its `VESSELS` draughts, `Surface`, `berth`,
`audit`, `spawn_op`, `emit` and portal-pairing are taken verbatim rather than
re-derived, because a coordinate re-derived by the consumer is a second source
of truth.
"""
from __future__ import annotations

import argparse
import functools
import json
import math
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
for _p in (JUMPSTART, JUMPSTART / "settlements", JUMPSTART / "crossings",
           JUMPSTART / "roads", JUMPSTART / "ledger", JUMPSTART / "terraform",
           JUMPSTART / "blueprints"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import assemble as A  # noqa: E402
import waterfront as WF  # noqa: E402
import water as W  # noqa: E402
import tcdata as TC  # noqa: E402  terraform/, the TCData blob encoder
import worldseed  # noqa: E402

ACTOR = "HarbourCity"

# The lattice-aligned field this module measures against.  Aligned means
# `cx - half + step/2` is an INTEGER, so every sample sits exactly on a
# TerrainComp sample centre; `waterfront.Surface` REFUSES an unaligned patch,
# and my first field (half 350) was refused for exactly that reason.
#   cityA (1080,700) half 250.5 step 1 -> x0 830.0  z0 450.0
#   villB (1200,460) half 200.5 step 1 -> x0 1000.0 z0 260.0
CITY_FIELD = "/tmp/harbour/city.bin"

# ---------------------------------------------------------------------------
# the two sites, every number MEASURED and carrying its instrument
# ---------------------------------------------------------------------------

SITE_A = "harbour-city-ulfsgard"
SITE_B = "harbour-village-sudrvik"

# The sea wall.  z and the x extent come from the closing sweep in
# `cmd_survey`; the 132.0 m line is the measured 112.0 m waterline plus 10 m
# onto dry ground at each end, so the wall dies into the banks instead of
# stopping at the water's edge where the sea could round it.
WALL = dict(
    z=752.0, x0=1000.5, x1=1132.5,
    waterline=(1010.5, 1122.5),           # measured first-dry samples
    pitch=20.009,                         # fb_fortress_wall measured length
    yaw=0.0,                              # local +z is the outward normal; the
                                          # 20 m length runs along world x
    encloses_m2=10313,
    max_depth_m=6.598,
    # The gate, SOLVED by arithmetic and not chosen: at span 132.0 with the
    # measured 20.009 m module pitch, EXACTLY ONE module count leaves a gap
    # between 9.5 m (wider than the largest admitted beam plus slack) and
    # 20.0 m (wider than that is not a gate): n = 6, gap 11.946 m, and
    # 6 * 20.009 + 11.946 = 132.000 to the millimetre.  Modules 0..2 run from
    # x0, so the gap opens after 3 modules: 1000.5 + 3 * 20.009 = 1060.527.
    gate=(1060.527, 1072.473),
    gate_clear_m=11.946,
)

# MEASURED collider geometry, solid non-trigger boxes only, from the modded
# sandbox dump /tmp/harbour/piece_geometry_modded.json.  The `mesh` and
# `mesh_render` rows report the whole 20 x 11.87 x 6.8 m envelope for every
# wall variant and are USELESS for an opening -- reading one of those is the
# same error that read 11.64 x 35.43 m for a 9.16 x 21.57 m hull.
KIT = dict(
    wall="fb_fortress_wall",
    tower="fb_fortress_wall_tower",
    stair_l="fb_fortress_wall_stair_left",
    stair_r="fb_fortress_wall_stair_right",
    corner="fb_fortress_wall_corner",
    gateway="fb_fortress_wall_gateway",
    portcullis="fb_fortress_portcullis",
    gates="fb_fortress_gates",
    wall_len_m=20.009, wall_h_m=11.866, wall_depth_m=6.800,
    tower_h_m=15.402, tower_platform_y=10.229, tower_cap_y=14.269,
    pivot_is_foot=True,
    # clear openings, from the BOX colliders
    gateway_clear_m=3.429,          # x[-10.002,-1.725] .. x[1.704,10.004]
    wall_portcullis_clear_m=6.851,  # x[-10.002,-3.656] .. x[3.195,10.004]
    # `panel_w_m` is the CLOSED envelope and is NOT a closable width.  The
    # 5.704 m is one moving leaf (x [-2.47495, 2.64786]) between two STATIC
    # posts (x [2.53573, 2.91] and [-2.79427, -2.42], 0.374 m wide, 5.834 m
    # tall) that do not move when the Door opens -- MEASURED by driving the
    # Animator by hand, see `sea_wall`.  Open aperture is 4.956 x 3.751 m.
    panel_w_m=5.704, panel_h_m=5.857, panel_foot_off=0.226,
    panel_open_w_m=4.956, panel_open_h_m=3.751, panel_leaf_lift_m=3.7431,
)

# Vanilla pieces, geometry from blueprints/data/piece_geometry.json.
DECK = "wood_floor"              # 2 x 2 m, top face +0.097 above its origin
DECK_SMALL = "wood_floor_1x1"
PILE = "wood_pole_log_4"         # 4 m
PILE_SHORT = "wood_pole_log"     # 2 m
BEAM = "wood_beam"
WALL_LOG = "wood_wall_log"       # 2 x 2 m wall panel
WALL_HALF = "wood_wall_half"
ROOF = "wood_roof"
ROOF_45 = "wood_roof_45"
POLE = "wood_pole"
STAIR = "wood_stair"
SIGN = "sign"
TORCH = "piece_groundtorch"
PORTAL = "portal_wood"

DECK_Y = WF.DECK_DATUM_Y          # 30.6, the fleet's walking datum over water
FLOOR_OFF = WF.FLOOR_TOP_OFFSET   # 0.097


def surface() -> "WF.Surface":
    """The applied surface, with MY field unioned in as `extra`.

    `extra` rather than `field` so `/tmp/harbour/extra.bin` -- another agent's
    patch set -- is not displaced, and `patch_for` still takes the SMALLEST
    containing patch so a 1 m question is never answered off the 8 m overview.
    """
    if not Path(CITY_FIELD).exists():
        raise SystemExit(
            f"{CITY_FIELD} is missing. It is the lattice-aligned 1 m field this "
            f"module measures against; regenerate it with\n"
            f"  printf 'cityA\\t1080\\t700\\t250.5\\t1\\nvillB\\t1200\\t460\\t200.5\\t1\\n' "
            f"> /tmp/harbour/city_req.tsv\n"
            f"  VH_SRC=... SEED=Pirate68 REQ=/tmp/harbour/city_req.tsv "
            f"OUT={CITY_FIELD} bash blueprints/run_patchscan.sh\n"
            f"rather than guessing a height.")
    return WF.Surface(field="/tmp/harbour/h1m.bin", extra=CITY_FIELD)


def depth(s, x: float, z: float) -> float:
    """Water over the APPLIED surface at a point.  Against y = 30.0, never 0."""
    return W.WATER_LEVEL - s.at(x, z)[0]


# ---------------------------------------------------------------------------
# shoreline, measured per row rather than assumed straight
# ---------------------------------------------------------------------------

def waterline(s, z: float, x_centre: float, *, freeboard: float = 0.5,
              reach: float = 140.0, step: float = 0.5) -> tuple[float, float]:
    """`(west_x, east_x)` of the first DRY sample either side of the centreline.

    Walked outward at `step` from the centreline rather than read off a
    straight line, because both banks of this inlet are irregular: between
    z = 716 and z = 728 the west shore moves 24 m east, and a quay laid on a
    straight line would be 24 m inland at one end and 24 m out to sea at the
    other.
    """
    xw = x_centre
    while xw > x_centre - reach:
        if s.at(xw, z)[0] > W.WATER_LEVEL + freeboard:
            break
        xw -= step
    xe = x_centre
    while xe < x_centre + reach:
        if s.at(xe, z)[0] > W.WATER_LEVEL + freeboard:
            break
        xe += step
    return xw, xe


# ---------------------------------------------------------------------------
# plan emission
# ---------------------------------------------------------------------------

class Plan:
    """A spawn-plan accumulator that refuses the two mistakes that cost this
    project whole structures.

    1. IT REFUSES A PIECE WHOSE SEAT IT CANNOT JUSTIFY.  Every `add` carries
       the surface the piece stands on and the pivot offset for its own prefab;
       nothing inherits another prefab's offset.  A ground torch needs
       +0.65359 and the `fb_fortress_*` kit needs ZERO because its pivot is
       already at the foot, and inheriting the torch's number is how three
       lamps ended up 5.17 m in the air.
    2. IT ORDERS BOTTOM-UP IN Y.  `to_rcon_plan` does this for vanilla bodies
       so WearNTear support exists when each piece lands.  The `fb_` kit has no
       WearNTear and does not need it, but the vanilla boathouses and quays in
       this build DO.
    """

    def __init__(self, site: str):
        self.site = site
        self.rows: list[tuple[str, float, float, float, float, str]] = []

    def add(self, prefab: str, x: float, y: float, z: float, yaw: float = 0.0,
            why: str = "") -> None:
        self.rows.append((prefab, float(x), float(y), float(z),
                          float(yaw) % 360.0, why))

    def sorted_rows(self):
        return sorted(self.rows, key=lambda r: (round(r[2], 3), r[0]))

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for prefab, *_ in self.rows:
            out[prefab] = out.get(prefab, 0) + 1
        return dict(sorted(out.items()))

    def lines(self) -> list[str]:
        return [f"spawn_object {p} pos={A.fmt(z)},{A.fmt(x)},{A.fmt(y)}"
                f" rot={A.fmt(yaw)},0,0 from=0,0,0"
                for p, x, y, z, yaw, _w in self.sorted_rows()]

    def extent(self) -> dict:
        if not self.rows:
            return {}
        xs = [r[1] for r in self.rows]
        ys = [r[2] for r in self.rows]
        zs = [r[3] for r in self.rows]
        return dict(x=[min(xs), max(xs)], y=[min(ys), max(ys)],
                    z=[min(zs), max(zs)])

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        head = [f"# {self.site}: {len(self.rows)} pieces, bottom-up in y",
                f"# counts: {json.dumps(self.counts())}"]
        path.write_text("\n".join(head + self.lines()) + "\n", "utf-8")
        return path

    def __len__(self) -> int:
        return len(self.rows)


# ---------------------------------------------------------------------------
# the town bodies, and the pads they stand on
# ---------------------------------------------------------------------------
#
# HOUSE FOOTPRINTS, measured from the SOLID non-trigger colliders in the modded
# sandbox dump: the declared unturned extent (w along x, d along z) and height.
# The `fb_` bodies are NOT centred on their pivot -- `fb_house_01` reaches
# x[-11.317,10.151] and z[-10.734,1.128] -- so WHERE the pivot goes, and the pad
# under the body, are both taken from the body AT ITS YAW (`house_body`), not
# from a pivot offset rotated here by hand.
HOUSES: dict[str, dict] = {
    "fb_house_01":       dict(w=21.469, d=11.862, h=7.489),
    "fb_house_02":       dict(w=13.624, d=9.376,  h=7.394),
    "fb_house_03":       dict(w=18.057, d=14.902, h=7.824),
    "fb_house_04":       dict(w=26.071, d=22.661, h=13.907),
    "fb_shelter_01":     dict(w=11.421, d=12.072, h=6.006),
    "fb_viking_house_01": dict(w=19.690, d=13.970, h=7.251),
    "fb_viking_house_02": dict(w=15.416, d=20.260, h=11.373),
    "fb_viking_house_03": dict(w=15.715, d=38.815, h=10.908),
    "fb_viking_house_04": dict(w=17.818, d=14.453, h=7.751),
    "fb_viking_house_05": dict(w=12.458, d=12.858, h=9.262),
    "fb_viking_house_06": dict(w=17.457, d=21.998, h=13.565),
    "fb_viking_house_07": dict(w=25.083, d=20.914, h=13.856),
    "fb_viking_house_08": dict(w=15.016, d=15.425, h=12.225),
}
# The working ground round a seated house: 1 m a side, as the seat always had.
SEAT_MARGIN_M = 1.0

# Terrain clamp, MEASURED: TerrainComp::ApplyToHeightmap clamps the applied
# height to the generated height +/- 8 m.  1 m is held back as margin for the
# same reason roads/route.py holds it back: a write that lands exactly on the
# clamp has no room for the bilinear blend at the pad edge.
CLAMP_M = 8.0
CLAMP_MARGIN_M = 1.0
CUT_MAX_M = CLAMP_M - CLAMP_MARGIN_M

# A pad must stand clear of the water plane by this much before a building goes
# on it.  0.5 m, the same freeboard `water.LAND_LEVEL` uses, so a house is never
# seated on a sample that is really surf.
PAD_FREEBOARD_M = 0.8


class Grid:
    """A vectorised numpy view of ONE patch, for the thousands of pad tests the
    quarter fitter makes.

    `Surface.at` is the authority and is used for every number that ends up in
    a record; this is a bulk pre-filter over the same samples of the same
    patch, and `pad_fits` cross-checks its own floor against `Surface.at` at
    the pad centre so the two cannot silently diverge.  The first version of
    the fitter called `Surface.at` per sample and took 275 s for one design
    pass, which is too slow to iterate a layout against.
    """

    def __init__(self, s, x: float, z: float, margin: float = 260.0):
        self.f = s.patch_for(x, z, margin=2.0)
        self.h = np.asarray(self.f.heights, dtype=np.float64)
        self.x0 = self.f.x0
        self.z0 = self.f.z0
        self.step = self.f.step
        self.n = self.f.n

    def box(self, cx: float, cz: float, w: float, d: float):
        j0 = int(round((cx - w / 2.0 - self.x0) / self.step))
        j1 = int(round((cx + w / 2.0 - self.x0) / self.step))
        i0 = int(round((cz - d / 2.0 - self.z0) / self.step))
        i1 = int(round((cz + d / 2.0 - self.z0) / self.step))
        if i0 < 0 or j0 < 0 or i1 >= self.n or j1 >= self.n:
            return None
        return self.h[i0:i1 + 1, j0:j1 + 1]


def pad_fits(s, cx: float, cz: float, w: float, d: float,
             *, step: float = 1.0, grid: "Grid | None" = None) -> dict:
    """Can a `w` x `d` pad centred here be levelled inside the clamp, on dry
    ground, and what floor height does it take?

    The floor is the MEDIAN of the footprint, not the mean and not the max: the
    median is the height that minimises the worst cut on a monotone slope and
    it is what keeps `need` inside the clamp for the largest possible pad.
    Reports `need` either way so a refusal carries its number.
    """
    a = None
    if grid is not None:
        blk = grid.box(cx, cz, w, d)
        if blk is None:
            raise SystemExit(f"pad ({cx},{cz}) {w}x{d} runs off the patch; "
                             f"widen the field rather than guessing a height")
        a = blk.ravel()
    else:
        hs = []
        nx = max(2, int(round(w / step)))
        nz = max(2, int(round(d / step)))
        for i in range(nx + 1):
            x = cx - w / 2.0 + i * w / nx
            for j in range(nz + 1):
                z = cz - d / 2.0 + j * d / nz
                hs.append(s.at(x, z)[0])
        a = np.asarray(hs)
    floor = float(np.median(a))
    need = float(np.abs(a - floor).max())
    wet = int((a <= W.WATER_LEVEL + PAD_FREEBOARD_M).sum())
    return dict(cx=cx, cz=cz, w=w, d=d, floor_y=round(floor, 3),
                cut_needed_m=round(need, 3), samples=int(a.size),
                wet_samples=wet, min_h=round(float(a.min()), 3),
                max_h=round(float(a.max()), 3),
                ok=bool(need <= CUT_MAX_M and wet == 0))


@functools.lru_cache(maxsize=None)
def house_body(prefab: str, yaw: float) -> tuple[dict, tuple[float, float]]:
    """`flatten.body_extents` of one `fb_` prefab at `yaw`, and the centre of
    its at-yaw collider rectangle relative to its PIVOT - both by
    `base_geometry.xz_footprint`, the emitter's own measure and the engine's
    yaw sense (clockwise seen from above).  RAISES for a prefab with no
    measured solid: the ground it stands on would be unknown."""
    import flatten as FL                                  # noqa: PLC0415
    import base_geometry as BG                            # noqa: PLC0415
    obj = SimpleNamespace(prefab=prefab, pos=(0.0, 0.0, 0.0),
                          rot=(0.0, 0.0, 0.0, 1.0), scale=(1.0, 1.0, 1.0))
    ext = FL.body_extents([obj], yaw)
    if ext["pivot_only"]:
        raise ValueError(f"{prefab} has no measured collider solid, so the "
                         f"ground it stands on at yaw {yaw:g} is unknown")
    f = BG.xz_footprint([obj], yaw)
    return ext, ((f.min_x + f.max_x) / 2.0, (f.min_z + f.max_z) / 2.0)


def house_seat(s, prefab: str, cx: float, cz: float, yaw: float,
               grid: "Grid | None" = None) -> dict:
    """Where the prefab's PIVOT goes so its footprint sits on the pad, and the
    pad: both the body AT ITS YAW (`house_body`).

    The pad is `flatten.pad_extent_at_yaw` (PAD_EXTENT_DEPENDS_ON) -- the rule
    every pad emitter shares -- with no declared floor, the declared footprint,
    `SEAT_MARGIN_M` and the lattice the writer stamps; the pivot is placed so
    the body's at-yaw rectangle is centred on the pad.  THIS REPLACED A HAND
    ROTATION THAT TURNED THE PIVOT OFFSET THE WRONG WAY: counter-clockwise,
    where the engine turns a body clockwise.  MEASURED 2026-09-25, read-only,
    on Ulfsland's harbour B (ledger seq 3079 against pads 3007-3047): all 3
    bodies at yaw 180 on their pads, and 8 of 8 at yaw 90/270 off them by
    exactly twice the pivot offset -- fb_viking_house_04 9.56 m, 171 of 304
    vertices under it unstamped.

    The pivot y is the pad floor exactly: every `fb_` body measured has its
    pivot AT THE FOOT (min y within 0.25 m of 0), so no shaft offset is added
    -- and adding the ground-torch's +0.65359 here is precisely the
    inheritance that put three beacons in the air.
    """
    import flatten as FL                                  # noqa: PLC0415
    g = HOUSES[prefab]
    ext, (mx, mz) = house_body(prefab, float(yaw))
    pe = FL.pad_extent_at_yaw((0.0, 0.0), (g["w"], g["d"]), ext, (cx, cz),
                              SEAT_MARGIN_M)
    pad = pad_fits(s, cx, cz, pe["w"], pe["d"], grid=grid)
    w, d = ext["at_yaw"]
    return dict(prefab=prefab, x=round(cx - mx, 3), z=round(cz - mz, 3),
                y=pad["floor_y"], yaw=yaw % 360.0,
                pad=pad, rotated_w=round(w, 2), rotated_d=round(d, 2),
                pivot_offset=[round(-mx, 3), round(-mz, 3)], height_m=g["h"])


# The quarters, as RECTANGLES OF INTEREST rather than a fixed list of seats.
# A hand-written seat list would be a guess; a rectangle plus a greedy fit
# against the measured surface is a decision the ground gets to make.  Each
# quarter states which way its buildings face, because a house should address
# the water or the street and not the hillside.
QUARTERS_A = [
    dict(id="westquay",  x=(966.0, 1032.0),  z=(650.0, 750.0),  yaw=90.0,
         why="the west bank, facing the basin across the quay"),
    dict(id="head",      x=(1030.0, 1140.0), z=(586.0, 650.0),  yaw=0.0,
         why="the head of the inlet, the market end, facing the water north"),
    dict(id="eastquay",  x=(1134.0, 1200.0), z=(646.0, 752.0),  yaw=270.0,
         why="the east bank, facing the basin across the quay"),
    dict(id="gatehill",  x=(966.0, 1036.0),  z=(756.0, 806.0),  yaw=180.0,
         why="behind the west gate tower, the garrison quarter"),
    dict(id="eastgate",  x=(1124.0, 1196.0), z=(756.0, 806.0),  yaw=180.0,
         why="behind the east gate tower, the seaward watch quarter"),
]
QUARTERS_B = [
    dict(id="fjordwest", x=(1130.0, 1176.0), z=(446.0, 512.0),  yaw=90.0,
         why="west bank of the fjord head"),
    dict(id="fjordeast", x=(1216.0, 1272.0), z=(440.0, 512.0),  yaw=270.0,
         why="east bank of the fjord head"),
    dict(id="fjordhead", x=(1170.0, 1226.0), z=(506.0, 546.0),  yaw=180.0,
         why="the head itself, above the shallows"),
]

# Which bodies go where.  Big halls at the head and the gate, ordinary houses
# along the quays, shelters filling the gaps.  Ordered largest-first so the
# greedy fit spends the good ground on the buildings that need it.
MIX_CITY = ["fb_house_04", "fb_viking_house_07", "fb_viking_house_06",
            "fb_house_01", "fb_viking_house_02", "fb_house_03",
            "fb_viking_house_01", "fb_viking_house_08", "fb_viking_house_04",
            "fb_house_02", "fb_viking_house_05", "fb_shelter_01"]
# PER-QUARTER CAPS, so the greedy fit does not spend every good pad on the
# largest hall.  Without these the first pass seated 10 x fb_house_04 and 4 x
# fb_viking_house_06 and nothing else in the whole city -- a skyline of ten
# identical 26 m halls, which is a mix failure, not a ground failure.
CAP_CITY = {"fb_house_04": 2, "fb_viking_house_07": 2, "fb_viking_house_06": 3,
            "fb_house_01": 3, "fb_viking_house_02": 3, "fb_house_03": 3,
            "fb_viking_house_01": 4, "fb_viking_house_08": 4,
            "fb_viking_house_04": 4, "fb_house_02": 5,
            "fb_viking_house_05": 6, "fb_shelter_01": 8}
MIX_VILLAGE = ["fb_viking_house_06", "fb_house_03", "fb_viking_house_01",
               "fb_viking_house_08", "fb_viking_house_04", "fb_house_02",
               "fb_viking_house_05", "fb_shelter_01"]
CAP_VILLAGE = {"fb_viking_house_06": 1, "fb_house_03": 1,
               "fb_viking_house_01": 2, "fb_viking_house_08": 2,
               "fb_viking_house_04": 2, "fb_house_02": 3,
               "fb_viking_house_05": 4, "fb_shelter_01": 5}

STREET_CLEAR_M = 3.0     # between two bodies' rotated footprints


def fit_quarter(s, q: dict, mix: list[str], taken: list[dict], *,
                lattice: float = 2.0, keepout: list[dict] | None = None,
                cap: dict[str, int] | None = None) -> list[dict]:
    """Greedily seat bodies in a quarter, largest first, on measured ground.

    REFUSALS ARE COUNTED, not swallowed.  Three different things can reject a
    candidate seat and they are different facts: the pad needs more cut than
    the clamp allows, the pad has samples at or under the water plane, or the
    seat collides with something already placed or with a road keep-out.  A
    quarter that seats nothing reports which of the three did it.
    """
    out: list[dict] = []
    why = dict(clamp=0, wet=0, collide=0, keepout=0, offpatch=0, placed=0)
    grid = Grid(s, (q["x"][0] + q["x"][1]) / 2.0, (q["z"][0] + q["z"][1]) / 2.0)
    used: dict[str, int] = {}
    for prefab in mix:
        g = HOUSES[prefab]
        limit = (cap or {}).get(prefab, 99)
        cz = q["z"][0]
        while cz <= q["z"][1]:
            cx = q["x"][0]
            while cx <= q["x"][1]:
                if used.get(prefab, 0) >= limit:
                    cx += lattice
                    continue
                try:
                    seat = house_seat(s, prefab, cx, cz, q["yaw"], grid=grid)
                except SystemExit:
                    why["offpatch"] += 1
                    cx += lattice
                    continue
                pad = seat["pad"]
                if pad["wet_samples"]:
                    why["wet"] += 1
                    cx += lattice
                    continue
                if pad["cut_needed_m"] > CUT_MAX_M:
                    why["clamp"] += 1
                    cx += lattice
                    continue
                rw, rd = seat["rotated_w"], seat["rotated_d"]
                clash = False
                for o in taken + out:
                    if (abs(cx - o["cx"]) < (rw + o["rw"]) / 2 + STREET_CLEAR_M
                            and abs(cz - o["cz"]) < (rd + o["rd"]) / 2 + STREET_CLEAR_M):
                        clash = True
                        break
                if clash:
                    why["collide"] += 1
                    cx += lattice
                    continue
                if keepout and any(
                        math.hypot(cx - k["x"], cz - k["z"]) < k["r"] + max(rw, rd) / 2
                        for k in keepout):
                    why["keepout"] += 1
                    cx += lattice
                    continue
                rec = dict(seat, quarter=q["id"], cx=cx, cz=cz, rw=rw, rd=rd)
                out.append(rec)
                why["placed"] += 1
                used[prefab] = used.get(prefab, 0) + 1
                cx += lattice
            cz += lattice
    return out, why


# ---------------------------------------------------------------------------
# vanilla geometry, MEASURED from blueprints/data/piece_geometry.json
# ---------------------------------------------------------------------------
#
# Every one of these is a PIVOT fact, and every one of them is different.  This
# block exists because "seat it on the surface" is not a single rule:
#   wood_floor        2.00 x 2.00, y[-0.033,+0.097]  -> pivot near the top face,
#                     so a deck whose WALKING SURFACE is 30.6 is authored at
#                     30.503.  That 0.097 is the whole reason DECK_DATUM_Y and
#                     the authored y are two different numbers.
#   wood_pole_log_4   0.40 x 4.00, y[-2.0,+2.0]      -> pivot at the CENTRE, so
#                     a pile standing from the seabed is authored 2 m above it,
#                     not at it.
#   woodwall          2.00 x 2.00 x 0.30, y[-1,+1]   -> pivot at the centre, so
#                     a wall course sitting on a deck is authored +1.0 above it.
#   wood_roof         2.00 wide, rises 1.130 m over 2.061 m of run (26 deg);
#                     y[-0.0485,+1.0825], z[-1.0583,+1.0643].
#   piece_groundtorch y[-0.6536,+0.8226]             -> +0.65359 ABOVE the
#                     surface, the opposite sense to the fb_ kit's pivot-at-foot.
# Inheriting any one of these for another prefab is the defect that put three
# beacons 5.173 m in the air.
GEOM = dict(
    floor=dict(prefab="wood_floor", w=2.0, top=0.0969, bottom=-0.0329),
    pile=dict(prefab="wood_pole_log_4", len=4.0, half=2.0),
    pile2=dict(prefab="wood_pole_log", len=2.0, half=1.0),
    wall=dict(prefab="woodwall", w=2.0, h=2.0, half=1.0),
    beam=dict(prefab="wood_beam", len=2.0),
    roof=dict(prefab="wood_roof", w=2.0, run=2.0611, rise=1.1310),
    stair=dict(prefab="wood_stair", run=2.029, rise=1.05),
)
DECK_AUTHOR_Y = DECK_Y - GEOM["floor"]["top"]        # 30.503


def piles(pl: Plan, x: float, z: float, seabed_y: float,
          deck_author_y: float = DECK_AUTHOR_Y, why: str = "") -> int:
    """A pile stack from the seabed up to just under the deck.

    `wood_pole_log_4`'s pivot is at its CENTRE, so the first pole is authored
    2 m above the seabed and each subsequent one 4 m higher.  The stack is
    sized to REACH the deck, never to stop short: a deck tile with no pile
    under it reads as the floating defect, and this kit -- unlike the fb_ one --
    has WearNTear, so a tile with no support really does fall.
    """
    n = 0
    y = seabed_y
    while y + GEOM["pile"]["len"] < deck_author_y + 0.5:
        pl.add(GEOM["pile"]["prefab"], x, y + GEOM["pile"]["half"], z, 0.0, why)
        y += GEOM["pile"]["len"]
        n += 1
    # the last, part-buried pole that actually meets the deck
    pl.add(GEOM["pile"]["prefab"], x,
           deck_author_y - GEOM["pile"]["half"] + 0.05, z, 0.0, why)
    return n + 1


def deck_strip(pl: Plan, s, x0: float, x1: float, z0: float, z1: float,
               *, pile_every: float = 4.0, why: str = "") -> dict:
    """A 2 m-tile deck over water at the fleet's 30.6 datum, on measured piles.

    Tiles are laid on the 2 m lattice so they abut exactly; a half-metre offset
    leaves a 0.5 m slot a player falls through, which is not visible in a plan
    and is immediately visible underfoot.

    THE DECK IS RE-TESTED AGAINST THE APPLIED SURFACE BEFORE IT IS TILED.
    `DECK_AUTHOR_Y` is a FIXED DATUM -- the fleet's 30.6 m walking height,
    minus the floor's own 0.0969 top -- chosen once for the whole city and
    never asked about again.  A datum is a declaration, not a measurement:
    the moment any write raises the ground under a tile past 30.6 m, that
    tile's walking surface is UNDER the world and it is a `spawn_object`, a
    ZDO and an acceptance refusal nobody can see.  Every tile is now asked,
    at its own xz, against the surface `s` reports -- which is the applied
    surface, since `DredgedSurface` composes the ledger's deltas.
    """
    tiles = 0
    pilestacks = 0
    piece_count_before = len(pl)
    xs = np.arange(x0 + 1.0, x1, 2.0)
    zs = np.arange(z0 + 1.0, z1, 2.0)
    deck_top = DECK_AUTHOR_Y + GEOM["floor"]["top"]
    buried = []
    for x in xs:
        for z in zs:
            g = s.at(float(x), float(z))[0]
            if g - deck_top > A.DECK_BURIAL_MAX_M:
                buried.append((float(x), float(z), round(g - deck_top, 3),
                               round(g, 3)))
            pl.add(GEOM["floor"]["prefab"], x, DECK_AUTHOR_Y, z, 0.0, why)
            tiles += 1
    if buried:
        buried.sort(key=lambda r: -r[2])
        w = buried[0]
        raise SystemExit(
            f"deck_strip({why or 'unnamed'}): REFUSED. {len(buried)} of "
            f"{tiles} tile(s) would have their walking surface more than "
            f"{A.DECK_BURIAL_MAX_M} m UNDER the applied surface at the fixed "
            f"{DECK_Y:g} m datum. Worst ({w[0]:.1f}, {w[1]:.1f}): ground "
            f"{w[3]:.3f} m, deck top {deck_top:.3f} m, buried {w[2]:.3f} m. "
            f"A buried walking surface is a hole in the quay, and the datum "
            f"is a DECLARATION -- re-solve the deck against this ground or "
            f"dredge under it, but do not tile into it. Surface: "
            f"{getattr(s, 'field_names', ['?'])[0]}")
    for x in xs[::max(1, int(pile_every // 2))]:
        for z in zs[::max(1, int(pile_every // 2))]:
            sb = s.at(float(x), float(z))[0]
            piles(pl, float(x), float(z), sb, why=why + " pile")
            pilestacks += 1
    return dict(tiles=tiles, pile_stacks=pilestacks,
                pieces=len(pl) - piece_count_before,
                x=[float(x0), float(x1)], z=[float(z0), float(z1)],
                deck_walking_y=DECK_Y,
                deck_retested_against=getattr(s, "field_names", ["?"])[0])


# ---------------------------------------------------------------------------
# the boathouses -- ENCLOSED, which is the point
# ---------------------------------------------------------------------------
#
# EVERY "boathouse" ON THIS WORLD TODAY IS BARE DECKING.  MEASURED over the
# five standing waterfront sites: `boathouse-temple-strait` is 171 objects of
# wood_floor 36 + wood_pole 12 + wood_pole_log_4 18 and nothing else; the other
# four are the same shape.  There is not one roof, wall, door or bay anywhere.
# So "boathouse" has been a label, not a building, and these are the first
# actual boathouses here.
#
# A boathouse is therefore defined by three things it must HAVE, each of which
# is counted in the census afterwards:
#   * a BAY -- open water a hull can float into, with a measured clear width
#     and a measured depth, so the hull class it admits is a fact and not a
#     hope.  A bay floored over is a shed, not a boathouse.
#   * WALLS on three sides and a ROOF over the bay, so it is enclosed.
#   * an APRON you can walk, at the 30.6 datum, with the bay open to seaward.
#
# The seaward gable is DELIBERATELY open: that is the bay mouth.  A census that
# counts "walls on four sides" would be counting a defect.
BOATHOUSE = dict(
    apron_w=2.0,          # walkway each side of a bay, 1 tile
    pier_w=2.0,           # the shared centre pier between two bays
    wall_courses=2,       # 2 x 2 m of woodwall above the deck
    roof_pitch_deg=26.0,  # wood_roof, measured 1.1310 rise / 2.0611 run
)


def shed(pl: Plan, s, *, bay_x0: float, bay_x1: float, z_back: float,
         z_front: float, why: str) -> dict:
    """One enclosed bay: aprons, piles, three walls, a gabled roof, open mouth.

    Geometry is on the 2 m lattice throughout.  The ROOF RIDGE runs along z --
    the bay's long axis -- so a hull enters under the ridge and the slopes shed
    to either side; `wood_roof`'s own slope is along its local z, so a tile
    sloping in world x is placed at yaw 90 / 270.

    WHICH OF THE TWO GOES ON WHICH HALF IS NOT FREE, AND THE FIRST VERSION HAD
    IT BACKWARDS ON ALL 224 PANELS HERE.  MEASURED from the prefab's own prism
    (`blueprints/data/piece_geometry.json`, game_version 1.0.12): `wood_roof`
    rises toward local -z (low edge z=+1.0031 y=-0.0485, high edge z=-0.9971
    y=+1.0825).  Under this tree's yaw convention (`crossings/assemble.py`:
    0=+Z, 90=+X, clockwise from above) yaw 90 puts the HIGH edge WEST and yaw
    270 puts it EAST.  Yaw 90 on the west half therefore slopes AWAY from the
    ridge: both halves fell 0.5654 m/m INTO the centreline, which with the
    1.131 m course rise is a sawtooth of six 2.234 m open risers per bay
    rather than a gable.  The half nearer -x takes 270, the half nearer +x
    takes 90.

    A 180 degree yaw flip moves neither the footprint nor the AABB, so the
    per-prefab count, the position census at tolerance 0 and the plane
    coverage test all read the same before and after -- the gradient's SIGN
    is the only thing that sees it.  Verify orientation as a signed quantity,
    never as the volume the piece occupies.
    """
    aw = BOATHOUSE["apron_w"]
    n0 = len(pl)
    # --- aprons: one tile column each side of the bay ---------------------
    west = deck_strip(pl, s, bay_x0 - aw, bay_x0, z_back, z_front,
                      why=why + " apron W")
    east = deck_strip(pl, s, bay_x1, bay_x1 + aw, z_back, z_front,
                      why=why + " apron E")
    # --- back apron, closing the landward end -----------------------------
    back = deck_strip(pl, s, bay_x0 - aw, bay_x1 + aw, z_back - 2.0, z_back,
                      why=why + " back apron")
    wall_y0 = DECK_Y + GEOM["wall"]["half"]
    walls = 0
    # --- the two long walls, on the OUTER edge of each apron --------------
    for x in (bay_x0 - aw, bay_x1 + aw):
        for z in np.arange(z_back - 2.0 + 1.0, z_front, 2.0):
            for c in range(BOATHOUSE["wall_courses"]):
                pl.add(GEOM["wall"]["prefab"], x, wall_y0 + 2.0 * c, float(z),
                       90.0, why + " long wall")
                walls += 1
    # --- the back wall, across the full width ------------------------------
    for x in np.arange(bay_x0 - aw + 1.0, bay_x1 + aw, 2.0):
        for c in range(BOATHOUSE["wall_courses"]):
            pl.add(GEOM["wall"]["prefab"], float(x), wall_y0 + 2.0 * c,
                   z_back - 2.0, 0.0, why + " back wall")
            walls += 1
    # --- the gabled roof ---------------------------------------------------
    span = (bay_x1 + aw) - (bay_x0 - aw)
    hw = span / 2.0
    xc = (bay_x0 + bay_x1) / 2.0
    eaves_y = DECK_Y + 2.0 * BOATHOUSE["wall_courses"]
    courses = int(math.ceil(hw / GEOM["roof"]["w"]))
    roofs = 0
    for c in range(courses):
        dx = hw - GEOM["roof"]["w"] * (c + 0.5)
        y = eaves_y + GEOM["roof"]["rise"] * c
        for z in np.arange(z_back - 2.0 + 1.0, z_front, 2.0):
            pl.add(GEOM["roof"]["prefab"], xc - dx, y, float(z), 270.0,
                   why + " roof W")
            pl.add(GEOM["roof"]["prefab"], xc + dx, y, float(z), 90.0,
                   why + " roof E")
            roofs += 2
    ridge_y = eaves_y + GEOM["roof"]["rise"] * courses
    # ridge beam
    for z in np.arange(z_back - 2.0 + 1.0, z_front, 2.0):
        pl.add(GEOM["beam"]["prefab"], xc, ridge_y, float(z), 90.0,
               why + " ridge")
    # --- gable fill at the CLOSED end only; the seaward gable stays open ---
    gable = 0
    for c in range(courses):
        y = eaves_y + GEOM["wall"]["half"] + 2.0 * c
        if y > ridge_y:
            break
        halfrun = max(0.0, hw - GEOM["roof"]["w"] * c)
        for x in np.arange(xc - halfrun + 1.0, xc + halfrun, 2.0):
            pl.add(GEOM["wall"]["prefab"], float(x), y, z_back - 2.0, 0.0,
                   why + " gable")
            gable += 1
    # --- the bay itself: MEASURED, not asserted ---------------------------
    clear = bay_x1 - bay_x0
    ds = []
    for x in np.arange(bay_x0 + 0.5, bay_x1, 0.5):
        for z in np.arange(z_back, z_front, 1.0):
            ds.append(W.WATER_LEVEL - s.at(float(x), float(z))[0])
        # (sampled on a 0.5 x 1.0 m lattice over the WHOLE bay footprint, not
        # at its centre point: a centre-point reading is what let a 9.16 x
        # 21.57 m hull be declared afloat in 4.21 m of water while part of its
        # own footprint stood on a bank 0.465 m ABOVE the water plane.)
    ds = np.asarray(ds)
    fits = [v for v, g in WF.VESSELS.items()
            if g["hull_w"] <= clear - 0.5
            and g["hull_l"] <= (z_front - z_back) - 1.0
            and g["draught_m"] + WF.KEEL_CLEARANCE_M <= ds.min()]
    best = None
    for v in fits:
        g = WF.VESSELS[v]
        if best is None or (g["draught_m"], g["hull_w"] * g["hull_l"]) > (
                WF.VESSELS[best]["draught_m"],
                WF.VESSELS[best]["hull_w"] * WF.VESSELS[best]["hull_l"]):
            best = v
    return dict(bay=dict(
        clear_width_m=round(clear, 3), length_m=round(z_front - z_back, 3),
        x=[bay_x0, bay_x1], z=[z_back, z_front],
        depth_min_m=round(float(ds.min()), 3),
        depth_med_m=round(float(np.median(ds)), 3),
        depth_max_m=round(float(ds.max()), 3),
        samples=int(ds.size),
        hull_classes_that_fit=sorted(fits), largest=best,
        why=("the bay's depth is the MINIMUM over its whole footprint on a "
             "0.5 x 1.0 m lattice, because a hull sits on the shallowest "
             "sample under it, not on the average")),
        aprons=dict(west=west, east=east, back=back),
        walls=walls, gable=gable, roofs=roofs,
        span_m=round(span, 3), eaves_y=round(eaves_y, 3),
        ridge_y=round(ridge_y, 3), roof_courses=courses,
        pieces=len(pl) - n0,
        enclosed=dict(long_walls=2, back_wall=1, gable_filled=True,
                      seaward_gable="OPEN - this is the bay mouth",
                      roof=True))


def solve_boathouse(s, *, x_range: tuple[float, float],
                    z_range: tuple[float, float], bay_clear: float,
                    length: float, want_m: float = 3.0,
                    floor_m: float = 2.2, step: float = 2.0) -> dict:
    """WHERE the boathouse goes, decided by the measured water under its bays.

    The first version of this build hard-coded two boathouse positions and
    three of the five bays came back fitting NOTHING -- one bay's shallowest
    sample was -0.755 m, i.e. DRY LAND under a boat bay.  A boathouse whose
    bay a hull cannot enter is exactly the "boathouse that is really decking"
    defect this build exists to correct, so the position is solved against the
    depth under each bay's own footprint instead of chosen.

    `want_m` is the depth one bay must reach (CargoShip's 2.0 m draught plus
    1.0 m keel clearance); `floor_m` is the depth BOTH must reach (LittleBoat).
    Returns the best candidate and the full refusal count.
    """
    grid = Grid(s, sum(x_range) / 2.0, sum(z_range) / 2.0)
    pw = BOATHOUSE["pier_w"]
    aw = BOATHOUSE["apron_w"]
    best = None
    tried = 0
    rej = dict(offpatch=0, too_shallow_both=0, no_deep_bay=0, apron_dry=0)
    for xc in np.arange(x_range[0], x_range[1] + 0.01, step):
        for zb in np.arange(z_range[0], z_range[1] + 0.01, step):
            tried += 1
            zf = zb + length
            l0, l1 = xc - pw / 2.0 - bay_clear, xc - pw / 2.0
            r0, r1 = xc + pw / 2.0, xc + pw / 2.0 + bay_clear
            depths = []
            off = False
            for (a, b) in ((l0, l1), (r0, r1)):
                blk = grid.box((a + b) / 2.0, (zb + zf) / 2.0, b - a, zf - zb)
                if blk is None:
                    off = True
                    break
                depths.append(float((W.WATER_LEVEL - blk).min()))
            if off:
                rej["offpatch"] += 1
                continue
            # the aprons and the centre pier stand on piles in water; they must
            # not be driven into dry ground, which would be a pier on a lawn
            apron_ok = True
            for ax in (l0 - aw / 2.0, (l0 + l1) / 2.0, xc, (r0 + r1) / 2.0,
                       r1 + aw / 2.0):
                blk = grid.box(ax, (zb + zf) / 2.0, aw, zf - zb)
                if blk is None or float((W.WATER_LEVEL - blk).max()) < 0.4:
                    apron_ok = False
                    break
            if not apron_ok:
                rej["apron_dry"] += 1
                continue
            if min(depths) < floor_m:
                rej["too_shallow_both"] += 1
                continue
            if max(depths) < want_m:
                rej["no_deep_bay"] += 1
                continue
            score = (min(depths), max(depths))
            if best is None or score > best["score"]:
                best = dict(score=score, x_centre=float(xc), z_back=float(zb),
                            z_front=float(zf), bay_depths=[round(d, 3) for d in depths])
    return dict(best=best, tried=tried, refusals=rej,
                bay_clear_m=bay_clear, length_m=length,
                want_m=want_m, floor_m=floor_m)


def boathouse(pl: Plan, s, *, x_centre: float, z_back: float, z_front: float,
              bay_clear: float, site: str, name: str) -> dict:
    """Two bays sharing a centre pier.  `bay_clear` is the CLEAR width of each.

    Two sheds rather than one 29 m span, because a single gable over both bays
    would put the ridge 8 m above the eaves and there is nothing to carry it.
    """
    pw = BOATHOUSE["pier_w"]
    left0 = x_centre - pw / 2.0 - bay_clear
    left1 = x_centre - pw / 2.0
    right0 = x_centre + pw / 2.0
    right1 = x_centre + pw / 2.0 + bay_clear
    a = shed(pl, s, bay_x0=left0, bay_x1=left1, z_back=z_back,
             z_front=z_front, why=f"{name}/bay1")
    b = shed(pl, s, bay_x0=right0, bay_x1=right1, z_back=z_back,
             z_front=z_front, why=f"{name}/bay2")
    return dict(name=name, site=site, bays=2, bay1=a, bay2=b,
                x_centre=x_centre, pier_w=pw,
                pieces=a["pieces"] + b["pieces"])


# ---------------------------------------------------------------------------
# the sea wall and its gate
# ---------------------------------------------------------------------------

def sea_wall(s) -> dict:
    """The 132.0 m curtain across the mouth, with the ship gate in the middle.

    Module count is solved, not chosen: the line length divided by the kit's
    measured 20.009 m pitch has to leave a gate between 9.5 m (wider than the
    largest admitted beam plus slack) and 20 m (wider than that is not a gate).
    At 132.0 m exactly one solution exists -- 6 modules and 11.946 m -- and it
    closes the line to the millimetre.

    Each module's foot is the APPLIED surface at its own xz.  Nothing is
    propped and nothing is written into the ground: `fb_fortress_wall` is
    11.866 m tall from its foot, the deepest sample on the line is 23.402, so
    the worst module still tops out 5.268 m above the water plane.
    """
    span = WALL["x1"] - WALL["x0"]
    pitch = WALL["pitch"]
    sol = [(n, span - n * pitch) for n in range(3, 10)
           if 9.5 <= span - n * pitch <= 20.0]
    if not sol:
        raise SystemExit(
            f"no module count divides the {span:.3f} m wall line leaving a "
            f"9.5-20 m gate; the line has to move, and moving it means "
            f"re-running the closing sweep, not fudging the pitch")
    n, gate = sol[-1]
    mid = n // 2
    segs = []
    x = WALL["x0"]
    gate_x = None
    for i in range(n):
        if i == mid:
            gate_x = (x, x + gate)
            x += gate
        cx = x + pitch / 2.0
        kind = (KIT["tower"] if i in (0, n - 1)
                else KIT["stair_l"] if i == 1
                else KIT["stair_r"] if i == n - 2
                else KIT["wall"])
        foot = s.at(cx, WALL["z"])[0]
        h = KIT["tower_h_m"] if kind == KIT["tower"] else KIT["wall_h_m"]
        segs.append(dict(i=i, prefab=kind, x=round(cx, 3), z=WALL["z"],
                         y=round(foot, 3), yaw=WALL["yaw"],
                         depth_m=round(W.WATER_LEVEL - foot, 3),
                         top_y=round(foot + h, 3),
                         freeboard_m=round(foot + h - W.WATER_LEVEL, 3),
                         seated_on=("seabed" if foot < W.WATER_LEVEL
                                    else "dry ground")))
        x += pitch
    # --- the gate: measured clear opening, and what fits through it --------
    gd = []
    for gx in np.arange(gate_x[0], gate_x[1] + 0.25, 0.5):
        gd.append(W.WATER_LEVEL - s.at(float(gx), WALL["z"])[0])
    gd = np.asarray(gd)
    fits, refused = [], []
    for v, g in WF.VESSELS.items():
        why = []
        if g["hull_w"] > gate:
            why.append(f"beam {g['hull_w']} m > gate {gate:.3f} m")
        if g["draught_m"] + WF.KEEL_CLEARANCE_M > gd.max():
            why.append(f"needs {g['draught_m'] + WF.KEEL_CLEARANCE_M:.2f} m of "
                       f"water, the gate has {gd.max():.3f} m")
        (refused if why else fits).append(
            v if not why else dict(vessel=v, why="; ".join(why)))
    # --- the barrier: REFUSED, and here is the measurement that refuses it -
    #
    # This block used to tile three `fb_fortress_portcullis` per row, two
    # rows, across the gate.  Six were built (ledger seq 2690) and all six
    # were RETIRED by `settlements/sea_gate_fix.py`, because the prefab
    # CANNOT clear a ship channel -- in any yaw, at any height.
    #
    # Why no earlier reading could see it: `Door.SetState` (assembly_valheim
    # IL, class `Door`) does exactly `m_animator.SetInteger("state", s)` and
    # `m_openEnable.SetActive(...)`, and `m_openEnable` is null here.  So the
    # OPEN geometry lives in an animation clip, and every collider number in
    # `KIT` above is the CLOSED pose.  MEASURED by instantiating the prefab
    # inactive in a sandbox copy of this server's own tree, stripping its
    # MonoBehaviours and driving the Animator by hand 0 -> 1 -> -1 -> 0:
    #
    #   THREE solid boxes, and only ONE of them moves.
    #     leaf `door`         x [-2.47495, 2.64786]  lifts +3.7431 m, returns
    #     post `collider`     x [ 2.53573, 2.91000]  0.374 m wide, 5.834 m
    #     post `collider (1)` x [-2.79427,-2.42000]  tall, NEVER MOVES
    #
    # One panel's open aperture is therefore 4.956 m wide by 3.751 m tall,
    # and TILING MAKES IT WORSE: three panels put six immovable post columns
    # in the 11.951 m throat and the widest post-free interval with every
    # panel fully open is 3.181 m -- narrower than the 3.777 m slots the
    # wrongly-yawed build left.  Moving them does not help either, proved in
    # both directions: everything in the prefab sits on one side of the pivot
    # and the posts are static, so posts below a 2.0 m keel put the closed
    # leaf's TOP under water (it blocks nothing), and posts above the ship put
    # the closed leaf's BOTTOM above the ship (same).  Flipping it mirrors
    # both cases and drives the open leaf down into the channel.
    #
    # Nor is there a substitute, MEASURED in the same run: `fb_fortress_gates`
    # is a double door whose jambs are static at 3.058 m clear;
    # `fb_fortress_wall_portcullis` has no `Door` and no `Animator` at all --
    # it is a wall with a 6.851 m hole; and `GB_Ship_Gate`, the widest keyless
    # leaf in a 7,262-prefab ZNetScene sweep, is 7.090 m against a 9.16 m
    # CargoShip beam.  THERE IS NO CLOSABLE SHIP GATE IN THIS GAME BUILD, so
    # `closure(..., gate_open=False)` below studies a state the world cannot
    # reach and is kept only as the arithmetic that says so.
    #
    # The gate is therefore an OPEN throat, which is what the operator asked
    # for: "you cant sail a ship through the harbour walls".
    panels: list[dict] = []
    return dict(
        z=WALL["z"], x=[WALL["x0"], WALL["x1"]], span_m=round(span, 3),
        modules=n, module_pitch_m=pitch, segments=segs,
        gate=dict(x=[round(gate_x[0], 3), round(gate_x[1], 3)],
                  clear_width_m=round(gate, 3),
                  depth_min_m=round(float(gd.min()), 3),
                  depth_max_m=round(float(gd.max()), 3),
                  depth_mean_m=round(float(gd.mean()), 3),
                  hull_classes_admitted=sorted(fits),
                  hull_classes_refused=refused,
                  barrier=dict(prefab=KIT["portcullis"], placed=False,
                               columns=0, rows=0, panels=len(panels),
                               panel_w_m=KIT["panel_w_m"],
                               panel_h_m=KIT["panel_h_m"],
                               column_pitch_m=None, overlap_m=None,
                               covers_y=None,
                               reaches_below_plane_m=None,
                               reaches_above_plane_m=None,
                               is_a_door=True,
                               open_aperture_w_m=4.956,
                               open_aperture_h_m=3.751,
                               widest_post_free_interval_if_tiled_m=3.181,
                               door_evidence=("Animator driven by hand in a "
                                              "sandbox: Door present, "
                                              "m_keyItem=null, "
                                              "m_canNotBeClosed=0, leaf lifts "
                                              "+3.7431 m and returns"),
                               refused_why=(
                                   "the leaf lifts but the two frame posts "
                                   "(0.374 m wide, 5.834 m tall) never move, "
                                   "so three tiled panels leave six immovable "
                                   "columns in the throat and the widest "
                                   "post-free interval when fully open is "
                                   "3.181 m against a 9.16 m CargoShip beam. "
                                   "No installed prefab offers a closable "
                                   "opening at that beam. The gate stands "
                                   "open.")),
                  panels=panels),
        no_terrain_write=True,
        why=("a structure, not an earthwork: the kit is 11.866 m tall with its "
             "pivot at the foot and has no WearNTear, so it stands on the "
             "seabed with freeboard and needs no footing. A terrain write here "
             "would drain water out from under the shoreline, which is the one "
             "damage class this project cannot undo."))


# ---------------------------------------------------------------------------
# lighthouse, beacons, guideposts
# ---------------------------------------------------------------------------

def beacon(pl: Plan, x: float, z: float, surface_y: float, why: str) -> dict:
    """The fleet's everburning lamp, seated on the surface a player stands on.

    `piece_groundtorch`'s pivot is 0.65359 m ABOVE its own base (MEASURED
    y[-0.6536,+0.8226]), which is the OPPOSITE sense to the fb_ kit's
    pivot-at-foot.  Getting this the wrong way round is not cosmetic: on a
    tower it is the difference between a lamp on the parapet and a lamp
    hovering 5.173 m over it.
    """
    y = surface_y + WF.LAMP["pivot_above_surface"]
    pl.add(WF.LAMP["prefab"], x, y, z, 0.0, why)
    return dict(prefab=WF.LAMP["prefab"], x=x, y=round(y, 3), z=z,
                seated_on_y=round(surface_y, 3),
                pivot_above_surface=WF.LAMP["pivot_above_surface"],
                ints=WF.LAMP["ints"])


def lighthouse(s, x: float, z: float) -> dict:
    """A `fb_fortress_wall_tower` on the headland, with the beacon on its
    FIGHTING PLATFORM at +10.229, not on the 15.402 m mesh top.

    The 5.173 m between those two numbers is the measured gap that put three
    tower lamps in the air earlier tonight, so the platform figure is used and
    the mesh figure is recorded beside it as the thing it is not.
    """
    foot = s.at(x, z)[0]
    if foot <= W.WATER_LEVEL + 0.5:
        raise SystemExit(
            f"lighthouse seat ({x},{z}) stands at h={foot:.3f}, which is under "
            f"or in the water plane at {W.WATER_LEVEL}. A beacon in the surf is "
            f"the early-dock defect wearing a lamp; move the seat rather than "
            f"writing terrain to raise it.")
    return dict(prefab=KIT["tower"], x=x, z=z, y=round(foot, 3), yaw=0.0,
                platform_y=round(foot + KIT["tower_platform_y"], 3),
                cap_y=round(foot + KIT["tower_cap_y"], 3),
                mesh_top_y=round(foot + KIT["tower_h_m"], 3),
                beacon_seats_on="platform",
                mesh_top_is_not_the_surface=True,
                ground_y=round(foot, 3),
                freeboard_m=round(foot - W.WATER_LEVEL, 3))


def guidepost(pl: Plan, s, x: float, z: float, text: str, yaw: float,
              why: str, surface_y: float | None = None) -> dict:
    """A `sign` with its text in the ZDO, on the ground, not floating.

    The text goes through `waterfront.sign_blob`, which is this repo's one ZDO
    encoder, so the payload is the same shape every other sign on this world
    carries and the reader is the same decoder.

    `surface_y` overrides the ground for a sign standing ON A DECK: the deck's
    walking surface is 30.6 and the seabed under it can be 6 m lower, so a
    quayside sign seated off `s.at` would be a sign underwater.
    """
    g = s.at(x, z)[0] if surface_y is None else float(surface_y)
    y = g + 1.4
    pl.add(SIGN, x, y, z, yaw, why)
    return dict(prefab=SIGN, x=x, y=round(y, 3), z=z, yaw=yaw,
                ground_y=round(g, 3), text=text,
                seated_on="deck" if surface_y is not None else "ground",
                blob=WF.sign_blob(text))


# ---------------------------------------------------------------------------
# the quays, laid on the MEASURED waterline rather than a straight line
# ---------------------------------------------------------------------------

def quay(pl: Plan, s, *, z0: float, z1: float, side: str, x_centre: float,
         width: float = 4.0, why: str = "") -> dict:
    """A deck hugging one bank, following the waterline row by row.

    The west shore of this inlet moves 24 m east between z = 716 and z = 728.
    A quay laid on a straight line would therefore be 24 m inland at one end
    and 24 m out to sea at the other, so each 2 m row is placed against its
    OWN measured waterline and the row's offsets are recorded.
    """
    n0 = len(pl)
    rows = []
    for z in np.arange(z0 + 1.0, z1, 2.0):
        xw, xe = waterline(s, float(z), x_centre)
        if side == "west":
            a, b = xw + 1.0, xw + 1.0 + width
        else:
            a, b = xe - 1.0 - width, xe - 1.0
        a = math.floor(a / 2.0) * 2.0 + 1.0
        tiles = 0
        for x in np.arange(a, b, 2.0):
            d = W.WATER_LEVEL - s.at(float(x), float(z))[0]
            if d < -0.6:          # more than 0.6 m of dry BANK: not a quay tile
                continue
            pl.add(GEOM["floor"]["prefab"], float(x), DECK_AUTHOR_Y, float(z),
                   0.0, why)
            tiles += 1
        rows.append(dict(z=float(z), waterline_x=[round(xw, 2), round(xe, 2)],
                         tiles=tiles, x_from=round(a, 2)))
        if int(z) % 4 == 0:
            for x in np.arange(a, b, 4.0):
                sb = s.at(float(x), float(z))[0]
                if sb < W.WATER_LEVEL:
                    piles(pl, float(x), float(z), sb, why=why + " pile")
    return dict(side=side, z=[z0, z1], width_m=width, rows=len(rows),
                tiles=sum(r["tiles"] for r in rows),
                pieces=len(pl) - n0, row_detail=rows,
                deck_walking_y=DECK_Y)


# ---------------------------------------------------------------------------
# THE DREDGED U.  Three connected quays, a cut basin, ships ALONGSIDE
# ---------------------------------------------------------------------------
#
# WHY THIS REPLACES THE WATERLINE-HUGGING QUAYS ABOVE.  `quay()` lays each 2 m
# row against its own measured waterline, which is right for a promenade and
# WRONG for berthing: the west shore of this inlet moves 24 m east between
# z = 716 and z = 728, and a hull is a straight 21.57 m line.  A ragged face
# touches a hull at one point.  So the U is a STRAIGHT-faced structure standing
# off the bank, with the ragged bank behind it and measured links to shore.
#
# WHY DREDGING IS ALLOWED HERE AT ALL, when the project's standing rule is
# "never write terrain below the water plane".  That rule was derived from
# LEVELLING A FOOTPRINT UP to a datum, which REMOVES water.  Dredging LOWERS
# the bed.  The two were measured against each other in one run, in a sandbox
# mirror of this server, by `terraform/WaterProof.cs`:
#
#   a 7.000 m CUT at (1088,720):  bed 23.726 -> 16.726, water 6.274 -> 13.274 m
#   a RAISE to y=30.900 at (1064,686): bed 27.208 -> 30.900,
#                                      water 2.792 -> -0.900 m, i.e. DRY GROUND
#   ring samples at 1, 2, 4 and 8 m outside every patch: delta EXACTLY 0.0000,
#                                      depth after == depth before
#   a real VikingShip left to physics over the cut settled at y 30.107 and
#                                      bobbed 30.068..30.117 for 20 s
#
# and the mechanism is read out of the deployed assembly (md5
# 89ffdb64fefebc011f5a9a826f2968bf): `WaterVolume::GetWaterSurface` returns
# `transform.position.y + wave + m_surfaceOffset`, and THE HEIGHTMAP IS NOT IN
# IT.  Each zone carries one water volume with `transform.y` 30.0000,
# `m_surfaceOffset` 0.0000 and a TRIGGER collider 64 x 60 x 64 m spanning
# y -20 .. +40 -- present even in a zone whose ground is 0.0 % wet.  The +/-8 m
# clamp cannot cut past 8 m below generated, so a dredge cannot leave that
# volume.
#
# THE ONE RULE THAT FALLS OUT OF IT, and it is enforced in code below rather
# than in a comment: THE DREDGE NEVER WRITES A POSITIVE DELTA.
# `target = min(generated, design_bed)` so a cut can only ever deepen.  A fill
# is the operation that was measured to remove water, and the dredge is
# structurally incapable of performing one.
#
# THE OTHER THING THAT RUN MEASURED, and it is a DESIGN constraint: two hulls
# dropped into 13 x 13 m dredged pockets with 6 m of water in them went AGROUND
# ON THE RIM at y 32.825 and 33.902, because a 9.16 x 21.57 m hull does not fit
# in a 13 m hole and its bow and stern rest on the uncut ground outside.  So
# every berth here is verified with `waterfront.hull_depths` under the hull's
# OWN ellipse at its OWN yaw, re-run against the POST-DREDGE surface.

# The U, solved rather than hand-picked.  2,688 candidate (west face, east
# face, south face) triples were swept over x_w 1032..1060, x_e 1096..1120,
# z_s 674..702 with the north ends fixed at z = 746, and scored on
#   * every basin sample reachable to the design bed inside the clamp, and
#   * no arm deck tile standing on ground above the deck's own authored y.
# 2,064 triples pass both.  This one maximises basin area among those with zero
# buried arm samples: 62 x 60 m of basin, worst cut 4.4264 m against the 7.0 m
# working limit.
U = dict(
    x_west_face=1052.0,      # inner (basin) face of the west arm
    x_east_face=1114.0,      # inner face of the east arm
    z_south_face=686.0,      # inner face of the south arm
    z_north_end=746.0,       # arm ends: the wall body spans z 748.6..755.4
                             # (fb_fortress_wall is 6.800 m deep, pivot at foot)
    arm_w=6.0,               # 3 deck tiles -- a working quay, not a catwalk
    design_bed=24.0,         # 6.0 m of water: twice the deepest ADMITTED
                             # requirement (CargoShip 2.0 + 1.0 = 3.0 m)
    jamb_bed=26.5,           # the gate's two outermost throat columns: 3.5 m of
                             # water.  See DREDGE_REGIONS for why they are not
                             # cut to the design bed.
    berth_standoff=7.0,      # berth centreline off the dock face: half the
                             # widest ADMITTED beam (10.07 / 2 = 5.04) plus
                             # 1.96 m of fender
)

# Hulls the 11.946 m gate ADMITS, by measured beam.  `BigCargoShip` (beam
# 14.10 m) is REFUSED BY THE GATE AND ONLY BY THE GATE -- the operator has
# already ruled that the gate stands and that this hull is excluded, and the
# dredged basin is measured below to float it with +3.500 m of margin at every
# berth, which is a fact he should own rather than one that stays in a script.
GATE_CLEAR_M = WALL["gate_clear_m"]
ADMITTED = tuple(v for v, d in sorted(WF.VESSELS.items(),
                                      key=lambda kv: -kv[1]["draught_m"])
                 if d["hull_w"] <= GATE_CLEAR_M)
REFUSED_BY_GATE = tuple(v for v, d in WF.VESSELS.items()
                        if d["hull_w"] > GATE_CLEAR_M)


def dredge_regions() -> list[dict]:
    """The cut, region by region, each with the bed it is cut to and why.

    THE APPROACH IS PART OF THE CUT, not an afterthought.  A dock face with a
    shelf in front of it is the same defect as a road that stops 10 m short of
    the town it was built for, and the natural channel is MEASURED to be too
    shallow for the deepest admitted hull exactly where it matters: the
    shallowest generated sample inside a 9.16 m-beam corridor centred on the
    gate is **2.890 m at (1062, 752)** against `CargoShip`'s 3.000 m
    requirement -- short by 0.110 m, which is 4x the instrument's own 0.0275 m
    noise floor.  Without the throat cut the gate refuses the deepest hull the
    basin can otherwise take.
    """
    xw, xe = U["x_west_face"], U["x_east_face"]
    zs, zn = U["z_south_face"], U["z_north_end"]
    bed, jamb = U["design_bed"], U["jamb_bed"]
    g0, g1 = WALL["gate"]
    return [
        dict(name="basin", x=(xw, xe), z=(zs, zn), bed=bed,
             why="the U's enclosed water: four berths alongside and the "
                 "turning circle between them"),
        dict(name="throat", x=(1062.0, 1071.0), z=(zn + 1.0, 757.0), bed=bed,
             why="the gate passage. The two 20.009 m modules flanking the gate "
                 f"end at x {g0:.2f} and start at x {g1:.2f}, so these ten "
                 "columns are clear of both module bodies"),
        dict(name="throat_jamb_west", x=(1061.0, 1061.0), z=(zn + 1.0, 757.0),
             bed=jamb,
             why="NOT cut to the design bed. The west gate module's body ends "
                 "at x 1060.53, halfway between samples 1060 and 1061, so the "
                 "heightmap mesh under its jamb face is the mean of those two "
                 "samples. Cutting 1061 to 24.0 would put that mesh at 25.714 "
                 "against the module's own foot at 26.111 -- a measured "
                 "0.397 m gap under the jamb. At 26.5 the mesh reads 26.964, "
                 "0.853 m ABOVE the foot, and the column still carries 3.500 m "
                 "of water, which clears every admitted hull"),
        dict(name="throat_jamb_east", x=(1072.0, 1072.0), z=(zn + 1.0, 757.0),
             bed=jamb, why="the same, mirrored, against the east module body "
                           f"starting at x {g1:.2f}"),
        dict(name="approach", x=(1055.0, 1079.0), z=(758.0, 776.0), bed=bed,
             why="the seaward approach. Natural depth in the gate corridor "
                 "MEASURED as 2.822-4.03 m from z 750 to 756 and does not "
                 "reach 6.0 m until z = 774; the cut starts at z = 758, which "
                 "is 2.6 m clear of the wall body's north face at 755.4, so no "
                 "sample under a wall module is touched"),
    ]


def zones_containing(x: float, z: float) -> list[tuple[int, int]]:
    """Every zone whose 65x65 lattice holds this sample.

    `tcdata.zone_of` answers ONE zone, which is right for a position and wrong
    for a SAMPLE: neighbouring zones SHARE their edge samples (sample 0 of one
    is sample 64 of the next), so a cut that stops on a zone line and writes
    only one side leaves a one-sample ridge the other zone still holds at its
    generated height.  This returns both.
    """
    out = []
    zx0, zz0 = TC.zone_of(x, z)
    for zx in (zx0 - 1, zx0, zx0 + 1):
        for zz in (zz0 - 1, zz0, zz0 + 1):
            cx, cz = TC.zone_centre(zx, zz)
            if abs(x - cx) <= 32.0 + 1e-9 and abs(z - cz) <= 32.0 + 1e-9:
                out.append((zx, zz))
    return sorted(out)


def dredge_plan(s) -> dict:
    """Every dredge sample with its generated height, written height, delta and
    clamp margin, plus one `tcdata.Compiler` per zone the cut touches.

    Three guards, each of which has a measurement behind it:

    1. `target = min(generated, bed)` -- THE DREDGE CANNOT FILL.  A positive
       delta is the operation measured to remove water; this one cannot emit
       one, and the returned stats assert it.
    2. a sample needing more than `CUT_MAX_M` is CLIPPED to it and COUNTED,
       never silently deepened past the clamp.  A shrink is silent unless it is
       recorded, so `clipped` and `shortfall_m` are in the output whether or not
       they are zero.
    3. every zone that holds a sample gets the sample, edge samples included in
       both zones (`zones_containing`).
    """
    rows: dict[tuple[int, int], dict] = {}
    for reg in dredge_regions():
        x0, x1 = reg["x"]
        z0, z1 = reg["z"]
        for x in range(int(x0), int(x1) + 1):
            for z in range(int(z0), int(z1) + 1):
                applied, gen, prior = s.at(float(x), float(z))
                # The delta is written against the GENERATED height because
                # that is what TerrainComp::ApplyToHeightmap adds to; `prior`
                # is any delta already in the ledger here and is carried
                # forward rather than overwritten.
                target = min(applied, reg["bed"])
                delta = target - gen
                clipped = False
                if delta < -CUT_MAX_M:
                    delta, clipped = -CUT_MAX_M, True
                if delta > 0.0:
                    delta, clipped = prior, False   # never fill; keep what is there
                row = dict(x=x, z=z, region=reg["name"], generated=round(gen, 4),
                           prior_delta=round(prior, 4), delta=round(delta, 4),
                           written=round(gen + delta, 4),
                           depth_before=round(W.WATER_LEVEL - applied, 4),
                           depth_after=round(W.WATER_LEVEL - (gen + delta), 4),
                           clamp_margin=round(CLAMP_M - abs(delta), 4),
                           target_bed=reg["bed"], clipped=clipped,
                           shortfall=round(max(0.0, (gen + delta) - reg["bed"]), 4)
                           if clipped else 0.0)
                have = rows.get((x, z))
                if have is None or row["delta"] < have["delta"]:
                    rows[(x, z)] = row

    comps: dict[tuple[int, int], TC.Compiler] = {}
    for (x, z), row in rows.items():
        if row["delta"] == 0.0 and row["prior_delta"] == 0.0:
            continue                       # already deeper than the design bed
        for zx, zz in zones_containing(float(x), float(z)):
            comp = comps.setdefault((zx, zz), TC.Compiler(zx, zz))
            cx, cz = TC.zone_centre(zx, zz)
            ix, iz = TC.vertex_mask_index(cx, cz, float(x), float(z))
            comp.set_height(ix, iz, row["delta"])

    cut = [r for r in rows.values() if r["delta"] < 0.0]
    deltas = [r["delta"] for r in rows.values()]
    depths = sorted(r["depth_after"] for r in rows.values())
    stats = dict(
        samples=len(rows), cut=len(cut),
        already_deep_enough=len(rows) - len(cut),
        min_delta=min(deltas), max_delta=max(deltas),
        any_fill=any(d > 0.0 for d in deltas),
        max_cut_m=round(-min(deltas), 4),
        worst_clamp_margin_m=round(CLAMP_M + min(deltas), 4),
        clipped=sum(1 for r in rows.values() if r["clipped"]),
        worst_shortfall_m=round(max((r["shortfall"] for r in rows.values()),
                                    default=0.0), 4),
        depth_after_min=depths[0], depth_after_max=depths[-1],
        depth_after_median=depths[len(depths) // 2],
        zones=sorted(comps),
        zone_samples={f"{zx},{zz}": comps[(zx, zz)].counts()["height_modified"]
                      for zx, zz in sorted(comps)},
    )
    if stats["any_fill"]:
        raise SystemExit(
            "REFUSING: the dredge emitted a POSITIVE delta, which is the "
            "operation MEASURED to remove water (a raise to y=30.900 took "
            "2.792 m of water to -0.900 m). target = min(generated, bed) is "
            "supposed to make that impossible, so this is a bug, not a site.")
    per_region = {}
    for reg in dredge_regions():
        rs = [r for r in rows.values() if r["region"] == reg["name"]]
        if not rs:
            continue
        per_region[reg["name"]] = dict(
            samples=len(rs), bed=reg["bed"],
            depth_after_min=min(r["depth_after"] for r in rs),
            depth_after_max=max(r["depth_after"] for r in rs),
            max_cut_m=round(-min(r["delta"] for r in rs), 4), why=reg["why"])
    return dict(rows=rows, comps=comps, stats=stats, per_region=per_region,
                regions=dredge_regions(),
                design_bed=U["design_bed"], water_level=W.WATER_LEVEL,
                clamp_m=CLAMP_M, cut_max_m=CUT_MAX_M)


class DredgedSurface(WF.Surface):
    """The surface the harbour WILL have, for measuring berths before the cut.

    It is a real `waterfront.Surface` with one in-memory patch whose heights
    are the applied heights PLUS this dredge, so `hull_depths` and `sea_mask`
    -- the project's own instruments, unmodified -- measure the dredged bed.
    Reimplementing the ellipse or the flood fill here would answer a different
    question with the same words.

    THE DOCSTRING ABOVE WAS TRUE AND THE CODE WAS NOT.  `__init__` loaded
    `W.load(base_field)` -- the raw PatchScan field, the GENERATED world --
    set `self.applied = None`, and `at()` returned `(g, g, 0.0)`, so every
    height this class answered was missing every terrain write the ledger
    holds outside the dredge rows.  `piles()` samples this surface for its
    seabed, so the city's pile columns were counted against the generated bed
    and every later write that RAISED it left poles underneath: MEASURED on
    Ulfsland as `harbour-village-sudrvik` 1 of 583 pieces buried at 0.096 m
    on `wood_pole_log_4`, and the same mechanism across the over-water half
    of the gate's 45 `buried` bodies.

    The composition now happens once, here, in the order the world applies
    it: generated, then the ledger's deltas, then this dredge on top -- the
    dredge writes an ABSOLUTE height, so it correctly overrides whatever was
    there.  `at()` reports the generated half and the composed delta
    separately, as the base class's contract says it must.
    """

    def __init__(self, plan: dict, base_field: str = CITY_FIELD,
                 patch_id: str = "cityA", actor: str = "HarbourCity"):
        import dataclasses  # noqa: PLC0415
        import applied as AP  # noqa: PLC0415  roads/
        base = W.load(base_field)[patch_id]
        fld = dataclasses.replace(base, id=patch_id + "_dredged",
                                  heights=base.heights.copy())
        live = AP.Applied(actor=actor)
        # The ledger's terrain deltas, at the field's own INTEGER lattice.
        # `Applied.zone` caches per zone, so this is one blob parse per zone
        # touched, not one per sample.
        composed = 0
        for i in range(fld.n):
            sz = int(round(fld.z0 + i))
            for j in range(fld.n):
                d, _w = live.sample(int(round(fld.x0 + j)), sz)
                if d:
                    fld.heights[i, j] += d
                    composed += 1
        self.applied_samples_composed = composed
        for (x, z), row in plan["rows"].items():
            i = int(round(z - fld.z0))
            j = int(round(x - fld.x0))
            if 0 <= i < fld.n and 0 <= j < fld.n:
                fld.heights[i, j] = row["written"]
        self.fields = {fld.id: fld}
        self.field_names = [
            f"(in-memory {fld.id} = {base_field} + {composed} ledger terrain "
            f"sample(s) via applied.Applied(actor={actor!r}) + this dredge)"]
        self.applied = live
        self.generated_only = base
        self.dredged = fld

    def at(self, x: float, z: float) -> tuple[float, float, float]:
        """`(applied_and_dredged, generated, delta)`.

        The delta is REPORTED, not re-added: it is already baked into the
        patch, and adding it again would double every ledger write under the
        harbour.
        """
        g = float(self.patch_for(x, z).height_at(x, z))
        gen = float(self.generated_only.height_at(x, z))
        return g, gen, g - gen


def closure(fld, need_m: float, *, gate_open: bool,
            probe: tuple[float, float] = (1083.0, 716.0)) -> dict:
    """The manual's topological closure test, re-run on the DREDGED bed.

    Both directions, because a guard that is only shown to pass is not shown to
    be a guard: with the gate OPEN the basin must be connected to the patch
    boundary at the hull's own required depth, and with the gate BARRED it must
    NOT be.  The wall line is rasterised and dilated to half the module's
    MEASURED 6.800 m depth, and the labels are computed from scratch after the
    cut -- reusing pre-cut labels keeps the basin and the sea in one label and
    is the step that makes this test always pass.
    """
    from scipy import ndimage  # noqa: PLC0415
    deep = (W.WATER_LEVEL - fld.heights) >= need_m
    r = KIT["wall_depth_m"] / 2.0
    xx = np.broadcast_to(fld.world_x(np.arange(fld.n))[None, :], (fld.n, fld.n))
    zz = np.broadcast_to(fld.world_z(np.arange(fld.n))[:, None], (fld.n, fld.n))
    on_wall = ((np.abs(zz - WALL["z"]) <= r)
               & (xx >= WALL["x0"]) & (xx <= WALL["x1"]))
    if gate_open:
        g0, g1 = WALL["gate"]
        on_wall = on_wall & ~((xx >= g0) & (xx <= g1))
    lab, _ = ndimage.label(deep & ~on_wall)
    edge = set(lab[0, :].tolist()) | set(lab[-1, :].tolist()) \
        | set(lab[:, 0].tolist()) | set(lab[:, -1].tolist())
    edge.discard(0)
    i, j = fld.index(*probe)
    basin = int(lab[i, j])
    return dict(required_depth_m=need_m, gate_open=gate_open,
                basin_wet=basin != 0, basin_label=basin,
                basin_area_m2=int((lab == basin).sum()) if basin else 0,
                connected_to_open_sea=bool(basin and basin in edge),
                probe=list(probe))


def arm(pl: Plan, s, *, name: str, x: tuple[float, float],
        z: tuple[float, float], plan: dict, why: str) -> dict:
    """One arm of the U: a 2 m-tile deck at the 30.6 datum on measured piles.

    THE PILE LATTICE IS ASSERTED TO LIE OUTSIDE THE CUT.  A pile is seated on
    the bed at its own xz; if the dredge moved that sample the pile would be
    computed against a height the cut then lowered and would end up hanging.
    The arm geometry is chosen so the pile columns fall on the landward side of
    the dock face, and rather than trust that, every pile position is checked
    against `plan["rows"]` and the build refuses if one is inside.
    """
    n0 = len(pl)
    xs = np.arange(x[0] + 1.0, x[1], 2.0)
    zs = np.arange(z[0] + 1.0, z[1], 2.0)
    tiles = 0
    for tx in xs:
        for tz in zs:
            pl.add(GEOM["floor"]["prefab"], float(tx), DECK_AUTHOR_Y, float(tz),
                   0.0, why)
            tiles += 1
    stacks = 0
    poles = 0
    inside = []
    skipped_piles: list[tuple] = []
    for tx in xs[::2]:
        for tz in zs[::2]:
            key = (int(round(tx)), int(round(tz)))
            if key in plan["rows"] and plan["rows"][key]["delta"] < 0.0:
                inside.append(key)
                continue
            sb = s.at(float(tx), float(tz))[0]
            if sb >= DECK_AUTHOR_Y - 0.5:
                # Nothing to pile into: the bed here is already at the deck's
                # own authored height, so `piles()` would drive its one
                # part-buried pole 3.4 m into the bank.  MEASURED on the east
                # arm's x=1119, z=743 column, bed 30.009 against an authored
                # 30.503.  `quay()` guards the same way with `sb < 30.0`.
                skipped_piles.append((float(tx), float(tz), round(sb, 3)))
                continue
            poles += piles(pl, float(tx), float(tz), sb, why=why + " pile")
            stacks += 1
    if inside:
        raise SystemExit(
            f"REFUSING {name}: {len(inside)} pile positions sit INSIDE the "
            f"dredge footprint ({inside[:5]}...). A pile seated on a sample "
            f"the cut then lowers hangs over the new bed, which is the "
            f"floating defect with extra steps. Move the arm or the cut.")
    seabed = [s.at(float(tx), float(tz))[0] for tx in xs[::2] for tz in zs[::2]]
    return dict(name=name, x=[x[0], x[1]], z=[z[0], z[1]], tiles=tiles,
                pile_stacks=stacks, poles=poles, pieces=len(pl) - n0,
                pile_positions_skipped=skipped_piles,
                deck_walking_y=DECK_Y, deck_authored_y=DECK_AUTHOR_Y,
                pile_seabed_min=round(min(seabed), 3),
                pile_seabed_max=round(max(seabed), 3),
                pile_reach_max_m=round(DECK_AUTHOR_Y - min(seabed), 3), why=why)


def shore_link(pl: Plan, s, *, name: str, axis: str, fixed: float,
               from_edge: float, to_land: float, land_h: float,
               width: float = 4.0, why: str = "") -> dict:
    """A trestle from an arm's outer edge to the first FIRM ground, with a stair.

    `FIRM` is `h > 31.0`, i.e. 1.0 m of freeboard over the plane and 0.5 m over
    `LAND_LEVEL`, and three consecutive samples must clear it -- the west bank
    here is a marsh whose surface sits AT the waterline, and a landing chosen on
    one lucky cell at 30.52 is a landing in the surf.  The deck datum is 30.6
    and the landings measure 31.08-31.21, so the step is UP off the deck and a
    single `wood_stair` (MEASURED rise 1.050 m) covers it.
    """
    n0 = len(pl)
    lo, hi = min(from_edge, to_land), max(from_edge, to_land)
    tiles = 0
    along = np.arange(math.floor(lo) + 1.0, hi + 1.0, 2.0)
    across = np.arange(fixed - width / 2.0 + 1.0, fixed + width / 2.0, 2.0)
    dropped = []
    placed: list[tuple[float, float]] = []
    for a in along:
        for c in across:
            px, pz = (float(a), float(c)) if axis == "x" else (float(c), float(a))
            sb = s.at(px, pz)[0]
            if sb > DECK_Y:
                # A DECK TILE THE BANK WOULD BURY IS NOT A DECK TILE.  The
                # landing is firm ground at 31.08-31.21 and the deck's walking
                # surface is 30.6, so the last metre or two of every trestle
                # rides INSIDE the bank.  MEASURED pre-fix: 4 tiles on the west
                # link buried by up to 0.243 m.  They are dropped and counted,
                # and the `wood_stair` is what bridges the gap -- which is what
                # it was placed for.
                dropped.append((px, pz, round(sb, 3)))
                continue
            pl.add(GEOM["floor"]["prefab"], px, DECK_AUTHOR_Y, pz, 0.0, why)
            placed.append((px, pz))
            tiles += 1
    stacks = 0
    for a in along[::2]:
        for c in across:
            px, pz = (float(a), float(c)) if axis == "x" else (float(c), float(a))
            sb = s.at(px, pz)[0]
            if sb < W.WATER_LEVEL:
                piles(pl, px, pz, sb, why=why + " pile")
                stacks += 1
    # The stair bridges the LAST PLACED TILE to the bank, and it is placed
    # against that tile rather than against the landing.  MEASURED pre-fix:
    # putting it at `to_land` left a 3 m hole between the stair and the deck,
    # because the tiles the bank would have buried were dropped and the stair
    # was still aimed at the original landing.  `wood_stair`'s treads DESCEND
    # with +z (MEASURED from PieceGeometry), so a run that climbs towards
    # bearing B is placed at yaw B + 180.
    inward = -1.0 if to_land < from_edge else 1.0
    bearing = (270.0 if inward < 0 else 90.0) if axis == "x" \
        else (180.0 if inward < 0 else 0.0)
    placed_along = [q[0] if axis == "x" else q[1] for q in placed]
    if placed_along:
        edge = min(placed_along) if inward < 0 else max(placed_along)
    else:
        edge = from_edge
    step_along = edge + inward * 2.0
    sx = (step_along, fixed) if axis == "x" else (fixed, step_along)
    pl.add(GEOM["stair"]["prefab"], float(sx[0]), DECK_AUTHOR_Y, float(sx[1]),
           (bearing + 180.0) % 360.0, why + " stair to shore")
    stair_head_y = DECK_AUTHOR_Y + GEOM["stair"]["rise"]
    stair_ground = s.at(float(sx[0]), float(sx[1]))[0]
    return dict(name=name, axis=axis, fixed=fixed,
                from_edge=from_edge, to_land=to_land, span_m=abs(to_land - from_edge),
                tiles_dropped_as_buried=dropped,
                deck_edge_along=edge, stair_at=[float(sx[0]), float(sx[1])],
                stair_head_y=round(stair_head_y, 3),
                stair_ground_y=round(stair_ground, 3),
                stair_head_above_ground_m=round(stair_head_y - stair_ground, 3),
                landing_h=round(land_h, 3),
                step_from_deck_m=round(land_h - DECK_Y, 3),
                tiles=tiles, pile_stacks=stacks, pieces=len(pl) - n0,
                stair_yaw=(bearing + 180.0) % 360.0, why=why)


# Where a berth sits on each face, and how long a berth is.  The berth PITCH is
# 30.0 m against the longest ADMITTED hull at 21.57 m, so a hull has 4.2 m clear
# at each end of its own box and 8.4 m between neighbours.  Two per long face.
# The SOUTH face carries the boathouse instead of a fifth berth: its two 11.5 m
# bays reach z = 710, and a fifth berth on the south face would put a 21.57 m
# hull across both bay mouths.  That trade is stated rather than hidden -- the
# U's 182 m of dock face is four open berths PLUS two covered bays, not five
# berths.
U_BERTHS = [
    dict(id="W1", face="west", x=U["x_west_face"] + U["berth_standoff"], z=702.0,
         yaw=0.0, why="west face, south berth; bow north towards the gate"),
    dict(id="W2", face="west", x=U["x_west_face"] + U["berth_standoff"], z=732.0,
         yaw=0.0, why="west face, north berth"),
    dict(id="E1", face="east", x=U["x_east_face"] - U["berth_standoff"], z=702.0,
         yaw=180.0, why="east face, south berth; bow south, port side to the quay"),
    dict(id="E2", face="east", x=U["x_east_face"] - U["berth_standoff"], z=732.0,
         yaw=180.0, why="east face, north berth"),
]


def u_berth_table(ds: "DredgedSurface") -> list[dict]:
    """Every berth against every hull, under the hull's OWN ellipse at its OWN
    yaw, on the DREDGED bed, flood-filled to open sea at that hull's own
    required depth.

    A point reading is not reported at all here, deliberately.  A 4.21 m point
    reading is what put a Longship aground at `ferry-terminal-eastisle` with
    -0.465 m under its bow for 48 h 47 m, and printing one next to the ellipse
    result invites somebody to quote the wrong column.
    """
    out = []
    for b in U_BERTHS:
        hulls = {}
        for v, spec in sorted(WF.VESSELS.items(), key=lambda kv: -kv[1]["draught_m"]):
            need = spec["draught_m"] + WF.KEEL_CLEARANCE_M
            res = WF.hull_depths(ds, b["yaw"], b["x"], b["z"], v)
            if res is None:
                hulls[v] = dict(measured=False,
                                why="no hull sample inside the patch")
                continue
            mn, mx, conn, ns, _f = res
            gate_ok = spec["hull_w"] <= GATE_CLEAR_M
            floats = mn >= need and conn
            hulls[v] = dict(
                measured=True, draught_m=spec["draught_m"], required_m=need,
                beam_m=spec["hull_w"], length_m=spec["hull_l"],
                shallowest_under_hull_m=round(mn, 3),
                deepest_under_hull_m=round(mx, 3),
                margin_m=round(mn - need, 3),
                water_under_keel_m=round(mn - spec["draught_m"], 3),
                every_sample_open_sea=conn, hull_samples=ns,
                yaw_deg=b["yaw"], floats=floats,
                clears_margin_floor=mn >= need + WF.BERTH_MARGIN_FLOOR_M,
                admitted_by_gate=gate_ok,
                berthable_here=bool(floats and gate_ok),
                why=("REFUSED BY THE GATE ONLY: beam "
                     f"{spec['hull_w']:.2f} m > {GATE_CLEAR_M:.3f} m clear "
                     "opening. It floats here; it cannot reach here.")
                if floats and not gate_ok else "")
        best = max((v for v, h in hulls.items()
                    if h.get("berthable_here")),
                   key=lambda v: WF.VESSELS[v]["draught_m"], default=None)
        out.append(dict(**b, largest_berthable=best,
                        refused_by_gate=[v for v, h in hulls.items()
                                         if h.get("floats") and not h.get("admitted_by_gate")],
                        hulls=hulls))
    return out

# The covered bays, on the SOUTH face, in the middle of it.  Both mouths open
# NORTH into the basin, which is the hand `boathouse()` already builds (`z_back`
# closed, `z_front` the mouth).  x_centre is set so the 29 m total footprint
# clears both inner berths' measured hull boxes: W1's hull reaches x 1064.04 and
# the west apron starts at 1066.0 (1.96 m clear); E1's hull starts at x 1101.96
# and the east apron ends at 1095.0 (6.96 m clear).
#
# THE PILES HERE STAND ON THE DREDGED BED, not the generated one, and that is
# why the build order is terrain-then-structures and not the other way round.
# The bays sit inside the cut, so `boathouse()` is handed the DREDGED surface;
# handing it the pre-cut surface would compute every pile against a bed the
# dredge then drops 4 m, which is the floating defect arrived at from the other
# side.
U_BOATHOUSE = dict(
    name="ulfsgard/boathouse-quay-south",
    x_centre=1080.5,
    bay_clear=11.5,       # > the widest ADMITTED beam (WarShip 10.07 m) + fender
    z_back=686.0,         # the south arm's inner face
    z_front=710.0,        # 24.0 m of bay against the longest admitted hull,
                          # 21.57 m -- 2.43 m of slack, and the mouth is open
)

# Deck furniture.  `piece_groundtorch`'s own AABB is y[-0.6536, +0.8226], so its
# origin sits 0.65359 ABOVE the surface it stands on -- the OPPOSITE sense to
# the `fb_` kit's pivot-at-foot, and inheriting the wrong one is what put three
# beacons 5.173 m in the air.  On a deck whose WALKING surface is 30.6 that is
# 31.25359, not 30.6 and not 31.2.
TORCH_ABOVE_SURFACE = 0.65359
TORCH_PITCH_M = 12.0


def dock_lighting(pl: Plan, arms: list[dict], why: str) -> dict:
    """A torch every 12 m along the INNER edge of each arm.

    The inner edge, because that is the face a hull comes alongside and the
    thing a night approach needs to see. Placed on the deck, not on the seabed:
    the seat is the deck's WALKING surface and the prefab's own offset.
    """
    n0 = len(pl)
    seats = []
    for a in arms:
        x0, x1 = a["x"]
        z0, z1 = a["z"]
        if (x1 - x0) < (z1 - z0):                      # a north-south arm
            inner_x = x1 - 1.0 if a["name"] == "arm_west" else x0 + 1.0
            for z in np.arange(z0 + 1.0, z1, TORCH_PITCH_M):
                seats.append((float(inner_x), float(z)))
        else:                                          # the south arm
            for x in np.arange(x0 + 1.0, x1, TORCH_PITCH_M):
                seats.append((float(x), float(z1 - 1.0)))
    for x, z in seats:
        pl.add(TORCH, x, DECK_Y + TORCH_ABOVE_SURFACE, z, 0.0, why)
    return dict(torches=len(seats), pitch_m=TORCH_PITCH_M,
                seat_y=round(DECK_Y + TORCH_ABOVE_SURFACE, 5),
                seated_on="the deck's 30.6 m walking surface",
                pivot_offset_m=TORCH_ABOVE_SURFACE, pieces=len(pl) - n0)


# THE LOWEST BOX A PLAYER CAN STAND ON, per hull, relative to the hull's own
# origin.  MEASURED from the SOLID non-trigger BOX colliders in the modded
# sandbox dump, taking the LOWEST top face among boxes of at least 0.8 m2 -- the
# surface nearest the quay is the one a player steps onto, not the median deck
# plate and certainly not the mesh bound.  `RowingCanoe` has NO standable box at
# all in the dump (its single box tops out at +6.392, which is rigging), so it is
# recorded as unmeasurable rather than given a plausible number.
HULL_STEP_ON_M = {
    "VikingShip": 0.666, "MercantShip": 0.666, "CargoShip": 0.866,
    "WarShip": 0.965, "BigCargoShip": 1.173, "Karve": 0.274,
    "LittleBoat": 0.134, "DoubleRowingCanoe": 0.186, "RowingCanoe": None,
}
# How far a floating hull's ORIGIN sits below the local water surface.  MEASURED
# twice in the same `WaterProof` run, on the same hull, over two different beds:
#   over the 7 m dredge   origin 30.1068, surface 30.3070  ->  0.2002 m
#   over natural water    origin 29.7723, surface 30.0354  ->  0.2631 m
# So 0.232 +/- 0.032 m, and the spread is wave phase rather than bed.  This is
# NOT `Ship.m_waterLevelOffset` (1.5 m for a Longship): that is where the FLOAT
# COLLIDER centre sits, not the origin, and using it here would drop every deck
# 1.27 m and invent a step that does not exist.
HULL_ORIGIN_BELOW_SURFACE_M = 0.232


def walk_aboard(berths: list[dict]) -> dict:
    """Can a player actually get from the dock face onto a berthed hull?

    A berth that floats a hull nobody can board is scenery with a depth
    measurement attached, so the step is computed and reported per hull rather
    than assumed from the fact that both numbers are near 30.
    """
    rows = []
    for v in sorted(WF.VESSELS):
        on = HULL_STEP_ON_M.get(v)
        admitted = WF.VESSELS[v]["hull_w"] <= GATE_CLEAR_M
        if on is None:
            rows.append(dict(vessel=v, admitted_by_gate=admitted,
                             measurable=False,
                             why="no solid box of >= 0.8 m2 above the keel in "
                                 "the collider dump; boarded from the water"))
            continue
        deck = W.WATER_LEVEL - HULL_ORIGIN_BELOW_SURFACE_M + on
        step = deck - DECK_Y
        rows.append(dict(
            vessel=v, admitted_by_gate=admitted, measurable=True,
            step_on_above_origin_m=on,
            hull_deck_y=round(deck, 3), quay_walking_y=DECK_Y,
            step_m=round(step, 3),
            direction="down onto the hull" if step < 0 else "up onto the hull",
            verdict=("a step" if abs(step) <= 0.5 else
                     "a drop down, and a JUMP on the way back")))
    worst = max((r for r in rows if r.get("measurable")
                 and r["admitted_by_gate"]), key=lambda r: abs(r["step_m"]))
    large = [r for r in rows if r.get("measurable") and r["admitted_by_gate"]
             and WF.VESSELS[r["vessel"]]["cls"] == "large"]
    return dict(
        quay_walking_y=DECK_Y,
        origin_below_surface_m=HULL_ORIGIN_BELOW_SURFACE_M,
        berths_checked=[b["id"] for b in berths],
        rows=rows,
        worst_admitted=dict(vessel=worst["vessel"], step_m=worst["step_m"]),
        worst_large_admitted_m=round(max(abs(r["step_m"]) for r in large), 3),
        why=("every open berth on this U is at the same 30.6 datum, so the step "
             "is a property of the HULL and not of the berth; it is reported "
             "once per hull rather than five times"))


def gate_passage(surf, label: str) -> dict:
    """What a hull actually finds under itself while passing the gate.

    The gate's depth profile in `sea_wall` is sampled along the wall LINE, and
    its `max` is what the admission test uses -- which is the same shape of
    error as the 4.21 m point reading that put a Longship aground: the number
    that decides is the SHALLOWEST sample under the hull's own footprint while
    it is in the opening, not the deepest sample on the line.  So the passage is
    measured with `hull_depths` at the gate centre, at the heading a hull
    actually enters on (yaw 0, bow north / bow south through a north-facing
    mouth), on whatever surface it is handed.
    """
    gx = sum(WALL["gate"]) / 2.0
    rows = {}
    for v, spec in sorted(WF.VESSELS.items(), key=lambda kv: -kv[1]["draught_m"]):
        need = spec["draught_m"] + WF.KEEL_CLEARANCE_M
        beam_ok = spec["hull_w"] <= GATE_CLEAR_M
        worst = None
        for yaw in (0.0, 180.0):
            res = WF.hull_depths(surf, yaw, gx, WALL["z"], v)
            if res is None:
                continue
            if worst is None or res[0] < worst[0]:
                worst = res
        if worst is None:
            rows[v] = dict(measured=False)
            continue
        mn, mx, conn, ns, _f = worst
        rows[v] = dict(measured=True, required_m=need, beam_m=spec["hull_w"],
                       beam_fits=beam_ok,
                       shallowest_under_hull_m=round(mn, 3),
                       margin_m=round(mn - need, 3),
                       clears_margin_floor=mn >= need + WF.BERTH_MARGIN_FLOOR_M,
                       hull_samples=ns, every_sample_open_sea=conn,
                       passes=bool(beam_ok and mn >= need))
    return dict(surface=label, gate_x=round(gx, 3), gate_z=WALL["z"],
                clear_width_m=GATE_CLEAR_M, hulls=rows,
                passes=[v for v, r in rows.items() if r.get("passes")],
                refused=[_gate_refusal(v, r) for v, r in rows.items()
                         if r.get("measured") and not r.get("passes")])


def _gate_refusal(v: str, r: dict) -> dict:
    """Why this hull cannot pass, in the units of the thing that stopped it."""
    if not r.get("beam_fits"):
        return dict(vessel=v, gate="beam",
                    why=f"beam {r['beam_m']:.2f} m > {GATE_CLEAR_M:.3f} m clear "
                        f"opening; it is refused by the GATE, not by the water")
    return dict(vessel=v, gate="depth",
                why=f"shallowest sample under its own hull "
                    f"{r['shallowest_under_hull_m']:.3f} m < "
                    f"{r['required_m']:.3f} m required")


def dredge_location_check(plan: dict, keep: list[dict]) -> dict:
    """The stand-off measured from the CUT ITSELF, sample by sample.

    The `location_check` on a `terrain_write` exists for exactly one defect:
    "a pad cutting the ground out from under a `LocationProxy`" -- MEASURED
    once on this world at 6.65 m of cut under a buried treasure chest.  So the
    number it wants is the distance from the nearest DREDGED SAMPLE to the
    nearest marker, not the distance from some other fixture of the same
    design.  Checking the sea wall's abutment here would refuse a cut 50 m away
    for a tower that writes no terrain at all.

    Every marker inside the stand-off is reported with its distance, so a
    narrowing is a number rather than a boolean.
    """
    cut = [(x, z) for (x, z), r in plan["rows"].items() if r["delta"] < 0.0]
    if not keep:
        return dict(nearest_m=None, nearest_marker=None, offenders=[],
                    clear=True, cut_samples=len(cut))
    best = None
    offenders = []
    for k in keep:
        dmin = min(math.hypot(x - k["x"], z - k["z"]) for x, z in cut)
        if best is None or dmin < best[0]:
            best = (dmin, k)
        if dmin < LOC_STANDOFF_M:
            offenders.append(dict(marker=[k["x"], k["z"]],
                                  dump_entry=k.get("nearest"),
                                  dist_m=round(dmin, 2),
                                  inside_standoff_by_m=round(
                                      LOC_STANDOFF_M - dmin, 2)))
    return dict(nearest_m=round(best[0], 2),
                nearest_marker=[best[1]["x"], best[1]["z"]],
                nearest_dump_entry=best[1].get("nearest"),
                offenders=offenders, clear=not offenders,
                standoff_m=LOC_STANDOFF_M, cut_samples=len(cut),
                markers_tested=len(keep))


def design_u(s) -> dict:
    """The whole dredged U, in one pass, with every refusal counted.

    Order matters and is the build order: the CUT is computed first, a surface
    that includes it is derived, and every structure is then seated on THAT.
    """
    out: dict = dict(site=SITE_A, water_level_y=W.WATER_LEVEL,
                     deck_walking_y=DECK_Y, field=CITY_FIELD, u=dict(U))

    # --- 0. virgin ground? ------------------------------------------------
    # The whole design assumes the harbour is virgin generated ground, because
    # `DredgedSurface` composes the cut onto GENERATED heights.  If anything
    # already wrote terrain here that assumption is wrong, so it is measured
    # rather than inherited from a survey taken a day ago.
    prior = []
    for x in range(1040, 1130, 2):
        for z in range(676, 782, 2):
            _a, _g, d = s.at(float(x), float(z))
            if abs(d) > 1e-6:
                prior.append((x, z, round(d, 4)))
    out["prior_terrain"] = dict(
        samples_probed=len(range(1040, 1130, 2)) * len(range(676, 782, 2)),
        samples_with_delta=len(prior), worst=prior[:8],
        window="x[1040,1130] z[676,782] on a 2 m lattice",
        verdict="virgin generated ground" if not prior else "PRIOR DELTA PRESENT")

    # --- 1. the cut -------------------------------------------------------
    plan = dredge_plan(s)
    out["dredge"] = dict(stats=plan["stats"], per_region=plan["per_region"],
                         regions=plan["regions"],
                         design_bed_y=plan["design_bed"],
                         design_depth_m=W.WATER_LEVEL - plan["design_bed"],
                         clamp_m=plan["clamp_m"], cut_max_m=plan["cut_max_m"])
    ds = DredgedSurface(plan)

    # --- 2. the berths, on the cut bed -----------------------------------
    out["berths"] = u_berth_table(ds)
    out["admitted"] = list(ADMITTED)
    out["refused_by_gate"] = list(REFUSED_BY_GATE)
    out["gate_clear_m"] = GATE_CLEAR_M

    # --- 3. closure, both directions, on the cut bed ----------------------
    out["closure"] = [closure(ds.dredged, need, gate_open=g)
                      for need in (1.8, 2.5, 2.7, 3.0) for g in (True, False)]

    # --- 4. the structures, seated on the cut bed -------------------------
    pl = Plan(SITE_A)
    xw, xe = U["x_west_face"], U["x_east_face"]
    zs, zn = U["z_south_face"], U["z_north_end"]
    aw = U["arm_w"]
    arms = [
        arm(pl, ds, name="arm_west", x=(xw - aw, xw), z=(zs, zn), plan=plan,
            why="U/arm west"),
        arm(pl, ds, name="arm_east", x=(xe, xe + aw), z=(zs, zn), plan=plan,
            why="U/arm east"),
        arm(pl, ds, name="arm_south", x=(xw - aw, xe + aw), z=(zs - aw, zs),
            plan=plan, why="U/arm south"),
    ]
    out["arms"] = arms
    out["dock_face_m"] = dict(
        west=zn - zs, east=zn - zs, south=xe - xw,
        total=(zn - zs) * 2 + (xe - xw),
        basin_clear_width_m=xe - xw, basin_clear_length_m=zn - zs,
        turning_circle_needed_m=round(1.5 * max(
            WF.VESSELS[v]["hull_l"] for v in ADMITTED), 2),
        why="1.5 x the longest ADMITTED hull is the turning diameter a basin "
            "has to hold; both basin dimensions must beat it")
    out["links"] = [
        shore_link(pl, ds, name="link_west", axis="x", fixed=712.0,
                   from_edge=xw - aw, to_land=1021.0, land_h=31.208,
                   why="U/link west"),
        shore_link(pl, ds, name="link_east", axis="x", fixed=724.0,
                   from_edge=xe + aw, to_land=1127.0, land_h=31.089,
                   why="U/link east"),
        shore_link(pl, ds, name="link_south", axis="z", fixed=1048.0,
                   from_edge=zs - aw, to_land=677.0, land_h=31.083,
                   why="U/link south"),
    ]
    out["boathouse"] = boathouse(
        pl, ds, x_centre=U_BOATHOUSE["x_centre"], z_back=U_BOATHOUSE["z_back"],
        z_front=U_BOATHOUSE["z_front"], bay_clear=U_BOATHOUSE["bay_clear"],
        site=SITE_A, name=U_BOATHOUSE["name"])
    out["lighting"] = dock_lighting(pl, arms, why="U/dock lighting")

    # --- 5. the wall and the gate, RE-SOLVED on the cut bed ---------------
    # The module line does not move: none of the six module centres
    # (x 1010.50 .. 1122.50 at z 752) lies inside the cut, so every foot is the
    # height it always was.  The BARRIER does move, because the portcullis
    # columns stand in the throat and the throat is cut, and the gate's own
    # water changes with it.  Both are recomputed off the dredged surface and
    # the differences are reported rather than restated.
    out["sea_wall"] = sea_wall(ds)
    out["sea_wall_pre_dredge"] = sea_wall(s)
    pre, post = out["sea_wall_pre_dredge"], out["sea_wall"]
    out["sea_wall_moved"] = dict(
        module_feet_moved=[
            dict(i=a["i"], prefab=a["prefab"], x=a["x"],
                 foot_before=a["y"], foot_after=b["y"],
                 moved_m=round(b["y"] - a["y"], 4))
            for a, b in zip(pre["segments"], post["segments"])
            if abs(b["y"] - a["y"]) > 1e-6],
        gate_depth_before=dict(
            min=pre["gate"]["depth_min_m"], mean=pre["gate"]["depth_mean_m"],
            max=pre["gate"]["depth_max_m"]),
        gate_depth_after=dict(
            min=post["gate"]["depth_min_m"], mean=post["gate"]["depth_mean_m"],
            max=post["gate"]["depth_max_m"]),
        barrier_covers_before=pre["gate"]["barrier"]["covers_y"],
        barrier_covers_after=post["gate"]["barrier"]["covers_y"],
        clear_width_unchanged_m=post["gate"]["clear_width_m"],
        admitted_before=sorted(pre["gate"]["hull_classes_admitted"]),
        admitted_after=sorted(post["gate"]["hull_classes_admitted"]),
        why=("the throat cut is what admits the deepest hull: the gate's "
             "shallowest sample was measured at "
             f"{pre['gate']['depth_min_m']:.3f} m against CargoShip's 3.000 m "
             "requirement, and reads "
             f"{post['gate']['depth_min_m']:.3f} m after the cut"))
    for sg in post["segments"]:
        pl.add(sg["prefab"], sg["x"], sg["y"], sg["z"], sg["yaw"],
               "ulfsgard/seawall")
    for pn in post["gate"]["panels"]:
        pl.add(pn["prefab"], pn["x"], pn["y"], pn["z"], pn["yaw"],
               "ulfsgard/gate-portcullis")
    out["tower_beacons"] = [
        beacon(pl, sg["x"], sg["z"], sg["y"] + KIT["tower_platform_y"],
               "ulfsgard/gate-beacon")
        for sg in post["segments"] if sg["prefab"] == KIT["tower"]]

    # --- 6. the lighthouse, on dry ground, clear of the cut ---------------
    lh = None
    for (lx, lz) in [(1140.0, 766.0), (1142.0, 770.0), (1144.0, 774.0),
                     (1136.0, 762.0), (1148.0, 778.0), (1006.0, 768.0),
                     (1002.0, 772.0)]:
        try:
            cand = lighthouse(s, lx, lz)
        except SystemExit:
            continue
        if lh is None or cand["freeboard_m"] > lh["freeboard_m"]:
            lh = cand
    if lh is None:
        raise SystemExit("no dry seat for the lighthouse on either headland; "
                         "refusing rather than writing terrain to make one")
    out["lighthouse"] = lh
    pl.add(lh["prefab"], lh["x"], lh["y"], lh["z"], lh["yaw"],
           "ulfsgard/lighthouse")
    out["lighthouse"]["beacon"] = beacon(pl, lh["x"], lh["z"], lh["platform_y"],
                                         "ulfsgard/lighthouse-beacon")

    # --- 7. guideposts, on the decks they label --------------------------
    # Into a SEPARATE plan: a sign and a portal carry a ZDO payload
    # (`data=<blob>`), and a `spawn_plan` batch has no room for one -- its wire
    # is a single semicolon-joined run of bare `spawn_object` lines.  So these
    # go out as individual `spawn` ops through `waterfront.spawn_op`, which is
    # the verb that already knows how to attach a blob, and they are kept out
    # of `pl` so nothing is placed twice.
    pd = Plan(SITE_A)
    out["_plan_data"] = pd
    out["guideposts"] = [
        guidepost(pd, s, 1002.0, 744.0, "Ulfsgard  -  West Gate", 0.0,
                  "ulfsgard/guidepost-westgate"),
        guidepost(pd, s, 1124.0, 744.0, "Ulfsgard  -  East Gate", 180.0,
                  "ulfsgard/guidepost-eastgate"),
        guidepost(pd, ds, 1049.0, 743.0,
                  "West Quay  -  berths W2, W1  |  6.0 m dredged", 90.0,
                  "ulfsgard/guidepost-westquay", surface_y=DECK_Y),
        guidepost(pd, ds, 1117.0, 743.0,
                  "East Quay  -  berths E2, E1  |  6.0 m dredged", 270.0,
                  "ulfsgard/guidepost-eastquay", surface_y=DECK_Y),
        guidepost(pd, ds, 1083.0, 681.0,
                  "South Quay  -  boathouse  |  no BigCargoShip: gate 11.95 m",
                  0.0, "ulfsgard/guidepost-southquay", surface_y=DECK_Y),
    ]

    # --- 8. the portal, LAST, and on land -------------------------------
    # Terrain before portals, and a portal on DRY GROUND rather than on a deck:
    # the south link's landing is the one seat here that is firm ground inside
    # the wall, measured at h 31.083 with three consecutive samples over 31.0.
    px, pz = 1048.0, 673.0
    pg = s.at(px, pz)[0]
    if pg <= W.LAND_LEVEL:
        raise SystemExit(
            f"REFUSING the portal seat ({px}, {pz}): ground {pg:.3f} is not "
            f"above LAND_LEVEL {W.LAND_LEVEL}. A portal in the surf is the "
            f"same class of defect as a spawn point under water.")
    pd.add(PORTAL, px, pg, pz, 0.0, "ulfsgard/portal-harbour")
    out["portal"] = dict(prefab=PORTAL, x=px, z=pz, y=round(pg, 3),
                         ground_y=round(pg, 3),
                         freeboard_m=round(pg - W.WATER_LEVEL, 3),
                         tag="ulfsgard",
                         why="on dry ground behind the south quay, placed after "
                             "the terrain write because terrain comes before "
                             "portals")

    # --- 9. every fixture against the LIVE generated-location markers -----
    keep = location_keepout()
    out["location_checks"] = [
        location_clear(keep, WALL["x0"], WALL["z"], "wall west abutment"),
        location_clear(keep, (WALL["x0"] + WALL["x1"]) / 2, WALL["z"], "gate"),
        location_clear(keep, WALL["x1"], WALL["z"], "wall east abutment"),
        location_clear(keep, lh["x"], lh["z"], "lighthouse"),
        location_clear(keep, (xw + xe) / 2, (zs + zn) / 2, "dredged basin centre"),
        location_clear(keep, xw - aw / 2, (zs + zn) / 2, "west arm"),
        location_clear(keep, xe + aw / 2, (zs + zn) / 2, "east arm"),
        location_clear(keep, (xw + xe) / 2, zs - aw / 2, "south arm"),
        location_clear(keep, U_BOATHOUSE["x_centre"],
                       (U_BOATHOUSE["z_back"] + U_BOATHOUSE["z_front"]) / 2,
                       "boathouse"),
        location_clear(keep, px, pz, "portal"),
    ]
    out["dredge_location_check"] = dredge_location_check(plan, keep)
    # A NARROWING IS SILENT UNLESS IT IS RECORDED.  Fixtures inside the 32 m
    # stand-off are listed here with their distances rather than being allowed
    # to fail the terrain write they have nothing to do with: a structure
    # cannot drain anything, only a terrain write can, and these write no
    # terrain.
    out["location_narrowings"] = [
        dict(fixture=c["name"], nearest_m=c["nearest_m"],
             inside_standoff_by_m=round(LOC_STANDOFF_M - c["nearest_m"], 2),
             marker=c.get("nearest_marker"),
             dump_entry=c.get("marker_nearest_dump_entry"),
             writes_terrain=False,
             why="a structure inside the stand-off. It is recorded rather than "
                 "moved because the stand-off exists to stop a TERRAIN WRITE "
                 "cutting the ground out from under a LocationProxy, and this "
                 "fixture writes none. The dredge's own stand-off is measured "
                 "separately over the cut samples.")
        for c in out["location_checks"]
        if c.get("nearest_m") is not None and not c.get("clear")]

    out["gate_passage"] = dict(
        after_dredge=gate_passage(ds, "dredged bed (this design)"),
        before_dredge=gate_passage(WF.Surface(field="/tmp/harbour/h1m.bin",
                                              extra=CITY_FIELD),
                                   "generated bed (what the gate is today)"))
    out["walk_aboard"] = walk_aboard(out["berths"])
    out["plan_pieces"] = pl.counts()
    out["pieces"] = len(pl)
    out["extent"] = pl.extent()
    out["_plan"] = pl
    out["_dredge"] = plan
    out["_ds"] = ds
    return out

# ---------------------------------------------------------------------------
# the whole design, in one pass, with every refusal counted
# ---------------------------------------------------------------------------

# The LIVE generated-location markers near both sites, measured over the RCON
# socket with a BOUNDED `findObjects -prefab LocationProxy -near x 30 z r` at
# six discs, and cross-read against the mod-free locscan dump for seed
# Pirate68 only to IDENTIFY a marker.  65 markers stand within reach of these
# two sites and only 5 of them match a dump entry to within the identity
# tolerance -- the other 60 are modded POI the dump cannot see at all, which is
# exactly why the live query is the authority and `plan.json` is not.
#
# EVERY marker gets the world-wide worst case of 32.0 m, including the five
# that ARE identified.  That is deliberately conservative: an identified
# `WoodHouse9` would earn 8 m, but a location's PIECES were measured reaching
# 43 m past their own marker on this project, and the cost of the conservative
# number here is a few building plots while the cost of the optimistic one is
# deleted POI content that cannot be put back.
LOC_MARKERS = Path("/tmp/hc/locmarkers.json")
LOC_STANDOFF_M = 32.0


def location_keepout() -> list[dict]:
    if not LOC_MARKERS.exists():
        raise SystemExit(
            f"{LOC_MARKERS} is missing. It is the LIVE LocationProxy set and it "
            f"gates every seat in this build; take it with /tmp/hc/locprobe.py "
            f"AFTER the current world start, and never inherit one from before "
            f"a restart or a plugin change.")
    ms = json.loads(LOC_MARKERS.read_text())
    seen = {}
    for m in ms:
        seen[(round(m["x"], 2), round(m["z"], 2))] = m
    return [dict(x=v["x"], z=v["z"], r=LOC_STANDOFF_M,
                 identified=v["identified"], nearest=v["nearest"])
            for v in seen.values()]


def location_clear(keep: list[dict], x: float, z: float, name: str,
                   *, half: float = 0.0) -> dict:
    """Distance from a fixture to the nearest generated-location marker."""
    if not keep:
        return dict(name=name, nearest_m=None, clear=True)
    d = min((math.hypot(x - k["x"], z - k["z"]) - half, k) for k in keep)
    return dict(name=name, x=x, z=z, nearest_marker=[d[1]["x"], d[1]["z"]],
                nearest_m=round(d[0], 2), standoff_m=LOC_STANDOFF_M,
                clear=bool(d[0] >= LOC_STANDOFF_M),
                marker_nearest_dump_entry=d[1]["nearest"])


def design_a(s) -> dict:
    """Harbour A: the walled city.  Returns the design AND the plan."""
    pl = Plan(SITE_A)
    out: dict = dict(site=SITE_A, water_level_y=W.WATER_LEVEL,
                     deck_walking_y=DECK_Y, field=CITY_FIELD)

    out["sea_wall"] = sea_wall(s)

    # quays down both banks of the basin, inside the wall
    out["quay_west"] = quay(pl, s, z0=660.0, z1=748.0, side="west",
                            x_centre=1084.0, why="ulfsgard/quay-west")
    out["quay_east"] = quay(pl, s, z0=660.0, z1=748.0, side="east",
                            x_centre=1084.0, why="ulfsgard/quay-east")

    # two boathouses on the deeper west side of the basin.  Bay clear width is
    # set from the MEASURED largest admitted beam (WarShip 10.07 m) plus 1.4 m
    # of slack, NOT from a round number, and the gate refuses BigCargoShip so
    # no bay here is sized for it.
    # two boathouses, POSITION SOLVED against the depth under each bay's own
    # footprint.  Bay clear width 11.5 m is set from the largest hull the gate
    # admits (WarShip, beam 10.07 m) plus 1.43 m of slack; the gate refuses
    # BigCargoShip at 14.10 m so no bay inside this wall is sized for it, per
    # the operator's ruling.
    sol_n = solve_boathouse(s, x_range=(1040.0, 1070.0), z_range=(696.0, 726.0),
                            bay_clear=11.5, length=26.0)
    sol_s = solve_boathouse(s, x_range=(1092.0, 1122.0), z_range=(672.0, 702.0),
                            bay_clear=11.5, length=26.0)
    out["boathouse_north_solve"] = {k: v for k, v in sol_n.items() if k != "best"}
    out["boathouse_south_solve"] = {k: v for k, v in sol_s.items() if k != "best"}
    out["boathouse_north_solve"]["best"] = sol_n["best"]
    out["boathouse_south_solve"]["best"] = sol_s["best"]
    if not sol_n["best"]:
        raise SystemExit(f"no boathouse position in the north window clears the "
                         f"depth floor; refusals {sol_n['refusals']}")
    if not sol_s["best"]:
        raise SystemExit(f"no boathouse position in the south window clears the "
                         f"depth floor; refusals {sol_s['refusals']}")
    out["boathouse_north"] = boathouse(
        pl, s, x_centre=sol_n["best"]["x_centre"],
        z_back=sol_n["best"]["z_back"], z_front=sol_n["best"]["z_front"],
        bay_clear=11.5, site=SITE_A, name="ulfsgard/boathouse-north")
    out["boathouse_south"] = boathouse(
        pl, s, x_centre=sol_s["best"]["x_centre"],
        z_back=sol_s["best"]["z_back"], z_front=sol_s["best"]["z_front"],
        bay_clear=11.5, site=SITE_A, name="ulfsgard/boathouse-south")

    # the town bodies
    keep = location_keepout()
    out["location_keepout"] = dict(markers=len(keep), standoff_m=LOC_STANDOFF_M,
                                   identified=sum(1 for k in keep if k["identified"]))
    taken: list[dict] = []
    quarters = []
    for q in QUARTERS_A:
        seats, why = fit_quarter(s, q, MIX_CITY, taken, cap=CAP_CITY,
                                 keepout=keep)
        taken += seats
        quarters.append(dict(q, seats=seats, refusals=why))
    out["quarters"] = quarters
    for qq in quarters:
        for seat in qq["seats"]:
            pl.add(seat["prefab"], seat["x"], seat["y"], seat["z"],
                   seat["yaw"], f"ulfsgard/{qq['id']}")
    out["bodies"] = sum(len(q["seats"]) for q in quarters)

    # --- the wall itself, into the plan ----------------------------------
    for sg in out["sea_wall"]["segments"]:
        pl.add(sg["prefab"], sg["x"], sg["y"], sg["z"], sg["yaw"],
               "ulfsgard/seawall")
    for pn in out["sea_wall"]["gate"]["panels"]:
        pl.add(pn["prefab"], pn["x"], pn["y"], pn["z"], pn["yaw"],
               "ulfsgard/gate-portcullis")
    # a beacon on each gate tower's FIGHTING PLATFORM, +10.229, not the mesh top
    out["tower_beacons"] = []
    for sg in out["sea_wall"]["segments"]:
        if sg["prefab"] != KIT["tower"]:
            continue
        out["tower_beacons"].append(
            beacon(pl, sg["x"], sg["z"], sg["y"] + KIT["tower_platform_y"],
                   "ulfsgard/gate-beacon"))

    # --- the lighthouse on the seaward headland, solved for dry ground ----
    lh = None
    for (lx, lz) in [(1140.0, 766.0), (1142.0, 770.0), (1144.0, 774.0),
                     (1136.0, 762.0), (1148.0, 778.0), (1006.0, 768.0),
                     (1002.0, 772.0)]:
        try:
            cand = lighthouse(s, lx, lz)
        except SystemExit:
            continue
        if lh is None or cand["freeboard_m"] > lh["freeboard_m"]:
            lh = cand
    if lh is None:
        raise SystemExit("no dry seat for the lighthouse on either headland; "
                         "refusing rather than writing terrain to make one")
    out["lighthouse"] = lh
    pl.add(lh["prefab"], lh["x"], lh["y"], lh["z"], lh["yaw"],
           "ulfsgard/lighthouse")
    out["lighthouse"]["beacon"] = beacon(pl, lh["x"], lh["z"], lh["platform_y"],
                                         "ulfsgard/lighthouse-beacon")

    # --- every FIXTURE checked against the live marker set too ------------
    out["location_checks"] = [
        location_clear(keep, WALL["x0"], WALL["z"], "wall west abutment"),
        location_clear(keep, (WALL["x0"] + WALL["x1"]) / 2, WALL["z"], "gate"),
        location_clear(keep, WALL["x1"], WALL["z"], "wall east abutment"),
        location_clear(keep, lh["x"], lh["z"], "lighthouse"),
        location_clear(keep, out["boathouse_north"]["x_centre"],
                       (sol_n["best"]["z_back"] + sol_n["best"]["z_front"]) / 2,
                       "boathouse north"),
        location_clear(keep, out["boathouse_south"]["x_centre"],
                       (sol_s["best"]["z_back"] + sol_s["best"]["z_front"]) / 2,
                       "boathouse south"),
    ]

    # --- guideposts ------------------------------------------------------
    out["guideposts"] = [
        guidepost(pl, s, 1002.0, 744.0, "Ulfsgard  -  West Gate", 0.0,
                  "ulfsgard/guidepost-westgate"),
        guidepost(pl, s, 1124.0, 744.0, "Ulfsgard  -  East Gate", 180.0,
                  "ulfsgard/guidepost-eastgate"),
        guidepost(pl, s, 1084.0, 632.0, "Ulfsgard  -  Market  |  Sudrvik 467 m",
                  0.0, "ulfsgard/guidepost-market"),
    ]
    return out, pl, taken


def design_b(s) -> dict:
    """Harbour B: the OPEN harbour village.  No curtain wall.

    MEASURED refusal, and the operator confirmed it: B's shortest verified
    closing line is 113.0 m of curtain for 4,326 m2 enclosed, and every line
    from z = 450 to z = 498 fails to close the inner basin at all.  113 m of
    wall for that is a bad trade, so B gets quays, a boathouse and berths.
    """
    pl = Plan(SITE_B)
    out: dict = dict(site=SITE_B, curtain_wall=None,
                     curtain_refused=dict(
                         shortest_closing_line_z=440.0, span_m=113.0,
                         encloses_m2=4326, navigable_m2=2762,
                         lines_that_do_not_close="z 450..498",
                         why=("113.0 m of curtain to enclose 4,326 m2 is a bad "
                              "trade for a village, and the operator asked for "
                              "the wall at A. Refused with the number rather "
                              "than built small.")))
    out["quay_west"] = quay(pl, s, z0=452.0, z1=490.0, side="west",
                            x_centre=1196.0, why="sudrvik/quay-west")
    out["quay_east"] = quay(pl, s, z0=452.0, z1=490.0, side="east",
                            x_centre=1196.0, why="sudrvik/quay-east")
    sol = solve_boathouse(s, x_range=(1178.0, 1214.0), z_range=(430.0, 460.0),
                          bay_clear=11.5, length=26.0)
    out["boathouse_solve"] = sol
    if not sol["best"]:
        raise SystemExit(f"no boathouse position at B clears the depth floor; "
                         f"refusals {sol['refusals']}")
    out["boathouse"] = boathouse(
        pl, s, x_centre=sol["best"]["x_centre"], z_back=sol["best"]["z_back"],
        z_front=sol["best"]["z_front"], bay_clear=11.5, site=SITE_B,
        name="sudrvik/boathouse")
    keep = location_keepout()
    out["location_keepout"] = dict(markers=len(keep), standoff_m=LOC_STANDOFF_M)
    taken: list[dict] = []
    quarters = []
    for q in QUARTERS_B:
        seats, why = fit_quarter(s, q, MIX_VILLAGE, taken, cap=CAP_VILLAGE,
                                 keepout=keep)
        taken += seats
        quarters.append(dict(q, seats=seats, refusals=why))
    out["quarters"] = quarters
    for qq in quarters:
        for seat in qq["seats"]:
            pl.add(seat["prefab"], seat["x"], seat["y"], seat["z"],
                   seat["yaw"], f"sudrvik/{qq['id']}")
    out["bodies"] = sum(len(q["seats"]) for q in quarters)
    out["guideposts"] = [
        guidepost(pl, s, 1216.0, 498.0, "Sudrvik  -  Harbour", 180.0,
                  "sudrvik/guidepost-harbour"),
        guidepost(pl, s, 1172.0, 498.0, "Sudrvik  |  Ulfsgard 467 m", 0.0,
                  "sudrvik/guidepost-road"),
    ]
    return out, pl, taken


def berth_table(s, spots: list[tuple[float, float, str]]) -> list[dict]:
    """Per berth, the water depth against THAT hull's own measured draught.

    The depth is the minimum over the hull's own elliptical footprint, flood-fill
    connected to open sea -- not a centre point and not a bounding box.  A
    centre-point reading is what declared a berth afloat in 4.21 m of water
    while part of the same 9.16 x 21.57 m hull stood on a bank 0.465 m ABOVE
    the water plane.
    """
    rows = []
    for spot in spots:
        x, z, note = spot[0], spot[1], spot[2]
        bearing = spot[3] if len(spot) > 3 else 0.0
        d0 = W.WATER_LEVEL - s.at(x, z)[0]
        v = WF.vessel_for(d0, want="any")
        row = dict(x=x, z=z, note=note, point_depth_m=round(d0, 3),
                   largest_hull_at_point=v, hulls={})
        for vessel in sorted(WF.VESSELS):
            g = WF.VESSELS[vessel]
            # (min_depth, max_depth, connected, samples, field) over the
            # hull's own ELLIPTICAL footprint rotated onto the berth bearing.
            hull = WF.hull_depths(s, bearing, x, z, vessel)
            b = WF.berth(s, x, z, vessel, radius_m=10.0)
            need = g["draught_m"] + WF.KEEL_CLEARANCE_M
            row["hulls"][vessel] = dict(
                draught_m=g["draught_m"], required_m=round(need, 2),
                beam_m=g["hull_w"], length_m=g["hull_l"],
                max_depth_in_disc_m=b["max_depth_in_disc_m"],
                navigable_area_m2=b["navigable_area_m2"],
                connected_to_open_sea=b["connected_to_open_sea"],
                hull_footprint=(None if hull is None else dict(
                    shallowest_m=round(hull[0], 3),
                    deepest_m=round(hull[1], 3),
                    all_connected_to_open_sea=hull[2],
                    samples=hull[3],
                    bearing_deg=bearing,
                    floats_under_whole_hull=bool(hull[0] >= need and hull[2]))),
                # THE VERDICT IS THE HULL FOOTPRINT, not the disc.  A disc
                # maximum passes on a pothole; the shallowest sample under the
                # hull is what the keel touches.
                floats=(None if hull is None
                        else bool(hull[0] >= need and hull[2])),
                floats_by_disc_only=b["ok"],
                margin_m=(None if hull is None else round(hull[0] - need, 3)))
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

BERTHS_A = [
    (1070.0, 730.0, "west quay face, north berth", 0.0),
    (1070.0, 712.0, "west quay face, south berth", 0.0),
    (1105.0, 730.0, "east quay face, north berth", 0.0),
    (1105.0, 712.0, "east quay face, south berth", 0.0),
    (1084.0, 744.0, "inner roadstead, behind the gate", 90.0),
]
BERTHS_B = [
    (1215.0, 455.0, "east quay face", 0.0),
    (1185.0, 458.0, "west quay face", 0.0),
    (1196.0, 470.0, "fjord centre, deep berth", 0.0),
]


def solve_berths(s, *, x_range: tuple[float, float],
                 z_range: tuple[float, float], want: str = "CargoShip",
                 bearing: float = 0.0, n: int = 3, apart_m: float = 24.0,
                 step: float = 2.0) -> dict:
    """Berths chosen by the depth under the hull's OWN footprint.

    The first pass hand-picked three berths at B and two of them were aground:
    (1185,458) read 1.96 m at its centre point and every hull class failed
    under its footprint, and (1196,470) read 3.657 m at the point while a
    VikingShip's own ellipse touched 1.30 m short.  That is the same defect
    FerryPair measured on the existing ferry, whose VikingShip sits on a bank
    0.465 m ABOVE the water plane from a record that read 4.21 m at a point.
    So a berth is only a berth if the SHALLOWEST sample under that specific
    hull clears that specific draught plus keel clearance.
    """
    g = WF.VESSELS[want]
    need = g["draught_m"] + WF.KEEL_CLEARANCE_M
    cands = []
    rej = dict(no_footprint=0, too_shallow=0, not_connected=0)
    for x in np.arange(x_range[0], x_range[1] + 0.01, step):
        for z in np.arange(z_range[0], z_range[1] + 0.01, step):
            hull = WF.hull_depths(s, bearing, float(x), float(z), want)
            if hull is None:
                rej["no_footprint"] += 1
                continue
            if not hull[2]:
                rej["not_connected"] += 1
                continue
            if hull[0] < need + WF.BERTH_MARGIN_FLOOR_M:
                rej["too_shallow"] += 1
                continue
            cands.append((float(hull[0]), float(x), float(z), int(hull[3])))
    cands.sort(reverse=True)
    picked = []
    for depth, x, z, nsamp in cands:
        if any(math.hypot(x - p["x"], z - p["z"]) < apart_m for p in picked):
            continue
        picked.append(dict(x=x, z=z, shallowest_under_hull_m=round(depth, 3),
                           hull_samples=nsamp, bearing_deg=bearing,
                           required_m=round(need, 2),
                           margin_m=round(depth - need, 3), sized_for=want))
        if len(picked) >= n:
            break
    return dict(berths=picked, candidates=len(cands), refusals=rej,
                sized_for=want, required_m=round(need, 2),
                margin_floor_m=WF.BERTH_MARGIN_FLOOR_M)


def cmd_design(args) -> int:
    s = surface()
    a, pl_a, taken_a = design_a(s)
    b, pl_b, taken_b = design_b(s)
    # BERTHS ARE SOLVED, not hand-picked, and each is verified under the
    # hull's own footprint afterwards by `berth_table`.
    a["berth_solve"] = solve_berths(s, x_range=(1040.0, 1120.0),
                                    z_range=(672.0, 744.0), n=4, apart_m=26.0)
    b["berth_solve"] = solve_berths(s, x_range=(1176.0, 1240.0),
                                    z_range=(428.0, 486.0), n=3, apart_m=24.0)
    a["berths"] = berth_table(s, [(p["x"], p["z"], f"solved berth {i+1}", 0.0)
                                  for i, p in enumerate(a["berth_solve"]["berths"])])
    b["berths"] = berth_table(s, [(p["x"], p["z"], f"solved berth {i+1}", 0.0)
                                  for i, p in enumerate(b["berth_solve"]["berths"])])
    # THE GATE IS A CONSTRAINT ON THE BERTHS INSIDE IT.  The operator accepted
    # an 11.946 m gate knowing it refuses BigCargoShip, and ruled that no
    # BigCargoShip is to be berthed inside the wall. A berth that floats it is
    # still not a berth FOR it, because it cannot get in.
    admitted = set(a["sea_wall"]["gate"]["hull_classes_admitted"])
    for row in a["berths"]:
        for v, h in row["hulls"].items():
            h["admitted_by_gate"] = v in admitted
            h["berthable_here"] = bool(h["floats"] and v in admitted)
    a["plan_pieces"] = pl_a.counts()
    a["plan_total"] = len(pl_a)
    a["plan_extent"] = pl_a.extent()
    b["plan_pieces"] = pl_b.counts()
    b["plan_total"] = len(pl_b)
    b["plan_extent"] = pl_b.extent()
    # the KEEP-OUT the road router is fed BEFORE it routes.  Not one big disc:
    # the union of the actual pads.  A 100 m `pad_radius_m` read as a building
    # footprint once made a road refuse to write for 294 m.
    a["road_keepout"] = [dict(x=t["cx"], z=t["cz"],
                              r=round(max(t["rw"], t["rd"]) / 2.0 + 4.0, 2))
                         for t in taken_a]
    b["road_keepout"] = [dict(x=t["cx"], z=t["cz"],
                              r=round(max(t["rw"], t["rd"]) / 2.0 + 4.0, 2))
                         for t in taken_b]
    out = dict(A=a, B=b)
    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=1, default=float))
        print(f"design written to {args.out}")
    if args.plans:
        d = Path(args.plans)
        pl_a.write(d / f"{SITE_A}.plan")
        pl_b.write(d / f"{SITE_B}.plan")
        print(f"plans written to {d}")
    if args.summary:
        print(summarise(out))
    else:
        print(json.dumps(out, indent=1, default=float))
    return 0


def summarise(out: dict) -> str:
    L = []
    for key in ("A", "B"):
        d = out[key]
        L.append(f"=== {key}  {d['site']}")
        if d.get("sea_wall"):
            w = d["sea_wall"]
            L.append(f"  SEA WALL z={w['z']} x{w['x']} span {w['span_m']} m, "
                     f"{w['modules']} modules @ {w['module_pitch_m']} m")
            for sg in w["segments"]:
                L.append(f"    {sg['prefab']:30s} x={sg['x']:8.2f} foot={sg['y']:7.3f} "
                         f"depth={sg['depth_m']:+6.3f} top={sg['top_y']:7.3f} "
                         f"freeboard={sg['freeboard_m']:+6.3f} on {sg['seated_on']}")
            g = w["gate"]
            L.append(f"    GATE x{g['x']} CLEAR {g['clear_width_m']} m; water under it "
                     f"min {g['depth_min_m']} mean {g['depth_mean_m']} max {g['depth_max_m']} m")
            L.append(f"      admits: {', '.join(g['hull_classes_admitted'])}")
            for r in g["hull_classes_refused"]:
                L.append(f"      REFUSED {r['vessel']}: {r['why']}")
            bar = g["barrier"]
            L.append(f"      barrier {bar['panels']} x {bar['prefab']} "
                     f"({bar['columns']} columns x {bar['rows']} rows, pitch "
                     f"{bar['column_pitch_m']} m, overlap {bar['overlap_m']} m) "
                     f"covering y {bar['covers_y']} = {bar['reaches_below_plane_m']} m "
                     f"below and {bar['reaches_above_plane_m']} m above the plane; "
                     f"is_a_door={bar['is_a_door']}")
        else:
            L.append(f"  NO CURTAIN WALL - {d['curtain_refused']['why']}")
        for k in ("quay_west", "quay_east"):
            if d.get(k):
                q = d[k]
                L.append(f"  {k}: {q['tiles']} deck tiles over {q['rows']} rows, "
                         f"{q['pieces']} pieces, walking surface y={q['deck_walking_y']}")
        for k in ("boathouse_north", "boathouse_south", "boathouse"):
            if not d.get(k):
                continue
            bh = d[k]
            L.append(f"  {k} ({bh['name']}): {bh['bays']} bays, {bh['pieces']} pieces")
            for bk in ("bay1", "bay2"):
                y = bh[bk]["bay"]
                L.append(f"    {bk}: clear {y['clear_width_m']} m x {y['length_m']} m, "
                         f"depth min {y['depth_min_m']} med {y['depth_med_m']} "
                         f"max {y['depth_max_m']} m over {y['samples']} samples")
                L.append(f"      fits: {', '.join(y['hull_classes_that_fit']) or 'NOTHING'}"
                         f"  largest={y['largest']}")
                e = bh[bk]["enclosed"]
                L.append(f"      enclosed: long walls {e['long_walls']}, back wall "
                         f"{e['back_wall']}, gable filled {e['gable_filled']}, roof "
                         f"{e['roof']}, seaward {e['seaward_gable']}; "
                         f"eaves {bh[bk]['eaves_y']} ridge {bh[bk]['ridge_y']} "
                         f"span {bh[bk]['span_m']} m")
        L.append(f"  town bodies seated: {d['bodies']}")
        for q in d["quarters"]:
            names = {}
            for st in q["seats"]:
                names[st["prefab"]] = names.get(st["prefab"], 0) + 1
            L.append(f"    {q['id']:10s} {len(q['seats']):3d} bodies {names}  "
                     f"refused: {q['refusals']}")
        if d.get("lighthouse"):
            lh = d["lighthouse"]
            L.append(f"  LIGHTHOUSE {lh['prefab']} at ({lh['x']},{lh['z']}) ground "
                     f"{lh['ground_y']} freeboard {lh['freeboard_m']} m; beacon seats "
                     f"on the PLATFORM y={lh['platform_y']} (mesh top {lh['mesh_top_y']} "
                     f"is NOT the surface)")
        L.append("  BERTHS (depth under the hull's own footprint vs that hull's draught):")
        for bb in d["berths"]:
            fits = [f"{v}({h['margin_m']:+.2f})" for v, h in bb["hulls"].items()
                    if h["floats"]]
            no = [f"{v}({h['margin_m']:+.2f})" for v, h in bb["hulls"].items()
                  if not h["floats"]]
            L.append(f"    ({bb['x']},{bb['z']}) {bb['note']}: point depth "
                     f"{bb['point_depth_m']} m")
            L.append(f"       FLOATS: {', '.join(fits) or 'none'}")
            L.append(f"       aground: {', '.join(no) or 'none'}")
        L.append(f"  plan: {d['plan_total']} pieces {d['plan_pieces']}")
        L.append(f"  road keep-out discs: {len(d['road_keepout'])}")
    return "\n".join(L)



# ---------------------------------------------------------------------------
# applying it: probe, then stage by stage, each stage with its own postcondition
# ---------------------------------------------------------------------------

# 48 pieces per record, not 64.  MEASURED reason: waterfront's 64-piece budget
# sits at ~9,920 B against a 65,536 B pipe, and a 1,255-piece record with no
# gate anywhere inside it wedged the log sink and left a structure at 272 of
# 1,255 pieces which then had to be RETIRED AND REBUILT.  48 keeps a record's
# echo near a third of the pipe and leaves room for the longer `fb_` prefab
# names, which are up to 12 characters longer than `wood_floor`.
BATCH_PIECES = 48
BATCH_BYTES = 1800


def chunk_rows(rows: list[tuple], *, pieces: int = BATCH_PIECES,
               nbytes: int = BATCH_BYTES) -> list[list[tuple]]:
    out: list[list[tuple]] = []
    cur: list[tuple] = []
    size = 0
    for r in rows:
        line = (f"spawn_object {r[0]} pos={A.fmt(r[3])},{A.fmt(r[1])},"
                f"{A.fmt(r[2])} rot={A.fmt(r[4])},0,0 from=0,0,0")
        if cur and (len(cur) >= pieces or size + len(line) + 1 > nbytes):
            out.append(cur)
            cur, size = [], 0
        cur.append(r)
        size += len(line) + 1
    if cur:
        out.append(cur)
    return out


def sentinel(chunk: list[tuple], all_rows: list[tuple],
             radius: float = 1.4) -> dict:
    """A postcondition for ONE record that cannot pass on a neighbour's work.

    THE EXPECTED COUNT IS COMPUTED WITH THE SAME GEOMETRY THE CHECKER USES,
    and getting that wrong cost a failed postcondition on the LIVE gate
    record (seq 2690) on a world that was CORRECT.

    MEASURED: `replay.Server.count` sends
    `objects_count id=<prefab> pos=<x>,<z> max=<r>` and that command has NO
    y term at all -- it is a Y-BLIND XZ CYLINDER. The earlier version of this
    function counted neighbours inside a THREE-AXIS CUBE (`abs(o[2]-r[2]) <=
    radius`), on the strength of a docstring about `findObjects -near`, which
    is a different command that this check does not use.

    What that cost, exactly: the ship gate is 3 columns x 2 courses of
    `fb_fortress_portcullis` at x 1062.518 / 1066.5 / 1070.482, courses at
    y 23.774 and y 29.481. Two pieces share every xz, 5.707 m apart
    vertically. The y-aware cube saw 1 at (1062.52, 752.0); the y-blind
    cylinder the checker actually sends saw 2. So the record was recorded,
    SENT CORRECTLY, and then failed its own postcondition -- 6 of 6
    portcullis were standing exactly as planned, confirmed by a scoped count
    of 6 in a 20 m disc on the wall line.

    This is the session's recurring defect in its purest form: one question,
    two instruments, never reconciled. The fix is not a better tolerance --
    it is to derive the expectation from the measuring instrument's own
    geometry, so there is only one authority for "how many of these are
    inside this radius".

    The count is still taken over the WHOLE plan rather than this chunk: a
    pile stack puts four `wood_pole_log_4` at one xz, and a naive `count=1`
    would fail on a correctly built stack. The sentinel is the chunk's piece
    whose cylinder holds the FEWEST pieces, so the check stays as sharp as
    the geometry allows and the expected count is derived, never assumed.
    """
    # THE THIRD ROTATION OF THIS SAME DEFECT, and it cost seq 2988.
    #
    # The docstring above says the expectation is computed with "the same
    # geometry the checker uses".  It was not.  `objects_count pos=x,z max=r`
    # is a DISC of radius r; this test was an axis-aligned SQUARE of
    # half-side r, which additionally admits every neighbour out to r*sqrt2.
    #
    # MEASURED on shed-vestvik, batch 6 of 13: the sentinel landed on the
    # `woodwall` at (-1040, 1649).  Its plan neighbour at (-1039, 1648) is
    # 1.000 m away on each axis - inside the square - and 1.41421 m away in
    # the plane, OUTSIDE the 1.4 m disc.  The expectation said 2; the live
    # `objects_count id=woodwall pos=-1040,1649 max=1.4` said 1, and a
    # `findObjects -prefab woodwall` cube at that column listed exactly the
    # four pieces the plan puts there.  The wall was BUILT CORRECTLY and
    # failed its own postcondition, for the third time on this world with
    # the same shape: one question, two instruments, never reconciled.
    #
    # The disc is not asserted from a manual either - it is what the live
    # instrument answered when the square and the disc disagreed by exactly
    # one object.  Euclidean here, and the two geometries now agree by
    # construction rather than by luck of the lattice.
    best = None
    for r in chunk:
        n = sum(1 for o in all_rows
                if o[0] == r[0]
                and math.hypot(o[1] - r[1], o[3] - r[3]) <= radius)
        if best is None or n < best[0]:
            best = (n, r)
    n, r = best
    return dict(prefab=r[0], pos=[round(r[1], 2), round(r[3], 2)],
                max=radius, count=n, tolerance=0,
                why=("expected count derived with the SAME y-blind XZ "
                     "cylinder `objects_count pos=x,z max=r` measures with - "
                     "a DISC of radius r, tested with hypot and not with a "
                     "per-axis box - over everything standing when this "
                     "record lands, so neither a correctly built vertical "
                     "stack nor a diagonal neighbour can fail it"))


def stage_ops(site: str, stage: str, rows: list[tuple], all_rows: list[tuple],
              role: str, *, placed_before: list[tuple] | None = None,
              from_batch: int = 1) -> list[dict]:
    """One `spawn_plan` record per batch, each carrying its own proof.

    `all_rows` IS NO LONGER THE WHOLE PLAN, and that change cost a second
    failed postcondition on a CORRECT world (seq 2698, boathouse batch 6).

    A record's postcondition is measured the moment that record lands. The
    expected count therefore has to be the count the world will hold AT THAT
    MOMENT -- everything placed before it, plus this batch -- and NOT the
    count the finished structure will hold. MEASURED: batch 6 puts a
    `woodwall` at (1066.0, 699.0, y 31.6) and batch 8 puts its partner at the
    same xz at y 33.6. Counted over the whole plan the y-blind cylinder at
    that xz predicts 2; two batches before its partner is sent, the world
    correctly holds 1. `want 2, got 1` on a perfectly built wall.

    So this is the same defect as the gate's, rotated one axis: there the
    expectation and the measurement disagreed about WHERE (a three-axis cube
    against a y-blind cylinder), here about WHEN (the final state against the
    state at the boundary). Both are one question measured by two instruments
    nobody had reconciled, and the fix in both cases is to derive the
    expectation from the measurement rather than from the design.

    `placed_before` is what the site already holds when this stage starts --
    the rows of every earlier stage in `U_STAGE_ORDER`. `from_batch` resumes
    a stage whose earlier batches are already standing, so a stage
    interrupted at batch 6 continues at 7 instead of re-sending six records
    and doubling everything in them.
    """
    ops = []
    groups = chunk_rows(rows)
    placed = list(placed_before or [])
    for i, ch in enumerate(groups):
        placed.extend(ch)          # the world's state once THIS batch lands
        if i + 1 < from_batch:
            continue               # already standing; not re-sent
        lines = [f"spawn_object {r[0]} pos={A.fmt(r[3])},{A.fmt(r[1])},"
                 f"{A.fmt(r[2])} rot={A.fmt(r[4])},0,0 from=0,0,0" for r in ch]
        counts: dict[str, int] = {}
        for r in ch:
            counts[r[0]] = counts.get(r[0], 0) + 1
        ops.append(dict(
            op="spawn_plan",
            params=dict(
                plan_sha256="",           # filled by `emit` against the blob
                anchor=dict(x=round(ch[0][1], 3), y=round(ch[0][2], 3),
                            z=round(ch[0][3], 3), yaw=round(ch[0][4], 1)),
                command_count=len(lines), prefabs=counts,
                align="world-absolute",
                datum=(f"{stage}: every y is the piece's own seat computed from "
                       f"the applied surface at its own xz, with that prefab's "
                       f"own measured pivot offset"),
                batch_bytes=BATCH_BYTES, role=role, site_id=site,
                flatten="FORBIDDEN",
                flatten_reason=OVER_WATER_REASON if stage in
                ("seawall", "gate", "quay", "boathouse") else ON_LAND_REASON),
            wire=[";".join(lines)],
            requires=dict(mods=["WorldEditCommands", "ServerDevcommands"],
                          prefabs=sorted(counts), blobs=[]),
            expect=dict(prefab_count=[sentinel(ch, list(placed))]),
            meta=dict(stage=stage, batch=i + 1, batches=len(groups),
                      pieces=len(ch), placed_before_this_batch=len(placed) - len(ch),
                      why=("48 pieces per record: a 1,255-piece record with no "
                           "sink gate inside it wedged the console log sink and "
                           "left a structure at 272 of 1,255 pieces, which had "
                           "to be retired and rebuilt rather than resumed"))))
    return ops


OVER_WATER_REASON = (
    "an over-water structure standing on the seabed. A terrain_write here "
    "would remove the water it stands in and drain the shoreline, which is the "
    "one damage class this project cannot undo. The fb_ kit has no WearNTear, "
    "so nothing needs a footing.")
ON_LAND_REASON = (
    "a body seated on the applied surface at its own xz, on ground already "
    "inside the +/-8 m clamp. No cut is needed, so none is written.")


PROBE_SET = [
    "fb_fortress_wall", "fb_fortress_wall_tower", "fb_fortress_wall_stair_left",
    "fb_fortress_wall_stair_right", "fb_fortress_portcullis",
    "fb_house_01", "fb_house_02", "fb_house_03", "fb_house_04",
    "fb_shelter_01", "fb_viking_house_01", "fb_viking_house_02",
    "fb_viking_house_03", "fb_viking_house_04", "fb_viking_house_05",
    "fb_viking_house_06", "fb_viking_house_07", "fb_viking_house_08",
]
# Somewhere with nothing in it and nothing planned in it, so a probe that
# leaves a piece behind is obvious and removable by position.
PROBE_AT = (1300.0, 900.0)


def cmd_probe(args) -> int:
    """Prove, LIVE, that every prefab this build places is in ZNetScene.

    `spawn_object` on a prefab that is not registered is a silent no-op with a
    cheerful echo -- measured: `mm_large_wall` answered "Object reference not
    set to an instance of an object" and placed nothing, while `objects_count`
    answered "Error: Entity id mm_large_wall not recognized".  So the only
    oracle is a postcondition, and a FAILED one here is the measurement.

    Re-run after every restart.  A registration measured under Jotunn 2.30.0 is
    not evidence about 2.30.1, and tonight's deploy moved it.
    """
    from ledger.live import LiveBuilder  # noqa: PLC0415
    s = surface()
    x0, z0 = PROBE_AT
    y = s.at(x0, z0)[0] if args.seat_on_ground else W.WATER_LEVEL + 5.0
    rows = []
    for i, prefab in enumerate(PROBE_SET):
        rows.append((prefab, x0 + 30.0 * i, y, z0, 0.0, "probe"))
    if not args.apply:
        for r in rows:
            print(f"  would probe {r[0]:30s} at ({r[1]},{r[3]}) y={r[2]:.3f}")
        return 0
    with LiveBuilder(actor=ACTOR) as b:
        b.note("harbour-city prefab existence probe: every fb_ prefab this "
               "build places, spawned at a throwaway site and counted. A "
               "prefab absent from ZNetScene makes spawn_object a silent "
               "no-op, so the postcondition is the only oracle; 53 fb_ strings "
               "exist in the installed DLL and only 29 are registered pieces, "
               "so the DLL list cannot be trusted as a placement list. "
               "Re-taken after tonight's Jotunn 2.30.0 -> 2.30.1 deploy "
               "because a registration measured before a library change is "
               "not evidence about after it.",
               role="site_prop", site_id="probe-harbour-city")
        ok, bad = [], []
        for prefab, x, yy, z, yaw, _w in rows:
            op = WF.spawn_op("probe-harbour-city", "site_prop", prefab,
                             x, yy, z, yaw,
                             reason="a probe, not a structure; nothing is "
                                    "load bearing and no ground is written",
                             guard=3.0,
                             meta=dict(probe=True,
                                       question="is this prefab in ZNetScene"))
            try:
                WF.emit(b, [op], label=f"probe {prefab}")
                ok.append(prefab)
            except Exception as exc:                      # noqa: BLE001
                bad.append(dict(prefab=prefab, error=str(exc)[:300]))
                print(f"    ABSENT {prefab}: {str(exc)[:160]}")
        b.observe("znetscene_existence::harbour_city_set",
                  "MEASURED LIVE: spawn_object followed by the record's own "
                  "prefab_count postcondition, one prefab at a time. A "
                  "negative result is the measurement, not a failure.",
                  "tools/jumpstart/settlements/harbour_city.py::cmd_probe",
                  dict(present=ok, absent=bad, probe_at=list(PROBE_AT)))
        print(json.dumps(b.close(), indent=1))
    print(f"present {len(ok)}/{len(PROBE_SET)}; absent {[d['prefab'] for d in bad]}")
    return 0 if not bad else 1


# ---------------------------------------------------------------------------
# marker reach, measured, so a worst case can be replaced by a fact
# ---------------------------------------------------------------------------

def marker_reach(srv, mx: float, my: float, mz: float, *,
                 box_m: float = 34.0) -> dict:
    """How far THIS generated location's own content actually reaches.

    The 32 m world-wide worst case is the right stand-off for a marker nobody
    can identify -- and it is expensive: applying it blanket cut the city from
    39 bodies to 15 and the village from 15 to 6.  A worst case is a stand-in
    for a measurement, so where it costs real ground the measurement is worth
    taking: census the marker's own neighbourhood and take the distance to the
    farthest object that belongs to it.

    Uses `waterfront.audit`, which re-asks any reply over 18 rows as eight
    TILING sub-boxes, so nothing here approaches the client's 4,096-byte buffer
    and nothing wedges the console log sink.
    """
    cen = WF.audit(srv, mx - box_m, mx + box_m, mz - box_m, mz + box_m,
                   y_centre=my, y_levels=(0.0, -16.0, 16.0))
    far = 0.0
    farthest = None
    kept = 0
    for o in cen["objects"]:
        if o["prefab"].startswith(("_", "sfx_", "vfx_")):
            continue
        d = math.hypot(o["x"] - mx, o["z"] - mz)
        kept += 1
        if d > far:
            far, farthest = d, o
    return dict(marker=[mx, my, mz], box_m=box_m, objects=kept,
                reach_m=round(far, 2), farthest=farthest,
                per_prefab=cen["per_prefab"], socket_calls=cen["socket_calls"],
                method=("MEASURED: an unscoped bounded census of the marker's "
                        "own neighbourhood; reach is the distance to the "
                        "farthest non-engine object in it. Engine and effect "
                        "prefabs (_*, sfx_*, vfx_*) are excluded because they "
                        "are runtime children, not location content."))


def cmd_reach(args) -> int:
    """Measure the real reach of the markers that actually block a plot.

    READ-ONLY.  Only the markers whose 32 m disc overlaps a quarter are
    measured -- there is no point paying for a census of a marker that blocks
    nothing -- and the result is written beside the marker file so the design
    can use a fact instead of a worst case.
    """
    import replay as R  # noqa: PLC0415
    keep = location_keepout()
    quarters = QUARTERS_A + QUARTERS_B
    blocking = []
    for k in keep:
        for q in quarters:
            if (q["x"][0] - LOC_STANDOFF_M <= k["x"] <= q["x"][1] + LOC_STANDOFF_M
                    and q["z"][0] - LOC_STANDOFF_M <= k["z"] <= q["z"][1] + LOC_STANDOFF_M):
                blocking.append(k)
                break
    print(f"{len(keep)} markers, {len(blocking)} of them within "
          f"{LOC_STANDOFF_M:.0f} m of a quarter")
    out = []
    with R.Server() as srv:
        for k in blocking:
            rec = marker_reach(srv, k["x"], 30.0, k["z"])
            rec["identified"] = k["identified"]
            rec["nearest_dump_entry"] = k["nearest"]
            out.append(rec)
            print(f"  ({k['x']:8.2f},{k['z']:8.2f}) {k['nearest']:22s} "
                  f"objects {rec['objects']:4d} REACH {rec['reach_m']:6.2f} m "
                  f"({'identified' if k['identified'] else 'unidentified'})")
    Path(args.out).write_text(json.dumps(out, indent=1, default=float))
    print(f"written to {args.out}")
    if out:
        rs = [r["reach_m"] for r in out]
        print(f"reach: min {min(rs):.2f} med {sorted(rs)[len(rs)//2]:.2f} "
              f"max {max(rs):.2f} m against the {LOC_STANDOFF_M:.0f} m worst case")
    return 0


# ---------------------------------------------------------------------------
# THE CENSUS GATE: what makes this build replayable instead of run-once
# ---------------------------------------------------------------------------
#
# WHY THIS EXISTS.  Without it a second run of this tool against a site that
# already carries its pieces places every one of them again.  That is not a
# hypothetical: tonight a 1,255-piece record was interrupted at 272 pieces and
# could NOT be resumed, because re-sending would have doubled everything
# standing, so the whole structure had to be retired and rebuilt.  Separately,
# a resume whose census had a silent y blind spot re-sent 83 pieces and
# DOUBLED 64 of them.  Both are the same missing thing: a read-before-write.
#
# So `place_missing` is the only placement path in this module.  It censuses
# the site first, places only what is absent, and reports placed/skipped per
# prefab.  Three consequences, and all three are the point:
#   * a partial run is RESUMABLE -- re-run it and it finishes the remainder;
#   * a completed run is a NO-OP -- re-run it and it places nothing;
#   * a replay against a regenerated world of the same seed starts from an
#     empty census and places everything, which is the same code path.
#
# The census is `waterfront.audit`: UNSCOPED, so it cannot be blind to a
# prefab nobody wrote down, on disjoint 16 m cells at tiling y levels, with any
# reply over 18 rows re-asked as eight sub-boxes so nothing approaches the
# client's 4,096-byte buffer.
MATCH_TOL_M = 0.60


def plan_box(rows: list[tuple], pad: float = 6.0) -> tuple:
    xs = [r[1] for r in rows]
    ys = [r[2] for r in rows]
    zs = [r[3] for r in rows]
    return (min(xs) - pad, max(xs) + pad, min(zs) - pad, max(zs) + pad,
            (min(ys) + max(ys)) / 2.0, min(ys) - pad, max(ys) + pad)


def y_levels_for(y_lo: float, y_hi: float, y_c: float) -> tuple[float, ...]:
    """A CONTIGUOUS census stack covering the plan's own y range.

    The module default (0,-16,+16,-32,+32) leaves an 8 m hole between +16 and
    +32.  A boathouse ridge sits 8.5 m above its eaves and a tower parapet
    10.2 m above its foot, so that hole is exactly where this build's tallest
    pieces live -- and a census that cannot see them reports them missing,
    which is how 64 pieces got placed twice.
    """
    lo = math.floor((y_lo - y_c) / 16.0) * 16.0
    hi = math.ceil((y_hi - y_c) / 16.0) * 16.0
    return tuple(float(v) for v in np.arange(lo, hi + 16.0, 16.0))


def place_missing(b, srv, site: str, stage: str, rows: list[tuple], role: str,
                  *, dry: bool = False) -> dict:
    """Census, then place only the absent pieces.  Returns the full report."""
    x0, x1, z0, z1, yc, ylo, yhi = plan_box(rows)
    levels = y_levels_for(ylo, yhi, yc)
    cen = WF.audit(srv, x0, x1, z0, z1, y_centre=yc, y_levels=levels)
    live: dict[str, list[tuple]] = {}
    for o in cen["objects"]:
        live.setdefault(o["prefab"], []).append((o["x"], o["y"], o["z"],
                                                 o["at_position"]))
    todo, skipped = [], []
    used: set[tuple] = set()
    for r in rows:
        prefab, x, y, z = r[0], r[1], r[2], r[3]
        hit = None
        for k, (lx, ly, lz, mult) in enumerate(live.get(prefab, [])):
            if (prefab, k) in used:
                continue
            if (abs(lx - x) <= MATCH_TOL_M and abs(ly - y) <= MATCH_TOL_M
                    and abs(lz - z) <= MATCH_TOL_M):
                hit = k
                break
        if hit is None:
            todo.append(r)
        else:
            used.add((prefab, hit))
            skipped.append(r)
    per_placed: dict[str, int] = {}
    per_skipped: dict[str, int] = {}
    for r in todo:
        per_placed[r[0]] = per_placed.get(r[0], 0) + 1
    for r in skipped:
        per_skipped[r[0]] = per_skipped.get(r[0], 0) + 1
    rep = dict(site=site, stage=stage, planned=len(rows),
               already_standing=len(skipped), to_place=len(todo),
               per_prefab_skipped=dict(sorted(per_skipped.items())),
               per_prefab_to_place=dict(sorted(per_placed.items())),
               census=dict(box=[x0, x1, z0, z1], y_levels=list(levels),
                           y_window=cen["y_window"], cells=cen["cells"],
                           socket_calls=cen["socket_calls"],
                           per_prefab=cen["per_prefab"], total=cen["total"]),
               match_tol_m=MATCH_TOL_M,
               why=("read-before-write: only pieces with no live match within "
                    "the tolerance in ALL THREE axes are sent, so a partial "
                    "run resumes and a finished run is a no-op"))
    print(f"  [{stage}] planned {len(rows)}, already standing "
          f"{len(skipped)}, to place {len(todo)}")
    if dry or not todo:
        return rep
    ops = stage_ops(site, stage, todo, rows, role)
    rep["records"] = len(ops)
    for i, op in enumerate(ops):
        text = "\n".join(op["wire"][0].split(";")) + "\n"
        op["params"]["plan_sha256"] = b.blob(
            text.encode(), note=f"{site}/{stage} batch {i+1}/{len(ops)}")
        b.emit(op["op"], params=op["params"], wire=op["wire"],
               requires=op["requires"], expect=op["expect"], meta=op["meta"])
        print(f"    record {i+1}/{len(ops)}: {op['meta']['pieces']} pieces ok",
              flush=True)
        g = WF.sink_gate(f"{site}/{stage} batch {i+1}")
        if g.get("verdict_after") not in (None, "flowing", "ok"):
            rep["sink"] = g
    return rep


# ---------------------------------------------------------------------------
# PROVENANCE: which numbers survive a world regeneration and which do not
# ---------------------------------------------------------------------------
# building the U: terrain first, portals last, a verified save at each stage
# ---------------------------------------------------------------------------

U_STAGE_ORDER = ("dredge", "arms", "links", "seawall", "gate",
                 "boathouse", "lighting", "signs")

# Which `why` prefixes belong to which stage.  Derived from the `why` each
# builder already stamps on its rows rather than re-deriving the geometry: a
# second way of deciding which piece is part of which stage is a second thing
# that can disagree with the first.
U_STAGE_MATCH = {
    "arms": ("U/arm",),
    "links": ("U/link",),
    "seawall": ("ulfsgard/seawall", "ulfsgard/gate-beacon",
                "ulfsgard/lighthouse"),
    "gate": ("ulfsgard/gate-portcullis",),
    "boathouse": (U_BOATHOUSE["name"],),
    "lighting": ("U/dock lighting",),
}
U_STAGE_ROLE = {
    "arms": "dock_place", "links": "dock_place", "seawall": "castle",
    "gate": "castle", "boathouse": "boathouse_place",
    "lighting": "site_prop", "signs": "waypoint_sign",
}


def u_stage_rows(d: dict, stage: str) -> list[tuple]:
    """Every row of the plan whose `why` belongs to this stage.

    The partition is CHECKED to be total: a row that matches no stage would
    silently never be built, which is how a structure ends up at 272 of 1,255
    pieces without anything saying so.
    """
    pl: Plan = d["_plan"]
    pref = U_STAGE_MATCH.get(stage, ())
    return [r for r in pl.sorted_rows() if any(r[5].startswith(q) for q in pref)]


def u_partition(d: dict) -> dict:
    pl: Plan = d["_plan"]
    seen = 0
    per = {}
    for st in U_STAGE_ORDER:
        if st in ("dredge", "signs"):
            continue
        rows = u_stage_rows(d, st)
        per[st] = len(rows)
        seen += len(rows)
    orphans = [r for r in pl.sorted_rows()
               if not any(r[5].startswith(q)
                          for qs in U_STAGE_MATCH.values() for q in qs)]
    if orphans:
        raise SystemExit(
            f"REFUSING: {len(orphans)} planned pieces belong to no build "
            f"stage, e.g. {orphans[:3]}. An unassigned piece is a piece that "
            f"never gets built and never gets missed.")
    return dict(per_stage=per, assigned=seen, planned=len(pl),
                total_ok=seen == len(pl))


def dredge_terrain_op(b, d: dict, probes: dict) -> dict:
    """The ONE `terrain_write` for the whole cut: one entry per zone.

    The data entries go through `flatten.write_entries`, which is the same
    encoder every other terrain write on this world used, so the bytes the
    ledger stores and the bytes the world gets are the same bytes -- and
    `replay._materialise_data_entries` regenerates the file from the logged
    blob rather than from whatever is left in the watched directory.
    """
    import flatten as FL  # noqa: PLC0415  terraform/
    plan = d["_dredge"]
    comps = plan["comps"]
    st = plan["stats"]
    # `op_y` is the height stamped into each zone's `m_lastOpPoint`, which
    # selects the grass a CLIENT throws away.  For a dredge the relevant
    # height is the new BED, not a pad datum -- and the whole cut is under
    # water where there is no grass, so it is the design bed and the radius is
    # the zone's own half-extent (see tcdata's header for why a zero here left
    # vegetation floating over a finished pad).
    op_y = {z: plan["design_bed"] for z in comps}
    _path, entries = FL.write_entries("ulfsgard_dredge", comps, op_y)
    out, blobs = [], []
    for e in entries:
        zx, zz = e["zone"]
        blob = comps[(zx, zz)].blob(op_y[(zx, zz)])
        digest = b.blob(blob, note=f"TCData {e['entry']} zone {zx},{zz} "
                                   f"(harbour A dredge)")
        blobs.append(digest)
        sc = e["selfcheck"]
        out.append({
            "zone": [int(zx), int(zz)],
            "centre": [float(c) for c in e["centre"]],
            "data_entry": e["entry"],
            "blob_sha256": digest,
            "generated_heights_sha256": _generated_sha(d["_ds"], zx, zz),
            "zone_generated_before": probes.get((zx, zz), {}).get("count", 0) >= 1,
            "zone_generated_probe": probes.get((zx, zz), {}).get("cmd", ""),
            "op_record": {
                "operations": int(sc["operations"]),
                "last_op_point": [round(float(v), 3) for v in sc["last_op_point"]],
                "last_op_radius": round(float(sc["last_op_radius"]), 3),
            },
            "samples": e["counts"]["height_modified"],
        })
    wire = [f"deleteObjects -zone {e['zone'][0]} {e['zone'][1]} "
            f"-prefab _TerrainCompiler -force" for e in entries]
    wire += [f"spawn_object _TerrainCompiler "
             f"from={e['centre'][0]:g},{e['centre'][1]:g},0 data={e['entry']}"
             for e in entries]
    dlc = d["dredge_location_check"]
    lc = {
        "dump": str(LOC_MARKERS),
        "method": ("MEASURED: every LIVE LocationProxy within reach of this "
                   "site, read over RCON with bounded `findObjects -prefab "
                   "LocationProxy -near x 30 z r`, and every fixture of this "
                   "design tested against the nearest one. THE DREDGE'S OWN "
                   "verdict is measured over EVERY CUT SAMPLE against every "
                   "marker, because the cut is an area and a check at its "
                   "centre is a check of its centre. Fixture checks that come "
                   "in inside the stand-off are listed under "
                   "`fixture_narrowings` with their distances; they write no "
                   "terrain, and a structure cannot drain anything."),
        "nearest": {"name": str(dlc.get("nearest_dump_entry")
                                 or "unidentified LocationProxy"),
                    "dist_m": dlc["nearest_m"],
                    "clearance_m": round(dlc["nearest_m"] - LOC_STANDOFF_M, 2)
                    if dlc["nearest_m"] is not None else None},
        "standoff_m": LOC_STANDOFF_M,
        "verdict": "clear" if dlc["clear"] else "BLOCKED",
        "cut_samples_tested": dlc["cut_samples"],
        "markers_tested": dlc.get("markers_tested"),
        "offenders": dlc["offenders"],
        "fixture_checks": d["location_checks"],
        "fixture_narrowings": d["location_narrowings"],
    }
    params = {
        "name": "ulfsgard-dredge",
        "paint": "none",
        # The DOMINANT target. Two columns of the gate throat -- the jamb ramps
        # at x 1061 and 1072 -- target 26.5 instead, and the reason is in
        # `dredge_regions`: cutting them to 24.0 would put the heightmap mesh
        # under the flanking wall module's jamb face 0.397 m BELOW that
        # module's own foot. Every other sample in the cut targets this.
        "target_y": float(plan["design_bed"]),
        "datum": (f"a DREDGE, not a pad: every delta is "
                  f"min(applied, {plan['design_bed']:g}) - generated at that "
                  f"sample, against patchscan generated heights at 1 m (seed "
                  f"Pirate68), so the write can only ever DEEPEN. A positive "
                  f"delta is the operation MEASURED to remove water and this "
                  f"write is structurally incapable of one."),
        "entries": [{k: v for k, v in e.items() if k != "samples"} for e in out],
        "location_check": lc,
        "max_cut_m": st["max_cut_m"], "max_fill_m": 0.0,
        "over_clamp": st["clipped"],
        "role": "harbour_place", "site_id": SITE_A,
    }
    expect = {"prefab_count": [{"prefab": "_TerrainCompiler",
                                "pos": e["centre"], "max": 31, "count": 1,
                                "tolerance": 0} for e in out]}
    return b.emit("terrain_write", params=params, wire=wire,
                  requires={"mods": ["WorldEditCommands", "ServerDevcommands"],
                            "prefabs": ["_TerrainCompiler"], "blobs": blobs},
                  expect=expect,
                  meta={"settlement": SITE_A, "stage": "dredge",
                        "samples_written": sum(e["samples"] for e in out),
                        "samples_in_footprint": st["samples"],
                        "already_deep_enough": st["already_deep_enough"],
                        "worst_clamp_margin_m": st["worst_clamp_margin_m"],
                        "depth_after_m": [st["depth_after_min"],
                                          st["depth_after_median"],
                                          st["depth_after_max"]],
                        "why": ("the harbour is DREDGED rather than berth-hunted: "
                                "the shallowest sample under a CargoShip's own "
                                "hull passing the gate MEASURED 2.890 m against "
                                "3.000 m required, so the gate refused the "
                                "deepest hull the basin could otherwise take")})


def _generated_sha(ds, zx: int, zz: int) -> str:
    """Digest of the GENERATED heights this zone's deltas were computed against.

    It is what detects a worldgen change: a delta is only correct relative to
    these numbers, so a replay against different generated ground must stop
    rather than write a basin cut to the wrong depth.
    """
    import hashlib  # noqa: PLC0415
    import struct   # noqa: PLC0415
    base = W.load(CITY_FIELD)["cityA"]
    cx, cz = TC.zone_centre(zx, zz)
    vals = []
    for iz in range(TC.PITCH):
        for ix in range(TC.PITCH):
            wx, wz = TC.sample_world(cx, cz, ix, iz)
            i = int(round(wz - base.z0))
            j = int(round(wx - base.x0))
            vals.append(float(base.heights[i, j]) if
                        (0 <= i < base.n and 0 <= j < base.n) else float("nan"))
    return hashlib.sha256(struct.pack(f"<{len(vals)}f", *vals)).hexdigest()


def cmd_udesign(args) -> int:
    """Measure and lay out the dredged U. READ-ONLY: nothing is sent."""
    s = surface()
    d = design_u(s)
    part = u_partition(d)
    d["stage_partition"] = part
    if args.out:
        pub = {k: v for k, v in d.items() if not k.startswith("_")}
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(pub, indent=1, default=str))
    if args.plans:
        d["_plan"].write(Path(args.plans) / "ulfsgard_u.plan")
        d["_plan_data"].write(Path(args.plans) / "ulfsgard_u_data.plan")
    print(u_summary(d))
    return 0


def u_summary(d: dict) -> str:
    st = d["dredge"]["stats"]
    L = [f"{d['site']}: THE DREDGED U", ""]
    L.append(f"  prior terrain in the window: {d['prior_terrain']['verdict']} "
             f"({d['prior_terrain']['samples_with_delta']} of "
             f"{d['prior_terrain']['samples_probed']} samples carry a delta)")
    L.append("")
    L.append(f"  DREDGE  design bed y={d['dredge']['design_bed_y']:g} "
             f"= {d['dredge']['design_depth_m']:g} m of water")
    L.append(f"    {st['samples']} samples in the footprint, {st['cut']} cut, "
             f"{st['already_deep_enough']} already deeper than the design bed")
    L.append(f"    delta {st['min_delta']:+.4f} .. {st['max_delta']:+.4f} m; "
             f"ANY FILL: {st['any_fill']}; clipped by the "
             f"{d['dredge']['cut_max_m']:g} m working limit: {st['clipped']}")
    L.append(f"    worst cut {st['max_cut_m']:.4f} m, i.e. "
             f"{st['worst_clamp_margin_m']:.4f} m of margin against the "
             f"{d['dredge']['clamp_m']:g} m apply clamp")
    L.append(f"    depth after: min {st['depth_after_min']:.3f} "
             f"median {st['depth_after_median']:.3f} "
             f"max {st['depth_after_max']:.3f} m")
    L.append(f"    zones {st['zones']}  samples per zone {st['zone_samples']}")
    for n, r in d["dredge"]["per_region"].items():
        L.append(f"      {n:<18} n={r['samples']:<5} bed {r['bed']:g}  "
                 f"depth after {r['depth_after_min']:.3f}..{r['depth_after_max']:.3f} m  "
                 f"worst cut {r['max_cut_m']:.3f} m")
    L.append("")
    f = d["dock_face_m"]
    L.append(f"  THE U  {f['total']:g} m of dock face "
             f"(west {f['west']:g} + south {f['south']:g} + east {f['east']:g}), "
             f"basin {f['basin_clear_width_m']:g} x {f['basin_clear_length_m']:g} m, "
             f"turning circle needed {f['turning_circle_needed_m']:g} m")
    for a in d["arms"]:
        L.append(f"    {a['name']:<10} x{a['x']} z{a['z']}  {a['tiles']} tiles, "
                 f"{a['pile_stacks']} pile stacks, {a['poles']} poles, "
                 f"worst pile reach {a['pile_reach_max_m']:.3f} m")
    for l in d["links"]:
        L.append(f"    {l['name']:<11} {l['span_m']:g} m of trestle to firm "
                 f"ground at {l['landing_h']:.3f}, step "
                 f"{l['step_from_deck_m']:+.3f} m off the 30.6 deck")
    L.append("")
    L.append("  BERTHS, shallowest sample under each hull's OWN ellipse at its OWN yaw")
    for b in d["berths"]:
        L.append(f"    {b['id']} {b['face']:<5} ({b['x']:.1f},{b['z']:.1f}) "
                 f"yaw {b['yaw']:.0f}  largest berthable: {b['largest_berthable']}")
        for v in ("CargoShip", "WarShip", "VikingShip", "BigCargoShip"):
            h = b["hulls"][v]
            L.append(f"      {v:<14} need {h['required_m']:.1f}  "
                     f"shallowest {h['shallowest_under_hull_m']:.3f}  "
                     f"margin {h['margin_m']:+.3f}  "
                     f"open sea {h['every_sample_open_sea']}  "
                     f"n={h['hull_samples']}  "
                     f"{'BERTHABLE' if h['berthable_here'] else 'refused: ' + (h['why'] or 'depth')}")
    L.append("")
    L.append("  GATE PASSAGE, under the hull rather than along the wall line")
    for k in ("before_dredge", "after_dredge"):
        g = d["gate_passage"][k]
        L.append(f"    {k} ({g['surface']}):")
        for v in ("CargoShip", "WarShip", "VikingShip", "BigCargoShip"):
            r = g["hulls"][v]
            L.append(f"      {v:<14} beam {r['beam_m']:5.2f} fits "
                     f"{str(r['beam_fits']):<5} shallowest "
                     f"{r['shallowest_under_hull_m']:.3f} margin "
                     f"{r['margin_m']:+.3f} -> "
                     f"{'PASS' if r['passes'] else 'REFUSED'}")
    m = d["sea_wall_moved"]
    L.append("")
    L.append(f"  WALL  module feet moved by the dredge: "
             f"{len(m['module_feet_moved'])} of {len(d['sea_wall']['segments'])}")
    L.append(f"    gate water {m['gate_depth_before']} -> {m['gate_depth_after']}")
    L.append(f"    barrier covers y {m['barrier_covers_before']} -> "
             f"{m['barrier_covers_after']}; clear opening "
             f"{m['clear_width_unchanged_m']:g} m UNCHANGED")
    L.append("")
    L.append("  CLOSURE, both directions, on the cut bed")
    for c in d["closure"]:
        L.append(f"    need {c['required_depth_m']:.1f} m  gate "
                 f"{'OPEN  ' if c['gate_open'] else 'BARRED'} -> basin "
                 f"{c['basin_area_m2']:6d} m2  connected to open sea: "
                 f"{c['connected_to_open_sea']}")
    wa = d["walk_aboard"]
    L.append("")
    L.append(f"  WALK ABOARD from the {wa['quay_walking_y']:g} m quay "
             f"(hull origin sits {wa['origin_below_surface_m']:g} m below the "
             f"local surface, MEASURED)")
    for r in wa["rows"]:
        if not r.get("measurable"):
            L.append(f"    {r['vessel']:<18} {r['why']}")
            continue
        L.append(f"    {r['vessel']:<18} deck y {r['hull_deck_y']:.3f}  step "
                 f"{r['step_m']:+.3f} m {r['direction']} -- {r['verdict']}")
    L.append(f"    worst step among ADMITTED LARGE hulls: "
             f"{wa['worst_large_admitted_m']:.3f} m")
    L.append("")
    L.append(f"  PIECES {d['pieces']} in {len(U_STAGE_ORDER) - 1} stages "
             f"+ {len(d['_plan_data'])} data-carrying pieces")
    L.append(f"    {json.dumps(d['plan_pieces'])}")
    if d.get("stage_partition"):
        L.append(f"    per stage {d['stage_partition']['per_stage']}  "
                 f"partition total_ok={d['stage_partition']['total_ok']}")
    L.append(f"  PORTAL {d['portal']['prefab']} at "
             f"({d['portal']['x']:g},{d['portal']['z']:g}) y "
             f"{d['portal']['y']:.3f}, freeboard "
             f"{d['portal']['freeboard_m']:+.3f} m -- placed AFTER the terrain")
    L.append(f"  ADMITTED {sorted(d['admitted'])}")
    L.append(f"  REFUSED BY THE GATE ONLY {list(d['refused_by_gate'])} -- and "
             f"the dredged basin floats it: see berth W1 BigCargoShip above")
    return "\n".join(L)


def stage_save(b, d: dict, stage: str) -> dict:
    """The verified save that closes a stage boundary.

    THREE THINGS THE FIRST VERSION OF THIS RECORD GOT WRONG, all three
    REFUSED by the ledger on the LIVE run rather than at replay time:
      * `params.reason` is not in `save`'s schema (known: flatten,
        flatten_reason, installation_id, note, provisional, role, site_id) --
        unmodelled data belongs in `meta`, which is free-form;
      * `expect` is MANDATORY and non-empty on a mutating op;
      * a mutating op with no `wire` cannot be replayed.

    `ubuild --dry` reported 48 records and 0 schema problems and did NOT
    catch any of them, because the save emit sits inside `if not args.dry`:
    the dry run validated the 48 world records and NONE of the 8 saves.
    That is a dry-run BLIND SPOT, not a dry-run pass, and it is the same
    shape as the portal that was staged as `op="spawn"` and thereby made
    invisible to the `portal` op's tag check, its `pair_tag` requirement and
    the `close()`-time `(tag -> exactly 2)` invariant. In both cases the
    validator did not approve the record; the validator was never asked.
    MEASURED cost of this one: the dredge `terrain_write` landed at seq 2666
    and its save was refused, leaving a terrain record with no save after it
    -- precisely the chain tail the stage-boundary save exists to prevent.

    The postcondition is derived from the stage's OWN work, via the same
    `sentinel` the piece records use, so a save cannot pass on a neighbour's
    pieces. It is deliberately NOT a claim that the save landed: whether the
    save landed is a HOST-SIDE fact -- `World save (5/5) done` in the
    container's supervisor log plus world-file mtimes -- and is measured
    there, because the command's own return says nothing. What this record
    proves is that the server was alive and still holding this stage's work
    on the far side of the save.
    """
    if stage == "dredge":
        # One compiler per dredged zone, at the zone centre inside 31 m --
        # smaller than the 32 m half-zone, so no neighbouring zone's
        # compiler can answer for this one.
        checks = [dict(prefab="_TerrainCompiler",
                       pos=[float(c) for c in TC.zone_centre(zx, zz)],
                       max=31.0, count=1, tolerance=0)
                  for zx, zz in d["_dredge"]["stats"]["zones"]]
    elif stage == "signs":
        rows = [r for r in d["_plan_data"].sorted_rows() if r[0] == SIGN]
        checks = [sentinel(rows, d["_plan_data"].rows)] if rows else []
    else:
        rows = u_stage_rows(d, stage)
        checks = [sentinel(rows, d["_plan"].rows)] if rows else []
    if not checks:
        raise SystemExit(
            f"REFUSING to emit a save for stage {stage}: no measurable "
            f"postcondition could be derived from its own rows. "
            f"'I could not measure it' is not 'it is fine'.")
    return b.emit(
        "save",
        params={"role": U_STAGE_ROLE.get(stage, "harbour_place"),
                "site_id": SITE_A,
                "note": f"U stage {stage} complete"},
        wire=["save"],
        expect={"prefab_count": checks},
        meta={"settlement": SITE_A, "stage": stage,
              "why": ("a verified save at every stage boundary, so any later "
                      "boundary is a clean stopping point rather than a chain "
                      "tail with no save after it")})


# THE PORTAL IS REFUSED IN THIS BUILD, and the refusal is recorded rather
# than the record being quietly dropped.
#
# It was staged as `op="spawn"` carrying a portal blob. MEASURED against
# `ledger/schema.py`: the `portal` op REQUIRES `tag` and `pair_tag`,
# validates the tag against `TAG_RE` (1-10 chars), errors if
# `pair_tag != tag`, and the uniqueness invariant `(tag -> exactly 2 portal
# ZDOs)` is checked at `close()` over `op == "portal"` records ONLY. A
# portal placed as a `spawn` is invisible to all four. 0 of the 60 portals
# in this chain were ever placed that way.
#
# Two further measured facts: the staged tag `ulfsgard` breaks the `u-<site>`
# / `x-<crossing>` convention of the 20 tags in use, and it is a SINGLETON --
# every existing tag has exactly two ZDOs. One arch with a fresh unique tag
# is inert rather than dangerous (it collides with none of the 20, so there
# is no random destination) but it is not a working portal either.
#
# The seat is SOUND and is not what is wrong: (1048, 673), applied ground
# 31.444, freeboard +1.444 m over the y=30.0 plane, dry land behind the
# south quay. Only the op and the tag are wrong.
#
# The correct form is a TWO-RECORD pass in one invocation -- `op="portal"`,
# `tag`/`pair_tag` = `u-ulfsgard` (10 chars, exactly at the TAG_RE limit, no
# collision), harbour end here and hall end in the portal hall's bay 5 --
# so the invariant holds at `close()` instead of being broken by one record
# and mended later. That pass needs a writer who owns the hall; this build
# lands 47 records and refuses the 48th.
PORTAL_REFUSED = {
    "refused": "portal_wood at (1048, 673)",
    "why_not_placed": ("staged as op=spawn, which is invisible to the portal "
                       "op's tag validation, its pair_tag requirement and the "
                       "close()-time (tag -> exactly 2 portal ZDOs) invariant"),
    "tag_staged": "ulfsgard",
    "tag_convention": "u-<settlement> / x-<crossing>, 20 tags in use",
    "would_be_singleton": True,
    "existing_tags_have_zdos": 2,
    "collides_with_existing_tag": False,
    "seat_is_sound": {"x": 1048.0, "z": 673.0, "ground_y": 31.444,
                      "freeboard_m": 1.444, "on": "dry land behind the south quay"},
    "correct_form": {"op": "portal", "tag": "u-ulfsgard",
                     "pair_tag": "u-ulfsgard", "records": 2,
                     "other_end": "portal hall bay 5",
                     "must_be_one_pass": True},
}


def cmd_ubuild(args) -> int:
    """Build the U on the LIVE world, one stage at a time, terrain first."""
    from ledger.live import LiveBuilder  # noqa: PLC0415
    import replay as R                   # noqa: PLC0415
    import build as B                    # noqa: PLC0415

    s = surface()
    d = design_u(s)
    u_partition(d)
    want = [q for q in U_STAGE_ORDER if q in (args.stages or U_STAGE_ORDER)]
    report = {"stages": [], "want": want}

    # PROTOCOL GUARD 1: RE-HASH THE LEDGER IMMEDIATELY BEFORE RECORD 1.
    # An flock serialises the ledger FILE; it does not serialise RCON. Two
    # writers can therefore both append cleanly while the game takes
    # overlapping compiler writes, and zone disjointness is not writer
    # exclusion. A hash taken at TAKING and re-taken here turns "a second
    # writer appeared in the interval" from a surprise into a measurement, and
    # the build refuses rather than interleaves.
    led_path = worldseed.ledger_path()     # THIS run's world, at call time
    import hashlib  # noqa: PLC0415
    raw = led_path.read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    report["ledger_preflight"] = dict(
        path=str(led_path), bytes=len(raw), sha256=got,
        records=sum(1 for q in raw.decode().splitlines() if q.strip()),
        expected=args.expect_sha or None)
    if args.expect_sha and got != args.expect_sha:
        raise SystemExit(
            f"REFUSING: the ledger moved between TAKING and record 1.\n"
            f"  expected {args.expect_sha}\n  found    {got}\n"
            f"Somebody else appended in the interval. Two writers on one live "
            f"world is the hazard this check exists for; re-read the chain, "
            f"settle who holds the token, and re-run.")

    with LiveBuilder(actor=ACTOR, dry=args.dry) as b:
        srv = b.srv
        # PROTOCOL GUARD 2: `players` before each write phase, RECORDED. The
        # operator being logged in is then a measurement rather than an
        # assumption, and an 8 B / 32-35 ms round trip also proves the Unity
        # main thread ran a frame -- which the log cannot, because the
        # heartbeat is 600 s and a quiet log looks exactly like a wedged one.
        players = srv.command("players") if not args.dry else "dry"
        report["players"] = str(players)[:200]
        report["players_per_stage"] = {}
        for stage in want:
            if not args.dry:
                report["players_per_stage"][stage] = str(
                    srv.command("players"))[:200]
            if stage == "dredge":
                probes = {}
                for (zx, zz) in d["_dredge"]["stats"]["zones"]:
                    if args.dry:
                        cx, cz = TC.zone_centre(zx, zz)
                        probes[(zx, zz)] = {
                            "count": 1,
                            "cmd": f"objects_count id=_ZoneCtrl "
                                   f"pos={cx:g},{cz:g} max=1 (DRY: not sent)",
                            "centre": [cx, cz]}
                    else:
                        probes[(zx, zz)] = B.zone_probe(srv, zx, zz)
                r = dredge_terrain_op(b, d, probes)
                report["stages"].append({"stage": stage, "records": [r]})
            elif stage == "signs":
                recs = []
                for g in d["guideposts"]:
                    recs.append(b.emit(**_strip_op(WF.spawn_op(
                        SITE_A, "waypoint_sign", g["prefab"], g["x"], g["y"],
                        g["z"], g["yaw"], reason=OVER_WATER_REASON
                        if g["seated_on"] == "deck" else ON_LAND_REASON,
                        blob=g["blob"], strings={"text": g["text"]},
                        meta={"part": "ulfsgard/guidepost", "stage": stage}))))
                # THE PORTAL IS REFUSED, and the refusal is APPENDED as a
                # `note` rather than silently omitted. A record that is not
                # sent and not written down is indistinguishable at replay
                # from one nobody thought of. See PORTAL_REFUSED for the
                # measurements; the seat is sound and only the op and the
                # tag are wrong, so this is a deferral with a spec attached
                # and not a dead end.
                recs.append(b.emit(
                    "note",
                    params={"text": json.dumps(PORTAL_REFUSED),
                            "role": "spawn_portal", "site_id": SITE_A},
                    meta={"part": "ulfsgard/portal-harbour",
                          "stage": stage, "refused": True,
                          "why": ("a portal staged as op=spawn is invisible "
                                  "to the portal op's tag validation, its "
                                  "pair_tag requirement and the close()-time "
                                  "(tag -> exactly 2 portal ZDOs) invariant. "
                                  "An arch that pairs with nothing is not a "
                                  "portal, so this build lands 47 and the "
                                  "pair is a two-record pass for whoever "
                                  "owns the portal hall -- to be placed only "
                                  "AFTER this build's stage saves have "
                                  "verified, because portal_wood's origin is "
                                  "its foot and a seat measured against a "
                                  "blob in flight is not a seat measured "
                                  "against a saved surface")}))
                report["stages"].append({"stage": stage, "records": recs})
            else:
                rows = u_stage_rows(d, stage)
                if not rows:
                    report["stages"].append({"stage": stage, "records": [],
                                             "note": "no rows"})
                    continue
                recs = []
                # `placed_before` is what the site holds when this stage
                # starts: every EARLIER stage's rows, in the design's own
                # stage order. Passing the whole plan here is what made
                # boathouse batch 6 predict a partner two batches in its own
                # future. `--from-batch` resumes a stage whose earlier
                # batches already stand, so an interrupted stage continues
                # instead of re-sending records and doubling their pieces.
                earlier = [q for st in U_STAGE_ORDER[:U_STAGE_ORDER.index(stage)]
                           for q in u_stage_rows(d, st)]
                fb = int(getattr(args, "from_batch", 1) or 1) \
                    if stage == want[0] else 1
                ops = stage_ops(SITE_A, stage, rows, d["_plan"].rows,
                                U_STAGE_ROLE[stage], placed_before=earlier,
                                from_batch=fb)
                for i, op in enumerate(ops):
                    # `plan_sha256` is the digest of the plan text this record
                    # actually sends, stored as a ledger blob, so a replay can
                    # prove it is replaying the same pieces rather than the
                    # same command count.
                    text = "\n".join(op["wire"][0].split(";")) + "\n"
                    op["params"]["plan_sha256"] = b.blob(
                        text.encode(),
                        note=f"{SITE_A}/{stage} batch "
                             f"{op['meta']['batch']}/{op['meta']['batches']}")
                    recs.append(b.emit(**_strip_op(op)))
                    if not args.dry:
                        # HOST-SIDE SINK CHECK BETWEEN RECORDS. A `players`
                        # round trip is 8 B and 32-35 ms and proves the Unity
                        # main thread ran a frame; the log cannot, because the
                        # heartbeat is 600 s and a quiet log looks exactly like
                        # a wedged one for ten minutes.
                        g = WF.sink_gate(f"{SITE_A}/{stage} batch {i + 1}")
                        if g.get("verdict_after") not in (None, "flowing", "ok"):
                            report.setdefault("sink", []).append(g)
                report["stages"].append({"stage": stage, "records": recs})
            # EMITTED IN DRY MODE TOO, and that is the fix for the defect
            # that put a terrain_write in the chain with no save after it.
            # The old form was guarded by `if not args.dry`, so `--dry`
            # validated the world records and NONE of the saves, reported
            # "0 schema problems", and the first save the ledger ever saw was
            # the one it refused on the LIVE run. A dry run that does not
            # validate what the live run sends is not a dry run of it.
            report["stages"][-1].setdefault("saves", []).append(
                stage_save(b, d, stage))
        report["close"] = b.close()
    if getattr(args, "report", None):
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(report, indent=1, default=str))
    problems = [q for st in report["stages"] for r in st["records"]
                for q in (r.get("schema_problems") or [])]
    print(json.dumps({"want": report["want"],
                      "players": report.get("players"),
                      "per_stage": {st["stage"]: len(st["records"])
                                    for st in report["stages"]},
                      "schema_problems": problems[:20],
                      "schema_problem_count": len(problems),
                      "sink": report.get("sink"),
                      "report": getattr(args, "report", None)}, indent=1,
                     default=str))
    return 0


def _strip_op(op: dict) -> dict:
    """`waterfront.spawn_op` / `stage_ops` return a whole record; `emit` takes
    the pieces. Unpacked here rather than at every call site."""
    return dict(op=op["op"], params=op["params"], wire=op.get("wire"),
                requires=op.get("requires"), expect=op.get("expect"),
                meta=op.get("meta"))


# ---------------------------------------------------------------------------
# verifying the U, LIVE, after the writes
# ---------------------------------------------------------------------------
#
# Two halves, because they use different instruments and one of them is the
# dangerous one:
#
#   the LIVE half   scoped `objects_count` per prefab (tolerance 0) plus ONE
#                   bounded `findObjects -prefab` per gate jamb.  Both are
#                   kept small on purpose: `findObjects`' per-ZDO listing IS
#                   its reply text, and ~1,900 pieces is ~200 KB in a single
#                   main-thread write, which is the outage this project has
#                   measured twice.
#   the TERRAIN half `WaterProof.cs` with FROM_SAVE=1 against a COPY of the
#                   live save: the game instantiates the `_TerrainCompiler`
#                   ZDOs the world actually stored, rebuilds its own heightmap
#                   and then answers `Heightmap.GetHeight` and
#                   `Floating.GetWaterLevel` at every point we ask about.  That
#                   is the world measuring itself, not us re-reading our own
#                   blobs.

VERIFY_DIR = Path("/tmp/hc/verify")


def hull_ellipse_points(bx: float, bz: float, yaw: float, vessel: str,
                        step: float = 1.0) -> list[tuple[float, float]]:
    """The integer lattice samples inside a hull's own ellipse at its own yaw.

    The SAME ellipse `waterfront.hull_depths` uses -- half-extents of the solid
    collider AABB rotated onto the vessel's yaw -- so the live reading and the
    design reading are measurements of the same footprint and can be compared.
    """
    v = WF.VESSELS[vessel]
    ux, uz = A._unit(yaw)
    lx, lz = uz, -ux
    hl, hw = v["hull_l"] / 2.0, v["hull_w"] / 2.0
    r = int(math.ceil(max(hl, hw))) + 1
    out = []
    for dx in np.arange(-r, r + 1, step):
        for dz in np.arange(-r, r + 1, step):
            a = (dx * ux + dz * uz) / hl
            b = (dx * lx + dz * lz) / hw
            if a * a + b * b <= 1.0:
                out.append((round(bx + dx, 3), round(bz + dz, 3)))
    return out


def verify_points(d: dict) -> tuple[Path, list[dict]]:
    """Every point the terrain half has to read, with what it is for.

    Written to a FILE rather than an env var: four hull ellipses are ~640
    samples and the basin grid another ~1,000, and the design also wants the
    bed under every one of its 1,011 pieces.
    """
    rows = []
    xw, xe = U["x_west_face"], U["x_east_face"]
    zs, zn = U["z_south_face"], U["z_north_end"]
    # the basin, on a 4 m grid
    for x in np.arange(xw, xe + 0.1, 4.0):
        for z in np.arange(zs, zn + 0.1, 4.0):
            rows.append(dict(label=f"basin|{x:g}|{z:g}", x=float(x), z=float(z),
                             kind="basin"))
    # the approach and the throat, on the gate centreline
    for z in np.arange(746.0, 778.1, 2.0):
        rows.append(dict(label=f"approach|{z:g}", x=1066.5, z=float(z),
                         kind="approach"))
    # every berth, every large ADMITTED hull, under its own ellipse
    for b in U_BERTHS:
        for v in ("CargoShip", "WarShip", "VikingShip", "MercantShip"):
            for (px, pz) in hull_ellipse_points(b["x"], b["z"], b["yaw"], v):
                rows.append(dict(label=f"hull|{b['id']}|{v}|{px:g}|{pz:g}",
                                 x=px, z=pz, kind="hull", berth=b["id"],
                                 vessel=v))
    # the bed under every piece, for the grounding census
    for i, r in enumerate(d["_plan"].sorted_rows()):
        rows.append(dict(label=f"piece|{i}", x=r[1], z=r[3], kind="piece",
                         piece=i))
    for i, r in enumerate(d["_plan_data"].sorted_rows()):
        rows.append(dict(label=f"data|{i}", x=r[1], z=r[3], kind="data",
                         piece=i))
    # de-duplicate on (label) but keep every distinct xz
    VERIFY_DIR.mkdir(parents=True, exist_ok=True)
    path = VERIFY_DIR / "points.tsv"
    seen = set()
    with path.open("w") as fh:
        fh.write("# label\tx\tz\n")
        for r in rows:
            if r["label"] in seen:
                continue
            seen.add(r["label"])
            fh.write(f"{r['label']}\t{r['x']:g}\t{r['z']:g}\n")
    return path, rows


def grounding_census(d: dict, bed: dict) -> dict:
    """Worst air gap and worst burial, per prefab class, against the LIVE bed.

    THE UNIT IS THE STACK, NOT THE PIECE, and this was a defect in the first
    version of this census rather than a refinement.  `piles()` builds a COLUMN
    of `wood_pole_log_4`: the lowest pole reaches the bed and every pole above
    it is carried by the one below.  Measuring each pole against the seabed
    reported 24 "floating" piles with a worst gap of 2.553 m on a design whose
    every column actually lands exactly on the bed, and the same error called
    three portcullis panels floating when they are the UPPER ROW of a two-row
    barrier.  So pieces are grouped by (prefab class, xz column) and the
    column's LOWEST member is what has to reach the ground.

    The classes have DIFFERENT datums and using one prefab's datum for another
    is the defect that put three beacons 5.173 m in the air:

      pile        `wood_pole_log_4`, pivot at its CENTRE, so a pole's bottom is
                  y - 2.000.  AIR GAP = column bottom - bed; positive is a
                  column hanging over the seabed.  A column that is BURIED is
                  not a defect -- a pile is meant to be in the ground -- so
                  burial is reported and not counted as a failure.
      deck        `wood_floor`, top face +0.0969 above its origin, authored at
                  30.5031 so the walking surface is 30.6.  BURIAL = bed - top;
                  positive is a deck tile inside the bank, and that IS a defect.
      fb_ kit     pivot AT THE FOOT, so AIR GAP = column bottom - bed.
      on-deck     torch, stair, wall: these stand on the DECK, so their datum is
                  30.6 and the question is whether a deck tile is under them.
      on-wall     roof and beam: carried by the wall courses AT THEIR EAVES.
                  A gable overhangs the bay void by design, so the test is
                  whether a wall course exists at the same LATITUDE, not
                  directly underneath.
      on-tower    a beacon on a wall tower's FIGHTING PLATFORM at +10.229, not
                  on any deck and not on the ground.
    """
    rows = d["_plan"].sorted_rows()
    deck_xz = {(round(r[1], 2), round(r[3], 2)) for r in rows
               if r[0] == GEOM["floor"]["prefab"]}
    wall_xz = {(round(r[1], 2), round(r[3], 2)) for r in rows
               if r[0] == GEOM["wall"]["prefab"]}
    tower_xz = {(round(r[1], 2), round(r[3], 2)) for r in rows
                if r[0] == KIT["tower"]}

    pile_cols: dict[tuple, dict] = {}
    fb_cols: dict[tuple, dict] = {}
    decks, ondeck, onwall, ontower, onground = [], [], [], [], []
    for i, r in enumerate(rows):
        pf, x, y, z, _yaw, why = r
        b = bed.get(f"piece|{i}")
        if pf == GEOM["pile"]["prefab"]:
            key = (round(x, 2), round(z, 2))
            bot = y - GEOM["pile"]["half"]
            col = pile_cols.setdefault(key, dict(x=x, z=z, poles=0,
                                                 bottom=bot, bed=b))
            col["poles"] += 1
            col["bottom"] = min(col["bottom"], bot)
            continue
        if pf.startswith("fb_"):
            key = (pf, round(x, 2), round(z, 2))
            col = fb_cols.setdefault(key, dict(prefab=pf, x=x, z=z, members=0,
                                               bottom=y, bed=b))
            col["members"] += 1
            col["bottom"] = min(col["bottom"], y)
            continue
        if b is None:
            continue
        if pf == GEOM["floor"]["prefab"]:
            top = y + GEOM["floor"]["top"]
            decks.append(dict(i=i, x=x, z=z, top=round(top, 3), bed=round(b, 3),
                              burial_m=round(b - top, 3)))
        elif pf in (GEOM["roof"]["prefab"], GEOM["beam"]["prefab"]):
            # A GABLE IS NOT SUPPORTED FROM UNDERNEATH ITS RIDGE.  The ridge
            # runs along the bay's long axis and the slopes shed to either side
            # onto the wall courses at the EAVES, so a roof tile over the bay
            # void has nothing directly below it by design -- MEASURED: 144 of
            # 234 roof tiles have no wall within 2.05 m in BOTH axes, on a roof
            # whose every course is carried at its own ends. The question a
            # gable actually answers is whether its own COURSE has wall support
            # at its own latitude, so the test is on z alone.
            near = any(abs(z - wz) <= 2.05 for _wx, wz in wall_xz)
            onwall.append(dict(i=i, prefab=pf, x=x, z=z, y=round(y, 3),
                               above_deck_m=round(y - DECK_Y, 3),
                               wall_under=near))
        elif pf == TORCH and "beacon" in why:
            near = any(abs(x - tx) <= 2.05 and abs(z - tz) <= 2.05
                       for tx, tz in tower_xz)
            ontower.append(dict(i=i, x=x, z=z, base=round(y - TORCH_ABOVE_SURFACE, 3),
                                tower_under=near, why=why))
        elif pf in (TORCH, GEOM["stair"]["prefab"], GEOM["wall"]["prefab"]):
            base = y - (TORCH_ABOVE_SURFACE if pf == TORCH else 0.0)
            near = any(abs(x - dx) <= 2.05 and abs(z - dz) <= 2.05
                       for dx, dz in deck_xz)
            ondeck.append(dict(i=i, prefab=pf, x=x, z=z, base=round(base, 3),
                               above_deck_m=round(base - DECK_Y, 3),
                               deck_tile_under=near))
        else:
            onground.append(dict(i=i, prefab=pf, x=x, z=z, bed=round(b, 3),
                                 air_gap_m=round(y - b, 3)))

    piles = [dict(x=c["x"], z=c["z"], poles=c["poles"],
                  bottom=round(c["bottom"], 3), bed=round(c["bed"], 3),
                  air_gap_m=round(c["bottom"] - c["bed"], 3))
             for c in pile_cols.values() if c["bed"] is not None]
    fbk = [dict(prefab=c["prefab"], x=c["x"], z=c["z"], members=c["members"],
                bottom=round(c["bottom"], 3), bed=round(c["bed"], 3),
                air_gap_m=round(c["bottom"] - c["bed"], 3))
           for c in fb_cols.values() if c["bed"] is not None]

    def worst(rs, key, sign=1):
        if not rs:
            return None
        return dict(max(rs, key=lambda q: sign * q[key]))

    # A column standing on a bed within the instrument's own noise is grounded.
    # 0.0275 m is the MEASURED worst disagreement between two independently
    # scanned 1 m fields over the same six control points; 0.25 m is the
    # project's own BERTH_MARGIN_FLOOR and is used here as the visible-gap
    # threshold, because a 5 cm gap under a pile is not something an eye finds
    # and a 25 cm one is.
    return dict(
        piles=dict(columns=len(piles), poles=sum(c["poles"] for c in piles),
                   worst_air_gap=worst(piles, "air_gap_m"),
                   deepest_buried=worst(piles, "air_gap_m", -1),
                   floating=[q for q in piles if q["air_gap_m"] > 0.25],
                   note="burial is not a defect for a pile"),
        decks=dict(n=len(decks), worst_burial=worst(decks, "burial_m"),
                   buried=[q for q in decks if q["burial_m"] > 0.0]),
        fb_kit=dict(columns=len(fbk), pieces=sum(c["members"] for c in fbk),
                    worst_air_gap=worst(fbk, "air_gap_m"),
                    deepest_buried=worst(fbk, "air_gap_m", -1),
                    floating=[q for q in fbk if q["air_gap_m"] > 0.25]),
        on_deck=dict(n=len(ondeck),
                     no_deck_tile_under=[q for q in ondeck
                                         if not q["deck_tile_under"]],
                     worst_above_deck=worst(ondeck, "above_deck_m")),
        on_wall=dict(n=len(onwall),
                     no_wall_under=[q for q in onwall if not q["wall_under"]]),
        on_tower=dict(n=len(ontower),
                      no_tower_under=[q for q in ontower
                                      if not q["tower_under"]]),
        on_ground=dict(n=len(onground), worst_air_gap=worst(onground, "air_gap_m"),
                       deepest_buried=worst(onground, "air_gap_m", -1)),
        thresholds=dict(visible_gap_m=0.25, field_noise_m=0.0275),
        method=("MEASURED against the bed the LIVE SAVE produces: "
                "`WaterProof.cs` FROM_SAVE=1 instantiates the world's own "
                "`_TerrainCompiler` ZDOs and answers `Heightmap.GetHeight` at "
                "each piece's own xz. Grouped by COLUMN, because the unit that "
                "has to reach the ground is the stack and not the piece"))


def reach_from_face(d: dict, bed: dict) -> dict:
    """Is a hull at each berth actually REACHABLE from the dock face?

    Two things, both measured, because either one alone passes on a defect:
      1. the gap from the dock face to the hull's own side, against the
         0.232 m-below-surface float altitude and the hull's measured beam;
      2. a deck tile within a stride of the face at that berth's latitude --
         a berth beside a hole in the quay is not reachable.
    """
    rows = d["_plan"].sorted_rows()
    deck = [(r[1], r[3]) for r in rows if r[0] == GEOM["floor"]["prefab"]]
    out = []
    for b in U_BERTHS:
        face = U["x_west_face"] if b["face"] == "west" else U["x_east_face"]
        beam = max(WF.VESSELS[v]["hull_w"] for v in ADMITTED
                   if WF.VESSELS[v]["cls"] == "large")
        side = b["x"] - beam / 2.0 if b["face"] == "west" else b["x"] + beam / 2.0
        gap = abs(side - face)
        tiles = [(x, z) for x, z in deck if abs(z - b["z"]) <= 1.01
                 and abs(x - face) <= 3.01]
        out.append(dict(
            berth=b["id"], face=b["face"], face_x=face,
            widest_admitted_large_beam_m=beam,
            hull_side_x=round(side, 3), gap_face_to_hull_m=round(gap, 3),
            deck_tiles_within_3m_of_face_at_this_z=len(tiles),
            bed_at_face=round(bed.get(f"basin|{face:g}|{b['z']:g}", float('nan')), 3)
            if f"basin|{face:g}|{b['z']:g}" in bed else None,
            reachable=bool(tiles) and gap <= 3.0,
            why=("a hull lies against the fenders, so the gap is the "
                 "stand-off minus half the widest admitted large beam; over "
                 "3 m and it is a jump, not a step")))
    return dict(berths=out,
                all_reachable=all(r["reachable"] for r in out),
                walk_aboard=d["walk_aboard"])


def cmd_uverify(args) -> int:
    """Verify the built U. LIVE counts plus a save-derived terrain read."""
    import replay as R  # noqa: PLC0415
    s = surface()
    d = design_u(s)
    VERIFY_DIR.mkdir(parents=True, exist_ok=True)
    rep: dict = dict(site=SITE_A, water_level_y=W.WATER_LEVEL)

    pts, want = verify_points(d)
    rep["points_file"] = dict(path=str(pts), points=len(want))

    # --- the terrain half -------------------------------------------------
    if not args.skip_terrain:
        import subprocess  # noqa: PLC0415
        out = VERIFY_DIR / "waterproof_from_save.tsv"
        # EVERY zone any verification point falls in, not just the dredged
        # ones: MEASURED pre-fix, 16 points read a water surface of -10000
        # because their zone was never spawned and so had no heightmap and no
        # water volume. A -10000 that means "unspawned zone" next to a -10000
        # that means "no water here" is an instrument that cannot answer its
        # own question.
        need_zones = set()
        for r in want:
            for zz2 in zones_containing(r["x"], r["z"]):
                need_zones.add(zz2)
        zones = ";".join(f"{zx},{zz}" for zx, zz in sorted(need_zones))
        rep["zones_spawned"] = sorted(need_zones)
        env = dict(os.environ)
        env.update(VH_SRC=args.vh_src, WORLD_SRC=args.world_src,
                   WORLD=args.world, ZONES=zones, POINTS="",
                   POINTS_FILE=str(pts), FROM_SAVE="1", SHIPS="", SETTLE="1",
                   OUT=str(out), SANDBOX=args.sandbox)
        cp = subprocess.run(["bash", str(JUMPSTART / "terraform"
                                         / "run_waterproof.sh")],
                            env=env, capture_output=True, text=True,
                            timeout=1800)
        rep["waterproof"] = dict(returncode=cp.returncode, out=str(out),
                                 stderr_tail=cp.stderr[-1500:])
        bed, surf = {}, {}
        comp = []
        for line in out.read_text().splitlines() if out.exists() else []:
            f = line.split("\t")
            if f[0] == "point" and len(f) > 12:
                bed[f[1]] = float(f[5])          # applied bed
                surf[f[1]] = float(f[10])        # water surface just under plane
            elif f[0].startswith("compiler_from_save") or f[0] == "compilers_from_save":
                comp.append(line)
        rep["compilers_from_save"] = comp
        rep["samples_read"] = len(bed)
        if bed:
            basin = [30.0 - v for k, v in bed.items() if k.startswith("basin|")]
            appr = [30.0 - v for k, v in bed.items() if k.startswith("approach|")]
            rep["basin_depth_live"] = dict(
                n=len(basin), min=round(min(basin), 3), max=round(max(basin), 3),
                median=round(sorted(basin)[len(basin) // 2], 3),
                under_design=sum(1 for q in basin if q < 5.99))
            rep["approach_depth_live"] = dict(
                n=len(appr), min=round(min(appr), 3), max=round(max(appr), 3))
            rep["water_surface_live"] = dict(
                n=len(surf), min=round(min(surf.values()), 4),
                max=round(max(surf.values()), 4),
                any_missing=sum(1 for v in surf.values() if v < 0.0))
            # per-berth, per-hull, shallowest sample under the hull's OWN ellipse
            per = {}
            for k, v in bed.items():
                if not k.startswith("hull|"):
                    continue
                _t, bid, ves, _x, _z = k.split("|")
                per.setdefault((bid, ves), []).append(30.0 - v)
            rep["berth_depth_live"] = [
                dict(berth=bid, vessel=ves, samples=len(q),
                     shallowest_m=round(min(q), 3), deepest_m=round(max(q), 3),
                     required_m=WF.VESSELS[ves]["draught_m"] + WF.KEEL_CLEARANCE_M,
                     margin_m=round(min(q) - WF.VESSELS[ves]["draught_m"]
                                    - WF.KEEL_CLEARANCE_M, 3),
                     yaw_deg=next(b["yaw"] for b in U_BERTHS if b["id"] == bid),
                     floats=min(q) >= WF.VESSELS[ves]["draught_m"]
                     + WF.KEEL_CLEARANCE_M)
                for (bid, ves), q in sorted(per.items())]
            rep["grounding_census"] = grounding_census(d, bed)
            rep["reachability"] = reach_from_face(d, bed)

    # --- the live half ----------------------------------------------------
    if not args.skip_live:
        with R.Server(dry=False) as srv:
            srv.probe()
            cx = (U["x_west_face"] + U["x_east_face"]) / 2.0
            cz = (U["z_south_face"] + U["z_north_end"]) / 2.0
            radius = 120.0
            counts = {}
            for pf, planned in sorted(d["plan_pieces"].items()):
                n, _ = srv.count(pf, cx, cz, radius)
                counts[pf] = dict(planned=planned, live=n,
                                  delta=n - planned, tolerance=0)
            for pf in ("sign", "portal_wood"):
                n, _ = srv.count(pf, cx, cz, radius)
                planned = sum(1 for r in d["_plan_data"].rows if r[0] == pf)
                counts[pf] = dict(planned=planned, live=n,
                                  delta=n - planned, tolerance=0)
            rep["census"] = dict(
                centre=[cx, cz], radius_m=radius, counts=counts,
                exact=[k for k, v in counts.items() if v["delta"] == 0],
                strays=[k for k, v in counts.items() if v["delta"] > 0],
                missing=[k for k, v in counts.items() if v["delta"] < 0],
                method=("MEASURED live: scoped `objects_count` per prefab over "
                        "one 120 m cylinder covering the whole U, the wall and "
                        "the approach. `objects_count` is y-blind, which is why "
                        "the grounding census is a separate instrument"))
            # the gate's clear opening, from the two flanking modules' own ZDOs
            g0, g1 = WALL["gate"]
            jambs = []
            for jx in (g0 - KIT["wall_len_m"] / 2.0, g1 + KIT["wall_len_m"] / 2.0):
                rows = srv.command(
                    f"findObjects -prefab {KIT['wall']} -near "
                    f"{jx:g} 30 {WALL['z']:g} 6")
                jambs.append(dict(query_x=round(jx, 3), reply=str(rows)[:400]))
            rep["gate_live"] = dict(
                declared_clear_m=WALL["gate_clear_m"],
                jamb_queries=jambs,
                method=("MEASURED: one BOUNDED `findObjects -prefab "
                        "fb_fortress_wall -near <x> 30 752 6` per jamb. Bounded "
                        "and prefab-filtered because findObjects' per-ZDO "
                        "listing IS its reply text and an unbounded one is the "
                        "~200 KB main-thread write that wedged this server "
                        "twice"))

    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(rep, indent=1, default=str))
    print(json.dumps({k: v for k, v in rep.items()
                      if k not in ("grounding_census", "reachability",
                                   "census", "waterproof")},
                     indent=1, default=str)[:3500])
    for k in ("census", "grounding_census", "reachability"):
        if k in rep:
            print(f"\n== {k}")
            print(json.dumps(rep[k], indent=1, default=str)[:2500])
    print(f"\nfull report: {args.report}")
    return 0


# ---------------------------------------------------------------------------
#
# The operator is deciding whether to keep this save or regenerate Ulfsland on
# the SAME SEED and replay the build.  That decision turns every number in this
# module into one of two kinds, and they behave completely differently:
#
#   SEED-DERIVED  - a function of the world generator and the seed string
#                   `Pirate68` alone.  A regeneration reproduces it exactly, so
#                   this module can be re-run against a fresh world and get the
#                   same answer.  Everything geometric here is of this kind
#                   because it all comes from one PatchScan raster of the
#                   GENERATED height and biome field, taken in a sandbox mirror
#                   with the live BepInEx excluded.
#   SAVE-DERIVED  - a function of what is IN this particular save: applied
#                   terrain deltas from the ledger, live ZDOs, live
#                   LocationProxy markers, the existing road network.  A
#                   regeneration destroys all of it, so any of these must be
#                   RE-MEASURED after the regeneration and must never be
#                   carried across it.
#
# MOD-DERIVED is a third kind worth separating because it survives a
# regeneration but NOT a mod change: prefab geometry and ZNetScene membership.
# Tonight's Jotunn 2.30.0 -> 2.30.1 deploy is exactly why the probe is re-run
# rather than trusted.
PROVENANCE = {
    "seed_derived": {
        "instrument": ("PatchScan VHPATCH1 raster of the GENERATED height and "
                       "biome field, seed Pirate68, run in a sandbox mirror of "
                       "the server install with the live BepInEx excluded"),
        "survives_regeneration": True,
        "numbers": [
            "the inlet's geometry: 130 m long, 57-130 m wide, depth p50 3.21 m, max 10.76 m",
            "the mouth's closing sweep and the spit that makes z=728/730/732 fail to close",
            "the chosen wall line z=752, x 1000.5..1132.5, span 132.0 m",
            "every module foot height on the wall line (30.435 .. 24.430)",
            "the gate's clear width 11.946 m and the water under it (2.704 .. 5.095 m)",
            "every bay depth, every berth depth, every pad floor and cut",
            "biome coverage (Meadows 100% to 200 m at both sites)",
            "the pad census against the +/-8 m clamp",
            "both road routes, their profiles, grades, cut/fill and fords",
        ],
    },
    "save_derived": {
        "instrument": "live RCON, and the append-only ledger's terrain blobs",
        "survives_regeneration": False,
        "must_be_retaken_after_regeneration": True,
        "numbers": [
            "the 65 live LocationProxy markers and every stand-off computed from them",
            "the measured reach of each blocking marker",
            "the applied road surface: T9/T8 sample positions and the junction "
            "carriageway datum 39.608 at (824,738) -- a regenerated world has "
            "NO roads at all, so R1 has nothing to join and must be re-planned "
            "as a road to wherever the network is rebuilt",
            "every piece census, grounding census and per-prefab count",
            "the existing waterfront sites and their berthed hulls",
        ],
    },
    "mod_derived": {
        "instrument": ("component + collider dump of ZNetScene in a sandbox "
                       "mirror with the content mods staged"),
        "survives_regeneration": True,
        "survives_a_mod_change": False,
        "numbers": [
            "fb_ ZNetScene membership (29 registered of 53 DLL strings)",
            "fb_fortress_wall 20.009 x 11.866 x 6.800, pivot at foot",
            "fb_fortress_wall_tower platform y 10.229 (NOT the 15.402 mesh top)",
            "fb_fortress_wall_gateway clear 3.429 m, _portcullis clear 6.851 m",
            "fb_fortress_portcullis 5.704 x 5.857, Door present, key null, closable",
            "every HOUSES footprint and pivot offset",
            "every vanilla pivot in GEOM, and piece_groundtorch's +0.65359",
            "VESSELS draughts (Ship.m_waterLevelOffset) and hull collider AABBs",
        ],
    },
    "replay": {
        "records_per_site": ("A 1,357 pieces -> 29 spawn_plan records at 48 "
                             "pieces each; B 653 -> 14; plus one note, one "
                             "observe and one save per stage"),
        "idempotent": True,
        "how": ("`place_missing` censuses the site and places only absent "
                "pieces, so a replay against an empty site places everything, "
                "a replay against a finished site places nothing, and an "
                "interrupted run resumes"),
        "what_a_regeneration_invalidates": (
            "only the save-derived set. The whole geometric design re-derives "
            "from the seed, so `design` can be re-run unchanged; `locprobe` "
            "and `reach` must be re-taken; R1's junction has to be re-planned "
            "because a regenerated world has no roads to join."),
    },
}


def cmd_provenance(args) -> int:
    print(json.dumps(PROVENANCE, indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="harbour_city")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("design", help="measure and lay out both sites; READ-ONLY")
    d.add_argument("--out")
    d.add_argument("--plans")
    d.add_argument("--summary", action="store_true")
    d.add_argument("--lh-x", type=float, default=1140.0)
    d.add_argument("--lh-z", type=float, default=766.0)
    d.set_defaults(fn=cmd_design)
    pr = sub.add_parser("probe", help="prove every fb_ prefab is in ZNetScene")
    pr.add_argument("--apply", action="store_true")
    pr.add_argument("--seat-on-ground", action="store_true", default=True)
    pr.set_defaults(fn=cmd_probe)
    rc = sub.add_parser("reach", help="measure the real reach of blocking markers")
    rc.add_argument("--out", default="/tmp/hc/marker_reach.json")
    rc.set_defaults(fn=cmd_reach)
    ud = sub.add_parser("udesign",
                        help="measure and lay out the DREDGED U; READ-ONLY")
    ud.add_argument("--out")
    ud.add_argument("--plans")
    ud.set_defaults(fn=cmd_udesign)
    ub = sub.add_parser("ubuild", help="build the dredged U on the LIVE world")
    ub.add_argument("--stages", nargs="*", choices=U_STAGE_ORDER)
    ub.add_argument("--dry", action="store_true")
    ub.add_argument("--report", default="/tmp/hc/ubuild_report.json")
    ub.add_argument("--from-batch", type=int, default=1,
                    help="resume the FIRST named stage at this batch, because "
                         "its earlier batches already stand. Re-sending a "
                         "spawn_plan doubles every piece in it, so a stage "
                         "interrupted at batch N continues at N+1 rather "
                         "than restarting")
    ub.add_argument("--expect-sha", default=None,
                    help="the ledger sha256 announced at TAKING; the build "
                         "refuses if the chain moved since")
    ub.set_defaults(fn=cmd_ubuild)
    uv = sub.add_parser("uverify", help="verify the built U, live + save-derived")
    uv.add_argument("--report", default="/tmp/hc/verify/uverify.json")
    uv.add_argument("--skip-live", action="store_true")
    uv.add_argument("--skip-terrain", action="store_true")
    uv.add_argument("--vh-src",
                    default="/media/big4/projects/game/valheim/Ulfsland/data/bepinex")
    # `--world-src` DEFAULTS TO NONE and is DERIVED from the resolved world.
    # It used to default to a hard-coded Ulfsland save path while `--world`
    # was a separate string, so the two could name different worlds and
    # nothing said so -- MEASURED on the gate this evening: `--world Fresh42`
    # moved the cache and the ledger to Fresh42 and left the SAVE on Ulfsland,
    # producing a verdict that was the join of two worlds.  Derived by default,
    # and an explicit override is checked against the save's OWN declared
    # world by `instruments.require_world_save` below.
    uv.add_argument("--world-src", default=None,
                    help="the save to measure; defaults to "
                         "<valheim root>/<world>/config_merged/worlds_local/<world>")
    worldseed.add_arguments(uv)
    uv.add_argument("--sandbox", default="/tmp/waterproof/vh")
    uv.set_defaults(fn=cmd_uverify)
    pv = sub.add_parser("provenance",
                        help="which numbers survive a world regeneration")
    pv.set_defaults(fn=cmd_provenance)
    args = ap.parse_args(argv)
    if getattr(args, "fn", None) is cmd_uverify:
        _seed, args.world = worldseed.apply(args)
        if not args.world_src:
            args.world_src = (f"/media/big4/projects/game/valheim/{args.world}"
                              f"/config_merged/worlds_local/{args.world}")
        # THE SAVE MUST BE THE WORLD IT IS BEING CALLED.  One reader for that
        # question, in `accept/instruments.py`; a second copy here would be a
        # second thing to keep true.
        from accept import instruments as _I                  # noqa: PLC0415
        _I.require_world_save(Path(args.world_src), args.world)
    return args.fn(args)

if __name__ == "__main__":
    raise SystemExit(main())
