#!/usr/bin/env python3
"""GENERATE the zones, CENSUS what is standing, ROUTE, then PAVE -- in that
order, and every step refuses to run out of it.

THE DEFECT THIS MODULE EXISTS TO END, MEASURED ON ULFSLAND.

`ribbon.py`'s live path (its STEP 0, at ribbon.py:3951-3973) generates every
zone the FINISHED road crosses.  Generating a zone is the moment a
`More_World_Locations_AIO` structure is CREATED -- so the road's first live act
brings into existence the very bodies it is about to pave through.  Between
that creation and the write, the code asks exactly one question about the zone
(`objects_count id=_ZoneCtrl`, ribbon.py:3980-3992): "is there a controller
here?"  It never asks WHAT STANDS THERE.

The gap from creation to paving is a median of 6 ledger steps and ranges from 1
to 206.  The consequence, MEASURED: 5,318 non-clearable objects in the paved
width plus a 1 m shoulder over 122 bodies; 641 pieces in 48 bodies that block a
walker (a walker-blocking count, not a presence count -- see `blocks_walker`);
and of those 48 bodies, 42 WERE ALREADY STANDING when the road was written.
The router had planned against `poi.load()`, the world GENERATOR's location
dump, which is mod-free by construction and cannot see them.

`poi.load_all()` is now wired into the routing entry points and
`roads/mod_keepout.json` carries 118 forbidding rows -- but that file is a
SURVEY OF ONE WORLD taken after the roads were built.  A future world starts
with an empty one.  A survey file is not a reading of the world that was just
generated, and this module is the difference:

    survey()      generate the band's zones, THEN census them -- per batch,
                  so a zone is never censused before it exists and never
                  paved before it is censused.
    Census        the artefact: cells actually read, bodies found, and the
                  provenance of every number in it.
    bodies_from_objects()
                  clusters raw ZDOs into BODIES with their own measured reach,
                  and decides "blocks a walker" from each prefab's own y
                  extent (invariant under yaw) rather than from presence.
    coverage_gate()
                  THE CHECK THAT WOULD HAVE CAUGHT IT.  A finished centreline
                  is legal only where the census actually read the ground.  Its
                  reference is the census -- a different artefact from the
                  PatchScan field the route was planned against -- so it cannot
                  be satisfied by construction the way the walkability check
                  was.
    freshness_gate()
                  a census taken before a zone was (re)generated is a census of
                  a different world.  Read from the ledger, per zone.

WHY A BAND AND NOT THE WHOLE CORRIDOR.  A census before the route exists has no
centreline to clip to, and a corridor is ~10^6 m2: at 16 m cells and five y
levels that is tens of thousands of socket calls, which is not a census, it is
an outage (`clear.py` documents four server wedges from exactly this).  So the
census covers a BAND about a PROVISIONAL line, the router is FORBIDDEN to leave
that band, and `coverage_gate` proves it did not.  A route that needs more room
than the band has does not quietly take it: `survey` is re-run with a wider
band, or the segment is refused with the numbers.  Cost stays proportional to
the road, and the invariant holds at any band width.

BAND_HALF_M's value is DERIVED, not taste.  16.0 m is one census cell each side
of the line, and it has to contain everything the earthwork and the router can
reach laterally:
  * the written footprint: paved half 3.0 m + `route.footprint_half_m` shoulder
    -> 4.0 m, and the batter reaches 10.5 m (MEASURED, ledger seq 972
    `batter_m`);
  * the largest lateral shift any MEASURED remedy in `mod_keepout.json` asks
    for is 3.93 m (the ten re-route rows run 1.45, 1.92, 2.21, 2.50, 2.65,
    3.13, 3.50, 3.85, 3.92, 3.93 m).
10.5 + 3.93 = 14.43 m, so 16 m covers both with 1.57 m to spare.  It does NOT
cover a detour around the widest body in that file (`hard_m` 36.38 m), and that
is deliberate and checked: `survey` REFUSES to hand back a census whose band is
narrower than a body it found needs, naming the band width to re-run with,
rather than handing the router a keep-out it has no room to honour.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import yaml

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
sys.path.insert(0, str(HERE))
for _p in (str(JUMPSTART / "ledger"), str(JUMPSTART / "terraform"),
           str(JUMPSTART)):
    if _p not in sys.path:
        sys.path.append(_p)

import poi as poimod  # noqa: E402
import worldseed  # noqa: E402

# `clear.py` HOLDS THE PROVEN SOCKET PRIMITIVES AND IS IMPORTED LAZILY.
# `clear` imports `ribbon`, and `ribbon` now imports THIS module for its
# write-end gate, so a module-scope import here is a cycle -- MEASURED, it
# raised `partially initialized module 'clear' has no attribute
# 'CENSUS_CELL_M'` the first time `ribbon.py plan` ran.
#
# The three constants this module needs at import time are therefore written
# out below, and `_clear()` CHECKS them against `clear`'s own values on the
# first live call -- so the duplication cannot drift silently, and it is
# checked on exactly the path that uses it.
_CLEAR = None


def _clear():
    global _CLEAR
    if _CLEAR is None:
        import clear as clearmod  # noqa: PLC0415
        bad = []
        if abs(clearmod.CENSUS_CELL_M - CELL_M) > 1e-9:
            bad.append(f"cell {clearmod.CENSUS_CELL_M} != {CELL_M}")
        if tuple(clearmod.CENSUS_Y_LEVELS) != tuple(Y_LEVELS):
            bad.append(f"y levels {clearmod.CENSUS_Y_LEVELS} != {Y_LEVELS}")
        if clearmod.ENGINE_PREFIX != ENGINE_PREFIX:
            bad.append(f"engine prefix {clearmod.ENGINE_PREFIX!r} != "
                       f"{ENGINE_PREFIX!r}")
        if bad:
            raise SystemExit(
                "precensus.py and clear.py disagree about the census lattice: "
                + "; ".join(bad) + ". They must census the SAME cells or the "
                "coverage gate tests a different population from the one that "
                "was read.")
        _CLEAR = clearmod
    return _CLEAR


# ---------------------------------------------------------------------------
# GEOMETRY.  Checked against `clear.py` by `_clear()`, never diverging quietly.
# ---------------------------------------------------------------------------
CELL_M = 16.0                            # divides the 64 m zone grid exactly
CELL_HALF_M = CELL_M / 2.0
Y_LEVELS = (0.0, -16.0, 16.0, -32.0, 32.0)   # the cube's five stacked boxes
ENGINE_PREFIX = "_"                      # _ZoneCtrl, _TerrainCompiler, ...
ZONE_M = 64.0                            # ZoneSystem zone; zone k spans 64k +/- 32

BAND_HALF_M = 16.0                       # derived in the module docstring
# THE BAND IS ABOUT THE LINE THAT IS WRITTEN, NOT ONLY THE PROVISIONAL ONE.
# The docstring's derivation (batter 10.5 m + a 3.93 m remedy shift inside
# 16 m) holds only while the written line stays within ~4 m of the line the
# band was drawn about -- and nothing held it there: the census-confined
# router may take any read cell.  MEASURED on Fresh42 drive 3 (stage 11,
# 2026-09-26): written lines stood up to 22.6 m (S5) and 24.3 m (S8) off
# their provisional lines, leaving 20 cells within 16 m of five written
# lines unread; four legs the confined router could not serve (S2, S3, S9,
# W9) routed band-free over 22 / 19 / 54 / 89 unread band cells.
#
# A WIDER BAND IS STILL NOT A GROW-AND-RETRY.  `BAND_GROW_M` and
# `BAND_ROUNDS_MAX` were drafted here for a loop that widened the band
# because A* failed, and they stay gone: widening every leg's band from
# 16 m to the 32 m `band_gate` asked for roughly doubles a 6,397 s census
# for the sake of a few legs.  What replaces them is EXACT: the
# routed plan names the cells within BAND_HALF_M of each line it writes, or
# would write, that nobody read (`network.CENSUS_BAND_DEPENDS_ON`,
# `census_wanted`), `survey --wanted` reads those and nothing else, and the
# pipeline re-routes -- bounded, with the cells named when it does not
# converge (`pipeline.CENSUS_LOOP_DEPENDS_ON`).
#
# What one cell costs, MEASURED on Fresh42 drive 3's 18 live parts: 6,235 s
# over 3,625 cell readings (23,429 socket calls), zone generation included.
SURVEY_S_PER_CELL = 1.72

# A body is one connected clump of pieces.  6.0 m is the single-link distance:
# it is larger than the longest single structural prefab in the corpus
# (`wood_beam` 4 m, `stone_wall_4x2` 4 m) so a wall and its neighbouring wall
# join, and smaller than the 8.25 m MEASURED separation between the closest two
# siblings in the build (`stenvik-hut-1` / `cottage-1`) so two buildings do not
# merge into one over-claiming disc.
BODY_LINK_M = 6.0

# THE WALKER, from the game's own code.  `Character::GetSlideAngle` returns 38
# for a player and there is NO step-height constant in Valheim, so on terrain
# the slide angle is the whole limit.  For a PIECE the question is different
# and these two numbers are `mod_keepout.json`'s own, restated here as code:
# a piece whose top is under STEP_OVER_M is stepped over, and one whose bottom
# is over HEAD_ROOM_M is walked under.  Everything between them is climbed or
# gone round, i.e. it blocks.
STEP_OVER_M = 0.5
HEAD_ROOM_M = 2.2

GEOMETRY_PATH = JUMPSTART / "blueprints" / "data" / "piece_geometry.json"
CENSUS_DIR = HERE / "census"


def zone_of(x: float, z: float) -> tuple[int, int]:
    """The zone index containing a point.  Zone k is centred on 64k, so the
    boundary is at 64k +/- 32 -- MEASURED against the ledger's own pairs
    (zones [[8,0]] emitted at pos [512.0, 32.0]; zone [-5,-2] at
    pos [-320.0, -128.0])."""
    return (int(math.floor((x + ZONE_M / 2.0) / ZONE_M)),
            int(math.floor((z + ZONE_M / 2.0) / ZONE_M)))


def cell_of(x: float, z: float) -> tuple[int, int]:
    return (int(math.floor(x / CELL_M)), int(math.floor(z / CELL_M)))


def cell_centre(i: int, j: int) -> tuple[float, float]:
    return (i * CELL_M + CELL_HALF_M, j * CELL_M + CELL_HALF_M)


def cells_for_band(nodes, half_m: float) -> list[tuple[int, int]]:
    """The disjoint cells whose SQUARE comes within `half_m` of the polyline.

    Square-to-segment distance, not centre-to-centre and not per-node: a cell
    whose corner clips the band holds a body that stands in the road, and a
    per-node test with 2 m stations and 16 m cells leaves diagonal gaps on a
    bend.  This is `clear.census_cells`' rule, taken off the STATIONS of a
    provisional line rather than off a built road's carriageway.
    """
    pts = np.asarray(nodes, dtype=np.float64)
    if pts.ndim != 2 or len(pts) == 0:
        return []
    keep: set[tuple[int, int]] = set()
    for k in range(len(pts)):
        ax, az = float(pts[k][0]), float(pts[k][1])
        bx, bz = (float(pts[k + 1][0]), float(pts[k + 1][1])) if k + 1 < len(pts) else (ax, az)
        i0 = int(math.floor((min(ax, bx) - half_m) / CELL_M))
        i1 = int(math.floor((max(ax, bx) + half_m) / CELL_M))
        j0 = int(math.floor((min(az, bz) - half_m) / CELL_M))
        j1 = int(math.floor((max(az, bz) + half_m) / CELL_M))
        for i in range(i0, i1 + 1):
            for j in range(j0, j1 + 1):
                if (i, j) in keep:
                    continue
                cx, cz = cell_centre(i, j)
                if _seg_box_dist(ax, az, bx, bz, cx, cz, CELL_HALF_M) <= half_m:
                    keep.add((i, j))
    return sorted(keep)


def _seg_box_dist(ax, az, bx, bz, cx, cz, h) -> float:
    """Distance from segment AB to the axis-aligned square of half-extent `h`
    about (cx, cz).  Sampled along the segment at a pitch that cannot step over
    the square: h is the coarsest safe pitch, and 0.5 h is used."""
    n = max(1, int(math.ceil(math.hypot(bx - ax, bz - az) / (0.5 * h))))
    best = math.inf
    for t in range(n + 1):
        f = t / n
        px, pz = ax + (bx - ax) * f, az + (bz - az) * f
        dx = max(abs(px - cx) - h, 0.0)
        dz = max(abs(pz - cz) - h, 0.0)
        best = min(best, math.hypot(dx, dz))
        if best == 0.0:
            break
    return best


def zones_for_cells(cells) -> list[tuple[int, int]]:
    """Every zone a censused cell touches.  A 16 m cell lies inside one zone
    because 16 divides 64 and the lattices share their origin offset, but the
    cell is asked at its four corners rather than at its centre so the claim is
    checked rather than assumed."""
    out: set[tuple[int, int]] = set()
    for i, j in cells:
        cx, cz = cell_centre(i, j)
        for dx in (-CELL_HALF_M, CELL_HALF_M - 1e-6):
            for dz in (-CELL_HALF_M, CELL_HALF_M - 1e-6):
                out.add(zone_of(cx + dx, cz + dz))
    return sorted(out)


# ---------------------------------------------------------------------------
# WHAT BLOCKS A WALKER.  Presence is the wrong population and it is the error
# that answered "184 objects left standing" to a question about structures.
# ---------------------------------------------------------------------------
_GEOM: dict[str, list] | None = None


def geometry() -> dict[str, list]:
    global _GEOM
    if _GEOM is None:
        _GEOM = json.loads(GEOMETRY_PATH.read_text())["prefabs"]
    return _GEOM


def y_extent(prefab: str) -> tuple[float, float] | None:
    """The prefab's own SOLID y extent in root-local metres, or None when the
    dump has no solid collider for it.

    Y IS INVARIANT UNDER YAW, which is why this is the one extent a census can
    use without knowing a rotation: `findObjects` prints Euler angles at 0.01
    precision and reconstructing a quaternion from them is lossy, but a yaw
    rotation is about the y axis and leaves min/max y untouched.
    """
    rows = geometry().get(prefab)
    if not rows:
        return None
    los = [r[2] for r in rows if len(r) >= 7]
    his = [r[5] for r in rows if len(r) >= 7]
    if not los:
        return None
    return (min(los), max(his))


def blocks_walker(prefab: str, obj_y: float, ground_y: float) -> tuple[bool, str]:
    """Does this piece, at this height over this ground, stop a walker?

    Returns (blocks, why).  The three outcomes are the ones
    `mod_keepout.json::census_2026_09_20.kinds` records, and applying this
    predicate instead of presence took that census's own count from 1,676
    pieces "in the road" to 641 that block one.
    """
    if _clear().is_clearable(prefab):
        return (False, "clearable vegetation: the clearing pass removes it")
    ext = y_extent(prefab)
    if ext is None:
        # NO SOLID COLLIDER IN THE DUMP IS NOT "HARMLESS" AND NOT "BLOCKING".
        # 1,992 of 4,644 prefabs have no solid collider at all (the dump's own
        # `prefabs_without_solid`), and most of those are effects and markers.
        # A name we cannot measure is reported as unknown and counted as
        # blocking, because the safe direction for a ROUTER is to bend.
        return (True, "no solid collider in piece_geometry.json: unmeasurable, "
                      "treated as blocking so the router bends rather than "
                      "assuming")
    top = obj_y + ext[1] - ground_y
    bot = obj_y + ext[0] - ground_y
    if top <= STEP_OVER_M:
        return (False, f"top {top:+.2f} m over ground: stepped over")
    if bot >= HEAD_ROOM_M:
        return (False, f"bottom {bot:+.2f} m over ground: walked under")
    return (True, f"y extent {bot:+.2f}..{top:+.2f} m over ground: climbed or "
                  f"gone round")


# ---------------------------------------------------------------------------
# THE ARTEFACT
# ---------------------------------------------------------------------------
@dataclass
class Body:
    """One clump of standing pieces, with its own measured reach."""
    name: str
    x: float
    z: float
    y: float
    declared_m: float          # max horizontal reach from the centroid to a piece
    pieces: int
    blocking: int
    prefabs: dict[str, int]
    source: str                # which instrument saw it
    measured_utc: str

    def as_poi(self) -> poimod.Poi:
        # protected=True unconditionally: a furnished body is the one damage
        # class that cannot be rebuilt, so it gets the full keep-out budget and
        # the road bends.  The standing ruling is re-route, re-terminate or
        # accept-and-record -- never delete.
        return poimod.Poi(self.name, self.x, self.z, self.y, self.declared_m,
                          True, 1)

    def to_json(self) -> dict:
        return {"name": self.name, "x": round(self.x, 2), "z": round(self.z, 2),
                "y": round(self.y, 2), "declared_m": round(self.declared_m, 2),
                "pieces": self.pieces, "blocking": self.blocking,
                "prefabs": self.prefabs, "source": self.source,
                "measured_utc": self.measured_utc}

    @staticmethod
    def from_json(d: dict) -> "Body":
        return Body(d["name"], float(d["x"]), float(d["z"]), float(d.get("y") or 0.0),
                    float(d["declared_m"]), int(d.get("pieces") or 0),
                    int(d.get("blocking") or 0), dict(d.get("prefabs") or {}),
                    d.get("source") or "unknown",
                    d.get("measured_utc") or "unknown")


@dataclass
class Census:
    """The ground actually read, the bodies actually found, and the provenance.

    `cells` is the population and it is the half of this artefact that the
    router cannot do without: a body list with no statement of where it looked
    is the mistake `mod_keepout.json` had to carry as prose ("COMPLETE for the
    carriageways of all 25 BUILT segments ... absence from it is not evidence
    of a clear corridor anywhere else").  Here the region is data, and
    `coverage_gate` reads it.
    """
    world: str
    seed: str
    measured_utc: str
    instrument: str
    band_half_m: float
    cells: list[tuple[int, int]]
    bodies: list[Body]
    zones: list[tuple[int, int]] = dc_field(default_factory=list)
    zones_generated_utc: dict[str, str] = dc_field(default_factory=dict)
    failed_reads: list[dict] = dc_field(default_factory=list)
    stats: dict = dc_field(default_factory=dict)
    # WHEN EACH ZONE WAS ACTUALLY READ.  `measured_utc` is ONE stamp and a
    # merged census is read over hours: MEASURED on Fresh42, 20 per-segment
    # parts spanning 17:33:20Z to 19:21Z, merged under the OLDEST stamp
    # (correctly -- the oldest is the only one that cannot overstate
    # currency).  `freshness_gate` then compared a zone GENERATED at 19:21 by
    # the survey's own generate-then-read against a reading time of 17:33
    # belonging to a DIFFERENT SEGMENT, and refused 295 of 295 zones.  ONE
    # SCALAR WAS NAMING THE READING TIME OF 295 ZONES.  This dict is that key
    # at the grain the quantity actually has; `measured_utc` remains the
    # artefact-wide claim and the conservative fallback for a census that
    # carries no per-zone times, because NONE MAY NOT STAND FOR AN ANSWER.
    # LAST in the field order on purpose: every positional `Census(...)` in
    # this tree passes `failed_reads` and `stats` by position.
    zones_censused_utc: dict[str, str] = dc_field(default_factory=dict)

    def __post_init__(self) -> None:
        self._cellset = {tuple(c) for c in self.cells}

    def covers(self, x: float, z: float) -> bool:
        return cell_of(x, z) in self._cellset

    def has_cell(self, cell) -> bool:
        return (int(cell[0]), int(cell[1])) in self._cellset

    def pois(self) -> list[poimod.Poi]:
        return [b.as_poi() for b in self.bodies]

    def widest_hard_m(self, half_width_m: float) -> tuple[float, str]:
        if not self.bodies:
            return (0.0, "")
        r, n = max((p.hard_m(half_width_m), p.name) for p in self.pois())
        return (r, n)

    def to_json(self) -> dict:
        return {
            "schema": 1,
            "world": self.world, "seed": self.seed,
            "measured_utc": self.measured_utc,
            "instrument": self.instrument,
            "band_half_m": self.band_half_m,
            "cell_m": CELL_M, "y_levels": list(Y_LEVELS),
            "cells": [[int(i), int(j)] for i, j in self.cells],
            "zones": [[int(a), int(b)] for a, b in self.zones],
            "zones_generated_utc": self.zones_generated_utc,
            "zones_censused_utc": self.zones_censused_utc,
            "bodies": [b.to_json() for b in self.bodies],
            "failed_reads": self.failed_reads,
            "stats": self.stats,
            "contract": (
                "`cells` IS THE POPULATION. A body absent from `bodies` is "
                "evidence of clear ground ONLY inside `cells`; anywhere else "
                "this artefact says nothing at all, which is what "
                "`coverage_gate` enforces. `zones_generated_utc` and "
                "`zones_censused_utc` are the ordering proof AND THEY ARE "
                "PER ZONE: a zone whose contents were created AFTER this "
                "artefact's reading OF THAT ZONE was censused before it "
                "existed and `freshness_gate` refuses it. `measured_utc` is "
                "the artefact-wide claim -- the OLDEST part of a merge -- and "
                "is the fallback for a zone with no per-zone reading time, "
                "never a substitute for one."),
        }

    @staticmethod
    def from_json(d: dict) -> "Census":
        if int(d.get("schema") or 0) != 1:
            raise SystemExit(f"census schema {d.get('schema')!r} is not 1")
        if abs(float(d.get("cell_m") or 0.0) - CELL_M) > 1e-9:
            raise SystemExit(
                f"census was taken on a {d.get('cell_m')} m cell lattice and "
                f"this build reads {CELL_M} m: the coverage test would compare "
                f"two different populations")
        return Census(
            d["world"], d["seed"], d["measured_utc"], d["instrument"],
            float(d["band_half_m"]),
            [(int(i), int(j)) for i, j in d["cells"]],
            [Body.from_json(b) for b in d["bodies"]],
            [(int(a), int(b)) for a, b in d.get("zones") or []],
            dict(d.get("zones_generated_utc") or {}),
            list(d.get("failed_reads") or []),
            dict(d.get("stats") or {}),
            dict(d.get("zones_censused_utc") or {}))

    def save(self, path: Path) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        blob = json.dumps(self.to_json(), indent=1, sort_keys=False).encode()
        path.write_bytes(blob)
        return hashlib.sha256(blob).hexdigest()

    @staticmethod
    def load(path: Path) -> "Census":
        return Census.from_json(json.loads(Path(path).read_text()))

    @staticmethod
    def merge(parts: list["Census"], *, instrument: str) -> "Census":
        """One census over every segment surveyed, from the per-segment ones.

        THE PRODUCER AND THE READER DISAGREED ABOUT THE KEY, and this is the
        SUPERSET arm of it: `survey`, `from_save` and `placed` all write ONE
        FILE PER SEGMENT (`<world>-<segment>[-save].json`) and the single
        reader -- `require()`, called from `network.py --census` -- takes ONE
        PATH and loads ONE document.  MEASURED 2026-09-23: `roads/census/`
        holds 13 files, every one of them `Ulfsland-<segment>-save.json`, and
        NOT ONE `Ulfsland-census.json`; `roads/segments.yaml` records
        `census_file: None`.  The reader's key names a superset of any one
        producer's -- so the whole-network census that `--census` asks for has
        never existed on any world, and `survey` computed the merged form in a
        local named `merged` and then RETURNED WITHOUT WRITING IT.

        `measured_utc` is the OLDEST of the parts, never the newest:
        `freshness_gate` refuses a cell whose zone was generated after the
        census, so the oldest reading is the only one that cannot overstate
        how current the artefact is.  THAT DECISION IS RIGHT AND IT WAS NOT
        ENOUGH, because one stamp cannot date twenty readings.  MEASURED on
        Fresh42: 20 per-segment parts, 17:33:20Z to 19:21Z, 295 distinct
        zones; the merged artefact claimed 17:33:20Z for all of them and
        `freshness_gate` refused 295 OF 295 -- every zone whose segment was
        surveyed after the first one, refused against ITS OWN survey.  So the
        merge now also carries `zones_censused_utc`, PER ZONE, and it is the
        OLDEST contributing part's reading for exactly the reason above: this
        merge keeps the FIRST part's bodies for a body seen twice, so the
        artefact's knowledge of a shared zone is only as current as the oldest
        part that read it.  A newest-wins per-zone stamp would claim the later
        part's currency for the earlier part's data, which is the same
        overstatement one step down.

        `band_half_m` must agree across parts.  Two bands merged into one
        number is a coverage claim nothing measured -- the wider band would
        certify cells the narrower survey never looked at.
        """
        if not parts:
            raise SystemExit("merge: no per-segment censuses to merge")
        bands = {p.band_half_m for p in parts}
        if len(bands) != 1:
            raise SystemExit(
                f"merge: parts were taken at different band half-widths "
                f"{sorted(bands)}; one number over two coverages would "
                f"certify cells nothing looked at")
        worlds = {p.world for p in parts if p.world}
        seeds = {str(p.seed) for p in parts if p.seed}
        if len(worlds) > 1 or len(seeds) > 1:
            raise SystemExit(
                f"merge: parts span worlds {sorted(worlds)} seeds "
                f"{sorted(seeds)}; a census is a reading of ONE world's ground")
        cells: list[tuple[int, int]] = []
        seen_cells: set[tuple[int, int]] = set()
        zones: list[tuple[int, int]] = []
        seen_zones: set[tuple[int, int]] = set()
        bodies: list[Body] = []
        seen_bodies: set[str] = set()
        gen: dict[str, str] = {}
        read: dict[str, str] = {}
        failed: list[dict] = []
        for p in parts:
            for c in p.cells:
                t = (int(c[0]), int(c[1]))
                if t not in seen_cells:
                    seen_cells.add(t)
                    cells.append(t)
            for z in p.zones:
                t = (int(z[0]), int(z[1]))
                if t not in seen_zones:
                    seen_zones.add(t)
                    zones.append(t)
            for b in p.bodies:
                # A body straddling two segments' bands is ONE body and is
                # censused by both. Keyed on its own name, which
                # `bodies_from_objects` makes unique per prefix and cluster.
                key = b.name
                if key not in seen_bodies:
                    seen_bodies.add(key)
                    bodies.append(b)
            for k, v in p.zones_generated_utc.items():
                # The EARLIEST generation time for a zone two surveys both saw:
                # freshness compares it against measured_utc, and the earlier
                # stamp is the one that cannot hide a zone generated after the
                # reading.
                if k not in gen or v < gen[k]:
                    gen[k] = v
            for z in p.zones:
                # WHEN THIS PART READ THIS ZONE.  A part that carries no
                # per-zone times -- every census written before this key
                # existed, and every `from_save` reading -- is dated by its
                # own `measured_utc`, which is the end of its survey and so
                # is never earlier than the reading it stands for.  UNREAD IS
                # NOT CLEAR, and an absent per-zone time is not an absent
                # constraint: it falls back to a real stamp, never to None.
                k = f"{int(z[0])},{int(z[1])}"
                v = p.zones_censused_utc.get(k) or p.measured_utc
                if v and (k not in read or v < read[k]):
                    read[k] = v
            failed.extend(p.failed_reads)
        return Census(
            next(iter(worlds), ""), next(iter(seeds), ""),
            min(p.measured_utc for p in parts),
            instrument, parts[0].band_half_m, cells, bodies, zones, gen,
            failed,
            {"merged_from": len(parts),
             "band_cells_wanted": sum(int(p.stats.get("band_cells_wanted") or 0)
                                      for p in parts),
             "band_cells_in_an_ungenerated_zone":
                 sum(int(p.stats.get("band_cells_in_an_ungenerated_zone") or 0)
                     for p in parts),
             "bodies_blocking": sum(1 for b in bodies if b.blocking)},
            read)


def bodies_from_objects(objects, ground_of, name_prefix: str,
                        measured_utc: str, source: str,
                        skip_prefabs: tuple[str, ...] = ()) -> list[Body]:
    """Cluster raw ZDOs into bodies and measure each one.

    `ground_of(x, z)` supplies the reference height for the walker predicate.
    It is the GENERATED surface, because a census that happens before the route
    exists has no road surface to compare against -- and that is the
    conservative direction: a piece sitting on ground the road will later cut
    away reads as blocking now.
    """
    rows = [o for o in objects
            if not str(o["prefab"]).startswith(ENGINE_PREFIX)
            and o["prefab"] not in skip_prefabs
            and not _clear().is_clearable(o["prefab"])]
    if not rows:
        return []
    # Single-link clustering by a uniform grid of BODY_LINK_M: two pieces join
    # when they are within the link distance, transitively.  Grid buckets keep
    # it near-linear instead of O(n^2) over the 10,335 objects a real census
    # returns.
    parent = list(range(len(rows)))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    buckets: dict[tuple[int, int], list[int]] = {}
    for k, o in enumerate(rows):
        key = (int(math.floor(o["x"] / BODY_LINK_M)),
               int(math.floor(o["z"] / BODY_LINK_M)))
        buckets.setdefault(key, []).append(k)
    for (bi, bj), ks in buckets.items():
        near = []
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                near += buckets.get((bi + di, bj + dj), [])
        for a in ks:
            for b in near:
                if a >= b:
                    continue
                if math.hypot(rows[a]["x"] - rows[b]["x"],
                              rows[a]["z"] - rows[b]["z"]) <= BODY_LINK_M:
                    union(a, b)
    groups: dict[int, list[int]] = {}
    for k in range(len(rows)):
        groups.setdefault(find(k), []).append(k)

    out: list[Body] = []
    for n, (_root, ks) in enumerate(sorted(groups.items(),
                                           key=lambda kv: -len(kv[1])), 1):
        mem = [rows[k] for k in ks]
        cx = sum(o["x"] for o in mem) / len(mem)
        cz = sum(o["z"] for o in mem) / len(mem)
        cy = sum(o["y"] for o in mem) / len(mem)
        reach = max(math.hypot(o["x"] - cx, o["z"] - cz) for o in mem)
        blocking = 0
        prefabs: dict[str, int] = {}
        for o in mem:
            prefabs[o["prefab"]] = prefabs.get(o["prefab"], 0) + 1
            hit, _why = blocks_walker(o["prefab"], float(o["y"]),
                                      float(ground_of(o["x"], o["z"])))
            blocking += 1 if hit else 0
        out.append(Body(f"{name_prefix}-{n:03d}", cx, cz, cy, reach,
                        len(mem), blocking, prefabs, source, measured_utc))
    return out


# ---------------------------------------------------------------------------
# THE TWO GATES.  Both are stated so they can FAIL, and both are proved to
# fail on the known-bad state in `selftest`.
# ---------------------------------------------------------------------------
def coverage_gate(nodes, census: Census, *, show: int = 6) -> dict:
    """IS EVERY STATION OF THIS CENTRELINE ON GROUND THE CENSUS READ?

    THE POINT OF THIS FUNCTION IS ITS REFERENCE.  Ulfsland's walkability check
    composed its surface from the same PatchScan field the road was planned
    against and therefore could not fail; this one compares the finished line
    against a DIFFERENT artefact -- cells a socket census actually answered for
    -- so an uncensused excursion has nowhere to hide.
    """
    pts = [(float(x), float(z)) for x, z in nodes]
    bad = [p for p in pts if not census.covers(*p)]
    return {
        "verdict": "COVERED" if not bad else "UNCENSUSED",
        "stations": len(pts),
        "stations_uncensused": len(bad),
        "first_uncensused": [[round(x, 1), round(z, 1)] for x, z in bad[:show]],
        "cells_in_census": len(census.cells),
        "band_half_m": census.band_half_m,
        "instrument": census.instrument,
        "measured_utc": census.measured_utc,
        "method": ("every station tested against the CELL SET the census "
                   "actually read, not against the field the route was "
                   "planned on"),
        "tool": "tools/jumpstart/roads/precensus.py::coverage_gate",
    }


# WHAT DATING A ZONE DEPENDS ON, DECLARED.  Each name is a thing
# `ledger_zone_generations` actually reads, and each is neutered in BOTH
# directions by `selftest` section 7: an OFF neuter the REFUSING arm must flip
# under, and a WRONG one -- inverted, swapped, forced -- the ACCEPTING arm must
# flip under.  A one-direction neuter is passed by an index that returned
# NOTHING, which is exactly what an empty generation map does here.
ZONE_GENERATION_DEPENDS_ON = ("record_ts", "record_zone",
                              "op_first_only_per_zone")

# Swapped out by those neuters.  Module-level so a neuter is a rebind and not
# a monkeypatch of a closure.
_GEN_TS_MODE = "real"        # real | off  | future
_GEN_ZONE_MODE = "real"      # real | off  | swapped
_GEN_FIRST_MODE = "declared"  # declared | off | last


def ledger_zone_generations(run_dir: Path) -> dict[tuple[int, int], dict]:
    """When each zone's CONTENTS were created, from the ledger, per zone.

    THE ORDERING FACT LIVES HERE AND NOWHERE ELSE.  A census is a reading of a
    zone's contents and a `zones_generate` is the moment those contents are
    created, so the only way to know a census is still true is to compare the
    two events.  Read from the ledger because the ledger is the only record of
    when a zone was generated.

    THIS FUNCTION USED TO KEEP THE LAST RECORD PER ZONE AND THAT WAS A KEY
    NAMING A SUPERSET.  "The last time the verb was SENT at this zone" is not
    "the last time this zone's contents CHANGED"; the two differ by every
    no-op, and `ledger.schema.OPS["zones_generate"].first_only_per_zone` is
    the measured declaration that every send after the first IS a no-op --
    `ZonesGenerate` targets UNGENERATED zones and nothing in the vocabulary
    returns a zone to that set.

    MEASURED 2026-09-23 on the fresh seed Fresh42, and the blast radius is the
    whole pipeline: `precensus.py survey` runs per segment and each segment's
    band generates every zone it touches, so 20 overlapping bands wrote 568
    `zones_generate` records for 295 distinct zones -- ALL 568 by the survey
    itself, actor CensusRun, seq 1-568.  `Census.merge` then stamps the
    network census with the OLDEST part's reading, correctly, because that is
    the only stamp that cannot overstate currency.  Under the last-record key
    those two correct decisions met and reported 295 OF 295 CENSUSED ZONES
    STALE: the artefact was refused against its own survey's re-asks, stage 12
    step 4 refused on a fresh seed at 3.8 s, and no seed could ever have got
    past it.  The build step that actually followed the census, `road_clear`,
    emitted 814 `objects_clear` records and ZERO generations -- so moving the
    census later in the declared order would have fixed NOTHING.

    THE POPULATION IS STILL `zones_generate` AND ONLY IT.  `zones_restore`
    adds a `_ZoneCtrl` and `terrain_write` moves ground, and both are
    questions a freshness gate does not ask; reading them here would widen
    what the gate means rather than correct what it reads.
    """
    from ledger import schema as _schema                    # noqa: PLC0415
    out: dict[tuple[int, int], dict] = {}
    path = run_dir / "ledger.jsonl"
    if not path.exists():
        return out
    spec = _schema.OPS.get("zones_generate")
    declared = bool(spec is not None and spec.first_only_per_zone)
    first_only = (declared if _GEN_FIRST_MODE == "declared"
                  else False if _GEN_FIRST_MODE == "last" else None)
    if first_only is None:
        # OFF: the declaration is not consulted at all, so no record dates
        # anything.  An empty map is what a neutered instrument returns, and
        # the refusing arm must go RED under it.
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or '"zones_generate"' not in line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("op") != "zones_generate":
            continue
        if _GEN_ZONE_MODE == "off":
            continue
        ts = rec.get("ts") or ""
        if _GEN_TS_MODE == "off":
            ts = ""
        elif _GEN_TS_MODE == "future":
            ts = "2099-01-01T00:00:00Z"
        for z in rec["params"].get("zones") or []:
            key = ((int(z[1]), int(z[0])) if _GEN_ZONE_MODE == "swapped"
                   else (int(z[0]), int(z[1])))
            seq = int(rec.get("seq") or 0)
            prev = out.get(key)
            if prev is None or (seq < prev["seq"] if first_only
                                else seq > prev["seq"]):
                out[key] = {"seq": seq, "ts": ts,
                            "actor": rec.get("actor") or "",
                            "dated_by": ("the FIRST record on this zone, "
                                         "because the op is declared "
                                         "first_only_per_zone"
                                         if first_only else
                                         "the LAST record on this zone")}
    return out


def freshness_gate(census: Census, run_dir: Path, zones=None) -> dict:
    """WAS ANY CENSUSED ZONE (RE)GENERATED AFTER THE CENSUS WAS TAKEN?

    A zone generated after the reading holds objects the reading never saw --
    which is the ordering defect itself, one step removed.  Refuses by naming
    the zones and both timestamps.

    `zones` NAMES THE POPULATION, and passing it is the difference between a
    useful refusal and an over-broad one.  Asked with no argument the question
    is about the whole artefact ("is this census still true anywhere?"), which
    is the right question for a plan-wide report; asked with ONE SEGMENT'S
    zones it is about the ground that segment will actually write.  MEASURED on
    Ulfsland: 232 of the replayed census's 551 zones were regenerated after it
    was taken, so the unscoped answer is STALE for every segment in the network
    -- including the ones whose own zones nobody has touched.  A count without
    its population is how "184 objects left standing" came to answer a question
    about structures, and the same mistake in a gate refuses work that is fine.

    THE COMPARISON IS PER ZONE ON BOTH SIDES, and it was per zone on only one.
    The generation side was already per zone; the reading side was the
    artefact-wide `measured_utc`, which on a merged census is the OLDEST
    part's stamp -- so a zone read at 19:21 was dated 17:33 because another
    segment had been surveyed first, and refused against its own reading.
    `census.zones_censused_utc` is the reading time OF THAT ZONE and is used
    when the artefact carries it; `measured_utc` remains the fallback, which
    is the conservative direction: it is never later than any reading it
    stands for.  This NARROWS nothing -- a zone whose contents were created
    after the artefact read it is still refused, and a census carrying no
    per-zone times behaves exactly as before.
    """
    gens = ledger_zone_generations(run_dir)
    pop = [tuple(z) for z in
           (zones if zones is not None
            else (census.zones or zones_for_cells(census.cells)))]
    per_zone = census.zones_censused_utc or {}
    stale = []
    for z in pop:
        g = gens.get(tuple(z))
        if g is None:
            continue
        read = per_zone.get(f"{int(z[0])},{int(z[1])}") or census.measured_utc
        if g["ts"] and read and g["ts"] > read:
            stale.append({"zone": list(z), "generated_utc": g["ts"],
                          "generated_seq": g["seq"], "actor": g["actor"],
                          "censused_utc": read,
                          "dated_by": g.get("dated_by", "")})
    return {
        "verdict": "FRESH" if not stale else "STALE",
        "population": ("the zones named by the caller" if zones is not None
                       else "every zone in the census"),
        "zones_checked": len(pop),
        "zones_in_ledger": len(gens),
        "zones_with_own_reading_time": sum(
            1 for z in pop if f"{int(z[0])},{int(z[1])}" in per_zone),
        "stale": stale[:12],
        "stale_count": len(stale),
        "method": ("each zone of the named population, the timestamp at which "
                   "its CONTENTS were created (the FIRST zones_generate "
                   "record on that zone -- the op is declared "
                   "first_only_per_zone, so later records changed nothing), "
                   "compared against the time THAT ZONE was read"),
        "tool": "tools/jumpstart/roads/precensus.py::freshness_gate",
    }


# BAND_GATE_DEPENDS_ON: the widest body FOUND, the band SURVEYED, and THE
# CELLS THE CENSUS ACTUALLY READ.  All three.
#
# The third was missing and it is the one that matters on a fresh seed.  The
# verdict was `widest + slack <= band_half`, and `widest_hard_m` is the
# widest body the census FOUND -- which on a census that found nothing is
# 0.00 m, giving `need = 12.31 m`, ROOM, on ground nobody has looked at.
#
# MEASURED 2026-09-23 on the fresh seed Fresh42, `from_save` over the frozen
# unbuilt save: W9-vestvik-lhwest came back "0 bodies, 0 of 460 band cells
# censused, 460 in an UNGENERATED zone" and this gate answered ROOM.  An
# ungenerated zone holds no ZDOs, so its emptiness is AN ABSENCE OF READING
# AND NOT A CLEAR CORRIDOR -- the exact sentence `census_from_save` already
# puts in its own `instrument` line, which this gate then contradicted.
# UNREAD IS NOT CLEAR.
#
# `UNCENSUSED` is the third verdict and it is a REFUSAL TO SPEAK, not a
# finding of obstruction: the remedy is a live `survey`, which generates the
# zones and then reads them, not a wider band.  Nothing changes for a census
# with full coverage -- Ulfsland's replayed keep-out and every live survey
# report exactly the verdict they did before, because for them
# `band_cells_wanted` is absent or equal to the cells read.
BAND_GATE_DEPENDS_ON = ("widest_hard_m", "band_half_m", "cells_read",
                        "cells_wanted")


def band_gate(census: Census, half_width_m: float) -> dict:
    """IS THE BAND WIDE ENOUGH TO HONOUR WHAT THE CENSUS FOUND?

    A keep-out disc wider than the band is a constraint the router has no room
    to satisfy: A* would fail inside the band and the honest answer is a
    re-survey at a stated width, not a route that clips the body.

    And a census that did not READ the band cannot answer the question at
    all.  That is `UNCENSUSED`, and it is not ROOM.
    """
    widest, who = census.widest_hard_m(half_width_m)
    need = widest + CELL_M / math.sqrt(2.0) + 1.0
    read = len(census.cells)
    wanted = int(census.stats.get("band_cells_wanted") or read)
    unread = max(wanted - read, 0)
    if read == 0 or unread:
        verdict = "UNCENSUSED"
    elif need <= census.band_half_m:
        verdict = "ROOM"
    else:
        verdict = "BAND_TOO_NARROW"
    return {
        "verdict": verdict,
        "band_half_m": census.band_half_m,
        "widest_hard_m": round(widest, 2),
        "widest_body": who,
        "band_half_needed_m": round(need, 2),
        "band_cells_read": read,
        "band_cells_wanted": wanted,
        "band_cells_unread": unread,
        "resurvey_band_half_m": (None if need <= census.band_half_m
                                 else float(math.ceil(need / CELL_M) * CELL_M)),
        "method": ("the widest body keep-out the census found, plus the A* "
                   "chord slack, against the band actually surveyed -- and "
                   "refused outright when the census did not read the band, "
                   "because a body nobody looked for is not a body that is "
                   "not there"),
        "remedy": ("" if verdict != "UNCENSUSED" else
                   f"{unread or wanted} of {wanted} band cell(s) were never "
                   f"read. Take a LIVE `precensus.py survey`, which generates "
                   f"each zone and then reads it; a wider band cannot help, "
                   f"and `from_save` on a world whose corridor zones have "
                   f"never been generated can only ever answer this."),
        "tool": "tools/jumpstart/roads/precensus.py::band_gate",
    }


# CENSUS_CONFINE_DEPENDS_ON: WHERE A CONFINED SEARCH'S LINE CAN STAND, not
# where its cell centres stand.
#
# The mask used to open a 4 m corridor cell whenever its CENTRE fell in a
# read 16 m cell, and this docstring claimed that made the constraint and
# `coverage_gate` "the same set".  They are not: A* moves diagonally between
# two open centres, and `network.build_segment` simplifies the staircase at
# 1 m and resamples it at 2 m, so a station can stand `cell_m/sqrt(2) + 1`
# from every centre the search was allowed -- inside a cell nobody read.
# Every OTHER corridor mask already carries exactly that slack (`poi.masks`,
# `network.pad_forbid_mask`, `route.water_standoff_forbid`); this one alone
# did not.  MEASURED 2026-09-26 on Fresh42 drive 3, S5-temple-southcape at
# stage 11: the confined line rode the band's diagonal staircase 22.63 m
# (= 16 sqrt 2) off the provisional line and cut three read-cell corners --
# 6 of 442 stations on unread ground -- and because every remedy candidate
# shared those corners, `plan_refusal` refused the alignment and the
# ceiling as 'uncensused' before the writer was asked about water.  The leg
# that routed in drive 2 was REJECTED; with the slack it routes 914.1 m,
# COVERED, alignment 1 at 0 water steps, on the census already taken.
#
# So a corridor cell is open only when every point within the CALLER'S chord
# slack of its centre lies in a read cell.  The slack is keyword-only with no
# default: it is a property of the caller's line (its cell, its simplify
# tolerance), and a default here would be a second copy of that number.
CENSUS_CONFINE_DEPENDS_ON = ("the_cells_the_census_read",
                             "the_callers_chord_slack")
# Swapped out by the selftest neuters: "declared" | "centre" (the shipped
# test, the cell centre alone) | "band" (WRONG: the slack stretched to the
# band half, which closes a band no line could leave anyway).
_CONFINE_SLACK_MODE = "declared"


def census_forbid(census: "Census", x0: float, z0: float, cell_m: float,
                  m: int, *, chord_slack_m: float
                  ) -> tuple[np.ndarray, dict]:
    """A corridor-grid mask forbidding every cell a line could leave the read
    ground from (CENSUS_CONFINE_DEPENDS_ON).

    THE ROUTER IS CONFINED TO GROUND THAT HAS BEEN READ -- the CELLS
    THEMSELVES, which is the set `coverage_gate` tests, reached through the
    same chord slack the line the gate tests carries.  This is the half that
    stops the excursion happening; the gate is the half that proves it did
    not.

    Vectorised because the corridor grid is 640,000 cells and this runs once
    per corridor: each corridor centre's census cell and its offset inside
    it come from two floor divisions, and a neighbouring census cell is asked
    only where the slack disc about the centre reaches into it (at most the
    3x3 block, because the slack is under half a census cell).
    """
    slack = {"declared": float(chord_slack_m), "centre": 0.0,
             "band": float(census.band_half_m)}[_CONFINE_SLACK_MODE]
    js = x0 + np.arange(m) * cell_m
    is_ = z0 + np.arange(m) * cell_m
    gx, gz = np.meshgrid(js, is_)
    ci = np.floor(gx / CELL_M).astype(np.int64)
    cj = np.floor(gz / CELL_M).astype(np.int64)
    # One integer key per cell.  The world is 10,500 m across, i.e. |index| is
    # under 700, so a 1e6 stride cannot collide.
    have = np.fromiter((i * 1_000_003 + j for i, j in census.cells),
                       dtype=np.int64, count=len(census.cells))
    have.sort()

    def read(i: np.ndarray, j: np.ndarray) -> np.ndarray:
        keys = i * 1_000_003 + j
        pos = np.clip(np.searchsorted(have, keys), 0, max(len(have) - 1, 0))
        return (len(have) > 0) & (have[pos] == keys)

    inside = read(ci, cj)
    open_ = inside.copy()
    if slack > 0.0:
        if slack >= CELL_M:
            # The disc spans more than one census cell each way; ask every
            # cell under its bounding box (the WRONG neuter lives here).
            k = int(math.ceil(slack / CELL_M))
            for di in range(-k, k + 1):
                for dj in range(-k, k + 1):
                    open_ &= read(ci + di, cj + dj)
        else:
            fx, fz = gx - ci * CELL_M, gz - cj * CELL_M
            ex, ez = CELL_M - fx, CELL_M - fz
            for di, dj, near in (
                    (-1, 0, fx <= slack), (1, 0, ex <= slack),
                    (0, -1, fz <= slack), (0, 1, ez <= slack),
                    (-1, -1, np.hypot(fx, fz) <= slack),
                    (1, -1, np.hypot(ex, fz) <= slack),
                    (-1, 1, np.hypot(fx, ez) <= slack),
                    (1, 1, np.hypot(ex, ez) <= slack)):
                open_ &= ~near | read(ci + di, cj + dj)
    forbid = ~open_
    return forbid, {"grid_cells": int(m * m),
                    "cells_uncensused_forbidden": int((~inside).sum()),
                    "cells_within_slack_of_unread_forbidden":
                        int((inside & ~open_).sum()),
                    "cells_censused_open": int(open_.sum()),
                    "census_cells": len(census.cells),
                    "chord_slack_m": round(slack, 3),
                    "depends_on": list(CENSUS_CONFINE_DEPENDS_ON),
                    "method": ("a 4 m corridor cell is open only when every "
                               "point within the caller's chord slack of its "
                               "centre falls in a 16 m cell the census "
                               "actually read")}


# ---------------------------------------------------------------------------
# SOURCES.  Two, and they are labelled differently on purpose.
# ---------------------------------------------------------------------------
def survey(srv, nodes, *, band_half_m: float = BAND_HALF_M,
           ground_of=None, builder=None, name_prefix: str = "census",
           world: str = "", seed: str = "", progress: bool = True,
           cells=None) -> Census:
    """GENERATE THE BAND'S ZONES, THEN READ THEM.  In that order, per batch.

    This is the inversion.  `ribbon.py` generates a zone and then writes
    terrain into it, asking only whether a controller appeared; here a zone is
    generated and then its CONTENTS are read, before any route is committed to
    the ground it covers.

    `builder` is a `LiveBuilder` when the zones still have to be created (the
    real pipeline) and None when they are known to exist already.  The
    per-batch order is kept either way: no cell is read before the zone that
    contains it has been generated in this same call, and the artefact records
    both timestamps so `freshness_gate` can check the claim later.

    `cells`, when given, IS the population and `nodes` is not banded: an
    incremental survey reads exactly the band cells no reusable reading holds
    (INCREMENTAL_DEPENDS_ON), never the whole band again.
    """
    cells = (sorted({(int(c[0]), int(c[1])) for c in cells})
             if cells is not None else cells_for_band(nodes, band_half_m))
    zones = zones_for_cells(cells)
    by_zone: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for c in cells:
        cx, cz = cell_centre(*c)
        by_zone.setdefault(zone_of(cx, cz), []).append(c)
    gen_utc: dict[str, str] = {}
    read_utc: dict[str, str] = {}
    objects: dict[str, dict] = {}
    failed: list[dict] = []
    calls = 0
    if ground_of is None:
        def ground_of(_x, _z):
            return 0.0
    for n, z in enumerate(zones, 1):
        # ---- GENERATE ----
        if builder is not None:
            cx, cz = z[0] * ZONE_M, z[1] * ZONE_M
            builder.emit(
                "zones_generate",
                params={"pos": [cx, cz], "max_m": ZONE_M / 2.0,
                        "zones": [[z[0], z[1]]], "role": "road_census"},
                wire=[f"zones_generate pos={cx:g},{cz:g} max={ZONE_M / 2.0:g}"],
                expect={"zone_ctrl": 1},
                meta={"why": "GENERATE BEFORE CENSUS. Generating a zone is the "
                             "moment a mod location is CREATED, so a census "
                             "taken before it is a census of an empty world "
                             "and a route planned on that census drives "
                             "through the building. ribbon.py's own STEP 0 "
                             "generated these zones AFTER the route was "
                             "planned, which is the ordering defect this "
                             "op exists to invert."})
        gen_utc[f"{z[0]},{z[1]}"] = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                  time.gmtime())
        # ---- THEN CENSUS ----
        for c in by_zone.get(z, []):
            cx, cz = cell_centre(*c)
            gy = float(ground_of(cx, cz))
            for dy in Y_LEVELS:
                got, unlisted, n_calls, _engine = _clear().list_box(
                    srv, cx, gy + dy, cz, CELL_HALF_M)
                calls += n_calls
                objects.update(got)
                for u in unlisted:
                    failed.append(dict(u, cell=[c[0], c[1]]))
        # WHEN THIS ZONE WAS READ, stamped HERE and not inherited from the
        # call's end.  `measured_utc` is one stamp for a survey that runs for
        # minutes and is merged with nineteen others that ran for hours, and
        # a zone dated by another segment's clock is how a census came to be
        # refused against its own generate-then-read.
        read_utc[f"{z[0]},{z[1]}"] = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                   time.gmtime())
        if progress:
            print(f"  zone {n}/{len(zones)} {z} generated then censused: "
                  f"{len(objects)} objects, {calls} socket calls", flush=True)
    measured = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    bodies = bodies_from_objects(
        list(objects.values()), ground_of, name_prefix, measured,
        "MEASURED live: unscoped `findObjects -near cx y cz 8` over the band's "
        "16 m cells at five y levels, after this same call generated the zone")
    return Census(
        world=world, seed=seed, measured_utc=measured,
        instrument=(
            "MEASURED live over the RCON SOCKET: per band cell, one UNSCOPED "
            "`findObjects -near cx y cz 8.00` at each of five y levels, the "
            "'Found n objects' header compared against the rows parsed and the "
            "box subdivided into eight TILING children on disagreement (the "
            "reply truncates silently at ValidatePayloadLength's 4050 bytes). "
            "Deduplicated by ZDO id. Unscoped because a prefab list cannot "
            "name the ~190 More_World_Locations types. Each zone is GENERATED "
            "in this same call immediately before its cells are read."),
        band_half_m=band_half_m, cells=cells, bodies=bodies, zones=zones,
        zones_generated_utc=gen_utc, failed_reads=failed,
        stats={"socket_calls": calls, "objects_seen": len(objects),
               "cells": len(cells), "zones": len(zones),
               "bodies": len(bodies),
               "bodies_blocking": sum(1 for b in bodies if b.blocking)},
        zones_censused_utc=read_utc)


# THE REPLAYED CENSUS'S COVERAGE IS A PROPERTY OF THE FILE, NOT A PARAMETER.
# `mod_keepout.json`'s own instrument line says "1450 of 1450 planned r=8
# boxes" over the live centrelines, so 8.0 m is how far from a centreline it
# actually looked.  It was briefly settable from the CLI (`--band`, default
# 16.0) and that is a provenance defect in exactly the family this ticket is
# about: it would have let a caller DECLARE coverage the census never had, and
# `coverage_gate` would then have passed ground nobody read.
REPLAY_BAND_HALF_M = 8.0


# THE PLAN IS IN TWO FILES.  `north.py` writes the N-series and the R-series
# into `north_segments.yaml` and everything else lives in `segments.yaml`.
# `mod_keepout.json`'s own coverage line claims "the carriageways of ALL 25
# BUILT segments, 21,929 m of live centreline" - so replaying it over ONE of
# the two files reconstructs cells for 15 of those 25 and then reports the
# other ten as UNCENSUSED, which understates the measurement it is replaying
# and sends the router out with no keep-out over the whole northern network.
#
# AND THEY ARE THE CENSUS'S SEED'S FILES, not a tuple of Pirate68's names:
# the replay's population is the network that census MEASURED, which is the
# one its own header declares.  `keepout_scope` reads that declaration and
# refuses a census that makes none.
def keepout_scope(keepout: Path = poimod.MOD_KEEPOUT) -> tuple[str, str]:
    """(seed, world) the replayed census DECLARES; refused when it does not."""
    d = json.loads(Path(keepout).read_text())
    seed, world = str(d.get("seed") or ""), str(d.get("world") or "")
    if not seed or not world:
        raise SystemExit(
            f"REFUSED: {keepout} declares seed={seed!r} world={world!r}; a "
            f"census that does not say which world it measured cannot name "
            f"the network it covered, and an absent declaration is not "
            f"permission.")
    return seed, world


def replay_mod_keepout(segments_yaml=None,
                       keepout: Path = poimod.MOD_KEEPOUT) -> Census:
    """THE ULFSLAND CENSUS, REPLAYED -- and labelled as a replay.

    `mod_keepout.json` is a real measurement: 1,450 boxes over 21,929 m of
    LIVE centreline, 1,943 socket calls, 9 failed reads recorded as failures
    rather than as zeros.  What it is NOT is a reading of the world that was
    just generated -- it was taken after the roads were built, and its
    population is the CARRIAGEWAY, not a routing band.  Both facts are carried
    into the artefact instead of being flattened: `instrument` says replay, and
    `cells` is reconstructed from the built centrelines at the half-width the
    census actually covered, so `coverage_gate` refuses ground it never read.

    `segments_yaml` may be one path or several; the default is every plan
    file of the seed THE CENSUS DECLARES, because the replayed census's own
    population is the network it measured and on Pirate68 that spans both.
    """
    d = json.loads(Path(keepout).read_text())
    band_half_m = REPLAY_BAND_HALF_M
    if segments_yaml is None:
        paths = [p for p in worldseed.segment_files(keepout_scope(keepout)[0])
                 if p.exists()]
    elif isinstance(segments_yaml, (str, Path)):
        paths = [Path(segments_yaml)]
    else:
        paths = [Path(p) for p in segments_yaml]
    cells: set[tuple[int, int]] = set()
    for p in paths:
        doc = yaml.safe_load(p.read_text())
        for s in doc["segments"]:
            cells.update(cells_for_band(s["nodes"], band_half_m))
    bodies = []
    for r in d["structures"]:
        if not r.get("forbid", True):
            continue
        # `census_2026_09_20` is a dict for the 13 rows the re-census
        # measured, a PROSE STRING for the 13 it re-censused and found clear
        # ("no pieces in any built carriageway"), and absent for the other 93.
        # Reading it without that check turned a measurement into a crash, so
        # the type is what selects the number rather than the key's presence.
        rc = r.get("census_2026_09_20")
        blocking = int((rc.get("blocking_in_paved") if isinstance(rc, dict)
                        else None) or r.get("blocking_carriageway") or 0)
        bodies.append(Body(
            r["name"], float(r["x"]), float(r["z"]), float(r.get("y") or 0.0),
            float(r["declared_m"]), int(r.get("pieces") or 0), blocking,
            dict(r.get("prefabs") or {}),
            "REPLAYED from roads/mod_keepout.json",
            d.get("generated") or "2026-09-20"))
    return Census(
        world=d.get("world") or "", seed=str(d.get("seed") or ""),
        measured_utc=str(d.get("generated") or "2026-09-20T00:00:00Z"),
        instrument=(
            "REPLAYED, NOT MEASURED NOW: roads/mod_keepout.json, the "
            "2026-09-19/20 carriageway census -- 1,450 of 1,450 planned r=8 "
            "boxes over 21,929 m of live centreline in 1,943 socket calls, 9 "
            "failed reads recorded as failures. Its population is the BUILT "
            "CARRIAGEWAY, so `cells` is reconstructed at that half-width and "
            "this artefact says nothing about ground outside it."),
        band_half_m=band_half_m, cells=sorted(cells), bodies=bodies,
        zones=zones_for_cells(sorted(cells)),
        stats={"replayed_rows": len(bodies),
               "source_coverage": d.get("coverage") or ""})


# ---------------------------------------------------------------------------
# THE THIRD SOURCE: the SAVE, read whole, offline
# ---------------------------------------------------------------------------
# WHY IT HAD TO EXIST, MEASURED tonight on T1.  `deviate.py` produced a
# REROUTE_OK around `LocationProxy@-198,42` -- 65 stations, detour -5.1 m,
# 11.29 m of clearance where the built line has 0.26 m -- and the route lies
# on ground NO census has read.  `replay` covers 8.0 m either side of the
# BUILT carriageways only (its own coverage line), so `band_gate` answers
# BAND_TOO_NARROW at a 3.0 m half-width and `coverage_gate` answers
# UNCENSUSED on the deviation.  The only remaining path was `--census-waiver`
# -- which is the waiver that put 42 standing bodies under a carriageway.
#
# `survey` is the honest alternative and it needs a LIVE SERVER, the harness
# port, and ~1,900 RCON calls; tonight that port is held by a whole-gate run
# and three agents have already paid for a read at a fingerprint that stopped
# being the world before it finished.
#
# But the SAVE already holds every ZDO, and the gate reads it: one
# `WATERPROOF_ZDOS` pass over `ZDOMan.m_objectsByID`, 488,980 rows, NO
# radius, NO paging and NO 4050-byte reply cap -- the three things
# `mod_keepout.json` records nine FAILED READS against.  So this is a
# STRICTLY BETTER INSTRUMENT for "what stands here", and the only thing it
# cannot do is generate a zone.
#
# WHICH IS EXACTLY WHERE ITS COVERAGE STOPS, AND THAT IS NOT PROSE HERE, IT
# IS THE CELL SET.  A zone that was never generated holds no ZDOs in the
# save, so absence of a body there is absence of a reading, not evidence of
# clear ground -- the same confusion `mod_keepout.json` had to carry as a
# paragraph.  A generated zone carries a `_ZoneCtrl`, so `cells` is built
# from the band cells whose OWN zone has one, and every other cell is
# omitted.  `coverage_gate` then refuses a line that leaves them, which is
# the invariant the whole module is for.
ZONE_CTRL = "_ZoneCtrl"


def read_zdo_census(tsv: Path) -> list[dict]:
    """Rows of the gate's whole-save ZDO census as `{prefab,x,y,z}`.

    The file is `accept/instruments.census`'s own artefact -- one tab-
    separated row per ZDO, `hash name x y z qx qy qz qw` -- and it is read
    here rather than re-derived because it is already the one reader for
    that format.
    """
    out: list[dict] = []
    with Path(tsv).open(encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            f = line.rstrip("\n").split("\t")
            if len(f) < 5:
                continue
            try:
                out.append({"prefab": f[1], "x": float(f[2]),
                            "y": float(f[3]), "z": float(f[4])})
            except ValueError:
                continue
    return out


def census_from_save(tsv: Path, nodes, *, band_half_m: float = BAND_HALF_M,
                     ground_of=None, name_prefix: str = "save",
                     world: str = "", seed: str = "",
                     fingerprint: str = "", rows=None) -> Census:
    """Census a routing band out of a FROZEN SAVE.  No server, no truncation.

    `rows` may be a pre-read census (several bands off one 488k-row file);
    otherwise `tsv` is read.  `fingerprint` is the save's own content hash and
    it travels in `instrument`, because a census is only as current as the
    save it was taken from and the fingerprint is the only thing that says
    which save that was.
    """
    if ground_of is None:
        def ground_of(_x, _z):
            return 0.0
    rows = read_zdo_census(tsv) if rows is None else rows
    want = set(cells_for_band(nodes, band_half_m))
    # WHICH ZONES THE SAVE ACTUALLY HOLDS.  One pass over the whole census,
    # because `_ZoneCtrl` is 1 row in ~500 and a per-cell scan of 488,980
    # rows would be the band's cell count times the file.
    generated: set[tuple[int, int]] = set()
    for r in rows:
        if r["prefab"] == ZONE_CTRL:
            generated.add(zone_of(r["x"], r["z"]))
    cells = sorted(c for c in want
                   if zone_of(*cell_centre(*c)) in generated)
    ungenerated = sorted(want - set(cells))
    cellset = set(cells)
    mine = [r for r in rows
            if cell_of(r["x"], r["z"]) in cellset]
    # MEASURED_UTC IS THE SAVE'S TIME, NOT THIS PROCESS'S, and the difference
    # is the whole of `freshness_gate`.  A frozen copy read an hour later
    # still answers for the world AS IT WAS WHEN IT WAS FROZEN; stamping it
    # `now` would let every zone generated in between pass the ordering check
    # while being invisible to the reading -- which is the ordering defect
    # this module exists to invert, re-introduced by a timestamp.  The frozen
    # world directory beside the census is the moment the copy was taken, so
    # it is preferred; the census file's own mtime is the fallback and is
    # still at or after the freeze.
    src = Path(tsv)
    stamp = src.parent / "world"
    when = (stamp if stamp.exists() else src).stat().st_mtime
    measured = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(when))
    bodies = bodies_from_objects(
        mine, ground_of, name_prefix, measured,
        f"MEASURED OFFLINE from the save {fingerprint[:12] or '(unnamed)'}: "
        f"every ZDO in ZDOMan.m_objectsByID whose xz falls in a censused "
        f"band cell")
    return Census(
        world=world, seed=seed, measured_utc=measured,
        instrument=(
            "MEASURED OFFLINE FROM THE SAVE, not over a socket: the gate's "
            "whole-save ZDO census (run_waterproof.sh WATERPROOF_ZDOS -> "
            "ZDOMan.m_objectsByID, the entire object table in one read, no "
            "radius, no paging, no 4050-byte reply cap), filtered to this "
            "band's 16 m cells. COVERAGE IS THE GENERATED ZONES AND ONLY "
            "THEM: a cell is censused only when its own zone carries a "
            f"{ZONE_CTRL}, because an ungenerated zone holds no ZDOs and its "
            "emptiness is an absence of reading, not a clear corridor. "
            f"save fingerprint {fingerprint or '(unnamed)'}."),
        band_half_m=band_half_m, cells=cells, bodies=bodies,
        zones=zones_for_cells(cells),
        stats={"band_cells_wanted": len(want),
               "band_cells_censused": len(cells),
               "band_cells_in_an_ungenerated_zone": len(ungenerated),
               "first_ungenerated_cell": ungenerated[:6],
               "zones_generated_in_save": len(generated),
               "zdo_rows_read": len(rows),
               "objects_in_band": len(mine),
               "bodies": len(bodies),
               "bodies_blocking": sum(1 for b in bodies if b.blocking),
               "save_fingerprint": fingerprint})


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# THE PLACED-OBJECT POPULATION THE BATTER GATE CLIPS AGAINST, FROM THE SAVE
#
# `ribbon.py` refuses to cut a batter without `--placed-objects`, because the
# batter is the one part of the write that moves ground that has never moved
# and a clip gate with no population cannot refuse anything.  The producer
# that shipped is `roads/clear.py --census` over the RCON socket: ~1.4 rows/s,
# 790 calls and 423 s for ONE segment, and `findObjects -detailed` truncates
# silently near 4050 bytes.  The whole-save ZDO table answers the same
# question once, offline, with no reply cap -- AND IT IS THE ONLY SOURCE A
# FRESH SEED HAS, because a six-day-old socket census of a world that does not
# exist yet is not an option.
#
# WHAT WENT WRONG THE FIRST TIME, and it is the night's recurring defect in a
# third artefact family.  The scratch reader declared in its own method line
# that it filtered "to within 24.0 m of this segment's own centreline".  It
# did not.  It filtered by a bounding box of the NODES plus a 16 m
# CELL-MEMBERSHIP test dilated +/- 2 cells around the cells holding nodes.
# MEASURED on the two northern segments: 1,562 and 671 of its rows lie between
# 40.00 m and 63.69 m from the polyline -- a population reaching 64 m wearing
# a 24 m description, against a batter that reaches 15.5 m.  A NODE-CELL TEST
# SAMPLES WHERE THE ROAD IS PLANNED; A DISTANCE TEST SAMPLES WHERE THE ROAD
# REACHES, and the two disagree by a factor of four.
#
# So this reader states its reach, measures TRUE PERPENDICULAR DISTANCE TO THE
# POLYLINE rather than to the nodes or to a lattice of cells, and -- the part
# that makes it a check rather than a claim -- REFUSES if any object in a
# comparison population that lies INSIDE this reach is missing from it.
# Direction is measured, never assumed from an instrument's nominal radius:
# assuming it is exactly how "the batter gate protects more ground rather than
# less" got written into a docstring and shipped while being false.
def placed_reach_m(seg: dict) -> float:
    """How far a batter can move ground from this segment's centreline.

    DERIVED FROM THE SEGMENT, never a constant: the clip population's reach
    has to be the WRITE's reach, and the write's reach is the carriageway
    half plus the shoulder plus the batter.  A population narrower than this
    lets the batter move ground out from under a standing object; one wider
    costs nothing, which is why the declaration -- not the number -- is what
    this module enforces.
    """
    import ribbon as R  # noqa: PLC0415  (ribbon imports this module's package)
    return float(seg["width_m"]) / 2.0 + R.SHOULDER_M + R.BATTER_MAX_M


def placed_from_save(tsv: Path, seg: dict, *, reach_m: float | None = None,
                     fingerprint: str = "") -> dict:
    """Every non-engine object within the DECLARED reach, read from the save.

    The reach is recorded in the artefact and so is the segment id, because a
    population that does not say how far it looked cannot be compared with one
    that looked further -- which is exactly how two censuses of one corridor
    disagreed by 722 objects while four agents measured four hypotheses -- and
    a population that does not say WHICH segments it covers cannot be asked
    whether it has any.
    """
    nodes = seg["nodes"]
    reach = placed_reach_m(seg) if reach_m is None else float(reach_m)
    rows = [r for r in read_zdo_census(tsv)
            if not str(r["prefab"]).startswith(ENGINE_PREFIX)]
    # ROUND FIRST, THEN MEASURE, so the artefact is measurable from its own
    # contents.  The file stores xz to 3 decimals; measuring the FULL
    # precision and storing the rounded value makes the population unprovable
    # by anyone who only has the file -- MEASURED, two rows straddled the
    # boundary by less than a millimetre and read as holes to the gate that
    # re-derived their distance from the stored values (N0 at exactly
    # 14.500000 m, N0b at 14.499932 m stored against 14.500+ full precision).
    # A population whose own recorded coordinates do not reproduce its own
    # membership is the same defect as a declared reach the code never
    # applied, one decimal place down.
    px = np.round(np.fromiter((r["x"] for r in rows), dtype=np.float64,
                              count=len(rows)), 3)
    pz = np.round(np.fromiter((r["z"] for r in rows), dtype=np.float64,
                              count=len(rows)), 3)
    d = _dist_to_polyline(px, pz, nodes)
    sel = np.nonzero(d <= reach)[0]
    return {
        "segment": seg["id"],
        "reach_m": reach,
        "objects": [[float(px[i]), float(pz[i])] for i in sel],
        "scanned_rows": len(rows),
        "method": (
            f"MEASURED OFFLINE from save {fingerprint[:12] or '(unnamed)'}: "
            f"the gate's whole-save ZDO census (ZDOMan.m_objectsByID in one "
            f"read, no radius, no paging, no 4050-byte reply cap), filtered "
            f"by TRUE PERPENDICULAR DISTANCE to this segment's own polyline "
            f"at a DECLARED reach of {reach:g} m = width/2 + SHOULDER_M + "
            f"BATTER_MAX_M, which is the reach of the write it clips. Engine "
            f"bookkeeping prefixes excluded; everything else kept, including "
            f"vegetation, because a clip gate must err toward more protected "
            f"ground."),
        "instrument": "roads/precensus.py::placed_from_save",
    }


def _dist_to_polyline(px, pz, nodes):
    """Distance from each point to the nearest point ON the polyline.

    Point-to-NODE is not point-to-LINE.  At a 2 m station pitch the difference
    is under a metre, but the defect this module exists to close was a
    description and a population disagreeing, so the population is the one the
    description names.
    """
    a = np.asarray(nodes, dtype=np.float64)
    best = np.full(px.shape, np.inf)
    ax, az = a[:-1, 0], a[:-1, 1]
    ex, ez = a[1:, 0] - ax, a[1:, 1] - az
    L2 = ex * ex + ez * ez
    L2[L2 == 0.0] = 1e-12
    for i in range(len(ax)):
        wx = px - ax[i]
        wz = pz - az[i]
        t = np.clip((wx * ex[i] + wz * ez[i]) / L2[i], 0.0, 1.0)
        dx = wx - t * ex[i]
        dz = wz - t * ez[i]
        np.minimum(best, dx * dx + dz * dz, out=best)
    return np.sqrt(best)


def placed_reach_gate(population: dict, nodes, *, required_m: float,
                      others=None, covers: str | None = None) -> dict:
    """REFUSE a placed-object population whose DECLARED REACH is not its reach.

    THREE WAYS A CLIP POPULATION LIES, and this refuses all three.

    1. IT REACHES FURTHER THAN IT SAYS.  The scratch reader declared 24 m and
       swept a +/- 2-cell dilation of 16 m node cells: MEASURED on the two
       northern segments, 1,562 and 671 of its rows lie between 40.00 m and
       63.69 m from the polyline.  Harmless to a batter, but it is the
       artefact's description disagreeing with its contents, which is the
       defect that cost three agents an hour deciding which of two censuses
       was complete.
    2. IT REACHES LESS THAN IT SAYS -- the dangerous one.  A hole inside the
       declared reach is ground the batter moves with nothing standing guard
       over it.  MEASURED by T1Exec on six southern segments: 180 objects on
       T1 alone (T5 69, T12 31, T13 37, T10 41, S8 121) stand INSIDE the
       14.5 m batter reach today and are absent from the file `ribbon.py`
       would otherwise have been handed.
    3. IT NEVER SAW THE SEGMENT AT ALL.  `/tmp/roadclean/placed_*.json` covers
       S1, S7, S8, S12, T1, T3, T10, T12, T13 and NO N-series segment, and
       nothing in the artefact said so -- the same failure as
       `structures.py::WRITTEN` seeing 15 of 25 roads.  A population that does
       not declare its own scope cannot be asked whether it has any.

    THE COMPARISON IS REACH-AWARE ON PURPOSE.  An object another census saw
    OUTSIDE this population's declared reach is a different question, not a
    hole: the socket sweep reaches 34.07 m against a 14.5 m batter, so a plain
    "must be a superset of the socket file" would refuse a healthy save-sourced
    reader forever -- and on a fresh seed, where from-save is the ONLY
    population there is, that refuses every batter on every future world.
    """
    reach = float(population["reach_m"])
    rows = population["objects"]
    if covers is not None and population.get("segment") != covers:
        return {"verdict": "NOT_COVERED",
                "reach_required_m": round(float(required_m), 2),
                "reach_declared_m": round(reach, 2),
                "reach_measured_m": None,
                "segment": population.get("segment"),
                "segment_asked": covers,
                "objects": len(rows),
                "why": "this artefact does not declare the segment it is "
                       "being asked about; a population that never saw this "
                       "corridor cannot clip anything in it, and one that "
                       "declares nothing cannot be asked",
                "compared": [],
                "tool": "roads/precensus.py::placed_reach_gate"}
    # THE REACH IS MEASURED FROM THE POPULATION, NEVER COPIED FROM THE
    # CONSTANT THE FILTER WAS WRITTEN AROUND.  MEASURED on the handed-over
    # northern files: `BAND_M = 24.0` names a band the code never applies --
    # a node bbox plus a +/-2-cell test at 16 m cells admits ~48 m -- and
    # 4,276 of 7,902 rows (54%) lie beyond the 24 m the artefact claims, out
    # to 63.69 m.  A DECLARED REACH THAT IS NOT THE REACH THE CODE APPLIES IS
    # WORSE THAN NO DECLARATION, because the next reader trusts it.
    over = []
    if rows:
        a = np.asarray(rows, dtype=np.float64)
        d = _dist_to_polyline(a[:, 0], a[:, 1], nodes)
        idx = np.nonzero(d > reach + 1e-6)[0]
        over = [[round(float(a[i, 0]), 1), round(float(a[i, 1]), 1),
                 round(float(d[i]), 2)] for i in idx[:6]]
        n_over = int(idx.size)
        worst_over = float(d[idx].max()) if idx.size else 0.0
        measured = float(d.max())
    else:
        n_over, worst_over, measured = 0, 0.0, 0.0
    # IDENTITY AT THE STORAGE QUANTUM, AND WHY IT IS NOT A TOLERANCE.
    #
    # Both files store xz to 3 decimals, so the smallest difference either can
    # express is 0.001 m.  MEASURED: 140 rows on N0 and 126 on N0b differ
    # between two populations of IDENTICAL SIZE read from the SAME save by
    # EXACTLY ONE MILLIMETRE -- (-538.796, 856.323) against (-538.797,
    # 856.323) -- because one producer rounded with `numpy.round` and the
    # other with the builtin, and they break the half-way case in opposite
    # directions.  An exact 3-decimal key therefore reported 140 holes that
    # are not holes, and a 0.1 m key reported 2 by merging distinct objects:
    # both wrong, in opposite directions, for the same reason.
    #
    # So a row matches if any cell of its 1 mm neighbourhood is present.  That
    # is not a tolerance standing in for a population -- it is the smallest
    # difference the format can represent, the same argument that makes
    # `deviate.PIN_EXACT_M` half a quantum rather than a chosen epsilon.  Two
    # DIFFERENT objects 1 mm apart do not exist in a Valheim save; two
    # ROUNDINGS of one object 1 mm apart happen 140 times per segment.
    Q = 0.001
    mine = {(int(round(float(x) / Q)), int(round(float(z) / Q)))
            for x, z in rows}
    findings = []
    for name, other in (others or {}).items():
        if not other:
            findings.append({"population": name, "rows": 0, "in_reach": 0,
                             "missing": 0, "first_missing": []})
            continue
        b = np.asarray(other, dtype=np.float64)
        db = _dist_to_polyline(b[:, 0], b[:, 1], nodes)
        idx = np.nonzero(db <= reach)[0]
        miss = []
        for i in idx:
            kx = int(round(float(b[i, 0]) / Q))
            kz = int(round(float(b[i, 1]) / Q))
            if not any((kx + a_, kz + c_) in mine
                       for a_ in (-1, 0, 1) for c_ in (-1, 0, 1)):
                miss.append([round(float(b[i, 0]), 3), round(float(b[i, 1]), 3)])
        inr = idx
        findings.append({"population": name, "rows": len(other),
                         "in_reach": len(inr), "missing": len(miss),
                         "first_missing": miss[:6]})
    holes = sum(f["missing"] for f in findings)
    # TWO NUMBERS, NOT ONE, and they refuse in OPPOSITE DIRECTIONS.
    #
    #   required  the CONTRACT: how far the batter can move ground from this
    #             centreline.  Derived from the segment, never a constant.
    #   declared  what the artefact says it looked at.
    #   measured  the FACT: the furthest row actually present.
    #
    # SHORT_REACH -- declared below required -- is the dangerous one: it is
    # ground the batter moves with nothing standing guard over it.  MISDECLARED
    # -- measured beyond declared -- is the one that cost this project an hour,
    # because the next reader trusts the declaration: MEASURED on the
    # handed-over northern files, `BAND_M = 24.0` against rows out to 63.69 m.
    # A declaration derived from the code's INTENT is a docstring; one derived
    # from the OUTPUT is a measurement, and only the second can be checked.
    verdict = ("SHORT_REACH" if reach + 1e-6 < required_m else
               "MISDECLARED" if n_over else
               "HOLE" if holes else
               "REACH_HONEST")
    return {
        "verdict": verdict,
        "reach_required_m": round(float(required_m), 2),
        "reach_declared_m": round(reach, 2),
        "reach_measured_m": round(measured, 2),
        "segment": population.get("segment"),
        "objects": len(mine),
        "beyond_declared_reach": n_over,
        "worst_beyond_m": round(worst_over, 2),
        "first_beyond": over,
        "compared": findings,
        # SINGLE-POPULATION BY DESIGN.  `others` is OPTIONAL and the verdict
        # never depends on its presence: the coverage and the reach
        # declaration are both decided against this artefact alone, so the
        # gate runs unchanged on a fresh seed, where the save is the only
        # population that exists and there is no socket census to compare
        # with.  A guard that needs a second artefact to fire is a guard that
        # passes vacuously on every world that does not have one, and a
        # vacuous pass reads exactly like a real one.
        "method": ("every row must lie within the DECLARED reach and the "
                   "artefact must name the segment it covers; where a second "
                   "census is supplied, every row it saw INSIDE that reach "
                   "must be present -- rows it saw beyond the reach are a "
                   "different question and are not counted"),
        "tool": "roads/precensus.py::placed_reach_gate",
    }


# THE REMEDY, ONCE.  Two refusal arms quoted it and the copy in `require` gave
# the file a name -- `roads/census/<world>.json` -- that NO PRODUCER HAS EVER
# WRITTEN. A remedy naming a path the tree does not produce is a remedy that
# cannot be followed, which is how the whole-network census went missing on
# every world for the life of this tree.
_CENSUS_REMEDY = (
    "  1. network.py plan --provisional --out DIR   (a line to survey along; "
    "it cannot be paved)\n"
    "  2. precensus.py survey --plan DIR/segments.yaml --field "
    "<Seed>_1m.bin --actor YOU\n"
    "     (that writes one <world>-<segment>.json per segment AND the merged "
    "roads/census/<world>-census.json, which is the file step 3 reads)\n"
    "  3. network.py plan --census roads/census/<world>-census.json\n"
    "A generator dump is not a census: it is mod-free by construction and "
    "MEASURED blind to 118 bodies on this world alone.")


def require(path: Path | None, *, world: str, seed: str) -> Census:
    """THE ROUTER'S ENTRY POINT.  No census, no route.

    A missing census is not defaulted to "the world is empty": that default is
    exactly what the mod-free generator dump already was, and it is what put 42
    standing bodies under a carriageway.  The refusal names the command that
    produces one.
    """
    # BOTH ARMS ARE A REFUSAL, and until 2026-09-23 only one of them was.
    # `path is None` got this paragraph; a path that simply DOES NOT EXIST
    # fell through to `Census.load` and came out as a FileNotFoundError
    # traceback -- MEASURED on Fresh42 stage 11, six frames of pathlib and not
    # one word about what to run. A refusal that names no reachable remedy is
    # an invitation to work around it, and a traceback names none at all.
    if path is not None and not Path(path).exists():
        raise SystemExit(
            f"REFUSED: no census at {path}. The file does not exist, which is "
            f"the same state as no census at all -- this world's corridor has "
            f"not been read.\n" + _CENSUS_REMEDY)
    if path is None:
        raise SystemExit(
            "REFUSED: no census. Routing requires a reading of the world the "
            "road will cross, taken AFTER its zones were generated.\n"
            + _CENSUS_REMEDY)
    cen = Census.load(Path(path))
    if world and cen.world and cen.world != world:
        raise SystemExit(
            f"REFUSED: census is for world {cen.world!r} and this plan is for "
            f"{world!r}. A census is a reading of ONE world's ground.")
    if seed and cen.seed and str(cen.seed) != str(seed):
        raise SystemExit(
            f"REFUSED: census seed {cen.seed!r} != plan seed {seed!r}.")
    return cen


def require_field(args) -> str:
    """The 1 m height field THIS run's census reads the ground from.

    A PATH LITERAL IS A KEY NAMING A SUPERSET.  Both census arms loaded
    `/tmp/roads/h1m.bin` -- one path, keyed by nothing, for every world and
    every seed on the host.  `field.py` stamps the seed into the VHPATCH1
    header precisely so a foreign field can be refused, and this reader
    bypassed the whole mechanism by never being told which file it wanted: a
    census of THIS world's bodies measured against ANOTHER world's ground is
    the defect `roads/network.py` records at its own `--field` (59% of a
    Fresh42 plan fitted to Ulfsland's buildings), one module over.

    `--field` is REQUIRED rather than defaulted: the old literal had a default
    that was silently wrong, and replacing it with a different default that is
    silently wrong less often is not the fix.
    """
    if not getattr(args, "field", None):
        raise SystemExit(
            "--field is required: the census measures each body against the "
            "GENERATED ground under it, and the ground is this seed's 1 m "
            "field. Produce it with `roads/field.py request --seed <Seed>` "
            "(pipeline.py stage 4 writes <work>/<Seed>_1m.bin) and pass that "
            "path. It used to default to the literal /tmp/roads/h1m.bin, "
            "which is one file for every world on this host.")
    path = Path(args.field)
    if not path.exists():
        raise SystemExit(f"--field {path} does not exist")
    return str(path)


NETWORK_CENSUS_SUFFIX = "-census.json"


def network_census_path(out_dir: Path, world: str) -> Path:
    """THE ONE NAME `network.py --census` ASKS FOR.

    `require()`'s own refusal text names `roads/census/<world>.json` and
    `pipeline.py` declares `roads/census/<World>-census.json`; the producers
    wrote `<world>-<segment>[-save].json`.  Three spellings, no overlap, so
    the reader's key has never named anything a producer wrote.  This function
    is the single home for that name and both producers call it.
    """
    return out_dir / f"{world}{NETWORK_CENSUS_SUFFIX}"


def write_network_census(parts: list["Census"], out_dir: Path, world: str,
                         instrument: str) -> Path | None:
    if not parts:
        return None
    cen = Census.merge(parts, instrument=instrument)
    path = network_census_path(out_dir, world)
    sha = cen.save(path)
    print(f"{path}  sha256 {sha}")
    print(f"  WHOLE NETWORK: {len(parts)} segment census(es) merged -- "
          f"{len(cen.bodies)} bodies ({cen.stats['bodies_blocking']} "
          f"blocking), {len(cen.cells)} cells, {len(cen.zones)} zones, band "
          f"half {cen.band_half_m} m, oldest reading {cen.measured_utc}")
    return path

# ---------------------------------------------------------------------------
# WHERE THE CENSUS READS, AND WHAT IT MAY REUSE.
#
# THE LINES.  A provisional plan's `segments` are the lines stage 9 kept; its
# `census_lines` are lines stage 9's search FOUND and then refused (a drowned
# footprint, a write-gate breach, a self-adjacency it could not join).  Stage
# 11 routes every one of those legs again, against the census, and may route
# them differently -- so ground nobody read under a line the router found is
# a leg stage 11 can never serve.  MEASURED 2026-09-26 on Fresh42 drive 3:
# S13-stathub-dock was refused at stage 9 (a footprint 1.20 m under the plane
# at its dock terminal) with its 217-node line in hand, the census never read
# its 181 band cells, and stage 11 answered "no allowed cell within 200.0 m
# of (502.5, 1062.5)" -- and blamed a network-wide BAND_TOO_NARROW for a body
# on S3.
#
# THE WANTED CELLS.  `network.py plan --census` names, in the plan it emits
# (`census_wanted`, network.CENSUS_BAND_DEPENDS_ON), every cell within the
# band of a line it will WRITE or would write if the ground were read, that
# the census has not read.  `survey --wanted <that plan>` reads exactly them.
#
# INCREMENTAL_DEPENDS_ON: a reading already on disk is REUSED -- not read
# again -- only when it is a reading of THIS world's zones as they now stand:
#   same_world_and_seed      it was taken of this world;
#   same_band_half           one band half over every part (`Census.merge`);
#   zone_generated_in_this_ledger
#                            every zone it read has a `zones_generate`
#                            record in this world's CURRENT ledger -- a world
#                            restored bare starts a new ledger, and a
#                            reading of the old instance is not of this one;
#   read_after_generated     `freshness_gate` FRESH over every zone.
# Anything else re-reads the whole plan, exactly as before.  And an
# incremental survey (`--only`, `--wanted`) that finds NO reusable reading
# REFUSES: the network census it would write holds only what it read, and
# that is how `survey --only <leg> --band 32` -- the re-survey `band_gate`
# advised as "re-survey at 32.0 m" -- would have REPLACED the whole network's
# 2,173 cells with one leg's (and `merge` refuses two band halves anyway).
# ---------------------------------------------------------------------------
INCREMENTAL_DEPENDS_ON = ("same_world_and_seed", "same_band_half",
                          "zone_generated_in_this_ledger",
                          "read_after_generated")
# Swapped out by the selftest neuters: "declared" | "off" (reuse nothing: the
# shipped survey, every band read again) | "blind" (WRONG: reuse any census
# on disk, of whatever world instance).
_REUSE_MODE = "declared"

# A REUSED CENSUS IS RE-VALIDATED, SO IT IS RE-STAMPED.  When every cell a
# plan wants was already read, the census IS the reading of this plan -- but
# the shipped `survey` returned without writing, so the artefact kept the
# mtime of the reading it was reused from, and the pipeline's freshness rule
# (output older than the input it derives from = STALE) refused stage 11 on a
# census that had just been checked against the new plan.  MEASURED on
# Fresh42 drive 4: `--from 9` re-ran the provisional route, stage 10 reused
# every cell in 16.4 s, and stage 11 refused rc 3 ("Fresh42-census.json is
# OLDER than ... segments_provisional").  The neuter `off` is that early
# return.
RESTAMP_DEPENDS_ON = ("every_wanted_cell_already_read",
                      "the_plan_it_was_checked_against")
_RESTAMP_MODE = "restamp"   # restamp | off (the shipped early return)


def restamp_reused(base: "Census", out_dir: Path, world: str,
                   plan: Path) -> Path | None:
    """Re-write a reused network census, unchanged in its cells, with the
    plan it was just checked against in its instrument line."""
    if _RESTAMP_MODE == "off":
        return None
    return write_network_census(
        [base], out_dir, world,
        f"roads/precensus.py::survey -- RE-VALIDATED against {plan.name}: "
        f"every cell the plan wants was already read; {len(base.cells)} "
        f"cell(s) carried, 0 read (RESTAMP_DEPENDS_ON)")


def band_lines(doc: dict) -> list[dict]:
    """Every line a census of this plan reads about: the kept `segments` and
    the `census_lines` its search found and refused."""
    return [s for s in list(doc.get("segments") or [])
            + list(doc.get("census_lines") or []) if s.get("nodes")]


def reusable_census(existing: "Census | None", run_dir: Path, *, world: str,
                    seed: str, band_half_m: float) -> tuple["Census | None", str]:
    """`(the reading an incremental survey may keep, why)`; None re-reads
    everything (INCREMENTAL_DEPENDS_ON)."""
    if existing is None:
        return None, "no network census on disk"
    if _REUSE_MODE == "off":
        return None, "NEUTER reuse=off: nothing already read is reused"
    if _REUSE_MODE == "blind":
        return existing, "NEUTER reuse=blind: reused without asking"
    if existing.world != world or str(existing.seed) != str(seed):
        return None, (f"the census on disk is of world {existing.world!r} "
                      f"seed {existing.seed!r}, not {world!r} / {seed!r}")
    if float(existing.band_half_m) != float(band_half_m):
        return None, (f"the census on disk was read at band half "
                      f"{existing.band_half_m} m, this survey reads "
                      f"{band_half_m} m")
    gens = ledger_zone_generations(run_dir)
    zones = [tuple(z) for z in (existing.zones
                                or zones_for_cells(existing.cells))]
    unled = [z for z in zones if z not in gens]
    if unled:
        return None, (f"{len(unled)} of {len(zones)} zones it read have no "
                      f"zones_generate record in {run_dir / 'ledger.jsonl'} "
                      f"(first {list(unled[0])}): a reading of another world "
                      f"instance, not of this one")
    fr = freshness_gate(existing, run_dir)
    if fr["verdict"] != "FRESH":
        return None, (f"STALE: {fr['stale_count']} of {fr['zones_checked']} "
                      f"zones it read were generated after the reading")
    return existing, (f"reused: {len(existing.cells)} cells over {len(zones)} "
                      f"zones, every zone generated in this ledger before "
                      f"it was read")


def incremental_plan(lines: list[dict], wanted: list[dict], base: "Census | None",
                     *, band_half_m: float) -> list[tuple[str, str, list]]:
    """`[(label, patch, cells to read)]`: each line's band, then each wanted
    entry's cells, less every cell `base` holds and every cell an earlier
    entry already reads.  With no `base` each line's band is read whole, as
    before this existed (overlapping bands are each read in full)."""
    have: set[tuple[int, int]] = ({tuple(c) for c in base.cells}
                                  if base is not None else set())
    out: list[tuple[str, str, list]] = []
    for s in lines:
        band = cells_for_band(s["nodes"], band_half_m)
        todo = ([c for c in band if c not in have] if base is not None
                else band)
        have.update(todo)
        out.append((str(s["id"]), str(s.get("patch") or ""), todo))
    for w in wanted or []:
        todo = sorted({(int(c[0]), int(c[1])) for c in w["cells"]} - have)
        have.update(todo)
        out.append((f"wanted:{w['id']}", str(w.get("patch") or ""), todo))
    return out


def read_wanted(path: Path, *, world: str, seed: str) -> list[dict]:
    """`census_wanted.legs` of a routed plan -- `{id, patch, cells}` per leg
    whose line the census has not read -- refused when the plan is another
    world's."""
    doc = yaml.safe_load(Path(path).read_text()) or {}
    if str(doc.get("world")) != str(world) or str(doc.get("seed")) != str(seed):
        raise SystemExit(f"--wanted {path} is a plan of world "
                         f"{doc.get('world')!r} seed {doc.get('seed')!r}, not "
                         f"{world!r} / {seed!r}")
    return [{"id": str(w["id"]), "patch": w.get("patch"),
             "cells": [(int(c[0]), int(c[1])) for c in w.get("cells") or []]}
            for w in (doc.get("census_wanted") or {}).get("legs") or []]


# ---------------------------------------------------------------------------
# ZDO_SOURCE_DEPENDS_ON: THE SAVE.  NOT ON WHETHER THE WORLD HAS BEEN BUILT.
#
# `from_save` used to REFUSE without `--zdos` and its refusal named the only
# producer it knew: "take one with `python3 -m accept --section roads`".
# MEASURED 2026-09-23 on the fresh seed Fresh42, that instruction is a cycle.
# `accept` returns exit 2 -- "Fresh42 HAS NO BUILD LEDGER ... an empty world
# is not an accepted world" -- BEFORE any section runs, and the remedy it
# prints is "build something first", i.e. stage 12, which requires this
# census.  So the unattended half of stage 10 asked for an artefact only the
# build writes and the build is downstream of it: unreachable on every world
# that has never been built, which is every fresh seed.
#
# `accept --zdo-census` now runs ahead of that ledger check because the
# reading depends on the save alone (accept/instruments.ZDO_CENSUS_DEPENDS_ON
# = world_src, world, vh_src, sandbox -- no ledger), and this function is
# `from_save`'s door to it.  A reader that ACQUIRES its own measurement is
# the established shape here: `accept/instruments.census` runs the harness
# when `zdos.tsv` is absent, and the one instrument that did not -- the ZDO
# FIELD census -- refused on any save whose cache nobody had populated by
# hand, which is the same defect one artefact family over.
ZDO_SOURCE_DEPENDS_ON = ("world", "world_src")


def acquire_zdo_census(world: str, *, world_src: str = "",
                       cache: str = "") -> tuple[Path, str]:
    """(zdos.tsv, save fingerprint) for `world`, read if cached, taken if not.

    No ledger, no build, no verdict -- the whole-save ZDO dump is a function
    of the bytes in the save directory.
    """
    sys.path.insert(0, str(JUMPSTART))
    import accept.instruments as AI                        # noqa: PLC0415
    live = Path(world_src) if world_src else Path(AI.world_src_default(world))
    if not live.is_dir():
        raise SystemExit(
            f"no save directory for {world} at {live}: a census of a save "
            f"needs the save. Pass --world-src <dir> or --zdos <tsv>.")
    AI.require_world_save(live, world)
    # A ledger that exists pins which world it is about; a save of the same
    # name on another install is refused.  No ledger yet -> nothing to compare.
    AI.require_save_is_the_ledgers(live, world)
    root = Path(cache or AI.CACHE_DEFAULT) / world
    root.mkdir(parents=True, exist_ok=True)
    fp = AI.save_fingerprint(live)
    frozen = AI.snapshot(live, root / fp / world)
    args = SimpleNamespace(vh_src=AI.VH_SRC_DEFAULT, world_src=str(frozen),
                           world=world, sandbox=AI.SANDBOX_DEFAULT)
    return AI.zdo_census_tsv(root, fp, args), fp


def main() -> int:
    ap = argparse.ArgumentParser(
        description="generate, then census, then route -- the order, enforced")
    ap.add_argument("op", choices=["survey", "merge", "replay", "from_save",
                                   "placed", "cells", "selftest"])
    ap.add_argument("--plan", default=None,
                    help="the PROVISIONAL plan whose lines define the band "
                         "(default: this run's seed's plan, "
                         "worldseed.segments_path())")
    ap.add_argument("--band", type=float, default=BAND_HALF_M)
    ap.add_argument("--out")
    ap.add_argument("--field",
                    help="this seed's 1 m VHPATCH1 height field; REQUIRED for "
                         "survey and from_save. Was the literal "
                         "/tmp/roads/h1m.bin, one file for every world")
    ap.add_argument("--actor", help="required for survey: it writes zones")
    ap.add_argument("--only", action="append")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--zdos",
                    help="for from_save: the gate's whole-save ZDO census "
                         "TSV, e.g. /tmp/accept/<world>/<fp>/zdos.tsv. "
                         "OPTIONAL -- without it this op ACQUIRES its own "
                         "reading from the save (see acquire_zdo_census)")
    ap.add_argument("--world-src", default="",
                    help="for from_save without --zdos: this world's save "
                         "directory; default "
                         "$VALHEIM_ROOT/<World>/config_merged/worlds_local/"
                         "<World>")
    ap.add_argument("--fingerprint", default="",
                    help="for from_save: the save's own content fingerprint, "
                         "recorded in the artefact's instrument line")
    ap.add_argument("--compare", action="append", default=[],
                    help="for placed: another placed-object JSON to check "
                         "this population against WITHIN ITS DECLARED REACH; "
                         "optional, and the verdict never depends on it -- a "
                         "fresh seed has no second census and a guard that "
                         "needs one passes vacuously there")
    ap.add_argument("--wanted",
                    help="for survey and cells: a ROUTED plan "
                         "(`network.py plan --census`) whose `census_wanted` "
                         "names the cells within the band of a line it writes, "
                         "or would write, that the census has not read; they "
                         "are read too (network.CENSUS_BAND_DEPENDS_ON)")
    args = ap.parse_args()

    world, seed = worldseed.world(), worldseed.seed()
    # THE RUN'S SEED'S PLAN, asked now: the default was Pirate68's
    # `segments.yaml` by name, so `precensus.py cells` on VH_SEED=Fresh42
    # banded Ulfsland's 39 segments and said nothing.  MEASURED 2026-09-25.
    args.plan = args.plan or str(worldseed.segments_path())

    if args.op == "cells":
        doc = yaml.safe_load(Path(args.plan).read_text())
        segs = [s for s in band_lines(doc)
                if not args.only or s["id"] in args.only]
        tot_c: set[tuple[int, int]] = set()
        for s in segs:
            c = cells_for_band(s["nodes"], args.band)
            tot_c.update(c)
            print(f"{s['id']:26s} {len(s['nodes']):5d} stations  "
                  f"{len(c):5d} cells  {len(zones_for_cells(c)):4d} zones  "
                  f"{len(c) * len(Y_LEVELS):6d} boxes")
        print(f"TOTAL {len(segs)} segments  {len(tot_c)} cells  "
              f"{len(zones_for_cells(sorted(tot_c)))} zones  "
              f"{len(tot_c) * len(Y_LEVELS)} boxes at band half {args.band} m")
        # WHAT `survey` WOULD ACTUALLY READ, against the census on disk.
        net = network_census_path(Path(args.out or CENSUS_DIR), world)
        base, why = reusable_census(
            Census.load(net) if net.exists() else None,
            worldseed.ledger_path(world).parent, world=world, seed=seed,
            band_half_m=args.band)
        wanted = (read_wanted(Path(args.wanted), world=world, seed=seed)
                  if args.wanted else [])
        todo = incremental_plan(segs, wanted, base, band_half_m=args.band)
        n = sorted({tuple(c) for _l, _p, cs in todo for c in cs})
        reads = sum(len(cs) for _l, _p, cs in todo)
        print(f"SURVEY WOULD READ {len(n)} cell(s) ({reads} reading(s)) in "
              f"{len(zones_for_cells(n))} zone(s)"
              + "".join(f"; {lab} {len(cs)}" for lab, _p, cs in todo if cs)
              + f"  [{why}]  -- about {reads * SURVEY_S_PER_CELL / 60:.0f} "
                f"min at {SURVEY_S_PER_CELL} s per cell reading")
        return 0

    if args.op == "placed":
        if not args.zdos:
            raise SystemExit(
                "--zdos is required: `placed` reads the SAVE. Point it at "
                "the gate's whole-save ZDO census, e.g. "
                "/tmp/accept/<world>/<fp>/zdos.tsv, and name the save with "
                "--fingerprint <fp>.")
        doc = yaml.safe_load(Path(args.plan).read_text())
        segs = [s for s in doc["segments"]
                if not args.only or s["id"] in args.only]
        if not segs:
            raise SystemExit(f"no segments selected in {args.plan}")
        rc = 0
        for s in segs:
            pop = placed_from_save(Path(args.zdos), s,
                                   fingerprint=args.fingerprint)
            others = {}
            for c in args.compare:
                cp = Path(c)
                if not cp.exists():
                    continue
                # KEYED BY FULL PATH, not basename: two placed-object files
                # for one segment routinely share a name in different
                # directories, and a dict keyed on the name silently keeps
                # one of them -- a population quietly dropped by the very
                # tool that exists to refuse quietly dropped populations.
                others[str(cp)] = (json.loads(cp.read_text()) or {}).get(
                    "objects", [])
            g = placed_reach_gate(pop, s["nodes"],
                                  required_m=placed_reach_m(s),
                                  others=others or None, covers=s["id"])
            out = Path(args.out or (CENSUS_DIR / f"{world}-{s['id']}-"
                                    f"placed.json"))
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({**pop, "reach_gate": g}))
            print(f"{s['id']}: {len(pop['objects'])} placed objects; reach "
                  f"required {g['reach_required_m']:g} m, declared "
                  f"{g['reach_declared_m']:g} m, MEASURED "
                  f"{g['reach_measured_m']} m -> {g['verdict']} -> {out}")
            for f in g["compared"]:
                print(f"    vs {f['population']}: {f['rows']} rows, "
                      f"{f['in_reach']} in reach, {f['missing']} missing"
                      + (f" {f['first_missing']}" if f["missing"] else ""))
            if g["verdict"] != "REACH_HONEST":
                print(f"  REFUSING {s['id']}: {g['verdict']}; "
                      f"{g['beyond_declared_reach']} row(s) beyond the "
                      f"declared reach, worst {g['worst_beyond_m']} m "
                      f"{g['first_beyond'][:3]}")
                rc = 5
        return rc

    if args.op == "from_save":
        doc = yaml.safe_load(Path(args.plan).read_text())
        segs = [s for s in doc["segments"]
                if not args.only or s["id"] in args.only]
        if not segs:
            raise SystemExit(f"no segments selected in {args.plan}")
        import field as fieldmod                          # noqa: PLC0415
        fld = fieldmod.load(require_field(args))
        # THE SAVE IS THE ONLY INPUT, SO THE READING IS ACQUIRED, NOT DEMANDED.
        zdos, fp = ((Path(args.zdos), args.fingerprint) if args.zdos
                    else acquire_zdo_census(world, world_src=args.world_src))
        args.zdos, args.fingerprint = str(zdos), fp
        rows = read_zdo_census(zdos)
        print(f"{len(rows)} ZDO rows from {zdos}")
        parts: list[Census] = []
        for s in segs:
            f = worldseed.resolve(fld, s.get("patch"))
            cen = census_from_save(
                Path(args.zdos), s["nodes"], band_half_m=args.band,
                ground_of=f.bilinear, name_prefix=f"save-{s['id']}",
                world=world, seed=seed, fingerprint=args.fingerprint,
                rows=rows)
            out = Path(args.out or CENSUS_DIR) / f"{world}-{s['id']}-save.json"
            print(f"{s['id']}: {out} sha256 {cen.save(out)}")
            print(f"  {len(cen.bodies)} bodies "
                  f"({cen.stats['bodies_blocking']} blocking), "
                  f"{len(cen.cells)} of {cen.stats['band_cells_wanted']} band "
                  f"cells censused, "
                  f"{cen.stats['band_cells_in_an_ungenerated_zone']} in an "
                  f"UNGENERATED zone, {len(cen.zones)} zones, band half "
                  f"{cen.band_half_m} m")
            print(f"  band gate at half-width 3.0 m: "
                  f"{band_gate(cen, 3.0)}")
            parts.append(cen)
        write_network_census(
            parts, Path(args.out or CENSUS_DIR), world,
            "roads/precensus.py::census_from_save, merged over every segment "
            "of the plan -- a reading of the SAVE, not of a live world")
        return 0

    if args.op == "replay":
        if args.band != BAND_HALF_M:
            raise SystemExit(
                f"--band does not apply to `replay`: the replayed census "
                f"looked {REPLAY_BAND_HALF_M:g} m from the centreline "
                f"(mod_keepout.json's own 'r=8 boxes') and declaring any "
                f"other number would claim coverage it does not have.")
        # A REPLAY IS A READING OF THE WORLD THE CENSUS MEASURED; filed under
        # this run's name it would be another seed's census wearing ours.
        k_seed, k_world = keepout_scope()
        worldseed.require_scope(
            "the replayed census", poimod.MOD_KEEPOUT, found_seed=k_seed,
            found_world=k_world,
            produced_by="the 2026-09-19/20 carriageway census "
                        "(roads/mod_keepout.json)")
        cen = replay_mod_keepout(Path(args.plan))
        out = Path(args.out or (CENSUS_DIR / f"{world}-replay.json"))
        sha = cen.save(out)
        print(f"{out}  sha256 {sha}")
        print(f"  {len(cen.bodies)} bodies, {len(cen.cells)} cells, "
              f"{len(cen.zones)} zones, band half {cen.band_half_m} m")
        print(f"  {cen.instrument[:120]}...")
        return 0

    if args.op == "survey":
        if not args.actor:
            raise SystemExit("--actor is required: survey GENERATES zones, "
                             "which is a write, and a write needs an owner")
        doc = yaml.safe_load(Path(args.plan).read_text())
        segs = [s for s in band_lines(doc)
                if not args.only or s["id"] in args.only]
        wanted = (read_wanted(Path(args.wanted), world=world, seed=seed)
                  if args.wanted else [])
        if not segs and not wanted:
            raise SystemExit(f"no segments selected in {args.plan}")
        out_dir = Path(args.out or CENSUS_DIR)
        net = network_census_path(out_dir, world)
        base, why = reusable_census(
            Census.load(net) if net.exists() else None,
            worldseed.ledger_path(world).parent, world=world, seed=seed,
            band_half_m=args.band)
        print(f"prior reading {net}: {why}")
        if base is None and (args.only or wanted):
            # NEVER A SUBSET WEARING THE NETWORK'S NAME -- see
            # INCREMENTAL_DEPENDS_ON.
            raise SystemExit(
                f"REFUSING an incremental survey ({'--only' if args.only else '--wanted'}) "
                f"with no reusable network census ({why}): the census it "
                f"would write under {net.name} would hold only the cells "
                f"this call read. Survey the whole plan.")
        todo = incremental_plan(segs, wanted, base, band_half_m=args.band)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        sys.path.insert(0, str(JUMPSTART / "ledger"))
        from live import LiveBuilder  # noqa: PLC0415
        import field as fieldmod  # noqa: PLC0415
        fld = fieldmod.load(require_field(args))
        parts: list[Census] = []
        with LiveBuilder(actor=args.actor, dry=args.dry) as b:
            for label, patch, cells in todo:
                if not cells:
                    print(f"{label}: every band cell already read -- "
                          f"nothing to read")
                    continue
                # `worldseed.resolve` is the established accessor for a patch
                # window, and it is used rather than indexing the dict because
                # a patch name that is not a window key (an alias, or a leg
                # planned on the trunk field) resolves there and would be a
                # silent fallback to "whatever came first" here.
                f = worldseed.resolve(fld, patch)
                # A READING ADDED TO A REUSED ONE IS NAMED APART FROM IT:
                # `Census.merge` keeps the first body of a name, so a new
                # reading under an old name would be dropped as a duplicate.
                name = (label.replace(":", "-") if base is None
                        else f"{label.replace(':', '-')}+{stamp}")
                cen = survey(b.srv, None, cells=cells, band_half_m=args.band,
                             ground_of=f.bilinear, builder=b,
                             name_prefix=f"census-{name}",
                             world=world, seed=seed)
                out = out_dir / f"{world}-{name}.json"
                print(f"{label}: {len(cells)} cell(s) -> {out} sha256 "
                      f"{cen.save(out)}")
                parts.append(cen)
        if base is not None and not parts:
            print(f"{net}: unchanged cells -- every cell the plan wants was "
                  f"already read; re-stamped against {Path(args.plan).name} "
                  f"(RESTAMP_DEPENDS_ON)")
            if not args.dry:
                restamp_reused(base, out_dir, world, Path(args.plan))
            return 0
        write_network_census(
            ([base] if base is not None else []) + parts, out_dir, world,
            "roads/precensus.py::survey over the RCON socket, merged over "
            "every segment of the plan"
            + (f" -- INCREMENTAL {stamp}: {len(base.cells)} cell(s) reused "
               f"(INCREMENTAL_DEPENDS_ON), "
               f"{sum(len(p.cells) for p in parts)} read"
               if base is not None else ""))
        return 0

    if args.op == "merge":
        # THE NETWORK ARTEFACT WITHOUT THE SURVEY.  `survey` merges at the end
        # of a run that takes hours over twenty segments, so a run that dies
        # on the last segment -- or a merge whose RULE has been corrected
        # since -- leaves twenty valid per-segment readings on disk and no
        # artefact the router can read.  Same `write_network_census`, same
        # `Census.merge`: this op adds no second home for the name, it only
        # supplies the parts from where the survey already wrote them.
        d = Path(args.out or CENSUS_DIR)
        net = network_census_path(d, world)
        # THE PARTS ARE THE LIVE PER-SEGMENT READINGS AND ONLY THOSE. A
        # `-save.json` is `from_save`'s reading of an UNBUILT save and is
        # measured against a different instrument; merging the two would
        # certify save-sourced coverage as live coverage.
        files = sorted(p for p in d.glob(f"{world}-*.json")
                       if p != net and not p.name.endswith("-save.json"))
        if not files:
            raise SystemExit(
                f"merge: no per-segment censuses {world}-<segment>.json under "
                f"{d}. UNREAD IS NOT CLEAR: there is nothing to merge and an "
                f"empty network census would read as a survey of clear ground.")
        parts = [Census.load(p) for p in files]
        write_network_census(
            parts, d, world,
            "roads/precensus.py::survey over the RCON socket, merged over "
            "every segment of the plan (re-merged by `precensus.py merge` "
            f"from {len(files)} per-segment readings already on disk)")
        return 0

    return selftest()


