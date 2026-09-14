#!/usr/bin/env python3
"""River-inclusive, point-exact terrain sampling for the road router.

There is exactly ONE sampler in this tree and this module does not become a second
one. It drives tools/jumpstart/blueprints/run_patchscan.sh -- a BepInEx plugin that
boots the real dedicated server with World.m_menu false, so Pregenerate() runs and
RIVERS AND LAKES ARE INCLUDED -- and reads the result with
tools/jumpstart/blueprints/site_finder.load_patches, which is the only reader.

Why that matters, MEASURED on Pirate68: the 8 m tools/seedscan grid omits
Pregenerate(), so at (-308, 172) it reports 48.29 m where the river-inclusive 1 m
sample reports 35.43 m -- a 12.9 m error, and the difference between a meadow and a
river valley. A router built on the coarse grid would bridge nothing and drown
half its nodes.

POINT EXACTNESS. A patch sample sits at x = cx - half + j*step + step/2. With
step 1.0, an INTEGER half and a HALF-INTEGER centre (cx = round(x) + 0.5), every
sample lands exactly on an integer world coordinate, so the height checked is the
height at the coordinate actually written into a plan rather than one half a cell
away. `Corridor` enforces that and refuses anything else.

Cost, MEASURED: 1,033,728 samples over 14 windows in 3.19 s of generator time
inside one sandbox boot, ~15 s wall including the sandbox rsync. So batch every
window into ONE request file and ONE boot; never boot per point.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import JUMPSTART

BLUEPRINTS = JUMPSTART / "blueprints"
PATCHSCAN = BLUEPRINTS / "run_patchscan.sh"
DEFAULT_VH_SRC = "/media/big4/projects/game/valheim/Ulfsland/data/bepinex"


def _load_site_finder():
    """Import the sibling's site_finder without putting its directory on sys.path.

    tools/jumpstart/blueprints/ is PlacementSolver's; importing by file location
    keeps this module a read-only consumer and avoids shadowing anything.
    """
    spec = importlib.util.spec_from_file_location(
        "jumpstart_blueprints_site_finder", BLUEPRINTS / "site_finder.py"
    )
    if spec is None or spec.loader is None:  # pragma: no cover - packaging failure
        raise RuntimeError(f"cannot load {BLUEPRINTS / 'site_finder.py'}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


SF = _load_site_finder()

#: Valheim's water plane, in WorldGenerator.GetHeight units. Imported, never
#: restated: one definition in the tree or the datum-0 incident happens again.
WATER_LEVEL: float = float(SF.WATER_LEVEL)
#: The game's own land threshold (ZoneSystem places land locations at minAltitude 1).
GAME_LAND_FREEBOARD: float = float(SF.DEFAULT_FREEBOARD)

BIOME_NAME: dict[int, str] = dict(SF.BIOME_NAME)


@dataclass(frozen=True)
class Window:
    """One patchscan request window. half and step are constrained, not advisory."""

    id: str
    cx: float
    cz: float
    half: int
    step: float = 1.0

    def __post_init__(self) -> None:
        if self.step != 1.0:
            raise ValueError("step must be 1.0: only step 1 with a half-integer centre is point-exact")
        if float(self.half) != int(self.half):
            raise ValueError("half must be an integer")
        if abs((self.cx % 1.0) - 0.5) > 1e-9 or abs((self.cz % 1.0) - 0.5) > 1e-9:
            raise ValueError(f"{self.id}: centre must be half-integer (round(x)+0.5), got {self.cx},{self.cz}")

    def tsv(self) -> str:
        return f"{self.id}\t{self.cx:.1f}\t{self.cz:.1f}\t{int(self.half)}\t{self.step:.1f}\n"

    @property
    def samples(self) -> int:
        return int(2 * self.half / self.step) ** 2


def tile(x0: int, x1: int, z0: int, z1: int, half: int = 200, prefix: str = "cor") -> list[Window]:
    """Cover an integer box with point-exact windows.

    A window centred at C+0.5 with half H covers integer coordinates C-H+1 .. C+H,
    so the centres step by 2H and the tiling is seamless with no overlap.
    """
    if x1 < x0 or z1 < z0:
        raise ValueError("empty box")
    out: list[Window] = []
    span = 2 * half
    cx = x0 + half - 1
    while cx - half + 1 <= x1:
        cz = z0 + half - 1
        while cz - half + 1 <= z1:
            out.append(Window(f"{prefix}_{cx}_{cz}", cx + 0.5, cz + 0.5, half))
            cz += span
        cx += span
    return out


@dataclass
class Corridor:
    """A mosaic of point-exact samples over an integer lattice, with helpers.

    height[i, j] is WorldGenerator.GetHeight at (x0 + j, z0 + i). NaN means a cell
    outside every window, which is a routing barrier rather than a guess.
    """

    x0: int
    z0: int
    height: np.ndarray
    biome: np.ndarray
    seed: str

    @property
    def shape(self) -> tuple[int, int]:
        return self.height.shape

    def contains(self, x: float, z: float) -> bool:
        j, i = int(round(x)) - self.x0, int(round(z)) - self.z0
        return 0 <= i < self.height.shape[0] and 0 <= j < self.height.shape[1]

    def at(self, x: float, z: float) -> tuple[float, int]:
        """Height and biome id at the nearest integer coordinate. Raises if outside."""
        j, i = int(round(x)) - self.x0, int(round(z)) - self.z0
        if not (0 <= i < self.height.shape[0] and 0 <= j < self.height.shape[1]):
            raise KeyError(f"({x},{z}) is outside the sampled corridor")
        return float(self.height[i, j]), int(self.biome[i, j])

    def freeboard(self, x: float, z: float) -> float:
        """Metres the ground clears the water plane by. Negative means submerged."""
        return self.at(x, z)[0] - WATER_LEVEL

    def biome_name(self, x: float, z: float) -> str:
        return BIOME_NAME.get(self.at(x, z)[1], "?")

    def coverage(self) -> float:
        return float(np.isfinite(self.height).mean())


def run(
    windows: list[Window],
    seed: str,
    work: Path,
    vh_src: str | None = None,
    reuse: bool = True,
) -> Path:
    """Run patchscan for `windows` in one boot. Returns the .bin path.

    `reuse` skips the run when the output already exists and the request file is
    byte-identical, which makes iterating on the router cheap.
    """
    work.mkdir(parents=True, exist_ok=True)
    req = work / "req.tsv"
    out = work / "patch.bin"
    body = "".join(w.tsv() for w in windows)
    if reuse and out.exists() and req.exists() and req.read_text() == body:
        return out
    req.write_text(body)
    env = dict(os.environ)
    env.update(
        {
            "VH_SRC": vh_src or os.environ.get("VH_SRC") or DEFAULT_VH_SRC,
            "SEED": seed,
            "REQ": str(req),
            "OUT": str(out),
            # Own sandbox, own savedir. patchscan binds port 3457, so two concurrent
            # runs collide: that is a serialise-or-retry situation, not a bug here.
            "SANDBOX": str(work / "vh"),
        }
    )
    subprocess.run(["bash", str(PATCHSCAN)], env=env, check=True, stdout=subprocess.DEVNULL)
    if not out.exists():
        raise RuntimeError(f"patchscan produced no output at {out}")
    return out


def load(path: Path, seed: str, only_prefix: str | None = None) -> Corridor:
    """Assemble patches in a .bin into one integer-lattice mosaic.

    `only_prefix` keeps just the windows whose id starts with it. One run can carry
    both a routing corridor and unrelated probe windows a thousand metres away;
    without the filter the mosaic's bounding box spans both and is mostly NaN.
    """
    patches = SF.load_patches(Path(path))
    if only_prefix:
        patches = {k: v for k, v in patches.items() if k.startswith(only_prefix)}
    if not patches:
        raise ValueError(f"{path}: no patches{' matching ' + only_prefix if only_prefix else ''}")
    xs: list[float] = []
    zs: list[float] = []
    for p in patches.values():
        if p["step"] != 1.0:
            raise ValueError(f"{path}: patch step {p['step']} is not 1.0; mosaic assumes an integer lattice")
        x0 = p["cx"] - p["half"] + p["step"] * 0.5
        z0 = p["cz"] - p["half"] + p["step"] * 0.5
        xs += [x0, x0 + (p["n"] - 1) * p["step"]]
        zs += [z0, z0 + (p["n"] - 1) * p["step"]]
    X0, X1, Z0, Z1 = int(min(xs)), int(max(xs)), int(min(zs)), int(max(zs))
    h = np.full((Z1 - Z0 + 1, X1 - X0 + 1), np.nan, dtype=np.float32)
    b = np.full(h.shape, 255, dtype=np.uint8)
    for p in patches.values():
        x0 = p["cx"] - p["half"] + p["step"] * 0.5
        z0 = p["cz"] - p["half"] + p["step"] * 0.5
        j0, i0 = int(round(x0 - X0)), int(round(z0 - Z0))
        h[i0 : i0 + p["n"], j0 : j0 + p["n"]] = p["height"]
        b[i0 : i0 + p["n"], j0 : j0 + p["n"]] = p["biome"]
    return Corridor(x0=X0, z0=Z0, height=h, biome=b, seed=seed)


def sample(points: list[tuple[float, float]], seed: str, work: Path, pad: int = 8, **kw) -> Corridor:
    """Convenience: sample small windows around a handful of points in one boot."""
    windows = [
        Window(f"pt{i}", round(x) + 0.5, round(z) + 0.5, pad) for i, (x, z) in enumerate(points)
    ]
    return load(run(windows, seed, work, **kw), seed)
