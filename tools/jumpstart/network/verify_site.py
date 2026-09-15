#!/usr/bin/env python3
"""Verify one built site LIVE, without ever issuing an unbounded query.

WHY THE SHAPE OF THIS MATTERS MORE THAN WHAT IT CHECKS. MEASURED by `BulkPlace`
from ValheimRcon 1.6.2's IL: `RconProxy.HandleCommandAsync` calls
`Log.Message(Concat("Command completed: ", command, "\\n", result.Text))` on the
FULL reply text, on the Unity main thread, BEFORE `ValidatePayloadLength`
truncates anything. `InvokeConsoleCommand.OnHandle` returns only
`Concat("Command '", joined, "' executed.")`, so a `consoleCommand` round trip
is bounded by the 4050-byte request cap no matter how large the world is --
but `findObjects` puts its per-ZDO listing IN `result.Text`, so a listing over
~1,900 pieces writes ~190 KB to the container's stdout from the main thread and
freezes the server. Four wedges on this world followed a `findObjects`, three of
them verification queries, one of them the query run to prove the workshop was
FIXED -- which froze the operator in game.

So this module asks only for COUNTS, via `objects_count`, which prints its table
to the SERVER CONSOLE and returns nothing over RCON. It never lists.

WHAT IT CHECKS, and what each check can and cannot answer:

  total count at the body's reach   == the plan's command count plus whatever
                                       was placed separately. Paired with
                                       `place.py`'s own confirmed count this is
                                       the no-stray proof: if exactly N commands
                                       were confirmed and exactly N objects are
                                       inside the building, none of them can be
                                       outside it.
  PER-PREFAB count at reach +
  `--ring` m, against a BASELINE      the delta must be zero. Catches a piece
                                       that landed away from the pad, and a
                                       fragment a removal left behind.

  THE BASELINE IS NOT OPTIONAL FOR THAT SECOND CHECK, and this module learned
  that the hard way in two stages. First it compared TOTALS at two radii, which
  reports the forest as strays: MEASURED at
  `pre-queen/mistlands-blackforge-base` the building is 493 objects and a 73 m
  circle holds 5,605, because `zones_generate` had just minted 5,112 trees and
  spawners. Scoping to the plan's own prefabs fixed that and then failed the
  same way one level down: MEASURED at the same site, `wood_beam` counts 52
  within 13 m and 68 within 30 m, and `iron_wall_1x1` 30 and 51 -- build-piece
  prefabs, so the naive reading is "37 of my pieces flew 30 m". They are not
  mine. `place.py` confirmed exactly 493 commands and exactly 493 objects stand
  inside the building, so the arithmetic closes with nothing left over; the
  cluster is a NEIGHBOUR that predates the build and sits outside the 15.08 m
  clearing radius. Without `--baseline` this module therefore reports the ring
  counts as neighbours and does not fail on them, because a check that cannot
  tell my piece from someone else's must not claim it can.

  GROUNDED FRACTION IS NOT ASKED HERE, deliberately, because answering it live
  means reading every piece's Y -- a listing, i.e. the wedge. It is measured
  OFFLINE instead, and the offline measurement is strictly better: it is the
  1 m column raster in `library/data/base_audit.json`, which reports the
  floating fraction of the body against a PERFECTLY FLAT plane. Paired with the
  flatten step's own per-sample self-check (`generated + delta == target_y`,
  printed by `terraform/flatten.py apply`), "this body grounds at 0.0% on a flat
  pad" plus "this pad is flat to target_y" is a complete answer that costs the
  server nothing.

Usage:
    network/verify_site.py --preset pre-queen --id mistlands-blackforge-base
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
sys.path.insert(0, str(JUMPSTART / "terraform"))
sys.path.insert(0, str(JUMPSTART / "clearing"))

from rcon import Rcon, rcon_credentials, container_ip  # noqa: E402
import clear as clearing  # noqa: E402

AUDIT = JUMPSTART / "library" / "data" / "base_audit.json"
FOOTPRINT_RE = re.compile(r"footprint@yaw[-\d.]+=([\d.]+)x([\d.]+)")


def audit_row(name: str) -> dict:
    for row in json.loads(AUDIT.read_text("utf-8"))["entries"]:
        if row["name"] == name:
            return row
    raise SystemExit(f"{name} is not in {AUDIT}")


def collider_reach(plan_notes: str, fallback_w: float, fallback_d: float) -> tuple[float, str]:
    """Half-diagonal of the body's OCCUPIED box after its yaw, in metres.

    `to_rcon_plan.py --verify` prints `footprint@yaw<N>=<w>x<d>` measured from
    the pieces' own COLLIDER corners after the rotation, which is the only
    number that describes the building's real reach. The manifest's pivot box
    understates it -- MEASURED, `wagon-camp` is 14.3 x 10.0 m by pivot and
    15.05 x 15.73 m by collider, and a clearing radius derived from the pivot
    box misses 0.17 m of the building.
    """
    m = FOOTPRINT_RE.search(plan_notes or "")
    if m:
        return math.hypot(float(m.group(1)) / 2, float(m.group(2)) / 2), "collider box after yaw"
    return math.hypot(fallback_w / 2, fallback_d / 2), "pivot box (collider box unavailable)"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--world", default="Ulfsland")
    ap.add_argument("--preset", required=True)
    ap.add_argument("--id", required=True)
    ap.add_argument("--index", default="/tmp/terraform/plans_wb/index.json",
                    help="sites.py index.json, for the plan's command count and collider box")
    ap.add_argument("--ring", type=float, default=60.0,
                    help="extra metres to re-count at, to catch a detached fragment")
    ap.add_argument("--probe-prefabs", type=int, default=4,
                    help="how many of the plan's most numerous prefabs to count inner vs outer")
    ap.add_argument("--baseline",
                    help="JSON of per-prefab ring counts taken AFTER clearing and BEFORE "
                         "placing; without it the ring counts are reported as neighbours "
                         "and cannot fail the site")
    ap.add_argument("--capture-baseline", action="store_true",
                    help="measure the ring counts and write --baseline, then stop")
    ap.add_argument("--expect-extra", type=int, default=0,
                    help="objects placed at this site beyond the plan (fixtures, portals)")
    args = ap.parse_args()

    path = JUMPSTART / "worlds" / args.world / args.preset / "placements.yaml"
    doc = yaml.safe_load(path.read_text())
    place = next((p for p in doc["placements"] if p["id"] == args.id), None)
    if place is None:
        raise SystemExit(f"no placement {args.id} in {path}")
    solved = place["solved"]
    cx, cz = float(solved["x"]), float(solved["z"])
    pad_y = float((solved.get("flatten_cost") or {})["target_y"])

    index = {f"{r['preset']}#{r['id']}": r for r in json.loads(Path(args.index).read_text())}
    row = index.get(f"{args.preset}#{args.id}")
    if row is None or row["status"] != "ok":
        raise SystemExit(f"{args.preset}#{args.id} has no ok plan in {args.index}")
    fp = place.get("footprint_xzy_m") or [0.0, 0.0, 0.0]
    reach, reach_src = collider_reach(row.get("notes", ""), float(fp[0]), float(fp[1]))
    inner = reach + 2.0
    outer = inner + args.ring
    expected = int(row["commands"]) + args.expect_extra

    print(f"{args.preset}#{args.id}  {place['blueprint']}")
    print(f"  pad ({cx}, {cz}) y {pad_y}; reach {reach:.2f} m from the {reach_src}")

    # The plan file IS the authority on what was sent, so the stray probe is
    # scoped to the prefabs it actually contains rather than to a guess.
    plan_prefabs = Counter()
    for line in Path(row["plan"]).read_text().splitlines():
        m = re.search(r"spawn_object\s+(\S+)", line)
        if m:
            plan_prefabs[m.group(1)] += 1
    probes = [name for name, _ in plan_prefabs.most_common(args.probe_prefabs)]

    a = audit_row(place["blueprint"])
    port, password = rcon_credentials()
    base = json.loads(Path(args.baseline).read_text()) if (
        args.baseline and not args.capture_baseline and Path(args.baseline).exists()) else None

    if args.capture_baseline:
        if not args.baseline:
            raise SystemExit("--capture-baseline needs --baseline to write to")
        with Rcon(container_ip(), port, password) as rc:
            snap = {name: clearing.box_query(rc, cx, pad_y, cz, outer, ident=name)[0]
                    for name in probes}
        Path(args.baseline).write_text(json.dumps(snap, indent=1))
        print(f"  baseline at {outer:.1f} m before placing: {snap}")
        print(f"  written -> {args.baseline}")
        return 0

    strays: list[str] = []
    with Rcon(container_ip(), port, password) as rc:
        n_inner, prefabs_inner = clearing.box_query(rc, cx, pad_y, cz, inner)
        for name in probes:
            got_in, _ = clearing.box_query(rc, cx, pad_y, cz, inner, ident=name)
            got_out, _ = clearing.box_query(rc, cx, pad_y, cz, outer, ident=name)
            want = plan_prefabs[name]
            if base is None:
                note = f"neighbours {got_out - got_in}" if got_out != got_in else "no neighbours"
                verdict = "" if got_in == want else "  <-- MISMATCH inside"
            else:
                outside = got_out - got_in - int(base.get(name, 0))
                note = f"strays {outside}"
                verdict = "" if (got_in == want and outside == 0) else "  <-- MISMATCH"
                if outside:
                    strays.append(f"{name}: {outside} standing OUTSIDE the building "
                                  f"(ring {got_out - got_in}, baseline {base.get(name, 0)})")
            print(f"  {name:<28} plan {want:>5}  within {inner:.0f} m {got_in:>5}  "
                  f"within {outer:.0f} m {got_out:>5}  {note}{verdict}")
            if got_in != want:
                strays.append(f"{name}: {got_in} in the building, plan sent {want}")

    print(f"  objects within {inner:.1f} m: {n_inner}   expected {expected} "
          f"({row['commands']} plan + {args.expect_extra} placed separately)")
    print(f"  distinct prefabs in the building: {len(set(prefabs_inner))}")
    print(f"  grounded fraction, MEASURED OFFLINE on a flat pad "
          f"(library/audit_bases.py, 1 m columns): "
          f"{100 * (1 - a['floating_fraction']):.1f}% grounded, "
          f"{100 * a['floating_fraction']:.1f}% clear, worst air gap {a['max_air_m']:.2f} m, "
          f"isolation {a['air_isolation_max_m']:.1f} m, verdict {a['flat_pad_verdict']}")

    problems = list(strays)
    if n_inner != expected:
        problems.append(f"{n_inner} objects present within {inner:.1f} m, {expected} expected")
    if problems:
        for p in problems:
            print(f"  FAIL: {p}", file=sys.stderr)
        return 1
    print("  PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
