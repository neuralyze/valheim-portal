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
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "terraform"))

import base_geometry  # noqa: E402
import to_rcon_plan  # noqa: E402  (for euler_to_quat, the plan's own encoder)
from rcon import CONTAINER  # noqa: E402  (terraform/, for the log-sink check)

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

# Sustained object-line rate this tool holds itself to, matching the figure
# `remove_placement.py` ran 1,916 deletions at without the log faltering.
#
# It exists because a TOTAL budget is not sufficient, MEASURED tonight: 21
# prefab-filtered `findObjects` boxes are answered in about 0.7 s, so a run
# well inside the 400-line budget still streams at ~700 lines/s -- six times
# the ~108 lines/s that wedged this server. What broke was the game process's
# STDOUT: Valheim's own console printing (and with it every `objects_count`
# table) stopped, and the docker json log froze, while BepInEx's file logger
# and the Unity main thread carried on -- `save` still ran and still wrote the
# world. So the failure is not "the server hangs"; it is "the server goes
# BLIND", which is worse, because a verification tool then reports nothing
# while the game looks healthy. Only a container restart clears it.
DEFAULT_LINES_PER_SECOND = 20.0

# Metres added to each floor-sweep box so that adjacent boxes OVERLAP.
# `NearCriteria`'s bounds are strict, so a piece sitting exactly on a cell line
# would otherwise fall in the crack between two boxes and be reported missing.
# Kept well under the 2 m pivot pitch of the slab prefabs this sweeps, so the
# overlap cannot reach a neighbouring pivot.
MARGIN_M = 0.25


def log_tip() -> str:
    """The container log's last timestamp.

    The sink's liveness is a PRECONDITION for every measurement this file makes,
    and it is observable. MEASURED tonight: one over-long `findObjects` reply
    killed the game process's stdout -- Valheim's console printing stopped, so
    `objects_count` answered with nothing at all, and the docker log froze at
    the last line of that reply -- while BepInEx's file logger and the Unity
    main thread carried on, `save` still ran and the world still wrote. A tool
    that keeps asking after that point reports a partial measurement as a
    finding, which is the one thing this file must never do.
    """
    out = subprocess.run(["sudo", "-n", "docker", "logs", "-t", "--tail", "1",
                          CONTAINER], capture_output=True, text=True)
    line = (out.stdout + out.stderr).strip().splitlines()
    return line[-1].split(" ", 1)[0] if line else ""


class Listing:
    """`findObjects` with a prefab filter, under a hard output budget, a
    sustained line-rate cap, and a live log sink.

    All three are needed. The budget bounds one run's total; the rate bounds the
    burst, and MEASURED it is the burst that took the server's stdout out; the
    sink check is what turns that failure into a refusal instead of a wrong
    answer.
    """

    def __init__(self, rc, budget: int,
                 lines_per_second: float = DEFAULT_LINES_PER_SECOND,
                 check_every: int = 8):
        self.rc = rc
        self.budget = budget
        self.lines_per_second = lines_per_second
        self.check_every = check_every
        self.spent = 0
        self.biggest = 0
        self.queries = 0
        self.began = time.time()
        self.waited = 0.0
        self.tip = log_tip()

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
        # Paid AFTER the reply, on the lines actually produced -- a box that
        # matched nothing costs nothing and is not paced for.
        owed = self.spent / self.lines_per_second - (time.time() - self.began)
        if owed > 0:
            time.sleep(min(owed, 5.0))
            self.waited += min(owed, 5.0)
        if self.queries % self.check_every == 0 and self.spent:
            fresh = log_tip()
            if fresh == self.tip:
                raise RuntimeError(
                    f"the container log has not advanced past {self.tip} across "
                    f"{self.check_every} listings that produced output, so the "
                    f"server's log sink has stopped draining. Every later answer "
                    f"would be partial; stopping with {self.spent} line(s) spent.")
            self.tip = fresh
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


@dataclass
class PlanObject:
    """One plan line in the shape `base_geometry` measures: world position, a
    real quaternion, unit scale."""

    prefab: str
    pos: tuple[float, float, float]
    rot: tuple[float, float, float, float]
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)


