#!/usr/bin/env python3
"""JUNCTION RECONCILER -- a PLAN-TIME gate that refuses a road network whose
members disagree about the height of ground they share.

WHY THIS FILE EXISTS, AND IT IS A STRUCTURAL HOLE RATHER THAN A MISSED CASE.
`ribbon.transverse_census` already MEASURES junction steps: it walks each
written segment's cross sections and, for every adjacent 1 m pair whose only
written sample belongs to somebody else, records the step as `junction_*`.
Read its own words at ribbon.py:2587-2591 -- pairs "whose only written sample
belongs to another claim ... are that claim's to grade".  That ruling is right
for a per-segment write-time census (a segment cannot re-emit its neighbour)
and it is exactly why a 4.206 m disagreement at the temple survived every
check ever run on this world: FIVE segments each measured it, each correctly
disclaimed it, and no participant owned it.  A junction step is not any single
segment's property.  It is the PLAN's.

So the reconciliation has to happen where the plan is still a plan -- before
any terrain is written, while every participant's profile is still an editable
array -- and it has to be owned by the thing that has all the participants in
hand at once.  That is this file.

WHAT A PARTICIPANT IS.  Anything that claims a height for a patch of ground:

  * a RIBBON -- a road segment's `nodes` + `profile_y` from a `segments.yaml`
    (`roads/ribbon.py`, `roads/north.py`), footprint = the carriageway plus
    shoulder, half-extent `width_m / 2 + route.SHOULDER_M`, which is the band
    `ribbon.stamp` actually paves.
  * a PAD -- a levelled rectangle with a datum: `site_pad`, `portal_hall_pad`,
    any `terrain_write` the schema recognises as a pad.  A PAD IS A
    PARTICIPANT, not scenery.  The hall-apron conflict exists precisely
    because a road arriving at a levelled pad has to meet the pad's datum and
    S1's plan does not: this gate would have refused the pair at plan time
    instead of leaving `writer._pad_footprint_check` to refuse the re-emit
    years later, with the road already in the ground.

Footprints come from `ledger.writer._pad_footprint`'s convention, imported
rather than restated: half-extents are `pad_w / 2 + apron_m` and the position
comes from the site's sibling records.  Restating that convention here is how
you get a gate that disagrees with the write-time guard.

THE TOLERANCE, AND WHERE EVERY DIGIT OF IT COMES FROM.

  MEASURED, from the game's own code: `Character::GetSlideAngle` returns the
  literal 38 for `IsPlayer` (`c_MinSlideDegreesPlayer = 38`).  There is NO
  step-height constant in Valheim -- on terrain the slide angle is the entire
  limit -- so the ceiling is a gradient, not a height, and it becomes a height
  only once you say over what run.

  MEASURED, from the engine's geometry: `Heightmap` samples terrain on a 1 m
  lattice and renders the bilinear surface between them.  Two participants who
  disagree about a shared patch do not blend: a zone holds exactly one
  `_TerrainCompiler`, `ribbon.py` deletes and respawns it, and the union
  resolves each world sample to ONE author.  So the disagreement is realised
  as a discontinuity at the authority seam, and the shortest run the seam can
  occupy is ONE lattice edge.

  Hence, for two participants whose footprints overlap (`gap_m == 0`):

      SLIDE_PER_M = tan(38 deg) = 0.7813 m per 1 m lattice edge

  and for participants that do not overlap, the unauthored ground between them
  is the run available, so the allowance grows with it:

      allow(gap) = SLIDE_PER_M * max(LATTICE_M, gap_m)

  That is tier 1, `IMPASSABLE`, and it is not waivable: over it, a player
  slides, which is the same defect the re-grades were run to remove.  A
  junction step is a step like any other -- the whole point of the operator's
  ruling -- so it is judged by the same constant, on the same run, as the
  8.927 m step N4 was refused for.

  Tier 2, `OVER_BUDGET`, is the segment's own promise: the plan declares
  `grade_limit` (0.08 trunk, 0.12 spur, 0.18/0.25 mountain) and a sample-level
  promise cannot exempt the one sample where two roads meet.

      budget(gap) = min(grade_limit_A, grade_limit_B) * max(LATTICE_M, gap_m)

  Tier 2 is REPORTED, not refused, and the reason is measured rather than
  timid -- see `TIER2_IS_REPORT_ONLY_BECAUSE` below, which carries the survey
  numbers that decided it.  Refusing a junction that is already walkable would
  make this gate worse than no gate.

BOTH DIRECTIONS OR IT IS NOT A CHECK.

    python3 junction.py --selftest --segments roads/segments.yaml \
        --segments roads/north_segments.yaml \
        --ledger ledger/runs/<world>/ledger.jsonl

runs TEN checks, four of them in the refuse direction and six in the pass
direction, and currently reports 10/10 on the recorded Ulfsland plans:

  REFUSE  a synthetic 1.20 m road/road crossing and a synthetic 2.00 m
          road/pad undercut -- nothing world-specific in either
  PASS    the same synthetic fixture repaired
  REFUSE  the recorded temple: 5-way at (-56.0, -34.0), spread 4.206 m
  REFUSE  the recorded S1 / portal-hall pad conflict: 1.675 m at the pad's
          edge, 1.500 m inside its rectangle
  PASS    N0/N0b where they meet: 53.320 vs 53.281 = 0.039 m
  PASS    N0b/R3 where they meet: 45.609 vs 45.609 = 0.000 m, 2.0 m apart
  PASS    the temple once its OWN proposal is applied: 4.206 -> 0.004 m
  REFUSE  a waiver quoting a stale spread -- the acceptance mechanism is the
          obvious second path to PASS, so it is tested both ways too
  REFUSE  the hall apron with an ARC number: 8.00 m of arc short
  PASS    the hall apron once the pad datum is held, with the cost named:
          S1's own grade 0.2507 -> 0.3611

A gate demonstrated only to pass is what produced "WALKABLE, worst 8 m
gradient 0.0801" over 84 impassable locations.  A gate demonstrated only to
fail is worse than none, because it refuses good work -- and three separate
bugs in THIS file were caught by the pass-direction fixtures alone: a
reference that wandered off the thing under test, a longitudinal separation
read as a lattice step, and a 4 m rounding grid that silently dropped one of
the two named conflicts from its own report.

WHAT THIS GATE CANNOT SEE, stated so nobody quotes it past its population:
it reads PLANS.  It compares design heights to design heights.  It does not
read the live surface and it is not a substitute for `run_waterproof.sh
FROM_SAVE=1` or the world acceptance gate (`accept/`); it is the check that stops a conflict
being written in the first place, which is the only cheap moment.  Its
reference (each participant's own declared datum) is NOT derived from the
artefact under test (the ground), so it cannot fail the way walkability did.
Run the two in order: this one refuses a plan before it is written, the
save-side gate refuses a world after it is built.

THE PLANNER NOW SOLVES WHAT THIS GATE READS.  `roads/network.py plan` hands
every routed leg to `route.junction_datums`, which turns every pair
`find_conflicts` returns (at the feet it records, `a_foot_arc`/`b_foot_arc`)
into a constraint on the legs' own stations and solves all legs at once --
see `route.JUNCTION_DATUM_DEPENDS_ON`.  MEASURED on Fresh42's plan: 550
impassable pairs in 8 of 52 places before, 0 after.  So at build time this
gate is the CHECK, not the first place a disagreement is found; a refusal
here on a plan the planner emitted means the two have drifted apart.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
for _p in (str(HERE), str(JUMPSTART / "ledger"), str(JUMPSTART / "terraform")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import yaml  # noqa: E402

# ---------------------------------------------------------------------------
# THE CONSTANTS.  Every one MEASURED, with the thing it was measured from.
# ---------------------------------------------------------------------------

# Character::GetSlideAngle returns the literal 38 for IsPlayer
# (c_MinSlideDegreesPlayer = 38; 45 mounted, 90 monster).
SLIDE_DEG = 38.0
SLIDE_PER_M = math.tan(math.radians(SLIDE_DEG))   # 0.78128562650672

# Heightmap's sample pitch.  The shortest run an authority seam can occupy.
LATTICE_M = 1.0

# The paved band's half-extent is width_m / 2 + this.  Same constant
# `ribbon.stamp` uses for `edge`, imported from its owner so the two cannot
# drift apart.
try:
    from route import SHOULDER_M            # roads/route.py: SHOULDER_M = 1.0
except Exception:                            # pragma: no cover - import guard
    SHOULDER_M = 1.0

# THE CORRIDOR IS THE CARRIAGEWAY, `width_m / 2`, NOT the paved band's outer
# shoulder edge -- "within a corridor width", and for a 6 m trunk meeting a
# 6 m trunk that is 6.0 m centre to centre.  Both readings were MEASURED on
# the recorded Ulfsland plans and the carriageway is the right one, twice
# over:
#
#   * SHOULDER EDGE (half + 1.0 m) makes N0 station 733 and N0b "share" ground
#     at (-134.0, 1422.0): they run PARALLEL 8.004 m apart with their outer
#     shoulder samples brushing, design heights 53.320 and 52.067, and the
#     gate refused a 1.253 m spread there.  N0/N0b is one of the two junctions
#     the operator names as already FLUSH (53.28 = 53.28 where they meet), so
#     that reading refuses a good junction -- the one failure mode worse than
#     no gate at all.
#   * SHOULDER EDGE also drags the temple's reading from the 4.206 m the
#     operator measured at the shared node out to 6.159 m between T3 and T5
#     8.9 m apart, where the two roads have simply diverged. Overstating a
#     conflict is how a reconciliation gets costed against the wrong number.
#
# A LATERAL OFFSET BETWEEN TWO PARALLEL CORRIDORS IS SOMEBODY ELSE'S
# POPULATION, and it is already instrumented: `ribbon.stamp` grades the
# interstice between neighbouring corridors (`interstice_rays`,
# `interstice_left_ungraded`, `batter_clipped_shared_corridor`) and
# `transverse_census` measures what is left as `earthwork_*`. That is the
# batter's job. THIS gate's population is roads that MEET.
#
# A pair whose carriageways miss by more than NEAR_MISS_M is not a junction
# and is not reported. One lattice edge beyond touching: past that there is
# unauthored generated ground between the two and the step is the hillside's,
# not the plan's -- but a road stopping 1 m short of a pad it was supposed to
# meet is still this gate's business, which is why it is not zero.
NEAR_MISS_M = 1.0

# Two parts of the SAME ribbon closer along its own arc than
# `2 * half + ADJACENT_ARC_PAD_M` are the same piece of road, not a junction:
# consecutive stations are 2 m apart and their carriageways necessarily
# overlap.  Their longitudinal agreement is the grade limit's job, not this
# gate's.  Beyond it a ribbon crossing ITSELF is judged like any other pair --
# a switchback stacked over its own lower leg is a junction whoever owns it.
ADJACENT_ARC_PAD_M = 2.0

# Spatial hash cell.  Only affects speed.
CELL_M = 8.0

TIER2_IS_REPORT_ONLY_BECAUSE = (
    "MEASURED over the recorded Ulfsland plans -- 45 segments in 70 ground "
    "runs, 22,639 stations, 58 ledger pads, 7,826 pairs sharing ground in "
    "202 junctions: 4,144 of those pairs are flush to within 1 cm, 1,571 "
    "are over the slide limit (tier 1, refused), and 1,259 more sit between "
    "the design grade and the slide limit. Refusing that middle band would "
    "turn 1,259 walkable readings into refusals, and tier 2 is only 0.08 m "
    "for a trunk meeting a trunk -- a junction is where two independently "
    "fitted profiles come nearest each other, so a few centimetres of "
    "disagreement is normal there and is inside the safety limit by a factor "
    "of ten. 'A reconciler that refuses a good junction is worse than none.' "
    "So it is recorded per pair with its numbers, where a re-emit can close "
    "it for free while it is already in the area, and it is not refused."
)


def slide_allow(gap_m: float) -> float:
    """The impassability ceiling for a disagreement across `gap_m` of ground.

    `max(LATTICE_M, gap)` because the lattice cannot express a run shorter
    than one edge: two authors disagreeing about the same sample hand off
    over exactly one edge however much their footprints overlap.
    """
    return SLIDE_PER_M * max(LATTICE_M, float(gap_m))


def budget_allow(gap_m: float, grade: float) -> float:
    """The participants' own declared grade, over the same run."""
    return float(grade) * max(LATTICE_M, float(gap_m))


# ---------------------------------------------------------------------------
# PARTICIPANTS
# ---------------------------------------------------------------------------

def ribbons_from_segments(doc: dict, source: str) -> list[dict]:
    """Every road segment in a `segments.yaml` as a list of ground RUNS.

    SPLIT AT BRIDGES, and this is not an optimisation.  A bridged station
    writes no ground -- the deck is a structure at its own datum
    (`crossing_contract_with_Crossings.deck_datum_y`) -- so comparing a deck
    height to a neighbouring road's ground height compares two different
    surfaces and invents conflicts at every abutment.  `ribbon.py`'s own
    transverse census skips `br[k] or br[k + 1]` for the same reason.
    """
    out: list[dict] = []
    for seg in doc.get("segments") or ():
        nodes = seg["nodes"]
        prof = seg["profile_y"]
        br = seg.get("is_bridge") or [False] * len(nodes)
        half = float(seg["width_m"]) / 2.0
        edge = half + SHOULDER_M
        grade = float(seg.get("grade_limit") or seg.get("grade_limit_design")
                      or 0.25)
        run: list[tuple[float, float, float]] = []
        runs: list[list] = []
        # THE ORIGINAL STATION INDEX OF EVERY POINT IN EVERY RUN.  A run
        # drops bridged stations, so run-local index k is NOT segment station
        # k and a proposal written back by run index would re-grade the wrong
        # metres of road.  Carried here rather than reconstructed by a caller
        # because the split rule lives here and a second implementation of it
        # is how this project got four answers to one question before.
        idx: list[int] = []
        idxs: list[list[int]] = []
        for n, ((x, z), y, b) in enumerate(zip(nodes, prof, br)):
            if b:
                if len(run) > 1:
                    runs.append(run)
                    idxs.append(idx)
                run = []
                idx = []
                continue
            run.append((float(x), float(z), float(y)))
            idx.append(n)
        if len(run) > 1:
            runs.append(run)
            idxs.append(idx)
        # STATIONS THE RECONCILER MAY NOT MOVE, declared by the plan itself.
        # An abutment's `level_run_m` metres at `level_y` is the Crossings
        # contract (`crossing_contract_with_Crossings.bank_level_run_m`): the
        # deck meets the bank flat, and a reconciliation that tilts that run
        # to buy itself grade budget has moved somebody else's datum to pay
        # for its own. Reading it here is what lets `reconcile` answer
        # "there is not enough road" with an arc number instead of silently
        # eating the level run.
        abuts = []
        for ab in (seg.get("abutments") or ()):
            xz = ab.get("ribbon_end_xz")
            if xz:
                abuts.append((float(xz[0]), float(xz[1]),
                              float(ab.get("level_run_m") or 0.0),
                              ab.get("level_y")))
        for i, r in enumerate(runs):
            arc = [0.0]
            for k in range(1, len(r)):
                arc.append(arc[-1] + math.hypot(r[k][0] - r[k - 1][0],
                                                r[k][1] - r[k - 1][1]))
            fixed = [False] * len(r)
            why_fixed: list[str | None] = [None] * len(r)
            for (ax, az, lrun, _ly) in abuts:
                for k, p in enumerate(r):
                    if math.hypot(p[0] - ax, p[1] - az) <= lrun + 1e-6:
                        fixed[k] = True
                        why_fixed[k] = (f"abutment level run {lrun} m at "
                                        f"({ax}, {az})")
            out.append({
                "kind": "ribbon",
                "id": seg["id"],
                "run": i,
                "label": seg["id"] if len(runs) == 1 else f"{seg['id']}#{i}",
                "cls": seg.get("cls"),
                "edge_m": edge,
                "half_m": half,
                "grade_limit": grade,
                "grade_limit_is_override": bool(
                    seg.get("grade_limit_is_override")),
                "grade_limit_design": seg.get("grade_limit_design"),
                "grade_max_measured": seg.get("grade_max_measured"),
                "xz": [(p[0], p[1]) for p in r],
                "y": [p[2] for p in r],
                "arc": arc,
                "station_index": list(idxs[i]),
                "fixed": fixed,
                "why_fixed": why_fixed,
                "source": source,
            })
    return out


