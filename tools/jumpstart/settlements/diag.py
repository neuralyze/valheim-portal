#!/usr/bin/env python3
"""Why did a site search return nothing? Count survivors per constraint.

A search that returns zero is not information: it could be one impossible
threshold or five reasonable ones stacking. This prints the funnel so the
binding constraint is visible, at 1 m, per patch.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import clearance  # noqa: E402
import sites as S  # noqa: E402
import terrain1m as T  # noqa: E402


def funnel(name: str, masks: list[tuple[str, np.ndarray]]) -> None:
    print(f"\n== {name}")
    acc = None
    for label, m in masks:
        acc = m if acc is None else (acc & m)
        print(f"   {label:34s} alone {int(m.sum()):7d}   cumulative {int(acc.sum()):7d}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--patches", default="/tmp/settle/h1m.bin")
    ap.add_argument("--locations", default="/tmp/settle/loc3/f6fe167f4fcd.json")
    ap.add_argument("--district", type=float, default=176.0)
    ap.add_argument("--purity", type=float, default=0.92)
    ap.add_argument("--relief", type=float, default=12.0)
    a = ap.parse_args()

    p = S.Picker(a.patches, a.locations)
    mead = T.BIOME_CODE["Meadows"]
    for pid in ("mainland", "westisle"):
        blk = p.blocks[pid]
        rel = S.win_relief(blk, a.district)
        pure = S.win_biome(blk, mead, a.district)
        wet = S.win_wet(blk, a.district)
        coast = S.win_wet(blk, 2.2 * a.district)
        funnel(f"{pid} town district {a.district} m", [
            (f"meadows fraction >= {a.purity}", pure >= a.purity),
            (f"relief <= {a.relief} m", rel <= a.relief),
            ("no wet block in district", wet <= 0.0),
            ("coast within ~2.2x district", coast > 0.02),
            ("dry (block min > 31 m)", blk.hmin > T.WATER_LEVEL_M + 1.0),
        ])
        print(f"   best meadows fraction {float(pure.max()):.3f}; "
              f"relief at the purest cell "
              f"{float(rel[np.unravel_index(int(pure.argmax()), pure.shape)]):.1f} m")
        # How many survivors of the terrain-only filters fail CLEARANCE?
        ok = (pure >= a.purity) & (rel <= a.relief) & (wet <= 0.0) & (coast > 0.02)
        ys, xs = np.nonzero(ok)
        hd = a.district * math.sqrt(2) / 2
        tested = cleared = 0
        for iz, ix in list(zip(ys, xs))[:4000]:
            x, z = blk.world(int(iz), int(ix))
            tested += 1
            if p.loc.clear(x, z, hd):
                cleared += 1
        print(f"   clearance: {cleared}/{tested} terrain-passing cells clear the "
              f"per-type stand-off with half-diagonal {hd:.0f} m")
        if tested and not cleared:
            worst = None
            for iz, ix in list(zip(ys, xs))[:200]:
                x, z = blk.world(int(iz), int(ix))
                v = p.loc.violations(x, z, hd)
                if v and (worst is None or v[0]["short_by_m"] < worst[0]["short_by_m"]):
                    worst = v
            print(f"   closest miss: {worst[:3] if worst else 'n/a'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
