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
          pad_keepouts: list[tuple[float, float, float]],
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
          "walls_before_max_m": 0.0,
          "walls_left_by_cause": {},
          "rays": 0, "rays_clipped": 0,
          "rays_without_road": 0,
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
        # THE PAD RADIUS STOPS THE CARRIAGEWAY AND NOT THE BATTER, and the
        # asymmetry is the point.  A pad radius is a PLANNING STAND-OFF -- it
        # is where Settlements may level ground and put buildings -- so paving
        # into it is the defect the ribbon exists to avoid.  But refusing the
        # BATTER there leaves the road's own wall standing at the pad
        # boundary, and MEASURED on T12 that wall is 4.1 m once the approach
        # ramps up to meet the pad: the operator meets a cliff exactly where
        # he arrives at a settlement.  What must not move is what is actually
        # THERE -- the pad's own written samples (`foreign`), a protected
        # piece, a live placed object, water -- and every one of those is
        # gated separately and live.  So the batter may taper into the
        # stand-off, bounded by its 10.5 m reach, and the pad's own write
        # unions over it and wins if it ever comes.
        if is_road and any(math.hypot(wx - px, wz - pz) <= pr
                           for px, pz, pr in pad_keepouts):
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

        for lat, zx, zz, gx, gy, wx, wz, y, generated, _fx, _fz in members:
            run = lat - edge
            if run > batter_m:
                # Reach spent.  Not a clip with a cause somebody owns: the
                # taper simply has not met ground inside its cap, and what is
                # left is a wall like any other.
                target = y - sign * batter_grade * (lat - edge)
                _clip(st, "reach", False, None, wx, wz, lat, full_edge,
                      residual=target - generated)
                st["rays_clipped"] += 1
                break
            cause = refusal(wx, wz, generated, False)
            if cause is not None:
                target = y - sign * batter_grade * run
                _clip(st, cause, False, None, wx, wz, lat, full_edge,
                      residual=target - generated)
                if cause == "placed" and len(st["batter_clipped_placed_at"]) < 20:
                    st["batter_clipped_placed_at"].append(
                        [round(wx, 1), round(wz, 1)])
                st["rays_clipped"] += 1
                break
            # THE PLANE AT GRADE, and where it meets ground the ray is done.
            target = y - sign * batter_grade * run
            delta = target - generated
            if delta * sign <= 0.0:
                # The plane has reached or passed natural ground: the taper
                # ENDS AT GROUND, which is the whole point.  No wall, nothing
                # to record but the fact that it happened.
                st["batter_met_ground"] += 1
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
                break
            comps[(zx, zz)].set_height(gx, gy, delta)
            written.append((wx, wz))
            st["samples_batter"] += 1
            if any(math.hypot(wx - px, wz - pz) <= pr
                   for px, pz, pr in pad_keepouts):
                st["samples_batter_in_pad_standoff"] += 1
            st["max_cut_m"] = min(st["max_cut_m"], delta)
            st["max_fill_m"] = max(st["max_fill_m"], delta)
            _clamp_check(st, delta, zx, zz, gx, gy, wx, wz, generated, target,
                         "batter")

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


# How far back from a foreign claim an approach ramp may reach.  Not a taste
# number: the apply clamp is +/-8 m, so the largest step the road can possibly
# owe a pad is 8 m, and 8 / 0.781 = 10.24 m of run removes it at grade.  One
# metre of margin and the ramp can always arrive.
APPROACH_MAX_M = 11.5


