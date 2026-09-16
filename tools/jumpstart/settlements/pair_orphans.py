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


def solve_new_spots(srv, tags: list[str]) -> dict:
    """One legal ring position per NEW tag, with the sixteen cached ones and
    every live arch held as `taken`."""
    import fixtures
    cache = json.loads(B.HUB_SPOTS.read_text())
    hub = cache["_hub"]
    place = B.hub_placement()
    yaw = hub["yaw"]
    w, d = B.FL.pad_extent(place)
    fx, _objs, body = B.body_of(place, (hub["x"], hub["pad_y"], hub["z"]), yaw)
    index = body.index()
    mask = body.interior()

    taken = []
    for key, spot in cache.items():
        if key == "_hub":
            continue
        # The cached Y is the piece's own ground height; the box is solved at
        # the pad plane the search itself works in, which is what `hub_spots`
        # did when it reserved these cells.
        taken.append(fixtures.fixture_box(
            B.PORTAL_PREFAB, spot["x"], spot.get("pad_y_unused", spot["y"]),
            spot["z"], spot["yaw"]))
    live = B.live_positions(srv, B.PORTAL_PREFAB, hub["x"], hub["pad_y"],
                            hub["z"], min(30.0, max(w, d)))
    for px, py, pz in live:
        taken.append(fixtures.fixture_box(B.PORTAL_PREFAB, px, py, pz, yaw))

    new: dict[str, dict] = {}
    for tag in tags:
        spot = fixtures.free_spot(
            B.PORTAL_PREFAB, body, index, (hub["x"], hub["z"]), hub["pad_y"],
            max(w, d) / 2, prefer=(hub["x"], hub["z"]), yaw_deg=yaw,
            taken=taken, mask=mask)
        if spot is None:
            raise SystemExit(
                f"no legal ring position left for {tag!r}: "
                f"{len(cache) - 1} cached cell(s) plus {len(live)} live "
                f"arch(es) occupy the ring. A real refusal -- it needs a "
                f"second ring, not a portal inside a wall.")
        taken.append(fixtures.fixture_box(B.PORTAL_PREFAB, spot["x"],
                                          spot["y"], spot["z"], spot["yaw"]))
        new[tag] = spot

    # Per-piece ground, from the SAME generated field the ring used.
    zones = sorted(set(B.zone_list(hub["x"], hub["z"], max(w, d) / 2 + 2.0)))
    patches = B.FL.run_patchscan(zones, B.SEED,
                                 B.FL.SCRATCH / "patch_portalring.bin")
    import tcdata
    for tag, spot in new.items():
        zx, zz = tcdata.zone_of(spot["x"], spot["z"])
        patch = patches[f"z_{zx}_{zz}"]
        spot["ground_y"] = round(patch.height_at_world(spot["x"], spot["z"]), 3)
        spot["pad_y_unused"] = spot["y"]
        spot["y"] = spot["ground_y"]
        spot["yaw"] = yaw
    return {"hub": hub, "new": new, "live_arches": len(live),
            "cached_cells": len(cache) - 1, "zones": zones, "patches": patches}


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
            pieces = W.portal_guidepost(spot["x"], spot["y"], spot["z"],
                                        spot["yaw"],
                                        W.pointer_text(name, compass, metres))
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
