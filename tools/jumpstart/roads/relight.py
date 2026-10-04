#!/usr/bin/env python3
"""RELIGHT - move the lamps that are already standing in the wrong place.

`lamps.py` sites lamps on ground that has none.  This is the other half, and
it is the half a built world needs: a lamp that is ALREADY standing inside a
carriageway, or already closer to its neighbour than the biome allows, is not
fixed by a siting pass.  A siting pass is blind to it by construction - the
proximity gate sees the offender as "this ground is already lit" and places
nothing, so the defect is PRESERVED by the very mechanism that stops the pass
duplicating.  Re-running `lamps.py --place` over Ulfsland closes none of the
30 lighting refusals; it is a no-op over exactly the lamps that are wrong.

THE CLASS, not the 30.  Two rules decide whether a standing lamp is a defect,
and both are the acceptance gate's own:

  LATERAL.  `KeepOut.carriageway_why_not` - the TRUE PERPENDICULAR from the
    lamp (plus its own 0.12 m half footprint) to EVERY built centreline, over
    the whole polyline.  MEASURED on Ulfsland: 23 standing lamps are inside a
    carriageway, 19 in their own road and 4 in a neighbour's - the worst
    0.081 m from T5's centreline, placed by `lighting-T12`.  A per-segment
    rule cannot see that second class at all.

  SPACING.  The gate's `check_spacing` cylinder: REAL SEPARATION in xz with a
    10 m half-height, not arclength.  MEASURED: 7 pairs under 30 m survive
    the arclength rule, one of them cross-segment at 19.303 m.

WHAT THIS FILE DOES, AND WHAT IT REFUSES TO DO.  For each offender it
searches its own segment's arclength for the nearest position that is legal
under EVERY published rule at once - carriageway, pad, POI, protected piece,
foreign ground, seating, biome spacing - and emits `retire` + `spawn_plan`
keyed on prefab and the FULL 3D position.  Where no such position exists in
the search window the lamp is REFUSED WITH ITS NUMBERS and left standing: a
lamp deleted and not replaced is a dark road, which is a worse defect than
the one being closed, and this pass will not trade one for the other.

THE OFFSET IS SOLVED, NOT CONSTRUCTED.  This is the root cause `lamps.py`
names: `build()` steps `half_width + 0.55` along the unit normal of a
five-station chord and never checks the result.  `solve_point` below does the
same construction and then MEASURES it against the whole polyline, correcting
the step until the measurement agrees with the requirement or refusing the
station outright.  MEASURED over the 43,908 candidate positions the current
geometry can produce: 4,632 are illegal, 3,338 of them inside the lamp's own
road.  A constructed offset that is never read back is the defect; a measured
one cannot have it.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
for _p in (JUMPSTART, HERE, JUMPSTART / "ledger", JUMPSTART / "terraform",
           JUMPSTART / "blueprints", JUMPSTART / "settlements",
           JUMPSTART / "accept"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import lamps as L                                          # noqa: E402
import tcdata                                              # noqa: E402

# THE GATE'S OWN NUMBERS, IMPORTED AND NOT RESTATED.  A builder that carries
# its own copy of the acceptance threshold is a builder that can pass its own
# check and fail the world's; this module and `accept/sections/lighting.py`
# must refuse the same positions or one of them is lying.
from accept.sections import lighting as GATE                # noqa: E402

SCRATCH = Path("/tmp/roads/relight")

# How far along its own segment a lamp may be moved looking for a legal spot.
# DESIGN, and bounded on purpose: a lamp is a ROADSIDE object whose job is the
# stretch it stands on, so moving it 200 m to find room is not a repair, it is
# a different lighting plan.  60 m is two of the tightest biome's spacings, so
# a lamp can always step clear of one neighbour without landing on the next.
SEARCH_HALF_M = 60.0
# The arclength lattice the search walks.  0.5 m: half the 1 m lattice the
# terrain is written on, so no legal station is stepped over.
SEARCH_STEP_M = 0.5
# Margin above the gate's minimum.  The gate refuses `d < 30.0`; a replacement
# landing at 30.000000 would pass on this host and fail on one that rounds the
# other way, and a repair that depends on float luck is not a repair.
SEPARATION_MARGIN_M = 0.05
# How far the SOLVED offset may sit from the designed one before the station
# is refused.  Half the gate's own `DESIGNED_TOL_M`, so a position this pass
# accepts cannot be read as "standing further back" by the gate.
SOLVE_TOL_M = GATE.DESIGNED_TOL_M / 2.0


# ---------------------------------------------------------------------------
# geometry: the offset, measured
# ---------------------------------------------------------------------------
def station_at(seg: "L.Segment", s: float) -> tuple[float, float]:
    """`(x, z)` at arclength `s`, linear in arclength - the interpolation the
    terrain_write profile declares."""
    s = max(0.0, min(seg.length_m, s))
    k = 0
    while k + 2 < len(seg.nodes) and seg.cum[k + 1] < s:
        k += 1
    span = seg.cum[k + 1] - seg.cum[k]
    t = 0.0 if span <= 0 else (s - seg.cum[k]) / span
    x0, z0 = seg.nodes[k]
    x1, z1 = seg.nodes[k + 1]
    return (x0 + (x1 - x0) * t, z0 + (z1 - z0) * t)


def tangent_at(seg: "L.Segment", s: float, half_m: float = 2.0
               ) -> tuple[float, float]:
    ax, az = station_at(seg, s - half_m)
    bx, bz = station_at(seg, s + half_m)
    dx, dz = bx - ax, bz - az
    n = math.hypot(dx, dz)
    return (1.0, 0.0) if n == 0 else (dx / n, dz / n)


def solve_point(seg: "L.Segment", s: float, side: int, off: float,
                iters: int = 8) -> tuple[float, float] | None:
    """A point whose TRUE PERPENDICULAR to this whole polyline is `off`.

    The chord normal gives a first step; the step is then corrected by the
    MEASURED error until the measurement agrees, and the station is refused if
    it will not converge.  On the concave side of a bend tighter than `off`
    there IS no such point, and returning the unconverged construction is
    exactly the defect this file exists to close.
    """
    cx, cz = station_at(seg, s)
    tx, tz = tangent_at(seg, s)
    nx, nz = side * -tz, side * tx
    step = off
    for _ in range(iters):
        px, pz = cx + nx * step, cz + nz * step
        d = L.perpendicular_m(px, pz, seg.nodes)
        if abs(d - off) <= 1e-4:
            return (px, pz)
        if d <= 1e-6:
            return None
        step += (off - d)
        if step <= 0.0 or step > 4.0 * off:
            return None
    px, pz = cx + nx * step, cz + nz * step
    d = L.perpendicular_m(px, pz, seg.nodes)
    return (px, pz) if abs(d - off) <= SOLVE_TOL_M else None


def yaw_across(seg: "L.Segment", s: float, side: int) -> float:
    """Face the lamp across the road, the same cosmetic choice `lamps.py`
    makes and for the same measured reason: the light is a POINT light, so yaw
    changes nothing it lights."""
    tx, tz = tangent_at(seg, s)
    return math.degrees(math.atan2(side * -tz, side * tx)) % 360.0


def project_s(seg: "L.Segment", px: float, pz: float) -> float:
    """The arclength of the polyline point nearest `(px, pz)`."""
    best = (math.inf, 0.0)
    for i in range(len(seg.nodes) - 1):
        ax, az = seg.nodes[i]
        bx, bz = seg.nodes[i + 1]
        vx, vz = bx - ax, bz - az
        ll = vx * vx + vz * vz
        t = 0.0 if ll == 0 else max(0.0, min(1.0, ((px - ax) * vx
                                                   + (pz - az) * vz) / ll))
        d = math.hypot(px - (ax + t * vx), pz - (az + t * vz))
        if d < best[0]:
            best = (d, seg.cum[i] + t * math.hypot(vx, vz))
    return best[1]


# ---------------------------------------------------------------------------
# the population, and who is a defect
# ---------------------------------------------------------------------------
def census_from_tsv(path: Path, prefabs=GATE.LAMP_PREFABS) -> list[dict]:
    """The save's own ZDO census, as `accept` reads it.

    OFFLINE PLANNING ONLY, and it carries no ZDO ids: a write keys on prefab
    plus the full 3D position, and `--place` re-reads the LIVE world through
    `lamps.Standing` anyway.
    """
    out = []
    for line in Path(path).read_text().splitlines():
        if line.startswith("#"):
            continue
        p = line.split("\t")
        if len(p) < 5 or p[1] not in prefabs:
            continue
        out.append({"prefab": p[1], "x": float(p[2]), "y": float(p[3]),
                    "z": float(p[4]), "id": None, "source": "save_census"})
    return out


def in_corridor(lamp: dict, segs: dict) -> tuple[float, str, float] | None:
    """`(lateral, segment name, that segment's half width)` for the NEAREST
    built centreline, or None when the lamp is not beside a road.

    The gate's `classify_lamps` assignment, asked the same way: nearest
    centreline wins and `CORRIDOR_M` bounds the population, so this module and
    the gate cannot disagree about which lamps are even in scope.
    """
    best = None
    for s in segs.values():
        d = L.perpendicular_m(lamp["x"], lamp["z"], s.nodes)
        if best is None or d < best[0]:
            best = (d, s.short, s.half_width_m)
    if best is None or best[0] > GATE.CORRIDOR_M:
        return None
    return best


def is_designed(lat: float, hw: float) -> bool:
    return abs(lat - (hw + L.SHOULDER_STANDOFF_M)) <= GATE.DESIGNED_TOL_M


def lateral_offenders(pop: list[dict], keep: "L.KeepOut") -> list[dict]:
    """Every standing lamp inside a carriageway, worst first.

    Asked of `KeepOut.carriageway_why_not` - the builder's own keep-out - so a
    lamp this pass leaves standing is one the next siting pass would also
    accept.  Worst first because the tightest offender has the fewest legal
    neighbours and should choose its spot before the others crowd it out.
    """
    out = []
    for lamp in pop:
        why = keep.carriageway_why_not(lamp["x"], lamp["z"])
        if why is None:
            continue
        out.append({**lamp, "why": why,
                    "lateral_m": min(L.perpendicular_m(lamp["x"], lamp["z"],
                                                       c[1])
                                     for c in keep.carriageways)})
    out.sort(key=lambda r: r["lateral_m"])
    return out


def spacing_pairs(pop: list[dict], segs: dict,
                  min_m: float = GATE.MIN_SPACING_M) -> list[dict]:
    """Every pair the gate's cylinder refuses, tightest first.

    `check_spacing` is the gate's own function over the gate's own
    `classify_lamps` rows, so the pairs this returns are exactly the pairs the
    gate reports - not a restatement that can drift from it.
    """
    corridor = GATE.classify_lamps(
        [(l["prefab"], l["x"], l["y"], l["z"]) for l in pop], _gate_segs(segs))
    findings, _stats = GATE.check_spacing(corridor)
    out = []
    for f in findings:
        n = f.numbers
        out.append({"gap_m": n["gap_m"], "dy_m": n["dy_m"],
                    "seg_a": n["seg_a"], "seg_b": n["seg_b"],
                    "cross_segment": n["cross_segment"],
                    "where": f.where, "limit_m": min_m})
    return out


def _gate_segs(segs: dict) -> dict:
    """`lamps.Segment` objects in the shape `accept.sections.lighting` reads.

    One conversion in one place rather than two loaders of the same ledger
    records: two readers is how two agents measure two different road networks
    and are both right about their own.
    """
    return {s.name: {"name": s.name, "half_width_m": s.half_width_m,
                     "nodes": s.nodes} for s in segs.values()}


# ---------------------------------------------------------------------------
# the search
# ---------------------------------------------------------------------------
class Sited:
    """One re-site decision, accepted or refused, with its numbers."""

    def __init__(self, old: dict, seg_short: str):
        self.old = old
        self.seg = seg_short
        self.new: dict | None = None
        self.tried = 0
        self.tier = 1
        self.causes: dict[str, int] = {}
        self.reason = ""

    def refuse(self, why: str) -> None:
        self.causes[why] = self.causes.get(why, 0) + 1

    def as_dict(self) -> dict:
        return {"segment": self.seg, "old": self.old, "new": self.new,
                "candidates_tried": self.tried, "tier": self.tier,
                "refusal_causes": dict(sorted(self.causes.items(),
                                              key=lambda kv: -kv[1])[:8]),
                "reason": self.reason}


def nearest_lamp_m(x: float, z: float, y: float,
                   others: list[dict]) -> float:
    """Distance to the nearest other lamp IN THE CYLINDER, or infinity.

    xz distance with a 10 m half-height, because a lamp on the road and a lamp
    on the clifftop 12 m above it are not a cluster from either one.
    """
    best = math.inf
    for o in others:
        if abs(o["y"] - y) > GATE.SPACING_Y_HALF_M:
            continue
        best = min(best, math.hypot(o["x"] - x, o["z"] - z))
    return best


# ---- THE TWO TIERS, AND WHY A SECOND ONE IS NOT A LOOPHOLE ----------------
#
# `lamps.py`'s siting rule is STRICTER than the acceptance gate's, on purpose:
# it keeps the biome minimum against EVERY standing torch, including the
# `piece_groundtorch_wood` inside a farmhouse 11 m back, because a torch in a
# house does light the road.  That is the right rule for choosing where to put
# a lamp on dark ground.
#
# IT IS THE WRONG RULE FOR MOVING ONE, and MEASURED on Ulfsland it is wrong in
# a way that costs the world: around the temple, five segments meet inside a
# settlement whose props are torches, and 190 to 387 of every 482 candidates
# are refused by proximity to one of them.  Applying it alone leaves 14 lamps
# STANDING IN CARRIAGEWAYS - obstacles - to protect a spacing rule the gate
# does not even ask about there, because the gate's spacing population is
# corridor lamps and a house torch 11 m back is not one.
#
# So the search runs twice.  TIER 1 is the builder's own rule, unchanged, and
# it is always tried first.  TIER 2 is the ACCEPTANCE RULE PLUS A
# NON-REGRESSION FLOOR: at least the gate's 30 m from every CORRIDOR lamp, and
# no closer to any non-corridor torch than the lamp ALREADY IS TODAY.  A tier-2
# move therefore cannot create a refusal and cannot make any measured quantity
# worse; it trades a spacing preference the gate does not assert for the
# removal of an obstacle the gate does.  Every tier-2 move is labelled as one
# in the report, because a weaker rule applied silently is how a gate gets
# talked down.
TIER_POLICY = 1
TIER_GATE_NO_REGRESSION = 2


def separation_ok(x: float, z: float, y: float, others: list[dict],
                  min_m: float) -> str | None:
    """The gate's CYLINDER, asked of every other standing lamp."""
    for o in others:
        if abs(o["y"] - y) > GATE.SPACING_Y_HALF_M:
            continue
        d = math.hypot(o["x"] - x, o["z"] - z)
        if d < min_m:
            return (f"{d:.3f} m from a standing {o['prefab']} at "
                    f"({o['x']:.1f}, {o['y']:.1f}, {o['z']:.1f}), under the "
                    f"{min_m:.2f} m minimum")
    return None


