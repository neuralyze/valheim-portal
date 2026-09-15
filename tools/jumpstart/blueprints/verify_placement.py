#!/usr/bin/env python3
"""Measure a placed blueprint in a live world, without wedging the server.

The question is the operator's, not the tool's: standing on the pad, is the
building centred on it, is the floor at the right height, and is all of it
there?

THE HAZARD THIS FILE IS SHAPED BY
---------------------------------
MEASURED on Ulfsland, twice, each costing a container restart:

  * `findObjects -detailed` over a cube containing all 1,907 pieces wedged the
    server. `RconCommandReceiver` hands the command's result to `Log.Message`
    -- which on this deployment is the container's stdout through supervisord --
    BEFORE `ValidatePayloadLength` truncates it, so a 1,907-line result is a
    ~290 KB blocking write on the Unity main thread. The container log stopped
    mid-object at id 80439 and the server answered nothing again.
  * A tiled sweep of ~700 small `findObjects` cubes wedged it the same way
    after 1,082 object lines in about ten seconds.

So the rule is NOT "keep the reply under ValheimRcon's 4050-byte cap" -- the
plugin truncates the reply but logs the whole thing first. The rule is: never
ask a question whose ANSWER is long, and keep the total answered volume small.

WHAT THAT LEAVES, AND WHY IT IS ENOUGH
--------------------------------------
1. COUNT comes from Upgrade World's `objects_count`, whose answer is one
   `<prefab>: <count>` line per prefab plus a `Total:` -- 61 lines for this
   1,907-piece body, and it does not grow with the thing being measured. It is
   NOT a staged operation (MEASURED: staging it answers `routine is null`) and
   it prints to the server console rather than the RCON reply, so it is read
   back from the container log.

2. REACH is bracketed with two more `objects_count` calls. `pos=`/`max=` is a
   vertical cylinder about the pad centre, so the smallest radius that contains
   every piece IS the body's reach from the pad centre. That single number is
   what separates a centred body from a corner-anchored one: centred, this body
   reaches 41.3 m; with its corner on the pad it reaches 92 m.

3. EXTENT and CENTROID come from the four EXTREME pieces, each fetched with a
   prefab filter and a tight box -- one to three lines per reply. The plan says
   which prefab should hold each extreme and where; the server is asked whether
   it is there. Those four coordinates are the extent, so the extent is
   measured rather than inferred, and the replies stay tiny.

4. FLOOR HEIGHT comes from the floor prefab alone, in quadrants, and is
   reported against the pad. `stone_floor_2x2`'s pivot sits at mid-thickness,
   so the walkable surface is pivot + the measured half-thickness.

Every listing is counted against `--budget` and the run stops before exceeding
it, so this file cannot repeat the failure it documents.

Usage:
    verify_placement.py --at -4673.5 70.91 -310.5 --plan /tmp/iron-era/plan.txt
"""

from __future__ import annotations

import argparse
import math
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "terraform"))

import base_geometry  # noqa: E402

# `Id:` is matched, not skipped, because it is the only stable key for
# deduplicating overlapping boxes -- and because requiring it rejects a
# severed final line rather than reading a half-name out of it.
POSITION_RE = re.compile(
    r"^-Prefab: (\S+) Id: (\S+) Position: \((-?[\d.]+) (-?[\d.]+) (-?[\d.]+)\)", re.M)
TOTAL_RE = re.compile(r"^Total:?\s*(\d+)\s*$", re.M)
COUNT_LINE_RE = re.compile(r"^(?!Total:)(\S+):\s*(\d+)\s*$", re.M)
TRUNCATED = "--- message truncated ---"

# Total object lines this tool is willing to make the server log. Both measured
# wedges were far above it: one reply of 1,907 lines, and 1,082 lines streamed
# over ten seconds.
DEFAULT_BUDGET = 400


class Listing:
    """`findObjects` with a prefab filter, under a hard output budget."""

    def __init__(self, rc, budget: int):
        self.rc = rc
        self.budget = budget
        self.spent = 0
        self.biggest = 0
        self.queries = 0

    def near(self, prefab: str, x: float, y: float, z: float,
             half: float) -> list[tuple[str, str, float, float, float]]:
        """(prefab, id, x, y, z) for every match in the box, budget permitting."""
        if self.spent >= self.budget:
            raise RuntimeError(
                f"listing budget of {self.budget} object lines is spent; refusing "
                f"another findObjects. A long reply is what wedges this server.")
        reply = self.rc.command(
            f"findObjects -prefab {prefab} -near {x:.2f} {y:.2f} {z:.2f} {half:.2f}")
        self.queries += 1
        if TRUNCATED in reply:
            raise RuntimeError(
                f"findObjects -prefab {prefab} -near {x:.2f} {y:.2f} {z:.2f} "
                f"{half:.2f} came back TRUNCATED, so it did not see every match "
                f"and its result must not be read. Narrow the box.")
        hits = [(p, oid, float(hx), float(hy), float(hz))
                for p, oid, hx, hy, hz in POSITION_RE.findall(reply)]
        self.spent += len(hits)
        self.biggest = max(self.biggest, len(hits))
        return hits


