#!/usr/bin/env python3
"""Keep-out geometry for vanilla world locations, for road routing.

SOURCE, MEASURED by `Settlements` and read-only here:
`/tmp/settle/loc2/f6fe167f4fcd.json` -- seed Pirate68, hash 147627509, 12,301
location instances, 177 types, each carrying the `ZoneLocation`'s own
`exteriorRadius`, `interiorRadius`, `clearArea`, `quantity`, `prioritized`,
`centerFirst`.  Largest declared exteriorRadius anywhere in the world is 32 m;
smallest are 3-6 m.  That order-of-magnitude spread is why a single global
stand-off number cleared zero cells out of 12,301.

RADIUS BUDGET, with each term's status stated because they are not equal:

    declared   = max(exteriorRadius, interiorRadius)      MEASURED, per instance
    overshoot  = 11 m                                     INFERRED from ONE
                 observation -- the old world's POI whose PIECES reached 43 m
                 against a 32 m declared radius.  Generalising one measurement
                 into a world-wide constant is exactly the defect shape this
                 project has paid for repeatedly, so it is marked INFERRED here
                 and in every record this module produces.  It is also
                 dimensionally suspicious on a 3 m Waymarker, where 11 m is
                 nearly four times the declared radius.
    road_margin = 6 m                                     TASTE PICK.  A road is
                 a 6 m ribbon passing by, not a 60 m pad sitting on top; it
                 needs less breathing room than a building site.
    half_width = the ribbon's own half-width                MEASURED from design

PROTECTED TEST, from `Settlements`, accepted: `prioritized or centerFirst or
quantity <= 5`.  There is no sacredness flag on `ZoneLocation` -- every field
was checked -- and `unique` is FALSE even on `bosslocation`, so `unique` is not
the signal and must not be used.  MEASURED examples: StartTemple quantity 1
prioritized centerFirst; bosslocation quantity 3 prioritized; against
InfestedTree01 quantity 700 and Mistlands_RoadPost1 quantity 500.

TWO TIERS, because a road is not a building:
  * PROTECTED instances are a HARD FORBID out to the full budget.  The road
    bends.
  * SCENERY is a hard forbid only out to `declared + piece_pad` (so the ribbon
    never levels ground out from under a standing piece, which is the floating-
    vegetation defect with a rock instead of a bush) and a SOFT penalty out to
    the full budget.  A road passing a waymarker is thematic; a road that
    undermines one is a defect.

RESIDUAL RISK, stated rather than hidden: this dump is MOD-FREE by
construction, so it cannot see More_World_Locations' ~190 POI types.  A ribbon
routed clear of every vanilla instance can still cross a mod POI.  The ribbon
DELETES nothing, so the failure mode is a mod POI's pieces standing over
levelled ground, not lost content -- but it is still a defect, and the only
trustworthy check is a live marker sweep along the finished centreline after the
zones are generated.  `network.py --marker-check` emits that sweep.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

DUMP = Path("/tmp/settle/loc2/f6fe167f4fcd.json")

OVERSHOOT_M = 11.0        # INFERRED, one observation
ROAD_MARGIN_M = 6.0       # taste pick
PIECE_PAD_M = 3.0         # taste pick: never level right up against a piece


@dataclass
class Poi:
    name: str
    x: float
    z: float
    y: float
    declared_m: float
    protected: bool
    quantity: int

    def keepout_m(self, half_width_m: float) -> float:
        return self.declared_m + OVERSHOOT_M + ROAD_MARGIN_M + half_width_m

    def hard_m(self, half_width_m: float) -> float:
        """The radius the road may not enter at all."""
        if self.protected:
            return self.keepout_m(half_width_m)
        return self.declared_m + PIECE_PAD_M + half_width_m


def load(path: Path = DUMP) -> list[Poi]:
    d = json.loads(Path(path).read_text())
    out = []
    for r in d["locations"]:
        declared = float(max(r.get("exteriorRadius") or 0, r.get("interiorRadius") or 0))
        q = int(r.get("quantity") or 0)
        prot = bool(r.get("prioritized")) or bool(r.get("centerFirst")) or (0 < q <= 5)
        out.append(Poi(r["name"], float(r["x"]), float(r["z"]), float(r.get("y") or 0.0),
                       declared, prot, q))
    return out


def masks(pois: list[Poi], x0: float, z0: float, cell_m: float, m: int,
          half_width_m: float, chord_slack_m: float | None = None,
          ) -> tuple[np.ndarray, np.ndarray, list[dict]]:
    """Rasterise hard-forbid and soft-penalty circles onto a corridor grid.

    `chord_slack_m` GROWS the hard radius, and it is not padding for its own
    sake.  A* returns cell CENTRES on a `cell_m` grid and the polyline is then
    simplified with a 1 m tolerance, so the finished centreline can sit up to
    `cell_m/sqrt(2) + tol` inside a cell whose centre was outside the circle.
    MEASURED consequence of not doing this: on the first planned trunk the
    centreline came within 7.6 m of a WoodHouse7 whose hard radius is 14.0 m --
    the mask was correct and the line still breached it.  Growing the mask by
    the geometric slack fixes the cause; a post-check on the finished centreline
    (`near_centreline`) then PROVES it rather than assuming it.  Default slack
    is that bound, not a guess.

    Returns (forbid, soft, touched) where `soft` is an additive cost
    contribution in [0, 1] decaying linearly from the hard edge to the full
    keep-out radius, and `touched` lists every instance whose keep-out overlaps
    the grid so a route's rationale can name what it bent around.
    """
    if chord_slack_m is None:
        chord_slack_m = cell_m / math.sqrt(2.0) + 1.0
    forbid = np.zeros((m, m), dtype=bool)
    soft = np.zeros((m, m), dtype=np.float32)
    touched: list[dict] = []
    xlo, xhi = x0 - cell_m, x0 + (m - 1) * cell_m + cell_m
    zlo, zhi = z0 - cell_m, z0 + (m - 1) * cell_m + cell_m
    for p in pois:
        ko = p.keepout_m(half_width_m)
        if not (xlo - ko <= p.x <= xhi + ko and zlo - ko <= p.z <= zhi + ko):
            continue
        hard = p.hard_m(half_width_m) + chord_slack_m
        ko = max(ko, hard)
        jc = (p.x - x0) / cell_m
        ic = (p.z - z0) / cell_m
        r = int(math.ceil(ko / cell_m))
        j0, j1 = max(0, int(jc) - r), min(m - 1, int(jc) + r + 1)
        i0, i1 = max(0, int(ic) - r), min(m - 1, int(ic) + r + 1)
        if j0 > j1 or i0 > i1:
            continue
        jj = np.arange(j0, j1 + 1)
        ii = np.arange(i0, i1 + 1)
        dx = (jj - jc) * cell_m
        dz = (ii - ic) * cell_m
        d = np.hypot(dx[None, :], dz[:, None])
        sub_f = d <= hard
        if sub_f.any():
            forbid[i0:i1 + 1, j0:j1 + 1] |= sub_f
        band = (d > hard) & (d <= ko)
        if band.any():
            w = np.clip((ko - d) / max(ko - hard, 1e-6), 0.0, 1.0).astype(np.float32)
            blk = soft[i0:i1 + 1, j0:j1 + 1]
            np.maximum(blk, np.where(band, w, 0.0), out=blk)
        touched.append({"name": p.name, "xz": [round(p.x, 1), round(p.z, 1)],
                        "declared_radius_m": p.declared_m, "protected": p.protected,
                        "quantity": p.quantity,
                        "hard_m": round(hard, 1), "keepout_m": round(ko, 1)})
    return forbid, soft, touched


def near_centreline(pois: list[Poi], nodes: list[tuple[float, float]],
                    half_width_m: float) -> list[dict]:
    """Every instance whose HARD radius the finished centreline comes within.

    Reported even when empty: "the route clears every vanilla POI" is a claim
    that needs a measurement behind it, and this is the measurement.
    """
    P = np.array([(p.x, p.z) for p in pois], dtype=np.float64)
    N = np.array(nodes, dtype=np.float64)
    out = []
    for k, p in enumerate(pois):
        hard = p.hard_m(half_width_m)
        ko = p.keepout_m(half_width_m)
        d = float(np.min(np.hypot(N[:, 0] - P[k, 0], N[:, 1] - P[k, 1])))
        if d <= ko:
            out.append({"name": p.name, "xz": [round(p.x, 1), round(p.z, 1)],
                        "protected": p.protected, "declared_radius_m": p.declared_m,
                        "centreline_dist_m": round(d, 1),
                        "hard_m": round(hard, 1), "keepout_m": round(ko, 1),
                        "violates_hard": d < hard})
    return sorted(out, key=lambda r: r["centreline_dist_m"])
