#!/usr/bin/env python3
"""Write a planned road segment into the world as a levelled, paved terrain
ribbon -- through the ledger, in the proven order, with the clamp checked per
sample.

THE MECHANISM.  A road is not pieces: `piece_pavedroad` has no persistent
ZNetView and zero road pieces exist in any fleet save.  What persists is one
`_TerrainCompiler` ZDO per zone carrying a gzip'd `TCData` blob of per-sample
height deltas and per-sample ground paint.  So the ribbon is rasterised onto
each zone's 65x65 sample lattice, the delta is `profile_y - generated_y` at that
sample, and the paint is `paved_cleared` on the carriageway with a `dirt_cleared`
shoulder.

THE ORDER IS NOT NEGOTIABLE, AND ONE STEP OF IT IS THE MOST EXPENSIVE THING
ANYONE MEASURED TONIGHT:

  0. `zones_generate` EVERY zone the ribbon touches, and OBSERVE a `_ZoneCtrl`
     at the zone centre before writing it.  MEASURED by `GroundTruth`: paint
     with a cleared alpha over all 1,681 samples of a pad in an UNGENERATED zone
     planted Bush01 x15, Beech1 x15, RaspberryBush x2 and eleven more prefabs
     anyway, up to 7.68 m above the pad, as permanent ZDOs.  Cause:
     `Heightmap::Generate` ends in `ApplyModifiers`, which locates its compiler
     by scanning the static `TerrainComp::s_instances` list of INSTANTIATED
     components, and a server with no peers instantiates none -- so neither the
     deltas nor the cleared alpha reach the heightmap before `SpawnZone` runs
     `PlaceVegetation`.  For a pad that is one zone; for a 4.8 km ribbon it is
     every zone along it, which is why generation time, not paving, dominates
     the cost of this build.
  1. LOCATION CHECK every zone against the per-type radii.  MEASURED: `flatten.py`
     has NO location check and cut 6.65 m of ground out from under a
     `LocationProxy` holding a buried treasure chest, because it delegates the
     stand-off to `clearing/area.py`, which only runs when CLEARING runs.  A
     ribbon crosses hundreds of zones where clearing has not run, so the check
     lives here and a zone that fails it is not written.
  2. Write the compiler, one per zone, `operations = 2` with the op point at the
     zone centre and radius 32.  MEASURED: `operations == 1` selects
     `TerrainComp::CheckLoad`'s narrow `ResetGrass(m_lastOpPoint,
     m_lastOpRadius)` branch (because `before` is 0 on a fresh component), and a
     zeroed op record then resets a zero-sized box at the WORLD ORIGIN -- the
     operator's recurring floating grass.

ZDO COST.  One `_TerrainCompiler` per zone, because
`Heightmap::GetAndCreateTerrainCompiler` returns the FIRST it finds and a second
is dead weight.  Cost is per ZONE, not per metre.

Usage:
    ribbon.py plan  --segment T1-portalhub-temple      # offline, no writes
    ribbon.py build --segment T1-portalhub-temple      # live, through the ledger
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import heapq
import math
import os
import re
import shutil
import sys
from pathlib import Path

import numpy as np
import yaml

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(JUMPSTART / "terraform"))
sys.path.insert(0, str(JUMPSTART))

import applied as appliedmod  # noqa: E402
import flatten as flattenmod  # noqa: E402
import poi as poimod  # noqa: E402
import precensus as censusmod  # noqa: E402
import reemit as reemitmod  # noqa: E402
import route as routemod  # noqa: E402
import spec as specmod  # noqa: E402
import tcdata  # noqa: E402
import worldseed  # noqa: E402

# RESOLVED, not literal.  `main()` rebinds from `--seed`/`--world`.
SEED = worldseed.seed()
WORLD = worldseed.world()
SCRATCH = Path("/tmp/roads/zonepatch")
# WHO THE LEDGER SAYS BUILT THE ROAD.  `RoadNet` routed and pre-flighted the
# network, `RoadBuild` wrote the first pass, and `RoadEmit` writes the REPAIR:
# the graded batter, the inclusive edge and the pad approaches.  The log has to
# say who did what or the provenance of a defect is a guess -- and a repair
# appended under the name of the actor whose defect it repairs is the one
# record nobody can read.
ACTOR = "RoadEmit"
# Shoulder and the road surface's half-extent: ONE DEFINITION, in `route.py`.
# Re-exported here under the names every caller in this file already uses, so
# there is exactly one place that computes the boundary and no caller has to
# change.  It moved out of this file deliberately: the ROUTER has to reserve
# the same footprint the WRITER lays down, the router must not import the
# writer, and `route.py` is the lowest module both already import.  See
# `route.footprint_half_m` for the measurement that made it one object --
# N4's 71 clamp breaches, every one of them at lat 3.99-4.12 m.
SHOULDER_M = routemod.SHOULDER_M
footprint_half_m = routemod.footprint_half_m
PAINT_ROAD = "paved_cleared"
PAINT_SHOULDER = "dirt_cleared"


# --- the batter: the side slope that joins the earthwork to natural ground --
#
# THE DEFECT THIS EXISTS FOR, MEASURED.  Until this existed the ribbon wrote
# every sample out to `edge = width/2 + shoulder` and NOTHING at edge + 1, so
# the height delta fell from the full local cut or fill to 0.0 across ONE 1 m
# lattice edge.  The carriageway itself was never the problem: on samples the
# road alone wrote, the applied surface matches the fitted profile exactly and
# its 1 m step is 0.047-0.120 m median, 0.118-0.182 m worst, i.e. the 8 %
# trunk / 12 % spur design grade expressed on a 1 m lattice.  The BLOCKINESS
# the operator reported was transverse: measured over the 15 standing
# segments, the step one metre outside the shoulder runs 0.39-1.46 m median,
# up to 8.06 m, on 42-89 % of each segment's length.  T12 at s = 302 m read
# 68.41 m across the whole 8 m bench and 65.27 m one metre later.  A level
# bench with a vertical face on each side IS a rectangular prism, and on a
# side slope that is exactly "blocky rather than sloped".
#
# THE BATTER IS A SURFACE AT GRADE, NOT A DELTA RAMPED TO ZERO, and the
# difference is the whole reason the transverse verdict could not be read.
# The first form interpolated the HEIGHT CHANGE linearly to zero over
# `run = |full| / grade`.  On level ground that yields a 38 deg face, and on a
# side slope -- the only place a batter is needed -- it does not: the applied
# surface is `generated + delta`, so its transverse slope is the taper's grade
# PLUS the hillside's own.  MEASURED on T12 after the first batter: the step
# one metre outside the shoulder read p50 0.83 m against a 0.781 limit, and
# that 0.049 m is not noise and not the design grade -- it is the hill.  A
# `step <= BATTER_GRADE` check therefore FAILS on output that looks right,
# which is this project's signature defect wearing a new hat.
#
# So the batter is constructed as what it is on the ground: a PLANE through
# the carriageway edge, falling (fill) or rising (cut) at exactly the grade,
# written as `delta = target - generated` and TERMINATED WHERE IT MEETS
# NATURAL GROUND, i.e. at the sign flip of that delta.  Three properties
# follow by construction rather than by measurement:
#   * the transverse step of the applied surface IS the grade, so the verdict
#     can be read against the grade;
#   * the taper ENDS AT GROUND -- there is nothing left to step off;
#   * the reach is bought only where the geometry needs it, and on a hillside
#     steeper than the batter the two surfaces never meet, which is a
#     MEASURABLE refusal (`steeper_than_batter`) rather than a silent wall.
#
# The slope is taken at the 38 deg SLIDE limit rather than something steeper
# so the batter is WALKABLE: the operator who steps off the carriageway on a
# hillside should walk down, not slide.  That costs reach, and reach is what
# the cap below bounds.
BATTER_GRADE = math.tan(math.radians(38.0))

# THE CAP, AND IT WAS DERIVED AGAINST THE WRONG HEIGHT.
#
# It read `CLAMP_M / BATTER_GRADE = 8 / 0.7813 = 10.24 m, plus margin` -- "the
# run the full +/-8 m apply clamp needs".  That is the run a taper needs TO
# CLOSE ONE CLAMP'S WORTH OF MOVE ON LEVEL GROUND, and level ground is the one
# case where no batter is wanted at all.  The face a batter actually has to
# lay down is the RELIEF THE CARRIAGEWAY WAS CUT THROUGH -- clamp of cut on
# the high side plus clamp of fill on the low side -- and `north.py` already
# derives exactly that bound and refuses a bench past it:
#
#     CARRIAGEWAY_RELIEF_LIMIT_M = 2.0 * routemod.CLAMP_M = 16.0 m
#
# so the run is 16.0 / 0.7813 = 20.479 m, carrying the SAME proportional
# margin the 10.5 m form carried (10.5 / 10.2395 = 1.02545) -> 21.0 m.  The
# arithmetic is written out rather than pasted so the two cannot drift apart.
#
# MEASURED, Fresh42, 14 segments, offline `--validate`, 2026-09-24, cap alone
# with nothing else changed:
#     rim steps CAUSED, network-wide   582 -> 147
#     worst caused step             5.4927 -> 2.3483 m
#     worst wall left                4.758 -> 3.200 m
#     T4 128 -> 4 . T9 203 -> 44 . T8 97 -> 54 . S11 43 -> 4 . S4 55 -> 8
# AND THE OBJECTIVE IS NOT MONOTONE IN THE CAP, which is why this had to be
# swept rather than nudged: T9's worst caused step goes 7.0959 (no batter) ->
# 7.6041 (3 m) -> 7.4515 (6 m) -> 5.4927 (10.5 m) -> 5.7291 (16 m) -> 2.3435
# (21 m) -> 1.4935 (24 m).  Both 10.5 and 16 sit on the wrong side of a hump.
# `approach_reach_m` records the same property about the pad approach.
BATTER_REACH_MARGIN = 10.5 / (tcdata.CLAMP_M / BATTER_GRADE)
BATTER_MAX_M = round(2.0 * tcdata.CLAMP_M / BATTER_GRADE
                     * BATTER_REACH_MARGIN, 1)
# A batter sample is EARTHWORK, not road: it keeps the biome's own ground
# texture so the visible road stays 8 m wide.  Writing paint here would read
# as a 29 m wide dirt highway.
PAINT_BATTER = None
# THE CARRIAGEWAY EDGE IS INCLUSIVE, AND ONE FLOAT ULP DECIDED IT WAS NOT.
# MEASURED by RoadClear on T12 at s = 116 m: lat -3 reads 67.00, lat -4 reads
# 65.24, lat -5 reads 68.13 -- a ONE METRE WIDE TRENCH 1.76 m deep at the
# road edge, deeper than the wall beside it.  The sample sits at exactly
# lat = 4.0 = half + shoulder, and `lat` is a `hypot` of two floats, so it
# comes back as 4.000000000000001 and a `lat <= edge` test drops it out of the
# carriageway.  Under the batter that notch would be a HIDDEN HOLE beneath a
# graded slope, so the tolerance is stated once, here, and used at both band
# boundaries.  1e-6 m is twelve orders of magnitude above the ULP and eleven
# below anything the operator can stand in.
EDGE_EPS_M = 1e-6


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------

def segment_zones(nodes: list, is_bridge: list, width_m: float,
                  shoulder_m: float = SHOULDER_M,
                  batter_m: float = BATTER_MAX_M) -> list[tuple[int, int]]:
    """Zones whose lattice the ribbon's terrain part touches.

    Densified to 1 m between stations: at a 2 m station spacing a ribbon that
    merely clips a zone corner between two stations would otherwise be missed,
    and a missed zone is a GAP IN THE ROAD -- the one defect the operator is
    guaranteed to walk into.

    THE BATTER IS PART OF THE REACH, and leaving it out is not a cosmetic
    error.  `stamp` can only write a sample whose zone it was handed a patch
    for, so a batter sample in an unrequested zone is dropped SILENTLY -- and a
    dropped batter sample is the very wall the batter exists to remove.
    MEASURED on T4 before this existed: zones (8,14), (9,14) and (8,15) were in
    the patch request and produced no compiler at all, and 333 of 1,443
    centreline metres came back unwritten.
    """
    half = width_m / 2.0 + shoulder_m + batter_m
    zones: set[tuple[int, int]] = set()
    prev = None
    for (x, z), br in zip(nodes, is_bridge):
        if br:
            prev = None
            continue
        pts = [(x, z)]
        if prev is not None:
            px, pz = prev
            n = max(1, int(math.ceil(math.hypot(x - px, z - pz))))
            pts = [(px + (x - px) * k / n, pz + (z - pz) * k / n) for k in range(n + 1)]
        for cx, cz in pts:
            for dx in (-half, 0.0, half):
                for dz in (-half, 0.0, half):
                    zones.add(tcdata.zone_of(cx + dx, cz + dz))
        prev = (x, z)
    return sorted(zones)


# WHAT THE STATION COORDINATE ACTUALLY DEPENDS ON, AND THE OLD RULE NAMED A
# SUBSET OF IT.  `s` exists for exactly one purpose -- to group lattice
# samples into RAYS -- and a grouping key is only a ray if it is INJECTIVE
# along the polyline: two samples with the same station and the same bearing
# must have the SAME FOOT.
#
# The old accumulator advanced only over TERRAIN runs, on the stated premise
# that "a bridged run is not part of this ribbon's surface, so its length must
# not shift the stations of everything past it".  That premise guards ONE
# direction -- a ray split in half -- and leaves the other wide open: with a
# bridged run contributing ZERO length, the far bank's arc RESUMES AT THE NEAR
# BANK'S, so the two banks of every crossing share a station bin and their two
# radials are walked as one ray.
#
# MEASURED, Fresh42, T1-portalhub-temple, offline `--validate`, 2026-09-24.
# Ray ((+1, 54), bearing 63) held 23 members with TWO DISTINCT FEET 28.9 m
# apart -- (-164.28, -351.70) and (-181.43, -375.32), the two banks of
# crossing x01 whose deck is 33.482 -- and `members.sort()` interleaved them
# by radius.  The walk took `full_edge` = -1.8526 (a CUT) from the near bank
# and applied it to the far bank, where the ground is 5.9 m lower and the
# earthwork is a FILL: every far-bank sample then read `met_ground`, which is
# the batter's SUCCESS exit, and was skipped WITH NO RECORD AT ALL.  The cell
# (-186, -372) is one of them -- unwritten, 2.792 m below eight written
# neighbours, and 6 of T1's 8 caused rim steps are that single cell.  Its
# segment reports `walls_left_by_cause {}` and `0 clipped`, because nothing
# clipped it: THE KEY THREW IT AWAY.
#
# So the length of a bridged run is carried.  The absolute value is still
# irrelevant -- nothing reads `s` except the two station keys in `stamp` --
# but it must be STRICTLY INCREASING along the polyline, which is the property
# that makes equal stations mean one foot.
STATION_ARC_DEPENDS_ON = ("every_run_length_along_the_polyline",
                          "including_the_runs_this_ribbon_does_not_surface")


def node_arcs(nodes, br) -> list[float]:
    """Arc length to each node, for grouping samples into cross sections.

    ONE FUNCTION FOR BOTH CALLERS.  `lateral_and_y_grid` and
    `solve_approach`'s station walk each carried their own copy of this loop,
    with a comment in the second saying it used "the same convention" as the
    first -- a convention two copies can only agree on by luck.  Replacing it
    globally is also what lets `selftest_station_arc_injective` neuter the
    SHIPPING rule instead of a reconstruction of it.

    `br` is accepted and deliberately unused: the signature names the thing
    the old rule keyed on so a reader can see that the dependency was DROPPED
    on purpose rather than forgotten.  See STATION_ARC_DEPENDS_ON.
    """
    del br
    arc = [0.0] * len(nodes)
    acc = 0.0
    for k in range(len(nodes) - 1):
        arc[k] = acc
        acc += math.hypot(float(nodes[k + 1][0]) - float(nodes[k][0]),
                          float(nodes[k + 1][1]) - float(nodes[k][1]))
    if len(nodes):
        arc[-1] = acc
    return arc


# WHAT THE RIBBON'S CLAIM ON A PIECE OF GROUND ACTUALLY DEPENDS ON.  It is not
# the perpendicular distance alone: it is the distance AND what the polyline
# does past the foot it was measured to.
RIBBON_CLAIM_DEPENDS_ON = ("perpendicular_distance_to_a_terrain_run",
                           "what_the_polyline_does_past_that_run's_end")


def clamp_ends(nodes, br, k: int) -> tuple[bool, bool]:
    """May terrain run `k`'s projection be CLAMPED at its low and high ends?

    A CLAMP AT AN END IS A CLAIM ON THE HALF-PLANE PAST IT, and whether that
    claim is the ribbon's depends on what the polyline does there:

      * the road CONTINUES ON LAND -- the next run is terrain.  The clamp is
        the vertex-wedge fix below and it must stay: without it 61 of T3's
        1,795 stations fall through at 22 turns.
      * the road ENDS.  The clamp caps the ribbon at its last station, which
        is the terminus the old fallback computed, and it must stay.
      * the road CONTINUES AS A DECK.  The clamp then claims the half-plane
        STRAIGHT DOWN THE SPAN -- a disc of `reach` around the bank node, laid
        over exactly the water the deck exists so that nothing touches.  The
        bridged-run skip on the line below is DEFEATED by the clamp on the run
        beside it, and "the ribbon avoids paving the riverbed" was true of the
        skip and false of the pair.

    MEASURED (Ford, Fresh42, replan2 with crossings declared): 736 of the
    1,111 samples `water_sign` still charges this network sit beside a station
    the plan marks `is_bridge: True`, 505 of them INSIDE the carriageway band
    -- worst +4.673 m of fill at T1 [-129, -313] and +3.461 m at T4
    [-169, -361].  Those stations write no carriageway at all; the earthwork
    went down there anyway, through the end fan.

    `tt > 1` is exactly "past the perpendicular through the end node", which
    on a bank IS the span side, so rejecting rather than clamping at that end
    cuts the fan to the half-disc BEHIND the abutment and leaves the abutment
    itself fully graded.
    """
    lo = not (k >= 1 and bool(br[k - 1]))
    hi = not (k + 2 < len(nodes) and bool(br[k + 2]))
    return lo, hi


def lateral_and_y(nodes: np.ndarray, prof: np.ndarray, br: np.ndarray,
                  x: float, z: float) -> tuple[float, float] | None:
    """Perpendicular distance from the centreline and the profile height there.

    Projects onto each TERRAIN polyline segment and keeps the nearest.  A
    bridged segment is skipped, so the ribbon stops at the bank and the deck is
    Crossings' -- which is also how the ribbon avoids paving the riverbed, but
    ONLY TOGETHER WITH `clamp_ends`: see RIBBON_CLAIM_DEPENDS_ON.

    THE PROJECTION PARAMETER IS CLAMPED, NOT REJECTED, and that is a DEFECT FIX
    rather than a refinement.  MEASURED on T3-temple-meadhall: rejecting `tt`
    outside [0, 1] leaves the WEDGE on the outside of every polyline vertex
    claimed by NO segment, so 61 of 1,795 centreline stations -- 22 separate
    notches, one at each turn -- were skipped by the rasteriser while sitting
    0.0 to 1.0 m from the centreline.  An unwritten sample keeps its GENERATED
    height, so each notch is a 1 m hole in the middle of the carriageway as
    deep as the fill there, up to 3.31 m on T3.  Worse, `min` over the segments
    that DID accept the projection then answered with a far branch of the same
    road: the sample at (-31, -34) came back as 58.69 m from the centreline
    while standing 1 m from it, so the check reported a lateral distance for a
    place the road does not go.  Clamping makes the measure the true distance
    to the POLYLINE, and at a vertex both adjacent segments agree on the same
    clamped point and the same profile height, so the surface stays continuous.
    The end behaviour is unchanged: clamping caps a ribbon end at the distance
    to its last station, which is exactly what the old fallback below computed.

    WHERE THE RUN ENDS AT A BANK THE CLAMP IS WITHHELD, because there the
    half-plane past the end is the DECK's ground and not this ribbon's.
    """
    best = None
    for k in range(len(nodes) - 1):
        if br[k] or br[k + 1]:
            continue
        ax, az = nodes[k]
        bx, bz = nodes[k + 1]
        dx, dz = bx - ax, bz - az
        L2 = dx * dx + dz * dz
        if L2 <= 1e-12:
            continue
        tt = ((x - ax) * dx + (z - az) * dz) / L2
        lo_ok, hi_ok = clamp_ends(nodes, br, k)
        if tt < 0.0:
            if not lo_ok:
                continue
            tt = 0.0
        elif tt > 1.0:
            if not hi_ok:
                continue
            tt = 1.0
        px, pz = ax + dx * tt, az + dz * tt
        d = math.hypot(x - px, z - pz)
        if best is None or d < best[0]:
            best = (d, prof[k] + (prof[k + 1] - prof[k]) * tt)
    if best is None:
        # Every segment is bridged (or there are none): the terrain part of the
        # ribbon is the bank only, measured to the nearest terrain station.
        idx = [k for k in range(len(nodes)) if not br[k]]
        if not idx:
            return None
        dd = [(math.hypot(nodes[k][0] - x, nodes[k][1] - z), k) for k in idx]
        d, k = min(dd)
        return d, float(prof[k])
    return best


# WHICH OF A CELL'S LOSING CLAIMANTS IS WORTH REPORTING, declared so that
# dropping one of these terms is an edit to this tuple rather than an
# invisible re-key.  It is not "the nearest one": that names a SUPERSET of
# "a claimant this ground cannot absorb", and the superset's answer at almost
# every cell is the winner's OWN contiguous run, which agrees with it by
# construction.  See `lateral_and_y_grid` for the measurement at
# T6-stathub-northcape (155, 506), where the nearest loser disagreed by
# 0.002 m and the branch standing 1.7 m above was never the runner-up.
#
# `separation` and `slide_limit` are in here TOGETHER: a claimant 20 m away
# and 1.7 m higher is a road going uphill, and one 0.7 m away and 1.7 m lower
# is a wall.  Neither distance alone nor height alone can tell them apart.
SECOND_CLAIMANT_DEPENDS_ON = ("winner_profile", "claimant_profile",
                              "separation", "slide_limit")


def lateral_and_y_grid(nodes: np.ndarray, prof: np.ndarray, br: np.ndarray,
                       X: np.ndarray, Z: np.ndarray, reach_m: float,
                       absorb_grade: float = BATTER_GRADE,
                       edge_m: float | None = None
                       ) -> tuple[np.ndarray, np.ndarray, np.ndarray,
                                  np.ndarray, np.ndarray, np.ndarray,
                                  np.ndarray, np.ndarray, np.ndarray]:
    """`lateral_and_y` for a whole lattice at once, and it is the SAME
    arithmetic -- clamped projection onto every terrain segment, nearest wins,
    profile interpolated at the clamped parameter.

    This exists because the batter made the scalar form unaffordable, not
    because vectorising is nice.  The scalar path is O(stations) per sample:
    T4 is 729 stations over a lattice that the widened reach grows from 20
    zones to ~40, i.e. 169,000 samples x 729 segments = 123 million Python
    iterations for ONE segment.  The same work as numpy row operations is 729
    vector ops per zone.  A bounding-box reject skips the segments that cannot
    reach this zone at all, which on a 1.4 km ribbon is nearly all of them.

    Returns `(lat, y, s, sgn, fx, fz, lat2, y2)`:
      lat  perpendicular distance to the polyline, +inf where nothing claims it
      y    the fitted profile height at the projected foot
      s    ARC LENGTH of that foot along the polyline, i.e. WHICH CROSS
           SECTION this sample belongs to
      sgn  WHICH SIDE of the road, from the sign of the cross product.  A
           cross section has two rays and they are independent: a bridge pile
           on the left must not clip the right-hand batter.
      fx,  the foot itself, so a caller can walk OUTWARD from the centreline
      fz   along this sample's own transverse ray
      lat2 the distance of the second claimant THAT THE GROUND BETWEEN THE
      y2   TWO CLAIMS CANNOT ABSORB, +inf where there is no such claimant,
           and that claimant's profile height at its own foot

    WHY THE SECOND CLAIMANT IS RETURNED AT ALL.  `nearest wins` makes the
    surface single-valued, which it has to be, and it also makes the OTHER
    claimant INVISIBLE -- and where a ribbon passes close to itself the other
    claimant is a piece of the same road at a different height.  The corridor
    rule already knows what to do about two carriageways inside one batter
    reach ("levelled FLUSH at the higher of the two"), but it is asked through
    `road_claim_at`, which reads the LIVE surface and returns None for
    `my_name`: a segment's own second branch cannot be seen by it, which is a
    key naming a SUBSET of "another carriageway inside this ray's reach".
    MEASURED, Fresh42, after the reach and the toe were repaired -- these are
    caused rim steps whose wall census reads `{}`:
      T6-stathub-northcape (92,583)/(92,582): arcs 485.041 and 455.645, 29.4 m
        apart along the polyline and ONE METRE apart on the ground, profiles
        58.1620 and 60.4627 -- 2.3007 m -- and a 2.2954 m caused step
      T3-temple-meadhall (-33,-131)/(-33,-130): arcs 120.691 and 107.647,
        feet 6.2713 m apart, profiles 0.3424 m apart, 0.9711 m caused step

    AND THE RUNNER-UP USED TO BE THE *NEAREST* LOSER, WHICH IS A KEY NAMING A
    SUPERSET OF WHAT THE QUANTITY DEPENDS ON -- the same class as every other
    defect in this file, found here by measuring what the counter it feeds
    actually reported.  The nearest loser at almost every cell is the WINNER'S
    OWN CONTIGUOUS RUN: at a polyline vertex both adjacent runs clamp to the
    same point, so they agree on the foot and on the profile by construction.
    MEASURED on Fresh42, `--validate`, 2026-09-24, at the cell that carries
    T6-stathub-northcape's worst caused rim step, (155, 506):
      nearest loser        lat2 4.455, y2 73.602 against y 73.604 -- 0.002 m
      the OTHER BRANCH     ~5.2 m away, y 75.311                 -- 1.707 m
    The counter therefore reported 46,982 cells "claimed by two branches" on
    T6 and 11 of them over the slide limit, while the branch that actually
    stands 1.7 m above this one was never the runner-up at all.

    SO THE RUNNER-UP IS NOW THE CLAIMANT THAT MOST EXCEEDS WHAT THE GROUND
    BETWEEN IT AND THE WINNER CAN ABSORB, which is the quantity the corridor
    rule is keyed on and needs no threshold of its own:

        exceedance = |y_win - y_k| - BATTER_GRADE * (d_k - lat_win)

    A contiguous run at a vertex has d_k == lat_win and y_k == y_win, so its
    exceedance is 0 and it drops out.  A run 20 m further along a straight
    road is 20 m away and 1.7 m higher, so `BATTER_GRADE * 20` absorbs it and
    it drops out too -- that is a road going uphill, not a step.  A return leg
    0.7 m away and 1.7 m lower does NOT drop out, and that is the hairpin.
    Only claimants with a POSITIVE exceedance are recorded, so `lat2` is +inf
    wherever the ribbon is not standing beside itself.

    So the pair is MEASURED and REPORTED here rather than left to be
    rediscovered from an empty cause list.

    THE STATION, THE SIDE AND THE FOOT ARE WHAT MAKE THE BATTER A RAY RATHER
    THAN A CLOUD OF INDEPENDENT SAMPLES, and that distinction is a measured
    defect: refusing
    batter samples one at a time cuts the taper MID-SLOPE and leaves the
    refused sample standing as a 1 m ridge with the graded ground dug away on
    both sides.  MEASURED on T12 at (28.0, -14.6): the step there went from
    3.138 m (the original wall) to 3.676 m (worse than the wall the batter was
    grading away).  A refusal has to end the whole ray outward of it, and
    "outward along which ray" is a question the lattice cannot answer without
    the foot and the station.
    """
    lat = np.full(X.shape, np.inf, dtype=np.float64)
    y = np.zeros(X.shape, dtype=np.float64)
    s = np.zeros(X.shape, dtype=np.float64)
    sgn = np.zeros(X.shape, dtype=np.int8)
    fx = np.zeros(X.shape, dtype=np.float64)
    fz = np.zeros(X.shape, dtype=np.float64)
    # THE RUNNER-UP IS RESOLVED IN A SECOND PASS, because the quantity it is
    # keyed on -- the exceedance against the WINNER -- is not known until the
    # winner is.  Resolving it inline made the answer depend on the order the
    # runs happen to be visited, which is a fact about the polyline's
    # indexing and not about the ground.
    lat2 = np.full(X.shape, np.inf, dtype=np.float64)
    y2 = np.zeros(X.shape, dtype=np.float64)
    xlo, xhi = float(X.min()) - reach_m, float(X.max()) + reach_m
    zlo, zhi = float(Z.min()) - reach_m, float(Z.max()) + reach_m
    # THE STATION COORDINATE, from the one accumulator both callers share.
    # It must be strictly increasing along the polyline or two feet share a
    # station and their two radials are walked as one ray.  See
    # STATION_ARC_DEPENDS_ON for the 23-member ray that measured it.
    arc = node_arcs(nodes, br)
    for k in range(len(nodes) - 1):
        if br[k] or br[k + 1]:
            continue
        ax, az = float(nodes[k][0]), float(nodes[k][1])
        bx, bz = float(nodes[k + 1][0]), float(nodes[k + 1][1])
        if (max(ax, bx) < xlo or min(ax, bx) > xhi
                or max(az, bz) < zlo or min(az, bz) > zhi):
            continue
        dx, dz = bx - ax, bz - az
        L2 = dx * dx + dz * dz
        if L2 <= 1e-12:
            continue
        seglen = math.sqrt(L2)
        tt = ((X - ax) * dx + (Z - az) * dz) / L2
        # THE SAME CONDITIONAL CLAMP THE SCALAR FORM USES, and it has to be
        # the same or the two answers diverge at exactly the banks this
        # exists for.  A withheld end does not clamp and does not claim:
        # `ok` drops the half-plane past it instead.
        lo_ok, hi_ok = clamp_ends(nodes, br, k)
        ok = None
        if not lo_ok:
            ok = tt >= 0.0
        if not hi_ok:
            hiok = tt <= 1.0
            ok = hiok if ok is None else (ok & hiok)
        np.clip(tt, 0.0, 1.0, out=tt)
        px, pz = ax + dx * tt, az + dz * tt
        d = np.hypot(X - px, Z - pz)
        m = d < lat if ok is None else ((d < lat) & ok)
        yk = (float(prof[k])
              + (float(prof[k + 1]) - float(prof[k])) * tt)
        if not m.any():
            continue
        lat[m] = d[m]
        y[m] = yk[m]
        s[m] = arc[k] + seglen * tt[m]
        # Cross product of the segment direction with the offset to the
        # sample: positive on one hand, negative on the other.  Zero exactly
        # on the centreline, where there is no ray to speak of.
        sgn[m] = np.sign(dx * (Z - az) - dz * (X - ax))[m].astype(np.int8)
        fx[m] = px[m]
        fz[m] = pz[m]
    # ---- second pass: THE CLAIMANT THIS GROUND CANNOT ABSORB -------------
    # Same runs, same clamp, same bounding-box reject; the only new work is
    # one comparison per run against the FINAL winner.  A cell keeps the
    # claimant with the LARGEST positive exceedance, and keeps none at all
    # where every other claimant fits -- which on a road that never comes
    # back on itself is every cell.
    # ---- ...AND THE FILL FLOOR THE SECOND CLAIMANT IMPOSES ---------------
    #
    # ONLY THE FLOOR, NEVER THE CEILING.  A ceiling arm -- holding the cell
    # DOWN to another run's `y_k + grade * run` -- was written and MEASURED
    # on Fresh42's 14 segments: it took the rim gate's caused count from 22
    # to 110 on T4-meadhall-stathub and 49 to 402 on T9-wttown-dockshore,
    # with 1,489 of T4's 2,977 moves being cuts of up to 3.4481 m into ground
    # no ray had asked to cut.  The floor BUTTRESSES a carriageway standing
    # above this cell, which is the defect; a ceiling digs the ground out
    # from under one.
    #
    # AND ONLY THE SECOND CLAIMANT'S FLOOR, NOT AN ENVELOPE OVER EVERY RUN.
    # The envelope was written too, and MEASURED on the same 14 segments: a
    # winding road that climbs has SOME run within 21 m of nearly every cell,
    # so `max_k (y_k - grade * (d_k - edge))` is a fill blanket -- T9's
    # caused went 49 -> 313, T13-temple-harboursouth went from PASS to
    # REFUSED, and `reach_not_closing` went from 1 to 372.  A rule that fires
    # on most of the lattice reports nothing, which is this file's oldest
    # lesson and was relearned here.
    #
    # THE THRESHOLD IS NOT A CLIFF, WHICH IS THE THING TO CHECK BEFORE
    # TRUSTING IT.  `lat2` is kept only where the exceedance
    # `|y - y_k| - grade * (d_k - lat)` is POSITIVE, and AT THAT THRESHOLD
    # the claimant's floor is EXACTLY this claim's own:
    #
    #   e = 0 and y_k > y  =>  y_k = y + grade * (d_k - lat)
    #   floor = y_k - grade * (d_k - edge) = y - grade * (lat - edge) = lo1
    #
    # so the constraint arrives non-binding and grows continuously with the
    # exceedance.  A claimant BELOW this one gives a floor under `lo1` and
    # never binds at all.  The raise is therefore continuous across the
    # threshold, which the envelope was bought to guarantee and did not need
    # to be.
    inband = np.isfinite(lat)
    floor = np.full(X.shape, -np.inf, dtype=np.float64)
    edge = (3.0 + SHOULDER_M) if edge_m is None else float(edge_m)
    if inband.any():
        exc = np.zeros(X.shape, dtype=np.float64)
        for k in range(len(nodes) - 1):
            if br[k] or br[k + 1]:
                continue
            ax, az = float(nodes[k][0]), float(nodes[k][1])
            bx, bz = float(nodes[k + 1][0]), float(nodes[k + 1][1])
            if (max(ax, bx) < xlo or min(ax, bx) > xhi
                    or max(az, bz) < zlo or min(az, bz) > zhi):
                continue
            dx, dz = bx - ax, bz - az
            L2 = dx * dx + dz * dz
            if L2 <= 1e-12:
                continue
            tt = ((X - ax) * dx + (Z - az) * dz) / L2
            lo_ok, hi_ok = clamp_ends(nodes, br, k)
            ok = None
            if not lo_ok:
                ok = tt >= 0.0
            if not hi_ok:
                hiok = tt <= 1.0
                ok = hiok if ok is None else (ok & hiok)
            np.clip(tt, 0.0, 1.0, out=tt)
            px, pz = ax + dx * tt, az + dz * tt
            d = np.hypot(X - px, Z - pz)
            yk = (float(prof[k])
                  + (float(prof[k + 1]) - float(prof[k])) * tt)
            # A LOSER, WITHIN REACH, THAT THIS GROUND CANNOT ABSORB.  `d >
            # lat` by a lattice ULP excludes the winner itself and excludes a
            # contiguous run clamped to the same vertex, which is the same
            # point and the same profile.  A run that may not claim this
            # ground may not be its second claimant either.
            near = inband & (d <= reach_m) & (d > lat + EDGE_EPS_M)
            if ok is not None:
                near = near & ok
            if not near.any():
                continue
            e = np.abs(y - yk) - absorb_grade * (d - lat)
            # THE HIGHEST FILL FLOOR ANY POSITIVE-EXCEEDANCE CLAIMANT
            # IMPOSES, AND IT IS A MAXIMUM AND NOT THE ARGMAX'S.  `lat2`
            # keeps ONE claimant -- the one that most exceeds what the ground
            # between can absorb -- and which one that is SWITCHES between
            # lattice-adjacent cells.  Keyed on the kept claimant's floor
            # alone the quantity jumps at every switch, and MEASURED on
            # Fresh42's 14 segments that cost T9-wttown-dockshore 49 -> 267
            # caused steps.  A maximum over all of them is continuous
            # wherever each of them is.
            #
            # AND EACH OF THEM IS.  A claimant enters this set at exceedance
            # 0, where `y_k = y + grade * (d_k - lat)` and its floor is
            # exactly `y - grade * (lat - edge)` -- THIS claim's own floor,
            # which the shipped batter already never writes below.  So every
            # term arrives non-binding and grows with its own exceedance, and
            # the maximum of grade-Lipschitz cones is grade-Lipschitz: the
            # same structural argument `roads/cone.py` rests on.
            # THE FLOOR IS ACCUMULATED BEFORE THE ARGMAX EARLY-OUT, because
            # a run that is not THE second claimant here can still be the one
            # whose floor binds -- the two are different questions and the
            # `continue` below answers only the first.
            hot = near & (e > 0.0)
            if hot.any():
                lo_k = yk - absorb_grade * np.maximum(d - edge, 0.0)
                np.maximum(floor, np.where(hot, lo_k, -np.inf), out=floor)
            take = near & (e > exc)
            if not take.any():
                continue
            exc[take] = e[take]
            lat2[take] = d[take]
            y2[take] = yk[take]
    return lat, y, s, sgn, fx, fz, lat2, y2, floor

# ---------------------------------------------------------------------------
# generated heights, on the compiler lattice
# ---------------------------------------------------------------------------

# THE SURFACE A ROAD DELTA IS COMPUTED AGAINST MUST BE THE ONE THE ENGINE
# DRAWS.  MEASURED by GroundingFix from the deployed assembly (md5
# 47df8869abdf95abedf859fce3deb022) and adopted here:
#
#   WorldGenerator::GetHeight(x,z) = GetBiomeHeight(GetBiome(x,z), x, z)
#                                    -- THE POINT'S OWN biome
#   HeightmapBuilder::Build        = per-vertex SmoothStep blend over THE
#                                    ZONE'S FOUR CORNER biomes, with a fast
#                                    path using corner b0 for the WHOLE zone
#                                    when all four corners agree
#
# They agree ONLY in a zone with no biome boundary, and `PatchScan` sampled
# `GetHeight` by default.  Validation, on 1,186 worldgen-placed trees and
# rocks standing on unwritten ground (a vegetation ZDO's y IS its ground
# contact point): residual against the HEIGHTMAP surface median -0.001,
# p95 +0.019, 8 over 1 m; against the WORLDGEN surface median -0.001 but
# min -10.516 and 43 over 1 m.  The world picks a side.
#
# A ribbon computes `levelDelta = target - generated`, so a wrong `generated`
# is a road written to a height the engine does not draw -- the road form of
# the floating watchtower at wt-peak.  `SURFACE=heightmap` is therefore NOT
# optional here and is not exposed as a parameter: there is no question a
# terrain delta can ask of the worldgen surface and get a usable answer.
PATCH_SURFACE = "heightmap"


def patch_cache_name(out: Path, surface: str, seed: str) -> str:
    """The patch-cache filename: keyed on the SURFACE **and** on the SEED.

    THE SURFACE KEY ALREADY EXISTED and the comment in `zone_patches` says
    why: a file written against the worldgen surface, reused for a heightmap
    delta, computes a road at a height the engine does not draw and REPORTS
    SUCCESS.  Making the seed a parameter opens the identical hole one axis
    over -- `z_-3_34` on Pirate68 and `z_-3_34` on a fresh seed are different
    ground under the same name -- so the seed joins the key.

    THE DEFAULT SEED KEEPS THE UNSUFFIXED NAME, deliberately.  Every file in
    `/tmp/roads/zonepatch` was written by Pirate68, so the unsuffixed name is
    now DEFINED to mean `worldseed.DEFAULT_SEED`; suffixing it would orphan
    correct data and buy a 20 s Unity boot per segment for nothing.  Any other
    seed gets its own name and therefore cannot read those files.  The mapping
    is injective either way -- `selftest_cache_name` proves it in both
    directions, including the direction that matters: two different non-default
    seeds never collide.
    """
    stem = f"{out.stem}.{surface}"
    if seed != worldseed.DEFAULT_SEED:
        stem = f"{stem}.seed-{re.sub(r'[^A-Za-z0-9_.-]', '_', seed)}"
    return f"{stem}{out.suffix}"


def selftest_cache_name(verbose: bool = True) -> dict:
    """PROVE THE CACHE KEY, BOTH DIRECTIONS.

    A key that ignored the seed entirely would pass a "default is unchanged"
    check; a key that always suffixed would pass a "seeds differ" check.  Both
    are asserted here, plus the surface axis the original comment defends.
    """
    p = SCRATCH / "N0-brgs2-wmid.bin"
    out = {
        "default": patch_cache_name(p, PATCH_SURFACE, worldseed.DEFAULT_SEED),
        "fresh": patch_cache_name(p, PATCH_SURFACE, "Freshling"),
        "fresh2": patch_cache_name(p, PATCH_SURFACE, "Otherseed"),
        "worldgen_default": patch_cache_name(p, "worldgen", worldseed.DEFAULT_SEED),
    }
    # (a) the default path still names the file that is on disk today
    assert out["default"] == "N0-brgs2-wmid.heightmap.bin", out["default"]
    # (b) a fresh seed CANNOT read it
    assert out["fresh"] != out["default"]
    # (c) two fresh seeds cannot read each other's
    assert out["fresh"] != out["fresh2"]
    # (d) the surface axis still separates
    assert out["worldgen_default"] != out["default"]
    # (e) a seed with path-hostile characters cannot escape the directory
    assert "/" not in patch_cache_name(p, PATCH_SURFACE, "../../etc/passwd")
    if verbose:
        for k, v in out.items():
            print(f"  {k}: {v}")
        print("ribbon patch-cache key selftest OK")
    return out


def zone_patches(zones: list[tuple[int, int]], seed: str, out: Path) -> dict:
    """PatchScan each zone at `half=32.5 step=1` centred on the zone centre.

    That request geometry is not arbitrary and not mine: it is `flatten.py`'s
    measured convention, and it is the ONLY one whose 65x65 lattice lands exactly
    on the zone's own TerrainComp sample centres (sample x = cx + (j - 32), an
    integer, because cx = zx*64).  The big routing fields are sampled on a
    HALF-integer lattice, which is fine for fitting a profile and wrong for
    computing a delta, so the deltas come from here.

    Sampled on the HEIGHTMAP surface -- see `PATCH_SURFACE` above.
    """
    import subprocess
    import os

    SCRATCH.mkdir(parents=True, exist_ok=True)
    # THE CACHE IS KEYED ON THE SURFACE, and that is the whole reason this
    # block was touched.  Every patch file written before the heightmap
    # correction holds WORLDGEN heights under a name that says nothing about
    # which surface produced it.  Reusing one here would silently compute a
    # ribbon's deltas against the wrong surface and report success -- the same
    # stale-output defect the SANDBOX comment below records, except that one
    # crashed with a KeyError and this one would not.  A cache that cannot
    # express what it holds must not be consulted across a semantic change.
    out = out.with_name(patch_cache_name(out, PATCH_SURFACE, seed))
    # Reuse a cached patch file when it already holds every zone asked for.
    # PatchScan boots a whole Unity process (~20 s) before sampling, so a
    # re-plan of the same segment paid that twice for bytes that cannot have
    # changed: the generated height of a zone is a pure function of the seed
    # AND of the surface sampled.
    if out.exists() and out.stat().st_size:
        try:
            sys.path.insert(0, str(JUMPSTART / "terraform"))
            import heights as _ph
            cached = _ph.load(out)
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
           "SEED": seed, "REQ": str(req), "OUT": str(out),
           # A PER-AGENT SANDBOX.  `/tmp/patchscan/vh` is shared and its
           # BepInEx tree is owned by root from an earlier run, so
           # `run_patchscan.sh`'s clean step fails with "Permission denied",
           # the scan never runs, and the STALE output file still exists and
           # still has non-zero size -- so the existence check passes and the
           # caller gets the previous run's zones.  MEASURED on T3: three
           # zones the widened batter needs, (1,-1), (3,5) and (5,5), came
           # back missing and `stamp` died on a KeyError.  A silent fallback
           # to old data is worse than a crash, so the sandbox is unshared and
           # the completeness check below is re-asserted after the scan.
           "SANDBOX": f"/tmp/patchscan/{ACTOR.lower()}",
           # THE RENDERED SURFACE, NOT THE POINT-BIOME ONE.  See PATCH_SURFACE.
           "SURFACE": PATCH_SURFACE}
    proc = subprocess.run(["bash", str(JUMPSTART / "blueprints" / "run_patchscan.sh")],
                          env=env, capture_output=True, text=True)
    if not out.exists() or out.stat().st_size == 0:
        raise SystemExit(f"patchscan produced nothing:\n{proc.stderr[-4000:]}")
    sys.path.insert(0, str(JUMPSTART / "terraform"))
    import heights as patchheights
    got = patchheights.load(out)
    missing = [(zx, zz) for zx, zz in zones if f"z_{zx}_{zz}" not in got]
    if missing:
        raise SystemExit(
            f"patchscan did not produce {len(missing)} requested zones "
            f"{missing[:8]}: the output file {out} exists and is non-empty, so "
            f"it is the PREVIOUS run's data. Refusing to rasterise a segment "
            f"against generated heights that do not cover it -- an unwritten "
            f"batter sample is the wall the batter exists to remove.\n"
            f"patchscan stderr tail:\n{proc.stderr[-2000:]}")
    return got


def generated_digest(patch) -> str:
    """sha256 of the zone's 65x65 GENERATED heights, little-endian float32,
    row-major with z ascending outer and x ascending inner -- exactly the order
    PatchScan wrote them.

    Stated because a digest without its byte order is worthless.  This is the
    field that lets a replay DETECT a worldgen change instead of silently
    putting the ground somewhere else: TCData deltas are relative to generated
    height, so the same deltas on different generated heights are a different
    road.
    """
    arr = np.asarray(patch.heights, dtype="<f4")
    return hashlib.sha256(arr.tobytes()).hexdigest()


def protected_piece_positions(zones: list[tuple[int, int]]) -> dict:
    """Every PIECE of every `flatten: FORBIDDEN` structure whose zone this
    segment touches, read out of the ledger BY THE GUARD'S OWN INSTRUMENT.

    THIS FUNCTION USED TO RECOVER THE PIECES ITSELF, AND IT COST A REFUSED
    WRITE AT APPEND TIME.  It walked each protected SITE's records, parsed
    `pos=<z>,<x>,<y>` out of `spawn_plan` blobs and read `pos` off `spawn` and
    `portal` records.  `writer.py::_flatten_forbidden_check` -- the guard that
    actually refuses -- walks the same ledger through
    `writer.py::_site_evidence`, which additionally honours declared
    `site_association` observations, the `role` fallback inside
    `_SITE_RADIUS_M`, and `retired_seqs`.  Two recoveries of one fact, and
    MEASURED on `N0b-wmid-wnorth` they disagreed: this function recovered
    1,381 pieces of `lh-north`, all of them the tower cluster around
    (-190, 2610), and the guard recovered the site-pad cluster around
    (-181.5, 2592) that this one never saw.  So the rasteriser refused nothing
    there, wrote the ground, and `Ledger.append` refused the whole 1,368.7 m
    leg for 38 pieces moved by 0.020 to 0.751 m -- after the zones had already
    been generated.

    A rasteriser that writes what the guard will refuse is not a rasteriser,
    and the fix is the same one the location boundary got: ONE implementation,
    and it lives with the guard.

    A protected record with NO recoverable piece and no disc is reported in
    `without_pieces`: the caller must treat that as unknown rather than clear.
    """
    sys.path.insert(0, str(JUMPSTART / "ledger"))
    from writer import (Ledger, _site_evidence, _site_key,      # noqa: PLC0415
                        keep_out_kind, retired_seqs)
    led = Ledger.open(WORLD, actor=ACTOR)
    zset = set(zones)
    pieces: list[tuple[float, float]] = []
    by_site: dict[str, int] = {}
    without: dict[str, list[int]] = {}
    # The structure's own recorded EXTENT, for the one question a piece list
    # cannot answer: is this bit of water the water a protected structure
    # stands over?  `expect.prefab_count` rows carry pos + max, which is the
    # radius Crossings itself verified the pieces inside.
    discs: list[tuple[float, float, float]] = []
    recs = led.records()
    # RETIRED STRUCTURES ARE NOT STANDING THERE, and the guard skips them --
    # so keeping out of them would reserve ground for a piece that no longer
    # exists while the guard permits it.  Same list, same exclusion.
    retired = retired_seqs(recs)
    for line, rec in enumerate(recs):
        p = rec["params"]
        # BOTH KEEP-OUT KINDS, AND THAT IS THE CONSERVATIVE DIRECTION.
        # `writer.KEEP_OUT_KINDS` now distinguishes `FORBIDDEN` (the ground
        # is the hazard) from `RESEAT` (a roadside lamp, whose claim is on
        # its own seating and which a `terrain_write` may discharge by
        # naming its seq).  This is the ROAD EMITTER'S keep-out, not the
        # guard's, and the rasteriser has no way to promise a re-seat -- so
        # it keeps out of both, exactly as it did when both read FORBIDDEN.
        # Testing the literal string here instead would have SILENTLY
        # DROPPED every lamp out of this set the moment the emitters started
        # writing RESEAT, which is a keep-out lost without anyone asking.
        if keep_out_kind(p) is None or rec["seq"] in retired:
            continue
        site_pieces, site_discs = _site_evidence(rec, recs, led)
        key = _site_key(p)
        site = (key[1] if key else None) or p.get("role") or f"line{line}"
        keep = [(float(x), float(z)) for x, z in site_pieces
                if tcdata.zone_of(float(x), float(z)) in zset]
        if keep:
            pieces += keep
            by_site[site] = by_site.get(site, 0) + len(keep)
        for dx, dz, r in site_discs:
            discs.append((float(dx), float(dz), float(r)))
        if not site_pieces and not site_discs:
            without.setdefault(site, []).append(line)
    seen = set()
    discs = [d for d in discs if not (d in seen or seen.add(d))]
    return {"pieces": pieces, "by_site": by_site, "discs": discs,
            "without_pieces": without,
            "recovery": "tools/jumpstart/ledger/writer.py::_site_evidence, "
                        "the same function writer.py::"
                        "_flatten_forbidden_check refuses with",
            "tool": "tools/jumpstart/roads/ribbon.py::protected_piece_positions"}


# ---------------------------------------------------------------------------
# the ribbon
# ---------------------------------------------------------------------------

# MEASURED ZoneSystem::c_WaterLevel.  A sample below it is seabed.
#
# THE HAZARD IS A DIRECTION, NOT A SIDE OF THIS NUMBER, and the comment that
# stood here said otherwise: "the one thing a terrain write must never do is
# lower ground out from under a structure that stands over water".  MEASURED
# by HarbourDredge, that is backwards -- LOWERING a bed ADDS water and RAISING
# it REMOVES water, with adjacent cells losing exactly 0.0000 at 1, 2, 4 and
# 8 m.  What must never happen is water being taken away from under a
# structure that stands over it, and the operation that does that is RAISING
# the bed, not cutting it.
#
# THIS CONSTANT IS THE RIGHT THRESHOLD AND IT WAS NEVER THE WHOLE KEY.  The
# text that stood here read "the batter's geometry only cuts downward from a
# carriageway edge above the plane, so a batter sample below the plane is a
# bank being taken down to the waterline", and it is MEASURED FALSE.  A batter
# descends from the ROAD, not from natural ground: it writes
# `delta = target - generated` and STOPS at the sign flip, so every sample it
# writes on a leg in FILL is ground going UP.  MEASURED on
# T1-portalhub-temple, Fresh42, 2026-09-24: of the 70 batter samples this gate
# refused as `water_edge`, 70 have `full_edge > 0` and ZERO have
# `full_edge < 0` -- every one of them a CAUSEWAY FLANK, not a bank being cut.
# The carriageway across the same water is permitted (its rule is scoped to
# protected structures, see `stamp`'s docstring and the W5/W10 causeway
# measurement), so the blanket batter rule left the flank of a sanctioned
# causeway standing as a vertical face: 5.132 m at (-105, -274) at lat 4.03 m,
# which is the 5.1433 m step `junction.rim_transfer` refused the whole write
# for.  The key named a SUPERSET of what the hazard depends on.
#
# So the gate below is keyed on the DIRECTION the earthwork moves the ground,
# which is what the hazard has always been about, and the threshold is
# unchanged: never take a bank DOWN at or below the waterline.  Raising a
# submerged sample is still judged -- by `water_sign`, on the write as a
# whole, as a delta -- and by the `underwater` and `placed` gates for anything
# standing in that water.  This gate is not those gates and must not
# impersonate them.
WATER_LEVEL_M = 30.0
# The set of samples that can move the ground at a protected piece is exactly
# the four the game blends around it, i.e. the ones within one lattice pitch on
# each axis.  Excluding that set by CHEBYSHEV distance makes `delta_at` at the
# piece 0.0 in float rather than merely small -- there is no tolerance to argue
# about afterwards.
PROTECT_CLEAR_M = 1.0
# The same Chebyshev set around a LIVE placed object.  MEASURED by SeatCheck
# tonight: the hub-end portal `u-lheast` at (-307.5, 223.5) floats 0.536 m
# because RoadBuild's ribbon cut the ground under it AFTER it was seated.  The
# batter is new ground movement over ground that already carries seated
# objects, so it is gated on the objects themselves rather than on a radius.
PLACED_CLEAR_M = 1.0
#
# BEFORE YOU TIGHTEN EITHER OF THESE TO THE PIECE'S OWN SOLID: 6,956 of the
# 11,972 non-render colliders in `blueprints/data/piece_geometry.json` are a
# MeshCollider's `sharedMesh.bounds`, which is an UPPER BOUND on the shape and
# not the shape.  Both gates above compare a sample against a PIVOT at
# Chebyshev 1.0 m, so neither reads that geometry and neither inherits the
# error - MEASURED: every use site here (`_reason`, `near_protected`) takes
# `(px, pz)` out of the ledger's own piece list.  A version that asked the
# corpus for the footprint instead WOULD inherit it: `Beech1` carries two mesh
# rows and no capsule at all, so its solid is a 19.08 x 21.50 m prefab-local
# box - 31.7 x 32.0 m once rotated - and on that reading a protected piece
# would forbid ground 15 m away from itself.  The spawn section's rule is the
# one to copy: judge on `Solid.tight_hull()` / the parts whose
# `instruments.part_kind` is in `TIGHT_KINDS`, re-run with the bounds dropped,
# and report a verdict that only the bounding box produced as ADVISORY rather
# than deciding the world on it.


# WHAT THE BATTER'S PLACED-OBJECT GATE ACTUALLY DEPENDS ON.  It used to depend
# on the POSITION alone.
PLACED_GATE_DEPENDS_ON = ("object_position", "object_prefab")
_CLEAR_MOD = None


def batter_freezes_on(prefab: str | None) -> bool:
    """Does a live object of this prefab freeze the ground under it?

    THE UNION THAT MADE TWO QUESTIONS ONE.  `clear.placed_objects` keeps every
    ZDO in the corridor that is "either not on CLEARABLE or outside the
    measured clear width", and its docstring says the point is that "one live
    measurement answers both 'may I delete this' and 'may I move the ground
    under it', and the two answers cannot disagree".  They are DIFFERENT
    QUESTIONS and the union is a SUPERSET of the second one's population.

    MEASURED on Fresh42's corridors, 2026-09-24: 11,814 kept objects over 54
    prefabs -- 296 of them (2.5%) property, POI content or structure
    (`woodwall`, `wood_floor`, `LocationProxy`, `RockDolmen_*`,
    `TreasureChest_*`, `Spawner_*`, `rock4_copper`, the music emitters), and
    11,518 of them (97.5%) CLEARABLE vegetation -- 1,715 `Beech_small2`, 1,201
    `Pickable_Stone`, 410 `Pickable_Dandelion`.  A prefab this project deletes
    by the thousand INSIDE the clear width cannot simultaneously be a prefab
    whose ground may not move OUTSIDE it: the only thing that changed is which
    side of a line it stands on, and that is a fact about the ROAD, not about
    the object.  MEASURED cost of the superset on T1-portalhub-temple with the
    water and advance keys already corrected: 292 rim steps and a 3.6459 m
    worst against 69 and 2.4128 m over an empty placed population -- 223 of
    the 292, 76%, charged to vegetation.

    The hazard this gate was derived from is a SEATED PIECE: portal `u-lheast`
    at (-307.5, 223.5) floats 0.536 m because a ribbon cut under it after it
    was seated.  That is a structure, and `clear.blocks` -- default-deny on
    any unknown name, so a mod POI's ordinary building prefabs are kept -- is
    exactly the set that contains it.  A tree is not seated by anything and a
    floating beech is not a defect a walker can fall into.

    An object with NO prefab recorded freezes the ground: UNREAD IS NOT CLEAR.
    """
    global _CLEAR_MOD
    if not prefab:
        return True
    if _CLEAR_MOD is None:
        # LAZY AND MEMOISED.  `clear.py` imports THIS module at its own top,
        # so a module-scope import here is circular; and this is asked once
        # per live object -- 11,814 of them on Fresh42 -- so it must not be an
        # `import` statement per call.
        sys.path.insert(0, str(HERE))
        import clear as clearmod  # noqa: PLC0415
        _CLEAR_MOD = clearmod
    return _CLEAR_MOD.blocks(prefab)


# WHAT THE BATTER'S WATER GATE DEPENDS ON -- IMPORTED, NOT RESTATED.  The rule
# now lives ONCE, in `route.batter_water_rule`, beside `ford_rule` and
# `footprint_wet`: the router reserves the footprint this writer lays down and
# the two may not hold different opinions about where the water is.  A second
# copy of the tuple here would be a declaration that can go stale against the
# rule it declares.
BATTER_WATER_GATE_DEPENDS_ON = routemod.BATTER_WATER_DEPENDS_ON


def batter_water_gate(generated: float, delta: float) -> str | None:
    """NEVER TAKE A BANK DOWN AT OR BELOW THE WATERLINE.  `"water_edge"` or None.

    `delta` is THIS sample's own `target - generated`, not the ray's intent
    and not the road's: a batter in cut lowers every sample it writes, a
    batter in fill raises every sample it writes, and the two have opposite
    hazards over the same submerged ground.

    LOWERING a submerged sample takes the bank down to the waterline beside
    standing water -- water removed from the shoreward side of whatever stands
    in it, the early-dock defect, the one unrepairable shape.  Refused.

    RAISING one is a flank, and WHAT HAPPENS TO IT NOW DEPENDS ON WHERE IT
    ENDS -- which is the term this gate's own declaration used to refuse to
    name.  A toe that lifts a shoal but stays under the plane removes no
    water and is laid; a toe that lifts the bed TO OR ABOVE the plane is the
    bay filled in to make a taper, and the generated field is the only record
    the water was ever there.

    THE PERMIT THAT USED TO STAND HERE WAS PROTECTING AN EMPTY SET, and that
    is the measurement that retired it rather than an opinion.  Its argument
    was that refusing the raise "leaves the causeway's whole flank standing as
    a cliff" -- true of a world that still had causeways in it.  MEASURED by
    `Causeway` over all fourteen Fresh42 segments, footprint minimum from
    `network.transverse_window`: 158 stations have their LEVELLED FOOTPRINT
    below the 30 m plane and 0 OF 158 ARE UNBRIDGED.  A bridged station writes
    no carriageway and `stamp` writes no batter where there is no road, so the
    causeway flank this permit existed for is not reachable from any plan this
    router emits.

    AND THE COST OF THE PERMIT, MEASURED THE SAME WAY, `ribbon.stamp` over the
    cached zone patches, all 14 segments, before -> after the delegation:

      seed-submerged samples RAISED to/above the plane   750 -> 0
      carriageway paved + shoulder                       UNCHANGED, 14 of 14
      batter samples                                45,106 -> 44,025  (-2.4 %)
      batter still laid on submerged ground, under plane   423 -> 201
      `water_fill` clips                                     0 -> 530
      walls recorded at those clips                              419
        of those 419, standing on SEED-SUBMERGED ground          419
                      standing on land                            0

    Every wall the new arm leaves is BELOW THE WATER SURFACE: it is a
    shoreline, not a cliff a player walks into, which is the difference from
    the 2026-09-24 experiment recorded above.

    Lives at module scope, like `water_sign`, so the selftest drives THE
    SHIPPING RULE rather than a copy of it -- and the shipping rule is now
    `route.batter_water_rule`, which this delegates to unchanged.
    """
    return routemod.batter_water_rule(generated, delta)


# WHAT "THE TAPER IS FINISHED HERE" ACTUALLY DEPENDS ON: this sample's own
# plane against this sample's own ground, and the DIRECTION of the earthwork.
# Module level rather than inline so the two places that ask it -- the walk
# over a ray's bin members and the walk along its radial past them -- ask the
# SAME rule, and so a selftest can neuter the shipping rule instead of a copy.
BATTER_MET_GROUND_DEPENDS_ON = ("this_sample_delta", "earthwork_direction")


def batter_met_ground(delta: float, sign: float) -> bool:
    """Is the graded plane AT OR BELOW natural ground AT THIS SAMPLE?

    `delta` is target minus generated; `sign` is +1 for fill and -1 for cut.
    Nothing may be written where this is true: the plane is underground here
    and writing it carves a disconnected trench in ground the taper has
    already finished with -- 530 samples on `selftest_batter_ray_end`'s
    neuter B.

    IT IS A FACT ABOUT ONE SAMPLE AND IT IS NOT THE TOE.  This predicate used
    to END the ray, on the stated premise that "past the crossing the plane
    is UNDERGROUND on this ray".  Natural ground is not monotone and one
    dimple is enough to make that false.  MEASURED on T3-temple-meadhall,
    Fresh42, 2026-09-24, with the reach nowhere near spent: ray (+1, arc 400)
    ended at lat 7.990 on this exit and the plane is +0.3533 m ABOVE ground
    0.707 m further out on the same radial; ray (-1, arc 327) ended at lat
    8.273 and the plane is +0.2907 m above ground at lat 8.980.  Those two
    unwritten cells are 0.8892 m and 0.8426 m rim steps, on a segment whose
    wall census reads `{}` -- the batter's SUCCESS exit taken over ground it
    had not finished, leaving no trace at all.

    So the caller SKIPS a sample this is true of and keeps walking; the toe
    is the last lateral on the radial where the plane was above ground, and
    the ray ends at a gate, the budget, the reach or the cover.
    """
    return delta * sign <= 0.0


# WHAT A BATTER SAMPLE'S TARGET HEIGHT ACTUALLY DEPENDS ON, AND `y - sign *
# grade * run` NAMES A SUBSET OF IT: IT NAMES ONE CLAIM.
#
# THE BATTER IS A ONE-DIMENSIONAL GUARANTEE AND THE RIM GATE IS A
# TWO-DIMENSIONAL ADJACENCY.  A ray closes ALONG its own radial -- every
# sample is within `BATTER_GRADE * run` of its own carriageway edge, which is
# exactly the walker's slide limit -- and NOTHING WHATEVER BOUNDS THE STEP
# ACROSS RAYS.  Where a ribbon passes close to itself, two lattice-adjacent
# cells belong to feet 9 to 30 m apart along the polyline, they carry the
# FULL PROFILE DIFFERENCE between those feet, and BOTH RAYS RECORD SUCCESS.
#
# THE RULE THE BATTER IS ALREADY OBEYING, WRITTEN OUT.  `batter_met_ground`
# stops a fill when the descending plane reaches ground and stops a cut when
# the rising plane reaches it, so what the pair of them computes is
#
#     written = clamp(ground, y - grade * run, y + grade * run)
#
# -- the ground, held inside a BAND around this claim's road edge.  The band
# is the whole content of the guarantee: a walker leaving the carriageway at
# `y` and travelling `run` metres cannot be asked to climb or fall more than
# `grade * run`.
#
# A CELL INSIDE TWO CLAIMS' REACH OWES THAT GUARANTEE TO BOTH OF THEM, and
# the surface is single-valued, so the admissible set is the INTERSECTION of
# the two bands.  `nearest wins` keys the target on one of them and the other
# becomes invisible -- the same defect class as every other one in this file,
# and the one the `shared_corridor` population has been naming without being
# able to act on it.
#
# THE INFEASIBILITY IS A ROUTING FACT AND HAS NO GROUND IN IT:
#
#     unabsorbable = |y1 - y2| - grade * (run1 + run2)
#
# Two carriageways at the same height, or far enough apart that the grade
# spans the difference, give zero.  A hairpin whose return leg stands 1.7 m
# lower one metre away gives a positive number, and THAT is a cell no
# earthwork can reconcile without cutting ground one of the two roads must
# keep.  It is REFUSED BY NAME with that number rather than written from one
# claim and left as a face the wall census never mentions.
# `the_natural_ground` is in here and `batter_grade` is not, and the choice is
# a measurement: the grade only ever appears as `grade * run`, so a neuter of
# it is indistinguishable from the neuter of the run and a term whose neuter
# duplicates another's proves nothing on its own.  The GROUND does separate:
# keyed on the PLANE instead of the ground the rule declines to write a cell
# whose ground stands above the other claim's ceiling, takes `met_ground` --
# the SUCCESS exit -- and leaves the face.  See `selftest_ribbon_beside_
# itself`, whose constructed table is the only place that case is reachable.
BATTER_TARGET_DEPENDS_ON = ("every_other_run_within_reach",
                            "the_lateral_run_to_each_of_them",
                            "this_claim_own_ceiling")


def claim_band(y_claim: float, lat_claim: float, edge_m: float,
               grade: float) -> tuple[float, float]:
    """The heights this cell may stand at and still be reachable from ONE
    carriageway edge at `grade`.  `run` is clamped at the edge: inside the
    carriageway the band collapses to the road surface itself, which is what
    a second claimant that PAVES this cell is entitled to."""
    run = max(lat_claim - edge_m, 0.0)
    return y_claim - grade * run, y_claim + grade * run


def reconcile_target(target: float, generated: float, sign: float,
                     y: float, lat: float, floor: float,
                     edge_m: float, grade: float
                     ) -> tuple[float, float, float]:
    """`(target, earthwork_sign, unabsorbable_m)` for one batter sample.

    `floor` is the highest fill floor any OTHER run of this polyline within
    reach imposes here -- `max_k (y_k - grade * run_k)`, a maximum of
    grade-Lipschitz cones and therefore grade-Lipschitz itself.  `-inf` where
    no other run reaches, and then THIS IS THE IDENTITY: the shipped
    one-claim answer and the ray's own sign come back untouched.  On a road
    that never comes back on itself that is every cell, which is what makes
    the repair measurable against the 14-segment table.

    WHAT THE RULE IS.  The shipped batter computes, through
    `batter_met_ground`, `clamp(generated, lo1, hi1)` -- the ground held
    inside THIS claim's band.  The surface is single-valued, so a cell inside
    another run's reach owes that run the same guarantee, and the shipped
    answer is therefore HELD AT OR ABOVE its floor:

        written = max(shipped, floor)

    A RAISE ONLY, AND THE ASYMMETRY IS MEASURED, NOT AESTHETIC.  The matching
    ceiling arm -- cutting a cell down to another run's `y_k + grade * run` --
    ran on Fresh42's 14 segments and took the rim gate's caused count from 22
    to 110 on T4-meadhall-stathub and 49 to 402 on T9-wttown-dockshore, with
    1,489 of T4's 2,977 moves being cuts of up to 3.4481 m into ground no ray
    had asked to cut.  The floor is the direction that BUTTRESSES a
    carriageway standing above this cell, which is the defect; the ceiling
    digs the ground out from under one.

    THE RETURNED SIGN IS THE DIRECTION OF THIS SAMPLE'S OWN MOVE, not the
    ray's.  A raise on a CUT ray read against the ray's sign is `met_ground`,
    the batter's SUCCESS exit, and the repair would delete itself in silence
    -- which is the exact failure mode it exists to remove.

    `unabsorbable_m` is positive when the floor stands ABOVE this claim's own
    ceiling: no height exists that both runs can live with, and the caller
    must refuse the cell by name rather than pick a side.  See
    BATTER_TARGET_DEPENDS_ON.
    """
    if not (floor > -math.inf):
        return target, sign, 0.0
    lo1, hi1 = claim_band(y, lat, edge_m, grade)
    if floor > hi1:
        return target, sign, floor - hi1
    # A SIXTH VARIANT WAS TRIED HERE AND REMOVED BY MEASUREMENT: restricting
    # the raise to samples the shipped batter WRITES NOTHING AT -- the rays
    # that exited on `met_ground` -- on the premise that a ray still grading
    # is already closing its own face.  It closed NOTHING on the wide-hairpin
    # fixture (79 CAUSED edges either way, 19 raises against 64) and it was
    # still worse on the corpus than the shipped batter (T9-wttown-dockshore
    # 288 against 49).  The premise is false: the face a raise closes is
    # usually beside a sample the ray DID write.
    shipped = generated if batter_met_ground(target - generated, sign) else target
    if shipped >= floor:
        return target, sign, 0.0
    return floor, 1.0, 0.0


def _count_reconciled(st: dict, plane: float, target: float,
                      generated: float, sign: float, sign_s: float) -> None:
    """Record that the joint band changed WHAT THIS SAMPLE LEAVES ON THE
    GROUND, which is not the same question as "did the target move".

    WHERE THE GROUND ALREADY STANDS INSIDE THE JOINT BAND the reconciled
    target IS the ground: it differs from the one-claim plane by whatever the
    plane had descended past it -- metres, out at the reach -- and it writes
    NOTHING, because that is `met_ground` restated.  Counting the target move
    reported 475 reconciliations and a 14.734 m "worst move" on the
    wide-hairpin fixture whose real, written population is the one this
    function counts.  A counter that cannot tell a write from a skip is the
    same defect as a key naming a superset, and it was caught here by
    measuring the fixture before trusting the number.

    Both arms are evaluated through `batter_met_ground`, the SHIPPING
    predicate, so the before-state is the file's own answer and not a
    restatement of it.
    """
    was = generated if batter_met_ground(plane - generated, sign) else plane
    now = generated if batter_met_ground(target - generated, sign_s) else target
    moved = now - was
    if abs(moved) <= EDGE_EPS_M:
        return
    st["self_corridor_reconciled"] += 1
    st["self_corridor_reconciled_raised" if moved > 0.0
       else "self_corridor_reconciled_lowered"] += 1
    if abs(moved) > st["self_corridor_reconciled_worst_m"]:
        st["self_corridor_reconciled_worst_m"] = round(abs(moved), 4)


# WHAT THE HEIGHT OF THE LAST BATTER SAMPLE ACTUALLY DEPENDS ON, AND THE
# PLANE ALONE NAMES A SUBSET OF IT.
#
# Every sample on a ray is written at `y - grade * run`: a function of the
# ray, and of nothing else.  The thing a walker meets, though, is a 1 m
# LATTICE EDGE between that sample and whatever stands beside it -- and
# beside the last one stands NATURAL GROUND, because the taper crossed it
# somewhere in the gap.  The plane's own fall over one lattice edge is at
# most `BATTER_GRADE` = tan(38 deg), which IS the walker's slide limit, so
# the taper spends the ENTIRE step budget on its own descent and any fall in
# the ground it lands on puts the pair over.
#
# MEASURED, Fresh42, 14 segments, offline `--validate`, 2026-09-24, after the
# station arc was made injective: 40 of the 163 caused rim steps are this and
# nothing else, and in every one of them the two parts ADD:
#   T3 (-141,-342)->(-140,-342)  write moved 0.130 m, ground fell 0.712 m,
#                                step 0.842 m -- 13 cm of earthwork charged
#                                with an 0.842 m cliff
#   S4 (-104,-331)->(-103,-331)  moved 0.155, ground 0.729, step 0.884
#   T1 (-131,-306)->(-132,-306)  moved 0.543, ground 0.331, step 0.874
# The gate is not wrong about the world -- the step IS there -- but its
# message says "the fix is in whatever clipped the batter" and NOTHING
# clipped it: the census reads `{}` and the ray exited on `met_ground`.
#
# SO THE FIX IS IN THE GROUND, NOT IN THE VERDICT.  Reclassifying these as
# somebody else's would be narrowing the check to make it pass.  A real
# earthwork does not end a fill in a vertical face; it FEATHERS the toe.
#
# AND THE FEATHER IS ALWAYS AFFORDABLE WHERE THE STEP IS CAUSED, which is
# what makes this a repair rather than a trade: `rim_transfer` calls a pair
# CAUSED only when it was INSIDE the limit before the write, i.e. when the
# ground's own step is already within the limit -- so laying the sample back
# down onto natural ground always satisfies it.  Where the ground itself is
# over the limit the pair is PRE-EXISTING, the feather is not asked for, and
# the terrain keeps the cliff it was drawn with.
TOE_FEATHER_DEPENDS_ON = ("the_graded_plane",
                          "the_natural_ground_at_every_lattice_NEIGHBOUR",
                          "which_of_those_neighbours_this_write_leaves_alone")


# WHICH WAY A TOE SAMPLE MOVES WHEN ITS NEIGHBOURS DISAGREE, AND THE SHIPPED
# RULE KEYED IT ON NOTHING.  `min(max(h, lo), hi)` answers a conflict --
# `lo > hi`, no height every neighbour accepts -- with `hi`, ALWAYS THE LOWER
# BOUND.  For a FILL that is toward the ground and the relaxation cascades
# inward exactly as the docstring promises.  For a CUT it is AWAY from the
# ground: the clamp into `[h, g]` then holds the sample at `hi`, no neighbour
# is ever re-queued, and the relaxation stops with the step still standing.
#
# MEASURED, Fresh42, T7-stathub-stenvik, offline `--validate`, 2026-09-25:
# the worst caused rim step, 1.2799 m at rim (557,-5) from taken (556,-5), is
# TWO BRANCHES OF ONE ROAD, both in cut.  The taken cell is ray (-1, arc 1183,
# bearing 26) off a road at 42.793, plane 45.325 over ground 46.351; the rim
# cell is ray (+1, arc 1154, bearing 71) off the SAME polyline 29 m of arc
# away at 44.376, plane 46.888 over ground 46.896 -- moved 0.008 m, inside
# AUTHOR_TOLERANCE, so it is rim.  The feather asked for `lo` 46.107 from the
# rim and `hi` 45.608 from its own inboard neighbours (556,-6) and (555,-5),
# answered 45.608, and neither neighbour was ever in violation, so neither
# was ever raised.  0.5448 m of the step is the ground's own; the other
# 0.7351 m is cut the feather was entitled to give back and could not.
#
# THE SAME CONFLICT ANSWERED THE OTHER WAY MOVES A CLIFF, which is the second
# half of this key.  On a FILL whose outboard cell is ground this write may
# not touch -- MEASURED, S13-stathub-dock (648,804): road 32.77, plane
# 32.068, outboard (647,804) submerged at 29.57 that `water_fill` refuses --
# `hi` laid the toe down onto natural 30.364 and the 2.41 m face moved from
# the water's edge ONTO THE CARRIAGEWAY EDGE.  And where the laid-back toe
# stops a centimetre or more above its ground, BOTH sides of that face are
# samples this write moves -- an INTERIOR pair, which `rim_transfer` never
# reads.  MEASURED, S13, every 1 m edge of the write under the CAUSED rule:
# shipped 54 interior over-limit edges (worst 2.4069 m) and 4 at the rim, so
# the gate saw 16 steps; with this key 8 interior and 43 at the rim, where
# the gate sees them.  On `_shore_ground` -- S13 in miniature -- the shipped
# rule leaves 134 faces of 1.6374 m at the carriageway edge and the rim gate
# reads CLEAR.  So a conflict moves a sample toward its ground ONLY AS FAR
# AS THE GROUND THAT CANNOT FOLLOW LETS IT: `feather_envelopes` bounds every
# sample by every chain to a cell this write leaves alone or cannot move.
#
# AND AN EDGE THE GROUND ALREADY HAD OVER THE LIMIT DOES NOT BIND, because
# `junction.rim_transfer` calls that pair PRE-EXISTING whatever this write
# leaves there.  Binding on it manufactures conflicts the gate does not
# judge: a toe beside a 4 m knoll was pinned high by the knoll and left a
# CAUSED face on its other side.
FEATHER_CONFLICT_DEPENDS_ON = (
    "this_sample's_own_earthwork_direction",
    "how_far_the_ground_that_cannot_follow_lets_it_move",
    "whether_the_edge_was_already_over_before_this_write")


def feather_envelopes(movable: dict, pinned_at, binds,
                      limit_m: float) -> tuple[dict, dict]:
    """`(ceiling, floor)` per movable sample: how far toward its ground a
    sample may move and still leave a chain every other sample can follow.

    `movable` maps a sample to its natural ground; every movable sample can
    end anywhere between its plane and that ground, so its ground is the
    CONSERVATIVE bound it offers a neighbour in both directions.  A sample
    this write leaves alone is a PIN at its own height.  The ceiling is
    `min(bound_x + L * d)` and the floor `max(bound_x - L * d)` over every
    sample `x` reachable through movable samples along binding edges -- a
    multi-source shortest path, so both are exact rather than iterated.
    """
    ceil: dict[tuple[int, int], float] = {}
    floor: dict[tuple[int, int], float] = {}
    for c, g in movable.items():
        u, d = g, g
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (c[0] + dx, c[1] + dz)
            if n in movable or not binds(c, n):
                continue
            v = pinned_at(n)
            if v is None:
                continue
            u = min(u, v + limit_m)
            d = max(d, v - limit_m)
        ceil[c], floor[c] = u, d
    for env, better in ((ceil, 1.0), (floor, -1.0)):
        heap = [(better * v, c) for c, v in env.items()]
        heapq.heapify(heap)
        while heap:
            key, c = heapq.heappop(heap)
            v = better * key
            if v != env[c]:
                continue
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                n = (c[0] + dx, c[1] + dz)
                if n not in movable or not binds(c, n):
                    continue
                cand = v + better * limit_m
                if better * cand < better * env[n]:
                    env[n] = cand
                    heapq.heappush(heap, (better * cand, n))
    return ceil, floor


def feather_toe(comps: dict, patches: dict, batter_at: dict,
                road_cells: set, st: dict, limit_m: float,
                natural_at=None,
                before_at=None, fixed_at=None,
                resolves_toward_ground: bool = True,
                pins_hold: bool = True,
                binds_on_preexisting: bool = False) -> dict:
    """Lay the batter's outermost samples back down until every 1 m lattice
    edge out of this write is one a walker can take.

    THE ONLY DIRECTION IS TOWARD NATURAL GROUND.  A sample may be lowered
    (fill) or raised (cut) only as far as the ground it stands on, never
    past it and never further from it: a feather REMOVES earth from a toe,
    and a rule that could add earth to satisfy a neighbour would move the
    cliff instead of closing it -- the restitution defect `rim_transfer`'s
    own docstring was written about.

    THE CARRIAGEWAY IS NOT FEATHERED.  It is the road surface; bending it to
    meet a hillside is not a repair, it is a hole in the road.  A step from
    the carriageway edge is the wall the batter exists to grade, and if the
    batter could not reach it that is a `reach` clip with its own name.

    RELAXATION, NOT A SINGLE PASS, because lowering one toe opens a step to
    the sample inboard of it.  Each update strictly reduces `|height -
    natural|`, which is bounded below by zero, so the sweep terminates; the
    worklist carries only the neighbours of a sample that actually moved.

    ORTHOGONAL NEIGHBOURS ARE ENOUGH, and that is the gate's own rule rather
    than an economy: `junction.rim_transfer` judges a diagonal as TWO 1 m
    edges through the easier corner, so a surface whose every orthogonal edge
    is within the limit satisfies the diagonal rule by construction.

    `before_at` is the surface BEFORE this write, the rim gate's own
    `height_now`: natural ground where nobody has written, another write's
    ground where somebody has.  Absent, it is `natural_at` -- the rule that
    shipped, which judged a toe beside another road's batter against ground
    that road had already moved.  See FOREIGN_BATTER_DEPENDS_ON.
    """
    if natural_at is None:
        def natural_at(sx: int, sz: int):
            return appliedmod.generated_at(patches, float(sx), float(sz))
    if before_at is None:
        before_at = natural_at
    out = {"feathered": 0, "worst_m": 0.0, "worst_at": None,
           "earth_given_back_m": 0.0, "passes": 0}
    if not batter_at:
        st["toe_feather"] = out
        return out
    height: dict[tuple[int, int], float] = {}
    natural: dict[tuple[int, int], float] = {}
    for c, (zx, zz, gx, gy, gen) in batter_at.items():
        comp = comps.get((zx, zz))
        if comp is None:
            continue
        k = gy * tcdata.PITCH + gx
        if not comp.modified_height[k]:
            continue
        height[c] = gen + comp.level_delta[k]
        b = before_at(c[0], c[1])
        natural[c] = gen if b is None else b
    fixed: dict[tuple[int, int], float | None] = {}

    def at(c):
        """The height beside a batter sample: its own if this write moves it,
        the surface otherwise.  NONE IS NOT A HEIGHT -- an unread neighbour
        constrains nothing and is counted, never assumed clear."""
        h = height.get(c)
        if h is not None:
            return h
        if c in fixed:
            return fixed[c]
        if fixed_at is not None:
            fixed_y = fixed_at(c[0], c[1])
            if fixed_y is not None:
                fixed[c] = fixed_y
                return fixed_y
        g = natural_at(c[0], c[1])
        if g is not None and c in road_cells:
            zx, zz = tcdata.zone_of(c[0], c[1])
            comp = comps.get((zx, zz))
            if comp is not None:
                cx, cz = comp.centre
                gx, gy = tcdata.vertex_mask_index(cx, cz, c[0], c[1])
                if 0 <= gx < tcdata.PITCH and 0 <= gy < tcdata.PITCH:
                    k = gy * tcdata.PITCH + gx
                    if comp.modified_height[k]:
                        g = g + comp.level_delta[k]
        elif g is not None:
            g = before_at(c[0], c[1])
        fixed[c] = g
        return g

    ground: dict[tuple[int, int], float | None] = dict(natural)

    def ground_of(c):
        """The surface BEFORE this write -- what `rim_transfer` reads as the
        pair's `before`: `before_at`, for every sample."""
        if c not in ground:
            ground[c] = before_at(c[0], c[1])
        return ground[c]

    def binds(c, n) -> bool:
        """Does the edge c-n constrain the feather?  Not when the ground
        already had it over the limit: see FEATHER_CONFLICT_DEPENDS_ON."""
        if binds_on_preexisting:
            return True
        if fixed_at is not None and (fixed_at(*c) is not None
                                     or fixed_at(*n) is not None):
            return True
        gc, gn = ground_of(c), ground_of(n)
        if gc is None or gn is None:
            return True
        return abs(gc - gn) <= limit_m + EDGE_EPS_M

    ceil_at, floor_at_ = ({}, {})
    if resolves_toward_ground and pins_hold:
        ceil_at, floor_at_ = feather_envelopes(natural, at, binds, limit_m)
    out["conflicts"] = 0
    out["conflicts_held_by_ground_that_cannot_follow"] = 0
    work = list(height)
    seen = set(work)
    moved_total = 0.0
    while work:
        out["passes"] += 1
        c = work.pop()
        seen.discard(c)
        h, g = height[c], natural[c]
        lo, hi = -math.inf, math.inf
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (c[0] + dx, c[1] + dz)
            hn = at(n)
            if hn is None or not binds(c, n):
                continue
            lo = max(lo, hn - limit_m)
            hi = min(hi, hn + limit_m)
        if lo <= hi or not resolves_toward_ground:
            new = min(max(h, lo), hi)
        else:
            # NO HEIGHT EVERY NEIGHBOUR ACCEPTS: move TOWARD THIS SAMPLE'S
            # OWN GROUND -- up for a cut, down for a fill -- and the
            # neighbours that can follow are re-queued below and follow.
            # See FEATHER_CONFLICT_DEPENDS_ON.
            out["conflicts"] += 1
            if g > h:
                new = lo
                if pins_hold and new > ceil_at[c]:
                    new = ceil_at[c]
                    out["conflicts_held_by_ground_that_cannot_follow"] += 1
            else:
                new = hi
                if pins_hold and new < floor_at_[c]:
                    new = floor_at_[c]
                    out["conflicts_held_by_ground_that_cannot_follow"] += 1
        # TOWARD THE GROUND ONLY: clamp the move into the closed interval
        # between where the plane put it and the ground it stands on.
        new = min(max(new, min(h, g)), max(h, g))
        if abs(new - h) <= EDGE_EPS_M:
            continue
        height[c] = new
        moved_total += abs(new - h)
        out["feathered"] += 1
        if abs(new - h) > out["worst_m"]:
            out["worst_m"] = round(abs(new - h), 4)
            out["worst_at"] = [c[0], c[1]]
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (c[0] + dx, c[1] + dz)
            if n in height and n not in seen:
                seen.add(n)
                work.append(n)
    out["earth_given_back_m"] = round(moved_total, 3)
    for c, h in height.items():
        zx, zz, gx, gy, gen = batter_at[c]
        comp = comps.get((zx, zz))
        if comp is None:
            continue
        comp.set_height(gx, gy, h - gen)
    st["toe_feather"] = out
    return out


# THE RIM THE RAYS LEAVE BETWEEN TWO BRANCHES, AND NOBODY'S RAY OWNED IT.
#
# A ray is keyed on the NEAREST foot, so where the line comes back beside
# itself -- a hairpin, the two legs of a switchback -- the first cell outboard
# of one branch's carriageway can belong to the OTHER branch's ray, whose
# plane there has met ground: success, nothing written.  The toe feather
# cannot close the face either, because it only lays back cells this write
# already moved and the cell in question is natural ground.  MEASURED,
# Fresh42 drive 2 step 4, offline `--validate`: EVERY one of the residual
# steps the refusal charged to "the earthwork's OWN face on virgin ground"
# after FOREIGN_BATTER_DEPENDS_ON landed is this -- T4 (545,-23) 0.9295 m
# (arcs 262 / 284), T5 (-85,-411) 0.7974 (434 / 449), T9 (-348,622) 0.8316
# (111 / 124), T8 (-140,-130) 1.6882 (969 / 1004) -- and so are the two
# `shared_corridor` records the same-cell band test left, T7 (590,-7) 0.8047
# (1107 / 1096) and S13 (550,684) 0.8371 (656 / 649).  T7's needs 0.024 m.
# The plan-time key (`route.SELF_ADJACENCY_DEPENDS_ON`, with its lattice
# edge of slide) JOINED all six: a surface exists, and the writer never
# built it.
#
# SO THE RIM IS CLOSED IN THE GATE'S OWN MODEL: a cell next to this write's
# ground that a written neighbour now steps over the limit is moved -- the
# LEAST distance from the surface it stood at -- into the interval every
# written neighbour allows, held inside the band of the foot that claims it
# (so it is never further from ground than this road's own edge could lay
# it) and inside everything `refusal` forbids.  Then the feather relaxes the
# result and the next ring is asked.  A cell no interval serves keeps its
# step and is counted `unclosable`: the gate still refuses it by owner.
RIM_CLOSURE_DEPENDS_ON = ("the_written_neighbours_heights_after",
                          "the_band_of_the_foot_that_claims_the_cell",
                          "what_refusal_forbids_at_the_cell",
                          "whether_the_edge_was_within_the_limit_before")
# Rings asked outward.  A ring closes against the plane it borders, so the
# walk ends at the band's own run long before this: it is a guard.
RIM_CLOSURE_ROUNDS = 24

# THE CLAIMING FOOT'S BAND IS A REACH, NOT A DESTINATION.  The sentence above
# says what the band is for -- "never further from ground than this road's
# own edge could lay it" -- and the rule that shipped INTERSECTED the band
# with the neighbours' interval, which says something else: the cell must
# LAND inside the band.  Where the cell stands on the ground side of the band
# and its neighbours need less than the band's near side, that forces more
# earth than the step needs; where another road's band stops short of this
# road's near side, it refuses a closure the lattice admits.
#
# MEASURED, Fresh42 drive 3 step 4, `T7-stathub-stenvik`, offline
# `--validate` against the ledger cut at seq 1976: its ONLY caused step,
# 0.8653 m at rim (648,-23) from taken (648,-22).  (648,-22) is T7's SHOULDER
# (lat 3.330, edge 4.0), which T7 raises 56.774 -> 56.858 over T4's paved
# 56.774; (648,-23) is `road_T4_meadhall_stathub#1940`'s batter at 55.9927,
# the face T4 left at 0.78128552 m -- 1e-7 inside the limit.  T7's claim
# there is lat 4.2996, y 56.858: band [56.6239, 57.0921].  T4's band
# (FOREIGN_BATTER_DEPENDS_ON, lat 4.373 from its y 56.153 foot) is
# [55.8614, 56.4446].  The rays' plane 56.6239 leaves T4's band (clipped
# `shared_corridor`), and the closure intersected [56.0767, 56.958] (the
# lattice), T7's band and T4's: EMPTY, so `unclosable`.  The lattice alone
# asks 56.0767 -- a 0.084 m raise, inside T4's band, carrying T4's face up
# with T7's shoulder -- and every height between the ground and the band's
# far side is no further from ground than T7's edge could lay the cell.
RIM_CLOSURE_BAND_DEPENDS_ON = ("the_ground_the_cell_stood_on",
                               "the_far_side_of_the_claiming_foot's_band")
_RIM_CLOSURE_BAND_MODE = "reach"   # reach | inside (the shipped reading)

# A TERRAIN RUN THAT ENDS AT A BANK ENDS SQUARE, AND ITS END FACE HAD NO FOOT.
# `clamp_ends` withholds the clamp where the polyline continues as a deck, so
# every cell past the perpendicular through that end node is claimed by NO
# foot (RIBBON_CLAIM_DEPENDS_ON) -- right for the rays, whose fan would lay a
# disc over the span -- and `close_rim` moves only claimed cells, so the
# carriageway's own end face on the land beside and under the abutment stood
# as it was drawn and was charged to "the earthwork's OWN face".
#
# MEASURED, Fresh42 drive 3 step 4, `W9-vestvik-lhwest`, offline `--validate`
# against the ledger cut at seq 1980: 8 of its 10 caused steps.  Crossings
# x03 (stations 1273..1311, `is_bridge` to 1312) and x04 (1316..1337,
# `is_bridge` from 1315, the segment's last station) leave ONE terrain run,
# 1313 (-382.26,-707.06) y 33.280 -> 1314 (-382.86,-708.43) y 33.286, 1.5 m
# long with BOTH ends withheld.  Its carriageway stands on a ridge (natural
# 33.06-33.44 at the centreline) and 0.5-1.3 m over the flanks, and every rim
# cell of those 8 steps has claim None: (-380,-710) 32.007 under
# (-380,-709) 33.2835 is 1.2765 m; (-385,-705) 32.337, (-380,-708) 32.467,
# (-381,-710) 32.477 the rest.  The closure's band is measured from the
# carriageway's square end -- the run past the end line where the cell is
# within the edge of the centreline, else past the end's corner -- at that
# end node's profile height, and ONLY for cells no run claims.  Every gate
# the closure asks still applies: a raise over water is `water_fill`.
RIM_BANK_END_DEPENDS_ON = ("the_carriageway's_square_end_at_a_withheld_clamp",
                           "the_run_past_that_end")
_RIM_BANK_END_MODE = "claimed"   # claimed | unclaimed (the shipped reading)


def bank_ends(nodes, prof, br) -> list[tuple[float, float, float, float,
                                             float]]:
    """`(x, z, ux, uz, y)` for every terrain-run end whose clamp `clamp_ends`
    withholds: the end node, the unit direction pointing PAST it, and the
    profile height there.  See RIM_BANK_END_DEPENDS_ON."""
    out = []
    for k in range(len(nodes) - 1):
        if br[k] or br[k + 1]:
            continue
        ax, az = float(nodes[k][0]), float(nodes[k][1])
        bx, bz = float(nodes[k + 1][0]), float(nodes[k + 1][1])
        seglen = math.hypot(bx - ax, bz - az)
        if seglen <= 1e-6:
            continue
        ux, uz = (bx - ax) / seglen, (bz - az) / seglen
        lo_ok, hi_ok = clamp_ends(nodes, br, k)
        if not lo_ok:
            out.append((ax, az, -ux, -uz, float(prof[k])))
        if not hi_ok:
            out.append((bx, bz, ux, uz, float(prof[k + 1])))
    return out


def bank_end_run(ends, X: np.ndarray, Z: np.ndarray,
                 edge_m: float) -> tuple[np.ndarray, np.ndarray]:
    """`(run, y)`: each sample's run past the nearest square carriageway end
    in `ends` (+inf where it stands past none) and that end's height."""
    run = np.full(X.shape, np.inf, dtype=np.float64)
    y = np.zeros(X.shape, dtype=np.float64)
    for ex, ez, ux, uz, ey in ends:
        dx, dz = X - ex, Z - ez
        lon = dx * ux + dz * uz
        per = np.abs(dz * ux - dx * uz)
        d = np.where(per <= edge_m, lon, np.hypot(lon, per - edge_m))
        take = (lon > 0.0) & (d < run)
        run[take] = d[take]
        y[take] = ey
    return run, y


def close_rim(comps: dict, patches: dict, batter_at: dict, road_cells: set,
              claim_of: dict, st: dict, limit_m: float, *, before_at,
              refuse, foreign, edge_m: float, grade: float, refeather,
              record=None, fixed_at=None,
              max_rounds: int = RIM_CLOSURE_ROUNDS) -> dict:
    """Close the caused steps at this write's rim that no ray owned.  See
    RIM_CLOSURE_DEPENDS_ON.  `claim_of[(sx, sz)] = (lat, y)` of the nearest
    foot for every cell within reach; `refuse(sx, sz, generated, delta)` is
    the stamp's own `refusal`; `foreign(sx, sz)` is `foreign_road`.
    `record(cell, cause, height_m)`, when given, is told every cell a gate
    forbade the closure to lay, once, under that gate's cause, with the
    height over generated ground the closure asked (`clip_at`'s shape)."""
    out = {"cells": 0, "rounds": 0, "unclosable": 0, "worst_move_m": 0.0,
           "worst_at": None, "refeathered": 0}

    def cell_of(c):
        zx, zz = tcdata.zone_of(c[0], c[1])
        comp = comps.get((zx, zz))
        if comp is None:
            return None
        gx, gy = tcdata.vertex_mask_index(*comp.centre, c[0], c[1])
        if not (0 <= gx < tcdata.PITCH and 0 <= gy < tcdata.PITCH):
            return None
        return zx, zz, comp, gx, gy, gy * tcdata.PITCH + gx

    def after(c):
        got = cell_of(c)
        if got is None or not got[2].modified_height[got[5]]:
            return None
        g = appliedmod.generated_at(patches, float(c[0]), float(c[1]))
        return None if g is None else g + float(got[2].level_delta[got[5]])

    def height(c):
        """What stands at `c` once this write lands: its own sample, or the
        surface before it."""
        if fixed_at is not None:
            fixed_y = fixed_at(*c)
            if fixed_y is not None:
                return fixed_y
        h = after(c)
        return before_at(c[0], c[1]) if h is None else h

    def lat_of(c):
        cl = claim_of.get(c)
        return math.inf if cl is None else cl[0]

    def binds(c, n) -> bool:
        if fixed_at is not None and (fixed_at(*c) is not None
                                     or fixed_at(*n) is not None):
            return True
        bc, bn = before_at(c[0], c[1]), before_at(n[0], n[1])
        return bc is None or bn is None or abs(bc - bn) <= limit_m + EDGE_EPS_M

    def movable(c) -> bool:
        cl = claim_of.get(c)
        return (cl is not None and cl[0] > edge_m + EDGE_EPS_M
                and c not in road_cells)

    def violated(c) -> bool:
        """Does `c` stand in a pair the rim gate CHARGES: a binding edge over
        the limit with at least one side this write's own sample?"""
        hc = height(c)
        if hc is None:
            return False
        mine = c in batter_at or c in road_cells
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (c[0] + dx, c[1] + dz)
            if (mine or n in batter_at or n in road_cells) and binds(c, n):
                hn = height(n)
                if hn is not None and abs(hc - hn) > limit_m + EDGE_EPS_M:
                    return True
        return False

    # A cell this write already moved by no more than AUTHOR_TOLERANCE is RIM
    # to the gate and batter to the feather, which only ever lays a cell BACK
    # toward its ground -- so a face that needs it moved the other way is
    # asked here too.  A move TOWARD the ground is the feather's, never this.
    #
    # THE WAVE GOES OUTWARD FROM THE ROAD.  A cell answers only to the
    # neighbours NEARER the carriageway than it is (and to cells nothing may
    # move): that makes the order a proof of termination and puts every
    # squeeze on the side away from the road, where the next cell out has the
    # ground's slack to absorb it -- T8's (-390,624) sits between (-389,624)
    # at 47.750 and (-391,624) at 49.373, and only the OUTER one can give.
    unclosable: set = set()
    for rnd in range(max_rounds):
        written = set(batter_at) | set(road_cells)
        ring = {(w[0] + dx, w[1] + dz) for w in written
                for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))}
        heap = [(lat_of(c), c) for c in (ring | set(batter_at))
                if movable(c) and c not in unclosable and violated(c)]
        heapq.heapify(heap)
        queued = {c for _l, c in heap}
        added = 0
        while heap:
            lc, c = heapq.heappop(heap)
            queued.discard(c)
            b = before_at(c[0], c[1])
            g = appliedmod.generated_at(patches, float(c[0]), float(c[1]))
            cur = height(c)
            if b is None or g is None or cur is None:
                continue
            lo, hi = -math.inf, math.inf
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                n = (c[0] + dx, c[1] + dz)
                if not binds(c, n):
                    continue
                if movable(n) and lat_of(n) > lc and n not in unclosable:
                    continue          # further out: it answers to this cell
                hn = height(n)
                if hn is not None:
                    lo, hi = max(lo, hn - limit_m), min(hi, hn + limit_m)
            if lo - EDGE_EPS_M <= cur <= hi + EDGE_EPS_M:
                continue
            lat, y = claim_of[c]
            run = lat - edge_m
            blo, bhi = y - grade * run, y + grade * run
            if _RIM_CLOSURE_BAND_MODE == "reach":
                # See RIM_CLOSURE_BAND_DEPENDS_ON: the band bounds how FAR
                # from its ground the cell may go, not where it must land.
                blo, bhi = min(blo, b), max(bhi, b)
            lo, hi = max(lo, blo), min(hi, bhi)
            fr = foreign(c[0], c[1])
            if fr is not None:
                if fr["paved"]:
                    unclosable.add(c)
                    continue
                lo, hi = max(lo, fr["lo"]), min(hi, fr["hi"])
            if lo > hi + EDGE_EPS_M:
                unclosable.add(c)
                continue
            new = min(max(cur, lo), hi)
            if (new - cur) * (cur - b) < 0.0:
                continue              # toward its ground: the feather's move
            delta = new - g
            cause = ("over_clamp" if abs(delta) > tcdata.CLAMP_M
                     else refuse(float(c[0]), float(c[1]), g, delta))
            if cause is not None:
                # A FACE A GATE FORBADE THE CLOSURE TO LAY is that gate's, and
                # is recorded under its cause so the rim owner names it
                # (RIM_STEP_OWNER_DEPENDS_ON) instead of "the earthwork".
                unclosable.add(c)
                if record is not None:
                    record(c, cause, abs(delta))
                continue
            got = cell_of(c)
            if got is None:
                unclosable.add(c)
                continue
            zx, zz, comp, gx, gy, _k = got
            comp.set_height(gx, gy, delta)
            batter_at[c] = (zx, zz, gx, gy, g)
            written.add(c)
            added += 1
            if abs(new - cur) > out["worst_move_m"]:
                out["worst_move_m"] = round(abs(new - cur), 4)
                out["worst_at"] = [c[0], c[1]]
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                n = (c[0] + dx, c[1] + dz)
                if (n not in queued and n not in unclosable and movable(n)
                        and lat_of(n) > lc and binds(c, n)):
                    hn = height(n)
                    if hn is not None and abs(hn - new) > limit_m + EDGE_EPS_M:
                        heapq.heappush(heap, (lat_of(n), n))
                        queued.add(n)
        out["rounds"] = rnd + 1
        if not added:
            break
        out["cells"] += added
        refeather()
        out["refeathered"] += 1
    out["unclosable"] = len(unclosable)
    st["rim_closure"] = out
    return out


# WHAT THE BATTER'S EARTH BUDGET ACTUALLY DEPENDS ON.
BATTER_ADVANCE_DEPENDS_ON = ("wall_at_the_carriageway_edge",
                             "this_sample_delta", "run_spent",
                             "apply_clamp")


def batter_advance(delta: float, full_edge: float, run: float) -> str | None:
    """Keep grading outward (None), or the cause that STOPS this ray.

    THE HILLSIDE IS STEEPER THAN THE BATTER.  The batter is only worth its
    earth while it is closing on natural ground: `delta` is the height still
    between the graded plane and the ground at this sample, and once that is
    bigger than the wall the road holds at its own edge, grading further out
    moves more earth than the road moves and STILL does not arrive.

    THE RUN IS PART OF THE KEY AND IT WAS MISSING.  The budget is measured at
    lat = edge -- `gen_edge`, a bilinear sample on the exact geometric edge --
    while the samples it is compared against sit on the integer lattice
    0.10-1.16 m further out, where natural ground has already moved.  With no
    allowance for that run the comparison charges the batter for the GROUND's
    own fall between the two points.  MEASURED on T1-portalhub-temple,
    Fresh42, 2026-09-24: ALL EIGHTEEN `steeper_than_batter` clips fire at the
    ray's FIRST sample, run 0.10-1.16 m, exceeding the budget by 0.002-0.374 m
    -- so the ray is terminated before it writes one sample and the road's
    whole edge wall (0.13-3.93 m) stays standing.  A rule that fires at the
    first sample has not measured a hillside, it has measured a sub-metre
    lattice offset.

    So the budget carries the run at the batter's OWN grade: over `run` the
    plane has descended `BATTER_GRADE * run`, and ground that fell by the same
    amount is exactly as steep as the batter, not steeper.  The predicate is
    therefore `|delta| > |full_edge| + BATTER_GRADE * run`, which is
    dimensionally the statement the docstring always made -- ground steeper
    than the batter -- and reduces to the old one at run = 0.

    AND THE CLAMP IS ITS OWN RULE NOW.  The old form claimed the budget
    "keeps the batter inside the +/-8 m apply clamp without a second rule,
    because the carriageway's own delta is already inside it"; once the run is
    carried that is no longer true, so the clamp is stated rather than
    inherited.  `TerrainComp::ApplyToHeightmap` discards anything past it, so
    a sample over the limit is earthwork that is not where the plan says.
    """
    if abs(delta) > tcdata.CLAMP_M:
        return "over_clamp"
    if abs(delta) > abs(full_edge) + BATTER_GRADE * run + EDGE_EPS_M:
        return "steeper_than_batter"
    return None


# WHAT "THE REACH IS SPENT" ACTUALLY DEPENDS ON, AND THE CAP ALONE NAMES A
# SUPERSET OF IT.  `reach` was one cause for two populations, and a single
# name over both cannot be acted on:
#
#   * the taper WAS closing and ran out of run.  What it still needed is
#     COMPUTABLE from what it already did, so the cap can be DERIVED against
#     a measurement instead of guessed at.
#   * the taper had NOT closed at all over the run it spent: the ground took
#     back at least as much as the plane laid down.  More reach on one of
#     these moves more earth and leaves a TALLER wall FURTHER from the road.
#
# AND THIS VERDICT IS RELATIVE TO THE RUN SPENT.  IT IS NOT "NO REACH EVER
# CLOSES IT", AND SAYING SO COST A BAD HANDOFF.  The class was first read as
# a permanent property of the hillside -- 99 rays "the route must move" --
# and it is not: it says the ray had not closed BY THE CAP IT WAS MEASURED
# AT.  A local break of slope inside a short run reads exactly like a
# hillside that never relents.  MEASURED, Fresh42, same plan, same field,
# reach 10.5 -> 24.0 m:
#
#   T9-wttown-dockshore   caused 203 -> 33   worst 5.4927 -> 1.4935 m
#                         reach clips 135 -> 7   NOT-CLOSING 57 -> 0
#   T8-stenvik-wttown     caused  97 -> 46   worst 5.4615 -> 2.2137 m
#                         reach clips  52 -> 2   NOT-CLOSING 39 -> 0
#
# Every one of the 99 closed once it was given the run.  So the class is a
# MEASUREMENT OF THIS CAP, reported as such, and a standing not-closing count
# at a reach that is itself derived is the number that would be the route's.
#
# AND THE CLAMP CANNOT BE THE BINDING CONSTRAINT ON THE CLOSING POPULATION.
# `|delta|` falls monotonically once the taper is closing, and the value it
# starts from is the carriageway's own `full_edge`, which is inside the apply
# clamp because the carriageway was written.  So a converging ray's `|delta|`
# never exceeds a number already inside the clamp: spending more reach on it
# cannot buy an `over_clamp`.  "Would a longer reach hit the clamp instead"
# therefore has a proof rather than a guess, and the answer is no.
#
# THE MEASUREMENT, NOT A SLOPE SAMPLE.  The closing rate is taken over the
# WHOLE run the ray spent -- `(|full_edge| - |residual|) / run` -- rather than
# from the ground's slope at the last cell.  A one-cell slope reading is the
# lattice-offset defect `batter_advance` was already repaired for: a single
# 1 m sample over a break of slope answers a question about a 10 m run.
REACH_EXIT_DEPENDS_ON = ("wall_at_the_carriageway_edge",
                         "residual_at_the_cap", "run_spent")


def batter_reach_exit(full_edge: float, residual: float,
                      run: float) -> tuple[str, float | None]:
    """Why this ray hit the cap, and the run that would have finished it.

    Returns `("reach", run_that_closes_it)` when the taper was converging --
    the second element is the total run from the carriageway edge this ray
    needs at its OWN measured closing rate -- or `("reach_not_closing", None)`
    when it had closed nothing by the cap it was measured at.  See
    REACH_EXIT_DEPENDS_ON: the second verdict is about THIS CAP, never about
    the hillside for all time.
    """
    closed = abs(full_edge) - abs(residual)
    if run <= 0.0 or closed <= EDGE_EPS_M:
        return "reach_not_closing", None
    return "reach", run + run * abs(residual) / closed


class PadKeepOut:
    """WHERE THE CARRIAGEWAY MAY NOT BE PAVED, and it is a set of PER-BUILDING
    FOUNDATIONS rather than one circle per settlement.

    THE DEFECT THIS EXISTS FOR, MEASURED.  `settlements/sites.yaml` records one
    `pad_radius_m` per site and this module read it as a pad footprint.  For a
    watchtower or a castle that is true and the two numbers agree: wt-spawn's
    disc is 16.1 m against a 20 x 20 m pad, hognest's 33.1 m against 44 x 44 m,
    i.e. the half-diagonal plus a margin.  For a TOWN it is false, and
    `plan.py` says so in its own docstring -- "taken from the district or the
    pad plus its own margin".  `stenvik` and `vestvik` are `pad_radius_m:
    100.0`, a DISTRICT radius, and stenvik's own `why` field states the
    consequence outright: "The district is NOT levelled -- no 176 m district of
    <= 12 m relief on this seed is both dry and coastal, so the town follows
    the ground on individual pads."

    So a 100 m disc was refusing the carriageway over ground nobody had built
    or ever would.  MEASURED on T4-meadhall-stathub: 293.9 m of centreline
    written NOTHING, a -8.491 m step across the gap at (544.8, 809.2), and the
    trunk road into the town ending at a wall.  The foundations it was standing
    in for are 15 per-building `site_pad` rectangles whose WORST half-extent is
    13.05 m (`stenvik-hall-1`), so the disc was 7.7x the thing it protected.
    `stenvik-house-3` at (606.5, 938.8) is 113 m from the centre and OUTSIDE
    the disc entirely -- the circle was both too big and in the wrong place.

    THE FOOTPRINTS ARE ASKED OF THE WRITE-TIME GUARD, NOT RE-DERIVED HERE.
    `writer._pad_claims` is the function `writer._pad_footprint_check` itself
    uses to decide whether a `terrain_write` authors a sample inside a
    foundation, and it is the authority that will refuse this write at append
    time.  Modelling its geometry here -- half-extents from `pad_w / 2 +
    apron_m`, one claim per site, latest in file order, position resolved from
    the site's sibling records -- is how a closed-form shrink radius silently
    skipped 6 cylinders the gate itself passes.  So the rasteriser asks the
    guard and gets the same rectangles the guard will judge it against.

    THE FOUNDATION IS THE GROUND THE PAD LAID, NOT THE RECTANGLE IT DECLARED,
    and reading it the other way round is the defect this class was carrying.
    `_pad_claims`' half-extents are `pad_w / 2 + apron_m` -- the NOMINAL
    rectangle PLUS the apron -- and that is strictly WIDER than the set of
    samples the pad's own `terrain_write` actually levelled.  MEASURED over
    this chain: 21,666 lattice samples lie inside the 58 live pad rectangles
    and 3,564 of them have never been levelled by any pad -- 1,355 inside
    `sudrberg-keep` alone, 666 of `stenvik-hall-1`'s 676.  Every one of those
    is ground the PAD does not write and the ROAD was forbidden to: an
    UNWRITTEN ANNULUS the width of the apron around every pad on this world,
    which no participant owns and no participant may repair.  A rectangle
    somebody declared is not the set of samples somebody wrote.

    So the keep-out for a pad that HAS LEVELLED SOMETHING is exactly the
    samples it levelled.  A pad levels its rectangle and FEATHERS PAST IT --
    MEASURED, `stenvik-stonehouse-1` seq 272 authored 165 samples and only 60
    lie inside its 11.0 x 15.0 m rectangle -- so the levelled set is both
    narrower and wider than the rectangle, and it is the only one of the two
    that names ground a building is standing on.  `refusal`'s per-sample
    `foreign` gate, which reads the AUTHOR of each sample off the live blobs
    (`applied.Applied`) and is blind to radii by design, remains the fine
    authority; a piece standing on ground NO pad levelled is protected by the
    piece gates (`protected_pieces`, `placed_objects`), which measure the
    piece rather than a rectangle it happens to sit in.

    THE RECTANGLE SURVIVES FOR A PAD THAT LEVELLED NOTHING, and the nominal
    disc for a SITE WITH NO LIVE PAD, because there the question cannot be
    asked: nothing is written, so authorship answers nothing, and the declared
    extent is the only statement of where Settlements intends to build.
    MEASURED on this chain: all 16 road sites have at least one live pad
    claim and all 58 pad claims levelled at least one sample, so neither path
    is taken today -- they are here so that a foundation planned and not yet
    laid is still protected rather than paved.
    """

    @staticmethod
    def pad_stamps(x: float, z: float, cx: float, cz: float,
                   hw: float, hd: float) -> bool:
        # The pad writer admits boundary samples with a 0.25 m tolerance.
        # A road must not reserve a different lattice from that future write.
        return flattenmod.stamps(x - cx, hw) and flattenmod.stamps(z - cz, hd)

    __slots__ = ("rects", "unlaid", "discs", "by_id", "levelled",
                 "undecodable")

    def __init__(self, rects: list[tuple[str, float, float, float, float]],
                 discs: list[tuple[str, float, float, float]],
                 by_id: dict[str, dict] | None = None,
                 levelled: dict[tuple[int, int], str] | None = None,
                 undecodable: list[dict] | None = None,
                 laid_sites: set[str] | None = None):
        self.rects = rects
        self.discs = discs
        # THE FOUNDATION'S OWN RECORD, for the junction report.  Kept beside
        # the hot tuple list rather than in it: `contains` is asked once per
        # lattice sample over a 1.5 km ribbon and has no business unpacking a
        # dict, while the report is asked once per gap.
        self.by_id = by_id or {}
        # EVERY WORLD SAMPLE A PAD ACTUALLY LEVELLED AND STILL EFFECTIVELY
        # AUTHORS, `{(sx, sz): site_id}`.  A dict keyed on the integer sample
        # is an O(1) test and it is the same set
        # `writer._pad_footprint_check` judges against, so the rasteriser and
        # the write-time guard cannot disagree about what a foundation is.
        self.levelled = levelled or {}
        self.undecodable = undecodable or []
        # THE RECTANGLES THAT STILL GATE, and it is only the ones belonging
        # to a pad that has levelled NOTHING.  `self.rects` stays the full
        # DECLARED set because the annulus check and `describe` have to be
        # able to say how far the declaration ran past the writes; `contains`
        # reads this one.  See the class docstring: 3,564 samples inside the
        # declared rectangles have never been levelled by any pad, and a
        # rectangle nobody wrote is not a foundation.
        #
        # "LEVELLED NOTHING" IS ASKED OF THE PAD'S OWN RECORD, which is why
        # `laid_sites` is a separate argument rather than
        # `set(levelled.values())`.  `levelled` is SCOPED TO EFFECTIVE
        # AUTHORSHIP, so a pad whose every sample has since been re-authored
        # by a later pad contributes nothing to it -- and reading the scoped
        # set would hand that pad its full declared rectangle back and
        # re-open the annulus around it.
        laid = set(laid_sites) if laid_sites is not None \
            else set(self.levelled.values())
        self.unlaid = [r for r in rects if r[0] not in laid]

    def contains(self, x: float, z: float) -> str | None:
        """The site whose foundation covers `(x, z)`, or None.

        THE LEVELLED SET IS THE FOUNDATION.  A pad's declared rectangle is
        `pad_w / 2 + apron_m` and is strictly wider than what it wrote; the
        difference is the unwritten annulus this class's docstring measures.
        A rectangle is consulted only for a pad that has levelled nothing,
        and a nominal disc only for a site with no live pad at all.

        `EDGE_EPS_M` on the rectangle for the same reason the carriageway edge
        carries it: a half-extent is a sum of floats and a sample sitting
        exactly on the boundary must land on one definite side of it.
        """
        x0, z0 = int(math.floor(x)), int(math.floor(z))
        if self.levelled:
            sid = self.levelled.get((x0, z0))
            if sid is None and (x != x0 or z != z0):
                # An off-lattice point renders as a BLEND of the samples
                # around it, so a pad owning any of them owns the ground here
                # -- but only the ones it is actually blended FROM.  A corner
                # whose bilinear weight is 0.0 is a whole metre away and is
                # not under the point; walking it unconditionally is the same
                # zero-weight error `applied.owners_at` was carrying, and it
                # widens every levelled set by one sample on its low side.
                tx, tz = x - x0, z - z0
                for dx, dz, w in ((0, 0, (1 - tx) * (1 - tz)),
                                  (1, 0, tx * (1 - tz)),
                                  (0, 1, (1 - tx) * tz), (1, 1, tx * tz)):
                    if w == 0.0:
                        continue
                    sid = self.levelled.get((x0 + dx, z0 + dz))
                    if sid is not None:
                        break
            if sid is not None:
                return sid
        for sid, cx, cz, hw, hd in self.unlaid:
            if self.pad_stamps(x, z, cx, cz, hw, hd):
                return sid
        for sid, cx, cz, r in self.discs:
            dx, dz = x - cx, z - cz
            if dx * dx + dz * dz <= r * r:
                return sid
        return None

    def finished_y(self, x: float, z: float,
                   only: set | None = None) -> float | None:
        """The future foundation at this sample; live pads use live deltas.

        `only` limits it to those pads (ARRIVAL_PADS_DEPENDS_ON): a road's
        earthwork is pinned to the pads it ARRIVES at, never to a pad it
        merely passes -- that pad shapes its own surroundings when built."""
        for sid, cx, cz, hw, hd in self.unlaid:
            if only is not None and sid not in only:
                continue
            info = self.by_id.get(sid) or {}
            if (info.get("levelled") is False
                    and info.get("pad_y") is not None
                    and self.pad_stamps(x, z, cx, cz, hw, hd)):
                return float(info["pad_y"])
        return None

    def arrival_walks(self, seg: dict) -> list[dict]:
        """The acceptance gate's entire walk to each nearby destination."""
        from accept.sections import settlements as gate    # noqa: PLC0415
        from accept import instruments                    # noqa: PLC0415
        out = []
        for end, k, other in (("A", 0, -1), ("B", -1, 0)):
            x, z = map(float, seg["nodes"][k])
            ox, oz = seg["nodes"][other]
            choices = []
            for sid, cx, cz, hw, hd in self.rects:
                gap = gate.rect_distance_m(x, z, cx, cz, hw, hd)
                if gate.rect_distance_m(ox, oz, cx, cz, hw, hd) < gap:
                    continue
                info = self.by_id.get(sid) or {}
                if gap <= gate.ARRIVAL_REACH_M and info.get("pad_y") is not None:
                    choices.append((gap, sid, cx, cz, hw, hd, info["pad_y"]))
            if not choices:
                continue
            gap, sid, cx, cz, hw, hd, datum = min(choices)
            dx, dz = cx - x, cz - z
            length = math.hypot(dx, dz)
            if length < 1e-9:
                continue
            ux, uz = dx / length, dz / length
            entry = gate.ray_entry_m(x, z, ux, uz, cx, cz, hw, hd)
            n = int(round((max(gap, entry or 0.0) + gate.INSIDE_PROBE_M)
                          / gate.SAMPLE_M))
            verts, inside = gate.probe_walk(
                x, z, ux, uz, n, gap,
                lambda vx, vz: abs(vx - cx) <= hw and abs(vz - cz) <= hd,
                instruments.snap)
            out.append({"segment": seg["id"], "end": end, "x": x, "z": z,
                        "datum": float(datum), "declared_datum": float(datum),
                        "gap_m": gap, "dest": f"pad {sid!r}",
                        "vertices": verts, "inside": inside,
                        "nums": {"destination": sid,
                                 "destination_kind": "declared"}})
        return out

    def approach_zones(self, seg: dict, batter_m: float = BATTER_MAX_M) -> set:
        zones = set()
        for walk in self.arrival_walks(seg):
            nodes = walk["vertices"]
            zones.update(segment_zones(nodes, [False] * len(nodes), 2.0,
                                       batter_m=batter_m))
        if batter_m:
            from accept.sections import settlements as gate  # noqa: PLC0415
            limit = min(float(seg.get("max_cf_m", tcdata.CLAMP_M)), tcdata.CLAMP_M)
            collar = math.ceil(limit / BATTER_GRADE) + 2
            reach = float(seg["width_m"]) / 2 + SHOULDER_M + batter_m
            for _sid, cx, cz, hw, hd in self.rects:
                distance, _foot = gate.centreline_distance(seg["nodes"], cx, cz)
                if distance > reach + math.hypot(hw, hd):
                    continue
                x0, z0 = tcdata.zone_of(cx - hw - collar, cz - hd - collar)
                x1, z1 = tcdata.zone_of(cx + hw + collar, cz + hd + collar)
                zones.update((x, z) for x in range(x0, x1 + 1)
                             for z in range(z0, z1 + 1))
        return zones

    def arrivals(self, seg: dict, height_at) -> list[dict]:
        """Judge the same lattice edges and berms as finished-world acceptance."""
        from accept.sections import settlements as gate    # noqa: PLC0415
        from blueprints import fixtures                    # noqa: PLC0415
        findings = []
        for walk in self.arrival_walks(seg):
            vertices = walk["vertices"]
            need = set(vertices)
            for (ax, az), (bx, bz) in zip(vertices, vertices[1:]):
                need.update(((ax, bz), (bx, az)))
            heights = {v: height_at(*v) for v in need}
            walk["heights_by_vertex"] = heights
            for f in gate.check_arrival(
                    walk, [heights[v] for v in vertices],
                    fixtures.walker_capsule()[0] / 2, BATTER_GRADE):
                if not f.ok and not f.advisory:
                    findings.append({"end": walk["end"], "check": f.check,
                                     "destination": walk["nums"]["destination"],
                                     "measured": f.measured, "limit": f.limit,
                                     "where": f.where})
        return findings

    def pad_at(self, x: float, z: float) -> dict | None:
        """The FOUNDATION RECORD covering `(x, z)` -- id, seq, centre,
        half-extents and the datum it was LEVELLED TO -- or None.

        THE DATUM HAS TO BE THE BUILDING'S, NOT THE TOWN'S, and this is the
        district defect wearing its third hat.  The junction report used to
        find a gap's site by `pad_radius_m` and quote that site's `pad_y`: for
        every one of T4's six Stenvik gaps that answered "stenvik, pad datum
        45.57 m, pad_levelled_yet False" -- a datum no ground in the town is
        at, on foundations that were levelled hours ago.  MEASURED: the gap at
        (584.3, 954.0) terminates on `stenvik-beehive-1` seq 286, whose
        `target_y` is 47.11 and whose floor reads 47.110 on the live surface,
        1.54 m above the town figure.  `sites.yaml`'s own `why` says the
        district is not levelled and the town follows the ground on individual
        pads, so quoting the district datum reports somebody's intention as
        the ground the operator will stand on.
        """
        sid = self.contains(x, z)
        return None if sid is None else self.by_id.get(sid)

    def __len__(self) -> int:
        return len(self.rects) + len(self.discs)

    def describe(self) -> str:
        worst = max((max(hw, hd) for _s, _x, _z, hw, hd in self.rects),
                    default=0.0)
        return (f"{len(self.levelled)} sample(s) the {len(self.rects)} live "
                f"pad(s) ACTUALLY LEVELLED (the foundation) + "
                f"{len(self.unlaid)} declared rectangle(s) for pad(s) that "
                f"levelled nothing (worst declared half-extent "
                f"{worst:.2f} m) + {len(self.discs)} nominal disc(s) for "
                f"site(s) with no live pad"
                + (f"; {len(self.undecodable)} pad entry(ies) COULD NOT BE "
                   f"DECODED" if self.undecodable else ""))


def planned_pad_claims(world: str, seed: str) -> list[dict]:
    """Pads the build WILL write, in `writer._pad_claims` shape.

    An installation that declares `duplicate_of` is its principal's building
    (terraform/sites.py skips it), so it contributes no second pad.  A world
    with no placement set or no settlement plan contributes nothing from that
    half and says so -- stages 7/8 refuse on their own when they are missing."""
    out: list[dict] = []
    root, pattern, key = worldseed.installations_read(world)
    if str(JUMPSTART / "terraform") not in sys.path:
        sys.path.insert(0, str(JUMPSTART / "terraform"))
    import flatten as flmod                                # noqa: PLC0415
    for path in (sorted(root.glob(pattern)) if root.is_dir() else []):
        doc = yaml.safe_load(path.read_text()) or {}
        for rec in doc.get("placements") or []:
            sol = rec.get(key) or {}
            if sol.get("x") is None or rec.get("duplicate_of"):
                continue
            w, d = flmod.pad_extent(rec)
            # THE DATUM `flatten.py` LEVELS THIS PAD TO -- its own
            # precedence (flatten.py, `target = flatten_cost.target_y or
            # y_centre_m or y`), carried so a road meeting the pad can be
            # held to it at plan time (`pad_participants`).
            out.append({"seq": None, "name": rec["id"], "site_id": rec["id"],
                        "role": "installation_pad", "planned": True,
                        "x": float(sol["x"]), "z": float(sol["z"]),
                        "hw": float(w) / 2.0, "hd": float(d) / 2.0,
                        "datum_y": float(
                            (sol.get("flatten_cost") or {}).get("target_y")
                            or sol.get("y_centre_m") or sol["y"])})
    plan = worldseed.plan_path(seed)
    if plan.exists():
        if str(JUMPSTART / "settlements") not in sys.path:
            sys.path.insert(0, str(JUMPSTART / "settlements"))
        import build as sbmod                              # noqa: PLC0415
        doc = json.loads(plan.read_text())
        sites = list(doc.get("towns") or {}) + [
            r["id"] for r in doc.get("outliers") or [] if r.get("id")]
        for site in sites:
            for u in sbmod.unit_records(doc, site):
                ap = float(getattr(sbmod, "APRON_M", 0.0))
                out.append({"seq": None, "name": u["id"], "site_id": u["id"],
                            "role": "site_pad", "planned": True,
                            "x": float(u["x"]), "z": float(u["z"]),
                            "hw": float(u["pad_w"]) / 2.0 + ap,
                            "hd": float(u["pad_d"]) / 2.0 + ap,
                            "datum_y": (None if u.get("target_y") is None
                                        else float(u["target_y"]))})
    return out


# A SITE WITH NO WRITTEN PAD IS NOT A DISC IF THE PLAN KNOWS ITS PADS.
#
# The fallback for a site with nothing in the chain was a disc of its
# `pad_radius_m` -- for a town that is its 100 m DISTRICT, for a watchtower
# the pad's circumscribed circle plus 2 m -- while `roads/network.py` routed
# against the RECTANGLES the build will lay (PAD_CLAIMS_DEPENDS_ON there).
# On a from-scratch world every settlement pad is laid at step 5, AFTER the
# roads, so at step 4 every settlement is a disc.  MEASURED, Fresh42 drive 2,
# offline `--validate`: once `sites_path` read Fresh42's own sites instead of
# Pirate68's, T3, T7, S4, S7 and S8 refused at the rim (7 / 4 / 1 / 4 / 41
# steps) and T8 gained 50, every one of them inside a nominal disc (stenvik
# r 100, lh-south 17.6, wt-north 16.1, wt-south 16.1, wt-town 16.1) and 4.2 -
# 26.5 m OUTSIDE every planned pad rectangle.  The disc stays only for a site
# with neither a written nor a planned pad.
PAD_KEEPOUT_DEPENDS_ON = ("the_pads_the_chain_wrote",
                          "the_pads_the_plan_will_lay")
# THE NEUTER: "written" restores the rule that shipped.
_PAD_KEEPOUT_SOURCE = "written+planned"


def pad_keepouts_from_chain(sites: list[dict], records: list[dict],
                            ledger=None,
                            planned: list[dict] | None = None) -> PadKeepOut:
    """Build the carriageway keep-out by ASKING `writer._pad_claims`.

    `sites` is `settlements/sites.yaml`'s site list -- the fallback keep-out
    and fallback datum for a site with nothing written yet.  `records` is the
    live chain, and for anything already built it is the authority for BOTH
    the footprint and the datum: a pad's `terrain_write` carries the
    `target_y` it levelled to, which is the height the road must arrive at.

    A pad claim is attributed to a site by its `site_id` prefix, which is the
    convention `settlements/build.py` writes: `stenvik-hall-1` belongs to
    `stenvik`.  A claim matching no site is still a foundation and still kept,
    under its own id -- the keep-out is a union of foundations, not a directory
    of settlements.

    THE FOOTPRINT IS THE RECTANGLE **OR** THE PAD'S OWN LEVELLED SET, and the
    second half is not optional -- MEASURED, it is what refused T4 at append
    time when this function returned rectangles alone.  A pad levels its
    rectangle and FEATHERS PAST IT: `stenvik-cottage-2` seq 265 levelled
    (480, 908), which is 0.1 m outside its own 10.4 x 12.4 m rectangle, and
    T4's batter authored a +1.400 m move there.  `writer._pad_footprint_check`
    tests rectangle OR levelled set and neither contains the other, so a
    keep-out built from one of them is refused by the other.

    THE LEVELLED SET IS READ WITH THE GUARD'S OWN DECODER, `_entry_samples`,
    rather than re-derived from the blob here.  A pad's entry is a 65x65
    lattice over a 64 m zone whose boundary row is SHARED with the next zone,
    so the set has to be keyed by WORLD SAMPLE and not by lattice index --
    which is the one thing that decoder already gets right and an
    index-arithmetic copy of it would not.

    THE LEVELLED SET IS SCOPED TO EFFECTIVE AUTHORSHIP, by the same ruling
    that fixed the guard.  `_entry_samples` lists every sample a pad's blob
    MODIFIED, which includes ground a later claim has since re-authored --
    and `writer._pad_footprint_check` now resolves a sample to the LAST claim
    that changed it, so a pad that has been built over no longer owns it.
    Keeping those samples in the keep-out refuses the road ground the guard
    would let it write: MEASURED on T4, the unscoped set skipped 2,079
    samples against 588, the road stopped writing around
    `stenvik-beehive-1`, the union restored seq 1158's old profile there and
    the 8 m gradient went back to 0.8849 -- the original defect, reintroduced
    by protecting a foundation that is no longer under that square metre.
    A superseded floor is ground that no longer exists.

    FAIL LOUD, NOT OPEN.  A pad whose blob cannot be read or decoded is
    reported in `undecodable`: the guard fails CLOSED on exactly that case, so
    silently protecting less here buys a refusal at append time instead of a
    warning now.
    """
    claims = writer_pad_claims(records)
    target_of = {r["seq"]: r["params"].get("target_y")
                 for r in records if r.get("op") == "terrain_write"}
    rec_of = {r["seq"]: r for r in records if r.get("op") == "terrain_write"}
    rects = [(q["site_id"], q["x"], q["z"], q["hw"], q["hd"]) for q in claims]
    levelled: dict[tuple[int, int], str] = {}
    undecodable: list[dict] = []
    # EVERY SITE WHOSE PAD RECORD LEVELLED AT LEAST ONE SAMPLE, unscoped by
    # later authorship.  A pad that laid ground keeps no declared rectangle
    # even if every sample of it has since been re-authored; see
    # `PadKeepOut.__init__`.
    laid_sites: set[str] = set()
    if ledger is not None:
        import writer as writermod  # noqa: PLC0415
        pad_seqs = {q["seq"] for q in claims}
        touched: dict[tuple[int, int], str] = {}
        for q in claims:
            rec = rec_of.get(q["seq"])
            if rec is None:
                continue
            for e in rec["params"].get("entries", []):
                got = writermod._entry_samples(e, ledger)
                if got is None:
                    undecodable.append({"site_id": q["site_id"],
                                        "seq": q["seq"],
                                        "zone": e.get("zone")})
                    continue
                # `touched` resolves to the LAST pad to hold a sample, so
                # `set(touched.values())` is the set of pads that are still
                # the last toucher SOMEWHERE -- MEASURED, 38 of 58; the other
                # 20 are fully overlapped by a later pad and would have kept
                # their whole declared rectangle.  "Did this pad lay ground"
                # is a per-claim fact and is recorded as one.
                if got:
                    laid_sites.add(q["site_id"])
                for sxz in got:
                    touched[sxz] = q["site_id"]
        # THE EFFECTIVE AUTHOR, by the guard's own disagreement test over the
        # chain in FILE ORDER.  Scoped to the samples some pad touched, so
        # this is a few tens of thousands of lookups rather than a whole-world
        # composition.
        state: dict[tuple[int, int], tuple[float, float]] = {}
        owner: dict[tuple[int, int], int] = {}
        for rec in records:
            if rec.get("op") != "terrain_write":
                continue
            sq = rec["seq"]
            for e in rec["params"].get("entries", []):
                got = writermod._entry_samples(e, ledger)
                if got is None:
                    continue
                for k, v in got.items():
                    if k not in touched:
                        continue
                    was = state.get(k)
                    if was is None or abs((v[0] + v[1]) - (was[0] + was[1])) \
                            > writermod.FLATTEN_TOLERANCE_M:
                        owner[k] = sq
                    state[k] = v
        for k, sid in touched.items():
            if owner.get(k) in pad_seqs:
                levelled[k] = sid
    by_id: dict[str, dict] = {}
    for q in claims:
        by_id[q["site_id"]] = {
            "site_id": q["site_id"], "seq": q["seq"], "name": q["name"],
            "xz": [round(q["x"], 2), round(q["z"], 2)],
            "half_extents_m": [round(q["hw"], 2), round(q["hd"], 2)],
            "pad_y": target_of.get(q["seq"]), "levelled": True,
            "datum_source": "the pad's own terrain_write target_y"}
    claimed_sites = {q["site_id"] for q in claims}
    # THE PLANNED PADS, where nothing is written for that pad: a declared
    # rectangle that has levelled nothing, exactly as a written pad that
    # levelled nothing is kept.  See PAD_KEEPOUT_DEPENDS_ON.
    if _PAD_KEEPOUT_SOURCE != "written":
        for q in planned or ():
            if str(q["site_id"]) in claimed_sites:
                continue
            rects.append((str(q["site_id"]), float(q["x"]), float(q["z"]),
                          float(q["hw"]), float(q["hd"])))
            by_id[str(q["site_id"])] = {
                "site_id": q["site_id"], "seq": None, "name": q["name"],
                "xz": [round(float(q["x"]), 2), round(float(q["z"]), 2)],
                "half_extents_m": [round(float(q["hw"]), 2),
                                   round(float(q["hd"]), 2)],
                "pad_y": q.get("datum_y"), "levelled": False,
                "datum_source": "the plan's own datum for a pad the build "
                                "has not laid yet"}
        claimed_sites |= {str(q["site_id"]) for q in planned or ()}
    discs = []
    for s in sites:
        sid = str(s["id"])
        if any(c == sid or c.startswith(sid + "-") for c in claimed_sites):
            continue
        discs.append((sid, float(s["xz"][0]), float(s["xz"][1]),
                      float(s["pad_radius_m"])))
        by_id[sid] = {
            "site_id": sid, "seq": None, "name": None,
            "xz": [float(s["xz"][0]), float(s["xz"][1])],
            "half_extents_m": None,
            "pad_y": (None if s.get("pad_y") is None else float(s["pad_y"])),
            "levelled": False,
            "datum_source": ("settlements/sites.yaml pad_y -- DECLARED, this "
                             "site has no pad in the chain yet")}
    return PadKeepOut(rects, discs, by_id, levelled, undecodable, laid_sites)


# A RIM SAMPLE INSIDE A PAD THE BUILD HAS NOT LAID YET IS JUDGED AGAINST THE
# PAD'S OWN DATUM, because that is the ground the next step lays there.
#
# Settlement pads are written at step 5, after the roads, so at step 4 every
# planned settlement pad is still generated ground -- and the road that
# arrives on it (`network.ARRIVAL_DEPENDS_ON`) stands at the pad's datum
# against a hillside the pad will cut away, or a hollow it will fill.  The
# rim gate read that transient face as a step this write leaves, and refused
# a road that meets the pad exactly.  MEASURED, Fresh42 drive 3 step 4:
# S8-wtsouth-treesth refused 16 steps, worst 1.5141 m at rim [-670,-382],
# every one inside `wt-south`'s planned rectangle (datum 72.48, laid at step
# 5 as seq 2251 at 72.48); against that datum all 16 are owed, worst 0.2514 m
# at [-673,-382] -- offline `--validate` of the same plan, S8 then lays rc 0.
#
# THE PAD'S DATUM, NOT A PASS: a row whose taken height is more than the
# slide limit off the planned datum is still CAUSED -- T8's 7 steps at
# wt-town stand 1.30 m under its 56.0 and still refuse (the route owns them).
# The owed rows are recorded on the write as a debt the pad discharges.
# NEUTER `_PLANNED_PAD_RIM_MODE = "ground"`, the shipped reading (today's
# generated ground), proven by `selftest_planned_pad_rim`.
PLANNED_PAD_RIM_DEPENDS_ON = ("the_planned_pads_datum",)
_PLANNED_PAD_RIM_MODE = "datum"   # datum | ground (the shipped reading)

# PLANNED_PAD_CLIFF_DEPENDS_ON: A STEP AGAINST A PLANNED PAD THAT THIS WRITE DID
# NOT MAKE, AND DID NOT DEEPEN, IS THE PAD'S, NOT THE ROAD'S.
#
# `rim_transfer` judges a DELTA ("a pair already over the limit before this
# write is PRE-EXISTING ... never charged"), but the rim of a planned pad is
# read at the pad's datum AFTER the write and at today's generated ground
# BEFORE it, so a hillside the pad will cut away or a hollow it will fill
# reads as a step the road CAUSED even where the road only moved the taken
# sample toward the datum.  `planned_pad_owed` already owes the rows the write
# brings within one slide limit of the datum; it charged the rest, including
# a batter sample the write RAISED 0.45 m on a 3.06 m cliff the pad has
# against natural ground.  MEASURED on the Ulfsland regen, S6-stenvik-hognest
# (5 `refused pad` rows, worst 1.8808 m at [533,1326], taken [533,1325]
# 107.953 -> 109.049 against `hognest-castle`'s planned 110.93: 2.98 m before,
# 1.88 m after) and S8-wtsouth-treesth (3 rows against `wt-south` 82.3 and
# `tree-sth-2` 75.61: 1.39 -> 0.97 m, 1.28 -> 0.89 m): the writer's own rim gate
# refuses both at step 4 on a cliff no road could close and the pad owns.
# THE RULE, two terms, both from the row:
#   the_taken_sample_was_already_over_the_limit_from_the_datum   before > limit;
#   the_write_did_not_deepen_it                                  after <= before.
# A write that CUTS a sample from within the limit of the datum to outside it,
# or deepens a step already over it, stays CAUSED (T8's 1.30 m under 56.0).
# NEUTER `_PLANNED_PAD_CLIFF_MODE = "caused"`: only the rows within one slide
# limit of the datum are owed (the rule before).  Proven by
# `selftest_planned_pad_rim`.
PLANNED_PAD_CLIFF_DEPENDS_ON = ("the_taken_sample_was_already_over_the_limit_from_the_datum",
                                "the_write_did_not_deepen_it")
_PLANNED_PAD_CLIFF_MODE = "owed"   # owed | caused (the rule before)

# ARRIVAL_PADS_DEPENDS_ON: A ROAD'S EARTHWORK IS PINNED ONLY TO THE PADS IT
# ARRIVES AT.  `stamp` pinned the future datum of, and graded a collar around,
# EVERY planned pad its claim reached; MEASURED on Fresh42 drive 6's saved
# plan, that made T1-portalhub-temple (9 steps, 2.0886 m), T3-temple-meadhall
# (2), S5-temple-southcape (44, 5.1856 m) and S11-southcape-lhsouth (44) read
# REFUSED at plan time -- the real writer laid all four with 0 caused rim
# steps -- and the drive-7 plan lost them and two more.  NEUTER
# `_ARRIVAL_PADS_MODE = "all"`, the pinning of every pad in reach; proven by
# `selftest_arrival_pads`.
ARRIVAL_PADS_DEPENDS_ON = ("the_pads_the_road_arrives_at",)
_ARRIVAL_PADS_MODE = "arrival"    # arrival | all (every planned pad in reach)
_CONNECTION_COLLAR = True         # graded collar round a pad the road meets
_APPROACH_GRADING = True          # grade the walk from the road end to a pad
_COLLAR_GATE = "approach"         # approach | any (the collar for any fixed cell)
# COLLAR_EDGE_DEPENDS_ON: THE COLLAR IS GRADED AGAINST THE GROUND AROUND IT.
# See the comment in `stamp`.  NEUTER `_COLLAR_EDGE_MODE = "free"`: the ring
# past the collar is not an anchor (the shipped envelope).
COLLAR_EDGE_DEPENDS_ON = ("the_ground_just_outside_the_collar",)
_COLLAR_EDGE_MODE = "anchored"    # anchored | free
# COLLAR_REGION_DEPENDS_ON: THE COLLAR IS LAID ROUND THE APPROACH, NOT THE PAD.
# See the comment in `stamp`.  NEUTER `_COLLAR_REGION_MODE = "pad"`: the whole
# pad perimeter within the collar's width.
COLLAR_REGION_DEPENDS_ON = ("the_cells_near_the_graded_approach",)
_COLLAR_REGION_MODE = "pad"    # approach | pad  (MEASURED: `approach` leaves a cliff at its own edge -- T5 9 rows 2.71 m, S7 5 rows 1.74 m, T4 1)
# COLLAR_PEEL_DEPENDS_ON: A COLLAR CELL THAT CANNOT JOIN THE GROUND IS PUT BACK.
# See the comment in `stamp`.  NEUTER `_COLLAR_PEEL_MODE = "off"`: every graded
# cell stays, joined to the ground or not.
COLLAR_PEEL_DEPENDS_ON = ("a_cell_over_the_slide_limit_of_ground_left_alone",)
_COLLAR_PEEL_MODE = "on"    # on | off


# SNAP_SHARED_DEPENDS_ON: A LEG THAT RUNS ALONG A BUILT LEG LIES ON IT.  Two
# legs that share a corridor are planned from the same 4 m cells and
# simplified separately, so their centrelines run a fraction of a metre apart
# at the SAME profile height -- and the later leg's batter then lands a
# fraction of a metre across the earlier leg's batter edge, which stands at
# the slide limit.  MEASURED on Fresh42 drive 10, real writer, step 4:
# W9-vestvik-lhwest runs 0.9 m from S5-temple-southcape (and S11 0.5 m) for
# 110 m at the same height (43.58 m); S5 and S11 laid clean at 0.5 m, W9
# refused 5 steps, worst 1.9211 m at [-316,-461], `rim ground owned by
# road_S5_temple_southcape#2243` -- and 8 earthwork steps without the collar.
# So the stations of a leg within SNAP_SHARED_M of a leg already built, for a
# run of at least SNAP_SHARED_RUN stations, are put on that leg's centreline:
# the same cells are paved twice at one height instead of two surfaces a
# shadow apart.  NEUTER `_SNAP_SHARED_MODE = "off"`.  Proven by
# `selftest_snap_shared`.
SNAP_SHARED_DEPENDS_ON = ("the_built_leg_it_runs_along",)
SNAP_SHARED_M = 1.5
SNAP_SHARED_RUN = 8
SNAP_SHARED_PIN = 3
_SNAP_SHARED_MODE = "snap"    # snap | off


def snap_shared_runs(nodes: list, is_bridge: list, others: list) -> int:
    """Move, in place, the stations of `nodes` that run within SNAP_SHARED_M of
    one of the polylines `others` (each a list of `[x, z]`) for at least
    SNAP_SHARED_RUN consecutive stations onto the nearest point of it.  The
    first and last SNAP_SHARED_PIN stations (the node ends) and bridged
    stations are never moved.  Returns the number of stations moved."""
    if _SNAP_SHARED_MODE != "snap" or not others or len(nodes) < 2:
        return 0
    pts = np.asarray(nodes, dtype=np.float64)
    n = len(pts)
    best_d = np.full(n, np.inf)
    best_q = pts.copy()
    rows = np.arange(n)
    for poly in others:
        poly = np.asarray(poly, dtype=np.float64)
        if len(poly) < 2:
            continue
        a, b = poly[:-1], poly[1:]
        ab = b - a
        l2 = np.maximum((ab * ab).sum(axis=1), 1e-12)
        t = np.clip(((pts[:, None, 0] - a[None, :, 0]) * ab[None, :, 0]
                     + (pts[:, None, 1] - a[None, :, 1]) * ab[None, :, 1])
                    / l2[None, :], 0.0, 1.0)
        px = a[None, :, 0] + t * ab[None, :, 0]
        pz = a[None, :, 1] + t * ab[None, :, 1]
        dd = np.hypot(px - pts[:, None, 0], pz - pts[:, None, 1])
        k = dd.argmin(axis=1)
        d = dd[rows, k]
        better = d < best_d
        best_d[better] = d[better]
        best_q[better] = np.stack([px[rows, k], pz[rows, k]], axis=1)[better]
    near = best_d <= SNAP_SHARED_M
    near[:SNAP_SHARED_PIN] = False
    near[n - SNAP_SHARED_PIN:] = False
    near &= ~np.asarray(is_bridge, dtype=bool)
    moved, i = 0, 0
    while i < n:
        if not near[i]:
            i += 1
            continue
        j = i
        while j < n and near[j]:
            j += 1
        if j - i >= SNAP_SHARED_RUN:
            for k in range(i, j):
                if best_d[k] > 1e-6:
                    nodes[k] = [round(float(best_q[k][0]), 2),
                                round(float(best_q[k][1]), 2)]
                    moved += 1
        i = j
    return moved


# HALF_LATTICE_DEPENDS_ON: NO STATION OF A LEG SITS ON A HALF-LATTICE LINE.  The
# acceptance gate snaps a station to its lattice vertex half-way-UP, and the
# engine's RoundToInt snaps half-way to EVEN, so a station whose x or z is
# exactly n + 0.5 reads the vertex the gate did not pick; on a sloped road the
# two neighbours differ and the gate reports one vertex with two heights, which
# no terrain write closes.  MEASURED on Fresh42 drive 11, the only finding the
# gate left: T7-stathub-stenvik station 222 at the 45-degree run through
# (336.5, 344.6), vertex (337,344) read 81.7624 m and 81.8475 m (0.0851 m).
# So a node is moved 2 cm, off the line, when a station of the gate's own
# resampling lies within HALF_LATTICE_TOL_M of one.  NEUTER
# `_HALF_LATTICE_MODE = "off"`.  Proven by `selftest_half_lattice`.
HALF_LATTICE_DEPENDS_ON = ("no_station_on_a_half_lattice_line",)
HALF_LATTICE_TOL_M = 1e-3
HALF_LATTICE_NUDGE_M = 0.02
_HALF_LATTICE_MODE = "nudge"    # nudge | off


def _on_half_lattice(x: float, z: float) -> bool:
    return (abs((x % 1.0) - 0.5) < HALF_LATTICE_TOL_M
            or abs((z % 1.0) - 0.5) < HALF_LATTICE_TOL_M)


def nudge_off_half_lattice(nodes: list, is_bridge: list,
                           step_m: float = 1.0) -> int:
    """Move, in place, nodes of `nodes` until no station of the gate's own
    `resample(nodes, step_m)` sits on a half-lattice line.  A bridged node is
    never moved.  Returns the number of node moves."""
    if _HALF_LATTICE_MODE != "nudge" or len(nodes) < 2:
        return 0
    from accept import instruments as _gate_i                  # noqa: PLC0415
    moves = 0
    dirs = ((1, 0), (0, 1), (-1, 0), (0, -1), (1, 1), (-1, 1), (1, -1), (-1, -1))
    for attempt in range(24):
        bad = [(s, x, z) for s, x, z in _gate_i.resample(
            [tuple(p) for p in nodes], step_m) if _on_half_lattice(x, z)]
        if not bad:
            return moves
        cum = [0.0]
        for a, b in zip(nodes[:-1], nodes[1:]):
            cum.append(cum[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
        for s, _x, _z in bad:
            k = max(i for i in range(len(nodes)) if cum[i] <= s + 1e-9)
            for cand in (k, k + 1):
                if (0 < cand < len(nodes) - 1 and not is_bridge[cand]):
                    dx, dz = dirs[(attempt + cand) % len(dirs)]
                    nodes[cand] = [round(nodes[cand][0]
                                         + dx * HALF_LATTICE_NUDGE_M, 3),
                                   round(nodes[cand][1]
                                         + dz * HALF_LATTICE_NUDGE_M, 3)]
                    moves += 1
                    break
    return moves


def built_road_lines(plans_by_write: dict, road_claim_at, my_name: str) -> list:
    """The centrelines of the legs in `plans_by_write` that the live ground
    already carries -- the leg whose write name claims a sample on it."""
    out = []
    for name, plan in plans_by_write.items():
        if name == my_name or len(plan["nodes"]) < 2:
            continue
        step = max(1, len(plan["nodes"]) // 10)
        for p in plan["nodes"][::step]:
            got = road_claim_at(float(p[0]), float(p[1]))
            if got is not None and str(got[1]).split("#")[0] == name:
                out.append(plan["nodes"])
                break
    return out


def planned_pad_owed(rows: list[dict], pad_keepouts: "PadKeepOut",
                     limit_m: float, *, cliff: bool | None = None
                     ) -> tuple[list[dict], list[dict]]:
    """Split caused rim rows into (still caused, owed to a planned pad).

    A row is owed when its rim sample lies inside a pad the plan declares and
    nothing has laid (`PadKeepOut.unlaid`, `levelled: False` in `by_id`) and
    either the height this write leaves on the taken side is within `limit_m`
    of that pad's datum, or the taken sample already stood MORE than `limit_m`
    from that datum before this write and the write left it no further from it
    (PLANNED_PAD_CLIFF_DEPENDS_ON): the cliff is the pad's own.  `cliff`
    None takes that rule from `_PLANNED_PAD_CLIFF_MODE`; False asks only the
    first term (the reading the plan's remedy search scores on)."""
    if _PLANNED_PAD_RIM_MODE != "datum":
        return list(rows), []
    planned = []
    for sid, rx, rz, hw, hd in pad_keepouts.unlaid:
        info = pad_keepouts.by_id.get(sid) or {}
        if info.get("levelled") is False and info.get("pad_y") is not None:
            planned.append((sid, rx, rz, hw, hd, float(info["pad_y"])))
    kept: list[dict] = []
    owed: list[dict] = []
    for r in rows:
        x, z = float(r["rim_xz"][0]), float(r["rim_xz"][1])
        hit = next((p for p in planned
                    if pad_keepouts.pad_stamps(x, z, *p[1:5])), None)
        if hit is None:
            kept.append(r)
            continue
        step = abs(float(r["taken_height_after_m"]) - hit[5])
        row = {**r, "planned_pad": hit[0], "pad_y": hit[5],
               "step_after_pad_m": round(step, 4)}
        if step <= limit_m + 1e-9:
            owed.append(row)
            continue
        was = r.get("taken_height_before_m")
        if ((_PLANNED_PAD_CLIFF_MODE == "owed" if cliff is None else cliff)
                and was is not None):
            before = abs(float(was) - hit[5])
            row["step_before_pad_m"] = round(before, 4)
            if before > limit_m + 1e-9 and step <= before + 1e-9:
                row["owed_as"] = "the pad's own cliff, not deepened"
                owed.append(row)
                continue
        kept.append(row)
    return kept, owed



def writer_entry_samples(entry: dict, ledger):
    """`writer._entry_samples`, for the same reason `writer_pad_claims` is
    wrapped: the ledger is imported where it is used.  Returns
    `{(sx, sz): (level, smooth)}` keyed by WORLD sample, or None when the blob
    cannot be read or decoded.
    """
    sys.path.insert(0, str(JUMPSTART / "ledger"))
    import writer as writermod  # noqa: PLC0415
    return writermod._entry_samples(entry, ledger)


def writer_pad_claims(records: list[dict]) -> list[dict]:
    """`writer._pad_claims`, imported where it is used rather than at module
    scope: `roads/` is importable without the ledger on the path (the census
    and equivalence tools do exactly that), and a keep-out is only ever built
    by a driver that already has a chain in hand.
    """
    sys.path.insert(0, str(JUMPSTART / "ledger"))
    import writer as writermod  # noqa: PLC0415
    return writermod._pad_claims(records)


def _clip(st: dict, cause: str, is_road: bool, road_key: str | None,
          wx: float, wz: float, lat: float, full: float,
          residual: float | None = None) -> None:
    """Record a sample the earthwork could not write, and -- when it is a
    BATTER sample -- the WALL that therefore stays standing.

    TWO HEIGHTS, BECAUSE THEY ARE TWO DIFFERENT CLAIMS AND CONFLATING THEM IS
    HOW THE WORSE-THAN-THE-WALL DEFECT HID.  `full` is the cut or fill the
    carriageway holds at the head of this ray -- the wall that stood there
    BEFORE any batter, and the number the repair has to beat.  `residual` is
    what is actually left at the clip: the height between the graded surface
    where the ray stopped and natural ground there.  A ray clipped at its very
    first sample leaves `residual == full` (no better, no worse); a ray
    clipped further out leaves less.  Reporting only `full` would understate
    the repair; reporting only `residual` would hide a clip that achieved
    nothing.

    A clip that leaves no trace is indistinguishable from a batter that
    worked, and "the batter is on every segment" would then be true of a run
    that graded nothing.

    Module level rather than a closure in the sample loop on purpose: it is
    called once per rejected sample over tens of thousands of samples per
    segment, and a `def` inside that loop allocates a function object per
    iteration for no benefit.
    """
    if is_road and road_key:
        st[road_key] = st.get(road_key, 0) + 1
        st["road_clip_at"].append((wx, wz, cause, round(full, 3)))
        return
    key = f"batter_clipped_{cause}"
    st[key] = st.get(key, 0) + 1
    before = abs(full)
    h = before if residual is None else abs(residual)
    st["walls_left_by_cause"][cause] = st["walls_left_by_cause"].get(cause, 0) + 1
    if h > st["walls_left_max_m"]:
        st["walls_left_max_m"] = round(h, 3)
    if before > st["walls_before_max_m"]:
        st["walls_before_max_m"] = round(before, 3)
    # Only walls worth warning about are listed, and the threshold is the one
    # the operator's own complaint is measured against: below 0.5 m a step is
    # a kerb, not a cliff.
    if len(st["walls_left"]) < 60 and h >= 0.5:
        st["walls_left"].append({"world": [round(wx, 1), round(wz, 1)],
                                 "lat_m": round(lat, 1), "wall_m": round(h, 2),
                                 "wall_before_m": round(before, 2),
                                 "cause": cause})
    # EVERY clip's position, uncapped and unfiltered, because the transverse
    # census has to be able to answer "is this step one I already recorded a
    # reason for, or one nobody explained".  An unexplained step is a bug; an
    # explained one is a documented residual, and a verdict that cannot tell
    # them apart is the same failure as a verdict against a flat 0.5 m.
    st["clip_at"].append((wx, wz, cause, round(h, 3)))


# WHETHER A RAY IS HALF OF ONE CORRIDOR OR THE BATTER OF ONE ROAD BESIDE
# ANOTHER.  The corridor apron exists because two TAPERS cannot fit in a gap
# -- T12 beside T3 at 6-10 m -- and that is a fact about the ground THIS
# ray's plane has not yet closed on.  The claim search ran to the batter CAP
# instead, and the cap went 10.5 -> 21.0 m for a reason that had nothing to
# do with corridors.
#
# MEASURED, Fresh42, T7-stathub-stenvik, offline `--validate`, 2026-09-25:
# two of the five caused rim steps are this.  Rays (+1, arc 1655) and
# (-1, arc 1507) are CUTS of 0.679 and 0.810 m that meet natural ground at
# lat 5.02 and 6.23; S4-meadhall-southeast's carriageway stands 24.25 and
# 24.51 m out, 20 m past where either taper closed.  Both were handed to the
# apron, which "levelled flush at the higher" -- 38.190 and 32.464, a FILL
# of 1.27 and 0.33 m on the first cell outside T7's cut carriageway -- the
# feather laid that back onto natural ground, and T7's own 0.39 / 0.80 m cut
# face stood at its edge: 0.9495 m at (403,-290) and 0.9049 m at (468,-181).
# The ray's own plane would have left 0.44 and 0.30.
INTERSTICE_DEPENDS_ON = ("another_road's_claim_on_this_radial",
                         "this_ray's_taper_still_open_there")


# THE APRON BETWEEN TWO CARRIAGEWAYS IS THE TAPER FROM ONE EDGE TO THE OTHER,
# NOT A FLAT AT THE HIGHER ONE.
#
# The corridor rule's own test is `|y_mine - y_theirs| <= grade * gap`: "a
# surface at the batter grade joins the two edges across this gap".  It then
# laid a DIFFERENT surface -- the interstice levelled flush at the higher
# edge -- which leaves the whole difference as one step at the lower edge,
# and spends more earth than either wall owns, so the budget gate clipped it
# at its first sample and the ray was left ungraded.  The plan-time claim
# boundary (`route.cross_claim_lattice`, `grade * (run1 + run2 + lattice)`)
# is the taper's allowance, so the plan approved joints the writer could not
# lay.  MEASURED, Fresh42 drive 5 step 4, J-southcape-wtsouth refused rc 7:
# 8 steps, worst 2.5455 m at rim (-676,-440) from taken (-675,-440), rim
# ground S8's fill batter (63.5873, S8#1799).  J (66.13) runs 8 m beside S8
# (64.65) for ~50 m out of wt-south; the rays at z -440/-439 met S8's
# carriageway 2.9 m past J's edge, `fits` (1.48 <= 2.27), apron flush 66.13
# = 2.571 m over seed ground 63.56 against a 2.22 m budget: clipped
# `steeper_than_batter` at lat 3.9, nothing written.  The plan's claim
# boundary read that very edge, (-676,-440)/(-677,-440), J run 0.90 / S8 run
# 0.44: 1.484 against an allowance of 1.824 -- approved.  The taper lays
# (-676,-440) 65.672 and (-677,-440) 65.161 between J's 66.133 and S8's
# 64.649: steps 0.461 / 0.511 / 0.512, and J is CLEAR against S8's
# recorded write (0 caused).
#
# THE RULE: a cell `lat` of this ray inside the gap is laid at
# `y_mine + (y_theirs - y_mine) * (lat - edge) / gap` -- so every metre of
# the apron along the ray moves `|diff| / gap <= grade`, it stays within the
# batter grade of both edges, and where the two edges agree it IS the old
# flush apron.  The rim gate still judges whatever it leaves.
#   both_carriageway_edges   y_mine at this edge, y_theirs at the first cell
#       their write paved on this ray.
#   the_run_between_them     the gap the `fits` test already measured.
# NEUTER `_INTERSTICE_APRON_MODE = "flush"` (the shipped rule), proven by
# `selftest_interstice_taper`.
INTERSTICE_APRON_DEPENDS_ON = ("both_carriageway_edges",
                               "the_run_between_them")
_INTERSTICE_APRON_MODE = "taper"   # taper | flush (the shipped rule)


# ANOTHER ROAD'S BATTER IS GROUND THAT ROAD SHAPED, NOT ITS CARRIAGEWAY.
#
# `road_claim_at` answers for every sample another road AUTHORED, and that
# is its carriageway AND its batter.  The corridor rule above read every
# answer as "their road height": over the other road's BATTER it compared
# this carriageway with the other road's earthwork face, found "does not
# fit", clipped `shared_corridor` and left the whole ray ungraded -- or
# bounded the walk at the first claimed cell -- so the step between this
# carriageway edge and the other road's batter stood where nothing could
# close it.  And the toe feather read NATURAL ground beside this write where
# the rim gate reads the LIVE surface, so where another write had already
# moved a neighbour the feather laid a toe back toward ground that is not
# there.
#
# MEASURED, Fresh42 drive 2, step 4, replayed offline through `--validate`
# against the ledger each segment saw: 99 caused rim steps over FIVE
# segments stand on ground another ROAD authored where the plans do not step
# -- T8 49 (T3's cut batter, worst 2.3836 m at (232,-57)), S13 40 (T6's, 2.369
# at (195,497)), T5 5 and T13 4 (T3's fill batter, 1.7485 at (-20,-49)), S5 1
# -- and it is T13's ONLY class.  At (232,-57) T8's carriageway stands at
# 33.44 and T3's plan at 33.42: two cuttings side by side, the ridge between
# them left standing because the rule could not tell a batter from a road.
#
# THE BAND IS THE OTHER ROAD'S OWN, from ITS plan: at a cell `run` past its
# carriageway edge, at design height `y`, the heights its edge still reaches
# at the batter grade are `[y - g*run, y + g*run]`.  A cell of their batter
# is walked like ground -- THEIR SURFACE is the ground the plane meets --
# and written only inside their band; a cell their plan paves is never
# written and ends the ray.  Where this plane leaves their band no surface
# serves both edges, which is the corridor rule's routing fact and is
# clipped under the corridor rule's own cause.
FOREIGN_BATTER_DEPENDS_ON = ("who_authored_the_sample",
                             "whether_their_PLAN_paves_it",
                             "their_design_height_and_run_past_their_edge",
                             "the_live_surface_they_left")


def foreign_road_band(road_claim_at, plans_by_write: dict, generated_at,
                      grade: float = BATTER_GRADE):
    """`(x, z) -> None | {who, paved, surface, lo, hi}` for a sample ANOTHER
    road authored.  `road_claim_at` is `main`'s (delta, "name#seq") answer;
    `plans_by_write` maps a write name to its plan.  A claim whose plan is
    unknown, or whose foot cannot be found, is PAVED: the corridor rule's
    conservative reading, never a licence to write."""
    arrays: dict = {}
    # ONE ANSWER PER LATTICE CELL: the claim, the plan and the live surface
    # are all fixed for the life of one rasterisation.
    memo: dict = {}

    def at(x: float, z: float):
        sx, sz = int(round(x)), int(round(z))
        if (sx, sz) in memo:
            return memo[(sx, sz)]
        memo[(sx, sz)] = got = _at(sx, sz)
        return got

    def _at(sx: int, sz: int):
        got = road_claim_at(float(sx), float(sz))
        if got is None:
            return None
        d, who = got
        plan = plans_by_write.get(str(who).split("#")[0])
        gen = generated_at(sx, sz)
        if plan is None or gen is None:
            return {"who": who, "paved": True, "surface": None,
                    "lo": None, "hi": None}
        arr = arrays.get(id(plan))
        if arr is None:
            arr = (np.array(plan["nodes"], dtype=np.float64),
                   np.array(plan["profile_y"], dtype=np.float64),
                   np.array(plan["is_bridge"], dtype=bool))
            arrays[id(plan)] = arr
        ly = lateral_and_y(*arr, float(sx), float(sz))
        edge_t = plan["width_m"] / 2.0 + SHOULDER_M
        if ly is None or ly[0] <= edge_t + EDGE_EPS_M:
            return {"who": who, "paved": True, "surface": gen + d,
                    "lo": None, "hi": None}
        run = ly[0] - edge_t
        return {"who": who, "paved": False, "surface": gen + d,
                "lo": ly[1] - grade * run, "hi": ly[1] + grade * run}
    return at


def stamp(seg: dict, patches: dict, zones: list[tuple[int, int]],
          pad_keepouts: "PadKeepOut",
          protected_pieces: list[tuple[float, float]] | None = None,
          protected_discs: list[tuple[float, float, float]] | None = None,
          placed_objects: list[tuple[float, float]] | None = None,
          poi_keepouts: list[tuple[float, float, float]] | None = None,
          batter_m: float = BATTER_MAX_M,
          batter_grade: float = BATTER_GRADE,
          foreign_at=None,
          road_claim_at=None,
          # `foreign_road_band`'s answer and the live delta beside this
          # write: see FOREIGN_BATTER_DEPENDS_ON.  Absent, both halves read
          # as the shipped rule, so a caller with no live surface is unmoved.
          foreign_road_at=None,
          live_delta_at=None,
          ray_key_carries_bearing: bool = True,
          ray_walks_past_its_bin: bool = True,
          feather_toe_on: bool = True,
          # THE TWO HALVES OF THE BESIDE-ITSELF REPAIR, EACH ITS OWN SWITCH
          # AND EACH DEFAULTING TO THE SHIPPING RULE.  Kept EXECUTABLE rather
          # than described so a selftest can run the false premise over the
          # same constructed ground -- and so the state this file shipped in
          # stays measurable by anyone who needs to attribute a number to one
          self_corridor_clips_on: bool = True,
          orphan_station_rays_on: bool = True,
          # ...AND THE THIRD HALF, WHICH IS THE ONE THAT MOVES GROUND -- AND
          # IT IS OFF, BY MEASUREMENT.  The first two NAME the beside-itself
          # population; this one RECONCILES it, by holding a batter sample at
          # or above the fill floor the claimant it stands beside imposes.
          # It is PROVEN on the fixture -- `selftest_ribbon_beside_itself`
          # runs it and the wide hairpin's 79 CAUSED over-limit lattice edges
          # go to 0 -- and it is REFUSED ON THE CORPUS, by its own numbers.
          #
          # FIVE VARIANTS, ALL MEASURED ON FRESH42'S 14 SEGMENTS, SERIAL,
          # `--validate`, against a BEFORE of T4 22 / T6 3 / T7 7 / T9 49 /
          # S13 1 caused rim steps and a worst wall of 5.486 m:
          #
          #   joint band, ground-keyed, floor AND ceiling, one claimant
          #       T4 110 · T6 32 · T7 24 · T9 402 · S13 9, worst wall 33.917
          #   raise only, envelope over EVERY run within reach
          #       T4 48 · T6 16 · T7 40 · T9 313 · S13 6, and
          #       T13-temple-harboursouth went from PASS to REFUSED
          #   raise only, the kept claimant's floor
          #       T4 47 · T6 16 · T7 40 · T9 267 · S13 6
          #   raise only, max floor over every positive-exceedance claimant
          #       T4 48 · T6 16 · T7 40 · T9 313 · S13 6
          #   raise only, and ONLY where the shipped batter writes nothing
          #       T4 48 · T6 16 · T9 288 · S13 7
          #
          # THE CAUSE IS IN THE CENSUS AND IT IS STRUCTURAL: every arm takes
          # `reach_not_closing` from 0-1 to 44-145 and `steeper_than_batter`
          # from 0 to 21.  The batter's earth budget and its 21 m reach are
          # ONE-DIMENSIONAL instruments, spent along a radial; a fill that
          # answers a TWO-DIMENSIONAL adjacency spends them on ground the ray
          # was not walking, and the ray then stops short and leaves a bigger
          # face than the one that was closed.  Raising `BATTER_MAX_M` is not
          # the answer either -- it is 21.0 and DERIVED -- and widening the
          # rim gate is forbidden.
          #
          # SO THE CROSS-RAY STEP IS REFUSED BY NAME, WITH ITS NUMBER, and
          # the number is `self_corridor_*`: a ROUTING fact with no ground
          # term in it.  The remedy it points at is a ROUTE, which is
          # `roads/network.py`'s and not this file's.  Kept EXECUTABLE rather
          # than described so the next agent re-measures instead of
          # re-deriving -- the same shape as `water_fill_ends_the_ray`.
          joint_claim_band_on: bool = False,
          # AND THE THIRD, WHICH IS `Causeway`'s WATER RULE MEETING THIS
          # FILE'S CONTROL FLOW.  True is the state the rule landed in -- the
          # ray ENDS at a submerged sample -- and it is the neuter, because
          # one dimple is not the end of the hillside.
          water_fill_ends_the_ray: bool = False,
          # THE CORRIDOR APRON ASKS ONLY ABOUT A CLAIM THIS RAY'S OWN TAPER
          # WOULD STILL BE OPEN AT.  False is the shipped rule -- any claim
          # inside the cap -- kept executable as the neuter.  See
          # INTERSTICE_DEPENDS_ON.
          interstice_needs_open_taper: bool = True,
          # THE FEATHER'S CONFLICT RULE, ONE SWITCH PER DEPENDENCY.  See
          # FEATHER_CONFLICT_DEPENDS_ON; the three shipped-rule values are
          # (False, False, True).
          feather_toward_ground: bool = True,
          feather_pins_hold: bool = True,
          feather_binds_on_preexisting: bool = False,
          # THE NEUTER OF FOREIGN_BATTER_DEPENDS_ON.  False is the rule that
          # shipped: every claim is a carriageway and the feather reads
          # natural ground beside this write.
          foreign_batter_is_ground: bool = True,
          # THE NEUTER OF RIM_CLOSURE_DEPENDS_ON.  False is the rule that
          # shipped: a rim cell no ray owned keeps the face its neighbour
          # left.
          rim_closure_on: bool = True,
          ) -> tuple[dict, dict]:
    """Rasterise one segment into per-zone compilers.  Returns (comps, stats).

    Four things stop the ribbon rather than one, and each is somebody's
    property: a Settlements PAD (its earthwork, levelled per building), a
    protected PIECE (an over-water structure whose ground must not move --
    this is the `flatten: FORBIDDEN` rule expressed at the granularity the
    hazard actually has), A PROTECTED STRUCTURE'S WATER, and -- new, and
    MEASURED rather than declared -- a sample a FOREIGN TERRAIN WRITE ALREADY
    OWNS.

    THE FOREIGN-OWNERSHIP GATE IS THE T4 DEFECT'S FIX, AND THE RULING IS
    MAIN'S: inside a settlement pad's footprint the PAD WINS -- it is a
    building's foundation and it must stay flat -- but the road owns the
    approach.  MEASURED with `applied.py`: 89 of T4's 1,443 centreline metres
    sit on samples `SiteFinish` seq 286 overwrote after `RoadBuild` seq 213,
    and sample (586, 954) holds road delta -4.470 against a live -1.571, so
    the applied surface jumps 45.05 -> 47.11 in ONE METRE mid-carriageway.
    Note what the gate is NOT: it is not a radius.  That clobber lies OUTSIDE
    the road's own `pad_radius_m` keep-out, so a circle cannot see it.
    Ownership is read from the blobs.  A sample the pad owns is left alone and
    carried forward by the union; the step into it is removed by regrading the
    APPROACH (`grade_into_foreign`), not by fighting over the sample.

    THE WATER RULE IS SCOPED TO THE STRUCTURES, NOT TO THE WATER PLANE, and
    the first version got that wrong.  Refusing every sample below 30.0 m
    sounds safe and is not the rule: MEASURED on W5-brgs1-wsouth and
    W10-brgs1-treenear, RoadNet's profile crosses an 8 m pond on 2.9 m of
    fill -- a CAUSEWAY, with no structure within 200 m -- and a blanket rule
    left an 8 m swim in the middle of a finished road.  The hazard the rule
    exists for is removing the water a protected structure STANDS OVER, so it
    is tested inside that structure's OWN recorded extent and nowhere else.

    THE EARTHWORK IS TWO BANDS WITH TWO DIFFERENT POLICIES, and the difference
    is the whole safety argument for widening the write.  The CARRIAGEWAY
    (lat <= edge) must be continuous, so a protection that blocks a
    carriageway sample leaves a recorded GAP and `walkability` judges it.  The
    BATTER (lat > edge) is cosmetic relief, so a protection that blocks a
    batter sample CLIPS THE REST OF THAT RAY: the wall stays there and is
    counted.  A clipped batter is a blemish; a clipped carriageway is a hole
    the operator falls into.  Same geometry, opposite failure mode, so they
    cannot share a policy.

    THE BATTER IS WRITTEN RAY BY RAY, OUTWARD, AND STOPS ONCE.  Two measured
    defects forced that shape and neither is visible sample by sample:

      * PER-SAMPLE REFUSAL LEAVES A RIDGE.  A refused sample keeps its
        generated height while the graded samples either side of it are dug
        away, so the step INTO it is bigger than the wall the batter was
        removing.  MEASURED on T12 at (28.0, -14.6): 3.138 m of original wall
        became a 3.676 m step.  Walking the ray outward and stopping at the
        first refusal makes the residual at the stop at most the original
        wall, by construction: the graded surface is monotone in the direction
        of the cut or fill, so whatever is left is a fraction of what was
        there.
      * A TAPER THAT RESUMES BEYOND A HOLLOW IS AN ISOLATED PATCH.  The batter
        stops where its plane MEETS natural ground; past that crossing the
        plane is underground, and writing it again further out because the
        ground dipped would bury a second slab in the hillside.

    A ray is one cross section on one side: `(sign, station rounded to 1 m)`
    PLUS THE BEARING FROM THE FOOT.  The station alone is not a cross section
    wherever the nearest point on the centreline is a VERTEX -- at every
    corner the projection collapses and one station bin holds a whole wedge of
    bearings -- and the walk's `break` is a fact about ONE bearing.  See the
    ray-key comment in pass 1 for the measurement.

    `ray_key_carries_bearing=False` restores the old station-only key.  It
    exists so `selftest_batter_ray_key` can NEUTER the dependency and watch
    the taper it used to throw away come back; it is not a shipping mode.

    `ray_walks_past_its_bin=False` restores the OTHER known-bad rule this
    file shipped: a ray whose arc bin ran out of samples stopped there and
    recorded nothing, on the premise that a neighbouring ray writes the
    ground outboard.  It exists so `selftest_batter_ray_end` can run that
    premise over ground built in memory; it is not a shipping mode.  See
    RAY_END_DEPENDS_ON.
    """
    nodes = np.array(seg["nodes"], dtype=np.float64)
    prof = np.array(seg["profile_y"], dtype=np.float64)
    br = np.array(seg["is_bridge"], dtype=bool)
    half = seg["width_m"] / 2.0
    edge = half + SHOULDER_M
    road_colour = tcdata.PAINTS[PAINT_ROAD]
    shoulder_colour = tcdata.PAINTS[PAINT_SHOULDER]

    comps: dict[tuple[int, int], tcdata.Compiler] = {}
    st = {"samples_paved": 0, "samples_shoulder": 0, "samples_batter": 0,
          "skipped_pad": 0, "skipped_protected": 0, "skipped_underwater": 0,
          "skipped_foreign": 0,
          "batter_clipped_pad": 0, "batter_clipped_protected": 0,
          "batter_clipped_underwater": 0, "batter_clipped_placed": 0,
          "batter_clipped_water_edge": 0, "batter_clipped_poi": 0,
          "batter_clipped_foreign": 0, "batter_clipped_steeper_than_batter": 0,
          "batter_clipped_shared_corridor": 0,
          "batter_clipped_over_clamp": 0,
          "batter_clipped_reach": 0,
          # THE CAP'S TWO POPULATIONS, SEPARATED.  See REACH_EXIT_DEPENDS_ON:
          # `reach` is a ray the cap stopped while it was still closing, and
          # `reach_not_closing` is one that had closed nothing by that cap.
          # The second is a measurement OF THE CAP and not of the hillside --
          # reading it as permanent is a mistake that has already been made
          # once here, and all 99 of them closed by 24.0 m.
          "batter_clipped_reach_not_closing": 0,
          # THE RUN A CONVERGING RAY ACTUALLY NEEDED, from the carriageway
          # edge, at the rate it was MEASURED closing at.  This is the number
          # a reach constant has to be derived against, and without it the
          # only way to move the cap is to guess.
          "reach_needed_max_m": 0.0,
          "reach_needed_at": None,
          "reach_needed_m": [],
          # TWO BRANCHES OF THIS RIBBON OVER ONE CELL, AND WHETHER THE GROUND
          # BETWEEN THEM CAN ABSORB THE DISAGREEMENT.  See
          # `SECOND_CLAIMANT_DEPENDS_ON`: the corridor gate is asked through
          # `road_claim_at`, which returns None for `my_name`, so a segment's
          # own second branch is invisible to it.  Counted AND CLIPPED here
          # rather than going unnamed behind an empty cause list.
          #
          # `self_corridor_inband_cells` is the DENOMINATOR and it is not
          # decoration: "3 cells" and "3 of 41,845" are different findings,
          # and the count alone was read as the second one for a whole
          # session while it was the first.
          "self_corridor_inband_cells": 0,
          "self_corridor_cells": 0,
          "self_corridor_worst_m": 0.0,
          "self_corridor_worst_at": None,
          "self_corridor_worst_pair": None,
          # THE TWO-CLAIM POPULATION, SEPARATELY FROM THE INFEASIBLE ONE.
          # `self_corridor_cells` divided by `self_corridor_inband_cells`
          # answers "how much of this road stands beside itself
          # IRRECONCILABLY"; it cannot answer "how much of it stands beside
          # itself at all", and a detector that fires on a tiny fraction of
          # a population it never counted is the shape this file has been
          # caught in twice.
          "self_corridor_two_claim_cells": 0,
          # ...AND HOW MANY OF THE INFEASIBLE ONES ARE CARRIAGEWAY, which is
          # the sub-population NO earthwork can close: a cell paved by one
          # branch has a band of ZERO WIDTH, so the only remedy is a route.
          "self_corridor_carriageway_cells": 0,
          # WHAT THE JOINT BAND ACTUALLY MOVED.  A reconciliation that
          # changes nothing and a reconciliation that is never asked are
          # indistinguishable from the refusal count alone.
          "self_corridor_reconciled": 0,
          "self_corridor_reconciled_raised": 0,
          "self_corridor_reconciled_lowered": 0,
          "self_corridor_reconciled_worst_m": 0.0,
          "self_corridor_refused_samples": 0,
          "batter_clipped_placed_at": [],
          "batter_met_ground": 0,
          # GATE 4: A WALL THAT COULD NOT BE GRADED IS RECORDED, WITH ITS
          # HEIGHT AND ITS REASON.  A recorded wall the operator can be warned
          # about beats a forced write beside a bridge pile, and a clip that
          # leaves no trace is indistinguishable from a batter that worked.
          "walls_left": [], "walls_left_max_m": 0.0, "clip_at": [],
          # EVERY CARRIAGEWAY REFUSAL'S POSITION AND CAUSE, uncapped.  The
          # counters beside them say HOW MANY samples the road could not
          # claim; they cannot say WHERE the road therefore stops, and that is
          # the only question a gap report can be answered from.  MEASURED on
          # T4: 47.0 m of centreline in zone (8,7) is refused because
          # `tree-sth-2` seq 669 owns all 1,126 written samples in that zone,
          # and with no position recorded `walkability` could only call an
          # explained stop a HOLE -- refusing a segment whose ground is
          # somebody's foundation, which is the one case the ruling says the
          # road must accept and grade into.
          "road_clip_at": [],
          # EVERY SAMPLE OF EVERY RAY THAT WAS SKIPPED FOR HAVING NO ROAD, in
          # the same `(x, z, cause, height)` shape the clip list uses, so the
          # transverse census can tell a recorded residual from a bug.
          "ray_skip_at": [],
          # EVERY RIM CELL A GATE FORBADE `close_rim` TO LAY, same shape, so
          # the rim owner names that gate (RIM_STEP_OWNER_DEPENDS_ON).
          "closure_refused_at": [],
          # WHERE EVERY RAY ENDED, as `(station_arc_m, side, lat_m, cause)`.
          # THE TERMINATION LOCUS IS A FIRST-CLASS MEASUREMENT because an
          # `ADJACENT` run of unexplained over-grade steps has two readings and
          # only this series tells them apart: rays that end at smoothly
          # varying laterals mean the hillside itself is adjacent and the
          # clips are honest, while rays that end at materially different
          # laterals mean the LOCUS OF TERMINATIONS is a new cliff running
          # parallel to the road -- the same family as the edge wall the
          # batter exists to repair.  It has to be recorded HERE, per ray,
          # because a ray is the unit that terminates: reconstructing it by
          # bucketing written samples into 1 m station bins measures lattice
          # aliasing instead, and MEASURED on T12 that reads a 5.611 m
          # "sawtooth" at station 268 where the ray series is smooth.
          # `met_ground` rays record nothing else at all, so without this the
          # 69 % of terminations that are the batter working as designed are
          # invisible to the test.
          "ray_end_at": [],
          "walls_before_max_m": 0.0,
          "walls_left_by_cause": {},
          "rays": 0, "rays_clipped": 0,
          "rays_without_road": 0,
          # ...AND ITS MIRROR, WHICH NOTHING COUNTED AT ALL.  `rays_without
          # _road` is a ray that exists and has no carriageway to taper from.
          # THIS is a carriageway that exists and has no ray: every cell
          # outboard of its edge was handed to another branch of this same
          # ribbon by `nearest wins`, so the wall at that edge was laid down
          # by nobody and reported by nobody.  See the block that walks them.
          "stations_road_without_ray": 0,
          "stations_road_without_ray_unwalkable": 0,
          "orphan_rays_walked": 0,
          # SAMPLES THE WATER RULE REFUSED AND THE RAY WALKED PAST.  Counted
          # separately from `rays_clipped` because they are not the same
          # event: the sample is still refused and still carries its wall in
          # `walls_left_by_cause`, but the hillside outboard of it is still
          # this ray's to finish.
          "water_fill_skipped": 0,
          # A RAY WHOSE OWN SAMPLE LIST ENDED INSIDE ITS CAP, AND WHAT IT
          # THEN DID.  It used to stop there and be counted as
          # `rays_bin_exhausted` on the premise that "the ground outboard of
          # it at this arc belongs to a neighbouring ray and is written
          # there".  MEASURED FALSE on Fresh42, 2026-09-24: of T1's 27
          # bin-exhausted rays, 27 have UNWRITTEN in-band ground outboard of
          # their last member, and the ray at (-130, -308) is the taken side
          # of the 1.0875 m step `rim_transfer` refused the whole write for.
          # A ray now WALKS ITS OWN RADIAL to one of the named exits.
          "rays_continued": 0,
          "samples_batter_continued": 0,
          "samples_batter_in_pad_standoff": 0,
          "interstice_rays": 0, "interstice_apron": 0,
          "interstice_left_ungraded": 0, "interstice": [],
          "interstice_taper_closes_first": 0,
          "interstice_taper_closes_first_at": [],
          "samples_apron": 0,
          "max_cut_m": 0.0, "max_fill_m": 0.0, "over_clamp": 0,
          "over_clamp_samples": [], "per_zone": {},
          "batter_grade": round(batter_grade, 4), "batter_max_m": batter_m,
          "edge_m": edge, "edge_eps_m": EDGE_EPS_M,
          "edge_exact_samples": 0,
          # FOREIGN_BATTER_DEPENDS_ON: cells of ANOTHER road's batter this
          # write laid its own plane over, and cells where that plane left
          # the other road's band (clipped `shared_corridor`).
          "foreign_batter_written": 0, "foreign_batter_out_of_band": 0,
          "foreign_batter_out_of_band_worst_m": 0.0,
          "foreign_batter_out_of_band_at": None}
    # The world positions of every sample this write actually TOUCHES.  The
    # location check is run against these, not against zone centres -- see
    # `location_check`.
    written: list[tuple[float, float]] = []
    # THE SAME SET, KEYED ON THE LATTICE, because a continued ray has to be
    # able to ask "has anybody already laid this cell" in O(1) and must never
    # overwrite a neighbour's answer or step back onto the carriageway.
    written_set: set[tuple[int, int]] = set()
    # CARRIAGEWAY samples only.  `location_check`'s distance rule budgets for
    # the ROAD's own geometry (`half_width_m`), and a batter sample is not
    # road: including it would both overstate the road's width and make an
    # 11 m-wider earthwork look like an 11 m-wider carriageway to the gate.
    # The batter's real hazard -- moving a piece's ground -- is measured
    # directly by `delta_gate` over the whole compiler set, which sees every
    # sample regardless of which list it is in.
    road_written: list[tuple[float, float]] = []
    # EVERY BATTER SAMPLE'S LATTICE CELL AND THE GROUND UNDER IT, for the toe
    # feather.  Batter ONLY: the carriageway is the road surface and is never
    # feathered, so it goes in `road_cells` and stays fixed.  See
    # TOE_FEATHER_DEPENDS_ON.
    batter_at: dict[tuple[int, int], tuple[int, int, int, int, float]] = {}
    road_cells: set[tuple[int, int]] = set()
    reach = edge + batter_m

    def foreign_road(wx: float, wz: float):
        """ANOTHER road's claim on this cell, read as FOREIGN_BATTER_DEPENDS_ON
        reads it, or None: absent, or the neuter restoring the shipped rule."""
        if not foreign_batter_is_ground or foreign_road_at is None:
            return None
        return foreign_road_at(wx, wz)

    def foreign_out_of_band(fr, target: float) -> float:
        """How far `target` stands outside the other road's band (0 inside)."""
        return max(fr["lo"] - target, target - fr["hi"], 0.0)

    def refusal(wx: float, wz: float, generated: float,
                is_road: bool, delta: float) -> str | None:
        """The one reason this sample cannot be written, or None.

        Ordered cheapest-and-hardest first.  The order is not cosmetic: a
        sample inside a pad is the pad's whatever else is true of it, and
        recording it under a softer cause would misattribute the wall.

        `delta` is the move THIS sample would make -- computed
        by the caller, which is the only place the target height is known --
        and it is what the water gate is keyed on.  Every caller passes it
        honestly even where the band short-circuits before it is read, because
        a parameter that is sometimes a lie is worse than no parameter.
        """
        # THE FOUNDATION STOPS BOTH BANDS, and the asymmetry that used to live
        # here was an artefact of what the keep-out USED to be.
        #
        # It was a PLANNING STAND-OFF -- a district or nominal circle, 100 m at
        # a town, 16.1 m at a watchtower -- and refusing the batter inside
        # something that large left the road's own wall standing far from any
        # building: MEASURED on T12, 4.1 m of it once the approach ramped up.
        # So the batter was allowed to taper into the stand-off and only the
        # carriageway was refused.
        #
        # THE KEEP-OUT IS NOW THE FOUNDATION ITSELF -- each pad's rectangle and
        # the samples that pad actually levelled -- and for a foundation the
        # ruling has no asymmetry in it: inside a per-building pad the pad
        # wins, because it is a building's footing.  MEASURED when this still
        # read `is_road`: T4's batter authored 28 samples inside
        # `stenvik-house-3`'s 20.3 x 15.2 m rectangle and 3 on ground
        # `stenvik-cottage-2` had levelled 0.1 m outside its own rectangle, and
        # `writer._pad_footprint_check` REFUSED the whole write at append time
        # -- it does not care which band a sample came from.  A rasteriser that
        # writes what the guard will refuse is not a rasteriser, it is a
        # 47-second way of finding out.
        #
        # The wall this used to prevent is now prevented by the thing that
        # should prevent it: the approach ramp grades the road to the pad's own
        # datum at its EDGE (`grade_into_foreign`), which is the ruling's other
        # half and is measured per pad in the junction report.
        if pad_keepouts.contains(wx, wz) is not None:
            return "pad"
        if any(abs(wx - px) <= PROTECT_CLEAR_M
               and abs(wz - pz) <= PROTECT_CLEAR_M
               for px, pz in (protected_pieces or ())):
            return "protected"
        if generated < WATER_LEVEL_M and any(
                math.hypot(wx - px, wz - pz) <= pr
                for px, pz, pr in (protected_discs or ())):
            return "underwater"
        if foreign_at is not None and foreign_at(wx, wz) is not None:
            return "foreign"
        if is_road:
            return None
        # --- batter-only gates ------------------------------------------
        # THE WATER RULE, AND IT IS `batter_water_gate` SO THE SELFTEST DRIVES
        # THE SHIPPING RULE.  It used to read `if generated < WATER_LEVEL_M`,
        # unconditionally, on the premise that a batter can only ever cut --
        # see the constant's own comment for the 70/70 measurement that killed
        # that premise -- and then permitted EVERY raise, which is how 206 of
        # T1's batter samples reached `water_sign` as drained water.  It is
        # keyed on the RESULTING height now.
        cause = batter_water_gate(generated, delta)
        if cause is not None:
            return cause
        # THE WIDENED FOOTPRINT RE-CHECKED AGAINST POI CLEARANCE.  The
        # ribbon's stand-offs were computed for a 7 m strip and a 38 deg
        # batter off an 8 m wall is ~10 m more per side, so they do not
        # transfer -- they are re-asked here, per sample, against each
        # instance's own `reach + MARGIN_M (+ PROTECTED_EXTRA_M)`.
        if any(math.hypot(wx - px, wz - pz) <= pr
               for px, pz, pr in (poi_keepouts or ())):
            return "poi"
        # A LIVE PLACED OBJECT'S GROUND MUST NOT MOVE EITHER, and this gate
        # is on the BATTER alone by construction: the carriageway's deltas
        # are unchanged by this repair, so they cannot newly float anything,
        # while every batter sample is ground that has never moved before.
        if any(abs(wx - px) <= PLACED_CLEAR_M
               and abs(wz - pz) <= PLACED_CLEAR_M
               for px, pz in (placed_objects or ())):
            return "placed"
        return None

    # ---- pass 1: the carriageway, and the batter collected into rays -----
    #
    # The carriageway is written here and now because its policy is per
    # sample: a refused carriageway sample is a recorded GAP that
    # `walkability` judges, and nothing about it depends on its neighbours.
    # The batter cannot be decided here, because whether a sample may be
    # written depends on every sample INBOARD of it on the same ray -- and
    # rays cross zone boundaries, so the decision cannot even be made one
    # zone at a time.
    rays: dict[tuple[int, int], list] = {}
    # NO BATTER WHERE THERE IS NO ROAD.  A station whose carriageway is
    # refused has no edge to taper from, and grading a 10 m apron around a
    # road that was never written is earthwork for nothing -- worse, on the
    # far side of a pad boundary it is a 10 m skirt around empty ground.
    road_stations: set[tuple[int, int]] = set()
    dead_stations: set[tuple[int, int]] = set()
    road_foot: dict[tuple[int, int], tuple] = {}
    # THE FILL FLOOR EVERY OTHER RUN IMPOSES, AT EVERY IN-BAND CELL, so the
    # walk along a radial past the bin asks the same question the bin walk
    # asks.  Absent where no other run of this polyline reaches, which on a
    # road that never comes back on itself is every cell.
    floor_at: dict[tuple[int, int], float] = {}
    # THE FOOT THAT CLAIMS EVERY IN-REACH CELL, `(lat, road y)`, for the rim
    # closure's band.  See RIM_CLOSURE_DEPENDS_ON.
    claim_of: dict[tuple[int, int], tuple[float, float]] = {}
    # ...AND, FOR THE CLOSURE ONLY, THE SQUARE END OF A RUN AT A BANK: the
    # cells past it no run claims.  See RIM_BANK_END_DEPENDS_ON.
    ends = (bank_ends(nodes, prof, br) if _RIM_BANK_END_MODE == "claimed"
            else [])
    grids: dict[tuple[int, int], tuple] = {}
    ax_1d = (np.arange(tcdata.PITCH) - tcdata.PITCH // 2) * tcdata.SCALE
    for zx, zz in zones:
        patch = patches[f"z_{zx}_{zz}"]
        comp = tcdata.Compiler(zone_x=zx, zone_z=zz)
        zcx, zcz = comp.centre
        # The zone's own sample centres, exactly `tcdata.sample_world` with gx
        # varying along the row and gy down the column.
        X = (zcx + ax_1d)[None, :] + np.zeros((tcdata.PITCH, 1))
        Z = (zcz + ax_1d)[:, None] + np.zeros((1, tcdata.PITCH))
        LAT, Y, S, SGN, FX, FZ, LAT2, Y2, FLOOR = lateral_and_y_grid(
            nodes, prof, br, X, Z, reach, BATTER_GRADE, edge)
        comps[(zx, zz)] = comp
        if ends:
            RUN_E, Y_E = bank_end_run(ends, X, Z, edge)
            for gy, gx in np.argwhere((LAT > reach) & (RUN_E <= batter_m)):
                claim_of[(int(round(float(X[gy, gx]))),
                          int(round(float(Z[gy, gx]))))] = (
                    edge + float(RUN_E[gy, gx]), float(Y_E[gy, gx]))
        inband = np.argwhere(LAT <= reach)
        for gy, gx in inband:
            gy, gx = int(gy), int(gx)
            wx, wz = float(X[gy, gx]), float(Z[gy, gx])
            lat, y = float(LAT[gy, gx]), float(Y[gy, gx])
            claim_of[(int(round(wx)), int(round(wz)))] = (lat, y)
            # TWO BRANCHES OF THE SAME RIBBON OVER ONE CELL.  `nearest wins`
            # hands this cell to one of them and the other is then invisible
            # to every gate, including the corridor gate that exists for
            # exactly this and is asked through `road_claim_at`, which
            # returns None for `my_name`.
            #
            # IT IS CLIPPED NOW, NOT MERELY COUNTED, and the reason is
            # MEASURED: 60 of the 71 caused rim steps across the five
            # rim-refusing Fresh42 segments have their two samples claimed by
            # feet 9 to 30 m APART ALONG THE POLYLINE, and on four of those
            # five segments EVERY ray exits `met_ground` -- the SUCCESS exit.
            # T6-stathub-northcape's worst is the shape in miniature: the
            # taken cell (154, 505) is CARRIAGEWAY at arc 233.6 and height
            # 75.311, the rim cell (155, 506) one diagonal away is the arc
            # 253.85 branch's FIRST batter cell at road height 73.604, and
            # that ray met ground at its first sample and recorded success.
            # Nothing in the run said a word about the 1.7 m face between two
            # carriageways of one road standing 1.4 m apart.  A wall with no
            # cause is indistinguishable from no wall.
            # `shared_corridor` is the SAME cause the foreign case uses,
            # because it is the same finding: two claims on one cell whose
            # planes the surface cannot both carry.
            #
            # AND THE KEY IS THE ONE THE SURFACE IS ACTUALLY BOUND BY: THE
            # TWO CLAIM BANDS, NOT THE TWO PLANES AND NOT THE TWO PROFILES.
            # This has now been wrong twice and each author caught his own.
            #
            # Keyed on `|y1 - y2|` against the gap between the two claims it
            # fired on 34,615 of T4-meadhall-stathub's 55,212 in-band cells
            # -- 63 % of the lattice -- at cells where BOTH PLANES HAD MET
            # GROUND EIGHTEEN METRES EARLIER.  Keyed on the two sign-chosen
            # PLANES against ONE LATTICE EDGE of slide it reported a number
            # that could not be acted on: a plane is what one branch would
            # lay, and a cell is refused only when there is NO height at all
            # that both branches can live with.
            #
            # THAT SET IS THE INTERSECTION OF THE TWO BANDS -- see
            # BATTER_TARGET_DEPENDS_ON -- and it is empty by exactly
            #
            #   |y1 - y2| - BATTER_GRADE * (run1 + run2)
            #
            # with `run_i = max(lat_i - edge, 0)`.  There is NO GROUND TERM
            # in it, which is the whole difference: whether two carriageways
            # can be joined by an earthwork is a fact about where they were
            # ROUTED, and the ground under them cannot make an impossible
            # pair possible or a possible pair impossible.  Everything the
            # bands DO admit is reconciled by `reconcile_target` at the write
            # instead of being counted here.
            generated = float(patch.at(gy, gx))
            full = y - generated
            lat2 = float(LAT2[gy, gx])
            st["self_corridor_inband_cells"] += 1
            # THE NEUTER LEAVES THE POPULATION COUNTED AND THE SURFACE
            # UNRECONCILED, which is the state this shipped in.  A neuter
            # that also emptied the count would make that state look clean.
            if joint_claim_band_on:
                fl = float(FLOOR[gy, gx])
                if fl > -np.inf:
                    floor_at[(int(round(wx)), int(round(wz)))] = fl
            if lat2 <= reach:
                y2 = float(Y2[gy, gx])
                st["self_corridor_two_claim_cells"] += 1
                lo1, hi1 = claim_band(y, lat, edge, batter_grade)
                lo2, hi2 = claim_band(y2, lat2, edge, batter_grade)
                over = max(lo1, lo2) - min(hi1, hi2)
                if over > 0.0:
                    st["self_corridor_cells"] += 1
                    paved = lat <= edge + EDGE_EPS_M
                    if paved:
                        st["self_corridor_carriageway_cells"] += 1
                    if over > st["self_corridor_worst_m"]:
                        st["self_corridor_worst_m"] = round(over, 4)
                        st["self_corridor_worst_at"] = [round(wx, 1),
                                                        round(wz, 1)]
                        st["self_corridor_worst_pair"] = {
                            "my_road_y": round(y, 3),
                            "their_road_y": round(y2, 3),
                            "my_lat_m": round(lat, 3),
                            "their_lat_m": round(lat2, 3),
                            "my_band": [round(lo1, 3), round(hi1, 3)],
                            "their_band": [round(lo2, 3), round(hi2, 3)],
                            "ground_y": round(generated, 3),
                            "profile_diff_m": round(abs(y - y2), 3),
                            "run_the_grade_has_m": round(
                                max(lat - edge, 0.0)
                                + max(lat2 - edge, 0.0), 3),
                            "carriageway": paved,
                            "grade_per_m": round(batter_grade, 4)}
                    # A PAVED CELL IS COUNTED AND NOT CLIPPED, and the
                    # distinction is the difference between a finding and a
                    # flood.  Two carriageways over one cell is a ROUTE that
                    # cannot be built and no earthwork can answer it; the
                    # batter never writes that cell in the first place, so a
                    # wall clip there names a face the batter did not leave.
                    # MEASURED, Fresh42, 14 segments: clipping them put 3,367
                    # of T4-meadhall-stathub's 3,403 refusals and 5,970 of
                    # T9-wttown-dockshore's 6,318 into the wall census, which
                    # buries the batter's own refusals under the route's.
                    if self_corridor_clips_on and not paved:
                        _clip(st, "shared_corridor", False, None, wx, wz,
                              lat, over, residual=over)
            # THE EDGE IS INCLUSIVE.  See EDGE_EPS_M: without the tolerance
            # the sample landing exactly on `edge` falls out of the
            # carriageway on a float ULP and keeps its generated height,
            # which is the 1 m wide, 1.76 m deep trench RoadClear measured at
            # T12 s = 116 m.
            if lat <= edge + EDGE_EPS_M:
                if lat > edge - EDGE_EPS_M:
                    st["edge_exact_samples"] += 1
                key = (int(SGN[gy, gx]), int(round(float(S[gy, gx]))))
                cause = refusal(wx, wz, generated, True, full)
                if cause is not None:
                    _clip(st, cause, True, f"skipped_{cause}",
                          wx, wz, lat, full)
                    dead_stations.add(key)
                    dead_stations.add((-key[0], key[1]))
                    continue
                road_stations.add(key)
                road_stations.add((-key[0], key[1]))
                # THE STATION'S OWN FOOT AND OUTWARD DIRECTION, kept from the
                # carriageway sample FURTHEST from the centreline on this
                # side.  Without it a station whose whole outboard lattice
                # was taken by another branch of this same ribbon has no ray,
                # no foot and no record -- see `stations_road_without_ray`.
                # The furthest sample is used because the direction is
                # `(sample - foot) / lat` and a sample ON the centreline has
                # no direction at all.
                if lat > EDGE_EPS_M:
                    prev = road_foot.get(key)
                    if prev is None or lat > prev[0]:
                        road_foot[key] = (lat, float(FX[gy, gx]),
                                          float(FZ[gy, gx]), wx, wz, y)
                comp.set_height(gx, gy, full)
                comp.set_paint(gx, gy, road_colour
                               if lat <= half + EDGE_EPS_M
                               else shoulder_colour)
                written.append((wx, wz))
                written_set.add((int(round(wx)), int(round(wz))))
                road_written.append((wx, wz))
                road_cells.add((int(round(wx)), int(round(wz))))
                st["samples_paved" if lat <= half + EDGE_EPS_M
                   else "samples_shoulder"] += 1
                st["max_cut_m"] = min(st["max_cut_m"], full)
                st["max_fill_m"] = max(st["max_fill_m"], full)
                _clamp_check(st, full, zx, zz, gx, gy, wx, wz, generated, y,
                             "carriageway")
                continue
            # THE RAY KEY CARRIES THE BEARING, AND WITHOUT IT A VERTEX FAN
            # TERMINATED ITSELF.  `(side, int(round(arc)))` is a STATION BIN.
            # Where the nearest point on the centreline is a VERTEX -- every
            # corner of the polyline -- the projection collapses: every
            # lattice sample in the wedge around that corner gets the SAME
            # foot and the SAME arc, so one bin holds a whole fan of different
            # BEARINGS.  `members.sort()` then walks them by radius, and a
            # `break` taken for one bearing stops every other bearing in the
            # wedge.
            #
            # MEASURED on T1-portalhub-temple, Fresh42, 2026-09-24, ray
            # (-1, 142) at the corner whose foot is (-124.17, -310.71):
            # (-117,-311) at lat 7.18 and (-119,-316) at lat 7.28 share that
            # foot and that bin and are 5.4 m apart on the ground.  The plane
            # met ground on the second at lat 7.28, so the walk BROKE, and
            # (-116,-311) at lat 8.18 -- which still owed 1.64 m of cut -- was
            # never written.  That is the 2.4128 m rim step the gate refused
            # the whole write for, and the batter did not clip it: the KEY
            # threw it away.
            #
            # The bearing is binned at the lattice's own width MEASURED AT THE
            # REACH -- `theta * reach` rounded to 1 m -- so an angular bin is
            # the same physical size as an arc bin, derived rather than
            # chosen.  On a straight run every sample in a station bin has the
            # foot's exact perpendicular as its bearing, so the extra
            # component is constant and the key is the old key.
            station = (int(SGN[gy, gx]), int(round(float(S[gy, gx]))))
            fx_, fz_ = float(FX[gy, gx]), float(FZ[gy, gx])
            bearing = (int(round(math.atan2(wz - fz_, wx - fx_) * reach))
                       if ray_key_carries_bearing else 0)
            rays.setdefault((station, bearing), []).append(
                (lat, zx, zz, gx, gy, wx, wz, y, generated, fx_, fz_))

    # ---- pass 2: each ray, outward, once -------------------------------
    # RAYS WHOSE BIN RAN OUT, HELD BACK UNTIL EVERY BIN HAS BEEN WALKED.
    # The premise this repairs is "the ground outboard belongs to a
    # neighbouring ray and is written there", and the only honest way to
    # answer it is to LOOK -- which means after the neighbours have run.
    pending: list[tuple] = []
    for (key, _bearing), members in rays.items():
        members.sort()
        # THE STATION decides whether there is road to taper from; the BEARING
        # decides which taper this is.  `road_stations` is a station question
        # and stays keyed on the station alone.
        if key not in road_stations or key in dead_stations:
            st["rays_without_road"] += 1
            # AND WHERE, AND HOW DEEP, because the counter alone hides a wall.
            # A ray whose carriageway was refused is skipped -- correct, there
            # is no edge to taper from -- but the NEIGHBOURING station's batter
            # still ends at this ray's boundary, and the ground on this side is
            # untouched.  MEASURED on T4 at (518.8, 640.5): 1.826 m applied
            # against 0.362 m generated, one metre from a written batter sample
            # at the same lateral.  With nothing recorded, the transverse
            # census can only call that step UNEXPLAINED, which is the same
            # word it uses for a bug.  `full` is the cut or fill the profile
            # asks for at this station: the wall the road WOULD have had here,
            # which is the bound on what the neighbour's taper can leave.
            for lat_, _zx, _zz, _gx, _gy, wx_, wz_, y_, gen_, _fx, _fz in members:
                st["ray_skip_at"].append(
                    (wx_, wz_, "ray_without_road", round(abs(y_ - gen_), 3)))
            continue
        st["rays"] += 1
        lat0, zx0, zz0, _, _, _, _, y0, _, fx0, fz0 = members[0]
        # THE WALL AT THE HEAD OF THIS RAY, which is what the batter has to
        # beat and what bounds how much earth it may move.  Taken at the
        # carriageway EDGE rather than at the first batter sample, because
        # that is where the operator steps off.
        ux, uz = (members[0][5] - fx0) / lat0, (members[0][6] - fz0) / lat0
        ex, ez = fx0 + ux * edge, fz0 + uz * edge
        gen_edge = appliedmod.generated_at(patches, ex, ez)
        if gen_edge is None:
            gen_edge = members[0][8]
        full_edge = y0 - gen_edge
        if abs(full_edge) <= EDGE_EPS_M:
            # Road at grade with the ground: there is no wall here, so there
            # is nothing to batter and no reach to spend.
            continue
        sign = 1.0 if full_edge > 0 else -1.0
        budget = abs(full_edge)

        # ---- ONE CORRIDOR OR TWO ROADS?  Main's ruling, and it is a GATE
        # before it is a nicety.  Where another road segment's carriageway
        # lies inside this ray's batter reach, the two roads are ONE CORRIDOR:
        # a 0.781 plane off a 2 m wall needs 2.6 m per side and off a 5 m wall
        # 6.4 m, so two tapers plus two carriageways cannot fit in a 6 m gap.
        # MEASURED on T12: T3 runs 6-10 m away near the temple with its
        # carriageway edge 1.18 m off its own centreline, so an ungated batter
        # would TAPER INTO A FINISHED ROAD -- cutting up to 2.2 m out of T3's
        # surface and leaving a trench between the pair.  So:
        #   * if the two road profiles are within grade across the gap, the
        #     interstice is laid as the TAPER from one edge to the other
        #     (INTERSTICE_APRON_DEPENDS_ON; it was levelled flush at the
        #     higher of the two, which leaves the difference as one step);
        #   * if they are not, the interstice is LEFT ALONE with the step
        #     recorded and both profiles named -- an honest ditch beats a
        #     forced one, and the outside batter is unaffected either way.
        #
        # "INSIDE THIS RAY'S BATTER REACH" NAMED A SUPERSET, and the cap made
        # it a large one.  The premise is two tapers colliding, which is a
        # fact about where THIS ray's plane is still OPEN; the search ran to
        # `edge + batter_m`, so when the cap went 10.5 -> 21.0 m every road
        # within 25 m became "one corridor" whether or not this taper would
        # ever reach it.  See INTERSTICE_DEPENDS_ON.
        claim = None
        bounded_by = None
        if road_claim_at is not None:
            for m in members:
                if m[0] > edge + batter_m:
                    break
                got = road_claim_at(m[5], m[6])
                if got is not None:
                    # ANOTHER ROAD'S BATTER IS GROUND, walked below; only
                    # its CARRIAGEWAY is a corridor partner.  See
                    # FOREIGN_BATTER_DEPENDS_ON.
                    fr = foreign_road(m[5], m[6])
                    if fr is not None and not fr["paved"]:
                        continue
                    claim = (m[0], m[8] + got[0], got[1])
                    break

        def _ground_of(m) -> float:
            """The ground a member's plane meets: the other road's surface
            over its batter, natural ground everywhere else."""
            fr = foreign_road(m[5], m[6])
            if fr is not None and not fr["paved"]:
                return fr["surface"]
            return m[8]
        if claim is not None and interstice_needs_open_taper and any(
                batter_met_ground(
                    (m[7] - sign * batter_grade * (m[0] - edge))
                    - _ground_of(m), sign)
                for m in members if m[0] < claim[0]):
            # THIS TAPER CLOSES SHORT OF THE OTHER ROAD: two roads, each with
            # its own batter.  The walk is the ordinary one, BOUNDED at the
            # claim so it never writes the other road's ground.
            st["interstice_taper_closes_first"] += 1
            if len(st["interstice_taper_closes_first_at"]) < 20:
                st["interstice_taper_closes_first_at"].append(
                    {"xz": [round(members[0][5], 1), round(members[0][6], 1)],
                     "claim_lat_m": round(claim[0], 2),
                     "their_claim": claim[2]})
            bounded_by = claim[0]
            claim = None
        if claim is not None:
            clat, their_y, their_name = claim
            gap = max(clat - edge, 1e-6)
            diff = y0 - their_y
            st["interstice_rays"] += 1
            flush = max(y0, their_y)
            fits = abs(diff) <= batter_grade * gap
            rec = {"xz": [round(members[0][5], 1), round(members[0][6], 1)],
                   "gap_m": round(gap, 2), "my_road_y": round(y0, 2),
                   "their_road_y": round(their_y, 2),
                   "their_claim": their_name,
                   "profile_diff_m": round(diff, 2),
                   "grade_can_absorb_m": round(batter_grade * gap, 2),
                   "action": ("left_ungraded" if not fits else "tapered"
                              if _INTERSTICE_APRON_MODE == "taper"
                              else "levelled_flush")}
            if len(st["interstice"]) < 40:
                st["interstice"].append(rec)
            if fits:
                st["interstice_apron"] += 1
                for lat, zx, zz, gx, gy, wx, wz, y, generated, _fx, _fz in members:
                    if lat >= clat:
                        break
                    # THE APRON'S OWN MOVE, computed BEFORE the gate because
                    # the water rule is keyed on its sign.  It was already
                    # computed twice below; now it is computed once, above.
                    # See INTERSTICE_APRON_DEPENDS_ON for the target.
                    target = (y0 + (their_y - y0) * min(max(
                        (lat - edge) / gap, 0.0), 1.0)
                        if _INTERSTICE_APRON_MODE == "taper" else flush)
                    delta = target - generated
                    cause = refusal(wx, wz, generated, False, delta)
                    if cause is not None:
                        _clip(st, cause, False, None, wx, wz, lat, full_edge,
                              residual=delta)
                        break
                    if abs(delta) > max(budget, abs(diff)) + tcdata.CLAMP_M * 0.0 + EDGE_EPS_M:
                        _clip(st, "steeper_than_batter", False, None, wx, wz,
                              lat, full_edge, residual=delta)
                        break
                    comps[(zx, zz)].set_height(gx, gy, delta)
                    written.append((wx, wz))
                    written_set.add((int(round(wx)), int(round(wz))))
                    batter_at[(int(round(wx)), int(round(wz)))] = (
                        zx, zz, gx, gy, generated)
                    st["samples_batter"] += 1
                    st["samples_apron"] += 1
                    st["max_cut_m"] = min(st["max_cut_m"], delta)
                    st["max_fill_m"] = max(st["max_fill_m"], delta)
                    _clamp_check(st, delta, zx, zz, gx, gy, wx, wz, generated,
                                 target, "apron")
            else:
                st["interstice_left_ungraded"] += 1
                _clip(st, "shared_corridor", False, None,
                      members[0][5], members[0][6], members[0][0], full_edge,
                      residual=full_edge)
            continue

        # THE RAY'S OWN TERMINATION, recorded at whichever of the five exits
        # it takes.  `end` is the lateral the earthwork reaches, which for a
        # clipped ray is the REFUSED sample (the last one written is inside
        # it) and for a ray that met ground is where the plane arrived.
        #
        # `lattice_end` is the SENTINEL for "no exit has been taken yet" and
        # is never the recorded answer: the block after this loop resolves it
        # into `reach` or `bin_exhausted`.  It used to BE the answer, for 1 to
        # 90 rays per segment, because the reach exit that belongs here was
        # gated on `lat - edge > batter_m` while the member list is built from
        # `LAT <= edge + batter_m` -- the same bound, so no member could ever
        # satisfy it.  A dead guard reads as a live one, so the branch was
        # deleted rather than repaired in place: the reach is spent when the
        # MEMBER LIST ends at the cap, which is a fact about the last member
        # and is therefore decided once, after the walk, instead of tested
        # against every sample on every ray.
        if bounded_by is not None:
            members = [m for m in members if m[0] < bounded_by]
        end_lat, end_cause = members[-1][0], "lattice_end"
        # WHERE THE PLANE WAS LAST ABOVE GROUND ON THIS RADIAL -- the TOE --
        # and whether it has crossed at all.  See BATTER_MET_GROUND_DEPENDS_ON:
        # a crossing is a fact about ONE SAMPLE and the toe is a fact about the
        # RADIAL, and the ray used to end on the first of those.
        crossed_lat = None
        for lat, zx, zz, gx, gy, wx, wz, y, generated, _fx, _fz in members:
            run = lat - edge
            # ANOTHER ROAD'S CLAIM ON THIS CELL: its carriageway ends the
            # ray, its batter is the ground this plane meets.  See
            # FOREIGN_BATTER_DEPENDS_ON.
            fr = foreign_road(wx, wz)
            if fr is not None and fr["paved"]:
                end_lat, end_cause = lat, "another_road"
                break
            surf = generated if fr is None else fr["surface"]
            # THE PLANE AT GRADE AND THIS SAMPLE'S OWN MOVE, computed BEFORE
            # the gates because the water rule is keyed on the SIGN of that
            # move.  `target` and `delta` were computed twice in this body
            # before; now they are computed once, here, and reused.
            target = y - sign * batter_grade * run
            # ...AND THEN HELD INSIDE EVERY OTHER CLAIM'S BAND, because a
            # cell inside two claims' reach owes the slide guarantee to BOTH
            # of them and the surface is single-valued.  See
            # BATTER_TARGET_DEPENDS_ON: without this the plane is a function
            # of THIS ray alone, every ray closes along its own radial, and
            # the step ACROSS two rays is bounded by nothing at all.
            floor_c = floor_at.get((int(round(wx)), int(round(wz))),
                                   -math.inf)
            plane = target
            target, sign_s, unabsorbable = reconcile_target(
                target, generated, sign, y, lat, floor_c, edge, batter_grade)
            if unabsorbable > 0.0:
                # NO HEIGHT EXISTS THAT BOTH BRANCHES CAN LIVE WITH.  Refused
                # BY NAME with the number, and it does not end the ray: one
                # cell of an impossible pair says nothing about the hillside
                # outboard of it, exactly as `met_ground` does not.
                st["self_corridor_refused_samples"] += 1
                continue
            _count_reconciled(st, plane, target, generated, sign, sign_s)
            delta = target - generated
            # UNDER GROUND HERE -- SKIP IT, DO NOT END THE RAY.  Nothing may
            # be written where the plane has gone below natural ground: that
            # is the disconnected trench `selftest_batter_ray_end` neuter B
            # cuts, 530 samples beyond the toe.  Asking a gate first would
            # also mislabel a finished taper as a refusal, with a `residual`
            # that is the plane's OVERSHOOT below ground counted as a wall
            # that does not exist -- MEASURED on T1-portalhub-temple, 33 rays
            # recorded as `water_edge` clips carrying up to 1.727 m of phantom
            # wall, all 33 on FILL rays the water rule does not apply to.  So
            # the order stays and the skip stays.
            #
            # WHAT CHANGED IS THAT THE SKIP NO LONGER ENDS THE RAY, BECAUSE
            # ITS DECLARED PREMISE WAS MEASURED FALSE.  It read "past the
            # crossing the plane is UNDERGROUND on this ray".  Natural ground
            # is not monotone and one dimple is enough.  MEASURED on
            # T3-temple-meadhall, Fresh42, 2026-09-24, with the reach spent
            # nowhere near its cap:
            #   ray (+1, arc 400) ended lat 7.990 `met_ground`; 0.707 m
            #     further out on the SAME radial the plane is +0.3533 m ABOVE
            #     ground, and (-158,-361) stands as a 0.8892 m rim step
            #   ray (-1, arc 327) ended lat 8.273 `met_ground`; at lat 8.980
            #     the plane is +0.2907 m above ground, and (-130,-292) stands
            #     as a 0.8426 m rim step
            # Both segments report `walls_left_by_cause {}` -- the batter's
            # SUCCESS exit, taken over ground it had not finished.  A clip
            # that leaves no trace is indistinguishable from a batter that
            # worked, and this one left no trace by construction.
            # `sign_s` IS THIS SAMPLE'S OWN EARTHWORK DIRECTION, which equals
            # the ray's everywhere the ribbon is not beside itself.  See
            # `reconcile_target`: a reconciled raise on a cut ray read
            # against the RAY's sign is `met_ground`, the success exit, and
            # the repair would delete itself in silence.
            if batter_met_ground(target - surf, sign_s):
                if crossed_lat is None:
                    crossed_lat = lat
                    st["batter_met_ground"] += 1
                continue
            if fr is not None:
                out_ = foreign_out_of_band(fr, target)
                if out_ > EDGE_EPS_M:
                    # NO SURFACE SERVES BOTH EDGES HERE: the corridor rule's
                    # routing fact, under the corridor rule's own cause.
                    _clip(st, "shared_corridor", False, None, wx, wz, lat,
                          full_edge, residual=out_)
                    st["foreign_batter_out_of_band"] += 1
                    if out_ > st["foreign_batter_out_of_band_worst_m"]:
                        st["foreign_batter_out_of_band_worst_m"] = round(out_, 4)
                        st["foreign_batter_out_of_band_at"] = [
                            round(wx, 1), round(wz, 1), fr["who"]]
                    st["rays_clipped"] += 1
                    end_lat, end_cause = lat, "shared_corridor"
                    break
            cause = refusal(wx, wz, generated, False, delta)
            if cause is not None:
                _clip(st, cause, False, None, wx, wz, lat, full_edge,
                      residual=delta)
                if cause == "placed" and len(st["batter_clipped_placed_at"]) < 20:
                    st["batter_clipped_placed_at"].append(
                        [round(wx, 1), round(wz, 1)])
                # A SUBMERGED DIMPLE IS NOT THE END OF THE HILLSIDE, and this
                # is the same call this walk already made for `met_ground`
                # twenty lines up: natural ground is not monotone and one
                # dimple is enough.  `water_fill` is refused PER SAMPLE -- the
                # rule is keyed on this sample's own generated height and its
                # own move -- so skipping it cannot reinstate a drain: every
                # sample outboard is still asked and still refused if IT would
                # clear the plane.
                #
                # MEASURED by `Causeway`, Fresh42, `--validate`, with the
                # `break`: rays stopped by `water_fill` and LAND samples left
                # lying outboard of the stop ON THE SAME RAY --
                #   T4 42 rays / 719 land samples   T9 17 / 312
                #   S5 17 / 247                     T1 15 / 2,037
                # 3,315 samples of unfinished taper on four segments.  On
                # T4-meadhall-stathub that abandoned strip is where the rim
                # gate's caused count went 10 -> 22 and its worst 1.4894 ->
                # 3.2592 m: of the five new rim cells ONE stood on submerged
                # ground at 29.347 and FOUR STOOD ON LAND at 30.767, 31.145,
                # 31.334 and 31.564 -- the strip just ABOVE the plane,
                # outboard of the water the ray stopped for.
                if cause == "water_fill" and not water_fill_ends_the_ray:
                    st["water_fill_skipped"] += 1
                    continue
                st["rays_clipped"] += 1
                end_lat, end_cause = lat, cause
                break
            stop = batter_advance(target - surf, full_edge, run)
            if stop is None and abs(delta) > tcdata.CLAMP_M:
                stop = "over_clamp"
            if stop is not None:
                _clip(st, stop, False, None, wx, wz, lat,
                      full_edge, residual=delta)
                st["rays_clipped"] += 1
                end_lat, end_cause = lat, stop
                break
            comps[(zx, zz)].set_height(gx, gy, delta)
            written.append((wx, wz))
            written_set.add((int(round(wx)), int(round(wz))))
            batter_at[(int(round(wx)), int(round(wz)))] = (
                zx, zz, gx, gy, generated)
            if fr is not None:
                st["foreign_batter_written"] += 1
            st["samples_batter"] += 1
            if pad_keepouts.contains(wx, wz) is not None:
                st["samples_batter_in_pad_standoff"] += 1
            st["max_cut_m"] = min(st["max_cut_m"], delta)
            st["max_fill_m"] = max(st["max_fill_m"], delta)
            _clamp_check(st, delta, zx, zz, gx, gy, wx, wz, generated, target,
                         "batter")
        if end_cause == "lattice_end" and bounded_by is not None:
            # THE OTHER ROAD'S GROUND, NOT THE END OF THE LATTICE: the plane
            # met natural ground short of it, so there is nothing left to
            # grade -- and walking on would write that road's carriageway.
            end_lat, end_cause = bounded_by, "another_road"
        if end_cause == "lattice_end":
            # THE RAY DOES NOT END WHERE ITS BIN RUNS OUT.
            #
            # Two mechanisms used to share this block.  The first is real: the
            # outermost member sits within a lattice pitch of the cap, THE
            # REACH IS SPENT, and the plane is still `residual` off natural
            # ground out there -- a wall like any other, and a `reach` clip.
            #
            # The second was `bin_exhausted`, and ITS DECLARED PREMISE WAS
            # MEASURED FALSE.  It read "this station's BIN ran out of samples,
            # so nothing refused this ray and it left no wall of its own: the
            # ground outboard of it at this arc belongs to a neighbouring ray
            # and is written there."  Nobody had measured that population.
            # MEASURED on Fresh42, 2026-09-24, over the in-band lattice the
            # stamp itself scanned: 1,970 rays network-wide took this exit and
            # 1,926 of them -- 97.8 % -- have in-band ground outboard of their
            # last member that NO ray writes; ZERO have no outboard ground at
            # all.  On T1-portalhub-temple it is 27 of 27, and the ray at
            # (-130, -308) is one of them: its own write moved 0.457 m into a
            # cell whose outboard neighbour at (-131, -308) nobody touched,
            # and 0.6303 + 0.457 is the 1.0875 m step `rim_transfer` refused
            # the whole segment for.  A SHRINK IS SILENT, and this one was
            # silent AND load-bearing.
            #
            # So the premise is now a MEASUREMENT.  The ray is held back until
            # every bin has been walked, and then walks its OWN RADIAL over
            # whatever the neighbours did not lay, ending at exactly the exits
            # the member walk ends at.  Deferring is not a detail: run inline
            # it claims cells a later bin holds as members, and that bin then
            # rewrites them from a different foot -- 37 of 1,484 samples on
            # the ray-end fixture, double-counted and last-writer-wins.
            # See RAY_END_DEPENDS_ON.
            pending.append((key, members[-1], sign, full_edge, crossed_lat))
            continue
        st["ray_end_at"].append((round(float(key[1]), 2), int(key[0]),
                                 round(float(end_lat), 3), end_cause))
    # ---- STATIONS THAT HAVE ROAD AND NO RAY AT ALL -----------------------
    #
    # A RAY IS KEYED ON THE NEAREST BRANCH, WHICH NAMES A SUBSET OF "the
    # cells outboard of THIS station's carriageway edge".  `nearest wins`
    # hands a cell to the branch it is nearest to, so where the ribbon runs
    # beside itself the ground one metre outboard of branch A's edge belongs
    # to branch B -- and B's plane, anchored on B's own road height, meets
    # ground there at its first sample and records SUCCESS.  A's wall is then
    # laid down by nobody and counted by nobody: `rays_without_road` counts
    # rays that exist and have no road, and there was no counter at all for a
    # station that has road and no ray.
    #
    # MEASURED on Fresh42, `--validate`, 2026-09-24, over the five segments
    # the rim gate refuses: 60 of their 71 caused rim steps have their two
    # samples claimed by feet 9 to 30 m APART ALONG THE POLYLINE, and on four
    # of the five EVERY ray exits `met_ground`.  T4-meadhall-stathub's worst,
    # 1.4894 m at the rim cell (-178, -357), is one of these: the rim cell is
    # the FIRST cell outboard of the edge at its own arc and no ray exists at
    # that (side, arc) to walk it.
    #
    # THE REMEDY IS THE MECHANISM THAT IS ALREADY HERE.  A ray whose member
    # list runs out walks its OWN RADIAL over whatever the neighbours did not
    # lay; a station whose member list was never created is the same fact
    # with an empty list, so it is handed to the same walk, anchored at its
    # own carriageway edge.  It ends at exactly the exits every other ray
    # ends at -- including `reach`, which is a NAMED refusal and not silence.
    # THE POPULATION IS COUNTED WHETHER OR NOT IT IS WALKED.  A neuter that
    # also hides the number would make the shipped state look clean, which is
    # the shape this whole repair exists to remove.
    ray_stations = {k for k, _b in rays}
    for key in sorted(road_stations - ray_stations):
        if key in dead_stations:
            continue
        foot = road_foot.get(key)
        if foot is None:
            # UNREAD IS NOT CLEAR.  This side of this station has no
            # carriageway sample off the centreline, so there is no direction
            # to walk and no honest way to invent one.  Counted and named.
            st["stations_road_without_ray_unwalkable"] += 1
            continue
        st["stations_road_without_ray"] += 1
        lat_f, fx_f, fz_f, wx_f, wz_f, y_f = foot
        ux_f, uz_f = (wx_f - fx_f) / lat_f, (wz_f - fz_f) / lat_f
        ex_f, ez_f = fx_f + ux_f * edge, fz_f + uz_f * edge
        gen_f = appliedmod.generated_at(patches, ex_f, ez_f)
        if gen_f is None:
            st["stations_road_without_ray_unwalkable"] += 1
            st["stations_road_without_ray"] -= 1
            continue
        full_f = y_f - gen_f
        if abs(full_f) <= EDGE_EPS_M:
            # Road at grade with the ground: no wall, nothing to batter.
            continue
        if not orphan_station_rays_on:
            # THE NEUTER, AND IT IS THE STATE THIS SHIPPED IN: the station's
            # edge wall is left standing and recorded as a SKIP, which no
            # wall cause names -- so `rim_transfer` is handed a caused step
            # with an empty cause list, which is the defect.
            st["ray_skip_at"].append(
                (ex_f, ez_f, "station_without_ray", round(abs(full_f), 3)))
            continue
        st["rays"] += 1
        st["orphan_rays_walked"] += 1
        pending.append((key, (edge, 0, 0, 0, 0, ex_f, ez_f, y_f, gen_f,
                              fx_f, fz_f),
                        1.0 if full_f > 0 else -1.0, full_f, None))


    for key, last, sign, full_edge, crossed_lat in pending:
        lat_, _zx, _zz, _gx, _gy, wx_, wz_, y_, gen_, fx_, fz_ = last
        _fr0 = foreign_road(wx_, wz_)
        if _fr0 is not None and not _fr0["paved"]:
            gen_ = _fr0["surface"]
        ux_, uz_ = (wx_ - fx_) / lat_, (wz_ - fz_) / lat_
        # The furthest cell this ray has ACCOUNTED FOR **WITH THE PLANE STILL
        # ABOVE GROUND** -- the toe -- which is what a `reach` clip has to
        # report its residual at.  A cell where the plane has gone under
        # ground carries no wall, and reporting one from there is the phantom
        # residual this file already paid for once.
        cx_, cz_, clat_, cgen_ = wx_, wz_, lat_, gen_
        # AND WHETHER THAT LAST MEMBER IS ITSELF UNDER GROUND.  The member
        # walk hands the ray over at its outermost member whatever the plane
        # is doing there, so starting this flag at False would let a ray that
        # had already finished report a `reach` wall measured below ground.
        under_ = batter_met_ground(
            (y_ - sign * batter_grade * (lat_ - edge)) - gen_, sign)
        st["rays_continued"] += 1
        if not ray_walks_past_its_bin:
            # THE NEUTER, AND IT IS THE STATE THIS SHIPPED IN.  Kept
            # EXECUTABLE rather than described, so `selftest_batter_ray_end`
            # runs the false premise over the same constructed ground instead
            # of asserting about it: stop at the last member, record no wall,
            # and leave the outboard cell to a neighbouring ray that 1,926
            # times in 1,970 does not exist.
            st["ray_skip_at"].append(
                (wx_, wz_, "ray_bin_exhausted",
                 round(abs((y_ - sign * batter_grade * (lat_ - edge))
                           - gen_), 3)))
            st["ray_end_at"].append((round(float(key[1]), 2), int(key[0]),
                                     round(float(lat_), 3), "bin_exhausted"))
            continue
        last_cell = (int(round(wx_)), int(round(wz_)))
        probe, end_cause = lat_, ""
        while not end_cause:
            probe += tcdata.SCALE
            if probe > reach + EDGE_EPS_M:
                if under_:
                    # THE TAPER FINISHED; the cap merely arrived afterwards.
                    end_cause = "met_ground"
                    break
                residual_ = ((y_ - sign * batter_grade * (clat_ - edge))
                             - cgen_)
                # WHICH OF THE CAP'S TWO POPULATIONS THIS RAY IS IN, and the
                # run that would finish it if it is the closing one.
                cause_, need_ = batter_reach_exit(full_edge, residual_,
                                                  clat_ - edge)
                _clip(st, cause_, False, None, cx_, cz_, clat_,
                      full_edge, residual=residual_)
                st["rays_clipped"] += 1
                if need_ is not None:
                    st["reach_needed_m"].append(round(need_, 3))
                    if need_ > st["reach_needed_max_m"]:
                        st["reach_needed_max_m"] = round(need_, 3)
                        st["reach_needed_at"] = [round(cx_, 1), round(cz_, 1)]
                end_cause = cause_
                break
            sx_ = int(round(fx_ + ux_ * probe))
            sz_ = int(round(fz_ + uz_ * probe))
            if (sx_, sz_) == last_cell:
                continue
            last_cell = (sx_, sz_)
            # THE SNAPPED CELL'S OWN LATERAL ON THIS RAY, not the probe:
            # rounding to the lattice moves the point by up to half a
            # diagonal and the plane is a function of the lateral.
            lat_s = math.hypot(sx_ - fx_, sz_ - fz_)
            if not edge + EDGE_EPS_M < lat_s <= reach + EDGE_EPS_M:
                continue
            zx_, zz_ = tcdata.zone_of(sx_, sz_)
            comp_ = comps.get((zx_, zz_))
            gen_s = appliedmod.generated_at(patches, float(sx_), float(sz_))
            if comp_ is None or gen_s is None:
                # UNREAD IS NOT CLEAR.  The corridor cover is built to include
                # the batter reach, so a ray walking off it is the cover being
                # short, and it must say so rather than end quietly one cell
                # inside it.
                _clip(st, "unread_ground", False, None, float(sx_),
                      float(sz_), lat_s, full_edge,
                      residual=((y_ - sign * batter_grade * (clat_ - edge))
                                - cgen_))
                st["rays_clipped"] += 1
                end_cause = "unread_ground"
                break
            # THE SAME READING OF ANOTHER ROAD'S CLAIM AS THE MEMBER WALK.
            # See FOREIGN_BATTER_DEPENDS_ON.
            fr_ = foreign_road(float(sx_), float(sz_))
            if fr_ is not None and fr_["paved"]:
                end_cause = "another_road"
                break
            surf_ = gen_s if fr_ is None else fr_["surface"]
            target_ = y_ - sign * batter_grade * (lat_s - edge)
            # THE SAME JOINT BAND AS THE MEMBER WALK, and it has to be the
            # same call or the two halves of one ray write two different
            # surfaces over the corridor they share.  See
            # BATTER_TARGET_DEPENDS_ON.
            floor_s = floor_at.get((sx_, sz_), -math.inf)
            plane_ = target_
            target_, sign_s, unabsorbable_ = reconcile_target(
                target_, gen_s, sign, y_, lat_s, floor_s, edge, batter_grade)
            if unabsorbable_ > 0.0:
                st["self_corridor_refused_samples"] += 1
                continue
            _count_reconciled(st, plane_, target_, gen_s, sign, sign_s)
            delta_ = target_ - gen_s
            if batter_met_ground(target_ - surf_, sign_s):
                # UNDER GROUND HERE.  Skip it and keep walking -- the toe is a
                # fact about the radial, not about one sample.  See the member
                # walk above for the two T3 rays this was measured on.
                if crossed_lat is None:
                    crossed_lat = lat_s
                    st["batter_met_ground"] += 1
                under_ = True
                continue
            under_ = False
            cx_, cz_, clat_, cgen_ = float(sx_), float(sz_), lat_s, surf_
            if fr_ is not None:
                out_ = foreign_out_of_band(fr_, target_)
                if out_ > EDGE_EPS_M:
                    _clip(st, "shared_corridor", False, None, float(sx_),
                          float(sz_), lat_s, full_edge, residual=out_)
                    st["foreign_batter_out_of_band"] += 1
                    if out_ > st["foreign_batter_out_of_band_worst_m"]:
                        st["foreign_batter_out_of_band_worst_m"] = round(out_, 4)
                        st["foreign_batter_out_of_band_at"] = [
                            sx_, sz_, fr_["who"]]
                    st["rays_clipped"] += 1
                    end_cause = "shared_corridor"
                    break
            cause = refusal(float(sx_), float(sz_), gen_s, False, delta_)
            if cause is not None:
                _clip(st, cause, False, None, float(sx_), float(sz_),
                      lat_s, full_edge, residual=delta_)
                # THE SAME SKIP AS THE MEMBER WALK, and it has to be the same
                # or the two halves of one ray disagree about where the
                # hillside ends.  See the member walk for the 3,315 abandoned
                # land samples this was measured on.
                if cause == "water_fill" and not water_fill_ends_the_ray:
                    st["water_fill_skipped"] += 1
                    continue
                st["rays_clipped"] += 1
                end_cause = cause
                break
            stop = batter_advance(target_ - surf_, full_edge, lat_s - edge)
            if stop is None and abs(delta_) > tcdata.CLAMP_M:
                stop = "over_clamp"
            if stop is not None:
                _clip(st, stop, False, None, float(sx_), float(sz_),
                      lat_s, full_edge, residual=delta_)
                st["rays_clipped"] += 1
                end_cause = stop
                break
            if (sx_, sz_) in written_set:
                # A NEIGHBOUR DID FINISH THIS CELL.  That is the premise being
                # true HERE, measured rather than assumed: the cell is graded,
                # so the walk carries on past it to ask about the next one
                # instead of clobbering somebody else's answer.
                continue
            ccx_, ccz_ = comp_.centre
            gx_, gy_ = tcdata.vertex_mask_index(ccx_, ccz_, sx_, sz_)
            if not (0 <= gx_ < tcdata.PITCH and 0 <= gy_ < tcdata.PITCH):
                continue
            comp_.set_height(gx_, gy_, delta_)
            written.append((float(sx_), float(sz_)))
            written_set.add((sx_, sz_))
            batter_at[(sx_, sz_)] = (zx_, zz_, gx_, gy_, gen_s)
            if fr_ is not None:
                st["foreign_batter_written"] += 1
            st["samples_batter"] += 1
            st["samples_batter_continued"] += 1
            if pad_keepouts.contains(float(sx_), float(sz_)) is not None:
                st["samples_batter_in_pad_standoff"] += 1
            st["max_cut_m"] = min(st["max_cut_m"], delta_)
            st["max_fill_m"] = max(st["max_fill_m"], delta_)
            _clamp_check(st, delta_, zx_, zz_, gx_, gy_, float(sx_),
                         float(sz_), gen_s, target_, "batter")
        st["ray_end_at"].append((round(float(key[1]), 2), int(key[0]),
                                 round(float(clat_), 3), end_cause))

    # THE PADS THIS ROAD ARRIVES AT (ARRIVAL_PADS_DEPENDS_ON): only they pin
    # its earthwork.  Pinning every planned pad within claim reach cut 5.19 m
    # (44 steps, `rim_authored_by` None) around a pad S5-temple-southcape only
    # passes, and flagged T1, T3, S5 and S11 REFUSED at plan time -- all four
    # laid CLEAR by the real writer in Fresh42 drive 6.  A pad the road merely
    # passes shapes its own surroundings when built; the rim it leaves is owed
    # to that pad (PLANNED_PAD_RIM_DEPENDS_ON).
    arrival_sids = {w["nums"]["destination"]
                    for w in (pad_keepouts.arrival_walks(seg)
                              if batter_m else [])}

    def foundation_y(sx, sz):
        future = pad_keepouts.finished_y(
            sx, sz, only=(arrival_sids if _ARRIVAL_PADS_MODE == "arrival"
                          else None))
        if future is not None:
            return future
        if live_delta_at is not None and (sx, sz) in pad_keepouts.levelled:
            g = appliedmod.generated_at(patches, float(sx), float(sz))
            return None if g is None else g + live_delta_at(float(sx), float(sz))
        return None

    # A pad approach is part of the road, not the unmeasured natural gap
    # beyond its last profile station.  Grade the gate's own snapped walk
    # from the last carriageway footfall to the first foundation footfall.
    # Keep every normal earthwork veto: a shore or protected claim requires
    # another alignment, never an approach-shaped exception.
    st["approach_samples"] = 0
    approach_cells = set()
    earth_limit = min(float(seg.get("max_cf_m", tcdata.CLAMP_M)), tcdata.CLAMP_M)
    for walk in (pad_keepouts.arrival_walks(seg)
                 if batter_m and _APPROACH_GRADING else []):
        vertices = walk["vertices"]
        inside = set(walk["inside"])
        stop = next((i for i, v in enumerate(vertices) if v in inside), None)
        if stop is None or stop == 0:
            continue
        start = max((i for i in range(stop) if vertices[i] in road_cells),
                    default=0)
        ax, az = vertices[start]
        bx, bz = vertices[stop]
        g0 = appliedmod.generated_at(patches, ax, az)
        if g0 is None:
            continue
        y0 = g0 + delta_at(comps, int(ax), int(az))
        y1 = walk["datum"]
        dx, dz = bx - ax, bz - az
        length2 = dx * dx + dz * dz
        if length2 == 0:
            continue
        approach = set()
        for x, z in vertices[start:stop]:
            approach.update((int(x + ox), int(z + oz))
                            for ox in (-1, 0, 1) for oz in (-1, 0, 1))
        for sx, sz in sorted(approach):
            if (sx, sz) in road_cells or pad_keepouts.contains(sx, sz):
                continue
            g = appliedmod.generated_at(patches, float(sx), float(sz))
            if g is None:
                continue
            t = min(1.0, max(0.0, ((sx - ax) * dx + (sz - az) * dz) / length2))
            target = y0 + t * (y1 - y0)
            fr = foreign_road(float(sx), float(sz))
            if fr is not None and (fr["paved"] or
                                    foreign_out_of_band(fr, target) > EDGE_EPS_M):
                continue
            delta = target - g
            if abs(delta) > earth_limit or \
                    refusal(sx, sz, g, False, delta) is not None:
                continue
            zx, zz = tcdata.zone_of(sx, sz)
            comp = comps.get((zx, zz))
            if comp is None:
                continue
            gx, gy = tcdata.vertex_mask_index(*comp.centre, sx, sz)
            comp.set_height(gx, gy, delta)
            road_cells.add((sx, sz))
            approach_cells.add((sx, sz))
            batter_at.pop((sx, sz), None)
            road_written.append((float(sx), float(sz)))
            if (sx, sz) not in written_set:
                written.append((float(sx), float(sz)))
                written_set.add((sx, sz))
            st["approach_samples"] += 1
            st["max_cut_m"] = min(st["max_cut_m"], delta)
            st["max_fill_m"] = max(st["max_fill_m"], delta)
            # Let the existing rim solver close the collar around these
            # footfalls, using the same slope and reach as the road's toe.
            for ox in range(-int(reach), int(reach) + 1):
                for oz in range(-int(reach), int(reach) + 1):
                    dist = math.hypot(ox, oz)
                    if dist > reach - edge:
                        continue
                    c = (sx + ox, sz + oz)
                    lat = edge + dist
                    if c not in claim_of or lat < claim_of[c][0]:
                        claim_of[c] = (lat, target)
    # The connection has sides as well as a centreline.  Close a single
    # Lipschitz collar against both the approach and future foundation,
    # before the ordinary toe/rim pass.  Merely pinning the walked samples
    # leaves a new cliff one metre to either side of a repaired approach.
    fixed = ({c: foundation_y(*c) for c in claim_of}
             if batter_m else {})
    fixed = {c: y for c, y in fixed.items() if y is not None}
    st["connection_collar_samples"] = 0
    collar_moved: dict = {}
    if (_CONNECTION_COLLAR and seg.get("connection_collar", True)
            and (approach_cells if _COLLAR_GATE == "approach"
                 else (fixed or approach_cells))):
        import heapq                                       # noqa: PLC0415
        # Reserve the collar as well as its fixed face.  Otherwise a pad
        # near the end of the original road mask closes one cliff by
        # creating another at that mask's edge.
        collar = int(math.ceil(earth_limit / batter_grade)) + 2
        # THE COLLAR SERVES THE APPROACH, WITHIN THE COLLAR'S OWN WIDTH OF IT
        # (COLLAR_REGION_DEPENDS_ON).  Grading the pad's whole perimeter
        # filled the hub's far south-east edge, 38 m from the road's end, on
        # Fresh42 drive 8: T1-portalhub-temple 14 caused steps, worst 2.7794 m
        # at [-194,-441].  That edge is the pad's own and is owed to it.
        near_approach = {(a[0] + ox, a[1] + oz) for a in approach_cells
                         for ox in range(-collar, collar + 1)
                         for oz in range(-collar, collar + 1)}
        for sid, cx, cz, hw, hd in pad_keepouts.rects:
            if _ARRIVAL_PADS_MODE == "arrival" and sid not in arrival_sids:
                continue
            info = pad_keepouts.by_id.get(sid) or {}
            if (info.get("pad_y") is None or not any(
                    pad_keepouts.pad_stamps(*c, cx, cz, hw, hd) for c in fixed)):
                continue
            y = float(info["pad_y"])
            # Once a road meets a foundation its complete face is fixed;
            # treating only the part inside the old road mask as fixed
            # creates a new step where that partial population ends.
            for sx in range(math.floor(cx - hw - collar), math.ceil(cx + hw + collar) + 1):
                for sz in range(math.floor(cz - hd - collar), math.ceil(cz + hd + collar) + 1):
                    c = (sx, sz)
                    if tcdata.zone_of(*c) not in comps:
                        continue
                    pin = foundation_y(sx, sz)
                    if pin is not None:
                        fixed[c] = pin
                    dist = max(abs(sx - cx) - hw, 0.0) + max(abs(sz - cz) - hd, 0.0)
                    if dist > collar or c in claim_of:
                        continue
                    # The ring is NOT bounded to the road's reach: a bound cut
                    # the collar off mid-slope 33-37 m from the centreline and
                    # left the cliff -- MEASURED on Fresh42 drive 8, T1 rows
                    # 2.458 m at 33.5 m, T4 0.974 m at 36.8 m; adding the bound
                    # took T1 from 2 rows to 49.  (An earlier note here blamed
                    # the ring for a hub-side fill; that was the whole-pad pin
                    # ARRIVAL_PADS_DEPENDS_ON fixed, and was mis-attributed.)
                    if _COLLAR_REGION_MODE == "approach" and c not in near_approach:
                        continue
                    claim_of[c] = (edge + dist, y)
        for c in road_cells:
            g = appliedmod.generated_at(patches, float(c[0]), float(c[1]))
            if g is not None:
                fixed[c] = g + delta_at(comps, *c)
        domain = set(claim_of) | set(fixed)
        # A CELL THE WRITER WOULD VETO STAYS WHERE IT IS, AND ITS NEIGHBOURS
        # ARE GRADED AGAINST IT.  Skipping it after the fact raised the
        # neighbour anyway and left a cliff -- MEASURED on Fresh42 drive 6's
        # saved plan, T1-portalhub-temple: rim ground 29.9875 m (under the 30 m
        # plane, a water_fill veto) beside a collar sample raised to 32.0761 m,
        # 9 steps, worst 2.0886 m.  Foreign paved road is anchored the same way.
        for c in sorted(domain - set(fixed)):
            g = appliedmod.generated_at(patches, float(c[0]), float(c[1]))
            if g is None:
                continue
            fr = foreign_road(float(c[0]), float(c[1]))
            if ((fr is not None and fr["paved"])
                    or refusal(c[0], c[1], g, False, earth_limit) is not None
                    or refusal(c[0], c[1], g, False, -earth_limit) is not None):
                fixed[c] = g + delta_at(comps, *c)
        # THE GROUND JUST OUTSIDE THE COLLAR IS AN ANCHOR TOO
        # (COLLAR_EDGE_DEPENDS_ON).  The envelope grades a cell against the
        # pad and the road, never against the untouched ground past the
        # collar's edge, so a cell that moved at the edge left a step against
        # its unmoved neighbour.  MEASURED on Fresh42 drive 8 (real writer,
        # step 4): T1-portalhub-temple 25 caused steps, worst 3.0465 m at
        # [-218,-382]; T4/S7 2 steps 0.8664 m at [91,385]; T6 4 steps 1.5626 m
        # -- every one virgin ground beside a collar, owned by nobody.  Without
        # the collar T1 and T4 lay clean; S7 and W9 need it.
        if _COLLAR_EDGE_MODE == "anchored":
            ring = {}
            for c in domain:
                for ox, oz in ((1, 0), (-1, 0), (0, 1), (0, -1),
                               (1, 1), (1, -1), (-1, 1), (-1, -1)):
                    n = (c[0] + ox, c[1] + oz)
                    if n in domain or n in fixed or n in ring:
                        continue
                    g = appliedmod.generated_at(patches, float(n[0]), float(n[1]))
                    if g is not None:
                        ring[n] = g + delta_at(comps, *n)
            fixed.update(ring)
            domain |= set(ring)

        def envelope(sign):
            bounds = {c: sign * y for c, y in fixed.items()}
            heap = [(v, c) for c, v in bounds.items()]
            heapq.heapify(heap)
            while heap:
                value, c = heapq.heappop(heap)
                if value > bounds[c] + EDGE_EPS_M:
                    continue
                for ox, oz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    n = (c[0] + ox, c[1] + oz)
                    proposed = value + batter_grade
                    if n in domain and proposed < bounds.get(n, math.inf):
                        bounds[n] = proposed
                        heapq.heappush(heap, (proposed, n))
            return bounds

        upper, negative_lower = envelope(1.0), envelope(-1.0)
        for c in sorted(domain - set(fixed)):
            if c not in upper or c not in negative_lower:
                continue
            lo, hi = -negative_lower[c], upper[c]
            if lo > hi + EDGE_EPS_M:
                continue
            sx, sz = c
            g = appliedmod.generated_at(patches, float(sx), float(sz))
            if g is None:
                continue
            cur = g + delta_at(comps, sx, sz)
            target = min(max(cur, lo), hi)
            if abs(target - cur) <= EDGE_EPS_M:
                continue
            delta = target - g
            fr = foreign_road(float(sx), float(sz))
            if (abs(delta) > earth_limit
                    or refusal(sx, sz, g, False, delta) is not None
                    or (fr is not None and (fr["paved"]
                        or foreign_out_of_band(fr, target) > EDGE_EPS_M))):
                continue
            zx, zz = tcdata.zone_of(sx, sz)
            comp = comps.get((zx, zz))
            if comp is None:
                continue
            gx, gy = tcdata.vertex_mask_index(*comp.centre, sx, sz)
            prior_batter = batter_at.get(c)
            _i = gy * tcdata.PITCH + gx
            # The compiler's OWN state for this cell, so a peel restores it
            # exactly: a cell this write had not touched must go back to
            # UNMODIFIED, not to a delta of 0 -- a modified cell at 0 writes
            # the generated ground over whatever an earlier leg left there.
            prior_cell = (comp.modified_height[_i], comp.level_delta[_i],
                          comp.smooth_delta[_i])
            comp.set_height(gx, gy, delta)
            batter_at[c] = (zx, zz, gx, gy, g)
            collar_moved[c] = (comp, gx, gy, target, prior_cell, prior_batter)
            if c not in written_set:
                written.append((float(sx), float(sz)))
                written_set.add(c)
                st["samples_batter"] += 1
            st["connection_collar_samples"] += 1
            st["max_cut_m"] = min(st["max_cut_m"], delta)
            st["max_fill_m"] = max(st["max_fill_m"], delta)

        # PEEL WHAT CANNOT JOIN THE GROUND (COLLAR_PEEL_DEPENDS_ON).  A collar
        # cell whose height stands more than one slide limit over a neighbour
        # that this write leaves alone -- untouched natural ground, or an
        # anchor the writer would veto -- is a cliff the earthwork made.  It is
        # put back, and the pass repeats: a cell that cannot be graded ALL the
        # way down to natural ground is not graded at all, and what remains
        # between it and the pad is the pad's own edge, owed to the pad.
        # MEASURED on Fresh42 drive 8 (real writer, step 4), T1-portalhub-
        # temple: the hub's south-east edge filled to 33.639 m against natural
        # ground at 30.86 m, 14 caused steps, worst 2.7794 m; the closure
        # reaches two rings and the fill needed five.
        if _COLLAR_PEEL_MODE == "on":
            pinned = {c for c in claim_of if foundation_y(*c) is not None}

            def _now(c):
                if c in collar_moved:
                    return collar_moved[c][3]
                g0 = appliedmod.generated_at(patches, float(c[0]), float(c[1]))
                return None if g0 is None else g0 + delta_at(comps, *c)
            st["collar_peeled"] = 0
            for _round in range(60):
                bad = []
                for c in collar_moved:
                    hc = _now(c)
                    for ox, oz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        n = (c[0] + ox, c[1] + oz)
                        if n in collar_moved or n in road_cells:
                            continue
                        pin_n = foundation_y(*n)
                        hn = pin_n if pin_n is not None else _now(n)
                        if hn is not None and abs(hc - hn) > batter_grade + EDGE_EPS_M:
                            bad.append(c)
                            break
                if not bad:
                    break
                for c in bad:
                    comp, gx, gy, _t, prior_cell, prior_batter = collar_moved.pop(c)
                    _i = gy * tcdata.PITCH + gx
                    (comp.modified_height[_i], comp.level_delta[_i],
                     comp.smooth_delta[_i]) = prior_cell
                    if prior_batter is None:
                        batter_at.pop(c, None)
                    else:
                        batter_at[c] = prior_batter
                    st["connection_collar_samples"] -= 1
                    st["collar_peeled"] = st.get("collar_peeled", 0) + 1


    # ---- THE TOE, LAID DOWN ----------------------------------------------
    # The limit is the GATE'S OWN, imported rather than restated: a feather
    # derived against a second copy of tan(38 deg) would pass a check keyed
    # on the first only by luck.  See TOE_FEATHER_DEPENDS_ON.
    sys.path.insert(0, str(HERE))
    import junction as _junction                          # noqa: PLC0415
    if feather_toe_on:
        _before = None
        if foreign_batter_is_ground and live_delta_at is not None:
            def _before(sx: int, sz: int):
                g = appliedmod.generated_at(patches, float(sx), float(sz))
                return None if g is None else g + live_delta_at(float(sx),
                                                                float(sz))
        def _feather():
            feather_toe(comps, patches, batter_at, road_cells, st,
                        _junction.SLIDE_PER_M, before_at=_before,
                        fixed_at=foundation_y,
                        resolves_toward_ground=feather_toward_ground,
                        pins_hold=feather_pins_hold,
                        binds_on_preexisting=feather_binds_on_preexisting)
        _feather()
        st["rim_closure"] = {"cells": 0, "rounds": 0, "unclosable": 0,
                             "worst_move_m": 0.0, "worst_at": None,
                             "refeathered": 0}
        if rim_closure_on:
            # THE FIRST FEATHER'S CENSUS IS THE ONE REPORTED; a re-feather
            # after a closed ring relaxes what the ring added.
            first = st["toe_feather"]

            def _refeather():
                _feather()
                st["toe_feather"] = first

            def _foreign_for_closure(x: float, z: float):
                """Another road's claim as the closure may read it: a claim
                with no band to read (none supplied, or the neuter) is never
                written -- the closure does not guess another road's band."""
                fr = foreign_road(x, z)
                if fr is None and road_claim_at is not None and \
                        road_claim_at(x, z) is not None:
                    return {"paved": True}
                return fr

            def _record(c, cause: str, h: float) -> None:
                st["closure_refused_at"].append(
                    (float(c[0]), float(c[1]), cause, round(h, 3)))

            close_rim(comps, patches, batter_at, road_cells, claim_of, st,
                      _junction.SLIDE_PER_M,
                      before_at=(_before if _before is not None else
                                 lambda sx, sz: appliedmod.generated_at(
                                     patches, float(sx), float(sz))),
                      refuse=lambda wx, wz, g, d: refusal(wx, wz, g, False, d),
                      foreign=_foreign_for_closure, edge_m=edge,
                      grade=batter_grade,
                      refeather=_refeather,
                      fixed_at=foundation_y,
                      record=(_record if _CLOSURE_REFUSALS_MODE == "recorded"
                              else None))
            # THE CELLS THE CLOSURE WROTE ARE WRITTEN BATTER, for every
            # census and gate that reads `written` (the location check).
            for c in batter_at:
                if c not in written_set:
                    written.append((float(c[0]), float(c[1])))
                    written_set.add(c)
                    st["samples_batter"] += 1
    else:
        st["toe_feather"] = {"feathered": 0, "worst_m": 0.0, "worst_at": None,
                             "earth_given_back_m": 0.0, "passes": 0}
        st["rim_closure"] = {"cells": 0, "rounds": 0, "unclosable": 0,
                             "worst_move_m": 0.0, "worst_at": None,
                             "refeathered": 0}

    for z, comp in list(comps.items()):
        touched = sum(comp.modified_height) + sum(comp.modified_paint)
        if touched:
            st["per_zone"][f"{z[0]},{z[1]}"] = int(sum(comp.modified_height))
        else:
            del comps[z]
    st["max_cut_m"] = round(st["max_cut_m"], 3)
    st["max_fill_m"] = round(st["max_fill_m"], 3)
    st["written"] = written
    st["road_written"] = road_written
    return comps, st


def _clamp_check(st: dict, delta: float, zx: int, zz: int, gx: int, gy: int,
                 wx: float, wz: float, generated: float, target: float,
                 band: str) -> None:
    """THE CLAMP, CHECKED PER SAMPLE.

    Not per segment and not on the fitting lattice: this is the integer
    lattice the game will apply, and `TerrainComp::ApplyToHeightmap` silently
    discards anything past +/- 8 m of the generated height, so a sample over
    the limit is a piece of road that is not where the plan says.
    """
    if abs(delta) <= tcdata.CLAMP_M:
        return
    st["over_clamp"] += 1
    # UNCAPPED, because the question a clamp refusal has to answer is not
    # "did it happen" but "WHERE does the road first stop being writable" --
    # and a list truncated at 20 cannot answer it.  MEASURED cost of the cap:
    # N4-mid2-terminus refused 239, then 120, then 112 samples across three
    # routing variants and the first 20 entries were the same Mistlands
    # hotspot every time, which hid whether the 3,070 m leg fails at 400 m or
    # at 2,900 m -- the difference between terminating the road early and
    # abandoning the leg.
    st["over_clamp_samples"].append(
            {"zone": [zx, zz], "sample": [gx, gy], "band": band,
             "world": [round(wx, 1), round(wz, 1)],
             "generated_y": round(generated, 2),
             "target_y": round(target, 2), "delta_m": round(delta, 2)})


def delta_at(comps: dict, x: float, z: float) -> float:
    """The height change this write applies AT an arbitrary world position.

    Not "the nearest written sample's delta": `Heightmap` renders a mesh that
    interpolates LINEARLY between adjacent samples at a 1 m pitch, so the
    ground under an off-lattice point moves by the bilinear blend of the four
    samples around it, and an UNWRITTEN sample contributes zero.  That is why a
    piece 0.9 m outside the ribbon edge still settles a little and one 1.1 m
    outside does not move at all -- and it is where `TERRAIN_SPREAD_M = 1.0`
    comes from.
    """
    x0, z0 = math.floor(x), math.floor(z)
    tx, tz = x - x0, z - z0
    total = 0.0
    for dx, dz, w in ((0, 0, (1 - tx) * (1 - tz)), (1, 0, tx * (1 - tz)),
                      (0, 1, (1 - tx) * tz), (1, 1, tx * tz)):
        if w == 0.0:
            continue
        sx, sz = x0 + dx, z0 + dz
        zx, zz = tcdata.zone_of(sx, sz)
        comp = comps.get((zx, zz))
        if comp is None:
            continue
        cx, cz = comp.centre
        gx, gy = tcdata.vertex_mask_index(cx, cz, sx, sz)
        if not (0 <= gx < tcdata.PITCH and 0 <= gy < tcdata.PITCH):
            continue
        k = gy * tcdata.PITCH + gx
        if comp.modified_height[k]:
            total += w * comp.level_delta[k]
    return total


# WHAT THE GROUND AT THE RIM STANDS AT ONCE THIS WRITE HAS LANDED, AND THE
# RULE THAT STOOD HERE NAMED A SUBSET OF IT.  It read "inside a zone this
# write emits, `generated + delta_at(comps)`", with a docstring saying
# `comps` is "the carried-forward union".  At the moment the rim gate runs it
# is not: the union is performed AFTER the gate, so `comps` holds THIS
# write's samples alone and every sample a PRIOR claim wrote in an emitted
# zone was judged at its GENERATED height -- while the blob that is actually
# sent carries that claim's delta verbatim.
#
# MEASURED, Fresh42, T8-stenvik-wttown, offline `--validate`, 2026-09-25:
# the refusal's worst step, 4.0718 m at rim (-113,-138) "owned by
# road_T1_portalhub_temple#1530", is T1's +4.169 m fill read as 40.650 when
# it stands, and will still stand, at 44.820 -- 0.098 m from T8's taken
# (-113,-137).  All 40 listed rows are that phantom.  And the same defect
# HIDES steps: judged against the surface actually sent, T8 has 226 caused
# (worst 3.7469 m at (-105,-137)) rather than 176, and T7 8 rather than 1.
# A gate reading a surface that is never built is wrong in both directions.
RIM_AFTER_SURFACE_DEPENDS_ON = (
    "this_write's_own_samples",
    "every_prior_claim_carried_into_the_zones_it_emits")


def rim_height_after(sx: int, sz: int, gen: float | None, comps: dict,
                     live_delta_at, carries_priors: bool = True,
                     own_samples: bool = True) -> float | None:
    """The applied height at lattice sample (sx, sz) ONCE THIS WRITE LANDS.

    THIS write's own sample where it wrote one -- even a millimetre's nudge
    is ground the walker stands on -- and otherwise what the live compiler
    holds, because the emitted blob carries every prior claim's sample
    verbatim (`prior_by_zone` in `main`) and outside the emitted zones
    nothing changes.  `carries_priors=False` is the shipped rule and
    `own_samples=False` its mirror; both exist for the selftest.
    """
    if gen is None:
        return None
    zx, zz = tcdata.zone_of(sx, sz)
    comp = comps.get((zx, zz))
    if comp is not None:
        cx, cz = comp.centre
        gx, gy = tcdata.vertex_mask_index(cx, cz, sx, sz)
        own = None
        if 0 <= gx < tcdata.PITCH and 0 <= gy < tcdata.PITCH:
            k = gy * tcdata.PITCH + gx
            if comp.modified_height[k]:
                own = comp.level_delta[k]
        if own_samples and own is not None:
            return gen + own
        if not carries_priors:
            return gen + (own if own is not None else 0.0)
    return gen + live_delta_at(float(sx), float(sz))


# WHOSE A CAUSED RIM STEP IS, ASKED PER STEP.  The refusal answered it for
# the whole write from one bit -- "does ANY caused row have an author" -- so a
# write with one step against another road's ground and a hundred against
# water was told to "pull [that road] into the closure", which closes one
# step and is silent about the hundred.  MEASURED, Fresh42, `--validate`,
# 2026-09-25: T7-stathub-stenvik's shipped refusal named
# `road_S4_meadhall_southeast#1535` as the owner of a write whose four other
# caused steps, 1.2799 m worst, stood on ground nobody had written.
#
# AND "NOBODY AUTHORED THE RIM" IS NOT "THE EARTHWORK LEFT IT".  The stamp
# RECORDS every sample it was forbidden to write (`clip_at`), every ray it
# could not walk (`ray_skip_at`) and every rim cell a gate forbade the closure
# to lay (`closure_refused_at`); a caused step whose rim sample carries one
# of those is a face the earthwork was REFUSED leave to close, and its
# remedy is whatever refused it -- for S13-stathub-dock, 91 of its 92
# steps, the water gate, i.e. a carriageway routed on a fill within its own
# batter run of water.  Only a step with NEITHER an author NOR a recorded
# refusal is this file's earthwork residue.  `refused_at_of` is the ONE
# reader of those three lists.
#
# AND AN AUTHORED RIM IS NOT ALWAYS A CLOSURE.  The claim sentence read
# "This is NOT a junction disagreement -- the plans agree there", as a
# constant.  MEASURED on T7 after this file's own residue closed: all 8 of
# its caused steps stand where T7's CARRIAGEWAY (lat 3.75, 34.42-34.66)
# abuts S4-meadhall-southeast's CARRIAGEWAY (lat 0.9-2.9, 35.37-35.61) one
# lattice edge away, and the two PLANS step 0.824-1.189 m across those edges
# -- the caused steps to the millimetre.  `junction.build_gate` compares the
# ground both plans pave and these cells are paved once each, so it read 0;
# re-emitting S4 after T7 lays the same step back and charges it to S4.
RIM_STEP_OWNER_DEPENDS_ON = ("who_authored_the_rim_ground",
                             "what_this_write_was_refused_at_the_rim_sample",
                             "whether_the_two_PLANS_step_across_the_edge")
# The closure's refusals were the list this reading missed: with the square
# end of a bank claimed (RIM_BANK_END_DEPENDS_ON) the closure asks to raise
# land over water, the water gate says no, and the face it leaves was charged
# to "the earthwork's OWN face" -- whose remedy is this file -- instead of
# `water_fill`, whose remedy is the route.  NEUTER
# `_CLOSURE_REFUSALS_MODE = "dropped"`, the shipped reading, proven by
# `selftest_rim_bank_end`'s wet arms.
_CLOSURE_REFUSALS_MODE = "recorded"   # recorded | dropped (the shipped one)


def rim_step_owner(row: dict, refused_at: dict, plan_step=None,
                   limit_m: float = BATTER_GRADE) -> str:
    """`plans <write>`, `claim <write>`, `refused <cause>[+<cause>]` or
    `earthwork`.  `plan_step(row)` is the step between THIS plan's
    carriageway at the taken sample and the owner's plan's carriageway at
    the rim sample, or None where either side is not a planned carriageway.
    Over the limit is `junction.step_over_limit`, the rim gate's own rule.
    """
    if row.get("rim_authored_by"):
        step = plan_step(row) if plan_step is not None else None
        if step is not None:
            if str(HERE) not in sys.path:
                sys.path.insert(0, str(HERE))
            import junction as _junction                  # noqa: PLC0415
            if _junction.step_over_limit(step, limit_m):
                return f"plans {row['rim_authored_by']}"
        return f"claim {row['rim_authored_by']}"
    causes = refused_at.get(tuple(row["rim_xz"]))
    if causes:
        return "refused " + "+".join(sorted(causes))
    return "earthwork"


def refused_at_of(st: dict) -> dict[tuple[int, int], set]:
    """What the stamp `st` was refused, per lattice sample: the causes of
    every clip, every skipped ray sample and every closure a gate forbade.
    See RIM_STEP_OWNER_DEPENDS_ON."""
    out: dict[tuple[int, int], set] = {}
    for wx, wz, cause, _h in (st["clip_at"] + st["ray_skip_at"]
                              + st["closure_refused_at"]):
        out.setdefault((int(round(wx)), int(round(wz))), set()).add(cause)
    return out


def carriageway_plan_y(plan: dict, x: float, z: float,
                       cache: dict | None = None) -> float | None:
    """The PLANNED road height at (x, z) if (x, z) is on that plan's
    carriageway, else None -- batter is earthwork, not a plan.  `cache` holds
    the plan's arrays by id, so the plan dict itself is never written."""
    arr = None if cache is None else cache.get(id(plan))
    if arr is None:
        arr = (np.array(plan["nodes"], dtype=np.float64),
               np.array(plan["profile_y"], dtype=np.float64),
               np.array(plan["is_bridge"], dtype=bool))
        if cache is not None:
            cache[id(plan)] = arr
    got = lateral_and_y(*arr, float(x), float(z))
    if got is None or got[0] > plan["width_m"] / 2.0 + SHOULDER_M + EDGE_EPS_M:
        return None
    return got[1]


def delta_gate(comps: dict, written: list[tuple[float, float]], L,
               tol_m: float, probe_step_m: float = 1.0) -> dict:
    """THE gate on a non-destructive terrain write: does any location piece's
    GROUND MOVE?

    Distance is a proxy; this is the thing itself.  A ribbon that deletes
    nothing can still leave a runestone hanging 3 m over a new road or buried
    under it, which is the operator's own condemning complaint arriving by a
    different route -- and it is the reason the loosened distance budget was
    granted.  A piece whose ground does not move is unaffected at ANY distance;
    one whose ground moves is damaged at any distance.

    Every instance within (its own reach + the terrain spread) of the written
    set is probed on a 1 m lattice over its own reach disc, and the worst
    absolute applied delta over that disc is compared to `tol_m`.  Probing the
    DISC rather than the marker matters: a location's pieces are spread over its
    reach (measured up to 35.77 m for TrollCave02), and the marker itself can be
    metres from the piece that would float.
    """
    reachable = L.instances_near(written, 60.0)
    worst, violations, probed = None, [], 0
    for inst in reachable:
        px, pz = inst["xz"]
        r = max(float(inst["reach_m"]), 1.0)
        n = max(1, int(r / probe_step_m))
        peak, peak_at = 0.0, (px, pz)
        for iz in range(-n, n + 1):
            for ix in range(-n, n + 1):
                qx, qz = px + ix * probe_step_m, pz + iz * probe_step_m
                if (qx - px) ** 2 + (qz - pz) ** 2 > r * r:
                    continue
                probed += 1
                d = abs(delta_at(comps, qx, qz))
                if d > peak:
                    peak, peak_at = d, (qx, qz)
        rec = {"name": inst["name"], "prefab": inst["prefab"],
               "xz": [round(px, 1), round(pz, 1)],
               "reach_m": inst["reach_m"], "protected": inst["protected"],
               "min_dist_m": inst["min_dist_m"],
               "worst_abs_delta_m": round(peak, 3),
               "worst_at": [round(peak_at[0], 1), round(peak_at[1], 1)]}
        if worst is None or peak > worst["worst_abs_delta_m"]:
            worst = rec
        if peak > tol_m:
            violations.append(rec)
    return {
        "verdict": "clear" if not violations else "VIOLATION",
        "tolerance_m": tol_m,
        "instances_probed": len(reachable),
        "positions_probed": probed,
        "worst": worst,
        "violations": violations,
        "method": ("MEASURED: for every location instance within 60 m of the "
                   "written set, the applied height delta is evaluated on a 1 m "
                   "lattice over that instance's own reach disc (reach is "
                   "max(exteriorRadius, interiorRadius, znviewReachM), up to "
                   "35.77 m) by bilinear blend of the four TerrainComp samples "
                   "around each probe, with unwritten samples contributing "
                   "zero -- which is how Heightmap renders the ground. The "
                   "worst absolute delta over the disc is compared to the "
                   "tolerance. Distance is a proxy for this; this is the thing "
                   "itself."),
        "tool": "tools/jumpstart/roads/ribbon.py::delta_gate",
    }


def _location_dump() -> str:
    """THE GENERATOR DUMP FOR THIS RUN'S SEED, RESOLVED, NEVER FROZEN.

    This used to be a two-entry tuple of absolute paths ending in
    `f6fe167f4fcd.json`, with no seed in the key at all.  MEASURED by
    RouteStandoff on Fresh42, 2026-09-24: that file is PIRATE68's dump of
    12,301 instances, while the run's own seed resolves to a dump of 12,376 --
    and S7-stathub-wtnorth was refused for a `ShipSetting01` at (124.2, 263.8)
    that exists in Pirate68 and does NOT exist in Fresh42.  The same wrong
    population also gated the batter, so the POI clips counted on this world
    were counted against another world's locations.  Same defect class as the
    ray bin: a key naming a population nobody checked.
    """
    return str(poimod.location_dump())


def poi_standoff_discs(zones: list[tuple[int, int]], pad_m: float = 64.0
                       ) -> list[tuple[float, float, float]]:
    """Per-instance keep-out discs for the BATTER, in the canonical budget.

    `reach + TERRAIN_SPREAD_M` per instance -- exactly the non-destructive
    budget `clearance.violations_for_samples` applies, with the caller's own
    half-extent zero because a batter sample IS the footprint point rather than
    the centre of one.  The destructive `standoff` (which adds `MARGIN_M` and
    `PROTECTED_EXTRA_M`) is deliberately NOT used: MEASURED in that module,
    applying it to a 6 m road refused 25 of 34 segments including every segment
    out of StartTemple, and this op deletes nothing, so it is budgeting against
    a hazard it does not have.

    Returned as discs rather than as a verdict because the batter CLIPS per
    sample: one sample too close to a runestone must not cost the other 40 km
    of side slope.  Restricted to the segment's own zone bounding box plus a
    zone of slack, so a 12,301-instance world costs a few dozen comparisons
    per sample instead of twelve thousand.

    THE ARITHMETIC NO LONGER LIVES HERE.  It used to -- `reach[i] +
    TERRAIN_SPREAD_M`, spelled out in this function -- and that made it a
    SECOND implementation of `violations_for_samples`' non-destructive budget.
    MEASURED cost of the duplication on the northern trunk: the two spellings
    drifted by exactly the road half-width, so the batter clipped at
    `reach + 1.0` while the write gate refused at `reach + 4.0`, and one
    shoulder sample 8.7 m from a 9.89 m `Ruin2` refused 1,779.4 m of
    `N0-brgs2-wmid`.  `Locations.hard_radius` is now the only place the number
    is computed and both gates read it.
    """
    sys.path.insert(0, str(JUMPSTART / "settlements"))
    import clearance

    L = clearance.load(_location_dump())
    xs = [zx * tcdata.ZONE_SIZE for zx, _ in zones]
    zs = [zz * tcdata.ZONE_SIZE for _, zz in zones]
    return L.keepout_discs(min(xs) - pad_m, max(xs) + pad_m,
                           min(zs) - pad_m, max(zs) + pad_m)


def location_check(comps: dict, written: list[tuple[float, float]],
                   road_written: list[tuple[float, float]] | None = None,
                   clipped: dict | None = None,
                   road_half_width_m: float | None = None) -> dict:
    """The clearance GATE, delegated to the canonical instrument, with the
    per-operation rule and the per-piece delta gate Main ruled.

    `tools/jumpstart/settlements/clearance.py` is canonical -- it is what
    `flatten.py`'s location gate and `Settlements`' `build.py` are wired to, and
    its reach is MEASURED PER TYPE rather than a declared radius plus an
    inferred constant.  A road passes `destructive=False`, which is a claim
    about the pipeline and an honest one: this tool emits `zones_generate` and
    `terrain_write` and NEVER `objects_clear`, so it cannot delete a location's
    ZDOs, which is the hazard `MARGIN_M` and `PROTECTED_EXTRA_M` budget for.
    The hazard it CAN cause -- a piece left floating or buried because its
    ground moved -- is gated directly by `delta_gate`, and `verdict_for_samples`
    REFUSES a non-destructive call that arrives without one.

    TWO DEFECTS ARE RECORDED HERE BECAUSE BOTH WERE MINE.
    (1) The first version measured each POI's distance to the ZONE CENTRE and
    returned a HARD-CODED `verdict: "clear"`.  It passed a StoneCircle 3.2 m
    from a zone centre against that circle's own 22 m radius, because the
    verdict was not computed from anything.  A check whose answer does not
    depend on its measurement is worse than none: it satisfies the gate.
    (2) The second measured the right thing but was a SECOND implementation,
    with an INFERRED 11 m overshoot where `clearance.py` had a per-type
    measurement.  Consolidating them did not just remove a duplicate -- it
    caught a WRONG VERDICT on the segment I was about to build first: my check
    called T3-temple-meadhall clear, the canonical gate refuses it with 5
    violations over 6,113 samples (StartTemple short by 84.9 m, Eikthyrnir by
    14.0 and 8.8, WoodHouse1 by 5.2, Dolmen01 by 2.7).
    (3) THE THIRD WAS THE EXPENSIVE ONE AND IT WAS A DISAGREEMENT, NOT AN
    ERROR.  Both gates were computing a boundary; they were computing
    DIFFERENT boundaries and each one was internally consistent.  `stamp`
    clipped the batter at `reach + TERRAIN_SPREAD_M`; this function asked for
    `reach + half_width_m + TERRAIN_SPREAD_M` and refused the whole write.
    The `half_width_m` was the road half-width, and it is DOUBLE COUNTED here:
    `road_written` is not a centreline, it is every 1 m carriageway sample out
    to the 4.0 m edge -- the footprint itself -- and `violations_for_samples`
    measures the MINIMUM distance from that set.  Adding the half-width again
    claims the footprint extends 3 m past its own outermost sample.  MEASURED
    on `N0-brgs2-wmid`: `Ruin2` at (-257.4, 1355.0), reach 9.89 m, nearest
    written sample 8.7 m, required 13.9 m -> refused; at the honest budget the
    requirement is 10.89 m and the same write is still refused, by 2.2 m,
    because that sample is genuinely SHOULDER inside a ruin.  Both numbers
    matter: the fix is not a loosening that lets N0 through, it is a gate that
    refuses N0 for the true reason and stops refusing legs whose only
    offending ground is batter the rasteriser already dropped.
    """
    sys.path.insert(0, str(JUMPSTART / "settlements"))
    import clearance

    L = clearance.load(_location_dump())
    # TWO SAMPLE SETS, TWO QUESTIONS, and conflating them is what made the
    # batter look like a wider ROAD to the distance rule.  `delta_gate` gets
    # EVERY written sample, carriageway and batter alike, because its question
    # is "does any piece's ground move" and a batter sample moves ground.  The
    # distance rule gets the CARRIAGEWAY only, because a batter sample that
    # crosses the boundary is CLIPPED by `stamp` and counted in `clipped`,
    # while a carriageway sample that crosses it is a hole in the road surface
    # and refuses the write.  One boundary, two costs.
    #
    # `half_width_m=0.0` IS THE WHOLE POINT OF (3) ABOVE: these samples ARE
    # the footprint, so the caller's own half-extent is zero -- exactly as
    # `violations_for_samples`' own docstring requires of a pad lattice.  The
    # road's half-width is still RECORDED, as provenance, because a reader has
    # to be able to tell "zero because the footprint is the sample set" from
    # "zero because somebody forgot".
    gate = delta_gate(comps, written, L, clearance.DELTA_TOL_M)
    road = list(road_written if road_written is not None else written)
    v = L.verdict_for_samples(road, half_width_m=0.0,
                              destructive=False, delta_gate=gate,
                              clipped=clipped)
    v["half_width_m_is_zero_because"] = (
        "the tested set is the rasterised CARRIAGEWAY footprint -- every 1 m "
        "sample out to the road edge -- and violations_for_samples measures "
        "the MINIMUM distance from it to each instance, so a half-extent "
        "about those samples would count the road's own width twice")
    v["road_half_width_m"] = road_half_width_m
    v["dump"] = _location_dump()
    v["destructive"] = False
    v["destructive_claim"] = (
        "this terrain op emits zones_generate + terrain_write only and never "
        "objects_clear, so it cannot delete a location's ZDOs; verifiable from "
        "the ledger records for this segment. Road-surface vegetation clearing "
        "is a SEPARATE op recorded by tools/jumpstart/roads/clear.py, which "
        "passes destructive=True to this same instrument and censuses each "
        "removal cylinder live in the same call")
    v["samples_tested_carriageway"] = len(road)
    v["samples_in_delta_gate"] = len(written)
    return v


# ---------------------------------------------------------------------------
# the thing the operator judges: is it CONTINUOUS and WALKABLE
# ---------------------------------------------------------------------------

# Player::UpdateMovement slides the character when the ground normal is steeper
# than 38 deg, so the hard limit on a walkable surface is tan(38 deg).  A road
# the operator slides down is not a road.
SLIDE_ANGLE_DEG = 38.0
SLIDE_GRADIENT = math.tan(math.radians(SLIDE_ANGLE_DEG))
# Stated as an assertion rather than a comment: `BATTER_GRADE` is defined at
# the top of the file (it is needed by `segment_zones`' default reach) and it
# IS this limit.  Two spellings of one number is how they drift apart.
assert abs(BATTER_GRADE - SLIDE_GRADIENT) < 1e-12, (
    "BATTER_GRADE must be the 38 deg slide limit")
# The BASELINE THE VERDICT IS TAKEN OVER.  The 1 m figure is a DIFFERENT
# QUANTITY: it is the slope of one lattice edge, and a 1 m rise between two
# adjacent samples on a fitted 8% profile is a rounding artefact of the integer
# lattice, not a wall.  Measuring the verdict at 1 m produced a false
# NOT WALKABLE earlier tonight, so both are reported and the verdict is taken
# over 8 m -- roughly the run the capsule actually traverses while the collider
# averages the mesh under it.
VERDICT_BASELINE_M = 8.0


def judge_gap(stations: list[dict], i: int, j: int,
              has_before: bool, has_after: bool,
              bridged: bool = False) -> dict:
    """Can a walker cross the gap running from station `i` to station `j`.

    `stations` carry `s` (arclength), `x`, `z` and `y` (the APPLIED surface:
    generated plus this write's bilinear delta, so an unwritten sample reads
    its generated height).  `i..j` are the stations NOT on written road;
    `has_before` / `has_after` say whether there is a written rim on that
    side, and the judged run extends onto it, because the step the operator
    meets is the one OFF the rim and the one back ON.

    THE WORST PAIR IS THE ONE FURTHEST OVER ITS OWN LIMIT, not the biggest
    rise: consecutive stations are not all the same distance apart (a
    polyline vertex lands mid-pitch), so a 0.40 m rise over 0.5 m is a wall
    and a 0.70 m rise over 1.0 m is not, and ranking on the rise alone would
    report the second and refuse on the first.
    """
    worst = 0.0
    limit = 0.0
    at = None
    unread = 0
    lo = i - 1 if has_before else i
    hi = j + 1 if has_after else j
    for k in range(lo, hi):
        a, b = stations[k], stations[k + 1]
        if a.get("y") is None or b.get("y") is None:
            unread += 1
            continue
        lim = SLIDE_GRADIENT * max(b["s"] - a["s"], 1e-6)
        rise = abs(b["y"] - a["y"])
        if rise - lim > worst - limit:
            worst, limit = rise, lim
            at = [round(a["x"], 1), round(a["z"], 1)]
    # UNMEASURED IS NOT WALKABLE.  A station the applied surface could not
    # answer for is a metre of this gap nobody read, and "I could not measure
    # it" has never been allowed to mean "it is fine" here.
    passable = (None if bridged
                else False if unread
                else worst <= limit)
    return {"step_into_m": round(worst, 3), "step_into_at": at,
            "step_into_limit_m": round(limit, 4),
            "stations_unread": unread, "passable": passable}


def selftest_gap_judgement(verbose: bool = True) -> dict:
    """PROVE THE GAP VERDICT ON THE KNOWN-BAD STATE, BOTH DIRECTIONS.

    The bad state is `T13-temple-harboursouth` seq 5322, which shipped
    WALKABLE with a 2.7 m unwritten notch in its own carriageway.  Its own
    recorded gap reads

        step_across_m  -0.001        <- the two rims against each other
        fill_at_edges_m [1.083, 0.381]
        rasteriser_refused {protected: 6}
        rasteriser_refused_worst {at [13.0, -256.0], full_m 1.147}

    and the acceptance gate, reading the SAVE afterwards, refuses the notch:
    vertex (14,-255) 32.230 -> (14,-256) 31.436 is 0.794 m over 1 m of
    lattice against 0.7813, which is one of the two links that keep
    `harboursouth` unreachable.  A judgement that only reads `step_across_m`
    answers -0.001 and passes, which is what happened.

    Stations here are the real lattice heights off the save at the 1 m pitch
    the notch is on, so the number the case asserts is the number the world
    has.
    """
    def st(s, x, z, y):
        return {"s": float(s), "x": float(x), "z": float(z), "y": y}

    # (13,-255) 32.230 rim | (14,-256) 31.436 unwritten | (15,-257) 32.230 rim
    bad = [st(0.0, 13.0, -255.0, 32.230),
           st(1.0, 14.0, -256.0, 31.436),
           st(2.0, 15.0, -257.0, 32.230)]
    got_bad = judge_gap(bad, 1, 1, True, True)
    # The rims agree to 0.000 and the notch is still 0.794 m deep.
    assert abs((bad[2]["y"] - bad[0]["y"])) < 1e-9, "the two rims agree"
    assert got_bad["passable"] is False, got_bad
    assert abs(got_bad["step_into_m"] - 0.794) < 1e-3, got_bad
    assert abs(got_bad["step_into_limit_m"] - 0.7813) < 1e-3, got_bad

    # THE OTHER DIRECTION.  The same gap once the refused samples are
    # written: the notch floor comes up to the carriageway and the crossing
    # is clean.  Without this the case would pass on a function that refuses
    # every gap.
    good = [st(0.0, 13.0, -255.0, 32.230),
            st(1.0, 14.0, -256.0, 32.230),
            st(2.0, 15.0, -257.0, 32.230)]
    got_good = judge_gap(good, 1, 1, True, True)
    assert got_good["passable"] is True, got_good

    # AND A GAP THAT IS DEEP BUT WALKABLE STAYS WALKABLE.  0.70 m over 1 m is
    # 35 deg: steep, under the slide limit, and not this check's business.
    ok = [st(0.0, 0.0, 0.0, 10.0), st(1.0, 1.0, 0.0, 9.3),
          st(2.0, 2.0, 0.0, 10.0)]
    assert judge_gap(ok, 1, 1, True, True)["passable"] is True

    # A STATION NOBODY COULD READ IS NOT A PASS.
    blind = [st(0.0, 0.0, 0.0, 10.0), st(1.0, 1.0, 0.0, None),
             st(2.0, 2.0, 0.0, 10.0)]
    assert judge_gap(blind, 1, 1, True, True)["passable"] is False

    # A BRIDGED RUN IS MEASURED AND EXEMPT: the ribbon must not write terrain
    # across a span, and the crossings gate judges the deck.  It must still
    # REPORT the drop, or a bridge that is really a hole goes silent.
    br = judge_gap(bad, 1, 1, True, True, bridged=True)
    assert br["passable"] is None and abs(br["step_into_m"] - 0.794) < 1e-3, br

    out = {"known_bad": got_bad, "repaired": got_good, "bridged": br}
    if verbose:
        print(f"  known-bad T13 notch: step_into {got_bad['step_into_m']} m "
              f"against {got_bad['step_into_limit_m']} m at "
              f"{got_bad['step_into_at']}, passable {got_bad['passable']} "
              f"-- while the two rims read 0.000 m against each other")
        print(f"  repaired: step_into {got_good['step_into_m']} m, passable "
              f"{got_good['passable']}")
        print("ribbon gap-judgement selftest OK")
    return out


def water_sign(taken_gen: dict, taken_after: dict,
               taken_now: dict | None = None) -> dict:
    """WOULD THIS WRITE REMOVE WATER?  The SIGN, never the magnitude.

    Ground the SEED drew below the water plane IS WATER.  Raising it to or
    above the plane does not make a shallow bay, it DELETES the bay, and no
    later write can put it back: the generated field is the only record that
    it was ever there.  MEASURED by `CloseHarbours` on Ulfsland 2026-09-22:
    the LittleBoat berth at (-307.04, -119.59) has 38 of 38 footprint
    samples not connected to open sea and 14 of 2,340 submerged samples
    raised to or above the 30 m plane, 56 m2 of water gone - and the nearest
    recorded terrain write is `road_S12_brgs1_boathouse` at 12.8 m, nearer
    than the next by a factor of three.

    A CUT IS ALWAYS PERMITTED.  Lowering a bed adds water, and a guard that
    refused all ground movement near water would take every legitimate
    dredge with it - which is why this is a sign test and not a proximity
    one.

    AND FOR ITS FIRST DAY IT WAS A STATE TEST WEARING A SIGN TEST'S NAME.
    The rule read `taken_after[k] >= WATER_LEVEL_M`, with no reference to
    where the sample started, so over ground a PRIOR write had already
    drained it refused a CUT.  MEASURED on T9's bank A (677, -94): the seed
    drew 29.067 and `terrain_write road_T9_wttown_dockshore` (seq 234) left
    it at 31.000, so 1.933 m of drawn water is already gone.  Cutting it to
    the 30.600 deck datum GIVES 0.400 m OF THAT BACK, and the old rule
    called it a drain -- because the sample is still above the plane after a
    write that moved it DOWN.  It attributed seq 234's drainage to whoever
    touched the sample next, and the practical effect was that THE ONE
    REMEDY A DRAINED HARBOUR HAS WAS THE THING IT BLOCKED.  The selftest
    could not see it: its dredge case takes the sample below the plane,
    which is the one region where the state and the delta agree.

    SO IT IS JUDGED AS A DELTA, NOT A STATE - the same rule the rim gate
    already follows.  A write is refused when it RAISES a seed-submerged
    sample AND leaves it at or above the plane.  `after <= now` is always
    clear, because a write that lowers ground has removed no water that was
    not already gone before it ran.

    `taken_now` is the applied surface THIS WRITE STARTS FROM.  It is
    optional only so that a caller with no prior state still gets the strict
    absolute rule; every caller inside this file has it, and `main` has been
    computing it four lines from this call and using it to report
    `raised_by_m` while the verdict was taken without it.

    Lives here rather than inline in `main` so the selftest drives THE
    SHIPPING RULE rather than a copy of it.
    """
    submerged = [k for k, g in taken_gen.items() if g < WATER_LEVEL_M]
    drains, cuts_over_prior = [], []
    for k in submerged:
        after = taken_after.get(k, -1e9)
        if after < WATER_LEVEL_M:
            continue
        now = None if taken_now is None else taken_now.get(k)
        if now is not None and after <= now + 1e-9:
            # Already drained by somebody else, and this write does not
            # deepen it.  Counted and named rather than silently dropped:
            # an exemption nobody can see is how a guard stops guarding.
            cuts_over_prior.append(k)
            continue
        drains.append(k)
    return dict(verdict="REFUSED" if drains else "CLEAR",
                moved_samples=len(taken_after),
                submerged_samples=len(submerged),
                drained_samples=len(drains),
                drained=sorted(drains),
                already_drained_not_deepened=len(cuts_over_prior),
                already_drained_xz=sorted(cuts_over_prior),
                judged_as="delta" if taken_now is not None else "state",
                plane_m=WATER_LEVEL_M,
                population=(f"the {len(submerged)} of {len(taken_after)} "
                            f"lattice sample(s) THIS WRITE MOVES whose "
                            f"GENERATED height is below the "
                            f"{WATER_LEVEL_M} m plane; of those, "
                            f"{len(drains)} are RAISED to or above it by "
                            f"this write and {len(cuts_over_prior)} were "
                            f"already above it and are not deepened"))


def selftest_water_sign(verbose: bool = True) -> dict:
    """BOTH SIGNS, and the second one is why this is not a proximity rule.

    A guard that only ever refused would take every dredge with it, and a
    dredge is the ONE remedy a drained harbour has.
    """
    # A FILL over submerged ground: the S12 shape, and the refusal.
    gen = {(0, 0): 28.5, (1, 0): 29.2, (2, 0): 31.0}
    fill = {(0, 0): 30.6, (1, 0): 29.9, (2, 0): 31.4}
    bad = water_sign(gen, fill)
    assert bad["verdict"] == "REFUSED", bad
    assert bad["submerged_samples"] == 2 and bad["drained_samples"] == 1, bad
    assert bad["drained"] == [(0, 0)], bad
    # A DREDGE over the same ground: deeper water, and it must PASS.
    dredge = {(0, 0): 27.0, (1, 0): 28.0, (2, 0): 31.4}
    good = water_sign(gen, dredge)
    assert good["verdict"] == "CLEAR", good
    assert good["submerged_samples"] == 2 and good["drained_samples"] == 0, good
    # A FILL that stays UNDER the plane is not a drain either: the test is
    # the plane, not the direction of the move.
    shallow = water_sign(gen, {(0, 0): 29.4, (1, 0): 29.9, (2, 0): 31.4})
    assert shallow["verdict"] == "CLEAR", shallow
    # AND A WRITE NOWHERE NEAR WATER HAS AN EMPTY POPULATION AND SAYS SO.
    dry = water_sign({(9, 9): 40.0}, {(9, 9): 41.0})
    assert dry["verdict"] == "CLEAR" and dry["submerged_samples"] == 0, dry

    # ---- THE DELTA, WHICH IS WHAT THIS GUARD CLAIMS TO BE -------------
    #
    # T9's bank A, on its own engine and seed numbers: the seed drew 29.067
    # and `terrain_write road_T9_wttown_dockshore` (seq 234) left it at
    # 31.000.  1.933 m of drawn water is ALREADY GONE and this write did not
    # take it.  Cutting to the 30.600 deck datum gives 0.400 m of it back.
    t9_gen = {(677, -94): 29.067}
    t9_now = {(677, -94): 31.000}
    restore = water_sign(t9_gen, {(677, -94): 30.600}, t9_now)
    assert restore["verdict"] == "CLEAR", restore
    assert restore["submerged_samples"] == 1, restore
    assert restore["drained_samples"] == 0, restore
    assert restore["already_drained_not_deepened"] == 1, restore
    assert restore["judged_as"] == "delta", restore
    # AND THE SAME SAMPLE RAISED IS STILL REFUSED.  Without this the
    # exemption is a bypass: "it was already above the plane" would let a
    # write pile another half-metre onto a drained bay.
    deepen = water_sign(t9_gen, {(677, -94): 31.400}, t9_now)
    assert deepen["verdict"] == "REFUSED", deepen
    assert deepen["drained"] == [(677, -94)], deepen
    assert deepen["already_drained_not_deepened"] == 0, deepen
    # A FIRST DRAIN IS UNCHANGED - the S12 shape, now with a prior state to
    # prove the rule did not go soft on the case it exists for.
    first = water_sign({(0, 0): 28.5}, {(0, 0): 30.6}, {(0, 0): 29.5})
    assert first["verdict"] == "REFUSED", first
    # A DREDGE IS UNCHANGED.
    deeper = water_sign({(0, 0): 28.5}, {(0, 0): 28.0}, {(0, 0): 29.5})
    assert deeper["verdict"] == "CLEAR" and deeper["drained_samples"] == 0
    # A RAISE THAT STAYS UNDER THE PLANE is permitted by BOTH forms, and it
    # is the shape this world actually shipped: `CloseHarbours` measured the
    # LittleBoat pool at boathouse-temple-strait hanging on ONE vertex at
    # (-319, -48) where the seed held 2.343 m of depth and the save holds
    # 2.176 m against the 2.200 m a LittleBoat needs - +0.167 m, 7 % of the
    # depth, 27.8 m BELOW the surface, and it landlocked a harbour.  IT IS
    # NOT A DRAIN AND THIS GUARD IS NOT THE ONE THAT CATCHES IT; the
    # predicate is `seed_depth >= required AND applied_depth < required` and
    # it belongs to `accept.sections.harbours`.  The case is here so that
    # the CLEAR verdict is a KNOWN LIMIT rather than an untested silence.
    shoal = water_sign({(-319, -48): 27.657}, {(-319, -48): 27.824},
                       {(-319, -48): 27.657})
    assert shoal["verdict"] == "CLEAR", shoal
    assert shoal["submerged_samples"] == 1 and shoal["drained_samples"] == 0
    # THE NEUTER: take the prior state away and the SAME restoration that
    # the fixed rule clears comes back REFUSED.  This is the known-bad
    # behaviour, kept permanently, so nobody can quietly drop `taken_now`
    # at a callsite and lose the fix without a case going red.
    neuter = water_sign(t9_gen, {(677, -94): 30.600})
    assert neuter["verdict"] == "REFUSED", neuter
    assert neuter["judged_as"] == "state", neuter
    if verbose:
        print(f"  fill over water: {bad['verdict']}, "
              f"{bad['drained_samples']} of {bad['submerged_samples']} "
              f"submerged sample(s) raised to or above {bad['plane_m']} m")
        print(f"  dredge over the same ground: {good['verdict']}, "
              f"{good['drained_samples']} of {good['submerged_samples']}")
        print(f"  T9 bank A 31.000 -> 30.600 over seed 29.067: "
              f"{restore['verdict']} as a delta, {neuter['verdict']} as a "
              f"state (the known-bad rule, kept as the neuter)")
        print(f"  the same sample 31.000 -> 31.400: {deepen['verdict']}")
        print("ribbon water-sign selftest OK")
    return dict(fill=bad, dredge=good, shallow=shallow, dry=dry,
                restore=restore, deepen=deepen, first=first, deeper=deeper,
                shoal=shoal, neuter=neuter)


# ---------------------------------------------------------------------------
# THE BATTER'S THREE KEYS, AND A NEUTER FOR EACH DEPENDENCY IN BOTH DIRECTIONS
# ---------------------------------------------------------------------------
#
# Every case below is over a CONSTRUCTED population built in this file.  The
# denominator is therefore unpatchable by any neuter: a neuter may only change
# the RULE, never the ground it is asked about, so an arm that stops flipping
# has stopped flipping because the rule moved.


def _flat_patch(zx: int, zz: int, height_at) -> "object":
    """One zone's 65x65 GENERATED heights from a callable, in memory."""
    sys.path.insert(0, str(JUMPSTART / "terraform"))
    import heights as heightsmod                             # noqa: PLC0415
    cx, cz = tcdata.zone_centre(zx, zz)
    half = (tcdata.PITCH // 2) * tcdata.SCALE
    hs = []
    for i in range(tcdata.PITCH):
        wz = cz + (i - tcdata.PITCH // 2) * tcdata.SCALE
        for j in range(tcdata.PITCH):
            wx = cx + (j - tcdata.PITCH // 2) * tcdata.SCALE
            hs.append(float(height_at(wx, wz)))
    return heightsmod.Patch(id=f"z_{zx}_{zz}", cx=cx, cz=cz, half=half + 0.5,
                            step=tcdata.SCALE, n=tcdata.PITCH, heights=hs,
                            biomes=[1] * (tcdata.PITCH * tcdata.PITCH))


def _rasterise(nodes, road_y: float, height_at, zones=None,
               placed=(), width_m: float = 6.0,
               is_bridge=None,
               ray_key_carries_bearing: bool = True,
               ray_walks_past_its_bin: bool = True,
               water_fill_ends_the_ray: bool = False,
               feather_toe_on: bool = True,
               joint_claim_band_on: bool = False,
               profile_y=None,
               road_claim_at=None,
               interstice_needs_open_taper: bool = True,
               feather_toward_ground: bool = True,
               feather_pins_hold: bool = True,
               feather_binds_on_preexisting: bool = False,
               **stamp_kw) -> dict:
    """Run the SHIPPING `stamp` over a constructed segment and ground.

    THE COVER IS DERIVED FROM THE REACH, exactly as the build path derives it
    ("N CANDIDATE zones in the corridor cover (batter reach included)").  It
    used to default to the single zone (0, 0) -- 65 m across -- while these
    fixtures lay 48 m of centreline whose end fans reach 14.5 m beyond the
    terminal nodes, so the outermost rays walked off the patches.  THAT WAS
    INVISIBLE while a ray leaving its bin ended silently: the moment a ray
    walks its own radial to a named exit it reads `unread_ground`, which is
    the FIXTURE being short of its own question and not the rule under test.
    MEASURED on the causeway arm: 32 of 184 rays, worst residual 2.101 m, all
    of them at |x| >= 32 -- the edge of the one zone.
    """
    seg = {"id": "fixture", "nodes": [list(n) for n in nodes],
           "profile_y": ([road_y] * len(nodes) if profile_y is None
                         else [float(p) for p in profile_y]),
           "is_bridge": ([False] * len(nodes) if is_bridge is None
                         else [bool(b) for b in is_bridge]),
           "width_m": width_m, "length_m": 0.0}
    if zones is None:
        reach = width_m / 2.0 + SHOULDER_M + BATTER_MAX_M
        xs = [float(n[0]) for n in nodes]
        zs = [float(n[1]) for n in nodes]
        zx0, zz0 = tcdata.zone_of(min(xs) - reach, min(zs) - reach)
        zx1, zz1 = tcdata.zone_of(max(xs) + reach, max(zs) + reach)
        zones = tuple((zx, zz) for zx in range(zx0, zx1 + 1)
                      for zz in range(zz0, zz1 + 1))
    patches = {f"z_{zx}_{zz}": _flat_patch(zx, zz, height_at)
               for zx, zz in zones}
    comps, st = stamp(seg, patches, list(zones), PadKeepOut([], []),
                      placed_objects=list(placed),
                      ray_key_carries_bearing=ray_key_carries_bearing,
                      ray_walks_past_its_bin=ray_walks_past_its_bin,
                      water_fill_ends_the_ray=water_fill_ends_the_ray,
                      feather_toe_on=feather_toe_on,
                      joint_claim_band_on=joint_claim_band_on,
                      road_claim_at=road_claim_at,
                      interstice_needs_open_taper=interstice_needs_open_taper,
                      feather_toward_ground=feather_toward_ground,
                      feather_pins_hold=feather_pins_hold,
                      feather_binds_on_preexisting=(
                          feather_binds_on_preexisting),
                      **stamp_kw)
    # THE COMPILERS COME BACK TOO, because the only honest way to ask "what
    # step does this surface leave" is `delta_at` over the ground the run
    # actually wrote -- the same composition `applied_at` uses -- and not a
    # reconstruction of the plane the fixture believes it asked for.
    st["_comps"] = comps
    return st


# A TERRAIN CAUSEWAY 5.5 m ABOVE A BED THE SEED DREW UNDER THE WATER PLANE.
# Its flank cannot fall below the 30.0 m plane until 3.8 m of run, so every
# batter sample it reaches over the bed lifts that bed OUT OF THE WATER.  This
# is T1-portalhub-temple's 206 batter samples in miniature -- the shape the
# resulting-height key exists to stop at the bank, and the shape the old
# permit-every-raise rule wrote.
_DRAIN_BED_M, _DRAIN_BANK_M, _DRAIN_ROAD_Y = 27.5, 35.0, 33.0
# A CUTTING beside standing water: natural ground below the plane and a road
# BELOW that, so the batter would take the bank DOWN to the waterline.  This
# is the shape the gate exists for and it must never be written.
_BANK_M, _CUTTING_Y = 29.0, 26.0


def _drain_ground(wx: float, wz: float) -> float:
    return _DRAIN_BED_M if abs(wz) <= 12.0 else _DRAIN_BANK_M


def _cutting_ground(wx: float, wz: float) -> float:
    return _BANK_M


# A TOE THAT STAYS UNDER THE PLANE.  A DRY bank at 30.2 with a shoal at 29.4
# beside it and the road at 30.7: the first batter sample sits at lat 5, where
# the plane has already fallen to 29.92, so the flank RAISES the bed by
# 0.52 m and the water over it is still water.  This is the population the
# direction key was added for, stated at the granularity that survives the
# resulting-height key.  The bank is deliberately ABOVE the plane: a bank
# under it is itself water, and the fans past the terminal nodes would then
# be drains rather than the case under test.
_SHOAL_BED_M, _SHOAL_BANK_M, _SHOAL_ROAD_Y = 29.4, 30.2, 30.7


def _shoal_ground(wx: float, wz: float) -> float:
    return _SHOAL_BANK_M if abs(wz) <= 4.0 else _SHOAL_BED_M


# A SUBMERGED DIMPLE WITH LAND BEYOND IT -- the shape the `water_fill` clip
# abandoned when it ENDED the ray.  A road at 34.0 over land at 30.4 with a
# 1 m wide pool at 28.0 between lat 5 and lat 6: the plane is still 1.26 m
# and 0.48 m above DRY GROUND at lat 7 and lat 8, and a ray that stops at the
# pool never lays either of them.  The pool is deliberately ONE METRE wide --
# wide enough that the gate fires, narrow enough that the plane is still
# above land on the far side, which is exactly the production shape:
# `Causeway` measured 3,315 land samples lying outboard of a `water_fill`
# stop across four Fresh42 segments.
_LAGOON_BED_M, _LAGOON_LAND_M, _LAGOON_ROAD_Y = 28.0, 30.4, 34.0


def _lagoon_ground(wx: float, wz: float) -> float:
    return (_LAGOON_BED_M if 5.0 < abs(wz) <= 6.0 else _LAGOON_LAND_M)


def selftest_batter_water_gate(verbose: bool = True) -> dict:
    """THE WATER GATE, KEYED ON DIRECTION, AND WHERE ITS JOB ENDS.

    Arm 1 is a TOE THAT STAYS UNDER THE PLANE: it must be written, and it is
    what the old state-keyed rule refused -- 70 of 70 refusals on
    T1-portalhub-temple were raises, and one of them is the 5.1433 m cliff the
    rim gate stopped the whole write for.

    Arm 2 is a BANK BEING CUT DOWN TO THE WATERLINE: it must be refused, and
    it is the hazard the gate exists for -- a re-route cannot undo it.

    Arm 3 IS THE CONTRACT WITH `water_sign`, and it is the arm that MOVED.
    A flank that lifts the bed OUT of the water used to be LAID here and left
    to `water_sign`, whose stated remedy is the ROUTE.  A batter toe has no
    routing remedy -- the ray is the only thing that can stop -- and the
    permit was measured to be protecting an empty set: 158 stations across
    all fourteen Fresh42 segments have their levelled footprint below the
    30 m plane and 0 OF 158 ARE UNBRIDGED, so the causeway flank the permit
    existed for is not reachable from any plan this router emits.  So arm 3
    is now CLIPPED at `water_fill`, `water_sign` comes back CLEAR, and the
    wall that is left STANDS ON SEED-SUBMERGED GROUND -- which is the whole
    difference from the 2026-09-24 experiment, whose walls stood on land.

    NEUTER C therefore drops `resulting_height` -- the shipped permit,
    verbatim -- and arm 3 must come back LAID with the water drained and
    `water_sign` REFUSED, while arms 1 and 2 do not move.
    NEUTER A drops `earthwork_direction` -- the old state rule, verbatim --
    and arm 1 must come back REFUSED while arm 2 is unchanged.
    NEUTER B drops `water_plane` and arm 2 must come back WRITTEN while arm 1
    is unchanged.  One arm moves per neuter, in both directions, and no
    neuter can move the ground, which is built here.
    """
    assert "earthwork_direction" in BATTER_WATER_GATE_DEPENDS_ON
    assert "water_plane" in BATTER_WATER_GATE_DEPENDS_ON
    assert "resulting_height" in BATTER_WATER_GATE_DEPENDS_ON
    nodes = [(-24.0, 0.0), (24.0, 0.0)]

    def run(ground, road_y, ends_the_ray: bool = False) -> dict:
        st = _rasterise(nodes, road_y, ground,
                        water_fill_ends_the_ray=ends_the_ray)
        comps = st["_comps"]
        gen, after = {}, {}
        for x, z in st["written"]:
            if abs(z) <= 4.0:
                continue
            k = (int(round(x)), int(round(z)))
            gen[k] = ground(x, z)
            after[k] = ground(x, z) + delta_at(comps, x, z)
        # `water_sign`'s OWN verdict over the batter this run laid, which is
        # the gate that actually owns the hazard.
        sign = water_sign(gen, after)
        # WHERE THE WALLS THIS ARM LEAVES ACTUALLY STAND.  A `water_fill`
        # clip that leaves a wall on DRY ground is the 2026-09-24 experiment
        # coming back; one that leaves it under the water surface is a
        # shoreline.  The two are indistinguishable from a count, so the
        # count is not what is asserted.
        fill_walls = [(wx, wz, h) for wx, wz, cause, h in st["clip_at"]
                      if cause == "water_fill"]
        return {"batter_samples": st["samples_batter"],
                "water_edge_clips": st["batter_clipped_water_edge"],
                "water_fill_clips": st.get("batter_clipped_water_fill", 0),
                "water_fill_skipped": st["water_fill_skipped"],
                "water_fill_walls": len(fill_walls),
                "water_fill_walls_on_submerged_ground": sum(
                    1 for wx, wz, _h in fill_walls
                    if ground(wx, wz) < WATER_LEVEL_M),
                # THE LAND OUTBOARD OF THE WATER, which is the population the
                # `break` abandoned: samples this run laid on ground the seed
                # drew ABOVE the plane.
                "land_batter_samples": sum(
                    1 for k, g in gen.items() if g >= WATER_LEVEL_M),
                "wet_samples": sum(1 for k, g in gen.items()
                                   if g < WATER_LEVEL_M),
                "drained_samples": sign["drained_samples"],
                "water_sign": sign["verdict"],
                "worst_wall_m": st["walls_left_max_m"],
                "met_ground": st["batter_met_ground"]}

    def arms() -> dict:
        return {"shoal": run(_shoal_ground, _SHOAL_ROAD_Y),
                "cutting": run(_cutting_ground, _CUTTING_Y),
                "causeway": run(_drain_ground, _DRAIN_ROAD_Y)}

    shipping = arms()
    # THE FIXTURE MUST DISCRIMINATE.  A flank that never reaches submerged
    # ground, or a cutting the gate never fires on, would pass every rule ever
    # written.
    if shipping["cutting"]["water_edge_clips"] == 0:
        raise AssertionError(
            f"this fixture exists to make the gate FIRE on a cutting beside "
            f"standing water and it did not: {shipping['cutting']}")
    if shipping["shoal"]["wet_samples"] == 0:
        raise AssertionError(
            f"this fixture exists to LAY ground under the plane and it laid "
            f"none: {shipping['shoal']}")
    verdicts = {"shipping": shipping}
    for name, gate in (
        ("neuter_drop_direction",
         lambda generated, delta: ("water_edge"
                                   if generated < WATER_LEVEL_M else None)),
        ("neuter_drop_water_plane",
         lambda generated, delta: None),
        # NEUTER C: THE SHIPPED PERMIT, VERBATIM.  `resulting_height` dropped,
        # so every raise over submerged ground is laid whatever it does to
        # the water -- which is the rule this file shipped until the 158/158
        # measurement retired it.
        ("neuter_drop_resulting_height",
         lambda generated, delta: ("water_edge"
                                   if delta < 0.0 and generated < WATER_LEVEL_M
                                   else None)),
    ):
        real = globals()["batter_water_gate"]
        globals()["batter_water_gate"] = gate
        try:
            verdicts[name] = arms()
        finally:
            globals()["batter_water_gate"] = real
    # SURVIVOR, AS A VERDICT: the toe under the plane is laid, no wall is left,
    # and `water_sign` is CLEAR about it because the water is still there.
    ship_s = shipping["shoal"]
    assert ship_s["water_edge_clips"] == 0, ship_s
    assert ship_s["wet_samples"] > 0, ship_s
    assert ship_s["drained_samples"] == 0, ship_s
    assert ship_s["water_sign"] == "CLEAR", ship_s
    assert ship_s["worst_wall_m"] == 0.0, ship_s
    # ...the cutting is refused, with the wall recorded rather than cut...
    ship_x = shipping["cutting"]
    assert ship_x["water_edge_clips"] > 0, ship_x
    # ...and the flank that lifts the bed OUT of the water is CLIPPED, with
    # `water_sign` CLEAR afterwards and EVERY wall it leaves standing on
    # seed-submerged ground.  The count of walls is not the verdict: a
    # `water_fill` clip that left its wall on dry land would be the
    # 2026-09-24 experiment, which is why WHERE they stand is asserted and
    # not HOW MANY.
    ship_c = shipping["causeway"]
    assert ship_c["water_fill_clips"] > 0, ship_c
    assert ship_c["drained_samples"] == 0, ship_c
    assert ship_c["water_sign"] == "CLEAR", ship_c
    assert ship_c["water_fill_walls"] > 0, ship_c
    assert (ship_c["water_fill_walls_on_submerged_ground"]
            == ship_c["water_fill_walls"]), ship_c
    # NEUTER E -- THE SAME RULE, BUT THE RAY ENDS AT THE WATER instead of
    # walking past it.  This is a neuter of the CONTROL FLOW rather than of
    # the rule, and it is the state `batter_water_rule` landed in.  The land
    # OUTBOARD of the submerged strip is then abandoned mid-taper.
    #
    # MEASURED by `Causeway` on Fresh42 with the `break`: rays stopped by
    # `water_fill` and land samples left lying outboard of the stop on the
    # SAME ray -- T4 42/719, T9 17/312, S5 17/247, T1 15/2,037; 3,315
    # abandoned samples on four segments, and T4's rim gate went 10 caused /
    # 1.4894 m to 22 caused / 3.2592 m, four of its five new rim cells
    # standing ON LAND at 30.767 to 31.564 m.
    ship_l = run(_lagoon_ground, _LAGOON_ROAD_Y)
    ends = run(_lagoon_ground, _LAGOON_ROAD_Y, ends_the_ray=True)
    verdicts["lagoon"] = ship_l
    verdicts["neuter_water_fill_ends_the_ray"] = ends
    # THE FIXTURE MUST DISCRIMINATE: a pool the gate never fires on, or a
    # pool with no land beyond it, would pass every control flow ever
    # written.
    if ship_l["water_fill_clips"] == 0:
        raise AssertionError(f"the pool never fired the gate: {ship_l}")
    assert ship_l["water_fill_skipped"] > 0, ship_l
    assert ship_l["land_batter_samples"] > 0, ship_l
    assert ends["water_fill_skipped"] == 0, ends
    assert ends["land_batter_samples"] < ship_l["land_batter_samples"], ends
    assert ends["batter_samples"] < ship_l["batter_samples"], ends
    # ...and the skip cannot reinstate a drain, because the rule is asked of
    # EVERY sample outboard and still refuses any that would clear the plane.
    assert ends["drained_samples"] == 0, ends
    assert ship_l["drained_samples"] == 0, ship_l
    assert ship_l["water_sign"] == "CLEAR", ship_l
    assert ship_c["water_fill_skipped"] > 0, ship_c
    # NEUTER A: the toe under the plane comes back REFUSED, the cutting is
    # unchanged.
    a = verdicts["neuter_drop_direction"]
    assert a["shoal"]["water_edge_clips"] > 0, a
    assert a["shoal"]["wet_samples"] == 0, a
    assert a["cutting"]["water_edge_clips"] == ship_x["water_edge_clips"], a
    # NEUTER B: the cutting is written into the bank, the toe is unchanged.
    b = verdicts["neuter_drop_water_plane"]
    assert b["cutting"]["water_edge_clips"] == 0, b
    assert b["cutting"]["batter_samples"] > ship_x["batter_samples"], b
    assert b["shoal"] == ship_s, b
    # NEUTER C: dropping `resulting_height` lays the flank, DRAINS THE BAY and
    # hands the hazard to `water_sign`, whose named remedy -- the route --
    # cannot be applied to a batter toe.  Arms 1 and 2 do not move, which is
    # why the trade is exactly this and nothing else.
    c = verdicts["neuter_drop_resulting_height"]
    assert c["causeway"]["water_fill_clips"] == 0, c
    assert c["causeway"]["drained_samples"] > 0, c
    assert c["causeway"]["water_sign"] == "REFUSED", c
    assert c["causeway"]["batter_samples"] > ship_c["batter_samples"], c
    assert c["shoal"] == ship_s, c
    assert c["cutting"] == ship_x, c
    if verbose:
        print(f"  a toe that stays under the plane: "
              f"{ship_s['batter_samples']} batter sample(s), "
              f"{ship_s['wet_samples']} over submerged ground, "
              f"{ship_s['drained_samples']} drained, water_sign "
              f"{ship_s['water_sign']}, worst wall {ship_s['worst_wall_m']} m")
        print(f"    NEUTER drop earthwork_direction -> "
              f"{a['shoal']['water_edge_clips']} clip(s), "
              f"{a['shoal']['wet_samples']} sample(s) laid (the known-bad "
              f"state rule)")
        print(f"  bank cut to the waterline: {ship_x['water_edge_clips']} "
              f"water_edge clip(s), {ship_x['batter_samples']} batter sample(s)")
        print(f"    NEUTER drop water_plane -> "
              f"{b['cutting']['water_edge_clips']} clip(s), "
              f"{b['cutting']['batter_samples']} batter sample(s)")
        print(f"  flank that lifts the bed OUT of the water: "
              f"{ship_c['water_fill_clips']} water_fill clip(s), water_sign "
              f"{ship_c['water_sign']} over {ship_c['drained_samples']} "
              f"drained sample(s), and "
              f"{ship_c['water_fill_walls_on_submerged_ground']} of "
              f"{ship_c['water_fill_walls']} wall(s) left standing on "
              f"seed-submerged ground")
        print(f"    NEUTER drop resulting_height -> "
              f"{c['causeway']['water_fill_clips']} clip(s), "
              f"{c['causeway']['drained_samples']} drained and water_sign "
              f"{c['causeway']['water_sign']} -- the shipped permit")
        print("ribbon batter water-gate selftest OK")
    return verdicts


def selftest_batter_advance(verbose: bool = True) -> dict:
    """THE EARTH BUDGET, KEYED ON THE RUN AND ON THE CLAMP.

    The three numbered rows are MEASURED, from T1-portalhub-temple's own
    `steeper_than_batter` clips: every one fired at the ray's FIRST sample,
    0.10-1.16 m outboard of the geometric edge, exceeding a budget taken AT
    that edge by 0.002-0.374 m -- the ground's own fall over the run, charged
    to the batter.
    """
    assert "run_spent" in BATTER_ADVANCE_DEPENDS_ON
    assert "apply_clamp" in BATTER_ADVANCE_DEPENDS_ON

    def drop_run(delta, full_edge, run):
        if abs(delta) > tcdata.CLAMP_M:
            return "over_clamp"
        if abs(delta) > abs(full_edge) + EDGE_EPS_M:
            return "steeper_than_batter"
        return None

    def drop_clamp(delta, full_edge, run):
        if abs(delta) > abs(full_edge) + BATTER_GRADE * run + EDGE_EPS_M:
            return "steeper_than_batter"
        return None

    # MEASURED rows: (delta, full_edge, run) from T1's own clip list.
    lattice = [(-2.272, -2.242, 0.10), (-2.418, -2.417, 0.18),
               (-3.934, -3.729, 1.16)]
    # A GENUINELY STEEPER HILLSIDE: the ground runs away at more than the
    # batter's own grade over the run, so no key can save it.
    hill = (-6.0, -2.0, 1.0)
    # AND A RUN LONG ENOUGH THAT THE RUN-CARRIED BUDGET WOULD PASS AN
    # OVER-CLAMP SAMPLE: 20 m of run buys 15.6 m of budget, and the apply
    # clamp is 8.0 m.
    clamped = (9.0, 1.0, 20.0)
    v = {
        "shipping_lattice": [batter_advance(*r) for r in lattice],
        "shipping_hill": batter_advance(*hill),
        "shipping_clamped": batter_advance(*clamped),
        "neuter_drop_run_lattice": [drop_run(*r) for r in lattice],
        "neuter_drop_run_hill": drop_run(*hill),
        "neuter_drop_clamp_clamped": drop_clamp(*clamped),
        "neuter_drop_clamp_lattice": [drop_clamp(*r) for r in lattice],
    }
    # SURVIVOR: the three measured lattice rows are WRITTEN by the shipping
    # rule, and every one of them is REFUSED once the run is dropped.
    assert v["shipping_lattice"] == [None, None, None], v
    assert v["neuter_drop_run_lattice"] == ["steeper_than_batter"] * 3, v
    # ...and the genuinely steeper hillside is refused BY BOTH, so the repair
    # did not buy the lattice rows by making the rule toothless.
    assert v["shipping_hill"] == "steeper_than_batter", v
    assert v["neuter_drop_run_hill"] == "steeper_than_batter", v
    # THE OTHER DIRECTION: the clamp is its own rule now, and dropping it lets
    # a 9.0 m move through a budget the run made big enough to hide it.
    assert v["shipping_clamped"] == "over_clamp", v
    assert v["neuter_drop_clamp_clamped"] is None, v
    assert v["neuter_drop_clamp_lattice"] == [None, None, None], v
    if verbose:
        print(f"  T1's three measured first-sample rows: shipping "
              f"{v['shipping_lattice']}, run dropped "
              f"{v['neuter_drop_run_lattice']}")
        print(f"  a hillside genuinely steeper than the batter: "
              f"{v['shipping_hill']} either way")
        print(f"  a 9.0 m move at 20 m of run: shipping "
              f"{v['shipping_clamped']}, clamp dropped "
              f"{v['neuter_drop_clamp_clamped']}")
        print("ribbon batter-advance selftest OK")
    return v


def selftest_batter_reach_exit(verbose: bool = True) -> dict:
    """THE CAP'S TWO POPULATIONS, AND WHY ONE NAME OVER BOTH IS UNACTIONABLE.

    MEASURED, Fresh42, `--validate`, 2026-09-24, on the two segments that
    hold the whole not-closing population, by sweeping the cap itself:

      cap     T9 caused / worst m / reach clips / not-closing
              T8 caused / worst m / reach clips / not-closing
       0.0    T9 4742 / 7.0959 / -   / -        T8 5356 / 5.1031 / -   / -
       3.0    T9 1668 / 7.6041 / 994 / 56       T8 1263 / 4.2172 / 924 / 28
       6.0    T9  593 / 7.4515 / 328 / 57       T8  174 / 4.9087 / 134 / 37
      10.5    T9  203 / 5.4927 / 135 / 57       T8   97 / 5.4615 /  52 / 39
      16.0    T9   61 / 5.7291 /  28 / 29       T8   66 / 5.7044 /  20 / 24
      24.0    T9   33 / 1.4935 /   7 /  0       T8   46 / 2.2137 /   2 /  0

    TWO THINGS THAT ARE ONLY VISIBLE ONCE THE POPULATIONS ARE SPLIT.  First,
    THE WORST STEP IS NOT MONOTONE IN THE CAP -- it rises from 10.5 to 16.0
    on both segments and then collapses by 24.0 -- which is the same property
    `approach_reach_m` records about the pad approach, and it is why a cap
    picked by eye lands on the wrong side of the hump.  Second, the
    not-closing count is a property OF THE CAP: all 99 of it went to zero on
    the way from 10.5 to 24.0 m.  It was read once as "the route must move
    these" and that was wrong.
    """
    assert "residual_at_the_cap" in REACH_EXIT_DEPENDS_ON
    assert "wall_at_the_carriageway_edge" in REACH_EXIT_DEPENDS_ON

    # NEUTER A -- drop `residual_at_the_cap`: the cap is the only fact, so
    # every ray that reaches it is called closing.  This is the state this
    # file shipped in, kept executable.
    def drop_residual(full_edge, residual, run):
        return "reach", None

    # NEUTER B, THE OTHER DIRECTION -- drop `wall_at_the_carriageway_edge`
    # and ask only whether anything is left at the cap.  Everything with a
    # residual then reads as a runaway, including the rays a longer reach
    # would finish.
    def drop_full_edge(full_edge, residual, run):
        return ("reach_not_closing", None) if abs(residual) > EDGE_EPS_M \
            else ("reach", run)

    # A RAY STILL CLOSING: 4.00 m of wall at the edge, 2.40 m left after
    # 10.5 m of run -- 1.60 m closed, 0.1524 m/m, so 15.75 m more run
    # finishes it and the total from the carriageway edge is 26.25 m.
    closing = (-4.0, -2.40, 10.5)
    # ONE THAT HAS CLOSED NOTHING: the same 4.00 m wall and MORE left than it
    # started with, by the cap it was asked at.
    runaway = (-4.0, -4.35, 10.5)
    # AND THE KNIFE EDGE: exactly no progress.  A rate of zero derives no
    # finite run, so it goes with the second population.
    flat = (-4.0, -4.0, 10.5)
    v = {"shipping_closing": batter_reach_exit(*closing),
         "shipping_runaway": batter_reach_exit(*runaway),
         "shipping_flat": batter_reach_exit(*flat),
         "neuter_drop_residual_runaway": drop_residual(*runaway),
         "neuter_drop_residual_closing": drop_residual(*closing),
         "neuter_drop_full_edge_closing": drop_full_edge(*closing),
         "neuter_drop_full_edge_runaway": drop_full_edge(*runaway)}
    # SURVIVOR: the shipping rule separates them and the run it derives for
    # the closing ray is its own measured rate, not a constant.
    assert v["shipping_closing"][0] == "reach", v
    assert abs(v["shipping_closing"][1] - 26.25) < 0.005, v
    assert v["shipping_runaway"] == ("reach_not_closing", None), v
    assert v["shipping_flat"] == ("reach_not_closing", None), v
    # NEUTER A calls the runaway a closing ray and offers NO run at all, so a
    # reader has the same one word for both and nothing to act on.
    assert v["neuter_drop_residual_runaway"] == ("reach", None), v
    assert v["neuter_drop_residual_closing"][1] is None, v
    # NEUTER B goes the other way and condemns the ray a longer reach closes,
    # which is how a real reach defect gets handed to the route.
    assert v["neuter_drop_full_edge_closing"][0] == "reach_not_closing", v
    assert v["neuter_drop_full_edge_runaway"][0] == "reach_not_closing", v
    # AND THE CLAMP CANNOT BIND ON THE CLOSING ONE: its residual is inside
    # its own edge wall, which is inside the apply clamp by construction.
    assert abs(closing[1]) < abs(closing[0]) <= tcdata.CLAMP_M, v
    if verbose:
        print(f"  4.00 m of edge wall, 2.40 m left after 10.5 m of run: "
              f"{v['shipping_closing'][0]}, needs "
              f"{round(v['shipping_closing'][1], 2)} m of run")
        print(f"  the same wall, 4.35 m left after the same run: "
              f"{v['shipping_runaway'][0]} -- and the neuter that drops the "
              f"residual calls it {v['neuter_drop_residual_runaway'][0]}")
        print(f"  dropping the edge wall instead condemns the closing ray: "
              f"{v['neuter_drop_full_edge_closing'][0]}")
        print("ribbon batter reach-exit selftest OK")
    return v


def selftest_batter_placed_gate(verbose: bool = True) -> dict:
    """WHOSE GROUND MAY NOT MOVE, over a CONSTRUCTED population.

    The proportions are Fresh42's, restated as a fixture so no neuter can
    reach the denominator: 296 property or POI objects against 11,518
    CLEARABLE ones, 40 to 1.
    """
    assert "object_prefab" in PLACED_GATE_DEPENDS_ON
    population = (["Beech_small2"] * 1715 + ["Pickable_Stone"] * 1201
                  + ["Rock_4"] * 1409 + ["Bush01"] * 1069
                  + ["Beech1"] * 1232 + ["Beech_small1"] * 1712
                  + ["shrub_2"] * 531 + ["Pickable_Dandelion"] * 410
                  + ["FirTree_small"] * 281 + ["Birch1"] * 149
                  + ["Pinetree_01"] * 129 + ["Rock_3"] * 460
                  + ["Pickable_Branch"] * 429 + ["RaspberryBush"] * 179
                  + ["stubbe"] * 113 + ["woodwall"] * 65
                  + ["wood_floor"] * 34 + ["LocationProxy"] * 22
                  + ["RockDolmen_1"] * 3 + ["TreasureChest_meadows"] * 5
                  + ["rock4_copper"] * 1 + ["Spawner_Greydwarf"] * 7
                  + ["BH_SomeModBuildingNobodyHasEverSeen"] * 2)
    freezes = sum(1 for p in population if batter_freezes_on(p))
    # THE UNKNOWN NAME IS THE OTHER DIRECTION.  `clear.blocks` is default-deny,
    # so a mod POI's ordinary building prefab freezes the ground WITHOUT being
    # listed anywhere -- and a rule that keyed on a positive list of structures
    # instead would let it through.
    unknown = batter_freezes_on("BH_SomeModBuildingNobodyHasEverSeen")
    v = {"population": len(population), "freezes": freezes,
         "vegetation": len(population) - freezes,
         "unknown_prefab_freezes": unknown,
         "unnamed_object_freezes": batter_freezes_on(None),
         "neuter_drop_prefab_freezes": len(population),
         "neuter_drop_default_deny_freezes":
             sum(1 for p in population
                 if p in ("woodwall", "wood_floor", "LocationProxy",
                          "RockDolmen_1", "TreasureChest_meadows",
                          "rock4_copper", "Spawner_Greydwarf"))}
    # SURVIVOR: a beech does not freeze the ground and a wall does.
    assert not batter_freezes_on("Beech_small2")
    assert batter_freezes_on("woodwall")
    assert freezes == 139, v
    assert v["vegetation"] == 11019, v
    # NEUTER A, drop `object_prefab`: the position-only rule freezes all of
    # them, which is the 40-to-1 superset and the 223 rim steps it cost.
    assert v["neuter_drop_prefab_freezes"] > 40 * freezes, v
    # NEUTER B, drop the default-deny: the unknown mod building stops freezing,
    # which is the direction a positive list of structures fails in.
    assert unknown is True, v
    assert v["neuter_drop_default_deny_freezes"] == freezes - 2, v
    # AND AN OBJECT WITH NO NAME FREEZES.  UNREAD IS NOT CLEAR.
    assert v["unnamed_object_freezes"] is True, v
    if verbose:
        print(f"  constructed population {v['population']}: {v['freezes']} "
              f"freeze the ground, {v['vegetation']} do not")
        print(f"    NEUTER drop object_prefab -> "
              f"{v['neuter_drop_prefab_freezes']} freeze "
              f"({v['neuter_drop_prefab_freezes'] // max(freezes, 1)}x)")
        print(f"    NEUTER drop default-deny -> "
              f"{v['neuter_drop_default_deny_freezes']} freeze, and the "
              f"unknown mod building is one of the two that got out")
        print("ribbon batter placed-gate selftest OK")
    return v


# WHAT A BATTER RAY ACTUALLY DEPENDS ON.  It used to depend on the station
# alone, which at a polyline vertex is a whole wedge of bearings.
RAY_KEY_DEPENDS_ON = ("side", "station_arc", "bearing_from_the_foot")


# THE WEDGE OUTSIDE A RIGHT-ANGLE CORNER, and every sample in it projects to
# the SAME vertex, so the station bin holds every bearing in it at once.
_FAN_NODES = [(-24.0, 12.0), (0.0, 12.0), (0.0, -12.0)]
_FAN_ROAD_Y = 36.0


def _fan_ground(wx: float, wz: float) -> float:
    """A uniform 2 m bank, so every bearing in the wedge owes the same cut.

    IT USED TO CARRY A TRENCH at road level along one bearing, so that
    bearing's plane met ground at its first batter sample and a walk treating
    the whole wedge as one ray exited there.  `met_ground` NO LONGER ENDS A
    RAY -- it skips one sample -- so that terminator stopped discriminating
    and the arm silently went to zero.  The key defect is untouched by that
    repair for every exit that DOES end a ray, so the fixture now uses one:
    a live placed object on the narrow bearing (`_FAN_PLACED`), which is the
    same shape and a real refusal.
    """
    return _FAN_ROAD_Y + 2.0


# ONE LIVE OBJECT, on the 45 deg bearing of the wedge at lat 4.95 -- outboard
# of the 4.0 m carriageway edge and inboard of where this bank's plane meets
# ground at lat 6.56.  Under a station-only key its `placed` clip ends EVERY
# bearing in the bin; under a bearing-carrying key it ends its own.
_FAN_PLACED = [(3.5, 15.5)]


def _in_fan(wx: float, wz: float) -> bool:
    return wx > 0.0 and wz > 12.0


def selftest_batter_ray_key(verbose: bool = True) -> dict:
    """THE VERTEX FAN, AND THE KEY THAT USED TO SWALLOW IT.

    A polyline CORNER collapses the nearest-point projection onto one vertex,
    so `(side, int(round(arc)))` holds an entire wedge of different BEARINGS.
    `members.sort()` then walks that wedge by radius, and the first exit taken
    on ANY bearing stops EVERY bearing.  MEASURED on T1-portalhub-temple: ray
    (-1, 142) holds (-117,-311) and (-119,-316), 5.4 m apart on the ground,
    and a `met_ground` on the second threw away the 1.64 m of cut the third
    still owed -- the 2.4128 m rim step the gate refused the write for.

    THE QUANTITY IS THE GROUND LAID IN THE WEDGE, not the segment's sample
    total: the two arms of the corner are unaffected by this key and they
    dominate any whole-segment count.  Counting them in is how a 601-to-3
    repair reads as a 5% difference.

    AND THE SILENCE IS THE POINT.  The station-only run leaves the bank
    standing and records NO WALL AT ALL: it exits on `met_ground`, which is
    the batter's success exit.  A clip that leaves no trace is
    indistinguishable from a batter that worked, so the case is pinned on the
    ground laid rather than on the wall reported.
    """
    def run(carries_bearing: bool) -> dict:
        st = _rasterise(_FAN_NODES, _FAN_ROAD_Y, _fan_ground,
                        placed=_FAN_PLACED,
                        ray_key_carries_bearing=carries_bearing,
                        # THE KEY IS THE DEPENDENCY UNDER TEST: the rim
                        # closure rebuilds 20 of the 22 wedge samples the
                        # blind key throws away, and would hide it.
                        rim_closure_on=False)
        return {"batter_samples": st["samples_batter"],
                "wedge_samples": sum(1 for x, z in st["written"]
                                     if _in_fan(x, z)),
                "rays": st["rays"],
                "walls_reported": len(st["clip_at"]),
                "worst_wall_m": st["walls_left_max_m"]}

    v = {"bearing_keyed": run(True), "station_only": run(False)}
    keyed, blind = v["bearing_keyed"], v["station_only"]
    v["wedge_thrown_away"] = keyed["wedge_samples"] - blind["wedge_samples"]
    # THE FIXTURE MUST DISCRIMINATE: a wedge nobody grades either way proves
    # nothing about a key.
    if keyed["wedge_samples"] == 0:
        raise AssertionError(f"the fixture graded no wedge at all: {v}")
    # SURVIVOR, AS A VERDICT: with the bearing in the key the wedge is graded
    # and the bank is laid down; without it most of that ground is never
    # written, and ONE clip on ONE bearing is all that is said about it.
    assert v["wedge_thrown_away"] > 0, v
    assert blind["wedge_samples"] * 2 < keyed["wedge_samples"], v
    assert blind["rays"] < keyed["rays"], v
    # THE OTHER DIRECTION: WHERE THERE IS NO BEND THE BEARING MUST COST
    # NOTHING.  On a straight run the foot is the perpendicular projection, so
    # every sample in a station bin has the same bearing and the partition is
    # unchanged -- and the fixture puts a HOLLOW at lat 8-9 so rays DO exit
    # early and a partition that had shifted would show.  If the angular bin
    # were ever made finer than the lattice this arm goes red, which is the
    # opposite failure from the one above.  MEASURED, not assumed.
    #
    # `rays` is NOT compared and the difference is not a defect: the two ENDS
    # of any polyline are fans too -- every sample beyond the last node
    # projects onto that node -- so the bearing correctly splits them.  The
    # invariant that matters is the GROUND LAID ON THE STRAIGHT PART, and the
    # comparison says so rather than quietly covering the end fans as well:
    # a sample outboard of a terminal node is IN a fan by the same argument
    # that makes case one fire, so asserting equality over it would be
    # asserting the opposite of what this function measures.
    def straight(carries: bool) -> dict:
        def ground(wx: float, wz: float) -> float:
            return (_FAN_ROAD_Y if 8.0 <= abs(wz) <= 9.0
                    else _FAN_ROAD_Y + 2.0)
        st = _rasterise([(-24.0, 0.0), (24.0, 0.0)], _FAN_ROAD_Y, ground,
                        ray_key_carries_bearing=carries)
        return {"batter_samples": st["samples_batter"],
                "written": sorted((x, z) for x, z in st["written"]
                                  if abs(x) < 24.0),
                "worst_wall_m": st["walls_left_max_m"],
                "rays": st["rays"]}

    v["straight_bearing_keyed"] = straight(True)
    v["straight_station_only"] = straight(False)
    sk, ss = v["straight_bearing_keyed"], v["straight_station_only"]
    assert sk["written"] == ss["written"], (
        f"the bearing changed the ground laid BETWEEN THE NODES of a STRAIGHT "
        f"run: {len(sk['written'])} vs {len(ss['written'])} sample(s)")
    assert len(sk["written"]) > 0, v
    assert sk["worst_wall_m"] == ss["worst_wall_m"], v
    assert sk["batter_samples"] > 0, v
    for arm in (sk, ss):
        arm.pop("written")
    if verbose:
        print(f"  vertex fan, bearing in the key: {keyed['rays']} ray(s), "
              f"{keyed['wedge_samples']} sample(s) of wedge graded")
        print(f"    NEUTER drop bearing -> {blind['rays']} ray(s), "
              f"{blind['wedge_samples']} sample(s) of wedge graded: "
              f"{v['wedge_thrown_away']} thrown away by the key, and "
              f"{blind['walls_reported']} wall(s) reported about it")
        print(f"  a STRAIGHT run has no fan and the key costs nothing: "
              f"{v['straight_bearing_keyed']['rays']} ray(s) / "
              f"{v['straight_bearing_keyed']['batter_samples']} sample(s) "
              f"either way")
        print("ribbon batter ray-key selftest OK")
    return v


# THE TWO BANKS OF A CROSSING, AND THE GROUND THEY STAND ON IS DIFFERENT
# EARTHWORK.  Same geometry as `_DECK_NODES` -- marking node 3 bridges runs 2
# and 3, so the banks are (-12, 0) and (24, 0) -- but the ground is stepped
# across the span so the NEAR bank is a 2 m CUT and the FAR bank a 5.5 m FILL.
# That is T1-portalhub-temple's crossing x01 in miniature: near bank ground
# 35.5, far bank 29.6, one deck height over both.
#
# Both banks' radials run perpendicular to the SAME heading (+x), so they
# share a side and a bearing bin; under a station coordinate that skips the
# span they also share a station, and the two radials become ONE RAY.
_SEAM_NODES = [(-36.0, 0.0), (-24.0, 0.0), (-12.0, 0.0),
               (12.0, 0.0), (24.0, 0.0), (36.0, 0.0)]
_SEAM_BRIDGE = [False, False, False, True, False, False]
_SEAM_ROAD_Y = 36.0
_SEAM_NEAR_BANK_X, _SEAM_FAR_BANK_X = -12.0, 24.0


def _seam_ground(wx: float, wz: float) -> float:
    """A 2 m bank to CUT before the span, a 5.5 m hollow to FILL after it."""
    del wz
    return 38.0 if wx < 0.0 else 30.5


def _terrain_only_arcs(nodes, br) -> list[float]:
    """THE NEUTER: the accumulator that advanced over TERRAIN runs only.

    Verbatim the rule this file shipped, kept as a function so the selftest
    replaces `node_arcs` MODULE-WIDE and `lateral_and_y_grid` runs the
    neutered rule rather than a copy of it.
    """
    arc = [0.0] * len(nodes)
    acc = 0.0
    for k in range(len(nodes) - 1):
        arc[k] = acc
        if not (br[k] or br[k + 1]):
            acc += math.hypot(float(nodes[k + 1][0]) - float(nodes[k][0]),
                              float(nodes[k + 1][1]) - float(nodes[k][1]))
    if len(nodes):
        arc[-1] = acc
    return arc


def selftest_station_arc_injective(verbose: bool = True) -> dict:
    """TWO BANKS, ONE STATION, ONE RAY -- AND NOT ONE WORD ABOUT IT.

    A station bin is only a ray if the coordinate it bins is INJECTIVE along
    the polyline.  Skipping a bridged run's length breaks that at every
    crossing: the far bank's arc resumes at the near bank's, so the two
    radials are sorted into one member list by radius, walked with ONE
    `full_edge` and ONE earthwork sign, and the bank that disagrees about the
    sign is skipped on `batter_met_ground` -- the batter's SUCCESS exit, which
    records nothing at all.

    THE QUANTITY IS THE GROUND LAID IN THE SEAM RADIALS, not the segment's
    sample total: everything else on both legs is untouched by this key and
    would drown the difference.

    AND THE SILENCE IS PART OF THE VERDICT.  The neutered run does not clip,
    does not refuse and does not warn; it reports a wall census over the lost
    ground of `{}` while leaving a cliff in it.  That is why this is asserted
    on the ground laid and on the walls NOT reported, and not on a count of
    refusals.
    """
    assert "including_the_runs_this_ribbon_does_not_surface" in \
        STATION_ARC_DEPENDS_ON
    global node_arcs
    shipping = node_arcs
    edge = 6.0 / 2.0 + SHOULDER_M

    def seam(written) -> set:
        """Written batter samples standing on either bank's seam radial."""
        return {(round(x), round(z)) for x, z in written
                if abs(z) > edge + EDGE_EPS_M
                and (abs(x - _SEAM_NEAR_BANK_X) < 0.5
                     or abs(x - _SEAM_FAR_BANK_X) < 0.5)}

    def run(nodes, bridge) -> dict:
        # THE KEY IS UNDER TEST, NOT THE BACKSTOP.  With the bank's square end
        # claimed (RIM_BANK_END_DEPENDS_ON) the rim closure re-lays the ground
        # the neutered key throws away -- 18 seam cells either way -- which
        # proves the closure and hides the coordinate.
        st = _rasterise(nodes, _SEAM_ROAD_Y, _seam_ground, is_bridge=bridge,
                        rim_closure_on=False)
        return {"written": {(round(x), round(z)) for x, z in st["written"]},
                "seam": seam(st["written"]),
                "rays": st["rays"],
                "batter_samples": st["samples_batter"],
                "walls_by_cause": dict(st["walls_left_by_cause"]),
                "clips": len(st["clip_at"])}

    try:
        carried = run(_SEAM_NODES, _SEAM_BRIDGE)
        flat_carried = run(_SEAM_NODES, [False] * len(_SEAM_NODES))
        node_arcs = _terrain_only_arcs
        dropped = run(_SEAM_NODES, _SEAM_BRIDGE)
        flat_dropped = run(_SEAM_NODES, [False] * len(_SEAM_NODES))
    finally:
        node_arcs = shipping

    v = {"span_carried": {k: x for k, x in carried.items() if k != "written"},
         "span_dropped": {k: x for k, x in dropped.items() if k != "written"}}
    v["seam_cells_carried"] = len(carried["seam"])
    v["seam_cells_dropped"] = len(dropped["seam"])
    v["seam_thrown_away"] = sorted(carried["seam"] - dropped["seam"])
    # THE FIXTURE MUST DISCRIMINATE: a seam nobody grades either way proves
    # nothing about a coordinate.
    if not carried["seam"]:
        raise AssertionError(f"the fixture graded no seam radial at all: {v}")

    # ---- DIRECTION ONE: the dependency is LOST and ground goes with it ----
    assert v["seam_thrown_away"], v
    assert len(dropped["seam"]) < len(carried["seam"]), v
    assert dropped["rays"] < carried["rays"], v
    # AND NOTHING IS SAID ABOUT IT.  Every cell the neuter lost was lost on
    # `met_ground`, so no cause is recorded and no wall is reported: the
    # census the operator would read is the census of a batter that worked.
    assert not dropped["walls_by_cause"], v
    assert dropped["clips"] == 0, v

    # ---- DIRECTION TWO: WITHOUT A SPAN THE COORDINATE MUST COST NOTHING ----
    # The two rules differ ONLY in what a bridged run contributes, so over the
    # same polyline with no bridged run they must lay the identical ground and
    # partition it into the identical rays.  This is the arm that goes red if
    # the station coordinate is ever made finer than the lattice, or keyed on
    # a quantity that wobbles within one radial -- the opposite failure from
    # the one above, and the reason a one-direction neuter proves nothing.
    assert flat_carried["written"] == flat_dropped["written"], (
        f"no bridged run, yet the station coordinate moved the ground: "
        f"{len(flat_carried['written'])} vs {len(flat_dropped['written'])}")
    assert flat_carried["rays"] == flat_dropped["rays"], v
    assert flat_carried["batter_samples"] > 0, v
    # AND THE NEAR LEG OF THE BRIDGED CASE IS BEFORE THE SPAN, so its arcs are
    # identical under both rules and its ground must be too.  A difference
    # here would mean the change leaked past the seam it was made for.
    near = lambda w: {c for c in w if c[0] < _SEAM_NEAR_BANK_X}  # noqa: E731
    assert near(carried["written"]) == near(dropped["written"]), v
    assert near(carried["written"]), v
    v["near_leg_cells"] = len(near(carried["written"]))

    if verbose:
        print(f"  crossing seam, span carried in the station: "
              f"{carried['rays']} ray(s), {v['seam_cells_carried']} seam "
              f"cell(s) graded")
        print(f"    NEUTER drop the span's length -> {dropped['rays']} ray(s), "
              f"{v['seam_cells_dropped']} seam cell(s) graded: "
              f"{len(v['seam_thrown_away'])} thrown away by the coordinate, "
              f"walls reported about them {dropped['walls_by_cause'] or '{}'}")
        print(f"  no span, so the coordinate costs nothing: "
              f"{flat_carried['rays']} ray(s) / "
              f"{len(flat_carried['written'])} sample(s) either way; near leg "
              f"{v['near_leg_cells']} cell(s) unchanged")
        print("ribbon station-arc injectivity selftest OK")
    return v


# A ROAD THAT CROSSES WATER ON A DECK: two terrain runs, a 24 m bridged span
# between them, and a bed the seed drew 4 m UNDER the 30.0 m plane.  The two
# bank nodes are (-12, 0) and (12, 0).  The road sits 4 m ABOVE the plane, so
# ANY sample the ribbon lays over the span is fill that lifts the bed out of
# the water -- which is what makes the population countable without a water
# gate in the loop.
_DECK_NODES = [(-36.0, 0.0), (-24.0, 0.0), (-12.0, 0.0),
               (12.0, 0.0), (24.0, 0.0), (36.0, 0.0)]
# Run j is bridged iff `br[j] or br[j+1]`, so marking NODE 3 alone bridges run
# 2 (nodes 2-3) AND run 3 (nodes 3-4): the deck spans x = -12 to x = +24 and
# the two bank nodes are (-12, 0) and (24, 0).
_DECK_BRIDGE = [False, False, False, True, False, False]
_DECK_ROAD_Y = 34.0
_DECK_SPAN = (-12.0, 24.0)


def _deck_ground(wx: float, wz: float) -> float:
    """Dry land at 34 m outside the span, a bed at 26 m under it."""
    return 26.0 if _DECK_SPAN[0] < wx < _DECK_SPAN[1] else 34.0


# A CORNER WITH NO WATER AND NO DECK, which is the population the clamp was
# ADDED for: the wedge on the outside of a turn that `tt` outside [0, 1]
# leaves claimed by nobody.
_CORNER_NODES = [(-24.0, 12.0), (0.0, 12.0), (0.0, -12.0)]


def selftest_ribbon_claim_ends(verbose: bool = True) -> dict:
    """WHOSE GROUND IS PAST THE END OF A TERRAIN RUN, over constructed ground.

    The clamp is right at a vertex and at a terminus and WRONG at a bank, and
    a rule that cannot tell them apart is one key over two populations.  Both
    neuters are the SHIPPING `clamp_ends` replaced module-wide, so `stamp`,
    `lateral_and_y_grid` and `lateral_and_y` all run the neutered rule rather
    than a copy of it.
    """
    assert "what_the_polyline_does_past_that_run's_end" in \
        RIBBON_CLAIM_DEPENDS_ON
    global clamp_ends
    shipping = clamp_ends

    def split(st):
        """The written set, divided by whether it is over the deck's span."""
        on, off = set(), set()
        for x, z in st["written"]:
            (on if _DECK_SPAN[0] < x < _DECK_SPAN[1] else off).add((x, z))
        return on, off

    def arms(rule) -> dict:
        global clamp_ends
        clamp_ends = rule
        try:
            deck = _rasterise(_DECK_NODES, _DECK_ROAD_Y, _deck_ground,
                              is_bridge=_DECK_BRIDGE)
            corner = _rasterise(_CORNER_NODES, _FAN_ROAD_Y, _fan_ground)
        finally:
            clamp_ends = shipping
        on, off = split(deck)
        return {"over_the_span": len(on), "off_the_span": off,
                "deck_total": len(deck["written"]),
                "corner_total": len(corner["written"]),
                "corner_paved": corner["samples_paved"]}

    v = {"shipping": arms(shipping),
         # NEUTER A -- drop the dependency on WHAT THE POLYLINE DOES past the
         # foot and clamp unconditionally.  This is the state this file
         # shipped in, kept EXECUTABLE rather than described.
         "neuter_always_clamp": arms(lambda nodes, br, k: (True, True)),
         # NEUTER B, THE OTHER DIRECTION -- drop the clamp entirely, which is
         # the state BEFORE the vertex-wedge fix.  It also removes the deck
         # fan, so a one-direction neuter would read it as an improvement.
         "neuter_never_clamp": arms(lambda nodes, br, k: (False, False))}
    assert clamp_ends is shipping
    # SURVIVOR 1: the shipping rule lays NOTHING over the span, and the
    # unconditional clamp lays a fan there.
    assert v["shipping"]["over_the_span"] == 0, v
    assert v["neuter_always_clamp"]["over_the_span"] > 0, v
    # ...and it is not bought by grading less everywhere: OFF the span the
    # two rules write exactly the same lattice cells, so the whole difference
    # is the fan.
    assert (v["neuter_always_clamp"]["off_the_span"]
            == v["shipping"]["off_the_span"]), (
        len(v["neuter_always_clamp"]["off_the_span"]),
        len(v["shipping"]["off_the_span"]))
    # SURVIVOR 2, THE OTHER DIRECTION: dropping the clamp altogether also
    # clears the span -- so the span count ALONE cannot tell the two apart --
    # and it loses the corner wedge the clamp exists for.
    assert v["neuter_never_clamp"]["over_the_span"] == 0, v
    assert (v["neuter_never_clamp"]["corner_paved"]
            < v["shipping"]["corner_paved"]), v
    assert (v["neuter_always_clamp"]["corner_paved"]
            == v["shipping"]["corner_paved"]), v
    if verbose:
        print(f"  a 24 m deck over a bed 8 m below the road: shipping lays "
              f"{v['shipping']['over_the_span']} sample(s) over the span, "
              f"the unconditional clamp lays "
              f"{v['neuter_always_clamp']['over_the_span']} "
              f"(off the span both lay "
              f"{len(v['shipping']['off_the_span'])}, identical cells)")
        print(f"  the corner the clamp exists for: shipping paves "
              f"{v['shipping']['corner_paved']} carriageway sample(s), "
              f"no clamp at all paves "
              f"{v['neuter_never_clamp']['corner_paved']}")
        print("ribbon claim-ends selftest OK")
    return v


# TWO HAIRPINS, AND THE DIFFERENCE BETWEEN THEM IS THE WHOLE FINDING.
#
# `_HAIRPIN` comes back on itself with 12 m between the two legs and the
# return leg 2.3 m LOWER.  The two legs' bands OVERLAP -- 12 m of lateral run
# buys 9.4 m of height at tan(38 deg) and the disagreement is 2.3 -- so every
# shared cell HAS a height both legs can live with, and the joint band finds
# it.  This is the RECONCILABLE population.
#
# `_TIGHT` is the same road with the legs 9.4 m apart and 1.7 m of fall, which
# is T6-stathub-northcape's (154,505)/(155,506) pair to scale: two
# CARRIAGEWAYS whose bands have zero width where they are paved, so 26 cells
# have NO admissible height at all.  This is the population that can only be
# REFUSED BY NAME, and it exists in the fixture set on purpose: a repair with
# only the reconcilable arm in it would be a repair that cannot say no.
_HAIRPIN_NODES = [(-30.0, 6.0), (18.0, 6.0), (18.0, -6.0), (-30.0, -6.0)]
_HAIRPIN_Y = [60.5, 60.5, 59.35, 58.2]
_TIGHT_NODES = [(-30.0, 4.5), (18.0, 4.5), (18.0, -4.5), (-30.0, -4.5)]
_TIGHT_Y = [60.5, 60.5, 59.65, 58.8]


def _hairpin_ground(wx: float, wz: float) -> float:
    """A plane falling away from the outbound leg, so both legs are in fill."""
    return 56.0


def selftest_ribbon_beside_itself(verbose: bool = True) -> dict:
    """THE RIBBON'S SECOND CLAIM ON ITS OWN GROUND, over two constructed
    hairpins -- one the fill floor RECONCILES and one it can only REFUSE.

    THE BATTER IS A ONE-DIMENSIONAL GUARANTEE AND THE RIM GATE IS A
    TWO-DIMENSIONAL ADJACENCY.  Every ray closes ALONG its own radial -- every
    sample is inside `BATTER_GRADE * run` of its own carriageway edge, which
    IS the walker's slide limit -- and NOTHING BOUNDS THE STEP ACROSS RAYS.
    Two lattice-adjacent cells belonging to feet far apart on the polyline
    carry the full profile difference and BOTH RAYS RECORD SUCCESS.  MEASURED
    on Fresh42, `--validate`, 2026-09-24, over the five segments the rim gate
    refuses: 60 of their 71 caused rim steps have their two samples claimed by
    feet 9 to 30 m APART ALONG THE POLYLINE, and on four of the five EVERY ray
    exits `met_ground` -- the SUCCESS exit.

    FOUR THINGS ARE PROVED, because closing any one of them alone leaves
    another silent:

      1. THE FILL FLOOR RECONCILES WHAT CAN BE RECONCILED.  See
         `BATTER_TARGET_DEPENDS_ON`.  MEASURED over the WRITTEN SURFACE and
         not over the wall census: 79 CAUSED over-limit lattice edges without
         it, 0 with it, and the census it leaves behind WITHOUT it is `{}`.
      2. WHAT CANNOT BE RECONCILED IS REFUSED BY NAME, WITH ITS NUMBER, and
         the refusal is a ROUTING fact with no ground term in it.
      3. THE SECOND CLAIMANT IS THE ONE THIS GROUND CANNOT ABSORB, not the
         NEAREST one.  See `SECOND_CLAIMANT_DEPENDS_ON`.
      4. A STATION WITH ROAD AND NO RAY GETS A RAY.  `nearest wins` hands the
         cells outboard of one branch's edge to the other branch, so a
         station can have a carriageway and no batter at all.
    """
    assert "every_other_run_within_reach" in BATTER_TARGET_DEPENDS_ON
    assert "the_lateral_run_to_each_of_them" in BATTER_TARGET_DEPENDS_ON
    assert "this_claim_own_ceiling" in BATTER_TARGET_DEPENDS_ON

    reach = 3.0 + SHOULDER_M + BATTER_MAX_M

    def build(nodes, prof):
        seg = {"id": "hairpin", "nodes": [list(n) for n in nodes],
               "profile_y": list(prof), "is_bridge": [False] * len(nodes),
               "width_m": 6.0, "length_m": 0.0}
        xs = [n[0] for n in nodes]
        zs = [n[1] for n in nodes]
        zx0, zz0 = tcdata.zone_of(min(xs) - reach, min(zs) - reach)
        zx1, zz1 = tcdata.zone_of(max(xs) + reach, max(zs) + reach)
        zones = [(zx, zz) for zx in range(zx0, zx1 + 1)
                 for zz in range(zz0, zz1 + 1)]
        patches = {f"z_{zx}_{zz}": _flat_patch(zx, zz, _hairpin_ground)
                   for zx, zz in zones}
        return seg, patches, zones

    wide = build(_HAIRPIN_NODES, _HAIRPIN_Y)
    tight = build(_TIGHT_NODES, _TIGHT_Y)
    straight = build([(-30.0, 0.0), (18.0, 0.0)], [60.5, 60.5])

    # THE QUANTITY IS THE SURFACE, NOT THE WALL CENSUS, and that is the whole
    # lesson of this defect: the steps it leaves are left by rays that exited
    # on `met_ground`, so a reader of the census cannot tell the two surfaces
    # apart.  Every edge is judged CAUSED -- `junction.rim_transfer`'s own
    # rule -- so a pair the terrain already had is terrain and not earthwork,
    # and the connectivity is EIGHT, because the judge walks diagonals.
    def cross_ray_steps(st):
        comps = st["_comps"]
        wrote = {(int(round(x)), int(round(z))) for x, z in st["written"]}
        H, G = {}, {}
        for c in wrote:
            for dx in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    n = (c[0] + dx, c[1] + dz)
                    if n not in H:
                        G[n] = _hairpin_ground(float(n[0]), float(n[1]))
                        H[n] = G[n] + delta_at(comps, float(n[0]),
                                               float(n[1]))
        seen, caused, worst = set(), 0, 0.0
        worst_at = None
        for c in wrote:
            for dx in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    if (dx, dz) == (0, 0):
                        continue
                    n = (c[0] + dx, c[1] + dz)
                    e = (c, n) if c < n else (n, c)
                    if e in seen:
                        continue
                    seen.add(e)
                    lim = BATTER_GRADE * math.hypot(dx, dz)
                    if abs(G[c] - G[n]) > lim + 1e-9:
                        continue
                    over = abs(H[c] - H[n]) - lim
                    if over > 1e-9:
                        caused += 1
                        if over > worst:
                            worst, worst_at = round(over, 4), [list(c),
                                                               list(n)]
        return caused, worst, worst_at

    global lateral_and_y_grid, reconcile_target
    real_grid = lateral_and_y_grid
    real_reconcile = reconcile_target

    # NEUTER -- DROP `every_other_run_within_reach` AT THE SOURCE.  The floor
    # is absent everywhere, so there is nothing to hold the surface up to.
    # This is the state this file shipped in, and it is the SECOND of two
    # ways to reach it: `joint_claim_band_on=False` reaches the same state
    # from the consumer end, and both are run, because a rule that only one
    # of them can turn off is a rule with two homes.
    def blind_grid(nodes, prof, br, X, Z, reach_m,
                   absorb_grade=BATTER_GRADE, edge_m=None):
        out = real_grid(nodes, prof, br, X, Z, reach_m, absorb_grade, edge_m)
        return out[:8] + (np.full(X.shape, -np.inf, dtype=np.float64),)

    # NEUTER -- DROP `the_lateral_run_to_each_of_them`.  The floor stops
    # decaying with distance, so every cell within reach of another run is
    # held at THAT RUN'S ROAD HEIGHT, and the envelope becomes a step
    # function of "is another run within 21 m" -- a cliff at the reach.
    def no_decay_grid(nodes, prof, br, X, Z, reach_m,
                      absorb_grade=BATTER_GRADE, edge_m=None):
        return real_grid(nodes, prof, br, X, Z, reach_m, absorb_grade,
                         reach_m)

    # NEUTER -- DROP `this_claim_own_ceiling`.  The floor is applied even
    # where it stands ABOVE what this claim's own carriageway edge can reach,
    # so a cell no earthwork can reconcile is written from the OTHER road's
    # floor and this road's edge is buried instead of being refused.
    def reconcile_never_refuse(target, generated, sign, y, lat, floor,
                               edge_m, grade):
        t, s_, _over = real_reconcile(target, generated, sign, y, lat, floor,
                                      edge_m, grade)
        if floor > -math.inf:
            shipped = (generated
                       if batter_met_ground(target - generated, sign)
                       else target)
            if shipped < floor:
                return floor, 1.0, 0.0
        return t, s_, 0.0

    # NEUTER -- DROP `separation` AND `slide_limit` FROM THE CLAIMANT KEY.
    # Distance costs nothing, so the claimant kept is whichever run disagrees
    # MOST wherever it stands: a road going uphill 20 m away reads as a wall.
    def loose_grid(nodes, prof, br, X, Z, reach_m,
                   absorb_grade=BATTER_GRADE, edge_m=None):
        # THE CLAIMANT KEY ONLY.  `absorb_grade` is shared with the fill
        # floor, so passing 0.0 straight through would neuter TWO rules at
        # once and the arm would be measuring their sum.  The floor comes
        # from the shipped call and only `lat2`/`y2` are replaced.
        out = real_grid(nodes, prof, br, X, Z, reach_m, absorb_grade, edge_m)
        loose = real_grid(nodes, prof, br, X, Z, reach_m, 0.0, edge_m)
        return out[:6] + (loose[6], loose[7], out[8])

    def run(fixture, *, grid=None, reconcile=None, **kw):
        # THE `global` IN THE ENCLOSING FUNCTION DOES NOT REACH IN HERE.
        # Without this line the assignments below bind LOCALS, every neuter
        # is a no-op, and each arm silently reports the shipping answer --
        # a neuter that cannot fail.  That happened once while this was being
        # written and the `neuter_no_claimant` arm returned the shipped
        # population, which is how it was caught.
        global lateral_and_y_grid, reconcile_target
        seg, patches, zones = fixture
        if grid is not None:
            lateral_and_y_grid = grid
        if reconcile is not None:
            reconcile_target = reconcile
        try:
            comps, st = stamp(seg, patches, list(zones), PadKeepOut([], []),
                              **kw)
        finally:
            lateral_and_y_grid = real_grid
            reconcile_target = real_reconcile
        st["_comps"] = comps
        st["_caused"], st["_worst"], st["_worst_at"] = cross_ray_steps(st)
        return st

    # THE RECONCILIATION IS OFF BY DEFAULT -- refused on the corpus by its
    # own numbers, see `stamp`'s `joint_claim_band_on` -- so the arm that
    # exercises it says so, and the SHIPPED arm is the one without it.  The
    # rule is still proved here, in both directions, because a rule that is
    # off and unproved is a rule nobody can switch back on.
    on_w = run(wide, joint_claim_band_on=True)
    off_w = run(wide)
    blind_w = run(wide, grid=blind_grid, joint_claim_band_on=True)
    nodecay_w = run(wide, grid=no_decay_grid, joint_claim_band_on=True)

    on_t = run(tight, joint_claim_band_on=True)
    off_t = run(tight)
    blind_t = run(tight, grid=blind_grid, joint_claim_band_on=True)
    nodecay_t = run(tight, grid=no_decay_grid, joint_claim_band_on=True)
    bury_t = run(tight, reconcile=reconcile_never_refuse,
                 joint_claim_band_on=True)
    loose_t = run(tight, grid=loose_grid)
    quiet_t = run(tight, self_corridor_clips_on=False)
    unwalked_t = run(tight, orphan_station_rays_on=False)

    assert lateral_and_y_grid is real_grid
    assert reconcile_target is real_reconcile

    far_on = run(straight, joint_claim_band_on=True)
    far_off = run(straight)

    def sep(w):
        p = w["self_corridor_worst_pair"]
        return None if p is None else round(p["their_lat_m"] - p["my_lat_m"], 3)

    def clips(w):
        return w["walls_left_by_cause"].get("shared_corridor", 0)

    v = {"wide_two_claim": on_w["self_corridor_two_claim_cells"],
         "wide_inband": on_w["self_corridor_inband_cells"],
         "wide_refused": on_w["self_corridor_cells"],
         "wide_reconciled": on_w["self_corridor_reconciled"],
         "wide_reconciled_raised": on_w["self_corridor_reconciled_raised"],
         "wide_reconciled_lowered":
             on_w["self_corridor_reconciled_lowered"],
         "wide_reconciled_worst_m":
             on_w["self_corridor_reconciled_worst_m"],
         "wide_caused": on_w["_caused"],
         "wide_worst_over_m": on_w["_worst"],
         "neuter_band_off_caused": off_w["_caused"],
         "neuter_band_off_worst_over_m": off_w["_worst"],
         "neuter_band_off_worst_at": off_w["_worst_at"],
         "neuter_band_off_causes": dict(off_w["walls_left_by_cause"]),
         "neuter_band_off_reconciled": off_w["self_corridor_reconciled"],
         "neuter_band_off_two_claim":
             off_w["self_corridor_two_claim_cells"],
         "neuter_floor_blind_caused": blind_w["_caused"],
         "neuter_floor_blind_reconciled":
             blind_w["self_corridor_reconciled"],
         "neuter_no_decay_caused": nodecay_w["_caused"],
         "neuter_no_decay_worst_over_m": nodecay_w["_worst"],
         "neuter_no_decay_reconciled": nodecay_w["self_corridor_reconciled"],
         "neuter_no_decay_withheld":
             nodecay_w["self_corridor_refused_samples"],
         "tight_two_claim": on_t["self_corridor_two_claim_cells"],
         "tight_inband": on_t["self_corridor_inband_cells"],
         "tight_refused": on_t["self_corridor_cells"],
         "tight_refused_carriageway":
             on_t["self_corridor_carriageway_cells"],
         "tight_clips": clips(on_t),
         "tight_withheld": on_t["self_corridor_refused_samples"],
         "tight_worst_unabsorbable_m": on_t["self_corridor_worst_m"],
         "tight_worst_at": on_t["self_corridor_worst_at"],
         "tight_worst_pair": on_t["self_corridor_worst_pair"],
         "tight_worst_separation_m": sep(on_t),
         "tight_reconciled": on_t["self_corridor_reconciled"],
         "tight_caused": on_t["_caused"],
         "tight_worst_over_m": on_t["_worst"],
         "tight_band_off_caused": off_t["_caused"],
         "tight_band_off_worst_over_m": off_t["_worst"],
         "tight_band_off_clips": clips(off_t),
         "tight_floor_blind_caused": blind_t["_caused"],
         "tight_no_decay_caused": nodecay_t["_caused"],
         "neuter_bury_withheld": bury_t["self_corridor_refused_samples"],
         "neuter_bury_causes": dict(bury_t["walls_left_by_cause"]),
         "neuter_distance_free_two_claim":
             loose_t["self_corridor_two_claim_cells"],
         "neuter_distance_free_refused": loose_t["self_corridor_cells"],
         "neuter_distance_free_separation_m": sep(loose_t),
         "neuter_quiet_refused": quiet_t["self_corridor_cells"],
         "neuter_quiet_clips": clips(quiet_t),
         "tight_orphan_stations": on_t["stations_road_without_ray"],
         "tight_orphan_rays": on_t["orphan_rays_walked"],
         "tight_ray_ends": len(on_t["ray_end_at"]),
         "tight_station_skips": sum(
             1 for r in on_t["ray_skip_at"] if r[2] == "station_without_ray"),
         "neuter_unwalked_stations":
             unwalked_t["stations_road_without_ray"],
         "neuter_unwalked_rays": unwalked_t["orphan_rays_walked"],
         "neuter_unwalked_ray_ends": len(unwalked_t["ray_end_at"]),
         "neuter_unwalked_skips": sum(
             1 for r in unwalked_t["ray_skip_at"]
             if r[2] == "station_without_ray"),
         "neuter_unwalked_wall_max_m": unwalked_t["walls_left_max_m"],
         "neuter_unwalked_worst_skip_m": max(
             [r[3] for r in unwalked_t["ray_skip_at"]
              if r[2] == "station_without_ray"] or [0.0]),
         "straight_two_claim": far_on["self_corridor_two_claim_cells"],
         "straight_inband": far_on["self_corridor_inband_cells"],
         "straight_refused": far_on["self_corridor_cells"],
         "straight_reconciled": far_on["self_corridor_reconciled"],
         "straight_batter": far_on["samples_batter"],
         "straight_batter_band_off": far_off["samples_batter"],
         "straight_caused": far_on["_caused"],
         "straight_orphan_stations": far_on["stations_road_without_ray"],
         "straight_causes": dict(far_on["walls_left_by_cause"])}

    # THE FIXTURES MUST DISCRIMINATE.  A hairpin whose legs never reach each
    # other, or one that leaves no step to begin with, would pass every rule
    # ever written.
    if not v["wide_two_claim"]:
        raise AssertionError(f"the wide fixture stands beside nothing: {v}")
    if not v["neuter_band_off_caused"]:
        raise AssertionError(f"the wide fixture leaves no step: {v}")
    if not v["tight_band_off_caused"]:
        raise AssertionError(f"the tight fixture leaves no step: {v}")

    # ---- 1. WHAT CAN BE RECONCILED IS, AND THE SURFACE SAYS SO -----------
    assert v["wide_caused"] == 0, v
    assert v["wide_caused"] < v["neuter_band_off_caused"], v
    assert v["wide_worst_over_m"] < v["neuter_band_off_worst_over_m"], v
    assert v["wide_reconciled"] > 0, v
    assert v["neuter_band_off_reconciled"] == 0, v
    # THE FLOOR MAY ONLY FILL.  A ceiling arm was written, measured on the
    # 14-segment Fresh42 table and REMOVED: it took the rim gate's caused
    # count from 22 to 110 on T4-meadhall-stathub and 49 to 402 on
    # T9-wttown-dockshore.  This is the arm that goes red if it comes back.
    assert v["wide_reconciled_lowered"] == 0, v
    assert v["wide_reconciled_raised"] == v["wide_reconciled"], v
    # ...AND THE NEUTER IS SILENT ABOUT WHAT IT LEAVES.  This is the finding:
    # the state this shipped in leaves 79 over-limit lattice edges and an
    # EMPTY wall census, because every one of them is left by a ray that
    # exited on `met_ground`.  A wall with no cause is indistinguishable from
    # no wall.
    assert "shared_corridor" not in v["neuter_band_off_causes"], v
    # THE NEUTER MAY NOT HIDE THE POPULATION: the cells are still counted
    # with the reconciliation switched off, so the shipped state cannot be
    # made to look clean by the same switch that makes it wrong.
    assert v["neuter_band_off_two_claim"] == v["wide_two_claim"], v
    # ...and both ways of reaching that state agree, which is what says the
    # rule has ONE home: blanking the floor in the grid and refusing to read
    # it at the write must produce the same surface.
    assert v["neuter_floor_blind_caused"] == v["neuter_band_off_caused"], v
    assert v["neuter_floor_blind_reconciled"] == 0, v
    assert v["tight_floor_blind_caused"] == v["tight_band_off_caused"], v

    # ---- 2. WHAT CANNOT BE RECONCILED IS REFUSED BY NAME -----------------
    assert v["tight_refused"] > 0, v
    assert v["tight_worst_unabsorbable_m"] > 0.0, v
    assert v["tight_clips"] > 0, v
    assert v["tight_withheld"] > 0, v
    # THE PAVED CELLS ARE COUNTED AND NOT CLIPPED.  Two carriageways over one
    # cell is a ROUTE that cannot be built; the batter never writes that cell
    # at all, so a wall clip there names a face the batter did not leave.
    # MEASURED, Fresh42, 14 segments: clipping them put 3,367 of T4's 3,403
    # refusals into the wall census and buried the batter's own.
    assert v["tight_refused_carriageway"] > 0, v
    assert v["tight_clips"] == v["tight_refused"] - v["tight_refused_carriageway"], v
    # ...and the refusal is a MINORITY of the cells that stand beside
    # themselves.  A rule that refused its whole population would be telling
    # the operator nothing.
    assert v["tight_refused"] < v["tight_two_claim"] // 4, v
    # ...and the claim it names is a CLOSE one: the face is beside the road,
    # not 20 m down it.
    assert v["tight_worst_separation_m"] is not None, v
    assert v["tight_worst_separation_m"] <= 2.0 * tcdata.SCALE, v
    # ...and the reconcilable remainder of the SAME fixture still closes.
    assert v["tight_caused"] < v["tight_band_off_caused"], v
    assert v["tight_worst_over_m"] < v["tight_band_off_worst_over_m"], v
    # THE REFUSAL IS A ROUTING FACT AND SURVIVES THE NEUTER, because it has
    # no ground term and no write in it: switching the reconciliation off
    # cannot make an impossible pair possible.
    assert v["tight_band_off_clips"] == v["tight_clips"], v
    # NEUTER: counted and not clipped -- the number is in the log and the rim
    # gate still gets nothing.
    assert v["neuter_quiet_refused"] == v["tight_refused"], v
    assert v["neuter_quiet_clips"] == 0, v

    # ---- 3. EVERY DECLARED TERM IS LOAD-BEARING, IN BOTH DIRECTIONS ------
    # `the_lateral_run_to_each_of_them`: with the decay out of the floor,
    # every cell within 21 m of another run is held at THAT RUN'S ROAD
    # HEIGHT, the quantity becomes a step function of the reach, and it
    # over-fires by an order of magnitude -- 514 moves against 64 -- while
    # leaving MORE over-limit edges than the decayed floor closes and
    # withholding samples the decayed floor never has to.  It closes some of
    # the fixture's steps by brute fill, which is why the arm is keyed on the
    # RECONCILED surface and not on the neutered one: "better than doing
    # nothing" is not the bar.
    assert v["neuter_no_decay_caused"] > v["wide_caused"], v
    assert v["neuter_no_decay_worst_over_m"] > v["wide_worst_over_m"], v
    assert v["neuter_no_decay_reconciled"] > 5 * v["wide_reconciled"], v
    assert v["neuter_no_decay_withheld"] > 0, v
    assert v["tight_no_decay_caused"] > v["tight_caused"], v
    # `this_claim_own_ceiling`: applying the floor where it stands above what
    # this claim's own edge can reach writes the cell from the OTHER road and
    # buries this one -- the withheld samples come back and the wall they
    # leave is `steeper_than_batter`, a DIFFERENT cause about a DIFFERENT
    # face, which is what a refusal turned into a write looks like.
    assert v["neuter_bury_withheld"] == 0, v
    assert v["tight_withheld"] > 0, v
    assert (set(v["neuter_bury_causes"]) - set({"shared_corridor"})
            != set(dict(on_t["walls_left_by_cause"]))
            - set({"shared_corridor"})), v
    # `separation` and `slide_limit` in the CLAIMANT key: distance free, the
    # runner-up kept is whichever run disagrees MOST wherever it stands, so a
    # road going uphill 20 m away reads as a wall and the population goes
    # from 296 cells to 3,625 and its refusals from 28 to 71.
    #
    # THE ARM IS KEYED ON THE POPULATION AND NOT ON THE WORST SEPARATION,
    # because on THIS fixture both keys name a claim 2.0 m off the cell --
    # the tight hairpin's legs are 9 m apart, so the far claimant the loose
    # key reaches for is not far.  Asserting a separation this ground does
    # not show would be asserting something else.
    assert (v["neuter_distance_free_two_claim"]
            > 5 * v["tight_two_claim"]), v
    assert v["neuter_distance_free_refused"] > 2 * v["tight_refused"], v

    # ---- 4. A STATION WITH ROAD AND NO RAY GETS A RAY --------------------
    # WHAT IS *NOT* ASSERTED HERE, and the honesty matters: the walk does not
    # lay a single extra sample ON THIS FIXTURE.  The hairpin's orphan
    # stations have their whole outboard radial already written by the other
    # leg, so the walk finds it graded and carries on.  Its value is that the
    # ray ENDS AT A NAMED EXIT instead of vanishing.
    assert v["tight_orphan_stations"] > 0, v
    assert v["tight_orphan_rays"] == v["tight_orphan_stations"], v
    assert v["tight_station_skips"] == 0, v
    assert v["neuter_unwalked_stations"] == v["tight_orphan_stations"], v
    assert v["neuter_unwalked_rays"] == 0, v
    assert v["neuter_unwalked_skips"] == v["tight_orphan_stations"], v
    assert (v["tight_ray_ends"]
            == v["neuter_unwalked_ray_ends"] + v["tight_orphan_stations"]), v
    assert v["neuter_unwalked_worst_skip_m"] > 0.0, v
    assert (v["neuter_unwalked_worst_skip_m"]
            > v["neuter_unwalked_wall_max_m"]), v

    # ---- AND THE STRAIGHT RUN IS UNTOUCHED, WHICH IS THE OTHER DIRECTION -
    assert v["straight_inband"] > 0, v
    assert v["straight_two_claim"] == 0, v
    assert v["straight_refused"] == 0, v
    assert v["straight_reconciled"] == 0, v
    assert v["straight_caused"] == 0, v
    assert v["straight_batter"] == v["straight_batter_band_off"], v
    assert v["straight_orphan_stations"] == 0, v
    assert "shared_corridor" not in v["straight_causes"], v

    if verbose:
        print(f"  a hairpin whose return leg is 2.3 m lower, 12 m away: "
              f"{v['wide_two_claim']} of {v['wide_inband']} in-band cell(s) "
              f"carry a second claim; {v['wide_reconciled']} batter sample(s) "
              f"held up to the fill floor (all RAISES, worst "
              f"{v['wide_reconciled_worst_m']} m) and {v['wide_caused']} "
              f"over-limit lattice edge(s) left")
        print(f"    NEUTER drop every other run -> "
              f"{v['neuter_band_off_caused']} CAUSED edge(s), worst "
              f"{v['neuter_band_off_worst_over_m']} m over at "
              f"{v['neuter_band_off_worst_at']}, and the wall census is "
              f"{v['neuter_band_off_causes'] or '{}'} -- SILENT")
        print(f"    NEUTER drop the run from the envelope -> "
              f"{v['neuter_no_decay_caused']} CAUSED edge(s) and "
              f"{v['neuter_no_decay_reconciled']} move(s) against "
              f"{v['wide_reconciled']}: a floor that does not decay is a "
              f"cliff at the reach")
        print(f"  the same road with its edges overlapping and 1.7 m of fall: "
              f"{v['tight_refused']} of {v['tight_two_claim']} two-claim "
              f"cell(s) have NO admissible height "
              f"({v['tight_refused_carriageway']} of them CARRIAGEWAY, "
              f"counted and not clipped; {v['tight_clips']} batter clip(s) "
              f"withholding {v['tight_withheld']} sample(s)), worst "
              f"{v['tight_worst_unabsorbable_m']} m at {v['tight_worst_at']} "
              f"over a {v['tight_worst_separation_m']} m gap; the rest closes "
              f"{v['tight_band_off_caused']} -> {v['tight_caused']} edge(s)")
        print(f"    NEUTER drop this claim's own ceiling -> "
              f"{v['neuter_bury_withheld']} sample(s) withheld and the census "
              f"becomes {v['neuter_bury_causes']}: the refusal is written "
              f"from the other road instead")
        print(f"    NEUTER make distance free -> "
              f"{v['neuter_distance_free_two_claim']} two-claim cell(s) "
              f"against {v['tight_two_claim']}, worst claim "
              f"{v['neuter_distance_free_separation_m']} m off this cell "
              f"against {v['tight_worst_separation_m']} m")
        print(f"    NEUTER count but do not clip -> "
              f"{v['neuter_quiet_refused']} cell(s) counted, "
              f"{v['neuter_quiet_clips']} clip(s) -- the empty cause list")
        print(f"  {v['tight_orphan_stations']} station(s) with ROAD AND NO "
              f"RAY, walked from their own edge")
        print(f"    NEUTER leave them unwalked -> "
              f"{v['neuter_unwalked_skips']} abandoned edge(s), worst "
              f"{v['neuter_unwalked_worst_skip_m']} m, against a wall census "
              f"whose worst is {v['neuter_unwalked_wall_max_m']} m: NO cause "
              f"names them")
        print(f"    a STRAIGHT run of the same road: "
              f"{v['straight_two_claim']} of {v['straight_inband']} cell(s) "
              f"stand beside anything, {v['straight_batter']} batter "
              f"sample(s) either way -- identical")
        print("ribbon beside-itself selftest OK")
    return v


# WHAT A RAY'S END ACTUALLY DEPENDS ON.  Every one of these is a fact about
# the ray's OWN RADIAL.  What is NOT in this tuple is the point: the ray used
# to end when its `(side, round(arc), bearing)` BIN ran out of lattice
# samples, which is a fact about rounding and not about the ground.
RAY_END_DEPENDS_ON = ("plane_vs_ground_on_this_radial", "gate_refusal",
                      "earth_budget", "reach_spent", "lattice_cover")


# A ZIGZAG OVER KNOLLY GROUND, BECAUSE BOTH HALVES OF THE PRODUCTION SHAPE
# ARE NEEDED AND NEITHER IS ENOUGH ALONE.
#
# THE CORNERS MAKE THE BINS RUN DRY.  `int(round(S))` bins an along-track
# metre and on a straight run each such bin holds a full transect -- MEASURED
# over twelve straight fixtures at angles from 5:48 to 41:48, 0 to 2 bins out
# of ~200 rays run dry.  At a VERTEX the projection collapses onto the corner
# and the bearing splits that wedge into many thin bins, most holding only
# the samples nearest the apex.  This fixture: 18 of 263 rays.
#
# THE KNOLLS MAKE THE NEIGHBOUR STOP.  A dry bin is harmless while the
# neighbouring bin walks the same ground -- MEASURED on flat ground, 33 dry
# bins and ZERO cells that no other ray lays, which is the old premise being
# TRUE.  It stops being true the moment adjacent bins meet ground at
# different laterals, which is what natural micro-terrain does and what
# leaves T1's 27 of 27.  The knolls are a fixed lattice pattern, computed
# here, so no neuter can reach them.
_END_NODES = [(-26.0, -6.0), (-6.0, 9.0), (9.0, -7.0), (26.0, 8.0)]
# WELL CLEAR OF THE 30.0 m WATER PLANE, because this fixture is about where a
# ray ENDS and nothing else: ground within reach of the plane brings the water
# gate into the answer and the arms stop measuring the thing under test.
_END_GROUND_Y = 60.0
_END_KNOLL_M, _END_KNOLL_MOD = 4.0, 5
# An embankment whose taper FINISHES inside the reach (toe at lat 11.68 of a
# 14.5 m reach over the lowest ground), so "did anything get written outboard
# of the toe" is a question with a known answer.
_END_ROAD_Y = 66.0
# THE BANK THAT RUNS AWAY.  Flat ground cannot make a ray spend its reach:
# the apply clamp caps the edge wall at 8 m and 8 / 0.7813 = 10.24 m of run
# lands inside a 10.5 m reach every time.  Ground that FALLS at 0.72 m/m
# leaves the plane closing at 0.0613 m/m, so a 6 m wall needs 98 m and every
# ray must spend its reach and say so.
_END_FALL_PER_M, _END_FALL_ROAD_Y = 0.72, 63.0


def _end_ground(wx: float, wz: float) -> float:
    knoll = (int(round(wx)) * 7 + int(round(wz)) * 13) % _END_KNOLL_MOD == 0
    return _END_GROUND_Y + (_END_KNOLL_M if knoll else 0.0)


def _end_falling_ground(wx: float, wz: float) -> float:
    return _END_GROUND_Y - _END_FALL_PER_M * _end_lat(wx, wz)


def _end_lat(wx: float, wz: float) -> float:
    """Distance to the fixture's own centreline, in closed form."""
    best = float("inf")
    for (ax, az), (bx, bz) in zip(_END_NODES, _END_NODES[1:]):
        dx, dz = bx - ax, bz - az
        t = ((wx - ax) * dx + (wz - az) * dz) / (dx * dx + dz * dz)
        t = min(1.0, max(0.0, t))
        best = min(best, math.hypot(wx - (ax + t * dx), wz - (az + t * dz)))
    return best


def selftest_batter_ray_end(verbose: bool = True) -> dict:
    """WHERE A RAY ENDS, AND THE PREMISE THAT USED TO END IT EARLY.

    A ray used to stop when its arc BIN ran out of lattice samples, recording
    nothing, on the declared premise that "the ground outboard of it at this
    arc belongs to a neighbouring ray and is written there".  NOBODY HAD
    MEASURED THAT POPULATION.  MEASURED on Fresh42, 2026-09-24, over the
    in-band lattice the stamp itself scanned: 1,970 rays network-wide took
    that exit and 1,926 of them (97.8 %) have in-band ground outboard of
    their last member that NO ray writes; ZERO of them have no outboard
    ground at all.  On T1-portalhub-temple it is 27 of 27, and the ray at
    (-130, -308) is the taken side of the 1.0875 m step `rim_transfer`
    refused the whole segment for.

    THE VERDICT IS THE STEP LEFT ON THE GROUND, not a counter: a ray that
    stops early leaves no clip, no residual and no counter of its own, which
    is exactly why the premise survived for as long as it did.  The step is
    read off the COMPILED surface -- `generated + delta_at`, the composition
    `applied_at` and `rim_transfer` use -- over ground this function builds
    in memory, so a neuter may change the rule and can never reach the
    denominator.
    """
    assert "plane_vs_ground_on_this_radial" in RAY_END_DEPENDS_ON
    assert "lattice_cover" in RAY_END_DEPENDS_ON
    edge = 3.0 + SHOULDER_M
    reach = edge + BATTER_MAX_M

    def judge(st: dict, ground_at=_end_ground, cells=None) -> dict:
        """The worst 1 m step this surface leaves, and where the ground went.

        `cells` narrows the scan to a named set.  The whole-fixture maximum is
        the wrong verdict for the abandoned ground: this fixture's knolls are
        4 m high, so the largest step anywhere is a knoll face that no batter
        touches and it drowns the thing under test.  The question is what the
        step is AT THE CELLS THE PREMISE ABANDONS.
        """
        comps = st["_comps"]
        w = {(int(round(x)), int(round(z))) for x, z in st["written"]}

        def surface(c) -> float:
            return (ground_at(float(c[0]), float(c[1]))
                    + delta_at(comps, float(c[0]), float(c[1])))

        worst, at = 0.0, None
        for c in sorted(w if cells is None else cells):
            for n in ((c[0] + 1, c[1]), (c[0] - 1, c[1]),
                      (c[0], c[1] + 1), (c[0], c[1] - 1)):
                if _end_lat(float(n[0]), float(n[1])) > reach:
                    continue
                step = abs(surface(c) - surface(n))
                if step > worst:
                    worst, at = step, [list(c), list(n)]
        # THE DEEPEST TOE THIS FIXTURE CAN HAVE, taken over its LOWEST ground
        # so the test is conservative: a cell written outboard of this is
        # past its own crossing whatever knoll it sits on.
        toe = edge + (_END_ROAD_Y - _END_GROUND_Y) / BATTER_GRADE
        return {"written": w, "samples": len(w),
                "worst_step_m": round(worst, 4), "worst_at": at,
                "toe_lat_m": round(toe, 3),
                "beyond_the_toe": sum(
                    1 for c in w
                    if _end_lat(float(c[0]), float(c[1])) > toe),
                "by_cause": dict(st["walls_left_by_cause"]),
                "worst_wall_m": st["walls_left_max_m"],
                "continued": st["rays_continued"],
                "rays": st["rays"]}

    ship_st = _rasterise(_END_NODES, _END_ROAD_Y, _end_ground)
    ship = judge(ship_st)
    # THE FIXTURE MUST DISCRIMINATE: if no bin runs dry there is no case.
    if ship["continued"] == 0:
        raise AssertionError(f"no bin ran out on this fixture: {ship}")
    v = {"shipping": ship}

    # NEUTER A -- THE FALSE PREMISE, RUN.  The ray stops where its bin stops
    # and says nothing.  The ground it abandons must go MISSING and the step
    # AT THAT GROUND must GROW, and nothing may be reported about either.
    blind_st = _rasterise(_END_NODES, _END_ROAD_Y, _end_ground,
                          ray_walks_past_its_bin=False)
    blind = judge(blind_st)
    v["neuter_bin_ends_the_ray"] = blind
    abandoned = ship["written"] - blind["written"]
    v["abandoned_cells"] = len(abandoned)
    assert v["abandoned_cells"] > 0, v
    assert blind["samples"] < ship["samples"], v
    assert not (blind["written"] - ship["written"]), v
    ship_at = judge(ship_st, cells=abandoned)
    blind_at = judge(blind_st, cells=abandoned)
    v["step_at_abandoned"] = {"shipping": ship_at["worst_step_m"],
                              "neutered": blind_at["worst_step_m"],
                              "neutered_at": blind_at["worst_at"]}
    assert blind_at["worst_step_m"] > ship_at["worst_step_m"], v
    # ...AND IT IS SILENT, WHICH IS WHY NO COUNTER COULD HAVE CAUGHT IT.  The
    # neutered run records a `ray_bin_exhausted` skip carrying a real residual
    # for every dry bin, and NOT ONE of them reaches `walls_left_by_cause` --
    # so the wall census, which is what every gate downstream consults, reads
    # the abandoned ground as ground nobody had anything to say about.  The
    # repaired run says MORE, never less.
    v["neuter_skips"] = [r for r in blind_st["ray_skip_at"]
                         if r[2] == "ray_bin_exhausted"]
    v["neuter_worst_unreported_m"] = round(
        max((r[3] for r in v["neuter_skips"]), default=0.0), 3)
    assert len(v["neuter_skips"]) == blind["continued"], v
    assert v["neuter_worst_unreported_m"] > 0.5, v
    assert "bin_exhausted" not in blind["by_cause"], v
    assert (sum(blind["by_cause"].values())
            <= sum(ship["by_cause"].values())), v

    # NEUTER B, THE OTHER DIRECTION -- drop `plane_vs_ground`.  A walk that
    # does not stop at its own crossing does not fix a step, it cuts a trench
    # in ground the taper had already finished with.  Ground laid BEYOND THE
    # TOE is zero when the rule holds and must not be when it is dropped.
    real = globals()["batter_met_ground"]
    globals()["batter_met_ground"] = lambda delta, sign: False
    try:
        runaway = judge(_rasterise(_END_NODES, _END_ROAD_Y, _end_ground))
    finally:
        globals()["batter_met_ground"] = real
    v["neuter_never_meets_ground"] = runaway
    assert ship["beyond_the_toe"] == 0, v
    assert runaway["beyond_the_toe"] > 0, v

    # NEUTER C, THE THIRD DEPENDENCY -- drop `lattice_cover` by handing the
    # walk a cover one zone wide while its rays reach 14.5 m past a 48 m
    # centreline.  UNREAD IS NOT CLEAR: the walk must NAME the ground it
    # cannot read rather than end quietly one cell short of it.
    short = judge(_rasterise(_END_NODES, _END_ROAD_Y, _end_ground,
                             zones=((0, 0),)))
    v["neuter_short_cover"] = short
    assert "unread_ground" not in ship["by_cause"], v
    assert short["by_cause"].get("unread_ground", 0) > 0, v
    assert short["worst_wall_m"] > 0.0, v

    # AND THE EXIT MUST BE NAMED WHEN THE REACH REALLY IS SPENT.  Over a bank
    # that falls at 0.72 m/m the plane closes at 0.0613 m/m and a 6 m wall
    # needs 98 m of run, so EVERY ray owes a wall at 10.5 m -- and the neuter
    # loses exactly the ones whose bin ran dry, silently.
    cliff = judge(_rasterise(_END_NODES, _END_FALL_ROAD_Y,
                             _end_falling_ground),
                  _end_falling_ground)
    cliff_blind = judge(_rasterise(_END_NODES, _END_FALL_ROAD_Y,
                                   _end_falling_ground,
                                   ray_walks_past_its_bin=False),
                        _end_falling_ground)
    v["cliff"], v["cliff_neutered"] = cliff, cliff_blind
    assert set(cliff["by_cause"]) == {"reach"}, v
    assert (cliff["by_cause"]["reach"]
            > cliff_blind["by_cause"].get("reach", 0)), v
    if verbose:
        print(f"  a 6 m embankment on a knolly zigzag: {ship['samples']} "
              f"sample(s) laid over {ship['rays']} ray(s), "
              f"{ship['continued']} of them past a dry bin")
        print(f"    NEUTER the bin ENDS the ray -> {blind['samples']} "
              f"sample(s): {v['abandoned_cells']} cell(s) abandoned, worst "
              f"1 m step AT THEM {v['step_at_abandoned']['neutered']} m at "
              f"{v['step_at_abandoned']['neutered_at']} against "
              f"{v['step_at_abandoned']['shipping']} m repaired -- and "
              f"{len(v['neuter_skips'])} residual(s) up to "
              f"{v['neuter_worst_unreported_m']} m recorded as skips that "
              f"NO wall cause names: SILENT")
        print(f"    NEUTER never meets ground -> {runaway['beyond_the_toe']} "
              f"sample(s) cut beyond the toe (shipping "
              f"{ship['beyond_the_toe']})")
        print(f"    NEUTER one-zone cover -> {short['by_cause']}, worst "
              f"unread residual {short['worst_wall_m']} m")
        print(f"  a bank that runs away: every ray spends its reach and says "
              f"so, {cliff['by_cause']} vs neutered {cliff_blind['by_cause']}")
        print("ribbon batter ray-end selftest OK")
    return v


# THE FIXTURE IS `selftest_batter_ray_end`'s OWN GROUND -- the 6 m embankment
# on the knolly zigzag -- REUSED ON PURPOSE.  Its corners give the fans where
# two lattice neighbours sit 2 m apart in LATERAL, which is where the plane
# falls 1.6 m over one metre of ground and the toe lands hard.  A gentler
# micro-terrain was tried first and MEASURED USELESS: 0.55 m knolls under a
# 3 m bank leave ZERO caused edges either way, because the taper crosses them
# between samples and never leaves a face.


def selftest_toe_feather(verbose: bool = True) -> dict:
    """THE STEP OFF THE END OF THE EARTHWORK, AND NOTHING EVER JUDGED IT.

    Every sample is written at its own ray's plane, and no rule in this file
    ever compared a written sample with the LATTICE CELL BESIDE IT.  The
    plane's own fall over one metre of lateral IS the walker's slide limit,
    so the taper arrives at natural ground having already spent the whole
    budget, and the last edge out of the write is over it.

    THE QUANTITY IS OVER-LIMIT 1 m LATTICE EDGES ON THE FINISHED SURFACE,
    measured with `delta_at` over the compilers the run actually produced --
    the same composition `applied_at` uses -- and not against the plane the
    fixture asked for.  That is the exact thing `junction.rim_transfer`
    judges, so the fixture cannot pass by measuring something adjacent to the
    gate.

    AND THE SILENCE IS THE POINT AGAIN: the neutered arm reports the same
    wall census as the repaired one, because every one of these edges is left
    by a ray that exited on `met_ground`.
    """
    assert "the_natural_ground_at_every_lattice_NEIGHBOUR" in \
        TOE_FEATHER_DEPENDS_ON
    sys.path.insert(0, str(HERE))
    import junction as _junction                          # noqa: PLC0415
    limit = _junction.SLIDE_PER_M

    def surface(st, ground):
        """The finished height at every written cell and every cell beside
        one, and the edges this write CAUSED to go over the limit.

        CAUSED, NOT OVER -- `junction.rim_transfer`'s own rule, and the
        fixture is worthless without it: an edge between two natural cells
        that were already over the limit is terrain, and counting it measures
        the hillside instead of the earthwork.
        """
        comps = st["_comps"]
        wrote = {(int(round(x)), int(round(z))) for x, z in st["written"]}
        H, G = {}, {}
        for c in wrote:
            for dx, dz in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)):
                n = (c[0] + dx, c[1] + dz)
                if n not in H:
                    G[n] = ground(float(n[0]), float(n[1]))
                    H[n] = G[n] + delta_at(comps, float(n[0]), float(n[1]))
        seen, caused, worst, worst_at = set(), 0, 0.0, None
        for c in wrote:
            for dx, dz in ((1, 0), (0, 1), (-1, 0), (0, -1)):
                n = (c[0] + dx, c[1] + dz)
                e = (c, n) if c < n else (n, c)
                if e in seen:
                    continue
                seen.add(e)
                if abs(G[c] - G[n]) > limit + 1e-9:
                    continue          # PRE-EXISTING: terrain, not this write
                step = abs(H[c] - H[n])
                if step > limit + 1e-9:
                    caused += 1
                    if step > worst:
                        worst, worst_at = round(step, 4), [c, n]
        return {"caused_edges": caused, "worst_edge_m": worst,
                "worst_edge_at": worst_at, "written": len(wrote),
                "walls_by_cause": dict(st["walls_left_by_cause"]),
                "H": H, "wrote": wrote}

    def run(on: bool, ground=_end_ground, road_y=_END_ROAD_Y):
        # THE FEATHER'S OWN DIRECTION RULE IS UNDER TEST; the rim closure is
        # a separate stage that moves a cell AWAY from ground inside its own
        # band (RIM_CLOSURE_DEPENDS_ON, `selftest_rim_closure`).
        st = _rasterise(_END_NODES, road_y, ground, feather_toe_on=on,
                        rim_closure_on=False)
        s = surface(st, ground)
        s["feathered"] = st["toe_feather"]["feathered"]
        s["paved"] = st["samples_paved"] + st["samples_shoulder"]
        s["road_written"] = {(int(round(x)), int(round(z)))
                             for x, z in st["road_written"]}
        return s

    laid, left = run(True), run(False)
    v = {"feathered": {k: x for k, x in laid.items()
                       if k not in ("H", "wrote", "road_written")},
         "not_feathered": {k: x for k, x in left.items()
                           if k not in ("H", "wrote", "road_written")}}
    # THE FIXTURE MUST DISCRIMINATE: ground that leaves no step either way
    # proves nothing about a feather.
    if not left["caused_edges"]:
        raise AssertionError(f"the fixture left no step at all: {v}")

    # ---- DIRECTION ONE: THE DEPENDENCY IS LOST AND THE STEPS COME BACK ----
    assert laid["caused_edges"] < left["caused_edges"], v
    # NOT the worst edge: the worst is a CARRIAGEWAY edge the batter never
    # reached (a `steeper_than_batter` clip, reported with its own cause), and
    # the feather is forbidden to bend the road surface.  Asserting on it
    # would be asserting that this repair fixes a different defect.
    assert laid["worst_edge_m"] <= left["worst_edge_m"], v
    assert laid["feathered"] > 0 and left["feathered"] == 0, v
    # AND NOTHING IS SAID ABOUT THEM.  The wall census is IDENTICAL in both
    # arms: every one of these edges is left by a ray that exited on the
    # batter's SUCCESS exit, so a reader of the census cannot tell the two
    # surfaces apart.
    assert laid["walls_by_cause"] == left["walls_by_cause"], v

    # ---- DIRECTION TWO: THE FEATHER MAY ONLY GIVE EARTH BACK --------------
    # It must never move a sample AWAY from the ground it stands on, and it
    # must never touch the carriageway.  A feather that could add earth would
    # satisfy one neighbour by making a step at another -- the restitution
    # defect -- and this arm is the one that goes red if that is ever
    # written.  MEASURED over every written cell, not sampled.
    assert laid["road_written"] == left["road_written"], v
    moved_road, moved_away = 0, 0
    for c in sorted(left["wrote"] & laid["wrote"]):
        g = _end_ground(float(c[0]), float(c[1]))
        if abs(laid["H"][c] - left["H"][c]) <= EDGE_EPS_M:
            continue
        if c in laid["road_written"]:
            moved_road += 1
        if abs(laid["H"][c] - g) > abs(left["H"][c] - g) + EDGE_EPS_M:
            moved_away += 1
    v["carriageway_cells_moved"] = moved_road
    v["cells_moved_away_from_ground"] = moved_away
    assert moved_road == 0, v
    assert moved_away == 0, v

    # ---- AND ON GROUND WITH NO BREAK IT MUST COST NOTHING -----------------
    # A flat plain under the same road: the taper closes on ground that has
    # no step of its own, so there is nothing to lay back and the two arms
    # must produce the identical surface.  This is the arm that goes red if
    # the feather is ever applied to samples that were already within the
    # limit -- the opposite failure from the one above.
    flat_on = run(True, lambda wx, wz: _END_GROUND_Y)
    flat_off = run(False, lambda wx, wz: _END_GROUND_Y)
    assert flat_on["wrote"] == flat_off["wrote"], v
    assert flat_on["feathered"] == 0, (
        f"the feather moved {flat_on['feathered']} sample(s) on ground with "
        f"no break of slope")
    assert flat_on["written"] > 0, v
    v["flat_written"] = flat_on["written"]

    if verbose:
        print(f"  a 6 m embankment on the knolly zigzag: "
              f"{laid['caused_edges']} CAUSED over-limit 1 m edge(s) out of "
              f"the write, worst {laid['worst_edge_m']} m, after laying "
              f"{laid['feathered']} toe sample(s) back")
        print(f"    NEUTER drop the feather -> {left['caused_edges']} "
              f"edge(s), worst {left['worst_edge_m']} m at "
              f"{left['worst_edge_at']} -- and the wall census is the SAME "
              f"{left['walls_by_cause'] or '{}'} either way: SILENT")
        print(f"  it only ever gives earth back: "
              f"{v['carriageway_cells_moved']} carriageway sample(s) moved, "
              f"{v['cells_moved_away_from_ground']} sample(s) moved away "
              f"from natural ground")
        print(f"  flat ground has no break of slope and costs nothing: "
              f"{v['flat_written']} sample(s), identical either way")
        print("ribbon toe-feather selftest OK")
    return v


def _edge_census(st: dict, ground) -> dict:
    """EVERY 1 m lattice edge out of or inside this write, on the FINISHED
    surface, `junction.rim_transfer`'s CAUSED rule -- and split by whether
    the gate can see it.

    `rim` is an edge with one side the write leaves (within
    AUTHOR_TOLERANCE of) its ground: the rim gate judges it.  `interior` has
    both sides moved: THE RIM GATE NEVER READS IT.  `carriageway` counts the
    caused edges with the road surface on one side and a non-road sample on
    the other -- a face standing AT THE ROAD EDGE, which is the one place
    the feather is forbidden to put one.  The verdict is read off
    `delta_at` over the compilers the run produced, so no arm can pass by
    measuring the plane it asked for instead of the ground it left.
    """
    sys.path.insert(0, str(HERE))
    import junction as _junction                          # noqa: PLC0415
    limit = _junction.SLIDE_PER_M
    comps = st["_comps"]
    wrote = {(int(round(x)), int(round(z))) for x, z in st["written"]}
    road = {(int(round(x)), int(round(z))) for x, z in st["road_written"]}
    H, G = {}, {}
    for c in wrote:
        for dx, dz in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (c[0] + dx, c[1] + dz)
            if n not in H:
                G[n] = ground(float(n[0]), float(n[1]))
                H[n] = G[n] + delta_at(comps, float(n[0]), float(n[1]))
    out = {"rim": 0, "interior": 0, "carriageway": 0, "worst_m": 0.0,
           "worst_at": None, "excess_m": 0.0}
    seen = set()
    for c in wrote:
        for dx, dz in ((1, 0), (0, 1), (-1, 0), (0, -1)):
            n = (c[0] + dx, c[1] + dz)
            e = (c, n) if c < n else (n, c)
            if e in seen:
                continue
            seen.add(e)
            if abs(G[c] - G[n]) > limit + 1e-9:
                continue          # PRE-EXISTING: terrain, not this write
            step = abs(H[c] - H[n])
            if step <= limit + 1e-9:
                continue
            moved = [abs(H[q] - G[q]) > appliedmod.AUTHOR_TOLERANCE_M
                     for q in (c, n)]
            out["interior" if all(moved) else "rim"] += 1
            if (c in road) != (n in road):
                out["carriageway"] += 1
            out["excess_m"] = round(out["excess_m"] + step - limit, 3)
            if step > out["worst_m"]:
                out["worst_m"], out["worst_at"] = round(step, 4), [c, n]
    return out


def _rim_gate_over(st: dict, ground, author_now=None, height_after=None) -> dict:
    """`junction.rim_transfer` ITSELF over a fixture's finished surface, with
    the taken set built exactly as `main` builds it: every sample this write
    moves by more than AUTHOR_TOLERANCE."""
    sys.path.insert(0, str(HERE))
    import junction as _junction                          # noqa: PLC0415
    comps = st["_comps"]
    taken: dict[tuple[int, int], float] = {}
    for (zx, zz), comp in comps.items():
        cx0, cz0 = tcdata.zone_centre(zx, zz)
        for i in range(tcdata.SAMPLES):
            if not comp.modified_height[i]:
                continue
            gy, gx = divmod(i, tcdata.PITCH)
            wx, wz = tcdata.sample_world(cx0, cz0, gx, gy)
            sx, sz = int(round(wx)), int(round(wz))
            g = ground(float(sx), float(sz))
            after = g + delta_at(comps, float(sx), float(sz))
            if abs(after - g) > appliedmod.AUTHOR_TOLERANCE_M:
                taken[(sx, sz)] = after
    r = _junction.rim_transfer(
        taken, lambda sx, sz: ground(float(sx), float(sz)), author_now,
        top=10_000,
        height_after=(height_after or
                      (lambda sx, sz: ground(float(sx), float(sz))
                       + delta_at(comps, float(sx), float(sz)))))
    return {"verdict": r["verdict"], "caused": r["caused"],
            "worst_caused_m": r["worst_caused_m"],
            "taken_samples": r["taken_samples"], "rows": r["caused_rows"]}


# S13-stathub-dock's SHORE, IN MINIATURE: a carriageway on a 2 m fill along
# the waterline, two metres of dry toe and then ground the seed drew under
# the 30.0 m plane, which `water_fill` forbids the batter to raise.  Road
# 33.0 over 31.0, toe 30.8 and 30.4, water 29.8 from lat 7: the plane still
# stands 0.86 m above the bed there and nothing may close it.
_SHORE_ROAD_Y = 33.0
_SHORE_NODES = [(-30.0, 0.0), (30.0, 0.0)]


def _shore_ground(wx: float, wz: float) -> float:
    a = int(round(abs(wz)))
    if a <= 4:
        return 31.0
    return {5: 30.8, 6: 30.4}.get(a, 29.8)


# T7-stathub-stenvik's TWO BRANCHES, IN MINIATURE: a hairpin laid at 45 deg
# to the lattice, both legs in CUT under a flat 70.0 m plain, the outbound
# leg 1.0 m under it and the return leg 17 m away 4.69 m under it.  The high
# leg's plane meets ground at lat 5.3; the low leg's is still 1.2 m below
# ground at the seam between them.  At 45 deg a lattice path to the low
# carriageway is sqrt(2) longer than the radial, so a surface every 1 m edge
# of which is walkable EXISTS -- the low leg's cut only has to be given back.
_TWIN_SEP_M, _TWIN_G = 17.0, 70.0
_TWIN_CUT_HI, _TWIN_CUT_LO = 1.0, 4.69


def _twin_nodes() -> list[tuple[float, float]]:
    r = math.sqrt(0.5)
    p1 = (40.0 * r, 40.0 * r)
    p2 = (p1[0] - _TWIN_SEP_M * r, p1[1] + _TWIN_SEP_M * r)
    return [(0.0, 0.0), p1, p2, (p2[0] - 40.0 * r, p2[1] - 40.0 * r)]


def selftest_feather_conflict(verbose: bool = True) -> dict:
    """WHICH WAY THE FEATHER RESOLVES A SAMPLE ITS NEIGHBOURS DISAGREE ABOUT.

    THREE DEPENDENCIES, ONE ARM EACH, and every arm's known-bad state is a
    measured production state.  The verdicts are the caused-edge census over
    the finished surface AND `junction.rim_transfer`'s own verdict over it,
    because the shipped rule's worst failure is a surface the gate PASSES.

      direction -- dropped, a CUT conflict answers `hi`, the sample never
                   rises and T7's two-branch seam stands: the twin hairpin's
                   rim steps come back.
      pins      -- dropped, a conflict moves toward the ground whatever
                   cannot follow, and S13's shore comes back: the toe is laid
                   down, the face moves ONTO THE CARRIAGEWAY EDGE, both of
                   its sides are moved samples, and the rim gate reads
                   CLEAR over every one of them.
      pre-existing -- bound on, an edge the ground already had over the
                   limit pins a toe the gate never judges, and the knolly
                   embankment's caused edges climb back toward no feather.
    """
    assert FEATHER_CONFLICT_DEPENDS_ON == (
        "this_sample's_own_earthwork_direction",
        "how_far_the_ground_that_cannot_follow_lets_it_move",
        "whether_the_edge_was_already_over_before_this_write")
    arms = {"survivor": {},
            "no_direction": {"feather_toward_ground": False},
            "no_pins": {"feather_pins_hold": False},
            "binds_on_preexisting": {"feather_binds_on_preexisting": True},
            "shipped": {"feather_toward_ground": False,
                        "feather_pins_hold": False,
                        "feather_binds_on_preexisting": True}}
    fixtures = {
        "twin_cut": (_twin_nodes(), None,
                     [_TWIN_G - _TWIN_CUT_HI] * 2 + [_TWIN_G - _TWIN_CUT_LO] * 2,
                     lambda wx, wz: _TWIN_G),
        "shore_fill": (_SHORE_NODES, _SHORE_ROAD_Y, None, _shore_ground),
        "knolly_fill": (_END_NODES, _END_ROAD_Y, None, _end_ground)}
    v: dict = {}
    for fname, (nodes, road_y, prof, ground) in fixtures.items():
        v[fname] = {}
        for arm, kw in arms.items():
            st = _rasterise(nodes, road_y if road_y is not None else 0.0,
                            ground, profile_y=prof, rim_closure_on=False,
                            **kw)
            e = _edge_census(st, ground)
            e["gate"] = _rim_gate_over(st, ground)
            e["written"] = len(st["written"])
            v[fname][arm] = e
        # THE DENOMINATOR IS THE FIXTURE'S, NOT THE ARM'S: every arm writes
        # the same samples -- the feather moves heights, never membership --
        # so no arm can pass by writing less.
        ws = {a: v[fname][a]["written"] for a in arms}
        assert len(set(ws.values())) == 1 and ws["survivor"] > 0, (fname, ws)

    tw, sh, kn = v["twin_cut"], v["shore_fill"], v["knolly_fill"]
    total = lambda e: e["rim"] + e["interior"]               # noqa: E731
    # ---- DIRECTION: T7's seam -------------------------------------------
    assert tw["shipped"]["rim"] > 0, tw
    assert tw["no_direction"]["rim"] == tw["shipped"]["rim"], tw
    assert tw["survivor"]["rim"] < tw["shipped"]["rim"], tw
    assert total(tw["survivor"]) < total(tw["shipped"]), tw
    assert tw["survivor"]["carriageway"] <= tw["shipped"]["carriageway"], tw
    # ---- PINS: S13's shore -- the gate PASSES the shipped surface -------
    assert sh["shipped"]["gate"]["verdict"] == "CLEAR", sh
    assert sh["shipped"]["carriageway"] > 0, sh
    assert sh["shipped"]["interior"] == sh["shipped"]["carriageway"], sh
    for arm in ("no_direction", "no_pins"):
        assert sh[arm]["gate"]["verdict"] == "CLEAR", (arm, sh)
        assert sh[arm]["carriageway"] > 0, (arm, sh)
    assert sh["survivor"]["gate"]["verdict"] == "REFUSED", sh
    assert sh["survivor"]["carriageway"] == 0, sh
    assert sh["survivor"]["interior"] == 0, sh
    # and on T7's seam the same neuter hides what it cannot close: the rim
    # count FALLS and the carriageway count RISES.
    assert tw["no_pins"]["carriageway"] > tw["survivor"]["carriageway"], tw
    # ---- PRE-EXISTING: the knolls ---------------------------------------
    assert total(kn["binds_on_preexisting"]) > total(kn["survivor"]), kn
    assert kn["survivor"]["interior"] < kn["shipped"]["interior"], kn
    assert kn["survivor"]["carriageway"] < kn["shipped"]["carriageway"], kn

    if verbose:
        def row(e):
            return (f"rim {e['rim']:3d}  interior {e['interior']:3d}  at the "
                    f"carriageway {e['carriageway']:3d}  worst {e['worst_m']}  excess {e['excess_m']}"
                    f"  | rim gate {e['gate']['verdict']} "
                    f"{e['gate']['caused']}")
        for fname in fixtures:
            print(f"  {fname}:")
            for arm in arms:
                print(f"    {arm:22s} {row(v[fname][arm])}")
        print("ribbon feather-conflict selftest OK")
    return v


def selftest_rim_after_surface(verbose: bool = True) -> dict:
    """THE RIM JUDGED AGAINST THE SURFACE THAT IS SENT, BOTH DIRECTIONS.

    One zone in memory, flat ground at 40.0, a PRIOR claim's live deltas in
    a dict, this write's samples in a real `tcdata.Compiler`, and the real
    `junction.rim_transfer` as the judge.  Three blocks, far enough apart
    that no rim pair spans two of them:

      A  T8's PHANTOM: a prior fill of +4.0 over a 7x7 block, this write
         re-lays its centre at +4.5.  The sent blob carries the prior fill,
         so the rim stands at 44.0 and nothing is caused.
      B  the HIDDEN step: a prior +0.5 over a block, this write cuts its
         centre to -0.3.  Against the sent surface the step is 0.8 m from
         0.0 before -- CAUSED; against generated ground it reads 0.3.
      C  this write's OWN NUDGE: the rim sample moves 0.009 m, inside
         AUTHOR_TOLERANCE so it is rim and not taken, and that nudge is what
         takes a 0.775 m step to 0.784 m.

    `carries_priors=False` is the rule that shipped (A returns, B hides) and
    `own_samples=False` its mirror (C hides).  The population is the
    fixture's and every arm is handed the same taken set, so no arm can pass
    by judging less.
    """
    assert RIM_AFTER_SURFACE_DEPENDS_ON == (
        "this_write's_own_samples",
        "every_prior_claim_carried_into_the_zones_it_emits")
    sys.path.insert(0, str(HERE))
    import junction as _junction                          # noqa: PLC0415
    ground = 40.0
    comp = tcdata.Compiler(zone_x=0, zone_z=0)
    comps = {(0, 0): comp}
    prior: dict[tuple[int, int], float] = {}

    def mine(sx: int, sz: int, d: float) -> None:
        gx, gy = tcdata.vertex_mask_index(*comp.centre, sx, sz)
        comp.set_height(gx, gy, d)

    for dx in range(-3, 4):
        for dz in range(-3, 4):
            prior[(-20 + dx, dz)] = 4.0
            prior[(dx, dz)] = 0.5
    mine(-20, 0, 4.5)                                   # A
    mine(0, 0, -0.3)                                    # B
    mine(20, 0, 0.775)                                  # C, taken
    mine(21, 0, -0.009)                                 # C, the nudged rim
    taken = {(-20, 0): ground + 4.5, (0, 0): ground - 0.3,
             (20, 0): ground + 0.775}

    def live(x: float, z: float) -> float:
        return prior.get((int(round(x)), int(round(z))), 0.0)

    def now(sx: int, sz: int) -> float:
        return ground + prior.get((sx, sz), 0.0)

    block = {"A_phantom": (-20, 0), "B_hidden": (0, 0), "C_own_nudge": (20, 0)}
    arms = {"survivor": {}, "shipped": {"carries_priors": False},
            "no_own_samples": {"own_samples": False}}
    v: dict = {}
    for arm, kw in arms.items():
        r = _junction.rim_transfer(
            taken, now, None, top=10_000,
            height_after=lambda sx, sz, kw=kw: rim_height_after(
                sx, sz, ground, comps, live, **kw))
        per = {b: 0 for b in block}
        for row in r["caused_rows"]:
            t = tuple(row["taken_xz"])
            for b, c in block.items():
                if t == c:
                    per[b] += 1
        assert r["taken_samples"] == len(taken), (arm, r["taken_samples"])
        assert r["rim_pairs_judged"] > 0, (arm, r)
        assert sum(per.values()) == r["caused"], (arm, per, r["caused"])
        v[arm] = {"caused": r["caused"], "worst_m": r["worst_caused_m"],
                  "by_block": per, "pairs": r["rim_pairs_judged"]}
    s, sh, mo = v["survivor"], v["shipped"], v["no_own_samples"]
    assert s["by_block"] == {"A_phantom": 0, "B_hidden": 8,
                             "C_own_nudge": 1}, v
    assert sh["by_block"]["A_phantom"] > 0, v            # the phantom returns
    assert sh["by_block"]["B_hidden"] == 0, v            # the step hides
    assert mo["by_block"]["C_own_nudge"] == 0, v         # the nudge hides
    assert mo["by_block"]["A_phantom"] == 0 and \
        mo["by_block"]["B_hidden"] == 8, v
    if verbose:
        for arm in arms:
            print(f"  {arm:15s} {v[arm]['caused']:2d} caused over "
                  f"{v[arm]['pairs']} pairs, worst {v[arm]['worst_m']} m, "
                  f"by block {v[arm]['by_block']}")
        print("ribbon rim-after-surface selftest OK")
    return v


# T7's STEP AT (403,-290), IN MINIATURE.  A carriageway at 36.0 cut 0.39 m
# into ground that rises 0.56 m per metre off its edge onto a 38.0 m bench;
# another road's carriageway stands on that bench at 38.2, 22-26 m out.
# This ray's taper closes at lat 5.8, sixteen metres short of it.
_APRON_ROAD_Y, _APRON_NODES = 36.0, [(-30.0, 0.0), (30.0, 0.0)]
_APRON_THEIR_Y, _APRON_THEIR_LAT = 38.2, (22.0, 26.0)
# ...AND THE CORRIDOR THE APRON EXISTS FOR: a 2 m cut under a flat 38.0 m
# plain with the other carriageway 6-10 m out, where this taper is still
# open (it would close at lat 6.56) when it reaches the other road.
_TIGHT_CUT_G, _TIGHT_THEIR_Y, _TIGHT_THEIR_LAT = 38.0, 37.5, (6.0, 10.0)


def _apron_ground(wx: float, wz: float) -> float:
    return min(36.39 + 0.56 * max(abs(wz) - 4.0, 0.0), 38.0)


def selftest_interstice_trigger(verbose: bool = True) -> dict:
    """ONE CORRIDOR OR TWO ROADS, and the key that decides it.

    Two fixtures, each the known-bad state of one dependency.

      the WIDE pair is T7's (403,-290): with the claim searched out to the
        cap -- the shipped rule -- the ray is handed to the apron.  Under the
        flush apron that left T7's cut face (0.95 m, REFUSED); under the
        taper apron (INTERSTICE_APRON_DEPENDS_ON) the rim gate is CLEAR but
        the apron cuts a ramp all the way to the other road: MEASURED 1081
        samples written 8-21 m out, past where this ray's own taper met
        ground at lat 5.8.  The verdict is the ground written past the toe,
        plus `junction.rim_transfer`'s over the finished surface.
      the TIGHT pair is T12 beside T3: the taper is still open where the
        other carriageway starts, and without the claim at all the batter
        writes INTO that road.  The verdict is the number of samples this
        write authors on the other road's ground.
    """
    assert INTERSTICE_DEPENDS_ON == ("another_road's_claim_on_this_radial",
                                     "this_ray's_taper_still_open_there")

    def claim(their_y, band, ground):
        def at(x: float, z: float):
            if band[0] <= z <= band[1]:
                return (their_y - ground(x, z), "other_road#1")
            return None
        return at

    tight_ground = lambda wx, wz: _TIGHT_CUT_G                # noqa: E731
    wide = claim(_APRON_THEIR_Y, _APRON_THEIR_LAT, _apron_ground)
    tight = claim(_TIGHT_THEIR_Y, _TIGHT_THEIR_LAT, tight_ground)
    arms = {"survivor": {},
            "no_open_taper": {"interstice_needs_open_taper": False},
            "no_claim": {"road_claim_at": None}}
    v: dict = {}
    for arm, kw in arms.items():
        # THE TRIGGER IS UNDER TEST; the rim closure would repair the wide
        # arm's step whichever rule chose the apron (RIM_CLOSURE_DEPENDS_ON).
        kw_w = {"road_claim_at": wide, "rim_closure_on": False, **kw}
        kw_t = {"road_claim_at": tight, "rim_closure_on": False, **kw}
        w = _rasterise(_APRON_NODES, _APRON_ROAD_Y, _apron_ground, **kw_w)
        t = _rasterise(_APRON_NODES, _APRON_ROAD_Y, tight_ground, **kw_t)
        on_theirs = sum(1 for x, z in t["written"]
                        if _TIGHT_THEIR_LAT[0] <= z <= _TIGHT_THEIR_LAT[1])
        # GROUND THIS RAY'S TAPER NEVER REACHED: two lattice metres past the
        # lat-5.8 toe, short of the other carriageway.
        past_toe = sum(1 for x, z in w["written"]
                       if 8.0 <= z < _APRON_THEIR_LAT[0])
        v[arm] = {"wide": {**_rim_gate_over(w, _apron_ground),
                           **{k: w[k] for k in ("interstice_apron",
                                                "interstice_taper_closes_first")},
                           "written_past_its_toe": past_toe},
                  "wide_edges": _edge_census(w, _apron_ground),
                  "tight_written_on_their_road": on_theirs,
                  "tight_aprons": t["interstice_apron"],
                  "tight_written": len(t["written"])}
    s, ns, nc = v["survivor"], v["no_open_taper"], v["no_claim"]
    # ---- THE OPEN TAPER: without it the apron owns ground past the toe ----
    assert ns["wide"]["written_past_its_toe"] > 0, v
    assert ns["wide"]["interstice_apron"] > 0, v
    assert s["wide"]["written_past_its_toe"] == 0, v
    assert s["wide"]["verdict"] == "CLEAR", v
    assert s["wide"]["interstice_apron"] == 0, v
    assert s["wide"]["interstice_taper_closes_first"] > 0, v
    assert s["wide_edges"]["rim"] + s["wide_edges"]["interior"] == 0, v
    # ---- THE CLAIM: without it the batter writes the other road -----------
    assert nc["tight_written_on_their_road"] > 0, v
    assert s["tight_written_on_their_road"] == 0, v
    assert s["tight_aprons"] > 0, v
    if verbose:
        for arm in arms:
            print(f"  {arm:14s} wide: rim gate {v[arm]['wide']['verdict']} "
                  f"{v[arm]['wide']['caused']} caused (worst "
                  f"{v[arm]['wide']['worst_caused_m']} m), "
                  f"{v[arm]['wide']['interstice_apron']} apron ray(s), "
                  f"{v[arm]['wide']['written_past_its_toe']} sample(s) "
                  f"written past its toe; tight: "
                  f"{v[arm]['tight_written_on_their_road']} sample(s) written "
                  f"on the other road")
        print("ribbon interstice-trigger selftest OK")
    return v


# J-southcape-wtsouth BESIDE S8-wtsouth-treesth AT (-676,-440), IN
# MINIATURE, every height drive 5's own: THEIR road (S8, already in the live
# surface) on a fill at 64.65 along z = 0, its batter falling onto the 63.56
# plain; MINE (J) at 66.13, 9 m out, over ground that stands at 63.91 under
# its edge -- J's 2.22 m wall.  Both 4 m wide, so the gap between the two
# benches is 3 m: 1.48 m of difference the batter grade absorbs in 1.9 m.
_JS_THEIR_Y, _JS_MY_Y, _JS_SEP = 64.65, 66.13, 9.0
_JS_PLAIN, _JS_UNDER_ME = 63.56, 63.91


def _js_ground(wx: float, wz: float) -> float:
    return _JS_UNDER_ME if wz >= _JS_SEP - 3.5 else _JS_PLAIN


def _js_their_delta(sx: int, sz: int, their_y: float) -> float:
    if abs(sx) > 60:
        return 0.0
    g = _js_ground(float(sx), float(sz))
    run = abs(sz) - 3.0
    if run <= 0.0:
        return their_y - g
    return max(their_y - BATTER_GRADE * run, g) - g


def selftest_interstice_taper(verbose: bool = True) -> dict:
    """INTERSTICE_APRON_DEPENDS_ON, BOTH WAYS, and the plan-time claim
    boundary asked of the same two legs.

    `route.cross_claim_lattice` APPROVES the joint (every cross-leg edge
    inside `grade * (run1 + run2 + lattice)`).  The shipped apron
    (`flush`) levels the gap at J's 66.13, which is 2.57 m over the plain
    against J's 2.22 m wall, so it is clipped `steeper_than_batter` at its
    first sample and `junction.rim_transfer` REFUSES the write -- the plan
    and step 4 disagree, which is drive 5's J.  The survivor (`taper`) lays
    the gap edge to edge and the gate is CLEAR.  A third arm drops their
    road to 62.5 m (3.63 m apart, past what 3 m of batter absorbs): the
    plan refuses the joint AND the writer still refuses the write, so the
    taper did not buy the J rows by making the corridor rule toothless."""
    global _INTERSTICE_APRON_MODE
    assert INTERSTICE_APRON_DEPENDS_ON == ("both_carriageway_edges",
                                           "the_run_between_them")
    sys.path.insert(0, str(HERE))
    import junction as _junction                          # noqa: PLC0415
    mine = [(-30.0, _JS_SEP), (30.0, _JS_SEP)]

    def plan_excess(their_y: float) -> float:
        legs = [{"id": "S", "nodes": [[-60.0, 0.0], [60.0, 0.0]],
                 "is_bridge": [False, False], "width_m": 4.0},
                {"id": "J", "nodes": [list(p) for p in mine],
                 "is_bridge": [False, False], "width_m": 4.0}]
        ys = (their_y, _JS_MY_Y)
        e = routemod.cross_claim_lattice(legs, grade=BATTER_GRADE,
                                         batter_m=BATTER_MAX_M, lattice_m=1.0)
        assert len(e["s1"]), e
        dy = np.abs(np.array([ys[int(a)] for a in e["la"]])
                    - np.array([ys[int(b)] for b in e["lb"]]))
        return round(float((dy - BATTER_GRADE * (e["run1"] + e["run2"]
                                                 + 1.0)).max()), 4)

    def arm(their_y: float) -> dict:
        def live(x: float, z: float) -> float:
            return _js_their_delta(int(round(x)), int(round(z)), their_y)

        def claim(x: float, z: float):
            d = live(x, z)
            return None if abs(d) <= 1e-9 else (d, "road_S#1")
        plans = {"road_S": {"nodes": [[-60.0, 0.0], [60.0, 0.0]],
                            "profile_y": [their_y, their_y],
                            "is_bridge": [False, False], "width_m": 4.0}}
        band = foreign_road_band(claim, plans, lambda sx, sz: _js_ground(
            float(sx), float(sz)))
        st = _rasterise(mine, _JS_MY_Y, _js_ground, width_m=4.0,
                        road_claim_at=claim, foreign_road_at=band,
                        live_delta_at=live)
        comps = st["_comps"]
        taken: dict = {}
        for (zx, zz), comp in comps.items():
            cx0, cz0 = tcdata.zone_centre(zx, zz)
            for i in range(tcdata.SAMPLES):
                if not comp.modified_height[i]:
                    continue
                gy, gx = divmod(i, tcdata.PITCH)
                sx, sz = (int(round(c)) for c in
                          tcdata.sample_world(cx0, cz0, gx, gy))
                g = _js_ground(float(sx), float(sz))
                after = g + delta_at(comps, float(sx), float(sz))
                if abs(after - (g + live(sx, sz))) > \
                        appliedmod.AUTHOR_TOLERANCE_M:
                    taken[(sx, sz)] = after

        def now(sx: int, sz: int) -> float:
            return _js_ground(float(sx), float(sz)) + live(sx, sz)
        r = _junction.rim_transfer(
            taken, now, None, top=10_000,
            height_after=lambda sx, sz: rim_height_after(
                sx, sz, _js_ground(float(sx), float(sz)), comps, live))
        return {"verdict": r["verdict"], "caused": r["caused"],
                "worst_m": r["worst_caused_m"],
                "worst_at": (r["caused_rows"][0]["rim_xz"]
                             if r["caused_rows"] else None),
                "apron": st["interstice_apron"],
                "steeper_than_batter": st["batter_clipped_steeper_than_batter"],
                "on_their_carriageway": sum(1 for (_x, z) in taken
                                            if abs(z) <= 3)}
    v: dict = {"plan_excess_m": plan_excess(_JS_THEIR_Y),
               "plan_excess_far_m": plan_excess(62.5)}
    was = _INTERSTICE_APRON_MODE
    try:
        for name, mode, their_y in (("survivor", "taper", _JS_THEIR_Y),
                                    ("shipped", "flush", _JS_THEIR_Y),
                                    ("too_far", "taper", 62.5)):
            _INTERSTICE_APRON_MODE = mode
            v[name] = arm(their_y)
    finally:
        _INTERSTICE_APRON_MODE = was
    s, sh, far = v["survivor"], v["shipped"], v["too_far"]
    # ---- THE PLAN APPROVES THE JOINT; the writer must lay it -------------
    assert v["plan_excess_m"] <= 0.0, v
    assert s["verdict"] == "CLEAR" and s["caused"] == 0, v
    assert s["apron"] > 0, v
    # ---- THE SHIPPED APRON: the plan-approved leg is refused at write -----
    assert sh["verdict"] == "REFUSED" and sh["caused"] > 0, v
    assert sh["steeper_than_batter"] > 0 and sh["worst_m"] > 2.0, v
    assert sh["worst_at"][1] == int(_JS_SEP - 4), v
    # ---- PAST THE GRADE: both instruments still refuse --------------------
    assert v["plan_excess_far_m"] > 0.0, v
    assert far["verdict"] == "REFUSED", v
    for a in (s, sh, far):
        assert a["on_their_carriageway"] == 0, v
    if verbose:
        print(f"  plan claim boundary: worst excess {v['plan_excess_m']} m "
              f"(approved); at 62.5 m {v['plan_excess_far_m']} m (refused)")
        for name in ("survivor", "shipped", "too_far"):
            a = v[name]
            print(f"  {name:9s} rim gate {a['verdict']} {a['caused']} caused "
                  f"(worst {a['worst_m']} m at {a['worst_at']}), "
                  f"{a['apron']} apron ray(s), "
                  f"{a['steeper_than_batter']} steeper_than_batter")
        print("ribbon interstice-taper selftest OK")
    return v


def selftest_rim_step_owner(verbose: bool = True) -> dict:
    """WHOSE EACH CAUSED STEP IS, over the SHORE fixture's real surface.

    The shore's faces stand where the water gate refused the batter; to make
    the author dependency reachable, the rim samples at x >= 20 are handed
    an author whose delta is nothing -- T7's (419,-274), which S4 had moved
    by 0.0014 m, in miniature.  The rows are `junction.rim_transfer`'s own,
    every one of them, and the refusals are the stamp's own `clip_at`.

      author dropped   -> the claimed rows read as the water's, and the
                          closure that would actually close them is never
                          named.
      refusal dropped  -> every unclaimed row reads EARTHWORK: "the
                          earthwork's own cut face on virgin ground", which
                          is what S13's refusal said over 40 water faces.
      plans dropped    -> T7's residue, restated: this plan's carriageway at
                          34.53 one edge from another plan's at 35.37.  With
                          the plans unread every such row reads CLAIM, whose
                          remedy -- re-emit the owner after this write --
                          lays the same step back.
    """
    assert RIM_STEP_OWNER_DEPENDS_ON == (
        "who_authored_the_rim_ground",
        "what_this_write_was_refused_at_the_rim_sample",
        "whether_the_two_PLANS_step_across_the_edge")
    sys.path.insert(0, str(HERE))
    import junction as _junction                          # noqa: PLC0415
    mine_plan = {"nodes": [[-30.0, 0.0], [30.0, 0.0]],
                 "profile_y": [34.53, 34.53], "is_bridge": [False, False],
                 "width_m": 6.0}
    their_plan = {"nodes": [[-30.0, 5.0], [30.0, 5.0]],
                  "profile_y": [35.37, 35.37], "is_bridge": [False, False],
                  "width_m": 4.0}
    # Rim at z = 4 is on THEIR carriageway (lat 1.0); rim at z = 9 is their
    # batter (lat 4.0 > 3.0).  Every taken sample is on MINE (lat 3.0).
    adj = [{"taken_xz": [x, 3], "rim_xz": [x, 4],
            "rim_authored_by": "road_S4#1"} for x in range(-5, 5)]
    far = [{"taken_xz": [x, 3], "rim_xz": [x, 9],
            "rim_authored_by": "road_S4#1"} for x in range(-5, 5)]

    def plan_step(row):
        a = carriageway_plan_y(mine_plan, *row["taken_xz"])
        b = carriageway_plan_y(their_plan, *row["rim_xz"])
        return None if a is None or b is None else abs(a - b)

    def label_counts(rs, ps):
        c: dict[str, int] = {}
        for r in rs:
            k = rim_step_owner(r, {}, ps, _junction.SLIDE_PER_M)
            c[k] = c.get(k, 0) + 1
        return c
    plans_v = {"survivor": label_counts(adj + far, plan_step),
               "no_plans": label_counts(adj + far, None)}
    assert plans_v["survivor"] == {"plans road_S4#1": len(adj),
                                   "claim road_S4#1": len(far)}, plans_v
    assert plans_v["no_plans"] == {"claim road_S4#1": len(adj) + len(far)}, \
        plans_v
    st = _rasterise(_SHORE_NODES, _SHORE_ROAD_Y, _shore_ground)
    rows = _rim_gate_over(st, _shore_ground,
                          author_now=lambda sx, sz: ("road_other#7"
                                                     if sx >= 20 else None)
                          )["rows"]
    refused = refused_at_of(st)
    arms = {"survivor": (rows, refused),
            "no_author": ([{**r, "rim_authored_by": None} for r in rows],
                          refused),
            "no_refusal": (rows, {})}
    v: dict = {}
    for arm, (rs, rf) in arms.items():
        c: dict[str, int] = {}
        for r in rs:
            k = rim_step_owner(r, rf)
            c[k] = c.get(k, 0) + 1
        v[arm] = c
    n = len(rows)
    claimed = sum(1 for r in rows if r["rim_authored_by"])
    assert n > claimed > 0, (n, claimed)
    assert v["survivor"] == {"claim road_other#7": claimed,
                             "refused water_fill": n - claimed}, v
    assert v["no_author"].get("claim road_other#7", 0) == 0, v
    assert v["no_refusal"].get("earthwork", 0) == n - claimed, v
    v["plans"] = plans_v
    if verbose:
        print(f"  the shore's {n} caused rim step(s), {claimed} on a claimed "
              f"rim: {v['survivor']}")
        print(f"    NEUTER drop the author  -> {v['no_author']}")
        print(f"    NEUTER drop the refusal -> {v['no_refusal']}")
        print(f"  T7's abutting carriageways, {len(adj)} across the edge and "
              f"{len(far)} onto the other road's batter: {plans_v['survivor']}")
        print(f"    NEUTER drop the plans   -> {plans_v['no_plans']}")
        print("ribbon rim-step-owner selftest OK")
    return v


# THE SHORTEST RUN AN APPROACH RAMP MAY USE.  Was the only run: the reasoning
# read "the apply clamp is +/-8 m, so the largest step the road can possibly
# owe a pad is 8 m, and 8 / 0.781 = 10.24 m of run removes it at grade; one
# metre of margin and the ramp can always arrive."
#
# THE PREMISE IS FALSE AND IT WAS MEASURED FALSE IN THE SAME SESSION THAT
# WROTE IT.  The clamp bounds what ONE `terrain_write` may move the ground
# relative to the GENERATED height; it does not bound the step between a
# road's profile and a pad's levelled datum, because the pad spends a clamp
# budget of its own in the opposite direction.  MEASURED on T4 at Stenvik:
# residual step at the pad -10.414 m on a 207.2 m run from (539.8, 814.2),
# terminating on stenvik's levelled 45.57 m datum -- 2.4 m larger than the
# largest step this constant assumes can exist.  With the run capped at
# 11.5 m the ramp grade then saturates at `BATTER_GRADE`, which is the slide
# limit, and T4's 8 m longitudinal gradient came out at 0.8849: NOT WALKABLE.
# A player cannot climb a 10.4 m wall and slides back down the stretch before
# it, so the trunk road into the town ends at a cliff.
#
# So the reach is now DERIVED: the run the step needs at the road's own design
# grade, floored here and ceilinged by `APPROACH_RUN_CAP_M`.  T4's 10.414 m
# at 8 % wants 130 m and it has 207; the second run's 2.770 m over 69.7 m is
# 4 %.
APPROACH_MIN_M = 11.5
# ...AND BOUNDED, because a claim that cannot be met must not re-profile a
# whole segment silently.  This is the 120 m this function's own docstring
# already claimed, plus the margin T4's measured run needs; a step that wants
# more run than this keeps its residual and the residual is REPORTED.
APPROACH_RUN_CAP_M = 240.0


def approach_reach_m(worst_step_m: float, design_grade: float,
                     available_run_m: float) -> float:
    """The run an approach ramp gets: what the step needs at design grade,
    never below `APPROACH_MIN_M`, never above the segment's own length or
    `APPROACH_RUN_CAP_M`.

    Bounded by the AVAILABLE run and not only by a constant, because a ramp
    longer than the segment re-profiles nodes that belong to the other end.
    """
    want = abs(worst_step_m) / max(design_grade, 1e-3)
    return max(APPROACH_MIN_M,
               min(want, APPROACH_RUN_CAP_M, max(available_run_m, 1e-9)))


# HOW MANY REACHES THE SOLVE ASKS ABOUT, and it is a ladder rather than a
# bisection because the quantity being minimised is NOT monotone in the reach.
# MEASURED on T4 across the full ladder: the worst 8 m gradient runs 0.9219,
# 0.7763, 0.6798, 0.6230, 0.5890, 0.5322, 0.4963, 0.4717, 0.4826, 0.4583,
# 0.5021, 0.5093, 0.5144, 0.4847, 0.4686 -- it falls, flattens, and rises
# again.  A bisection on a non-monotone objective finds a local answer and
# reports it as the answer.
APPROACH_LADDER_STEPS = 14
# Two reaches whose worst 8 m gradient differ by less than this are the same
# road as far as the operator is concerned -- 0.02 is 2.6 % of the 0.781 slide
# limit -- so the tie is broken toward the LONGER reach, which is the gentler
# ramp and the smaller earthwork.
APPROACH_G8_TIE = 0.02


def solve_approach(seg: dict, patches: dict, live, pad_keepouts: "PadKeepOut",
                   rasterise) -> tuple[dict, dict, dict, dict]:
    """CHOOSE THE APPROACH REACH BY ASKING THE CLAMP, not by deriving it.

    THE DEFECT THIS EXISTS FOR, MEASURED, AND IT IS `APPROACH_MAX_M`'s DEFECT
    WEARING THE OPPOSITE HAT.  That constant was 11.5 m on a false premise
    about the apply clamp, and the fix replaced it with a run DERIVED from the
    step at the design grade -- `step / design`, bounded by the segment's own
    length and `APPROACH_RUN_CAP_M`.  That derivation is about GRADE and
    LENGTH, and the quantity that actually binds is neither: it is the FILL
    the write is allowed to deliver.

    MEASURED on T4 the first time it rasterised through Stenvik's district:
    the derived reach is 79.9 m (a 6.391 m step at 8 %), the cone that reach
    imposes lifts 175 profile nodes by up to 6.861 m, and five samples at
    (583-584, 998-1000) land 8.01-8.13 m above the generated ground -- past
    `tcdata.CLAMP_M` 8.0, which `TerrainComp::ApplyToHeightmap` silently
    discards.  The write is REFUSED, correctly, and a gentler ramp is the
    cause: a long approach holds the road high far from the claim, so it fills
    a valley 40 m out that a shorter, steeper approach simply walks down.  At
    67.9 m the same segment is inside the clamp; at 18.5 m its worst 8 m
    gradient is 0.4583 against the 0.781 limit.

    So the reach is SOLVED against the instrument that decides it.  The
    alternative is to predict which reaches clamp, and this project has paid
    for that shape three times over: a closed-form shrink radius skipped six
    cylinders the gate itself passes, and `APPROACH_MAX_M` was a number
    justified by a proof about a different quantity.  `rasterise(seg)` must
    return `(comps, st, walk)` for the profile currently on `seg`.

    THE RULE, in order, every term measured by the call:
      1. HARD -- `st["over_clamp"]` must be 0.  This is not a preference: a
         write past the clamp is refused at validation and the road would not
         be where the plan says.
      2. Among those, MINIMISE `walk["max_gradient_8m"]`.  That is the
         operator's reported defect ("slides back down") and one of the two
         acceptance criteria.
      3. Tie inside `APPROACH_G8_TIE` -> the LONGER reach.

    THE STEP AT THE PAD IS RECORDED PER CANDIDATE AND IS NOT THE OBJECTIVE,
    and the measurement is why.  MEASURED on T4 over the whole ladder, the two
    acceptance criteria move in OPPOSITE directions: the worst 8 m gradient
    falls from 0.9219 to 0.4583 as the reach shortens, while the worst step at
    a pad edge RISES from 1.078 m (at 67.9 m of reach) to 1.408 m and beyond.
    No reach on the ladder brings the step under `BATTER_GRADE`, because it is
    set by the DATUM DIFFERENCE between adjacent foundations -- `stenvik-hut-1`
    at 47.95 m and `stenvik-longhouse-2` at 46.20 m, 17.7 m of centreline
    apart -- and a ramp cannot move a foundation.  So the solve optimises the
    criterion it CAN meet, records the floor of the one it cannot, and the
    tie-break toward the longer reach is also the direction that reduces the
    pad step.  Optimising a knob against a quantity it does not control is how
    a repair reports success and changes nothing.

    Returns `(approach, comps, st, solve)`; `seg["profile_y"]` is left holding
    the winning profile.
    """
    base_profile = list(seg["profile_y"])
    design = float(seg.get("grade_limit") or 0.08)

    def attempt(reach_m: float | None):
        seg["profile_y"] = list(base_profile)
        ap = grade_into_foreign(seg, patches, live, pad_keepouts,
                                reach_m=reach_m)
        comps, st, walk = rasterise(seg)
        return ap, comps, st, walk

    # The derived reach is the TOP of the ladder: there is no merit in a ramp
    # gentler than the step needs at design grade.
    ap0, comps0, st0, walk0 = attempt(None)
    if not ap0["claims"]:
        return ap0, comps0, st0, {
            "reaches_tried": 0, "chosen_m": None,
            "why": "no claim on this segment, so there is no approach to solve",
            "tool": "tools/jumpstart/roads/ribbon.py::solve_approach"}
    hi = float(ap0["reach_m"])
    # The BOTTOM is the shortest reach that can still remove the step at the
    # batter's own grade: below it the ramp cannot arrive at all, so the
    # residual is structural rather than chosen.
    lo = max(abs(ap0["worst_step_before_m"]) / BATTER_GRADE, 1.0)
    if lo >= hi:
        rows = [{"reach_m": round(hi, 1), "over_clamp": st0["over_clamp"],
                 "g8": walk0.get("max_gradient_8m"),
                 "ramp_grade": ap0.get("ramp_grade"),
                 "step_after_m": ap0["worst_step_after_m"]}]
        return ap0, comps0, st0, {
            "reaches_tried": 1, "chosen_m": round(hi, 1), "rows": rows,
            "why": ("the derived reach is already the shortest that can remove "
                    "this step at BATTER_GRADE, so there is no ladder"),
            "tool": "tools/jumpstart/roads/ribbon.py::solve_approach"}

    ladder = sorted({round(hi * (lo / hi) ** (i / APPROACH_LADDER_STEPS), 1)
                     for i in range(APPROACH_LADDER_STEPS + 1)}, reverse=True)

    def worst_pad_step(walk: dict):
        """The worst step between the road at a pad boundary and that pad's
        own levelled datum -- the second acceptance criterion, recorded per
        candidate so the trade-off against the gradient is visible."""
        steps = [(g["terminates_at"]["step_road_to_pad_m"],
                  g["terminates_at"]["site_id"])
                 for g in walk.get("gaps", [])
                 if g.get("terminates_at")
                 and g["terminates_at"].get("step_road_to_pad_m") is not None]
        if not steps:
            return None, None, 0
        s, sid = max(steps, key=lambda t: abs(t[0]))
        return s, sid, sum(1 for v, _ in steps if abs(v) > BATTER_GRADE)
    rows = []
    best = None
    for reach in ladder:
        if abs(reach - hi) < 1e-9:
            ap, comps, st, walk = ap0, comps0, st0, walk0
        else:
            ap, comps, st, walk = attempt(reach)
        g8 = float(walk.get("max_gradient_8m") or 0.0)
        row = {"reach_m": reach, "ramp_grade": ap.get("ramp_grade"),
               "nodes_regraded": ap["nodes_regraded"],
               "max_profile_move_m": ap.get("max_profile_move_m"),
               "step_after_m": ap["worst_step_after_m"],
               "over_clamp": st["over_clamp"],
               "max_fill_m": st["max_fill_m"], "max_cut_m": st["max_cut_m"],
               "g8": round(g8, 4), "verdict": walk.get("verdict"),
               "stations_on_road": walk.get("stations_on_road")}
        row["worst_pad_step_m"], row["worst_pad_step_at"], \
            row["pads_over_batter_grade"] = worst_pad_step(walk)
        rows.append(row)
        if st["over_clamp"]:
            continue
        if best is None:
            best = (g8, reach, ap, comps, st, walk)
            continue
        # Rule 2 then rule 3: strictly better gradient wins; a gradient inside
        # the tie band yields to the longer reach.  The ladder descends, so the
        # incumbent is always the longer of any tied pair.
        if g8 < best[0] - APPROACH_G8_TIE:
            best = (g8, reach, ap, comps, st, walk)

    if best is None:
        # EVERY REACH CLAMPS.  The tightest is returned so the caller can
        # report the refusal against the smallest earthwork that produced it,
        # rather than against the largest.
        ap, comps, st, walk = attempt(ladder[-1])
        return ap, comps, st, {
            "reaches_tried": len(rows), "chosen_m": ladder[-1], "rows": rows,
            "feasible": False,
            "why": ("NO reach keeps this segment inside the +/-8 m apply "
                    "clamp; the shortest is returned so the refusal names the "
                    "smallest earthwork that still fails"),
            "tool": "tools/jumpstart/roads/ribbon.py::solve_approach"}

    g8, reach, ap, comps, st, walk = best
    seg["profile_y"] = list(base_profile)
    ap, comps, st, walk = attempt(reach)
    return ap, comps, st, {
        "reaches_tried": len(rows), "chosen_m": reach, "rows": rows,
        "feasible": True,
        "derived_reach_m": round(hi, 1), "shortest_reach_m": round(lo, 1),
        "design_grade": design, "clamp_m": tcdata.CLAMP_M,
        "chosen_g8": round(g8, 4),
        "why": ("the longest reach, inside the gradient tie band, whose "
                "rasterisation the +/-8 m apply clamp accepts -- asked of "
                "`stamp` per candidate, never predicted"),
        "tool": "tools/jumpstart/roads/ribbon.py::solve_approach"}


def grade_into_foreign(seg: dict, patches: dict, live,
                       pad_keepouts: "PadKeepOut",
                       grade: float | None = None,
                       station_step_m: float = 1.0,
                       reach_m: float | None = None) -> dict:
    """RE-PROFILE THE APPROACH SO THE ROAD ARRIVES AT WALKABLE GROUND INSTEAD
    OF ENDING AT A CLIFF.  Returns a report; MUTATES `seg["profile_y"]`.

    THE TWO DEFECTS THIS EXISTS FOR, both measured and both in the record
    before anybody read them:

      1. THE ROAD SIMPLY STOPS AT A PAD.  T4 has 333 unwritten centreline
         metres in four runs (204, 68, 41, 5 m); the 204 m run is the stathub
         pad keep-out, and seq 213's OWN meta already records
         `step_across_m -7.644` at s = 888.3.  A 7.6 m step at the end of a
         trunk road, measured, recorded, and shipped.
      2. A FOREIGN PAD WRITE OWNS CARRIAGEWAY SAMPLES.  MEASURED with
         `applied.py`: 89 of T4's metres sit on samples `SiteFinish` seq 286
         overwrote after `RoadBuild` seq 213, and the applied surface jumps
         45.05 -> 47.11 in one metre mid-road.

    THE RULING IS MAIN'S AND IT IS NOT SYMMETRIC: inside the pad's footprint
    the PAD WINS -- it is a building's foundation and it must stay flat -- and
    the road owns the approach.  So this never restores a road delta inside a
    pad.  It moves the ROAD'S OWN target profile, in the metres OUTSIDE the
    claim, until the two surfaces meet.

    HOW: every station that is not ours -- inside a pad keep-out disc, or
    holding a sample a non-road write owns -- contributes a CONE constraint
    `|y(s) - H| <= grade * |s - s_claim|`, where H is the LIVE APPLIED SURFACE
    there, read off the ledger's own blobs.  The new profile is the planned
    one clamped into the intersection of those cones.  Three properties follow:
    a station already within grade of the claim is untouched (the clamp is
    inactive); the ramp is exactly as long as the step needs and no longer;
    and where two claims at different heights are too close to satisfy both,
    the conflict is REPORTED with its residual step rather than split silently.

    THE HEIGHT IS THE LIVE SURFACE, NOT THE PAD'S DATUM, and that distinction
    is the whole reason the defect survived.  `pad_y` in sites.yaml is what a
    pad was ASKED to be levelled to; what the road has to meet is what the
    ground IS.  For the T4 clobber those differ, because the surface there is
    a pad write's samples unioned over a road write's.
    """
    # THE RAMP GRADE IS THE ROAD'S OWN DESIGN GRADE, NOT THE BATTER'S, and
    # Main's ruling ("grade the last metres into the pad at BATTER_GRADE") is
    # the CEILING rather than the target.  MEASURED why the ceiling is the
    # wrong target: ramping T12's 2.99 m pad step at 0.781 took the
    # longitudinal 1 m gradient from 0.169 to 0.6446 -- within the slide limit
    # but only just -- and because the road TURNS through that ramp, the
    # batter plane beside it warps: points 9 m out along one station's normal
    # project to a station 1-2 m away, so the transverse census read 1.686 m
    # steps that are the ramp's own longitudinal fall leaking sideways.  At
    # the road's 8 % the same step needs 37 m of run, which the road has, and
    # both effects vanish.  The batter's grade stays the ceiling for the case
    # where the run genuinely is not there.
    design = float(seg.get("grade_limit") or 0.08)
    want_grade = grade
    nodes = seg["nodes"]
    prof = list(seg["profile_y"])
    br = seg["is_bridge"]
    # THE SAME ACCUMULATOR `lateral_and_y_grid` uses, CALLED rather than
    # re-typed: this copy carried the comment "the same convention
    # `lateral_and_y_grid` uses", which two copies can only honour by luck.
    # Bridged stations are still skipped by the walk below; what changed is
    # that they now occupy arc, so a station is one place on the polyline.
    arc = node_arcs(nodes, br)
    acc = arc[-1] if arc else 0.0
    # THE RUN A RAMP GETS IS DERIVED FROM THE STEP, so it cannot be decided
    # here: the step is not known until the claims have been measured.  It is
    # computed below, once, beside the grade it determines.

    # ---- the claims, measured station by station -----------------------
    claims: list[dict] = []
    s = 0.0
    k = 0
    while s <= acc + 1e-9:
        while k < len(nodes) - 2 and arc[k + 1] <= s:
            k += 1
        if br[k] or br[k + 1]:
            s += station_step_m
            continue
        span = max(arc[k + 1] - arc[k], 1e-9)
        f = min(max((s - arc[k]) / span, 0.0), 1.0)
        x = nodes[k][0] + (nodes[k + 1][0] - nodes[k][0]) * f
        z = nodes[k][1] + (nodes[k + 1][1] - nodes[k][1]) * f
        y = prof[k] + (prof[k + 1] - prof[k]) * f
        cause = None
        owner = pad_keepouts.contains(x, z)
        if owner is not None:
            # NAMED, because "pad_keepout" alone cannot say WHICH foundation,
            # and a district's name is no longer the answer -- the claim is a
            # per-building rectangle and the report has to say which building.
            cause = "pad_keepout"
        if cause is None and live is not None:
            w = live.foreign_at(x, z)
            if w is not None:
                cause = "foreign_write"
                owner = f"{w['name']}#{w['seq']}"
        if cause is not None:
            surf = (live.surface_at(patches, x, z)
                    if live is not None else None)
            # A CLAIM IS GROUND SOMEBODY HAS ACTUALLY WRITTEN, NOT A CIRCLE ON
            # A MAP, and this is the second time tonight that distinction
            # decided a number.  A pad radius over UNWRITTEN ground is
            # natural hillside: MEASURED on T12, 11 stations inside
            # wt-spawn's stand-off read a live surface 2.991 m ABOVE the
            # planned profile purely because the road CUTS through that hill.
            # Ramping up to meet it re-profiled 56 nodes, turned a 4.58 m fill
            # into a 7.02 m embankment and pushed the transverse wall at the
            # pad boundary from 2.60 m to 5.09 m -- a repair that made the
            # defect worse, in service of meeting a surface nobody had built.
            # So a claim requires a non-zero live delta: somebody's earthwork,
            # not somebody's intention.
            if surf is not None and abs(surf[2]) > 1e-6:
                claims.append({"s": round(s, 1), "xz": [round(x, 1), round(z, 1)],
                               "cause": cause, "owner": owner,
                               "live_y": round(surf[0], 3),
                               "planned_y": round(y, 3),
                               "step_m": round(y - surf[0], 3)})
        s += station_step_m
    # THE RAMP: THE RUN COMES FROM THE STEP, THE GRADE COMES FROM THE RUN.
    #
    # The previous order was the other way round and it is what shipped T4's
    # cliff: the run was a constant 11.5 m, so the grade was whatever a 11.5 m
    # ramp needed, which for a 10.414 m step is 0.905 -- clipped to
    # `BATTER_GRADE`, the slide limit -- and 1.4 m of the step was still left
    # over at the boundary.  A ramp at the slide limit is a ramp the player
    # slides down, and T4's measured 8 m longitudinal gradient came out 0.8849
    # against a 0.781 limit.
    #
    # Now the run is `step / design` (T4: 10.414 / 0.08 = 130 m, and it has
    # 207) bounded by the segment's own length and `APPROACH_RUN_CAP_M`, and
    # the grade is `step / run` -- so it lands ON the design grade whenever
    # the run is there, and only steepens toward the batter ceiling when it is
    # not.  Never gentler than design: there is no merit in a flatter road
    # than was fitted.
    #
    # THE T12 REGRESSION THIS CONSTANT WAS SHRUNK TO AVOID CANNOT RECUR, and
    # the reason is a DIFFERENT fix that landed after it.  That regression was
    # ramping UP to meet ground nobody had built: 11 stations inside
    # wt-spawn's stand-off whose live surface sat 2.991 m above the profile
    # purely because the road CUTS a hill there, which at design grade over
    # 37 m re-profiled 56 nodes, turned a 4.58 m fill into a 7.02 m
    # embankment and pushed the transverse wall 2.60 -> 5.09 m.  A claim now
    # REQUIRES a non-zero live delta -- "somebody's earthwork, not somebody's
    # intention", the test twenty lines above -- so those 11 stations are no
    # longer claims at all and there is no ramp to lengthen.  MEASURED after
    # this change: T12 regrades the same nodes it did before.
    if claims:
        worst = max(abs(c["step_m"]) for c in claims)
        if reach_m is None:
            reach_m = approach_reach_m(worst, design, acc)
        if want_grade is None:
            grade = min(max(worst / reach_m, design), BATTER_GRADE)
        else:
            grade = min(max(want_grade, 1e-3), BATTER_GRADE)
    else:
        if reach_m is None:
            reach_m = APPROACH_MIN_M
        grade = min(max(design if want_grade is None else want_grade, 1e-3),
                    BATTER_GRADE)
    if not claims:
        return {"claims": 0, "nodes_regraded": 0, "ramps": [],
                "worst_step_before_m": 0.0, "worst_step_after_m": 0.0,
                "tool": "tools/jumpstart/roads/ribbon.py::grade_into_foreign"}

    # ---- clamp the profile into every claim's cone ----------------------
    regraded = 0
    moved_max = 0.0
    conflicts: list[dict] = []
    for i in range(len(nodes)):
        if br[i]:
            continue
        si = arc[i]
        lo, hi = -1e30, 1e30
        lo_c = hi_c = None
        for c in claims:
            d = abs(si - c["s"])
            if d > reach_m:
                continue
            h = c["live_y"]
            if h - grade * d > lo:
                lo, lo_c = h - grade * d, c
            if h + grade * d < hi:
                hi, hi_c = h + grade * d, c
        if lo_c is None and hi_c is None:
            continue
        want = prof[i]
        if lo > hi:
            # TWO CLAIMS AT DIFFERENT HEIGHTS TOO CLOSE TOGETHER: no profile
            # satisfies both at grade.  Meet them halfway and record the
            # residual, because a silent split is how a 2 m step gets shipped.
            mid = (lo + hi) / 2.0
            conflicts.append({"node": i, "s": round(si, 1),
                              "between": [lo_c["xz"], hi_c["xz"]],
                              "residual_step_m": round((lo - hi) / 2.0, 3)})
            new = mid
        else:
            new = min(max(want, lo), hi)
        if abs(new - want) > 1e-6:
            prof[i] = new
            regraded += 1
            moved_max = max(moved_max, abs(new - want))
    seg["profile_y"] = prof

    worst_before = max(abs(c["step_m"]) for c in claims)
    # AFTER, on the regraded profile, by the same instrument.
    after = []
    for c in claims:
        si = c["s"]
        kk = 0
        while kk < len(nodes) - 2 and arc[kk + 1] <= si:
            kk += 1
        span = max(arc[kk + 1] - arc[kk], 1e-9)
        f = min(max((si - arc[kk]) / span, 0.0), 1.0)
        y = prof[kk] + (prof[kk + 1] - prof[kk]) * f
        c["regraded_y"] = round(y, 3)
        c["step_after_m"] = round(y - c["live_y"], 3)
        after.append(abs(y - c["live_y"]))
    return {
        "claims": len(claims),
        "claims_by_cause": {k: sum(1 for c in claims if c["cause"] == k)
                            for k in sorted({c["cause"] for c in claims})},
        "owners": sorted({c["owner"] for c in claims if c["owner"]}),
        "nodes_regraded": regraded,
        "max_profile_move_m": round(moved_max, 3),
        "worst_step_before_m": round(worst_before, 3),
        "worst_step_after_m": round(max(after) if after else 0.0, 3),
        "conflicts": conflicts,
        "ramp_grade": round(grade, 4),
        "ramp_grade_is": ("the segment's own design grade"
                          if abs(grade - design) < 1e-9
                          else "steeper than design, capped at BATTER_GRADE"),
        "design_grade": design,
        "batter_grade_ceiling": round(BATTER_GRADE, 4),
        "reach_m": round(reach_m, 1),
        "claims_detail": claims[:80],
        "method": ("MEASURED: the live applied surface from the ledger's own "
                   "blobs (union per zone in file order + generated, blended "
                   "bilinearly as Heightmap renders it) at every 1 m station "
                   "the road does not own, then the planned profile clamped "
                   "into each claim's +/-BATTER_GRADE cone. The pad keeps "
                   "every sample it owns; only the ROAD's target height "
                   "moves."),
        "tool": "tools/jumpstart/roads/ribbon.py::grade_into_foreign",
    }


def edge_step_census(seg: dict, comps: dict, patches: dict,
                     station_step_m: float = 2.0, lat_max_m: float = 24.0,
                     lat_step_m: float = 1.0,
                     batter_m: float = BATTER_MAX_M,
                     grade: float = BATTER_GRADE,
                     mine: set | None = None,
                     clips: list | None = None) -> dict:
    """THE NUMBER THE OPERATOR'S "BLOCKY" COMPLAINT IS ABOUT: the worst 1 m
    TRANSVERSE step on the applied surface, per station, and where it falls.

    `walkability` walks ALONG the centreline and answers "is this hill too
    steep to climb".  It cannot see a wall beside the road, and the wall beside
    the road is what a level bench on a side slope has: measured over the 15
    standing segments before the batter existed, the step one metre outside the
    shoulder ran 0.39-1.46 m median and up to 8.06 m on 42-89 % of each
    segment.  A check that walks the centreline reported every one of those
    segments walkable, which is true and was not the question.

    THE VERDICT IS TAKEN AGAINST `BATTER_GRADE`, NOT AGAINST A FLAT 0.5 m, and
    the flat number is the trap.  A correctly battered segment's 1 m
    transverse step IS the design grade -- 0.781 m per metre at 38 deg -- so a
    0.5 m threshold fails every segment that was repaired properly and sends
    the next reader chasing a non-defect.  MEASURED on T12 after the first
    batter: p50 0.83 m, which a 0.5 m checker calls a wall and which is in
    fact the slope, plus the hillside the old delta-ramp form added on top of
    it (see BATTER_GRADE).

    AND THE VERDICT SET IS THE GROUND THIS WRITE TOUCHED, not a fixed lateral
    band.  Sampling out to `lat_max_m` is deliberate -- it shows where the
    ground goes -- but a natural cliff at lat 21 is not a wall the road left,
    and a band-based rule reported exactly that on T12 before this.  So every
    adjacent PAIR is classified by whether either of its samples has a
    MODIFIED corner under it (`applied_at` returns that count), which is
    precisely "is this step on ground we moved".  Three disjoint sets come
    out: the carriageway, the EARTHWORK (the verdict), and natural ground
    (reported, never judged).
    """
    nodes = np.array(seg["nodes"], dtype=np.float64)
    br = np.array(seg["is_bridge"], dtype=bool)
    half = seg["width_m"] / 2.0
    edge = half + SHOULDER_M

    # EVERY RECORDED CLIP, ON A 2 m GRID, so an over-grade step can be asked
    # "did this write already record a reason for you".  2 m because the census
    # samples the cross section at 1 m and a clip sits on the lattice: one cell
    # either way covers the bilinear footprint of the step being judged.
    clipgrid: dict[tuple[int, int], list] = {}
    for cx_, cz_, cause_, h_ in (clips or ()):
        clipgrid.setdefault((int(cx_ // 2), int(cz_ // 2)), []).append(
            (cx_, cz_, cause_, h_))

    def clip_near(x: float, z: float, r: float = 2.5):
        best = None
        for gx_ in (int(x // 2) - 1, int(x // 2), int(x // 2) + 1):
            for gz_ in (int(z // 2) - 1, int(z // 2), int(z // 2) + 1):
                for cx_, cz_, cause_, h_ in clipgrid.get((gx_, gz_), ()):
                    d = math.hypot(x - cx_, z - cz_)
                    if d <= r and (best is None or d < best[0]):
                        best = (d, cause_, h_)
        return None if best is None else {"cause": best[1],
                                          "recorded_wall_m": best[2],
                                          "dist_m": round(best[0], 2)}

    # TOLERANCE, AND WHY IT IS NOT ZERO: the cross section is sampled at a 1 m
    # pitch along a direction that is NOT the lattice axis, so each reading is
    # a bilinear blend of four samples and a step measured across a diagonal
    # picks up the LONGITUDINAL grade as well.  On an 8 % road that is at most
    # 0.08 m of the 0.781, so the allowance is the road's own design grade
    # rather than an invented number.
    tol = float(seg.get("grade_limit") or 0.12)
    limit = grade + tol

    lats = np.arange(-lat_max_m, lat_max_m + 1e-9, lat_step_m)
    worst_road = {"step_m": 0.0}
    worst_toe = {"step_m": 0.0}
    worst_work = {"step_m": 0.0}
    worst_junction = {"step_m": 0.0}
    toe_steps: list[float] = []
    road_steps: list[float] = []
    work_steps: list[float] = []
    nat_steps: list[float] = []
    junction_steps: list[float] = []
    junctions: list[dict] = []
    over_grade: list[dict] = []
    explained = 0
    unexplained = 0
    stations = 0
    uncovered = 0

    acc = 0.0
    for k in range(len(nodes) - 1):
        if br[k] or br[k + 1]:
            continue
        ax, az = nodes[k]
        bx, bz = nodes[k + 1]
        seglen = math.hypot(bx - ax, bz - az)
        if seglen <= 1e-9:
            continue
        t = station_step_m - acc
        while t < seglen:
            f = t / seglen
            cx, cz = ax + (bx - ax) * f, az + (bz - az) * f
            nx, nz = -(bz - az) / seglen, (bx - ax) / seglen
            ys = []
            mods = []
            owns = []
            for lat in lats:
                got = applied_at(comps, patches, cx + nx * lat, cz + nz * lat,
                                 mine)
                ys.append(None if got is None else got[0])
                mods.append(0 if got is None else got[2])
                owns.append(0 if got is None else got[3])
            if any(v is None for v in ys):
                uncovered += 1
            else:
                stations += 1
                arr = np.asarray(ys, dtype=np.float64)
                d = np.abs(np.diff(arr))
                md = np.asarray(mods, dtype=np.int32)
                mn = np.asarray(owns, dtype=np.int32)
                mid = (lats[:-1] + lats[1:]) / 2.0
                on_road = np.abs(mid) <= edge + EDGE_EPS_M
                touched = (md[:-1] > 0) | (md[1:] > 0)
                # A step is MINE when a sample this write wrote is on one
                # side of it.  It is a JUNCTION when the only written samples
                # involved belong to someone else -- a neighbouring segment's
                # carriageway or a settlement pad carried in by the union.
                # Grading that is the neighbour's re-emit, not this one's, and
                # judging this segment on it fails a correct segment.
                ownpair = (mn[:-1] > 0) | (mn[1:] > 0)
                # A pair straddling MY sample and SOMEBODY ELSE'S is a
                # junction wherever it sits, carriageway included.  MEASURED
                # on T12 at (75.2, -65.2): a 2.602 m step at lat 3.5, between
                # a sample this write wrote and one it refuses because the
                # site's pad radius GREW since the first build (sites.yaml's
                # 100 m town against spec.py's provisional 12 m).  The old
                # road's samples inside the new keep-out are the pad's ground
                # now, by Main's ruling, and the road may not take them back.
                mixed = ((mn[:-1] > 0) & (md[1:] > mn[1:])) | \
                        ((mn[1:] > 0) & (md[:-1] > mn[:-1]))
                work = ownpair & ~on_road & ~mixed
                junction = (touched & ~ownpair & ~on_road) | mixed
                nat = ~touched & ~on_road
                toe = edge + batter_m + 1.0
                outside = (np.abs(mid) > edge) & (np.abs(mid) <= toe)
                on_road = on_road & ~mixed
                if on_road.any():
                    j = int(np.argmax(np.where(on_road, d, -1.0)))
                    road_steps.append(float(d[j]))
                    if d[j] > worst_road["step_m"]:
                        worst_road = {"step_m": round(float(d[j]), 3),
                                      "xz": [round(cx, 1), round(cz, 1)],
                                      "lat_m": round(float(mid[j]), 1)}
                if outside.any():
                    j = int(np.argmax(np.where(outside, d, -1.0)))
                    toe_steps.append(float(d[j]))
                    if d[j] > worst_toe["step_m"]:
                        worst_toe = {"step_m": round(float(d[j]), 3),
                                     "xz": [round(cx, 1), round(cz, 1)],
                                     "lat_m": round(float(mid[j]), 1)}
                if work.any():
                    j = int(np.argmax(np.where(work, d, -1.0)))
                    work_steps += [float(v) for v in d[work]]
                    if d[j] > worst_work["step_m"]:
                        worst_work = {"step_m": round(float(d[j]), 3),
                                      "xz": [round(cx, 1), round(cz, 1)],
                                      "lat_m": round(float(mid[j]), 1)}
                    for jj in np.flatnonzero(work & (d > limit)):
                        jj = int(jj)
                        px_ = cx + nx * float(mid[jj])
                        pz_ = cz + nz * float(mid[jj])
                        why = clip_near(px_, pz_)
                        if why is None:
                            unexplained += 1
                        else:
                            explained += 1
                        if len(over_grade) < 60:
                            over_grade.append(
                                {"step_m": round(float(d[jj]), 3),
                                 "xz": [round(cx, 1), round(cz, 1)],
                                 "lat_m": round(float(mid[jj]), 1),
                                 # WHERE THE STEP IS, not where the station
                                 # is.  `xz` is the station centre and `lat_m`
                                 # is measured along THAT station's normal, so
                                 # on a ribbon that doubles back two steps 19 m
                                 # apart across the road share an `xz` -- and
                                 # an adjacency test clustering on `xz` then
                                 # reads a bend's cross section as a cliff.
                                 # MEASURED on T4: 41 unexplained steps
                                 # clustered to a run of 8 on station centres
                                 # and to runs of at most 2 on these.
                                 "step_xz": [round(px_, 1), round(pz_, 1)],
                                 # AND THE TWO SAMPLES IT IS BETWEEN, so a
                                 # consumer can re-measure the same step on
                                 # another surface -- the GENERATED one -- at
                                 # exactly these points.  Reconstructing them
                                 # from the station and the lat requires the
                                 # normal, which is not in the record, and
                                 # guessing the steepest direction instead
                                 # overstates the natural step and excuses a
                                 # wall this write made.
                                 "step_between": [
                                     [round(cx + nx * float(lats[jj]), 2),
                                      round(cz + nz * float(lats[jj]), 2)],
                                     [round(cx + nx * float(lats[jj + 1]), 2),
                                      round(cz + nz * float(lats[jj + 1]), 2)]],
                                 "recorded_clip": why})
                if nat.any():
                    nat_steps += [float(v) for v in d[nat]]
                if junction.any():
                    j = int(np.argmax(np.where(junction, d, -1.0)))
                    junction_steps += [float(v) for v in d[junction]]
                    if d[j] > worst_junction["step_m"]:
                        worst_junction = {"step_m": round(float(d[j]), 3),
                                          "xz": [round(cx, 1), round(cz, 1)],
                                          "lat_m": round(float(mid[j]), 1)}
                    if d[j] > grade and len(junctions) < 40:
                        junctions.append(
                            {"step_m": round(float(d[j]), 3),
                             "xz": [round(cx, 1), round(cz, 1)],
                             "lat_m": round(float(mid[j]), 1)})
            t += station_step_m
        acc = seglen - (t - station_step_m)

    def pct(vals, q):
        return round(float(np.percentile(vals, q)), 3) if vals else None

    # TOLERANCE, AND WHY IT IS NOT ZERO (restated where it is used): the cross section is sampled at a 1 m
    # pitch along a direction that is NOT the lattice axis, so each reading is
    # a bilinear blend of four samples and a step measured across a diagonal
    # picks up the LONGITUDINAL grade as well.  On an 8 % road that is at most
    # 0.08 m of the 0.781, so the allowance is stated as the road's own design
    # grade rather than invented.
    tol = float(seg.get("grade_limit") or 0.12)
    limit = grade + tol
    return {
        "stations": stations,
        "stations_without_generated_cover": uncovered,
        "lat_span_m": [float(-lat_max_m), float(lat_max_m)],
        "verdict_band": ("every adjacent 1 m pair outside the carriageway "
                         "with a sample THIS WRITE wrote under it; pairs "
                         "whose only written sample belongs to another claim "
                         "are reported as junction_* and are that claim's to "
                         "grade"),
        "carriageway_step_p50": pct(road_steps, 50),
        "carriageway_step_p95": pct(road_steps, 95),
        "carriageway_step_max": worst_road,
        "earthwork_step_p50": pct(work_steps, 50),
        "earthwork_step_p95": pct(work_steps, 95),
        "earthwork_step_max": worst_work,
        "earthwork_pairs": len(work_steps),
        "earthwork_over_grade_frac": (
            round(float(np.mean(np.asarray(work_steps) > limit)), 4)
            if work_steps else None),
        "earthwork_over_grade_at": over_grade,
        "natural_ground_step_p50": pct(nat_steps, 50),
        "natural_ground_step_max": (round(max(nat_steps), 3)
                                    if nat_steps else None),
        "junction_step_p50": pct(junction_steps, 50),
        "junction_step_max": worst_junction,
        "junction_pairs": len(junction_steps),
        "junction_over_grade_at": junctions,
        "outside_edge_step_p50": pct(toe_steps, 50),
        "outside_edge_step_p95": pct(toe_steps, 95),
        "outside_edge_step_max": worst_toe,
        "outside_edge_over_0_5m_frac": (
            round(float(np.mean(np.asarray(toe_steps) > 0.5)), 3)
            if toe_steps else None),
        "outside_edge_over_1_0m_frac": (
            round(float(np.mean(np.asarray(toe_steps) > 1.0)), 3)
            if toe_steps else None),
        "batter_grade_limit": round(grade, 4),
        "diagonal_tolerance_m": round(tol, 4),
        "verdict_limit_m": round(limit, 4),
        "earthwork_over_grade_explained_by_a_recorded_clip": explained,
        "earthwork_over_grade_UNEXPLAINED": unexplained,
        "verdict": ("no_earthwork" if not work_steps
                    else "pass" if worst_work["step_m"] <= limit
                    else "pass_with_recorded_clips" if unexplained == 0
                    else "UNEXPLAINED_STEPS"),
        "method": ("MEASURED: the applied surface (generated bilinear + "
                   "delta_at, i.e. how Heightmap composes the mesh) sampled "
                   "across the ribbon at 1 m lateral pitch every 2 m of "
                   "centreline, reporting the largest adjacent-sample "
                   "difference inside the carriageway, on the ground this "
                   "write MODIFIED, and on untouched natural ground, "
                   "separately. The verdict is the earthwork set against "
                   "BATTER_GRADE plus the segment's own longitudinal grade "
                   "as a diagonal allowance -- NOT a flat 0.5 m, which fails "
                   "a correctly battered segment. This is the transverse "
                   "quantity; walkability's gradients are the longitudinal "
                   "one and cannot see a wall beside the road."),
        "tool": "tools/jumpstart/roads/ribbon.py::edge_step_census",
    }


def applied_at(comps: dict, patches: dict, x: float, z: float,
               mine: set | None = None):
    """The height the game will RENDER at (x, z), and how much of it is road.

    Returns `(applied_y, generated_y, corners_modified, corners_mine)` or None
    when the generated lattice around the point is not in hand.  This is
    `generated bilinear + delta_at`, which is exactly `Heightmap`'s own
    composition: the mesh interpolates linearly between 1 m samples and an
    unwritten sample contributes zero delta.  Reading the PROFILE instead would
    answer what the plan intended rather than what the player stands on -- and
    the difference IS the edge, the pad gap and the ribbon end.

    `corners_mine` EXISTS TO STOP THIS SEGMENT ANSWERING FOR ANOTHER
    SEGMENT'S WALL, and it was a measured misattribution.  After the union a
    compiler holds every neighbour's samples too, so the cross section at T12
    s = 116 m reads a 3.676 m wall at lat -6.5 -- which is T3's carriageway
    edge 1.18 m off T3's OWN centreline, running 6-10 m from T12 near the
    temple.  Judging T12's batter on it would fail a correct segment and hide
    the actual owner.  Pass the `{(zone, index)}` set THIS write wrote and
    every step can be attributed.
    """
    x0, z0 = math.floor(x), math.floor(z)
    tx, tz = x - x0, z - z0
    gen = 0.0
    modified = 0
    own = 0
    for dx, dz, w in ((0, 0, (1 - tx) * (1 - tz)), (1, 0, tx * (1 - tz)),
                      (0, 1, (1 - tx) * tz), (1, 1, tx * tz)):
        sx, sz = x0 + dx, z0 + dz
        zx, zz = tcdata.zone_of(sx, sz)
        patch = patches.get(f"z_{zx}_{zz}")
        if patch is None:
            return None
        cx, cz = tcdata.zone_centre(zx, zz)
        gx, gy = tcdata.vertex_mask_index(cx, cz, sx, sz)
        if not (0 <= gx < tcdata.PITCH and 0 <= gy < tcdata.PITCH):
            return None
        gen += w * float(patch.at(gy, gx))
        comp = comps.get((zx, zz))
        k = gy * tcdata.PITCH + gx
        if comp is not None and comp.modified_height[k]:
            modified += 1
            if mine is None or ((zx, zz), k) in mine:
                own += 1
    return gen + delta_at(comps, x, z), gen, modified, own


def pad_standing_datum(pad_keepouts: "PadKeepOut", site_id: str, comps: dict,
                       patches: dict) -> tuple[float | None, int]:
    """THE PAD'S OWN FLOOR, READ - the median APPLIED surface over the
    lattice samples that pad actually levelled.  `(y, samples_read)`.

    THE DEFECT CLASS, and it is the third face of the district defect.
    `settlements/sites.yaml` publishes Vestvik's `pad_y` as 33.99; the
    u-vestvik portal_wood stands at (-915.0, 35.29, 1883.4) on
    vestvik-hall-1's levelled pad and the applied ground under it reads
    35.2900 against a generated 36.6006.  The DECLARATION IS 1.30 m LOW.
    A pad's own `terrain_write` `target_y` is better but is the same KIND
    of thing: it is what that write ASKED the ground to become, before the
    engine blended it with its neighbours, before a later claim re-authored
    part of it, and before anything was built on it.  A road profiled to
    either lands on ground nobody stands on, and every check that compared
    the road against the same declaration agreed with it - which is exactly
    how eleven termini on this world passed.

    So the datum is READ, and three things about HOW are load-bearing:

      * ON THE LATTICE.  Every reading is taken at an integer world sample,
        so `applied_at`'s bilinear blend collapses onto that one vertex.  An
        off-lattice reading at a pad CENTRE blends four corners and one of
        them may belong to a road.
      * OVER THE PAD'S OWN SAMPLES, not its centre.  `stenvik-hall-1` has
        666 of its 676 rectangle samples currently authored by somebody
        else; a single centre reading is a reading of whatever is on top of
        that square metre today, not of the pad's floor.
      * BY MEDIAN.  A pad feathers at its edge and carries stairs, ramps
        and a cut face; the median is the height the pad IS, and it does not
        move when a neighbour re-authors a fringe.

    A pad that levelled nothing has no floor to read and returns
    `(None, 0)`: the caller then quotes the declaration AND SAYS SO, rather
    than passing a plan off as a reading.
    """
    ys: list[float] = []
    for (sx, sz), sid in pad_keepouts.levelled.items():
        if sid != site_id:
            continue
        got = applied_at(comps, patches, float(sx), float(sz))
        if got is not None:
            ys.append(got[0])
    if not ys:
        return None, 0
    ys.sort()
    n = len(ys)
    y = ys[n // 2] if n % 2 else (ys[n // 2 - 1] + ys[n // 2]) / 2.0
    return y, n


def walkability(seg: dict, comps: dict, patches: dict,
                pad_keepouts: "PadKeepOut",
                protected_pieces: list[tuple[float, float]] | None = None,
                protected_discs: list[tuple[float, float, float]] | None = None,
                road_clips: list[tuple] | None = None,
                step_m: float = 0.5) -> dict:
    """Walk the centreline on the APPLIED surface and report the numbers the
    operator's own test produces: is the ribbon CONTINUOUS, is every part of it
    WALKABLE, and does it MEET what it terminates at.

    Continuity is measured as the set of RUNS where the centreline is not on
    written road, each classified by cause, with the height STEP across it
    AND the height step a walker meets going INTO it.  A bridged run is
    expected -- the deck is Crossings' and the ribbon stops at the bank by
    construction.  A pad run is expected too: the ribbon stops at a
    Settlements pad edge rather than levelling ground under a building.
    Anything else is a hole in the road.

    BUT "EXPECTED" IS NOT "WALKABLE", AND THAT CONFLATION WAS A DEFECT.  The
    cause says whose work the gap is; it says nothing about whether the
    operator can cross it.  Every non-bridge gap is therefore judged against
    the slide limit on the applied surface, and a gap he cannot climb out of
    makes the segment NOT WALKABLE however well explained it is.

    A GAP IS NOT A SLOPE AND MUST NOT BE AVERAGED INTO ONE.  The verdict
    gradient is taken over 8 m between stations whose whole run is road,
    because that is the question "is this hill too steep to walk up".  At a
    boundary the question is different -- "can I get from the pad onto the
    road" -- and the answer is a STEP over one lattice edge, which an 8 m
    average would divide by eight and hide.  So each boundary is reported as a
    step, in metres, against the MEASURED pad datum of the site it terminates
    at (`pad_y` from settlements/sites.yaml, the height Settlements levels that
    pad to) rather than against the generated ground that is there today.  The
    generated ground is a temporary answer; the datum is the one the junction
    will actually have.
    """
    nodes = np.array(seg["nodes"], dtype=np.float64)
    br = np.array(seg["is_bridge"], dtype=bool)

    # Densify the centreline by arclength, carrying each station's cause tags.
    stations: list[dict] = []
    s = 0.0
    for k in range(len(nodes) - 1):
        ax, az = nodes[k]
        bx, bz = nodes[k + 1]
        seglen = math.hypot(bx - ax, bz - az)
        if seglen <= 1e-9:
            continue
        n = max(1, int(math.ceil(seglen / step_m)))
        bridged = bool(br[k] or br[k + 1])
        for j in range(n):
            t = j / n
            x, z = ax + (bx - ax) * t, az + (bz - az) * t
            stations.append({"s": s + seglen * t, "x": x, "z": z,
                             "bridged": bridged})
        s += seglen
    ax, az = nodes[-1]
    stations.append({"s": s, "x": float(ax), "z": float(az),
                     "bridged": bool(br[-1])})

    # THE RASTERISER'S OWN REFUSALS, indexed by the lattice sample they were
    # taken at.  `stamp` records one entry per carriageway sample it could not
    # write, as `(wx, wz, cause, full)`; the samples are integers, so an exact
    # dict keyed on them needs no radius and no tolerance.
    refusal_index: dict[tuple[int, int], dict] = {}
    for wx, wz, cause, full in (road_clips or ()):
        refusal_index[(int(round(wx)), int(round(wz)))] = {
            "cause": cause, "full_m": full,
            "at": [round(wx, 1), round(wz, 1)]}
    off_lattice = 0
    for st in stations:
        got = applied_at(comps, patches, st["x"], st["z"])
        if got is None:
            st["y"] = None
            off_lattice += 1
        else:
            st["y"], st["gen"], st["mod"], st["mine"] = got
        # PAD ADJACENCY IS DECIDED BY THE SAMPLES, NOT BY THE STATION CENTRE.
        # `stamp` skips a SAMPLE whose world position is inside a pad disc, and
        # a station whose centre sits just outside the disc can still have one
        # of its four corner samples inside it -- so the station is not on
        # written road for a reason that IS the pad.  MEASURED on
        # T10-wtspawn-wtsouth: one such station at (520.0, 36.8), 1 m outside
        # wt-south's 16.1 m radius, was classified a HOLE and refused the whole
        # segment. Testing the corners is exact and needs no margin constant.
        x0, z0 = math.floor(st["x"]), math.floor(st["z"])
        st["in_pad"] = any(
            pad_keepouts.contains(x0 + dx, z0 + dz) is not None
            for dx in (0, 1) for dz in (0, 1))
        # The same reasoning for the other two things that stop the rasteriser:
        # a station is not on road BECAUSE of a protected piece, or BECAUSE the
        # ground there is seabed, and both are explanations rather than holes.
        st["near_protected"] = any(
            abs(x0 + dx - px) <= PROTECT_CLEAR_M
            and abs(z0 + dz - pz) <= PROTECT_CLEAR_M
            for dx in (0, 1) for dz in (0, 1)
            for px, pz in (protected_pieces or ()))
        st["underwater"] = (
            st.get("gen") is not None and st["gen"] < WATER_LEVEL_M
            and any(math.hypot(st["x"] - px, st["z"] - pz) <= pr
                    for px, pz, pr in (protected_discs or ())))
        # WHAT THE RASTERISER ITSELF SAID, and it is the authority here.  The
        # three tests above ask circles and piece lists whether a station
        # OUGHT to have been refused; this asks `stamp` what it ACTUALLY
        # refused and why, keyed on the station's own four lattice samples.
        # The difference is not academic: a site pad's WRITTEN footprint is
        # not its nominal `pad_radius_m`, so on T4 the 47 m of centreline that
        # `tree-sth-2` seq 669 owns is outside every pad circle and inside
        # somebody's foundation. Modelling the gate got that wrong in the
        # direction that refuses a whole segment.
        st["refused"] = None
        if road_clips:
            for cx0 in (x0, x0 + 1):
                for cz0 in (z0, z0 + 1):
                    hit = refusal_index.get((cx0, cz0))
                    if hit is not None:
                        st["refused"] = hit
                        break
                if st["refused"]:
                    break
        # ON ROAD means all four samples under the point are ones this write
        # set: that is where the rendered surface IS the fitted profile rather
        # than a blend of road and untouched ground.
        st["on_road"] = st["y"] is not None and st.get("mod") == 4

    def grade_over(baseline: float) -> dict:
        """Worst |rise/run| between two ON-ROAD stations `baseline` apart."""
        worst = {"gradient": 0.0, "at": None, "rise_m": 0.0, "run_m": baseline,
                 "pairs": 0}
        j = 0
        for i, a in enumerate(stations):
            if not a["on_road"]:
                continue
            if j < i:
                j = i
            while j + 1 < len(stations) and stations[j]["s"] - a["s"] < baseline:
                j += 1
            b = stations[j]
            run = b["s"] - a["s"]
            if run < baseline * 0.9 or not b["on_road"]:
                continue
            # A pair that straddles a gap is not a slope, it is a step, and it
            # is reported as a step below.  Require the whole run to be road.
            if any(not stations[k]["on_road"] for k in range(i, j + 1)):
                continue
            worst["pairs"] += 1
            g = abs(b["y"] - a["y"]) / run
            if g > worst["gradient"]:
                worst.update({"gradient": round(g, 4),
                              "rise_m": round(b["y"] - a["y"], 3),
                              "run_m": round(run, 2),
                              "at": [round(a["x"], 1), round(a["z"], 1)]})
        return worst

    g8 = grade_over(VERDICT_BASELINE_M)
    g1 = grade_over(1.0)

    # Runs of centreline that are NOT on written road.
    gaps = []
    # One READING per site, not per gap: six of T4's gaps terminate on the
    # same town and the median is over hundreds of samples.
    pad_floor: dict[str, tuple] = {}
    i = 0
    while i < len(stations):
        if stations[i]["on_road"]:
            i += 1
            continue
        j = i
        while j + 1 < len(stations) and not stations[j + 1]["on_road"]:
            j += 1
        before = stations[i - 1] if i > 0 and stations[i - 1]["on_road"] else None
        after = (stations[j + 1]
                 if j + 1 < len(stations) and stations[j + 1]["on_road"] else None)
        run = stations[j]["s"] - stations[i]["s"] + step_m
        # WHAT THE RASTERISER REFUSED OVER THIS RUN, by cause and by count.
        # Recorded whatever the verdict is, because a gap the road stops at
        # for a measured reason and a gap nobody explained are the same shape
        # on the ground and must never read the same in the report.
        refused_here: dict[str, int] = {}
        refused_worst = None
        for k in range(i, j + 1):
            rf = stations[k].get("refused")
            if not rf:
                continue
            refused_here[rf["cause"]] = refused_here.get(rf["cause"], 0) + 1
            if (refused_worst is None
                    or abs(rf["full_m"]) > abs(refused_worst["full_m"])):
                refused_worst = rf
        cause = ("bridge" if any(stations[k]["bridged"] for k in range(i, j + 1))
                 else "settlement_pad" if any(stations[k]["in_pad"]
                                              for k in range(i, j + 1))
                 else "protected_structure" if any(stations[k]["near_protected"]
                                                   for k in range(i, j + 1))
                 else "below_water_plane" if any(stations[k]["underwater"]
                                                 for k in range(i, j + 1))
                 # A FOREIGN CLAIM IS AN EXPLANATION, NOT A HOLE, and it is
                 # the ruling: inside somebody's written footprint the pad
                 # wins and the road grades into its edge.  Measured from the
                 # blobs by `stamp`, never inferred from a radius.
                 else "foreign_claim" if refused_here.get("foreign")
                 else "protected_structure" if refused_here.get("protected")
                 else "below_water_plane" if refused_here.get("underwater")
                 else "settlement_pad" if refused_here.get("pad")
                 else "ribbon_end" if i == 0 or j == len(stations) - 1
                 else "HOLE")
        step = None
        if before is not None and after is not None:
            step = round(after["y"] - before["y"], 3)
        # The step a walker actually meets is at the SHOULDER EDGE, between the
        # last road station and the untouched ground half a metre on: report the
        # applied-vs-generated difference at the boundary stations, which is the
        # height the ground moves there.
        lip = []
        for st in (before, after):
            if st is not None:
                lip.append(round(st["y"] - st["gen"], 3))
        # THE STEP ACROSS A GAP IS NOT THE STEP INTO IT, AND ONLY THE SECOND
        # ONE IS A WALKER'S PROBLEM.
        #
        # `step_across_m` is `after.y - before.y`: the two RIMS against each
        # other.  A rasteriser refusal leaves the ground between them
        # UNWRITTEN at its generated height while both rims carry the fill,
        # so a symmetric notch reads ~0.000 across and the walker still has
        # to climb out of it.  MEASURED on this world, from the writes'
        # own recorded gaps:
        #
        #   T13-temple-harboursouth seq 5322, gap at (13.3, -255.3), 2.7 m,
        #   cause protected_structure, rasteriser_refused {protected: 6}:
        #       step_across_m           -0.001   <- passed
        #       fill_at_edges_m   [1.083, 0.381]
        #       refused_worst.full_m     1.147   at (13.0, -256.0)
        #   and the acceptance gate, reading the SAVE months of writes later,
        #   refuses exactly that notch: (14,-255) 32.230 -> (14,-256) 31.436
        #   is 0.794 m over 1 m of lattice against 0.7813, which is the two
        #   links that keep `harboursouth` unreachable.
        #
        # POPULATION, over the latest terrain_write of all 84 segment names
        # in this ledger: 40 recorded gaps, 14 of them NOT a bridge, and 7 of
        # those 14 have a drop into them over the slide limit - 1.292 m on
        # S12-brgs1-boathouse, 1.147 on T13, 1.067 on S1-portalhub-brgs2,
        # 0.824 and 0.807 on S8-wtsouth-treesth, 0.802 on N0-brgs2-wmid,
        # 0.792 on T10-wtspawn-wtsouth.  Every one of them shipped WALKABLE.
        #
        # THE CAUSE IS AN EXPLANATION, NOT AN EXEMPTION.  `holes` only ever
        # counted gaps nobody could explain, so naming the refusal - a pad, a
        # protected piece, a foreign claim - removed the gap from the verdict
        # entirely, whatever was left on the ground.  `stamp`'s own docstring
        # already says the opposite: "a protection that blocks a carriageway
        # sample leaves a recorded GAP and `walkability` judges it".  This is
        # that judgement.
        #
        # A BRIDGED RUN IS THE ONE HONEST EXEMPTION and it is a PLAN fact, not
        # a refusal: the ribbon stops at the bank by construction because the
        # deck is Crossings', and whether that deck exists is measured by the
        # crossings gate metre by metre.  Judging it here would refuse every
        # correctly bridged segment for the absence of terrain it must not
        # write.  It is still MEASURED and recorded, so a bridge that is
        # really a hole is visible rather than silent.
        judged = judge_gap(stations, i, j, before is not None,
                           after is not None, bridged=(cause == "bridge"))
        worst_in = judged["step_into_m"]
        worst_in_at = judged["step_into_at"]
        worst_in_limit = judged["step_into_limit_m"]
        unread_in = judged["stations_unread"]
        passable = judged["passable"]
        entry = {"cause": cause,
                 "from_s_m": round(stations[i]["s"], 1),
                 "length_m": round(run, 1),
                 "xz": [round(stations[i]["x"], 1), round(stations[i]["z"], 1)],
                 "step_across_m": step,
                 "fill_at_edges_m": lip,
                 "step_into_m": round(worst_in, 3),
                 "step_into_at": worst_in_at,
                 "step_into_limit_m": round(worst_in_limit, 4),
                 "stations_unread": unread_in,
                 "passable": passable,
                 "rasteriser_refused": refused_here or None,
                 "rasteriser_refused_worst": refused_worst}
        # WHAT THE JUNCTION WILL ACTUALLY BE.  A pad gap is the road stopping
        # at somebody else's earthwork, so the step the operator meets is
        # road-surface against that FOUNDATION'S OWN FLOOR.
        #
        # THE FOUNDATION, NOT THE SETTLEMENT.  Matching a gap to a site by
        # `pad_radius_m` and quoting the site's `pad_y` answered all six of
        # T4's Stenvik gaps "stenvik, 45.57 m, pad_levelled_yet False" -- one
        # number for a district `sites.yaml` itself says is NOT levelled, on
        # foundations that were levelled at seqs 61-311.  `pad_at` answers
        # with the building whose rectangle the gap is in.
        #
        # AND THE FLOOR, NOT THE INTENTION.  `pad_y` -- whether it came from
        # `sites.yaml` or from the pad's own `target_y` -- is a DECLARATION:
        # what somebody asked the ground to become.  `pad_standing_datum`
        # READS what it became, as the median applied surface over the
        # samples that pad actually levelled.  The step reported here is
        # measured against the reading whenever there is one, and the
        # declaration is carried beside it with its own error, so a
        # declaration that has drifted off its own ground is visible in the
        # junction report instead of being the thing the road is built to.
        pad = None
        for k in range(i, j + 1):
            pad = pad_keepouts.pad_at(stations[k]["x"], stations[k]["z"])
            if pad is not None:
                break
        if pad is not None:
            sid = pad["site_id"]
            if sid not in pad_floor:
                pad_floor[sid] = pad_standing_datum(pad_keepouts, sid, comps,
                                                    patches)
            stood, n_read = pad_floor[sid]
            declared = pad.get("pad_y")
            datum = stood if stood is not None else declared
            if datum is not None:
                edge = before if before is not None else after
                road_y = edge["y"] if edge is not None else None
                entry["terminates_at"] = {
                    "site_id": sid, "pad_seq": pad["seq"],
                    "pad_y": round(datum, 3), "pad_xz": pad["xz"],
                    "pad_half_extents_m": pad["half_extents_m"],
                    "pad_y_standing": (None if stood is None
                                       else round(stood, 3)),
                    "pad_y_declared": declared,
                    "pad_samples_read": n_read,
                    "declaration_error_m": (
                        None if stood is None or declared is None
                        else round(declared - stood, 3)),
                    "road_y_at_pad_edge": (None if road_y is None
                                           else round(road_y, 3)),
                    "step_road_to_pad_m": (None if road_y is None
                                           else round(datum - road_y, 3)),
                    "pad_levelled_yet": pad["levelled"],
                    "datum_source": (
                        f"READ: median applied surface over the {n_read} "
                        f"lattice sample(s) this pad levelled"
                        if stood is not None else
                        f"DECLARED ONLY - {pad['datum_source']}; this pad "
                        f"has levelled nothing, so there is no floor to "
                        f"read and the number below is an intention"),
                    "why": "the road surface at the pad boundary against the "
                           "height the pad's ground ACTUALLY IS. Positive "
                           "means the pad floor stands ABOVE the road and the "
                           "operator has to climb it; a step over ~0.5 m "
                           "cannot be walked up. `declaration_error_m` is how "
                           "far that pad's own declared datum has drifted off "
                           "its own ground - Vestvik's sites.yaml figure is "
                           "1.30 m low, and a road built to it arrives "
                           "nowhere.",
                }
        gaps.append(entry)
        i = j + 1

    holes = [g for g in gaps if g["cause"] == "HOLE"]
    # A GAP WITH A NAME IS STILL A GAP.  `holes` counts the ones nobody could
    # explain; `impassable` counts the ones a walker cannot get across
    # whatever their explanation is, and the verdict needs both.  See the
    # step_into block above for the population this was measured on.
    impassable = [g for g in gaps if g.get("passable") is False]
    on_road = sum(1 for st in stations if st["on_road"])
    verdict = ("WALKABLE" if g8["gradient"] <= SLIDE_GRADIENT and not holes
               and not impassable else "NOT WALKABLE")
    return {
        "verdict": verdict,
        "max_gradient_8m": g8["gradient"],
        "max_gradient_8m_at": g8["at"],
        "max_gradient_8m_rise_m": g8["rise_m"],
        "baselines_8m_pairs": g8["pairs"],
        "max_gradient_1m": g1["gradient"],
        "max_gradient_1m_at": g1["at"],
        "slide_limit": round(SLIDE_GRADIENT, 3),
        "centreline_stations": len(stations),
        "stations_on_road": on_road,
        "stations_off_generated_lattice": off_lattice,
        "gaps": gaps,
        "holes": len(holes),
        "gaps_impassable": len(impassable),
        "impassable": [
            {k: g[k] for k in ("cause", "xz", "length_m", "step_into_m",
                               "step_into_limit_m", "step_into_at",
                               "step_across_m", "stations_unread",
                               "rasteriser_refused")}
            for g in impassable],
        "method": (
            "MEASURED on the APPLIED surface, not the plan: the centreline is "
            f"walked at {step_m} m and each station's height is the bilinear "
            "blend of the four generated samples around it PLUS this write's "
            "bilinear delta (unwritten samples contribute zero), which is how "
            "Heightmap composes the mesh. The VERDICT gradient is taken over a "
            f"{VERDICT_BASELINE_M} m baseline between two stations whose whole "
            "run is on written road; the 1 m figure is reported beside it and "
            "is a DIFFERENT QUANTITY -- one lattice edge -- which is why it is "
            "not the verdict. Limit is tan(38 deg), the MEASURED slide "
            "angle. A GAP IS JUDGED SEPARATELY AND IN THE OTHER DIRECTION: "
            "`step_into_m` is the worst adjacent-station rise walking from "
            "the last written rim THROUGH the gap to the first written rim "
            "after it, against the same slide limit scaled by the two "
            "stations' own separation - which is the climb out of an "
            "unwritten notch, NOT `step_across_m`, the two rims measured "
            "against each other. A bridged run is exempt because the ribbon "
            "must not write terrain there and the crossings gate judges the "
            "deck; every other cause is an explanation of the gap and never "
            "an exemption from it."),
        "tool": "tools/jumpstart/roads/ribbon.py::walkability",
    }

# ---------------------------------------------------------------------------
# WHICH GROUND A ROAD'S RECORD NAMES
# ---------------------------------------------------------------------------
# THE WRITER'S HALF OF THE UNIT KEY, AND THIS FILE SPELT IT TWO DIFFERENT WAYS
# IN ONE CALL.  `buildstage.unit_of` reads `params.site_id`, then
# `meta.segment`, then `params.segment`; a record answering none of them is
# NO_UNIT, which is the replay's name for ground nothing can be ordered
# against.  This tool stamped `meta.segment` on its `zones_generate` and
# omitted it from the `terrain_write` -- the record that actually MOVES THE
# GROUND -- so the prep was keyed and the road was not.
#
# MEASURED 2026-09-24 over both ledgers: Ulfsland carries 291 road
# `zones_generate` WITH `meta.segment` and 100 road `terrain_write` with no
# unit at all (plus 16 `regrade_cut`); Fresh42, minutes after this build laid
# its first two carriageways, carried 2 `zones_generate` keyed and 2
# `terrain_write` unkeyed -- `road_S7_stathub_wtnorth` at seq 1455 and
# `road_T13_temple_harboursouth` at seq 1459.  The segment name was in
# `params.name` the whole time, prefixed and underscored, which is a name for
# a human and not a key: `unit_of` returned "" for both.
#
# ONE HELPER, USED AT BOTH EMIT SITES, so the two spellings cannot drift
# again.  ROAD_UNIT_DEPENDS_ON is the segment id and nothing else -- not the
# record's role, not its op, not the zone list, because the ground a road
# write touches belongs to the segment whatever kind of record names it.
ROAD_UNIT_DEPENDS_ON = ("segment_id",)


def road_unit_meta(segment_id: str) -> dict:
    """The `meta` keys by which a road record names ITS OWN GROUND."""
    return {"segment": segment_id}


def selftest_road_unit_key(verbose: bool = True) -> dict:
    """A road record must name its segment WHERE THE REPLAY READS IT.

    Read through `buildstage.unit_of` -- the real consumer -- rather than by
    re-implementing the lookup here, because a second copy of the reader is a
    second place for it to be wrong.
    """
    sys.path.insert(0, str(HERE.parent))
    import buildstage                                      # noqa: PLC0415

    seg_id = "S7-stathub-wtnorth"
    # THE KNOWN-BAD RECORD, VERBATIM: seq 1455 as this tool wrote it minutes
    # before this helper existed -- the segment in `params.name` and nowhere
    # machine-readable.
    known_bad = {"seq": 1455, "op": "terrain_write",
                 "params": {"role": "road_segment",
                            "name": "road_S7_stathub_wtnorth"},
                 "meta": {"why": "roads are terrain, not pieces"}}
    assert buildstage.unit_of(known_bad) == "", known_bad
    # THE SAME RECORD THROUGH THE HELPER.
    fixed = {**known_bad, "meta": {**known_bad["meta"],
                                   **road_unit_meta(seg_id)}}
    assert buildstage.unit_of(fixed) == seg_id, fixed
    # AND THE OTHER RECORD KIND OF THE SAME SEGMENT KEYS TO THE SAME GROUND,
    # which is the whole point of one helper: a `zones_generate` and a
    # `terrain_write` of one segment must be ONE unit, not two.
    zones = {"seq": 1454, "op": "zones_generate",
             "params": {"role": "road_segment"},
             "meta": road_unit_meta(seg_id)}
    assert buildstage.unit_of(zones) == buildstage.unit_of(fixed), zones
    # ...AND THEY RANK IN THE ORDER THIS TOOL EMITS THEM.  Asserted as an
    # ORDER, not as two literal indices: `ribbon.py` emits the segment's
    # `zones_generate` and then its `terrain_write` inside ONE step, and a
    # replay that ranks the write before its own generate reads a correct
    # build as OUT_OF_ORDER -- which is exactly what Fresh42's 2 OUT_OF_ORDER
    # rows (seq 1454 S7, seq 1458 T13) were, against a table that gave
    # ('road','zones_generate') to step 0 `inputs` while this file emits it
    # in step 4.  The literals are carried beside the order so a reader can
    # see WHICH step, but the order is the verdict: a renumbering of the
    # stages must not be able to pass this case silently.
    assert buildstage.rank_of(zones) < buildstage.rank_of(known_bad), (
        buildstage.rank_of(zones), buildstage.rank_of(known_bad))
    assert buildstage.rank_of(zones)[0] == buildstage.rank_of(known_bad)[0], (
        "a segment's generate and its write are ONE step",
        buildstage.rank_of(zones), buildstage.rank_of(known_bad))
    assert buildstage.rank_of(known_bad) == (4, 1), known_bad
    # THE FIXTURE MUST DISCRIMINATE: an empty segment id is not a key, and a
    # helper that returned one would make every road one unit.
    assert buildstage.unit_of({"meta": road_unit_meta("")}) == "", "empty"
    if verbose:
        print(f"  road unit key: known-bad record (params.name only) -> "
              f"unit {buildstage.unit_of(known_bad)!r}; through "
              f"road_unit_meta -> {buildstage.unit_of(fixed)!r}; "
              f"zones_generate of the same segment -> "
              f"{buildstage.unit_of(zones)!r}")
        print("ribbon road-unit-key selftest OK")
    return {"known_bad_unit": buildstage.unit_of(known_bad),
            "fixed_unit": buildstage.unit_of(fixed),
            "same_unit": buildstage.unit_of(zones) == buildstage.unit_of(fixed)}



# `--validate` SAYS "NOTHING IS APPENDED AND NOTHING IS SENT", AND IT WROTE
# THE WORLD'S BLOB INDEX.  The guard sandbox symlinked EVERY file of the real
# blob store, `index.json` included, and then stored this write's candidate
# blobs through the sandbox -- `Ledger.blob` rewrites `blobs/index.json`, and
# through a symlink that is the REAL store's index.  MEASURED: Ulfsland's
# `blobs/index.json` carries entries noted "guardcheck N1-wnorth-shelf",
# "guardcheck N3-mid1-mid2" and "guardcheck N4-mid2-terminus" for blobs its
# store never held.  The content-addressed blob files are immutable and are
# still linked; the index is a MUTABLE file and is copied.
GUARD_SANDBOX_DEPENDS_ON = ("which_files_of_the_store_are_immutable",)


# T8 BESIDE T3 AT (232,-57), IN MINIATURE: two parallel cuttings under a
# flat 37.0 m plain, both roads at 33.4, centrelines 10.5 m apart.  THEIR
# road is already in the live surface with its cut batter; MINE is stamped.
_FB_PLAIN, _FB_Y, _FB_SEP = 37.0, 33.4, 10.5


def _fb_their_delta(sx: int, sz: int) -> float:
    a = abs(sz)
    if abs(sx) > 60:
        return 0.0
    if a <= 4:
        return _FB_Y - _FB_PLAIN
    return min(_FB_Y + BATTER_GRADE * (a - 4.0), _FB_PLAIN) - _FB_PLAIN


def selftest_foreign_batter(verbose: bool = True) -> dict:
    """ANOTHER ROAD'S BATTER IS GROUND, BOTH WAYS -- see
    FOREIGN_BATTER_DEPENDS_ON.  The shipped rule (`foreign_batter_is_ground`
    False) reads their batter as their road, leaves the ray ungraded and the
    rim gate REFUSES the ridge; the survivor cuts the ridge inside their band
    and the gate is CLEAR, and neither arm writes their carriageway."""
    sys.path.insert(0, str(HERE))
    import junction as _junction                          # noqa: PLC0415

    def claim(x: float, z: float):
        d = _fb_their_delta(int(round(x)), int(round(z)))
        return None if abs(d) <= 1e-9 else (d, "road_P#1")
    plans = {"road_P": {"nodes": [[-60.0, 0.0], [60.0, 0.0]],
                        "profile_y": [_FB_Y, _FB_Y],
                        "is_bridge": [False, False], "width_m": 6.0}}
    band = foreign_road_band(claim, plans, lambda sx, sz: _FB_PLAIN)
    nodes = [(-30.0, _FB_SEP), (30.0, _FB_SEP)]

    def live(x: float, z: float) -> float:
        return _fb_their_delta(int(round(x)), int(round(z)))
    v: dict = {}
    for arm, on in (("survivor", True), ("shipped", False)):
        st = _rasterise(nodes, _FB_Y, lambda x, z: _FB_PLAIN,
                        road_claim_at=claim, foreign_road_at=band,
                        live_delta_at=live, foreign_batter_is_ground=on)
        comps = st["_comps"]
        taken: dict = {}
        for (zx, zz), comp in comps.items():
            cx0, cz0 = tcdata.zone_centre(zx, zz)
            for i in range(tcdata.SAMPLES):
                if not comp.modified_height[i]:
                    continue
                gy, gx = divmod(i, tcdata.PITCH)
                sx, sz = (int(round(c)) for c in
                          tcdata.sample_world(cx0, cz0, gx, gy))
                after = _FB_PLAIN + delta_at(comps, float(sx), float(sz))
                if abs(after - (_FB_PLAIN + live(sx, sz))) > \
                        appliedmod.AUTHOR_TOLERANCE_M:
                    taken[(sx, sz)] = after
        r = _junction.rim_transfer(
            taken, lambda sx, sz: _FB_PLAIN + live(sx, sz), None, top=10_000,
            height_after=lambda sx, sz: rim_height_after(
                sx, sz, _FB_PLAIN, comps, live))
        v[arm] = {"verdict": r["verdict"], "caused": r["caused"],
                  "worst_m": r["worst_caused_m"],
                  "on_their_carriageway": sum(1 for (x, z) in taken
                                              if abs(z) <= 4),
                  "their_batter_written": st["foreign_batter_written"]}
    assert v["shipped"]["verdict"] == "REFUSED" and v["shipped"]["caused"], v
    assert v["survivor"]["verdict"] == "CLEAR", v
    assert v["survivor"]["their_batter_written"] > 0, v
    assert v["survivor"]["on_their_carriageway"] == 0, v
    assert v["shipped"]["on_their_carriageway"] == 0, v
    if verbose:
        for arm, r in v.items():
            print(f"  {arm:9s} {r}")
        print("ribbon foreign-batter selftest OK")
    return v


def selftest_rim_closure(verbose: bool = True) -> dict:
    """T8's (-390,624), IN MINIATURE, THROUGH `close_rim` ITSELF.

    A cut: carriageway at 47.750, then this write's batter at 49.060 and
    49.373 over ground 49.070 / 49.388, natural 49.60 beyond -- the 1.31 m
    face the gate refused, with the cell outboard of it the only one that
    can give.  Three identical rows so no lateral edge is the question.  The
    shipped rule (no closure) leaves the gate REFUSED; the survivor lowers
    BOTH cells inside their band and the gate is CLEAR.  A second arm pins
    the claim band so tight nothing serves: the cell stays, counted
    `unclosable`, and the gate still refuses -- no step is hidden."""
    sys.path.insert(0, str(HERE))
    import junction as _junction                          # noqa: PLC0415
    gen_x = {0: 49.005, 1: 49.070, 2: 49.388, 3: 49.60, 4: 49.80, -1: 48.25}
    after_x = {0: 47.750, 1: 49.060, 2: 49.373}
    # THE ROWS BESIDE THE STRIP ALREADY STAND AT THE FINISHED SURFACE, so the
    # only edges the gate can charge are the ones along the strip.
    beside = {0: 47.750, 1: 48.531, 2: 49.312, 3: 49.60, 4: 49.80, -1: 48.25}

    def ground(x: float, z: float) -> float:
        row = gen_x if abs(int(round(z))) <= 1 else beside
        return row.get(int(round(x)), 49.80 if x > 0 else 48.25)
    v: dict = {}
    for arm, (closes, road_y) in {"survivor": (True, 47.654),
                                  "shipped": (False, 47.654),
                                  "no_band": (True, 50.5)}.items():
        comp = tcdata.Compiler(zone_x=0, zone_z=0)
        comps = {(0, 0): comp}
        patches = {"z_0_0": _flat_patch(0, 0, ground)}
        batter_at, road_cells, claim_of = {}, set(), {}
        for z in (-1, 0, 1):
            for x, h in after_x.items():
                gx, gy = tcdata.vertex_mask_index(*comp.centre, x, z)
                comp.set_height(gx, gy, h - gen_x[x])
                if x == 0:
                    road_cells.add((x, z))
                else:
                    batter_at[(x, z)] = (0, 0, gx, gy, gen_x[x])
            for x in range(1, 6):
                claim_of[(x, z)] = (4.9 + x, road_y)
        st: dict = {}
        if closes:
            close_rim(comps, patches, batter_at, road_cells, claim_of, st,
                      _junction.SLIDE_PER_M,
                      before_at=lambda sx, sz: ground(sx, sz),
                      refuse=lambda *a: None, foreign=lambda *a: None,
                      edge_m=4.0, grade=BATTER_GRADE, refeather=lambda: None)
        taken = {}
        for c in list(batter_at) + list(road_cells):
            h = ground(*c) + delta_at(comps, float(c[0]), float(c[1]))
            if abs(h - ground(*c)) > appliedmod.AUTHOR_TOLERANCE_M:
                taken[c] = h
        r = _junction.rim_transfer(
            taken, lambda sx, sz: ground(sx, sz), None, top=10_000,
            height_after=lambda sx, sz: ground(sx, sz) + delta_at(
                comps, float(sx), float(sz)))
        v[arm] = {"verdict": r["verdict"], "caused": r["caused"],
                  "worst_m": r["worst_caused_m"],
                  "closure": st.get("rim_closure")}
    assert v["shipped"]["verdict"] == "REFUSED", v
    assert v["survivor"]["verdict"] == "CLEAR", v
    assert v["survivor"]["closure"]["cells"] >= 2, v
    assert v["no_band"]["verdict"] == "REFUSED", v
    assert v["no_band"]["closure"]["unclosable"] > 0, v
    if verbose:
        for arm, r in v.items():
            print(f"  {arm:9s} {r['verdict']} {r['caused']} caused worst "
                  f"{r['worst_m']} closure {r['closure']}")
        print("ribbon rim-closure selftest OK")
    return v


# T7-stathub-stenvik's (648,-23), IN MINIATURE, every height the drive-3 run's
# own (RIM_CLOSURE_BAND_DEPENDS_ON).  Columns along x, identical rows.  T4
# paved x <= 0 (the live 56.794 / 56.774) and left its batter at x >= 1, the
# face at x 0 -> 1 standing 1e-7 inside the limit; T7 raises its shoulder at
# x -1 and 0 to 56.858.  x 1 is T7's claim at lat 4.2996 off a 4.0 m edge
# and T4's batter with T4's own band [55.8614, 56.4446].
_BAND_GEN = {-2: 54.50, -1: 54.273, 0: 53.757, 1: 53.499, 2: 53.290,
             3: 53.100, 4: 53.000}
_BAND_LIVE = {-2: 56.800, -1: 56.794, 0: 56.774, 1: 56.774 - 0.78128552,
              2: 55.6957, 3: 55.200, 4: 54.800}
_BAND_THEIRS = {1: (55.8614, 56.4446), 2: (55.6957, 56.6103),
                3: (54.900, 57.000)}
_BAND_ROAD_Y, _BAND_EDGE_M, _BAND_LAT1 = 56.858, 4.0, 4.2996


def selftest_rim_closure_band(verbose: bool = True) -> dict:
    """RIM_CLOSURE_BAND_DEPENDS_ON, BOTH WAYS, through `close_rim` and
    `junction.rim_transfer` themselves.  The shipped reading (`inside`)
    intersects T7's band [56.6239, 57.0921] with T4's and finds nothing, so
    the 0.8653 m step stands and the gate refuses; the survivor (`reach`)
    raises x 1 by the 0.084 m the lattice asks, inside T4's band, and the
    gate is CLEAR.  A third arm pulls T4's band under what the lattice needs
    (hi 56.05 < 56.0767): the survivor may not pass by ignoring it."""
    global _RIM_CLOSURE_BAND_MODE
    sys.path.insert(0, str(HERE))
    import junction as _junction                          # noqa: PLC0415
    lim = _junction.SLIDE_PER_M

    def col(x) -> int:
        return min(max(int(round(x)), -2), 4)

    def gen(x, z) -> float:
        return _BAND_GEN[col(x)]

    def live(x, z) -> float:
        return _BAND_LIVE[col(x)]

    rows = range(-3, 4)
    v: dict = {}
    was = _RIM_CLOSURE_BAND_MODE
    try:
        for arm, mode, hi1 in (("survivor", "reach", _BAND_THEIRS[1][1]),
                               ("shipped", "inside", _BAND_THEIRS[1][1]),
                               ("their_band_short", "reach", 56.05)):
            _RIM_CLOSURE_BAND_MODE = mode
            theirs = dict(_BAND_THEIRS)
            theirs[1] = (theirs[1][0], hi1)
            comp = tcdata.Compiler(zone_x=0, zone_z=0)
            comps = {(0, 0): comp}
            patches = {"z_0_0": _flat_patch(0, 0, gen)}
            batter_at, road_cells, claim_of = {}, set(), {}
            for z in rows:
                for x in range(1, 5):
                    claim_of[(x, z)] = (_BAND_LAT1 + x - 1, _BAND_ROAD_Y)
            for z in (-1, 0, 1):
                for x in (-1, 0):
                    gx, gy = tcdata.vertex_mask_index(*comp.centre, x, z)
                    comp.set_height(gx, gy, _BAND_ROAD_Y - gen(x, z))
                    road_cells.add((x, z))

            def foreign(x, z, theirs=theirs):
                c = col(x)
                if c <= 0:
                    return {"paved": True}
                if c not in theirs:
                    return None
                return {"who": "road_T4#1", "paved": False,
                        "surface": live(x, z), "lo": theirs[c][0],
                        "hi": theirs[c][1]}
            st: dict = {}
            close_rim(comps, patches, batter_at, road_cells, claim_of, st,
                      lim, before_at=live, refuse=lambda *a: None,
                      foreign=foreign, edge_m=_BAND_EDGE_M,
                      grade=BATTER_GRADE, refeather=lambda: None)

            def after(sx, sz):
                return rim_height_after(sx, sz, gen(sx, sz), comps,
                                        lambda x, z: live(x, z) - gen(x, z))
            taken = {c: after(*c) for c in list(batter_at) + list(road_cells)
                     if abs(after(*c) - live(*c)) > appliedmod.AUTHOR_TOLERANCE_M}
            r = _junction.rim_transfer(taken, live, None, top=10_000,
                                       height_after=after)
            v[arm] = {"verdict": r["verdict"], "caused": r["caused"],
                      "worst_m": r["worst_caused_m"],
                      "x1_after": round(after(1, 0), 4),
                      "closure": st.get("rim_closure")}
    finally:
        _RIM_CLOSURE_BAND_MODE = was
    assert v["shipped"]["verdict"] == "REFUSED", v
    assert v["shipped"]["worst_m"] == 0.8653, v
    assert v["shipped"]["closure"]["unclosable"] > 0, v
    assert v["survivor"]["verdict"] == "CLEAR", v
    # THE LEAST MOVE, and inside the other road's band.
    assert abs(v["survivor"]["x1_after"] - round(_BAND_ROAD_Y - lim, 4)) \
        <= 1e-4, v
    assert _BAND_THEIRS[1][0] <= v["survivor"]["x1_after"] \
        <= _BAND_THEIRS[1][1], v
    assert v["their_band_short"]["verdict"] == "REFUSED", v
    assert v["their_band_short"]["closure"]["unclosable"] > 0, v
    if verbose:
        for arm, r in v.items():
            print(f"  {arm:16s} {r['verdict']} {r['caused']} caused worst "
                  f"{r['worst_m']} x1 {r['x1_after']} closure "
                  f"{r['closure']}")
        print("ribbon rim-closure-band selftest OK")
    return v


# W9-vestvik-lhwest's ISLAND BETWEEN x03 AND x04, IN MINIATURE
# (RIM_BANK_END_DEPENDS_ON): ONE terrain run 1.5 m long between two bridged
# runs, both its clamps withheld, its 4 m carriageway at W9's 33.28 over land
# at 32.30.  The wet arm lowers the island to a 30.50 spit and puts the
# seed's water, 29.80, past both end faces: 0.70 m inside the limit before
# the write, so the face is CAUSED and only the water rule can refuse it.
_ISLE_NODES = [(-9.0 + 1.5 * i, 0.0) for i in range(13)]
_ISLE_BRIDGE = [i not in (6, 7) for i in range(13)]
_ISLE_ROAD_Y, _ISLE_LAND_M = 33.28, 32.30
_ISLE_SPIT_M, _ISLE_WATER_M = 30.50, 29.80


def _isle_ground(wx: float, wz: float) -> float:
    return _ISLE_LAND_M


def _isle_wet_ground(wx: float, wz: float) -> float:
    return _ISLE_WATER_M if (wx <= -1.0 or wx >= 2.0) else _ISLE_SPIT_M


def selftest_rim_bank_end(verbose: bool = True) -> dict:
    """RIM_BANK_END_DEPENDS_ON, BOTH WAYS, through the shipping `stamp` and
    `junction.rim_transfer`.  Unclaimed (the shipped reading) the island's two
    end faces stand 0.98 m over the land past them and the gate refuses; with
    the square end claimed the closure lays them to the limit and the gate is
    CLEAR.  Over water the closure's raise is `water_fill` and the gate still
    refuses: the claim is a band for the closure, never a licence.  And the
    face the water gate kept is labelled by `rim_step_owner` as the WATER's
    (`_CLOSURE_REFUSALS_MODE`): dropped, the same rows read "earthwork"."""
    global _RIM_BANK_END_MODE, _CLOSURE_REFUSALS_MODE
    v: dict = {}
    was = (_RIM_BANK_END_MODE, _CLOSURE_REFUSALS_MODE)
    try:
        for arm, mode, refusals, ground in (
                ("survivor", "claimed", "recorded", _isle_ground),
                ("shipped", "unclaimed", "recorded", _isle_ground),
                ("wet", "claimed", "recorded", _isle_wet_ground),
                ("wet_dropped", "claimed", "dropped", _isle_wet_ground)):
            _RIM_BANK_END_MODE, _CLOSURE_REFUSALS_MODE = mode, refusals
            st = _rasterise(_ISLE_NODES, _ISLE_ROAD_Y, ground,
                            is_bridge=_ISLE_BRIDGE, width_m=4.0)
            r = _rim_gate_over(st, ground)
            refused = refused_at_of(st)
            labels: dict[str, int] = {}
            for row in r["rows"]:
                lab = rim_step_owner(row, refused)
                labels[lab] = labels.get(lab, 0) + 1
            v[arm] = {"verdict": r["verdict"], "caused": r["caused"],
                      "worst_m": r["worst_caused_m"],
                      "paved": st["samples_paved"] + st["samples_shoulder"],
                      "closure": st["rim_closure"], "labels": labels}
    finally:
        _RIM_BANK_END_MODE, _CLOSURE_REFUSALS_MODE = was
    assert v["shipped"]["verdict"] == "REFUSED", v
    assert v["shipped"]["worst_m"] >= _ISLE_ROAD_Y - _ISLE_LAND_M - 1e-3, v
    assert v["survivor"]["verdict"] == "CLEAR", v
    assert v["survivor"]["closure"]["cells"] > 0, v
    # ...and not by paving less: the carriageway is the same lattice.
    assert v["survivor"]["paved"] == v["shipped"]["paved"], v
    assert v["wet"]["verdict"] == "REFUSED", v
    assert v["wet"]["closure"]["unclosable"] > 0, v
    # THE OWNER, NOT THE VERDICT, IS WHAT THE RECORD CHANGES: the same
    # surface, the same caused rows, and the faces the water gate kept are
    # the water's.  (The rest stand beside cells the closure never asked:
    # their same-lat neighbours on the water fix the interval first.)
    assert v["wet_dropped"]["verdict"] == "REFUSED", v
    assert v["wet_dropped"]["caused"] == v["wet"]["caused"], v
    assert v["wet"]["labels"].get("refused water_fill", 0) > 0, v
    assert "refused water_fill" not in v["wet_dropped"]["labels"], v
    if verbose:
        for arm, r in v.items():
            print(f"  {arm:11s} {r['verdict']} {r['caused']} caused worst "
                  f"{r['worst_m']} closure {r['closure']} {r['labels']}")
        print("ribbon rim-bank-end selftest OK")
    return v


def guard_sandbox(real, sandbox: Path, ledger_cls, actor: str,
                  links_index: bool = False):
    """A private chain for the write-time guards: the real ledger copied, its
    blob FILES linked, its index COPIED.  Returns `(sandbox ledger, linked)`.
    `links_index=True` is the rule that shipped (see
    GUARD_SANDBOX_DEPENDS_ON)."""
    if sandbox.exists():
        shutil.rmtree(sandbox)
    (sandbox / "blobs").mkdir(parents=True)
    shutil.copy2(real.path, sandbox / real.path.name)
    index_name = real._index_path.name
    if real._index_path.exists() and not links_index:
        shutil.copy2(real._index_path, sandbox / "blobs" / index_name)
    # SYMLINKS, not hardlinks: `/tmp` is a different filesystem from the
    # repo that holds the blob store, so `os.link` fails with EXDEV.  A
    # symlink costs the same nothing and `read_blob` resolves it -- it reads
    # bytes and re-digests them, so a link that pointed at the wrong payload
    # would be caught rather than trusted.
    linked = 0
    for blobfile in real.blobs.iterdir():
        if not blobfile.is_file():
            continue
        if blobfile.name == index_name and not links_index:
            continue
        os.symlink(blobfile, sandbox / "blobs" / blobfile.name)
        linked += 1
    return ledger_cls.open(WORLD, actor=actor, root=sandbox), linked


def selftest_pad_keepout_planned(verbose: bool = True) -> dict:
    """PAD_KEEPOUT_DEPENDS_ON, BOTH WAYS, through `pad_keepouts_from_chain`.

    An empty chain, two sites -- `town` with a 100 m district radius and ONE
    planned 10 x 10 m pad at its centre, `tower` with a 16 m radius and no
    plan -- the step-4 state of every Fresh42 settlement.  A sample 12 m from
    the town centre is outside every pad the build will lay: the shipped rule
    (`written`) refuses it to the district disc; the survivor does not.  The
    planned pad itself, and the unplanned tower's disc, are refused by both,
    so the survivor cannot pass by protecting nothing."""
    global _PAD_KEEPOUT_SOURCE
    sites = [{"id": "town", "xz": [0.0, 0.0], "pad_radius_m": 100.0},
             {"id": "tower", "xz": [500.0, 0.0], "pad_radius_m": 16.0}]
    planned = [{"seq": None, "name": "town-hall-1", "site_id": "town-hall-1",
                "x": 0.0, "z": 0.0, "hw": 5.0, "hd": 5.0, "datum_y": 40.0}]
    probes = {"district_12m": (12.0, 0.0), "planned_pad": (3.0, 0.0),
              "unplanned_tower": (510.0, 0.0)}
    v: dict = {}
    was = _PAD_KEEPOUT_SOURCE
    try:
        for arm, src in (("survivor", "written+planned"),
                         ("shipped", "written")):
            _PAD_KEEPOUT_SOURCE = src
            pk = pad_keepouts_from_chain(sites, [], None, planned=planned)
            v[arm] = {k: pk.contains(*p) for k, p in probes.items()}
    finally:
        _PAD_KEEPOUT_SOURCE = was
    assert v["shipped"]["district_12m"] == "town", v
    assert v["survivor"]["district_12m"] is None, v
    for arm in v:
        assert v[arm]["planned_pad"] is not None, v
        assert v[arm]["unplanned_tower"] == "tower", v
    if verbose:
        for arm, r_ in v.items():
            print(f"  {arm:9s} {r_}")
        print("ribbon pad-keepout-planned selftest OK")
    return v

def selftest_planned_pad_rim(verbose: bool = True) -> dict:
    """PLANNED_PAD_RIM_DEPENDS_ON, BOTH WAYS, on drive 3's own rim rows.

    Two caused rows VERBATIM from Fresh42 drive 3 step 4 (offline
    `--validate` dump of the 2026-09-25 plan): S8's worst, rim [-670,-382]
    inside `wt-south` (planned 20 x 20 m at (-672.1, -372.8), datum 72.48),
    and T8's worst, rim [-400,652] inside `wt-town` (planned at
    (-400.1, 661.2), datum 56.0).  The shipped reading (`ground`) charges
    both; the survivor owes S8's to the pad (0.1676 m against its datum) and
    still charges T8's (1.3013 m), so it cannot pass by forgiving a pad."""
    global _PLANNED_PAD_RIM_MODE
    planned = [{"seq": None, "name": "wt-south", "site_id": "wt-south",
                "x": -672.1, "z": -372.8, "hw": 10.0, "hd": 10.0,
                "datum_y": 72.48},
               {"seq": None, "name": "wt-town", "site_id": "wt-town",
                "x": -400.1, "z": 661.2, "hw": 10.0, "hd": 10.0,
                "datum_y": 56.0}]
    rows = [{"rim_xz": [-670, -382], "taken_xz": [-670, -383],
             "step_after_m": 1.5141, "step_before_m": 0.5621,
             "limit_m": 0.7813, "span": "one 1 m edge",
             "rim_height_m": 74.1617, "taken_height_after_m": 72.6476,
             "rim_authored_by": None},
            {"rim_xz": [-400, 652], "taken_xz": [-400, 651],
             "step_after_m": 1.2759, "step_before_m": 0.5338,
             "limit_m": 0.7813, "span": "one 1 m edge",
             "rim_height_m": 55.9746, "taken_height_after_m": 54.6987,
             "rim_authored_by": None}]
    pk = pad_keepouts_from_chain([], [], None, planned=planned)
    v: dict = {}
    was = _PLANNED_PAD_RIM_MODE
    try:
        for arm, mode in (("survivor", "datum"), ("shipped", "ground")):
            _PLANNED_PAD_RIM_MODE = mode
            kept, owed = planned_pad_owed(rows, pk, 0.78128562650672)
            v[arm] = {"caused": [r["rim_xz"] for r in kept],
                      "owed": [(r["planned_pad"], r["step_after_pad_m"])
                               for r in owed]}
    finally:
        _PLANNED_PAD_RIM_MODE = was
    assert v["shipped"] == {"caused": [[-670, -382], [-400, 652]],
                            "owed": []}, v
    assert v["survivor"] == {"caused": [[-400, 652]],
                             "owed": [("wt-south", 0.1676)]}, v
    # PLANNED_PAD_CLIFF_DEPENDS_ON on the Ulfsland regen's own S6 rows
    # (`hognest-castle`, planned 44 x 44 m at (530, 1348), datum 110.93, over
    # ground 108.2 m at its south edge) and three controls built from the
    # same pad: the write RAISED a sample on a cliff the pad already had
    # (owed), the write CUT one deeper under the datum (caused), and the write
    # moved one from within a slide limit of the datum to beyond it (caused).
    global _PLANNED_PAD_CLIFF_MODE
    castle = {"seq": None, "name": "hognest-castle",
              "site_id": "hognest-castle", "x": 530.0, "z": 1348.0,
              "hw": 22.0, "hd": 22.0, "datum_y": 110.93}

    def cliff_row(taken_before, taken_after, tag):
        return {"rim_xz": [533, 1326], "taken_xz": [533, 1325],
                "step_after_m": round(abs(taken_after - 110.93), 4),
                "step_before_m": 0.3, "limit_m": 0.7813,
                "span": "one 1 m edge", "rim_height_m": 110.93,
                "taken_height_before_m": taken_before,
                "taken_height_after_m": taken_after,
                "rim_authored_by": None, "tag": tag}
    crow = [cliff_row(107.953, 109.049, "raised_on_own_cliff"),
            cliff_row(109.8, 108.3, "cut_deeper"),
            cliff_row(110.5, 109.9, "cut_from_within_limit"),
            {**cliff_row(107.953, 109.049, "no_before_height"),
             "taken_height_before_m": None}]
    pk2 = pad_keepouts_from_chain([], [], None, planned=planned + [castle])
    was_cliff = _PLANNED_PAD_CLIFF_MODE
    try:
        for arm, mode in (("survivor", "owed"), ("neuter", "caused")):
            _PLANNED_PAD_CLIFF_MODE = mode
            kept, owed = planned_pad_owed(crow, pk2, 0.78128562650672)
            v["cliff_" + arm] = {"caused": [r["tag"] for r in kept],
                                 "owed": [r["tag"] for r in owed]}
    finally:
        _PLANNED_PAD_CLIFF_MODE = was_cliff
    assert v["cliff_survivor"] == {
        "caused": ["cut_deeper", "cut_from_within_limit", "no_before_height"],
        "owed": ["raised_on_own_cliff"]}, v
    assert v["cliff_neuter"] == {
        "caused": ["raised_on_own_cliff", "cut_deeper",
                   "cut_from_within_limit", "no_before_height"],
        "owed": []}, v
    if verbose:
        for arm, r_ in v.items():
            print(f"  {arm:14s} {r_}")
        print("ribbon planned-pad-rim selftest OK")
    return v


def selftest_finished_arrivals(verbose: bool = True) -> dict:
    """A displaced terminus must grade its entire gap without filling water."""
    pad = {"site_id": "destination", "name": "destination",
           "x": 30.2, "z": 0.0, "hw": 4.0, "hd": 4.0, "datum_y": 40.0}
    pads = pad_keepouts_from_chain([], [], planned=[pad])
    assert pads.finished_y(26, 0) == 40.0
    assert pads.finished_y(25, 0) is None
    seg = {"id": "approach", "nodes": [[-10.0, 0.0], [18.0, 0.0]],
           "profile_y": [41.0, 41.0], "is_bridge": [False, False],
           "width_m": 3.0, "length_m": 28.0}
    zones = sorted(set(segment_zones(seg["nodes"], seg["is_bridge"], 3.0))
                   | pads.approach_zones(seg))
    results = {}
    for case in ("dry", "water", "protected"):
        def ground(x, z):
            if case == "water" and 22 <= x <= 24:
                return 29.0
            return 42.0 if 22 <= x <= 32 else 40.0
        patches = {f"z_{x}_{z}": _flat_patch(x, z, ground) for x, z in zones}
        comps, stats = stamp(
            seg, patches, zones, pads,
            poi_keepouts=[(23.0, 0.0, 2.0)] if case == "protected" else [])
        def finished(x, z):
            fixed = pads.finished_y(x, z)
            if fixed is not None:
                return fixed
            return ground(x, z) + delta_at(comps, int(x), int(z))
        failures = pads.arrivals(seg, finished)
        results[case] = [f["check"] for f in failures]
        if case == "dry":
            assert not failures, failures
            assert abs(finished(25, 0) - finished(26, 0)) <= BATTER_GRADE
            stats["_comps"] = comps
            rim = _rim_gate_over(stats, ground, height_after=finished)
            assert rim["caused"] == 0, rim
        elif case == "water":
            assert finished(23, 0) < WATER_LEVEL_M
            assert failures, "an approach across unfilled water must be refused"
        else:
            assert finished(23, 0) == ground(23, 0)
            assert failures, "an immutable berm must not be called served"
    if verbose:
        print("ribbon finished-arrivals selftest OK", results)
    return results



def selftest_half_lattice(verbose: bool = True) -> dict:
    """HALF_LATTICE_DEPENDS_ON, both ways: the T7 shape (a 45-degree run whose
    stations land exactly on x = n + 0.5), a clean leg, a bridged node, and
    the neuter."""
    global _HALF_LATTICE_MODE
    from accept import instruments as gi                       # noqa: PLC0415

    def bad(nodes):
        return sum(_on_half_lattice(x, z) for _s, x, z in
                   gi.resample([tuple(p) for p in nodes], 1.0))
    # A 45-degree run with its ends on whole metres and an interior node
    # whose position puts a station exactly on x = n + 0.5: found by a
    # deterministic search so the fixture states what it needs, not a number.
    t7 = next([[0.0, 0.0], [round(0.7 * k, 3), round(0.7 * k, 3)], [40.0, 40.0]]
              for k in range(5, 400)
              if bad([[0.0, 0.0], [round(0.7 * k, 3), round(0.7 * k, 3)],
                      [40.0, 40.0]]) > 0)
    assert bad(t7) > 0, "the fixture must start on a half-lattice line"
    moved = nudge_off_half_lattice(t7, [False] * len(t7))
    assert moved > 0 and bad(t7) == 0, (moved, bad(t7))
    clean = [[0.0, 0.0], [10.3, 0.1], [20.1, 0.3]]
    before = [p[:] for p in clean]
    assert nudge_off_half_lattice(clean, [False] * 3) == 0 and clean == before
    _HALF_LATTICE_MODE = "off"
    try:
        off = [[0.0, 0.0], [t7[1][0] - 0.0, t7[1][1]], [40.0, 40.0]]
        off[1] = [round(t7[1][0] - 0.0, 3), round(t7[1][1], 3)]
        off = next([[0.0, 0.0], [round(0.7 * k, 3), round(0.7 * k, 3)],
                    [40.0, 40.0]] for k in range(5, 400)
                   if bad([[0.0, 0.0], [round(0.7 * k, 3), round(0.7 * k, 3)],
                           [40.0, 40.0]]) > 0)
        assert nudge_off_half_lattice(off, [False] * 3) == 0 and bad(off) > 0
    finally:
        _HALF_LATTICE_MODE = "nudge"
    if verbose:
        print("ribbon half-lattice selftest OK")
    return {"moved": moved}


def selftest_snap_shared(verbose: bool = True) -> dict:
    """SNAP_SHARED_DEPENDS_ON, both ways: the W9 / S5 shape of Fresh42 drive 10
    (a leg 0.9 m beside a built leg for a long run), a leg that merely crosses
    it, a leg further off than the snap distance, and the neuter."""
    global _SNAP_SHARED_MODE
    built = [[float(x), 0.0] for x in range(0, 200, 2)]

    def leg(z, xs):
        return [[float(x), z] for x in xs]
    res = {}
    beside = leg(0.9, range(0, 100, 2))
    moved = snap_shared_runs(beside, [False] * len(beside), [built])
    res["beside"] = (moved, max(abs(p[1]) for p in beside[3:-3]))
    assert moved == len(beside) - 6 and res["beside"][1] == 0.0, res
    assert beside[0][1] == 0.9 and beside[-1][1] == 0.9, "node ends are pinned"
    far = leg(3.0, range(0, 100, 2))
    assert snap_shared_runs(far, [False] * len(far), [built]) == 0
    cross = [[float(x), z] for x, z in zip(range(50, 60), [-4.5 + k for k in range(10)])]
    assert snap_shared_runs(cross, [False] * len(cross), [built]) == 0, \
        "a leg that only crosses is not a shared corridor"
    bridged = leg(0.9, range(0, 100, 2))
    assert snap_shared_runs(bridged, [True] * len(bridged), [built]) == 0
    _SNAP_SHARED_MODE = "off"
    try:
        off = leg(0.9, range(0, 100, 2))
        assert snap_shared_runs(off, [False] * len(off), [built]) == 0
        res["neuter"] = max(abs(p[1]) for p in off)
        assert res["neuter"] == 0.9
    finally:
        _SNAP_SHARED_MODE = "snap"
    if verbose:
        print("ribbon snap-shared selftest OK", res)
    return res


def selftest_guard_sandbox(verbose: bool = True) -> dict:
    """THE GUARD SANDBOX LEAVES THE REAL STORE BYTE-FOR-BYTE, BOTH WAYS.

    A throwaway chain in a temp dir with one blob and an index, a sandbox
    built from it, and a candidate blob stored through the sandbox -- the
    exact sequence `--validate` runs.  The shipped rule (`links_index=True`)
    must change the real index; the survivor must leave it identical and
    still let the sandbox read the real blob."""
    import tempfile                                       # noqa: PLC0415
    sys.path.insert(0, str(JUMPSTART / "ledger"))
    from writer import Ledger as _L                       # noqa: PLC0415

    class _Open:
        """`Ledger.open` without its seq-0 manifest: the fixture chain is
        empty, and a manifest would fingerprint this host."""
        @staticmethod
        def open(_world, *, actor, root):
            return _L(root, actor)
    v: dict = {}
    for arm, links in (("survivor", False), ("shipped", True)):
        with tempfile.TemporaryDirectory() as td:
            rroot = Path(td) / "real"
            rroot.mkdir()
            (rroot / "ledger.jsonl").write_text("")
            real = _L(rroot, "selftest")
            old = real.blob(b"a prior terrain blob", note="prior")
            before = real._index_path.read_bytes()
            sand, linked = guard_sandbox(real, Path(td) / "sand", _Open,
                                         "selftest", links_index=links)
            sand.blob(b"a candidate blob", note="guardcheck fixture")
            v[arm] = {"real_index_unchanged":
                      real._index_path.read_bytes() == before,
                      "sandbox_reads_prior": sand.read_blob(old)
                      == b"a prior terrain blob",
                      "linked": linked}
    assert v["shipped"]["real_index_unchanged"] is False, v
    assert v["survivor"]["real_index_unchanged"] is True, v
    assert v["survivor"]["sandbox_reads_prior"] is True, v
    if verbose:
        for arm, r in v.items():
            print(f"  {arm:9s} {r}")
        print("ribbon guard-sandbox selftest OK")
    return v


def main() -> int:
    # THE CASES RUN WITHOUT A WORLD, A SEED OR A SERVER, so they are reachable
    # before `--segment` is required: a self-test you have to name a segment
    # to run is a self-test nobody runs.
    if "--selftest" in sys.argv[1:]:
        selftest_cache_name()
        selftest_gap_judgement()
        selftest_water_sign()
        selftest_batter_water_gate()
        selftest_batter_advance()
        selftest_batter_placed_gate()
        selftest_batter_ray_key()
        selftest_station_arc_injective()
        selftest_toe_feather()
        selftest_feather_conflict()
        selftest_interstice_trigger()
        selftest_interstice_taper()
        selftest_rim_after_surface()
        selftest_rim_step_owner()
        selftest_batter_ray_end()
        selftest_batter_reach_exit()
        selftest_ribbon_claim_ends()
        selftest_guard_sandbox()
        selftest_pad_keepout_planned()
        selftest_planned_pad_rim()
        selftest_finished_arrivals()
        selftest_snap_shared()
        selftest_half_lattice()
        selftest_foreign_batter()
        selftest_rim_closure()
        selftest_rim_closure_band()
        selftest_rim_bank_end()
        selftest_ribbon_beside_itself()
        selftest_road_unit_key()
        # THE CROSSING PREMISE, WHICH THIS FILE NOW REFUSES ON, PROVES
        # ITSELF WHERE IT LIVES - and it is run from here so a builder who
        # runs one selftest runs both.
        sys.path.insert(0, str(HERE))
        import premise as premisemod                      # noqa: PLC0415
        premisemod.selftest()
        return 0
    ap = argparse.ArgumentParser()
    ap.add_argument("op", choices=["plan", "build"])
    ap.add_argument("--selftest", action="store_true",
                    help="run the cases that need no world and exit")
    ap.add_argument("--segment", required=True)
    ap.add_argument("--no-connection-collar", action="store_true",
                    help="lay this segment without the graded pad collar "
                         "(COLLAR_CHOICE_DEPENDS_ON): the alternate arm the "
                         "build step retries when the collar leaves a step "
                         "the writer refuses")
    ap.add_argument("--segments", default=None,
                    help="the plan file (default: this run's seed's, "
                         "worldseed.segments_path(), asked after --seed)")
    ap.add_argument("--preflight-out",
                    help="append this segment's validated pre-flight record to "
                         "a JSON file. The pre-flight IS the replay evidence: it "
                         "names the zones, the sample counts, the worst cut and "
                         "fill, the clamp result, both clearance verdicts and the "
                         "blob digests, so a future regeneration can tell whether "
                         "it is rebuilding the same road before it sends a "
                         "command.")
    ap.add_argument("--validate", action="store_true",
                    help="build the ledger records and run schema.validate on "
                         "them OFFLINE. Nothing is appended and nothing is "
                         "sent. This is the safe pre-flight: LiveBuilder(dry=1) "
                         "still APPENDS its records and still runs the "
                         "postcondition against the live server, so a dry run "
                         "leaves failed attempts in the shared ledger and "
                         "cannot tell you whether a terrain_write validates.")
    ap.add_argument("--dry", action="store_true",
                    help="validate the ledger records and the wire strings "
                         "without sending: LiveBuilder(dry=True). Run this "
                         "BEFORE taking a live console window -- the schema "
                         "refuses a malformed terrain_write at append time, and "
                         "finding that out while holding the console is how a "
                         "staged operation gets left half-sent.")
    ap.add_argument("--zone-batch", type=int, default=8,
                    help="zones per zones_generate call")
    ap.add_argument("--placed-objects",
                    help="JSON map of LIVE placed-object positions produced by "
                         "roads/clear.py --census: {\"objects\": [[x, z], ...]}. "
                         "The batter is new ground movement over ground that "
                         "already carries seated objects, and MEASURED by "
                         "SeatCheck the hub portal u-lheast already floats "
                         "0.536 m because a ribbon cut under it after it was "
                         "seated. Without this file the batter is REFUSED, "
                         "because an ungated batter is that defect at scale.")
    ap.add_argument("--no-batter", action="store_true",
                    help="rasterise the carriageway only, no side slopes. This "
                         "reproduces the pre-repair geometry and exists to "
                         "MEASURE it, not to ship it.")
    ap.add_argument("--repair-of", type=int,
                    help="the seq of the terrain_write this run REPAIRS. A "
                         "repair that does not name what it repairs is "
                         "indistinguishable from a first build, and the "
                         "ledger is the only place the operator can find out "
                         "which one he is standing on.")
    ap.add_argument("--repair-before",
                    help="JSON file holding the MEASURED before-state of the "
                         "surface being repaired (the step census off the "
                         "live blobs). Recorded verbatim in meta so the "
                         "repair has a verifiable baseline rather than a "
                         "claim -- the same shape the T12 pothole census took "
                         "before its repair.")
    ap.add_argument("--actor", required=True,
                    help="the agent id that is ACTUALLY running this pass, "
                         "used for every ledger record this run appends. "
                         "Stated rather than defaulted: the module constant "
                         "is the PatchScan sandbox's name and reusing it for "
                         "provenance records work under the name of an agent "
                         "that yielded hours earlier.")
    # THE WRITE END OF THE ORDER.  A plan whose route was never checked
    # against a reading of this world's ground is the state every Ulfsland
    # road was written in, so it is refused here rather than at the end of the
    # rasterisation -- and the escape has to be TYPED, with a reason, which is
    # the difference between a waiver and a default.
    ap.add_argument("--census",
                    help="the census artefact this plan was routed against. "
                         "Re-checked HERE -- coverage and freshness -- "
                         "because a plan that was covered when it was written "
                         "is not covered any more once its zones have been "
                         "regenerated.")
    ap.add_argument("--census-waiver",
                    help="write a segment whose plan carries no census "
                         "verdict. Requires a reason, which is recorded in "
                         "the ledger. Every road on Ulfsland was written this "
                         "way; 42 of the 48 bodies that block a walker were "
                         "already standing when their road was written.")
    ap.add_argument("--junction-accept",
                    help="JSON of {\"A|B\": {\"spread_m\": x, \"reason\": "
                         "\"...\"}} waivers for junction disagreements this "
                         "write knowingly leaves in place. A waiver is pinned "
                         "to the SPREAD and is VOID if the measured number "
                         "moves by more than 1 cm, because a blanket by-name "
                         "waiver is how the temple's 4.206 m conflict became "
                         "a 4.304 m conflict with nobody noticing.")
    ap.add_argument("--only-zones",
                    help="restrict the write to these zone indices "
                         "(\"0,0;1,-1\"), INTERSECTED with the corridor cover "
                         "-- a named zone the road does not reach is REFUSED, "
                         "never dropped. The zones NOT named keep the ground "
                         "they have and this write is not their author. This "
                         "is the consumer for `reconciled[].zones_to_reemit`, "
                         "which `junction.py --reconcile --apply` has been "
                         "recording since it was written and which no caller "
                         "ever read.")
    worldseed.add_arguments(ap)
    args = ap.parse_args()

    global SEED, WORLD
    SEED, WORLD = worldseed.apply(args)
    args.segments = args.segments or str(worldseed.segments_path())

    doc = yaml.safe_load(Path(args.segments).read_text())
    segs = [s for s in doc["segments"] if s["id"] == args.segment]
    if not segs:
        raise SystemExit(f"no segment {args.segment} in {args.segments}; have "
                         f"{[s['id'] for s in doc['segments']]}")
    seg = segs[0]
    if args.no_connection_collar:
        seg["connection_collar"] = False
    # THE RECORD, TAKEN BEFORE ANYTHING CAN MUTATE IT.  `solve_approach` and
    # the cliff pass both rebind `seg["profile_y"]` IN PLACE, so a comparison
    # made later would compare the emission with itself and report EVERY write
    # as a re-emit -- the always-says-yes failure, which is the most dangerous
    # possible outcome for this particular check.  See `reemit.snapshot()`.
    recorded_profile = reemitmod.snapshot(seg)

    # ---- THE GATE, ASKED BEFORE THE WRITE INSTEAD OF AFTER IT -------------
    #
    # `location_check` below is the authority and it stays the authority.
    # The problem it had was WHEN it spoke.  It runs after `zone_patches`,
    # `stamp` and a full carriageway rasterisation, so a route that cannot
    # legally be built costs minutes of work and a refusal at the end --
    # and MEASURED on R2-ulfsgard-harbourb, 2026-09-19, the router that
    # produced the alignment had no way to ask the question at all.  Its
    # answer came back as two hand-typed discs (9.0 m round a `WoodHouse6`
    # and 25.0 m round a `ShipSetting01`) pasted into a bespoke forbid mask.
    #
    # `poi.gate_check` is that same boundary restated on the CENTRELINE, so
    # it can be answered from the dump in milliseconds and BEFORE any
    # rasterisation.  It derives 13.0 m and 29.0 m for those two instances
    # -- the hand-typed radii, inflated by the 4.0 m level half-width, which
    # is what `RoadsConnect` worked out the hard way.
    #
    # It is conservative by at most one 1 m sample pitch, so a violation
    # inside that band is reported as `marginal` and does NOT refuse: the
    # lattice, not this function, decides those, and `location_check` is
    # still the thing that decides them.  MEASURED both ways on the live
    # centrelines: `road_R1_ulfsgard_t8junction` is short by 0.30 m here and
    # the writer accepts it by +0.21 m (marginal, correctly passed);
    # `road_N2_shelf_mid1` is short by 2.52 m here and the writer refuses it
    # by -2.14 m (firm, correctly stopped).
    # `pois = poimod.load_all()` STOOD HERE AND WAS NEVER READ -- the gate
    # below asks `poimod.load()` inline.  It loaded the generator dump plus
    # `mod_keepout.json` on every run for nothing, and now that the census is
    # the source for standing bodies it would be reading the wrong artefact as
    # well as reading it pointlessly.
    pre = poimod.gate_check([(n[0], n[1]) for n in seg["nodes"]],
                            seg["width_m"] / 2.0, poimod.load())
    print(f"plan-time location gate: {pre['verdict']} over {pre['instances']} "
          f"generator instances, {len(pre['firm_violations'])} firm / "
          f"{pre['marginal_violations']} marginal (boundary "
          f"{pre['boundary']}, level half {pre['level_half_width_m']} m)")
    for v in pre["violations"]:
        print(f"   {'FIRM' if not v['marginal'] else 'marginal'} {v['name']} "
              f"{v['xz']} centreline {v['centreline_dist_m']} m, "
              f"needs {v['required_m']} m, short by {v['short_by_m']} m")
    if pre["firm_violations"]:
        raise SystemExit(
            "REFUSING BEFORE THE WRITE: the centreline is inside a generator "
            "location's write-gate radius by more than the 1 m sample pitch, "
            "so `location_check` will refuse this alignment after the whole "
            "rasterisation. Re-route the leg. This is the same boundary and "
            "the same numbers -- asked early, not a second opinion.")
    # OUR OWN BUILDINGS ARE NOT IN THE GENERATOR DUMP AND THE WRITER CANNOT
    # SEE THEM.  `mod_keepout.json` schema 2 is the completed carriageway
    # census -- all 25 built segments, 21,929 m of live centreline -- but it is
    # still only the ROAD BAND, so absence from it is not evidence of a clear
    # corridor anywhere else.  Reported, never a refusal, because the standing
    # ruling on a structure in a carriageway is re-route / re-terminate /
    # accept-and-record -- and a segment that is ALREADY BUILT must not be
    # blocked from a repair pass by a building it has stood beside since it was
    # written.  Rows with `forbid: false` are records, not constraints: schema 1
    # recorded our OWN bridge deck as a mod structure with a 13.18 m keepout,
    # which would forbid N0 to cross the water it was built to cross.
    modp = poimod.load_mod_keepout()
    modv = poimod.gate_check([(n[0], n[1]) for n in seg["nodes"]],
                             seg["width_m"] / 2.0, modp)["violations"] if modp else []
    print(f"mod-placed keep-outs: {len(modp)} known, {len(modv)} entered by "
          f"this centreline" + (f" {[v['name'] for v in modv]}" if modv else ""))

    # ---- THE ORDERING GATE, AT THE WRITE END --------------------------------
    #
    # THE DEFECT, AND IT IS THIS FILE'S OWN.  `main`'s live path generates every
    # zone this road crosses (STEP 0, below) and generating a zone is the moment
    # a More_World_Locations body is CREATED.  Between that and the terrain
    # write it asks ONE question -- `objects_count id=_ZoneCtrl` -- which is
    # "did a controller appear", not "what stands here".  MEASURED consequence
    # across this world: 5,318 non-clearable objects in the paved width plus a
    # 1 m shoulder over 122 bodies, 641 pieces in 48 bodies that block a walker,
    # and 42 of those 48 bodies were ALREADY STANDING when their road was
    # written.  The router could not have known: it planned against the
    # generator dump, which is mod-free by construction.
    #
    # So the plan has to arrive carrying a verdict from a reading of THIS
    # world's ground, and it is re-checked here rather than trusted, because
    # the two failure modes have different dates: coverage is a fact about the
    # route, freshness is a fact about the zones, and a zone regenerated after
    # the census holds objects the census never saw.
    cen_rec = seg.get("census") or {}
    cen_live = None
    if args.census:
        cen_live = censusmod.Census.load(Path(args.census))
        cov = censusmod.coverage_gate([(n[0], n[1]) for n in seg["nodes"]],
                                      cen_live)
        # THE POPULATION IS THIS SEGMENT'S OWN ZONES, not the whole census.
        # Unscoped, the replayed Ulfsland census is STALE in 232 of its 551
        # zones and would refuse every segment in the network, including the
        # ones whose ground nobody has touched since it was read.  The zones
        # come from the census cells this centreline actually lies in, which is
        # the same set the coverage gate just tested.
        my_cells = censusmod.cells_for_band(
            [(n[0], n[1]) for n in seg["nodes"]], 0.0)
        my_zones = censusmod.zones_for_cells(my_cells)
        fresh = censusmod.freshness_gate(
            cen_live, JUMPSTART / "ledger" / "runs" / str(WORLD),
            zones=my_zones)
        print(f"census gate: {cov['verdict']} "
              f"({cov['stations_uncensused']}/{cov['stations']} stations on "
              f"unread ground, {len(cen_live.bodies)} bodies over "
              f"{len(cen_live.cells)} cells) / freshness {fresh['verdict']} "
              f"({fresh['stale_count']}/{fresh['zones_checked']} of THIS "
              f"segment's zones generated after the census)")
        near = poimod.near_centreline(cen_live.pois(),
                                      [(n[0], n[1]) for n in seg["nodes"]],
                                      seg["width_m"] / 2.0)
        for r in near[:6]:
            print(f"   census body {r['name']} at {r['xz']} centreline "
                  f"{r['centreline_dist_m']} m, hard {r['hard_m']} m, "
                  f"violates={r['violates_hard']}")
        if cov["verdict"] != "COVERED":
            raise SystemExit(
                f"REFUSING BEFORE THE WRITE: {cov['stations_uncensused']} of "
                f"{cov['stations']} stations stand on ground this census never "
                f"read (first at {cov['first_uncensused'][:3]}). Paving unread "
                f"ground is how 42 standing bodies ended up under a "
                f"carriageway. Re-run `precensus.py survey` over this line and "
                f"re-plan.")
        if fresh["verdict"] == "STALE" and not args.census_waiver:
            raise SystemExit(
                f"REFUSING BEFORE THE WRITE: {fresh['stale_count']} of "
                f"{fresh['zones_checked']} censused zones were generated AFTER "
                f"this census was taken (e.g. {fresh['stale'][:2]}), so it is a "
                f"reading of a different world. Re-survey, or pass "
                f"--census-waiver with a reason.")
    elif cen_rec.get("verdict") == "COVERED":
        print(f"census gate: plan carries COVERED from "
              f"{cen_rec.get('measured_utc')} "
              f"({cen_rec.get('stations_uncensused')}/"
              f"{cen_rec.get('stations')} stations unread at plan time). Pass "
              f"--census to re-check it here against the live ledger.")
    elif not args.census_waiver:
        raise SystemExit(
            f"REFUSING BEFORE THE WRITE: this plan carries no census verdict "
            f"(census={cen_rec or 'absent'}). A road planned without a reading "
            f"of the ground it crosses is the state every Ulfsland road was "
            f"written in: 42 of the 48 bodies that block a walker were already "
            f"standing when their road was written, and the router could not "
            f"see one of them. Produce one with `precensus.py survey` and "
            f"re-plan with `network.py plan --census`, or -- if this is a "
            f"repair of a road that is ALREADY on the ground -- pass "
            f"--census-waiver '<reason>' and the reason goes in the ledger.")
    if args.census_waiver:
        print(f"census gate WAIVED: {args.census_waiver}")

    # ---- THE JUNCTION GATE, ASKED BY THE THING THAT WRITES ----------------
    #
    # THE STRUCTURAL HOLE THIS CLOSES, and it is not a missed case.
    # `transverse_census` below MEASURES a junction step and then correctly
    # DISCLAIMS it -- ribbon.py:2587-2591, "pairs whose only written sample
    # belongs to another claim ... are that claim's to grade" -- because a
    # segment cannot re-emit its neighbour.  That ruling is right for a
    # per-segment write-time census and it is exactly why FIVE segments each
    # measured the temple's 4.206 m disagreement at (-56.0, -34.0), each
    # correctly disclaimed it, and it went into the ground: a junction step
    # is not any single segment's property, it is the PLAN's.
    #
    # `roads/junction.py` has owned that question for a while and NOTHING
    # CALLED IT.  This is the call.  It reads design profiles only, costs
    # milliseconds, and runs beside the other two plan-time gates rather
    # than after a full rasterisation.
    #
    # The population is every `*segments*.yaml` beside the one named, because
    # Ulfsland's two networks meet (W4/N0, 4.522 m at (-272.18, 1281.82)) and
    # a gate handed one file would pass that pair by never looking at it.
    # The refusals are THIS segment's own pairs: a writer can only be held to
    # what it writes.
    sys.path.insert(0, str(HERE))
    import junction as junctionmod  # noqa: PLC0415
    j_accept = (json.loads(Path(args.junction_accept).read_text())
                if args.junction_accept else {})
    j_files = junctionmod.population_files(Path(args.segments))
    jrep = junctionmod.build_gate(
        seg["id"], j_files,
        JUMPSTART / "ledger" / "runs" / str(WORLD) / "ledger.jsonl",
        j_accept)
    # DID THIS WRITE MAKE IT WORSE?  Recomputed HERE, over the EMITTED set, at
    # the point the gate is read -- never computed once and carried, because a
    # boolean held across a boundary is a `--re-emit` flag with an internal
    # caller instead of an external one, and a later change that emits a moved
    # station inherits a certification it never earned.
    #
    # IT WAIVES NOTHING.  A carriageway re-emit does not make a junction
    # refusal acceptable; it records that THIS WRITE did not create it. The
    # refusals ride along in `pre_existing` and stay refused for anyone who
    # moves them.  And the verdict names its own reference and its own SCOPE:
    # the carriageway only, never the batter, which is measured separately.
    reemit_verdict = reemitmod.classify(
        seg["id"], recorded_profile, seg["profile_y"],
        junction_refusals=jrep.get("refusals") or [], nodes=seg["nodes"])
    print(f"  re-emit: {reemit_verdict['verdict']} "
          f"({reemit_verdict['moved_count']} of {reemit_verdict['stations']} "
          f"centreline stations moved, worst "
          f"{reemit_verdict['worst_move_m']:.6f} m; batter measured separately)")
    print(f"junction gate: {jrep['verdict']} -- {jrep['my_pairs']} pair(s) of "
          f"this segment share ground with another participant, "
          f"{jrep['my_refusals']} over the "
          f"{jrep['tolerance']['tier1_at_overlap_m']} m slide allowance, "
          f"{jrep['my_waived']} waived; population "
          f"{jrep['population']['segments']} segments / "
          f"{jrep['population']['pads']} pads from "
          f"{[p.name for p in j_files]}")
    for r in jrep["refusals"][:8]:
        print(f"   {r['a']} vs {r['b']} at ({r['xz'][0]}, {r['xz'][1]}): "
              f"{r['a_y']} vs {r['b_y']} = {r['spread_m']} m over a "
              f"{r['gap_m']} m gap, limit {r['slide_allow_m']} m"
              + (f"  [{r['waiver_void']}]" if r.get("waiver_void") else ""))
    if len(jrep["refusals"]) > 8:
        print(f"   ... {len(jrep['refusals']) - 8} more")
    # IS THE PLAN THIS GATE JUST READ ACTUALLY IN THE WORLD?  `build_gate`
    # compares `profile_y` against its neighbours' `profile_y`, and
    # `solve_approach`/`grade_into_foreign` REWRITE this segment's own
    # `profile_y` further down this same function and NOTHING WRITES THE
    # RESULT BACK INTO THE PLAN FILE.  So a station the emitter has regraded
    # in a previous run carries a height that has never been in the ground,
    # and every plan-time gate measures it as if it were.
    #
    # THAT IS NOT A REPORTING PROBLEM, IT IS A REFUSAL THAT CANNOT BE ACTED
    # ON.  MEASURED on Ulfsland 2026-09-22 over T12-wtspawn-temple: 16 drifted
    # stations, worst 3.009 m, producing 10 of this segment's 22 junction
    # refusals -- including a 3.180 m disagreement with the wt-spawn pad at
    # (104.93, -98.0) whose GROUND agrees with that pad to 0.000 m.  The
    # refusal sent the operator to `--reconcile --at 104.93,-98.0`, which
    # answered [] -- and an empty answer reads as "already reconciled" when it
    # means "there is nothing to reconcile, because what you measured is not
    # the ground".  Four hours of a campaign rested on that reading.
    #
    # NETWORK-WIDE, same measurement: 137 drifted stations over 9 of the 25
    # built segments, against 43 deliberate ones over 3.  Drift is the larger
    # population and nothing was tracking it.
    #
    # SO IT IS REFUSED WHERE IT IS LOAD-BEARING AND ONLY THERE: a drifted
    # station this write's junction gate is REFUSING on.  Drift the gate does
    # not refuse on is reported and carried; drift under a refusal stops the
    # write, because reconciling against a height the world does not have is a
    # repair aimed at nothing.  THE FIX IS NOT A WAIVER: restore the plan to
    # this segment's own last emission at those stations and re-run.
    last_em = reemitmod.last_emission(
        JUMPSTART / "ledger" / "runs" / str(WORLD) / "ledger.jsonl",
        f"road_{seg['id']}".replace("-", "_"))
    div = None
    if last_em:
        div = reemitmod.divergence(
            {"id": seg["id"], "nodes": seg["nodes"],
             "profile_y": recorded_profile,
             "reconciled": seg.get("reconciled") or []},
            last_em["nodes"])
        print(f"plan vs ground: {div['matched_xz']}/{div['stations']} "
              f"stations matched by xz against the last emission "
              f"(seq {last_em['seq']}), {div['unmatched_xz']} unmatched; "
              f"{div['deliberate_count']} DELIBERATE (inside a recorded "
              f"reconciliation taper, worst {div['worst_deliberate_m']:.3f} m) "
              f"and {div['drift_count']} EMIT-TIME DRIFT the plan never "
              f"received (worst {div['worst_drift_m']:.3f} m)")
        for r in div["drift"][:6]:
            print(f"   DRIFT st{r['station']} {r['xz']} plan "
                  f"{r['record_y']:.3f} vs ground {r['ground_y']:.3f} "
                  f"= {r['delta_m']:+.3f} m")
        drifted = {(round(v[0], 2), round(v[1], 2)) for v in div["drift_xz"]}
        # REFUSED **AND** WAIVED, and the waived half is the one that cost
        # four hours.  A waiver is pinned to a MEASURED SPREAD and is void
        # when that number moves -- but a spread taken against a station the
        # world does not have was never a measurement of the world at all, so
        # the pin holds a number nothing can move.  MEASURED: seq 5101 waived
        # `T12-wtspawn-temple|wt-spawn` at 3.180 m; the ground there agrees
        # with that pad to 0.000 m and has since the emission that created
        # the drift.  A waived row on a drifted station is the same defect as
        # a refused one and is stopped here with it.
        on_drift = []
        for r in list(jrep["refusals"] or []) + list(jrep.get("waived") or []):
            for key in ("a_station_xz", "b_station_xz"):
                p = r.get(key)
                if p and (round(float(p[0]), 2),
                          round(float(p[1]), 2)) in drifted:
                    on_drift.append(r)
                    break
        if on_drift:
            w = max(on_drift, key=lambda r: r["spread_m"])
            raise SystemExit(
                f"REFUSING BEFORE THE WRITE: {len(on_drift)} junction row(s) "
                f"of {len(jrep['refusals'] or [])} refused + "
                f"{len(jrep.get('waived') or [])} waived stand on a station "
                f"whose PLAN IS NOT IN THE GROUND. Worst: {w['a']} vs "
                f"{w['b']} at ({w['xz'][0]}, {w['xz'][1]}) reads "
                f"{w['spread_m']} m against the plan, and this segment's plan "
                f"there disagrees with its own last emission (seq "
                f"{last_em['seq']}) by up to {div['worst_drift_m']:.3f} m over "
                f"{div['drift_count']} station(s) that NO recorded "
                f"reconciliation covers. `grade_into_foreign` rewrote those "
                f"heights at emission time and nothing wrote them back, so "
                f"the spread you are being refused for -- or have WAIVED -- "
                f"is between a height in the world and a height that has "
                f"never been in it. `--reconcile --at` cannot repair it and "
                f"will answer with an empty proposal, which reads as "
                f"'already reconciled'. RESTORE THE PLAN TO THE GROUND at "
                f"{sorted(drifted)[:6]}"
                + (f" (+{len(drifted) - 6} more)" if len(drifted) > 6 else "")
                + f" -- take the heights from this segment's own last "
                f"emission -- and re-run. A waiver is NOT the answer: pinned "
                f"to a spread that is not in the world it is pinned to "
                f"nothing.")
    if jrep["verdict"] == "REFUSED":
        w = jrep["worst"]
        raise SystemExit(
            f"REFUSING BEFORE THE WRITE: this segment disagrees with a "
            f"neighbour about ground they share, worst {w['spread_m']} m at "
            f"({w['xz'][0]}, {w['xz'][1]}) between {w['a']} and {w['b']}, "
            f"against a {w['slide_allow_m']} m allowance derived from "
            f"tan(38 deg) over one lattice edge. A player slides down that, "
            f"and no per-segment census will ever own it -- five segments "
            f"each measured the temple's 4.206 m step and each correctly "
            f"disclaimed it. Reconcile with `roads/junction.py --reconcile "
            f"--at {w['xz'][0]},{w['xz'][1]}` and apply the proposal, or -- "
            f"if this repair is knowingly leaving that junction broken -- "
            f"pass --junction-accept with a file naming the pair AND its "
            f"measured spread, e.g. "
            f'{{"{junctionmod.accept_key(w)}": '
            f'{{"spread_m": {w["spread_m"]}, "reason": "..."}}}}. '
            f"The waiver is VOID the moment that number moves by 1 cm.")
    # SETTLEMENTS' OWN FILE IS THE AUTHORITY FOR THE DATUM, AND THE LEDGER IS
    # THE AUTHORITY FOR THE FOOTPRINT.  Main ruled
    # tools/jumpstart/settlements/sites.yaml authoritative and it is the
    # boundary -- but the two things it carries are not the same kind of fact.
    #
    # `pad_y` is a DECLARATION: 45.57 m is the height stenvik's pads will be
    # levelled to and the height T4 has to arrive at, and before a pad is
    # written that file is the only place the number exists.  It stays.
    #
    # `pad_radius_m` is a PLANNING STAND-OFF and `plan.py`'s own docstring
    # says it is "taken from the district or the pad plus its own margin" --
    # two different quantities behind one name.  Reading it as a foundation
    # cost T4 293.9 m of unwritten centreline; see `PadKeepOut`.  So the
    # keep-out is now built from the pad claims the WRITE-TIME GUARD will
    # judge this write against, with the declared radius kept only for a site
    # that has no live pad at all.
    sites_path = worldseed.sites_path()
    if sites_path.exists():
        site_list = [dict(s) for s in
                     yaml.safe_load(sites_path.read_text())["sites"]]
        pad_source = str(sites_path)
    else:
        site_list = [{"id": sid, "xz": list(s["xz"]),
                      "pad_radius_m": s["pad_radius_m"], "pad_y": s.get("pad_y")}
                     for sid, s in specmod.SETTLEMENT_SITES.items()]
        pad_source = "spec.SETTLEMENT_SITES (PROVISIONAL fallback)"
    sys.path.insert(0, str(JUMPSTART / "ledger"))
    from writer import Ledger  # noqa: PLC0415
    _led_for_pads = Ledger.open(WORLD, actor=args.actor)
    pad_keepouts = pad_keepouts_from_chain(
        site_list, _led_for_pads.records(), ledger=_led_for_pads,
        planned=planned_pad_claims(str(WORLD), str(SEED)))
    if pad_keepouts.undecodable:
        raise SystemExit(
            f"REFUSING: {len(pad_keepouts.undecodable)} pad terrain_write "
            f"entry(ies) could not be decoded, so the ground those pads "
            f"levelled is unknown: {pad_keepouts.undecodable[:6]}. "
            f"`writer._pad_footprint_check` fails CLOSED on exactly this, so "
            f"rasterising now buys a refusal at append time. Fix the blob "
            f"store first.")
    print(f"pad keep-outs: {pad_keepouts.describe()}; "
          f"datums from {pad_source}")
    batter_m = 0.0 if args.no_batter else BATTER_MAX_M
    zones = segment_zones(seg["nodes"], seg["is_bridge"], seg["width_m"],
                          batter_m=batter_m)
    planned_approaches = planned_pad_claims(str(WORLD), str(SEED))
    approach_pads = pad_keepouts_from_chain([], [], planned=planned_approaches)
    zones = sorted(set(zones) | approach_pads.approach_zones(seg, batter_m))
    # THE WINDOW THE PLANNER ALREADY RECORDED, AND NOTHING CONSUMED.
    # `junction.py::reconcile --apply` writes `reconciled[].zones_to_reemit`
    # into every segment it re-grades, and `junction.zones_touched` computes
    # it -- "the zones a re-emit of the moved stations has to rewrite".  No
    # caller ever read either.  Written, tested, consulted by nobody: the same
    # shape as `deviate.forbidden_footprints` before it was wired.
    #
    # WHY A ZONE AND NOT A STATION.  A `terrain_write` entry IS a zone -- one
    # `_TerrainCompiler` blob per 64 m square -- so the zone is the only unit
    # in which "emit this part of the road and not that part" is expressible
    # at all.  A station window has no representation on the wire.
    #
    # WHAT IT BUYS AND WHAT IT COSTS, BOTH MEASURED on T12-wtspawn-temple
    # 2026-09-22, and the cost is the larger number -- recorded here because a
    # flag whose docstring names only its benefit is a flag nobody re-measures.
    #
    #   BUYS: emitting the WHOLE segment re-asserts carriageway at the temple
    #     that `road_T5_temple_dockshore` overwrote at seq 5310 AFTER T12's
    #     last emission, tearing a 2.047 m step into ground T5 had healed --
    #     24 of the 97 rim steps `rim_transfer` charges to the write, none of
    #     them anywhere near the repair being carried.  The window leaves that
    #     ground exactly as it stands and those 24 do not occur.
    #   COSTS: the zone boundary BECOMES A RIM.  Over T12's reconciliation
    #     window {(0,-1),(0,0),(1,-1),(1,0)} the taken set falls 1641 -> 1256
    #     samples and the CAUSED steps rise 97 -> 131, because the write now
    #     stops mid-carriageway at x = +96 and z = -32 where it previously ran
    #     out into its own batter.  A narrower write is not automatically a
    #     gentler one.
    #
    # So this is a WINDOW, not a mitigation: use it where the excluded zones
    # hold ground another author has since taken, and measure the rim either
    # way before choosing.
    #
    # IT IS AN INTERSECTION, NEVER AN ASSERTION.  A named zone the corridor
    # does not cover is REFUSED rather than silently dropped: a window that
    # quietly selects nothing is `missing: []` on the write path.
    if args.only_zones:
        want = {tuple(int(v) for v in z.split(","))
                for z in args.only_zones.split(";") if z.strip()}
        cover = set(zones)
        stray = want - cover
        if stray:
            raise SystemExit(
                f"REFUSING: --only-zones names {sorted(stray)}, which "
                f"{seg['id']}'s corridor cover does not contain. Its "
                f"{len(cover)} candidate zone(s) are {sorted(cover)}. A window "
                f"naming a zone the road does not reach selects nothing there "
                f"and says nothing about it.")
        zones = [z for z in zones if z in want]
        print(f"--only-zones: writing {len(zones)} of {len(cover)} candidate "
              f"zone(s) {sorted(zones)}; the {len(cover) - len(zones)} "
              f"excluded zone(s) {sorted(cover - want)} KEEP THE GROUND THEY "
              f"HAVE -- this write does not re-assert them and is not their "
              f"author")
    # TWO POPULATIONS, AND THIS LINE USED TO NAME ONE AND CLAIM THE OTHER.
    # `segment_zones` returns the CANDIDATE cover of the corridor including
    # the full batter reach; the ZDO cost is the STAMPED set, the zones that
    # actually receive a sample and therefore a `_TerrainCompiler`, printed
    # by the `stamped ... over N zones` line below.  MEASURED on the three
    # northern legs: candidates 55 / 21 / 83 against stamped 38 / 18 / 66, so
    # `candidates = ZDOs` over-states N2's cost by 45%, and it over-states it
    # in the direction the 470-ZDO network budget is reasoned against.  A
    # candidate zone that receives no sample gets no compiler.
    print(f"{seg['id']}: {seg['length_m']} m, {seg['width_m']} m wide + "
          f"{SHOULDER_M} m shoulder + {batter_m} m batter reach, "
          f"{len(zones)} CANDIDATE zones in the corridor cover (batter reach "
          f"included); the ZDO cost is the STAMPED set, see `stamped ... over "
          f"N zones` below")

    out = SCRATCH / f"{seg['id']}.bin"
    # THE PATCH REQUEST IS THE FULL CORRIDOR COVER, NOT THE WRITE COVER.
    # `segment_zones` deliberately drops a bridged station - the ribbon
    # writes nothing there, so the rasteriser needs no patch for it - and
    # the ground under the bridged stations is EXACTLY what the premise gate
    # below has to read.  Asking for the write cover and then asking the
    # premise question against it reported 17 of W5's 48 stations and 17 of
    # W10's as `unread`: an honest count over a population the instrument
    # had excluded by construction.  The superset is requested FIRST so one
    # PatchScan serves both; `stamp` is still handed `zones`, so nothing
    # extra is written.
    premise_zones = segment_zones(seg["nodes"], [False] * len(seg["nodes"]),
                                  seg["width_m"], batter_m=batter_m)
    patches = zone_patches(sorted(set(zones) | set(premise_zones)), SEED, out)
    prot = protected_piece_positions(zones)
    print(f"protected pieces in these zones: {len(prot['pieces'])} "
          f"{prot['by_site'] or '{}'}"
          + (f"; records with NO recoverable piece: {prot['without_pieces']}"
             if prot["without_pieces"] else ""))
    # THE LIVE PLACED-OBJECT MAP.  Read before stamping because the batter is
    # gated per sample on it, and a gate applied after the fact is a report
    # rather than a gate.
    placed: list[tuple[float, float]] = []
    placed_source = "none"
    if args.placed_objects:
        pdoc = json.loads(Path(args.placed_objects).read_text())
        # TWO SHAPES, ONE PRODUCER.  This argument's own help names
        # `roads/clear.py --census` as the producer and documents
        # `{"objects": [[x, z], ...]}`, but what `clear.py census` actually
        # writes today is a list of RECORDS - {"prefab", "id", "x", "y",
        # "z", "lat_m", "road_y", "station", "clearable", ...} - and the
        # pair form is what some older artefacts on disk hold.  Reading
        # only the pair form makes the documented command fail with
        # `KeyError: 0` AFTER the census has been paid for (423 s and 790
        # socket calls on S1), at which point the tempting move is to
        # hand-convert the file, which is how a batter ends up gated on a
        # transformation nobody checked.  Accept both, keyed by NAME where
        # the producer gives names, and say which was read.
        raw = pdoc["objects"]
        if raw and isinstance(raw[0], dict):
            placed = [(float(o["x"]), float(o["z"])) for o in raw
                      if batter_freezes_on(o.get("prefab"))]
            named = sum(1 for o in raw if o.get("prefab"))
            shape = (f"records, {len(placed)} of {len(raw)} name a prefab "
                     f"whose ground may not move ({named} named)")
        else:
            # NO PREFAB, SO NO SPLIT, SO EVERY POSITION FREEZES THE BATTER.
            # Fail closed and say so: an artefact that cannot answer "what is
            # standing here" must not be read as "nothing that matters is".
            placed = [(float(o[0]), float(o[1])) for o in raw]
            shape = ("xz pairs, NO PREFAB NAMES -- every position freezes the "
                     "batter, which is the pre-split behaviour and is what an "
                     "artefact that cannot be keyed is worth")
        placed_source = (f"{args.placed_objects} ({pdoc.get('method', '?')}"
                         f", {shape})")
    elif batter_m:
        raise SystemExit(
            "REFUSING to write a batter without --placed-objects: the batter "
            "moves ground that has never moved before, and the one recorded "
            "instance of that hazard tonight (portal u-lheast, 0.536 m float) "
            "happened precisely because nothing measured what was standing on "
            "it. Run roads/clear.py --census first, or pass --no-batter.")
    print(f"placed objects gating the batter: {len(placed)} from {placed_source}")
    # THE WIDENED FOOTPRINT'S OWN POI KEEP-OUTS.  The ribbon's clearance was
    # computed for a 7 m strip; a 38 deg batter off an 8 m wall is ~10 m more
    # per side, so the stand-offs are re-asked for the widened region rather
    # than assumed to transfer. Discs, not a verdict: the batter CLIPS per
    # sample, so what it needs from the instrument is a geometry it can test a
    # single sample against.
    poi_keepouts = poi_standoff_discs(zones) if batter_m else []
    print(f"POI keep-out discs gating the batter: {len(poi_keepouts)}")

    # ---- THE LIVE SURFACE, BEFORE ANYTHING IS DECIDED ------------------
    #
    # Read first, because two of this repair's three jobs are answers to what
    # is ALREADY in the world rather than to what the plan says: which samples
    # a foreign write owns (so the road stops fighting a pad for them), and
    # what height the road has to arrive at when it meets one.  Deriving
    # either from radii or from `pad_y` measures the intention instead of the
    # ground, and the T4 clobber is 89 m of road that proves the difference.
    live = appliedmod.Applied(actor=ACTOR)
    # RULE 1, MAIN'S RULING, NOW IMPLEMENTED AS AUTHORSHIP RATHER THAN AS A
    # NAME FILTER -- AND THE NAME FILTER WAS WRONG IN BOTH DIRECTIONS.
    #
    # THE UNION LAUNDERS OWNERSHIP.  A repair MUST union every prior claim in
    # a zone forward into its own blob -- a zone holds exactly one
    # `_TerrainCompiler` and this op does `deleteObjects -zone` first, so
    # anything it does not carry is destroyed.  The record then stores that
    # same union, so a pad's floor inside a road's blob reads as
    # `role: road_segment` under the road's name, and `foreign_at` -- the
    # instrument that implements "inside a settlement pad the pad wins", and
    # which is deliberately blind to radii because that is what caught T4's
    # clobber -- cannot see the pad any more.
    #
    # MEASURED on T8-stenvik-wttown: its write (seq 1197) refused 123
    # carriageway samples because a site_pad owned them; the identical
    # rasterisation run against the surface that write produced finds ZERO.
    # Those samples are a building's foundation and a second repair would
    # pave them.
    #
    # THE FIRST FIX WAS TO DROP THIS SEGMENT'S OWN ZONE ENTRIES FROM THE
    # OWNERSHIP VIEW, so the per-sample owner fell back to the previous claim
    # in file order.  It cures the T8 direction and CAUSES THE MIRROR DEFECT,
    # measured on S1-portalhub-brgs2: the portal hall's pad seq 801 unioned
    # S1's carriageway forward VERBATIM (sample (-300, 214) is -1.694 in both
    # blobs, to the bit), so with S1's own entries dropped the hall became the
    # first writer of that sample in its zone lattice and read as its author.
    # `foreign_at` then answered `portal-hall#801` over the whole 30.6 m ramp
    # and the rasteriser refused all 215 carriageway samples and stamped ZERO
    # -- the first surface a player walks off a portal, unrepairable.  No name
    # filter can fix that, because the laundered copy is under the HALL's
    # name.
    #
    # SO THE QUESTION IS ASKED PROPERLY INSTEAD: `applied.py` now composes an
    # AUTHOR per sample -- the last claim whose value DIFFERED from the state
    # the claims before it had composed -- alongside the holder, and every
    # ownership answer comes from the author.  Nothing is dropped from the
    # composition, so a laundered copy can never become a first write; a pad's
    # floor is attributed to the pad even inside a road's blob, and a road's
    # carriageway is attributed to the road even inside a pad's blob.  Both
    # directions fall out of one measurement.
    # WHOSE GROUND, SAID OUT LOUD.  This line reported "1786 terrain_write
    # zone entries, 479 zones claimed" while building Fresh42, a world with
    # ZERO terrain writes, because `applied.Applied` defaulted its world to
    # Ulfsland and this call passed none.  An instrument that cannot name the
    # world it read cannot be caught reading the wrong one, so it names it --
    # and a reading of a world that is not the one being built is REFUSED
    # here rather than turned into a rim verdict about foreign ground.
    if str(live.world) != str(WORLD):
        raise SystemExit(
            f"REFUSING BEFORE THE WRITE: the live applied surface was read "
            f"from world {live.world!r} ({live.world_source}) and this "
            f"segment is being built in {WORLD!r}. Every rim, ownership and "
            f"re-emit verdict below would be a measurement of another "
            f"world's ground -- MEASURED on Fresh42, 1,786 foreign "
            f"terrain_write entries over 479 zones and a refusal naming "
            f"seq 5354, which that world's 1,451-record ledger does not have.")
    print(f"live surface: {len(live.writes)} terrain_write zone entries in "
          f"{live.world}'s ledger ({live.world_source}), "
          f"{len({w['zone'] for w in live.writes})} zones claimed; "
          f"OWNERSHIP BY AUTHORSHIP (the last claim that CHANGED each sample, "
          f"not the last whose blob holds it), so the union cannot launder a "
          f"pad's floor into a road's name nor a road's carriageway into a "
          f"pad's")

    # ---- THE CROSSING PREMISE, RE-MEASURED BEFORE THE RIBBON SKIPS ------
    #
    # THE DEFECT, AND IT IS THE ONLY ONE IN THIS FILE THAT MAKES A ROAD
    # VANISH RATHER THAN GO WRONG.  Every `is_bridge` flag on this segment
    # traces back to `route.find_crossings` writing `why_terrain_failed:
    # water_body` against a run of stations -- a statement about the
    # GENERATED field, made ONCE, at routing time, and never re-measured
    # against anything afterwards.
    #
    # Its consequence is total and SILENT IN BOTH DIRECTIONS AT ONCE: this
    # writer skips those stations so no `terrain_write` covers them, and the
    # deck builder owns them instead and refuses them as buried, so no
    # `bridge_body_place` covers them either.  A crossing that is WRONG
    # deletes a stretch of road from BOTH owners' populations in the same
    # moment, and NEITHER owner's gate can see it.  MEASURED on Ulfsland
    # 2026-09-22 over all fourteen detected crossings, engine bed from the
    # ledger's own blobs on the generated patch: 13 clear, and
    # `S12-brgs1-boathouse-x01` standing on 20 of its own 46 stations with
    # the bed ABOVE its declared deck of 33.870, worst +2.507 m, over a
    # ridge -- 46 of that road's 74 stations struck out of this writer's
    # population by a water body that is not there.  Its built ribbon is
    # 51.2 m of a planned 144.9 m and its A terminus reads EXACTLY 33.870:
    # the road was benched AT DECK LEVEL and then stopped.
    #
    # SO IT IS ASKED HERE, BY THE THING THAT SKIPS.  A wet-metre threshold
    # is refused as a discriminator -- W5 is only 43 % wet and is a real
    # 89.44 m crossing -- and the threshold-free statement is the physical
    # one: A DECK MUST BE ABOVE THE GROUND IT CROSSES.  The repair is
    # `roads/premise.py reclassify`, which hands the stations back and
    # refits the profile off the bed; it is NOT a waiver, because a waiver
    # here would put the road back between the two owners.
    import premise as premisemod                          # noqa: PLC0415

    def _premise_bed(x: float, z: float):
        got = live.surface_at(patches, x, z)
        return None if got is None else float(got[0])

    prem = premisemod.gate(seg, _premise_bed)
    print(f"crossing premise: {prem['verdict']} -- {prem['crossings_asked']} "
          f"of {prem['crossings_declared']} crossing(s) on this segment "
          f"asked, {prem['judged']} judged, {prem['failed']} FAILED, "
          f"{prem['unjudged']} unjudgeable, "
          f"{prem['crossings_already_reclassified']} already reclassified")
    for v in prem["verdicts"]:
        head = ("FAILED" if v["judged"] and not v["pass"]
                else "clear" if v["judged"] else "UNJUDGED")
        print(f"   {head} {v['crossing_id']} over "
              f"{v['stations_in_population']} skipped station(s) "
              f"({v.get('samples')} read, {v.get('samples_unread')} unread): "
              f"{v.get('wet_samples')} wet, "
              f"{v.get('bed_above_deck_samples')} with the bed above its own "
              f"declared deck {v.get('deck_y')}, worst "
              f"{v.get('worst_bed_above_deck_m')} m -> owner {v.get('owner')}")
    if prem["verdict"] == "REFUSED":
        bad = [v for v in prem["verdicts"] if v["judged"] and not v["pass"]]
        w = max(bad, key=lambda v: v["worst_bed_above_deck_m"])
        raise SystemExit(
            f"REFUSING BEFORE THE WRITE: {len(bad)} crossing(s) of "
            f"{prem['crossings_declared']} on this segment are about to take "
            f"stations out of this writer's population for a water body that "
            f"IS NOT THERE. Worst: {w['crossing_id']} stands "
            f"{w['worst_bed_above_deck_m']} m above its own declared deck "
            f"{w['deck_y']} on {w['bed_above_deck_samples']} of "
            f"{w['samples']} station(s) read, against a "
            f"{premisemod.BURIAL_MAX_M} m limit; {w['wet_samples']} of those "
            f"metres are under the {premisemod.WATER_LEVEL_M} m plane. "
            f"Skipping them writes no terrain, and the deck builder refuses "
            f"the same metres as buried, so the road falls between two "
            f"owners with both gates green -- which is exactly what happened "
            f"to S12-brgs1-boathouse. THE FIX IS NOT A WAIVER: run "
            f"`roads/premise.py reclassify --segment {seg['id']} --apply`, "
            f"which hands the stations back to the ribbon and refits the "
            f"profile off the measured bed, and re-run this build.")

    def foreign_at(x: float, z: float):
        return live.foreign_at(x, z)

    # ANOTHER ROAD'S CARRIAGEWAY, FOR THE CORRIDOR RULE.  Main's ruling: where
    # two road claims run closer than the sum of their batter runs they are
    # ONE CORRIDOR, and a batter that tapers into a finished road cuts it.
    # This is scoped to OTHER segments -- this segment's own prior write is
    # what is being replaced, so treating it as foreign would refuse the
    # repair its own ground.
    my_name = f"road_{seg['id']}".replace("-", "_")

    def road_claim_at(x: float, z: float):
        d, w = live.sample(int(round(x)), int(round(z)))
        if w is None or w["role"] != appliedmod.ROAD_ROLE:
            return None
        if w["name"] == my_name:
            return None
        return (d, f"{w['name']}#{w['seq']}")

    # ...AND READ AGAINST THAT ROAD'S OWN PLAN, so its batter is ground and
    # only its carriageway is a corridor partner: FOREIGN_BATTER_DEPENDS_ON.
    # EVERY PLAN IN THIS RUN'S SEGMENTS FILE, by the name its write carries;
    # the rim-owner table below asks the same map.
    plans_by_write = {"road_" + s_["id"].replace("-", "_"): s_
                      for s_ in doc["segments"]}
    foreign_road_at = foreign_road_band(
        road_claim_at, plans_by_write,
        lambda sx, sz: appliedmod.generated_at(patches, float(sx), float(sz)))

    # SHARED CORRIDORS (SNAP_SHARED_DEPENDS_ON): lie on the built leg.
    _nodes_before = [list(n) for n in seg["nodes"]]
    _snapped = snap_shared_runs(
        seg["nodes"], seg["is_bridge"],
        built_road_lines(plans_by_write, road_claim_at, my_name))
    _nudged = nudge_off_half_lattice(seg["nodes"], seg["is_bridge"])
    if (_snapped or _nudged) and (
            set(segment_zones(seg["nodes"], seg["is_bridge"], seg["width_m"],
                              batter_m=batter_m))
            - set(zones)):
        # The zones were scanned from the nodes as given: a move that needs
        # a zone nobody scanned is not made.
        seg["nodes"][:] = _nodes_before
        _snapped = _nudged = 0
    if _nudged:
        print(f"half-lattice: {_nudged} node(s) of {seg['id']} moved "
              f"{HALF_LATTICE_NUDGE_M} m so no station sits on a half-lattice "
              f"line [HALF_LATTICE_DEPENDS_ON]")
    if _snapped:
        print(f"shared corridor: {_snapped} station(s) of {seg['id']} lay "
              f"within {SNAP_SHARED_M} m of a built leg and are now ON its "
              f"centreline [SNAP_SHARED_DEPENDS_ON]")

    # THE RASTERISER, AS A FUNCTION OF THE PROFILE, so the approach solve can
    # ASK the clamp and the gradient about a candidate reach instead of
    # predicting them.  Identical inputs to the single call it replaces --
    # this is the same rasterisation, run once per candidate.
    def rasterise(sg: dict):
        cmps, stt = stamp(sg, patches, zones, pad_keepouts, prot["pieces"],
                          prot["discs"], placed_objects=placed,
                          poi_keepouts=poi_keepouts, batter_m=batter_m,
                          foreign_at=foreign_at, road_claim_at=road_claim_at,
                          foreign_road_at=foreign_road_at,
                          live_delta_at=live.delta_at)
        wlk = walkability(sg, cmps, patches, pad_keepouts,
                          prot["pieces"], prot["discs"],
                          road_clips=stt["road_clip_at"])
        return cmps, stt, wlk

    approach, comps, st, reach_solve = solve_approach(
        seg, patches, live, pad_keepouts, rasterise)
    # THE EARTHWORK'S OWN CENSUS, PRINTED BEFORE ANYTHING JUDGES IT.  The same
    # numbers are printed again at the end of a successful run; the point of
    # printing them HERE is that every refusal below returns before that line,
    # and `rim_transfer`'s refusal message ends with "the fix is in whatever
    # clipped the batter" -- while the run it refused had not said what
    # clipped the batter.  A refusal that names the remedy's location and then
    # exits without printing it costs a whole second run to answer.
    print(f"batter census: {st['samples_batter']} sample(s) written over "
          f"{st['rays']} ray(s); {st['batter_met_ground']} met ground, "
          f"{st['rays_clipped']} clipped, "
          f"{st['rays_continued']} continued past their bin for "
          f"{st['samples_batter_continued']} more sample(s), "
          f"{st['rays_without_road']} with no carriageway, "
          f"{st['stations_road_without_ray']} station(s) that had ROAD AND NO "
          f"RAY walked from their own edge for "
          f"{st['orphan_rays_walked']} more ray(s) "
          f"(+{st['stations_road_without_ray_unwalkable']} with no readable "
          f"foot); walls left worst "
          f"{st['walls_left_max_m']} m by cause {st['walls_left_by_cause']}")
    _tf = st.get("toe_feather") or {}
    print(f"toe feather: {_tf.get('feathered', 0)} batter sample(s) laid back "
          f"toward natural ground over {_tf.get('passes', 0)} relaxation "
          f"step(s), worst {_tf.get('worst_m', 0.0)} m at "
          f"{_tf.get('worst_at')}, {_tf.get('earth_given_back_m', 0.0)} m of "
          f"height given back in total. The plane alone spends the WHOLE "
          f"0.7813 m step budget on its own descent, so a toe that ends on "
          f"ground with any fall of its own leaves a step nothing clipped "
          f"and nothing reported -- see TOE_FEATHER_DEPENDS_ON.")
    _rc = st.get("rim_closure") or {}
    print(f"rim closure: {_rc.get('cells', 0)} cell(s) no ray owned moved "
          f"into the interval their written neighbours allow, over "
          f"{_rc.get('rounds', 0)} ring(s), worst move "
          f"{_rc.get('worst_move_m', 0.0)} m at {_rc.get('worst_at')}; "
          f"{_rc.get('unclosable', 0)} caused cell(s) UNCLOSABLE inside their "
          f"own band and the gates -- see RIM_CLOSURE_DEPENDS_ON")
    print(f"another road's batter walked as ground: "
          f"{st['foreign_batter_written']} cell(s) laid inside its band, "
          f"{st['foreign_batter_out_of_band']} left because this plane "
          f"leaves it (worst {st['foreign_batter_out_of_band_worst_m']} m at "
          f"{st['foreign_batter_out_of_band_at']}, clipped shared_corridor) "
          f"-- see FOREIGN_BATTER_DEPENDS_ON")
    # AND WHAT THE CAP COST, SEPARATELY FROM WHAT THE HILLSIDE COST.  A
    # `reach` count on its own cannot be acted on: see REACH_EXIT_DEPENDS_ON.
    if st["batter_clipped_reach"] or st["batter_clipped_reach_not_closing"]:
        need = st["reach_needed_m"]
        need_p50 = (round(float(np.percentile(need, 50)), 2) if need else None)
        print(f"batter reach exits: {st['batter_clipped_reach']} ray(s) STILL "
              f"CLOSING when the {BATTER_MAX_M} m cap stopped them (the cap "
              f"binds; the run they needed from the carriageway edge is p50 "
              f"{need_p50} m, worst {st['reach_needed_max_m']} m at "
              f"{st['reach_needed_at']}), "
              f"{st['batter_clipped_reach_not_closing']} ray(s) that had "
              f"closed NOTHING by that cap -- more reach on one of those "
              f"moves more earth and leaves a taller wall further out. That "
              f"count is a measurement OF THIS CAP and not of the hillside: "
              f"see REACH_EXIT_DEPENDS_ON, where 99 of them went to 0 between "
              f"10.5 and 24.0 m")
    # THE RIBBON BESIDE ITSELF.  Printed unconditionally when it happens,
    # because the steps it leaves arrive at `rim_transfer` with an EMPTY wall
    # cause list -- the batter's success exit over ground it was never asked
    # to grade.
    if st["self_corridor_two_claim_cells"]:
        print(f"ribbon beside itself: "
              f"{st['self_corridor_two_claim_cells']} of "
              f"{st['self_corridor_inband_cells']} in-band cell(s) carry a "
              f"SECOND claim from this same polyline; "
              f"{st['self_corridor_reconciled']} batter sample(s) RECONCILED "
              f"into the joint band "
              f"({st['self_corridor_reconciled_raised']} raised, "
              f"{st['self_corridor_reconciled_lowered']} lowered, worst move "
              f"{st['self_corridor_reconciled_worst_m']} m), and "
              f"{st['self_corridor_cells']} cell(s) REFUSED because the two "
              f"bands do not meet -- worst {st['self_corridor_worst_m']} m at "
              f"{st['self_corridor_worst_at']} "
              f"{st['self_corridor_worst_pair']}, of which "
              f"{st['self_corridor_carriageway_cells']} are CARRIAGEWAY (a "
              f"paved cell has a band of zero width, so the only remedy is a "
              f"route) and {st['self_corridor_refused_samples']} batter "
              f"sample(s) were withheld over them. The refusal is a ROUTING "
              f"fact and has no ground term in it: "
              f"|y1 - y2| - {round(BATTER_GRADE, 4)} * (run1 + run2). See "
              f"BATTER_TARGET_DEPENDS_ON.")
    print(f"approach grading: {approach['claims']} stations claimed by "
          f"{approach.get('claims_by_cause', {})}"
          + (f" owners {approach['owners']}" if approach.get("owners") else "")
          + f"; regraded {approach['nodes_regraded']} profile nodes "
          f"(worst move {approach.get('max_profile_move_m')} m); step into the "
          f"claim {approach['worst_step_before_m']} -> "
          f"{approach['worst_step_after_m']} m")
    print(f"approach reach SOLVED against the clamp: "
          f"{reach_solve.get('reaches_tried')} candidate(s) between "
          f"{reach_solve.get('shortest_reach_m')} and "
          f"{reach_solve.get('derived_reach_m')} m, chose "
          f"{reach_solve.get('chosen_m')} m "
          f"(ramp grade {approach.get('ramp_grade')}, 8 m gradient "
          f"{reach_solve.get('chosen_g8')}); {reach_solve.get('why')}")
    for r in (reach_solve.get("rows") or []):
        print(f"     reach {r['reach_m']:>7} grade {r['ramp_grade']:<7} "
              f"over_clamp {r['over_clamp']:<4} fill {r['max_fill_m']:<7} "
              f"g8 {r['g8']:<7} pad_step {str(r.get('worst_pad_step_m')):<7} "
              f"({r.get('pads_over_batter_grade')} over {BATTER_GRADE:.3f}) "
              f"{r['verdict']}")
    for c in approach.get("conflicts", [])[:5]:
        print("   CONFLICT", c)
    # THE SAMPLES THIS WRITE WROTE, snapshotted BEFORE the union carries
    # anybody else's in.  Without it every neighbour's wall is attributed to
    # this segment -- measured: T3's carriageway edge reads as a 3.676 m wall
    # in T12's own cross section at lat -6.5, because T3 runs 6-10 m from T12
    # near the temple.
    mine = {(z, i) for z, comp in comps.items()
            for i in range(tcdata.SAMPLES) if comp.modified_height[i]}

    # ---- RULE 2, MAIN'S RULING: REFUSE, DO NOT CLAMP -------------------
    #
    # NO AUTHORED SAMPLE MAY LAND INSIDE ANOTHER CLAIM'S WRITTEN FOOTPRINT.
    # A road may grade into a pad's EDGE -- that is the approach grading, and
    # it is required -- and it may never author a sample the pad wrote.  The
    # footprint is read from the pad's OWN record's blob, per sample index,
    # exactly as `foreign_at` reads it and for the same reason: a pad's
    # written extent is not its nominal `pad_radius_m`, and the T4 clobber was
    # measured OUTSIDE the road's keep-out circle.
    #
    # `refusal` already rejects such a sample one at a time, so this cannot
    # fire while the ownership above is rewound -- which is the point.  It is
    # the GUARD that makes the rewind safe rather than merely correct: if a
    # future caller hands this function a laundered surface, or the rewind is
    # removed, or a new pad lands between the plan and the write, the result
    # is a LOUD REFUSAL naming the count and the owning claim instead of a
    # silently regraded foundation.  Nothing is clamped: a road that cannot be
    # written without authoring somebody's floor is a road to re-plan.
    authored_in_a_claim: dict[str, list] = {}
    for (zx, zz), comp in comps.items():
        cx0, cz0 = tcdata.zone_centre(zx, zz)
        for i in range(tcdata.SAMPLES):
            if not comp.modified_height[i]:
                continue
            gy, gx = divmod(i, tcdata.PITCH)
            wx, wz = tcdata.sample_world(cx0, cz0, gx, gy)
            # THE AUTHOR, AND FROM EVERY ZONE LATTICE THAT HOLDS THE SAMPLE.
            # Reading this zone's `owner` array asked two wrong questions at
            # once and MEASURED both wrong on S1-portalhub-brgs2: it refused
            # 156 samples to `portal-hall#801` including (-302, 212), which
            # is outside the hall's 37.4 x 37.4 m rectangle entirely and is
            # S1's own carriageway that the hall's blob carried forward
            # verbatim.  `holders` answers per WORLD sample across the
            # adjacent lattices (the 65 x 65 boundary row is shared, so one
            # square metre sits in two compilers) and now answers with the
            # AUTHOR, so a carried copy is never mistaken for a claim.
            foreign = [w for w in live.holders(int(round(wx)), int(round(wz)))
                       if w["role"] != appliedmod.ROAD_ROLE]
            for w in foreign[:1]:
                authored_in_a_claim.setdefault(
                    f"{w['name']}#{w['seq']} ({w['role']})", []).append(
                        [round(wx, 1), round(wz, 1)])
    if authored_in_a_claim:
        for owner, pts in sorted(authored_in_a_claim.items()):
            print(f"   AUTHORED INSIDE A CLAIM: {len(pts)} samples owned by "
                  f"{owner}, first at {pts[:6]}")
        print(f"REFUSING: this write authors "
              f"{sum(len(p) for p in authored_in_a_claim.values())} samples "
              f"inside {len(authored_in_a_claim)} other claim's written "
              f"footprint. A road grades into a pad's EDGE and never authors a "
              f"sample inside it -- inside a settlement pad the pad wins, "
              f"because it is a foundation. Nothing has been sent. Re-plan the "
              f"segment or have the pad's owner re-emit; do not clamp.")
        return 6

    # ---- THE RIM: WHO OWNS THE GROUND AT THE EDGE OF WHAT THIS WRITE TAKES
    #
    # MEASURED, by the write that found it.  `LandT3T4` emitted the closure of
    # T3-temple-meadhall to close a 2.113 m step at (32,-10) -- a RESTITUTION,
    # T3 re-asserting a sample T12 had re-authored fourteen minutes after it.
    # Every gate in this file passed.  `junction.clean_order` returned
    # `clean: true, samples_changing_owner: 0`, which was HONEST AND COMPLETE
    # ON ITS OWN TERMS.  The gate then went from 11/28 destinations walkable to
    # 13/28: it OPENED meadhall, stenvik, t8junction, ulfsgard and wttown, and
    # it SEVERED treesth, wtsouth and wtspawn on a new link
    # (28,-12) -> (28,-11), 2.113 m, bed 67.040 -> 69.153 -- THE IDENTICAL
    # METRE AND THE IDENTICAL TWO BEDS AS THE STEP THAT WAS CLOSED.  The cliff
    # moved four metres along T12's own centreline to the first sample OUTSIDE
    # the reclaimed set.  Replayed through this check afterwards, that write
    # caused 217 rim steps over 104 DISTINCT rim samples, worst 2.7655 m: the
    # three destinations were not one unlucky vertex, they were the whole rim.
    #
    # WHY NO EXISTING GATE SAW IT, and each one is answering its real question
    # correctly.  `junction.build_gate` compares PLANS, and T3's plan and T12's
    # AGREE at (28,-12) to the byte -- T12's own node 62 reads 69.153.
    # `clean_order` closes over DISPUTED samples, and a sample nobody disagreed
    # about is not in the closure.  `walkability` walks THIS segment's
    # centreline and (28,-12) is not on it.  `transverse_census` disclaims a
    # pair whose only written sample belongs to somebody else -- correctly,
    # since a segment cannot re-emit its neighbour.  `delta_gate` measures
    # GENERATED location content within 60 m.  Five right answers to five other
    # questions, and the ground between them belonged to nobody.
    #
    # SINGLE POPULATION, which is what makes it survive a fresh seed: the taken
    # set is this write's own emission, the rim is derived from it, and both
    # heights come from the patches this run already scanned plus the ledger's
    # own blobs.  No second world, no prior save, no reference artefact.
    taken_after: dict[tuple[int, int], float] = {}
    taken_now: dict[tuple[int, int], float] = {}
    taken_gen: dict[tuple[int, int], float] = {}
    rim_gen_missing = 0
    for (zx, zz), comp in comps.items():
        cx0, cz0 = tcdata.zone_centre(zx, zz)
        for i in range(tcdata.SAMPLES):
            if not comp.modified_height[i]:
                continue
            gy, gx = divmod(i, tcdata.PITCH)
            wx, wz = tcdata.sample_world(cx0, cz0, gx, gy)
            sx, sz = int(round(wx)), int(round(wz))
            gen = appliedmod.generated_at(patches, float(sx), float(sz))
            if gen is None:
                rim_gen_missing += 1
                continue
            after = gen + delta_at(comps, float(sx), float(sz))
            now = gen + live.delta_at(float(sx), float(sz))
            # OWNERSHIP THAT CHANGES HANDS WITHOUT MOVING THE GROUND CANNOT
            # STEP ANYTHING, so the taken set is the samples this write MOVES.
            # Using "every sample the compiler holds" would put the whole
            # carried-forward union in here and put the rim a zone away.
            if abs(after - now) > appliedmod.AUTHOR_TOLERANCE_M:
                taken_after[(sx, sz)] = after
                taken_now[(sx, sz)] = now
                taken_gen[(sx, sz)] = gen

    _gcache: dict[tuple[int, int], float | None] = {}

    def _gen_at(sx: int, sz: int):
        k = (sx, sz)
        if k not in _gcache:
            _gcache[k] = appliedmod.generated_at(patches, float(sx), float(sz))
        return _gcache[k]

    def _height_now(sx: int, sz: int):
        g = _gen_at(sx, sz)
        return None if g is None else g + live.delta_at(float(sx), float(sz))

    def _height_after(sx: int, sz: int):
        """The applied height ONCE THIS WRITE HAS LANDED.

        Not `_height_now`: the rim is derived from `taken`, which is "moved by
        more than AUTHOR_TOLERANCE" and is therefore a SUBSET of "written by
        this write".  A rim sample this write nudges by millimetres still
        moves, and the walker stands on the moved ground.  And not
        `delta_at(comps)` for a whole emitted zone either: the carried-forward
        union is applied AFTER this gate, so here `comps` is this write alone
        -- see RIM_AFTER_SURFACE_DEPENDS_ON for the T8 phantom that read.
        """
        future = pad_keepouts.finished_y(sx, sz)
        if future is not None:
            return future
        return rim_height_after(sx, sz, _gen_at(sx, sz), comps,
                                live.delta_at)

    def _author_now(sx: int, sz: int):
        _d, w = live.sample(sx, sz, by="author")
        return None if w is None else f"{w.get('name')}#{w.get('seq')}"

    rim = junctionmod.rim_transfer(taken_after, _height_now, _author_now,
                                   height_after=_height_after,
                                   # EVERY caused row: each is given its own
                                   # owner below, and a row the list cut off
                                   # is an owner nobody read.
                                   top=max(40, 8 * len(taken_after)))
    # A CAUSED STEP WHOSE RIM IS A PAD THE BUILD LAYS NEXT is judged against
    # that pad's datum -- see PLANNED_PAD_RIM_DEPENDS_ON.  The rows owed are
    # recorded on the write (`rim.owed_to_planned_pads`), not dropped.
    _rim_kept, rim_owed = planned_pad_owed(rim["caused_rows"], pad_keepouts,
                                           junctionmod.SLIDE_PER_M)
    if rim_owed:
        _left = rim["caused"] - len(rim_owed)
        rim = {**rim, "caused_rows": _rim_kept, "caused": _left,
               "verdict": "REFUSED" if _left else "CLEAR",
               "worst_caused_m": (_rim_kept[0]["step_after_m"]
                                  if _rim_kept else 0.0),
               "worst_caused_at": (_rim_kept[0]["rim_xz"]
                                   if _rim_kept else None)}
        _by_pad: dict[str, int] = {}
        for _r in rim_owed:
            _by_pad[_r["planned_pad"]] = _by_pad.get(_r["planned_pad"], 0) + 1
        _w = max(rim_owed, key=lambda r: r["step_after_pad_m"])
        print(f"rim owed to planned pads: {len(rim_owed)} caused step(s) "
              f"stand inside a pad the build lays next {_by_pad}; against "
              f"its datum the worst is {_w['step_after_pad_m']} m at "
              f"{_w['rim_xz']} ({_w['step_after_m']} m on today's ground) "
              f"-- see PLANNED_PAD_RIM_DEPENDS_ON")
    # WHAT THIS WRITE WAS REFUSED, PER LATTICE SAMPLE -- see
    # RIM_STEP_OWNER_DEPENDS_ON.
    refused_at = refused_at_of(st)
    # EVERY PLAN IN THIS RUN'S SEGMENTS FILE (`plans_by_write`, built above
    # for the rasteriser), so an authored rim can be asked whether the PLANS
    # step across it.
    plan_arrays: dict = {}

    def _plan_step(row: dict):
        theirs = plans_by_write.get(
            (row.get("rim_authored_by") or "").split("#")[0])
        if theirs is None:
            return None
        mine_y = carriageway_plan_y(seg, *row["taken_xz"], plan_arrays)
        their_y = carriageway_plan_y(theirs, *row["rim_xz"], plan_arrays)
        if mine_y is None or their_y is None:
            return None
        return abs(mine_y - their_y)

    labels = [rim_step_owner(r, refused_at, _plan_step,
                             junctionmod.SLIDE_PER_M)
              for r in rim["caused_rows"]]
    by_owner: dict[str, dict] = {}
    for r, lab in zip(rim["caused_rows"], labels):
        o = by_owner.setdefault(lab, {"steps": 0, "worst_m": 0.0, "at": None})
        o["steps"] += 1
        if r["step_after_m"] > o["worst_m"]:
            o["worst_m"], o["at"] = r["step_after_m"], r["rim_xz"]
    print(f"rim transfer: {rim['verdict']} -- this write MOVES "
          f"{rim['taken_samples']} lattice sample(s); their rim is "
          f"{rim['rim_samples']} sample(s), {rim['rim_pairs_judged']} pair(s) "
          f"judged, {rim['rim_samples_unreadable']} unreadable"
          + (f" (+{rim_gen_missing} moved sample(s) with no generated height)"
             if rim_gen_missing else "")
          + f"; {rim['caused']} step(s) CAUSED by this write, "
          f"{rim['pre_existing']} pre-existing")
    for r, lab in list(zip(rim["caused_rows"], labels))[:8]:
        print(f"   CAUSED {r['step_after_m']} m at rim {r['rim_xz']} from "
              f"taken {r['taken_xz']} ({r['span']}), was "
              f"{r['step_before_m']} m, rim ground owned by "
              f"{r['rim_authored_by']} -> {lab}")
    for k, o in sorted(by_owner.items(), key=lambda kv: -kv[1]["steps"]):
        print(f"   BY OWNER {k}: {o['steps']} step(s), worst {o['worst_m']} m "
              f"at {o['at']}")
    if len(rim["caused_rows"]) != rim["caused"]:
        # UNREAD IS NOT CLEAR: an owner table over a truncated row list is a
        # table that omits owners.
        unread = rim["caused"] - len(rim["caused_rows"])
        print(f"REFUSING: rim_transfer returned {len(rim['caused_rows'])} of "
              f"{rim['caused']} caused rows, so {unread} step(s) have no "
              f"owner. Nothing has been sent.")
        return 7
    if rim["verdict"] == "REFUSED":
        owners = sorted({k[len("claim "):] for k in by_owner
                         if k.startswith("claim ")})
        claimed = sum(o["steps"] for k, o in by_owner.items()
                      if k.startswith("claim "))
        disagree = {k[len("plans "):]: o for k, o in by_owner.items()
                    if k.startswith("plans ")}
        refused = {k[len("refused "):]: o for k, o in by_owner.items()
                   if k.startswith("refused ")}
        residue = by_owner.get("earthwork")
        # TWO DIFFERENT REFUSALS WEARING ONE SENTENCE, and the difference is
        # whether anybody owns the rim.  With owners the remedy is real:
        # re-emit them after this segment and the step closes.  With an EMPTY
        # owner list the same sentence says "pull [] into the closure", which
        # prescribes a no-op -- a refusal whose named remedy cannot be
        # performed reads as ACTIONABLE and is not.
        #
        # MEASURED 2026-09-24, the first from-scratch build this tree has
        # driven: Fresh42 stage 12 step 4, `T1-portalhub-temple`, 601 CAUSED
        # steps over a rim owned by NOBODY (the world holds zero
        # `terrain_write` records of any kind).  Every taken sample lies
        # within 3.75 m of the centreline and every caused rim sample at
        # 4.03-5.16 m, against a segment that declares "6.0 m wide + 1.0 m
        # shoulder + 10.5 m BATTER REACH" -- so the cut face stands where the
        # shoulder ends and the batter that exists to lay it down was emitted
        # nowhere on this segment.  It is not the placed-object gate either:
        # with that population emptied the count falls 601 -> 505 and the
        # worst step is unchanged at 5.1433 m.
        # ONE SENTENCE PER OWNER, each over ITS OWN steps.  The sentences
        # below used to be chosen by one bit for the whole write -- see
        # RIM_STEP_OWNER_DEPENDS_ON.
        print(f"REFUSING: this write would leave {rim['caused']} step(s) a "
              f"walker cannot take AT THE RIM of the ground it takes, worst "
              f"{rim['worst_caused_m']} m at {rim['worst_caused_at']} against "
              f"a {junctionmod.SLIDE_PER_M:.4f} m limit. Nothing has been "
              f"sent. Do not waive it segment-wide; a waiver here belongs per "
              f"rim sample, pinned to its measured metre.")
        for who, o in sorted(disagree.items()):
            print(f"  {o['steps']} step(s), worst {o['worst_m']} m at "
                  f"{o['at']}, stand where THIS PLAN'S CARRIAGEWAY ABUTS "
                  f"{who}'s CARRIAGEWAY and the two PLANS step more than the "
                  f"limit across that edge: a JUNCTION disagreement that "
                  f"`junction.build_gate` cannot see, because it compares "
                  f"only ground both plans pave. Re-emitting either write "
                  f"lays the same step back; the remedy is the PLANS.")
        if owners:
            print(f"  {claimed} step(s) stand on ground OWNED by {owners} "
                  f"where the plans do not step across the edge (or one "
                  f"side is earthwork, not carriageway): pull those into "
                  f"the closure and re-emit them AFTER this segment, which "
                  f"is what `clean_order` would have done had the sample "
                  f"ever been disputed.")
        for causes, o in sorted(refused.items()):
            print(f"  {o['steps']} step(s), worst {o['worst_m']} m at "
                  f"{o['at']}, stand on ground nobody owns where THIS WRITE'S "
                  f"BATTER WAS REFUSED ({causes}): the earthwork is not "
                  f"allowed to close them, so neither a closure nor this "
                  f"file's geometry can. The remedy is what refused it -- "
                  f"for water, relief and the ribbon beside itself that is "
                  f"the ROUTE (the profile or the alignment), for a keep-out "
                  f"it is the keep-out's owner.")
        if residue:
            print(f"  {residue['steps']} step(s), worst {residue['worst_m']} "
                  f"m at {residue['at']}, stand on ground NOBODY OWNS and "
                  f"where nothing refused the batter: the earthwork's OWN "
                  f"face on virgin ground, and this file's to close. WHAT "
                  f"THE BATTER ACTUALLY DID HERE, MEASURED THIS RUN rather "
                  f"than asserted: {st['samples_batter']} sample(s) over "
                  f"{st['rays']} ray(s), {st['batter_met_ground']} met "
                  f"ground, {st['rays_clipped']} clipped, "
                  f"{st['rays_continued']} continued past their bin; walls "
                  f"left worst {st['walls_left_max_m']} m by cause "
                  f"{st['walls_left_by_cause'] or '{}'}. Re-emitting, "
                  f"re-ordering and waiving are all the wrong tool. DO NOT "
                  f"WAIVE: a waiver here would put the cliff in the world, "
                  f"which is the defect this whole gate exists to keep out "
                  f"of a fresh seed.")
        return 7

    arrivals = pad_keepouts.arrivals(seg, _height_after)
    st["arrival_failures"] = arrivals
    if arrivals:
        print("REFUSING: the finished approach to a declared pad is not "
              f"walkable: {arrivals}. Nothing has been sent.")
        return 7

    # ---- THE WATER SIGN: A FILL OVER A SUBMERGED SAMPLE REMOVES WATER ----
    #
    # `_flatten_forbidden_check`'s sibling, and the reason it is a SIGN test
    # and not a magnitude one is the whole finding.  MEASURED by
    # `CloseHarbours` on Ulfsland 2026-09-22: the LittleBoat berth at
    # (-307.04, -119.59) has 38 of 38 footprint samples not connected to
    # open sea and 14 of 2,340 submerged samples RAISED TO OR ABOVE the
    # 30 m plane, 56 m2 of water gone -- and the nearest recorded terrain
    # write is `road_S12_brgs1_boathouse` at 12.8 m, nearer than the next by
    # a factor of three.  A road filled a harbour and nothing in this file
    # asked.
    #
    # Ground the SEED drew below the water plane is WATER.  Raising it to or
    # above the plane does not make a shallow bay, it DELETES the bay, and
    # no later write can put it back: the generated field is the only record
    # that it was ever there.  A CUT is always permitted -- lowering a bed
    # adds water, and a guard that refused all ground movement near water
    # would take every legitimate dredge with it.
    #
    # The population is this write's own taken set, the same set the rim
    # gate judged, so the two cannot disagree about what this write moves.
    #
    # AND `taken_now` GOES IN, because the rule is a DELTA.  It was computed
    # at line 4325 and used two lines below this call to report
    # `raised_by_m` while the verdict itself was taken on the absolute
    # result -- so the report quoted a delta and the gate judged a state.
    # Over ground a PRIOR write already drained, that refused a CUT: T9's
    # bank A stands at 31.000 on a seed of 29.067, and lowering it to the
    # 30.600 deck datum gives back 0.400 m of the 1.933 m seq 234 took.
    wsign = water_sign(taken_gen, taken_after, taken_now)
    drains = [dict(xz=[k[0], k[1]], seed=round(taken_gen[k], 3),
                   now=round(taken_now[k], 3),
                   after=round(taken_after[k], 3),
                   raised_by_m=round(taken_after[k] - taken_now[k], 3))
              for k in wsign["drained"]]
    drains.sort(key=lambda r: -r["raised_by_m"])
    print(f"water sign: {wsign['verdict']} -- "
          f"{wsign['submerged_samples']} of {wsign['moved_samples']} moved "
          f"sample(s) sit on ground the SEED drew below the "
          f"{wsign['plane_m']} m plane; {wsign['drained_samples']} of those "
          f"would be RAISED to or above it by this write "
          f"({wsign['already_drained_not_deepened']} were already above it "
          f"and are not deepened by this write, "
          f"{wsign['submerged_samples'] - wsign['drained_samples'] - wsign['already_drained_not_deepened']} "
          f"stay under); judged as a {wsign['judged_as']}")
    for k in wsign["already_drained_xz"][:8]:
        print(f"   already drained, NOT deepened {list(k)}: seed "
              f"{taken_gen[k]:.3f}, now {taken_now[k]:.3f} -> "
              f"{taken_after[k]:.3f} ({taken_after[k] - taken_now[k]:+.3f} m)")
    for r in drains[:8]:
        print(f"   DRAINS {r['xz']} seed {r['seed']} now {r['now']} -> "
              f"{r['after']} ({r['raised_by_m']:+.3f} m)")
    if wsign["verdict"] == "REFUSED":
        print(f"REFUSING: this write would raise {wsign['drained_samples']} "
              f"of {wsign['submerged_samples']} submerged sample(s) to or "
              f"above the {wsign['plane_m']} m water plane, removing "
              f"{wsign['drained_samples']} m2 of water PERMANENTLY -- the "
              f"generated field is the only record that water was ever "
              f"there, so no later write can restore it. Worst at "
              f"{drains[0]['xz']}: seed {drains[0]['seed']}, this write "
              f"{drains[0]['after']}. Nothing has been sent. The remedy is "
              f"to STOP THE RIBBON AT THE BANK, not to narrow the batter: a "
              f"causeway over water is a deck, and Crossings owns it.")
        return 8

    # ---- WOULD THIS WRITE BURY OR UNDERMINE SOMETHING STANDING? ----------
    #
    # `accept/instruments.py::regrade_census`, over a PER-SAMPLE SURFACE.
    # It has existed for a while and EVERY SETTLEMENT WRITE IN THIS WORLD
    # HAS BEEN THROUGH IT AND NOT ONE ROAD WRITE EVER HAD, because the only
    # shape it took was the pad's single datum -- and a ribbon has no datum,
    # it stamps a different height at every lattice sample.  A question that
    # can only be asked of half the writes is not a gate, and this file
    # moves more ground in one segment than a night of pads.
    #
    # DO NOT COLLAPSE THE PROFILE TO A DATUM.  `target` is `taken_after` --
    # the actual height this write leaves at each sample it authors -- and
    # the mean of a road profile answers CLEAR over a write that buries a
    # piece by a metre at one end and undermines one at the other.
    #
    # BOTH SIGNS.  A fill that reaches a solid's top BURIES it; a cut that
    # leaves its foot more than one walker step in the air UNDERMINES it.
    # "A cut cannot bury anything, so it is safe" is true and insufficient.
    if args.validate or args.dry:
        print("regrade census: SKIPPED -- --validate/--dry does not open the "
              "save, and a census taken off an unflushed world is a reading "
              "of an earlier one")
    else:
        import accept.instruments as AI                    # noqa: PLC0415
        import accept.sections.structures as AS            # noqa: PLC0415
        from types import SimpleNamespace                  # noqa: PLC0415
        wsrc = AI.world_save(str(WORLD))
        # THE SAVE THE CENSUS READS MUST BE THE WORLD THIS LEDGER IS ABOUT.
        # The name check passes for any world called `{WORLD}`; on the
        # from-scratch Ulfsland regeneration it passed for the LIVE world's
        # save (a different uid) and 19 legs were refused for burying pieces
        # that exist only there.  See `accept.instruments._SAVE_IDENTITY_MODE`.
        rg_identity = AI.require_save_is_the_ledgers(wsrc, str(WORLD))
        print(f"regrade census save: {wsrc} ({rg_identity} against the "
              f"ledger's world manifest)")
        rg_fp = AI.save_fingerprint(wsrc)
        rg_cache = Path(AI.CACHE_DEFAULT) / str(WORLD)
        (rg_cache / rg_fp).mkdir(parents=True, exist_ok=True)
        snap = AI.snapshot(wsrc, rg_cache / rg_fp / "world")
        cen = AI.census(rg_cache, rg_fp,
                        SimpleNamespace(vh_src=AI.VH_SRC_DEFAULT,
                                        world_src=str(snap), world=str(WORLD),
                                        sandbox=AI.SANDBOX_DEFAULT))
        rg_geom, rg_build = AI.load_geometry()
        rect = sorted(taken_after)
        tgt = {(float(x), float(z)): v for (x, z), v in taken_after.items()}
        gnd = {AS.gkey(float(x), float(z)): v
               for (x, z), v in taken_now.items()}
        xs = [p[0] for p in rect] or [0]
        zs = [p[1] for p in rect] or [0]
        x0, x1 = min(xs) - 8.0, max(xs) + 8.0
        z0, z1 = min(zs) - 8.0, max(zs) + 8.0
        solids = [s for s in AI.solids_from_census(cen, rg_geom)
                  if s.name in rg_build and s.verts
                  and s.xmax >= x0 and s.xmin <= x1
                  and s.zmax >= z0 and s.zmin <= z1]
        rg = AI.regrade_census([(float(x), float(z)) for (x, z) in rect],
                               tgt, gnd, solids, rg_geom, AS.Columns(rg_geom),
                               AS.gkey, AS.piece_columns)
        print(f"regrade census: {rg['verdict']} over {rg['rect_samples']} "
              f"sample(s) this write authors, of which {rg['moving']} MOVE "
              f"({rg['fills']} fill / {rg['cuts']} cut); target is a "
              f"{rg['target_kind']}; {rg['samples_without_a_target']} "
              f"sample(s) with no target; "
              f"{rg['solids_over_a_moving_sample']} build-piece solid(s) "
              f"cover a moving sample, {rg['ground_bearing']} ground-bearing, "
              f"{rg['aloft_and_not_in_the_question']} carried by something "
              f"else; {len(rg['buries'])} buries / {len(rg['undermines'])} "
              f"undermines; census {len(cen.rows)} ZDOs at save {rg_fp[:8]}, "
              f"{len(solids)} build pieces in the window")
        for r in rg["buries"][:6]:
            print(f"   BURIES {r['prefab']} at {r['at']} span {r['span']} "
                  f"ground {r['ground']} -> {r['target']} "
                  f"({r['over_its_top_m']:+.3f} m over its top)")
        for r in rg["undermines"][:6]:
            print(f"   UNDERMINES {r['prefab']} at {r['at']} span "
                  f"{r['span']} ground {r['ground']} -> {r['target']} "
                  f"({r['foot_in_air_m']:.3f} m of foot in the air)")
        if rg["verdict"] != "CLEAR":
            print(f"REFUSING: this write would bury {len(rg['buries'])} and "
                  f"undermine {len(rg['undermines'])} standing build "
                  f"piece(s). Nothing has been sent. Retire and re-spawn the "
                  f"pieces, or re-plan the profile; do not clamp the road "
                  f"around them and do not delete a building.")
            return 9
    # THE CLIP CENSUS TRAVELS WITH THE VERDICT.  `clear` because nothing came
    # near a location and `clear` because the batter dropped nine samples at
    # one are different statements about the same road, and the ledger is the
    # only place the operator can tell them apart.
    clip_census = {c: n for c, n in st["walls_left_by_cause"].items() if n}
    loc = location_check(comps, st["written"],
                         road_written=st["road_written"],
                         clipped=clip_census,
                         road_half_width_m=seg["width_m"] / 2.0)
    print(f"location check: verdict={loc['verdict']} nearest="
          f"{(loc['nearest'] or {}).get('name')} standoff {loc['standoff_m']} m "
          f"over {loc['samples_tested']} CARRIAGEWAY samples "
          f"(delta gate saw {loc['samples_in_delta_gate']}), "
          f"{len(loc['violations'])} distance violations; delta gate "
          f"{loc['delta_gate']['verdict']} worst "
          f"{(loc['delta_gate']['worst'] or {}).get('worst_abs_delta_m')} m over "
          f"{loc['delta_gate']['instances_probed']} instances; "
          f"batter clipped {loc['clipped_total']} samples {loc['clipped']}")
    if loc["verdict"] != "clear":
        for v in loc["violations"] + loc.get("delta_gate_violations", []):
            print("   VIOLATION", v)
        print("REFUSING: a CARRIAGEWAY sample is inside a location's hard "
              "radius, or a location piece's ground moves past the delta "
              "tolerance. The batter clips at this same boundary and costs a "
              "recorded wall; the road surface cannot be clipped away, because "
              "a dropped carriageway sample is a hole the operator falls into. "
              "Re-route the leg; do not widen the budget.")
        return 3
    print(f"stamped {st['samples_paved']} paved + {st['samples_shoulder']} "
          f"shoulder + {st['samples_batter']} batter samples over "
          f"{len(comps)} zones; "
          f"cut {st['max_cut_m']} fill {st['max_fill_m']} m; skipped "
          f"{st['skipped_pad']} inside a Settlements pad, "
          f"{st['skipped_protected']} beside a protected piece, "
          f"{st['skipped_underwater']} on a protected structure's water")
    print(f"batter clipped: {st['batter_clipped_pad']} at a pad, "
          f"{st['batter_clipped_protected']} at a protected piece, "
          f"{st['batter_clipped_underwater']} on protected water, "
          f"{st['batter_clipped_water_edge']} at a water edge (below "
          f"{WATER_LEVEL_M} m), {st['batter_clipped_poi']} inside a POI "
          f"stand-off, {st['batter_clipped_placed']} beside a live placed "
          f"object (grade {st['batter_grade']}, reach {st['batter_max_m']} m)")
    print(f"WALLS LEFT STANDING: worst {st['walls_left_max_m']} m, by cause "
          f"{st['walls_left_by_cause']}")
    for w in st["walls_left"][:10]:
        print("   ", w)
    print(f"samples past the MEASURED +/-8 m apply clamp: {st['over_clamp']}")
    for s in st["over_clamp_samples"]:
        print("   ", s)
    if st["over_clamp"]:
        print("REFUSING: a sample past the clamp is road that will not be where "
              "the plan says. Re-plan the segment.")
        return 2

    # UNION WITH ANY EARLIER TERRAIN WRITE TO THE SAME ZONE.
    #
    # A zone holds exactly ONE `_TerrainCompiler`
    # (`Heightmap::GetAndCreateTerrainCompiler` returns the first it finds, so a
    # second is dead weight), and this op does `deleteObjects -zone` before it
    # spawns -- so writing a zone someone already wrote DESTROYS their terrain.
    # On a 33 km network crossing 470 zones alongside 50 building pads that is
    # the normal case, not a corner case, which is why the ledger REFUSES it
    # unless the earlier write is named.  MEASURED here: T3-temple-meadhall
    # shares five zones with T12-wtspawn-temple, built minutes earlier at
    # seq 181, and the guard refused rather than quietly erasing 2,051 samples
    # of finished road.
    #
    # The merge is per SAMPLE INDEX: every sample the earlier blob wrote and
    # this one does not is carried forward verbatim, both its height delta and
    # its paint. Samples both wrote are MINE -- last writer wins per sample --
    # and in practice that set is empty, because two ribbons meeting at a node
    # share a zone but not a square metre, and a ribbon skips everything inside
    # a settlement pad radius. The union is recorded per entry so a replay
    # reproduces the same bytes in the same order.
    prior_by_zone: dict[tuple[int, int], list[dict]] = {}
    # RUN UNCONDITIONALLY, INCLUDING UNDER `--validate`, and the reason is a
    # measured lie.  This block used to be skipped when validating, so the
    # record the pre-flight validated was NOT the record the live path builds:
    # the live one carries every prior claim's samples unioned into its own
    # blob.  MEASURED on T4 -- the offline pad-guard check PASSED and
    # `Ledger.append` then REFUSED the same write for 59 authored samples
    # inside three Stenvik pads, because the samples it objected to were the
    # UNIONED ones the validate path had never created.  A pre-flight that
    # validates different bytes from the ones that get sent is worse than no
    # pre-flight: it converts a caught defect into a confident one.  The union
    # is a pure read of the ledger and costs nothing to run twice.
    sys.path.insert(0, str(JUMPSTART / "ledger"))
    from writer import Ledger
    led_ro = Ledger.open(WORLD, actor=ACTOR)
    for line, rec in enumerate(led_ro.records()):
        if rec.get("op") != "terrain_write":
            continue
        for e in rec["params"]["entries"]:
            z = tuple(e["zone"])
            if z in comps:
                # SEQ IS AMBIGUOUS PAST FILE LINE 47 -- the chain forked
                # tonight and the repair left duplicate seq labels, so
                # `merged_from` (which the clobber guard matches on seq, and
                # must keep matching on) is recorded alongside the FILE
                # LINE, the only total order this artefact has.  The merge
                # itself keys on the BLOB DIGEST, so an ambiguous label
                # cannot corrupt the bytes -- only the human trail.
                prior_by_zone.setdefault(z, []).append(
                    {"seq": rec["seq"], "sha": e["blob_sha256"],
                     "file_line": line,
                     "name": rec["params"].get("name")})
    for z, plist in prior_by_zone.items():
        comp = comps[z]
        carried = 0
        # THIS WRITE'S OWN SAMPLES, captured BEFORE the union fills any, so
        # the postcondition below can tell "ours" from "carried".
        ours = {i for i in range(tcdata.SAMPLES) if comp.modified_height[i]}
        # THE UNION CARRIES THE LATEST PRIOR CLAIM, NOT THE FIRST, and the
        # first version of this loop had it backwards: it stopped at the
        # first prior blob holding a sample, which on a zone with five
        # prior writes is the OLDEST.  That is Main's T4 ruling inverted --
        # it would restore `RoadBuild` seq 213's road delta over
        # `SiteFinish` seq 286's pad inside the pad's own footprint,
        # re-breaking the foundation this repair exists to respect.  So
        # the priors are resolved amongst themselves in FILE ORDER first
        # (later wins, which is what the live compiler holds), and only
        # then filled in where THIS write wrote nothing.
        merged_h: dict[int, tuple[float, float]] = {}
        merged_p: dict[int, tuple] = {}
        for pr in sorted(plist, key=lambda p: p["file_line"]):
            old = tcdata.parse(led_ro.read_blob(pr["sha"]))
            for i, (lvl, sm) in old["heights"].items():
                merged_h[i] = (lvl, sm)
            for i, col in old["paints"].items():
                merged_p[i] = col
        # NO PAD-FLOOR RESTORATION HERE, and the reason is the same ruling
        # that unblocked this write.  A previous form of this loop looked up
        # each sample's pad floor and restored it over the later claim's
        # value, because `writer._pad_footprint_check` used to judge a road
        # against EVERY pad that had ever authored a sample.  That check was
        # the defect: a superseded pad's floor is ground that NO LONGER
        # EXISTS, and two pads that authored the same square metre made it
        # unsatisfiable (T4 at (480-482, 908)).  The guard now resolves to the
        # EFFECTIVE author, so the correct union is the plain one -- FILE
        # ORDER, later wins, which is what the live compiler holds -- and
        # restoring a dead floor would now be the thing that gets refused.
        for i, (lvl, sm) in merged_h.items():
            if not comp.modified_height[i]:
                comp.modified_height[i] = True
                comp.level_delta[i] = lvl
                comp.smooth_delta[i] = sm
                carried += 1
        for i, col in merged_p.items():
            if not comp.modified_paint[i]:
                comp.modified_paint[i] = True
                comp.paint[i] = col
        # AND NOW PROVE IT.  This loop is correct and was wrong once, and
        # nothing here could tell the difference: the carried samples lie
        # outside this segment's own corridor, so its profile check, its
        # edge-step census and its own postcondition all pass over a zone
        # whose pad has just been un-levelled.  `settlements/build.py`'s
        # version of this same union WAS still oldest-wins until it was
        # measured - 6.507 m of T4's bed at (598, 967) in zone (9,15) - so
        # the check is SHARED rather than restated: one implementation,
        # asked of the FINISHED compiler against the live surface.
        appliedmod.Applied(world=WORLD, led=led_ro).assert_restores_current(
            z[0], z[1], comp, ours)
        print(f"  zone {list(z)}: unioned {carried} samples forward from "
              f"seq {[pr['seq'] for pr in plist]} "
              f"(file lines {[pr['file_line'] for pr in plist]}, "
              f"{sorted({pr['name'] for pr in plist})})")
    # `pad_levelled_yet` used to be derived here, by name-prefixing every
    # `terrain_write` against a site id.  It is now a property of the
    # keep-out itself: a foundation in `pad_claims` IS levelled and
    # carries the `target_y` it was levelled to, and a site that only
    # exists in `sites.yaml` is not and carries its declared `pad_y`.
    # Deriving it twice from two sources is how they disagree.

    # THE OPERATOR'S OWN TEST, on the surface that will exist after the union.
    # Run here rather than before the merge because a junction zone's carried
    # samples are part of the rendered mesh: at the temple the T3 ribbon meets
    # T12's, and continuity across that joint is a property of the union, not
    # of this segment alone.
    walk = walkability(seg, comps, patches, pad_keepouts,
                       prot["pieces"], prot["discs"],
                       road_clips=st["road_clip_at"])
    walk["skipped"] = {"settlement_pad": st["skipped_pad"],
                       "beside_protected_piece": st["skipped_protected"],
                       "on_protected_structure_water": st["skipped_underwater"],
                       "on_a_foreign_claim": st["skipped_foreign"]}
    walk["batter_clipped"] = {
        "settlement_pad": st["batter_clipped_pad"],
        "beside_protected_piece": st["batter_clipped_protected"],
        "on_protected_structure_water": st["batter_clipped_underwater"],
        "beside_live_placed_object": st["batter_clipped_placed"],
        "inside_poi_standoff": st["batter_clipped_poi"],
        "at_a_water_edge": st["batter_clipped_water_edge"],
        "on_a_foreign_claim": st["batter_clipped_foreign"],
        "hillside_steeper_than_batter":
            st["batter_clipped_steeper_than_batter"],
        "reach_spent_still_closing": st["batter_clipped_reach"],
        "reach_spent_hillside_outran_it":
            st["batter_clipped_reach_not_closing"],
        "reach_needed_worst_m": st["reach_needed_max_m"],
        "reach_needed_worst_at": st["reach_needed_at"],
        "ribbon_beside_itself_unabsorbable_cells": st["self_corridor_cells"],
        "ribbon_beside_itself_inband_cells":
            st["self_corridor_inband_cells"],
        "ribbon_beside_itself_two_claim_cells":
            st["self_corridor_two_claim_cells"],
        "ribbon_beside_itself_carriageway_cells":
            st["self_corridor_carriageway_cells"],
        "ribbon_beside_itself_reconciled": st["self_corridor_reconciled"],
        "ribbon_beside_itself_reconciled_raised":
            st["self_corridor_reconciled_raised"],
        "ribbon_beside_itself_reconciled_lowered":
            st["self_corridor_reconciled_lowered"],
        "ribbon_beside_itself_reconciled_worst_move_m":
            st["self_corridor_reconciled_worst_m"],
        "ribbon_beside_itself_samples_withheld":
            st["self_corridor_refused_samples"],
        "ribbon_beside_itself_worst_exceedance_m":
            st["self_corridor_worst_m"],
        "ribbon_beside_itself_worst_at": st["self_corridor_worst_at"],
        "ribbon_beside_itself_worst_pair": st["self_corridor_worst_pair"],
        "beside_live_placed_object_at": st["batter_clipped_placed_at"]}
    walk["batter_rays"] = {"rays": st["rays"], "clipped": st["rays_clipped"],
                           "met_natural_ground": st["batter_met_ground"]}
    walk["walls_left"] = {"worst_m": st["walls_left_max_m"],
                          "worst_before_m": st["walls_before_max_m"],
                          "by_cause": st["walls_left_by_cause"],
                          "listed": st["walls_left"]}
    walk["approach_grading"] = approach
    # THE SOLVE, NOT ONLY ITS ANSWER.  A chosen reach with no ladder beside it
    # is a constant again: the row per candidate is what lets a later reader
    # see that the clamp, not a derivation, picked it.
    walk["approach_reach_solve"] = reach_solve
    walk["edge_inclusive"] = {"samples_at_exactly_edge":
                              st["edge_exact_samples"],
                              "eps_m": st["edge_eps_m"],
                              "edge_m": st["edge_m"]}
    walk["protected_pieces_in_zones"] = prot["by_site"]
    walk["protected_records_without_pieces"] = prot["without_pieces"]
    # THE TRANSVERSE QUANTITY, beside the longitudinal one and never instead
    # of it.  The operator's "blocky" is this number; `max_gradient_8m` is the
    # answer to "is it too steep", which every one of these segments already
    # passed while carrying an 8 m wall.
    walk["transverse"] = edge_step_census(
        seg, comps, patches, batter_m=batter_m, mine=mine,
        clips=st["clip_at"] + st["ray_skip_at"] + st["closure_refused_at"])
    tv = walk["transverse"]
    print(f"TRANSVERSE (the operator's 'blocky'): carriageway 1 m step p50 "
          f"{tv['carriageway_step_p50']} max {tv['carriageway_step_max']}")
    print(f"  MY EARTHWORK p50 {tv['earthwork_step_p50']} p95 "
          f"{tv['earthwork_step_p95']} max {tv['earthwork_step_max']} over "
          f"{tv['earthwork_pairs']} pairs; VERDICT {tv['verdict']} against "
          f"{tv['verdict_limit_m']} m ({tv['batter_grade_limit']} grade + "
          f"{tv['diagonal_tolerance_m']} diagonal), over-grade fraction "
          f"{tv['earthwork_over_grade_frac']}: "
          f"{tv['earthwork_over_grade_explained_by_a_recorded_clip']} explained "
          f"by a recorded clip, "
          f"{tv['earthwork_over_grade_UNEXPLAINED']} UNEXPLAINED")
    print(f"  batter rays {st['rays']} ({st['rays_clipped']} clipped, "
          f"{st['batter_met_ground']} met natural ground, "
          f"{st['rays_without_road']} skipped for having no written road); "
          f"{st['samples_batter_in_pad_standoff']} batter samples inside a pad "
          f"stand-off; interstice {st['interstice_rays']} rays "
          f"({st['interstice_apron']} apron(s) "
          f"{'tapered' if _INTERSTICE_APRON_MODE == 'taper' else 'levelled flush'}"
          f" edge to edge, "
          f"{st['interstice_left_ungraded']} left ungraded; "
          f"{st['interstice_taper_closes_first']} more whose own taper "
          f"closed short of the other road, graded as ordinary batter -- "
          f"INTERSTICE_DEPENDS_ON); "
          f"{st['edge_exact_samples']} samples at exactly lat==edge")
    for r in st["interstice"][:4]:
        print("   interstice", r)
    for w in tv["earthwork_over_grade_at"]:
        if w["recorded_clip"] is None:
            print("   UNEXPLAINED", w)
    print(f"  ANOTHER CLAIM'S (junctions, not mine to grade) p50 "
          f"{tv['junction_step_p50']} max {tv['junction_step_max']} over "
          f"{tv['junction_pairs']} pairs")
    print(f"  natural ground p50 {tv['natural_ground_step_p50']} max "
          f"{tv['natural_ground_step_max']}; legacy band metric p50 "
          f"{tv['outside_edge_step_p50']} max {tv['outside_edge_step_max']}")
    for w in tv["earthwork_over_grade_at"][:6]:
        print("   over grade", w)
    print(f"walkability: {walk['verdict']} max gradient over "
          f"{VERDICT_BASELINE_M:g} m baseline {walk['max_gradient_8m']} "
          f"(limit {walk['slide_limit']}, 38 deg slide angle) at "
          f"{walk['max_gradient_8m_at']} over {walk['baselines_8m_pairs']} "
          f"baselines; the 1 m figure, a DIFFERENT QUANTITY, is "
          f"{walk['max_gradient_1m']}; {walk['stations_on_road']}/"
          f"{walk['centreline_stations']} centreline stations on written road")
    for g in walk["gaps"]:
        print(f"   gap {g['cause']:14s} {g['length_m']:6.1f} m at {g['xz']} "
              f"step ACROSS {g['step_across_m']} m, INTO {g['step_into_m']} m "
              f"(limit {g['step_into_limit_m']} at {g['step_into_at']}), "
              f"fill at edges {g['fill_at_edges_m']}, "
              f"passable {g['passable']}")
        t = g.get("terminates_at")
        if t:
            print(f"      terminates at {t['site_id']}: road "
                  f"{t['road_y_at_pad_edge']} m vs pad datum {t['pad_y']} m = "
                  f"STEP {t['step_road_to_pad_m']} m "
                  f"(pad levelled yet: {t['pad_levelled_yet']})")
    if walk["holes"]:
        print(f"REFUSING: {walk['holes']} gap(s) in the ribbon with no cause "
              f"-- a hole in the road is the one defect the operator is "
              f"guaranteed to walk into.")
        return 5
    if walk["gaps_impassable"]:
        for g in walk["impassable"]:
            print(f"   IMPASSABLE {g['cause']} {g['length_m']} m at {g['xz']}: "
                  f"{g['step_into_m']} m into the gap at {g['step_into_at']} "
                  f"against {g['step_into_limit_m']} m "
                  f"({g['stations_unread']} station(s) unread), while the two "
                  f"rims read {g['step_across_m']} m against each other; "
                  f"rasteriser refused {g['rasteriser_refused']}")
        print(f"REFUSING: {walk['gaps_impassable']} gap(s) this write leaves "
              f"in its own carriageway cannot be walked out of. The CAUSE "
              f"names whose work the gap is; it does not make the ground "
              f"climbable. Either the thing that refused those samples moves, "
              f"or the profile comes down to meet the ground it could not "
              f"write -- writing the road at a datum the walker cannot reach "
              f"from inside the gap ships a notch and calls it a road.")
        return 6

    entries = []
    for (zx, zz), comp in sorted(comps.items()):
        cx, cz = comp.centre
        patch = patches[f"z_{zx}_{zz}"]
        op_y = float(np.median([comp.level_delta[i] + patch.heights[i]
                                for i in range(tcdata.SAMPLES)
                                if comp.modified_height[i]]))
        blob = comp.blob(op_y)
        opx, opy, opz, opr = comp.op_record(op_y)
        entries.append({
            "zone": [zx, zz], "centre": [cx, cz],
            "data_entry": f"road_{seg['id']}_z{zx}_{zz}".replace("-", "_"),
            "blob": blob,
            "blob_sha256": hashlib.sha256(blob).hexdigest(),
            "generated_heights_sha256": generated_digest(patch),
            "op_record": {"operations": comp.operations,
                          "last_op_point": [opx, opy, opz],
                          "last_op_radius": opr},
            "counts": comp.counts(),
        })
        if (zx, zz) in prior_by_zone:
            entries[-1]["merged_from"] = [pr["seq"] for pr
                                          in prior_by_zone[(zx, zz)]]
            entries[-1]["merge_policy"] = "union"

    plan = {
        "segment": seg["id"], "zones": len(entries),
        "zdo_cost": len(entries),
        "paved_samples": st["samples_paved"],
        "shoulder_samples": st["samples_shoulder"],
        "batter_samples": st["samples_batter"],
        "paved_m2": st["samples_paved"] * 1.0,
        "location_check": {k: v for k, v in loc.items() if k != "violations"},
        "over_clamp": st["over_clamp"],
        "walkability": walk,
    }
    if args.op == "plan":
        print(json.dumps(plan, indent=1))
        return 0

    terrain_params = {
        "name": f"road_{seg['id']}".replace("-", "_"),
        "role": "road_segment",
        "paint": PAINT_ROAD,
        "datum": "road profile: a fitted longitudinal profile. "
                 "lowest_major_walkable_surface is a PAD datum and does not "
                 "apply to a ribbon, which has a different target height at "
                 "every station.",
        "profile": {
            "nodes": [[n[0], n[1], y] for n, y, brg
                      in zip(seg["nodes"], seg["profile_y"], seg["is_bridge"])
                      if not brg],
            "half_width_m": seg["width_m"] / 2.0,
            "shoulder_m": SHOULDER_M,
            # The batter is part of the WRITTEN SURFACE, so it belongs in the
            # field a replay re-derives deltas from. Without it a replay
            # reproduces the carriageway and silently drops every side slope,
            # which is the pre-repair geometry wearing this record's name.
            "batter_m": batter_m,
            "batter_grade": round(BATTER_GRADE, 4),
            "interp": "linear_arclength",
        },
        "max_cut_m": st["max_cut_m"], "max_fill_m": st["max_fill_m"],
        "over_clamp": st["over_clamp"],
        "location_check": loc,
        "entries": entries,
    }
    wire_del = [f"deleteObjects -zone {e['zone'][0]} {e['zone'][1]} "
                f"-prefab _TerrainCompiler -force" for e in entries]
    wire_spawn = [f"spawn_object _TerrainCompiler "
                  f"from={e['centre'][0]:g},{e['centre'][1]:g},0 "
                  f"data={e['data_entry']}" for e in entries]

    if args.validate:
        sys.path.insert(0, str(JUMPSTART / "ledger"))
        import schema
        # The entries carry `blob` (raw bytes) until the blob store swallows
        # them; validation only ever sees the digest, so strip them here.
        vparams = dict(terrain_params)
        vparams["entries"] = [{k: v for k, v in e.items() if k != "blob"}
                              for e in entries]
        for e in vparams["entries"]:
            e["zone_generated_before"] = True
            e["zone_generated_probe"] = (
                f"objects_count id=_ZoneCtrl pos={e['centre'][0]:g},"
                f"{e['centre'][1]:g} max=1")
        rec = {"seq": 1, "ts": "1970-01-01T00:00:00Z", "actor": "RoadNet",
               "op": "terrain_write", "params": vparams,
               "wire": wire_del + wire_spawn,
               "requires": {"mods": ["WorldEditCommands", "UpgradeWorld",
                                     "ServerDevcommands", "ValheimRcon"],
                            "prefabs": ["_TerrainCompiler"],
                            "blobs": [e["blob_sha256"] for e in entries]},
               "expect": {"terrain_compiler": True}, "meta": {},
               "prev": "0" * 64}
        if args.preflight_out:
            pf = Path(args.preflight_out)
            doc = json.loads(pf.read_text()) if pf.exists() else {
                "owner": "RoadNet", "world": WORLD, "seed": SEED,
                "what": "validated pre-flight per road segment: the evidence a "
                        "replay needs BEFORE it sends anything",
                "segments": {}}
            doc["segments"][seg["id"]] = {
                "length_m": seg["length_m"], "cls": seg["cls"],
                "width_m": seg["width_m"], "shoulder_m": SHOULDER_M,
                "grade_limit": seg["grade_limit"],
                "grade_limit_design": seg.get("grade_limit_design"),
                "grade_relaxed_to": seg.get("grade_relaxed_to"),
                "grade_max_measured": seg["grade_max_measured"],
                "zones": [e["zone"] for e in entries],
                "zdo_cost": len(entries),
                "paved_samples": st["samples_paved"],
                "shoulder_samples": st["samples_shoulder"],
                "batter_samples": st["samples_batter"],
                "batter_m": batter_m, "batter_grade": round(BATTER_GRADE, 4),
                "transverse": walk["transverse"],
                "skipped_inside_settlement_pad": st["skipped_pad"],
                "max_cut_m": st["max_cut_m"], "max_fill_m": st["max_fill_m"],
                "samples_past_8m_clamp": st["over_clamp"],
                "clearance": {k: v for k, v in loc.items()
                              if k in ("verdict", "budget", "standoff_m",
                                       "nearest", "samples_tested", "dump",
                                       "tool")},
                "delta_gate": {k: v for k, v in loc["delta_gate"].items()
                               if k in ("verdict", "tolerance_m", "worst",
                                        "instances_probed", "positions_probed")},
                "blob_sha256": {f"{e['zone'][0]},{e['zone'][1]}": e["blob_sha256"]
                                for e in entries},
                "generated_heights_sha256": {
                    f"{e['zone'][0]},{e['zone'][1]}": e["generated_heights_sha256"]
                    for e in entries},
                "crossings_handed_to_Crossings": [c["crossing_id"]
                                                  for c in seg["crossings"]],
                "fords_kept_as_terrain": [f["crossing_id"] for f in seg["fords"]],
                "abutments": seg["abutments"],
            }
            pf.write_text(json.dumps(doc, indent=1, sort_keys=True))
            print(f"pre-flight -> {pf}")
        bad = schema.validate(rec)
        print(f"schema.validate(terrain_write): "
              f"{'OK' if not bad else str(len(bad)) + ' problems'}")
        for b in bad:
            print("   ", b)
        zrec = {"seq": 2, "ts": "1970-01-01T00:00:00Z", "actor": "RoadNet",
                "op": "zones_generate",
                "params": {"pos": list(entries[0]["centre"]), "max_m": 64.0,
                           "zones": [e["zone"] for e in entries],
                           "role": "road_segment"},
                "wire": [f"zones_generate pos={entries[0]['centre'][0]:g},"
                         f"{entries[0]['centre'][1]:g} max=64"],
                "requires": {}, "expect": {"zone_ctrl": len(entries)},
                "meta": {}, "prev": "0" * 64}
        zbad = schema.validate(zrec)
        print(f"schema.validate(zones_generate): "
              f"{'OK' if not zbad else str(len(zbad)) + ' problems'}")
        for b in zbad:
            print("   ", b)
        # ---- THE WRITE-TIME PAD GUARD, ASKED HERE RATHER THAN DISCOVERED
        # AT APPEND TIME.
        #
        # `schema.validate` is not the whole gate.  `Ledger.append` also runs
        # `_pad_footprint_check`, which refuses any `terrain_write` that
        # AUTHORS a sample inside another claim's pad footprint -- the
        # rectangle or the ground that pad itself levelled.  It is the check
        # that will reject this write, it is not in `roads/`, and it is not
        # waivable.  This flag's whole purpose is that "finding that out while
        # holding the console is how a staged operation gets left half-sent",
        # so it is asked offline, against the real chain, before anything is
        # appended.
        #
        # IN A SANDBOX CHAIN, because the guard must DECODE the candidate's
        # blobs to find its authored samples and those blobs are not in the
        # store until the write happens.  A copy of `ledger.jsonl` plus
        # HARDLINKED blobs costs no bytes and keeps the shared store free of
        # digests belonging to a write that may never be made.
        sys.path.insert(0, str(JUMPSTART / "ledger"))
        from writer import Ledger as _Ledger
        sandbox = Path(f"/tmp/{args.actor.lower()}/guardcheck")
        real = _Ledger.open(WORLD, actor=args.actor)
        sand, linked = guard_sandbox(real, sandbox, _Ledger, args.actor)
        for e in entries:
            sand.blob(e["blob"], note=f"guardcheck {seg['id']}")
        prior = sand.records()
        # THE SAME GUARD **SET** THE APPEND RUNS, NOT A SUBSET OF IT.
        #
        # `Ledger.append` runs THREE checks on a `terrain_write`:
        # `_zone_clobber_check`, `_flatten_forbidden_check` and
        # `_pad_footprint_check` (writer.py:1006-1008).  This pre-flight ran
        # only the third and printed "0 problems".
        #
        # MEASURED cost of that gap, on N0b-wmid-wnorth: `--validate` reported
        # schema OK on both records AND the pad guard PASSING, and
        # `Ledger.append` then refused the whole 1,368.7 m leg on a LIVE world
        # for 38 pieces of `lh-north` moved 0.020-0.751 m -- a
        # `_flatten_forbidden_check` refusal, from the one guard that was not
        # asked.  The scaffolding to ask it already existed: the sandbox chain
        # with symlinked blobs is built two lines above, for the other guard.
        #
        # THE GENERAL FORM, and it is the third instance of it tonight in
        # three different guard families: AN OFFLINE PRE-FLIGHT MUST RUN THE
        # SAME GUARD SET AS THE APPEND, OR ITS VERDICT IS A STATEMENT ABOUT A
        # SUBSET.  A pre-flight that runs 2 of 3 guards and reports "0
        # problems" is not a weaker check -- it is a DIFFERENT check wearing
        # the same words, and absence of a complaint from a check that never
        # executed is not evidence.
        #
        # Named individually rather than looped, so a guard added to
        # `append` shows up here as a missing name in review instead of being
        # silently covered or silently skipped.
        guard_set = [
            ("_zone_clobber_check", sand._zone_clobber_check(rec, prior)),
            ("_flatten_forbidden_check",
             sand._flatten_forbidden_check(rec, prior)),
            ("_pad_footprint_check", sand._pad_footprint_check(rec, prior)),
        ]
        guard_all = [g for _n, gs in guard_set for g in gs]
        print(f"write-time guard SET (writer.py::append's own three) over "
              f"{len(prior)} prior record(s) and {linked} linked blob(s) "
              f"in {sandbox}:")
        for nm, gs in guard_set:
            print(f"   {nm:28s} {'PASSES' if not gs else 'REFUSES'}"
                  f"{'' if not gs else f' ({len(gs)} problem(s))'}")
            for g in gs:
                print("       ", g)
        # The diagnostic block below is SPECIFIC to the pad guard -- it prints
        # prior-versus-mine for each disagreeing sample -- so it reads the pad
        # list alone.  The VERDICT reads all three.
        guard = guard_set[2][1]
        if guard:
            # WHICH SAMPLES, AND WHAT THIS WRITE PUT THERE.  The guard names a
            # count, a worst move and the first six positions; that is enough
            # to know it refused and not enough to know WHY this write differs
            # from the composition.  The rasteriser refuses every sample the
            # keep-out covers, so a disagreement here is NOT a paved
            # foundation -- it is this write's blob carrying a different value
            # at a sample it never wrote, which is a UNION defect and reads
            # identically in the refusal message.  So the two values are
            # printed side by side.
            composed: dict[tuple[int, int], tuple[float, int]] = {}
            for line, pr in enumerate(prior):
                if pr.get("op") != "terrain_write":
                    continue
                for e in pr["params"].get("entries", []):
                    got = writer_entry_samples(e, sand)
                    if got is None:
                        continue
                    for sxz, val in got.items():
                        composed[sxz] = (val[0], pr["seq"])
            shown = 0
            for e in rec["params"]["entries"]:
                got = writer_entry_samples(e, sand)
                if got is None:
                    print(f"    entry zone {e['zone']}: BLOB WOULD NOT DECODE")
                    continue
                for sxz, val in sorted(got.items()):
                    sid = pad_keepouts.contains(float(sxz[0]), float(sxz[1]))
                    if sid is None:
                        continue
                    was, was_seq = composed.get(sxz, (None, None))
                    if was is not None and abs(was - val[0]) <= 1e-6:
                        continue
                    if shown < 12:
                        zx, zz = int(e["zone"][0]), int(e["zone"][1])
                        cmp_ = comps.get((zx, zz))
                        mh = (None if cmp_ is None else
                              cmp_.modified_height[
                                  tcdata.vertex_mask_index(
                                      float(e["centre"][0]),
                                      float(e["centre"][1]), sxz[0], sxz[1])[1]
                                  * tcdata.PITCH
                                  + tcdata.vertex_mask_index(
                                      float(e["centre"][0]),
                                      float(e["centre"][1]), sxz[0], sxz[1])[0]])
                        print(f"    sample {sxz} in {sid}: prior "
                              f"{'None' if was is None else f'{was:+.4f}'} "
                              f"(seq {was_seq}) -> mine {val[0]:+.4f} "
                              f"[zone {e['zone']}, my rasteriser wrote it: "
                              f"{mh}]")
                    shown += 1
            print(f"    {shown} disagreeing sample(s) inside a pad footprint")
        return 0 if not (bad or zbad or guard_all) else 4

    # ---- live, through the ledger --------------------------------------
    sys.path.insert(0, str(JUMPSTART / "ledger"))
    from live import LiveBuilder

    with LiveBuilder(actor=args.actor, dry=args.dry) as b:
        b.observe(
            "road_segment_plan", 
            method="MEASURED: A* over a 1 m PatchScan field (rivers included) "
                   "with the achievable surface clipped to +/-7 m of generated "
                   "ground; profile fitted by exact interval propagation under "
                   "the grade limit and the cut/fill box",
            tool="tools/jumpstart/roads/network.py + ribbon.py",
            value={k: v for k, v in seg.items()
                   if k not in ("nodes", "profile_y", "terrain_y", "is_bridge",
                                "poi_within_keepout")})

        # STEP 0: generate every zone, in batches, and prove each one.
        done_zones = []
        for i in range(0, len(entries), args.zone_batch):
            batch = entries[i:i + args.zone_batch]
            xs = [e["centre"][0] for e in batch]
            zs = [e["centre"][1] for e in batch]
            px, pz = (min(xs) + max(xs)) / 2.0, (min(zs) + max(zs)) / 2.0
            reach = max(math.hypot(e["centre"][0] - px, e["centre"][1] - pz)
                        for e in batch) + 1.0
            r = b.emit("zones_generate",
                       params={"pos": [px, pz], "max_m": round(reach, 1),
                               "zones": [e["zone"] for e in batch],
                               "role": "road_segment"},
                       wire=[f"zones_generate pos={px:g},{pz:g} max={reach:g}"],
                       expect={"zone_ctrl": len(batch)},
                       meta={**road_unit_meta(seg["id"]),
                             "why": "paint alpha is NOT honoured on a zone that "
                                    "has never been generated -- MEASURED, a "
                                    "paved_cleared pad in an ungenerated zone "
                                    "still planted 30+ vegetation ZDOs up to "
                                    "7.68 m above it"})
            print(f"  zones_generate {len(batch)} zones -> {r['status']}")
            done_zones += batch

        # Probe each zone individually: the batch's aggregate _ZoneCtrl count
        # proves the staged op completed, but the ledger requires per-zone
        # evidence and `objects_count id=_ZoneCtrl pos=cx,cz max=1` is exact,
        # because PlaceZoneCtrl puts the controller at the zone CENTRE and the
        # filter is an XZ cylinder.
        import replay as R
        for e in entries:
            cx, cz = e["centre"]
            probe = f"objects_count id=_ZoneCtrl pos={cx:g},{cz:g} max=1"
            got, _ = b.srv.count("_ZoneCtrl", cx, cz, 1.0, ignore="")
            e["zone_generated_before"] = bool(got >= 1)
            e["zone_generated_probe"] = probe
            e["zone_ctrl_observed"] = int(got)
            if got < 1:
                raise SystemExit(
                    f"zone {e['zone']} shows {got} _ZoneCtrl after "
                    f"zones_generate: refusing to write terrain into a zone "
                    f"that is not generated. Probe: {probe}")

        shas = []
        for e in entries:
            shas.append(b.blob(e.pop("blob"),
                               note=f"TCData {seg['id']} zone {e['zone']}"))
        wire = []
        for e in entries:
            wire.append(f"deleteObjects -zone {e['zone'][0]} {e['zone'][1]} "
                        f"-prefab _TerrainCompiler -force")
        for e in entries:
            wire.append(f"spawn_object _TerrainCompiler "
                        f"from={e['centre'][0]:g},{e['centre'][1]:g},0 "
                        f"data={e['data_entry']}")

        r = b.emit(
            "terrain_write",
            params={
                "name": f"road_{seg['id']}".replace("-", "_"),
                "role": "road_segment",
                "paint": PAINT_ROAD,
                "datum": "road profile: fitted longitudinal profile, "
                         "lowest_major_walkable_surface is not applicable to a "
                         "ribbon",
                "profile": {
                    "nodes": [[n[0], n[1], y] for n, y, brg
                              in zip(seg["nodes"], seg["profile_y"], seg["is_bridge"])
                              if not brg],
                    "half_width_m": seg["width_m"] / 2.0,
                    "shoulder_m": SHOULDER_M,
                    "batter_m": batter_m,
                    "batter_grade": round(BATTER_GRADE, 4),
                    "interp": "linear_arclength",
                },
                "max_cut_m": st["max_cut_m"], "max_fill_m": st["max_fill_m"],
                "over_clamp": st["over_clamp"],
                "location_check": {k: v for k, v in loc.items()
                                   if k not in ("within_keepout",)},
                "entries": entries,
            },
            wire=wire,
            requires={"mods": ["WorldEditCommands", "UpgradeWorld",
                               "ServerDevcommands", "ValheimRcon"],
                      "prefabs": ["_TerrainCompiler"],
                      "blobs": shas},
            expect={"terrain_compiler": True},
            meta={**road_unit_meta(seg["id"]),
                  "grade_limit": seg["grade_limit"],
                  "grade_max_measured": seg["grade_max_measured"],
                  "crossings_handed_to_Crossings": [c["crossing_id"]
                                                    for c in seg["crossings"]],
                  "fords_kept_as_terrain": [f["crossing_id"] for f in seg["fords"]],
                  "abutments": seg["abutments"],
                  "paved_samples": st["samples_paved"],
                  "shoulder_samples": st["samples_shoulder"],
                  "batter_samples": st["samples_batter"],
                  "batter_m": batter_m,
                  "batter_grade": round(BATTER_GRADE, 4),
                  "repair_of": args.repair_of,
                  "repair_before": (json.loads(
                      Path(args.repair_before).read_text())
                      if args.repair_before else None),
                  "walkability": walk,
                  # THE ORDER, IN THE RECORD.  A road's ledger entry now
                  # carries WHICH reading of the ground it was routed against
                  # and when that reading was taken -- or, when there was
                  # none, the typed reason it was written anyway.  Every road
                  # already on this world would read `waiver`, which is the
                  # point: the 42 standing bodies were invisible in the record
                  # as well as to the router.
                  "census": {
                      "plan_verdict": cen_rec or None,
                      "artefact": args.census,
                      "rechecked_here": bool(args.census),
                      "coverage": (None if cen_live is None else
                                   censusmod.coverage_gate(
                                       [(n[0], n[1]) for n in seg["nodes"]],
                                       cen_live)),
                      "bodies": (None if cen_live is None
                                 else len(cen_live.bodies)),
                      "waiver": args.census_waiver,
                  },
                  # THE JUNCTION VERDICT, IN THE RECORD.  A waiver that
                  # lives only in a shell history is a decision nobody can
                  # audit: the operator who inherits this road has to be
                  # able to read which junctions it was knowingly written
                  # into and at what spread, because a waiver is void once
                  # that number moves and a number nobody wrote down cannot
                  # be compared to anything.
                  "junction": {
                      "verdict": jrep["verdict"],
                      "tool": "roads/junction.py::build_gate",
                      "population": [p.name for p in j_files],
                      "pairs_sharing_ground": jrep["my_pairs"],
                      "refused": jrep["my_refusals"],
                      "waived": jrep["my_waived"],
                      "slide_allow_at_overlap_m":
                          jrep["tolerance"]["tier1_at_overlap_m"],
                      "accepted": j_accept or None,
                      "accepted_from": args.junction_accept,
                  },
                  # THE RIM VERDICT, IN THE RECORD, for the same reason the
                  # junction verdict is: a write that TAKES ground has to
                  # say what it left at the edge of what it took, and how
                  # much of that it inherited rather than caused.  The write
                  # that produced this check carried no such row, so the
                  # only evidence that it severed three destinations was a
                  # destination count taken an hour later.
                  "rim": {
                      "verdict": rim["verdict"],
                      "tool": "roads/junction.py::rim_transfer",
                      "samples_moved_by_this_write": rim["taken_samples"],
                      "rim_samples": rim["rim_samples"],
                      "rim_pairs_judged": rim["rim_pairs_judged"],
                      "rim_samples_unreadable": rim["rim_samples_unreadable"],
                      "moved_samples_with_no_generated_height":
                          rim_gen_missing,
                      "steps_caused_by_this_write": rim["caused"],
                      "steps_pre_existing": rim["pre_existing"],
                      "worst_caused_m": rim["worst_caused_m"],
                      "worst_caused_at": rim["worst_caused_at"],
                      "pre_existing_rows": rim["pre_existing_rows"][:12],
                      # PLANNED_PAD_RIM_DEPENDS_ON: the steps this write
                      # leaves against ground a pad lays next, as a debt
                      # that pad discharges.
                      "owed_to_planned_pads": [
                          {k: r[k] for k in ("rim_xz", "taken_xz",
                                             "planned_pad", "pad_y",
                                             "step_after_m",
                                             "step_after_pad_m")}
                          for r in rim_owed[:40]],
                      "owed_to_planned_pads_count": len(rim_owed),
                  },
                  "merged_from_file_lines": {
                      f"{z[0]},{z[1]}": [{"file_line": pr["file_line"],
                                          "seq": pr["seq"],
                                          "name": pr["name"]}
                                         for pr in plist]
                      for z, plist in sorted(prior_by_zone.items())},
                  "why": "roads are terrain, not pieces: piece_pavedroad has no "
                         "persistent ZNetView and zero road pieces exist in any "
                         "fleet save"})
        print(f"  terrain_write {len(entries)} zones -> {r['status']}")
        for c in r["checks"]:
            print("   ", c)
        # `expect` is MANDATORY and non-empty on every mutating op, and `save`
        # is mutating.  The honest cheap postcondition for a save is that the
        # thing just written is STILL THERE afterwards: count this segment's
        # own compilers at their zone centres.  A bare `save` with no
        # postcondition is a command whose effect nothing measured.
        b.emit("save", params={"role": "road_segment"}, wire=["save"],
               expect={"prefab_count": [
                   {"prefab": "_TerrainCompiler",
                    "pos": [e["centre"][0], e["centre"][1]],
                    "max": 31, "count": 1} for e in entries]})
        print(json.dumps(b.close(), indent=1)[:1200])
    return 0


if __name__ == "__main__":
    sys.exit(main())
