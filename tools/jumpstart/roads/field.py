#!/usr/bin/env python3
"""Numpy-backed height/biome fields for road routing, from PatchScan VHPATCH1.

Why a second reader when terraform/heights.py already parses VHPATCH1: that one
builds Python `list`s, which is right for a 65x65 pad (4,225 samples) and wrong
for a routing field.  Routing needs the WHOLE island at 1 m -- the spawn landmass
alone is 3,200 x 3,200 = 10.24 M samples -- and a Python list of 10 M floats
costs ~8 bytes of pointer plus a 24-byte float object each.  `numpy.frombuffer`
maps the same bytes at 4 bytes per sample with no copy, and the routing maths is
array maths anyway.  terraform/heights.py stays the reader for pads; this is the
reader for fields.  Same file format, same sample geometry, different container.

MEASURED CONSTANTS used here, with their source:
  * water plane y = 30.0            ZoneSystem::c_WaterLevel, a
                                    `static literal float32(30.)` in
                                    assembly_valheim.dll 1.0.12.  PatchScan's
                                    GetHeight is in the same world frame -- on
                                    Pirate68 the Ocean biome's height p99 is
                                    3.88 and the non-Ocean p1 is 8.11, so the
                                    heights are absolute world y and 30 is the
                                    shoreline, not an offset.
  * player slide angle = 38 deg     Character::GetSlideAngle returns literal
                                    38 for IsPlayer (45 mounted, 90 monster).
                                    A player standing on ground steeper than
                                    this SLIDES.  This is the absolute ceiling
                                    on any walkable grade, not a design target.

SAMPLE GEOMETRY, from PatchScan.cs (identical to terraform/heights.py):
    x = cx - half + j*step + step/2
    z = cz - half + i*step + step/2
row-major, i outer (z ascending), j inner (x ascending).
"""

from __future__ import annotations

import math
import os
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

MAGIC = b"VHPATCH1"

# ZoneSystem::c_WaterLevel, measured.
WATER_LEVEL = 30.0
# Character::GetSlideAngle for a player, measured, in degrees.
PLAYER_SLIDE_DEG = 38.0

BIOME_NAMES = {
    0: "None", 1: "Meadows", 2: "Swamp", 3: "Mountain", 4: "BlackForest",
    5: "Plains", 6: "AshLands", 7: "DeepNorth", 8: "Ocean", 9: "Mistlands",
    255: "Unknown",
}
BIOME_CODE = {v: k for k, v in BIOME_NAMES.items()}

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
DEFAULT_VH_SRC = Path(os.environ.get(
    "VH_SRC", "/media/big4/projects/game/valheim/Ulfsland/data/bepinex"))


@dataclass
class Field:
    """One PatchScan patch as arrays.  `h` and `biome` are (n, n), indexed [i, j]."""

    id: str
    cx: float
    cz: float
    half: float
    step: float
    n: int
    h: np.ndarray
    biome: np.ndarray

    # --- frame conversion -------------------------------------------------
    @property
    def x0(self) -> float:
        """World x of sample column 0's CENTRE."""
        return self.cx - self.half + self.step / 2

    @property
    def z0(self) -> float:
        return self.cz - self.half + self.step / 2

    def world(self, i: int, j: int) -> tuple[float, float]:
        return self.x0 + j * self.step, self.z0 + i * self.step

    def index(self, x: float, z: float) -> tuple[int, int]:
        """Nearest sample index.  Raises if outside the patch."""
        j = int(round((x - self.x0) / self.step))
        i = int(round((z - self.z0) / self.step))
        if not (0 <= i < self.n and 0 <= j < self.n):
            raise ValueError(f"({x}, {z}) is outside field {self.id}")
        return i, j

    def contains(self, x: float, z: float, margin: float = 0.0) -> bool:
        return (self.cx - self.half + margin <= x <= self.cx + self.half - margin
                and self.cz - self.half + margin <= z <= self.cz + self.half - margin)

    def at(self, x: float, z: float) -> float:
        i, j = self.index(x, z)
        return float(self.h[i, j])

    def bilinear(self, x: float, z: float) -> float:
        """Height at an off-lattice point.  A road centreline node lands
        off-lattice whenever the route is diagonal, so this is the normal path
        for profile fitting; the RIBBON itself is written on-lattice."""
        fx = (x - self.x0) / self.step
        fz = (z - self.z0) / self.step
        j0 = int(math.floor(fx))
        i0 = int(math.floor(fz))
        j0 = min(max(j0, 0), self.n - 2)
        i0 = min(max(i0, 0), self.n - 2)
        tx, tz = fx - j0, fz - i0
        tx = min(max(tx, 0.0), 1.0)
        tz = min(max(tz, 0.0), 1.0)
        h = self.h
        return float(h[i0, j0] * (1 - tx) * (1 - tz) + h[i0, j0 + 1] * tx * (1 - tz)
                     + h[i0 + 1, j0] * (1 - tx) * tz + h[i0 + 1, j0 + 1] * tx * tz)

    def biome_at(self, x: float, z: float) -> str:
        i, j = self.index(x, z)
        return BIOME_NAMES.get(int(self.biome[i, j]), "Unknown")

    # --- derived masks ----------------------------------------------------
    def land(self, freeboard: float = 0.5) -> np.ndarray:
        """Dry ground.  `freeboard` lifts the test above the water plane so a
        surf sample is not called land: a road sample AT y=30.0 is awash."""
        return self.h > WATER_LEVEL + freeboard

    def slope_deg(self) -> np.ndarray:
        """Terrain slope magnitude in degrees, central-difference, per sample."""
        gz, gx = np.gradient(self.h.astype(np.float32), self.step)
        return np.degrees(np.arctan(np.hypot(gx, gz)))


