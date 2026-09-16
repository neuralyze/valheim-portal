#!/usr/bin/env python3
"""Survey the water: landmasses, straits, sheltered bays, ferry pairs.

Everything here is MEASURED off a 1 m `VHPATCH1` height+biome field produced by
`tools/jumpstart/blueprints/run_patchscan.sh`, which runs
`WorldGenerator.Initialize` with full pregeneration and therefore HAS RIVERS.
Nothing here reads the 8 m `.biome` overview grid, because that grid is written
with `SEEDSCAN_PREGEN=0` and is river-free: a dry-looking valley on it can hold
a 60-100 m river, and a crossing verdict taken from it is worthless.

Subcommands
  landmasses   connected land components, areas, bounding boxes
  straits      narrowest water gaps between two named components
  bays         score shoreline samples for a harbour: shelter, water area,
               depth, and approach
  ferry        pairs of shorelines on the same water body whose water crossing
               is short but which no bridge can span
  profile      measure one straight line: span, bank heights, depth, bed

Usage:
  survey.py landmasses --field /tmp/roads/h1m.bin --patch mainland
  survey.py straits    --field ... --patch mainland --a 7 --b 195
  survey.py bays       --field ... --patch mainland --mask 7 --top 12
  survey.py profile    --field ... --patch mainland --from -312,-64 --to -324,-64
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).resolve().parent))
import water as W  # noqa: E402

BIOME_NAME = {0: "None", 1: "Meadows", 2: "Swamp", 3: "Mountain", 4: "BlackForest",
              5: "Plains", 6: "AshLands", 7: "DeepNorth", 8: "Ocean", 9: "Mistlands"}

# 24 rays at 15 deg. Used to measure SHELTER: the fraction of rays from a
# candidate mooring that terminate on land. A harbour wants a high number
# (enclosed), an open jetty a low one.
RAYS = [(math.sin(math.radians(a)), math.cos(math.radians(a))) for a in range(0, 360, 15)]

# Minimum water depth at a mooring. MEASURED from the ship prefabs
# (tools/jumpstart/crossings/run_piecematerial.sh output): Ship.m_waterLevelOffset
# is 1.2 m on Karve, 1.5 m on Raft and VikingShip (= Longship), 1.7 m on
# Trailership and 2.1 m on VikingShip_Ashlands. That offset is how far the hull's
# float collider centre sits below the water plane, so 2.5 m of water is the
# shallowest berth a Longship can sit in without grounding on the bed.
MOOR_DEPTH_M = 2.5


def load_field(path: str, patch: str) -> W.Field:
    fields = W.load(path, only={patch})
    if patch not in fields:
        raise SystemExit(f"patch {patch!r} not in {path}; have "
                         f"{sorted(W.load(path, only=set()).keys())}")
    return fields[patch]


def label_land(fld: W.Field):
    lab, count = ndimage.label(fld.land())
    sizes = np.bincount(lab.ravel())
    return lab, sizes


def cmd_landmasses(fld: W.Field, args) -> None:
    lab, sizes = label_land(fld)
    order = np.argsort(sizes[1:])[::-1][:args.top]
    rows = []
    for o in order:
        cid = int(o) + 1
        m = lab == cid
        ii, jj = np.nonzero(m)
        bio = fld.biomes[m]
        counts = np.bincount(bio, minlength=10)
        mix = ", ".join(f"{BIOME_NAME.get(b, b)} {counts[b] * 100 // max(counts.sum(), 1)}%"
                        for b in np.argsort(counts)[::-1][:3] if counts[b])
        rows.append(dict(
            id=cid, cells=int(sizes[cid]), km2=round(float(sizes[cid]) / 1e6, 3),
            x=[float(fld.world_x(jj.min())), float(fld.world_x(jj.max()))],
            z=[float(fld.world_z(ii.min())), float(fld.world_z(ii.max()))],
            biomes=mix))
    print(json.dumps(rows, indent=1))


def gap_between(fld: W.Field, ma: np.ndarray, mb: np.ndarray, top: int, sep_m: float):
    dt, ix = ndimage.distance_transform_edt(~ma, return_indices=True)
    d = np.where(mb, dt, np.inf)
    cand = np.argwhere(np.isfinite(d) & (d < 2000))
    if not len(cand):
        return []
    vals = d[cand[:, 0], cand[:, 1]]
    out = []
    for t in np.argsort(vals):
        i, j = cand[t]
        if any((i - pi) ** 2 + (j - pj) ** 2 < sep_m ** 2 for pi, pj, _, _, _ in out):
            continue
        ai, aj = int(ix[0][i, j]), int(ix[1][i, j])
        out.append((int(i), int(j), float(vals[t]), ai, aj))
        if len(out) >= top:
            break
    return out


def cmd_straits(fld: W.Field, args) -> None:
    lab, _ = label_land(fld)
    ma, mb = lab == args.a, lab == args.b
    rows = []
    for i, j, g, ai, aj in gap_between(fld, ma, mb, args.top, args.sep):
        bx, bz = float(fld.world_x(j)), float(fld.world_z(i))
        ax, az = float(fld.world_x(aj)), float(fld.world_z(ai))
        p = measure(fld, ax, az, bx, bz, args.extend)
        rows.append(as_row(p, extra=dict(gap_m=round(g, 1))))
    print(json.dumps(rows, indent=1))


def measure(fld: W.Field, ax: float, az: float, bx: float, bz: float,
            extend: float = 12.0) -> W.Profile:
    """Profile A->B with the line extended past both ends, so the reported bank
    sample is genuinely dry land and not the shoreline sample itself."""
    length = math.hypot(bx - ax, bz - az)
    ux, uz = (bx - ax) / length, (bz - az) / length
    # Shrink the extension rather than throwing when a bank sits at the edge of
    # the patch. An honest 2 m extension beats a ValueError, and the caller sees
    # the achieved value in the row.
    e = extend
    while e > 0 and not (fld.contains(ax - ux * e, az - uz * e, 1.0)
                         and fld.contains(bx + ux * e, bz + uz * e, 1.0)):
        e -= 1.0
    p = W.profile(fld, ax - ux * e, az - uz * e, bx + ux * e, bz + uz * e)
    p.extend_m = e
    return p


def as_row(p: W.Profile, extra: dict | None = None) -> dict:
    row = dict(
        bank_a=[round(p.bank_a_xz[0], 1), round(p.bank_a_xz[1], 1)],
        bank_a_y=round(p.bank_a_y, 2),
        bank_b=[round(p.bank_b_xz[0], 1), round(p.bank_b_xz[1], 1)],
        bank_b_y=round(p.bank_b_y, 2),
        bearing_deg=round(p.bearing_deg, 2),
        span_m=round(p.span_m, 1),
        wet_runs=p.wet_runs,
        max_depth_m=round(p.max_depth_m, 2),
        bed_min_y=round(p.bed_min_y, 2),
        deck_y=round(p.deck_y_min, 2),
        extend_m=getattr(p, "extend_m", None),
    )
    row.update(extra or {})
    return row


def cmd_profile(fld: W.Field, args) -> None:
    ax, az = [float(v) for v in args.frm.split(",")]
    bx, bz = [float(v) for v in args.to.split(",")]
    p = measure(fld, ax, az, bx, bz, args.extend)
    print(json.dumps(as_row(p), indent=1))


def disc_mask(r: int):
    yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
    return (yy * yy + xx * xx) <= r * r


def cmd_bays(fld: W.Field, args) -> None:
    """Score every shoreline sample of one landmass as a harbour site.

    A harbour needs four things and each is measured separately rather than
    rolled into one opaque score:
      shelter      fraction of 24 rays at `shelter_r` m that end on land. High
                   = an enclosed bay, which is what a harbour is.
      water_frac   fraction of a `basin_r` m disc offshore that is wet. A
                   sheltered point with no water in front of it is a ditch.
      berth_depth  deepest water within `basin_r` m. Must clear MOOR_DEPTH_M or
                   a Longship grounds.
      bank_y       the land height at the sample, which is the height the dock
                   deck has to reconcile with. A 12 m cliff is not a dock site.
    """
    lab, _ = label_land(fld)
    mask = lab == args.mask
    shore = W.shoreline(fld, mask)
    dep = fld.depth()
    wet = fld.wet()
    br = int(args.basin_r)
    dm = disc_mask(br)
    ii, jj = np.nonzero(shore)
    # Subsample the shoreline: adjacent shore samples give near-identical
    # answers and there are thousands of them.
    keep = slice(None, None, max(1, args.stride))
    ii, jj = ii[keep], jj[keep]
    rows = []
    for i, j in zip(ii, jj):
        if i - br < 0 or j - br < 0 or i + br + 1 > fld.n or j + br + 1 > fld.n:
            continue
        x, z = float(fld.world_x(j)), float(fld.world_z(i))
        bank_y = float(fld.heights[i, j])
        if bank_y > args.max_bank_y:
            continue
        sub_w = wet[i - br:i + br + 1, j - br:j + br + 1]
        wfrac = float((sub_w & dm).sum()) / dm.sum()
        if wfrac < args.min_water:
            continue
        sub_d = dep[i - br:i + br + 1, j - br:j + br + 1]
        berth = float((sub_d * dm).max())
        if berth < MOOR_DEPTH_M:
            continue
        hit = tot = 0
        for ux, uz in RAYS:
            xx, zz = x + ux * args.shelter_r, z + uz * args.shelter_r
            if not fld.contains(xx, zz, 2.0):
                continue
            si, sj = fld.index(xx, zz)
            tot += 1
            if fld.land()[si, sj]:
                hit += 1
        shelter = hit / max(tot, 1)
        rows.append(dict(x=x, z=z, bank_y=round(bank_y, 2),
                         shelter=round(shelter, 3),
                         water_frac=round(wfrac, 3),
                         berth_depth_m=round(berth, 2),
                         biome=BIOME_NAME.get(int(fld.biomes[i, j]), "?")))
    # Rank by shelter first, then by berth depth. Then thin so two picks are
    # never within `sep` metres of each other.
    rows.sort(key=lambda r: (-r["shelter"], -r["berth_depth_m"]))
    picked = []
    for r in rows:
        if any(math.hypot(r["x"] - q["x"], r["z"] - q["z"]) < args.sep for q in picked):
            continue
        picked.append(r)
        if len(picked) >= args.top:
            break
    print(json.dumps(picked, indent=1))


def cmd_ferry(fld: W.Field, args) -> None:
    """Find crossings that a bridge CANNOT do, which is what makes a ferry real.

    A ferry is only justified where a bridge is impossible, because a bridge the
    operator can walk is strictly better than a boat they have to sail. So the
    filter is: same water body, gap wider than `bridge_max` (my measured
    iron-stringer ceiling) or deeper than `depth_max` (my measured pile reach),
    and a short water line between two shores that are far apart by land.
    """
    lab, sizes = label_land(fld)
    ids = [int(c) for c in np.argsort(sizes[1:])[::-1][:args.consider] + 1]
    ids = [c for c in ids if sizes[c] >= args.min_cells]
    rows = []
    for n, a in enumerate(ids):
        ma = lab == a
        dt, ix = ndimage.distance_transform_edt(~ma, return_indices=True)
        for b in ids[n + 1:]:
            mb = lab == b
            d = np.where(mb, dt, np.inf)
            k = int(np.argmin(d))
            bi, bj = np.unravel_index(k, d.shape)
            gap = float(d[bi, bj])
            if not math.isfinite(gap):
                continue
            ai, aj = int(ix[0][bi, bj]), int(ix[1][bi, bj])
            axw, azw = float(fld.world_x(aj)), float(fld.world_z(ai))
            bxw, bzw = float(fld.world_x(bj)), float(fld.world_z(bi))
            # A closest-approach pair that lands ON the patch boundary is an
            # artefact of where the patch was cut, not a real crossing: the
            # landmass continues outside the rectangle and its true nearest
            # point is unmeasured. Report it as such instead of sizing a ferry
            # to it.
            if not (fld.contains(axw, azw, 2.0) and fld.contains(bxw, bzw, 2.0)):
                rows.append(dict(a=a, b=b, gap_m=round(gap, 1),
                                 verdict="unmeasurable_at_patch_edge",
                                 bank_a=[round(axw, 1), round(azw, 1)],
                                 bank_b=[round(bxw, 1), round(bzw, 1)],
                                 span_m=float("inf")))
                continue
            p = measure(fld, axw, azw, bxw, bzw, args.extend)
            verdict = ("bridge" if (p.span_m <= args.bridge_max
                                    and p.max_depth_m <= args.depth_max)
                       else "ferry")
            rows.append(as_row(p, extra=dict(a=a, b=b, gap_m=round(gap, 1),
                                             verdict=verdict)))
    rows.sort(key=lambda r: r["span_m"])
    print(json.dumps(rows, indent=1))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["landmasses", "straits", "bays", "ferry", "profile"])
    ap.add_argument("--field", default="/tmp/roads/h1m.bin")
    ap.add_argument("--patch", default="mainland")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--sep", type=float, default=200.0)
    ap.add_argument("--extend", type=float, default=12.0)
    ap.add_argument("--a", type=int)
    ap.add_argument("--b", type=int)
    ap.add_argument("--mask", type=int, default=1)
    ap.add_argument("--stride", type=int, default=4)
    ap.add_argument("--basin-r", type=float, default=35.0)
    ap.add_argument("--shelter-r", type=float, default=70.0)
    ap.add_argument("--min-water", type=float, default=0.25)
    ap.add_argument("--max-bank-y", type=float, default=34.0)
    ap.add_argument("--consider", type=int, default=8)
    ap.add_argument("--min-cells", type=int, default=20000)
    ap.add_argument("--bridge-max", type=float, default=96.0)
    ap.add_argument("--depth-max", type=float, default=45.0)
    ap.add_argument("--frm", "--from", dest="frm")
    ap.add_argument("--to", dest="to")
    args = ap.parse_args(argv)
    fld = load_field(args.field, args.patch)
    {"landmasses": cmd_landmasses, "straits": cmd_straits, "bays": cmd_bays,
     "ferry": cmd_ferry, "profile": cmd_profile}[args.cmd](fld, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