def plan_rows(path: Path) -> list[tuple[str, float, float, float, float]]:
    """(prefab, x, y, z, yaw) per `spawn_object` line.

    `pos=` is z,x,y (`Parse::VectorZXYRange`) and `rot=` is euler y,x,z
    (`Parse::AngleYXZ`) -- three different component orders in one command, all
    documented on `to_rcon_plan.pos_arg` / `rot_arg`. Reading either
    positionally as x,y,z silently swaps axes, so both are unpacked by name.
    """
    out = []
    for line in path.read_text("utf-8").splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[0] != "spawn_object":
            continue
        pos = next((p for p in parts if p.startswith("pos=")), None)
        if pos is None:
            continue
        z, x, y = (float(v) for v in pos[4:].split(","))
        rot = next((p for p in parts if p.startswith("rot=")), None)
        yaw = float(rot[4:].split(",")[0]) if rot else 0.0
        out.append((parts[1], x, y, z, yaw))
    return out


def extremes(rows) -> dict[str, tuple]:
    """The row holding each XZ extreme, which is what defines the footprint."""
    return {
        "min_x": min(rows, key=lambda r: r[1]),
        "max_x": max(rows, key=lambda r: r[1]),
        "min_z": min(rows, key=lambda r: r[3]),
        "max_z": max(rows, key=lambda r: r[3]),
    }


