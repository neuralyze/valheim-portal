#!/usr/bin/env python3
"""LIGHTING - is the road lit, measured from EYE HEIGHT against the surface
the player actually walks on, and is the SIGHT DISTANCE stated with every
number that depends on it.

THREE LESSONS, ONE PER CHECK.

  1. THE SIGHT DISTANCE IS PART OF THE NUMBER.  The same corridor read
     40 m worst-unlit at 30 m sight and 20 m at 50 m sight.  Neither number
     is wrong and neither is meaningful alone, so this section computes the
     worst unlit run at TWO sight distances and prints both, every time.
     The reading is taken from the walker's EYE over the APPLIED surface -
     the ground the engine draws, read back out of the save - and not
     lamp-to-lamp, because lamp-to-lamp cannot see the ridge between them.

  2. A LAMP AT ITS DESIGNED OFFSET IS NOT AN OBSTACLE.  Road lamps stand at
     exactly `half_width + 0.55 m` lateral: the carriageway is half_width
     each side and the shoulder is beyond it, so the lamp's 0.24 m footprint
     lies wholly on the shoulder with clear air both sides.  That is
     DESIGNED OCCUPANCY and this section says so with a count, so no other
     check has to guess.  A lamp NOT at that offset is a different thing
     entirely, and 23 of them on this world stand INSIDE the carriageway -
     the closest 0.081 m from the centreline of T5.

  3. A PER-SEGMENT SPACING RULE IS BLIND TO A SHARED JUNCTION.  The siting
     pass enforced its minimum against `last_s`, the previous lamp in ITS
     OWN chain on ONE segment - a decision made from local state about a
     shared world.  Two segments meeting at a junction each lit the same
     station from opposite sides and each was correct.  So spacing is
     checked NETWORK-WIDE, over every ground-torch variant near any built
     corridor, with a CYLINDER: radius in xz, +/- 10 m in y, because a lamp
     on a clifftop 12 m above the road is not a cluster with the lamp below
     it and refusing either one would be a cube answering a question about
     a cylinder.

WHAT IT NEVER DOES: it never divides a length by a spacing and calls the
result coverage, it never reads the PLANNED profile (89 m of T4's centreline
sits on samples a settlement pad overwrote after the road was written), and
it never deletes a lamp.
"""
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict

from . import _approach as A
from .. import termination as T
from ..registry import Case, Finding, Section, register


# WHY EACH REFUSAL THIS SECTION CAN EMIT CAN OR CANNOT BE CLOSED.
#
# THE POPULATION IS `termination.emittable("lighting")`, read out of this
# file's parse tree, and `termination.coverage` refuses the gate's own
# selftest if any of it is unnamed here.
#
# AND THREE OF THE FOUR ARE ACTIONABLE, WHICH IS NOT THE COMMON ANSWER ON
# THIS BOARD, BECAUSE THE TOOLS EXIST AND ARE NAMED:
#
#   `roads/relight.py` MOVES a lamp that is already standing in the wrong
#     place - it emits `retire` + `spawn_plan` for the nearest position
#     legal under every published rule at once, writes no terrain, and
#     replaces the piece rather than removing it.  Its own docstring names
#     the two defects it was written for, and they are this section's
#     `lamp_lateral` and `lamp_spacing` VERBATIM.
#   `roads/lamps.py --place` SITES a lamp on ground that has none, which is
#     what an over-long unlit run needs.  Also no terrain, also nothing
#     removed.
#
#   THE FOURTH IS NOT A DEFECT OF THE LIGHTING AT ALL.  `population` fires
#     when NO ROAD SEGMENT was ever written to this world - there is no
#     corridor to light - and the remedy is the ROAD BUILD, an earlier
#     stage of this same tree.  That is 1E, FACTORY WORK, and the row names
#     the stage it is waiting on.  MEASURED on the fresh seed Fresh42,
#     where this was one of the thirteen refusals the board could not
#     triage.
TERMINATION_FAMILIES = {
    "lamp_lateral": (
        "RETIRE_AND_RESPAWN",
        "roads/relight.py moves a standing lamp out of the carriageway to "
        "the nearest position legal under every published rule; it emits "
        "retire + spawn_plan and writes no terrain"),
    "lamp_spacing": (
        "RETIRE_AND_RESPAWN",
        "roads/relight.py moves one of the pair to the nearest position "
        "that satisfies the biome spacing; it writes no terrain"),
    "unlit_run": (
        "PLACE_A_PIECE",
        "roads/lamps.py --place sites a lamp on ground that has none, "
        "measuring the terrain for the longest sightline in both "
        "directions; it writes no terrain and removes nothing"),
    "population": (
        "UPSTREAM_STAGE_NOT_RUN",
        "no road segment was ever written to this world, so the corridor "
        "this section lights does not exist yet; the prerequisite is the "
        "ROAD BUILD stage of this tree, not a lighting pass"),
}
# Every ground-torch variant, because the question is "is this stretch lit",
# not "did the lighting pass put something here".  MEASURED on Ulfsland: the
# torches beside these roads are mostly `piece_groundtorch` at the designed
# offset, plus `piece_groundtorch_wood` standing INSIDE houses up to 35 m
# back - and a torch in a house 11 m off the carriageway lights the
# carriageway, so the census must see it.
LAMP_PREFABS = ("piece_groundtorch", "piece_groundtorch_wood",
                "piece_groundtorch_green", "piece_groundtorch_blue",
                "piece_groundtorch_mist")

