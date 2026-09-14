#!/usr/bin/env python3
"""Solve base-placement sites for one world seed, from the game's own generator.

Blueprint placements are **seed-specific**: a coordinate that is flat meadow in
one seed is open ocean in another.  So `placements.yaml` records a *requirement*
(biome, footprint, flatness, coastal, which boss/feature to sit near) and this
tool turns that requirement into a concrete `x y z` for a given seed.  Re-roll
the world, re-run this, regenerate the coordinates.

Inputs are the two artefacts produced by the sibling `tools/seedscan` harness
(this tool never modifies them):

  * a `.biome` grid -- `VHBIOME4` header, `n*n` uint8 biome plane plus, with
    `HEIGHT=1`, an `n*n` float32 terrain-height plane.  Cell centres are
    `-extent + i*step + step/2`.
  * the `run_locscan.sh` JSON dump -- every `ZoneSystem` location instance with
    its real world coordinates, which is what lets a site be anchored to the
    actual Eikthyr altar or Deep North boss rather than a guess.

Scoring a candidate cell, all MEASURED off the grid:

  `flat`      max-minus-min terrain height over the footprint window; this is
              the amount of hoe work the site needs.
  `biome_pure` fraction of footprint cells in the requested biome.
  `water`     distance to the nearest cell whose height is below sea level,
              i.e. how close a dock or a longship can get.
  `anchor`    distance to the anchor location the requirement names.

Usage
-----
    # list the anchors a seed actually has
    python3 site_finder.py --grid /tmp/bp_p68_8/00000.biome \\
        --locations /tmp/seedscan/loc/<hash>.json --list-anchors

    # solve one requirement
    python3 site_finder.py --grid ... --locations ... \\
        --biome Meadows --footprint 56 --max-flat 3 --coastal \\
        --near Eikthyrnir --within 600 --top 5
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import sys
from pathlib import Path

import numpy as np

MAGIC = b"VHBIOME4"
BIOME_NAME = {
    0: "None",
    1: "Meadows",
    2: "Swamp",
    3: "Mountain",
    4: "BlackForest",
    5: "Plains",
    6: "AshLands",
    7: "DeepNorth",
    8: "Ocean",
    9: "Mistlands",
}
BIOME_ID = {v.lower(): k for k, v in BIOME_NAME.items()}

# Sea level is 0 in these height units, NOT the 30 of Valheim's in-game water
# plane -- `WorldGenerator.GetHeight` returns terrain height relative to the
# water surface. MEASURED on the Pirate68 grid: Ocean-biome cells span
# -400..+4 with a median of exactly 0.0, and the lowest Meadows cell is +6.1.
# Ocean-biome cells above 0 are therefore beaches, and land is height > 0.
WATER_LEVEL = 0.0

# The location dump carries no exterior/clear radius (MEASURED: its records are
# name/prefab/biome/group/unique/icon/x/y/z/placed only), and ZoneSystem
# re-cuts terrain inside a location's clear area when it spawns. A candidate
# site inside that area will fight the generator, so keep a conservative
# stand-off from every placed location instead of guessing per-prefab radii.
DEFAULT_LOCATION_CLEARANCE = 60.0


def load_grid(path: Path) -> dict:
    blob = path.read_bytes()
    if blob[:8] != MAGIC:
        raise ValueError(f"{path}: bad magic {blob[:8]!r}")
    step, extent, n, planes, namelen = struct.unpack_from("<iiiii", blob, 8)
    off = 28
    seed = blob[off : off + namelen].decode("utf-8")
    off += namelen
    (seed_hash,) = struct.unpack_from("<i", blob, off)
    off += 4
    grid = np.frombuffer(blob, dtype=np.uint8, count=n * n, offset=off).reshape(n, n)
    off += n * n
    if planes < 2:
        raise ValueError(f"{path}: no height plane; re-scan with HEIGHT=1")
    hgt = np.frombuffer(blob, dtype="<f4", count=n * n, offset=off).reshape(n, n)
    return {
        "seed": seed,
        "hash": seed_hash,
        "step": step,
        "extent": extent,
        "n": n,
        "biome": grid,
        "height": hgt,
    }


def axis(n: int, step: int, extent: int) -> np.ndarray:
    return -extent + np.arange(n) * step + step * 0.5


def window_stats(a: np.ndarray, k: int, fn: str) -> np.ndarray:
    """Sliding k*k window reduction, returned at the window's top-left index."""
    from numpy.lib.stride_tricks import sliding_window_view

    w = sliding_window_view(a, (k, k))
    if fn == "max":
        return w.max(axis=(-1, -2))
    if fn == "min":
        return w.min(axis=(-1, -2))
    if fn == "mean":
        return w.mean(axis=(-1, -2))
    raise ValueError(fn)