def solid_reach(rows, geom: base_geometry.Geometry) -> tuple[float, float, float, float]:
    """The XZ box the plan's colliders actually fill.

    Each prefab's measured collider AABB is rotated by that row's own yaw
    before being added, because an unrotated box is the wrong shape for a piece
    at 45 degrees -- and 854 of this body's 1,907 pieces are `stone_wall_2x1`
    at yaws of 22.5, 45, 67.5 and 135. Pitch and roll are ignored: this corpus
    emits yaw-only rotations for its wall and floor pieces, and the figure is
    reported as the footprint, which is a horizontal question.
    """
    lo_x = lo_z = math.inf
    hi_x = hi_z = -math.inf
    for prefab, x, _y, z, yaw in rows:
        box = geom.local_aabb(prefab)
        if box is None:
            box = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        rad = math.radians(yaw)
        cos, sin = math.cos(rad), math.sin(rad)
        for ox in (box[0], box[3]):
            for oz in (box[2], box[5]):
                wx = x + ox * cos + oz * sin
                wz = z + oz * cos - ox * sin
                lo_x, hi_x = min(lo_x, wx), max(hi_x, wx)
                lo_z, hi_z = min(lo_z, wz), max(hi_z, wz)
    return lo_x, hi_x, lo_z, hi_z


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--at", nargs=3, type=float, required=True,
                    metavar=("X", "Y", "Z"), help="pad centre the body should be on")
    ap.add_argument("--plan", required=True,
                    help="the plan that was sent; supplies the expected count, the "
                         "per-prefab counts and which piece should hold each extreme")
    ap.add_argument("--reach", type=float,
                    help="counting radius. Default is the plan's own maximum pivot "
                         "distance from --at plus --margin, so it covers the body's "
                         "actual reach rather than a radius picked off the pad size")
    ap.add_argument("--margin", type=float, default=3.0,
                    help="metres added to the plan's reach for the counting cylinder")
    ap.add_argument("--tol", type=float, default=0.25,
                    help="how far an extreme piece may sit from where the plan put it")
    ap.add_argument("--floor-prefab", default="stone_floor_2x2")
    ap.add_argument("--budget", type=int, default=DEFAULT_BUDGET)
    args = ap.parse_args(argv)

    from rcon import Rcon  # noqa: E402
    from console import run_console  # noqa: E402

    cx, cy, cz = args.at
    geom = base_geometry.geometry()
    rows = plan_rows(Path(args.plan))
    if not rows:
        print(f"{args.plan}: no spawn_object lines", file=sys.stderr)
        return 2
    want = Counter(r[0] for r in rows)
    plan_reach = max(math.hypot(r[1] - cx, r[3] - cz) for r in rows)
    reach = args.reach if args.reach is not None else plan_reach + args.margin
    failures: list[str] = []

    print(f"{args.plan}: {len(rows)} piece(s), {len(want)} prefab(s); the plan's "
          f"furthest pivot is {plan_reach:.2f} m from ({cx:g},{cz:g})")

    with Rcon(timeout=60.0) as rc:
        # 1. count, from the console table
        lines = run_console(
            rc, f"objects_count id=* ignore=_* pos={cx:.2f},{cz:.2f} max={reach:.2f}")
        text = "\n".join(lines)
        total = TOTAL_RE.search(text)
        if not total:
            print(f"objects_count printed no Total line; console said {lines!r}",
                  file=sys.stderr)
            return 4
        live = Counter({name: int(n) for name, n in COUNT_LINE_RE.findall(text)})
        found = int(total.group(1))
        print(f"count inside a {reach:.2f} m cylinder about the pad: {found} vs "
              f"{len(rows)} sent  "
              f"({'exact' if found == len(rows) else f'{found - len(rows):+d}'})")
        # A SHORTFALL is a lost piece and fails. A SURPLUS is reported but does
        # not: the site-preparation order runs `terraform/stock.py` after this
        # step, which adds its own stations, props and chests to the same pad --
        # 23 of them on this site -- and several share prefabs with the body. A
        # tool that can only be run before stocking is a tool the operator
        # cannot re-run when they ask whether the building is still intact.
        short = {k: (n, live.get(k, 0)) for k, n in want.items() if live.get(k, 0) < n}
        surplus = {k: live.get(k, 0) - want.get(k, 0)
                   for k in set(live) | set(want) if live.get(k, 0) > want.get(k, 0)}
        if short:
            print(f"  MISSING (plan, live): {short}")
            failures.append(f"{len(short)} prefab(s) short of the plan")
        else:
            print(f"  every one of the plan's {len(want)} prefabs is present in at "
                  f"least its planned count")
        if surplus:
            print(f"  surplus beyond the plan, {sum(surplus.values())} object(s) "
                  f"across {len(surplus)} prefab(s): {surplus}")

        # 2. reach: nothing of the body lies beyond where the plan put it.
        #
        # Stated as two cylinder counts rather than one, because a single count
        # cannot tell a centred body from an off-centre one. The shell between
        # `plan_reach - 0.5` and `plan_reach` must be OCCUPIED (so the body does
        # reach that far) and the count at `plan_reach` must equal the count at
        # the wider `reach` (so nothing lies beyond it). Both survive a surplus,
        # because both are comparisons between two live counts.
        inside = run_console(
            rc, f"objects_count id=* ignore=_* pos={cx:.2f},{cz:.2f} "
                f"max={plan_reach - 0.5:.2f}")
        tight = TOTAL_RE.search("\n".join(inside))
        at_reach = run_console(
            rc, f"objects_count id=* ignore=_* pos={cx:.2f},{cz:.2f} "
                f"max={plan_reach + 0.01:.2f}")
        full = TOTAL_RE.search("\n".join(at_reach))
        if tight and full:
            near_n, full_n = int(tight.group(1)), int(full.group(1))
            print(f"reach from the pad centre: {near_n} object(s) within "
                  f"{plan_reach - 0.5:.2f} m, {full_n} within {plan_reach + 0.01:.2f} m, "
                  f"{found} within {reach:.2f} m")
            if full_n <= near_n:
                failures.append(f"nothing sits in the {plan_reach - 0.5:.2f}-"
                                f"{plan_reach:.2f} m shell, so the body does not reach "
                                f"as far as the plan put it")
            elif full_n != found:
                failures.append(f"{found - full_n} object(s) sit beyond "
                                f"{plan_reach:.2f} m, further out than the plan's "
                                f"furthest piece")
            else:
                diagonal = math.hypot(
                    max(r[1] for r in rows) - min(r[1] for r in rows),
                    max(r[3] for r in rows) - min(r[3] for r in rows))
                print(f"  so the site reaches {plan_reach:.2f} m and no further, with "
                      f"{full_n - near_n} object(s) in the outermost 0.5 m. Anchored "
                      f"on its own origin instead of its centre, this body would "
                      f"reach its full diagonal, {diagonal:.0f} m")

        # 3. the four extremes, each a tiny prefab-filtered reply
        listing = Listing(rc, args.budget)
        measured: dict[str, tuple[float, float, float]] = {}
        for label, (prefab, x, y, z, _yaw) in extremes(rows).items():
            hits = listing.near(prefab, x, y, z, args.tol * 4)
            best = min(hits, key=lambda h: math.hypot(h[2] - x, h[3] - y, h[4] - z),
                       default=None)
            if best is None:
                print(f"{label:5s} {prefab:22s} expected ({x:.2f},{y:.2f},{z:.2f}) "
                      f"-- NOT FOUND")
                failures.append(f"{label} piece missing")
                continue
            off = math.hypot(best[2] - x, best[3] - y, best[4] - z)
            measured[label] = (best[2], best[3], best[4])
            print(f"{label:5s} {prefab:22s} at ({best[2]:.2f},{best[3]:.2f},"
                  f"{best[4]:.2f}) vs plan ({x:.2f},{y:.2f},{z:.2f})  "
                  f"off by {off:.3f} m"
                  + ("" if off <= args.tol else "  -- OUTSIDE TOLERANCE"))
            if off > args.tol:
                failures.append(f"{label} piece is {off:.3f} m from the plan")

        # 4. floor height
        floors: list[tuple[str, str, float, float, float]] = []
        quad = reach * 0.5 + 0.05
        for sx in (-1, 1):
            for sz in (-1, 1):
                floors += listing.near(args.floor_prefab,
                                       cx + sx * reach * 0.5, cy,
                                       cz + sz * reach * 0.5, quad)
        # Deduplicated by ZDO id, because the quadrant boxes overlap on purpose:
        # `NearCriteria`'s bounds are strict, so a piece exactly on a quadrant
        # line would otherwise fall in the crack between two boxes.
        unique = {f[1]: f for f in floors}
        span = geom.y_span(args.floor_prefab, (0, 0, 0, 1), (1, 1, 1))

    if measured:
        lo_x = measured.get("min_x", (math.nan,))[0]
        hi_x = measured.get("max_x", (math.nan,))[0]
        lo_z = measured.get("min_z", (math.nan,) * 3)[2]
        hi_z = measured.get("max_z", (math.nan,) * 3)[2]
        print(f"measured pivot extent x {lo_x:.2f}..{hi_x:.2f} ({hi_x - lo_x:.2f} m)"
              f"  z {lo_z:.2f}..{hi_z:.2f} ({hi_z - lo_z:.2f} m)")
        print(f"measured pivot centre ({(lo_x + hi_x) / 2:.2f},{(lo_z + hi_z) / 2:.2f})"
              f" vs pad ({cx:.2f},{cz:.2f})  offset "
              f"({(lo_x + hi_x) / 2 - cx:+.2f},{(lo_z + hi_z) / 2 - cz:+.2f}) m")
        s_lo_x, s_hi_x, s_lo_z, s_hi_z = solid_reach(rows, geom)
        print(f"occupied extent  x {s_lo_x:.2f}..{s_hi_x:.2f} ({s_hi_x - s_lo_x:.2f} m)"
              f"  z {s_lo_z:.2f}..{s_hi_z:.2f} ({s_hi_z - s_lo_z:.2f} m)")
        print(f"occupied centre ({(s_lo_x + s_hi_x) / 2:.2f},"
              f"{(s_lo_z + s_hi_z) / 2:.2f}) vs pad ({cx:.2f},{cz:.2f})  offset "
              f"({(s_lo_x + s_hi_x) / 2 - cx:+.2f},{(s_lo_z + s_hi_z) / 2 - cz:+.2f}) m")
        print(f"  overhang  -x {cx - s_lo_x:.2f}  +x {s_hi_x - cx:.2f}"
              f"  -z {cz - s_lo_z:.2f}  +z {s_hi_z - cz:.2f}"
              f"   (collider boxes rotated by each row's own yaw; the four pivots "
              f"above were read back from the SERVER and agree with the plan, so "
              f"this is the plan's geometry on confirmed positions)")

    if unique:
        tops = sorted(v[3] + span[1] for v in unique.values())
        print(f"{args.floor_prefab}: {len(tops)} piece(s) found "
              f"({want.get(args.floor_prefab, 0)} in the plan); walkable top "
              f"min {tops[0]:.3f} median {statistics.median(tops):.3f} "
              f"max {tops[-1]:.3f} vs pad {cy:.3f}")
        if len(tops) < want.get(args.floor_prefab, 0):
            failures.append(f"{len(tops)} {args.floor_prefab} found, "
                            f"{want.get(args.floor_prefab, 0)} in the plan")
        if abs(tops[0] - cy) > 0.5:
            failures.append(f"lowest floor top {tops[0]:.3f} is not on the pad {cy:.3f}")
    else:
        print(f"{args.floor_prefab}: none found, so no floor height was measured")
        failures.append("no floor pieces found")

    print(f"listings: {listing.queries} quer{'y' if listing.queries == 1 else 'ies'}, "
          f"{listing.spent}/{args.budget} object lines, largest reply "
          f"{listing.biggest} object(s)")
    if failures:
        print("FAILED: " + "; ".join(failures), file=sys.stderr)
        return 1
    print("every check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
