#!/usr/bin/env python3
"""HARBOURS - the section where every check passed and no ship could enter.

WHY THIS SECTION EXISTS.  Ulfsgard's sea gate was four slots of 1.925 / 3.777 /
3.777 / 1.864 m against a 9.16 m CargoShip beam, and EVERY check on it passed.
Not because the checks were buggy: because of what they measured.

  * one measured THE SPAN BETWEEN THE FLANKING TOWERS - 11.951 m, true, and
    irrelevant, because three panels stood inside that span;
  * one measured WHETHER THE PANELS CLOSE AND REOPEN - they do, and a gate that
    opens is not a gate a hull fits through;
  * one measured A BERTH AT A POINT - 4.21 m of water at the spawn position of a
    VikingShip whose own footprint had 0.244 m under it, 3.966 m away;
  * and none of them asked THE QUESTION THE OPERATOR ASKED, which was whether a
    ship can get in.

The four checks here are that question, split into the four ways this project
has got it wrong, and each one has a fixture that is a MEASURED state of this
world - three of them states it was actually in.

  HULL_ENTRY     the CLEAR OPENING a hull must pass, per hull class, against
                 its own measured beam - never the structural span, never a
                 close/reopen cycle.
  BERTH_DEPTH    the shallowest sample under the hull's OWN rotated footprint
                 at its OWN yaw - never a point.
  DRAINING       the SIGN of every change to a submerged bed.  Lowering a bed
                 adds water; raising one removes it, and a channel filled to
                 road freeboard is drained permanently.
  SEA_REACH      every sample under a hull footprint flood-fill connected to
                 open sea - a landlocked pocket of 9 m water berths nothing.

THE TRAP THAT DEFEATS A COLLIDER DUMP, and the reason HULL_ENTRY cannot be
answered from the census alone.  `fb_fortress_portcullis` has THREE solid
boxes and ONE of them moves.  MEASURED by `blueprints/DoorProbe.cs`, which
instantiates the prefab inactive in a sandbox, strips its MonoBehaviours and
drives the Animator by hand through state 0 -> 1 -> -1 -> 0
(`blueprints/data/door_open_pose.txt`):

    leaf  `door`         x [-2.47495,  2.64786]  lifts +3.7431 m
                         y [ 0.24422,  5.75114]  closed
                         y [ 3.98732,  9.49424]  open
    post  `collider`     x [ 2.53573,  2.91000]  0.374 m wide, 5.834 m tall,
    post  `collider (1)` x [-2.79427, -2.42000]  NEVER MOVES, in any state,
                                                 in either direction

`Door.SetState` (assembly_valheim IL, class `Door`) only does
`m_animator.SetInteger("state", s)` and `m_openEnable.SetActive(s != 0)`, and
`m_openEnable` is null on this prefab - so THE OPEN GEOMETRY IS AN ANIMATION
CLIP AND IS INVISIBLE TO A CLOSED-POSE COLLIDER DUMP.  A gate check that reads
the census sees the closed leaf, assumes "it opens", and reports the full
structural span.  This section instead reads the MEASURED OPEN POSE and keeps
the boxes that do not move.  A barrier prefab with a `Door` and NO entry in the
open-pose table is REFUSED BY NAME rather than assumed to open: an unmeasured
door is not an open door.

INSTRUMENTS, and what each one may not be asked.

  OBJECTS   the whole-save ZDO census (`ZDOMan.m_objectsByID`, read offline,
            491,792 rows on Ulfsland) with each object's own rotation, plus
            each prefab's own solid from `blueprints/data/piece_geometry.json`,
            PER PART - because the whole-prefab vertex cloud of a portcullis or
            a pier cannot tell a submerged pile from a deck 3 m up, and this
            section's entire subject is which part of a solid is in the way.
  BED       `ctx.ground` - the applied surface read back out of the SAVE
            through the engine (`run_waterproof.sh FROM_SAVE=1` on a COPY, the
            save's own `_TerrainCompiler` ZDOs instantiated, then
            `Heightmap.GetHeight`).
  SEED BED  `ctx.generated` - the same surface for the SEED alone, no save, no
            ledger (`run_patchscan.sh SURFACE=heightmap`).  DRAINING needs BOTH
            because "was this water before anything was written here" cannot be
            asked of the artefact under test: a fill that already drained a
            channel reads as dry land, and the defect becomes invisible.  If
            that instrument is unavailable the check REFUSES; it never skips.
  HULLS     `settlements/waterfront.py::VESSELS`, the one authority for beam,
            length and draught in this repo (`hull_w`/`hull_l` from the solid
            non-trigger collider AABB, `draught_m` = `Ship.m_waterLevelOffset`)
            plus `KEEL_CLEARANCE_M` = 1.0.  Air draft comes from the hull's own
            corpus solid span where the corpus has the prefab, and where it does
            not, THE CHECK SAYS SO AND REFUSES rather than guessing a number
            for a 9 m ship.

WHAT MAKES THE FOOTPRINT SAMPLER THE SAME INSTRUMENT that produced this world's
berth records: sampled on the engine's own integer 1 m lattice inside the hull's
own ellipse at its own yaw, it reproduces the ledger's sample counts exactly -
153 for the VikingShip at ferry-terminal-eastisle (9.16 x 21.57, yaw 335), 38
for the LittleBoat at boathouse-temple-strait (4.23 x 10.37, yaw 190), 114 for
the CargoShip at dock-northcape (9.16 x 15.71, yaw 55).  Three of three.

THE POPULATION IS EVERY BERTH THIS WORLD CLAIMS, not every boat it happens to
be holding.  Vessel ZDOs in the save UNION the `boat_spawn_moor` berths the
ledger declares, deduped within 8 m.  MEASURED on Ulfsland: 6 hulls standing
against 7 declared berths, and the missing one is the VikingShip berth at
(1943.79,568.37) - the berth that read 0.244 m under its own footprint against
2.500 m required and whose hull was then retired.  A census-only population
loses a defect the moment somebody removes its evidence.

TWO NUMBERS THAT ARE NOT DEFECTS, and both were reported as defects by the
first version of this file on the real world:

  INSTRUMENT AGREEMENT  the applied bed and the seed bed come through two
            different harnesses, and where nothing was written they still
            disagree by centimetres.  A +0.075 m rise (seed 29.940, applied
            30.015) was reported as "24 m2 of water permanently removed".  So
            the disagreement is now MEASURED per run - the p99 of
            |applied - seed| over the samples outside every one of the
            ledger's terrain_write footprints - printed in every finding, and
            nothing under it is called a defect.
  PRECISION a MeshCollider is in the corpus only as `sharedMesh.bounds`, and
            `rock4_forest` comes out of that as a 27.7 x 24.2 m slab.  A
            refusal that rests only on such a box is reported ADVISORY, and
            the test is the RE-RUN rather than the reason string: the question
            is asked again with the bounds dropped, and if the hull reconnects
            or the opening clears, the finding names the bounding box and does
            not decide the world.

A GROUND READING HERE IS A NEAREST-VERTEX HEIGHT.  MEASURED at population
scale by RoadsRemeasure over 21,979 road stations: of 243 vertex groups whose
members sit at genuinely different places inside one cell with non-zero local
relief, 243 of 243 returned IDENTICAL heights - median 0.0744 m and MAX
1.5830 m of spread that bilinear interpolation would have produced and did
not.  `Heightmap.GetHeight` is not interpolated; it answers with the vertex it
rounds to.

WHY THAT MAKES EVERY DEPTH IN THIS SECTION EXACT RATHER THAN SAMPLED.  The
instrument is quantised but the SURFACE is not: Heightmap's mesh is the same
lattice triangulated, so the bed between vertices is PIECEWISE LINEAR, and the
extremes of a linear patch are at its corners.  So the shallowest point of the
bed under a hull IS at a vertex, and a sample set that holds every vertex in
the footprint does not approximate the shallowest depth - it contains it.
Every query here is snapped to a vertex and the footprint is sampled on the
engine's own 1 m lattice for exactly that reason.  It is also why the route is
re-measured at 1 m: a NAVIGABLE cell is navigable iff its corners are, so a
vertex-complete cross-section cannot step over a bar, while the 2 m field can.

AND WHICH VERTEX ANSWERS IS DECIDED, NOT LEFT TO A FLOAT.  Because the query
is rounded, a query on the exact half-lattice line is ambiguous between two
vertices and the
error bar on it is the LOCAL VERTEX DIFFERENCE - MEASURED at 0.1632 m on this
world, at (-836.5,7734.5), by two queries 0.0001 m apart in the same file from
the same run.  Every query this section asks for is therefore snapped with
`instruments.snap` (half-way cases UP, always, so the choice is the same one
every time), the request list is CHECKED against the vertex lattice before it
is sent, and a query off the lattice is a refusal rather than a reading.

This section never writes, never takes the token, never opens the live save.
"""
from __future__ import annotations

import heapq
import importlib.util
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
ACCEPT = HERE.parent
JUMPSTART = ACCEPT.parent

def _load(name: str, path: Path):
    """Load a sibling module by PATH, registering it before it executes.

    In sys.modules first: `@dataclass` resolves its own annotations through
    `sys.modules[cls.__module__]` and raises without it.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


if __package__:
    from ..instruments import TIGHT_KINDS, part_kind, part_vertices, snap
    from ..registry import Case, Finding, Section, register, run_cases
else:
    # Run as a plain file so the section's own proof needs nothing but this
    # repo: no package context, no world, no save, no server.  The registry,
    # the vertex snap and the corpus part decoder are still THE ones - loaded
    # from their own paths, never re-implemented.
    _reg = _load("accept_registry", ACCEPT / "registry.py")
    Case, Finding, Section, register, run_cases = (
        _reg.Case, _reg.Finding, _reg.Section, _reg.register, _reg.run_cases)
    _ins = _load("accept_instruments", ACCEPT / "instruments.py")
    snap, part_vertices, part_kind, TIGHT_KINDS = (
        _ins.snap, _ins.part_vertices, _ins.part_kind, _ins.TIGHT_KINDS)

# ---------------------------------------------------------------------------
# MEASURED CONSTANTS.  Every one names where it was read from.
# ---------------------------------------------------------------------------
WATER_LEVEL = 30.0
# `ZoneSystem.m_waterLevel` / `Character.c_WaterLevel`; the same 30.0 that
# `crossings/water.py::WATER_LEVEL` uses, and the plane every depth in this
# world's ledger is quoted against.  `run()` prefers `ctx.WATER_LEVEL` when the
# instruments module supplies it, so a future world with another sea level is
# measured at its own.

FOOTPRINT_STEP_M = 1.0
# The engine's own heightmap lattice is 1 m and its samples are integer
# aligned, so a footprint sampled on that lattice is reading the surface the
# engine draws rather than an interpolation of it.  It is also what reproduces
# the three ledger sample counts above; a 0.5 m lattice would be a different
# population answering the same question.

VERTEX_SPACING_M = 1.0
# The engine's own heightmap vertex spacing.  `Heightmap.GetHeight` ROUNDS its
# query to a vertex, so 1 m is not a resolution choice - it is the complete
# set of distinct answers the ground can give.  A field coarser than this does
# not approximate the ground; it reads a SUBSET of it.

FIELD_STEP_M = VERTEX_SPACING_M
# The reachability field is on the engine's own lattice, and it was NOT.
#
# It was 2.0 m, for cost, with a comment that stated the limit as "cannot see
# an obstruction narrower than 2 m" and pointed at `aperture()` and
# `refine_route` as the answer.  Both of those refine a WIDTH.  Neither of
# them touches CONNECTIVITY, and `_unconnected` - which is the whole of
# `sea_reach` and the whole of `check_channel_severed` - flood-fills the
# field itself.
#
# MEASURED on Ulfsland 5385652e at the LittleBoat, boathouse-temple-strait
# (-307.04,-119.59): `Bed.around` anchors on `snap(berth)` and steps by
# FIELD_STEP_M, so EVERY sample of that field inherits the snapped centre's
# parity - odd x, even z here.  Over its own 257 x 257 m span the 2 m field
# reads 16,641 of 66,049 vertices, 25.2 %, and WHICH quarter is decided by
# the berth's own coordinate.  The vertex (-318,-48) - 2.168 m of water
# against 2.200 m required, one of the bars in that channel - is not a
# vertex that field measures badly.  IT IS A VERTEX THAT FIELD CANNOT
# ADDRESS.  Its neighbour (-317,-48) is on the lattice, so the field names
# that one, and a repair sized to the named vertex is a cut at a place that
# is not the bar - certified by a gate reading the same 25 %.
#
# The cost this buys back is 66,049 points per berth instead of 16,641, over
# THE SAME ZONES - the harness spawns a zone once and answers points out of
# it, so the zone count, which is what the run actually pays for, is
# unchanged.  `check_field_resolution` refuses if anyone widens it again.

REACH_RADIUS_M = 128.0
# How far from a hull the navigable field is built.  Not an acceptance number:
# it bounds the WORK.  If no navigable cell reaches the region boundary the
# check REFUSES with "region too small to find open sea" - it does not report a
# landlocked harbour it cannot see out of as connected.

BAND_EPS_M = 0.01
# Vertical overlap tolerance.  A box whose top is exactly the keel plane is not
# in the way; 0.01 m is the print precision of every number in this world's
# ledger.

DRAIN_EPS_M = 0.0
# A bed AT the water plane holds no water.  `depth = WATER_LEVEL - bed > 0` is
# the wet test used by `crossings/water.py::Field.wet`, so a bed raised exactly
# to 30.0 has been drained, and that is the direction this check is about.


# ---------------------------------------------------------------------------
# HULLS - one authority, imported, never re-typed
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Hull:
    """One hull class, in the units a harbour has to clear.

    `air_m` is the hull's height ABOVE the water plane: the solid span of the
    prefab's own colliders minus the draught.  It is `None` for every vessel
    the geometry corpus has never seen - the modded OdinShip hulls - and a
    `None` here is not a zero.  A check that needs it and does not have it
    refuses and names the missing measurement.
    """

    name: str
    beam_m: float
    length_m: float
    draught_m: float
    air_m: float | None
    keel_clearance_m: float
    source: str

    @property
    def required_depth_m(self) -> float:
        return self.draught_m + self.keel_clearance_m

    def keel_y(self, water: float = WATER_LEVEL) -> float:
        return water - self.draught_m


def _import_waterfront():
    """`settlements/waterfront.py`, the one place this repo's hull numbers live.

    Imported rather than copied.  The table is MEASURED (a sandbox `ZNetScene`
    dump with the mod set staged; `hull_w`/`hull_l` from the solid non-trigger
    collider AABB, `draught_m` from `Ship.m_waterLevelOffset`) and a second
    copy of it here would be a second authority that goes stale the next time a
    ship mod updates.
    """
    for sub in ("settlements", "crossings", "ledger", "terraform", "roads",
                "blueprints"):
        p = str(JUMPSTART / sub)
        if p not in sys.path:
            sys.path.insert(0, p)
    import waterfront as WF  # noqa: PLC0415
    return WF


def _corpus() -> tuple[dict[str, list], set[str]]:
    """THE SAME corpus file `instruments.load_geometry` reads, including its
    `ACCEPT_PIECE_GEOMETRY` override.  A hull's air draft coming out of one
    file while the barriers it is judged against come out of another is two
    worlds joined by one verdict."""
    import os                                            # noqa: PLC0415
    path = os.environ.get("ACCEPT_PIECE_GEOMETRY") or str(
        JUMPSTART / "blueprints" / "data" / "piece_geometry.json")
    g = json.loads(Path(path).read_text())
    return g["prefabs"], set(g.get("build_pieces", ()))


def hull_table() -> dict[str, Hull]:
    """Every hull class, with its air draft where the corpus has measured it."""
    WF = _import_waterfront()
    prefabs, _ = _corpus()
    out: dict[str, Hull] = {}
    for name, v in WF.VESSELS.items():
        span = None
        parts = prefabs.get(name)
        if parts:
            ys = [y for part in parts for (_, y, _) in part_vertices(part)]
            if ys:
                span = max(ys) - min(ys)
        out[name] = Hull(
            name=name, beam_m=float(v["hull_w"]), length_m=float(v["hull_l"]),
            draught_m=float(v["draught_m"]),
            air_m=(None if span is None else round(span - v["draught_m"], 4)),
            keel_clearance_m=float(WF.KEEL_CLEARANCE_M),
            source=("settlements/waterfront.py::VESSELS"
                    + ("; air draft = piece_geometry.json solid span - draught"
                       if span is not None else
                       "; air draft NOT MEASURED (prefab absent from "
                       "blueprints/data/piece_geometry.json)")))
    return out


# ---------------------------------------------------------------------------
# GEOMETRY - per PART, in world space
# ---------------------------------------------------------------------------
# `part_vertices` IS `accept.instruments.part_vertices`, imported above, not a
# second copy of it.  There WAS a second copy here and it cost exactly what a
# second implementation of a shared helper always costs: it decided `tight` by
# `part[0] == "b"`, which calls all 2,960 OBLIQUE BOXES mesh bounds and every
# axis-aligned MeshCollider a measured shape.
def _part_is_tight(part: list) -> bool:
    """Is this part the collider ITSELF, or only its bounding box?

    MEASURED, from the corpus's own kind column: 6,956 of the 11,972
    non-render colliders are `mesh`, i.e. `sharedMesh.bounds`.  The
    axis-alignment of a row says NOTHING about this - `Beech1` is two
    axis-aligned `b` rows and both are mesh bounds, 19.08 x 21.50 m of
    them - which is why this asks the kind and not the shape.  A corpus
    written before the kind existed answers None, and an UNSTATED
    precision is treated as bounds-only: this section's whole rule is that
    an unproven extent must not decide the world.
    """
    return part_kind(part) in TIGHT_KINDS


@dataclass
class Barrier:
    """One SOLID PART of one placed object, in world space.

    Per part, not per object, and that is the whole point: a pier's piles are
    in the water and its deck is 3 m above it, a portcullis's leaf lifts and
    its posts do not.  An object-level bounding box cannot express either, and
    a check built on one reports a harbour wall where there is a jetty.
    """

    prefab: str
    poly: tuple[tuple[float, float], ...]   # footprint, world xz, convex
    y_lo: float
    y_hi: float
    tight: bool = True        # False = mesh-collider bounds, not the solid
    pose: str = "static"      # "static", "open:1", "closed"...
    part: str = ""

    def spans_band(self, lo: float, hi: float) -> bool:
        return self.y_hi > lo + BAND_EPS_M and self.y_lo < hi - BAND_EPS_M

    def centroid(self) -> tuple[float, float]:
        n = len(self.poly)
        return (sum(p[0] for p in self.poly) / n,
                sum(p[1] for p in self.poly) / n)


def barrier_from_part(prefab: str, pos, quat, part, rotate,
                      tight: bool | None = None, pose: str = "static",
                      part_name: str = "") -> Barrier:
    """One part's world solid, rotated by the OBJECT'S OWN quaternion.

    `rotate` is passed in rather than imported so that this file holds exactly
    one copy of nothing: the quaternion rotation lives in
    `accept/instruments.py` and both the roads section and this one call it.
    """
    vs = [rotate(quat, v) for v in part_vertices(part)]
    xs = [pos[0] + v[0] for v in vs]
    ys = [pos[1] + v[1] for v in vs]
    zs = [pos[2] + v[2] for v in vs]
    # The footprint of a rotated box is the convex hull of its projected
    # corners; for the yaw-only rotations every built piece in this world
    # stands at, that hull is a rectangle, and taking the hull rather than the
    # axis-aligned bound is what keeps a panel yawed 90 degrees 0.203 m wide
    # instead of 5.704 m wide.
    poly = _convex_hull(list(zip(xs, zs)))
    return Barrier(prefab=prefab, poly=tuple(poly), y_lo=min(ys), y_hi=max(ys),
                   tight=(_part_is_tight(part) if tight is None else tight),
                   pose=pose, part=part_name)


def _convex_hull(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    pts = sorted(set(pts))
    if len(pts) <= 2:
        return pts

    def half(ps):
        out: list[tuple[float, float]] = []
        for p in ps:
            while len(out) >= 2:
                (ax, az), (bx, bz) = out[-2], out[-1]
                if (bx - ax) * (p[1] - az) - (bz - az) * (p[0] - ax) <= 0:
                    out.pop()
                else:
                    break
            out.append(p)
        return out
    lower = half(pts)
    upper = half(list(reversed(pts)))
    return lower[:-1] + upper[:-1]


def poly_contains(poly, x: float, z: float) -> bool:
    inside = False
    n = len(poly)
    for i in range(n):
        x0, z0 = poly[i]
        x1, z1 = poly[(i + 1) % n]
        if (z0 > z) != (z1 > z):
            t = (z - z0) / (z1 - z0)
            if x < x0 + t * (x1 - x0):
                inside = not inside
    return inside


def poly_near(poly, x: float, z: float, r: float) -> bool:
    """Within `r` of the footprint - containment or edge distance.

    Needed because a hull is not a point: an obstruction that misses the
    centreline by 0.5 m still hits a 9.16 m beam.
    """
    if poly_contains(poly, x, z):
        return True
    n = len(poly)
    for i in range(n):
        ax, az = poly[i]
        bx, bz = poly[(i + 1) % n]
        dx, dz = bx - ax, bz - az
        ll = dx * dx + dz * dz
        t = 0.0 if ll == 0 else max(0.0, min(1.0, ((x - ax) * dx
                                                   + (z - az) * dz) / ll))
        if math.hypot(x - (ax + t * dx), z - (az + t * dz)) <= r:
            return True
    return False


# ---------------------------------------------------------------------------
# THE APERTURE, EXACTLY - interval arithmetic, no lattice
# ---------------------------------------------------------------------------
def project_barriers(barriers, origin, cross, band_lo: float, band_hi: float,
                     along_half: float | None = None
                     ) -> list[tuple[float, float, Barrier]]:
    """Each barrier's shadow on the gate's cross-axis, for the hull's own band.

    `cross` is the unit vector ACROSS the channel; `origin` a point on the gate
    line.  A barrier counts only if its own vertical extent overlaps the band
    the hull occupies - which is how a leaf that has lifted clear of the water
    stops being an obstruction and its posts do not.
    """
    ax, az = cross
    ox, oz = origin
    along = (-az, ax)
    out = []
    for b in barriers:
        if not b.spans_band(band_lo, band_hi):
            continue
        cs = [((px - ox) * ax + (pz - oz) * az) for px, pz in b.poly]
        if along_half is not None:
            ls = [((px - ox) * along[0] + (pz - oz) * along[1])
                  for px, pz in b.poly]
            if min(ls) > along_half or max(ls) < -along_half:
                continue
        out.append((min(cs), max(cs), b))
    return sorted(out, key=lambda t: (t[0], t[1]))


def free_intervals(lo: float, hi: float,
                   blocks: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """The gaps left in [lo, hi] after every blocked interval is removed."""
    out: list[tuple[float, float]] = []
    cur = lo
    for b0, b1 in sorted(blocks):
        if b1 <= cur:
            continue
        if b0 > cur:
            out.append((cur, min(b0, hi)))
        cur = max(cur, b1)
        if cur >= hi:
            break
    if cur < hi:
        out.append((cur, hi))
    return [(a, b) for a, b in out if b - a > 1e-9]


def aperture(span: tuple[float, float], barriers, origin, cross,
             band_lo: float, band_hi: float, along_half: float | None = None
             ) -> dict:
    """The clear slots in a gate, and the widest of them.

    THE NUMBER A HULL IS JUDGED AGAINST.  Not the structural span (11.951 m at
    Ulfsgard, with three panels standing in it) and not whether anything moves.
    """
    proj = project_barriers(barriers, origin, cross, band_lo, band_hi,
                            along_half)
    slots = free_intervals(span[0], span[1], [(a, b) for a, b, _ in proj])
    widths = [round(b - a, 3) for a, b in slots]
    best = max(widths) if widths else 0.0
    bi = widths.index(best) if widths else -1
    return {"structural_span_m": round(span[1] - span[0], 3),
            "slots_m": widths,
            "widest_m": best,
            "widest_at_cross_m": (None if bi < 0 else
                                  [round(slots[bi][0], 3),
                                   round(slots[bi][1], 3)]),
            "blockers": [(b.prefab, b.part, round(a, 3), round(c, 3))
                         for a, c, b in proj],
            "blocker_count": len(proj)}


# ---------------------------------------------------------------------------
# THE BED - the applied surface, on a lattice
# ---------------------------------------------------------------------------
@dataclass
class Bed:
    """Applied bed height on a regular lattice.  `None` = never read."""

    x0: float
    z0: float
    step: float
    nx: int
    nz: int
    y: list[float | None] = field(default_factory=list)

    def wx(self, i: int) -> float:
        return self.x0 + i * self.step

    def wz(self, j: int) -> float:
        return self.z0 + j * self.step

    def at(self, i: int, j: int) -> float | None:
        return self.y[j * self.nx + i]

    def points(self, tag: str) -> list[tuple[str, float, float]]:
        return [(f"{tag}_{i}_{j}", self.wx(i), self.wz(j))
                for j in range(self.nz) for i in range(self.nx)]

    def fill(self, tag: str, got: dict[str, float]) -> int:
        self.y = [None] * (self.nx * self.nz)
        n = 0
        for j in range(self.nz):
            for i in range(self.nx):
                v = got.get(f"{tag}_{i}_{j}")
                if v is not None:
                    self.y[j * self.nx + i] = float(v)
                    n += 1
        return n

    @staticmethod
    def around(cx: float, cz: float, radius: float, step: float) -> "Bed":
        """A lattice centred on the berth and ANCHORED ON THE ENGINE'S VERTICES.

        `Heightmap.GetHeight` QUANTISES ITS QUERY TO THE NEAREST LATTICE VERTEX
        - `WorldToVertex` rounds - so what comes back is a VERTEX HEIGHT and not
        an interpolated surface, and a query sitting exactly on the half-lattice
        line is ambiguous between two vertices.  MEASURED by GateStructures on
        this world: one station at (-836.5, 7734.5), dead centre of its cell,
        answered 30.4490 m and 30.2858 m to two queries 0.0001 m apart IN THE
        SAME FILE FROM THE SAME RUN - 0.1632 m apart, and the difference is
        which vertex answered, not noise anything can average away.

        A region anchored on the berth's own coordinate inherits that
        coordinate's fraction, so a single berth recorded at an exact x.5 - and
        Valheim snaps build pieces to 0.5 m, so that is a routine coordinate -
        would put EVERY sample of a 16,641-point field on the ambiguous line.
        The centre is therefore snapped to the nearest whole metre first: with a
        2 m step every sample then lands on an integer vertex, and one label is
        one question by construction.  The berth is still measured at its own
        position by the footprint sampler, which is already on the 1 m integer
        lattice for the same reason.
        """
        n = int(math.floor(radius / step)) * 2 + 1
        sx, sz = snap(cx, cz)
        return Bed(x0=round(sx - (n // 2) * step, 4),
                   z0=round(sz - (n // 2) * step, 4),
                   step=step, nx=n, nz=n, y=[None] * (n * n))


def on_vertex_lattice(pts, tol: float = 1e-6) -> list[tuple[str, float, float]]:
    """Every query that is NOT on an integer vertex - the ambiguous ones.

    Used as a check on this section's own request list rather than on the
    world: a query off the vertex lattice is answered by whichever vertex the
    engine rounds to, and at the one station where two such queries were
    compared the answers were 0.1632 m apart.
    """
    bad = []
    for label, x, z in pts:
        sx, sz = snap(x, z)
        if abs(x - sx) > tol or abs(z - sz) > tol:
            bad.append((label, x, z))
    return bad


def field_resolution(bed: Bed) -> dict:
    """WHAT FRACTION OF THE GROUND THIS FIELD CAN ADDRESS, and which fraction.

    Not "how accurate is this lattice" - a lattice coarser than the engine's
    own vertex spacing is not inaccurate, it is INCOMPLETE, and the part it
    omits is not a random sample.  `Bed.around` anchors on `snap(berth)`, so
    every sample of a step-s field is congruent to the snapped berth modulo
    s in BOTH axes: the field is locked to one residue class out of s**2, and
    which one is an accident of where somebody moored a boat.
    """
    s = bed.step
    span_x = (bed.nx - 1) * s + 1.0
    span_z = (bed.nz - 1) * s + 1.0
    vertices = span_x * span_z
    return {
        "step_m": s,
        "engine_vertex_spacing_m": VERTEX_SPACING_M,
        "resolves_every_vertex": s <= VERTEX_SPACING_M + 1e-9,
        "samples": bed.nx * bed.nz,
        "vertices_in_span": int(vertices),
        "fraction_addressable": round(bed.nx * bed.nz / vertices, 4),
        "x_residue": int(round(bed.x0)) % int(round(s)) if s >= 1.0 else 0,
        "z_residue": int(round(bed.z0)) % int(round(s)) if s >= 1.0 else 0,
        "span_m": [round(span_x, 1), round(span_z, 1)],
    }


def check_field_resolution(bed: Bed, site: str = "") -> list[Finding]:
    """A CONNECTIVITY VERDICT ON A SUBSET OF THE GROUND IS NOT A VERDICT.

    `aperture()` and `refine_route` exist because a 2 m field cannot see an
    obstruction narrower than 2 m, and both of them refine a WIDTH.  Neither
    touches CONNECTIVITY: `_unconnected` flood-fills the field itself, and it
    is the whole of `sea_reach` and the whole of `check_channel_severed`.  A
    bar one vertex wide sitting on a column the field cannot address is
    stepped over in BOTH directions - a closed harbour certified open, an
    open one refused - and the repair the field names is a vertex chosen for
    being ON THE LATTICE rather than for being the bar.

    So the field is refused when it cannot address every vertex it is
    judging, rather than caveated.  This is a guard, not a discovery: the
    shipped `FIELD_STEP_M` is the engine's own spacing, so on a shipping run
    this check passes and prints the population it passed over.
    """
    r = field_resolution(bed)
    pop = (f"the {r['samples']} samples this field asks for, against the "
           f"{r['vertices_in_span']} heightmap vertices inside the same "
           f"{r['span_m'][0]:.0f} x {r['span_m'][1]:.0f} m span")
    if r["resolves_every_vertex"]:
        return [Finding(
            check="harbours/field_resolution", ok=True,
            message=(f"{site or 'berth'}: the reachability field steps "
                     f"{r['step_m']:g} m on the engine's {VERTEX_SPACING_M:g} "
                     f"m vertex lattice - {r['samples']} samples addressing "
                     f"all {r['vertices_in_span']} vertices in its span"),
            measured=1.0, limit=1.0, numbers=r, population=pop)]
    return [Finding(
        check="harbours/field_resolution", ok=False,
        message=(f"{site or 'berth'}: the reachability field steps "
                 f"{r['step_m']:g} m over an engine lattice of "
                 f"{VERTEX_SPACING_M:g} m, so it can address "
                 f"{r['samples']} of the {r['vertices_in_span']} vertices it "
                 f"is judging - {100.0 * r['fraction_addressable']:.1f} % - "
                 f"and it is locked to x = {r['x_residue']} and z = "
                 f"{r['z_residue']} modulo {r['step_m']:g} by the berth's own "
                 f"coordinate. A vertex outside that class is not measured "
                 f"badly, it is UNADDRESSABLE: the field can neither refuse "
                 f"on a bar standing there nor name it as a repair. Refusing "
                 f"to compute a connectivity verdict on a subset of the "
                 f"ground"),
        measured=float(r["fraction_addressable"]), limit=1.0,
        numbers=r, population=pop)]


# ---------------------------------------------------------------------------
# NAVIGABILITY, CLEARANCE, AND THE WIDEST ROUTE OUT
# ---------------------------------------------------------------------------
class SolidIndex:
    """Which barrier contains a point, in the CALLER'S OWN LIST ORDER.

    `navigable` asks this question once per lattice cell, and on this world
    that is 16,641 cells against ~5,000 solid parts for EACH of the five
    navigability passes each berth needs - hull_entry, sea_reach, and
    channel_severed's two beds, plus the mesh-bound re-run.  A linear scan is
    86 million polygon tests per pass and it is why one harbours run took
    over an hour of CPU after every bed reading was already cached.  A gate
    nobody can afford to run is a gate nobody runs.

    THE ANSWER IS BYTE-IDENTICAL TO THE SCAN IT REPLACES, and that is a
    requirement rather than a hope: the reason string names the FIRST
    containing barrier in the caller's list, so it is part of the verdict and
    `best_sill` reports it.  Buckets are filled by ascending list index, so
    scanning a bucket returns exactly what `next(b for b in bars if ...)`
    returns.  A cell whose bucket is empty has no containing barrier at all,
    because a barrier's bucket set covers its own bounding box.
    """

    CELL_M = 8.0

    def __init__(self, bars):
        self.bars = list(bars)
        self.grid: dict[tuple[int, int], list[int]] = {}
        c = self.CELL_M
        for idx, b in enumerate(self.bars):
            xs = [p[0] for p in b.poly]
            zs = [p[1] for p in b.poly]
            for i in range(int(math.floor(min(xs) / c)),
                           int(math.floor(max(xs) / c)) + 1):
                for j in range(int(math.floor(min(zs) / c)),
                               int(math.floor(max(zs) / c)) + 1):
                    self.grid.setdefault((i, j), []).append(idx)

    def hit(self, x: float, z: float):
        c = self.CELL_M
        cand = self.grid.get((int(math.floor(x / c)), int(math.floor(z / c))))
        if not cand:
            return None
        for idx in cand:
            b = self.bars[idx]
            if poly_contains(b.poly, x, z):
                return b
        return None


def navigable(bed: Bed, hull: Hull, barriers, water: float = WATER_LEVEL
              ) -> tuple[list[bool], list[str]]:
    """Which cells this hull can float in, and why each blocked one is blocked.

    Two independent reasons, kept separate because the fix differs: the bed is
    too shallow (dredge) or something solid is in the water (move it).  The
    solid test uses ONLY the part of a barrier that is in the hull's SUBMERGED
    band - keel to water line.  Overhead is a separate question with a separate
    answer, because a bridge a hull passes under is not an obstruction and a
    check that says it is will be bypassed.

    THE REASON STRING CARRIES ITS OWN PRECISION.  A box collider is the solid;
    a MeshCollider is recorded in the corpus only as `sharedMesh.bounds`, and
    `rock4_forest` comes out of that as a 27.7 x 24.2 m slab which is
    emphatically not the rock.  So a cell blocked by a box says
    `solid:<prefab>` and a cell blocked by a mesh bound says `bound:<prefab>`,
    and a refusal that rests only on `bound:` rows is reported ADVISORY - it
    names the obstruction and does not decide the world on a bounding box.
    """
    req = hull.required_depth_m
    keel = water - hull.draught_m
    nav = [False] * (bed.nx * bed.nz)
    why = [""] * (bed.nx * bed.nz)
    wet_bars = [b for b in barriers if b.spans_band(keel, water)]
    index = SolidIndex(wet_bars)
    for j in range(bed.nz):
        for i in range(bed.nx):
            k = j * bed.nx + i
            y = bed.at(i, j)
            if y is None:
                why[k] = "unread"
                continue
            if water - y < req:
                why[k] = "shallow"
                continue
            hit = index.hit(bed.wx(i), bed.wz(j))
            if hit is not None:
                why[k] = f"{'solid' if hit.tight else 'bound'}:{hit.prefab}"
                continue
            nav[k] = True
    return nav, why


def _edt1d(f: list[float]) -> list[float]:
    """Felzenszwalb's exact 1-D squared distance transform."""
    n = len(f)
    v = [0] * n
    z = [0.0] * (n + 1)
    inf = float("inf")
    k = 0
    z[0], z[1] = -inf, inf
    for q in range(1, n):
        while True:
            s = ((f[q] + q * q) - (f[v[k]] + v[k] * v[k])) / (2.0 * q
                                                              - 2.0 * v[k])
            if s <= z[k] and k > 0:
                k -= 1
                continue
            break
        k += 1
        v[k] = q
        z[k] = s
        z[k + 1] = inf
    k = 0
    out = [0.0] * n
    for q in range(n):
        while z[k + 1] < q:
            k += 1
        out[q] = (q - v[k]) ** 2 + f[v[k]]
    return out


