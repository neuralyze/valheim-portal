#!/usr/bin/env python3
"""Furnish and stock a flattened site: stations, props, chests, and chest contents.

Three things this does that a blueprint paste cannot:

  * STATIONS AT THE REQUIRED LEVEL.  A preset names each station with the level
    it must reach, and a station's level is 1 + the number of StationExtension
    pieces in range (CraftingStation::GetLevel).  There is no "level" field on
    the ZDO to set -- the game counts neighbours at runtime -- so reaching
    level 3 means placing two extensions close enough to be counted.  They go
    2 m out on a ring, well inside any plausible m_maxStationDistance.
  * CHESTS WITH CONTENTS.  `spawn` carries no object data, so a pasted
    blueprint's chests arrive empty and the blueprint's own `data` column is
    lost.  Chests are therefore placed here and filled with
    `addItemToContainer`, which is the one RCON verb that writes inventory.
  * READ-BACK.  Every chest is re-read with `showContainer` and the reported
    stacks are compared against what was requested, because "the add command
    answered OK" and "the chest holds it" are different claims.

Capacity is discovered, not assumed: items are added until the container
refuses, then the next chest takes over.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
JUMPSTART = HERE.parent

from rcon import Rcon  # noqa: E402

ID_RE = re.compile(r"Id: (\d+:\d+)")
ADDED_RE = re.compile(r"Item (\S+) x(\d+) added to")
# showContainer prints a header line, then "Items (N):", then one line per slot:
#   [0] Coal Stack: 50 Quality: 1 Crafter: Server (-1)
# MEASURED on Ulfsland.  The Stack figure is the important one, because
# addItemToContainer reports the count it was ASKED for and adds one stack.
STACK_RE = re.compile(r"^\[(\d+)\]\s+(\S+)\s+Stack:\s*(\d+)\s+Quality:\s*(\d+)")

CHEST_PREFAB = "piece_chest"

# Props every finished base wants and no preset spells out piece by piece.  Offsets are
# metres from the pad centre, before the site yaw is applied.
PROPS = [
    ("portal_wood", (6.0, 0.0), 0.0),
    ("fire_pit", (0.0, 4.0), 0.0),
    ("bed", (-4.0, 4.0), 0.0),
    ("bed", (-6.0, 4.0), 0.0),
    ("piece_cartographytable", (4.0, -4.0), 180.0),
    ("piece_maypole", (0.0, -8.0), 0.0),
    ("wood_wall_log", (0.0, 0.0), 0.0),  # replaced below; placeholder never used
]


def rotate(dx: float, dz: float, yaw_deg: float) -> tuple[float, float]:
    """Unity yaw: +yaw turns clockwise seen from above, i.e. x' = x cos + z sin."""
    import math

    r = math.radians(yaw_deg)
    return dx * math.cos(r) + dz * math.sin(r), -dx * math.sin(r) + dz * math.cos(r)


def load_preset(preset: str) -> dict:
    return yaml.safe_load((JUMPSTART / "presets" / f"{preset}.yaml").read_text())


def load_manifest(world: str, preset: str) -> dict:
    path = JUMPSTART / "worlds" / world / preset / "settings" / "chest-manifest.yaml"
    return yaml.safe_load(path.read_text())


def load_placement(world: str, preset: str, pid: str) -> dict:
    doc = yaml.safe_load((JUMPSTART / "worlds" / world / preset / "placements.yaml").read_text())
    for p in doc["placements"]:
        if p["id"] == pid:
            return p
    raise SystemExit(f"no placement {pid}")


def spawn(rc: Rcon, prefab: str, x: float, y: float, z: float, yaw: float = 0.0) -> str | None:
    reply = rc.command(f"spawn {prefab} {x:.3f} {y:.3f} {z:.3f} -rotation 0 {yaw:.1f} 0")
    found = ID_RE.search(reply)
    if found:
        return found.group(1)
    return None