def load(path: str | Path) -> dict[str, Field]:
    """Parse VHPATCH1 with zero-copy numpy views over the file bytes."""
    data = memoryview(Path(path).read_bytes())
    if bytes(data[:8]) != MAGIC:
        raise ValueError(f"{path} is not a VHPATCH1 file")
    off = 8
    off += 4  # seed hash
    (name_len,) = struct.unpack_from("<i", data, off)
    off += 4 + name_len
    (count,) = struct.unpack_from("<i", data, off)
    off += 4
    fields: dict[str, Field] = {}
    for _ in range(count):
        (id_len,) = struct.unpack_from("<i", data, off)
        off += 4
        fid = bytes(data[off:off + id_len]).decode()
        off += id_len
        cx, cz, half, step = struct.unpack_from("<ffff", data, off)
        off += 16
        (n,) = struct.unpack_from("<i", data, off)
        off += 4
        h = np.frombuffer(data, dtype="<f4", count=n * n, offset=off).reshape(n, n)
        off += 4 * n * n
        bio = np.frombuffer(data, dtype=np.uint8, count=n * n, offset=off).reshape(n, n)
        off += n * n
        fields[fid] = Field(fid, cx, cz, half, step, n, h, bio)
    return fields


def request(patches: list[tuple[str, float, float, float, float]], seed: str,
            out: str | Path, vh_src: Path = DEFAULT_VH_SRC,
            sandbox: str = "/tmp/patchscan/vh") -> dict[str, Field]:
    """Run PatchScan for (id, cx, cz, half, step) patches and load the result.

    Validated by OUTPUT, not exit status: the plugin SIGKILLs the process once
    the file is flushed, so a success is always raw_exit=137 (see
    blueprints/run_patchscan.sh).
    """
    out = Path(out)
    req = out.with_suffix(".req.tsv")
    req.parent.mkdir(parents=True, exist_ok=True)
    req.write_text("".join(
        f"{pid}\t{cx:g}\t{cz:g}\t{half:g}\t{step:g}\n" for pid, cx, cz, half, step in patches))
    env = {**os.environ, "VH_SRC": str(vh_src), "SEED": seed,
           "REQ": str(req), "OUT": str(out), "SANDBOX": sandbox}
    proc = subprocess.run(["bash", str(JUMPSTART / "blueprints" / "run_patchscan.sh")],
                          env=env, capture_output=True, text=True)
    if not out.exists() or out.stat().st_size == 0:
        raise SystemExit(f"patchscan produced nothing:\n{proc.stderr[-4000:]}")
    return load(out)


def zone_field_digest(fld: Field, zx: int, zz: int) -> str:
    """sha256 of the 65x65 GENERATED heights for one zone, little-endian float32,
    row-major with z ascending outer -- the digest BuildLedger requires on every
    terrain_write entry so a replay can detect a worldgen change instead of
    silently putting the ground somewhere else.

    The byte order is stated because the digest is worthless without it.
    """
    import hashlib

    cx, cz = zx * 64.0, zz * 64.0
    rows = []
    for y in range(65):
        z = cz + (y - 32)
        row = np.empty(65, dtype="<f4")
        for x in range(65):
            row[x] = fld.at(cx + (x - 32), z)
        rows.append(row)
    return hashlib.sha256(np.concatenate(rows).tobytes()).hexdigest()


if __name__ == "__main__":
    import sys

    for fid, f in load(sys.argv[1]).items():
        ld = f.land()
        print(f"{fid}: n={f.n} step={f.step} centre=({f.cx},{f.cz}) half={f.half} "
              f"h[{f.h.min():.1f},{f.h.max():.1f}] land={ld.mean():.3f}")
