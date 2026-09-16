#!/usr/bin/env python3
"""Level a building pad headlessly, by writing terrain data instead of using a hoe.

ROUTE, and why it is this one (all three measured on Ulfsland tonight):

  1. World Edit Commands' `terrain` command IS reachable from RCON through
     `consoleCommand` -- it parses its arguments and prints its own errors -- but
     it selects ground with `Heightmap::FindHeightmap`, which scans the static
     list of INSTANTIATED zone heightmaps.  A dedicated server with no players
     instantiates no zones, so the selection is always empty: the command
     returns silently, creates no `_TerrainCompiler`, and changes nothing.
     MEASURED: `terrain from=241.5,353.5,78 circle=8 level=77.47` produced no
     console output and no ZDO.
  2. ValheimRcon's `modifyObject` cannot reach ZDO byte arrays -- its own
     `list` output offers only -position, -rotation, -health, -tag, -prefab.
     So plain RCON cannot write TCData.
  3. WEC's `spawn_object <prefab> from=x,z,y data=<entry>` CAN.  A WEC data
     entry is YAML in <BepInEx config>/data/*.yaml and its `bytes:` field is a
     list of "<key>, <base64>" strings, hashed with the ZDO key hash and
     base64-decoded straight into the ZDO's byte-array map (MEASURED from
     WorldEditCommands.dll IL: Convert::FromBase64String into
     Data.DataEntry::ByteArrays).  `spawn_object` needs no player: it is the
     same headless path ValheimRcon's `spawn` uses.

So: synthesise TCData offline, drop it in a data entry, and have the server
spawn a `_TerrainCompiler` already carrying it.  No byte-level save surgery, no
stop/start, and the zone does not need to be loaded.

HEIGHTS.  The delta is applied to the generated height, so the generated height
has to be known per sample.  That comes from PatchScan
(tools/jumpstart/blueprints/run_patchscan.sh), requested at half=32.5 step=1
centred on each zone centre, which lands its 65x65 lattice exactly on that
zone's TerrainComp samples.

Usage:
    flatten.py plan   --world Ulfsland --preset pre-eikthyr --id meadows-starter-hall
    flatten.py apply  --world Ulfsland --preset pre-eikthyr --id meadows-starter-hall
"""

from __future__ import annotations

import argparse
import math
import re
import subprocess
import sys
import time
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import tcdata  # noqa: E402
import heights as patchheights  # noqa: E402
import locations  # noqa: E402
from rcon import Rcon  # noqa: E402
from console import run_console  # noqa: E402

JUMPSTART = HERE.parent
REPO = JUMPSTART.parent.parent
VH_SRC = Path("/media/big4/projects/game/valheim/Ulfsland/data/bepinex")
DATA_DIR = Path("/media/big4/projects/game/valheim/Ulfsland/config_merged/bepinex/data")
SCRATCH = Path("/tmp/terraform")
# The per-type location dump (tools/seedscan/LocScan.cs): name, position and the
# ZoneLocation's own exteriorRadius / interiorRadius / quantity / prioritized /
# centerFirst.  MOD-FREE by construction -- run_locscan.sh excludes BepInEx --
# so it cannot see More_World_Locations' POIs, which is exactly why
# locations.py cross-reads it against LIVE markers instead of trusting it.
DEFAULT_LOCATION_DUMP = "/tmp/settle/loc2/f6fe167f4fcd.json"


def placements_path(world: str, preset: str, override: str | None) -> Path:
    """Where the placement records live.  `override` exists so an ORDERING
    experiment can be run against a throwaway pad without inventing a preset
    under worlds/, which would read as a thirteenth installation."""
    if override:
        return Path(override)
    return JUMPSTART / "worlds" / world / preset / "placements.yaml"


def placement(world: str, preset: str, pid: str, override: str | None = None) -> dict:
    path = placements_path(world, preset, override)
    doc = yaml.safe_load(path.read_text())
    for p in doc["placements"]:
        if p["id"] == pid:
            return p
    raise SystemExit(f"no placement {pid} in {path}")


def pad_extent(place: dict) -> tuple[float, float]:
    """The square pad the solver costed.  `footprint_orientation` in the solved
    block names it as '<w> m along x by <d> m along z'; that is the authority,
    because the flatten bill was computed over exactly that rectangle."""
    solved = place["solved"]
    text = solved.get("footprint_orientation", "")
    parts = text.replace("m along x by", "|").replace("m along z", "").split("|")
    if len(parts) == 2:
        try:
            return float(parts[0].strip()), float(parts[1].strip())
        except ValueError:
            pass
    fp = place.get("footprint_xzy_m") or place.get("footprint_xz_m")
    return float(fp[0]), float(fp[1])


