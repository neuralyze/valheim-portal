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
JUMPSTART = HERE.parent
# ORDER MATTERS AND IT BIT TWICE.  `tools/jumpstart/network/` is a PACKAGE and
# `tools/jumpstart/roads/network.py` is a module; whichever directory comes
# FIRST on sys.path wins `import network`.
#
# So JUMPSTART is APPENDED, never inserted.  The obvious spelling --
# `insert(0, JUMPSTART)` guarded by `not in sys.path` -- puts the package
# ahead of this directory when run AS A SCRIPT, because Python has already
# put the script's own directory at sys.path[0] and the guard then skips it
# while still inserting JUMPSTART in front. MEASURED: `python3 north.py plan`
# died on `module 'network' has no attribute 'MAX_BRIDGE_SPAN_M'`, i.e. it had
# imported the package. JUMPSTART only has to supply `worldseed`, which no
# other directory defines, so the end of the path is the right place for it.
sys.path.insert(0, str(HERE))
if str(JUMPSTART) not in sys.path:
    sys.path.append(str(JUMPSTART))

import field as fieldmod  # noqa: E402
import route as routemod  # noqa: E402
import poi as poimod  # noqa: E402
import precensus as censusmod  # noqa: E402
import structures as ST  # noqa: E402
import worldseed  # noqa: E402

PATCH_FILE = Path("/tmp/roads/h1m.bin")

# RESOLVED, not literal.  `main()` rebinds both from `--seed`/`--world`.
SEED = worldseed.seed()
WORLD = worldseed.world()

# WHICH KEEP-OUT POPULATION A DEVIATION IS SOLVED AGAINST.  This module exists
# to bend a road around a body it can see, and it was reading the MOD-FREE
# generator dump -- so the one population it most needed was the one it could
# not see.  `--poi-source {all,generator}` is gone: "all" folded in
# `roads/mod_keepout.json`, a survey of ONE world's finished carriageways, and
# a survey file is not a reading of the ground this deviation has to fit
# through.  The set is now the dump (the [SEED] question) plus a CENSUS read
# live after the zones were generated.  `main()` rebinds `CENSUS` or refuses.
CENSUS: censusmod.Census | None = None
POI_SOURCE = "generator+census"


def routing_pois() -> list:
    """The keep-out set a deviation is solved against."""
    return poimod.load() + ([] if CENSUS is None else CENSUS.pois())

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
    fieldmod.require_seed(PATCH_FILE, SEED)
    return worldseed.resolve(fieldmod.load(PATCH_FILE), patch)


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
    # THE KEEP-OUT FIX, WIRED IN. Was `poimod.load()`, the MOD-FREE dump.
    pois = routing_pois()
    pf, ps, _touch = poimod.masks(pois, corr.x0, corr.z0, corr.cell_m, corr.m,
                                  half)
    own, meta = build_forbid(corr, pad_rects(claims), pieces, half,
                             margin_m=margin_m)
    corr.forbid = pf | own
    # `cells_forbidden` above counts the PAD-AND-PIECE mask only; the POI mask
    # is a separate population and reporting one total for both would hide
    # which of them moved a line. Counted separately, and named.
    meta["poi_source"] = POI_SOURCE
    meta["poi_instances"] = len(pois)
    meta["poi_generator_instances"] = len(pois) - (
        0 if CENSUS is None else len(CENSUS.bodies))
    meta["poi_census_bodies"] = 0 if CENSUS is None else len(CENSUS.bodies)
    meta["poi_census_cells_read"] = 0 if CENSUS is None else len(CENSUS.cells)
    meta["poi_census_measured_utc"] = (None if CENSUS is None
                                       else CENSUS.measured_utc)
    # A DEVIATION IS A ROUTE, so it answers the coverage question too: a bend
    # onto ground nobody read is the same defect as a route onto it.
    meta["poi_census_coverage"] = (
        {"verdict": "NO_CENSUS",
         "why": "solved against the mod-free dump alone; this deviation can "
                "bend into a standing body"}
        if CENSUS is None else
        censusmod.coverage_gate([(float(x), float(z))
                                 for x, z in seg["nodes"]], CENSUS))
    meta["poi_cells_forbidden"] = int(pf.sum())
    meta["poi_cells_forbidden_beyond_pads"] = int((pf & ~own).sum())
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


