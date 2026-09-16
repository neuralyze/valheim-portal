#!/usr/bin/env python3
"""Coarse whole-world terrain plane for settlement siting, read from a
`tools/seedscan/run_scan.sh` `.biome` grid.

MEASURED provenance of the grid this module reads (/tmp/fb/grid/00000.biome,
index.tsv `Pirate68 147627509`): header `VHBIOME4`, step 8 m, half-extent
10496 m, n = 2*10496/8 = 2624 samples per side, 2 planes (biome uint8 then
float32 `WorldGenerator.GetHeight`). Sample (iy, ix) sits at world

    x = -extent + ix*step + step/2
    z = -extent + iy*step + step/2

so the plane covers x,z in [-10492, +10492] at 8 m.

TWO LIMITS OF THIS PLANE, both MEASURED, both load-bearing for siting:

* `SEEDSCAN_PREGEN=0` when it was produced, so `WorldGenerator.AddRivers`
  read an empty river dictionary and the height plane is RIVER-FREE. A valley
  that looks dry here can hold a river. Any site verdict must be re-taken at
  1 m from `blueprints/run_patchscan.sh`, which always pregenerates.
* 8 m cannot decide flatness for a building-sized footprint (site_finder.py's
  own table: 0.18 of 32 m / 2.5 m windows it calls flat really are). This
  plane SEARCHES; the 1 m patch DECIDES.

Biome codes are `SeedScan.BiomeCode`: 0 None, 1 Meadows, 2 Swamp, 3 Mountain,
4 BlackForest, 5 Plains, 6 AshLands, 7 DeepNorth, 8 Ocean, 9 Mistlands.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np

MAGIC = b"VHBIOME4"

BIOME_NAME = {
    0: "None", 1: "Meadows", 2: "Swamp", 3: "Mountain", 4: "BlackForest",
    5: "Plains", 6: "AshLands", 7: "DeepNorth", 8: "Ocean", 9: "Mistlands",
}
BIOME_CODE = {v: k for k, v in BIOME_NAME.items()}

# MEASURED, assembly_valheim IL: `ZoneSystem::c_WaterLevel` is
# `.field public static literal float32 c_WaterLevel = float32(30.)`. Land is
# height > 30, not height > 0, and every land/water and coastal test in this
# package goes through this constant.
WATER_LEVEL_M = 30.0


@dataclass
class Grid:
    step: int
    extent: int
    n: int
    seed_name: str
    seed_hash: int
    biome: np.ndarray   # (n, n) uint8, [iy, ix]
    height: np.ndarray  # (n, n) float32

    # --- frames -----------------------------------------------------------
    def world_of(self, iy: int, ix: int) -> tuple[float, float]:
        return (-self.extent + ix * self.step + self.step / 2,
                -self.extent + iy * self.step + self.step / 2)

    def index_of(self, x: float, z: float) -> tuple[int, int]:
        ix = int(round((x + self.extent - self.step / 2) / self.step))
        iy = int(round((z + self.extent - self.step / 2) / self.step))
        if not (0 <= ix < self.n and 0 <= iy < self.n):
            raise ValueError(f"({x}, {z}) outside the grid")
        return iy, ix

    def height_at(self, x: float, z: float) -> float:
        iy, ix = self.index_of(x, z)
        return float(self.height[iy, ix])

    def biome_at(self, x: float, z: float) -> str:
        iy, ix = self.index_of(x, z)
        return BIOME_NAME[int(self.biome[iy, ix])]

    def cells_per(self, metres: float) -> int:
        """Odd cell count covering `metres`, for a centred window."""
        k = int(round(metres / self.step))
        return k + 1 if k % 2 == 0 else k


def load(path: str | Path) -> Grid:
    data = Path(path).read_bytes()
    if data[:8] != MAGIC:
        raise ValueError(f"{path} is not a {MAGIC.decode()} grid")
    off = 8
    step, extent, n, planes, namelen = struct.unpack_from("<iiiii", data, off)
    off += 20
    seed_name = data[off:off + namelen].decode()
    off += namelen
    (seed_hash,) = struct.unpack_from("<i", data, off)
    off += 4
    if planes != 2:
        raise ValueError(f"{path} has {planes} plane(s); siting needs the height plane "
                         f"(re-run run_scan.sh with HEIGHT=1)")
    biome = np.frombuffer(data, dtype=np.uint8, count=n * n, offset=off).reshape(n, n).copy()
    off += n * n
    height = np.frombuffer(data, dtype="<f4", count=n * n, offset=off).reshape(n, n).copy()
    return Grid(step, extent, n, seed_name, seed_hash, biome, height)


# --- window reductions ----------------------------------------------------
# Implemented over summed-area / strided reductions rather than
# scipy.ndimage filters: MEASURED on this host (scipy 1.11.4, numpy 1.26.4)
# ndi.maximum_filter(H, size=25) on the 2624^2 plane returned a (2375, 2375)
# array AND left the input array's own shape reported as (2375, 2375), i.e. the
# filter is not trustworthy at this size here. These do the same job with
# numpy only.

def _pad_reflect(a: np.ndarray, r: int) -> np.ndarray:
    return np.pad(a, r, mode="edge")


def window_mean(a: np.ndarray, k: int) -> np.ndarray:
    """Mean over a k x k window centred on every cell (edge-padded)."""
    r = k // 2
    p = _pad_reflect(a.astype(np.float64), r)
    c = np.zeros((p.shape[0] + 1, p.shape[1] + 1), np.float64)
    c[1:, 1:] = p.cumsum(0).cumsum(1)
    n0, n1 = a.shape
    tot = (c[k:k + n0, k:k + n1] - c[0:n0, k:k + n1]
           - c[k:k + n0, 0:n1] + c[0:n0, 0:n1])
    return (tot / (k * k)).astype(np.float32)


def window_minmax(a: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    """Min and max over a k x k window, by separable running extremes."""
    r = k // 2
    p = _pad_reflect(a.astype(np.float32), r)

    def run(x: np.ndarray, axis: int, fn) -> np.ndarray:
        # k-wide sliding extreme along `axis`, output length = len - k + 1
        v = np.moveaxis(x, axis, -1)
        out = np.empty(v.shape[:-1] + (v.shape[-1] - k + 1,), np.float32)
        acc = v[..., 0:out.shape[-1]].copy()
        for s in range(1, k):
            fn(acc, v[..., s:s + out.shape[-1]], out=acc)
        out[...] = acc
        return np.moveaxis(out, -1, axis)

    mn = run(run(p, 0, np.minimum), 1, np.minimum)
    mx = run(run(p, 0, np.maximum), 1, np.maximum)
    return mn, mx


def biome_fraction(g: Grid, code: int, metres: float) -> np.ndarray:
    k = g.cells_per(metres)
    return window_mean((g.biome == code).astype(np.float32), k)


def relief(g: Grid, metres: float) -> np.ndarray:
    k = g.cells_per(metres)
    mn, mx = window_minmax(g.height, k)
    return mx - mn


def distance_to_water_m(g: Grid) -> np.ndarray:
    """Euclidean distance in metres from each cell to the nearest WET cell.

    Wet means at or below the water plane, and the water plane is not zero:
    MEASURED in assembly_valheim IL, `ZoneSystem::c_WaterLevel` is
    `.field public static literal float32 c_WaterLevel = float32(30.)`, and the
    height plane here is absolute (the seed's ocean floor reads -400 and the
    StartTemple ground reads ~75). An earlier version of this function tested
    `height <= 0` and so called every shoreline in the world 1.3 km inland.

    8 m resolution; the coastal tests built on it use >=24 m thresholds.
    """
    from scipy import ndimage as ndi
    wet = g.height <= WATER_LEVEL_M
    return ndi.distance_transform_edt(~wet, sampling=g.step).astype(np.float32)


def prominence_m(g: Grid, radius_m: float) -> np.ndarray:
    """Height above the highest other ground within `radius_m`, approximated on
    the square window of that half-width. Positive only on local maxima."""
    k = g.cells_per(2 * radius_m)
    _, mx = window_minmax(g.height, k)
    return g.height - mx


if __name__ == "__main__":
    import sys
    g = load(sys.argv[1] if len(sys.argv) > 1 else "/tmp/fb/grid/00000.biome")
    print(f"seed {g.seed_name} hash {g.seed_hash} step {g.step} n {g.n} "
          f"h [{g.height.min():.1f}, {g.height.max():.1f}]")
    for name, code in sorted(BIOME_CODE.items(), key=lambda kv: kv[1]):
        cnt = int((g.biome == code).sum())
        if cnt:
            print(f"  {name:12s} {cnt:9d} cells  {cnt * g.step * g.step / 1e6:8.1f} km2")
