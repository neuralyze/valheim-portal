#!/usr/bin/env python3
"""RE-ROUTE A WRITTEN SEGMENT AROUND A BUILDING, and PROVE the deviation
before anything is written.

WHY THIS EXISTS.  `structures.py` measures the defect the operator reported:
464 building pieces stand in a carriageway on a stretch of road that CONTINUES
PAST them, 294 of them ours at Stenvik and 170 of them `More_World_Locations`
bodies.  That is a ROUTING defect, not a clearing gap -- `clear.py` was always
going to refuse a building and `ribbon.py`'s `PadKeepOut` was always going to
clip the terrain at a pad edge, which is why the operator sees flat road on
both sides and a hut in the middle.  Nothing in this family fixes an
ALIGNMENT, so nothing fixed it.

WHAT THE ROUTER GOT WRONG, MEASURED.  `network.build_segment` prices its
corridor with `poi.masks`, which is fed from the world GENERATOR's location
dump.  Our own settlement bodies are not location instances, so they are not
in the mask at any radius -- and a segment that merely PASSES a town is never
told the town exists.  T4-meadhall-stathub's own audit reports
`poi_within_keepout: []` while its centreline passes 0.10 m from
`stenvik-cottage-1`'s centre, 0.68 m from `stenvik-cottage-3`'s and 1.53 m
from `stenvik-beehive-1`'s.  The audit was right about what it measured.

THE KEEP-OUT HERE IS BUILT FROM MEASURED PIECE POSITIONS, not from a radius.
Every earlier attempt in this family to describe a body with one number has
failed in the same direction: `pad_radius_m: 100.0` is a DISTRICT and reading
it as a foundation cost T4 293.9 m of unwritten centreline; a circumscribing
circle over `stenvik-longhouse-2`'s 14.4 x 23.0 m pad over-claims 39 m2 of
street.  So the forbidden set is the union of

  * every OUR-claim pad RECTANGLE at its written `pad_w x pad_d` and yaw, and
  * every STRUCTURE/FIXTURE piece the RCON census actually found, as a point,

each dilated by the half-width the ribbon needs there plus a stated margin.
A deviation that clears THOSE clears the thing a player walks into.

WHAT IT REFUSES TO DO.  It never proposes a line through a pad rectangle, it
never accepts a deviation whose profile exceeds the segment's own recorded
grade limit, and it never writes.  It prints the measurement and emits the
replacement node list; `ribbon.py build --repair-of` does the writing.

READ-ONLY.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import field as fieldmod  # noqa: E402
import route as routemod  # noqa: E402
import poi as poimod  # noqa: E402
import structures as ST  # noqa: E402

PATCH_FILE = Path("/tmp/roads/h1m.bin")

# HOW FAR BEYOND THE CONFLICT THE DEVIATION IS ALLOWED TO START, i.e. how much
# of the existing alignment it may rewrite on each side.  A deviation has to
# leave and rejoin the old line tangentially enough that the profile solver
# can absorb the height difference: the permitted cut/fill is 7.0 m and the
# trunk grade is 0.08, so a 60 m run is enough to absorb 4.8 m of datum
# change, which is more than the worst pad-to-pad height difference in
# Stenvik's east district (3.25 m, MEASURED: target_y 46.20 at longhouse-2 to
# 49.45 at stonehouse-1).
BRACKET_M = 60.0

# Two conflicts closer together than this are ONE deviation: a line that
# leaves the alignment, rejoins it for 40 m and leaves again is worse to walk
# than one that stays off it, and it doubles the number of tangency joints.
MERGE_GAP_M = 2 * BRACKET_M

# MARGIN BEYOND THE WRITTEN FOOTPRINT, and it is not decoration.  The ribbon
# writes `width/2 + shoulder + batter run + 1 m of spread`, and the batter run
# is what moves ground under a body's feet: `SeatCheck` MEASURED the u-lheast
# hub portal floating 0.536 m because a ribbon cut under it after it was
# seated.  3 m keeps the batter toe off the body rather than keeping the
# carriageway off it.
BODY_MARGIN_M = 3.0

# HUG THE EXISTING ALIGNMENT, and this is the single most important policy in
# this module because without it the router answers a DIFFERENT QUESTION.
#
# MEASURED: left to minimise its own cost, A* answered T4's Stenvik conflict
# with an 87.3 m-offset, 43.5 m-mean BYPASS -- a new road that abandons the
# whole east district and walks around the outside of the town.  That is a
# correct solution to "find the cheapest line from A to B avoiding these
# bodies" and a wrong solution to the operator's problem.  Their words were
# "the rest of stenvik streets are good": they like the road through the town
# and want the huts out of it.  A bypass deletes the street they like AND
# leaves the old carriageway behind as a 400 m ghost, because
# `ribbon.py`'s union carries every prior sample forward verbatim and there is
# no operation in this family that unwrites one.
#
# So distance from the OLD centreline is priced as a soft penalty, on the same
# additive [0, 1] scale and through the same `poi_weight` the POI decay uses.
# A deviation then costs itself for every metre it strays and takes the
# narrowest swerve the keep-outs permit.  `HUG_REF_M` is the distance at which
# the penalty saturates: 40 m, which is wider than the widest body half-extent
# plus the corridor it needs, so a forced swerve is never priced at the cap
# while it is still being forced.
HUG_REF_M = 40.0


def load_field(patch: str):
    if not PATCH_FILE.exists():
        raise SystemExit(f"{PATCH_FILE} is missing: the 1 m PatchScan field is "
                         f"the height source for every number here and there "
                         f"is no fallback that measures the same thing")
    return fieldmod.load(PATCH_FILE)[patch]


def body_points(report: dict, seg_id: str) -> list[dict]:
    """Every STRUCTURE/FIXTURE piece `structures.py` found near ANY segment.

    ANY segment, not just this one: a deviation that leaves T4 to miss a hut
    must not arrive inside the body T8 clips 40 m away.  The census corpus is
    per segment, so the union over all fifteen is the widest measured view of
    what stands near this network that exists.
    """
    out = []
    for sid, s in report["segments"].items():
        for h in s["hits"]:
            out.append(h)
    return out


def pad_rects(claims: ST.Claims) -> list[dict]:
    return [p for p in claims.pads if p["w"] > 0.0]


def build_forbid(corr, pads: list[dict], pieces: list[dict],
                 half_m: float, margin_m: float = BODY_MARGIN_M
                 ) -> tuple[np.ndarray, dict]:
    """Rasterise pad rectangles and measured piece positions onto the grid.

    The dilation is `half_m + BODY_MARGIN_M + cell slack`.  The cell slack is
    the same geometric bound `poi.masks` documents: A* returns cell CENTRES and
    `simplify` then cuts chords to 1 m, so a finished centreline can sit
    `cell/sqrt(2) + 1` inside a cell whose centre was outside the mask.  Not
    adding it is the measured defect where "the mask was correct and the line
    still breached it".
    """
    m = corr.m
    grow = half_m + margin_m + corr.cell_m / math.sqrt(2.0) + 1.0
    jj, ii = np.meshgrid(np.arange(m), np.arange(m))
    X = corr.x0 + jj * corr.cell_m
    Z = corr.z0 + ii * corr.cell_m
    forbid = np.zeros((m, m), dtype=bool)
    hit_pads = []
    for p in pads:
        a = math.radians(p["yaw"])
        dx, dz = X - p["x"], Z - p["z"]
        u = dx * math.cos(a) + dz * math.sin(a)
        v = -dx * math.sin(a) + dz * math.cos(a)
        inside = ((np.abs(u) <= p["w"] / 2.0 + grow)
                  & (np.abs(v) <= p["d"] / 2.0 + grow))
        if inside.any():
            forbid |= inside
            hit_pads.append(p["site_id"])
    npieces = 0
    for h in pieces:
        d2 = (X - h["x"]) ** 2 + (Z - h["z"]) ** 2
        near = d2 <= grow * grow
        if near.any():
            forbid |= near
            npieces += 1
    return forbid, {"grow_m": round(grow, 2), "pads_on_grid": sorted(set(hit_pads)),
                    "pieces_on_grid": npieces,
                    "cells_forbidden": int(forbid.sum())}


def windows(report: dict, seg_id: str) -> list[tuple[float, float, list[str]]]:
    """Merged along-track windows of the THROUGH conflicts on one segment."""
    s = report["segments"][seg_id]
    keep = [e for e in s["by_claim"] if e["verdict_scope"] == "THROUGH"
            and e["bands"].get("CARRIAGEWAY", 0) > 0]
    spans = sorted((e["along_min_m"], e["along_max_m"], e["site_id"])
                   for e in keep)
    out: list[list] = []
    for a, b, who in spans:
        if out and a - out[-1][1] <= MERGE_GAP_M:
            out[-1][1] = max(out[-1][1], b)
            out[-1][2].append(who)
        else:
            out.append([a, b, [who]])
    return [(a, b, w) for a, b, w in out]


def along_of(nodes: np.ndarray) -> np.ndarray:
    seglen = np.hypot(np.diff(nodes[:, 0]), np.diff(nodes[:, 1]))
    return np.concatenate(([0.0], np.cumsum(seglen)))


def grade_report(t: np.ndarray, s: np.ndarray, baseline_m: float) -> dict:
    """Worst and mean |dy/dx| over a baseline, plus the 1 m figure beside it.

    BOTH, always: the 1 m figure is what the 38 deg slide angle (0.781) acts
    on and the 8 m figure is what a player feels as a hill, and reporting
    either alone has misled this project.
    """
    out = {}
    for name, base in (("1m", 1.0), (f"{baseline_m:g}m", baseline_m)):
        g = []
        j = 0
        for i in range(len(s)):
            while j < len(s) - 1 and s[j] - s[i] < base:
                j += 1
            if s[j] - s[i] <= 0:
                continue
            g.append(abs(t[j] - t[i]) / (s[j] - s[i]))
        g = np.asarray(g) if g else np.zeros(1)
        out[name] = {"max": round(float(g.max()), 3),
                     "mean": round(float(g.mean()), 3)}
    return out


def clearance(nodes: np.ndarray, pads: list[dict], pieces: list[dict]
              ) -> dict:
    """Worst approach of a polyline to any pad rectangle or measured piece.

    Distance to a RECTANGLE, not to its centre: the point-to-rectangle
    distance in the pad's own frame, so a long body is not credited with the
    clearance of its short axis.
    """
    import ribbon as RB
    prof = np.zeros(len(nodes))
    br = np.zeros(len(nodes), dtype=bool)
    worst_pad = (9e9, "-")
    for p in pads:
        a = math.radians(p["yaw"])
        best = 9e9
        for x, z in nodes:
            dx, dz = x - p["x"], z - p["z"]
            u = abs(dx * math.cos(a) + dz * math.sin(a)) - p["w"] / 2.0
            v = abs(-dx * math.sin(a) + dz * math.cos(a)) - p["d"] / 2.0
            d = math.hypot(max(u, 0.0), max(v, 0.0))
            if u < 0 and v < 0:
                d = -min(-u, -v)
            best = min(best, d)
        if best < worst_pad[0]:
            worst_pad = (best, p["site_id"])
    worst_pc = (9e9, "-")
    for h in pieces:
        r = RB.lateral_and_y(nodes, prof, br, h["x"], h["z"])
        if r is None:
            continue
        if r[0] < worst_pc[0]:
            worst_pc = (float(r[0]), f"{h['prefab']}@{h['x']:.1f},{h['z']:.1f}")
    return {"worst_pad_m": round(worst_pad[0], 2), "worst_pad": worst_pad[1],
            "worst_piece_m": round(worst_pc[0], 2), "worst_piece": worst_pc[1]}


def deviate(seg: dict, report: dict, claims: ST.Claims, pieces: list[dict],
            patch: str, *, margin_m: float = BODY_MARGIN_M,
            bracket_m: float = BRACKET_M, hug: bool = True) -> dict:
    fld = load_field(patch)
    half = seg["width_m"] / 2.0 + 1.0  # carriageway half + shoulder
    g_max = float(seg.get("grade_limit") or 0.08)
    corr = routemod.Corridor(fld=fld, cell_m=4.0, grade_max=g_max)
    pois = poimod.load()
    pf, ps, _touch = poimod.masks(pois, corr.x0, corr.z0, corr.cell_m, corr.m,
                                  half)
    own, meta = build_forbid(corr, pad_rects(claims), pieces, half,
                             margin_m=margin_m)
    corr.forbid = pf | own
    nodes = np.asarray(seg["nodes"], dtype=np.float64)
    al = along_of(nodes)
    if hug:
        # Distance from every grid cell to the OLD alignment, by EUCLIDEAN
        # DISTANCE TRANSFORM over the cells the old alignment occupies.
        #
        # Not by clamped projection onto each old polyline segment, which is
        # how `ribbon.lateral_and_y_grid` does it and what this used to do:
        # that is O(stations x cells) and T4 is 763 stations over a 1,217 x
        # 1,217 cell grid, i.e. 1.1 billion vector elements for ONE segment.
        # MEASURED: the run did not finish.  The transform is O(cells) and
        # answers the same question to within half a cell, because the
        # stations are 2 m apart on a 4 m grid so consecutive stations land in
        # the same or adjacent cells and the rasterised line has no gaps.
        from scipy import ndimage  # noqa: PLC0415
        seed = np.ones((corr.m, corr.m), dtype=bool)
        ji = np.rint((nodes[:, 0] - corr.x0) / corr.cell_m).astype(int)
        ii = np.rint((nodes[:, 1] - corr.z0) / corr.cell_m).astype(int)
        keep = (ii >= 0) & (ii < corr.m) & (ji >= 0) & (ji < corr.m)
        seed[ii[keep], ji[keep]] = False
        dmin = ndimage.distance_transform_edt(seed) * corr.cell_m
        hug_soft = np.clip(dmin / HUG_REF_M, 0.0, 1.0)
        corr.soft = np.maximum(ps, hug_soft)
        meta["hug_ref_m"] = HUG_REF_M
        meta["hug_seed_cells"] = int((~seed).sum())
    else:
        corr.soft = ps
    meta["margin_m"] = margin_m
    meta["bracket_m"] = bracket_m
    out = {"segment": seg["id"], "grade_limit": g_max, "mask": meta,
           "deviations": []}
    # THE ANCHOR IS WHERE THE ROAD ALREADY IS, so it cannot be forbidden.  A
    # deviation whose bracket reaches the segment's own terminus starts inside
    # the destination's own keep-out by construction -- T8 begins at Stenvik's
    # square, 0 m from the town, so its first anchor is 100 % masked and A*
    # answered "no route found" for a corridor that certainly exists.
    #
    # THE EXEMPTION IS RECORDED, never silent, because a mask that quietly
    # narrows is the defect class this project has paid for four times.  Each
    # anchor gets a disc of exactly `grow` cleared around it, and the record
    # says which anchor, how big the disc was and how many cells it returned.
    jj, ii = np.meshgrid(np.arange(corr.m), np.arange(corr.m))
    GX = corr.x0 + jj * corr.cell_m
    GZ = corr.z0 + ii * corr.cell_m
    grow = float(meta["grow_m"])
    meta["anchor_exemptions"] = []

    def exempt(px: float, pz: float, why: str) -> None:
        disc = ((GX - px) ** 2 + (GZ - pz) ** 2) <= grow * grow
        freed = int((corr.forbid & disc).sum())
        if freed:
            corr.forbid = corr.forbid & ~disc
        meta["anchor_exemptions"].append({
            "at": [round(px, 2), round(pz, 2)], "radius_m": round(grow, 2),
            "cells_freed": freed, "why": why})

    for a_m, b_m, who in windows(report, seg["id"]):
        ia = int(np.searchsorted(al, max(0.0, a_m - bracket_m)))
        ib = int(np.searchsorted(al, min(al[-1], b_m + bracket_m)))
        ia = max(0, min(ia, len(nodes) - 1))
        ib = max(0, min(ib, len(nodes) - 1))
        anchor_a = (float(nodes[ia, 0]), float(nodes[ia, 1]))
        anchor_b = (float(nodes[ib, 0]), float(nodes[ib, 1]))
        exempt(*anchor_a, why=f"{seg['id']} station {ia} is on the existing "
                              f"alignment and is the join this deviation must "
                              f"make")
        exempt(*anchor_b, why=f"{seg['id']} station {ib} is on the existing "
                              f"alignment and is the join this deviation must "
                              f"make")
        rec = {"conflicts": who, "window_m": [a_m, b_m],
               "anchor_station": [ia, ib],
               "anchor_a": anchor_a, "anchor_b": anchor_b,
               "old_run_m": round(float(al[ib] - al[ia]), 1)}
        try:
            raw = corr.search(anchor_a, anchor_b)
        except Exception as exc:  # noqa: BLE001
            rec["verdict"] = "NO_CORRIDOR"
            rec["why"] = f"{type(exc).__name__}: {exc}"
            out["deviations"].append(rec)
            continue
        poly = routemod.simplify(raw, tol_m=1.0)
        pts, s = routemod.resample(poly, seg["station_m"])
        pts_a = np.asarray(pts, dtype=np.float64)
        s = np.asarray(s, dtype=np.float64)
        t = np.array([fld.bilinear(x, z) for x, z in pts], dtype=np.float64)
        fit = routemod.fit_profile(t, s, g_max)
        rec["new_run_m"] = round(float(s[-1]), 1)
        rec["detour_m"] = round(float(s[-1]) - rec["old_run_m"], 1)
        rec["stations"] = len(pts)
        rec["infeasible_stations"] = len(fit.get("infeasible_spans") or [])
        rec["terrain_grade"] = grade_report(t, s, 8.0)
        prof = np.asarray(fit["y"], dtype=np.float64)
        rec["profile_grade"] = grade_report(prof, s, 8.0)
        rec["cut_max_m"] = round(float(np.max(prof - t)), 2)
        rec["fill_max_m"] = round(float(np.max(t - prof)), 2)
        rec["clearance"] = clearance(pts_a, pad_rects(claims), pieces)
        rec["nodes"] = [[round(x, 2), round(z, 2)] for x, z in pts]
        ok = (rec["infeasible_stations"] == 0
              and rec["clearance"]["worst_pad_m"] >= margin_m
              and rec["clearance"]["worst_piece_m"] >= half)
        rec["verdict"] = "REROUTE_OK" if ok else "REROUTE_FAILS_GATE"
        out["deviations"].append(rec)
    return out


def splice(seg: dict, dev: dict, patch: str) -> dict:
    """A COMPLETE, self-consistent segment record for the deviated alignment.

    A deviation is a polyline; `ribbon.py build` needs a SEGMENT: nodes,
    `profile_y`, `terrain_y`, `is_bridge`, the grade audit and the crossing
    declarations, all mutually consistent.  Handing it a segment whose nodes
    moved and whose profile did not is the "check that answers a question it
    is not asked" defect with a 1.5 km blast radius: the rasteriser would
    stamp the OLD heights along the NEW line.

    SO THE WHOLE PROFILE IS RE-FITTED, not patched.  `fit_profile` is a global
    interval propagation -- a grade limit couples every station to every other
    one -- so a spliced middle changes the feasible band at the ends too.
    Re-fitting is also what proves the deviation: an infeasible span is a PROOF
    that no slope-limited profile exists inside the clamp, and a spliced
    profile that was never re-proven is an assumption.

    THE RETAINED NODES ARE COPIED VERBATIM, which is the whole point of
    splicing rather than re-running `network.build_segment`.  A full rebuild
    re-searches the corridor end to end and moves the alignment everywhere,
    and every metre it moves is a metre of ALREADY-WRITTEN carriageway left
    behind: `ribbon.py`'s union carries prior samples forward verbatim and
    nothing in this family unwrites one.  Splicing confines the abandoned
    surface to the conflict window.

    THE DECK IS PINNED, NOT RE-CHOSEN.  Each crossing this segment already
    declared keeps its recorded `required_deck_y` over its own window, located
    by BANK POSITION rather than by station index because the splice
    renumbers stations.  Crossings owns that datum; re-deriving it here would
    be a second source of truth for a number another agent publishes.
    """
    # LOADED BY PATH, and the plain `import network` this replaces was a
    # MEASURED defect, not a style preference.  `ribbon.py` inserts
    # `tools/jumpstart` onto `sys.path` at import time, and that directory
    # holds a PACKAGE called `network/` -- so `import network` resolved to
    # `tools/jumpstart/network/__init__.py` and `_station_of` vanished with an
    # AttributeError.  Two modules with one name and the winner decided by
    # import order is exactly the class of bug this project keeps paying for,
    # so the sibling is addressed by its file rather than by its name.
    import importlib.util as _ilu  # noqa: PLC0415
    _spec = _ilu.spec_from_file_location("roads_network", HERE / "network.py")
    NW = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(NW)

    fld = load_field(patch)
    ia, ib = dev["anchor_station"]
    old = [list(p) for p in seg["nodes"]]
    dev_pts = [list(p) for p in dev["nodes"]]
    # CONCATENATED, NOT RE-RESAMPLED, and this is a defect fix.  Resampling
    # the whole spliced polyline moves EVERY retained station by a fraction of
    # a metre, which re-samples the terrain under it and lets the global
    # profile fit drift outside the window.  MEASURED: the first version
    # re-resampled, `stations_over_clamp` still read 0 because that counts the
    # CENTRELINE, and `ribbon.py --validate` then refused the write for one
    # CARRIAGEWAY SAMPLE at (636, 1094) needing -8.000 m -- exactly the
    # measured apply clamp -- 24 m from the stathub terminus and 400 m from
    # anything this deviation touches.  A centreline count is not a surface
    # count, and a splice that moves ground it was not asked to move is how a
    # local repair becomes a global regression.
    pts = old[:ia] + dev_pts + old[ib + 1:]
    # ARC LENGTH, NOT CHORD LENGTH, and the difference is a factor of two at a
    # turn.  `routemod.resample` places stations every `station_m` of ARC along
    # the polyline, so around a vertex two consecutive stations are 2 m apart
    # ALONG THE ROAD while their straight-line separation is as little as
    # 0.86 m -- MEASURED on T4's own stored nodes.  Deriving `s` from chords
    # therefore reports a 0.14 m rise over 0.866 m as a 0.16 grade where the
    # road actually climbs at 0.07, and `fit_profile` then PROVED the
    # already-built profile infeasible at 255 of 763 stations.  That is a
    # measurement error masquerading as a structural verdict, and it is the
    # same class as every other one in this family: the instrument answered a
    # question about a chord when it was asked about a road.
    #
    # Every step is `station_m` because both halves were resampled at
    # `station_m`; only the two JOINT steps are a real chord, because that pair
    # was never resampled together.
    step = np.full(len(pts) - 1, float(seg["station_m"]))
    P = np.asarray(pts, dtype=np.float64)
    for j in (ia - 1, ia + len(dev_pts) - 1):
        if 0 <= j < len(step):
            step[j] = float(np.hypot(P[j + 1, 0] - P[j, 0],
                                     P[j + 1, 1] - P[j, 1]))
    s = np.concatenate(([0.0], np.cumsum(step)))
    t = np.array([fld.bilinear(x, z) for x, z in pts], dtype=np.float64)
    # THE RETAINED PROFILE IS PINNED TO THE HEIGHTS THAT WERE ALREADY BUILT.
    # `fit_profile` is a global interval propagation, so without this the
    # re-fit is free to move the profile a kilometre away from the deviation
    # -- and the ground it would move there is ground a player already walks
    # and that `ribbon.py` already proved inside the clamp.  Pinning makes the
    # deviation window the ONLY thing that changes and turns the grade limit
    # at the two joints into a constraint the fit must PROVE rather than an
    # assumption.
    retained = ([(k, k) for k in range(ia)]
                + [(len(pts) - (len(old) - k), k)
                   for k in range(ib + 1, len(old))])
    old_prof = np.asarray(seg["profile_y"], dtype=np.float64)

    CF = routemod.CUT_FILL_MAX
    WL = 30.0  # c_WaterLevel, MEASURED; network.py's WATER_LEVEL
    lo = np.maximum(t - CF, WL + routemod.ROAD_FREEBOARD_M)
    hi = t + CF
    free = np.zeros(len(pts), dtype=bool)
    pinned = []
    for c in seg.get("crossings") or []:
        ka = NW._station_of(pts, c["bank_a"]["xz"])
        kb = NW._station_of(pts, c["bank_b"]["xz"])
        ka, kb = min(ka, kb), max(ka, kb)
        free[ka:kb + 1] = True
        wa = max(0, int(np.searchsorted(s, s[ka] - NW.BANK_LEVEL_M)))
        wb = min(len(pts) - 1, int(np.searchsorted(s, s[kb] + NW.BANK_LEVEL_M)))
        H = c.get("required_deck_y") or c["bank_a"].get("road_deck_y")
        if H is None:
            raise SystemExit(f"{seg['id']} crossing {c.get('crossing_id')} has "
                             f"no recorded deck height to pin to")
        lo[wa:wb + 1] = float(H)
        hi[wa:wb + 1] = float(H)
        pinned.append({"crossing_id": c.get("crossing_id"),
                       "stations": [int(ka), int(kb)],
                       "level_window": [int(wa), int(wb)],
                       "pinned_deck_y": float(H)})
    for ab in seg.get("abutments") or []:
        H = float(ab["level_y"])
        if ab["end"] == "a":
            sel = np.nonzero(s <= s[0] + NW.BANK_LEVEL_M)[0]
        else:
            sel = np.nonzero(s >= s[-1] - NW.BANK_LEVEL_M)[0]
        lo[sel] = H
        hi[sel] = H
        pinned.append({"abutment": ab["node"], "end": ab["end"],
                       "pinned_level_y": H,
                       "stations": [int(sel.min()), int(sel.max())]})

    # A BAND, NOT AN EXACT PIN, and the width of it is forced by the file
    # rather than chosen.  `segments.yaml` stores `profile_y` ROUNDED TO 3
    # DECIMALS, and T4's measured grade sits exactly ON its 0.08 limit, so two
    # adjacent stored heights can differ by 0.1601 m over 2.0 m = 0.08005 --
    # over the limit by rounding alone.  MEASURED: pinning `lo == hi` to the
    # stored values made `fit_profile` prove 25 spans INFEASIBLE across a
    # kilometre of road that is already built and already inside the clamp.
    # 0.05 m absorbs the 0.0005 m quantum with three orders of margin while
    # keeping every retained station within 5 cm of the height a player is
    # standing on now.
    PIN_EPS_M = 0.05
    # AND THE JOINTS GET A BLEND RUN, because a deviation has to ARRIVE at the
    # retained profile and the grade limit decides how fast it may.  MEASURED:
    # pinning right up to the joint left `fit_profile` proving two spans
    # infeasible at stations (254, 262) and (377, 403) -- inside the RETAINED
    # head, where the old profile is a feasible point -- because interval
    # propagation is bidirectional and an unreachable height at the joint
    # propagates backwards until something can absorb it.  The absorbing
    # distance is the clamp over the grade: 7.0 m of permitted cut at 0.08 is
    # 87.5 m, so 100 m is that bound rounded up.  Inside the blend the station
    # keeps its ordinary terrain-derived band; outside it the built road does
    # not move.
    PIN_BLEND_M = 100.0
    joints = [float(s[ia]), float(s[ia + len(dev_pts) - 1])]
    pinned_n = 0
    for knew, kold in retained:
        if not (0 <= knew < len(pts)):
            continue
        if min(abs(float(s[knew]) - j) for j in joints) <= PIN_BLEND_M:
            continue
        lo[knew] = float(old_prof[kold]) - PIN_EPS_M
        hi[knew] = float(old_prof[kold]) + PIN_EPS_M
        pinned_n += 1
    g_max = float(seg["grade_limit"])
    fit = routemod.fit_profile(t, s, g_max, lo=lo, hi=hi)
    bad = [(p, q) for p, q in fit["infeasible_spans"] if not free[p:q + 1].all()]
    y = np.asarray(fit["y"], dtype=np.float64)
    grade = np.abs(np.diff(y)) / np.maximum(np.diff(s), 1e-9)
    cf = (y - t)[~free]
    out = dict(seg)
    out.update({
        "length_m": round(float(s[-1]), 1),
        "grade_max_measured": round(float(grade.max()), 4),
        "grade_p95": round(float(np.percentile(grade, 95)), 4),
        "grade_mean": round(float(grade.mean()), 4),
        "cut_max_m": round(float(cf.min()), 2),
        "fill_max_m": round(float(cf.max()), 2),
        "stations_over_clamp": int((np.abs(cf) > routemod.CLAMP_M).sum()),
        "stations_over_design_margin": int((np.abs(cf) > CF).sum()),
        "terrain_stations": int((~free).sum()),
        "bridged_stations": int(free.sum()),
        "nodes": [[round(float(x), 2), round(float(z), 2)] for x, z in pts],
        "profile_y": [round(float(v), 3) for v in y],
        "terrain_y": [round(float(v), 3) for v in t],
        "is_bridge": [bool(v) for v in free],
        # `zones_of_segment`, NOT `ribbon.segment_zones`.  This field is
        # informational -- `ribbon.py` main recomputes the write set itself
        # from `segment_zones` with the batter reach -- and every other
        # segment in this file carries the narrower carriageway-only set that
        # `network.py` put there.  Filling one record from a different
        # function would make the column mean two things.
        "zones": sorted([list(z) for z in NW.zones_of_segment(
            {"width_m": seg["width_m"],
             "nodes": [[float(x), float(z)] for x, z in pts],
             "is_bridge": [bool(v) for v in free]})]),
        "reroute": (seg.get("reroute") or []) + [{
            "of": seg["id"],
            "why": "the operator reported building structures standing in the "
                   "carriageway; this deviation is the ROUTING fix, and no "
                   "building was removed to make it",
            "conflicts_cleared": sorted(set(dev["conflicts"])),
            "anchor_station_old": [ia, ib],
            "old_window_run_m": dev["old_run_m"],
            "new_window_run_m": dev["new_run_m"],
            "detour_m": dev["detour_m"],
            "clearance": dev["clearance"],
            "infeasible_spans_after_splice": [[int(p), int(q)] for p, q in bad],
            "pinned": pinned,
            "retained_stations_pinned": pinned_n,
            "pin_band_m": PIN_EPS_M,
            "pin_blend_m": PIN_BLEND_M,
            "instrument": "roads/deviate.py::splice",
        }],
    })
    if bad:
        raise SystemExit(
            f"{seg['id']} spliced profile is INFEASIBLE over {bad} -- refusing "
            f"to write a segment record whose profile is not proven; a road "
            f"that cannot hold its grade inside the clamp is not a fix")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", default="/tmp/roadclean/structures_network.json")
    ap.add_argument("--segment", action="append", required=True)
    ap.add_argument("--out", default="/tmp/roadclean/deviations.json")
    ap.add_argument("--margin", type=float, default=BODY_MARGIN_M,
                    help="metres of clearance demanded BEYOND the ribbon's "
                         "own written half-width. The default keeps the "
                         "batter toe off a body; 0 makes the pad edge the "
                         "property line, which is how a village street "
                         "works and is the only way a line threads a dense "
                         "district.")
    ap.add_argument("--bracket", type=float, default=BRACKET_M,
                    help="metres of existing alignment the deviation may "
                         "rewrite on each side of the conflict")
    ap.add_argument("--splice", metavar="SEG:INDEX", action="append",
                    help="write the deviated alignment for SEG's INDEX'th "
                         "deviation back into segments.yaml, as a complete "
                         "re-fitted segment record. Repeatable. Nothing is "
                         "sent to the world: `ribbon.py build --repair-of` "
                         "does that, against the record this writes.")
    ap.add_argument("--no-hug", action="store_true",
                    help="drop the stay-near-the-old-line penalty. Answers "
                         "'cheapest line avoiding these bodies', which is a "
                         "DIFFERENT question: on T4 it returns an 87 m bypass "
                         "of the town whose street the operator likes.")
    a = ap.parse_args()

    report = json.loads(Path(a.report).read_text())
    segs = ST.segment_map()
    claims = ST.Claims()
    for sid in ST.WRITTEN:
        claims.add_proxies(json.loads(
            Path(report["sources"][sid]).read_text())["objects"])
    pieces = body_points(report, "")

    doc = yaml.safe_load(ST.SEGMENTS.read_text())
    patch_of = {s["id"]: s["patch"] for s in doc["segments"]}

    all_out = {}
    for sid in a.segment:
        res = deviate(segs[sid], report, claims, pieces, patch_of[sid],
                      margin_m=a.margin, bracket_m=a.bracket,
                      hug=not a.no_hug)
        all_out[sid] = res
        print(f"=== {sid}  grade_limit {res['grade_limit']}  "
              f"mask {res['mask']['cells_forbidden']} cells "
              f"(grow {res['mask']['grow_m']} m, "
              f"{res['mask']['pieces_on_grid']} pieces, "
              f"{len(res['mask']['pads_on_grid'])} pads)")
        for d in res["deviations"]:
            print(f"  {d['verdict']:20s} {','.join(sorted(set(d['conflicts'])))}")
            print(f"    window {d['window_m'][0]:.0f}-{d['window_m'][1]:.0f} m  "
                  f"anchors st{d['anchor_station'][0]}-{d['anchor_station'][1]}")
            if "new_run_m" in d:
                print(f"    old {d['old_run_m']} m -> new {d['new_run_m']} m "
                      f"(detour {d['detour_m']:+.1f} m), "
                      f"infeasible {d['infeasible_stations']}")
                print(f"    profile grade 8m max {d['profile_grade']['8m']['max']} "
                      f"(1m max {d['profile_grade']['1m']['max']}), "
                      f"cut {d['cut_max_m']} fill {d['fill_max_m']}")
                print(f"    clearance pad {d['clearance']['worst_pad_m']} m "
                      f"({d['clearance']['worst_pad']}), piece "
                      f"{d['clearance']['worst_piece_m']} m "
                      f"({d['clearance']['worst_piece']})")
            else:
                print(f"    {d.get('why')}")
    Path(a.out).write_text(json.dumps(all_out, indent=1))
    print(f"wrote {a.out}")

    order = sorted(a.splice or [],
                   key=lambda sp: -all_out[sp.split(":")[0]]["deviations"][
                       int(sp.split(":")[1])]["anchor_station"][0])
    for spec in order:
        sid, _, idx = spec.partition(":")
        d = all_out[sid]["deviations"][int(idx)]
        if d["verdict"] != "REROUTE_OK":
            raise SystemExit(f"{spec} is {d['verdict']}, not REROUTE_OK -- "
                             f"refusing to splice an unproven deviation")
        # RE-READ THE RECORD FROM DISK, never the map loaded at start-up.
        # Two deviations on one segment are spliced one after the other and
        # the first splice renumbers every station after it; splicing the
        # second against the start-up snapshot would place it by an index that
        # no longer means what it meant.  Splice DESCENDING by station so the
        # indices of the ones still to come are the ones that did not move.
        doc2 = yaml.safe_load(ST.SEGMENTS.read_text())
        cur = next(sg for sg in doc2["segments"] if sg["id"] == sid)
        rec = splice(cur, d, patch_of[sid])
        for k, sg in enumerate(doc2["segments"]):
            if sg["id"] == sid:
                doc2["segments"][k] = rec
                break
        else:
            raise SystemExit(f"no {sid} in {ST.SEGMENTS}")
        ST.SEGMENTS.write_text(yaml.safe_dump(doc2, sort_keys=True,
                                              default_flow_style=False))
        print(f"spliced {sid} deviation {idx}: length {rec['length_m']} m, "
              f"grade_max {rec['grade_max_measured']}, "
              f"cut {rec['cut_max_m']} fill {rec['fill_max_m']}, "
              f"over_clamp {rec['stations_over_clamp']}, "
              f"zones {len(rec['zones'])} -> {ST.SEGMENTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
