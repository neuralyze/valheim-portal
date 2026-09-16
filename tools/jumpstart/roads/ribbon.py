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
        if tt < 0.0 or tt > 1.0:
            continue
        px, pz = ax + dx * tt, az + dz * tt
        d = math.hypot(x - px, z - pz)
        if best is None or d < best[0]:
            best = (d, prof[k] + (prof[k + 1] - prof[k]) * tt)
    if best is None:
        # Beyond both ends of every segment: fall back to the nearest terrain
        # station, so a ribbon END is square rather than tapering to a point.
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


# ---------------------------------------------------------------------------
# the ribbon
# ---------------------------------------------------------------------------

def stamp(seg: dict, patches: dict, zones: list[tuple[int, int]],
          pad_keepouts: list[tuple[float, float, float]],
          ) -> tuple[dict, dict]:
    """Rasterise one segment into per-zone compilers.  Returns (comps, stats)."""
    nodes = np.array(seg["nodes"], dtype=np.float64)
    prof = np.array(seg["profile_y"], dtype=np.float64)
    br = np.array(seg["is_bridge"], dtype=bool)
    half = seg["width_m"] / 2.0
    edge = half + SHOULDER_M
    road_colour = tcdata.PAINTS[PAINT_ROAD]
    shoulder_colour = tcdata.PAINTS[PAINT_SHOULDER]

    comps: dict[tuple[int, int], tcdata.Compiler] = {}
    st = {"samples_paved": 0, "samples_shoulder": 0, "skipped_pad": 0,
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
                generated = float(patch.at(gy, gx))
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("op", choices=["plan", "build"])
    ap.add_argument("--segment", required=True)
    ap.add_argument("--segments", default=str(HERE / "segments.yaml"))
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
    pad_source = "none"
    if sites_path.exists():
        sdoc = yaml.safe_load(sites_path.read_text())
        for site in sdoc["sites"]:
            pad_keepouts.append((float(site["xz"][0]), float(site["xz"][1]),
                                 float(site["pad_radius_m"])))
        pad_source = str(sites_path)
    else:
        for site in specmod.SETTLEMENT_SITES.values():
            pad_keepouts.append((site["xz"][0], site["xz"][1], site["pad_radius_m"]))
        pad_source = "spec.SETTLEMENT_SITES (PROVISIONAL fallback)"
    print(f"pad keep-outs: {len(pad_keepouts)} from {pad_source}")
    zones = segment_zones(seg["nodes"], seg["is_bridge"], seg["width_m"])
    print(f"{seg['id']}: {seg['length_m']} m, {seg['width_m']} m wide + "
          f"{SHOULDER_M} m shoulder, {len(zones)} zones = {len(zones)} "
          f"_TerrainCompiler ZDOs")

    out = SCRATCH / f"{seg['id']}.bin"
    patches = zone_patches(zones, SEED, out)
    comps, st = stamp(seg, patches, zones, pad_keepouts)
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
          f"cut {st['max_cut_m']} fill {st['max_fill_m']} m; "
          f"{st['skipped_pad']} samples skipped inside a Settlements pad")
    print(f"samples past the MEASURED +/-8 m apply clamp: {st['over_clamp']}")
    for s in st["over_clamp_samples"]:
        print("   ", s)
    if st["over_clamp"]:
        print("REFUSING: a sample past the clamp is road that will not be where "
              "the plan says. Re-plan the segment.")
        return 2

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

    plan = {
        "segment": seg["id"], "zones": len(entries),
        "zdo_cost": len(entries),
        "paved_samples": st["samples_paved"],
        "shoulder_samples": st["samples_shoulder"],
        "paved_m2": st["samples_paved"] * 1.0,
        "location_check": {k: v for k, v in loc.items() if k != "violations"},
        "over_clamp": st["over_clamp"],
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

    with LiveBuilder(actor="RoadNet", dry=args.dry) as b:
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
                  "why": "roads are terrain, not pieces: piece_pavedroad has no "
                         "persistent ZNetView and zero road pieces exist in any "
                         "fleet save"})
        print(f"  terrain_write {len(entries)} zones -> {r['status']}")
        for c in r["checks"]:
            print("   ", c)
        b.emit("save", params={}, wire=["save"], expect={})
        print(json.dumps(b.close(), indent=1)[:1200])
    return 0


if __name__ == "__main__":
    sys.exit(main())
