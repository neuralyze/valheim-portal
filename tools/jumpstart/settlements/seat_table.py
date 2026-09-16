#!/usr/bin/env python3
"""HOW FAR IS EVERY PLACED PORTAL AND SIGN FROM THE GROUND A PLAYER STANDS ON?

One question, one instrument, every object this project placed that a player
walks up to: the 36 portal ends and every `waypoint_sign` post and board.

THE INSTRUMENT, and it is named because the whole failure this answers is
agents measuring something other than what a player sees:

    applied ground(x, z) = PatchScan generated height at (x, z)
                         + ribbon.delta_at(live compilers, x, z)

`PatchScan` is `WorldGenerator.GetHeight` sampled on the same 1 m lattice every
flatten's arithmetic is relative to, bilinear between samples.
`ribbon.delta_at` is the bilinear blend of the four written samples around the
point -- which is how `Heightmap` renders the mesh, so an object 0.9 m outside a
written edge still settles a little and one 1.1 m outside does not move at all.
The live compilers are rebuilt from the ledger's OWN blob store: per zone, every
`terrain_write` entry in FILE ORDER, unioned per sample index, later claim
winning -- exactly what the write path does, because each write re-reads the
earlier blob and unions it.

`gap` is `recorded Y - applied ground Y`.  Positive floats, negative buries.

`written_here` is the measurable version of "is it inside the flattened
footprint": the applied delta at the object's own XZ is non-zero, i.e. the
terrain under THIS OBJECT was actually written -- not a rectangle somebody
declared.  `pad_target_y` is the height the site's `terrain_write` record says
its pad was levelled to, so a row with `written_here: false` standing next to a
pad whose `target_y` is metres away is an object OUTSIDE its own pad, on natural
ground, which is cause (b); a row with `written_here: true` whose applied ground
differs from `pad_target_y` is cause (a), a pad that did not land where the
record says.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
for _p in (JUMPSTART, JUMPSTART / "terraform", JUMPSTART / "roads",
           JUMPSTART / "ledger"):
    sys.path.insert(0, str(_p))

import tcdata            # noqa: E402
import flatten as FL     # noqa: E402
import ribbon            # noqa: E402

WORLD = "Ulfsland"
SEED = "Pirate68"
ROOT = JUMPSTART / "ledger/runs" / WORLD
LEDGER = ROOT / "ledger.jsonl"
BLOBS = ROOT / "blobs"


def records() -> list[dict]:
    return [json.loads(l) for l in LEDGER.read_text().splitlines()]


def retired(recs) -> set[int]:
    gone: set[int] = set()
    for r in recs:
        if r["op"] == "retire":
            gone.update(int(s) for s in r["params"].get("retires", []))
    return gone


def objects(recs) -> list[dict]:
    """Every LIVE portal end and waypoint sign, with the record that placed it."""
    gone = retired(recs)
    out = []
    for idx, r in enumerate(recs):
        if r["seq"] in gone:
            continue
        p = r["params"]
        if r["op"] == "portal":
            kind = "portal"
        elif r["op"] == "spawn" and p.get("role") == "waypoint_sign":
            kind = "waypoint_sign"
        else:
            continue
        x, y, z = p["pos"]
        out.append({"kind": kind, "line": idx + 1, "seq": r["seq"],
                    "actor": r["actor"], "prefab": p["prefab"],
                    "tag": p.get("tag"), "site_id": p.get("site_id"),
                    "text": (p.get("zdo_strings") or {}).get("text"),
                    "x": float(x), "y": float(y), "z": float(z)})
    return out


def pad_targets(recs) -> dict[str, dict]:
    """Each site's declared pad height, from its own `terrain_write`."""
    gone = retired(recs)
    out: dict[str, dict] = {}
    for idx, r in enumerate(recs):
        if r["op"] != "terrain_write" or r["seq"] in gone:
            continue
        p = r["params"]
        key = p.get("site_id") or p.get("name")
        if p.get("target_y") is None:
            continue
        out[key] = {"target_y": float(p["target_y"]), "seq": r["seq"],
                    "line": idx + 1, "name": p.get("name"),
                    "pad_w": p.get("pad_w"), "pad_d": p.get("pad_d")}
    return out


def live_comps(recs, zones: set[tuple[int, int]]):
    comps: dict[tuple[int, int], tcdata.Compiler] = {}
    prov: dict[tuple[int, int], list[int]] = {}
    for r in recs:
        if r["op"] != "terrain_write":
            continue
        for e in r["params"]["entries"]:
            z = (int(e["zone"][0]), int(e["zone"][1]))
            if z not in zones:
                continue
            comp = comps.setdefault(z, tcdata.Compiler(zone_x=z[0], zone_z=z[1]))
            old = tcdata.parse((BLOBS / e["blob_sha256"]).read_bytes())
            for i, (lvl, sm) in old["heights"].items():
                comp.modified_height[i] = True
                comp.level_delta[i] = lvl
                comp.smooth_delta[i] = sm
            prov.setdefault(z, []).append(r["seq"])
    return comps, prov


def main() -> int:
    recs = records()
    objs = objects(recs)
    pads = pad_targets(recs)
    zones = {tcdata.zone_of(o["x"], o["z"]) for o in objs}
    patches = FL.run_patchscan(sorted(zones), SEED,
                               FL.SCRATCH / "patch_seat_table.bin")
    comps, prov = live_comps(recs, zones)
    rows = []
    for o in objs:
        zx, zz = tcdata.zone_of(o["x"], o["z"])
        patch = patches[f"z_{zx}_{zz}"]
        gen = patch.height_at_world(o["x"], o["z"])
        delta = ribbon.delta_at(comps, o["x"], o["z"])
        applied = gen + delta
        pad = pads.get(o["site_id"] or "")
        rows.append({**o, "zone": [zx, zz],
                     "generated_y": round(gen, 3),
                     "applied_delta_m": round(delta, 3),
                     "applied_ground_y": round(applied, 3),
                     "gap_m": round(o["y"] - applied, 3),
                     "written_here": delta != 0.0,
                     "zone_written_by_seq": prov.get((zx, zz), []),
                     "pad_target_y": None if pad is None else pad["target_y"],
                     "applied_vs_pad_target_m":
                         None if pad is None else round(applied - pad["target_y"], 3)})
    rows.sort(key=lambda r: (-abs(r["gap_m"]), r["tag"] or "", r["prefab"]))
    print(json.dumps({
        "instrument": "PatchScan generated height + ribbon.delta_at over the "
                      "live _TerrainCompiler blobs rebuilt from the ledger's "
                      "blob store, probed at each object's OWN XZ",
        "objects": len(rows),
        "portals": sum(1 for r in rows if r["kind"] == "portal"),
        "signs": sum(1 for r in rows if r["kind"] == "waypoint_sign"),
        "over_tolerance_0_10m": sum(1 for r in rows if abs(r["gap_m"]) > 0.10),
        "rows": rows}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
