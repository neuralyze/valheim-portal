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

THE SECOND BUG THIS EXISTS FOR
------------------------------
Reported from live play on Ulfsland on 2026-09-15, after the placement above was
fixed: "i noticed that there were hearth and portal and some things outside the
building... is this intentional?"  MEASURED live, all at pad height, against a
body whose walls end ~12 m from the pad centre: `portal_wood` 15.0 m out,
`hearth` 15.8, `fire_pit` 12.6, both `bed`s 12.8 and 12.6, `piece_maypole`
13.0. Every one of them LEGAL and every one of them in the rain.

The cause is that `free_spot()` was asked one question -- "does this box clear
every body solid?" -- and answered it perfectly. Nothing asked whether the spot
was a place a player would put a bed. So this module now computes INTERIOR
SPACE (`interior()`): which cells of the pad are indoors, covered, and
reachable from outside, at 1 m resolution, from the body's own geometry. Each
fixture then declares a PREFERENCE (`PLACEMENT_PREFERENCE`) and a preference
that cannot be satisfied is reported LOUDLY rather than silently satisfied in
the yard, because the silent fallback IS the defect.

THE THIRD BUG, AND THE ONE THAT MADE THE MASK POSSIBLE
------------------------------------------------------
A placed object was represented by one world AABB. MEASURED on
`halvar-master-refinery`: its `stone_wall_4x2` pieces are laid diagonally, and a
4.0 x 1.0 m wall turned 59 degrees has a 2.45 x 4.08 m AABB -- so the AABB claims
10 m2 of floor for a wall that occupies 4. Rastering the body at 1 m with AABBs
marks 360 of 1,443 cells blocked; with the pieces' OWN oriented collider boxes,
190. The interior of that building is unreachable arithmetic under AABBs: NOTHING
fits in it, not a bed, not a stool. So `Solid` now carries its oriented boxes and
the collision test is exact (separating-axis) with the AABB kept as the
broad-phase filter. This can only ever make the guard TIGHTER, never looser: an
AABB contains the true shape, so every intersection it used to find that is real
is still found, and the ones it invented are gone.

AND THE FOURTH: STANDING ON A FLOOR IS NOT COLLIDING WITH IT
------------------------------------------------------------
MEASURED: `fire_pit`'s collider starts 0.73 m BELOW its own pivot -- it is a pit,
modelled sunk into the ground. Dropped on bare pad that is invisible; dropped on
a `stone_floor_2x2` it overlaps the slab by 0.73 m, so under the old rule a fire
pit could never legally stand on a floor at all, only on dirt. That is why every
hearth and fire pit ended up in the yard. `pierced()` therefore classifies an
overlap with a FLOOR-role solid whose top is at or below the standing plane as
SUPPORT, and reports it as `rests_on` rather than as a collision. The portal in
the wall is untouched by this: its overlap reached 1.18 m ABOVE the floor.