def resite_one(lamp: dict, seg: "L.Segment", ground: "L.Ground",
               keep: "L.KeepOut", others: list[dict],
               extra_min_m: float = 0.0,
               tier: int = TIER_POLICY,
               corridor_ids: set | None = None) -> Sited:
    """The nearest legal position on this lamp's own segment, or a refusal.

    NEAREST, and that is the whole ordering: the lamp is already doing a job -
    it lights the stretch it stands on - so the repair that changes the least
    about the night walk is the one that moves it least.  A global re-plan
    would move every lamp on the segment to close one defect.
    """
    out = Sited(lamp, seg.short)
    out.tier = tier
    s0 = project_s(seg, lamp["x"], lamp["z"])
    off = seg.half_width_m + L.SHOULDER_STANDOFF_M
    steps = int(SEARCH_HALF_M / SEARCH_STEP_M)
    order: list[float] = [0.0]
    for k in range(1, steps + 1):
        order += [k * SEARCH_STEP_M, -k * SEARCH_STEP_M]
    # The two populations the tiers separate, and the floor tier 2 may not
    # go under: what this lamp is ALREADY doing today.  Measured from the
    # lamp's own standing position, so "no worse" is a number and not a claim.
    cor_ids = corridor_ids if corridor_ids is not None else set()
    corridor_others = [o for o in others if id(o) in cor_ids]
    other_others = [o for o in others if id(o) not in cor_ids]
    today_nc = nearest_lamp_m(lamp["x"], lamp["z"], lamp["y"], other_others)
    best = None
    for ds in order:
        s = s0 + ds
        if s < 0.0 or s > seg.length_m:
            continue
        # BOTH SIDES ARE EVALUATED AT EVERY ARCLENGTH BEFORE EITHER IS TAKEN.
        # `solve_point` corrects its step against the MEASURED perpendicular,
        # and where the polyline doubles back that correction legitimately
        # walks the point past the far leg - the answer is still exactly
        # `off` from the road, but it can be metres from the station it was
        # derived from.  Taking the first legal side would then hand back the
        # far shoulder when the near one was free, so the two are measured and
        # the SHORTER MOVE wins.
        here = []
        for side in (+1, -1):
            out.tried += 1
            p = solve_point(seg, s, side, off)
            if p is None:
                out.refuse("no point at the designed offset (bend tighter "
                           "than the offset, or the polyline doubles back)")
                continue
            x, z = p
            bad = keep.why_not(x, z)
            if bad:
                out.refuse(bad.split(" at ")[0].split(" of ")[0][:70])
                continue
            sy, relief = L.seat_surface(ground, x, z)
            if sy is None:
                out.refuse("footprint reaches ground with no measured surface")
                continue
            if sy <= L.WATER_LEVEL_M + L.FREEBOARD_M:
                out.refuse("surface at or under the water plane + freeboard")
                continue
            if relief > L.FOOTPRINT_RELIEF_MAX_M:
                out.refuse("ground rises and falls across the lamp's own "
                           "footprint, over the limit")
                continue
            biome = ground.biome(x, z)
            pol = L.BIOME_POLICY.get(biome, L.DEFAULT_POLICY)
            if pol is None:
                out.refuse(f"biome {biome} has no published spacing policy")
                continue
            min_m = max(float(pol["min_m"]), GATE.MIN_SPACING_M,
                        extra_min_m) + SEPARATION_MARGIN_M
            y = sy + L.seat_offset_m()
            if tier == TIER_POLICY:
                sep = separation_ok(x, z, y, others, min_m)
                if sep:
                    out.refuse("under the biome spacing minimum from a "
                               "standing lamp")
                    continue
                nc_floor = min_m
            else:
                sep = separation_ok(x, z, y, corridor_others,
                                    max(GATE.MIN_SPACING_M, extra_min_m)
                                    + SEPARATION_MARGIN_M)
                if sep:
                    out.refuse("under the gate's 30 m minimum from a "
                               "corridor lamp")
                    continue
                # NON-REGRESSION, MEASURED: no closer to a torch outside the
                # gate's corridor population than this lamp already stands.
                nc_floor = min(min_m, today_nc)
                got = nearest_lamp_m(x, z, y, other_others)
                if got < nc_floor:
                    out.refuse(f"closer to a non-corridor torch than the lamp "
                               f"already stands ({got:.2f} m against "
                               f"{nc_floor:.2f} m today)")
                    continue
            unit = L.lamp_unit(x, z, sy, yaw_across(seg, s, side))
            # THE REPLACEMENT IS THE SAME PREFAB THAT WAS STANDING THERE.
            # `lamp_unit` names the road pass's own `piece_groundtorch`, and
            # four of the offenders are not that: one is a harbour prop, some
            # are `_wood`.  Re-siting a lamp is a MOVE; silently swapping a
            # harbour's torch for a road marker would be a different building
            # decision taken under cover of a repair.
            unit["prefab"] = lamp["prefab"]
            unit.update(biome=biome, side=side, s_m=round(s, 2),
                        moved_m=round(math.hypot(x - lamp["x"],
                                                 z - lamp["z"]), 3),
                        along_m=round(ds, 2), relief_m=round(relief, 4),
                        min_separation_m=round(min_m, 3),
                        tier=tier,
                        nearest_corridor_lamp_m=round(
                            nearest_lamp_m(x, z, y, corridor_others), 3)
                        if corridor_others else None,
                        nearest_other_torch_m=round(
                            nearest_lamp_m(x, z, y, other_others), 3)
                        if other_others else None,
                        nearest_other_torch_today_m=(round(today_nc, 3)
                                                     if today_nc < math.inf
                                                     else None),
                        lateral_m=round(L.perpendicular_m(x, z, seg.nodes), 4),
                        designed_offset_m=round(off, 3))
            here.append(unit)
        if here:
            best = min(here, key=lambda u: u["moved_m"])
            break
    if best is None:
        out.reason = (f"no legal position within +/-{SEARCH_HALF_M:.0f} m of "
                      f"s={s0:.1f} on {seg.short}, over {out.tried} "
                      f"candidates (tier {tier})")
    else:
        out.new = best
        out.reason = (f"tier {tier}: moved {best['moved_m']:.3f} m "
                      f"({best['along_m']:+.1f} m along {seg.short}, side "
                      f"{best['side']:+d}) to the designed offset "
                      f"{best['designed_offset_m']:.2f} m, measured lateral "
                      f"{best['lateral_m']:.4f} m; nearest corridor lamp "
                      f"{best['nearest_corridor_lamp_m']} m, nearest other "
                      f"torch {best['nearest_other_torch_m']} m against "
                      f"{best['nearest_other_torch_today_m']} m today")
    return out