# WHERE THE GROUND MAY NOT MOVE AT ALL, read from the one authority that
# owns the question.  `ledger/writer.py::_flatten_forbidden_check` is the
# write-time guard; this is the same population read at PLAN time, so a
# splice finds out before the console is taken rather than after.  Asked of
# the ledger's own records and its own `_site_evidence`, never re-derived:
# one reader per question is this project's rule and a second copy of a
# footprint is a second answer.
def forbidden_footprints(ledger_path: Path | None = None,
                         pad_m: float = 2.0) -> list[tuple[float, float, float]]:
    """(x, z, radius) for every live `flatten: FORBIDDEN` extent.

    `pad_m` widens each piece to the sample lattice around it, because the
    guard refuses movement AT the structure and a terrain sample 1 m away
    blends into it.
    """
    sys.path.insert(0, str(JUMPSTART / "ledger"))
    import writer as W  # noqa: PLC0415
    led = Path(ledger_path or (JUMPSTART / "ledger" / "runs" / WORLD
                               / "ledger.jsonl"))
    if not led.exists():
        return []
    prior = [json.loads(ln) for ln in led.read_text().splitlines() if ln.strip()]
    retired = W.retired_seqs(prior)
    # `_site_evidence` reads a record's protected-piece BLOB, so it needs the
    # ledger object, not just the lines.  Opened read-only: nothing here
    # appends, and the actor name says who is asking.
    ledger = W.Ledger.open(WORLD, actor="deviate-forbidden-footprints",
                           root=led.parent, heavy_fingerprint=False)
    out: list[tuple[float, float, float]] = []
    for r in prior:
        p = r.get("params") or {}
        # BOTH KEEP-OUT KINDS.  `writer.KEEP_OUT_KINDS` splits `FORBIDDEN`
        # (the ground is the hazard) from `RESEAT` (a lamp, dischargeable by
        # a write that names its seq) -- and THIS function is the alignment
        # planner's keep-out, which cannot promise a re-seat.  It keeps out
        # of both, as it did when both read FORBIDDEN.  Matching the literal
        # string here would have silently dropped every lamp out of this set
        # the moment the emitters started writing RESEAT.
        if W.keep_out_kind(p) is None or r.get("seq") in retired:
            continue
        pieces, discs = W._site_evidence(r, prior, ledger)
        if pieces:
            out += [(float(px), float(pz), pad_m) for px, pz in pieces]
        elif discs:
            out += [(float(dx), float(dz), float(rad) + pad_m)
                    for dx, dz, rad in discs]
    return out