def zones_for(cx: float, cz: float, w: float, d: float) -> list[tuple[int, int]]:
    """Every zone whose 65x65 sample lattice touches the pad.  A sample lives in
    the zone whose centre is within 32 m of it, and edge samples belong to two
    zones, so the set is taken over the pad corners AND the whole span."""
    x0, x1 = cx - w / 2, cx + w / 2
    z0, z1 = cz - d / 2, cz + d / 2
    out = set()
    for x in (x0, x1):
        for z in (z0, z1):
            out.add(tcdata.zone_of(x, z))
    # Fill in any zone between the corners, for a pad wider than one zone.
    xs = sorted({zx for zx, _ in out})
    zs = sorted({zz for _, zz in out})
    return [(zx, zz) for zx in range(xs[0], xs[-1] + 1) for zz in range(zs[0], zs[-1] + 1)]


def run_patchscan(zones: list[tuple[int, int]], seed: str, out: Path) -> dict[str, patchheights.Patch]:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    req = SCRATCH / "patch_req.tsv"
    lines = []
    for zx, zz in zones:
        cx, cz = tcdata.zone_centre(zx, zz)
        lines.append(f"z_{zx}_{zz}\t{cx:g}\t{cz:g}\t32.5\t1")
    req.write_text("\n".join(lines) + "\n")
    env = {
        "VH_SRC": str(VH_SRC), "SEED": seed, "REQ": str(req), "OUT": str(out),
        "SANDBOX": "/tmp/patchscan/vh",
    }
    cmd = ["bash", str(JUMPSTART / "blueprints" / "run_patchscan.sh")]
    proc = subprocess.run(cmd, env={**dict(__import__("os").environ), **env},
                          capture_output=True, text=True)
    if not out.exists() or out.stat().st_size == 0:
        raise SystemExit(f"patchscan produced nothing:\n{proc.stderr[-2000:]}")
    return patchheights.load(out)


def build(place: dict, patches: dict[str, patchheights.Patch], zones: list[tuple[int, int]],
          paint: str, apron: float,
          comps: dict[tuple[int, int], tcdata.Compiler],
          op_y: dict[tuple[int, int], float]) -> dict:
    """Stamp one placement's pad into the shared per-zone compilers.

    Compilers are shared rather than one-per-placement because a zone holds
    exactly ONE `_TerrainCompiler`: `Heightmap::GetAndCreateTerrainCompiler`
    returns the first it finds, so a second compiler in the same zone is dead
    weight whose data is never applied.  Two pads in one zone therefore have to
    merge into one blob.
    """
    solved = place["solved"]
    cx, cz = float(solved["x"]), float(solved["z"])
    w, d = pad_extent(place)
    cost = solved.get("flatten_cost") or {}
    target = float(cost.get("target_y") or solved.get("y_centre_m") or solved["y"])
    colour = tcdata.PAINTS[paint]
    stats = {"id": place["id"], "pad_w": w, "pad_d": d, "target_y": target, "samples": 0,
             "max_cut": 0.0, "max_fill": 0.0, "over_clamp": 0, "checks": []}
    half_w, half_d = w / 2 + apron, d / 2 + apron
    for zx, zz in zones:
        patch = patches[f"z_{zx}_{zz}"]
        comp = comps.setdefault((zx, zz), tcdata.Compiler(zone_x=zx, zone_z=zz))
        zcx, zcz = comp.centre
        touched = 0
        for y in range(tcdata.PITCH):
            for x in range(tcdata.PITCH):
                wx, wz = tcdata.sample_world(zcx, zcz, x, y)
                if abs(wx - cx) > half_w or abs(wz - cz) > half_d:
                    continue
                # Sample (x, y) of this zone is sample (i=y, j=x) of the patch: the
                # request is centred on the same point with the same step, so
                # patch j == mask x and patch i == mask y.
                generated = patch.at(y, x)
                delta = target - generated
                comp.set_height(x, y, delta)
                comp.set_paint(x, y, colour)
                touched += 1
                stats["max_cut"] = min(stats["max_cut"], delta)
                stats["max_fill"] = max(stats["max_fill"], delta)
                if abs(delta) > tcdata.CLAMP_M:
                    stats["over_clamp"] += 1
        if touched == 0:
            continue
        stats["samples"] += touched
        # The pad height this zone was levelled to, for the compiler's op
        # record.  Two pads sharing a zone is rare and their targets differ by
        # less than the grass reset cares about (it compares x and z only), so
        # the last writer wins and nothing depends on which.
        op_y[(zx, zz)] = target
        # One verification per zone: a sample at a named world position, its
        # generated height, and the height the game will end up with.
        mid_x, mid_y = tcdata.vertex_mask_index(zcx, zcz, cx, cz)
        if 0 <= mid_x < tcdata.PITCH and 0 <= mid_y < tcdata.PITCH:
            i = mid_y * tcdata.PITCH + mid_x
            stats["checks"].append({
                "zone": [zx, zz],
                "sample": [mid_x, mid_y],
                "world": list(tcdata.sample_world(zcx, zcz, mid_x, mid_y)),
                "generated_m": round(patch.at(mid_y, mid_x), 3),
                "level_delta_m": round(comp.level_delta[i], 3),
                "result_m": round(patch.at(mid_y, mid_x) + comp.level_delta[i], 3),
            })
    stats["max_cut"] = round(stats["max_cut"], 3)
    stats["max_fill"] = round(stats["max_fill"], 3)
    return stats


