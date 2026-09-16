#!/usr/bin/env python3
"""Build and read the `TCData` blob that one `_TerrainCompiler` ZDO carries.

This is the WRITE side of internal/worldintel/terrainmods.go.  That file reads
the format; this one synthesises it, so a pad can be levelled without a client,
a hoe or a player.  The decoder here exists only to self-check what the encoder
produced -- the authoritative reader stays the Go one.

FORMAT, measured from `TerrainComp::Save` / `TerrainComp::Load` IL in
assembly_valheim.dll 1.0.12, gzip'd by `Utils::Compress` (a GZipStream):

    int32   version          always 1
    int32   m_operations
    float32 m_lastOpPoint x, y, z
    float32 m_lastOpRadius
    int32   N = pitch*pitch
    N x     byte modified; if modified: float32 levelDelta, float32 smoothDelta
    int32   M = pitch*pitch
    M x     byte modified; if modified: float32 r, g, b, a

GEOMETRY, measured from `Heightmap::WorldToVertexMask` and
`Heightmap::VertexMaskToWorld`:

    index          = y*pitch + x,  pitch = m_width + 1 = 65
    sample centre  = compiler + ((x - 32)*scale, 0, (y - 32)*scale)
    mask index     = floor(dx/scale + 0.5) + 32

so sample 32 sits ON the compiler, sample 0 is 32 m to its -x/-z, and a zone's
samples span exactly the 64 m centred on the compiler.  Neighbouring zones share
their edge samples, which is why a pad crossing a zone line needs both
compilers written.

HEIGHT SEMANTICS, measured from `TerrainComp::ApplyToHeightmap`:

    if (levelDelta != 0 || smoothDelta != 0)
        heights[i] = Clamp(heights[i] + levelDelta + smoothDelta,
                           baseHeights[i] - 8, baseHeights[i] + 8)

Two consequences that decide what a flatten tool can even attempt:

  * the delta is applied to the GENERATED height, so a flat delta field does
    not flatten anything -- levelDelta[i] must be target - generated[i], and
    the generated height has to come from the world generator itself;
  * the result is CLAMPED to +/-8 m of the generated height.  A pad that needs
    more cut or fill than that cannot be produced by terrain data at all, no
    matter what delta is written.  Vangard stores deltas down to -44 m, which
    the game clamps on apply; the clamp is the real limit.

PAINT is a UnityEngine.Color whose channels are the paint types, from
Heightmap's own constants: r dirt, g cultivated, b paved, a vegetation still
standing (driven DOWN to clear it).

`m_operations`, `m_lastOpPoint` AND `m_lastOpRadius` ARE NOT BOOKKEEPING.  They
select which grass the CLIENT throws away, and getting them wrong is what made
the operator report vegetation floating over a flattened pad.  MEASURED from
`TerrainComp::CheckLoad` IL (asm md5 89ffdb64fefebc011f5a9a826f2968bf):

    if (m_nview.GetZDO().DataRevision == m_lastDataRevision) return;
    int before = m_operations;
    if (!Load()) return;                       // reads this blob
    m_hmap.Poke(0, false);                     // terrain now correct
    if (!ClutterSystem.instance) return;       // dedicated server: stops here
    if (m_operations == before + 1) {
        ClutterSystem.instance.ResetGrass(m_lastOpPoint, m_lastOpRadius);
        return;                                // NARROW branch
    }
    ClutterSystem.instance.ResetGrass(m_hmap.transform.position,
                                      m_hmap.m_width * m_hmap.m_scale / 2f);

`m_operations` is a plain field on a freshly instantiated component, so
`before` is 0 on a first load, and a blob claiming `operations == 1` takes the
NARROW branch.  With a synthesised blob whose op point and radius were zeroes,
that reset a zero-sized box at the world origin and left the pad's grass
untouched.  Grass is `ClutterSystem` clutter: MEASURED from `GenerateVegPatch`
-> `GetGroundInfo`, its Y comes from a downward `Physics.Raycast` baked into a
GameObject transform at generation time and never re-evaluated, and
`GeneratePatch` regenerates a cached patch ONLY when `PatchData.m_reset` is
set -- which `ResetGrass` is the only thing that sets.  So a missed reset is
permanently floating grass for as long as the patch lives, and it recurs on
every reload because `m_lastDataRevision` starts at 0 again on each new
component.  `m_operations` has no other meaning: it is written only by
`InternalDoOperation` (increment) and read only by `Save` and `CheckLoad`.

`Compiler` therefore defaults `operations` to 2, so a first load cannot equal
`before + 1`, AND writes an op point/radius that covers the whole zone, so a
client that does somehow take the narrow branch still resets the same grass.
"""