"""

from __future__ import annotations

import math
import re
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

HERE = Path(__file__).resolve().parent
import sys

sys.path.insert(0, str(HERE))

import base_geometry  # noqa: E402
import data_entry  # noqa: E402  (the ZDO per-piece payload codec)

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


# One oriented collider box of a placed piece, in WORLD space: centre, the three
# unit axes of the piece's own frame, and the half-extents along those axes.
OBB = tuple[
    tuple[float, float, float],
    tuple[tuple[float, float, float], tuple[float, float, float],
          tuple[float, float, float]],
    tuple[float, float, float],
]


class Solid(NamedTuple):
    """One placed object: its world AABB, its prefab, and its ORIENTED boxes.

    Fields 0..5 are the AABB and 6 is the prefab, which is why this is a
    NamedTuple and not a dataclass: it is drop-in wherever the old
    `(x0, x1, y0, y1, z0, z1, prefab)` tuple was, so `overlap()`, `gap()` and
    every caller that reads `s[6]` are untouched.

    `obbs` is the exact geometry. The AABB survives as the broad-phase filter,
    because an AABB test is three comparisons and a separating-axis test is
    fifteen projections, and 1,900 pieces times a few thousand candidate cells
    is a real number of tests.
    """

    x0: float
    x1: float
    y0: float
    y1: float
    z0: float
    z1: float
    prefab: str
    obbs: tuple[OBB, ...] = ()


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
    # The pad this body was placed on: (centre x, centre z) and the pad height.
    # Stored rather than passed around, because every question about INTERIOR
    # space is asked in pad coordinates and an optional argument that defaults
    # to a guess is how a check ends up confidently answering the wrong
    # question. `audit()` can therefore always compute the interior mask.
    centre: tuple[float, float] = (0.0, 0.0)
    pad_y: float = 0.0
    _interior: "InteriorMask | None" = field(default=None, repr=False)

    def index(self, cell_m: float = 4.0) -> "SolidIndex":
        return SolidIndex(self.solids, cell_m)

    def interior(self, geom: base_geometry.Geometry | None = None) -> "InteriorMask":
        """The occupancy mask, computed once per body and cached."""
        if self._interior is None:
            self._interior = interior(self, geom=geom)
        return self._interior


class SolidIndex:
    """XZ hash over the body's solids. 1,906 solids times a few thousand
    candidate positions is 10^7 box tests done naively, and the search below is
    run per fixture per preset in a test, so the buckets are not premature."""

    def __init__(self, solids: list[Solid], cell_m: float = 4.0):
        self.cell_m = cell_m
        self.buckets: dict[tuple[int, int], list[Solid]] = {}
        # near() is answered per BUCKET RECTANGLE, and the same rectangle comes
        # back over and over: the search walks a 1 m lattice through 4 m buckets
        # and tries four yaws at each cell, so consecutive queries land on the
        # same handful of buckets. MEASURED without this cache, on the 915-piece
        # `halvar-master-refinery`: one outdoor `free_spot` that has to walk 11 m
        # out from the pad centre spends seconds rebuilding the same candidate
        # list, and a 20-fixture plan does not finish in a minute.
        self._near: dict[tuple[int, int, int, int], list[Solid]] = {}
        for s in solids:
            for ix in range(math.floor(s[0] / cell_m), math.floor(s[1] / cell_m) + 1):
                for iz in range(math.floor(s[4] / cell_m), math.floor(s[5] / cell_m) + 1):
                    self.buckets.setdefault((ix, iz), []).append(s)

    def near(self, box: tuple[float, float, float, float, float, float]) -> list[Solid]:
        key = (math.floor(box[0] / self.cell_m), math.floor(box[1] / self.cell_m),
               math.floor(box[4] / self.cell_m), math.floor(box[5] / self.cell_m))
        hit = self._near.get(key)
        if hit is not None:
            return hit
        out: dict[int, Solid] = {}
        for ix in range(key[0], key[1] + 1):
            for iz in range(key[2], key[3] + 1):
                for s in self.buckets.get((ix, iz), ()):
                    out[id(s)] = s
        found = list(out.values())
        self._near[key] = found
        return found


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
        # ...and the same solids as ORIENTED boxes, which is the exact shape.
        # A stored solid is either an axis-aligned prefab-local box (`"b"`) or
        # eight explicit corners of a collider that was already rotated inside
        # the prefab (`"p"`). For the first the local min/max IS the box and the
        # oriented result is exact; for the second the local min/max is its
        # local-frame AABB, which is conservative in the same direction the
        # world AABB was -- it can over-state, never under-state. MEASURED on
        # `halvar-master-refinery`: 980 of its 1,364 stored solids are `"b"`.
        axes = (base_geometry._qrot(row_rot, (1.0, 0.0, 0.0)),
                base_geometry._qrot(row_rot, (0.0, 1.0, 0.0)),
                base_geometry._qrot(row_rot, (0.0, 0.0, 1.0)))
        obbs: list[OBB] = []
        for s in prefab_solids:
            corners = base_geometry._solid_corners(s)
            lo = [min(c[k] for c in corners) * (sx, sy, sz)[k] for k in range(3)]
            hi = [max(c[k] for c in corners) * (sx, sy, sz)[k] for k in range(3)]
            local_c = tuple((lo[k] + hi[k]) * 0.5 for k in range(3))
            ext = tuple(abs(hi[k] - lo[k]) * 0.5 for k in range(3))
            c = base_geometry._qrot(row_rot, local_c)
            obbs.append(((wx + c[0], wy + c[1], wz + c[2]), axes, ext))
        solids.append(Solid(
            wx + min(p[0] for p in pts), wx + max(p[0] for p in pts),
            wy + min(p[1] for p in pts), wy + max(p[1] for p in pts),
            wz + min(p[2] for p in pts), wz + max(p[2] for p in pts),
            o.prefab, tuple(obbs),
        ))
    return PlacedBody(solids=solids, carried=carried, datum=datum, footprint=foot,
                      origin=(dx + ox, dy + oy, dz + oz), pivot_only=pivot_only,
                      centre=(ox, oz), pad_y=oy)


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


# The separating-axis test between the fixture's world AABB and one of a piece's
# ORIENTED boxes. Fifteen axes: the three world axes, the box's own three, and
# the nine cross products. For boxes that is complete -- if all fifteen overlap,
# the boxes intersect, and the smallest overlap is the penetration depth.
#
# The disjoint branch returns the largest per-axis separation, which is a LOWER
# BOUND on the true distance and not the distance itself: the closest-point
# direction of two boxes can be an edge-edge direction that is not one of the
# fifteen. Under-stating a clearance is the safe direction for a guard, and it
# is stated here rather than rounded off, because a number that claims to be a
# distance and is not is exactly the class of bug this pipeline keeps finding.
_WORLD_AXES = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def _sat(box, obb: OBB) -> tuple[float, float]:
    """(penetration, separation) between a world AABB and an oriented box.

    Exactly one of the two is non-zero: they intersect or they do not.
    """
    ca = ((box[0] + box[1]) * 0.5, (box[2] + box[3]) * 0.5, (box[4] + box[5]) * 0.5)
    ea = ((box[1] - box[0]) * 0.5, (box[3] - box[2]) * 0.5, (box[5] - box[4]) * 0.5)
    cb, axb, eb = obb
    d = (cb[0] - ca[0], cb[1] - ca[1], cb[2] - ca[2])
    axes = list(_WORLD_AXES) + list(axb)
    for u in _WORLD_AXES:
        for v in axb:
            c = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2],
                 u[0] * v[1] - u[1] * v[0])
            n = math.sqrt(c[0] * c[0] + c[1] * c[1] + c[2] * c[2])
            if n > 1e-6:
                axes.append((c[0] / n, c[1] / n, c[2] / n))
    pen = math.inf
    sep = 0.0
    for L in axes:
        ra = abs(L[0]) * ea[0] + abs(L[1]) * ea[1] + abs(L[2]) * ea[2]
        rb = 0.0
        for j in range(3):
            rb += abs(L[0] * axb[j][0] + L[1] * axb[j][1] + L[2] * axb[j][2]) * eb[j]
        dist = abs(L[0] * d[0] + L[1] * d[1] + L[2] * d[2])
        o = ra + rb - dist
        if o <= 0.0:
            if -o > sep:
                sep = -o
        elif o < pen:
            pen = o
    if sep > 0.0:
        return 0.0, sep
    return (0.0 if pen is math.inf else pen), 0.0


def _boxes(s) -> tuple[OBB, ...]:
    """A solid's oriented boxes, or its own AABB as a single box.

    The fallback exists for a hand-built `(x0, x1, y0, y1, z0, z1, prefab)`
    tuple -- the shape the geometry unit tests use -- and it is the conservative
    reading, not a silent skip.
    """
    obbs = getattr(s, "obbs", ())
    if obbs:
        return obbs
    return ((((s[0] + s[1]) * 0.5, (s[2] + s[3]) * 0.5, (s[4] + s[5]) * 0.5),
             _WORLD_AXES,
             ((s[1] - s[0]) * 0.5, (s[3] - s[2]) * 0.5, (s[5] - s[4]) * 0.5)),)


def probe(box, index: SolidIndex, tol: float = PIERCE_TOL_M,
          stand_y: float | None = None,
          geom: base_geometry.Geometry | None = None) -> tuple[list[dict], list[dict]]:
    """(collisions, supports) for this fixture box against the body.

    A SUPPORT is an overlap with a FLOOR-role solid whose overlap does not reach
    above the plane the fixture stands on. That is a prop resting on a slab, and
    it is the difference between a fire pit standing on the floor of a house and
    a fire pit embedded in a wall: MEASURED, `fire_pit`'s collider starts 0.73 m
    below its own pivot, so on any real floor it overlaps the slab by 0.73 m and
    the old rule called that a collision -- which is why every hearth and fire
    pit this pipeline placed ended up on bare pad in the yard.

    Pass `stand_y` to get that classification; without it every overlap is a
    collision, which is the old behaviour and is still the right answer when the
    caller does not know what the fixture is standing on.
    """
    g = geom or base_geometry.geometry()
    hits: list[dict] = []
    supports: list[dict] = []
    for s in index.near(box):
        ox, oy, oz = overlap(box, s)
        if not (ox > tol and oy > tol and oz > tol):
            continue                      # broad phase: AABBs already disjoint
        worst = 0.0
        for obb in _boxes(s):
            pen, _sepn = _sat(box, obb)
            if pen > worst:
                worst = pen
        if worst <= tol:
            continue                      # the AABBs met; the shapes did not
        row = {
            "prefab": s[6],
            "overlap_m": [round(ox, 3), round(oy, 3), round(oz, 3)],
            "penetration_m": round(worst, 3),
        }
        # The slab you stand on has its TOP at your feet. Stated as the solid's
        # own top rather than as the overlap's top so that `clearance()` can
        # apply the identical rule -- MEASURED consequence of the two differing:
        # a spot reported clear by `pierced` came back with clearance 0.000 m,
        # which is a report contradicting itself.
        if (stand_y is not None and g.is_floor(s[6])
                and s[3] <= stand_y + tol):
            row["role"] = "support"
            supports.append(row)
        else:
            hits.append(row)
    hits.sort(key=lambda h: -h["penetration_m"])
    supports.sort(key=lambda h: -h["penetration_m"])
    return hits, supports


def pierced(box, index: SolidIndex, tol: float = PIERCE_TOL_M,
            stand_y: float | None = None,
            geom: base_geometry.Geometry | None = None) -> list[dict]:
    """Every body solid this fixture box reaches INTO, worst first, with the
    measured overlap. The wording is the wording a build failure should carry:
    what, in what, by how much."""
    return probe(box, index, tol, stand_y, geom)[0]


def clearance(box, index: SolidIndex, search_m: float = 6.0,
              stand_y: float | None = None,
              geom: base_geometry.Geometry | None = None) -> float:
    """Distance from this fixture box to the nearest body solid; 0.0 if it
    intersects one. Bounded by `search_m` so it stays a local query.

    The floor a fixture STANDS ON is excluded when `stand_y` says which plane
    that is -- otherwise every indoor clearance reads 0.000 m and the figure
    stops meaning anything.
    """
    g = geom or base_geometry.geometry()
    probe_box = (box[0] - search_m, box[1] + search_m, box[2] - search_m,
                 box[3] + search_m, box[4] - search_m, box[5] + search_m)
    best = search_m
    for s in index.near(probe_box):
        if (stand_y is not None and g.is_floor(s[6])
                and s[3] <= stand_y + PIERCE_TOL_M):
            continue
        if gap(box, s) >= best:
            continue                      # broad phase
        for obb in _boxes(s):
            pen, sep = _sat(box, obb)
            d = 0.0 if pen > 0.0 else sep
            if d < best:
                best = d
        if best == 0.0:
            break
    return best


# ---------------------------------------------------------------------------
# interior space -- "is this spot INSIDE the building, and can I get to it?"
# ---------------------------------------------------------------------------

# The interior raster. 1 m, and the resolution is a decision with two reasons.
#
# `base_geometry.base_profile()` rasters at 2 m because it measures RELIEF and
# 2 m is the module size of the pieces (`stone_floor_2x2`, `stone_wall_2x1`), so
# a finer grid reports sub-piece sampling noise as terracing. That argument does
# not transfer: a 2 m cell cannot represent a 1 m doorway or a 1 m gap between
# two smelters, and both of those decide whether a room is enterable.
#
# 1 m is also exactly `free_spot`'s search step, on the same lattice measured
# from the pad centre. That matters more than it sounds: a mask at any other
# pitch would classify cells the search cannot choose and vice versa, so the
# report and the placement would be describing different grids.
INTERIOR_CELL_M = 1.0

# How far past the body's own extents the raster reaches. The mask needs a rim
# of definitely-outdoor cells to flood-fill inwards FROM, and 6 m is three
# building modules -- wider than any eave in the corpus, so the rim is never
# accidentally under a roof.
INTERIOR_RIM_M = 6.0

# Where a cell is sampled for standing room, as heights above the pad and as a
# horizontal offset from the cell centre. Three heights because a single one
# lies: sample only at 1.0 m and a cell whose floor is blocked by a 0.5 m bench
# reads clear; sample only at 0.3 m and a cell crossed by a beam at head height
# reads clear. The 0.22 m horizontal probe is a player-sized footprint rather
# than a point -- MEASURED consequence of dropping it: a point test admits the
# 1 m gaps between `halvar-master-refinery`'s smelters as standing room.
STAND_HEIGHTS_M = (0.3, 1.0, 1.7)
STAND_PROBE_M = 0.22

# How far ABOVE the pad the interior's own floor may sit and still be the plane
# a pad-level fixture stands on, and how far below.
#
# THE DEFECT THIS EXISTS FOR, MEASURED: `hs_mistlands_thrad_workshop` at its
# solved pad (y 65.53, datum base_y 1.192) has its `stone_floor_2x2` pieces
# spanning pad-0.37 to pad+0.63 -- its interior floor SURFACE is 0.63 m above
# the pad, because the datum rested the body on a lower level. Sampling standing
# room in a fixed band above the PAD put every sample point inside that slab, so
# all 198 interior cells read BLOCKED and the mask reported 0.0 m2 of interior in
# a 493-piece workshop whose 77 roof pieces it had correctly found overhead. A
# mask that confidently answers "no interior" while measuring the wrong plane is
# the exact failure class this pipeline keeps producing.
#
# 1.0 m is one wall course, and it is the SAME bound `base_geometry`'s
# `MAJOR_LEVEL_CLIMB_M` already uses to decide whether two floor levels can be
# separate storeys: below a course there is no room for a storey, so a floor
# within a course of the pad is this storey's floor. Above that it is an upper
# level, and a fixture spawned at pad height does not belong on it.
FLOOR_STEP_UP_M = 1.0
FLOOR_STEP_DOWN_M = 0.5

# Prefabs a player can WALK THROUGH or WALK UP even though they have a
# collider. A door is not a wall: a house whose only entrance is a closed door
# is not a sealed cellar, and treating it as one would make the reachability
# test condemn most real buildings. Nor is a staircase: MEASURED on
# `hs_mistlands_thrad_workshop`, its entrance is two `stone_stair` pieces
# spanning pad-0.37 to pad+0.63 up to a pair of `darkwood_gate`, and a stair's
# collider fills the standing band of the cell it is in -- so reading it as a
# blocker sealed the only way in and condemned the whole 60-cell interior as
# UNREACHABLE. Kept deliberately narrow: these are the pieces whose PURPOSE is
# to be traversed.
_PASSABLE_NAME = re.compile(
    r"(?:^|_)(?:door|gate|hatch|arch|archway|stair|stairs|step|steps|"
    r"stepladder|ladder|ramp)", re.IGNORECASE)

OUTDOOR = "outdoor"
INDOOR_COVERED = "indoor_covered"
INDOOR_UNCOVERED = "indoor_uncovered"
COVERED_UNENCLOSED = "covered_unenclosed"
UNREACHABLE = "unreachable"
BLOCKED = "blocked"


def _ray_vertical(obb: OBB, pt: tuple[float, float, float],
                  sign: float = 1.0) -> float | None:
    """Where a vertical ray from `pt` first enters this oriented box, as a
    distance, or None if it never does. Slab test in the box's own frame.

    `sign` is +1 for up (what is over my head?) and -1 for down (what am I
    standing on?).
    """
    cb, axb, eb = obb
    d = (pt[0] - cb[0], pt[1] - cb[1], pt[2] - cb[2])
    lo, hi = -math.inf, math.inf
    for j in range(3):
        a = axb[j]
        o = d[0] * a[0] + d[1] * a[1] + d[2] * a[2]
        dirn = a[1] * sign                # the vertical world ray, in box coords
        if abs(dirn) < 1e-9:
            if o < -eb[j] or o > eb[j]:
                return None
            continue
        t1 = (-eb[j] - o) / dirn
        t2 = (eb[j] - o) / dirn
        if t1 > t2:
            t1, t2 = t2, t1
        lo = max(lo, t1)
        hi = min(hi, t2)
    if hi < max(lo, 0.0):
        return None
    return max(lo, 0.0)


def _ray_up(obb: OBB, pt: tuple[float, float, float]) -> float | None:
    return _ray_vertical(obb, pt, 1.0)


@dataclass
class InteriorMask:
    """Which cells of the pad are indoors, covered, and reachable.

    Everything is keyed by (ix, iz), the cell index from the pad centre, so a
    cell's world position is `centre + index * cell_m` -- the same arithmetic
    `free_spot` walks.
    """

    cell_m: float
    centre: tuple[float, float]
    pad_y: float
    headroom_m: float
    bounds: tuple[int, int, int, int]          # ix0, ix1, iz0, iz1
    klass: dict[tuple[int, int], str] = field(default_factory=dict)
    cover_m: dict[tuple[int, int], float] = field(default_factory=dict)
    # The world Y a fixture in this cell STANDS ON. `pad_y` for open ground;
    # the top of the building's own floor slab where there is one within a wall
    # course of the pad. See FLOOR_STEP_UP_M for the body that forced this.
    floor_y: dict[tuple[int, int], float] = field(default_factory=dict)

    def stand_y(self, ix: int, iz: int) -> float:
        return self.floor_y.get((ix, iz), self.pad_y)

    def world(self, ix: int, iz: int) -> tuple[float, float]:
        return (self.centre[0] + ix * self.cell_m, self.centre[1] + iz * self.cell_m)

    def cell(self, x: float, z: float) -> tuple[int, int]:
        return (int(round((x - self.centre[0]) / self.cell_m)),
                int(round((z - self.centre[1]) / self.cell_m)))

    def at(self, x: float, z: float) -> str:
        """The class of the cell containing this world point. Anything off the
        raster is OUTDOOR: the raster covers the body plus a 6 m rim, so off it
        means well clear of the building."""
        return self.klass.get(self.cell(x, z), OUTDOOR)

    def cells_of(self, box) -> list[tuple[int, int]]:
        """Every raster cell whose centre lies under this world box's XZ."""
        ix0, ix1 = self.cell(box[0], box[4])[0], self.cell(box[1], box[5])[0]
        iz0, iz1 = self.cell(box[0], box[4])[1], self.cell(box[1], box[5])[1]
        out = []
        for ix in range(ix0 - 1, ix1 + 2):
            for iz in range(iz0 - 1, iz1 + 2):
                x, z = self.world(ix, iz)
                if box[0] <= x <= box[1] and box[4] <= z <= box[5]:
                    out.append((ix, iz))
        return out

    def of_class(self, *classes: str) -> list[tuple[int, int]]:
        return [c for c, k in self.klass.items() if k in classes]

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for k in self.klass.values():
            out[k] = out.get(k, 0) + 1
        return out

    def summary(self) -> dict:
        ind = self.of_class(INDOOR_COVERED)
        heights = sorted(round(self.cover_m[c], 2) for c in ind if c in self.cover_m)
        steps = sorted(round(self.floor_y[c] - self.pad_y, 2) for c in ind
                       if c in self.floor_y)
        return {
            "cell_m": self.cell_m,
            "cells": len(self.klass),
            "counts": self.counts(),
            "indoor_covered_area_m2": round(len(ind) * self.cell_m ** 2, 1),
            "cover_height_m": {"min": heights[0], "max": heights[-1]} if heights else None,
            # How far the interior floor sits above the pad. Reported because a
            # body whose floor is a step up is the body that broke the mask, and
            # a figure nobody prints is a figure nobody checks.
            "interior_floor_above_pad_m": ({"min": steps[0], "max": steps[-1]}
                                           if steps else None),
        }

    def render(self) -> str:
        """The mask as text, north up. Cheap to print and the only way a human
        checks a mask: `#` blocked, `?` reachable-by-nobody, `I` indoor covered,
        `y` indoor uncovered (walled yard), `c` covered but not enclosed
        (an eave or porch), `.` outdoor."""
        glyph = {BLOCKED: "#", UNREACHABLE: "?", INDOOR_COVERED: "I",
                 INDOOR_UNCOVERED: "y", COVERED_UNENCLOSED: "c", OUTDOOR: "."}
        ix0, ix1, iz0, iz1 = self.bounds
        rows = ["     " + "".join(str(abs(ix) % 10) for ix in range(ix0, ix1 + 1))]
        for iz in range(iz1, iz0 - 1, -1):
            rows.append(f"{iz:+4d} " + "".join(
                glyph.get(self.klass.get((ix, iz), OUTDOOR), ".")
                for ix in range(ix0, ix1 + 1)))
        return "\n".join(rows)


