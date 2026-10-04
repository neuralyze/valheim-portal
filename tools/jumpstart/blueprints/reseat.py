#!/usr/bin/env python3
"""RE-SEAT A SPAWN SEAT: move a bed the acceptance gate refuses to a place on
the same floor where a player can actually stand up.

WHY THIS IS A TOOL AND NOT A ONE-OFF
------------------------------------
A spawn seat is where the world puts a player who has just died, and the gate's
`spawn` section refuses one that buries them, pins them against a beam, gives
them no room to stand up in, drops them, or leaves them 4.7 km from anything.
On Ulfsland that was 18 refusals over 9 of 18 claimable beds. Nine beds is a
list; the RULE that finds a legal seat is what a future world inherits, so the
rule lives here and the list is an argument.

THE CHOOSER AND THE JUDGE ARE THE SAME INSTRUMENTS, BY IMPORT
-------------------------------------------------------------
Every number below comes from `accept.instruments` and
`accept.sections._approach` - the gate's own census, the gate's own
`standing_surface`, `free_radius` and `headroom`, and the Player prefab's own
MEASURED capsule. A second implementation of "is this seat clear" would be a
second authority for one question, and this project has paid for that twice.
`plan()` therefore cannot choose a seat the gate will refuse for a reason
`plan()` did not evaluate.

WHERE THE CHOOSER IS DELIBERATELY STRICTER THAN THE GATE
--------------------------------------------------------
`piece_geometry.json` now carries each collider's KIND, and 6,956 of its 11,972
non-render colliders are a MeshCollider's `sharedMesh.bounds` - an UPPER BOUND
on a shape rather than the shape. The gate reports a refusal that rests only on
such a box as ADVISORY, because it must not fail a world on a reading whose
precision is worse than the judgement. A PLACER owes the opposite duty: it must
not put a bed inside something it cannot prove is empty. So:

  * for the SEAT the chooser uses the gate's rule exactly, and
  * for the BED'S OWN BODY a bounds-only overlap DISQUALIFIES a candidate
    whenever that solid's own pivot is within `PIVOT_NEAR_M` - i.e. it really
    is a nearby object and its box is a fair proxy (`piece_chest_wood` is one
    mesh row, 0.82 x 0.74 m, and sits half a metre from a bed).
  * A bounds-only solid whose pivot is FURTHER away than that is reaching in
    from outside and its box says nothing about this cell. MEASURED: the
    `Beech1` that refused bed#8 has its pivot 23.2 m away and a 19.08 x 21.50 m
    prefab-local box. Those are recorded per seat as `unproven`, never silently
    dropped.

A RE-SITE IS A DEMOLITION AND A PLACEMENT
-----------------------------------------
There is no `modifyObject` that moves a pivot - `ledger/schema.py::modify`
takes `rotation_deg` and nothing else, and `RotationModification::Apply` is
`Quaternion.Euler` then `ZDO.SetRotation`. So each seat is a `retire` followed
immediately by a `spawn`, back to back, and never one without the other: a bed
removed and not replaced is a LOST SPAWN, which is a worse defect than the one
being closed.

Usage:
    reseat.py --plan  --out /tmp/reseat.json
    reseat.py --apply --plan-file /tmp/reseat.json [--dry]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
if str(JUMPSTART) not in sys.path:
    sys.path.insert(0, str(JUMPSTART))

from accept import instruments as I                          # noqa: E402
from accept.sections import _approach as A                   # noqa: E402
from accept.sections import spawn as SPAWN                   # noqa: E402
import worldseed                                             # noqa: E402

ACTOR = "ParSpawnBeds"
# ROOM_WALLS_DEPENDS_ON: what may NOT wall a seat's room in (see `room`).
#   covered_low  a piece the seat's authored solid already enters and that
#                rises no higher than it.  Neuter: () -- the jute_carpet
#                under the lighthouse bed walls it into one cell.
ROOM_WALLS_DEPENDS_ON = ("covered_low",)

# A bounds-only solid is a fair proxy for a real obstruction when its own pivot
# is this close to the candidate. DESIGN, and named as one: the MEASURED
# separation that makes the distinction useful is 0.5 m (a chest beside a bed)
# against 13.4-23.2 m (the five Beech1 canopies whose boxes reach over Stenvik
# and Vestvik). Any threshold between those two decides identically on this
# world's population, and every skipped solid is reported with its own measured
# pivot distance so a reader can re-decide.
PIVOT_NEAR_M = 3.0

# How far from its present pivot a seat may be re-sited, and how finely the
# floor is searched. A seat is RE-SEATED, not relocated: it must stay in the
# room it belongs to, on the same floor, and the search prefers the smallest
# move that works.
SEARCH_R_M = 6.0
SEARCH_STEP_M = 0.25
YAW_STEP_DEG = 7.5
# Margin over the walker's own measured capsule. Not slack: the gate's limit is
# exactly the capsule, and a seat chosen AT the limit is one float rounding away
# from the refusal it was meant to close.
FREE_MARGIN_M = 0.06
HEAD_MARGIN_M = 0.05
# A floor is the SAME floor within this. Two storeys of a Valheim house are
# 2 m apart, so this cannot pick the wrong one.
SAME_FLOOR_M = 0.02
# A piece whose whole solid sits at or below the seat's surface by this much is
# the thing the bed RESTS ON, not something it collides with.
FLOOR_TOL_M = 0.08
BODY_TOL_M = 0.05

LOOKUP_R_M = 0.30
SPAWN_PROBE_R_M = 1.5


# ---------------------------------------------------------------------------
# the space a seat is judged in: a world's census, or one blueprint's rows
# ---------------------------------------------------------------------------
class SeatSpace:
    """Solids, the corpus, the walker and the seats - everything `measure`,
    `body_overlap` and `candidates` read, and nothing about where it came
    from.

    TWO SOURCES, ONE RULE.  `World` fills it from a save's object census, so
    a bed already standing in a world can be re-seated.  `blueprints/
    preflight.py` fills it from ONE BLUEPRINT's own rows over a dead-flat pad,
    so a bed the file itself puts under its own beam is re-seated before a
    byte is written.  MEASURED, drive 3 of Fresh42: seven `bed` rows refused
    by the gate at 0.431 m of room and 0.449 m of headroom under `wood_beam`,
    all seven from `hs_blackforest_kin_01_woodhouse.blueprint`.  A second
    chooser in the emitter would be a second authority for one question.

    `ground_y` is the applied ground every seat may stand on, or None where
    the ground is not read and only pieces are offered - which is how `World`
    has always judged, and it is unchanged.
    """

    def __init__(self, grid, geom: dict, walker_r: float, walker_h: float,
                 seats: list, pieces: list, ground_y: float | None = None):
        self.grid = grid
        self.geom = geom
        self.walker_r = walker_r
        self.walker_h = walker_h
        self.seats = seats          # (prefab, x, y, z, (qx, qy, qz, qw))
        self.pieces = pieces        # (prefab, x, y, z) of every build piece
        self.ground_y = ground_y

    def own(self, i: int) -> list:
        """The seat's own solid, BY IDENTITY - same prefab and the same pivot
        to within a millimetre, never by prefab name. A SECOND bed on this
        pivot is a duplicate and must stay visible."""
        n, x, y, z, _q = self.seats[i]
        return [s for s in self.grid.near(x, z, 0.0)
                if s.name == n and abs(s.pos[0] - x) < 1e-3
                and abs(s.pos[1] - y) < 1e-3 and abs(s.pos[2] - z) < 1e-3]

    def loose(self, x: float, z: float) -> list:
        """Every solid near (x, z) whose whole extent is a MeshCollider's
        bounding box. The gate drops these before deciding; see `probe_seat`."""
        return [s for s in self.grid.near(x, z, SPAWN.CLEAR_CAP_M + 2.0)
                if s.bounds_only]

    def reach(self, i: int, x: float, z: float) -> float:
        """Nearest build piece EXCLUDING THE SEAT'S OWN ZDO. A bed is a build
        piece; a reach taken over every build piece returns 0.000 m for every
        bed in every world, which is the seat measuring itself."""
        n, sx, sy, sz, _q = self.seats[i]
        best = math.inf
        for pn, px, py, pz in self.pieces:
            if (pn == n and abs(px - sx) < 1e-3 and abs(py - sy) < 1e-3
                    and abs(pz - sz) < 1e-3):
                continue
            d = math.hypot(px - x, pz - z)
            if d < best:
                best = d
        return best


class World(SeatSpace):
    """The census, the corpus and the walker - the gate's own, not a copy."""

    def __init__(self, world: str | None = None, seed: str | None = None,
                 zone_batch: int = 110):
        # ARGUMENT > ENVIRONMENT > DEFAULT, through the one resolver.  These
        # used to be the literals "Ulfsland" and "Pirate68", which made this
        # gate unaddressable on any other world no matter what the caller
        # passed to the process.
        world = world or worldseed.world()
        seed = seed or worldseed.seed()
        self.args = types.SimpleNamespace(
            world=world, seed=seed, vh_src=I.VH_SRC_DEFAULT,
            world_src=I.world_src_default(world), sandbox=I.SANDBOX_DEFAULT,
            cache=I.CACHE_DEFAULT, zone_batch=zone_batch)
        self.cache = Path(self.args.cache) / world
        live = Path(self.args.world_src)
        # THE SAVE MUST BE THIS WORLD'S SAVE.  It was `WORLD_SRC_DEFAULT`, a
        # fixed Ulfsland path; parameterising `world` while leaving it alone
        # is exactly the join-of-two-worlds `accept --world Fresh42` produced
        # -- cache and census under one name, ground from another.  One reader
        # for that question, in instruments.
        I.require_world_save(live, world)
        self.fp = I.save_fingerprint(live)
        self.args.world_src = str(I.snapshot(live, self.cache / self.fp
                                             / "world"))
        self.census = I.census(self.cache, self.fp, self.args)
        geom, build = I.load_geometry()
        self.build = build
        super().__init__(
            grid=I.Grid(I.solids_from_census(self.census, geom)), geom=geom,
            walker_r=self.census.walker_radius,
            walker_h=self.census.walker_height,
            seats=[(n, x, y, z, (qx, qy, qz, qw))
                   for (n, x, y, z, qx, qy, qz, qw) in self.census.rows
                   if n in SPAWN.CLAIMABLE_BEDS],
            pieces=[(n, x, y, z) for (n, x, y, z, *_q) in self.census.rows
                    if n in build])