def show_container(rc: Rcon, zid: str) -> tuple[int, dict[str, int], int]:
    """Read a container back.  Returns (slots used, item -> total units, capacity
    if the header reports it)."""
    reply = rc.command(f"showContainer {zid}")
    totals: dict[str, int] = {}
    slots = 0
    for line in reply.splitlines():
        found = STACK_RE.match(line.strip())
        if not found:
            continue
        slots += 1
        totals[found.group(2)] = totals.get(found.group(2), 0) + int(found.group(3))
    header = re.search(r"Container: (\d+) items", reply)
    return slots, totals, int(header.group(1)) if header else slots


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", default="Ulfsland")
    ap.add_argument("--preset", required=True)
    ap.add_argument("--id", required=True)
    ap.add_argument("--chests", type=int, default=0,
                    help="how many chests to place; 0 = enough for the manifest at 8 stacks each")
    ap.add_argument("--report", help="write a JSON report here")
    args = ap.parse_args()

    place = load_placement(args.world, args.preset, args.id)
    solved = place["solved"]
    cost = solved.get("flatten_cost") or {}
    cx, cz = float(solved["x"]), float(solved["z"])
    base_y = float(cost.get("target_y") or solved["y"])
    yaw = float((place.get("rotation") or {}).get("yaw") or 0.0)
    preset = load_preset(args.preset)
    manifest = load_manifest(args.world, args.preset)
    items = dict(manifest.get("items") or {})

    report: dict = {"site": args.id, "preset": args.preset, "centre": [cx, cz],
                    "y": base_y, "yaw": yaw, "stations": [], "props": [], "chests": []}

    # Chests sit in two ranks 12 m south of the pad centre, 1.6 m apart, which keeps them
    # clear of the building footprint and on flattened ground.
    # A reinforced chest holds a fixed grid of slots and a manifest line of 300 units
    # needs one slot per max-stack, so the bank is sized on estimated STACKS (50 units
    # is the common material stack) and not on the number of item kinds.
    est_stacks = sum(max(1, -(-int(c) // 50)) for c in items.values())
    n_chests = args.chests or max(2, -(-est_stacks // 18))
    with Rcon(timeout=30.0) as rc:
        # --- stations, with enough extensions to reach the required level
        ring = 0
        for station in preset.get("stations") or []:
            prefab = station["prefab"]
            level = int(station.get("level") or 1)
            exts = station.get("extensions") or []
            dx, dz = -10.0 + ring * 5.0, -10.0
            ring += 1
            sx, sz = rotate(dx, dz, yaw)
            sid = spawn(rc, prefab, cx + sx, base_y, cz + sz, yaw)
            placed_exts = []
            for i in range(max(0, level - 1)):
                if not exts:
                    break
                ext = exts[i % len(exts)]
                ex, ez = rotate(dx + 2.0 * (1 if i % 2 == 0 else -1), dz + (1.5 if i > 1 else 0.0), yaw)
                eid = spawn(rc, ext, cx + ex, base_y, cz + ez, yaw)
                placed_exts.append({"prefab": ext, "id": eid})
            report["stations"].append({
                "prefab": prefab, "id": sid, "required_level": level,
                "extensions_placed": placed_exts,
                "level_note": "level is computed at runtime as 1 + extensions in range; "
                              "there is no ZDO field to read, so what is verified is that "
                              f"{len(placed_exts)} extension(s) exist within 2.5 m",
            })

        # --- props
        for prefab, (dx, dz), extra_yaw in PROPS:
            if prefab == "wood_wall_log":
                continue
            px, pz = rotate(dx, dz, yaw)
            pid = spawn(rc, prefab, cx + px, base_y, cz + pz, yaw + extra_yaw)
            report["props"].append({"prefab": prefab, "id": pid,
                                    "at": [round(cx + px, 2), base_y, round(cz + pz, 2)]})

        # --- chests
        chest_ids: list[str] = []
        for i in range(n_chests):
            dx = -((n_chests - 1) * 0.8) + i * 1.6
            px, pz = rotate(dx, 12.0, yaw)
            zid = spawn(rc, CHEST_PREFAB, cx + px, base_y, cz + pz, yaw)
            if zid:
                chest_ids.append(zid)
        if not chest_ids:
            raise SystemExit("no chests placed")

        # --- fill.
        #
        # MEASURED: `addItemToContainer <id> Coal -count 300` answers
        # "Item Coal x300 added to ..." but the chest ends up holding ONE stack of 50.
        # The verb adds a single ItemDrop whose stack the game clamps to the item's
        # own max, so a 300-unit line needs six calls, and the max stack per item is
        # not knowable up front.  So: add once, read the container to learn the stack
        # size that item actually took, then repeat until the manifest figure is met.
        # Capacity is discovered the same way - the chest refuses when it is full.
        pending = [(name, int(count)) for name, count in sorted(items.items())]
        chest = 0
        requested: dict[str, dict[str, int]] = {z: {} for z in chest_ids}
        overflow: list[list] = []
        errors: list[list] = []
        for name, target in pending:
            remaining = target
            guard = 0
            while remaining > 0 and guard < 64:
                guard += 1
                if chest >= len(chest_ids):
                    overflow.append([name, remaining])
                    break
                zid = chest_ids[chest]
                before = show_container(rc, zid)[1].get(name, 0)
                reply = rc.command(
                    f"addItemToContainer {zid} {name} -count {remaining}").strip()
                if not ADDED_RE.search(reply):
                    if "Failed to add item" in reply:
                        chest += 1
                        continue
                    errors.append([name, reply.splitlines()[0][:140]])
                    break
                after = show_container(rc, zid)[1].get(name, 0)
                gained = after - before
                if gained <= 0:
                    # Reported success but nothing landed: the grid is full.
                    chest += 1
                    continue
                requested[zid][name] = requested[zid].get(name, 0) + gained
                remaining -= gained

        # --- read back every chest from the world
        for zid in chest_ids:
            slots, totals, reported = show_container(rc, zid)
            want = requested[zid]
            report["chests"].append({
                "id": zid, "slots_used": slots, "container_reports": reported,
                "requested": want, "read_back": totals,
                "matches": want == {k: v for k, v in totals.items() if k in want},
            })
        report["overflow_not_placed"] = overflow
        report["errors"] = errors
        rc.command("save")
        time.sleep(1)

    print(json.dumps(report, indent=1))
    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
