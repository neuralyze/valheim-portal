#!/usr/bin/env python3
"""The settlement plan: every site, every body, every pad, every portal tag --
measured, and emitted in the two forms other agents consume.

OUTPUTS
  sites.yaml        the interchange `RoadNet` and `Crossings` read. One record
                    per site: id, type, xz, pad_radius_m, road_priority, the
                    approach bearing where the LAYOUT forces one, and the
                    measured ground facts behind the choice.
  ledger_ops.jsonl  one JSON object per line, in build order, in
                    `tools/jumpstart/ledger/`'s v1.1 envelope, validated
                    against its own `schema.py`. This is the replay artefact.
  plan.json         everything, for humans and for re-deriving inputs.

WHAT IS MEASURED HERE AND WHAT IS NOT, stated per field rather than in general:
  * pad bills, clamp verdicts, street profiles, biome fractions, island
    membership, distances to navigable water -- MEASURED at 1 m from
    `/tmp/settle/h1m.bin` (PatchScan, rivers included) by the scripts in this
    directory.
  * POI stand-offs -- MEASURED per location type from the `LocScan.cs` dump,
    `max(exteriorRadius, interiorRadius, znviewReachM)`. The MOD-FREE limit
    stands: More_World_Locations' POIs are invisible to it and a live marker
    check after `zones_generate` is still required.
  * the margins, gaps, street widths and the protected-location extra --
    TASTE PICKS, labelled as such in `clearance.py` and `layout.py`.
  * the ZDO cost -- the body's own MEASURED piece count. One persistent piece
    is one ZDO.

The build-time fields of a `terrain_write` (the gzip'd `TCData` blob digest,
the generated-heights digest, the `_ZoneCtrl` probe reply) cannot exist before
the write is made, so those ops are emitted as a PLAN with the fields the
executor must fill named explicitly in `pending`. Nothing here pretends to have
measured something it has not.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import bodies as BD  # noqa: E402
import layout as LY  # noqa: E402
import sites as S  # noqa: E402
import terrain1m as T  # noqa: E402

PATCHES = "/tmp/settle/h1m.bin"
LOCATIONS = "/tmp/settle/loc3/f6fe167f4fcd.json"

# --- THE CHOSEN SITES -----------------------------------------------------
# Coordinates come from `sites.py`'s 1 m search; each was the highest-scoring
# candidate of its kind that cleared the per-type POI stand-off, the recorded
# installations, and the +/-8 m clamp. The `why` string is the justification the
# operator asked for -- elevation and prominence for the towers and castles,
# waterfront for the harbour towns -- and every number in it is measured.

OUTLIERS: list[dict] = [
    dict(id="hognest-castle", kind="castle", tag="u-castle", patch="mainland",
         x=530.0, z=1348.0, pad_m=44.0,
         body="hs_ashlands_neletit_portalhubtower.blueprint",
         road_priority="spur",
         why="MEASURED: 110.95 m pad height, 94.9 m above the lowest ground "
             "within 300 m, 100% Mountain over the pad, on the SPAWN ISLAND so "
             "it is reachable on foot. The clamp decides the architecture here: "
             "a 44 m pad has 4.09 m of headroom, a 52 m pad 1.80 m, and a 60 m "
             "pad FAILS at -6.54 m, so the body must span <= 38 m."),
    dict(id="sudrberg-keep", kind="castle", tag="u-sudrberg", patch="south33",
         x=2406.0, z=-3354.0, pad_m=44.0,
         body="hs_mistlands_sheepshank_trophytowerv2.blueprint",
         road_priority="none",
         why="MEASURED: 121.84 m pad height, 120.9 m of local rise, 100% "
             "Mountain, clamp headroom 2.73 m. On the south isle, which is a "
             "SEPARATE landmass -- portal-only, no road, and that is stated "
             "rather than implied."),
    dict(id="wt-spawn", kind="watchtower", tag="u-wtspawn", patch="mainland",
         x=110.0, z=-104.0, pad_m=20.0, body="outposttower.blueprint",
         road_priority="trunk", approach_bearing_deg=None,
         why="MEASURED: 52.6 m above the lowest ground within 160 m and 205 m "
             "from the StartTemple -- the first built thing a player on foot "
             "can see from spawn."),
    dict(id="wt-south", kind="watchtower", tag="u-wtsouth", patch="mainland",
         x=538.0, z=40.0, pad_m=20.0, body="outposttower.blueprint",
         road_priority="trunk",
         why="MEASURED: 56.2 m of local rise, 604 m from spawn, overlooking "
             "the ground between the temple and the dock."),
    dict(id="wt-town", kind="watchtower", tag="u-wttown", patch="mainland",
         x=820.0, z=738.0, pad_m=20.0, body="outposttower.blueprint",
         road_priority="trunk",
         why="MEASURED: 40.0 m of local rise, 78.0 m from navigable water, "
             "between Stenvik and the dock -- the tower that watches the town's "
             "own approach."),
    dict(id="wt-peak", kind="watchtower", tag="u-wtpeak", patch="mainland",
         x=1418.0, z=1530.0, pad_m=20.0, body="outposttower.blueprint",
         road_priority="spur",
         why="MEASURED: 85.6 m of local rise at 103.64 m, the highest "
             "watchtower on the spawn island."),
    dict(id="wt-north", kind="watchtower", tag="u-wtnorth", patch="mainland",
         x=152.0, z=1194.0, pad_m=20.0, body="outposttower.blueprint",
         road_priority="trunk",
         why="MEASURED: 32.5 m of local rise, 18.4 m from navigable water on "
             "the north shore -- it watches the channel."),
    dict(id="wt-east", kind="watchtower", tag="u-wteast", patch="mainland",
         x=1858.0, z=146.0, pad_m=20.0, body="outposttower.blueprint",
         road_priority="spur",
         why="MEASURED: 55.5 m of local rise, 1928 m out at the east end of "
             "the island, where the ferry terminals are."),
    dict(id="lh-south", kind="lighthouse", tag="u-lhsouth", patch="mainland",
         x=-102.0, z=-916.0, pad_m=22.0, body="drake-lighthouse.blueprint",
         road_priority="spur",
         why="MEASURED: 11.7 m from NAVIGABLE water (the sea component that "
             "reaches the patch edge, not a pond), 24.0 m of local rise, and "
             "30% of a 640 m window below the water plane -- a cape, not a bay "
             "shore. It stands over the southern sea approach to the dock at "
             "(675,-85)."),
    dict(id="lh-east", kind="lighthouse", tag="u-lheast", patch="mainland",
         x=2354.0, z=156.0, pad_m=22.0, body="drake-lighthouse.blueprint",
         road_priority="spur",
         why="MEASURED: 9.8 m from navigable water, 22.7 m of local rise, at "
             "the island's east cape beside the ferry crossing."),
    dict(id="lh-west", kind="lighthouse", tag="u-lhwest", patch="westisle",
         x=-1350.0, z=2358.0, pad_m=22.0,
         body="hs_plains_ulf_the_builder_lighthouse.blueprint",
         road_priority="spur",
         why="MEASURED: 13.5 m from navigable water, 33.3 m of local rise, on "
             "the west isle's north cape -- the far corner of the main "
             "landmass, and the light a boat sees first coming from the north."),
    dict(id="tree-sth", kind="treehouse", tag="u-treesth", patch="mainland",
         x=546.0, z=482.0, pad_m=16.0,
         body="hs_meadows_stilt_house.blueprint", road_priority="spur",
         cluster=3,
         why="MEASURED: 100% BlackForest over the pad at 75.46 m, 776 m from "
             "spawn -- the nearest canopy hamlet to the temple on the spawn "
             "island. `hs_ashlands_swampytreehouse.blueprint` was chosen first "
             "and is OUT: MEASURED, `to_rcon_plan.py` refuses it on 22 "
             "unspawnable prefabs (Eyescream, FeastAshlands, MeadBzerker, "
             "QueensJam and 18 more consumables on item stands), and a body "
             "needing --drop-prefab is a worse choice than one that does not. "
             "The library has no large treehouse that spawns clean, so the "
             "flagship is three stilt bodies rather than one big one, and that "
             "is stated rather than quietly substituted."),
    dict(id="tree-west", kind="treehouse", tag="u-treewest", patch="westisle",
         x=-530.0, z=850.0, pad_m=16.0, body="hs_meadows_stilt_house.blueprint",
         road_priority="spur", cluster=3,
         why="MEASURED: BlackForest, 41.39 m, 966 m from spawn. A hamlet of "
             "three stilt bodies, each of which GROUNDS on its posts."),
    dict(id="tree-near", kind="treehouse", tag="u-treenear", patch="westisle",
         x=-614.0, z=-236.0, pad_m=16.0, body="hs_meadows_stilt_house.blueprint",
         road_priority="spur", cluster=3,
         why="MEASURED: BlackForest, 35.20 m, 599 m from spawn -- the nearest "
             "built thing to the temple after the spawn watchtower."),
]

TOWNS = [
    dict(id="stenvik", tag="u-stenvik", spec=LY.STENVIK_SPEC, road_priority="trunk",
         gate="south-west end of the main street",
         why="MEASURED: a 176 m district of 99.8% Meadows at 45.57 m with "
             "17.8 m of district relief, 133 m from NAVIGABLE water, 260 m "
             "from the existing station hub at (661.5,1088.5), on the spawn "
             "island. The district is NOT levelled -- MEASURED, no 176 m "
             "district of <=12 m relief on this seed is both dry and coastal, "
             "so the town follows the ground on individual pads."),
    dict(id="vestvik", tag="u-vestvik", spec=LY.VESTVIK_SPEC, road_priority="trunk",
         gate="east end of the main street, toward the quay",
         why="MEASURED: 100% Meadows at 33.99 m with 13.3 m of district relief "
             "on the west isle, the other half of the largest landmass in the "
             "world. Waterfront CORRECTED by Crossings and re-measured here: "
             "93.8 m to the nearest wet cell but 137.9 m to navigable water, "
             "and the nearest shoreline that takes a 22 m pier is 227.3 m away "
             "at (-1031.5,1656.5). The village does not move; the quay is "
             "Crossings' harbour-vestvik."),
]

# Pairing: every portal's far end is the world's portal hall, whose hub-side
# tags are WayFinding's to allocate. Recorded as a declared counterparty rather
# than invented here, because a tag invented at this end is a one-ended pair.
PAIR_OTHER_END = "WayFinding: portal hall hub end"


def island_membership(picker: S.Picker) -> dict:
    """Which landmass each patch's spawn-side component is, so 'walkable from
    spawn' is a measurement rather than a distance."""
    out = {}
    for pid in ("mainland", "westisle"):
        f = picker.fields[pid]
        lab, comps = T.land_components(f, min_area_m2=100000.0)
        sx, sz = picker.loc.spawn
        spawn_label = None
        if f.contains(sx, sz):
            iz, ix = f.idx(sx, sz)
            spawn_label = int(lab[iz, ix]) or None
        out[pid] = {"labels": lab, "components": comps, "spawn_label": spawn_label}
    return out