def solve(
    g: dict,
    biome: str,
    footprint_m: float,
    max_flat: float,
    coastal: bool,
    anchor_xz: tuple[float, float] | None,
    within: float,
    min_purity: float,
    top: int,
    avoid_xz: np.ndarray | None = None,
    clearance: float = DEFAULT_LOCATION_CLEARANCE,
    coastal_within: float = 120.0,
    min_height: float = 2.0,
    exclude: list[tuple[float, float, float]] | None = None,
) -> list[dict]:
    step, n, extent = g["step"], g["n"], g["extent"]
    bid = BIOME_ID[biome.lower()]
    k = max(2, int(math.ceil(footprint_m / step)))
    if k > n:
        raise ValueError("footprint larger than the world")

    hgt = g["height"].astype(np.float32)
    bio = g["biome"]

    hmax = window_stats(hgt, k, "max")
    hmin = window_stats(hgt, k, "min")
    flat = hmax - hmin
    pure = window_stats((bio == bid).astype(np.float32), k, "mean")
    # Every footprint cell must be dry land, and dry is not enough on its own:
    # a perfectly flat 0.05 m site is the waterline, where a build ends up with
    # its foundations in the sea. Require real freeboard above sea level.
    dry = hmin > max(WATER_LEVEL, min_height)

    ok = dry & (flat <= max_flat) & (pure >= min_purity)

    m = flat.shape[0]
    xs = axis(n, step, extent)
    # window centre in world coords
    cx = xs[: m] + (k - 1) * step * 0.5
    # grid is [row, col]; seedscan writes row = z index, col = x index
    cz2d, cx2d = np.meshgrid(cx, cx, indexing="ij")

    if anchor_xz is not None:
        ax_, az_ = anchor_xz
        adist = np.hypot(cx2d - ax_, cz2d - az_)
        ok &= adist <= within
    else:
        adist = np.hypot(cx2d, cz2d)

    # Sites already taken by earlier placements in the same preset. Without
    # this, two placements with similar requirements resolve to the identical
    # flattest cell and the second building lands inside the first.
    if exclude:
        for ex_, ez_, er_ in exclude:
            ok &= np.hypot(cx2d - ex_, cz2d - ez_) > er_

    idx = np.argwhere(ok)
    if idx.size == 0:
        return []

    # A permissive requirement can leave millions of qualifying cells; ranking
    # them in Python would dominate the runtime. Pre-select in numpy on the
    # primary key (flatness, then anchor distance) and only materialise dicts
    # for a bounded shortlist.
    CAP = 20000
    if len(idx) > CAP:
        fl = flat[idx[:, 0], idx[:, 1]]
        ad = adist[idx[:, 0], idx[:, 1]]
        order = np.lexsort((ad, np.round(fl, 2)))
        idx = idx[order[:CAP]]

    # Water proximity via a Euclidean distance transform of the water mask.
    # The naive form -- for each candidate, min-distance over every water cell
    # -- is O(candidates * water cells), and this grid has 2.3 million water
    # cells, so it turns a one-second query into minutes. The EDT is one pass
    # over the grid, cached on the grid object because the solver runs many
    # requirements against the same seed.
    water_cells = g.get("_water_edt")
    if water_cells is None:
        from scipy import ndimage

        water = hgt <= WATER_LEVEL
        if water.any():
            water_cells = ndimage.distance_transform_edt(~water) * step
        else:
            water_cells = np.full(hgt.shape, np.inf, dtype=np.float64)
        g["_water_edt"] = water_cells

    out = []
    half_cells = (k - 1) * 0.5
    for r, c in idx:
        # sample the EDT at the footprint centre, not its top-left corner
        rr = min(n - 1, int(round(r + half_cells)))
        cc = min(n - 1, int(round(c + half_cells)))
        out.append(
            {
                "x": float(cx2d[r, c]),
                "z": float(cz2d[r, c]),
                "y": float(hmax[r, c]),
                "flat": float(flat[r, c]),
                "biome_purity": float(pure[r, c]),
                "anchor_dist": float(adist[r, c]),
                "water_dist": float(water_cells[rr, cc]),
            }
        )
    out.sort(key=lambda d: (round(d["flat"], 2), d["anchor_dist"]))
    out = out[: max(top * 40, 400)]

    # Stand off from every ZoneSystem location: its clear area re-cuts terrain.
    if avoid_xz is not None and len(avoid_xz) and out:
        tree = g.get("_loc_tree")
        if tree is None:
            from scipy.spatial import cKDTree

            tree = cKDTree(avoid_xz)
            g["_loc_tree"] = tree
        pts = np.array([(d["x"], d["z"]) for d in out])
        nearest, _ = tree.query(pts, k=1)
        keep = []
        for d, nd in zip(out, nearest):
            d["location_dist"] = float(nd)
            if nd >= clearance + (k - 1) * step * 0.5:
                keep.append(d)
        out = keep

    if coastal:
        out = [d for d in out if d["water_dist"] <= coastal_within]
        out.sort(key=lambda d: (round(d["flat"], 2), d["water_dist"], d["anchor_dist"]))
    for d in out:
        d.pop("_rc", None)
    return out[:top]


