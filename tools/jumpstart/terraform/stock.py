#!/usr/bin/env python3
"""Furnish and stock a flattened site: stations, props, chests, and chest contents.

WHAT THIS OWNS, AND WHAT IT DOES NOT
------------------------------------
The BLUEPRINT owns the POSITION of everything it carries. This file owns only
what the blueprint does not carry, plus every chest's CONTENTS.

That split is measured, and getting it wrong is what the operator walked into.
MEASURED on `pre-bonemass/iron-era-workshop` on 2026-09-15, box-testing what
this file placed against all 1,906 solids of the body that was placed before it:
12 of its 18 fixtures were INSIDE the structure. `portal_wood` at pad-offset
(-6.00, 0.00) overlapped a `stone_wall_4x2` by 1.00 x 2.00 x 1.18 m -- the
operator's "there are portals in the middle of walls". `piece_maypole` pierced
22 solids, `smelter` 20, `piece_workbench` 11; the minimum fixture-to-wall gap
across the whole set was 0.000 m, because nothing was checking.

Two separate faults, both fixed here:

  * DUPLICATION. The pad offsets were solved when the body was anchored on its
    own corner and the middle of the pad was empty. They are also spawns of
    prefabs the body already has: this body carries all NINE of the preset's
    stations plus `piece_cartographytable`, so those spawns were second copies
    standing in the walls of the first. `blueprints/fixtures.carried()` is now
    the authority, and a station the blueprint carries is NOT re-placed -- only
    the extensions needed to reach the preset's required LEVEL are added, beside
    the blueprint's own station, at that station's own floor height.
  * POSITION. What genuinely is missing (`fire_pit`, the beds, `piece_maypole`,
    the bulk-material chest bank) is placed at a position SEARCHED against the
    body's real solids -- `blueprints/fixtures.free_spot()` -- and then audited,
    rather than at an offset that was once true.

Three things this does that a blueprint paste cannot:

  * STATIONS AT THE REQUIRED LEVEL.  A preset names each station with the level
    it must reach, and a station's level is 1 + the number of StationExtension
    pieces in range (CraftingStation::GetLevel).  There is no "level" field on
    the ZDO to set -- the game counts neighbours at runtime -- so reaching
    level 3 means placing two extensions close enough to be counted.
  * CHESTS WITH CONTENTS.  `spawn` carries no object data, so a pasted
    blueprint's chests arrive empty and the blueprint's own `data` column is
    lost.  Chests are therefore placed here and filled with
    `addItemToContainer`, which is the one RCON verb that writes inventory.
  * READ-BACK.  Every chest is re-read with `showContainer` and the reported
    stacks are compared against what was requested, because "the add command
    answered OK" and "the chest holds it" are different claims.

Capacity is discovered, not assumed: items are added until the container
refuses, then the next chest takes over.

`--plan` does the whole placement decision offline -- body transform, carried
census, free-spot search, audit -- and touches no server, so what this file is
about to do can be inspected and tested without a world.
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
sys.path.insert(0, str(JUMPSTART / "blueprints"))

from rcon import Rcon  # noqa: E402
import fixtures  # noqa: E402  (jumpstart/blueprints/fixtures.py)
import to_rcon_plan  # noqa: E402

ID_RE = re.compile(r"Id: (\d+:\d+)")
ADDED_RE = re.compile(r"Item (\S+) x(\d+) added to")
# showContainer prints a header line, then "Items (N):", then one line per slot:
#   [0] Coal Stack: 50 Quality: 1 Crafter: Server (-1)
# MEASURED on Ulfsland.  The Stack figure is the important one, because
# addItemToContainer reports the count it was ASKED for and adds one stack.
STACK_RE = re.compile(r"^\[(\d+)\]\s+(\S+)\s+Stack:\s*(\d+)\s+Quality:\s*(\d+)")

CHEST_PREFAB = "piece_chest"

# Props every finished base wants and no preset spells out piece by piece.
#
# The offsets are PREFERENCES, not positions: the placement search starts from
# them and moves until the prop clears the building. They were positions once,
# and that is precisely how `portal_wood` ended up inside a `stone_wall_4x2`.
# A prop whose prefab the blueprint already carries is not placed at all.
PROPS = [
    ("portal_wood", (6.0, 0.0), 0.0),
    ("fire_pit", (0.0, 4.0), 0.0),
    ("bed", (-4.0, 4.0), 0.0),
    ("bed", (-6.0, 4.0), 0.0),
    ("piece_cartographytable", (4.0, -4.0), 180.0),
    ("piece_maypole", (0.0, -8.0), 0.0),
]

# How far a StationExtension may sit from its station and still be counted.
# `CraftingStation::GetLevel` counts extensions within the station's own
# `m_maxStationDistance`, which is not readable from RCON, so the search is
# bounded well inside any plausible value and the achieved distance is reported
# rather than assumed.
EXT_SEARCH_M = 2.5


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


def load_body(place: dict, cx: float, cz: float, pad_y: float, yaw: float):
    """The placed blueprint this site is being stocked around.

    Returns None when the placement names no blueprint -- a bare pad still gets
    stocked, it just has nothing to collide with. The transform is
    `fixtures.place_body`, which is the SAME arithmetic `to_rcon_plan.py` emits
    with, so the solids tested against are the solids in the world. Verified:
    reconstructing eight of this body's fixtures and comparing against their
    positions read back from the live server agrees to 0.007 m.
    """
    name = place.get("blueprint")
    if not name:
        return None
    import survey_datum  # noqa: E402  (blueprints/, for the corpus roots)

    path = None
    for root in survey_datum.CORPUS_ROOTS:
        direct = root / name
        hits = [direct] if direct.is_file() else (
            sorted(root.rglob(name)) if root.is_dir() else [])
        for hit in hits:
            if hit.stat().st_size:
                path = hit
                break
        if path:
            break
    if path is None:
        raise SystemExit(f"placement {place['id']} names blueprint {name}, which is "
                         f"not in any corpus root; cannot check fixtures against it")
    datum = place.get("blueprint_datum") or {}
    override = datum.get("override_base_y")
    objs = to_rcon_plan.read_objects(path)
    return fixtures.place_body(objs, at=(cx, pad_y, cz), yaw_deg=yaw,
                               base_y=None if override is None else float(override))


def solve_placement(place: dict, preset: dict, n_chests: int,
                    cx: float, cz: float, pad_y: float, yaw: float,
                    pad_half: float) -> dict:
    """Decide WHERE everything goes, before anything is spawned.

    Offline and total: every station, extension, prop and chest is either
    declared owned by the blueprint or given a position that has been box-tested
    against the body. Nothing here talks to a server, which is what makes it
    testable and what makes `--plan` honest.
    """
    body = load_body(place, cx, cz, pad_y, yaw)
    index = body.index() if body else None
    carried = dict(body.carried) if body else {}
    taken: list = []
    out: dict = {
        "blueprint": place.get("blueprint"),
        "carried_by_blueprint": carried,
        "body_solids": len(body.solids) if body else 0,
        "base_y": None if body is None else (
            None if body.datum.base_y is None else round(body.datum.base_y, 3)),
        "datum_method": None if body is None else body.datum.method,
        "stations": [], "props": [], "chests": [], "skipped": [],
    }

    def spot(prefab: str, prefer_offset, want_yaw: float,
             centre=None, y=None, half=None, step=1.0, headroom=2.0):
        c = centre or (cx, cz)
        px, pz = rotate(*prefer_offset, yaw) if centre is None else prefer_offset
        if body is None:
            return {"prefab": prefab, "x": round(c[0] + px, 3), "y": y or pad_y,
                    "z": round(c[1] + pz, 3), "yaw": want_yaw,
                    "offset": [round(px, 3), round(pz, 3)],
                    "clearance_m": None, "from_prefer_m": 0.0,
                    "box": None, "note": "no blueprint on this placement to check against"}
        found = fixtures.free_spot(
            prefab, body, index, c, y if y is not None else pad_y,
            half if half is not None else pad_half,
            prefer=(c[0] + px, c[1] + pz), yaw_deg=want_yaw, step_m=step,
            headroom_m=headroom, taken=taken)
        if found and found.get("box"):
            taken.append(tuple(found["box"]))
        return found

    # --- stations. The blueprint owns any station it carries; only the
    #     extensions needed for the preset's LEVEL are added, beside the
    #     blueprint's own station and at that station's own floor height.
    for station in preset.get("stations") or []:
        prefab = station["prefab"]
        level = int(station.get("level") or 1)
        exts = station.get("extensions") or []
        have = carried.get(prefab, 0)
        row: dict = {"prefab": prefab, "required_level": level,
                     "carried_by_blueprint": have, "extensions": []}
        if have:
            row["place"] = None
            row["owner"] = "blueprint"
            anchors = [s for s in body.solids if s[6] == prefab] if body else []
            # the blueprint's own copies, highest floor last, so the extensions
            # go to the one a player is most likely to be standing at: the
            # LOWEST, which is the one reachable from the pad.
            anchors.sort(key=lambda s: s[2])
            row["blueprint_positions"] = [
                [round((s[0] + s[1]) / 2, 2), round(s[2], 2), round((s[4] + s[5]) / 2, 2)]
                for s in anchors]
            anchor = anchors[0] if anchors else None
        else:
            row["owner"] = "stock"
            found = spot(prefab, (-10.0 + len(out["stations"]) * 5.0, -10.0), yaw)
            row["place"] = found
            anchor = None
            if found:
                anchor = (found["x"], found["x"], found["y"], found["y"],
                          found["z"], found["z"], prefab)
        already = sum(carried.get(e, 0) for e in exts)
        need = max(0, level - 1 - already)
        row["extensions_carried"] = already
        row["extensions_needed"] = need
        for i in range(need):
            if not exts:
                break
            ext = exts[i % len(exts)]
            if anchor is None:
                row["extensions"].append({"prefab": ext, "place": None,
                                          "note": "no anchor station position known"})
                continue
            ax, ay, az = (anchor[0] + anchor[1]) / 2, anchor[2], (anchor[4] + anchor[5]) / 2
            # Two attempts, coarse then fine. An extension goes beside a station
            # the builder already walled in, so the free space around it is
            # centimetres wide; the fine pass exists because MEASURED on
            # `deepnorth-sandbox/complete-station-hub` the coarse one leaves a
            # `blackforge_ext4` and a `piece_magetable_ext` with nowhere to
            # stand, and half a preset's station levels is worse than a 0.25 m
            # grid. Neither pass will accept an intersection.
            found = None
            for half, step, margin in ((EXT_SEARCH_M, 0.5, 0.1),
                                       (EXT_SEARCH_M + 1.0, 0.25, 0.05)):
                found = fixtures.free_spot(
                    ext, body, index, (ax, az), ay, half,
                    prefer=(ax, az), yaw_deg=yaw, step_m=step, margin_m=margin,
                    headroom_m=0.5, taken=taken) if body else None
                if found:
                    break
            if found and found.get("box"):
                taken.append(tuple(found["box"]))
            row["extensions"].append({
                "prefab": ext, "place": found,
                "distance_m": None if not found else found["from_prefer_m"],
                # EXT_SEARCH_M is the search BOUND, not a game constant: the grid
                # is square so its corners reach further than its half-width, and
                # a spot beyond the bound is reported rather than silently kept.
                "within_search_bound": bool(found)
                                       and found["from_prefer_m"] <= EXT_SEARCH_M,
            })
        out["stations"].append(row)

    # --- props
    for prefab, offset, extra_yaw in PROPS:
        if carried.get(prefab):
            out["skipped"].append({"prefab": prefab, "reason": "carried by the blueprint",
                                   "count_in_body": carried[prefab]})
            continue
        out["props"].append({"prefab": prefab,
                             "place": spot(prefab, offset, yaw + extra_yaw)})

    # --- the bulk-material chest bank. Its own prefab, `piece_chest`, is
    #     deliberately not one the bodies carry (they use `piece_chest_wood`),
    #     so the bank is always stock's and is always placed by search.
    for i in range(n_chests):
        out["chests"].append({
            "prefab": CHEST_PREFAB,
            "place": spot(CHEST_PREFAB,
                          (-((n_chests - 1) * 0.8) + i * 1.6, 12.0), yaw),
        })
    return out


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
    ap.add_argument("--plan", action="store_true",
                    help="decide and audit every position offline, spawn nothing, "
                         "touch no server")
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
    pad_half = float((place.get("requirement") or {}).get("footprint_m") or 70.0) / 2.0

    report: dict = {"site": args.id, "preset": args.preset, "centre": [cx, cz],
                    "y": base_y, "yaw": yaw, "stations": [], "props": [], "chests": []}

    # A reinforced chest holds a fixed grid of slots and a manifest line of 300 units
    # needs one slot per max-stack, so the bank is sized on estimated STACKS (50 units
    # is the common material stack) and not on the number of item kinds.
    est_stacks = sum(max(1, -(-int(c) // 50)) for c in items.values())
    n_chests = args.chests or max(2, -(-est_stacks // 18))

    # Decide everything first, then audit the decision, then spawn. The audit is
    # the guard the operator had to act as: 12 of the 18 fixtures this file used
    # to place stood inside the building, and no check said so.
    plan = solve_placement(place, preset, n_chests, cx, cz, base_y, yaw, pad_half)
    report["plan"] = plan
    to_place: list[tuple[str, dict]] = []
    unplaceable: list[dict] = []
    for row in plan["stations"]:
        if row["place"]:
            to_place.append(("station", dict(row["place"], prefab=row["prefab"])))
        elif row["owner"] == "stock":
            unplaceable.append({"prefab": row["prefab"], "role": "station"})
        for ext in row["extensions"]:
            if ext["place"]:
                to_place.append(("extension", dict(ext["place"], prefab=ext["prefab"])))
            else:
                unplaceable.append({"prefab": ext["prefab"], "role": "extension",
                                    "of": row["prefab"]})
    for row in plan["props"]:
        if row["place"]:
            to_place.append(("prop", dict(row["place"], prefab=row["prefab"])))
        else:
            unplaceable.append({"prefab": row["prefab"], "role": "prop"})
    for row in plan["chests"]:
        if row["place"]:
            to_place.append(("chest", dict(row["place"], prefab=row["prefab"])))
        else:
            unplaceable.append({"prefab": row["prefab"], "role": "chest"})
    report["unplaceable"] = unplaceable

    body = load_body(place, cx, cz, base_y, yaw)
    if body is not None:
        report["audit"] = fixtures.audit(
            [(p["prefab"], p["x"], p["y"], p["z"], p.get("yaw", 0.0))
             for _role, p in to_place], body)
        if report["audit"]["failures"]:
            print(json.dumps(report, indent=1))
            for f in report["audit"]["failures"]:
                print(f"  FIXTURE INSIDE THE STRUCTURE: {f}", file=sys.stderr)
            raise SystemExit("refusing to spawn: a fixture would stand inside the "
                             "building. That is the defect the operator reported as "
                             "'portals in the middle of walls'.")

    if args.plan:
        print(json.dumps(report, indent=1))
        if args.report:
            Path(args.report).write_text(json.dumps(report, indent=1))
        return 0

    with Rcon(timeout=30.0) as rc:
        chest_ids: list[str] = []
        for role, p in to_place:
            oid = spawn(rc, p["prefab"], p["x"], p["y"], p["z"], p.get("yaw", 0.0))
            entry = {"prefab": p["prefab"], "id": oid,
                     "at": [round(p["x"], 2), round(p["y"], 2), round(p["z"], 2)],
                     "offset": p.get("offset"), "clearance_m": p.get("clearance_m")}
            if role == "chest":
                if oid:
                    chest_ids.append(oid)
            elif role == "prop":
                report["props"].append(entry)
            else:
                report["stations"].append(dict(entry, role=role))
        report["owned_by_blueprint"] = plan["carried_by_blueprint"]
        report["not_placed_because_the_blueprint_carries_them"] = plan["skipped"]
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