def interior(body: PlacedBody, geom: base_geometry.Geometry | None = None,
             cell_m: float = INTERIOR_CELL_M, headroom_m: float = 2.0,
             index: SolidIndex | None = None) -> InteriorMask:
    """Classify every cell of the pad around this body.

    A cell is BLOCKED when a piece occupies the standing volume above it.
    Of the cells that are not, one is UNREACHABLE when no path of free cells
    connects it to the rim of the raster -- a sealed cellar, or the 1 m slot
    between two smelters, and MEASURED on `halvar-master-refinery` there are 7
    such cells inside it. A fixture in one is as useless as a fixture in the
    rain, which is the whole reason reachability is computed and not assumed.

    The rest are classified by two independent questions:
      COVERED   -- is there a piece of this building directly overhead, at least
                   `headroom_m` up? No upper bound: a hall roofed at 9.4 m is
                   roofed. That is measured per cell and reported, not thresholded.
      ENCLOSED  -- walking away along each of the four cardinal axes, is a piece
                   met before leaving the raster, in all four directions?
    Covered and enclosed is INDOOR_COVERED, which is where a bed goes. Enclosed
    and open to the sky is INDOOR_UNCOVERED, a walled yard. Covered and not
    enclosed is COVERED_UNENCLOSED -- an eave or a porch, which is exactly what
    `halvar-master-refinery`'s roof overhang produces and is NOT the same thing
    as being indoors.
    """
    g = geom or base_geometry.geometry()
    idx = index or body.index()
    cx, cz = body.centre
    pad_y = body.pad_y
    if not body.solids:
        return InteriorMask(cell_m=cell_m, centre=(cx, cz), pad_y=pad_y,
                            headroom_m=headroom_m, bounds=(0, 0, 0, 0))
    lo_x = min(s[0] for s in body.solids) - INTERIOR_RIM_M
    hi_x = max(s[1] for s in body.solids) + INTERIOR_RIM_M
    lo_z = min(s[4] for s in body.solids) - INTERIOR_RIM_M
    hi_z = max(s[5] for s in body.solids) + INTERIOR_RIM_M
    ix0 = math.floor((lo_x - cx) / cell_m)
    ix1 = math.ceil((hi_x - cx) / cell_m)
    iz0 = math.floor((lo_z - cz) / cell_m)
    iz1 = math.ceil((hi_z - cz) / cell_m)
    mask = InteriorMask(cell_m=cell_m, centre=(cx, cz), pad_y=pad_y,
                        headroom_m=headroom_m, bounds=(ix0, ix1, iz0, iz1))

    # PASS 1: the standing SURFACE of each cell. Cast a ray straight down from
    # one wall course above the pad and take the first FLOOR-role solid it
    # enters; that entry point is the top of the slab a fixture would stand on.
    #
    # Floor-role only, and that matters: casting at everything finds the top of
    # a workbench or a barrel and calls it the floor. `Geometry.is_floor` is the
    # name-AND-measured-slab-shape test `base_geometry` already uses to pick the
    # datum, so the mask and the datum agree about what a floor is.
    top = pad_y + FLOOR_STEP_UP_M
    for ix in range(ix0, ix1 + 1):
        for iz in range(iz0, iz1 + 1):
            x, z = cx + ix * cell_m, cz + iz * cell_m
            column = (x - 0.01, x + 0.01, pad_y - FLOOR_STEP_DOWN_M, top,
                      z - 0.01, z + 0.01)
            best: float | None = None
            for s in idx.near(column):
                if s[3] < pad_y - FLOOR_STEP_DOWN_M or s[2] > top:
                    continue
                if not g.is_floor(s[6]):
                    continue
                for obb in _boxes(s):
                    t = _ray_vertical(obb, (x, top, z), -1.0)
                    if t is None:
                        continue
                    surface = top - t
                    if surface < pad_y - FLOOR_STEP_DOWN_M:
                        continue
                    if best is None or surface > best:
                        best = surface
            if best is not None and abs(best - pad_y) > 1e-6:
                mask.floor_y[(ix, iz)] = best

    offsets = ((0.0, 0.0), (STAND_PROBE_M, 0.0), (-STAND_PROBE_M, 0.0),
               (0.0, STAND_PROBE_M), (0.0, -STAND_PROBE_M))
    blocked: set[tuple[int, int]] = set()
    passable_blocked: set[tuple[int, int]] = set()
    # PASS 2: standing room and cover, measured from each cell's OWN surface.
    for ix in range(ix0, ix1 + 1):
        for iz in range(iz0, iz1 + 1):
            x, z = cx + ix * cell_m, cz + iz * cell_m
            floor = mask.stand_y(ix, iz)
            cellbox = (x - STAND_PROBE_M, x + STAND_PROBE_M,
                       floor + STAND_HEIGHTS_M[0], floor + STAND_HEIGHTS_M[-1],
                       z - STAND_PROBE_M, z + STAND_PROBE_M)
            near = idx.near(cellbox)
            hard = soft = False
            cover: float | None = None
            for s in near:
                inside = False
                for obb in _boxes(s):
                    for dx, dz in offsets:
                        for h in STAND_HEIGHTS_M:
                            if _obb_contains(obb, (x + dx, floor + h, z + dz)):
                                inside = True
                                break
                        if inside:
                            break
                    if inside:
                        break
                if inside:
                    if _PASSABLE_NAME.search(s[6]):
                        soft = True
                    else:
                        hard = True
                if s[3] <= floor + headroom_m:
                    continue
                for obb in _boxes(s):
                    t = _ray_up(obb, (x, floor + headroom_m, z))
                    if t is not None and (cover is None or t < cover):
                        cover = t
            if cover is not None:
                mask.cover_m[(ix, iz)] = cover + headroom_m
            # A DOOR BEATS ITS OWN FRAME. A cell holding an openable piece is a
            # cell a player walks through, even though the jamb beside it is
            # solid and lands in the same 1 m cell -- the wall around a doorway
            # IS the doorway. Tested the other way round (any solid piece wins)
            # MEASURED on `hs_mistlands_thrad_workshop`: its 2 `darkwood_gate`,
            # 4 `blackmarble_arch`, 2 `stone_arch` and 4 cloth doors all shared
            # their cell with a wall, so every entrance read blocked and the
            # flood fill condemned the entire 60-cell interior as UNREACHABLE.
            # A whole sealed workshop is not a thing this corpus contains; a
            # doorway too narrow for a 1 m raster is.
            if soft:
                passable_blocked.add((ix, iz))
            elif hard:
                blocked.add((ix, iz))

    free = [(ix, iz) for ix in range(ix0, ix1 + 1) for iz in range(iz0, iz1 + 1)
            if (ix, iz) not in blocked]
    freeset = set(free)
    seeds = [c for c in free if c[0] in (ix0, ix1) or c[1] in (iz0, iz1)]
    reached = set(seeds)
    queue = deque(seeds)
    while queue:
        ix, iz = queue.popleft()
        for nxt in ((ix + 1, iz), (ix - 1, iz), (ix, iz + 1), (ix, iz - 1)):
            if nxt in freeset and nxt not in reached:
                reached.add(nxt)
                queue.append(nxt)

    # ENCLOSURE asks a different question from REACHABILITY and needs a
    # different set. A wall with a door in it is still a wall: the door makes
    # the room enterable, not open-plan. So the flood fill above walks THROUGH
    # doorway cells while this march STOPS at them. MEASURED consequence of
    # using one set for both, on `hs_mistlands_thrad_workshop`: with doorway
    # cells excluded from the fabric, the march down the +x axis from every
    # interior cell walked out through the gate and off the raster, so 42 of
    # its 61 interior cells reported COVERED_UNENCLOSED -- a roofed stone
    # workshop classified as a porch.
    fabric = blocked | passable_blocked

    def enclosed(ix: int, iz: int) -> bool:
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            j, k = ix + dx, iz + dz
            met = False
            while ix0 <= j <= ix1 and iz0 <= k <= iz1:
                if (j, k) in fabric:
                    met = True
                    break
                j += dx
                k += dz
            if not met:
                return False
        return True

    for ix in range(ix0, ix1 + 1):
        for iz in range(iz0, iz1 + 1):
            c = (ix, iz)
            if c in blocked:
                mask.klass[c] = BLOCKED
            elif c not in reached:
                mask.klass[c] = UNREACHABLE
            elif c in mask.cover_m:
                mask.klass[c] = INDOOR_COVERED if enclosed(ix, iz) else COVERED_UNENCLOSED
            elif enclosed(ix, iz):
                mask.klass[c] = INDOOR_UNCOVERED
            else:
                mask.klass[c] = OUTDOOR
    # A cell whose only blocker is an openable door is walkable, so it never
    # blocks a path -- but it is still not somewhere a bed may stand, because the
    # door needs the space to swing. It is therefore reachable AND not offered.
    for c in passable_blocked:
        if mask.klass.get(c) != BLOCKED:
            mask.klass[c] = BLOCKED
    return mask