# MEASURED by roads/LampProbe.cs off the prefab itself: the Point light sits
# at local y +0.746 from the pivot (and the pivot is 0.65359 above the
# visible foot, so the flame is 1.39959 m above the ground it stands on).
# Taken from the pivot here because the pivot is what the ZDO carries.
FLAME_ABOVE_PIVOT_M = 0.746
# DESIGN, not measured, and labelled: there is no instrument on this host
# that reads `Player.m_eyePoint` without a client, and inventing a
# measurement is worse than declaring a constant.  roads/lamps.py declares
# the same 1.7 m for the same reason; this is that number, not a second one.
EYE_M = 1.7
# The two sight distances every unlit number is reported at.  Not a
# tolerance: the pair IS the answer, because a single number hides which
# question was asked.
SIGHT_DISTANCES_M = (30.0, 50.0)
# How far off a centreline a torch is still "beside this road".
CORRIDOR_M = 12.0
# The lateral standoff a road lamp is DESIGNED to stand at, and the
# tolerance within which a lamp counts as being at it.  From
# roads/lamps.py::SHOULDER_STANDOFF_M, which is published with its reason.
SHOULDER_STANDOFF_M = 0.55
DESIGNED_TOL_M = 0.10
# Spacing: the TIGHTEST minimum any biome in roads/lamps.py::BIOME_POLICY
# asks for.  Using the tightest means a pair under it is under EVERY policy
# row, so the verdict does not depend on reading the biome.
MIN_SPACING_M = 30.0
SPACING_Y_HALF_M = 10.0
# The WIDEST maximum any biome in that policy allows (Meadows / Plains /
# AshLands, "used sparingly").  An unlit run longer than this is beyond even
# the most sparing row, so - again - it is a defect under every policy and
# the biome need not be read to say so.
MAX_UNLIT_RUN_M = 90.0
SAMPLE_M = 1.0


# ---------------------------------------------------------------------------
def perpendicular_m(px: float, pz: float,
                    nodes: list[tuple[float, float]]) -> float:
    """True distance from a point to the centreline POLYLINE.

    Not the distance to the nearest 1 m station: on a bend that overstates
    the offset, and the offset is the quantity that decides whether a lamp
    is designed occupancy or an obstacle.
    """
    best = math.inf
    for i in range(len(nodes) - 1):
        ax, az = nodes[i]
        bx, bz = nodes[i + 1]
        vx, vz = bx - ax, bz - az
        ll = vx * vx + vz * vz
        t = 0.0 if ll == 0 else max(0.0, min(1.0, ((px - ax) * vx
                                                   + (pz - az) * vz) / ll))
        best = min(best, math.hypot(px - (ax + t * vx), pz - (az + t * vz)))
    return best


def classify_lamps(lamps, segs) -> list[dict]:
    """Every torch near a built corridor, with its own segment, that
    segment's OWN half width (from the road's own write record, never a
    constant here) and its true perpendicular offset."""
    out = []
    for name, x, y, z in lamps:
        best = None
        for sname, s in segs.items():
            d = perpendicular_m(x, z, s["nodes"])
            if best is None or d < best[0]:
                best = (d, sname, s["half_width_m"])
        if best is None or best[0] > CORRIDOR_M:
            continue
        lat, sname, hw = best
        out.append(dict(prefab=name, x=x, y=y, z=z, seg=sname, lat=lat,
                        half_width_m=hw,
                        designed=abs(lat - (hw + SHOULDER_STANDOFF_M))
                        <= DESIGNED_TOL_M,
                        in_carriageway=lat < hw))
    return out


