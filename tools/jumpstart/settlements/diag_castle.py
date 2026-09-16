#!/usr/bin/env python3
"""Why is there no mountaintop castle on the mountain isle?

A castle pad is the one case where the +/-8 m clamp is expected to bind, so the
funnel has to separate "no mountain here" from "the summit is too steep to
level" from "a location is in the way". Prints all three, per patch, at 1 m.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import sites as S  # noqa: E402
import terrain1m as T  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--patches", default="/tmp/settle/h1m.bin")
    ap.add_argument("--locations", default="/tmp/settle/loc3/f6fe167f4fcd.json")
    ap.add_argument("--patch", default="mtn7114")
    ap.add_argument("--pad", type=float, default=44.0)
    ap.add_argument("--min-h", type=float, default=120.0)
    ap.add_argument("--max-relief", type=float, default=20.0)
    ap.add_argument("--mfrac", type=float, default=0.6)
    a = ap.parse_args()

    p = S.Picker(a.patches, a.locations)
    blk = p.blocks[a.patch]
    f = p.fields[a.patch]
    mtn = T.BIOME_CODE["Mountain"]
    rel = S.win_relief(blk, a.pad + 8)
    mfrac = S.win_biome(blk, mtn, a.pad + 8)
    print(f"{a.patch}: blocks {blk.hmean.shape}, h [{f.h.min():.1f}, {f.h.max():.1f}], "
          f"mountain 1 m fraction {float((f.biome == mtn).mean()):.1%}")
    print(f"  blocks above {a.min_h} m: {int((blk.hmean >= a.min_h).sum())}")
    high = blk.hmean >= a.min_h
    if high.any():
        print(f"  of those, mountain fraction >= {a.mfrac}: "
              f"{int((high & (mfrac >= a.mfrac)).sum())}")
        print(f"  of those, relief <= {a.max_relief} m: "
              f"{int((high & (mfrac >= a.mfrac) & (rel <= a.max_relief)).sum())}")
        r = rel[high]
        print(f"  relief over the high blocks: min {float(r.min()):.1f} "
              f"median {float(np.median(r)):.1f} max {float(r.max()):.1f} m")
    # The honest question: what is the FLATTEST pad of this size anywhere high on
    # this patch, and what does it cost? Answer it by pricing the best blocks.
    cand = np.argsort(-(blk.hmean - rel * 5).ravel())[:40]
    print("\n  best-priced summit pads (1 m, pad slid +/-16 m):")
    shown = 0
    for t in cand:
        iz, ix = np.unravel_index(int(t), blk.hmean.shape)
        if blk.hmean[iz, ix] < 60:
            continue
        x, z = blk.world(int(iz), int(ix))
        try:
            bill = S.best_pad(f, x, z, a.pad, a.pad, search_m=16.0)
        except ValueError:
            continue
        hd = math.hypot(a.pad, a.pad) / 2
        v = p.loc.violations(bill["x"], bill["z"], hd)
        print(f"   ({bill['x']:7.1f}, {bill['z']:7.1f}) y={bill['target_y']:7.2f} "
              f"moved={bill['moved_m3']:8.0f} cut={bill['max_cut_m']:6.2f} "
              f"fill={bill['max_fill_m']:6.2f} clamp={'OK ' if bill['clamp_ok'] else 'FAIL'} "
              f"head={bill['clamp_headroom_m']:6.2f} mtn="
              f"{bill['biome_counts'].get('Mountain', 0) / max(bill['samples'], 1):.2f} "
              f"poi_violations={len(v)}"
              + (f" worst={v[0]['prefab']}@{v[0]['dist_m']}" if v else ""))
        shown += 1
        if shown >= 12:
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