def _obb_contains(obb: OBB, pt: tuple[float, float, float]) -> bool:
    cb, axb, eb = obb
    d = (pt[0] - cb[0], pt[1] - cb[1], pt[2] - cb[2])
    for j in range(3):
        a = axb[j]
        o = d[0] * a[0] + d[1] * a[1] + d[2] * a[2]
        if o < -eb[j] or o > eb[j]:
            return False
    return True


# ---------------------------------------------------------------------------
# where each fixture WANTS to be
# ---------------------------------------------------------------------------

# A fixture's declared placement preference. The point of declaring it per
# fixture is that the previous rule was global -- "anywhere legal" -- and a
# global rule cannot be wrong about a bed without also being wrong about a
# maypole. These are the operator's words: beds, hearths and fire pits belong
# INSIDE, under a roof; a maypole and a chest bank are fine outdoors.
#
# Keyed by exact prefab where the prefab is exact, and by name pattern for the
# kit variants (`ashwood_bed`, `piece_chest_blackmetal`), because that is how
# the rest of this module identifies roles.
WANT_INDOOR = "indoor_covered"
WANT_OUTDOOR = "outdoor"
WANT_ANY = "any"

_PREFERENCE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Sleep and fire. A bed in the rain does not set your spawn any better, but
    # it is the thing the operator walked up to and called wrong, and a hearth
    # in a yard is a hearth nobody warms their hands at.
    #
    # Fire indoors has a real game consequence this module cannot measure: smoke
    # accumulates and damages a player unless there is a vent above. Obeyed
    # anyway, deliberately -- the instruction is explicit, and the cells chosen
    # are the ones with the highest measured cover, which is where a chimney
    # would be. Recorded here because it is a known limitation, not an oversight.
    (re.compile(r"(?:^|_)bed$|(?:^|_)bed_", re.IGNORECASE), WANT_INDOOR),
    (re.compile(r"^bed$|bed(?:_|$)", re.IGNORECASE), WANT_INDOOR),
    (re.compile(r"(?:^|_)(?:hearth|fire_pit|firepit|cookingstation|"
                r"stool|chair|bench|table|wardrobe|barber|bathtub)",
                re.IGNORECASE), WANT_INDOOR),
    # Outdoors by function: a maypole is a village green fixture, a kiln and a
    # smelter vent smoke and are traditionally built in the open, and a sundial
    # in a room tells no time.
    (re.compile(r"(?:^|_)(?:maypole|sundial|charcoal_kiln|smelter|windmill)",
                re.IGNORECASE), WANT_OUTDOOR),
)

