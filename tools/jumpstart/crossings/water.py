#!/usr/bin/env python3
"""Water measurement for crossings: straits, rivers, shorelines, depths.

Why this file exists and why it does not reuse `terraform/heights.py`:
`heights.py` decodes a `VHPATCH1` file into Python lists, which is the right
shape for probing a handful of samples around one pad. A crossing question is
not that shape -- "how wide is the strait between these two landmasses and how
deep is it at the narrowest usable line" is a raster question over tens of
millions of samples, so the same file format is decoded here straight into a
numpy view with ZERO copies. Same bytes, same layout, same sample-centre
convention; if the two ever disagree about where sample (i, j) is, this file is
wrong, because `heights.py` is the one that has been used against the live
world.

Sample (i, j) of a patch sits at world
    x = cx - half + j*step + step/2
    z = cz - half + i*step + step/2
exactly as documented in `terraform/heights.py`.

The datum that matters here is the WATER PLANE, and it is not a taste pick:
MEASURED in assembly_valheim.dll 1.0.12, `ZoneSystem::c_WaterLevel` is a
`static literal float32(30.)`. So `depth = 30.0 - generated_height` and a sample
is wet when its height is below 30. Land is taken at > 30.5 rather than > 30.0
so that the shoreline sample itself -- which is within centimetres of the plane
and reads as either -- is never counted as dry ground a pier could stand on.
That 0.5 m is the same figure `RoadNet` used to label landmasses, deliberately,
so the two agents' landmass identities are the same identities.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np

MAGIC = b"VHPATCH1"

# MEASURED: ZoneSystem::c_WaterLevel = static literal float32(30.) in
# assembly_valheim.dll 1.0.12.
WATER_LEVEL = 30.0

# Land threshold. 0.5 m above the plane, so a shoreline sample is never mistaken
# for buildable dry ground. Shared with RoadNet's landmass labelling.
LAND_LEVEL = 30.5


@dataclass
class Field:
    """A 1 m (or step m) height+biome raster over one rectangle of the world."""

    id: str
    cx: float
    cz: float
    half: float
    step: float
    n: int
    heights: np.ndarray  # (n, n) float32, generated height, rivers included
    biomes: np.ndarray  # (n, n) uint8

    # --- coordinate transforms -------------------------------------------------
    @property
    def x0(self) -> float:
        return self.cx - self.half + self.step / 2

    @property
    def z0(self) -> float:
        return self.cz - self.half + self.step / 2

    def world_x(self, j) -> np.ndarray:
        return self.x0 + np.asarray(j) * self.step

    def world_z(self, i) -> np.ndarray:
        return self.z0 + np.asarray(i) * self.step

    def index(self, x: float, z: float) -> tuple[int, int]:
        j = int(round((x - self.x0) / self.step))
        i = int(round((z - self.z0) / self.step))
        if not (0 <= i < self.n and 0 <= j < self.n):
            raise ValueError(f"({x}, {z}) is outside field {self.id}")
        return i, j

    def contains(self, x: float, z: float, margin: float = 0.0) -> bool:
        return (self.x0 + margin <= x <= self.x0 + (self.n - 1) * self.step - margin
                and self.z0 + margin <= z <= self.z0 + (self.n - 1) * self.step - margin)

    def height_at(self, x: float, z: float) -> float:
        """Bilinear sample. A placement centre is usually not on the lattice."""
        fx = (x - self.x0) / self.step
        fz = (z - self.z0) / self.step
        j0, i0 = int(np.floor(fx)), int(np.floor(fz))
        if not (0 <= j0 < self.n - 1 and 0 <= i0 < self.n - 1):
            raise ValueError(f"({x}, {z}) is outside field {self.id}")
        tx, tz = fx - j0, fz - i0
        h = self.heights
        return float(h[i0, j0] * (1 - tx) * (1 - tz) + h[i0, j0 + 1] * tx * (1 - tz)
                     + h[i0 + 1, j0] * (1 - tx) * tz + h[i0 + 1, j0 + 1] * tx * tz)

    # --- derived masks ---------------------------------------------------------
    def wet(self) -> np.ndarray:
        return self.heights < WATER_LEVEL

    def land(self) -> np.ndarray:
        return self.heights > LAND_LEVEL

    def depth(self) -> np.ndarray:
        """Metres of water. Zero on land, never negative."""
        return np.maximum(WATER_LEVEL - self.heights, 0.0)


def load(path: str | Path, only: set[str] | None = None) -> dict[str, Field]:
    """Memory-map a VHPATCH1 file and return numpy views, no copies.

    `only` restricts which patch ids are materialised; the rest are skipped by
    seeking past their bytes, so asking for one 1600 m patch out of a 230 MB
    file costs one patch, not the file.
    """
    data = np.memmap(path, dtype=np.uint8, mode="r")
    raw = memoryview(data.data)
    if bytes(raw[:8]) != MAGIC:
        raise ValueError(f"{path} is not a VHPATCH1 file")
    off = 8
    (_seed_hash,) = struct.unpack_from("<i", raw, off)
    off += 4
    (name_len,) = struct.unpack_from("<i", raw, off)
    off += 4 + name_len
    (count,) = struct.unpack_from("<i", raw, off)
    off += 4
    out: dict[str, Field] = {}
    for _ in range(count):
        (id_len,) = struct.unpack_from("<i", raw, off)
        off += 4
        pid = bytes(raw[off:off + id_len]).decode()
        off += id_len
        cx, cz, half, step = struct.unpack_from("<ffff", raw, off)
        off += 16
        (n,) = struct.unpack_from("<i", raw, off)
        off += 4
        h_off, b_off = off, off + 4 * n * n
        off = b_off + n * n
        if only is not None and pid not in only:
            continue
        heights = np.frombuffer(data, dtype="<f4", count=n * n, offset=h_off).reshape(n, n)
        biomes = np.frombuffer(data, dtype=np.uint8, count=n * n, offset=b_off).reshape(n, n)
        out[pid] = Field(pid, cx, cz, half, step, n, heights, biomes)
    return out


# ---------------------------------------------------------------------------
# Crossing measurement
# ---------------------------------------------------------------------------

@dataclass
class Profile:
    """What a straight line from A to B crosses, MEASURED sample by sample."""

    ax: float
    az: float
    bx: float
    bz: float
    bearing_deg: float
    length_m: float
    # the wet run that has to be spanned
    span_m: float
    span_start_m: float  # distance along A->B where the wet run starts
    span_end_m: float
    bank_a_y: float  # generated height at the dry sample just before the water
    bank_b_y: float
    bank_a_xz: tuple[float, float]
    bank_b_xz: tuple[float, float]
    max_depth_m: float
    bed_min_y: float
    wet_fraction: float
    wet_runs: int
    heights: np.ndarray
    extend_m: float = 0.0

    @property
    def deck_y_min(self) -> float:
        """Lowest deck that still clears the water plane by 0.6 m.

        0.6 m is not arbitrary: it is the freeboard the existing `early-dock`
        placement declares for a dock DECK over the y=30 plane
        (`target_y: 30.6`, `target_y_basis: 'a dock DECK, not a sea floor'`), so
        a bridge deck and a dock deck sit at the same height above water and read
        as one piece of infrastructure.
        """
        return WATER_LEVEL + 0.6


def profile(field: Field, ax: float, az: float, bx: float, bz: float,
            step_m: float | None = None) -> Profile:
    """Sample the straight line A->B and measure the water it crosses."""
    step_m = step_m or field.step
    dx, dz = bx - ax, bz - az
    length = float(np.hypot(dx, dz))
    if length <= 0:
        raise ValueError("A and B coincide")
    k = max(2, int(round(length / step_m)) + 1)
    t = np.linspace(0.0, 1.0, k)
    xs, zs = ax + dx * t, az + dz * t
    hs = np.array([field.height_at(x, z) for x, z in zip(xs, zs)], dtype=np.float64)
    d = t * length

    wet = hs < WATER_LEVEL
    # Longest contiguous wet run: that is the thing a bridge has to span. A line
    # that clips two separate channels has two runs and gets reported as such,
    # because "span" for a multi-channel line is a lie.
    runs, start, best = [], None, None
    for i, w in enumerate(wet):
        if w and start is None:
            start = i
        elif not w and start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, len(wet) - 1))
    if runs:
        best = max(runs, key=lambda r: r[1] - r[0])
        i0, i1 = best
        span = float(d[i1] - d[i0]) + step_m
        a_i = max(i0 - 1, 0)
        b_i = min(i1 + 1, len(hs) - 1)
        bank_a_y, bank_b_y = float(hs[a_i]), float(hs[b_i])
        bank_a = (float(xs[a_i]), float(zs[a_i]))
        bank_b = (float(xs[b_i]), float(zs[b_i]))
        bed = float(hs[i0:i1 + 1].min())
        span_start, span_end = float(d[i0]), float(d[i1])
    else:
        span = 0.0
        bank_a_y, bank_b_y = float(hs[0]), float(hs[-1])
        bank_a, bank_b = (ax, az), (bx, bz)
        bed = float(hs.min())
        span_start = span_end = 0.0

    bearing = float((np.degrees(np.arctan2(dx, dz))) % 360.0)
    return Profile(ax, az, bx, bz, bearing, length, span, span_start, span_end,
                   bank_a_y, bank_b_y, bank_a, bank_b,
                   float(max(0.0, WATER_LEVEL - bed)), bed,
                   float(wet.mean()), len(runs), hs)


def landmasses(field: Field, min_cells: int = 64):
    """Label connected land components. Returns (labels, sizes) 1-indexed."""
    from scipy import ndimage
    lab, count = ndimage.label(field.land())
    sizes = ndimage.sum(np.ones_like(lab, dtype=np.float32), lab,
                        np.arange(1, count + 1))
    return lab, sizes


def shoreline(field: Field, mask: np.ndarray) -> np.ndarray:
    """Land samples of `mask` that touch water. 4-connected, so a diagonal
    pinhole is not a shoreline."""
    from scipy import ndimage
    k = np.array([[0, 1, 0], [1, 0, 1], [0, 1, 0]], dtype=bool)
    return mask & ndimage.binary_dilation(field.wet(), k)
