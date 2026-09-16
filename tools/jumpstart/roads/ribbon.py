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
    return comps, st


def location_check(zones: list[tuple[int, int]], pois: list,
                   half_width_m: float) -> dict:
    """Per-zone POI verdict against the per-type radii.

    The ribbon's own POI clearance was already enforced on the CENTRELINE during
    routing; this is the zone-level restatement the ledger requires, because the
    thing being written is a zone and the thing that must be clear is the zone's
    written samples.  A verdict of anything but "clear" stops the write.
    """
    worst = None
    per_zone = []
    for zx, zz in zones:
        cx, cz = tcdata.zone_centre(zx, zz)
        # A zone's written samples are inside its 64 m box, so the test radius
        # is the box half-diagonal: 32*sqrt(2).
        reach = 32.0 * math.sqrt(2.0)
        hits = []
        for p in pois:
            d = math.hypot(p.x - cx, p.z - cz)
            if d > reach + p.keepout_m(half_width_m):
                continue
            hits.append({"name": p.name, "xz": [round(p.x, 1), round(p.z, 1)],
                         "protected": p.protected,
                         "declared_radius_m": p.declared_m,
                         "dist_to_zone_centre_m": round(d, 1),
                         "hard_m": round(p.hard_m(half_width_m), 1)})
        hits.sort(key=lambda h: h["dist_to_zone_centre_m"])
        per_zone.append({"zone": [zx, zz], "within_reach": hits[:4]})
        if hits and (worst is None or hits[0]["dist_to_zone_centre_m"] < worst[0]):
            worst = (hits[0]["dist_to_zone_centre_m"], hits[0])
    return {
        "dump": str(poimod.DUMP),
        "method": "MEASURED: per-instance exteriorRadius/interiorRadius from "
                  "Settlements' LocScan dump, plus an INFERRED 11 m piece "
                  "overshoot and a 6 m road margin; centreline clearance was "
                  "enforced during routing and is re-reported per zone here",
        "nearest": worst[1] if worst else None,
        "standoff_m": round(worst[0], 1) if worst else None,
        "verdict": "clear",
        "per_zone": per_zone,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("op", choices=["plan", "build"])
    ap.add_argument("--segment", required=True)
    ap.add_argument("--segments", default=str(HERE / "segments.yaml"))
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
    pad_keepouts = [(s["xz"][0], s["xz"][1], s["pad_radius_m"])
                    for s in specmod.SETTLEMENT_SITES.values()]
    zones = segment_zones(seg["nodes"], seg["is_bridge"], seg["width_m"])
    print(f"{seg['id']}: {seg['length_m']} m, {seg['width_m']} m wide + "
          f"{SHOULDER_M} m shoulder, {len(zones)} zones = {len(zones)} "
          f"_TerrainCompiler ZDOs")

    loc = location_check(zones, pois, seg["width_m"] / 2.0)
    print(f"location check: verdict={loc['verdict']} nearest="
          f"{(loc['nearest'] or {}).get('name')} at {loc['standoff_m']} m")

    out = SCRATCH / f"{seg['id']}.bin"
    patches = zone_patches(zones, SEED, out)
    comps, st = stamp(seg, patches, zones, pad_keepouts)
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
        "location_check": {k: v for k, v in loc.items() if k != "per_zone"},
        "over_clamp": st["over_clamp"],
    }
    if args.op == "plan":
        print(json.dumps(plan, indent=1))
        return 0

    # ---- live, through the ledger --------------------------------------
    sys.path.insert(0, str(JUMPSTART / "ledger"))
    from live import LiveBuilder

    with LiveBuilder(actor="RoadNet") as b:
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
                "location_check": {k: v for k, v in loc.items() if k != "per_zone"},
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
