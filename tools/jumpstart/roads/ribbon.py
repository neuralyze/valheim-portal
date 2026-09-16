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

import poi as poimod  # noqa: E402
import route as routemod  # noqa: E402
import spec as specmod  # noqa: E402
import tcdata  # noqa: E402

SEED = "Pirate68"
SCRATCH = Path("/tmp/roads/zonepatch")
# WHO THE LEDGER SAYS BUILT THE ROAD.  `RoadNet` routed and pre-flighted the
# network; `RoadBuild` writes it.  The log has to say who did what or the
# provenance of a defect is a guess.
ACTOR = "RoadBuild"
# Shoulder: one metre of dirt each side of the paved carriageway.  It is not
# decoration -- it is the visual edge that tells the operator where the road is
# from a distance, and it gives the paved band a margin so a half-metre
# rasterisation difference at the edge cannot leave a gap in the paving.
SHOULDER_M = 1.0
PAINT_ROAD = "paved_cleared"
PAINT_SHOULDER = "dirt_cleared"


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------

def segment_zones(nodes: list, is_bridge: list, width_m: float,
                  shoulder_m: float = SHOULDER_M) -> list[tuple[int, int]]:
    """Zones whose lattice the ribbon's terrain part touches.

    Densified to 1 m between stations: at a 2 m station spacing a ribbon that
    merely clips a zone corner between two stations would otherwise be missed,
    and a missed zone is a GAP IN THE ROAD -- the one defect the operator is
    guaranteed to walk into.
    """
    half = width_m / 2.0 + shoulder_m
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
           "SANDBOX": "/tmp/patchscan/vh"}
    proc = subprocess.run(["bash", str(JUMPSTART / "blueprints" / "run_patchscan.sh")],
                          env=env, capture_output=True, text=True)
    if not out.exists() or out.stat().st_size == 0:
        raise SystemExit(f"patchscan produced nothing:\n{proc.stderr[-4000:]}")
    sys.path.insert(0, str(JUMPSTART / "terraform"))
    import heights as patchheights
    return patchheights.load(out)


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