def pads_from_ledger(path: Path) -> list[dict]:
    """Every live pad footprint in a ledger, as participants with a datum.

    THE FOOTPRINT CONVENTION IS IMPORTED, NOT RESTATED.
    `writer._pad_footprint` resolves half-extents (`pad_w / 2 + apron_m`) and
    the rectangle's POSITION (which is not in the `terrain_write` record --
    it comes from the site's sibling `objects_clear`/`spawn_plan`), and
    `writer._pad_claims` keeps ONE claim per site, the LATEST in file order,
    which is the order the live compiler resolves. Re-deriving any of that
    here would produce a plan-time gate that disagrees with the write-time
    guard, which is worse than having neither.
    """
    import writer  # ledger/writer.py, on sys.path above

    recs = []
    with open(path, "r", encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            r.setdefault("file_line", i)
            recs.append(r)
    # target_y is not in the footprint dict, so index the records by site.
    datum: dict[str, float] = {}
    for r in recs:
        p = r.get("params", {})
        if writer._is_pad(p) and p.get("site_id"):
            datum[p["site_id"]] = float(p["target_y"])
    out = []
    for f in writer._pad_claims(recs):
        y = datum.get(f["site_id"])
        if y is None:
            continue
        out.append({
            "kind": "pad",
            "id": f["site_id"],
            "label": f["site_id"],
            "role": f["role"],
            "seq": f["seq"],
            "x": f["x"], "z": f["z"], "hw": f["hw"], "hd": f["hd"],
            "datum_y": y,
            "source": str(path),
        })
    return out


def pads_from_plan(units: list[dict], source: str = "plan") -> list[dict]:
    """Pads that are still a PLAN -- `settlements/build.py` units, which carry
    `x`, `z`, `pad_w`, `pad_d` and a solved `target_y` before anything is
    written.  Same shape as `pads_from_ledger`, so a future world gets the
    gate before its first pad exists in a ledger.
    """
    out = []
    for u in units:
        y = u.get("target_y", (u.get("solved") or {}).get("y"))
        if y is None:
            continue
        ap = float(u.get("apron_m") or 0.0)
        w = float(u.get("pad_w") or u.get("pad_d"))
        d = float(u.get("pad_d") or u.get("pad_w"))
        out.append({"kind": "pad", "id": u["id"], "label": u["id"],
                    "role": u.get("role", "site_pad"), "seq": None,
                    "x": float(u["x"]), "z": float(u["z"]),
                    "hw": w / 2.0 + ap, "hd": d / 2.0 + ap,
                    "datum_y": float(y), "source": source})
    return out


# ---------------------------------------------------------------------------
# GEOMETRY.  Point-to-polyline with the height INTERPOLATED at the closest
# point, because a graded ribbon is a surface between its stations, not a bag
# of 387 dots.  A T-junction landing between two of the through road's
# stations is the normal case, and comparing to the nearest station instead
# reads a 2 m station step as junction disagreement.
# ---------------------------------------------------------------------------

def _closest_on_run(rib: dict, k: int, px: float, pz: float
                    ) -> tuple[float, float, float] | None:
    """Closest point of edge k->k+1 to (px,pz): (dist, y_interp, arc)."""
    xz = rib["xz"]
    ax, az = xz[k]
    bx, bz = xz[k + 1]
    dx, dz = bx - ax, bz - az
    L2 = dx * dx + dz * dz
    if L2 <= 1e-12:
        t = 0.0
    else:
        t = ((px - ax) * dx + (pz - az) * dz) / L2
        t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    cx, cz = ax + dx * t, az + dz * t
    y = rib["y"][k] + (rib["y"][k + 1] - rib["y"][k]) * t
    arc = rib["arc"][k] + (rib["arc"][k + 1] - rib["arc"][k]) * t
    return math.hypot(px - cx, pz - cz), y, arc


def _pad_gap(pad: dict, px: float, pz: float) -> float:
    """Distance from a point to the pad RECTANGLE (0 inside)."""
    dx = max(0.0, abs(px - pad["x"]) - pad["hw"])
    dz = max(0.0, abs(pz - pad["z"]) - pad["hd"])
    return math.hypot(dx, dz)


def _closest_xz(rib: dict, k: int, px: float, pz: float) -> tuple[float, float]:
    """World XZ of the closest point of edge k->k+1 to (px,pz)."""
    ax, az = rib["xz"][k]
    bx, bz = rib["xz"][k + 1]
    dx, dz = bx - ax, bz - az
    L2 = dx * dx + dz * dz
    if L2 <= 1e-12:
        return ax, az
    t = ((px - ax) * dx + (pz - az) * dz) / L2
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return ax + dx * t, az + dz * t


def _pad_nearest(pad: dict, px: float, pz: float) -> tuple[float, float]:
    """The point of the pad RECTANGLE nearest (px,pz) -- itself if inside."""
    return (min(max(px, pad["x"] - pad["hw"]), pad["x"] + pad["hw"]),
            min(max(pz, pad["z"] - pad["hd"]), pad["z"] + pad["hd"]))


def _foot_near(rib: dict, k_hint: int, px: float, pz: float,
               window: int = 8) -> tuple[float, float, float]:
    """`_y_at_near`, plus the ARC along the run of the foot it read.

    The arc is what a plan-time solver needs to turn a pair row into a
    constraint on the ribbon's own stations (`route.junction_datums`): the
    height was read by interpolating between the two stations either side of
    that arc, so the solver binds the SAME reading the gate judges rather
    than re-projecting the shared point with a second implementation of
    "closest point".
    """
    n = len(rib["xz"])
    lo = max(0, k_hint - window)
    hi = min(n - 2, k_hint + window)
    best = None
    for k in range(lo, hi + 1):
        d, y, a = _closest_on_run(rib, k, px, pz)
        if best is None or d < best[1]:
            best = (y, d, a)
    if best is None:                             # a 1-station run
        return rib["y"][0], math.hypot(px - rib["xz"][0][0],
                                       pz - rib["xz"][0][1]), 0.0
    return best


def _y_at_near(rib: dict, k_hint: int, px: float, pz: float,
               window: int = 8) -> tuple[float, float]:
    """A ribbon's design height at (px,pz), searched around `k_hint`.

    Windowed rather than global so the sweep stays linear; `window` stations
    either side is 16 m of road at the 2 m station pitch, which is wider
    than any corridor this gate compares.
    """
    y, d, _a = _foot_near(rib, k_hint, px, pz, window)
    return y, d


def shared_point(px: float, pz: float, cx: float, cz: float,
                 half_a: float, half_b: float) -> tuple[float, float]:
    """A point inside BOTH footprints, given A's station and B's nearest point.

    WHY THE COMPARISON CANNOT BE MADE AT A's STATION.  Reading A at its
    station P and B at its own closest point C compares two DIFFERENT places
    whenever P and C are apart, and where the separation runs ALONG the road
    that difference is the road's own grade, not a step.  MEASURED on the
    junction the operator names as already good: N0 station 768 sits 5.61 m
    behind w-mid on the same bearing N0b leaves on, so N0 reads 54.091 there
    and N0b's nearest claim is its start at 53.281 -- a 0.810 m "impassable
    junction" that is really 5.61 m of 14 % road, inside N0's own 0.25
    limit.  Nothing is stacked at those two points; they are the same
    carriageway, 5.61 m apart.

    So both heights are read at ONE point Q that lies inside both footprints:
    Q divides P->C in the ratio of the two half-widths, which puts it at most
    `half_a` from A and at most `half_b` from B whenever the footprints
    overlap at all.  A pad's half-width is 0 -- its rectangle IS its
    footprint -- so for a road meeting a pad, Q is on the pad's edge, which
    is precisely where "the road MUST grade into the pad's EDGE" is measured.
    """
    span = half_a + half_b
    f = 1.0 if span <= 1e-9 else half_a / span
    return px + (cx - px) * f, pz + (cz - pz) * f


# ---------------------------------------------------------------------------
# THE SWEEP
# ---------------------------------------------------------------------------
# JUNCTION_POPULATION_DEPENDS_ON -- WHAT THIS GATE MEASURES, SAID ONCE, WITH
# THE POPULATION IT ACTUALLY REFUSES.
#
# The gate does NOT mean "two carriageways must agree on a shared height". It
# means: EVERY AUTHORED CLAIM ON A PATCH OF GROUND MUST AGREE ABOUT THAT
# GROUND'S HEIGHT, and a levelled rectangle is such a claim -- the module
# docstring has said so since it was written ("A PAD IS A PARTICIPANT, not
# scenery"), so pads are DECLARED, not silently acquired. What was missing is
# this tuple: the terms the verdict rests on, in a form that can be neutered
# one at a time.
#
# THE POPULATION IS PROXIMITY, NOT KIND. A pair enters only when the two
# footprints come within NEAR_MISS_M = 1.0 m of each other; `spread_m` is a
# HEIGHT difference and is never a distance. MEASURED 2026-09-24 on Fresh42,
# because the two refusals it produced were read as thirty-seven times apart
# and therefore as one gate measuring two populations:
#
#   S11-southcape-lhsouth#2 vs wt-spawn   spread 44.955 m, GAP 0.0 m,
#       pad_inside TRUE at (109.50, -112.40): the carriageway runs THROUGH the
#       pad rectangle at design height 31.085 m while the pad's written datum
#       is 76.04 m. It is not "a settlement 45 m away"; it is zero metres away
#       and forty-five metres apart in height, and the gate is right.
#   T8-stenvik-wttown#1 vs T13-temple-harboursouth  spread 1.207 m over a
#       0.32 m gap against a 0.7813 m allowance at (-39.26, -77.12), beds
#       39.740 and 38.533, both segments BUILT. A step a walker cannot take.
#
# BOTH STAND REFUSED AND NEITHER GATE IS WIDENED. The first is not a false
# positive from a pad joining a road population: it is the ONLY instrument in
# the tree that caught Fresh42's settlements being levelled to Ulfsland's
# datums (`settlements/build.py::PLAN_SCOPE_DEPENDS_ON` carries the census).
# Removing pads from this population would have deleted the one reading that
# found a 45 m error in the ground.
#
# THE NEUTER, BOTH DIRECTIONS, is `find_conflicts(..., depends_on=...)` and
# the arms are asserted in `selftest`:
#   * drop `pad_datums` and the S11 class goes UNSEEN -- a road may run
#     through a levelled rectangle 45 m below it and the gate says AGREED;
#   * drop `pad_datums` and the T8 class STILL REFUSES -- road-to-road is
#     carried by `ribbon_design_heights` alone, so the pad term is not
#     propping up the road verdict;
#   * drop `ribbon_design_heights` and the T8 class goes unseen while the
#     S11 class still refuses. Each term answers for its own population and
#     neither answers for the other's.
JUNCTION_POPULATION_DEPENDS_ON = ("ribbon_design_heights", "pad_datums")

# PAD_GAP_DEPENDS_ON: THE GAP BETWEEN A ROAD AND A PAD IS READ FROM THE ROAD'S
# LEVEL FOOTPRINT (`edge_m` = carriageway half + shoulder), because that is
# where the writer's level ground ends and the pad's begins: `ribbon.stamp`
# paves the shoulder at the road's own profile height, and a pad abuts that
# surface, not the carriageway line inside it.  Road-to-road pairs keep the
# carriageway: their population is roads that MEET, and two shoulders
# touching is the interstice the batter owns (see NEAR_MISS_M above).
#
# THE SHIPPED KEY WAS THE CARRIAGEWAY, and it left the one pad a road is FOR
# outside this population on every arrival.  `network.enforce_pad_edge` trims
# a terminal to the pad grown by the level half-width, so the nearest a
# terminus can stand is `edge_m` from the rectangle, i.e. `SHOULDER_M` (1.0 m)
# past the carriageway -- a gap of exactly NEAR_MISS_M with nothing to spare,
# and any rounding of the plan's 1 cm nodes puts it outside.  MEASURED,
# Fresh42 drive 3: the gate judged 13 termini within 30 m of a pad, and the
# plan's 16 legs have 0 road-vs-pad pairs against the ledger's 59 live pads;
# the stathub hub stands 8.2 m from `complete-station-hub` at 79.45 against
# its 77.67 datum and the gate read a 0.907 m riser onto it on 5 roads.
# `network.ARRIVAL_DEPENDS_ON` carries the
# terminus to the footprint; this term makes the pad it reaches a participant.
# NEUTER `_PAD_GAP_EDGE = "carriageway"` (the shipped key), proven by
# `network.selftest_arrival`.
PAD_GAP_DEPENDS_ON = ("the_level_footprint",)
_PAD_GAP_EDGE = "level"      # level | carriageway (the shipped key)



def find_conflicts(ribbons: list[dict], pads: list[dict],
                   depends_on: tuple = JUNCTION_POPULATION_DEPENDS_ON) -> dict:
    """Every pair of participants that share ground, with its height spread.

    One row per (A, B, A's station): exactly one reading per place, and no
    lossy bucketing.  An earlier version kept only the worst row per 4 m
    cell, and MEASURED on the recorded plans that DROPPED the reading the
    operator names -- S1 against the hall pad at (-300.0, 214.0) shares a
    4 m cell with (-301.41, 215.41), which reads worse, so the named
    location disappeared from the report that was supposed to refuse it.  A
    gate whose output depends on a rounding grid cannot be asserted on.

    The verdict lives on the PAIR because that is where the step physically
    is; `cluster()` then groups rows into junctions for reporting, so the
    temple reads as one five-way junction rather than ten anonymous pairs.
    """
    # Spatial hash of ribbon stations.
    grid: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for ri, rib in enumerate(ribbons):
        for k, (x, z) in enumerate(rib["xz"]):
            grid.setdefault((int(math.floor(x / CELL_M)),
                             int(math.floor(z / CELL_M))), []).append((ri, k))
    max_half = max([r["half_m"] for r in ribbons], default=0.0)

    rows: list[dict] = []

    # --- ribbon vs ribbon ---------------------------------------------------
    #
    # ONE ROW PER (A's STATION, B's RUN), AT B's CLOSEST POINT, AND BOTH
    # HEIGHTS READ AT THE SHARED POINT.  Two earlier versions of this loop
    # were wrong in the same family of way and both were caught by the
    # PASS-direction fixtures rather than by the fail-direction ones:
    #
    #   * offering a row for EVERY edge of B within reach and keeping the
    #     biggest spread let the reference wander off the thing under test,
    #     the same shape of error as averaging a gradient over 8 m;
    #   * reading A at its own station and B at B's closest point compared
    #     two DIFFERENT places, so a longitudinal separation along a shared
    #     bearing read as a lattice step -- see `shared_point`.
    # THE TERM, NOT A FLAG: dropping `ribbon_design_heights` removes the
    # road-to-road population and nothing else.
    for ai, A in enumerate(ribbons if "ribbon_design_heights" in depends_on
                           else ()):
        reach = A["half_m"] + max_half + NEAR_MISS_M
        span = int(math.ceil(reach / CELL_M))
        for i, (px, pz) in enumerate(A["xz"]):
            cx = int(math.floor(px / CELL_M))
            cz = int(math.floor(pz / CELL_M))
            # per candidate run: (dist, closest point, its edge index)
            nearest: dict[int, tuple[float, float, float, int]] = {}
            seen: set[tuple[int, int]] = set()
            for gx in range(cx - span, cx + span + 1):
                for gz in range(cz - span, cz + span + 1):
                    for (bi, k) in grid.get((gx, gz), ()):
                        if bi < ai:
                            continue            # each unordered pair once
                        B = A if bi == ai else ribbons[bi]
                        for kk in (k - 1, k):
                            if kk < 0 or kk + 1 >= len(B["xz"]):
                                continue
                            if (bi, kk) in seen:
                                continue
                            seen.add((bi, kk))
                            dist, _yb, arcb = _closest_on_run(B, kk, px, pz)
                            if bi == ai:
                                # Same ribbon: adjacent stations are not a
                                # junction.  Filtered per EDGE, before the
                                # closest is chosen, so a self-crossing is
                                # still compared against the leg it crosses
                                # rather than against the station next door.
                                if abs(A["arc"][i] - arcb) < (
                                        2 * A["half_m"] + ADJACENT_ARC_PAD_M):
                                    continue
                            cur = nearest.get(bi)
                            if cur is None or dist < cur[0]:
                                nearest[bi] = (dist, kk, arcb)
            for bi, (dist, kk, _arcb) in nearest.items():
                B = A if bi == ai else ribbons[bi]
                gap = dist - A["half_m"] - B["half_m"]
                if gap > NEAR_MISS_M:
                    continue
                # B's closest point, recovered in world XZ
                bxz = _closest_xz(B, kk, px, pz)
                qx, qz = shared_point(px, pz, bxz[0], bxz[1],
                                      A["half_m"], B["half_m"])
                ya, _da, fa = _foot_near(A, i, qx, qz)
                yb, _db, fb = _foot_near(B, kk, qx, qz)
                rows.append({
                    "a": A["label"], "b": B["label"],
                    "a_kind": "ribbon", "b_kind": "ribbon",
                    "self_overlap": bi == ai,
                    "xz": [qx, qz],
                    "a_station_xz": [px, pz],
                    "a_y": ya, "b_y": yb,
                    "spread_m": abs(ya - yb),
                    "gap_m": max(0.0, gap),
                    "grade_limit": min(A["grade_limit"], B["grade_limit"]),
                    "a_station": i,
                    "centre_dist_m": dist,
                    # WHERE ON EACH RUN THE TWO HEIGHTS WERE READ, as arc
                    # along that run: the interpolation the spread is made
                    # of, handed to a plan-time solver so it binds this row
                    # and not a re-projection of it.
                    "a_foot_arc": fa, "b_foot_arc": fb,
                })

    # --- ribbon vs pad ------------------------------------------------------
    # A PAD IS A PARTICIPANT AND IT WINS.  `writer._pad_footprint_check` and
    # `ribbon.stamp`'s `pad` refusal both say so: inside the rectangle the
    # pad's datum is the ground, and the road must arrive at that datum at
    # the pad's EDGE.  A pad's half-width is 0 in `shared_point` -- its
    # rectangle IS its footprint -- so the comparison lands exactly on that
    # edge for a road outside, and on the road's own station for a road
    # inside, which is the second and separate defect `pad_inside` records.
    # Dropping `pad_datums` removes the levelled rectangles from the
    # population -- which is exactly the reading that would have let a road
    # run through wt-spawn 44.955 m below its datum and reported AGREED.
    #
    # THE GAP TO A PAD IS MEASURED FROM THE ROAD'S LEVEL FOOTPRINT, NOT ITS
    # CARRIAGEWAY -- see PAD_GAP_DEPENDS_ON above `find_conflicts`.
    for A in ribbons:
        edge = A["edge_m"] if _PAD_GAP_EDGE == "level" else A["half_m"]
        for pad in (pads if "pad_datums" in depends_on else ()):
            reach_x = pad["hw"] + edge + NEAR_MISS_M
            reach_z = pad["hd"] + edge + NEAR_MISS_M
            for i, (px, pz) in enumerate(A["xz"]):
                if abs(px - pad["x"]) > reach_x or abs(pz - pad["z"]) > reach_z:
                    continue
                d = _pad_gap(pad, px, pz)
                gap = max(0.0, d - edge)
                if gap > NEAR_MISS_M:
                    continue
                cxz = _pad_nearest(pad, px, pz)
                qx, qz = shared_point(px, pz, cxz[0], cxz[1], edge, 0.0)
                ya, _da, fa = _foot_near(A, i, qx, qz)
                rows.append({
                    "a": A["label"], "b": pad["label"],
                    "a_kind": "ribbon", "b_kind": "pad",
                    "self_overlap": False,
                    "xz": [qx, qz],
                    "a_station_xz": [px, pz],
                    "a_y": ya, "b_y": pad["datum_y"],
                    "spread_m": abs(ya - pad["datum_y"]),
                    "gap_m": gap,
                    "grade_limit": A["grade_limit"],
                    "a_station": i,
                    "centre_dist_m": d,
                    "pad_inside": d <= 1e-9,
                    "pad_role": pad["role"],
                    "a_foot_arc": fa,
                })

    # ONE ROW PER (pair, place), keeping the worst -- but keyed on the exact
    # station, never on a rounding grid.
    dedup: dict[tuple, dict] = {}
    for r in rows:
        key = (r["a"], r["b"], r["a_station"])
        cur = dedup.get(key)
        if cur is None or r["spread_m"] > cur["spread_m"]:
            dedup[key] = r
    rows = list(dedup.values())
    for r in rows:
        r["slide_allow_m"] = round(slide_allow(r["gap_m"]), 4)
        r["budget_allow_m"] = round(budget_allow(r["gap_m"],
                                                 r["grade_limit"]), 4)
        r["impassable"] = r["spread_m"] > r["slide_allow_m"]
        r["over_budget"] = (not r["impassable"]
                            and r["spread_m"] > r["budget_allow_m"])
        r["spread_m"] = round(r["spread_m"], 3)
        r["gap_m"] = round(r["gap_m"], 3)
        r["a_y"] = round(r["a_y"], 3)
        r["b_y"] = round(r["b_y"], 3)
        r["xz"] = [round(r["xz"][0], 2), round(r["xz"][1], 2)]
        r["a_station_xz"] = [round(r["a_station_xz"][0], 2),
                             round(r["a_station_xz"][1], 2)]
        r["centre_dist_m"] = round(r["centre_dist_m"], 2)
    rows.sort(key=lambda r: -r["spread_m"])
    return {"pairs": rows}


def _height_at(part: dict, px: float, pz: float) -> tuple[float, float] | None:
    """A participant's DESIGN height at (px,pz), and its distance to it."""
    if part["kind"] == "pad":
        return part["datum_y"], _pad_gap(part, px, pz)
    best = None
    for k in range(len(part["xz"]) - 1):
        ax, az = part["xz"][k]
        if abs(ax - px) > 64.0 or abs(az - pz) > 64.0:
            continue
        d, y, _arc = _closest_on_run(part, k, px, pz)
        if best is None or d < best[1]:
            best = (y, d)
    return best


def cluster(rows: list[dict], parts: dict, radius_m: float = 6.0) -> list[dict]:
    """Group pair rows that are the same place into one junction.

    Union-find over rows whose positions are within `radius_m`, so the
    temple's pairs read as ONE five-way junction instead of ten anonymous
    rows nobody owns.

    THE ANCHOR IS THE MOST-SHARED POINT, NOT THE CENTROID, and the difference
    is the difference between the operator's number and a wrong one.  Five
    roads radiate from the temple, so their carriageways keep overlapping for
    several metres past the meeting point while the profiles diverge; the
    centroid of the pair rows lands near (-51.7, -38.0), where T3 and T5
    differ by more than they do where they actually meet.  The quantity a
    reconciliation has to move is the spread WHERE THEY MEET.

    So the anchor is the candidate position that the GREATEST NUMBER of
    participants actually share -- each within its own carriageway reach --
    tie-broken by the largest spread among those participants.  At the temple
    that is exactly (-56.0, -34.0), where five segments have a station, and
    the spread read there is the 4.206 m the operator measured.  A sixth
    segment (S5) is in the same cluster but does not reach that point; it is
    listed under `participants` with its own height and excluded from
    `spread_m`, because a spread is only a step between things standing on the
    same ground.

    `worst_pair_spread_m` keeps the diverging reading, because it is also
    true and a reader must not have to choose between them blind.
    """
    n = len(rows)
    parent = list(range(n))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    buckets: dict[tuple[int, int], list[int]] = {}
    for i, r in enumerate(rows):
        buckets.setdefault((int(math.floor(r["xz"][0] / radius_m)),
                            int(math.floor(r["xz"][1] / radius_m))),
                           []).append(i)
    for (gx, gz), idx in buckets.items():
        for ddx in (-1, 0, 1):
            for ddz in (-1, 0, 1):
                for j in buckets.get((gx + ddx, gz + ddz), ()):
                    for i in idx:
                        if math.hypot(rows[i]["xz"][0] - rows[j]["xz"][0],
                                      rows[i]["xz"][1] - rows[j]["xz"][1]) \
                                <= radius_m:
                            union(i, j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    out = []
    for idx in groups.values():
        members = [rows[i] for i in idx]
        names: list[str] = []
        for r in members:
            for nm in (r["a"], r["b"]):
                if nm not in names:
                    names.append(nm)
        here = [parts[nm] for nm in names if nm in parts]

        def reach_of(p: dict) -> float:
            return (NEAR_MISS_M if p["kind"] == "pad"
                    else p["half_m"] + NEAR_MISS_M)

        def endpoints_at(px: float, pz: float) -> int:
            """How many participating runs TERMINATE at this point.

            THE THIRD KEY, AND IT IS NOT COSMETIC.  The temple is not a point
            crossing: the five roads leave it along a COMMON corridor, so
            every one of them passes exactly through (-56.0, -34.0) AND
            exactly through (-53.17, -36.83) 4.7 m later, both at zero
            distance, and the count-then-tightness score ties.  What
            distinguishes them is topology: at (-56.0, -34.0) all five runs
            END -- T1 at station 386, T12 at 167, and T3/T5/T13 at 0 -- and
            4.7 m later none of them does.  A junction is where roads
            terminate into each other; the corridor past it is just shared
            road, and its divergence is `worst_pair_spread_m`.
            """
            c = 0
            for q in here:
                if q["kind"] != "ribbon":
                    continue
                for k in (0, len(q["xz"]) - 1):
                    if math.hypot(q["xz"][k][0] - px,
                                  q["xz"][k][1] - pz) <= 0.05:
                        c += 1
                        break
            return c

        anchor = None
        for r in members:
            px, pz = r["xz"]
            got = [_height_at(p, px, pz) for p in here]
            shared = [(p, g) for p, g in zip(here, got)
                      if g is not None and g[1] <= reach_of(p)]
            if len(shared) < 2:
                continue
            # MOST participants, then MOST runs TERMINATING here, then the
            # TIGHTEST coincidence.  Tie-breaking on the largest spread
            # instead was measured to be exactly backwards: it walks the
            # anchor down the shared corridor to wherever the profiles have
            # diverged furthest, which is the opposite of where they meet.
            score = (len(shared), endpoints_at(px, pz),
                     -max(g[1] for _p, g in shared))
            if anchor is None or score > anchor[0]:
                anchor = (score, px, pz, got, shared)
        if anchor is None:                       # no point is shared by two
            worst0 = max(members, key=lambda r: r["spread_m"])
            px, pz = worst0["xz"]
            got = [_height_at(p, px, pz) for p in here]
            anchor = ((0, 0.0), px, pz, got,
                      [(p, g) for p, g in zip(here, got) if g is not None])

        score, ax, az, got, shared = anchor
        heights = {p["label"]: round(g[0], 3)
                   for p, g in zip(here, got) if g is not None}
        at_anchor = {p["label"]: round(g[0], 3) for p, g in shared}
        worst = max(members, key=lambda r: r["spread_m"])
        out.append({
            "at": [round(ax, 2), round(az, 2)],
            "anchor_is": "the candidate position the GREATEST NUMBER of "
                         "participants share, each within its own "
                         "carriageway reach; ties broken by largest spread",
            "ways": len(at_anchor),
            "sharing_at_anchor": sorted(at_anchor),
            "design_heights": at_anchor,
            "spread_m": (round(max(at_anchor.values())
                               - min(at_anchor.values()), 3)
                         if len(at_anchor) > 1 else 0.0),
            # Every participant of the cluster, including any that does NOT
            # reach the anchor, with its own profile read there -- named
            # separately so a height that is not standing on the anchor's
            # ground can never be quoted as part of the anchor's spread.
            "participants": heights,
            "in_cluster_not_at_anchor": sorted(set(heights) - set(at_anchor)),
            "worst_pair_spread_m": worst["spread_m"],
            "worst_pair": [worst["a"], worst["b"]],
            "worst_pair_at": worst["xz"],
            "worst_pair_gap_m": worst["gap_m"],
            "worst_pair_slide_allow_m": worst["slide_allow_m"],
            # TWO DIFFERENT DEFECTS, COUNTED APART.  A pair refused AT the
            # anchor is the junction disagreeing about one patch of ground,
            # and one datum fixes all of them at once.  A pair refused metres
            # down a SHARED CORRIDOR is two roads running side by side at
            # different heights, which no point datum can fix -- that is the
            # router's and the batter's problem, a different population.
            # MEASURED at the temple: 671 pairs in the cluster, 180 of them
            # impassable, of which 8 are AT the anchor (-56.0,-34.0) and 172
            # are the five roads diverging down a common corridor.  One
            # number for both would credit a junction repair with work it did
            # not do, or blame it for work it could not.
            "impassable_pairs": sum(1 for r in members if r["impassable"]),
            "impassable_pairs_at_anchor": sum(
                1 for r in members if r["impassable"]
                and math.hypot(r["xz"][0] - ax, r["xz"][1] - az) <= 1.0),
            "impassable_pairs_in_shared_corridor": sum(
                1 for r in members if r["impassable"]
                and math.hypot(r["xz"][0] - ax, r["xz"][1] - az) > 1.0),
            "over_budget_pairs": sum(1 for r in members if r["over_budget"]),
            "pairs": len(members),
        })
    out.sort(key=lambda g: -max(g["spread_m"], g["worst_pair_spread_m"]))
    return out


# ---------------------------------------------------------------------------
# THE GATE
# ---------------------------------------------------------------------------

def gate(ribbons: list[dict], pads: list[dict],
         accepted: dict | None = None,
         depends_on: tuple = JUNCTION_POPULATION_DEPENDS_ON) -> dict:
    """REFUSE the plan, or pass it, naming every segment and every spread.

    ACCEPTANCE IS PINNED TO THE NUMBER.  A waiver names a pair AND the spread
    it was granted for; if the measured spread has changed by more than 1 cm
    the waiver is VOID and the pair is refused.  A blanket by-name waiver is
    how a 4.206 m conflict becomes a 4.304 m conflict with nobody noticing.

    THE NUMBER IS THE PAIR'S WORST ROW, NOT EACH ROW'S OWN, and that is a
    DEFECT FIX rather than a loosening.  A pair row is one station of A
    against the closest point of B, so two roads sharing a corridor produce
    dozens of rows at dozens of spreads under ONE key.  Comparing the waiver
    to each row meant a waiver written from the reported worst silently
    waived the one row that happened to match and left the rest refused --
    MEASURED on T3-temple-meadhall: 65 refusals collapse to 7 pair keys, and
    waiving all 7 at their reported spreads honoured 10 rows and still
    refused 55.  An escape hatch nobody can operate correctly is not an
    escape hatch; it is a gate that cannot be waived and therefore will be
    bypassed some other way.

    Judging the WORST keeps the whole point: the granted number is the
    ceiling for the entire pair, so a waiver cannot hide a row worse than the
    one it was shown, and it still dies -- in EITHER direction -- the moment
    that ceiling moves by more than 1 cm.
    """
    accepted = accepted or {}
    found = find_conflicts(ribbons, pads, depends_on)
    rows = found["pairs"]
    parts = {p["label"]: p for p in list(ribbons) + list(pads)}
    junctions = cluster(rows, parts)

    impassable = [r for r in rows if r["impassable"]]
    worst_of_pair: dict[tuple[str, str], float] = {}
    for r in impassable:
        k = (r["a"], r["b"])
        worst_of_pair[k] = max(worst_of_pair.get(k, 0.0), r["spread_m"])

    refusals = []
    waived = []
    for r in impassable:
        key = f"{r['a']}|{r['b']}"
        w = accepted.get(key) or accepted.get(f"{r['b']}|{r['a']}")
        pair_worst = worst_of_pair[(r["a"], r["b"])]
        if w and abs(float(w.get("spread_m", -1)) - pair_worst) <= 0.01:
            waived.append(dict(r, waiver_reason=w.get("reason", ""),
                               waiver_granted_at_m=pair_worst))
            continue
        if w:
            r = dict(r, waiver_void=(
                f"accepted at spread {w.get('spread_m')} m, this pair's worst "
                f"measures {pair_worst} m -- the waiver does not cover this"))
        refusals.append(r)

    over_budget = [r for r in rows if r["over_budget"]]
    stations = sum(len(r["xz"]) for r in ribbons)
    return {
        "instrument": "roads/junction.py -- PLAN-TIME reconciler. Reads "
                      "design profiles (segments.yaml nodes/profile_y) and "
                      "pad datums (ledger terrain_write pad claims). It does "
                      "NOT read the live surface.",
        "population": {
            "ribbon_runs": len(ribbons),
            "segments": len({r["id"] for r in ribbons}),
            "stations": stations,
            "pads": len(pads),
            "sources": sorted({r["source"] for r in ribbons}
                              | {p["source"] for p in pads}),
        },
        "tolerance": {
            "slide_deg": SLIDE_DEG,
            "slide_per_m": round(SLIDE_PER_M, 6),
            "slide_source": "Character::GetSlideAngle returns the literal 38 "
                            "for IsPlayer (c_MinSlideDegreesPlayer = 38). "
                            "MEASURED from the game's code. There is no "
                            "step-height constant in Valheim; on terrain the "
                            "slide angle is the entire limit.",
            "lattice_m": LATTICE_M,
            "lattice_source": "Heightmap samples terrain at 1 m and renders "
                              "the bilinear surface between samples; a zone "
                              "holds exactly one _TerrainCompiler, so two "
                              "disagreeing authors hand off across ONE edge.",
            "tier1_refuse": "spread > tan(38) * max(1.0, gap_m)",
            "tier1_at_overlap_m": round(slide_allow(0.0), 4),
            "tier2_report": "spread > min(grade_limit) * max(1.0, gap_m)",
            "tier2_is_report_only_because": TIER2_IS_REPORT_ONLY_BECAUSE,
            "corridor": f"footprints = paved band, half-extent "
                        f"width_m/2 + SHOULDER_M ({SHOULDER_M} m), the same "
                        f"band ribbon.stamp paves; a pair is a junction when "
                        f"the bands come within {NEAR_MISS_M} m",
        },
        # POPULATIONS NAMED.  A pair row is (one station of A, the closest
        # point of B); a long shared corridor therefore contributes many
        # rows and ONE junction, so the two counts answer different
        # questions and neither may be quoted for the other.
        "survey": {
            "pairs_sharing_ground": len(rows),
            "pair_is": "one station of A against the CLOSEST point of B, "
                       "one row per station, no rounding grid",
            "junctions": len(junctions),
            "junctions_with_an_impassable_pair": sum(
                1 for j in junctions if j["impassable_pairs"]),
            "junctions_agreed": sum(1 for j in junctions
                                    if not j["impassable_pairs"]),
            # THE COUNT THAT WAS INVISIBLE.  A cluster reads RECONCILED off
            # its anchor spread while holding impassable pairs metres away,
            # and until this line the only place that showed was a per-
            # junction field nobody printed.  MEASURED on Ulfsland: 19 of
            # 204 clusters, 312 impassable pairs, worst the TEMPLE -- anchor
            # spread 0.004 m, 156 impassable pairs, none at the anchor.
            "junctions_flush_at_anchor_but_impassable_in_corridor": sum(
                1 for j in junctions if j["impassable_pairs"]
                and not j["impassable_pairs_at_anchor"]),
            "impassable_pairs_no_anchor_can_see": sum(
                j["impassable_pairs"] for j in junctions
                if j["impassable_pairs"]
                and not j["impassable_pairs_at_anchor"]),
            "flush_within_1cm": sum(1 for r in rows if r["spread_m"] <= 0.01),
            "over_budget_pairs": len(over_budget),
            "impassable_pairs": sum(1 for r in rows if r["impassable"]),
            "worst_spread_m": rows[0]["spread_m"] if rows else 0.0,
            "road_vs_pad_pairs": sum(1 for r in rows
                                     if r["b_kind"] == "pad"),
            "road_authoring_inside_a_pad": sum(
                1 for r in rows if r.get("pad_inside")),
        },
        "verdict": "REFUSED" if refusals else "AGREED",
        "refusals": refusals,
        "waived": waived,
        "over_budget": over_budget[:60],
        "junctions": junctions,
        # EVERY PAIR, not only the refused ones.  A gate that only publishes
        # its failures cannot be shown to PASS a good junction, and that is
        # exactly the direction this project keeps getting bitten in: the
        # known-good N0/N0b and N0b/R3 readings are here, at spread 0.0, and
        # the self-test asserts on them.
        "pairs": rows,
        # A SEPARATE, ADJACENT FINDING with its own instrument and its own
        # population, named so it can never be quoted as a junction number.
        "profile_grade_audit": grade_audit(ribbons),
    }

# ---------------------------------------------------------------------------
# THE GATE, ASKED BY THE THING THAT WRITES.
# ---------------------------------------------------------------------------
# A GATE NOBODY RUNS IS A DOCUMENT.  Everything above this line was already
# true when the temple's 4.206 m spread went into the ground: the reconciler
# refuses it, the self-test asserts the refusal, and `ribbon.py build` never
# asked.  MEASURED: `grep -n junction roads/ribbon.py roads/network.py
# accept/sections/roads.py` returns only this world's *place names* --
# `t8junction`, `n0bjunction` -- and `ribbon.py`'s own per-segment census,
# which correctly DISCLAIMS a junction step as another claim's to grade.  So
# the plan-time check existed and the write path could not see it.
#
# These two functions are the wiring.  They live here, next to the tolerance
# they enforce, so the write path cannot end up asking a restated version of
# the question -- the same reason `_pad_footprint` is imported rather than
# re-derived.

def population_files(primary: Path) -> list[Path]:
    """Every plan that can contribute a participant, DERIVED from the one the
    writer was handed rather than listed.

    A junction is a fact about two participants and the population decides
    whether it is seen at all.  Ulfsland's own networks are split across
    `roads/segments.yaml` and `roads/north_segments.yaml`, and they MEET:
    `W4-wmid-wwest` (segments.yaml) and `N0-brgs2-wmid` (north_segments.yaml)
    share ground at (-272.18, 1281.82) with a 4.522 m spread.  A gate handed
    only the file being written from would pass that pair by never looking at
    it, which is the failure mode this project keeps paying for.

    So the population is every `*segments*.yaml` beside the file the writer
    named -- AND ONLY THOSE THAT DECLARE THE SAME WORLD.  The directory is a
    SUPERSET of this world's plans: every seed's road plan is emitted into
    `roads/` (worldseed.road_plan_names carries the seed in the FILE NAME, not in
    a subdirectory), so an unscoped glob hands a fresh seed's junction gate
    the roads of every other world on the host.  MEASURED 2026-09-23 building
    Fresh42: the population was "45 segments from ['north_segments.yaml',
    'segments.Fresh42.yaml', 'segments.yaml']" -- 31 of those 45 are
    ULFSLAND'S -- and both files contain a segment called
    `T1-portalhub-temple`, so the id the writer looked itself up by named two
    different centrelines in two different worlds.

    The scope comes from the plans' own `world` key, which every emitted plan
    carries (roads/spec.py writes it and pipeline.py declares these documents
    SCOPE_YAML).  A file that declares no world is NOT adopted: an
    unattributable participant is exactly the reading this gate must not make
    silently.  It is derived, it grows when a network is added to the same
    world, and it fails in the safe direction.
    """
    import yaml as _yaml                                    # noqa: PLC0415
    primary = Path(primary).resolve()

    def declared_world(p: Path) -> str | None:
        try:
            d = _yaml.safe_load(p.read_text())
        except Exception:                                   # noqa: BLE001
            return None
        return (d or {}).get("world") if isinstance(d, dict) else None

    mine = declared_world(primary)
    out = {primary}
    for p in primary.parent.glob("*segments*.yaml"):
        q = p.resolve()
        if q == primary:
            continue
        # Both sides must NAME a world and name the SAME one.  When the
        # primary itself declares none there is nothing to match against, so
        # nothing is adopted -- a population assembled on no evidence is the
        # superset this docstring is about.
        if mine and declared_world(q) == mine:
            out.add(q)
    return sorted(out)


def build_gate(segment_id: str, seg_paths: list[Path], ledger: Path | None,
               accepted: dict | None = None) -> dict:
    """Would writing `segment_id` put an impassable step at a junction?

    THE POPULATION IS THIS SEGMENT'S OWN PAIRS, and that is the whole design.
    `gate()` judges the network; a writer can only be held to what it writes.
    Refusing `T3-temple-meadhall` because `W4` and `N0` disagree 1.3 km away
    would make every repair on this world impossible and would teach the
    operator to pass the waiver by reflex, which is worse than no gate.
    A COUNTERPARTY THAT HAS WRITTEN NO GROUND IS NOT A JUNCTION.  The other
    half of "a writer can only be held to what it writes" is that it can only
    be held against what somebody else has ALSO written: `segments.yaml` and
    `north_segments.yaml` together hold 45 planned runs and the ledger holds
    25 built ones, so a plan-only neighbour contributes a profile over ground
    that has never been touched.  Two profiles over unwritten ground cannot
    put a step under a player, because neither of them is the surface.

    MEASURED on Ulfsland 2026-09-21, and it is not a rounding case: of 1,509
    impassable pairs on the whole network, 180 are between two BUILT
    segments; 682 have one unbuilt participant and 647 have two.  For
    `N0-brgs2-wmid` specifically, ALL 277 of its impassable pairs are against
    W2, W3, W4, W8 and W11 -- five roads that were planned and never built --
    so the write gate refused a real repair on behalf of five roads that do
    not exist, and the only way past it was a waiver naming each of 277
    pairs.  A gate that can only be satisfied by a waiver is a gate that
    teaches the operator to waive.

    They are REPORTED, never dropped: `plan_only` carries every one of them
    with its spread, because the day W4 is built that disagreement is real
    and `gate()` -- which judges the NETWORK, not a write -- already sees it.
    PADS ARE NOT PLANS: `load()` takes them from the ledger, so a pad
    counterparty is written ground and still refuses.
    """
    ribbons, pads = load([Path(p) for p in seg_paths], ledger)
    rep = gate(ribbons, pads, accepted or {})
    if segment_id not in {r["id"] for r in ribbons}:
        raise ValueError(
            f"{segment_id} contributes no ground run to "
            f"{[str(p) for p in seg_paths]} -- a fully bridged segment has no "
            f"ground to disagree about, but a MISSPELLED one has none either, "
            f"and passing the second silently is how a gate stops gating")
    planned = {r["id"] for r in ribbons}
    written = set(ledger_write_order(ledger)) if ledger else planned
    unbuilt = planned - written

    def _mine(rows):
        return [r for r in rows
                if segment_id in (r["a"].split("#")[0], r["b"].split("#")[0])]

    def _other(r):
        a, b = r["a"].split("#")[0], r["b"].split("#")[0]
        return b if a == segment_id else a

    all_mine = _mine(rep["refusals"])
    plan_only = [r for r in all_mine if _other(r) in unbuilt]
    mine = [r for r in all_mine if _other(r) not in unbuilt]
    waived = _mine(rep["waived"])
    worst = max(mine, key=lambda r: r["spread_m"]) if mine else None
    return {
        "segment": segment_id,
        "verdict": "REFUSED" if mine else "AGREED",
        "population": rep["population"],
        "sources": [str(p) for p in seg_paths],
        "tolerance": rep["tolerance"],
        "my_pairs": len(_mine(rep["pairs"])),
        "my_refusals": len(mine),
        "my_waived": len(waived),
        "worst": worst,
        "refusals": sorted(mine, key=lambda r: -r["spread_m"]),
        "waived": waived,
        # NOT a refusal and NOT a silence: the disagreements this write has
        # with roads nobody has built, named with their numbers.
        "my_refusals_against_unbuilt_plans": len(plan_only),
        "plan_only": sorted(plan_only, key=lambda r: -r["spread_m"])[:12],
        "plan_only_counterparties": sorted({_other(r) for r in plan_only}),
        "segments_planned": len(planned),
        "segments_written": len(planned & written),
        "network_verdict": rep["verdict"],
        "network_impassable_pairs": rep["survey"]["impassable_pairs"],
    }


def accept_key(row: dict) -> str:
    """The waiver key for a refusal row, in the shape `gate()` reads.

    Published rather than left to the caller: the key is `a|b` with the RUN
    suffix kept (`T4-meadhall-stathub#0`), because two runs of one segment
    split by a bridge are different participants with different profiles and
    a waiver written against the bare id would cover a conflict it was never
    shown.
    """
    return f"{row['a']}|{row['b']}"


# ---------------------------------------------------------------------------
# RECONCILIATION -- the arithmetic a proposal has to be costed with.
# ---------------------------------------------------------------------------

def profile_grades(xz: list, y: list, base_m: float = LATTICE_M) -> list[float]:
    """Walked gradient along a profile, over a MINIMUM `base_m` run.

    NOT NODE TO NODE.  The stored centrelines are denser than the profile
    fit's stations -- MEASURED on the recorded plans, edges get as short as
    0.07 m at bends -- and dividing a station's height change by a 0.07 m
    edge reports a 0.5 gradient for a step the 1 m Heightmap lattice cannot
    even express.  The baseline is one lattice edge, the same run the
    tolerance is derived over, so this function and `slide_allow` measure
    the same thing.
    """
    n = len(y)
    arc = [0.0]
    for k in range(1, n):
        arc.append(arc[-1] + math.hypot(xz[k][0] - xz[k - 1][0],
                                        xz[k][1] - xz[k - 1][1]))
    out = []
    j = 0
    for i in range(n):
        j = max(j, i + 1)
        while j < n and arc[j] - arc[i] < base_m:
            j += 1
        if j >= n:
            break
        out.append(abs(y[j] - y[i]) / (arc[j] - arc[i]))
    return out


def grade_audit(ribbons: list[dict]) -> dict:
    """Declared grade limit versus the gradient the profile actually walks.

    A SEPARATE FINDING, REPORTED BECAUSE IT CHANGES WHAT A RECONCILIATION
    CAN COST.  `network.py`:714 computes `grade_max_measured` as
    `|diff(y)| / diff(s)` where `s` is the PROFILE FIT's station arc -- but
    the `nodes` written into `segments.yaml` are denser than those stations
    at every bend, so the same height change is divided by a shorter run and
    the walked gradient is larger than the declared one.  MEASURED over the
    recorded plans at a 1 m baseline: 44 of 45 segments walk steeper than
    their own `grade_limit`, T12 declaring 0.08 and walking 0.158.

    It is reported rather than refused here because it is not a junction
    defect and not this gate's population -- but it is why a junction taper
    is judged against `max(grade_limit, walked)` instead of `grade_limit`:
    requiring a reconciliation to reach a limit the road never met would
    make every proposal read "impossible" for a reason that has nothing to
    do with the junction.
    """
    rows = []
    for r in ribbons:
        g = profile_grades(r["xz"], r["y"])
        walked = max(g, default=0.0)
        rows.append({"segment": r["label"],
                     "grade_limit": r["grade_limit"],
                     "declared_grade_max": r.get("grade_max_measured"),
                     "walked_grade_max_1m_baseline": round(walked, 4),
                     "over_declared_limit": walked > r["grade_limit"] + 1e-4})
    rows.sort(key=lambda q: -(q["walked_grade_max_1m_baseline"]
                              - q["grade_limit"]))
    return {
        "instrument": "max |dy| / ds over a >=1 m run of the STORED "
                      "centreline, which is what a player walks; the plans' "
                      "own grade_max_measured is taken against the profile "
                      "fit's station arc (network.py:714), a different and "
                      "coarser run",
        "population": "PLAN versus PLAN -- a segment's declared grade_limit "
                      "against the gradient its own stored centreline "
                      "walks. NOT the built surface, and the two must not "
                      "be quoted for each other: MEASURED FROM SAVE by "
                      "WorldAcceptGate over the same world, 15 of 25 BUILT "
                      "segments have any adjacent-1 m pair over tan(38), "
                      "79 pairs in 71 distinct locations. Different "
                      "population, different instrument, smaller number.",
        "segments_over_their_own_limit": sum(
            1 for q in rows if q["over_declared_limit"]),
        "segments": len(rows),
        "worst": rows[:12],
    }


def taper(rib: dict, station: int, delta: float, grade_limit: float,
          max_len_m: float | None = None,
          relax_ceiling: float | None = None) -> dict:
    """Move one station of a profile by `delta` and absorb it in a taper.

    Returns the SHORTEST taper (in whole stations) that keeps the profile's
    worst grade at or under `relax_ceiling` (default: `grade_limit`), plus
    the whole LENGTH-versus-RELAXATION frontier, because on this world the
    answer at the declared limit is usually "impossible" and that is not a
    useful thing for a proposal to say on its own.

    WHY THE FRONTIER IS THE DELIVERABLE AND NOT A SINGLE VERDICT.  MEASURED
    on the recorded plans: every trunk at the temple is fitted FLUSH to its
    own ceiling -- `grade_max_measured` 0.08 against `grade_limit` 0.08, and
    `grade_p95` 0.08 too -- so the profile has NO slack anywhere.  Adding any
    taper in the same direction as the existing grade exceeds 0.08 at some
    station for EVERY taper length, and the search correctly returns
    infeasible for all 418 of them.  The real decision is a trade: a short
    taper is a big local relaxation over few stations, a long taper is a
    negligible relaxation over the whole segment and a much larger re-emit.
    Reporting only "infeasible" hides a decision the operator can actually
    make; reporting the frontier prices it.

    THE TAPER STOPS AT A FIXED STATION.  `rib["fixed"]` is the plan's own
    declaration -- an abutment's level run, which the Crossings contract
    holds flat at `level_y`. A taper that ran through it would be buying its
    grade budget with somebody else's datum, and the resulting proposal
    would read as feasible when it is not.
    """
    xz, y0 = rib["xz"], list(rib["y"])
    n = len(y0)
    arc = rib["arc"]
    fixedf = rib.get("fixed") or [False] * n
    # How far the taper may reach in each direction before it would move a
    # station the plan fixes.
    lo_cap = hi_cap = float("inf")
    for k in range(n):
        if not fixedf[k]:
            continue
        d = arc[k] - arc[station]
        if d < 0:
            lo_cap = min(lo_cap, -d)
        elif d > 0:
            hi_cap = min(hi_cap, d)
        else:
            return {"feasible": False, "grade_limit": grade_limit,
                    "why": f"station {station} is itself FIXED "
                           f"({rib.get('why_fixed', [None] * n)[station]}); "
                           f"it cannot be moved {round(delta, 3)} m"}
    # THE NEAREST FIXED STATION, NOT THE FARTHEST.  `at(L)` moves every
    # non-fixed station within L of the taper's own station IN BOTH
    # DIRECTIONS, so a fixed station at distance d bounds L by d -- and
    # `max(lo_cap, hi_cap)` bounded it by the far one instead.  With a fix at
    # ONE end only the two agree (the other side is `inf`, which the old
    # spelling turned into 0), so nothing that ran before changes; with a fix
    # at BOTH it did not.  MEASURED: holding T1's temple terminal 100 m ahead
    # of a taper at (-58.66, -58.02) left the cap at 655.4 m, the station
    # beside the held one moved 3.56 m while the held one did not, and every
    # L in the ladder reported grade 0.1885 -- "no taper up to 655.4 m",
    # which reads as "this road cannot be regraded" when what it means is
    # "the taper was allowed to run past the thing it was told not to move".
    reach_cap = min(lo_cap, hi_cap)
    total = arc[-1]
    cap = total if max_len_m is None else min(total, max_len_m)
    cap = min(cap, reach_cap)
    before = max(profile_grades(xz, y0), default=0.0)
    # THE BASELINE A RECONCILIATION IS JUDGED AGAINST IS "NO WORSE".
    # `grade_audit` measures that 44 of 45 recorded segments already walk
    # steeper than their own declared limit, so requiring a junction taper
    # to land under `grade_limit` would refuse every proposal for a reason
    # the junction did not cause.  The test is therefore: do not make this
    # road steeper than it already is, and do not exceed its declared limit
    # where it currently honours it.  `grade_before` travels with every
    # move so the pre-existing figure is never laundered into the
    # reconciliation's cost.
    baseline = max(grade_limit, before)
    ceiling = baseline if relax_ceiling is None else relax_ceiling

    def at(L: float):
        y = list(y0)
        for k in range(n):
            if fixedf[k]:
                continue
            d = abs(arc[k] - arc[station])
            if d >= L:
                continue
            y[k] = y0[k] + delta * (1.0 - d / L)
        return y, max(profile_grades(xz, y), default=0.0)

    curve: list[tuple[float, float, int]] = []
    hit = None
    best = None
    L = 2.0
    while L <= cap + 1e-9:
        y, g = at(L)
        moved = sum(1 for k in range(n) if abs(y[k] - y0[k]) > 1e-6)
        curve.append((L, g, moved))
        if best is None or g < best[1]:
            best = (L, g, moved)
        if hit is None and g <= ceiling + 1e-9:
            hit = (L, g, moved, y)
        L += 2.0

    # THE FRONTIER, as the shortest taper reaching each relaxation factor.
    frontier = []
    for fac in (1.0, 1.1, 1.25, 1.5, 2.0):
        tgt = baseline * fac
        got = next(((cl, cg, cm) for cl, cg, cm in curve
                    if cg <= tgt + 1e-9), None)
        if got:
            y2, _g2 = at(got[0])
            frontier.append({
                "relax_factor": fac,
                "grade_ceiling": round(tgt, 4),
                "taper_m": round(got[0], 1),
                "max_grade_after": round(got[1], 4),
                "taper_own_grade": round(abs(delta) / got[0], 4),
                "stations_moved": got[2],
                "zones_to_reemit": len(zones_touched(
                    rib, {k for k in range(n)
                          if abs(y2[k] - y0[k]) > 1e-6})),
            })
    # `max_grade_after_exact` IS NOT A DUPLICATE FIELD.  The rounded one is
    # for a human; the exact one is for the RETRY, because `reconcile` feeds
    # this ceiling straight back into `taper` and 4-dp rounding rejects the
    # very taper that produced it.  MEASURED here on T1: the least relaxation
    # at (-58.66, -58.02) is 0.19434, the reported ceiling was 0.1943, the
    # retry found no L at all, and the move came back INFEASIBLE with
    # "no taper up to 53.5 m" -- a road that can be regraded reported as one
    # that cannot.  The same rounding trap is already named, in these words,
    # in `reconcile`'s `_profile` comment; it was live one function away.
    minrelax = ({"taper_m": round(best[0], 1),
                 "max_grade_after": round(best[1], 4),
                 "max_grade_after_exact": float(best[1]),
                 "stations_moved": best[2],
                 "relax_factor_needed": (round(best[1] / baseline, 3)
                                         if baseline > 0 else None)}
                if best else None)

    out = {"max_grade_before": round(before, 4),
           "grade_limit": grade_limit,
           "judged_against": round(baseline, 4),
           "judged_against_is": ("max(grade_limit, the gradient this "
                                 "profile already walks) -- see grade_audit"),
           "taper_cap_m": round(cap, 1),
           "relax_frontier": frontier,
           "least_relaxation": minrelax,
           "arc_needed_on_flat_ground_m": (round(abs(delta) / grade_limit, 2)
                                           if grade_limit > 0 else None)}
    if hit:
        L, g, moved, y = hit
        out.update({"feasible": True, "profile": y, "taper_m": round(L, 1),
                    "max_grade_after": round(g, 4),
                    "stations_moved": moved,
                    "relax_factor_used": (round(g / baseline, 3)
                                          if baseline > 0 else None),
                    "taper_own_grade": round(abs(delta) / L, 4),
                    "max_abs_shift_m": round(max(abs(y[k] - y0[k])
                                                 for k in range(n)), 3)})
        return out
    out.update({
        "feasible": False,
        "why": (f"no taper up to {round(cap, 1)} m holds "
                f"{round(delta, 3)} m of shift inside grade "
                f"{round(ceiling, 4)}; this profile is already at "
                f"{round(before, 4)} against a limit of {grade_limit}, so it "
                f"has no slack and the least relaxation any taper can "
                f"achieve is "
                f"{minrelax['max_grade_after'] if minrelax else None} "
                f"({minrelax['relax_factor_needed'] if minrelax else None}x) "
                f"over {minrelax['taper_m'] if minrelax else None} m")})
    return out


def pin_to_pad(rib: dict, pad: dict, grade_limit: float) -> dict:
    """Hold a road at a pad's datum over the pad's reach, then re-grade out.

    THIS IS THE RULING, EXPRESSED AS ARITHMETIC.  Inside a pad's rectangle
    the pad wins (`writer._pad_footprint_check`, `ribbon.stamp`'s `pad`
    refusal) and the road "MUST grade into the pad's EDGE".  So the road's
    stations within the pad's reach take the datum, and the question is
    whether the arc from the pad's edge to the next station the road may NOT
    move is long enough to carry the remaining height at the road's own
    grade limit.

    Returns the arc budget explicitly -- needed, available, shortfall -- so
    a refusal is a number a designer can act on (lengthen the approach by
    N m, or move the pad by N m, or raise the far datum) rather than a
    verdict.
    """
    n = len(rib["y"])
    arc, y0 = rib["arc"], list(rib["y"])
    fixedf = rib.get("fixed") or [False] * n
    datum = pad["datum_y"]
    # OWNERSHIP, NOT DETECTION.  A station is the pad's only where the
    # road's CARRIAGEWAY actually overlaps the rectangle: `half_m`, with no
    # NEAR_MISS_M in it.  `NEAR_MISS_M` exists so the gate can SEE a road
    # that stops a metre short of a pad it should have met; using it here
    # instead pins ground the pad does not own -- MEASURED on S1, it pinned
    # station 5 at (-301.41, 215.41), 2.71 m clear of the rectangle, and
    # reported a 2.500 m shift where the plans say 2.000 m.
    reach = rib["half_m"]
    pinned = [k for k in range(n) if _pad_gap(pad, *rib["xz"][k]) <= reach]
    if not pinned:
        return {"feasible": True, "pinned": 0,
                "why": "this road does not reach the pad"}
    clash = [k for k in pinned if fixedf[k]]
    lo, hi = min(pinned), max(pinned)
    out = {"pad": pad["label"], "datum_y": datum,
           "pinned_stations": len(pinned),
           "pinned_span": [lo, hi],
           "pinned_at": [list(rib["xz"][lo]), list(rib["xz"][hi])],
           "worst_pinned_shift_m": round(max(abs(datum - y0[k])
                                             for k in pinned), 3),
           "legs": []}
    if clash:
        out.update({"feasible": False,
                    "why": f"stations {clash} are inside the pad's reach AND "
                           f"fixed by the plan "
                           f"({rib.get('why_fixed')[clash[0]]}); the pad "
                           f"datum {datum} and that declaration cannot both "
                           f"hold"})
        return out
    feasible = True
    for side, k_edge, k_end, step in (("before", lo, 0, -1),
                                      ("after", hi, n - 1, +1)):
        if (step < 0 and lo == 0) or (step > 0 and hi == n - 1):
            continue
        # The first station on this side the road may not move: a fixed one,
        # else the run's own far endpoint.
        k = k_edge + step
        stop = k_end
        while 0 <= k <= n - 1:
            if fixedf[k]:
                stop = k
                break
            k += step
        avail = abs(arc[stop] - arc[k_edge])
        need_h = abs(datum - y0[stop])
        need_arc = need_h / grade_limit if grade_limit > 0 else float("inf")
        leg = {"side": side, "from_station": k_edge,
               "to_station": stop,
               "to_is_fixed": bool(fixedf[stop]),
               "to_why_fixed": rib.get("why_fixed", [None] * n)[stop],
               "to_y": round(y0[stop], 3),
               "height_to_carry_m": round(need_h, 3),
               "arc_available_m": round(avail, 2),
               "arc_needed_at_limit_m": round(need_arc, 2),
               "grade_required": (round(need_h / avail, 4) if avail > 1e-9
                                  else None),
               "grade_limit": grade_limit,
               "arc_shortfall_m": round(max(0.0, need_arc - avail), 2)}
        leg["feasible"] = leg["arc_shortfall_m"] <= 1e-9
        feasible = feasible and leg["feasible"]
        out["legs"].append(leg)
    out["feasible"] = feasible
    return out


def pinned_profile(rib: dict, pad: dict) -> list[float] | None:
    """The profile that HOLDS a pad's datum over the pad's own ground and
    re-grades linearly out to the next station the plan fixes.

    This is the repair `pin_to_pad` costs, made applicable, so the gate can
    be run against it.  A proposal that cannot be applied and re-measured is
    an assertion; this makes the hall apron testable in the PASS direction
    as well as the refuse direction.

    Returns None if the road does not reach the pad.
    """
    n = len(rib["y"])
    arc, y0 = rib["arc"], list(rib["y"])
    fixedf = rib.get("fixed") or [False] * n
    datum = pad["datum_y"]
    pinned = [k for k in range(n)
              if _pad_gap(pad, *rib["xz"][k]) <= rib["half_m"]]
    if not pinned:
        return None
    y = list(y0)
    for k in pinned:
        y[k] = datum
    lo, hi = min(pinned), max(pinned)
    for k_edge, step, end in ((lo, -1, 0), (hi, +1, n - 1)):
        if (step < 0 and lo == 0) or (step > 0 and hi == n - 1):
            continue
        k = k_edge + step
        stop = end
        while 0 <= k <= n - 1:
            if fixedf[k]:
                stop = k
                break
            k += step
        span = arc[stop] - arc[k_edge]
        if abs(span) <= 1e-9:
            continue
        for k in range(min(k_edge, stop) + 1, max(k_edge, stop)):
            f = (arc[k] - arc[k_edge]) / span
            y[k] = datum + (y0[stop] - datum) * f
    return y


def rejoin_profile(rib: dict, pad: dict,
                   grade_limit: float | None = None) -> list[float] | None:
    """THE PIN REPAIR THAT IS ACTUALLY APPLICABLE: hold the pad's datum over
    the pad's own ground, then climb or fall at the road's own grade limit
    until the profile MEETS ITS OWN ORIGINAL LINE again, and leave the rest
    of the road alone.

    WHY THIS EXISTS AND `pinned_profile` IS NOT ENOUGH.  `pinned_profile`
    ramps LINEARLY from the pad's edge to the next station the plan fixes.
    Where that station is an abutment 18 m away (S1) the two are the same
    thing.  Where it is the segment's own far endpoint the ramp is the whole
    road, and MEASURED on the recorded T1-portalhub-temple that is not a
    repair, it is a different road: 303 of 387 stations moved, worst shift
    2.357 m at station 223 -- 566 m from the pad, on a profile that was
    fitted to the hillside inside a +/-8 m clamp.  `pin_to_pad`'s own leg
    arithmetic prices exactly that ramp ("carry 31.094 m over 673.78 m ->
    grade 0.0461, feasible") and so reports FEASIBLE for a proposal nobody
    could ship.  This function is what the cost SHOULD be attached to:
    MEASURED on the same road, 52 stations moved, worst shift 1.658 m, and
    the profile's worst gradient goes DOWN, 0.15566 -> 0.15416, because the
    1.494 m authority seam at the pad's edge was itself the steepest metre.

    A rejoin is only possible where the road may move: if a FIXED station
    (an abutment's level run) is reached before the original line is met,
    the answer is `pinned_profile`'s ramp to that station and the shortfall
    `pin_to_pad` already prices.  Returns None if the road does not reach
    the pad.
    """
    n = len(rib["y"])
    arc, y0 = rib["arc"], list(rib["y"])
    fixedf = rib.get("fixed") or [False] * n
    datum = pad["datum_y"]
    limit = rib["grade_limit"] if grade_limit is None else grade_limit
    pinned = [k for k in range(n)
              if _pad_gap(pad, *rib["xz"][k]) <= rib["half_m"]]
    if not pinned:
        return None
    y = list(y0)
    for k in pinned:
        y[k] = datum
    lo, hi = min(pinned), max(pinned)
    for k_edge, step, end in ((lo, -1, 0), (hi, +1, n - 1)):
        if (step < 0 and lo == 0) or (step > 0 and hi == n - 1):
            continue
        k = k_edge + step
        while 0 <= k <= n - 1:
            ds = abs(arc[k] - arc[k - step])
            room = limit * ds
            prev = y[k - step]
            # Close the gap to the road's OWN line as fast as the limit
            # allows, in whichever direction the gap lies.
            if y0[k] >= prev:
                cand = min(y0[k], prev + room)
            else:
                cand = max(y0[k], prev - room)
            if fixedf[k]:
                # The plan fixes this station: the rejoin had no room and
                # the ramp to it is the only shape available.  Hand back to
                # the ramp for this leg so the two agree.
                span = arc[k] - arc[k_edge]
                if abs(span) > 1e-9:
                    for j in range(min(k_edge, k) + 1, max(k_edge, k)):
                        f = (arc[j] - arc[k_edge]) / span
                        y[j] = datum + (y0[k] - datum) * f
                break
            y[k] = cand
            if abs(cand - y0[k]) <= 1e-9:
                break                             # met its own line
            k += step
    return y


# ---------------------------------------------------------------------------
# A RE-EMIT IS NOT FREE.  WHO LOSES GROUND TO IT.
# ---------------------------------------------------------------------------
# `ribbon.py` rasterises a WHOLE segment and its zone union resolves every
# sample to ONE author -- this write's own samples win, priors survive only
# where this write authored nothing (ribbon.py:3413-3450, 3636-3640).  So
# re-emitting a segment to repair one end TAKES BACK every sample a later
# neighbour had overwritten at the other end, and the disagreement does not
# go away: it changes owner.
#
# MEASURED on this world, and it is the reason this function exists rather
# than a paragraph of advice.  Re-emitting T1-portalhub-temple alone to fix
# the 1.497 m step at its portal-hall apron reclaims its last 32 stations
# from T5-temple-dockshore and T13-temple-harboursouth, whose beds sit
# 2.70-4.25 m BELOW T1's design there: T1's own over-limit steps fall 6 -> 0
# and FIVE new ones appear on T5 and T13 (3.492, 3.237, 2.881 m on T5;
# 4.169 m twice on T13), for a network total of 27 -> 31.  Emitting
# T1, then T13, then T5 -- every loser re-emitted after its winner --
# scores 27 -> 23 with no transfer at all.  The order IS the repair.
#
# 46 of 434 road zones on this world are claimed by two or more segments, so
# this is the normal case and not a corner.

def ledger_write_order(ledger: Path) -> dict[str, int]:
    """Who authored last, BEFORE this emit: the file position of each road
    segment's most recent `terrain_write`.

    This is the prior half of the ownership question and it cannot be
    guessed from the plans: `segments.yaml` has no order in it.  The union
    resolves a disputed sample to the LAST author, so the only way to say
    whether an emit CHANGES an owner is to know who the owner is now.
    """
    order: dict[str, int] = {}
    if ledger is None or not Path(ledger).exists():
        return order
    for i, line in enumerate(Path(ledger).read_text().splitlines()):
        if '"road_segment"' not in line:
            continue
        try:
            d = json.loads(line)
        except ValueError:                         # pragma: no cover
            continue
        p = d.get("params") or {}
        if p.get("role") != "road_segment" or d.get("op") != "terrain_write":
            continue
        order[p["name"][len("road_"):].replace("_", "-")] = i
    return order


def reemit_transfer(ribbons: list[dict], pads: list[dict],
                    order: list[str], prior: dict[str, int]) -> dict:
    """Which disputed samples CHANGE OWNER because of this emit order.

    `prior` is `ledger_write_order` -- who owns each disputed sample now.
    `order` is the emit sequence, later wins.  A pair that disagrees by more
    than the slide allowance is a DISPUTED sample; this emit is charged with
    it only if the emit moves it from one author to the other, because a
    disagreement that stays where it was is somebody else's defect and
    pricing it here would hide the one this operation causes.

    An order is CLEAN when it takes no disputed sample away from the segment
    that holds it -- which in practice means: re-emit a segment's
    overlapping neighbours AFTER it.
    """
    rows = find_conflicts(ribbons, pads)["pairs"]
    base = max(prior.values(), default=0) + 1
    rank: dict[str, int] = {}
    for r in ribbons:
        lab = r["label"]
        seg = lab.split("#")[0]
        rank[lab] = prior.get(seg, -1)
    for i, lab in enumerate(order):
        for r in ribbons:
            if r["label"] == lab or r["label"].split("#")[0] == lab:
                rank[r["label"]] = base + i
    transfers = []
    for r in rows:
        if not r["impassable"]:
            continue
        a, b = r["a"], r["b"]
        if a not in rank or b not in rank:
            continue                               # a pad does not move
        if a.split("#")[0] == b.split("#")[0]:
            continue                               # one segment, two runs
        before = a if prior.get(a.split("#")[0], -1) > \
            prior.get(b.split("#")[0], -1) else b
        after = a if rank[a] > rank[b] else b
        if before == after:
            continue                               # owner unchanged
        transfers.append({
            "was_owned_by": before, "now_owned_by": after,
            "loser": before, "xz": r["xz"], "spread_m": r["spread_m"],
            "gap_m": r["gap_m"], "slide_allow_m": r["slide_allow_m"]})
    losers: dict[str, list] = {}
    for t in transfers:
        losers.setdefault(t["loser"], []).append(t)
    return {
        "order": list(order),
        "rule": "the union resolves a disputed sample to its LAST author; "
                "an emit is CLEAN when it takes no disputed sample away "
                "from the segment that holds it, which means re-emitting a "
                "segment's overlapping neighbours AFTER it",
        "samples_changing_owner": len(transfers),
        "losers": {
            k: {"samples": len(v),
                "worst_spread_m": max(t["spread_m"] for t in v),
                "worst_at": max(v, key=lambda t: t["spread_m"])["xz"],
                "taken_by": sorted({t["now_owned_by"] for t in v})}
            for k, v in sorted(losers.items())},
        "clean": not transfers,
        "transfers": sorted(transfers, key=lambda t: -t["spread_m"])[:40],
    }


def clean_order(ribbons: list[dict], pads: list[dict],
                want: list[str], prior: dict[str, int]) -> dict:
    """The emit sequence that repairs `want` and changes no owner.

    THE RULE, derived rather than chosen.  A disputed sample belongs to the
    segment that wrote last.  Re-emitting segment A therefore takes every
    disputed sample A shares with a segment that currently outranks it --
    unless that segment is re-emitted again afterwards.  So the closure of
    `want` under "shares a disputed sample with, and currently outranks" is
    exactly the set that has to be re-emitted, and emitting the closure in
    the ledger's OWN prior order reproduces every existing ownership
    decision while letting the repaired profiles through.

    MEASURED on Ulfsland: the closure of the S1 apron repair is S1 ALONE,
    because pinning S1 to the hall's datum removes the only dispute S1 had
    (with T1, at the apron).  The closure of the T1 apron repair is ELEVEN
    segments -- T1, T3, T5, T12, T13, T4, S7, T8, T10, S8 and their own
    disputes -- because T1's last 32 stations lie on ground T5 and T13 own
    2.70-4.25 m below T1's design.  Those two numbers are the difference
    between a one-write repair and an eleven-write operation, and nothing
    in `segments.yaml` shows either of them: the order lives in the ledger.

    Pass the PROPOSED profiles in `ribbons`, not the recorded ones.  A
    repair that settles a dispute removes it from the closure, which is the
    whole reason S1's closure is empty.
    """
    rows = find_conflicts(ribbons, pads)["pairs"]
    shares: dict[str, set[str]] = {}
    for r in rows:
        if not r["impassable"]:
            continue
        a, b = r["a"].split("#")[0], r["b"].split("#")[0]
        if a == b or a not in prior or b not in prior:
            continue
        shares.setdefault(a, set()).add(b)
        shares.setdefault(b, set()).add(a)
    out = {w for w in want if w in prior}
    missing = [w for w in want if w not in prior]
    stack = list(out)
    while stack:
        cur = stack.pop()
        for nb in shares.get(cur, ()):
            if nb in out or prior[nb] <= prior[cur]:
                continue
            out.add(nb)
            stack.append(nb)
    order = sorted(out, key=lambda s: prior[s])
    got = reemit_transfer(ribbons, pads, order, prior)
    return {"want": list(want), "order": order,
            "segments_to_reemit": len(order),
            "pulled_in_by_closure": [s for s in order if s not in want],
            "not_in_ledger": missing,
            "clean": got["clean"],
            "samples_changing_owner": got["samples_changing_owner"],
            "losers": got["losers"]}


# ---------------------------------------------------------------------------
# THE SECOND QUESTION `clean_order` NEVER ASKED: WHO OWNS THE RIM
# ---------------------------------------------------------------------------
# MEASURED on Ulfsland 2026-09-22, by the write that found it.  `LandT3T4`
# emitted the closure of T3-temple-meadhall to close a 2.113 m step at
# (32,-10), a RESTITUTION: T3 seq 5091 authored that sample at +4.5832 and
# T12 seq 5101 re-authored it at +2.4702 fourteen minutes later, so the union
# gave it to T12 and left a 2.113 m cliff in T3's carriageway.  `clean_order`
# returned `clean: true, samples_changing_owner: 0` -- HONEST AND COMPLETE ON
# ITS OWN TERMS -- the write landed, and the gate went from 11/28 destinations
# walkable to 13/28: it OPENED meadhall, stenvik, t8junction, ulfsgard and
# wttown, and it SEVERED treesth, wtsouth and wtspawn.
#
# THE NEW LINK WAS (28,-12) -> (28,-11), T12 against T12, step 2.113 m, bed
# 67.040 -> 69.153.  THE IDENTICAL METRE AND THE IDENTICAL TWO BED HEIGHTS AS
# THE STEP THAT WAS CLOSED.  The cliff did not go away.  IT MOVED FOUR METRES
# ALONG T12's OWN CENTRELINE, to the first sample OUTSIDE the set T3 took
# back -- still authored by seq 181, RoadNet, six days earlier, which seq 5101
# never re-authored there.
#
# WHY THE CLOSURE CANNOT SEE IT, AND IT IS STRUCTURAL RATHER THAN A MISSED
# CASE.  `clean_order` closes over DISPUTED samples: pairs whose two PLANS
# disagree by more than the slide allowance.  T3 and T12 AGREE at (28,-12) --
# T12's own plan node 62 at (28.0,-12.79) reads 69.153, byte-identical to what
# T3 wrote.  No disagreement, no dispute, no closure membership, and the write
# still severed three destinations.  `CloseFive` found the first face of this
# hole hours earlier: for a restitution the closure's `clean` is INVERTED,
# because a write that repaired NOTHING would also have satisfied it.  This is
# the same hole with the opposite sign -- it equally accepts a write that
# repairs something AND BREAKS SOMETHING ELSE.
#
# THE QUESTION THAT CATCHES BOTH: WHICH SAMPLES DOES THIS WRITE TAKE, AND WHO
# OWNS THE GROUND AT THE RIM OF THAT SET?  A taken sample's new height has to
# meet its neighbours, and the neighbours it was never compared against are
# exactly the ones no plan disagreed with.
#
# SINGLE POPULATION, BY CONSTRUCTION, which is what makes it survive a fresh
# seed.  The taken set is the write's OWN emission.  The rim is derived from
# it.  The heights on both sides come from the same generated field plus the
# ledger's own blobs.  No second world, no prior save, no reference artefact.
RIM_EPS_M = 0.001            # the shipped reading's float noise (the neuter)

# ONE RULE FOR "OVER THE LIMIT", AND IT IS THE JUDGE'S.  This gate refused a
# step only past `limit + RIM_EPS_M`, a millimetre, while the acceptance gate
# judging the same ground refuses past `limit + instruments.GROUND_QUANTUM_M`,
# a tenth of one (`accept/instruments.py::exceeds_step_limit`).  Two rules
# for one question: a write could leave a step up to 0.9 mm over the quantum,
# pass here, and be refused by the gate that reads the world.  MEASURED,
# Fresh42 drive 3 step 4, W9-vestvik-lhwest: rim [-350,-500] from taken
# [-350,-501], 0.782426 m against 0.781286, 1.14 mm over -- real under both
# rules (11 quanta), so it stays charged to the plans that step there; a step
# between 0.1 and 1.0 mm over would have been passed here and refused there.
# The rule is IMPORTED, never restated.  NEUTER `_RIM_STEP_RULE_MODE =
# "rim_eps"`, the shipped reading, proven by selftest case (13i).
RIM_STEP_RULE_DEPENDS_ON = ("accept.instruments.exceeds_step_limit",)
_RIM_STEP_RULE_MODE = "quantum"   # quantum | rim_eps (the shipped reading)


def step_over_limit(step_m: float, limit_m: float) -> bool:
    """Is `step_m` over `limit_m` by more than a reading resolves?  The ONE
    rule, the acceptance gate's own.  See RIM_STEP_RULE_DEPENDS_ON."""
    if _RIM_STEP_RULE_MODE == "rim_eps":
        return step_m > limit_m + RIM_EPS_M
    if str(JUMPSTART) not in sys.path:
        sys.path.append(str(JUMPSTART))
    from accept import instruments as _AI                  # noqa: PLC0415
    return _AI.exceeds_step_limit(step_m, limit_m)

# WHAT THE STEP AT A RIM PAIR ACTUALLY DEPENDS ON.  It used to depend on the
# rim's height BEFORE the write, on the premise that a sample outside the
# taken set is a sample this write does not move.  `taken` is "moved by more
# than `applied.AUTHOR_TOLERANCE_M`", which is a SUBSET of "written by this
# write", so the premise is false for every sample a write nudges by less
# than a centimetre -- and the step is then charged against ground that will
# not be there.  MEASURED on Fresh42 T1-portalhub-temple, 2026-09-24: the two
# steps that refused the whole segment were 0.7897 m at rim (-23, -152) and
# 0.7827 m at rim (-36, -132) against a 0.78128562650672 m limit, and this
# write's own delta at those two rim samples is +0.008381 m and +0.001517 m.
# Subtract them and the steps are 0.7813 and 0.7812: BOTH INSIDE THE LIMIT,
# both charged, and the segment refused for ground the write itself removes.
# THE LIMIT IS NOT WIDENED BY THIS -- the same 0.7813 judges the same pair;
# what changes is that the rim is measured on the surface the walker will
# actually stand on.
RIM_STEP_DEPENDS_ON = ("taken_height_after", "rim_height_after",
                       "rim_height_before")


def rim_of(taken) -> set[tuple[int, int]]:
    """The lattice samples ADJACENT to `taken` and NOT in it.

    Eight-connected, because a walker crosses diagonals and `roads.py`'s own
    severance graph judges a diagonal move as two 1 m mesh edges.  A rim
    computed on four neighbours would miss exactly the corner steps that
    instrument reports.
    """
    inside = {(int(x), int(z)) for x, z in taken}
    out: set[tuple[int, int]] = set()
    for x, z in inside:
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                if dx == 0 and dz == 0:
                    continue
                n = (x + dx, z + dz)
                if n not in inside:
                    out.add(n)
    return out


def rim_transfer(taken_after: dict, height_now, author_now=None, *,
                 height_after, limit_m: float = SLIDE_PER_M,
                 top: int = 40) -> dict:
    """Does this write leave a step a walker cannot take AT THE RIM of the
    ground it takes?

    `taken_after` maps each lattice sample this write AUTHORS to the applied
    height it will leave there.  `height_now` answers the CURRENT applied
    height at a lattice sample and `height_after` the height once this write
    has landed (either may be None where nothing can be read).  A caller
    whose taken set IS everything it writes passes the same function twice
    and says so; `ribbon.py` does not -- see RIM_STEP_DEPENDS_ON.
    `author_now` is optional and only names who is being stepped away from.

    JUDGED AS A DELTA, NEVER AS A STATE, which is `reemit.py`'s rule pointed
    at geometry instead of at time.  A rim pair already over the limit before
    this write is PRE-EXISTING and is reported, not charged: the writer did
    not author either side of it.  A pair that was inside the limit and is
    outside it afterwards is CAUSED, and that is the refusal.

    A DIAGONAL IS TWO 1 m MESH EDGES THROUGH THE EASIER OF ITS TWO SHARED
    CORNERS, the same rule `accept/sections/roads.py` walks a near-miss line
    with and the same one this section's own pinned defect exists for: a
    diagonal read as ONE 1.414 m step accepts a wall in the cell.  A corner
    takes its AFTER height when this write authors it and its current height
    otherwise, because the corner is ground the walker actually stands on
    once the write has landed.

    A rim sample nothing can read is UNREADABLE and is neither passed nor
    refused -- it is counted and named, because a silent skip here is the
    absent-versus-empty failure this whole family of gates exists to refuse.
    """
    taken = {(int(x), int(z)): float(y) for (x, z), y in taken_after.items()}
    rim = rim_of(taken)

    def after_or_now(s):
        if s in taken:
            return taken[s]
        return height_after(s[0], s[1])

    caused, pre_existing = [], []
    unreadable, judged = 0, 0
    for r in sorted(rim):
        # TWO HEIGHTS AT THE RIM, AND THEY ARE TWO DIFFERENT CLAIMS.  `rn` is
        # the ground the walker steps onto AFTER this write, which is what
        # the verdict is about; `rb` is what stood there before, which is
        # what makes the verdict a DELTA instead of a state.
        rn = height_after(r[0], r[1])
        rb = height_now(r[0], r[1])
        if rn is None or rb is None:
            unreadable += 1
            continue
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                t = (r[0] + dx, r[1] + dz)
                if t not in taken:
                    continue
                diagonal = dx != 0 and dz != 0
                ta, tn = taken[t], height_now(t[0], t[1])
                if diagonal:
                    # the two corners this move shares, each route judged as
                    # two 1 m edges, and the EASIER route decides
                    best_a = best_n = None
                    for c in ((t[0], r[1]), (r[0], t[1])):
                        ca, cn = after_or_now(c), height_now(c[0], c[1])
                        if ca is None or cn is None:
                            continue
                        wa = max(abs(ta - ca), abs(ca - rn))
                        wn = (max(abs(tn - cn), abs(cn - rb))
                              if tn is not None else None)
                        if best_a is None or wa < best_a:
                            best_a, best_n = wa, wn
                    if best_a is None:
                        unreadable += 1
                        continue
                    step_after, step_before = best_a, best_n
                    span = "two 1 m edges through the easier corner"
                else:
                    step_after = abs(ta - rn)
                    step_before = abs(tn - rb) if tn is not None else None
                    span = "one 1 m edge"
                judged += 1
                if not step_over_limit(step_after, limit_m):
                    continue
                row = {
                    "rim_xz": [r[0], r[1]], "taken_xz": [t[0], t[1]],
                    "step_after_m": round(step_after, 4),
                    "step_before_m": (None if step_before is None
                                      else round(step_before, 4)),
                    "limit_m": round(limit_m, 4), "span": span,
                    "rim_height_m": round(rn, 4),
                    "rim_height_before_m": round(rb, 4),
                    "taken_height_after_m": round(ta, 4),
                    "taken_height_before_m": (None if tn is None
                                              else round(tn, 4)),
                    "rim_authored_by": (author_now(r[0], r[1])
                                        if author_now else None)}
                if step_before is not None and step_over_limit(step_before,
                                                               limit_m):
                    pre_existing.append(row)
                else:
                    caused.append(row)
    caused.sort(key=lambda d: -d["step_after_m"])
    pre_existing.sort(key=lambda d: -d["step_after_m"])
    return {
        "verdict": "REFUSED" if caused else "CLEAR",
        "rule": "a write that TAKES ground must meet the ground at the RIM of "
                "what it takes. The closure is over DISPUTED samples and a rim "
                "sample nobody disagreed about is invisible to it, which is "
                "how a restitution moves its own cliff onto the segment it "
                "takes from instead of removing it.",
        "taken_samples": len(taken),
        "rim_samples": len(rim),
        "rim_pairs_judged": judged,
        "rim_samples_unreadable": unreadable,
        "caused": len(caused),
        "pre_existing": len(pre_existing),
        "worst_caused_m": (caused[0]["step_after_m"] if caused else 0.0),
        "worst_caused_at": (caused[0]["rim_xz"] if caused else None),
        "caused_rows": caused[:top],
        "pre_existing_rows": pre_existing[:top],
        "method": "MEASURED on the 1 m lattice. Orthogonal moves are one mesh "
                  "edge; a diagonal is TWO 1 m edges through the easier of its "
                  "two shared corners, a corner taking its AFTER height where "
                  "this write authors it. Judged as a DELTA: a pair already "
                  "over the limit is PRE-EXISTING and reported, never charged.",
        "tool": "roads/junction.py::rim_transfer",
    }


def zones_touched(rib: dict, moved: set[int]) -> set[tuple[int, int]]:
    """The zones a re-emit of the moved stations has to rewrite.

    Zone index is `floor((w + 32) / 64)`, the same arithmetic
    `writer._pad_footprint_check` uses, and the band is widened by the
    paved half-extent plus `ribbon.BATTER_MAX_M` because a re-graded station
    re-lays its batter too.
    """
    out: set[tuple[int, int]] = set()
    reach = rib["edge_m"] + 10.5           # ribbon.BATTER_MAX_M
    for k in moved:
        x, z = rib["xz"][k]
        for sx in (-reach, 0.0, reach):
            for sz in (-reach, 0.0, reach):
                out.add((int(math.floor((x + sx + 32.0) / 64.0)),
                         int(math.floor((z + sz + 32.0) / 64.0))))
    return out


# ---------------------------------------------------------------------------
# THE HALF OF `--reconcile` THAT WAS MISSING: a cluster is not a point
# ---------------------------------------------------------------------------
# MEASURED on Ulfsland, 2026-09-22, over the 45-segment / 58-pad population:
# 204 junctions, 50 with an impassable pair, and 19 of those 50 have ZERO
# impassable pairs AT THEIR ANCHOR -- 312 impassable pairs in total that
# `--reconcile` declined to cost, because its selection asked only
# `impassable_pairs_at_anchor`.  The worst of them is the TEMPLE:
#
#   JUNCTION 5-way at (-56.0, -34.0), anchor spread 0.004 m  -- RECONCILED
#   156 impassable pairs in the same cluster, 0 at the anchor,
#   worst 4.25 m between T1-portalhub-temple and T13-temple-harboursouth
#   at (-58.66, -58.02), 24.1 m from the anchor.
#
# That 4.25 m step is link 5 of the chain that gates 13 of Ulfsland's 28
# destinations, and the loop the operator was left in is closed with no exit:
# `ribbon.py plan --segment T1-portalhub-temple` REFUSES the write and prints
# "Reconcile with `roads/junction.py --reconcile --at -58.66,-58.02` and
# apply the proposal", and that command answered `"reconciliation": []`.
# A refusal that names a tool which declines to answer is worse than no
# refusal: it reads as "already reconciled" and it is not.
#
# The reasoning that produced the gap is half right and is preserved: ONE
# datum at the anchor cannot move a pair 24 m down a shared corridor.  The
# wrong half was the conclusion -- say nothing.  A 24 m shared corridor is
# not one junction, it is a CHAIN of places where two participants meet, and
# each of those places has its own datum question that `reconcile()` already
# answers.  So the corridor is RE-ANCHORED on the worst impassable pair of
# each participant pair and costed there.
ANCHOR_RADIUS_M = 1.0          # the radius `cluster` calls "at the anchor"


def corridor_junctions(rep: dict) -> list[dict]:
    """Sub-junctions for the impassable pairs a cluster's ANCHOR cannot see.

    One per (A, B) participant pair per cluster, anchored at THAT pair's
    worst row and carrying only those two participants' heights AT THAT
    POINT.  `reconcile()` takes it unchanged: it finds each ribbon's own
    nearest station to `at`, tries every participant height as the datum and
    prices the taper, which is exactly the question "who moves, and how far
    back does the re-grade run".

    SCOPED TO CLUSTERS THAT ARE CLEAN AT THE ANCHOR.  Where the anchor is
    itself impassable the existing proposal already moves these participants
    and a second, nearby proposal would double-count the same move; the gap
    this closes is the cluster that reads RECONCILED and is not.
    """
    out: list[dict] = []
    for j in rep["junctions"]:
        if not j["impassable_pairs"] or j["impassable_pairs_at_anchor"]:
            continue
        ax, az = j["at"]
        parts = set(j["participants"])
        worst: dict[tuple[str, str], dict] = {}
        for r in rep["pairs"]:
            if not r["impassable"] or r["a"] not in parts or r["b"] not in parts:
                continue
            if math.hypot(r["xz"][0] - ax, r["xz"][1] - az) <= ANCHOR_RADIUS_M:
                continue
            k = tuple(sorted((r["a"], r["b"])))
            if k not in worst or r["spread_m"] > worst[k]["spread_m"]:
                worst[k] = r
        for k, r in sorted(worst.items(), key=lambda kv: -kv[1]["spread_m"]):
            heights = {r["a"]: r["a_y"], r["b"]: r["b_y"]}
            out.append({
                "at": list(r["xz"]), "ways": 2,
                "spread_m": r["spread_m"],
                "participants": dict(heights),
                "design_heights": dict(heights),
                "sharing_at_anchor": sorted(heights),
                "in_cluster_not_at_anchor": [],
                "impassable_pairs": 1,
                "impassable_pairs_at_anchor": 1,
                "impassable_pairs_in_shared_corridor": 1,
                "over_budget_pairs": 0,
                "worst_pair": [r["a"], r["b"]],
                "worst_pair_at": list(r["xz"]),
                "worst_pair_spread_m": r["spread_m"],
                "worst_pair_gap_m": r["gap_m"],
                "worst_pair_slide_allow_m": r["slide_allow_m"],
                "corridor_of": {
                    "cluster_anchor": [ax, az],
                    "cluster_ways": j["ways"],
                    "cluster_anchor_spread_m": j["spread_m"],
                    "cluster_impassable_pairs": j["impassable_pairs"],
                    "distance_from_anchor_m": round(
                        math.hypot(r["xz"][0] - ax, r["xz"][1] - az), 2),
                    "why": ("this cluster is FLUSH AT ITS ANCHOR and "
                            "impassable down its shared corridor; one datum "
                            "at the anchor cannot reach here, so the pair is "
                            "re-anchored on its own worst row and costed "
                            "there"),
                },
                "anchor_is": ("the WORST IMPASSABLE ROW of this participant "
                              "pair inside a cluster whose own anchor is "
                              "flush -- see `corridor_of`"),
            })
    return out


def reconcile(junction: dict, ribbons: list[dict], pads: list[dict],
              candidates: list[float] | None = None,
              keep_profiles: bool = False) -> dict:
    """Propose ONE datum for a junction and cost every participant's move.

    A PAD FORCES THE DATUM.  Inside a pad's rectangle the pad wins -- it is a
    building's footing and `ribbon.stamp` refuses the road there -- so if a
    pad participates, the only admissible datum is the pad's.  Otherwise
    every participant's own height is tried as the datum and the one with the
    least re-graded arc is proposed.
    """
    by_label = {r["label"]: r for r in ribbons}
    pad_by_label = {p["label"]: p for p in pads}
    pad_labels = [p for p in junction["participants"]
                  if p in pad_by_label and p not in by_label]

    # A PAD IN THE JUNCTION MEANS THE DATUM IS ALREADY DECIDED, so there is
    # nothing to choose and everything to cost: each road holds the pad's
    # datum over the pad's reach and has to carry the rest away within its
    # own grade limit.  `pin_to_pad` publishes that arc budget, so a refusal
    # names the metres missing instead of just saying no.
    #
    # AND THE PROPOSAL MUST BE APPLICABLE, which for eleven months it was
    # not.  MEASURED 2026-09-22 on `S8-wtsouth-treesth` against the
    # `tree-sth-2` pad, the one link standing between the network and the
    # `treesth` destination: `--reconcile` reports datum 76.19, both legs
    # feasible, grade 0.0145 and 0.0273 against a 0.12 limit, SHORTFALL 0.0 m
    # -- and `--apply` refuses it with "1 of 1 proposal(s) need more than the
    # 1.0x relaxation authorised -- worst 0.0x".  There is no 0.0x: this
    # branch published no `worst_relax_factor` at all, `--apply`'s own
    # `blocked` test reads `is None` as over-budget, and the printed number
    # is `max(... or 0.0)` naming the absence.  So EVERY road/pad
    # reconciliation on every world was unapplyable, and the refusal quoted
    # a quantity that had never been measured.
    #
    # The second half is worse, because it fails the other way.  `--apply`
    # collects `m["_profile"]`, which only the road/road branch produced;
    # past the gate this branch would have matched zero moves and printed
    # "nothing to apply: every participant is already at the proposed datum"
    # over a 1.248 m disagreement.  ABSENT IS NOT ZERO, and an absent
    # profile is not an agreed one.
    #
    # THE COST IS ATTACHED TO THE REPAIR THAT CAN BE SHIPPED.  `pin_to_pad`
    # prices the RAMP to the next fixed station, which on T1 is 303 of 387
    # stations and 566 m from the pad -- `rejoin_profile`'s own docstring
    # says so and case (10) refuses that shape.  So the applicable profile,
    # its station count, its worst shift and its relaxation factor all come
    # from the REJOIN, and the ramp's arc budget stays beside them because a
    # shortfall is the number a designer acts on.
    if pad_labels:
        legs = []
        feasible = True
        no_taper = 0
        relaxes: list[float] = []
        rejoin_arc = 0.0
        ramp_arc = 0.0
        zones: set[tuple[int, int]] = set()
        for label in junction["participants"]:
            rib = by_label.get(label)
            if rib is None:
                continue
            for pl in pad_labels:
                pad = pad_by_label[pl]
                r = pin_to_pad(rib, pad, rib["grade_limit"])
                r["segment"] = label
                r["grade_limit_is_override"] = rib.get(
                    "grade_limit_is_override")
                r["grade_limit_design"] = rib.get("grade_limit_design")
                ramp_arc += sum(lg.get("arc_available_m", 0.0)
                                for lg in r.get("legs", ()))
                if not r.get("pinned_stations"):
                    legs.append(r)            # this road does not reach it
                    continue
                y0 = rib["y"]
                y_new = rejoin_profile(rib, pad)
                if y_new is None:
                    no_taper += 1
                    r["feasible"] = False
                    r.setdefault("why", "no rejoin profile: this road does "
                                        "not reach the pad")
                    legs.append(r)
                    feasible = False
                    continue
                moved = sorted(k for k in range(len(y0))
                               if abs(y_new[k] - y0[k]) > 1e-6)
                g_before = max(profile_grades(rib["xz"], y0), default=0.0)
                g_after = max(profile_grades(rib["xz"], y_new), default=0.0)
                baseline = max(rib["grade_limit"], g_before)
                relax = (g_after / baseline) if baseline > 0 else None
                pin_hi = max(k for k in range(len(y0))
                             if _pad_gap(pad, *rib["xz"][k]) <= rib["half_m"])
                pin_lo = min(k for k in range(len(y0))
                             if _pad_gap(pad, *rib["xz"][k]) <= rib["half_m"])
                arc = rib["arc"]
                taper_m = (max(abs(arc[moved[-1]] - arc[pin_hi]),
                               abs(arc[moved[0]] - arc[pin_lo]))
                           if moved else 0.0)
                rejoin_arc += taper_m
                zones |= zones_touched(rib, set(moved))
                # THE LEG BUDGET IS THE RAMP'S; THE VERDICT IS THE REJOIN'S.
                # They disagree on purpose and both travel: a leg shortfall
                # says the ramp to a FIXED station cannot be carried, and
                # where that bites the rejoin runs into the same station and
                # its gradient says so -- which is the quantity the slide
                # limit is stated in.
                r["ramp_legs_feasible"] = all(lg.get("feasible")
                                              for lg in r.get("legs", ()))
                r.update({
                    "delta_m": round(max((abs(y_new[k] - y0[k])
                                          for k in moved), default=0.0), 3),
                    "taper_m": round(taper_m, 1),
                    "stations_moved": len(moved),
                    "moved_span": ([moved[0], moved[-1]] if moved else None),
                    "grade_before": round(g_before, 4),
                    "grade_after": round(g_after, 4),
                    "grade_limit": rib["grade_limit"],
                    "judged_against": round(baseline, 4),
                    "judged_against_is": (
                        "max(grade_limit, the gradient this profile already "
                        "walks) -- the same baseline the road/road branch is "
                        "judged on"),
                    "relax_factor": (None if relax is None
                                     else round(relax, 3)),
                    "repair": "roads/junction.py::rejoin_profile",
                    "zones": len(zones_touched(rib, set(moved))),
                    "zone_list": sorted(zones_touched(rib, set(moved))),
                })
                r["feasible"] = bool(relax is not None
                                     and relax <= 1.001 and moved)
                if not moved:
                    r["why"] = ("the rejoin moves no station: this road is "
                                "already on the pad's datum")
                    r["feasible"] = True
                if relax is not None:
                    relaxes.append(relax)
                feasible = feasible and r["feasible"]
                if keep_profiles:
                    r["_profile"] = y_new
                legs.append(r)
        return {"at": junction["at"], "ways": junction["ways"],
                "spread_m": junction["spread_m"],
                "datum_forced_by_pad": pad_labels,
                "proposed": {"datum_y": pad_by_label[pad_labels[0]]["datum_y"],
                             "all_feasible": feasible,
                             "moves_with_no_taper": no_taper,
                             "worst_relax_factor": round(
                                 max(relaxes, default=1.0), 3),
                             "regraded_arc_m": round(rejoin_arc, 1),
                             "ramp_arc_m": round(ramp_arc, 1),
                             "zones_to_reemit": len(zones),
                             "zone_list": sorted(list(zones)),
                             "moves": legs},
                "options": legs}

    cands = candidates or sorted(set(junction["design_heights"].values()))
    forced = []
    jx, jz = junction["at"]
    options = []
    for datum in cands:
        moves = []
        ok = True
        arc_total = 0.0
        zones: set[tuple[int, int]] = set()
        # ONLY the participants that actually share the anchor's ground.
        # A segment in the same cluster that does not reach the anchor is
        # not party to this disagreement and moving it would be a cost with
        # no defect behind it.
        for label, y in junction["design_heights"].items():
            rib = by_label.get(label)
            if rib is None:
                continue                       # a pad does not move
            # the station of this ribbon that is at the junction
            st = min(range(len(rib["xz"])),
                     key=lambda k: math.hypot(rib["xz"][k][0] - jx,
                                              rib["xz"][k][1] - jz))
            delta = datum - rib["y"][st]
            if abs(delta) <= 0.01:
                moves.append({"segment": label, "delta_m": round(delta, 3),
                              "taper_m": 0.0, "stations_moved": 0,
                              "feasible": True, "zones": 0})
                continue
            # A RECONCILIATION AT ONE PLACE MAY NOT MOVE ANOTHER JUNCTION'S
            # DATUM.  A run's own END STATIONS are the plan's junction nodes
            # -- every other segment meeting there was fitted to them -- so
            # a taper started in the MIDDLE of a run must die before it
            # reaches one.  MEASURED, and it is why this exists: the T1/T13
            # corridor proposal at (-58.66, -58.02) tapered -4.200 m over
            # 104 m across 84 stations, dragged T1's temple terminal from
            # 65.036 down to 62.998, and the write-path gate then refused
            # T1 against FOUR roads at the five-way it had just been
            # reconciled with -- trading a 4.25 m step for four 2.04 m ones.
            # An end station that IS the station being moved stays free:
            # that is the anchor reconciliation itself, which is allowed to
            # move the datum because every participant moves with it.
            ends = {0, len(rib["y"]) - 1} - {st}
            rib_held = (dict(rib, fixed=[bool(f) or k in ends
                                         for k, f in enumerate(
                                             rib.get("fixed")
                                             or [False] * len(rib["y"]))],
                             why_fixed=[w or ("a junction node this run "
                                              "terminates at" if k in ends
                                              else None)
                                        for k, w in enumerate(
                                            rib.get("why_fixed")
                                            or [None] * len(rib["y"]))])
                        if ends else rib)
            t = taper(rib_held, st, delta, rib["grade_limit"])
            if not t.get("feasible"):
                lr = t.get("least_relaxation")
                if not lr:
                    ok = False
                    moves.append({"segment": label,
                                  "delta_m": round(delta, 3),
                                  "feasible": False, "why": t["why"]})
                    continue
                t = taper(rib_held, st, delta, rib["grade_limit"],
                          relax_ceiling=(lr.get("max_grade_after_exact")
                                         or lr["max_grade_after"]) + 1e-9)
            y_new = t.get("profile")
            if y_new is None:
                ok = False
                moves.append({"segment": label, "delta_m": round(delta, 3),
                              "feasible": False, "why": t.get("why")})
                continue
            moved = {k for k in range(len(rib["y"]))
                     if abs(y_new[k] - rib["y"][k]) > 1e-6}
            zs = zones_touched(rib, moved)
            zones |= zs
            arc_total += t["taper_m"]
            relax = t.get("relax_factor_used") or 1.0
            if relax > 1.001:
                ok = False                     # not free: needs a ruling
            mv = {"segment": label, "station": st,
                  "from_y": round(rib["y"][st], 3),
                  "to_y": round(datum, 3),
                  "delta_m": round(delta, 3),
                  "taper_m": t["taper_m"],
                  "stations_moved": t["stations_moved"],
                  "grade_before": t["max_grade_before"],
                  "grade_after": t["max_grade_after"],
                  "grade_limit": rib["grade_limit"],
                  "judged_against": t["judged_against"],
                  "relax_factor": relax,
                  "relax_frontier": t.get("relax_frontier"),
                  "feasible": relax <= 1.001,
                  "zones": len(zs),
                  "zone_list": sorted(list(zs))}
            if keep_profiles:
                # THE PROFILE ITSELF, so a caller can APPLY the proposal and
                # re-run the gate on it.  Re-deriving it from the reported
                # numbers does not work and the attempt is instructive: the
                # reported `grade_after` is rounded to 4 dp, and feeding it
                # back as a ceiling rejects the very taper that produced it,
                # which silently dropped one of the temple's three moves.
                mv["_profile"] = y_new
            moves.append(mv)
        # AN INFEASIBLE MOVE MUST NOT SCORE AS 1.0.  `dict.get(k, 1.0)` on a
        # move that never produced a taper reads as "no relaxation needed",
        # which ranked a datum nothing could reach ABOVE one that works.
        bad = sum(1 for m in moves if not m.get("feasible"))
        worst_relax = max([m.get("relax_factor") or 1.0 for m in moves
                           if m.get("relax_factor") is not None],
                          default=1.0)
        options.append({"datum_y": round(datum, 3), "all_feasible": ok,
                        "moves_with_no_taper": bad,
                        "worst_relax_factor": round(worst_relax, 3),
                        "regraded_arc_m": round(arc_total, 1),
                        "zones_to_reemit": len(zones),
                        "zone_list": sorted(list(zones)),
                        "moves": moves})
    # CHEAPEST FIRST, AND "CHEAP" IS ORDERED: a datum every participant can
    # reach beats one that needs a relaxation, which beats one that needs a
    # relaxation AND more arc.  A relaxation is a change to a declared
    # promise; arc is only time.
    pick = min(options, key=lambda o: (o["moves_with_no_taper"],
                                       o["worst_relax_factor"],
                                       o["regraded_arc_m"]))
    return {"at": junction["at"], "ways": junction["ways"],
            "spread_m": junction["spread_m"],
            "datum_forced_by_pad": forced,
            "proposed": pick, "options": options}


# ---------------------------------------------------------------------------
# SELF-TEST.  BOTH DIRECTIONS.
# ---------------------------------------------------------------------------

def _synthetic_bad() -> tuple[list, list]:
    """Two 6 m trunks crossing at one point, 1.20 m apart in design height,
    plus a pad a road undercuts by 2.00 m.  Nothing world-specific in it."""
    A = {"kind": "ribbon", "id": "SYN-A", "run": 0, "label": "SYN-A",
         "cls": "trunk", "edge_m": 4.0, "half_m": 3.0, "grade_limit": 0.08,
         "xz": [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0)],
         "y": [50.0, 50.0, 50.0], "arc": [0.0, 10.0, 20.0],
         "source": "synthetic"}
    B = {"kind": "ribbon", "id": "SYN-B", "run": 0, "label": "SYN-B",
         "cls": "trunk", "edge_m": 4.0, "half_m": 3.0, "grade_limit": 0.08,
         "xz": [(10.0, -10.0), (10.0, 0.0), (10.0, 10.0)],
         "y": [51.2, 51.2, 51.2], "arc": [0.0, 10.0, 20.0],
         "source": "synthetic"}
    C = {"kind": "ribbon", "id": "SYN-C", "run": 0, "label": "SYN-C",
         "cls": "spur", "edge_m": 3.0, "half_m": 2.0, "grade_limit": 0.12,
         "xz": [(100.0, 0.0), (110.0, 0.0), (118.0, 0.0)],
         "y": [40.0, 40.0, 38.0], "arc": [0.0, 10.0, 18.0],
         "source": "synthetic"}
    pad = {"kind": "pad", "id": "SYN-PAD", "label": "SYN-PAD",
           "role": "site_pad", "seq": None, "x": 128.0, "z": 0.0,
           "hw": 10.0, "hd": 10.0, "datum_y": 40.0, "source": "synthetic"}
    return [A, B, C], [pad]


def _synthetic_good() -> tuple[list, list]:
    ribs, pads = _synthetic_bad()
    ribs[1]["y"] = [50.0, 50.0, 50.0]          # B agrees with A
    ribs[2]["y"] = [40.0, 40.0, 40.0]          # C meets the pad datum
    return ribs, pads


def _fresh42_classes() -> tuple[list, list]:
    """THE TWO REFUSALS FRESH42 PRODUCED, AT THEIR MEASURED NUMBERS.

    Not a copy of the world: three participants carrying the heights the
    recorded plans and the recorded ledger actually hold, so the two classes
    can be neutered independently on a fixture no seed can change.

      S11 CLASS -- a carriageway crossing a levelled rectangle. Fresh42:
        S11-southcape-lhsouth#2 at design 31.085 m runs INSIDE wt-spawn's
        20x20 pad whose written datum is 76.04 m. gap 0.0, spread 44.955.
      T8 CLASS -- two BUILT carriageways meeting. Fresh42:
        T8-stenvik-wttown#1 bed 39.740 against T13-temple-harboursouth
        38.533, gap 0.32 m, allowance 0.7813 m, spread 1.207.
    """
    s11 = {"kind": "ribbon", "id": "F42-S11", "run": 2, "label": "F42-S11#2",
           "cls": "trunk", "edge_m": 4.0, "half_m": 3.0, "grade_limit": 0.08,
           "xz": [(90.0, -108.0), (114.0, -108.0), (140.0, -108.0)],
           "y": [31.085, 31.085, 31.085], "arc": [0.0, 24.0, 50.0],
           "source": "fresh42-class"}
    # 0.32 m of unauthored ground between the two bands: half 3.0 + 3.0 plus
    # the gap puts the centrelines 6.32 m apart.
    t8 = {"kind": "ribbon", "id": "F42-T8", "run": 1, "label": "F42-T8#1",
          "cls": "trunk", "edge_m": 4.0, "half_m": 3.0, "grade_limit": 0.08,
          "xz": [(-60.0, -77.12), (-39.26, -77.12), (-20.0, -77.12)],
          "y": [39.740, 39.740, 39.740], "arc": [0.0, 20.74, 40.0],
          "source": "fresh42-class"}
    t13 = {"kind": "ribbon", "id": "F42-T13", "run": 0, "label": "F42-T13#0",
           "cls": "trunk", "edge_m": 4.0, "half_m": 3.0, "grade_limit": 0.08,
           "xz": [(-60.0, -83.44), (-39.26, -83.44), (-20.0, -83.44)],
           "y": [38.533, 38.533, 38.533], "arc": [0.0, 20.74, 40.0],
           "source": "fresh42-class"}
    wt_spawn = {"kind": "pad", "id": "wt-spawn-class", "label": "wt-spawn-class",
                "role": "site_pad", "seq": None, "x": 114.0, "z": -108.0,
                "hw": 10.0, "hd": 10.0, "datum_y": 76.04,
                "source": "fresh42-class"}
    return [s11, t8, t13], [wt_spawn]


def _apply(rib: dict, y: list[float]) -> dict:
    out = dict(rib)
    out["y"] = list(y)
    return out


def _find_junction(rep: dict, x: float, z: float, r: float = 2.0):
    for j in rep["junctions"]:
        if math.hypot(j["at"][0] - x, j["at"][1] - z) <= r:
            return j
    return None


def _pairs_between(rep: dict, key: str, a: str, b: str) -> list[dict]:
    return [r for r in rep[key]
            if ((r["a"].startswith(a) and r["b"].startswith(b))
                or (r["a"].startswith(b) and r["b"].startswith(a)))]


def selftest(seg_paths: list[Path], ledger: Path | None) -> int:
    """BOTH DIRECTIONS, on synthetic fixtures AND on the recorded plans.

    Six checks, three of them in the PASS direction, because a gate that has
    only ever been shown to fail is exactly as untrustworthy as one that has
    only ever been shown to pass: the first refuses good work, the second
    ships defects. This project has been bitten by both.
    """
    fails = []
    checks = []

    def check(name, ok, detail):
        checks.append((name, ok, detail))
        if not ok:
            fails.append(name)

    # (1) synthetic known-bad MUST be refused
    ribs, pads = _synthetic_bad()
    g = gate(ribs, pads)
    check("synthetic known-bad is REFUSED",
          g["verdict"] == "REFUSED" and len(g["refusals"]) >= 2,
          f"verdict={g['verdict']} refusals={len(g['refusals'])} "
          f"worst={g['survey']['worst_spread_m']} m "
          f"(road/road 1.200, road/pad 2.000)")

    # (2) the SAME fixture repaired MUST pass
    ribs, pads = _synthetic_good()
    g = gate(ribs, pads)
    check("synthetic repaired is AGREED", g["verdict"] == "AGREED",
          f"verdict={g['verdict']} "
          f"pairs={g['survey']['pairs_sharing_ground']} "
          f"flush={g['survey']['flush_within_1cm']}")

    # (3) A PAD-FORCED RECONCILIATION MUST BE APPLICABLE, and this case
    #      exists because for eleven months it was not.  `--reconcile`
    #      produced a datum, a feasible arc budget and a zone list for
    #      `S8-wtsouth-treesth` against `tree-sth-2` -- the single link
    #      between the network and the `treesth` destination -- and
    #      `--apply` answered "need more than the 1.0x relaxation
    #      authorised -- worst 0.0x".  There is no 0.0x.  The pad branch
    #      published no `worst_relax_factor`, `--apply`'s `blocked` test
    #      reads absence as over-budget, and its message printed the
    #      absence as a number.  Behind that, the branch also published no
    #      `_profile`, so a proposal that DID clear the gate would have
    #      matched no move and reported "nothing to apply: every
    #      participant is already at the proposed datum" over a 1.248 m
    #      disagreement -- an absence reported as agreement, which is the
    #      one direction this project cannot afford.
    #
    #      THREE PROPERTIES, and the pre-repair branch fails all three:
    #      the proposal carries the relaxation it needs; every feasible
    #      move carries the profile that delivers it; and applying those
    #      profiles actually settles the road/pad pairs.  The fixture is
    #      synthetic so the case survives the repair of any real world.
    sribs, spads = _synthetic_bad()
    sg = gate(sribs, spads)
    sj = _find_junction(sg, 118.0, 0.0, 4.0)
    pad_refused_before = [r for r in sg["refusals"] if r["b"] == "SYN-PAD"]
    if sj is None:
        check("a pad-forced reconciliation is APPLICABLE", False,
              "no road/pad junction in the synthetic fixture, so this case "
              "measured nothing")
    else:
        sp = reconcile(sj, sribs, spads, keep_profiles=True)
        prop = sp["proposed"]
        has_relax = prop.get("worst_relax_factor") is not None
        pinned_moves = [m for m in prop["moves"] if m.get("pinned_stations")]
        with_profile = [m for m in pinned_moves
                        if m.get("feasible") and m.get("_profile") is not None]
        appliable = bool(pinned_moves) and len(with_profile) == len(
            [m for m in pinned_moves if m.get("feasible")])
        after = sribs
        for m in with_profile:
            after = [_apply(q, m["_profile"]) if q["label"] == m["segment"]
                     else q for q in after]
        sg2 = gate(after, spads)
        pad_refused_after = [r for r in sg2["refusals"] if r["b"] == "SYN-PAD"]
        ok = (has_relax and appliable and pad_refused_before
              and not pad_refused_after)
        check("a pad-forced reconciliation is APPLICABLE", ok,
              (f"datum {prop['datum_y']} forced by {sp['datum_forced_by_pad']}"
               f": worst_relax_factor {prop.get('worst_relax_factor')} "
               f"(the pre-repair branch published NONE, which --apply reads "
               f"as over-budget and prints as 0.0x); "
               f"{len(with_profile)} of {len(pinned_moves)} pinned move(s) "
               f"carry an applicable `_profile` from "
               f"{pinned_moves[0].get('repair') if pinned_moves else None}, "
               f"{pinned_moves[0].get('stations_moved') if pinned_moves else 0}"
               f" station(s) moved by "
               f"{pinned_moves[0].get('delta_m') if pinned_moves else 0} m "
               f"over {pinned_moves[0].get('taper_m') if pinned_moves else 0}"
               f" m; road/pad refusals "
               f"{len(pad_refused_before)} -> {len(pad_refused_after)}")
              if ok else
              (f"worst_relax_factor present: {has_relax}; every feasible "
               f"pinned move applicable: {appliable} "
               f"({len(with_profile)}/{len(pinned_moves)}); road/pad "
               f"refusals {len(pad_refused_before)} -> "
               f"{len(pad_refused_after)}"))

    # (13i) ONE RULE FOR "OVER", THE JUDGE'S (RIM_STEP_RULE_DEPENDS_ON), BOTH
    #       WAYS.  Three rim pairs off one flat taken block: 0.5 mm over the
    #       limit (inside the shipped millimetre, five quanta over the
    #       acceptance gate's), 0.05 mm over (inside the quantum: the
    #       instrument cannot place it), and W9-vestvik-lhwest's own drive-3
    #       step against S5, 0.782426 m.  The survivor charges the first and
    #       third and passes the second; the neuter passes the first, which
    #       `accept/instruments.py::exceeds_step_limit` would refuse.
    global _RIM_STEP_RULE_MODE
    _q_block = {(x, 0) for x in range(0, 3)}
    _q_steps = {"half_mm": SLIDE_PER_M + 5e-4, "in_quantum": SLIDE_PER_M
                + 5e-5, "w9_s5": 0.782426}
    _q: dict = {}
    _q_was = _RIM_STEP_RULE_MODE
    try:
        for _arm in ("quantum", "rim_eps"):
            _RIM_STEP_RULE_MODE = _arm
            for _name, _h in _q_steps.items():
                _r = rim_transfer({s: _h for s in _q_block},
                                  lambda x, z: 0.0, None,
                                  height_after=lambda x, z, _h=_h: (
                                      _h if (x, z) in _q_block else 0.0))
                _q[(_arm, _name)] = _r["verdict"]
    finally:
        _RIM_STEP_RULE_MODE = _q_was
    check("the rim gate judges 'over' by the acceptance gate's own rule, and "
          "_neuter_rim_step_rule_rim_eps passes a step the judge refuses",
          _q[("quantum", "half_mm")] == "REFUSED"
          and _q[("quantum", "in_quantum")] == "CLEAR"
          and _q[("quantum", "w9_s5")] == "REFUSED"
          and _q[("rim_eps", "half_mm")] == "CLEAR"
          and _q[("rim_eps", "w9_s5")] == "REFUSED",
          f"{ {f'{a}/{n}': v for (a, n), v in _q.items()} }")

    if not seg_paths:
        for name, ok, detail in checks:
            print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
        return 1 if fails else 0

    ribbons, pads = load(seg_paths, ledger)
    g = gate(ribbons, pads)
    by_label = {r["label"]: r for r in ribbons}

    # (3) THE RECORDED TEMPLE FIVE-WAY MUST BE REFUSED, at its own anchor and
    #     with the operator's own number.
    t = _find_junction(g, -56.0, -34.0, 0.5)
    ok = (t is not None and t["ways"] >= 5 and t["spread_m"] >= 4.2
          and t["impassable_pairs"] > 0)
    check("recorded temple five-way is REFUSED",
          ok,
          (f"{t['ways']}-way at {t['at']} spread {t['spread_m']} m over "
           f"{t['impassable_pairs']} impassable pairs; {t['design_heights']}")
          if t else "no junction found within 0.5 m of (-56.0, -34.0)")

    # (4) THE RECORDED S1 / PORTAL-HALL PAD CONFLICT MUST BE REFUSED, and the
    #     ASSERTION IS ON THE PAD EDGE, which is the quantity this gate can
    #     defend.  NAME THE INSTRUMENT: the operator's 2.00 m is the pad
    #     DATUM 37.10 against S1's design height at (-300.0, 214.0), a point
    #     1.3 m OUTSIDE the pad rectangle whose 37.10 comes from T1 seq
    #     1984's WRITTEN apron -- a surface quantity, read out of a blob,
    #     that a plan-time gate does not have.  What the PLANS say is:
    #     1.675 m at the rectangle's own edge (-298.7, 214.0), where "the
    #     road MUST grade into the pad's EDGE" is measured, and 1.500 m at
    #     station 3 (-298.0, 214.0) INSIDE the rectangle, which is also the
    #     1.5 m `portal_hall.py`'s own docstring records as "the worst
    #     road/pad mismatch". Both are 2x over the 0.7813 m limit, so the
    #     refusal does not depend on which of the three numbers you take --
    #     but the report must not quote one instrument's number as another's.
    #
    #     THE POPULATION IS A FROZEN FIXTURE, NOT THE LIVE FILE, AND IT HAD
    #     TO BECOME ONE.  `segments.yaml` now carries the REPAIRED S1 --
    #     stations 1..12 pinned to the hall's 37.100 datum and re-graded out,
    #     12 stations moved, worst 2.000 m -- so asking the live plan for the
    #     old conflict finds nothing and this case reported FAIL with three
    #     empty lists.  A refuse-direction case that evaporates when its
    #     defect is repaired stops defending anything exactly when the
    #     repair needs defending.  So the known-bad profile is recorded here,
    #     from `git show HEAD:...segments.yaml`, and the case runs against
    #     the CURRENT nodes with those heights put back.
    S1_BEFORE_THE_APRON_REPAIR = {
        1: 36.6, 2: 36.1, 3: 35.6, 4: 35.1, 5: 34.6, 6: 34.1,
        7: 33.6, 8: 33.1, 9: 32.6, 10: 32.1, 11: 31.6, 12: 31.1,
    }
    s1_now = by_label.get("S1-portalhub-brgs2")
    s1_then = _apply(s1_now, [S1_BEFORE_THE_APRON_REPAIR.get(k, y)
                              for k, y in enumerate(s1_now["y"])])
    g_then = gate([s1_then if q is s1_now else q for q in ribbons], pads)
    hall = [r for r in g_then["refusals"]
            if r["b_kind"] == "pad" and r["b"] == "portal-hall"
            and r["a"].startswith("S1")]
    edge = [r for r in hall
            if abs(r["xz"][0] + 298.7) < 0.05 and abs(r["xz"][1] - 214.0) < 0.05]
    inside = [r for r in hall if r.get("pad_inside")]
    ok = (bool(edge) and abs(edge[0]["spread_m"] - 1.675) <= 0.01
          and bool(inside))
    check("recorded S1 / portal-hall pad conflict is REFUSED", ok,
          (f"pad edge (-298.7, 214.0): S1 {edge[0]['a_y']} vs pad datum "
           f"{edge[0]['b_y']} = {edge[0]['spread_m']} m against a "
           f"{edge[0]['slide_allow_m']} m limit; "
           f"{len(inside)} station(s) also AUTHOR inside the rectangle, "
           f"worst {max(r['spread_m'] for r in inside)} m; "
           f"{len(hall)} refused S1/pad rows in all -- on the FROZEN "
           f"pre-repair profile, 12 stations")
          if ok else
          (f"edge row {[(r['xz'], r['spread_m']) for r in edge]}, "
           f"inside rows {[(r['xz'], r['spread_m']) for r in inside]}, "
           f"all S1/pad rows {[(r['xz'], r['spread_m']) for r in hall]}"))

    # (4b) AND THE REPAIR THAT WAS APPLIED TO THE LIVE FILE MUST PASS.  Same
    #      pair, same instrument, the plan as it now stands: this is the
    #      pass-direction half that case (4) used to provide by accident and
    #      now provides on purpose.
    hall_now = [r for r in g["refusals"]
                if r["b_kind"] == "pad" and r["b"] == "portal-hall"
                and r["a"].startswith("S1")]
    shared_now = [r for r in g["pairs"]
                  if r["b"] == "portal-hall" and r["a"].startswith("S1")]
    check("the S1 apron AS RECORDED NOW agrees with the hall pad",
          not hall_now and bool(shared_now),
          f"{len(shared_now)} S1/pad rows share ground, {len(hall_now)} "
          f"refused; worst remaining spread "
          f"{max((r['spread_m'] for r in shared_now), default=0.0)} m "
          f"against a {shared_now[0]['slide_allow_m'] if shared_now else 0} m "
          f"limit")

    # (5) THE KNOWN-GOOD JUNCTIONS MUST PASS.  Judged where they MEET, which
    #     is the claim the operator actually made -- N0/N0b 53.28 = 53.28 and
    #     N0b/R3 45.61 = 45.61 at 2.0 m apart.
    for a, b in (("N0-", "N0b-"), ("N0b-", "R3-")):
        shared = _pairs_between(g, "pairs", a, b)
        refused = _pairs_between(g, "refusals", a, b)
        flush = min(shared, key=lambda r: r["centre_dist_m"]) if shared else None
        ok = bool(shared) and not refused
        check(f"known-good junction {a}/{b} PASSES", ok,
              (f"{len(shared)} shared-ground rows, 0 refused; where they meet "
               f"({flush['xz']}, {flush['centre_dist_m']} m apart) "
               f"{flush['a_y']} vs {flush['b_y']} = {flush['spread_m']} m "
               f"against a {flush['slide_allow_m']} m limit")
              if ok and flush else
              (f"REFUSED {len(refused)}: "
               f"{[(r['xz'], r['spread_m']) for r in refused[:3]]}"
               if refused else "no shared ground found at all -- the gate "
                               "cannot see this junction"))

    # (6) THE REPAIRED RECORDED PLAN MUST PASS.  This is the direction that
    #     proves the gate RESPONDS rather than merely objects: take the
    #     temple's own reconciliation proposal, apply it to in-memory copies
    #     of the five profiles, and re-run the WHOLE gate -- not just the
    #     one junction, so a repair that fixes the temple by breaking its
    #     neighbours cannot pass.
    if t:
        pr = reconcile(t, ribbons, pads, keep_profiles=True)
        q = pr["proposed"]
        applied = {m["segment"]: m["_profile"] for m in q["moves"]
                   if m.get("_profile") is not None}
        fixedup = [_apply(r, applied[r["label"]])
                   if r["label"] in applied else r for r in ribbons]
        g2 = gate(fixedup, pads)
        t2 = _find_junction(g2, -56.0, -34.0, 0.5)
        near_before = sum(1 for r in g["refusals"]
                          if math.hypot(r["xz"][0] + 56.0,
                                        r["xz"][1] + 34.0) <= 60.0)
        near_after = sum(1 for r in g2["refusals"]
                         if math.hypot(r["xz"][0] + 56.0,
                                       r["xz"][1] + 34.0) <= 60.0)
        # THE CLAIM UNDER TEST IS THE JUNCTION'S, AND NOTHING WIDER.  One
        # datum fixes the disagreement AT the anchor; it cannot fix the
        # 170 pairs where these same five roads run down a shared corridor
        # diverging, and asserting that it does would be testing the wrong
        # population.  So: the anchor spread comes inside the slide limit,
        # EVERY anchor pair stops being refused, and the refusal count in
        # the whole neighbourhood strictly falls -- the last clause is what
        # stops a "repair" that trades the junction for its surroundings.
        ok = (t2 is not None
              and t2["spread_m"] <= slide_allow(0.0)
              and t2["impassable_pairs_at_anchor"] == 0
              and near_after < near_before)
        check("temple AGREES once its own proposal is applied", ok,
              (f"datum {q['datum_y']} applied to {len(applied)} profiles at "
               f"relax factor {q['worst_relax_factor']}x: anchor spread "
               f"{t['spread_m']} -> {t2['spread_m']} m, impassable pairs AT "
               f"THE ANCHOR {t['impassable_pairs_at_anchor']} -> "
               f"{t2['impassable_pairs_at_anchor']}, refusals within 60 m "
               f"{near_before} -> {near_after}; {q['regraded_arc_m']} m "
               f"re-graded over {q['zones_to_reemit']} zones. Untouched by "
               f"this datum and reported separately: "
               f"{t2['impassable_pairs_in_shared_corridor']} pairs where "
               f"these roads share a corridor away from the junction.")
              if t2 else "the temple junction vanished after the repair, "
                         "which means the repair moved the roads apart "
                         "rather than levelling them")

    # (6b) THE CORRIDOR THE ANCHOR CANNOT SEE MUST STILL BE COSTED.
    #      Case (6) above proves a datum repairs the ANCHOR and says, in its
    #      own words, that it cannot touch the pairs down the shared
    #      corridor.  For months that sentence was the end of it: the
    #      selection in `main` asked `impassable_pairs_at_anchor` and the
    #      temple -- anchor spread 0.004 m, 156 impassable pairs, NONE at
    #      the anchor -- produced no proposal at all, while `ribbon.py`
    #      refused every T1 write and told the operator to run
    #      `--reconcile --at -58.66,-58.02`, which answered [].
    #
    #      BOTH DIRECTIONS, and the first one is the discriminator: under
    #      the OLD rule this count is ZERO.  If `corridor_junctions` ever
    #      stops finding the temple's corridor, the first clause fails.
    old_rule = [j for j in g["junctions"] if j["impassable_pairs_at_anchor"]]
    corr = corridor_junctions(g)
    temple_corr = [c for c in corr
                   if math.hypot(c["corridor_of"]["cluster_anchor"][0] + 56.0,
                                 c["corridor_of"]["cluster_anchor"][1] + 34.0)
                   <= 0.5]
    blind = sum(j["impassable_pairs"] for j in g["junctions"]
                if j["impassable_pairs"]
                and not j["impassable_pairs_at_anchor"])
    check("a cluster flush at its anchor still gets a proposal",
          bool(temple_corr) and not any(
              math.hypot(j["at"][0] + 56.0, j["at"][1] + 34.0) <= 0.5
              for j in old_rule),
          (f"the anchor-only rule offers {len(old_rule)} proposal(s) and the "
           f"temple is NOT among them (anchor spread {t['spread_m'] if t else '?'} m, "
           f"{t['impassable_pairs'] if t else '?'} impassable pairs, "
           f"{t['impassable_pairs_at_anchor'] if t else '?'} at the anchor); "
           f"corridor_junctions re-anchors {len(temple_corr)} of them here, "
           f"worst {temple_corr[0]['worst_pair']} "
           f"{temple_corr[0]['spread_m']} m at {temple_corr[0]['at']}, "
           f"{temple_corr[0]['corridor_of']['distance_from_anchor_m']} m from "
           f"the anchor. World-wide {blind} impassable pair(s) in "
           f"{sum(1 for j in g['junctions'] if j['impassable_pairs'] and not j['impassable_pairs_at_anchor'])} "
           f"cluster(s) were invisible to --reconcile."
           if temple_corr else
           "corridor_junctions found nothing at the temple -- the cluster "
           "that gates 13 destinations is back to having no proposal"))

    #      And the PASS direction: applying the proposal must actually move
    #      that pair inside the slide limit and must not buy it by breaking
    #      the neighbourhood.
    if temple_corr:
        c0 = temple_corr[0]
        cp = reconcile(c0, ribbons, pads, keep_profiles=True)
        capp = {m["segment"]: m["_profile"] for m in cp["proposed"]["moves"]
                if m.get("_profile") is not None}
        cfix = [_apply(r, capp[r["label"]]) if r["label"] in capp else r
                for r in ribbons]
        g3 = gate(cfix, pads)
        cx, cz = c0["at"]
        pair = set(c0["worst_pair"])
        before = sum(1 for r in g["refusals"]
                     if {r["a"], r["b"]} == pair
                     and math.hypot(r["xz"][0] - cx, r["xz"][1] - cz) <= 20.0)
        after = sum(1 for r in g3["refusals"]
                    if {r["a"], r["b"]} == pair
                    and math.hypot(r["xz"][0] - cx, r["xz"][1] - cz) <= 20.0)
        check("the re-anchored corridor proposal repairs its own pair",
              cp["proposed"]["moves_with_no_taper"] == 0 and after < before,
              (f"datum {cp['proposed']['datum_y']} moves "
               f"{[m['segment'] for m in cp['proposed']['moves'] if m.get('delta_m')]} "
               f"at relax {cp['proposed']['worst_relax_factor']}x over "
               f"{cp['proposed']['regraded_arc_m']} m / "
               f"{cp['proposed']['zones_to_reemit']} zones: "
               f"{'/'.join(sorted(pair))} refusals within 20 m of "
               f"{c0['at']} go {before} -> {after}"))

    # (7) A WAIVER MUST NOT BE A SECOND PATH TO PASS.  The acceptance
    #     mechanism is the obvious way to smuggle a conflict through, so it
    #     is tested in both directions too: a waiver quoting the MEASURED
    #     spread suppresses the refusal, and the SAME waiver quoting a stale
    #     number does not.  This project has already been bitten by "a guard
    #     with two paths to PASS".
    worst = g["refusals"][0]
    key = f"{worst['a']}|{worst['b']}"
    good = {key: {"spread_m": worst["spread_m"], "reason": "test"}}
    stale = {key: {"spread_m": round(worst["spread_m"] - 0.10, 3),
                   "reason": "test"}}
    g_ok = gate(ribbons, pads, good)
    g_bad = gate(ribbons, pads, stale)
    still_ok = [r for r in g_ok["refusals"]
                if r["a"] == worst["a"] and r["b"] == worst["b"]
                and r["a_station"] == worst["a_station"]]
    voided = [r for r in g_bad["refusals"]
              if r["a"] == worst["a"] and r["b"] == worst["b"]
              and r["a_station"] == worst["a_station"]]
    ok = (not still_ok and bool(voided)
          and voided[0].get("waiver_void") is not None)
    check("a waiver pinned to a STALE spread is VOID", ok,
          (f"{key} at {worst['spread_m']} m: waived when the waiver quotes "
           f"{worst['spread_m']}, still refused when it quotes "
           f"{stale[key]['spread_m']} -- "
           f"\"{voided[0]['waiver_void']}\"")
          if ok else
          (f"waived-with-correct-number still refused: {bool(still_ok)}; "
           f"waived-with-stale-number suppressed: {not voided}"))

    # (8) THE HALL APRON MUST REFUSE WITH AN ARC NUMBER, not just a verdict.
    #     "Refuse with numbers rather than forcing" is the standing rule, and
    #     an arc shortfall is the one number a designer can act on.
    hj = [j for j in g["junctions"] if "portal-hall" in j["participants"]]
    leg = None
    if hj:
        hp = reconcile(hj[0], ribbons, pads)
        for m in hp["proposed"]["moves"]:
            for lg in m.get("legs", ()):
                if not lg["feasible"] and m.get("segment", "").startswith("S1"):
                    leg = (m, lg)
    ok = leg is not None and leg[1]["arc_shortfall_m"] > 0.0
    check("hall apron refuses with an ARC SHORTFALL, not just a verdict", ok,
          (f"S1 held at pad datum {leg[0]['datum_y']} over "
           f"{leg[0]['pinned_stations']} stations (worst shift "
           f"{leg[0]['worst_pinned_shift_m']} m), then must carry "
           f"{leg[1]['height_to_carry_m']} m to station "
           f"{leg[1]['to_station']} which is FIXED by "
           f"{leg[1]['to_why_fixed']}: {leg[1]['arc_available_m']} m "
           f"available, {leg[1]['arc_needed_at_limit_m']} m needed at limit "
           f"{leg[1]['grade_limit']} -> would require grade "
           f"{leg[1]['grade_required']}; SHORTFALL "
           f"{leg[1]['arc_shortfall_m']} m of arc")
          if leg else "no infeasible S1 leg was produced at all")

    # (9) AND THE HALL APRON MUST AGREE ONCE ITS REPAIR IS APPLIED.  Same
    #     both-directions rule as the temple: apply `pinned_profile`, which
    #     holds the pad's datum over the pad's own ground and re-grades to
    #     the abutment level run, then re-measure.  The junction step goes
    #     away; what is LEFT is the arc shortfall, now expressed where it
    #     belongs -- as S1's own grade, not as a 2 m wall across somebody's
    #     apron.  Proving that is the whole point of the proposal: the
    #     reconciliation is possible and it is not free, and the gate can
    #     tell those two facts apart.
    hallpad = next((q for q in pads if q["label"] == "portal-hall"), None)
    s1 = next((q for q in ribbons if q["label"].startswith("S1")), None)
    if hallpad is not None and s1 is not None:
        # AGAINST THE FROZEN PRE-REPAIR PROFILE, for the same reason case (4)
        # is: the live file already carries this repair, so measuring the
        # BEFORE on it would report 0 -> 0 and prove nothing. `s1_then` is
        # the recorded conflict; `pinned_profile` is the proposal; the pair
        # of numbers below is the claim.
        y_new = pinned_profile(s1_then, hallpad)
        fixed2 = [_apply(q, y_new) if q is s1_now else q for q in ribbons]
        g3 = gate(fixed2, pads)
        was = [r for r in g_then["refusals"]
               if r["b"] == "portal-hall" and r["a"].startswith("S1")]
        now = [r for r in g3["refusals"]
               if r["b"] == "portal-hall" and r["a"].startswith("S1")]
        worst_pair_now = [r for r in g3["pairs"]
                          if r["b"] == "portal-hall" and r["a"].startswith("S1")]
        gr_before = max(profile_grades(s1_then["xz"], s1_then["y"]), default=0.0)
        gr_after = max(profile_grades(s1_then["xz"], y_new), default=0.0)
        ok = (not now) and bool(was)
        check("hall apron AGREES once the pad datum is held", ok,
              (f"S1/pad refusals {len(was)} -> {len(now)}; worst remaining "
               f"S1/pad spread "
               f"{max([r['spread_m'] for r in worst_pair_now], default=0.0)} m "
               f"against a {slide_allow(0.0):.4f} m limit. THE COST, stated "
               f"rather than hidden: S1's own worst grade "
               f"{gr_before:.4f} -> {gr_after:.4f} against a declared limit "
               f"of {s1['grade_limit']} (itself already an override of "
               f"{s1['grade_limit_design']}), which is the 8.00 m arc "
               f"shortfall re-expressed as grade. Walkable -- "
               f"{gr_after:.4f} is under tan(38) = {SLIDE_PER_M:.4f} -- but "
               f"outside policy, so it needs a ruling, not a commit.")
              if ok else
              (f"S1/pad refusals {len(was)} -> {len(now)}: "
               f"{[(r['xz'], r['spread_m']) for r in now[:4]]}"))

    # (10) A PIN REPAIR MUST TERMINATE WHERE IT MEETS THE ROAD'S OWN LINE.
    #      This case fails on `pinned_profile`, which is the shape that was
    #      shipped as "the repair, made applicable", and passes on
    #      `rejoin_profile`.  The property is DERIVED, not a chosen bound:
    #      a pad's datum is a statement about the pad's own ground, so the
    #      repair it forces is finished the moment the profile is back on
    #      the line the router fitted to the hillside.  Every station past
    #      that point is being moved for no reason, and on a profile fitted
    #      inside a +/-8 m clamp "no reason" is how a road leaves its own
    #      cut/fill box.  The second half of the property is the same rule
    #      stated on gradient: a repair that removes an authority seam must
    #      not make the road steeper anywhere.
    t1 = next((q for q in ribbons
               if q["label"].startswith("T1-portalhub-temple")), None)
    if hallpad is not None and t1 is not None:
        ramp = pinned_profile(t1, hallpad)
        join = rejoin_profile(t1, hallpad)
        y_orig = t1["y"]
        n = len(y_orig)

        def _moved(y):
            return [k for k in range(n) if abs(y[k] - y_orig[k]) > 1e-6]

        m_ramp, m_join = _moved(ramp), _moved(join)
        far_ramp = max((abs(ramp[k] - y_orig[k]) for k in m_ramp), default=0)
        far_join = max((abs(join[k] - y_orig[k]) for k in m_join), default=0)
        g_before = max(profile_grades(t1["xz"], y_orig), default=0.0)
        g_ramp = max(profile_grades(t1["xz"], ramp), default=0.0)
        g_join = max(profile_grades(t1["xz"], join), default=0.0)
        pinned = [k for k in range(n)
                  if _pad_gap(hallpad, *t1["xz"][k]) <= t1["half_m"]]
        shift = max(abs(hallpad["datum_y"] - y_orig[k]) for k in pinned)
        # TERMINATES: the moved stations are one contiguous run out of the
        # pad, and past its last station the profile IS the original.
        end_join = max(m_join) if m_join else max(pinned)
        end_ramp = max(m_ramp) if m_ramp else max(pinned)
        # AN EMPTY MOVE SET IS TERMINATION, NOT A CRASH.  `end_join` already
        # had this guard and `min(m_join)` did not, so the day T1's recorded
        # profile actually CARRIED the hall rejoin (seq 5086, "52 of 387
        # stations") this case died on `min() arg is empty` instead of
        # passing.  A selftest that crashes once its subject is repaired
        # cannot be run again by the agent who repaired it.
        contiguous = (not m_join
                      or (m_join == list(range(min(m_join), end_join + 1))
                          and all(abs(join[k] - y_orig[k]) <= 1e-6
                                  for k in range(end_join + 1, n))))
        reach_join = abs(t1["arc"][end_join] - t1["arc"][max(pinned)])
        reach_ramp = abs(t1["arc"][end_ramp] - t1["arc"][max(pinned)])
        ok = (contiguous and g_join <= g_before + 1e-9
              and end_ramp > end_join)
        check("a pin repair terminates where it meets the road's own line",
              ok,
              (f"pad shift {shift:.3f} m at limit {t1['grade_limit']}. "
               f"REJOIN: {len(m_join)} of {n} stations, last moved "
               f"{end_join}, {reach_join:.1f} m of arc from the pad, worst "
               f"shift {far_join:.3f} m, worst grade {g_before:.5f} -> "
               f"{g_join:.5f}, terminates={contiguous}. RAMP "
               f"(pinned_profile, the shape this case exists to refuse): "
               f"{len(m_ramp)} of {n} stations, last moved {end_ramp}, "
               f"{reach_ramp:.1f} m of arc from the pad, worst shift "
               f"{far_ramp:.3f} m, worst grade -> {g_ramp:.5f}"))

        # (11) AND THE REJOINED PROFILE MUST STILL SETTLE THE PAD.
        fixed3 = [_apply(q, join) if q is t1 else q for q in ribbons]
        g4 = gate(fixed3, pads)
        was = [r for r in g["refusals"]
               if r["b"] == "portal-hall" and r["a"].startswith("T1")]
        now = [r for r in g4["refusals"]
               if r["b"] == "portal-hall" and r["a"].startswith("T1")]
        pr_now = [r for r in g4["pairs"]
                  if r["b"] == "portal-hall" and r["a"].startswith("T1")]
        check("T1 AGREES with the hall pad once rejoined",
              not now,
              f"T1/pad refusals {len(was)} -> {len(now)}; worst remaining "
              f"T1/pad spread "
              f"{max([r['spread_m'] for r in pr_now], default=0.0)} m "
              f"against a {slide_allow(0.0):.4f} m limit")

    # (12) AN EMIT ORDER THAT TAKES GROUND OFF A NEIGHBOUR MUST BE REFUSED.
    #      The known-bad state is the obvious one: re-emit T1 alone to fix
    #      its apron.  Nothing in the plans says that is destructive - the
    #      destruction is in the ORDER - so a gate that cannot see it lets
    #      an agent repair one road by breaking two.
    prior = ledger_write_order(ledger) if ledger else {}
    if prior and t1 is not None:
        lone = reemit_transfer(ribbons, pads, ["T1-portalhub-temple"], prior)
        check("a lone re-emit that steals a neighbour's ground is REFUSED",
              not lone["clean"] and lone["samples_changing_owner"] > 0,
              f"emitting T1 alone moves {lone['samples_changing_owner']} "
              f"disputed samples to it, off "
              f"{sorted(lone['losers'])}; worst "
              + ", ".join(f"{k} {v['worst_spread_m']} m at {v['worst_at']}"
                          for k, v in lone["losers"].items()))

        # (13) AND THE TOOL MUST BE ABLE TO NAME AN ORDER THAT IS CLEAN.
        #      Same plans, same repair, only the SEQUENCE differs.  The
        #      closure is computed on the PROPOSED profiles, which is why
        #      the S1 repair needs one write and the T1 repair needs
        #      eleven: pinning S1 to the hall datum settles S1's only
        #      dispute, and nothing in segments.yaml says so.
        s1c = clean_order(
            [_apply(q, pinned_profile(q, hallpad))
             if q is s1 else q for q in ribbons],
            pads, ["S1-portalhub-brgs2"], prior)
        t1c = clean_order(
            [_apply(q, rejoin_profile(q, hallpad))
             if q is t1 else q for q in ribbons],
            pads, ["T1-portalhub-temple"], prior)
        ok = (s1c["clean"] and s1c["segments_to_reemit"] == 1
              and t1c["clean"] and t1c["segments_to_reemit"] > 1)
        check("clean_order names a CLEAN sequence for each repair", ok,
              f"S1 apron repair: {s1c['segments_to_reemit']} write(s) "
              f"{s1c['order']}, clean={s1c['clean']}. T1 apron repair: "
              f"{t1c['segments_to_reemit']} writes, clean={t1c['clean']}, "
              f"pulled in by the closure {t1c['pulled_in_by_closure']}"
              + ("" if ok else
                 f"  [S1 losers {s1c['losers']}; T1 losers {t1c['losers']}]"))

        # (13b) THE RIM. `clean_order` was CLEAN, COMPLETE AND HONEST for the
        #       T3 restitution and the write it authorised still severed three
        #       destinations, because the closure is over DISPUTED samples and
        #       the sample that broke was one nobody disagreed about. See the
        #       measurement above `rim_transfer`. These cases are SYNTHETIC on
        #       purpose: they carry the world's own numbers -- 67.040 and
        #       69.153, the two beds of the real link -- without needing the
        #       chain, the blobs or a generated field, so they keep working on
        #       a fresh seed where none of those exist.
        #
        #       THE FIXTURE IS THE DEFECT: a flat shelf at 67.040, of which a
        #       repair takes a patch and raises it to 69.153. Nothing inside
        #       the taken patch steps at all -- a check that looks only at what
        #       it wrote sees a perfect road -- and every sample around its
        #       edge is now 2.113 m below it. The patch is placed so that
        #       (28,-11) is TAKEN and (28,-12) is on its RIM, which is the
        #       real pair: T3 authored (28,-11) and seq 181 still holds
        #       (28,-12) one metre away.
        shelf, raised = 67.040, 69.153
        patch = {(x, z) for x in range(28, 33) for z in range(-11, -6)}

        def _shelf_now(x, z):
            return shelf

        def _who(x, z):
            return "seq 181 road_T12_wtspawn_temple"
        # THE FIXTURE'S TAKEN SET IS EVERYTHING IT WRITES, so its rim stands
        # where it stood: `height_after` IS `height_now` here, and saying so
        # is the point -- see (13f), where it is not.
        took = rim_transfer({s: raised for s in patch}, _shelf_now, _who,
                            height_after=_shelf_now)
        named = [r for r in took["caused_rows"]
                 if r["rim_xz"] == [28, -12]]
        ok = (took["verdict"] == "REFUSED" and took["caused"] > 0
              and named
              and abs(named[0]["step_after_m"] - 2.113) < 5e-4
              and named[0]["step_before_m"] == 0.0)
        check("a RESTITUTION that moves its own cliff onto the RIM is "
              "REFUSED, and the rim sample is NAMED", ok,
              (f"verdict={took['verdict']} caused={took['caused']} "
               f"pre_existing={took['pre_existing']} over "
               f"{took['rim_pairs_judged']} rim pair(s) / "
               f"{took['rim_samples']} rim sample(s), "
               f"{took['rim_samples_unreadable']} unreadable; worst "
               f"{took['worst_caused_m']} m at {took['worst_caused_at']}; "
               f"(28,-12) is named {len(named)} time(s) at "
               f"{named[0]['step_after_m'] if named else None} m, which was "
               f"{named[0]['step_before_m'] if named else None} m before -- "
               f"the real link's own two beds {shelf} -> {raised}. THE "
               f"NEUTER, and it is the state this shipped in: judge only the "
               f"samples the write OWNS and the rim disappears."))

        # (13c) THE NEUTER, RUN. Restoring the defect -- looking only INSIDE
        #       the taken set, which is what every check on this write path
        #       did before this one existed -- MUST flip the case. A refusal
        #       nobody has seen pass is not evidence that the rule is load
        #       bearing.
        inside_only = rim_transfer(
            {s: raised for s in patch},
            lambda x, z: raised if (x, z) in patch else None, _who,
            height_after=lambda x, z: raised if (x, z) in patch else None)
        check("_neuter_rim_inside_only: judging only the ground the write "
              "OWNS reports CLEAR on the same known-bad fixture",
              inside_only["verdict"] == "CLEAR"
              and inside_only["caused"] == 0
              and inside_only["rim_samples_unreadable"]
              == inside_only["rim_samples"],
              f"verdict={inside_only['verdict']} caused="
              f"{inside_only['caused']} with all "
              f"{inside_only['rim_samples']} rim sample(s) unreadable -- "
              f"which is precisely why an UNREADABLE rim is COUNTED AND "
              f"NAMED by this function rather than silently skipped: a rim "
              f"nobody can read is not a rim nobody has to meet.")

        # (13d) AND IT MUST NOT REFUSE A REPAIR THAT MEETS ITS RIM. Same
        #       fixture, same taken set, but the surrounding shelf already
        #       stands at the height the repair writes: 0 caused, CLEAR. A
        #       gate only ever shown to refuse refuses good work.
        met = rim_transfer({s: raised for s in patch},
                           lambda x, z: raised, _who,
                           height_after=lambda x, z: raised)
        check("a repair whose RIM already meets it is CLEAR",
              met["verdict"] == "CLEAR" and met["caused"] == 0
              and met["rim_pairs_judged"] > 0,
              f"verdict={met['verdict']} caused={met['caused']} over "
              f"{met['rim_pairs_judged']} rim pair(s) judged")

        # (13e) A RIM STEP THAT WAS ALREADY OVER THE LIMIT IS PRE-EXISTING
        #       AND NOT THIS WRITE'S. `reemit.py`'s rule -- judge the DELTA,
        #       never the STATE -- pointed at geometry. Without this the
        #       check becomes a newer gate refusing an older world, which is
        #       the exact failure `reemit.py` was written to close.
        pre = rim_transfer({s: shelf for s in patch},
                           lambda x, z: shelf if (x, z) in patch else raised,
                           _who,
                           height_after=lambda x, z: (
                               shelf if (x, z) in patch else raised))
        check("a rim step that PREDATES this write is reported, not charged",
              pre["verdict"] == "CLEAR" and pre["caused"] == 0
              and pre["pre_existing"] > 0,
              f"verdict={pre['verdict']} caused={pre['caused']} "
              f"pre_existing={pre['pre_existing']} at "
              f"{abs(raised - shelf):.3f} m, unchanged by this write")

        # (13f) THE RIM IS GROUND THIS WRITE MAY ALSO MOVE, AND THE STEP IS
        #       MEASURED ON THE SURFACE THE WALKER WILL STAND ON.  `taken` is
        #       "moved by more than `applied.AUTHOR_TOLERANCE_M`" -- a SUBSET
        #       of "written by this write" -- so a sample the write nudges by
        #       less than a centimetre sits on the RIM while still moving.
        #       ALL FOUR NUMBERS ARE FRESH42 T1-portalhub-temple's OWN,
        #       2026-09-24, rim (-23, -152) from taken (-24, -152): the pair
        #       stood at 0.0854 m before, this write leaves the taken side
        #       0.7897 m from the rim's OLD height and 0.7813 m from its NEW
        #       one, because the write's own delta at that rim sample is
        #       0.008381 m -- the entire excess over the 0.78128562650672 m
        #       limit. Built in memory from those four numbers: no world, no
        #       blob, no generated field.
        rim_b, rim_a, tak_b, tak_a = 0.0, -0.008381, 0.0854, -0.7897
        block = {(x, z) for x in range(0, 5) for z in range(0, 5)}

        def _before(x, z):
            return tak_b if (x, z) in block else rim_b

        def _after(x, z):
            return tak_a if (x, z) in block else rim_a
        nudged = rim_transfer({s: tak_a for s in block}, _before, _who,
                              height_after=_after)
        check("a rim sample THIS WRITE also moves is judged on the height it "
              "will have, not the height it had",
              nudged["verdict"] == "CLEAR" and nudged["caused"] == 0
              and nudged["rim_pairs_judged"] > 0,
              f"verdict={nudged['verdict']} caused={nudged['caused']} over "
              f"{nudged['rim_pairs_judged']} rim pair(s); the pair stood at "
              f"{abs(tak_b - rim_b):.4f} m, and after this write it is "
              f"{abs(tak_a - rim_b):.4f} m against the rim's OLD height and "
              f"{abs(tak_a - rim_a):.4f} m against its NEW one, limit "
              f"{SLIDE_PER_M:.4f} judged by `step_over_limit`. The limit is "
              f"UNCHANGED; only "
              f"the surface it is read on is.")

        # (13g) NEUTER ONE, AND IT IS THE STATE THIS SHIPPED IN: read the
        #       rim's BEFORE height as its after height. The same fixture must
        #       go RED, or the correction above is decoration.
        stale_rim = rim_transfer({s: tak_a for s in block}, _before, _who,
                                 height_after=_before)
        check("_neuter_rim_reads_the_before_surface: charging the step "
              "against ground this write removes REFUSES the same fixture",
              stale_rim["verdict"] == "REFUSED" and stale_rim["caused"] > 0
              and abs(stale_rim["worst_caused_m"]
                      - abs(tak_a - rim_b)) < 5e-4,
              f"verdict={stale_rim['verdict']} caused={stale_rim['caused']} "
              f"worst {stale_rim['worst_caused_m']} m -- T1's own refusal, "
              f"reproduced from four numbers")

        # (13h) NEUTER TWO, IN THE OTHER DIRECTION, because a rule that only
        #       ever FORGIVES is a waiver. Where this write does NOT move the
        #       rim, the step is real and must still be charged; where it
        #       moves the rim AWAY, the charge must GROW. One arm loses the
        #       write's own delta, the other keeps it and reverses its sign.
        untouched = rim_transfer(
            {s: tak_a for s in block}, _before, _who,
            height_after=lambda x, z: tak_a if (x, z) in block else rim_b)
        away = rim_transfer(
            {s: tak_a for s in block}, _before, _who,
            height_after=lambda x, z: (tak_a if (x, z) in block
                                       else rim_b - rim_a))
        check("_neuter_rim_forgives_everything: an untouched rim is still "
              "charged and a rim pushed AWAY is charged MORE",
              untouched["verdict"] == "REFUSED"
              and away["verdict"] == "REFUSED"
              and away["worst_caused_m"] > untouched["worst_caused_m"]
              > nudged["worst_caused_m"],
              f"untouched {untouched['worst_caused_m']} m, pushed away "
              f"{away['worst_caused_m']} m, pulled in "
              f"{nudged['worst_caused_m']} m (0.0 = CLEAR). The reading is "
              f"not a forgiveness: it moves with the write's own delta in "
              f"BOTH directions.")

    # (14) THE WRITE-PATH GATE MUST REFUSE THE SEGMENT THAT CARRIES THE
    #      TEMPLE'S STEP.  Cases (3)-(13) all prove the RECONCILER; this one
    #      proves the thing `ribbon.py build` actually calls.  The gate was
    #      right about the temple for a long time and the write path never
    #      asked, which is the defect this closes -- so the wiring needs its
    #      own both-directions proof and not the reconciler's.
    # THE PRIMARY PLAN IS THIS SEED'S, resolved -- `segments.yaml` is the
    # DEFAULT seed's name, and on any other seed a literal fell silently to
    # `seg_paths[0]`.  Appended, never inserted: see network.py's path note.
    if str(JUMPSTART) not in sys.path:
        sys.path.append(str(JUMPSTART))
    import worldseed                                        # noqa: PLC0415
    prim_name = worldseed.segments_path().name
    prim = next((p for p in seg_paths if p.name == prim_name), seg_paths[0])
    files = population_files(prim)
    bad = build_gate("T3-temple-meadhall", files, ledger)
    ok = (bad["verdict"] == "REFUSED" and bad["worst"]["spread_m"] >= 4.4)
    check("the WRITE-PATH gate REFUSES T3 at the temple", ok,
          f"verdict={bad['verdict']} {bad['my_refusals']} of "
          f"{bad['my_pairs']} shared-ground pairs over the allowance, worst "
          f"{bad['worst']['spread_m']} m at {bad['worst']['xz']} against "
          f"{bad['worst']['b']}")

    # (15) AND IT MUST PASS A SEGMENT THAT AGREES WITH ITS NEIGHBOURS.
    #      T9-wttown-dockshore shares ground in 311 pairs and disagrees in
    #      none.  A write-path gate that refused it would stop every repair
    #      on this world and teach the operator to pass the waiver by
    #      reflex, which is worse than no gate -- the same failure the
    #      shoulder-edge corridor reading was rejected for.
    good = build_gate("T9-wttown-dockshore", files, ledger)
    check("the WRITE-PATH gate PASSES a segment that agrees",
          good["verdict"] == "AGREED",
          f"verdict={good['verdict']} over {good['my_pairs']} shared-ground "
          f"pairs, {good['my_refusals']} refused")

    # (16) THE POPULATION IS DERIVED AND IT MUST SPAN BOTH NETWORKS.  W4
    #      lives in segments.yaml and N0 in north_segments.yaml and they
    #      share ground at (-272.18, 1281.82) with a 4.522 m spread.  A
    #      write-path gate handed only the file it was writing from would
    #      PASS that pair by never looking at it, and nothing in the output
    #      would say so.
    w4 = build_gate("W4-wmid-wwest", files, ledger)
    cross = [r for r in w4["refusals"]
             if "N0-brgs2-wmid" in (r["a"].split("#")[0], r["b"].split("#")[0])]
    check("the derived population spans BOTH networks",
          len(files) >= 2 and bool(cross),
          f"population {[p.name for p in files]} -> {len(cross)} W4/N0 "
          f"cross-file refusal(s), worst "
          f"{max((r['spread_m'] for r in cross), default=0.0)} m")

    # (17) A WAIVER PINNED TO THE PAIR'S WORST SPREAD LETS THE WRITE
    #      THROUGH.  `bad["refusals"]` is sorted worst-first, so the first
    #      row under each key IS that pair's ceiling -- which is exactly the
    #      number the gate's own report hands the operator.
    waiver: dict[str, dict] = {}
    for r in bad["refusals"]:
        waiver.setdefault(accept_key(r),
                          {"spread_m": r["spread_m"], "reason": "selftest"})
    waived_ok = build_gate("T3-temple-meadhall", files, ledger, waiver)
    # (18) ...AND ONE QUOTING A STALE NUMBER MUST NOT.  The escape hatch is
    #      the obvious second path to PASS, so it is tested in both
    #      directions too: 1 cm of drift and the waiver is void.
    stale = {k: {"spread_m": v["spread_m"] + 0.02, "reason": "stale"}
             for k, v in waiver.items()}
    waived_bad = build_gate("T3-temple-meadhall", files, ledger, stale)
    check("a waiver at the MEASURED spread passes the write-path gate",
          waived_ok["verdict"] == "AGREED"
          and waived_ok["my_refusals"] == 0
          and waived_ok["my_waived"] == bad["my_refusals"],
          # KEYS AND ROWS ARE DIFFERENT POPULATIONS and quoting one for the
          # other is what this case caught: 65 refused ROWS collapse to 7
          # pair KEYS, and a correct waiver covers all 65.
          f"verdict={waived_ok['verdict']} -- {len(waiver)} pair key(s) "
          f"covering {bad['my_refusals']} refused row(s), "
          f"{waived_ok['my_waived']} honoured, {waived_ok['my_refusals']} "
          f"still refused")
    check("a waiver quoting a STALE spread is VOID at the write path",
          waived_bad["verdict"] == "REFUSED"
          and waived_bad["my_waived"] == 0,
          f"verdict={waived_bad['verdict']} with every spread moved 2 cm: "
          f"{waived_bad['my_refusals']} refused, {waived_bad['my_waived']} "
          f"honoured, first void reason "
          f"{waived_bad['refusals'][0].get('waiver_void')}")

    # ----------------------------------------------------------------- #
    # JUNCTION_POPULATION_DEPENDS_ON, NEUTERED IN EACH DIRECTION.
    # The two refusals Fresh42 produced were read as one gate measuring two
    # populations because 44.955 and 1.207 are thirty-seven times apart.
    # They are not: both are HEIGHT spreads over shared ground, and each
    # term answers for its own population and for nothing else.
    fribs, fpads = _fresh42_classes()
    full = gate(fribs, fpads)
    s11_full = _pairs_between(full, "refusals", "F42-S11", "wt-spawn-class")
    t8_full = _pairs_between(full, "refusals", "F42-T8", "F42-T13")
    check("BOTH Fresh42 classes are REFUSED at full strength",
          full["verdict"] == "REFUSED" and bool(s11_full) and bool(t8_full),
          f"S11 class worst {max((r['spread_m'] for r in s11_full), default=0)}"
          f" m at gap {min((r['gap_m'] for r in s11_full), default=-1)} m "
          f"(pad_inside={any(r.get('pad_inside') for r in s11_full)}); "
          f"T8 class worst {max((r['spread_m'] for r in t8_full), default=0)}"
          f" m at gap {min((r['gap_m'] for r in t8_full), default=-1)} m "
          f"against allowance "
          f"{t8_full[0]['slide_allow_m'] if t8_full else None} m")

    # LOSS ARM: without `pad_datums` the S11 class is UNSEEN -- a road may
    # run through a levelled rectangle 44.955 m below it and the gate says
    # AGREED for that pair.  This is the reading that would have deleted the
    # only instrument that caught Fresh42's cross-world datum leak.
    no_pads = gate(fribs, fpads, None, ("ribbon_design_heights",))
    s11_lost = _pairs_between(no_pads, "refusals", "F42-S11",
                              "wt-spawn-class")
    t8_survives = _pairs_between(no_pads, "refusals", "F42-T8", "F42-T13")
    check("dropping `pad_datums` LOSES the S11 class",
          not s11_lost,
          f"road/pad refusals {len(s11_lost)} (was {len(s11_full)}); "
          f"pads in population {no_pads['population']['pads']} still declared")
    # SURVIVOR, the same arm: road-to-road is carried by the ribbon term
    # alone, so the pad term is not propping up the road verdict.
    check("...and the T8 class SURVIVES it",
          bool(t8_survives)
          and t8_survives[0]["spread_m"] == t8_full[0]["spread_m"],
          f"T8 class still refused at {t8_survives[0]['spread_m'] if t8_survives else None} m "
          f"with the pad population empty")

    # THE OTHER DIRECTION: drop `ribbon_design_heights` and the T8 class is
    # unseen while the S11 class still refuses.  Neither term answers for
    # the other's population, which is what makes this one gate and not two.
    no_ribs = gate(fribs, fpads, None, ("pad_datums",))
    check("dropping `ribbon_design_heights` LOSES the T8 class, S11 survives",
          not _pairs_between(no_ribs, "refusals", "F42-T8", "F42-T13")
          and bool(_pairs_between(no_ribs, "refusals", "F42-S11",
                                  "wt-spawn-class")),
          f"road/road refusals "
          f"{len(_pairs_between(no_ribs, 'refusals', 'F42-T8', 'F42-T13'))}, "
          f"road/pad refusals "
          f"{len(_pairs_between(no_ribs, 'refusals', 'F42-S11', 'wt-spawn-class'))}")

    # PASS DIRECTION on the same fixture: the gate is not widened to let
    # either through -- it agrees only when the heights agree.  S11 held at
    # the pad's own datum and T13 raised to T8's bed.
    ok_ribs = [_apply(fribs[0], [76.04] * 3), fribs[1],
               _apply(fribs[2], [39.740] * 3)]
    repaired = gate(ok_ribs, fpads)
    check("both classes AGREE once the heights agree",
          repaired["verdict"] == "AGREED",
          f"verdict={repaired['verdict']} over "
          f"{repaired['survey']['pairs_sharing_ground']} pairs sharing "
          f"ground, flush {repaired['survey']['flush_within_1cm']}")

    for name, ok, detail in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    print(f"\n{len(checks) - len(fails)}/{len(checks)} checks passed")
    return 1 if fails else 0


# ---------------------------------------------------------------------------

def load(seg_paths: list[Path], ledger: Path | None,
         plan_pads: list[dict] | None = None) -> tuple[list, list]:
    ribbons: list[dict] = []
    for p in seg_paths:
        with open(p, "r", encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
        ribbons += ribbons_from_segments(doc, str(p))
    pads: list[dict] = []
    if ledger is not None:
        pads += pads_from_ledger(ledger)
    if plan_pads:
        pads += pads_from_plan(plan_pads)
    return ribbons, pads


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--segments", action="append", default=[],
                    help="a segments.yaml; repeatable")
    ap.add_argument("--ledger", default=None,
                    help="ledger.jsonl whose pad claims participate")
    ap.add_argument("--accept", default=None,
                    help="JSON of {'A|B': {spread_m, reason}} waivers; a "
                         "waiver is VOID if the measured spread moved >1 cm")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--reconcile", action="store_true",
                    help="propose and cost a datum for every junction with "
                         "an impassable pair at its anchor AND for every "
                         "pair a cluster's anchor cannot see (re-anchored on "
                         "its own worst row); narrow with --at")
    ap.add_argument("--apply", action="store_true",
                    help="WRITE the reconciliations back into the plan file "
                         "each segment came from. Requires --reconcile and "
                         "--at, because applying every proposal in the world "
                         "in one command is not a decision anybody made. "
                         "Only proposals whose every move is feasible at "
                         "relax 1.0 are written; nothing is sent to the "
                         "world -- `ribbon.py build --repair-of` does that.")
    ap.add_argument("--apply-relax", type=float, default=1.0,
                    help="the LARGEST relaxation of a segment's own declared "
                         "grade limit this apply is authorised to write, as "
                         "a factor on max(grade_limit, the gradient the "
                         "profile already walks). Default 1.0 = none. Above "
                         "1.0 requires --apply-reason and both are recorded "
                         "in the plan record: a relaxation is a RULING and "
                         "the number has to travel with it, exactly as "
                         "`--junction-accept` pins a waiver to its spread.")
    ap.add_argument("--apply-reason", default=None,
                    help="why the relaxation above is authorised")
    ap.add_argument("--at", default=None,
                    help="X,Z[,radius] -- reconcile only junctions whose "
                         "anchor is within radius (default 2 m) of this "
                         "point, so a named junction can be costed without "
                         "re-searching every taper in the world")
    ap.add_argument("--json", default=None)
    ap.add_argument("--top", type=int, default=12)
    a = ap.parse_args(argv)

    seg_paths = [Path(s) for s in a.segments]
    ledger = Path(a.ledger) if a.ledger else None

    if a.selftest:
        return selftest(seg_paths, ledger)

    if not seg_paths:
        ap.error("--segments is required (or --selftest)")

    accepted = json.loads(Path(a.accept).read_text()) if a.accept else {}
    ribbons, pads = load(seg_paths, ledger)
    rep = gate(ribbons, pads, accepted)

    if a.reconcile:
        want = None
        if a.at:
            bits = [float(v) for v in a.at.split(",")]
            want = (bits[0], bits[1], bits[2] if len(bits) > 2 else 2.0)
        props = []
        pad_labels = {q["label"] for q in pads}
        # THE CORRIDOR SUB-JUNCTIONS ARE PART OF THE POPULATION, not an
        # afterthought: a cluster flush at its anchor and impassable 24 m
        # down its shared corridor produced NO proposal at all, and
        # `ribbon.py`'s own refusal message sends the operator here with the
        # PAIR's coordinates -- which never matched a cluster anchor.  See
        # `corridor_junctions`.
        corridor = corridor_junctions(rep)
        rep["corridor_junctions"] = corridor
        for j in list(rep["junctions"]) + corridor:
            # TWO REASONS TO OFFER A PROPOSAL, because there are two shapes
            # of participant.
            #
            # A ROAD/ROAD junction is a POINT: one datum fixes the
            # disagreement AT the anchor and can do nothing about pairs
            # metres away down a shared corridor.  That is still true, and it
            # is why those pairs are RE-ANCHORED on themselves above rather
            # than costed against this anchor -- but it is not a reason to
            # stay silent about them.
            #
            # A PAD is a RECTANGLE.  MEASURED at the hall: the anchor
            # (-292.0, 214.0) is FLUSH -- T1 37.277, S1 37.100, pad datum
            # 37.100, a 0.177 m spread that passes -- while S1 dives across
            # the pad's own ground 6 to 8 m further on, 22 impassable pairs
            # of them.  Gating the offer on the anchor alone would decline to
            # cost the very conflict the operator named. A pad's datum has to
            # hold EVERYWHERE inside its rectangle, so any refused road/pad
            # pair in the cluster is a reconciliation this tool owes a
            # number for.
            pad_here = [q for q in j["participants"] if q in pad_labels]
            pad_refused = bool(pad_here) and any(
                r["impassable"] for r in rep["pairs"]
                if r["b"] in pad_here and r["a"] in j["participants"])
            if not (j["impassable_pairs_at_anchor"] or pad_refused):
                continue
            if want and math.hypot(j["at"][0] - want[0],
                                   j["at"][1] - want[1]) > want[2]:
                continue
            props.append(reconcile(j, ribbons, pads, keep_profiles=a.apply))
        rep["reconciliation"] = props

        if a.apply:
            if not a.at:
                raise SystemExit(
                    "--apply requires --at: applying every proposal in the "
                    "world in one command is not a decision anybody made.")
            # A RELAXATION IS A RULING, NOT AN EDIT, and the ceiling has to
            # be stated on the command line with a reason so the number
            # travels into the plan record.  `--apply-relax 1.0` (the
            # default) writes only proposals that need none.
            ceiling = float(a.apply_relax)
            if ceiling > 1.001 and not a.apply_reason:
                raise SystemExit(
                    f"--apply-relax {ceiling}x needs --apply-reason: a "
                    f"relaxation of a segment's own declared grade limit is "
                    f"a decision, and a decision with no reason in the "
                    f"record is indistinguishable from an accident.")
            moves = [(p, m) for p in props
                     for m in p["proposed"]["moves"]
                     if m.get("_profile") is not None]
            blocked = [p for p in props
                       if p["proposed"].get("worst_relax_factor") is None
                       or (p["proposed"]["worst_relax_factor"]) > ceiling + 1e-9]
            if blocked:
                raise SystemExit(
                    f"REFUSED: {len(blocked)} of {len(props)} proposal(s) "
                    f"need more than the {ceiling}x relaxation authorised -- "
                    f"worst "
                    f"{max(p['proposed'].get('worst_relax_factor') or 0.0 for p in blocked)}x "
                    f"on max(grade_limit, the gradient already walked). "
                    f"Re-run without --apply, read the cost, and decide; "
                    f"then authorise it explicitly with --apply-relax and "
                    f"--apply-reason.")
            by_file: dict[Path, dict] = {}
            touched: list[str] = []
            for p, m in moves:
                rib = next(r for r in ribbons if r["label"] == m["segment"])
                path = Path(rib["source"])
                doc = by_file.get(path)
                if doc is None:
                    doc = yaml.safe_load(path.read_text())
                    by_file[path] = doc
                seg = next(s for s in doc["segments"] if s["id"] == rib["id"])
                prof = list(seg["profile_y"])
                n = 0
                for k, station in enumerate(rib["station_index"]):
                    if abs(float(m["_profile"][k]) - prof[station]) > 1e-9:
                        prof[station] = round(float(m["_profile"][k]), 3)
                        n += 1
                seg["profile_y"] = prof
                seg.setdefault("reconciled", []).append({
                    "at": p["at"], "datum_y": p["proposed"]["datum_y"],
                    "spread_before_m": p["spread_m"],
                    "delta_m": m["delta_m"], "taper_m": m["taper_m"],
                    "stations_rewritten": n,
                    "grade_before": m.get("grade_before"),
                    "grade_after": m.get("grade_after"),
                    "relax_factor": m.get("relax_factor"),
                    "relax_authorised": ceiling,
                    "relax_reason": a.apply_reason,
                    "slide_limit": round(slide_allow(0.0), 4),
                    "zones_to_reemit": p["proposed"]["zone_list"],
                    "corridor_of": p.get("corridor_of"),
                    "instrument": "roads/junction.py::reconcile --apply",
                })
                touched.append(f"{m['segment']} {n} station(s) by "
                               f"{m['delta_m']:+.3f} m over {m['taper_m']} m")
            for path, doc in by_file.items():
                path.write_text(yaml.safe_dump(doc, sort_keys=True,
                                               default_flow_style=False))
                print(f"applied -> {path}")
            for line in touched:
                print(f"  {line}")
            if not touched:
                print("nothing to apply: every participant is already at the "
                      "proposed datum")

    if a.json:
        # `_profile` is a per-station height array kept only so `--apply`
        # can write the very taper it costed (re-deriving it from the
        # rounded report was MEASURED to drop a move).  It is not a finding
        # and it would triple this file, so it does not travel.
        for pr in rep.get("reconciliation") or ():
            for mv in pr["proposed"]["moves"]:
                mv.pop("_profile", None)
        Path(a.json).write_text(json.dumps(rep, indent=1, default=str))

    p = rep["population"]
    print(f"JUNCTION RECONCILER  verdict={rep['verdict']}")
    print(f"  population: {p['segments']} segments / {p['ribbon_runs']} ground "
          f"runs / {p['stations']} stations / {p['pads']} pads")
    t = rep["tolerance"]
    print(f"  tolerance:  refuse above tan({t['slide_deg']}) = "
          f"{t['slide_per_m']} per max(1.0 m, gap) -> "
          f"{t['tier1_at_overlap_m']} m at footprint overlap")
    s = rep["survey"]
    print(f"  survey:     {s['pairs_sharing_ground']} pairs share ground in "
          f"{s['junctions']} junctions; {s['flush_within_1cm']} flush to "
          f"1 cm; {s['over_budget_pairs']} over design budget; "
          f"{s['impassable_pairs']} IMPASSABLE")
    for r in rep["refusals"][:a.top]:
        print(f"  REFUSE {r['a']} vs {r['b']} at "
              f"({r['xz'][0]}, {r['xz'][1]}): {r['a_y']} vs {r['b_y']} = "
              f"{r['spread_m']} m over a {r['gap_m']} m gap, limit "
              f"{r['slide_allow_m']} m"
              # WHICH STATION IT WAS READ FROM.  Several stations can share
              # one evaluation point -- for a pad, every station outside the
              # rectangle projects onto the same edge point -- so without
              # this, identical-looking rows are indistinguishable and a
              # reader cannot tell three findings from one repeated.
              f" [{r['a']} station {r['a_station']} at "
              f"({r['a_station_xz'][0]}, {r['a_station_xz'][1]})]"
              + (f"  [{r['waiver_void']}]" if r.get("waiver_void") else ""))
    if len(rep["refusals"]) > a.top:
        print(f"  ... {len(rep['refusals']) - a.top} more refusals")
    for j in rep["junctions"][:a.top]:
        if not j["impassable_pairs"]:
            continue
        print(f"  JUNCTION {j['ways']}-way at {j['at']} spread "
              f"{j['spread_m']} m: {j['design_heights']}")
    for pr in rep.get("reconciliation", []):
        q = pr["proposed"]
        print(f"  PROPOSE {pr['ways']}-way at {pr['at']}: datum "
              f"{q['datum_y']}"
              + (f" FORCED by pad {pr['datum_forced_by_pad']}"
                 if pr["datum_forced_by_pad"] else "")
              + f", {q['regraded_arc_m']} m re-graded, "
                f"{q['zones_to_reemit']} zones, feasible={q['all_feasible']}")
        for m in q["moves"]:
            if "delta_m" in m:                    # a road moving to a datum
                print(f"      {m['segment']}: {m['delta_m']:+.3f} m over "
                      f"{m.get('taper_m', 0)} m taper, "
                      f"{m.get('stations_moved', 0)} stations, "
                      f"{m.get('zones', 0)} zones, grade "
                      f"{m.get('grade_before')} -> {m.get('grade_after')} "
                      f"(limit {m.get('grade_limit')}), "
                      f"feasible={m['feasible']}"
                      + (f"  {m.get('why', '')}" if not m["feasible"] else ""))
                continue
            # a road pinned to a pad's datum
            print(f"      {m.get('segment')}: hold pad datum "
                  f"{m.get('datum_y')} over {m.get('pinned_stations')} "
                  f"stations (worst shift {m.get('worst_pinned_shift_m')} m), "
                  f"feasible={m.get('feasible')}")
            for lg in m.get("legs", ()):
                print(f"         {lg['side']} station {lg['from_station']} "
                      f"-> {lg['to_station']}"
                      + (" FIXED" if lg["to_is_fixed"] else "")
                      + f": carry {lg['height_to_carry_m']} m over "
                        f"{lg['arc_available_m']} m, needs "
                        f"{lg['arc_needed_at_limit_m']} m at limit "
                        f"{lg['grade_limit']} -> grade "
                        f"{lg['grade_required']}, SHORTFALL "
                        f"{lg['arc_shortfall_m']} m of arc, "
                        f"feasible={lg['feasible']}")
                if lg["to_why_fixed"]:
                    print(f"            far end fixed by: "
                          f"{lg['to_why_fixed']}")
            if m.get("why"):
                print(f"         {m['why']}")
    return 1 if rep["verdict"] == "REFUSED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