# ---------------------------------------------------------------------------
# geometry helpers
# ---------------------------------------------------------------------------
def yaw_quat(deg: float) -> tuple[float, float, float, float]:
    h = math.radians(deg) / 2.0
    return (0.0, math.sin(h), 0.0, math.cos(h))


def _unrotate(q, p):
    return I.rotate((-q[0], -q[1], -q[2], q[3]), p)


def _local_box(parts) -> tuple[float, float, float, float, float, float]:
    vs = [v for part in parts for v in I.part_vertices(part)]
    return (min(v[0] for v in vs), max(v[0] for v in vs),
            min(v[1] for v in vs), max(v[1] for v in vs),
            min(v[2] for v in vs), max(v[2] for v in vs))


def _overlaps(w: SeatSpace, prefab: str, x: float, y: float, z: float,
              yaw: float, skip, surf: float, extra=()):
    """Every solid the bed's OWN solid would enter at this pose, with the
    depth, and the bounds-only solids too far away to mean anything.

    The overlap is measured in the BED'S frame: the world solids' vertices are
    rotated into it and clipped against the prefab's own local box. That
    over-states for an oblique solid and never under-states, which is the
    direction a placer must err in.
    """
    parts = w.geom[prefab]
    lx0, lx1, ly0, ly1, lz0, lz1 = _local_box(parts)
    q = yaw_quat(yaw)
    reach = max(math.hypot(a, b) for a in (lx0, lx1) for b in (lz0, lz1))
    hits, unproven = [], {}
    for s in list(w.grid.near(x, z, reach + 3.0)) + list(extra):
        if s in skip or not s.verts:
            continue
        top = s.level_top()
        if (top is not None and top <= surf + FLOOR_TOL_M
                and s.ymax <= surf + FLOOR_TOL_M):
            continue                       # the floor the bed rests on
        if s.ymin >= y + ly1 - BODY_TOL_M or s.ymax <= y + ly0 + BODY_TOL_M:
            continue
        xs, zs = [], []
        for v in s.verts:
            r = _unrotate(q, (v[0] - x, v[1] - y, v[2] - z))
            xs.append(r[0])
            zs.append(r[2])
        ox = min(max(xs), lx1) - max(min(xs), lx0)
        oz = min(max(zs), lz1) - max(min(zs), lz0)
        if ox <= BODY_TOL_M or oz <= BODY_TOL_M:
            continue
        pivot_d = math.hypot(s.pos[0] - x, s.pos[2] - z)
        if s.bounds_only and pivot_d > PIVOT_NEAR_M:
            unproven[s.name] = round(pivot_d, 2)
            continue
        hits.append((s, min(ox, oz)))
    return hits, unproven


