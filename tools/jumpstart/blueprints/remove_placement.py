#!/usr/bin/env python3
"""Take a placed blueprint back off a site, prefab-scoped and rate-bounded.

WHY A TOOL AND NOT A COMMAND
----------------------------
Removing a 1,900-piece body is the operation that has wedged this server three
times in one night, and each of the three had the same shape: a question whose
ANSWER was long.

  * MEASURED: `deleteObjects` echoes ONE LINE PER DELETED OBJECT --
    `-Prefab: charcoal_kiln Id: 52240:1 Position: (...) [DELETED]`, 155 bytes.
    So one command that deletes 1,907 pieces is a 290 KB reply, and ValheimRcon
    1.6.2 hands the FULL reply to `Log.Message` -- the container's stdout
    through supervisord -- BEFORE `ValidatePayloadLength` truncates it. That
    write blocks Unity's main thread.
  * MEASURED: the rate matters as much as the size. A tiled sweep of small
    `findObjects` cubes wedged the server after 1,082 object lines in about ten
    seconds, i.e. ~108 lines/s. So "keep each reply small" is necessary and not
    sufficient.

This tool therefore deletes in SLICES of at most `--slice` objects, paces them
so the sustained echo stays under `--lines-per-second`, and re-checks that the
container log is still draining as it goes. If the log stops advancing it STOPS,
because that is the first observable symptom of the wedge and the point at which
every later command is lost.

WHY PREFAB-SCOPED, AND WHY THE RADIUS IS NOT THE PAD
----------------------------------------------------
MEASURED on `pre-bonemass/iron-era-workshop`: removals bounded to 51.5 m, then
60 m, then 70 m of the PAD CENTRE each left the far corners of a
CORNER-ANCHORED copy standing, because that convention puts the body's origin on
the pad and its far corner 93 m away. Three removals, three times the same
error, and each was then "verified" with a radius that could not see the
leftovers either. So the reach is computed from the body's own footprint under
BOTH anchor conventions.

And it is prefab-scoped rather than `id=*`, because at that radius a site is not
alone: there are mod-added world locations within 30 m of this pad and generated
content beyond. Deleting half of somebody else's location is worse than leaving
a detached kiln standing. The authority for what is OURS is the blueprint's own
prefab list plus, with `--stock`, the preset's.

Usage:
    remove_placement.py --world Ulfsland --preset pre-bonemass \
        --id iron-era-workshop --plan
    remove_placement.py --world Ulfsland --preset pre-bonemass \
        --id iron-era-workshop --apply --stock
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
import time
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "terraform"))

import base_geometry  # noqa: E402
import fixtures  # noqa: E402
import to_rcon_plan  # noqa: E402
import survey_datum  # noqa: E402

JUMPSTART = HERE.parent
DELETED_RE = re.compile(r"Deleted (\d+)/(\d+) objects")
OBJECT_LINE_RE = re.compile(r"^-Prefab: ", re.M)
CONTAINER = "valheim-server-Ulfsland"


def log_tip() -> str:
    """The container log's last timestamp. The sink's liveness is a
    PRECONDITION for any command whose reply gets logged, and it is observable:
    when the log stops advancing while the server still answers, the next long
    reply is the one that never comes back."""
    out = subprocess.run(["sudo", "-n", "docker", "logs", "-t", "--tail", "1",
                          CONTAINER], capture_output=True, text=True)
    line = (out.stdout + out.stderr).strip().splitlines()
    return line[-1].split(" ", 1)[0] if line else ""


def placement(world: str, preset: str, pid: str) -> dict:
    doc = yaml.safe_load(
        (JUMPSTART / "worlds" / world / preset / "placements.yaml").read_text())
    for p in doc["placements"]:
        if p["id"] == pid:
            return p
    raise SystemExit(f"no placement {pid}")


def body_path(name: str) -> Path:
    for root in survey_datum.CORPUS_ROOTS:
        direct = root / name
        hits = [direct] if direct.is_file() else (
            sorted(root.rglob(name)) if root.is_dir() else [])
        for hit in hits:
            if hit.stat().st_size:
                return hit
    raise SystemExit(f"blueprint {name} is in no corpus root")


def expected(objs, place: dict, cx: float, cz: float, pad_y: float, yaw: float,
             base_ys: list[float]) -> list[tuple[str, float, float, float]]:
    """Every world position this body could be occupying, over both anchor
    conventions and every datum it may have been placed with.

    Both conventions, because a site may hold the leftovers of an older run:
    MEASURED, three pieces of a corner-anchored copy of this body were still
    standing 72-80 m from the pad centre after three removals aimed at the pad.
    """
    g = base_geometry.geometry()
    foot = base_geometry.xz_footprint(objs, yaw, g)
    out = []
    for base_y in base_ys:
        place_y = pad_y - base_y
        for dx, dz in ((-foot.center_x, -foot.center_z), (0.0, 0.0)):
            half = math.radians(yaw) * 0.5
            yq = (0.0, math.sin(half), 0.0, math.cos(half))
            for o in objs:
                px, py, pz = base_geometry._qrot(yq, o.pos)
                out.append((o.prefab, px + dx + cx, py + place_y, pz + dz + cz))
    return out


def slices(points: list[tuple[str, float, float, float]], cube_m: float,
           margin: float) -> list[dict]:
    """One delete command per (prefab, `cube_m` cell) that holds anything.

    A fixed small CUBE, not a chunk of N pieces, and the difference is the whole
    point: `deleteObjects -near` matches everything of that prefab inside the
    cube, so what has to be bounded is the cube's VOLUME, not the number of
    pieces I meant to catch. A 20-piece chunk of `stone_wall_2x1` sorted by x
    spans this body's whole 46 m of copy separation, and the cube that contains
    it would match a couple of hundred walls and echo a couple of hundred lines.
    A 4 m cube of 2 x 1 x 0.5 m walls cannot hold more than about 16.
    """
    cells: dict[tuple[str, int, int, int], int] = {}
    for prefab, x, y, z in points:
        key = (prefab, math.floor(x / cube_m), math.floor(y / cube_m),
               math.floor(z / cube_m))
        cells[key] = cells.get(key, 0) + 1
    out = []
    for (prefab, ix, iy, iz), n in sorted(cells.items()):
        out.append({
            "prefab": prefab,
            "x": round((ix + 0.5) * cube_m, 2),
            "y": round((iy + 0.5) * cube_m, 2),
            "z": round((iz + 0.5) * cube_m, 2),
            "half": round(cube_m / 2 + margin, 2),
            "expected": n,
        })
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--world", default="Ulfsland")
    ap.add_argument("--preset", required=True)
    ap.add_argument("--id", required=True)
    ap.add_argument("--base-y", type=float, action="append", default=[],
                    help="a datum the body may have been placed with; repeatable. "
                         "Defaults to the measured one plus any override.")
    ap.add_argument("--stock", action="store_true",
                    help="also remove what terraform/stock.py places: the preset's "
                         "stations and extensions, the props and the chest bank")
    ap.add_argument("--cube", type=float, default=4.0,
                    help="edge of the cube one delete command covers; small "
                         "because the command matches everything of that prefab "
                         "inside it, and a long reply is what wedges the server")
    ap.add_argument("--lines-per-second", type=float, default=20.0,
                    help="sustained echo budget; the measured wedge was ~108/s")
    ap.add_argument("--margin", type=float, default=0.6,
                    help="metres added to each slice cube")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--report")
    args = ap.parse_args(argv)

    place = placement(args.world, args.preset, args.id)
    solved = place["solved"]
    cost = solved.get("flatten_cost") or {}
    cx, cz = float(solved["x"]), float(solved["z"])
    pad_y = float(cost.get("target_y") or solved["y"])
    yaw = float((place.get("rotation") or {}).get("yaw") or 0.0)
    objs = to_rcon_plan.read_objects(body_path(place["blueprint"]))
    datum = base_geometry.floor_datum(objs)
    base_ys = list(args.base_y)
    if not base_ys:
        recorded = (place.get("blueprint_datum") or {})
        for candidate in (datum.base_y, recorded.get("base_y"),
                          recorded.get("override_base_y")):
            if candidate is not None and float(candidate) not in base_ys:
                base_ys.append(float(candidate))
    points = expected(objs, place, cx, cz, pad_y, yaw, base_ys)

    plan = slices(points, args.cube, args.margin)

    if args.stock:
        # stock.py's own set, at BOTH layouts it can have used.
        #
        # The legacy layout is the hardcoded pad offsets -- the station row at
        # (-10 + 5i, -10), the props at their own offsets, the chest rank 12 m
        # out -- which is what is in the world now and what put a `portal_wood`
        # inside a `stone_wall_4x2`. The current layout is whatever
        # `stock.solve_placement` searches out, which is deterministic for a
        # given body and pad, so it can be recomputed rather than guessed.
        #
        # Both are needed, and a prefab the blueprint CARRIES is needed here too:
        # stock placed its own second copy of all nine of this preset's stations
        # at pad level, and those copies are nowhere near where the body puts
        # them, so the body slices cannot reach them.
        import stock  # noqa: E402  (terraform/)

        preset_doc = stock.load_preset(args.preset)
        pad_half = float(
            (place.get("requirement") or {}).get("footprint_m") or 70.0) / 2.0
        stock_points: list[tuple[str, float, float, float]] = []

        # -- the legacy hardcoded layout, reproduced exactly
        for i, station in enumerate(preset_doc.get("stations") or []):
            dx, dz = -10.0 + i * 5.0, -10.0
            sx, sz = stock.rotate(dx, dz, yaw)
            stock_points.append((station["prefab"], cx + sx, pad_y, cz + sz))
            exts = station.get("extensions") or []
            for k in range(max(0, int(station.get("level") or 1) - 1)):
                if not exts:
                    break
                ex, ez = stock.rotate(dx + 2.0 * (1 if k % 2 == 0 else -1),
                                      dz + (1.5 if k > 1 else 0.0), yaw)
                stock_points.append((exts[k % len(exts)], cx + ex, pad_y, cz + ez))
        for prefab, (dx, dz), _extra in stock.PROPS:
            px, pz = stock.rotate(dx, dz, yaw)
            stock_points.append((prefab, cx + px, pad_y, cz + pz))
        # the chest rank, for any bank size the manifest could have produced
        for n_chests in range(2, 33):
            for i in range(n_chests):
                px, pz = stock.rotate(-((n_chests - 1) * 0.8) + i * 1.6, 12.0, yaw)
                stock_points.append((stock.CHEST_PREFAB, cx + px, pad_y, cz + pz))

        # -- the current searched layout
        try:
            items = dict((stock.load_manifest(args.world, args.preset)
                          .get("items") or {}))
            est = sum(max(1, -(-int(c) // 50)) for c in items.values())
            solved_plan = stock.solve_placement(
                place, preset_doc, max(2, -(-est // 18)), cx, cz, pad_y, yaw,
                pad_half)
            for row in solved_plan["stations"]:
                for entry in ([row] if row.get("place") else []) + row["extensions"]:
                    p = entry.get("place")
                    if p:
                        stock_points.append((p["prefab"], p["x"], p["y"], p["z"]))
            for row in solved_plan["props"] + solved_plan["chests"]:
                p = row.get("place")
                if p:
                    stock_points.append((p["prefab"], p["x"], p["y"], p["z"]))
        except SystemExit:
            # No manifest for this preset: the legacy sweep still stands.
            pass

        plan += slices(stock_points, args.cube, args.margin)

    lines = sum(s["expected"] or 0 for s in plan)
    report = {
        "site": args.id, "centre": [cx, cz], "pad_y": pad_y, "yaw": yaw,
        "base_ys_covered": base_ys,
        "body_pieces": len(objs),
        "reach_m": round(max(math.hypot(p[1] - cx, p[3] - cz) for p in points), 1),
        "commands": len(plan),
        "echo_lines_upper_bound": lines,
        "slices": plan,
    }
    print(f"{args.id}: {len(objs)} body pieces over {len(base_ys)} datum(s) and both "
          f"anchor conventions -> {len(plan)} delete command(s), reach "
          f"{report['reach_m']} m, at most {lines} echoed object line(s)")
    if not args.apply:
        if args.report:
            Path(args.report).write_text(json.dumps(report, indent=1))
        return 0

    from rcon import Rcon  # noqa: E402

    tip = log_tip()
    if not tip:
        raise SystemExit("cannot read the container log, so the sink's liveness "
                         "cannot be established; refusing to issue deletes")
    deleted = 0
    echoed = 0
    stalled = None
    began = time.time()
    with Rcon(timeout=60.0) as rc:
        for n, s in enumerate(plan, 1):
            reply = rc.command(
                f"deleteObjects -prefab {s['prefab']} -near {s['x']:.2f} "
                f"{s['y']:.2f} {s['z']:.2f} {s['half']:.2f} -force")
            found = DELETED_RE.search(reply)
            got = int(found.group(1)) if found else 0
            s["deleted"] = got
            deleted += got
            # The budget is spent in LINES, because the wedge is a function of
            # how much text the server logs per second and not of how many
            # commands it answers. A command that deletes nothing costs nothing
            # and is not paced for.
            echoed += len(OBJECT_LINE_RE.findall(reply))
            owed = echoed / args.lines_per_second - (time.time() - began)
            if owed > 0:
                time.sleep(min(owed, 5.0))
            if n % 25 == 0 or n == len(plan):
                fresh = log_tip()
                print(f"  {n}/{len(plan)} commands, {deleted} deleted, "
                      f"{echoed} echoed line(s), {time.time() - began:.0f}s, "
                      f"{echoed / max(1e-9, time.time() - began):.1f} line/s, "
                      f"log tip {fresh}", flush=True)
                if fresh == tip:
                    stalled = n
                    print("  the container log has STOPPED advancing -- this is the "
                          "wedge's first symptom. Stopping.", file=sys.stderr)
                    break
                tip = fresh
        rc.command("save")
    report["deleted"] = deleted
    report["echoed_lines"] = echoed
    report["stalled_at_command"] = stalled
    report["seconds"] = round(time.time() - began, 1)
    print(f"deleted {deleted} object(s) in {report['seconds']}s over "
          f"{len(plan)} command(s)")
    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=1))
    return 1 if stalled else 0


if __name__ == "__main__":
    raise SystemExit(main())