def stamp(seg: dict, patches: dict, zones: list[tuple[int, int]],
          pad_keepouts: list[tuple[float, float, float]],
          protected_pieces: list[tuple[float, float]] | None = None,
          protected_discs: list[tuple[float, float, float]] | None = None,
          ) -> tuple[dict, dict]:
    """Rasterise one segment into per-zone compilers.  Returns (comps, stats).

    Three things stop the ribbon rather than one, and each is somebody's
    property: a Settlements PAD (its earthwork, levelled per building), a
    protected PIECE (an over-water structure whose ground must not move -- this
    is the `flatten: FORBIDDEN` rule expressed at the granularity the hazard
    actually has), and A PROTECTED STRUCTURE'S WATER.

    THE WATER RULE IS SCOPED TO THE STRUCTURES, NOT TO THE WATER PLANE, and
    the first version got that wrong.  Refusing every sample below 30.0 m
    sounds safe and is not the rule: MEASURED on W5-brgs1-wsouth and
    W10-brgs1-treenear, RoadNet's profile crosses an 8 m pond on 2.9 m of
    fill -- a CAUSEWAY, with no structure within 200 m -- and a blanket rule
    left an 8 m swim in the middle of a finished road.  The hazard the rule
    exists for is removing the water a protected structure STANDS OVER, so it
    is tested inside that structure's OWN recorded extent and nowhere else.
    """
    nodes = np.array(seg["nodes"], dtype=np.float64)
    prof = np.array(seg["profile_y"], dtype=np.float64)
    br = np.array(seg["is_bridge"], dtype=bool)
    half = seg["width_m"] / 2.0
    edge = half + SHOULDER_M
    road_colour = tcdata.PAINTS[PAINT_ROAD]
    shoulder_colour = tcdata.PAINTS[PAINT_SHOULDER]

    comps: dict[tuple[int, int], tcdata.Compiler] = {}
    st = {"samples_paved": 0, "samples_shoulder": 0, "skipped_pad": 0,
          "skipped_protected": 0, "skipped_underwater": 0,
          "max_cut_m": 0.0, "max_fill_m": 0.0, "over_clamp": 0,
          "over_clamp_samples": [], "per_zone": {}}
    # The world positions of every sample this write actually TOUCHES.  The
    # location check is run against these, not against zone centres -- see
    # `location_check`.
    written: list[tuple[float, float]] = []

    for zx, zz in zones:
        patch = patches[f"z_{zx}_{zz}"]
        comp = tcdata.Compiler(zone_x=zx, zone_z=zz)
        zcx, zcz = comp.centre
        touched = 0
        for gy in range(tcdata.PITCH):
            for gx in range(tcdata.PITCH):
                wx, wz = tcdata.sample_world(zcx, zcz, gx, gy)
                got = lateral_and_y(nodes, prof, br, wx, wz)
                if got is None:
                    continue
                lat, y = got
                if lat > edge:
                    continue
                # Settlements' pads are its earthwork.  A road levelling ground
                # under a building is the defect class this whole build exists
                # to avoid, so the ribbon simply stops at the pad edge.
                if any(math.hypot(wx - px, wz - pz) <= pr
                       for px, pz, pr in pad_keepouts):
                    st["skipped_pad"] += 1
                    continue
                # A PROTECTED PIECE'S GROUND MUST NOT MOVE.  Chebyshev, not
                # Euclidean: the samples that can move the ground at a piece
                # are precisely the four the mesh blends around it, one pitch
                # away on each axis. MEASURED why this exists: S12 moved the
                # ground 2.896 m under the boathouse's over-water pieces and
                # 0.986 m under a pile, and T13 moved 1.629 m under a harbour
                # pile -- both inside their own zone's FORBIDDEN structure,
                # both invisible to a distance-from-centre test.
                if any(abs(wx - px) <= PROTECT_CLEAR_M
                       and abs(wz - pz) <= PROTECT_CLEAR_M
                       for px, pz in (protected_pieces or ())):
                    st["skipped_protected"] += 1
                    continue
                generated = float(patch.at(gy, gx))
                # THE WATER A PROTECTED STRUCTURE STANDS OVER.  Not all water:
                # raising a pond bed outside every protected extent is a
                # causeway, which is what a road does. Inside one it is the
                # early-dock defect.
                if generated < WATER_LEVEL_M and any(
                        math.hypot(wx - px, wz - pz) <= pr
                        for px, pz, pr in (protected_discs or ())):
                    st["skipped_underwater"] += 1
                    continue
                delta = y - generated
                comp.set_height(gx, gy, delta)
                comp.set_paint(gx, gy, road_colour if lat <= half else shoulder_colour)
                written.append((wx, wz))
                if lat <= half:
                    st["samples_paved"] += 1
                else:
                    st["samples_shoulder"] += 1
                touched += 1
                st["max_cut_m"] = min(st["max_cut_m"], delta)
                st["max_fill_m"] = max(st["max_fill_m"], delta)
                # THE CLAMP, CHECKED PER SAMPLE.  Not per segment and not on the
                # fitting lattice: this is the integer lattice the game will
                # apply, and `TerrainComp::ApplyToHeightmap` silently discards
                # anything past +/- 8 m of the generated height, so a sample over
                # the limit is a piece of road that is not where the plan says.
                if abs(delta) > tcdata.CLAMP_M:
                    st["over_clamp"] += 1
                    if len(st["over_clamp_samples"]) < 20:
                        st["over_clamp_samples"].append(
                            {"zone": [zx, zz], "sample": [gx, gy],
                             "world": [round(wx, 1), round(wz, 1)],
                             "generated_y": round(generated, 2),
                             "target_y": round(y, 2), "delta_m": round(delta, 2)})
        if touched:
            comps[(zx, zz)] = comp
            st["per_zone"][f"{zx},{zz}"] = touched
    st["max_cut_m"] = round(st["max_cut_m"], 3)
    st["max_fill_m"] = round(st["max_fill_m"], 3)
    st["written"] = written
    return comps, st


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


def location_check(comps: dict, written: list[tuple[float, float]],
                   half_width_m: float) -> dict:
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
    gate = delta_gate(comps, written, L, clearance.DELTA_TOL_M)
    v = L.verdict_for_samples(written, half_width_m=half_width_m,
                              destructive=False, delta_gate=gate)
    v["dump"] = dump
    v["destructive"] = False
    v["destructive_claim"] = (
        "this pipeline emits zones_generate + terrain_write only and never "
        "objects_clear, so it cannot delete a location's ZDOs; verifiable from "
        "the ledger records for this segment")
    return v


# ---------------------------------------------------------------------------
# the thing the operator judges: is it CONTINUOUS and WALKABLE
# ---------------------------------------------------------------------------