def body_overlap(w: SeatSpace, prefab: str, x: float, y: float, z: float,
                 yaw: float, skip, surf: float,
                 extra=()) -> tuple[float, str | None, dict]:
    """How deep the bed's OWN solid would sit in something else, the piece it
    would be, and the bounds-only solids whose boxes reach this cell from too
    far away to mean anything (`_overlaps`, deepest first)."""
    hits, unproven = _overlaps(w, prefab, x, y, z, yaw, skip, surf, extra)
    worst, deepest = None, 0.0
    for s, d in hits:
        if d > deepest:
            deepest, worst = d, s
    return (round(deepest, 3), worst.name if worst else None, unproven)


def measure(w: SeatSpace, i: int, x: float, y: float, z: float, skip
            ) -> dict | None:
    """The five player-facing numbers at one cell, on the gate's rule.

    `penetration`, `clearance` and `headroom` are judged with every
    bounds-only solid dropped, exactly as `spawn.probe_seat` does, and the
    full-corpus readings are reported beside them so the difference is
    visible rather than assumed.
    """
    surf, carrier = A.standing_surface(x, z, w.grid, w.ground_y,
                                       ceiling=y + 1e-3,
                                       skip=skip)
    if surf is None:
        return None
    tight = list(skip) + w.loose(x, z)
    free, who = A.free_radius(x, z, surf, w.grid, w.walker_h,
                              SPAWN.CLEAR_CAP_M, skip=tight)
    head, cap = A.headroom(x, z, surf, w.grid, w.walker_r,
                           SPAWN.HEADROOM_CAP_M, skip=tight)
    bfree, _bw = A.free_radius(x, z, surf, w.grid, w.walker_h,
                               SPAWN.CLEAR_CAP_M, skip=skip)
    bhead, _bc = A.headroom(x, z, surf, w.grid, w.walker_r,
                            SPAWN.HEADROOM_CAP_M, skip=skip)
    return dict(
        surface_y=round(surf, 3),
        carrier=carrier.name if carrier else "applied ground",
        penetration_m=round(-free, 3) if free < 0.0 else 0.0,
        clearance_m=round(free, 3), clearance_piece=who.name if who else None,
        headroom_m=round(head, 3), headroom_piece=cap.name if cap else None,
        drop_m=round(y - surf, 3),
        reach_m=round(w.reach(i, x, z), 3),
        full_corpus_clearance_m=round(bfree, 3),
        full_corpus_headroom_m=round(bhead, 3))