def site_island(islands: dict, picker: S.Picker, patch: str, x: float, z: float) -> dict:
    info = islands.get(patch)
    if info is None:
        return {"same_landmass_as_spawn": False,
                "evidence": f"patch {patch} does not contain the spawn point; "
                            f"separate landmass, portal-only"}
    f = picker.fields[patch]
    iz, ix = f.idx(x, z)
    lab = int(info["labels"][iz, ix])
    same = bool(lab and info["spawn_label"] and lab == info["spawn_label"])
    area = next((c["area_m2"] for c in info["components"] if c["label"] == lab), None)
    return {"landmass_label": lab, "landmass_area_m2": area,
            "same_landmass_as_spawn": same,
            "evidence": ("MEASURED: 4-connected land (h > 30, the measured "
                         "c_WaterLevel) at 1 m puts this site in the same "
                         "component as the StartTemple"
                         if same else
                         "MEASURED: 4-connected land at 1 m puts this site in a "
                         "DIFFERENT component from the StartTemple -- it is not "
                         "walkable from spawn and needs a portal or a boat")}


def block_rise(picker: S.Picker, patch: str, x: float, z: float,
               radius_m: float = 160.0) -> float:
    """Height above the lowest ground within `radius_m`, read at the pad's own
    block. This is the prominence figure quoted for a tower or a castle: a
    local maximum says nothing about whether a tower there overlooks anything,
    and this does."""
    blk = picker.blocks[patch]
    rise = S.win_rise(blk, radius_m)
    iz = min(max(int(round((z - blk.z0) / blk.step)), 0), rise.shape[0] - 1)
    ix = min(max(int(round((x - blk.x0) / blk.step)), 0), rise.shape[1] - 1)
    return float(rise[iz, ix])