from __future__ import annotations

import gzip
import io
import struct
from dataclasses import dataclass, field

PITCH = 65
SAMPLES = PITCH * PITCH
SCALE = 1.0
ZONE_SIZE = 64.0
VERSION = 1
# TerrainComp::ApplyToHeightmap clamps the applied height to base +/- this.
CLAMP_M = 8.0
# The grass reset a first load must NOT select.  `CheckLoad` takes its narrow
# branch when the loaded operation count is exactly one more than the count the
# component already held, which on a first load is zero.
FIRST_LOAD_OPERATIONS = 0
OPERATIONS = FIRST_LOAD_OPERATIONS + 2
# Radius of the reset `CheckLoad` performs on its wide branch:
# m_width * m_scale / 2 = 64 * 1.0 / 2.  Writing the same radius on the op
# record makes both branches clear the same grass.
ZONE_GRASS_RESET_M = ZONE_SIZE / 2.0

# Heightmap's paint constants, as (r, g, b, a).
PAINT_DIRT = (1.0, 0.0, 0.0, 1.0)
PAINT_CULTIVATED = (0.0, 1.0, 0.0, 1.0)
PAINT_PAVED = (0.0, 0.0, 1.0, 1.0)
PAINT_RESET = (0.0, 0.0, 0.0, 1.0)
PAINT_CLEARED = (0.0, 0.0, 0.0, 0.0)
PAINT_PAVED_CLEARED = (0.0, 0.0, 1.0, 0.0)
PAINT_DIRT_CLEARED = (1.0, 0.0, 0.0, 0.0)

PAINTS = {
    "dirt": PAINT_DIRT,
    "cultivated": PAINT_CULTIVATED,
    "paved": PAINT_PAVED,
    "reset": PAINT_RESET,
    "cleared": PAINT_CLEARED,
    "paved_cleared": PAINT_PAVED_CLEARED,
    "dirt_cleared": PAINT_DIRT_CLEARED,
}


def zone_of(x: float, z: float) -> tuple[int, int]:
    """ZoneSystem::GetZone: floor((c + 32) / 64) on each axis."""
    import math

    return math.floor((x + 32.0) / 64.0), math.floor((z + 32.0) / 64.0)


def zone_centre(zx: int, zz: int) -> tuple[float, float]:
    """ZoneSystem::GetZonePos: the zone id times the zone size."""
    return zx * ZONE_SIZE, zz * ZONE_SIZE