def seat_ok(w: SeatSpace, m: dict) -> bool:
    return (m["penetration_m"] == 0.0
            and m["clearance_m"] >= w.walker_r
            and m["headroom_m"] >= w.walker_h
            and m["drop_m"] <= SPAWN.DROP_LIMIT_M
            and m["reach_m"] <= SPAWN.REACH_M)


# ---------------------------------------------------------------------------
# the search
# ---------------------------------------------------------------------------
def room(w: SeatSpace, i: int, skip, radius: float = SEARCH_R_M
         ) -> set[tuple[int, int]]:
    """The lattice cells of seat `i`'s own ROOM, as `(ix, iz)` of
    `candidates`' own lattice: flood-filled 4-connected from the seat's
    pivot, on the seat's own floor, through every cell that is

      * at least HALF A LATTICE STEP clear of anything standing in the BED'S
        OWN BAND - from a floor tolerance above the surface to the top of the
        seat's own solid.  That band is what a bed slides through: the
        drive-3 `wood_wall_half` (0.73 m over the floor) closes it, the
        `wood_beam` the defect is about (0.449 m UP, over a 0.38 m bed) does
        not.  Asked in the walker's band instead, the beam walls the seat in
        at its own pivot and the room is one cell.  Half a step is what makes
        a wall a wall at any thickness: a wall `t` thick closes a band
        `t + step` wide, which no 4-connected walk crosses without landing
        in it;
      * UNDER A ROOF, when the seat itself is: something directly overhead
        above the walker's own height.  A DOOR is an opening in the band and
        the fill walks through it; the roof is what keeps it indoors.

    WHAT THE BED ALREADY LIES OVER IS NOT A WALL OF ITS ROOM
    (ROOM_WALLS_DEPENDS_ON `covered_low`).  A piece the seat's own AUTHORED
    solid already enters, and that rises no higher than the top of that
    solid, is something the bed was authored lying on or across, not
    something that bounds it.  MEASURED on
    `hs_plains_ulf_the_builder_lighthouse.blueprint`, row 180: the bed lies
    on a `jute_carpet` (2.72-2.86 m over a 2.741 m floor, entered 1.200 m
    deep), the carpet's 0.119 m top is over the 0.08 m floor tolerance, and
    every 4-neighbour of the pivot is inside it (free -0.655 to -1.117 m), so
    the room was ONE cell and the bed could not be moved off the stair above
    it.  A wall the bed was authored into still bounds it: it rises above
    the bed's top.
    """
    _n, bx, by, bz, q0 = w.seats[i]
    base, _c = A.standing_surface(bx, bz, w.grid, w.ground_y,
                                  ceiling=by + 1e-3, skip=skip)
    own = w.own(i)
    if base is None or not own:
        return set()
    band = max(s.ymax for s in own) - base
    k = int(radius / SEARCH_STEP_M)
    half = SEARCH_STEP_M / 2.0
    walls_skip = list(skip)
    if "covered_low" in ROOM_WALLS_DEPENDS_ON:
        top = max(s.ymax for s in own)
        hits, _u = _overlaps(w, _n, bx, by, bz, A.yaw_deg(q0), skip, base)
        walls_skip += [s for s, _d in hits if s.ymax <= top + BODY_TOL_M]

    def roofed(x: float, z: float) -> bool:
        _h, cap = A.headroom(x, z, base + w.walker_h, w.grid, 0.0,
                             SPAWN.HEADROOM_CAP_M, skip=skip)
        return cap is not None

    indoors = roofed(bx, bz)

    def open_(ix: int, iz: int) -> bool:
        x, z = bx + ix * SEARCH_STEP_M, bz + iz * SEARCH_STEP_M
        free, _who = A.free_radius(x, z, base, w.grid, band, half,
                                   underfoot_tol=FLOOR_TOL_M, skip=walls_skip)
        return free >= half and (not indoors or roofed(x, z))

    seen = {(0, 0)}
    todo = [(0, 0)]
    while todo:
        ix, iz = todo.pop()
        for jx, jz in ((ix + 1, iz), (ix - 1, iz), (ix, iz + 1), (ix, iz - 1)):
            if ((jx, jz) in seen or math.hypot(jx, jz) > k
                    or not open_(jx, jz)):
                continue
            seen.add((jx, jz))
            todo.append((jx, jz))
    return seen