def grade_into_foreign(seg: dict, patches: dict, live,
                       pad_keepouts: list[tuple[float, float, float]],
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
    # The run a ramp needs is set by the step it has to remove, and the step
    # cannot exceed the +/-8 m apply clamp.  Capped at 120 m so a pathological
    # claim cannot re-profile a whole segment silently.
    if reach_m is None:
        reach_m = APPROACH_MAX_M

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
        for px, pz, pr in pad_keepouts:
            if math.hypot(x - px, z - pz) <= pr:
                cause = "pad_keepout"
                break
        owner = None
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
    # THE RAMP GRADE, SET BY THE STEP AND BOUNDED AT BOTH ENDS.  Main's
    # ruling is "grade the last METRES into the pad at BATTER_GRADE", so the
    # reach is the bound and the grade is whatever that run needs -- as gentle
    # as possible, never gentler than the road's own design grade (there is no
    # merit in a flatter road than was fitted) and never steeper than the
    # batter's 38 deg.  MEASURED why the reach has to be the bound rather than
    # the grade: ramping T12's 2.991 m step at the design 8 % needs 37 m of
    # run, re-profiles 56 nodes and turns a 4.58 m fill into a 7.02 m
    # embankment whose own batter is then clipped by the farmhouse -- the
    # transverse wall at the pad boundary went 2.60 -> 5.09 m.  Over 11.5 m
    # the same step needs 0.26, which is a third of the slide limit and moves
    # a tenth of the earth.
    if claims and want_grade is None:
        worst = max(abs(c["step_m"]) for c in claims)
        grade = min(max(worst / reach_m, design), BATTER_GRADE)
    else:
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
                pad_keepouts: list[tuple[float, float, float]],
                pad_sites: list[dict] | None = None,
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
            math.hypot(x0 + dx - px, z0 + dz - pz) <= pr
            for dx in (0, 1) for dz in (0, 1)
            for px, pz, pr in pad_keepouts)
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
        # road-surface against PAD DATUM, not against the generated ground
        # that happens to be there until the pad is levelled.
        site = None
        for cand in (pad_sites or []):
            for k in range(i, j + 1):
                if math.hypot(stations[k]["x"] - cand["xz"][0],
                              stations[k]["z"] - cand["xz"][1]) <= cand["pad_radius_m"]:
                    site = cand
                    break
            if site is not None:
                break
        if site is not None and site.get("pad_y") is not None:
            edge = before if before is not None else after
            road_y = edge["y"] if edge is not None else None
            entry["terminates_at"] = {
                "site_id": site["id"], "pad_y": site["pad_y"],
                "pad_radius_m": site["pad_radius_m"],
                "road_y_at_pad_edge": None if road_y is None else round(road_y, 3),
                "step_road_to_pad_m": (None if road_y is None
                                       else round(site["pad_y"] - road_y, 3)),
                "pad_levelled_yet": site.get("pad_written", False),
                "why": "the road surface at the pad boundary against the height "
                       "Settlements levels that pad to. Positive means the pad "
                       "floor stands ABOVE the road and the operator has to "
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
    # PAD RADII COME FROM Settlements' OWN FILE, not from my copy of its
    # coordinates.  Main ruled tools/jumpstart/settlements/sites.yaml
    # authoritative and it is the boundary: town/village 100 m, castle 33.1,
    # watchtower 16.1, lighthouse 17.6, treehouse 13.3 -- all LARGER than the
    # provisional numbers I was handed over `hub` (12 and 10 for the small
    # types), so reading my own copy would have paved inside three pads.  One
    # source of truth, and it is the producer's.
    sites_path = JUMPSTART / "settlements" / "sites.yaml"
    pad_keepouts = []
    # The same file also carries the DATUM each pad will be levelled to, which
    # is what the road has to meet at a junction.  Keeping it beside the
    # keep-out means the junction report is measured against the height that
    # will be there rather than the generated ground that is there now.
    pad_sites = []
    pad_source = "none"
    if sites_path.exists():
        sdoc = yaml.safe_load(sites_path.read_text())
        for site in sdoc["sites"]:
            pad_keepouts.append((float(site["xz"][0]), float(site["xz"][1]),
                                 float(site["pad_radius_m"])))
            pad_sites.append({"id": site["id"],
                              "xz": [float(site["xz"][0]), float(site["xz"][1])],
                              "pad_radius_m": float(site["pad_radius_m"]),
                              "pad_y": (None if site.get("pad_y") is None
                                        else float(site["pad_y"]))})
        pad_source = str(sites_path)
    else:
        for sid, site in specmod.SETTLEMENT_SITES.items():
            pad_keepouts.append((site["xz"][0], site["xz"][1], site["pad_radius_m"]))
            pad_sites.append({"id": sid, "xz": list(site["xz"]),
                              "pad_radius_m": site["pad_radius_m"],
                              "pad_y": site.get("pad_y")})
        pad_source = "spec.SETTLEMENT_SITES (PROVISIONAL fallback)"
    print(f"pad keep-outs: {len(pad_keepouts)} from {pad_source}")
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
    # RULE 1, MAIN'S RULING: REWIND PAST THIS SEGMENT'S OWN PRIOR WRITES.
    #
    # THE UNION LAUNDERS FOREIGN OWNERSHIP, and this is the line that stops it
    # poisoning the next repair.  A repair MUST union every prior claim in a
    # zone forward into its own blob -- a zone holds exactly one
    # `_TerrainCompiler` and this op does `deleteObjects -zone` first, so
    # anything it does not carry is destroyed.  But the carried samples then
    # sit in a blob whose `role` is `road_segment` and whose `name` is this
    # road's, so `foreign_at` -- the instrument that implements "inside a
    # settlement pad the pad wins", and which is deliberately blind to radii
    # because that is what caught T4's clobber -- cannot see the pad any more.
    #
    # MEASURED on T8-stenvik-wttown: its write (seq 1197) refused 123
    # carriageway samples because a site_pad owned them; the IDENTICAL
    # rasterisation run against the surface that write produced finds ZERO and
    # authors 810 batter samples where the write made 706.  Those 123 samples
    # are a building's foundation and the second repair would pave them.
    #
    # Dropping this segment's own entries makes the per-sample owner fall back
    # to the previous claim in FILE ORDER -- the pad's own record -- which is
    # the state the FIRST build was handed.  The UNION still reads every prior
    # including these, from the ledger directly, so nothing is lost from the
    # blob: only the ownership question is rewound.
    my_name_early = f"road_{seg['id']}".replace("-", "_")
    laundered = [w for w in live.writes if w["name"] == my_name_early]
    live.writes = [w for w in live.writes if w["name"] != my_name_early]
    live._zones = {}
    print(f"live surface: {len(live.writes)} terrain_write zone entries in the "
          f"ledger, {len({w['zone'] for w in live.writes})} zones claimed; "
          f"OWNERSHIP REWOUND past {len(laundered)} of this segment's own zone "
          f"entries (seq {sorted({w['seq'] for w in laundered})}) so a pad's "
          f"samples are inherited from the PAD's claim and not from this "
          f"road's laundered copy of it")
    approach = grade_into_foreign(seg, patches, live, pad_keepouts)
    print(f"approach grading: {approach['claims']} stations claimed by "
          f"{approach.get('claims_by_cause', {})}"
          + (f" owners {approach['owners']}" if approach.get("owners") else "")
          + f"; regraded {approach['nodes_regraded']} profile nodes "
          f"(worst move {approach.get('max_profile_move_m')} m); step into the "
          f"claim {approach['worst_step_before_m']} -> "
          f"{approach['worst_step_after_m']} m")
    for c in approach.get("conflicts", [])[:5]:
        print("   CONFLICT", c)

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
    comps, st = stamp(seg, patches, zones, pad_keepouts, prot["pieces"],
                      prot["discs"], placed_objects=placed,
                      poi_keepouts=poi_keepouts, batter_m=batter_m,
                      foreign_at=foreign_at, road_claim_at=road_claim_at)
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
        zc = live.zone(zx, zz)
        cx0, cz0 = tcdata.zone_centre(zx, zz)
        for i in range(tcdata.SAMPLES):
            if not comp.modified_height[i] or not zc["modified"][i]:
                continue
            o = int(zc["owner"][i])
            w = live.writes[o] if o >= 0 else None
            if w is None or w["role"] == appliedmod.ROAD_ROLE:
                continue
            gy, gx = divmod(i, tcdata.PITCH)
            wx, wz = tcdata.sample_world(cx0, cz0, gx, gy)
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
    if not args.validate:
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
        # Which pads have actually been levelled yet: a site whose name
        # prefixes an existing terrain_write. It changes what a junction step
        # MEANS -- against a levelled pad it is the real step today, against an
        # unlevelled one it is the step the operator will meet once
        # Settlements gets there, and those are different claims.
        written_names = [r["params"].get("name") or ""
                         for r in led_ro.records() if r["op"] == "terrain_write"]
        for site in pad_sites:
            site["pad_written"] = any(n.startswith(site["id"])
                                      for n in written_names)

    # THE OPERATOR'S OWN TEST, on the surface that will exist after the union.
    # Run here rather than before the merge because a junction zone's carried
    # samples are part of the rendered mesh: at the temple the T3 ribbon meets
    # T12's, and continuity across that joint is a property of the union, not
    # of this segment alone.
    walk = walkability(seg, comps, patches, pad_keepouts, pad_sites,
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
        return 0 if not (bad or zbad) else 4

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
