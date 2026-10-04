#!/usr/bin/env python3
"""IS THERE WATER UNDER THIS BRIDGE - re-measured, against the ground.

THE DEFECT THIS EXISTS FOR, and it is not a missed case.
=======================================================
Every crossing in this world exists because `route.find_crossings` wrote
`why_terrain_failed: water_body` against a run of stations.  That is a
statement about the GENERATED field, made ONCE, at routing time, and it was
never re-measured against anything afterwards.

Its consequence is total and it is SILENT IN BOTH DIRECTIONS AT ONCE:

  * the RIBBON skips those stations (`is_bridge`), so no `terrain_write`
    ever covers them and they are not in any terrain gate's population;
  * the DECK BUILDER owns them instead, and refuses them as buried - so no
    `bridge_body_place` covers them either.

A crossing that is WRONG therefore deletes a stretch of road from BOTH
owners' populations at the same moment, and NEITHER owner's gate can see it.
The road falls between them and every check on both sides stays green.

MEASURED on Ulfsland 2026-09-22, engine bed at 1 m from the save through
`terraform/run_waterproof.sh FROM_SAVE=1`, over the six declared crossings
that sit on BUILT segments - wet metres, and metres whose BED STANDS ABOVE
THE CROSSING'S OWN DECLARED DECK:

    T4-meadhall-stathub-x01    8 of 12 wet     0 of 12 above deck
    T4-meadhall-stathub-x02   41 of 49 wet     0 of 49
    T5-temple-dockshore-x01   17 of 29 wet     0 of 29
    W5-brgs1-wsouth-x01       39 of 90 wet     0 of 90
    W10-brgs1-treenear-x01    39 of 90 wet     0 of 90
    S12-brgs1-boathouse-x01    2 of 82 wet    32 of 82, worst +2.926 m

`S12-brgs1-boathouse-x01` is a DRY RIDGE rising to 36.796 m against its own
declared deck of 33.870, and its record claims `gap_min_y: 25.24`,
`max_depth_below_plane_m: 4.76`, `stations_flagged: 45`.  Forty-six of that
road's 74 planned stations were struck out of the terrain writer's
population by a water body that is not there.  Its BUILT ribbon is 51.2 m
against a planned 144.9 m and its A terminus reads EXACTLY 33.870: the road
was benched AT DECK LEVEL and then stopped.

THE DISCRIMINATOR IS NOT A WET-METRE THRESHOLD
==============================================
"How much water is enough water" has no principled answer, and it would take
a real bridge away: `W5-brgs1-wsouth-x01` is only 43 % wet and is an 89.44 m
crossing of real water.  The threshold-free statement is the PHYSICAL one:

    A DECK MUST BE ABOVE THE GROUND IT CROSSES.

Where the bed stands above the deck the structure is not a bridge over
anything, it is a slab buried in a hillside, and those stations belong to
the RIBBON.  On this world that separates the one mis-detected crossing from
the other five by 32 metres to 0.

WHY THIS MODULE AND NOT `accept/`
=================================
`accept/sections/roads.py::crossing_premise` asks the same question and is
the gate that JUDGES the finished world.  It cannot be the thing that stops
the write, because by the time it runs the road is already missing.  This is
the same physical statement asked by THE THING THAT WRITES, before it
writes, off the APPLIED ENGINE BED - and `reclaim()` is the repair, so a
mis-detected crossing has somewhere to go besides a refusal.

`accept/` must not import the builder it judges and this module must not
import `accept`, so the constant is stated in both places; `selftest()`
asserts the two are the same number and fails if anybody moves one.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# A deck tile whose walking surface is buried is a hole in the road, and this
# is `roads/boardwalk.py::BURIAL_MAX_M` - the same number the deck builder
# refuses a buried travelled-way tile at, so the two owners cannot disagree
# about what "buried" means.  `selftest()` asserts the equality rather than
# importing it: `boardwalk` imports `ribbon` and `ribbon` imports this
# module, so a load-time import here is a cycle.
BURIAL_MAX_M = 0.30

# The engine's water plane.  `roads/boardwalk.py::WATER_LEVEL_M` and
# `roads/route.py::WATER_LEVEL`; asserted equal in `selftest()`.
WATER_LEVEL_M = 30.0

# THE SLIDE LIMIT, tan(38 deg) per 1 m lattice edge, from
# `Character::c_MinSlideDegreesPlayer`.  It is `accept.instruments.
# STEP_LIMIT_PER_M` and `ribbon.SLIDE_GRADIENT`; `selftest()` asserts the
# equality with ribbon's rather than importing accept, which this module is
# forbidden to import.  There is no step-height constant in Valheim and none
# is invented here.
SLIDE_DEG = 38.0
STEP_LIMIT_PER_M = math.tan(math.radians(SLIDE_DEG))

# A DIAGONAL IS TWO EDGES.  The heightmap lattice is 1 m square and a walker
# crossing a corner passes over the two axis edges through the easier of the
# two shared vertices, so a diagonal sample pair is judged over
# sqrt(2) m of run, never over 1 m.
LATTICE_DIAGONAL_M = math.sqrt(2.0)


# ---------------------------------------------------------------------------
# the physical statement
# ---------------------------------------------------------------------------
def premise(beds: list[float], deck_y, *, unread: int = 0) -> dict:
    """IS THERE WATER UNDER THIS BRIDGE.  `beds` are ENGINE readings.

    Returns a verdict that is one of three things and never a fourth:

      judged=True,  pass=True    a deck here does bridge something
      judged=True,  pass=False   the bed stands ABOVE this crossing's own
                                 declared deck: THE RIBBON OWNS THESE METRES
      judged=False               it could not be asked - no bed was read, or
                                 the crossing declares no deck datum

    An UNJUDGEABLE premise never passes silently.  `pass` is True on the
    unjudged branches only so a caller that ignores `judged` cannot be made
    to refuse a crossing nobody measured; every caller in this repo reads
    `judged` and says which of the three it got.
    """
    n = len(beds)
    if not n:
        return dict(**{"pass": True}, judged=False, samples=0,
                    samples_unread=unread,
                    why="no bed was read on this span, so its premise is "
                        "UNMEASURED rather than true")
    wet = sum(1 for b in beds if b < WATER_LEVEL_M)
    if deck_y is None:
        return dict(**{"pass": True}, judged=False, samples=n,
                    samples_unread=unread, wet_samples=wet,
                    why="this crossing declares no deck datum, so 'is the "
                        "bed above the deck' has nothing to compare against")
    deck_y = float(deck_y)
    over = [b - deck_y for b in beds if b - deck_y > BURIAL_MAX_M]
    return dict(**{"pass": not over}, judged=True, samples=n,
                samples_unread=unread, wet_samples=wet,
                deck_y=round(deck_y, 3),
                bed_above_deck_samples=len(over),
                worst_bed_above_deck_m=round(max(over), 3) if over else 0.0,
                limit_m=BURIAL_MAX_M,
                owner=("Ribbon" if over else "Crossings"),
                why=("the bed stands above this crossing's own declared deck "
                     "on these metres: there is no water body here and these "
                     "stations belong to the TERRAIN WRITER, which skipped "
                     "them as is_bridge"
                     if over else
                     "the bed is below the declared deck on every metre "
                     "read, so a deck here does bridge something"))


# ---------------------------------------------------------------------------
# the population: THE STATIONS THE RIBBON WAS MADE TO SKIP
# ---------------------------------------------------------------------------
def bridge_runs(seg: dict) -> list[tuple[int, int]]:
    """The maximal runs of `is_bridge` on this segment, inclusive."""
    br = list(seg.get("is_bridge") or ())
    runs, i, n = [], 0, len(br)
    while i < n:
        if br[i]:
            j = i
            while j + 1 < n and br[j + 1]:
                j += 1
            runs.append((i, j))
            i = j + 1
        else:
            i += 1
    return runs


def assign_runs(seg: dict) -> dict[str, tuple[int, int] | None]:
    """Which `is_bridge` RUN each crossing actually took out of the ribbon.

    `crossings[].stations` LOOKS like the answer AND IS NOT.  It is recorded
    by `route.find_crossings` against the point list as it stood BEFORE
    `network.build_segment` straightens each crossing window and re-samples
    the line, and nothing writes it back.  MEASURED on Ulfsland 2026-09-22:

        T4-meadhall-stathub   680 nodes
          x01 stations [697, 702]   real is_bridge run (613, 619)
          x02 stations [707, 731]   real is_bridge run (622, 648)

    Both recorded windows are PAST THE END OF THE SEGMENT.  Read literally
    and clamped to the node count, they collapse onto station 679 at
    (660, 1090) - 60 m inland, 4.4 m of hillside above a 32.672 m deck - and
    this check condemned BOTH of T4's crossings, one of which is the only
    certified bridge deck in this world.  A clamp turned a stale index into
    a confident wrong answer, which is the exact shape of the defect this
    module exists to stop.

    So the population is READ FROM THE RIBBON'S OWN FLAGS: each crossing is
    matched to the `is_bridge` run whose ENDS are nearest its own banks, one
    run per crossing, and a crossing with no run left over gets `None` and
    is reported UNJUDGED rather than guessed at.
    """
    runs = bridge_runs(seg)
    nodes = seg.get("nodes") or []
    crossings = [c for c in (seg.get("crossings") or ())
                 if (c.get("status") or "detected") != "reclassified"]
    pairs = []
    for c in crossings:
        ax, az = (c.get("bank_a") or {}).get("xz") or (None, None)
        bx, bz = (c.get("bank_b") or {}).get("xz") or (None, None)
        for r, (i, j) in enumerate(runs):
            (px, pz), (qx, qz) = nodes[i], nodes[j]
            if ax is None or bx is None:
                d = float("inf")
            else:
                # both orientations - a run's A end may be the crossing's B
                d = min(math.dist((ax, az), (px, pz))
                        + math.dist((bx, bz), (qx, qz)),
                        math.dist((ax, az), (qx, qz))
                        + math.dist((bx, bz), (px, pz)))
            pairs.append((d, c.get("crossing_id"), r))
    pairs.sort()
    out: dict[str, tuple[int, int] | None] = {
        c.get("crossing_id"): None for c in crossings}
    used_run: set[int] = set()
    for d, cid, r in pairs:
        if out.get(cid) is not None or r in used_run or d == float("inf"):
            continue
        out[cid] = runs[r]
        used_run.add(r)
    return out


def crossing_stations(seg: dict, crossing: dict) -> list[int]:
    """The station indices this crossing takes out of the ribbon.

    NOT the `bank_a`..`bank_b` straight line.  That line is the water-edge
    geometry the crossing search found and it is NOT guaranteed to lie on
    the centreline the ribbon would have written - MEASURED over the sixteen
    northern crossings, `boardwalk.project_onto_centreline` finds lateral
    offsets of 0.00 to 5.05 m, and a 4 m spur benches only +/-2 m.  Asking
    about ground 5 m off the road answers a different question confidently.

    And NOT `crossings[].stations` either - see `assign_runs`.  The
    population that matters is exactly the stations the terrain writer was
    made to skip, and the only record of that is `is_bridge` itself.
    """
    run = assign_runs(seg).get(crossing.get("crossing_id"))
    if run is None:
        return []
    return list(range(run[0], run[1] + 1))


def measure(seg: dict, crossing: dict, bed_at) -> dict:
    """Re-measure ONE crossing's premise against the applied engine bed.

    `bed_at(x, z) -> float | None`.  A station the instrument cannot answer
    for is COUNTED in `samples_unread` and never treated as clean: an absent
    reading is the population-too-small defect this whole file is about.
    """
    stations = crossing_stations(seg, crossing)
    beds, unread, rows = [], 0, []
    deck_y = crossing.get("required_deck_y")
    if deck_y is None:
        deck_y = ((crossing.get("bank_a") or {}).get("road_deck_y"))
    for i in stations:
        x, z = seg["nodes"][i][0], seg["nodes"][i][1]
        y = bed_at(float(x), float(z))
        if y is None:
            unread += 1
            continue
        beds.append(float(y))
        if deck_y is not None and float(y) - float(deck_y) > BURIAL_MAX_M:
            rows.append(dict(station=i, xz=[round(float(x), 2),
                                            round(float(z), 2)],
                             bed=round(float(y), 3),
                             over_the_deck_m=round(float(y) - float(deck_y), 3)))
    v = premise(beds, deck_y, unread=unread)
    if not stations:
        v["why"] = ("this crossing owns no `is_bridge` run on its segment, "
                    "so it is taking no stations out of the terrain writer's "
                    "population and there is nothing here to judge")
    rows.sort(key=lambda r: -r["over_the_deck_m"])
    recorded = crossing.get("stations")
    v.update(crossing_id=crossing.get("crossing_id"),
             segment=seg.get("id"),
             status=crossing.get("status", "detected"),
             why_terrain_failed=crossing.get("why_terrain_failed"),
             stations=([stations[0], stations[-1]] if stations else []),
             stations_in_population=len(stations),
             # THE STALE INDEX, NAMED RATHER THAN SILENTLY CORRECTED.  It is
             # the field every other reader of this record still trusts.
             stations_recorded_on_the_crossing=recorded,
             stations_recorded_agree=(
                 bool(stations) and list(recorded or ()) ==
                 [stations[0], stations[-1]]),
             worst_rows=rows[:8],
             instrument="roads/premise.py::measure over the `is_bridge` run "
                        "this crossing actually owns (NOT its stale "
                        "stations[] field), applied bed = generated patch + "
                        "the ledger's own deltas")
    return v


def gate(seg: dict, bed_at) -> dict:
    """Every crossing THIS SEGMENT declares, re-measured, with denominators.

    The verdict is REFUSED when any crossing on this segment is judged and
    fails - i.e. the ribbon is about to skip stations for a water body that
    is not there.  UNJUDGED crossings are reported with their count and
    never counted as passing.
    """
    crossings = list(seg.get("crossings") or ())
    live = [c for c in crossings
            if (c.get("status") or "detected") != "reclassified"]
    verdicts = [measure(seg, c, bed_at) for c in live]
    failed = [v for v in verdicts if v["judged"] and not v["pass"]]
    unjudged = [v for v in verdicts if not v["judged"]]
    reclassified = [c for c in crossings
                    if (c.get("status") or "") == "reclassified"]
    if failed:
        verdict = "REFUSED"
    elif verdicts and len(unjudged) == len(verdicts):
        # EVERY crossing asked came back unjudgeable.  That is not a clean
        # segment, it is a segment nobody measured, and it must not read as
        # one - the whole defect class this file exists for is an absent
        # reading being taken for a good one.
        verdict = "UNJUDGED"
    else:
        verdict = "CLEAR"
    return dict(
        verdict=verdict,
        segment=seg.get("id"),
        crossings_declared=len(crossings),
        crossings_asked=len(live),
        crossings_already_reclassified=len(reclassified),
        judged=len(verdicts) - len(unjudged),
        failed=len(failed),
        unjudged=len(unjudged),
        verdicts=verdicts,
        population=(f"the {len(live)} crossing(s) of "
                    f"{len(crossings)} this segment declares that are still "
                    f"claiming stations, each re-measured over its OWN "
                    f"stations[] window against the APPLIED bed; "
                    f"{len(reclassified)} already reclassified and not asked "
                    f"again"))


# ---------------------------------------------------------------------------
# CAN A LEVEL CARRIAGEWAY FIT ACROSS THIS GROUND AT ALL?
# ---------------------------------------------------------------------------
def transverse_fit(seg: dict, stations, bed_at, profile=None, *,
                   step_limit_m: float | None = None,
                   batter_grade: float | None = None) -> dict:
    """THE QUESTION NOBODY IN THIS NETWORK EVER ASKED, and S12 is the case.

    `route.find_crossings` classifies an unbuildable run by one of three
    reasons - `clamp_exceeded`, `water_body`, `chasm` - and ALL THREE ARE
    LONGITUDINAL.  They are computed from `t`, the ground height ALONG the
    centreline, against a profile fitted ALONG the centreline.  Nothing in
    the router, and nothing in `ribbon.py` before the rasterisation, ever
    asks whether a LEVEL CARRIAGEWAY FITS ACROSS THE HILL.

    It is not an academic gap.  A road is level across its width, so on a
    sidehill of transverse slope `m` the uphill shoulder must be cut
    `m * (half + shoulder)` below natural ground before the profile has
    done anything at all, and the batter can only recover that at
    `batter_grade - m` per metre of reach.  WHERE `m` APPROACHES THE BATTER
    GRADE THE CUT FACE CANNOT BE TAPERED BY ANY REACH.

    MEASURED on `S12-brgs1-boathouse`'s 45 reclaimable stations, applied bed
    at 1 m either side of the centreline:

        transverse |slope|   min 0.152   mean 0.590   max 1.022 per metre
        against a batter grade of 0.7813 - 8 of 45 stations are AT OR ABOVE
        the batter grade, so their cut face gets WIDER with reach, not
        narrower
        uphill shoulder+1 m step   45 of 45 over 0.7813 m, worst +5.576 m

    So the stretch is not buildable as terrain EITHER, and the route was
    right that no ribbon fits there while being wrong about why - it said
    `water_body` over ground that is 2 metres wet in 82.  This is the test
    that separates "the crossing is mis-detected, hand it back" from "the
    crossing is mis-detected AND THE RIBBON CANNOT TAKE IT", which are two
    different answers and were one before.

    `profile` is the height the carriageway would hold at each station; at
    None the BED itself is used, which is the most favourable possible
    profile - so a refusal here is a refusal for EVERY profile.
    """
    import junction as junctionmod                        # noqa: PLC0415
    import ribbon as ribbonmod                            # noqa: PLC0415
    lim = float(step_limit_m if step_limit_m is not None
                else junctionmod.SLIDE_PER_M)
    bg = float(batter_grade if batter_grade is not None
               else ribbonmod.BATTER_GRADE)
    nodes = seg["nodes"]
    n = len(nodes)
    edge = float(seg["width_m"]) / 2.0 + ribbonmod.SHOULDER_M
    rows, slopes, unread = [], [], 0
    for i in stations:
        a = nodes[max(i - 1, 0)]
        b = nodes[min(i + 1, n - 1)]
        dx, dz = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dz) or 1.0
        ux, uz = -dz / L, dx / L
        x, z = float(nodes[i][0]), float(nodes[i][1])
        c = bed_at(x, z)
        if c is None:
            unread += 1
            continue
        y = float(profile[i]) if profile is not None else float(c)
        best = None
        for sgn in (1.0, -1.0):
            o = sgn * (edge + 1.0)
            g = bed_at(x + ux * o, z + uz * o)
            if g is None:
                continue
            d = float(g) - y
            if best is None or d > best:
                best = d
        p = bed_at(x + ux * 3.0, z + uz * 3.0)
        q = bed_at(x - ux * 3.0, z - uz * 3.0)
        m = abs(float(p) - float(q)) / 6.0 if p is not None and q is not None \
            else None
        if m is not None:
            slopes.append(m)
        if best is None:
            unread += 1
            continue
        rows.append(dict(station=i, xz=[round(x, 2), round(z, 2)],
                         carriageway_y=round(y, 3),
                         uphill_step_m=round(best, 3),
                         transverse_slope_per_m=(None if m is None
                                                 else round(m, 3))))
    over = [r for r in rows if r["uphill_step_m"] > lim]
    at_grade = [v for v in slopes if v >= bg]
    over.sort(key=lambda r: -r["uphill_step_m"])
    return dict(
        verdict=("UNJUDGED" if not rows else
                 "REFUSED" if over else "CLEAR"),
        stations_judged=len(rows), stations_asked=len(list(stations)),
        stations_unread=unread,
        stations_over_the_limit=len(over),
        worst_uphill_step_m=(round(max(r["uphill_step_m"] for r in rows), 3)
                             if rows else None),
        limit_m=round(lim, 4), batter_grade=round(bg, 4),
        carriageway_half_plus_shoulder_m=edge,
        transverse_slope_min=(round(min(slopes), 3) if slopes else None),
        transverse_slope_mean=(round(sum(slopes) / len(slopes), 3)
                               if slopes else None),
        transverse_slope_max=(round(max(slopes), 3) if slopes else None),
        stations_at_or_above_the_batter_grade=len(at_grade),
        worst_rows=over[:8],
        population=(f"the {len(rows)} of {len(list(stations))} station(s) "
                    f"read, each measured 1 m beyond its own uphill "
                    f"shoulder edge at {edge:g} m, against the height a "
                    + ("LEVEL CARRIAGEWAY AT THE BED" if profile is None
                       else "level carriageway at the fitted profile")
                    + f" would hold; {unread} unread"),
        instrument="roads/premise.py::transverse_fit")


# ---------------------------------------------------------------------------
# THE METRES BETWEEN A ROAD'S LAST STATION AND WHAT IT IS NAMED FOR
# ---------------------------------------------------------------------------
def standoff_scan(seg: dict, end: str, target_xz, bed_at, *,
                  water_y: float = WATER_LEVEL_M,
                  step_m: float = 1.0) -> dict:
    """Crossings in the ENDPOINT STAND-OFF, where the detector never looked.

    THE POPULATION DEFECT, and it is structural rather than a missed case.
    `network.build_segment` calls `enforce_pad_edge` - which TRIMS the point
    list back to the pad edge - and only THEN calls
    `route.find_crossings(edge['id'], pts, ...)`.  So the detector's
    population ends at the road's last station.  THE WALKER'S JOURNEY DOES
    NOT: he still has to cross the stand-off to reach the thing the segment
    is named for, and nothing has ever measured that ground for water.

    MEASURED on Ulfsland 2026-09-22, engine bed at 1 m from the save.
    `T9-wttown-dockshore` terminates at (672, -94) with
    `endpoint_standoff_m: {dockshore: 9.1}`; its dock deck stands at
    (691, -94).  Across those metres the bench holds 31.000 for six metres,
    then DROPS 2.174 m at x = 678 into a five-metre channel (678..682,
    minimum 28.826, all five under the 30.0 m plane) before rising to the
    deck.  NO CROSSING IS DECLARED THERE, the detector never looked, and
    the 2.174 m edge is the worst approach step in the whole settlements
    section.  It is a crossing by every test this project has - it is
    simply outside every population.

    `target_xz` is where the walker is actually going, named by the caller
    and recorded on the result, because "the destination" is a different
    point from the planner's node: the node `dockshore` is at (675.4,
    -85.6) and the DECK is 17.6 m away at (691, -94).  A scan to the node
    would have missed this channel too.

    Returns declared-crossing records, ready for `crossings[]`.  It NEVER
    fills the channel and never proposes to: the disposal is a deck.
    """
    nodes = seg["nodes"]
    a = nodes[0] if end.lower() == "a" else nodes[-1]
    ax, az = float(a[0]), float(a[1])
    bx, bz = float(target_xz[0]), float(target_xz[1])
    span = math.hypot(bx - ax, bz - az)
    k = max(1, int(round(span / step_m)))
    rows, unread = [], 0
    for i in range(k + 1):
        u = i / k
        x, z = ax + (bx - ax) * u, az + (bz - az) * u
        y = bed_at(x, z)
        if y is None:
            unread += 1
            rows.append(dict(i=i, xz=[round(x, 2), round(z, 2)], bed=None))
            continue
        rows.append(dict(i=i, xz=[round(x, 2), round(z, 2)],
                         bed=round(float(y), 3)))
    wet = [r for r in rows if r["bed"] is not None and r["bed"] < water_y]
    # maximal runs of WET samples, banked by the last dry sample each side
    runs, i = [], 0
    while i <= k:
        if rows[i]["bed"] is not None and rows[i]["bed"] < water_y:
            j = i
            while j + 1 <= k and rows[j + 1]["bed"] is not None \
                    and rows[j + 1]["bed"] < water_y:
                j += 1
            runs.append((i, j))
            i = j + 1
        else:
            i += 1
    out = []
    for n_, (i0, j0) in enumerate(runs, 1):
        ba = rows[max(i0 - 1, 0)]
        bb = rows[min(j0 + 1, k)]
        if ba["bed"] is None or bb["bed"] is None:
            continue
        beds = [r["bed"] for r in rows[i0:j0 + 1]]
        width = math.dist(ba["xz"], bb["xz"])
        out.append(dict(
            crossing_id=f"{seg['id']}-s{('a' if end.lower() == 'a' else 'b')}"
                        f"{n_:02d}",
            segment_id=seg["id"],
            owner="Crossings", kind="bridge", status="declared",
            in_endpoint_standoff=end.upper(),
            endpoint_standoff_m=(seg.get("endpoint_standoff_m") or {}),
            bank_a={"xz": list(ba["xz"]), "ground_y": ba["bed"],
                    "road_deck_y": None},
            bank_b={"xz": list(bb["xz"]), "ground_y": bb["bed"],
                    "road_deck_y": None},
            span_m=round(width, 2),
            wet_m=len(beds),
            gap_min_y=round(min(beds), 3),
            water_surface_y=water_y,
            why_terrain_failed="water_body",
            drop_into_the_channel_m=round(ba["bed"] - rows[i0]["bed"], 3),
            ribbon_width_m=seg["width_m"],
            bank_level_run_m=6.0,
            single_H_both_banks=True,
            measured=("roads/premise.py::standoff_scan over the APPLIED "
                      "engine bed at 1 m, from this segment's own last "
                      f"station to {list(target_xz)}"),
        ))
    return dict(
        verdict=("UNJUDGED" if unread == len(rows) else
                 "FOUND" if out else "CLEAR"),
        segment=seg["id"], end=end.upper(),
        from_xz=[round(ax, 2), round(az, 2)],
        to_xz=[round(bx, 2), round(bz, 2)],
        span_m=round(span, 2),
        samples=len(rows), samples_unread=unread,
        wet_samples=len(wet), crossings=out, rows=rows,
        population=(f"the {len(rows) - unread} of {len(rows)} sample(s) read "
                    f"at {step_m:g} m between {seg['id']}'s {end.upper()} "
                    f"terminus and {list(target_xz)} - ground that is in NO "
                    f"crossing detector's population, because "
                    f"`network.build_segment` trims the point list to the pad "
                    f"edge BEFORE it calls `route.find_crossings`"),
        instrument="roads/premise.py::standoff_scan")


# ---------------------------------------------------------------------------
def standoff_walk(seg: dict, end: str, target_xz, bed_at, *,
                  water_y: float = WATER_LEVEL_M,
                  limit_per_m: float = STEP_LIMIT_PER_M,
                  step_m: float = 1.0,
                  deck_y: float | None = None) -> dict:
    """CAN A WALKER WALK THE ENDPOINT STAND-OFF.  Not: is it wet.

    `standoff_scan` found the population defect -- the detector's window
    ends at the road's last station and the walker's journey does not -- and
    then asked that window ONE question: is any of it under the water plane.
    That is the question T9 needed.  It is not the only way a stand-off
    stops a walker, and MEASURED on Ulfsland 2026-09-22 the scan's own
    selftest says so out loud: case 15 feeds it a stand-off flat at 31.000
    and gets CLEAR, which is correct for water and blind to everything else.

    R4-vestvik-harbour's B stand-off is that blind spot with a number on it.
    Twenty-two of its twenty-three metres are DRY; it falls monotonically
    from the bench to the quay; and ONE 1 m lattice edge drops 0.799 m
    against tan(38 deg) = 0.7813.  Over by 0.018 m, on a 21 m approach, and
    every water test in this tree calls it CLEAR -- so the quay is not a
    walkable destination and nothing in the population says why.

    A GATE THAT ONLY ASKS "IS THERE WATER" WAVES THROUGH A DRY CLIFF.  This
    is the second question, over the same population, on the same
    instrument, with the same denominators:

      verdict  UNJUDGED   nothing was read; never CLEAR
               BLOCKED    at least one edge exceeds the slide limit, or the
                          walker would have to enter water to continue
               CLEAR      every judged edge is inside the limit

    THE BASELINE IS THE EDGE THE FAILURE HAPPENS ON.  A 0.799 m drop
    averaged over the 21 m approach is a gradient of 0.038 and passes
    anything; it is judged over its own 1 m of run, and a diagonal sample
    pair over `LATTICE_DIAGONAL_M`, because that is the lattice the engine
    actually makes the walker cross.

    `deck_y`, when given, is the walking surface of the thing at the far end
    -- a quay deck is a PLACED PIECE and its top face is not the bed under
    it.  The last edge is then judged from the last dry sample ONTO that
    deck, which is the step the walker really takes.
    """
    nodes = seg["nodes"]
    a = nodes[0] if end.lower() == "a" else nodes[-1]
    ax, az = float(a[0]), float(a[1])
    bx, bz = float(target_xz[0]), float(target_xz[1])
    span = math.hypot(bx - ax, bz - az)
    k = max(1, int(round(span / step_m)))
    rows, unread = [], 0
    for i in range(k + 1):
        u = i / k
        x, z = ax + (bx - ax) * u, az + (bz - az) * u
        y = bed_at(x, z)
        if y is None:
            unread += 1
            rows.append(dict(i=i, xz=[round(x, 2), round(z, 2)], bed=None))
            continue
        rows.append(dict(i=i, xz=[round(x, 2), round(z, 2)],
                         bed=round(float(y), 3)))

    dry = [r for r in rows if r["bed"] is not None and r["bed"] >= water_y]
    wet = [r for r in rows if r["bed"] is not None and r["bed"] < water_y]

    # THE WALKER STOPS AT THE WATER.  Edges are judged over the DRY prefix
    # from the road's own terminus: an edge whose far end is under the plane
    # is not a step a walker takes, it is a swim, and scoring it as a step
    # would report a drowning as a gradient.
    walkable: list[dict] = []
    for r in rows:
        if r["bed"] is None or r["bed"] < water_y:
            break
        walkable.append(r)

    edges = []
    for p, q in zip(walkable, walkable[1:]):
        run = math.dist(p["xz"], q["xz"])
        if run <= 1e-9:
            continue
        # A DIAGONAL IS TWO EDGES: charge it the diagonal's run, never 1 m.
        rise = q["bed"] - p["bed"]
        edges.append(dict(frm=p["xz"], to=q["xz"], i=p["i"],
                          run_m=round(run, 4), rise_m=round(rise, 4),
                          per_m=round(rise / run, 4),
                          over_by_m=round(abs(rise) - limit_per_m * run, 4),
                          over=abs(rise) > limit_per_m * run + 1e-9))

    # AND THE STEP ONTO THE THING AT THE END, which is a placed piece and
    # not the bed.  Without it a stand-off that ends one metre short of a
    # deck it cannot climb reads CLEAR.
    deck_edge = None
    if deck_y is not None and walkable:
        last = walkable[-1]
        run = math.dist(last["xz"], [bx, bz]) or step_m
        rise = float(deck_y) - last["bed"]
        deck_edge = dict(frm=last["xz"], to=[round(bx, 2), round(bz, 2)],
                         run_m=round(run, 4), rise_m=round(rise, 4),
                         per_m=round(rise / run, 4),
                         over_by_m=round(abs(rise) - limit_per_m * run, 4),
                         over=abs(rise) > limit_per_m * run + 1e-9,
                         onto="deck")
        if deck_edge["over"]:
            edges = edges + [deck_edge]

    over = [e for e in edges if e["over"]]
    reached = len(walkable) == len(rows)
    if unread == len(rows):
        verdict = "UNJUDGED"
    elif over or not reached:
        verdict = "BLOCKED"
    else:
        verdict = "CLEAR"

    return dict(
        verdict=verdict, segment=seg["id"], end=end.upper(),
        from_xz=[round(ax, 2), round(az, 2)],
        to_xz=[round(bx, 2), round(bz, 2)],
        span_m=round(span, 2), deck_y=deck_y,
        samples=len(rows), samples_unread=unread,
        dry_samples=len(dry), wet_samples=len(wet),
        reached_the_destination=reached,
        stopped_at=None if reached else (
            walkable[-1]["xz"] if walkable else [round(ax, 2), round(az, 2)]),
        limit_per_m=round(limit_per_m, 4),
        edges_judged=len(edges), edges_over=len(over),
        worst_edge=(max(edges, key=lambda e: abs(e["per_m"]))
                    if edges else None),
        over_edges=over[:12], deck_edge=deck_edge,
        rows=rows,
        population=(f"the {len(edges)} lattice edge(s) between the "
                    f"{len(walkable)} of {len(rows)} sample(s) that are read "
                    f"AND dry, walking from {seg['id']}'s {end.upper()} "
                    f"terminus toward {list(target_xz)} at {step_m:g} m; "
                    f"{unread} unread, {len(wet)} under the {water_y:g} m "
                    f"plane.  This ground is in NO detector's population: "
                    f"`network.build_segment` trims the point list to the "
                    f"pad edge before `route.find_crossings` runs, and "
                    f"`standoff_scan` -- which does look here -- asks only "
                    f"whether it is WET"),
        instrument="roads/premise.py::standoff_walk")

# ---------------------------------------------------------------------------
# the repair: hand the stations back to the ribbon
# ---------------------------------------------------------------------------
def reclaim(seg: dict, verdict: dict, bed_at, *,
            grade_max: float | None = None,
            cut_fill_max: float | None = None,
            near_m: float | None = None,
            drop_wet_ends: bool = True) -> dict:
    """Turn ONE failed crossing back into ribbon, IN PLACE on `seg`.

    Four things change and each one is a fact the world will be held to:

      1. `is_bridge` is cleared over the crossing's own stations - they are
         the terrain writer's again.
      2. THE PROFILE IS REFITTED over those stations against the APPLIED
         BED.  It cannot be left where it is: a crossing's profile is its
         DECK DATUM held flat across the whole window, and on S12 that is
         33.870 m benched across a ridge topping 36.4 m.  Building that as
         terrain is a 2.5 m cut for no reason, which is exactly the shape
         `boardwalk` already refused as 31 buried travelled-way tiles.
         The refit is `route.fit_profile`, the same solver that produced
         every other metre of this network, pinned at both boundaries to
         the stations that are ALREADY IN THE GROUND so this cannot move
         them.
      3. A WET STATION AT AN END IS DROPPED, not filled.  At a harbour a
         fill over ground the seed drew below the water plane removes water
         permanently, and that is a refusal no width or batter can undo.
         The ribbon terminates at the first DRY station and the dropped
         ones are named.  (`drop_wet_ends=False` exists for the selftest,
         never for a build.)
      4. The crossing row is marked `status: reclassified` and carries the
         measurement that reclassified it, so nothing has to be believed.

    `bank_level` rows for the reclassified crossing are removed: there is no
    bank to level to a deck that is not there.
    """
    import numpy as np                                    # noqa: PLC0415
    import route as routemod                              # noqa: PLC0415

    cid = verdict["crossing_id"]
    stations = crossing_stations(
        seg, next(c for c in seg["crossings"]
                  if c.get("crossing_id") == cid))
    if not stations:
        raise SystemExit(f"{cid} claims no stations, so there is nothing to "
                         f"hand back to the ribbon")
    g = float(grade_max if grade_max is not None else seg["grade_limit"])
    cf = float(cut_fill_max if cut_fill_max is not None
               else routemod.CUT_FILL_MAX)

    nodes = seg["nodes"]
    n = len(nodes)
    # THE BED, over EVERY station of this segment - not just the reclaimed
    # window.  `fit_profile` is a chain: pinning the boundary needs the
    # station on the far side of it, and a profile fitted over a window in
    # isolation is a profile with no boundary condition at all.
    bed, unread = [], []
    for i in range(n):
        y = bed_at(float(nodes[i][0]), float(nodes[i][1]))
        if y is None:
            unread.append(i)
            y = float(seg["terrain_y"][i])
        bed.append(float(y))

    # WET ENDS.  Only at the ENDS of the reclaimed window and only where the
    # window reaches the end of the segment: an interior wet station is a
    # ford and the ribbon has always owned those.
    dropped: list[int] = []
    if drop_wet_ends:
        a = stations[0]
        while a <= stations[-1] and a == len(dropped) and \
                bed[a] < WATER_LEVEL_M:
            dropped.append(a)
            a += 1
    keep = [i for i in stations if i not in set(dropped)]

    # CAN THE RIBBON ACTUALLY TAKE THESE METRES?  Asked BEFORE the profile
    # is refitted and BEFORE anything is written back, because a
    # reclassification that hands a road to an owner who cannot build it
    # has not repaired the defect, IT HAS MOVED IT.  The stretch would be
    # out of the crossing's population and refused by the ribbon, which is
    # the same road falling between the same two owners with a different
    # label on it.
    #
    # Judged against THE BED, which is the most favourable profile that
    # exists, so a refusal here holds for every profile - including the one
    # `fit_profile` is about to choose.
    tf = transverse_fit(seg, keep, bed_at)
    if tf["verdict"] == "REFUSED":
        return dict(applied=False, crossing_id=cid,
                    transverse_fit=tf,
                    why=(f"this crossing's premise is FALSE - there is no "
                         f"water body here - AND THE RIBBON CANNOT TAKE THE "
                         f"METRES EITHER: {tf['stations_over_the_limit']} of "
                         f"{tf['stations_judged']} station(s) read leave an "
                         f"uphill shoulder step over {tf['limit_m']} m even "
                         f"with the carriageway laid ON the bed, worst "
                         f"{tf['worst_uphill_step_m']} m, over ground whose "
                         f"transverse slope runs "
                         f"{tf['transverse_slope_min']}-"
                         f"{tf['transverse_slope_max']} per metre "
                         f"(mean {tf['transverse_slope_mean']}) against a "
                         f"batter grade of {tf['batter_grade']} - "
                         f"{tf['stations_at_or_above_the_batter_grade']} of "
                         f"them AT OR ABOVE it, where the cut face gets "
                         f"WIDER with reach. Neither owner can build this "
                         f"alignment. It needs a RE-ROUTE, not a "
                         f"reclassification, and the crossing record is "
                         f"left exactly as it stands."))

    s = np.asarray(seg["station_m"] * np.arange(n, dtype=np.float64)
                   if isinstance(seg.get("station_m"), (int, float))
                   else np.arange(n, dtype=np.float64) * 2.0)
    t = np.asarray(bed, dtype=np.float64)
    lo = t - cf
    hi = t + cf
    # PIN EVERY STATION THAT IS ALREADY IN THE GROUND.  The reclaimed window
    # is the only part of this profile that may move; anything else moving
    # is a re-emit of finished road under the cover of a reclassification.
    mine = set(keep)
    prof = np.asarray(seg["profile_y"], dtype=np.float64)
    for i in range(n):
        if i not in mine:
            lo[i] = hi[i] = prof[i]
    # AND BOUND THE REFIT BY THE GROUND IT WILL RUN BESIDE.
    #
    # MEASURED, by the refit that found it.  S12-brgs1-boathouse is a
    # HAIRPIN: its outbound leg and its return leg pass within 0.00-0.77 m
    # of each other for thirty metres.  A refit that follows the ridge
    # correctly on its own terms raised the outbound leg up to +1.583 m and
    # opened a 2.115 m step at (-296.97, -101.88) BETWEEN THE SEGMENT AND
    # ITSELF - ten pairs over the 0.7813 m slide allowance, every one of
    # them `S12 vs S12`.  `junction.build_gate` refused it correctly, at
    # the write, after the whole profile had been fitted.
    #
    # A repair planned against a rule it will be judged by later is a
    # repair that gets refused later, so the rule is a BOUND here.
    #
    # AND IT IS THE JUNCTION GATE'S OWN GEOMETRY, WHICH IS THE SECOND HALF
    # OF THE SAME FINDING AND TOOK TWO WRONG VERSIONS TO GET RIGHT.
    #
    # `junction.pairs` does not measure centreline to centreline and it does
    # not judge at stations.  It takes the CLOSEST POINT on the other run's
    # EDGES, subtracts BOTH FOOTPRINT HALF-WIDTHS, and asks
    # `slide_allow(gap)` of the remaining gap - so on S12's hairpin the
    # nearest station pair is `centre_dist 4.77 m`, which as a centreline
    # distance buys a 3.73 m allowance and passes 35.453 vs 32.629
    # comfortably, while the SAME PAIR at `gap = 4.77 - 2.0 - 2.0 = 0.769 m`
    # buys 0.7813 m and reads 2.115 m.  Two refits in a row came back
    # byte-identical and the gate refused each with the identical numbers,
    # because the bound was measuring a quantity the gate does not use.
    #
    # So: `half_m = width/2` per run, pair while `gap <= NEAR_MISS_M`, and
    # skip a self-pair whose ARC separation is under
    # `2*half + ADJACENT_ARC_PAD_M` - the station next door is not a
    # junction.  Every constant and the allowance come from `junction`
    # itself; none is retyped.  Sampling is dense on both sides, and a
    # dense pinned point binds BOTH stations of the refit span it falls in:
    # conservative, and expressible as a box bound, which a constraint on
    # an interpolated value is not.
    import junction as junctionmod                        # noqa: PLC0415
    half_m = float(seg["width_m"]) / 2.0
    if near_m is None:
        near_m = 2.0 * half_m + junctionmod.NEAR_MISS_M
    near_m = float(near_m)
    adjacent_arc_m = 2.0 * half_m + junctionmod.ADJACENT_ARC_PAD_M
    DENSE_M = 0.5

    arc = [0.0]
    for k in range(1, n):
        arc.append(arc[-1] + math.dist(nodes[k - 1][:2], nodes[k][:2]))

    def _dense(a: int, b: int):
        """Points every DENSE_M along span a->b: (x, z, y, arc)."""
        ax, az = float(nodes[a][0]), float(nodes[a][1])
        bx, bz = float(nodes[b][0]), float(nodes[b][1])
        L = math.hypot(bx - ax, bz - az)
        k = max(1, int(math.ceil(L / DENSE_M)))
        for m in range(k + 1):
            u = m / k
            yield (ax + (bx - ax) * u, az + (bz - az) * u,
                   float(prof[a]) + (float(prof[b]) - float(prof[a])) * u,
                   arc[a] + (arc[b] - arc[a]) * u)

    pinned_pts = []
    for j in range(n - 1):
        if j in mine or (j + 1) in mine:
            continue            # a span with a refitted end is not pinned
        pinned_pts.extend(_dense(j, j + 1))
    bound_rows = []
    spans = [(i, i + 1) for i in range(n - 1)
             if i in mine or (i + 1) in mine]
    for a, b in spans:
        for (dx, dz, _dy, darc) in _dense(a, b):
            # THE NEARER STATION TAKES THE BOUND.  `junction` reads A's
            # height at the shared point by interpolating between A's two
            # stations, so the station nearer that point is the one the
            # reading is mostly made of.  Binding BOTH ends of every span
            # was the first version and it is not merely conservative, it
            # is WRONG BY CONSTRUCTION at a hairpin: it charges a station
            # the allowance owed two metres further along its own road, and
            # on S12 it made the fit INFEASIBLE at stations 44-46 - a
            # refusal produced by the bound rather than by the ground.
            i = a if abs(darc - arc[a]) <= abs(darc - arc[b]) else b
            if i not in mine:
                continue
            for (px, pz, py, parc) in pinned_pts:
                if abs(darc - parc) < adjacent_arc_m:
                    continue     # the station next door is not a junction
                dist = math.hypot(dx - px, dz - pz)
                if dist > near_m:
                    continue
                gap = dist - 2.0 * half_m
                allow = junctionmod.slide_allow(max(0.0, gap))
                nl, nh = py - allow, py + allow
                if nl > lo[i] or nh < hi[i]:
                    bound_rows.append(dict(
                        station=i,
                        xz=[round(float(nodes[i][0]), 2),
                            round(float(nodes[i][1]), 2)],
                        against_xz=[round(px, 2), round(pz, 2)],
                        centre_dist_m=round(dist, 3),
                        gap_m=round(max(0.0, gap), 3),
                        neighbour_y=round(py, 3),
                        allow_m=round(allow, 4)))
                lo[i], hi[i] = max(lo[i], nl), min(hi[i], nh)
    fit = routemod.fit_profile(t, s, g, cut_fill_max=cf, lo=lo, hi=hi)
    if fit["infeasible_spans"]:
        return dict(applied=False, crossing_id=cid,
                    infeasible_spans=fit["infeasible_spans"],
                    proximity_bounds=len(bound_rows),
                    proximity_rows=bound_rows[:8],
                    why=("no slope-limited profile exists over the reclaimed "
                         "stations inside the clamp with the built stations "
                         "pinned and "
                         f"{len(bound_rows)} proximity bound(s) applied "
                         f"against ground this segment already runs beside: "
                         "this crossing is not a mis-detection, it is "
                         "ground no ribbon can take"))

    before = list(seg["profile_y"])
    y = fit["y"]
    moved = []
    for i in keep:
        if abs(float(y[i]) - before[i]) > 1e-6:
            moved.append(dict(station=i,
                              xz=[round(float(nodes[i][0]), 2),
                                  round(float(nodes[i][1]), 2)],
                              was=round(before[i], 3),
                              now=round(float(y[i]), 3),
                              bed=round(bed[i], 3)))
        seg["profile_y"][i] = round(float(y[i]), 3)
        seg["terrain_y"][i] = round(bed[i], 3)
        seg["is_bridge"][i] = False
    for i in dropped:
        seg["is_bridge"][i] = True          # still not the ribbon's: it is water

    # The crossing row records what happened to it.
    for c in seg["crossings"]:
        if c.get("crossing_id") != cid:
            continue
        c["status"] = "reclassified"
        c["owner"] = "RoadNet"
        c["kind"] = "reclassified_to_terrain"
        c["why_terrain_failed"] = None
        c["reclassified"] = dict(
            by="roads/premise.py::reclaim",
            because=("the applied engine bed stands above this crossing's "
                     "own declared deck on "
                     f"{verdict.get('bed_above_deck_samples')} of "
                     f"{verdict.get('samples')} station(s) read, worst "
                     f"{verdict.get('worst_bed_above_deck_m')} m against a "
                     f"{BURIAL_MAX_M} m limit: there is no water body here"),
            premise=verdict,
            stations_returned_to_the_ribbon=[keep[0], keep[-1]] if keep else [],
            stations_left_as_water=dropped,
            profile_refit=dict(
                solver="roads/route.py::fit_profile",
                grade_max=g, cut_fill_max=cf,
                stations_moved=len(moved),
                stations_in_window=len(keep),
                worst_move_m=round(max((abs(m["now"] - m["was"])
                                        for m in moved), default=0.0), 3),
                max_cut_m=round(max(((bed[i] - float(y[i]))
                                     for i in keep), default=0.0), 3),
                max_fill_m=round(max(((float(y[i]) - bed[i])
                                      for i in keep), default=0.0), 3),
                pinned_stations=n - len(keep),
                proximity_bounds=len(bound_rows),
                proximity_near_m=round(near_m, 2),
                proximity_rows=bound_rows[:8],
                bed_samples_unread=len(unread)),
        )
    seg["bank_level"] = [b for b in (seg.get("bank_level") or ())
                         if b.get("crossing_id") != cid]
    seg["bridged_stations"] = int(sum(1 for v in seg["is_bridge"] if v))
    seg["terrain_stations"] = int(len(seg["is_bridge"]) - seg["bridged_stations"])
    return dict(applied=True, crossing_id=cid,
                stations_returned=len(keep),
                stations_left_as_water=dropped,
                stations_moved=len(moved), moved=moved[:8],
                bed_samples_unread=len(unread),
                bridged_stations=seg["bridged_stations"],
                terrain_stations=seg["terrain_stations"],
                proximity_bounds=len(bound_rows),
                metres_returned=round(len(keep) * float(s[1] - s[0]), 1))


# ---------------------------------------------------------------------------
# the cases
# ---------------------------------------------------------------------------
# S12-brgs1-boathouse-x01's OWN STATE, engine bed at 1 m from save 9a242873
# through `run_waterproof.sh FROM_SAVE=1`, along the crossing's own recorded
# centreline.  A ridge, against a declared deck of 33.870.
_S12_DECK = 33.870
_S12_BEDS = [
    29.193, 29.660, 31.107, 30.940, 31.285, 30.827, 31.648, 31.560, 32.797,
    33.246, 33.660, 33.879, 33.574, 34.078, 33.895, 33.602, 34.146, 34.814,
    34.496, 33.984, 34.261, 34.281, 35.019, 34.990, 35.123, 36.183, 36.174,
    36.796, 36.408, 36.763, 36.468, 35.915, 36.215, 35.993, 36.215, 35.910,
    35.352, 35.606, 35.314, 36.141, 35.600, 35.154, 35.494, 35.081,
]
# W5-brgs1-wsouth-x01, the same instrument, the same night: 43 % wet and a
# REAL 89.44 m crossing.  A wet-metre threshold that cleared S12 would have
# taken this deck away, which is why the discriminator is not one.
_W5_DECK = 32.5
_W5_BEDS = [31.9, 31.2, 30.4, 29.6, 28.8, 27.9, 26.4, 25.1, 24.8, 25.6,
            26.9, 28.1, 29.2, 29.9, 30.6, 31.4, 32.0, 32.19]


def _seg_fixture(beds, deck, first=0):
    """A one-crossing segment whose nodes are the sample line itself."""
    n = len(beds)
    return dict(
        id="FIX-a-b", nodes=[[float(first + i), 0.0] for i in range(n)],
        profile_y=[deck] * n, terrain_y=list(beds),
        is_bridge=[True] * n, station_m=1.0, grade_limit=0.12, width_m=4.0,
        crossings=[dict(crossing_id="FIX-a-b-x01", segment_id="FIX-a-b",
                        stations=[0, n - 1], required_deck_y=deck,
                        status="detected", why_terrain_failed="water_body",
                        bank_a={"xz": [0.0, 0.0], "ground_y": beds[0]},
                        bank_b={"xz": [float(n - 1), 0.0],
                                "ground_y": beds[-1]})],
        bank_level=[dict(crossing_id="FIX-a-b-x01", level_at_y=deck)])


def _bed_from(beds):
    def bed_at(x, _z):
        i = int(round(x))
        return beds[i] if 0 <= i < len(beds) else None
    return bed_at


def selftest() -> int:
    """Both directions, no world, no server, no seed."""
    ok = 0

    # THE CONSTANTS ARE ONE NUMBER, not two that agree today.
    sys.path.insert(0, str(JUMPSTART / "roads"))
    import boardwalk as bw                                # noqa: PLC0415
    assert bw.BURIAL_MAX_M == BURIAL_MAX_M, (
        f"boardwalk refuses a buried travelled-way tile at "
        f"{bw.BURIAL_MAX_M} m and this module reclassifies a crossing at "
        f"{BURIAL_MAX_M} m: the two owners would disagree about the same "
        f"metre")
    assert bw.WATER_LEVEL_M == WATER_LEVEL_M
    ok += 1

    # 1. S12's OWN STATE IS REFUSED.  This is the case the whole module is
    #    for: the known-bad state must come out FAILED.
    v = premise(_S12_BEDS, _S12_DECK)
    assert v["judged"] and not v["pass"], v
    assert v["owner"] == "Ribbon", v
    assert v["bed_above_deck_samples"] >= 24, v
    assert v["worst_bed_above_deck_m"] >= 2.9, v
    assert v["wet_samples"] == 2, v
    ok += 1

    # 2. A REAL CHANNEL KEEPS ITS DECK, and it is only 43 % wet - so a
    #    wet-metre threshold in place of the physical test would have taken
    #    this deck away.
    w = premise(_W5_BEDS, _W5_DECK)
    assert w["judged"] and w["pass"], w
    assert w["owner"] == "Crossings", w
    assert w["bed_above_deck_samples"] == 0, w
    assert 0 < w["wet_samples"] < len(_W5_BEDS), w
    ok += 1

    # 3. AN UNJUDGEABLE PREMISE SAYS SO rather than passing quietly.
    u1 = premise(_S12_BEDS, None)
    u2 = premise([], _S12_DECK)
    assert not u1["judged"] and not u2["judged"], (u1, u2)
    assert u2["samples"] == 0 and u1["samples"] == len(_S12_BEDS)
    ok += 1

    # 4. AN UNREAD STATION IS COUNTED, NEVER CLEAN.  A bed_at that answers
    #    for half the window must not produce a verdict over half a road.
    seg = _seg_fixture(_S12_BEDS, _S12_DECK)
    half = _bed_from(_S12_BEDS[:len(_S12_BEDS) // 2])
    m = measure(seg, seg["crossings"][0], half)
    assert m["samples_unread"] == len(_S12_BEDS) - len(_S12_BEDS) // 2, m
    assert m["stations_in_population"] == len(_S12_BEDS), m
    ok += 1

    # 5. THE GATE REFUSES THE SEGMENT, and names both denominators.
    seg = _seg_fixture(_S12_BEDS, _S12_DECK)
    rep = gate(seg, _bed_from(_S12_BEDS))
    assert rep["verdict"] == "REFUSED", rep
    assert rep["crossings_declared"] == 1 and rep["judged"] == 1, rep
    ok += 1

    # 6. AND IT CLEARS ONE THAT IS REALLY A CROSSING.  A gate that only ever
    #    refuses is not a discriminator.
    segw = _seg_fixture(_W5_BEDS, _W5_DECK)
    repw = gate(segw, _bed_from(_W5_BEDS))
    assert repw["verdict"] == "CLEAR", repw
    assert repw["judged"] == 1 and repw["failed"] == 0, repw
    ok += 1

    # 7. THE REPAIR HANDS THE STATIONS BACK, REFITS THE PROFILE OFF THE BED,
    #    AND LEAVES THE WET END AS WATER.  S12's first two metres are under
    #    the plane; filling them at a harbour removes water permanently.
    seg = _seg_fixture(_S12_BEDS, _S12_DECK)
    seg["is_bridge"] = [True] * len(_S12_BEDS)
    v = measure(seg, seg["crossings"][0], _bed_from(_S12_BEDS))
    r = reclaim(seg, v, _bed_from(_S12_BEDS))
    assert r["applied"], r
    assert r["stations_left_as_water"] == [0, 1], r
    assert r["stations_returned"] == len(_S12_BEDS) - 2, r
    assert seg["is_bridge"][0] and seg["is_bridge"][1], seg["is_bridge"][:4]
    assert not any(seg["is_bridge"][2:]), seg["is_bridge"]
    assert seg["crossings"][0]["status"] == "reclassified"
    assert seg["bank_level"] == []
    # THE PROFILE IS NO LONGER THE DECK DATUM HELD FLAT ACROSS A RIDGE.
    ridge = _S12_BEDS.index(max(_S12_BEDS))
    assert seg["profile_y"][ridge] > _S12_DECK + 0.3, seg["profile_y"][ridge]
    # and it is grade-legal on every edge it moved
    for i in range(3, len(_S12_BEDS)):
        d = abs(seg["profile_y"][i] - seg["profile_y"][i - 1])
        assert d <= seg["grade_limit"] * 1.0 + 1e-6, (i, d)
    ok += 1

    # 7b. THE SIDEHILL.  S12's OWN transverse numbers: the reclaimed
    #     stations run across ground whose transverse slope averages 0.590
    #     per metre against a batter grade of 0.7813, 8 of 45 AT OR ABOVE
    #     it, and 45 of 45 leave an uphill shoulder step over the limit,
    #     worst +5.576 m.  A reclassification that handed those metres to
    #     the ribbon would have moved the defect, not repaired it: the
    #     ribbon refuses them at the rim and the road falls between the
    #     same two owners with a different label on it.
    _SIDEHILL_M = 0.75          # per metre, across the road
    side = _seg_fixture(_S12_BEDS, _S12_DECK)
    side["width_m"] = 4.0

    def _side_bed(x, z):
        i = int(round(x))
        if not (0 <= i < len(_S12_BEDS)):
            return None
        # the centreline runs along +x, so z IS the transverse offset
        return _S12_BEDS[i] + _SIDEHILL_M * float(z)
    sv = measure(side, side["crossings"][0], _side_bed)
    assert sv["judged"] and not sv["pass"], sv
    sr = reclaim(side, sv, _side_bed)
    assert sr["applied"] is False, sr
    tf = sr["transverse_fit"]
    assert tf["verdict"] == "REFUSED", tf
    assert tf["stations_over_the_limit"] == tf["stations_judged"], tf
    assert abs(tf["transverse_slope_mean"] - _SIDEHILL_M) < 0.01, tf
    # NOTHING WAS WRITTEN BACK: the crossing record is left as it stands.
    assert side["crossings"][0]["status"] == "detected", side["crossings"][0]
    assert all(side["is_bridge"]), side["is_bridge"]
    ok += 1

    # 7c. AND A SHELF IS TAKEN.  The same beds on FLAT ground across the
    #     road reclaim normally - so the refusal is about the sidehill and
    #     not about the stretch being long, high or previously bridged.
    shelf = _seg_fixture(_S12_BEDS, _S12_DECK)
    shelf["width_m"] = 4.0
    sh_v = measure(shelf, shelf["crossings"][0], _bed_from(_S12_BEDS))
    sh_r = reclaim(shelf, sh_v, _bed_from(_S12_BEDS))
    assert sh_r["applied"], sh_r
    assert transverse_fit(shelf, range(2, len(_S12_BEDS)),
                          _bed_from(_S12_BEDS))["verdict"] == "CLEAR"
    ok += 1

    # 7d. NEUTER: ASK THE ROUTER'S QUESTION - the LONGITUDINAL one, the only
    #     one `route.find_crossings` has ever asked - and the sidehill and
    #     the shelf ARE THE SAME RUN.  Identical beds along the centreline,
    #     identical profile, identical wet count, identical everything the
    #     three recorded reasons (`clamp_exceeded`, `water_body`, `chasm`)
    #     are computed from.  The check is only a check because the
    #     transverse read flips it.
    def _longitudinal_only(seg_, sts, bed, profile=None, **_kw):
        ys = [bed(float(seg_["nodes"][i][0]), float(seg_["nodes"][i][1]))
              for i in sts]
        return dict(verdict="CLEAR", along=[round(v, 3) for v in ys if v])
    a = _longitudinal_only(side, range(2, len(_S12_BEDS)), _side_bed)
    b = _longitudinal_only(shelf, range(2, len(_S12_BEDS)),
                           _bed_from(_S12_BEDS))
    assert a == b, "neuter broken: the two fixtures must be longitudinally identical"
    assert transverse_fit(side, range(2, len(_S12_BEDS)),
                          _side_bed)["verdict"] != \
        transverse_fit(shelf, range(2, len(_S12_BEDS)),
                       _bed_from(_S12_BEDS))["verdict"], \
        "the transverse read must separate them"
    ok += 1

    # 8. A SECOND PASS ASKS NOTHING.  A reclassified crossing is out of the
    #    population, so the gate that refused the write does not refuse the
    #    repair that fixed it.
    rep2 = gate(seg, _bed_from(_S12_BEDS))
    assert rep2["verdict"] == "CLEAR", rep2
    assert rep2["crossings_asked"] == 0, rep2
    assert rep2["crossings_already_reclassified"] == 1, rep2
    ok += 1

    # 9. NEUTER: DECIDE THE PREMISE OFF THE RECORD INSTEAD OF THE BED - which
    #    is what the world did - and S12 and W5 SCORE IDENTICALLY.  The check
    #    is only a check because this flips it.
    def by_the_record(c):
        return c.get("why_terrain_failed") == "water_body"
    s12 = _seg_fixture(_S12_BEDS, _S12_DECK)["crossings"][0]
    w5 = _seg_fixture(_W5_BEDS, _W5_DECK)["crossings"][0]
    assert by_the_record(s12) == by_the_record(w5) is True, "neuter broken"
    assert premise(_S12_BEDS, _S12_DECK)["pass"] != \
        premise(_W5_BEDS, _W5_DECK)["pass"], "the measured test must separate them"
    ok += 1

    # 10. THE STALE `stations[]` FIELD, ON T4's OWN NUMBERS.  Read literally
    #     and clamped to the node count, x01's recorded [697, 702] collapses
    #     onto the last station of a 680-node segment - 60 m inland, on a
    #     hillside 1.617 m above its deck - and this check CONDEMNED THE ONLY
    #     CERTIFIED BRIDGE DECK IN THIS WORLD.  The run the ribbon actually
    #     skipped is (613, 619) and it is over water.
    t4 = dict(
        id="T4-fixture",
        nodes=[[560.0 + i, 1033.0 + i] for i in range(680)],
        profile_y=[32.672] * 680, terrain_y=[32.0] * 680,
        is_bridge=[613 <= i <= 619 for i in range(680)],
        station_m=1.0, grade_limit=0.12,
        crossings=[dict(crossing_id="T4-fixture-x01", segment_id="T4-fixture",
                        stations=[697, 702],       # STALE, past the end
                        required_deck_y=32.672, status="detected",
                        why_terrain_failed="water_body",
                        bank_a={"xz": [560.0 + 613, 1033.0 + 613],
                                "ground_y": 28.0},
                        bank_b={"xz": [560.0 + 619, 1033.0 + 619],
                                "ground_y": 28.0})],
        bank_level=[])
    # the bed under the RUN is water; the bed at the clamped station is hill
    def t4_bed(x, _z, _i0=560.0):
        i = int(round(x - _i0))
        return 28.5 if 613 <= i <= 619 else 34.289
    m = measure(t4, t4["crossings"][0], t4_bed)
    assert m["stations"] == [613, 619], m["stations"]
    assert m["stations_recorded_on_the_crossing"] == [697, 702], m
    assert m["stations_recorded_agree"] is False, m
    assert m["judged"] and m["pass"], m
    assert m["bed_above_deck_samples"] == 0 and m["wet_samples"] == 7, m
    ok += 1

    # 11. NEUTER: BELIEVE `stations[]` AND CLAMP IT, which is what the first
    #     version of this module did, and T4's REAL CROSSING IS REFUSED.
    #     The run-matching is only load-bearing because this flips it.
    def clamped(seg, crossing):
        st = crossing.get("stations")
        n = len(seg["nodes"])
        a, b = max(0, min(int(st[0]), n - 1)), max(0, min(int(st[1]), n - 1))
        return list(range(a, b + 1))
    was = globals()["crossing_stations"]
    globals()["crossing_stations"] = clamped
    try:
        bad = measure(t4, t4["crossings"][0], t4_bed)
    finally:
        globals()["crossing_stations"] = was
    assert bad["judged"] and not bad["pass"], bad
    assert bad["stations"] == [679, 679], bad["stations"]
    assert abs(bad["worst_bed_above_deck_m"] - 1.617) < 0.001, bad
    ok += 1

    # 12. THE HAIRPIN.  S12's outbound and return legs pass within 0.00 m of
    #     each other for thirty metres, and a refit that follows the ridge
    #     correctly on its own terms opened a 2.115 m step BETWEEN THE
    #     SEGMENT AND ITSELF - ten pairs over the 0.7813 m slide allowance,
    #     refused at the write by `junction.build_gate` AFTER the whole
    #     profile had been fitted.  The bound is the fix, and the case is:
    #     a refit beside a pinned neighbour must land inside that
    #     neighbour's own slide allowance.
    import junction as _j                                 # noqa: PLC0415
    _NB = 33.0                                            # the pinned return leg
    _N = 20
    # a ridge the fit will want to climb, well outside the allowance
    _RIDGE = [33.0 + 4.0 * (1.0 - abs(i - (_N - 1) / 2.0) / ((_N - 1) / 2.0))
              for i in range(_N)]

    def _hairpin():
        """Outbound 0..N-1 at z=0; the BUILT return leg N..2N-1 at z=0.5,
        running back over the same x, pinned at 33.0."""
        return dict(
            id="HAIR-a-b",
            nodes=[[float(i), 0.0] for i in range(_N)]
            + [[float(_N - 1 - i), 0.5] for i in range(_N)],
            profile_y=[33.87] * _N + [_NB] * _N,
            terrain_y=list(_RIDGE) + [_NB] * _N,
            is_bridge=[True] * _N + [False] * _N,
            width_m=4.0, station_m=1.0, grade_limit=0.12,
            crossings=[dict(crossing_id="HAIR-a-b-x01", segment_id="HAIR-a-b",
                            stations=[0, _N - 1], required_deck_y=33.87,
                            status="detected",
                            why_terrain_failed="water_body",
                            bank_a={"xz": [0.0, 0.0], "ground_y": _RIDGE[0]},
                            bank_b={"xz": [float(_N - 1), 0.0],
                                    "ground_y": _RIDGE[-1]})],
            bank_level=[])

    def _hair_bed(x, z):
        i = int(round(x))
        return _RIDGE[i] if z < 0.25 and 0 <= i < _N else _NB

    def _worst_self_spread(seg):
        w = 0.0
        for i in range(_N):
            gap = math.dist(seg["nodes"][i], seg["nodes"][2 * _N - 1 - i])
            w = max(w, abs(seg["profile_y"][i] - _NB) - _j.slide_allow(gap))
        return w

    hair = _hairpin()
    hv = measure(hair, hair["crossings"][0], _hair_bed)
    assert hv["judged"] and not hv["pass"], hv
    hr = reclaim(hair, hv, _hair_bed)
    assert hr["applied"] and hr["proximity_bounds"] > 0, hr
    assert _worst_self_spread(hair) <= 1e-6, _worst_self_spread(hair)
    ok += 1

    # 13. NEUTER: DROP THE PROXIMITY BOUND and the same refit opens the step
    #     the junction gate refuses. The bound is only load-bearing because
    #     this flips it.
    hair2 = _hairpin()
    hr2 = reclaim(hair2, measure(hair2, hair2["crossings"][0], _hair_bed),
                  _hair_bed, near_m=-1.0)
    assert hr2["applied"] and hr2["proximity_bounds"] == 0, hr2
    assert _worst_self_spread(hair2) > 0.0, (
        "the neuter must reproduce the step the junction gate refused")
    ok += 1

    # 14. T9's UNDECLARED CHANNEL, ON ITS OWN ENGINE NUMBERS.  The bench
    #     holds 31.000 for six metres, DROPS 2.174 m at x = 678 into a five
    #     metre channel (678..682, minimum 28.826, every metre under the
    #     30.0 plane) and rises to the dock deck.  No crossing is declared
    #     there because the detector's population ends at the road's last
    #     station, and this ground is in the 9.1 m dockshore stand-off.
    _T9 = [31.000, 31.000, 31.000, 31.000, 31.000, 31.000,
           28.8265, 28.8409, 29.1772, 29.5084, 29.9902,
           30.2045, 30.3869, 30.5769, 30.712, 30.6883, 30.7812, 30.8039,
           30.6902, 30.5571]
    t9 = dict(id="T9-fixture", width_m=6.0,
              nodes=[[672.0 - i, -94.0] for i in range(4)][::-1],
              endpoint_standoff_m={"dockshore": 9.1})

    def _t9_bed(x, _z):
        i = int(round(x - 672.0))
        return _T9[i] if 0 <= i < len(_T9) else None
    sc = standoff_scan(t9, "B", (691.0, -94.0), _t9_bed)
    assert sc["verdict"] == "FOUND", sc
    assert sc["samples"] == 20 and sc["samples_unread"] == 0, sc
    assert sc["wet_samples"] == 5, sc
    assert len(sc["crossings"]) == 1, sc
    ch = sc["crossings"][0]
    assert ch["bank_a"]["xz"] == [677.0, -94.0], ch
    assert ch["bank_b"]["xz"] == [683.0, -94.0], ch
    assert ch["wet_m"] == 5 and abs(ch["gap_min_y"] - 28.826) < 0.001, ch
    assert abs(ch["span_m"] - 6.0) < 0.01, ch
    assert abs(ch["drop_into_the_channel_m"] - 2.174) < 0.001, ch
    assert ch["in_endpoint_standoff"] == "B", ch
    ok += 1

    # 15. NEUTER: SCAN ONLY THE ROUTED CENTRELINE - the detector's actual
    #     population, which ends at the last station - AND THE CHANNEL IS
    #     NOT THERE.  A world with the channel and a world without it score
    #     identically, which is why it survived every gate.
    blind = standoff_scan(t9, "B", (672.0, -94.0), _t9_bed)
    assert blind["verdict"] == "CLEAR", blind
    assert blind["wet_samples"] == 0 and not blind["crossings"], blind
    # and the same scan over a stand-off with NO channel is also CLEAR, so
    # the discriminator is the water and not the widening of the window
    dry = standoff_scan(t9, "B", (691.0, -94.0),
                        lambda x, _z: 31.0 if 0 <= int(round(x - 672.0)) < 20
                        else None)
    assert dry["verdict"] == "CLEAR" and dry["samples"] == 20, dry
    ok += 1

    # 16. A STAND-OFF NOBODY COULD READ IS UNJUDGED, NEVER CLEAR.
    blindeye = standoff_scan(t9, "B", (691.0, -94.0), lambda *_a: None)
    assert blindeye["verdict"] == "UNJUDGED", blindeye
    assert blindeye["samples_unread"] == blindeye["samples"], blindeye
    ok += 1

    # 17. THE CONSTANT IS ONE NUMBER.  `ribbon.SLIDE_GRADIENT` is what the
    #     writer grades to and this is what the walk is judged on; two
    #     numbers that agree today are how a limit drifts.
    import ribbon as _rb                                  # noqa: PLC0415
    assert abs(_rb.SLIDE_GRADIENT - STEP_LIMIT_PER_M) < 1e-12, (
        f"ribbon grades to {_rb.SLIDE_GRADIENT} and the stand-off walk is "
        f"judged at {STEP_LIMIT_PER_M}: the writer and the check would "
        f"disagree about the same metre")
    assert abs(_rb.SLIDE_ANGLE_DEG - SLIDE_DEG) < 1e-12
    ok += 1

    # 18. R4-vestvik-harbour's B STAND-OFF, ON ITS OWN ENGINE NUMBERS, IS
    #     REFUSED.  The known-bad state: 22 of 23 metres dry, monotone
    #     32.869 -> 29.544, every 1 m edge inside tan(38 deg) EXCEPT ONE --
    #     -0.799 m at (-1034, 1643), over by 0.018 m.  This is the state the
    #     check is REQUIRED to refuse, and it is refused before it is
    #     allowed to pass anything.
    #     THE FAILING EDGE IS BETWEEN TWO DRY SAMPLES, which is the whole
    #     point: a walker who has already stopped at the water is not
    #     evidence about a step, so a fixture whose bad edge ends under the
    #     plane would prove the wrong thing.
    _R4 = [32.869, 32.765, 32.661, 32.557, 32.453, 32.350, 32.246, 32.142,
           32.038, 31.934, 31.830, 31.726, 31.623, 31.519, 31.415, 31.311,
           31.207, 31.103, 31.000,
           30.201,                       # the -0.799 m edge, both ends dry
           30.105, 30.010,
           29.544]                       # the quay metre, under the plane
    r4 = dict(id="R4-fixture", width_m=6.0,
              nodes=[[-1015.0 - i, 1636.0] for i in range(4)][::-1],
              endpoint_standoff_m={"r4-harbour-gateway": 0.0})

    def _r4_bed(x, _z):
        i = int(round(-1015.0 - x))
        return _R4[i] if 0 <= i < len(_R4) else None
    wk = standoff_walk(r4, "B", (-1037.0, 1636.0), _r4_bed)
    assert wk["verdict"] == "BLOCKED", wk
    assert wk["samples"] == 23 and wk["samples_unread"] == 0, wk
    assert wk["dry_samples"] == 22 and wk["wet_samples"] == 1, wk
    assert wk["edges_over"] == 1, wk
    bad = wk["over_edges"][0]
    assert abs(bad["rise_m"] + 0.799) < 1e-6, bad
    assert abs(bad["over_by_m"] - (0.799 - STEP_LIMIT_PER_M)) < 1e-3, bad
    assert 0.017 < bad["over_by_m"] < 0.019, bad
    ok += 1

    # 19. NEUTER: ASK THE WATER QUESTION OVER THE IDENTICAL POPULATION and
    #     the same ground comes out CLEAR.  The window is cut at the last
    #     dry sample so BOTH instruments see exactly the 22 metres that
    #     carry the failing edge -- otherwise the scan answers about a
    #     different metre (the quay's own wet one) and the comparison
    #     proves nothing.  `standoff_scan` already looks here; this is the
    #     proof that WIDENING THE WINDOW was not the discriminator and that
    #     the second question is load-bearing on its own.
    blind2 = standoff_scan(r4, "B", (-1036.0, 1636.0), _r4_bed)
    assert blind2["verdict"] == "CLEAR", blind2
    assert blind2["samples"] == 22 and not blind2["crossings"], blind2
    same = standoff_walk(r4, "B", (-1036.0, 1636.0), _r4_bed)
    assert same["samples"] == blind2["samples"], (same, blind2)
    assert same["verdict"] == "BLOCKED" and same["edges_over"] == 1, same
    ok += 1

    # 20. NEUTER: PUT THE SAME TOTAL DROP BACK AS A GRADIENT over the same
    #     dry window and the check has to come out the other way.  The 22
    #     dry metres fall 2.859 m in total; spread evenly that is 0.136 per
    #     metre, inside the limit by a factor of six -- which is exactly how
    #     an 8 m baseline certified a 6 m cliff.  Same endpoints, same
    #     window, same total fall: THE ONLY THING CHANGED IS WHETHER THE
    #     FALL IS CONCENTRATED IN ONE EDGE.  That makes the per-edge
    #     baseline load-bearing rather than merely present.
    _DRY_N = 22
    _R4_SMOOTH = [_R4[0] - (_R4[0] - _R4[_DRY_N - 1]) * i / (_DRY_N - 1)
                  for i in range(_DRY_N)] + [_R4[-1]]

    def _r4_smooth(x, _z):
        i = int(round(-1015.0 - x))
        return _R4_SMOOTH[i] if 0 <= i < len(_R4_SMOOTH) else None
    sm = standoff_walk(r4, "B", (-1036.0, 1636.0), _r4_smooth)
    assert sm["verdict"] == "CLEAR" and sm["edges_over"] == 0, sm
    assert sm["samples"] == same["samples"], (sm, same)
    assert abs(sm["rows"][0]["bed"] - _R4[0]) < 1e-6, sm["rows"][0]
    assert abs(sm["rows"][-1]["bed"] - _R4[_DRY_N - 1]) < 1e-6, sm["rows"][-1]
    ok += 1

    # 21. THE WALKER STOPS AT THE WATER, and a stand-off he cannot finish is
    #     BLOCKED rather than scored over the metres he did manage.  T9's
    #     own channel: six dry metres, then five under the plane.
    t9w = standoff_walk(t9, "B", (691.0, -94.0), _t9_bed)
    assert t9w["verdict"] == "BLOCKED", t9w
    assert not t9w["reached_the_destination"], t9w
    assert t9w["stopped_at"] == [677.0, -94.0], t9w
    assert t9w["wet_samples"] == 5, t9w
    ok += 1

    # 22. THE STEP ONTO THE DECK IS JUDGED, because a quay deck is a PLACED
    #     PIECE and its walking surface is not the bed under it.  A
    #     stand-off that is flawless right up to a deck it cannot climb is
    #     not a walkable destination.
    flat = standoff_walk(t9, "B", (691.0, -94.0),
                         lambda x, _z: 31.0
                         if 0 <= int(round(x - 672.0)) < 20 else None,
                         deck_y=34.0)
    assert flat["verdict"] == "BLOCKED" and flat["deck_edge"]["over"], flat
    near = standoff_walk(t9, "B", (691.0, -94.0),
                         lambda x, _z: 31.0
                         if 0 <= int(round(x - 672.0)) < 20 else None,
                         deck_y=31.4)
    assert near["verdict"] == "CLEAR" and not near["deck_edge"]["over"], near
    ok += 1

    # 23. A STAND-OFF NOBODY COULD READ IS UNJUDGED, NEVER CLEAR - the same
    #     refusal the scan makes, restated for the walk because a silent
    #     instrument must not be able to certify a road.
    nowt = standoff_walk(t9, "B", (691.0, -94.0), lambda *_a: None)
    assert nowt["verdict"] == "UNJUDGED", nowt
    assert nowt["samples_unread"] == nowt["samples"], nowt
    ok += 1

    print(f"premise selftest: {ok}/{ok} cases, both directions, "
          f"no world and no server")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _bed_reader(seg: dict, seed: str, scratch: Path, actor: str):
    """The APPLIED bed: the generated patch plus the ledger's own deltas.

    The same pair `ribbon.py` rasterises against and `junction.rim_transfer`
    judges with, so this cannot be measuring a different surface from the
    write it is gating.

    THE ZONE COVER IS TAKEN WITH `is_bridge` ALL FALSE, AND THAT IS THE
    POINT.  `ribbon.segment_zones` deliberately drops a bridged station -
    the ribbon writes nothing there, so it needs no patch - and the ground
    under the bridged stations is EXACTLY the ground this question is
    about.  Taking the cover at face value read 31 of W5's 48 stations and
    31 of W10's 48, and reported the other 17 of each as `unread`: an
    honest count, over a population the instrument had excluded by
    construction.
    """
    import ribbon as ribbonmod                            # noqa: PLC0415
    import applied as appliedmod                          # noqa: PLC0415
    zones = ribbonmod.segment_zones(seg["nodes"],
                                    [False] * len(seg["nodes"]),
                                    seg["width_m"],
                                    batter_m=ribbonmod.BATTER_MAX_M)
    patches = ribbonmod.zone_patches(zones, seed, scratch / f"{seg['id']}.bin")
    live = appliedmod.Applied(actor=actor)

    def bed_at(x: float, z: float):
        got = live.surface_at(patches, x, z)
        return None if got is None else float(got[0])
    return bed_at, len(zones)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("op", choices=["report", "reclassify", "selftest"])
    ap.add_argument("--segment", help="segment id, or omit for every segment "
                                      "that declares a crossing")
    ap.add_argument("--segments", default=None,
                    help="the plan file (default: this run's seed's, "
                         "worldseed.segments_path())")
    ap.add_argument("--actor", default="premise")
    ap.add_argument("--apply", action="store_true",
                    help="WRITE the reclassification back into the segments "
                         "file. Without it nothing on disk changes and the "
                         "report is the product.")
    ap.add_argument("--json", help="write the full verdicts here")
    args = ap.parse_args()
    if args.op == "selftest":
        return selftest()

    import yaml                                           # noqa: PLC0415
    import ribbon as ribbonmod                            # noqa: PLC0415
    import worldseed                                      # noqa: PLC0415
    path = Path(args.segments or worldseed.segments_path())
    doc = yaml.safe_load(path.read_text())
    segs = [s for s in doc["segments"]
            if (args.segment is None and (s.get("crossings") or ()))
            or s["id"] == args.segment]
    if not segs:
        raise SystemExit(f"no segment {args.segment} in {path}")
    scratch = ribbonmod.SCRATCH
    scratch.mkdir(parents=True, exist_ok=True)
    seed = doc.get("seed") or ribbonmod.SEED

    report, changed = [], 0
    for seg in segs:
        bed_at, nz = _bed_reader(seg, seed, scratch, args.actor)
        rep = gate(seg, bed_at)
        rep["zones_read"] = nz
        print(f"{seg['id']}: {rep['verdict']} -- {rep['crossings_asked']} of "
              f"{rep['crossings_declared']} crossing(s) asked, {rep['judged']} "
              f"judged, {rep['failed']} FAILED, {rep['unjudged']} unjudgeable, "
              f"{rep['crossings_already_reclassified']} already reclassified")
        for v in rep["verdicts"]:
            head = ("FAILED" if v["judged"] and not v["pass"]
                    else "clear" if v["judged"] else "UNJUDGED")
            print(f"   {head} {v['crossing_id']} over "
                  f"{v['stations_in_population']} station(s) "
                  f"({v.get('samples')} read, {v.get('samples_unread')} "
                  f"unread): {v.get('wet_samples')} wet, "
                  f"{v.get('bed_above_deck_samples')} with the bed above the "
                  f"declared deck {v.get('deck_y')}, worst "
                  f"{v.get('worst_bed_above_deck_m')} m -> owner "
                  f"{v.get('owner')}")
            for r in v.get("worst_rows", [])[:4]:
                print(f"      st{r['station']} {r['xz']} bed {r['bed']} "
                      f"= {r['over_the_deck_m']:+.3f} m over the deck")
        if args.op == "reclassify":
            for v in rep["verdicts"]:
                if not (v["judged"] and not v["pass"]):
                    continue
                r = reclaim(seg, v, bed_at)
                rep.setdefault("reclaims", []).append(r)
                # A REFUSED RECLAIM IS NOT A CHANGE.  Counting it printed
                # "APPLIED: 1 crossing(s) reclassified" over a run that had
                # refused and written nothing into the segment - a verdict
                # and its report disagreeing about the same act.
                if not r["applied"]:
                    print(f"   NOT RECLAIMED {r['crossing_id']}: {r['why']}")
                    continue
                changed += 1
                print(f"   RECLAIMED {r['crossing_id']}: "
                      f"{r['stations_returned']} station(s) "
                      f"({r['metres_returned']} m) back to the ribbon, "
                      f"{len(r['stations_left_as_water'])} left as water "
                      f"{r['stations_left_as_water']}, {r['stations_moved']} "
                      f"profile node(s) moved; segment now "
                      f"{r['terrain_stations']} terrain / "
                      f"{r['bridged_stations']} bridged")
                for m in r["moved"][:6]:
                    print(f"      st{m['station']} {m['xz']} "
                          f"{m['was']} -> {m['now']} (bed {m['bed']})")
        report.append(rep)
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1))
        print(f"wrote {args.json}")
    if args.op == "reclassify" and args.apply and changed:
        path.write_text(yaml.safe_dump(doc, sort_keys=True, width=10 ** 9))
        print(f"APPLIED: {changed} crossing(s) reclassified in {path}")
    elif args.op == "reclassify" and changed:
        print(f"DRY: {changed} crossing(s) would be reclassified; "
              f"nothing written. Pass --apply.")
    return 1 if any(r["verdict"] == "REFUSED" for r in report) else 0


if __name__ == "__main__":
    raise SystemExit(main())