# ---------------------------------------------------------------------------
# CHECK 1 - designed occupancy, and what is not it
# ---------------------------------------------------------------------------
def check_lateral(corridor: list[dict]) -> list[Finding]:
    out = []
    for l in sorted((l for l in corridor if l["in_carriageway"]),
                    key=lambda l: l["lat"]):
        out.append(Finding(
            check="lamp_lateral", ok=False,
            message=("{} at ({:.1f}, {:.1f}) stands {:.3f} m from the "
                     "centreline of {}, INSIDE its {:.1f} m half-width: a "
                     "road lamp is designed to stand at half_width + "
                     "{:.2f} m = {:.2f} m, wholly on the shoulder. This one "
                     "is in the carriageway and is an obstacle, not "
                     "designed occupancy.".format(
                         l["prefab"], l["x"], l["z"], l["lat"], l["seg"],
                         l["half_width_m"], SHOULDER_STANDOFF_M,
                         l["half_width_m"] + SHOULDER_STANDOFF_M)),
            where=(l["x"], l["y"], l["z"]),
            measured=l["lat"], limit=l["half_width_m"],
            numbers=dict(lateral_m=round(l["lat"], 3),
                         half_width_m=l["half_width_m"],
                         designed_offset_m=round(
                             l["half_width_m"] + SHOULDER_STANDOFF_M, 3),
                         segment=l["seg"])))
    return out