def candidates(w: SeatSpace, i: int, placed=(), retired=(),
               radius: float = SEARCH_R_M, first: bool = False,
               admit=None, body_tol: float = 0.0,
               yaw_step: float = YAW_STEP_DEG, confirm=None) -> list[dict]:
    """Every legal re-seat for seat `i`, nearest and cleanest first.

    `placed` are beds already re-seated in this same plan, as Solids at their
    NEW poses, and `retired` are their old solids. Without both, two beds in
    one loft are each chosen against the other's stale position and land on
    top of each other - MEASURED on bed#14 and bed#15, whose independently
    chosen seats were 0.67 m apart with a 1.2 x 2.8 m bed each.

    `first=True` is the EMITTER's question - "the smallest move that works" -
    and returns at most one candidate: the nearest cell whose seat passes AND
    whose bed body enters nothing deeper than `body_tol`.  Cells are walked
    nearest-first in both modes, so it is the head of the full list's
    admissible subset up to a same-distance tie; the full scan tries up to
    48 yaws on every passing cell and MEASURED took over 30 s on one
    216-row blueprint, which an emitter that re-measures after every repair
    cannot afford.  Yaws are tried in order of their turn away from the
    authored one, so an authored yaw that fits is always kept.

    `admit(ix, iz) -> bool` vetoes a lattice cell before it is measured; the
    emitter uses it to keep a seat in its own `room`.  `confirm(row) -> bool`
    is the first mode's last word on a candidate it would return; the
    emitter uses it to refuse a pose that clears this seat by blocking
    another.
    """
    n, bx, by, bz, q0 = w.seats[i]
    skip = w.own(i) + list(retired)
    base, _c = A.standing_surface(bx, bz, w.grid, w.ground_y,
                                  ceiling=by + 1e-3,
                                  skip=skip)
    if base is None:
        return []
    lift = by - base                    # the prefab's own pivot-above-floor
    yaw0 = A.yaw_deg(q0)
    nturn = int(round(360.0 / yaw_step))
    turns = sorted(range(nturn), key=lambda j: (min(j, nturn - j), j))
    yaws = [yaw0 + j * yaw_step for j in turns]
    out: list[dict] = []
    k = int(radius / SEARCH_STEP_M)
    cells = sorted(((math.hypot(ix, iz) * SEARCH_STEP_M, ix, iz)
                    for ix in range(-k, k + 1) for iz in range(-k, k + 1)))
    for _d, ix, iz in cells:
        x, z = bx + ix * SEARCH_STEP_M, bz + iz * SEARCH_STEP_M
        d = math.hypot(x - bx, z - bz)
        if d > radius:
            continue
        if admit is not None and not admit(ix, iz):
            continue
        surf, _c = A.standing_surface(x, z, w.grid, w.ground_y,
                                      ceiling=by + 2.5, skip=skip)
        if surf is None or abs(surf - base) > SAME_FLOOR_M:
            continue
        y = surf + lift
        m = measure(w, i, x, y, z, skip)
        if m is None:
            continue
        if (m["clearance_m"] < w.walker_r + FREE_MARGIN_M
                or m["headroom_m"] < w.walker_h + HEAD_MARGIN_M
                or m["drop_m"] > SPAWN.DROP_LIMIT_M
                or m["reach_m"] > SPAWN.REACH_M):
            continue
        # AND the same two readings with the NEAR bounds-only solids put
        # back: a chest half a metre away is a real obstruction whatever
        # its collider kind.
        near_loose = [s for s in w.loose(x, z)
                      if math.hypot(s.pos[0] - x, s.pos[2] - z)
                      > PIVOT_NEAR_M]
        nfree, _w2 = A.free_radius(x, z, surf, w.grid, w.walker_h,
                                   SPAWN.CLEAR_CAP_M,
                                   skip=list(skip) + near_loose)
        nhead, _c2 = A.headroom(x, z, surf, w.grid, w.walker_r,
                                SPAWN.HEADROOM_CAP_M,
                                skip=list(skip) + near_loose)
        if (nfree < w.walker_r + FREE_MARGIN_M
                or nhead < w.walker_h + HEAD_MARGIN_M):
            continue
        best = None
        for yw in yaws:
            ov, piece, unproven = body_overlap(w, n, x, y, z, yw, skip,
                                               surf, extra=placed)
            if best is None or ov < best[0]:
                best = (ov, piece, yw % 360.0, unproven)
            if ov <= body_tol:
                break
        row = dict(seat=i, prefab=n, x=round(x, 3), y=round(y, 3),
                   z=round(z, 3), yaw_deg=round(best[2], 2),
                   body_overlap_m=best[0], body_overlap_piece=best[1],
                   unproven_bounds=best[3], moved_m=round(d, 3),
                   pos=(x, y, z), **m)
        if first:
            if best[0] <= body_tol and (confirm is None or confirm(row)):
                return [row]
            continue
        out.append(row)
    # Least body overlap first, then the smallest move: a seat is RE-SEATED.
    out.sort(key=lambda r: (r["body_overlap_m"], r["moved_m"],
                            -r["clearance_m"]))
    return out


