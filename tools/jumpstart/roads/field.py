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

import importlib.util
import json
import math
import os
import struct
import subprocess
from dataclasses import dataclass, replace
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


def _worldseed():
    """`worldseed`, imported LAZILY and with the jumpstart root APPENDED.

    Appended, never inserted: `tools/jumpstart/network/` is a package that
    shadows `roads/network.py` for every later importer if it gets in front of
    this directory -- the same trap `roads/spec.py` documents at its own
    import.  Lazy because `field` is imported by survey tooling that has no
    seed at all, and a hard import at module scope would make the path
    manipulation a cost every one of those pays.
    """
    import sys
    if str(JUMPSTART) not in sys.path:
        sys.path.append(str(JUMPSTART))
    import worldseed
    return worldseed


def _by_path(alias: str, path: Path):
    """Load a sibling module BY PATH under a private name, once.

    By path, not by `import <name>`: three `plan.py` exist in this tree and
    whoever imports first owns `sys.modules['plan']`, which has already cost a
    full run's worth of confusion here.  Every module this one reaches for
    across a directory gets the same treatment before it can happen again.
    """
    import sys
    if alias in sys.modules:
        return sys.modules[alias]
    spec = importlib.util.spec_from_file_location(alias, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod
    spec.loader.exec_module(mod)
    return mod


def _site_finder():
    """`blueprints/site_finder.py`: the seed's own land, measured."""
    return _by_path("jumpstart_blueprints_site_finder",
                    JUMPSTART / "blueprints" / "site_finder.py")


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


def header(path: str | Path) -> dict:
    """The VHPATCH1 header: the SEED THE FIELD WAS SAMPLED ON, and its hash.

    `load` read both of these and threw them away.  That was harmless while
    the seed was a literal in eight modules and could not be anything else;
    the moment `--seed` exists it stops being harmless, because planning a
    fresh seed against Ulfsland's 230 MB field succeeds and returns a road
    fitted to the wrong planet.  PatchScan writes the seed NAME, not just the
    hash (`PatchScan.cs` line 283), so the check can be exact.
    """
    data = memoryview(Path(path).read_bytes()[:4096])
    if bytes(data[:8]) != MAGIC:
        raise ValueError(f"{path} is not a VHPATCH1 file")
    (seed_hash,) = struct.unpack_from("<i", data, 8)
    (name_len,) = struct.unpack_from("<i", data, 12)
    return {"seed": bytes(data[16:16 + name_len]).decode(),
            "seed_hash": seed_hash}


def require_field(path: str | Path, seed: str) -> Path:
    """REFUSE a field that is not there, NAMING THE STAGE THAT MAKES ONE.

    MEASURED 2026-09-21, and it is the reason this exists: a fresh seed handed
    a path it has no field for got `FileNotFoundError: [Errno 2] No such file
    or directory: '/tmp/roads/h1m.bin'`, and a truncated one got "is not a
    VHPATCH1 file".  Both are true and neither says WHO PRODUCES ONE, so the
    cheapest way past them is to point `--field` at the field that does exist
    -- which is Ulfsland's.  An error that makes borrowing the easy answer is
    part of the defect.

    There is NO fallback and there is no synthetic field: `request_for_seed`
    makes a real one, or this run does not have ground to measure.
    """
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        raise SystemExit(
            f"no height field for seed {seed!r} at {p}.\n"
            f"A height field is PRODUCED, never defaulted or borrowed:\n"
            f"  1. tools/seedscan/run_scan.sh   SEEDS=<a file holding {seed!r}> "
            f"HEIGHT=1 STEP=8   -> the coarse whole-world grid\n"
            f"  2. roads/field.py::request_for_seed --grid <that grid>          "
            f"-> derives the windows from THIS SEED's own land components and "
            f"runs blueprints/run_patchscan.sh\n"
            f"Both need a Valheim dedicated-server install in $VH_SRC "
            f"(run_patchscan.sh:32). Another seed's field is refused by "
            f"require_seed and a hand-written one is not a measurement.")
    return p


def require_seed(path: str | Path, seed: str) -> dict:
    """REFUSE a field sampled on a different seed than the one being planned.

    Fails CLOSED and loudly.  The alternative -- a warning -- is how a wrong
    number gets into a plan file that later reads as evidence.

    THE REFUSAL ITSELF NOW LIVES IN `worldseed.require_scope`, because this was
    never a height-field rule.  MEASURED 2026-09-21: while this function was
    correctly refusing a foreign field, `poi.load` accepted 12,301 Pirate68
    locations and `spec.load_solved_sites` adopted 16 Pirate68 sites into the
    same Fresh42 run.  One artefact guarded out of three is the same as none,
    because the plan is the join of all three.

    ABSENT counts as wrong, and it is checked FIRST: a fresh seed's usual state
    is "no field at all", and an errno is not an instruction.
    """
    h = header(require_field(path, seed))
    _worldseed().require_scope(
        "height field", path, found_seed=h["seed"], seed_=seed,
        produced_by="roads/field.py::request_for_seed, which derives the "
                    "windows from the seed's own land and runs "
                    "blueprints/run_patchscan.sh; pass --field")
    return h


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
    # Road/pad heights are the rendered Heightmap corner-biome blend, not
    # WorldGenerator.GetHeight at one point. Do not inherit an ambient mode.
    env = {**os.environ, "VH_SRC": str(vh_src), "SEED": seed,
           "REQ": str(req), "OUT": str(out), "SANDBOX": sandbox,
           "SURFACE": "heightmap"}
    proc = subprocess.run(["bash", str(JUMPSTART / "blueprints" / "run_patchscan.sh")],
                          env=env, capture_output=True, text=True)
    if not out.exists() or out.stat().st_size == 0:
        raise SystemExit(f"patchscan produced nothing:\n{proc.stderr[-4000:]}")
    # The file must declare the seed we asked for.  PatchScan writes the seed
    # NAME into its header, so this catches a stale OUT, a sandbox that ignored
    # $SEED, and any future path where the scan silently scanned something else
    # -- the whole point of producing a field is that it is THIS seed's ground.
    require_seed(out, seed)
    return load(out)


def request_for_seed(seed: str, grid: str | Path, out: str | Path,
                     locations: str | Path, *,
                     spawn_location: str = "StartTemple",
                     vh_src: Path = DEFAULT_VH_SRC,
                     sandbox: str = "/tmp/patchscan/vh",
                     half_max_m: float | None = None,
                     step_m: float | None = None,
                     min_area_m2: float | None = None,
                     budget_bytes: int | None = None,
                     declared_windows: bool = False,
                     north_preset: str | None = None,
                     ) -> tuple[dict[str, Field], tuple]:
    """PRODUCE A HEIGHT FIELD FOR A SEED FROM THAT SEED'S OWN LAND.

    This is the stage that was missing.  `worldseed.ULFSLAND_WINDOWS` is seven
    hand-written window geometries MEASURED for Pirate68; every later stage
    reads a field, `require_seed` correctly refuses a foreign one, and there
    was nothing to hand a fresh seed -- so driving one meant hand-writing a
    synthetic VHPATCH1, which is a measurement of nothing.

    The chain, all of it seed-scoped and each link refused if it is not:

      tools/seedscan/run_scan.sh  (HEIGHT=1)   -> coarse whole-world grid
      tools/seedscan/run_locscan.sh            -> the generator's location dump
      site_finder.land_components(grid)        -> this seed's landmasses
      site_finder.component_at(grid, spawn)    -> the one the spawn stands on
      worldseed.windows_for_components(...)    -> trunk-primary/-secondary/
                                                  outlier-* window geometry
      blueprints/run_patchscan.sh              -> the 1 m VHPATCH1 field

    Returns the loaded field and the windows it was scanned with, so a caller
    can record what it asked for next to what it got.
    """
    ws = _worldseed()
    sf = _site_finder()
    grid, out = Path(grid), Path(out)
    g = sf.load_grid(grid)
    # load_grid refuses against `$VH_SEED`; this run has an EXPLICIT seed, and
    # an explicit one that nobody compared is exactly the unread declaration
    # this whole guard exists for.
    ws.require_scope("coarse biome grid", grid, found_seed=g.get("seed"),
                     seed_=seed,
                     produced_by="tools/seedscan/run_scan.sh SEEDS=<this seed> "
                                 "HEIGHT=1")
    doc = json.loads(Path(locations).read_text("utf-8"))
    poimod = _by_path("jumpstart_roads_poi", HERE / "poi.py")
    ws.require_scope("location dump", locations, found_seed=poimod.dump_seed(doc),
                     seed_=seed,
                     produced_by="tools/seedscan/run_locscan.sh for this seed")
    spawn = next((l for l in doc.get("locations", [])
                  if l.get("name") == spawn_location), None)
    if spawn is None:
        raise SystemExit(
            f"no {spawn_location!r} in {locations}: the spawn point is what "
            f"`trunk-primary` is defined as holding, and a window set without "
            f"it is not this seed's trunk. Re-run tools/seedscan/run_locscan.sh.")
    spawn_xz = (float(spawn["x"]), float(spawn["z"]))
    comp = sf.component_at(g, spawn_xz)
    kw = {k: v for k, v in (("half_max_m", half_max_m), ("step_m", step_m),
                            ("min_area_m2", min_area_m2),
                            ("budget_bytes", budget_bytes)) if v is not None}
    windows = (ws.ULFSLAND_WINDOWS if declared_windows else ws.windows_for_components(
        sf.land_components(g), spawn_xz,
        None if comp is None else comp["label"],
        # So each window records how far its landmass is from the spawn's, in
        # metres of water, rather than leaving a reader to infer it from
        # whether two windows happen to overlap.
        separation_of=(None if comp is None else
                       lambda label: sf.separation_m(g, comp["label"], label)),
        **kw))
    # The road-to-dock preview stamps the writer's INTEGER Heightmap lattice.
    # Read those vertices in the producer itself; stage 9 must not interpolate
    # half-grid settlement samples or issue a second unprovenanced terrain scan.
    # Expand one metre before shifting at most half a metre, preserving every
    # original boundary margin. PatchScan samples this NEW request geometry;
    # no old field's coordinates or bytes are reinterpreted.
    windows = tuple(replace(w, half=w.half+1.0,
        cx=round(w.cx-w.half+w.step/2.0)+w.half-w.step/2.0,
        cz=round(w.cz-w.half+w.step/2.0)+w.half-w.step/2.0)
        for w in windows)
    north = _by_path("jumpstart_roads_north_itinerary", HERE / "north_itinerary.py")
    coarse = north.coarse_route(g, spawn_xz, preset=north_preset)
    windows = tuple(windows) + north.route_windows(coarse)
    limit = ws.DEFAULT_WINDOW_BUDGET_BYTES if budget_bytes is None else budget_bytes
    cost = ws.windows_bytes(windows)
    if cost > limit:
        raise SystemExit(
            f"REFUSING field request: settlement AND all eligible north frontier "
            f"union tiles cost {cost / 1e6:.1f} MB against {limit / 1e6:.1f} MB. "
            f"The {len(coarse['candidate_order'])} ordered 32m shore candidates "
            "have NOT been capped or silently omitted; raise --budget-mb.")
    fields = request(ws.request_rows(windows), seed, out,
                     vh_src=vh_src, sandbox=sandbox)
    north.require_search_coverage(coarse, fields)
    return fields, windows


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
    import argparse
    import sys

    ws = _worldseed()
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="op")

    show = sub.add_parser("show", help="summarise a VHPATCH1 field")
    show.add_argument("path")

    # THE FRONT DOOR FOR A FRESH SEED.  Without it "derive the windows and run
    # PatchScan" is a paragraph an operator has to write Python for, and the
    # Python they write is where the next borrowed artefact gets in.
    req = sub.add_parser("request", help="derive this seed's windows and scan them")
    req.add_argument("--grid", required=True,
                     help="VHBIOME4 grid from tools/seedscan/run_scan.sh HEIGHT=1")
    req.add_argument("--locations", required=True,
                     help="location dump from tools/seedscan/run_locscan.sh")
    req.add_argument("--out", required=True)
    req.add_argument("--vh-src", default=str(DEFAULT_VH_SRC))
    req.add_argument("--sandbox", default="/tmp/patchscan/vh")
    req.add_argument("--half-max-m", type=float)
    req.add_argument("--min-area-m2", type=float)
    req.add_argument("--budget-mb", type=float)
    req.add_argument("--north-preset", help="shipped progression preset for north terrain coverage")
    req.add_argument("--north-coverage", choices=("ordered-frontier-union-v1",),
                     default="ordered-frontier-union-v1",
                     help="provenance contract: scan the union of ALL eligible frontier corridors")
    req.add_argument("--declared-windows", action="store_true",
                     help="scan worldseed.ULFSLAND_WINDOWS, the window set "
                          "MEASURED for the default seed (and the one its "
                          "committed corpus was built on) instead of deriving "
                          "windows from the land components; refused for any "
                          "other seed (FIELD_WINDOWS_DEPENDS_ON)")
    ws.add_arguments(req, world_arg=False)

    # `field.py <a field>` was the whole interface before `request` existed and
    # still is: a subcommand that broke every operator's muscle memory would be
    # paid for in exactly the moment somebody is debugging ground.
    argv = sys.argv[1:]
    if not argv or argv[0] not in ("show", "request"):
        argv = ["show", *argv]
    args = ap.parse_args(argv)

    if args.op == "request":
        seed, _ = ws.apply(args)
        if args.declared_windows:
            if seed != ws.DEFAULT_SEED:
                raise SystemExit(
                    f"REFUSING --declared-windows for seed {seed!r}: "
                    f"ULFSLAND_WINDOWS are geometries measured for "
                    f"{ws.DEFAULT_SEED!r} and are ground nobody measured on "
                    f"any other seed. Drop the flag to derive this seed's "
                    f"windows from its own land.")
            fields, windows = request_for_seed(
                seed, args.grid, args.out, args.locations, declared_windows=True,
                north_preset=args.north_preset,
                vh_src=Path(args.vh_src), sandbox=args.sandbox,
                budget_bytes=None if args.budget_mb is None
                else int(args.budget_mb * 1024 * 1024))
            for w in windows:
                print(f"{w.fid:24s} ({w.cx:9.1f},{w.cz:9.1f}) half={w.half:6.0f}"
                      f"  {w.role}")
            print(f"{args.out}: {len(fields)} declared base + derived north windows, "
                  f"{Path(args.out).stat().st_size:,} bytes, seed {seed}")
            raise SystemExit(0)
        fields, windows = request_for_seed(
            seed, args.grid, args.out, args.locations,
            north_preset=args.north_preset,
            vh_src=Path(args.vh_src), sandbox=args.sandbox,
            half_max_m=args.half_max_m, min_area_m2=args.min_area_m2,
            budget_bytes=None if args.budget_mb is None
            else int(args.budget_mb * 1024 * 1024))
        for w in windows:
            print(f"{w.fid:24s} ({w.cx:9.1f},{w.cz:9.1f}) half={w.half:6.0f}  {w.role}")
        print(f"{args.out}: {len(fields)} windows, "
              f"{Path(args.out).stat().st_size:,} bytes, seed {seed}")
        raise SystemExit(0)

    for fid, f in load(args.path).items():
        ld = f.land()
        print(f"{fid}: n={f.n} step={f.step} centre=({f.cx},{f.cz}) half={f.half} "
              f"h[{f.h.min():.1f},{f.h.max():.1f}] land={ld.mean():.3f}")
