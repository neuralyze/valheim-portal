#!/usr/bin/env python3
"""THE LIVE APPLIED SURFACE: what a player actually stands on, per zone, from
the ledger's own blobs.

WHY THIS EXISTS, AND IT IS THE INSTRUMENT THAT FOUND EVERY DEFECT IN THIS
FAMILY.  Every check this project ran before it read the surface answered a
question it was not asked:

  * `walkability` walks the CENTRELINE of the PLANNED profile.  It cannot see
    a wall beside the road, and it cannot see that the ground under the
    centreline is no longer the ground the plan asked for.
  * `edge_step_census` reads the surface THIS run composes -- its own
    compilers plus generated ground.  It cannot see a FOREIGN write that
    replaced this road's samples afterwards.

MEASURED with this instrument, by `RoadClear`: 89 of T4's 1,443 centreline
metres sit on samples that `SiteFinish` seq 286 (stenvik-beehive-1) overwrote
AFTER `RoadBuild` seq 213.  Sample (586, 954) holds road delta -4.470 while
the live union holds -1.571, so the applied surface jumps 45.05 -> 47.11 IN
ONE METRE, in the middle of the carriageway.  Worst applied-vs-planned
deviation per segment: T4 6.391 m, T13 4.400, T5 3.574, T3 2.113, T12 1.064.

HOW THE LIVE SURFACE IS COMPOSED, and each step is a measured property of the
game rather than a convention:

  1. A zone holds exactly ONE `_TerrainCompiler`
     (`Heightmap::GetAndCreateTerrainCompiler` returns the first it finds), so
     the live state of a zone is ONE compiler, not a stack.
  2. Every write into an already-written zone re-reads the prior blob and
     unions it PER SAMPLE INDEX.  A sample both writes claim goes to the LATER
     writer -- that is what "last writer wins per sample" means and it is what
     the live compiler holds.  So replaying the ledger's `terrain_write`
     records in order, letting later records overwrite per sample, reproduces
     the live compiler.
  3. `TerrainComp::ApplyToHeightmap` adds `m_levelDelta` (plus
     `m_smoothDelta`) to the GENERATED height at each 1 m sample.
  4. `Heightmap` then renders a mesh that interpolates LINEARLY between those
     samples, so the surface under an off-lattice point is the BILINEAR blend
     of the four samples around it and an unwritten sample contributes zero
     delta.

Composing it any other way -- nearest sample, planned profile, this run's own
compilers -- answers a different question.  Every one of those substitutions
has produced a false clear in this project.

READ-ONLY.  This module opens the ledger for reading, decodes blobs and
returns numbers.  It appends nothing and sends nothing.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
sys.path.insert(0, str(JUMPSTART / "ledger"))
sys.path.insert(0, str(JUMPSTART / "terraform"))

import tcdata  # noqa: E402

# A terrain_write with this role is A ROAD.  Anything else that claims a
# sample is somebody else's property, and the ruling on a contested sample is
# that the PAD wins: it is a building's foundation and it must stay flat.
ROAD_ROLE = "road_segment"


class Applied:
    """The live applied surface for as many zones as you ask about.

    Zones are decoded LAZILY and cached: the ledger holds 66 `terrain_write`
    records over ~470 zones and a segment needs 20-45 of them, so decoding the
    world costs ~20x what the question costs.
    """

    def __init__(self, world: str = "Ulfsland", actor: str = "RoadEmit"):
        from writer import Ledger  # noqa: PLC0415
        self.led = Ledger.open(world, actor=actor)
        self.writes: list[dict] = []
        for line, rec in enumerate(self.led.records()):
            if rec.get("op") != "terrain_write":
                continue
            p = rec["params"]
            for e in p["entries"]:
                self.writes.append({
                    "file_line": line, "seq": rec["seq"],
                    "name": p.get("name"), "role": p.get("role"),
                    "actor": rec.get("actor"),
                    "zone": (int(e["zone"][0]), int(e["zone"][1])),
                    "sha": e["blob_sha256"]})
        self._zones: dict[tuple[int, int], dict] = {}

    # -- per zone ---------------------------------------------------------

    def zone(self, zx: int, zz: int) -> dict:
        """`{delta, modified, owner}` for one zone, unioned in FILE ORDER.

        FILE ORDER, not `seq`: the chain forked at file line 47 and carries
        duplicate seq labels 43-46, so `seq` is not a total order on this
        artefact and sorting by it would put two records in an arbitrary
        relative position.  The file is append-only and its line number is the
        only total order it has.
        """
        key = (int(zx), int(zz))
        got = self._zones.get(key)
        if got is not None:
            return got
        n = tcdata.SAMPLES
        delta = np.zeros(n, dtype=np.float64)
        modified = np.zeros(n, dtype=bool)
        owner = np.full(n, -1, dtype=np.int32)
        for w in sorted((w for w in self.writes if w["zone"] == key),
                        key=lambda w: w["file_line"]):
            blob = tcdata.parse(self.led.read_blob(w["sha"]))
            for i, (lvl, sm) in blob["heights"].items():
                delta[i] = lvl + sm
                modified[i] = True
                owner[i] = self.writes.index(w)
        got = {"zone": key, "delta": delta, "modified": modified,
               "owner": owner,
               "writes": [w for w in self.writes if w["zone"] == key]}
        self._zones[key] = got
        return got

    def sample(self, sx: int, sz: int) -> tuple[float, dict | None]:
        """`(delta, owning write)` at an INTEGER lattice sample."""
        zx, zz = tcdata.zone_of(sx, sz)
        zc = self.zone(zx, zz)
        cx, cz = tcdata.zone_centre(zx, zz)
        gx, gy = tcdata.vertex_mask_index(cx, cz, sx, sz)
        if not (0 <= gx < tcdata.PITCH and 0 <= gy < tcdata.PITCH):
            return 0.0, None
        k = gy * tcdata.PITCH + gx
        if not zc["modified"][k]:
            return 0.0, None
        o = int(zc["owner"][k])
        return float(zc["delta"][k]), (self.writes[o] if o >= 0 else None)

    # -- off-lattice, the way Heightmap renders it ------------------------

    def delta_at(self, x: float, z: float) -> float:
        """Bilinear blend of the four surrounding samples' live deltas."""
        x0, z0 = math.floor(x), math.floor(z)
        tx, tz = x - x0, z - z0
        total = 0.0
        for dx, dz, w in ((0, 0, (1 - tx) * (1 - tz)), (1, 0, tx * (1 - tz)),
                          (0, 1, (1 - tx) * tz), (1, 1, tx * tz)):
            if w == 0.0:
                continue
            d, _ = self.sample(x0 + dx, z0 + dz)
            total += w * d
        return total

    def holders(self, sx: int, sz: int) -> list[dict]:
        """EVERY write whose zone lattice holds the sample at `(sx, sz)`.

        `sample()` resolves a sample to ONE zone, `tcdata.zone_of`'s, and for a
        height that is right: the engine renders each zone's own mesh.  For
        OWNERSHIP it is wrong, because a zone's lattice is 65 x 65 over a 64 m
        zone -- the boundary row is SHARED -- so one world sample sits in two
        compilers and `zone_of` picks one of them.

        MEASURED, and this is a hole in the clobber detector that has nothing
        to do with the union: sample (498, 96) is owned by `wt-south` seq 373,
        a site_pad, in zone (8,1); `zone_of` says (8,2), where nobody wrote it,
        so `sample()` answers None and `foreign_at` answers None and a road is
        free to author a watchtower's floor.  At (522, 480) it is worse -- zone
        (8,7) holds it as `tree-sth-2` seq 669's pad and zone (8,8) holds it as
        a road, and `zone_of` picks the road.  Two compilers then disagree
        about one square metre of ground.
        """
        out: list[dict] = []
        zx0, zz0 = tcdata.zone_of(sx, sz)
        for dzx in (-1, 0, 1):
            for dzz in (-1, 0, 1):
                zx, zz = zx0 + dzx, zz0 + dzz
                cx, cz = tcdata.zone_centre(zx, zz)
                gx, gy = tcdata.vertex_mask_index(cx, cz, sx, sz)
                if not (0 <= gx < tcdata.PITCH and 0 <= gy < tcdata.PITCH):
                    continue
                zc = self.zone(zx, zz)
                k = gy * tcdata.PITCH + gx
                if not zc["modified"][k]:
                    continue
                o = int(zc["owner"][k])
                w = self.writes[o] if o >= 0 else None
                if w is not None and w not in out:
                    out.append(w)
        return out

    def owners_at(self, x: float, z: float) -> list[dict]:
        """Every write that owns one of the four samples under (x, z).

        The four, not the nearest: the mesh under the point is a blend of all
        four, so a foreign claim on any of them moves the ground the player
        stands on.  And every ZONE that holds each of those four, not just
        `zone_of`'s -- see `holders`.
        """
        x0, z0 = math.floor(x), math.floor(z)
        out = []
        for dx in (0, 1):
            for dz in (0, 1):
                for w in self.holders(x0 + dx, z0 + dz):
                    if w not in out:
                        out.append(w)
        return out

    def foreign_at(self, x: float, z: float) -> dict | None:
        """The NON-road write owning ground under (x, z), if any.

        This is the detector for the clobber defect and it is deliberately
        blind to radii: a pad's written footprint is not its nominal
        `pad_radius_m`, and the T4 clobber was measured OUTSIDE the road's own
        pad keep-out.  Ownership is read from the blobs, not inferred from a
        circle -- from EVERY blob that holds the sample, because a boundary
        sample lives in two of them.
        """
        for w in self.owners_at(x, z):
            if w["role"] != ROAD_ROLE:
                return w
        return None

    def surface_at(self, patches: dict, x: float, z: float):
        """`(applied_y, generated_y, live_delta)` or None if not covered.

        `patches` is a `{"z_zx_zz": Patch}` map of GENERATED heights, exactly
        what `ribbon.zone_patches` returns.
        """
        x0, z0 = math.floor(x), math.floor(z)
        tx, tz = x - x0, z - z0
        gen = 0.0
        for dx, dz, w in ((0, 0, (1 - tx) * (1 - tz)), (1, 0, tx * (1 - tz)),
                          (0, 1, (1 - tx) * tz), (1, 1, tx * tz)):
            sx, sz = x0 + dx, z0 + dz
            zx, zz = tcdata.zone_of(sx, sz)
            patch = patches.get(f"z_{zx}_{zz}")
            if patch is None:
                return None
            cx, cz = tcdata.zone_centre(zx, zz)
            gx, gy = tcdata.vertex_mask_index(cx, cz, sx, sz)
            if not (0 <= gx < tcdata.PITCH and 0 <= gy < tcdata.PITCH):
                return None
            if w:
                gen += w * float(patch.at(gy, gx))
        d = self.delta_at(x, z)
        return gen + d, gen, d


def generated_at(patches: dict, x: float, z: float) -> float | None:
    """Bilinear GENERATED height, no deltas.  None when uncovered."""
    x0, z0 = math.floor(x), math.floor(z)
    tx, tz = x - x0, z - z0
    gen = 0.0
    for dx, dz, w in ((0, 0, (1 - tx) * (1 - tz)), (1, 0, tx * (1 - tz)),
                      (0, 1, (1 - tx) * tz), (1, 1, tx * tz)):
        sx, sz = x0 + dx, z0 + dz
        zx, zz = tcdata.zone_of(sx, sz)
        patch = patches.get(f"z_{zx}_{zz}")
        if patch is None:
            return None
        cx, cz = tcdata.zone_centre(zx, zz)
        gx, gy = tcdata.vertex_mask_index(cx, cz, sx, sz)
        if not (0 <= gx < tcdata.PITCH and 0 <= gy < tcdata.PITCH):
            return None
        if w:
            gen += w * float(patch.at(gy, gx))
    return gen
