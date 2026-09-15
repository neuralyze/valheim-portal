#!/usr/bin/env python3
"""What does a blueprint already CARRY, and where may a loose object stand?

THE BUG THIS EXISTS FOR
-----------------------
MEASURED on Ulfsland on 2026-09-15, after `to_rcon_plan.py` was fixed to centre
a body on its measured footprint: `terraform/stock.py` puts its stations, props
and chests at HARDCODED offsets from the pad centre -- `portal_wood` at
(6.0, 0.0), the chest bank 12 m south, the station row 10 m out -- and those
offsets were solved when the body was anchored on its own CORNER and the middle
of the pad was empty ground. Once the body moved onto the middle of the pad, the
offsets did not.

Box-tested here against all 1,906 body solids of `pre-bonemass/
iron-era-workshop`: 12 of stock.py's 18 fixtures physically intersected the
structure. `portal_wood` at pad-offset (-6.00, 0.00) overlapped a
`stone_wall_4x2` by 1.00 x 2.00 x 1.18 m -- a portal inside a wall, which is
exactly what the operator reported: "the position isnt correct, there are
portals in the middle of walls". `piece_maypole` pierced 22 solids,
`smelter` 20, `piece_workbench` 11. The minimum fixture-to-wall gap across the
set was 0.000 m, i.e. nothing was ever checking.

WHAT THIS MODULE ASSERTS
------------------------
1. OWNERSHIP. A blueprint that carries a prefab owns its POSITION, because the
   blueprint's own coordinates are relative to the building and the pad offsets
   are not. `carried()` reports what a body holds so the stocking step can stop
   duplicating it. MEASURED on this body: it carries all NINE of the preset's
   stations (forge, cauldron, smelter, charcoal_kiln, piece_chest_wood,
   portal_wood, hearth, piece_stonecutter, piece_workbench) plus
   `piece_cartographytable`, so nine of stock.py's ten station spawns were
   duplicates standing in the walls of the originals.

2. LEGALITY. Anything the body does NOT carry still has to stand somewhere, and
   "somewhere" is measured: `free_spot()` searches the pad for a position whose
   prefab solid clears every body solid by a margin, nearest to a preferred
   point. That is the check that would have refused the portal in the wall.

The transform is `to_rcon_plan.py`'s, not a copy of its arithmetic: yaw about the
blueprint origin FIRST, then translate so the measured footprint centre lands on
the pad centre and the datum lands on the pad height. If those two ever diverge
the intersection test is measuring a building that is not there, so
`place_body()` is the single place that knows it and both callers use it.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
import sys

sys.path.insert(0, str(HERE))

import base_geometry  # noqa: E402

# A FIXTURE-role prefab: a loose, functional object that a jumpstart preset or
# `stock.py` might place, as opposed to a structural piece. Name-pattern based
# for the same reason the rest of `base_geometry` is -- a kit variant
# (`piece_chest_blackmetal`, `ashwood_bed`) has to be caught -- and deliberately
# WIDER than the preset's station list, because the question being asked is "is
# this thing already in the building?" and a `piece_chest_wood` in the body
# answers it for a `piece_chest_wood` in the preset.
_FIXTURE_NAME = re.compile(
    r"(?:^|_)(?:chest|portal|smelter|kiln|hearth|forge|workbench|cauldron|"
    r"cookingstation|stonecutter|preptable|cartographytable|bed|firepit|fire_pit|"
    r"maypole|artisanstation|blackforge|spinningwheel|windmill|oven|"
    r"eitrrefinery|beehive|sapcollector|itemstand|bathtub|table|chair|bench|"
    r"throne|stool|sign|wardrobe|barber)", re.IGNORECASE)


def is_fixture(prefab: str) -> bool:
    return bool(_FIXTURE_NAME.search(prefab))


# (x0, x1, y0, y1, z0, z1, prefab)
Solid = tuple[float, float, float, float, float, float, str]


@dataclass
class PlacedBody:
    """A blueprint's solids in WORLD coordinates, as `to_rcon_plan.py` will emit
    them, plus what it carries."""

    solids: list[Solid]
    carried: dict[str, int]
    datum: base_geometry.FloorDatum
    footprint: base_geometry.Footprint
    origin: tuple[float, float, float]
    pivot_only: list[str] = field(default_factory=list)

    def index(self, cell_m: float = 4.0) -> "SolidIndex":
        return SolidIndex(self.solids, cell_m)


class SolidIndex:
    """XZ hash over the body's solids. 1,906 solids times a few thousand
    candidate positions is 10^7 box tests done naively, and the search below is
    run per fixture per preset in a test, so the buckets are not premature."""

    def __init__(self, solids: list[Solid], cell_m: float = 4.0):
        self.cell_m = cell_m
        self.buckets: dict[tuple[int, int], list[Solid]] = {}
        for s in solids:
            for ix in range(math.floor(s[0] / cell_m), math.floor(s[1] / cell_m) + 1):
                for iz in range(math.floor(s[4] / cell_m), math.floor(s[5] / cell_m) + 1):
                    self.buckets.setdefault((ix, iz), []).append(s)

    def near(self, box: tuple[float, float, float, float, float, float]) -> list[Solid]:
        out: dict[int, Solid] = {}
        for ix in range(math.floor(box[0] / self.cell_m),
                        math.floor(box[1] / self.cell_m) + 1):
            for iz in range(math.floor(box[4] / self.cell_m),
                            math.floor(box[5] / self.cell_m) + 1):
                for s in self.buckets.get((ix, iz), ()):
                    out[id(s)] = s
        return list(out.values())


def overlap(a, b) -> tuple[float, float, float]:
    """Per-axis penetration of two AABBs. All three positive means they
    intersect; the smallest is how far one has to move to stop."""
    return (min(a[1], b[1]) - max(a[0], b[0]),
            min(a[3], b[3]) - max(a[2], b[2]),
            min(a[5], b[5]) - max(a[4], b[4]))


def gap(a, b) -> float:
    """0.0 when the boxes intersect, else their 3-D separation."""
    dx = max(a[0] - b[1], b[0] - a[1], 0.0)
    dy = max(a[2] - b[3], b[2] - a[3], 0.0)
    dz = max(a[4] - b[5], b[4] - a[5], 0.0)
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def fixture_box(prefab: str, x: float, y: float, z: float, yaw_deg: float = 0.0,
                geom: base_geometry.Geometry | None = None, pad: float = 0.0):
    """The world AABB of one loose object standing at (x, y, z).

    `y` is the surface it stands on: MEASURED, every ground-resting prefab in
    this corpus has its solid starting within PIVOT_AT_BASE_TOL_M of its own
    pivot (`piece_workbench` +0.025, `forge` +0.035, `hearth` -0.017,
    `piece_chest_wood` +0.0003, `smelter` 0.000, `charcoal_kiln` -0.023,
    `portal_wood` -0.007), which is why `stock.py` spawns at the pad height and
    the props sit right.
    """
    g = geom or base_geometry.geometry()
    solids = g._prefabs.get(prefab)
    if not solids:
        # An unmeasured prefab gets a deliberately SMALL box rather than none:
        # a point cannot be proven clear, so the caller sees it in `unmeasured`
        # and does not get a silent pass.
        return (x - 0.25 - pad, x + 0.25 + pad, y, y + 1.0,
                z - 0.25 - pad, z + 0.25 + pad)
    half = math.radians(yaw_deg) * 0.5
    q = (0.0, math.sin(half), 0.0, math.cos(half))
    pts = [base_geometry._qrot(q, (cx, cy, cz))
           for s in solids for cx, cy, cz in base_geometry._solid_corners(s)]
    return (x + min(p[0] for p in pts) - pad, x + max(p[0] for p in pts) + pad,
            y + min(p[1] for p in pts), y + max(p[1] for p in pts),
            z + min(p[2] for p in pts) - pad, z + max(p[2] for p in pts) + pad)


def place_body(objects, at: tuple[float, float, float], yaw_deg: float = 0.0,
               base_y: float | None = None,
               geom: base_geometry.Geometry | None = None) -> PlacedBody:
    """Every solid of `objects` in world coordinates, placed the way
    `to_rcon_plan.py --align floor-center` places them.

    `at` is (pad centre x, PAD HEIGHT, pad centre z). `base_y` overrides the
    measured datum, mirroring `to_rcon_plan.py --base-y`.
    """
    g = geom or base_geometry.geometry()
    objects = list(objects)
    datum = base_geometry.floor_datum(objects, g)
    foot = base_geometry.xz_footprint(objects, yaw_deg, g)
    chosen = datum.base_y if base_y is None else base_y
    if chosen is None:
        raise ValueError(f"unplaceable body: {datum.violations}")
    ox, oy, oz = at
    dx, dz = -foot.center_x, -foot.center_z
    dy = -chosen
    half = math.radians(yaw_deg) * 0.5
    sin_h, cos_h = math.sin(half), math.cos(half)
    yq = (0.0, sin_h, 0.0, cos_h)

    solids: list[Solid] = []
    carried: dict[str, int] = {}
    pivot_only: list[str] = []
    for o in objects:
        if is_fixture(o.prefab):
            carried[o.prefab] = carried.get(o.prefab, 0) + 1
        prefab_solids = g._prefabs.get(o.prefab)
        # yaw the pivot, exactly as the emitter does
        px = base_geometry._qrot(yq, o.pos)
        wx, wy, wz = px[0] + dx + ox, px[1] + dy + oy, px[2] + dz + oz
        if not prefab_solids:
            pivot_only.append(o.prefab)
            continue
        rot = base_geometry._normalised(tuple(o.rot))
        # the emitter multiplies the yaw onto each row's own rotation
        rx, ry, rz, rw = rot
        ax, ay, az, aw = yq
        row_rot = (
            aw * rx + ax * rw + ay * rz - az * ry,
            aw * ry - ax * rz + ay * rw + az * rx,
            aw * rz + ax * ry - ay * rx + az * rw,
            aw * rw - ax * rx - ay * ry - az * rz,
        )
        sx, sy, sz = o.scale
        pts = [base_geometry._qrot(row_rot, (cx * sx, cy * sy, cz * sz))
               for s in prefab_solids for cx, cy, cz in base_geometry._solid_corners(s)]
        solids.append((
            wx + min(p[0] for p in pts), wx + max(p[0] for p in pts),
            wy + min(p[1] for p in pts), wy + max(p[1] for p in pts),
            wz + min(p[2] for p in pts), wz + max(p[2] for p in pts),
            o.prefab,
        ))
    return PlacedBody(solids=solids, carried=carried, datum=datum, footprint=foot,
                      origin=(dx + ox, dy + oy, dz + oz), pivot_only=pivot_only)


# How deep a box has to reach into another before it counts as INSIDE it.
#
# Not a fudge factor: a correctly seated prop genuinely overlaps the surface it
# stands on, because its solid does not start exactly at its pivot. MEASURED
# over the ground-resting prefabs this pipeline places, solid start relative to
# pivot: `charcoal_kiln` -0.023, `hearth` -0.017, `portal_wood` -0.007,
# `piece_chest_wood` +0.0003, `smelter` 0.000, `piece_workbench` +0.025,
# `forge` +0.035. So a kiln standing on a floor is 0.023 m into it and that is
# the prop being right. 0.05 m clears the worst of those with margin and is 20x
# smaller than the defect being caught -- the portal in the wall was 1.00 m in.
PIERCE_TOL_M = 0.05


def pierced(box, index: SolidIndex, tol: float = PIERCE_TOL_M) -> list[dict]:
    """Every body solid this fixture box reaches INTO, worst first, with the
    measured overlap. The wording is the wording a build failure should carry:
    what, in what, by how much."""
    hits = []
    for s in index.near(box):
        ox, oy, oz = overlap(box, s)
        if ox > tol and oy > tol and oz > tol:
            hits.append({
                "prefab": s[6],
                "overlap_m": [round(ox, 3), round(oy, 3), round(oz, 3)],
                "penetration_m": round(min(ox, oy, oz), 3),
            })
    hits.sort(key=lambda h: -h["penetration_m"])
    return hits


def clearance(box, index: SolidIndex, search_m: float = 6.0) -> float:
    """Distance from this fixture box to the nearest body solid; 0.0 if it
    intersects one. Bounded by `search_m` so it stays a local query."""
    probe = (box[0] - search_m, box[1] + search_m, box[2] - search_m,
             box[3] + search_m, box[4] - search_m, box[5] + search_m)
    best = search_m
    for s in index.near(probe):
        g = gap(box, s)
        if g < best:
            best = g
        if best == 0.0:
            break
    return best


def free_spot(prefab: str, body: PlacedBody, index: SolidIndex,
              centre: tuple[float, float], pad_y: float, pad_half: float,
              *, prefer: tuple[float, float] | None = None, yaw_deg: float = 0.0,
              margin_m: float = 0.5, step_m: float = 1.0, headroom_m: float = 2.0,
              taken: list | None = None,
              geom: base_geometry.Geometry | None = None) -> dict | None:
    """The legal position for a loose object nearest to `prefer`.

    Legal means: inside the pad, the prefab's own solid clears every body solid
    by `margin_m` horizontally, nothing sits within `headroom_m` above it, and
    it does not collide with anything already placed this run (`taken`, a list
    of boxes the caller appends to). The search is a grid because the free space
    on a pad carrying a 66 m body is courtyards and a perimeter ring, and those
    are found by looking rather than by arithmetic.

    `headroom_m` defaults to 2.0 -- standing room. MEASURED on
    `pre-bonemass/iron-era-workshop`: of the 5,041 one-metre cells of its 70 m
    pad, 3,580 are clear for a `bed` at pad level and 3,544 still clear with 2 m
    of headroom, so insisting on headroom costs 36 cells and buys not tucking a
    bed under a floor slab. The nearest legal cell to the pad centre is 8.1 m
    out, which is the building standing where it should.

    Returns the position and the MEASURED clearance it achieved, or None -- and
    None is a real answer the caller must report, not paper over.
    """
    g = geom or base_geometry.geometry()
    cx, cz = centre
    px, pz = prefer if prefer is not None else centre
    steps = int(pad_half / step_m)
    best = None
    for ix in range(-steps, steps + 1):
        for iz in range(-steps, steps + 1):
            x = cx + ix * step_m
            z = cz + iz * step_m
            d = math.hypot(x - px, z - pz)
            if best is not None and d >= best["from_prefer_m"]:
                continue
            box = fixture_box(prefab, x, pad_y, z, yaw_deg, g, pad=margin_m)
            probe = (box[0], box[1], box[2], max(box[3], pad_y + headroom_m),
                     box[4], box[5])
            if pierced(probe, index):
                continue
            if taken and any(all(o > 1e-6 for o in overlap(box, t)) for t in taken):
                continue
            tight = fixture_box(prefab, x, pad_y, z, yaw_deg, g)
            best = {
                "prefab": prefab,
                "x": round(x, 3), "y": round(pad_y, 3), "z": round(z, 3),
                "yaw": yaw_deg,
                "offset": [round(x - cx, 3), round(z - cz, 3)],
                "from_prefer_m": round(d, 3),
                "clearance_m": round(clearance(tight, index), 3),
                "box": list(fixture_box(prefab, x, pad_y, z, yaw_deg, g,
                                        pad=margin_m)),
            }
    return best


# Wall-role solids, for the clearance figure the operator actually asked about.
# A prop TOUCHING a floor is a prop standing on it; a prop touching a wall is
# the report that started this: "there are portals in the middle of walls".
_WALL_NAME = re.compile(r"(?:^|_)(?:wall|arch|pillar|door|gate|woodwall)",
                        re.IGNORECASE)


def _min_wall_clearance(rows, fixture_rows, body: PlacedBody, index: SolidIndex,
                        geom: base_geometry.Geometry) -> float | None:
    walls = [s for s in body.solids if _WALL_NAME.search(s[6])]
    if not walls:
        return None
    best = math.inf
    for prefab, x, y, z, yaw in fixture_rows:
        box = fixture_box(prefab, x, y, z, yaw, geom)
        for s in walls:
            g = gap(box, s)
            if g < best:
                best = g
    return None if best is math.inf else round(best, 3)


def audit(fixtures, body: PlacedBody, index: SolidIndex | None = None,
          geom: base_geometry.Geometry | None = None) -> dict:
    """Does any of these fixtures stand inside the building?

    `fixtures` is an iterable of (prefab, x, y, z, yaw). The report is shaped to
    be a build failure message: `failures` is empty or the build is wrong.
    """
    g = geom or base_geometry.geometry()
    idx = index or body.index()
    rows, failures, unmeasured = [], [], []
    worst = math.inf
    for prefab, x, y, z, yaw in fixtures:
        if not g.has_solid(prefab):
            # A prefab with no measured collider cannot be PROVEN clear, so it
            # is named rather than passed. Its box is a deliberate
            # under-statement, which is why it is not allowed to stand in for
            # a real one silently.
            unmeasured.append(prefab)
        box = fixture_box(prefab, x, y, z, yaw, g)
        hits = pierced(box, idx)
        clear = 0.0 if hits else clearance(box, idx)
        worst = min(worst, clear)
        rows.append({"prefab": prefab, "at": [round(x, 2), round(y, 2), round(z, 2)],
                     "pierces": len(hits), "worst": hits[0] if hits else None,
                     "clearance_m": round(clear, 3)})
        if hits:
            h = hits[0]
            failures.append(
                f"{prefab} at ({x:.2f}, {y:.2f}, {z:.2f}) overlaps {h['prefab']} by "
                f"{h['overlap_m'][0]:.2f} x {h['overlap_m'][1]:.2f} x "
                f"{h['overlap_m'][2]:.2f} m"
                + (f" (and {len(hits) - 1} more solid(s))" if len(hits) > 1 else ""))
    return {
        "fixtures": rows,
        "failures": failures,
        "unmeasured_prefabs": sorted(set(unmeasured)),
        "min_clearance_m": None if worst is math.inf else round(worst, 3),
        "min_wall_clearance_m": _min_wall_clearance(rows, fixtures, body, idx, g),
    }