def vertex_mask_index(cx: float, cz: float, wx: float, wz: float) -> tuple[int, int]:
    """Heightmap::WorldToVertexMask against a compiler at (cx, cz)."""
    import math

    return (math.floor((wx - cx) / SCALE + 0.5) + PITCH // 2,
            math.floor((wz - cz) / SCALE + 0.5) + PITCH // 2)


def sample_world(cx: float, cz: float, x: int, y: int) -> tuple[float, float]:
    """Inverse of vertex_mask_index: the sample's CENTRE, not its low corner."""
    return cx + (x - PITCH // 2) * SCALE, cz + (y - PITCH // 2) * SCALE


@dataclass
class Compiler:
    """One zone's worth of terrain edits, ready to serialise."""

    zone_x: int
    zone_z: int
    operations: int = OPERATIONS
    modified_height: list[bool] = field(default_factory=lambda: [False] * SAMPLES)
    level_delta: list[float] = field(default_factory=lambda: [0.0] * SAMPLES)
    smooth_delta: list[float] = field(default_factory=lambda: [0.0] * SAMPLES)
    modified_paint: list[bool] = field(default_factory=lambda: [False] * SAMPLES)
    paint: list[tuple[float, float, float, float]] = field(
        default_factory=lambda: [PAINT_RESET] * SAMPLES)

    @property
    def centre(self) -> tuple[float, float]:
        return zone_centre(self.zone_x, self.zone_z)

    def op_record(self, op_y: float = 0.0) -> tuple[float, float, float, float]:
        """`m_lastOpPoint` + `m_lastOpRadius`: the grass the client must drop.

        The zone centre with the zone's own half-extent, because a synthesised
        compiler edits the whole pad at once rather than at one hoe strike, and
        because that is exactly the reset `CheckLoad` performs on its wide
        branch.  `op_y` is cosmetic -- `ClutterSystem::ResetGrass` compares x
        and z only -- but a pad height reads better in a dump than a zero.
        """
        cx, cz = self.centre
        return cx, op_y, cz, ZONE_GRASS_RESET_M

    def set_height(self, x: int, y: int, delta: float, smooth: float = 0.0) -> None:
        i = y * PITCH + x
        self.modified_height[i] = True
        self.level_delta[i] = delta
        self.smooth_delta[i] = smooth

    def set_paint(self, x: int, y: int, colour: tuple[float, float, float, float]) -> None:
        i = y * PITCH + x
        self.modified_paint[i] = True
        self.paint[i] = colour

    def counts(self) -> dict[str, int]:
        return {
            "height_modified": sum(self.modified_height),
            "paint_modified": sum(self.modified_paint),
            "clamped": sum(1 for i in range(SAMPLES)
                           if self.modified_height[i] and abs(self.level_delta[i]) > CLAMP_M),
            "min_delta": min((self.level_delta[i] for i in range(SAMPLES)
                              if self.modified_height[i]), default=0.0),
            "max_delta": max((self.level_delta[i] for i in range(SAMPLES)
                              if self.modified_height[i]), default=0.0),
        }

    def plain(self, op_y: float = 0.0) -> bytes:
        """The inflated TerrainComp::Save payload."""
        out = io.BytesIO()
        out.write(struct.pack("<ii", VERSION, self.operations))
        out.write(struct.pack("<ffff", *self.op_record(op_y)))
        out.write(struct.pack("<i", SAMPLES))
        for i in range(SAMPLES):
            if self.modified_height[i]:
                out.write(b"\x01")
                out.write(struct.pack("<ff", self.level_delta[i], self.smooth_delta[i]))
            else:
                out.write(b"\x00")
        out.write(struct.pack("<i", SAMPLES))
        for i in range(SAMPLES):
            if self.modified_paint[i]:
                out.write(b"\x01")
                out.write(struct.pack("<ffff", *self.paint[i]))
            else:
                out.write(b"\x00")
        return out.getvalue()

    def blob(self, op_y: float = 0.0) -> bytes:
        """gzip, matching assembly_utils Utils::Compress (a GZipStream).

        mtime is pinned to 0 so the same pad always produces the same bytes -- a
        reproducible blob is what makes "did the world get the bytes I built"
        answerable by comparison rather than by trust.
        """
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=9, mtime=0) as gz:
            gz.write(self.plain(op_y))
        return buf.getvalue()


def parse(blob: bytes) -> dict:
    """Self-check decoder: inflate and walk the record, asserting it closes."""
    plain = gzip.decompress(blob)
    r = io.BytesIO(plain)

    def i32() -> int:
        return struct.unpack("<i", r.read(4))[0]

    def f32() -> float:
        return struct.unpack("<f", r.read(4))[0]

    version = i32()
    if version != VERSION:
        raise ValueError(f"unsupported TCData version {version}")
    operations = i32()
    last_point = (f32(), f32(), f32())
    last_radius = f32()
    n = i32()
    heights: dict[int, tuple[float, float]] = {}
    for i in range(n):
        if r.read(1) != b"\x00":
            heights[i] = (f32(), f32())
    m = i32()
    if m != n:
        raise ValueError(f"height array is {n} but paint array is {m}")
    paints: dict[int, tuple[float, float, float, float]] = {}
    for i in range(m):
        if r.read(1) != b"\x00":
            paints[i] = (f32(), f32(), f32(), f32())
    trailing = r.read()
    if trailing:
        raise ValueError(f"{len(trailing)} trailing bytes after the paint array")
    return {
        "version": version,
        "operations": operations,
        "last_op_point": last_point,
        "last_op_radius": last_radius,
        "samples": n,
        "heights": heights,
        "paints": paints,
        "plain_bytes": len(plain),
        "blob_bytes": len(blob),
    }
