#!/usr/bin/env python3
"""Plan the Ulfsland road network: route, fit, cost in ZDOs, hand off crossings.

A ROAD IS TERRAIN, NOT PIECES.  `piece_pavedroad` has no persistent ZNetView and
zero road pieces exist in any fleet world save, so a spawned road piece leaves
nothing behind.  What persists is one `_TerrainCompiler` ZDO per zone carrying a
gzip'd `TCData` blob of per-sample height deltas and per-sample ground paint
(r dirt, g cultivated, b paved, a vegetation-still-standing) at 1 m over a 64 m
zone.  So a road is a levelled, paved ribbon of terrain.

THE ZDO COST IS PER ZONE, NOT PER METRE, AND THAT IS THE HEADLINE NUMBER.
MEASURED mechanism: `Heightmap::GetAndCreateTerrainCompiler` returns the FIRST
`_TerrainCompiler` it finds in a zone, so a zone holds exactly one and a second
is dead weight whose data is never applied (this is also why flatten.py deletes
before it spawns).  Therefore N metres of ribbon cost one ZDO per 64 m zone the
ribbon touches, NOT one per metre.  The "956 ZDOs per 957 m of paving" figure
from the earlier design was the PIECE-based road, which is the approach this
tool exists to replace; quoting it against a terrain ribbon overstates the cost
by roughly the zone length.

Usage:
    network.py plan                 # route everything, write segments/crossings
    network.py plan --pri 1         # just the spawn-island trunk
    network.py plan --only T1-...   # one segment
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import field as fieldmod  # noqa: E402
import poi as poimod  # noqa: E402
import route as routemod  # noqa: E402
import spec as specmod  # noqa: E402
from field import BIOME_NAMES, WATER_LEVEL  # noqa: E402

SEED = "Pirate68"
FIELD_BIN = "/tmp/roads/h1m.bin"

# Ribbon widths, set by Crossings' MEASURED deck pitch (wood_floor is a
# 2.000 x 0.130 x 2.000 collider solid, so 2 m tiles): it builds a 3-tile 6 m
# deck on trunk crossings and a 2-tile 4 m deck on spurs.  A ribbon wider than
# the deck is a pinch the operator sees at every bridge.
WIDTH_M = {"trunk": 6.0, "spur": 4.0, "mountain": 4.0}
BANK_LEVEL_M = 6.0      # Crossings' requirement: level run at each abutment
# Crossings' MEASURED absolute span ceiling: 72 m free span at a 3x safety
# factor on an iron stringer under the deck, 96 m absolute, from its own
# implementation of WearNTear::UpdateSupport.  Beyond this a "bridge" is not a
# thing that can be built, so a route needing one is rejected rather than
# handed over.
MAX_BRIDGE_SPAN_M = 96.0
STATION_M = 2.0         # profile station spacing; also Crossings' snap


def plain(o):
    """Coerce numpy scalars and arrays to YAML/JSON-representable Python.

    Needed because `yaml.safe_dump` raises RepresenterError on `numpy.float32`,
    and every height in this tool comes out of a float32 array.  Silent
    stringification would be worse than the exception, so this is explicit.
    """
    if isinstance(o, dict):
        return {k: plain(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [plain(v) for v in o]
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.ndarray):
        return [plain(v) for v in o.tolist()]
    return o


def straighten(pts, s, a: int, b: int):
    """Replace stations a..b with a straight line between their endpoints.

    `Crossings` cannot build a curve mid-span and asked for a constant bearing
    across the span plus 6 m each side.  Done on the STATION list, not the
    corridor nodes, so station count and spacing survive and the profile indices
    stay valid.
    """
    ax, az = pts[a]
    bx, bz = pts[b]
    span = s[b] - s[a]
    out = list(pts)
    for k in range(a + 1, b):
        f = (s[k] - s[a]) / span if span > 0 else 0.0
        out[k] = (ax + (bx - ax) * f, az + (bz - az) * f)
    return out


def build_segment(fld, edge: dict, corr: routemod.Corridor, pois: list,
                  land_only: bool = False) -> dict:
    a_node = specmod.NODES[edge["a"]]
    b_node = specmod.NODES[edge["b"]]
    cls = edge["cls"]
    # Crossings' deck width wins over my road-class default where a segment
    # arrives at one of its terminals: a 6 m trunk ribbon meeting a 4 m jetty
    # IS the pinch that matching widths exists to avoid.
    width_m = float(edge.get("width_m") or WIDTH_M[cls])
    half_w = width_m / 2.0
    # A per-edge override exists for exactly one situation: a short ramp from
    # high ground down onto a bridge deck whose height Crossings owns.  The
    # portal hall stands 6.8 m above the S2 abutment 30 m away, so no 12 %
    # profile reaches it and the interval propagation PROVES that (2 stations
    # infeasible).  Lengthening a 30 m spur into a 60 m switchback to save 10 %
    # of grade on a bridge approach is worse for the operator than a short ramp.
    # Overrides are recorded per segment and are never silent.
    g_max = float(edge.get("grade_max") or routemod.GRADE[cls])

    corr.forbid_all_water = land_only
    try:
        raw = corr.search(a_node["xz"], b_node["xz"])
    finally:
        corr.forbid_all_water = False
    # Simplify tolerance is DELIBERATELY tight.  A* returns one node per 4 m
    # cell, i.e. a staircase, and the ribbon is rasterised from segments, so a
    # staircase shows as a scalloped edge from the first step.  But every metre
    # of chord-cutting also moves the centreline off the cells the POI mask
    # cleared, so the tolerance is 1 m and the mask is dilated to cover it
    # rather than the tolerance being loosened and the breach being discovered
    # afterwards.
    poly = routemod.simplify(raw, tol_m=1.0)
    pts, s = routemod.resample(poly, STATION_M)
    s = np.asarray(s, dtype=np.float64)
    t = np.array([fld.bilinear(x, z) for x, z in pts], dtype=np.float64)

    # Pass 1, terrain only: its infeasible spans plus the wet stations ARE the
    # definition of a crossing, and the infeasibility is a PROOF (interval
    # propagation is necessary and sufficient), not an opinion.
    fit1 = routemod.fit_profile(t, s, g_max)
    detected, _flag, fords = routemod.find_crossings(
        edge["id"], pts, s, t, fit1["infeasible_spans"])

    # Geometry second: mark the deck stations, straighten each crossing window
    # and each declared-abutment window, then re-sample the terrain because
    # straightening moved the stations.
    free = np.zeros(len(pts), dtype=bool)
    windows = []
    for c in detected:
        ia = _station_of(pts, c["bank_a"]["xz"])
        ib = _station_of(pts, c["bank_b"]["xz"])
        ia, ib = min(ia, ib), max(ia, ib)
        wa = max(0, int(np.searchsorted(s, s[ia] - BANK_LEVEL_M)))
        wb = min(len(pts) - 1, int(np.searchsorted(s, s[ib] + BANK_LEVEL_M)))
        free[ia:ib + 1] = True
        windows.append((c, ia, ib, wa, wb))
    abut_windows = []
    for which, node_id in (("a", edge["a"]), ("b", edge["b"])):
        node = specmod.NODES[node_id]
        if node.get("level_y") is None:
            continue
        if which == "a":
            sel = np.nonzero(s <= s[0] + BANK_LEVEL_M)[0]
        else:
            sel = np.nonzero(s >= s[-1] - BANK_LEVEL_M)[0]
        abut_windows.append((which, node_id, float(node["level_y"]),
                             int(sel.min()), int(sel.max())))
    for _c, _ia, _ib, wa, wb in windows:
        pts = straighten(pts, s, wa, wb)
    for _w, _n, _h, lo_i, hi_i in abut_windows:
        if hi_i > lo_i:
            pts = straighten(pts, s, lo_i, hi_i)
    if windows or abut_windows:
        t = np.array([fld.bilinear(x, z) for x, z in pts], dtype=np.float64)

    # ONE constraint set.  Every special case -- bridge deck, level bank run,
    # abutment height owned by Crossings, ordinary cut and fill -- becomes a
    # per-station [lo, hi] pair, and the profile is fitted once inside it.
    #
    # This replaced a version that fitted first and then PATCHED the levelling
    # and the grade limit on afterwards.  That version left 8 stations outside
    # the measured +/-8 m clamp on the first trunk segment, because a flat
    # insert violates the grade limit and re-imposing the grade limit violates
    # the cut/fill box: each fix broke what the other had just fixed.  Stating
    # the constraints once and solving them once cannot do that.
    CF = routemod.CUT_FILL_MAX
    lo = np.maximum(t - CF, WATER_LEVEL + routemod.ROAD_FREEBOARD_M)
    hi = t + CF
    for _c, ia, ib, _wa, _wb in windows:
        wet = bool((t[ia:ib + 1] <= WATER_LEVEL + routemod.ROAD_FREEBOARD_M).any())
        lo[ia:ib + 1] = (np.maximum(t[ia:ib + 1], WATER_LEVEL + routemod.DECK_CLEARANCE_M)
                         if wet else t[ia:ib + 1] + 0.5)
        hi[ia:ib + 1] = 1e6

    # A level run needs ONE height H across the whole window.  The set of legal
    # H is an interval, computed exactly: H must be within the clamp of every
    # DRY station in the window, and above deck clearance if the gap holds
    # water.  An empty interval means a single H is impossible here, which is a
    # fact Crossings needs (its deck then has a grade) rather than something to
    # average away.
    bank_level = []
    for c, ia, ib, wa, wb in windows:
        dry = [k for k in range(wa, wb + 1) if not free[k]]
        wet = bool((t[ia:ib + 1] <= WATER_LEVEL + routemod.ROAD_FREEBOARD_M).any())
        if dry:
            h_lo = float(max(t[k] for k in dry)) - CF
            h_hi = float(min(t[k] for k in dry)) + CF
        else:
            # Only reachable when the window is clipped by the end of the
            # segment.  Fall back to the window's own end stations rather than
            # to an unbounded interval: an unbounded interval's midpoint is
            # zero, and a level run at y=0 is 30 m below the sea.
            h_lo = float(max(t[wa], t[wb])) - CF
            h_hi = float(min(t[wa], t[wb])) + CF
        if wet:
            h_lo = max(h_lo, WATER_LEVEL + routemod.DECK_CLEARANCE_M)
        single = h_lo <= h_hi
        if single:
            ref = [t[k] for k in dry] if dry else [t[wa], t[wb]]
            H = min(max(float(np.median(ref)), h_lo), h_hi)
            lo[wa:wb + 1] = H
            hi[wa:wb + 1] = H
        else:
            H = None
        ax, az = pts[wa]
        bx, bz = pts[wb]
        brg = (math.degrees(math.atan2(bx - ax, bz - az)) + 360.0) % 360.0
        c.update({
            "status": "detected", "ribbon_width_m": width_m,
            "bank_level_run_m": BANK_LEVEL_M,
            "required_deck_y": round(H, 3) if H is not None else None,
            "single_H_both_banks": bool(single),
            "legal_deck_y_interval": [round(h_lo, 3), round(h_hi, 3)],
            "centreline_straight_from_to": [[round(ax, 2), round(az, 2)],
                                            [round(bx, 2), round(bz, 2)]],
        })
        c["bank_a"].update({"road_deck_y": round(H, 3) if H is not None else None,
                            "bearing_deg": round(brg, 1)})
        c["bank_b"].update({"road_deck_y": round(H, 3) if H is not None else None,
                            "bearing_deg": round((brg + 180.0) % 360.0, 1)})
        c["why_terrain_failed"] = c.pop("reason", c.get("why_terrain_failed"))
        bank_level.append({"crossing_id": c["crossing_id"],
                           "level_at_y": c["required_deck_y"],
                           "single_H_both_banks": bool(single),
                           "legal_interval": [round(h_lo, 3), round(h_hi, 3)]})

    # A declared abutment's height is NOT mine to choose: Crossings owns the
    # deck at exactly y = 30.6 (the same datum early-dock already declares), so
    # the last 6 m of ribbon is pinned there and any conflict with the clamp is
    # reported rather than resolved by moving the deck.
    abutments = []
    for which, node_id, H, lo_i, hi_i in abut_windows:
        sel = np.arange(lo_i, hi_i + 1)
        worst = float(np.max(np.abs(H - t[sel])))
        lo[sel] = H
        hi[sel] = H
        k_end = 0 if which == "a" else len(pts) - 1
        k_in = hi_i if which == "a" else lo_i
        bx, bz = pts[k_in]
        ax, az = pts[k_end]
        brg = (math.degrees(math.atan2(ax - bx, az - bz)) + 360.0) % 360.0
        abutments.append({
            "node": node_id, "end": which, "level_y": H,
            "level_run_m": BANK_LEVEL_M,
            "worst_cut_fill_m": round(worst, 3),
            "within_clamp": bool(worst <= CF),
            "ribbon_width_m": width_m,
            "outbound_bearing_deg": round(brg, 2),
            "ribbon_end_xz": [round(pts[k_end][0], 2), round(pts[k_end][1], 2)],
        })

    # GRADE RELAXATION LADDER.  If the one constraint set proves no profile
    # exists at the design grade, the honest options are: relax the grade, or
    # declare the segment unbuildable.  Silently leaving samples outside the
    # clamp is not an option -- those are metres of road that will not be where
    # the plan says, because ApplyToHeightmap discards the excess.
    #
    # The ladder stops at the 25 % network ceiling, which is still less than a
    # third of the 38 deg (78 %) angle at which Character::GetSlideAngle makes a
    # player slide, so no rung of it can produce a road that dumps the operator
    # downhill.  Every relaxation is recorded per segment.
    ladder = [g_max] + [g for g in (routemod.GRADE["spur"],
                                    routemod.GRADE["mountain"],
                                    routemod.GRADE_CEILING) if g > g_max]
    relaxed_to = None
    for rung in ladder:
        fit = routemod.fit_profile(t, s, rung, lo=lo, hi=hi)
        terrain_bad = [(a, b) for a, b in fit["infeasible_spans"]
                       if not free[a:b + 1].all()]
        if not terrain_bad:
            if rung != g_max:
                relaxed_to = rung
            g_max = rung
            break
    else:
        relaxed_to = ladder[-1]
        g_max = ladder[-1]
    y = fit["y"]
    ds = np.diff(s)
    grade = np.abs(np.diff(y)) / np.maximum(ds, 1e-9)
    terr = ~free
    cf = (y - t)[terr]
    # TWO numbers, because they answer different questions.  `over_margin` is
    # against the 7 m design margin this tool fits to; `over_clamp` is against
    # the MEASURED 8 m limit in TerrainComp::ApplyToHeightmap, which is the only
    # one that decides whether the ribbon lands where the plan says.  Collapsing
    # them into one reported a buildable road as a failure and would have
    # reported an unbuildable one as fine if the margin were ever raised.
    over_margin = int((np.abs(cf) > CF).sum())
    over = int((np.abs(cf) > routemod.CLAMP_M).sum())

    # Second-pass infeasibility: a span the ONE constraint set could not satisfy
    # is terrain that cannot be built, and it goes to Crossings too.  Reported
    # separately so it is obvious these were found by the final solve rather
    # than by the wet test.
    residual = []
    for a, b in fit["infeasible_spans"]:
        if free[a:b + 1].all():
            continue
        residual.append({
            "segment_id": edge["id"], "stations": [int(a), int(b)],
            "xz_from": [round(pts[a][0], 2), round(pts[a][1], 2)],
            "xz_to": [round(pts[b][0], 2), round(pts[b][1], 2)],
            "length_m": round(float(s[b] - s[a]), 1),
            "why": "no slope-limited profile exists inside the +/-8 m apply clamp "
                   "here, PROVEN by interval propagation",
        })

    # Where did the route actually end relative to what was asked for?  The
    # corridor snaps endpoints out of water and out of POI keep-outs, so the
    # stand-off is a RESULT to report, not an assumption to hide.
    def standoff(node_id, p):
        nx, nz = specmod.NODES[node_id]["xz"]
        return round(math.hypot(p[0] - nx, p[1] - nz), 1)

    near = poimod.near_centreline(pois, pts, half_w)
    violations = [r for r in near if r["violates_hard"]]

    return {
        "id": edge["id"], "cls": cls, "pri": edge["pri"],
        "island": a_node["island"], "patch": a_node["patch"],
        "from": edge["a"], "to": edge["b"],
        "why": edge.get("why", ""),
        "width_m": width_m, "station_m": STATION_M,
        "length_m": round(float(s[-1]), 1),
        "endpoint_standoff_m": {edge["a"]: standoff(edge["a"], pts[0]),
                                edge["b"]: standoff(edge["b"], pts[-1])},
        "grade_limit": g_max,
        "grade_limit_design": routemod.GRADE[cls],
        "grade_relaxed_to": relaxed_to,
        "grade_max_measured": round(float(grade.max()), 4) if len(grade) else 0.0,
        "grade_p95": round(float(np.percentile(grade, 95)), 4) if len(grade) else 0.0,
        "grade_mean": round(float(grade.mean()), 4) if len(grade) else 0.0,
        "cut_max_m": round(float(cf.min()), 2) if cf.size else 0.0,
        "fill_max_m": round(float(cf.max()), 2) if cf.size else 0.0,
        "stations_over_clamp": over,
        "stations_over_design_margin": over_margin,
        "design_margin_m": CF,
        "grade_limit_is_override": bool(edge.get("grade_max")),
        "terrain_stations": int(terr.sum()),
        "fords": fords,
        "ford_count": len(fords),
        "bridged_stations": int(free.sum()),
        "abutments": abutments,
        "bank_level": bank_level,
        "crossings": detected,
        "residual_infeasible": residual,
        "poi_within_keepout": near[:12],
        "poi_hard_violations": violations,
        "nodes": [[round(x, 2), round(z, 2)] for x, z in pts],
        "profile_y": [round(float(v), 3) for v in y],
        "terrain_y": [round(float(v), 3) for v in t],
        "is_bridge": [bool(v) for v in free],
    }


def _station_of(pts, xz) -> int:
    x, z = xz
    d = [(px - x) ** 2 + (pz - z) ** 2 for px, pz in pts]
    return int(np.argmin(d))


def zones_of_segment(seg: dict) -> set[tuple[int, int]]:
    """Every zone whose sample lattice a TERRAIN station of this segment touches.

    `ZoneSystem::GetZone` is floor((c + 32) / 64) per axis, and a zone's 65x65
    lattice includes both edges, so a sample within half a metre of a zone line
    belongs to two zones and both compilers must be written.  Bridged stations
    are excluded: those are Crossings' deck, not my terrain.
    """
    zones: set[tuple[int, int]] = set()
    half = seg["width_m"] / 2.0
    prev = None
    for (x, z), bridged in zip(seg["nodes"], seg["is_bridge"]):
        if bridged:
            prev = None
            continue
        pts = [(x, z)]
        if prev is not None:
            # Fill between stations at 1 m so a 2 m station spacing cannot skip a
            # zone whose corner the ribbon clips.
            px, pz = prev
            n = max(1, int(math.hypot(x - px, z - pz)))
            pts = [(px + (x - px) * k / n, pz + (z - pz) * k / n) for k in range(n + 1)]
        for cx, cz in pts:
            for dx in (-half, 0.0, half):
                for dz in (-half, 0.0, half):
                    zones.add((math.floor((cx + dx + 32.0) / 64.0),
                               math.floor((cz + dz + 32.0) / 64.0)))
        prev = (x, z)
    return zones


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("op", choices=["plan"])
    ap.add_argument("--field", default=FIELD_BIN)
    ap.add_argument("--only", action="append")
    ap.add_argument("--pri", type=int, action="append")
    ap.add_argument("--out", default=str(HERE))
    args = ap.parse_args()

    flds = fieldmod.load(args.field)
    pois = poimod.load()
    out_dir = Path(args.out)
    corridors: dict[tuple[str, str], routemod.Corridor] = {}
    segments = []
    unroutable: list[dict] = []
    rejected: list[dict] = []
    poi_masked: dict[str, list] = {}

    edges = [e for e in specmod.EDGES
             if (not args.only or e["id"] in args.only)
             and (not args.pri or e["pri"] in args.pri)]
    for edge in edges:
        a_node = specmod.NODES[edge["a"]]
        if specmod.NODES[edge["b"]]["island"] != a_node["island"]:
            # An edge between two islands is a finding, not a crash: it means a
            # destination was assigned to the wrong landmass, and the honest
            # output is a record saying so rather than a dead run.
            unroutable.append({
                "id": edge["id"], "from": edge["a"], "to": edge["b"],
                "cls": edge["cls"], "pri": edge["pri"],
                "error": "endpoints are on different landmasses",
                "from_island": a_node["island"],
                "to_island": specmod.NODES[edge["b"]]["island"],
                "why": "a road cannot span two landmasses; this needs a bridge or "
                       "a ferry from Crossings, or the destination is portal-only",
            })
            print(f"{edge['id']:22s} p{edge['pri']} CROSS-ISLAND: "
                  f"{a_node['island']} -> {specmod.NODES[edge['b']]['island']}")
            continue
        patch = a_node["patch"]
        key = (patch, edge["cls"])
        if key not in corridors:
            fld = flds[patch]
            corr = routemod.Corridor(
                fld, cell_m=4.0, grade_max=routemod.GRADE[edge["cls"]],
                # Crossings MEASURED that SPAN LENGTH is nearly free to it (a
                # wood deck is any length in 8 m bays), and asked me not to
                # detour to find a narrow neck.  That is about WHERE to cross a
                # channel the route has to cross -- it is not licence to
                # substitute bridge for road.  With water at 3 road-metres per
                # metre the router MEASURABLY did exactly that: it planned a
                # 136 m bridge on the 442 m temple->S1 segment to avoid roughly
                # 400 m of walking, which is terrible value for a structure
                # nobody had measured.  At 10 per metre plus a 250 m entry, that
                # same crossing costs 1,610 road-metres and the router only
                # bridges when the land detour is genuinely long -- while still
                # choosing the flattest place to cross rather than the narrowest,
                # because the per-metre term is small next to the gradient term.
                # RETUNED AGAIN, and the measurement that forced it: at 10
                # road-metres per water metre the router took SHORELINE
                # SHORTCUTS over open water in preference to going inland, and
                # six segments came out with bridged runs of 103 m, 111 m,
                # 175 m, 184 m, 318 m and 418 m -- every one of them past
                # Crossings' MEASURED 96 m absolute span ceiling, i.e. six
                # bridges that cannot be built. At 60 per metre plus a 400 m
                # entry, a 100 m span costs 6,400 road-metres and is only taken
                # when the land detour is longer than that, while the 12 m
                # channel Crossings measured still costs only 1,120 -- so real
                # short crossings survive and shoreline shortcuts do not.
                # Water is priced MODERATELY now that the span ceiling is a
                # geometric constraint rather than a price (see
                # Corridor.water_forbidden).  A crossing the search can still
                # propose is one a bridge can physically reach across, so the
                # price only has to express "prefer land, and cross at the
                # flattest place rather than the narrowest" -- which is exactly
                # what Crossings asked for.
                bridge_entry_cost_m=300.0, bridge_per_m_cost=15.0)
            forbid, soft, touched = poimod.masks(
                pois, corr.x0, corr.z0, corr.cell_m, corr.m, WIDTH_M[edge["cls"]] / 2.0)
            corr.forbid, corr.soft = forbid, soft
            poi_masked[f"{patch}:{edge['cls']}"] = {
                "instances_overlapping_grid": len(touched),
                "cells_hard_forbidden": int(forbid.sum()),
                "cells_soft_penalised": int((soft > 0).sum()),
                "grid_cells": int(corr.m * corr.m),
                "protected_overlapping": sum(1 for r in touched if r["protected"]),
            }
            corridors[key] = corr
        try:
            seg = build_segment(flds[patch], edge, corridors[key], pois)
            worst = max((c["span_m"] for c in seg["crossings"]), default=0.0)
            if worst > MAX_BRIDGE_SPAN_M:
                # LAND-ONLY RETRY.  The span ceiling is already encoded
                # geometrically (water further than half the maximum span from
                # land is not traversable), but that bounds a crossing's WIDTH,
                # not its LENGTH: a route running 200 m parallel to the coast
                # 20 m offshore is inside the width bound at every step and is
                # still a 200 m "bridge".  MEASURED on three segments (114.8 m,
                # 174.7 m, 200.6 m).  Rather than add water-run length to the
                # search state -- which would multiply a 640,000-cell grid by
                # the run bound -- re-route the offending segment with water
                # forbidden outright.  If a land route exists it is the right
                # answer anyway; if none exists the rejection is a proof rather
                # than a tuning artefact.
                land = build_segment(flds[patch], edge, corridors[key], pois,
                                     land_only=True)
                land["land_only_retry"] = {
                    "first_attempt_worst_span_m": worst,
                    "why": "first route proposed a bridged run past Crossings' "
                           "96 m absolute span ceiling; re-routed with water "
                           "forbidden",
                    "length_penalty_m": round(land["length_m"] - seg["length_m"], 1),
                }
                seg = land
        except ValueError as exc:
            # An unroutable edge is a FINDING, not a crash.  A destination on a
            # different landmass, or one whose every approach is inside a
            # protected POI's keep-out, has no road and the plan has to say so:
            # a segment silently dropped from a network file reads as an
            # oversight to the next reader, which is exactly how "why is there
            # no road to the watchtower" becomes unanswerable.
            unroutable.append({
                "id": edge["id"], "from": edge["a"], "to": edge["b"],
                "cls": edge["cls"], "pri": edge["pri"], "patch": patch,
                "error": str(exc),
                "from_xz": list(a_node["xz"]),
                "to_xz": list(specmod.NODES[edge["b"]]["xz"]),
                "why": "A* found no admissible path. Either the endpoints are on "
                       "different land components (this seed has thousands), or "
                       "every corridor into the target is inside a protected "
                       "location's keep-out.",
            })
            print(f"{edge['id']:22s} p{edge['pri']} UNROUTABLE: {exc}")
            continue
        # A BRIDGED RUN LONGER THAN CROSSINGS CAN BUILD IS A REJECTED SEGMENT,
        # not a crossing to hand over.  MEASURED ceiling from Crossings' own
        # WearNTear implementation: 72 m free span at a 3x safety factor on an
        # iron stringer, 96 m absolute.  Handing over a 330 m "bridge" would be
        # the mirror image of the defect this project already paid for in the
        # other direction (a 12 m bridge planned over a 40 m river).
        worst_span = max((c["span_m"] for c in seg["crossings"]), default=0.0)
        if worst_span > MAX_BRIDGE_SPAN_M:
            seg["rejected"] = {
                "why": f"longest bridged run is {worst_span:.1f} m, past Crossings' "
                       f"MEASURED {MAX_BRIDGE_SPAN_M} m absolute span ceiling "
                       f"(96 m iron stringer). Re-route inland or make it a ferry.",
                "worst_span_m": worst_span,
            }
            rejected.append({"id": seg["id"], **seg["rejected"]})
            print(f"{seg['id']:22s} p{seg['pri']} REJECTED: {seg['rejected']['why']}")
            continue
        zs = zones_of_segment(seg)
        seg["zones"] = sorted([list(z) for z in zs])
        seg["zdo_cost"] = len(zs)
        segments.append(seg)
        print(f"{seg['id']:22s} p{seg['pri']} {seg['cls']:5s} {seg['length_m']:7.1f} m  "
              f"grade max {seg['grade_max_measured']*100:5.2f}%/{seg['grade_limit']*100:.0f}%  "
              f"cut {seg['cut_max_m']:6.2f} fill {seg['fill_max_m']:5.2f}  "
              f"clamp8 {seg['stations_over_clamp']:3d} margin7 {seg['stations_over_design_margin']:3d}  "
              f"brg {seg['bridged_stations']:3d}st/{len(seg['crossings'])}x  "
              f"zones {seg['zdo_cost']:3d}  poi-viol {len(seg['poi_hard_violations'])}")

    all_zones = sorted({tuple(z) for sg in segments for z in sg["zones"]})
    total_len = sum(sg["length_m"] for sg in segments)
    paved_m2 = sum(sg["terrain_stations"] * sg["station_m"] * sg["width_m"] for sg in segments)
    worst_grade = max((sg["grade_max_measured"] for sg in segments), default=0.0)
    over = sum(sg["stations_over_clamp"] for sg in segments)
    over_m = sum(sg["stations_over_design_margin"] for sg in segments)
    detected = [c for sg in segments for c in sg["crossings"]]

    doc = {
        "schema_version": 2, "owner": "RoadNet", "world": "Ulfsland", "seed": SEED,
        "archipelago_note": "The main continent is spawn island + west isle joined by "
                            "two bridges. Largest land component in the world is "
                            "5.4559 km2 at 16 m cells, bbox x[-1520,1904] z[-928,2720], "
                            "which is exactly the union of the two. MEASURED, "
                            "tools/jumpstart/roads/archipelago.py.",
        "height_source": {
            "tool": "tools/jumpstart/blueprints/run_patchscan.sh",
            "field": args.field, "resolution_m": 1.0, "rivers_included": True,
            "samples": 46112400, "bytes": 230562258, "seed_hash": 147627509,
            "why": "PatchScan initialises WorldGenerator with full pregeneration, so "
                   "WorldGenerator.AddRivers has the dictionary Pregenerate() fills. "
                   "The coarse *.biome grid on this host was made with "
                   "SEEDSCAN_PREGEN=0 and is RIVER-FREE; no number here comes from it.",
        },
        "grade_policy": {
            **routemod.GRADE, "ceiling": routemod.GRADE_CEILING,
            "status": "DESIGN, not measured",
            "measured_player_slide_deg": fieldmod.PLAYER_SLIDE_DEG,
            "measured_slide_source": "Character::GetSlideAngle returns literal 38 for "
                                     "IsPlayer (45 mounted, 90 monster)",
            "rationale": "38 deg is a 78 % grade -- the angle above which a player "
                         "SLIDES, not a walkable target. Vagon is a plain physics body "
                         "(m_baseMass, m_itemWeightMassFactor, m_breakForce; no slope "
                         "logic at all), so cart behaviour on a grade is momentum, not "
                         "a rule, and the limit has to come from engineering rather "
                         "than from the game. Historic cart roads held to <= 8 %.",
        },
        "clamp": {"measured_m": routemod.CLAMP_M, "margin_m": routemod.CLAMP_MARGIN_M,
                  "source": "TerrainComp::ApplyToHeightmap clamps the applied height to "
                            "the GENERATED height +/- 8 m",
                  "checked": "per sample, not per segment"},
        "crossing_contract_with_Crossings": {
            "bank_snap_m": 2.0, "stand_off_m": 0.0,
            "bank_level_run_m": BANK_LEVEL_M,
            "deck_datum_y": specmod.DECK_DATUM_Y,
            "centreline_straight_across_window": True,
            "ribbon_width_m": WIDTH_M,
        },
        "poi_keepout": {
            "dump": str(poimod.DUMP), "instances": len(pois),
            "declared_radius": "MEASURED per instance (exteriorRadius/interiorRadius)",
            "overshoot_m": poimod.OVERSHOOT_M,
            "overshoot_status": "INFERRED from ONE observation (pieces reaching 43 m "
                                "against a 32 m declared radius in the old world)",
            "road_margin_m": poimod.ROAD_MARGIN_M, "road_margin_status": "taste pick",
            "piece_pad_m": poimod.PIECE_PAD_M, "piece_pad_status": "taste pick",
            "protected_test": "prioritized or centerFirst or quantity <= 5 "
                              "(Settlements; there is no sacredness flag on "
                              "ZoneLocation and `unique` is FALSE even on bosslocation)",
            "residual_risk": "the dump is MOD-FREE and cannot see More_World_Locations' "
                             "~190 POI types. A ribbon clear of every vanilla instance "
                             "can still cross a mod POI. The ribbon DELETES nothing, so "
                             "the failure mode is mod pieces standing over levelled "
                             "ground -- still a defect. Only a live marker sweep along "
                             "the finished centreline after zones_generate settles it.",
            "per_corridor": poi_masked,
        },
        "totals": {
            "segments": len(segments),
            "length_m": round(total_len, 1),
            "paved_m2": round(paved_m2, 1),
            "zones_touched": len(all_zones),
            "zdo_cost": len(all_zones),
            "zdo_cost_basis": "ONE _TerrainCompiler ZDO per zone. "
                              "Heightmap::GetAndCreateTerrainCompiler returns the first "
                              "compiler in a zone, so a second is dead weight. Cost is "
                              "per ZONE, not per metre: the 956-ZDO-per-957-m figure was "
                              "the PIECE-based road this tool replaces.",
            "worst_grade": worst_grade,
            "stations_over_clamp": over,
            "stations_over_design_margin": over_m,
            "detected_crossings": len(detected),
            "declared_crossings": len(specmod.DECLARED_CROSSINGS),
            "unroutable_edges": len(unroutable),
            "rejected_segments": len(rejected),
        },
        "unroutable": unroutable,
        "rejected": rejected,
        "no_road": specmod.NO_ROAD,
        "segments": segments,
    }
    (out_dir / "segments.yaml").write_text(
        yaml.safe_dump(plain(doc), default_flow_style=False, sort_keys=False, width=200))

    xdoc = {
        "schema_version": 2, "owner": "RoadNet", "consumer": "Crossings",
        "world": "Ulfsland", "seed": SEED,
        "measurement": "generated heights from PatchScan at 1 m WITH rivers; bank "
                       "coordinates snapped to 2 m per Crossings' MEASURED deck pitch",
        "precedence": "DECLARED crossings carry Crossings' own measurements and are "
                      "authoritative. DETECTED crossings are mine, from routing over "
                      "the 1 m field. Where they describe the same channel, Crossings' "
                      "numbers win and mine are marked superseded.",
        "contract": {"ribbon_stops_at_bank": True, "stand_off_m": 0.0,
                     "bank_level_run_m": BANK_LEVEL_M,
                     "deck_datum_y": specmod.DECK_DATUM_Y,
                     "centreline_straight_across_window": True},
        "declared": specmod.DECLARED_CROSSINGS,
        "detected": detected,
        "abutment_handoffs": [a | {"segment_id": sg["id"], "cls": sg["cls"]}
                              for sg in segments for a in sg["abutments"]],
    }
    (out_dir / "crossings.yaml").write_text(
        yaml.safe_dump(plain(xdoc), default_flow_style=False, sort_keys=False, width=200))

    print(f"\nTOTAL {len(segments)} segments  {total_len:.0f} m  {paved_m2:.0f} m2 paved  "
          f"{len(all_zones)} zones = {len(all_zones)} _TerrainCompiler ZDOs")
    print(f"worst grade {worst_grade*100:.2f}%  over 8 m clamp {over}  over 7 m margin {over_m}  "
          f"detected crossings {len(detected)}  declared {len(specmod.DECLARED_CROSSINGS)}")
    viol = [(sg["id"], r) for sg in segments for r in sg["poi_hard_violations"]]
    print(f"POI hard-radius violations: {len(viol)}")
    for sid, r in viol[:10]:
        print(f"   {sid} {r['name']} at {r['xz']} dist {r['centreline_dist_m']} m "
              f"hard {r['hard_m']} m protected={r['protected']}")
    print(f"-> {out_dir/'segments.yaml'}  {out_dir/'crossings.yaml'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