def plan(w: World) -> dict:
    """Every claimable bed the gate refuses, and where it should go instead."""
    refused, keep = [], []
    for i in range(len(w.seats)):
        n, x, y, z, _q = w.seats[i]
        before = measure(w, i, x, y, z, w.own(i))
        ov, piece, unproven = (
            (None, None, {}) if before is None else
            body_overlap(w, n, x, y, z, A.yaw_deg(_q), w.own(i),
                         before["surface_y"]))
        row = dict(seat=i, prefab=n, x=round(x, 3), y=round(y, 3),
                   z=round(z, 3), yaw_deg=round(A.yaw_deg(_q), 2),
                   body_overlap_m=ov, body_overlap_piece=piece,
                   unproven_bounds=unproven,
                   **(before or dict(surface_y=None)))
        (keep if (before is not None and seat_ok(w, before))
         else refused).append(row)

    moves, refusals = [], []
    placed, retired = [], []
    for row in refused:
        i = row["seat"]
        cs = candidates(w, i, placed=placed, retired=retired)
        if not cs:
            refusals.append({"before": row,
                             "why": f"no cell within {SEARCH_R_M:.0f} m on "
                                    "this seat's own floor satisfies the "
                                    "gate's five checks"})
            continue
        new = cs[0]
        moves.append({"before": row, "after": new,
                      "alternatives": len(cs)})
        placed.append(I.mk_solid(new["prefab"],
                                 (new["x"], new["y"], new["z"]),
                                 yaw_quat(new["yaw_deg"]),
                                 w.geom[new["prefab"]]))
        retired.extend(w.own(i))
    return {"world": w.args.world, "save_fingerprint": w.fp,
            "walker": {"radius_m": w.walker_r, "height_m": w.walker_h},
            "seats_total": len(w.seats), "seats_passing": len(keep),
            "moves": moves, "unresolved": refusals, "passing": keep}


