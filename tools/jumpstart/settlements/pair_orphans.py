#!/usr/bin/env python3
"""Pair the hub ends of portal tags whose SITE end already stands alone.

THE DEFECT THIS FIXES, in the operator's own words: a one-way trip. MEASURED
live on Ulfsland at the time of writing:

    findObjects -prefab portal_wood -near 16.45 32.2 -253.55 6 -detailed
      -> Position: (16.45 32.22 -253.55) ... Portal tag: x-harbour
    findObjects -prefab portal_wood -near 1957.84 31.0 553.54 6 -detailed
      -> Position: (1957.84 30.95 553.54) ... Portal tag: x-ferry-e
    findObjects -prefab portal_wood -near -292.5 36.7 213.5 40 -detailed
      -> Found 1 objects: ... Portal tag: u-stenvik

Two tags with exactly ONE end each. Pairing in `TeleportWorld` is exact string
equality against a uniform random draw among equally-tagged UNCONNECTED
portals, so a tag with one end pairs with nothing: the operator walks into the
harbour arch and stays where they are.

WHY THIS IS NOT `build.py portals`. That path is keyed on a settlement site in
`plan.json` -- it solves a SITE end on that site's own pad and then the hub end
for that site's tag. These two tags belong to `Crossings`: their site ends are
already standing on a harbour deck and a ferry terminal, placed by another
agent against bodies this module has no pad record for. So the site end is
LEFT ALONE and only the missing hub end is added.

WHY THE CACHE IS EXTENDED AND NEVER RE-SOLVED. `build.py::hub_spots` caches
because `fixtures.free_spot` is order-dependent: it returns the nearest legal
cell to `prefer` that no box in `taken` occupies, so a second solve with a
different `taken` list hands out DIFFERENT cells. One of the sixteen cached
spots is now a standing arch (u-stenvik, MEASURED above), which means a fresh
16-tag solve would legitimately route around it and could reassign every other
tag -- moving ends that are already in the ledger. So: the sixteen cached
spots are loaded as `taken` VERBATIM, together with every portal the world
actually reports, and only the new tags are solved. The file is appended to.

WHY EACH ARCH GETS ITS OWN GROUND HEIGHT. Same reason as the ring: there is no
portal hall on this seed and there will be no terrain write here, because the
ring's zones include (-5,4) which carries Crossings' S2 bridge declared
`flatten: FORBIDDEN` -- an `objects_remove` at ring radius would delete 45 of
its pieces. Generated ground across a 30-40 m ring varies by metres, so a
single pad height would leave arches sunk or floating. Every XZ is sampled
from the patchscan field for its OWN zone, bilinear at 1 m, and the residual
|ground - placed| is reported.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import build as B  # noqa: E402  the emit path that just paired u-stenvik

# The orphans, and the destination name each hub board must carry. The name is
# the SITE the arch leads to and not the tag: a tag is <=10 characters because
# `TeleportWorld`'s tag field is, and "x-ferry-e" is not a place.
ORPHANS = {
    "x-harbour": {"site": "harbour-temple-south", "name": "Temple Harbour"},
    "x-ferry-e": {"site": "ferry-terminal-eastisle", "name": "East Isle Ferry"},
}

# Sea level. MEASURED as a worldgen constant on this build (`ZoneSystem`'s
# water level, the same 30 m the road survey's ford spans are cut against);
# +1 m on top because an arch whose foot is at the waterline stands in surf.
WATER_LEVEL_M = 30.0

# How much air the arch's AABB must keep from every other solid. 0.5 m is
# `fixtures.free_spot`'s own default margin for a non-indoor fixture, reused
# deliberately: two gates with two margins is how a placement passes one check
# and fails the other.
CLEARANCE_PAD_M = 0.5

# How unlevel the generated ground under one arch's own footprint may be,
# corner to corner. There is no terrain write on the ring, so a 3.83 m wide
# arch across a slope has one leg buried and the other in the air; and
# `portal_wood`'s solid starts at y -0.007, so its origin IS its foot and this
# spread is exactly the visible mismatch.
#
# The number is MEASURED off the ring that already stands rather than chosen:
# over the sixteen reserved cells the footprint spread runs 0.621 m
# (u-wtpeak, the most level) to 2.467 m (u-treewest), median 1.610
# (u-stenvik). Across the 2,246 land cells of the 12-30 m annulus the median
# is 1.626 m, so this ground is simply not flat. The cap is therefore the
# BEST STANDING ARCH: a new arch must be at least as level as the most level
# of the sixteen. 44 annulus cells qualify, 15 of them clear of the reserved
# cells -- so it is a real constraint with real room, not a formality.
FOOTPRINT_LEVEL_M = 0.621

# The gap that makes an arch USABLE rather than merely legal. A guidepost
# stands beside the arch (`waypoints.portal_guidepost` walks the post out
# along the arch's own width axis until its boxes clear), and a player walks
# through the arch to reach it, so a 0.08 m gap to a Beech is a portal in a
# hedge. 1.0 m is TASTE and is applied as a PREFERENCE TIER, not a veto: a
# cell that cannot reach it still counts, it just loses to any cell that can.
COMFORT_CLEARANCE_M = 1.0


def fixtures_gap_to_world(piece, world_boxes: list) -> float:
    """Smallest 3-D gap between one guidepost piece and any live solid.

    `waypoints.Piece` carries prefab, position and yaw, so the box is built by
    the SAME `fixtures.fixture_box` the placement gate and `fixtures.audit()`
    use -- one geometry, three callers.
    """
    import fixtures
    box = fixtures.fixture_box(piece.prefab, piece.x, piece.y, piece.z,
                               piece.yaw)
    return min([fixtures.gap(box, t) for t in world_boxes] or [99.0])


def live_site_end(srv, tag: str) -> dict:
    """WHERE the standing site end is, read off the world rather than the plan.

    Prefab-scoped and radius-bounded -- `findObjects` refuses without both, and
    an unbounded listing is the question whose answer has wedged this server.
    The seed position comes from the ledger record that placed it; this call is
    what proves the arch is still there and what its tag reads back as.
    """
    import re
    rows = []
    path = B.JUMPSTART / "ledger/runs/Ulfsland/ledger.jsonl"
    for line in path.read_text().splitlines():
        rec = json.loads(line)
        if rec["op"] == "portal" and rec["params"].get("tag") == tag:
            rows.append(rec)
    if not rows:
        raise SystemExit(f"{tag}: no `portal` record in the ledger, so there is "
                         f"no site end to pair with. This tool pairs an "
                         f"EXISTING orphan; it does not invent one.")
    if len(rows) > 1:
        raise SystemExit(f"{tag}: {len(rows)} portal records already. That tag "
                         f"is not an orphan and a third end would make the "
                         f"pairing draw non-deterministic.")
    x, y, z = rows[0]["params"]["pos"]
    reply = srv.command(f"findObjects -prefab {B.PORTAL_PREFAB} -near "
                        f"{x:.2f} {y:.2f} {z:.2f} 6 -detailed")
    found = re.search(r"Portal tag: (\S*)", reply)
    return {"pos": [x, y, z], "yaw_deg": rows[0]["params"]["yaw_deg"],
            "ledger_seq": rows[0]["seq"], "actor": rows[0]["actor"],
            "tag_readback": found.group(1) if found else None,
            "standing": f"Position: ({x:.2f}" in reply.replace("  ", " ")
                        or bool(found),
            "cmd": f"findObjects -prefab {B.PORTAL_PREFAB} -near {x:.2f} "
                   f"{y:.2f} {z:.2f} 6 -detailed"}


# HOW MANY `findObjects -detailed` ROWS ONE QUERY MAY ASK FOR.
#
# MEASURED THE HARD WAY, tonight, on this server. `findObjects -prefab
# Beech_small1 -near -292.5 0 213.5 40 -detailed` answered `Found 39 objects:`
# plus 39 lines of ~110 characters -- about 4.4 KB. The server RAN it (the
# container log carries the full answer and `Command completed: findObjects`
# at 23:01:09), and `rcon.py`'s own docstring says why the CLIENT still lost:
# `RconPeer.TryReceive` reads what is available into ONE 4096-byte buffer,
# parses exactly one packet and clears it, so a reply that does not fit is
# parsed against a zero-padded tail and the stream desynchronises. Every
# subsequent `connect()` then timed out for both me and RoadNet.
#
# It cost more than the socket: the same answer went to the console sink, and
# supervisord/syslogd deadlocked on it -- `log_sink_state` returned
# `unix_wait_for_peer` + `pipe_write`, the documented stall signature, the
# game's main thread stopped emitting entirely, and `docker restart` timed out
# at 180 s because the process could not be signalled. `recover_log_sink`
# drained it in 20 s without touching the game.
#
# 18 rows is about 2.0 KB, half the buffer, and it is enforced BY SPLITTING
# THE QUERY rather than by hoping: `detailed_positions` counts first and
# quarters the disc until every leaf is under the cap.
MAX_DETAILED_ROWS = 18

# Props that cannot obstruct an arch: `Pickable_*` is a flower, a mushroom, a
# loose flint or a branch -- picked up by walking over it. Counting them is
# free; querying their positions is 44 rows of reply for something that does
# not occupy space. They stay in the census and out of the collision set, and
# that is a measurement about what they ARE, not a convenience.


def detailed_positions(srv, prefab: str, cx: float, cz: float,
                       radius_m: float, depth: int = 0) -> list[tuple]:
    """Positions of one prefab inside a disc, in replies that FIT.

    Counts with `objects_count` (two lines of answer) and only then asks for
    rows; over `MAX_DETAILED_ROWS` the disc is quartered -- four sub-discs at
    half-offset with radius 0.71 r, which covers the parent disc including its
    corners -- and the results are de-duplicated on rounded position.

    The recursion is bounded: eight levels takes a 40 m disc to 0.16 m, and a
    leaf that STILL reports more rows than the cap is a real refusal rather
    than a query to send anyway.
    """
    count, _ = srv.count(prefab, cx, cz, radius_m)
    if count == 0:
        return []
    if count <= MAX_DETAILED_ROWS:
        return B.live_positions(srv, prefab, cx, 0.0, cz, radius_m)
    if depth >= 8:
        raise SystemExit(
            f"{prefab}: {count} instances still inside a {radius_m:.2f} m disc "
            f"after {depth} splits. A reply that large desynchronises the RCON "
            f"stream and deadlocks the console sink (MEASURED tonight), so "
            f"this refuses rather than sends it.")
    out: dict[tuple, tuple] = {}
    half = radius_m / 2.0
    sub = radius_m * 0.7072
    for dx in (-half, half):
        for dz in (-half, half):
            for p in detailed_positions(srv, prefab, cx + dx, cz + dz, sub,
                                        depth + 1):
                out[(round(p[0], 2), round(p[1], 2), round(p[2], 2))] = p
    return list(out.values())


def live_boxes(srv, cx: float, cz: float, radius_m: float) -> dict:
    """Every object standing near the ring, as boxes, MEASURED.

    An `id=*` census names the prefabs (the one place a star is the point, and
    it is inside the 40 m cap `Server.count` enforces), then
    `detailed_positions` reads each family's positions in replies that fit the
    transport. `fixture_box` turns each into the same AABB the placement gate
    uses, so a candidate cell cannot be cleared by one measure and refused by
    another.
    """
    import fixtures
    total, by_prefab = srv.count("*", cx, cz, min(40.0, radius_m), ignore="")
    boxes, rows, unmeasured, skipped = [], [], [], {}
    import base_geometry
    g = base_geometry.geometry()
    for prefab in sorted(by_prefab):
        if prefab.startswith("_"):
            continue
        if "Pickable_" in prefab:
            skipped[prefab] = by_prefab[prefab]
            continue
        for px, py, pz in detailed_positions(srv, prefab, cx, cz,
                                             min(40.0, radius_m)):
            if g._prefabs.get(prefab) is None:
                unmeasured.append(prefab)
            # Yaw 0: `findObjects` gives vegetation a rotation we do not feed
            # back, and the axis-aligned box of a trunk at yaw 0 is a
            # CONSERVATIVE stand-in -- for a near-square footprint it is the
            # widest form the box takes.
            boxes.append(fixtures.fixture_box(prefab, px, py, pz, 0.0))
            rows.append({"prefab": prefab, "pos": [px, py, pz]})
    return {"census_total": total, "census": by_prefab, "boxes": boxes,
            "rows": rows, "unmeasured": sorted(set(unmeasured)),
            "skipped_pickables": skipped,
            "cmd": f"objects_count id=* pos={cx:.2f},{cz:.2f} "
                   f"max={min(40.0, radius_m):.2f}"}


def phantom_capacity(hub: dict, cache: dict) -> dict:
    """WHY the two new ends are not solved by `build.py::hub_spots`.

    MEASURED, and it is the kind of check this project keeps finding: that
    function solves the ring against the 1,043-solid body of the
    `sandbox-portal-hub` BLUEPRINT and its interior mask. On this seed THERE
    IS NO HALL -- an `objects_count id=*` over 40 m of the ring centre answers
    261 objects of which the only structures are Crossings' 45-piece S2
    bridge, one arch and one guidepost. So the mask's `blocked` cells and the
    body's solids describe a building that does not stand, and they are what
    caps the ring: of the 1,225 one-metre cells of the notional pad, 571 are
    free of the sixteen reserved arches, 245 are unblocked by the phantom
    mask, and only 9 clear the phantom's own solids -- which is why the
    seventeenth tag solves and the eighteenth refuses.

    Recomputed here rather than quoted, so the number in the report is this
    run's.
    """
    import fixtures
    place = B.hub_placement()
    w, d = B.FL.pad_extent(place)
    _fx, _objs, body = B.body_of(place, (hub["x"], hub["pad_y"], hub["z"]),
                                 hub["yaw"])
    mask, index = body.interior(), body.index()
    taken = [fixtures.fixture_box(B.PORTAL_PREFAB, s["x"],
                                  s.get("pad_y_unused", s["y"]), s["z"],
                                  s["yaw"])
             for k, s in cache.items() if k != "_hub"]
    steps = int(max(w, d) / 2)
    free_t = free_mask = free_body = 0
    for ix in range(-steps, steps + 1):
        for iz in range(-steps, steps + 1):
            x, z = hub["x"] + ix, hub["z"] + iz
            box = fixtures.fixture_box(B.PORTAL_PREFAB, x, hub["pad_y"], z,
                                       hub["yaw"], pad=0.5)
            if any(all(v > 0 for v in fixtures.overlap(box, t)) for t in taken):
                continue
            free_t += 1
            if mask.at(x, z) in ("blocked", "unreachable"):
                continue
            free_mask += 1
            if any(all(v > 0 for v in fixtures.overlap(box, s))
                   for s in index.near(box)):
                continue
            free_body += 1
    return {"pad_cells": (2 * steps + 1) ** 2, "free_of_arches": free_t,
            "unblocked_by_phantom_mask": free_mask,
            "clear_of_phantom_solids": free_body,
            "phantom_body_solids": len(_objs),
            "method": ("MEASURED this run over the notional pad's 1 m "
                       "lattice; the body is the sandbox-portal-hub "
                       "blueprint, which does not stand in this world"),
            "tool": "tools/jumpstart/settlements/pair_orphans.py::"
                    "phantom_capacity"}


def solve_new_spots(srv, tags: list[str], inner_m: float = 12.0,
                    outer_m: float = 40.0) -> dict:
    """One ring position per NEW tag, solved against THE WORLD.

    Not against the phantom hall: see `phantom_capacity`. The constraints here
    are the ones a player can walk into --

      * the arch's own padded AABB clears every object MEASURED live within
        40 m (Crossings' bridge above all, plus 168 trees, bushes and rocks
        that grew when the ring's zones were generated),
      * it clears the sixteen reserved cells and every standing arch,
      * it stands on land, not water (`ground_y` above the 30 m sea level),
      * the generated ground under its own footprint is LEVEL ENOUGH: the
        corner-to-corner spread is measured and capped, because an arch on a
        0.9 m slope is half-sunk on one leg and that is the operator's
        original complaint,

    and among the cells that pass, the one NEAREST the ring centre wins, so
    the two new arches join the ring rather than wander off into the trees.

    The annulus runs 12-40 m, and 40 m is not a taste number: it is the
    largest disc about (-292.5, 213.5) that stays inside the FOUR ZONES the
    ring generated -- (-5,3) (-5,4) (-4,3) (-4,4), i.e. x in [-352, -224] and
    z in [160, 288]. A cell outside them is on UNGENERATED ground, and
    placing an arch there plants that zone's whole vegetation set the first
    time somebody walks up, against the collider at that instant. The zone of
    every candidate is checked rather than inferred from the radius.

    MEASURED at these bounds: 27 of the annulus's land cells are level enough
    AND clear of the sixteen reserved cells, before the live world is
    consulted. At the original 30 m outer bound there was exactly ONE, which
    the first tag consumed -- which is why the second refused.
    """
    import fixtures
    import tcdata
    cache = json.loads(B.HUB_SPOTS.read_text())
    hub = cache["_hub"]
    yaw = hub["yaw"]
    place = B.hub_placement()
    w, d = B.FL.pad_extent(place)

    taken = [fixtures.fixture_box(B.PORTAL_PREFAB, s["x"],
                                  s.get("pad_y_unused", s["y"]), s["z"],
                                  s["yaw"])
             for k, s in cache.items() if k != "_hub"]
    world = live_boxes(srv, hub["x"], hub["z"], 40.0)

    zones = sorted(set(B.zone_list(hub["x"], hub["z"], outer_m + 6.0)))
    patches = B.FL.run_patchscan(zones, B.SEED,
                                 B.FL.SCRATCH / "patch_portalring.bin")

    def ground(x: float, z: float) -> float | None:
        zx, zz = tcdata.zone_of(x, z)
        patch = patches.get(f"z_{zx}_{zz}")
        return None if patch is None else patch.height_at_world(x, z)

    new: dict[str, dict] = {}
    generated = {tuple(z) for z in hub["zones"]}
    rejected = {"off_field": 0, "water": 0, "slope": 0, "arch_clash": 0,
                "world_clash": 0, "out_of_annulus": 0, "ungenerated_zone": 0}
    for tag in tags:
        best = None
        span = int(outer_m) + 1
        for ix in range(-span, span + 1):
            for iz in range(-span, span + 1):
                x, z = hub["x"] + ix, hub["z"] + iz
                r = math.hypot(ix, iz)
                if not (inner_m <= r <= outer_m):
                    rejected["out_of_annulus"] += 1
                    continue
                if tcdata.zone_of(x, z) not in generated:
                    rejected["ungenerated_zone"] += 1
                    continue
                gy = ground(x, z)
                if gy is None:
                    rejected["off_field"] += 1
                    continue
                if gy <= WATER_LEVEL_M + 1.0:
                    rejected["water"] += 1
                    continue
                box = fixtures.fixture_box(B.PORTAL_PREFAB, x, gy, z, yaw,
                                           pad=CLEARANCE_PAD_M)
                corners = [ground(box[0], box[4]), ground(box[1], box[4]),
                           ground(box[0], box[5]), ground(box[1], box[5])]
                if any(c is None for c in corners):
                    rejected["off_field"] += 1
                    continue
                spread = max(corners) - min(corners)
                if spread > FOOTPRINT_LEVEL_M:
                    rejected["slope"] += 1
                    continue
                if any(all(v > 0 for v in fixtures.overlap(box, t))
                       for t in taken):
                    rejected["arch_clash"] += 1
                    continue
                if any(all(v > 0 for v in fixtures.overlap(box, t))
                       for t in world["boxes"]):
                    rejected["world_clash"] += 1
                    continue
                clear = min([fixtures.gap(
                    fixtures.fixture_box(B.PORTAL_PREFAB, x, gy, z, yaw), t)
                    for t in taken + world["boxes"]] or [99.0])
                # NEAREST IS NOT THE ONLY THING THAT MATTERS. The first solve
                # at these bounds returned a cell whose measured gap to the
                # nearest solid was 0.081 m: legal (the padded boxes do not
                # intersect) and still wrong, because a guidepost has to stand
                # beside the arch and a player has to walk through it. So
                # cells with a COMFORTABLE gap are preferred as a tier, and
                # only within a tier does nearest-to-centre decide.
                key = (0 if clear >= COMFORT_CLEARANCE_M else 1,
                       round(r, 3), -round(clear, 3))
                if best is None or key < best[0]:
                    best = (key, {"x": float(x), "y": round(gy, 3),
                                  "z": float(z), "yaw": yaw,
                                  "ground_y": round(gy, 3),
                                  "pad_y_unused": hub["pad_y"],
                                  "radius_from_centre_m": round(r, 2),
                                  "clearance_m": round(clear, 3),
                                  "footprint_level_spread_m": round(spread, 3)})
        if best is None:
            raise SystemExit(
                f"no legal ring position for {tag!r} in the {inner_m:g}-"
                f"{outer_m:g} m annulus. Rejections: {rejected}. A real "
                f"refusal -- it needs a wider annulus or a second ring, not "
                f"an arch inside the bridge.")
        spot = best[1]
        new[tag] = spot
        taken.append(fixtures.fixture_box(B.PORTAL_PREFAB, spot["x"],
                                          spot["y"], spot["z"], yaw,
                                          pad=CLEARANCE_PAD_M))
    return {"hub": hub, "new": new, "world": world,
            "live_arches": sum(1 for r in world["rows"]
                               if r["prefab"] == B.PORTAL_PREFAB),
            "cached_cells": len(cache) - 1, "zones": zones,
            "patches": patches, "rejected": rejected,
            "phantom": phantom_capacity(hub, cache)}


def ring_ground_residuals(patches, spots: dict) -> list[dict]:
    """|ground - placed| for every arch and board now standing on the ring.

    The number the brief asks for: a portal floating 40 cm is the operator's
    original complaint in miniature, and it is measured per piece because the
    ring is on unlevelled ground.
    """
    import tcdata
    out = []
    for tag, spot in spots.items():
        if tag == "_hub":
            continue
        zx, zz = tcdata.zone_of(spot["x"], spot["z"])
        patch = patches.get(f"z_{zx}_{zz}")
        if patch is None:
            continue
        g = patch.height_at_world(spot["x"], spot["z"])
        out.append({"tag": tag, "xz": [spot["x"], spot["z"]],
                    "ground_y": round(g, 3), "placed_y": round(spot["y"], 3),
                    "residual_m": round(abs(g - spot["y"]), 3)})
    return sorted(out, key=lambda r: -r["residual_m"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("op", choices=["solve", "apply"])
    ap.add_argument("--tags", default=",".join(ORPHANS))
    a = ap.parse_args()
    tags = [t for t in a.tags.split(",") if t]

    from live import LiveBuilder  # noqa: E402

    with LiveBuilder(actor="SiteFinish") as b:
        ends = {t: live_site_end(b.srv, t) for t in tags}
        for t, e in ends.items():
            if e["tag_readback"] != t:
                raise SystemExit(
                    f"{t}: the standing site end at {e['pos']} reads back "
                    f"{e['tag_readback']!r}. Pairing is exact string equality, "
                    f"so a hub end with the intended tag would pair with "
                    f"nothing. Fix the site end first.")
        sol = solve_new_spots(b.srv, tags)
        hub = sol["hub"]

        # The ring's zones, MEASURED rather than assumed generated. Placing an
        # arch in an ungenerated zone plants that zone's whole vegetation set
        # the first time somebody walks there, against the collider at that
        # instant -- a Beech growing between two arches.
        probes = {f"{zx},{zz}": B.zone_probe(b.srv, zx, zz)
                  for zx, zz in [tuple(z) for z in hub["zones"]]}
        ungenerated = [k for k, v in probes.items() if v["count"] < 1]

        report = {"tags": tags, "site_ends": ends, "zone_probes": probes,
                  "ungenerated_zones": ungenerated,
                  "ring": {"cached_cells": sol["cached_cells"],
                           "live_arches": sol["live_arches"]},
                  "spots": sol["new"]}
        if a.op == "solve":
            print(json.dumps(report, indent=1, default=str))
            return 0

        if ungenerated:
            res = b.emit("zones_generate", params={
                "pos": [hub["x"], hub["z"]],
                "max_m": round(math.hypot(32.0, 32.0) + 34.1, 2),
                "zones": [list(z) for z in [tuple(z) for z in hub["zones"]]],
                "role": "spawn_portal", "site_id": "portal-ring",
                "flatten": "FORBIDDEN",
                "flatten_reason": (
                    "the ring shares zone (-5,4) with Crossings' S2 bridge, "
                    "itself flatten=FORBIDDEN over water; the ring stands on "
                    "generated ground with each piece's own measured height")},
                wire=[f"zones_generate pos={hub['x']:.1f},{hub['z']:.1f} "
                      f"max={math.hypot(32.0, 32.0) + 34.1:.1f}"],
                requires={"mods": ["UpgradeWorld"], "prefabs": [],
                          "blobs": []},
                expect={"zone_ctrl": len(hub["zones"])},
                meta={"why": "ring zones for the two orphan hub ends",
                      "ring": True})
            report["seqs_zones_generate"] = res["seq"]

        W = B._waypoints()
        report["placed"] = {}
        for tag in tags:
            spot = sol["new"][tag]
            site = ORPHANS[tag]["site"]
            name = ORPHANS[tag]["name"]
            rec = B.portal_record(tag, spot["x"], spot["y"], spot["z"],
                                  spot["yaw"], site, "hub",
                                  f"{site} site end (already standing, "
                                  f"ledger seq {ends[tag]['ledger_seq']})",
                                  spot)
            rec["meta"]["pairs_orphan"] = True
            rec["meta"]["site_end_pos"] = ends[tag]["pos"]
            rec["meta"]["ground_y"] = spot["ground_y"]
            res = b.emit("portal", params=rec["params"], wire=rec["wire"],
                         requires=rec["requires"], expect=rec["expect"],
                         meta=rec["meta"])
            entry = {"seq": res["seq"], "readback": res["checks"],
                     "pos": [spot["x"], spot["y"], spot["z"]],
                     "yaw": spot["yaw"], "ground_y": spot["ground_y"],
                     "residual_m": round(abs(spot["ground_y"] - spot["y"]), 3)}

            bearing, compass, metres = W.bearing_and_range(
                (spot["x"], spot["z"]),
                (ends[tag]["pos"][0], ends[tag]["pos"][2]))
            board = W.pointer_text(name, compass, metres)
            # WHICH SIDE THE BOARD STANDS ON IS MEASURED, NOT DEFAULTED.
            # `portal_guidepost` walks the post outward until it clears the
            # ARCH -- that is all it knows about. Out here the arch stands in
            # unmanaged forest, so the post can clear the arch and still be
            # inside a Beech. Both sides are built, each is tested against
            # every live solid measured this run, and the one with the larger
            # worst-case gap wins. If neither clears, that is reported rather
            # than planted.
            sides = {}
            for side in ("left", "right"):
                cand = W.portal_guidepost(spot["x"], spot["y"], spot["z"],
                                          spot["yaw"], board, side=side)
                worst = min(
                    [fixtures_gap_to_world(p, sol["world"]["boxes"])
                     for p in cand] or [99.0])
                sides[side] = {"pieces": cand, "worst_gap_m": round(worst, 3)}
            side = max(sides, key=lambda s: sides[s]["worst_gap_m"])
            pieces = sides[side]["pieces"]
            entry["sign_side"] = side
            entry["sign_worst_gap_m"] = sides[side]["worst_gap_m"]
            entry["sign_side_gaps_m"] = {s: v["worst_gap_m"]
                                         for s, v in sides.items()}
            if sides[side]["worst_gap_m"] <= 0.0:
                raise SystemExit(
                    f"{tag}: the guidepost intersects a live solid on BOTH "
                    f"sides of the arch (gaps {entry['sign_side_gaps_m']}). "
                    f"The arch itself is clear; the board is not. Reported "
                    f"rather than planted inside a tree.")
            entry["sign_seqs"] = []
            for srec in B.sign_records(pieces, site, tag):
                sres = b.emit(srec["op"], params=srec["params"],
                              wire=srec["wire"], requires=srec["requires"],
                              expect=srec["expect"], meta=srec["meta"])
                entry["sign_seqs"].append(sres["seq"])
            entry["board"] = W.pointer_text(name, compass, metres)
            entry["bearing_deg"] = round(bearing, 1)
            entry["range_m"] = round(metres, 1)
            report["placed"][tag] = entry

        b.srv.command("save")

        # BOTH ENDS, read back off the ZDO. A one-ended tag is the defect this
        # whole tool exists to close, so the proof is a count of ends per tag.
        import re
        report["pairing"] = {}
        for tag in tags:
            spot = sol["new"][tag]
            se = ends[tag]["pos"]
            got = []
            for label, (x, y, z) in (("hub", (spot["x"], spot["y"], spot["z"])),
                                     ("site", tuple(se))):
                reply = b.srv.command(
                    f"findObjects -prefab {B.PORTAL_PREFAB} -near {x:.2f} "
                    f"{y:.2f} {z:.2f} 6 -detailed")
                tags_found = re.findall(r"Portal tag: (\S*)", reply)
                got.append({"end": label, "tags": tags_found,
                            "ok": tags_found.count(tag) == 1})
            report["pairing"][tag] = {
                "ends": got,
                "paired": all(g["ok"] for g in got) and len(got) == 2}
        report["ring_residuals"] = ring_ground_residuals(
            sol["patches"], {**{k: v for k, v in
                                json.loads(B.HUB_SPOTS.read_text()).items()
                                if k != "_hub"}, **sol["new"]})

        # Extend the cache so a later `build.py portals` run cannot hand one
        # of these two cells to a settlement tag.
        cache = json.loads(B.HUB_SPOTS.read_text())
        cache.update(sol["new"])
        B.HUB_SPOTS.write_text(json.dumps(cache, indent=1))
        report["close"] = b.close()
    print(json.dumps(report, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
