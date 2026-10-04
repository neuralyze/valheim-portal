#!/usr/bin/env python3
"""STRUCTURES - a building is not finished when it exists, only when it stands
on the ground the engine draws, over the samples its own pad wrote, under a
skin that is actually over it, the right way up, once.

WHY THIS SECTION EXISTS.  Five defect classes in this world were each declared
correct by a check that could not have failed:

  "grounding census: 0 floating"        -> a 212-piece watchtower standing in
      3.173-13.542 m of air over 40 columns.  FOUR censuses said zero, because
      each compared a body to THE DATUM ITS OWN PAD WAS LEVELLED TO.  The pad
      datum is a number the write chose.  It is not the ground.
  "pad flat"                            -> pads asked to be dead flat that
      sloped 6.593 / 3.478 / 1.495 / 0.939 m.
  "roof closed"                         -> 48 of 96 deck samples open to the
      sky at the portal hall.  A box cannot tell a gable from an X: two panels
      leaning apart have the same axis-aligned bounding volume as two panels
      leaning together.
  "349/349 at tolerance 0"              -> 385 inverted roof panels in 74
      bodies.  A 180 degree yaw flip moves neither the position nor the
      footprint nor the AABB.  Count, position-at-tolerance-0 and even the
      plane-coverage test of this file all return IDENTICAL numbers before and
      after.  The ONLY discriminator is the SIGNED gradient toward the ridge.
  "objects_count: expected"             -> 52 co-located pieces at the
      Ulfsgard boathouse.  `objects_count` is an XZ CYLINDER WITH NO HEIGHT
      BOUND, so it reads a legitimate stack of floors as a duplicate and a
      genuine duplicate as a legitimate stack.  A CUBE tells them apart.

The four rules that follow from that, and that every number below obeys:

  1. THE REFERENCE IS NEVER THE ARTEFACT UNDER TEST.  Grounding and flatness
     are measured against `Heightmap.GetHeight` read back out of the SAVE
     through `run_waterproof.sh FROM_SAVE=1`, which instantiates the save's
     own `_TerrainCompiler` ZDOs.  Not the ledger's heights.  Not `target_y`.
     Not the datum the write chose.  `target_y` appears here exactly once, as
     the CLAIM a pad is held to - never as a measurement of it.
  2. THE POPULATION IS THE ONE THE WRITE ITSELF NAMES.  A pad's flatness is
     measured over the lattice samples that pad's OWN TerrainComp write set
     and still owns, recovered by differencing its blobs against the previous
     state of the same zones.  Not a rectangle: a 1.634 m figure at one site
     was the pad EDGE - unwritten natural ground outside the rect - 36x larger
     than the real error.
  3. THE SOLID IS THE PIECE'S OWN SOLID, ROTATED.  Every vertical question
     here is answered by intersecting the column at (x, z) with the piece's
     own rotated parallelepiped, not with its bounding box.  MEASURED: all
     11,726 parts in `blueprints/data/piece_geometry.json` are parallelepipeds
     to within 2.0e-4 m in the basis (v1-v0, v2-v0, v4-v0), so this is exact
     for the whole corpus and not an approximation of it.
  4. A SIGN IS NOT A COUNT.  Orientation is `dot(this panel's own rise
     direction, the direction to its body's ridge)`, which is the one quantity
     the 180 degree flip changes.

WHAT IT NEVER DOES: no write, no token, no deletion.  It is a read.  A body it
cannot measure is REPORTED as unmeasured; it is never assumed harmless, which
is the shape of all five defects above.
"""
from __future__ import annotations

import collections
import gzip
import json
import math
import re
import struct
import sys

from .. import instruments as I
from ..registry import Case, Finding, Section, SectionResult, register

# ---------------------------------------------------------------------------
# MEASURED CONSTANTS.  Each names where it was read from.  Nothing here is a
# round number somebody liked.
# ---------------------------------------------------------------------------
# tan(38 deg) = 0.78129 m per 1 m.  Character::c_MinSlideDegreesPlayer.  There
# is no step-height constant in Valheim, so the slide angle is the entire
# limit, and it is the limit used for "is this body standing on anything":
# a body whose every piece clears the ground beneath it by MORE than the
# largest step the terrain itself is allowed to take is standing in air.
SUPPORT_LIMIT_M = I.STEP_LIMIT_PER_M

# The clean-site signature.  `stone_floor_2x2` is a box from y=-0.5 to y=+0.5
# about its own pivot, so a slab laid with its TOP at pad level has its bottom
# exactly 1.000 m under the applied surface.  "Worst burial 1 m" on a site
# floored with stone slabs is not a defect, it is the pivot.  The burial check
# therefore asks whether a piece is COMPLETELY under the surface - ymax below
# the ground - which no correctly laid slab ever is, instead of comparing its
# bottom to a tolerance that would have to be 1.0 m to let the slab through
# and would then hide a metre of real burial.
BURIED_TOL_M = 0.05
# THE SHIPPED `buried` ARITHMETIC, off, and reachable so a selftest case can
# put it back.  See `check_grounding` and
# `_case_a_piece_that_spans_height_is_not_buried_by_one_column`.
_BURIED_PER_COLUMN = False
# THE TWO SHIPPED `roof_skin` DEFECTS, off, and reachable so selftest cases
# can put them back.  Both are states this world was measurably in: the rise
# standing in for the thickness let a hole exactly one course deep through as
# "one panel thick", and a MAX-ONLY height field refused 24 of 27 bodies for
# the edges of ridge boards standing on a skin that carries on underneath.
_SKIN_ALLOW_USES_RISE = False
_SKIN_MAX_ONLY_LAYERS = False
_SKIN_NO_SPAN_TEST = False
# THE THREE SHIPPED `pad_flatness` EMPTY-POPULATION DEFECTS, off, and
# reachable so selftest cases can put them back.  `_PAD_EMPTY_BLANKET` is the
# state this section shipped in - one "unmeasured" sentence over four
# different states, seven times on this chain.  The other two are the shapes
# the replacement could most easily have become: an escape that rests on
# BOOKKEEPING (somebody owns it) rather than on a current record's declared
# rectangle, and an unreadable sample counted as if it read the target.
_PAD_EMPTY_BLANKET = False
_PAD_ANSWER_BY_OWNERSHIP = False
_PAD_UNREAD_IS_TARGET = False
# THE `orphan_plan` RETIRE DEFECTS, off, and reachable so selftest cases can
# put them back.  `_RETIRE_NETTING_OFF` is the state this section shipped in:
# `ledger_anchors` read what was PLANTED and never what the same chain later
# PULLED OUT, so a structure removed on purpose was an orphan forever.  The
# other two are the shapes the fix could most easily have become - excusing a
# whole plan because SOMETHING of its site was retired, and excusing it on a
# retire that never says what it undoes.
_RETIRE_NETTING_OFF = False
_RETIRE_BY_SITE = False
_RETIRE_WITHOUT_CITATION = False

# Co-location tolerance for the duplicate cube, in METRES, CHEBYSHEV, ON ALL
# THREE AXES.  MEASURED at the known-bad fixture (Ulfsgard boathouse, 748
# placed build pieces in the 60x60 m box around 1090,700): the duplicate count
# is a flat 52 for every tolerance from 0.001 m to 0.05 m, becomes 53 at
# 0.10 m and 56 at 0.25 m.  0.05 m is the top of the stable interval - wide
# enough that a float round-trip cannot hide a duplicate, narrow enough that
# the nearest legitimately distinct same-prefab pair at that site (0.10 m) is
# not swept in.
DUP_TOL_M = 0.05

# WHEN TWO PIECES SHARE A PIVOT, WHAT MAKES ONE OF THEM A DUPLICATE.
#
# Sharing a pivot is NOT the same thing as being a copy, and grading it as
# one refused 417 pieces of correct architecture on this world.  Two
# examples, both MEASURED here:
#
#   THE VALLEY.  The Ulfsgard boathouse is TWO gables that meet at x=1080.5:
#   ridges at 1073/1074.5 and 1086.5/1088 (top y 39.076), outer eaves at
#   1067 and 1094 (y 34.60), and one SHARED eave at 1080.5 (y 34.60) on each
#   of the 13 rows z=685..709.  Each gable's own last panel lands on that
#   pivot, one yawed 90 and one 270, and they cross in section.  They are
#   not two copies of one panel; they are two roofs meeting.  Deleting
#   either opens a 1.88 x 2 m hole in one of the two roof planes, 13 times
#   over - 48.9 m2 of roof, on a building nobody asked to be demolished.
#
#   THE HALF-ARCH.  `stone_arch` and `Piece_grausten_pillar_arch_small` are
#   half-arches that spring from one pillar; a full arch is two of them on
#   one pivot at opposite yaw.  339 pairs on this world.
#
# So the discriminator is OCCUPANCY, not the pivot and not the quaternion: a
# piece is a copy when its own solid is already filled by the pieces that
# share its pivot - remove it and nothing moves, nothing opens, nobody can
# tell.  Measured as the fraction of a 4x4x4 INTERIOR probe grid of each of
# the piece's own parts that lies inside another member's solid.
#
# THE THRESHOLD SITS IN AN EMPTY INTERVAL 0.48 WIDE.  MEASURED over all 908
# differently-posed co-located members on Ulfsland, against a 0.08 m voxel
# integration of the same overlap: 820 members read cheap-fraction <= 0.50
# (true overlap 0.00-0.60) and 88 read EXACTLY 1.00 (true overlap
# 0.96-1.00).  Nothing lands between 0.50 and 1.00.
#
# The grid is INTERIOR (0.125, 0.375, 0.625, 0.875) and not the corners: a
# corner of one box sits ON the face of the box that contains it, and a
# boundary test there answers on a coin-flip.  With corners included the two
# populations OVERLAP (0.333 vs 0.519) and the discriminator is gone.
DUP_BURIED_FRACTION = 0.98
DUP_PROBE_LEVELS = (0.125, 0.375, 0.625, 0.875)

# How far a ledger anchor may fall outside a body's own footprint and still be
# that body's anchor.  TASTE, and stated as such: it is not a threshold on a
# measurement, it only decides which bodies this section claims as BUILT BY
# THIS RUN rather than generated by the world.  The body's own extent does the
# real work; 8 m only covers an anchor placed at a doorway or a jetty head.
ANCHOR_MARGIN_M = 8.0

# A body of one or two pieces is a bench, a sign post or a single beam.  The
# grounding question - "is this BUILDING standing on anything" - is about
# bodies, and a two-piece body has no interior to be wrong about.  Single
# pieces are still counted and reported; they just do not carry the verdict.
MIN_BODY_PIECES = 3

# WHEN A PLAN'S OWN COMMANDS ARE NOT A BUILDING.
#
# `orphan_plan` asks "does a body of MIN_BODY_PIECES+ stand within
# ANCHOR_MARGIN_M of this plan's anchor".  That is a question about a
# BUILDING, and it was being asked of every plan - including the ones whose
# own commands are a SCATTER: single torches 30 m apart down a road shoulder,
# which cannot cluster into a body no matter how faithfully they were placed.
#
# MEASURED on Ulfsland, before any repair: 25 of the 27 orphan_plan advisories
# were `road_lighting`, and they were EVERY road_lighting record on the chain -
# lighting-N0 N0b N1 N2 N3 N4 R1 R2 R3 R4 S1 S12 S7 S8 T1 T3 T4 T5 T8 T9 T10
# T12 T13 W5 W10, 25 of 25, without exception, and the count rose to 41 of 41
# when 16 more lighting plans were written.  A check that has never once been
# able to pass on a role is not measuring that role, it is counting it - and
# the noise hid the two findings that were real (a castle and a town body).
#
# THE DISCRIMINATOR IS THE PLAN'S OWN COMMANDED POSITIONS, not its role and
# not its piece count: a plan is judged against a body only if ITS OWN
# COMMANDS could make one, i.e. at least MIN_BODY_PIECES of them lie within
# this distance of each other by single linkage.  A plan declaring 5,553
# pieces in one place is still judged - and a demolished one still refused -
# because its commands cluster; a plan declaring 5 pieces 30 m apart is not,
# because "no body stands here" is not a statement about it.  The distance is
# the section's OWN reach, so the rule cannot disagree with the check it
# guards: two commanded pieces further apart than the margin this check
# searches could never be read as one body by it.
PLAN_CLUSTER_M = ANCHOR_MARGIN_M

_HEIGHTMAP_W = 65        # Heightmap lattice, 65x65 vertices on a 64 m zone


# ---------------------------------------------------------------------------
# THE COLUMN - the one geometric primitive all five checks share
# ---------------------------------------------------------------------------
# "Is there material above this point, and where does this piece's underside
# sit here" are the same question asked twice, and both are wrong if asked of
# an axis-aligned bounding box.  Two roof panels leaning APART (an X) and two
# leaning TOGETHER (a gable) have the SAME bounding volume; the box says both
# are closed and only one of them is.  So the column at (x, z) is intersected
# with the piece's actual rotated solid.
#
# Every part in the corpus is a parallelepiped: 8 vertices, with edges
# (v1-v0, v2-v0, v4-v0).  For a `b` box that is exact by construction; for a
# `p` prism it was MEASURED over all 2,960 `p` parts, worst residual
# 2.0e-4 m, which is the corpus' own 4-decimal rounding.  So the intersection
# is a ray/OBB slab clip in that basis - exact, and cheap enough to run per
# deck sample.
_BASIS_CACHE: dict[int, tuple | None] = {}


def _basis(part: list):
    key = id(part)
    got = _BASIS_CACHE.get(key, 0)
    if got != 0:
        return got
    vs = I.part_vertices(part)
    out = None
    if len(vs) == 8:
        o = vs[0]
        e = [tuple(vs[i][k] - o[k] for k in range(3)) for i in (1, 2, 4)]
        out = (o, e)
    _BASIS_CACHE[key] = out
    return out


def _inv3(m):
    """Inverse of a 3x3 given as three COLUMN vectors, or None if singular."""
    (a, d, g), (b, e, h), (c, f, i) = m
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(det) < 1e-12:
        return None
    r = 1.0 / det
    return ((e * i - f * h) * r, (c * h - b * i) * r, (b * f - c * e) * r,
            (f * g - d * i) * r, (a * i - c * g) * r, (c * d - a * f) * r,
            (d * h - e * g) * r, (b * g - a * h) * r, (a * e - b * d) * r)


# A DECK IS WHAT A BUILDER BUILDS TO STAND ON, NOT EVERY LEVEL TOP.
#
# `deck_columns` used to take the highest level top in a column from ANY
# build piece, so the top of a machine was a floor.  MEASURED on
# complete-station-hub (`hs_ashlands_20999_valheimian`): its one "hole in
# the roof" is local (9.5, 15.5), where the "deck" is the 6.7 m top of the
# `blastfurnace` at (9.46, 1.5, 15.2) and the sky above it is that
# furnace's own open 2 x 2 m chimney (`Piece_grausten_wall_1x2` shaft,
# 10.7-14.7 m).  Drive 2 emitted the body only because the artisan
# station's 200 m trigger capsule was counted as roof over it; once the
# corpus stopped reading trigger volumes the emitter refused the body.
#
# WHICH PIECES ARE BUILT TO BE STOOD ON IS MEASURED, NOT LISTED.
# `crossings/data/piece_material.tsv` is `PieceMaterial.cs`'s dump of the
# real serialised `Piece` component of all 4,644 vanilla prefabs, and its
# `category` column is `Piece.m_category`.  The dump itself says what each
# value holds: 2 is every workbench-built floor, wall, beam, pole, stair
# and roof (`wood_floor`, `woodwall`, `wood_beam`, `darkwood_roof`,
# `wood_stair`), 3 every stonecutter-built one (`stone_floor_2x2`,
# `Piece_grausten_floor_4x4`, `blackmarble_floor`, `iron_floor_1x1_v2`);
# 1 is the crafting stations (`blastfurnace`, `smelter`, `charcoal_kiln`,
# `forge`, `piece_workbench`, `piece_artisanstation`), 4 the furniture
# (`piece_table`, `piece_chest_wood`, `piece_bed02`,
# `piece_brazierfloor01`), 0 the fires and odds (`hearth`, `fire_pit`,
# `bonfire`, `piece_cartographytable`).  A deck is the level top of a piece
# in a BUILDING category.  A prefab the dump does not hold (a mod's piece)
# keeps the old rule and is still a deck - the rule only ever removes a
# top the component data positively classes as something else.
PIECE_MATERIAL_TSV = (I.JUMPSTART / "crossings" / "data"
                      / "piece_material.tsv")
DECK_CATEGORIES = frozenset((2, 3))
# The shipped deck, restorable by a neuter: every level top is a floor.
_EVERY_LEVEL_TOP_IS_A_DECK = False
_PIECE_CATEGORIES: dict[str, int] = {}


def piece_categories() -> dict[str, int]:
    """prefab -> `Piece.m_category`, off the measured component dump, read
    once per process."""
    if not _PIECE_CATEGORIES:
        import csv                                         # noqa: PLC0415
        with open(PIECE_MATERIAL_TSV, newline="") as fh:
            for r in csv.DictReader(fh, delimiter="\t"):
                if r.get("piece") == "1" and r.get("category", "na") != "na":
                    _PIECE_CATEGORIES[r["name"]] = int(r["category"])
    return _PIECE_CATEGORIES


class Columns:
    """Per-solid column geometry, built once and reused by every check.

    `categories` is `piece_categories()` unless a caller (a selftest
    fixture) supplies its own: prefab -> `Piece.m_category`."""

    def __init__(self, geom: dict[str, list],
                 categories: dict[str, int] | None = None):
        self.geom = geom
        self.categories = (piece_categories() if categories is None
                           else categories)
        self._cache: dict[int, list] = {}
        self.degenerate = 0

    def is_deck(self, name: str) -> bool:
        """Is this prefab's level top a floor a builder built to stand on?
        See `DECK_CATEGORIES`."""
        c = self.categories.get(name)
        return (_EVERY_LEVEL_TOP_IS_A_DECK or c is None
                or c in DECK_CATEGORIES)

    def _prep(self, s) -> list:
        got = self._cache.get(id(s))
        if got is not None:
            return got
        out = []
        for part in self.geom.get(s.name) or ():
            b = _basis(part)
            if b is None:
                continue
            o, e = b
            ow = I.rotate(s.quat, o)
            ow = (s.pos[0] + ow[0], s.pos[1] + ow[1], s.pos[2] + ow[2])
            cols = [I.rotate(s.quat, v) for v in e]
            inv = _inv3(cols)
            if inv is None:
                self.degenerate += 1
                continue
            out.append((ow, inv))
        self._cache[id(s)] = out
        return out

    def span(self, s, x: float, z: float) -> tuple[float, float] | None:
        """The y-interval of this piece's own solid on the vertical line at
        (x, z), or None if the column misses the piece entirely."""
        lo = hi = None
        for ow, inv in self._prep(s):
            px, py, pz = x - ow[0], -ow[1], z - ow[2]
            # u = inv @ p ; b = inv @ (0,1,0) -> the second column of inv
            u = (inv[0] * px + inv[1] * py + inv[2] * pz,
                 inv[3] * px + inv[4] * py + inv[5] * pz,
                 inv[6] * px + inv[7] * py + inv[8] * pz)
            bv = (inv[1], inv[4], inv[7])
            t0, t1 = -1e30, 1e30
            hit = True
            for k in range(3):
                if abs(bv[k]) < 1e-12:
                    if u[k] < -1e-9 or u[k] > 1.0 + 1e-9:
                        hit = False
                        break
                    continue
                ta = (0.0 - u[k]) / bv[k]
                tb = (1.0 - u[k]) / bv[k]
                if ta > tb:
                    ta, tb = tb, ta
                t0 = max(t0, ta)
                t1 = min(t1, tb)
                if t0 > t1:
                    hit = False
                    break
            if not hit:
                continue
            lo = t0 if lo is None else min(lo, t0)
            hi = t1 if hi is None else max(hi, t1)
        return None if lo is None else (lo, hi)


def footprint_centre(s) -> tuple[float, float]:
    """A point GUARANTEED inside the piece's own footprint.

    The centroid of the convex hull, not the centre of the bounding box: for
    a piece placed at an angle the box centre can sit outside the footprint,
    and a ground reading taken outside the piece is a reading of somebody
    else's ground."""
    h = s.hull()
    if len(h) >= 3:
        return (sum(p[0] for p in h) / len(h), sum(p[1] for p in h) / len(h))
    return ((s.xmin + s.xmax) / 2.0, (s.zmin + s.zmax) / 2.0)


# ---------------------------------------------------------------------------
# POPULATION 1 - which bodies THIS RUN built
# ---------------------------------------------------------------------------
_SPAWN_POS_RE = re.compile(
    r"spawn_object\s+(?P<prefab>\S+).*?\bpos=(?P<pos>[-\d.,eE+]+)")


def plan_commands(rec: dict) -> list[tuple[str, float, float]] | None:
    """`(prefab, x, z)` for every `spawn_object` this record put on the wire,
    or None when the wire cannot be trusted to be complete.

    ONE READER, IMPORTED AND NOT COPIED.  `ledger/writer.py::parse_spawn_wire`
    is the single implementation of "what did this record command", and it
    carries both halves that three separate copies of this question got wrong
    on 2026-09-21: the wire is SEMICOLON-PACKED, so it is split before
    matching, and the parsed total is asserted against the record's own
    `command_count`, so a short read is UNREADABLE rather than quietly small.
    MEASURED on this chain: 226 of 421 records have `len(wire) !=
    command_count` because seq 72 declares 612 commands in 14 lines and seq
    137 declares 48 in ONE.  A second implementation here is how the tree got
    to four of them.
    """
    import sys                                               # noqa: PLC0415
    led = str(I.JUMPSTART / "ledger")
    if led not in sys.path:
        sys.path.insert(0, led)
    from writer import parse_spawn_wire                      # noqa: PLC0415
    return parse_spawn_wire("\n".join(str(w) for w in (rec.get("wire") or ())),
                            (rec.get("params") or {}).get("command_count"))


def clusters_into_a_body(pts: list[tuple[str, float, float]],
                         reach_m: float = PLAN_CLUSTER_M,
                         need: int = MIN_BODY_PIECES) -> bool:
    """Could these commanded positions BE a body of `need` pieces?

    Single linkage at `reach_m`, the same reach `our_bodies` searches with, so
    the guard and the check it guards cannot disagree.  Quadratic, and
    deliberately: the largest plan on this chain is 5,580 commands, which is
    16 million comparisons once per run against a check that already walks
    every ZDO in the save - and an approximate answer here would be a rule
    nobody could reproduce by hand from the record.
    """
    n = len(pts)
    if n < need:
        return False
    seen = [False] * n
    for start in range(n):
        if seen[start]:
            continue
        stack, size = [start], 0
        seen[start] = True
        while stack:
            i = stack.pop()
            size += 1
            if size >= need:
                return True
            for j in range(n):
                if seen[j]:
                    continue
                if (math.hypot(pts[i][1] - pts[j][1],
                               pts[i][2] - pts[j][2]) <= reach_m):
                    seen[j] = True
                    stack.append(j)
    return False


def ledger_retires(ledger) -> list[dict]:
    """Every placement this chain DELETED ON PURPOSE, and what it undoes.

    THE HALF OF PROVENANCE THIS SECTION NEVER READ.  `ledger_anchors` asks
    "what did the ledger plant" and nothing asked "what did the ledger then
    pull out", so a structure removed deliberately, with a reason, by records
    that NAME THE PLAN THEY UNDO, is indistinguishable here from one that was
    never built or that fell down.  `orphan_plan` then reports it forever and
    nothing a builder can do will ever clear it: rebuilding is the one action
    the ledger explicitly decided against.

    MEASURED on Ulfsland, save 2a6940a934b5, the only `orphan_plan` left on
    the chain: `spawn_plan` seq 2690 commanded SIX `fb_fortress_portcullis`
    at three columns x = 1062.518 / 1066.500 / 1070.482, two rows y = 23.774
    and 29.481, all at z = 752.  Retires seq 3090-3095 name those six pivots
    one for one, each carries `retires: [2690]`, and each states its reason -
    "Removed so a ship can enter the harbour, which is what the operator
    asked for and what the wall does not allow".  The save holds ZERO
    `fb_fortress_portcullis` in 489,182 ZDOs.  The rest of that castle is
    standing exactly where it was planted: `fb_fortress_wall` at 11.98 m and
    19.98 m from the anchor, the two stairs at 31.99 m and 39.99 m, the
    towers at 52.00 m and 59.99 m, all on the same z = 752 line.

    `retires` IS THE DISCRIMINATOR, NOT PROXIMITY AND NOT THE ROLE.  A retire
    that does not say which record it undoes cannot excuse one, and a retire
    of one piece cannot excuse a plan of six: the match is per COMMANDED
    POSITION, by prefab, inside the retire's OWN declared `radius_m`, which
    is Chebyshev because `deleteObjects -near` is a cube and the records
    themselves say so.
    """
    out: list[dict] = []
    for line in ledger.read_text().splitlines():
        if '"retire"' not in line:
            continue
        d = json.loads(line)
        if d.get("op") != "retire":
            continue
        p = d.get("params") or {}
        pos, pref = p.get("pos"), p.get("prefab")
        if not (pref and isinstance(pos, (list, tuple)) and len(pos) >= 3):
            continue
        out.append(dict(prefab=str(pref), x=float(pos[0]), z=float(pos[2]),
                        y=float(pos[1]), r=float(p.get("radius_m") or 0.0),
                        seq=d.get("seq"), site_id=p.get("site_id"),
                        retires=[int(s) for s in (p.get("retires") or ())
                                 if isinstance(s, (int, float))],
                        reason=str(p.get("reason") or "")))
    return out


def net_of_retires(anchors: list[dict], retires: list[dict]) -> None:
    """Subtract, IN PLACE, the placements this chain deliberately pulled out.

    A plan every one of whose commanded placements was retired by a record
    that CITES THAT PLAN is not an orphan and never will be: there is nothing
    left for a body to be made of, by the ledger's own decision.  It is
    reported as RETIRED, with the retire seqs and the reason, and it is kept
    out of `orphan_plan` - which otherwise refuses the operator's own ask,
    permanently, with no action that could clear it.

    AND THE REFUSAL THIS MUST NOT COST.  A body that is gone with NO retire
    naming it is still an orphan, and so is a plan of which only some
    placements were retired.  Silence is never the escape; only a record that
    says what it undid is.
    """
    if _RETIRE_NETTING_OFF:                    # what this section shipped
        for a in anchors:
            a["retired_commands"] = None
            a["retired_in_full"] = False
            a["retire_seqs"] = []
            a["retire_reason"] = ""
        return
    for a in anchors:
        cmds = a.get("commands")
        a["retired_commands"] = None
        a["retired_in_full"] = False
        a["retire_seqs"] = []
        a["retire_reason"] = ""
        if not cmds:
            continue
        cited = [r for r in retires if set(r["retires"]) & set(a["seqs"])]
        if _RETIRE_WITHOUT_CITATION:           # any retire excuses anything
            cited = list(retires)
        if not cited:
            a["retired_commands"] = 0
            continue
        if _RETIRE_BY_SITE:                    # one retire excuses the plan
            same = [r for r in cited if r["site_id"] == a["site_id"]]
            a["retired_commands"] = len(cmds) if same else 0
            a["retired_in_full"] = bool(same)
            a["retire_seqs"] = sorted({r["seq"] for r in same})
            a["retire_reason"] = next((r["reason"] for r in same
                                       if r["reason"]), "")
            continue
        dead, seqs = 0, []
        for (pref, x, z) in cmds:
            hit = next((r for r in cited if r["prefab"] == pref
                        and max(abs(r["x"] - x), abs(r["z"] - z))
                        <= r["r"] + 1e-6), None)
            if hit is not None:
                dead += 1
                seqs.append(hit["seq"])
        a["retired_commands"] = dead
        a["retired_in_full"] = dead == len(cmds)
        a["retire_seqs"] = sorted(set(seqs))
        a["retire_reason"] = next((r["reason"] for r in cited
                                   if r["reason"]), "")


def ledger_anchors(ledger) -> list[dict]:
    """Every structure the ledger planned, with the anchor it was planted on,
    NET OF THE PLACEMENTS THE SAME CHAIN LATER RETIRED.

    The ledger defines WHICH BUILDINGS ARE OURS and nothing else.  Not one
    height in this file comes from it, and `target_y` is read only as the
    claim a pad is judged against."""
    out: dict[tuple, dict] = {}
    for line in ledger.read_text().splitlines():
        if '"spawn_plan"' not in line:
            continue
        d = json.loads(line)
        if d.get("op") != "spawn_plan":
            continue
        p = d.get("params") or {}
        a = p.get("anchor")
        if not a:
            continue
        key = (p.get("role"), p.get("site_id"), round(a["x"], 2),
               round(a["z"], 2))
        prev = out.get(key)
        # THE PLAN'S OWN PREFAB LIST, because proximity is not provenance.
        # Every one of the 405 anchored `spawn_plan` records on this chain
        # declares `params.prefabs`, so the discriminator is always available;
        # `our_bodies` uses it to refuse a neighbour's building.  Unioned
        # across repeated plans for one anchor because a body emitted in
        # several passes is one body.
        pf = {str(n) for n in (p.get("prefabs") or ())}
        cmds = plan_commands(d)
        # WHICH RECORDS BUILT THIS ANCHOR, because a retire names the record
        # it undoes and not the site.  Unioned for the same reason `prefabs`
        # and `commands` are.
        seqs = {d.get("seq")} - {None}
        # ONE UNRECOVERABLE RECORD POISONS THE ANCHOR, and that is the safe
        # direction: an anchor emitted in several passes is one body, so a
        # pass whose commands cannot be read leaves the anchor's own geometry
        # unknown, and an unknown plan is judged against a body like any
        # other rather than excused as a scatter.
        if prev is not None:
            pf |= prev["prefabs"]
            seqs |= prev["seqs"]
            cmds = (None if cmds is None or prev["commands"] is None
                    else prev["commands"] + cmds)
        out[key] = dict(role=p.get("role"), site_id=p.get("site_id"),
                        x=float(a["x"]), z=float(a["z"]),
                        y=float(a.get("y") or 0.0), actor=d.get("actor"),
                        prefabs=pf, commands=cmds, seqs=seqs,
                        # DECLARED BY THE PLAN ITSELF, not inferred from its
                        # role: could these commands make a body at all.
                        declares_a_body=(True if cmds is None
                                         else clusters_into_a_body(cmds)))
    anchors = list(out.values())
    net_of_retires(anchors, ledger_retires(ledger))
    return anchors


def generate_discs(ledger) -> list[tuple[float, float, float]]:
    """Every `zones_generate pos=X,Z max=M` disc the ledger ever asked for.

    THIS IS THE ONLY LEDGER READ IN THIS SECTION THAT IS NOT ABOUT OWNERSHIP,
    and it exists because "2,874 duplicate ZDOs" is a symptom with no named
    cause, which is the shape of finding this gate exists to stop producing.

    MEASURED on Ulfsland: 2,248 of the 2,874 are two zones, (-23,111) with
    1,181 extras of 2,374 ZDOs and (2,56) with 1,067 of 2,187.  In (-23,111)
    1,161 of 1,191 distinct (prefab, position, rotation) keys stand at
    multiplicity EXACTLY 2, and every singleton is a cliff, an Ygga shoot, a
    spawner, the `LocationProxy` or the treasure chest - i.e. a whole vanilla
    Location instantiated a second time, not a builder emitting twice.  Those
    two zones are covered by 12 and 15 separate `zones_generate` discs
    respectively; 406 of the ledger's 489 zones are covered more than once
    and the worst is 27.  `ledger/schema.py`'s `zones_generate` declares
    `idempotent="yes"` on the grounds that `SpawnZone` skips a zone that
    `IsZoneGenerated`, and nothing has ever measured that claim.

    The disc is taken from `pos` and `max_m`, not from `params.zones`: the
    wire command selects by distance and `zones` carries only the first
    eight, so counting the list would under-report the coverage by an order
    of magnitude and name the wrong ops.
    """
    out = []
    for line in ledger.read_text().splitlines():
        if '"zones_generate"' not in line:
            continue
        d = json.loads(line)
        if d.get("op") != "zones_generate":
            continue
        p = d.get("params") or {}
        pos, m = p.get("pos"), p.get("max_m")
        if not (isinstance(pos, (list, tuple)) and len(pos) >= 2
                and isinstance(m, (int, float))):
            continue
        out.append((float(pos[0]), float(pos[1]), float(m)))
    return out


def our_bodies(bods: list[list], anchors: list[dict]
               ) -> tuple[list[tuple[list, list]], list[dict], list[dict],
                          list[dict], list[dict]]:
    """`(matched, orphan, foreign, scatter, retired)` - bodies this run
    planted, matched to the anchors that planted them, plus the four
    populations that did not match and WHY they did not.

    Matched by the BODY'S OWN EXTENT plus a stated margin, not by a fixed
    radius: a 60 m hall and a 4 m shed are not the same search.  An anchor
    that matches nothing is REPORTED - a plan with no building is a finding,
    not a rounding error - UNLESS the plan's own commands could not have made
    a body in the first place, which is the `scatter` population and is
    explained at `PLAN_CLUSTER_M`.

    AND PROXIMITY IS NOT PROVENANCE.  Extent-plus-margin alone claimed a
    building this run did not place and then refused it.  MEASURED on
    Ulfsland: `lighting-T13`'s plan is ONE `piece_groundtorch` at
    (-38.1221, -78.9671) - `spawn_plan` seq 2206 declares
    `prefabs: ["piece_groundtorch"]` and `command_count: 5` - and 11.6 m away
    stands a 30-piece body of 17 `woodwall`, 6 `wood_pole`, 4 `wood_floor` and
    2 `wood_wall_half` whose own `LocationProxy` at (-45.57, -68.11) has NO
    row in the mod-free 12,301-instance generator dump
    (`roads/mod_keepout.json` census-T13_temple_harboursouth-039,
    `placer: mod-placed`, `forbid: true`).  The section reported that body as
    "lighting-T13: 16 of 30 pieces lie COMPLETELY under the applied surface,
    worst 4.092 m" - a real burial, of somebody else's farmhouse, filed
    against a lamp that cannot emit a wall.

    So a body is OURS only if an anchor reaches it AND the body holds at least
    one piece of a prefab THAT ANCHOR'S OWN PLAN DECLARED.  All 405 anchored
    `spawn_plan` records on this chain carry `params.prefabs`, so this is
    never a guess; an anchor with no declared prefabs falls back to extent
    alone and SAYS SO through `prefabs_declared`.  A body that an anchor
    reaches but shares no prefab with is returned as FOREIGN - reported, and
    still measured, because our terrain writes burying a protected body is a
    finding that must not disappear with the attribution.
    """
    idx: dict[tuple[int, int], list[int]] = collections.defaultdict(list)
    boxes = []
    names = []
    for i, b in enumerate(bods):
        bb = (min(s.xmin for s in b), max(s.xmax for s in b),
              min(s.zmin for s in b), max(s.zmax for s in b))
        boxes.append(bb)
        names.append({s.name for s in b})
        for cx in range(int(bb[0] // 32), int(bb[1] // 32) + 1):
            for cz in range(int(bb[2] // 32), int(bb[3] // 32) + 1):
                idx[(cx, cz)].append(i)
    hit: dict[int, list[dict]] = {}
    near: dict[int, list[dict]] = {}
    orphan: list[dict] = []
    scatter: list[dict] = []
    retired: list[dict] = []
    m = ANCHOR_MARGIN_M
    for a in anchors:
        cand: set[int] = set()
        for cx in range(int((a["x"] - m) // 32), int((a["x"] + m) // 32) + 1):
            for cz in range(int((a["z"] - m) // 32),
                            int((a["z"] + m) // 32) + 1):
                cand.update(idx.get((cx, cz), ()))
        reach = [i for i in cand
                 if boxes[i][0] - m <= a["x"] <= boxes[i][1] + m
                 and boxes[i][2] - m <= a["z"] <= boxes[i][3] + m]
        want = a.get("prefabs") or set()
        found = [i for i in reach if not want or (names[i] & want)]
        for i in found:
            hit.setdefault(i, []).append(a)
        for i in reach:
            if i not in found:
                near.setdefault(i, []).append(a)
        if found:
            continue
        # A PLAN IS JUDGED AGAINST A BODY ONLY IF ITS OWN COMMANDS COULD MAKE
        # ONE.  `declares_a_body` is measured off the record's own wire by
        # `clusters_into_a_body`, at this function's own reach, so the guard
        # and the search agree by construction.  A plan whose commands are a
        # scatter is reported as a SCATTER and not as an orphan: "no body
        # stands within 8 m" is a true sentence about it and not a finding,
        # and its pieces are already carried by its own record's
        # `expect.prefab_count` postcondition, which is measured at write
        # time and re-measured by the save.
        #
        # A RECORD WHOSE WIRE IS UNRECOVERABLE DECLARES NOTHING AND IS STILL
        # AN ORPHAN.  Silence is not evidence of a scatter; the only way out
        # of the orphan population is a positive measurement off the plan.
        if a.get("commands") and not a.get("declares_a_body", True):
            scatter.append(dict(a, reached_bodies=len(reach),
                                prefabs_declared=sorted(want),
                                commands_declared=len(a["commands"])))
            continue
        # AND A PLAN THE LEDGER ITSELF PULLED OUT IS NOT AN ORPHAN.  Every
        # one of its commanded placements was deleted by a record that CITES
        # IT and states why; "no body stands here" is that record's own
        # intent, not a finding, and the only action `orphan_plan` could ever
        # ask for is to put back the thing the chain decided to remove.  See
        # `net_of_retires` - a PARTIAL retire is still an orphan, and a body
        # that is gone with no retire naming it is still an orphan.
        if a.get("retired_in_full"):
            retired.append(dict(a, reached_bodies=len(reach),
                                prefabs_declared=sorted(want),
                                commands_declared=len(a["commands"])))
            continue
        orphan.append(dict(a, reached_bodies=len(reach),
                           prefabs_declared=sorted(want)))
    foreign = [dict(pieces=len(bods[i]),
                    x=round(sum(s.pos[0] for s in bods[i]) / len(bods[i]), 1),
                    z=round(sum(s.pos[2] for s in bods[i]) / len(bods[i]), 1),
                    prefabs=sorted(names[i]),
                    claimed_by=sorted({a["site_id"] or a["role"]
                                       for a in near[i]}),
                    declared=sorted({n for a in near[i]
                                     for n in (a.get("prefabs") or ())}))
               for i in sorted(near) if i not in hit]
    return ([(bods[i], hit[i]) for i in sorted(hit)], orphan, foreign,
            scatter, retired)


# ---------------------------------------------------------------------------
# POPULATION 2 - the samples each pad's OWN write set, and still owns
# ---------------------------------------------------------------------------
def decode_tcdata(raw: bytes) -> dict:
    """`TerrainComp`'s own serialised form, parsed byte for byte.

    version:int operations:int lastOpPoint:Vector3 lastOpRadius:float
    n:int then n x (modified:bool [levelDelta:float smoothDelta:float])
    then n2:int then n2 x (modifiedPaint:bool [Color:4xfloat]).

    `left` is the number of bytes the parse did NOT consume and MUST be 0.  A
    partial parse that silently returns the samples it managed to read is how
    an instrument starts reporting a smaller population than it was asked
    about, and the caller refuses to use a blob that leaves bytes behind."""
    off = 0

    def i32() -> int:
        nonlocal off
        v = struct.unpack_from("<i", raw, off)[0]
        off += 4
        return v

    def f32() -> float:
        nonlocal off
        v = struct.unpack_from("<f", raw, off)[0]
        off += 4
        return v

    ver, ops = i32(), i32()
    lop = (f32(), f32(), f32())
    lrad = f32()
    n = i32()
    level: dict[int, float] = {}
    for i in range(n):
        b = raw[off]
        off += 1
        if b:
            level[i] = f32()
            off += 4                       # smoothDelta, not used here
    npaint = 0
    if len(raw) - off >= 4:
        n2 = i32()
        for _ in range(n2):
            b = raw[off]
            off += 1
            if b:
                off += 16
                npaint += 1
    return dict(version=ver, operations=ops, last_op_point=lop,
                last_op_radius=lrad, n=n, level=level, painted=npaint,
                left=len(raw) - off)


def pad_rect(cx: float, cz: float, w: float, d: float,
             apron: float) -> list[tuple[float, float]]:
    """Every lattice sample inside the rectangle a pad write DECLARED.

    This is the promise, and it is not the same set as "the samples somebody
    wrote": a sample the writer dropped is still inside the rectangle it said
    it levelled, and it is the only population in which that sample is
    visible at all.  MEASURED on Ulfsland - see `check_pad_coverage`.
    """
    hw, hd = w / 2 + apron, d / 2 + apron
    return [(float(x), float(z))
            for x in range(int(math.ceil(cx - hw - 1e-6)),
                           int(math.floor(cx + hw + 1e-6)) + 1)
            for z in range(int(math.ceil(cz - hd - 1e-6)),
                           int(math.floor(cz + hd + 1e-6)) + 1)]


def pad_populations(ledger, blobs) -> tuple[dict[str, dict], list[str]]:
    """For every pad the ledger wrote: the rectangle it promised, the lattice
    samples it set, and which of them it STILL OWNS.

    A pad's blob is the WHOLE ZONE'S TerrainComp state after the write, not
    the pad's contribution to it - so reading a blob directly attributes every
    road and every neighbouring pad in that zone to this one.  The written set
    is therefore recovered by DIFFERENCE: walking the ledger in order and
    keeping the previous state of each zone, the samples a write is
    responsible for are the ones whose modified-set or level changed.
    Ownership then passes to whoever wrote a sample LAST, so a pad that a road
    later cut through is judged on what it still owns and TOLD how much it
    lost.  MEASURED on Ulfsland: 11 of 57 pads lost samples to a later writer.

    The alternative - a rectangle around the ANCHOR - is the exact mistake
    that produced a 1.634 m "pad error" that was unwritten natural ground
    outside the pad, 36x the real figure.  The rectangle here is not that: it
    is the one the RECORD declares, `params.centre` with `pad_w x pad_d`,
    required on every pad write since 2026-09-19 (`ledger/schema.py:964`).

    TWO THINGS WERE WRONG HERE AND BOTH WERE POPULATIONS.

    1. THE KEY WAS THE SITE, NOT THE WRITE.  A site written twice with
    DIFFERENT targets had both writes' samples unioned under the LATER
    target.  MEASURED: `tree-sth-2` is seq 633 at target 75.700 and seq 669 at
    76.190; `tree-sth-3` is seq 640 at 75.700 and seq 676 at 75.000.  Judged
    per record, seq 633's 16 surviving samples read 75.700 with spread
    0.000 m - dead on the target the record that wrote them declared - and so
    do seq 640's 28.  Judged under the later target they were 0.490 m and
    0.700 m "out", and those were two of this section's four pad refusals.
    The verdict for a site is now its LATEST record's; an earlier record's
    surviving samples are reported as a superseded skirt.

    2. THE PROMISE WAS NOT SEPARATED FROM THE SPILL.  A write's difference set
    includes samples OUTSIDE its own rectangle, because two pads in one zone
    share one compiler (`terraform/flatten.py:236`) and the blob carries the
    union.  MEASURED: `stenvik-stonehouse-2` seq 279 declares an 11 x 15 pad;
    180 of its 190 attributed samples are the exact 12 x 15 lattice of that
    rectangle and every one reads 47.210, and the other 10 sit at z = 908,
    31 m away, reading 47.030 - which is `stenvik-hall-1`'s target.  Held to
    47.210 they made the third refusal.  `wt-south` seq 373 is the same shape:
    8 surviving samples, 5 of them 3-5 m outside its 20 x 20 rectangle at
    ~80.5 m, which is where its headline "2.180 m" came from.
    """
    zone_state: dict[tuple, dict] = {}
    owner: dict[tuple, tuple] = {}
    wrote: dict[tuple, set] = collections.defaultdict(set)
    recs: list[tuple] = []
    complaints: list[str] = []
    cache: dict[str, dict] = {}
    # Where each site's unit was planted, for recovering a legacy pad's
    # centre.  `settlements/build.py:1121` derives the pad centre from the
    # same `unit["x"], unit["z"]` the `spawn_plan` anchor records.
    plan_anchor: dict[str, tuple[float, float]] = {}
    for line in ledger.read_text().splitlines():
        if '"spawn_plan"' not in line:
            continue
        d = json.loads(line)
        if d.get("op") != "spawn_plan":
            continue
        p = d.get("params") or {}
        sid = p.get("site_id") or p.get("name")
        a = p.get("anchor") or {}
        if sid and "x" in a and sid not in plan_anchor:
            plan_anchor[sid] = (float(a["x"]), float(a["z"]))
    seq = 0
    for line in ledger.read_text().splitlines():
        seq += 1
        if '"terrain_write"' not in line:
            continue
        d = json.loads(line)
        if d.get("op") != "terrain_write":
            continue
        p = d.get("params") or {}
        sid = p.get("site_id") or p.get("name")
        # THE RECORD, not the site: a site written twice with two targets is
        # two promises and only the later one is current.
        tag = (d.get("seq", seq), p.get("role"), sid)
        for e in p.get("entries") or ():
            sha = e.get("blob_sha256")
            f = blobs / (sha or "")
            if not sha or not f.exists():
                complaints.append(f"{sid}: blob {sha} is missing")
                continue
            dd = cache.get(sha)
            if dd is None:
                dd = decode_tcdata(gzip.open(f, "rb").read())
                cache[sha] = dd
            if dd["left"] != 0 or dd["n"] != _HEIGHTMAP_W ** 2:
                complaints.append(
                    f"{sid}: blob {sha[:12]} parsed to n={dd['n']} with "
                    f"{dd['left']} bytes left over")
                continue
            z = tuple(e["zone"])
            cx, cz = e["centre"]
            prev = zone_state.get(z, {})
            cur = dd["level"]
            for i, v in cur.items():
                if i not in prev or abs(prev[i] - v) > 1e-6:
                    iy, ix = divmod(i, _HEIGHTMAP_W)
                    w = (float(cx + ix - 32), float(cz + iy - 32))
                    owner[w] = tag
                    wrote[tag].add(w)
            zone_state[z] = cur
        if p.get("role") == "site_pad":
            dg = ((p.get("location_check") or {}).get("delta_gate") or {})
            c = p.get("centre")
            recs.append((tag, dict(
                seq=tag[0], site_id=sid,
                target_y=float(p["target_y"]), pad_w=p.get("pad_w"),
                pad_d=p.get("pad_d"), apron_m=p.get("apron_m"),
                actor=d.get("actor"),
                centre=([float(c[0]), float(c[1])]
                        if isinstance(c, (list, tuple)) and len(c) == 2
                        else None),
                # The tolerance the WRITE ITSELF declared it met.  A pad is
                # held to its own claim, not to a number invented here.
                tol_m=float(dg.get("tolerance_m") or 0.10),
                tol_src=("the write's own delta_gate.tolerance_m"
                         if dg.get("tolerance_m") else
                         "0.10 m, the delta_gate tolerance every other pad "
                         "write on this world declared"))))
    # THE RECORD THAT CARRIES THE SITE'S VERDICT is the LAST one that still
    # OWNS a sample.  A re-write that changed nothing appears in no
    # difference and owns nothing: `stenvik-hall-1` seq 71 and 84 re-sent
    # blobs byte-identical to seq 61's, and `tree-sth-1` seq 647 re-sent
    # seq 640's - four records, zero samples each.  Making one of those the
    # current record hands the site's promise to a write with no population
    # and reports "0 samples and no applied reading - unmeasured", which is
    # a refusal about the ledger's bookkeeping dressed up as a refusal about
    # the ground.  If NO record for a site owns anything, the last one is
    # still named, and its emptiness is then the honest finding.
    latest: dict[str, tuple] = {}
    owning: dict[str, tuple] = {}
    for tag, m in recs:
        latest[m["site_id"]] = tag
        if any(owner.get(w) == tag for w in wrote[tag]):
            owning[m["site_id"]] = tag
    for sid, tag in owning.items():
        latest[sid] = tag
    out = {}
    for tag, m in recs:
        own = sorted(w for w in wrote[tag] if owner.get(w) == tag)
        # WHERE THE RECTANGLE IS, when the record does not say.
        #
        # `params.centre` has been REQUIRED on a pad write since 2026-09-19
        # (`ledger/schema.py:964`), and 16 of this chain's 66 pad records
        # carry it.  The other 50 predate the requirement, and without a
        # position their `pad_w x pad_d` is a size with nowhere to be - which
        # is the whole reason this section had to fall back to "the samples
        # somebody wrote" and then hold a neighbour's samples to this pad's
        # target.
        #
        # THE LEDGER ALREADY HOLDS THE ANSWER ELSEWHERE.  `settlements/
        # build.py:1121` sets the pad centre from `unit["x"], unit["z"]`, and
        # the `spawn_plan` for the same `site_id` records the same unit's
        # anchor.  MEASURED over all 63 pad records with blobs: taking that
        # anchor as the centre and the record's own `pad_w x pad_d` as the
        # extent, the implied lattice rectangle reproduces EVERY sample the
        # record wrote for 53 of them, exactly, with no sample missing.  Of
        # the 10 that do not, six are re-writes whose unchanged samples were
        # already at target and so appear in no difference, and FOUR are the
        # `flatten.build` float-edge defect itself, each short by exactly one
        # full edge row.  The failure mode is never a SHIFTED rectangle.
        #
        # So the centre is RECOVERED, and every number derived from it says
        # so.  `centre_src` is carried into the population string of both pad
        # checks; a refusal that rests on a recovered centre is readable as
        # such, and one that rests on a declared centre - `wt-south` seq
        # 3100 - is not weakened by sharing a code path with it.
        centre = m["centre"]
        src = "the record's own params.centre"
        if centre is None:
            a = plan_anchor.get(m["site_id"])
            if a is not None:
                centre = [a[0], a[1]]
                src = (f"RECOVERED from this site's own spawn_plan anchor "
                       f"({a[0]:g}, {a[1]:g}) - the record declares no "
                       f"centre")
            else:
                src = ("NOWHERE - the record declares no centre and no "
                       "spawn_plan anchor names this site")
        rect = (pad_rect(centre[0], centre[1], float(m["pad_w"]),
                         float(m["pad_d"]), float(m["apron_m"] or 0.0))
                if centre and m["pad_w"] and m["pad_d"] else None)
        inside = ([w for w in own if w in set(rect)] if rect is not None
                  else own)
        key = (m["site_id"] if latest[m["site_id"]] == tag
               else f"{m['site_id']}@seq{m['seq']}")
        # THE TWO FACTS A COVERAGE VERDICT NEEDS ABOUT EACH RECTANGLE
        # SAMPLE, and neither of them is "is it in my own set".
        #
        # `rect_wrote`  -- did THIS write ever set this sample.  A sample it
        #                  never wrote is its own coverage gap no matter who
        #                  holds the ground now.
        # `rect_owner`  -- which record's bytes hold it TODAY, by FILE ORDER,
        #                  which is the order this walk runs in.  NOT by
        #                  `seq`: this chain forked at file line 47 and
        #                  carries duplicate labels 43-46, so `seq` is not a
        #                  total order on it.  The tag carries the role, so a
        #                  road that later cut this pad is identifiable
        #                  without a second pass.
        rs = set(rect or ())
        out[key] = dict(m, wrote=len(wrote[tag]), own=own, rect=rect,
                        inside=inside, centre=centre, centre_src=src,
                        centre_declared=m["centre"] is not None,
                        current=latest[m["site_id"]] == tag,
                        rect_wrote={w for w in wrote[tag] if w in rs},
                        rect_owner={w: owner[w] for w in rs
                                    if w in owner})
        # A PAD IS A SITE, BUT OWNERSHIP IS KEYED BY RECORD - AND THAT COSTS
        # THE GATE THE PAD IT REPAIRED BEST.
        #
        # `inside` above is `own & rect`, and `own` is only the samples THIS
        # RECORD's write changed.  So a re-emit inherits NONE of its own
        # site's earlier record's samples, and the population shrinks by
        # exactly how little the repair needed to move: the better the
        # re-emit, the smaller the population, and a re-emit that finds the
        # interior already correct owns none of its own rectangle and is
        # reported "0 samples ... unmeasured" -- a refusal about the
        # ledger's bookkeeping dressed up as a refusal about the ground,
        # which is the very sentence the `owning` guard above was written
        # to prevent.  That guard tests `own`, not `own & rect`, so a record
        # owning only the row the write straddles OUTSIDE its rectangle
        # passes it and still lands with an empty rectangle population.
        #
        # MEASURED on Ulfsland: `vestvik-villa-3` seq 5375 re-levelled its
        # pad, correctly moved only the 34 edge samples that were off, and
        # came out rect=288 own=18 INSIDE=0 -- while all 288 of its
        # rectangle's samples are owned by `(502, 'site_pad',
        # 'vestvik-villa-3')`, ITS OWN EARLIER RECORD.  The pad is fully
        # measurable and dead flat; the gate refused it as unmeasured.
        #
        # Successive records of one site ARE one pad, so the flatness
        # population is keyed by SITE.  `inside` and every number derived
        # from it stay per-record, because "how much did THIS write own"
        # and "how much of it left its own rectangle" are real questions
        # about the record.
        inside_site = sorted(
            w for w in rs
            if (owner.get(w) or (None, None, None))[2] == m["site_id"])
        out[key]["inside_site"] = inside_site
    return out, complaints


# A terrain_write with one of these roles is A ROAD.  A road bed is declared
# by a centreline and a width, not by a `target_y`, and the `roads` section
# measures it over its own population - so a building pad that a road later
# cut through cedes that ground to roads rather than holding it to a datum
# the road was never asked to meet.  Charging one piece of ground to two
# sections is double-counting it, and holding a carriageway to a floor level
# is a category error that can only be satisfied by un-building the road.
ROAD_ROLES = ("road_segment", "road_junction", "road_clear")


def pad_promises(pads: dict) -> list[tuple[str, set, float, float]]:
    """Every pad record's own declared promise: `(key, rect, target, tol)`.

    SUPERSEDED RECORDS ARE IN HERE ON PURPOSE.  A promise a later record
    replaced still describes the ground its own samples were levelled to, and
    that is the only thing `check_pad_coverage` asks it: MEASURED on Ulfsland,
    `stenvik-cottage-2`'s ten contested samples read 47.030 m, which is
    `stenvik-hall-1`'s target - the ground keeps hall-1's promise exactly, and
    the record that last HELD those bytes (`stenvik-stonehouse-2` seq 279) is
    a third party whose own rectangle does not even contain them.
    """
    return [(k, set(p["rect"]), p["target_y"], p["tol_m"])
            for k, p in pads.items() if p["rect"]]


def gpoint(x: float, z: float) -> tuple[str, float, float]:
    """The ground query for a column: label and coordinate together, from the
    SHARED instrument, so there is one implementation of this in the gate.

    Kept as a one-line wrapper only so the reason is recorded where the
    section that caused it can be read.  THE DEFECT WAS HERE: this section
    labelled a query by its ROUNDED coordinate and then asked the engine the
    UNROUNDED one.  MEASURED, and it cost a whole run - of 38,867 queries,
    3,388 labels were requested more than once and exactly ONE pair
    disagreed: (-836.5001, 7734.5) read 30.4490 m and (-836.5000, 7734.5)
    read 30.2858 m.  A tenth of a millimetre, 0.1632 m apart, in the same
    file, from the same run, with the same zones spawned and an applied delta
    of 0.0000 at both - so no TerrainComp was involved at all.

    `Heightmap.GetHeight` QUANTISES ITS QUERY TO THE NEAREST LATTICE VERTEX,
    so it is a STEP function on the 1 m lattice and not a surface.  A query on
    the exact half-lattice line is ambiguous between two vertices and flips on
    a nudge too small to print.  Valheim snaps build pieces to 0.5 m, so
    piece-derived columns land on that line constantly: 413 of this section's
    own 38,867 queries were on an exact *.5 in x or z.  `I.gpoint` snaps the
    query to the vertex that will actually answer and derives the label from
    the snapped coordinate, so one label is one question by construction.

    WHAT THAT MEANS FOR EVERY NUMBER BELOW, stated so no reader overstates
    this section's resolution:

      GROUNDING reads THE HEIGHT OF THE NEAREST LATTICE VERTEX to the piece's
      own footprint centroid - at most 0.707 m away, diagonally - and not an
      interpolated surface under the piece.  A body is judged on the MINIMUM
      over all its pieces, which spans many distinct vertices on anything
      bigger than a shed, so the quantisation cannot hide a floating body; it
      does mean a single piece's clearance carries the local vertex
      difference as its error bar.
      FLATNESS has no such error bar at all: a pad's own written samples ARE
      lattice vertices - zone centre plus an integer index - so `snap` is the
      identity on them and the query is exactly the vertex the pad wrote.
    """
    return I.gpoint(x, z, prefix="S")


def gkey(x: float, z: float) -> str:
    return gpoint(x, z)[0]


def piece_columns(s) -> list[tuple[float, float]]:
    """Every lattice vertex under this piece's OWN rotated footprint.

    These are the columns the engine has heights for; asking anywhere else
    returns one of them anyway.  A piece too small to cover a vertex - a
    0.3 m pole, most of the corpus by count - gets the vertex nearest its
    centroid, because for that piece that vertex IS the ground under it.
    """
    out = []
    for x in range(int(math.floor(s.xmin)), int(math.ceil(s.xmax)) + 1):
        for z in range(int(math.floor(s.zmin)), int(math.ceil(s.zmax)) + 1):
            if s.covers(float(x), float(z)):
                out.append((float(x), float(z)))
    if not out:
        cx, cz = footprint_centre(s)
        out = [I.snap(cx, cz)]
    return out


# ---------------------------------------------------------------------------
# CHECK S1 - GROUNDING, against the surface the engine draws
# ---------------------------------------------------------------------------
def check_grounding(bodies_named, ground: dict, cols: Columns, geom,
                    judge_support: bool = True) -> list:
    """Does each body touch the ground UNDER IT, and is any piece swallowed?

    THE REFERENCE IS THE APPLIED SURFACE, sampled under each piece's own
    footprint centroid.  Four grounding censuses reported zero floating over a
    212-piece watchtower in 3.173-13.542 m of air, because each compared the
    body to the datum its own pad was levelled to - and a pad datum agrees
    with itself no matter where the ground went.

    A body is judged by its BEST-SUPPORTED piece: `support = min over pieces,
    and over every lattice vertex under that piece, of (the piece's own
    underside in that column - the applied ground in that column)`.  That is
    what makes a bridge, a balcony and a second storey not defects: they are
    pieces of a body that touches down somewhere.  It is also what makes the
    watchtower a defect: all 40 of its columns cleared the ground, so the
    minimum itself was 3.173 m.

    EVERY VERTEX UNDER THE PIECE, NOT THE ONE NEAREST ITS CENTRE.
    `Heightmap.GetHeight` is NEAREST-VERTEX - MEASURED at population scale on
    this world, 243 of 243 same-cell station groups returned IDENTICAL
    heights where interpolation would have been required to differ, with a
    median true spread of 0.0744 m and a maximum of 1.5830 m.  So one reading
    per piece is one vertex, and a 2x2 m slab can sit over ground that rises
    1.58 m across it.  Reading only the centre vertex would call a slab
    floating when a corner of it is resting on the ground, and would miss a
    piece buried at one end.  The piece is therefore asked at EVERY lattice
    vertex its own rotated footprint covers - the columns the engine actually
    has heights for - and only a piece too small to cover one falls back to
    the vertex nearest its centroid, which for that piece IS the whole of the
    ground under it.

    `judge_support=False` asks ONLY the burial half, for `lone_placed`'s
    one- and two-piece bodies: whether a bench stands on anything is a
    question about the building around it, and whether a placed piece is
    invisible under the ground is a question about the piece.
    """
    out = []
    for body, anchors, label in bodies_named:
        best = None
        best_piece = None
        swallowed = []
        unmeasured = 0
        for s in body:
            clear = None
            # THE LOWEST APPLIED SURFACE ANYWHERE UNDER THIS PIECE.  "Buried"
            # is a claim about the WHOLE SOLID - it lies completely under the
            # surface, nobody can see it, removing it changes nothing - and
            # it was being decided from the top of whatever slice of the
            # solid happened to occupy a 1 m lattice vertex.  A PIECE THAT
            # SPANS HEIGHT HAS A DIFFERENT TOP IN EVERY COLUMN, so that is
            # the top of a CROSS-SECTION, not of the piece.
            #
            # MEASURED on Ulfsland save 5385652e, the section's only two
            # `buried` refusals, both false:
            #
            #   sudrberg-keep `woodiron_beam_45` (2415.064, 121.244,
            #     -3351.906): refused at 0.495 m "below its own top".  It is
            #     a 45 DEGREE beam spanning y 120.113..122.376, its single
            #     lattice vertex cuts it at 121.345, and the applied ground
            #     is 121.840 - so 0.536 m OF THE BEAM STANDS PROUD OF THE
            #     GROUND while the check called it invisible.  302 identical
            #     beams stand in that body; 2 of them fired, and which 2 was
            #     decided by where the metre lattice happened to cut the
            #     diagonal.
            #   vestvik-villa-3 `wood_door` (-887.976, 36.713, 1877.499):
            #     refused at 0.105 m.  Both of its lattice vertices land in
            #     the DOORWAY, where the only solid is the 1.8 mm threshold
            #     sill (`piece_geometry` part 4: y -1.0100..-1.0082), so the
            #     "top of the piece" read 35.705 against ground 35.810.  The
            #     door's own top is 37.723 - 1.911 m in the air, hinges and
            #     all.
            #
            # The remedy either of those findings has is `retire`, role
            # `buried_piece`: they were two recorded demolitions of standing
            # structure, one of them a DOOR, waiting on somebody to action
            # a number.  So the test is now the piece's OWN top against the
            # LOWEST ground under it - if `s.ymax` is under even the lowest
            # surface the piece covers then every point of the solid is too,
            # which is exact, and it is the only form of the question that
            # cannot be changed by where the lattice falls.
            gmin = None
            over = None            # the SHIPPED reading, kept for the neuter
            for (x, z) in piece_columns(s):
                g = ground.get(gkey(x, z))
                if g is None:
                    continue
                sp = cols.span(s, x, z)
                lo, hi = (s.ymin, s.ymax) if sp is None else sp
                c = lo - g
                clear = c if clear is None else min(clear, c)
                o = hi - g
                over = o if over is None else max(over, o)
                gmin = g if gmin is None else min(gmin, g)
            if clear is None:
                unmeasured += 1
                continue
            if best is None or clear < best:
                best, best_piece = clear, s
            if _BURIED_PER_COLUMN:
                if over < -BURIED_TOL_M:
                    swallowed.append((round(-over, 3), s))
            elif s.ymax - gmin < -BURIED_TOL_M:
                swallowed.append((round(gmin - s.ymax, 3), s))
        if best is None and not judge_support:
            continue
        if best is None:
            out.append(Finding(
                check="grounding", ok=False,
                message=(f"{label}: {len(body)} pieces and NOT ONE ground "
                         "reading came back - the body is unmeasured, which "
                         "is not the same as sound"),
                where=(round(sum(s.pos[0] for s in body) / len(body), 1),
                       round(sum(s.pos[2] for s in body) / len(body), 1)),
                numbers={"pieces": len(body), "ground_readings": 0}))
            continue
        cx = round(sum(s.pos[0] for s in body) / len(body), 1)
        cz = round(sum(s.pos[2] for s in body) / len(body), 1)
        roles = sorted({a["role"] for a in anchors})
        if judge_support and best > SUPPORT_LIMIT_M:
            out.append(Finding(
                check="grounding", ok=False,
                message=(f"{label}: {len(body)} pieces, NOTHING TOUCHES THE "
                         f"GROUND - the best-supported piece "
                         f"({best_piece.name}) clears the applied surface "
                         f"beneath it by {best:.3f} m, limit "
                         f"{SUPPORT_LIMIT_M:.3f} m"),
                where=(cx, round(best_piece.pos[1], 2), cz),
                measured=round(best, 3), limit=round(SUPPORT_LIMIT_M, 3),
                numbers={"pieces": len(body), "support_gap_m": round(best, 3),
                         "lowest_piece": best_piece.name,
                         "unmeasured_pieces": unmeasured, "roles": roles},
                population=f"{len(body)} placed build pieces in this body"))
        if swallowed:
            swallowed.sort(key=lambda t: -t[0])
            d, s = swallowed[0]
            out.append(Finding(
                check="buried", ok=False,
                message=(f"{label}: {len(swallowed)} of {len(body)} pieces "
                         f"lie COMPLETELY under the applied surface, worst "
                         f"{s.name} whose OWN TOP is {d:.3f} m below the "
                         f"lowest ground anywhere under it"),
                where=(round(s.pos[0], 1), round(s.pos[1], 2),
                       round(s.pos[2], 1)),
                measured=round(d, 3), limit=BURIED_TOL_M,
                numbers={"buried_pieces": len(swallowed),
                         "pieces": len(body), "worst_m": round(d, 3),
                         # EVERY BURIED PIECE, NAMED, NOT JUST THE WORST.
                         # A builder cannot act on "16 of 30 pieces are
                         # buried": the repair is per PIECE, and the only
                         # durable key for one is prefab + FULL 3D POSITION
                         # (ZDO ids are reassigned on world load).  Naming
                         # them here makes this finding its own repair plan
                         # and keeps the repair driven by the gate's
                         # measurement instead of a second derivation of it
                         # that could disagree.
                         "buried_at": [
                             dict(prefab=p.name, depth_m=dd,
                                  pos=[round(p.pos[0], 3), round(p.pos[1], 3),
                                       round(p.pos[2], 3)])
                             for dd, p in swallowed],
                         # THE CLAIMANT OF A CONTEST OVER THIS GROUND, NAMED
                         # BY PREFAB AND PIVOT, so `termination.own_blockers`
                         # can ask the ledger whose piece is buried as well
                         # as whose write buries it.  MEASURED on drive 3:
                         # T6's roadside groundtorch (our lamps, seq 2716)
                         # under T6's own road write (seq 1951), classed 1B
                         # as if two strangers disagreed.
                         "claimant_pieces": [
                             dict(prefab=p.name, x=round(p.pos[0], 4),
                                  y=round(p.pos[1], 4), z=round(p.pos[2], 4))
                             for _dd, p in swallowed]},
                population=f"{len(body)} placed build pieces in this body"))
        if unmeasured and judge_support:
            out.append(Finding(
                check="grounding_coverage", ok=False, advisory=True,
                message=(f"{label}: {unmeasured} of {len(body)} pieces got no "
                         "ground reading and were not judged"),
                where=(cx, cz),
                numbers={"unmeasured": unmeasured, "pieces": len(body)}))
    return out


# ---------------------------------------------------------------------------
# WHEN A PAD'S POPULATION IS EMPTY - WHICH OF FOUR STATES IT IS
# ---------------------------------------------------------------------------
def _empty_population(sid: str, p: dict, ground: dict, answers: dict,
                      n_pop: int):
    """A pad record with no sample to measure, CLASSIFIED against the ground.

    THE SHAPE THIS REPLACES.  Seven records on this chain reported "0 of 676
    / 0 of 272 / 0 of 256 / 0 of 441 samples in its population and no applied
    reading for any of them - unmeasured".  The second clause is VACUOUS: the
    population was empty, so the surface was never asked.  `0 of 441` and
    `441 of 441 dead flat` are the same sentence in that finding, and the
    same sentence refused `vestvik-villa-3` for four hours over a pad that was
    288 of 288 on target.  An absent measurement reading as an absent world is
    the defect class this gate exists to stop producing, so the rectangle is
    READ HERE even though nothing in the population survives to be read.

    FOUR STATES, and the order is the order of severity:

      UNWRITTEN   no rectangle at all - the record declares no centre and no
                  `spawn_plan` anchor names its site - and it owns no sample
                  it wrote.  There is nothing to measure and nothing to
                  excuse it.  REFUSED.
      UNREADABLE  the rectangle exists and the applied-surface instrument
                  returns no reading for some of it.  That is a hole in the
                  MEASUREMENT, not a statement about the ground, and it is
                  refused with the count and the denominator so it can never
                  again read as "fine".
      UNANSWERED  readable, but some samples of its own rectangle lie inside
                  NO current pad record's rectangle.  `check_pad_coverage`
                  only judges current records, so those samples are ground
                  this record promised and nobody now answers for.  REFUSED.
      SUPERSEDED  readable in full, and every sample is inside the rectangle
       IN FULL    of a CURRENT record that is therefore judged by
                  `check_pad_coverage`.  This record's promise was entirely
                  replaced; it retains no skirt because there is none left to
                  retain.  The ground is still MEASURED and REPORTED - spread,
                  and the worst offset from this record's own target - so the
                  finding states what is there rather than that nothing was
                  looked at.  NOT a refusal, and only reachable for a
                  superseded record: a current record cannot supersede itself.

    MEASURED on Ulfsland at save 2a6940a934b5, all seven of them, 2,333
    distinct lattice samples: 2,333 readable, 0 unreadable, 0 unowned, 2,333
    inside a current record's rectangle.  Every one is SUPERSEDED IN FULL, and
    six of the seven stand on ground with spread 0.000 m.  `wt-peak@seq545`'s
    441 samples - the `0 of 441` - read 96.656 m dead flat, which is its own
    site's later record seq 3150's target; the 5.654 m "offset" is the
    distance from a promise that record replaced.
    """
    if _PAD_EMPTY_BLANKET:                     # the shipped sentence, verbatim
        return Finding(
            check="pad_flatness", ok=False, advisory=not p["current"],
            message=(f"pad {sid}: {n_pop} of {len(p['rect'] or ())} samples "
                     "in its population and no applied reading for any of "
                     "them - unmeasured"),
            numbers={"population": n_pop, "owned_samples": len(p["own"]),
                     "rect_samples": (len(p["rect"])
                                      if p["rect"] is not None else None)})
    rect = list(p["rect"] or ())
    read = [(ground[gkey(x, z)], x, z) for (x, z) in rect
            if ground.get(gkey(x, z)) is not None]
    if _PAD_UNREAD_IS_TARGET:                  # an absent reading as the datum
        read = [(ground.get(gkey(x, z), p["target_y"]), x, z)
                for (x, z) in rect]
    unreadable = len(rect) - len(read)
    unowned = sum(1 for w in rect if w not in p["rect_owner"])
    # ANSWERED BY SOMEBODY ELSE, AND ONLY BY SOMEBODY ELSE.  A current record
    # answering for its own rectangle is not an escape from its own verdict,
    # so this record is excluded from its own answerers.
    #
    # AND THE ESCAPE IS A DECLARED RECTANGLE, NEVER BOOKKEEPING.  "somebody
    # else's bytes hold this sample" is satisfied by a road, by a retired
    # record, by anything that ever wrote - and none of those is a pad promise
    # anybody is now judged against.  `answers` is built from CURRENT pad
    # records' own rectangles, which is exactly the population
    # `check_pad_coverage` runs over, so a sample excused here is a sample
    # refused there if it is wrong.
    elsewhere = [w for w in rect if [k for k in answers.get(w, ()) if k != sid]]
    if _PAD_ANSWER_BY_OWNERSHIP:
        elsewhere = [w for w in rect
                     if (p["rect_owner"].get(w) or (None, None, sid))[2] != sid]
    holders = collections.Counter(f"{t[2]}@seq{t[0]}"
                                  for t in p["rect_owner"].values())
    answerers = collections.Counter(
        k for w in elsewhere for k in answers.get(w, ()) if k != sid)
    num = {"population": n_pop, "owned_samples": len(p["own"]),
           "rect_samples": len(rect) if p["rect"] is not None else None,
           "rect_readable": len(read), "rect_unreadable": unreadable,
           "rect_unowned": unowned,
           "rect_answered_by_a_current_record": len(elsewhere),
           "seq": p["seq"], "target_y": p["target_y"],
           "held_by": dict(holders), "answered_by": dict(answerers)}
    # A SUPERSEDED RECORD'S SKIRT IS ADVISORY; A HOLE IN THE MEASUREMENT IS
    # NOT.  `advisory=not current` is right for "this replaced record's own
    # surviving samples are a little off its own replaced target" - the
    # promise no longer governs.  It is WRONG for the three refusals below:
    # "nothing declares a rectangle here", "the instrument returns no reading
    # for this ground" and "no current record answers for this ground" are
    # statements about the POPULATION, not about whose promise is current,
    # and a superseded record is exactly where they hide.  Six of the seven
    # records that carried the old blanket sentence were superseded, so an
    # advisory-only classification would have moved the silence rather than
    # ended it.
    adv = not p["current"]
    where = (f"its {p['pad_w']} x {p['pad_d']} m rectangle at centre "
             f"({p['centre'][0]:g}, {p['centre'][1]:g})"
             if p["rect"] else "no rectangle")
    if not rect:
        return Finding(
            check="pad_flatness", ok=False,
            message=(f"pad {sid}: UNWRITTEN - it declares no rectangle "
                     f"({p['centre_src']}) and owns none of the "
                     f"{p['wrote']} samples it wrote, so there is no "
                     "population and no ground this record can be judged "
                     "over"),
            numbers=num,
            population="0 samples, and 0 is the whole of what it declared")
    if unreadable:
        bad = [w for w in rect if ground.get(gkey(*w)) is None]
        return Finding(
            check="pad_flatness", ok=False,
            message=(f"pad {sid}: UNREADABLE - the applied surface answers "
                     f"for {len(read)} of the {len(rect)} samples of "
                     f"{where}; {unreadable} of them have no reading at all, "
                     f"first at ({bad[0][0]:g}, {bad[0][1]:g}).  This is a "
                     "hole in the measurement, not a statement about the "
                     "ground"),
            where=(round(bad[0][0], 1), round(bad[0][1], 1)),
            measured=float(unreadable), limit=0.0, numbers=num,
            population=(f"{len(read)} of the {len(rect)} lattice samples of "
                        f"{where} could be read"))
    if len(elsewhere) < len(rect):
        n = len(rect) - len(elsewhere)
        return Finding(
            check="pad_flatness", ok=False,
            message=(f"pad {sid}: UNANSWERED - it owns none of the "
                     f"{len(rect)} samples of {where}, and {n} of them are "
                     f"inside NO current pad record's rectangle, so no pad "
                     f"verdict is taken over them at all "
                     f"(held today by {dict(holders) or 'nobody'})"),
            measured=float(n), limit=0.0, numbers=num,
            population=(f"{len(rect)} lattice samples of {where}, of which "
                        f"{len(elsewhere)} are inside a current pad record's "
                        "own rectangle and judged there"))
    lo, hi = min(read), max(read)
    off = max(read, key=lambda t: abs(t[0] - p["target_y"]))
    return Finding(
        check="pad_flatness", ok=True, advisory=True,
        message=(f"pad {sid}: REPLACED IN FULL - it retains none of the "
                 f"{len(rect)} samples of {where}; all {len(elsewhere)} are "
                 f"inside the rectangle of a current record "
                 f"({', '.join(f'{k} {n}' for k, n in answerers.most_common(3))})"
                 f" and are judged there.  MEASURED over that rectangle "
                 f"anyway: {len(read)} of {len(rect)} readable, spread "
                 f"{hi[0] - lo[0]:.3f} m, worst offset from THIS record's "
                 f"own {'superseded ' if adv else ''}target_y "
                 f"{p['target_y']:.3f} is "
                 f"{abs(off[0] - p['target_y']):.3f} m"),
        where=(round(off[1], 1), round(off[0], 3), round(off[2], 1)),
        measured=round(max(hi[0] - lo[0], abs(off[0] - p["target_y"])), 3),
        limit=p["tol_m"],
        numbers=dict(num, spread_m=round(hi[0] - lo[0], 3),
                     offset_m=round(abs(off[0] - p["target_y"]), 3),
                     low=[round(lo[1], 1), round(lo[0], 3), round(lo[2], 1)],
                     high=[round(hi[1], 1), round(hi[0], 3),
                           round(hi[2], 1)]),
        population=(f"all {len(rect)} lattice samples of {where}, read back "
                    f"off the save - NOT this record's own population, which "
                    f"is {n_pop}"))


# ---------------------------------------------------------------------------
# CHECK S2 - PAD FLATNESS, over the rectangle the pad write PROMISED
# ---------------------------------------------------------------------------
def check_flatness(pads: dict, ground: dict) -> list:
    """Is a pad asked to be dead flat actually dead flat?

    Measured over THE RECTANGLE THE WRITE ITSELF DECLARED - `params.centre`
    with `pad_w x pad_d` and its own apron - against the applied surface read
    back from the save, and held to the tolerance the write itself declared it
    had met.  Two numbers are reported and they answer different questions:
    SPREAD (max - min) is whether the pad is flat, and OFFSET (worst |applied
    - target_y|) is whether it is flat AT THE HEIGHT IT PROMISED.  A pad can
    be perfectly flat 2 m below its own datum, and then every building on it
    is 2 m in the air - which is how the grounding defect and this one are the
    same defect seen twice.

    A record that does not declare a centre cannot have its promise separated
    from the spill of the compiler it shares, and is judged over the samples
    it wrote and still owns WITH THAT STATED in its population.  All 62
    pre-2026-09-19 pad records on this chain are in that position;
    `ledger/schema.py:964` refuses a new one.

    A SUPERSEDED record does not carry the verdict.  Its surviving samples are
    reported as a skirt, against ITS OWN target, because that is the promise
    they were levelled to.

    AND AN EMPTY POPULATION IS NOT AN EMPTY WORLD.  "0 of 441 samples and no
    applied reading for any of them - unmeasured" is one sentence covering
    four different states, only two of which are defects, and the ground it
    describes had never been looked at: the population was empty, so nothing
    ASKED.  `_empty_population` below asks - over the rectangle itself - and
    the finding then names which of the four it is.  See its own docstring
    for the classification and for what each state was MEASURED to be on
    this world.
    """
    out = []
    # WHICH RECORD ANSWERS FOR EACH SAMPLE TODAY.  `check_pad_coverage` runs
    # over CURRENT records only, so a superseded record may be excused as
    # "somebody else's promise now" ONLY if that somebody is a current record
    # whose own rectangle contains the sample and which is therefore judged.
    # Bookkeeping alone is never the escape: see `_empty_population`.
    answers: dict[tuple, list] = collections.defaultdict(list)
    for k, q in pads.items():
        if q["current"] and q["rect"]:
            for w in q["rect"]:
                answers[w].append(k)
    for sid, p in sorted(pads.items()):
        # THE CURRENT RECORD CARRIES THE SITE'S PROMISE, SO IT IS JUDGED
        # OVER THE SITE'S SAMPLES.  A superseded record is still judged over
        # what IT owns, because its finding is explicitly a skirt reported
        # against its own target.
        pop = (p["inside_site"] if p["current"] else p["inside"]) \
            if p["rect"] is not None else p["own"]
        src = (f"the {p['pad_w']} x {p['pad_d']} m rectangle at centre "
               f"({p['centre'][0]:g}, {p['centre'][1]:g}) + apron "
               f"{float(p['apron_m'] or 0.0):g} m, from {p['centre_src']}"
               if p["rect"] is not None else
               f"the samples this write set and still owns - its rectangle "
               f"is {p['centre_src']}, so its promise cannot be separated "
               f"from the spill of the compiler it shares with its "
               f"neighbours")
        ys = []
        for (x, z) in pop:
            g = ground.get(gkey(x, z))
            if g is not None:
                ys.append((g, x, z))
        if not ys:
            out.append(_empty_population(sid, p, ground, answers, len(pop)))
            continue
        lo = min(ys)
        hi = max(ys)
        spread = hi[0] - lo[0]
        off = max(ys, key=lambda t: abs(t[0] - p["target_y"]))
        offset = abs(off[0] - p["target_y"])
        worst = max(spread, offset)
        ok = worst <= p["tol_m"] + 1e-9
        state = "" if p["current"] else " [SUPERSEDED - skirt, advisory]"
        msg = (f"pad {sid}{state}: spread {spread:.3f} m, worst offset from "
               f"its own target_y {p['target_y']:.3f} is {offset:.3f} m, over "
               f"{len(ys)} samples of {src} (tolerance {p['tol_m']:.3f} m, "
               f"{p['tol_src']})")
        out.append(Finding(
            check="pad_flatness", ok=ok, advisory=not p["current"],
            message=msg,
            where=(round(off[1], 1), round(off[0], 3), round(off[2], 1)),
            measured=round(worst, 3), limit=p["tol_m"],
            numbers={"spread_m": round(spread, 3),
                     "offset_m": round(offset, 3),
                     "target_y": p["target_y"], "seq": p["seq"],
                     "declares_centre": p["centre_declared"],
                     "centre": p["centre"], "centre_src": p["centre_src"],
                     "rect_samples": (len(p["rect"]) if p["rect"] is not None
                                      else None),
                     "samples_owned": len(p["own"]),
                     "samples_in_rect": len(p["inside"]),
                     # THE POPULATION THIS VERDICT WAS ACTUALLY TAKEN OVER,
                     # which for a current record is the SITE's samples in
                     # the rectangle and not just this record's.
                     "samples_in_rect_site": len(p["inside_site"]),
                     "samples_read": len(ys),
                     "samples_written": p["wrote"],
                     "lost_to_later_writer": p["wrote"] - len(p["own"]),
                     "outside_its_own_rectangle":
                         len(p["own"]) - len(p["inside"]),
                     "low": [round(lo[1], 1), round(lo[0], 3),
                             round(lo[2], 1)],
                     "high": [round(hi[1], 1), round(hi[0], 3),
                              round(hi[2], 1)]},
            population=f"the {len(pop)} lattice samples of {src}"))
    return out


# ---------------------------------------------------------------------------
# CHECK S2b - PAD COVERAGE: a sample nobody wrote is still inside the pad
# ---------------------------------------------------------------------------
def check_pad_coverage(pads: dict, ground: dict) -> list:
    """Is EVERY lattice sample inside the declared rectangle at the target?

    `check_flatness` asks whether the pad is flat; this asks whether the pad
    is all there.  They are different questions because the two populations
    are different, and the difference is exactly where a defect hides: a
    sample the writer never wrote is in nobody's written set, so no check that
    starts from "the samples this pad wrote" can see it, however carefully it
    then measures them.

    MEASURED on Ulfsland, and this check is here because the world was in this
    state.  `terraform/flatten.py` selected samples with
    `abs(wx - cx) > half_w -> continue`; a solved centre is a decimal that
    binary cannot hold, so 886.6 - 879.0 came out 7.6000000000000085 against a
    half_d of 7.6 and the whole edge row went.  Four first writes each lost
    one full edge row or column - `stenvik-house-1` z=879 (20 samples),
    `stenvik-cottage-3` x=552 (12), `stenvik-hut-1` z=892 (10),
    `vestvik-villa-2` x=-876 (17).  Read back off the save, 54 of those 59
    samples are more than 0.10 m off their pad's own target and the worst is
    1.589 m, under a building, inside a rectangle the record says it levelled.
    Every one of them was invisible to this section.

    THE POPULATION WAS TOO BIG, AND THE DOCSTRING ALREADY SAID SO WHILE THE
    CODE DID THE OPPOSITE.  It read "a sample a LATER writer owns is not this
    pad's failure and is counted as ceded"; nothing ceded anything, and every
    off-target sample in the rectangle was charged to this pad and reported as
    one "this write never wrote at all".

    MEASURED on Ulfsland at save 2c7011d2, partitioning all ten refused pads'
    off-target rectangle samples by who wrote them and who holds them now:
    FIVE of the ten wrote EVERY sample of their own rectangle - 300/300,
    272/272, 256/256 - and their off-target samples had been RE-LEVELLED BY
    SOMEBODY ELSE.  `tree-sth-1`'s 25 samples "worst 0.700 m" read 75.000
    exactly, which is `tree-sth-3`'s target inside `tree-sth-3`'s own
    overlapping rectangle; `vestvik-house-1`'s 20 read 35.690, which is
    `vestvik-stonehouse-1`'s; `tree-near-1`'s 16 read 36.160, `tree-near-2`'s;
    `stenvik-cottage-2`'s 10 read 47.030, `stenvik-hall-1`'s; and
    `stenvik-house-3`'s 18 are `road_T8_stenvik_wttown`'s carriageway.  Not
    one of them is a hole in a pad, and no fill is the right answer to any of
    them - Stenvik is a dense village whose pad rectangles overlap by
    authorship, and the later pad's datum governs the overlap.

    SO THE ESCAPE IS A POSITIVE MEASUREMENT ABOUT THE GROUND, NOT ABOUT
    BOOKKEEPING.  "Somebody later claims it" would have been the dangerous
    form: a sample THIS pad dropped, that some distant later write happens to
    hold, would have gone quiet - trading a population that is too big for one
    that is too small, which is the harder of the two to see.  A sample is
    ceded only when BOTH hold:

      1. THIS WRITE ACTUALLY WROTE IT.  A sample it never set is its own
         coverage gap however the ground now reads, which is what keeps the
         four dropped edge rows and `wt-south`'s three unwritten boundary
         samples refused.
      2. THE GROUND KEEPS A DECLARED PROMISE THAT IS NOT THIS ONE - some
         other pad record whose OWN rectangle contains the sample and whose
         OWN target_y the reading meets within that record's OWN tolerance;
         or the sample is held by a ROAD, whose bed is declared by a
         centreline and measured by the `roads` section over its own
         population.

    Run over the same ten pads, that leaves FIVE refusing, and they are
    exactly the two `terraform/flatten.py` defects: four dropped edge rows
    (`stenvik-house-1` z=879 17, `vestvik-villa-2` x=-876 17,
    `stenvik-cottage-3` x=552 11, `stenvik-hut-1` z=892 9) and `wt-south`'s
    21-sample z=32 row, written into zones (8,1)/(9,1) as row 0 while the
    engine answers it from (8,0)/(9,0) row 64, which nothing wrote.
    """
    promises = pad_promises(pads)
    out = []
    for sid, p in sorted(pads.items()):
        if p["rect"] is None or not p["current"]:
            continue
        tag = (p["seq"], "site_pad", p["site_id"])
        wrote = p["rect_wrote"]
        owners = p["rect_owner"]
        bad = []
        ceded: dict[str, int] = {}
        unread = 0
        for (x, z) in p["rect"]:
            g = ground.get(gkey(x, z))
            if g is None:
                unread += 1
                continue
            if abs(g - p["target_y"]) <= p["tol_m"] + 1e-9:
                continue
            o = owners.get((x, z))
            if (x, z) in wrote and o is not None and o != tag:
                kept = [k for k, r, t, tol in promises
                        if k != sid and (x, z) in r and abs(g - t) <= tol + 1e-9]
                if kept:
                    ceded[f"kept {kept[0]}'s own datum"] = (
                        ceded.get(f"kept {kept[0]}'s own datum", 0) + 1)
                    continue
                if (o[1] or "") in ROAD_ROLES:
                    ceded[f"{o[2]} carriageway"] = (
                        ceded.get(f"{o[2]} carriageway", 0) + 1)
                    continue
            bad.append((abs(g - p["target_y"]), x, g, z, (x, z) in wrote))
        cd = sum(ceded.values())
        cwhy = ("; ceded " + ", ".join(f"{n} {k}" for k, n in
                                       sorted(ceded.items(), key=lambda t: -t[1]))
                if ceded else "")
        if not bad:
            out.append(Finding(
                check="pad_coverage", ok=True,
                message=(f"pad {sid}: all {len(p['rect']) - unread - cd} "
                         f"readable samples of its declared "
                         f"{p['pad_w']} x {p['pad_d']} m rectangle that it "
                         f"still answers for are within {p['tol_m']:.3f} m of "
                         f"target_y {p['target_y']:.3f}{cwhy}"),
                numbers={"rect_samples": len(p["rect"]), "unread": unread,
                         "off_target": 0, "ceded": cd,
                         "ceded_to": ceded}))
            continue
        bad.sort(key=lambda t: -t[0])
        d, bx, by, bz, _was_written = bad[0]
        never = sum(1 for t in bad if not t[4])
        out.append(Finding(
            check="pad_coverage", ok=False,
            message=(f"pad {sid}: {len(bad)} of {len(p['rect'])} samples "
                     f"inside its own {p['pad_w']} x {p['pad_d']} m "
                     f"rectangle are off its own target_y "
                     f"{p['target_y']:.3f} by more than {p['tol_m']:.3f} m, "
                     f"worst {d:.3f} m; {never} of them this write never "
                     f"wrote at all (centre from {p['centre_src']}){cwhy}"),
            where=(round(bx, 1), round(by, 3), round(bz, 1)),
            measured=round(d, 3), limit=p["tol_m"],
            numbers={"rect_samples": len(p["rect"]), "off_target": len(bad),
                     "never_written": never, "unread": unread,
                     "ceded": cd, "ceded_to": ceded,
                     "seq": p["seq"], "target_y": p["target_y"],
                     "declares_centre": p["centre_declared"],
                     "centre": p["centre"], "centre_src": p["centre_src"],
                     "worst_m": round(d, 3)},
            population=(f"every lattice sample inside centre "
                        f"({p['centre'][0]:g}, {p['centre'][1]:g}) "
                        f"{p['pad_w']} x {p['pad_d']} m + apron "
                        f"{float(p['apron_m'] or 0.0):g} m, written or not, "
                        f"MINUS {cd} this write set and another declared "
                        f"write's own kept datum now stands on; the centre is "
                        f"{p['centre_src']}")))
    return out


# ---------------------------------------------------------------------------
# CHECK S3 - SHELTER, modelled as SLOPING PLANES
# ---------------------------------------------------------------------------
def _is_roof(name: str) -> bool:
    n = name.lower()
    return "roof" in n or "_shingle" in n or "shingle_" in n


def deck_columns(body: list, cols: Columns, step: float = 1.0) -> list:
    """Every 1 m column over a body that a player could stand on, with the
    height of the floor there.

    The DECK is what shelter is for.  Sampling the roof instead would ask
    whether the roof covers the roof."""
    out = []
    xs0 = min(s.xmin for s in body)
    xs1 = max(s.xmax for s in body)
    zs0 = min(s.zmin for s in body)
    zs1 = max(s.zmax for s in body)
    floors = [s for s in body if s.verts and s.level_top() is not None
              and not _is_roof(s.name) and cols.is_deck(s.name)]
    if not floors:
        return out
    grid = I.Grid(floors, cell=4.0)
    x = math.floor(xs0) + 0.5
    while x <= xs1:
        z = math.floor(zs0) + 0.5
        while z <= zs1:
            top = None
            for s in grid.near(x, z, 0.0):
                if not (s.xmin <= x <= s.xmax and s.zmin <= z <= s.zmax):
                    continue
                if not s.covers(x, z):
                    continue
                sp = cols.span(s, x, z)
                if sp is None:
                    continue
                t = s.level_top()
                if t is None or abs(sp[1] - t) > 0.15:
                    continue        # not a level standable top in this column
                if top is None or sp[1] > top:
                    top = sp[1]
            if top is not None:
                out.append((x, z, top))
            z += step
        x += step
    return out


# A CRACK IS NOT A HOLE.  A deck column is a LINE, and a line can land in a
# seam between two collider parts that no disc of any size fits through.
# MEASURED on `sandbox-portal-hub` (salty-dick-portal-hub-final), the one
# column that refused the body: local (5.5, 18.5) misses the
# `darkwood_roof_ocorner` over it by 0.002 m - its own part 4 - and lies
# inside that piece's own footprint; mapped at 2 cm over the metre around
# it, the uncovered set is 15 of 2601 samples (0.006 m2), a diagonal seam
# 2-4 cm wide.  So an uncovered column is re-asked on a ring of this radius
# in the same eight directions as `_RAYS`, and it is open only if the WHOLE
# ring is: an opening must admit a disc SHELTER_CRACK_M across at its own
# sample.  The radius is the section's own "same place" (`DUP_TOL_M`, the
# 5 cm cube two pieces must share to be one): edges nearer than that to one
# line meet on it.  A hole of `lh-west`'s size - ten columns with 48 panels
# round them - is open a metre from any edge and is untouched.
SHELTER_CRACK_M = DUP_TOL_M
# The shipped test, restorable by a neuter: the column is a line.
_SHELTER_COLUMN_IS_A_LINE = False

# A HOLE MUST ADMIT THE WALKER.  A deck column is a place a walker STANDS,
# and the verdict set (walled, open, roof on all eight sides) is only a
# hole he stands under the sky in if the opening over that column's own
# 1 m deck cell admits his capsule: a disc of the walker's radius, read off
# `Player` in the same corpus the columns are cut from (0.490 m, the figure
# `spawn.probe_seat` and `blueprints/reseat.py` read) - DERIVED, not typed.
# MEASURED on `hs_plains_ulf_the_builder_lighthouse.blueprint`, the three
# columns the 5 cm ring still called holes at top 19.5 are slots between
# darkwood beams and arches of 0.27, 0.50 and 0.20 m2 within a metre, with
# cover 0.10, 0.22 and 0.08 m from the column; no disc of the walker's
# radius fits in any of them.  The 2 x 3.9 m gap of
# `deck_open_to_the_sky_through_a_hole_in_the_roof` admits it and is still
# refused.  Asked only of the verdict set, so `open` and `walled` counts are
# the shipped ones.
# SHELTER_HOLE_DEPENDS_ON: "walker".  Neuter "any": every column of the
# verdict set is a hole, whatever fits in it.
_SHELTER_HOLE_MODE = "walker"
# How finely a candidate disc centre is searched over the deck cell, and
# how finely its rings are asked.  DESIGN: a tenth of the 1 m deck cell.
SHELTER_DISC_STEP_M = 0.1


def walker_radius_m(cols: "Columns") -> float:
    """The walker's capsule radius off `Player` in the columns' own corpus.
    A corpus with no `Player` is REFUSED, never guessed."""
    parts = cols.geom.get("Player")
    if not parts:
        raise ValueError(
            "check_shelter: the corpus carries no `Player`, so the walker's "
            "capsule - what a hole must admit - is unmeasured")
    vs = [v for p in parts for v in I.part_vertices(p)]
    return max(max(abs(v[0]) for v in vs), max(abs(v[2]) for v in vs))


def admits_walker(x: float, z: float, top: float, cover_at,
                  radius: float, cell: float = 1.0) -> bool:
    """Does the opening over deck cell (x, z) hold a disc of `radius`?

    Disc centres are searched over the cell at SHELTER_DISC_STEP_M; a disc
    is open when its centre and every ring (every step out to `radius`, and
    `radius` itself) is open in the eight `_RAYS` directions."""
    step = SHELTER_DISC_STEP_M
    rings = [step * k for k in range(1, int(radius / step + 1e-9) + 1)]
    if not rings or rings[-1] < radius - 1e-9:
        rings.append(radius)
    n = int(cell / 2 / step + 1e-9)
    for i in range(-n, n + 1):
        for j in range(-n, n + 1):
            cx, cz = x + i * step, z + j * step
            if cover_at(cx, cz, top) is not None:
                continue
            if all(cover_at(cx + ux * r, cz + uz * r, top) is None
                   for r in rings for ux, uz in _RAYS):
                return True
    return False


# Eight horizontal directions.  Enclosure is asked in eight because four
# lets a walker stand in an open corner and be called indoors.
_RAYS = ((1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0),
         (0.70711, 0.70711), (-0.70711, 0.70711),
         (0.70711, -0.70711), (-0.70711, -0.70711))
HEAD_M = 1.0          # where a wall has to be to be a wall you cannot see over
ENCLOSURE_REACH_M = 24.0


def is_enclosed(x: float, z: float, top: float, pieces: list, cols: Columns,
                reach: float = ENCLOSURE_REACH_M) -> bool:
    """Is this deck column INSIDE the building?

    Every one of eight horizontal rays at head height must run into material
    belonging to this body before it leaves the body.  This is the difference
    between a hole in a roof and a terrace, and without it the check refuses
    every watchtower in the world for having a parapet walk: MEASURED on
    Ulfsland, wt-peak has 69 of 110 deck columns open to the sky and 4 of
    them are enclosed.  A check that called all 69 a defect would be asking
    for a roof over a balcony, and the next builder would learn to switch the
    gate off.

    THE RAY IS CLIPPED AGAINST EACH PIECE'S FOOTPRINT, NOT MARCHED IN STEPS.
    A marched ray with a 0.5 m stride steps straight over a 0.24 m wall and
    reports the room open - which is the same class of miss as a vertex test
    that cannot see a wall crossing a road diagonally.  `Solid.lateral_span`
    already solves the line/footprint clip exactly, so the wall's own
    thickness cannot fall between two samples."""
    hy = top + HEAD_M
    near = [s for s in pieces
            if s.ymin - 0.05 <= hy <= s.ymax + 0.05 and s.verts]
    for ux, uz in _RAYS:
        blocked = False
        for s in near:
            sp = s.lateral_span(x, z, ux, uz, reach)
            if sp is None:
                continue
            t0, t1 = sp
            if t1 <= 0.05:
                continue                     # entirely behind the walker
            tm = (max(t0, 0.05) + t1) / 2.0
            q = cols.span(s, x + ux * tm, z + uz * tm)
            if q is not None and q[0] - 0.05 <= hy <= q[1] + 0.05:
                blocked = True
                break
        if not blocked:
            return False
    return True


def roof_enclosed(x: float, z: float, top: float, roofs: list,
                  cols: Columns, reach: float = ENCLOSURE_REACH_M) -> bool:
    """Is this column IN a roof - that is, is there roof on every side of it?

    A HOLE IN A ROOF AND AN OPEN COURTYARD ARE BOTH WALLED AND BOTH OPEN TO
    THE SKY, AND ONLY ONE OF THEM IS A DEFECT.  `is_enclosed` is the right
    question about walls and the wrong one on its own: its reach is 24 m with
    no bound on room size, so in a 40 m keep EVERY interior point has
    material on all eight sides and a courtyard reads as a room.  That is the
    same mistake as calling a parapet walk a hole, one storey down.

    MEASURED from the four blueprint files that produce 11 of this section's
    12 shelter refusals, with no world, asking both questions of every
    walled-and-open deck column:

      file                                  panels  deck  open  walled  ROOFED
      hs_ashlands_neletit_portalhubtower       232   872   248       7       0
      outposttower                              21   113    65       8       0
      hs_mistlands_sheepshank_trophytowerv2      8   376   359     106       0
      hs_plains_ulf_the_builder_lighthouse      48   164    44      10      10

    The two populations SEPARATE COMPLETELY - 0, 0, 0 against 10 of 10 - and
    the separation is the architecture.  `lh-west` is a real hole: ten columns
    at deck tops 19.500 and 25.133 with 48 panels all round them.
    `trophytowerv2` is a TOWER - EIGHT panels over 376 deck columns at 20
    distinct storey heights from 0.100 to 19.427, 359 of them open.  Calling
    106 of those "a hole in the roof" and calling the building roofless are
    the same sentence and only the second is useful.  `outposttower`'s 8 sit
    on an open gallery strip; the portal hub's 7 sit at 9.000 on an eave
    ledge whose roof begins at 8.962.

    THREE OTHER CANDIDATES WERE MEASURED AND REJECTED, recorded so nobody
    spends the evening on them again:

      the roof's convex XZ hull        DOES NOT SEPARATE - eight scattered
                                       panels enclose 22 of sudrberg's 83
                                       courtyard columns by up to 2.498 m.  A
                                       discriminator that does not separate
                                       is a knob.
      the patch ringed by covered deck refuses NOTHING, lh-west included:
                                       every open patch at a floor edge has
                                       off-deck neighbours, so the ring never
                                       closes.
      walk out until roof or floor end refuses EVERYTHING, 106 of 106 -
                                       `deck_columns` keeps ONE top per
                                       column, so crossing a courtyard
                                       beside a storey 18 m up reads as
                                       running off the floor.

    A GAP THAT SPANS THE ROOF'S WHOLE DEPTH IS A VENT, NOT A LEAK, and this
    predicate says so by construction - there is no roof fore and aft of a
    full-depth slot, so it cannot be enclosed by roof.  That is asserted in
    both directions by `a_ridge_slot_across_the_whole_roof_depth` (ACCEPT) and
    `deck_open_to_the_sky_through_a_hole_in_the_roof` (REFUSE), and the
    second needs a roof DEEP enough to have material on all sides of its own
    hole - which is what lh-west's 48 panels are and what a two-course 6 x 2 m
    gable can never be.

    THE RAY IS CAST IN THE ROOF'S OWN VERTICAL BAND, not at head height: a
    panel over the NEXT column sits above the walker's head, so a head-height
    ray passes under every tile and would answer "no roof" for a genuine
    hole.  `Solid.lateral_span` does the exact footprint clip, so a 0.126 m
    tile cannot fall between two samples.
    """
    if not roofs:
        return False
    if max(s.ymax for s in roofs) <= top:
        return False
    band = [s for s in roofs if s.ymax >= top]
    for ux, uz in _RAYS:
        blocked = False
        for s in band:
            sp = s.lateral_span(x, z, ux, uz, reach)
            if sp is None:
                continue
            t0, t1 = sp
            if t1 <= 0.05:
                continue
            tm = (max(t0, 0.05) + t1) / 2.0
            q = cols.span(s, x + ux * tm, z + uz * tm)
            if q is not None and q[1] > top:
                blocked = True
                break
        if not blocked:
            return False
    return True



def check_shelter(bodies_named, cols: Columns) -> list:
    """Is the ENCLOSED deck of a roofed body actually under something?

    A BOX CANNOT TELL A GABLE FROM AN X.  Two panels leaning apart occupy the
    same axis-aligned volume as two leaning together, and a bounding box is
    41 % wider than the footprint of anything placed at an angle, so a
    box-based coverage test reports material over columns that have none.
    Here the column at each deck sample is intersected with each candidate
    piece's own ROTATED SOLID.

    THREE POPULATIONS, THREE DIFFERENT CLAIMS, AND THE THIRD CARRIES THE
    VERDICT.  OPEN is every standable deck column with no material above it.
    WALLED is the subset a walker cannot walk out of in eight directions
    without passing through a wall.  A HOLE IN THE ROOF is the subset of
    those with roof material on all eight sides - `roof_enclosed`, where the
    four candidate discriminators and the three rejected ones are recorded
    with their numbers.

    THE FIRST TWO WERE NOT ENOUGH, MEASURED FROM THE FILES WITH NO WORLD.
    Walled-and-open refuses 11 bodies out of four blueprints and only ONE of
    the four has a hole in its roof: `trophytowerv2` is a tower with EIGHT
    panels over 376 deck columns at 20 storey heights, `outposttower`'s 8 sit
    on an open gallery strip, the portal hub's 7 on an eave ledge.  An open
    courtyard and an open storey are correct architecture for the same reason
    a parapet walk is, and a check that refuses them is a check the next
    builder switches off.  `lh-west` IS a hole - ten columns with 48 panels
    all round them - and it is still refused, which is what makes this a
    population fix and not a relaxation.

    IT IS ALSO CATCHABLE BEFORE ANY WORLD IS WRITTEN:
    `blueprints/preflight.py:405::check_shelter` calls this function
    unwrapped over a blueprint's own solids, so a NEW SEED cannot acquire
    this class silently - the emitter refuses the file, and
    `--allow-unsheltered-deck` is the only way past."""
    out = []
    for body, _anchors, label in bodies_named:
        roofs = [s for s in body if _is_roof(s.name) and s.verts]
        if not roofs:
            continue
        deck = deck_columns(body, cols)
        if not deck:
            continue
        grid = I.Grid([s for s in body if s.verts], cell=4.0)
        open_sky = []
        roofed = 0
        cracks = 0

        def cover_at(x, z, top):
            cover = None
            for s in grid.near(x, z, 0.0):
                if s.ymax <= top + 0.05:
                    continue
                sp = cols.span(s, x, z)
                if sp is not None and sp[1] > top + 0.05:
                    cover = s
                    if _is_roof(s.name):
                        break
            return cover
        for (x, z, top) in deck:
            cover = cover_at(x, z, top)
            if cover is None and not _SHELTER_COLUMN_IS_A_LINE:
                # A CRACK IS NOT A HOLE - see `SHELTER_CRACK_M`.
                cover = next((c for ux, uz in _RAYS
                              if (c := cover_at(x + ux * SHELTER_CRACK_M,
                                                z + uz * SHELTER_CRACK_M,
                                                top)) is not None), None)
                cracks += cover is not None
            if cover is None:
                open_sky.append((x, z, top))
            elif _is_roof(cover.name):
                roofed += 1
        solid_pieces = [s for s in body if s.verts]
        walled = [p for p in open_sky
                  if is_enclosed(p[0], p[1], p[2], solid_pieces, cols)]
        inside = [p for p in walled
                  if roof_enclosed(p[0], p[1], p[2], roofs, cols)]
        # A HOLE MUST ADMIT THE WALKER - see `_SHELTER_HOLE_MODE`.
        narrow = []
        if inside and _SHELTER_HOLE_MODE == "walker":
            wr = walker_radius_m(cols)
            narrow = [p for p in inside
                      if not admits_walker(p[0], p[1], p[2], cover_at, wr)]
            inside = [p for p in inside if p not in narrow]
        yard = len(walled) - len(inside) - len(narrow)
        if inside:
            x, z, top = inside[0]
            out.append(Finding(
                check="shelter", ok=False,
                message=(f"{label}: {len(inside)} deck columns are A HOLE IN "
                         f"THE ROOF - walled, open to the sky, and with roof "
                         f"material on all eight sides - under a "
                         f"{len(roofs)}-panel roof ({len(open_sky)} of "
                         f"{len(deck)} deck columns open in all, {roofed} "
                         f"under a roof panel, {yard} walled but outside the "
                         f"roof line - courtyard, open storey or full-depth "
                         f"vent - and the rest terrace)"),
                where=(round(x, 1), round(top, 2), round(z, 1)),
                measured=float(len(inside)), limit=0.0,
                numbers={"inside_and_open": len(inside),
                         "walled_and_open": len(walled),
                         "cracks_not_holes": cracks,
                         "too_narrow_for_the_walker": len(narrow),
                         "walled_outside_the_roof_line": yard,
                         "open": len(open_sky), "deck_columns": len(deck),
                         "deck_under_a_roof_panel": roofed,
                         "roof_panels": len(roofs),
                         "storey_tops": len({round(p[2], 2) for p in deck}),
                         # EVERY HOLE, NOT A SAMPLE.  `holes_at` used to stop
                         # at 12 because it was a display field; it is now the
                         # POPULATION a repair is allowed to act on
                         # (`blueprints/preflight.py::shelter_plan`), and a
                         # repair fed a truncated population would silently
                         # close twelve holes and declare a roof finished -
                         # the exact shape of `upright_plan`'s 17-inverted /
                         # 13-proposed / 16-remaining failure.  Deck columns
                         # are on the half-integer lattice, so 1 decimal is
                         # lossless in x and z; `top` is a report figure.
                         "holes_at": [[round(p[0], 1), round(p[2], 1),
                                       round(p[1], 1)] for p in inside],
                         "worst_at": [round(x, 1), round(z, 1)]},
                population=(f"{len(deck)} standable 1 m deck columns in this "
                            f"body; {len(walled)} are walled and open, and "
                            f"the {len(inside)} of THOSE with roof on all "
                            f"eight sides carry the verdict")))
        elif open_sky:
            out.append(Finding(
                check="shelter", ok=True,
                message=(f"{label}: {len(open_sky)} of {len(deck)} deck "
                         f"columns are open to the sky and not one is inside "
                         f"this body's roof line - "
                         f"{len(open_sky) - yard - len(narrow)} terrace, "
                         f"{yard} walled but outside the roof (courtyard, "
                         f"open storey or full-depth vent), {len(narrow)} "
                         f"under an opening too narrow for the walker"),
                where=(round(open_sky[0][0], 1), round(open_sky[0][1], 1)),
                numbers={"open": len(open_sky), "deck_columns": len(deck),
                         "walled_and_open": len(walled),
                         "cracks_not_holes": cracks,
                         "too_narrow_for_the_walker": len(narrow),
                         "walled_outside_the_roof_line": yard,
                         "deck_under_a_roof_panel": roofed,
                         "inside_and_open": 0}))
    return out


def panel_pitch(s, geom: dict) -> float:
    """This panel's OWN rise over run, from its own rotated geometry."""
    v = I.rise_vector(geom.get(s.name) or [], s.quat)
    if v is None:
        return 0.0
    (rx, ry, rz), _local = v
    run = math.hypot(rx, rz)
    return abs(ry) / run if run > 1e-6 else 0.0


def check_skin(bodies_named, cols: Columns, geom: dict,
               step: float = 0.5) -> list:
    """Is the roof skin CONTINUOUS, or can you see daylight through it?

    The roof is sampled as a HEIGHT FIELD on a 0.5 m lattice - the top of the
    highest roof material in each column - and a step between two adjacent
    columns is a defect only if it is BOTH bigger than this body's own
    steepest panel could account for AND not spanned by anything.  Both
    halves matter:

      THE LIMIT IS MEASURED, NOT CHOSEN.  A 67-degree roof legitimately
      climbs 1.18 m in half a metre.  The allowance is this body's own
      steepest panel's rise-over-run times the sample spacing, plus the
      thickest panel's own thickness for the overlap where two courses lap.
      Picking a number instead would either pass the defect or refuse every
      steep roof in the world.
      A SPANNED STEP IS NOT A HOLE.  A dormer standing on a main roof is a
      step in the height field with a wall under it.  Only a step with
      nothing in the gap is daylight."""
    out = []
    for body, _anchors, label in bodies_named:
        roofs = [s for s in body if _is_roof(s.name) and s.verts]
        if len(roofs) < 2:
            continue
        pitch = max(panel_pitch(s, geom) for s in roofs)
        # THE PANEL'S THICKNESS, NOT ITS RISE.  `ymax - ymin` of a pitched
        # panel is how far it CLIMBS - 1.13 m for a wood_roof - and using
        # that as the lap allowance would let a 1.13 m hole through as
        # "one panel thick".  The thickness is the vertical extent of the
        # solid in a single column, measured at each panel's own centre.
        thick = 0.0
        for s in roofs:
            cx, cz = footprint_centre(s)
            q = cols.span(s, cx, cz)
            if q is not None:
                thick = max(thick, q[1] - q[0])
        if _SKIN_ALLOW_USES_RISE:
            # THE SHIPPED-DEFECT FORM, reachable for a selftest neuter: the
            # panel's RISE standing in for its THICKNESS, which lets a hole
            # exactly one course deep through as "one panel thick".
            thick = max((s.ymax - s.ymin) for s in roofs)
        allow = pitch * step + thick
        rgrid = I.Grid(roofs, cell=4.0)
        agrid = I.Grid([s for s in body if s.verts], cell=4.0)
        x0 = min(s.xmin for s in roofs)
        x1 = max(s.xmax for s in roofs)
        z0 = min(s.zmin for s in roofs)
        z1 = max(s.zmax for s in roofs)
        # THE HEIGHT FIELD KEEPS EVERY LAYER, NOT JUST THE HIGHEST.  Reducing
        # a column to its maximum is the same mistake as reducing a body to
        # its silhouette: a roof surface with something STANDING ON IT reads
        # as one number, and the step off the edge of that something reads as
        # a cliff with nothing under it.  MEASURED on this world: 24 of the 27
        # bodies refused by the max-only rule were refused for exactly that -
        # a `wood_wall_roof_top` ridge board 1.28 m proud of the tiles it
        # stands on, so every one of the five caps on `stenvik-manor-1` raised
        # three "holes" of 1.379-1.603 m at its own edges, 15 in all, on a
        # roof `check_shelter` reports as fully closed.  The midpoint bridging
        # test cannot see it: the midpoint is BESIDE a thin vertical board,
        # not under its top.
        tops: dict[tuple[int, int], tuple[float, float, float]] = {}
        layers: dict[tuple[int, int], list[float]] = {}
        for i in range(int((x1 - x0) / step) + 2):
            x = x0 + i * step
            for j in range(int((z1 - z0) / step) + 2):
                z = z0 + j * step
                ts = []
                for s in rgrid.near(x, z, 0.0):
                    sp = cols.span(s, x, z)
                    if sp is not None:
                        ts.append(sp[1])
                if ts:
                    tops[(i, j)] = (max(ts), x, z)
                    layers[(i, j)] = ([max(ts)] if _SKIN_MAX_ONLY_LAYERS
                                      else ts)
        holes = 0
        spanned = 0
        second = 0
        worst = None
        for (i, j), (t, x, z) in tops.items():
            for di, dj in ((1, 0), (0, 1)):
                k = (i + di, j + dj)
                o = tops.get(k)
                if o is None:
                    continue
                d = abs(t - o[0])
                if d <= allow:
                    continue
                lo, hi = min(t, o[0]), max(t, o[0])
                # A SECOND LAYER IN THE TALLER COLUMN IS THE SKIN ITSELF.
                # If the taller column ALSO carries roof material at the
                # shorter column's level, the skin is continuous across the
                # pair and the extra height is something resting on it.  A
                # genuine hole has NOTHING at the lower level in the taller
                # column - which is exactly the lifted-course fixture and
                # exactly the sawtooth a 180-degree flip makes.
                tall = k if o[0] > t else (i, j)
                if any(abs(v - lo) <= allow for v in layers[tall]):
                    second += 1
                    continue
                mx, mz = (x + o[1]) / 2.0, (z + o[2]) / 2.0
                bridged = False
                if not _SKIN_NO_SPAN_TEST:
                    for s in agrid.near(mx, mz, 0.0):
                        sp = cols.span(s, mx, mz)
                        if (sp is not None and sp[1] > lo + 0.05
                                and sp[0] < hi - 0.05):
                            bridged = True
                            break
                if bridged:
                    spanned += 1
                    continue
                holes += 1
                if worst is None or d > worst[0]:
                    worst = (d, mx, mz, t)
        if holes:
            d, mx, mz, t = worst
            out.append(Finding(
                check="roof_skin", ok=False,
                message=(f"{label}: {holes} places where the roof skin jumps "
                         f"{allow:.3f} m or more between adjacent 0.5 m "
                         f"columns with NOTHING IN THE GAP, worst {d:.3f} m "
                         f"({spanned} further steps are spanned by the body "
                         f"and {second} are the edge of something standing "
                         "on a skin that carries on underneath)"),
                where=(round(mx, 1), round(t, 2), round(mz, 1)),
                measured=round(d, 3), limit=round(allow, 3),
                numbers={"holes": holes, "spanned_steps": spanned,
                         "second_layer_steps": second,
                         "worst_step_m": round(d, 3),
                         "allowance_m": round(allow, 3),
                         "steepest_pitch": round(pitch, 3),
                         "thickest_panel_m": round(thick, 3),
                         "roof_columns": len(tops)},
                population=(f"{len(tops)} roof columns on a {step:.1f} m "
                            "lattice over this body")))
    return out


# ---------------------------------------------------------------------------
# CHECK S5 - DUPLICATES, with a CUBE
# ---------------------------------------------------------------------------
def _solid_cells(s, geom: dict) -> list:
    """Each of a piece's own parts as (origin, inverse-basis, corners).

    Every part in `piece_geometry.json` is an 8-vertex parallelepiped, and
    `part_vertices` emits them in a fixed corner order, so v1-v0, v2-v0 and
    v4-v0 are the three edge vectors.  Inverting that basis turns "is this
    world point inside the part" into three comparisons against 0 and 1,
    which is exact for a rotated box and is what a bounding box cannot do:
    an AABB of a 45-degree piece is 41 % too wide and would report a
    neighbour as a copy.

    PER PART, not out of `Solid.verts`: that field is the concatenation of
    every part with no boundaries in it, and a piece with two parts would
    have its first part's corners mixed with its second part's."""
    out = []
    for raw in (geom.get(s.name) or ()):
        vs = I.part_vertices(raw)
        if len(vs) != 8:
            continue
        part = []
        for v in vs:
            r = I.rotate(s.quat, v)
            part.append((s.pos[0] + r[0], s.pos[1] + r[1], s.pos[2] + r[2]))
        o = part[0]
        m = [[part[1][k] - o[k], part[2][k] - o[k], part[4][k] - o[k]]
             for k in range(3)]
        (a, b, c), (d, e, f), (g, h, i) = m
        det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
        if abs(det) < 1e-12:
            continue
        inv = ((e * i - f * h) / det, (c * h - b * i) / det,
               (b * f - c * e) / det,
               (f * g - d * i) / det, (a * i - c * g) / det,
               (c * d - a * f) / det,
               (d * h - e * g) / det, (b * g - a * h) / det,
               (a * e - b * d) / det)
        out.append((o, inv, part))
    return out


def _inside_any(cells: list, p, eps: float = 1e-6) -> bool:
    for o, inv, _part in cells:
        x, y, z = p[0] - o[0], p[1] - o[1], p[2] - o[2]
        u = inv[0] * x + inv[1] * y + inv[2] * z
        if u < -eps or u > 1.0 + eps:
            continue
        v = inv[3] * x + inv[4] * y + inv[5] * z
        if v < -eps or v > 1.0 + eps:
            continue
        w = inv[6] * x + inv[7] * y + inv[8] * z
        if -eps <= w <= 1.0 + eps:
            return True
    return False


def buried_fraction(s, others: list, geom: dict) -> float:
    """How much of this piece is already filled by the pieces beside it.

    THE QUESTION A DUPLICATE CHECK IS ACTUALLY ASKING.  Not "do these two
    share a pivot" and not "are their quaternions equal", but "if I delete
    this one, does anything change".  A piece whose whole interior is inside
    a sibling is invisible and its removal costs nothing; a piece that fills
    space nothing else fills is holding up a roof.

    Fraction of a 4x4x4 INTERIOR probe grid of each of this piece's own
    parts that lies inside another piece's solid.  Interior and not corners:
    a corner sits ON the face of the box that would contain it and the
    boundary test there is a coin-flip - MEASURED, with corners the buried
    and the load-bearing populations overlap (0.333 vs 0.519) and the
    discriminator disappears; with the interior grid they separate to
    exactly 1.00 against at most 0.50."""
    cells = [c for t in others for c in _solid_cells(t, geom)]
    if not cells:
        return 0.0
    total = hit = 0
    for o, _inv, part in _solid_cells(s, geom):
        ex = [part[1][k] - o[k] for k in range(3)]
        ey = [part[2][k] - o[k] for k in range(3)]
        ez = [part[4][k] - o[k] for k in range(3)]
        for a in DUP_PROBE_LEVELS:
            for b in DUP_PROBE_LEVELS:
                for c in DUP_PROBE_LEVELS:
                    total += 1
                    if _inside_any(cells, (o[0] + a * ex[0] + b * ey[0]
                                           + c * ez[0],
                                           o[1] + a * ex[1] + b * ey[1]
                                           + c * ez[1],
                                           o[2] + a * ex[2] + b * ey[2]
                                           + c * ez[2])):
                        hit += 1
    return 0.0 if not total else hit / total


def check_duplicates(pieces: list, geom: dict, tol: float = DUP_TOL_M,
                     scope: str = "",
                     discs: list | None = None) -> tuple[list, dict]:
    """Two pieces of the same prefab in the same place.

    WITH A CUBE, ON ALL THREE AXES.  `objects_count` is an XZ CYLINDER WITH NO
    HEIGHT BOUND: it reads a legitimate stack of floors one above the other as
    a pile of duplicates, and it cannot see that two pieces at the same xz are
    at different heights.  The cube tells those apart, which is the whole
    reason the 52 pieces at the Ulfsgard boathouse were invisible to a count.

    THE GRADE IS DECIDED BY THE OCCUPIED SPACE, NOT BY THE QUATERNION AND NOT
    BY THE PIVOT.  Two quaternions that differ can place the SAME solid in
    the SAME space - a 180 degree yaw of a symmetric piece is the obvious
    case - and grading on the quaternion would call that pair "differently
    posed" and let a plain duplicate through.  But the converse is just as
    wrong and cost more: two pieces CAN share a pivot and still both be
    load-bearing, and calling that a duplicate asks a builder to delete
    architecture.  Three grades, decided by what the space says:

      SAME SOLID       identical occupied space at 1 cm.  One piece emitted
                       twice, literally invisible, pure waste; the signature
                       of two sub-builders each emitting one shared element
                       in full.  REFUSED.
      BURIED COPY      a different solid whose own volume is already filled
                       by the pieces it shares a pivot with -
                       >= DUP_BURIED_FRACTION of its own interior probe grid
                       lies inside a sibling.  Remove it and nothing moves,
                       nothing opens, nobody can tell.  REFUSED.
      SHARED PIVOT     a different solid that fills space nothing else
                       fills.  Two roofs meeting in a valley, two
                       half-arches making one arch.  NOT a duplicate, and
                       reported as an advisory so the count is visible
                       without being a demand to demolish.

    MEASURED at the fixture: the Ulfsgard boathouse box (1060..1120,
    670..730) holds 748 placed build pieces and 52 co-located extras.  39
    are same-solid (20 wood_floor, 19 wood_pole_log_4) - two sub-builders
    each emitting the whole deck.  The other 13 are `wood_roof` at the
    SHARED EAVE of two gables at x=1080.5, one yawed 90 and one 270, each
    carrying 2.047 m2 of top surface no other piece carries; those are
    architecture and this check no longer asks for them to be deleted.
    World-wide: 2,413 same-solid, 44 buried copies, 417 shared pivots."""
    cells: dict[tuple, list] = collections.defaultdict(list)
    for s in pieces:
        cells[(s.name, int(math.floor(s.pos[0] / tol)),
               int(math.floor(s.pos[1] / tol)),
               int(math.floor(s.pos[2] / tol)))].append(s)
    idx = {id(s): i for i, s in enumerate(pieces)}
    parent = list(range(len(pieces)))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for (nm, cx, cy, cz), lst in cells.items():
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    other = cells.get((nm, cx + dx, cy + dy, cz + dz))
                    if not other:
                        continue
                    for s in lst:
                        for t in other:
                            if s is t:
                                continue
                            if (abs(s.pos[0] - t.pos[0]) <= tol
                                    and abs(s.pos[1] - t.pos[1]) <= tol
                                    and abs(s.pos[2] - t.pos[2]) <= tol):
                                ra, rb = find(idx[id(s)]), find(idx[id(t)])
                                if ra != rb:
                                    parent[rb] = ra
    groups: dict[int, list] = collections.defaultdict(list)
    for s in pieces:
        groups[find(idx[id(s)])].append(s)

    def occupied(s) -> tuple:
        """What this piece fills, at 1 cm.  Pose-independent by construction:
        two different quaternions that put the same solid in the same space
        give the same key, which is what the player sees."""
        return tuple(sorted((round(v[0], 2), round(v[1], 2), round(v[2], 2))
                            for v in s.verts))

    sites: dict[tuple, dict] = collections.defaultdict(
        lambda: dict(same=0, buried=0, shared=0, mix=collections.Counter(),
                     shared_mix=collections.Counter(), unmeasured=0))
    n_same = n_buried = n_shared = n_unmeasured = 0
    for g in groups.values():
        if len(g) < 2:
            continue
        a = g[0]
        site = (round(a.pos[0] / 50) * 50, round(a.pos[2] / 50) * 50)
        rec = sites[site]
        ka = occupied(a)
        for i, b in enumerate(g):
            if i == 0:
                continue
            if not a.verts or not b.verts:
                rec["mix"][a.name] += 1
                rec["unmeasured"] += 1
                n_unmeasured += 1
            elif ka == occupied(b):
                rec["mix"][a.name] += 1
                rec["same"] += 1
                n_same += 1
            elif buried_fraction(b, [t for j, t in enumerate(g)
                                     if j != i], geom) \
                    >= DUP_BURIED_FRACTION:
                rec["mix"][a.name] += 1
                rec["buried"] += 1
                n_buried += 1
            else:
                rec["shared_mix"][a.name] += 1
                rec["shared"] += 1
                n_shared += 1
    out = []
    n_generated_over = 0
    for site, rec in sorted(sites.items(),
                            key=lambda kv: -(kv[1]["same"] + kv[1]["buried"]
                                             + kv[1]["shared"])):
        n = rec["same"] + rec["buried"] + rec["unmeasured"]
        # HOW MANY TIMES DID THE LEDGER ASK FOR THIS GROUND TO BE GENERATED.
        # A duplicate with no named cause is half a finding; a builder
        # cannot act on "there are two of these" without knowing which op
        # made the second one.  The count is the disc coverage, measured
        # from each record's own `pos`/`max_m`.
        #
        # IT IS A CORRELATION AND IT IS LABELLED AS ONE.  MEASURED on
        # Ulfsland: 406 of the ledger's 489 zones are covered by more than
        # one `zones_generate` disc, so almost ANY finding lands on
        # over-covered ground and the number cannot carry a cause on its
        # own.  `zones_generate` is idempotent where it is measurable -
        # `ZoneSystem::SpawnZone` returns before `PlaceLocations` when
        # `IsZoneGenerated` (assembly_valheim IL_0085) and
        # `UpgradeWorld.Generate::ExecuteZone` checks it again (IL_001d);
        # the live console answers "Generate completed. 0 zones generated."
        # on a repeat.
        cover = 0
        if discs:
            cover = sum(1 for px, pz, m in discs
                        if (px - site[0]) ** 2 + (pz - site[1]) ** 2
                        <= m * m)
            if cover > 1:
                n_generated_over += n
        if rec["shared"]:
            out.append(Finding(
                check="duplicates", ok=True, advisory=True,
                message=(f"{rec['shared']} pieces around ({site[0]}, "
                         f"{site[1]}){scope} SHARE A PIVOT with a sibling of "
                         f"the same prefab and fill space nothing else fills "
                         f"- two roofs meeting in a valley, or two "
                         f"half-arches making one arch.  NOT duplicates; "
                         f"removing either would open a hole - "
                         + ", ".join(f"{k} x{v}" for k, v
                                     in rec["shared_mix"].most_common(4))),
                where=(float(site[0]), float(site[1])),
                numbers={"shared_pivot": rec["shared"],
                         "buried_fraction_limit": DUP_BURIED_FRACTION,
                         "mix": dict(rec["shared_mix"].most_common(8))}))
        if not n:
            continue
        out.append(Finding(
            check="duplicates", ok=False,
            message=(f"{n} CO-LOCATED pieces around ({site[0]}, {site[1]})"
                     f"{scope} - same prefab, same place within {tol:.2f} m "
                     f"on all three axes: {rec['same']} filling identical "
                     f"space, {rec['buried']} buried inside the sibling they "
                     f"share a pivot with"
                     + (f", {rec['unmeasured']} with no measured solid"
                        if rec["unmeasured"] else "")
                     + " - "
                     + ", ".join(f"{k} x{v}"
                                 for k, v in rec["mix"].most_common(4))
                     + (f"; the ledger asked for this ground to be "
                        f"generated {cover} times" if cover > 1 else
                        ("; the ledger asked for this ground to be "
                         "generated once" if cover == 1 else ""))),
            where=(float(site[0]), float(site[1])),
            measured=float(n), limit=0.0,
            numbers={"co_located_pieces": n, "same_solid": rec["same"],
                     "buried_copy": rec["buried"],
                     "shared_pivot_not_a_duplicate": rec["shared"],
                     "unmeasured": rec["unmeasured"], "cube_tol_m": tol,
                     "zones_generate_discs_covering": cover,
                     "mix": dict(rec["mix"].most_common(8))}))
    return out, {"same_solid": n_same, "buried_copy": n_buried,
                 "shared_pivot_not_a_duplicate": n_shared,
                 "unmeasured": n_unmeasured, "sites": len(sites),
                 "in_ground_generated_more_than_once": n_generated_over}


# ---------------------------------------------------------------------------
# WHY EACH REFUSAL CAN OR CANNOT BE CLOSED
# ---------------------------------------------------------------------------
# STRUCTURES WAS THE LAST SECTION EMITTING `NO_CLASS`.  Every other section
# on this gate publishes `termination_class` beside its refusals, so the
# board can add "N refusals, M ACTIONABLE" up across sections - and a section
# that publishes nothing is not a zero in that sum, it is a hole in it.  On
# Ulfsland save 7632b3a5 this section's single refusal was the only NO_CLASS
# row on the whole board.
#
# AND THE CLASS IS `settlements.termination_class`, NOT A SIXTH VOCABULARY.
# Four sections found their termination mechanisms independently; a gate
# whose sections each name them differently cannot add its own rows up.
# This function's job is to produce the NUMBERS that function reads, one
# honest set per check, and nothing else.
#
# THE FIVE REMEDY FAMILIES, and each names the tool that performs it or the
# absence of one.  A family is a property of the CHECK, because what closes a
# refusal is decided by what the refusal IS, not by where it happened:
#
#   A TERRAIN CUT           `buried`, `pad_flatness`, `pad_coverage`.  Ground
#       stands where it must not; `roads/regrade.py` lowers it.  These are
#       the only rows with an ownership question to ask, so they are the only
#       ones that can come back 1A or 1B.
#   A RETIRE, AND NOTHING ELSE MOVES  `duplicates`.  The duplicate cube's own
#       definition of a copy is "its solid is already filled by the pieces
#       that share its pivot - remove it and nothing moves, nothing opens,
#       nobody can tell" (DUP_BURIED_FRACTION, measured over an empty 0.48
#       interval).  A `retire` performs it, it writes no terrain, and by the
#       check's own measurement it destroys nothing.  That is every clause of
#       ACTIONABLE.
#   A RETIRE AND A RE-SPAWN  `orientation`.  A panel laid backwards is closed
#       by retiring it and sending the same prefab at the opposite yaw -
#       ledger seq 5074 did exactly that to 144 panels of one roof.  No
#       terrain, and the piece is replaced rather than removed.
#   NO TOOL IN THIS TREE     `grounding`, `shelter`, `roof_skin`.  A body
#       standing in air is closed by RAISING ground, and every terrain
#       instrument in this tree is cut-only by rule; a hole in a roof is
#       closed by BUILDING a panel, and nothing here solves a missing
#       panel's pose.  1C, and it says which.
#   THE LEDGER'S OWN RECORD UNREADABLE  `pad_population`: a pad write whose
#       blob is missing or does not parse.  No tool restores a record's blob
#       and a re-run writes a NEW record beside it, so 1C by SOURCE_CHANGE.
#       It was UNKNOWN (`NOT_MEASURED`) until drive 2's frozen Fresh42 save.
#
# THE THREE-STATE CONTRACT IS THE CALLER'S, AND THIS IS THE CALLER.  A row
# with no proposed repair carries `repair_footprint_m = 0.0`, NEVER None:
# 0.0 says the question was asked and a repair would need no ground, None
# says nobody looked, and `termination_class` reads the two differently by
# design.  Passing None on the no-remedy rows turned six correct 1C rows into
# UNKNOWN in harbours, one branch over from reading absence of a remedy as
# presence of an owner.
REMEDY_IS_A_TERRAIN_CUT = ("buried", "pad_flatness", "pad_coverage")
REMEDY_IS_A_RETIRE = ("duplicates",)
REMEDY_IS_A_RETIRE_AND_A_RESPAWN = ("orientation",)
REMEDY_IS_IN_NO_TOOL_HERE = {
    "grounding": "a body standing clear of the ground is closed by RAISING "
                 "the bed under it or by re-siting the building; every "
                 "terrain instrument in this tree is cut-only by rule and "
                 "this tree never deletes a building",
    "shelter": "an enclosed deck open to the sky is closed by BUILDING the "
               "missing roof, and nothing in this tree solves the pose of a "
               "panel that is not there",
    "roof_skin": "a hole in a roof skin is closed by BUILDING the missing "
                 "course, and nothing in this tree solves the pose of a "
                 "panel that is not there",
}
RECORD_UNREADABLE = ("pad_population",)

# AND THE SAME FIVE FAMILIES, DECLARED IN THE SHAPE THE COVERAGE AUDIT
# READS - DERIVED FROM THE CONSTANTS ABOVE, NEVER RETYPED BESIDE THEM.
#
# `termination.coverage` enumerates every `Finding(ok=False)` site
# reachable from this section's `run()` OUT OF THE SOURCE and refuses the
# gate's own selftest if one of them is named by no family here.  A table
# written out by hand would be a second copy of the four tuples above, free
# to drift from the branch that actually classifies; a derived one cannot.
#
# THESE ENTRIES DRIVE NOTHING ON A NORMAL RUN.  `check_termination` below
# classifies this section's rows against measurements only it holds, and
# `termination.backstop` skips every row that already carries a class - so
# on Ulfsland it fills nothing and moves none of the 105.  What the table
# buys is that a check added to this section without a remedy family
# REDDENS THE SELFTEST instead of landing on a future world's board with
# no class.
TERMINATION_FAMILIES = {
    **{c: ("NO_DESTINATION",
           "the ground under this piece must come down and the ownership "
           "question is asked per row; see check_termination")
       for c in REMEDY_IS_A_TERRAIN_CUT},
    **{c: ("RETIRE_AND_RESPAWN",
           "the copy is retired and nothing moves, nothing opens and "
           "nobody can tell - DUP_BURIED_FRACTION, measured")
       for c in REMEDY_IS_A_RETIRE},
    **{c: ("RETIRE_AND_RESPAWN",
           "the panel is retired and re-spawned at the opposite yaw, as "
           "ledger seq 5074 did to 144 panels of one roof")
       for c in REMEDY_IS_A_RETIRE_AND_A_RESPAWN},
    **{c: ("NO_DESTINATION", why)
       for c, why in REMEDY_IS_IN_NO_TOOL_HERE.items()},
    **{c: ("SOURCE_CHANGE",
           "the ledger's own record of this pad cannot be read - its "
           "terrain_write blob is missing or does not parse to the heightmap "
           "it declares; no tool in this tree restores a record's blob, and "
           "a re-run writes a NEW record beside it rather than repairing "
           "this one")
       for c in RECORD_UNREADABLE},
}

# THE THREE SHIPPED / NEARLY-SHIPPED TERMINATION DEFECTS, off, and reachable
# so the selftest cases can put them back.  `_TERMINATION_OFF` is the state
# this section shipped in - no class at all, `NO_CLASS` on the board.  The
# other two are the shapes the repair could most easily have become, and both
# were MEASURED in another section before they were guarded: an instrument
# that did not answer read as one that answered CLEAR, and a row with no
# terrain remedy handed a `None` footprint that turns 1C_UNSERVED into
# UNKNOWN.
_TERMINATION_OFF = False
_TERMINATION_UNREAD_IS_CLEAR = False
_TERMINATION_NO_REMEDY_FOOTPRINT_IS_NONE = False
# AND THE STATE EVERY REFUSAL IN THIS SECTION WAS IN UNTIL NOW: the
# ownership question NEVER ASKED.  `repair_ownership`'s own docstring names
# the cost - four of nine refusals on gate C stood on ground `writer`'s
# flatten guard will not let anyone move and NOTHING IN THE REFUSAL SAID SO -
# and every reader had to rediscover the split by hand.
_TERMINATION_OWNERSHIP_NEVER_ASKED = False


def _where_xz(f) -> tuple[float, float] | None:
    """`(x, z)` off a finding's own `where`, which this section writes as
    either `(x, z)` or `(x, y, z)`."""
    w = f.where
    if not w or len(w) < 2:
        return None
    return (float(w[0]), float(w[-1]))


def check_termination(findings, owners, writes,
                      placements=None) -> list[Finding]:
    """Give every hard refusal in this section its `termination_class`.

    `owners=None` means the flatten=FORBIDDEN set could not be read and
    `writes=None` the recorded terrain_write footprints could not be; in
    either case the rows whose remedy is a cut come back 1E_OUT_OF_ORDER
    naming the read, never ACTIONABLE and never UNKNOWN
    (`termination.settle`).  UNREAD IS NOT CLEAR: a classifier that
    conflated "nobody holds this ground" with "nobody could tell me" would
    report work available precisely where the gate cannot see.  A row with
    no measured cut, retire or no-tool remedy is classed by its remedy
    family (`termination.fill`).  Then every operator row is asked whose
    blocker it names, off `placements` (`termination.own_blockers`); None
    is UNREAD, and a named blocker is then 1E, never foreign.
    """
    from .. import termination as T                        # noqa: PLC0415
    from .settlements import (contested_ground,           # noqa: PLC0415
                              repair_direction, repair_ownership)
    hard = [f for f in findings if not f.ok and not f.advisory]
    by_class: collections.Counter = collections.Counter()
    for f in hard:
        n = f.numbers if isinstance(f.numbers, dict) else {}
        f.numbers = n
        # NOT A ROAD, AND THE FIELD SAYS SO RATHER THAN BEING ABSENT.  1D is
        # a positive measurement from the road body census - pieces standing
        # in a carriageway - and this section never takes it.  None is "never
        # asked", which is exactly true here and is not 0.
        n.setdefault("obstruction_bodies", None)
        n.setdefault("own_actor", n.get("site_id") or n.get("role"))
        xz = _where_xz(f)
        if f.check in REMEDY_IS_A_TERRAIN_CUT and xz is not None \
                and isinstance(f.measured, (int, float)) \
                and not _TERMINATION_OWNERSHIP_NEVER_ASKED:
            # THE DEPTH IS THE REFUSAL'S OWN MEASUREMENT.  A `buried` row's
            # `measured` is how far the piece's top stands under the surface,
            # which is exactly how far the surface has to come down; a pad
            # row's is its worst error from its own datum.  Sizing the
            # footprint off anything else is sizing it off a number the
            # refusal did not take.
            own = repair_ownership(xz[0], xz[1], float(f.measured), owners,
                                   SUPPORT_LIMIT_M)
            n.update(own)
            n.update(contested_ground(xz[0], xz[1],
                                      own["repair_footprint_m"], writes))
        elif f.check in REMEDY_IS_A_RETIRE \
                or f.check in REMEDY_IS_A_RETIRE_AND_A_RESPAWN:
            # A REMEDY THAT MOVES NO GROUND HAS NO OWNERSHIP QUESTION, AND
            # THAT IS AN ANSWER.  0.0 is "asked, and a repair needs no
            # ground"; the writer permits it because there is nothing for the
            # writer to permit.
            n["repair_footprint_m"] = 0.0
            n["repairable_by_terrain_write"] = True
            n["ground_owned_by"] = None
            n["ground_owner_distance_m"] = None
            n["contested_by"] = None
            n["contested_within_m"] = 0.0
            n["remedy"] = ("retire the copy" if f.check in REMEDY_IS_A_RETIRE
                           else "retire the panel and re-spawn it at the "
                                "opposite yaw")
        elif f.check in REMEDY_IS_IN_NO_TOOL_HERE:
            n["no_remedy_in_this_tree"] = REMEDY_IS_IN_NO_TOOL_HERE[f.check]
            # 0.0, NOT None - see the block comment above.  This is the one
            # place in this section where the two are a different CLASS.
            n["repair_footprint_m"] = (
                None if _TERMINATION_NO_REMEDY_FOOTPRINT_IS_NONE else 0.0)
            n["repairable_by_terrain_write"] = False
            n["ground_owned_by"] = None
            n["ground_owner_distance_m"] = None
            n["contested_by"] = None
            n["contested_within_m"] = None
            if f.check == "grounding" and isinstance(
                    n.get("support_gap_m"), (int, float)):
                # WHICH WAY, BESIDE WHETHER.  A body in the air is closed by
                # ground coming UP, and every instrument a refusal row can be
                # handed here is cut-only - so the sign is published where a
                # consumer looks instead of being rediscovered.
                n.setdefault("judged_as", "riser")
                n.setdefault("rise_m", n["support_gap_m"])
        else:
            # NO MEASURED REPAIR TO ASK AN OWNERSHIP QUESTION ABOUT - a
            # `pad_population` row, or a cut row that published no place or
            # no depth - so its class is its remedy family's, read by the
            # one shared function the backstop reads it with.
            if _TERMINATION_OFF:
                by_class["NO_CLASS"] += 1
                continue
            T.fill(sys.modules[__name__], f.check, n, "structures")
            continue
        if _TERMINATION_UNREAD_IS_CLEAR:
            # THE CONFLATION, restored: an instrument that did not answer,
            # read as an instrument that answered CLEAR.
            if n.get("repairable_by_terrain_write") is None:
                n["repairable_by_terrain_write"] = True
            if n.get("contested_within_m") is None:
                n["contested_within_m"] = 0.0
        if _TERMINATION_OFF:
            # THE STATE THIS SECTION SHIPPED IN: no class, and the board
            # reads it as NO_CLASS.
            by_class["NO_CLASS"] += 1
            continue
        cls = T.settle(n, T.unread(owners, writes))
        n["termination_class"] = cls
        n["repair_direction"] = repair_direction(n)
    # AND WHOSE BLOCKER - after every row has its terrain/body class, and
    # before anything is counted.  See `termination.own_blockers`.
    ownership = T.own_blockers(hard, placements, "structures")
    by_class = collections.Counter(
        (f.numbers or {}).get("termination_class") or "NO_CLASS"
        for f in hard)
    act = by_class.get("ACTIONABLE", 0)
    return [Finding(
        check="structures/termination", ok=True,
        message=(f"{len(hard)} refusal(s), {act} ACTIONABLE"
                 + (" - every other one is named against the decision or the "
                    "missing tool that blocks it" if len(hard) > act else "")
                 + "; by class "
                 + (", ".join(f"{k} {v}" for k, v in sorted(by_class.items()))
                    or "none")),
        measured=float(act), limit=float(len(hard)),
        numbers=dict(refusals=len(hard), actionable=act,
                     by_class=dict(sorted(by_class.items())),
                     by_check={c: dict(sorted(collections.Counter(
                         (f.numbers or {}).get("termination_class")
                         or "UNCLASSIFIED"
                         for f in hard if f.check == c).items()))
                         for c in sorted({f.check for f in hard})},
                     owners_read=owners is not None,
                     writes_read=writes is not None,
                     blocker_ownership=ownership),
        population="every hard refusal this section produced in this run")]


# ---------------------------------------------------------------------------
# A ROOF IS JUDGED ONE BUILDING AT A TIME, AND A BUILDING IS WHAT ONE ANCHOR
# EMITTED
# ---------------------------------------------------------------------------
# Every roof-geometry question in this section was asked of a GEOMETRIC
# union that knows nothing about who built what: `check_orientation` pools
# roof pieces within 1.2 m, and `shelter` / `roof_skin` take `our_bodies`,
# which pools every build piece within 0.35 m.  Neighbouring houses are then
# one roof, and two separate defects follow from that one fact.  MEASURED on
# the Fresh42 drive-2 save a521b70356e7, every refused body of each kind:
#
# ORIENTATION, 12 refused panels.  The ridge set is the panels within 0.5 m
# of THE TALLER HOUSE'S top, so the shorter house's panels, climbing
# correctly toward their own ridge, read as rising AWAY:
#   (401.9,-214.0)  57 panels = stenvik-house-2 24 (ridge 41.33) + stenvik-
#                   manor-1 33 (ridge 42.05), roofs 0.68 m apart; union rule
#                   11 inverted, EVERY ONE of them house-2's; each house alone
#                   0 of 23 and 0 of 28
#   (-686.1,-280.8) 48 panels = tree-sth-1/2/3, 16 each, one ridge height;
#                   union rule 1 inverted (tree-sth-2 wood_wall_roof, margin
#                   -0.265 m against tree-sth-3's ridge); each alone 0 of 12
# 12 of 12 were CORRECT, and the refusal's own remedy - "retire the panel
# and re-spawn it at the opposite yaw" - would have inverted all twelve.
# 1551 of 1551 roof pieces in our bodies attribute to exactly one anchor
# and 0 of 1297 judged rise away from their own building's ridge.
#
# SHELTER, tree-west-1+2+3.  Three stilt houses round one tree: the column
# between them is walled by their walls and has roof material on all eight
# sides - from three different roofs - so the union called it "A HOLE IN
# THE ROOF".  Each house alone: 0 inside-and-open columns of 37.
#
# PROVENANCE IS THE DISCRIMINATOR, BECAUSE NO GEOMETRY IS.  Two halls of ONE
# plan sharing an eave (`harbour-city-ulfsgard`) are one roof and must stay
# one; two plans' houses 0.68 m apart are two roofs.  Only the ledger can
# tell those apart, so each anchor's own commanded placements are one
# building, and the unchanged checks are asked of each building separately.
# A piece NO anchor's wire names is COUNTED, never dropped (see each user).
#
# THE NEUTER SURFACE, ONE MODE PER DIRECTION:
#   "building"  each anchor's own placements - the rule
#   "union"     the geometric pool - THE DEFECT: a neighbour's ridge, walls
#               and roof judge a building that is sound
#   "piece"     every roof piece alone (orientation only) - the degenerate
#               partition: no body reaches 3 panels, every roof passes
BUILDING_PARTITION = "building"
ATTRIBUTION_M = 0.01            # a placed pivot vs its own wire row: the
                                # wire carries 4 decimals and a ZDO float32
                                # at |x|<10,000 m holds ~1e-3, so 1 cm is
                                # a hundredfold margin and far below the
                                # 2.0 m pitch of any two pieces of one prefab


def anchor_of(solids: list, anchors: list[dict]) -> tuple[list, int, int]:
    """Per solid, the index into `anchors` of the anchor whose own
    `spawn_object` row placed it - same prefab, pivot within ATTRIBUTION_M
    in x and z - or None.  Returns `(keys, unattributed, ambiguous)`; an
    ambiguous solid (two anchors' wires name it) takes the first."""
    tol = ATTRIBUTION_M
    names = {s.name for s in solids}
    idx: dict[tuple, list] = collections.defaultdict(list)
    for k, a in enumerate(anchors):
        for (n, x, z) in (a.get("commands") or ()):
            if n in names:
                idx[(n, round(x / tol), round(z / tol))].append((k, x, z))
    keys: list = []
    unattributed = ambiguous = 0
    for s in solids:
        cx, cz = round(s.pos[0] / tol), round(s.pos[2] / tol)
        own = sorted({k for dx in (-1, 0, 1) for dz in (-1, 0, 1)
                      for (k, x, z) in idx.get((s.name, cx + dx, cz + dz), ())
                      if math.hypot(x - s.pos[0], z - s.pos[2]) <= tol})
        if not own:
            unattributed += 1
        elif len(own) > 1:
            ambiguous += 1
        keys.append(own[0] if own else None)
    return keys, unattributed, ambiguous


def split_by_building(named: list) -> tuple[list, dict]:
    """`named` - `(body, anchors, label)` rows - with every body that more
    than one anchor reaches split into one row per anchor.

    A piece no anchor's wire names goes with the anchor of the NEAREST piece
    that one does name (pivot distance in x,z): a wall a later record moved
    still belongs to the house it stands in, and dropping it would open a
    hole the building does not have.  A body none of whose pieces any wire
    names is kept whole.  Returns `(rows, census)`."""
    census = dict(bodies_split=0, buildings=0, unattributed_pieces=0,
                  ambiguous_pieces=0)
    if BUILDING_PARTITION == "union":
        return named, census
    out: list = []
    for body, anch, label in named:
        if len(anch) < 2:
            out.append((body, anch, label))
            continue
        keys, un, amb = anchor_of(body, anch)
        census["unattributed_pieces"] += un
        census["ambiguous_pieces"] += amb
        known = [(s, k) for s, k in zip(body, keys) if k is not None]
        if not known:
            out.append((body, anch, label))
            continue
        parts: dict = collections.defaultdict(list)
        for s, k in zip(body, keys):
            if k is None:
                k = min(known, key=lambda t: math.hypot(
                    t[0].pos[0] - s.pos[0], t[0].pos[2] - s.pos[2]))[1]
            parts[k].append(s)
        census["bodies_split"] += len(parts) > 1
        for k in sorted(parts):
            a = anch[k]
            out.append((parts[k], [a], a.get("site_id") or a.get("role")))
    census["buildings"] = len(out)
    return out, census


def orientation_by_building(solids: list, anchors: list[dict],
                            geom: dict[str, list]) -> dict:
    """`instruments.check_orientation`, asked of each building separately.

    `solids` is the population the caller judges (this section's `ours`);
    `anchors` is `ledger_anchors()`.  Returns `check_orientation`'s own
    shape, summed over the partitions, plus the partition's census:
    `buildings` judged, `unattributed_panels` (no anchor's wire names the
    piece: judged together, by the geometric union - a roof piece has no
    wall to be "nearest" to, and a pool is the conservative reading) and
    `ambiguous_panels` (two anchors' wires name it: judged with the first).
    """
    roof = [s for s in solids
            if ("roof" in s.name.lower() or "_shingle" in s.name.lower())
            and s.known]
    if BUILDING_PARTITION == "union":
        out = dict(I.check_orientation(solids, geom))
        out.update(buildings=None, unattributed_panels=None,
                   ambiguous_panels=None)
        return out
    keys, unattributed, ambiguous = anchor_of(roof, anchors)
    groups: dict = collections.defaultdict(list)
    for s, key in zip(roof, keys):
        groups[id(s) if BUILDING_PARTITION == "piece" else key].append(s)
    parts = [I.check_orientation(g, geom) for _k, g in sorted(
        groups.items(), key=lambda kv: (kv[0] is None, kv[0] or 0))]
    out = dict(I.check_orientation([], geom))
    for p in parts:
        for f in ("pieces", "bodies", "checked", "inverted", "upside_down",
                  "no_ridge_bodies", "no_ridge_panels",
                  "bodies_with_inversions", "seam_cleared", "on_side"):
            out[f] = out.get(f, 0) + p.get(f, 0)
        out["worst"] = out["worst"] + list(p.get("worst") or ())
        out["inverted_at"] = out["inverted_at"] + list(
            p.get("inverted_at") or ())
        out["seam_cleared_at"] = out["seam_cleared_at"] + list(
            p.get("seam_cleared_at") or ())
        out["inverted_bodies"] = (list(out.get("inverted_bodies") or ())
                                  + list(p.get("inverted_bodies") or ()))
    out["worst"] = sorted(out["worst"])[:20]
    out["inverted_at"].sort(key=lambda r: r["margin_m"])
    out["inverted_bodies"] = sorted(out.get("inverted_bodies") or (),
                                    key=lambda b: -b["inverted"])[:25]
    out["pass"] = out["inverted"] == 0 and out["upside_down"] == 0
    out["population"] = (out["population"] + ", partitioned by the ledger "
                         "anchor whose own spawn_object row placed it "
                         f"(prefab and x,z within {ATTRIBUTION_M} m)")
    out.update(buildings=sum(1 for k in groups if k is not None),
               unattributed_panels=unattributed, ambiguous_panels=ambiguous)
    return out


# ---------------------------------------------------------------------------
# A LONE PLACED PROP IS STILL A PLACED PIECE
# ---------------------------------------------------------------------------
# `MIN_BODY_PIECES` is a rule about BUILDINGS - "a two-piece body has no
# interior to be wrong about" - and it was also the only door into the
# burial check.  MEASURED on the Fresh42 drive-2 save a521b70356e7: the
# cartography table `terraform/stock.py` placed at ledger seq 1978, 1.6 m
# outside its levelled pad with its OWN TOP 1.982 m under the bank, was a
# 3+-piece body only while the artisan station's 40 x 200 x 40 m trigger
# volume counted as solid; once the corpus stopped reading that volume
# (`instruments._CORPUS_KEEPS_TRIGGER_VOLUMES`) the table was a body of ONE
# and no check ever asked whether it was buried.
#
# So every body under the minimum is judged for burial - and for burial
# only - WHEN THIS RUN'S OWN WIRE PLACED IT: a piece whose prefab and pivot
# (within ATTRIBUTION_M) are a `spawn_object` row of an anchor on this
# chain.  Provenance, not proximity: a lone generated piece beside one of
# our plans is the world's, exactly as `our_bodies` refuses to claim a
# generated farmhouse for a lamp.
_LONE_PIECES_UNJUDGED = False    # the shipped population, for a neuter:
                                 # a body under the minimum is never judged


def ledger_spawns(ledger) -> list[dict]:
    """Every single-object `spawn` record as a one-command anchor.

    A lone prop is as often placed by one `spawn` as by a plan: the drive-2
    cartography table is ledger seq 1978, op `spawn`, prefab
    `piece_cartographytable` at (-8167.5, 4425.5) - and `ledger_anchors`
    reads `spawn_plan` only, so its wire was never anyone's."""
    out = []
    for line in ledger.read_text().splitlines():
        if '"op":"spawn"' not in line.replace(" ", ""):
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        p = d.get("params") or {}
        pos = p.get("pos")
        if (d.get("op") != "spawn" or not p.get("prefab")
                or not (isinstance(pos, (list, tuple)) and len(pos) == 3)):
            continue
        x, z = float(pos[0]), float(pos[2])
        out.append(dict(site_id=p.get("site_id"), role=p.get("role") or "spawn",
                        x=x, z=z, prefabs={p["prefab"]}, seqs={d.get("seq")},
                        declares_a_body=False,
                        commands=[(p["prefab"], x, z)]))
    return out


def lone_placed(small: list[list], anchors: list[dict]) -> list:
    """`(body, [anchor], label)` for every body under MIN_BODY_PIECES that
    an anchor's own wire placed - see the note above."""
    if _LONE_PIECES_UNJUDGED:
        return []
    flat = [s for body in small for s in body]
    keys, _un, _amb = anchor_of(flat, anchors)
    key = {id(s): k for s, k in zip(flat, keys)}
    out = []
    for body in small:
        own = sorted({key[id(s)] for s in body if key[id(s)] is not None})
        if not own:
            continue
        a = anchors[own[0]]
        out.append((body, [a], f"{a.get('site_id') or a.get('role')}: lone "
                               + "+".join(sorted({s.name for s in body}))))
    return out


# ---------------------------------------------------------------------------
# the section
# ---------------------------------------------------------------------------
def run(ctx) -> SectionResult:
    geom = ctx.geom
    solids = ctx.solids_all()
    build = ctx.build_pieces
    pieces = [s for s in solids if s.name in build]
    cols = Columns(geom)

    every_body = I.bodies(pieces, slack=0.35)
    bods = [b for b in every_body if len(b) >= MIN_BODY_PIECES]
    anchors = ledger_anchors(ctx.ledger)
    lone = lone_placed([b for b in every_body if len(b) < MIN_BODY_PIECES],
                       anchors + ledger_spawns(ctx.ledger))
    matched, orphan, foreign, scatter, retired = our_bodies(bods, anchors)
    named = []
    for body, anch in matched:
        sid = sorted({a["site_id"] or a["role"] for a in anch})
        named.append((body, anch, "+".join(sid[:3])
                      + ("" if len(sid) <= 3 else f"+{len(sid) - 3} more")))

    # --- ground, once, for grounding AND flatness -------------------------
    pads, pad_complaints = pad_populations(ctx.ledger, ctx.ledger_dir
                                           / "blobs")
    pts: list[tuple[str, float, float]] = []
    for body, _a, label in named + lone:
        for s in body:
            for (x, z) in piece_columns(s):
                pts.append(gpoint(x, z))
    for sid, p in sorted(pads.items()):
        # THE RECTANGLE, not only what was written: a sample the writer
        # dropped is only visible in the promise.  See `check_pad_coverage`.
        for (x, z) in p["own"]:
            pts.append(gpoint(x, z))
        for (x, z) in (p["rect"] or ()):
            pts.append(gpoint(x, z))
    seen: set[str] = set()
    pts = [q for q in pts if not (q[0] in seen or seen.add(q[0]))]
    ground = ctx.ground(pts) if pts else {}

    findings: list[Finding] = []
    findings += check_grounding(named, ground, cols, geom)
    findings += check_grounding(lone, ground, cols, geom, judge_support=False)
    findings += check_flatness(pads, ground)
    findings += check_pad_coverage(pads, ground)
    # ONE BUILDING AT A TIME - see `split_by_building`.  tree-west's three
    # stilt houses made a "hole in the roof" of the column between them.
    by_building, split_census = split_by_building(named)
    findings += check_shelter(by_building, cols)
    findings += check_skin(by_building, cols, geom)

    # --- orientation ------------------------------------------------------
    # THE POPULATION WAS THE WHOLE SAVE, AND THE WHOLE SAVE IS NOT OURS.
    # `check_orientation` declares "every placed piece whose prefab name
    # contains 'roof'" - 8,423 pieces in 712 bodies - while every other check
    # in this section runs over `our_bodies()`, because the section's claim is
    # about what THIS RUN built.
    #
    # MEASURED at save 39e4d7b7, asking this section's own ownership question
    # of all 21 refused bodies with its own 8 m margin: TWENTY of the 21 are
    # nowhere near a ledger anchor - nearest 65.3 m, 107.4, 123.5, 132.9,
    # 155.6, 159.7, 170.0 ... 3,929.8 and 4,465.2 m - and they arrive in
    # repeated signatures, five identical 11-panel bodies, four identical
    # 27-panel and seven identical 42-panel, which is the seed placing one
    # location prefab over and over.  They are Valheim's own generated
    # village houses and ruins, and a RUIN IS AUTHORED WITH BROKEN PANELS.
    #
    # AND THE ONE BODY THAT IS OURS WAS REPORTED AT THE WRONG 14 PANELS FOR
    # A WHOLE SESSION.  This comment used to say `harbour-city-ulfsgard`'s
    # refusal was 14 shared-eave panels that must stay, "a REFUSAL WITH
    # NUMBERS", on the strength of a count the instrument produced by
    # breaking a 6.000 m / 6.006 m distance tie with float dust.  MEASURED,
    # same save, nothing moved (see `instruments.check_orientation`):
    #
    #   those 14 panels tie at 0.006 m and are CORRECT - each climbs toward
    #     its own hall's ridge, and a 180 degree flip of them, which is what
    #     "refuse it" would eventually have bought, would have reversed 14
    #     sound panels;
    #   the 52 panels that ARE reversed are that roof's whole APEX COURSE,
    #     margin 0.506 m, never judged at all because the old rule skipped
    #     any panel within 0.75 m of the ridge - and they are why the same
    #     roof carries 104 `roof_skin` holes: the apex is a 0.97 m GUTTER
    #     where the ridge should be.
    #
    # Ledger seq 5074 repaired 144 of this roof's 196 panels and stopped
    # exactly at the course the check could not see.  That is the whole
    # shape of it: A REPAIR CAN ONLY EVER BE AS WIDE AS THE CHECK THAT
    # NAMES ITS POPULATION.
    #
    # The world-wide figure is still reported, as an ADVISORY, because "the
    # seed's own ruins have inverted panels" is a true sentence worth having
    # in the log and is not a defect in this build.
    ours = [s for body, _a, _l in named for s in body]
    # ONE BUILDING AT A TIME - see `orientation_by_building`.  12 of the 12
    # panels the drive-2 gate refused were correct panels judged against a
    # NEIGHBOURING house's ridge.
    ori = orientation_by_building(ours, anchors, geom)
    # THE WORLD FIGURE IS OURS AS JUDGED ABOVE PLUS EVERYTHING ELSE BY THE
    # GEOMETRIC UNION.  Re-judging the whole save by the union would put the
    # neighbouring-house artefact straight back into the advisory's "stand
    # in bodies NO ledger anchor of this run reaches" - a false sentence
    # about panels that are ours and sound.
    mine = {id(s) for s in ours}
    rest = I.check_orientation([s for s in solids if id(s) not in mine],
                               geom)
    ori_keys = ("pieces", "bodies", "checked", "inverted", "upside_down",
                "bodies_with_inversions", "no_ridge_bodies",
                "no_ridge_panels", "margin_limit_m")
    world = {k: ori.get(k, 0) + rest.get(k, 0) for k in ori_keys}
    world["margin_limit_m"] = ori["margin_limit_m"]
    world["population"] = rest["population"]
    if ori["inverted"] or ori["upside_down"]:
        for b in ori.get("inverted_bodies", [])[:20]:
            findings.append(Finding(
                check="orientation", ok=False,
                message=(f"{b['inverted']} of {b['checked']} panels in a "
                         f"{b['panels']}-panel roof at ({b['at'][0]}, "
                         f"{b['at'][1]}) rise AWAY from their own ridge"),
                where=(float(b["at"][0]), float(b["at"][1])),
                measured=float(b["inverted"]), limit=0.0,
                numbers=dict(b),
                population=(f"{b['checked']} of this body's {b['panels']} "
                            "roof pieces have a measurable rise")))
        findings.append(Finding(
            check="orientation", ok=False,
            message=(f"{ori['inverted']} inverted roof panels in "
                     f"{ori['bodies_with_inversions']} bodies THIS RUN "
                     f"BUILT, {ori['upside_down']} of them upside down, out "
                     f"of {ori['checked']} judged of {ori['pieces']} roof "
                     f"pieces in {ori['bodies']} of our bodies "
                     f"({ori['no_ridge_panels']} of those pieces are in "
                     f"{ori['no_ridge_bodies']} bodies whose every panel "
                     f"tops out at one height, so they have no ridge to "
                     f"point at and are UNMEASURED, not sound)"),
            measured=float(ori["inverted"]), limit=0.0,
            numbers={**{k: ori[k] for k in ori_keys},
                     "buildings": ori.get("buildings"),
                     "unattributed_panels": ori.get("unattributed_panels"),
                     "ambiguous_panels": ori.get("ambiguous_panels")},
            population=(f"{ori['checked']} of {ori['pieces']} pieces whose "
                        "prefab name contains 'roof' in a body matched to a "
                        "ledger anchor of this run - `our_bodies`, the same "
                        "population every other check in this section "
                        "uses")))
    if world["inverted"] > ori["inverted"] or world["upside_down"] > ori[
            "upside_down"]:
        findings.append(Finding(
            check="orientation", ok=True, advisory=True,
            message=(f"world-wide, INCLUDING the seed's own generated "
                     f"locations: {world['inverted']} inverted roof panels "
                     f"in {world['bodies_with_inversions']} bodies, "
                     f"{world['upside_down']} of them upside down, out of "
                     f"{world['checked']} judged of {world['pieces']}.  "
                     f"{world['inverted'] - ori['inverted']} of those "
                     f"inverted and "
                     f"{world['upside_down'] - ori['upside_down']} of those "
                     f"upside-down panels stand in bodies NO ledger anchor "
                     f"of this run reaches - a generated village house or a "
                     f"ruin, which is authored with broken panels - and this "
                     f"build cannot be refused for them"),
            numbers={k: world[k] for k in ori_keys},
            population=(f"{world['checked']} of {world['pieces']} - "
                        + world["population"])))

    # --- duplicates -------------------------------------------------------
    # THE SAME POPULATION EVERY OTHER CHECK IN THIS SECTION USES, AND IT WAS
    # NOT, AND THE COST OF THAT WAS A DELETE.  `check_duplicates` ran over
    # every build piece in the save while `orientation`, `grounding`,
    # `shelter` and `skin` all run over `our_bodies()`, so it refused this
    # build for co-located pieces standing inside VALHEIM'S OWN GENERATED
    # LOCATIONS - and the only remedy a duplicate finding has is a retire.
    #
    # MEASURED on Ulfsland save 5385652e, all 7 refused sites, all 46
    # co-located pieces:
    #
    #   nearest position at which THIS RUN EVER DECLARED THAT PREFAB:
    #     246.8 m, 581.9, 936.1, 1042.7, 1057.3, 1903.4, 1918.1, 6075.5 m
    #   nearest declared build placement of ANY prefab: 86.9 m to 433.7 m
    #   and two of the bodies are the SAME 437-piece generated location
    #     stamped twice, 2,345 m apart at (2050,700) and (-250,4950), each
    #     carrying ONE co-located `wood_beam` pair at the SAME LOCAL OFFSET
    #     (-1.366, 3.312, -5.317) and (-1.366, 3.312, -5.315) - agreeing to
    #     2 mm.  The seed authored that pair; nothing this run did could
    #     reproduce one defect at one offset in two places.
    #
    # Over `our_bodies` the same instrument on the same save reports ZERO
    # same-solid and zero buried copies out of 221 shared pivots.  The world
    # figure is kept as an ADVISORY, in the shape `orientation` already uses,
    # because "the seed's own locations contain co-located pieces" is worth
    # having in the log and is not a state this build may be asked to fix by
    # deleting somebody else's building.
    discs = generate_discs(ctx.ledger)
    dup, dupn = check_duplicates(ours, geom, discs=discs,
                                 scope=" in a body this run built")
    findings += dup
    wdup, wdupn = check_duplicates(pieces, geom, discs=discs)
    extra = (wdupn["same_solid"] + wdupn["buried_copy"]
             + wdupn["unmeasured"]) - (dupn["same_solid"]
                                       + dupn["buried_copy"]
                                       + dupn["unmeasured"])
    if extra > 0:
        findings.append(Finding(
            check="duplicates", ok=True, advisory=True,
            message=(f"world-wide, INCLUDING the seed's own generated "
                     f"locations: {extra} co-located pieces at "
                     f"{wdupn['sites'] - dupn['sites']} further sites stand "
                     f"in bodies NO ledger anchor of this run reaches.  The "
                     f"only remedy a duplicate finding has is a RETIRE, and "
                     f"a retire there deletes a piece of a generated "
                     f"village, ruin or dvergr hall this build did not "
                     f"place - so it is reported and never refused"),
            numbers={"co_located_outside_our_bodies": extra,
                     "world": wdupn, "ours": dupn},
            population=(f"{len(pieces)} placed build pieces in the save, of "
                        f"which {len(ours)} are in a body matched to a "
                        f"ledger anchor of this run")))

    for a in retired:
        # NEVER SILENT.  A plan the chain deliberately pulled out is a fact
        # about this world worth having in the log; what it is not is a
        # refusal, because the only action it could ask for is to rebuild the
        # thing the ledger decided to remove.
        findings.append(Finding(
            check="retired_plan", ok=True, advisory=True,
            message=(f"the ledger planned {a['role']} {a['site_id']} at "
                     f"({a['x']:.1f}, {a['z']:.1f}) and then RETIRED ALL "
                     f"{a['commands_declared']} of its own commanded "
                     f"placements ("
                     + (", ".join(sorted(a["prefabs_declared"]))
                        or "none declared")
                     + f") by records seq "
                     + ", ".join(str(s) for s in a["retire_seqs"])
                     + f", each citing this plan; {a['reached_bodies']} "
                     f"body/bodies are within "
                     f"{ANCHOR_MARGIN_M:.0f} m.  Reason on the chain: "
                     + (a["retire_reason"][:160] or "none recorded")),
            where=(round(a["x"], 1), round(a["z"], 1)),
            numbers={"role": a["role"], "site_id": a["site_id"],
                     "commands_declared": a["commands_declared"],
                     "retired_commands": a["retired_commands"],
                     "retire_seqs": a["retire_seqs"],
                     "reached_bodies": a["reached_bodies"],
                     "prefabs_declared": a["prefabs_declared"]},
            population=(f"{a['retired_commands']} of this plan's "
                        f"{a['commands_declared']} commanded placements are "
                        "named by a retire record that cites it")))
    for a in orphan:
        findings.append(Finding(
            check="orphan_plan", ok=False, advisory=True,
            message=(f"the ledger planned {a['role']} {a['site_id']} at "
                     f"({a['x']:.1f}, {a['z']:.1f}) and no body of "
                     f"{MIN_BODY_PIECES}+ build pieces stands within "
                     f"{ANCHOR_MARGIN_M:.0f} m of it that holds any of the "
                     "prefabs it declared ("
                     + (", ".join(a["prefabs_declared"])
                        or "none declared")
                     + f"); {a['reached_bodies']} body/bodies are within "
                     f"reach"),
            where=(round(a["x"], 1), round(a["z"], 1)),
            numbers={"role": a["role"], "site_id": a["site_id"],
                     "reached_bodies": a["reached_bodies"],
                     "prefabs_declared": a["prefabs_declared"]}))
    for f in foreign:
        # NOT OURS, AND STILL MEASURED.  This body is inside a plan's reach
        # and holds none of that plan's prefabs, so the section does not carry
        # a verdict over it - but our own terrain writes can bury somebody
        # else's building, and `roads/mod_keepout.json` marks these
        # `forbid: true`, so the fact is reported rather than dropped.
        findings.append(Finding(
            check="foreign_body", ok=False, advisory=True,
            message=(f"a {f['pieces']}-piece body at ({f['x']}, {f['z']}) is "
                     f"within {ANCHOR_MARGIN_M:.0f} m of the plan for "
                     f"{'+'.join(f['claimed_by'])} and holds NONE of the "
                     f"prefabs that plan declared "
                     f"({', '.join(f['declared']) or 'none'}) - it is "
                     f"{', '.join(f['prefabs'][:6])}"
                     + (f" +{len(f['prefabs']) - 6} more"
                        if len(f["prefabs"]) > 6 else "")
                     + "; not judged as ours"),
            where=(f["x"], f["z"]),
            numbers={"pieces": f["pieces"], "claimed_by": f["claimed_by"],
                     "declared": f["declared"], "prefabs": f["prefabs"]}))
    for c in pad_complaints:
        findings.append(Finding(
            check="pad_population", ok=False,
            message=f"pad population is incomplete: {c}",
            numbers={"complaint": c}))

    # --- why each refusal can or cannot be closed -------------------------
    # LAST, because it reads every refusal this section produced and writes
    # its class back onto that refusal's own numbers.  The two instruments
    # are the ones every other section on this gate uses, read through the
    # same functions, so an owner named here is the owner named there.
    from .settlements import (forbidden_extents,           # noqa: PLC0415
                              live_records)
    from .harbours import WriteIndex, write_footprints     # noqa: PLC0415
    owners = writes = None
    # RESOLVED BEFORE THE GUARD, the same repair `roads.py::owner_instruments`
    # carries and for the same MEASURED reason: `ctx.world` does not exist,
    # `Ctx.__getattr__` raises AttributeError, and an `except Exception`
    # written for an unparseable ledger relabels a wiring fault as an
    # unreadable corpus.  That is how 1A_FORBIDDEN came to be 0 of 106
    # refusals gate-wide.  THIS SECTION COMMITTED THE IDENTICAL LINE IN THE
    # SAME RUN.
    world_name = ctx.args.world
    ledger, ledger_dir = ctx.ledger, ctx.ledger_dir
    try:
        owners = forbidden_extents(live_records(ledger), ledger_dir,
                                   world_name)
    except Exception as exc:                               # noqa: BLE001
        # UNREAD IS NOT CLEAR, AND IT IS NOT SILENT EITHER.  Every row whose
        # remedy is a cut falls to UNKNOWN below; this says why.
        ctx.log(f"flatten=FORBIDDEN owner set unreadable ({exc}); no refusal "
                f"in this section can say whether the ground its repair "
                f"needs is owned")
    try:
        writes = WriteIndex(write_footprints(ledger), margin_max=8.0)
    except Exception as exc:                               # noqa: BLE001
        ctx.log(f"recorded terrain_write footprints unreadable ({exc}); no "
                f"refusal in this section can say whose material its repair "
                f"would cut")
    # THE POPULATION, BESIDE THE VERDICT, AND PRINTED WHETHER OR NOT THIS
    # SECTION PASSES.  The gate's own standing rule - no verdict over a
    # population nobody printed the size of - was being counted against six
    # sections, and this was one of them: a reader of a PASSING structures
    # run could not tell 349 bodies from 3.
    findings.append(Finding(
        check="population", ok=True,
        message=(f"{len(named)} of {len(bods)} connected bodies of "
                 f"{MIN_BODY_PIECES}+ pieces are matched to a ledger anchor "
                 f"of this run ({sum(len(b) for b, _a, _l in named)} of "
                 f"{len(pieces)} placed build pieces, of {len(ctx.census.rows)}"
                 f" ZDOs in the save); {len(pads)} pad write(s), "
                 f"{sum(1 for p in pads.values() if p['current'])} of them "
                 f"current, over {len(ground)} of {len(pts)} ground vertices "
                 f"read"),
        numbers=dict(bodies_ours=len(named), bodies_total=len(bods),
                     pieces_ours=sum(len(b) for b, _a, _l in named),
                     build_pieces_placed=len(pieces),
                     zdos=len(ctx.census.rows), pads=len(pads),
                     ground_read=len(ground), ground_points=len(pts)),
        population="every connected body of 3 or more placed build pieces "
                   "in the save, and every lattice sample a ledger site_pad "
                   "write declared"))
    # AND THE SPLIT GOES IN THE REPORT, NOT ONLY IN A FINDING.  The JSON
    # artefact carries only the REFUSING findings, so a passing census row
    # is invisible to every reader downstream of `--json` - which is where
    # the board's "N refusals, M ACTIONABLE" is actually read from.  Same
    # place `roads` and `settlements` publish theirs.
    from .. import termination as T                        # noqa: PLC0415
    term = check_termination(findings, owners, writes,
                             placements=T.Placements.read(ctx.ledger))
    findings += term

    report = dict(
        bodies_total=len(bods), bodies_ours=len(named),
        pieces_ours=sum(len(b) for b, _a, _l in named),
        # THE BODIES UNDER THE MINIMUM THIS RUN'S WIRE PLACED, judged for
        # burial only - see `lone_placed`.
        lone_placed_bodies=len(lone),
        lone_placed_pieces=sum(len(b) for b, _a, _l in lone),
        build_pieces_placed=len(pieces), zdos=len(ctx.census.rows),
        unresolved_prefabs=ctx.census.unresolved,
        anchors=len(anchors), anchors_without_a_body=len(orphan),
        # SCATTER PLANS ARE COUNTED, NEVER SILENT.  A plan whose
        # own commands cannot cluster into a body is not judged
        # against one; the number is published so a reader can
        # see how much of the anchor population that is.
        anchors_declaring_a_scatter=len(scatter),
        scatter_roles=sorted({a['role'] or '?' for a in scatter}),
        # AND THE PLANS THE CHAIN PULLED OUT ITSELF, counted beside the ones
        # that never arrived, so the two are never one number.
        anchors_retired_in_full=len(retired),
        retired_roles=sorted({a['role'] or '?' for a in retired}),
        foreign_bodies_near_a_plan=len(foreign),
        pads=len(pads),
        pads_current=sum(1 for p in pads.values() if p["current"]),
        pads_declaring_a_centre=sum(1 for p in pads.values()
                                    if p["centre"] is not None),
        pad_samples=sum(len(p["own"]) for p in pads.values()),
        pad_rect_samples=sum(len(p["rect"] or ()) for p in pads.values()
                             if p["current"]),
        ground_points=len(pts), ground_read=len(ground),
        orientation=ori, orientation_world_wide=world, duplicates=dupn,
        buildings=split_census,
        termination=dict(
            instrument="settlements.termination_class over each refusal's "
                       "own remedy family, with the flatten=FORBIDDEN owner "
                       "set and the recorded terrain_write footprints read "
                       "from this run's own ledger",
            population="every hard refusal this section produced",
            **dict(term[0].numbers),
            remedy_families=dict(
                terrain_cut=list(REMEDY_IS_A_TERRAIN_CUT),
                retire=list(REMEDY_IS_A_RETIRE),
                retire_and_respawn=list(REMEDY_IS_A_RETIRE_AND_A_RESPAWN),
                no_tool_in_this_tree=sorted(REMEDY_IS_IN_NO_TOOL_HERE),
                record_unreadable=list(RECORD_UNREADABLE))),
        degenerate_parts=cols.degenerate)
    return SectionResult(findings=findings, report=report)


# ---------------------------------------------------------------------------
# THE SELF-TEST - every check proved in BOTH DIRECTIONS, with no world
# ---------------------------------------------------------------------------
# Each case is a STATE built here in code and a direction the check must come
# out.  Five of them encode a state THIS WORLD WAS ACTUALLY IN; that is the
# difference between a proof and a decoration.
# ---------------------------------------------------------------------------
_FLOOR = [["b", -1.0, -0.03, -1.0, 1.0, 0.10, 1.0]]
_SLAB = [["b", -1.0, -0.5, -1.0, 1.0, 0.5, 1.0]]          # stone_floor_2x2
_POST = [["b", -0.15, 0.0, -0.15, 0.15, 4.0, 0.15]]
# wood_roof, verbatim from blueprints/data/piece_geometry.json
_PANEL = [["p", -1.0, 0.9569, -1.0583, 1.0, 0.9569, -1.0583,
           -1.0, 1.0825, -0.9971, 1.0, 1.0825, -0.9971,
           -1.0, -0.0485, 1.0031, 1.0, -0.0485, 1.0031,
           -1.0, 0.0771, 1.0643, 1.0, 0.0771, 1.0643]]
# woodwall, rounded from the corpus: 2 m wide, 2 m tall, 0.24 m thick
_WALL = [["b", -1.0, 0.0, -0.12, 1.0, 2.0, 0.12]]
_GEOM = {"wood_floor": _FLOOR, "stone_floor_2x2": _SLAB,
         "wood_pole_log_4": _POST, "wood_roof": _PANEL,
         "woodwall": _WALL, "probe_roof": _PANEL,
         # The walker, at the gate's measured 0.490 x 1.850 m capsule: the
         # shelter crack radius is read off it (`shelter_crack_m`).
         "Player": [["b", -0.49, 0.0, -0.49, 0.49, 1.85, 0.49]]}


def _cols() -> Columns:
    return Columns(_GEOM)


def _tower(y0: float) -> list:
    """Four columns and a deck, placed with their feet at y0."""
    out = []
    for x in (-2.0, 2.0):
        for z in (-2.0, 2.0):
            out.append(I.mk_solid("wood_pole_log_4", (x, y0, z), I.yaw(0),
                                  _POST))
    for x in (-2.0, 0.0, 2.0):
        for z in (-2.0, 0.0, 2.0):
            out.append(I.mk_solid("wood_floor", (x, y0 + 4.0, z), I.yaw(0),
                                  _FLOOR))
    return out


def _ground_for(body, label, y):
    g = {}
    for s in body:
        for (x, z) in piece_columns(s):
            g[gkey(x, z)] = y
    return g


def _case_floating():
    """THE WATCHTOWER.  40 columns in 3.173-13.542 m of air, and four
    censuses said zero floating because each compared the body to the datum
    its own pad was levelled to.  Here the ground is 3.173 m below the feet
    and the check must refuse."""
    body = _tower(40.0)
    g = _ground_for(body, "wt", 40.0 - 3.173)
    return check_grounding([(body, [], "wt")], g, _cols(), _GEOM)


def _case_standing():
    body = _tower(40.0)
    g = _ground_for(body, "wt", 40.0 - 0.10)
    return check_grounding([(body, [], "wt")], g, _cols(), _GEOM)


def _case_pad_datum_lies():
    """The defect the OLD census could not see: the body is exactly on its
    own pad datum, and the pad datum is 3.173 m off the ground.  Measuring
    against the datum returns 0.000 m; measuring against the applied surface
    returns 3.173 m.  Both numbers are asserted, because the first one is why
    the watchtower shipped."""
    body = _tower(40.0)
    datum = 40.0
    blind = min(s.ymin - datum for s in body)     # what the old census did
    if abs(blind) > 1e-6:
        return [Finding(check="grounding", ok=False,
                        message="fixture is wrong: the body is not on its "
                                f"own datum ({blind:.3f} m)",
                        measured=blind)]
    return check_grounding([(body, [], "wt")], _ground_for(body, "wt", 36.827),
                           _cols(), _GEOM)


def _case_clean_site_slab():
    """THE CLEAN-SITE SIGNATURE.  `stone_floor_2x2` has a MID-SLAB pivot, so a
    slab laid correctly with its top at pad level has its bottom exactly
    1.000 m under the applied surface.  "Worst burial 1.000 m" is not a
    defect, and a check that refuses it refuses every stone floor ever laid."""
    body = [I.mk_solid("stone_floor_2x2", (float(x), 40.0 - 0.5, float(z)),
                       I.yaw(0), _SLAB)
            for x in (-2, 0, 2) for z in (-2, 0, 2)]
    return check_grounding([(body, [], "pad")],
                           _ground_for(body, "pad", 40.0), _cols(), _GEOM)


def _case_supported_at_one_corner():
    """A SLAB RESTING ON ONE CORNER OF ITS OWN FOOTPRINT.

    `Heightmap.GetHeight` is nearest-vertex, so one reading per piece is one
    vertex, and MEASURED on this world the true ground can rise 1.5830 m
    inside a single cell.  Here the ground under the slab is 3.5 m down at
    its centre and 0.1 m down at one corner: the slab is resting on the
    ground and the check must ACCEPT it.  The case also asserts that the
    CENTRE-ONLY reading - one vertex per piece, what this check did before -
    refuses the same sound slab, because that is the blindness being
    retired."""
    body = [I.mk_solid("stone_floor_2x2", (0.0, 40.0 - 0.5, 0.0), I.yaw(0),
                       _SLAB)]
    g = {gkey(x, z): 36.5 for (x, z) in piece_columns(body[0])}
    g[gkey(1.0, 1.0)] = 39.4
    centre_only = {gkey(*I.snap(*footprint_centre(body[0]))): 36.5}
    blind = check_grounding([(body, [], "slab")], centre_only, _cols(), _GEOM)
    if not [f for f in blind if not f.ok and not f.advisory]:
        return [Finding(
            check="grounding", ok=False,
            message="fixture is wrong: the centre-only reading no longer "
                    "refuses the slab, so this case records nothing",
            numbers={"centre_ground": 36.5, "slab_bottom": 39.0})]
    return check_grounding([(body, [], "slab")], g, _cols(), _GEOM)


def _case_swallowed():
    """A piece COMPLETELY under the applied surface - top and all.  That is
    the state the -1.000 m slab is not in, and it must be refused."""
    body = [I.mk_solid("stone_floor_2x2", (float(x), 38.0, float(z)),
                       I.yaw(0), _SLAB)
            for x in (-2, 0, 2) for z in (-2, 0, 2)]
    return check_grounding([(body, [], "pad")],
                           _ground_for(body, "pad", 40.0), _cols(), _GEOM)


# THE CARTOGRAPHY TABLE, to scale: one piece whose OWN TOP stands 1.982 m
# under the bank, as `terraform/stock.py` placed it at ledger seq 1978 on
# the Fresh42 drive-2 save, a body of one.
_GEOM["table_probe"] = [["b", -1.0, 0.0, -0.6, 1.0, 1.0, 0.6]]


def _lone_table(wire_dx: float) -> list:
    """The table as a one-piece body, judged the way `run` judges one: by
    `lone_placed` and then burial alone.  `wire_dx` is where the anchor's
    own spawn_object row put its table, relative to this one."""
    table = I.mk_solid("table_probe", (0.0, 40.57, 4425.5), I.yaw(0),
                       _GEOM["table_probe"])
    anchor = dict(site_id="complete-station-hub", role="fixtures",
                  x=-4.0, z=4417.5, prefabs={"table_probe"}, seqs={1978},
                  declares_a_body=False,
                  commands=[("table_probe", wire_dx, 4425.5)])
    small = [b for b in I.bodies([table], slack=0.35)
             if len(b) < MIN_BODY_PIECES]
    return check_grounding(lone_placed(small, [anchor]),
                           _ground_for([table], "bank", table.ymax + 1.982),
                           _cols(), _GEOM, judge_support=False)


def _case_a_lone_placed_prop_under_the_bank_is_buried():
    """A PLACED PIECE IS JUDGED FOR BURIAL HOWEVER FEW PIECES IT STANDS
    WITH - see `lone_placed`."""
    return _lone_table(0.0)


def _case_a_lone_piece_our_wire_did_not_place_is_not_ours():
    """AND ONLY OURS: the same buried table 0.5 m from the row our plan
    wrote is the world's, however near our anchor it stands."""
    return _lone_table(0.5)


def _neuter_lone_pieces_unjudged():
    """THE DEFECT: a body under MIN_BODY_PIECES is never judged."""
    global _LONE_PIECES_UNJUDGED
    was = _LONE_PIECES_UNJUDGED
    _LONE_PIECES_UNJUDGED = True

    def undo():
        global _LONE_PIECES_UNJUDGED
        _LONE_PIECES_UNJUDGED = was
    return undo


def _neuter_lone_pieces_by_proximity():
    """THE OTHER DIRECTION: a lone piece is ours if our anchor's plan named
    its prefab anywhere within ANCHOR_MARGIN_M - proximity for provenance."""
    global ATTRIBUTION_M
    was = ATTRIBUTION_M
    ATTRIBUTION_M = ANCHOR_MARGIN_M

    def undo():
        global ATTRIBUTION_M
        ATTRIBUTION_M = was
    return undo


# woodiron_beam_45, to scale: a 45 degree beam 2.263 m tall and 1.8 m across,
# whose top is 0.536 m PROUD of the ground and whose middle is 0.495 m under
# it.  Built here from one parallelepiped so the case owns its own geometry.
_DIAG = [["p", -0.9, -1.1315, -0.2, 0.9, -1.1315, -0.2,
          -0.9, -0.9315, -0.2, 0.9, -0.9315, -0.2,
          -0.9, 0.9315, 1.6, 0.9, 0.9315, 1.6,
          -0.9, 1.1315, 1.6, 0.9, 1.1315, 1.6]]
_GEOM["diag_beam"] = _DIAG


def _case_a_piece_that_spans_height_is_not_buried_by_one_column():
    """A DIAGONAL BEAM 0.536 m PROUD OF THE GROUND, CALLED INVISIBLE.

    MEASURED on Ulfsland save 5385652e: `sudrberg-keep` was refused for a
    `woodiron_beam_45` at (2415.064, 121.244, -3351.906) that "lies
    COMPLETELY under the applied surface, 0.495 m".  The beam spans y
    120.113..122.376, the applied ground is 121.840, and its own top is
    HALF A METRE IN THE AIR.  The 0.495 m was the top of the slice the one
    lattice vertex under it cut out of the diagonal - 302 identical beams
    stand in that body and which 2 fired was decided by where the metre
    lattice fell on the slope.  `vestvik-villa-3` was the same shape on a
    `wood_door`: both its vertices land in the doorway, where the only
    solid is a 1.8 mm threshold sill, so 1.911 m of standing door read as
    buried.

    THE REMEDY EITHER FINDING HAS IS `retire`, ROLE `buried_piece`.  Two
    recorded demolitions of standing structure, one of them a door, waiting
    on somebody to action a number.  So: the fixture puts the beam's midriff
    under the surface and its head above, and the check must ACCEPT it.
    """
    beam = I.mk_solid("diag_beam", (0.0, 40.0, 0.0), I.yaw(0), _DIAG)
    assert round(beam.ymax, 3) == 41.132, beam.ymax
    body = [beam] + [I.mk_solid("stone_floor_2x2", (float(x), 41.0, 4.0),
                                I.yaw(0), _SLAB) for x in (-2, 0, 2)]
    g = {gkey(x, z): 40.596 for (x, z) in
         [(a, b) for s in body for (a, b) in piece_columns(s)]}
    # THE TWO NUMBERS THE TWO RULES READ, on this fixture: the beam's OWN
    # top stands 0.536 m proud of that surface, and the highest slice the
    # metre lattice cuts out of the diagonal tops out 0.152 m UNDER it.  The
    # shipped rule reads the second and calls a beam standing half a metre
    # in the air invisible.
    assert round(beam.ymax - 40.596, 3) == 0.536, beam.ymax
    assert round(max(_cols().span(beam, x, z)[1] for (x, z)
                     in piece_columns(beam)) - 40.596, 3) == -0.152
    return check_grounding([(body, [], "keep")], g, _cols(), _GEOM)


def _neuter_the_per_column_top():
    """THE DEFECT: decide 'buried' from the top of the piece's slice in the
    sampled column instead of from the top of the piece.  Restored by the
    module flag `check_grounding` reads, so the neuter puts back the shipped
    ARITHMETIC and touches nothing else."""
    g = check_grounding.__globals__
    was = g["_BURIED_PER_COLUMN"]
    g["_BURIED_PER_COLUMN"] = True

    def undo():
        g["_BURIED_PER_COLUMN"] = was
    return undo


_PAD_SAMPLES = [(float(x), float(z)) for x in range(-5, 6)
                for z in range(-5, 6)]


def _pad(spread: float, offset: float = 0.0, centre=(0.0, 0.0),
         own=None, current: bool = True, target: float = 40.0,
         wrote=None, rect_owner=None, extra: dict | None = None
         ) -> tuple[dict, dict]:
    """A one-pad population.

    `own` is what this write still OWNS; `wrote` is what it EVER SET and
    defaults to `own`, because for a pad nobody has overwritten they are the
    same set and every pre-existing case means that.  `rect_owner` is the
    per-sample holder today, keyed as `pad_populations` keys it -
    `(seq, role, site_id)` - and defaults to this pad owning everything it
    owns.  `extra` adds further pad records to the population, which is how a
    contested sample gets a second declared promise to be measured against.
    """
    own = _PAD_SAMPLES if own is None else own
    rect = pad_rect(centre[0], centre[1], 10.0, 10.0, 0.0)
    mine = (1, "site_pad", "probe")
    ws = set(own if wrote is None else wrote)
    ro = ({w: mine for w in own} if rect_owner is None else rect_owner)
    pads = {"probe": dict(seq=1, site_id="probe", target_y=target,
                          pad_w=10.0, pad_d=10.0, apron_m=0.0,
                          actor="selftest", tol_m=0.10, centre=list(centre),
                          centre_declared=True,
                          centre_src="the record's own params.centre",
                          tol_src="the selftest's own declared tolerance",
                          wrote=len(own), own=own, rect=rect,
                          inside=[w for w in own if w in set(rect)],
                          inside_site=[
                              w for w in set(rect)
                              if (ro.get(w) or (None, None, None))[2]
                              == "probe"],
                          current=current,
                          rect_wrote={w for w in ws if w in set(rect)},
                          rect_owner={w: t for w, t in ro.items()
                                      if w in set(rect)})}
    if extra:
        pads.update(extra)
    g = {}
    n = len(_PAD_SAMPLES) - 1
    for i, _p in enumerate(_PAD_SAMPLES):
        g[gkey(_p[0], _p[1])] = target + offset + (spread * i / n
                                                   if n else 0.0)
    return pads, g


def _case_pad_sloped():
    """A pad asked to be dead flat that slopes.  Four did, by 6.593 / 3.478 /
    1.495 / 0.939 m; 0.939 is the smallest of them and is used here so the
    case fails for the smallest real defect, not the most obvious one."""
    pads, g = _pad(0.939)
    return check_flatness(pads, g)


def _case_pad_flat():
    pads, g = _pad(0.0)
    return check_flatness(pads, g)


def _case_pad_flat_but_low():
    """FLAT AT THE WRONG HEIGHT.  A pad can be dead flat 2 m below the datum
    every building on it was placed against, and a spread-only check passes
    it.  This is the grounding defect and the flatness defect being the same
    defect, and the check must refuse it on OFFSET even though SPREAD is
    0.000."""
    pads, g = _pad(0.0, offset=-2.0)
    return check_flatness(pads, g)


def _case_a_reemit_inherits_its_own_sites_samples():
    """THE BETTER THE REPAIR, THE SMALLER THE POPULATION - UNTIL IT IS ZERO.

    A pad re-emit only owns the samples it CHANGED.  So a re-emit that finds
    its interior already correct and moves only the edge owns none of its own
    rectangle, and a population keyed by RECORD reports "0 samples ... and no
    applied reading for any of them - unmeasured" over a pad that is dead
    flat and fully readable.  For a CURRENT record that is a refusal, so the
    gate punishes the cleanest possible repair.

    MEASURED on Ulfsland: `vestvik-villa-3` seq 5375 re-levelled its pad,
    correctly moved only the 34 edge samples that were off target, and came
    out rect=288 own=18 inside=0 -- with all 288 rectangle samples owned by
    `(502, 'site_pad', 'vestvik-villa-3')`, ITS OWN EARLIER RECORD.

    The fixture is that state exactly: the current record owns NONE of its
    rectangle, an earlier record OF THE SAME SITE owns all of it, and the
    ground is dead flat on target.  The check must MEASURE it and accept.
    """
    pads, g = _pad(0.0, own=[], wrote=_PAD_SAMPLES,
                   rect_owner={w: (502, "site_pad", "probe")
                               for w in _PAD_SAMPLES})
    return check_flatness(pads, g)


def _neuter_the_site_keyed_population():
    """RESTORE THE DEFECT: key the current record's population by RECORD
    again, so a re-emit sees only the samples it changed itself and a pad
    whose interior was already correct comes back with a population of 0."""
    real = check_flatness

    def by_record(pads, ground):
        return real({k: dict(p, inside_site=p["inside"])
                     for k, p in pads.items()}, ground)

    globals()["check_flatness"] = by_record

    def undo():
        globals()["check_flatness"] = real

    return undo


def _replacement(sid: str = "later", seq: int = 2, target: float = 40.0):
    """A CURRENT pad record whose own declared rectangle is the probe's,
    which is what "somebody else answers for this ground now" means."""
    rect = pad_rect(0.0, 0.0, 10.0, 10.0, 0.0)
    return {sid: dict(
        seq=seq, site_id=sid, target_y=target, pad_w=10.0, pad_d=10.0,
        apron_m=0.0, actor="selftest", tol_m=0.10, centre=[0.0, 0.0],
        centre_declared=True, centre_src="the record's own params.centre",
        tol_src="the selftest's own declared tolerance", wrote=len(rect),
        own=list(rect), rect=rect, inside=list(rect), inside_site=list(rect),
        current=True, rect_wrote=set(rect),
        rect_owner={w: (seq, "site_pad", sid) for w in rect})}


def _replaced_pad(answered: bool, readable: bool = True):
    """A pad record that owns NOTHING of its own rectangle because a later
    write re-levelled all of it.  `answered` decides whether that later write
    is a CURRENT pad record whose own declared rectangle covers the ground -
    i.e. whether anybody is judged over it today.

    THE PROBE IS CURRENT ON PURPOSE.  A superseded record's findings are
    advisory and an advisory finding cannot decide a verdict, so a superseded
    fixture could never prove any of these three states in either direction.
    The state is identical for a current record - `inside_site` already hands
    a current record every sample its OWN SITE owns, so reaching here means
    no record of its site owns any of its rectangle - and this way the case
    can fail."""
    later = (2, "site_pad", "later")
    pads, g = _pad(0.0, own=[], wrote=_PAD_SAMPLES,
                   rect_owner={w: later for w in _PAD_SAMPLES},
                   extra=_replacement() if answered else None)
    return pads, (g if readable else {})


def _probe_only(fs):
    """This fixture's OWN record, because `_replacement` is a second pad in
    the same population and is judged in its own right."""
    return [f for f in fs if f.message.startswith("pad probe")]


def _case_a_pad_replaced_in_full_is_measured_not_unmeasured():
    """`0 of 441 samples ... and no applied reading for any of them`.

    THE SECOND CLAUSE IS VACUOUS AND THAT IS THE DEFECT.  The population was
    empty, so the applied surface was never asked - and "nobody looked" was
    printed in the same words as "the ground is not there".  MEASURED on
    Ulfsland at save 2a6940a934b5, over all seven records in this state,
    2,333 distinct lattice samples: 2,333 READABLE, 0 unreadable, 0 unowned,
    2,333 inside a current record's rectangle, and six of the seven dead flat
    at spread 0.000 m.  `wt-peak@seq545` - the `0 of 441` - reads 96.656 m
    over all 441, which is its own site's later record seq 3150's target.

    The fixture is that state: a record owning none of its 121 rectangle
    samples, all of them held and answered for by another current record, and
    the ground readable and flat.  The check must READ THE RECTANGLE and say
    REPLACED IN FULL with the numbers, not refuse it as unmeasured."""
    return _probe_only(check_flatness(*_replaced_pad(answered=True)))


def _case_a_replaced_pad_nobody_answers_for_is_refused():
    """AND THE REFUSAL THE ESCAPE MUST NOT LOSE.

    The same record, the same empty population, the same readable ground -
    but the write that took its samples is NOT a current pad record whose own
    rectangle contains them, so `check_pad_coverage` never judges that ground
    either.  Excusing this would retire 121 samples of a declared pad out of
    the gate entirely on the strength of somebody else's bytes holding
    them."""
    return _probe_only(check_flatness(*_replaced_pad(answered=False)))


def _case_a_pad_rectangle_that_cannot_be_read_is_refused():
    """AN UNREADABLE RECTANGLE IS NOT A CLEAN ONE.  Same record, same full
    replacement by a current record - and the applied-surface instrument
    returns nothing for any of the 121 samples.  That is a hole in the
    MEASUREMENT, and the finding that hid this state for seven records said
    exactly the same words about it as about dead-flat ground."""
    return _probe_only(
        check_flatness(*_replaced_pad(answered=True, readable=False)))


def _neuter_the_empty_population_to_unmeasured():
    """RESTORE THE DEFECT VERBATIM: the one sentence over four states."""
    g = globals()
    was = g["_PAD_EMPTY_BLANKET"]
    g["_PAD_EMPTY_BLANKET"] = True

    def undo():
        g["_PAD_EMPTY_BLANKET"] = was
    return undo


def _neuter_the_answer_to_bookkeeping():
    """RESTORE THE DEFECT THE ESCAPE MUST NOT BECOME: excuse a sample because
    SOMEBODY ELSE'S BYTES HOLD IT, rather than because a current pad record's
    own declared rectangle contains it and is judged over it.  That is the
    shape `check_pad_coverage` was explicitly written not to take."""
    g = globals()
    was = g["_PAD_ANSWER_BY_OWNERSHIP"]
    g["_PAD_ANSWER_BY_OWNERSHIP"] = True

    def undo():
        g["_PAD_ANSWER_BY_OWNERSHIP"] = was
    return undo


def _neuter_the_unreadable_sample_to_its_target():
    """RESTORE THE DEFECT: a sample the instrument could not read counted as
    if it read the pad's own target, which turns an unmeasured rectangle into
    a perfect one."""
    g = globals()
    was = g["_PAD_UNREAD_IS_TARGET"]
    g["_PAD_UNREAD_IS_TARGET"] = True

    def undo():
        g["_PAD_UNREAD_IS_TARGET"] = was
    return undo


def _case_pad_edge_not_counted():
    """THE 1.634 m THAT WAS NOT THERE.  A lattice box around a pad includes
    unwritten natural ground outside it and reported an error 36x the real
    one.  Here the pad's own samples are flat and the ground one metre beyond
    them falls away 1.634 m; the check must ACCEPT, because that ground is
    not the pad."""
    pads, g = _pad(0.0)
    for i in range(40):
        g[gkey(100.0 + i, 100.0)] = 40.0 - 1.634
    return check_flatness(pads, g)


def _case_pad_skirt_outside_its_own_rectangle():
    """THE SAMPLES A PAD MOVED BUT NEVER PROMISED.

    Two pads in one zone share ONE `_TerrainCompiler`
    (`terraform/flatten.py:236`), so a pad's blob carries its neighbour's
    deltas and the difference that recovers "what this write did" hands them
    to whichever record saw them first.  MEASURED on Ulfsland:
    `stenvik-stonehouse-2` seq 279 declares an 11 x 15 m pad at
    (478.5, 869.6); 180 of its 190 attributed samples are exactly the 12 x 15
    lattice of that rectangle and every one reads 47.210, and the other 10
    sit at z = 908, 31 m away, reading 47.030 - `stenvik-hall-1`'s target.
    Held to 47.210 those 10 made a 0.180 m refusal.

    Here the pad's own rectangle is dead flat and ten samples 30 m away are
    0.180 m off.  The check must ACCEPT, because the write never promised
    them - and the case asserts in the other direction too, that the same ten
    samples DO refuse when the record declares no centre, since then its
    promise genuinely cannot be separated from the spill.
    """
    skirt = [(float(x), 30.0) for x in range(-5, 5)]
    pads, g = _pad(0.0, own=_PAD_SAMPLES + skirt)
    for (x, z) in skirt:
        g[gkey(x, z)] = 40.0 - 0.180
    blind = dict(pads)
    blind["probe"] = dict(pads["probe"], centre=None, rect=None,
                          centre_declared=False,
                          centre_src=("NOWHERE - the record declares no "
                                      "centre and no spawn_plan anchor "
                                      "names this site"),
                          inside=pads["probe"]["own"],
                          inside_site=pads["probe"]["own"])
    if not [f for f in check_flatness(blind, g)
            if not f.ok and not f.advisory]:
        return [Finding(
            check="pad_flatness", ok=False,
            message="fixture is wrong: the no-centre population no longer "
                    "refuses the skirt, so this case records nothing",
            numbers={"skirt_samples": len(skirt), "offset_m": 0.180})]
    return check_flatness(pads, g)


def _case_pad_superseded_record_is_not_the_verdict():
    """A SITE WRITTEN TWICE WITH TWO TARGETS IS TWO PROMISES.

    MEASURED: `tree-sth-2` is seq 633 at target 75.700 and seq 669 at 76.190;
    `tree-sth-3` is seq 640 at 75.700 and seq 676 at 75.000.  Keyed by site
    and judged against the LATER target, seq 633's 16 surviving samples were
    0.490 m "out" and seq 640's 28 were 0.700 m "out" - two of this section's
    four pad refusals.  Judged against the target of the record that actually
    wrote them, both read dead on it, spread 0.000.

    Here the superseded record is flat at its own target.  It must not carry
    the verdict: the finding is advisory.
    """
    pads, g = _pad(0.0, current=False, target=75.70)
    return check_flatness(pads, g)


def _case_pad_row_nobody_wrote():
    """THE 59 SAMPLES NO POPULATION CONTAINED.

    `terraform/flatten.py` selected pad samples with
    `abs(wx - cx) > half_w -> continue`.  A solved centre is a decimal that
    binary cannot hold, so 886.6 - 879.0 evaluates to 7.6000000000000085
    against a half_d of 7.6 and the whole edge row is dropped.  MEASURED over
    the 63 site_pad records on Ulfsland: four first writes each lost exactly
    one full edge row or column - `stenvik-house-1` z=879 (20 samples),
    `stenvik-cottage-3` x=552 (12), `stenvik-hut-1` z=892 (10),
    `vestvik-villa-2` x=-876 (17).  Read back off the save, 54 of those 59
    are more than 0.10 m off their pad's own target and the worst is
    1.589 m - under a building, inside a rectangle the record says it
    levelled.

    EVERY ONE OF THEM WAS INVISIBLE, and that is the point of this case: a
    check whose population is "the samples this pad wrote" cannot see a
    sample this pad failed to write, no matter how exactly it measures the
    ones it did.  So the fixture drops the z = -5 row from `own` and leaves
    it at natural height, and asserts BOTH directions - that `check_flatness`
    over the written samples PASSES (it is blind, by construction) and that
    `check_pad_coverage` over the declared rectangle REFUSES.
    """
    kept = [w for w in _PAD_SAMPLES if w[1] != -5.0]
    pads, g = _pad(0.0, own=kept)
    for x in range(-5, 6):
        g[gkey(float(x), -5.0)] = 40.0 - 1.589
    if [f for f in check_flatness(pads, g) if not f.ok and not f.advisory]:
        return [Finding(
            check="pad_coverage", ok=False,
            message="fixture is wrong: the written-sample population now "
                    "refuses the unwritten row by itself, so this case no "
                    "longer records the blindness it exists for",
            numbers={"unwritten_row_samples": 11, "offset_m": 1.589})]
    return check_pad_coverage(pads, g)


def _neighbour(target: float, tol: float = 0.10, seq: int = 2,
               sid: str = "neighbour", centre=(8.0, 0.0)):
    """A SECOND pad record whose rectangle overlaps the probe's.

    Stenvik is a dense village and this is its real geometry: `tree-sth-1`
    (541, 487.9) and `tree-sth-3` (552.9, 476) are both 16 x 16 m and their
    rectangles overlap, so the later pad's datum governs the overlap.

    IT IS MARKED SUPERSEDED, and that is the real shape rather than a
    convenience: the ground at `stenvik-cottage-2`'s ten contested samples
    reads 47.030, which is `stenvik-hall-1`'s target, and hall-1's record is
    NOT the one whose bytes hold them - `stenvik-stonehouse-2` seq 279 is.  A
    promise a later record replaced still describes the ground its own
    samples were levelled to, which is exactly what `pad_promises` says and
    why it keeps superseded records.  It also keeps the fixture honest: only
    the probe carries a verdict here, so the case measures the probe's
    population and nothing else.
    """
    rect = pad_rect(centre[0], centre[1], 10.0, 10.0, 0.0)
    return {sid: dict(seq=seq, site_id=sid, target_y=target, pad_w=10.0,
                      pad_d=10.0, apron_m=0.0, actor="selftest", tol_m=tol,
                      centre=list(centre), centre_declared=True,
                      centre_src="the record's own params.centre",
                      tol_src="the selftest's own declared tolerance",
                      wrote=len(rect), own=rect, rect=rect, inside=rect,
                      inside_site=rect,
                      current=False, rect_wrote=set(rect),
                      rect_owner={w: (seq, "site_pad", sid) for w in rect})}


def _case_a_later_pad_relevelled_it_and_that_is_not_a_hole():
    """TWO OVERLAPPING PADS, AND THE LATER ONE'S DATUM GOVERNS THE OVERLAP.

    THE REAL STATE, MEASURED at save 2c7011d2: five of this section's ten
    pad_coverage refusals had written EVERY sample of their own rectangle -
    `vestvik-house-1` 300/300, `tree-sth-1` 272/272, `tree-near-1` 256/256 -
    and their "off-target" samples read another pad's target EXACTLY:
    tree-sth-1's 25 read 75.000, which is tree-sth-3's; vestvik-house-1's 20
    read 35.690, vestvik-stonehouse-1's; tree-near-1's 16 read 36.160,
    tree-near-2's.  The ground keeps a declared promise; it is simply not
    this record's.  Holding it to this record's target asks for a fill on top
    of a finished neighbouring pad, and a fill at a pad edge is how a
    standing piece gets buried.

    The x = +5 column of the probe's rectangle is inside the neighbour's
    rectangle too, and reads the neighbour's target.  The probe WROTE those
    samples and the neighbour now holds them, so they are CEDED and the pad
    must PASS.
    """
    nb = _neighbour(41.4)
    mine = (1, "site_pad", "probe")
    nbtag = (2, "site_pad", "neighbour")
    owners = {w: (nbtag if w[0] == 5.0 else mine) for w in _PAD_SAMPLES}
    pads, g = _pad(0.0, rect_owner=owners, extra=nb)
    for z in range(-5, 6):
        g[gkey(5.0, float(z))] = 41.4
    return check_pad_coverage(pads, g)


def _case_a_dropped_sample_a_neighbour_holds_is_still_refused():
    """AND THE POPULATION-TOO-SMALL DEFECT THE SAME ESCAPE COULD HAVE BEEN.

    If ceding needed only "somebody else holds it", a sample THIS pad never
    wrote would go quiet the moment any later write happened to cover it -
    and that is exactly the class the coverage check exists for: four dropped
    edge rows on this world, up to 1.589 m off, under buildings.  Trading a
    population that is too big for one that is too small is the worse of the
    two trades, because the refusal disappears instead of being loud and
    wrong.

    Same geometry as the accept case, with ONE difference: the probe never
    wrote the x = +5 column.  The neighbour holds it and keeps its own
    promise there, and the pad must STILL REFUSE, because a sample it never
    set is its own coverage gap however the ground reads.
    """
    nb = _neighbour(41.4)
    mine = (1, "site_pad", "probe")
    nbtag = (2, "site_pad", "neighbour")
    kept = [w for w in _PAD_SAMPLES if w[0] != 5.0]
    owners = {w: (nbtag if w[0] == 5.0 else mine) for w in _PAD_SAMPLES}
    pads, g = _pad(0.0, own=kept, wrote=kept, rect_owner=owners, extra=nb)
    for z in range(-5, 6):
        g[gkey(5.0, float(z))] = 41.4
    return check_pad_coverage(pads, g)


def _case_a_road_that_cut_the_pad_is_the_roads_section():
    """A CARRIAGEWAY IS NOT A BUILDING FLOOR, AND IT IS MEASURED ELSEWHERE.

    MEASURED: `stenvik-house-3` wrote all 300 samples of its own rectangle and
    18 of them are now `road_T8_stenvik_wttown`'s bed, 0.407 m above the pad's
    47.150.  A road bed is declared by a centreline and a width, not by a
    `target_y`, and the `roads` section measures it over its own population -
    148 refusals of its own at this save.  Charging that ground to structures
    as well is double-counting it, and the only way to satisfy the pad's datum
    there is to un-build the road.
    """
    mine = (1, "site_pad", "probe")
    road = (9, "road_segment", "road_T8_stenvik_wttown")
    owners = {w: (road if w[0] == 5.0 else mine) for w in _PAD_SAMPLES}
    pads, g = _pad(0.0, rect_owner=owners)
    for z in range(-5, 6):
        g[gkey(5.0, float(z))] = 40.407
    return check_pad_coverage(pads, g)


def _case_pad_full_rectangle_is_clean():
    """The same rectangle with every sample written and on target must PASS,
    so the coverage check is not simply a refusal generator."""
    pads, g = _pad(0.0)
    return check_pad_coverage(pads, g)


def _case_a_lamp_did_not_build_a_farmhouse():
    """PROXIMITY IS NOT PROVENANCE.

    MEASURED on Ulfsland: `lighting-T13`'s plan is ONE `piece_groundtorch`
    (`spawn_plan` seq 2206, `prefabs: ["piece_groundtorch"]`) and 11.6 m away
    stands a 30-piece body of 17 `woodwall`, 6 `wood_pole`, 4 `wood_floor`
    and 2 `wood_wall_half` whose own `LocationProxy` has NO row in the
    mod-free 12,301-instance generator dump
    (`roads/mod_keepout.json` census-T13_temple_harboursouth-039,
    `placer: mod-placed`, `forbid: true`).  Extent-plus-margin claimed it and
    this section reported "lighting-T13: 16 of 30 pieces lie COMPLETELY under
    the applied surface, worst 4.092 m" - a real burial, of somebody else's
    farmhouse, filed against a lamp that cannot emit a wall.

    The case asserts BOTH directions: the woodwall body must NOT be claimed
    by the lamp anchor, and a body that DOES hold the declared prefab must
    still be claimed.
    """
    # Both bodies are WITHIN the anchor's reach - 11.6 m apart, as at T13 -
    # so extent-plus-margin alone claims both and only the prefab list can
    # tell them apart.
    hut = [I.mk_solid("woodwall", (9.0 + float(x), 40.0, 0.0), I.yaw(0),
                      _WALL) for x in (0, 1, 2)]
    lamp = [I.mk_solid("wood_pole_log_4", (float(x), 40.0, 0.0),
                       I.yaw(0), _POST) for x in (0, 1, 2)]
    anchor = dict(role="road_lighting", site_id="lighting-probe",
                  x=0.5, z=0.0, y=40.0, actor="selftest",
                  prefabs={"wood_pole_log_4"})
    matched, orphan, foreign, _sc, _re = our_bodies([hut, lamp], [anchor])
    out = []
    claimed = [b for b, _a in matched]
    if any(b is hut for b in claimed):
        out.append(Finding(
            check="foreign_body", ok=False,
            message=("a road_lighting plan declaring only wood_pole_log_4 "
                     "still claimed a 3-piece woodwall body 0.5 m away"),
            numbers={"claimed": len(claimed)}))
    if not any(b is lamp for b in claimed):
        out.append(Finding(
            check="foreign_body", ok=False,
            message=("the body that DOES hold the declared prefab was not "
                     "claimed - the discriminator has become a rejection of "
                     "everything"),
            numbers={"claimed": len(claimed)}))
    if len(foreign) != 1:
        out.append(Finding(
            check="foreign_body", ok=False,
            message=(f"the unclaimed neighbour must be REPORTED as foreign, "
                     f"not dropped: {len(foreign)} reported"),
            numbers={"foreign": len(foreign)}))
    if not out:
        out.append(Finding(
            check="foreign_body", ok=True,
            message=("the lamp plan claimed only the body holding its own "
                     "declared prefab; the woodwall body was reported as "
                     "foreign and not judged as ours"),
            numbers={"claimed": len(claimed), "foreign": len(foreign),
                     "orphan": len(orphan)}))
    return out


def _lamp_plan(spacing_m: float, n: int = 5, packed: bool = False,
               seq: int = 2690) -> dict:
    """A `spawn_plan` record whose wire places `n` torches `spacing_m` apart
    along Z, in the WIRE'S OWN z,x,y order.

    `packed` puts every command on ONE semicolon-joined line, which is the
    shape 226 of this chain's 421 records actually have.  `seq` is carried
    because a `retire` names the RECORD it undoes, not the site.
    """
    cmds = [f"spawn_object piece_groundtorch "
            f"pos={k * spacing_m:.4f},0.0000,40.0000 rot=0,0,0"
            for k in range(n)]
    return {"op": "spawn_plan", "actor": "selftest", "seq": seq,
            "params": {"role": "road_lighting", "site_id": "lighting-probe",
                       "anchor": {"x": 0.0, "y": 40.0, "z": 0.0},
                       "command_count": n,
                       "prefabs": ["piece_groundtorch"]},
            "wire": [";".join(cmds)] if packed else cmds}


def _retire_rec(z: float, seq: int, retires: list, r: float = 0.4,
                prefab: str = "piece_groundtorch") -> dict:
    """A `retire` record naming ONE commanded pivot, keyed the way the chain's
    own retires are: prefab, a three-axis position, its own `radius_m`, and
    the `retires` list citing the record it undoes."""
    return {"op": "retire", "actor": "selftest", "seq": seq,
            "params": {"prefab": prefab, "pos": [0.0, 40.0, z],
                       "radius_m": r, "retires": retires,
                       "role": "road_lighting", "site_id": "lighting-probe",
                       "reason": "removed so a ship can enter the harbour"}}


def _case_a_packed_wire_declares_every_position():
    """THE TRUNCATION FAMILY, CAUGHT BEFORE IT SHIPS.

    MEASURED on this chain: 226 of 421 `spawn_plan` records have
    `len(wire) != params.command_count`, because the wire is SEMICOLON-PACKED
    - seq 72 stenvik-hall-1 declares 612 commands in 14 lines, seq 137
    stenvik-cottage-3 declares 48 in ONE.  A reader that counted LINES would
    see a 48-piece cottage declare one position, fall under MIN_BODY_PIECES
    and be excused as a scatter, and it would do that to every blueprint body
    in Stenvik and Vestvik - while a one-command-per-line fixture passed.

    So the same three commands are read twice, once per line and once packed
    onto one, and the two MUST agree.  The count is also asserted against the
    record's own `command_count`, and a record that disagrees is UNRECOVERABLE
    and stays in the orphan population rather than escaping into the scatter.
    """
    out = []
    loose = plan_commands(_lamp_plan(1.0, 3, packed=False))
    packed = plan_commands(_lamp_plan(1.0, 3, packed=True))
    if packed is None or len(packed) != 3:
        out.append(Finding(
            check="orphan_plan", ok=False,
            message=(f"three commands packed onto ONE line parsed as "
                     f"{'None' if packed is None else len(packed)}, not 3: "
                     f"the reader is counting lines, and every blueprint body "
                     f"on this chain would be reclassified as a scatter"),
            numbers={"parsed": -1 if packed is None else len(packed)}))
    elif loose != packed:
        out.append(Finding(
            check="orphan_plan", ok=False,
            message="the same commands read differently packed and loose",
            numbers={"loose": len(loose), "packed": len(packed)}))
    lying = _lamp_plan(1.0, 3, packed=True)
    lying["params"]["command_count"] = 48
    if plan_commands(lying) is not None:
        out.append(Finding(
            check="orphan_plan", ok=False,
            message=("a record declaring 48 commands and carrying 3 was "
                     "accepted; an unreadable wire must stay an ORPHAN, "
                     "because silence is not evidence of a scatter"),
            numbers={"declared": 48}))
    if not out:
        out.append(Finding(
            check="orphan_plan", ok=True,
            message=("positions are counted by occurrence of pos=, not by "
                     "line, and the count is asserted against the record's "
                     "own command_count"),
            numbers={"loose": len(loose), "packed": len(packed)}))
    return out


def _case_the_wire_is_z_x_y():
    """`pos=` IS z,x,y - `Parse::VectorZXYRange` - AND NOTHING DOWNSTREAM CAN
    CATCH IT IF THIS IS WRONG.

    Swapping x and z for every commanded point is an ISOMETRY: it leaves every
    pairwise distance unchanged, so `clusters_into_a_body` returns exactly the
    same verdict on a transposed read.  A transposition would therefore pass
    every clustering case ever written and put every plan's geometry in the
    wrong place silently.  It is pinned here, directly, against a MEASURED
    record: seq 72's wire reads `pos=926.9673,473.5581,44.9331` and
    stenvik-hall-1 stands at x 473.6, z 927.0.
    """
    rec = {"op": "spawn_plan", "actor": "selftest",
           "params": {"role": "town_body", "site_id": "stenvik-hall-1",
                      "anchor": {"x": 481.1, "y": 47.03, "z": 920.4},
                      "command_count": 1, "prefabs": ["wood_stair"]},
           "wire": ["spawn_object wood_stair "
                    "pos=926.9673,473.5581,44.9331 rot=0,80,0 from=0,0,0"]}
    got = plan_commands(rec)
    if not got:
        return [Finding(check="orphan_plan", ok=False,
                        message="the measured seq 72 command did not parse",
                        numbers={})]
    _pf, x, z = got[0]
    if abs(x - 473.5581) > 1e-6 or abs(z - 926.9673) > 1e-6:
        return [Finding(
            check="orphan_plan", ok=False,
            message=(f"the wire was read as x={x}, z={z}; `pos=` is z,x,y and "
                     f"stenvik-hall-1 stands at x 473.6, z 927.0, so this "
                     f"read is transposed - and no clustering case can catch "
                     f"it, because a global x/z swap preserves every distance"),
            numbers={"x": x, "z": z})]
    return [Finding(check="orphan_plan", ok=True,
                    message="the wire is read z,x,y, as the builders send it",
                    numbers={"x": x, "z": z})]


def _neuter_the_scatter_split():
    """RESTORE THE DEFECT: every plan judged against a BODY.

    `clusters_into_a_body` forced to always-True IS the check as it stood
    before this measurement - and it is the state in which 25 of 25
    `road_lighting` records on this chain were reported as orphaned plans for
    want of a building five torches 30 m apart could never have made.
    Returns its own undo.
    """
    mod = sys.modules[__name__]
    real = mod.clusters_into_a_body
    mod.clusters_into_a_body = lambda *a, **k: True

    def undo():
        mod.clusters_into_a_body = real
    return undo


def _neuter_the_scatter_escape_to_presence():
    """RESTORE THE DEFECT THE ESCAPE MUST NOT BECOME: any plan may claim to be
    a scatter.

    `clusters_into_a_body` forced to always-False is the weakest possible
    escape - the shape a PRESENCE test ("one of my prefabs stands nearby")
    would have - and it excuses a 5,553-piece castle demolished down to one
    stray pillar, which is the exact state `orphan_plan` exists to refuse.
    """
    mod = sys.modules[__name__]
    real = mod.clusters_into_a_body
    mod.clusters_into_a_body = lambda *a, **k: False

    def undo():
        mod.clusters_into_a_body = real
    return undo


def _neuter_the_wire_reader_to_lines():
    """RESTORE THE DEFECT: count LINES, not occurrences of `pos=`.

    226 of this chain's 421 `spawn_plan` records are SEMICOLON-PACKED, so a
    line-counting reader sees a 48-piece cottage declare ONE position and
    reclassifies every blueprint body in Stenvik and Vestvik as a scatter.
    """
    mod = sys.modules[__name__]
    real = mod.plan_commands

    def by_line(rec):
        out = []
        for w in (rec.get("wire") or ()):
            m = _SPAWN_POS_RE.match(str(w).strip())
            if not m:
                continue
            try:
                z, x, _y = (float(t) for t in m.group("pos").split(",")[:3])
            except ValueError:
                continue
            out.append((m.group("prefab"), x, z))
        return out
    mod.plan_commands = by_line

    def undo():
        mod.plan_commands = real
    return undo


def _neuter_the_wire_transposition():
    """RESTORE THE DEFECT: read `pos=` as x,y,z instead of z,x,y.

    Nothing downstream can catch this - a global x/z swap is an ISOMETRY and
    leaves every pairwise distance unchanged, so `clusters_into_a_body`
    returns the same verdict either way.  That is precisely why the parse
    needs a case of its own.
    """
    mod = sys.modules[__name__]
    real = mod.plan_commands

    def transposed(rec):
        got = real(rec)
        return None if got is None else [(pf, z, x) for pf, x, z in got]
    mod.plan_commands = transposed

    def undo():
        mod.plan_commands = real
    return undo


def _case_a_scattered_plan_is_not_an_orphan():
    """THE CHECK THAT COULD NEVER PASS ON A ROLE.

    `orphan_plan` asks whether a body of MIN_BODY_PIECES+ build pieces stands
    within ANCHOR_MARGIN_M of a plan's anchor.  A road_lighting plan is single
    torches 30 m apart down a road shoulder; they cannot cluster into a body
    however faithfully they were placed, so the answer is always no.

    MEASURED on Ulfsland before any repair: 25 of the 27 orphan_plan
    advisories were road_lighting, and they were EVERY road_lighting record on
    the chain - 25 of 25, without exception - rising to 41 of 41 as more were
    written.  A check that has never once been able to pass on a role is not
    measuring that role, it is counting it, and the count was hiding the two
    findings that were real.

    ACCEPT: the plan's own commands are 30 m apart, so it is a SCATTER and no
    body is expected.  The case fails if it is reported as an orphan.
    """
    anchors = ledger_anchors(_FakeLedger([_lamp_plan(30.0)]))
    out = []
    if len(anchors) != 1:
        return [Finding(check="orphan_plan", ok=False,
                        message=f"fixture broke: {len(anchors)} anchors",
                        numbers={"anchors": len(anchors)})]
    a = anchors[0]
    if a["declares_a_body"]:
        out.append(Finding(
            check="orphan_plan", ok=False,
            message=("five torches 30 m apart were read as declaring a BODY; "
                     "the discriminator is not looking at the commanded "
                     "positions"),
            numbers={"commands": len(a["commands"])}))
    _m, orphan, _f, scatter, _re = our_bodies([], anchors)
    if orphan:
        out.append(Finding(
            check="orphan_plan", ok=False,
            message=("a road_lighting plan whose own commands are 30 m apart "
                     "was reported as an ORPHAN for want of a body it could "
                     "never have built - this is the 25-of-25 state"),
            numbers={"orphan": len(orphan), "scatter": len(scatter)}))
    if len(scatter) != 1:
        out.append(Finding(
            check="orphan_plan", ok=False,
            message=(f"the scatter must be COUNTED, not dropped: "
                     f"{len(scatter)} reported"),
            numbers={"scatter": len(scatter)}))
    if not out:
        out.append(Finding(
            check="orphan_plan", ok=True,
            message=("a plan whose own commands cannot cluster into a body is "
                     "reported as a scatter and not judged against one"),
            numbers={"orphan": len(orphan), "scatter": len(scatter),
                     "commands": len(a["commands"])}))
    return out


def _case_a_demolished_body_is_still_an_orphan():
    """THE STATE THE SCATTER RULE MUST NOT SWALLOW, and the reason the
    discriminator is the plan's own commands rather than "is anything of mine
    still standing".

    A plan that commanded a real BUILDING - pieces within metres of each other
    - and whose building is GONE is exactly what `orphan_plan` exists to
    refuse.  A presence test ("one of my prefabs stands nearby") would pass a
    5,553-piece castle that was demolished and left one stray pillar; this
    case is that state, shrunk to three pieces, and it MUST still refuse.
    """
    anchors = ledger_anchors(_FakeLedger([_lamp_plan(1.0)]))
    a = anchors[0]
    out = []
    if not a["declares_a_body"]:
        out.append(Finding(
            check="orphan_plan", ok=False,
            message=("three commanded pieces 1 m apart were NOT read as "
                     "declaring a body, so a demolished building would be "
                     "excused as a scatter"),
            numbers={"commands": len(a["commands"])}))
    _m, orphan, _f, scatter, _re = our_bodies([], anchors)
    if not orphan or scatter:
        out.append(Finding(
            check="orphan_plan", ok=False,
            message=(f"a plan that commanded a body and has none standing "
                     f"must REFUSE: orphan {len(orphan)}, scatter "
                     f"{len(scatter)}"),
            numbers={"orphan": len(orphan), "scatter": len(scatter)}))
    if not out:
        out.append(Finding(
            check="orphan_plan", ok=True,
            message=("a plan whose commands cluster into a body is still "
                     "judged against one, and still refused when it is gone"),
            numbers={"orphan": len(orphan), "scatter": len(scatter)}))
    return out


def _retired_plan(n_retired: int, cite: bool = True):
    """A plan that commanded a BODY - five torches 1 m apart - and `n_retired`
    of those five pivots deleted by retire records.  `cite` decides whether
    those records say which plan they undo."""
    plan = _lamp_plan(1.0)
    recs = [plan] + [_retire_rec(float(k), 3090 + k, [2690] if cite else [])
                     for k in range(n_retired)]
    anchors = ledger_anchors(_FakeLedger(recs))
    return anchors, our_bodies([], anchors)


def _case_a_plan_the_ledger_retired_is_not_an_orphan():
    """THE OPERATOR'S OWN ASK, REFUSED FOREVER.

    MEASURED on Ulfsland at save 2a6940a934b5, the only `orphan_plan` left on
    the chain: `spawn_plan` seq 2690 commanded SIX `fb_fortress_portcullis`
    for the Ulfsgard sea gate, and retires seq 3090-3095 deleted all six -
    one record per pivot, each carrying `retires: [2690]`, each stating
    "Removed so a ship can enter the harbour, which is what the operator
    asked for and what the wall does not allow".  Zero remain in 489,182
    ZDOs; the rest of that castle stands where it was planted, walls at 11.98
    and 19.98 m, stairs at 31.99 and 39.99 m, towers at 52.00 and 59.99 m.

    `ledger_anchors` read what was PLANTED and never what the chain PULLED
    OUT, so the section reported "no body of 3+ build pieces stands within
    8 m of it that holds any of the prefabs it declared" - and the only
    action that advisory could ever ask for is to rebuild the gate the
    operator asked to have removed.  The body is neither missing nor moved:
    IT WAS RETIRED, by name, by six records that cite the plan they undo.

    The fixture is that state at five pivots.  ACCEPT, and the plan must land
    in the RETIRED population rather than vanish from the count."""
    anchors, (_m, orphan, _f, scatter, retired) = _retired_plan(5)
    out = []
    if orphan or scatter:
        out.append(Finding(
            check="retired_plan", ok=False,
            message=(f"a plan whose every commanded placement was retired by "
                     f"a record citing it is not an orphan: orphan "
                     f"{len(orphan)}, scatter {len(scatter)}"),
            numbers={"orphan": len(orphan), "scatter": len(scatter)}))
    if len(retired) != 1:
        out.append(Finding(
            check="retired_plan", ok=False,
            message=(f"the retired plan must be COUNTED, not dropped: "
                     f"{len(retired)} reported"),
            numbers={"retired": len(retired)}))
    elif retired[0]["retired_commands"] != len(anchors[0]["commands"]):
        out.append(Finding(
            check="retired_plan", ok=False,
            message=(f"{retired[0]['retired_commands']} of "
                     f"{len(anchors[0]['commands'])} commands matched a "
                     "retire, which is not 'in full'"),
            numbers={"retired_commands": retired[0]["retired_commands"]}))
    if not out:
        out.append(Finding(
            check="retired_plan", ok=True,
            message=("a plan the chain itself retired in full is reported as "
                     "RETIRED with its retire seqs, not refused as an "
                     "orphan"),
            numbers={"retired": len(retired), "orphan": len(orphan),
                     "retire_seqs": retired[0]["retire_seqs"]}))
    return out


def _case_a_partly_retired_plan_is_still_an_orphan():
    """AND THE REFUSAL THE NETTING MUST NOT COST.

    Four of five pivots retired, one still commanded and nothing standing.
    That is a building this run planted, partly removed on purpose and partly
    just gone, and it is exactly what `orphan_plan` exists to refuse.  An
    escape keyed on "this site was retired at all" would swallow a
    5,553-piece castle of which one pillar was retired."""
    _a, (_m, orphan, _f, _s, retired) = _retired_plan(4)
    out = []
    if not orphan or retired:
        out.append(Finding(
            check="orphan_plan", ok=False,
            message=(f"4 of 5 commanded placements retired is NOT a retired "
                     f"plan: orphan {len(orphan)}, retired {len(retired)}"),
            numbers={"orphan": len(orphan), "retired": len(retired)}))
    if not out:
        out.append(Finding(
            check="orphan_plan", ok=True,
            message=("a partly retired plan with no body standing is still "
                     "refused"),
            numbers={"orphan": len(orphan), "retired": len(retired)}))
    return out


def _case_a_retire_that_names_no_plan_excuses_nothing():
    """SILENCE IS NEVER THE ESCAPE.

    The same five pivots, deleted by five retire records that do not say what
    they undo.  `retires` is the discriminator, and a retire with an empty
    one is indistinguishable from a deletion nobody accounted for - which is
    the state `orphan_plan` was written to catch."""
    _a, (_m, orphan, _f, _s, retired) = _retired_plan(5, cite=False)
    out = []
    if not orphan or retired:
        out.append(Finding(
            check="orphan_plan", ok=False,
            message=(f"retires that cite no record excused a plan: orphan "
                     f"{len(orphan)}, retired {len(retired)}"),
            numbers={"orphan": len(orphan), "retired": len(retired)}))
    if not out:
        out.append(Finding(
            check="orphan_plan", ok=True,
            message=("a retire that names no plan cannot clear one"),
            numbers={"orphan": len(orphan), "retired": len(retired)}))
    return out


def _flag(name: str):
    g = globals()
    was = g[name]
    g[name] = True

    def undo():
        g[name] = was
    return undo


def _neuter_the_retire_netting():
    """RESTORE THE DEFECT VERBATIM: read what the ledger PLANTED and never
    what the same chain PULLED OUT, so a structure removed on purpose is an
    orphan forever and the only remedy is to rebuild it."""
    return _flag("_RETIRE_NETTING_OFF")


def _neuter_the_retire_to_the_site():
    """RESTORE THE DEFECT THE ESCAPE MUST NOT BECOME: one retire anywhere in a
    site clears that site's whole plan, so a castle with one retired pillar
    stops being judged."""
    return _flag("_RETIRE_BY_SITE")


def _neuter_the_retire_citation():
    """RESTORE THE DEFECT: let a retire that says nothing about what it undoes
    excuse a plan, which makes any deletion anywhere on the chain an alibi."""
    return _flag("_RETIRE_WITHOUT_CITATION")


class _FakeLedger:
    """A ledger file that exists only in memory, so the anchor reader can be
    tested with no world, no save and no chain."""

    def __init__(self, records: list[dict]):
        self._text = "\n".join(json.dumps(r) for r in records)

    def read_text(self, *_a, **_k) -> str:
        return self._text


def _shelter_body(hole: bool, walls: bool = True):
    """A hut: a 6 x 2 m deck under a two-course gable, inside four walls.

    Six `wood_roof` panels, the z=-1 course rising toward +z and the z=+1
    course rising toward -z, meeting over the middle of the deck.  Every deck
    column has panel material above it.

    `hole=True` drops the middle panel of each course - the portal hall's
    state, where 48 of 96 deck samples were open to the sky.
    `walls=False` takes the walls away, which turns exactly the same open
    columns into a TERRACE and must be accepted."""
    body = [I.mk_solid("wood_floor", (float(x), 10.0, 0.0), I.yaw(0), _FLOOR)
            for x in (-2, 0, 2)]
    # _PANEL rises toward its own local -z, so at yaw 180 it rises toward +z.
    for z, yw in ((-1.0, 180.0), (1.0, 0.0)):
        for x in (-2.0, 0.0, 2.0):
            if hole and x == 0.0:
                continue
            body.append(I.mk_solid("wood_roof", (x, 13.0, z), I.yaw(yw),
                                   _PANEL))
    if walls:
        for x in (-2.0, 0.0, 2.0):
            for z in (-1.2, 1.2):
                body.append(I.mk_solid("woodwall", (x, 10.1, z), I.yaw(0),
                                       _WALL))
        for z in (-1.0, 1.0):
            for x in (-3.2, 3.2):
                body.append(I.mk_solid("woodwall", (x, 10.1, z), I.yaw(90),
                                       _WALL))
    return body


def _deep_roof_body(hole: bool, walls: bool = True):
    """A 6 x 6 m deck under a roof FOUR COURSES DEEP, inside four walls.

    WHY A SECOND, DEEPER FIXTURE EXISTS, and it is the difference between a
    case that proves the check and a case that proves the check on this
    world.  `_shelter_body` is 6 x 2 m with exactly TWO z-courses, so
    dropping x=0 from both removes panel material across the ENTIRE DEPTH of
    the roof: the result is open fore and aft, which is a RIDGE SLOT - a vent
    - and no predicate asking "is there roof on all sides of this" can call
    it a hole, because there genuinely is not.  The state the check exists
    for is `lh-west`: ten open columns with 48 panels ALL ROUND THEM.  A 2 m
    deep gable cannot have panels all round anything, so the old fixture had
    drifted from the defect it was written for.

    Here the courses are at z = -3, -1, +1, +3 over five x stations, so
    dropping x=0 from the two INNER courses leaves a gap with roof on every
    side of it.  That is a leak.
    """
    body = [I.mk_solid("wood_floor", (float(x), 10.0, float(z)), I.yaw(0),
                       _FLOOR)
            for x in (-2, 0, 2) for z in (-2, 0, 2)]
    # _PANEL rises toward its own local -z, so at yaw 180 it rises toward +z.
    for z, yw, inner in ((-3.0, 180.0, False), (-1.0, 180.0, True),
                         (1.0, 0.0, True), (3.0, 0.0, False)):
        for x in (-4.0, -2.0, 0.0, 2.0, 4.0):
            if hole and inner and x == 0.0:
                continue
            body.append(I.mk_solid("wood_roof", (x, 13.0, z), I.yaw(yw),
                                   _PANEL))
    if walls:
        for x in (-2.0, 0.0, 2.0):
            for z in (-3.2, 3.2):
                body.append(I.mk_solid("woodwall", (x, 10.1, z), I.yaw(0),
                                       _WALL))
        for z in (-2.0, 0.0, 2.0):
            for x in (-3.2, 3.2):
                body.append(I.mk_solid("woodwall", (x, 10.1, z), I.yaw(90),
                                       _WALL))
    return body


def _neuter_roof_enclosure():
    """RESTORE THE DEFECT: the shelter verdict back on walls alone.

    `roof_enclosed` forced to always-True IS the check as it stood before the
    four-candidate measurement - walled-and-open carried the verdict - and it
    is the state that charged 106 of a trophy tower's courtyard columns as a
    leaking roof and 20 unanchored generated bodies besides.  Returns its own
    undo, so the harness can put the section back.
    """
    mod = sys.modules[__name__]
    real = mod.roof_enclosed
    mod.roof_enclosed = lambda *a, **k: True

    def undo():
        mod.roof_enclosed = real
    return undo


def _neuter_the_roof_line_test():
    """RESTORE THE DEFECT FROM THE OTHER SIDE: `roof_enclosed` forced to
    always-FALSE, i.e. every walled and uncovered column charged to
    `walled_outside_the_roof_line` and NOTHING ever called a hole.

    THE ACCEPT ARM AND THE REFUSE ARM NEED DIFFERENT NEUTERS AND THIS ONE WAS
    MISSING FOR A WHOLE SESSION.  `_neuter_roof_enclosure` widens the
    predicate (always-True) and is what keeps
    `a_ridge_slot_across_the_whole_roof_depth` from being replaced by a plain
    wall test - but `deck_open_to_the_sky_through_a_hole_in_the_roof` SURVIVES
    it, because widening a predicate can only ever add holes.  Nothing
    protected the refusal, so the one case that encodes `lh-west` was
    unproven: the check could have been narrowed to silence until it refused
    nothing and every registered case would still have passed.

    AND IT IS THE LIVE FAILURE MODE, NOT AN INVENTED ONE.  The predicate
    exists because walled-and-open ALONE over-refused - 106 of a trophy
    tower's courtyard columns and 20 unanchored generated bodies - and the
    obvious remedy for an over-refusal is to keep narrowing the population
    until the complaints stop.  Narrowed one step too far it reaches exactly
    this state, where a 5.9 m oculus over a walled deck reads as a courtyard.
    Returns its own undo.
    """
    mod = sys.modules[__name__]
    real = mod.roof_enclosed
    mod.roof_enclosed = lambda *a, **k: False

    def undo():
        mod.roof_enclosed = real
    return undo


def _neuter_the_cede():
    """RESTORE THE DEFECT: every off-target rectangle sample charged to the
    pad, ceded or not - `pad_promises` returning nothing, which is what
    `check_pad_coverage` effectively had before the ownership walk."""
    mod = sys.modules[__name__]
    real = mod.pad_promises
    mod.pad_promises = lambda pads: []

    def undo():
        mod.pad_promises = real
    return undo


# ---------------------------------------------------------------------------
# THE REGRADE CENSUS - one case per rule somebody violated on 2026-09-21
# ---------------------------------------------------------------------------
# A TRUNK: a non-build solid that GENUINELY STANDS ON THE GROUND, foot at
# the surface.  NOT `Beech1`'s real corpus shape (span 70.021..73.536 over
# ground 48.275) - that one is 21.7 m ALOFT and rule 2 discards it before
# the sign is considered, which is the thing writing this case taught me.
_TREE = [["b", -1.0, 0.0, -1.0, 1.0, 5.0, 1.0]]
_GEOM["probe_tree"] = _TREE                            
                                                       


def _regrade(rect, target, ground, solids, exclude=()):
    return I.regrade_census(rect, target, ground, solids, _GEOM, _cols(),
                            gkey, piece_columns, exclude)


_R5 = [(float(x), float(z)) for x in range(-2, 3) for z in range(-2, 3)]


def _flat(y: float, **over):
    g = {gkey(x, z): y for (x, z) in _R5}
    for k, v in over.items():
        x, z = (float(p) for p in k.split("_"))
        g[gkey(x, z)] = v
    return g


def _case_a_fill_that_buries_a_standing_piece():
    """THE STATE MAIN NAMED AS THE HAZARD.  A pad edge sample 1.5 m low, a
    slab standing ON that low ground, and a fill to the pad datum that puts
    the ground over its top.  ParBuried retired 385 pieces for exactly this
    and a fill puts them back."""
    g = {gkey(x, z): 40.0 for (x, z) in _R5}
    for z in range(-2, 3):
        g[gkey(-2.0, float(z))] = 38.5
    slab = I.mk_solid("stone_floor_2x2", (-2.0, 39.0, 0.0), I.yaw(0), _SLAB)
    r = _regrade(_R5, 40.0, g, [slab])
    if r["verdict"] != "REFUSE":
        return []
    b = r["buries"][0]
    return [Finding(
        check="regrade", ok=False,
        message=(f"the fill buries {b['prefab']} at {b['at']}: span "
                 f"{b['span']}, ground {b['ground']} -> 40.000, "
                 f"{b['over_its_top_m']:+.3f} m over its own top"),
        measured=b["over_its_top_m"], limit=0.0, numbers=r,
        population=r["population"])]


def _case_a_sample_that_does_not_move_cannot_bury():
    """RULE 1, AND IT IS THE ONE THAT WITHDREW A CLEAN TERRACE.

    The rectangle DOES have work to do - its x = +2 column is 1.5 m low and
    gets filled - but the slab stands at x = -2 on ground ALREADY at the
    target, with its top flush at 40.000.  The write rewrites those samples
    to the value they already hold, so the ground under the slab does not
    move and nothing standing on it can be buried by it.

    A census that asks "is any piece in this RECTANGLE below the target"
    condemns it: 40.000 > 40.000 - 0.05 is true, and that is the arithmetic
    that withdrew a clean terrace.  A census that asks about the samples that
    MOVE does not.  The fixture must contain both kinds of sample or the
    neuter has nothing to change - the first version of this case had a
    rectangle where nothing moved at all, and the harness correctly reported
    it as NOT LOAD-BEARING."""
    g = {gkey(x, z): 40.0 for (x, z) in _R5}
    for z in range(-2, 3):
        g[gkey(2.0, float(z))] = 38.5
    slab = I.mk_solid("stone_floor_2x2", (-2.0, 39.5, 0.0), I.yaw(0), _SLAB)
    return _regrade_finding(_regrade(_R5, 40.0, g, [slab]))


def _case_a_roof_is_not_undermined_by_a_cut():
    """RULE 2.  A terrace spec was WITHDRAWN over a wood_roof at foot 36.225
    and a wood_wall_roof at 36.005 standing 1.6-1.9 m above the pad datum,
    carried by the hut's WALLS.  "A cut is refused by any solid whose span
    bottom is above the target" is satisfied by every piece above ground
    level in the world.  A solid the ground is not holding up is not in the
    question."""
    g = {gkey(x, z): 40.0 for (x, z) in _R5}
    roof = I.mk_solid("wood_roof", (0.0, 43.0, 0.0), I.yaw(0), _PANEL)
    return _regrade_finding(_regrade(_R5, 38.0, g, [roof]))


def _case_a_piece_already_under_the_ground_is_not_buried_by_a_fill():
    """RULE 6.  Fresh42's road T6 was refused for 'burying' a stone_stair and
    five walls and floors spanning 34.683..35.683 m under ground at 60.03 m:
    already 24.6 m down, the interior of a generated underground location.
    A fill that raises ground over a piece whose top is ALREADY under it does
    not bury it; that burial is pre-existing.  The fixture raises flat ground
    40.0 -> 40.5 over a slab whose top is 5 m under the present surface."""
    g = {gkey(x, z): 40.0 for (x, z) in _R5}
    deep = I.mk_solid("stone_floor_2x2", (0.0, 34.5, 0.0), I.yaw(0), _SLAB)
    return _regrade_finding(_regrade(_R5, 40.5, g, [deep]))


def _neuter_burial_as_a_state():
    """RESTORE THE DEFECT: burial judged by the state AFTER the write, so a
    piece already under the ground counts as buried by any fill over it."""
    real = I.REGRADE_BURIAL_IS_A_TRANSITION
    I.REGRADE_BURIAL_IS_A_TRANSITION = False

    def undo():
        I.REGRADE_BURIAL_IS_A_TRANSITION = real
    return undo



def _case_a_cut_undermines_what_it_takes_away():
    """AND THE REFUSAL RULE 2 MUST NOT COST.  The same cut under a post whose
    foot IS on the current ground: a cut does not bury, it UNDERMINES, and
    "a cut cannot bury anything so it is safe" is true and insufficient."""
    g = {gkey(x, z): 40.0 for (x, z) in _R5}
    post = I.mk_solid("wood_pole_log_4", (0.0, 40.0, 0.0), I.yaw(0), _POST)
    r = _regrade(_R5, 38.0, g, [post])
    if r["verdict"] != "REFUSE":
        return []
    u = r["undermines"][0]
    return [Finding(
        check="regrade", ok=False,
        message=(f"the cut undermines {u['prefab']} at {u['at']}: foot "
                 f"{u['span'][0]}, ground {u['ground']} -> 38.000, left "
                 f"{u['foot_in_air_m']:.3f} m in the air"),
        measured=u["foot_in_air_m"], limit=SUPPORT_LIMIT_M, numbers=r,
        population=r["population"])]


def _case_the_probe_does_not_answer_with_its_own_subject():
    """RULE 4.  A probe reported "28 of 28 lamps sit inside a build-piece
    span" and the piece WAS the lamp.  The subject is excluded by prefab AND
    full 3D position - the same key the retires use."""
    g = {gkey(x, z): 40.0 for (x, z) in _R5}
    for z in range(-2, 3):
        g[gkey(-2.0, float(z))] = 38.5
    slab = I.mk_solid("stone_floor_2x2", (-2.0, 39.0, 0.0), I.yaw(0), _SLAB)
    return _regrade_finding(_regrade(_R5, 40.0, g, [slab], exclude=[slab]))


def _case_a_tree_is_not_a_building():
    """RULE 5, AND WRITING THE CASE CHANGED WHAT I BELIEVED ABOUT MY OWN
    DEFECT.  My census over every placed solid refused a clean pad because
    `Beech1` - A TREE - would be "undermined by 22.071 m", span
    70.021..73.536 over ground 48.275.  I filed that as a build-pieces
    defect.  Building the fixture shows RULE 2 ALREADY CATCHES IT: a foot at
    70.021 over ground 48.275 is 21.7 m ALOFT, so a ground-bearing test
    discards it before the sign is ever considered, and my first run had no
    ground-bearing test.  Two rules were missing and I had only noticed one.

    So rule 5 is load-bearing only for a non-build solid that GENUINELY
    STANDS ON THE GROUND - which a real tree does - and that is what this
    fixture is: foot AT the surface, so rule 2 admits it and only the
    caller's choice of population excludes it.  `regrade_census` takes
    `solids` already filtered and therefore CANNOT tell a trunk from a post;
    the case pins that as a caller obligation by asserting both directions -
    passed in it REFUSES, left out it is CLEAR."""
    g = {gkey(x, z): 40.0 for (x, z) in _R5}
    trunk = I.mk_solid("probe_tree", (0.0, 40.0, 0.0), I.yaw(0), _TREE)
    if _regrade(_R5, 38.0, g, [trunk])["verdict"] != "REFUSE":
        return [Finding(
            check="regrade", ok=False,
            message=("fixture is wrong: a GROUND-STANDING non-build solid no "
                     "longer refuses, so leaving it out of the population "
                     "records nothing and rule 5 is untested"),
            numbers={"span": _TREE[0][2::3]})]
    return _regrade_finding(_regrade(_R5, 38.0, g, []))


# THE RIBBON SURFACE, and it is the half of this census that never existed.
# The pad rect is ONE datum; a road stamps a DIFFERENT height at every sample.
# `_RIBBON` fills the x = -2 column and cuts everywhere else by 0.1 m, which
# is a write NO single datum can describe -- and the column it fills is the
# one a slab is standing in.
_RIBBON = {(float(x), float(z)): (41.5 if x == -2 else 39.9)
           for x in range(-2, 2) for z in range(-2, 3)}


def _case_a_ribbon_surface_is_not_one_datum():
    """THE GENERALISATION, AND THE DEFECT IS THAT THE QUESTION COULD NOT BE
    ASKED.  MEASURED 2026-09-22: every settlement pad in this world has been
    through `regrade_census` and NOT ONE ROAD WRITE HAS, because the only
    shape `target` took was a float and `ribbon.py` has no datum to pass.
    A gate that can only be asked of half the writes is not a gate.

    The fixture is a write no datum can describe: +1.5 m of FILL in one
    lattice column and -0.1 m of CUT in every other, with a slab standing in
    the filled column, top flush at 40.500.  Per sample the fill goes 1.000 m
    over that top and the census REFUSES; the cut columns are 0.400 m, inside
    the walker's own step limit, and correctly cost nothing.

    ONE CASE ASKS ONE THING.  The rect-wider-than-the-write half of this
    generalisation is `an_unwritten_sample_is_not_an_unmoved_one` next door:
    two assertions in one case mean the neuter can trip either, and a case
    whose discriminator is ambiguous proves neither of them."""
    g = {gkey(x, z): 40.0 for (x, z) in _R5}
    slab = I.mk_solid("stone_floor_2x2", (-2.0, 40.0, 0.0), I.yaw(0), _SLAB)
    r = _regrade(_R5, _RIBBON, g, [slab])
    if r["verdict"] != "REFUSE":
        return []
    b = r["buries"][0]
    return [Finding(
        check="regrade", ok=False,
        message=(f"the ribbon fill buries {b['prefab']} at {b['at']}: span "
                 f"{b['span']}, ground {b['ground']} -> {b['target']}, "
                 f"{b['over_its_top_m']:+.3f} m over its own top "
                 f"({r['target_kind']})"),
        measured=b["over_its_top_m"], limit=0.0, numbers=r,
        population=r["population"])]


def _case_an_unwritten_sample_is_not_an_unmoved_one():
    """ABSENT IS NOT EMPTY, ON THE SURFACE INSTEAD OF ON THE ROW.

    A road's rect is whatever window the caller cut; the SURFACE is only the
    samples the rasteriser actually stamped.  The rect here is 25 samples and
    the surface names 20, so 5 are ground THIS WRITE DOES NOT TOUCH.  They
    must be counted and named, never folded into the population as samples
    that "did not move" -- that is the difference between a census over 20
    written samples and a clean-looking census over 25 where 5 were never
    asked about.  Nothing stands here: the only thing this case measures is
    whether the unwritten samples were declared."""
    g = {gkey(x, z): 40.0 for (x, z) in _R5}
    r = _regrade(_R5, _RIBBON, g, [])
    if r["samples_without_a_target"] == 5:
        return []
    return [Finding(
        check="regrade", ok=False,
        message=(f"the rect carries 5 samples this surface does not write "
                 f"and the census declared {r['samples_without_a_target']}: "
                 f"an unwritten sample folded in as one that did not move is "
                 f"absent read as empty"),
        measured=r["samples_without_a_target"], limit=5, numbers=r,
        population=r["population"])]


def _neuter_the_unwritten_sample():
    """RESTORE THE DEFECT: give every rect sample the surface does not name
    its OWN CURRENT GROUND, so it reads as a sample that did not move and is
    never counted.  That is precisely "absent read as empty" -- the census
    then reports a population of 25 with nothing missing, and a reader cannot
    tell the 5 it never looked at from the 5 it measured and found still."""
    real = I.regrade_census

    def folded(rect, target, ground, solids, geom, columns, gkey_,
               piece_columns_, exclude=()):
        if isinstance(target, dict):
            t = {(float(x), float(z)): float(v) for (x, z), v in target.items()}
            for (x, z) in rect:
                k = (float(x), float(z))
                if k not in t:
                    y = ground.get(gkey_(x, z))
                    if y is not None:
                        t[k] = y
            target = t
        return real(rect, target, ground, solids, geom, columns, gkey_,
                    piece_columns_, exclude)

    I.regrade_census = folded

    def undo():
        I.regrade_census = real
    return undo


def _neuter_the_per_sample_surface():
    """RESTORE THE DEFECT VERBATIM: `target` is a DATUM, so a caller holding
    a per-sample surface has to collapse it to one number before it can ask.
    That collapse is the defect -- it is what a road caller would have had to
    do, and it is why no road caller ever did.

    The mean of this fixture's surface is 40.220 m: ABOVE the slab's ground
    and BELOW its top, so the collapsed question answers CLEAR over a write
    that puts 1.000 m of earth over the piece.  Not a tolerance, not a
    rounding -- a surface averaged into a datum is a different write."""
    real = I.regrade_census

    def flattened(rect, target, *a, **kw):
        if isinstance(target, dict) and target:
            target = sum(target.values()) / len(target)
        return real(rect, target, *a, **kw)

    I.regrade_census = flattened

    def undo():
        I.regrade_census = real
    return undo


def _neuter_the_moving_filter():
    """RESTORE THE DEFECT VERBATIM: the hand-written census, which asked
    "does any solid's footprint overlap this RECTANGLE, and is its span
    bottom on the wrong side of the target".

    Turning the moving tolerance down is NOT enough to reproduce it and the
    harness proved that: with every sample admitted, the per-sample sign test
    still reads the CURRENT ground under the slab, finds it equal to the
    target, and takes neither branch.  Rule 1's protection is delivered
    jointly by the moving filter AND by taking the sign from each sample's
    own ground - so the neuter has to restore the whole shape, which is what
    "restore the defective code verbatim" means."""
    real = I.regrade_census

    def naive(rect, target, ground, solids, geom, columns, gkey_,
              piece_columns_, exclude=()):
        rs = {(float(x), float(z)) for (x, z) in rect}
        bad = []
        for s in solids:
            if not s.verts or s.name not in geom:
                continue
            for (x, z) in piece_columns_(s):
                if (float(x), float(z)) not in rs:
                    continue
                sp = columns.span(s, x, z)
                if sp is None:
                    continue
                lo, hi = sp
                if target > lo and target > hi - I.REGRADE_BEARING_M:
                    bad.append(dict(prefab=s.name, at=[x, z],
                                    span=[round(lo, 3), round(hi, 3)],
                                    ground=round(target, 3),
                                    over_its_top_m=round(target - hi, 3)))
                    break
        return dict(verdict="REFUSE" if bad else "CLEAR",
                    rect_samples=len(rect), moving=len(rect), fills=len(rect),
                    cuts=0, solids_over_a_moving_sample=len(bad),
                    ground_bearing=len(bad), aloft_and_not_in_the_question=0,
                    buries=bad[:8], undermines=[], excluded=0,
                    population="every solid overlapping the RECTANGLE")

    I.regrade_census = naive

    def undo():
        I.regrade_census = real
    return undo


def _neuter_the_ground_bearing_test():
    """RESTORE THE DEFECT: judge every solid over the rectangle, whether the
    ground is holding it up or not.  "A cut is refused by any solid whose
    span bottom is above the target" is satisfied by every piece above
    ground level in the world."""
    real = I.REGRADE_BEARING_M
    I.REGRADE_BEARING_M = 1e6        # everything is "ground-bearing"

    def undo():
        I.REGRADE_BEARING_M = real
    return undo


def _regrade_finding(r: dict):
    if r["verdict"] == "CLEAR":
        return [Finding(check="regrade", ok=True,
                        message=(f"{r['moving']} of {r['rect_samples']} "
                                 f"samples move and nothing ground-bearing "
                                 f"stands over them"),
                        numbers=r, population=r["population"])]
    bad = (r["buries"] or r["undermines"])[0]
    return [Finding(check="regrade", ok=False,
                    message=f"refused over {bad['prefab']} at {bad['at']}",
                    measured=1.0, limit=0.0, numbers=r,
                    population=r["population"])]


def _case_shelter_open():
    """A HOLE IN A ROOF DEEP ENOUGH TO HAVE ONE.  Panels on all four sides of
    the gap, which is what `lh-west`'s 48-panel roof has round its ten open
    columns and what this section is REQUIRED to refuse."""
    return check_shelter([(_deep_roof_body(True), [], "hut")], _cols())


def _case_shelter_closed():
    return check_shelter([(_deep_roof_body(False), [], "hut")], _cols())


def _cracked_roof_body():
    """`_deep_roof_body(False)` with the two INNER courses' x=0 and x=2
    panels slid apart to leave a 3 cm seam at x=1.49..1.52 - the portal
    hub's crack, laid exactly on the deck lattice at x=1.5."""
    body = []
    for s in _deep_roof_body(False):
        inner = s.name == "wood_roof" and abs(s.pos[2]) == 1.0
        dx = {0.0: 0.49, 2.0: 0.52}.get(s.pos[0], 0.0) if inner else 0.0
        body.append(I.mk_solid(s.name, (s.pos[0] + dx, s.pos[1], s.pos[2]),
                               s.quat, _GEOM[s.name]) if dx else s)
    return body


def _case_a_crack_between_two_panels_is_not_a_hole():
    """A LINE CAN LAND IN A SEAM NO DISC FITS THROUGH - see
    `SHELTER_CRACK_M`.  Four deck columns sit on a 3 cm seam between two
    panels of a roof that is otherwise whole."""
    return check_shelter([(_cracked_roof_body(), [], "hut")], _cols())


def _neuter_the_column_is_a_line():
    """THE DEFECT: the shipped line test, so a seam is a hole - with every
    opening a hole as well (`_SHELTER_HOLE_MODE` "any"), because a 3 cm
    seam admits no walker either, and a neuter that left that rule on would
    restore nothing the case can see."""
    undo_line = _flag("_SHELTER_COLUMN_IS_A_LINE")
    undo_any = _neuter_every_opening_admits_the_walker()

    def undo():
        undo_any()
        undo_line()
    return undo


def _neuter_every_opening_admits_the_walker():
    """THE DEFECT: every column of the verdict set is a hole, whatever fits
    in the opening over it (`_SHELTER_HOLE_MODE` "any").  Returns its undo."""
    global _SHELTER_HOLE_MODE
    was = _SHELTER_HOLE_MODE
    _SHELTER_HOLE_MODE = "any"

    def undo():
        global _SHELTER_HOLE_MODE
        _SHELTER_HOLE_MODE = was
    return undo


def _slotted_roof_body():
    """`_deep_roof_body(False)` with the two INNER courses' x=0 and x=2
    panels slid apart to leave a 0.30 m SLOT at x=1.35..1.65 - six times the
    5 cm crack ring, so the seam rule does not clear it, and narrower than
    the walker's 0.98 m capsule: the lighthouse's three slots (0.20 to
    0.50 m2 between darkwood beams and arches at top 19.5), on the lattice."""
    body = []
    for s in _deep_roof_body(False):
        inner = s.name == "wood_roof" and abs(s.pos[2]) == 1.0
        dx = {0.0: 0.35, 2.0: 0.65}.get(s.pos[0], 0.0) if inner else 0.0
        body.append(I.mk_solid(s.name, (s.pos[0] + dx, s.pos[1], s.pos[2]),
                               s.quat, _GEOM[s.name]) if dx else s)
    return body


def _case_a_slot_narrower_than_the_walker_is_not_a_hole():
    """A HOLE MUST ADMIT THE WALKER - see `_SHELTER_HOLE_MODE`."""
    return check_shelter([(_slotted_roof_body(), [], "hut")], _cols())


# A MACHINE UNDER ITS OWN FLUE: `_deep_roof_body(True)`'s open 2 x 4 m
# shaft with a crafting station filling the deck under it, as the
# blastfurnace stands under its chimney at complete-station-hub.  The
# categories are the fixture's own, in `Piece.m_category`'s measured
# values (see `DECK_CATEGORIES`): the floor builds (2), the machine crafts
# (1).
_GEOM["furnace_probe"] = [["b", -1.0, 0.0, -2.0, 1.0, 0.6, 2.0]]
_DECK_FIXTURE_CATEGORIES = {"wood_floor": 2, "woodwall": 2, "wood_roof": 2,
                            "furnace_probe": 1}


def _case_a_machine_under_its_flue_is_not_a_deck():
    """THE TOP OF A CRAFTING STATION IS NOT A FLOOR - see `DECK_CATEGORIES`.
    Every open column of the shaft is over the machine's top."""
    body = _deep_roof_body(True) + [
        I.mk_solid("furnace_probe", (0.0, 10.1, 0.0), I.yaw(0),
                   _GEOM["furnace_probe"])]
    return check_shelter([(body, [], "station")],
                         Columns(_GEOM, _DECK_FIXTURE_CATEGORIES))


def _case_an_open_floor_under_a_hole_is_still_refused():
    """AND A FLOOR STILL IS: the same shaft with nothing in it."""
    return check_shelter([(_deep_roof_body(True), [], "hut")],
                         Columns(_GEOM, _DECK_FIXTURE_CATEGORIES))


def _neuter_every_level_top_is_a_deck():
    """THE DEFECT: a machine's top is a floor."""
    return _flag("_EVERY_LEVEL_TOP_IS_A_DECK")


def _neuter_no_category_is_a_deck():
    """THE OTHER DIRECTION: no category builds a deck, which hides every
    hole.  Returns its undo."""
    global DECK_CATEGORIES
    was = DECK_CATEGORIES
    DECK_CATEGORIES = frozenset()

    def undo():
        global DECK_CATEGORIES
        DECK_CATEGORIES = was
    return undo


def _neuter_a_crack_a_deck_cell_wide():
    """THE OTHER DIRECTION: an opening must admit a disc a whole deck cell
    across, which swallows lh-west's oculus.  Returns its undo."""
    global SHELTER_CRACK_M
    was = SHELTER_CRACK_M
    SHELTER_CRACK_M = 1.0

    def undo():
        global SHELTER_CRACK_M
        SHELTER_CRACK_M = was
    return undo


def _case_shallow_gable_still_closed():
    """The original 6 x 2 m hut with its roof intact must still PASS, so the
    deeper fixture is an addition and not a replacement of the baseline."""
    return check_shelter([(_shelter_body(False), [], "hut")], _cols())


def _case_ridge_slot_across_the_whole_roof_depth():
    """A GAP THAT SPANS THE ROOF'S WHOLE DEPTH IS A VENT, NOT A LEAK.

    This is `_shelter_body(hole=True)` - the 6 x 2 m hut with x=0 dropped
    from BOTH of its two courses - and it must be ACCEPTED.  There is no roof
    fore or aft of the gap because the roof is only two courses deep, so the
    opening is a slot along the ridge rather than a hole in a plane.  Calling
    it a leak is the same error as calling a parapet walk a hole or a keep's
    courtyard a room, and it is the case that stops the roof-enclosure
    predicate being replaced by a plain wall test: with `roof_enclosed`
    removed this arm refuses and the case fails."""
    return check_shelter([(_shelter_body(True), [], "hut")], _cols())


def _case_terrace_is_not_a_hole():
    """THE SAME OPEN COLUMNS, WITH NO WALLS ROUND THEM.

    MEASURED on Ulfsland: wt-peak has 69 of 110 deck columns open to the sky
    and only 4 of them are enclosed - the other 65 are its parapet walk.  A
    check that refused all 69 would be asking for a roof over a balcony, and
    the next builder would learn to switch the gate off."""
    return check_shelter([(_deep_roof_body(True, walls=False), [], "hut")],
                         _cols())


def _case_box_covers_what_the_panel_does_not():
    """WHY A BOX CANNOT BE USED FOR THIS, asserted as a number.

    One `wood_roof` yawed 45 degrees.  Its footprint is a diamond; its
    axis-aligned bounding box is the square around that diamond, 41 % wider,
    and the four corners of that square are box-covered and panel-open.  The
    case asserts BOTH - that the corner IS inside the bounding box and is NOT
    inside the solid - so that the day somebody 'simplifies' the column
    intersection back to a box, this is what stops them.  It is the same
    blindness that let a gable and an X read alike."""
    s = I.mk_solid("wood_roof", (0.0, 13.0, 0.0), I.yaw(45), _PANEL)
    cols = _cols()
    corner = (s.xmax - 0.05, s.zmax - 0.05)
    in_box = (s.xmin <= corner[0] <= s.xmax and s.zmin <= corner[1] <= s.zmax)
    in_solid = cols.span(s, corner[0], corner[1]) is not None
    centre_solid = cols.span(s, 0.0, 0.0) is not None
    if in_box and not in_solid and centre_solid:
        return []
    return [Finding(
        check="shelter", ok=False,
        message=("fixture is wrong: a 45 degree panel's bounding-box corner "
                 f"is no longer outside its solid - in_box={in_box}, "
                 f"in_solid={in_solid}, centre_in_solid={centre_solid}"),
        where=(round(corner[0], 3), round(corner[1], 3)),
        numbers={"in_box": in_box, "in_solid": in_solid,
                 "centre_in_solid": centre_solid,
                 "box_width_m": round(s.xmax - s.xmin, 4)})]


def _skin_body(step: float):
    """Two courses of a gable meeting at the ridge, the second lifted by
    `step`.  At step 0 their high edges coincide and the skin is continuous;
    lift one course and daylight comes through the ridge."""
    a = I.mk_solid("wood_roof", (0.0, 13.0, -1.0), I.yaw(180), _PANEL)
    b = I.mk_solid("wood_roof", (0.0, 13.0 + step, 1.0), I.yaw(0), _PANEL)
    return [a, b]


def _case_skin_step():
    """dockshore/vestvik's remaining outer-skin step, 1.13 m, against a
    `wood_roof` that is 0.126 m thick."""
    return check_skin([(_skin_body(1.13), [], "dock")], _cols(), _GEOM)


def _case_skin_continuous():
    return check_skin([(_skin_body(0.0), [], "dock")], _cols(), _GEOM)


# A RIDGE BOARD IS NOT A HOLE, AND THIS IS THE STATE THAT PROVED IT.
# `stenvik-manor-1`: a `wood_wall_roof_top` standing 1.28 m PROUD of the
# tiles it sits on, so the step off its edge reads as a cliff over a skin
# that carries straight on underneath.
#
# THIS FIXTURE WAS VACUOUS IN THREE INDEPENDENT WAYS AND ITS OWN NEUTER
# FOUND ALL THREE - restoring the max-only height field left the verdict
# UNCHANGED, which is the harness saying the case had stopped describing
# anything:
#   1. the prefab was called `probe_cap`, and `_is_roof` rejects it, so the
#      cap NEVER ENTERED THE HEIGHT FIELD;
#   2. the cap was as wide as the whole roof, so there was no tile-only
#      column beside it and therefore no edge to step off;
#   3. the cap was modelled as a 1.666 m TALL triangular gable, and `thick`
#      is the solid's vertical extent in its own centre column - so the cap
#      inflated the allowance to 1.949 m and swallowed its own 1.65 m step.
# Rebuilt: a 2 x 2 m board 0.24 m thick, standing 1.28 m proud of a SIX
# panel roof, which is the shape the finding describes.  MEASURED on it:
# clean with the layered height field, 8 holes of 0.523 m with the max-only
# field restored.
_CAP = [["b", -1.0, -0.12, -1.0, 1.0, 0.12, 1.0]]
_GEOM["probe_roof_top"] = _CAP


def _capped_roof():
    """A six-panel gable with a ridge board standing 1.28 m proud of it."""
    body = []
    for k in range(3):
        body.append(I.mk_solid("wood_roof", (2.0 * k - 2.0, 13.0, -1.0),
                               I.yaw(180), _PANEL))
        body.append(I.mk_solid("wood_roof", (2.0 * k - 2.0, 13.0, 1.0),
                               I.yaw(0), _PANEL))
    body.append(I.mk_solid("probe_roof_top", (0.0, 14.077 + 1.28 + 0.12,
                                              0.0), I.yaw(0), _CAP))
    return body


def _case_ridge_board_is_not_a_hole():
    """THE 24 BODIES THE MAX-ONLY HEIGHT FIELD REFUSED.

    MEASURED on Ulfsland: 24 of 27 `roof_skin` refusals were the edges of
    gable caps and ridge boards - `stenvik-manor-1` 15 "holes" of
    1.379-1.603 m from five `wood_wall_roof_top`, every tree house 12, both
    vestvik villas 16 - on roofs `check_shelter` reports as fully closed.
    Reducing a column to its highest solid is the silhouette mistake in a
    second costume: it hides the layer UNDER the thing standing on the roof.
    This case is the ACCEPT half and it is permanent."""
    return check_skin([(_capped_roof(), [], "capped")], _cols(), _GEOM)


def _case_lifted_course_is_still_a_hole():
    """AND THE REFUSAL THE SAME FIX MUST NOT LOSE.  The lifted course has
    NOTHING at the lower level in the taller column, so the second-layer
    rule cannot reach it: 1.13 m of daylight stays 1.13 m of daylight.
    Asserted beside the accept case, because a discriminator that only ever
    says yes is not a discriminator."""
    return check_skin([(_skin_body(1.13), [], "dock")], _cols(), _GEOM)


def _neuter_the_lap_allowance_to_the_rise():
    """THE DEFECT: use the panel's RISE as the lap allowance instead of its
    THICKNESS.  `ymax - ymin` of a pitched panel is how far it CLIMBS -
    1.1310 m for a `wood_roof`, against a real thickness of 0.126 m - so a
    hole exactly one course deep passes as "one panel thick", which is the
    size every hole a 180 degree flip or a dropped course makes."""
    g = check_skin.__globals__
    was = g["_SKIN_ALLOW_USES_RISE"]
    g["_SKIN_ALLOW_USES_RISE"] = True

    def undo():
        g["_SKIN_ALLOW_USES_RISE"] = was
    return undo


def _neuter_the_height_field_to_its_maximum():
    """THE DEFECT: reduce each column to its highest solid.

    MEASURED on Ulfsland: 24 of 27 `roof_skin` refusals were the edges of
    gable caps and ridge boards standing ON a skin that carries straight on
    underneath - `stenvik-manor-1` 15 of them, every tree house 12, both
    vestvik villas 16 - on roofs `check_shelter` reports as fully closed.
    """
    g = check_skin.__globals__
    was = g["_SKIN_MAX_ONLY_LAYERS"]
    g["_SKIN_MAX_ONLY_LAYERS"] = True

    def undo():
        g["_SKIN_MAX_ONLY_LAYERS"] = was
    return undo


def _neuter_the_measured_pitch_allowance():
    """TIGHTENS THE DISCRIMINATOR, because a sound roof has no historical
    defect to restore - and it takes away BOTH of the things that keep one
    from being refused, because MEASURED on this fixture neither alone is
    enough and saying so is the point of the field:

      the pitch allowance   a 26 degree `wood_roof` climbs 0.244 m between
        adjacent 0.5 m columns against an allowance of 0.438; with the
        allowance at its 0.155 m thickness alone, every course is a step
      the SPANNED-STEP test  and every one of those steps is then bridged
        by the very panel that made it, so the verdict does not move

    Only with both off does a continuous gable refuse itself, which is the
    honest statement of what this case holds in place.
    """
    real = panel_pitch
    g = check_skin.__globals__
    was = g["_SKIN_NO_SPAN_TEST"]

    def flat(s, geom):
        return 0.0
    g["panel_pitch"] = flat
    g["_SKIN_NO_SPAN_TEST"] = True

    def undo():
        g["panel_pitch"] = real
        g["_SKIN_NO_SPAN_TEST"] = was
    return undo


def _ridge_and(panel_solid):
    """A RIDGE IS A LINE ACROSS THE PITCH, NOT A STACK ALONG IT.

    This fixture used to put its three ridge panels at z = -0.2, 0.0, +0.2 -
    0.2 m apart ALONG THE SLOPE - which is not a ridge, it is three courses
    of one pitch 0.2 m apart.  It read as a ridge only to a check that
    reduced the ridge to "the nearest of these centres" and then skipped
    every panel within 0.75 m of it, i.e. to a check that never looked at
    the ridge at all.  The moment the ridge course was judged like any other
    course, the fixture's own middle panel came out inverted - the fixture
    was wrong, and its wrongness was invisible for exactly as long as the
    blindness it was testing through.  The ridge here now runs along x,
    across the pitch, which is what a ridge is.
    """
    ridge = [I.mk_solid("probe_roof", (float(x), 5.2, 0.0), I.yaw(0), _PANEL)
             for x in (-2.0, 0.0, 2.0)]
    return ridge + [panel_solid]


_RIGHT = I.mk_solid("probe_roof", (0.0, 4.0, -2.0), I.yaw(180), _PANEL)
_FLIPPED = I.mk_solid("probe_roof", (0.0, 4.0, 0.0 - 2.0), I.yaw(0), _PANEL)


def _case_flip_moves_nothing():
    """A 180 degree flip moves the piece NOT AT ALL.  Asserted, because this
    is exactly how 349 inverted panels were certified "349/349 at
    tolerance 0" - the position test was right, and blind."""
    same = ((round(_RIGHT.pos[0], 6), round(_RIGHT.pos[1], 6),
             round(_RIGHT.pos[2], 6))
            == (round(_FLIPPED.pos[0], 6), round(_FLIPPED.pos[1], 6),
                round(_FLIPPED.pos[2], 6)))
    return [] if same else [Finding(
        check="orientation", ok=False,
        message="fixture is wrong: the flip moved the piece",
        numbers={"right": _RIGHT.pos, "flipped": _FLIPPED.pos})]


def _case_flip_keeps_the_box():
    """A 180 degree flip changes the bounding box BY NOTHING A TEST CAN SEE.

    Exactly: the box's EXTENTS are invariant - a rotation by pi about y maps
    the solid onto its own point reflection, so width, height and depth are
    identical to the last bit.  Its CENTRE moves by half the panel's own
    asymmetry about its pivot, which for `wood_roof` is 0.0060 m in z and
    0.0000 m in x and y.  Six millimetres.  That is why "349/349 at
    tolerance 0" was reported over 349 inverted panels: there was nothing in
    the extents to see and six millimetres in the centre, and the panels were
    upside down.  Both numbers are asserted here."""
    def ext(s):
        return [round(v, 6) for v in (s.xmax - s.xmin, s.ymax - s.ymin,
                                      s.zmax - s.zmin)]

    def ctr(s):
        return [(s.xmin + s.xmax) / 2, (s.ymin + s.ymax) / 2,
                (s.zmin + s.zmax) / 2]
    shift = max(abs(a - b) for a, b in zip(ctr(_RIGHT), ctr(_FLIPPED)))
    if ext(_RIGHT) == ext(_FLIPPED) and shift <= 0.0061:
        return []
    return [Finding(
        check="orientation", ok=False,
        message=(f"fixture is wrong: the flip changed the bounding box - "
                 f"extents {ext(_RIGHT)} vs {ext(_FLIPPED)}, centre moved "
                 f"{shift:.4f} m"),
        measured=round(shift, 4), limit=0.0061,
        numbers={"right_extents": ext(_RIGHT),
                 "flipped_extents": ext(_FLIPPED),
                 "centre_shift_m": round(shift, 4)})]


def _ori_finding(solids):
    r = I.check_orientation(solids, {"probe_roof": _PANEL})
    if not r["inverted"] and not r["upside_down"]:
        return []
    return [Finding(check="orientation", ok=False,
                    message=f"{r['inverted']} inverted, "
                            f"{r['upside_down']} upside down",
                    measured=float(r["inverted"]), limit=0.0,
                    numbers={"inverted": r["inverted"]})]


def _case_upslope_panel_passes():
    return _ori_finding(_ridge_and(_RIGHT))


def _case_flipped_panel_fails():
    return _ori_finding(_ridge_and(_FLIPPED))



def _ori_scope() -> tuple[int, int, int]:
    """`(world inverted, ours inverted, bodies claimed)` for one inverted
    roof standing beside an anchor and an identical one 200 m away."""
    mine = _ridge_and(_FLIPPED)
    far = [I.mk_solid(s.name, (s.pos[0] + 200.0, s.pos[1], s.pos[2] + 200.0),
                      s.quat, _PANEL) for s in mine]
    anchors = [dict(site_id="probe", role="town_body", x=0.0, z=-2.0,
                    prefabs={"probe_roof"}, commands=[], seq=1,
                    declares_a_body=True)]
    matched, _o, _f, _s, _r = our_bodies(
        I.bodies(mine + far, slack=1.2), anchors)
    ours = [s for body, _a in matched for s in body]
    w = I.check_orientation(mine + far, {"probe_roof": _PANEL})
    o = I.check_orientation(ours, {"probe_roof": _PANEL})
    return w["inverted"], o["inverted"], len(matched)


def _case_orientation_is_scoped_to_our_own_bodies():
    """A GENERATED RUIN'S INVERTED PANELS ARE NOT THIS BUILD'S DEFECT.

    MEASURED at save 39e4d7b7: `check_orientation` declares its population as
    "every placed piece whose prefab name contains 'roof'" - the WHOLE SAVE,
    8,423 pieces in 712 bodies - while every other check in this section runs
    over `our_bodies`.  Asking this section's own ownership question of all 21
    refused bodies at its own 8 m margin, TWENTY are nowhere near a ledger
    anchor: nearest 65.3 m, then 107.4, 123.5, 132.9, 155.6 ... 3,929.8 and
    4,465.2 m.  They arrive as repeated signatures - five identical 11-panel
    bodies, four 27-panel, seven 42-panel - which is the seed placing one
    location prefab over and over, and a RUIN IS AUTHORED WITH BROKEN PANELS.
    All 10 upside-down panels were in them.  57 inverted in 21 bodies became
    14 in 1 with nothing moving.

    The fixture is two identical inverted roofs, one at a ledger anchor and
    one 200 m away.  The world figure must see BOTH and our figure exactly
    ONE, or the scope is not doing anything.
    """
    w, o, n = _ori_scope()
    if n == 1 and o and w == 2 * o:
        return []
    return [Finding(
        check="orientation", ok=False,
        message=("fixture is wrong: the ownership scope no longer separates "
                 f"a generated body from ours - world inverted {w}, ours "
                 f"{o}, bodies claimed {n}"),
        measured=float(w), limit=float(o),
        numbers={"world_inverted": w, "our_inverted": o, "claimed": n})]


# ---------------------------------------------------------------------------
# TWO GABLED HALLS SHARING AN EAVE - `harbour-city-ulfsgard`, to scale
# ---------------------------------------------------------------------------
# Built here in code from `_PANEL` and nothing else: two gables whose courses
# step out by the prefab's own 2.0 m width and up by its own 1.1310 m rise,
# with the right-hand eave of one hall and the left-hand eave of the other on
# ONE PIVOT.  That single fact - a panel exactly equidistant from two ridges -
# is the whole of the defect, and it needs two halls to exist at all.
def _two_halls(apex_backwards: bool = False) -> list:
    out = []
    for xc in (0.0, 5.5):                       # the shared eave is at x=2.75
        for c, dx in enumerate((2.75, 0.75)):   # eave course, then apex course
            right_way = (not apex_backwards) or c == 0
            out.append(I.mk_solid("probe_roof", (xc - dx, 1.131 * c, 0.0),
                                  I.yaw(270.0 if right_way else 90.0), _PANEL))
            out.append(I.mk_solid("probe_roof", (xc + dx, 1.131 * c, 0.0),
                                  I.yaw(90.0 if right_way else 270.0), _PANEL))
    return out


def _neuter_the_nearest_ridge_tie():
    """THE DEFECT: pick ONE ridge point, the nearest, and take the dot
    product against it.  Restored verbatim from the shipped instrument,
    including its 0.75 m "this piece IS the ridge" skip."""
    real = I.check_orientation

    def shipped(solids, geom):
        roofs = [s for s in solids
                 if ("roof" in s.name.lower() or "_shingle" in s.name.lower())
                 and s.known]
        out = dict(real(solids, geom))
        out["inverted"] = out["upside_down"] = out["checked"] = 0
        out["worst"] = []
        out["inverted_at"] = []
        for body in I.bodies(roofs, slack=1.2):
            if len(body) < 3:
                continue
            top = max(s.ymax for s in body)
            ridge = [((s.xmin + s.xmax) / 2, (s.zmin + s.zmax) / 2)
                     for s in body if top - s.ymax <= 0.5]
            for s in body:
                rv = I.rise_vector(geom.get(s.name) or [], s.quat)
                if rv is None:
                    continue
                (rx, ry, rz), _p = rv
                cx, cz = (s.xmin + s.xmax) / 2, (s.zmin + s.zmax) / 2
                rx0, rz0 = min(ridge, key=lambda p: (p[0] - cx) ** 2
                               + (p[1] - cz) ** 2)
                dx, dz = rx0 - cx, rz0 - cz
                dlen = math.hypot(dx, dz)
                rlen = math.hypot(rx, rz)
                if dlen < 0.75 or rlen < 1e-6:
                    continue
                out["checked"] += 1
                if ry < 0:
                    out["upside_down"] += 1
                if (rx * dx + rz * dz) / (rlen * dlen) < 0:
                    out["inverted"] += 1
                    out["worst"].append((-1.0, s.name, round(s.pos[0], 1),
                                         round(s.pos[1], 2),
                                         round(s.pos[2], 1)))
        return out
    I.check_orientation = shipped

    def undo():
        I.check_orientation = real
    return undo


def _neuter_the_ridge_skip():
    """THE DEFECT, the other half of it: never judge the ridge course.

    The shipped rule skipped any panel whose nearest ridge point was under
    0.75 m away, and the apex course of every gable in the world is its own
    nearest ridge point.  So the course that decides whether a roof has a
    ridge or a gutter was the one course never measured - which is why
    ledger seq 5074 repaired 144 of `harbour-city-ulfsgard`'s 196 panels and
    stopped exactly there.
    """
    real = I.check_orientation

    def blind(solids, geom):
        r = dict(real(solids, geom))
        ridge_keys = set()
        roofs = [s for s in solids
                 if ("roof" in s.name.lower() or "_shingle" in s.name.lower())
                 and s.known]
        for body in I.bodies(roofs, slack=1.2):
            top = max(s.ymax for s in body)
            for s in body:
                if top - s.ymax <= 0.5:
                    ridge_keys.add((s.name, round(s.pos[0], 4),
                                    round(s.pos[1], 4), round(s.pos[2], 4)))
        kept = [row for row in r["inverted_at"]
                if (row["prefab"], round(row["pos"][0], 4),
                    round(row["pos"][1], 4),
                    round(row["pos"][2], 4)) not in ridge_keys]
        r["inverted_at"] = kept
        r["inverted"] = len(kept)
        r["worst"] = [w for w in r["worst"]][:len(kept)]
        r["pass"] = not kept and not r["upside_down"]
        return r
    I.check_orientation = blind

    def undo():
        I.check_orientation = real
    return undo


def _case_two_halls_sharing_an_eave_are_not_inverted():
    """FOURTEEN CORRECT PANELS WERE REFUSED OVER SIX MILLIMETRES.

    MEASURED on Ulfsland save 5385652e at `harbour-city-ulfsgard` (1080.5,
    697.1): two gabled halls share their eave course at x=1080.5, and their
    ridges are 12 m apart at x=1074.5 and x=1086.5.  A panel on that shared
    eave is 6.000 m from one and 6.006 m from the other - the 6 mm is the
    panel's own asymmetry about its pivot - so "the nearest ridge point"
    handed 13 of the 14 the ridge of the OTHER hall and the check called all
    14 inverted.  They are not: each climbs toward its own hall's ridge.

    THE FIXTURE IS THAT ROOF TO SCALE and nothing is borrowed: the courses
    step out by `_PANEL`'s own 2.0 m width, up by its own 1.1310 m rise, and
    the two halls put their facing eaves on ONE pivot exactly as Ulfsgard
    does.  Every panel here rises toward its own ridge and the check must
    say so.
    """
    return _ori_finding(_two_halls())


def _case_an_apex_course_laid_backwards_is_refused():
    """AND THE STATE THE SAME ROOF WAS ACTUALLY IN, WHICH NOTHING COULD SEE.

    `harbour-city-ulfsgard`'s apex course - 52 of its 196 panels, 4 per row
    over 13 rows - is yawed 180 degrees from what `settlements/harbour_city
    .py::shed` emits, so the two top panels of each hall rise AWAY from each
    other and the apex is a 0.969 m GUTTER between two 1.37 m cliffs instead
    of a ridge.  MEASURED consequences on that save: `orientation` 52 at
    margin 0.506 m, and `roof_skin` 104 daylight steps worst 0.950 m.
    Flipping those 52 in place takes BOTH to zero - measured on the same
    census, nothing else touched.

    The apex course is the one course the shipped rule could never judge,
    because a panel at the apex is its own nearest ridge point and anything
    within 0.75 m of the ridge was skipped.  Ledger seq 5074 flipped 144 of
    this roof's 196 panels and stopped exactly at the 52 the check could not
    name: A REPAIR IS ONLY EVER AS WIDE AS THE CHECK THAT NAMES ITS
    POPULATION.
    """
    return _ori_finding(_two_halls(apex_backwards=True))


def _case_a_roof_with_no_ridge_is_unmeasured_not_sound():
    """A SINGLE COURSE HAS NOTHING TO CLIMB TOWARD, AND SAYS SO.

    Three panels at one height, all facing the same way: a lean-to, or one
    course of a hip laid flat.  Every panel is in the ridge set, so the
    ridge is the whole body and there is no direction to be right about.
    Asked anyway, the panels on the OUTSIDE of the group read as inverted -
    MEASURED before this guard existed: 11 phantom inversions on a 30-panel
    `wood_roof_45` body at (481.1, 920.4) that the shipped rule reported as
    zero.  So it is counted as UNMEASURED and reported, never as sound.
    """
    flat = [I.mk_solid("probe_roof", (float(x), 4.0, 0.0), I.yaw(0), _PANEL)
            for x in (-2.0, 0.0, 2.0)]
    r = I.check_orientation(flat, {"probe_roof": _PANEL})
    assert r["no_ridge_bodies"] == 1 and r["no_ridge_panels"] == 3, r
    return _ori_finding(flat)


# ---------------------------------------------------------------------------
# THE RIDGE NOMINATES, THE SEAMS DECIDE - see `instruments.SEAM_PROBE_M`
# ---------------------------------------------------------------------------
# `darkwood_roof_ocorner`, verbatim from blueprints/data/piece_geometry.json:
# a hip's outer corner, seven stepped parts whose high corner is its apex.
_OCORNER = [
    ["p", -1.218, 0.9598, -0.7937, -0.7937, 0.9598, -1.218, -1.1507, 1.2443,
     -0.7264, -0.7264, 1.2443, -1.1507, 1.0329, -0.1053, 1.4572, 1.4572,
     -0.1053, 1.0329, 1.1002, 0.1792, 1.5245, 1.5245, 0.1792, 1.1002, "box"],
    ["p", -0.6176, 0.8601, -0.493, -0.6619, 0.7705, -0.493, -0.6176, 0.8601,
     -0.993, -0.6619, 0.7705, -0.993, 1.0807, 0.0195, -0.493, 1.0364, -0.0701,
     -0.493, 1.0807, 0.0195, -0.993, 1.0364, -0.0701, -0.993, "box"],
    ["p", -0.1294, 0.6185, 0.007, -0.1738, 0.5288, 0.007, -0.1294, 0.6185,
     -0.493, -0.1738, 0.5288, -0.493, 1.0807, 0.0195, 0.007, 1.0364, -0.0701,
     0.007, 1.0807, 0.0195, -0.493, 1.0364, -0.0701, -0.493, "box"],
    ["p", 0.3596, 0.3764, 0.5029, 0.3152, 0.2868, 0.5029, 0.3596, 0.3764,
     0.0069, 0.3152, 0.2868, 0.0069, 1.0807, 0.0195, 0.5029, 1.0364, -0.0701,
     0.5029, 1.0807, 0.0195, 0.0069, 1.0364, -0.0701, 0.0069, "box"],
    ["p", -0.993, 0.8645, -0.6327, -0.993, 0.7749, -0.677, -0.493, 0.8645,
     -0.6327, -0.493, 0.7749, -0.677, -0.993, 0.0185, 1.0766, -0.993, -0.0711,
     1.0323, -0.493, 0.0185, 1.0766, -0.493, -0.0711, 1.0323, "box"],
    ["p", -0.493, 0.6293, -0.1574, -0.493, 0.5397, -0.2018, 0.007, 0.6293,
     -0.1574, 0.007, 0.5397, -0.2018, -0.493, 0.0186, 1.0766, -0.493, -0.0711,
     1.0322, 0.007, 0.0186, 1.0766, 0.007, -0.0711, 1.0322, "box"],
    ["p", 0.0069, 0.3881, 0.3299, 0.0069, 0.2985, 0.2856, 0.5113, 0.3881,
     0.3299, 0.5113, 0.2985, 0.2856, 0.0069, 0.0186, 1.0766, 0.0069, -0.0711,
     1.0322, 0.5113, 0.0186, 1.0766, 0.5113, -0.0711, 1.0322, "box"]]
_HIP_GEOM = {"probe_roof": _PANEL, "probe_roof_ocorner": _OCORNER}
# The top two courses of `salty-dick-portal-hub-final.blueprint`, to 4
# decimals: four caps meeting at one apex, and the course under them - four
# corners and eight `darkwood_roof` (whose collider IS `_PANEL`).  Each
# course is one yaw per side, so the four yaws are named once.
_HIP_Q = ((0.0, 0.773, 0.0, -0.6344), (0.0, 0.9952, 0.0, 0.098),
          (0.0, 0.098, 0.0, -0.9952), (0.0, 0.6344, 0.0, 0.773))
_HIP_ROWS = (
    ("probe_roof_ocorner", (13.037, 15.0, 14.8962), 0),
    ("probe_roof_ocorner", (13.4272, 15.0, 12.9346), 1),
    ("probe_roof_ocorner", (14.9984, 15.0, 15.2864), 2),
    ("probe_roof_ocorner", (15.3886, 15.0, 13.3248), 3),
    ("probe_roof_ocorner", (10.6852, 14.0, 16.4676), 0),
    ("probe_roof", (11.0754, 14.0, 14.506), 0),
    ("probe_roof", (11.4656, 14.0, 12.5445), 0),
    ("probe_roof_ocorner", (11.8558, 14.0, 10.5829), 1),
    ("probe_roof", (12.6467, 14.0, 16.8578), 2),
    ("probe_roof", (13.8173, 14.0, 10.9731), 1),
    ("probe_roof", (14.6082, 14.0, 17.248), 2),
    ("probe_roof", (15.7789, 14.0, 11.3633), 1),
    ("probe_roof_ocorner", (16.5698, 14.0, 17.6381), 2),
    ("probe_roof", (16.96, 14.0, 15.6766), 3),
    ("probe_roof", (17.3502, 14.0, 13.715), 3),
    ("probe_roof_ocorner", (17.7404, 14.0, 11.7534), 3))


def _hip_cap() -> list:
    return [I.mk_solid(n, p, _HIP_Q[q], _HIP_GEOM[n]) for n, p, q in _HIP_ROWS]


def _seam_finding(solids, geom) -> list:
    r = I.check_orientation(solids, geom)
    if not r["inverted"] and not r["upside_down"]:
        return []
    return [Finding(check="orientation", ok=False,
                    message=(f"{r['inverted']} inverted, {r['upside_down']} "
                             f"upside down, {r['seam_cleared']} nominated by "
                             f"the ridge and cleared by their own seams"),
                    measured=float(r["inverted"]), limit=0.0,
                    numbers={"inverted": r["inverted"],
                             "seam_cleared": r["seam_cleared"],
                             "at": [row["pos"] for row in r["inverted_at"]]})]


def _ridge_alone(fn):
    """`fn()` with the instrument as it shipped: the ridge alone decides."""
    was = I._RIDGE_ALONE_DECIDES
    I._RIDGE_ALONE_DECIDES = True
    try:
        return fn()
    finally:
        I._RIDGE_ALONE_DECIDES = was


def _case_a_hip_cap_is_not_judged_against_its_own_centre():
    """FOUR CORRECT CAPS COULD NOT PASS IN ANY POSE.

    MEASURED on `sandbox-portal-hub` (drive 2, build step 5 rc 1): the four
    `darkwood_roof_ocorner` caps ARE the body's ridge set, so each is asked
    about its own box centre - 0.56 m from its low edge, 1.50 m from its
    apex - and read -0.947 m authored AND -0.947 m turned 180.  The emitter
    restored all four as "uncured" and refused the body, stranding every
    portal pair in the hub.  Their seams say what they are: placed, the low
    edge meets the course below at 0.042 m; turned, both edges miss by a
    mean 1.105 m.  This fixture is those two courses to 4 decimals."""
    solids = _hip_cap()
    was = _ridge_alone(lambda: I.check_orientation(solids, _HIP_GEOM))
    assert was["inverted"] == 4 and was["bodies"] == 1, was
    return _seam_finding(solids, _HIP_GEOM)


def _case_a_lower_wing_is_judged_by_its_own_seams():
    """A LOWER WING OF ONE BUILDING IS NOT A SECOND BUILDING.

    `two_neighbouring_houses_are_two_roofs` cures a neighbour's ridge by
    partitioning on the ledger's anchors, and a blueprint's wings share one
    anchor.  MEASURED on `blackforest-walled-base` (`fox-0123456`, ridges
    43.49 to 50.31 m in ONE body): the ridge alone calls 35 correct lower-
    wing panels inverted, the emitter yawed all 35, and roof_skin went from
    3 holes / 12 spanned steps as authored to 5 / 31 as emitted.  This
    fixture is `_two_houses` judged as ONE building - the lower wing's two
    right-hand panels climb to their own ridge and away from the taller
    one, and their seams meet their own courses."""
    solids, _anchors = _two_houses()
    was = _ridge_alone(lambda: I.check_orientation(solids,
                                                   {"probe_roof": _PANEL}))
    assert was["bodies"] == 1 and was["inverted"] == 2, was
    return _seam_finding(solids, {"probe_roof": _PANEL})


def _case_a_reversed_panel_is_not_cleared_by_its_seams():
    """AND A PANEL THAT IS REALLY BACKWARDS STAYS REFUSED.

    The same one building, with the lower wing's LEFT eave laid at the
    opposite yaw: the ridge nominates it, and its own seams read worse
    placed than turned, so nothing clears it."""
    solids, _anchors = _two_houses()
    solids[0] = I.mk_solid("probe_roof", solids[0].pos, I.yaw(90.0), _PANEL)
    return _seam_finding(solids, {"probe_roof": _PANEL})


def _neuter_the_ridge_alone_decides():
    """THE DEFECT: the ridge proxy decides alone, as it shipped."""
    was = I._RIDGE_ALONE_DECIDES
    I._RIDGE_ALONE_DECIDES = True

    def undo():
        I._RIDGE_ALONE_DECIDES = was
    return undo


def _neuter_the_seams_clear_every_nominee():
    """THE OTHER DIRECTION: a seam test so loose it clears whatever the
    ridge nominates - the veto as a relaxation.  Returns its undo."""
    real = I.seam_clears
    I.seam_clears = lambda *a, **k: dict(placed_m=0.0, placed_edges=0,
                                         turned_m=0.0, turned_edges=0,
                                         need_m=0.0)

    def undo():
        I.seam_clears = real
    return undo


# `ashwood_wall_roof_26` and its mirror twin, verbatim from
# blueprints/data/piece_geometry.json.
_ASH26 = [["b", -0.2904, 0.0032, -0.15, 0.9922, 0.6777, 0.15, "box"],
          ["p", -0.8462, -0.313, 0.2, -0.8462, -0.313, -0.2, -1.1585, 0.3135,
           0.2, -1.1585, 0.3135, -0.2, 1.1585, 0.6865, 0.2, 1.1585, 0.6865,
           -0.2, 0.8462, 1.313, 0.2, 0.8462, 1.313, -0.2, "box"]]
_ASH26_UD = [["b", -0.2904, 0.5723, -0.15, 0.9922, 1.2468, 0.15, "box"],
             ["p", -0.8462, 1.563, -0.2, -0.8462, 1.563, 0.2, -1.1585, 0.9365,
              -0.2, -1.1585, 0.9365, 0.2, 1.1585, 0.5635, -0.2, 1.1585,
              0.5635, 0.2, 0.8462, -0.063, -0.2, 0.8462, -0.063, 0.2, "box"]]
_CORBEL_GEOM = {"probe_roof": _PANEL, "ash_wall_roof_26": _ASH26,
                "ash_wall_roof_26_ud": _ASH26_UD}


def _corbels_under_a_gable() -> list:
    """`ashlands-forward-base` rows 44 and 46 as authored - one pillar's two
    corbels, own up at y = -0.0007 - under a correct two-course gable that
    gives the body a ridge."""
    out = [I.mk_solid("ash_wall_roof_26", (-3.264, 2.158, 1.257),
                      (0.205, -0.205, -0.677, 0.677), _ASH26),
           I.mk_solid("ash_wall_roof_26_ud", (-2.22, 4.0, 2.005),
                      (0.205, 0.205, -0.677, -0.677), _ASH26_UD)]
    for c, dx in enumerate((2.75, 0.75)):
        out.append(I.mk_solid("probe_roof", (-2.7 - dx, 5.5 + 1.131 * c, 1.6),
                              I.yaw(270.0), _PANEL))
        out.append(I.mk_solid("probe_roof", (-2.7 + dx, 5.5 + 1.131 * c, 1.6),
                              I.yaw(90.0), _PANEL))
    return out


def _case_a_corbel_on_its_side_is_not_upside_down():
    """A ROOF-CLASS PIECE ROLLED ONTO ITS SIDE HAS NO UP TO BE UPSIDE DOWN
    ABOUT - see `instruments.ON_SIDE_TOL`."""
    solids = _corbels_under_a_gable()
    was = _ridge_alone(lambda: _on_side_judged(
        lambda: I.check_orientation(solids, _CORBEL_GEOM)))
    assert was["upside_down"] == 2, was
    return _seam_finding(solids, _CORBEL_GEOM)


def _case_a_panel_rolled_face_down_is_still_upside_down():
    """AND FACE-DOWN IS STILL UPSIDE DOWN: the ridge fixture's correct panel
    rolled 180 degrees about its own x, own up at y = -1."""
    face_down = I.mk_solid("probe_roof", _RIGHT.pos,
                           I.qmul(_RIGHT.quat, (1.0, 0.0, 0.0, 0.0)), _PANEL)
    return _seam_finding(_ridge_and(face_down), {"probe_roof": _PANEL})


def _on_side_judged(fn):
    was = I._ON_SIDE_JUDGED
    I._ON_SIDE_JUDGED = True
    try:
        return fn()
    finally:
        I._ON_SIDE_JUDGED = was


def _neuter_on_side_judged():
    """THE DEFECT: every sloped roof-class piece judged whatever its roll."""
    was = I._ON_SIDE_JUDGED
    I._ON_SIDE_JUDGED = True

    def undo():
        I._ON_SIDE_JUDGED = was
    return undo


def _neuter_every_roll_on_its_side():
    """THE OTHER DIRECTION: any roll at all exempts a piece, face-down
    included.  Returns its undo."""
    was = I.ON_SIDE_TOL
    I.ON_SIDE_TOL = 1.0

    def undo():
        I.ON_SIDE_TOL = was
    return undo


# ---------------------------------------------------------------------------
# TWO HOUSES OF TWO PLANS, EAVES 0.68 m APART - stenvik-house-2 beside
# stenvik-manor-1 on the Fresh42 drive-2 save, to scale
# ---------------------------------------------------------------------------
# Built from `_PANEL` alone, in `_two_halls`' own geometry (courses 2.0 m
# out, 1.131 m up), but as TWO buildings each placed by its OWN anchor: the
# shorter house A at x=0, the taller house B at x=8.3 and 0.72 m higher,
# which is manor-1's ridge (42.05) over house-2's (41.33).  B's left eave
# box starts 0.68 m past A's right eave box - inside `check_orientation`'s
# 1.2 m body slack - so one geometric body holds both roofs, its ridge band
# is B's apex alone, and A's two right-hand panels, climbing correctly
# toward A's own ridge, point away from B's.
_HOUSE_B_X, _HOUSE_B_RISE = 8.3, 0.72


def _two_houses(a_backwards: bool = False) -> tuple[list, list[dict]]:
    """`(solids, anchors)`.  `a_backwards` lays house A's RIGHT eave panel
    at the opposite yaw: one genuinely inverted panel, climbing away from
    A's own ridge and TOWARD house B's - exactly the panel the union rule
    scores as SOUND, because B's apex is the only ridge that body has."""
    solids: list = []
    anchors: list[dict] = []
    for site, xc, y0 in (("house-a", 0.0, 0.0),
                         ("house-b", _HOUSE_B_X, _HOUSE_B_RISE)):
        mine = []
        for c, dx in enumerate((2.75, 0.75)):
            back = a_backwards and site == "house-a" and c == 0
            mine.append(I.mk_solid("probe_roof", (xc - dx, y0 + 1.131 * c,
                                                  0.0),
                                   I.yaw(270.0), _PANEL))
            mine.append(I.mk_solid("probe_roof", (xc + dx, y0 + 1.131 * c,
                                                  0.0),
                                   I.yaw(270.0 if back else 90.0), _PANEL))
        solids += mine
        anchors.append(dict(site_id=site, role="town_body", x=xc, z=0.0,
                            prefabs={"probe_roof"}, seqs={1},
                            declares_a_body=True,
                            commands=[(s.name, s.pos[0], s.pos[2])
                                      for s in mine]))
    return solids, anchors


def _by_building_finding(solids, anchors) -> list:
    r = orientation_by_building(solids, anchors, {"probe_roof": _PANEL})
    if not r["inverted"] and not r["upside_down"]:
        return []
    return [Finding(check="orientation", ok=False,
                    message=(f"{r['inverted']} inverted, "
                             f"{r['upside_down']} upside down, over "
                             f"{r['checked']} judged in {r['bodies']} "
                             f"bodies"),
                    measured=float(r["inverted"]), limit=0.0,
                    numbers={"inverted": r["inverted"],
                             "checked": r["checked"],
                             "bodies": r["bodies"],
                             "at": [row["pos"] for row in r["inverted_at"]]})]


def _case_two_neighbouring_houses_are_two_roofs():
    """TWELVE CORRECT PANELS WERE REFUSED FOR A NEIGHBOUR'S RIDGE.

    MEASURED on the Fresh42 drive-2 save a521b70356e7: 11 of stenvik-house-2
    's 24 panels judged against stenvik-manor-1's ridge 0.72 m higher, and 1
    of tree-sth-2's against tree-sth-3's; each building alone 0 inverted.
    The refusal's remedy - a 180 degree re-spawn - would have inverted all
    twelve.  Every panel of this fixture climbs toward its own house's ridge
    and the check must say so."""
    solids, anchors = _two_houses()
    union = _ridge_alone(lambda: I.check_orientation(solids,
                                                     {"probe_roof": _PANEL}))
    assert union["bodies"] == 1 and union["inverted"] == 2, union
    return _by_building_finding(solids, anchors)


def _case_a_backwards_panel_beside_a_neighbour_is_still_refused():
    """AND THE PARTITION MUST NOT HIDE A PANEL THAT IS REALLY BACKWARDS.

    House A's right eave laid at the opposite yaw: it now climbs AWAY from
    A's ridge and TOWARD house B.  Judged per building it is inverted; the
    union rule scores it as sound, because B's ridge is the only ridge that
    body has."""
    return _by_building_finding(*_two_houses(a_backwards=True))


def _neuter_orientation_partition(mode: str):
    def neuter():
        global BUILDING_PARTITION
        was = BUILDING_PARTITION
        BUILDING_PARTITION = mode

        def undo():
            global BUILDING_PARTITION
            BUILDING_PARTITION = was
        return undo
    return neuter


def _case_a_gap_between_two_houses_is_not_a_hole_in_either():
    """THE SHELTER HALF OF THE SAME DEFECT.

    `_deep_roof_body(hole=True)` is one roof with a leak.  The SAME pieces,
    split at x=0 between two anchors, are two houses whose roofs and walls
    together surround one open column - tree-west-1/2/3 round their tree on
    the Fresh42 drive-2 save, where the union found 1 "hole" and each house
    alone 0 of 37.  Neither house has a hole in its own roof line."""
    body = _deep_roof_body(True)
    anchors = []
    for site, pred in (("house-w", lambda s: s.pos[0] <= 0.0),
                       ("house-e", lambda s: s.pos[0] > 0.0)):
        anchors.append(dict(site_id=site, role="town_body", seqs={1},
                            prefabs={s.name for s in body},
                            declares_a_body=True,
                            commands=[(s.name, s.pos[0], s.pos[2])
                                      for s in body if pred(s)]))
    rows, _census = split_by_building([(body, anchors, "house-w+house-e")])
    return [f for f in check_shelter(rows, _cols()) if not f.ok]




_neuter_orientation_by_geometric_union = _neuter_orientation_partition(
    "union")
_neuter_orientation_every_piece_alone = _neuter_orientation_partition(
    "piece")


def _neuter_union_and_the_ridge_alone():
    """BOTH CURES OFF.  Since the seams decide, a neighbour's taller ridge
    no longer misjudges `_two_houses` even as one pool - the lower wing IS
    that state - so the partition case is load-bearing only with the ridge
    deciding alone as well.  Each cure has its own case: the partition in
    `a_gap_between_two_houses_is_not_a_hole_in_either`, the seams in
    `a_lower_wing_is_judged_by_its_own_seams`."""
    undo_pool = _neuter_orientation_by_geometric_union()
    undo_ridge = _neuter_the_ridge_alone_decides()

    def undo():
        undo_ridge()
        undo_pool()
    return undo


def _dup_scope(ours_too: bool) -> tuple[dict, dict, int]:
    """`(world, ours, bodies claimed)` for a co-located pair in a body no
    anchor reaches, and optionally one in a body an anchor does reach."""
    mine = [I.mk_solid("wood_floor", (float(x), 10.0, 0.0), I.yaw(0), _FLOOR)
            for x in (0.0, 2.0, 4.0)]
    far = [I.mk_solid("wood_floor", (float(x) + 200.0, 10.0, 200.0),
                      I.yaw(0), _FLOOR) for x in (0.0, 2.0, 4.0)]
    far.append(I.mk_solid("wood_floor", (200.0, 10.0, 200.0), I.yaw(0),
                          _FLOOR))
    if ours_too:
        mine.append(I.mk_solid("wood_floor", (0.0, 10.0, 0.0), I.yaw(0),
                               _FLOOR))
    anchors = [dict(site_id="probe", role="town_body", x=2.0, z=0.0,
                    prefabs={"wood_floor"}, commands=[], seq=1,
                    declares_a_body=True)]
    bods = [b for b in I.bodies(mine + far, slack=0.35)
            if len(b) >= MIN_BODY_PIECES]
    matched, _o, _f, _s, _r = our_bodies(bods, anchors)
    ours = [s for body, _a in matched for s in body]
    return (check_duplicates(mine + far, _GEOM)[1],
            check_duplicates(ours, _GEOM)[1], len(matched))


def _neuter_the_duplicate_scope():
    """THE DEFECT: judge duplicates over EVERY build piece in the save.

    Restored by making `_dup_scope` hand back the world figure as if it were
    ours, which is what `run()` did before - `check_duplicates(pieces, ...)`
    where `pieces` is the whole census while every other check in this
    section runs over `our_bodies()`.
    """
    real = globals()["_dup_scope"]

    def unscoped(ours_too: bool):
        w, _o, n = real(ours_too)
        return w, w, n
    globals()["_dup_scope"] = unscoped

    def undo():
        globals()["_dup_scope"] = real
    return undo


def _neuter_the_scope_to_nothing():
    """THE OTHER SIGN OF THE SAME DEFECT: a population narrowed until there
    is nothing left in it.

    `duplicates_are_scoped_to_our_own_bodies` holds one end - do not judge
    this build for somebody else's village.  This holds the other, and it is
    the end that ships quietly: THE BETTER THE REPAIR, THE SMALLER THE
    POPULATION, and a scope that reaches zero reports a clean world.
    MEASURED tonight one file over: a pad whose every sample had been
    re-levelled correctly by its own earlier record owned none of them and
    was refused as `population 0` while dead flat.  Here the same shape
    would read as a PASS.
    """
    real = globals()["_dup_scope"]

    def empty(ours_too: bool):
        w, _o, n = real(ours_too)
        return w, dict(same_solid=0, buried_copy=0,
                       shared_pivot_not_a_duplicate=0, unmeasured=0,
                       sites=0, in_ground_generated_more_than_once=0), n
    globals()["_dup_scope"] = empty

    def undo():
        globals()["_dup_scope"] = real
    return undo


def _neuter_the_no_ridge_guard():
    """THE DEFECT: ask a body with no ridge which way its panels climb.

    Every panel of a lean-to, or of one hip course laid flat, is in the
    ridge set, so the ridge is the whole body and there is no direction to
    be right about.  Asked anyway, the panels on the OUTSIDE of the group
    come out inverted - MEASURED, 11 of them on the 30-panel
    `wood_roof_45` body at (481.1, 920.4) whose every panel tops out at
    53.052.
    """
    real = I.check_orientation

    def unguarded(solids, geom):
        r = real(solids, geom)
        if not r["no_ridge_bodies"]:
            return r
        # re-run the same rule with the guard off, by lifting one panel out
        # of the ridge band so the body is no longer all-ridge
        lifted = []
        seen = False
        for s in solids:
            if not seen and s.known:
                lifted.append(I.mk_solid(s.name,
                                         (s.pos[0], s.pos[1] + 2.0, s.pos[2]),
                                         s.quat, geom.get(s.name) or []))
                seen = True
            else:
                lifted.append(s)
        return real(lifted, geom)
    I.check_orientation = unguarded

    def undo():
        I.check_orientation = real
    return undo


def _neuter_orientation_to_a_position_census():
    """THE DEFECT, verbatim: "349/349 at tolerance 0".

    A 180 degree yaw moves neither the pivot, nor the footprint, nor the
    extents of the AABB.  So a check that compares placed positions against
    planned ones at tolerance 0 passes every inverted panel in the world,
    which is what was reported over 385 of them in 74 bodies.  Restored here
    as exactly that: count and position, nothing else.
    """
    real = I.check_orientation

    def census(solids, geom):
        r = dict(real(solids, geom))
        r["inverted"] = 0
        r["upside_down"] = 0
        r["inverted_at"] = []
        r["inverted_bodies"] = []
        r["bodies_with_inversions"] = 0
        r["worst"] = []
        r["pass"] = True
        return r
    I.check_orientation = census

    def undo():
        I.check_orientation = real
    return undo


def _case_duplicates_are_scoped_to_our_own_bodies():
    """A GENERATED VILLAGE'S CO-LOCATED PIECES ARE NOT THIS BUILD'S DEFECT,
    AND THE ONLY REMEDY A DUPLICATE FINDING HAS IS A DELETE.

    MEASURED on Ulfsland save 5385652e, all 7 refused sites and all 46
    co-located pieces, against this run's own declarations:

      nearest position this run EVER declared that prefab   246.8 m to
        6,075.5 m;  nearest declared build placement of ANY prefab  86.9 m
        to 433.7 m
      (2050, 700) and (-250, 4950) are the SAME 437-piece generated location
        stamped twice, 2,345 m apart, and EACH carries one co-located
        `wood_beam` pair at the same local offset - (-1.366, 3.312, -5.317)
        and (-1.366, 3.312, -5.315), agreeing to 2 mm.  The seed authored
        that pair.
      over `our_bodies`, the same instrument on the same save: ZERO
        same-solid, ZERO buried copies, 221 shared pivots.

    The fixture is a co-located pair in a body 200 m from any anchor.  The
    world figure must see it and ours must not.
    """
    w, o, n = _dup_scope(False)
    assert n == 1, n
    assert w["same_solid"] == 1, w
    if not o["same_solid"] and not o["buried_copy"]:
        return []
    return [Finding(
        check="duplicates", ok=False,
        message=("this build was refused for co-located pieces in a body no "
                 f"ledger anchor reaches - ours {o}, world {w}"),
        measured=float(o["same_solid"]), limit=0.0,
        numbers={"ours": o, "world": w})]


def _case_a_duplicate_in_a_body_we_built_is_still_refused():
    """AND THE REFUSAL THE SCOPE MUST NOT LOSE.

    The same fixture with the pair inside the anchored body as well.  A
    population narrowed until nothing is left is the other half of this
    defect class, and it is the half that ships quietly.
    """
    w, o, _n = _dup_scope(True)
    assert w["same_solid"] == 2, w
    return [Finding(
        check="duplicates", ok=not o["same_solid"],
        message=(f"{o['same_solid']} co-located pieces in a body this run "
                 "built"),
        measured=float(o["same_solid"]), limit=0.0,
        numbers={"ours": o, "world": w})]


def _dup_pieces(same_place: bool, same_pose: bool = True):
    """A course of three floors, plus one more piece.

    `same_place=True` puts the extra piece exactly on top of the first - the
    Ulfsgard boathouse state.  `same_place=False` puts it 1.0 m along, which
    is 20x the 0.05 m cube and lands on no other piece's pivot: a tight
    course of abutting floors must NOT read as duplicated."""
    out = [I.mk_solid("wood_floor", (float(x), 10.0, 0.0), I.yaw(0), _FLOOR)
           for x in (0.0, 2.0, 4.0)]
    src = out[0]
    dx = 0.0 if same_place else 1.0
    out.append(I.mk_solid("wood_floor", (src.pos[0] + dx, src.pos[1],
                                         src.pos[2]),
                          I.yaw(0 if same_pose else 90), _FLOOR))
    return out


def _case_duplicate_pair():
    """The Ulfsgard boathouse: 52 pieces emitted twice, invisible to a count
    and to a cylinder."""
    return check_duplicates(_dup_pieces(True), _GEOM)[0]


def _case_duplicate_pair_rotated():
    """THE SAME PREFAB ON THE SAME PIVOT AT A DIFFERENT ROTATION, AND BURIED
    INSIDE IT.  A `wood_floor` is a 2 x 2 m square slab, so a copy yawed 90
    degrees fills EXACTLY the same space while carrying a different
    quaternion and a different vertex list.  A grade decided on the
    quaternion would call the two "differently posed" and let the copy
    through; the occupancy test reads buried_fraction 1.00 and refuses it.

    This case is why the discriminator cannot be the pose.  Its sibling
    `_case_valley_pair_is_not_a_duplicate` is why it cannot be the pivot."""
    return check_duplicates(_dup_pieces(True, same_pose=False), _GEOM)[0]


def _case_valley_pair_is_not_a_duplicate():
    """TWO ROOFS MEETING IN A VALLEY SHARE A PIVOT AND ARE NOT A DUPLICATE.

    The Ulfsgard boathouse is two gables that meet at x=1080.5: each one's
    last eave panel lands on that pivot, one yawed 90 and one 270, and they
    cross in section.  MEASURED there: each carries 2.047 m2 of top surface
    no other piece carries, and buried_fraction is 0.06 / 0.08 - so
    deleting either opens a 1.88 x 2 m hole in one of the two roof planes,
    13 rows over, 48.9 m2 of roof.

    This is the refusal that cost the most to get wrong: it was reported as
    "13 of the boathouse's 52 are wood_roof emitted twice at two yaws", it
    was the stated reason 26 inverted panels were "NOT FLIPPABLE", and
    acting on it would have demolished half a roof on a building nobody
    asked to be touched.  The panels ARE inverted - that is the orientation
    check's finding and it stands - but they are not copies."""
    a = I.mk_solid("wood_roof", (0.0, 10.0, 0.0), I.yaw(90), _PANEL)
    b = I.mk_solid("wood_roof", (0.0, 10.0, 0.0), I.yaw(270), _PANEL)
    fs, n = check_duplicates([a, b], _GEOM)
    if n["shared_pivot_not_a_duplicate"] != 1:
        return [Finding(
            check="duplicates", ok=False,
            message=(f"fixture is wrong: the valley pair graded "
                     f"{n} instead of one shared pivot"), numbers=n)]
    return [f for f in fs if not f.ok]


def _case_distinct_pieces():
    return check_duplicates(_dup_pieces(False), _GEOM)[0]


def _case_pile_is_not_a_duplicate():
    """THE CYLINDER'S MISTAKE.  `objects_count` has NO HEIGHT BOUND, so a
    legitimate stack of floors one above the other reads as a pile of
    duplicates.  The cube must accept it."""
    stack = [I.mk_solid("wood_floor", (0.0, 10.0 + 0.5 * k, 0.0), I.yaw(0),
                        _FLOOR) for k in range(8)]
    cyl = len(stack) - 1          # what a height-blind XZ count would report
    fs = check_duplicates(stack, _GEOM)[0]
    if cyl < 1:
        return [Finding(check="duplicates", ok=False,
                        message="fixture is wrong: the stack is not a stack",
                        numbers={"stack": len(stack)})]
    return fs


def _case_column_is_not_a_box():
    """The primitive every check above stands on.  A panel rotated 45 degrees
    about x has a bounding box 1.4 m tall at its centre column while its own
    solid there is 0.18 m tall.  If this ever stops being true the column has
    been replaced by a box and four checks have gone blind."""
    h = math.radians(45) / 2.0
    q = (math.sin(h), 0.0, 0.0, math.cos(h))
    s = I.mk_solid("wood_roof", (0.0, 10.0, 0.0), q, _PANEL)
    sp = _cols().span(s, 0.0, 0.0)
    if sp is None:
        return [Finding(check="column", ok=False,
                        message="the column missed a piece centred on it",
                        numbers={"x": 0.0, "z": 0.0})]
    box_h = s.ymax - s.ymin
    solid_h = sp[1] - sp[0]
    if box_h - solid_h < 0.5:
        return [Finding(
            check="column", ok=False,
            message=(f"the column no longer distinguishes a rotated solid "
                     f"from its bounding box: box {box_h:.3f} m, solid "
                     f"{solid_h:.3f} m"),
            measured=round(box_h - solid_h, 3), limit=0.5,
            numbers={"box_h": round(box_h, 3), "solid_h": round(solid_h, 3)})]
    return []


def _case_column_misses_outside():
    s = I.mk_solid("wood_roof", (0.0, 10.0, 0.0), I.yaw(0), _PANEL)
    if _cols().span(s, 5.0, 5.0) is not None:
        return [Finding(check="column", ok=False,
                        message="the column hit a piece 7 m away",
                        where=(5.0, 5.0), numbers={"dx": 5.0})]
    return []


# ---------------------------------------------------------------------------
# THE TERMINATION ARM, IN BOTH DIRECTIONS
# ---------------------------------------------------------------------------
# Three fixture refusals, one per remedy family that can carry a class, built
# here in code with no world: a BURIED piece (a terrain cut), a SHELTER hole
# (no tool in this tree performs it) and a DUPLICATE (a retire).  Each case
# below restores one defect and REQUIRES a named row to move AND a named row
# to STAND STILL - the survivor, because a neuter that simply switched the
# arm off would otherwise be indistinguishable from one that lost a
# dependency.  Every survivor is a VERDICT here, never an assertion:
# `registry.run_cases` reads an AssertionError as a flip, so an
# assert-shaped survivor lets a degenerate neuter be stamped PROVEN.
_TERM_OWNER = [(40.0, 40.0, 0.0, "bridge_body_place", 5421)]


def _term_findings() -> list:
    """One refusal per remedy family, at a known place with a known depth."""
    return [
        Finding(check="buried", ok=False,
                message="fixture: a piece 0.380 m under the applied surface",
                where=(40.0, 33.0, 40.0), measured=0.380, limit=BURIED_TOL_M,
                numbers={"buried_pieces": 1, "pieces": 319,
                         "worst_m": 0.380}),
        Finding(check="shelter", ok=False,
                message="fixture: an enclosed deck open to the sky",
                where=(40.0, 40.0), measured=6.0, limit=0.0,
                numbers={"open": 6, "deck": 169}),
        Finding(check="duplicates", ok=False,
                message="fixture: a piece whose solid is already filled",
                where=(40.0, 40.0), measured=1.0, limit=0.0,
                numbers={"co_located_pieces": 2, "same_solid": 1}),
    ]


def _term_writes():
    """A REAL, EMPTY write index: the question WAS asked and nobody holds the
    ground.  `None` here would mean the footprints could not be read, which
    is a different fixture and a different class - see the unread case."""
    from .harbours import WriteIndex                       # noqa: PLC0415
    return WriteIndex([], margin_max=8.0)


class _T6Write:
    """The `WriteIndex.at` surface: T6's own road write holds the ground."""

    def at(self, _x, _z, margin=0.0, exact_only=False):
        return "terrain_write road_T6_stathub_northcape (seq 1951)"


def _case_our_lamp_under_our_road_is_ours_to_reconcile() -> list:
    """BOTH CLAIMS OURS IS OURS TO RECONCILE.  MEASURED on drive 3, save
    05eb7c6c05de: `buried` T6-stathub-northcape lone piece_groundtorch, its
    own top 1.168 m under the applied surface - the torch placed by our
    lamps (spawn_plan seq 2716, step 7) and the ground by T6's own road
    write (seq 1951, step 4) - classed 1B_CONTESTED, an operator decision
    between two strangers.  The LATER claim built against the earlier one's
    standing work, so step 7 roadside owns the repair."""
    from .. import termination as T                        # noqa: PLC0415
    torch = dict(prefab="piece_groundtorch", x=-72.3310, y=39.7070,
                 z=700.7500)
    row = Finding(check="buried", ok=False,
                  message="T6-stathub-northcape: lone piece_groundtorch",
                  where=(-72.3, 39.71, 700.8), measured=1.168,
                  limit=BURIED_TOL_M,
                  numbers={"buried_pieces": 1, "pieces": 1, "worst_m": 1.168,
                           "claimant_pieces": [torch]})
    chain = T.Placements([
        dict(seq=1951, op="terrain_write", actor="fixture",
             params=dict(role="road_segment",
                         name="road_T6_stathub_northcape"),
             meta=dict(build_step="4", build_unit="T6-stathub-northcape")),
        dict(seq=2716, op="spawn_plan", actor="fixture",
             params=dict(role="road_lighting",
                         site_id="T6-stathub-northcape", command_count=1),
             meta=dict(build_step="7", build_unit="T6-stathub-northcape"),
             wire=[f"spawn_object piece_groundtorch pos={torch['z']},"
                   f"{torch['x']},{torch['y']} rot=0,0,0"])],
        "fixture: T6's road and its lamps")
    check_termination([row], [], _T6Write(), placements=chain)
    n = row.numbers
    steps = " ".join(n.get("owner_step") or ())
    return [Finding(
        check="structures/termination",
        ok=n.get("termination_class") == "ACTIONABLE"
        and n.get("termination_reclassed_from") == "1B_CONTESTED"
        and "roadside" in steps,
        message=(f"our torch under our own road's write classed "
                 f"{n.get('termination_class')} (from "
                 f"{n.get('termination_reclassed_from')}), owner "
                 f"{n.get('owner_step')}"),
        where=(-72.3, 700.8), numbers={"cls": n.get("termination_class")})]


def _neuter_blocker_ownership_blind():
    """`termination.neuter_blocker_ownership("blind")`, imported where it
    is used, as this section imports `termination` everywhere."""
    from .. import termination as T                        # noqa: PLC0415
    return T.neuter_blocker_ownership("blind")()


def _term_classes(owners=(), writes=False) -> dict:
    fs = _term_findings()
    check_termination(fs, list(owners) if owners is not None else None,
                      _term_writes() if writes is False else writes)
    return {f.check: (f.numbers or {}).get("termination_class") for f in fs}


def _term_case(want: dict, neuter, must_move: str, must_stand: str,
               why: str) -> list:
    """`want` is what each fixture row must classify as with the defect OFF.
    With it ON, `must_move` must change class and `must_stand` must not."""
    real = _term_classes([])
    bad = {k: (real.get(k), v) for k, v in want.items() if real.get(k) != v}
    if bad:
        return [Finding(
            check="termination", ok=False,
            message=(f"the termination arm classified {sorted(bad)} as "
                     + ", ".join(f"{k} {g!r} not {w!r}"
                                 for k, (g, w) in sorted(bad.items()))),
            numbers={"got": real, "want": want})]
    undo = neuter()
    try:
        got = _term_classes([])
    finally:
        undo()
    moved = real.get(must_move) != got.get(must_move)
    stood = real.get(must_stand) == got.get(must_stand)
    if moved and stood:
        return []
    return [Finding(
        check="termination", ok=False,
        message=(("THE NEUTER DID NOT FLIP" if not moved else
                  "THE NEUTER EMPTIED THE ARM")
                 + f": {why}.  {must_move} went "
                 f"{real.get(must_move)!r} -> {got.get(must_move)!r} and "
                 f"{must_stand} went {real.get(must_stand)!r} -> "
                 f"{got.get(must_stand)!r}"),
        numbers={"real": real, "neutered": got, "must_move": must_move,
                 "must_stand": must_stand})]




def _term_census(owners=()) -> tuple[dict, dict]:
    """`(class per fixture row, the census row's own numbers)`."""
    fs = _term_findings()
    out = check_termination(fs, list(owners), _term_writes())
    return ({f.check: (f.numbers or {}).get("termination_class") for f in fs},
            dict(out[0].numbers))


def _case_every_refusal_carries_a_class():
    """THE STATE THIS SECTION SHIPPED IN, AND IT WAS THE LAST ONE ON THE
    BOARD.  MEASURED on Ulfsland save 7632b3a5: 105 refusals across eleven
    sections, and the ONE row carrying no class at all was
    `structures/buried`.  A section that publishes no class is not a zero in
    the board's sum, it is a hole in it.

    THE SURVIVOR IS THE DENOMINATOR, NOT ANOTHER CLASS, and the first form of
    this case got that wrong and its own harness said so: the neuter blanks
    EVERY class, so no row can stand still in that field and "the survivor
    survived" was unsatisfiable.  What must survive is the census's own
    `refusals` count - the arm goes on SEEING all three refusals and merely
    stops classifying them.  An arm that had instead stopped producing
    refusals would blank the classes too, and only the denominator tells the
    two apart."""
    from .settlements import TERMINATION_CLASSES          # noqa: PLC0415
    real, census = _term_census()
    bad = [k for k, v in real.items() if v not in TERMINATION_CLASSES]
    if bad or census["refusals"] != len(real) \
            or "UNCLASSIFIED" in census["by_class"]:
        return [Finding(
            check="termination", ok=False,
            message=(f"{len(bad)} of {len(real)} refusal(s) carry no class "
                     f"in the shared vocabulary: {real}; census {census}"),
            numbers={"got": real, "census": census})]
    undo = _neuter_the_termination_class_away()
    try:
        neu, ncensus = _term_census()
    finally:
        undo()
    if any(v is not None for v in neu.values()):
        return [Finding(
            check="termination", ok=False,
            message=("THE NEUTER DID NOT FLIP: with the class switched off "
                     f"the rows still published {neu}"),
            numbers={"real": real, "neutered": neu})]
    if ncensus["refusals"] != census["refusals"] \
            or ncensus["by_class"].get("NO_CLASS") != census["refusals"]:
        return [Finding(
            check="termination", ok=False,
            message=("THE NEUTER EMPTIED THE ARM: the census went from "
                     f"{census['refusals']} refusal(s) {census['by_class']} "
                     f"to {ncensus['refusals']} {ncensus['by_class']}, so "
                     "the classes vanishing proves nothing about the class "
                     "field - the arm stopped seeing the refusals"),
            numbers={"census": census, "neutered": ncensus})]
    return []


def _case_an_unread_owner_set_is_out_of_order_not_actionable():
    """UNREAD IS NOT CLEAR, and this is the direction that manufactures work.
    `ground_owned_by` is None both when nobody owns the ground and when the
    owner set could not be read; an arm that does not separate them reports
    ACTIONABLE precisely where the gate cannot see.  AND UNREAD IS NOT
    UNKNOWN: the readings are tools in this tree, so the cut row is
    1E_OUT_OF_ORDER and NAMES both reads it waits on.

    THE SURVIVOR IS `shelter`: its class does not depend on the owner set at
    all - no tool performs its remedy whoever owns the ground - so it must
    stand still while `buried` moves.  Without it, an arm that answered
    1E to everything would pass this case."""
    fs = _term_findings()
    check_termination(fs, None, None)
    got = {f.check: (f.numbers or {}).get("termination_class") for f in fs}
    pre = str((fs[0].numbers or {}).get("prerequisite") or "")
    if got.get("buried") != "1E_OUT_OF_ORDER" \
            or "flatten=FORBIDDEN" not in pre or "terrain_write" not in pre \
            or got.get("shelter") != "1C_UNSERVED":
        return [Finding(
            check="termination", ok=False,
            message=(f"with BOTH instruments unread the cut row came back "
                     f"{got.get('buried')!r} naming {pre!r} and the no-tool "
                     f"row {got.get('shelter')!r}; a cut row must be "
                     f"1E_OUT_OF_ORDER naming both reads and a no-tool row "
                     f"must not depend on an owner set it never needed"),
            numbers={"got": got, "prerequisite": pre})]
    undo = _neuter_the_unread_instrument_to_clear()
    try:
        fs2 = _term_findings()
        check_termination(fs2, None, None)
        neu = {f.check: (f.numbers or {}).get("termination_class")
               for f in fs2}
    finally:
        undo()
    if neu.get("buried") == got.get("buried"):
        return [Finding(
            check="termination", ok=False,
            message=("THE NEUTER DID NOT FLIP: reading an unanswered "
                     f"instrument as CLEAR left the cut row at "
                     f"{neu.get('buried')!r}, so the separation is not "
                     "load-bearing"),
            numbers={"real": got, "neutered": neu})]
    if neu.get("shelter") != "1C_UNSERVED":
        return [Finding(
            check="termination", ok=False,
            message=("THE NEUTER EMPTIED THE ARM: the no-tool row moved to "
                     f"{neu.get('shelter')!r} under a neuter that only "
                     "touches unanswered instruments, so the flip proves "
                     "nothing about the separation"),
            numbers={"real": got, "neutered": neu})]
    return []


def _case_a_refusal_with_no_terrain_remedy_is_unserved_not_unknown():
    """A REFUSAL WITH NO PROPOSED REPAIR HAS A FOOTPRINT OF 0.0, NOT OF
    NOTHING.  0.0 says the question was asked and a repair would need no
    ground; None says nobody looked.  MEASURED by another section one branch
    over: passing None on its no-remedy rows turned SIX correct 1C_UNSERVED
    rows into UNKNOWN.

    THE SURVIVOR IS `buried`, whose footprint is a real measured reach and is
    untouched by the defect - so a neuter that had simply blanked every
    footprint would be caught here rather than counted as a proof."""
    return _term_case(
        {"buried": "ACTIONABLE", "shelter": "1C_UNSERVED",
         "duplicates": "ACTIONABLE"},
        _neuter_the_no_remedy_footprint_to_nothing, "shelter", "buried",
        "handing a row with no terrain remedy a None footprint")


def _case_ground_a_forbidden_site_owns_is_not_actionable():
    """AND THE REFUSAL THE CLASS MUST NOT LOSE.  The same buried piece, with
    a `flatten=FORBIDDEN` record's own ground under it: `writer`'s flatten
    guard will refuse the cut, so this is an OPERATOR decision and not work.
    1A must NAME the record, which is what separates it from "no repair
    exists"."""
    fs = _term_findings()
    check_termination(fs, _TERM_OWNER, _term_writes())
    n, shelter = fs[0].numbers, fs[1].numbers
    if n.get("termination_class") != "1A_FORBIDDEN":
        return [Finding(
            check="termination", ok=False,
            message=(f"a buried piece standing on ground "
                     f"{_TERM_OWNER[0][3]} seq {_TERM_OWNER[0][4]} owns came "
                     f"back {n.get('termination_class')!r}, not "
                     f"1A_FORBIDDEN"),
            numbers=dict(n))]
    if not n.get("ground_owned_by"):
        return [Finding(
            check="termination", ok=False,
            message="1A with no owner named is not 1A",
            numbers=dict(n))]
    # AND THE SURVIVOR, AS A VERDICT.  `shelter`'s class comes off the tool
    # inventory and never off the owner set, so it must stand still when the
    # ownership question is taken away - which is what tells "the query was
    # lost" apart from "the arm stopped classifying".
    if shelter.get("termination_class") != "1C_UNSERVED":
        return [Finding(
            check="termination", ok=False,
            message=("the no-tool row's class moved with the owner set it "
                     f"never needed: {shelter.get('termination_class')!r}"),
            numbers=dict(shelter))]
    return []


def _neuter_the_termination_class_away():
    """RESTORE THE DEFECT VERBATIM: publish no class, and let the board read
    the row as NO_CLASS."""
    return _flag("_TERMINATION_OFF")


def _neuter_the_unread_instrument_to_clear():
    """RESTORE THE DEFECT THE ARM MUST NOT BECOME: an instrument that did not
    answer, read as one that answered CLEAR."""
    return _flag("_TERMINATION_UNREAD_IS_CLEAR")


def _neuter_the_no_remedy_footprint_to_nothing():
    """RESTORE THE DEFECT: `None` where 0.0 belongs, which reads as "nobody
    looked" and turns 1C_UNSERVED into UNKNOWN."""
    return _flag("_TERMINATION_NO_REMEDY_FOOTPRINT_IS_NONE")


def _neuter_the_ownership_question_to_unasked():
    """RESTORE THE DEFECT EVERY REFUSAL IN THIS SECTION CARRIED: never ask
    who owns the ground a cut would take, so a refusal standing on a
    flatten=FORBIDDEN site's own ground is indistinguishable from one
    standing on open meadow."""
    return _flag("_TERMINATION_OWNERSHIP_NEVER_ASKED")


# ---------------------------------------------------------------------------
# THE BUILDER'S OWN POSTCONDITION, SCORED HERE
# ---------------------------------------------------------------------------
# The instrument this section scores lives in the BUILDER, because the
# builder is what decides whether a piece is ever placed: a deck this section
# refuses afterwards is a deck `crossings/assemble.refuse_on_seating`
# certified on the way out.  MEASURED on Ulfsland save 7632b3a5: 319 pieces
# emitted, 319 certified, and this section refused FOUR of them - two wearing
# roles no arm of that check claimed, two seated exactly on a BILINEAR bed
# reading `Heightmap.GetHeight` never returns.  Imported rather than
# restated, the same way `roads` imports `boardwalk`'s tile frame: a second
# copy of the arms here could not refuse the builder's own blindness.
def _seating_arms():
    p = str(I.JUMPSTART / "crossings")
    if p not in sys.path:
        sys.path.insert(0, p)
    import assemble as asm                                 # noqa: PLC0415
    return asm, asm.seating_population_report()


def _seating_case(key: str, what: str):
    _asm, r = _seating_arms()
    if not r["ok"]:
        return [Finding(
            check="seating_population", ok=False,
            message=f"crossings/assemble seating postcondition: "
                    + "; ".join(r["why"]),
            numbers={"arms": r["arms"], "role_neuter": r["role_neuter"],
                     "bed_neuter": r["bed_neuter"],
                     "empty_neuter": r["empty_neuter"]},
            population=f"{len(r['arms'])} arm(s) of "
                       f"`assemble.SEATING_POPULATION_DEPENDS_ON`")]
    if not r[key]["flipped"] or not r[key]["survived"]:
        return [Finding(
            check="seating_population", ok=False,
            message=(f"{what} is not load-bearing: flipped "
                     f"{r[key]['flipped']}, survived {r[key]['survived']}"),
            numbers={key: r[key]})]
    return []


def _case_the_builder_judges_every_piece_it_emits():
    """A BUILDER THAT CERTIFIES ITS OWN PLACEMENT AND A GATE THAT THEN DENIES
    IT IS THE TWO-INSTRUMENTS DEFECT ONE LEVEL DOWN, AND THE BUILDER WAS
    WRONG.  `assemble.bridge` emits five roles and its seating postcondition
    asked three of them, so a rail post 0.380 m under the ground left that
    function certified.  The role now picks the ARM and never the
    POPULATION."""
    return _seating_case("role_neuter",
                         "the seating population's independence of the role "
                         "suffix")


def _case_the_builder_reads_the_lattice_the_engine_answers_on():
    """AND THE OTHER HALF, WHICH NO ROLE WOULD HAVE FIXED.  `_pile_stack`
    seats its extra pole with the top EXACTLY on its own bed reading - zero
    margin - and that reading is BILINEAR while `Heightmap.GetHeight` is
    nearest-vertex.  MEASURED on the three columns that needed one: the
    bilinear value reproduces the placed top to 1 mm and the governing vertex
    stands +0.335 / -0.187 / +0.293 m off it."""
    return _seating_case("bed_neuter",
                         "the seating bed's dependence on the engine's own "
                         "lattice")


def _neuter_the_builders_population_to_role_suffixes():
    asm, _ = _seating_arms()
    return asm._neuter_seating_population_to_role_suffixes()


def _neuter_the_builders_bed_to_the_pivot_sample():
    asm, _ = _seating_arms()
    return asm._neuter_seating_bed_to_the_pivot_sample()


CASES = [
    Case("floating_body_over_the_ground_it_stands_on", True, _case_floating,
         "the 212-piece watchtower, 3.173-13.542 m of air over 40 columns"),
    Case("body_resting_on_the_ground", False, _case_standing),
    Case("pad_datum_agrees_with_itself", True, _case_pad_datum_lies,
         "four grounding censuses reported 0 floating by comparing each "
         "body to the datum its own pad was levelled to"),
    Case("clean_site_minus_1m_slab_pivot", False, _case_clean_site_slab,
         "stone_floor_2x2's mid-slab pivot: worst burial exactly -1.000 m"),
    Case("slab_resting_on_one_corner_of_its_own_footprint", False,
         _case_supported_at_one_corner,
         "GetHeight is nearest-vertex; the true ground rose 1.5830 m inside "
         "one cell on this world"),
    Case("piece_completely_under_the_surface", True, _case_swallowed),
    Case("a_piece_that_spans_height_is_not_buried_by_one_column", False,
         _case_a_piece_that_spans_height_is_not_buried_by_one_column,
         "sudrberg-keep's woodiron_beam_45 at (2415.064, 121.244, "
         "-3351.906), refused at 0.495 m with its own top 0.536 m PROUD of "
         "the ground, and vestvik-villa-3's wood_door refused at 0.105 m "
         "with 1.911 m of it standing - two recorded demolitions waiting on "
         "somebody to action a number",
         neuter=_neuter_the_per_column_top,
         neuters="the shipped arithmetic: the top of the piece's slice in "
                 "the sampled column instead of the top of the piece"),
    Case("a_lone_placed_prop_under_the_bank_is_buried", True,
         _case_a_lone_placed_prop_under_the_bank_is_buried,
         "Fresh42 drive-2 save a521b70356e7: the cartography table stock.py "
         "placed at ledger seq 1978, own top 1.982 m under the bank, a body "
         "of ONE once the artisan station's trigger volume stopped counting "
         "as solid - and never judged",
         neuter=_neuter_lone_pieces_unjudged,
         neuters="MIN_BODY_PIECES as the door to the burial check"),
    Case("a_lone_piece_our_wire_did_not_place_is_not_ours", False,
         _case_a_lone_piece_our_wire_did_not_place_is_not_ours,
         "lighting-T13's lamp was once blamed for burying a generated "
         "farmhouse 11.6 m away; a lone piece is claimed by provenance",
         neuter=_neuter_lone_pieces_by_proximity,
         neuters="a lone piece claimed by any anchor within "
                 "ANCHOR_MARGIN_M that declared its prefab"),
    Case("pad_asked_flat_that_slopes", True, _case_pad_sloped,
         "pads that sloped 6.593 / 3.478 / 1.495 / 0.939 m"),
    Case("pad_that_is_flat", False, _case_pad_flat),
    Case("pad_flat_at_the_wrong_height", True, _case_pad_flat_but_low),
    Case("pad_edge_outside_the_written_samples", False,
         _case_pad_edge_not_counted,
         "a 1.634 m 'pad error' that was unwritten natural ground outside "
         "the pad, 36x the real one"),
    Case("pad_samples_outside_its_own_declared_rectangle", False,
         _case_pad_skirt_outside_its_own_rectangle,
         "stenvik-stonehouse-2's 10 samples at z=908, 31 m outside its own "
         "11x15 m pad, reading stenvik-hall-1's target 47.030 and refused "
         "against 47.210"),
    Case("a_superseded_pad_record_does_not_carry_the_verdict", False,
         _case_pad_superseded_record_is_not_the_verdict,
         "tree-sth-2 seq 633 at 75.700 and tree-sth-3 seq 640 at 75.700 "
         "judged against their successors' 76.190 and 75.000, for 0.490 m "
         "and 0.700 m refusals over samples dead on their own target"),
    Case("a_reemit_inherits_its_own_sites_samples", False,
         _case_a_reemit_inherits_its_own_sites_samples,
         "vestvik-villa-3 seq 5375: rect 288, own 18, inside 0 - all 288 "
         "owned by its OWN earlier record seq 502, refused as unmeasured "
         "while dead flat on target",
         neuter=_neuter_the_site_keyed_population),
    Case("a_pad_replaced_in_full_is_measured_not_unmeasured", False,
         _case_a_pad_replaced_in_full_is_measured_not_unmeasured,
         "seven records on this chain reporting '0 of 676 / 272 / 256 / 441 "
         "samples in its population and no applied reading for any of them "
         "- unmeasured' over 2,333 lattice samples that are 2,333 READABLE, "
         "0 unowned, and six of seven dead flat at spread 0.000 m",
         neuter=_neuter_the_empty_population_to_unmeasured,
         neuters="the shipped sentence: one 'unmeasured' over four states, "
                 "printed without ever asking the surface"),
    Case("a_replaced_pad_nobody_answers_for_is_refused", True,
         _case_a_replaced_pad_nobody_answers_for_is_refused,
         "the escape the last one must not become: 121 declared pad samples "
         "retired out of the gate because somebody else's bytes hold them, "
         "with no current record judged over the ground",
         neuter=_neuter_the_answer_to_bookkeeping,
         neuters="the escape resting on OWNERSHIP instead of on a current "
                 "pad record's own declared rectangle"),
    Case("a_pad_rectangle_that_cannot_be_read_is_refused", True,
         _case_a_pad_rectangle_that_cannot_be_read_is_refused,
         "an unreadable rectangle and a dead-flat one described in the same "
         "words, which is what 'and no applied reading for any of them' "
         "said about seven fully readable pads",
         neuter=_neuter_the_unreadable_sample_to_its_target,
         neuters="an absent reading counted as the pad's own target"),
    Case("a_pad_row_nobody_wrote_is_still_inside_the_pad", True,
         _case_pad_row_nobody_wrote,
         "59 lattice samples in four declared pad rectangles that "
         "flatten.build dropped on a float comparison, 54 of them over "
         "0.10 m off target, worst 1.589 m, and invisible to every "
         "written-sample population"),
    Case("a_pad_whose_whole_rectangle_is_on_target", False,
         _case_pad_full_rectangle_is_clean),
    Case("a_later_pad_relevelled_it_and_that_is_not_a_hole", False,
         _case_a_later_pad_relevelled_it_and_that_is_not_a_hole,
         "five of ten pad_coverage refusals at save 2c7011d2 - "
         "vestvik-house-1 20, tree-sth-1 25, tree-near-1 16, "
         "stenvik-cottage-2 10, stenvik-house-3 18 - over samples the pad "
         "wrote and a later overlapping pad or road then relevelled to its "
         "own declared datum, read back EXACTLY on it",
         neuter=_neuter_the_cede,
         neuters="pad_promises returning nothing, so no sample can cede"),
    Case("a_dropped_sample_a_neighbour_holds_is_still_refused", True,
         _case_a_dropped_sample_a_neighbour_holds_is_still_refused,
         "the population-too-SMALL defect the cede could have been: a "
         "sample this pad never wrote, covered by a later write, going "
         "quiet - the four dropped edge rows up to 1.589 m under buildings"),
    Case("a_road_that_cut_the_pad_is_the_roads_section", False,
         _case_a_road_that_cut_the_pad_is_the_roads_section,
         "stenvik-house-3: 18 of its own 300 written samples are "
         "road_T8_stenvik_wttown's bed, 0.407 m up, charged to structures "
         "as well as to roads' own 148"),
    Case("a_fill_that_buries_a_standing_piece", True,
         _case_a_fill_that_buries_a_standing_piece,
         "Main's named hazard: nine of ten pad re-emits were FILLS of up to "
         "1.589 m at a pad EDGE, and ParBuried had just retired 385 pieces "
         "the ground had come up through"),
    Case("a_sample_that_does_not_move_cannot_bury", False,
         _case_a_sample_that_does_not_move_cannot_bury,
         "a terrace spec WITHDRAWN over pieces standing on samples the "
         "write does not move",
         neuter=_neuter_the_moving_filter,
         neuters="the whole rectangle judged, not the samples that move"),
    Case("a_roof_is_not_undermined_by_a_cut", False,
         _case_a_roof_is_not_undermined_by_a_cut,
         "the same withdrawal: a wood_roof at foot 36.225 and a "
         "wood_wall_roof at 36.005 standing 1.6-1.9 m up, carried by the "
         "hut's WALLS - 'a cut is refused by any solid whose span bottom is "
         "above the target' is satisfied by every piece above ground level "
         "in the world",
         neuter=_neuter_the_ground_bearing_test,
         neuters="every solid over the rectangle judged, whether the ground "
                 "is holding it up or not"),
    Case("a_piece_already_under_the_ground_is_not_buried_by_a_fill", False,
         _case_a_piece_already_under_the_ground_is_not_buried_by_a_fill,
         "Fresh42's T6 refused over a stone_stair and walls 24.6 m under "
         "the ground already (a generated underground interior); T4 over a "
         "torch whose top was already 0.78 m under its ground",
         neuter=_neuter_burial_as_a_state,
         neuters="burial judged by the state after the write, not by the "
                 "change the write makes"),
    Case("a_cut_undermines_what_it_takes_away", True,
         _case_a_cut_undermines_what_it_takes_away,
         "the refusal the ground-bearing rule must not cost: 'a cut cannot "
         "bury anything so it is safe' is true and insufficient"),
    Case("the_probe_does_not_answer_with_its_own_subject", False,
         _case_the_probe_does_not_answer_with_its_own_subject,
         "a probe reported '28 of 28 lamps sit inside a build-piece span' "
         "and the piece WAS the lamp"),
    Case("a_tree_is_not_a_building", False,
         _case_a_tree_is_not_a_building,
         "my own: a census over all 488,980 ZDOs refused a clean pad "
         "because Beech1 - A TREE - would be 'undermined by 22.071 m'"),
    Case("a_ribbon_surface_is_not_one_datum", True,
         _case_a_ribbon_surface_is_not_one_datum,
         "every pad in this world has been through the regrade census and "
         "NOT ONE ROAD WRITE HAS: `target` was a datum and ribbon.py stamps "
         "a different height at every sample, so the question could not be "
         "asked of the writes that move the most ground",
         neuter=_neuter_the_per_sample_surface,
         neuters="a per-sample surface collapsed to its mean datum, which "
                 "is what a road caller would have had to do"),
    Case("an_unwritten_sample_is_not_an_unmoved_one", False,
         _case_an_unwritten_sample_is_not_an_unmoved_one,
         "a road's rect is the window the caller cut and the SURFACE is only "
         "what the rasteriser stamped: a rect sample the write never touches "
         "folded in as one that did not move is a clean-looking census over "
         "ground nobody looked at",
         neuter=_neuter_the_unwritten_sample,
         neuters="every unnamed rect sample given its own current ground, so "
                 "it reads as unmoved and is never counted"),
    Case("a_lamp_did_not_build_a_farmhouse", False,
         _case_a_lamp_did_not_build_a_farmhouse,
         "lighting-T13: a mod-placed 30-piece farmhouse 11.6 m from a "
         "one-groundtorch plan, refused as 16 of 30 pieces buried 4.092 m"),
    Case("a_scattered_plan_is_not_an_orphan", False,
         _case_a_scattered_plan_is_not_an_orphan,
         "25 of 27 orphan_plan advisories were road_lighting - EVERY "
         "road_lighting record on the chain, 25 of 25, rising to 41 of 41: "
         "a check asking a body question of a plan that is a scatter",
         neuter=_neuter_the_scatter_split,
         neuters="clusters_into_a_body forced to always-True, i.e. every "
                 "plan judged against a body again"),
    Case("a_demolished_body_is_still_an_orphan", False,
         _case_a_demolished_body_is_still_an_orphan,
         "the state the scatter rule must not swallow: a plan that commanded "
         "a building and has none standing",
         neuter=_neuter_the_scatter_escape_to_presence,
         neuters="clusters_into_a_body forced to always-False, the weakest "
                 "escape - the shape a PRESENCE test would have"),
    Case("a_plan_the_ledger_retired_is_not_an_orphan", False,
         _case_a_plan_the_ledger_retired_is_not_an_orphan,
         "harbour-city-ulfsgard: spawn_plan seq 2690 commanded six "
         "fb_fortress_portcullis and retires seq 3090-3095 deleted all six, "
         "each citing 2690, 'Removed so a ship can enter the harbour' - "
         "reported as an orphan_plan whose only remedy would be to rebuild "
         "the gate the operator asked to have removed",
         neuter=_neuter_the_retire_netting,
         neuters="reading what the ledger PLANTED and never what the same "
                 "chain PULLED OUT"),
    Case("a_partly_retired_plan_is_still_an_orphan", False,
         _case_a_partly_retired_plan_is_still_an_orphan,
         "the escape keyed on the SITE would clear a 5,553-piece castle of "
         "which one pillar was retired",
         neuter=_neuter_the_retire_to_the_site,
         neuters="one retire anywhere in a site clearing that site's whole "
                 "plan"),
    Case("a_retire_that_names_no_plan_excuses_nothing", False,
         _case_a_retire_that_names_no_plan_excuses_nothing,
         "a deletion nobody accounted for is the state orphan_plan exists "
         "to catch, and it is indistinguishable from a retire with an empty "
         "`retires`",
         neuter=_neuter_the_retire_citation,
         neuters="any retire on the chain excusing any plan"),
    Case("a_packed_wire_declares_every_position", False,
         _case_a_packed_wire_declares_every_position,
         "226 of 421 spawn_plan records have len(wire) != command_count: the "
         "wire is semicolon-packed, and seq 137 declares 48 pieces on ONE "
         "line",
         neuter=_neuter_the_wire_reader_to_lines,
         neuters="plan_commands counting LINES instead of occurrences of "
                 "pos="),
    Case("the_wire_is_z_x_y", False, _case_the_wire_is_z_x_y,
         "a global x/z swap is an isometry, so no clustering case can catch "
         "a transposed read of pos=",
         neuter=_neuter_the_wire_transposition,
         neuters="plan_commands reading pos= as x,y,z instead of z,x,y"),
    Case("deck_open_to_the_sky_through_a_hole_in_the_roof", True,
         _case_shelter_open,
         "lh-west: 10 walled deck columns at tops 19.500 and 25.133 open to "
         "the sky with 48 roof panels all round them, MEASURED from "
         "hs_plains_ulf_the_builder_lighthouse.blueprint with no world",
         neuter=_neuter_the_roof_line_test,
         neuters="roof_enclosed forced to always-FALSE, i.e. the roof-line "
                 "test narrowed until nothing is ever inside it and every "
                 "oculus reads as a courtyard.  NOT _neuter_roof_enclosure: "
                 "that one WIDENS the predicate and this arm SURVIVES it, "
                 "because widening can only ever add holes"),
    Case("a_hole_is_a_hole_past_the_crack_width", True, _case_shelter_open,
         "the crack rule must not become a relaxation: lh-west's ten open "
         "columns sit a metre from any panel edge",
         neuter=_neuter_a_crack_a_deck_cell_wide,
         neuters="a crack as wide as a deck cell"),
    Case("a_machine_under_its_flue_is_not_a_deck", False,
         _case_a_machine_under_its_flue_is_not_a_deck,
         "complete-station-hub: emitted in drive 2, then refused at step 5 "
         "for one 'hole' at local (9.5, 15.5) - the 6.7 m top of its "
         "blastfurnace under that furnace's own open chimney",
         neuter=_neuter_every_level_top_is_a_deck,
         neuters="every level top a deck, a machine's included"),
    Case("an_open_floor_under_a_hole_is_still_refused", True,
         _case_an_open_floor_under_a_hole_is_still_refused,
         "the deck rule must not hide a hole over a building floor",
         neuter=_neuter_no_category_is_a_deck,
         neuters="no Piece category building a deck"),
    Case("a_crack_between_two_panels_is_not_a_hole", False,
         _case_a_crack_between_two_panels_is_not_a_hole,
         "sandbox-portal-hub, drive 2 step 5: refused for ONE deck column at "
         "local (5.5, 18.5) that misses its darkwood_roof_ocorner by 0.002 m "
         "- a 2-4 cm seam, 0.006 m2 over the metre around it",
         neuter=_neuter_the_column_is_a_line,
         neuters="the shipped line test, every opening a hole"),
    Case("a_slot_narrower_than_the_walker_is_not_a_hole", False,
         _case_a_slot_narrower_than_the_walker_is_not_a_hole,
         "hs_plains_ulf_the_builder_lighthouse.blueprint (lh-west): refused "
         "unsheltered_deck for 3 columns at top 19.5 that are slots of 0.27, "
         "0.50 and 0.20 m2 between darkwood beams and arches, cover 0.08 to "
         "0.22 m from each column - no disc of the walker's 0.49 m radius "
         "fits in any",
         neuter=_neuter_every_opening_admits_the_walker,
         neuters="every column of the verdict set a hole, whatever fits in "
                 "the opening over it"),
    Case("deck_under_a_closed_gable", False, _case_shelter_closed),
    Case("a_shallow_gable_with_its_roof_intact", False,
         _case_shallow_gable_still_closed),
    Case("a_ridge_slot_across_the_whole_roof_depth", False,
         _case_ridge_slot_across_the_whole_roof_depth,
         "the old two-course fixture: a gap spanning the roof's ENTIRE "
         "depth, which is a vent and not a leak - and the case that fails if "
         "roof_enclosed is ever replaced by a plain wall test",
         neuter=_neuter_roof_enclosure,
         neuters="roof_enclosed forced to always-True, i.e. the shelter "
                 "verdict back on walls alone"),
    Case("open_deck_with_no_walls_round_it_is_a_terrace", False,
         _case_terrace_is_not_a_hole,
         "wt-peak: 69 of 110 deck columns open, 65 of them parapet walk"),
    Case("a_bounding_box_covers_what_the_panel_does_not", False,
         _case_box_covers_what_the_panel_does_not,
         "the blind test, kept so it cannot come back"),
    Case("roof_skin_step", True, _case_skin_step,
         "dockshore/vestvik's remaining 1.13 m outer-skin step",
         neuter=_neuter_the_lap_allowance_to_the_rise,
         neuters="the panel's 1.1310 m RISE standing in for its 0.126 m "
                 "thickness, which passes a hole exactly one course deep"),
    Case("roof_skin_continuous", False, _case_skin_continuous,
         neuter=_neuter_the_measured_pitch_allowance,
         neuters="the allowance the body's own steepest panel earns; "
                 "without it a 26 degree roof refuses itself"),
    Case("a_ridge_board_standing_on_the_tiles_is_not_a_hole", False,
         _case_ridge_board_is_not_a_hole,
         "24 of 27 roof_skin refusals were gable-cap edges over a skin "
         "that carries on underneath - stenvik-manor-1 15, every tree "
         "house 12, both vestvik villas 16",
         neuter=_neuter_the_height_field_to_its_maximum,
         neuters="the MAX-ONLY height field that refused those 24 bodies"),
    Case("a_lifted_course_is_still_a_hole", True,
         _case_lifted_course_is_still_a_hole,
         "the refusal the second-layer rule must not lose: nothing at the "
         "lower level in the taller column",
         neuter=_neuter_the_lap_allowance_to_the_rise,
         neuters="the rise-as-thickness allowance, which is exactly one "
                 "course deep and so swallows exactly this hole"),
    Case("a_180_deg_flip_moves_the_piece_not_at_all", False,
         _case_flip_moves_nothing,
         "349/349 at tolerance 0, over 349 inverted panels"),
    Case("a_180_deg_flip_changes_the_bounding_box_not_at_all", False,
         _case_flip_keeps_the_box,
         "the AABB is invariant under the flip that inverts the roof"),
    Case("an_upslope_panel_passes_orientation", False,
         _case_upslope_panel_passes),
    Case("the_same_panel_yawed_180_deg_fails_orientation", True,
         _case_flipped_panel_fails,
         "385 inverted panels in 74 bodies, 10 upside down",
         neuter=_neuter_orientation_to_a_position_census,
         neuters="the count-and-position census that reported '349/349 at "
                 "tolerance 0' over 349 inverted panels"),
    Case("orientation_is_scoped_to_our_own_bodies", False,
         _case_orientation_is_scoped_to_our_own_bodies,
         "20 of 21 refused roofs at save 39e4d7b7 stood 65.3 m to 4,465.2 m "
         "from any ledger anchor of this run - generated village houses and "
         "ruins, in repeated 11/27/42-panel signatures, holding all 10 "
         "upside-down panels"),
    Case("two_halls_sharing_an_eave_are_not_inverted", False,
         _case_two_halls_sharing_an_eave_are_not_inverted,
         "harbour-city-ulfsgard: 14 CORRECT panels refused because their "
         "shared eave is 6.000 m from one hall's ridge and 6.006 m from the "
         "other's, and the rule picked the nearest",
         neuter=_neuter_the_nearest_ridge_tie,
         neuters="the shipped rule: one ridge point, the nearest, and the "
                 "dot product against it"),
    Case("an_apex_course_laid_backwards_is_refused", True,
         _case_an_apex_course_laid_backwards_is_refused,
         "harbour-city-ulfsgard's 52 apex panels, margin 0.506 m - a "
         "0.969 m gutter where the ridge should be and 104 roof_skin "
         "holes; ledger seq 5074 repaired the other 144 and could not see "
         "these",
         neuter=_neuter_the_ridge_skip,
         neuters="the shipped 0.75 m skip, which never judged the ridge "
                 "course of any roof in the world"),
    Case("a_roof_with_no_ridge_is_unmeasured_not_sound", False,
         _case_a_roof_with_no_ridge_is_unmeasured_not_sound,
         "11 phantom inversions on the 30-panel wood_roof_45 body at "
         "(481.1, 920.4), every panel of which tops out at 53.052",
         neuter=_neuter_the_no_ridge_guard,
         neuters="asking a body with no ridge which way its panels climb"),
    Case("a_hip_cap_is_not_judged_against_its_own_centre", False,
         _case_a_hip_cap_is_not_judged_against_its_own_centre,
         "sandbox-portal-hub, drive 2 step 5 rc 1: four darkwood_roof_"
         "ocorner caps read -0.947 m authored AND turned 180, the body was "
         "refused and every portal pair in the hub stranded",
         neuter=_neuter_the_ridge_alone_decides,
         neuters="the ridge proxy deciding alone, which asks a ridge piece "
                 "about its own box centre"),
    Case("a_lower_wing_is_judged_by_its_own_seams", False,
         _case_a_lower_wing_is_judged_by_its_own_seams,
         "blackforest-walled-base: 35 correct lower-wing panels judged "
         "against the tallest ridge and yawed by the emitter - roof_skin 3 "
         "holes / 12 spanned as authored, 5 / 31 as emitted",
         neuter=_neuter_the_ridge_alone_decides,
         neuters="the ridge proxy deciding alone: one ridge per body"),
    Case("a_reversed_panel_is_not_cleared_by_its_seams", True,
         _case_a_reversed_panel_is_not_cleared_by_its_seams,
         "the seam test only clears a nominee whose own edges meet the roof "
         "better placed than turned; a real reversal reads the other way",
         neuter=_neuter_the_seams_clear_every_nominee,
         neuters="a seam test that clears whatever the ridge nominates"),
    Case("a_corbel_on_its_side_is_not_upside_down", False,
         _case_a_corbel_on_its_side_is_not_upside_down,
         "ashlands-forward-base, drive 2 step 5 rc 1: refused for row 44, "
         "one of four ashwood_wall_roof_26 pillar corbels with their own "
         "up at y = -0.0007 - on their side - whose mirror twin 'rises away' "
         "0.674 m from a ridge above the pillar",
         neuter=_neuter_on_side_judged,
         neuters="every sloped roof-class piece judged whatever its roll"),
    Case("a_panel_rolled_face_down_is_still_upside_down", True,
         _case_a_panel_rolled_face_down_is_still_upside_down,
         "the on-side exemption must not become a roll exemption: a panel "
         "with its own up at y = -1 is upside down",
         neuter=_neuter_every_roll_on_its_side,
         neuters="any roll at all exempting a piece"),
    Case("two_neighbouring_houses_are_two_roofs", False,
         _case_two_neighbouring_houses_are_two_roofs,
         "12 inverted panels refused ACTIONABLE on the Fresh42 drive-2 save "
         "a521b70356e7 - 11 of stenvik-house-2's judged against stenvik-"
         "manor-1's ridge 0.72 m higher (roofs 0.68 m apart), 1 of "
         "tree-sth-2's against tree-sth-3's; 0 of 1297 inverted with each "
         "building judged alone",
         neuter=_neuter_union_and_the_ridge_alone,
         neuters="every roof piece back in one geometric pool AND the ridge "
                 "deciding alone, so a neighbour's taller ridge judges a "
                 "correct roof"),
    Case("a_backwards_panel_beside_a_neighbour_is_still_refused", True,
         _case_a_backwards_panel_beside_a_neighbour_is_still_refused,
         "the per-building partition must keep refusing a panel laid at the "
         "opposite yaw, including the one facing a taller neighbour, which "
         "the geometric union scores as sound",
         neuter=_neuter_orientation_every_piece_alone,
         neuters="the degenerate partition: every roof piece its own "
                 "building, so no body reaches three panels"),
    Case("a_gap_between_two_houses_is_not_a_hole_in_either", False,
         _case_a_gap_between_two_houses_is_not_a_hole_in_either,
         "shelter refused tree-west-1+tree-west-2+tree-west-3 on the Fresh42 "
         "drive-2 save a521b70356e7 for 1 'hole in the roof' at "
         "(-243.5,811.5) - the column between three stilt houses, walled "
         "and roofed by their three roofs; each house alone 0 of 37",
         neuter=_neuter_orientation_by_geometric_union,
         neuters="the geometric pool back: two houses judged as one roof"),
    Case("duplicates_are_scoped_to_our_own_bodies", False,
         _case_duplicates_are_scoped_to_our_own_bodies,
         "all 7 refused sites and all 46 co-located pieces on save "
         "5385652e stood 86.9 m to 433.7 m from any declared build "
         "placement of this run, and two of the bodies are one generated "
         "location stamped twice with the SAME co-located pair at the same "
         "local offset to 2 mm",
         neuter=_neuter_the_duplicate_scope,
         neuters="the world-wide population - check_duplicates over every "
                 "build piece in the save"),
    Case("a_duplicate_in_a_body_we_built_is_still_refused", True,
         _case_a_duplicate_in_a_body_we_built_is_still_refused,
         "the other sign of the scope: a population narrowed until nothing "
         "is left in it reports a clean world",
         neuter=_neuter_the_scope_to_nothing,
         neuters="an ownership scope that reaches zero"),
    Case("two_pieces_in_the_same_cube", True, _case_duplicate_pair,
         "52 co-located pieces at the Ulfsgard boathouse"),
    Case("same_prefab_same_pivot_buried_in_its_twin", True,
         _case_duplicate_pair_rotated,
         "a square slab yawed 90 deg fills the same space and a "
         "quaternion grade would let it through"),
    Case("a_valley_pair_is_not_a_duplicate", False,
         _case_valley_pair_is_not_a_duplicate,
         "13 wood_roof at the Ulfsgard boathouse's shared eave were "
         "refused as copies; deleting one opens 48.9 m2 of roof"),
    Case("pieces_2m_apart", False, _case_distinct_pieces),
    Case("a_legitimate_pile_is_not_a_duplicate", False,
         _case_pile_is_not_a_duplicate,
         "objects_count is an XZ cylinder with no height bound"),
    Case("the_column_is_not_a_bounding_box", False, _case_column_is_not_a_box),
    Case("the_column_misses_what_it_is_not_over", False,
         _case_column_misses_outside),
    # --- why each refusal can or cannot be closed -------------------------
    Case("every_refusal_carries_a_termination_class", False,
         _case_every_refusal_carries_a_class,
         "structures was the LAST section emitting NO_CLASS: 105 refusals "
         "on Ulfsland 7632b3a5 and the one unclassified row was this "
         "section's `buried`",
         neuter=_neuter_the_termination_class_away,
         neuters="publishing no termination_class at all, as shipped"),
    Case("an_unread_owner_set_is_out_of_order_not_actionable", False,
         _case_an_unread_owner_set_is_out_of_order_not_actionable,
         neuter=_neuter_the_unread_instrument_to_clear,
         neuters="an instrument that did not answer, read as one that "
                 "answered CLEAR"),
    Case("a_refusal_with_no_terrain_remedy_is_unserved_not_unknown", False,
         _case_a_refusal_with_no_terrain_remedy_is_unserved_not_unknown,
         "passing None for the footprint on no-remedy rows turned six "
         "correct 1C_UNSERVED rows into UNKNOWN in harbours",
         neuter=_neuter_the_no_remedy_footprint_to_nothing,
         neuters="a None footprint where 0.0 belongs"),
    Case("ground_a_forbidden_site_owns_is_not_actionable", False,
         _case_ground_a_forbidden_site_owns_is_not_actionable,
         "four of nine refusals on gate C named ground writer's flatten "
         "guard will not let anyone move, and nothing in the refusal said so",
         neuter=_neuter_the_ownership_question_to_unasked,
         neuters="never asking who owns the ground a cut would take"),
    Case("our_lamp_under_our_road_is_ours_to_reconcile", False,
         _case_our_lamp_under_our_road_is_ours_to_reconcile,
         "drive 3, save 05eb7c6c05de: T6's roadside groundtorch (seq 2716, "
         "step 7) 1.168 m under T6's own road write (seq 1951, step 4), "
         "classed 1B_CONTESTED",
         neuter=_neuter_blocker_ownership_blind,
         neuters="ownership blind: two of our own claims read as strangers"),
    # --- the builder's own postcondition, scored here ---------------------
    Case("the_builder_judges_every_piece_it_emits", False,
         _case_the_builder_judges_every_piece_it_emits,
         "assemble.bridge certified 319 of 319 pieces of "
         "W10-brgs1-treenear-x01 and this section refused 4 of them; 2 wore "
         "roles no arm of the builder's seating check claimed",
         neuter=_neuter_the_builders_population_to_role_suffixes,
         neuters="the builder's burial population keyed on the role "
                 "suffixes /pile, /deck and /head"),
    Case("the_builder_reads_the_lattice_the_engine_answers_on", False,
         _case_the_builder_reads_the_lattice_the_engine_answers_on,
         "_pile_stack seated three poles with their top EXACTLY on a "
         "BILINEAR bed reading; the governing lattice vertex stands "
         "+0.335 / -0.187 / +0.293 m off it and two of the three were "
         "emitted under the surface",
         neuter=_neuter_the_builders_bed_to_the_pivot_sample,
         neuters="one bilinear bed sample at the piece pivot"),
]


register(Section(
    name="structures",
    measures=("every built body stands on the surface the engine draws, "
              "every pad asked to be flat is flat over its own written "
              "samples, every sheltered deck is actually under something, "
              "every oriented piece rises toward its own ridge, and no piece "
              "was emitted twice"),
    instrument=("Heightmap.GetHeight read back from the save through "
                "run_waterproof.sh FROM_SAVE=1 on a frozen copy (the applied "
                "surface); ZDOMan.m_objectsByID whole-save census with "
                "rotation; each prefab's own solid from "
                "blueprints/data/piece_geometry.json intersected with the "
                "vertical column, never its bounding box; every ground query "
                "snapped to the lattice vertex that answers it, because "
                "Heightmap.GetHeight is a step function on the 1 m lattice "
                "and not a surface; each pad's own "
                "TerrainComp blobs differenced against the previous state of "
                "the same zones"),
    population=("every connected body of 3 or more placed build pieces that "
                "a ledger spawn_plan anchor falls within 8 m of (the "
                "buildings this run planted) for grounding, shelter and skin; "
                "the lattice samples each ledger site_pad write set and still "
                "owns for flatness; every placed piece whose prefab name "
                "contains 'roof' world-wide for orientation; every placed "
                "build piece world-wide for duplicates"),
    run=run,
    cases=CASES,
))
