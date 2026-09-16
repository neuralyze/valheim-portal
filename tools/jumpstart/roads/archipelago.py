#!/usr/bin/env python3
"""Is Ulfsland an archipelago?  Connected-component labelling at several
resolutions, from scripts, so the answer is reproducible.

WHY IT IS RE-DERIVED.  The first statement of this result was computed in the
shared `eval` kernel, whose globals a sibling agent was concurrently rebinding
(surfaced as a `KeyError` on a dict key that existed two cells earlier).  A
regeneration decision is being made on this number, so it is recomputed here
with no shared state and the script is the artefact.

WHY MULTIPLE RESOLUTIONS.  Two landmasses that touch at a one-metre spit are one
component at 1 m and two at 8 m; two that are separated by a 3 m channel are two
at 1 m and one at 8 m under 8-connectivity.  A component COUNT is therefore not
a property of the world alone, it is a property of (world, resolution,
connectivity, threshold).  Reporting one number without those is the defect this
project keeps paying for, so all four are reported and the sensitivity is
measured rather than assumed.

THRESHOLD.  land = h > 30.0 + 0.5, where 30.0 is `ZoneSystem::c_WaterLevel`
(MEASURED: `.field public static literal float32 c_WaterLevel = float32(30.)`,
assembly_valheim.dll 1.0.12).  The 0.5 m freeboard keeps a surf sample from
counting as land.  Connectivity is 8-way (`np.ones((3,3))`), which is the
PERMISSIVE choice: it merges landmasses that touch only diagonally, so it
UNDER-counts islands.  An archipelago verdict from a permissive test is
therefore conservative.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from scipy import ndimage

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import field as fieldmod  # noqa: E402
from field import BIOME_NAMES, WATER_LEVEL  # noqa: E402

FREEBOARD_M = 0.5
INSTALLATIONS = {
    "meadhall": (303.5, 385.5), "dock": (675.4, -85.6),
    "stathub": (661.5, 1088.5), "portalhub": (-292.5, 213.5),
    "mtnpost": (-7.5, 3939.5), "plainsfm": (750.5, -4278.5),
    "workshop": (-4673.5, -310.5), "blacbase": (-3045.5, -4101.5),
    "ashland": (-4577.5, -8218.5), "dnshrine": (3957.3, 7734.3),
    "dncamp": (-1198.6, 8607.4), "mistfrge": (5523.3, -5631.7),
    "temple": (-64.68, 3.31),
}


def label_at(fld, decim: int) -> dict:
    """Label land components on the field decimated by `decim`.

    Decimation uses MAX over each block: a block counts as land if ANY sample in
    it is land.  That is the permissive direction again -- it grows islands and
    merges them -- so a high component count survives it rather than being
    manufactured by it.
    """
    n = (fld.n // decim) * decim
    h = fld.h[:n, :n]
    if decim > 1:
        h = h.reshape(n // decim, decim, n // decim, decim).max(axis=(1, 3))
    land = h > WATER_LEVEL + FREEBOARD_M
    lab, cnt = ndimage.label(land, structure=np.ones((3, 3)))
    if cnt == 0:
        return {"decim": decim, "cell_m": fld.step * decim, "components": 0}
    sizes = ndimage.sum(land, lab, range(1, cnt + 1))
    cell_area = (fld.step * decim) ** 2
    order = np.argsort(sizes)[::-1]
    return {
        "decim": decim,
        "cell_m": fld.step * decim,
        "components": int(cnt),
        "land_fraction": round(float(land.mean()), 4),
        "largest_km2": round(float(sizes[order[0]]) * cell_area / 1e6, 4),
        "top10_km2": [round(float(sizes[k]) * cell_area / 1e6, 4) for k in order[:10]],
        "components_over_1km2": int((sizes * cell_area / 1e6 > 1.0).sum()),
        "components_over_0p1km2": int((sizes * cell_area / 1e6 > 0.1).sum()),
    }


def island_of(fld, lab, sizes, x: float, z: float, step: float,
              search_cells: int = 40):
    if not fld.contains(x, z, margin=1.0):
        return None
    i, j = fld.index(x, z)
    i, j = int(i / (fld.step / step) if step != fld.step else i), int(j)
    cid = int(lab[i, j]) if 0 <= i < lab.shape[0] and 0 <= j < lab.shape[1] else 0
    if cid:
        return cid, 0.0
    for r in range(1, search_cells + 1):
        win = lab[max(0, i - r):i + r + 1, max(0, j - r):j + r + 1]
        ids = [int(v) for v in np.unique(win) if v > 0]
        if ids:
            return max(ids, key=lambda c: sizes[c - 1]), float(r) * step
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="/tmp/roads/heights.bin",
                    help="VHPATCH1 holding the wide coarse patches")
    ap.add_argument("--fine", default="/tmp/roads/h1m.bin",
                    help="VHPATCH1 holding the 1 m island patches")
    ap.add_argument("--out", default=str(HERE / "archipelago.json"))
    args = ap.parse_args()

    out: dict = {
        "seed": "Pirate68",
        "water_level_y": WATER_LEVEL,
        "water_level_source": "ZoneSystem::c_WaterLevel, static literal float32(30.), "
                              "assembly_valheim.dll 1.0.12, read via monodis",
        "land_test": f"h > {WATER_LEVEL} + {FREEBOARD_M}",
        "connectivity": "8-way (np.ones((3,3))) -- PERMISSIVE, merges diagonal touches, "
                        "so it under-counts islands",
        "decimation": "MAX over each block -- PERMISSIVE, grows and merges islands",
        "rivers_included": True,
        "method": "script tools/jumpstart/roads/archipelago.py via bash; no shared kernel state",
        "resolution_sweep": {},
        "installations": {},
    }

    reg = fieldmod.load(args.region)
    for pid, fld in reg.items():
        sweep = []
        for dec in (1, 2, 4):
            sweep.append(label_at(fld, dec))
        out["resolution_sweep"][pid] = {
            "patch_step_m": fld.step, "n": fld.n,
            "patch_bbox": {"x": [fld.cx - fld.half, fld.cx + fld.half],
                           "z": [fld.cz - fld.half, fld.cz + fld.half]},
            "levels": sweep,
        }
        for s in sweep:
            print(f"{pid:10s} cell {s['cell_m']:5.1f} m  components {s['components']:6d}  "
                  f"largest {s.get('largest_km2', 0):8.4f} km2  "
                  f">1km2 {s.get('components_over_1km2', 0):3d}  "
                  f">0.1km2 {s.get('components_over_0p1km2', 0):4d}")

    # Per-installation landmass identity, at the finest available resolution
    # covering it.
    fine = fieldmod.load(args.fine)
    for name, (x, z) in INSTALLATIONS.items():
        rec = {"xz": [x, z], "covered_by": None}
        for pid, fld in fine.items():
            if fld.contains(x, z, margin=2.0):
                land = fld.h > WATER_LEVEL + FREEBOARD_M
                lab, cnt = ndimage.label(land, structure=np.ones((3, 3)))
                sizes = ndimage.sum(land, lab, range(1, cnt + 1))
                i, j = fld.index(x, z)
                cid = int(lab[i, j])
                dist = 0.0
                if cid == 0:
                    for r in range(1, 41):
                        win = lab[max(0, i - r):i + r + 1, max(0, j - r):j + r + 1]
                        ids = [int(v) for v in np.unique(win) if v > 0]
                        if ids:
                            cid = max(ids, key=lambda c: sizes[c - 1])
                            dist = float(r)
                            break
                rec = {
                    "xz": [x, z], "covered_by": pid, "step_m": fld.step,
                    "generated_y": round(float(fld.h[i, j]), 2),
                    "biome": BIOME_NAMES.get(int(fld.biome[i, j]), "Unknown"),
                    "on_land": bool(lab[i, j] > 0),
                    "landmass_component": cid,
                    "landmass_km2": round(float(sizes[cid - 1]) / 1e6, 4) if cid else 0.0,
                    "dist_to_land_m": dist,
                }
                break
        out["installations"][name] = rec
        print(f"  {name:10s} patch={rec['covered_by']} "
              f"landmass_km2={rec.get('landmass_km2', 'n/a')} "
              f"on_land={rec.get('on_land', 'n/a')}")

    Path(args.out).write_text(json.dumps(out, indent=1))
    print(f"-> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