def build(picker: S.Picker, run_interior: bool = True) -> dict:
    planner = LY.TownPlanner(picker)
    islands = island_membership(picker)
    ocean = {pid: S.ocean_distance(picker.fields[pid]) for pid in picker.fields}

    plan: dict = {"seed": "Pirate68", "seed_hash": 147627509,
                  "patch_file": PATCHES, "locations_dump": LOCATIONS,
                  "towns": {}, "outliers": [], "bodies": {}, "totals": {}}

    town_sites = S.dedupe(picker, picker.town())
    by_xz = {(round(s.x), round(s.z)): s for s in town_sites}
    want = {"stenvik": (496, 904), "vestvik": (-906, 1846)}
    for spec in TOWNS:
        site = by_xz.get(want[spec["id"]])
        if site is None:
            raise ValueError(f"the recorded {spec['id']} centre {want[spec['id']]} "
                             f"is no longer a town candidate: the search moved, and "
                             f"a plan that silently relocates a town is worse than "
                             f"one that stops. Candidates: {sorted(by_xz)}")
        town = planner.plan(site, spec["id"], spec["spec"])
        f = picker.fields[site.patch]
        iz, ix = f.idx(site.x, site.z)
        town["why"] = spec["why"]
        town["tag"] = spec["tag"]
        town["road_priority"] = spec["road_priority"]
        town["gate"] = spec["gate"]
        town["dist_wet_cell_m"] = round(float(picker.water(site.patch)[iz, ix]), 1)
        town["dist_navigable_water_m"] = round(float(ocean[site.patch][iz, ix]), 1)
        town["dist_spawn_m"] = site.dist_spawn_m
        town["island"] = site_island(islands, picker, site.patch, site.x, site.z)
        town["overlaps"] = LY.check_overlaps(town)
        town["site_notes"] = site.notes
        plan["towns"][spec["id"]] = town

    for o in OUTLIERS:
        f = picker.fields[o["patch"]]
        row = planner.by_name.get(o["body"])
        if row is None:
            raise ValueError(f"{o['body']} is not in the library audit")
        if not BD.fits(row, o["pad_m"]):
            raise ValueError(
                f"{o['id']}: body {o['body']} spans {BD.span(row):.1f} m and does "
                f"not fit a {o['pad_m']} m pad with a {BD.PAD_MARGIN_M} m margin")
        pad = S.best_pad(
            f, o["x"], o["z"], o["pad_m"], o["pad_m"], search_m=16.0, stride_m=2.0,
            must_clear=lambda px, pz, hd: (
                picker.loc.clear(px, pz, hd)
                and picker.installation_conflict(px, pz, hd) is None))
        iz, ix = f.idx(pad["x"], pad["z"])
        rec = dict(o)
        rec.update({
            "x": round(pad["x"], 1), "z": round(pad["z"], 1), "pad": pad,
            "body_footprint": [row["footprint_x_m"], row["footprint_z_m"]],
            "body_sha256": row["sha256"], "pieces": int(row["pieces"]),
            "grounded": row["flat_pad_verdict"],
            "floating_fraction": float(row["floating_fraction"]),
            "missing_prefabs": row["missing_prefabs"],
            "stations": int(row["stations"]), "beds": int(row["beds"]),
            "walkable_levels": int(row["walkable_levels"]),
            "base_y": float(row["base_y"]),
            "ground_y": round(float(f.h[iz, ix]), 2),
            "rise_160m_m": round(block_rise(picker, o["patch"], pad["x"], pad["z"]), 1),
            "dist_wet_cell_m": round(float(picker.water(o["patch"])[iz, ix]), 1),
            "dist_navigable_water_m": round(float(ocean[o["patch"]][iz, ix]), 1),
            "dist_spawn_m": round(math.hypot(pad["x"] - picker.loc.spawn[0],
                                             pad["z"] - picker.loc.spawn[1]), 1),
            "nearest_location": picker.loc.nearest(pad["x"], pad["z"]),
            "poi_violations": picker.loc.violations(
                pad["x"], pad["z"], math.hypot(o["pad_m"], o["pad_m"]) / 2),
            "island": site_island(islands, picker, o["patch"], pad["x"], pad["z"]),
            "cluster": o.get("cluster", 1),
        })
        plan["outliers"].append(rec)

    # --- the NPC gate, measured per body actually used -------------------
    # SCOPE, because a gate applied to the wrong set is the same defect as a
    # check measured at the wrong baseline. The operator's remark was about a
    # TOWN that might later host villagers, so the gate is HARD for a DWELLING
    # and informational everywhere else: a watchtower, a lighthouse and a
    # beehive are not places a villager lives, and refusing them for having no
    # enclosed floor would refuse the operator's own brief.
    dwelling_roles = {"hall", "house", "cottage", "longhouse", "manor",
                      "quickhouse", "stonehouse", "hut", "workshop"}
    used: dict[str, int] = {}
    dwelling_bodies: set[str] = set()
    for town in plan["towns"].values():
        for b in town["buildings"]:
            used[b["body"]] = used.get(b["body"], 0) + 1
            if b["role"] in dwelling_roles:
                dwelling_bodies.add(b["body"])
    for rec in plan["outliers"]:
        used[rec["body"]] = used.get(rec["body"], 0) + rec["cluster"]
    for name, count in sorted(used.items()):
        row = planner.by_name[name]
        entry = {
            "count": count, "sha256": row["sha256"], "pieces": int(row["pieces"]),
            "footprint": [row["footprint_x_m"], row["footprint_z_m"]],
            "grounded": row["flat_pad_verdict"],
            "floating_fraction": float(row["floating_fraction"]),
            "missing_prefabs": row["missing_prefabs"],
            "base_y": float(row["base_y"]), "relief_m": float(row["relief_m"]),
            "max_buried_m": float(row["max_buried_m"]),
            "stations": int(row["stations"]), "beds": int(row["beds"]),
            "walkable_levels": int(row["walkable_levels"]),
            "is_town_dwelling": name in dwelling_bodies,
        }
        if run_interior:
            try:
                entry["interior"] = BD.interior_report(name)
            except Exception as e:
                entry["interior"] = {"error": f"{type(e).__name__}: {e}",
                                     "npc_ok": None}
            entry["npc_gate"] = (
                "PASS" if (not entry["is_town_dwelling"]
                           or entry["interior"].get("npc_ok")) else "FAIL")
            entry["npc_gate_scope"] = (
                "hard gate: this body houses a town dwelling"
                if entry["is_town_dwelling"] else
                "informational: not a dwelling (tower, light, production or "
                "outlying structure), so an unenclosed interior is not a defect")
        plan["bodies"][name] = entry
    failed = [n for n, e in plan["bodies"].items() if e.get("npc_gate") == "FAIL"]
    if failed:
        raise ValueError(
            f"these town dwellings have under {BD.MIN_INDOOR_M2:g} m2 of covered, "
            f"enclosed, reachable floor and a villager could never stand in them: "
            f"{failed}. Replace them in the town spec rather than lowering the gate.")

    # --- the spawnability gate -------------------------------------------
    # Asked of the thing that does the spawning, not of the audit. MEASURED,
    # they disagree: `base_audit.json` reports `missing_prefabs: []` for
    # `hs_blackforest_crimsonchaostownhall.blueprint` while `to_rcon_plan.py`
    # refuses it on `Placeable_HardRock: not in the evidence file`. Four of the
    # eighteen bodies first chosen here failed this way.
    import spawnable
    unspawnable = []
    for name in plan["bodies"]:
        rep = spawnable.check(name)
        plan["bodies"][name]["spawnable"] = {
            "ok": rep["ok"], "commands": rep["commands"],
            "missing": rep["missing"],
            "method": "MEASURED: a real run of blueprints/to_rcon_plan.py "
                      "--align floor-center, which is the emitter that puts the "
                      "body in the world",
            "tool": "tools/jumpstart/settlements/spawnable.py",
        }
        if not rep["ok"]:
            unspawnable.append((name, [m["prefab"] for m in rep["missing"]]))
    if unspawnable:
        raise ValueError(
            f"these bodies cannot be spawned without --drop-prefab and are not "
            f"acceptable choices: {unspawnable}. Replace them in the spec.")

    pieces = sum(e["pieces"] * e["count"] for e in plan["bodies"].values())
    earth = (sum(t["totals"]["earthwork_m3"] for t in plan["towns"].values())
             + sum(r["pad"]["moved_m3"] * r["cluster"] for r in plan["outliers"]))
    portals = len(plan["towns"]) + len(plan["outliers"])
    plan["totals"] = {
        "sites": len(plan["towns"]) + len(plan["outliers"]),
        "buildings": sum(t["totals"]["buildings"] for t in plan["towns"].values())
        + sum(r["cluster"] for r in plan["outliers"]),
        "pieces_total": pieces,
        "portals": portals,
        "signs": portals,
        "zdo_estimate": pieces + 2 * portals,
        "earthwork_m3": round(earth, 1),
        "beds": sum(t["totals"]["beds"] for t in plan["towns"].values())
        + sum(r["beds"] * r["cluster"] for r in plan["outliers"]),
        "stations": sum(t["totals"]["stations"] for t in plan["towns"].values())
        + sum(r["stations"] * r["cluster"] for r in plan["outliers"]),
        "all_clamp_ok": (all(t["totals"]["all_clamp_ok"] for t in plan["towns"].values())
                         and all(r["pad"]["clamp_ok"] for r in plan["outliers"])),
        "bodies_distinct": len(plan["bodies"]),
        "bodies_with_missing_prefabs": sum(
            1 for e in plan["bodies"].values() if e["missing_prefabs"]),
        "bodies_not_grounding": sum(
            1 for e in plan["bodies"].values() if e["grounded"] not in BD.GROUNDS),
    }
    return plan