# ---------------------------------------------------------------------------
# the campaign
# ---------------------------------------------------------------------------
def plan(pop: list[dict], segs: dict, keep: "L.KeepOut",
         grounds: dict, scope: set | None = None, limit: int = 0) -> dict:
    """Close every lighting refusal this population has, or name what is left.

    LATERAL FIRST, THEN SPACING, AND SPACING RE-MEASURED AFTER EVERY MOVE.  An
    in-carriageway lamp that is also half of a tight pair is ONE defect with
    two symptoms, and fixing the lateral often closes the pair for free -
    MEASURED on T4, where the 27.259 m pair and the 0.314 m lateral are the
    same lamp.  Re-measuring after each move is what keeps a repair from
    creating the next refusal.
    """
    work = [dict(l) for l in pop]
    decisions: list[dict] = []
    moved: list[dict] = []
    # SCOPE BOUNDS WHAT IS WRITTEN, NEVER WHAT IS MEASURED.  The population
    # is always the whole network, because a neighbour's lamp decides this
    # one's spacing and a scope-shaped census is the per-segment blindness
    # this file exists to close.  `scope` only decides which offenders this
    # RUN is allowed to move; the rest are reported as out of scope, with
    # their numbers, so a one-lamp test cannot read as a clean world.
    in_scope = (lambda short: scope is None or short in scope)

    # WHICH LAMPS THE GATE'S SPACING RULE EVEN LOOKS AT.  `check_spacing`
    # reads `classify_lamps`, which drops anything further than CORRIDOR_M
    # from every centreline - so a farmhouse torch 20 m back cannot be half
    # of a gate refusal, and tier 2 is allowed to stand nearer to it than the
    # builder's own preference, but never nearer than it already does.
    corridor_ids = {id(l) for l in work if in_corridor(l, segs)}

    def others_for(target: dict) -> list[dict]:
        return [l for l in work if l is not target]

    def try_both_tiers(target, seg, others, extra_min_m: float = 0.0):
        """Tier 1 first, ALWAYS.  Tier 2 only where tier 1 found nothing."""
        got = resite_one(target, seg, grounds[seg.short], keep, others,
                         extra_min_m=extra_min_m, tier=TIER_POLICY,
                         corridor_ids=corridor_ids)
        if got.new is not None:
            return got
        second = resite_one(target, seg, grounds[seg.short], keep, others,
                            extra_min_m=extra_min_m,
                            tier=TIER_GATE_NO_REGRESSION,
                            corridor_ids=corridor_ids)
        if second.new is not None:
            for why, n in got.causes.items():
                second.causes.setdefault(f"tier1: {why}", n)
            return second
        for why, n in second.causes.items():
            got.causes.setdefault(f"tier2: {why}", n)
        return got

    def budget_spent() -> bool:
        return bool(limit) and len(moved) >= limit
    for off in lateral_offenders(work, keep):
        target = next(l for l in work
                      if l["x"] == off["x"] and l["z"] == off["z"]
                      and l["prefab"] == off["prefab"])
        cor = in_corridor(target, segs)
        seg = segs[cor[1]] if cor else None
        if seg is None:
            decisions.append({"kind": "lateral", "segment": None,
                              "old": target, "new": None,
                              "reason": "the lamp is inside a carriageway but "
                                        "further than the corridor bound from "
                                        "every centreline, which cannot "
                                        "happen; refusing rather than "
                                        "guessing a segment"})
            continue
        if not in_scope(seg.short) or budget_spent():
            decisions.append({
                "kind": "lateral", "segment": seg.short, "old": target,
                "new": None, "was": off["why"],
                "reason": ("OUT OF SCOPE for this run - measured and "
                           "reported, not written")})
            continue
        got = try_both_tiers(target, seg, others_for(target))
        d = got.as_dict()
        d["kind"] = "lateral"
        d["was"] = off["why"]
        decisions.append(d)
        if got.new:
            work.remove(target)
            fresh = {"prefab": target["prefab"], "x": got.new["x"],
                     "y": got.new["y"], "z": got.new["z"],
                     "id": None, "source": "resited"}
            work.append(fresh)
            corridor_ids.discard(id(target))
            corridor_ids.add(id(fresh))
            moved.append(d)

    # Spacing, re-measured over the population the lateral moves produced.
    # A pair this run cannot close does not stop the loop: it is recorded
    # with its numbers and SKIPPED, because one unfixable pair hiding the
    # four behind it is the report that sends a builder to close a class he
    # can only see a fifth of.
    guard = 0
    blocked: set = set()
    while True:
        guard += 1
        if guard > 60:
            decisions.append({"kind": "spacing", "reason": "the spacing loop "
                              "hit its own iteration guard"})
            break
        pairs = [p for p in spacing_pairs(work, segs)
                 if _pair_key(p) not in blocked]
        if not pairs:
            break
        pair = pairs[0]
        cands = _pair_members(work, pair)
        if not cands:
            decisions.append({"kind": "spacing", "pair": pair,
                              "reason": "the pair's members could not be "
                                        "recovered from the population"})
            blocked.add(_pair_key(pair))
            continue
        done = False
        skipped = 0
        for target in cands:
            cor = in_corridor(target, segs)
            if cor is None:
                continue
            seg = segs[cor[1]]
            if seg.short not in grounds:
                skipped += 1
                continue
            if not in_scope(seg.short) or budget_spent():
                skipped += 1
                continue
            others = [l for l in work if l is not target]
            got = try_both_tiers(target, seg, others)
            d = got.as_dict()
            d["kind"] = "spacing"
            d["was"] = (f"{pair['gap_m']:.3f} m from its neighbour "
                        f"({pair['seg_a']} / {pair['seg_b']}), under the "
                        f"{pair['limit_m']:.0f} m minimum")
            decisions.append(d)
            if got.new:
                work.remove(target)
                fresh = {"prefab": target["prefab"], "x": got.new["x"],
                         "y": got.new["y"], "z": got.new["z"],
                         "id": None, "source": "resited"}
                work.append(fresh)
                corridor_ids.discard(id(target))
                corridor_ids.add(id(fresh))
                moved.append(d)
                done = True
                break
        if not done:
            decisions.append({
                "kind": "spacing", "pair": pair,
                "reason": (f"OUT OF SCOPE for this run ({skipped} of "
                           f"{len(cands)} members skipped) - measured and "
                           f"reported, not written"
                           if skipped else
                           "neither member of this pair has a legal "
                           "position; REFUSED with its numbers rather than "
                           "deleted")})
            blocked.add(_pair_key(pair))

    corridor = GATE.classify_lamps(
        [(l["prefab"], l["x"], l["y"], l["z"]) for l in work],
        _gate_segs(segs))
    lat_after = GATE.check_lateral(corridor)
    sp_after, spstats = GATE.check_spacing(corridor)
    return {"decisions": decisions, "moved": len(moved),
            "population": len(work),
            "projected": {"lamp_lateral": len(lat_after),
                          "lamp_spacing": len(sp_after), "spacing": spstats},
            "projected_refusals": [
                {"check": f.check, "message": f.message, "numbers": f.numbers}
                for f in lat_after + sp_after]}