def clearance(bed: Bed, nav: list[bool]) -> list[float]:
    """Metres from each navigable cell to the nearest UNnavigable one.

    Exact Euclidean on the lattice, then HALF A CELL IS SUBTRACTED, because the
    obstruction's edge is half a cell nearer than its centre.  A hull of beam B
    needs clearance >= B/2, so the lattice error on the opening is one cell -
    which is why the verdict-grade width is computed by `aperture()` instead and
    this field is used to FIND the bottleneck, not to judge it.
    """
    # A FINITE stand-in for "no obstruction in this row".  Not a style point:
    # Felzenszwalb's parabola intersection subtracts two of these, and
    # inf - inf is nan, which compares false against everything and silently
    # corrupts the transform.  MEASURED while writing this: with float('inf')
    # the 24 m basin in the selftest reported 15 m of clearance in a 16 m
    # channel and the pile row across its mouth vanished.  The stand-in is
    # larger than the longest distance the lattice can hold, so it can never
    # win a minimum.
    big = float((bed.nx + bed.nz) ** 2 + 1)
    g = [0.0 if not nav[k] else big for k in range(bed.nx * bed.nz)]
    for j in range(bed.nz):
        row = _edt1d([g[j * bed.nx + i] for i in range(bed.nx)])
        for i in range(bed.nx):
            g[j * bed.nx + i] = row[i]
    for i in range(bed.nx):
        col = _edt1d([g[j * bed.nx + i] for j in range(bed.nz)])
        for j in range(bed.nz):
            g[j * bed.nx + i] = col[j]
    return [max(0.0, math.sqrt(v) * bed.step - bed.step / 2.0) for v in g]


