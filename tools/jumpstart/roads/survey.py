#!/usr/bin/env python3
"""Landmass survey: which landmasses exist, how big, and where a road can end.

WHY THIS IS A SCRIPT AND NOT AN INTERACTIVE SESSION.  The first version of this
survey ran in the shared `eval` kernel and a sibling agent rebound the global
`F`, which surfaced as a `KeyError` on a key that had existed two cells earlier.
Every number that came out of that kernel was therefore untrustworthy, including
landmass areas and the extremity coordinates other agents were asked to site
against.  A survey whose output other people build on has to be reproducible by
re-running one command, so it is a script with no shared state and it writes its
answer to a file.

WHAT IT MEASURES, all from `run_patchscan.sh` output at 1 m WITH RIVERS:
  * connected components of land, land being h > 30.0 + freeboard, where 30.0 is
    `ZoneSystem::c_WaterLevel` (MEASURED: a `static literal float32(30.)` in
    assembly_valheim.dll 1.0.12).  A land test against h > 0 calls the seabed
    dry; this one does not.
  * per-landmass area, bounding box, biome mix.
  * EXTREMITY nodes: for each 45 deg sector from the landmass centroid, the
    farthest sample that is at least `inland_m` from any water edge.  The inland
    condition is the point -- the farthest sample outright is usually a
    one-pixel spit, and a road that terminates on a spit terminates in the sea.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy import ndimage

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import field as fieldmod  # noqa: E402
from field import BIOME_NAMES, WATER_LEVEL  # noqa: E402

FREEBOARD_M = 0.5
INLAND_M = 25.0


def survey_patch(fld, probes: dict[str, tuple[float, float]],
                 inland_m: float = INLAND_M) -> dict:
    land = fld.h > WATER_LEVEL + FREEBOARD_M
    lab, n = ndimage.label(land, structure=np.ones((3, 3)))
    sizes = ndimage.sum(land, lab, range(1, n + 1))
    main = int(np.argmax(sizes)) + 1

    # Which component does each probe belong to?  A probe on a shoreline sample
    # can be WATER by the h test (early-dock solved to water_dist_m 0.0), so
    # report the nearest component within a short search instead of asserting.
    probe_out = {}
    for name, (px, pz) in probes.items():
        if not fld.contains(px, pz, margin=2.0):
            probe_out[name] = {"in_patch": False}
            continue
        i, j = fld.index(px, pz)
        cid = int(lab[i, j])
        near = None
        if cid == 0:
            for r in range(1, 41):
                win = lab[max(0, i - r):i + r + 1, max(0, j - r):j + r + 1]
                ids = [int(v) for v in np.unique(win) if v > 0]
                if ids:
                    near = {"component": max(ids, key=lambda c: sizes[c - 1]),
                            "dist_m": float(r)}
                    break
        probe_out[name] = {
            "in_patch": True, "xz": [px, pz],
            "generated_y": round(float(fld.h[i, j]), 2),
            "biome": BIOME_NAMES.get(int(fld.biome[i, j]), "Unknown"),
            "component": cid, "is_land": bool(cid > 0),
            "nearest_land": near,
        }

    comps = []
    order = np.argsort(sizes)[::-1][:6]
    for k in order:
        cid = int(k) + 1
        m = lab == cid
        ii, jj = np.nonzero(m)
        xs = fld.x0 + jj
        zs = fld.z0 + ii
        bi = fld.biome[ii, jj]
        vals, cts = np.unique(bi, return_counts=True)
        mix = {BIOME_NAMES.get(int(v), "Unknown"): round(float(c) / len(bi), 3)
               for v, c in zip(vals, cts) if c / len(bi) >= 0.02}
        dist = ndimage.distance_transform_edt(m)
        di = dist[ii, jj]
        cx, cz = float(xs.mean()), float(zs.mean())
        ang = np.degrees(np.arctan2(xs - cx, zs - cz)) % 360.0
        rad = np.hypot(xs - cx, zs - cz)
        ext = []
        for a0 in range(0, 360, 45):
            sel = (np.abs((ang - a0 + 180.0) % 360.0 - 180.0) < 22.5) & (di >= inland_m)
            if not sel.any():
                continue
            idx = int(np.nonzero(sel)[0][int(np.argmax(rad[sel]))])
            ext.append({
                "sector_deg": a0,
                "xz": [float(xs[idx]), float(zs[idx])],
                "generated_y": round(float(fld.h[ii[idx], jj[idx]]), 2),
                "biome": BIOME_NAMES.get(int(fld.biome[ii[idx], jj[idx]]), "Unknown"),
                "radius_m": round(float(rad[idx]), 1),
                "inland_m": round(float(di[idx]), 1),
            })
        comps.append({
            "component": cid, "is_largest": cid == main,
            "area_km2": round(float(m.sum()) * fld.step * fld.step / 1e6, 4),
            "bbox": {"x": [float(xs.min()), float(xs.max())],
                     "z": [float(zs.min()), float(zs.max())]},
            "centroid": [round(cx, 1), round(cz, 1)],
            "max_inland_m": round(float(di.max()), 1),
            "biome_mix": mix,
            "extremities": ext,
        })
    return {"patch": fld.id, "step_m": fld.step, "n": fld.n,
            "patch_bbox": {"x": [fld.cx - fld.half, fld.cx + fld.half],
                           "z": [fld.cz - fld.half, fld.cz + fld.half]},
            "components_total": int(n),
            "land_fraction": round(float(land.mean()), 4),
            "components": comps, "probes": probe_out}


PROBES = {
    "temple": (-64.68, 3.31), "portalhub": (-292.5, 213.5),
    "meadhall": (303.5, 385.5), "dockshore": (675.4, -85.6),
    "stathub": (661.5, 1088.5),
    "brg-s1-e": (-312.0, -64.0), "brg-s1-w": (-325.0, -64.0),
    "brg-s2-e": (-318.0, 230.0), "brg-s2-w": (-349.0, 246.0),
    "blacbase": (-3045.5, -4101.5), "plainsfm": (750.5, -4278.5),
    "workshop": (-4673.5, -310.5), "mtnpost": (-7.5, 3939.5),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--field", default="/tmp/roads/h1m.bin")
    ap.add_argument("--out", default=str(HERE / "survey.json"))
    ap.add_argument("--patch", action="append")
    args = ap.parse_args()

    flds = fieldmod.load(args.field)
    out = {"source": args.field, "seed": "Pirate68",
           "water_level_y": WATER_LEVEL,
           "water_level_source": "ZoneSystem::c_WaterLevel, static literal float32(30.), "
                                 "assembly_valheim.dll 1.0.12",
           "land_test": f"h > {WATER_LEVEL} + {FREEBOARD_M}",
           "rivers_included": True,
           "rivers_evidence": "PatchScan.cs initialises WorldGenerator with full "
                              "pregeneration, so WorldGenerator.AddRivers has the "
                              "dictionary Pregenerate() fills",
           "inland_condition_m": INLAND_M,
           "patches": {}}
    for pid, fld in flds.items():
        if args.patch and pid not in args.patch:
            continue
        out["patches"][pid] = survey_patch(fld, PROBES)
        c0 = out["patches"][pid]["components"][0]
        print(f"{pid:14s} largest {c0['area_km2']:7.4f} km2  "
              f"x[{c0['bbox']['x'][0]:.0f},{c0['bbox']['x'][1]:.0f}] "
              f"z[{c0['bbox']['z'][0]:.0f},{c0['bbox']['z'][1]:.0f}]  "
              f"comps {out['patches'][pid]['components_total']:5d}  {c0['biome_mix']}")
    Path(args.out).write_text(json.dumps(out, indent=1))
    print(f"-> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
