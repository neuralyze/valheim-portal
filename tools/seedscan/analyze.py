#!/usr/bin/env python3
"""Score Valheim seeds from the biome grids produced by SeedScan.

Reads <dir>/*.biome (written by the SeedScan BepInEx plugin, which queries the
shipped WorldGenerator) and ranks seeds on Deep North accessibility.

Scoring criteria, in the priority order the jumpstart work needs:
  1. Deep North reachable by water from the sea around world spawn, landing on a
     coast rather than sitting in a landlocked pocket.
  2. Short distance from world spawn (0,0) to that landing.
  3. A meaningful Deep North landmass, not a sliver.
  4. Bonus: Mistlands and Ashlands also water-reachable and not absurdly far.

What this does NOT prove - see README.md.

Usage:
  tools/seedscan/analyze.py /tmp/seedscan/out [--top N] [--map SEED] [--csv out.csv]
"""

import argparse
import csv
import glob
import os
import struct
import sys

import numpy as np
from scipy.ndimage import label as ndi_label

CROSS4 = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=bool)

MAGIC = b"VHBIOME4"

NONE, MEADOWS, SWAMP, MOUNTAIN, BLACKFOREST, PLAINS, ASHLANDS, DEEPNORTH, OCEAN, MISTLANDS = range(10)

BIOME_NAME = {
    NONE: "None", MEADOWS: "Meadows", SWAMP: "Swamp", MOUNTAIN: "Mountain",
    BLACKFOREST: "BlackForest", PLAINS: "Plains", ASHLANDS: "AshLands",
    DEEPNORTH: "DeepNorth", OCEAN: "Ocean", MISTLANDS: "Mistlands",
}
BIOME_CHAR = {
    NONE: " ", MEADOWS: ".", SWAMP: "s", MOUNTAIN: "^", BLACKFOREST: "f",
    PLAINS: "p", ASHLANDS: "A", DEEPNORTH: "N", OCEAN: "~", MISTLANDS: "m",
}

NEIGH8 = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def load(path):
    with open(path, "rb") as fh:
        blob = fh.read()
    if blob[:8] != MAGIC:
        raise ValueError("%s: bad magic %r" % (path, blob[:8]))
    step, extent, n, planes, namelen = struct.unpack_from("<iiiii", blob, 8)
    off = 28
    name = blob[off:off + namelen].decode("utf-8")
    off += namelen
    (seed_hash,) = struct.unpack_from("<i", blob, off)
    off += 4
    grid = np.frombuffer(blob, dtype=np.uint8, count=n * n, offset=off).reshape(n, n)
    off += n * n
    hgt = None
    if planes >= 2:
        hgt = np.frombuffer(blob, dtype="<f4", count=n * n, offset=off).reshape(n, n)
    return {"file": path, "seed": name, "hash": seed_hash, "step": step,
            "extent": extent, "n": n, "grid": grid, "height": hgt}


def coords(n, step, extent):
    """Cell-centre world coordinates for one axis."""
    return -extent + np.arange(n) * step + step * 0.5


def components(mask):
    """4-connected labelling. Returns (labels int32, sizes list indexed by label-1)."""
    labels, count = ndi_label(mask, structure=CROSS4)
    if count == 0:
        return labels.astype(np.int32), []
    sizes = np.bincount(labels.ravel(), minlength=count + 1)[1:]
    return labels.astype(np.int32), sizes.tolist()


def water_distance(ocean, start, step):
    """8-connected Dijkstra over ocean cells, edge cost = geometric length.

    Returns a float array of travel distance in world units, inf where unreachable.
    """
    import heapq
    n = ocean.shape[0]
    dist = np.full((n, n), np.inf, dtype=np.float64)
    sy, sx = start
    dist[sy, sx] = 0.0
    diag = step * 2 ** 0.5
    heap = [(0.0, sy, sx)]
    while heap:
        d, y, x = heapq.heappop(heap)
        if d > dist[y, x]:
            continue
        for dy, dx in NEIGH8:
            ny, nx = y + dy, x + dx
            if not (0 <= ny < n and 0 <= nx < n) or not ocean[ny, nx]:
                continue
            nd = d + (diag if dy and dx else step)
            if nd < dist[ny, nx]:
                dist[ny, nx] = nd
                heapq.heappush(heap, (nd, ny, nx))
    return dist


