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
                 half_m: float) -> tuple[np.ndarray, dict]:
    """Rasterise pad rectangles and measured piece positions onto the grid.

    The dilation is `half_m + BODY_MARGIN_M + cell slack`.  The cell slack is
    the same geometric bound `poi.masks` documents: A* returns cell CENTRES and
    `simplify` then cuts chords to 1 m, so a finished centreline can sit
    `cell/sqrt(2) + 1` inside a cell whose centre was outside the mask.  Not
    adding it is the measured defect where "the mask was correct and the line
    still breached it".
    """
    m = corr.m
    grow = half_m + BODY_MARGIN_M + corr.cell_m / math.sqrt(2.0) + 1.0
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
            patch: str) -> dict:
    fld = load_field(patch)
    half = seg["width_m"] / 2.0 + 1.0  # carriageway half + shoulder
    g_max = float(seg.get("grade_limit") or 0.08)
    corr = routemod.Corridor(fld=fld, cell_m=4.0, grade_max=g_max)
    pois = poimod.load()
    pf, ps, _touch = poimod.masks(pois, corr.x0, corr.z0, corr.cell_m, corr.m,
                                  half)
    own, meta = build_forbid(corr, pad_rects(claims), pieces, half)
    corr.forbid = pf | own
    corr.soft = ps
    nodes = np.asarray(seg["nodes"], dtype=np.float64)
    al = along_of(nodes)
    out = {"segment": seg["id"], "grade_limit": g_max, "mask": meta,
           "deviations": []}
    for a_m, b_m, who in windows(report, seg["id"]):
        ia = int(np.searchsorted(al, max(0.0, a_m - BRACKET_M)))
        ib = int(np.searchsorted(al, min(al[-1], b_m + BRACKET_M)))
        ia = max(0, min(ia, len(nodes) - 1))
        ib = max(0, min(ib, len(nodes) - 1))
        anchor_a = (float(nodes[ia, 0]), float(nodes[ia, 1]))
        anchor_b = (float(nodes[ib, 0]), float(nodes[ib, 1]))
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
              and rec["clearance"]["worst_pad_m"] >= BODY_MARGIN_M
              and rec["clearance"]["worst_piece_m"] >= half)
        rec["verdict"] = "REROUTE_OK" if ok else "REROUTE_FAILS_GATE"
        out["deviations"].append(rec)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", default="/tmp/roadclean/structures_network.json")
    ap.add_argument("--segment", action="append", required=True)
    ap.add_argument("--out", default="/tmp/roadclean/deviations.json")
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
        res = deviate(segs[sid], report, claims, pieces, patch_of[sid])
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
