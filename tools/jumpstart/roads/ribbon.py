#!/usr/bin/env python3
"""Write a planned road segment into the world as a levelled, paved terrain
ribbon -- through the ledger, in the proven order, with the clamp checked per
sample.

THE MECHANISM.  A road is not pieces: `piece_pavedroad` has no persistent
ZNetView and zero road pieces exist in any fleet save.  What persists is one
`_TerrainCompiler` ZDO per zone carrying a gzip'd `TCData` blob of per-sample
height deltas and per-sample ground paint.  So the ribbon is rasterised onto
each zone's 65x65 sample lattice, the delta is `profile_y - generated_y` at that
sample, and the paint is `paved_cleared` on the carriageway with a `dirt_cleared`
shoulder.

THE ORDER IS NOT NEGOTIABLE, AND ONE STEP OF IT IS THE MOST EXPENSIVE THING
ANYONE MEASURED TONIGHT:

  0. `zones_generate` EVERY zone the ribbon touches, and OBSERVE a `_ZoneCtrl`
     at the zone centre before writing it.  MEASURED by `GroundTruth`: paint
     with a cleared alpha over all 1,681 samples of a pad in an UNGENERATED zone
     planted Bush01 x15, Beech1 x15, RaspberryBush x2 and eleven more prefabs
     anyway, up to 7.68 m above the pad, as permanent ZDOs.  Cause:
     `Heightmap::Generate` ends in `ApplyModifiers`, which locates its compiler
     by scanning the static `TerrainComp::s_instances` list of INSTANTIATED
     components, and a server with no peers instantiates none -- so neither the
     deltas nor the cleared alpha reach the heightmap before `SpawnZone` runs
     `PlaceVegetation`.  For a pad that is one zone; for a 4.8 km ribbon it is
     every zone along it, which is why generation time, not paving, dominates
     the cost of this build.
  1. LOCATION CHECK every zone against the per-type radii.  MEASURED: `flatten.py`
     has NO location check and cut 6.65 m of ground out from under a
     `LocationProxy` holding a buried treasure chest, because it delegates the
     stand-off to `clearing/area.py`, which only runs when CLEARING runs.  A
     ribbon crosses hundreds of zones where clearing has not run, so the check
     lives here and a zone that fails it is not written.
  2. Write the compiler, one per zone, `operations = 2` with the op point at the
     zone centre and radius 32.  MEASURED: `operations == 1` selects
     `TerrainComp::CheckLoad`'s narrow `ResetGrass(m_lastOpPoint,
     m_lastOpRadius)` branch (because `before` is 0 on a fresh component), and a
     zeroed op record then resets a zero-sized box at the WORLD ORIGIN -- the
     operator's recurring floating grass.

ZDO COST.  One `_TerrainCompiler` per zone, because
`Heightmap::GetAndCreateTerrainCompiler` returns the FIRST it finds and a second
is dead weight.  Cost is per ZONE, not per metre.

Usage:
    ribbon.py plan  --segment T1-portalhub-temple      # offline, no writes
    ribbon.py build --segment T1-portalhub-temple      # live, through the ledger
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import shutil
import sys
from pathlib import Path

import numpy as np
import yaml

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(JUMPSTART / "terraform"))
sys.path.insert(0, str(JUMPSTART))

import applied as appliedmod  # noqa: E402
import poi as poimod  # noqa: E402
import route as routemod  # noqa: E402
import spec as specmod  # noqa: E402
import tcdata  # noqa: E402

SEED = "Pirate68"
SCRATCH = Path("/tmp/roads/zonepatch")
# WHO THE LEDGER SAYS BUILT THE ROAD.  `RoadNet` routed and pre-flighted the
# network, `RoadBuild` wrote the first pass, and `RoadEmit` writes the REPAIR:
# the graded batter, the inclusive edge and the pad approaches.  The log has to
# say who did what or the provenance of a defect is a guess -- and a repair
# appended under the name of the actor whose defect it repairs is the one
# record nobody can read.
ACTOR = "RoadEmit"
# Shoulder: one metre of dirt each side of the paved carriageway.  It is not
# decoration -- it is the visual edge that tells the operator where the road is
# from a distance, and it gives the paved band a margin so a half-metre
# rasterisation difference at the edge cannot leave a gap in the paving.
SHOULDER_M = 1.0
PAINT_ROAD = "paved_cleared"
PAINT_SHOULDER = "dirt_cleared"

# --- the batter: the side slope that joins the earthwork to natural ground --
#
# THE DEFECT THIS EXISTS FOR, MEASURED.  Until this existed the ribbon wrote
# every sample out to `edge = width/2 + shoulder` and NOTHING at edge + 1, so
# the height delta fell from the full local cut or fill to 0.0 across ONE 1 m
# lattice edge.  The carriageway itself was never the problem: on samples the
# road alone wrote, the applied surface matches the fitted profile exactly and
# its 1 m step is 0.047-0.120 m median, 0.118-0.182 m worst, i.e. the 8 %
# trunk / 12 % spur design grade expressed on a 1 m lattice.  The BLOCKINESS
# the operator reported was transverse: measured over the 15 standing
# segments, the step one metre outside the shoulder runs 0.39-1.46 m median,
# up to 8.06 m, on 42-89 % of each segment's length.  T12 at s = 302 m read
# 68.41 m across the whole 8 m bench and 65.27 m one metre later.  A level
# bench with a vertical face on each side IS a rectangular prism, and on a
# side slope that is exactly "blocky rather than sloped".
#
# THE BATTER IS A SURFACE AT GRADE, NOT A DELTA RAMPED TO ZERO, and the
# difference is the whole reason the transverse verdict could not be read.
# The first form interpolated the HEIGHT CHANGE linearly to zero over
# `run = |full| / grade`.  On level ground that yields a 38 deg face, and on a
# side slope -- the only place a batter is needed -- it does not: the applied
# surface is `generated + delta`, so its transverse slope is the taper's grade
# PLUS the hillside's own.  MEASURED on T12 after the first batter: the step
# one metre outside the shoulder read p50 0.83 m against a 0.781 limit, and
# that 0.049 m is not noise and not the design grade -- it is the hill.  A
# `step <= BATTER_GRADE` check therefore FAILS on output that looks right,
# which is this project's signature defect wearing a new hat.
#
# So the batter is constructed as what it is on the ground: a PLANE through
# the carriageway edge, falling (fill) or rising (cut) at exactly the grade,
# written as `delta = target - generated` and TERMINATED WHERE IT MEETS
# NATURAL GROUND, i.e. at the sign flip of that delta.  Three properties
# follow by construction rather than by measurement:
#   * the transverse step of the applied surface IS the grade, so the verdict
#     can be read against the grade;
#   * the taper ENDS AT GROUND -- there is nothing left to step off;
#   * the reach is bought only where the geometry needs it, and on a hillside
#     steeper than the batter the two surfaces never meet, which is a
#     MEASURABLE refusal (`steeper_than_batter`) rather than a silent wall.
#
# The slope is taken at the 38 deg SLIDE limit rather than something steeper
# so the batter is WALKABLE: the operator who steps off the carriageway on a
# hillside should walk down, not slide.  That costs reach -- the full +/-8 m
# clamp needs 8 / 0.781 = 10.24 m of run -- and reach is what the cap below
# bounds.
BATTER_GRADE = math.tan(math.radians(38.0))
BATTER_MAX_M = 10.5
# THE LATTICE'S OWN DIAGONAL, and it is the yardstick for "was the reach
# actually spent".  A ray walks OUTWARD along its transverse normal over the
# compiler's `SCALE` m lattice, so the outermost sample a ray can hold before
# the band `LAT <= edge + batter_m` cuts it off is within one diagonal of the
# cap; a ray whose outermost sample is FURTHER inside than that did not spend
# its reach, its station bin simply held no more samples.  Derived from the
# lattice rather than chosen, because the two cases are different facts and
# recording one as the other is how a silent termination becomes a false
# `reach` clip.
LATTICE_DIAGONAL_M = math.sqrt(2.0) * tcdata.SCALE
# A batter sample is EARTHWORK, not road: it keeps the biome's own ground
# texture so the visible road stays 8 m wide.  Writing paint here would read
# as a 29 m wide dirt highway.
PAINT_BATTER = None
# THE CARRIAGEWAY EDGE IS INCLUSIVE, AND ONE FLOAT ULP DECIDED IT WAS NOT.
# MEASURED by RoadClear on T12 at s = 116 m: lat -3 reads 67.00, lat -4 reads
# 65.24, lat -5 reads 68.13 -- a ONE METRE WIDE TRENCH 1.76 m deep at the
# road edge, deeper than the wall beside it.  The sample sits at exactly
# lat = 4.0 = half + shoulder, and `lat` is a `hypot` of two floats, so it
# comes back as 4.000000000000001 and a `lat <= edge` test drops it out of the
# carriageway.  Under the batter that notch would be a HIDDEN HOLE beneath a
# graded slope, so the tolerance is stated once, here, and used at both band
# boundaries.  1e-6 m is twelve orders of magnitude above the ULP and eleven
# below anything the operator can stand in.
EDGE_EPS_M = 1e-6


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------

def segment_zones(nodes: list, is_bridge: list, width_m: float,
                  shoulder_m: float = SHOULDER_M,
                  batter_m: float = BATTER_MAX_M) -> list[tuple[int, int]]:
    """Zones whose lattice the ribbon's terrain part touches.

    Densified to 1 m between stations: at a 2 m station spacing a ribbon that
    merely clips a zone corner between two stations would otherwise be missed,
    and a missed zone is a GAP IN THE ROAD -- the one defect the operator is
    guaranteed to walk into.

    THE BATTER IS PART OF THE REACH, and leaving it out is not a cosmetic
    error.  `stamp` can only write a sample whose zone it was handed a patch
    for, so a batter sample in an unrequested zone is dropped SILENTLY -- and a
    dropped batter sample is the very wall the batter exists to remove.
    MEASURED on T4 before this existed: zones (8,14), (9,14) and (8,15) were in
    the patch request and produced no compiler at all, and 333 of 1,443
    centreline metres came back unwritten.
    """
    half = width_m / 2.0 + shoulder_m + batter_m
    zones: set[tuple[int, int]] = set()
    prev = None
    for (x, z), br in zip(nodes, is_bridge):
        if br:
            prev = None
            continue
        pts = [(x, z)]
        if prev is not None:
            px, pz = prev
            n = max(1, int(math.ceil(math.hypot(x - px, z - pz))))
            pts = [(px + (x - px) * k / n, pz + (z - pz) * k / n) for k in range(n + 1)]
        for cx, cz in pts:
            for dx in (-half, 0.0, half):
                for dz in (-half, 0.0, half):
                    zones.add(tcdata.zone_of(cx + dx, cz + dz))
        prev = (x, z)
    return sorted(zones)


def lateral_and_y(nodes: np.ndarray, prof: np.ndarray, br: np.ndarray,
                  x: float, z: float) -> tuple[float, float] | None:
    """Perpendicular distance from the centreline and the profile height there.

    Projects onto each TERRAIN polyline segment and keeps the nearest.  A
    bridged segment is skipped, so the ribbon stops at the bank and the deck is
    Crossings' -- which is also how the ribbon avoids paving the riverbed.

    THE PROJECTION PARAMETER IS CLAMPED, NOT REJECTED, and that is a DEFECT FIX
    rather than a refinement.  MEASURED on T3-temple-meadhall: rejecting `tt`
    outside [0, 1] leaves the WEDGE on the outside of every polyline vertex
    claimed by NO segment, so 61 of 1,795 centreline stations -- 22 separate
    notches, one at each turn -- were skipped by the rasteriser while sitting
    0.0 to 1.0 m from the centreline.  An unwritten sample keeps its GENERATED
    height, so each notch is a 1 m hole in the middle of the carriageway as
    deep as the fill there, up to 3.31 m on T3.  Worse, `min` over the segments
    that DID accept the projection then answered with a far branch of the same
    road: the sample at (-31, -34) came back as 58.69 m from the centreline
    while standing 1 m from it, so the check reported a lateral distance for a
    place the road does not go.  Clamping makes the measure the true distance
    to the POLYLINE, and at a vertex both adjacent segments agree on the same
    clamped point and the same profile height, so the surface stays continuous.
    The end behaviour is unchanged: clamping caps a ribbon end at the distance
    to its last station, which is exactly what the old fallback below computed.
    """
    best = None
    for k in range(len(nodes) - 1):
        if br[k] or br[k + 1]:
            continue
        ax, az = nodes[k]
        bx, bz = nodes[k + 1]
        dx, dz = bx - ax, bz - az
        L2 = dx * dx + dz * dz
        if L2 <= 1e-12:
            continue
        tt = ((x - ax) * dx + (z - az) * dz) / L2
        tt = 0.0 if tt < 0.0 else (1.0 if tt > 1.0 else tt)
        px, pz = ax + dx * tt, az + dz * tt
        d = math.hypot(x - px, z - pz)
        if best is None or d < best[0]:
            best = (d, prof[k] + (prof[k + 1] - prof[k]) * tt)
    if best is None:
        # Every segment is bridged (or there are none): the terrain part of the
        # ribbon is the bank only, measured to the nearest terrain station.
        idx = [k for k in range(len(nodes)) if not br[k]]
        if not idx:
            return None
        dd = [(math.hypot(nodes[k][0] - x, nodes[k][1] - z), k) for k in idx]
        d, k = min(dd)
        return d, float(prof[k])
    return best


def lateral_and_y_grid(nodes: np.ndarray, prof: np.ndarray, br: np.ndarray,
                       X: np.ndarray, Z: np.ndarray, reach_m: float
                       ) -> tuple[np.ndarray, np.ndarray, np.ndarray,
                                  np.ndarray, np.ndarray]:
    """`lateral_and_y` for a whole lattice at once, and it is the SAME
    arithmetic -- clamped projection onto every terrain segment, nearest wins,
    profile interpolated at the clamped parameter.

    This exists because the batter made the scalar form unaffordable, not
    because vectorising is nice.  The scalar path is O(stations) per sample:
    T4 is 729 stations over a lattice that the widened reach grows from 20
    zones to ~40, i.e. 169,000 samples x 729 segments = 123 million Python
    iterations for ONE segment.  The same work as numpy row operations is 729
    vector ops per zone.  A bounding-box reject skips the segments that cannot
    reach this zone at all, which on a 1.4 km ribbon is nearly all of them.

    Returns `(lat, y, s, sgn, fx, fz)`:
      lat  perpendicular distance to the polyline, +inf where nothing claims it
      y    the fitted profile height at the projected foot
      s    ARC LENGTH of that foot along the polyline, i.e. WHICH CROSS
           SECTION this sample belongs to
      sgn  WHICH SIDE of the road, from the sign of the cross product.  A
           cross section has two rays and they are independent: a bridge pile
           on the left must not clip the right-hand batter.
      fx,  the foot itself, so a caller can walk OUTWARD from the centreline
      fz   along this sample's own transverse ray

    THE STATION, THE SIDE AND THE FOOT ARE WHAT MAKE THE BATTER A RAY RATHER
    THAN A CLOUD OF INDEPENDENT SAMPLES, and that distinction is a measured
    defect: refusing
    batter samples one at a time cuts the taper MID-SLOPE and leaves the
    refused sample standing as a 1 m ridge with the graded ground dug away on
    both sides.  MEASURED on T12 at (28.0, -14.6): the step there went from
    3.138 m (the original wall) to 3.676 m (worse than the wall the batter was
    grading away).  A refusal has to end the whole ray outward of it, and
    "outward along which ray" is a question the lattice cannot answer without
    the foot and the station.
    """
    lat = np.full(X.shape, np.inf, dtype=np.float64)
    y = np.zeros(X.shape, dtype=np.float64)
    s = np.zeros(X.shape, dtype=np.float64)
    sgn = np.zeros(X.shape, dtype=np.int8)
    fx = np.zeros(X.shape, dtype=np.float64)
    fz = np.zeros(X.shape, dtype=np.float64)
    xlo, xhi = float(X.min()) - reach_m, float(X.max()) + reach_m
    zlo, zhi = float(Z.min()) - reach_m, float(Z.max()) + reach_m
    # Arc length to each node, over TERRAIN nodes: a bridged run is not part
    # of this ribbon's surface, so its length must not shift the stations of
    # everything past it.  The absolute value is irrelevant -- `s` is only
    # ever used to group samples into cross sections -- but it must be
    # CONSISTENT, because two samples given different stations are two
    # different rays and a ray split in half is the defect above.
    acc = 0.0
    arc = [0.0] * len(nodes)
    for k in range(len(nodes) - 1):
        arc[k] = acc
        if not (br[k] or br[k + 1]):
            acc += math.hypot(float(nodes[k + 1][0]) - float(nodes[k][0]),
                              float(nodes[k + 1][1]) - float(nodes[k][1]))
    if len(nodes):
        arc[-1] = acc
    for k in range(len(nodes) - 1):
        if br[k] or br[k + 1]:
            continue
        ax, az = float(nodes[k][0]), float(nodes[k][1])
        bx, bz = float(nodes[k + 1][0]), float(nodes[k + 1][1])
        if (max(ax, bx) < xlo or min(ax, bx) > xhi
                or max(az, bz) < zlo or min(az, bz) > zhi):
            continue
        dx, dz = bx - ax, bz - az
        L2 = dx * dx + dz * dz
        if L2 <= 1e-12:
            continue
        seglen = math.sqrt(L2)
        tt = ((X - ax) * dx + (Z - az) * dz) / L2
        np.clip(tt, 0.0, 1.0, out=tt)
        px, pz = ax + dx * tt, az + dz * tt
        d = np.hypot(X - px, Z - pz)
        m = d < lat
        if not m.any():
            continue
        lat[m] = d[m]
        y[m] = (float(prof[k])
                + (float(prof[k + 1]) - float(prof[k])) * tt[m])
        s[m] = arc[k] + seglen * tt[m]
        # Cross product of the segment direction with the offset to the
        # sample: positive on one hand, negative on the other.  Zero exactly
        # on the centreline, where there is no ray to speak of.
        sgn[m] = np.sign(dx * (Z - az) - dz * (X - ax))[m].astype(np.int8)
        fx[m] = px[m]
        fz[m] = pz[m]
    return lat, y, s, sgn, fx, fz

# ---------------------------------------------------------------------------
# generated heights, on the compiler lattice
# ---------------------------------------------------------------------------

def zone_patches(zones: list[tuple[int, int]], seed: str, out: Path) -> dict:
    """PatchScan each zone at `half=32.5 step=1` centred on the zone centre.

    That request geometry is not arbitrary and not mine: it is `flatten.py`'s
    measured convention, and it is the ONLY one whose 65x65 lattice lands exactly
    on the zone's own TerrainComp sample centres (sample x = cx + (j - 32), an
    integer, because cx = zx*64).  The big routing fields are sampled on a
    HALF-integer lattice, which is fine for fitting a profile and wrong for
    computing a delta, so the deltas come from here.
    """
    import subprocess
    import os

    SCRATCH.mkdir(parents=True, exist_ok=True)
    # Reuse a cached patch file when it already holds every zone asked for.
    # PatchScan boots a whole Unity process (~20 s) before sampling, so a
    # re-plan of the same segment paid that twice for bytes that cannot have
    # changed: the generated height of a zone is a pure function of the seed.
    if out.exists() and out.stat().st_size:
        try:
            sys.path.insert(0, str(JUMPSTART / "terraform"))
            import heights as _ph
            cached = _ph.load(out)
            if all(f"z_{zx}_{zz}" in cached for zx, zz in zones):
                return cached
        except Exception:
            pass
    req = out.with_suffix(".req.tsv")
    lines = []
    for zx, zz in zones:
        cx, cz = tcdata.zone_centre(zx, zz)
        lines.append(f"z_{zx}_{zz}\t{cx:g}\t{cz:g}\t32.5\t1")
    req.write_text("\n".join(lines) + "\n")
    env = {**os.environ,
           "VH_SRC": "/media/big4/projects/game/valheim/Ulfsland/data/bepinex",
           "SEED": seed, "REQ": str(req), "OUT": str(out),
           # A PER-AGENT SANDBOX.  `/tmp/patchscan/vh` is shared and its
           # BepInEx tree is owned by root from an earlier run, so
           # `run_patchscan.sh`'s clean step fails with "Permission denied",
           # the scan never runs, and the STALE output file still exists and
           # still has non-zero size -- so the existence check passes and the
           # caller gets the previous run's zones.  MEASURED on T3: three
           # zones the widened batter needs, (1,-1), (3,5) and (5,5), came
           # back missing and `stamp` died on a KeyError.  A silent fallback
           # to old data is worse than a crash, so the sandbox is unshared and
           # the completeness check below is re-asserted after the scan.
           "SANDBOX": f"/tmp/patchscan/{ACTOR.lower()}"}
    proc = subprocess.run(["bash", str(JUMPSTART / "blueprints" / "run_patchscan.sh")],
                          env=env, capture_output=True, text=True)
    if not out.exists() or out.stat().st_size == 0:
        raise SystemExit(f"patchscan produced nothing:\n{proc.stderr[-4000:]}")
    sys.path.insert(0, str(JUMPSTART / "terraform"))
    import heights as patchheights
    got = patchheights.load(out)
    missing = [(zx, zz) for zx, zz in zones if f"z_{zx}_{zz}" not in got]
    if missing:
        raise SystemExit(
            f"patchscan did not produce {len(missing)} requested zones "
            f"{missing[:8]}: the output file {out} exists and is non-empty, so "
            f"it is the PREVIOUS run's data. Refusing to rasterise a segment "
            f"against generated heights that do not cover it -- an unwritten "
            f"batter sample is the wall the batter exists to remove.\n"
            f"patchscan stderr tail:\n{proc.stderr[-2000:]}")
    return got


def generated_digest(patch) -> str:
    """sha256 of the zone's 65x65 GENERATED heights, little-endian float32,
    row-major with z ascending outer and x ascending inner -- exactly the order
    PatchScan wrote them.

    Stated because a digest without its byte order is worthless.  This is the
    field that lets a replay DETECT a worldgen change instead of silently
    putting the ground somewhere else: TCData deltas are relative to generated
    height, so the same deltas on different generated heights are a different
    road.
    """
    arr = np.asarray(patch.heights, dtype="<f4")
    return hashlib.sha256(arr.tobytes()).hexdigest()


def protected_piece_positions(zones: list[tuple[int, int]]) -> dict:
    """Every PIECE of every `flatten: FORBIDDEN` structure whose zone this
    segment touches, read out of the ledger.

    The positions are not a declaration anyone made for this purpose: each
    `spawn_plan` record names a blob, the blob is the literal command list that
    built the structure, and the ledger refuses to replay a plan whose blob is
    missing -- so this is the same evidence the structure itself was built
    from.  MEASURED field order in those blobs: `pos=<z>,<x>,<y>`, confirmed
    against three structures whose `expect.prefab_count` positions are (x, z)
    and whose counts verified live (harbour expect (4.5, -265.2) has plan
    field 0 in -276.9..-256.5 and field 1 in -7.5..12.9).  Read the other way
    round and every piece lands in the wrong zone, which is a guard that
    protects nothing while appearing to.

    `spawn` and `portal` records carry a single piece as `pos=[x, y, z]`.
    A protected record with NO recoverable piece is reported in
    `without_pieces`: the caller must treat that as unknown rather than clear.
    """
    sys.path.insert(0, str(JUMPSTART / "ledger"))
    from writer import Ledger
    led = Ledger.open("Ulfsland", actor=ACTOR)
    zset = set(zones)
    pieces: list[tuple[float, float]] = []
    by_site: dict[str, int] = {}
    without: dict[str, list[int]] = {}
    # The structure's own recorded EXTENT, for the one question a piece list
    # cannot answer: is this bit of water the water a protected structure
    # stands over?  `expect.prefab_count` rows carry pos + max, which is the
    # radius Crossings itself verified the pieces inside.
    discs: list[tuple[float, float, float]] = []
    # A STRUCTURE IS A SITE, NOT A RECORD.  MEASURED why this matters: the
    # spawn portal ring's only `flatten: FORBIDDEN` record is a
    # `zones_generate` (seq 170) that carries no pieces at all, while the four
    # portals it protects are separate `portal` records whose own `flatten` is
    # unset -- so grouping by record finds nothing to keep out of, and grouping
    # by SITE finds the four positions that actually must not move.  Same for
    # Crossings' structures: the pieces live in the `spawn_plan` record and the
    # FORBIDDEN flag is repeated on its `zones_generate` and `objects_clear`
    # siblings.
    recs = led.records()
    protected_sites = {
        (r["params"].get("site_id") or r["params"].get("role") or f"line{i}")
        for i, r in enumerate(recs)
        if r["params"].get("flatten") == "FORBIDDEN"}
    for line, rec in enumerate(recs):
        p = rec["params"]
        site = p.get("site_id") or p.get("role") or f"line{line}"
        if site not in protected_sites:
            continue
        found = []
        if rec["op"] == "spawn_plan":
            for sha in (rec.get("requires") or {}).get("blobs", []):
                try:
                    text = led.read_blob(sha).decode("utf-8", "replace")
                except Exception:
                    continue
                if "spawn_object" not in text:
                    continue
                for ln in text.splitlines():
                    if "pos=" not in ln:
                        continue
                    f = ln.split("pos=")[1].split()[0].split(",")
                    found.append((float(f[1]), float(f[0])))
        elif rec["op"] in ("spawn", "portal"):
            pos = p.get("pos")
            if isinstance(pos, (list, tuple)) and len(pos) == 3:
                found.append((float(pos[0]), float(pos[2])))
        keep = [(x, z) for x, z in found if tcdata.zone_of(x, z) in zset]
        if keep:
            pieces += keep
            by_site[site] = by_site.get(site, 0) + len(keep)
        for row in ((rec.get("expect") or {}).get("prefab_count") or []):
            q, mx = row.get("pos"), row.get("max")
            if q is None or mx is None:
                continue
            discs.append((float(q[0]), float(q[1]), float(mx)))
    for site in sorted(protected_sites):
        if site not in by_site:
            without[site] = [i for i, r in enumerate(recs)
                             if (r["params"].get("site_id")
                                 or r["params"].get("role")) == site]
    seen = set()
    discs = [d for d in discs if not (d in seen or seen.add(d))]
    return {"pieces": pieces, "by_site": by_site, "discs": discs,
            "without_pieces": without,
            "tool": "tools/jumpstart/roads/ribbon.py::protected_piece_positions"}


# ---------------------------------------------------------------------------
# the ribbon
# ---------------------------------------------------------------------------

# MEASURED ZoneSystem::c_WaterLevel.  A sample below it is seabed, and the one
# thing a terrain write must never do is lower ground out from under a
# structure that stands over water -- the early-dock defect.  Dry ground has no
# water to remove, so the hazard is exactly "below this".
WATER_LEVEL_M = 30.0
# The set of samples that can move the ground at a protected piece is exactly
# the four the game blends around it, i.e. the ones within one lattice pitch on
# each axis.  Excluding that set by CHEBYSHEV distance makes `delta_at` at the
# piece 0.0 in float rather than merely small -- there is no tolerance to argue
# about afterwards.
PROTECT_CLEAR_M = 1.0
# The same Chebyshev set around a LIVE placed object.  MEASURED by SeatCheck
# tonight: the hub-end portal `u-lheast` at (-307.5, 223.5) floats 0.536 m
# because RoadBuild's ribbon cut the ground under it AFTER it was seated.  The
# batter is new ground movement over ground that already carries seated
# objects, so it is gated on the objects themselves rather than on a radius.
PLACED_CLEAR_M = 1.0


class PadKeepOut:
    """WHERE THE CARRIAGEWAY MAY NOT BE PAVED, and it is a set of PER-BUILDING
    FOUNDATIONS rather than one circle per settlement.

    THE DEFECT THIS EXISTS FOR, MEASURED.  `settlements/sites.yaml` records one
    `pad_radius_m` per site and this module read it as a pad footprint.  For a
    watchtower or a castle that is true and the two numbers agree: wt-spawn's
    disc is 16.1 m against a 20 x 20 m pad, hognest's 33.1 m against 44 x 44 m,
    i.e. the half-diagonal plus a margin.  For a TOWN it is false, and
    `plan.py` says so in its own docstring -- "taken from the district or the
    pad plus its own margin".  `stenvik` and `vestvik` are `pad_radius_m:
    100.0`, a DISTRICT radius, and stenvik's own `why` field states the
    consequence outright: "The district is NOT levelled -- no 176 m district of
    <= 12 m relief on this seed is both dry and coastal, so the town follows
    the ground on individual pads."

    So a 100 m disc was refusing the carriageway over ground nobody had built
    or ever would.  MEASURED on T4-meadhall-stathub: 293.9 m of centreline
    written NOTHING, a -8.491 m step across the gap at (544.8, 809.2), and the
    trunk road into the town ending at a wall.  The foundations it was standing
    in for are 15 per-building `site_pad` rectangles whose WORST half-extent is
    13.05 m (`stenvik-hall-1`), so the disc was 7.7x the thing it protected.
    `stenvik-house-3` at (606.5, 938.8) is 113 m from the centre and OUTSIDE
    the disc entirely -- the circle was both too big and in the wrong place.

    THE FOOTPRINTS ARE ASKED OF THE WRITE-TIME GUARD, NOT RE-DERIVED HERE.
    `writer._pad_claims` is the function `writer._pad_footprint_check` itself
    uses to decide whether a `terrain_write` authors a sample inside a
    foundation, and it is the authority that will refuse this write at append
    time.  Modelling its geometry here -- half-extents from `pad_w / 2 +
    apron_m`, one claim per site, latest in file order, position resolved from
    the site's sibling records -- is how a closed-form shrink radius silently
    skipped 6 cylinders the gate itself passes.  So the rasteriser asks the
    guard and gets the same rectangles the guard will judge it against.

    THE RECTANGLE IS A TRIGGER, NOT THE WHOLE FOUNDATION, and the fine gate is
    already in place.  A pad levels its rectangle and FEATHERS PAST IT:
    MEASURED, `stenvik-stonehouse-1` seq 272 authored 165 samples and only 60
    lie inside its 11.0 x 15.0 m rectangle.  Those outer 105 are caught by
    `refusal`'s per-sample `foreign` gate, which reads the AUTHOR of each
    sample off the live blobs (`applied.Applied`) and is blind to radii by
    design -- it is what caught the original T4 clobber OUTSIDE every circle.
    Per-sample authorship is the authority; this rectangle set is the cheap
    planning stand-off that keeps the road off a foundation the pad has not
    finished laying.

    THE NOMINAL DISC SURVIVES FOR A SITE WITH NO LIVE PAD, because there the
    question cannot be asked: nothing is written, so authorship answers
    nothing, and the declared radius is the only statement of where Settlements
    intends to build.  MEASURED on this chain: all 16 road sites have at least
    one live pad claim, so no site takes this path today -- it is here so that
    a site planned and not yet built is still protected rather than paved.
    """

    __slots__ = ("rects", "discs", "by_id", "levelled", "undecodable")

    def __init__(self, rects: list[tuple[str, float, float, float, float]],
                 discs: list[tuple[str, float, float, float]],
                 by_id: dict[str, dict] | None = None,
                 levelled: dict[tuple[int, int], str] | None = None,
                 undecodable: list[dict] | None = None):
        self.rects = rects
        self.discs = discs
        # THE FOUNDATION'S OWN RECORD, for the junction report.  Kept beside
        # the hot tuple list rather than in it: `contains` is asked once per
        # lattice sample over a 1.5 km ribbon and has no business unpacking a
        # dict, while the report is asked once per gap.
        self.by_id = by_id or {}
        # EVERY WORLD SAMPLE A PAD ACTUALLY LEVELLED, `{(sx, sz): site_id}`.
        # A dict keyed on the integer sample is an O(1) test and it is the
        # same set `writer._pad_footprint_check` judges against, so the
        # rasteriser and the write-time guard cannot disagree about what a
        # foundation is.
        self.levelled = levelled or {}
        self.undecodable = undecodable or []

    def contains(self, x: float, z: float) -> str | None:
        """The site whose foundation covers `(x, z)`, or None.

        RECTANGLE **OR** LEVELLED SET, because the pad's own write is the
        foundation and it does not stop at the rectangle -- see
        `pad_keepouts_from_chain`.  The levelled set is asked first: it is an
        O(1) dict hit against an O(n) scan of the rectangles.

        `EDGE_EPS_M` on the rectangle for the same reason the carriageway edge
        carries it: a half-extent is a sum of floats and a sample sitting
        exactly on the boundary must land on one definite side of it.
        """
        if self.levelled:
            sid = self.levelled.get((int(math.floor(x)), int(math.floor(z))))
            if sid is None and (x != math.floor(x) or z != math.floor(z)):
                # An off-lattice point renders as a BLEND of the four samples
                # around it, so a pad owning any of them owns the ground here.
                # `delta_at`'s docstring is the measurement: a piece 0.9 m
                # outside a written sample still settles.
                x0, z0 = int(math.floor(x)), int(math.floor(z))
                for dx in (0, 1):
                    for dz in (0, 1):
                        sid = self.levelled.get((x0 + dx, z0 + dz))
                        if sid is not None:
                            break
                    if sid is not None:
                        break
            if sid is not None:
                return sid
        for sid, cx, cz, hw, hd in self.rects:
            if abs(x - cx) <= hw + EDGE_EPS_M and abs(z - cz) <= hd + EDGE_EPS_M:
                return sid
        for sid, cx, cz, r in self.discs:
            dx, dz = x - cx, z - cz
            if dx * dx + dz * dz <= r * r:
                return sid
        return None

    def pad_at(self, x: float, z: float) -> dict | None:
        """The FOUNDATION RECORD covering `(x, z)` -- id, seq, centre,
        half-extents and the datum it was LEVELLED TO -- or None.

        THE DATUM HAS TO BE THE BUILDING'S, NOT THE TOWN'S, and this is the
        district defect wearing its third hat.  The junction report used to
        find a gap's site by `pad_radius_m` and quote that site's `pad_y`: for
        every one of T4's six Stenvik gaps that answered "stenvik, pad datum
        45.57 m, pad_levelled_yet False" -- a datum no ground in the town is
        at, on foundations that were levelled hours ago.  MEASURED: the gap at
        (584.3, 954.0) terminates on `stenvik-beehive-1` seq 286, whose
        `target_y` is 47.11 and whose floor reads 47.110 on the live surface,
        1.54 m above the town figure.  `sites.yaml`'s own `why` says the
        district is not levelled and the town follows the ground on individual
        pads, so quoting the district datum reports somebody's intention as
        the ground the operator will stand on.
        """
        sid = self.contains(x, z)
        return None if sid is None else self.by_id.get(sid)

    def __len__(self) -> int:
        return len(self.rects) + len(self.discs)

    def describe(self) -> str:
        worst = max((max(hw, hd) for _s, _x, _z, hw, hd in self.rects),
                    default=0.0)
        return (f"{len(self.rects)} per-building pad rectangle(s) "
                f"(worst half-extent {worst:.2f} m) + "
                f"{len(self.levelled)} sample(s) those pads actually levelled "
                f"+ {len(self.discs)} nominal disc(s) for site(s) with no live "
                f"pad"
                + (f"; {len(self.undecodable)} pad entry(ies) COULD NOT BE "
                   f"DECODED" if self.undecodable else ""))


def pad_keepouts_from_chain(sites: list[dict], records: list[dict],
                            ledger=None) -> PadKeepOut:
    """Build the carriageway keep-out by ASKING `writer._pad_claims`.

    `sites` is `settlements/sites.yaml`'s site list -- the fallback keep-out
    and fallback datum for a site with nothing written yet.  `records` is the
    live chain, and for anything already built it is the authority for BOTH
    the footprint and the datum: a pad's `terrain_write` carries the
    `target_y` it levelled to, which is the height the road must arrive at.

    A pad claim is attributed to a site by its `site_id` prefix, which is the
    convention `settlements/build.py` writes: `stenvik-hall-1` belongs to
    `stenvik`.  A claim matching no site is still a foundation and still kept,
    under its own id -- the keep-out is a union of foundations, not a directory
    of settlements.

    THE FOOTPRINT IS THE RECTANGLE **OR** THE PAD'S OWN LEVELLED SET, and the
    second half is not optional -- MEASURED, it is what refused T4 at append
    time when this function returned rectangles alone.  A pad levels its
    rectangle and FEATHERS PAST IT: `stenvik-cottage-2` seq 265 levelled
    (480, 908), which is 0.1 m outside its own 10.4 x 12.4 m rectangle, and
    T4's batter authored a +1.400 m move there.  `writer._pad_footprint_check`
    tests rectangle OR levelled set and neither contains the other, so a
    keep-out built from one of them is refused by the other.

    THE LEVELLED SET IS READ WITH THE GUARD'S OWN DECODER, `_entry_samples`,
    rather than re-derived from the blob here.  A pad's entry is a 65x65
    lattice over a 64 m zone whose boundary row is SHARED with the next zone,
    so the set has to be keyed by WORLD SAMPLE and not by lattice index --
    which is the one thing that decoder already gets right and an
    index-arithmetic copy of it would not.

    THE LEVELLED SET IS SCOPED TO EFFECTIVE AUTHORSHIP, by the same ruling
    that fixed the guard.  `_entry_samples` lists every sample a pad's blob
    MODIFIED, which includes ground a later claim has since re-authored --
    and `writer._pad_footprint_check` now resolves a sample to the LAST claim
    that changed it, so a pad that has been built over no longer owns it.
    Keeping those samples in the keep-out refuses the road ground the guard
    would let it write: MEASURED on T4, the unscoped set skipped 2,079
    samples against 588, the road stopped writing around
    `stenvik-beehive-1`, the union restored seq 1158's old profile there and
    the 8 m gradient went back to 0.8849 -- the original defect, reintroduced
    by protecting a foundation that is no longer under that square metre.
    A superseded floor is ground that no longer exists.

    FAIL LOUD, NOT OPEN.  A pad whose blob cannot be read or decoded is
    reported in `undecodable`: the guard fails CLOSED on exactly that case, so
    silently protecting less here buys a refusal at append time instead of a
    warning now.
    """
    claims = writer_pad_claims(records)
    target_of = {r["seq"]: r["params"].get("target_y")
                 for r in records if r.get("op") == "terrain_write"}
    rec_of = {r["seq"]: r for r in records if r.get("op") == "terrain_write"}
    rects = [(q["site_id"], q["x"], q["z"], q["hw"], q["hd"]) for q in claims]
    levelled: dict[tuple[int, int], str] = {}
    undecodable: list[dict] = []
    if ledger is not None:
        import writer as writermod  # noqa: PLC0415
        pad_seqs = {q["seq"] for q in claims}
        touched: dict[tuple[int, int], str] = {}
        for q in claims:
            rec = rec_of.get(q["seq"])
            if rec is None:
                continue
            for e in rec["params"].get("entries", []):
                got = writermod._entry_samples(e, ledger)
                if got is None:
                    undecodable.append({"site_id": q["site_id"],
                                        "seq": q["seq"],
                                        "zone": e.get("zone")})
                    continue
                for sxz in got:
                    touched[sxz] = q["site_id"]
        # THE EFFECTIVE AUTHOR, by the guard's own disagreement test over the
        # chain in FILE ORDER.  Scoped to the samples some pad touched, so
        # this is a few tens of thousands of lookups rather than a whole-world
        # composition.
        state: dict[tuple[int, int], tuple[float, float]] = {}
        owner: dict[tuple[int, int], int] = {}
        for rec in records:
            if rec.get("op") != "terrain_write":
                continue
            sq = rec["seq"]
            for e in rec["params"].get("entries", []):
                got = writermod._entry_samples(e, ledger)
                if got is None:
                    continue
                for k, v in got.items():
                    if k not in touched:
                        continue
                    was = state.get(k)
                    if was is None or abs((v[0] + v[1]) - (was[0] + was[1])) \
                            > writermod.FLATTEN_TOLERANCE_M:
                        owner[k] = sq
                    state[k] = v
        for k, sid in touched.items():
            if owner.get(k) in pad_seqs:
                levelled[k] = sid
    by_id: dict[str, dict] = {}
    for q in claims:
        by_id[q["site_id"]] = {
            "site_id": q["site_id"], "seq": q["seq"], "name": q["name"],
            "xz": [round(q["x"], 2), round(q["z"], 2)],
            "half_extents_m": [round(q["hw"], 2), round(q["hd"], 2)],
            "pad_y": target_of.get(q["seq"]), "levelled": True,
            "datum_source": "the pad's own terrain_write target_y"}
    claimed_sites = {q["site_id"] for q in claims}
    discs = []
    for s in sites:
        sid = str(s["id"])
        if any(c == sid or c.startswith(sid + "-") for c in claimed_sites):
            continue
        discs.append((sid, float(s["xz"][0]), float(s["xz"][1]),
                      float(s["pad_radius_m"])))
        by_id[sid] = {
            "site_id": sid, "seq": None, "name": None,
            "xz": [float(s["xz"][0]), float(s["xz"][1])],
            "half_extents_m": None,
            "pad_y": (None if s.get("pad_y") is None else float(s["pad_y"])),
            "levelled": False,
            "datum_source": ("settlements/sites.yaml pad_y -- DECLARED, this "
                             "site has no pad in the chain yet")}
    return PadKeepOut(rects, discs, by_id, levelled, undecodable)


def writer_entry_samples(entry: dict, ledger):
    """`writer._entry_samples`, for the same reason `writer_pad_claims` is
    wrapped: the ledger is imported where it is used.  Returns
    `{(sx, sz): (level, smooth)}` keyed by WORLD sample, or None when the blob
    cannot be read or decoded.
    """
    sys.path.insert(0, str(JUMPSTART / "ledger"))
    import writer as writermod  # noqa: PLC0415
    return writermod._entry_samples(entry, ledger)


def writer_pad_claims(records: list[dict]) -> list[dict]:
    """`writer._pad_claims`, imported where it is used rather than at module
    scope: `roads/` is importable without the ledger on the path (the census
    and equivalence tools do exactly that), and a keep-out is only ever built
    by a driver that already has a chain in hand.
    """
    sys.path.insert(0, str(JUMPSTART / "ledger"))
    import writer as writermod  # noqa: PLC0415
    return writermod._pad_claims(records)


def _clip(st: dict, cause: str, is_road: bool, road_key: str | None,
          wx: float, wz: float, lat: float, full: float,
          residual: float | None = None) -> None:
    """Record a sample the earthwork could not write, and -- when it is a
    BATTER sample -- the WALL that therefore stays standing.

    TWO HEIGHTS, BECAUSE THEY ARE TWO DIFFERENT CLAIMS AND CONFLATING THEM IS
    HOW THE WORSE-THAN-THE-WALL DEFECT HID.  `full` is the cut or fill the
    carriageway holds at the head of this ray -- the wall that stood there
    BEFORE any batter, and the number the repair has to beat.  `residual` is
    what is actually left at the clip: the height between the graded surface
    where the ray stopped and natural ground there.  A ray clipped at its very
    first sample leaves `residual == full` (no better, no worse); a ray
    clipped further out leaves less.  Reporting only `full` would understate
    the repair; reporting only `residual` would hide a clip that achieved
    nothing.

    A clip that leaves no trace is indistinguishable from a batter that
    worked, and "the batter is on every segment" would then be true of a run
    that graded nothing.

    Module level rather than a closure in the sample loop on purpose: it is
    called once per rejected sample over tens of thousands of samples per
    segment, and a `def` inside that loop allocates a function object per
    iteration for no benefit.
    """
    if is_road and road_key:
        st[road_key] = st.get(road_key, 0) + 1
        st["road_clip_at"].append((wx, wz, cause, round(full, 3)))
        return
    key = f"batter_clipped_{cause}"
    st[key] = st.get(key, 0) + 1
    before = abs(full)
    h = before if residual is None else abs(residual)
    st["walls_left_by_cause"][cause] = st["walls_left_by_cause"].get(cause, 0) + 1
    if h > st["walls_left_max_m"]:
        st["walls_left_max_m"] = round(h, 3)
    if before > st["walls_before_max_m"]:
        st["walls_before_max_m"] = round(before, 3)
    # Only walls worth warning about are listed, and the threshold is the one
    # the operator's own complaint is measured against: below 0.5 m a step is
    # a kerb, not a cliff.
    if len(st["walls_left"]) < 60 and h >= 0.5:
        st["walls_left"].append({"world": [round(wx, 1), round(wz, 1)],
                                 "lat_m": round(lat, 1), "wall_m": round(h, 2),
                                 "wall_before_m": round(before, 2),
                                 "cause": cause})
    # EVERY clip's position, uncapped and unfiltered, because the transverse
    # census has to be able to answer "is this step one I already recorded a
    # reason for, or one nobody explained".  An unexplained step is a bug; an
    # explained one is a documented residual, and a verdict that cannot tell
    # them apart is the same failure as a verdict against a flat 0.5 m.
    st["clip_at"].append((wx, wz, cause, round(h, 3)))


def stamp(seg: dict, patches: dict, zones: list[tuple[int, int]],
          pad_keepouts: "PadKeepOut",
          protected_pieces: list[tuple[float, float]] | None = None,
          protected_discs: list[tuple[float, float, float]] | None = None,
          placed_objects: list[tuple[float, float]] | None = None,
          poi_keepouts: list[tuple[float, float, float]] | None = None,
          batter_m: float = BATTER_MAX_M,
          batter_grade: float = BATTER_GRADE,
          foreign_at=None,
          road_claim_at=None,
          ) -> tuple[dict, dict]:
    """Rasterise one segment into per-zone compilers.  Returns (comps, stats).

    Four things stop the ribbon rather than one, and each is somebody's
    property: a Settlements PAD (its earthwork, levelled per building), a
    protected PIECE (an over-water structure whose ground must not move --
    this is the `flatten: FORBIDDEN` rule expressed at the granularity the
    hazard actually has), A PROTECTED STRUCTURE'S WATER, and -- new, and
    MEASURED rather than declared -- a sample a FOREIGN TERRAIN WRITE ALREADY
    OWNS.

    THE FOREIGN-OWNERSHIP GATE IS THE T4 DEFECT'S FIX, AND THE RULING IS
    MAIN'S: inside a settlement pad's footprint the PAD WINS -- it is a
    building's foundation and it must stay flat -- but the road owns the
    approach.  MEASURED with `applied.py`: 89 of T4's 1,443 centreline metres
    sit on samples `SiteFinish` seq 286 overwrote after `RoadBuild` seq 213,
    and sample (586, 954) holds road delta -4.470 against a live -1.571, so
    the applied surface jumps 45.05 -> 47.11 in ONE METRE mid-carriageway.
    Note what the gate is NOT: it is not a radius.  That clobber lies OUTSIDE
    the road's own `pad_radius_m` keep-out, so a circle cannot see it.
    Ownership is read from the blobs.  A sample the pad owns is left alone and
    carried forward by the union; the step into it is removed by regrading the
    APPROACH (`grade_into_foreign`), not by fighting over the sample.

    THE WATER RULE IS SCOPED TO THE STRUCTURES, NOT TO THE WATER PLANE, and
    the first version got that wrong.  Refusing every sample below 30.0 m
    sounds safe and is not the rule: MEASURED on W5-brgs1-wsouth and
    W10-brgs1-treenear, RoadNet's profile crosses an 8 m pond on 2.9 m of
    fill -- a CAUSEWAY, with no structure within 200 m -- and a blanket rule
    left an 8 m swim in the middle of a finished road.  The hazard the rule
    exists for is removing the water a protected structure STANDS OVER, so it
    is tested inside that structure's OWN recorded extent and nowhere else.

    THE EARTHWORK IS TWO BANDS WITH TWO DIFFERENT POLICIES, and the difference
    is the whole safety argument for widening the write.  The CARRIAGEWAY
    (lat <= edge) must be continuous, so a protection that blocks a
    carriageway sample leaves a recorded GAP and `walkability` judges it.  The
    BATTER (lat > edge) is cosmetic relief, so a protection that blocks a
    batter sample CLIPS THE REST OF THAT RAY: the wall stays there and is
    counted.  A clipped batter is a blemish; a clipped carriageway is a hole
    the operator falls into.  Same geometry, opposite failure mode, so they
    cannot share a policy.

    THE BATTER IS WRITTEN RAY BY RAY, OUTWARD, AND STOPS ONCE.  Two measured
    defects forced that shape and neither is visible sample by sample:

      * PER-SAMPLE REFUSAL LEAVES A RIDGE.  A refused sample keeps its
        generated height while the graded samples either side of it are dug
        away, so the step INTO it is bigger than the wall the batter was
        removing.  MEASURED on T12 at (28.0, -14.6): 3.138 m of original wall
        became a 3.676 m step.  Walking the ray outward and stopping at the
        first refusal makes the residual at the stop at most the original
        wall, by construction: the graded surface is monotone in the direction
        of the cut or fill, so whatever is left is a fraction of what was
        there.
      * A TAPER THAT RESUMES BEYOND A HOLLOW IS AN ISOLATED PATCH.  The batter
        stops where its plane MEETS natural ground; past that crossing the
        plane is underground, and writing it again further out because the
        ground dipped would bury a second slab in the hillside.

    A ray is one cross section on one side: `(sign, station rounded to 1 m)`.
    That is the lattice's own resolution -- samples are 1 m apart -- so a ray
    is the set of samples a player crosses walking straight off the road at
    that station.
    """
    nodes = np.array(seg["nodes"], dtype=np.float64)
    prof = np.array(seg["profile_y"], dtype=np.float64)
    br = np.array(seg["is_bridge"], dtype=bool)
    half = seg["width_m"] / 2.0
    edge = half + SHOULDER_M
    road_colour = tcdata.PAINTS[PAINT_ROAD]
    shoulder_colour = tcdata.PAINTS[PAINT_SHOULDER]

    comps: dict[tuple[int, int], tcdata.Compiler] = {}
    st = {"samples_paved": 0, "samples_shoulder": 0, "samples_batter": 0,
          "skipped_pad": 0, "skipped_protected": 0, "skipped_underwater": 0,
          "skipped_foreign": 0,
          "batter_clipped_pad": 0, "batter_clipped_protected": 0,
          "batter_clipped_underwater": 0, "batter_clipped_placed": 0,
          "batter_clipped_water_edge": 0, "batter_clipped_poi": 0,
          "batter_clipped_foreign": 0, "batter_clipped_steeper_than_batter": 0,
          "batter_clipped_shared_corridor": 0,
          "batter_clipped_reach": 0,
          "batter_clipped_placed_at": [],
          "batter_met_ground": 0,
          # GATE 4: A WALL THAT COULD NOT BE GRADED IS RECORDED, WITH ITS
          # HEIGHT AND ITS REASON.  A recorded wall the operator can be warned
          # about beats a forced write beside a bridge pile, and a clip that
          # leaves no trace is indistinguishable from a batter that worked.
          "walls_left": [], "walls_left_max_m": 0.0, "clip_at": [],
          # EVERY CARRIAGEWAY REFUSAL'S POSITION AND CAUSE, uncapped.  The
          # counters beside them say HOW MANY samples the road could not
          # claim; they cannot say WHERE the road therefore stops, and that is
          # the only question a gap report can be answered from.  MEASURED on
          # T4: 47.0 m of centreline in zone (8,7) is refused because
          # `tree-sth-2` seq 669 owns all 1,126 written samples in that zone,
          # and with no position recorded `walkability` could only call an
          # explained stop a HOLE -- refusing a segment whose ground is
          # somebody's foundation, which is the one case the ruling says the
          # road must accept and grade into.
          "road_clip_at": [],
          # EVERY SAMPLE OF EVERY RAY THAT WAS SKIPPED FOR HAVING NO ROAD, in
          # the same `(x, z, cause, height)` shape the clip list uses, so the
          # transverse census can tell a recorded residual from a bug.
          "ray_skip_at": [],
          # WHERE EVERY RAY ENDED, as `(station_arc_m, side, lat_m, cause)`.
          # THE TERMINATION LOCUS IS A FIRST-CLASS MEASUREMENT because an
          # `ADJACENT` run of unexplained over-grade steps has two readings and
          # only this series tells them apart: rays that end at smoothly
          # varying laterals mean the hillside itself is adjacent and the
          # clips are honest, while rays that end at materially different
          # laterals mean the LOCUS OF TERMINATIONS is a new cliff running
          # parallel to the road -- the same family as the edge wall the
          # batter exists to repair.  It has to be recorded HERE, per ray,
          # because a ray is the unit that terminates: reconstructing it by
          # bucketing written samples into 1 m station bins measures lattice
          # aliasing instead, and MEASURED on T12 that reads a 5.611 m
          # "sawtooth" at station 268 where the ray series is smooth.
          # `met_ground` rays record nothing else at all, so without this the
          # 69 % of terminations that are the batter working as designed are
          # invisible to the test.
          "ray_end_at": [],
          "walls_before_max_m": 0.0,
          "walls_left_by_cause": {},
          "rays": 0, "rays_clipped": 0,
          "rays_without_road": 0,
          # A RAY WHOSE OWN SAMPLE LIST ENDED INSIDE ITS CAP.  Counted
          # separately from `rays_clipped` because nothing refused it and it
          # left no wall of its own: the samples outboard of it at that arc
          # were binned to the neighbouring station and are written there.
          "rays_bin_exhausted": 0,
          "samples_batter_in_pad_standoff": 0,
          "interstice_rays": 0, "interstice_apron": 0,
          "interstice_left_ungraded": 0, "interstice": [],
          "samples_apron": 0,
          "max_cut_m": 0.0, "max_fill_m": 0.0, "over_clamp": 0,
          "over_clamp_samples": [], "per_zone": {},
          "batter_grade": round(batter_grade, 4), "batter_max_m": batter_m,
          "edge_m": edge, "edge_eps_m": EDGE_EPS_M,
          "edge_exact_samples": 0}
    # The world positions of every sample this write actually TOUCHES.  The
    # location check is run against these, not against zone centres -- see
    # `location_check`.
    written: list[tuple[float, float]] = []
    # CARRIAGEWAY samples only.  `location_check`'s distance rule budgets for
    # the ROAD's own geometry (`half_width_m`), and a batter sample is not
    # road: including it would both overstate the road's width and make an
    # 11 m-wider earthwork look like an 11 m-wider carriageway to the gate.
    # The batter's real hazard -- moving a piece's ground -- is measured
    # directly by `delta_gate` over the whole compiler set, which sees every
    # sample regardless of which list it is in.
    road_written: list[tuple[float, float]] = []
    reach = edge + batter_m

    def refusal(wx: float, wz: float, generated: float,
                is_road: bool) -> str | None:
        """The one reason this sample cannot be written, or None.

        Ordered cheapest-and-hardest first.  The order is not cosmetic: a
        sample inside a pad is the pad's whatever else is true of it, and
        recording it under a softer cause would misattribute the wall.
        """
        # THE FOUNDATION STOPS BOTH BANDS, and the asymmetry that used to live
        # here was an artefact of what the keep-out USED to be.
        #
        # It was a PLANNING STAND-OFF -- a district or nominal circle, 100 m at
        # a town, 16.1 m at a watchtower -- and refusing the batter inside
        # something that large left the road's own wall standing far from any
        # building: MEASURED on T12, 4.1 m of it once the approach ramped up.
        # So the batter was allowed to taper into the stand-off and only the
        # carriageway was refused.
        #
        # THE KEEP-OUT IS NOW THE FOUNDATION ITSELF -- each pad's rectangle and
        # the samples that pad actually levelled -- and for a foundation the
        # ruling has no asymmetry in it: inside a per-building pad the pad
        # wins, because it is a building's footing.  MEASURED when this still
        # read `is_road`: T4's batter authored 28 samples inside
        # `stenvik-house-3`'s 20.3 x 15.2 m rectangle and 3 on ground
        # `stenvik-cottage-2` had levelled 0.1 m outside its own rectangle, and
        # `writer._pad_footprint_check` REFUSED the whole write at append time
        # -- it does not care which band a sample came from.  A rasteriser that
        # writes what the guard will refuse is not a rasteriser, it is a
        # 47-second way of finding out.
        #
        # The wall this used to prevent is now prevented by the thing that
        # should prevent it: the approach ramp grades the road to the pad's own
        # datum at its EDGE (`grade_into_foreign`), which is the ruling's other
        # half and is measured per pad in the junction report.
        if pad_keepouts.contains(wx, wz) is not None:
            return "pad"
        if any(abs(wx - px) <= PROTECT_CLEAR_M
               and abs(wz - pz) <= PROTECT_CLEAR_M
               for px, pz in (protected_pieces or ())):
            return "protected"
        if generated < WATER_LEVEL_M and any(
                math.hypot(wx - px, wz - pz) <= pr
                for px, pz, pr in (protected_discs or ())):
            return "underwater"
        if foreign_at is not None and foreign_at(wx, wz) is not None:
            return "foreign"
        if is_road:
            return None
        # --- batter-only gates ------------------------------------------
        # NEVER BATTER BELOW THE WATER LEVEL, anywhere, not only inside a
        # protected extent.  A CAUSEWAY raises a pond bed and is what a road
        # legitimately does; a BATTER beside a shoreline cuts the bank down
        # and drains the water out from under whatever stands over it, which
        # is the early-dock defect and the one unrepairable shape.  The
        # carriageway's own water rule stays scoped to protected structures
        # because a causeway has to be possible; the batter's does not,
        # because a batter is never load bearing and refusing it costs a
        # recorded wall.
        if generated < WATER_LEVEL_M:
            return "water_edge"
        # THE WIDENED FOOTPRINT RE-CHECKED AGAINST POI CLEARANCE.  The
        # ribbon's stand-offs were computed for a 7 m strip and a 38 deg
        # batter off an 8 m wall is ~10 m more per side, so they do not
        # transfer -- they are re-asked here, per sample, against each
        # instance's own `reach + MARGIN_M (+ PROTECTED_EXTRA_M)`.
        if any(math.hypot(wx - px, wz - pz) <= pr
               for px, pz, pr in (poi_keepouts or ())):
            return "poi"
        # A LIVE PLACED OBJECT'S GROUND MUST NOT MOVE EITHER, and this gate
        # is on the BATTER alone by construction: the carriageway's deltas
        # are unchanged by this repair, so they cannot newly float anything,
        # while every batter sample is ground that has never moved before.
        if any(abs(wx - px) <= PLACED_CLEAR_M
               and abs(wz - pz) <= PLACED_CLEAR_M
               for px, pz in (placed_objects or ())):
            return "placed"
        return None

    # ---- pass 1: the carriageway, and the batter collected into rays -----
    #
    # The carriageway is written here and now because its policy is per
    # sample: a refused carriageway sample is a recorded GAP that
    # `walkability` judges, and nothing about it depends on its neighbours.
    # The batter cannot be decided here, because whether a sample may be
    # written depends on every sample INBOARD of it on the same ray -- and
    # rays cross zone boundaries, so the decision cannot even be made one
    # zone at a time.
    rays: dict[tuple[int, int], list] = {}
    # NO BATTER WHERE THERE IS NO ROAD.  A station whose carriageway is
    # refused has no edge to taper from, and grading a 10 m apron around a
    # road that was never written is earthwork for nothing -- worse, on the
    # far side of a pad boundary it is a 10 m skirt around empty ground.
    road_stations: set[tuple[int, int]] = set()
    dead_stations: set[tuple[int, int]] = set()
    grids: dict[tuple[int, int], tuple] = {}
    ax_1d = (np.arange(tcdata.PITCH) - tcdata.PITCH // 2) * tcdata.SCALE
    for zx, zz in zones:
        patch = patches[f"z_{zx}_{zz}"]
        comp = tcdata.Compiler(zone_x=zx, zone_z=zz)
        zcx, zcz = comp.centre
        # The zone's own sample centres, exactly `tcdata.sample_world` with gx
        # varying along the row and gy down the column.
        X = (zcx + ax_1d)[None, :] + np.zeros((tcdata.PITCH, 1))
        Z = (zcz + ax_1d)[:, None] + np.zeros((1, tcdata.PITCH))
        LAT, Y, S, SGN, FX, FZ = lateral_and_y_grid(nodes, prof, br, X, Z,
                                                    reach)
        comps[(zx, zz)] = comp
        inband = np.argwhere(LAT <= reach)
        for gy, gx in inband:
            gy, gx = int(gy), int(gx)
            wx, wz = float(X[gy, gx]), float(Z[gy, gx])
            lat, y = float(LAT[gy, gx]), float(Y[gy, gx])
            generated = float(patch.at(gy, gx))
            full = y - generated
            # THE EDGE IS INCLUSIVE.  See EDGE_EPS_M: without the tolerance
            # the sample landing exactly on `edge` falls out of the
            # carriageway on a float ULP and keeps its generated height,
            # which is the 1 m wide, 1.76 m deep trench RoadClear measured at
            # T12 s = 116 m.
            if lat <= edge + EDGE_EPS_M:
                if lat > edge - EDGE_EPS_M:
                    st["edge_exact_samples"] += 1
                key = (int(SGN[gy, gx]), int(round(float(S[gy, gx]))))
                cause = refusal(wx, wz, generated, True)
                if cause is not None:
                    _clip(st, cause, True, f"skipped_{cause}",
                          wx, wz, lat, full)
                    dead_stations.add(key)
                    dead_stations.add((-key[0], key[1]))
                    continue
                road_stations.add(key)
                road_stations.add((-key[0], key[1]))
                comp.set_height(gx, gy, full)
                comp.set_paint(gx, gy, road_colour
                               if lat <= half + EDGE_EPS_M
                               else shoulder_colour)
                written.append((wx, wz))
                road_written.append((wx, wz))
                st["samples_paved" if lat <= half + EDGE_EPS_M
                   else "samples_shoulder"] += 1
                st["max_cut_m"] = min(st["max_cut_m"], full)
                st["max_fill_m"] = max(st["max_fill_m"], full)
                _clamp_check(st, full, zx, zz, gx, gy, wx, wz, generated, y,
                             "carriageway")
                continue
            key = (int(SGN[gy, gx]), int(round(float(S[gy, gx]))))
            rays.setdefault(key, []).append(
                (lat, zx, zz, gx, gy, wx, wz, y, generated,
                 float(FX[gy, gx]), float(FZ[gy, gx])))

    # ---- pass 2: each ray, outward, once -------------------------------
    for key, members in rays.items():
        members.sort()
        if key not in road_stations or key in dead_stations:
            st["rays_without_road"] += 1
            # AND WHERE, AND HOW DEEP, because the counter alone hides a wall.
            # A ray whose carriageway was refused is skipped -- correct, there
            # is no edge to taper from -- but the NEIGHBOURING station's batter
            # still ends at this ray's boundary, and the ground on this side is
            # untouched.  MEASURED on T4 at (518.8, 640.5): 1.826 m applied
            # against 0.362 m generated, one metre from a written batter sample
            # at the same lateral.  With nothing recorded, the transverse
            # census can only call that step UNEXPLAINED, which is the same
            # word it uses for a bug.  `full` is the cut or fill the profile
            # asks for at this station: the wall the road WOULD have had here,
            # which is the bound on what the neighbour's taper can leave.
            for lat_, _zx, _zz, _gx, _gy, wx_, wz_, y_, gen_, _fx, _fz in members:
                st["ray_skip_at"].append(
                    (wx_, wz_, "ray_without_road", round(abs(y_ - gen_), 3)))
            continue
        st["rays"] += 1
        lat0, zx0, zz0, _, _, _, _, y0, _, fx0, fz0 = members[0]
        # THE WALL AT THE HEAD OF THIS RAY, which is what the batter has to
        # beat and what bounds how much earth it may move.  Taken at the
        # carriageway EDGE rather than at the first batter sample, because
        # that is where the operator steps off.
        ux, uz = (members[0][5] - fx0) / lat0, (members[0][6] - fz0) / lat0
        ex, ez = fx0 + ux * edge, fz0 + uz * edge
        gen_edge = appliedmod.generated_at(patches, ex, ez)
        if gen_edge is None:
            gen_edge = members[0][8]
        full_edge = y0 - gen_edge
        if abs(full_edge) <= EDGE_EPS_M:
            # Road at grade with the ground: there is no wall here, so there
            # is nothing to batter and no reach to spend.
            continue
        sign = 1.0 if full_edge > 0 else -1.0
        budget = abs(full_edge)

        # ---- ONE CORRIDOR OR TWO ROADS?  Main's ruling, and it is a GATE
        # before it is a nicety.  Where another road segment's carriageway
        # lies inside this ray's batter reach, the two roads are ONE CORRIDOR:
        # a 0.781 plane off a 2 m wall needs 2.6 m per side and off a 5 m wall
        # 6.4 m, so two tapers plus two carriageways cannot fit in a 6 m gap.
        # MEASURED on T12: T3 runs 6-10 m away near the temple with its
        # carriageway edge 1.18 m off its own centreline, so an ungated batter
        # would TAPER INTO A FINISHED ROAD -- cutting up to 2.2 m out of T3's
        # surface and leaving a trench between the pair.  So:
        #   * if the two road profiles are within grade across the gap, the
        #     interstice is levelled FLUSH at the higher of the two;
        #   * if they are not, the interstice is LEFT ALONE with the step
        #     recorded and both profiles named -- an honest ditch beats a
        #     forced one, and the outside batter is unaffected either way.
        claim = None
        if road_claim_at is not None:
            for m in members:
                if m[0] > edge + batter_m:
                    break
                got = road_claim_at(m[5], m[6])
                if got is not None:
                    claim = (m[0], m[8] + got[0], got[1])
                    break
        if claim is not None:
            clat, their_y, their_name = claim
            gap = max(clat - edge, 1e-6)
            diff = y0 - their_y
            st["interstice_rays"] += 1
            flush = max(y0, their_y)
            fits = abs(diff) <= batter_grade * gap
            rec = {"xz": [round(members[0][5], 1), round(members[0][6], 1)],
                   "gap_m": round(gap, 2), "my_road_y": round(y0, 2),
                   "their_road_y": round(their_y, 2),
                   "their_claim": their_name,
                   "profile_diff_m": round(diff, 2),
                   "grade_can_absorb_m": round(batter_grade * gap, 2),
                   "action": "levelled_flush" if fits else "left_ungraded"}
            if len(st["interstice"]) < 40:
                st["interstice"].append(rec)
            if fits:
                st["interstice_apron"] += 1
                for lat, zx, zz, gx, gy, wx, wz, y, generated, _fx, _fz in members:
                    if lat >= clat:
                        break
                    cause = refusal(wx, wz, generated, False)
                    if cause is not None:
                        _clip(st, cause, False, None, wx, wz, lat, full_edge,
                              residual=flush - generated)
                        break
                    delta = flush - generated
                    if abs(delta) > max(budget, abs(diff)) + tcdata.CLAMP_M * 0.0 + EDGE_EPS_M:
                        _clip(st, "steeper_than_batter", False, None, wx, wz,
                              lat, full_edge, residual=delta)
                        break
                    comps[(zx, zz)].set_height(gx, gy, delta)
                    written.append((wx, wz))
                    st["samples_batter"] += 1
                    st["samples_apron"] += 1
                    st["max_cut_m"] = min(st["max_cut_m"], delta)
                    st["max_fill_m"] = max(st["max_fill_m"], delta)
                    _clamp_check(st, delta, zx, zz, gx, gy, wx, wz, generated,
                                 flush, "apron")
            else:
                st["interstice_left_ungraded"] += 1
                _clip(st, "shared_corridor", False, None,
                      members[0][5], members[0][6], members[0][0], full_edge,
                      residual=full_edge)
            continue

        # THE RAY'S OWN TERMINATION, recorded at whichever of the five exits
        # it takes.  `end` is the lateral the earthwork reaches, which for a
        # clipped ray is the REFUSED sample (the last one written is inside
        # it) and for a ray that met ground is where the plane arrived.
        #
        # `lattice_end` is the SENTINEL for "no exit has been taken yet" and
        # is never the recorded answer: the block after this loop resolves it
        # into `reach` or `bin_exhausted`.  It used to BE the answer, for 1 to
        # 90 rays per segment, because the reach exit that belongs here was
        # gated on `lat - edge > batter_m` while the member list is built from
        # `LAT <= edge + batter_m` -- the same bound, so no member could ever
        # satisfy it.  A dead guard reads as a live one, so the branch was
        # deleted rather than repaired in place: the reach is spent when the
        # MEMBER LIST ends at the cap, which is a fact about the last member
        # and is therefore decided once, after the walk, instead of tested
        # against every sample on every ray.
        end_lat, end_cause = members[-1][0], "lattice_end"
        for lat, zx, zz, gx, gy, wx, wz, y, generated, _fx, _fz in members:
            run = lat - edge
            cause = refusal(wx, wz, generated, False)
            if cause is not None:
                target = y - sign * batter_grade * run
                _clip(st, cause, False, None, wx, wz, lat, full_edge,
                      residual=target - generated)
                if cause == "placed" and len(st["batter_clipped_placed_at"]) < 20:
                    st["batter_clipped_placed_at"].append(
                        [round(wx, 1), round(wz, 1)])
                st["rays_clipped"] += 1
                end_lat, end_cause = lat, cause
                break
            # THE PLANE AT GRADE, and where it meets ground the ray is done.
            target = y - sign * batter_grade * run
            delta = target - generated
            if delta * sign <= 0.0:
                # The plane has reached or passed natural ground: the taper
                # ENDS AT GROUND, which is the whole point.  No wall, nothing
                # to record but the fact that it happened.
                st["batter_met_ground"] += 1
                end_lat, end_cause = lat, "met_ground"
                break
            if abs(delta) > budget + EDGE_EPS_M:
                # THE HILLSIDE IS STEEPER THAN THE BATTER.  Grading further
                # out would move MORE earth than the road itself moves at the
                # edge and still not arrive, so the batter stops and says so.
                # This is also what keeps the batter inside the +/-8 m apply
                # clamp without a second rule: the carriageway's own delta is
                # already inside it.
                _clip(st, "steeper_than_batter", False, None, wx, wz, lat,
                      full_edge, residual=delta)
                st["rays_clipped"] += 1
                end_lat, end_cause = lat, "steeper_than_batter"
                break
            comps[(zx, zz)].set_height(gx, gy, delta)
            written.append((wx, wz))
            st["samples_batter"] += 1
            if pad_keepouts.contains(wx, wz) is not None:
                st["samples_batter_in_pad_standoff"] += 1
            st["max_cut_m"] = min(st["max_cut_m"], delta)
            st["max_fill_m"] = max(st["max_fill_m"], delta)
            _clamp_check(st, delta, zx, zz, gx, gy, wx, wz, generated, target,
                         "batter")
        if end_cause == "lattice_end":
            # THE FIFTH EXIT, AND IT WAS UNREACHABLE.  The `reach` clip above
            # is gated on `lat - edge > batter_m`, i.e. `lat > reach`, and the
            # member list is built from `LAT <= reach` -- so no member can
            # ever satisfy it and a ray that spends its whole reach without
            # meeting ground falls out of this loop with NO clip, NO residual
            # and nothing for `edge_step_census` to consult.  MEASURED across
            # the twelve battered segments before this block existed: `reach`
            # occurs ZERO times and `lattice_end` 1 to 90 times per segment,
            # and T8's 9.192 m termination reversal at station 223 is one of
            # them.  A SHRINK IS SILENT: this is the batter narrowing its own
            # request and saying nothing, which is the same defect class as
            # the concentric removal-disc shrink.
            #
            # The two mechanisms are DIFFERENT FACTS and are recorded apart:
            #   * the outermost member sits within one lattice diagonal of the
            #     cap -> THE REACH IS SPENT, the plane is still `delta` above
            #     or below natural ground out there, and that residual is a
            #     wall like any other -- a `reach` clip, exactly as the source
            #     above already describes it;
            #   * the outermost member sits further inside -> this station's
            #     BIN ran out of samples (`int(round(arc))` scatters a
            #     diagonal centreline's transect across adjacent stations), so
            #     nothing refused this ray and it left no wall of its own: the
            #     ground outboard of it at this arc belongs to a neighbouring
            #     ray and is written there.  Recorded in `ray_skip_at` -- the
            #     same place a ray with no carriageway is recorded, and for
            #     the same reason: the neighbour's earthwork ends at this
            #     ray's boundary, so the census must be able to name it.
            lat_, _zx, _zz, _gx, _gy, wx_, wz_, y_, gen_, _fx_, _fz_ = (
                members[-1])
            residual_ = (y_ - sign * batter_grade * (lat_ - edge)) - gen_
            if reach - lat_ < LATTICE_DIAGONAL_M + EDGE_EPS_M:
                _clip(st, "reach", False, None, wx_, wz_, lat_, full_edge,
                      residual=residual_)
                st["rays_clipped"] += 1
                end_cause = "reach"
            else:
                st["rays_bin_exhausted"] += 1
                st["ray_skip_at"].append(
                    (wx_, wz_, "ray_bin_exhausted", round(abs(residual_), 3)))
                end_cause = "bin_exhausted"
            end_lat = lat_
        st["ray_end_at"].append((round(float(key[1]), 2), int(key[0]),
                                 round(float(end_lat), 3), end_cause))

    for z, comp in list(comps.items()):
        touched = sum(comp.modified_height) + sum(comp.modified_paint)
        if touched:
            st["per_zone"][f"{z[0]},{z[1]}"] = int(sum(comp.modified_height))
        else:
            del comps[z]
    st["max_cut_m"] = round(st["max_cut_m"], 3)
    st["max_fill_m"] = round(st["max_fill_m"], 3)
    st["written"] = written
    st["road_written"] = road_written
    return comps, st


def _clamp_check(st: dict, delta: float, zx: int, zz: int, gx: int, gy: int,
                 wx: float, wz: float, generated: float, target: float,
                 band: str) -> None:
    """THE CLAMP, CHECKED PER SAMPLE.

    Not per segment and not on the fitting lattice: this is the integer
    lattice the game will apply, and `TerrainComp::ApplyToHeightmap` silently
    discards anything past +/- 8 m of the generated height, so a sample over
    the limit is a piece of road that is not where the plan says.
    """
    if abs(delta) <= tcdata.CLAMP_M:
        return
    st["over_clamp"] += 1
    if len(st["over_clamp_samples"]) < 20:
        st["over_clamp_samples"].append(
            {"zone": [zx, zz], "sample": [gx, gy], "band": band,
             "world": [round(wx, 1), round(wz, 1)],
             "generated_y": round(generated, 2),
             "target_y": round(target, 2), "delta_m": round(delta, 2)})


def delta_at(comps: dict, x: float, z: float) -> float:
    """The height change this write applies AT an arbitrary world position.

    Not "the nearest written sample's delta": `Heightmap` renders a mesh that
    interpolates LINEARLY between adjacent samples at a 1 m pitch, so the
    ground under an off-lattice point moves by the bilinear blend of the four
    samples around it, and an UNWRITTEN sample contributes zero.  That is why a
    piece 0.9 m outside the ribbon edge still settles a little and one 1.1 m
    outside does not move at all -- and it is where `TERRAIN_SPREAD_M = 1.0`
    comes from.
    """
    x0, z0 = math.floor(x), math.floor(z)
    tx, tz = x - x0, z - z0
    total = 0.0
    for dx, dz, w in ((0, 0, (1 - tx) * (1 - tz)), (1, 0, tx * (1 - tz)),
                      (0, 1, (1 - tx) * tz), (1, 1, tx * tz)):
        if w == 0.0:
            continue
        sx, sz = x0 + dx, z0 + dz
        zx, zz = tcdata.zone_of(sx, sz)
        comp = comps.get((zx, zz))
        if comp is None:
            continue
        cx, cz = comp.centre
        gx, gy = tcdata.vertex_mask_index(cx, cz, sx, sz)
        if not (0 <= gx < tcdata.PITCH and 0 <= gy < tcdata.PITCH):
            continue
        k = gy * tcdata.PITCH + gx
        if comp.modified_height[k]:
            total += w * comp.level_delta[k]
    return total


def delta_gate(comps: dict, written: list[tuple[float, float]], L,
               tol_m: float, probe_step_m: float = 1.0) -> dict:
    """THE gate on a non-destructive terrain write: does any location piece's
    GROUND MOVE?

    Distance is a proxy; this is the thing itself.  A ribbon that deletes
    nothing can still leave a runestone hanging 3 m over a new road or buried
    under it, which is the operator's own condemning complaint arriving by a
    different route -- and it is the reason the loosened distance budget was
    granted.  A piece whose ground does not move is unaffected at ANY distance;
    one whose ground moves is damaged at any distance.

    Every instance within (its own reach + the terrain spread) of the written
    set is probed on a 1 m lattice over its own reach disc, and the worst
    absolute applied delta over that disc is compared to `tol_m`.  Probing the
    DISC rather than the marker matters: a location's pieces are spread over its
    reach (measured up to 35.77 m for TrollCave02), and the marker itself can be
    metres from the piece that would float.
    """
    reachable = L.instances_near(written, 60.0)
    worst, violations, probed = None, [], 0
    for inst in reachable:
        px, pz = inst["xz"]
        r = max(float(inst["reach_m"]), 1.0)
        n = max(1, int(r / probe_step_m))
        peak, peak_at = 0.0, (px, pz)
        for iz in range(-n, n + 1):
            for ix in range(-n, n + 1):
                qx, qz = px + ix * probe_step_m, pz + iz * probe_step_m
                if (qx - px) ** 2 + (qz - pz) ** 2 > r * r:
                    continue
                probed += 1
                d = abs(delta_at(comps, qx, qz))
                if d > peak:
                    peak, peak_at = d, (qx, qz)
        rec = {"name": inst["name"], "prefab": inst["prefab"],
               "xz": [round(px, 1), round(pz, 1)],
               "reach_m": inst["reach_m"], "protected": inst["protected"],
               "min_dist_m": inst["min_dist_m"],
               "worst_abs_delta_m": round(peak, 3),
               "worst_at": [round(peak_at[0], 1), round(peak_at[1], 1)]}
        if worst is None or peak > worst["worst_abs_delta_m"]:
            worst = rec
        if peak > tol_m:
            violations.append(rec)
    return {
        "verdict": "clear" if not violations else "VIOLATION",
        "tolerance_m": tol_m,
        "instances_probed": len(reachable),
        "positions_probed": probed,
        "worst": worst,
        "violations": violations,
        "method": ("MEASURED: for every location instance within 60 m of the "
                   "written set, the applied height delta is evaluated on a 1 m "
                   "lattice over that instance's own reach disc (reach is "
                   "max(exteriorRadius, interiorRadius, znviewReachM), up to "
                   "35.77 m) by bilinear blend of the four TerrainComp samples "
                   "around each probe, with unwritten samples contributing "
                   "zero -- which is how Heightmap renders the ground. The "
                   "worst absolute delta over the disc is compared to the "
                   "tolerance. Distance is a proxy for this; this is the thing "
                   "itself."),
        "tool": "tools/jumpstart/roads/ribbon.py::delta_gate",
    }


LOC_DUMPS = ("/tmp/settle/loc3/f6fe167f4fcd.json",
             "/tmp/settle/loc2/f6fe167f4fcd.json")


def _location_dump() -> str:
    p = next((q for q in LOC_DUMPS if Path(q).exists()), None)
    if p is None:
        raise SystemExit(
            "no location dump found: refusing to write terrain without a "
            "location check. MEASURED: flatten.py has no location check and "
            "cut 6.65 m of ground out from under a LocationProxy holding a "
            "buried treasure chest.")
    return p


def poi_standoff_discs(zones: list[tuple[int, int]], pad_m: float = 64.0
                       ) -> list[tuple[float, float, float]]:
    """Per-instance keep-out discs for the BATTER, in the canonical budget.

    `reach + TERRAIN_SPREAD_M` per instance -- exactly the non-destructive
    budget `clearance.violations_for_samples` applies, with the caller's own
    half-extent zero because a batter sample IS the footprint point rather than
    the centre of one.  The destructive `standoff` (which adds `MARGIN_M` and
    `PROTECTED_EXTRA_M`) is deliberately NOT used: MEASURED in that module,
    applying it to a 6 m road refused 25 of 34 segments including every segment
    out of StartTemple, and this op deletes nothing, so it is budgeting against
    a hazard it does not have.

    Returned as discs rather than as a verdict because the batter CLIPS per
    sample: one sample too close to a runestone must not cost the other 40 km
    of side slope.  Restricted to the segment's own zone bounding box plus a
    zone of slack, so a 12,301-instance world costs a few dozen comparisons
    per sample instead of twelve thousand.
    """
    sys.path.insert(0, str(JUMPSTART / "settlements"))
    import clearance

    L = clearance.load(_location_dump())
    xs = [zx * tcdata.ZONE_SIZE for zx, _ in zones]
    zs = [zz * tcdata.ZONE_SIZE for _, zz in zones]
    xlo, xhi = min(xs) - pad_m, max(xs) + pad_m
    zlo, zhi = min(zs) - pad_m, max(zs) + pad_m
    out: list[tuple[float, float, float]] = []
    for i in range(len(L.names)):
        x, z = float(L.xz[i, 0]), float(L.xz[i, 1])
        if not (xlo <= x <= xhi and zlo <= z <= zhi):
            continue
        out.append((x, z, float(L.reach[i]) + clearance.TERRAIN_SPREAD_M))
    return out

def location_check(comps: dict, written: list[tuple[float, float]],
                   half_width_m: float,
                   road_written: list[tuple[float, float]] | None = None
                   ) -> dict:
    """The clearance GATE, delegated to the canonical instrument, with the
    per-operation rule and the per-piece delta gate Main ruled.

    `tools/jumpstart/settlements/clearance.py` is canonical -- it is what
    `flatten.py`'s location gate and `Settlements`' `build.py` are wired to, and
    its reach is MEASURED PER TYPE rather than a declared radius plus an
    inferred constant.  A road passes `destructive=False`, which is a claim
    about the pipeline and an honest one: this tool emits `zones_generate` and
    `terrain_write` and NEVER `objects_clear`, so it cannot delete a location's
    ZDOs, which is the hazard `MARGIN_M` and `PROTECTED_EXTRA_M` budget for.
    The hazard it CAN cause -- a piece left floating or buried because its
    ground moved -- is gated directly by `delta_gate`, and `verdict_for_samples`
    REFUSES a non-destructive call that arrives without one.

    TWO DEFECTS ARE RECORDED HERE BECAUSE BOTH WERE MINE.
    (1) The first version measured each POI's distance to the ZONE CENTRE and
    returned a HARD-CODED `verdict: "clear"`.  It passed a StoneCircle 3.2 m
    from a zone centre against that circle's own 22 m radius, because the
    verdict was not computed from anything.  A check whose answer does not
    depend on its measurement is worse than none: it satisfies the gate.
    (2) The second measured the right thing but was a SECOND implementation,
    with an INFERRED 11 m overshoot where `clearance.py` had a per-type
    measurement.  Consolidating them did not just remove a duplicate -- it
    caught a WRONG VERDICT on the segment I was about to build first: my check
    called T3-temple-meadhall clear, the canonical gate refuses it with 5
    violations over 6,113 samples (StartTemple short by 84.9 m, Eikthyrnir by
    14.0 and 8.8, WoodHouse1 by 5.2, Dolmen01 by 2.7).
    """
    sys.path.insert(0, str(JUMPSTART / "settlements"))
    import clearance

    dump = next((p for p in ("/tmp/settle/loc3/f6fe167f4fcd.json",
                             "/tmp/settle/loc2/f6fe167f4fcd.json")
                 if Path(p).exists()), None)
    if dump is None:
        raise SystemExit(
            "no location dump found: refusing to write terrain without a "
            "location check. MEASURED: flatten.py has no location check and "
            "cut 6.65 m of ground out from under a LocationProxy holding a "
            "buried treasure chest.")
    L = clearance.load(dump)
    # TWO SAMPLE SETS, TWO QUESTIONS, and conflating them is what made the
    # batter look like a wider ROAD to the distance rule.  `delta_gate` gets
    # EVERY written sample, carriageway and batter alike, because its question
    # is "does any piece's ground move" and a batter sample moves ground.  The
    # distance rule gets the CARRIAGEWAY only, because its budget is
    # `half_width_m` -- the road's own geometry -- and a batter is earthwork
    # that tapers to nothing rather than a 29 m wide road.
    gate = delta_gate(comps, written, L, clearance.DELTA_TOL_M)
    v = L.verdict_for_samples(list(road_written if road_written is not None
                                   else written),
                              half_width_m=half_width_m,
                              destructive=False, delta_gate=gate)
    v["dump"] = dump
    v["destructive"] = False
    v["destructive_claim"] = (
        "this terrain op emits zones_generate + terrain_write only and never "
        "objects_clear, so it cannot delete a location's ZDOs; verifiable from "
        "the ledger records for this segment. Road-surface vegetation clearing "
        "is a SEPARATE op recorded by tools/jumpstart/roads/clear.py, which "
        "passes destructive=True to this same instrument and censuses each "
        "removal cylinder live in the same call")
    v["samples_tested_carriageway"] = len(road_written if road_written
                                          is not None else written)
    v["samples_in_delta_gate"] = len(written)
    return v


# ---------------------------------------------------------------------------
# the thing the operator judges: is it CONTINUOUS and WALKABLE
# ---------------------------------------------------------------------------

# Player::UpdateMovement slides the character when the ground normal is steeper
# than 38 deg, so the hard limit on a walkable surface is tan(38 deg).  A road
# the operator slides down is not a road.
SLIDE_ANGLE_DEG = 38.0
SLIDE_GRADIENT = math.tan(math.radians(SLIDE_ANGLE_DEG))
# Stated as an assertion rather than a comment: `BATTER_GRADE` is defined at
# the top of the file (it is needed by `segment_zones`' default reach) and it
# IS this limit.  Two spellings of one number is how they drift apart.
assert abs(BATTER_GRADE - SLIDE_GRADIENT) < 1e-12, (
    "BATTER_GRADE must be the 38 deg slide limit")
# The BASELINE THE VERDICT IS TAKEN OVER.  The 1 m figure is a DIFFERENT
# QUANTITY: it is the slope of one lattice edge, and a 1 m rise between two
# adjacent samples on a fitted 8% profile is a rounding artefact of the integer
# lattice, not a wall.  Measuring the verdict at 1 m produced a false
# NOT WALKABLE earlier tonight, so both are reported and the verdict is taken
# over 8 m -- roughly the run the capsule actually traverses while the collider
# averages the mesh under it.
VERDICT_BASELINE_M = 8.0


# THE SHORTEST RUN AN APPROACH RAMP MAY USE.  Was the only run: the reasoning
# read "the apply clamp is +/-8 m, so the largest step the road can possibly
# owe a pad is 8 m, and 8 / 0.781 = 10.24 m of run removes it at grade; one
# metre of margin and the ramp can always arrive."
#
# THE PREMISE IS FALSE AND IT WAS MEASURED FALSE IN THE SAME SESSION THAT
# WROTE IT.  The clamp bounds what ONE `terrain_write` may move the ground
# relative to the GENERATED height; it does not bound the step between a
# road's profile and a pad's levelled datum, because the pad spends a clamp
# budget of its own in the opposite direction.  MEASURED on T4 at Stenvik:
# residual step at the pad -10.414 m on a 207.2 m run from (539.8, 814.2),
# terminating on stenvik's levelled 45.57 m datum -- 2.4 m larger than the
# largest step this constant assumes can exist.  With the run capped at
# 11.5 m the ramp grade then saturates at `BATTER_GRADE`, which is the slide
# limit, and T4's 8 m longitudinal gradient came out at 0.8849: NOT WALKABLE.
# A player cannot climb a 10.4 m wall and slides back down the stretch before
# it, so the trunk road into the town ends at a cliff.
#
# So the reach is now DERIVED: the run the step needs at the road's own design
# grade, floored here and ceilinged by `APPROACH_RUN_CAP_M`.  T4's 10.414 m
# at 8 % wants 130 m and it has 207; the second run's 2.770 m over 69.7 m is
# 4 %.
APPROACH_MIN_M = 11.5
# ...AND BOUNDED, because a claim that cannot be met must not re-profile a
# whole segment silently.  This is the 120 m this function's own docstring
# already claimed, plus the margin T4's measured run needs; a step that wants
# more run than this keeps its residual and the residual is REPORTED.
APPROACH_RUN_CAP_M = 240.0


def approach_reach_m(worst_step_m: float, design_grade: float,
                     available_run_m: float) -> float:
    """The run an approach ramp gets: what the step needs at design grade,
    never below `APPROACH_MIN_M`, never above the segment's own length or
    `APPROACH_RUN_CAP_M`.

    Bounded by the AVAILABLE run and not only by a constant, because a ramp
    longer than the segment re-profiles nodes that belong to the other end.
    """
    want = abs(worst_step_m) / max(design_grade, 1e-3)
    return max(APPROACH_MIN_M,
               min(want, APPROACH_RUN_CAP_M, max(available_run_m, 1e-9)))


# HOW MANY REACHES THE SOLVE ASKS ABOUT, and it is a ladder rather than a
# bisection because the quantity being minimised is NOT monotone in the reach.
# MEASURED on T4 across the full ladder: the worst 8 m gradient runs 0.9219,
# 0.7763, 0.6798, 0.6230, 0.5890, 0.5322, 0.4963, 0.4717, 0.4826, 0.4583,
# 0.5021, 0.5093, 0.5144, 0.4847, 0.4686 -- it falls, flattens, and rises
# again.  A bisection on a non-monotone objective finds a local answer and
# reports it as the answer.
APPROACH_LADDER_STEPS = 14
# Two reaches whose worst 8 m gradient differ by less than this are the same
# road as far as the operator is concerned -- 0.02 is 2.6 % of the 0.781 slide
# limit -- so the tie is broken toward the LONGER reach, which is the gentler
# ramp and the smaller earthwork.
APPROACH_G8_TIE = 0.02


def solve_approach(seg: dict, patches: dict, live, pad_keepouts: "PadKeepOut",
                   rasterise) -> tuple[dict, dict, dict, dict]:
    """CHOOSE THE APPROACH REACH BY ASKING THE CLAMP, not by deriving it.

    THE DEFECT THIS EXISTS FOR, MEASURED, AND IT IS `APPROACH_MAX_M`'s DEFECT
    WEARING THE OPPOSITE HAT.  That constant was 11.5 m on a false premise
    about the apply clamp, and the fix replaced it with a run DERIVED from the
    step at the design grade -- `step / design`, bounded by the segment's own
    length and `APPROACH_RUN_CAP_M`.  That derivation is about GRADE and
    LENGTH, and the quantity that actually binds is neither: it is the FILL
    the write is allowed to deliver.

    MEASURED on T4 the first time it rasterised through Stenvik's district:
    the derived reach is 79.9 m (a 6.391 m step at 8 %), the cone that reach
    imposes lifts 175 profile nodes by up to 6.861 m, and five samples at
    (583-584, 998-1000) land 8.01-8.13 m above the generated ground -- past
    `tcdata.CLAMP_M` 8.0, which `TerrainComp::ApplyToHeightmap` silently
    discards.  The write is REFUSED, correctly, and a gentler ramp is the
    cause: a long approach holds the road high far from the claim, so it fills
    a valley 40 m out that a shorter, steeper approach simply walks down.  At
    67.9 m the same segment is inside the clamp; at 18.5 m its worst 8 m
    gradient is 0.4583 against the 0.781 limit.

    So the reach is SOLVED against the instrument that decides it.  The
    alternative is to predict which reaches clamp, and this project has paid
    for that shape three times over: a closed-form shrink radius skipped six
    cylinders the gate itself passes, and `APPROACH_MAX_M` was a number
    justified by a proof about a different quantity.  `rasterise(seg)` must
    return `(comps, st, walk)` for the profile currently on `seg`.

    THE RULE, in order, every term measured by the call:
      1. HARD -- `st["over_clamp"]` must be 0.  This is not a preference: a
         write past the clamp is refused at validation and the road would not
         be where the plan says.
      2. Among those, MINIMISE `walk["max_gradient_8m"]`.  That is the
         operator's reported defect ("slides back down") and one of the two
         acceptance criteria.
      3. Tie inside `APPROACH_G8_TIE` -> the LONGER reach.

    THE STEP AT THE PAD IS RECORDED PER CANDIDATE AND IS NOT THE OBJECTIVE,
    and the measurement is why.  MEASURED on T4 over the whole ladder, the two
    acceptance criteria move in OPPOSITE directions: the worst 8 m gradient
    falls from 0.9219 to 0.4583 as the reach shortens, while the worst step at
    a pad edge RISES from 1.078 m (at 67.9 m of reach) to 1.408 m and beyond.
    No reach on the ladder brings the step under `BATTER_GRADE`, because it is
    set by the DATUM DIFFERENCE between adjacent foundations -- `stenvik-hut-1`
    at 47.95 m and `stenvik-longhouse-2` at 46.20 m, 17.7 m of centreline
    apart -- and a ramp cannot move a foundation.  So the solve optimises the
    criterion it CAN meet, records the floor of the one it cannot, and the
    tie-break toward the longer reach is also the direction that reduces the
    pad step.  Optimising a knob against a quantity it does not control is how
    a repair reports success and changes nothing.

    Returns `(approach, comps, st, solve)`; `seg["profile_y"]` is left holding
    the winning profile.
    """
    base_profile = list(seg["profile_y"])
    design = float(seg.get("grade_limit") or 0.08)

    def attempt(reach_m: float | None):
        seg["profile_y"] = list(base_profile)
        ap = grade_into_foreign(seg, patches, live, pad_keepouts,
                                reach_m=reach_m)
        comps, st, walk = rasterise(seg)
        return ap, comps, st, walk

    # The derived reach is the TOP of the ladder: there is no merit in a ramp
    # gentler than the step needs at design grade.
    ap0, comps0, st0, walk0 = attempt(None)
    if not ap0["claims"]:
        return ap0, comps0, st0, {
            "reaches_tried": 0, "chosen_m": None,
            "why": "no claim on this segment, so there is no approach to solve",
            "tool": "tools/jumpstart/roads/ribbon.py::solve_approach"}
    hi = float(ap0["reach_m"])
    # The BOTTOM is the shortest reach that can still remove the step at the
    # batter's own grade: below it the ramp cannot arrive at all, so the
    # residual is structural rather than chosen.
    lo = max(abs(ap0["worst_step_before_m"]) / BATTER_GRADE, 1.0)
    if lo >= hi:
        rows = [{"reach_m": round(hi, 1), "over_clamp": st0["over_clamp"],
                 "g8": walk0.get("max_gradient_8m"),
                 "ramp_grade": ap0.get("ramp_grade"),
                 "step_after_m": ap0["worst_step_after_m"]}]
        return ap0, comps0, st0, {
            "reaches_tried": 1, "chosen_m": round(hi, 1), "rows": rows,
            "why": ("the derived reach is already the shortest that can remove "
                    "this step at BATTER_GRADE, so there is no ladder"),
            "tool": "tools/jumpstart/roads/ribbon.py::solve_approach"}

    ladder = sorted({round(hi * (lo / hi) ** (i / APPROACH_LADDER_STEPS), 1)
                     for i in range(APPROACH_LADDER_STEPS + 1)}, reverse=True)

    def worst_pad_step(walk: dict):
        """The worst step between the road at a pad boundary and that pad's
        own levelled datum -- the second acceptance criterion, recorded per
        candidate so the trade-off against the gradient is visible."""
        steps = [(g["terminates_at"]["step_road_to_pad_m"],
                  g["terminates_at"]["site_id"])
                 for g in walk.get("gaps", [])
                 if g.get("terminates_at")
                 and g["terminates_at"].get("step_road_to_pad_m") is not None]
        if not steps:
            return None, None, 0
        s, sid = max(steps, key=lambda t: abs(t[0]))
        return s, sid, sum(1 for v, _ in steps if abs(v) > BATTER_GRADE)
    rows = []
    best = None
    for reach in ladder:
        if abs(reach - hi) < 1e-9:
            ap, comps, st, walk = ap0, comps0, st0, walk0
        else:
            ap, comps, st, walk = attempt(reach)
        g8 = float(walk.get("max_gradient_8m") or 0.0)
        row = {"reach_m": reach, "ramp_grade": ap.get("ramp_grade"),
               "nodes_regraded": ap["nodes_regraded"],
               "max_profile_move_m": ap.get("max_profile_move_m"),
               "step_after_m": ap["worst_step_after_m"],
               "over_clamp": st["over_clamp"],
               "max_fill_m": st["max_fill_m"], "max_cut_m": st["max_cut_m"],
               "g8": round(g8, 4), "verdict": walk.get("verdict"),
               "stations_on_road": walk.get("stations_on_road")}
        row["worst_pad_step_m"], row["worst_pad_step_at"], \
            row["pads_over_batter_grade"] = worst_pad_step(walk)
        rows.append(row)
        if st["over_clamp"]:
            continue
        if best is None:
            best = (g8, reach, ap, comps, st, walk)
            continue
        # Rule 2 then rule 3: strictly better gradient wins; a gradient inside
        # the tie band yields to the longer reach.  The ladder descends, so the
        # incumbent is always the longer of any tied pair.
        if g8 < best[0] - APPROACH_G8_TIE:
            best = (g8, reach, ap, comps, st, walk)

    if best is None:
        # EVERY REACH CLAMPS.  The tightest is returned so the caller can
        # report the refusal against the smallest earthwork that produced it,
        # rather than against the largest.
        ap, comps, st, walk = attempt(ladder[-1])
        return ap, comps, st, {
            "reaches_tried": len(rows), "chosen_m": ladder[-1], "rows": rows,
            "feasible": False,
            "why": ("NO reach keeps this segment inside the +/-8 m apply "
                    "clamp; the shortest is returned so the refusal names the "
                    "smallest earthwork that still fails"),
            "tool": "tools/jumpstart/roads/ribbon.py::solve_approach"}

    g8, reach, ap, comps, st, walk = best
    seg["profile_y"] = list(base_profile)
    ap, comps, st, walk = attempt(reach)
    return ap, comps, st, {
        "reaches_tried": len(rows), "chosen_m": reach, "rows": rows,
        "feasible": True,
        "derived_reach_m": round(hi, 1), "shortest_reach_m": round(lo, 1),
        "design_grade": design, "clamp_m": tcdata.CLAMP_M,
        "chosen_g8": round(g8, 4),
        "why": ("the longest reach, inside the gradient tie band, whose "
                "rasterisation the +/-8 m apply clamp accepts -- asked of "
                "`stamp` per candidate, never predicted"),
        "tool": "tools/jumpstart/roads/ribbon.py::solve_approach"}


def grade_into_foreign(seg: dict, patches: dict, live,
                       pad_keepouts: "PadKeepOut",
                       grade: float | None = None,
                       station_step_m: float = 1.0,
                       reach_m: float | None = None) -> dict:
    """RE-PROFILE THE APPROACH SO THE ROAD ARRIVES AT WALKABLE GROUND INSTEAD
    OF ENDING AT A CLIFF.  Returns a report; MUTATES `seg["profile_y"]`.

    THE TWO DEFECTS THIS EXISTS FOR, both measured and both in the record
    before anybody read them:

      1. THE ROAD SIMPLY STOPS AT A PAD.  T4 has 333 unwritten centreline
         metres in four runs (204, 68, 41, 5 m); the 204 m run is the stathub
         pad keep-out, and seq 213's OWN meta already records
         `step_across_m -7.644` at s = 888.3.  A 7.6 m step at the end of a
         trunk road, measured, recorded, and shipped.
      2. A FOREIGN PAD WRITE OWNS CARRIAGEWAY SAMPLES.  MEASURED with
         `applied.py`: 89 of T4's metres sit on samples `SiteFinish` seq 286
         overwrote after `RoadBuild` seq 213, and the applied surface jumps
         45.05 -> 47.11 in one metre mid-road.

    THE RULING IS MAIN'S AND IT IS NOT SYMMETRIC: inside the pad's footprint
    the PAD WINS -- it is a building's foundation and it must stay flat -- and
    the road owns the approach.  So this never restores a road delta inside a
    pad.  It moves the ROAD'S OWN target profile, in the metres OUTSIDE the
    claim, until the two surfaces meet.

    HOW: every station that is not ours -- inside a pad keep-out disc, or
    holding a sample a non-road write owns -- contributes a CONE constraint
    `|y(s) - H| <= grade * |s - s_claim|`, where H is the LIVE APPLIED SURFACE
    there, read off the ledger's own blobs.  The new profile is the planned
    one clamped into the intersection of those cones.  Three properties follow:
    a station already within grade of the claim is untouched (the clamp is
    inactive); the ramp is exactly as long as the step needs and no longer;
    and where two claims at different heights are too close to satisfy both,
    the conflict is REPORTED with its residual step rather than split silently.

    THE HEIGHT IS THE LIVE SURFACE, NOT THE PAD'S DATUM, and that distinction
    is the whole reason the defect survived.  `pad_y` in sites.yaml is what a
    pad was ASKED to be levelled to; what the road has to meet is what the
    ground IS.  For the T4 clobber those differ, because the surface there is
    a pad write's samples unioned over a road write's.
    """
    # THE RAMP GRADE IS THE ROAD'S OWN DESIGN GRADE, NOT THE BATTER'S, and
    # Main's ruling ("grade the last metres into the pad at BATTER_GRADE") is
    # the CEILING rather than the target.  MEASURED why the ceiling is the
    # wrong target: ramping T12's 2.99 m pad step at 0.781 took the
    # longitudinal 1 m gradient from 0.169 to 0.6446 -- within the slide limit
    # but only just -- and because the road TURNS through that ramp, the
    # batter plane beside it warps: points 9 m out along one station's normal
    # project to a station 1-2 m away, so the transverse census read 1.686 m
    # steps that are the ramp's own longitudinal fall leaking sideways.  At
    # the road's 8 % the same step needs 37 m of run, which the road has, and
    # both effects vanish.  The batter's grade stays the ceiling for the case
    # where the run genuinely is not there.
    design = float(seg.get("grade_limit") or 0.08)
    want_grade = grade
    nodes = seg["nodes"]
    prof = list(seg["profile_y"])
    br = seg["is_bridge"]
    # Arc length per node over the TERRAIN polyline, the same convention
    # `lateral_and_y_grid` uses.
    arc = [0.0] * len(nodes)
    acc = 0.0
    for k in range(len(nodes) - 1):
        arc[k] = acc
        if not (br[k] or br[k + 1]):
            acc += math.hypot(nodes[k + 1][0] - nodes[k][0],
                              nodes[k + 1][1] - nodes[k][1])
    arc[-1] = acc
    # THE RUN A RAMP GETS IS DERIVED FROM THE STEP, so it cannot be decided
    # here: the step is not known until the claims have been measured.  It is
    # computed below, once, beside the grade it determines.

    # ---- the claims, measured station by station -----------------------
    claims: list[dict] = []
    s = 0.0
    k = 0
    while s <= acc + 1e-9:
        while k < len(nodes) - 2 and arc[k + 1] <= s:
            k += 1
        if br[k] or br[k + 1]:
            s += station_step_m
            continue
        span = max(arc[k + 1] - arc[k], 1e-9)
        f = min(max((s - arc[k]) / span, 0.0), 1.0)
        x = nodes[k][0] + (nodes[k + 1][0] - nodes[k][0]) * f
        z = nodes[k][1] + (nodes[k + 1][1] - nodes[k][1]) * f
        y = prof[k] + (prof[k + 1] - prof[k]) * f
        cause = None
        owner = pad_keepouts.contains(x, z)
        if owner is not None:
            # NAMED, because "pad_keepout" alone cannot say WHICH foundation,
            # and a district's name is no longer the answer -- the claim is a
            # per-building rectangle and the report has to say which building.
            cause = "pad_keepout"
        if cause is None and live is not None:
            w = live.foreign_at(x, z)
            if w is not None:
                cause = "foreign_write"
                owner = f"{w['name']}#{w['seq']}"
        if cause is not None:
            surf = (live.surface_at(patches, x, z)
                    if live is not None else None)
            # A CLAIM IS GROUND SOMEBODY HAS ACTUALLY WRITTEN, NOT A CIRCLE ON
            # A MAP, and this is the second time tonight that distinction
            # decided a number.  A pad radius over UNWRITTEN ground is
            # natural hillside: MEASURED on T12, 11 stations inside
            # wt-spawn's stand-off read a live surface 2.991 m ABOVE the
            # planned profile purely because the road CUTS through that hill.
            # Ramping up to meet it re-profiled 56 nodes, turned a 4.58 m fill
            # into a 7.02 m embankment and pushed the transverse wall at the
            # pad boundary from 2.60 m to 5.09 m -- a repair that made the
            # defect worse, in service of meeting a surface nobody had built.
            # So a claim requires a non-zero live delta: somebody's earthwork,
            # not somebody's intention.
            if surf is not None and abs(surf[2]) > 1e-6:
                claims.append({"s": round(s, 1), "xz": [round(x, 1), round(z, 1)],
                               "cause": cause, "owner": owner,
                               "live_y": round(surf[0], 3),
                               "planned_y": round(y, 3),
                               "step_m": round(y - surf[0], 3)})
        s += station_step_m
    # THE RAMP: THE RUN COMES FROM THE STEP, THE GRADE COMES FROM THE RUN.
    #
    # The previous order was the other way round and it is what shipped T4's
    # cliff: the run was a constant 11.5 m, so the grade was whatever a 11.5 m
    # ramp needed, which for a 10.414 m step is 0.905 -- clipped to
    # `BATTER_GRADE`, the slide limit -- and 1.4 m of the step was still left
    # over at the boundary.  A ramp at the slide limit is a ramp the player
    # slides down, and T4's measured 8 m longitudinal gradient came out 0.8849
    # against a 0.781 limit.
    #
    # Now the run is `step / design` (T4: 10.414 / 0.08 = 130 m, and it has
    # 207) bounded by the segment's own length and `APPROACH_RUN_CAP_M`, and
    # the grade is `step / run` -- so it lands ON the design grade whenever
    # the run is there, and only steepens toward the batter ceiling when it is
    # not.  Never gentler than design: there is no merit in a flatter road
    # than was fitted.
    #
    # THE T12 REGRESSION THIS CONSTANT WAS SHRUNK TO AVOID CANNOT RECUR, and
    # the reason is a DIFFERENT fix that landed after it.  That regression was
    # ramping UP to meet ground nobody had built: 11 stations inside
    # wt-spawn's stand-off whose live surface sat 2.991 m above the profile
    # purely because the road CUTS a hill there, which at design grade over
    # 37 m re-profiled 56 nodes, turned a 4.58 m fill into a 7.02 m
    # embankment and pushed the transverse wall 2.60 -> 5.09 m.  A claim now
    # REQUIRES a non-zero live delta -- "somebody's earthwork, not somebody's
    # intention", the test twenty lines above -- so those 11 stations are no
    # longer claims at all and there is no ramp to lengthen.  MEASURED after
    # this change: T12 regrades the same nodes it did before.
    if claims:
        worst = max(abs(c["step_m"]) for c in claims)
        if reach_m is None:
            reach_m = approach_reach_m(worst, design, acc)
        if want_grade is None:
            grade = min(max(worst / reach_m, design), BATTER_GRADE)
        else:
            grade = min(max(want_grade, 1e-3), BATTER_GRADE)
    else:
        if reach_m is None:
            reach_m = APPROACH_MIN_M
        grade = min(max(design if want_grade is None else want_grade, 1e-3),
                    BATTER_GRADE)
    if not claims:
        return {"claims": 0, "nodes_regraded": 0, "ramps": [],
                "worst_step_before_m": 0.0, "worst_step_after_m": 0.0,
                "tool": "tools/jumpstart/roads/ribbon.py::grade_into_foreign"}

    # ---- clamp the profile into every claim's cone ----------------------
    regraded = 0
    moved_max = 0.0
    conflicts: list[dict] = []
    for i in range(len(nodes)):
        if br[i]:
            continue
        si = arc[i]
        lo, hi = -1e30, 1e30
        lo_c = hi_c = None
        for c in claims:
            d = abs(si - c["s"])
            if d > reach_m:
                continue
            h = c["live_y"]
            if h - grade * d > lo:
                lo, lo_c = h - grade * d, c
            if h + grade * d < hi:
                hi, hi_c = h + grade * d, c
        if lo_c is None and hi_c is None:
            continue
        want = prof[i]
        if lo > hi:
            # TWO CLAIMS AT DIFFERENT HEIGHTS TOO CLOSE TOGETHER: no profile
            # satisfies both at grade.  Meet them halfway and record the
            # residual, because a silent split is how a 2 m step gets shipped.
            mid = (lo + hi) / 2.0
            conflicts.append({"node": i, "s": round(si, 1),
                              "between": [lo_c["xz"], hi_c["xz"]],
                              "residual_step_m": round((lo - hi) / 2.0, 3)})
            new = mid
        else:
            new = min(max(want, lo), hi)
        if abs(new - want) > 1e-6:
            prof[i] = new
            regraded += 1
            moved_max = max(moved_max, abs(new - want))
    seg["profile_y"] = prof

    worst_before = max(abs(c["step_m"]) for c in claims)
    # AFTER, on the regraded profile, by the same instrument.
    after = []
    for c in claims:
        si = c["s"]
        kk = 0
        while kk < len(nodes) - 2 and arc[kk + 1] <= si:
            kk += 1
        span = max(arc[kk + 1] - arc[kk], 1e-9)
        f = min(max((si - arc[kk]) / span, 0.0), 1.0)
        y = prof[kk] + (prof[kk + 1] - prof[kk]) * f
        c["regraded_y"] = round(y, 3)
        c["step_after_m"] = round(y - c["live_y"], 3)
        after.append(abs(y - c["live_y"]))
    return {
        "claims": len(claims),
        "claims_by_cause": {k: sum(1 for c in claims if c["cause"] == k)
                            for k in sorted({c["cause"] for c in claims})},
        "owners": sorted({c["owner"] for c in claims if c["owner"]}),
        "nodes_regraded": regraded,
        "max_profile_move_m": round(moved_max, 3),
        "worst_step_before_m": round(worst_before, 3),
        "worst_step_after_m": round(max(after) if after else 0.0, 3),
        "conflicts": conflicts,
        "ramp_grade": round(grade, 4),
        "ramp_grade_is": ("the segment's own design grade"
                          if abs(grade - design) < 1e-9
                          else "steeper than design, capped at BATTER_GRADE"),
        "design_grade": design,
        "batter_grade_ceiling": round(BATTER_GRADE, 4),
        "reach_m": round(reach_m, 1),
        "claims_detail": claims[:80],
        "method": ("MEASURED: the live applied surface from the ledger's own "
                   "blobs (union per zone in file order + generated, blended "
                   "bilinearly as Heightmap renders it) at every 1 m station "
                   "the road does not own, then the planned profile clamped "
                   "into each claim's +/-BATTER_GRADE cone. The pad keeps "
                   "every sample it owns; only the ROAD's target height "
                   "moves."),
        "tool": "tools/jumpstart/roads/ribbon.py::grade_into_foreign",
    }


def edge_step_census(seg: dict, comps: dict, patches: dict,
                     station_step_m: float = 2.0, lat_max_m: float = 24.0,
                     lat_step_m: float = 1.0,
                     batter_m: float = BATTER_MAX_M,
                     grade: float = BATTER_GRADE,
                     mine: set | None = None,
                     clips: list | None = None) -> dict:
    """THE NUMBER THE OPERATOR'S "BLOCKY" COMPLAINT IS ABOUT: the worst 1 m
    TRANSVERSE step on the applied surface, per station, and where it falls.

    `walkability` walks ALONG the centreline and answers "is this hill too
    steep to climb".  It cannot see a wall beside the road, and the wall beside
    the road is what a level bench on a side slope has: measured over the 15
    standing segments before the batter existed, the step one metre outside the
    shoulder ran 0.39-1.46 m median and up to 8.06 m on 42-89 % of each
    segment.  A check that walks the centreline reported every one of those
    segments walkable, which is true and was not the question.

    THE VERDICT IS TAKEN AGAINST `BATTER_GRADE`, NOT AGAINST A FLAT 0.5 m, and
    the flat number is the trap.  A correctly battered segment's 1 m
    transverse step IS the design grade -- 0.781 m per metre at 38 deg -- so a
    0.5 m threshold fails every segment that was repaired properly and sends
    the next reader chasing a non-defect.  MEASURED on T12 after the first
    batter: p50 0.83 m, which a 0.5 m checker calls a wall and which is in
    fact the slope, plus the hillside the old delta-ramp form added on top of
    it (see BATTER_GRADE).

    AND THE VERDICT SET IS THE GROUND THIS WRITE TOUCHED, not a fixed lateral
    band.  Sampling out to `lat_max_m` is deliberate -- it shows where the
    ground goes -- but a natural cliff at lat 21 is not a wall the road left,
    and a band-based rule reported exactly that on T12 before this.  So every
    adjacent PAIR is classified by whether either of its samples has a
    MODIFIED corner under it (`applied_at` returns that count), which is
    precisely "is this step on ground we moved".  Three disjoint sets come
    out: the carriageway, the EARTHWORK (the verdict), and natural ground
    (reported, never judged).
    """
    nodes = np.array(seg["nodes"], dtype=np.float64)
    br = np.array(seg["is_bridge"], dtype=bool)
    half = seg["width_m"] / 2.0
    edge = half + SHOULDER_M

    # EVERY RECORDED CLIP, ON A 2 m GRID, so an over-grade step can be asked
    # "did this write already record a reason for you".  2 m because the census
    # samples the cross section at 1 m and a clip sits on the lattice: one cell
    # either way covers the bilinear footprint of the step being judged.
    clipgrid: dict[tuple[int, int], list] = {}
    for cx_, cz_, cause_, h_ in (clips or ()):
        clipgrid.setdefault((int(cx_ // 2), int(cz_ // 2)), []).append(
            (cx_, cz_, cause_, h_))

    def clip_near(x: float, z: float, r: float = 2.5):
        best = None
        for gx_ in (int(x // 2) - 1, int(x // 2), int(x // 2) + 1):
            for gz_ in (int(z // 2) - 1, int(z // 2), int(z // 2) + 1):
                for cx_, cz_, cause_, h_ in clipgrid.get((gx_, gz_), ()):
                    d = math.hypot(x - cx_, z - cz_)
                    if d <= r and (best is None or d < best[0]):
                        best = (d, cause_, h_)
        return None if best is None else {"cause": best[1],
                                          "recorded_wall_m": best[2],
                                          "dist_m": round(best[0], 2)}

    # TOLERANCE, AND WHY IT IS NOT ZERO: the cross section is sampled at a 1 m
    # pitch along a direction that is NOT the lattice axis, so each reading is
    # a bilinear blend of four samples and a step measured across a diagonal
    # picks up the LONGITUDINAL grade as well.  On an 8 % road that is at most
    # 0.08 m of the 0.781, so the allowance is the road's own design grade
    # rather than an invented number.
    tol = float(seg.get("grade_limit") or 0.12)
    limit = grade + tol

    lats = np.arange(-lat_max_m, lat_max_m + 1e-9, lat_step_m)
    worst_road = {"step_m": 0.0}
    worst_toe = {"step_m": 0.0}
    worst_work = {"step_m": 0.0}
    worst_junction = {"step_m": 0.0}
    toe_steps: list[float] = []
    road_steps: list[float] = []
    work_steps: list[float] = []
    nat_steps: list[float] = []
    junction_steps: list[float] = []
    junctions: list[dict] = []
    over_grade: list[dict] = []
    explained = 0
    unexplained = 0
    stations = 0
    uncovered = 0

    acc = 0.0
    for k in range(len(nodes) - 1):
        if br[k] or br[k + 1]:
            continue
        ax, az = nodes[k]
        bx, bz = nodes[k + 1]
        seglen = math.hypot(bx - ax, bz - az)
        if seglen <= 1e-9:
            continue
        t = station_step_m - acc
        while t < seglen:
            f = t / seglen
            cx, cz = ax + (bx - ax) * f, az + (bz - az) * f
            nx, nz = -(bz - az) / seglen, (bx - ax) / seglen
            ys = []
            mods = []
            owns = []
            for lat in lats:
                got = applied_at(comps, patches, cx + nx * lat, cz + nz * lat,
                                 mine)
                ys.append(None if got is None else got[0])
                mods.append(0 if got is None else got[2])
                owns.append(0 if got is None else got[3])
            if any(v is None for v in ys):
                uncovered += 1
            else:
                stations += 1
                arr = np.asarray(ys, dtype=np.float64)
                d = np.abs(np.diff(arr))
                md = np.asarray(mods, dtype=np.int32)
                mn = np.asarray(owns, dtype=np.int32)
                mid = (lats[:-1] + lats[1:]) / 2.0
                on_road = np.abs(mid) <= edge + EDGE_EPS_M
                touched = (md[:-1] > 0) | (md[1:] > 0)
                # A step is MINE when a sample this write wrote is on one
                # side of it.  It is a JUNCTION when the only written samples
                # involved belong to someone else -- a neighbouring segment's
                # carriageway or a settlement pad carried in by the union.
                # Grading that is the neighbour's re-emit, not this one's, and
                # judging this segment on it fails a correct segment.
                ownpair = (mn[:-1] > 0) | (mn[1:] > 0)
                # A pair straddling MY sample and SOMEBODY ELSE'S is a
                # junction wherever it sits, carriageway included.  MEASURED
                # on T12 at (75.2, -65.2): a 2.602 m step at lat 3.5, between
                # a sample this write wrote and one it refuses because the
                # site's pad radius GREW since the first build (sites.yaml's
                # 100 m town against spec.py's provisional 12 m).  The old
                # road's samples inside the new keep-out are the pad's ground
                # now, by Main's ruling, and the road may not take them back.
                mixed = ((mn[:-1] > 0) & (md[1:] > mn[1:])) | \
                        ((mn[1:] > 0) & (md[:-1] > mn[:-1]))
                work = ownpair & ~on_road & ~mixed
                junction = (touched & ~ownpair & ~on_road) | mixed
                nat = ~touched & ~on_road
                toe = edge + batter_m + 1.0
                outside = (np.abs(mid) > edge) & (np.abs(mid) <= toe)
                on_road = on_road & ~mixed
                if on_road.any():
                    j = int(np.argmax(np.where(on_road, d, -1.0)))
                    road_steps.append(float(d[j]))
                    if d[j] > worst_road["step_m"]:
                        worst_road = {"step_m": round(float(d[j]), 3),
                                      "xz": [round(cx, 1), round(cz, 1)],
                                      "lat_m": round(float(mid[j]), 1)}
                if outside.any():
                    j = int(np.argmax(np.where(outside, d, -1.0)))
                    toe_steps.append(float(d[j]))
                    if d[j] > worst_toe["step_m"]:
                        worst_toe = {"step_m": round(float(d[j]), 3),
                                     "xz": [round(cx, 1), round(cz, 1)],
                                     "lat_m": round(float(mid[j]), 1)}
                if work.any():
                    j = int(np.argmax(np.where(work, d, -1.0)))
                    work_steps += [float(v) for v in d[work]]
                    if d[j] > worst_work["step_m"]:
                        worst_work = {"step_m": round(float(d[j]), 3),
                                      "xz": [round(cx, 1), round(cz, 1)],
                                      "lat_m": round(float(mid[j]), 1)}
                    for jj in np.flatnonzero(work & (d > limit)):
                        jj = int(jj)
                        px_ = cx + nx * float(mid[jj])
                        pz_ = cz + nz * float(mid[jj])
                        why = clip_near(px_, pz_)
                        if why is None:
                            unexplained += 1
                        else:
                            explained += 1
                        if len(over_grade) < 60:
                            over_grade.append(
                                {"step_m": round(float(d[jj]), 3),
                                 "xz": [round(cx, 1), round(cz, 1)],
                                 "lat_m": round(float(mid[jj]), 1),
                                 # WHERE THE STEP IS, not where the station
                                 # is.  `xz` is the station centre and `lat_m`
                                 # is measured along THAT station's normal, so
                                 # on a ribbon that doubles back two steps 19 m
                                 # apart across the road share an `xz` -- and
                                 # an adjacency test clustering on `xz` then
                                 # reads a bend's cross section as a cliff.
                                 # MEASURED on T4: 41 unexplained steps
                                 # clustered to a run of 8 on station centres
                                 # and to runs of at most 2 on these.
                                 "step_xz": [round(px_, 1), round(pz_, 1)],
                                 # AND THE TWO SAMPLES IT IS BETWEEN, so a
                                 # consumer can re-measure the same step on
                                 # another surface -- the GENERATED one -- at
                                 # exactly these points.  Reconstructing them
                                 # from the station and the lat requires the
                                 # normal, which is not in the record, and
                                 # guessing the steepest direction instead
                                 # overstates the natural step and excuses a
                                 # wall this write made.
                                 "step_between": [
                                     [round(cx + nx * float(lats[jj]), 2),
                                      round(cz + nz * float(lats[jj]), 2)],
                                     [round(cx + nx * float(lats[jj + 1]), 2),
                                      round(cz + nz * float(lats[jj + 1]), 2)]],
                                 "recorded_clip": why})
                if nat.any():
                    nat_steps += [float(v) for v in d[nat]]
                if junction.any():
                    j = int(np.argmax(np.where(junction, d, -1.0)))
                    junction_steps += [float(v) for v in d[junction]]
                    if d[j] > worst_junction["step_m"]:
                        worst_junction = {"step_m": round(float(d[j]), 3),
                                          "xz": [round(cx, 1), round(cz, 1)],
                                          "lat_m": round(float(mid[j]), 1)}
                    if d[j] > grade and len(junctions) < 40:
                        junctions.append(
                            {"step_m": round(float(d[j]), 3),
                             "xz": [round(cx, 1), round(cz, 1)],
                             "lat_m": round(float(mid[j]), 1)})
            t += station_step_m
        acc = seglen - (t - station_step_m)

    def pct(vals, q):
        return round(float(np.percentile(vals, q)), 3) if vals else None

    # TOLERANCE, AND WHY IT IS NOT ZERO (restated where it is used): the cross section is sampled at a 1 m
    # pitch along a direction that is NOT the lattice axis, so each reading is
    # a bilinear blend of four samples and a step measured across a diagonal
    # picks up the LONGITUDINAL grade as well.  On an 8 % road that is at most
    # 0.08 m of the 0.781, so the allowance is stated as the road's own design
    # grade rather than invented.
    tol = float(seg.get("grade_limit") or 0.12)
    limit = grade + tol
    return {
        "stations": stations,
        "stations_without_generated_cover": uncovered,
        "lat_span_m": [float(-lat_max_m), float(lat_max_m)],
        "verdict_band": ("every adjacent 1 m pair outside the carriageway "
                         "with a sample THIS WRITE wrote under it; pairs "
                         "whose only written sample belongs to another claim "
                         "are reported as junction_* and are that claim's to "
                         "grade"),
        "carriageway_step_p50": pct(road_steps, 50),
        "carriageway_step_p95": pct(road_steps, 95),
        "carriageway_step_max": worst_road,
        "earthwork_step_p50": pct(work_steps, 50),
        "earthwork_step_p95": pct(work_steps, 95),
        "earthwork_step_max": worst_work,
        "earthwork_pairs": len(work_steps),
        "earthwork_over_grade_frac": (
            round(float(np.mean(np.asarray(work_steps) > limit)), 4)
            if work_steps else None),
        "earthwork_over_grade_at": over_grade,
        "natural_ground_step_p50": pct(nat_steps, 50),
        "natural_ground_step_max": (round(max(nat_steps), 3)
                                    if nat_steps else None),
        "junction_step_p50": pct(junction_steps, 50),
        "junction_step_max": worst_junction,
        "junction_pairs": len(junction_steps),
        "junction_over_grade_at": junctions,
        "outside_edge_step_p50": pct(toe_steps, 50),
        "outside_edge_step_p95": pct(toe_steps, 95),
        "outside_edge_step_max": worst_toe,
        "outside_edge_over_0_5m_frac": (
            round(float(np.mean(np.asarray(toe_steps) > 0.5)), 3)
            if toe_steps else None),
        "outside_edge_over_1_0m_frac": (
            round(float(np.mean(np.asarray(toe_steps) > 1.0)), 3)
            if toe_steps else None),
        "batter_grade_limit": round(grade, 4),
        "diagonal_tolerance_m": round(tol, 4),
        "verdict_limit_m": round(limit, 4),
        "earthwork_over_grade_explained_by_a_recorded_clip": explained,
        "earthwork_over_grade_UNEXPLAINED": unexplained,
        "verdict": ("no_earthwork" if not work_steps
                    else "pass" if worst_work["step_m"] <= limit
                    else "pass_with_recorded_clips" if unexplained == 0
                    else "UNEXPLAINED_STEPS"),
        "method": ("MEASURED: the applied surface (generated bilinear + "
                   "delta_at, i.e. how Heightmap composes the mesh) sampled "
                   "across the ribbon at 1 m lateral pitch every 2 m of "
                   "centreline, reporting the largest adjacent-sample "
                   "difference inside the carriageway, on the ground this "
                   "write MODIFIED, and on untouched natural ground, "
                   "separately. The verdict is the earthwork set against "
                   "BATTER_GRADE plus the segment's own longitudinal grade "
                   "as a diagonal allowance -- NOT a flat 0.5 m, which fails "
                   "a correctly battered segment. This is the transverse "
                   "quantity; walkability's gradients are the longitudinal "
                   "one and cannot see a wall beside the road."),
        "tool": "tools/jumpstart/roads/ribbon.py::edge_step_census",
    }


def applied_at(comps: dict, patches: dict, x: float, z: float,
               mine: set | None = None):
    """The height the game will RENDER at (x, z), and how much of it is road.

    Returns `(applied_y, generated_y, corners_modified, corners_mine)` or None
    when the generated lattice around the point is not in hand.  This is
    `generated bilinear + delta_at`, which is exactly `Heightmap`'s own
    composition: the mesh interpolates linearly between 1 m samples and an
    unwritten sample contributes zero delta.  Reading the PROFILE instead would
    answer what the plan intended rather than what the player stands on -- and
    the difference IS the edge, the pad gap and the ribbon end.

    `corners_mine` EXISTS TO STOP THIS SEGMENT ANSWERING FOR ANOTHER
    SEGMENT'S WALL, and it was a measured misattribution.  After the union a
    compiler holds every neighbour's samples too, so the cross section at T12
    s = 116 m reads a 3.676 m wall at lat -6.5 -- which is T3's carriageway
    edge 1.18 m off T3's OWN centreline, running 6-10 m from T12 near the
    temple.  Judging T12's batter on it would fail a correct segment and hide
    the actual owner.  Pass the `{(zone, index)}` set THIS write wrote and
    every step can be attributed.
    """
    x0, z0 = math.floor(x), math.floor(z)
    tx, tz = x - x0, z - z0
    gen = 0.0
    modified = 0
    own = 0
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
        gen += w * float(patch.at(gy, gx))
        comp = comps.get((zx, zz))
        k = gy * tcdata.PITCH + gx
        if comp is not None and comp.modified_height[k]:
            modified += 1
            if mine is None or ((zx, zz), k) in mine:
                own += 1
    return gen + delta_at(comps, x, z), gen, modified, own


def walkability(seg: dict, comps: dict, patches: dict,
                pad_keepouts: "PadKeepOut",
                protected_pieces: list[tuple[float, float]] | None = None,
                protected_discs: list[tuple[float, float, float]] | None = None,
                road_clips: list[tuple] | None = None,
                step_m: float = 0.5) -> dict:
    """Walk the centreline on the APPLIED surface and report the numbers the
    operator's own test produces: is the ribbon CONTINUOUS, is every part of it
    WALKABLE, and does it MEET what it terminates at.

    Continuity is measured as the set of RUNS where the centreline is not on
    written road, each classified by cause, with the height STEP across it.  A
    bridged run is expected -- the deck is Crossings' and the ribbon stops at
    the bank by construction.  A pad run is expected too: the ribbon stops at a
    Settlements pad edge rather than levelling ground under a building.
    Anything else is a hole in the road.

    A GAP IS NOT A SLOPE AND MUST NOT BE AVERAGED INTO ONE.  The verdict
    gradient is taken over 8 m between stations whose whole run is road,
    because that is the question "is this hill too steep to walk up".  At a
    boundary the question is different -- "can I get from the pad onto the
    road" -- and the answer is a STEP over one lattice edge, which an 8 m
    average would divide by eight and hide.  So each boundary is reported as a
    step, in metres, against the MEASURED pad datum of the site it terminates
    at (`pad_y` from settlements/sites.yaml, the height Settlements levels that
    pad to) rather than against the generated ground that is there today.  The
    generated ground is a temporary answer; the datum is the one the junction
    will actually have.
    """
    nodes = np.array(seg["nodes"], dtype=np.float64)
    br = np.array(seg["is_bridge"], dtype=bool)

    # Densify the centreline by arclength, carrying each station's cause tags.
    stations: list[dict] = []
    s = 0.0
    for k in range(len(nodes) - 1):
        ax, az = nodes[k]
        bx, bz = nodes[k + 1]
        seglen = math.hypot(bx - ax, bz - az)
        if seglen <= 1e-9:
            continue
        n = max(1, int(math.ceil(seglen / step_m)))
        bridged = bool(br[k] or br[k + 1])
        for j in range(n):
            t = j / n
            x, z = ax + (bx - ax) * t, az + (bz - az) * t
            stations.append({"s": s + seglen * t, "x": x, "z": z,
                             "bridged": bridged})
        s += seglen
    ax, az = nodes[-1]
    stations.append({"s": s, "x": float(ax), "z": float(az),
                     "bridged": bool(br[-1])})

    # THE RASTERISER'S OWN REFUSALS, indexed by the lattice sample they were
    # taken at.  `stamp` records one entry per carriageway sample it could not
    # write, as `(wx, wz, cause, full)`; the samples are integers, so an exact
    # dict keyed on them needs no radius and no tolerance.
    refusal_index: dict[tuple[int, int], dict] = {}
    for wx, wz, cause, full in (road_clips or ()):
        refusal_index[(int(round(wx)), int(round(wz)))] = {
            "cause": cause, "full_m": full,
            "at": [round(wx, 1), round(wz, 1)]}
    off_lattice = 0
    for st in stations:
        got = applied_at(comps, patches, st["x"], st["z"])
        if got is None:
            st["y"] = None
            off_lattice += 1
        else:
            st["y"], st["gen"], st["mod"], st["mine"] = got
        # PAD ADJACENCY IS DECIDED BY THE SAMPLES, NOT BY THE STATION CENTRE.
        # `stamp` skips a SAMPLE whose world position is inside a pad disc, and
        # a station whose centre sits just outside the disc can still have one
        # of its four corner samples inside it -- so the station is not on
        # written road for a reason that IS the pad.  MEASURED on
        # T10-wtspawn-wtsouth: one such station at (520.0, 36.8), 1 m outside
        # wt-south's 16.1 m radius, was classified a HOLE and refused the whole
        # segment. Testing the corners is exact and needs no margin constant.
        x0, z0 = math.floor(st["x"]), math.floor(st["z"])
        st["in_pad"] = any(
            pad_keepouts.contains(x0 + dx, z0 + dz) is not None
            for dx in (0, 1) for dz in (0, 1))
        # The same reasoning for the other two things that stop the rasteriser:
        # a station is not on road BECAUSE of a protected piece, or BECAUSE the
        # ground there is seabed, and both are explanations rather than holes.
        st["near_protected"] = any(
            abs(x0 + dx - px) <= PROTECT_CLEAR_M
            and abs(z0 + dz - pz) <= PROTECT_CLEAR_M
            for dx in (0, 1) for dz in (0, 1)
            for px, pz in (protected_pieces or ()))
        st["underwater"] = (
            st.get("gen") is not None and st["gen"] < WATER_LEVEL_M
            and any(math.hypot(st["x"] - px, st["z"] - pz) <= pr
                    for px, pz, pr in (protected_discs or ())))
        # WHAT THE RASTERISER ITSELF SAID, and it is the authority here.  The
        # three tests above ask circles and piece lists whether a station
        # OUGHT to have been refused; this asks `stamp` what it ACTUALLY
        # refused and why, keyed on the station's own four lattice samples.
        # The difference is not academic: a site pad's WRITTEN footprint is
        # not its nominal `pad_radius_m`, so on T4 the 47 m of centreline that
        # `tree-sth-2` seq 669 owns is outside every pad circle and inside
        # somebody's foundation. Modelling the gate got that wrong in the
        # direction that refuses a whole segment.
        st["refused"] = None
        if road_clips:
            for cx0 in (x0, x0 + 1):
                for cz0 in (z0, z0 + 1):
                    hit = refusal_index.get((cx0, cz0))
                    if hit is not None:
                        st["refused"] = hit
                        break
                if st["refused"]:
                    break
        # ON ROAD means all four samples under the point are ones this write
        # set: that is where the rendered surface IS the fitted profile rather
        # than a blend of road and untouched ground.
        st["on_road"] = st["y"] is not None and st.get("mod") == 4

    def grade_over(baseline: float) -> dict:
        """Worst |rise/run| between two ON-ROAD stations `baseline` apart."""
        worst = {"gradient": 0.0, "at": None, "rise_m": 0.0, "run_m": baseline,
                 "pairs": 0}
        j = 0
        for i, a in enumerate(stations):
            if not a["on_road"]:
                continue
            if j < i:
                j = i
            while j + 1 < len(stations) and stations[j]["s"] - a["s"] < baseline:
                j += 1
            b = stations[j]
            run = b["s"] - a["s"]
            if run < baseline * 0.9 or not b["on_road"]:
                continue
            # A pair that straddles a gap is not a slope, it is a step, and it
            # is reported as a step below.  Require the whole run to be road.
            if any(not stations[k]["on_road"] for k in range(i, j + 1)):
                continue
            worst["pairs"] += 1
            g = abs(b["y"] - a["y"]) / run
            if g > worst["gradient"]:
                worst.update({"gradient": round(g, 4),
                              "rise_m": round(b["y"] - a["y"], 3),
                              "run_m": round(run, 2),
                              "at": [round(a["x"], 1), round(a["z"], 1)]})
        return worst

    g8 = grade_over(VERDICT_BASELINE_M)
    g1 = grade_over(1.0)

    # Runs of centreline that are NOT on written road.
    gaps = []
    i = 0
    while i < len(stations):
        if stations[i]["on_road"]:
            i += 1
            continue
        j = i
        while j + 1 < len(stations) and not stations[j + 1]["on_road"]:
            j += 1
        before = stations[i - 1] if i > 0 and stations[i - 1]["on_road"] else None
        after = (stations[j + 1]
                 if j + 1 < len(stations) and stations[j + 1]["on_road"] else None)
        run = stations[j]["s"] - stations[i]["s"] + step_m
        # WHAT THE RASTERISER REFUSED OVER THIS RUN, by cause and by count.
        # Recorded whatever the verdict is, because a gap the road stops at
        # for a measured reason and a gap nobody explained are the same shape
        # on the ground and must never read the same in the report.
        refused_here: dict[str, int] = {}
        refused_worst = None
        for k in range(i, j + 1):
            rf = stations[k].get("refused")
            if not rf:
                continue
            refused_here[rf["cause"]] = refused_here.get(rf["cause"], 0) + 1
            if (refused_worst is None
                    or abs(rf["full_m"]) > abs(refused_worst["full_m"])):
                refused_worst = rf
        cause = ("bridge" if any(stations[k]["bridged"] for k in range(i, j + 1))
                 else "settlement_pad" if any(stations[k]["in_pad"]
                                              for k in range(i, j + 1))
                 else "protected_structure" if any(stations[k]["near_protected"]
                                                   for k in range(i, j + 1))
                 else "below_water_plane" if any(stations[k]["underwater"]
                                                 for k in range(i, j + 1))
                 # A FOREIGN CLAIM IS AN EXPLANATION, NOT A HOLE, and it is
                 # the ruling: inside somebody's written footprint the pad
                 # wins and the road grades into its edge.  Measured from the
                 # blobs by `stamp`, never inferred from a radius.
                 else "foreign_claim" if refused_here.get("foreign")
                 else "protected_structure" if refused_here.get("protected")
                 else "below_water_plane" if refused_here.get("underwater")
                 else "settlement_pad" if refused_here.get("pad")
                 else "ribbon_end" if i == 0 or j == len(stations) - 1
                 else "HOLE")
        step = None
        if before is not None and after is not None:
            step = round(after["y"] - before["y"], 3)
        # The step a walker actually meets is at the SHOULDER EDGE, between the
        # last road station and the untouched ground half a metre on: report the
        # applied-vs-generated difference at the boundary stations, which is the
        # height the ground moves there.
        lip = []
        for st in (before, after):
            if st is not None:
                lip.append(round(st["y"] - st["gen"], 3))
        entry = {"cause": cause,
                 "from_s_m": round(stations[i]["s"], 1),
                 "length_m": round(run, 1),
                 "xz": [round(stations[i]["x"], 1), round(stations[i]["z"], 1)],
                 "step_across_m": step,
                 "fill_at_edges_m": lip,
                 "rasteriser_refused": refused_here or None,
                 "rasteriser_refused_worst": refused_worst}
        # WHAT THE JUNCTION WILL ACTUALLY BE.  A pad gap is the road stopping
        # at somebody else's earthwork, so the step the operator meets is
        # road-surface against that FOUNDATION'S OWN DATUM.
        #
        # THE FOUNDATION, NOT THE SETTLEMENT.  Matching a gap to a site by
        # `pad_radius_m` and quoting the site's `pad_y` answered all six of
        # T4's Stenvik gaps "stenvik, 45.57 m, pad_levelled_yet False" -- one
        # number for a district `sites.yaml` itself says is NOT levelled, on
        # foundations that were levelled at seqs 61-311.  `pad_at` answers
        # with the building whose rectangle the gap is in and the `target_y`
        # that building's own write levelled it to.
        pad = None
        for k in range(i, j + 1):
            pad = pad_keepouts.pad_at(stations[k]["x"], stations[k]["z"])
            if pad is not None:
                break
        if pad is not None and pad.get("pad_y") is not None:
            edge = before if before is not None else after
            road_y = edge["y"] if edge is not None else None
            entry["terminates_at"] = {
                "site_id": pad["site_id"], "pad_seq": pad["seq"],
                "pad_y": pad["pad_y"], "pad_xz": pad["xz"],
                "pad_half_extents_m": pad["half_extents_m"],
                "road_y_at_pad_edge": None if road_y is None else round(road_y, 3),
                "step_road_to_pad_m": (None if road_y is None
                                       else round(pad["pad_y"] - road_y, 3)),
                "pad_levelled_yet": pad["levelled"],
                "datum_source": pad["datum_source"],
                "why": "the road surface at the pad boundary against the height "
                       "that pad's OWN write levelled it to. Positive means the "
                       "pad floor stands ABOVE the road and the operator has to "
                       "climb it; a step over ~0.5 m cannot be walked up.",
            }
        gaps.append(entry)
        i = j + 1

    holes = [g for g in gaps if g["cause"] == "HOLE"]
    on_road = sum(1 for st in stations if st["on_road"])
    verdict = ("WALKABLE" if g8["gradient"] <= SLIDE_GRADIENT and not holes
               else "NOT WALKABLE")
    return {
        "verdict": verdict,
        "max_gradient_8m": g8["gradient"],
        "max_gradient_8m_at": g8["at"],
        "max_gradient_8m_rise_m": g8["rise_m"],
        "baselines_8m_pairs": g8["pairs"],
        "max_gradient_1m": g1["gradient"],
        "max_gradient_1m_at": g1["at"],
        "slide_limit": round(SLIDE_GRADIENT, 3),
        "centreline_stations": len(stations),
        "stations_on_road": on_road,
        "stations_off_generated_lattice": off_lattice,
        "gaps": gaps,
        "holes": len(holes),
        "method": (
            "MEASURED on the APPLIED surface, not the plan: the centreline is "
            f"walked at {step_m} m and each station's height is the bilinear "
            "blend of the four generated samples around it PLUS this write's "
            "bilinear delta (unwritten samples contribute zero), which is how "
            "Heightmap composes the mesh. The VERDICT gradient is taken over a "
            f"{VERDICT_BASELINE_M} m baseline between two stations whose whole "
            "run is on written road; the 1 m figure is reported beside it and "
            "is a DIFFERENT QUANTITY -- one lattice edge -- which is why it is "
            "not the verdict. Limit is tan(38 deg), the MEASURED slide angle."),
        "tool": "tools/jumpstart/roads/ribbon.py::walkability",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("op", choices=["plan", "build"])
    ap.add_argument("--segment", required=True)
    ap.add_argument("--segments", default=str(HERE / "segments.yaml"))
    ap.add_argument("--preflight-out",
                    help="append this segment's validated pre-flight record to "
                         "a JSON file. The pre-flight IS the replay evidence: it "
                         "names the zones, the sample counts, the worst cut and "
                         "fill, the clamp result, both clearance verdicts and the "
                         "blob digests, so a future regeneration can tell whether "
                         "it is rebuilding the same road before it sends a "
                         "command.")
    ap.add_argument("--validate", action="store_true",
                    help="build the ledger records and run schema.validate on "
                         "them OFFLINE. Nothing is appended and nothing is "
                         "sent. This is the safe pre-flight: LiveBuilder(dry=1) "
                         "still APPENDS its records and still runs the "
                         "postcondition against the live server, so a dry run "
                         "leaves failed attempts in the shared ledger and "
                         "cannot tell you whether a terrain_write validates.")
    ap.add_argument("--dry", action="store_true",
                    help="validate the ledger records and the wire strings "
                         "without sending: LiveBuilder(dry=True). Run this "
                         "BEFORE taking a live console window -- the schema "
                         "refuses a malformed terrain_write at append time, and "
                         "finding that out while holding the console is how a "
                         "staged operation gets left half-sent.")
    ap.add_argument("--zone-batch", type=int, default=8,
                    help="zones per zones_generate call")
    ap.add_argument("--placed-objects",
                    help="JSON map of LIVE placed-object positions produced by "
                         "roads/clear.py --census: {\"objects\": [[x, z], ...]}. "
                         "The batter is new ground movement over ground that "
                         "already carries seated objects, and MEASURED by "
                         "SeatCheck the hub portal u-lheast already floats "
                         "0.536 m because a ribbon cut under it after it was "
                         "seated. Without this file the batter is REFUSED, "
                         "because an ungated batter is that defect at scale.")
    ap.add_argument("--no-batter", action="store_true",
                    help="rasterise the carriageway only, no side slopes. This "
                         "reproduces the pre-repair geometry and exists to "
                         "MEASURE it, not to ship it.")
    ap.add_argument("--repair-of", type=int,
                    help="the seq of the terrain_write this run REPAIRS. A "
                         "repair that does not name what it repairs is "
                         "indistinguishable from a first build, and the "
                         "ledger is the only place the operator can find out "
                         "which one he is standing on.")
    ap.add_argument("--repair-before",
                    help="JSON file holding the MEASURED before-state of the "
                         "surface being repaired (the step census off the "
                         "live blobs). Recorded verbatim in meta so the "
                         "repair has a verifiable baseline rather than a "
                         "claim -- the same shape the T12 pothole census took "
                         "before its repair.")
    ap.add_argument("--actor", required=True,
                    help="the agent id that is ACTUALLY running this pass, "
                         "used for every ledger record this run appends. "
                         "Stated rather than defaulted: the module constant "
                         "is the PatchScan sandbox's name and reusing it for "
                         "provenance records work under the name of an agent "
                         "that yielded hours earlier.")
    args = ap.parse_args()

    doc = yaml.safe_load(Path(args.segments).read_text())
    segs = [s for s in doc["segments"] if s["id"] == args.segment]
    if not segs:
        raise SystemExit(f"no segment {args.segment} in {args.segments}; have "
                         f"{[s['id'] for s in doc['segments']]}")
    seg = segs[0]

    pois = poimod.load()
    # SETTLEMENTS' OWN FILE IS THE AUTHORITY FOR THE DATUM, AND THE LEDGER IS
    # THE AUTHORITY FOR THE FOOTPRINT.  Main ruled
    # tools/jumpstart/settlements/sites.yaml authoritative and it is the
    # boundary -- but the two things it carries are not the same kind of fact.
    #
    # `pad_y` is a DECLARATION: 45.57 m is the height stenvik's pads will be
    # levelled to and the height T4 has to arrive at, and before a pad is
    # written that file is the only place the number exists.  It stays.
    #
    # `pad_radius_m` is a PLANNING STAND-OFF and `plan.py`'s own docstring
    # says it is "taken from the district or the pad plus its own margin" --
    # two different quantities behind one name.  Reading it as a foundation
    # cost T4 293.9 m of unwritten centreline; see `PadKeepOut`.  So the
    # keep-out is now built from the pad claims the WRITE-TIME GUARD will
    # judge this write against, with the declared radius kept only for a site
    # that has no live pad at all.
    sites_path = JUMPSTART / "settlements" / "sites.yaml"
    if sites_path.exists():
        site_list = [dict(s) for s in
                     yaml.safe_load(sites_path.read_text())["sites"]]
        pad_source = str(sites_path)
    else:
        site_list = [{"id": sid, "xz": list(s["xz"]),
                      "pad_radius_m": s["pad_radius_m"], "pad_y": s.get("pad_y")}
                     for sid, s in specmod.SETTLEMENT_SITES.items()]
        pad_source = "spec.SETTLEMENT_SITES (PROVISIONAL fallback)"
    sys.path.insert(0, str(JUMPSTART / "ledger"))
    from writer import Ledger  # noqa: PLC0415
    _led_for_pads = Ledger.open("Ulfsland", actor=args.actor)
    pad_keepouts = pad_keepouts_from_chain(
        site_list, _led_for_pads.records(), ledger=_led_for_pads)
    if pad_keepouts.undecodable:
        raise SystemExit(
            f"REFUSING: {len(pad_keepouts.undecodable)} pad terrain_write "
            f"entry(ies) could not be decoded, so the ground those pads "
            f"levelled is unknown: {pad_keepouts.undecodable[:6]}. "
            f"`writer._pad_footprint_check` fails CLOSED on exactly this, so "
            f"rasterising now buys a refusal at append time. Fix the blob "
            f"store first.")
    print(f"pad keep-outs: {pad_keepouts.describe()}; "
          f"datums from {pad_source}")
    batter_m = 0.0 if args.no_batter else BATTER_MAX_M
    zones = segment_zones(seg["nodes"], seg["is_bridge"], seg["width_m"],
                          batter_m=batter_m)
    print(f"{seg['id']}: {seg['length_m']} m, {seg['width_m']} m wide + "
          f"{SHOULDER_M} m shoulder + {batter_m} m batter reach, "
          f"{len(zones)} zones = {len(zones)} _TerrainCompiler ZDOs")

    out = SCRATCH / f"{seg['id']}.bin"
    patches = zone_patches(zones, SEED, out)
    prot = protected_piece_positions(zones)
    print(f"protected pieces in these zones: {len(prot['pieces'])} "
          f"{prot['by_site'] or '{}'}"
          + (f"; records with NO recoverable piece: {prot['without_pieces']}"
             if prot["without_pieces"] else ""))
    # THE LIVE PLACED-OBJECT MAP.  Read before stamping because the batter is
    # gated per sample on it, and a gate applied after the fact is a report
    # rather than a gate.
    placed: list[tuple[float, float]] = []
    placed_source = "none"
    if args.placed_objects:
        pdoc = json.loads(Path(args.placed_objects).read_text())
        placed = [(float(o[0]), float(o[1])) for o in pdoc["objects"]]
        placed_source = f"{args.placed_objects} ({pdoc.get('method', '?')})"
    elif batter_m:
        raise SystemExit(
            "REFUSING to write a batter without --placed-objects: the batter "
            "moves ground that has never moved before, and the one recorded "
            "instance of that hazard tonight (portal u-lheast, 0.536 m float) "
            "happened precisely because nothing measured what was standing on "
            "it. Run roads/clear.py --census first, or pass --no-batter.")
    print(f"placed objects gating the batter: {len(placed)} from {placed_source}")
    # THE WIDENED FOOTPRINT'S OWN POI KEEP-OUTS.  The ribbon's clearance was
    # computed for a 7 m strip; a 38 deg batter off an 8 m wall is ~10 m more
    # per side, so the stand-offs are re-asked for the widened region rather
    # than assumed to transfer. Discs, not a verdict: the batter CLIPS per
    # sample, so what it needs from the instrument is a geometry it can test a
    # single sample against.
    poi_keepouts = poi_standoff_discs(zones) if batter_m else []
    print(f"POI keep-out discs gating the batter: {len(poi_keepouts)}")

    # ---- THE LIVE SURFACE, BEFORE ANYTHING IS DECIDED ------------------
    #
    # Read first, because two of this repair's three jobs are answers to what
    # is ALREADY in the world rather than to what the plan says: which samples
    # a foreign write owns (so the road stops fighting a pad for them), and
    # what height the road has to arrive at when it meets one.  Deriving
    # either from radii or from `pad_y` measures the intention instead of the
    # ground, and the T4 clobber is 89 m of road that proves the difference.
    live = appliedmod.Applied(actor=ACTOR)
    # RULE 1, MAIN'S RULING, NOW IMPLEMENTED AS AUTHORSHIP RATHER THAN AS A
    # NAME FILTER -- AND THE NAME FILTER WAS WRONG IN BOTH DIRECTIONS.
    #
    # THE UNION LAUNDERS OWNERSHIP.  A repair MUST union every prior claim in
    # a zone forward into its own blob -- a zone holds exactly one
    # `_TerrainCompiler` and this op does `deleteObjects -zone` first, so
    # anything it does not carry is destroyed.  The record then stores that
    # same union, so a pad's floor inside a road's blob reads as
    # `role: road_segment` under the road's name, and `foreign_at` -- the
    # instrument that implements "inside a settlement pad the pad wins", and
    # which is deliberately blind to radii because that is what caught T4's
    # clobber -- cannot see the pad any more.
    #
    # MEASURED on T8-stenvik-wttown: its write (seq 1197) refused 123
    # carriageway samples because a site_pad owned them; the identical
    # rasterisation run against the surface that write produced finds ZERO.
    # Those samples are a building's foundation and a second repair would
    # pave them.
    #
    # THE FIRST FIX WAS TO DROP THIS SEGMENT'S OWN ZONE ENTRIES FROM THE
    # OWNERSHIP VIEW, so the per-sample owner fell back to the previous claim
    # in file order.  It cures the T8 direction and CAUSES THE MIRROR DEFECT,
    # measured on S1-portalhub-brgs2: the portal hall's pad seq 801 unioned
    # S1's carriageway forward VERBATIM (sample (-300, 214) is -1.694 in both
    # blobs, to the bit), so with S1's own entries dropped the hall became the
    # first writer of that sample in its zone lattice and read as its author.
    # `foreign_at` then answered `portal-hall#801` over the whole 30.6 m ramp
    # and the rasteriser refused all 215 carriageway samples and stamped ZERO
    # -- the first surface a player walks off a portal, unrepairable.  No name
    # filter can fix that, because the laundered copy is under the HALL's
    # name.
    #
    # SO THE QUESTION IS ASKED PROPERLY INSTEAD: `applied.py` now composes an
    # AUTHOR per sample -- the last claim whose value DIFFERED from the state
    # the claims before it had composed -- alongside the holder, and every
    # ownership answer comes from the author.  Nothing is dropped from the
    # composition, so a laundered copy can never become a first write; a pad's
    # floor is attributed to the pad even inside a road's blob, and a road's
    # carriageway is attributed to the road even inside a pad's blob.  Both
    # directions fall out of one measurement.
    print(f"live surface: {len(live.writes)} terrain_write zone entries in the "
          f"ledger, {len({w['zone'] for w in live.writes})} zones claimed; "
          f"OWNERSHIP BY AUTHORSHIP (the last claim that CHANGED each sample, "
          f"not the last whose blob holds it), so the union cannot launder a "
          f"pad's floor into a road's name nor a road's carriageway into a "
          f"pad's")

    def foreign_at(x: float, z: float):
        return live.foreign_at(x, z)

    # ANOTHER ROAD'S CARRIAGEWAY, FOR THE CORRIDOR RULE.  Main's ruling: where
    # two road claims run closer than the sum of their batter runs they are
    # ONE CORRIDOR, and a batter that tapers into a finished road cuts it.
    # This is scoped to OTHER segments -- this segment's own prior write is
    # what is being replaced, so treating it as foreign would refuse the
    # repair its own ground.
    my_name = f"road_{seg['id']}".replace("-", "_")

    def road_claim_at(x: float, z: float):
        d, w = live.sample(int(round(x)), int(round(z)))
        if w is None or w["role"] != appliedmod.ROAD_ROLE:
            return None
        if w["name"] == my_name:
            return None
        return (d, f"{w['name']}#{w['seq']}")

    # THE RASTERISER, AS A FUNCTION OF THE PROFILE, so the approach solve can
    # ASK the clamp and the gradient about a candidate reach instead of
    # predicting them.  Identical inputs to the single call it replaces --
    # this is the same rasterisation, run once per candidate.
    def rasterise(sg: dict):
        cmps, stt = stamp(sg, patches, zones, pad_keepouts, prot["pieces"],
                          prot["discs"], placed_objects=placed,
                          poi_keepouts=poi_keepouts, batter_m=batter_m,
                          foreign_at=foreign_at, road_claim_at=road_claim_at)
        wlk = walkability(sg, cmps, patches, pad_keepouts,
                          prot["pieces"], prot["discs"],
                          road_clips=stt["road_clip_at"])
        return cmps, stt, wlk

    approach, comps, st, reach_solve = solve_approach(
        seg, patches, live, pad_keepouts, rasterise)
    print(f"approach grading: {approach['claims']} stations claimed by "
          f"{approach.get('claims_by_cause', {})}"
          + (f" owners {approach['owners']}" if approach.get("owners") else "")
          + f"; regraded {approach['nodes_regraded']} profile nodes "
          f"(worst move {approach.get('max_profile_move_m')} m); step into the "
          f"claim {approach['worst_step_before_m']} -> "
          f"{approach['worst_step_after_m']} m")
    print(f"approach reach SOLVED against the clamp: "
          f"{reach_solve.get('reaches_tried')} candidate(s) between "
          f"{reach_solve.get('shortest_reach_m')} and "
          f"{reach_solve.get('derived_reach_m')} m, chose "
          f"{reach_solve.get('chosen_m')} m "
          f"(ramp grade {approach.get('ramp_grade')}, 8 m gradient "
          f"{reach_solve.get('chosen_g8')}); {reach_solve.get('why')}")
    for r in (reach_solve.get("rows") or []):
        print(f"     reach {r['reach_m']:>7} grade {r['ramp_grade']:<7} "
              f"over_clamp {r['over_clamp']:<4} fill {r['max_fill_m']:<7} "
              f"g8 {r['g8']:<7} pad_step {str(r.get('worst_pad_step_m')):<7} "
              f"({r.get('pads_over_batter_grade')} over {BATTER_GRADE:.3f}) "
              f"{r['verdict']}")
    for c in approach.get("conflicts", [])[:5]:
        print("   CONFLICT", c)
    # THE SAMPLES THIS WRITE WROTE, snapshotted BEFORE the union carries
    # anybody else's in.  Without it every neighbour's wall is attributed to
    # this segment -- measured: T3's carriageway edge reads as a 3.676 m wall
    # in T12's own cross section at lat -6.5, because T3 runs 6-10 m from T12
    # near the temple.
    mine = {(z, i) for z, comp in comps.items()
            for i in range(tcdata.SAMPLES) if comp.modified_height[i]}

    # ---- RULE 2, MAIN'S RULING: REFUSE, DO NOT CLAMP -------------------
    #
    # NO AUTHORED SAMPLE MAY LAND INSIDE ANOTHER CLAIM'S WRITTEN FOOTPRINT.
    # A road may grade into a pad's EDGE -- that is the approach grading, and
    # it is required -- and it may never author a sample the pad wrote.  The
    # footprint is read from the pad's OWN record's blob, per sample index,
    # exactly as `foreign_at` reads it and for the same reason: a pad's
    # written extent is not its nominal `pad_radius_m`, and the T4 clobber was
    # measured OUTSIDE the road's keep-out circle.
    #
    # `refusal` already rejects such a sample one at a time, so this cannot
    # fire while the ownership above is rewound -- which is the point.  It is
    # the GUARD that makes the rewind safe rather than merely correct: if a
    # future caller hands this function a laundered surface, or the rewind is
    # removed, or a new pad lands between the plan and the write, the result
    # is a LOUD REFUSAL naming the count and the owning claim instead of a
    # silently regraded foundation.  Nothing is clamped: a road that cannot be
    # written without authoring somebody's floor is a road to re-plan.
    authored_in_a_claim: dict[str, list] = {}
    for (zx, zz), comp in comps.items():
        cx0, cz0 = tcdata.zone_centre(zx, zz)
        for i in range(tcdata.SAMPLES):
            if not comp.modified_height[i]:
                continue
            gy, gx = divmod(i, tcdata.PITCH)
            wx, wz = tcdata.sample_world(cx0, cz0, gx, gy)
            # THE AUTHOR, AND FROM EVERY ZONE LATTICE THAT HOLDS THE SAMPLE.
            # Reading this zone's `owner` array asked two wrong questions at
            # once and MEASURED both wrong on S1-portalhub-brgs2: it refused
            # 156 samples to `portal-hall#801` including (-302, 212), which
            # is outside the hall's 37.4 x 37.4 m rectangle entirely and is
            # S1's own carriageway that the hall's blob carried forward
            # verbatim.  `holders` answers per WORLD sample across the
            # adjacent lattices (the 65 x 65 boundary row is shared, so one
            # square metre sits in two compilers) and now answers with the
            # AUTHOR, so a carried copy is never mistaken for a claim.
            foreign = [w for w in live.holders(int(round(wx)), int(round(wz)))
                       if w["role"] != appliedmod.ROAD_ROLE]
            for w in foreign[:1]:
                authored_in_a_claim.setdefault(
                    f"{w['name']}#{w['seq']} ({w['role']})", []).append(
                        [round(wx, 1), round(wz, 1)])
    if authored_in_a_claim:
        for owner, pts in sorted(authored_in_a_claim.items()):
            print(f"   AUTHORED INSIDE A CLAIM: {len(pts)} samples owned by "
                  f"{owner}, first at {pts[:6]}")
        print(f"REFUSING: this write authors "
              f"{sum(len(p) for p in authored_in_a_claim.values())} samples "
              f"inside {len(authored_in_a_claim)} other claim's written "
              f"footprint. A road grades into a pad's EDGE and never authors a "
              f"sample inside it -- inside a settlement pad the pad wins, "
              f"because it is a foundation. Nothing has been sent. Re-plan the "
              f"segment or have the pad's owner re-emit; do not clamp.")
        return 6
    loc = location_check(comps, st["written"], seg["width_m"] / 2.0,
                         road_written=st["road_written"])
    print(f"location check: verdict={loc['verdict']} nearest="
          f"{(loc['nearest'] or {}).get('name')} standoff {loc['standoff_m']} m "
          f"over {loc['samples_tested']} written samples, "
          f"{len(loc['violations'])} distance violations; delta gate "
          f"{loc['delta_gate']['verdict']} worst "
          f"{(loc['delta_gate']['worst'] or {}).get('worst_abs_delta_m')} m over "
          f"{loc['delta_gate']['instances_probed']} instances")
    if loc["verdict"] != "clear":
        for v in loc["violations"] + loc.get("delta_gate_violations", []):
            print("   VIOLATION", v)
        print("REFUSING: a written sample is inside a location's hard radius. "
              "This is the defect that deleted POI content in the old world.")
        return 3
    print(f"stamped {st['samples_paved']} paved + {st['samples_shoulder']} "
          f"shoulder + {st['samples_batter']} batter samples over "
          f"{len(comps)} zones; "
          f"cut {st['max_cut_m']} fill {st['max_fill_m']} m; skipped "
          f"{st['skipped_pad']} inside a Settlements pad, "
          f"{st['skipped_protected']} beside a protected piece, "
          f"{st['skipped_underwater']} on a protected structure's water")
    print(f"batter clipped: {st['batter_clipped_pad']} at a pad, "
          f"{st['batter_clipped_protected']} at a protected piece, "
          f"{st['batter_clipped_underwater']} on protected water, "
          f"{st['batter_clipped_water_edge']} at a water edge (below "
          f"{WATER_LEVEL_M} m), {st['batter_clipped_poi']} inside a POI "
          f"stand-off, {st['batter_clipped_placed']} beside a live placed "
          f"object (grade {st['batter_grade']}, reach {st['batter_max_m']} m)")
    print(f"WALLS LEFT STANDING: worst {st['walls_left_max_m']} m, by cause "
          f"{st['walls_left_by_cause']}")
    for w in st["walls_left"][:10]:
        print("   ", w)
    print(f"samples past the MEASURED +/-8 m apply clamp: {st['over_clamp']}")
    for s in st["over_clamp_samples"]:
        print("   ", s)
    if st["over_clamp"]:
        print("REFUSING: a sample past the clamp is road that will not be where "
              "the plan says. Re-plan the segment.")
        return 2

    # UNION WITH ANY EARLIER TERRAIN WRITE TO THE SAME ZONE.
    #
    # A zone holds exactly ONE `_TerrainCompiler`
    # (`Heightmap::GetAndCreateTerrainCompiler` returns the first it finds, so a
    # second is dead weight), and this op does `deleteObjects -zone` before it
    # spawns -- so writing a zone someone already wrote DESTROYS their terrain.
    # On a 33 km network crossing 470 zones alongside 50 building pads that is
    # the normal case, not a corner case, which is why the ledger REFUSES it
    # unless the earlier write is named.  MEASURED here: T3-temple-meadhall
    # shares five zones with T12-wtspawn-temple, built minutes earlier at
    # seq 181, and the guard refused rather than quietly erasing 2,051 samples
    # of finished road.
    #
    # The merge is per SAMPLE INDEX: every sample the earlier blob wrote and
    # this one does not is carried forward verbatim, both its height delta and
    # its paint. Samples both wrote are MINE -- last writer wins per sample --
    # and in practice that set is empty, because two ribbons meeting at a node
    # share a zone but not a square metre, and a ribbon skips everything inside
    # a settlement pad radius. The union is recorded per entry so a replay
    # reproduces the same bytes in the same order.
    prior_by_zone: dict[tuple[int, int], list[dict]] = {}
    # RUN UNCONDITIONALLY, INCLUDING UNDER `--validate`, and the reason is a
    # measured lie.  This block used to be skipped when validating, so the
    # record the pre-flight validated was NOT the record the live path builds:
    # the live one carries every prior claim's samples unioned into its own
    # blob.  MEASURED on T4 -- the offline pad-guard check PASSED and
    # `Ledger.append` then REFUSED the same write for 59 authored samples
    # inside three Stenvik pads, because the samples it objected to were the
    # UNIONED ones the validate path had never created.  A pre-flight that
    # validates different bytes from the ones that get sent is worse than no
    # pre-flight: it converts a caught defect into a confident one.  The union
    # is a pure read of the ledger and costs nothing to run twice.
    sys.path.insert(0, str(JUMPSTART / "ledger"))
    from writer import Ledger
    led_ro = Ledger.open("Ulfsland", actor=ACTOR)
    for line, rec in enumerate(led_ro.records()):
        if rec.get("op") != "terrain_write":
            continue
        for e in rec["params"]["entries"]:
            z = tuple(e["zone"])
            if z in comps:
                # SEQ IS AMBIGUOUS PAST FILE LINE 47 -- the chain forked
                # tonight and the repair left duplicate seq labels, so
                # `merged_from` (which the clobber guard matches on seq, and
                # must keep matching on) is recorded alongside the FILE
                # LINE, the only total order this artefact has.  The merge
                # itself keys on the BLOB DIGEST, so an ambiguous label
                # cannot corrupt the bytes -- only the human trail.
                prior_by_zone.setdefault(z, []).append(
                    {"seq": rec["seq"], "sha": e["blob_sha256"],
                     "file_line": line,
                     "name": rec["params"].get("name")})
    for z, plist in prior_by_zone.items():
        comp = comps[z]
        carried = 0
        # THE UNION CARRIES THE LATEST PRIOR CLAIM, NOT THE FIRST, and the
        # first version of this loop had it backwards: it stopped at the
        # first prior blob holding a sample, which on a zone with five
        # prior writes is the OLDEST.  That is Main's T4 ruling inverted --
        # it would restore `RoadBuild` seq 213's road delta over
        # `SiteFinish` seq 286's pad inside the pad's own footprint,
        # re-breaking the foundation this repair exists to respect.  So
        # the priors are resolved amongst themselves in FILE ORDER first
        # (later wins, which is what the live compiler holds), and only
        # then filled in where THIS write wrote nothing.
        merged_h: dict[int, tuple[float, float]] = {}
        merged_p: dict[int, tuple] = {}
        for pr in sorted(plist, key=lambda p: p["file_line"]):
            old = tcdata.parse(led_ro.read_blob(pr["sha"]))
            for i, (lvl, sm) in old["heights"].items():
                merged_h[i] = (lvl, sm)
            for i, col in old["paints"].items():
                merged_p[i] = col
        # NO PAD-FLOOR RESTORATION HERE, and the reason is the same ruling
        # that unblocked this write.  A previous form of this loop looked up
        # each sample's pad floor and restored it over the later claim's
        # value, because `writer._pad_footprint_check` used to judge a road
        # against EVERY pad that had ever authored a sample.  That check was
        # the defect: a superseded pad's floor is ground that NO LONGER
        # EXISTS, and two pads that authored the same square metre made it
        # unsatisfiable (T4 at (480-482, 908)).  The guard now resolves to the
        # EFFECTIVE author, so the correct union is the plain one -- FILE
        # ORDER, later wins, which is what the live compiler holds -- and
        # restoring a dead floor would now be the thing that gets refused.
        for i, (lvl, sm) in merged_h.items():
            if not comp.modified_height[i]:
                comp.modified_height[i] = True
                comp.level_delta[i] = lvl
                comp.smooth_delta[i] = sm
                carried += 1
        for i, col in merged_p.items():
            if not comp.modified_paint[i]:
                comp.modified_paint[i] = True
                comp.paint[i] = col
        print(f"  zone {list(z)}: unioned {carried} samples forward from "
              f"seq {[pr['seq'] for pr in plist]} "
              f"(file lines {[pr['file_line'] for pr in plist]}, "
              f"{sorted({pr['name'] for pr in plist})})")
    # `pad_levelled_yet` used to be derived here, by name-prefixing every
    # `terrain_write` against a site id.  It is now a property of the
    # keep-out itself: a foundation in `pad_claims` IS levelled and
    # carries the `target_y` it was levelled to, and a site that only
    # exists in `sites.yaml` is not and carries its declared `pad_y`.
    # Deriving it twice from two sources is how they disagree.

    # THE OPERATOR'S OWN TEST, on the surface that will exist after the union.
    # Run here rather than before the merge because a junction zone's carried
    # samples are part of the rendered mesh: at the temple the T3 ribbon meets
    # T12's, and continuity across that joint is a property of the union, not
    # of this segment alone.
    walk = walkability(seg, comps, patches, pad_keepouts,
                       prot["pieces"], prot["discs"],
                       road_clips=st["road_clip_at"])
    walk["skipped"] = {"settlement_pad": st["skipped_pad"],
                       "beside_protected_piece": st["skipped_protected"],
                       "on_protected_structure_water": st["skipped_underwater"],
                       "on_a_foreign_claim": st["skipped_foreign"]}
    walk["batter_clipped"] = {
        "settlement_pad": st["batter_clipped_pad"],
        "beside_protected_piece": st["batter_clipped_protected"],
        "on_protected_structure_water": st["batter_clipped_underwater"],
        "beside_live_placed_object": st["batter_clipped_placed"],
        "inside_poi_standoff": st["batter_clipped_poi"],
        "at_a_water_edge": st["batter_clipped_water_edge"],
        "on_a_foreign_claim": st["batter_clipped_foreign"],
        "hillside_steeper_than_batter":
            st["batter_clipped_steeper_than_batter"],
        "reach_spent": st["batter_clipped_reach"],
        "beside_live_placed_object_at": st["batter_clipped_placed_at"]}
    walk["batter_rays"] = {"rays": st["rays"], "clipped": st["rays_clipped"],
                           "met_natural_ground": st["batter_met_ground"]}
    walk["walls_left"] = {"worst_m": st["walls_left_max_m"],
                          "worst_before_m": st["walls_before_max_m"],
                          "by_cause": st["walls_left_by_cause"],
                          "listed": st["walls_left"]}
    walk["approach_grading"] = approach
    # THE SOLVE, NOT ONLY ITS ANSWER.  A chosen reach with no ladder beside it
    # is a constant again: the row per candidate is what lets a later reader
    # see that the clamp, not a derivation, picked it.
    walk["approach_reach_solve"] = reach_solve
    walk["edge_inclusive"] = {"samples_at_exactly_edge":
                              st["edge_exact_samples"],
                              "eps_m": st["edge_eps_m"],
                              "edge_m": st["edge_m"]}
    walk["protected_pieces_in_zones"] = prot["by_site"]
    walk["protected_records_without_pieces"] = prot["without_pieces"]
    # THE TRANSVERSE QUANTITY, beside the longitudinal one and never instead
    # of it.  The operator's "blocky" is this number; `max_gradient_8m` is the
    # answer to "is it too steep", which every one of these segments already
    # passed while carrying an 8 m wall.
    walk["transverse"] = edge_step_census(
        seg, comps, patches, batter_m=batter_m, mine=mine,
        clips=st["clip_at"] + st["ray_skip_at"])
    tv = walk["transverse"]
    print(f"TRANSVERSE (the operator's 'blocky'): carriageway 1 m step p50 "
          f"{tv['carriageway_step_p50']} max {tv['carriageway_step_max']}")
    print(f"  MY EARTHWORK p50 {tv['earthwork_step_p50']} p95 "
          f"{tv['earthwork_step_p95']} max {tv['earthwork_step_max']} over "
          f"{tv['earthwork_pairs']} pairs; VERDICT {tv['verdict']} against "
          f"{tv['verdict_limit_m']} m ({tv['batter_grade_limit']} grade + "
          f"{tv['diagonal_tolerance_m']} diagonal), over-grade fraction "
          f"{tv['earthwork_over_grade_frac']}: "
          f"{tv['earthwork_over_grade_explained_by_a_recorded_clip']} explained "
          f"by a recorded clip, "
          f"{tv['earthwork_over_grade_UNEXPLAINED']} UNEXPLAINED")
    print(f"  batter rays {st['rays']} ({st['rays_clipped']} clipped, "
          f"{st['batter_met_ground']} met natural ground, "
          f"{st['rays_without_road']} skipped for having no written road); "
          f"{st['samples_batter_in_pad_standoff']} batter samples inside a pad "
          f"stand-off; interstice {st['interstice_rays']} rays "
          f"({st['interstice_apron']} levelled flush, "
          f"{st['interstice_left_ungraded']} left ungraded); "
          f"{st['edge_exact_samples']} samples at exactly lat==edge")
    for r in st["interstice"][:4]:
        print("   interstice", r)
    for w in tv["earthwork_over_grade_at"]:
        if w["recorded_clip"] is None:
            print("   UNEXPLAINED", w)
    print(f"  ANOTHER CLAIM'S (junctions, not mine to grade) p50 "
          f"{tv['junction_step_p50']} max {tv['junction_step_max']} over "
          f"{tv['junction_pairs']} pairs")
    print(f"  natural ground p50 {tv['natural_ground_step_p50']} max "
          f"{tv['natural_ground_step_max']}; legacy band metric p50 "
          f"{tv['outside_edge_step_p50']} max {tv['outside_edge_step_max']}")
    for w in tv["earthwork_over_grade_at"][:6]:
        print("   over grade", w)
    print(f"walkability: {walk['verdict']} max gradient over "
          f"{VERDICT_BASELINE_M:g} m baseline {walk['max_gradient_8m']} "
          f"(limit {walk['slide_limit']}, 38 deg slide angle) at "
          f"{walk['max_gradient_8m_at']} over {walk['baselines_8m_pairs']} "
          f"baselines; the 1 m figure, a DIFFERENT QUANTITY, is "
          f"{walk['max_gradient_1m']}; {walk['stations_on_road']}/"
          f"{walk['centreline_stations']} centreline stations on written road")
    for g in walk["gaps"]:
        print(f"   gap {g['cause']:14s} {g['length_m']:6.1f} m at {g['xz']} "
              f"step {g['step_across_m']} m, fill at edges {g['fill_at_edges_m']}")
        t = g.get("terminates_at")
        if t:
            print(f"      terminates at {t['site_id']}: road "
                  f"{t['road_y_at_pad_edge']} m vs pad datum {t['pad_y']} m = "
                  f"STEP {t['step_road_to_pad_m']} m "
                  f"(pad levelled yet: {t['pad_levelled_yet']})")
    if walk["holes"]:
        print(f"REFUSING: {walk['holes']} gap(s) in the ribbon with no cause "
              f"-- a hole in the road is the one defect the operator is "
              f"guaranteed to walk into.")
        return 5

    entries = []
    for (zx, zz), comp in sorted(comps.items()):
        cx, cz = comp.centre
        patch = patches[f"z_{zx}_{zz}"]
        op_y = float(np.median([comp.level_delta[i] + patch.heights[i]
                                for i in range(tcdata.SAMPLES)
                                if comp.modified_height[i]]))
        blob = comp.blob(op_y)
        opx, opy, opz, opr = comp.op_record(op_y)
        entries.append({
            "zone": [zx, zz], "centre": [cx, cz],
            "data_entry": f"road_{seg['id']}_z{zx}_{zz}".replace("-", "_"),
            "blob": blob,
            "blob_sha256": hashlib.sha256(blob).hexdigest(),
            "generated_heights_sha256": generated_digest(patch),
            "op_record": {"operations": comp.operations,
                          "last_op_point": [opx, opy, opz],
                          "last_op_radius": opr},
            "counts": comp.counts(),
        })
        if (zx, zz) in prior_by_zone:
            entries[-1]["merged_from"] = [pr["seq"] for pr
                                          in prior_by_zone[(zx, zz)]]
            entries[-1]["merge_policy"] = "union"

    plan = {
        "segment": seg["id"], "zones": len(entries),
        "zdo_cost": len(entries),
        "paved_samples": st["samples_paved"],
        "shoulder_samples": st["samples_shoulder"],
        "batter_samples": st["samples_batter"],
        "paved_m2": st["samples_paved"] * 1.0,
        "location_check": {k: v for k, v in loc.items() if k != "violations"},
        "over_clamp": st["over_clamp"],
        "walkability": walk,
    }
    if args.op == "plan":
        print(json.dumps(plan, indent=1))
        return 0

    terrain_params = {
        "name": f"road_{seg['id']}".replace("-", "_"),
        "role": "road_segment",
        "paint": PAINT_ROAD,
        "datum": "road profile: a fitted longitudinal profile. "
                 "lowest_major_walkable_surface is a PAD datum and does not "
                 "apply to a ribbon, which has a different target height at "
                 "every station.",
        "profile": {
            "nodes": [[n[0], n[1], y] for n, y, brg
                      in zip(seg["nodes"], seg["profile_y"], seg["is_bridge"])
                      if not brg],
            "half_width_m": seg["width_m"] / 2.0,
            "shoulder_m": SHOULDER_M,
            # The batter is part of the WRITTEN SURFACE, so it belongs in the
            # field a replay re-derives deltas from. Without it a replay
            # reproduces the carriageway and silently drops every side slope,
            # which is the pre-repair geometry wearing this record's name.
            "batter_m": batter_m,
            "batter_grade": round(BATTER_GRADE, 4),
            "interp": "linear_arclength",
        },
        "max_cut_m": st["max_cut_m"], "max_fill_m": st["max_fill_m"],
        "over_clamp": st["over_clamp"],
        "location_check": loc,
        "entries": entries,
    }
    wire_del = [f"deleteObjects -zone {e['zone'][0]} {e['zone'][1]} "
                f"-prefab _TerrainCompiler -force" for e in entries]
    wire_spawn = [f"spawn_object _TerrainCompiler "
                  f"from={e['centre'][0]:g},{e['centre'][1]:g},0 "
                  f"data={e['data_entry']}" for e in entries]

    if args.validate:
        sys.path.insert(0, str(JUMPSTART / "ledger"))
        import schema
        # The entries carry `blob` (raw bytes) until the blob store swallows
        # them; validation only ever sees the digest, so strip them here.
        vparams = dict(terrain_params)
        vparams["entries"] = [{k: v for k, v in e.items() if k != "blob"}
                              for e in entries]
        for e in vparams["entries"]:
            e["zone_generated_before"] = True
            e["zone_generated_probe"] = (
                f"objects_count id=_ZoneCtrl pos={e['centre'][0]:g},"
                f"{e['centre'][1]:g} max=1")
        rec = {"seq": 1, "ts": "1970-01-01T00:00:00Z", "actor": "RoadNet",
               "op": "terrain_write", "params": vparams,
               "wire": wire_del + wire_spawn,
               "requires": {"mods": ["WorldEditCommands", "UpgradeWorld",
                                     "ServerDevcommands", "ValheimRcon"],
                            "prefabs": ["_TerrainCompiler"],
                            "blobs": [e["blob_sha256"] for e in entries]},
               "expect": {"terrain_compiler": True}, "meta": {},
               "prev": "0" * 64}
        if args.preflight_out:
            pf = Path(args.preflight_out)
            doc = json.loads(pf.read_text()) if pf.exists() else {
                "owner": "RoadNet", "world": "Ulfsland", "seed": SEED,
                "what": "validated pre-flight per road segment: the evidence a "
                        "replay needs BEFORE it sends anything",
                "segments": {}}
            doc["segments"][seg["id"]] = {
                "length_m": seg["length_m"], "cls": seg["cls"],
                "width_m": seg["width_m"], "shoulder_m": SHOULDER_M,
                "grade_limit": seg["grade_limit"],
                "grade_limit_design": seg.get("grade_limit_design"),
                "grade_relaxed_to": seg.get("grade_relaxed_to"),
                "grade_max_measured": seg["grade_max_measured"],
                "zones": [e["zone"] for e in entries],
                "zdo_cost": len(entries),
                "paved_samples": st["samples_paved"],
                "shoulder_samples": st["samples_shoulder"],
                "batter_samples": st["samples_batter"],
                "batter_m": batter_m, "batter_grade": round(BATTER_GRADE, 4),
                "transverse": walk["transverse"],
                "skipped_inside_settlement_pad": st["skipped_pad"],
                "max_cut_m": st["max_cut_m"], "max_fill_m": st["max_fill_m"],
                "samples_past_8m_clamp": st["over_clamp"],
                "clearance": {k: v for k, v in loc.items()
                              if k in ("verdict", "budget", "standoff_m",
                                       "nearest", "samples_tested", "dump",
                                       "tool")},
                "delta_gate": {k: v for k, v in loc["delta_gate"].items()
                               if k in ("verdict", "tolerance_m", "worst",
                                        "instances_probed", "positions_probed")},
                "blob_sha256": {f"{e['zone'][0]},{e['zone'][1]}": e["blob_sha256"]
                                for e in entries},
                "generated_heights_sha256": {
                    f"{e['zone'][0]},{e['zone'][1]}": e["generated_heights_sha256"]
                    for e in entries},
                "crossings_handed_to_Crossings": [c["crossing_id"]
                                                  for c in seg["crossings"]],
                "fords_kept_as_terrain": [f["crossing_id"] for f in seg["fords"]],
                "abutments": seg["abutments"],
            }
            pf.write_text(json.dumps(doc, indent=1, sort_keys=True))
            print(f"pre-flight -> {pf}")
        bad = schema.validate(rec)
        print(f"schema.validate(terrain_write): "
              f"{'OK' if not bad else str(len(bad)) + ' problems'}")
        for b in bad:
            print("   ", b)
        zrec = {"seq": 2, "ts": "1970-01-01T00:00:00Z", "actor": "RoadNet",
                "op": "zones_generate",
                "params": {"pos": list(entries[0]["centre"]), "max_m": 64.0,
                           "zones": [e["zone"] for e in entries],
                           "role": "road_segment"},
                "wire": [f"zones_generate pos={entries[0]['centre'][0]:g},"
                         f"{entries[0]['centre'][1]:g} max=64"],
                "requires": {}, "expect": {"zone_ctrl": len(entries)},
                "meta": {}, "prev": "0" * 64}
        zbad = schema.validate(zrec)
        print(f"schema.validate(zones_generate): "
              f"{'OK' if not zbad else str(len(zbad)) + ' problems'}")
        for b in zbad:
            print("   ", b)
        # ---- THE WRITE-TIME PAD GUARD, ASKED HERE RATHER THAN DISCOVERED
        # AT APPEND TIME.
        #
        # `schema.validate` is not the whole gate.  `Ledger.append` also runs
        # `_pad_footprint_check`, which refuses any `terrain_write` that
        # AUTHORS a sample inside another claim's pad footprint -- the
        # rectangle or the ground that pad itself levelled.  It is the check
        # that will reject this write, it is not in `roads/`, and it is not
        # waivable.  This flag's whole purpose is that "finding that out while
        # holding the console is how a staged operation gets left half-sent",
        # so it is asked offline, against the real chain, before anything is
        # appended.
        #
        # IN A SANDBOX CHAIN, because the guard must DECODE the candidate's
        # blobs to find its authored samples and those blobs are not in the
        # store until the write happens.  A copy of `ledger.jsonl` plus
        # HARDLINKED blobs costs no bytes and keeps the shared store free of
        # digests belonging to a write that may never be made.
        sys.path.insert(0, str(JUMPSTART / "ledger"))
        from writer import Ledger as _Ledger
        sandbox = Path(f"/tmp/{args.actor.lower()}/guardcheck")
        if sandbox.exists():
            shutil.rmtree(sandbox)
        (sandbox / "blobs").mkdir(parents=True)
        real = _Ledger.open("Ulfsland", actor=args.actor)
        shutil.copy2(real.path, sandbox / real.path.name)
        if real._index_path.exists():
            shutil.copy2(real._index_path, sandbox / real._index_path.name)
        # SYMLINKS, not hardlinks: `/tmp` is a different filesystem from the
        # repo that holds the blob store, so `os.link` fails with EXDEV.  A
        # symlink costs the same nothing and `read_blob` resolves it -- it
        # reads bytes and re-digests them, so a link that pointed at the
        # wrong payload would be caught rather than trusted.
        linked = 0
        for blobfile in real.blobs.iterdir():
            if blobfile.is_file():
                os.symlink(blobfile, sandbox / "blobs" / blobfile.name)
                linked += 1
        sand = _Ledger.open("Ulfsland", actor=args.actor, root=sandbox)
        for e in entries:
            sand.blob(e["blob"], note=f"guardcheck {seg['id']}")
        prior = sand.records()
        guard = sand._pad_footprint_check(rec, prior)
        print(f"write-time pad guard (writer.py::_pad_footprint_check) over "
              f"{len(prior)} prior record(s) and {linked} linked blob(s) "
              f"in {sandbox}: {'PASSES' if not guard else 'REFUSES'}")
        for g in guard:
            print("   ", g)
        if guard:
            # WHICH SAMPLES, AND WHAT THIS WRITE PUT THERE.  The guard names a
            # count, a worst move and the first six positions; that is enough
            # to know it refused and not enough to know WHY this write differs
            # from the composition.  The rasteriser refuses every sample the
            # keep-out covers, so a disagreement here is NOT a paved
            # foundation -- it is this write's blob carrying a different value
            # at a sample it never wrote, which is a UNION defect and reads
            # identically in the refusal message.  So the two values are
            # printed side by side.
            composed: dict[tuple[int, int], tuple[float, int]] = {}
            for line, pr in enumerate(prior):
                if pr.get("op") != "terrain_write":
                    continue
                for e in pr["params"].get("entries", []):
                    got = writer_entry_samples(e, sand)
                    if got is None:
                        continue
                    for sxz, val in got.items():
                        composed[sxz] = (val[0], pr["seq"])
            shown = 0
            for e in rec["params"]["entries"]:
                got = writer_entry_samples(e, sand)
                if got is None:
                    print(f"    entry zone {e['zone']}: BLOB WOULD NOT DECODE")
                    continue
                for sxz, val in sorted(got.items()):
                    sid = pad_keepouts.contains(float(sxz[0]), float(sxz[1]))
                    if sid is None:
                        continue
                    was, was_seq = composed.get(sxz, (None, None))
                    if was is not None and abs(was - val[0]) <= 1e-6:
                        continue
                    if shown < 12:
                        zx, zz = int(e["zone"][0]), int(e["zone"][1])
                        cmp_ = comps.get((zx, zz))
                        mh = (None if cmp_ is None else
                              cmp_.modified_height[
                                  tcdata.vertex_mask_index(
                                      float(e["centre"][0]),
                                      float(e["centre"][1]), sxz[0], sxz[1])[1]
                                  * tcdata.PITCH
                                  + tcdata.vertex_mask_index(
                                      float(e["centre"][0]),
                                      float(e["centre"][1]), sxz[0], sxz[1])[0]])
                        print(f"    sample {sxz} in {sid}: prior "
                              f"{'None' if was is None else f'{was:+.4f}'} "
                              f"(seq {was_seq}) -> mine {val[0]:+.4f} "
                              f"[zone {e['zone']}, my rasteriser wrote it: "
                              f"{mh}]")
                    shown += 1
            print(f"    {shown} disagreeing sample(s) inside a pad footprint")
        return 0 if not (bad or zbad or guard) else 4

    # ---- live, through the ledger --------------------------------------
    sys.path.insert(0, str(JUMPSTART / "ledger"))
    from live import LiveBuilder

    with LiveBuilder(actor=args.actor, dry=args.dry) as b:
        b.observe(
            "road_segment_plan", 
            method="MEASURED: A* over a 1 m PatchScan field (rivers included) "
                   "with the achievable surface clipped to +/-7 m of generated "
                   "ground; profile fitted by exact interval propagation under "
                   "the grade limit and the cut/fill box",
            tool="tools/jumpstart/roads/network.py + ribbon.py",
            value={k: v for k, v in seg.items()
                   if k not in ("nodes", "profile_y", "terrain_y", "is_bridge",
                                "poi_within_keepout")})

        # STEP 0: generate every zone, in batches, and prove each one.
        done_zones = []
        for i in range(0, len(entries), args.zone_batch):
            batch = entries[i:i + args.zone_batch]
            xs = [e["centre"][0] for e in batch]
            zs = [e["centre"][1] for e in batch]
            px, pz = (min(xs) + max(xs)) / 2.0, (min(zs) + max(zs)) / 2.0
            reach = max(math.hypot(e["centre"][0] - px, e["centre"][1] - pz)
                        for e in batch) + 1.0
            r = b.emit("zones_generate",
                       params={"pos": [px, pz], "max_m": round(reach, 1),
                               "zones": [e["zone"] for e in batch],
                               "role": "road_segment"},
                       wire=[f"zones_generate pos={px:g},{pz:g} max={reach:g}"],
                       expect={"zone_ctrl": len(batch)},
                       meta={"segment": seg["id"],
                             "why": "paint alpha is NOT honoured on a zone that "
                                    "has never been generated -- MEASURED, a "
                                    "paved_cleared pad in an ungenerated zone "
                                    "still planted 30+ vegetation ZDOs up to "
                                    "7.68 m above it"})
            print(f"  zones_generate {len(batch)} zones -> {r['status']}")
            done_zones += batch

        # Probe each zone individually: the batch's aggregate _ZoneCtrl count
        # proves the staged op completed, but the ledger requires per-zone
        # evidence and `objects_count id=_ZoneCtrl pos=cx,cz max=1` is exact,
        # because PlaceZoneCtrl puts the controller at the zone CENTRE and the
        # filter is an XZ cylinder.
        import replay as R
        for e in entries:
            cx, cz = e["centre"]
            probe = f"objects_count id=_ZoneCtrl pos={cx:g},{cz:g} max=1"
            got, _ = b.srv.count("_ZoneCtrl", cx, cz, 1.0, ignore="")
            e["zone_generated_before"] = bool(got >= 1)
            e["zone_generated_probe"] = probe
            e["zone_ctrl_observed"] = int(got)
            if got < 1:
                raise SystemExit(
                    f"zone {e['zone']} shows {got} _ZoneCtrl after "
                    f"zones_generate: refusing to write terrain into a zone "
                    f"that is not generated. Probe: {probe}")

        shas = []
        for e in entries:
            shas.append(b.blob(e.pop("blob"),
                               note=f"TCData {seg['id']} zone {e['zone']}"))
        wire = []
        for e in entries:
            wire.append(f"deleteObjects -zone {e['zone'][0]} {e['zone'][1]} "
                        f"-prefab _TerrainCompiler -force")
        for e in entries:
            wire.append(f"spawn_object _TerrainCompiler "
                        f"from={e['centre'][0]:g},{e['centre'][1]:g},0 "
                        f"data={e['data_entry']}")

        r = b.emit(
            "terrain_write",
            params={
                "name": f"road_{seg['id']}".replace("-", "_"),
                "role": "road_segment",
                "paint": PAINT_ROAD,
                "datum": "road profile: fitted longitudinal profile, "
                         "lowest_major_walkable_surface is not applicable to a "
                         "ribbon",
                "profile": {
                    "nodes": [[n[0], n[1], y] for n, y, brg
                              in zip(seg["nodes"], seg["profile_y"], seg["is_bridge"])
                              if not brg],
                    "half_width_m": seg["width_m"] / 2.0,
                    "shoulder_m": SHOULDER_M,
                    "batter_m": batter_m,
                    "batter_grade": round(BATTER_GRADE, 4),
                    "interp": "linear_arclength",
                },
                "max_cut_m": st["max_cut_m"], "max_fill_m": st["max_fill_m"],
                "over_clamp": st["over_clamp"],
                "location_check": {k: v for k, v in loc.items()
                                   if k not in ("within_keepout",)},
                "entries": entries,
            },
            wire=wire,
            requires={"mods": ["WorldEditCommands", "UpgradeWorld",
                               "ServerDevcommands", "ValheimRcon"],
                      "prefabs": ["_TerrainCompiler"],
                      "blobs": shas},
            expect={"terrain_compiler": True},
            meta={"grade_limit": seg["grade_limit"],
                  "grade_max_measured": seg["grade_max_measured"],
                  "crossings_handed_to_Crossings": [c["crossing_id"]
                                                    for c in seg["crossings"]],
                  "fords_kept_as_terrain": [f["crossing_id"] for f in seg["fords"]],
                  "abutments": seg["abutments"],
                  "paved_samples": st["samples_paved"],
                  "shoulder_samples": st["samples_shoulder"],
                  "batter_samples": st["samples_batter"],
                  "batter_m": batter_m,
                  "batter_grade": round(BATTER_GRADE, 4),
                  "repair_of": args.repair_of,
                  "repair_before": (json.loads(
                      Path(args.repair_before).read_text())
                      if args.repair_before else None),
                  "walkability": walk,
                  "merged_from_file_lines": {
                      f"{z[0]},{z[1]}": [{"file_line": pr["file_line"],
                                          "seq": pr["seq"],
                                          "name": pr["name"]}
                                         for pr in plist]
                      for z, plist in sorted(prior_by_zone.items())},
                  "why": "roads are terrain, not pieces: piece_pavedroad has no "
                         "persistent ZNetView and zero road pieces exist in any "
                         "fleet save"})
        print(f"  terrain_write {len(entries)} zones -> {r['status']}")
        for c in r["checks"]:
            print("   ", c)
        # `expect` is MANDATORY and non-empty on every mutating op, and `save`
        # is mutating.  The honest cheap postcondition for a save is that the
        # thing just written is STILL THERE afterwards: count this segment's
        # own compilers at their zone centres.  A bare `save` with no
        # postcondition is a command whose effect nothing measured.
        b.emit("save", params={"role": "road_segment"}, wire=["save"],
               expect={"prefab_count": [
                   {"prefab": "_TerrainCompiler",
                    "pos": [e["centre"][0], e["centre"][1]],
                    "max": 31, "count": 1} for e in entries]})
        print(json.dumps(b.close(), indent=1)[:1200])
    return 0


if __name__ == "__main__":
    sys.exit(main())
