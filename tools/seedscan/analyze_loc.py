#!/usr/bin/env python3
"""Score Valheim seeds on LOCATION PLACEMENT, from the JSON dumps LocScan writes.

Pairs with analyze.py: that one measures terrain (WorldGenerator), this one
measures placed content (ZoneSystem) - boss altars, the three traders, the start
temple, and the Deep North content chain.

Distances are measured from the REAL spawn, i.e. the StartTemple position that
ZoneSystem actually chose, not from (0,0).

Usage:
  tools/seedscan/analyze_loc.py /tmp/seedscan/loc [--biomes /tmp/seedscan/out_comm]
                                [--only SEED]... [--csv out.csv]

What this does NOT prove - see README.md.
"""

import argparse
import csv
import glob
import json
import math
import os
import sys

# Location prefab names, taken verbatim from a LocScan dump (176 distinct types
# in Valheim 1.0.12). Nothing here is guessed from lore names.
BOSS_ALTARS = {
    "Eikthyrnir": "Eikthyr",
    "GDKing": "TheElder",
    "Bonemass": "Bonemass",
    "Dragonqueen": "Moder",
    "GoblinKing": "Yagluth",
    "Mistlands_DvergrBossEntrance1": "TheQueen",
    "FaderLocation": "Fader",
    "bosslocation": "Fimbulbringer",  # biome mask confirms DeepNorth
}
TRADERS = {
    "Vendor_BlackForest": "Haldor",
    "Hildir_camp": "Hildir",
    "BogWitch_Camp": "BogWitch",
}
# Deep North / Ashlands content is identified by the ZoneLocation's OWN biome
# mask, not by a hand-written name list. Measured: 17 location types carry
# DeepNorth in their mask, and an exact-match filter on "DeepNorth" isolates the
# 12 DN-only ones from the 5 that are shared with other biomes (Lumbercamp,
# shipsetting, FrozenShip*). This also avoids the mistake a name list invites -
# AncientUpgradeStation sounds like Deep North content but its mask is Mountain,
# so the level-4 Black Forge upgrade lives in mountain caves, not the north.
DN_BIOME = "DeepNorth"
AS_BIOME = "AshLands"

# Named Deep North landmarks that the progression chain actually needs:
# Gammeltroll (Petrified Tissue), Morkhalla, the ice ponds / Winding Tunnels,
# and the northern settlements.
DN_LANDMARKS = ["DN_gammeltrollFrac01", "DN_gammeltrollFrac02", "morkborg",
                "icepond", "thehole01", "NorthVillage", "NorthMemorialPlace"]


def load_dumps(d):
    idx = {}
    p = os.path.join(d, "index.tsv")
    if os.path.exists(p):
        for line in open(p):
            parts = line.rstrip("\n").split("\t")
            if len(parts) == 2:
                idx[parts[1]] = parts[0]
    out = []
    for f in sorted(glob.glob(os.path.join(d, "*.json"))):
        try:
            j = json.load(open(f))
        except Exception as e:
            print("skip %s: %s" % (f, e), file=sys.stderr)
            continue
        j["_file"] = f
        j["_listed_seed"] = idx.get(os.path.basename(f), j.get("seed"))
        out.append(j)
    return out


def dist(ax, az, bx, bz):
    return math.hypot(ax - bx, az - bz)