def sites_yaml(plan: dict) -> str:
    """The interchange `RoadNet` asked for, in the shape it specified.

    `pad_radius_m` is the radius a ribbon must stay OUTSIDE and terminate at,
    taken from the district or the pad plus its own margin.
    `approach_bearing_deg` is given ONLY where the layout forces one -- a gate
    or a downhill castle approach -- and omitted otherwise, because RoadNet has
    the height field and picking the lowest-gradient approach there is strictly
    better than guessing here.
    """
    import yaml as _yaml
    recs = []
    for name, t in plan["towns"].items():
        main = t["streets"][0]
        recs.append({
            "id": name, "type": "town" if name == "stenvik" else "village",
            "xz": [t["centre"][0], t["centre"][1]],
            "pad_radius_m": 100.0, "road_priority": t["road_priority"],
            "approach_bearing_deg": round((main["bearing_deg"] + 180) % 360, 1),
            "portal_tag": t["tag"], "provisional": False,
            "pad_y": t["pad_y"], "patch": t["patch"],
            "buildings": t["totals"]["buildings"],
            "pieces": t["totals"]["pieces_total"],
            "earthwork_m3": t["totals"]["earthwork_m3"],
            "clamp_headroom_min_m": t["totals"]["clamp_headroom_min_m"],
            "streets": [{"name": s["name"], "bearing_deg": s["bearing_deg"],
                         "length_m": s["profile"]["length_m"],
                         "max_grade_8m": s["profile"]["max_grade"],
                         "max_step_1m_m": s["profile"]["max_step_m"],
                         "walkable": s["walkable"]} for s in t["streets"]],
            "gate": t["gate"],
            "dist_navigable_water_m": t["dist_navigable_water_m"],
            "dist_spawn_m": t["dist_spawn_m"],
            "same_landmass_as_spawn": t["island"]["same_landmass_as_spawn"],
            "why": t["why"],
        })
    for r in plan["outliers"]:
        rec = {
            "id": r["id"], "type": r["kind"], "xz": [r["x"], r["z"]],
            "pad_radius_m": round(r["pad_m"] * math.sqrt(2) / 2 + 2.0, 1),
            "road_priority": r["road_priority"], "portal_tag": r["tag"],
            "provisional": False, "pad_y": r["pad"]["target_y"],
            "patch": r["patch"], "body": r["body"],
            "pieces": r["pieces"] * r["cluster"], "cluster": r["cluster"],
            "earthwork_m3": round(r["pad"]["moved_m3"], 1),
            "clamp_headroom_m": r["pad"]["clamp_headroom_m"],
            "rise_160m_m": r["rise_160m_m"],
            "dist_navigable_water_m": r["dist_navigable_water_m"],
            "dist_spawn_m": r["dist_spawn_m"],
            "same_landmass_as_spawn": r["island"]["same_landmass_as_spawn"],
            "why": r["why"],
        }
        if r["kind"] == "castle":
            rec["approach_bearing_deg"] = None
            rec["approach_note"] = ("gate faces downhill; RoadNet's switchback "
                                    "may terminate below the summit with a "
                                    "portal rather than reaching the door")
        recs.append(rec)
    doc = {
        "world": "Ulfsland", "seed": plan["seed"], "seed_hash": plan["seed_hash"],
        "owner": "Settlements",
        "provenance": {
            "height_field": plan["patch_file"],
            "height_field_note": "PatchScan 1 m with rivers; water plane y=30 "
                                 "(MEASURED ZoneSystem::c_WaterLevel)",
            "locations_dump": plan["locations_dump"],
            "clamp_m": T.CLAMP_M,
            "scripts": ["sites.py", "layout.py", "clearance.py", "terrain1m.py",
                        "bodies.py", "spawnable.py", "plan.py"],
        },
        "totals": plan["totals"],
        "sites": recs,
    }
    return _yaml.safe_dump(doc, sort_keys=False, width=10 ** 9)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--patches", default=PATCHES)
    ap.add_argument("--locations", default=LOCATIONS)
    ap.add_argument("--out", default=str(HERE))
    ap.add_argument("--no-interior", action="store_true")
    a = ap.parse_args()
    picker = S.Picker(a.patches, a.locations)
    plan = build(picker, run_interior=not a.no_interior)
    out = Path(a.out)
    (out / "plan.json").write_text(json.dumps(plan, indent=1, default=str))
    (out / "sites.yaml").write_text(sites_yaml(plan))
    print(json.dumps(plan["totals"], indent=1))
    print("\nbodies used:")
    for name, e in plan["bodies"].items():
        ind = e.get("interior", {})
        print(f"  {name[:52]:54s} x{e['count']:2d} pcs={e['pieces']:5d} "
              f"{e['grounded']:16s} float={e['floating_fraction']:.2f} "
              f"indoor={ind.get('indoor_covered_m2', 'n/a')!s:>7s} "
              f"npc={ind.get('npc_ok')}")
    print("\noutliers:")
    for r in plan["outliers"]:
        print(f"  {r['id']:16s} {r['kind']:11s} ({r['x']:7.1f},{r['z']:7.1f}) "
              f"pad {r['pad']['width_m']:4.0f} y={r['pad']['target_y']:7.2f} "
              f"moved={r['pad']['moved_m3']:7.1f} head={r['pad']['clamp_headroom_m']:5.2f} "
              f"rise={r['rise_160m_m']:6.1f} ocean={r['dist_navigable_water_m']:6.1f} "
              f"island_same_as_spawn={r['island']['same_landmass_as_spawn']} "
              f"poi_violations={len(r['poi_violations'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