# ---------------------------------------------------------------------------
def selftest() -> int:
    """PROVE THE GATES FAIL ON THE KNOWN-BAD STATE AND PASS ON THE REPAIRED ONE.

    A check shown only to pass is not a check.  Each block below asserts BOTH
    directions and prints the counts it asserted on.
    """
    ok = True
    # THE REPLAYED CENSUS IS THE SEED IT DECLARES, and so is the plan whose
    # carriageway it covered -- whatever seed this selftest is run under.
    seg_yaml = worldseed.segments_path(keepout_scope()[0])
    cen = replay_mod_keepout(seg_yaml)
    doc = yaml.safe_load(seg_yaml.read_text())
    segs = {s["id"]: s for s in doc["segments"]}

    # --- 1. the walker predicate, both directions ---
    cases = [
        ("wood_floor", 0.0, 0.0, False),      # 0.10 m top: stepped over
        ("stone_wall_2x1", 0.5, 0.0, True),   # 0.00..1.00 m: climbed
        ("wood_floor", 3.0, 0.0, False),      # 3.10 m bottom: walked under
        ("Beech1", 0.0, 0.0, False),          # clearable
    ]
    for prefab, oy, gy, want in cases:
        got, why = blocks_walker(prefab, oy, gy)
        flag = "OK " if got == want else "BAD"
        ok &= got == want
        print(f"  {flag} blocks_walker({prefab}, y={oy}) = {got}  [{why}]")

    # --- 2. coverage gate: FAILS off the censused band, PASSES on it ---
    inb = segs[sorted(segs)[0]]
    g_in = coverage_gate(inb["nodes"], cen)
    shifted = [[x + 400.0, z + 400.0] for x, z in inb["nodes"]]
    g_out = coverage_gate(shifted, cen)
    ok &= g_in["verdict"] == "COVERED" and g_out["verdict"] == "UNCENSUSED"
    print(f"  {'OK ' if g_in['verdict'] == 'COVERED' else 'BAD'} coverage on "
          f"the censused line: {g_in['verdict']} "
          f"({g_in['stations_uncensused']}/{g_in['stations']} uncensused)")
    print(f"  {'OK ' if g_out['verdict'] == 'UNCENSUSED' else 'BAD'} coverage "
          f"400 m off it: {g_out['verdict']} "
          f"({g_out['stations_uncensused']}/{g_out['stations']} uncensused)")

    # --- 3. band gate: the replayed 8 m band cannot hold the widest body ---
    bg = band_gate(cen, 3.0)
    ok &= bg["verdict"] == "BAND_TOO_NARROW"
    print(f"  {'OK ' if bg['verdict'] == 'BAND_TOO_NARROW' else 'BAD'} band "
          f"gate at half {bg['band_half_m']} m vs widest keep-out "
          f"{bg['widest_hard_m']} m ({bg['widest_body']}): {bg['verdict']}, "
          f"re-survey at {bg['resurvey_band_half_m']} m")
    wide = Census(cen.world, cen.seed, cen.measured_utc, cen.instrument,
                  64.0, cen.cells, cen.bodies, cen.zones)
    bg2 = band_gate(wide, 3.0)
    ok &= bg2["verdict"] == "ROOM"
    print(f"  {'OK ' if bg2['verdict'] == 'ROOM' else 'BAD'} same bodies at "
          f"band half 64 m: {bg2['verdict']} "
          f"(needs {bg2['band_half_needed_m']} m)")

    # --- 4. freshness gate: the replayed census IS stale, by the ledger ---
    run = worldseed.ledger_path(cen.world).parent
    fg = freshness_gate(cen, run)
    fresh = Census(cen.world, cen.seed, "2099-01-01T00:00:00Z", cen.instrument,
                   cen.band_half_m, cen.cells, cen.bodies, cen.zones)
    fg2 = freshness_gate(fresh, run)
    ok &= fg2["verdict"] == "FRESH"
    print(f"  {'OK ' if fg['verdict'] == 'STALE' else '?? '} freshness of the "
          f"2026-09-20 replay against the ledger: {fg['verdict']} "
          f"({fg['stale_count']} of {fg['zones_checked']} censused zones "
          f"generated after it)")
    print(f"  {'OK ' if fg2['verdict'] == 'FRESH' else 'BAD'} same census "
          f"dated 2099: {fg2['verdict']}")

    # --- 5. the placed-object reach gate, ALL FOUR DIRECTIONS ---
    # A guard observed only to say NO is not yet a guard: the accepting arm is
    # the landing condition, because on a fresh seed the save-sourced
    # population is the ONLY one there is and it will be the first thing this
    # ever meets.  The fixture CONSTRUCTS every population it judges -- a
    # straight 200 m line and four hand-placed rows -- rather than borrowing
    # an artefact, so the cases cannot go green because the world moved.
    line = [[float(i), 0.0] for i in range(0, 201, 2)]
    fixture = {"width_m": 6.0}          # required = 3.0 + SHOULDER + BATTER
    req = placed_reach_m(fixture)
    honest = {"segment": "FIX-a-b", "reach_m": req,
              "objects": [[10.0, 0.0], [50.0, req - 0.5], [120.0, -2.0]]}
    short = {"segment": "FIX-a-b", "reach_m": req / 2.0,
             "objects": [[10.0, 0.0]]}
    misdeclared = {"segment": "FIX-a-b", "reach_m": req,
                   "objects": [[10.0, 0.0], [60.0, req + 40.0]]}
    elsewhere = {"segment": "SOME-OTHER-SEG", "reach_m": req,
                 "objects": [[10.0, 0.0]]}
    holed = {"segment": "FIX-a-b", "reach_m": req, "objects": [[10.0, 0.0]]}
    for want, pop, others in (
            ("REACH_HONEST", honest, None),
            ("SHORT_REACH", short, None),
            ("MISDECLARED", misdeclared, None),
            ("NOT_COVERED", elsewhere, None),
            # a second census that saw a standing object INSIDE the declared
            # reach which this population does not hold; the row it saw
            # BEYOND the reach is a different question and must not count.
            ("HOLE", holed, {"socket": [[10.0, 0.0], [70.0, 4.0],
                                        [90.0, req + 25.0]]}),
            ("REACH_HONEST", honest, {"socket": [[50.0, req + 25.0]]})):
        g = placed_reach_gate(pop, line, required_m=req, others=others,
                              covers="FIX-a-b")
        good = g["verdict"] == want
        ok &= good
        print(f"  {'OK ' if good else 'BAD'} reach gate {want:12s} -> "
              f"{g['verdict']:12s} required {g['reach_required_m']:g} m, "
              f"declared {g['reach_declared_m']:g} m, measured "
              f"{g['reach_measured_m']} m"
              + (f", {sum(f['missing'] for f in g['compared'])} missing"
                 if others else ""))

    # --- 6. THE WHOLE-NETWORK MERGE, NEUTERED IN BOTH DIRECTIONS ----------
    # DECLARED DEPENDENCY.  A merged census depends on:
    #   (a) the UNION of every part's cells -- coverage is the half the router
    #       cannot do without, and a merge that keeps one part's coverage
    #       certifies as clear the ground the other parts looked at and the
    #       first one did not;
    #   (b) the OLDEST `measured_utc` -- `freshness_gate` refuses a cell whose
    #       zone was generated after the reading, so a newest-wins merge turns
    #       a STALE survey FRESH by averaging it with a recent one;
    #   (c) a single `band_half_m` -- one number over two coverages claims the
    #       wider band's certification for the narrower survey's cells.
    # Each arm below LOSES exactly one of those and must FLIP; the last arm
    # loses none and must STAND.  The denominator is the real per-segment
    # censuses on disk, which no neuter can reach.
    MERGE_DEPENDS_ON = ("cells: UNION", "measured_utc: OLDEST",
                        "band_half_m: SINGLE")
    saved = sorted(CENSUS_DIR.glob(f"*{'-save.json'}"))
    if len(saved) >= 2:
        parts = [Census.load(p) for p in saved]
        merged = Census.merge(parts, instrument="selftest")
        union = {tuple(c) for p in parts for c in p.cells}
        first = {tuple(c) for c in parts[0].cells}

        # (a) coverage. NEUTER: keep only the first part's cells.
        neutered_cells = len(first)
        good = len(merged.cells) == len(union) and len(union) > neutered_cells
        ok &= good
        print(f"  {'OK ' if good else 'BAD'} merge cells are the UNION: "
              f"{len(merged.cells)} over {len(parts)} parts; a first-part-wins "
              f"merge would carry {neutered_cells} and lose "
              f"{len(union) - neutered_cells}")
        # ...and the lost cells must actually be UNCENSUSED under the neuter,
        # which is the consequence, not the count.
        lost = sorted(union - first)[:1]
        if lost:
            neutered = Census(merged.world, merged.seed, merged.measured_utc,
                              "NEUTERED: first part's cells only",
                              merged.band_half_m, sorted(first), merged.bodies,
                              merged.zones, merged.zones_generated_utc)
            good = merged.covers(lost[0][0] * CELL_M, lost[0][1] * CELL_M) and \
                not neutered.covers(lost[0][0] * CELL_M, lost[0][1] * CELL_M)
            ok &= good
            print(f"  {'OK ' if good else 'BAD'} a cell only the 2nd..nth part "
                  f"saw {lost[0]}: covered by the merge, NOT covered by the "
                  f"first-part-wins neuter")

        # (b) freshness. NEUTER: newest stamp wins.
        oldest = min(p.measured_utc for p in parts)
        newest = max(p.measured_utc for p in parts)
        good = merged.measured_utc == oldest
        ok &= good
        print(f"  {'OK ' if good else 'BAD'} merge takes the OLDEST reading "
              f"{merged.measured_utc}; a newest-wins neuter would stamp "
              f"{newest} and claim {'no' if oldest == newest else 'more'} "
              f"currency than was measured")
        # AND THE CONSEQUENCE, ON A POPULATION BUILT HERE.  This arm used to
        # ask the question of Ulfsland's own 13 save-sourced parts, and that
        # stopped flipping the moment freshness started comparing each zone
        # against its OWN reading time: the corpus merge is now genuinely
        # FRESH under either stamp, so the case had come to depend on the
        # world being stale rather than on the merge rule.  A `from_save` or
        # `replay` part carries NO per-zone reading time -- that is where
        # `measured_utc: OLDEST` is still the whole answer -- so the fixture
        # is two such parts over one zone, generated BETWEEN their stamps.
        led = JUMPSTART / "ledger" / "runs" / str(merged.world)
        one_zone = next(iter(ledger_zone_generations(led).items()), None)
        if one_zone is not None:
            (zx, zz), g = one_zone
            before = "1970-01-01T00:00:00Z"
            after = "2099-01-01T00:00:00Z"
            early = Census("F", "F", before, "FIXTURE: read before it existed",
                           16.0, [], [], [(zx, zz)])
            late = Census("F", "F", after, "FIXTURE: read long after",
                          16.0, [], [], [(zx, zz)])
            m2 = Census.merge([early, late], instrument="selftest")
            real_v = freshness_gate(m2, led)["verdict"]
            neut = Census(m2.world, m2.seed, after,
                          "NEUTERED: newest stamp wins", m2.band_half_m,
                          m2.cells, m2.bodies, m2.zones,
                          m2.zones_generated_utc)
            neut_v = freshness_gate(neut, led)["verdict"]
            good = real_v == "STALE" and neut_v == "FRESH"
            ok &= good
            print(f"  {'OK ' if good else 'BAD'} two parts with NO per-zone "
                  f"times over zone ({zx},{zz}) created {g['ts']}: the "
                  f"oldest-wins merge is {real_v}, the newest-wins neuter is "
                  f"{neut_v}")

        # (c) band. NEUTER: a part at a different band half.
        wide = Census(parts[0].world, parts[0].seed, parts[0].measured_utc,
                      "NEUTERED: different band", parts[0].band_half_m * 2.0,
                      parts[0].cells, parts[0].bodies, parts[0].zones)
        refused = False
        try:
            Census.merge([parts[0], wide], instrument="selftest")
        except SystemExit:
            refused = True
        ok &= refused
        print(f"  {'OK ' if refused else 'BAD'} merge REFUSES two different "
              f"band half-widths ({parts[0].band_half_m} m and "
              f"{wide.band_half_m} m)")

        # THE SURVIVOR, AS A VERDICT.  Losing no dependency must STAND -- and
        # it is reported, never asserted: an assert-shaped survivor that threw
        # would be read as one more flip and stamp a merge that names nothing
        # as proven.
        stands = (len(merged.cells) == len(union)
                  and merged.measured_utc == oldest
                  and merged.band_half_m == parts[0].band_half_m
                  and len(merged.bodies) >= max(len(p.bodies) for p in parts))
        ok &= stands
        print(f"  {'OK ' if stands else 'BAD'} SURVIVOR: the unneutered merge "
              f"stands -- {len(merged.cells)} cells, {len(merged.bodies)} "
              f"bodies, band half {merged.band_half_m} m, stamped "
              f"{merged.measured_utc}  [depends on "
              f"{', '.join(MERGE_DEPENDS_ON)}]")
    else:
        # UNREAD IS NOT CLEAR. Two parts are the minimum a merge can be tested
        # on; fewer is a check that cannot speak, and a check that cannot speak
        # says so rather than passing.
        ok = False
        print(f"  BAD merge neuters need >=2 per-segment censuses under "
              f"{CENSUS_DIR}; found {len(saved)}. A merge cannot be tested on "
              f"one part and silence here would read as a pass")

    # --- 7. DATING A ZONE: THREE KEYS, EACH NEUTERED IN BOTH DIRECTIONS ----
    #
    # THE KNOWN-BAD STATE THESE CASES WERE WRITTEN AGAINST, and it is not a
    # hypothetical: stage 12 step 4 on the fresh seed Fresh42 refused at 3.8 s
    # with "12 of 12 censused zones were generated AFTER this census was
    # taken", and over the whole network the count was 295 OF 295.  Both keys
    # in the comparison named a SUPERSET of what freshness depends on, and
    # MEASURED ON THAT ARTEFACT each one alone accounted for part of it:
    #
    #   LAST zones_generate + artefact-wide measured_utc   295 stale  (before)
    #   FIRST zones_generate + artefact-wide measured_utc  271 stale
    #   LAST zones_generate + per-zone reading time        118 stale
    #   FIRST zones_generate + per-zone reading time         0 stale  (after)
    #
    # so neither correction alone would have let any seed past this gate.
    #
    # The fixtures below are BUILT HERE -- a three-record ledger in a temp
    # directory and two censuses over one zone -- so no neuter can reach the
    # denominator and no world moving can turn a case green.
    ZONE_DATING_DEPENDS_ON = ("record_ts", "record_zone",
                              "op_first_only_per_zone", "per_zone_read_time")
    import tempfile                                          # noqa: PLC0415
    with tempfile.TemporaryDirectory() as _td:
        run = Path(_td)
        def _gen(seq, zone, ts):
            return json.dumps({"seq": seq, "op": "zones_generate", "ts": ts,
                               "actor": "FIXTURE",
                               "params": {"pos": [0, 0], "max_m": 32.0,
                                          "zones": [list(zone)]}})
        # (1,2) created at 09:00 and merely RE-ASKED at 11:30 by a second
        # segment's band; (2,1) is the decoy the swapped-key neuter finds.
        (run / "ledger.jsonl").write_text("\n".join([
            _gen(1, (1, 2), "2026-01-01T09:00:00Z"),
            _gen(2, (2, 1), "2026-01-01T11:00:00Z"),
            _gen(3, (1, 2), "2026-01-01T11:30:00Z")]) + "\n")

        def _cen(read_utc, per_zone):
            return Census("FIX", "FIX", read_utc, "FIXTURE", 16.0,
                          [], [], [(1, 2)], {"1,2": "2026-01-01T09:00:00Z"},
                          [], {},
                          {"1,2": per_zone} if per_zone else {})
        # REFUSING ARM: the zone's contents were created at 09:00, AFTER this
        # census read it at 08:00.  Must be STALE under every honest reading.
        refuse = _cen("2026-01-01T08:00:00Z", "2026-01-01T08:00:00Z")
        # ACCEPTING ARM: read at 10:00, after the 09:00 creation and before
        # the 11:30 re-ask that changed nothing.  Must be FRESH.
        accept = _cen("2026-01-01T07:00:00Z", "2026-01-01T10:00:00Z")

        base_r = freshness_gate(refuse, run)["verdict"]
        base_a = freshness_gate(accept, run)["verdict"]
        good = base_r == "STALE" and base_a == "FRESH"
        ok &= good
        print(f"  {'OK ' if good else 'BAD'} unneutered: a census read BEFORE "
              f"the zone was created is {base_r}; one read after it, whose "
              f"zone was merely re-asked later, is {base_a}")

        # EACH DEPENDENCY, OFF THEN WRONG.  The OFF neuter must flip the
        # REFUSING arm -- an instrument that reads nothing accepts everything
        # -- and the WRONG neuter must flip the ACCEPTING arm, because
        # switching a check off only ever protects the accepting side.
        for name, attr, off, wrong in (
                ("record_ts", "_GEN_TS_MODE", "off", "future"),
                ("record_zone", "_GEN_ZONE_MODE", "off", "swapped"),
                ("op_first_only_per_zone", "_GEN_FIRST_MODE", "off", "last")):
            g = globals()
            keep = g[attr]
            g[attr] = off
            off_r = freshness_gate(refuse, run)["verdict"]
            g[attr] = wrong
            wrong_a = freshness_gate(accept, run)["verdict"]
            g[attr] = keep
            flipped = off_r == "FRESH" and wrong_a == "STALE"
            ok &= flipped
            print(f"  {'OK ' if flipped else 'BAD'} {name}: OFF turns the "
                  f"refusing arm {base_r}->{off_r}, {wrong!r} turns the "
                  f"accepting arm {base_a}->{wrong_a}")

        # (4) THE PER-ZONE READING TIME, which is the census's key and not the
        # ledger's, so its neuter is the artefact and not a mode flag.  OFF =
        # the artefact carries no per-zone time and falls back to
        # `measured_utc`; the ACCEPTING arm must flip, because its
        # artefact-wide stamp is another segment's 07:00.
        blind = _cen("2026-01-01T07:00:00Z", "")
        blind_v = freshness_gate(blind, run)["verdict"]
        # ...and the fallback must still REFUSE where the artefact-wide stamp
        # is itself before the creation: NONE MAY NOT STAND FOR AN ANSWER, so
        # a missing per-zone time weakens nothing.
        blind_r = freshness_gate(_cen("2026-01-01T08:00:00Z", ""),
                                 run)["verdict"]
        good = blind_v == "STALE" and blind_r == "STALE"
        ok &= good
        print(f"  {'OK ' if good else 'BAD'} per_zone_read_time: dropping it "
              f"turns the accepting arm {base_a}->{blind_v} (it falls back to "
              f"the artefact-wide 07:00), and the refusing arm stays "
              f"{blind_r} -- the fallback never accepts what the per-zone key "
              f"would refuse")

        # THE SURVIVOR, AS A VERDICT.  Reported, never asserted: an
        # AssertionError here would be read as one more flip.
        stands = (freshness_gate(refuse, run)["verdict"] == "STALE"
                  and freshness_gate(accept, run)["verdict"] == "FRESH"
                  and freshness_gate(accept, run)["zones_in_ledger"] == 2)
        ok &= stands
        print(f"  {'OK ' if stands else 'BAD'} SURVIVOR: with every key "
              f"restored the two arms stand at STALE/FRESH over a 3-record "
              f"ledger naming 2 zones  [depends on "
              f"{', '.join(ZONE_DATING_DEPENDS_ON)}]")

    # --- 8. REUSE: INCREMENTAL_DEPENDS_ON, NEUTERED IN BOTH DIRECTIONS -----
    #
    # THE KNOWN-BAD STATES.  (1) `survey` re-read every band of the plan on
    # every run: MEASURED on Fresh42 drive 3, 6,397 s for 2,173 cells, and a
    # stage-9 re-run that adds ONE leg (S13, 134 new cells) would pay all of
    # it again.  (2) the re-survey `band_gate` advised -- `survey --only <leg>
    # --band 32` -- wrote a network census holding only that leg's cells.
    # Fixtures BUILT HERE: a ledger in a temp dir, a census over half a line.
    with tempfile.TemporaryDirectory() as _td:
        run, other = Path(_td) / "run", Path(_td) / "other"
        run.mkdir()
        other.mkdir()
        line = {"id": "FIX-a-b", "patch": "", "nodes": [[0.0, 0.0],
                                                        [96.0, 0.0]]}
        half = {"id": "FIX-a-b", "patch": "", "nodes": [[0.0, 0.0],
                                                        [40.0, 0.0]]}
        band = cells_for_band(line["nodes"], BAND_HALF_M)
        read = cells_for_band(half["nodes"], BAND_HALF_M)
        zones = zones_for_cells(band)
        (run / "ledger.jsonl").write_text("\n".join(
            json.dumps({"seq": n, "op": "zones_generate",
                        "ts": "2026-01-01T09:00:00Z", "actor": "FIXTURE",
                        "params": {"pos": [0, 0], "max_m": 32.0,
                                   "zones": [list(z)]}})
            for n, z in enumerate(zones, 1)) + "\n")
        prior = Census("FIX", "FIX", "2026-01-01T10:00:00Z", "FIXTURE",
                       BAND_HALF_M, read, [], zones_for_cells(read), {}, [],
                       {}, {f"{z[0]},{z[1]}": "2026-01-01T10:00:00Z"
                            for z in zones_for_cells(read)})

        def reads(run_dir: Path) -> int:
            base, _why = reusable_census(prior, run_dir, world="FIX",
                                         seed="FIX", band_half_m=BAND_HALF_M)
            return sum(len(c) for _l, _p, c in
                       incremental_plan([line], [], base,
                                        band_half_m=BAND_HALF_M))

        def moded(value, fn):
            global _REUSE_MODE
            keep = _REUSE_MODE
            _REUSE_MODE = value
            try:
                return fn()
            finally:
                _REUSE_MODE = keep
        new = len(band) - len(read)
        got_a, off_a = reads(run), moded("off", lambda: reads(run))
        good = got_a == new and off_a == len(band)
        ok &= good
        print(f"  {'OK ' if good else 'BAD'} a reading of THIS ledger's "
              f"zones is reused: {got_a} of {len(band)} band cells read "
              f"(the {new} nobody read); NEUTER reuse=off (the shipped "
              f"survey) reads {off_a}")
        got_r = reads(other)
        blind_r = moded("blind", lambda: reads(other))
        good = got_r == len(band) and blind_r == new
        ok &= good
        print(f"  {'OK ' if good else 'BAD'} a reading whose zones this "
              f"world's ledger never generated is NOT reused: {got_r} read; "
              f"NEUTER reuse=blind reuses it and reads {blind_r}")
        # (2) NEVER A SUBSET WEARING THE NETWORK'S NAME.
        plan = Path(_td) / "plan.yaml"
        plan.write_text(yaml.safe_dump({"segments": [line]}))
        argv = sys.argv
        sys.argv = ["precensus.py", "survey", "--plan", str(plan), "--only",
                    "FIX-a-b", "--actor", "FIXTURE", "--out", str(other)]
        try:
            main()
            refused = ""
        except SystemExit as exc:
            refused = str(exc)
        finally:
            sys.argv = argv
        good = refused.startswith("REFUSING an incremental survey")
        ok &= good
        print(f"  {'OK ' if good else 'BAD'} `survey --only` with no reusable "
              f"network census REFUSES before any zone is generated: "
              f"{refused[:90]!r}  [depends on "
              f"{', '.join(INCREMENTAL_DEPENDS_ON)}]")
        # (3) A REUSED CENSUS IS RE-STAMPED AGAINST THE PLAN IT WAS CHECKED
        # AGAINST -- the drive-4 `--from 9` shape: census on disk, a newer
        # plan, every wanted cell already read.
        stamped = Path(_td) / "stamped"
        stamped.mkdir()

        def restamped_newer(mode: str) -> tuple[bool, bool]:
            global _RESTAMP_MODE
            keep = _RESTAMP_MODE
            _RESTAMP_MODE = mode
            try:
                net_p = network_census_path(stamped, "FIX")
                prior.save(net_p)
                os.utime(net_p, (1_000_000_000, 1_000_000_000))
                os.utime(plan, (1_000_000_100, 1_000_000_100))
                restamp_reused(prior, stamped, "FIX", plan)
                after = Census.load(net_p)
                return (net_p.stat().st_mtime > plan.stat().st_mtime,
                        sorted(map(tuple, after.cells))
                        == sorted(map(tuple, prior.cells)))
            finally:
                _RESTAMP_MODE = keep
        newer, same = restamped_newer("restamp")
        off_newer, _ = restamped_newer("off")
        good = newer and same and not off_newer
        ok &= good
        print(f"  {'OK ' if good else 'BAD'} a census reused whole against a "
              f"newer plan is re-stamped newer than it, cells unchanged "
              f"({newer}, {same}); NEUTER restamp=off (the shipped early "
              f"return) leaves it older ({off_newer})  [depends on "
              f"{', '.join(RESTAMP_DEPENDS_ON)}]")

    print(f"selftest {'PASSES' if ok else 'FAILS'}: "
          f"{len(cen.bodies)} replayed bodies over {len(cen.cells)} cells")
    return 0 if ok else 4


if __name__ == "__main__":
    sys.exit(main())