def measure(d):
    locs = d["locations"]
    temples = [l for l in locs if l["name"] == "StartTemple"]
    if not temples:
        return None
    sx, sz = temples[0]["x"], temples[0]["z"]

    m = {
        "seed": d["seed"],
        "hash": d["hash"],
        "worldGenVersion": d["worldGenVersion"],
        "n_locations": len(locs),
        "spawn_x": round(sx, 1),
        "spawn_z": round(sz, 1),
        "spawn_offset_from_origin": int(round(math.hypot(sx, sz))),
    }

    by_name = {}
    for l in locs:
        by_name.setdefault(l["name"], []).append(l)

    # Boss altars: nearest instance of each, measured from the real spawn.
    for prefab, label in BOSS_ALTARS.items():
        got = by_name.get(prefab, [])
        if not got:
            m["boss_" + label] = None
            m["boss_" + label + "_n"] = 0
            continue
        ds = sorted(((dist(sx, sz, l["x"], l["z"]), l) for l in got), key=lambda t: t[0])
        m["boss_" + label] = int(round(ds[0][0]))
        m["boss_" + label + "_n"] = len(got)
        m["boss_" + label + "_biome"] = ds[0][1].get("biome")
        m["boss_" + label + "_xz"] = "(%d,%d)" % (round(ds[0][1]["x"]), round(ds[0][1]["z"]))

    # Traders.
    for prefab, label in TRADERS.items():
        got = by_name.get(prefab, [])
        if not got:
            m["trader_" + label] = None
            m["trader_" + label + "_n"] = 0
            continue
        ds = sorted(((dist(sx, sz, l["x"], l["z"]), l) for l in got), key=lambda t: t[0])
        m["trader_" + label] = int(round(ds[0][0]))
        m["trader_" + label + "_n"] = len(got)
        m["trader_" + label + "_xz"] = "(%d,%d)" % (round(ds[0][1]["x"]), round(ds[0][1]["z"]))

    # First five bosses: the furthest of them bounds an "all five near spawn" claim.
    early = [m.get("boss_" + b) for b in ("Eikthyr", "TheElder", "Bonemass", "Moder", "Yagluth")]
    m["boss5_max"] = max(early) if all(e is not None for e in early) else None
    m["boss5_sum"] = sum(early) if all(e is not None for e in early) else None
    allt = [m.get("trader_" + t) for t in ("Haldor", "Hildir", "BogWitch")]
    m["trader_max"] = max(allt) if all(t is not None for t in allt) else None

    # Deep North / Ashlands content, classified by the game's own biome mask.
    dn_locs = [l for l in locs if l.get("biome") == DN_BIOME]
    as_locs = [l for l in locs if l.get("biome") == AS_BIOME]
    m["dn_content_n"] = len(dn_locs)
    m["dn_content_types"] = len(set(l["name"] for l in dn_locs))
    m["as_content_n"] = len(as_locs)
    m["as_content_types"] = len(set(l["name"] for l in as_locs))
    for prefab in DN_LANDMARKS:
        m["dn_" + prefab] = len(by_name.get(prefab, []))

    # Nearest content of each, and where. This is the real "how far to the
    # content" number - it beats the terrain-only biome distance because a
    # biome cell with nothing placed on it is not content.
    for tag, group in (("dn", dn_locs), ("as", as_locs)):
        if group:
            ds = sorted(((dist(sx, sz, l["x"], l["z"]), l) for l in group), key=lambda t: t[0])
            m[tag + "_content_near"] = int(round(ds[0][0]))
            m[tag + "_content_near_name"] = ds[0][1]["name"]
            m[tag + "_content_near_xz"] = "(%d,%d)" % (round(ds[0][1]["x"]), round(ds[0][1]["z"]))
            # Density near the frontier: how much content sits within 2 km of
            # the nearest piece, i.e. is the landing a cluster or a lone hut.
            x0, z0 = ds[0][1]["x"], ds[0][1]["z"]
            m[tag + "_within2k_of_landing"] = sum(
                1 for l in group if dist(x0, z0, l["x"], l["z"]) <= 2000)
        else:
            m[tag + "_content_near"] = None
            m[tag + "_content_near_name"] = None
            m[tag + "_content_near_xz"] = None
            m[tag + "_within2k_of_landing"] = 0
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--csv")
    ap.add_argument("--detail", action="append", default=[],
                    help="print the full boss/trader/DN breakdown for this seed")
    args = ap.parse_args()

    rows = []
    for d in load_dumps(args.dir):
        m = measure(d)
        if m:
            rows.append(m)
    if not rows:
        sys.exit("no usable dumps in %s" % args.dir)

    rows.sort(key=lambda r: (r["boss5_max"] if r["boss5_max"] is not None else 1 << 30))

    if args.csv:
        keys = []
        for r in rows:
            for k in r:
                if k not in keys:
                    keys.append(k)
        with open(args.csv, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=keys)
            w.writeheader()
            w.writerows(rows)
        print("wrote %s (%d seeds)" % (args.csv, len(rows)))

    hdr = ("%-12s %8s %6s %6s %6s %6s %6s %7s %7s %6s %6s %6s %6s %6s" %
           ("seed", "spawnOff", "Eik", "Elder", "Bone", "Moder", "Yag",
            "Queen", "Fader", "Hald", "Hild", "BogW", "b5max", "tmax"))
    print("distances in metres from the REAL spawn (StartTemple), not (0,0)")
    print(hdr)
    print("-" * len(hdr))
    for m in rows:
        if args.only and m["seed"] not in args.only:
            continue
        print("%-12s %8s %6s %6s %6s %6s %6s %7s %7s %6s %6s %6s %6s %6s" % (
            m["seed"], m["spawn_offset_from_origin"],
            m.get("boss_Eikthyr"), m.get("boss_TheElder"), m.get("boss_Bonemass"),
            m.get("boss_Moder"), m.get("boss_Yagluth"), m.get("boss_TheQueen"),
            m.get("boss_Fader"), m.get("trader_Haldor"), m.get("trader_Hildir"),
            m.get("trader_BogWitch"), m["boss5_max"], m["trader_max"]))

    for seed in args.detail:
        for m in rows:
            if m["seed"] != seed:
                continue
            print("\n=== %s detail ===" % seed)
            for k in sorted(m):
                print("  %-30s %s" % (k, m[k]))


if __name__ == "__main__":
    main()