def load_anchors(path: Path) -> dict[str, list[dict]]:
    data = json.loads(path.read_text("utf-8"))
    out: dict[str, list[dict]] = {}
    for l in data["locations"]:
        out.setdefault(l["name"], []).append(l)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--grid", required=True)
    ap.add_argument("--locations")
    ap.add_argument("--list-anchors", action="store_true")
    ap.add_argument("--biome")
    ap.add_argument("--footprint", type=float, default=56.0, help="square side, metres")
    ap.add_argument("--max-flat", type=float, default=4.0, help="max height spread, metres")
    ap.add_argument("--min-purity", type=float, default=0.85)
    ap.add_argument("--coastal", action="store_true", help="require water within 120 m")
    ap.add_argument("--near", help="anchor location name from the locations dump")
    ap.add_argument("--near-xz", nargs=2, type=float, metavar=("X", "Z"))
    ap.add_argument("--within", type=float, default=1500.0)
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument(
        "--clearance",
        type=float,
        default=DEFAULT_LOCATION_CLEARANCE,
        help="minimum stand-off from any placed ZoneSystem location, metres",
    )
    ap.add_argument(
        "--coastal-within", type=float, default=120.0, help="water distance that counts as coastal"
    )
    ap.add_argument("--json", help="write candidates here")
    args = ap.parse_args(argv)

    g = load_grid(Path(args.grid))
    print(
        f"seed={g['seed']} hash={g['hash']} step={g['step']}m n={g['n']} extent={g['extent']}",
        file=sys.stderr,
    )

    anchors: dict[str, list[dict]] = {}
    if args.locations:
        anchors = load_anchors(Path(args.locations))

    if args.list_anchors:
        for name in sorted(anchors):
            inst = anchors[name]
            nearest = min(inst, key=lambda l: math.hypot(l["x"], l["z"]))
            print(
                f"{name:38} n={len(inst):5}  nearest=({nearest['x']:9.1f},"
                f"{nearest['y']:7.1f},{nearest['z']:9.1f}) {nearest['biome']:12}"
                f" r={math.hypot(nearest['x'], nearest['z']):8.0f}"
            )
        return 0

    if not args.biome:
        ap.error("--biome is required unless --list-anchors")

    anchor_xz = None
    if args.near_xz:
        anchor_xz = (args.near_xz[0], args.near_xz[1])
    elif args.near:
        if args.near not in anchors:
            print(f"no anchor named {args.near!r}; use --list-anchors", file=sys.stderr)
            return 2
        inst = min(anchors[args.near], key=lambda l: math.hypot(l["x"], l["z"]))
        anchor_xz = (inst["x"], inst["z"])
        print(
            f"anchor {args.near} at ({inst['x']:.1f},{inst['y']:.1f},{inst['z']:.1f})",
            file=sys.stderr,
        )

    avoid_xz = None
    if anchors:
        pts = [(l["x"], l["z"]) for inst in anchors.values() for l in inst]
        avoid_xz = np.asarray(pts, dtype=np.float64)
        print(f"avoiding {len(pts)} placed locations by >= {args.clearance} m", file=sys.stderr)

    cands = solve(
        g,
        args.biome,
        args.footprint,
        args.max_flat,
        args.coastal,
        anchor_xz,
        args.within,
        args.min_purity,
        args.top,
        avoid_xz,
        args.clearance,
        args.coastal_within,
    )
    if not cands:
        print("no site satisfies the requirement -- relax --max-flat or --within", file=sys.stderr)
        return 1

    print(
        f"{'x':>9} {'y':>7} {'z':>9} {'flat':>6} {'pure':>5} {'water':>7} "
        f"{'anchor':>8} {'loc':>7}"
    )
    for d in cands:
        print(
            f"{d['x']:9.1f} {d['y']:7.1f} {d['z']:9.1f} {d['flat']:6.2f} "
            f"{d['biome_purity']:5.2f} {d['water_dist']:7.1f} {d['anchor_dist']:8.0f} "
            f"{d.get('location_dist', float('nan')):7.0f}"
        )
    if args.json:
        Path(args.json).write_text(json.dumps(cands, indent=1) + "\n", "utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