# Explicit wins over pattern. A portal is deliberately ANY: it is a doorway, and
# a doorway outdoors is not a defect -- the portal's defect was its TAG. Chests,
# workbenches, forges and stonecutters are ANY because the pipeline places them
# where their work is, and the station extensions must stay beside their own
# station whatever room that is in.
PLACEMENT_PREFERENCE: dict[str, str] = {
    "bed": WANT_INDOOR,
    "hearth": WANT_INDOOR,
    "fire_pit": WANT_INDOOR,
    "piece_cookingstation": WANT_INDOOR,
    # `piece_cauldron` is deliberately WANT_ANY and not indoors. A preference
    # that cannot be met means the fixture is NOT PLACED, and a cauldron is a
    # station a preset REQUIRES: refusing to place one because a dock has no
    # roofed room would cost the site its cooking tier to satisfy an aesthetic
    # nobody asked for. The indoor set is exactly the four the operator named --
    # beds, hearths, fire pits, cooking stations.
    "piece_cauldron": WANT_ANY,
    "piece_maypole": WANT_OUTDOOR,
    "portal_wood": WANT_ANY,
    "piece_chest": WANT_ANY,
    "piece_chest_wood": WANT_ANY,
    "piece_workbench": WANT_ANY,
    "forge": WANT_ANY,
    "piece_stonecutter": WANT_ANY,
    "piece_cartographytable": WANT_ANY,
}


def preference(prefab: str) -> str:
    """What this fixture wants. WANT_ANY is the default and is a real answer:
    most of what the pipeline places has no indoor/outdoor opinion."""
    if prefab in PLACEMENT_PREFERENCE:
        return PLACEMENT_PREFERENCE[prefab]
    if base_geometry._STATION_NAME.search(prefab):
        # includes every `*_ext*` station extension
        return WANT_ANY
    for pattern, want in _PREFERENCE_PATTERNS:
        if pattern.search(prefab):
            return want
    return WANT_ANY


# How much room an INDOOR fixture is required to keep from the building it is
# standing in. Not 0.5 m, and the number is measured off the building itself.
#
# MEASURED on `halvar-master-refinery` with exact oriented geometry: its own 24
# fixtures against its own 186 wall, arch, pillar and door pieces have a MEDIAN
# gap of 0.000 m; 23 of the 24 sit within 0.15 m of a wall and 18 of the 24
# overlap one by more than 0.05 m. The builder put everything flush. Requiring
# 0.5 m of stock's fixtures inside that building demands something the building
# never does anywhere, and MEASURED it is not merely strict but impossible:
# at 0.5 m there is no legal indoor cell for a bed, a hearth, a fire pit or a
# cooking station anywhere in that body, which is exactly how all six of them
# ended up in the yard.
#
# 0.10 m is twice PIERCE_TOL_M, so a fixture can never be counted clear while
# actually intersecting, and it is the same margin the station-extension search
# has always used for the same reason. The 0.5 m default is unchanged for
# everything else, and the NO-INTERSECTION guard is unchanged for everything.
INDOOR_MARGIN_M = 0.10