def _pair_key(pair: dict) -> tuple:
    """A stable identity for a spacing pair, so one this run cannot close is
    skipped ONCE rather than re-tried forever.  Keyed on the reported
    position and the measured gap, both rounded to the millimetre the gate
    itself reports."""
    x, y, z = pair["where"]
    return (round(x, 3), round(y, 3), round(z, 3), round(pair["gap_m"], 3))


def _pair_members(work: list[dict], pair: dict) -> list[dict]:
    """The two lamps a spacing finding names, MOVABLE ONE FIRST.

    `where` carries only the first member, so the second is recovered by the
    measured gap.  Order matters: a lamp that is somebody else's prop - a
    harbour torch, a house lamp - is moved only when the road's own lamp has
    nowhere to go, because a road repair that rearranges a harbour is scope
    this pass was not given.
    """
    ax, ay, az = pair["where"]
    a = _at(work, ax, ay, az)
    b = None
    if a is not None:
        for l in work:
            if l is a:
                continue
            if abs(l["y"] - ay) > GATE.SPACING_Y_HALF_M:
                continue
            if abs(math.hypot(l["x"] - ax, l["z"] - az)
                   - pair["gap_m"]) <= 0.002:
                b = l
                break
    return [l for l in (a, b) if l is not None]


def _at(work: list[dict], x: float, y: float, z: float,
        tol: float = 0.01) -> dict | None:
    for l in work:
        if (abs(l["x"] - x) <= tol and abs(l["y"] - y) <= tol
                and abs(l["z"] - z) <= tol):
            return l
    return None


# ---------------------------------------------------------------------------
# zones
# ---------------------------------------------------------------------------
def zones_around(points: list[tuple[float, float]],
                 pad_m: float = SEARCH_HALF_M + 20.0) -> list[tuple[int, int]]:
    """Every zone within `pad_m` of any of these points, plus the ring.

    The ring is not slack: the lattice's boundary row is SHARED between two
    zones, so a sample on a boundary lives in both and `zone_of` picks one.
    `applied.surface_at` returns None for a missing patch, and None reads as
    "no ground here", which is the answer that quietly refuses a legal spot.
    """
    zs: set[tuple[int, int]] = set()
    step = 32.0
    for x, z in points:
        k = int(pad_m / step) + 1
        for i in range(-k, k + 1):
            for j in range(-k, k + 1):
                zs.add(tcdata.zone_of(x + i * step, z + j * step))
    ring = set(zs)
    for zx, zz in zs:
        for dzx in (-1, 0, 1):
            for dzz in (-1, 0, 1):
                ring.add((zx + dzx, zz + dzz))
    return sorted(ring)


# ---------------------------------------------------------------------------
# THE SELFTEST - both directions, no world and no server
# ---------------------------------------------------------------------------
def _seg(nodes, hw=3.0, short="X") -> "L.Segment":
    cum = [0.0]
    for k in range(1, len(nodes)):
        cum.append(cum[-1] + math.dist(nodes[k - 1], nodes[k]))
    return L.Segment(name=f"road_{short}_a_b", short=short, file_line=0,
                     seq=0, half_width_m=hw, nodes=list(nodes), cum=cum,
                     length_m=cum[-1])


def _case_straight_offset_is_exact() -> list[str]:
    """On a straight road the construction is already right, and the solver
    must not move it."""
    seg = _seg([(0.0, 0.0), (200.0, 0.0)])
    p = solve_point(seg, 100.0, +1, 3.55)
    bad = []
    if p is None:
        return ["solver refused a straight road"]
    if abs(L.perpendicular_m(p[0], p[1], seg.nodes) - 3.55) > 1e-6:
        bad.append(f"straight road solved to {p}")
    return bad


def _case_bend_construction_is_wrong_and_solver_fixes_it() -> list[str]:
    """THE ROOT CAUSE, ASSERTED.  A 90 degree corner: the chord normal at the
    apex points outward along the bisector, and stepping 3.55 m along it lands
    2.51 m from the polyline - INSIDE a 3.0 m carriageway.  The case fails if
    the constructed point is legal (it is not) or if the solved one is not.
    """
    seg = _seg([(0.0, 0.0), (50.0, 0.0), (50.0, 50.0)])
    s = project_s(seg, 50.0, 0.0)
    cx, cz = station_at(seg, s)
    tx, tz = tangent_at(seg, s)
    nx, nz = -tz, tx
    constructed = (cx + nx * 3.55, cz + nz * 3.55)
    d_bad = L.perpendicular_m(constructed[0], constructed[1], seg.nodes)
    bad = []
    if d_bad >= 3.0:
        bad.append(f"the constructed offset at the apex measured {d_bad:.3f} "
                   f"m, which is not inside the carriageway - this case no "
                   f"longer reproduces the defect it exists to prove")
    p = solve_point(seg, s, -1, 3.55)
    if p is None:
        bad.append("the solver refused the convex side of a 90 degree bend, "
                   "where a legal point plainly exists")
    elif abs(L.perpendicular_m(p[0], p[1], seg.nodes) - 3.55) > SOLVE_TOL_M:
        bad.append(f"the solver returned {p} at "
                   f"{L.perpendicular_m(p[0], p[1], seg.nodes):.4f} m")
    return bad


