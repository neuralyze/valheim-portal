#!/usr/bin/env python3
"""Rank base-placement sites for one world seed, from the game's own generator.

Blueprint placements are **seed-specific**: a coordinate that is flat meadow in
one seed is open ocean in another.  So `placements.yaml` records a *requirement*
(biome, footprint, flatness, coastal, which boss/feature to sit near) and this
module turns that requirement into a RANKED SHORTLIST of concrete sites for a
given seed.  Re-roll the world, re-run, regenerate the coordinates.

Inputs are artefacts of the sibling `tools/seedscan` harness plus this
directory's own `run_patchscan.sh` (none of them is ever modified here):

  * a `.biome` grid -- `VHBIOME4` header, `n*n` uint8 biome plane plus, with
    `HEIGHT=1`, an `n*n` float32 terrain-height plane.  This is the COARSE
    search plane.
  * the `run_locscan.sh` JSON dump -- every `ZoneSystem` location instance with
    its real world coordinates.  Anchors and clearance both come from here.
  * a `VHPATCH1` patch file from `run_patchscan.sh` -- 1 m heights and biomes in
    a window around each shortlisted candidate.  This is the VERDICT plane.

Why two planes: resolution, MEASURED
------------------------------------
Valheim builds terrain on a 1 m heightmap.  Sampling the whole 21 km world at
1 m is 440 M generator calls; the shipped grids are 8 m.  Against a 1 m ground
truth over 2048x2048 m of Pirate68, the fraction of windows a coarse grid calls
flat that really ARE flat is:

    footprint / max_flat      8 m grid   4 m grid   2 m grid
    32 m / 2.5 m                 0.18       0.44       0.62
    60 m / 3.0 m                 0.74       0.82       0.83
    70 m / 3.5 m                 0.83       0.90       0.90
    80 m / 4.0 m                 0.85       0.94       0.95

So no affordable whole-world step can decide flatness, and a coarse-only answer
for a small footprint is wrong most of the time.  Hence: search coarse, decide
fine.  Per criterion, the resolution actually needed:

    criterion            needs      served by
    flatness             1 m        1 m patch, and the footprint is re-slid
                                    inside the patch to the flattest 1 m offset
    biome purity         1 m        1 m patch (coarse grid only pre-filters)
    location clearance   exact      exact KD-tree against the ZoneSystem dump
    anchor distance      exact      exact, both endpoints are known points
    water distance       ~8 m       8 m EDT; the coastal test is a 120 m
                                    threshold, so 8 m is two orders inside it
    overland routing     32 m       32 m land graph; a detour factor does not
                                    need metre accuracy

The coarse grid ALSO has no rivers: `run_scan.sh` defaults SEEDSCAN_PREGEN=0 and
`WorldGenerator.AddRivers` reads the dictionary `Pregenerate()` fills, so the
grid's height plane is river-free.  `run_patchscan.sh` always pregenerates, so
the refined pass sees river beds the search pass cannot.

Usage
-----
    # list the anchors a seed actually has
    python3 site_finder.py --grid /tmp/bp_p68_8/00000.biome \\
        --locations /tmp/seedscan/loc/<hash>.json --list-anchors

    # rank sites for one requirement (coarse only; solve_placements.py adds the
    # 1 m refinement pass)
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
PATCH_MAGIC = b"VHPATCH1"
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

# Valheim's water plane is y = 30 in `WorldGenerator.GetHeight` units, and these
# heights are ABSOLUTE, not relative to the surface. This was wrong here until
# `SpawnOnLand` caught it; the old code used 0.0 on the reading that Ocean cells
# "span -400..+4 with median 0.0, so 0 is the surface". That is the ocean FLOOR
# and its clamp, roughly 26-30 m down, not the surface. MEASURED on the Pirate68
# artefacts, three independent ways:
#   * per-biome grid heights: Ocean is clamped at max exactly 4.00, while Swamp
#     spans 27.46..33.84 with median 29.84 -- a narrow band straddling 30, which
#     is precisely what a swamp is;
#   * Meadows median 30.61, min 4.16 (the clamp again), Plains median 28.10;
#   * the ZoneSystem dump agrees with the grid to a median of 0.02 m, and every
#     land location type floors just above 30 -- Grave1 30.50, Ruin1 31.02,
#     StoneHouse4 31.04, Crypt2 31.11 -- because Valheim places land locations
#     with minAltitude 1, while ShipWreck01..04 sit at 29.0..30.9.
# So land is height > 30, and `min_height_m` is freeboard ABOVE that.
WATER_LEVEL = 30.0

# Default freeboard: metres the LOWEST cell of a footprint must clear the water
# plane by. "Above water" is not a usable test -- a pad at +0.01 m is the
# waterline. 1.0 m is not a taste pick: it is the game's own definition of land.
# MEASURED in the 12 301-instance Pirate68 ZoneSystem dump, every land location
# type floors just above 31 (Grave1 30.50, Ruin1 31.02, StoneHouse4 31.04,
# Crypt2 31.11) because Valheim places land locations with minAltitude 1, while
# ShipWreck01..04 sit at 29.0..30.9. So "one metre clear of 30" is where Iron
# Gate itself draws the line. Requirements may override per placement, and the
# exact freeboard of every solved site is recorded either way.
DEFAULT_FREEBOARD = 1.0

# The location dump carries no exterior/clear radius (MEASURED: its records are
# name/prefab/biome/group/unique/icon/x/y/z/placed only), and ZoneSystem
# re-cuts terrain inside a location's clear area when it spawns. A candidate
# site inside that area will fight the generator, so keep a conservative
# stand-off from every placed location instead of guessing per-prefab radii.
DEFAULT_LOCATION_CLEARANCE = 60.0

# Extra metres of 1 m sampling around a candidate footprint. The refinement pass
# slides the footprint inside this margin to find the flattest 1 m-aligned
# offset, so the margin is also the maximum distance a site may move during
# refinement.
PATCH_PAD = 16.0

# Score weights, summing to 1. Every term is normalised with 1 = best, so this
# dict is the only place the solver expresses taste and the only thing to argue
# with. `water` is folded into `flat` when the requirement is not coastal,
# because water proximity is then neither wanted nor unwanted.
WEIGHTS = {
    "flat": 0.35,
    "anchor": 0.20,
    "reach": 0.15,
    "clearance": 0.12,
    "purity": 0.08,
    "water": 0.10,
}

# Resolution of the land graph used for overland routing (metres).
ROUTE_STEP = 32.0


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


def load_patches(path: Path) -> dict[str, dict]:
    """Read a VHPATCH1 file from run_patchscan.sh, keyed by request id."""
    b = Path(path).read_bytes()
    if b[:8] != PATCH_MAGIC:
        raise ValueError(f"{path}: bad magic {b[:8]!r}")
    off = 8
    (_seed_hash,) = struct.unpack_from("<i", b, off)
    off += 4
    (nl,) = struct.unpack_from("<i", b, off)
    off += 4
    off += nl
    (count,) = struct.unpack_from("<i", b, off)
    off += 4
    out: dict[str, dict] = {}
    for _ in range(count):
        (il,) = struct.unpack_from("<i", b, off)
        off += 4
        pid = b[off : off + il].decode("utf-8")
        off += il
        cx, cz, half, pstep = struct.unpack_from("<ffff", b, off)
        off += 16
        (n,) = struct.unpack_from("<i", b, off)
        off += 4
        hgt = np.frombuffer(b, "<f4", n * n, off).reshape(n, n)
        off += 4 * n * n
        bio = np.frombuffer(b, np.uint8, n * n, off).reshape(n, n)
        off += n * n
        out[pid] = {
            "cx": float(cx),
            "cz": float(cz),
            "half": float(half),
            "step": float(pstep),
            "n": n,
            "height": hgt,
            "biome": bio,
        }
    return out


def axis(n: int, step: int, extent: int) -> np.ndarray:
    return -extent + np.arange(n) * step + step * 0.5


def grade_cap(p: dict) -> float:
    """Absolute height spread the HARD grade cap allows, in metres.

    `max_grade_pct` is spread over the footprint's long side, so one number
    means the same thing for a 48 m harbour and a 32 m jetty -- but as a pure
    percentage it becomes incoherent on small pads: 10 % of a 6.5 m portal
    footprint is 0.65 m, which is STRICTER in absolute terms than anything else
    in the tree and rejects ground no player would notice was sloped.

    So the cap is floored at TWICE `max_flat_m`, the placement's own costed
    flatness tolerance. Flooring it at exactly `max_flat_m` was the first
    attempt and it was wrong in the mirror-image way: it made the hard gate
    identical to the cheap threshold, so the gate went back to re-litigating
    flatness and rejected three sites by 0.2 to 0.31 m. This gate exists to
    exclude BANKS AND CLIFFS -- ground that is the wrong shape at any price --
    not to re-price slopes, which `flatten_cost` already does. Twice the costed
    tolerance is that boundary.
    """
    pct = max(footprint_extent(p)) * p["max_grade_pct"] * 0.01
    return max(pct, 2.0 * p["max_flat_m"])


def window_cells(p: dict, step: float) -> tuple[int, int]:
    """Odd (rows, cols) = (z, x) cell counts covering the footprint.

    Rectangular, because a square window is the wrong shape for anything that
    meets the water and shorelines are LINEAR. MEASURED on Pirate68 for
    sandbox-harbour's gentle-beach band: a 48 x 48 m square finds 0 qualifying
    shelves within 1400 m of the start temple at ANY radius out to 6 km, while
    the building's real 42 x 29 m rectangle finds 2 -- and the transposed
    29 x 42 m finds 0 again. Shape and orientation both matter, so the solver
    tries both orientations and records which one it assumed.

    `footprint_xz_m` declares the rectangle; without it the square
    `footprint_m` is used and nothing changes for inland placements.
    """
    fx, fz = footprint_extent(p)
    return footprint_cells(fz, step), footprint_cells(fx, step)


def footprint_extent(p: dict) -> tuple[float, float]:
    """(x, z) metres the footprint occupies, honouring `_swap`."""
    rect = p.get("footprint_xz_m")
    if not rect:
        return float(p["footprint_m"]), float(p["footprint_m"])
    fx, fz = float(rect[0]), float(rect[1])
    return (fz, fx) if p.get("_swap") else (fx, fz)


def footprint_cells(footprint_m: float, step: float) -> int:
    """Odd cell count covering the footprint, so windows are centred on a cell.

    Odd is what makes every reported coordinate the true centre of the window it
    was scored on; with an even window the site would sit half a cell off every
    distance it is measured against.
    """
    k = int(math.ceil(footprint_m / step))
    k = k + 1 if k % 2 == 0 else k
    # Never fewer than 3 cells. A footprint smaller than the grid step
    # degenerates to a ONE-cell window, where max == min, so the coarse pass
    # reports zero spread and a single cell's height and is simply blind.
    # MEASURED: pre-kall's 3.9 x 6.5 m portal reported 9687 "feasible" cells
    # that way, every one of which failed at 1 m. Three cells makes the coarse
    # pass evaluate the 24 m neighbourhood instead, which is conservative in the
    # right direction -- it can over-reject, never over-admit -- and the 1 m pass
    # then measures the real footprint.
    return max(k, 3)


def _minmax(g: dict, k) -> tuple[np.ndarray, np.ndarray]:
    """Centred k*k min and max of the height plane, cached per k.

    Separable rank filters, O(n^2) rather than the O(n^2 k^2) of an explicit
    sliding window: at k=9 on a 2624^2 grid that is the difference between a
    half-minute and a blink, and the solver runs this once per anchor instance.
    Windows that overhang the world edge are poisoned by the constant border so
    they can never win.
    """
    from scipy import ndimage

    cache = g.setdefault("_minmax", {})
    key = tuple(k) if isinstance(k, tuple) else k
    hit = cache.get(key)
    if hit is None:
        h = g["height"].astype(np.float32)
        hmax = ndimage.maximum_filter(h, size=k, mode="constant", cval=np.float32(1e9))
        hmin = ndimage.minimum_filter(h, size=k, mode="constant", cval=np.float32(-1e9))
        hit = (hmin, hmax)
        cache[key] = hit
    return hit


def _purity(g: dict, k, bid: int) -> np.ndarray:
    from scipy import ndimage

    cache = g.setdefault("_purity", {})
    key = ((tuple(k) if isinstance(k, tuple) else k), bid)
    hit = cache.get(key)
    if hit is None:
        mask = (g["biome"] == bid).astype(np.float32)
        hit = ndimage.uniform_filter(mask, size=k, mode="constant", cval=0.0)
        cache[key] = hit
    return hit


def _water_edt(g: dict) -> np.ndarray:
    """Metres from each cell to the nearest sub-sea-level cell."""
    hit = g.get("_water")
    if hit is None:
        from scipy import ndimage

        water = g["height"] <= WATER_LEVEL
        if water.any():
            hit = ndimage.distance_transform_edt(~water) * g["step"]
        else:
            hit = np.full(g["height"].shape, np.inf, dtype=np.float64)
        g["_water"] = hit
    return hit


def _biome_edt(g: dict, bid: int) -> np.ndarray:
    """Metres from each cell to the nearest cell of biome `bid`.

    This is what makes "serve a biome" expressible without having to BE in it.
    An iron-era workshop wants dry ground at the swamp's edge, not a foundation
    raft in the swamp -- MEASURED on Pirate68, the largest swamp square entirely
    above the water plane anywhere in the world is 16 m, against a 70 m
    footprint, so "a dry pad in a Swamp" is not a thing that exists.
    """
    cache = g.setdefault("_biomeedt", {})
    hit = cache.get(bid)
    if hit is None:
        from scipy import ndimage

        hit = ndimage.distance_transform_edt(g["biome"] != bid) * g["step"]
        cache[bid] = hit
    return hit


def _location_edt(g: dict, avoid_xz: np.ndarray) -> np.ndarray:
    """Metres from each cell to the nearest ZoneSystem location, cell-quantised.

    Used only as a full-grid PRE-filter; the surviving shortlist is re-checked
    exactly against the point set, because quantising a 60 m stand-off onto an
    8 m lattice is worth half a cell of error and no more.
    """
    hit = g.get("_locedt")
    if hit is None:
        from scipy import ndimage

        n, step, extent = g["n"], g["step"], g["extent"]
        occ = np.zeros((n, n), dtype=bool)
        ix = np.clip(((avoid_xz[:, 0] + extent) / step).astype(int), 0, n - 1)
        iz = np.clip(((avoid_xz[:, 1] + extent) / step).astype(int), 0, n - 1)
        occ[iz, ix] = True
        hit = ndimage.distance_transform_edt(~occ) * step
        g["_locedt"] = hit
    return hit


def _loc_tree(g: dict, avoid_xz: np.ndarray):
    hit = g.get("_loctree")
    if hit is None:
        from scipy.spatial import cKDTree

        hit = cKDTree(avoid_xz)
        g["_loctree"] = hit
    return hit


# --------------------------------------------------------------------------
# overland routing
# --------------------------------------------------------------------------


def _land_graph(g: dict) -> dict:
    """Coarse 8-connected land graph for overland routing.

    Downsampled to ROUTE_STEP with `any land in the block` so a one-cell isthmus
    still connects -- Valheim players walk 8 m land bridges. The result answers
    "can I get there on foot" and "how much longer than the straight line", both
    of which are robust to 32 m quantisation; it is NOT a metre-accurate path.
    """
    hit = g.get("_landgraph")
    if hit is not None:
        return hit
    from scipy import ndimage
    from scipy.sparse import coo_matrix

    step, n, extent = g["step"], g["n"], g["extent"]
    f = max(1, int(round(ROUTE_STEP / step)))
    m = n // f
    land = (g["height"][: m * f, : m * f] > WATER_LEVEL).reshape(m, f, m, f).any(axis=(1, 3))
    lab, _ = ndimage.label(land, structure=np.ones((3, 3), dtype=int))

    idx = -np.ones((m, m), dtype=np.int64)
    rr, cc = np.nonzero(land)
    idx[rr, cc] = np.arange(rr.size)
    cell = step * f
    rows: list[np.ndarray] = []
    cols: list[np.ndarray] = []
    data: list[np.ndarray] = []
    for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
        a = idx[
            max(0, -dr) : m - max(0, dr),
            max(0, -dc) : m - max(0, dc),
        ]
        b = idx[
            max(0, dr) : m - max(0, -dr),
            max(0, dc) : m - max(0, -dc),
        ]
        ok = (a >= 0) & (b >= 0)
        w = cell * math.hypot(dr, dc)
        rows.append(a[ok])
        cols.append(b[ok])
        data.append(np.full(ok.sum(), w))
    r = np.concatenate(rows)
    c = np.concatenate(cols)
    d = np.concatenate(data)
    nn = rr.size
    graph = coo_matrix(
        (np.concatenate([d, d]), (np.concatenate([r, c]), np.concatenate([c, r]))),
        shape=(nn, nn),
    ).tocsr()
    hit = {
        "m": m,
        "cell": cell,
        "extent": extent,
        "land": land,
        "label": lab,
        "idx": idx,
        "rr": rr,
        "cc": cc,
        "graph": graph,
        "_dist": {},
    }
    g["_landgraph"] = hit
    return hit


def _snap(lg: dict, x: float, z: float, search_m: float = 96.0) -> int | None:
    """Nearest land node to a world point, or None if the point is well at sea."""
    m, cell, extent = lg["m"], lg["cell"], lg["extent"]
    ci = int((x + extent) / cell)
    ri = int((z + extent) / cell)
    rad = int(math.ceil(search_m / cell))
    best: tuple[float, int] | None = None
    for dr in range(-rad, rad + 1):
        for dc in range(-rad, rad + 1):
            r, c = ri + dr, ci + dc
            if not (0 <= r < m and 0 <= c < m):
                continue
            node = lg["idx"][r, c]
            if node < 0:
                continue
            d = math.hypot(dr, dc) * cell
            if best is None or d < best[0]:
                best = (d, int(node))
    return None if best is None else best[1]


def overland(g: dict, src: tuple[float, float], dst: tuple[float, float]) -> dict:
    """Is `dst` walkable from `src`, and how much longer than the straight line?

    Returns `reachable`, `straight_m`, and when reachable `path_m` and `detour`.
    `reachable: false` means a boat (or a portal) is required.
    """
    from scipy.sparse.csgraph import dijkstra

    lg = _land_graph(g)
    straight = math.hypot(dst[0] - src[0], dst[1] - src[1])
    a = _snap(lg, *src)
    b = _snap(lg, *dst)
    if a is None or b is None:
        return {"reachable": False, "straight_m": round(straight, 1), "reason": "endpoint at sea"}
    if lg["label"][lg["rr"][a], lg["cc"][a]] != lg["label"][lg["rr"][b], lg["cc"][b]]:
        return {
            "reachable": False,
            "straight_m": round(straight, 1),
            "reason": "different landmass -- needs a boat",
        }
    cache = lg["_dist"]
    if a not in cache:
        cache[a] = dijkstra(lg["graph"], indices=a, directed=False)
    d = float(cache[a][b])
    if not math.isfinite(d):
        return {"reachable": False, "straight_m": round(straight, 1), "reason": "no land path"}
    return {
        "reachable": True,
        "straight_m": round(straight, 1),
        "path_m": round(d, 1),
        "detour": round(d / straight, 3) if straight > 1 else 1.0,
    }


def reach_plane(g: dict, spawn_xz: tuple[float, float] | None) -> np.ndarray | None:
    """Per-cell mask: is this cell on the same landmass as the spawn?

    MEASURED on Pirate68: 17 land components, the two biggest holding 137 k and
    92 k of 279 k coarse land cells, and their closest approach anywhere is 64 m
    of open water. A base on the wrong one is a boat trip every time, which is a
    real difference between two otherwise identical sites -- so it is scored,
    not merely reported. It is never a hard gate: whole biomes (Deep North,
    Ash Lands) can sit on their own landmass, and refusing to place there would
    be worse than saying "bring a longship".
    """
    if spawn_xz is None:
        return None
    lg = _land_graph(g)
    key = (round(spawn_xz[0], 1), round(spawn_xz[1], 1))
    cache = g.setdefault("_reach", {})
    if key in cache:
        return cache[key]
    node = _snap(lg, *spawn_xz)
    if node is None:
        cache[key] = None
        return None
    lab = lg["label"][lg["rr"][node], lg["cc"][node]]
    f = max(1, int(round(ROUTE_STEP / g["step"])))
    coarse = lg["label"] == lab
    plane = np.zeros((g["n"], g["n"]), dtype=bool)
    big = np.kron(coarse, np.ones((f, f), dtype=bool))
    plane[: big.shape[0], : big.shape[1]] = big[: g["n"], : g["n"]]
    cache[key] = plane
    return plane


# --------------------------------------------------------------------------
# search
# --------------------------------------------------------------------------


# Two classes of constraint, and the distinction is the whole design:
#
#   HARD    -- nothing we build fixes it. Biome, biome purity, freeboard over
#              the WHOLE footprint, clearance from placed ZoneSystem locations,
#              coastality, anchor distance, and overland reachability when the
#              requirement asks for it. A site that fails one is REJECTED.
#   COSTED  -- terrain work fixes it, so it is a bill, not a verdict. Flatness
#              is the only one: every placement declares `flatten_required:
#              true` and is placed from the admin seat with Infinity Hammer and
#              World Edit Commands, both of which level ground. Demanding a
#              naturally flat 70 m pad asks for something Valheim's generator
#              almost never makes -- MEASURED on Pirate68, requiring it failed 8
#              of 13 placements. `max_flat_m` is now the tolerance that defines
#              a CHEAP site, not a gate, and `flatten_cost` reports the bill.
HARD_CONSTRAINTS = (
    "biome",
    "min_biome_purity",
    "min_height_m",
    "location_clearance_m",
    "coastal_within_m",
    "within_m",
    "require_overland",
    "near_biome",
    "max_height_m",
    "max_grade_pct",
)
COSTED_CONSTRAINTS = ("max_flat_m",)


def requirement_defaults(req: dict) -> dict:
    """Normalise a placements.yaml `requirement` block into solver parameters."""
    return {
        "biome": req["biome"],
        "footprint_m": float(req.get("footprint_m", 56)),
        "max_flat_m": float(req["max_flat_m"]),
        "min_biome_purity": float(req.get("min_biome_purity", 0.85)),
        "coastal": bool(req.get("coastal", False)),
        "coastal_within_m": float(req.get("coastal_within_m", 120)),
        "within_m": float(req.get("within_m", 1500)),
        "location_clearance_m": float(req.get("location_clearance_m", DEFAULT_LOCATION_CLEARANCE)),
        "min_height_m": float(req.get("min_height_m", DEFAULT_FREEBOARD)),
        # HARD when declared true. Left false by default deliberately: a Deep
        # North landing or an Ash Lands forward base is reached by longship or
        # portal by design, and refusing to place one because you cannot walk
        # there from spawn would be inventing a requirement nobody asked for.
        "require_overland": bool(req.get("require_overland", False)),
        # HARD when declared: the site must be within `within_biome_m` of the
        # named biome without being in it. "Serve the swamp" rather than "be in
        # the swamp".
        "near_biome": req.get("near_biome"),
        "within_biome_m": float(req.get("within_biome_m", 200)),
        # Freeboard is a BAND, not a floor, for anything whose purpose is to meet
        # the water. Expressed as a floor alone, `min_height_m` + `coastal_within_m`
        # reads as "high ground near the sea", and on a real coastline that means
        # a CLIFFTOP -- MEASURED, it cost sandbox-harbour a 43.5 m spread and a
        # 23 419 m3 levelling bill for something you are supposed to walk a boat
        # up to. `max_height_m` caps the usable freeboard so the site is a shore
        # and not a headland. Default infinite: inland placements want no ceiling.
        "max_height_m": float(req.get("max_height_m", float("inf"))),
        # Grade cap, HARD, as a percentage of the footprint side: height spread
        # divided by footprint_m. This is deliberately NOT the same thing as the
        # costed `max_flat_m` -- that is a bill for levelling, this is the SHAPE
        # of the ground, and a 43.5 m drop across 48 m is not a beach at any
        # height or any price. Footprint-relative so one number means the same
        # thing for a 32 m jetty and an 80 m base.
        "max_grade_pct": float(req.get("max_grade_pct", float("inf"))),
        # Optional [x, z] extent in metres. When present the search window is
        # that rectangle rather than a `footprint_m` square, and both
        # orientations are tried.
        "footprint_xz_m": req.get("footprint_xz_m"),
        "_swap": bool(req.get("_swap", False)),
    }


def _planes(
    g: dict,
    req: dict,
    anchor_xz: tuple[float, float] | None,
    avoid_xz: np.ndarray | None,
    exclude: list[tuple[float, float, float]] | None,
    spawn_xz: tuple[float, float] | None = None,
) -> dict:
    """Full-grid HARD-constraint mask plus the planes every score needs.

    Every HARD constraint is applied HERE, to the whole grid, before anything is
    truncated. Flatness is NOT among them -- see HARD_CONSTRAINTS: it is costed,
    not gated, because every placement is levelled on arrival.
    """
    p = requirement_defaults(req)
    step, n, extent = g["step"], g["n"], g["extent"]
    bid = BIOME_ID[p["biome"].lower()]
    kz, kx = window_cells(p, step)
    if max(kz, kx) > n:
        raise ValueError("footprint larger than the world")
    fx, fz = footprint_extent(p)
    half_diag = math.hypot(fx, fz) * 0.5
    long_side = max(fx, fz)

    hmin, hmax = _minmax(g, (kz, kx))
    flat = hmax - hmin
    pure = _purity(g, (kz, kx), bid)

    xs = axis(n, step, extent)
    cz2d, cx2d = np.meshgrid(xs, xs, indexing="ij")

    # Dry land with real freeboard. `min_height_m` is metres ABOVE the water
    # plane, not an absolute height: a footprint whose lowest cell sits at 30.05
    # is the waterline, where a build ends up with its foundations in the sea.
    ok = hmin > WATER_LEVEL + p["min_height_m"]
    if math.isfinite(p["max_height_m"]):
        ok &= hmin <= WATER_LEVEL + p["max_height_m"]
    if math.isfinite(p["max_grade_pct"]):
        ok &= flat <= long_side * p["max_grade_pct"] * 0.01
    ok &= pure >= p["min_biome_purity"]
    reach = reach_plane(g, spawn_xz)
    if p["require_overland"] and reach is not None:
        ok &= reach

    if p["near_biome"]:
        bdist = _biome_edt(g, BIOME_ID[str(p["near_biome"]).lower()])
        ok &= bdist <= p["within_biome_m"]
    else:
        bdist = np.zeros_like(flat)

    if anchor_xz is not None:
        adist = np.hypot(cx2d - anchor_xz[0], cz2d - anchor_xz[1])
        ok &= adist <= p["within_m"]
    else:
        adist = np.hypot(cx2d, cz2d)

    wdist = _water_edt(g)
    if p["coastal"]:
        ok &= wdist <= p["coastal_within_m"]

    if avoid_xz is not None and len(avoid_xz):
        # cell-quantised pre-filter; the surviving shortlist is re-checked
        # exactly against the point set
        need = p["location_clearance_m"] + half_diag
        ok &= _location_edt(g, avoid_xz) >= need - step * math.sqrt(2.0) * 0.5

    # Sites already taken by earlier placements in the same preset. Without
    # this, two placements with similar requirements resolve to the identical
    # flattest cell and the second building lands inside the first.
    for ex, ez, er in exclude or ():
        ok &= np.hypot(cx2d - ex, cz2d - ez) > er

    return {
        "p": p,
        "ok": ok,
        "flat": flat,
        "pure": pure,
        "hmax": hmax,
        "hmin": hmin,
        "adist": adist,
        "wdist": wdist,
        "bdist": bdist,
        "reach": reach,
        "cx2d": cx2d,
        "cz2d": cz2d,
        "half_diag": half_diag,
        "kz": kz,
        "kx": kx,
    }


def flat_floor(
    g: dict,
    req: dict,
    anchor_xz: tuple[float, float] | None,
    avoid_xz: np.ndarray | None = None,
    exclude: list[tuple[float, float, float]] | None = None,
) -> float | None:
    """Flattest GRID-ALIGNED footprint available under every OTHER constraint.

    For any window on the coarse lattice the coarse min/max are a subset of the
    1 m samples inside it, so this is a lower bound on that window's true spread.
    It is NOT a bound over arbitrary 1 m offsets -- the refinement pass slides
    the footprint off the lattice and can beat it (MEASURED on Pirate68: it did
    so once in nine, by 0.18 m). Treat it as "what the seed has to offer, to
    within a fraction of a metre": when it sits well above `max_flat_m`, no
    amount of further searching will satisfy the requirement, and saying that is
    more use than another search.
    """
    pl = _planes(g, req, anchor_xz, avoid_xz, exclude)
    if not pl["ok"].any():
        return None
    return float(pl["flat"][pl["ok"]].min())


def freeboard_ceiling(
    g: dict,
    req: dict,
    anchor_xz: tuple[float, float] | None,
    avoid_xz: np.ndarray | None = None,
) -> tuple[float, int] | None:
    """Best footprint-minimum freeboard available under every OTHER constraint.

    Returns `(metres_above_water, qualifying_cells)`. When the best is negative
    there is NO dry pad of this size in this seed satisfying the rest of the
    requirement, and the solver's negative `freeboard_m` is not a poor choice but
    the only choice. That distinction is the difference between "the solver put
    the base in a swamp" and "the biome is a swamp".
    """
    alt = dict(req)
    alt["min_height_m"] = -1e9
    pl = _planes(g, alt, anchor_xz, avoid_xz, None)
    if not pl["ok"].any():
        return None
    fb = pl["hmin"][pl["ok"]] - WATER_LEVEL
    return float(fb.max()), int((fb >= req.get("min_height_m", DEFAULT_FREEBOARD)).sum())


def flatten_cost(window: np.ndarray) -> dict:
    """The terrain-work bill for levelling one footprint, from 1 m samples.

    The target plane is the footprint's MEDIAN height, which is the level that
    minimises total material moved, and is what an operator levelling by eye
    converges on anyway. Everything is per square metre of pad, so the figures
    are directly comparable between a 32 m and an 80 m footprint.

      cut_m3 / fill_m3  material above / below the target, cubic metres
      moved_m3          cut + fill, the whole bill
      mean_move_m       moved_m3 / area -- average depth of work
      rms_m             root-mean-square deviation from the target plane
      max_cut_m         worst single cut (a boulder or a spur)
      max_fill_m        worst single fill (a hollow or a channel)

    `max_fill_m` is the one to read with suspicion: filling is raising terrain,
    and raising it over water is a different job from raising it over land.
    """
    area = float(window.size)
    target = float(np.median(window))
    dev = window.astype(np.float64) - target
    cut = float(dev[dev > 0].sum())
    fill = float(-dev[dev < 0].sum())
    return {
        "target_y": round(target, 2),
        "cut_m3": round(cut, 1),
        "fill_m3": round(fill, 1),
        "moved_m3": round(cut + fill, 1),
        "mean_move_m": round((cut + fill) / area, 3),
        "rms_m": round(float(np.sqrt((dev * dev).mean())), 3),
        "max_cut_m": round(float(dev.max()), 2),
        "max_fill_m": round(float(-dev.min()), 2),
    }


def miss_vector(cand: dict, req: dict) -> np.ndarray:
    """Total metres by which each candidate breaks the DECLARED requirement.

    HARD constraints only -- flatness is costed, not missed, so it is absent
    here on purpose. The vectorised twin of `violations`, and it has to stay in
    step with it: every constraint `violations` reports, this one sums. Purity is
    a fraction rather than a distance, so it is weighted into metres by the
    footprint side -- a tenth of the pad in the wrong biome is a tenth of a
    footprint wrong.
    """
    p = requirement_defaults(req)
    half_diag = math.hypot(*footprint_extent(p)) * 0.5
    zero = np.zeros_like(cand["flat"])
    m = np.maximum(zero, p["min_biome_purity"] - cand["purity"]) * p["footprint_m"]
    if req.get("near") or req.get("near_xz"):
        m = m + np.maximum(zero, cand["anchor"] - p["within_m"])
    if p["coastal"]:
        m = m + np.maximum(zero, cand["water"] - p["coastal_within_m"])
    m = m + np.maximum(
        zero, (p["location_clearance_m"] + half_diag) - cand["locdist"]
    )
    m = m + np.maximum(zero, p["min_height_m"] - cand["freeboard"])
    if p["near_biome"]:
        m = m + np.maximum(
            zero, cand.get("biome_dist", zero) - p["within_biome_m"]
        )
    if math.isfinite(p["max_height_m"]):
        m = m + np.maximum(zero, cand["freeboard"] - p["max_height_m"])
    if math.isfinite(p["max_grade_pct"]):
        m = m + np.maximum(
            zero, cand["flat"] - grade_cap(p)
        )
    return m


def miss(d: dict, req: dict) -> float:
    """`miss_vector` for a single candidate dict, same weighting.

    Kept beside the vector form deliberately: the coarse search ranks with the
    vector and the final choice ranks with this, and if the two ever disagree
    the solver picks a site its own search did not surface.
    """
    cand = {
        "flat": np.array([d.get("flat", 0.0)]),
        "purity": np.array([d["biome_purity"]]),
        "anchor": np.array([d["anchor_dist"]]),
        "water": np.array([d["water_dist"]]),
        "locdist": np.array([d.get("location_dist", np.inf)]),
        "freeboard": np.array([d.get("freeboard_m", np.inf)]),
        "biome_dist": np.array([d.get("near_biome_dist_m", 0.0)]),
    }
    return float(miss_vector(cand, req)[0])



def search(
    g: dict,
    req: dict,
    anchor_xz: tuple[float, float] | None,
    avoid_xz: np.ndarray | None = None,
    top: int = 8,
    exclude: list[tuple[float, float, float]] | None = None,
    separation_m: float | None = None,
    spawn_xz: tuple[float, float] | None = None,
    declared_req: dict | None = None,
) -> list[dict]:
    """Ranked, spatially separated candidate sites for one requirement.

    Every constraint in `req` is a hard full-grid mask (see `_planes`) applied
    before anything is truncated.

    `flat_slack` widens ONLY the flatness gate, and only for a coarse pass whose
    survivors will be re-decided at 1 m. The coarse grid under-reports spread
    (MEASURED on Pirate68 at a 70 m footprint: median 1.4 m, p90 8.0 m low), so
    a coarse gate set exactly at `max_flat_m` throws away sites that are flat in
    reality. Recall is the coarse pass's job; the verdict belongs to `refine`.
    """
    pl = _planes(g, req, anchor_xz, avoid_xz, exclude, spawn_xz)
    p, ok, step = pl["p"], pl["ok"], g["step"]
    flat, pure, hmax = pl["flat"], pl["pure"], pl["hmax"]
    hmin = pl["hmin"]
    adist, wdist, reach, bdist = pl["adist"], pl["wdist"], pl["reach"], pl["bdist"]
    cx2d, cz2d, half_diag = pl["cx2d"], pl["cz2d"], pl["half_diag"]
    kz, kx = pl["kz"], pl["kx"]

    idx = np.argwhere(ok)
    if idx.size == 0:
        return []

    r, c = idx[:, 0], idx[:, 1]
    cand = {
        "row": r.astype(np.float64),
        "col": c.astype(np.float64),
        "x": cx2d[r, c],
        "z": cz2d[r, c],
        "y": hmax[r, c],
        # metres of the footprint's LOWEST cell above the water plane; negative
        # means part of the pad is under water, which in a Swamp is normal and
        # everywhere else is a mistake
        "freeboard": hmin[r, c] - WATER_LEVEL,
        "flat": flat[r, c],
        "purity": pure[r, c],
        "water": wdist[r, c],
        "anchor": adist[r, c],
        "biome_dist": bdist[r, c],
        "reach": (
            reach[r, c].astype(np.float64)
            if reach is not None
            else np.ones(r.shape, dtype=np.float64)
        ),
    }

    if avoid_xz is not None and len(avoid_xz):
        tree = _loc_tree(g, avoid_xz)
        ld, _ = tree.query(np.stack([cand["x"], cand["z"]], axis=1), k=1)
        keep = ld >= p["location_clearance_m"] + half_diag
        if not keep.any():
            return []
        cand = {kk: v[keep] for kk, v in cand.items()}
        cand["locdist"] = ld[keep]
    else:
        cand["locdist"] = np.full(cand["x"].shape, np.inf)

    scores = _score(cand, p)
    if declared_req is None:
        order = np.argsort(-scores)
    else:
        # The requirement being searched has been widened, so its own score says
        # nothing useful about how badly the DECLARED requirement is broken --
        # and truncating by score here is what used to hide the answer. MEASURED:
        # for pre-elder the least-violating site is a dry 6.97 m slope, but it
        # scores terribly on flatness, so a score-truncated pool never contained
        # it and the solver returned a 5.6 m pad two metres under water instead.
        miss = miss_vector(cand, declared_req)
        order = np.lexsort((-scores, np.round(miss, 2)))

    # Spatial non-maximum suppression: five adjacent cells of one meadow are one
    # site, not five, and an operator choosing from a shortlist needs genuinely
    # different options.
    sep = separation_m if separation_m is not None else max(footprint_extent(p)) * 1.5
    out: list[dict] = []
    px: list[float] = []
    pz: list[float] = []
    for i in order:
        xi, zi = float(cand["x"][i]), float(cand["z"][i])
        if any((xi - a) ** 2 + (zi - b) ** 2 < sep * sep for a, b in zip(px, pz)):
            continue
        px.append(xi)
        pz.append(zi)
        out.append(
            {
                "x": xi,
                "z": zi,
                "y": float(cand["y"][i]),
                "freeboard_m": float(cand["freeboard"][i]),
                # The bill, priced on the COARSE grid here and repriced at 1 m by
                # `refine`. `resolution_m` says which, because an estimate and a
                # measurement must never share a field without saying so.
                "flatten_cost": dict(
                    flatten_cost(
                        g["height"][
                            max(0, int(cand["row"][i]) - kz // 2) : int(cand["row"][i]) + kz // 2 + 1,
                            max(0, int(cand["col"][i]) - kx // 2) : int(cand["col"][i]) + kx // 2 + 1,
                        ]
                    ),
                    resolution_m=float(step),
                    estimated=True,
                ),
                "flat": float(cand["flat"][i]),
                "biome_purity": float(cand["purity"][i]),
                "water_dist": float(cand["water"][i]),
                "anchor_dist": float(cand["anchor"][i]),
                "near_biome_dist_m": float(cand["biome_dist"][i]),
                "location_dist": float(cand["locdist"][i]),
                "overland_from_spawn": bool(cand["reach"][i]),
                "score": float(scores[i]),
                "scores": _score_terms(cand, p, i),
                "flat_resolution_m": float(step),
                "refined": False,
            }
        )
        if len(out) >= top:
            break
    return out


def _score_vectors(cand: dict, p: dict) -> dict[str, np.ndarray]:
    clear_need = max(1.0, p["location_clearance_m"])
    terms = {
        # `max_flat_m` is now a TOLERANCE, not a gate: a pad inside it is free,
        # one outside it costs terrain work, and the score says how much rather
        # than refusing. Uses the measured mean_move_m once refined, the coarse
        # spread as a proxy before that.
        "flat": 1.0 - cand["flat"] / max(p["max_flat_m"], 1e-6),
        "anchor": 1.0 - cand["anchor"] / max(p["within_m"], 1e-6),
        "clearance": np.clip((cand["locdist"] - clear_need) / clear_need, 0.0, 1.0),
        "purity": (
            (cand["purity"] - p["min_biome_purity"]) / (1.0 - p["min_biome_purity"])
            if p["min_biome_purity"] < 1.0
            else np.ones_like(cand["purity"])
        ),
        "water": (
            1.0 - cand["water"] / max(p["coastal_within_m"], 1e-6)
            if p["coastal"]
            else np.zeros_like(cand["water"])
        ),
        "reach": np.asarray(cand.get("reach", 1.0), dtype=np.float64) * np.ones_like(cand["flat"]),
    }
    # Capped above at 1 (no credit for exceeding a requirement) but NOT floored
    # at 0: a coarse pass run with `flat_slack` deliberately admits sites over
    # budget, and flooring would collapse all of them to the same score and make
    # the shortlist order arbitrary. A negative term means "worse than asked
    # for", which is exactly what the ranking should say.
    return {kk: np.minimum(v, 1.0) for kk, v in terms.items()}


def _weights(p: dict) -> dict[str, float]:
    w = dict(WEIGHTS)
    if not p["coastal"]:
        w["flat"] += w.pop("water")
        w["water"] = 0.0
    return w


def _score(cand: dict, p: dict) -> np.ndarray:
    t = _score_vectors(cand, p)
    w = _weights(p)
    return sum(w[kk] * t[kk] for kk in t)


def _score_terms(cand: dict, p: dict, i: int) -> dict[str, float]:
    t = _score_vectors(cand, p)
    return {kk: round(float(v[i]), 3) for kk, v in t.items()}


def rescore(d: dict, req: dict) -> None:
    """Recompute `score`/`scores` in place after refinement moved the numbers."""
    p = requirement_defaults(req)
    cand = {
        "flat": np.array([d["flat"]]),
        "anchor": np.array([d["anchor_dist"]]),
        "locdist": np.array([d["location_dist"]]),
        "purity": np.array([d["biome_purity"]]),
        "water": np.array([d["water_dist"]]),
        "reach": np.array([1.0 if d.get("overland_from_spawn", True) else 0.0]),
    }
    d["scores"] = _score_terms(cand, p, 0)
    d["score"] = float(_score(cand, p)[0])


# --------------------------------------------------------------------------
# 1 m refinement
# --------------------------------------------------------------------------


def patch_request(cands: list[dict], req: dict, prefix: str) -> list[tuple[str, float, float, float, float]]:
    """Patch rows for run_patchscan.sh, one per candidate."""
    fp = float(req.get("footprint_m", 56))
    half = fp * 0.5 + PATCH_PAD
    return [(f"{prefix}#{i}", d["x"], d["z"], half, 1.0) for i, d in enumerate(cands)]


def refine(
    d: dict, patch: dict, req: dict, anchor_xz, avoid_xz, g: dict, spawn_xz=None,
    declared_req: dict | None = None,
) -> dict:
    """Re-decide one candidate at 1 m, sliding the footprint to its best offset.

    The patch is `footprint + 2*PATCH_PAD` metres square at 1 m. Every whole-metre
    offset of the footprint inside it is scored, the flattest is taken, and the
    distances that the move invalidates (anchor, clearance, water) are recomputed
    exactly for the new centre. Returns the candidate with 1 m `flat`,
    1 m `biome_purity`, a `moved_m` record and `refined: True`.
    """
    from scipy import ndimage

    # A patch is only meaningful for the candidate it was cut around. Reusing a
    # stale patch file silently pairs candidate i with someone else's window,
    # which reads as a site that teleported and kept its old anchor. Cheap
    # assertion, unambiguous failure.
    if math.hypot(patch["cx"] - d["x"], patch["cz"] - d["z"]) > 1.0:
        raise ValueError(
            f"patch centre ({patch['cx']:.1f},{patch['cz']:.1f}) does not match "
            f"candidate ({d['x']:.1f},{d['z']:.1f}); the patch file is stale"
        )

    p = requirement_defaults(req)
    fp = int(round(p["footprint_m"]))
    h = patch["height"]
    bio = patch["biome"]
    n = patch["n"]
    if fp >= n:
        raise ValueError("patch smaller than the footprint")
    bid = BIOME_ID[p["biome"].lower()]

    k = fp if fp % 2 == 1 else fp + 1
    hmax = ndimage.maximum_filter(h, size=k, mode="constant", cval=np.float32(1e9))
    hmin = ndimage.minimum_filter(h, size=k, mode="constant", cval=np.float32(-1e9))
    pure = ndimage.uniform_filter((bio == bid).astype(np.float32), size=k, mode="constant", cval=0.0)
    pad = k // 2
    sl = slice(pad, n - pad)
    spread = (hmax - hmin)[sl, sl]
    pur = pure[sl, sl]
    top = hmax[sl, sl]

    # world coords of each retained sample centre
    base = patch["cx"] - patch["half"] + patch["step"] * 0.5
    basez = patch["cz"] - patch["half"] + patch["step"] * 0.5
    ii = np.arange(pad, n - pad)
    xs = base + ii * patch["step"]
    zs = basez + ii * patch["step"]
    zz, xx = np.meshgrid(zs, xs, indexing="ij")

    okm = pur >= p["min_biome_purity"]
    okm &= (hmin[sl, sl]) > WATER_LEVEL + p["min_height_m"]
    if anchor_xz is not None:
        ad = np.hypot(xx - anchor_xz[0], zz - anchor_xz[1])
        okm &= ad <= p["within_m"]
    else:
        ad = np.hypot(xx, zz)

    # Every HARD constraint has to hold at the offset the slide picks, not just
    # the ones that are cheap to evaluate. Sliding a site 16 m for flatness and
    # landing 6 m inside a crypt's clear area, or 1 m past the coastal budget,
    # is the same class of bug as the coarse pass filtering after truncation.
    ldm = np.full(xx.shape, np.inf)
    clear_ok = np.ones(xx.shape, dtype=bool)
    if avoid_xz is not None and len(avoid_xz):
        tree = _loc_tree(g, avoid_xz)
        pts = np.stack([xx.ravel(), zz.ravel()], axis=1)
        q, _ = tree.query(pts, k=1)
        ldm = q.reshape(xx.shape)
        clear_ok = ldm >= p["location_clearance_m"] + math.hypot(*footprint_extent(p)) * 0.5
        okm &= clear_ok
    wedt = _water_edt(g)
    gn, gstep, gext = g["n"], g["step"], g["extent"]
    gi = np.clip(((xx + gext) / gstep).astype(int), 0, gn - 1)
    gj = np.clip(((zz + gext) / gstep).astype(int), 0, gn - 1)
    wd = wedt[gj, gi]
    if p["coastal"]:
        okm &= wd <= p["coastal_within_m"]
    if math.isfinite(p["max_height_m"]):
        okm &= (hmin[sl, sl] - WATER_LEVEL) <= p["max_height_m"]
    if math.isfinite(p["max_grade_pct"]):
        okm &= spread <= grade_cap(p)
    bd = np.zeros(xx.shape)
    if p["near_biome"]:
        bedt = _biome_edt(g, BIOME_ID[str(p["near_biome"]).lower()])
        bd = bedt[gj, gi]
        okm &= bd <= p["within_biome_m"]

    # What the slide OPTIMISES. Flatness alone is wrong whenever the search had
    # to be widened: the patch then contains ground that is flatter but wetter,
    # and a flatness-only objective slides a dry candidate straight off its dry
    # ground. MEASURED on Pirate68: pre-elder's dry 7 m slope became a 6.2 m pad
    # 2.7 m under water, a worse answer to the question actually asked. With a
    # declared requirement in hand, minimise the miss against THAT; the spread
    # is only a tie-break.
    offsets = {
        "flat": spread,
        "purity": pur,
        "anchor": ad,
        "water": wd,
        "locdist": ldm,
        "freeboard": hmin[sl, sl] - WATER_LEVEL,
        "biome_dist": bd,
    }
    # Minimise the HARD miss first, then the terrain-work bill. Both are in
    # metres -- the miss in metres of broken constraint, the bill in mean metres
    # of material moved per square metre of pad -- so they add honestly, and
    # inside the hard gate (where the miss is zero) this is purely "pick the
    # cheapest pad to level".
    work = spread * 0.25  # cheap proxy for mean_move_m; the exact bill is
    # computed once, for the offset this picks
    objective = work if declared_req is None else miss_vector(offsets, declared_req) + work
    # When nothing inside the patch clears the gate, do NOT freeze at the patch
    # centre: that silently returns the coarse pick and throws the 1 m pass away.
    # Optimise the same objective unconstrained instead, so the answer is still
    # the best offset available, and let `violations` say what it costs.
    # Fall back in order: the full gate, then clearance alone, then nothing.
    # Clearance survives one step longer than the rest on purpose -- it is the
    # one hard constraint the slide can always honour inside a padded patch, and
    # trading 10 cm of stand-off from a crypt to save 6 cm of freeboard is not a
    # trade anybody wants made on their behalf.
    if okm.any():
        pen = np.where(okm, objective, np.inf)
    elif clear_ok.any():
        pen = np.where(clear_ok, objective, np.inf)
    else:
        pen = objective
    ci = np.unravel_index(int(np.argmin(pen)), pen.shape)

    # `flat` is max-minus-min, which is what the schema declares and what the
    # verdict is judged on -- but it is hostage to one boulder in one corner.
    # Report the p5..p95 spread alongside it so an operator can tell "4 m of
    # hoe work everywhere" from "flat except for one lump".
    ri, cj = int(ci[0]) + pad, int(ci[1]) + pad
    win = h[ri - pad : ri + pad + 1, cj - pad : cj + pad + 1]
    lo, hi = np.percentile(win, (5.0, 95.0))
    cost = flatten_cost(win)
    nx, nz = float(xx[ci]), float(zz[ci])
    moved = math.hypot(nx - d["x"], nz - d["z"])
    out = dict(d)
    out.update(
        {
            "x": nx,
            "z": nz,
            "y": float(top[ci]),
            "flat": float(spread[ci]),
            "freeboard_m": float(hmin[sl, sl][ci]) - WATER_LEVEL,
            "flatten_cost": dict(cost, resolution_m=1.0, estimated=False),
            "near_biome_dist_m": float(bd[ci]),
            "y_centre_m": float(h[ri, cj]),
            "flat_p5_p95_m": float(hi - lo),
            "biome_purity": float(pur[ci]),
            "anchor_dist": float(ad[ci]),
            "flat_resolution_m": 1.0,
            "flat_spread_coarse_m": round(d["flat"], 2),
            "moved_m": round(moved, 1),
            "refined": True,
        }
    )
    out["water_dist"] = float(_sample(g, _water_edt(g), nx, nz))
    if avoid_xz is not None and len(avoid_xz):
        tree = _loc_tree(g, avoid_xz)
        ld, _ = tree.query(np.array([[nx, nz]]), k=1)
        out["location_dist"] = float(ld[0])
    if spawn_xz is not None:
        rp = reach_plane(g, spawn_xz)
        out["overland_from_spawn"] = bool(_sample(g, rp, nx, nz)) if rp is not None else True
    rescore(out, req)
    return out


def _sample(g: dict, plane: np.ndarray, x: float, z: float) -> float:
    n, step, extent = g["n"], g["step"], g["extent"]
    ix = min(n - 1, max(0, int((x + extent) / step)))
    iz = min(n - 1, max(0, int((z + extent) / step)))
    return float(plane[iz, ix])


# --------------------------------------------------------------------------
# constraint verdict
# --------------------------------------------------------------------------


def facing_check(g: dict, x: float, z: float, yaw_deg: float, biome: str | None = None,
                 reach_m: float = 400.0, water: bool = False,
                 anchor_xz: tuple[float, float] | None = None) -> dict:
    """Does the declared yaw actually point at the biome it claims to face?

    A yaw is a number inherited from wherever the blueprint was captured, and
    "the workshop mouth faces the swamp it serves" is a claim about the world,
    not about the file. So walk outward along the yaw and report how much of the
    ray lands in the named biome, against the best of the four cardinal
    rotations. MEASURED convention (see rotation_convention in placements.yaml):
    +yaw turns the building clockwise seen from above, so the facing direction
    is (sin yaw, cos yaw).
    """
    n, step, extent = g["n"], g["step"], g["extent"]

    if anchor_xz is not None:
        # Nothing to ray-cast at: the facing names a POINT, so compare bearings.
        # Bearing convention matches rotation_convention: +yaw clockwise from
        # +z, i.e. direction (sin yaw, cos yaw), so bearing = atan2(dx, dz).
        bearing = math.degrees(math.atan2(anchor_xz[0] - x, anchor_xz[1] - z)) % 360.0
        err = abs((yaw_deg - bearing + 180.0) % 360.0 - 180.0)
        return {
            "faces": "anchor",
            "declared_yaw": yaw_deg,
            "bearing_to_anchor": round(bearing, 1),
            "error_deg": round(err, 1),
            "best_cardinal_yaw": int(round(bearing / 90.0) % 4) * 90,
            "verdict": (
                "declared yaw points at the anchor within 45 deg"
                if err <= 45.0
                else f"declared yaw is {err:.0f} deg off the anchor; bearing is {bearing:.0f}"
            ),
        }

    target = "open water" if water else str(biome)
    bid = None if water else BIOME_ID[str(biome).lower()]
    bio = g["biome"]
    hgt = g["height"]

    def fraction(deg: float) -> float:
        rad = math.radians(deg)
        dx, dz = math.sin(rad), math.cos(rad)
        hits = 0
        total = 0
        for t in range(8, int(reach_m) + 1, 8):
            ix = int((x + dx * t + extent) / step)
            iz = int((z + dz * t + extent) / step)
            if not (0 <= ix < n and 0 <= iz < n):
                break
            total += 1
            if (hgt[iz, ix] <= WATER_LEVEL) if water else (bio[iz, ix] == bid):
                hits += 1
        return hits / total if total else 0.0

    declared = fraction(yaw_deg)
    options = {int(d): round(fraction(float(d)), 3) for d in (0, 90, 180, 270)}
    best = max(options, key=lambda d: options[d])
    return {
        "faces": target,
        "declared_yaw": yaw_deg,
        "declared_yaw_biome_fraction": round(declared, 3),
        "by_cardinal_yaw": options,
        "best_cardinal_yaw": best,
        "reach_m": reach_m,
        "verdict": (
            # Relative to the best cardinal, with NO absolute floor. An absolute
            # threshold smuggles in an assumption about how close the target is:
            # a site 437 m from the swamp it serves can face it dead-on and still
            # have only a fraction of the ray inside it. What matters is that the
            # declared yaw is the best available direction, and that the best
            # available direction finds the target at all.
            "declared yaw points at the biome it claims to serve"
            if options[best] > 0.0 and declared >= options[best] - 0.1
            else f"declared yaw does NOT face {target}; yaw {best} does"
            if options[best] > 0.0
            else f"no yaw finds {target} within {reach_m:.0f} m of this site"
        ),
    }


def violations(d: dict, req: dict) -> list[dict]:
    """Every declared constraint this site fails, with the size of the miss.

    HARD constraints only. Flatness is not here: every placement declares
    `flatten_required: true` and is levelled on arrival, so a rough pad is a bill
    (`flatten_cost`), not a disqualification. Demanding natural flatness failed 8
    of 13 placements on Pirate68 for terrain the hoe fixes in minutes.

    This is the whole point: a solved block that disagrees with its own
    requirement must say so in the file, not bury it in a multiplier string.
    """
    p = requirement_defaults(req)
    half_diag = math.hypot(*footprint_extent(p)) * 0.5
    out: list[dict] = []

    def bad(name: str, required, actual, over) -> None:
        out.append(
            {
                "constraint": name,
                "required": round(float(required), 2),
                "actual": round(float(actual), 2),
                "over_by": round(float(over), 2),
            }
        )

    if d["biome_purity"] < p["min_biome_purity"] - 1e-6:
        bad(
            "min_biome_purity",
            p["min_biome_purity"],
            d["biome_purity"],
            p["min_biome_purity"] - d["biome_purity"],
        )
    if req.get("near") or req.get("near_xz"):
        if d["anchor_dist"] > p["within_m"] + 1e-6:
            bad("within_m", p["within_m"], d["anchor_dist"], d["anchor_dist"] - p["within_m"])
    if p["coastal"] and d["water_dist"] > p["coastal_within_m"] + 1e-6:
        bad(
            "coastal_within_m",
            p["coastal_within_m"],
            d["water_dist"],
            d["water_dist"] - p["coastal_within_m"],
        )
    need = p["location_clearance_m"] + half_diag
    if d.get("location_dist", float("inf")) < need - 1e-6:
        bad(
            "location_clearance_m",
            p["location_clearance_m"],
            d["location_dist"] - half_diag,
            need - d["location_dist"],
        )
    fb = d.get("freeboard_m")
    if fb is not None and fb < p["min_height_m"] - 1e-6:
        bad("min_height_m", p["min_height_m"], fb, p["min_height_m"] - fb)
    if math.isfinite(p["max_height_m"]) and fb is not None and fb > p["max_height_m"] + 1e-6:
        bad("max_height_m", p["max_height_m"], fb, fb - p["max_height_m"])
    if math.isfinite(p["max_grade_pct"]):
        cap = grade_cap(p)
        if d["flat"] > cap + 1e-6:
            bad("max_grade_pct", cap, d["flat"], d["flat"] - cap)
    if p["near_biome"]:
        bd = d.get("near_biome_dist_m")
        if bd is not None and bd > p["within_biome_m"] + 1e-6:
            bad("within_biome_m", p["within_biome_m"], bd, bd - p["within_biome_m"])
    if p["require_overland"] and not d.get("overland_from_spawn", True):
        bad("require_overland", 1, 0, 1)
    return out


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
    ap.add_argument(
        "--anchor-instance",
        type=int,
        help="index into the named anchor's instances; default is every instance",
    )
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

    req = {
        "biome": args.biome,
        "footprint_m": args.footprint,
        "max_flat_m": args.max_flat,
        "min_biome_purity": args.min_purity,
        "coastal": args.coastal,
        "coastal_within_m": args.coastal_within,
        "within_m": args.within,
        "location_clearance_m": args.clearance,
    }

    instances: list[tuple[str, tuple[float, float] | None]] = [("", None)]
    if args.near_xz:
        req["near_xz"] = list(args.near_xz)
        instances = [("fixed", (args.near_xz[0], args.near_xz[1]))]
    elif args.near:
        if args.near not in anchors:
            print(f"no anchor named {args.near!r}; use --list-anchors", file=sys.stderr)
            return 2
        req["near"] = args.near
        picked = anchors[args.near]
        if args.anchor_instance is not None:
            picked = [picked[args.anchor_instance]]
        instances = [
            (f"{args.near}#{i} ({l['x']:.0f},{l['z']:.0f})", (l["x"], l["z"]))
            for i, l in enumerate(picked)
        ]

    avoid_xz = None
    if anchors:
        pts = [(l["x"], l["z"]) for inst in anchors.values() for l in inst]
        avoid_xz = np.asarray(pts, dtype=np.float64)
        print(f"avoiding {len(pts)} placed locations by >= {args.clearance} m", file=sys.stderr)

    cands: list[dict] = []
    for label, axz in instances:
        for d in search(g, req, axz, avoid_xz, args.top):
            d["anchor_instance"] = label
            cands.append(d)
    cands.sort(key=lambda d: -d["score"])
    cands = cands[: args.top]
    if not cands:
        print("no site satisfies the requirement", file=sys.stderr)
        return 1

    print(
        f"{'x':>9} {'y':>7} {'z':>9} {'flat':>6} {'pure':>5} {'water':>7} "
        f"{'anchor':>8} {'loc':>7} {'score':>6}  instance"
    )
    for d in cands:
        print(
            f"{d['x']:9.1f} {d['y']:7.1f} {d['z']:9.1f} {d['flat']:6.2f} "
            f"{d['biome_purity']:5.2f} {d['water_dist']:7.1f} {d['anchor_dist']:8.0f} "
            f"{d['location_dist']:7.0f} {d['score']:6.3f}  {d.get('anchor_instance','')}"
        )
    if args.json:
        Path(args.json).write_text(json.dumps(cands, indent=1) + "\n", "utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