def measure(rec, want_sail=False, want_walk=False):
    g = rec["grid"]
    h = rec.get("height")
    n, step, extent = rec["n"], rec["step"], rec["extent"]
    ax = coords(n, step, extent)
    X, Y = np.meshgrid(ax, ax)            # X[iy,ix], Y[iy,ix]
    R = np.hypot(X, Y)
    cell_km2 = (step * step) / 1e6

    out = {"seed": rec["seed"], "hash": rec["hash"], "step": step,
           "has_height_plane": h is not None}

    # --- sailable water -------------------------------------------------------
    # Sea level is 0 in WorldGenerator.GetHeight units. Measured on seed
    # 8JiFcknsJd at 128 m spacing: Ocean-biome cells span -400.0 .. +4.0 with a
    # median of exactly 0.0, while the lowest Meadows cell is +6.1. So
    # "height <= 0" is water and Ocean-biome cells above 0 are beaches.
    #
    # Without the height plane the only available proxy is "biome == Ocean",
    # which is wrong in two directions: it counts beaches as water, and it
    # misses the entire southern sea because GetBiome tests IsAshlands before
    # its water test so that sea reports AshLands.
    sail_cells = (h <= 0.0) if h is not None else (g == OCEAN)

    # Inland lakes are sailable too but are not the sea. The sea is the largest
    # connected water body; spawn_coast_dist records how far spawn is from it.
    if not sail_cells.any():
        return None
    olabels, osizes = components(sail_cells)
    world_label = int(np.argmax(osizes)) + 1
    home_sea = olabels == world_label
    out["home_sea_km2"] = round(home_sea.sum() * cell_km2, 1)
    si = int(np.argmin(np.where(home_sea, R, np.inf)))
    o_start = (si // n, si % n)
    out["spawn_coast_dist"] = int(round(R[o_start]))
    wdist = water_distance(home_sea, o_start, step) if want_sail else None

    # 8-adjacency to the sea, precomputed once.
    pad = np.zeros((n + 2, n + 2), dtype=bool)
    pad[1:-1, 1:-1] = home_sea
    sea_adj = np.zeros((n, n), dtype=bool)
    for dy, dx in NEIGH8:
        sea_adj |= pad[1 + dy:n + 1 + dy, 1 + dx:n + 1 + dx]

    # --- the starting landmass ------------------------------------------------
    # Several community seeds are sold on "a gigantic starting continent" or
    # "the starting landmass extends far north toward the Deep North". Both are
    # measurable.
    #
    # BUT: dn_walkable comes out True in 29 of 30 curated seeds, so as a boolean
    # it is worthless. The reason is structural, not a sampling bug - Valheim's
    # world rim is near-continuous land (measured: 88-91% land coverage in the
    # radius band 9600-10000 m), and the Ashlands landmass wraps the south, so
    # essentially every continent is joined to every other somewhere along the
    # edge. What actually discriminates is dn_walk_path, the geodesic distance
    # over that land. Measured on 8JiFcknsJd: 24 338 m of walking for an 8 228 m
    # straight line, and the route crosses Ashlands. "Walkable" does not mean
    # "a sensible route".
    land = (h > 0.0) if h is not None else (g != OCEAN)
    llabels, lsizes = components(land)
    si_land = int(np.argmin(np.where(land, R, np.inf)))
    spawn_cell = (si_land // n, si_land % n)
    sl = llabels[spawn_cell]
    spawn_land = llabels == sl
    out["spawn_land_km2"] = round(spawn_land.sum() * cell_km2, 1)
    out["spawn_land_maxy"] = int(round(Y[spawn_land].max()))
    dn_land_mask = (g == DEEPNORTH)
    if h is not None:
        dn_land_mask &= h > 0.0
    walkable_dn = spawn_land & dn_land_mask
    out["dn_walkable"] = bool(walkable_dn.any())
    out["dn_walk_near"] = int(round(R[walkable_dn].min())) if walkable_dn.any() else None
    out["dn_walk_km2"] = round(walkable_dn.sum() * cell_km2, 1)
    if want_walk and walkable_dn.any():
        wd = water_distance(spawn_land, spawn_cell, step)   # same 8-way Dijkstra, over land
        finite = wd[walkable_dn]
        finite = finite[np.isfinite(finite)]
        out["dn_walk_path"] = int(round(finite.min())) if finite.size else None
    else:
        out["dn_walk_path"] = None

    for code, tag in ((DEEPNORTH, "dn"), (MISTLANDS, "ml"), (ASHLANDS, "as")):
        mask = g == code
        if h is not None:
            mask &= h > 0.0               # land only
        out[tag + "_km2"] = round(mask.sum() * cell_km2, 1)
        if not mask.any():
            for k in ("_near", "_reach_near", "_sail", "_land_x", "_land_y",
                      "_sail_x", "_sail_y", "_detour", "_reach_km2",
                      "_biggest_km2"):
                out[tag + k] = None
            out[tag + "_reachable"] = False
            continue

        out[tag + "_near"] = int(round(R[mask].min()))
        labels, sizes = components(mask)
        out[tag + "_biggest_km2"] = round(max(sizes) * cell_km2, 1)

        landings = mask & sea_adj
        if not landings.any():
            out[tag + "_reachable"] = False
            for k in ("_reach_near", "_sail", "_land_x", "_land_y",
                      "_sail_x", "_sail_y", "_detour"):
                out[tag + k] = None
            out[tag + "_reach_km2"] = 0.0
            continue

        out[tag + "_reachable"] = True

        # (a) The robust headline: nearest sea-connected beach by straight line.
        # Depends only on the connectivity boolean, so it barely moves with
        # sampling resolution.
        ni = int(np.argmin(np.where(landings, R, np.inf)))
        ny_, nx_ = ni // n, ni % n
        out[tag + "_reach_near"] = int(round(R[ny_, nx_]))
        out[tag + "_land_x"] = int(round(X[ny_, nx_]))
        out[tag + "_land_y"] = int(round(Y[ny_, nx_]))

        # (b) The grid estimate of the actual water route. Opt-in (--sail) and
        # reported, never ranked on: it swings by 2x between 128 m and 32 m
        # sampling because finer sampling resolves both new channels AND new
        # blocking isthmuses. Order of magnitude only.
        if wdist is None:
            out[tag + "_sail"] = out[tag + "_sail_x"] = out[tag + "_sail_y"] = None
            out[tag + "_detour"] = None
        else:
            # Cheapest adjacent sea cell per landing, via a padded 8-way min.
            wpad = np.full((n + 2, n + 2), np.inf)
            wpad[1:-1, 1:-1] = np.where(home_sea, wdist, np.inf)
            land_cost = np.full((n, n), np.inf)
            for dy, dx in NEIGH8:
                land_cost = np.minimum(land_cost, wpad[1 + dy:n + 1 + dy, 1 + dx:n + 1 + dx])
            land_cost = np.where(landings, land_cost, np.inf)
            bi = int(np.argmin(land_cost))
            by, bx = bi // n, bi % n
            if np.isfinite(land_cost[by, bx]):
                out[tag + "_sail"] = int(round(land_cost[by, bx]))
                out[tag + "_sail_x"] = int(round(X[by, bx]))
                out[tag + "_sail_y"] = int(round(Y[by, bx]))
                out[tag + "_detour"] = round(land_cost[by, bx] / max(1.0, R[by, bx]), 2)
            else:
                out[tag + "_sail"] = out[tag + "_sail_x"] = out[tag + "_sail_y"] = None
                out[tag + "_detour"] = None

        # Total area of landmasses that touch the sea (excludes inland pockets).
        reach_labels = set(int(v) for v in np.unique(labels[landings]))
        out[tag + "_reach_km2"] = round(
            sum(sizes[l - 1] for l in reach_labels if l > 0) * cell_km2, 1)

    # --- early-tier texture near spawn (bonus) --------------------------------
    near = R <= 2000
    for code, tag in ((BLACKFOREST, "bf"), (SWAMP, "sw"), (MOUNTAIN, "mt"), (PLAINS, "pl")):
        out["near2k_" + tag] = int((near & (g == code)).sum())
    return out


def trip(m):
    """Spawn -> Deep North cost in metres, resolution-robust form.

    spawn_coast_dist  straight-line gap from (0,0) to the nearest cell of the
                      world sea: how far overland before a boat is any use.
    dn_reach_near     straight-line distance from (0,0) to the nearest Deep
                      North beach that is on THAT SAME sea.

    Both are straight lines, so they depend on the water-connectivity boolean
    but not on grid path length. The grid-path estimate (dn_sail) is reported
    separately; it is not summed here because it swings by 2x with sampling
    resolution.
    """
    if not m["dn_reachable"] or m["dn_reach_near"] is None:
        return None
    return m["spawn_coast_dist"] + m["dn_reach_near"]


def score(m):
    """Rank key: lower is better. Hard gate on Deep North water reachability.

    Calibrated against the measured distribution over 1001 random seeds:
      dn_near         7994 .. 8092  (median 7994) - NOT a differentiator.
                      Deep North's inner edge is fixed geometry, always ~8 km
                      due north of spawn, because IsDeepnorth is seed-
                      independent: magnitude(x, y+4000) > 12000 + 100*sin(20*
                      atan2(x,y)). Minimising that radius gives exactly 8000 m
                      due north. No seed can put Deep North closer than that.
      dn_reach_km2      20 .. 28    (median 24) - barely varies either, so
                      "meaningful landmass" is satisfied by essentially every
                      seed and is not a differentiator.
      dn_biggest_km2     2 .. 13    (median 5)  - the real "sliver" test.
      spawn_coast_dist  91 .. 6629  (median 1177) - a real differentiator.

    So: rank on trip (overland + straight line to a connected Deep North
    beach), gate on a non-sliver landmass, and fold the bonus biomes in at a
    fraction of their weight since they are explicitly secondary.
    """
    t = trip(m)
    if t is None:
        return (2, 0.0)
    biggest = m["dn_biggest_km2"] or 0.0
    penalty = max(0.0, 6.0 - biggest) * 1500      # 6 km2 ~ p60 of largest landmass
    bonus = 0.20 * (m["ml_reach_near"] if m["ml_reach_near"] is not None else 12000)
    bonus += 0.10 * (m["as_reach_near"] if m["as_reach_near"] is not None else 12000)
    return (0, t + penalty + bonus)


def ascii_map(rec, width=100):
    g = rec["grid"]
    n = rec["n"]
    stride = max(1, n // width)
    rows = []
    for iy in range(n - 1, -1, -stride):          # north at top
        rows.append("".join(BIOME_CHAR.get(int(g[iy, ix]), "?")
                            for ix in range(0, n, stride)))
    return "\n".join(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--map", action="append", default=[],
                    help="print an ASCII biome map for this seed")
    ap.add_argument("--csv")
    ap.add_argument("--only", action="append", default=[],
                    help="restrict output to these seeds (still ranked globally)")
    ap.add_argument("--walk", action="store_true",
                    help="also compute the geodesic land-walk distance from "
                         "spawn to Deep North land (slow)")
    ap.add_argument("--sail", action="store_true",
                    help="also compute the grid water-route estimate (slow; "
                         "resolution-sensitive, never used for ranking)")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.dir, "*.biome")))
    if not files:
        sys.exit("no .biome files in %s" % args.dir)

    rows = []
    by_seed = {}
    for f in files:
        rec = load(f)
        m = measure(rec, want_sail=args.sail, want_walk=args.walk)
        if m:
            rows.append(m)
        if rec["seed"] in args.map:
            by_seed[rec["seed"]] = rec

    rows.sort(key=score)
    for i, m in enumerate(rows, 1):
        m["rank"] = i

    if args.csv:
        with open(args.csv, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print("wrote %s (%d seeds)" % (args.csv, len(rows)))

    hdr = ("%-4s %-12s %-4s %7s %6s %8s %7s %6s %6s %6s  %-14s %s" %
           ("rank", "seed", "wtr", "TRIP", "toSea", "dnLanding", "sail~",
            "dnBig", "dnRch", "dnTot", "landing(x,y)", "ml/as line"))
    print("scanned %d seeds; step=%d m; height plane=%s"
          % (len(rows), rows[0]["step"], rows[0]["has_height_plane"]))
    print("TRIP = toSea (overland, spawn to the world sea) + dnLanding "
          "(straight line to the nearest Deep North beach on that same sea), m.")
    print("sail~ = grid estimate of the water route; resolution-sensitive, "
          "indicative only. Areas km2. ml/as = straight line to a connected "
          "Mistlands / Ashlands beach.")
    print(hdr)
    print("-" * len(hdr))
    show = rows if args.only else rows[:args.top]
    for m in show:
        if args.only and m["seed"] not in args.only:
            continue
        print("%-4d %-12s %-4s %7s %6s %8s %7s %6s %6s %6s  %-14s %s" % (
            m["rank"], m["seed"], "yes" if m["dn_reachable"] else "NO",
            trip(m), m["spawn_coast_dist"], m["dn_reach_near"], m["dn_sail"],
            m["dn_biggest_km2"], m["dn_reach_km2"], m["dn_km2"],
            "(%s,%s)" % (m["dn_land_x"], m["dn_land_y"]),
            ("ml=%s/%s as=%s/%s" % (
                "y" if m["ml_reachable"] else "n", m["ml_reach_near"],
                "y" if m["as_reachable"] else "n", m["as_reach_near"])),
        ))

    for seed in args.map:
        if seed not in by_seed:
            print("\nno grid for %s" % seed)
            continue
        print("\n=== %s (north at top; ~%s m per character column) ===" %
              (seed, by_seed[seed]["step"] * max(1, by_seed[seed]["n"] // 100)))
        print("legend: . meadows  f blackforest  s swamp  ^ mountain  p plains"
              "  m mistlands  A ashlands  N deepnorth  ~ ocean")
        print(ascii_map(by_seed[seed]))


if __name__ == "__main__":
    main()