def plan_objects(path: Path) -> list[PlanObject]:
    """The plan as WORLD-frame objects, so the base profile can be rastered
    against the pad rather than against the blueprint's own origin.

    All three euler components are recovered, not just yaw: `rot=` is y,x,z and
    a pitched roof corner read as yaw-only would have the wrong collider
    footprint, which is precisely the kind of piece that bottoms an outlying
    column.

    Scale is unit because `spawn_object` carries scale in the base64 `data=`
    payload rather than in the command, and MEASURED on this body exactly one
    of 915 rows states a non-unit scale. That one row is reported by
    `to_rcon_plan` as `scale-only` and is a `sign`, which bottoms nothing.
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
        ey, ex, ez = ((float(v) for v in rot[4:].split(","))
                      if rot else (0.0, 0.0, 0.0))
        out.append(PlanObject(parts[1], (x, y, z),
                              to_rcon_plan.euler_to_quat(ex, ey, ez)))
    return out


def bottoming(objs: list[PlanObject], geom: base_geometry.Geometry,
              cell_m: float = base_geometry.BASE_PROFILE_CELL_M
              ) -> dict[tuple[int, int], tuple[int, float]]:
    """Which OBJECT is the lowest solid in each column, and at what Y.

    `base_geometry.base_profile` answers the height; this answers whose it is,
    which is what makes the air figure measurable against a live server: the
    verdict depends on a few dozen specific pieces, and those can be read back
    without asking for a listing of the whole body.
    """
    out: dict[tuple[int, int], tuple[int, float]] = {}
    for n, o in enumerate(objs):
        solids = geom._prefabs.get(o.prefab)
        if not solids:
            continue
        q = base_geometry._normalised(tuple(o.rot))
        sx, sy, sz = o.scale
        lo_x = lo_y = lo_z = math.inf
        hi_x = hi_z = -math.inf
        for s in solids:
            for cx0, cy0, cz0 in base_geometry._solid_corners(s):
                px, py, pz = base_geometry._qrot(q, (cx0 * sx, cy0 * sy, cz0 * sz))
                lo_x, hi_x = min(lo_x, px), max(hi_x, px)
                lo_z, hi_z = min(lo_z, pz), max(hi_z, pz)
                lo_y = min(lo_y, py)
        y0 = o.pos[1] + lo_y
        ix0 = math.floor((o.pos[0] + lo_x) / cell_m)
        ix1 = math.ceil((o.pos[0] + hi_x) / cell_m)
        iz0 = math.floor((o.pos[2] + lo_z) / cell_m)
        iz1 = math.ceil((o.pos[2] + hi_z) / cell_m)
        for ix in range(ix0, max(ix0 + 1, ix1)):
            for iz in range(iz0, max(iz0 + 1, iz1)):
                if (ix, iz) not in out or y0 < out[(ix, iz)][1]:
                    out[(ix, iz)] = (n, y0)
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
    ap.add_argument("--per-box", type=int, default=24,
                    help="most floor pieces one findObjects box may cover. "
                         "MEASURED: a `-Prefab: X Id: N:M Position: (x y z)` line "
                         "for these names runs 88-104 bytes, so ValheimRcon's "
                         "4050-byte payload cap truncates somewhere around 40 "
                         "lines; 24 leaves that headroom and a truncated reply "
                         "is refused rather than read")
    ap.add_argument("--budget", type=int, default=DEFAULT_BUDGET)
    ap.add_argument("--lines-per-second", type=float,
                    default=DEFAULT_LINES_PER_SECOND,
                    help="sustained object-line rate the floor sweep holds itself "
                         "to. The total budget alone is not enough: MEASURED, 21 "
                         "boxes answered in 0.7 s is ~700 lines/s and that burst "
                         "killed the game process's stdout while the world kept "
                         "saving")
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
        # Stated as two counts rather than one, because a single count cannot
        # tell a centred body from an off-centre one. The shell between
        # `plan_reach - 0.5` and `plan_reach` must be OCCUPIED (so the body does
        # reach that far) and the count at `plan_reach` must equal the count at
        # the wider `reach` (so nothing lies beyond it).
        #
        # Counted over the PLAN'S OWN PREFABS and capped at the planned count
        # per prefab, not over `id=*` totals. The totals version reported a
        # failure it was not measuring, MEASURED on this site: after
        # `terraform/stock.py` ran, three of its twelve fixtures stood between
        # 14.3 m and 17.3 m from the pad centre, so the wider cylinder held 3
        # more objects than the narrow one and the tool announced "3 object(s)
        # sit beyond 14.27 m, further out than the plan's furthest piece" --
        # about a cauldron, a hearth and a stonecutter that stock had
        # deliberately put on the apron. The count check above already tolerates
        # a surplus for exactly this reason; the reach check did not, which made
        # the tool unrunnable after stocking, which is when the operator most
        # wants to run it. Capping per prefab also handles a prefab BOTH own:
        # this body carries 2 `piece_chest` and stock adds 3.
        def body_count(lines_out: list[str]) -> int:
            here = Counter({name: int(n)
                            for name, n in COUNT_LINE_RE.findall("\n".join(lines_out))})
            return sum(min(here.get(k, 0), n) for k, n in want.items())

        inside = run_console(
            rc, f"objects_count id=* ignore=_* pos={cx:.2f},{cz:.2f} "
                f"max={plan_reach - 0.5:.2f}")
        tight = TOTAL_RE.search("\n".join(inside))
        at_reach = run_console(
            rc, f"objects_count id=* ignore=_* pos={cx:.2f},{cz:.2f} "
                f"max={plan_reach + 0.01:.2f}")
        full = TOTAL_RE.search("\n".join(at_reach))
        if tight and full:
            near_n, full_n = body_count(inside), body_count(at_reach)
            body_found = sum(min(live.get(k, 0), n) for k, n in want.items())
            print(f"reach from the pad centre, counting only the plan's prefabs and "
                  f"only up to their planned counts: {near_n} within "
                  f"{plan_reach - 0.5:.2f} m, {full_n} within {plan_reach + 0.01:.2f} m, "
                  f"{body_found} within {reach:.2f} m "
                  f"({int(full.group(1))} objects of any kind at that radius)")
            if full_n <= near_n:
                failures.append(f"nothing sits in the {plan_reach - 0.5:.2f}-"
                                f"{plan_reach:.2f} m shell, so the body does not reach "
                                f"as far as the plan put it")
            elif full_n != body_found:
                failures.append(f"{body_found - full_n} piece(s) of the body sit beyond "
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
        listing = Listing(rc, args.budget, args.lines_per_second)
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
        #
        # Tiled from the PLAN's own floor positions in ALL THREE axes, not into
        # four quadrants of the reach. Two measured failures shaped this:
        #
        #   * A quadrant is the wrong unit horizontally, because what has to be
        #     bounded is how many pieces one reply names, and that is set by the
        #     body's DENSITY rather than by how far it spreads. MEASURED,
        #     `halvar-master-refinery` on this pad: 127 stone_floor_2x2 inside a
        #     14.3 m reach, so a quadrant box of half 8.68 m held ~60 of them
        #     and the reply came back `--- message truncated ---`. A truncated
        #     reply has not seen every match, so it cannot be read -- the tool
        #     refused it and was then left with no floor measurement at all.
        #     `BjOrN_blueprint001` never hit it: 452 floor pieces over a 66 m
        #     footprint is a quarter of the density.
        #   * `findObjects -near X Y Z HALF` is a CUBE, so shrinking the box
        #     horizontally shrinks it VERTICALLY too. MEASURED on the first
        #     tiled run: 4.32 m cells centred on the pad found exactly 70 of the
        #     127, which is precisely the 68 + 2 pieces the plan puts on the
        #     ground storey -- the 57 on the storey at pad + 4.0 m were outside
        #     every box. A sweep that silently measures one floor of a two-floor
        #     building and reports the height it finds is the exact failure this
        #     file exists to avoid, so the storeys are enumerated from the plan
        #     and each is asked about at its own height.
        #
        # The cell is halved until no (cell, storey) group of the plan holds
        # more than `--per-box` pieces, and only groups the plan populates are
        # queried. That bounds each reply by construction and asks the smallest
        # number of questions that covers every piece.
        floors: list[tuple[str, str, float, float, float]] = []
        want_floor = [r for r in rows if r[0] == args.floor_prefab]

        # The cap is on what a BOX covers, never on what a cell is "assigned".
        # MEASURED failure of the cell-count version: at a 34.54 m edge the 68
        # ground-storey pieces split 24/20/14/10 across four cells because the
        # body straddles the cell origin, so `max(cell counts)` read 24 and
        # passed -- while each box, half 17.52 m, still contained all 68 and
        # came back truncated. `findObjects` answers about the box, so the box
        # is what has to be counted.
        def boxes_at(edge: float) -> list[tuple[tuple[int, int, float], int]]:
            half = edge / 2.0 + MARGIN_M
            keys = sorted({(math.floor((r[1] - cx) / edge),
                            math.floor((r[3] - cz) / edge),
                            round(r[2], 2)) for r in want_floor})
            out = []
            for ix, iz, level in keys:
                bx, bz = cx + (ix + 0.5) * edge, cz + (iz + 0.5) * edge
                out.append(((ix, iz, level), sum(
                    1 for r in want_floor
                    if abs(r[1] - bx) <= half and abs(r[3] - bz) <= half
                    and abs(r[2] - level) <= half)))
            return out

        cell = max(2.0 * reach, 1.0)
        covered = boxes_at(cell)
        while cell > 1.0 and covered and max(n for _, n in covered) > args.per_box:
            cell /= 2.0
            covered = boxes_at(cell)
        worst = max((n for _, n in covered), default=0)
        print(f"{args.floor_prefab}: {len(want_floor)} in the plan on "
              f"{len({k[2] for k, _ in covered})} storey(s), swept as "
              f"{len(covered)} box(es) of {cell:.2f} m; the fullest covers "
              f"{worst} piece(s) against a cap of {args.per_box}")
        # The margin overlaps adjacent boxes deliberately -- `NearCriteria`'s
        # bounds are strict, so a piece exactly on a cell line would fall in the
        # crack between two boxes -- and is kept well under the 2 m pivot pitch
        # of this prefab so the overlap cannot pull in a neighbour. Duplicates
        # are removed by ZDO id.
        for (ix, iz, level), _n in covered:
            floors += listing.near(
                args.floor_prefab,
                cx + (ix + 0.5) * cell, level, cz + (iz + 0.5) * cell,
                cell / 2.0 + MARGIN_M)
        unique = {f[1]: f for f in floors}
        span = geom.y_span(args.floor_prefab, (0, 0, 0, 1), (1, 1, 1))

        # 5. THE OPERATOR'S QUESTION: standing on the pad, is there daylight
        #    under the building?
        #
        # It is not answerable from the floor height -- `BjOrN_blueprint001`
        # had its ground-storey slabs exactly on this pad and 692 m2 of its
        # footprint standing up to 11.5 m in the air on a hillside deck. The
        # answer is the lowest SOLID in each 2 m column of the footprint,
        # rastered against the pad.
        #
        # Measured live without listing the whole body: the verdict depends on
        # the FEW pieces that bottom out a column, so the raster is built from
        # the plan, the bottoming pieces are identified, and only THOSE are read
        # back from the server. Every column whose bottoming piece is confirmed
        # in place is a column measured live; the report says how many, and any
        # column with a FLOATING bottom is always read, because those are the
        # ones the operator would be looking at.
        plan_objs = plan_objects(Path(args.plan))
        owners = bottoming(plan_objs, geom)
        air_cols = {k: v for k, v in owners.items()
                    if v[1] - cy > base_geometry.BASE_PROFILE_FLAT_TOL_M}
        cell_area = base_geometry.BASE_PROFILE_CELL_M ** 2
        gaps = sorted(v[1] - cy for v in air_cols.values())
        print(f"base profile: {len(owners)} occupied "
              f"{base_geometry.BASE_PROFILE_CELL_M:g} m column(s), "
              f"{len(owners) * cell_area:.0f} m2 of footprint; "
              f"{len(air_cols)} floating "
              f"({len(air_cols) / len(owners):.1%}, "
              f"{len(air_cols) * cell_area:.0f} m2, "
              f"{'none' if not gaps else f'mean {sum(gaps) / len(gaps):.2f} m, max {gaps[-1]:.2f} m, {sum(gaps) * cell_area:.1f} m3'})"
              f" against a {base_geometry.BASE_PROFILE_AIR_AREA_FRACTION:.0%} limit")
        # Read back every floating column's piece, then as many of the grounded
        # ones as the budget allows, nearest-the-edge first: an edge column is
        # where a body stops touching the pad, so it is where a wrong answer
        # would hide.
        want_check = [owners[k][0] for k in air_cols]
        grounded_keys = sorted(
            (k for k in owners if k not in air_cols),
            key=lambda k: -math.hypot(k[0] * base_geometry.BASE_PROFILE_CELL_M - cx,
                                      k[1] * base_geometry.BASE_PROFILE_CELL_M - cz))
        seen: set[int] = set()
        order: list[int] = []
        for n in want_check + [owners[k][0] for k in grounded_keys]:
            if n not in seen:
                seen.add(n)
                order.append(n)
        confirmed = 0
        worst_off = 0.0
        unconfirmed: list[str] = []
        for n in order:
            if listing.spent + args.per_box > args.budget:
                break
            o = plan_objs[n]
            try:
                hits = listing.near(o.prefab, o.pos[0], o.pos[1], o.pos[2],
                                    args.tol * 4)
            except RuntimeError as exc:
                print(f"  base read stopped: {exc}", file=sys.stderr)
                break
            best = min((h for h in hits),
                       key=lambda h: math.hypot(h[2] - o.pos[0], h[3] - o.pos[1],
                                                h[4] - o.pos[2]), default=None)
            if best is None:
                unconfirmed.append(f"{o.prefab} at ({o.pos[0]:.2f},"
                                   f"{o.pos[1]:.2f},{o.pos[2]:.2f})")
                continue
            off = math.hypot(best[2] - o.pos[0], best[3] - o.pos[1],
                             best[4] - o.pos[2])
            worst_off = max(worst_off, off)
            confirmed += 1
        checked_cols = sum(1 for k, v in owners.items()
                           if v[0] in order[:confirmed + len(unconfirmed)])
        print(f"  bottoming pieces: {len(set(owners[k][0] for k in owners))} distinct, "
              f"{confirmed} read back from the server (all "
              f"{len(air_cols)} floating one(s) first), worst position error "
              f"{worst_off:.3f} m; those pieces bottom {checked_cols} of "
              f"{len(owners)} column(s)")
        if unconfirmed:
            print(f"  NOT FOUND in the world: {unconfirmed[:6]}")
            failures.append(f"{len(unconfirmed)} bottoming piece(s) missing")
        if worst_off > args.tol:
            failures.append(f"a bottoming piece is {worst_off:.3f} m from the plan, "
                            f"so the live base profile is not the planned one")
        if len(air_cols) / len(owners) > base_geometry.BASE_PROFILE_AIR_AREA_FRACTION:
            failures.append(
                f"{len(air_cols) / len(owners):.1%} of the footprint stands clear of "
                f"the pad, over the "
                f"{base_geometry.BASE_PROFILE_AIR_AREA_FRACTION:.0%} limit")
        for k, (n, y0) in sorted(air_cols.items(),
                                 key=lambda kv: -(kv[1][1] - cy))[:6]:
            o = plan_objs[n]
            print(f"    column {k} stands {y0 - cy:+.2f} m clear, bottomed by "
                  f"{o.prefab} at ({o.pos[0]:.2f},{o.pos[1]:.2f},{o.pos[2]:.2f})")

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

    elapsed = max(1e-9, time.time() - listing.began)
    print(f"listings: {listing.queries} quer{'y' if listing.queries == 1 else 'ies'}, "
          f"{listing.spent}/{args.budget} object lines, largest reply "
          f"{listing.biggest} object(s), {listing.spent / elapsed:.1f} line/s "
          f"sustained against a {args.lines_per_second:g} cap "
          f"({listing.waited:.1f}s paced)")
    if failures:
        print("FAILED: " + "; ".join(failures), file=sys.stderr)
        return 1
    print("every check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