def _case_solver_never_returns_an_unverified_point() -> list[str]:
    """THE INVARIANT, OVER A POPULATION, IN BOTH DIRECTIONS.

    Three polylines a road can actually be - a hairpin whose legs are 4 m
    apart, a 90 degree corner, and a tight arc - swept at every 1 m station on
    both sides.  Two things must hold at once and neither alone is the point:

      1.  THE DEFECT REPRODUCES.  The CONSTRUCTION - step `off` along the
          chord normal, which is exactly what `lamps.py::build` does - must
          still land inside the carriageway on a measurable fraction of these
          stations.  If it stops doing so this case is no longer testing
          anything and says so rather than passing quietly.
      2.  THE SOLVER IS NEVER WRONG.  Every point `solve_point` RETURNS must
          measure `off` from the whole polyline within `SOLVE_TOL_M`.  A
          returned point that does not is the original defect wearing a
          correction loop, which is the one outcome worse than not trying.
    """
    shapes = [[(0.0, 0.0), (60.0, 0.0), (60.0, 4.0), (0.0, 4.0)],
              [(0.0, 0.0), (50.0, 0.0), (50.0, 50.0)],
              [(0.0, 0.0), (10.0, 2.0), (18.0, 8.0), (22.0, 18.0),
               (20.0, 28.0), (12.0, 34.0)]]
    off, hw = 3.55, 3.0
    bad, constructed_illegal, total, returned = [], 0, 0, 0
    for nodes in shapes:
        seg = _seg(nodes, hw=hw)
        s = 0.0
        while s <= seg.length_m:
            cx, cz = station_at(seg, s)
            tx, tz = tangent_at(seg, s)
            for side in (+1, -1):
                total += 1
                nx, nz = side * -tz, side * tx
                if L.perpendicular_m(cx + nx * off, cz + nz * off,
                                     nodes) < hw:
                    constructed_illegal += 1
                p = solve_point(seg, s, side, off)
                if p is None:
                    continue
                returned += 1
                d = L.perpendicular_m(p[0], p[1], nodes)
                if abs(d - off) > SOLVE_TOL_M:
                    bad.append(f"solver returned a point {d:.4f} m from the "
                               f"polyline when {off:.2f} m was required "
                               f"(shape {nodes[:2]}, s={s:.1f}, side {side})")
            s += 1.0
    if constructed_illegal == 0:
        bad.append(f"the chord-normal CONSTRUCTION produced no illegal point "
                   f"over {total} stations, so this case no longer "
                   f"reproduces the defect it exists to prove")
    if returned == 0:
        bad.append("the solver returned nothing at all over "
                   f"{total} stations")
    return bad[:6]


def _case_carriageway_offender_is_seen() -> list[str]:
    """The MEASURED defect: a lamp 0.081 m from a centreline is an offender,
    and one on the shoulder is not."""
    segs = {"X": _seg([(0.0, 0.0), (200.0, 0.0)])}
    keep = _FakeKeep(segs)
    pop = [{"prefab": "piece_groundtorch", "x": 50.0, "y": 32.0, "z": 0.081},
           {"prefab": "piece_groundtorch", "x": 90.0, "y": 32.0, "z": 3.55}]
    got = lateral_offenders(pop, keep)
    bad = []
    if len(got) != 1:
        bad.append(f"expected exactly 1 in-carriageway lamp, got {len(got)}")
    elif abs(got[0]["z"] - 0.081) > 1e-9:
        bad.append("the wrong lamp was named the offender")
    return bad


def _case_neighbours_carriageway_is_seen() -> list[str]:
    """THE CASE A PER-SEGMENT RULE CANNOT FAIL.  A lamp at T12's own designed
    offset, standing in T5's lane, MEASURED on Ulfsland at 3.550 m from its
    own segment and 0.081 m from the neighbour's."""
    segs = {"A": _seg([(0.0, 0.0), (200.0, 0.0)], short="A"),
            "B": _seg([(0.0, 3.631), (200.0, 3.631)], short="B")}
    keep = _FakeKeep(segs)
    lamp = {"prefab": "piece_groundtorch", "x": 100.0, "y": 32.0, "z": 3.55}
    own = L.perpendicular_m(lamp["x"], lamp["z"], segs["A"].nodes)
    other = L.perpendicular_m(lamp["x"], lamp["z"], segs["B"].nodes)
    bad = []
    if abs(own - 3.55) > 1e-6:
        bad.append(f"fixture broken: own offset {own}")
    if other >= 3.0:
        bad.append(f"fixture broken: neighbour offset {other}")
    if not lateral_offenders([lamp], keep):
        bad.append("a lamp at its OWN designed offset standing 0.081 m from "
                   "the NEIGHBOUR's centreline was accepted - this is the "
                   "4-of-23 class and a per-segment rule is blind to it")
    return bad


def _case_spacing_uses_separation_not_arclength() -> list[str]:
    """MEASURED on Ulfsland: N1 and N2 sit 29.9995 m apart from an arclength
    of EXACTLY 30.0, on opposite shoulders of a bend.  The pair must refuse."""
    segs = {"X": _seg([(0.0, 0.0), (200.0, 0.0)])}
    a = {"prefab": "piece_groundtorch", "x": 100.0, "y": 32.0, "z": 3.55}
    b = {"prefab": "piece_groundtorch", "x": 129.99949, "y": 32.0, "z": 3.55}
    bad = []
    if not spacing_pairs([a, b], segs):
        bad.append("29.9995 m was accepted against a 30 m minimum")
    c = {"prefab": "piece_groundtorch", "x": 130.0, "y": 32.0, "z": 3.55}
    if spacing_pairs([a, c], segs):
        bad.append("exactly 30.000 m was refused; the minimum is a minimum")
    return bad


def _case_spacing_is_a_cylinder() -> list[str]:
    """A lamp 12 m above the road is not a cluster with the lamp below it."""
    segs = {"X": _seg([(0.0, 0.0), (200.0, 0.0)])}
    a = {"prefab": "piece_groundtorch", "x": 100.0, "y": 32.0, "z": 3.55}
    b = {"prefab": "piece_groundtorch", "x": 105.0, "y": 44.5, "z": 3.55}
    return ([] if not spacing_pairs([a, b], segs)
            else ["a lamp 12.5 m above the road was called a cluster with "
                  "the one below it"])


def _case_resite_moves_the_minimum() -> list[str]:
    """A lamp in the middle of a straight lane moves to the shoulder beside
    itself, not to the far end of the segment."""
    seg = _seg([(0.0, 0.0), (400.0, 0.0)])
    segs = {"X": seg}
    keep = _FakeKeep(segs)
    lamp = {"prefab": "piece_groundtorch", "x": 200.0, "y": 32.0, "z": 0.0}
    got = resite_one(lamp, seg, _FlatGround(), keep, [])
    bad = []
    if got.new is None:
        return [f"no legal spot on an empty straight road: {got.reason}"]
    if abs(got.new["along_m"]) > 1e-9:
        bad.append(f"moved {got.new['along_m']} m along a road with no reason "
                   f"to move at all")
    if abs(abs(got.new["z"]) - 3.55) > SOLVE_TOL_M:
        bad.append(f"landed at z={got.new['z']}, not the designed offset")
    return bad


def _case_resite_respects_a_standing_neighbour() -> list[str]:
    """THE STATE THIS PASS MUST NOT CREATE.  A lamp in the lane with a legal
    lamp 10 m along it must not be re-sited 10 m from that neighbour."""
    seg = _seg([(0.0, 0.0), (400.0, 0.0)])
    segs = {"X": seg}
    keep = _FakeKeep(segs)
    lamp = {"prefab": "piece_groundtorch", "x": 200.0, "y": 32.0, "z": 0.0}
    nb = {"prefab": "piece_groundtorch", "x": 210.0, "y": 32.5, "z": 3.55}
    got = resite_one(lamp, seg, _FlatGround(), keep, [nb])
    if got.new is None:
        return [f"refused where a legal spot exists 30 m away: {got.reason}"]
    d = math.hypot(got.new["x"] - nb["x"], got.new["z"] - nb["z"])
    return ([] if d >= GATE.MIN_SPACING_M
            else [f"re-sited {d:.3f} m from a standing lamp, under the "
                  f"{GATE.MIN_SPACING_M:.0f} m minimum"])


def _case_refuses_rather_than_deletes() -> list[str]:
    """A lamp on a 20 m stub with a legal lamp at each end has nowhere to go.
    The pass must REFUSE it and leave it standing: a deleted lamp that is not
    replaced is a dark road, which is a worse defect than the one closed."""
    seg = _seg([(0.0, 0.0), (20.0, 0.0)])
    segs = {"X": seg}
    keep = _FakeKeep(segs)
    lamp = {"prefab": "piece_groundtorch", "x": 10.0, "y": 32.0, "z": 0.0}
    walls = [{"prefab": "piece_groundtorch", "x": 10.0, "y": 32.0, "z": 12.0},
             {"prefab": "piece_groundtorch", "x": 10.0, "y": 32.0, "z": -12.0}]
    got = resite_one(lamp, seg, _FlatGround(), keep, walls)
    return ([] if got.new is None
            else [f"accepted {got.new} where every candidate is inside "
                  f"12 m of a standing lamp"])