def splice(seg: dict, dev: dict, patch: str, forbidden_xz=None) -> dict:
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
    # AND INSIDE A flatten=FORBIDDEN FOOTPRINT THE BAND IS THE STORAGE
    # QUANTUM, NOT 5 cm.
    #
    # PIN_EPS_M exists to absorb `segments.yaml`'s 3-decimal rounding, and it
    # is a BAND, so `fit_profile` is free to sit anywhere in it -- and being
    # an interval propagation it takes the edge.  MEASURED on this splice:
    # 35 retained stations of N0-brgs2-wmid, all more than 100 m past the
    # blend, moved EXACTLY 0.050 m, and one of them is at (-342.7, 317.9),
    # 994 m from the joint and inside the 15.3 m footprint of our own
    # over-water bridge at ledger seq 2789/2790. `writer._flatten_forbidden_
    # check` refuses ANY movement there -- "filling this channel to road
    # freeboard would permanently drain it, which is the unrepairable
    # direction" -- so the whole repair was refused before the write, for
    # 5 cm of drift a kilometre away that nothing asked for.
    #
    # The fix is not to loosen that guard and not to shrink PIN_EPS_M for
    # every station (lo == hi everywhere was MEASURED to prove 25 spans
    # infeasible across a kilometre of already-built road).  It is that a
    # station standing inside a footprint where ground may not move gets the
    # tightest band the file can express -- half the stored quantum -- while
    # its neighbours keep the 5 cm that makes the fit feasible.
    PIN_EXACT_M = 0.0005
    joints = [float(s[ia]), float(s[ia + len(dev_pts) - 1])]
    pinned_n = 0
    pinned_exact = 0
    # A RETAINED RUN END IS A JUNCTION NODE AND GETS THE QUANTUM TOO.
    # Same defect as the forbidden footprint, different owner: a segment's
    # terminal is the point every other road meeting there was fitted to, so
    # the 5 cm band is 5 cm of somebody else's datum.  MEASURED on this
    # splice of T1-portalhub-temple: 75 of 273 retained stations took the
    # band edge EXACTLY, and station 0 -- (-292.0, 214.0), the portal hall
    # apron and a five-way node -- moved +0.050 m.  It is inside the blend
    # (it IS a joint neighbour) so the loop below would skip it entirely and
    # leave it to the fit; pinning it at the quantum makes the end the one
    # thing a re-fit may not trade.
    ends = {0, len(pts) - 1}
    for knew, kold in retained:
        if not (0 <= knew < len(pts)):
            continue
        x, z = pts[knew]
        inside = any(math.hypot(x - fx, z - fz) <= fr
                     for fx, fz, fr in (forbidden_xz or ()))
        exact = inside or knew in ends
        if not exact and min(abs(float(s[knew]) - j)
                             for j in joints) <= PIN_BLEND_M:
            continue
        # AN AGREEMENT IS PINNED lo == hi, NOT TO A BAND.  PIN_EXACT_M is a
        # BAND of half a storage quantum and `fit_profile`, being an interval
        # propagation, takes the edge -- which `segments.yaml`'s own 3-decimal
        # rounding then prints as 0.001 m, one FULL quantum.  MEASURED twice
        # tonight, independently, on two different agreements:
        #   T1 station 0, (-292.0,214.0), the portal-hall apron and a
        #     five-way node: the band took it from +0.050 m to +0.001 m.
        #   N0 inside the 15.3 m footprint of our own over-water bridge at
        #     ledger seq 2789/2790: the band took it from 0.0500 m to
        #     0.0010 m.
        # Same residual, two different things being broken, and 0.001 m is
        # still the splice moving ground somebody else was fitted to --
        # `writer._flatten_forbidden_check` refuses ANY movement, so 0.001 m
        # refuses the write exactly as 0.050 m did.  A tightened band is not
        # a pin.  Both cases are pinned to the STORED value itself, which is
        # already exact, so there is nothing to absorb.
        #
        # SCOPED, and it stays scoped: lo == hi for EVERY retained station
        # was MEASURED to prove 25 spans infeasible across a kilometre of
        # already-built road.  Exactness is for ground something else has
        # agreed on -- a flatten=FORBIDDEN footprint and a run end -- and the
        # ordinary retained station keeps the 5 cm that keeps the fit
        # feasible.
        if exact:
            lo[knew] = hi[knew] = float(old_prof[kold])
        else:
            lo[knew] = float(old_prof[kold]) - PIN_EPS_M
            hi[knew] = float(old_prof[kold]) + PIN_EPS_M
        pinned_n += 1
        pinned_exact += 1 if exact else 0
    g_max = float(seg["grade_limit"])
    fit = routemod.fit_profile(t, s, g_max, lo=lo, hi=hi)
    bad = [(p, q) for p, q in fit["infeasible_spans"] if not free[p:q + 1].all()]
    y = np.asarray(fit["y"], dtype=np.float64)

    # WHOSE INFEASIBILITY IS IT.  The re-fit pins every retained station
    # outside the blend to the height that is ON THE GROUND, so a span of
    # retained stations can come back infeasible for a reason the deviation
    # never touched -- the RECORDED profile is already over its own declared
    # grade there.  MEASURED on T1-portalhub-temple: the deviation replaces
    # stations 183-247 around `LocationProxy@-198,42` and the re-fit refused
    # over (366, 376), 120 stations past the far joint, where T1's own stored
    # polyline walks 0.1085 to 0.1472 node-to-node against a declared 0.08 --
    # five days old, already in the ground, and nothing to do with the house.
    # Refusing the whole splice for that is a tool declining to make the one
    # repair it exists for because of a defect it did not cause and cannot
    # reach.
    #
    # So the two are separated by POSITION, which is exact: outside the
    # rewritten window every node is byte-identical to the record's, so the
    # spacings and therefore the grades there are the record's own.  An
    # introduced span REFUSES.  A pre-existing span is RESTORED to the
    # heights a player is standing on -- never re-graded silently by a fit
    # that could not satisfy its own pins -- and REPORTED with its numbers,
    # so it stays a defect somebody has to fix rather than becoming this
    # record's problem or this record's secret.
    win_lo, win_hi = int(ia), int(ia + len(dev_pts) - 1)
    old_at = {int(knew): int(kold) for knew, kold in retained}

    def _overlaps_window(p, q):
        return not (q < win_lo or p > win_hi)

    introduced = [(p, q) for p, q in bad if _overlaps_window(p, q)]
    pre_existing = []
    for p, q in bad:
        if _overlaps_window(p, q):
            continue
        ks = [k for k in range(int(p), int(q) + 1) if k in old_at]
        if len(ks) != int(q) - int(p) + 1:
            # A span that is not wholly retained cannot be attributed to the
            # record: fail closed and treat it as introduced.
            introduced.append((p, q))
            continue
        worst = 0.0
        for k in ks[:-1]:
            run = float(s[k + 1] - s[k])
            rise = abs(float(old_prof[old_at[k + 1]] - old_prof[old_at[k]]))
            worst = max(worst, rise / max(run, 1e-9))
        for k in ks:
            y[k] = float(old_prof[old_at[k]])
        pre_existing.append({
            "stations": [int(p), int(q)],
            "old_stations": [old_at[ks[0]], old_at[ks[-1]]],
            "worst_recorded_grade": round(worst, 4),
            "declared_grade_limit": g_max,
            "restored_to": "the RECORDED profile_y, unchanged",
            "why": ("outside the rewritten window every node is the "
                    "record's own, so this span's grades are the recorded "
                    "road's and were over its declared limit before this "
                    "deviation existed"),
        })
    bad = introduced
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
        #
        # IN MEMORY THE COLUMN IS `zones_carriageway`; `zones` is the ON-DISK
        # name and is re-instated by `ST.carriageway_to_disk` at the splice
        # write below, which is the only serialisation boundary this tool has.
        "zones_carriageway": sorted([list(z) for z in NW.zones_of_segment(
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
            "rewritten_window_new_stations": [win_lo, win_hi],
            "pre_existing_infeasible_spans": pre_existing,
            "pinned": pinned,
            "retained_stations_pinned": pinned_n,
            "retained_stations_pinned_exactly_in_a_forbidden_footprint":
                pinned_exact,
            "forbidden_footprints_consulted": len(forbidden_xz or ()),
            "pin_band_m": PIN_EPS_M,
            "pin_blend_m": PIN_BLEND_M,
            "instrument": "roads/deviate.py::splice",
        }],
    })
    if pre_existing:
        print(f"  {seg['id']}: {len(pre_existing)} PRE-EXISTING infeasible "
              f"span(s) OUTSIDE the rewritten window "
              f"[{win_lo},{win_hi}] -- the recorded road is already over its "
              f"declared grade there and this deviation neither caused it "
              f"nor moved it: "
              + "; ".join(
                  f"new {p['stations']} (old {p['old_stations']}) worst "
                  f"{p['worst_recorded_grade']} vs limit "
                  f"{p['declared_grade_limit']}" for p in pre_existing))
    if bad:
        raise SystemExit(
            f"{seg['id']} spliced profile is INFEASIBLE over {bad} INSIDE or "
            f"ACROSS the rewritten window [{win_lo},{win_hi}] -- refusing to "
            f"write a segment record whose profile is not proven; a road that "
            f"cannot hold its grade inside the clamp is not a fix")
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
    worldseed.add_arguments(ap)
    ap.add_argument("--census",
                    help="the census artefact `precensus.py survey` wrote "
                         "AFTER generating the zones this deviation crosses")
    ap.add_argument("--provisional", action="store_true",
                    help="solve with NO census; the result is stamped "
                         "NO_CENSUS and cannot be paved")
    a = ap.parse_args()

    global POI_SOURCE, SEED, WORLD, CENSUS
    SEED, WORLD = worldseed.apply(a)
    if a.provisional and a.census:
        raise SystemExit("--provisional and --census are the two ends of the "
                         "same pipeline; pass one")
    CENSUS = (None if a.provisional
              else censusmod.require(Path(a.census) if a.census else None,
                                     world=WORLD, seed=SEED))
    POI_SOURCE = ("generator dump ONLY (PROVISIONAL)" if CENSUS is None
                  else f"generator dump + census {a.census}")

    report = json.loads(Path(a.report).read_text())
    segs = ST.segment_map()
    claims = ST.Claims()
    # THE PROXY POPULATION IS THE REPORT'S, AND ITS SHORTFALL IS NAMED.
    # `written_segments()` is read from this world's ledger on every run and
    # GROWS; the report on disk was taken on one day over the segments
    # written by then.  Indexing `report["sources"]` by today's written set
    # raised a bare `KeyError: 'N0-brgs2-wmid'` -- a crash where the honest
    # answer is "this report predates six segments, here they are".  A
    # missing proxy file cannot hide a body on the segment being deviated
    # (its own census IS in the report, or the segment is refused below); it
    # can only narrow the LocationProxy set that names bodies elsewhere, so
    # it is a disclosed narrowing, not a silent one.
    sources = report.get("sources") or {}
    written = ST.written_segments()
    missing = [sid for sid in written if sid not in sources]
    for sid in written:
        if sid in sources:
            claims.add_proxies(json.loads(
                Path(sources[sid]).read_text())["objects"])
    if missing:
        print(f"report {a.report} carries {len(sources)} of "
              f"{len(written)} written segments; NO LocationProxy census "
              f"for {missing}")
    for sid in a.segment:
        if sid not in sources:
            raise SystemExit(
                f"REFUSED: {sid} is not in {a.report} -- this report was "
                f"taken before that segment was censused, so nothing here "
                f"knows what stands in its carriageway. Re-run "
                f"`roads/structures.py` over {sid} and deviate against the "
                f"new report; deviating against a report that never looked "
                f"is how a road gets re-routed around nothing.")
    pieces = body_points(report, "")

    # WHICH FILE EACH SEGMENT LIVES IN, because there are two and a splice
    # that writes a segment back into the wrong one creates the same id in
    # both.  On Pirate68 `north.py` owns `north_segments.yaml` (the N-series
    # and the R-series, 10 of the ledger's 25 built segments) and
    # `segments.yaml` holds the rest; reading only the second is why this
    # tool could not address the northern network at all.  WHICH files is the
    # seed's, asked here: an import-time tuple of Pirate68's names read
    # Ulfsland's 45 runs on every seed.
    patch_of: dict[str, str] = {}
    file_of: dict[str, Path] = {}
    for p in worldseed.segment_files():
        if not p.exists():
            continue
        for s in (yaml.safe_load(p.read_text()) or {}).get("segments", ()):
            patch_of[s["id"]] = s["patch"]
            file_of[s["id"]] = p

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
    # `forbidden_xz` DEFAULTS TO None AND A MISSING ARGUMENT IS A SILENT 5 cm.
    # `forbidden_footprints` was written, tested and then consulted by NO
    # caller on this path, so every splice this CLI has ever taken handed a
    # station standing inside a live `flatten: FORBIDDEN` extent the same
    # PIN_EPS_M band as open ground.  That is precisely the drift
    # `writer._flatten_forbidden_check` refuses AT THE CONSOLE, a kilometre
    # from the joint, after the plan has already been signed off.  A guard you
    # did not pass is indistinguishable from a guard that passed, so it is
    # read here, once, from the ledger that owns the question.
    forbidden_xz = forbidden_footprints() if order else []
    if order:
        print(f"forbidden footprints consulted: {len(forbidden_xz)} "
              f"(pinned at the storage quantum, not the 5 cm band)")
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
        target = file_of[sid]
        doc2 = yaml.safe_load(target.read_text())
        cur = next(sg for sg in doc2["segments"] if sg["id"] == sid)
        rec = splice(cur, d, patch_of[sid], forbidden_xz=forbidden_xz)
        # READ THE COLUMN BY ITS MEMORY NAME, then convert the record at the
        # SERIALISATION BOUNDARY so the file keeps the on-disk name its other
        # 38 segments carry.  `doc2` came from a bare `yaml.safe_load` and is
        # written straight back, so only `rec` is ever in the memory name.
        n_carriageway = len(rec["zones_carriageway"])
        ST.carriageway_to_disk(rec)
        for k, sg in enumerate(doc2["segments"]):
            if sg["id"] == sid:
                doc2["segments"][k] = rec
                break
        else:
            raise SystemExit(f"no {sid} in {target}")
        target.write_text(yaml.safe_dump(doc2, sort_keys=True,
                                         default_flow_style=False))
        print(f"spliced {sid} deviation {idx}: length {rec['length_m']} m, "
              f"grade_max {rec['grade_max_measured']}, "
              f"cut {rec['cut_max_m']} fill {rec['fill_max_m']}, "
              f"over_clamp {rec['stations_over_clamp']}, "
              f"carriageway zones {n_carriageway} -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