# ---------------------------------------------------------------------------
# the write
# ---------------------------------------------------------------------------
def apply_plan(doc: dict, dry: bool) -> dict:
    """Retire each refused bed and spawn its replacement, one seat at a time.

    READ BEFORE WRITE AT THE BOUNDARY THAT MATTERS. The plan came from a census
    minutes old and other agents are live on this world, so every victim is
    re-asked of the LIVE world immediately before its command is built: exactly
    one bed of that prefab within `LOOKUP_R_M` of the recorded 3D position, or
    the move is DROPPED and reported. The id that read returns goes on the
    wire because a radius cannot tell two pieces at one pivot apart, and the id
    is evidence and never the key - it does not survive a container restart.
    """
    sys.path.insert(0, str(JUMPSTART / "roads"))
    sys.path.insert(0, str(JUMPSTART / "ledger"))
    import clear as C                                        # noqa: PLC0415
    import to_rcon_plan                                      # noqa: PLC0415
    from live import LiveBuilder                             # noqa: PLC0415

    out = {"moved": [], "dropped": []}
    with LiveBuilder(actor=ACTOR, dry=dry) as b:
        for mv in doc["moves"]:
            old, new = mv["before"], mv["after"]
            got, unlisted, _k = C.list_prefab(
                b.srv, old["prefab"], old["x"], old["y"], old["z"], 1.0)
            if unlisted:
                out["dropped"].append({**mv, "why": "the box around this bed "
                                                    "was too dense to list"})
                continue
            here = [r for r in got.values()
                    if math.dist((r["x"], r["y"], r["z"]),
                                 (old["x"], old["y"], old["z"])) <= LOOKUP_R_M]
            if len(here) != 1:
                out["dropped"].append({**mv, "why": f"{len(here)} "
                                       f"{old['prefab']} within {LOOKUP_R_M} "
                                       "m of the recorded position, not 1"})
                continue
            taken, _by = b.srv.count(new["prefab"], new["x"], new["z"],
                                     SPAWN_PROBE_R_M)
            col, _by = b.srv.count(old["prefab"], old["x"], old["z"],
                                   SPAWN_PROBE_R_M)
            # The chosen spot may legitimately be inside the probe radius of
            # the bed we are about to remove; anything MORE than that is
            # another bed and the move is dropped.
            same = 1 if math.hypot(new["x"] - old["x"],
                                   new["z"] - old["z"]) <= SPAWN_PROBE_R_M \
                else 0
            if taken - same > 0:
                out["dropped"].append({**mv, "why": f"{taken - same} other "
                                       f"{new['prefab']} already within "
                                       f"{SPAWN_PROBE_R_M} m of the chosen "
                                       "spot"})
                continue
            zdo = here[0]["id"]
            site = f"spawn-seat-{old['prefab']}-{old['seat']}"
            rr = b.emit("retire", params={
                "prefab": old["prefab"],
                "pos": [old["x"], old["y"], old["z"]],
                "radius_m": LOOKUP_R_M, "retires": [0],
                "role": "spawn_seat", "site_id": site,
                "pieces": [{"prefab": old["prefab"],
                            "pos": [old["x"], old["y"], old["z"]],
                            "radius_m": LOOKUP_R_M,
                            "keeps": col - 1, "zdo_id": zdo}],
                "reason": (
                    f"spawn seat {old['prefab']}#{old['seat']} is a place the "
                    f"world delivers a player to and a player cannot stand "
                    f"up in it: clearance {old['clearance_m']:.3f} m against "
                    f"the Player capsule's own measured "
                    f"{doc['walker']['radius_m']:.3f} m, headroom "
                    f"{old['headroom_m']:.3f} m against its "
                    f"{doc['walker']['height_m']:.3f} m, penetration "
                    f"{old['penetration_m']:.3f} m. Re-seated in the very "
                    f"next record {new['moved_m']:.3f} m away on the SAME "
                    f"floor ({new['surface_y']:.3f} m, carried by "
                    f"{new['carrier']}), so no spawn is lost. Removed by ZDO "
                    f"id because a radius cannot tell two pieces at one pivot "
                    f"apart; the id is evidence and NOT the key - it does not "
                    f"survive a container restart, which is why the entry "
                    f"also carries prefab plus the full 3D position.")},
                wire=[f"deleteObjects -id {zdo} -force"],
                expect={"prefab_count": [
                    {"prefab": old["prefab"],
                     "pos": [old["x"], old["z"]],
                     "max": SPAWN_PROBE_R_M, "count": col - 1}]},
                meta={"zdo_id_removed": zdo, "seat": old["seat"],
                      "players_probe_ms": round(b.srv.probe(), 1),
                      "why": "re-seat, not demolition: the replacement spawn "
                             "is the next record"})

            line = (f"spawn_object {new['prefab']}"
                    f" pos={to_rcon_plan.pos_arg(new['x'], new['y'], new['z'])}"
                    f" rot={to_rcon_plan.rot_arg(0.0, new['yaw_deg'], 0.0)}"
                    f" {to_rcon_plan.FROM_ORIGIN}")
            body = line + "\n"
            blob = b.blob(body.encode(),
                          note=f"re-seated spawn bed {site}")
            b.observe(
                f"{site} re-seated clearance and headroom",
                "MEASURED with the acceptance gate's own instruments - "
                "_approach.standing_surface, free_radius and headroom over "
                "the save's object census, against the Player prefab's own "
                "measured capsule, with every MeshCollider-bounds solid "
                "dropped exactly as spawn.probe_seat drops them",
                "tools/jumpstart/blueprints/reseat.py::measure",
                {"clearance_m": new["clearance_m"],
                 "headroom_m": new["headroom_m"],
                 "penetration_m": new["penetration_m"],
                 "drop_m": new["drop_m"], "reach_m": new["reach_m"],
                 "was_clearance_m": old["clearance_m"],
                 "was_headroom_m": old["headroom_m"],
                 "was_penetration_m": old["penetration_m"]},
                units="m")
            sr = b.emit("spawn", params={
                "prefab": new["prefab"],
                "pos": [new["x"], new["y"], new["z"]],
                "yaw_deg": new["yaw_deg"],
                "role": "spawn_seat", "site_id": site,
                "rot_sent": [0.0, new["yaw_deg"], 0.0],
                "from": [0.0, 0.0, 0.0],
                "datum": (f"the seat's own floor top {new['surface_y']:.3f} m "
                          f"({new['carrier']}) plus the prefab's own measured "
                          f"pivot offset {new['y'] - new['surface_y']:.3f} m"),
                "guard_radius_m": SPAWN_PROBE_R_M,
            }, wire=[line],
                requires={"blobs": [blob],
                          "mods": ["WorldEditCommands", "ServerDevcommands"],
                          "prefabs": [new["prefab"]]},
                expect={"prefab_count": [
                    {"prefab": new["prefab"],
                     "pos": [new["x"], new["z"]],
                     "max": SPAWN_PROBE_R_M, "count": 1}]},
                meta={"seat": old["seat"], "moved_m": new["moved_m"],
                      "plan_sha256": hashlib.sha256(body.encode()).hexdigest(),
                      "unproven_bounds": new["unproven_bounds"]})
            out["moved"].append({"seat": old["seat"], "retire": rr.get("seq"),
                                 "spawn": sr.get("seq"),
                                 "status": sr.get("status")})
            print(f"  seat {old['seat']}: retire {rr.get('seq')} -> "
                  f"spawn {sr.get('seq')} [{sr.get('status')}] "
                  f"{new['moved_m']:.3f} m")
        if not dry and out["moved"]:
            save_seats(b, doc, {m["seat"] for m in out["moved"]})
    return out