def free_spot(prefab: str, body: PlacedBody, index: SolidIndex,
              centre: tuple[float, float], pad_y: float, pad_half: float,
              *, prefer: tuple[float, float] | None = None, yaw_deg: float = 0.0,
              margin_m: float | None = None, step_m: float = 1.0,
              headroom_m: float = 2.0, taken: list | None = None,
              want: str | None = None, yaws: tuple[float, ...] = (),
              mask: InteriorMask | None = None,
              geom: base_geometry.Geometry | None = None) -> dict | None:
    """The legal position for a loose object that satisfies its PREFERENCE.

    Legal means: inside the pad, the prefab's own oriented solid clears every
    body solid by `margin_m` (the floor it stands on excepted -- see `probe()`),
    nothing sits within `headroom_m` above it, the cell is reachable, and it does
    not collide with anything already placed this run (`taken`, a list of boxes
    the caller appends to).

    `want` adds the question that was missing. WANT_INDOOR requires every raster
    cell under the fixture to be INDOOR_COVERED, and then minimises the distance
    to the nearest WALL -- "near but not touching", which is where a person puts
    a bed. WANT_OUTDOOR requires the cell to be outdoors. WANT_ANY is the old
    behaviour: nearest legal cell to `prefer`.

    `headroom_m` defaults to 2.0 -- standing room. MEASURED on
    `pre-bonemass/iron-era-workshop`: of the 5,041 one-metre cells of its 70 m
    pad, 3,580 are clear for a `bed` at pad level and 3,544 still clear with 2 m
    of headroom, so insisting on headroom costs 36 cells and buys not tucking a
    bed under a floor slab.

    Returns the position and the MEASURED clearance it achieved, or None -- and
    None is a real answer the caller must report, not paper over. When `want`
    cannot be met it returns None EVEN IF a legal spot exists elsewhere on the
    pad, because quietly standing a bed in the yard is the defect.
    """
    g = geom or base_geometry.geometry()
    want = want or preference(prefab)
    if margin_m is None:
        margin_m = INDOOR_MARGIN_M if want == WANT_INDOOR else 0.5
    m = mask if mask is not None else body.interior(geom=g)
    cx, cz = centre
    px, pz = prefer if prefer is not None else centre
    steps = int(pad_half / step_m)
    angles = yaws or (yaw_deg,)

    # Candidates are WORLD points, not cell indices, and that is on purpose:
    # the mask is indexed from the PAD centre while a search may be centred
    # somewhere else entirely (the station-extension search centres on its
    # station), so carrying indices around invites reading one frame's index in
    # the other's space. One frame, converted at the edges.
    if want == WANT_INDOOR:
        # Iterate the interior directly. On `halvar-master-refinery` that is 39
        # cells against the pad's 5,041, and it is the same 1 m lattice.
        candidates = [m.world(*c) for c in m.of_class(INDOOR_COVERED)]
        candidates = [p for p in candidates
                      if abs(p[0] - cx) <= pad_half and abs(p[1] - cz) <= pad_half]
    else:
        candidates = [(cx + ix * step_m, cz + iz * step_m)
                      for ix in range(-steps, steps + 1)
                      for iz in range(-steps, steps + 1)]

    def distance(point) -> float:
        return math.hypot(point[0] - px, point[1] - pz)

    # Nearest first, so the search can STOP once a distance band has produced a
    # winner. Without it this is 5,041 cells times four yaws times an exact
    # separating-axis test per candidate, which MEASURED takes seconds per
    # fixture and turned a 20-fixture plan into a minute. With it, a fixture
    # whose preferred point is already legal costs one evaluation.
    candidates.sort(key=lambda p: (round(distance(p), 3), p))

    best = None
    best_key = None
    for x, z in candidates:
        d = math.hypot(x - px, z - pz)
        if (best_key is not None and want != WANT_INDOOR
                and round(d, 3) > best_key[0]):
            break                         # every remaining cell is further away
        # The mask's own cell for this point. NOT (ix, iz): those indices are
        # relative to the `centre` this SEARCH was given, and the extension
        # search centres on its station rather than on the pad, so reading the
        # mask with them reports another cell's cover height. Same class of bug
        # as everything else in this file's history -- a number that looks like
        # an answer to the question asked.
        mcell = m.cell(x, z)
        klass = m.at(x, z)
        if klass in (BLOCKED, UNREACHABLE):
            continue
        if want == WANT_OUTDOOR and klass != OUTDOOR:
            continue
        # The plane this fixture stands on. `pad_y` in the open; the top of the
        # building's own floor slab where the mask found one within a wall
        # course. MEASURED case: `hs_mistlands_thrad_workshop`'s interior floor
        # is 0.63 m above its pad, so a fixture placed at pad height there
        # stands 0.63 m INSIDE the slab -- which the pierce test correctly
        # refuses, which is why that body reported no interior at all.
        stand = m.stand_y(*mcell) if want == WANT_INDOOR else pad_y
        for angle in angles:
            box = fixture_box(prefab, x, stand, z, angle, g, pad=margin_m)
            spanned = m.cells_of(fixture_box(prefab, x, stand, z, angle, g))
            # The fixture's own footprint has to be UNDER THE ROOF and reachable,
            # but it does NOT have to be standing room. Those are different
            # questions and conflating them vetoes legal placements: the mask's
            # BLOCKED means "a player cannot stand in this cell", tested up to
            # 1.7 m, and a 0.38 m bed under a gallery beam is fine where a player
            # is not. Collision is the pierce test's job, below, and it is exact.
            if want == WANT_INDOOR and any(
                    c not in m.cover_m or m.klass.get(c) == UNREACHABLE
                    for c in spanned):
                continue
            if want == WANT_OUTDOOR and any(
                    m.klass.get(c, OUTDOOR) in (INDOOR_COVERED, INDOOR_UNCOVERED)
                    for c in spanned):
                continue
            # An indoor fixture must stand on ONE plane: if the cells under it
            # disagree about their floor height, it is straddling a step.
            if want == WANT_INDOOR and any(
                    abs(m.stand_y(*c) - stand) > PIERCE_TOL_M for c in spanned):
                continue
            probe_box = (box[0], box[1], box[2], max(box[3], stand + headroom_m),
                         box[4], box[5])
            if pierced(probe_box, index, stand_y=stand, geom=g):
                continue
            if taken and any(all(o > 1e-6 for o in overlap(box, t)) for t in taken):
                continue
            tight = fixture_box(prefab, x, stand, z, angle, g)
            wall_gap = _wall_gap(tight, index)
            # WANT_INDOOR wants the wall; everything else wants the preference
            # point. Both are then tie-broken by the other, so the search is
            # deterministic rather than dependent on iteration order.
            key = ((round(wall_gap, 3), round(d, 3)) if want == WANT_INDOOR
                   else (round(d, 3), round(wall_gap, 3)))
            if best_key is not None and key >= best_key:
                continue
            best_key = key
            best = {
                "prefab": prefab,
                "x": round(x, 3), "y": round(stand, 3), "z": round(z, 3),
                "yaw": angle,
                "offset": [round(x - cx, 3), round(z - cz, 3)],
                "from_prefer_m": round(d, 3),
                "clearance_m": round(clearance(tight, index, stand_y=stand,
                                               geom=g), 3),
                "wall_gap_m": round(wall_gap, 3),
                "want": want,
                "class": klass,
                "stands_on_m_above_pad": round(stand - m.pad_y, 3),
                "cover_m": (None if mcell not in m.cover_m
                            else round(m.cover_m[mcell], 2)),
                "margin_m": margin_m,
                "box": list(box),
            }
    return best


def _wall_gap(box, index: SolidIndex, search_m: float = 6.0) -> float:
    """Distance from this box to the nearest WALL-role solid, bounded.

    `search_m` when there is no wall in range, which for the indoor objective
    means "as far from a wall as the search can tell" and therefore sorts last.
    """
    probe_box = (box[0] - search_m, box[1] + search_m, box[2] - search_m,
                 box[3] + search_m, box[4] - search_m, box[5] + search_m)
    best = search_m
    for s in index.near(probe_box):
        if not _WALL_NAME.search(s[6]):
            continue
        if gap(box, s) >= best:
            continue
        for obb in _boxes(s):
            pen, sep = _sat(box, obb)
            d = 0.0 if pen > 0.0 else sep
            if d < best:
                best = d
    return best