def write_entries(name: str, comps: dict[tuple[int, int], tcdata.Compiler],
                  op_y: dict[tuple[int, int], float]) -> tuple[Path, list[dict]]:
    """One WEC data entry per zone.  The entry name carries the zone because
    each zone gets different bytes, and the file is one YAML list so the whole
    run reloads in a single watcher event.

    `op_y` is the pad height stamped into each zone's `m_lastOpPoint`.  See
    tcdata's header: that record and `m_operations` decide which grass a
    client's `ClutterSystem` throws away when it loads the compiler, and a blob
    that gets them wrong leaves the pad's grass floating at its old height.
    The self-check reports them so a wrong one is visible in the run output
    rather than only in the operator's screenshot.
    """
    import base64

    entries = []
    doc = []
    for (zx, zz), comp in sorted(comps.items()):
        blob = comp.blob(op_y.get((zx, zz), 0.0))
        entry = f"{name}_z{zx}_{zz}".replace("-", "_")
        doc.append({"name": entry, "bytes": ["TCData, " + base64.b64encode(blob).decode()]})
        cx, cz = comp.centre
        entries.append({"entry": entry, "zone": [zx, zz], "centre": [cx, cz],
                        "blob_bytes": len(blob), "counts": comp.counts(),
                        "selfcheck": {k: v for k, v in tcdata.parse(blob).items()
                                      if k in ("version", "samples", "plain_bytes", "blob_bytes",
                                               "operations", "last_op_point", "last_op_radius")}})
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"terraform_{name}.yaml".replace("-", "_")
    path.write_text(yaml.safe_dump(doc, default_flow_style=False, width=10**9, sort_keys=False))
    return path, entries


ZONE_CTRL = "_ZoneCtrl"
TOTAL_RE = re.compile(r"^Total:?\s*(\d+)\s*$", re.M)