def save_seats(b, doc: dict, seats: set[int]) -> dict:
    """`save`, with the re-seat itself as its postcondition.

    A `spawn_object` that answered is a LIVE ZDO; a seat that survives
    SERIALISATION is a different claim, and the whole point of a spawn seat is
    that it is still there when the player who died comes back. So the save
    re-reads every new pivot rather than asserting a total.

    Its own function because the save is a SEPARATE mutation from the moves:
    when nine seats applied and the save record was refused, the world was
    correct and unsaved, and re-running `--apply` would have dropped all nine
    at the read-before-write boundary (no bed at the old pivots any more) and
    therefore never reached the save at all. `--save` closes that.
    """
    counts = [{"prefab": mv["after"]["prefab"],
               "pos": [mv["after"]["x"], mv["after"]["z"]],
               "max": LOOKUP_R_M, "count": 1}
              for mv in doc["moves"] if mv["before"]["seat"] in seats]
    if not counts:
        raise SystemExit("save refused: no re-seated pivot to assert, and a "
                         "save with no postcondition is a record with no "
                         "evidence")
    return b.emit("save", params={
        "role": "spawn_seat",
        "note": f"{len(counts)} spawn seat(s) re-seated; every retire has "
                "its spawn in the next record"},
        wire=["save"], expect={"prefab_count": counts})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--out", default="/tmp/reseat_plan.json")
    ap.add_argument("--plan-file")
    worldseed.add_arguments(ap)
    ap.add_argument("--save", default="",
                    help="emit the `save` record for seats already applied: "
                         "'all' or a comma list of seat indices")
    args = ap.parse_args(argv)
    seed, world = worldseed.apply(args)

    if args.plan:
        w = World(world, seed)
        doc = plan(w)
        Path(args.out).write_text(json.dumps(doc, indent=1))
        print(f"save {doc['save_fingerprint'][:12]}  "
              f"{doc['seats_total']} seats, {doc['seats_passing']} already "
              f"pass, {len(doc['moves'])} re-seated, "
              f"{len(doc['unresolved'])} unresolved -> {args.out}")
        for mv in doc["moves"]:
            a, bb = mv["before"], mv["after"]
            print(f"  {a['prefab']}#{a['seat']} "
                  f"({a['x']:.3f},{a['y']:.3f},{a['z']:.3f}) -> "
                  f"({bb['x']:.3f},{bb['y']:.3f},{bb['z']:.3f}) "
                  f"{bb['moved_m']:.3f} m | pen {a['penetration_m']:.3f}->"
                  f"{bb['penetration_m']:.3f} clear {a['clearance_m']:.3f}->"
                  f"{bb['clearance_m']:.3f} head {a['headroom_m']:.3f}->"
                  f"{bb['headroom_m']:.3f} drop {a['drop_m']:.3f}->"
                  f"{bb['drop_m']:.3f} reach {a['reach_m']:.3f}->"
                  f"{bb['reach_m']:.3f}")
        for u in doc["unresolved"]:
            print(f"  UNRESOLVED {u['before']['prefab']}"
                  f"#{u['before']['seat']}: {u['why']}")
        return 0
    if args.apply:
        src = Path(args.plan_file or args.out)
        doc = json.loads(src.read_text())
        res = apply_plan(doc, dry=args.dry)
        print(json.dumps(res, indent=1))
        return 0 if not res["dropped"] else 1
    if args.save:
        sys.path.insert(0, str(JUMPSTART / "ledger"))
        from live import LiveBuilder                        # noqa: PLC0415
        doc = json.loads(Path(args.plan_file or args.out).read_text())
        seats = ({int(s) for s in args.save.split(",")}
                 if args.save != "all"
                 else {mv["before"]["seat"] for mv in doc["moves"]})
        with LiveBuilder(actor=ACTOR, dry=args.dry) as b:
            print(json.dumps(save_seats(b, doc, seats), indent=1)[:2000])
        return 0
    ap.error("one of --plan, --apply or --save")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