# Wall-role solids, for the clearance figure the operator actually asked about.
# A prop TOUCHING a floor is a prop standing on it; a prop touching a wall is
# the report that started this: "there are portals in the middle of walls".
_WALL_NAME = re.compile(r"(?:^|_)(?:wall|arch|pillar|door|gate|woodwall)",
                        re.IGNORECASE)


def _min_wall_clearance(fixture_rows, body: PlacedBody, index: SolidIndex,
                        geom: base_geometry.Geometry) -> float | None:
    if not any(_WALL_NAME.search(s[6]) for s in body.solids):
        return None
    best = math.inf
    for row in fixture_rows:
        prefab, x, y, z, yaw = row[:5]
        box = fixture_box(prefab, x, y, z, yaw, geom)
        d = _wall_gap(box, index)
        if d < best:
            best = d
    return None if best is math.inf else round(best, 3)



def audit(fixtures, body: PlacedBody, index: SolidIndex | None = None,
          geom: base_geometry.Geometry | None = None) -> dict:
    """Does any of these fixtures stand inside the building, or outside it when
    it belongs inside, or pair with a portal nobody chose?

    `fixtures` is an iterable of `(prefab, x, y, z, yaw)` or
    `(prefab, x, y, z, yaw, tag)`, the sixth element being a portal tag.

    THREE fatal lists, kept separate because they are three different wrongs and
    a build message that conflates them tells an operator nothing:
      `failures`            -- the fixture is inside the structure.
      `preference_failures` -- the fixture is legal and in the wrong KIND of
                               place: a bed in the rain, a maypole in a cellar.
      `portal_failures`     -- a portal with no usable tag, which pairs at
                               random with any other blank-tag portal in the
                               world. See `portal_tag_problem`.

    And one NON-fatal list that exists because an empty `preference_failures`
    was being read as "every preference is satisfied":
      `preference_unsatisfiable` -- what this BODY cannot house AT ALL, derived
                               from the mask rather than from the rows. A bed
                               that is not placed cannot appear in
                               `preference_failures`, because that list only
                               contains fixtures that WERE placed somewhere
                               wrong -- so on a body with no interior the list
                               is empty and silent, which is the same
                               answers-a-different-question defect this module
                               keeps being extended to close.
    """
    g = geom or base_geometry.geometry()
    idx = index or body.index()
    mask = body.interior(geom=g)
    rows: list[dict] = []
    failures: list[str] = []
    preference_failures: list[str] = []
    portal_failures: list[str] = []
    portal_warnings: list[str] = []
    unmeasured: list[str] = []
    worst = math.inf
    fixture_rows = [tuple(r) for r in fixtures]
    for row in fixture_rows:
        prefab, x, y, z, yaw = row[:5]
        tag = row[5] if len(row) > 5 else None
        if not g.has_solid(prefab):
            # A prefab with no measured collider cannot be PROVEN clear, so it
            # is named rather than passed. Its box is a deliberate
            # under-statement, which is why it is not allowed to stand in for
            # a real one silently.
            unmeasured.append(prefab)
        box = fixture_box(prefab, x, y, z, yaw, g)
        hits, rests = probe(box, idx, stand_y=y, geom=g)
        clear = 0.0 if hits else clearance(box, idx, stand_y=y, geom=g)
        worst = min(worst, clear)
        want = preference(prefab)
        klass = mask.at(x, z)
        cells = mask.cells_of(box)
        spanned = {mask.klass.get(c, OUTDOOR) for c in cells}
        # Same two questions `free_spot` asks, in the same order, so the audit
        # cannot pass something the search would refuse or vice versa.
        indoors = (klass == INDOOR_COVERED
                   and all(c in mask.cover_m for c in cells)
                   and all(mask.klass.get(c) != UNREACHABLE for c in cells))
        uncovered = [c for c in cells if c not in mask.cover_m]
        rows.append({
            "prefab": prefab, "at": [round(x, 2), round(y, 2), round(z, 2)],
            "pierces": len(hits), "worst": hits[0] if hits else None,
            "rests_on": [r["prefab"] for r in rests],
            "clearance_m": round(clear, 3),
            "wall_gap_m": round(_wall_gap(box, idx), 3),
            "want": want, "class": klass,
            "spans": sorted(spanned),
            "tag": tag,
        })
        if hits:
            h = hits[0]
            failures.append(
                f"{prefab} at ({x:.2f}, {y:.2f}, {z:.2f}) overlaps {h['prefab']} by "
                f"{h['overlap_m'][0]:.2f} x {h['overlap_m'][1]:.2f} x "
                f"{h['overlap_m'][2]:.2f} m"
                + (f" (and {len(hits) - 1} more solid(s))" if len(hits) > 1 else ""))
        if want == WANT_INDOOR and not indoors:
            preference_failures.append(
                f"{prefab} at ({x:.2f}, {y:.2f}, {z:.2f}) wants {WANT_INDOOR}: it "
                f"stands on {'/'.join(sorted(spanned))} with "
                f"{len(uncovered)} of {len(cells)} cell(s) open to the sky -- this "
                f"body offers {len(mask.of_class(INDOOR_COVERED))} indoor covered "
                f"cell(s)")
        if want == WANT_OUTDOOR and (INDOOR_COVERED in spanned
                                     or INDOOR_UNCOVERED in spanned):
            preference_failures.append(
                f"{prefab} at ({x:.2f}, {y:.2f}, {z:.2f}) wants {WANT_OUTDOOR} and "
                f"stands on {'/'.join(sorted(spanned))}")
        if UNREACHABLE in spanned:
            preference_failures.append(
                f"{prefab} at ({x:.2f}, {y:.2f}, {z:.2f}) stands in space no player "
                f"can walk to -- it exists and cannot be used")
        if is_portal(prefab):
            problem = portal_tag_problem(tag)
            if problem:
                portal_failures.append(
                    f"{prefab} at ({x:.2f}, {y:.2f}, {z:.2f}): {problem}. MEASURED "
                    f"from assembly_valheim.dll, `Game::FindRandomUnconnectedPortal` "
                    f"pairs on `zdo.GetString(ZDOVars.s_tag, \"\") == tag` and then "
                    f"picks `Random.Range(0, count)`, so a blank tag pairs with any "
                    f"other blank-tag portal in the world at random")
    carried_portals = sum(n for p, n in body.carried.items() if is_portal(p))
    if carried_portals:
        # NOT a failure, and the distinction is the point: a captured portal
        # carries its own column-13 payload, which may or may not hold a `tag`
        # string, and this module does not decode it. Calling it untagged would
        # be asserting something unmeasured. Calling it fine would be worse.
        portal_warnings.append(
            f"the blueprint carries {carried_portals} portal piece(s) whose tag is "
            f"whatever its capture recorded; a blank one pairs at random. Either "
            f"add the prefab to the placement's `drop_prefabs` and let stock place "
            f"a tagged portal, or verify each carried tag in the world")
    # What this BODY cannot house, measured off the mask and not off the rows.
    # An empty `preference_failures` means "nothing placed is in the wrong kind
    # of place", which on a body with no interior is TRUE AND USELESS: the bed
    # was not placed at all, so it could not fail. This is the list that says so.
    unsatisfiable: list[str] = []
    if not mask.of_class(INDOOR_COVERED):
        wants_indoor = sorted({p for p in PLACEMENT_PREFERENCE
                               if PLACEMENT_PREFERENCE[p] == WANT_INDOOR})
        unsatisfiable.append(
            f"this body has NO indoor covered space at all: of "
            f"{len(mask.klass)} cell(s) at {mask.cell_m} m, "
            f"{mask.counts().get(BLOCKED, 0)} are blocked, "
            f"{mask.counts().get(COVERED_UNENCLOSED, 0)} are covered but not "
            f"enclosed and {mask.counts().get(INDOOR_UNCOVERED, 0)} are enclosed "
            f"but open to the sky. Nothing that wants {WANT_INDOOR} "
            f"({', '.join(wants_indoor)}) can be placed here, so an empty "
            f"`preference_failures` means nothing was placed rather than "
            f"everything was placed well -- pick a body with a room in it")
    return {
        "fixtures": rows,
        "failures": failures,
        "preference_failures": preference_failures,
        "preference_unsatisfiable": unsatisfiable,
        "portal_failures": portal_failures,
        "portal_warnings": portal_warnings,
        "unmeasured_prefabs": sorted(set(unmeasured)),
        "min_clearance_m": None if worst is math.inf else round(worst, 3),
        "min_wall_clearance_m": _min_wall_clearance(fixture_rows, body, idx, g),
        "interior": mask.summary(),
    }


