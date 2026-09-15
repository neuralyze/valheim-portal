#!/usr/bin/env python3
"""Build one site end to end, in the one order that works, and stop on the first refusal.

The order is `tools/jumpstart/SITE_PREPARATION_ORDER.md` and every step in it
exists because skipping it broke something real:

  0/1  CLEAR      `clearing/clear.py apply` -- zones_generate FIRST, then a
                  prefab-scoped objects_remove. With no players online the
                  site's zones have never loaded, so its trees and rocks do not
                  exist as ZDOs yet and a delete-only pass has nothing to
                  delete. MEASURED: the workshop pad flattened correctly and the
                  trees were still standing in it.
       BASELINE   per-prefab ring counts, taken AFTER clearing and BEFORE
                  placing. Without this the stray check cannot tell my piece
                  from a neighbour's -- see `verify_site.py`.
  2    FLATTEN    `terraform/flatten.py apply` -- levels the pad to
                  `flatten_cost.target_y` by writing TCData into the zone's
                  `_TerrainCompiler`. Terrain edits cost ZERO persistent ZDOs
                  (MEASURED across three fleet worlds: 111,784 persistent
                  objects, not one TerrainModifier).
  3    PLACE      `terraform/place.py` -- the hardened batched sender.
  4    VERIFY     `network/verify_site.py` -- counts only, never a listing.

FIXTURES (`terraform/stock.py`) are deliberately NOT run here. They depend on
the body's interior mask, they are cheap to run later, and discovering that a
body cannot house a bed is a reason to change the BODY -- which is far cheaper
before the site is stocked than after.

An apron of 2 m is the default and it is measured, not chosen for comfort: the
clearing radius is derived from the pad rectangle, but a building's real reach
is its COLLIDER box, which is larger. MEASURED across the twelve Ulfsland
bodies, an apron of 0 leaves `pre-kall/deepnorth-landing-camp` clearing 10.72 m
against a 10.89 m reach -- 17 cm of building outside the cleared ground -- and
an apron of 2 m covers every site's reach with margin.

Usage:
    network/build_site.py --preset pre-moder --id mountain-outpost
    network/build_site.py --preset pre-moder --id mountain-outpost --skip clear
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
STATUS = Path("/media/big4/projects/game/valheim/Ulfsland/data/htdocs/status.json")


def players_online() -> int:
    """How many players are on, read from the web status file.

    Read from the file rather than asked over RCON on purpose: the RCON answer
    would be another query, and the whole discipline here is to issue as few as
    possible. `place.py` refuses to send with a player online anyway; this is
    the earlier, cheaper stop so a clearing pass does not start either.
    """
    try:
        return int(json.loads(STATUS.read_text()).get("player_count") or 0)
    except Exception:
        return -1


def run(step: str, cmd: list[str], tail: int = 14) -> None:
    print(f"\n=== {step}: {' '.join(str(c) for c in cmd[1:])}", flush=True)
    proc = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    out = (proc.stdout + proc.stderr).strip().splitlines()
    for line in out[-tail:]:
        print("   " + line, flush=True)
    if proc.returncode != 0:
        raise SystemExit(f"{step} FAILED with exit {proc.returncode}; stopping before the next step")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--world", default="Ulfsland")
    ap.add_argument("--preset", required=True)
    ap.add_argument("--id", required=True)
    ap.add_argument("--apron", type=float, default=2.0)
    ap.add_argument("--plans", default="/tmp/terraform/plans_wb")
    ap.add_argument("--skip", action="append", default=[],
                    choices=["clear", "baseline", "flatten", "place", "verify"])
    args = ap.parse_args()

    n = players_online()
    if n != 0:
        raise SystemExit(f"{n} player(s) online (or status.json unreadable: {n} == -1); "
                         f"refusing to touch the world")
    print(f"{args.preset}#{args.id}: 0 players online, proceeding")

    plan = Path(args.plans) / f"{args.preset}__{args.id}.txt"
    if not plan.exists():
        raise SystemExit(f"no plan at {plan}; run terraform/sites.py --flattened --out {args.plans}")
    baseline = Path("/tmp/terraform") / f"baseline_{args.preset}__{args.id}.json"

    common = ["--world", args.world, "--preset", args.preset, "--id", args.id]
    if "clear" not in args.skip:
        run("CLEAR", [JUMPSTART / "clearing" / "clear.py", "apply", *common,
                      "--apron", str(args.apron)], tail=8)
    if "baseline" not in args.skip:
        run("BASELINE", [HERE / "verify_site.py", *common, "--index", f"{args.plans}/index.json",
                         "--baseline", baseline, "--capture-baseline"], tail=6)
    if "flatten" not in args.skip:
        run("FLATTEN", [JUMPSTART / "terraform" / "flatten.py", "apply",
                        "--world", args.world, "--preset", args.preset, "--id", args.id], tail=10)
    if "place" not in args.skip:
        run("PLACE", [JUMPSTART / "terraform" / "place.py", plan,
                      "--checkpoint", f"/tmp/terraform/ck_{args.preset}__{args.id}.json",
                      "--save-every", "1000"], tail=8)
    if "verify" not in args.skip:
        run("VERIFY", [HERE / "verify_site.py", *common, "--index", f"{args.plans}/index.json",
                       "--baseline", baseline], tail=14)
    print(f"\n{args.preset}#{args.id}: structure built and verified. "
          f"Fixtures (terraform/stock.py) NOT run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
