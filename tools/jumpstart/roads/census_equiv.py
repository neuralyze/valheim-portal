#!/usr/bin/env python3
"""PROVE THE CHEAP CENSUS BY MEASURING IT AGAINST THE EXPENSIVE ONE.

The per-cell census (`clear.census_zoned`) is an order of magnitude cheaper
than the per-station one (`clear.census`) in calls and in echoed log lines,
and the whole road-repair pass depends on it being cheap.  Cheap is worthless
if it is also blind, so the two are run over the SAME GROUND and their object
sets are compared BY ZDO ID.

WHAT "THE SAME GROUND" MEANS, exactly, and why the comparison is taken on an
intersection rather than on the raw sets.  `findObjects -near x y z h` is a
CUBE of half-extent h, so:

  * the per-station run covers the union of its discs' CUBES -- radius
    1.35 * clear_half, so up to ~17 m from the centreline;
  * the per-cell run covers the union of 32 m CELLS, kept where a cell square
    reaches clear_half + 1 m of a station.

Neither region contains the other: the discs reach further laterally at a wide
station, the cells reach further into a corner the discs miss.  Comparing raw
sets would therefore report differences that are geometry rather than
blindness.  The honest test is on the INTERSECTION of the two regions, where
both censuses claim completeness: there the sets must be IDENTICAL, and any id
in one and not the other is reported as an id, not as a count delta.

The region predicates are evaluated from the returned positions, so this
script asks the server nothing the censuses did not already ask.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(JUMPSTART / "terraform"))
sys.path.insert(0, str(JUMPSTART / "ledger"))
sys.path.insert(0, str(JUMPSTART / "settlements"))
sys.path.insert(0, str(JUMPSTART))

import numpy as np  # noqa: E402

import clear as C  # noqa: E402


def in_boxes(o: dict, boxes: list) -> bool:
    """Is this object inside any of the query BOXES -- all three axes.

    `findObjects -near x y z r` takes one radius for every axis, so a query
    region is a cube and its y extent is part of it.  The first version of
    this script compared in XZ only and reported 38 "differences" that were
    all the per-station run's own y truncation at the T10 spawn hall: pieces
    at y 84-85 that a 7.42 m box centred at 75.73 cannot contain.  Comparing
    in two axes a three-axis question is how a complete census gets called
    incomplete.
    """
    for cx, cy, cz, h in boxes:
        if (abs(o["x"] - cx) <= h and abs(o["z"] - cz) <= h
                and abs(o["y"] - cy) <= h):
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--segment", default="T10")
    ap.add_argument("--discs", type=int, default=5,
                    help="how many consecutive removal discs form the "
                         "sub-range the two censuses are compared over")
    ap.add_argument("--first-disc", type=int, default=0)
    ap.add_argument("--out", default="/tmp/roadfinish/census_equiv_T10.json")
    args = ap.parse_args()

    seg = C.load_segment(args.segment, HERE / "segments.yaml")
    per_station, _widest = C.clear_half_width(seg)
    lat_of = C.lateral_fn(seg)
    discs = C.tiles_local(seg, per_station)[
        args.first_disc:args.first_disc + args.discs]
    # THE REGIONS, AS BOXES, because that is what the wire asked for.  The
    # per-station run's box is the disc's cube centred on the road profile at
    # that station; the per-cell run's boxes are the cell square at each of
    # the three y levels.
    # Every cell of the real per-segment cell set whose square meets the
    # sub-range: the cheap census is run exactly as it would be run for real,
    # restricted in extent but not in shape.
    all_cells = C.census_cells(seg, per_station)
    h = C.CENSUS_CELL_HALF_M
    cells = [(cx, cz) for cx, cz in all_cells
             if any(abs(cx - dx) <= h + r and abs(cz - dz) <= h + r
                    for dx, dz, r in discs)]
    names = C.universe()

    import replay as R  # noqa: PLC0415

    print(f"{seg['id']}: sub-range = discs "
          f"{args.first_disc}..{args.first_disc + len(discs) - 1} of "
          f"{len(C.tiles_local(seg, per_station))}, {len(cells)} cells, "
          f"{len(names)} names in the per-station universe")

    with R.Server(dry=False) as srv:
        srv.probe()

        C.echo_reset()
        t0 = time.time()
        zoned_objects: dict[str, dict] = {}
        zcalls = 0
        for i, (cx, cz) in enumerate(cells):
            got = lat_of(cx, cz)
            y = got[1] if got else 0.0
            for dy in C.CENSUS_Y_LEVELS:
                f, _un, n, _eng = C.list_box(srv, cx, y + dy, cz, h)
                zoned_objects.update(f)
                zcalls += n
            print(f"  [zoned] cell {i + 1}/{len(cells)} ({cx:.0f},{cz:.0f}) "
                  f"-> {len(zoned_objects)} objects, {zcalls} socket calls",
                  flush=True)
        zoned_echo = dict(C._ECHO)
        zoned_s = time.time() - t0

        C.echo_reset()
        t0 = time.time()
        station_objects: dict[str, dict] = {}
        scalls = 0
        for i, (cx, cz, r) in enumerate(discs):
            got = lat_of(cx, cz)
            y = got[1] if got else 0.0
            for name in names:
                f, _un, n = C.list_prefab(srv, name, cx, y, cz, r)
                station_objects.update(f)
                scalls += n
            print(f"  [station] disc {i + 1}/{len(discs)} ({cx:.0f},{cz:.0f}) "
                  f"r={r:.1f} -> {len(station_objects)} objects, {scalls} "
                  f"socket calls", flush=True)
        station_echo = dict(C._ECHO)
        station_s = time.time() - t0

    station_boxes = [(cx, (lat_of(cx, cz) or (0.0, 0.0))[1], cz, r)
                     for cx, cz, r in discs]
    cell_boxes = [(cx, (lat_of(cx, cz) or (0.0, 0.0))[1] + dy, cz, h)
                  for cx, cz in cells for dy in C.CENSUS_Y_LEVELS]

    def both_regions(o: dict) -> bool:
        return in_boxes(o, station_boxes) and in_boxes(o, cell_boxes)

    zin = {k: v for k, v in zoned_objects.items() if both_regions(v)}
    sin = {k: v for k, v in station_objects.items() if both_regions(v)}
    only_station = sorted(set(sin) - set(zin))
    only_zoned = sorted(set(zin) - set(sin))
    doc = {
        "segment": seg["id"],
        "sub_range": {"first_disc": args.first_disc, "discs": len(discs),
                      "disc_centres": [[round(a, 2), round(b, 2), round(c, 2)]
                                       for a, b, c in discs],
                      "cells": [[round(a, 2), round(b, 2)] for a, b in cells]},
        "per_station": {
            "socket_calls": station_echo["socket_calls"],
            "socket_reply_lines": station_echo["socket_reply_lines"],
            "console_calls": station_echo["console_calls"],
            "console_reply_lines": station_echo["console_reply_lines"],
            "objects_returned": len(station_objects),
            "objects_in_both_regions": len(sin),
            "names_asked_per_region": len(names),
            "seconds": round(station_s, 1)},
        "per_cell": {
            "socket_calls": zoned_echo["socket_calls"],
            "socket_reply_lines": zoned_echo["socket_reply_lines"],
            "console_calls": zoned_echo["console_calls"],
            "console_reply_lines": zoned_echo["console_reply_lines"],
            "objects_returned": len(zoned_objects),
            "objects_in_both_regions": len(zin),
            "seconds": round(zoned_s, 1)},
        "echo_per_object": {
            "per_station": round(station_echo["socket_reply_lines"]
                                 / max(len(station_objects), 1), 2),
            "per_cell": round(zoned_echo["socket_reply_lines"]
                              / max(len(zoned_objects), 1), 2)},
        "set_equal": not only_station and not only_zoned,
        "only_per_station": [
            {"id": k, "prefab": sin[k]["prefab"],
             "pos": [round(sin[k]["x"], 2), round(sin[k]["z"], 2)]}
            for k in only_station],
        "only_per_cell": [
            {"id": k, "prefab": zin[k]["prefab"],
             "pos": [round(zin[k]["x"], 2), round(zin[k]["z"], 2)]}
            for k in only_zoned],
        "prefabs_only_per_cell": sorted(
            {o["prefab"] for o in zoned_objects.values()}
            - {o["prefab"] for o in station_objects.values()}),
        "prefabs_only_per_station": sorted(
            {o["prefab"] for o in station_objects.values()}
            - {o["prefab"] for o in zoned_objects.values()}),
        "method": (
            "Both censuses run live in one RCON session, back to back, over "
            "the same sub-range. Sets compared by ZDO id over the "
            "INTERSECTION of the two covered regions (union of the "
            "per-station CUBES and union of the 32 m cells), because neither "
            "region contains the other and a raw set difference would report "
            "geometry as blindness. Call and reply-line counts are taken by "
            "clear.py::_ECHO, which both shapes increment in the same "
            "function."),
        "tool": "tools/jumpstart/roads/census_equiv.py",
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(doc, indent=1))
    print(json.dumps({k: v for k, v in doc.items()
                      if k not in ("sub_range", "method")}, indent=1))
    return 0 if doc["set_equal"] else 4


if __name__ == "__main__":
    sys.exit(main())