# ---------------------------------------------------------------------------
# CHECK 2 - spacing, network-wide, with a cylinder
# ---------------------------------------------------------------------------
def check_spacing(corridor: list[dict]) -> tuple[list[Finding], dict]:
    pairs = []
    cell = MIN_SPACING_M
    grid = defaultdict(list)
    for i, l in enumerate(corridor):
        grid[(int(l["x"] // cell), int(l["z"] // cell))].append(i)
    for i, a in enumerate(corridor):
        for ix in range(int((a["x"] - MIN_SPACING_M) // cell),
                        int((a["x"] + MIN_SPACING_M) // cell) + 1):
            for iz in range(int((a["z"] - MIN_SPACING_M) // cell),
                            int((a["z"] + MIN_SPACING_M) // cell) + 1):
                for j in grid.get((ix, iz), ()):
                    if j <= i:
                        continue
                    b = corridor[j]
                    if not (a["designed"] or b["designed"]):
                        continue
                    d = math.hypot(a["x"] - b["x"], a["z"] - b["z"])
                    if d >= MIN_SPACING_M:
                        continue
                    if abs(a["y"] - b["y"]) > SPACING_Y_HALF_M:
                        continue        # the cylinder, not a cube
                    pairs.append((d, a, b))
    pairs.sort(key=lambda t: t[0])
    cross = sum(1 for d, a, b in pairs if a["seg"] != b["seg"])
    stats = dict(pairs=len(pairs), cross_segment=cross,
                 same_segment=len(pairs) - cross,
                 tightest_m=round(pairs[0][0], 3) if pairs else None)
    out = []
    for d, a, b in pairs:
        out.append(Finding(
            check="lamp_spacing", ok=False,
            message=("two corridor lamps {:.3f} m apart at ({:.1f}, {:.1f}) "
                     "and ({:.1f}, {:.1f}), under the {:.0f} m minimum - "
                     "{}. Measured with a CYLINDER: {:.3f} m in xz, "
                     "{:.3f} m in y.".format(
                         d, a["x"], a["z"], b["x"], b["z"], MIN_SPACING_M,
                         (f"segments {a['seg']} and {b['seg']}, which a "
                          "per-segment rule cannot see")
                         if a["seg"] != b["seg"] else
                         f"both on {a['seg']}",
                         d, abs(a["y"] - b["y"]))),
            where=(a["x"], a["y"], a["z"]),
            measured=d, limit=MIN_SPACING_M,
            numbers=dict(gap_m=round(d, 3), dy_m=round(abs(a["y"] - b["y"]),
                                                       3),
                         seg_a=a["seg"], seg_b=b["seg"],
                         cross_segment=a["seg"] != b["seg"])))
    return out, stats


# ---------------------------------------------------------------------------
# CHECK 3 - unlit runs, at eye height, at two sight distances
# ---------------------------------------------------------------------------
def unlit_runs(stations, profile, lamps_idx, sight_m: float
               ) -> tuple[float, tuple | None, int, int]:
    """Longest contiguous run of stations with NO lamp flame in view, the
    station it ends at, and how many stations are unlit.

    A station with no applied reading is NOT counted as unlit: an
    unmeasured sample must never be reported as a defect, in either
    direction.  The count of them is returned so the reader can see it.
    """
    n = len(stations)
    lit = [False] * n
    unread = 0
    for i in range(n):
        y = profile[i]
        if y is None:
            lit[i] = True
            unread += 1
            continue
        eye = y + EYE_M
        for (j, lx, lz, flame) in lamps_idx:
            _s, x, z = stations[i]
            if math.hypot(lx - x, lz - z) > sight_m:
                continue
            if A.visible(profile, i, j, eye, flame):
                lit[i] = True
                break
    worst, worst_at, run, unlit = 0.0, None, 0, 0
    for i, f in enumerate(lit):
        if f:
            run = 0
            continue
        run += 1
        unlit += 1
        if run > worst:
            worst, worst_at = float(run), (stations[i][1], stations[i][2])
    return worst, worst_at, unlit, unread


def lamp_index(st, corridor, reach_m: float) -> list[tuple]:
    """Every corridor lamp within `reach_m` of THIS segment's stations,
    mapped to the station nearest it.

    EVERY LAMP, NOT THIS SEGMENT'S.  This check used to index only the lamps
    `classify_lamps` filed under the segment being walked, and that is a
    per-segment rule answering a question about a place: `temple` is an
    endpoint of SIX segments, a flame does not know which centreline it was
    assigned to, and a torch 4 m from a T3 station that happens to be 0.3 m
    nearer T5 lit nothing at all as far as this check could see.

    MEASURED on Ulfsland, which is how it was caught: the section reported a
    192 m unlit run on T3 at 30 m sight distance, while NO station on T3 is
    more than 39.1 m from a standing torch and the longest run of stations
    further than 30 m from ANY torch is 19 m.  A 192 m dark run cannot be
    built out of 19 m of darkness; the missing 173 m were flames filed under
    a neighbour.  Same shape as the lamp-lateral defect this section already
    refuses: a rule that partitions a shared place by segment is blind to
    exactly the junctions where the segments meet.

    The lamp is mapped to the nearest station on THIS segment because that is
    where the sightline walk over this road's own profile has to start, and
    it is the same approximation the segment's own lamps already get: they
    stand `half_width + 0.55` m off the centreline, not on it.
    """
    idx = []
    for l in corridor:
        j = min(range(len(st)),
                key=lambda k: (st[k][1] - l["x"]) ** 2
                + (st[k][2] - l["z"]) ** 2)
        if math.hypot(st[j][1] - l["x"], st[j][2] - l["z"]) <= reach_m:
            idx.append((j, l["x"], l["z"], l["y"] + FLAME_ABOVE_PIVOT_M))
    return idx


def check_unlit(stations_by_seg, profiles, corridor) -> tuple[list[Finding],
                                                              dict]:
    out, stats = [], {}
    # The index is built ONCE per segment at the widest sight distance and
    # reused: `unlit_runs` already drops a lamp further than the sight
    # distance it was asked about, so a second index would be the same
    # measurement computed twice and a second place for the two to disagree.
    reach = max(SIGHT_DISTANCES_M)
    idx_by_seg = {name: lamp_index(st, corridor, reach)
                  for name, st in stations_by_seg.items()}
    for sight in SIGHT_DISTANCES_M:
        worst_all, worst_seg, worst_at = 0.0, None, None
        unlit_all = total = unread_all = 0
        for name, st in stations_by_seg.items():
            prof = profiles[name]
            w, at, unlit, unread = unlit_runs(st, prof, idx_by_seg[name],
                                              sight)
            total += len(st)
            unlit_all += unlit
            unread_all += unread
            if w > worst_all:
                worst_all, worst_seg, worst_at = w, name, at
        stats[f"sight_{sight:.0f}m"] = dict(
            worst_unlit_run_m=worst_all, segment=worst_seg,
            at=(round(worst_at[0], 1), round(worst_at[1], 1))
            if worst_at else None,
            unlit_stations=unlit_all, stations=total,
            stations_with_no_applied_reading=unread_all)
        if worst_all > MAX_UNLIT_RUN_M:
            out.append(Finding(
                check="unlit_run", ok=False,
                message=("AT {:.0f} m SIGHT DISTANCE the worst unlit run is "
                         "{:.0f} m on {}, ending at ({:.1f}, {:.1f}); {} of "
                         "{} stations have no flame in view from eye height "
                         "{:.1f} m over the applied surface. The widest gap "
                         "any biome in the published policy allows is "
                         "{:.0f} m, so this run is too long under EVERY row "
                         "of it and the biome does not have to be read to "
                         "say so.".format(
                             sight, worst_all, worst_seg,
                             worst_at[0] if worst_at else float("nan"),
                             worst_at[1] if worst_at else float("nan"),
                             unlit_all, total, EYE_M, MAX_UNLIT_RUN_M)),
                where=(worst_at[0], worst_at[1]) if worst_at else None,
                measured=worst_all, limit=MAX_UNLIT_RUN_M,
                numbers=dict(sight_distance_m=sight,
                             worst_unlit_run_m=worst_all,
                             segment=worst_seg, unlit_stations=unlit_all,
                             stations=total, eye_m=EYE_M)))
    return out, stats


# ---------------------------------------------------------------------------
# the section
# ---------------------------------------------------------------------------
def run(ctx) -> list[Finding]:
    I = A.instruments()
    segs = built_segments(ctx.ledger)
    if not segs:
        # AN EARLY RETURN IS STILL A BOARD ROW.  This is the arm the fresh
        # seed Fresh42 took, and a classifier wired only into the long path
        # would have left exactly this refusal - the only one an unbuilt
        # world can produce here - reading NO_CLASS.
        empty = [Finding(check="population", ok=False,
                         message="no road segment was ever written to this "
                                 "world, so there is no corridor to light",
                         numbers=dict(segments=0))]
        return empty + T.classify(sys.modules[__name__], empty, "lighting")

    # THE STATION SET IS BUILT EXACTLY AS THE ROADS SECTION BUILDS IT -
    # same sort, same 1 m resample, same `i|j` labels - so `ctx.ground`
    # hits the SAME cached batches instead of asking the harness for a
    # second reading of the same 21,979 points.  If the two ever diverge the
    # cost is 18 harness batches, not a wrong answer, but they should not.
    codes = sorted(segs, key=lambda n: segs[n]["code"])
    stations, pts = {}, []
    for i, name in enumerate(codes):
        st = I.resample(segs[name]["nodes"], SAMPLE_M)
        stations[name] = st
        for j, (_s, x, z) in enumerate(st):
            pts.append((f"{i}|{j}", x, z))
    bed = ctx.ground(pts)
    profiles = {}
    for i, name in enumerate(codes):
        profiles[name] = [bed.get(f"{i}|{j}")
                          for j in range(len(stations[name]))]

    lamps = [(n, x, y, z) for (n, x, y, z, *_q) in ctx.census.rows
             if n in LAMP_PREFABS]
    corridor = classify_lamps(lamps, segs)

    findings: list[Finding] = []
    findings += check_lateral(corridor)
    sp, spstats = check_spacing(corridor)
    findings += sp
    un, unstats = check_unlit(stations, profiles, corridor)
    findings += un

    designed = sum(1 for l in corridor if l["designed"])
    inside = sum(1 for l in corridor if l["in_carriageway"])
    findings.append(Finding(
        check="population", ok=True,
        message=("{} ground-torch ZDOs world-wide ({}), {} of them within "
                 "{:.0f} m of a built centreline: {} at the designed "
                 "half_width + {:.2f} m (DESIGNED OCCUPANCY, never an "
                 "obstacle), {} inside the carriageway, {} standing further "
                 "back. Spacing: {} pair(s) under {:.0f} m network-wide, {} "
                 "of them ACROSS two segments and therefore invisible to a "
                 "per-segment rule. Unlit: {}".format(
                     len(lamps), "+".join(sorted({l[0] for l in lamps})),
                     len(corridor), CORRIDOR_M, designed,
                     SHOULDER_STANDOFF_M, inside,
                     len(corridor) - designed - inside,
                     spstats["pairs"], MIN_SPACING_M,
                     spstats["cross_segment"],
                     "; ".join(
                         "{} -> worst run {:.0f} m, {} of {} stations dark"
                         .format(k, v["worst_unlit_run_m"],
                                 v["unlit_stations"], v["stations"])
                         for k, v in unstats.items()))),
        numbers=dict(torches_world=len(lamps), corridor=len(corridor),
                     designed=designed, in_carriageway=inside,
                     spacing=spstats, unlit=unstats)))
    # AND EVERY REFUSAL ABOVE GETS ITS CLASS, THROUGH THE ONE CLASSIFIER.
    return findings + T.classify(sys.modules[__name__], findings, "lighting")


def built_segments(ledger_path):
    """Every road segment the ledger records a terrain write for - the
    POPULATION and the centreline geometry, and nothing else: not one
    height in this section comes from the ledger.

    ONE AUTHORITY: this is the ROADS section's own loader, imported rather
    than copied.  A second reader of the same records is how two agents end
    up measuring two different road networks and both being right about
    their own.  The import is local to the call so that listing this
    section's cases costs nothing.
    """
    from .roads import built_segments as _roads_built   # noqa: PLC0415
    return _roads_built(ledger_path)


# ---------------------------------------------------------------------------
# THE SELFTEST
# ---------------------------------------------------------------------------
def _line(n: int, x0: float = 0.0, z0: float = 0.0):
    return [(float(k), x0 + float(k), z0) for k in range(n)]


def _seg(nodes, hw=3.0):
    return dict(name="road_X", seg="X-a-b", code="X", half_width_m=hw,
                shoulder_m=1.0, nodes=nodes, writes=1, **{"from": "a",
                                                          "to": "b"})


def _case_lamp_on_the_shoulder():
    """A lamp at exactly half_width + 0.55 m: DESIGNED OCCUPANCY.  If this
    case ever fails, some other check is about to start reporting the
    world's own lampposts as obstructions."""
    segs = {"road_X": _seg([(0.0, 0.0), (100.0, 0.0)], hw=3.0)}
    lamps = [("piece_groundtorch", 50.0, 31.0, 3.55)]
    corridor = classify_lamps(lamps, segs)
    assert corridor and corridor[0]["designed"], (
        "half_width 3.0 + 0.55 = 3.55 m must read as the designed offset")
    return check_lateral(corridor)


def _case_lamp_in_the_carriageway():
    """MEASURED on Ulfsland: 23 torches stand inside the carriageway, the
    closest 0.081 m from the centreline of T5-temple-dockshore."""
    segs = {"road_X": _seg([(0.0, 0.0), (100.0, 0.0)], hw=3.0)}
    lamps = [("piece_groundtorch", 50.0, 31.0, 0.081)]
    return check_lateral(classify_lamps(lamps, segs))


def _case_spacing_shared_junction():
    """THE CASE A PER-SEGMENT RULE CANNOT FAIL.  Two segments meet at a
    junction; each lights the station from its own side, 12 m apart, and
    each pass was correct about its own chain."""
    segs = {"road_A": _seg([(0.0, 0.0), (60.0, 0.0)]),
            "road_B": _seg([(60.0, 0.0), (60.0, 60.0)])}
    lamps = [("piece_groundtorch", 54.0, 31.0, 3.55),
             ("piece_groundtorch", 63.55, 31.0, 6.0)]
    corridor = classify_lamps(lamps, segs)
    fs, stats = check_spacing(corridor)
    assert stats["same_segment"] == 0, (
        "both lamps are on DIFFERENT segments - that is the whole point of "
        "the case; a per-segment rule sees no pair at all")
    return fs


def _case_spacing_clifftop_is_not_a_cluster():
    """A lamp 12 m above the road is not a cluster with the lamp below it.
    The cylinder says so; a cube would refuse both."""
    segs = {"road_A": _seg([(0.0, 0.0), (60.0, 0.0)])}
    lamps = [("piece_groundtorch", 30.0, 31.0, 3.55),
             ("piece_groundtorch", 34.0, 43.0, 3.55)]
    fs, _ = check_spacing(classify_lamps(lamps, segs))
    return fs


def _case_spacing_ok():
    segs = {"road_A": _seg([(0.0, 0.0), (200.0, 0.0)])}
    lamps = [("piece_groundtorch", 40.0, 31.0, 3.55),
             ("piece_groundtorch", 80.0, 31.0, 3.55)]
    fs, _ = check_spacing(classify_lamps(lamps, segs))
    return fs


def _flat(n: int, y: float = 30.0):
    return [y] * n


def _case_unlit_run_too_long():
    """200 m of road with one lamp at one end.  At both sight distances the
    dark run is longer than the widest gap any biome allows."""
    st = _line(201)
    lamps = [(0, 0.0, 0.0, 31.4)]
    fs, stats = check_unlit({"road_A": st}, {"road_A": _flat(201)},
                            [dict(seg="road_A", x=0.0, y=30.654, z=0.0)])
    return fs


def _case_unlit_number_moves_with_sight_distance():
    """THE LESSON, ASSERTED: the same corridor gives two different worst-run
    numbers at two sight distances, so a report that prints one without the
    other is not reporting a measurement.  This case ACCEPTS the world (the
    runs are inside the limit) and refuses to let the two numbers be
    equal."""
    st = _line(121)
    # Two lamps, 120 m apart: at 30 m sight the middle 59 stations are dark
    # and at 50 m only 19 are.  Same world, same lamps, same surface - two
    # numbers, which is the entire lesson.
    corridor = [dict(seg="road_A", x=0.0, y=30.654, z=0.0),
                dict(seg="road_A", x=120.0, y=30.654, z=0.0)]
    fs, stats = check_unlit({"road_A": st}, {"road_A": _flat(121)}, corridor)
    a = stats["sight_30m"]["unlit_stations"]
    b = stats["sight_50m"]["unlit_stations"]
    assert a != b, (
        f"the unlit count must depend on the sight distance; got {a} at 30 m "
        f"and {b} at 50 m, which would mean the sight distance is not in the "
        "measurement at all")
    return fs


def _case_unlit_ridge_blocks_the_flame():
    """Lamp-to-lamp DISTANCE says these stations are lit; the APPLIED
    SURFACE says a ridge is in the way, and the ridge wins.

    Same 61 m of road and the same two lamps, measured twice: once flat and
    once with a 9 m ridge across stations 8..12.  The case asserts the two
    readings DIFFER, which is the only way to show the profile is in the
    measurement rather than decorating it.  A spacing rule returns the same
    answer for both worlds.
    """
    st = _line(61)
    corridor = [dict(seg="road_A", x=0.0, y=30.654, z=0.0),
                dict(seg="road_A", x=60.0, y=30.654, z=0.0)]
    flat = _flat(61)
    ridge = _flat(61)
    for k in range(8, 13):
        ridge[k] = 40.0
    fs_flat, s_flat = check_unlit({"road_A": st}, {"road_A": flat}, corridor)
    fs, s_ridge = check_unlit({"road_A": st}, {"road_A": ridge}, corridor)
    a = s_flat["sight_30m"]["unlit_stations"]
    b = s_ridge["sight_30m"]["unlit_stations"]
    assert b > a, (
        f"the ridge must darken stations a flat profile leaves lit; got {a} "
        f"unlit flat and {b} unlit with a 9 m ridge across the sightline, "
        "which would mean the check is measuring distance and calling it "
        "visibility")
    return fs


def _neuter_the_lamp_index_to_one_segment():
    """RESTORE THE DEFECT: light a segment from its OWN lamps only.

    `lamp_index` returning nothing for a segment whose lamps were all filed
    under a neighbour IS the check as it stood - `temple` is an endpoint of
    six segments, and this is the state that reported a 192 m unlit run on T3
    while no T3 station is more than 39.1 m from a standing torch and the
    longest run of stations beyond 30 m from ANY torch is 19 m.  Returns its
    own undo.
    """
    mod = sys.modules[__name__]
    real = mod.lamp_index

    def own_segment_only(st, corridor, reach_m):
        # The old rule, restated: a lamp counted for a segment only when
        # `classify_lamps` filed it under that segment.  In this fixture the
        # junction torch is filed under road_A, so road_B's index is EMPTY -
        # which is exactly what the defect did on Ulfsland.
        return [] if not corridor else real(
            st, [l for l in corridor
                 if min(math.hypot(q[1] - l["x"], q[2] - l["z"]) for q in st)
                 <= 3.0], reach_m)
    mod.lamp_index = own_segment_only

    def undo():
        mod.lamp_index = real
    return undo


def _case_unlit_junction_lamp_lights_both_roads():
    """THE CASE A PER-SEGMENT LAMP INDEX CANNOT PASS.

    A T junction on flat ground: road_A runs 60 m east, road_B branches north
    for 100 m, and ONE torch stands 3.0 m off road_A and 4.47 m from road_B's
    first station.  `classify_lamps` files it under road_A because road_A is
    nearer, and a check that lights a segment from its OWN lamps then reports
    road_B as 100 m of unbroken darkness with a flame five metres away.

    THE PRIOR STATE, MEASURED ON ULFSLAND: this section reported a 192 m
    unlit run on T3 at 30 m sight distance while no station on T3 is more
    than 39.1 m from a standing torch and the longest run of stations further
    than 30 m from ANY torch is 19 m.  Five segments meet at the temple; the
    missing 173 m were flames filed under a neighbour.

    The case ACCEPTS the world - 100 m of road with a lamp at one end leaves
    71 m dark at 30 m sight, inside the 90 m bound - and it fails loudly if
    the index is ever narrowed back to one segment's own lamps.
    """
    a = [(float(k), float(k), 0.0) for k in range(61)]
    b = [(float(k), 30.0, float(k)) for k in range(101)]
    corridor = [dict(seg="road_A", x=26.0, y=30.654, z=3.0)]
    fs, stats = check_unlit({"road_A": a, "road_B": b},
                            {"road_A": _flat(61), "road_B": _flat(101)},
                            corridor)
    lit_b = sum(1 for (_j, lx, lz, _f) in lamp_index(b, corridor, 50.0))
    # THE PREMISE IS A REFUSAL, NOT AN ASSERTION, and that is what makes the
    # case neuterable.  `run_cases` restores the defect and requires this
    # case to come out the OTHER way; a raise is neither ACCEPT nor REFUSE,
    # so an `assert` here would make the case fail in both worlds and prove
    # nothing about which one holds the fix in place.
    out = list(fs)
    if lit_b != 1:
        out.append(Finding(
            check="unlit_run", ok=False,
            message=("road_B's lamp index is empty: the only torch in this "
                     "fixture stands 4.47 m from road_B's first station and "
                     "3.0 m from road_A's, so a check that indexes a "
                     "segment's OWN lamps sees nothing at all beside road_B "
                     "and calls 100 m of road dark with a flame five metres "
                     "away"),
            numbers=dict(road_b_lamps_in_index=lit_b)))
    w30 = stats["sight_30m"]["worst_unlit_run_m"]
    if w30 >= 100.0:
        out.append(Finding(
            check="unlit_run", ok=False,
            message=(f"road_B reads {w30:.0f} m unlit; with the junction "
                     f"flame in the index the first ~29 stations are lit, so "
                     f"a reading of the full 100 m means the lamp was filed "
                     f"under road_A and thrown away"),
            numbers=dict(worst_unlit_run_m=w30)))
    return out


def _case_unmeasured_is_not_dark():
    """A station with no applied reading is UNMEASURED.  It must not be
    counted as unlit - an instrument hole reported as a defect is as bad as
    a defect reported as fine - and the count of them is printed."""
    st = _line(31)
    prof = [None] * 31
    fs, stats = check_unlit({"road_A": st}, {"road_A": prof}, [])
    assert stats["sight_30m"]["stations_with_no_applied_reading"] == 31
    assert stats["sight_30m"]["unlit_stations"] == 0
    return fs


CASES = [
    Case("lamp_on_the_shoulder", False, _case_lamp_on_the_shoulder,
         prior_state="338 of this world's 388 corridor torches stand at "
                     "exactly half_width + 0.55 m"),
    Case("lamp_in_the_carriageway", True, _case_lamp_in_the_carriageway,
         prior_state="live on Ulfsland: 23 torches inside the carriageway, "
                     "the closest 0.081 m from T5's centreline"),
    Case("spacing_shared_junction", True, _case_spacing_shared_junction,
         prior_state="34 pairs under 30 m were two segments lighting one "
                     "station from opposite sides"),
    Case("spacing_clifftop_not_a_cluster", False,
         _case_spacing_clifftop_is_not_a_cluster),
    Case("spacing_ok", False, _case_spacing_ok),
    Case("unlit_run_too_long", True, _case_unlit_run_too_long),
    Case("unlit_moves_with_sight_distance", False,
         _case_unlit_number_moves_with_sight_distance,
         prior_state="the same corridor read 40 m worst-unlit at 30 m sight "
                     "and 20 m at 50 m"),
    Case("unlit_ridge_blocks_flame", False,
         _case_unlit_ridge_blocks_the_flame),
    Case("unlit_junction_lamp_lights_both_roads", False,
         _case_unlit_junction_lamp_lights_both_roads,
         prior_state="T3 read a 192 m unlit run at 30 m sight distance while "
                     "no T3 station is more than 39.1 m from a standing "
                     "torch: the flames at the temple were filed under five "
                     "different segments and each road was lit only from its "
                     "own",
         neuter=_neuter_the_lamp_index_to_one_segment,
         neuters="lamp_index narrowed to the segment's own lamps, so a "
                 "junction flame lights neither road"),
    Case("unmeasured_is_not_dark", False, _case_unmeasured_is_not_dark),
]

register(Section(
    name="lighting",
    measures=("every built carriageway has a lamp flame in view from eye "
              "height over the applied surface - reported at two sight "
              "distances because the number depends on it - every corridor "
              "lamp stands at its designed lateral offset rather than in "
              "the carriageway, and no two lamps are closer than the "
              "tightest published minimum anywhere on the network"),
    instrument=("the save's object census for every ground-torch variant; "
                "the ledger's own terrain_write profiles for the "
                "centrelines and each road's OWN half width; "
                "run_waterproof.sh FROM_SAVE=1 Heightmap.GetHeight for the "
                "applied surface at every 1 m station; LampProbe.cs for the "
                "flame's height above the pivot"),
    population=("every piece_groundtorch / _wood / _green / _blue / _mist "
                "ZDO in the save within 12 m of a built centreline - "
                "structure lamps included, because a torch in a house 11 m "
                "off the carriageway lights the carriageway - split into "
                "those at the designed half_width + 0.55 m, those inside "
                "the carriageway, and those standing further back"),
    run=run,
    cases=CASES))
