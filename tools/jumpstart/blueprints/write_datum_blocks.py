#!/usr/bin/env python3
"""Record each placement's measured floor datum into its `placements.yaml`.

Writes one `blueprint_datum` mapping per placement, in place, preserving every
other key. The block is DURABLE, not seed-specific: it is a property of the
blueprint body, so unlike `solved` it survives a world re-roll. Fields:

    base_y            blueprint-local Y of the floor plane; the placement Y is
                      `flatten_cost.target_y - base_y`
    method            which rule answered (see base_geometry.floor_datum)
    walkable_levels   every walkable surface in the body, each with
                      `above_pad_m`: the unavoidable residual of laying a
                      slope-captured building on a flat plane
    violations        the reported costs, verbatim
    override_base_y   absent unless a human declares a different plane; when
                      present `terraform/sites.py` passes it to
                      `to_rcon_plan.py --base-y` and it must carry a `basis`

Usage:
    tools/jumpstart/blueprints/write_datum_blocks.py            # dry run table
    tools/jumpstart/blueprints/write_datum_blocks.py --write
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(JUMPSTART / "terraform"))
sys.path.append(str(JUMPSTART))

import base_geometry as bg  # noqa: E402
from survey_datum import resolve_bodies  # noqa: E402
from to_rcon_plan import read_objects  # noqa: E402
import flatten as flattenmod  # noqa: E402  terraform/, for THE placements reader
import worldseed  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    worldseed.add_arguments(ap)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)
    _seed, world = worldseed.apply(args)

    geom = bg.geometry()
    bodies = resolve_bodies()
    root = JUMPSTART / "worlds" / world

    print(f"{'preset':20s} {'placement':27s} {'pad':>8s} {'base_y':>8s} "
          f"{'OLD y':>8s} {'NEW y':>8s} {'delta':>8s}  method")
    for path in sorted(root.glob("*/placements.yaml")):
        # EACH FILE IS ITS OWN DECLARATION.  Nine presets, nine `world:`/`seed:`
        # headers; scope-checking the first and calling the sweep done is how
        # eight go unread.  This loop REWRITES every file it touches.
        doc = flattenmod.load_placements(world, path.parent.name)
        changed = False
        for place in doc["placements"]:
            solved = place.get("solved") or {}
            if not solved:
                continue
            datum = bg.floor_datum(read_objects(bodies[place["blueprint"]]), geom)
            block = datum.summary_dict()
            existing = place.get("blueprint_datum") or {}
            if "override_base_y" in existing:
                block["override_base_y"] = existing["override_base_y"]
                block["basis"] = existing.get("basis", "")
            place["blueprint_datum"] = block
            changed = True

            pad = float((solved.get("flatten_cost") or {}).get("target_y") or solved["y"])
            base = block.get("override_base_y", datum.base_y)
            new = None if base is None else pad - base
            print(f"{path.parent.name:20s} {place['id']:27s} {pad:8.2f} "
                  f"{'    --  ' if base is None else f'{base:8.3f}'} {pad:8.2f} "
                  f"{'    --  ' if new is None else f'{new:8.2f}'} "
                  f"{'    --  ' if new is None else f'{new - pad:+8.3f}'}  {datum.method}")
        if changed and args.write:
            path.write_text(
                yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100), "utf-8")
    if not args.write:
        print("\ndry run; pass --write to update placements.yaml", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
