#!/usr/bin/env python3
"""Read a VHPATCH1 file produced by tools/jumpstart/blueprints/run_patchscan.sh.

Why this matters for terraforming and not just for site picking: a terrain edit
stores levelDelta per sample and the game applies it to the GENERATED height
(TerrainComp::ApplyToHeightmap), so the only way to write a flatten that
actually flattens is to know WorldGenerator.GetHeight at every sample.  PatchScan
is the one thing on this host that produces those numbers, at 1 m and with
rivers included.

Sample (i, j) of a patch sits at world
    x = cx - half + j*step + step/2
    z = cz - half + i*step + step/2
so a request of half=32.5, step=1 centred on a zone centre lands n=65 samples
per side exactly on that zone's TerrainComp sample centres, j == mask x and
i == mask y.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

MAGIC = b"VHPATCH1"


@dataclass
class Patch:
    id: str
    cx: float
    cz: float
    half: float
    step: float
    n: int
    heights: list[float]
    biomes: list[int]

    def at(self, i: int, j: int) -> float:
        return self.heights[i * self.n + j]

    def world(self, i: int, j: int) -> tuple[float, float]:
        return (self.cx - self.half + j * self.step + self.step / 2,
                self.cz - self.half + i * self.step + self.step / 2)

    def height_at_world(self, wx: float, wz: float) -> float:
        """Bilinear sample, for checking a height at a position that is not on
        the lattice (a placement centre usually is not)."""
        fx = (wx - (self.cx - self.half + self.step / 2)) / self.step
        fz = (wz - (self.cz - self.half + self.step / 2)) / self.step
        j0, i0 = int(fx // 1), int(fz // 1)
        if not (0 <= j0 < self.n - 1 and 0 <= i0 < self.n - 1):
            raise ValueError(f"({wx}, {wz}) is outside patch {self.id}")
        tx, tz = fx - j0, fz - i0
        h00, h10 = self.at(i0, j0), self.at(i0, j0 + 1)
        h01, h11 = self.at(i0 + 1, j0), self.at(i0 + 1, j0 + 1)
        return (h00 * (1 - tx) * (1 - tz) + h10 * tx * (1 - tz)
                + h01 * (1 - tx) * tz + h11 * tx * tz)


def load(path: str | Path) -> dict[str, Patch]:
    data = Path(path).read_bytes()
    if data[:8] != MAGIC:
        raise ValueError(f"{path} is not a VHPATCH1 file")
    off = 8
    (_seed_hash,) = struct.unpack_from("<i", data, off)
    off += 4
    (name_len,) = struct.unpack_from("<i", data, off)
    off += 4 + name_len
    (count,) = struct.unpack_from("<i", data, off)
    off += 4
    patches: dict[str, Patch] = {}
    for _ in range(count):
        (id_len,) = struct.unpack_from("<i", data, off)
        off += 4
        pid = data[off:off + id_len].decode()
        off += id_len
        cx, cz, half, step = struct.unpack_from("<ffff", data, off)
        off += 16
        (n,) = struct.unpack_from("<i", data, off)
        off += 4
        heights = list(struct.unpack_from(f"<{n * n}f", data, off))
        off += 4 * n * n
        biomes = list(data[off:off + n * n])
        off += n * n
        patches[pid] = Patch(pid, cx, cz, half, step, n, heights, biomes)
    return patches