# Player::UpdateMovement slides the character when the ground normal is steeper
# than 38 deg, so the hard limit on a walkable surface is tan(38 deg).  A road
# the operator slides down is not a road.
SLIDE_ANGLE_DEG = 38.0
SLIDE_GRADIENT = math.tan(math.radians(SLIDE_ANGLE_DEG))
# The BASELINE THE VERDICT IS TAKEN OVER.  The 1 m figure is a DIFFERENT
# QUANTITY: it is the slope of one lattice edge, and a 1 m rise between two
# adjacent samples on a fitted 8% profile is a rounding artefact of the integer
# lattice, not a wall.  Measuring the verdict at 1 m produced a false
# NOT WALKABLE earlier tonight, so both are reported and the verdict is taken
# over 8 m -- roughly the run the capsule actually traverses while the collider
# averages the mesh under it.
VERDICT_BASELINE_M = 8.0


def applied_at(comps: dict, patches: dict, x: float, z: float):
    """The height the game will RENDER at (x, z), and how much of it is road.

    Returns (applied_y, generated_y, corners_modified) or None when the
    generated lattice around the point is not in hand.  This is
    `generated bilinear + delta_at`, which is exactly `Heightmap`'s own
    composition: the mesh interpolates linearly between 1 m samples and an
    unwritten sample contributes zero delta.  Reading the PROFILE instead would
    answer what the plan intended rather than what the player stands on -- and
    the difference IS the edge, the pad gap and the ribbon end.
    """
    x0, z0 = math.floor(x), math.floor(z)
    tx, tz = x - x0, z - z0
    gen = 0.0
    modified = 0
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
        if comp is not None and comp.modified_height[gy * tcdata.PITCH + gx]:
            modified += 1
    return gen + delta_at(comps, x, z), gen, modified


def walkability(seg: dict, comps: dict, patches: dict,
                pad_keepouts: list[tuple[float, float, float]],
                pad_sites: list[dict] | None = None,
                protected_pieces: list[tuple[float, float]] | None = None,
                protected_discs: list[tuple[float, float, float]] | None = None,
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

    off_lattice = 0
    for st in stations:
        got = applied_at(comps, patches, st["x"], st["z"])
        if got is None:
            st["y"] = None
            off_lattice += 1
        else:
            st["y"], st["gen"], st["mod"] = got
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
        cause = ("bridge" if any(stations[k]["bridged"] for k in range(i, j + 1))
                 else "settlement_pad" if any(stations[k]["in_pad"]
                                              for k in range(i, j + 1))
                 else "protected_structure" if any(stations[k]["near_protected"]
                                                   for k in range(i, j + 1))
                 else "below_water_plane" if any(stations[k]["underwater"]
                                                 for k in range(i, j + 1))
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
                 "fill_at_edges_m": lip}
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
    zones = segment_zones(seg["nodes"], seg["is_bridge"], seg["width_m"])
    print(f"{seg['id']}: {seg['length_m']} m, {seg['width_m']} m wide + "
          f"{SHOULDER_M} m shoulder, {len(zones)} zones = {len(zones)} "
          f"_TerrainCompiler ZDOs")

    out = SCRATCH / f"{seg['id']}.bin"
    patches = zone_patches(zones, SEED, out)
    prot = protected_piece_positions(zones)
    print(f"protected pieces in these zones: {len(prot['pieces'])} "
          f"{prot['by_site'] or '{}'}"
          + (f"; records with NO recoverable piece: {prot['without_pieces']}"
             if prot["without_pieces"] else ""))
    comps, st = stamp(seg, patches, zones, pad_keepouts, prot["pieces"],
                      prot["discs"])
    loc = location_check(comps, st["written"], seg["width_m"] / 2.0)
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
          f"shoulder samples over {len(comps)} zones; "
          f"cut {st['max_cut_m']} fill {st['max_fill_m']} m; skipped "
          f"{st['skipped_pad']} inside a Settlements pad, "
          f"{st['skipped_protected']} beside a protected piece, "
          f"{st['skipped_underwater']} on a protected structure's water")
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
            for pr in plist:
                old = tcdata.parse(led_ro.read_blob(pr["sha"]))
                for i, (lvl, sm) in old["heights"].items():
                    if not comp.modified_height[i]:
                        comp.modified_height[i] = True
                        comp.level_delta[i] = lvl
                        comp.smooth_delta[i] = sm
                        carried += 1
                for i, col in old["paints"].items():
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
                       prot["pieces"], prot["discs"])
    walk["skipped"] = {"settlement_pad": st["skipped_pad"],
                       "beside_protected_piece": st["skipped_protected"],
                       "on_protected_structure_water": st["skipped_underwater"]}
    walk["protected_pieces_in_zones"] = prot["by_site"]
    walk["protected_records_without_pieces"] = prot["without_pieces"]
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

    with LiveBuilder(actor=ACTOR, dry=args.dry) as b:
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