# ---------------------------------------------------------------------------
# portals -- the pairing rule, read out of the game
# ---------------------------------------------------------------------------
#
# THE DEFECT: "i went through portal and on the other side there wasnt a portal
# to return with. i dont know how a portal could work without another endpoint."
#
# THE RULE, MEASURED from the DEPLOYED
# `Ulfsland/data/bepinex/valheim_server_Data/Managed/assembly_valheim.dll`
# (`monodis`), not from documentation or memory:
#
#   Game::ConnectPortals, pass 1 -- for every portal ZDO, if its
#   `GetConnectionZDOID(ConnectionType.Portal)` is set but the partner is gone,
#   or the partner's `GetString(ZDOVars.s_tag, "")` differs, or the partner does
#   not point back, the connection is CLEARED.
#
#   Game::ConnectPortals, pass 2 -- for every portal with no connection, it
#   calls `FindRandomUnconnectedPortal(portals, self, tag)` where
#   `tag = zdo.GetString(ZDOVars.s_tag, "")`, and cross-links the two.
#
#   Game::FindRandomUnconnectedPortal -- the candidate filter, verbatim in IL
#   order: `zdo != skip`; `zdo.GetString(ZDOVars.s_tag, "") != tag` SKIPS;
#   `GetConnectionZDOID(Portal) != ZDOID.None` SKIPS; `IsCurrentlyConnectingPortal`
#   SKIPS. Then `list[Random.Range(0, list.Count)]`.
#
# So pairing is EXACT STRING EQUALITY on the tag -- case-sensitive, whitespace
# significant, `""` a tag like any other -- and among equals the partner is a
# UNIFORM RANDOM DRAW, redrawn every time either end loses its partner. There is
# no proximity term and no ownership term anywhere in it.
#
# THE CONSEQUENCE, against what is MEASURED in this world: one blank-tag
# `portal_wood` at the site and 26 blank-tag `portal_wood` instances in
# mod-added world locations means the site's portal drew one of 26 destinations
# at random, and the operator's trip was not merely one-way but
# NON-DETERMINISTIC. `teleportall` / `Portals Casual` changes what may be
# CARRIED through a portal; it appears nowhere in the pairing path.
#
# THE SCHEME CHOSEN: ONE portal per site, with a UNIQUE NON-EMPTY TAG supplied
# by the caller, and NO portal at all when no tag is supplied.
#
# Not a pair per site, and that is a deliberate conformance decision rather than
# a preference: `tools/jumpstart/network/portals.py` already designs this world
# as a tiered hub-and-spoke graph in which one portal piece stands at EACH END of
# each tagged edge -- the spoke end at the site, the hub end in the hub's portal
# room. A pair at every site would be a second, contradictory topology and would
# double the piece count while leaving the hub unreachable. So stock places the
# spoke end; the hub end is a graph placement with the same tag. A tag also
# guarantees the one thing a blank tag cannot: our portal can NEVER pair with a
# world-location portal, because those are blank and `"" != "u-workshop"`.

PORTAL_PREFABS = frozenset(("portal_wood", "portal"))

# The in-game tag field's character limit. MEASURED: `TeleportWorld::Interact`
# calls `TextInput::RequestText(this, "$piece_portal_tag", 10)` -- `ldc.i4.s
# 0x0a`. This is an INPUT cap, not a storage or pairing cap: a longer tag
# written through `data=` pairs perfectly well. What 10 characters buys is
# REPAIRABILITY -- a player whose pair has broken can only retype a tag that
# fits in the box. A tag nobody can retype is a portal nobody can fix.
PORTAL_TAG_MAX_CHARS = 10

# Pairing is raw string equality, so a tag with a space or a non-ASCII homoglyph
# in it is a pair that silently never forms and cannot be diagnosed in game.
_PORTAL_TAG_OK = re.compile(r"^[A-Za-z0-9._-]+$")


def is_portal(prefab: str) -> bool:
    return prefab in PORTAL_PREFABS


def portal_tag_problem(tag: str | None) -> str | None:
    """Why this tag is unusable, or None when it is usable.

    A string, not a bool, because the caller's job is to say what is wrong to an
    operator, and "invalid tag" is not something anyone can act on.
    """
    if tag is None:
        return ("no portal tag was supplied, so this portal would be placed with "
                "a blank tag and pair at random with any other blank-tag portal")
    if not isinstance(tag, str):
        return f"portal tag must be a string, got {type(tag).__name__}"
    if tag == "":
        return ("the portal tag is empty, which is the blank tag: it pairs at "
                "random with every other untagged portal in the world")
    if tag != tag.strip():
        return (f"the portal tag {tag!r} has leading or trailing whitespace, which "
                f"pairing counts as part of the tag and a player cannot see")
    if len(tag) > PORTAL_TAG_MAX_CHARS:
        return (f"the portal tag {tag!r} is {len(tag)} characters; the in-game tag "
                f"field accepts {PORTAL_TAG_MAX_CHARS}, so a player could never "
                f"retype it to repair a broken pair")
    if not _PORTAL_TAG_OK.match(tag):
        return (f"the portal tag {tag!r} contains characters outside "
                f"[A-Za-z0-9._-]; pairing is exact string equality, so anything a "
                f"player cannot type identically is a pair that never forms")
    return None


def portal_data(tag: str) -> str:
    """The `data=` payload that puts `tag` on a portal at spawn time.

    MEASURED that this is the whole mechanism: `TeleportWorld` keeps no tag of
    its own -- `GetTagSignature`, `GetTagInfo` and `GetText` all read
    `m_nview.GetZDO().GetString(ZDOVars.s_tag, "")`, and the UI's `SetText` only
    invokes an RPC that writes that same ZDO string. The pairing code reads the
    ZDO directly. So a portal spawned with the string already on its ZDO is
    indistinguishable, to the game, from one a player typed a tag into.

    `tagauthor` is deliberately left unset. MEASURED: the tag is displayed via
    `CensorShittyWords::FilterUGC`, whose entire body in this assembly is
    `ldarg.0; ret` -- it returns the text unchanged -- so an author buys nothing,
    and fabricating a `PlatformUserID` string would be inventing identity.
    """
    problem = portal_tag_problem(tag)
    if problem:
        raise ValueError(problem)
    entry = data_entry.DataEntry()
    entry.strings[data_entry.stable_hash("tag")] = tag
    return entry.encode()


def portal_spawn_command(prefab: str, x: float, y: float, z: float, yaw: float,
                         tag: str) -> str:
    """The console line that lands a TAGGED portal, headlessly.

    `spawn_object` and not ValheimRcon's own `spawn`, because `spawn` takes no
    data payload and therefore cannot write a tag -- a portal placed with it is
    the defect. The argument orders are `to_rcon_plan`'s, MEASURED from IL:
    `pos=` is z,x,y and `rot=` is euler y,x,z.
    """
    import to_rcon_plan  # noqa: PLC0415  (circular at module import time)

    return (f"spawn_object {prefab}"
            f" pos={to_rcon_plan.pos_arg(x, y, z)}"
            f" rot={to_rcon_plan.rot_arg(0.0, yaw, 0.0)}"
            f" {to_rcon_plan.FROM_ORIGIN}"
            f" data={portal_data(tag)}")
