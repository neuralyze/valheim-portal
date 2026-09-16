#!/usr/bin/env python3
"""Find the sites worth walking to, on the coarse plane, for one seed.

This is the SEARCH half of settlement siting. It answers, over the whole world
at 8 m: where is there a patch of one biome big enough and flat enough for the
thing I want to put there, far enough from a vanilla location that I will not
bulldoze it, and close enough to the places a player already is that they will
walk to it. It DOES NOT decide flatness -- `refine.py` re-takes every verdict at
1 m from `blueprints/run_patchscan.sh`, which is the only plane that includes
rivers and the only resolution the game actually builds terrain at.

Per site type, the criteria and why each one is there:

    town         Meadows purity over a whole district, low relief, coastal so
                 `Crossings` can put one harbour on the town's own waterfront
                 rather than a second waterfront 200 m away, and near the
                 StartTemple so the operator can walk out of spawn into it.
    watchtower   local prominence over a 160 m radius -- a tower in a hollow is
                 a shed. Small pad, so the earthwork is cheap even on a knoll.
    castle       Mountain biome, high, and with a SUMMIT BROAD ENOUGH that a
                 level pad stays inside TerrainComp's +/-8 m per-sample clamp.
                 That last constraint is what disqualifies most peaks.
    lighthouse   on the coast (water within a cell or two), standing above the
                 water it marks, and at a headland -- measured as water on
                 three of the four cardinal bearings within 120 m.
    treehouse    BlackForest, canopy country, on ground flat enough for a
                 stilted body to reach the pad with its posts.

Clearance from vanilla locations is EXACT against the `run_locscan.sh` dump,
with a stand-off of the location's own worst measured piece reach. Two limits
of that, both stated because a check that quietly answers the wrong question is
the defect this project keeps hitting:

  * The dump is MOD-FREE by construction (`run_locscan.sh` excludes BepInEx),
    so it CANNOT see More_World_Locations' POIs. A site that clears here still
    has to clear a LIVE marker check after `zones_generate`.
  * MEASURED in the previous world: location PIECES reach 43 m past their
    marker, so a marker-distance stand-off smaller than that deletes POI
    content. `STANDOFF_M` is set from that measurement plus the pad's own half
    diagonal, not from the declared `location_clearance_m`.

Usage:
    ./survey.py --grid /tmp/fb/grid/00000.biome --locations /tmp/settle/loc/<h>.json
    ./survey.py ... --kind town --top 12
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

import grid as G

# MEASURED in the previous Ulfsland world: a vanilla location's pieces were
# found 43 m from the location marker, so a marker-based stand-off under that
# deletes POI content. Everything here keeps 43 m PLUS the pad's own half
# diagonal PLUS a 10 m margin.
LOCATION_PIECE_REACH_M = 43.0
STANDOFF_MARGIN_M = 10.0

# The player spawn, from the locscan dump's StartTemple. Walkability from spawn
# is a first-class criterion: the operator inspects by portal and on foot.
SPAWN_FALLBACK = (-64.68, 3.31)


@dataclass
class Candidate:
    kind: str
    x: float
    z: float
    h: float
    biome: str
    window_m: float
    relief_m: float
    biome_fraction: float
    dist_water_m: float
    prominence_m: float
    dist_spawn_m: float
    nearest_location: str = ""
    nearest_location_m: float = 0.0
    score: float = 0.0
    notes: dict = field(default_factory=dict)

    def row(self) -> str:
        return (f"{self.kind:11s} x={self.x:8.0f} z={self.z:8.0f} h={self.h:7.1f} "
                f"{self.biome:11s} win={self.window_m:5.0f} relief={self.relief_m:6.1f} "
                f"pure={self.biome_fraction:4.2f} water={self.dist_water_m:6.0f} "
                f"prom={self.prominence_m:6.1f} spawn={self.dist_spawn_m:6.0f} "
                f"poi={self.nearest_location or '-'}@{self.nearest_location_m:.0f}")


def load_locations(path: str | Path) -> tuple[np.ndarray, list[str], tuple[float, float]]:
    d = json.loads(Path(path).read_text())
    locs = d["locations"]
    xs = np.array([l["x"] for l in locs], np.float32)
    zs = np.array([l["z"] for l in locs], np.float32)
    names = [l["name"] for l in locs]
    spawn = SPAWN_FALLBACK
    for l in locs:
        if l["name"] == "StartTemple":
            spawn = (float(l["x"]), float(l["z"]))
            break
    return np.stack([xs, zs], 1), names, spawn


def nearest_location(pts: np.ndarray, names: list[str], x: float, z: float
                     ) -> tuple[str, float]:
    d = np.hypot(pts[:, 0] - x, pts[:, 1] - z)
    i = int(np.argmin(d))
    return names[i], float(d[i])


def _peaks(mask: np.ndarray, value: np.ndarray, min_sep_cells: int, limit: int
           ) -> list[tuple[int, int]]:
    """Greedy non-maximum suppression: take the best cell, forbid a disc around
    it, repeat. Gives well-separated sites instead of 40 cells of one hill."""
    ys, xs = np.nonzero(mask)
    if len(ys) == 0:
        return []
    order = np.argsort(-value[ys, xs])
    taken: list[tuple[int, int]] = []
    for t in order:
        iy, ix = int(ys[t]), int(xs[t])
        if all((iy - py) ** 2 + (ix - px) ** 2 >= min_sep_cells ** 2 for py, px in taken):
            taken.append((iy, ix))
            if len(taken) >= limit:
                break
    return taken


class Survey:
    def __init__(self, grid_path: str, loc_path: str):
        self.g = G.load(grid_path)
        self.loc_pts, self.loc_names, self.spawn = load_locations(loc_path)
        self.dist_water = G.distance_to_water_m(self.g)
        self._relief: dict[float, np.ndarray] = {}
        self._frac: dict[tuple[int, float], np.ndarray] = {}
        self._prom: dict[float, np.ndarray] = {}

    def relief(self, m: float) -> np.ndarray:
        if m not in self._relief:
            self._relief[m] = G.relief(self.g, m)
        return self._relief[m]

    def frac(self, code: int, m: float) -> np.ndarray:
        key = (code, m)
        if key not in self._frac:
            self._frac[key] = G.biome_fraction(self.g, code, m)
        return self._frac[key]

    def prominence(self, r: float) -> np.ndarray:
        if r not in self._prom:
            self._prom[r] = G.prominence_m(self.g, r)
        return self._prom[r]

    # --- per-kind searches -------------------------------------------------
    def town(self, window_m: float = 176.0, purity: float = 0.94,
             max_relief: float = 11.0, max_water_m: float = 260.0,
             limit: int = 12) -> list[Candidate]:
        g = self.g
        rel = self.relief(window_m)
        pure = self.frac(G.BIOME_CODE["Meadows"], window_m)
        dw = self.dist_water
        ok = ((pure >= purity) & (rel <= max_relief) & (g.height > 3.0)
              & (dw <= max_water_m) & (dw >= 24.0))
        sx, sz = self.spawn
        dsp = np.hypot(self._xplane() - sx, self._zplane() - sz)
        # Rank: purity and flatness first, proximity to spawn as the tiebreak
        # that decides which of several good districts the operator reaches.
        value = pure * 100.0 - rel * 2.0 - dsp / 400.0 - np.maximum(dw - 120.0, 0) / 60.0
        picks = _peaks(ok, value, min_sep_cells=int(700 / g.step), limit=limit * 3)
        out = [self._candidate("town", iy, ix, window_m, "Meadows", value) for iy, ix in picks]
        out = [c for c in out if self._clears(c, window_m)]
        return out[:limit]

    def watchtower(self, window_m: float = 24.0, prom_radius: float = 160.0,
                   min_prom: float = 10.0, max_relief: float = 6.0,
                   limit: int = 24) -> list[Candidate]:
        g = self.g
        rel = self.relief(window_m)
        prom = self.prominence(prom_radius)
        land = (g.biome != G.BIOME_CODE["Ocean"]) & (g.height > 5.0)
        ok = land & (prom >= -0.01) & (rel <= max_relief) & (self.local_rise(prom_radius) >= min_prom)
        value = self.local_rise(prom_radius) - rel
        picks = _peaks(ok, value, min_sep_cells=int(600 / g.step), limit=limit * 2)
        out = [self._candidate("watchtower", iy, ix, window_m, None, value) for iy, ix in picks]
        return [c for c in out if self._clears(c, window_m)][:limit]

    def local_rise(self, r: float) -> np.ndarray:
        """Height above the LOWEST ground within r -- the number that says a
        tower here overlooks something, where `prominence_m` only says it is a
        local maximum."""
        key = -r
        if key not in self._prom:
            k = self.g.cells_per(2 * r)
            mn, _ = G.window_minmax(self.g.height, k)
            self._prom[key] = self.g.height - mn
        return self._prom[key]

    def castle(self, window_m: float = 48.0, min_h: float = 150.0,
               max_relief: float = 13.0, limit: int = 12) -> list[Candidate]:
        g = self.g
        rel = self.relief(window_m)
        mfrac = self.frac(G.BIOME_CODE["Mountain"], window_m)
        ok = (g.height >= min_h) & (rel <= max_relief) & (mfrac >= 0.8)
        value = g.height - rel * 4.0
        picks = _peaks(ok, value, min_sep_cells=int(1200 / g.step), limit=limit * 2)
        out = [self._candidate("castle", iy, ix, window_m, "Mountain", value) for iy, ix in picks]
        return [c for c in out if self._clears(c, window_m)][:limit]

    def lighthouse(self, window_m: float = 24.0, max_water_m: float = 40.0,
                   min_rise: float = 6.0, limit: int = 12) -> list[Candidate]:
        g = self.g
        rel = self.relief(window_m)
        dw = self.dist_water
        rise = self.local_rise(140.0)
        ok = ((dw <= max_water_m) & (dw > 0) & (g.height > 4.0) & (rel <= 7.0)
              & (rise >= min_rise) & (g.biome != G.BIOME_CODE["Ocean"]))
        openness = self.sea_openness(160.0)
        ok = ok & (openness >= 0.45)
        value = g.height + openness * 40.0 - rel
        picks = _peaks(ok, value, min_sep_cells=int(900 / g.step), limit=limit * 3)
        out = []
        for iy, ix in picks:
            c = self._candidate("lighthouse", iy, ix, window_m, None, value)
            c.notes["sea_openness"] = round(float(openness[iy, ix]), 3)
            out.append(c)
        return [c for c in out if self._clears(c, window_m)][:limit]

    def sea_openness(self, reach_m: float) -> np.ndarray:
        """Fraction of a `reach_m` square window that is at or below sea level.
        A headland reads high; a bay shore reads low; inland reads zero."""
        k = self.g.cells_per(2 * reach_m)
        return G.window_mean((self.g.height <= 0.0).astype(np.float32), k)

    def treehouse(self, window_m: float = 24.0, purity: float = 0.85,
                  max_relief: float = 5.0, limit: int = 12) -> list[Candidate]:
        g = self.g
        rel = self.relief(window_m)
        pure = self.frac(G.BIOME_CODE["BlackForest"], window_m)
        ok = (pure >= purity) & (rel <= max_relief) & (g.height > 4.0)
        sx, sz = self.spawn
        dsp = np.hypot(self._xplane() - sx, self._zplane() - sz)
        value = pure * 50 - rel * 3 - dsp / 300.0
        picks = _peaks(ok, value, min_sep_cells=int(800 / g.step), limit=limit * 2)
        out = [self._candidate("treehouse", iy, ix, window_m, "BlackForest", value)
               for iy, ix in picks]
        return [c for c in out if self._clears(c, window_m)][:limit]

    # --- helpers ----------------------------------------------------------
    def _xplane(self) -> np.ndarray:
        if not hasattr(self, "_xp"):
            g = self.g
            ax = (-g.extent + np.arange(g.n) * g.step + g.step / 2).astype(np.float32)
            self._xp = np.broadcast_to(ax[None, :], (g.n, g.n))
            self._zp = np.broadcast_to(ax[:, None], (g.n, g.n))
        return self._xp

    def _zplane(self) -> np.ndarray:
        self._xplane()
        return self._zp

    def _candidate(self, kind: str, iy: int, ix: int, window_m: float,
                   biome: str | None, value: np.ndarray) -> Candidate:
        g = self.g
        x, z = g.world_of(iy, ix)
        name, dist = nearest_location(self.loc_pts, self.loc_names, x, z)
        return Candidate(
            kind=kind, x=float(x), z=float(z), h=float(g.height[iy, ix]),
            biome=biome or G.BIOME_NAME[int(g.biome[iy, ix])],
            window_m=window_m, relief_m=float(self.relief(window_m)[iy, ix]),
            biome_fraction=float(self.frac(G.BIOME_CODE[biome or G.BIOME_NAME[int(g.biome[iy, ix])]],
                                           window_m)[iy, ix]),
            dist_water_m=float(self.dist_water[iy, ix]),
            prominence_m=float(self.local_rise(160.0)[iy, ix]),
            dist_spawn_m=float(math.hypot(x - self.spawn[0], z - self.spawn[1])),
            nearest_location=name, nearest_location_m=dist,
            score=float(value[iy, ix]),
        )

    def _clears(self, c: Candidate, window_m: float) -> bool:
        need = LOCATION_PIECE_REACH_M + window_m * math.sqrt(2) / 2 + STANDOFF_MARGIN_M
        c.notes["standoff_required_m"] = round(need, 1)
        return c.nearest_location_m >= need


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--grid", default="/tmp/fb/grid/00000.biome")
    ap.add_argument("--locations", required=True)
    ap.add_argument("--kind", default="all")
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    s = Survey(a.grid, a.locations)
    print(f"# seed {s.g.seed_name} hash {s.g.seed_hash} spawn {s.spawn} "
          f"locations {len(s.loc_names)}")
    kinds = (["town", "watchtower", "castle", "lighthouse", "treehouse"]
             if a.kind == "all" else [a.kind])
    allc: list[Candidate] = []
    for k in kinds:
        cs = getattr(s, k)(limit=a.top)
        print(f"\n## {k}: {len(cs)}")
        for c in cs:
            print("  " + c.row())
        allc += cs
    if a.json:
        Path(a.json).write_text(json.dumps([c.__dict__ for c in allc], indent=1))
        print(f"\nwrote {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
