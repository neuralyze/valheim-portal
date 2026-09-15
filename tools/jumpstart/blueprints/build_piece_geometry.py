#!/usr/bin/env python3
"""Compact the raw PieceGeometry collider dump into `data/piece_geometry.json`.

Input is the TSV that `run_piecegeometry.sh` produces inside a sandbox
dedicated server: one row per collider, eight corner points, in the PREFAB
ROOT's local frame. See PieceGeometry.cs for how those corners are obtained and
why corners rather than centre+extent.

What this keeps, and what it drops, stated because a data file that quietly
drops the case you needed is the expensive kind of bug:

  KEPT    every non-trigger collider of every prefab, as either
            ["b", minx, miny, minz, maxx, maxy, maxz]   axis-aligned, 6 floats
            ["p", x0, y0, z0, ... x7, y7, z7]           oblique, 24 floats
          The `b` form is used only when the corner set really is axis-aligned
          in prefab-local space (at most two distinct values per axis), so it is
          lossless, not an approximation.
  KEPT    prefabs with NO solid collider at all, as an empty list. That is the
          difference between "measured, has no solid" and "never measured",
          and the datum rule has to be able to tell them apart in order to
          refuse rather than guess.
  DROPPED trigger colliders. A trigger is an interaction volume, not geometry.
  DROPPED render meshes. The datum question is about solids.

Usage:
    VH_SRC=<server install> OUT=/tmp/piece_colliders.tsv \
        tools/jumpstart/blueprints/run_piecegeometry.sh
    tools/jumpstart/blueprints/build_piece_geometry.py /tmp/piece_colliders.tsv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE / "data" / "piece_geometry.json"
ROUND = 4


def compact(rows) -> tuple[dict[str, list], dict[str, bool], dict[str, int]]:
    prefabs: dict[str, list] = {}
    is_piece: dict[str, bool] = {}
    tally = {"colliders": 0, "axis_aligned": 0, "oblique": 0, "unmeasurable": 0,
             "triggers_dropped": 0, "render_meshes_dropped": 0}
    for r in rows:
        name = r["name"]
        prefabs.setdefault(name, [])
        # `has_piece` separates BUILD pieces from world decor. It is load-bearing
        # for the floorless fallback: `Portal13.vbuild` contains a `Rock_4` whose
        # solid reaches 3.02 m below its pivot -- a rock modelled to be sunk into
        # terrain -- while every built piece in that body bottoms out at 0.066.
        # Taking the lowest solid over ALL objects would lift the whole shrine
        # 3 m into the air, which is the reported bug wearing a different hat.
        is_piece[name] = is_piece.get(name, False) or r["has_piece"] == "1"
        kind = r["kind"]
        if kind == "mesh_render":
            tally["render_meshes_dropped"] += 1
            continue
        if r["is_trigger"] == "1":
            tally["triggers_dropped"] += 1
            continue
        if kind in ("none", "mesh_null") or r["x0"] == "na":
            # `none` means the prefab has no collider component at all;
            # `mesh_null` means it has one whose mesh was not loadable. Both are
            # recorded by their ABSENCE from the solid list, and mesh_null is
            # counted so a caller can see that a measurement failed.
            if kind != "none":
                tally["unmeasurable"] += 1
            continue
        pts = [(float(r[f"x{i}"]), float(r[f"y{i}"]), float(r[f"z{i}"])) for i in range(8)]
        xs = {round(p[0], ROUND) for p in pts}
        ys = {round(p[1], ROUND) for p in pts}
        zs = {round(p[2], ROUND) for p in pts}
        tally["colliders"] += 1
        if len(xs) <= 2 and len(ys) <= 2 and len(zs) <= 2:
            tally["axis_aligned"] += 1
            prefabs[name].append(["b", min(xs), min(ys), min(zs), max(xs), max(ys), max(zs)])
        else:
            tally["oblique"] += 1
            prefabs[name].append(["p"] + [round(v, ROUND) for p in pts for v in p])
    return prefabs, is_piece, tally


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tsv", help="raw dump from run_piecegeometry.sh")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--game-version", default="1.0.12")
    args = ap.parse_args(argv)

    src = Path(args.tsv)
    with src.open(newline="", encoding="utf-8") as fh:
        prefabs, is_piece, tally = compact(csv.DictReader(fh, delimiter="\t"))

    doc = {
        "generated_by": "tools/jumpstart/blueprints/build_piece_geometry.py",
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "tools/jumpstart/blueprints/PieceGeometry.cs via run_piecegeometry.sh",
        "game_version": args.game_version,
        "frame": "prefab root local space; a blueprint row's x/y/z is in this frame",
        "units": "metres",
        "solid_only": True,
        "totals": dict(tally, prefabs=len(prefabs),
                       prefabs_without_solid=sum(1 for v in prefabs.values() if not v)),
        "build_pieces": sorted(k for k, v in is_piece.items() if v),
        "prefabs": prefabs,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{out}: {out.stat().st_size} bytes, {len(prefabs)} prefabs, "
          f"{tally['colliders']} solid colliders "
          f"({tally['axis_aligned']} axis-aligned, {tally['oblique']} oblique), "
          f"{doc['totals']['prefabs_without_solid']} prefabs with no solid collider",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
