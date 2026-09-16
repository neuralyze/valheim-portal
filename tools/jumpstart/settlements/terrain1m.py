#!/usr/bin/env python3
"""The 1 m VERDICT plane: land, water, biome and the earthwork bill for a pad.

Reads a `VHPATCH1` file from `blueprints/run_patchscan.sh`. PatchScan runs
`WorldGenerator.Initialize` with full pregeneration, so unlike the 8 m
`.biome` grid these heights INCLUDE rivers and lakes. Provenance of the file
this is used with tonight, and the reason it is not produced here: `RoadNet`
already ran it for the whole reachable world and published
`/tmp/roads/h1m.bin` -- 46,112,400 samples over seven landmasses, 230 MB. A
hardlinked snapshot lives at `/tmp/settle/h1m.bin` so its author's cleanup
cannot pull it away mid-analysis.

CROSS-CHECKED against two independent numbers before being trusted:
  * `StartTemple` ground from the ZoneSystem dump is y = 75.05; the 1 m plane
    reads 75.046 at (-64.68, 3.31). MEASURED agreement 0.004 m.
  * The 8 m river-free grid disagrees by up to 12.6 m where there is water
    (MEASURED at (661.5, 1088.5): grid 44.9, 1 m 32.3), which is the river the
    coarse plane cannot see. When they disagree, this plane is right.

WATER IS AT y = 30. MEASURED, assembly_valheim IL: `ZoneSystem::c_WaterLevel`
is `.field public static literal float32 c_WaterLevel = float32(30.)`.

THE CLAMP IS THE REAL SITING CONSTRAINT. `TerrainComp::ApplyToHeightmap` clamps
every per-sample level delta to +/-8 m of the GENERATED height (recorded as
`terraform/tcdata.py::CLAMP_M`), and only `ApplyToHeightmap` makes an edit
persist. So a pad whose worst sample needs more than 8 m of cut or fill CANNOT
be built as terrain at all, however good the site looks otherwise. Every pad
this module prices carries that verdict, and `pad_bill()` refuses to round it
away: `clamp_ok` is a measurement, not an opinion.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "terraform"))
sys.path.insert(0, str(HERE.parent / "blueprints"))

import heights as vhpatch  # noqa: E402  terraform/heights.py, the VHPATCH1 reader
from site_finder import flatten_cost  # noqa: E402  the ONE definition of the bill

# MEASURED, assembly_valheim IL: ZoneSystem::c_WaterLevel.
WATER_LEVEL_M = 30.0
# MEASURED, TerrainComp::ApplyToHeightmap, recorded in terraform/tcdata.py.
CLAMP_M = 8.0

BIOME_NAME = {
    0: "None", 1: "Meadows", 2: "Swamp", 3: "Mountain", 4: "BlackForest",
    5: "Plains", 6: "AshLands", 7: "DeepNorth", 8: "Ocean", 9: "Mistlands",
}
BIOME_CODE = {v: k for k, v in BIOME_NAME.items()}


@dataclass
class Field:
    """One patch as arrays, indexed [iz, ix] with ix along +x."""
    id: str
    cx: float
    cz: float
    half: float
    step: float
    n: int
    h: np.ndarray
    biome: np.ndarray

    @property
    def x0(self) -> float:
        return self.cx - self.half + self.step / 2

    @property
    def z0(self) -> float:
        return self.cz - self.half + self.step / 2

    def bounds(self) -> tuple[float, float, float, float]:
        return (self.cx - self.half, self.cx + self.half,
                self.cz - self.half, self.cz + self.half)

    def contains(self, x: float, z: float, pad_m: float = 0.0) -> bool:
        x0, x1, z0, z1 = self.bounds()
        return (x0 + pad_m <= x <= x1 - pad_m) and (z0 + pad_m <= z <= z1 - pad_m)

    def idx(self, x: float, z: float) -> tuple[int, int]:
        return (int(round((z - self.z0) / self.step)),
                int(round((x - self.x0) / self.step)))

    def world(self, iz: int, ix: int) -> tuple[float, float]:
        return (self.x0 + ix * self.step, self.z0 + iz * self.step)

    def at(self, x: float, z: float) -> float:
        iz, ix = self.idx(x, z)
        return float(self.h[iz, ix])

    def biome_at(self, x: float, z: float) -> str:
        iz, ix = self.idx(x, z)
        return BIOME_NAME.get(int(self.biome[iz, ix]), "?")

    def window(self, x: float, z: float, half_x: float, half_z: float
               ) -> tuple[np.ndarray, np.ndarray]:
        """Height and biome over an axis-aligned rectangle, clipped to the patch."""
        iz0, ix0 = self.idx(x - half_x, z - half_z)
        iz1, ix1 = self.idx(x + half_x, z + half_z)
        iz0, ix0 = max(iz0, 0), max(ix0, 0)
        iz1, ix1 = min(iz1, self.n - 1), min(ix1, self.n - 1)
        return (self.h[iz0:iz1 + 1, ix0:ix1 + 1],
                self.biome[iz0:iz1 + 1, ix0:ix1 + 1])


def load(path: str | Path) -> dict[str, Field]:
    out: dict[str, Field] = {}
    for pid, p in vhpatch.load(path).items():
        h = np.asarray(p.heights, dtype=np.float32).reshape(p.n, p.n)
        b = np.frombuffer(bytes(p.biomes), dtype=np.uint8).reshape(p.n, p.n)
        out[pid] = Field(pid, p.cx, p.cz, p.half, p.step, p.n, h, b)
    return out


def field_for(fields: dict[str, Field], x: float, z: float, pad_m: float = 0.0
              ) -> Field | None:
    """The patch that holds this point with `pad_m` to spare, preferring the one
    whose centre it is nearest -- the published patches OVERLAP (mainland and
    westisle share x[-802, 840]) and a point in the overlap must resolve
    deterministically or two runs disagree."""
    cands = [f for f in fields.values() if f.contains(x, z, pad_m)]
    if not cands:
        return None
    return min(cands, key=lambda f: (f.cx - x) ** 2 + (f.cz - z) ** 2)


# --- pads -----------------------------------------------------------------

def pad_bill(f: Field, x: float, z: float, width_m: float, depth_m: float,
             yaw_deg: float = 0.0, apron_m: float = 0.0) -> dict:
    """Price levelling a `width_m` x `depth_m` pad centred on (x, z).

    Yaw is honoured rather than ignored: a building turned 30 degrees does not
    occupy the axis-aligned box its extents imply, and `flatten.py` levels the
    footprint it is given. Samples are taken on the 1 m lattice and tested
    against the ROTATED rectangle, so a yawed pad is priced on the ground it
    actually covers.

    The returned dict is `site_finder.flatten_cost` (target_y = the footprint
    median, the plane that minimises material moved) plus:
      samples          how many 1 m samples the pad covers
      clamp_ok         does EVERY sample stay inside +/-8 m of generated height
      over_clamp       how many samples do not
      worst_delta_m    the largest |delta|, signed by which way it went
      wet_samples      samples below the water plane -- non-zero means this pad
                       is partly in water, and levelling it would create the
                       dry-land-under-a-pier defect
      biome_counts     what the pad is standing in, by 1 m sample
    """
    half_w, half_d = width_m / 2 + apron_m, depth_m / 2 + apron_m
    reach = math.hypot(half_w, half_d)
    hz, bz = f.window(x, z, reach, reach)
    if hz.size == 0:
        raise ValueError(f"pad at ({x}, {z}) is outside patch {f.id}")
    iz0, ix0 = f.idx(x - reach, z - reach)
    iz0, ix0 = max(iz0, 0), max(ix0, 0)
    zz, xx = np.meshgrid(
        (np.arange(hz.shape[0]) + iz0) * f.step + f.z0,
        (np.arange(hz.shape[1]) + ix0) * f.step + f.x0, indexing="ij")
    a = math.radians(yaw_deg)
    ca, sa = math.cos(a), math.sin(a)
    dx, dz = xx - x, zz - z
    # Into the pad's own frame: the inverse rotation of the body's yaw.
    lx = dx * ca + dz * sa
    lz = -dx * sa + dz * ca
    inside = (np.abs(lx) <= half_w) & (np.abs(lz) <= half_d)
    win = hz[inside]
    if win.size == 0:
        raise ValueError(f"pad at ({x}, {z}) covers no samples")
    bill = flatten_cost(win)
    target = bill["target_y"]
    dev = win.astype(np.float64) - target          # + = ground above target = cut
    worst = float(dev.max() if dev.max() >= -dev.min() else dev.min())
    counts: dict[str, int] = {}
    for code, cnt in zip(*np.unique(bz[inside], return_counts=True)):
        counts[BIOME_NAME.get(int(code), "?")] = int(cnt)
    bill.update({
        "yaw_deg": round(yaw_deg, 1),
        "width_m": width_m, "depth_m": depth_m, "apron_m": apron_m,
        "samples": int(win.size),
        "over_clamp": int((np.abs(dev) > CLAMP_M).sum()),
        "clamp_ok": bool((np.abs(dev) <= CLAMP_M).all()),
        "clamp_headroom_m": round(CLAMP_M - float(np.abs(dev).max()), 2),
        "worst_delta_m": round(worst, 2),
        "wet_samples": int((win <= WATER_LEVEL_M).sum()),
        "freeboard_m": round(target - WATER_LEVEL_M, 2),
        "biome_counts": counts,
        "patch": f.id,
    })
    return bill


# --- landmasses -----------------------------------------------------------

def land_components(f: Field, min_area_m2: float = 20000.0) -> tuple[np.ndarray, list[dict]]:
    """Label connected land (height > water plane) with 4-connectivity.

    Returns the label image and one record per component above `min_area_m2`,
    largest first. The point of this is that Ulfsland is an ARCHIPELAGO
    (MEASURED independently by `RoadNet`: 7,794 separate landmasses, largest
    near spawn 2.79 km2), so "is my town reachable on foot from spawn" is a
    connected-component question and not a distance question.
    """
    from scipy import ndimage as ndi
    lab, cnt = ndi.label(f.h > WATER_LEVEL_M)
    out = []
    if cnt:
        sizes = ndi.sum(np.ones_like(lab, np.float32), lab, index=np.arange(1, cnt + 1))
        for i, npix in enumerate(sizes, start=1):
            area = float(npix) * f.step * f.step
            if area < min_area_m2:
                continue
            ys, xs = np.nonzero(lab == i)
            out.append({
                "label": i, "area_m2": area,
                "x_range": [float(f.x0 + xs.min() * f.step), float(f.x0 + xs.max() * f.step)],
                "z_range": [float(f.z0 + ys.min() * f.step), float(f.z0 + ys.max() * f.step)],
                "centroid": [float(f.x0 + xs.mean() * f.step), float(f.z0 + ys.mean() * f.step)],
            })
    out.sort(key=lambda r: -r["area_m2"])
    return lab, out


def walk_profile(f: Field, nodes: list[tuple[float, float]], step_m: float = 1.0,
                 baselines_m: tuple[int, ...] = (1, 4, 8, 16)) -> dict:
    """Walkability of a polyline on the GENERATED ground.

    This is the check that decides whether a street is a street. The operator
    inspects by WALKING, so a layout whose main street crosses a 2 m bank is a
    failure even though every building on it is perfect.

    THE BASELINE MATTERS AND THE FIRST VERSION OF THIS GOT IT WRONG. Gradient
    measured between ADJACENT 1 m samples is dominated by terrain noise, not by
    the slope a player feels: MEASURED on Stenvik's main street, 150 m long
    with 5.82 m of relief end to end -- an average of 3.9% -- the worst
    adjacent-sample gradient is 0.425, and it comes from a single 0.43 m bump.
    Judging the street on that number calls a gentle contour road unwalkable.

    So the gradient is reported over several baselines. `grade_1m` is the
    roughness, `grade_8m` is what a player experiences over a few strides, and
    `max_step_m` is the one number that can hide a wall. For reference, the
    hard limit is the player slide angle: MEASURED from `Character::GetSlideAngle`
    (via IL) it is 38 degrees, i.e. a gradient of 0.78, above which a player
    slides instead of walking.
    """
    pts: list[tuple[float, float]] = []
    for (x0, z0), (x1, z1) in zip(nodes, nodes[1:]):
        d = math.hypot(x1 - x0, z1 - z0)
        k = max(1, int(round(d / step_m)))
        for i in range(k):
            t = i / k
            pts.append((x0 + (x1 - x0) * t, z0 + (z1 - z0) * t))
    pts.append(nodes[-1])
    hs = [f.at(x, z) for x, z in pts]
    steps = [abs(b - a) for a, b in zip(hs, hs[1:])]
    dists = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:])]
    total = sum(dists)
    out = {
        "length_m": round(total, 1),
        "samples": len(pts),
        "y_min": round(min(hs), 2), "y_max": round(max(hs), 2),
        "relief_m": round(max(hs) - min(hs), 2),
        "max_step_m": round(max(steps), 2) if steps else 0.0,
        "end_to_end_grade": round(abs(hs[-1] - hs[0]) / total, 3) if total else 0.0,
        "slide_angle_grade": 0.781,   # MEASURED tan(38 deg), Character::GetSlideAngle
    }
    for b in baselines_m:
        if len(hs) <= b:
            continue
        g = [abs(hs[i + b] - hs[i]) / (b * step_m) for i in range(len(hs) - b)]
        out[f"grade_{b}m_max"] = round(max(g), 3)
        out[f"grade_{b}m_mean"] = round(sum(g) / len(g), 3)
    # Kept under the old names so a caller that only wants one number gets the
    # PLAYER-SCALE one rather than the noise one.
    out["max_grade"] = out.get("grade_8m_max", out.get("grade_1m_max", 0.0))
    out["mean_grade"] = out.get("grade_8m_mean", out.get("grade_1m_mean", 0.0))
    out["grade_baseline_m"] = 8
    out["wet_samples"] = sum(1 for v in hs if v <= WATER_LEVEL_M)
    return out


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/settle/h1m.bin"
    fields = load(path)
    print(f"{path}: {len(fields)} patches")
    for f in fields.values():
        x0, x1, z0, z1 = f.bounds()
        land = float((f.h > WATER_LEVEL_M).mean())
        print(f"  {f.id:14s} n={f.n:5d} step={f.step:.1f} "
              f"x[{x0:7.0f},{x1:7.0f}] z[{z0:7.0f},{z1:7.0f}] "
              f"h[{f.h.min():7.1f},{f.h.max():7.1f}] land={land:5.1%}")