def ungenerated_zones(rc: Rcon, zones: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Which of `zones` the world has never generated.

    THIS IS A PRECONDITION, NOT A DIAGNOSTIC, and it is the measured fix for
    the floating-vegetation defect.  MEASURED on Ulfsland at zone (-18, 28) on
    2026-09-15: a compiler was spawned into an ungenerated zone, the zone was
    then generated, and all 190 objects it planted landed on the UNMODIFIED
    generated height -- median offset from the patchscan height 0.000 m, worst
    0.42 m -- which stood them up to 7.68 m ABOVE the flattened pad, as real
    ZDOs, permanently.  The paint's vegetation-cleared alpha was ignored too:
    Bush01, RaspberryBush, Beech1 and eleven more prefabs were placed on ground
    painted alpha 0.

    The cause, from IL: `Heightmap::Generate` ends by calling `ApplyModifiers`,
    which locates its compiler through `TerrainComp::FindTerrainCompiler` -- a
    scan of the static `s_instances` list of INSTANTIATED components.  A
    dedicated server with no peers instantiates no `_TerrainCompiler`
    ZNetView, so the list is empty, no deltas and no cleared mask reach the
    heightmap, and `ZoneSystem::SpawnZone` then calls `PlaceVegetation`
    straight away -- whose `GetGroundData` is a downward `Physics.Raycast`
    against that unmodified collider.

    A `_ZoneCtrl` is planted at the zone CENTRE by `PlaceZoneCtrl` (MEASURED:
    exactly (-1152, 0, 1408) for zone (-18, 22)), and `objects_count`'s
    `pos`/`max` filter is a vertical cylinder on `Utils.DistanceXZ`, so a
    1 m probe at the zone centre is an exact test with a two-line answer.
    """
    missing = []
    for zx, zz in zones:
        cx, cz = tcdata.zone_centre(zx, zz)
        lines = run_console(
            rc, f"objects_count id={ZONE_CTRL} pos={cx:g},{cz:g} max=1", settle=3.0)
        found = TOTAL_RE.search("\n".join(lines))
        if not found:
            raise SystemExit(
                f"zone {zx},{zz}: objects_count printed no Total line, so whether the zone "
                f"is generated is unknown. Refusing to write terrain blind; console said "
                f"{lines!r}")
        if int(found.group(1)) == 0:
            missing.append((zx, zz))
    return missing


def pad_location_check(rc: Rcon, place: dict, apron: float,
                       dump: Path) -> tuple[list, str]:
    """Generated locations whose stand-off this pad's rectangle reaches into.

    Separate from `clearing/area.py`'s check on purpose: that one guards the
    CLEARING cylinder and only runs when clearing runs, and MEASURED on
    2026-09-15 a flatten cut 6.65 m out from under a `LocationProxy` holding a
    `TreasureChest_meadows_buried` with nothing complaining.  A terrain write
    is destructive on its own and needs its own gate.

    Two sources, because neither is sufficient alone -- the live world sees
    modded POIs but cannot name a marker's type, and the mod-free dump names
    the type and carries its declared radii but contains no modded location.
    See locations.py for the authority ordering and for which term of the
    stand-off is measured, which is inferred, and which is taste.
    """
    solved = place["solved"]
    cx, cz = float(solved["x"]), float(solved["z"])
    w, d = pad_extent(place)
    half_w, half_d = w / 2 + apron, d / 2 + apron
    catalogue = locations.load_dump(dump)
    probe = locations.probe_radius_m(catalogue, half_w, half_d)
    target = float((solved.get("flatten_cost") or {}).get("target_y")
                   or solved.get("y_centre_m") or solved["y"])
    # Prefab-scoped and radius-bounded: `findObjects` refuses without both, and
    # an unbounded listing is what wedges the console.
    reply = rc.command(f"findObjects -prefab {locations.LOCATION_MARKER} "
                       f"-near {cx:g} {target:g} {cz:g} {probe:.2f} -detailed")
    markers = locations.describe(catalogue, locations.live_markers(reply),
                                 cx, cz, half_w, half_d)
    hits = locations.violations(markers, cx, cz, half_w, half_d)
    return hits, locations.report(markers, hits, cx, cz, half_w, half_d)



def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("op", choices=["plan", "apply"])
    ap.add_argument("--world", default="Ulfsland")
    ap.add_argument("--preset")
    ap.add_argument("--placements",
                    help="placements.yaml path; overrides --world/--preset, "
                         "for ordering experiments on a throwaway pad")
    ap.add_argument("--id", action="append", required=True,
                    help="placement id; repeat to flatten several pads in one pass")
    ap.add_argument("--name", help="data-entry prefix; defaults to the first id")
    ap.add_argument("--seed", default="Pirate68")
    ap.add_argument("--paint", default="paved_cleared", choices=sorted(tcdata.PAINTS))
    ap.add_argument("--apron", type=float, default=0.0,
                    help="extra metres of levelled ground outside the costed footprint")
    ap.add_argument("--locations", default=DEFAULT_LOCATION_DUMP,
                    help="per-type location dump from tools/seedscan (default %(default)s)")
    ap.add_argument("--allow-near-location", action="store_true",
                    help="proceed despite a location stand-off violation. Records the "
                         "violation on stdout; there is no silent override")
    args = ap.parse_args()

    if not args.preset and not args.placements:
        ap.error("one of --preset or --placements is required")
    places = [placement(args.world, args.preset, pid, args.placements) for pid in args.id]
    name = args.name or args.id[0]
    zone_set: set[tuple[int, int]] = set()
    spans: list[tuple[dict, list[tuple[int, int]]]] = []
    for place in places:
        solved = place["solved"]
        w, d = pad_extent(place)
        zones = zones_for(float(solved["x"]), float(solved["z"]),
                          w + 2 * args.apron, d + 2 * args.apron)
        spans.append((place, zones))
        zone_set.update(zones)

    out = SCRATCH / f"patch_{name}.bin"
    patches = run_patchscan(sorted(zone_set), args.seed, out)

    comps: dict[tuple[int, int], tcdata.Compiler] = {}
    op_y: dict[tuple[int, int], float] = {}
    for place, zones in spans:
        stats = build(place, patches, zones, args.paint, args.apron, comps, op_y)
        solved = place["solved"]
        print(f"placement {stats['id']} at ({solved['x']}, {solved['z']}) "
              f"pad {stats['pad_w']} x {stats['pad_d']} m target_y {stats['target_y']}")
        print(f"  zones {zones}, {stats['samples']} samples, cut {stats['max_cut']} m "
              f"fill {stats['max_fill']} m, {stats['over_clamp']} beyond the +/-8 m apply clamp")
        for check in stats["checks"]:
            print(f"  check zone {check['zone']} sample {check['sample']} at "
                  f"({check['world'][0]:.1f}, {check['world'][1]:.1f}): generated "
                  f"{check['generated_m']} + delta {check['level_delta_m']} = {check['result_m']}")

    # THE ORDER IS A PRECONDITION, checked before ANYTHING is written -- not
    # even the data-entry file -- because a stale entry in the watched data
    # directory is a loaded gun for the next run.  Every zone about to receive
    # a compiler must already be generated: see `ungenerated_zones`.  Checked
    # rather than trusted, because clearing and flattening are separate
    # commands and nothing else stops them being run the wrong way round.
    if args.op == "apply":
        with Rcon(timeout=60.0) as rc:
            missing = ungenerated_zones(rc, sorted(comps))
            if missing:
                print(f"REFUSED: zones {missing} have never been generated (no {ZONE_CTRL} at "
                      f"their centres), and nothing was written. Writing terrain into an "
                      f"ungenerated zone makes the zone plant its vegetation on the UNMODIFIED "
                      f"generated height, leaving it standing in the air over the finished pad "
                      f"-- MEASURED to 7.68 m at zone (-18, 28) on 2026-09-15. Run "
                      f"clearing/clear.py (zones_generate) for this pad first.")
                return 1
            # Second gate: a terrain write is destructive on its own, and
            # `clearing/area.py`'s stand-off only runs when clearing runs.
            refused = False
            for place, _ in spans:
                hits, text = pad_location_check(rc, place, args.apron, Path(args.locations))
                print(f"  location check {place['id']}:")
                print(text)
                if hits and not args.allow_near_location:
                    refused = True
            if refused:
                print("REFUSED: the pad rectangle reaches a generated location's stand-off, "
                      "and nothing was written. MEASURED on 2026-09-15: a flatten cut 6.65 m "
                      "out from under a LocationProxy holding a TreasureChest_meadows_buried "
                      "without complaining, and marker-based stand-off had already deleted "
                      "POI content in the pre-wipe world because a location's PIECES reach "
                      "past its marker. Re-solve the placement further out, shrink the apron, "
                      "or pass --allow-near-location and say why in the ledger.")
                return 1

    path, entries = write_entries(name, comps, op_y)
    print(f"data entries -> {path}")
    for e in entries:
        print(f"  {e['entry']}: {e['blob_bytes']} B gzip, "
              f"{e['counts']['height_modified']} samples, selfcheck {e['selfcheck']}")

    # A zone holds one compiler, so an existing one is replaced rather than
    # doubled: `deleteObjects -zone` is scoped to exactly the zones being written.
    deletes = [f"deleteObjects -zone {e['zone'][0]} {e['zone'][1]} "
               f"-prefab _TerrainCompiler -force" for e in entries]
    commands = [f"spawn_object _TerrainCompiler from={e['centre'][0]:g},{e['centre'][1]:g},0 "
                f"data={e['entry']}" for e in entries]
    for c in deletes + commands:
        print(f"  {c}")
    if args.op == "plan":
        return 0

    # The data directory is watched (ServerDevcommands.Yaml::SetupWatcher), so the
    # entry is live shortly after the write; the pause is for the watcher, not the disk.
    time.sleep(4)
    with Rcon(timeout=30.0) as rc:
        for c in deletes:
            print("   ", c, "->", rc.command(c).strip().splitlines()[0] if rc else "")
        for c in commands:
            for line in run_console(rc, c, settle=2.0):
                print(f"    {line}")
        print("   ", rc.command("save").strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