class _FakeKeep:
    """A keep-out with ONLY the carriageway rule, for the selftest.

    The other members - pads, POI discs, protected pieces, foreign ground -
    each need a ledger and a world.  This one carries the rule the cases are
    about and is honest about carrying nothing else.
    """

    def __init__(self, segs: dict):
        self.carriageways = [(s.name, s.nodes, s.half_width_m)
                             for s in segs.values()]

    carriageway_why_not = L.KeepOut.carriageway_why_not

    def why_not(self, x: float, z: float) -> str | None:
        return self.carriageway_why_not(x, z)


class _FlatGround:
    """Flat Meadows at y 32, for the selftest."""

    def surface(self, x: float, z: float) -> float | None:
        return 32.0

    def biome(self, x: float, z: float) -> str:
        return "Meadows"


# ---------------------------------------------------------------------------
# NEUTERS - how a case proves it is still LOAD-BEARING
# ---------------------------------------------------------------------------
# A case that passes proves the code is right TODAY.  It does not prove the
# case is what holds the fix in place: a fixture can quietly stop describing
# the defect and then it passes for the wrong reason, silently, forever.
# MEASURED on 2026-09-21, three cases in two sections of the acceptance gate
# had already done exactly that and all three were found by accident.
#
# So each neuter RESTORES ITS DEFECT VERBATIM, returns its own undo, and the
# harness requires ITS case to come out the OTHER way while every sibling
# still passes.  "The other way" is a non-empty problem list OR a raise -
# a case whose fixture invariant fires IS a case that came out the other way,
# and demanding a flipped verdict would exclude every case that asserts on an
# instrument's output rather than on findings.
def _neuter_the_offset_to_a_construction():
    """RESTORE THE DEFECT: step `off` along the chord normal and never read it
    back - `lamps.py::build` verbatim, the geometry that put 23 lamps inside
    carriageways."""
    real = globals()["solve_point"]

    def constructed(seg, s, side, off, iters=8):
        cx, cz = station_at(seg, s)
        tx, tz = tangent_at(seg, s)
        return (cx + side * -tz * off, cz + side * tx * off)
    globals()["solve_point"] = constructed

    def undo():
        globals()["solve_point"] = real
    return undo


def _neuter_the_lateral_rule_to_one_segment():
    """RESTORE THE DEFECT: ask the lamp's OWN segment only.

    This is the rule that could not see 4 of the 23 - the ones standing in a
    NEIGHBOUR's lane, the closest 0.081 m from T5's centreline and placed by
    lighting-T12's own run.
    """
    real = L.KeepOut.carriageway_why_not

    def own_segment_only(self, x, z):
        name, nodes, hw = self.carriageways[0]
        d = L.perpendicular_m(x, z, nodes)
        return (None if d >= hw + L.LAMP_HALF_FOOTPRINT_M
                else f"{d:.3f} m inside {name}")
    L.KeepOut.carriageway_why_not = own_segment_only
    _FakeKeep.carriageway_why_not = own_segment_only

    def undo():
        L.KeepOut.carriageway_why_not = real
        _FakeKeep.carriageway_why_not = real
    return undo


def _neuter_spacing_to_arclength():
    """RESTORE THE DEFECT: a minimum that an arclength of exactly 30.0 m
    satisfies, which is what passed N1 and N2 at a real separation of
    29.9995 m."""
    real = GATE.MIN_SPACING_M
    GATE.MIN_SPACING_M = 29.999

    def undo():
        GATE.MIN_SPACING_M = real
    return undo


CASES = [
    ("straight offset is exact", _case_straight_offset_is_exact),
    ("bend construction is wrong, solver fixes it",
     _case_bend_construction_is_wrong_and_solver_fixes_it),
    ("solver never returns an unverified point",
     _case_solver_never_returns_an_unverified_point,
     _neuter_the_offset_to_a_construction,
     "solve_point returning the raw chord-normal construction"),
    ("in-carriageway lamp is seen", _case_carriageway_offender_is_seen),
    ("a NEIGHBOUR's carriageway is seen",
     _case_neighbours_carriageway_is_seen,
     _neuter_the_lateral_rule_to_one_segment,
     "the lateral rule asking the lamp's OWN segment only"),
    ("spacing is separation, not arclength",
     _case_spacing_uses_separation_not_arclength,
     _neuter_spacing_to_arclength,
     "a minimum an arclength of exactly 30.0 m satisfies"),
    ("spacing is a cylinder", _case_spacing_is_a_cylinder),
    ("re-site moves the minimum", _case_resite_moves_the_minimum),
    ("re-site respects a standing neighbour",
     _case_resite_respects_a_standing_neighbour),
    ("refuses rather than deletes", _case_refuses_rather_than_deletes),
]


def _run_case(fn) -> tuple[list[str], BaseException | None]:
    """`(problems, the exception it raised)`.

    A RAISE IS A RESULT, not a crash: a case that asserts its own fixture
    invariant says "the other way" by raising, and the neuter pass has to be
    able to hear that.  But WHICH raise matters - see `selftest`.
    """
    try:
        return list(fn() or []), None
    except AssertionError as exc:
        return [f"ASSERTED: {exc}"], exc
    except Exception as exc:                                # noqa: BLE001
        return [f"RAISED {type(exc).__name__}: {exc}"], exc


def selftest() -> int:
    bad = proven = 0
    for entry in CASES:
        name, fn = entry[0], entry[1]
        neuter, neuters = (entry[2], entry[3]) if len(entry) > 2 else (None, "")
        problems, _exc = _run_case(fn)
        if problems:
            bad += 1
            print(f"  FAIL {name}")
            for p in problems:
                print(f"       {p}")
            continue
        if neuter is None:
            print(f"  ok   {name}")
            continue
        # IS THIS CASE WHAT HOLDS THE FIX IN PLACE?  Restore the defect and
        # require THIS case to break.  A case that still passes with its own
        # discriminator switched off is a decoration.
        undo = neuter()
        try:
            under, exc = _run_case(fn)
        finally:
            undo()
        # A NEUTER THAT MISSES ITS TARGET PROVES NOTHING, AND THE NAIVE PASS
        # STAMPS IT PROVEN.  A neuter that patches the wrong attribute raises
        # TypeError or AttributeError from INSIDE the code under test; read as
        # "it came out the other way" that is an enforcement claiming a check
        # is held in place when it is not, which is strictly worse than no
        # enforcement.  Only the CASE'S OWN AssertionError, or a real change
        # in its problem list, counts.
        if exc is not None and not isinstance(exc, AssertionError):
            bad += 1
            print(f"  FAIL {name}")
            print(f"       THE NEUTER IS BROKEN, not the case: restoring the "
                  f"defect ({neuters}) raised "
                  f"{type(exc).__name__}: {exc}. A neuter must restore the "
                  f"defect and let the case's own measurement or assertion "
                  f"answer; any other exception comes from inside the code "
                  f"under test and proves nothing")
        elif not under:
            bad += 1
            print(f"  FAIL {name}")
            print(f"       THIS CASE IS NOT LOAD-BEARING: with the defect "
                  f"restored ({neuters}) it still passed, so it is not the "
                  f"case that holds the fix in place")
        else:
            proven += 1
            print(f"  ok   {name}   [PROVEN LOAD-BEARING: breaks under "
                  f"{neuters}]")
    print(f"\n  {len(CASES) - bad}/{len(CASES)} cases pass")
    print(f"  {proven} of {len(CASES)} PROVEN LOAD-BEARING BY NEUTER (the "
          f"case was re-run with its own discriminator restored and had to "
          f"break); {len(CASES) - proven} unproven")
    return 0 if not bad else 1


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--census", default="",
                    help="a cached `zdos.tsv` for OFFLINE planning; without "
                         "it the live world is censused through "
                         "lamps.Standing")
    ap.add_argument("--json", default="")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--place", action="store_true",
                    help="WRITE: retire each offender and spawn its "
                         "replacement, then save")
    ap.add_argument("--dry", action="store_true",
                    help="build and validate the records without appending")
    ap.add_argument("--only", default="",
                    help="comma list of segment short ids to act on; the "
                         "measurement still spans the whole network, because "
                         "a neighbour's lamp decides this one's spacing")
    ap.add_argument("--limit", type=int, default=0,
                    help="act on at most this many offenders. TEST ONE "
                         "BEFORE THE REST is what this is for.")
    ap.add_argument("--actor", default="",
                    help="the agent actually running this pass; REQUIRED for "
                         "--place and --dry")
    import worldseed
    worldseed.add_arguments(ap)
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    L.SEED, L.WORLD = worldseed.apply(args)
    if args.actor:
        L.ACTOR = args.actor
    elif args.place or args.dry:
        raise SystemExit(
            "REFUSING: --place/--dry without --actor. Every record would be "
            f"appended under {L.ACTOR!r}, which is the module's author and "
            "not whoever is running it.")

    segs = L.segments_from_ledger(L.WORLD, actor=L.ACTOR)
    if args.census:
        pop = census_from_tsv(Path(args.census))
        print(f"population: {len(pop)} torches from {args.census} "
              f"(OFFLINE; no ZDO ids)")
    else:
        import replay as R
        with R.Server(dry=False) as srv:
            srv.probe()
            standing = L.Standing(srv, list(segs.values()))
        pop = [dict(r) for r in standing.live]
        print(f"population: {len(pop)} torches from the LIVE world, "
              f"{standing.calls} calls")

    live = __import__("applied").Applied(actor=L.ACTOR)
    keep_zones = zones_around([(l["x"], l["z"]) for l in pop], pad_m=0.0)
    keep = L.KeepOut(keep_zones, live, actor=L.ACTOR)
    print(f"keep-outs: {keep.pads.describe()}; {len(keep.poi)} POI disc(s); "
          f"{len(keep.pieces)} protected piece(s); {len(keep.discs)} recorded "
          f"structure extent(s); {len(keep.carriageways)} carriageway(s)")

    offs = lateral_offenders(pop, keep)
    pairs = spacing_pairs(pop, segs)
    print(f"\nBEFORE: {len(offs)} lamp_lateral, {len(pairs)} lamp_spacing")
    want = {s.strip() for s in args.only.split(",") if s.strip()}
    touched = set()
    for o in offs:
        cor = in_corridor(o, segs)
        if cor:
            touched.add(cor[1])
    for p in pairs:
        for nm in (p["seg_a"], p["seg_b"]):
            touched.add(nm.split("_")[1] if nm.startswith("road_") else nm)
    if want:
        touched &= want
    print(f"segments in scope: {sorted(touched)}")

    grounds = {}
    for short in sorted(touched):
        seg = segs[short]
        pts = [(l["x"], l["z"]) for l in pop
               if in_corridor(l, segs) and in_corridor(l, segs)[1] == short]
        pts += [station_at(seg, s) for s in
                _window_stations(seg, pop, segs, short)]
        zs = zones_around(pts)
        print(f"  ground {short}: {len(zs)} zones", flush=True)
        grounds[short] = L.Ground(zs, SCRATCH / f"{short}.bin", live)

    res = plan(pop, segs, keep, grounds,
               scope=(touched if want else None), limit=args.limit)
    print(f"\nAFTER (projected): lamp_lateral {res['projected']['lamp_lateral']}"
          f", lamp_spacing {res['projected']['lamp_spacing']}")
    for d in res["decisions"]:
        if d.get("new"):
            print(f"  MOVE {d['segment']:>4s} "
                  f"({d['old']['x']:.2f},{d['old']['z']:.2f}) -> "
                  f"({d['new']['x']:.2f},{d['new']['z']:.2f})  {d['reason']}")
        else:
            print(f"  REFUSED {d.get('segment')} {d.get('reason')}")
            for c, n in (d.get("refusal_causes") or {}).items():
                print(f"          {n:5d} x {c}")
    if args.json:
        Path(args.json).write_text(json.dumps(res, indent=1, default=str))
        print(f"\nreport -> {args.json}")

    if args.place or args.dry:
        if args.census:
            raise SystemExit(
                "REFUSING: --place/--dry with --census. A cached save census "
                "carries no ZDO ids and is a reading of a world that has "
                "already moved on; a retire keyed on a stale population is "
                "how a lamp somebody else placed gets deleted.")
        moves = [d for d in res["decisions"] if d.get("new")]
        res["written"] = write_moves(moves, dry=args.dry)
        if args.json:
            Path(args.json).write_text(json.dumps(res, indent=1, default=str))
    return 0