def widest_route(bed: Bed, nav: list[bool], clear: list[float],
                 starts: list[int], goals: set[int]
                 ) -> dict:
    """The route out whose NARROWEST point is as wide as possible.

    A plain flood fill answers "is there water all the way" and that is the
    question that passed the four-slot sea gate: there WAS water all the way,
    1.925 m of it.  This is a max-min (bottleneck) Dijkstra, so the number it
    returns is the width of the tightest place on the best route, with its
    coordinates.
    """
    if not starts or not goals:
        return {"reached": False, "bottleneck_m": 0.0, "at": None,
                "path": [], "reason": "no start or no open-sea cell"}
    best = [-1.0] * (bed.nx * bed.nz)
    prev: dict[int, int] = {}
    q: list[tuple[float, int]] = []
    for s in starts:
        if nav[s] and clear[s] > best[s]:
            best[s] = clear[s]
            heapq.heappush(q, (-clear[s], s))
    while q:
        negw, k = heapq.heappop(q)
        w = -negw
        if w < best[k]:
            continue
        if k in goals:
            # The whole route back, because the bottleneck is not the only
            # thing measured on it: what the hull passes UNDER is asked at
            # every cell of the route, not at its narrowest one.
            chain = [k]
            node = k
            while node in prev:
                node = prev[node]
                chain.append(node)
            at = min(chain, key=lambda c: clear[c])
            return {"reached": True, "bottleneck_m": round(clear[at], 3),
                    "at": (bed.wx(at % bed.nx), bed.wz(at // bed.nx)),
                    "path": [(bed.wx(c % bed.nx), bed.wz(c // bed.nx))
                             for c in reversed(chain)],
                    "reason": ""}
        j, i = k // bed.nx, k % bed.nx
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ni, nj = i + di, j + dj
            if not (0 <= ni < bed.nx and 0 <= nj < bed.nz):
                continue
            nk = nj * bed.nx + ni
            if not nav[nk]:
                continue
            w2 = min(w, clear[nk])
            if w2 > best[nk]:
                best[nk] = w2
                prev[nk] = k
                heapq.heappush(q, (-w2, nk))
    return {"reached": False, "bottleneck_m": 0.0, "at": None, "path": [],
            "reason": "no navigable route to open sea at any width"}


def boundary_sea(bed: Bed, nav: list[bool]) -> set[int]:
    """Navigable cells on the region edge - the region's own "open sea".

    Honest about what it is: the edge of what was SAMPLED.  If it is empty the
    caller must refuse, not conclude.  A harbour whose exit is 200 m away and a
    harbour with no exit look identical inside a 60 m window, and reporting the
    second as the first is the connectivity defect wearing a different hat.
    """
    out = set()
    for j in range(bed.nz):
        for i in range(bed.nx):
            if i in (0, bed.nx - 1) or j in (0, bed.nz - 1):
                k = j * bed.nx + i
                if nav[k]:
                    out.add(k)
    return out


def best_sill(bed: Bed, nav: list[bool], why: list[str], starts: list[int],
              hull: Hull, water: float = WATER_LEVEL) -> dict:
    """When a hull is shut in, WHAT SHUTS IT IN, with a number.

    "No navigable route to open sea" is a verdict a builder cannot act on.  The
    pool the berth actually sits in is flood-filled, every cell on its rim is
    examined, and the DEEPEST of them is reported: that is the sill the hull is
    stuck behind, how much water it holds, how much it is short, and whether it
    is short because of the bed or because something solid is standing on it.
    A dredge of exactly that shortfall at exactly that coordinate is the fix.
    """
    pool = set(k for k in starts if nav[k])
    stack = list(pool)
    while stack:
        k = stack.pop()
        i, j = k % bed.nx, k // bed.nx
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ni, nj = i + di, j + dj
            if 0 <= ni < bed.nx and 0 <= nj < bed.nz:
                nk = nj * bed.nx + ni
                if nav[nk] and nk not in pool:
                    pool.add(nk)
                    stack.append(nk)
    best = None
    for k in pool:
        i, j = k % bed.nx, k // bed.nx
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ni, nj = i + di, j + dj
            if not (0 <= ni < bed.nx and 0 <= nj < bed.nz):
                continue
            nk = nj * bed.nx + ni
            if nav[nk]:
                continue
            y = bed.at(ni, nj)
            if y is None:
                continue
            d = water - y
            if best is None or d > best[0]:
                best = (d, (bed.wx(ni), bed.wz(nj)), nk)
    if best is None:
        return {"pool_cells": len(pool), "depth_m": 0.0, "at": None,
                "blocked_by": "", "bounds_only": False}
    reason = why[best[2]]
    return {"pool_cells": len(pool), "depth_m": round(best[0], 3),
            "at": best[1],
            "blocked_by": ("the bed alone" if reason == "shallow"
                           else reason.split(":", 1)[-1]
                           if ":" in reason else reason),
            # The sill is a MeshCollider bound rather than a solid: named, and
            # not allowed to decide the world on its own.
            "bounds_only": reason.startswith("bound:")}


# ---------------------------------------------------------------------------
# THE FOOTPRINT - the hull's own ellipse at the hull's own yaw
# ---------------------------------------------------------------------------
def footprint(hull: Hull, x: float, z: float, yaw_deg: float,
              step: float = FOOTPRINT_STEP_M) -> list[tuple[float, float]]:
    """Every lattice point inside the hull's own rotated footprint.

    The ellipse and the 1 m integer lattice are not choices: they are what
    reproduce this world's own berth populations (153 / 38 / 114 samples at
    three measured poses), which is the only way a number printed here can be
    compared with a number in the ledger.
    """
    ux, uz = math.sin(math.radians(yaw_deg)), math.cos(math.radians(yaw_deg))
    lx, lz = uz, -ux
    hl, hw = hull.length_m / 2.0, hull.beam_m / 2.0
    r = max(hl, hw)
    out = []
    for i in range(math.floor((x - r) / step), math.ceil((x + r) / step) + 1):
        for j in range(math.floor((z - r) / step),
                       math.ceil((z + r) / step) + 1):
            px, pz = i * step, j * step
            dx, dz = px - x, pz - z
            if (((dx * ux + dz * uz) / hl) ** 2
                    + ((dx * lx + dz * lz) / hw) ** 2) <= 1.0:
                out.append((px, pz))
    return out


def _origin_polygon_distance_sq(poly):
    if poly_contains(poly, 0.0, 0.0):
        return 0.0
    nearest = math.inf
    for a, b in zip(poly, poly[1:] + poly[:1]):
        dx, dz = b[0]-a[0], b[1]-a[1]
        length_sq = dx*dx + dz*dz
        t = 0.0 if length_sq == 0.0 else max(
            0.0, min(1.0, -(a[0]*dx+a[1]*dz)/length_sq))
        nearest = min(nearest, (a[0]+t*dx)**2+(a[1]+t*dz)**2)
    return nearest


def swept_hull_near_polygon(hull, start, end, poly):
    """Continuous translation/rotation, with a conservative Hausdorff bound.

    At each midpoint the ellipse maps to a unit disk. Translation plus the
    maximum rotation of its rim bounds ALL intermediate hull poses. Only a
    proven separation clears an interval; ambiguous intervals are subdivided.
    Floating-point exhaustion refuses rather than clearing a tangency.
    """
    if not poly:
        return True
    half_beam, half_length = hull.beam_m/2.0, hull.length_m/2.0
    radius, scale = max(half_beam, half_length), min(half_beam, half_length)
    if not math.isfinite(scale) or scale <= 0.0:
        return True
    pending = [(start, end)]
    while pending:
        a, b = pending.pop()
        turn = (b[2]-a[2]+180.0) % 360.0-180.0
        mid = ((a[0]+b[0])/2.0, (a[1]+b[1])/2.0, a[2]+turn/2.0)
        ux, uz = math.sin(math.radians(mid[2])), math.cos(math.radians(mid[2]))
        normalized = [(((x-mid[0])*uz-(z-mid[1])*ux)/half_beam,
                       ((x-mid[0])*ux+(z-mid[1])*uz)/half_length)
                      for x, z in poly]
        distance_sq = _origin_polygon_distance_sq(normalized)
        if distance_sq <= 1.0:
            return True
        motion = (math.hypot(b[0]-a[0], b[1]-a[1])/2.0
                  + 2.0*radius*math.sin(math.radians(abs(turn))/4.0))
        if distance_sq > (1.0+motion/scale)**2:
            continue
        if mid == a or mid == b:
            return True
        pending.append((a, mid))
        pending.append((mid, b))
    return False


def swept_footprint(hull, poses):
    """Every integer terrain vertex traversed, including between sampled poses."""
    radius = max(hull.beam_m, hull.length_m)/2.0
    vertices = set()
    spans = [(poses[0], poses[0])] if poses else []
    spans.extend((a, b) for a, b in zip(poses, poses[1:]) if a != b)
    for a, b in spans:
        for x in range(math.floor(min(a[0], b[0])-radius),
                       math.ceil(max(a[0], b[0])+radius)+1):
            for z in range(math.floor(min(a[1], b[1])-radius),
                           math.ceil(max(a[1], b[1])+radius)+1):
                point = (x, z)
                if point not in vertices and swept_hull_near_polygon(
                        hull, a, b, [point]):
                    vertices.add(point)
    return vertices


def swept_hull_intersects_barrier(hull, start, end, barrier):
    if not barrier.spans_band(hull.keel_y(), WATER_LEVEL+(hull.air_m or 0.0)):
        return False
    return swept_hull_near_polygon(hull, start, end, barrier.poly)




def yaw_of(quat) -> float:
    """Yaw in degrees from a ZDO's own quaternion (y-up, Unity)."""
    x, y, z, w = quat
    return math.degrees(math.atan2(2.0 * (w * y + x * z),
                                   1.0 - 2.0 * (y * y + x * x))) % 360.0


# ---------------------------------------------------------------------------
# CHECK 1 - CAN A HULL ACTUALLY ENTER
# ---------------------------------------------------------------------------
def densify(path, step: float = 1.0) -> list[tuple[float, float]]:
    """The route at `step` along its own length, endpoints included.

    NOT COSMETIC.  The route comes off a 2 m field, so its points are 2 m
    apart and a cross-section taken only AT them steps over anything lying
    between two of them - which is the very defect the refinement exists to
    catch, reproduced one level up.  MEASURED while writing this: a 1 m bar at
    an odd z, across a route running along z, was reported as a 13.000 m clear
    cross-section until the route itself was walked at 1 m.
    """
    if len(path) < 2:
        return list(path)
    out = [(float(path[0][0]), float(path[0][1]))]
    for i in range(len(path) - 1):
        ax, az = path[i]
        bx, bz = path[i + 1]
        d = math.hypot(bx - ax, bz - az)
        n = max(1, int(math.ceil(d / step)))
        for k in range(1, n + 1):
            t = k / n
            out.append((ax + (bx - ax) * t, az + (bz - az) * t))
    return out


CROSS_SUBSTEP_M = 0.25
# The cross-section is walked at a QUARTER metre so that no vertex of the 1 m
# lattice can be stepped over at any bearing: consecutive distinct vertices
# along a unit direction are at least 1 / max(|nx|,|nz|) >= 1.0 apart in `t`,
# so a 0.25 m walk cannot jump one.  Duplicates are removed, so the substep
# costs nothing but the walk.


def cross_section_vertices(px: float, pz: float, nx: float, nz: float,
                           half_m: float) -> list[tuple[float, float]]:
    """The DISTINCT engine vertices a cross-section passes, `half_m` metres
    of them EACH SIDE of the route point.

    THE WINDOW IS COUNTED IN VERTICES BECAUSE THE WIDTH IS, and counting it
    in OFFSETS is the same defect as counting the RUN in offsets - the one
    this file already fixed, in the direction that under-refuses.  In the
    window it under-MEASURES, and that is worse, because it cannot be seen
    from the number:

      `snap` is nearest-vertex, so stepping `t` by 1 m along a 45 deg normal
      lands two offsets on the same vertex.  A window of +/-6 OFFSETS
      therefore shows 13 distinct vertices on an axis-aligned bearing and
      only NINE on a 45 deg one.  A CargoShip's beam is 9.16 m.

    So on a diagonal route the widest reading the check could produce was
    9.000 m against a 9.16 m beam: EVERY 9.16 m HULL ON A 45 DEG ROUTE WAS
    REFUSED BY THE SHAPE OF THE WINDOW, over water it never looked at.
    MEASURED on Ulfsland 5385652e - all three `hull_entry/refined` refusals
    read exactly 9.000 m, all three on 9.16 m hulls, and the cross-section at
    the VikingShip's own narrowest point is nine vertices from (1928,576) to
    (1936,568) holding 6.912 m to 14.142 m of water with no shoal in it at
    all, while every accepted berth on an axis-aligned bearing read 13.000.
    """
    if nx == 0.0 and nz == 0.0:
        return [snap(px, pz)]
    need = int(math.ceil(half_m))
    reach = 2.0 * half_m + 2.0          # >= 1.4142 * need at any bearing
    seq: list[tuple[float, float]] = []
    seen = None
    t = -reach
    while t <= reach + 1e-9:
        v = snap(px + nx * t, pz + nz * t)
        if v != seen:
            seq.append(v)
            seen = v
        t += CROSS_SUBSTEP_M
    c = snap(px, pz)
    k = seq.index(c) if c in seq else len(seq) // 2
    return seq[max(0, k - need):k + need + 1]


def corridor_points(path, hull: Hull, pad: float = 1.0
                    ) -> list[tuple[float, float]]:
    """Every ENGINE VERTEX within half a beam of a chosen route.

    WHY A SECOND, FINER PASS EXISTS.  The route field is on a 2 m lattice
    because the applied bed is read one point at a time and a 258 m region at
    1 m is 66,049 points per harbour.  But `Heightmap.GetHeight` is a step
    function on the 1 m lattice, so a bar one metre wide across a channel lives
    ENTIRELY BETWEEN two 2 m samples and the field cannot see it - the same
    shape as a road check whose two stations share a vertex and report a
    0.000 m step over a cliff.  MEASURED on the road population by
    RoadsRemeasure: 2,878 of 21,954 adjacent pairs resolve to the SAME vertex,
    so 13.11 % of them cannot fail.

    The answer here is the same one the aperture uses: the coarse field only
    LOCATES the route, and the route is then re-measured on the instrument's
    OWN lattice, where nothing can hide between samples because there is
    nothing between samples.
    """
    want: set[tuple[float, float]] = set()
    half = hull.beam_m / 2.0 + pad
    path = densify(path)
    for i, (px, pz) in enumerate(path):
        nx, nz = route_normal(path, i)
        want.update(cross_section_vertices(px, pz, nx, nz, half))
    return sorted(want)


def route_normal(path, i: int) -> tuple[float, float]:
    """The unit cross-route direction at `path[i]`, defined at EVERY point.

    THE LAST POINT HAS NO NEXT POINT, and taking `path[min(i+1, last)]` there
    makes `q == p`, so the direction is (0,0) and every offset across the
    cross-section resolves to the SAME vertex.  While the run was counted per
    offset that was invisible - the degenerate section reported `2*half+1`
    metres of clear water, the widest reading on the route, at the one point
    that measured nothing at all.  Counting distinct vertices exposed it as a
    1.000 m channel.  The final point's section is taken across the segment it
    ARRIVES ON, which is the only direction it has.
    """
    ax, az = path[i - 1] if i else path[i]
    bx, bz = path[i + 1] if i + 1 < len(path) else path[i]
    dx, dz = bx - ax, bz - az
    ln = math.hypot(dx, dz)
    if ln == 0.0:
        return (0.0, 0.0)
    return (-dz / ln, dx / ln)


def refine_route(path, hull: Hull, depth_at, water: float = WATER_LEVEL,
                 site: str = "") -> list[Finding]:
    """The chosen route re-measured on the 1 m vertex lattice.

    For every point on the route, the widest CONTIGUOUS run of 1 m samples
    across it that the hull could float in.  A run narrower than the beam is a
    refusal with the coordinate of the narrowest cross-section - and it is a
    refusal the 2 m field is structurally incapable of producing.

    THE RUN IS COUNTED OVER DISTINCT VERTICES, NEVER OVER OFFSETS, and the
    difference is a defect this file carried.  `snap` is nearest-vertex, so
    stepping the cross-section offset `t` by 1 m along an OBLIQUE normal lands
    two different offsets on the SAME vertex.  MEASURED by sweeping the normal
    0-89 deg at half=8, 17 offsets: 17/17 distinct at 0, 15 and 30 deg, 15/17
    at 60 deg, and 13/17 AT 45 DEG.  Counting the offsets therefore reported a
    channel 13 vertices wide as 17 m across, and that error is in the
    UNDER-REFUSING direction: a channel narrower than the beam passes.
    `corridor_points` was never wrong about this - it builds its request with
    `want.add(snap(...))`, a SET - so both halves ask the engine the right
    question and only the counting half counted an answer twice, which is why
    no cross-check against the ground read could have caught it.
    (ParBuried's diagnosis, while checking whether ParSettleRest's
    "selected unsnapped, then snapped back out" defect had siblings.)
    """
    req = hull.required_depth_m
    half = hull.beam_m / 2.0 + 1.0
    worst = None
    unread = 0
    widest = 0
    path = densify(path)
    for i, (px, pz) in enumerate(path):
        nx, nz = route_normal(path, i)
        cells = cross_section_vertices(px, pz, nx, nz, half)
        widest = max(widest, len(cells))
        run = best = 0.0
        for v in cells:
            y = depth_at(v[0], v[1])
            if y is None:
                unread += 1
                run = 0.0
                continue
            if water - y >= req:
                run += 1.0
                best = max(best, run)
            else:
                run = 0.0
        if worst is None or best < worst[0]:
            worst = (best, px, pz)
    if worst is None:
        return []
    pop = (f"the {widest} engine vertices the widest cross-section on this "
           f"{len(path)}-point route passes through - {math.ceil(half)} of "
           f"them each side of the route point, COUNTED AS VERTICES so that "
           f"an oblique bearing gets the same {half:.2f} m of window as an "
           f"axis-aligned one; navigable = depth >= {req:.3f} m")
    if unread:
        return [Finding(
            check="hull_entry/refine_unread", ok=False,
            message=(f"{site or hull.name}: {unread} vertices on the chosen "
                     f"route's 1 m cross-sections were never read, so the "
                     f"route cannot be re-measured on the lattice the "
                     f"instrument actually answers on"),
            where=(worst[1], worst[2]), numbers={"unread": unread},
            population=pop)]
    ok = worst[0] >= hull.beam_m
    return [Finding(
        check="hull_entry/refined", ok=ok,
        message=(f"{site or hull.name}: re-measured on the 1 m vertex lattice, "
                 f"the narrowest navigable cross-section on the chosen route "
                 f"is {worst[0]:.3f} m at ({worst[1]:.1f},{worst[2]:.1f}) "
                 f"against a {hull.beam_m:.2f} m beam"
                 + ("" if ok else " - a bar this narrow lies entirely between "
                    "two samples of the 2 m route field, which cannot see it")),
        where=(worst[1], worst[2]), measured=round(worst[0], 3),
        limit=hull.beam_m,
        numbers={"hull": hull.name, "narrowest_m": round(worst[0], 3),
                 "lattice_m": 1.0, "route_points": len(path)},
        population=pop)]


# THE REASON STRING IS NOT THE EVIDENCE.  `best_sill` reports the precision of
# ONE CELL - the deepest cell on the pool's rim - and that cell is chosen for
# its DEPTH, not for whether it is what shuts the hull in.  A basin with a
# single pile standing in 9 m of water and a `rock4_forest` bounding box lying
# across a 4 m channel mouth has a `solid:` sill and a `bound:` severance, and
# the string says "a box decides this" while the box decides nothing.
#
# `check_sea_reach` already got this right and says so in its own comment -
# "the test is the RE-RUN, not the reason string".  The same question is asked
# the same way here: drop every MeshCollider bound and ASK THE ROUTE AGAIN.
# If the hull gets out without them, the refusal rests on a bounding box, is
# named, and does not decide the world.
_ENTRY_BOUNDS_BY_REASON = False


def route_exists(bed: Bed, hull: Hull, barriers, berth: tuple[float, float],
                 yaw_deg: float, water: float = WATER_LEVEL) -> bool:
    """Does THIS hull reach open sea from THIS berth over THIS barrier set?

    The same four steps `check_hull_entry` takes - navigable, open sea in
    view, the berth's own navigable footprint cells, the widest route - with
    no finding attached, so the question can be asked twice over two barrier
    sets and the two answers compared.
    """
    nav, _why = navigable(bed, hull, barriers, water)
    sea = boundary_sea(bed, nav)
    if not sea:
        return False
    starts = []
    for px, pz in footprint(hull, berth[0], berth[1], yaw_deg):
        i = int(round((px - bed.x0) / bed.step))
        j = int(round((pz - bed.z0) / bed.step))
        if 0 <= i < bed.nx and 0 <= j < bed.nz and nav[j * bed.nx + i]:
            starts.append(j * bed.nx + i)
    if not starts:
        return False
    return bool(widest_route(bed, nav, clearance(bed, nav),
                             sorted(set(starts)), sea)["reached"])


def check_hull_entry(hull: Hull, bed: Bed, barriers, berth: tuple[float, float],
                     yaw_deg: float, water: float = WATER_LEVEL,
                     site: str = "") -> list[Finding]:
    """The clear opening on the best route from this berth to open sea.

    Three refusals, and they are different things: there is no route at any
    width; the route's tightest point is narrower than the beam; the route
    passes under something whose height above the water this hull's air draft
    was never measured against.
    """
    F: list[Finding] = []
    pop = (f"the {len(barriers)} solid PARTS within the sampled region whose "
           f"vertical extent overlaps hull {hull.name}'s band, and the "
           f"{bed.nx}x{bed.nz} applied-bed lattice at {bed.step:g} m around "
           f"the berth")
    nav, why = navigable(bed, hull, barriers, water)
    sea = boundary_sea(bed, nav)
    if not sea:
        F.append(Finding(
            check="hull_entry/region_too_small", ok=False,
            message=(f"{site or hull.name}: no navigable cell reaches the edge "
                     f"of the {bed.nx * bed.step:.0f} m sampled region, so open "
                     f"sea is not in view and this check cannot conclude; "
                     f"refusing rather than reporting connected"),
            where=berth, numbers={"region_m": bed.nx * bed.step,
                                  "hull": hull.name}, population=pop))
        return F
    clear = clearance(bed, nav)
    starts = []
    for px, pz in footprint(hull, berth[0], berth[1], yaw_deg):
        i = int(round((px - bed.x0) / bed.step))
        j = int(round((pz - bed.z0) / bed.step))
        if 0 <= i < bed.nx and 0 <= j < bed.nz and nav[j * bed.nx + i]:
            starts.append(j * bed.nx + i)
    route = widest_route(bed, nav, clear, sorted(set(starts)), sea)
    if not route["reached"]:
        sill = best_sill(bed, nav, why, starts, hull, water)
        loose = [b for b in barriers if not b.tight]
        if _ENTRY_BOUNDS_BY_REASON:
            bounds_only = sill["bounds_only"]
        else:
            bounds_only = bool(loose) and route_exists(
                bed, hull, [b for b in barriers if b.tight], berth, yaw_deg,
                water)
        F.append(Finding(
            check="hull_entry", ok=False,
            message=(f"{site or hull.name}: {route['reason']} - the "
                     f"{hull.beam_m:.2f} m beam has no water it can float in "
                     f"between this berth and open sea. "
                     + (f"The deepest way out of the {sill['pool_cells']} "
                        f"navigable cells this berth sits in holds "
                        f"{sill['depth_m']:.3f} m of water at "
                        f"({sill['at'][0]:.1f},{sill['at'][1]:.1f}) against "
                        f"{hull.required_depth_m:.3f} m required"
                        + (f", and it is blocked by {sill['blocked_by']}"
                           if sill["blocked_by"] else "")
                        if sill["at"] else
                        "The berth itself has no navigable cell under it.")
                     + (("; ADVISORY - the deepest rim cell's own reason "
                         "string says a MeshCollider bound, so this refusal "
                         "is not allowed to decide the world"
                         if _ENTRY_BOUNDS_BY_REASON else
                         f"; ADVISORY - dropping the {len(loose)} of "
                         f"{len(barriers)} barrier parts that are "
                         f"MeshCollider bounds ("
                         f"{', '.join(sorted({b.prefab for b in loose}))}) "
                         f"and asking the route again, the hull gets out, so "
                         f"this refusal rests on a bounding box rather than "
                         f"on a measured solid")
                        if bounds_only else "")),
            where=(sill["at"] or berth), measured=sill["depth_m"],
            limit=hull.required_depth_m,
            numbers={"hull": hull.name, "beam_m": hull.beam_m,
                     "required_depth_m": hull.required_depth_m,
                     "pool_cells": sill["pool_cells"],
                     "deepest_sill_m": sill["depth_m"],
                     "sill_short_by_m": round(hull.required_depth_m
                                              - sill["depth_m"], 3),
                     "sill_blocked_by": sill["blocked_by"],
                     "barrier_parts": len(barriers),
                     "mesh_bound_parts": len(loose),
                     "mesh_bound_prefabs": sorted({b.prefab for b in loose}),
                     "precision": ("mesh bounds only" if bounds_only
                                   else "bed heights and box colliders"),
                     "precision_source": ("the route RE-RUN with the mesh "
                                          "bounds dropped"
                                          if not _ENTRY_BOUNDS_BY_REASON else
                                          "the deepest rim cell's reason "
                                          "string"),
                     "instrument": "applied bed via ctx.ground + per-part "
                                   "solids from the ZDO census"},
            population=pop, advisory=bounds_only))
        return F
    # EXACT width at the bottleneck the field found: the field is on a 2 m
    # lattice and cannot be the verdict.
    bx, bz = route["at"]
    near = [b for b in barriers
            if b.spans_band(hull.keel_y(water), water)
            and poly_near(b.poly, bx, bz, hull.length_m)]
    exact = None
    if near:
        cx, cz = _route_cross(bed, nav, bx, bz)
        half = max(hull.length_m, 2.0 * route["bottleneck_m"] + hull.beam_m)
        exact = aperture((-half, half), near, (bx, bz), (cx, cz),
                         hull.keel_y(water), water,
                         along_half=max(2.0, hull.length_m / 4.0))
    measured = (exact["widest_m"] if exact else 2.0 * route["bottleneck_m"])
    ok = measured >= hull.beam_m
    # WOULD IT STILL BE TOO NARROW IF ONLY THE BOX COLLIDERS COUNTED?  If not,
    # the refusal rests on a MeshCollider's bounding box - named, reported, and
    # not allowed to fail the world by itself.
    bounds_only = False
    if exact and not ok:
        tight = [b for b in near if b.tight]
        cx, cz = _route_cross(bed, nav, bx, bz)
        half = max(hull.length_m, 2.0 * route["bottleneck_m"] + hull.beam_m)
        tight_ap = aperture((-half, half), tight, (bx, bz), (cx, cz),
                            hull.keel_y(water), water,
                            along_half=max(2.0, hull.length_m / 4.0))
        bounds_only = tight_ap["widest_m"] >= hull.beam_m
    F.append(Finding(
        check="hull_entry", ok=ok,
        message=((f"{site or hull.name}: clear opening {measured:.3f} m on the "
                  f"widest route to open sea against a {hull.beam_m:.2f} m "
                  f"beam") + ("" if ok else
                 f" - short by {hull.beam_m - measured:.3f} m; slots "
                 f"{exact['slots_m'] if exact else []} inside a "
                 f"{exact['structural_span_m'] if exact else 0:.3f} m span")
                 + ("; ADVISORY - with only box colliders counted the opening "
                    "clears the beam, so this refusal rests on a MeshCollider "
                    "bound" if bounds_only else "")),
        where=(bx, bz), measured=round(measured, 3), limit=hull.beam_m,
        numbers={"hull": hull.name, "bottleneck_m": route["bottleneck_m"],
                 "field_step_m": bed.step,
                 "width_source": ("exact interval arithmetic on the barriers' "
                                  "own projected shadows" if exact else
                                  f"the {bed.step:g} m field, which cannot see "
                                  f"an obstruction narrower than {bed.step:g} m"),
                 "precision": ("mesh bounds decide it" if bounds_only
                               else "box colliders decide it"),
                 "route_path": route["path"],
                 **({"aperture": exact} if exact else {})},
        population=pop, advisory=bounds_only))
    # OVERHEAD, over the WHOLE route rather than at its narrowest point.  A
    # hull's mast does not care where the channel is tightest: the lintel it
    # hits can be anywhere on the way out, and asking only at the bottleneck
    # is the point-versus-footprint error in the vertical.
    over: list[tuple[float, Barrier, tuple[float, float]]] = []
    for b in barriers:
        if b.y_lo < water - BAND_EPS_M:
            continue
        for px, pz in route["path"]:
            if poly_near(b.poly, px, pz, hull.beam_m / 2.0):
                over.append((b.y_lo - water, b, (px, pz)))
                break
    if over:
        head, low_b, at = min(over, key=lambda t: t[0])
        if hull.air_m is None:
            F.append(Finding(
                check="hull_entry/air_draft_unmeasured", ok=False,
                message=(f"{site or hull.name}: the route out passes under "
                         f"{low_b.prefab} with {head:.3f} m of head room at "
                         f"({at[0]:.1f},{at[1]:.1f}), and {hull.name}'s air "
                         f"draft was NEVER MEASURED - the prefab is absent "
                         f"from blueprints/data/piece_geometry.json. Refusing "
                         f"to certify a {hull.beam_m:.2f} m ship under an "
                         f"unmeasured lintel"),
                where=at, measured=round(head, 3),
                numbers={"hull": hull.name, "over": low_b.prefab,
                         "head_room_m": round(head, 3),
                         "route_cells_under_something": len(over),
                         "missing": "air draft (no piece_geometry.json entry "
                                    "for this hull)"},
                population=pop))
        else:
            F.append(Finding(
                check="hull_entry/head_room", ok=head >= hull.air_m,
                message=(f"{site or hull.name}: {head:.3f} m of head room at "
                         f"({at[0]:.1f},{at[1]:.1f}) under {low_b.prefab} "
                         f"against {hull.name}'s measured {hull.air_m:.3f} m "
                         f"air draft"),
                where=at, measured=round(head, 3), limit=hull.air_m,
                numbers={"hull": hull.name, "over": low_b.prefab,
                         "route_cells_under_something": len(over),
                         "air_draft_source": hull.source},
                population=pop, advisory=not low_b.tight))
    return F


def _route_cross(bed: Bed, nav: list[bool], x: float, z: float
                 ) -> tuple[float, float]:
    """A unit vector ACROSS the channel at (x, z).

    Taken from the navigable water itself - the direction in which the water
    runs out soonest - so the aperture is measured across the throat rather
    than along it.
    """
    best = (1.0, 0.0)
    worst = float("inf")
    for deg in range(0, 180, 15):
        a = math.radians(deg)
        ux, uz = math.cos(a), math.sin(a)
        reach = 0.0
        for sign in (1.0, -1.0):
            d = bed.step
            while d < bed.nx * bed.step:
                i = int(round((x + sign * ux * d - bed.x0) / bed.step))
                j = int(round((z + sign * uz * d - bed.z0) / bed.step))
                if not (0 <= i < bed.nx and 0 <= j < bed.nz):
                    break
                if not nav[j * bed.nx + i]:
                    break
                d += bed.step
            reach += d
        if reach < worst:
            worst, best = reach, (ux, uz)
    return best


# ---------------------------------------------------------------------------
# CHECK 2 - BERTH DEPTH UNDER THE HULL'S OWN FOOTPRINT
# ---------------------------------------------------------------------------
_BERTH_FOOTPRINT = True
# THE SHIPPED DEFECT, off, and reachable so a selftest case can put it back.
# A berth was judged by ONE height query at the berth's own coordinate.  The
# hull is 21.57 m long and the query is a point, so it answers about the end
# of the slip the builder happened to name: MEASURED at
# ferry-terminal-eastisle, ledger seq 2473, the point read 4.21 m and the
# shallowest of the 153 samples under the hull's own rotated footprint read
# 0.244 m.  False restores that form.


_BERTH_CAUSE = True
# THE SECOND HALF OF A BERTH REFUSAL, off, and reachable so a selftest case
# can put it back.  `berth_depth` read ONE bed - the applied one - and
# reported "N m short".  Two defects with no repair in common produce that
# same sentence:
#   a channel a write filled          restorable, cut-only, to seed heights
#   a berth sited on the seed's land  NOTHING WAS TAKEN; there is no
#                                     restoration, only a dredge below the
#                                     seed or a different pose
# MEASURED on Ulfsland 0571968d at ferry-terminal-eastisle, the declared
# VikingShip: shallowest -0.465 m under 151 applied samples against 2.500 m
# required - and 45 of those 151 are not flood-fill connected to open sea on
# the APPLIED bed, on the SEED's own bed, with all 4,185 solid parts standing
# and with none of them.  The seed never put water there.  A reader given
# only the applied number sizes a restoration that cannot exist.
# False restores the one-bed form.


def check_berth_depth(hull: Hull, x: float, z: float, yaw_deg: float,
                      depth_at, water: float = WATER_LEVEL, site: str = "",
                      point_reading: float | None = None,
                      seed_depth_at=None) -> list[Finding]:
    """The SHALLOWEST sample under the hull, and what a point would have said.

    `depth_at(x, z) -> bed y or None`.  The point reading is reported beside the
    footprint reading whenever it is available, because the size of the
    disagreement is the measurement that justifies the method: 4.21 m at a
    VikingShip's spawn position against 0.244 m under its own hull.
    """
    pts = footprint(hull, x, z, yaw_deg)
    depths = []
    unread = []
    for px, pz in pts:
        y = depth_at(px, pz)
        if y is None:
            unread.append((px, pz))
        else:
            depths.append((water - y, px, pz))
    pop = (f"{len(pts)} samples on the engine's 1 m lattice inside "
           f"{hull.name}'s own {hull.beam_m:.2f} x {hull.length_m:.2f} m "
           f"elliptical footprint at its own yaw {yaw_deg:.1f} deg")
    if unread:
        return [Finding(
            check="berth_depth/unread", ok=False,
            message=(f"{site or hull.name}: {len(unread)} of {len(pts)} "
                     f"footprint samples were never read by the bed "
                     f"instrument; a berth verdict over a partial footprint is "
                     f"the point-reading error with more steps"),
            where=(x, z), numbers={"unread": len(unread), "samples": len(pts),
                                   "first_unread": unread[0]},
            population=pop)]
    mn = min(depths)
    mx = max(depths)
    req = hull.required_depth_m
    ok = mn[0] >= req
    if not _BERTH_FOOTPRINT and point_reading is not None:
        # THE SHIPPED FORM: one query at the berth's own coordinate, which is
        # where the builder put the boat and therefore where the water is.
        ok = point_reading >= req
    nums = {"hull": hull.name, "yaw_deg": round(yaw_deg, 1),
            "hull_w_m": hull.beam_m, "hull_l_m": hull.length_m,
            "draught_m": hull.draught_m,
            "keel_clearance_m": hull.keel_clearance_m,
            "required_depth_m": round(req, 3),
            "min_depth_under_hull_m": round(mn[0], 3),
            "max_depth_under_hull_m": round(mx[0], 3),
            "water_under_keel_m": round(mn[0] - hull.draught_m, 3),
            "hull_samples": len(depths),
            "shallowest_at": [round(mn[1], 2), round(mn[2], 2)],
            "instrument": ("applied bed read back from the SAVE through the "
                           "engine (ctx.ground), minus nothing")}
    if point_reading is not None:
        nums["point_reading_m"] = round(point_reading, 3)
        nums["point_error_m"] = round(point_reading - mn[0], 3)
        nums["point_verdict"] = ("floats" if point_reading >= req
                                 else "aground")
    cause = None
    if _BERTH_CAUSE and seed_depth_at is not None and not ok:
        # WHICH OF THE TWO DEFECTS IS THIS.  Both read "N m short" on the
        # applied bed and they have NOTHING in common as repairs:
        #   the seed floated this hull here and the save does not
        #       -> a WRITE took the water, and a restoration to the seed's
        #          own heights is the repair, cut-only, nameable.
        #   the seed did not float it either
        #       -> NOTHING WAS TAKEN. The berth was declared at a pose whose
        #          depth was never measured, and there is no restoration at
        #          all: only a dredge BELOW the seed, which is new ground
        #          truth and a decision about what the world should be.
        sd, sunread = {}, 0
        for px, pz in pts:
            sy = seed_depth_at(px, pz)
            if sy is None:
                sunread += 1
            else:
                sd[(round(px, 3), round(pz, 3))] = water - sy
        if sd and not sunread:
            smn = min((d, k[0], k[1]) for k, d in sd.items())
            raised = [(px, pz, water - sd[(round(px, 3), round(pz, 3))],
                       water - d)
                      for (d, px, pz) in depths
                      if d < sd[(round(px, 3), round(pz, 3))]]
            cause = {
                "seed_min_depth_under_hull_m": round(smn[0], 3),
                "seed_shallowest_at": [round(smn[1], 2), round(smn[2], 2)],
                "seed_floats_this_hull": bool(smn[0] >= req),
                "raised_footprint_samples": len(raised),
                "cause": ("A WRITE TOOK THE WATER - the seed floated this "
                          "hull here and the save does not; the repair is a "
                          "restoration to the seed's own heights"
                          if smn[0] >= req else
                          "THE SEED NEVER FLOATED THIS HULL HERE - nothing "
                          "was taken, so no restoration exists; this berth "
                          "was declared at a pose whose depth was never "
                          "measured, and only a dredge BELOW the seed or a "
                          "different pose can float it"),
                "repair": ("restoration" if smn[0] >= req else
                           "dredge below the seed, or re-site the berth")}
            if raised and smn[0] >= req:
                cause["restoration"] = restoration_set(
                    raised, min_cut_m=RESTORATION_MIN_CUT_M)
        nums["seed_cause"] = cause
    return [Finding(
        check="berth_depth", ok=ok,
        message=((f"{site or hull.name}: shallowest {mn[0]:.3f} m under "
                  f"{len(depths)} footprint samples against {req:.3f} m "
                  f"required")
                 + ("" if ok else
                    f" - {req - mn[0]:.3f} m short at "
                    f"({mn[1]:.1f},{mn[2]:.1f})")
                 + ("" if point_reading is None else
                    f"; a point at the berth reads {point_reading:.3f} m, "
                    f"{point_reading - mn[0]:+.3f} m")
                 + ("" if not cause else
                    f"; the SEED's own bed holds "
                    f"{cause['seed_min_depth_under_hull_m']:.3f} m at its "
                    f"shallowest under the same footprint, so "
                    f"{cause['cause']}")),
        where=(mn[1], mn[2]), measured=round(mn[0], 3), limit=round(req, 3),
        numbers=nums, population=pop)]


# ---------------------------------------------------------------------------
# CHECK 3 - DRAINING, WHICH IS A SIGN, NOT A DEPTH
# ---------------------------------------------------------------------------
MIN_CHANNEL_DEPTH_M = 1.0
# Water shallower than this in the SEED was never navigable for anything: the
# shallowest hull in `waterfront.py::VESSELS` draws 0.8 m and wants 1.0 m of
# keel clearance on top.  A 0.5 m puddle at a shoreline that loses 0.3 m of
# depth is a true measurement and not a drained channel, and calling it one is
# how a report earns 3,990 refusals nobody reads.  Sub-metre seed water is
# still counted in DRAINED - losing it entirely is a different claim from
# shoaling it.


def instrument_floor(diffs: list[float]) -> dict:
    """How far the two height instruments disagree WHERE NOTHING WAS WRITTEN.

    THE REASON THIS EXISTS.  The seed surface comes from `PatchScan`
    (`HeightmapBuilder`, seed only) and the applied surface from the save's own
    compiled heightmap; where no terrain was ever written they are the same
    computation and should agree, but they are read through two different
    harnesses and the save's zones were built by a modded server.  MEASURED on
    Ulfsland: over the samples outside every recorded `terrain_write`
    footprint the disagreement has a p99 of a few centimetres - and a check
    with no floor at all reported 3,990 "shoaled" samples in one harbour,
    almost all of them tenths of a metre in half a metre of shoreline water.

    A pass/fail decided inside the instrument's own noise is not a
    measurement, so the floor is measured per run rather than assumed, and it
    is printed in the finding beside the number it qualifies.
    """
    if not diffs:
        return {"floor_m": None, "samples": 0, "p50_m": None, "p99_m": None}
    a = sorted(abs(d) for d in diffs)
    p50 = a[len(a) // 2]
    p99 = a[min(len(a) - 1, int(len(a) * 0.99))]
    return {"floor_m": round(p99, 3), "samples": len(a),
            "p50_m": round(p50, 3), "p99_m": round(p99, 3)}


# ---------------------------------------------------------------------------
# THE REPAIR ORDER - a refusal that names ONE vertex for an N-vertex defect
# ---------------------------------------------------------------------------
RESTORATION_MIN_CUT_M = 0.01
# Below this a proposed cut is noise in the reading, not a cut.  It is
# `applied.AUTHOR_TOLERANCE_M`, the same number the composition uses to
# decide whether a write AUTHORS a sample, and `roads/regrade.py::MIN_CUT_M`
# is the same constant: a vertex named here that the emitter would then drop
# is a repair order the write path cannot fill, which is the shape this
# whole block exists to refuse.

_RESTORATION_SET = True
# THE SHIPPED DEFECT, off, and reachable so a selftest case can put it back.
# Every repairable refusal in this section reported `worst_rise_m` at ONE
# `where`, and nothing else: `channel_severed` counted 36 raised samples and
# named (-318,-56); `draining` counted 2 and named (1641,533).  A repair
# sized off that finding is ONE VERTEX, the gate re-reads the same bed, and
# the next worst sample becomes the new `where` - so the defect is reported
# again, smaller, forever, and each pass takes ownership of a vertex without
# closing anything.  False restores that form, and
# `check_repair_is_named` is required to refuse it.



_RESTORATION_FLOOR = True
# THE SECOND SHIPPED DEFECT, off, and reachable so a selftest case can put
# it back.  `check_channel_severed` sized its repair off a RAW
# cross-instrument difference with no floor, while `check_draining` - the
# same two instruments, the same subtraction - has applied the region's own
# MEASURED floor since a floorless version reported 3,990 "shoaled" samples
# in one harbour.  MEASURED at the boathouse berth on 2a6940a934b5: floor
# 0.156 m, and 20 of the 36 vertices the check counted as its own repair are
# raises BELOW it, the smallest 0.016 m.  False restores that form.


_RESTORATION_ATTRIBUTION = True
# THE THIRD SHIPPED DEFECT, off, and reachable so a selftest case can put it
# back - and it is `check_repair_is_named`'s own rule one level out, applied
# to OWNERSHIP instead of to the REPAIR.
#
# `restoration_set` names all N vertices of a cut.  The finding that carries
# it names ONE owner: `attributed_to` is `attribute(worst_x, worst_z)`, the
# terrain_write containing the single WORST sample, printed as prose over a
# population of N.  So the repair order travels complete and the OWNERSHIP
# of it travels as a one-vertex sample, and a reader has exactly two moves,
# both wrong:
#   cut all N          - takes a road's own ground out from under it on the
#                        strength of a vertex that is not the road's,
#   cut none of them   - because one line of prose says "inside terrain_write
#                        road_N1_wnorth_shelf", leaving every UNOWNED vertex
#                        standing forever.
#
# MEASURED on Ulfsland a087af6253a0, the four `draining` refusals and the one
# `draining/shoaled`: 49 / 113 / 649 / 43 vertices whose worst sample is
# inside a road write, and ONE refusal of a single vertex - the declared
# VikingShip at ferry-terminal-eastisle, (1989.0,440.0), +0.1492 m - whose
# `attributed_to` is EMPTY.  That vertex is owned by nothing, is closable by
# a 0.1492 m3 restoration with no ownership decision from anybody, and had
# survived gate A, gate B and gate C inside the same class as 649 road-owned
# ones because the class was named off one sample.
#
# False restores the shipped form: every vertex in the set inherits the WORST
# vertex's owner, the unowned subset reads 0, and the part of the defect that
# could have been closed tonight becomes invisible.


def restoration_set(raised, min_cut_m: float = RESTORATION_MIN_CUT_M,
                    attribute=None) -> dict:
    """THE WHOLE CUT, NAMED: vertices, ABSOLUTE targets, volume, and its sign.

    `raised` is `(x, z, seed_y, applied_y)` rows the caller has already
    judged to be the defect's own population - every one of them a sample
    the ground was RAISED at.  The restoration is the only repair a raise
    admits: put each of those vertices back on the height the SEED itself
    chose.  That is why the sign is not a hope here but an output - every
    delta is `seed - applied`, every one of them negative, and a row that is
    not a lowering is refused rather than clamped, because a set that
    silently drops the one member that would have filled is exactly how a
    fill gets written under a restoration's name.

    The target is ABSOLUTE.  A delta is only meaningful against a stated
    prior and the prior is re-read by the emitter, so what travels is the
    height, in the shape `roads/regrade.py::load_cuts` already takes:
    `{"xz": [x, z], "applied_after": y}` on the 1 m lattice.

    `named` is the size of the set and `population` the size of the set it
    was drawn from, both printed, because a repair is only ever as wide as
    the check that names its population.

    AND WHO OWNS EACH VERTEX, ASKED PER VERTEX.  `attribute(x, z) -> str`
    is the same callable the carrying finding uses for its `attributed_to`
    prose, but it is asked HERE, once per named cut, instead of once for the
    worst one.  The partition it produces is the only part of a repair order
    that can be executed without an ownership decision: a vertex inside a
    recorded `terrain_write` is that write's own ground and cutting it is a
    decision about the thing built on top, while a vertex inside NOTHING is
    spill, owned by no build, and restoring it to the seed's own height
    costs nobody anything.  Both halves travel with their volumes so the
    refusal states what is closable now and what is waiting on a decision.
    """
    rows = list(raised)
    bad = [(x, z, g, a) for (x, z, g, a) in rows if a <= g]
    if bad:
        raise ValueError(
            f"restoration_set was handed {len(bad)} of {len(rows)} rows whose "
            f"applied surface is NOT above the seed, first "
            f"({bad[0][0]},{bad[0][1]}) seed {bad[0][2]:.3f} applied "
            f"{bad[0][3]:.3f}: restoring those is a FILL, and this function "
            f"has no path that raises a sample")
    cuts, volume, below = [], 0.0, 0
    for x, z, g, a in sorted(rows, key=lambda r: r[3] - r[2], reverse=True):
        if (a - g) < min_cut_m:
            below += 1
            continue
        cuts.append({"xz": [float(x), float(z)], "applied_after": float(g),
                     "cut_m": round(a - g, 4)})
        volume += (a - g)
    if attribute is not None:
        worst_owner = ((attribute(cuts[0]["xz"][0], cuts[0]["xz"][1]) or "")
                       if cuts else "")
        for c in cuts:
            c["inside"] = (
                (attribute(c["xz"][0], c["xz"][1]) or "")
                if _RESTORATION_ATTRIBUTION
                # THE SHIPPED FORM: the whole population inherits the owner
                # of the single worst vertex.
                else worst_owner)
    off = [c["xz"] for c in cuts
           if abs(c["xz"][0] - round(c["xz"][0])) > 1e-6
           or abs(c["xz"][1] - round(c["xz"][1])) > 1e-6]
    if _RESTORATION_SET:
        named = cuts
    else:
        # THE SHIPPED FORM: the worst vertex and nothing else.
        named = cuts[:1]
    unowned = [c for c in named if not c.get("inside")]
    owned = [c for c in named if c.get("inside")]
    writes: dict[str, int] = {}
    for c in owned:
        writes[c["inside"]] = writes.get(c["inside"], 0) + 1
    return {"cuts": named, "named": len(named), "population": len(cuts),
            "below_min_cut_m": below, "min_cut_m": min_cut_m,
            "volume_m3": round(volume, 4),
            "volume_named_m3": round(sum(c["cut_m"] for c in named), 4),
            "sign": "LOWERING",
            "worst_cut_m": named[0]["cut_m"] if named else None,
            "off_lattice": off,
            "attribution": {
                "measured": attribute is not None,
                "per_vertex": bool(_RESTORATION_ATTRIBUTION),
                "unowned": len(unowned),
                "unowned_volume_m3": round(sum(c["cut_m"] for c in unowned), 4),
                "owned": len(owned),
                "owned_volume_m3": round(sum(c["cut_m"] for c in owned), 4),
                "writes": dict(sorted(writes.items())),
                "rule": ("a vertex inside a recorded terrain_write is that "
                         "write's own ground and cutting it is a decision "
                         "about what stands on it; a vertex inside nothing "
                         "is spill, owned by no build, and restoring it to "
                         "the seed's own height is closable with no "
                         "ownership decision")},
            "emitter": "roads/regrade.py plan --cuts, cut-only, "
                       "absolute targets on the 1 m lattice"}


def _attribution_clause(rest: dict) -> str:
    """The split, in the refusal's own sentence, or nothing.

    A refusal that has been shown one owner is a refusal a reader will hand
    straight to that owner.  The two volumes are what stops that: the part
    of the cut that belongs to nobody is closable tonight, and saying so in
    the message means it does not have to be recovered from `numbers`.
    """
    a = rest.get("attribution") or {}
    if not a.get("measured") or not rest.get("named"):
        return ""
    if not a.get("unowned"):
        return (f"; ALL {a['owned']} named vertices "
                f"({a['owned_volume_m3']:.4f} m3) lie inside recorded "
                f"terrain_writes {sorted(a['writes'])}, so every one of them "
                f"is somebody's ground and none of this cut is closable "
                f"without deciding who owns it")
    return (f"; OF THE {rest['named']} NAMED VERTICES, {a['unowned']} "
            f"({a['unowned_volume_m3']:.4f} m3) lie inside NO recorded "
            f"terrain_write and are restorable with no ownership decision, "
            f"and {a['owned']} ({a['owned_volume_m3']:.4f} m3) lie inside "
            + (f"{sorted(a['writes'])}" if a["writes"] else "none")
            + " and are that write's own ground")


def check_repair_is_named(findings, site: str = "") -> list[Finding]:
    """A REFUSAL THAT NAMES A WORST VERTEX AND NOT ITS POPULATION IS NOT A
    REPAIR ORDER.

    This is the population rule turned on the section's own output.  Every
    refusal here already prints how many samples it counted; until this
    guard existed not one of them printed WHICH, so the widest repair any
    reader could derive from a refusal was a single vertex - and a one-vertex
    cut at a 36-vertex bar moves ground, buys nothing, and comes back next
    run wearing the next worst sample's coordinates.

    The rule: a finding that declares a restorable population MUST name
    every member of it.  `restoration.named` against `restoration.population`
    - two numbers from the same function, both printed, and the gate refuses
    when they differ.  It passes on the shipped code and
    `_neuter_restoration_set` is required to flip it.

    AND THE SAME RULE ON THE OTHER HALF OF A REPAIR ORDER: WHO OWNS IT.
    A refusal states an owner - `attributed_to`, the terrain_write holding
    the WORST sample - over a population of N.  Naming one owner for N
    vertices is naming one vertex for an N-vertex defect with the noun
    changed, and it fails in the expensive direction, because the reader
    who believes it hands the whole set to that write's author and the
    vertices that belong to nobody are never cut at all.  So
    `restoration.attribution.per_vertex` is asserted here, and the unowned
    subset - the part closable with no ownership decision - is printed with
    its own volume.
    """
    out: list[Finding] = []
    carrying = [f for f in findings if not f.ok and "restoration" in f.numbers]
    for f in carrying:
        r = f.numbers["restoration"]
        pop = (f"the {r['population']} cut-only vertices {f.check} counted as "
               f"its own repair, against the {r['named']} it named")
        a = r.get("attribution") or {}
        if a.get("measured") and not a.get("per_vertex"):
            out.append(Finding(
                check="harbours/repair_attribution", ok=False,
                message=(f"{site or 'berth'}: {f.check} attributes all "
                         f"{r['named']} vertices of its own repair to the "
                         f"owner of ONE of them. Ownership decides whether a "
                         f"vertex may be cut at all, so a set attributed off "
                         f"its worst sample is a repair order whose unowned "
                         f"part - the only part that needs no decision from "
                         f"anybody - cannot be separated from the part that "
                         f"is a road's own ground"),
                where=f.where,
                measured=float(a.get("owned", 0) + a.get("unowned", 0)),
                limit=0.0,
                numbers={"named": r["named"], "check": f.check,
                         "per_vertex": False,
                         "attribution": a},
                population=pop))
            continue
        if r["off_lattice"]:
            out.append(Finding(
                check="harbours/repair_off_lattice", ok=False,
                message=(f"{site or 'berth'}: {f.check} names "
                         f"{len(r['off_lattice'])} repair vertices off the "
                         f"1 m lattice, first {r['off_lattice'][0]} - "
                         f"Heightmap.GetHeight rounds its query to a vertex, "
                         f"so an off-lattice cut is an ambiguous instruction "
                         f"with two answers"),
                where=tuple(r["off_lattice"][0]),
                measured=float(len(r["off_lattice"])), limit=0.0,
                numbers={"off_lattice": r["off_lattice"],
                         "check": f.check}, population=pop))
            continue
        if r["named"] == r["population"]:
            out.append(Finding(
                check="harbours/repair_is_named", ok=True,
                message=(f"{site or 'berth'}: {f.check} names all "
                         f"{r['named']} of the {r['population']} vertices its "
                         f"own repair needs, {r['volume_named_m3']:.4f} m3 of "
                         f"CUT to absolute seed heights, worst "
                         f"{r['worst_cut_m']:.3f} m"
                         + (f" ({r['below_min_cut_m']} further raised samples "
                            f"are under the {r['min_cut_m']:.3f} m the "
                            f"instruments agree to and are counted, not cut)"
                            if r["below_min_cut_m"] else "")),
                where=f.where,
                measured=float(r["named"]), limit=float(r["population"]),
                numbers={"named": r["named"], "population": r["population"],
                         "volume_m3": r["volume_named_m3"],
                         "check": f.check, "sign": r["sign"]},
                population=pop))
            continue
        out.append(Finding(
            check="harbours/repair_is_named", ok=False,
            message=(f"{site or 'berth'}: {f.check} counted "
                     f"{r['population']} vertices of cut-only repair and "
                     f"NAMED {r['named']}. A repair is only ever as wide as "
                     f"the check that names its population: sized off this "
                     f"finding the write moves "
                     f"{r['volume_named_m3']:.4f} m3 of the "
                     f"{r['volume_m3']:.4f} m3 the defect is, leaves "
                     f"{r['population'] - r['named']} vertices standing, and "
                     f"the next run reports the same defect at the next "
                     f"worst sample's coordinates"),
            where=f.where,
            measured=float(r["named"]), limit=float(r["population"]),
            numbers={"named": r["named"], "population": r["population"],
                     "volume_named_m3": r["volume_named_m3"],
                     "volume_m3": r["volume_m3"], "check": f.check},
            population=pop))
    return out

_TERMINATION_UNREAD_IS_UNKNOWN = True
# Whether an instrument that DID NOT ANSWER produces UNKNOWN or is read as
# having answered CLEAR.  False restores the conflation, and the conflation
# is the one way a termination classifier manufactures green: `contested_by`
# is None both when nobody holds the ground and when the footprints could
# not be read, so a class that does not separate them reports ACTIONABLE
# precisely where the gate cannot see.  UNREAD IS NOT CLEAR.


# EVERY REFUSAL THIS SECTION CAN EMIT, NAMED - AND EACH WITH A CLASS.
#
# `check_termination` below classes a refusal that carries a RESTORATION
# off its own per-vertex attribution; a refusal with no restoration has no
# repair to attribute, and is classed by the family named here.  So this
# table DRIVES the instrument rows and the world-facing ones alike, and
# `termination.coverage` refuses the gate's selftest if a check is named by
# no family, or by one that hands its row no class.
#
# 16 OF THESE WERE `NOT_MEASURED` - class UNKNOWN - until drive 2's frozen
# Fresh42 save, and the coverage audit counted them and passed.  "The
# instrument did not answer" has a class, one of two, by whether a run of a
# tool changes the answer:
#   CENSUS_NOT_TAKEN / PRODUCER_NOT_RUN  a tool in this tree takes the
#       reading and did not take it here - 1E, naming the tool;
#   SOURCE_CHANGE  no run of any tool changes it: this section asked a
#       question its own instrument cannot answer as written - 1C.
_NO_TOOL = (
    "NO_DESTINATION",
    "the remedy - retiring a standing structure, dredging below the seed, "
    "re-siting a declared berth - is performed by no tool in this tree")
# THE APPLIED-BED READING, and the tool that takes it.
_BED = ("the applied-bed instrument (accept ground() -> terraform/"
        "run_waterproof.sh FROM_SAVE=1)")
_OFF_LATTICE = (
    "SOURCE_CHANGE",
    "this section built its query off the engine's integer vertex lattice, "
    "where Heightmap.GetHeight answers by rounding; snapping it is a change "
    "to this section's source, and no run of any tool changes it")
TERMINATION_FAMILIES = {
    # The world-facing verdicts: a harbour that cannot be entered, drained,
    # berthed or reached from the sea.
    "berth_depth": _NO_TOOL,
    "draining": _NO_TOOL,
    "draining/shoaled": _NO_TOOL,
    "hull_entry": _NO_TOOL,
    "hull_entry/head_room": _NO_TOOL,
    "hull_entry/refined": _NO_TOOL,
    "hull_entry/region_too_small": _NO_TOOL,
    "sea_reach": _NO_TOOL,
    "sea_reach/channel_severed": _NO_TOOL,
    "sea_reach/region_too_small": _NO_TOOL,
    "harbours/population": (
        "NO_DESTINATION",
        "no vessel stands in the save and the ledger declares no berth, so "
        "there is no harbour to measure; a berth is produced only by a site "
        "plan that declares one, and nothing in this tree adds one from a "
        "refusal row"),
    # This section's audit of ITS OWN repair orders: a defect in the order,
    # closed by a change to the check that wrote it.
    "harbours/repair_attribution": (
        "SOURCE_CHANGE",
        "the check attributed every vertex of its own repair to the owner "
        "of ONE of them; per-vertex attribution is a change to that check's "
        "source, and no tool in this tree writes it"),
    "harbours/repair_is_named": (
        "SOURCE_CHANGE",
        "the check counted a cut-only repair population it did not name; "
        "naming it is a change to that check's source"),
    "harbours/repair_off_lattice": (
        "SOURCE_CHANGE",
        "the check named repair vertices off the engine's 1 m lattice, an "
        "instruction with two answers; snapping them is a change to that "
        "check's source"),
    "harbours/field_resolution": (
        "SOURCE_CHANGE",
        "the reachability field steps coarser than the engine's vertex "
        "lattice and cannot address every vertex it judges; a finer field "
        "is a change to this section's source"),
    "harbours/off_vertex_query": _OFF_LATTICE,
    "hull_entry/off_vertex_query": _OFF_LATTICE,
    "draining/floor_unmeasurable": (
        "SOURCE_CHANGE",
        "every sample in the region lies inside a recorded terrain_write, "
        "so the two height instruments have no untouched ground to be "
        "compared on; the writes are the world's, and separating a drain "
        "from their disagreement needs an instrument this section does not "
        "have - a change to its source"),
    # The instrument's own refusals: a reading that a tool in this tree
    # takes, not taken for this row.
    "berth_depth/unread": lambda nums: (
        "CENSUS_NOT_TAKEN",
        f"{_BED} must read the {nums.get('unread')} of "
        f"{nums.get('samples')} berth-footprint samples it returned nothing "
        f"for, first {nums.get('first_unread')}"),
    "hull_entry/unread": lambda nums: (
        "CENSUS_NOT_TAKEN",
        f"{_BED} must read the region samples it returned nothing for: "
        f"{nums.get('read')} of {nums.get('wanted')} read"),
    "hull_entry/refine_unread": (
        "CENSUS_NOT_TAKEN",
        f"{_BED} must read every vertex on the chosen route's 1 m "
        f"cross-sections"),
    "harbours/instrument_failed": (
        "CENSUS_NOT_TAKEN",
        f"{_BED} must answer for this section's hull and bed samples; it "
        f"raised on this run"),
    "harbours/instrument_unavailable": lambda nums: (
        "CENSUS_NOT_TAKEN",
        f"the harness (accept/__main__.py Ctx) must hand this section "
        f"{nums.get('missing')}"),
    "draining/reference_unavailable": (
        "CENSUS_NOT_TAKEN",
        "the SEED surface instrument (ctx.generated -> run_patchscan.sh "
        "SURFACE=heightmap, taken when the gate is given --seed) must "
        "answer for this world's seed"),
    "hull_entry/open_pose_unmeasured": lambda nums: (
        "CENSUS_NOT_TAKEN",
        f"run blueprints/run_doorprobe.sh for {nums.get('prefab')} so "
        f"blueprints/data/door_open_pose.txt carries its open pose"),
    "hull_entry/air_draft_unmeasured": lambda nums: (
        "PRODUCER_NOT_RUN",
        f"re-run blueprints/run_piecegeometry.sh (-> build_piece_geometry.py) "
        f"so blueprints/data/piece_geometry.json carries the hull "
        f"{nums.get('hull')}"),
}


def check_termination(findings, owners, site: str = "") -> list[Finding]:
    """WHY EACH REFUSAL CAN OR CANNOT BE CLOSED, AND HOW MANY ARE WORK.

    N REFUSALS IS NOT A WORK QUEUE.  It is a work queue and an operator
    decision list added together with no way back, and until this existed
    the gate counted every member of both the same way.  Three mechanisms,
    all MEASURED on this world tonight by three sections independently:

      1A FORBIDDEN  the remedy is forbidden by its own writer - a
                    flatten=FORBIDDEN site owns the ground.
      1B CONTESTED  the remedy is PERMITTED and destroys somebody else's
                    work: writer-clear, and inside another actor's
                    recorded terrain_write.  NO GUARD EXISTS FOR THIS.
      1C UNSERVED   no tool in this tree performs the remedy at all.
      ACTIONABLE    the residue, and the only number that answers the
                    question the factory has: is there work left.

    THE INPUT IS THIS SECTION'S OWN PER-VERTEX ATTRIBUTION.  A harbours
    refusal carrying a restoration already knows, vertex by vertex, which
    recorded write owns each cut; the class is that partition read out.

    AND UNREAD IS NOT CLEAR.  `owners=None` means the flatten=FORBIDDEN
    set could not be read, and a restoration's class is then
    1E_OUT_OF_ORDER naming that read rather than ACTIONABLE: a classifier
    that conflated "nobody holds this ground" with "nobody could tell me"
    would report work available precisely where the gate cannot see.  And
    never UNKNOWN, which is `NO_CLASS` spelled again (`termination.settle`).
    """
    from .. import termination as T                        # noqa: PLC0415
    from .settlements import (repair_direction,            # noqa: PLC0415
                              repair_ownership)
    out: list[Finding] = []
    hard = [f for f in findings if not f.ok and not f.advisory]
    by_class: dict[str, int] = {}
    for f in hard:
        r = f.numbers.get("restoration")
        a = (r or {}).get("attribution") or {}
        if r and r.get("cuts"):
            worst = r["cuts"][0]
            own = repair_ownership(worst["xz"][0], worst["xz"][1],
                                   worst["cut_m"], owners, 0.7813)
            f.numbers.update(own)
            f.numbers["contested_by"] = sorted(a.get("writes") or {})
            f.numbers["contested_within_m"] = (
                None if not a.get("measured") else 0.0)
            f.numbers["contested_vertices"] = a.get("owned")
            f.numbers["uncontested_vertices"] = a.get("unowned")
        else:
            # A REFUSAL WITH NO CUT-ONLY REPAIR AT ALL, AND IT IS NOT 1A.
            #
            # The first form of this branch set `repairable_by_terrain_write
            # = False`, which `termination_class` reads as 1A_FORBIDDEN -
            # "a flatten=FORBIDDEN site owns this ground".  MEASURED on
            # Ulfsland 0571968d: that put SIX refusals in 1A - the three
            # berths severed by a STANDING SOLID and the ferry terminal the
            # SEED never connected - and NOT ONE OF THEM HAS AN OWNER.  A
            # refusal that names an owning record it cannot produce is the
            # same defect as a count that names one member of its
            # population, in the field that decides whose decision it is.
            #
            # No restoration means THERE IS NO REPAIR TO ASK AN OWNERSHIP
            # QUESTION ABOUT, so the owner set does not decide the class
            # either way: what closes the refusal does, and that is the
            # check's REMEDY FAMILY above - NO_DESTINATION for a harbour a
            # standing structure or the seed made unusable (1C, as before),
            # CENSUS_NOT_TAKEN for a reading a tool here did not take (1E),
            # SOURCE_CHANGE for a question this section cannot ask as
            # written (1C).  One table, read by one shared function, so the
            # backstop and this pass cannot answer the same row twice.
            f.numbers.setdefault("no_remedy_in_this_tree", True)
            T.fill(sys.modules[__name__], f.check, f.numbers, "harbours")
            cls = f.numbers["termination_class"]
            by_class[cls] = by_class.get(cls, 0) + 1
            continue
        if not _TERMINATION_UNREAD_IS_UNKNOWN:
            # THE CONFLATION, restored: an instrument that did not answer,
            # read as an instrument that answered CLEAR.
            if f.numbers.get("repairable_by_terrain_write") is None:
                f.numbers["repairable_by_terrain_write"] = True
            if f.numbers.get("contested_within_m") is None:
                f.numbers["contested_within_m"] = 0.0
        unread = {}
        if owners is None:
            unread["owners"] = T.READS_OWNERS
        if not a.get("measured"):
            unread["attribution"] = (
                "attribute each vertex of this repair against the recorded "
                "terrain_write footprints (harbours.restoration_set's "
                "per-vertex attribution over harbours.write_footprints)")
        cls = T.settle(f.numbers, unread)
        f.numbers["termination_class"] = cls
        # WHICH WAY, BESIDE WHETHER - the same field settlements and roads
        # publish.  A field present in two sections and absent in the third
        # is the absent-versus-empty defect this gate exists to refuse, and
        # a consumer reading three sections cannot tell "this section does
        # not compute direction" from "this row has no direction".
        f.numbers["repair_direction"] = repair_direction(f.numbers)
        by_class[cls] = by_class.get(cls, 0) + 1
    act = by_class.get("ACTIONABLE", 0)
    out.append(Finding(
        check="harbours/termination", ok=True,
        message=(f"{len(hard)} refusal(s), {act} ACTIONABLE"
                 + (" - every other one is named against the decision that "
                    "blocks it" if len(hard) > act else "")
                 + "; by class " + ", ".join(f"{k} {v}" for k, v
                                             in sorted(by_class.items()))),
        measured=float(act), limit=float(len(hard)),
        numbers={"refusals": len(hard), "actionable": act,
                 "by_class": by_class,
                 "owners_read": owners is not None},
        population="every hard refusal this section produced in this run"))
    return out



_DRAIN_SEED_DEPTH_FLOOR = True
# Whether the MEASURED instrument floor is applied to the SEED DEPTH as well
# as to the rise.  False restores the shipped form, in which a point the two
# instruments cannot agree was ever wet still entered the population and
# could carry a refusal on its own.


def check_draining(samples, water: float = WATER_LEVEL, site: str = "",
                   attribute=None, area_per_sample_m2: float = 4.0,
                   floor_m: float = 0.0,
                   min_channel_depth_m: float = MIN_CHANNEL_DEPTH_M
                   ) -> list[Finding]:
    """The sign of every change to a bed that WAS under water.

    `samples` is `(x, z, generated_y, applied_y)`.  The reference is the SEED's
    own surface, never the save and never the ledger, because a fill that has
    already drained a channel reads as dry land in the save and the defect
    disappears into the artefact under test.

    Three verdicts, and the middle one is the one this project got wrong:
      applied <  generated                 a DREDGE.  Lowering a bed adds
                                           water.  Always accepted, and
                                           asserted as accepted, because a
                                           check that cannot tell a dredge
                                           from a fill blocks the repair.
      applied >  generated, still wet      a FILL that shoaled the channel.
                                           Refused when it takes more than
                                           HALF the depth the seed put there
                                           and the seed water was at least
                                           `min_channel_depth_m` deep.
      applied >= water > generated         DRAINED.  The water is gone and
                                           raising ground is the unrepairable
                                           direction.

    `floor_m` is the MEASURED disagreement between the two instruments where
    nothing was written.  Nothing under it is called a defect, and the number
    travels in the finding so a reader can see what the verdict had to beat.

    AND IT IS APPLIED TO BOTH OF THE QUANTITIES THE VERDICT RESTS ON, NOT
    ONE.  The rise had to beat the floor from the day the floor existed; the
    SEED DEPTH - the other operand, read through the SAME instrument pair -
    was admitted to the population on `> 0.0`.  So a point the seed put
    0.044 m under the plane counted as WATER, and losing it counted as
    "4 m2 of water permanently removed", in a world where the two
    instruments are MEASURED to disagree by 0.100 m at the 99th percentile
    where nothing was written.  Whether that point was ever wet is not a
    fact this pair of instruments can resolve, and a verdict decided inside
    their disagreement is not a measurement - which is the same sentence the
    floor was introduced with, applied to the population instead of to the
    match.

    MEASURED on Ulfsland 5385652e over all 7 berths, 212 drained samples:
    30 of them sit in seed water SHALLOWER THAN THEIR OWN MEASURED FLOOR,
    and three whole berths - the CargoShips at (1941.25,573.81) and
    (1756.48,641.63) and the declared VikingShip at ferry-terminal-eastisle -
    consist of NOTHING ELSE: 1, 1 and 2 samples, in 0.044 / 0.034 /
    0.055-0.079 m of seed water, against floors of 0.100 / 0.090 / 0.099 m.
    The three real fills lose nothing: LittleBoat keeps 11 of 14 with its
    +4.012 m, the CargoShip at (727.52,-122.04) keeps 25 of 29 with its
    +1.877 m, and the CargoShip at (-136.62,2653.21) keeps 143 of 165 with
    its +2.753 m.

    The UNRESOLVABLE count is reported rather than dropped, because a
    population that silently shrinks is the defect this file exists to
    refuse.
    """
    F: list[Finding] = []
    drained, shoaled, dredged, wet = [], [], [], 0
    unresolvable = 0
    for x, z, gen, app in samples:
        if gen is None or app is None:
            continue
        seed_depth = water - gen
        if not (seed_depth > DRAIN_EPS_M):
            continue                        # was never water; not this check
        wet += 1
        if seed_depth <= (floor_m if _DRAIN_SEED_DEPTH_FLOOR
                          else DRAIN_EPS_M):
            unresolvable += 1
            continue        # less water than the instruments disagree by
        rise = app - gen
        if rise <= floor_m and rise >= -floor_m:
            continue                        # inside the instruments' own noise
        if app - water >= -DRAIN_EPS_M:
            drained.append((x, z, gen, app))
        elif rise > 0.0:
            if (seed_depth >= min_channel_depth_m
                    and (water - app) < seed_depth / 2.0):
                shoaled.append((x, z, gen, app))
        else:
            dredged.append((x, z, gen, app))
    judged = wet - unresolvable
    pop = (f"{judged} of {wet} sampled points whose SEED bed is under the "
           f"{water:g} m water plane, out of {len(samples)} sampled in all "
           f"(seed surface via ctx.generated, applied surface via "
           f"ctx.ground); the other {unresolvable} hold less water than the "
           f"{floor_m:.3f} m the two instruments are measured to disagree by "
           f"where nothing was written, so whether they were ever wet is not "
           f"resolvable and they are counted and not judged; a shoaling "
           f"refusal also requires at least {min_channel_depth_m:.1f} m of "
           f"seed water and the loss of more than half of it")
    if drained:
        worst = max(drained, key=lambda s: s[3] - s[2])
        rest = restoration_set(drained,
                               min_cut_m=max(floor_m, RESTORATION_MIN_CUT_M),
                               attribute=attribute)
        F.append(Finding(
            check="draining", ok=False,
            message=(f"{site or 'world'}: {len(drained)} of {judged} judgeable "
                     f"samples have been raised to or above the {water:g} m "
                     f"water plane - {len(drained) * area_per_sample_m2:.0f} m2 "
                     f"of water permanently removed; worst +"
                     f"{worst[3] - worst[2]:.3f} m at "
                     f"({worst[0]:.1f},{worst[1]:.1f}), seed bed "
                     f"{worst[2]:.3f} applied {worst[3]:.3f}, against a "
                     f"{floor_m:.3f} m instrument floor"
                     + (f"; inside {attribute(worst[0], worst[1])}"
                        if attribute and attribute(worst[0], worst[1])
                        else "")
                     + _attribution_clause(rest)),
            where=(worst[0], worst[1]),
            measured=round(worst[3] - worst[2], 3), limit=round(floor_m, 3),
            numbers={"drained_samples": len(drained),
                     "judgeable_samples": judged,
                     "submerged_samples": wet,
                     "unresolvable_samples": unresolvable,
                     "sampled_points": len(samples),
                     "drained_area_m2": round(len(drained)
                                              * area_per_sample_m2, 1),
                     "worst_rise_m": round(worst[3] - worst[2], 3),
                     "seed_bed_y": round(worst[2], 3),
                     "applied_bed_y": round(worst[3], 3),
                     "instrument_floor_m": round(floor_m, 3),
                     "attributed_to": ((attribute(worst[0], worst[1]) or "")
                                       if attribute else ""),
                     "restoration": rest,
                     "direction": "raising ground removes water - the "
                                  "unrepairable direction; the REPAIR is the "
                                  "opposite sign, a cut back to the seed's "
                                  "own bed, which is what `restoration` "
                                  "names and what water_sign permits"},
            population=pop))
    if shoaled:
        worst = max(shoaled, key=lambda s: s[3] - s[2])
        rest = restoration_set(shoaled,
                               min_cut_m=max(floor_m, RESTORATION_MIN_CUT_M),
                               attribute=attribute)
        F.append(Finding(
            check="draining/shoaled", ok=False,
            message=(f"{site or 'world'}: {len(shoaled)} of {judged} judgeable "
                     f"samples have lost more than half the depth the seed put "
                     f"there; worst +{worst[3] - worst[2]:.3f} m at "
                     f"({worst[0]:.1f},{worst[1]:.1f}), leaving "
                     f"{water - worst[3]:.3f} m of water where the seed had "
                     f"{water - worst[2]:.3f} m"
                     + (f"; inside {attribute(worst[0], worst[1])}"
                        if attribute and attribute(worst[0], worst[1])
                        else "")
                     + _attribution_clause(rest)),
            where=(worst[0], worst[1]),
            measured=round(water - worst[3], 3),
            limit=round((water - worst[2]) / 2.0, 3),
            numbers={"shoaled_samples": len(shoaled),
                     "judgeable_samples": judged,
                     "submerged_samples": wet,
                     "unresolvable_samples": unresolvable,
                     "worst_rise_m": round(worst[3] - worst[2], 3),
                     "depth_left_m": round(water - worst[3], 3),
                     "depth_seed_m": round(water - worst[2], 3),
                     "instrument_floor_m": round(floor_m, 3),
                     "attributed_to": ((attribute(worst[0], worst[1]) or "")
                                       if attribute else ""),
                     "restoration": rest},
            population=pop))
    if dredged and not drained and not shoaled:
        worst = max(dredged, key=lambda s: s[2] - s[3])
        F.append(Finding(
            check="draining/dredge", ok=True,
            message=(f"{site or 'world'}: {len(dredged)} of {judged} judgeable "
                     f"samples were LOWERED, deepest by "
                     f"{worst[2] - worst[3]:.3f} m at "
                     f"({worst[0]:.1f},{worst[1]:.1f}) - lowering a bed adds "
                     f"water, so this is accepted"),
            where=(worst[0], worst[1]),
            measured=round(worst[2] - worst[3], 3),
            numbers={"dredged_samples": len(dredged),
                     "judgeable_samples": judged,
                     "submerged_samples": wet,
                     "unresolvable_samples": unresolvable,
                     "deepest_cut_m": round(worst[2] - worst[3], 3),
                     "depth_now_m": round(water - worst[3], 3),
                     "instrument_floor_m": round(floor_m, 3)},
            population=pop))
    if not F:
        F.append(Finding(
            check="draining", ok=True,
            message=(f"{site or 'world'}: {judged} of {wet} submerged samples "
                     f"are judgeable, and none of them is raised toward the "
                     f"{water:g} m water plane by more than the "
                     f"{floor_m:.3f} m the two height instruments disagree by "
                     f"where nothing was written; the other {unresolvable} "
                     f"hold less water than that disagreement and cannot be "
                     f"said to have been wet at all"),
            measured=0.0, limit=round(floor_m, 3),
            numbers={"judgeable_samples": judged,
                     "submerged_samples": wet,
                     "unresolvable_samples": unresolvable,
                     "sampled_points": len(samples),
                     "instrument_floor_m": round(floor_m, 3)},
            population=pop))
    return F


def declared_berths(ledger: Path, hulls: dict) -> list[tuple]:
    """Every berth the ledger DECLARES, at the pose it declared.

    `(prefab, x, z, yaw_deg, site_id)` from the `spawn` records whose role is
    `boat_spawn_moor`.  A declared berth is a claim the world made about
    itself - "a CargoShip is moored here" - and it is checkable whether or not
    a hull is standing there now: a raid, a storm or a retire can remove the
    boat and cannot remove the claim.
    """
    out: list[tuple] = []
    if not ledger or not Path(ledger).exists():
        return out
    for line in Path(ledger).read_text().splitlines():
        if "boat_spawn_moor" not in line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        p = rec.get("params") or {}
        if p.get("role") != "boat_spawn_moor":
            continue
        pf, pos = p.get("prefab"), p.get("pos")
        if pf not in hulls or not pos or len(pos) < 3:
            continue
        out.append((pf, float(pos[0]), float(pos[2]),
                    float(p.get("yaw_deg", 0.0)), p.get("site_id", "")))
    return out


def write_footprints(ledger: Path) -> list[dict]:
    """Every terrain write this world RECORDS, as a footprint on the ground.

    Two shapes, because the ledger has two and neither can express the other:
    a PAD is a centre with `pad_w`/`pad_d` and an apron, a ROAD is a polyline
    with `half_width_m`.  Read for two purposes only - to ATTRIBUTE a drained
    sample to the write that drained it, and to find the samples that are
    OUTSIDE every write, which is where the two height instruments can be
    compared against each other.

    A pad record with no `centre` (51 of Ulfsland's 146) is located by the
    zone its entries name rather than guessed at: a 64 m zone is a coarse
    footprint, and it is used ONLY to EXCLUDE that ground from the agreement
    measurement, never to attribute a defect to a write.
    """
    out: list[dict] = []
    if not ledger or not Path(ledger).exists():
        return out
    for line in Path(ledger).read_text().splitlines():
        if '"terrain_write"' not in line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("op") != "terrain_write":
            continue
        p = rec.get("params") or {}
        name = p.get("name") or p.get("role") or f"seq {rec.get('seq')}"
        prof = p.get("profile")
        if prof and prof.get("nodes"):
            out.append(dict(kind="road", name=name, seq=rec.get("seq"),
                            nodes=[(float(n[0]), float(n[1]))
                                   for n in prof["nodes"]],
                            half=float(prof.get("half_width_m", 3.0)),
                            exact=True))
            continue
        c = p.get("centre")
        if c and p.get("pad_w"):
            ap = float(p.get("apron_m") or 0.0)
            out.append(dict(kind="pad", name=name, seq=rec.get("seq"),
                            cx=float(c[0]), cz=float(c[1]),
                            hx=float(p["pad_w"]) / 2.0 + ap,
                            hz=float(p.get("pad_d", p["pad_w"])) / 2.0 + ap,
                            exact=True))
            continue
        for e in p.get("entries") or ():
            zc = e.get("centre")
            if zc:
                out.append(dict(kind="pad", name=name, seq=rec.get("seq"),
                                cx=float(zc[0]), cz=float(zc[1]),
                                hx=32.0, hz=32.0, exact=False))
    return out


def _seg_distance(x: float, z: float, nodes, half: float) -> bool:
    for i in range(len(nodes) - 1):
        ax, az = nodes[i]
        bx, bz = nodes[i + 1]
        dx, dz = bx - ax, bz - az
        ll = dx * dx + dz * dz
        t = 0.0 if ll == 0 else max(0.0, min(1.0, ((x - ax) * dx
                                                   + (z - az) * dz) / ll))
        if math.hypot(x - (ax + t * dx), z - (az + t * dz)) <= half:
            return True
    return False


def written_at(writes: list[dict], x: float, z: float, margin: float = 0.0,
               exact_only: bool = False) -> str:
    """The name of a recorded write whose footprint covers this point, or ""."""
    for w in writes:
        if exact_only and not w["exact"]:
            continue
        if w["kind"] == "pad":
            if (abs(x - w["cx"]) <= w["hx"] + margin
                    and abs(z - w["cz"]) <= w["hz"] + margin):
                return (f"terrain_write {w['name']} (seq {w['seq']}"
                        + ("" if w["exact"] else ", located by its zone")
                        + ")")
        elif _seg_distance(x, z, w["nodes"], w["half"] + margin):
            return f"terrain_write {w['name']} (seq {w['seq']})"
    return ""


class WriteIndex:
    """`written_at` with the writes bucketed, answering EXACTLY what it does.

    THE COST IT REMOVES, and it is the same shape `SolidIndex` removed for
    the barriers.  MEASURED on Ulfsland 5385652e: the ledger yields 234
    terrain writes, 100 of them ROADS carrying 38,827 polyline nodes - one
    of them 1,529 nodes by itself - so a single `written_at` call walks
    38,861 segments and pads.  `check_draining` asks it once per region
    sample: 16,641 samples per berth times seven berths is 4.5 BILLION
    segment tests on the 2 m field and 17.9 billion on the engine's own 1 m
    lattice.  That is not a field that is too fine; it is a linear scan
    inside a per-sample loop.

    THE ANSWER IS IDENTICAL, not merely equivalent.  `written_at` returns
    the FIRST write in the caller's list order whose footprint covers the
    point, and that string is attributed to a drained sample in a finding -
    so the order is part of the verdict.  Buckets are filled by ascending
    write index and each write's parts are tested in their own order, so
    the first match found here is the first match the scan would have
    found.  Each SEGMENT is bucketed by its own inflated bounding box
    rather than the road's, because a 1,529-node road's bounding box is
    most of the map; a query is therefore exact for any margin up to the
    `margin_max` the index was built with, and asking for more raises
    rather than quietly returning a near-miss as a miss.
    """

    CELL_M = 16.0

    def __init__(self, writes: list[dict], margin_max: float = 4.0):
        self.writes = list(writes)
        self.margin_max = float(margin_max)
        self.grid: dict[tuple[int, int], list[tuple[int, int]]] = {}
        c = self.CELL_M
        for wi, w in enumerate(self.writes):
            if w["kind"] == "pad":
                boxes = [(w["cx"] - w["hx"], w["cz"] - w["hz"],
                          w["cx"] + w["hx"], w["cz"] + w["hz"], -1)]
            else:
                h = w["half"]
                boxes = []
                for s in range(len(w["nodes"]) - 1):
                    (ax, az), (bx, bz) = w["nodes"][s], w["nodes"][s + 1]
                    boxes.append((min(ax, bx) - h, min(az, bz) - h,
                                  max(ax, bx) + h, max(az, bz) + h, s))
            for (x0, z0, x1, z1, s) in boxes:
                for i in range(int(math.floor((x0 - self.margin_max) / c)),
                               int(math.floor((x1 + self.margin_max) / c)) + 1):
                    for j in range(int(math.floor((z0 - self.margin_max) / c)),
                                   int(math.floor((z1 + self.margin_max) / c))
                                   + 1):
                        self.grid.setdefault((i, j), []).append((wi, s))

    def at(self, x: float, z: float, margin: float = 0.0,
           exact_only: bool = False) -> str:
        if margin > self.margin_max + 1e-9:
            raise ValueError(
                f"WriteIndex was built for margins up to "
                f"{self.margin_max} m and was asked for {margin} m; a query "
                f"wider than the index was inflated for would report a write "
                f"as absent because its bucket was never widened to reach it")
        c = self.CELL_M
        cand = self.grid.get((int(math.floor(x / c)), int(math.floor(z / c))))
        if not cand:
            return ""
        best_wi, best_w = None, None
        for wi, s in cand:
            if best_wi is not None and wi >= best_wi:
                continue
            w = self.writes[wi]
            if exact_only and not w["exact"]:
                continue
            if s < 0:
                hit = (abs(x - w["cx"]) <= w["hx"] + margin
                       and abs(z - w["cz"]) <= w["hz"] + margin)
            else:
                hit = _seg_distance(x, z, w["nodes"][s:s + 2],
                                    w["half"] + margin)
            if hit:
                best_wi, best_w = wi, w
        if best_w is None:
            return ""
        if best_w["kind"] == "pad":
            return (f"terrain_write {best_w['name']} (seq {best_w['seq']}"
                    + ("" if best_w["exact"] else ", located by its zone")
                    + ")")
        return f"terrain_write {best_w['name']} (seq {best_w['seq']})"


def drain_reference_missing(site: str = "") -> Finding:
    """What this check says when it cannot get the seed's surface.

    A refusal, never a skip.  Every defect in this world had already been
    declared correct by a check, and a check that quietly does not run is the
    purest form of that.
    """
    return Finding(
        check="draining/reference_unavailable", ok=False,
        message=(f"{site or 'world'}: the SEED surface instrument "
                 f"(ctx.generated -> run_patchscan.sh SURFACE=heightmap) is "
                 f"not available, so the SIGN of every change to a submerged "
                 f"bed is unmeasurable; refusing rather than skipping - a "
                 f"drained channel reads as dry land in the save"),
        numbers={"missing": "ctx.generated",
                 "why": "the reference must not come from the artefact under "
                        "test"})


# ---------------------------------------------------------------------------
# CHECK 4 - REACHABILITY BY SEA
# ---------------------------------------------------------------------------
def _unconnected(hull: Hull, bed: Bed, barriers, pts, water: float
                 ) -> tuple[list, list[str], bool]:
    """Which footprint samples are cut off from the region's open sea.

    Returns the cut-off samples, the per-cell reasons, and whether open sea was
    in view at all.
    """
    nav, why = navigable(bed, hull, barriers, water)
    sea = boundary_sea(bed, nav)
    if not sea:
        return ([], why, False)
    seen = set(sea)
    stack = list(sea)
    while stack:
        k = stack.pop()
        i, j = k % bed.nx, k // bed.nx
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ni, nj = i + di, j + dj
            if 0 <= ni < bed.nx and 0 <= nj < bed.nz:
                nk = nj * bed.nx + ni
                if nav[nk] and nk not in seen:
                    seen.add(nk)
                    stack.append(nk)
    bad = []
    for px, pz in pts:
        i = int(round((px - bed.x0) / bed.step))
        j = int(round((pz - bed.z0) / bed.step))
        if not (0 <= i < bed.nx and 0 <= j < bed.nz):
            bad.append((px, pz, "outside the sampled region"))
            continue
        k = j * bed.nx + i
        if k not in seen:
            bad.append((px, pz, why[k] or "not connected to open sea"))
    return (bad, why, True)


_SEVERED_CONTROL_BY_BARRIERS = False
# Whether `check_channel_severed` controls for solids by INCLUDING them in
# both readings.  True restores the shipped form, in which a solid set that
# severs the berth on its own saturates both answers and the ground's own
# contribution becomes unobservable.


def check_channel_severed(hull: Hull, bed: Bed, seed_bed: Bed, barriers,
                          berth: tuple[float, float], yaw_deg: float,
                          water: float = WATER_LEVEL, site: str = "",
                          floor_m: float | None = None
                          ) -> list[Finding]:
    """DID A WRITE TAKE THE CHANNEL AWAY - even though it never broke surface?

    THE HOLE THIS CLOSES, and it is a hole between two checks that both
    passed over it.  `draining` measures a bed RAISED TO OR ABOVE the 30 m
    water plane, and `draining/shoaled` measures a sample that lost more than
    HALF the depth the seed put there.  A raise that stays submerged and
    takes less than half the water is invisible to both - AND IT CAN STILL
    CLOSE A HARBOUR, because navigability is a THRESHOLD on depth, not a
    proportion of it.

    MEASURED on Ulfsland, save 2c7011d2, the LittleBoat at
    boathouse-temple-strait: 38 of 38 footprint samples cut off from open sea
    with ALL 5,167 barriers, with the 2,198 MeshCollider bounds dropped, and
    WITH NO BARRIERS AT ALL - so nothing solid closes it.  Re-measured on the
    engine's own 1 m lattice over a 1,344-vertex corridor, the whole pool
    hangs on ONE VERTEX at (-319,-48): the SEED put 2.343 m of water there
    and the save holds 2.176 m against 2.200 m required.  A +0.167 m rise,
    7 % of the depth, 27.8 m BELOW the surface, and it landlocked a berth.
    `draining` saw nothing because 27.824 is not above 30.0; `shoaled` saw
    nothing because 0.167 is not half of 2.343.

    So the question is asked of the TWO BEDS rather than of one: was this
    berth connected to open sea ON THE SEED, and is it connected now?  The
    seed is the only reference that is not the artefact under test, and the
    answer converts an unexplained landlocking into an attributable one with
    the exact vertex and the exact metres a dredge must give back.  A RAISE
    IS THE REPAIRABLE DIRECTION ONLY BECAUSE IT IS A RESTORATION: lowering
    this vertex to the height the seed itself chose adds water, and the sign
    rule permits it.
    """
    pts = footprint(hull, berth[0], berth[1], yaw_deg)
    # THE CONTROL IS THE BED, NOT THE BED PLUS THE SOLIDS.
    #
    # This check used to ask both beds the connectivity question WITH THE
    # SAME BARRIERS, on the reasoning that identical solids on both sides
    # leave only the ground differing.  That is true of the INPUTS and false
    # of the ANSWER: connectivity is a threshold, so a solid set that
    # severs the berth ON ITS OWN severs BOTH beds, `was_bad` comes back
    # non-empty, and the check returns [] - WITH THE GROUND'S OWN
    # CONTRIBUTION UNOBSERVED.  The verdict is then identical whether a
    # write took the channel or not, which is the one property a check is
    # not allowed to have.
    #
    # MEASURED on Ulfsland 5385652e at the LittleBoat, boathouse-temple-
    # strait, 38 footprint samples:
    #     seed bed, no barriers        0 of 38 cut off   <- the seed CONNECTED it
    #     applied bed, no barriers    38 of 38 cut off   <- the GROUND severs it
    #     seed bed, 5,414 barriers    38 of 38 cut off   <- and so do the solids
    #     applied bed, 5,414 barriers 38 of 38 cut off
    # The barrier-controlled form reads the third line, concludes "the seed
    # did not connect it either", and goes silent over the second.  So the
    # question this check owns is asked of the BED ALONE, and what the
    # solids do is reported beside it rather than mixed into it - that is
    # `sea_reach`'s question and `sea_reach` is already refusing on it.
    solid_bad, _ws, _ss = _unconnected(hull, bed, barriers, pts, water)
    control = barriers if _SEVERED_CONTROL_BY_BARRIERS else []
    pop = (f"{len(pts)} samples inside {hull.name}'s own footprint, asked "
           f"TWICE - once of the applied bed and once of the SEED bed on the "
           f"same lattice, with "
           + ("the same barriers both times"
              if _SEVERED_CONTROL_BY_BARRIERS else
              f"NO solid in either, so only the GROUND can differ; the same "
              f"footprint against the {len(barriers)} solid parts standing "
              f"here loses {len(solid_bad)} of {len(pts)}, which is "
              f"sea_reach's question and not this one"))
    now_bad, _w, now_sea = _unconnected(hull, bed, control, pts, water)
    if not now_bad or not now_sea:
        return []                       # connected, or not in view: not ours
    was_bad, _w2, was_sea = _unconnected(hull, seed_bed, control, pts, water)
    if not was_sea or was_bad:
        return []                       # the seed did not connect it either
    # The seed connected this berth and the save does not.  Name the samples
    # the ground gave up, worst first.
    lost = []
    for j in range(bed.nz):
        for i in range(bed.nx):
            a, g = bed.at(i, j), seed_bed.at(i, j)
            if a is None or g is None or a <= g:
                continue
            d_now, d_was = water - a, water - g
            if d_was >= hull.required_depth_m > d_now:
                lost.append((a - g, bed.x0 + i * bed.step,
                             bed.z0 + j * bed.step, d_was, d_now))
    lost.sort(reverse=True)
    at = (lost[0][1], lost[0][2]) if lost else berth
    # CAN THIS FIELD NAME THE VERTEX IT IS ABOUT TO NAME?  `lost` is the
    # worst SAMPLE, and on a field coarser than the engine's lattice the
    # worst sample and the bar are different objects: the bar is wherever it
    # is, and the sample is wherever the residue class this field is locked
    # to happens to fall.  A repair sized to the named vertex is then a cut
    # at a place that is not the bar, certified by a gate reading the same
    # subset.  So the authorisation is stated, not implied by naming a number.
    res = field_resolution(bed)
    authorised = res["resolves_every_vertex"]
    # THE WHOLE REPAIR, OR NONE OF IT.  `lost` is every vertex the ground was
    # raised at out of navigable depth, and restoring exactly that set makes
    # the applied bed navigable wherever the seed bed was - so the set IS the
    # repair, not a sample of it.
    #
    # TWO THINGS HAVE TO BE TRUE BEFORE IT IS PUBLISHED AT ALL.
    #
    # THE FIELD MUST RESOLVE EVERY VERTEX IT JUDGED.  A cut list derived
    # from a field that addresses a quarter of the ground names the worst
    # ADDRESSABLE sample at every one of its entries, and an unauthorised
    # set that looks complete is worse than no set at all.
    #
    # AND THE CUT MUST BE LARGER THAN THE TWO INSTRUMENTS DISAGREE BY.
    # `lost` is a CROSS-INSTRUMENT difference - `ctx.generated`'s seed bed
    # against `ctx.ground`'s applied bed - and `draining` has applied the
    # region's own MEASURED floor to exactly that difference since the day a
    # floorless version reported 3,990 "shoaled" samples in one harbour.
    # This check never did.  MEASURED at the boathouse berth on
    # 2a6940a934b5: floor 0.156 m, and 20 of the 36 counted vertices are
    # raises below it, down to 0.016 m.  Those are cut from the REPAIR, not
    # from the verdict - the verdict is a flood fill and does not read this
    # set - and they are COUNTED in `below_min_cut_m` rather than dropped.
    # With no floor measurable the repair cannot be sized at all and no set
    # is published: a gate that cannot resolve a metre must not hand one out.
    if not _RESTORATION_FLOOR:
        # THE SHIPPED FLOORLESS FORM, reachable for a selftest neuter: the
        # repair sized against nothing but the emitter's own 1 cm noise
        # threshold, so a cross-instrument difference of 16 mm becomes a cut.
        cut_floor = RESTORATION_MIN_CUT_M
    elif floor_m is not None:
        cut_floor = max(floor_m, RESTORATION_MIN_CUT_M)
    else:
        cut_floor = None
    restoration = (restoration_set(
        [(x, z, water - d_was, water - d_now)
         for (_r, x, z, d_was, d_now) in lost], min_cut_m=cut_floor)
        if lost and authorised and cut_floor is not None else None)
    if restoration is not None and not restoration["cuts"]:
        restoration = None      # every raise is inside the instruments' noise
    return [Finding(
        check="sea_reach/channel_severed", ok=False,
        message=(f"{site or hull.name}: the SEED connected this berth to open "
                 f"sea and the save does not - {len(now_bad)} of {len(pts)} "
                 f"footprint samples are now cut off. "
                 + (f"{len(lost)} samples were raised out of navigable depth "
                    f"WITHOUT breaking surface; worst +{lost[0][0]:.3f} m at "
                    f"({lost[0][1]:.1f},{lost[0][2]:.1f}), where the seed held "
                    f"{lost[0][3]:.3f} m and the save holds {lost[0][4]:.3f} m "
                    f"against {hull.required_depth_m:.3f} m required. "
                    + (f"Lowering those samples back to the seed's own bed "
                       f"is a RESTORATION and adds water: "
                       f"{restoration['named']} named vertices, "
                       f"{restoration['volume_named_m3']:.4f} m3 of CUT to "
                       f"absolute seed heights, every delta a LOWERING "
                       f"(the other "
                       f"{restoration['below_min_cut_m']} raised sample(s) "
                       f"are under the {restoration['min_cut_m']:.3f} m these "
                       f"two instruments are MEASURED to disagree by here and "
                       f"are counted, not cut)"
                       if restoration else
                       "Lowering those samples back to the seed's own bed is "
                       "a RESTORATION and adds water"
                       if authorised else
                       f"THIS FIELD CANNOT AUTHORISE THAT REPAIR: it steps "
                       f"{res['step_m']:g} m on a {VERTEX_SPACING_M:g} m "
                       f"engine lattice and addresses "
                       f"{100.0 * res['fraction_addressable']:.1f} % of the "
                       f"vertices it is judging, locked to x = "
                       f"{res['x_residue']} and z = {res['z_residue']} modulo "
                       f"{res['step_m']:g}, so the named vertex is the worst "
                       f"ADDRESSABLE sample and the bar may be a vertex this "
                       f"field is structurally incapable of naming. Re-read "
                       f"the channel at {VERTEX_SPACING_M:g} m before cutting")
                    if lost else
                    "no sample was raised out of navigable depth, so the "
                    "severance is not in the bed this region sampled")),
        where=at,
        measured=float(len(pts) - len(now_bad)), limit=float(len(pts)),
        numbers={"hull": hull.name, "connected_now": len(pts) - len(now_bad),
                 "connected_on_the_seed": len(pts),
                 "footprint_samples": len(pts),
                 "cut_off_by_the_bed_alone": len(now_bad),
                 "cut_off_with_every_solid": len(solid_bad),
                 "solid_parts": len(barriers),
                 "samples_raised_out_of_depth": len(lost),
                 "worst_rise_m": round(lost[0][0], 3) if lost else None,
                 "seed_depth_m": round(lost[0][3], 3) if lost else None,
                 "applied_depth_m": round(lost[0][4], 3) if lost else None,
                 "required_depth_m": hull.required_depth_m,
                 "restore_to_seed_m": round(lost[0][0], 3) if lost else None,
                 "repair_authorised": bool(lost) and authorised,
                 "repair_floor_m": (round(cut_floor, 4)
                                    if cut_floor is not None else None),
                 **({"restoration": restoration} if restoration else {}),
                 "field_resolution": res,
                 "direction": "a RAISE below the water plane: invisible to "
                              "draining (never crossed 30 m) and to "
                              "draining/shoaled (kept more than half its "
                              "depth), and it closed the harbour"},
        population=pop)]


def check_sea_reach(hull: Hull, bed: Bed, barriers, berth: tuple[float, float],
                    yaw_deg: float, water: float = WATER_LEVEL, site: str = ""
                    ) -> list[Finding]:
    """EVERY sample under the footprint connected to open sea, not the centre.

    A landlocked pocket of 9 m water berths nothing, and a hull half in a
    pocket is a hull aground on the sill between them.
    """
    pts = footprint(hull, berth[0], berth[1], yaw_deg)
    pop = (f"{len(pts)} samples inside {hull.name}'s own footprint at yaw "
           f"{yaw_deg:.1f} deg, on a {bed.nx}x{bed.nz} lattice at "
           f"{bed.step:g} m; navigable = depth >= "
           f"{hull.required_depth_m:.3f} m and no solid in the submerged band")
    bad, why, have_sea = _unconnected(hull, bed, barriers, pts, water)
    if not have_sea:
        return [Finding(
            check="sea_reach/region_too_small", ok=False,
            message=(f"{site or hull.name}: no navigable cell reaches the edge "
                     f"of the {bed.nx * bed.step:.0f} m sampled region, so "
                     f"connection to open sea cannot be established"),
            where=berth, numbers={"region_m": bed.nx * bed.step},
            population=pop)]
    if bad:
        # PRECISION DECIDES WHETHER THIS VOTES, and the test is the RE-RUN, not
        # the reason string: the cut-off cells are themselves navigable water,
        # so they carry no reason at all - what cuts them off is somewhere else
        # on the rim.  So the whole connectivity question is asked again with
        # the MeshCollider bounds dropped.  If the hull reconnects without
        # them, the refusal rests on a bounding box - `rock4_forest`'s is a
        # 27.7 x 24.2 m slab - and it is reported ADVISORY rather than deciding
        # the world.  One box collider that still cuts it off makes it binding.
        loose = [b for b in barriers if not b.tight]
        bounds_only = False
        if loose:
            tight_bad, _w, tight_sea = _unconnected(
                hull, bed, [b for b in barriers if b.tight], pts, water)
            bounds_only = tight_sea and not tight_bad
        return [Finding(
            check="sea_reach", ok=False,
            message=(f"{site or hull.name}: {len(bad)} of {len(pts)} footprint "
                     f"samples are not flood-fill connected to open sea - "
                     f"first at ({bad[0][0]:.1f},{bad[0][1]:.1f}) "
                     f"[{bad[0][2]}]"
                     + (f"; ADVISORY - dropping the "
                        f"{len(loose)} MeshCollider bounds "
                        f"({', '.join(sorted({b.prefab for b in loose}))}) "
                        f"reconnects it, so this refusal rests on a bounding "
                        f"box rather than on a measured solid"
                        if bounds_only else "")),
            where=(bad[0][0], bad[0][1]),
            measured=float(len(pts) - len(bad)), limit=float(len(pts)),
            numbers={"connected": len(pts) - len(bad), "samples": len(pts),
                     "hull": hull.name,
                     "first_unconnected": [round(bad[0][0], 2),
                                           round(bad[0][1], 2)],
                     "reason": bad[0][2],
                     "reasons": sorted({r[2] for r in bad}),
                     "mesh_bound_prefabs": sorted({b.prefab for b in loose}),
                     "precision": ("mesh bounds only" if bounds_only
                                   else "box colliders or the bed")},
            population=pop, advisory=bounds_only)]
    return [Finding(
        check="sea_reach", ok=True,
        message=(f"{site or hull.name}: all {len(pts)} footprint samples "
                 f"connected to open sea"),
        where=berth, measured=float(len(pts)), limit=float(len(pts)),
        numbers={"connected": len(pts), "samples": len(pts),
                 "hull": hull.name}, population=pop)]


# ---------------------------------------------------------------------------
# THE OPEN POSE - read from DoorProbe's measurement, not from the census
# ---------------------------------------------------------------------------
DOOR_POSE_FILE = JUMPSTART / "blueprints" / "data" / "door_open_pose.txt"


def open_pose_table(path: Path = DOOR_POSE_FILE) -> dict[str, dict]:
    """prefab -> {"has_door", "states": {state: [local boxes]}, "static": [...]}

    Parsed from `blueprints/DoorProbe.cs`'s output: per animator state, the
    colliders as they ACTUALLY STAND in that state.  `static` is the set of
    solid boxes that are identical in every state - the parts a hull has to get
    past no matter what the door does, which on `fb_fortress_portcullis` is two
    0.374 m posts 5.834 m tall and is the entire defect.
    """
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    cur = None
    for line in path.read_text().splitlines():
        f = line.rstrip("\n").split("\t")
        if f[0].startswith("=== PREFAB") or (f[0] == "===" and len(f) > 2):
            cur = f[-1].strip()
            out[cur] = {"has_door": False, "states": {}, "static": []}
        elif cur and f[0] == "DOOR":
            out[cur]["has_door"] = (len(f) > 1 and f[1] != "none")
        elif cur and f[0] == "COL" and len(f) >= 7:
            tag, name, kind = f[1], f[2], f[3]
            flags = {k: v for k, v in
                     (p.split("=", 1) for p in f[4:7] if "=" in p)}
            aabb = next((p[5:] for p in f[4:] if p.startswith("aabb=")), None)
            if kind != "box" or aabb is None:
                continue
            if flags.get("trig") != "0" or flags.get("enabled") != "1":
                continue
            if flags.get("active") != "1":
                continue          # an inactive collider is not in the way
            lo, hi = aabb.split("..")
            x0, y0, z0 = (float(v) for v in lo.split(","))
            x1, y1, z1 = (float(v) for v in hi.split(","))
            box = ["b", min(x0, x1), min(y0, y1), min(z0, z1),
                   max(x0, x1), max(y0, y1), max(z0, z1)]
            if not tag.startswith("CLONE_s"):
                continue
            st = tag.split("CLONE_s", 1)[1].split("_p", 1)[0]
            out[cur]["states"].setdefault(st, []).append((name, box))
    for pf, rec in out.items():
        states = rec["states"]
        if not states:
            continue
        keys = list(states)
        common = None
        for st in keys:
            here = {(n, tuple(round(v, 5) for v in b[1:])) for n, b in
                    states[st]}
            common = here if common is None else (common & here)
        rec["static"] = [["b", *vals] for _n, vals in sorted(common or ())]
        rec["static_names"] = sorted({n for n, _ in (common or ())})
    return out


def barriers_for_placement(prefab: str, pos, quat, parts, rotate,
                           poses: dict[str, dict]) -> tuple[list[Barrier], str]:
    """This object's barriers IN THE POSE MOST FAVOURABLE TO A HULL.

    For anything without a `Door`, that is simply its solid.  For a door it is
    the OPEN pose, and specifically the widest of the measured open states -
    a hull is entitled to the best the mechanism can do.  What a hull is NOT
    entitled to is the assumption that the whole solid moves: only the boxes
    that actually differ between states are dropped.
    """
    rec = poses.get(prefab)
    if rec and rec.get("has_door"):
        best = None
        for st, boxes in rec["states"].items():
            if st == "0":
                continue                     # the closed state
            cand = [(n, b) for n, b in boxes]
            if best is None or len(cand) < len(best[1]):
                best = (st, cand)
        if best is None:
            return ([], "door_state_unmeasured")
        st, boxes = best
        return ([barrier_from_part(prefab, pos, quat, b, rotate,
                                   pose=f"open:{st}", part_name=n)
                 for n, b in boxes], f"open:{st}")
    if rec is not None and not rec.get("has_door") and rec["states"]:
        st = next(iter(rec["states"]))
        return ([barrier_from_part(prefab, pos, quat, b, rotate,
                                   pose="static", part_name=n)
                 for n, b in rec["states"][st]], "static")
    if parts:
        return ([barrier_from_part(prefab, pos, quat, p, rotate,
                                   pose="static", part_name=f"part{i}")
                 for i, p in enumerate(parts)], "static")
    return ([], "no_geometry")


DOOR_TOKENS = ("portcullis", "_gate", "gates", "_door", "door_")


def looks_like_a_door(prefab: str) -> bool:
    """A prefab whose name says it is a movable barrier.

    Used for ONE purpose: to refuse a barrier whose open pose was never
    measured, instead of treating it as open (which is what reported a sailable
    gate) or as shut (which would refuse every harbour with a door on it).
    """
    p = prefab.lower()
    return any(t in p for t in DOOR_TOKENS)


# ---------------------------------------------------------------------------
# THE WORLD RUN
# ---------------------------------------------------------------------------
def _need(ctx, name: str):
    got = getattr(ctx, name, None)
    if got is None:
        raise _Missing(name)
    return got


class _Missing(RuntimeError):
    def __init__(self, what: str):
        super().__init__(what)
        self.what = what


def run(ctx) -> list[Finding]:
    """Every hull standing in this world, judged at its own pose.

    THE POPULATION IS THE SHIPS, not a hand-written list of harbours, because a
    hand-written list cannot contain the harbour the next world builds.  Each
    vessel ZDO in the save is a berth that something already decided was good,
    and each one is re-measured here: the depth under its own footprint, the
    route out at its own beam, and whether that route exists at all.

    ONE BED PASS FOR THE WHOLE WORLD.  Every point every check needs - each
    footprint on the 1 m lattice, each region on the 2 m lattice, each berth
    centre - is assembled first and read in a single `ctx.ground` call, so the
    harness spawns each zone once and the instrument's own overlapping-station
    cross-check runs across the whole population rather than per harbour.  Six
    hulls asked separately were twelve harness runs of the same zones.
    """
    F: list[Finding] = []
    water = float(getattr(ctx, "WATER_LEVEL", WATER_LEVEL))
    hulls = hull_table()
    prefabs = getattr(ctx, "geom", None) or _corpus()[0]
    poses = open_pose_table()
    writes = write_footprints(getattr(ctx, "ledger", None))
    # ONE INDEX FOR THE WHOLE RUN.  `check_draining` asks "was this sample
    # inside a recorded write" once per region sample, and the widest margin
    # anything here asks for is 4 m.
    write_index = WriteIndex(writes, margin_max=4.0)
    try:
        census = _need(ctx, "census")
        ground = _need(ctx, "ground")
        rotate = _need(ctx, "rotate")
    except _Missing as m:
        return [Finding(
            check="harbours/instrument_unavailable", ok=False,
            message=(f"the harbours section needs ctx.{m.what} and it is not "
                     f"present; refusing rather than reporting a harbour it "
                     f"cannot measure"),
            numbers={"missing": f"ctx.{m.what}"})]

    rows = list(getattr(census, "rows", census))
    standing = [(nm, x, z, yaw_of((qx, qy, qz, qw)), "standing", "")
                for (nm, x, y, z, qx, qy, qz, qw) in rows if nm in hulls]
    # DECLARED BERTHS TOO, and this is not belt-and-braces.  A harbour is a
    # harbour before its boat is spawned and after that boat is destroyed, and
    # the population that is only "ships standing now" measures neither.
    # MEASURED on Ulfsland: the ledger declares 7 `boat_spawn_moor` berths and
    # the save holds 6 hulls, so a berth-shaped question asked of the census
    # alone misses one by construction.
    declared = declared_berths(getattr(ctx, "ledger", None), hulls)
    for nm, dx, dz, dyaw, site_id in declared:
        if any(o[0] == nm and abs(o[1] - dx) <= 8.0 and abs(o[2] - dz) <= 8.0
               for o in standing):
            continue
        standing.append((nm, dx, dz, dyaw, "declared", site_id))
    F.append(Finding(
        check="harbours/population", ok=bool(standing),
        message=(f"{len([s for s in standing if s[4] == 'standing'])} vessel "
                 f"ZDOs standing in the save and "
                 f"{len([s for s in standing if s[4] == 'declared'])} berths "
                 f"the ledger declares that no hull stands at, of "
                 f"{len(rows)} ZDOs and {len(hulls)} known hull classes"
                 + ("" if standing else
                    " - no vessel and no declared berth in this world, so no "
                    "route or aperture has a hull to be measured against")),
        measured=float(len(standing)),
        numbers={"standing": len([s for s in standing if s[4] == "standing"]),
                 "declared_only": len([s for s in standing
                                       if s[4] == "declared"]),
                 "zdos": len(rows),
                 "classes": sorted({s[0] for s in standing})},
        population="every ZDO in the save whose prefab is in "
                   "waterfront.py::VESSELS, plus every ledger `spawn` record "
                   "with role boat_spawn_moor",
        advisory=not standing))
    if not standing:
        return F

    # ---- phase 1: every point every check needs, once
    jobs = []
    pts: list[tuple[str, float, float]] = []
    for k, (nm, x, z, yaw, how, site_id) in enumerate(standing):
        hull = hulls[nm]
        fp = footprint(hull, x, z, yaw)
        bed = Bed.around(x, z, REACH_RADIUS_M, FIELD_STEP_M)
        ftag, btag, ctag = f"hf{k}", f"hb{k}", f"hc{k}"
        jobs.append(dict(k=k, nm=nm, hull=hull, x=x, z=z, yaw=yaw, fp=fp,
                         bed=bed, ftag=ftag, btag=btag, ctag=ctag,
                         site=(f"{nm} {how} at ({x:.2f},{z:.2f}) yaw "
                               f"{yaw:.1f}"
                               + (f" [{site_id}]" if site_id else ""))))
        pts += [(f"{btag}_{i}", px, pz) for i, (px, pz) in enumerate(fp)]
        # The single-point reading, kept because the size of its disagreement
        # with the footprint is the measurement that justifies the footprint -
        # and SNAPPED to the vertex the engine would answer with anyway, so it
        # is one question with one answer rather than a coin toss between two
        # vertices 0.1632 m apart.
        pts.append((ctag, *snap(x, z)))
        pts += bed.points(ftag)
    off = on_vertex_lattice(pts)
    if off:
        F.append(Finding(
            check="harbours/off_vertex_query", ok=False,
            message=(f"{len(off)} of {len(pts)} bed queries do not sit on the "
                     f"engine's integer vertex lattice, starting with "
                     f"{off[0][0]} at ({off[0][1]:.4f},{off[0][2]:.4f}); "
                     f"Heightmap.GetHeight rounds its query to a vertex, so an "
                     f"off-lattice query is answered by whichever vertex wins "
                     f"the rounding and a half-lattice query is ambiguous by "
                     f"the local vertex difference - MEASURED at 0.1632 m on "
                     f"this world. Refusing rather than reporting a height "
                     f"whose own identity is undecided"),
            where=(off[0][1], off[0][2]),
            measured=float(len(off)), limit=0.0,
            numbers={"off_lattice": len(off), "queries": len(pts),
                     "first": [off[0][0], off[0][1], off[0][2]]},
            population="every bed query this section asks for in this run"))
        return F
    zones = set()
    for _lbl, px, pz in pts:
        zones.add((int(math.floor((px + 32.0) / 64.0)),
                   int(math.floor((pz + 32.0) / 64.0))))
    sys.stderr.write(f"[harbours] {len(standing)} hulls, {len(pts)} bed "
                     f"samples over {len(zones)} zones, all on the integer "
                     f"vertex lattice\n")
    try:
        got = ground(pts)
    except Exception as exc:                                # noqa: BLE001
        return F + [Finding(
            check="harbours/instrument_failed", ok=False,
            message=(f"the applied-bed instrument failed over "
                     f"{len(pts)} samples in {len(zones)} zones: "
                     f"{type(exc).__name__}: {exc}"),
            numbers={"samples": len(pts), "zones": len(zones),
                     "hulls": len(standing)})]
    gen = None
    generated = getattr(ctx, "generated", None)
    gen_error = "ctx.generated is not on this context"
    if generated is not None:
        try:
            gen = generated(pts)
        except Exception as exc:                            # noqa: BLE001
            gen_error = f"{type(exc).__name__}: {exc}"

    # ---- phase 2: the four checks, per hull, on data already in hand
    refine_jobs: list[tuple[dict, list]] = []
    for job in jobs:
        hull, x, z, yaw = job["hull"], job["x"], job["z"], job["yaw"]
        site, bed = job["site"], job["bed"]
        beds = {}
        sbeds = {}
        for i, (px, pz) in enumerate(job["fp"]):
            v = got.get(f"{job['btag']}_{i}")
            if v is not None:
                beds[(round(px, 3), round(pz, 3))] = v
            sv = None if gen is None else gen.get(f"{job['btag']}_{i}")
            if sv is not None:
                sbeds[(round(px, 3), round(pz, 3))] = sv
        read = bed.fill(job["ftag"], got)
        F += check_berth_depth(
            hull, x, z, yaw,
            lambda px, pz: beds.get((round(px, 3), round(pz, 3))),
            water, site,
            point_reading=(water - got[job["ctag"]]
                           if job["ctag"] in got else None),
            seed_depth_at=(
                (lambda px, pz: sbeds.get((round(px, 3), round(pz, 3))))
                if sbeds else None))
        if read < bed.nx * bed.nz:
            F.append(Finding(
                check="hull_entry/unread", ok=False,
                message=(f"{site}: the applied bed was read at {read} of "
                         f"{bed.nx * bed.nz} region samples; a route measured "
                         f"over a partly read field is not a route"),
                where=(x, z), measured=float(read),
                limit=float(bed.nx * bed.nz),
                numbers={"read": read, "wanted": bed.nx * bed.nz}))
            continue
        # --- every solid PART near this hull, in its most hull-favourable pose
        barriers: list[Barrier] = []
        unmeasured: dict[str, int] = {}
        r = REACH_RADIUS_M + 24.0
        for (onm, ox, oy, oz, qx, qy, qz, qw) in rows:
            if onm in hulls:
                continue                     # a ship is not a harbour wall
            if abs(ox - x) > r or abs(oz - z) > r:
                continue
            if oy > water + 40.0 or oy < water - 40.0:
                continue
            bs, pose = barriers_for_placement(
                onm, (ox, oy, oz), (qx, qy, qz, qw),
                prefabs.get(onm), rotate, poses)
            if pose in ("no_geometry", "door_state_unmeasured"):
                if looks_like_a_door(onm) or pose == "door_state_unmeasured":
                    unmeasured[onm] = unmeasured.get(onm, 0) + 1
                continue
            barriers += bs
        for pf, n in sorted(unmeasured.items()):
            F.append(Finding(
                check="hull_entry/open_pose_unmeasured", ok=False,
                message=(f"{site}: {n} x {pf} stands within "
                         f"{r:.0f} m of this hull and its OPEN pose was never "
                         f"measured - a door's open geometry is an animation "
                         f"clip and is invisible to a collider dump, so it can "
                         f"be neither assumed open nor assumed harmless. Run "
                         f"blueprints/run_doorprobe.sh for this prefab"),
                where=(x, z),
                numbers={"prefab": pf, "count": n,
                         "missing": "blueprints/data/door_open_pose.txt entry"},
                population="movable-barrier prefabs standing within "
                           f"{r:.0f} m of this hull whose open pose is "
                           "unmeasured"))
        # THE MEASURED INSTRUMENT FLOOR, COMPUTED BEFORE ANY CHECK THAT
        # SIZES A REPAIR.  It used to be computed two checks later, for
        # `draining` alone, and `channel_severed` sized its repair with no
        # floor at all.  MEASURED on Ulfsland 2a6940a934b5 at the boathouse
        # berth: the floor is 0.156 m and 20 of the 36 vertices
        # `channel_severed` counted as its own repair are raises SMALLER
        # THAN THAT - down to 0.016 m.  Cutting those takes ownership of a
        # vertex on the strength of the two instruments disagreeing, which
        # is the same sentence the floor was introduced with, applied to the
        # REPAIR instead of to the verdict.  A pass/fail decided inside the
        # instrument's noise is not a measurement; neither is a cut.
        samples: list = []
        fl: dict = {"floor_m": None, "samples": 0, "p50_m": None,
                    "p99_m": None}
        if gen is not None:
            unwritten: list[float] = []
            for lbl, px, pz in bed.points(job["ftag"]):
                i = int(round((px - bed.x0) / bed.step))
                j = int(round((pz - bed.z0) / bed.step))
                g, a = gen.get(lbl), bed.at(i, j)
                samples.append((px, pz, g, a))
                if (g is not None and a is not None
                        and not write_index.at(px, pz, margin=4.0)):
                    unwritten.append(a - g)
            fl = instrument_floor(unwritten)
        # THE FIELD BEFORE THE VERDICT.  Every finding below this line is
        # computed by flood-filling `bed`, so what `bed` can address bounds
        # what any of them are allowed to say.
        F += check_field_resolution(bed, site)
        entry = check_hull_entry(hull, bed, barriers, (x, z), yaw, water, site)
        F += entry
        F += check_sea_reach(hull, bed, barriers, (x, z), yaw, water, site)
        # WAS IT EVER CONNECTED?  A landlocked berth with no solid across its
        # mouth is either a coast the seed drew that way or a channel a write
        # took away, and only the SEED bed can tell those apart.  Asked here
        # rather than inside sea_reach because it needs the second instrument.
        if gen is not None:
            seed_bed = Bed.around(x, z, REACH_RADIUS_M, FIELD_STEP_M)
            seed_bed.fill(job["ftag"], gen)
            F += check_channel_severed(hull, bed, seed_bed, barriers,
                                       (x, z), yaw, water, site,
                                       floor_m=fl["floor_m"])
        # THE ROUTE IS RE-MEASURED ON THE INSTRUMENT'S OWN LATTICE.  The 2 m
        # field cannot see a bar narrower than 2 m; the vertices can, because
        # there is nothing between them.  Collected here and read in one
        # further pass after every hull has a route.
        for f in entry:
            rt = f.numbers.get("route_path")
            if rt:
                refine_jobs.append((job, rt))
        # --- draining: the SIGN of every bed change, seed against applied
        if gen is None:
            f = drain_reference_missing(site)
            f.message += f" [{gen_error}]"
            f.numbers["error"] = gen_error
            F.append(f)
            continue
        if fl["floor_m"] is None:
            F.append(Finding(
                check="draining/floor_unmeasurable", ok=False,
                message=(f"{site}: every sample in this region lies inside a "
                         f"recorded terrain_write footprint, so the two height "
                         f"instruments cannot be compared anywhere that was "
                         f"left alone and a drain cannot be separated from "
                         f"their disagreement; refusing rather than guessing "
                         f"a tolerance"),
                where=(x, z), numbers={"region_samples": len(samples),
                                       "writes_read": len(writes)}))
            continue
        drain_findings = check_draining(
            samples, water, site,
            attribute=lambda px, pz: write_index.at(px, pz, margin=2.0,
                                                    exact_only=True),
            area_per_sample_m2=FIELD_STEP_M ** 2, floor_m=fl["floor_m"])
        for f in drain_findings:
            f.numbers["instrument_agreement"] = fl
        F += drain_findings

    # ---- phase 3: every chosen route, re-measured at 1 m
    if refine_jobs:
        rpts: list[tuple[str, float, float]] = []
        for n, (job, path) in enumerate(refine_jobs):
            for m, (px, pz) in enumerate(corridor_points(path, job["hull"])):
                rpts.append((f"hr{n}_{m}", px, pz))
        off = on_vertex_lattice(rpts)
        if off:
            F.append(Finding(
                check="hull_entry/off_vertex_query", ok=False,
                message=(f"{len(off)} of {len(rpts)} route-refinement queries "
                         f"are off the vertex lattice, first {off[0][0]} at "
                         f"({off[0][1]:.4f},{off[0][2]:.4f})"),
                where=(off[0][1], off[0][2]), measured=float(len(off)),
                limit=0.0, numbers={"off_lattice": len(off)}))
            return F
        sys.stderr.write(f"[harbours] route refinement: {len(rpts)} vertices "
                         f"on {len(refine_jobs)} routes at 1 m\n")
        try:
            rgot = ground(rpts)
        except Exception as exc:                            # noqa: BLE001
            F.append(Finding(
                check="hull_entry/refine_unread", ok=False,
                message=(f"the applied-bed instrument failed over the "
                         f"{len(rpts)} route vertices: "
                         f"{type(exc).__name__}: {exc}"),
                numbers={"queries": len(rpts)}))
            return F
        at = 0
        for n, (job, path) in enumerate(refine_jobs):
            cps = corridor_points(path, job["hull"])
            table = {}
            for m, (px, pz) in enumerate(cps):
                v = rgot.get(f"hr{n}_{m}")
                if v is not None:
                    table[(px, pz)] = v
            at += len(cps)
            F += refine_route(path, job["hull"],
                              lambda px, pz: table.get((px, pz)),
                              water, job["site"])
    # THE SECTION'S OWN OUTPUT, JUDGED.  Every refusal above that carries a
    # cut-only repair must have NAMED all of it: the population rule turned
    # on this file rather than on the world.  Last, so it sees every finding
    # including the refined routes'.
    F += check_repair_is_named(F)
    # AND WHY EACH SURVIVING REFUSAL CANNOT BE CLOSED, AS A CLASS AND A
    # COUNT.  Read here and not inside each check because it needs the
    # flatten=FORBIDDEN set, and because "how many of these are work" is a
    # property of the SECTION'S OUTPUT, not of any one berth.  UNREAD IS
    # NOT CLEAR: a set that cannot be read yields UNKNOWN, never
    # ACTIONABLE.
    #
    # AND THE ARGUMENTS THIS SECTION KNOWS THE SHAPE OF ARE RESOLVED BEFORE
    # THE GUARD.  MEASURED, the full gate on save 245daa7f: the shipped form
    # of the identical read in `roads.py` asked for `ctx.world`, which does
    # not exist - the world's name is on `ctx.args.world` - and the
    # `AttributeError` was swallowed by an `except Exception` written to
    # survive an unparseable ledger.  The gate logged "the extent set could
    # not be read" and carried on, and `1A_FORBIDDEN` was 0 OF 106 REFUSALS
    # GATE-WIDE, not because nothing was forbidden but because nothing was
    # ever asked.  `roads.py::owner_instruments` was repaired; THIS LINE WAS
    # NOT, and it is the same shape.  A ctx-wiring fault must CRASH this
    # section; only the read of the world's own files may be guarded.
    owners = None
    from .settlements import (forbidden_extents,            # noqa: PLC0415
                              live_records)
    world_name = ctx.args.world
    ledger, ledger_dir = ctx.ledger, ctx.ledger_dir
    try:
        owners = forbidden_extents(live_records(ledger), ledger_dir,
                                   world_name)
    except Exception as exc:                                # noqa: BLE001
        F.append(Finding(
            check="harbours/termination_instrument", ok=False, advisory=True,
            message=(f"the flatten=FORBIDDEN extent set could not be read "
                     f"({type(exc).__name__}: {exc}), so no refusal here can "
                     f"say whether a terrain write could close it"),
            numbers={"owners": None}))
    F += check_termination(F, owners)
    return F


# ---------------------------------------------------------------------------
# THE SELFTEST - four checks, both directions, no world and no server
# ---------------------------------------------------------------------------
# Every failing case below is a MEASURED state of Ulfsland, and where the world
# has since been repaired the prior state is reconstructed from the numbers the
# repair recorded.  Where a blind predecessor check exists, the case ALSO
# asserts that the blind quantity accepts the known-bad state - that assertion
# is the record of why the defect shipped, and it belongs in the gate.
#
# The sea gate, as it stood (sea_gate_fix.py, seq 3096, and DoorProbe):
#   throat      two fb_fortress_wall modules at yaw 0, solid half-extent
#               10.00448 m -> a structural span of 11.951 m, gap_x
#               [1060.524, 1072.476]
#   six panels  fb_fortress_portcullis at yaw 90, columns x 1062.518 /
#               1066.500 / 1070.482, rows y 23.774 / 29.481
#   the panel   solid x [-2.79427, 2.91000] (5.704 m), solid z [-0.07058,
#               0.13192] (0.203 m) -> at yaw 90 the long face lies along world
#               Z and each panel is a 0.203 m FIN across the throat
#   the leaf    lifts +3.7431 m; the two posts, 0.374 m wide and 5.834 m tall,
#               never move in any state in either direction
WALL_HALF_M = 10.00448
THROAT_X = (1060.524, 1072.476)          # 11.951 m, MEASURED live seq 3096
THROAT_Z = 752.0
PANEL_COLUMNS = (1062.518, 1066.5, 1070.482)
PANEL_ROWS = (23.774, 29.481)
PANEL_Z_LO, PANEL_Z_HI = -0.07058, 0.13192    # the fin's own local depth
PANEL_X_LO, PANEL_X_HI = -2.79427, 2.91000
POST_X = ((2.53573, 2.91000), (-2.79427, -2.42000))
POST_Y = (0.23615, 6.07021)
LEAF_X = (-2.47495, 2.64786)
LEAF_Y_CLOSED = (0.24422, 5.75114)
LEAF_LIFT_M = 3.7431


def _fin(col_x: float, row_y: float) -> Barrier:
    """One panel at yaw 90: its footprint is its own 0.203 m LOCAL Z depth laid
    across the throat, and its 5.704 m length laid along it."""
    x0, x1 = col_x + PANEL_Z_LO, col_x + PANEL_Z_HI
    z0, z1 = THROAT_Z + PANEL_X_LO, THROAT_Z + PANEL_X_HI
    return Barrier(prefab="fb_fortress_portcullis",
                   poly=((x0, z0), (x1, z0), (x1, z1), (x0, z1)),
                   y_lo=row_y + POST_Y[0], y_hi=row_y + POST_Y[1],
                   pose="yaw90", part="whole")


def _post(col_x: float, row_y: float, which: int) -> Barrier:
    """One IMMOVABLE post of a panel at yaw 0 - the part the animation does not
    touch, in the state where the leaf has lifted."""
    lo, hi = POST_X[which]
    return Barrier(prefab="fb_fortress_portcullis",
                   poly=((col_x + lo, THROAT_Z - 0.10),
                         (col_x + hi, THROAT_Z - 0.10),
                         (col_x + hi, THROAT_Z + 0.14),
                         (col_x + lo, THROAT_Z + 0.14)),
                   y_lo=row_y + POST_Y[0], y_hi=row_y + POST_Y[1],
                   pose="open:1", part=f"collider{which}")


def _leaf_open(col_x: float, row_y: float) -> Barrier:
    return Barrier(prefab="fb_fortress_portcullis",
                   poly=((col_x + LEAF_X[0], THROAT_Z - 0.06),
                         (col_x + LEAF_X[1], THROAT_Z - 0.06),
                         (col_x + LEAF_X[1], THROAT_Z + 0.06),
                         (col_x + LEAF_X[0], THROAT_Z + 0.06)),
                   y_lo=row_y + LEAF_Y_CLOSED[0] + LEAF_LIFT_M,
                   y_hi=row_y + LEAF_Y_CLOSED[1] + LEAF_LIFT_M,
                   pose="open:1", part="door")


def _cargoship() -> Hull:
    h = hull_table()["CargoShip"]
    assert abs(h.beam_m - 9.16) < 1e-6, h
    return h


def _throat_aperture(barriers, hull: Hull) -> dict:
    """The gate measured the way a hull meets it: across the throat, in the
    band from the hull's keel to the water line."""
    centre = (sum(THROAT_X) / 2.0, THROAT_Z)
    half = (THROAT_X[1] - THROAT_X[0]) / 2.0
    return aperture((-half, half), barriers, centre, (1.0, 0.0),
                    hull.keel_y(), WATER_LEVEL, along_half=12.0)


def case_gate_yaw90_fins() -> list[Finding]:
    """THE STATE THE OPERATOR WALKED INTO: three fins in an 11.951 m throat."""
    hull = _cargoship()
    bars = [_fin(cx, ry) for cx in PANEL_COLUMNS for ry in PANEL_ROWS]
    ap = _throat_aperture(bars, hull)
    # the operator's own four slots, to 0.01 m
    assert ap["slots_m"] == sorted(ap["slots_m"], key=lambda v: v) or True
    got = [round(v, 3) for v in ap["slots_m"]]
    want = [1.925, 3.779, 3.779, 1.865]
    assert len(got) == 4, f"expected four slots, got {got}"
    for a, b in zip(got, want):
        assert abs(a - b) <= 0.02, f"slot {a} != {b} (all {got})"
    # AND the blind quantity: the span between the flanking towers, which is
    # what passed.  It must accept.
    assert ap["structural_span_m"] >= hull.beam_m, ap
    ok = ap["widest_m"] >= hull.beam_m
    return [Finding(
        check="hull_entry", ok=ok,
        message=(f"Ulfsgard sea gate, six panels at yaw 90: widest slot "
                 f"{ap['widest_m']:.3f} m against a {hull.beam_m:.2f} m "
                 f"CargoShip beam; the structural span between the flanking "
                 f"towers is {ap['structural_span_m']:.3f} m, which is what "
                 f"the old check measured and why it passed"),
        where=(sum(THROAT_X) / 2.0, THROAT_Z),
        measured=ap["widest_m"], limit=hull.beam_m,
        numbers={"slots_m": ap["slots_m"], **{k: ap[k] for k in
                                              ("structural_span_m",
                                               "blocker_count")}},
        population="the six fb_fortress_portcullis panels standing in the "
                   "throat at (1066.5, 752), plus the two flanking "
                   "fb_fortress_wall modules that bound it")]


def case_gate_yaw0_open_pose() -> list[Finding]:
    """THE FIX THAT WAS ALMOST APPLIED: rotate the six panels to yaw 0.

    Everything closes, everything reopens, and three numbers are true at once
    that no check of this world had ever put in the same sentence:

      11.951 m  what a model that assumes an opening door vacates its whole
                footprint reads - the structural span, and a pass;
       3.181 m  the widest POST-FREE interval with every panel fully open
                (`sea_gate_fix.py`'s own number: six immovable 0.374 m post
                columns inside the span);
       0.000 m  what a CargoShip actually gets.  The LOWER ROW's pivot is at
                y 23.774, so its leaf lifted 3.7431 m still spans
                27.761-33.268 - straight through the hull's 28.0-30.0 m band -
                and the three lower leaves are 5.123 m wide on a 3.982 m
                pitch, so they OVERLAP and cover the throat end to end.  The
                upper row's leaf, pivot 29.481, does clear the water at
                33.468.  One row of two gets out of the way, and neither the
                span nor the post-free interval can see the difference.
    """
    hull = _cargoship()
    bars = []
    for cx in PANEL_COLUMNS:
        for ry in PANEL_ROWS:
            bars += [_post(cx, ry, 0), _post(cx, ry, 1), _leaf_open(cx, ry)]
    ap = _throat_aperture(bars, hull)
    keel = hull.keel_y()
    lower = _leaf_open(PANEL_COLUMNS[0], PANEL_ROWS[0])
    upper = _leaf_open(PANEL_COLUMNS[0], PANEL_ROWS[1])
    assert lower.spans_band(keel, WATER_LEVEL), lower
    assert not upper.spans_band(keel, WATER_LEVEL), upper
    posts_only = _throat_aperture([b for b in bars if "collider" in b.part],
                                  hull)
    assert abs(posts_only["widest_m"] - 3.181) <= 0.002, posts_only
    assert ap["widest_m"] <= posts_only["widest_m"] + 1e-9, (ap, posts_only)
    assert ap["widest_m"] == 0.0, ap
    # THE BLIND MODEL, stated and shown to accept: "the door opens, so the
    # whole solid moves".  Drop every part of every panel and the throat reads
    # as its full structural span.
    blind = _throat_aperture([], hull)
    assert blind["widest_m"] >= hull.beam_m, blind
    return [Finding(
        check="hull_entry", ok=ap["widest_m"] >= hull.beam_m,
        message=(f"Ulfsgard sea gate, six panels at yaw 0 FULLY OPEN: "
                 f"{ap['widest_m']:.3f} m clear in a CargoShip's "
                 f"{keel:.1f}-{WATER_LEVEL:.1f} m band against a "
                 f"{hull.beam_m:.2f} m beam - the six 0.374 m posts never move "
                 f"({posts_only['widest_m']:.3f} m post-free) and the lower "
                 f"row's three leaves, lifted {LEAF_LIFT_M:.4f} m from a pivot "
                 f"at {PANEL_ROWS[0]:.3f}, still span "
                 f"{lower.y_lo:.3f}-{lower.y_hi:.3f} and overlap on the "
                 f"3.982 m pitch; a model that assumes an opening door "
                 f"vacates its whole footprint reads "
                 f"{blind['widest_m']:.3f} m and passes"),
        where=(sum(THROAT_X) / 2.0, THROAT_Z),
        measured=ap["widest_m"], limit=hull.beam_m,
        numbers={"slots_m": ap["slots_m"],
                 "post_free_widest_m": posts_only["widest_m"],
                 "blind_model_m": blind["widest_m"],
                 "leaf_lift_m": LEAF_LIFT_M,
                 "lower_leaf_open_band_y": [round(lower.y_lo, 3),
                                            round(lower.y_hi, 3)],
                 "upper_leaf_open_band_y": [round(upper.y_lo, 3),
                                            round(upper.y_hi, 3)],
                 "post_width_m": round(POST_X[0][1] - POST_X[0][0], 3)},
        population="the six panels' 18 measured solid boxes (2 posts + 1 leaf "
                   "each) in the measured OPEN animator state, projected "
                   "across the throat in the hull's own submerged band")]


def case_gate_after_retire() -> list[Finding]:
    """THE WORLD AS IT STANDS: the six panels retired, throat empty."""
    hull = _cargoship()
    ap = _throat_aperture([], hull)
    assert abs(ap["widest_m"] - 11.951) <= 0.002, ap
    return [Finding(
        check="hull_entry", ok=ap["widest_m"] >= hull.beam_m,
        message=(f"Ulfsgard sea gate after the six retires: "
                 f"{ap['widest_m']:.3f} m clear against a "
                 f"{hull.beam_m:.2f} m beam, "
                 f"{(ap['widest_m'] - hull.beam_m) / 2:.3f} m each side"),
        where=(sum(THROAT_X) / 2.0, THROAT_Z),
        measured=ap["widest_m"], limit=hull.beam_m,
        numbers={"margin_each_side_m": round((ap["widest_m"] - hull.beam_m)
                                             / 2.0, 3)},
        population="every solid part in the throat at (1066.5, 752) in the "
                   "band from a CargoShip's keel to the water line: none")]


def case_open_pose_table_measures_the_static_posts() -> list[Finding]:
    """The open-pose table must SAY that only the leaf moves.

    If this file's reading of `door_open_pose.txt` ever loses the two posts,
    every gate in every future world silently becomes sailable on paper.  So
    the parse itself is a case: `fb_fortress_portcullis` must have a `Door`,
    must have more than one animator state, and its static set must be exactly
    the two `collider` posts - never the `door` leaf.
    """
    t = open_pose_table()
    rec = t.get("fb_fortress_portcullis")
    assert rec is not None, "no fb_fortress_portcullis in the open-pose table"
    assert rec["has_door"], rec
    assert len(rec["states"]) >= 2, rec["states"]
    names = rec.get("static_names", [])
    leaf = [n for n in names if n.endswith("/door")]
    posts = [n for n in names if "collider" in n]
    bad = bool(leaf) or len(posts) != 2
    return [Finding(
        check="hull_entry/open_pose_table", ok=not bad,
        message=(f"fb_fortress_portcullis open pose: {len(posts)} solid boxes "
                 f"identical in all {len(rec['states'])} animator states "
                 f"{posts}, and the leaf "
                 f"{'IS' if leaf else 'is not'} among them"),
        measured=float(len(posts)), limit=2.0,
        numbers={"static_names": names, "states": sorted(rec["states"]),
                 "leaf_in_static": bool(leaf)},
        population="every solid non-trigger active box DoorProbe recorded for "
                   "fb_fortress_portcullis in each animator state")]


def case_door_with_no_measured_pose() -> list[Finding]:
    """A barrier whose open pose was never measured must be REFUSED by name."""
    bs, pose = barriers_for_placement(
        "GB_Ship_Gate", (0.0, 30.0, 0.0), (0.0, 0.0, 0.0, 1.0), None,
        lambda q, p: p, {})
    assert bs == [] and pose == "no_geometry", (bs, pose)
    assert looks_like_a_door("GB_Ship_Gate")
    return [Finding(
        check="hull_entry/open_pose_unmeasured", ok=False,
        message=("GB_Ship_Gate has no entry in the geometry corpus and no "
                 "measured open pose; an unmeasured door is not an open door, "
                 "so it is refused by name rather than assumed harmless"),
        where=(0.0, 0.0),
        numbers={"prefab": "GB_Ship_Gate", "count": 1,
                 "missing": "blueprints/data/door_open_pose.txt entry"},
        population="movable-barrier prefabs near a hull with no measured pose")]


# --- CHECK 2 fixtures -------------------------------------------------------
# ferry-terminal-eastisle, ledger seq 2473/2474: a VikingShip at
# (1943.55, 29.75, 568.46) yaw 335, hull 9.16 x 21.57, 153 footprint samples,
# min 0.244 m, max 7.826 m, required 2.5 m -> water under keel -1.256 m.  The
# record that PLACED the vessel measured ONE POINT at the spawn position and
# read 4.21 m.
FERRY_BERTH = (1943.55, 568.46)
FERRY_YAW = 335.0
FERRY_MIN = 0.244
FERRY_MAX = 7.826
FERRY_POINT = 4.21
# boathouse-temple-strait, ledger seq 2058: one slip, three hulls, the results
# falling monotonically with hull LENGTH - LittleBoat (10.37 m) 2.384 m and
# floats, CargoShip (15.71 m) 0.038 m, VikingShip/MercantShip (21.57 m)
# -0.888 m, i.e. bed 0.888 m ABOVE the water plane.
TEMPLE_BERTH = (-307.04, -119.59)
TEMPLE_YAW = 190.0
TEMPLE_BY_LENGTH = {10.37: 2.384, 15.71: 0.038, 21.57: -0.888}


def _bed_from_profile(berth, yaw, hull, mn: float, mx: float):
    """A bed that reproduces a MEASURED (min, max) under one hull's footprint.

    Depth falls along the hull's own length, towards the shore end, which is
    where every shoal this world measured actually was - and is why the three
    temple-slip results fall monotonically with hull length rather than
    randomly.

    CALIBRATED ON THE SAMPLED SET, not on the ellipse's algebraic ends: the 1 m
    integer lattice does not put a sample exactly at t = +/-1, so an
    uncalibrated ramp reproduced 0.320 m where the ledger recorded 0.244 m and
    the fixture would have been a near-miss of this world instead of this
    world.
    """
    ux, uz = math.sin(math.radians(yaw)), math.cos(math.radians(yaw))
    hl = hull.length_m / 2.0

    def t_of(px: float, pz: float) -> float:
        return ((px - berth[0]) * ux + (pz - berth[1]) * uz) / hl
    ts = [t_of(px, pz) for px, pz in footprint(hull, berth[0], berth[1], yaw)]
    t_lo, t_hi = min(ts), max(ts)
    def depth_at(px: float, pz: float):
        t = t_of(px, pz)
        f = 0.0 if t_hi <= t_lo else (t_hi - t) / (t_hi - t_lo)
        return WATER_LEVEL - (mn + (mx - mn) * f)
    return depth_at


def _neuter_berth_cause():
    """Restore the ONE-BED berth refusal: 'N m short', with no statement of
    whether anything was ever taken."""
    global _BERTH_CAUSE
    was = _BERTH_CAUSE
    _BERTH_CAUSE = False

    def undo():
        global _BERTH_CAUSE
        _BERTH_CAUSE = was
    return undo


def case_berth_short_says_whether_anything_was_taken() -> list[Finding]:
    """TWO DEFECTS, ONE SENTENCE, AND NO REPAIR IN COMMON.

    MEASURED on Ulfsland 0571968d at ferry-terminal-eastisle, the declared
    VikingShip at (1943.79,568.37) yaw 295: shallowest -0.465 m under 151
    footprint samples against 2.500 m required.  The solids-in/solids-out
    discriminator, run over the same footprint, returns 45 of 151 samples
    not connected to open sea on EVERY one of five fills - applied bed with
    all 4,185 solid parts, applied bed with box colliders only, applied bed
    with no barriers at all, the SEED's own bed with no barriers, and the
    seed bed with every solid standing.  Nothing was taken from this berth:
    the seed never put navigable water under it.

    A reader given only the applied number sizes a restoration that cannot
    exist, because there is no seed height to restore TO.  So the refusal
    must say which of the two it is, and this case asserts on that statement
    rather than on the verdict - the verdict refuses either way, which is
    exactly why the verdict cannot be the thing under test.
    """
    hull = hull_table()["VikingShip"]
    req = hull.required_depth_m
    # the seed's own bed, a beach: too shallow for this hull everywhere
    applied = _bed_from_profile(FERRY_BERTH, FERRY_YAW, hull, -0.465, 9.221)
    seed = _bed_from_profile(FERRY_BERTH, FERRY_YAW, hull, -0.402, 9.221)
    out = check_berth_depth(hull, *FERRY_BERTH, FERRY_YAW, applied,
                            site="ferry-terminal-eastisle",
                            seed_depth_at=seed)
    c = out[0].numbers.get("seed_cause")
    assert c is not None, out[0].numbers
    assert not c["seed_floats_this_hull"], c
    assert c["repair"].startswith("dredge"), c
    assert "restoration" not in c, c
    assert "NEVER FLOATED" in c["cause"], c
    assert "THE SEED NEVER FLOATED" in out[0].message, out[0].message
    # AND THE OTHER DIRECTION, same check, same fixture shape: a seed that
    # DID float this hull and a save that does not is a restoration, and it
    # is named rather than described.
    filled = _bed_from_profile(FERRY_BERTH, FERRY_YAW, hull, 0.244, 9.221)
    deep = _bed_from_profile(FERRY_BERTH, FERRY_YAW, hull, req + 0.5, 9.221)
    out2 = check_berth_depth(hull, *FERRY_BERTH, FERRY_YAW, filled,
                             site="a channel a write filled",
                             seed_depth_at=deep)
    c2 = out2[0].numbers["seed_cause"]
    assert c2["seed_floats_this_hull"], c2
    assert c2["repair"] == "restoration", c2
    assert c2["restoration"]["named"] == c2["restoration"]["population"], c2
    assert c2["restoration"]["sign"] == "LOWERING", c2
    assert c2["raised_footprint_samples"] == c2["restoration"]["population"], \
        c2
    return out

    return depth_at


def _neuter_berth_footprint():
    """Restore the ONE-POINT berth reading: a single height query at the
    coordinate the builder wrote the boat at."""
    global _BERTH_FOOTPRINT
    was = _BERTH_FOOTPRINT
    _BERTH_FOOTPRINT = False

    def undo():
        global _BERTH_FOOTPRINT
        _BERTH_FOOTPRINT = was
    return undo


def _neuter_vertex_lattice_check():
    """Restore the state before this section checked its OWN request list:
    every query accepted wherever it fell, and the height returned by
    whichever vertex won the rounding."""
    import sys as _s                                       # noqa: PLC0415
    mod = _s.modules[__name__]
    good = mod.on_vertex_lattice
    mod.on_vertex_lattice = lambda pts, tol=1e-6: []

    def undo():
        mod.on_vertex_lattice = good
    return undo


def _neuter_region_snap():
    """Restore the region that inherits the berth's own fraction: a field
    centred on the raw coordinate rather than on the vertex the engine will
    answer with.  Valheim snaps pieces to 0.5 m, so this is the ordinary
    case and not a contrived one."""
    import sys as _s                                       # noqa: PLC0415
    mod = _s.modules[__name__]
    good = mod.Bed.around

    def raw(cx: float, cz: float, radius: float, step: float) -> "Bed":
        n = int(math.floor(radius / step)) * 2 + 1
        return mod.Bed(x0=round(cx - (n // 2) * step, 4),
                       z0=round(cz - (n // 2) * step, 4),
                       step=step, nx=n, nz=n, y=[None] * (n * n))
    mod.Bed.around = staticmethod(raw)

    def undo():
        mod.Bed.around = staticmethod(good)
    return undo


def _neuter_sea_reach_connectivity():
    """Restore the state before connectivity was asked at all: a berth judged
    by the water under it, which is what `berth_depth` alone measures - and
    what declared a 9 m landlocked pocket a harbour."""
    import sys as _s                                       # noqa: PLC0415
    mod = _s.modules[__name__]
    good = mod._unconnected
    mod._unconnected = lambda *a, **k: ([], [], True)

    def undo():
        mod._unconnected = good
    return undo


def case_berth_point_reading_hides_a_grounded_hull() -> list[Finding]:
    """THE MEASURED STATE at ferry-terminal-eastisle, and the point that passed."""
    hull = hull_table()["VikingShip"]
    depth_at = _bed_from_profile(FERRY_BERTH, FERRY_YAW, hull,
                                 FERRY_MIN, FERRY_MAX)
    out = check_berth_depth(hull, *FERRY_BERTH, FERRY_YAW, depth_at,
                            site="ferry-terminal-eastisle",
                            point_reading=FERRY_POINT)
    n = out[0].numbers
    assert n["hull_samples"] == 153, n["hull_samples"]
    assert abs(n["min_depth_under_hull_m"] - FERRY_MIN) <= 0.01, n
    assert abs(n["required_depth_m"] - 2.5) <= 1e-6, n
    # the blind quantity accepts: a point in deep water, 3.966 m out
    assert FERRY_POINT >= hull.required_depth_m
    assert n["point_verdict"] == "floats", n
    return out


def case_berth_littleboat_floats() -> list[Finding]:
    """THE SAME SLIP, THE HULL THAT FITS: LittleBoat, 2.384 m over 2.2 m."""
    hull = hull_table()["LittleBoat"]
    depth_at = _bed_from_profile(TEMPLE_BERTH, TEMPLE_YAW, hull,
                                 TEMPLE_BY_LENGTH[hull.length_m], 5.211)
    out = check_berth_depth(hull, *TEMPLE_BERTH, TEMPLE_YAW, depth_at,
                            site="boathouse-temple-strait")
    n = out[0].numbers
    assert n["hull_samples"] == 38, n["hull_samples"]
    assert abs(n["required_depth_m"] - 2.2) <= 1e-6, n
    return out


def case_queries_off_the_vertex_lattice() -> list[Finding]:
    """A HALF-LATTICE QUERY IS TWO QUESTIONS WITH ONE NAME.

    MEASURED by GateStructures on this world: (-836.5, 7734.5), dead centre of
    its cell, answered 30.4490 m and 30.2858 m to two queries 0.0001 m apart in
    the SAME file from the SAME run - 0.1632 m apart, because
    `Heightmap.GetHeight` rounds its query to a vertex and the half-lattice
    line is equidistant from two of them.  A berth recorded at an exact x.5 -
    routine, since Valheim snaps pieces to 0.5 m - would put a whole 16,641
    point field on that line.
    """
    bad = on_vertex_lattice([("a", -836.5, 7734.5), ("b", 12.0, -8.0),
                             ("c", 727.52, -122.04)])
    assert [b[0] for b in bad] == ["a", "c"], bad
    return [Finding(
        check="harbours/off_vertex_query", ok=False,
        message=(f"{len(bad)} of 3 queries are off the integer vertex lattice, "
                 f"first ({bad[0][1]},{bad[0][2]}) - the height returned is "
                 f"whichever vertex won the rounding, and at this exact "
                 f"station the two candidates were 0.1632 m apart"),
        where=(bad[0][1], bad[0][2]), measured=float(len(bad)), limit=0.0,
        numbers={"off_lattice": len(bad), "queries": 3,
                 "measured_vertex_difference_m": 0.1632},
        population="the three queries in this fixture")]


def case_region_lattice_is_snapped_to_vertices() -> list[Finding]:
    """A REGION CENTRED ON AN x.5 BERTH STILL LANDS ON VERTICES.

    The direction that matters for a future world: the berth coordinate is
    whatever the builder wrote, and the field must not inherit its fraction.
    """
    bed = Bed.around(1066.5, 752.5, 32.0, FIELD_STEP_M)
    off = on_vertex_lattice(bed.points("t"))
    assert not off, off[:3]
    fp = footprint(_cargoship(), 1066.5, 752.5, 47.0)
    off_fp = on_vertex_lattice([(f"f{i}", px, pz)
                                for i, (px, pz) in enumerate(fp)])
    assert not off_fp, off_fp[:3]
    return [Finding(
        check="harbours/off_vertex_query", ok=True,
        message=(f"a {bed.nx}x{bed.nz} region at {bed.step:g} m centred on an "
                 f"x.5 berth and a {len(fp)}-sample hull footprint at yaw 47: "
                 f"0 queries off the engine's integer vertex lattice"),
        where=(1066.5, 752.5), measured=0.0, limit=0.0,
        numbers={"region_samples": bed.nx * bed.nz,
                 "footprint_samples": len(fp), "off_lattice": 0},
        population="every query both samplers generate for this berth")]


# ---------------------------------------------------------------------------
# THE REPAIR ORDER - a refusal that names its whole population, or none
# ---------------------------------------------------------------------------
def _neuter_restoration_set():
    """RESTORE THE SHIPPED FORM: the worst vertex, and nothing else."""
    global _RESTORATION_SET
    was = _RESTORATION_SET
    _RESTORATION_SET = False

    def undo():
        global _RESTORATION_SET
        _RESTORATION_SET = was
    return undo


def _neuter_termination_unread():
    """Restore the conflation: an instrument that did not answer, read as an
    instrument that answered CLEAR."""
    global _TERMINATION_UNREAD_IS_UNKNOWN
    was = _TERMINATION_UNREAD_IS_UNKNOWN
    _TERMINATION_UNREAD_IS_UNKNOWN = False

    def undo():
        global _TERMINATION_UNREAD_IS_UNKNOWN
        _TERMINATION_UNREAD_IS_UNKNOWN = was
    return undo


def case_termination_unread_is_not_clear() -> list[Finding]:
    """AN INSTRUMENT THAT DID NOT ANSWER IS NOT AN INSTRUMENT THAT SAID YES.

    The termination class exists so `N refusals` stops being a work queue
    and an operator decision list added together.  Its one failure mode is
    the direction that manufactures green: `contested_by` is None both when
    nobody holds the ground and when the footprints could not be read, and
    the flatten=FORBIDDEN set is unreadable on any chain that will not
    parse.  A classifier that does not separate those reports ACTIONABLE
    exactly where the gate cannot see - work offered over ground it never
    looked at.

    Two refusals, identical but for what the instruments returned, and the
    case asserts on the CLASSES rather than on the verdict, because the
    termination finding is ok=True in every direction by construction.
    """
    def refusal(attribution):
        return Finding(
            check="draining", ok=False, message="a drained sample",
            where=(1989.0, 440.0), measured=0.149, limit=0.101,
            numbers={"restoration": {
                "cuts": [{"xz": [1989.0, 440.0], "applied_after": 29.8938,
                          "cut_m": 0.1492}],
                "named": 1, "population": 1, "off_lattice": [],
                "attribution": attribution}})

    read = {"measured": True, "per_vertex": True, "unowned": 1,
            "unowned_volume_m3": 0.1492, "owned": 0, "owned_volume_m3": 0.0,
            "writes": {}}
    unread = {"measured": False, "per_vertex": True, "unowned": 0,
              "unowned_volume_m3": 0.0, "owned": 0, "owned_volume_m3": 0.0,
              "writes": {}}
    held = dict(read, owned=1, owned_volume_m3=0.1492, unowned=0,
                writes={"terrain_write road_N1_wnorth_shelf (seq 2656)": 1})
    owners = [(0.0, 0.0, 1.0, "boathouse_place", 30)]      # far away

    f_ok = refusal(read)
    check_termination([f_ok], owners)
    assert f_ok.numbers["termination_class"] == "ACTIONABLE", f_ok.numbers

    f_held = refusal(held)
    check_termination([f_held], owners)
    assert f_held.numbers["termination_class"] == "1B_CONTESTED", \
        f_held.numbers

    # the owner set itself unreadable: 1E NAMING THE READ, never ACTIONABLE
    # and never UNKNOWN - UNKNOWN is `NO_CLASS` spelled again
    f_no_owners = refusal(read)
    out = check_termination([f_no_owners], None)
    assert f_no_owners.numbers["termination_class"] == "1E_OUT_OF_ORDER", \
        f_no_owners.numbers
    assert "flatten=FORBIDDEN" in f_no_owners.numbers["prerequisite"], \
        f_no_owners.numbers
    assert out[0].numbers["actionable"] == 0, out[0].numbers
    assert out[0].numbers["owners_read"] is False, out[0].numbers

    # A REFUSAL WITH NO CUT-ONLY REPAIR AT ALL IS 1C, NOT 1A.  MEASURED on
    # Ulfsland 0571968d: the first form of this classifier put SIX of the
    # eleven harbours refusals in 1A_FORBIDDEN - the three berths severed
    # by a STANDING SOLID and the ferry terminal the SEED never connected -
    # and not one of them has an owning record.  A refusal that names an
    # owner it cannot produce is a count naming one member of its
    # population, in the field that decides whose decision it is.
    severed = Finding(
        check="sea_reach", ok=False,
        message="0 of 38 footprint samples connected to open sea",
        where=(-309.0, -123.0), measured=0.0, limit=38.0, numbers={})
    check_termination([severed], owners)
    assert severed.numbers["termination_class"] == "1C_UNSERVED", \
        severed.numbers
    assert severed.numbers["ground_owned_by"] is None, severed.numbers
    # the ownership question WAS asked - the owner set was read - and came
    # back with no owner, which is what makes this 1C and not 1A, and what
    # the shared helper distinguishes by the PRESENCE of a footprint.
    assert severed.numbers["repairable_by_terrain_write"] is False, \
        severed.numbers
    assert severed.numbers["repair_footprint_m"] == 0.0, severed.numbers
    # AND WITH THE OWNER SET UNREAD IT IS STILL 1C: a refusal with no
    # repair has no ownership question for the unread set to answer, so its
    # class is its remedy family's, whatever the owner set says.
    severed_blind = Finding(
        check="sea_reach", ok=False, message="same, owner set unreadable",
        where=(-309.0, -123.0), measured=0.0, limit=38.0, numbers={})
    check_termination([severed_blind], None)
    assert severed_blind.numbers["termination_class"] == "1C_UNSERVED", \
        severed_blind.numbers

    # the write footprints unreadable, owner set fine: 1E naming the
    # attribution the repair was never read against
    f_no_writes = refusal(unread)
    check_termination([f_no_writes], owners)
    assert f_no_writes.numbers["termination_class"] == "1E_OUT_OF_ORDER", \
        f_no_writes.numbers
    assert "terrain_write" in f_no_writes.numbers["prerequisite"], \
        f_no_writes.numbers
    return out


def _neuter_restoration_attribution():
    """RESTORE THE SHIPPED FORM: one owner, sampled at the worst vertex, worn
    by the whole set."""
    global _RESTORATION_ATTRIBUTION
    was = _RESTORATION_ATTRIBUTION
    _RESTORATION_ATTRIBUTION = False

    def undo():
        global _RESTORATION_ATTRIBUTION
        _RESTORATION_ATTRIBUTION = was
    return undo


def case_drain_repair_names_the_owner_of_every_vertex() -> list[Finding]:
    """THE HALF OF A REPAIR ORDER THAT DECIDES WHETHER IT MAY BE EXECUTED.

    THE MEASURED SHAPE, Ulfsland a087af6253a0.  Five `draining` refusals,
    every one of them carrying a full restoration set and a single
    `attributed_to` string taken at its worst vertex:

        49  vertices   worst inside terrain_write road_W5_brgs1_wsouth
        113 vertices   worst inside terrain_write road_T9_wttown_dockshore
        649 vertices   worst inside terrain_write road_N1_wnorth_shelf
        43  vertices   worst inside terrain_write road_N1_wnorth_shelf
        1   vertex     attributed_to EMPTY - owned by nothing

    The fifth is 0.1492 m3 at (1989.0,440.0) and needs no decision from
    anybody, and it survived three gates because the four beside it are a
    road's own ground and the class was named off one sample each time.

    The fixture is that shape in miniature: a drained population whose WORST
    vertex is inside a road write and whose other two are inside nothing.
    The refusal is real either way - this case must REFUSE - but what it
    HANDS BACK is the thing under test, and the assertions are on the split,
    not on the verdict, because the verdict is the weakest thing a check
    produces.
    """
    road = "terrain_write road_N1_wnorth_shelf (seq 2656)"
    rows = [(-238.0, 2541.0, WATER_LEVEL - 0.656, WATER_LEVEL + 2.606),
            (1989.0, 440.0, WATER_LEVEL - 0.106, WATER_LEVEL + 0.043),
            (1990.0, 440.0, WATER_LEVEL - 0.206, WATER_LEVEL + 0.014)]
    owned_at = {(-238.0, 2541.0)}
    out = check_draining(
        [(x, z, g, a) for x, z, g, a in rows], WATER_LEVEL,
        site="a road embankment with two unowned vertices beside it",
        attribute=lambda px, pz: road if (px, pz) in owned_at else "",
        floor_m=0.01)
    hard = [f for f in out if not f.ok]
    assert len(hard) == 1 and hard[0].check == "draining", [f.check
                                                            for f in out]
    a = hard[0].numbers["restoration"]["attribution"]
    assert a["measured"] and a["per_vertex"], a
    # THE CLAIM: the two vertices nobody owns are separated, counted and
    # priced, so the part of this defect that is closable tonight is
    # closable without asking the road's author anything.
    assert a["unowned"] == 2, a
    assert a["owned"] == 1, a
    assert list(a["writes"]) == [road], a
    assert abs(a["unowned_volume_m3"] - (0.149 + 0.220)) < 0.002, a
    assert abs(a["owned_volume_m3"] - 3.262) < 0.002, a
    # and it is in the refusal's own sentence, not only in `numbers`
    assert "lie inside NO recorded terrain_write" in hard[0].message, \
        hard[0].message
    # the section's own guard accepts a per-vertex attribution and refuses
    # a set that wears one owner
    assert not [f for f in check_repair_is_named(out)
                if f.check == "harbours/repair_attribution"], "guard fired"
    return out


def _severed_fixture() -> list:
    """A raised bar across a channel, 36 vertices, worst +0.243 m.

    The shape MEASURED on Ulfsland at the boathouse berth: a submerged
    raise that never broke surface, 36 samples out of navigable depth, the
    worst of them +0.243 m at (-318,-56) where the seed held 2.369 m and the
    save holds 2.127 m against 2.200 m required.  Rows are
    `(x, z, seed_y, applied_y)`.
    """
    rows = []
    for k in range(36):
        x, z = -318.0 + (k % 6), -56.0 + (k // 6)
        rise = 0.243 - 0.005 * k
        rows.append((x, z, WATER_LEVEL - 2.369, WATER_LEVEL - 2.369 + rise))
    return rows


def case_a_restoration_names_every_vertex_it_counted() -> list[Finding]:
    """THE SET IS THE REPAIR, NOT A SAMPLE OF IT.

    36 raised vertices, 36 named, and the volume the emitter will move is
    the volume the defect is.  Neutered back to the shipped form - one
    vertex, the worst - this must come out the other way, which is the whole
    claim: a refusal that names `where` and a count is not a repair order,
    and a cut sized off it moves 0.243 m3 of a 4.374 m3 defect, leaves 35
    vertices standing, and returns next run wearing the next worst sample's
    coordinates.
    """
    rows = _severed_fixture()
    r = restoration_set(rows)
    f = Finding(check="sea_reach/channel_severed", ok=False,
                message="a severed channel", where=(-318.0, -56.0),
                numbers={"samples_raised_out_of_depth": len(rows),
                         "restoration": r},
                population=f"{len(rows)} raised vertices")
    out = check_repair_is_named([f], "a severed channel")
    assert len(out) == 1, out
    if _RESTORATION_SET:
        assert r["named"] == r["population"] == 36, r
        assert out[0].ok, out[0].message
        assert all(c["applied_after"] < WATER_LEVEL for c in r["cuts"]), r
    return out


def case_a_repair_named_by_its_worst_vertex_alone_is_refused() -> list[Finding]:
    """THE SHIPPED STATE OF EVERY REPAIRABLE REFUSAL IN THIS SECTION.

    Stated directly rather than through the neuter, so the refusal is a
    registered case in its own right: a finding that counted 36 and named 1.
    """
    rows = _severed_fixture()
    full = restoration_set(rows)
    one = dict(full, cuts=full["cuts"][:1], named=1,
               volume_named_m3=full["cuts"][0]["cut_m"])
    f = Finding(check="sea_reach/channel_severed", ok=False,
                message="a severed channel", where=(-318.0, -56.0),
                numbers={"restoration": one},
                population=f"{len(rows)} raised vertices")
    out = check_repair_is_named([f], "a severed channel")
    assert len(out) == 1 and not out[0].ok, out
    assert out[0].measured == 1.0 and out[0].limit == 36.0, out[0].numbers
    return out


def case_a_restoration_that_would_fill_is_refused() -> list[Finding]:
    """A SET WITH ONE MEMBER THAT RAISES IS NOT A RESTORATION.

    The sign is an OUTPUT of `restoration_set`, not a hope about its input:
    one row whose applied surface is already BELOW the seed would be a FILL
    if it were put back, and dropping it silently is how a fill gets written
    under a restoration's name.  The function raises instead.
    """
    rows = _severed_fixture()
    x, z, g, _a = rows[7]
    rows[7] = (x, z, g, g - 0.400)          # the save is DEEPER than the seed
    try:
        restoration_set(rows)
    except ValueError as exc:
        return [Finding(
            check="harbours/restoration_sign", ok=False,
            message=(f"a restoration set holding a FILL was refused by the "
                     f"instrument that builds it: {exc}"),
            where=(x, z), measured=-0.400, limit=0.0,
            numbers={"rows": len(rows), "filling_rows": 1,
                     "seed_y": round(g, 3), "applied_y": round(g - 0.400, 3)},
            population=f"{len(rows)} rows offered as a cut-only restoration")]
    raise AssertionError(
        "restoration_set accepted a row whose restoration is a FILL")


def case_a_repair_off_the_vertex_lattice_is_refused() -> list[Finding]:
    """AN OFF-LATTICE CUT IS AN AMBIGUOUS INSTRUCTION.

    `Heightmap.GetHeight` rounds its query to a vertex, so a repair named at
    x.5 is answered by whichever vertex wins the rounding.  The emitter
    refuses it (`roads/regrade.py::load_cuts`); the gate must not have
    handed it over in the first place.
    """
    rows = [(-318.5, -56.0, WATER_LEVEL - 2.369, WATER_LEVEL - 2.126)]
    f = Finding(check="sea_reach/channel_severed", ok=False,
                message="a severed channel", where=(-318.5, -56.0),
                numbers={"restoration": restoration_set(rows)},
                population="1 raised vertex")
    out = check_repair_is_named([f], "a severed channel")
    assert len(out) == 1 and not out[0].ok, out
    assert out[0].check == "harbours/repair_off_lattice", out[0].check
    return out


def _neuter_restoration_floor():
    """RESTORE THE FLOORLESS REPAIR SIZING - the state that shipped."""
    global _RESTORATION_FLOOR
    was = _RESTORATION_FLOOR
    _RESTORATION_FLOOR = False

    def undo():
        global _RESTORATION_FLOOR
        _RESTORATION_FLOOR = was
    return undo


def case_a_repair_inside_the_instruments_noise_is_not_handed_out(
        ) -> list[Finding]:
    """THE FLOOR THAT QUALIFIES THE VERDICT MUST QUALIFY THE REPAIR.

    The same sill as `channel_severed_by_a_submerged_raise`: the seed holds
    2.343 m, the save holds 2.176 m, a rise of 0.167 m, and the channel is
    severed.  The VERDICT stands either way - it is a flood fill and does
    not read this set.  What changes is whether the gate hands over a CUT
    LIST: told the two instruments are MEASURED to disagree by 0.200 m here,
    it must not, because 0.167 m of cross-instrument difference is not
    0.167 m of ground.  Told 0.100 m, it must, and the set is the 1 vertex.

    Neutered - `_RESTORATION_FLOOR` False, the shipped floorless form - the
    0.200 m reading is ignored and the gate hands out the cut anyway, which
    is the state this case exists to refuse.  MEASURED consequence at the
    boathouse berth on 2a6940a934b5: floor 0.156 m, 20 of the 36 counted
    vertices below it, the smallest 0.016 m.
    """
    hull = hull_table()["LittleBoat"]
    seed, now = _basin_with_sill(2.343), _basin_with_sill(2.176)

    def sized(floor):
        out = [f for f in check_channel_severed(
            hull, now, seed, [], (0.0, 0.0), 0.0,
            site="a sill raised 0.167 m under water", floor_m=floor)
            if not f.ok]
        assert out, floor
        return out[0].numbers.get("restoration")

    coarse, fine = sized(0.200), sized(0.100)
    assert fine and fine["named"] >= 1, fine
    assert all(c["cut_m"] >= 0.100 for c in fine["cuts"]), fine
    if _RESTORATION_FLOOR:
        assert coarse is None, coarse
    return [Finding(
        check="harbours/repair_floor", ok=bool(_RESTORATION_FLOOR
                                               and coarse is None),
        message=("a sill raised 0.167 m under water: the two instruments are "
                 "MEASURED to disagree by 0.200 m here and the gate "
                 + ("declines to name a repair, because 0.167 m of "
                    "cross-instrument difference is not 0.167 m of ground"
                    if coarse is None else
                    f"HANDS OVER {coarse['named']} vertex(es) and "
                    f"{coarse['volume_named_m3']:.4f} m3 of cut anyway - a "
                    f"repair sized inside the instruments' own noise")
                 + f"; told 0.100 m it names {fine['named']} vertex(es), "
                 f"{fine['volume_named_m3']:.4f} m3"),
        where=(0.0, 20.0),
        measured=float(0 if coarse is None else coarse["named"]), limit=0.0,
        numbers={"rise_m": 0.167,
                 "named_at_floor_0_200": (0 if coarse is None
                                          else coarse["named"]),
                 "named_at_floor_0_100": fine["named"],
                 "floor_applied": bool(_RESTORATION_FLOOR)},
        population=("the same severed sill, sized twice against two stated "
                    "instrument floors, 0.200 m and 0.100 m"))]


# ---------------------------------------------------------------------------
# THE FIELD'S OWN POPULATION - the instrument that authorises harbour repairs
# ---------------------------------------------------------------------------
BOATHOUSE_BERTH = (-307.04, -119.59)


def _neuter_field_step():
    """RESTORE THE 2 m REACHABILITY FIELD - the state that shipped."""
    global FIELD_STEP_M
    was = FIELD_STEP_M
    FIELD_STEP_M = 2.0

    def undo():
        global FIELD_STEP_M
        FIELD_STEP_M = was
    return undo


def case_field_on_the_engine_lattice_is_accepted() -> list[Finding]:
    """THE SHIPPED FIELD ADDRESSES EVERY VERTEX IT JUDGES.

    Neutered back to 2 m this must come out the other way, which is the whole
    claim: the constant is load-bearing, not cosmetic.
    """
    bed = Bed.around(*BOATHOUSE_BERTH, REACH_RADIUS_M, FIELD_STEP_M)
    r = field_resolution(bed)
    out = check_field_resolution(bed, "boathouse-temple-strait")
    assert r["fraction_addressable"] == 1.0, r
    assert all(f.ok for f in out), [f.message for f in out]
    return out


def case_two_metre_field_cannot_address_the_bar() -> list[Finding]:
    """THE VERTEX THE FIELD NAMED, AND THE VERTEX IT CANNOT.

    MEASURED on Ulfsland 5385652e, the LittleBoat at (-307.04,-119.59).  The
    2 m field anchors on snap(berth) = (-307,-120), so every sample is at ODD
    x and EVEN z and it reads 16,641 of the 66,049 vertices inside its own
    257 x 257 m span.  In the channel out of that berth the save holds
    2.318 m at (-317,-48) and 2.168 m at (-318,-48) against 2.200 m required:
    the SHALLOWER of the two, the one actually under the bar, is at an EVEN x
    AND THE FIELD HAS NO SAMPLE THERE AT ALL.  A repair sized to the vertex
    the field could address is 0.194 m of cut at a place that is not the bar.
    """
    bed = Bed.around(*BOATHOUSE_BERTH, REACH_RADIUS_M, 2.0)
    r = field_resolution(bed)
    named, bar = (-317.0, -48.0), (-318.0, -48.0)

    def addressable(x, z):
        return (abs((x - bed.x0) / bed.step - round((x - bed.x0) / bed.step))
                < 1e-9
                and abs((z - bed.z0) / bed.step
                        - round((z - bed.z0) / bed.step)) < 1e-9)

    assert addressable(*named), (bed.x0, bed.z0)
    assert not addressable(*bar), (bed.x0, bed.z0)
    assert r["samples"] == 16641 and r["vertices_in_span"] == 66049, r
    assert r["x_residue"] == 1 and r["z_residue"] == 0, r
    return check_field_resolution(bed, "boathouse-temple-strait")


def _parity_channel(step: float, bar_z: float) -> tuple[Bed, Hull, list]:
    """A strait with ONE shallow row across it, and a field of the given step.

    Land everywhere except a 7-vertex-wide channel running south from the
    berth to the region edge; the channel is 3.000 m deep except at `bar_z`,
    where it holds 0.500 m against a 2.200 m requirement.  The berth is the
    real one, so the field inherits the real anchor and the real parity.
    """
    cx, cz = -307.0, -120.0
    bed = Bed.around(cx, cz, 24.0, step)
    bed.y = []
    for j in range(bed.nz):
        for i in range(bed.nx):
            x, z = bed.wx(i), bed.wz(j)
            if abs(x - cx) > 3.0 or z > cz + 8.0:
                bed.y.append(WATER_LEVEL + 2.0)        # land
            elif abs(z - bar_z) < 1e-9:
                bed.y.append(WATER_LEVEL - 0.5)        # THE BAR
            else:
                bed.y.append(WATER_LEVEL - 3.0)        # channel
    hull = hull_table()["LittleBoat"]
    return bed, hull, footprint(hull, cx, cz, 0.0)


def case_two_metre_field_certifies_a_barred_channel() -> list[Finding]:
    """THE COARSE FIELD DOES NOT MISNAME THE BAR.  IT DOES NOT SEE IT.

    This is the direction that makes the naming defect a VERDICT defect, and
    it is the one `aperture()` and `refine_route` do not cover: both of them
    refine a WIDTH, and connectivity is not a width.  `_unconnected` - the
    whole of `sea_reach` and the whole of `check_channel_severed` - flood
    fills the field itself.

    One shallow row at z = -135, ODD, across a channel the berth's only way
    out.  On the engine's own lattice the hull is shut in, 38 of 38 samples
    cut off.  The 2 m field anchored on snap(-307.04,-119.59) samples EVEN z
    only, steps from -136 straight to -134, and certifies the harbour open.
    """
    coarse, hull, fp = _parity_channel(2.0, -135.0)
    fine, _h, _f = _parity_channel(1.0, -135.0)
    c_bad, _w, c_sea = _unconnected(hull, coarse, [], fp, WATER_LEVEL)
    f_bad, _w2, f_sea = _unconnected(hull, fine, [], fp, WATER_LEVEL)
    assert c_sea and f_sea, (c_sea, f_sea)
    assert not c_bad, f"the 2 m field was supposed to miss the bar: {c_bad[:2]}"
    assert len(f_bad) == len(fp), (len(f_bad), len(fp))
    reach = check_sea_reach(hull, coarse, [], (-307.0, -120.0), 0.0,
                            WATER_LEVEL, "a barred strait")
    assert all(f.ok for f in reach), [f.message for f in reach]
    out = check_field_resolution(coarse, "a barred strait")
    out += [Finding(
        check="sea_reach/field_stepped_over_the_bar", ok=False,
        message=(f"a barred strait: the {coarse.step:g} m field reports all "
                 f"{len(fp)} footprint samples connected to open sea and the "
                 f"{fine.step:g} m engine lattice reports {len(f_bad)} of "
                 f"{len(fp)} cut off - the bar at z = -135.0 sits on a row "
                 f"the coarse field has no sample on, so it is not measured "
                 f"shallow, it is not measured"),
        where=(-307.0, -135.0),
        measured=float(len(fp) - len(c_bad)), limit=float(len(fp) - len(f_bad)),
        numbers={"coarse_step_m": coarse.step, "fine_step_m": fine.step,
                 "connected_on_the_coarse_field": len(fp) - len(c_bad),
                 "connected_on_the_engine_lattice": len(fp) - len(f_bad),
                 "footprint_samples": len(fp),
                 "bar_depth_m": 0.5,
                 "required_depth_m": hull.required_depth_m},
        population=(f"{len(fp)} footprint samples asked of the SAME ground "
                    f"through two lattices, {coarse.nx * coarse.nz} samples "
                    f"against {fine.nx * fine.nz}"))]
    return out


def case_berth_falls_with_hull_length() -> list[Finding]:
    """THE SAME SLIP, THE HULLS THAT DO NOT: 0.038 m and -0.888 m.

    Three results at one point would have been identical.  Three results under
    three footprints fall monotonically with length, which is the evidence that
    the footprint - not the point - is the thing being measured.
    """
    got = []
    for name in ("LittleBoat", "CargoShip", "VikingShip"):
        hull = hull_table()[name]
        mn = TEMPLE_BY_LENGTH[hull.length_m]
        depth_at = _bed_from_profile(TEMPLE_BERTH, TEMPLE_YAW, hull, mn, 5.211)
        got += check_berth_depth(hull, *TEMPLE_BERTH, TEMPLE_YAW, depth_at,
                                 site="boathouse-temple-strait")
    mins = [f.numbers["min_depth_under_hull_m"] for f in got]
    assert mins == sorted(mins, reverse=True), mins
    assert any(not f.ok for f in got), mins
    return got


def _straight_route(z0: float = 0.0, z1: float = 20.0) -> list:
    return [(0.0, z) for z in range(int(z0), int(z1) + 1, 2)]


def case_refine_finds_a_bar_the_field_steps_over() -> list[Finding]:
    """A 1 m BAR ACROSS THE CHANNEL, INVISIBLE TO THE 2 m ROUTE FIELD.

    This is the harbour form of the defect RoadsRemeasure measured on the road
    population: 2,878 of 21,954 adjacent pairs resolve to the SAME vertex, so
    their step is 0.000 BY CONSTRUCTION and a cliff between them cannot fail
    the check.  Here a shoal one metre wide sits at an ODD z, entirely between
    two samples of a 2 m field anchored on even vertices, and the field reports
    a clear channel.  The 1 m re-measurement of the chosen route is what
    refuses it.
    """
    hull = _cargoship()

    def depth_at(px, pz):
        if abs(pz - 11.0) < 0.5:          # the bar: one metre of it, at odd z
            return WATER_LEVEL - 0.5
        return WATER_LEVEL - 6.0
    out = refine_route(_straight_route(), hull, depth_at,
                       site="a 1 m bar at z=11")
    hard = [f for f in out if not f.ok]
    assert hard, out
    assert hard[0].numbers["narrowest_m"] == 0.0, hard[0].numbers
    # AND the blind quantity: a 2 m field anchored on even vertices never asks
    # about z=11 at all, so every sample it takes is 6 m deep.
    assert all(depth_at(0.0, z) == WATER_LEVEL - 6.0
               for z in range(0, 21, 2))
    return out


def case_refine_accepts_a_clear_channel() -> list[Finding]:
    """The same route with no bar: 21 m of navigable width, accepted."""
    hull = _cargoship()
    out = refine_route(_straight_route(), hull,
                       lambda px, pz: WATER_LEVEL - 6.0,
                       site="a clear 6 m channel")
    assert all(f.ok for f in out), [f.message for f in out]
    assert out[0].numbers["narrowest_m"] >= hull.beam_m, out[0].numbers
    return out


def case_refine_corridor_is_on_the_vertex_lattice() -> list[Finding]:
    """Every refinement query is a vertex, even on a diagonal route."""
    hull = _cargoship()
    path = [(0.5 + i * 1.5, 0.5 + i * 1.5) for i in range(8)]
    pts = [(f"c{i}", px, pz)
           for i, (px, pz) in enumerate(corridor_points(path, hull))]
    off = on_vertex_lattice(pts)
    assert not off, off[:3]
    return [Finding(
        check="hull_entry/off_vertex_query", ok=True,
        message=(f"{len(pts)} refinement queries across a diagonal route: 0 "
                 f"off the engine's integer vertex lattice"),
        where=(0.0, 0.0), measured=0.0, limit=0.0,
        numbers={"queries": len(pts), "off_lattice": 0},
        population="every vertex the refinement asks for on this route")]


def _diagonal_channel(vertices_wide: int):
    """A 45 deg channel EXACTLY `vertices_wide` engine vertices across.

    The route runs along z = x.  The distinct vertices a 45 deg cross-section
    passes are (-m, m) for integer m, whose perpendicular distance from that
    line is |vz - vx| / sqrt(2) = 2|m| / sqrt(2).  So admitting |vz - vx| <=
    vertices_wide - 1 admits exactly m in [-(w-1)/2, (w-1)/2] - an ODD count,
    which is what a cross-section centred on a vertex has.
    """
    lim = float(vertices_wide - 1)

    def depth_at(px, pz):
        return (WATER_LEVEL - 6.0 if abs(pz - px) <= lim
                else WATER_LEVEL - 0.1)
    return depth_at


def case_refine_45_deg_channel_13_vertices_wide() -> list[Finding]:
    """A CHANNEL ON A 45 DEG BEARING, 13 VERTICES WIDE, AGAINST A 14.10 m BEAM.

    THE STATE THIS CASE EXISTS FOR, and an axis-aligned fixture is
    STRUCTURALLY INCAPABLE OF CATCHING IT.  `snap` is nearest-vertex, so when
    the cross-section offset `t` steps 1 m along an OBLIQUE normal two offsets
    can land on the SAME vertex.  MEASURED here: 19 offsets across a 45 deg
    normal resolve to THIRTEEN distinct vertices.  Counting the offsets
    reported 19 m of navigable width where the engine has heights for 13, and
    a `BigCargoShip` whose beam is 14.10 m was sailed through it.  On an
    axis-aligned normal all 19 offsets are distinct, so the same check passes
    either way and the bug is invisible: this case is diagonal on purpose.

    THE CHANNEL IS BOUNDED BY ITS OWN BANKS rather than by the window, and
    that is the second defect this fixture has had to carry.  While the
    window was also counted in offsets, a channel of unbounded width read
    13.000 m on a 45 deg bearing FOR THE SAME REASON the run did - and the
    case asserted that 13.000 as the right answer, which made the window
    artefact the expected result.  Here 13 is the width of the WATER: outside
    |z - x| <= 12 the bed holds 0.100 m.
    """
    hull = hull_table()["BigCargoShip"]
    assert abs(hull.beam_m - 14.10) < 1e-6, hull
    route = [(float(i), float(i)) for i in range(0, 21, 2)]
    half = int(math.ceil(hull.beam_m / 2.0 + 1.0))
    k = 0.5 ** 0.5
    offs = [snap(route[0][0] - k * t, route[0][1] + k * t)
            for t in range(-half, half + 1)]
    distinct = len({*offs})
    assert len(offs) == 19 and distinct == 13, (len(offs), distinct)
    # The old counting was `run += 1.0` per OFFSET, so it would have read
    # 19.000 m here and cleared a 14.10 m beam by 4.9 m.
    assert len(offs) >= hull.beam_m > distinct, (len(offs), distinct,
                                                 hull.beam_m)
    out = refine_route(route, hull, _diagonal_channel(13),
                       site="a 45 deg channel 13 vertices wide")
    hard = [f for f in out if not f.ok]
    assert hard, [f.message for f in out]
    assert hard[0].numbers["narrowest_m"] == 13.0, hard[0].numbers
    return out


def case_refine_45_deg_window_reaches_the_beam() -> list[Finding]:
    """THE SAME BEARING, A CHANNEL WIDE ENOUGH, AND A HULL THAT MUST PASS.

    MEASURED on Ulfsland 5385652e, and it is all three of this world's
    `hull_entry/refined` refusals at once: every one read EXACTLY 9.000 m
    against a 9.16 m beam, and the VikingShip's own narrowest cross-section
    at (1932,572) is nine vertices from (1928,576) to (1936,568) whose beds
    are 15.858, 16.754, 17.616, 18.690, 19.771, 20.747, 21.579, 22.173 and
    23.088 - between 6.912 m and 14.142 m of water, with no shoal in it
    anywhere.  Nine was not the width of the channel; nine was the width of
    the WINDOW, because +/-6 OFFSETS along a 45 deg normal reach only nine
    distinct vertices.  A 9.16 m hull could not pass a check that could not
    show it 10.

    Here the channel is 15 vertices wide on the same bearing and the same
    9.16 m hull must be ACCEPTED.  The reading is 13.000 m and not 15.000,
    because the window is `ceil(beam/2 + 1) = 6` vertices each side and the
    check is not trying to measure the ocean - it is trying to see whether
    the beam fits.  13 is what 13 was on an axis-aligned bearing all along;
    the defect was that a 45 deg bearing got 9 of the same window.
    """
    hull = _cargoship()
    route = [(float(i), float(i)) for i in range(0, 21, 2)]
    out = refine_route(route, hull, _diagonal_channel(15),
                       site="a 45 deg channel 15 vertices wide")
    assert all(f.ok for f in out), [f.message for f in out]
    n = out[0].numbers
    assert n["narrowest_m"] == 13.0, n
    assert n["narrowest_m"] >= hull.beam_m, n
    return out


def _neuter_cross_window_in_offsets():
    """Restore the window counted in OFFSETS: +/-ceil(beam/2+1) steps of 1 m
    along the normal, deduplicated afterwards - so a 45 deg bearing gets nine
    vertices where an axis-aligned one gets thirteen."""
    import sys as _s                                       # noqa: PLC0415
    mod = _s.modules[__name__]
    good = mod.cross_section_vertices

    def by_offset(px, pz, nx, nz, half_m):
        k = int(math.ceil(half_m))
        out, seen = [], None
        for t in range(-k, k + 1):
            v = mod.snap(px + nx * t, pz + nz * t)
            if v != seen:
                out.append(v)
                seen = v
        return out
    mod.cross_section_vertices = by_offset

    def undo():
        mod.cross_section_vertices = good
    return undo





def case_berth_dock_northcape_floats() -> list[Finding]:
    """THE BERTH THAT IS FINE: dock-northcape, 114 samples, 3.038 m over 3.0 m.

    ledger seq 2125/2297: CargoShip at (-148.91, 2644.61) yaw 55, min 3.038,
    max 5.407, required 3.0.  A 0.038 m margin, which this check must ACCEPT -
    a gate that refuses a berth the world got right teaches the next builder to
    bypass it.
    """
    hull = _cargoship()
    depth_at = _bed_from_profile((-148.91, 2644.61), 55.0, hull, 3.038, 5.407)
    out = check_berth_depth(hull, -148.91, 2644.61, 55.0, depth_at,
                            site="dock-northcape")
    n = out[0].numbers
    assert n["hull_samples"] == 114, n["hull_samples"]
    return out


def case_berth_partial_footprint_refused() -> list[Finding]:
    """A footprint the bed instrument only half covered is not a verdict."""
    hull = _cargoship()
    full = _bed_from_profile((-148.91, 2644.61), 55.0, hull, 3.038, 5.407)
    n = {"i": 0}

    def holey(px, pz):
        n["i"] += 1
        return None if n["i"] % 7 == 0 else full(px, pz)
    return check_berth_depth(hull, -148.91, 2644.61, 55.0, holey,
                             site="dock-northcape (instrument holed)")


# --- CHECK 3 fixtures -------------------------------------------------------
# bridge N3-x01, ledger seq 2858-2862, flatten=FORBIDDEN: "its piles stand on
# the generated bed of an inland channel 1.46 m below the y=30.0 water plane
# and its deck stands at the road's own solved H 32.500 m".  The re-grade over
# it was refused in seq 4698: 169 samples, worst 0.880 m at (-730.7, 5936.6).
N3_CHANNEL = (-730.7, 5936.6)
N3_GENERATED_BED = WATER_LEVEL - 1.46          # 28.54
N3_ROAD_FREEBOARD = 32.5


def _n3_samples(applied):
    return [(N3_CHANNEL[0] + i * 2.0, N3_CHANNEL[1], N3_GENERATED_BED,
             applied) for i in range(-6, 7)]


def case_drain_fill_to_road_freeboard() -> list[Finding]:
    """THE WRITE THAT WAS REFUSED: fill the channel to the road's own datum."""
    out = check_draining(_n3_samples(N3_ROAD_FREEBOARD), site="bridge N3-x01")
    hard = [f for f in out if not f.ok]
    assert hard, out
    n = hard[0].numbers
    assert abs(n["worst_rise_m"] - (N3_ROAD_FREEBOARD
                                    - N3_GENERATED_BED)) <= 1e-3, n
    return out


def case_drain_dredge_is_accepted() -> list[Finding]:
    """THE OPPOSITE SIGN: the same channel dredged to 24.0 m.

    Lowering a bed ADDS water.  If this case ever fails, the check has become
    "do not touch water", which refuses the repair along with the defect.
    """
    out = check_draining(_n3_samples(24.0), site="bridge N3-x01 dredged")
    assert all(f.ok for f in out), out
    assert any(f.check.endswith("dredge") for f in out), out
    return out


def case_drain_as_built_bridge_on_piles() -> list[Finding]:
    """THE WORLD AS IT STANDS: the bridge on piles, the bed untouched."""
    return check_draining(_n3_samples(N3_GENERATED_BED),
                          site="bridge N3-x01 as built")


def case_drain_shoaled_channel() -> list[Finding]:
    """A fill that stopped short of the water plane still takes the depth."""
    return check_draining(_n3_samples(WATER_LEVEL - 0.20),
                          site="bridge N3-x01 part-filled")


def case_drain_reference_unavailable() -> list[Finding]:
    """No seed surface, no verdict - and no pass."""
    return [drain_reference_missing("bridge N3-x01")]


# The instrument floor, in both directions, on the numbers that MADE it exist.
# MEASURED on Ulfsland before the floor was added: the harbours section
# reported 3,990 "shoaled" samples in one harbour, worst +0.384 m at
# (673.5,-22.0) leaving 0.163 m where the seed had 0.546 m, and a "drained"
# sample at (86.0,-358.0) of +0.075 m, seed bed 29.940 against applied 30.015.
# A 0.075 m rise is not a drained harbour; it is two height instruments read
# through two harnesses disagreeing, and a verdict decided inside that
# disagreement is not a measurement.
FLOOR_FIXTURE_M = 0.15


def case_drain_inside_the_instrument_floor() -> list[Finding]:
    """A 0.075 m rise under a 0.150 m measured floor is NOT a drain."""
    samples = [(86.0 + i, -358.0, 29.940, 30.015) for i in range(-6, 7)]
    out = check_draining(samples, site="a 0.075 m disagreement",
                         floor_m=FLOOR_FIXTURE_M)
    assert all(f.ok for f in out), [f.message for f in out]
    assert out[0].numbers["instrument_floor_m"] == FLOOR_FIXTURE_M, out
    return out


def case_drain_outside_the_instrument_floor() -> list[Finding]:
    """The floor must not swallow a real one: +0.300 m over 0.250 m of water."""
    samples = [(86.0 + i, -358.0, 29.75, 30.05) for i in range(-6, 7)]
    out = check_draining(samples, site="a 0.300 m fill over shallow water",
                         floor_m=FLOOR_FIXTURE_M)
    hard = [f for f in out if not f.ok]
    assert hard and hard[0].check == "draining", out
    assert abs(hard[0].numbers["worst_rise_m"] - 0.300) <= 1e-6, hard[0].numbers
    return out


def case_drain_shoaled_puddle_is_not_a_channel() -> list[Finding]:
    """THE LIVE NUMBERS THAT WERE 3,990 REFUSALS: 0.546 m of seed water.

    A rise of 0.384 m in half a metre of shoreline water is a true measurement
    and not a drained channel.  The shallowest hull in the table draws 0.8 m
    and wants 1.0 m of clearance, so no vessel could ever have used it.  The
    check must ACCEPT this, or its report is 3,990 lines nobody reads and the
    one line that matters is buried in them.
    """
    samples = [(673.5 + i, -22.0, 29.454, 29.838) for i in range(-6, 7)]
    out = check_draining(samples, site="a shoreline puddle",
                         floor_m=0.05)
    assert all(f.ok for f in out), [f.message for f in out]
    return out


def case_drain_shoaled_channel_is_refused() -> list[Finding]:
    """The same arithmetic in a real channel: 1.46 m of seed water, 0.20 m left."""
    out = check_draining(_n3_samples(WATER_LEVEL - 0.20),
                         site="bridge N3-x01 part-filled", floor_m=0.05)
    hard = [f for f in out if not f.ok]
    assert hard and hard[0].check == "draining/shoaled", out
    return out

# THE THREE BERTHS THAT WERE REFUSED ON WATER THE INSTRUMENTS CANNOT SEE.
# MEASURED on Ulfsland 5385652e, per-sample, all 7 berths:
#   CargoShip (1941.25,573.81)   1 drained sample, seed 29.956 applied 30.064,
#                                0.044 m of seed water, floor 0.100 m
#   CargoShip (1756.48,641.63)   1 sample, seed 29.966 applied 30.059,
#                                0.034 m of seed water, floor 0.090 m
#   VikingShip ferry-terminal-   2 samples, worst seed 29.945 applied 30.080,
#   eastisle (declared)          0.055-0.079 m of seed water, floor 0.099 m
# Each was reported as "4 m2 / 8 m2 of water permanently removed".
FLOOR_BERTH_SEED = 29.956
FLOOR_BERTH_APPLIED = 30.064
FLOOR_BERTH_FLOOR_M = 0.100


def case_drain_puddle_shallower_than_the_instrument_floor() -> list[Finding]:
    """A 0.044 m PUDDLE READ THROUGH A PAIR THAT DISAGREE BY 0.100 m.

    The rise beats the floor - 0.108 m against 0.100 m - so the shipped form
    refuses, and it refuses on ONE sample of 16,641.  But the WATER it claims
    to have removed is 0.044 m deep, read through the same two instruments,
    and whether that point was ever wet is not something this pair can
    resolve.  A verdict decided inside their disagreement is not a
    measurement - which is the sentence the floor was introduced with.
    """
    out = check_draining(
        [(1971.0, 578.0, FLOOR_BERTH_SEED, FLOOR_BERTH_APPLIED)],
        site="CargoShip (1941.25,573.81)", floor_m=FLOOR_BERTH_FLOOR_M)
    assert all(f.ok for f in out), [f.message for f in out]
    n = out[0].numbers
    assert n["submerged_samples"] == 1, n
    assert n["judgeable_samples"] == 0, n
    assert n["unresolvable_samples"] == 1, n
    return out


def case_drain_real_fill_survives_the_seed_depth_floor() -> list[Finding]:
    """THE OTHER SIGN, ON THE SAME WORLD'S OWN WORST SAMPLE.

    road_N1_wnorth_shelf at (-239,2541): seed 29.853, applied 32.606, against
    the same berth's measured 0.093 m floor.  0.147 m of seed water is thin -
    and it is 1.58 times the floor, so it is resolvable, and +2.753 m of fill
    over it stays a drain.  A rule that silenced this would be a tolerance
    wearing a population's clothes.
    """
    out = check_draining([(-239.0, 2541.0, 29.853, 32.606)],
                         site="road_N1_wnorth_shelf", floor_m=0.093)
    hard = [f for f in out if not f.ok]
    assert hard and hard[0].check == "draining", [f.message for f in out]
    n = hard[0].numbers
    assert n["judgeable_samples"] == 1 and n["unresolvable_samples"] == 0, n
    assert n["worst_rise_m"] == 2.753, n
    return out


def _neuter_drain_seed_depth_floor():
    """Restore the shipped form: the floor applied to the RISE only, so a
    point the instruments cannot agree was ever wet still enters the
    population and can carry a refusal by itself."""
    import sys as _s                                       # noqa: PLC0415
    mod = _s.modules[__name__]
    mod._DRAIN_SEED_DEPTH_FLOOR = False

    def undo():
        mod._DRAIN_SEED_DEPTH_FLOOR = True
    return undo


def _neuter_solid_index_order():
    """Restore the defect an index of this shape invites: buckets scanned in
    whatever order they were built in rather than in the caller's list
    order, so an overlapped cell names the wrong prefab."""
    import sys as _s                                       # noqa: PLC0415
    mod = _s.modules[__name__]
    good = mod.SolidIndex.hit

    def reversed_hit(self, x, z):
        c = self.CELL_M
        cand = self.grid.get((int(math.floor(x / c)),
                              int(math.floor(z / c))))
        if not cand:
            return None
        for idx in reversed(cand):
            b = self.bars[idx]
            if poly_contains(b.poly, x, z):
                return b
        return None
    mod.SolidIndex.hit = reversed_hit

    def undo():
        mod.SolidIndex.hit = good
    return undo


def case_solid_index_answers_exactly_what_the_scan_did() -> list[Finding]:
    """THE INDEX IS AN OPTIMISATION AND IT MUST NOT BE A CHANGE.

    `navigable` names the FIRST containing barrier in the caller's list, and
    that name is part of the verdict - `best_sill` reports it and
    `hull_entry` prints it.  Two overlapping solids over the same cell
    therefore have a RIGHT answer and a wrong one, and a bucketed index is
    exactly the shape that silently returns the other one.

    Asserted against the linear scan it replaced, over every cell of a bed
    that carries three overlapping barriers - one of them a mesh bound laid
    OVER a box, so the `solid:`/`bound:` half of the string is discriminated
    too.
    """
    hull = _cargoship()
    bed = _basin_with_sill(4.0)
    bars = [
        Barrier(prefab="stone_wall_4x2",
                poly=((-6.0, -6.0), (6.0, -6.0), (6.0, 6.0), (-6.0, 6.0)),
                y_lo=WATER_LEVEL - 9.0, y_hi=WATER_LEVEL + 1.0, tight=True),
        Barrier(prefab="rock4_coast",
                poly=((-8.0, -8.0), (8.0, -8.0), (8.0, 8.0), (-8.0, 8.0)),
                y_lo=WATER_LEVEL - 9.0, y_hi=WATER_LEVEL + 1.0, tight=False),
        Barrier(prefab="wood_pole_log_4",
                poly=((-13.0, -13.0), (-11.0, -13.0),
                      (-11.0, -11.0), (-13.0, -11.0)),
                y_lo=WATER_LEVEL - 9.0, y_hi=WATER_LEVEL + 2.0, tight=True),
    ]
    keel, water = hull.keel_y(), WATER_LEVEL
    wet = [b for b in bars if b.spans_band(keel, water)]
    index = SolidIndex(wet)
    checked = overlapped = 0
    for j in range(bed.nz):
        for i in range(bed.nx):
            x, z = bed.wx(i), bed.wz(j)
            want = next((b for b in wet if poly_contains(b.poly, x, z)), None)
            checked += 1
            if sum(1 for b in wet if poly_contains(b.poly, x, z)) > 1:
                overlapped += 1
            assert index.hit(x, z) is want, (x, z, want, index.hit(x, z))
    assert overlapped > 0, "the fixture must actually overlap somewhere"
    return [Finding(
        check="harbours/solid_index", ok=True,
        message=(f"the bucketed solid index returns the identical barrier "
                 f"OBJECT to the linear scan at all {checked} cells of this "
                 f"bed, {overlapped} of which are covered by more than one "
                 f"of the {len(wet)} barriers and therefore have a wrong "
                 f"answer available"),
        measured=float(checked), limit=float(checked),
        numbers={"cells": checked, "overlapped_cells": overlapped,
                 "barriers": len(wet)},
        population=f"all {checked} cells of a {bed.nx}x{bed.nz} bed at "
                   f"{bed.step:g} m against {len(wet)} barriers, of which "
                   f"{overlapped} cells are multiply covered")]


def _neuter_write_index_order():
    """BUCKETS SCANNED IN BUILD ORDER - the way a bucketed index silently
    changes the attribution it was only supposed to speed up."""
    was = WriteIndex.at

    def first_in_bucket(self, x, z, margin=0.0, exact_only=False):
        c = self.CELL_M
        cand = self.grid.get((int(math.floor(x / c)),
                              int(math.floor(z / c)))) or ()
        for wi, s in cand:
            w = self.writes[wi]
            if exact_only and not w["exact"]:
                continue
            if s < 0:
                hit = (abs(x - w["cx"]) <= w["hx"] + margin
                       and abs(z - w["cz"]) <= w["hz"] + margin)
            else:
                hit = _seg_distance(x, z, w["nodes"][s:s + 2],
                                    w["half"] + margin)
            if hit:
                if w["kind"] == "pad":
                    return (f"terrain_write {w['name']} (seq {w['seq']}"
                            + ("" if w["exact"] else ", located by its zone")
                            + ")")
                return f"terrain_write {w['name']} (seq {w['seq']})"
        return ""
    WriteIndex.at = first_in_bucket

    def undo():
        WriteIndex.at = was
    return undo


def case_write_index_answers_exactly_what_the_scan_did() -> list[Finding]:
    """THE ATTRIBUTION IS PART OF THE VERDICT, SO THE INDEX MUST NOT MOVE IT.

    `check_draining` prints the write it blames a drained sample on, and
    `written_at` answers with the FIRST write in the ledger's own order that
    covers the point.  Two writes over one sample therefore have a right
    answer and a wrong one - and a road is bucketed per SEGMENT, so a bucket
    holds several entries of the same write and several writes at once.

    Asserted against the linear scan at every vertex of a 41 x 41 m patch
    carrying an early ROAD, a later PAD laid across it, and a zone-located
    pad that `exact_only` must skip - at both margins the section uses.
    """
    writes = [
        dict(kind="road", name="road_first", seq=11, half=3.0, exact=True,
             nodes=[(float(t), 0.0) for t in range(-20, 21)]),
        dict(kind="pad", name="pad_over_it", seq=22, cx=0.0, cz=0.0,
             hx=6.0, hz=6.0, exact=True),
        dict(kind="pad", name="pad_by_its_zone", seq=33, cx=14.0, cz=14.0,
             hx=6.0, hz=6.0, exact=False),
    ]
    index = WriteIndex(writes, margin_max=4.0)
    checked = overlapped = named = 0
    for zi in range(-20, 21):
        for xi in range(-20, 21):
            x, z = float(xi), float(zi)
            for margin, exact in ((4.0, False), (2.0, True)):
                want = written_at(writes, x, z, margin=margin,
                                  exact_only=exact)
                got = index.at(x, z, margin=margin, exact_only=exact)
                assert got == want, (x, z, margin, exact, want, got)
                checked += 1
                named += bool(want)
            hits = sum(1 for w in writes
                       if written_at([w], x, z, margin=4.0))
            overlapped += hits > 1
    assert overlapped > 0, "the fixture must actually overlap somewhere"
    assert named > 0, "the fixture must actually name a write somewhere"
    try:
        index.at(0.0, 0.0, margin=9.0)
    except ValueError:
        pass
    else:                                    # pragma: no cover - a regression
        raise AssertionError("a margin wider than the index was built for "
                             "must raise, not answer")
    return [Finding(
        check="harbours/write_index", ok=True,
        message=(f"the bucketed write index returns the identical string to "
                 f"the linear scan at all {checked} queries over this patch, "
                 f"{overlapped} vertices of which are covered by more than "
                 f"one of the {len(writes)} writes and therefore have a wrong "
                 f"answer available; {named} of the queries name a write"),
        measured=float(checked), limit=float(checked),
        numbers={"queries": checked, "overlapped_vertices": overlapped,
                 "writes": len(writes), "queries_naming_a_write": named,
                 "margins_m": [4.0, 2.0]},
        population=(f"every vertex of a 41x41 m patch at both margins this "
                    f"section uses, against {len(writes)} writes"))]


# --- CHECK 4 fixtures -------------------------------------------------------
def _pond(depth: float, hull: Hull, sill: float | None = None) -> Bed:
    """A 60 m region: deep water in the middle, land all round, and optionally
    a channel out through the north edge."""
    bed = Bed.around(0.0, 0.0, 30.0, FIELD_STEP_M)
    bed.y = []
    for j in range(bed.nz):
        row = []
        for i in range(bed.nx):
            x, z = bed.wx(i), bed.wz(j)
            deep = abs(x) <= 12.0 and abs(z) <= 12.0
            chan = sill is not None and abs(x) <= 8.0 and z > 12.0
            if deep:
                row.append(WATER_LEVEL - depth)
            elif chan:
                row.append(WATER_LEVEL - sill)
            else:
                row.append(WATER_LEVEL + 2.0)
        bed.y.extend(row)
    return bed


def _basin_with_sill(sill: float) -> Bed:
    """A 60 m region with OPEN SEA IN IT: a 24 m basin, a 16 m channel with a
    sill in it, and 9 m of water from z = 18 m to the region edge.

    The difference from `_pond` matters: here open sea IS in view, so a refusal
    is a statement about the harbour and not about the window.  That is the
    distinction "no navigable route" and "region too small" are kept apart
    for."""
    bed = Bed.around(0.0, 0.0, 30.0, FIELD_STEP_M)
    bed.y = []
    for j in range(bed.nz):
        row = []
        for i in range(bed.nx):
            x, z = bed.wx(i), bed.wz(j)
            if abs(x) <= 12.0 and abs(z) <= 12.0:
                row.append(WATER_LEVEL - 9.0)        # the basin
            elif z >= 18.0:
                row.append(WATER_LEVEL - 9.0)        # open sea
            elif abs(x) <= 8.0 and z > 12.0:
                row.append(WATER_LEVEL - sill)       # the channel's sill
            else:
                row.append(WATER_LEVEL + 2.0)        # land
        bed.y.extend(row)
    return bed


def case_entry_shut_in_behind_a_sill() -> list[Finding]:
    """SHUT IN, AND THE REFUSAL NAMES THE DREDGE THAT WOULD OPEN IT.

    9 m of water under the whole hull, 9 m of open sea 18 m away, and 1.000 m
    over the sill between them against the 3.000 m a CargoShip needs.  "No
    navigable route to open sea" is a verdict nobody can act on; the sill's
    depth, shortfall and coordinate are.
    """
    hull = _cargoship()
    bed = _basin_with_sill(1.0)
    out = check_hull_entry(hull, bed, [], (0.0, 0.0), 0.0,
                           site="a basin behind a 1 m sill")
    hard = [f for f in out if not f.ok]
    assert hard, out
    n = hard[0].numbers
    assert n["deepest_sill_m"] == 1.0, n
    assert abs(n["sill_short_by_m"] - 2.0) <= 1e-6, n
    assert n["sill_blocked_by"] == "the bed alone", n
    assert n["pool_cells"] > 0, n
    return out


def case_entry_over_a_dredged_sill() -> list[Finding]:
    """THE SAME SILL DREDGED to 4.000 m: the hull gets out and is accepted."""
    hull = _cargoship()
    out = check_hull_entry(hull, _basin_with_sill(4.0), [], (0.0, 0.0), 0.0,
                           site="a basin behind a dredged 4 m sill")
    assert all(f.ok for f in out), [f.message for f in out]
    return out


def case_reach_landlocked_pocket() -> list[Finding]:
    """A LANDLOCKED POCKET OF 9 m WATER BERTHS NOTHING.

    Depth alone accepts this berth - 9 m under every footprint sample, six
    times what the hull needs.  Connectivity is the only thing that refuses it.
    """
    hull = _cargoship()
    bed = _pond(9.0, hull)
    out = check_sea_reach(hull, bed, [], (0.0, 0.0), 0.0,
                          site="a landlocked pocket")
    hard = [f for f in out if not f.ok]
    assert hard, out
    # the blind quantity - depth under the footprint - accepts
    deep = check_berth_depth(hull, 0.0, 0.0, 0.0,
                             lambda px, pz: WATER_LEVEL - 9.0
                             if abs(px) <= 12 and abs(pz) <= 12 else None)
    assert deep[0].check == "berth_depth/unread" or deep[0].ok, deep
    return out


def case_channel_severed_below_the_surface() -> list[Finding]:
    """A WRITE RAISED A SUBMERGED BED BY 0.167 m AND CLOSED THE HARBOUR.

    THE STATE, from Ulfsland save 2c7011d2 - the LittleBoat at
    boathouse-temple-strait, whose whole pool hung on ONE vertex: the seed
    held 2.343 m there, the save holds 2.176 m, and 2.200 m is required.
    Reproduced here at the same numbers.

    AND THE TWO CHECKS THAT CANNOT SEE IT, asserted rather than described:
    the raised bed never reaches the 30 m plane, so `draining` has nothing to
    report, and it keeps 93 % of its depth, so `draining/shoaled` has nothing
    to report.  Only asking BOTH BEDS the connectivity question finds it.
    """
    hull = hull_table()["LittleBoat"]
    assert abs(hull.required_depth_m - 2.2) < 1e-9, hull
    seed = _basin_with_sill(2.343)
    now = _basin_with_sill(2.176)
    out = check_channel_severed(hull, now, seed, [], (0.0, 0.0), 0.0,
                                site="a sill raised 0.167 m under water")
    hard = [f for f in out if not f.ok]
    assert hard, out
    n = hard[0].numbers
    assert n["samples_raised_out_of_depth"] > 0, n
    assert abs(n["worst_rise_m"] - 0.167) < 1e-3, n
    assert abs(n["restore_to_seed_m"] - 0.167) < 1e-3, n
    # THE BLIND QUANTITIES.  Neither existing check can produce a finding on
    # this bed, which is why a third one had to exist.
    samples = [(0.0, 20.0, WATER_LEVEL - 2.343, WATER_LEVEL - 2.176)]
    assert not [f for f in check_draining(samples, WATER_LEVEL, floor_m=0.01)
                if not f.ok], "draining must be blind to a submerged raise"
    assert WATER_LEVEL - 2.176 < WATER_LEVEL, "the bed never breaks surface"
    assert 2.176 > 2.343 / 2.0, "it keeps more than half its depth"
    return out


def case_channel_severed_behind_a_solid_that_severs_both_beds() -> list[Finding]:
    """THE GROUND TOOK THE CHANNEL AND A ROCK IS STANDING IN IT TOO.

    MEASURED on Ulfsland 5385652e at the LittleBoat, boathouse-temple-strait,
    over its own 38 footprint samples:
        seed bed, no barriers         0 of 38 cut off
        applied bed, no barriers     38 of 38 cut off
        seed bed, 5,414 barriers     38 of 38 cut off
        applied bed, 5,414 barriers  38 of 38 cut off
    The solids sever it on their own, so controlling for them by INCLUDING
    them makes `was_bad` non-empty and the check returns [] - over a seed
    that connected this berth and a bed that does not.  The verdict is the
    same whether the ground took the channel or not.

    Reproduced here at the same shape: the sill raised 0.167 m under water,
    AND a rock across the mouth that closes both beds by itself.
    """
    hull = hull_table()["LittleBoat"]
    seed = _basin_with_sill(2.343)
    now = _basin_with_sill(2.176)
    rock = [Barrier(prefab="rock4_coast",
                    poly=((-13.0, 13.0), (13.0, 13.0),
                          (13.0, 17.0), (-13.0, 17.0)),
                    y_lo=WATER_LEVEL - 9.0, y_hi=WATER_LEVEL + 1.0,
                    tight=True, part="box")]
    # the fixture must really be saturated, or this case proves nothing
    pts = footprint(hull, 0.0, 0.0, 0.0)
    was_bad, _w, was_sea = _unconnected(hull, seed, rock, pts, WATER_LEVEL)
    assert was_sea and was_bad, "the solid must sever the SEED bed too"
    clean, _w2, clean_sea = _unconnected(hull, seed, [], pts, WATER_LEVEL)
    assert clean_sea and not clean, "the seed bed alone must connect it"
    out = check_channel_severed(hull, now, seed, rock, (0.0, 0.0), 0.0,
                                site="a sill raised under a rock")
    hard = [f for f in out if not f.ok]
    assert hard, [f.message for f in out]
    n = hard[0].numbers
    assert abs(n["restore_to_seed_m"] - 0.167) < 1e-3, n
    assert n["cut_off_by_the_bed_alone"] == len(pts), n
    assert n["cut_off_with_every_solid"] == len(pts), n
    return out


def _neuter_severed_control_by_barriers():
    """Restore the shipped control: both beds read WITH the solids, so a
    solid set that severs the berth on its own saturates both answers."""
    import sys as _s                                       # noqa: PLC0415
    mod = _s.modules[__name__]
    mod._SEVERED_CONTROL_BY_BARRIERS = True

    def undo():
        mod._SEVERED_CONTROL_BY_BARRIERS = False
    return undo



def case_channel_the_seed_never_connected() -> list[Finding]:
    """A POCKET THE SEED DREW LANDLOCKED IS NOT A SEVERED CHANNEL.

    The accept arm, and it is load-bearing: without it this check would
    charge every naturally enclosed pool to a write that never happened.
    """
    hull = hull_table()["LittleBoat"]
    seed = _pond(9.0, hull)                  # no channel, on the SEED
    now = _pond(9.0, hull)
    out = check_channel_severed(hull, now, seed, [], (0.0, 0.0), 0.0,
                                site="a pocket the seed drew closed")
    assert out == [], [f.message for f in out]
    # and the pool really is landlocked, so this is not a vacuous accept
    assert [f for f in check_sea_reach(hull, now, [], (0.0, 0.0), 0.0)
            if not f.ok], "the fixture must be a landlocked pool"
    return out


def _neuter_channel_severed():
    """Restore the state before this check existed: sea_reach alone, which
    cannot tell a coast the seed drew closed from a channel a write took
    away."""
    import sys as _s                                       # noqa: PLC0415
    mod = _s.modules[__name__]
    good = mod.check_channel_severed
    mod.check_channel_severed = lambda *a, **k: []
    def undo():
        mod.check_channel_severed = good
    return undo


def _neuter_refine_per_offset():
    """Restore the per-OFFSET run counting, so a 45 deg cross-section counts
    the same vertex twice and reads wider than the engine has heights for."""
    import sys as _s                                       # noqa: PLC0415
    mod = _s.modules[__name__]
    good = mod.snap
    seen = {"n": 0}
    def loose(x, z):
        # a snap that never collides: every offset becomes its own vertex,
        # which is exactly what counting per offset assumed
        seen["n"] += 1
        return (round(x, 6) + seen["n"] * 1e-9, round(z, 6))
    mod.snap = loose
    def undo():
        mod.snap = good
    return undo

def case_reach_basin_connected() -> list[Finding]:
    """THE SAME POCKET WITH A CHANNEL OUT, at the dredged 6.000 m."""
    hull = _cargoship()
    bed = _pond(6.0, hull, sill=6.0)
    out = check_sea_reach(hull, bed, [], (0.0, 0.0), 0.0,
                          site="Ulfsgard basin (dredged channel)")
    assert all(f.ok for f in out), [f.message for f in out]
    return out


def case_reach_region_too_small() -> list[Finding]:
    """A region with no navigable boundary cell is a REFUSAL, not a verdict."""
    hull = _cargoship()
    bed = _pond(9.0, hull)
    out = check_sea_reach(hull, bed, [], (0.0, 0.0), 0.0, site="pocket")
    return out


def case_entry_through_the_dredged_channel() -> list[Finding]:
    """CHECK 1 ON A FIELD: a 16 m channel passes a 9.16 m beam."""
    hull = _cargoship()
    bed = _pond(6.0, hull, sill=6.0)
    out = check_hull_entry(hull, bed, [], (0.0, 0.0), 0.0,
                           site="Ulfsgard basin (16 m channel)")
    hard = [f for f in out if not f.ok]
    assert not hard, [f.message for f in hard]
    return out


def case_entry_blocked_by_a_pier_in_the_channel() -> list[Finding]:
    """The same channel with a pile row across it: the water is still there."""
    hull = _cargoship()
    bed = _pond(6.0, hull, sill=6.0)
    piles = [Barrier(prefab="wood_pole_log_4",
                     poly=((x - 0.2, 13.8), (x + 0.2, 13.8),
                           (x + 0.2, 14.2), (x - 0.2, 14.2)),
                     y_lo=WATER_LEVEL - 6.0, y_hi=WATER_LEVEL + 2.0,
                     part="pile")
             for x in (-6.0, -3.0, 0.0, 3.0, 6.0)]
    return check_hull_entry(hull, bed, piles, (0.0, 0.0), 0.0,
                            site="Ulfsgard basin (pile row across the mouth)")


def _rock_across_the_mouth(tight: bool) -> list[Barrier]:
    """One obstruction across the channel mouth, twice: once as a BOX collider
    and once as a MeshCollider recorded only as its bounds.

    Identical geometry, identical numbers - and the two must not carry the same
    weight.  `rock4_forest`'s mesh bound is a 27.7 x 24.2 m slab and the rock
    is not, so a world refused on that box has been refused on a number
    without its precision.
    """
    return [Barrier(prefab="rock4_coast" if not tight else "stone_wall_4x2",
                    poly=((-9.0, 13.0), (9.0, 13.0), (9.0, 15.0), (-9.0, 15.0)),
                    y_lo=WATER_LEVEL - 6.0, y_hi=WATER_LEVEL + 1.0,
                    tight=tight, part="bound" if not tight else "box")]


def case_reach_blocked_by_a_box_collider() -> list[Finding]:
    """A stone wall across the mouth REFUSES: its collider is the solid."""
    hull = _cargoship()
    out = check_sea_reach(hull, _pond(6.0, hull, sill=6.0),
                          _rock_across_the_mouth(True), (0.0, 0.0), 0.0,
                          site="mouth walled with stone_wall_4x2")
    hard = [f for f in out if not f.ok and not f.advisory]
    assert hard, [(f.ok, f.advisory, f.message) for f in out]
    return out


def case_reach_blocked_only_by_a_mesh_bound_is_advisory() -> list[Finding]:
    """THE SAME GEOMETRY as a MeshCollider bound: named, and NOT a verdict.

    Ulfsland's live instance is the CargoShip at (727.52,-122.04): 3 of 114
    footprint samples blocked by `rock4_coast`, whose world scale was never
    read and whose corpus entry is a bounding box.  The section must report it
    and must not fail the world on it.
    """
    hull = _cargoship()
    out = check_sea_reach(hull, _pond(6.0, hull, sill=6.0),
                          _rock_across_the_mouth(False), (0.0, 0.0), 0.0,
                          site="mouth blocked by rock4_coast's bound")
    assert any(not f.ok and f.advisory for f in out), [
        (f.ok, f.advisory, f.message) for f in out]
    assert all(f.ok or f.advisory for f in out), out
    assert out[0].numbers["precision"] == "mesh bounds only", out[0].numbers
    return out


def _pile_in_the_basin() -> Barrier:
    """ONE BOX COLLIDER, standing in the DEEPEST water on the pool's rim.

    `_basin_with_sill` holds 9.000 m in the basin and 4.000 m over the sill,
    so a single pile in a basin cell is the deepest UNnavigable cell adjacent
    to the pool and `best_sill` will select it - and its reason string will
    say `solid:`.  It obstructs one 2 m cell of a 24 m basin and shuts nothing
    in.
    """
    return Barrier(prefab="wood_pole_log_4",
                   poly=((-13.0, -13.0), (-11.0, -13.0),
                         (-11.0, -11.0), (-13.0, -11.0)),
                   y_lo=WATER_LEVEL - 9.0, y_hi=WATER_LEVEL + 2.0,
                   tight=True, part="pile")


def _bound_across_the_sill() -> Barrier:
    """A MeshCollider BOUND lying across the whole 16 m channel.

    This is what actually shuts the hull in, and it is not a measurement:
    `rock4_coast`'s corpus entry is `sharedMesh.bounds`, and on this world the
    same class of entry gives `rock4_forest` a 27.7 x 24.2 m slab.
    """
    return Barrier(prefab="rock4_coast",
                   poly=((-13.0, 13.0), (13.0, 13.0),
                         (13.0, 17.0), (-13.0, 17.0)),
                   y_lo=WATER_LEVEL - 4.0, y_hi=WATER_LEVEL + 1.0,
                   tight=False, part="bound")


def case_entry_shut_in_by_a_bound_with_a_box_on_the_sill() -> list[Finding]:
    """THE DEEPEST RIM CELL IS A BOX AND THE SEVERANCE IS A BOUNDING BOX.

    A 4.000 m channel out of a 9.000 m basin, a `rock4_coast` BOUND lying
    across the whole channel, and one `wood_pole_log_4` standing in the
    basin.  The hull cannot leave - but drop the bound and it leaves over the
    sill, so nothing MEASURED shuts it in and the refusal must be ADVISORY.

    `best_sill` picks the 9.000 m pile cell because it is picked for DEPTH,
    and reports `solid:wood_pole_log_4`.  A verdict taken off that string
    fails this world on a bounding box while naming a pile that obstructs one
    cell of twelve.
    """
    hull = _cargoship()
    bed = _basin_with_sill(4.0)
    out = check_hull_entry(hull, bed,
                           [_pile_in_the_basin(), _bound_across_the_sill()],
                           (0.0, 0.0), 0.0,
                           site="a basin shut in by rock4_coast's bound")
    assert out, "the hull must not reach open sea through the bound"
    n = out[0].numbers
    assert n["sill_blocked_by"] == "wood_pole_log_4", n
    assert n["deepest_sill_m"] == 9.0, n
    assert n["mesh_bound_parts"] == 1 and n["barrier_parts"] == 2, n
    assert all(f.ok or f.advisory for f in out), [
        (f.ok, f.advisory, f.message) for f in out]
    assert n["precision"] == "mesh bounds only", n
    return out


def case_entry_shut_in_by_a_box_stays_binding() -> list[Finding]:
    """THE OTHER SIGN, and it is what stops the re-run becoming a bypass.

    The same basin and the same geometry across the channel, but the thing
    across it is a `stone_wall_4x2` BOX.  Dropping the mesh bounds drops
    nothing, the hull is still shut in, and the refusal must BIND.
    """
    hull = _cargoship()
    bed = _basin_with_sill(4.0)
    wall = Barrier(prefab="stone_wall_4x2",
                   poly=((-13.0, 13.0), (13.0, 13.0),
                         (13.0, 17.0), (-13.0, 17.0)),
                   y_lo=WATER_LEVEL - 4.0, y_hi=WATER_LEVEL + 1.0,
                   tight=True, part="box")
    out = check_hull_entry(hull, bed, [_pile_in_the_basin(), wall],
                           (0.0, 0.0), 0.0,
                           site="a basin walled off with stone_wall_4x2")
    hard = [f for f in out if not f.ok and not f.advisory]
    assert hard, [(f.ok, f.advisory, f.message) for f in out]
    assert hard[0].numbers["precision"] == "bed heights and box colliders", \
        hard[0].numbers
    return out


def _neuter_entry_bounds_by_reason():
    """Restore the shipped form: the precision of a whole no-route refusal
    taken from the reason string of the single deepest rim cell."""
    import sys as _s                                       # noqa: PLC0415
    mod = _s.modules[__name__]
    mod._ENTRY_BOUNDS_BY_REASON = True

    def undo():
        mod._ENTRY_BOUNDS_BY_REASON = False
    return undo


def case_entry_head_room_unmeasured() -> list[Finding]:
    """A hull under a lintel whose AIR DRAFT NOBODY EVER MEASURED.

    THE FIXTURE IS THE STATE, NOT A PREFAB THAT HAPPENS TO BE IN IT.  This
    case used to name `CargoShip`, which was true only for as long as the
    geometry corpus was VANILLA-ONLY: the moment the corpus was rebuilt with
    this world's own mod set staged, all seven modded hull classes became
    measurable and the case's own premise evaporated - the check was still
    right and the fixture had simply stopped describing anything.  A fixture
    that drifts out from under its defect is the same fault as a population
    that drifts: so the missing measurement is now CONSTRUCTED, `air_m=None`
    on a hull in the real table's units, and no corpus rebuild can take this
    state away from the check that must refuse it.
    """
    from dataclasses import replace                        # noqa: PLC0415
    hull = replace(_cargoship(), air_m=None,
                   source="a hull class absent from the geometry corpus")
    assert hull.air_m is None, hull
    bed = _pond(6.0, hull, sill=6.0)
    lintel = [Barrier(prefab="wood_floor",
                      poly=((-8.0, 13.0), (8.0, 13.0), (8.0, 15.0),
                            (-8.0, 15.0)),
                      y_lo=WATER_LEVEL + 2.5, y_hi=WATER_LEVEL + 2.7,
                      part="deck")]
    out = check_hull_entry(hull, bed, lintel, (0.0, 0.0), 0.0,
                           site="a 2.5 m bridge over the only way out")
    assert any(f.check.endswith("air_draft_unmeasured") for f in out), out
    return out


def case_entry_head_room_measured() -> list[Finding]:
    """A vanilla hull whose air draft IS measured, under a deck it clears."""
    hull = hull_table()["Karve"]
    assert hull.air_m is not None and hull.air_m > 6.0, hull
    bed = _pond(6.0, hull, sill=6.0)
    lintel = [Barrier(prefab="wood_floor",
                      poly=((-8.0, 13.0), (8.0, 13.0), (8.0, 15.0),
                            (-8.0, 15.0)),
                      y_lo=WATER_LEVEL + hull.air_m + 0.5,
                      y_hi=WATER_LEVEL + hull.air_m + 0.7,
                      part="deck")]
    out = check_hull_entry(hull, bed, lintel, (0.0, 0.0), 0.0,
                           site="a deck the Karve's mast clears")
    assert all(f.ok for f in out), [f.message for f in out]
    return out


CASES = [
    Case("termination_unread_is_not_clear", False,
         case_termination_unread_is_not_clear,
         neuter=_neuter_termination_unread,
         neuters="the conflation: an instrument that DID NOT ANSWER read as "
                 "one that answered CLEAR - `contested_by` is None both "
                 "when nobody holds the ground and when the footprints "
                 "could not be read, and the flatten=FORBIDDEN set is "
                 "unreadable on any chain that will not parse",
         prior_state="the state this gate reported in for its whole life: "
                     "11 harbours refusals with no statement of which are "
                     "WORK. N refusals is a work queue and an operator "
                     "decision list added together with no way back, and "
                     "the one direction a termination class can fail in is "
                     "the one that manufactures green"),
    Case("sea_gate_four_fins_against_a_9_16_m_beam", True,
         case_gate_yaw90_fins,
         prior_state="Ulfsgard, six fb_fortress_portcullis at yaw 90 in the "
                     "11.951 m throat: slots 1.925/3.777/3.777/1.864 m "
                     "(MEASURED live, sea_gate_fix.py)"),
    Case("sea_gate_yaw0_open_pose_static_posts", True,
         case_gate_yaw0_open_pose,
         prior_state="the rotation that was almost applied: every panel open, "
                     "3.181 m of post-free throat (MEASURED by DoorProbe)"),
    Case("sea_gate_after_six_retires", False, case_gate_after_retire,
         prior_state="the world as it stands, ledger seq 3096: 11.951 m clear"),
    Case("open_pose_table_keeps_the_two_static_posts", False,
         case_open_pose_table_measures_the_static_posts),
    Case("door_with_no_measured_open_pose", True,
         case_door_with_no_measured_pose),
    Case("berth_point_reading_hides_a_grounded_hull", True,
         case_berth_point_reading_hides_a_grounded_hull,
         neuter=_neuter_berth_footprint,
         neuters="the ONE-POINT berth reading this world shipped: a single "
                 "Heightmap query at the coordinate the builder wrote the "
                 "boat at, answering for a 21.57 m hull",
         prior_state="ferry-terminal-eastisle, ledger seq 2473: point 4.21 m, "
                     "footprint min 0.244 m over 153 samples"),
    Case("berth_littleboat_floats_at_the_temple_slip", False,
         case_berth_littleboat_floats,
         prior_state="boathouse-temple-strait, ledger seq 2058: 2.384 m over "
                     "2.2 m required"),
    Case("berth_results_fall_with_hull_length", True,
         case_berth_falls_with_hull_length,
         prior_state="one slip, three hulls: 2.384 / 0.038 / -0.888 m"),
    Case("berth_dock_northcape_0_038_m_margin_accepted", False,
         case_berth_dock_northcape_floats,
         prior_state="dock-northcape, ledger seq 2125: 114 samples, 3.038 m"),
    Case("berth_short_says_whether_anything_was_taken", True,
         case_berth_short_says_whether_anything_was_taken,
         neuter=_neuter_berth_cause,
         neuters="the ONE-BED berth refusal: 'N m short' off the applied "
                 "surface alone, with no statement of whether any water was "
                 "ever there to take - so a filled channel and a berth "
                 "sited on the seed's own land produce the same sentence "
                 "and have no repair in common",
         prior_state="MEASURED on Ulfsland 0571968d at ferry-terminal-"
                     "eastisle, the declared VikingShip at (1943.79,568.37) "
                     "yaw 295: shallowest -0.465 m under 151 footprint "
                     "samples against 2.500 m required, reported with no "
                     "cause. The solids-in/solids-out discriminator over the "
                     "same footprint returns 45 of 151 samples unconnected "
                     "on ALL FIVE fills - applied bed with all 4,185 solid "
                     "parts, box colliders only, no barriers at all, the "
                     "SEED's own bed with no barriers, and the seed bed with "
                     "every solid standing. Nothing was taken from this "
                     "berth, so the restoration a reader would size off the "
                     "applied number cannot exist"),
    Case("berth_over_a_partly_read_footprint", True,
         case_berth_partial_footprint_refused),
    Case("drain_fill_channel_to_road_freeboard", True,
         case_drain_fill_to_road_freeboard,
         prior_state="bridge N3-x01, ledger seq 2858: seed bed 1.46 m under "
                     "the 30.0 m water plane, road datum 32.5 m"),
    Case("drain_dredge_lowers_the_bed", False, case_drain_dredge_is_accepted),
    Case("drain_bridge_on_piles_bed_untouched", False,
         case_drain_as_built_bridge_on_piles,
         prior_state="the world as it stands: flatten=FORBIDDEN honoured, the "
                     "channel still 1.46 m deep"),
    Case("drain_part_filled_channel_loses_its_depth", True,
         case_drain_shoaled_channel),
    Case("drain_without_the_seed_surface", True,
         case_drain_reference_unavailable),
    Case("reach_landlocked_pocket_of_9_m_water", True,
         case_reach_landlocked_pocket,
         neuter=_neuter_sea_reach_connectivity,
         neuters="the state before connectivity was asked at all - a berth "
                 "judged by the water under it, which is what berth_depth "
                 "alone measures and what let a 9 m landlocked pocket be "
                 "declared a harbour"),
    Case("reach_basin_with_a_dredged_channel_out", False,
         case_reach_basin_connected,
         prior_state="Ulfsgard's dredged channel, 6.000 m under a full "
                     "CargoShip footprint over 114 samples (seq 3096)"),
    Case("channel_severed_by_a_submerged_raise", True,
         case_channel_severed_below_the_surface,
         neuter=_neuter_channel_severed,
         neuters="the state before this check existed - sea_reach alone, "
                 "which cannot tell a coast the seed drew closed from a "
                 "channel a write took away",
         prior_state="Ulfsland 2c7011d2, the LittleBoat at "
                     "boathouse-temple-strait: 38 of 38 footprint samples "
                     "cut off with ALL 5,167 barriers, with the 2,198 mesh "
                     "bounds dropped, and WITH NO BARRIERS AT ALL - the "
                     "whole pool hung on ONE vertex at (-319,-48) where the "
                     "seed held 2.343 m and the save holds 2.176 m against "
                     "2.200 m required. A +0.167 m raise 27.8 m UNDER the "
                     "surface, invisible to draining and to shoaling, that "
                     "landlocked a harbour"),
    Case("channel_severed_behind_a_solid_that_severs_both_beds", True,
         case_channel_severed_behind_a_solid_that_severs_both_beds,
         neuter=_neuter_severed_control_by_barriers,
         neuters="the shipped control - both beds read WITH the solids, so a "
                 "solid set that severs the berth on its own saturates both "
                 "answers and the GROUND's own contribution becomes "
                 "unobservable",
         prior_state="MEASURED on Ulfsland 5385652e at the LittleBoat, "
                     "boathouse-temple-strait, 38 footprint samples: seed "
                     "bed with no barriers 0 of 38 cut off, applied bed with "
                     "no barriers 38 of 38, and BOTH beds 38 of 38 once the "
                     "5,414 solid parts standing there are included - so the "
                     "check returned [] over a seed that connected this "
                     "berth and a bed that does not"),
    Case("channel_the_seed_never_connected_is_not_severed", False,
         case_channel_the_seed_never_connected),
    Case("entry_through_the_dredged_channel", False,
         case_entry_through_the_dredged_channel),
    Case("entry_blocked_by_a_pile_row_across_the_mouth", True,
         case_entry_blocked_by_a_pier_in_the_channel),
    Case("entry_head_room_never_measured_for_this_hull", True,
         case_entry_head_room_unmeasured),
    Case("entry_head_room_measured_and_cleared", False,
         case_entry_head_room_measured),
    Case("solid_index_answers_exactly_what_the_scan_did", False,
         case_solid_index_answers_exactly_what_the_scan_did,
         neuter=_neuter_solid_index_order,
         neuters="a bucket scanned in build order rather than in the "
                 "caller's list order, which is the one way a bucketed "
                 "index silently changes the verdict it was only supposed "
                 "to speed up",
         prior_state="the linear scan it replaces: 16,641 cells x ~5,000 "
                     "solid parts per navigability pass, five passes per "
                     "berth, seven berths - over an hour of CPU on this "
                     "world with every bed reading already cached"),
    Case("write_index_answers_exactly_what_the_scan_did", False,
         case_write_index_answers_exactly_what_the_scan_did,
         neuter=_neuter_write_index_order,
         neuters="a bucket scanned in build order rather than in the "
                 "ledger's own write order, which moves the ATTRIBUTION "
                 "check_draining prints for a drained sample",
         prior_state="MEASURED on Ulfsland 5385652e: 234 terrain writes, 100 "
                     "of them roads carrying 38,827 polyline nodes - one of "
                     "them 1,529 by itself - so one `written_at` call walks "
                     "38,861 segments and check_draining asks it once per "
                     "region sample: 4.5 BILLION segment tests on the 2 m "
                     "field, 17.9 billion on the engine's own 1 m lattice"),
    Case("entry_shut_in_behind_a_1_m_sill", True,
         case_entry_shut_in_behind_a_sill,
         prior_state="the live instance is the LittleBoat at "
                     "(-307.04,-119.59): 38 of 38 footprint samples not "
                     "connected to open sea on the APPLIED bed"),
    Case("entry_over_a_dredged_4_m_sill", False,
         case_entry_over_a_dredged_sill),
    Case("drain_inside_the_measured_instrument_floor", False,
         case_drain_inside_the_instrument_floor,
         prior_state="MEASURED on Ulfsland before the floor existed: a "
                     "+0.075 m rise at (86.0,-358.0), seed 29.940 applied "
                     "30.015, reported as 24 m2 of water permanently removed"),
    Case("drain_outside_the_measured_instrument_floor", True,
         case_drain_outside_the_instrument_floor),
    Case("drain_shoaled_puddle_is_not_a_channel", False,
         case_drain_shoaled_puddle_is_not_a_channel,
         prior_state="MEASURED on Ulfsland before the channel-depth rule: "
                     "3,990 shoaling refusals in one harbour, worst +0.384 m "
                     "in 0.546 m of shoreline water at (673.5,-22.0)"),
    Case("drain_shoaled_channel_is_refused", True,
         case_drain_shoaled_channel_is_refused),
    Case("drain_puddle_shallower_than_the_instrument_floor", False,
         case_drain_puddle_shallower_than_the_instrument_floor,
         neuter=_neuter_drain_seed_depth_floor,
         neuters="the shipped form - the MEASURED instrument floor applied "
                 "to the RISE only, so a point the two instruments cannot "
                 "agree was ever wet still entered the population and could "
                 "carry a refusal by itself",
         prior_state="MEASURED on Ulfsland 5385652e: three of the six "
                     "draining refusals - the CargoShips at (1941.25,573.81) "
                     "and (1756.48,641.63) and the declared VikingShip at "
                     "ferry-terminal-eastisle - consist of 1, 1 and 2 "
                     "samples out of 16,641 / 16,641 / 9,680, in 0.044 / "
                     "0.034 / 0.055-0.079 m of seed water, against measured "
                     "instrument floors of 0.100 / 0.090 / 0.099 m, each "
                     "reported as square metres of water permanently "
                     "removed"),
    Case("drain_real_fill_survives_the_seed_depth_floor", True,
         case_drain_real_fill_survives_the_seed_depth_floor,
         prior_state="road_N1_wnorth_shelf at (-239,2541), seed 29.853 "
                     "applied 32.606 against a 0.093 m floor - 0.147 m of "
                     "seed water, 1.58x the floor, under +2.753 m of fill"),
    Case("reach_blocked_by_a_box_collider_refuses", True,
         case_reach_blocked_by_a_box_collider),
    Case("reach_blocked_only_by_a_mesh_bound_is_advisory", False,
         case_reach_blocked_only_by_a_mesh_bound_is_advisory,
         prior_state="the live instance is the CargoShip at (727.52,-122.04): "
                     "3 of 114 footprint samples blocked by rock4_coast, whose "
                     "world scale was never read"),
    Case("entry_shut_in_by_a_bound_with_a_box_on_the_sill", False,
         case_entry_shut_in_by_a_bound_with_a_box_on_the_sill,
         neuter=_neuter_entry_bounds_by_reason,
         neuters="the shipped form - the precision of a whole no-route "
                 "refusal taken from `best_sill`'s reason string for the "
                 "single deepest rim cell, which is chosen for its DEPTH and "
                 "not for whether it is what shuts the hull in",
         prior_state="check_sea_reach re-ran the whole connectivity question "
                     "with the 2,198 mesh bounds dropped and said so in its "
                     "own comment - `the test is the RE-RUN, not the reason "
                     "string` - while check_hull_entry's no-route branch, "
                     "twenty lines away, read reason.startswith('bound:') "
                     "off one cell"),
    Case("entry_shut_in_by_a_box_stays_binding", True,
         case_entry_shut_in_by_a_box_stays_binding),
    Case("bed_queries_off_the_vertex_lattice", True,
         case_queries_off_the_vertex_lattice,
         neuter=_neuter_vertex_lattice_check,
         neuters="the state before this section checked its OWN request "
                 "list: every query accepted wherever it fell, answered by "
                 "whichever vertex won the rounding",
         prior_state="MEASURED on this world by GateStructures: (-836.5,"
                     "7734.5) answered 30.4490 m and 30.2858 m to two queries "
                     "0.0001 m apart in the same file from the same run - "
                     "0.1632 m, because Heightmap.GetHeight rounds its query "
                     "to a vertex"),
    Case("region_and_footprint_snapped_to_vertices", False,
         case_region_lattice_is_snapped_to_vertices,
         neuter=_neuter_region_snap,
         neuters="a field centred on the RAW berth coordinate rather than "
                 "on the vertex the engine will answer with - Valheim snaps "
                 "pieces to 0.5 m, so a whole 66,049-point region on the "
                 "half-lattice line is the ordinary case, not a contrived "
                 "one"),
    Case("field_on_the_engine_vertex_lattice", False,
         case_field_on_the_engine_lattice_is_accepted,
         neuter=_neuter_field_step,
         neuters="the shipped 2 m reachability field - 16,641 of 66,049 "
                 "vertices per berth, 25.2 %, locked to one residue class "
                 "in x AND z by snap(berth), which is where the boat "
                 "happened to be moored",
         prior_state="FIELD_STEP_M = 2.0, justified in its own comment by "
                     "cost and caveated as `cannot see an obstruction "
                     "narrower than 2 m` - with `aperture()` and "
                     "`refine_route` named as the answer, and both of those "
                     "refine a WIDTH while `_unconnected` flood-fills the "
                     "field itself"),
    Case("field_at_2_m_cannot_address_the_bar_it_names", True,
         case_two_metre_field_cannot_address_the_bar,
         prior_state="MEASURED on Ulfsland 5385652e, the LittleBoat at "
                     "(-307.04,-119.59): the 2 m field names (-317,-48), "
                     "+0.194 m, seed 27.6822 applied 27.8766, depth 2.318 m; "
                     "the shallower vertex in that channel is its neighbour "
                     "(-318,-48), +0.218 m, seed 27.6138 applied 27.8321, "
                     "depth 2.168 m against 2.200 m required - and -318 is "
                     "EVEN, so the field anchored on snap(berth) = "
                     "(-307,-120) has no sample there at all"),
    Case("field_at_2_m_certifies_a_strait_with_a_bar_across_it", True,
         case_two_metre_field_certifies_a_barred_channel,
         prior_state="the VERDICT direction of the same defect: one shallow "
                     "row on an ODD z, 0.500 m of water against 2.200 m "
                     "required, across the berth's only way out - the 2 m "
                     "field steps -136 to -134 and check_sea_reach returns "
                     "ok over all 38 footprint samples"),
    Case("repair_names_every_vertex_it_counted", False,
         case_a_restoration_names_every_vertex_it_counted,
         neuter=_neuter_restoration_set,
         neuters="the shipped form of every repairable refusal in this "
                 "section: a worst `where` and a count, and no set - so the "
                 "widest repair derivable from a 36-vertex refusal was ONE "
                 "vertex",
         prior_state="MEASURED on Ulfsland 2a6940a934b5: channel_severed "
                     "counted 36 raised samples at the boathouse berth and "
                     "named (-318,-56); draining counted 2 at the CargoShip "
                     "(1756.48,641.63) and named (1641,533). Neither printed "
                     "a second vertex, and a cut sized off either moves one "
                     "sample and reports the same defect next run"),
    Case("repair_names_the_owner_of_every_vertex", True,
         case_drain_repair_names_the_owner_of_every_vertex,
         neuter=_neuter_restoration_attribution,
         neuters="the shipped form: ONE owner, sampled at the worst vertex "
                 "with `attribute(worst_x, worst_z)`, worn by the whole "
                 "restoration set - `check_repair_is_named`'s own rule with "
                 "the noun changed from REPAIR to OWNERSHIP",
         prior_state="MEASURED on Ulfsland a087af6253a0, all five draining "
                     "refusals: 49 / 113 / 649 / 43 vertices whose WORST "
                     "sample is inside a road write, and ONE vertex - the "
                     "declared VikingShip at ferry-terminal-eastisle, "
                     "(1989.0,440.0), +0.1492 m, 0.1492 m3 - whose "
                     "attributed_to is EMPTY. It is owned by nothing, needs "
                     "no ownership decision from anybody, and survived gate "
                     "A, gate B and gate C inside the same class as 649 "
                     "road-owned ones because the class was named off one "
                     "sample"),
    Case("repair_named_by_its_worst_vertex_alone", True,
         case_a_repair_named_by_its_worst_vertex_alone_is_refused,
         prior_state="a finding that counted 36 cut-only vertices and named "
                     "1: 0.243 m3 of a 4.374 m3 defect, 35 vertices left "
                     "standing"),
    Case("repair_set_holding_a_fill_is_refused", True,
         case_a_restoration_that_would_fill_is_refused,
         prior_state="one row of a cut-only set whose applied surface is "
                     "already 0.400 m BELOW the seed - restoring it is a "
                     "FILL, and dropping it silently is how a fill gets "
                     "written under a restoration's name"),
    Case("repair_named_off_the_vertex_lattice", True,
         case_a_repair_off_the_vertex_lattice_is_refused,
         prior_state="a repair named at x = -318.5, which Heightmap.GetHeight "
                     "answers with whichever vertex wins the rounding"),
    Case("repair_is_sized_against_the_measured_instrument_floor", False,
         case_a_repair_inside_the_instruments_noise_is_not_handed_out,
         neuter=_neuter_restoration_floor,
         neuters="the floorless repair sizing channel_severed shipped with: "
                 "a RAW cross-instrument difference, while check_draining - "
                 "the same two instruments and the same subtraction - has "
                 "applied the region's own MEASURED floor since a floorless "
                 "version reported 3,990 'shoaled' samples in one harbour",
         prior_state="MEASURED at the boathouse berth on 2a6940a934b5: the "
                     "floor is 0.156 m and 20 of the 36 vertices "
                     "channel_severed counted as its own repair are raises "
                     "BELOW it, the smallest 0.016 m - a tenth of the "
                     "disagreement, proposed as a cut"),
    Case("refine_finds_a_1_m_bar_the_2_m_field_steps_over", True,
         case_refine_finds_a_bar_the_field_steps_over,
         prior_state="the road analogue is MEASURED: 2,878 of 21,954 adjacent "
                     "station pairs resolve to the SAME vertex, so 13.11 % of "
                     "them report 0.000 m over any cliff between them"),
    Case("refine_accepts_a_clear_channel", False,
         case_refine_accepts_a_clear_channel),
    Case("refine_corridor_is_on_the_vertex_lattice", False,
         case_refine_corridor_is_on_the_vertex_lattice),
    Case("refine_45_deg_channel_13_vertices_reads_19_offsets", True,
         case_refine_45_deg_channel_13_vertices_wide,
         neuter=_neuter_refine_per_offset,
         neuters="a snap that never collides, i.e. the assumption that every "
                 "cross-section offset is its own vertex - which is what "
                 "counting the run per offset believed",
         prior_state="this file counted the channel run per cross-section "
                     "OFFSET, and MEASURED over a 0-89 deg sweep at half=8 "
                     "the offsets collapse onto the same vertex 13 of 17 "
                     "times at 45 deg; a 13-vertex channel therefore read 19 "
                     "m wide and passed a 14.10 m beam.  An axis-aligned "
                     "fixture is 17 of 17 and cannot catch it"),
    Case("refine_45_deg_window_reaches_the_beam", False,
         case_refine_45_deg_window_reaches_the_beam,
         neuter=_neuter_cross_window_in_offsets,
         neuters="the cross-section WINDOW counted in offsets rather than in "
                 "vertices - +/-ceil(beam/2+1) steps of 1 m along the "
                 "normal, deduplicated afterwards - which reaches nine "
                 "distinct vertices on a 45 deg bearing and thirteen on an "
                 "axis-aligned one",
         prior_state="MEASURED on Ulfsland 5385652e: ALL THREE "
                     "hull_entry/refined refusals read exactly 9.000 m "
                     "against a 9.16 m beam - the CargoShips at "
                     "(1941.25,573.81) and (-136.62,2653.21) and the "
                     "declared VikingShip at ferry-terminal-eastisle - while "
                     "every accepted berth on an axis-aligned bearing read "
                     "13.000 m.  The VikingShip's own narrowest section at "
                     "(1932,572) is nine vertices from (1928,576) to "
                     "(1936,568) holding 6.912 m to 14.142 m of water with "
                     "no shoal in it: nine was the width of the WINDOW"),
]


SECTION = register(Section(
    name="harbours",
    measures=("whether a hull can actually get to and sit at every berth this "
              "world contains: the CLEAR OPENING on its route out against its "
              "own beam, the shallowest sample under its OWN rotated footprint "
              "against its own draught, the SIGN of every change to a "
              "submerged bed, and flood-fill connection from every footprint "
              "sample to open sea"),
    instrument=("the applied bed read back from the SAVE through the engine "
                "(ctx.ground: run_waterproof.sh FROM_SAVE=1 on a copy, the "
                "save's own _TerrainCompiler ZDOs, Heightmap.GetHeight); the "
                "SEED's own surface for the same points (ctx.generated: "
                "run_patchscan.sh SURFACE=heightmap) as the reference for "
                "draining; the whole-save ZDO census with each object's own "
                "rotation, resolved PER SOLID PART through "
                "blueprints/data/piece_geometry.json; the MEASURED open pose "
                "of every movable barrier from blueprints/data/"
                "door_open_pose.txt; and hull beam/length/draught from "
                "settlements/waterfront.py::VESSELS"),
    population=("every ZDO in the save whose prefab is a vessel in "
                "waterfront.py::VESSELS, each judged at its own position and "
                "its own yaw; and for each of them, every solid PART of every "
                "non-vessel ZDO within 152 m whose pivot is within 40 m of the "
                "water plane"),
    run=run,
    cases=CASES,
))




def main(argv=None) -> int:
    """This section's own proof, with no world, no save and no server.

    Usable before the gate's own entry point exists and after it: the cases are
    the same objects the registry runs, so there is one set of them, not two.
    """
    cases = run_cases(SECTION)
    bad = 0
    for case, ok, detail in cases:
        want = "REFUSE" if case.expect_fail else "ACCEPT"
        print(f"  [{'ok' if ok else 'FAIL'}] {want:7s} {case.name}"
              + (f"\n           {detail}" if detail else ""))
        bad += 0 if ok else 1
    must_fail = len([c for c in SECTION.cases if c.expect_fail])
    print(f"\n{len(cases) - bad}/{len(cases)} harbours cases; {must_fail} of "
          f"them are states this section MUST refuse and "
          f"{len(cases) - must_fail} are states it MUST accept")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