# ---------------------------------------------------------------------------
# the write
# ---------------------------------------------------------------------------
# RETIRE THEN SPAWN, PER SEGMENT, AND NEVER ONE WITHOUT THE OTHER.  A lamp
# removed and not replaced is a DARK ROAD, which is a worse defect than the
# one being closed, so the two records are emitted back to back and the
# segment is verified by census before the next one starts.  There is no
# `modifyObject` that moves a pivot - MEASURED: `modify` in `ledger/schema.py`
# takes `rotation_deg` and nothing else, and `RotationModification::Apply` is
# `Quaternion.Euler` then `ZDO.SetRotation` - so a re-site is a demolition and
# a placement, and it is recorded as one.
LOOKUP_R_M = 0.30
SPAWN_PROBE_R_M = 1.5


def write_moves(moves: list[dict], dry: bool) -> dict:
    """Retire each offender and spawn its replacement, one segment at a time.

    READ-BEFORE-WRITE AT THE BOUNDARY THAT MATTERS.  The plan was made from a
    census taken minutes ago and three other agents are live on this world, so
    every victim is re-asked of the LIVE world immediately before its command
    is built: exactly one lamp of that prefab within `LOOKUP_R_M` of the
    recorded 3D position, or the move is DROPPED and reported.  The ZDO id
    that read returns is what goes on the wire, because a radius cannot tell
    two pieces at one pivot apart - and the id is evidence, never the key: it
    does not survive a container restart.
    """
    import clear as C                                       # noqa: PLC0415
    import to_rcon_plan                                     # noqa: PLC0415
    from live import LiveBuilder                            # noqa: PLC0415

    payload, keys = L.everburning_payload()
    by_seg: dict[str, list[dict]] = {}
    for d in moves:
        by_seg.setdefault(d["segment"], []).append(d)
    out: dict = {"segments": {}, "payload_keys": keys}

    with LiveBuilder(actor=L.ACTOR, dry=dry) as b:
        for short in sorted(by_seg):
            group = by_seg[short]
            victims, dropped = [], []
            for d in group:
                old = d["old"]
                got, unlisted, _k = C.list_prefab(
                    b.srv, old["prefab"], old["x"], old["y"], old["z"], 1.0)
                if unlisted:
                    dropped.append({**d, "why": "the box around this lamp was "
                                                "too dense to list"})
                    continue
                here = [r for r in got.values()
                        if math.dist((r["x"], r["y"], r["z"]),
                                     (old["x"], old["y"], old["z"]))
                        <= LOOKUP_R_M]
                if len(here) != 1:
                    dropped.append({**d, "why": f"{len(here)} {old['prefab']} "
                                                f"within {LOOKUP_R_M} m of "
                                                f"the recorded position, not "
                                                f"1"})
                    continue
                # THE VICTIM IS INSIDE ITS OWN PROBE, AND THAT IS NOT AN
                # OBSTACLE.  MEASURED on T5: the move is 1.345 m and the
                # probe radius is 1.5 m, so the lamp being removed answered
                # the question "is the new spot free" with itself and the
                # move was dropped.  A short move is exactly the move this
                # pass wants to make - it disturbs the night walk least - so
                # the probe subtracts the pieces this record is about to
                # delete rather than the pass declining to make them.
                free, _by = b.srv.count(d["new"]["prefab"], d["new"]["x"],
                                        d["new"]["z"], SPAWN_PROBE_R_M)
                leaving = sum(
                    1 for e in group
                    if e["old"]["prefab"] == d["new"]["prefab"]
                    and math.hypot(e["old"]["x"] - d["new"]["x"],
                                   e["old"]["z"] - d["new"]["z"])
                    <= SPAWN_PROBE_R_M)
                if free - leaving > 0:
                    dropped.append({**d, "why": f"{free - leaving} "
                                                f"{d['new']['prefab']} "
                                                f"already within "
                                                f"{SPAWN_PROBE_R_M} m of the "
                                                f"chosen spot and not being "
                                                f"retired by this record"})
                    continue
                col, _by = b.srv.count(old["prefab"], old["x"], old["z"],
                                       SPAWN_PROBE_R_M)
                # Every victim in THIS batch that shares the probe leaves
                # together, so the postcondition is the count after all of
                # them go, not after one.
                same = sum(1 for e in group
                           if e["old"]["prefab"] == old["prefab"]
                           and math.hypot(e["old"]["x"] - old["x"],
                                          e["old"]["z"] - old["z"])
                           <= SPAWN_PROBE_R_M)
                victims.append({**d, "zdo_id": here[0]["id"],
                                "column_after": col - same})
            if dropped:
                print(f"  {short}: DROPPED {len(dropped)} of {len(group)} "
                      f"move(s) at the write boundary")
                for x in dropped:
                    print(f"    ({x['old']['x']:.2f},{x['old']['z']:.2f}) "
                          f"{x['why']}")
            if not victims:
                out["segments"][short] = {"moved": 0,
                                          "dropped": len(dropped)}
                continue

            pieces = [{"prefab": v["old"]["prefab"],
                       "pos": [v["old"]["x"], v["old"]["y"], v["old"]["z"]],
                       "radius_m": LOOKUP_R_M,
                       "keeps": v["column_after"],
                       "zdo_id": v["zdo_id"]} for v in victims]
            wire = [f"deleteObjects -id {v['zdo_id']} -force" for v in victims]
            worst = min(v["old"].get("lateral_m", 0.0) for v in victims) \
                if all("lateral_m" in v["old"] for v in victims) else None
            # `retires` NAMES THE LEDGER RECORDS THIS SUPERSEDES, AND A
            # RE-SITE SUPERSEDES NONE.  The lighting plans that placed these
            # lamps are not cancelled - their other lamps still stand - so
            # the honest answer is the EMPTY LIST.  It used to be `[0]`,
            # because the validator demanded a non-empty list and `[0]` was
            # the only thing that passed; MEASURED by ParBuried, 123 records
            # on this chain say `retires: [0]` and SEQ 0 IS THE WORLD
            # MANIFEST, so five agents have collectively asserted that this
            # world's seed, fwl digest and mod set are retired.  Inert today
            # - every reader of `retired_seqs` filters on `op` first - and a
            # latent lie in an append-only log cannot be corrected afterwards.
            rr = b.emit("retire", params={
                "prefab": pieces[0]["prefab"], "pos": pieces[0]["pos"],
                "radius_m": LOOKUP_R_M, "retires": [],
                "role": "road_lighting", "site_id": f"lighting-{short}",
                "pieces": pieces,
                "reason": (
                    f"{len(victims)} road lamp(s) on {short} stand where a "
                    f"lamp may not: inside a built carriageway, or closer to "
                    f"a neighbour than the biome's own minimum, MEASURED as "
                    f"the TRUE PERPENDICULAR to every built centreline and as "
                    f"REAL SEPARATION in a 10 m-half-height cylinder - the "
                    f"acceptance gate's own two rules. Each is replaced in "
                    f"the very next record at a position that satisfies both, "
                    f"so no stretch of road loses its light. Removed by ZDO "
                    f"id because a radius cannot tell two pieces at one pivot "
                    f"apart; the id is evidence and NOT the key - it does not "
                    f"survive a container restart, which is why every entry "
                    f"also carries prefab plus the full 3D position."
                    + (f" Worst offender {worst:.3f} m from a centreline."
                       if worst is not None else ""))},
                wire=wire,
                expect={"prefab_count": [
                    {"prefab": v["old"]["prefab"],
                     "pos": [v["old"]["x"], v["old"]["z"]],
                     "max": SPAWN_PROBE_R_M, "count": v["column_after"]}
                    for v in victims]},
                meta={"zdo_ids_removed": [v["zdo_id"] for v in victims],
                      "segment": short,
                      "players_probe_ms": round(b.srv.probe(), 1),
                      "why": "re-site, not demolition: the replacement "
                             "spawn_plan is the next record"})
            print(f"  {short}: retire seq {rr['seq']} -> {rr['status']} "
                  f"{rr.get('checks')}")

            lines = []
            for v in victims:
                n = v["new"]
                lines.append(
                    f"spawn_object {n['prefab']}"
                    f" pos={to_rcon_plan.pos_arg(n['x'], n['y'], n['z'])}"
                    f" rot={to_rcon_plan.rot_arg(0.0, n['yaw_deg'], 0.0)}"
                    f" {to_rcon_plan.FROM_ORIGIN} data={payload}")
            body = "\n".join(lines) + "\n"
            import hashlib                                   # noqa: PLC0415
            sha = hashlib.sha256(body.encode()).hexdigest()
            blob = b.blob(body.encode(),
                          note=f"re-sited lamp spawn commands for {short}")
            b.observe(
                f"{short} re-sited lamp lateral offsets",
                "MEASURED: true perpendicular from each new position to the "
                "WHOLE polyline of every built centreline, the same "
                "`perpendicular_m` the acceptance gate's lighting section "
                "uses, after the offset was solved against that measurement "
                "rather than constructed from a chord normal",
                "tools/jumpstart/roads/relight.py::solve_point",
                {v["new"]["x"]: v["new"]["lateral_m"] for v in victims},
                units="m")
            sr = b.emit("spawn_plan", params={
                "role": "road_lighting", "site_id": f"lighting-{short}",
                "plan_sha256": sha,
                "anchor": {"x": victims[0]["new"]["x"],
                           "y": victims[0]["new"]["y"],
                           "z": victims[0]["new"]["z"],
                           "yaw": victims[0]["new"]["yaw_deg"]},
                "command_count": len(lines),
                "prefabs": sorted({v["new"]["prefab"] for v in victims}),
                "datum": ("each lamp's own applied surface + "
                          f"{L.seat_offset_m():.5f} m, the prefab's measured "
                          "pivot offset from fixtures.fixture_box"),
                # RESEAT, NOT FORBIDDEN -- the same correction `lamps.py`
                # carries, and for the same measured reason: a re-sited lamp
                # claims its OWN SEATING, not the road's ground, and THIS
                # FILE IS THE TOOL THAT DISCHARGES THE CLAIM.  The retire +
                # spawn_plan pair immediately above is exactly the re-seat a
                # `params.reseats` declaration promises.
                "flatten": "RESEAT",
                "flatten_reason":
                    "a re-sited lamp's claim is on its OWN SEATING: cut "
                    "this shoulder and the lamp is left hanging above it. "
                    "The ground is the road's and the road may regrade it "
                    "-- a terrain_write discharges this claim by naming "
                    "this seq in params.reseats and re-seating afterwards, "
                    "which is what this tool does. A write that does not "
                    "name it is still refused.",
            }, wire=lines, requires={"blobs": [blob],
                                     "mods": ["WorldEditCommands",
                                              "ServerDevcommands"],
                                     "prefabs": sorted({v["new"]["prefab"]
                                                        for v in victims})},
                expect={"prefab_count": [
                    {"prefab": v["new"]["prefab"],
                     "pos": [v["new"]["x"], v["new"]["z"]],
                     "max": SPAWN_PROBE_R_M, "count": 1, "tolerance": 0}
                    for v in victims]},
                meta={"lamps": len(victims), "segment": short,
                      "players_probe_ms": round(b.srv.probe(), 1),
                      "everburning_zdo_ints": keys,
                      "moved_m": [v["new"]["moved_m"] for v in victims],
                      "lateral_m": [v["new"]["lateral_m"] for v in victims],
                      "why": ("each lamp re-sited to the nearest position on "
                              "its own segment that is legal under every "
                              "published rule at once; see relight.py")})
            print(f"  {short}: spawn_plan seq {sr['seq']} -> {sr['status']} "
                  f"{sr.get('checks')}")
            out["segments"][short] = {
                "moved": len(victims), "dropped": len(dropped),
                "retire_seq": rr["seq"], "spawn_seq": sr["seq"],
                "retire_status": rr["status"], "spawn_status": sr["status"],
                "moves": [{"from": [v["old"]["x"], v["old"]["y"],
                                    v["old"]["z"]],
                           "to": [v["new"]["x"], v["new"]["y"],
                                  v["new"]["z"]],
                           "prefab": v["new"]["prefab"],
                           "zdo_id": v["zdo_id"],
                           "lateral_m": v["new"]["lateral_m"],
                           "moved_m": v["new"]["moved_m"]} for v in victims]}
        # THE SAVE'S POSTCONDITION IS THE MOVES THAT WERE ACTUALLY WRITTEN,
        # never the ones that were planned.  A move dropped at the write
        # boundary is a lamp still standing where it was, and asserting a
        # replacement at a spot nothing was spawned into would fail the save
        # over a lamp this pass correctly declined to touch.
        written = [m for s in out["segments"].values()
                   for m in s.get("moves", [])]
        if written:
            sv = b.emit("save", params={"role": "road_lighting"},
                        wire=["save"],
                        expect={"prefab_count": [
                            {"prefab": m["prefab"],
                             "pos": [m["to"][0], m["to"][2]],
                             "max": SPAWN_PROBE_R_M, "count": 1}
                            for m in written]},
                        meta={"why": "a re-site must survive a SAVE: a ZDO "
                                     "spawned into a live world and a ZDO in "
                                     "the file are not the same claim, and "
                                     "the gate reads the file"})
            print(f"  save seq {sv['seq']} -> {sv['status']}")
            out["save_seq"] = sv["seq"]
        out["ledger"] = b.close()
    return out


def _window_stations(seg, pop, segs, short) -> list[float]:
    out = []
    for l in pop:
        cor = in_corridor(l, segs)
        if not cor or cor[1] != short:
            continue
        s0 = project_s(seg, l["x"], l["z"])
        out += [max(0.0, s0 - SEARCH_HALF_M), s0,
                min(seg.length_m, s0 + SEARCH_HALF_M)]
    return out


if __name__ == "__main__":
    raise SystemExit(main())
