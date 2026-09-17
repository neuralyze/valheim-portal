#!/usr/bin/env python3
"""BUILDINGS STANDING IN A ROAD SURFACE: the network-wide census, and the
routing verdict per case.

WHY THIS EXISTS.  The operator walked the finished network and reported:
"there still are some building structures in the road in some places. stenvik
for instance has a circle of huts in the road at the far end."  Every
instrument this family already owns answers a DIFFERENT question:

  * `clear.py` censuses the corridor and plans REMOVALS.  A building piece is
    not on `CLEARABLE`, so `blocks()` is true and the whole cylinder is
    skipped -- correctly, because furnished mod content is the one
    unrepairable damage class.  `clear.py` therefore reports a building as a
    REASON A TREE SURVIVED, never as a defect in its own right.  Its closing
    audit counts `in_clear_width_clearable`, which excludes every building by
    construction: seq 1999-2006 report 18 objects network-wide and NOT ONE of
    them is a building.
  * `applied.py` reads the surface and finds FOREIGN TERRAIN claims.  A pad
    that wins a contested sample is recorded; a building whose pad sits
    somewhere else entirely while its ROOF overhangs the carriageway is
    invisible to it.
  * `network.py`'s `poi_within_keepout` is the router's keep-out audit, and it
    is fed from `poi.py`, i.e. the world-GENERATOR's location instances.  Our
    OWN settlement buildings are not location instances, so a pass-through
    segment has no keep-out against them at all.  MEASURED: T4-meadhall-
    stathub reports `poi_within_keepout: []` while its centreline passes
    0.1 m from `stenvik-cottage-1`'s centre.

So the defect class has never been measured.  This module measures it, and it
measures the thing the operator can see -- a piece of a building standing on
the ground a player walks on -- not a proxy for it.

WHAT IS A "BUILDING IN THE ROAD", stated so it can be argued with:

  1. A STRUCTURE piece.  The classification below is read off the census
     corpus's own prefab index (168 distinct names over all 15 written
     segments), not guessed from a name shape, and every name in that index
     is assigned exactly one class.  `STRUCTURE` is the class the operator's
     words name: walls, roofs, floors, beams, poles, doors, gates, stairs,
     arches, pillars -- the body of a building.  Furniture, fixtures and
     lights are `FIXTURE`: they stand INSIDE a building and a player does not
     call a torch a structure, but they are reported beside it because a
     workbench in a carriageway is the same routing defect.
  2. Inside the ROAD SURFACE, and the bands are named separately because they
     are different defects.  `CARRIAGEWAY` (|lat| <= width/2) is a piece in
     the lane a player walks.  `SHOULDER` (<= width/2 + 1.0) is a piece at
     the kerb.  `EARTHWORK` (<= that station's own written footprint, i.e.
     carriageway + shoulder + batter run + terrain spread) is a piece standing
     on ground the ribbon MOVED, which is how a piece ends up buried or
     floating.
  3. Not on a bridged station.  A bridge deck is Crossings' and the ribbon
     does not write there, so a piece under a deck is not standing in a road
     surface this module wrote.

WHOSE IT IS.  Resolved geometrically, because the ledger holds no per-piece
provenance for a town: `settlements/build.py` spawns a body from a blueprint
over RCON, so the 2,606 pieces of Stenvik have no `spawn` record between them
(MEASURED: 144 `spawn` records in the whole chain, none of them a town piece).
The resolution is therefore

  OURS  -- the piece sits inside a building's own written `site_pad`
           rectangle, taken from `settlements/plan.json` (centre, pad
           width/depth, pad yaw).  That rectangle IS the foundation
           `writer.py::_pad_footprint_check` defends.
  MOD   -- otherwise, and within `exteriorRadius` of a location instance from
           the world-generator's own locations dump.  These are
           `More_World_Locations` bodies (WoodHouse*, StoneHouse*, WoodFarm*,
           Ruin*, StoneTowerRuins*) and they are the unrepairable class.
  LOOSE -- neither.  A piece with no claim: either an operator's own build or
           a body whose pad is recorded elsewhere.  Reported, never touched.

A town's `pad_radius_m: 100.0` IS NOT USED HERE and that is deliberate: it is
a DISTRICT radius, and treating it as a footprint would put 100 m of open
meadow under Stenvik's name and call a road crossing it a violation.  The
foundations are the per-building rectangles.

READ-ONLY.  This module opens the ledger and the plans for reading and returns
numbers.  It appends nothing, sends nothing and removes nothing.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import yaml

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
sys.path.insert(0, str(HERE))

import ribbon as RB  # noqa: E402

SEGMENTS = HERE / "segments.yaml"
SETTLE_PLAN = JUMPSTART / "settlements" / "plan.json"

# The 15 segments whose terrain IS WRITTEN, read off the ledger's own
# `terrain_write` records with `role == "road_segment"` rather than off the
# 35-segment plan: 20 of the planned segments were never built, and a building
# standing on an unbuilt alignment is not standing in a road.
WRITTEN = [
    "T1-portalhub-temple", "T3-temple-meadhall", "T4-meadhall-stathub",
    "T5-temple-dockshore", "T8-stenvik-wttown", "T9-wttown-dockshore",
    "T10-wtspawn-wtsouth", "T12-wtspawn-temple", "T13-temple-harboursouth",
    "S1-portalhub-brgs2", "S7-stathub-wtnorth", "S8-wtsouth-treesth",
    "S12-brgs1-boathouse", "W5-brgs1-wsouth", "W10-brgs1-treenear",
]

# ---------------------------------------------------------------------------
# the prefab index, classified
# ---------------------------------------------------------------------------
# EVERY name below was READ from the census corpus (`prefab` field of every
# object in the 15 per-segment censuses), not enumerated from a mod bundle and
# not matched by prefix.  A prefix filter over this index is exactly the defect
# that cost this project a wrong answer earlier: `stone_floor` and
# `stone_pillar` are structure, `stone_wall_*` is structure, but
# `Pickable_Stone` and `Pickable_StoneRock` are pickups and `rock4_*` is a
# mineable deposit.  `classify` raises on an unknown name so a new prefab
# cannot silently land in the wrong bucket.

STRUCTURE = {
    # our bodies and the mod houses share the vanilla + mod piece vocabulary
    "Piece_flametal_beam", "Piece_flametal_pillar",
    "Piece_grausten_floor_1x1", "Piece_grausten_floor_2x2",
    "Piece_grausten_floor_4x4", "Piece_grausten_pillar_arch",
    "Piece_grausten_pillar_arch_small", "Piece_grausten_pillarbase_medium",
    "Piece_grausten_pillarbase_small", "Piece_grausten_pillarbeam_medium",
    "Piece_grausten_pillarbeam_small", "Piece_grausten_wall_4x2",
    "ashwood_beam_1m", "ashwood_beam_2m", "ashwood_decowall_2x2",
    "ashwood_decowall_divider", "ashwood_decowall_tree", "ashwood_pole_1m",
    "ashwood_wall_beam_26", "ashwood_wall_beam_45",
    "blackmarble_1x1", "blackmarble_2x1x1", "blackmarble_arch",
    "blackmarble_base_1", "blackmarble_column_2", "blackmarble_floor",
    "blackmarble_floor_triangle", "blackmarble_stair",
    "darkwood_arch", "darkwood_beam", "darkwood_beam4x4", "darkwood_pole4",
    "darkwood_roof_45", "flametal_gate", "iron_floor_1x1_v2",
    "stone_arch", "stone_floor", "stone_floor_2x2", "stone_pillar",
    "stone_stair", "stone_wall_1x1", "stone_wall_2x1", "stone_wall_4x2",
    "wood_beam", "wood_beam_1", "wood_beam_26", "wood_beam_45", "wood_door",
    "wood_floor", "wood_floor_1x1", "wood_gate", "wood_log_45", "wood_pole",
    "wood_pole2", "wood_pole_log", "wood_pole_log_4", "wood_roof",
    "wood_roof_45", "wood_roof_ocorner", "wood_roof_ocorner_45",
    "wood_roof_top", "wood_roof_top_45", "wood_stair", "wood_stepladder",
    "wood_wall_half", "wood_wall_log", "wood_wall_log_4x0.5",
    "wood_wall_quarter", "wood_wall_roof", "wood_wall_roof_45",
    "wood_wall_roof_a", "wood_wall_roof_top", "wood_wall_roof_top_45",
    "wood_wall_roof_upsidedown", "woodwall", "woodiron_beam",
    "woodiron_beam_26",
    # a fence is a built boundary: it is not a house body, but a player who
    # walks into one in a carriageway calls it a structure in the road.
    "wood_fence",
}

FIXTURE = {
    "CastleKit_groundtorch_blue", "MWL_Shrine", "StatueDeer", "bed",
    "fire_pit", "fire_pit_iron", "goblin_bed", "guard_stone",
    "piece_banner07", "piece_beehive", "piece_bench01", "piece_chair",
    "piece_chair02", "piece_chest_wood", "piece_cookingstation",
    "piece_dvergr_lantern", "piece_groundtorch_mist", "piece_groundtorch_wood",
    "piece_logbench01", "piece_table", "piece_workbench",
    "piece_workbench_ext1", "piece_workbench_ext2", "portal_wood", "rug_deer",
    "sign", "wood_stack",
}

POI = {
    "DG_MeadowsFarm", "Flies", "LocationProxy", "RockDolmen_1", "RockDolmen_2",
    "RockDolmen_3", "Pickable_DolmenTreasure", "Pickable_ForestCryptRandom",
    "Pickable_ForestCryptRemains01", "Pickable_ForestCryptRemains02",
    "Pickable_ForestCryptRemains04", "Pickable_SurtlingCoreStand",
    "Music_GreydwarfCamp", "Music_MeadowsVillageFarm",
    "BlackForestLocationMusic", "Spawner_Boar", "Spawner_Greydwarf",
    "Spawner_Greydwarf_Shaman", "Spawner_Skeleton",
    "Spawner_Skeleton_Meadows_night", "Spawner_Skeleton_Meadows_night_noarcher",
    "Spawner_Skeleton_respawn_30", "TreasureChest_blackforest",
    "TreasureChest_forestcrypt", "TreasureChest_meadows",
    "TreasureChest_meadows_01", "TreasureChest_meadows_buried",
    "TreasureChest_trollcave", "MineRock_Tin", "rock4_copper", "rock4_forest",
    "Beehive",
}

CREATURE = {"Boar", "Crow", "Deer", "Neck", "Greyling", "Greydwarf",
            "Skeleton", "sfx_boar_idle"}

# Everything else in the index is vegetation or scenery.  Listed rather than
# defaulted, so an unseen name is an error and not a silent "nature".
NATURE = {
    "BH_Pickable_Bjorncap", "BH_Pickable_Boswellia", "BH_Pickable_Chamomile",
    "BH_Pickable_SkaldsIvy", "BH_Pickable_ValkyrieFern",
    "BH_Pickable_VikingsBreadcap", "Beech1", "Beech_small1", "Beech_small2",
    "Birch1", "Birch2", "BlueberryBush", "Bush01", "FirTree",
    "FirTree_oldLog", "FirTree_small", "Oak1", "Pickable_Branch",
    "Pickable_Dandelion", "Pickable_Flint", "Pickable_Mushroom",
    "Pickable_Stone", "Pickable_StoneRock", "Pickable_Thistle", "Pinetree_01",
    "RaspberryBush", "Rock_3", "Rock_4", "Rock_7", "shrub_2", "stubbe",
    "vines",
}


def classify(prefab: str) -> str:
    if prefab in STRUCTURE:
        return "STRUCTURE"
    if prefab in FIXTURE:
        return "FIXTURE"
    if prefab in POI:
        return "POI"
    if prefab in CREATURE:
        return "CREATURE"
    if prefab in NATURE:
        return "NATURE"
    raise KeyError(
        f"prefab {prefab!r} is not in this module's index.  READ THE INDEX and "
        f"assign it a class; defaulting it would put a building in NATURE or a "
        f"tree in STRUCTURE, and both have cost this project a wrong answer.")


# ---------------------------------------------------------------------------
# claims
# ---------------------------------------------------------------------------

class Claims:
    """Who owns the ground a piece stands on.

    OUR pads are rotated rectangles, so the test is the rectangle's, not a
    circle's: `stenvik-longhouse-2` is 8.4 x 17.0 m at yaw 30, and its
    circumscribing circle is 9.5 m where its half-extents are 4.2 and 8.5.  A
    circle over-claims 39 m2 of street.
    """

    def __init__(self, plan_path: Path = SETTLE_PLAN,
                 loc_dump: Path | None = None):
        doc = json.loads(plan_path.read_text())
        self.pads: list[dict] = []
        for town, t in doc["towns"].items():
            for b in t["buildings"]:
                pad = b["pad"]
                self.pads.append({
                    "site_id": b["id"], "town": town, "role": b.get("role"),
                    "x": pad.get("x", b["x"]), "z": pad.get("z", b["z"]),
                    "w": pad["width_m"], "d": pad.get("depth_m", pad["width_m"]),
                    "yaw": pad.get("yaw_deg", b["yaw_deg"]),
                    "body": b.get("body"), "pieces": b.get("pieces"),
                    "street": b.get("street")})
        for o in doc["outliers"]:
            pad = o.get("pad") or {}
            m = o.get("pad_m") or pad.get("width_m") or 0.0
            self.pads.append({
                "site_id": o["id"], "town": "outlier", "role": o.get("kind"),
                "x": pad.get("x", o["x"]), "z": pad.get("z", o["z"]),
                "w": pad.get("width_m", m), "d": pad.get("depth_m", m),
                "yaw": pad.get("yaw_deg", 0.0), "body": o.get("body"),
                "pieces": None, "street": None})
        # THE PLACED INSTANCES, read off the world rather than off the
        # generator's request list.  MEASURED, and this is a correction:
        # `plan.json`'s `locations_dump` carries 12,301 entries and EVERY ONE
        # of them reports `placed: false`, so it is the generator's REQUEST
        # list with request coordinates, not an index of what stands in the
        # world.  Resolving against it put the nearest named location 20-74 m
        # from clusters that turned out to be sitting 0.3-6.9 m from a real
        # `LocationProxy`, i.e. it answered LOOSE for eleven mod houses.  The
        # census's own `LocationProxy` / `DG_MeadowsFarm` / `MWL_Shrine` rows
        # ARE the placed instances: they are ZDOs the server answered with.
        self.locs: list[dict] = []

        # DESTINATION SITES THAT ARE NOT IN `plan.json`, each read from the
        # module that owns it.  Without these the census answers LOOSE for
        # 389 pieces of our own work: the portal hall (267 in T1's carriageway
        # and all 112 of S1's), Crossings' harbour at the end of T13 (the
        # cluster sits 0.2 m from the declared node and 15.3 m from an
        # unrelated `LocationProxy`, so a proxy-only resolver mislabels it as
        # mod content) and the boathouse at the end of S12 (5.8 m).
        #
        # A RADIUS, not a rectangle, because that is all the owning module
        # declares: `CROSSING_TERMINALS` publishes an approach bearing and a
        # deck width, never a footprint.  30 m is the widest waterfront body
        # in this world (the 22 m pier plus its apron) and it is checked
        # against the proxy distance rather than short-circuiting it -- the
        # NEARER claim wins, so a mod house 4 m from a piece still beats a
        # terminal 28 m away.
        self.sites: list[dict] = [
            {"site_id": "portal-hall", "x": -280.0, "z": 216.0, "r": 30.0,
             "src": "settlements/portal_hall.py HALL_X, HALL_Z (PAD 37.4)"},
        ]
        for tid, xz in (("harbour-south", (11.5, -258.5)),
                        ("dock-stathub", (502.5, 1062.5)),
                        ("boathouse-strait", (-303.5, -99.5)),
                        ("ferry-east", (1746.5, 655.5)),
                        ("harbour-vestvik", (-1031.5, 1656.5))):
            self.sites.append({"site_id": tid, "x": xz[0], "z": xz[1],
                               "r": 30.0,
                               "src": "roads/spec.py CROSSING_TERMINALS"})

    def add_proxies(self, objects: list[dict]) -> None:
        """Register the location markers a census found, deduped by position.

        Deduped by POSITION, never by ZDO id: ids are reassigned on world
        load, and the same marker appears in several overlapping censuses.
        """
        for o in objects:
            if o["prefab"] not in ("LocationProxy", "DG_MeadowsFarm",
                                   "MWL_Shrine"):
                continue
            key = (round(o["x"], 1), round(o["z"], 1))
            if any((round(e["x"], 1), round(e["z"], 1)) == key
                   for e in self.locs):
                continue
            self.locs.append({"name": o["prefab"], "x": o["x"], "z": o["z"]})

    def pad_of(self, x: float, z: float) -> dict | None:
        """The pad rectangle holding (x, z), tightest first."""
        best = None
        for p in self.pads:
            if p["w"] <= 0.0:
                continue
            a = math.radians(p["yaw"])
            dx, dz = x - p["x"], z - p["z"]
            u = dx * math.cos(a) + dz * math.sin(a)
            v = -dx * math.sin(a) + dz * math.cos(a)
            if abs(u) <= p["w"] / 2.0 and abs(v) <= p["d"] / 2.0:
                area = p["w"] * p["d"]
                if best is None or area < best[0]:
                    best = (area, p)
        return best[1] if best else None

    # HOW FAR A PIECE MAY SIT FROM A LOCATION MARKER AND STILL BE ITS BODY.
    # A generated location's marker is at its centre and its pieces spread
    # over its reach; the widest `exteriorRadius` in this world's request list
    # is 32 m (WoodFarm1) and the measured marker-to-cluster distances for the
    # eleven clusters this resolves are 0.3, 3.8, 4.0, 4.1, 4.6, 4.6, 6.9,
    # 13.9, 15.3, 25.3 m.  40 m admits all of them with margin and is still
    # far short of the 72-134 m that separates the two clusters that are NOT
    # mod content (the portal hall and the boathouse).
    MOD_REACH_M = 40.0

    def loc_of(self, x: float, z: float) -> tuple[dict, float] | None:
        best = None
        for e in self.locs:
            d = math.hypot(x - e["x"], z - e["z"])
            if d <= self.MOD_REACH_M and (best is None or d < best[0]):
                best = (d, e)
        return (best[1], best[0]) if best else None

    def site_of(self, x: float, z: float) -> tuple[dict, float] | None:
        best = None
        for e in self.sites:
            d = math.hypot(x - e["x"], z - e["z"])
            if d <= e["r"] and (best is None or d < best[0]):
                best = (d, e)
        return (best[1], best[0]) if best else None

    def resolve(self, x: float, z: float) -> tuple[str, str]:
        p = self.pad_of(x, z)
        if p is not None:
            return "OURS", p["site_id"]
        s = self.site_of(x, z)
        e = self.loc_of(x, z)
        if s is not None and (e is None or s[1] <= e[1]):
            return "OURS", s[0]["site_id"]
        if e is not None:
            return "MOD", f"{e[0]['name']}@{e[0]['x']:.0f},{e[0]['z']:.0f}"
        return "LOOSE", "-"


# ---------------------------------------------------------------------------
# the census
# ---------------------------------------------------------------------------

def segment_map(path: Path = SEGMENTS) -> dict:
    doc = yaml.safe_load(path.read_text())
    return {s["id"]: s for s in doc["segments"]}


def written_footprint(seg: dict) -> np.ndarray:
    """Per-station half-width of ground this ribbon WROTE.

    The same arithmetic as `clear.clear_half_width`, restated rather than
    imported so this module does not drag `clear.py`'s RCON machinery in: edge
    = carriageway/2 + shoulder, plus the batter run this station's own cut or
    fill needs, plus the 1 m the terrain write spreads.
    """
    prof = np.asarray(seg["profile_y"], dtype=np.float64)
    terr = np.asarray(seg["terrain_y"], dtype=np.float64)
    edge = seg["width_m"] / 2.0 + RB.SHOULDER_M
    run = np.minimum(RB.BATTER_MAX_M, np.abs(prof - terr) / RB.BATTER_GRADE)
    return edge + run + 1.0


def band(lat: float, half: float, shoulder: float, written: float) -> str | None:
    if lat <= half:
        return "CARRIAGEWAY"
    if lat <= half + shoulder:
        return "SHOULDER"
    if lat <= written:
        return "EARTHWORK"
    return None


# HOW CLOSE TO AN END COUNTS AS THE TERMINUS, and this distinction decides
# every verdict in this module, so it is stated rather than tuned.  A road is
# BUILT TO a destination: Stenvik's square, a watchtower door, the portal
# hall's ramp.  `spec.py`'s contract is that the ribbon "terminates at the pad
# edge", and where the standoff was set to 0.0-2.1 m the ribbon runs the last
# few metres inside the destination's own footprint.  A player arriving at the
# watchtower they set out for does not report that the watchtower is in the
# road.  What they report -- and what the operator reported -- is a body they
# have to walk around IN THE MIDDLE of a road that continues past it.
#
# MEASURED IN A STRAIGHT LINE TO THE END NODE, not along the alignment, and
# the correction matters: T9 leaves wttown on a tight turn around the tower,
# so its own watchtower's pieces sit 28-62 m ALONG the ribbon while standing
# 3-19 m from the end node.  Along-track distance called that a through case
# and it is an arrival -- the road curls around the destination it arrived at.
#
# 30 m, and it is the destination's own geometry that sets it: the worst pad
# half-extent in this world is 13.05 m and the largest pad is the portal
# hall's 37.4 m square, whose half-diagonal is 26.4 m.  Every arrival body
# therefore fits inside 30 m of its node, and MEASURED, every cluster this
# labels TERMINUS lies 0-27 m from a node while every cluster it labels
# THROUGH lies 46 m or more from the nearer one.  There is no case in the
# band between, so the number is not load-bearing to a metre.
TERMINUS_M = 30.0


def census_segment(seg: dict, objects: list[dict], claims: Claims) -> dict:
    """Per-object band, claim, class and position along the alignment."""
    nodes = np.asarray(seg["nodes"], dtype=np.float64)
    prof = np.asarray(seg["profile_y"], dtype=np.float64)
    terr = np.asarray(seg["terrain_y"], dtype=np.float64)
    br = np.asarray(seg["is_bridge"], dtype=bool)
    per = written_footprint(seg)
    half = seg["width_m"] / 2.0
    # Cumulative along-track metres per station, from the polyline itself
    # rather than from `station_m`: the nodes are not exactly evenly spaced at
    # the turns, so multiplying the index by 2.0 m misplaces a cluster by
    # metres over a kilometre.
    seglen = np.hypot(np.diff(nodes[:, 0]), np.diff(nodes[:, 1]))
    along = np.concatenate(([0.0], np.cumsum(seglen)))
    total = float(along[-1])
    hits: list[dict] = []
    for o in objects:
        cls = classify(o["prefab"])
        if cls not in ("STRUCTURE", "FIXTURE"):
            continue
        r = RB.lateral_and_y(nodes, prof, br, o["x"], o["z"])
        if r is None:
            continue
        lat, road_y = float(r[0]), float(r[1])
        st = int(((nodes[:, 0] - o["x"]) ** 2
                  + (nodes[:, 1] - o["z"]) ** 2).argmin())
        if br[st]:
            continue
        b = band(lat, half, RB.SHOULDER_M, float(per[st]))
        if b is None:
            continue
        who, site = claims.resolve(o["x"], o["z"])
        s_m = float(along[st])
        d_end = min(math.hypot(o["x"] - nodes[0, 0], o["z"] - nodes[0, 1]),
                    math.hypot(o["x"] - nodes[-1, 0], o["z"] - nodes[-1, 1]))
        hits.append({
            "prefab": o["prefab"], "cls": cls, "band": b,
            "x": round(o["x"], 2), "y": round(o["y"], 2), "z": round(o["z"], 2),
            "lat_m": round(lat, 2), "station": st,
            "along_m": round(s_m, 1),
            "from_node_m": round(d_end, 1),
            "at_terminus": d_end <= TERMINUS_M,
            "road_y": round(road_y, 3),
            "dy_m": round(o["y"] - road_y, 2),
            "claim": who, "site_id": site,
            "cut_fill_m": round(float(prof[st] - terr[st]), 2)})
    return {"segment": seg["id"], "length_m": round(total, 1), "hits": hits}


def roll_up(cen: dict) -> dict:
    out: dict = {}
    for h in cen["hits"]:
        key = (h["claim"], h["site_id"])
        e = out.setdefault(key, {
            "claim": h["claim"], "site_id": h["site_id"],
            "bands": {}, "prefabs": {}, "lat_min": 9e9, "lat_max": 0.0,
            "stations": set(), "along": [], "n": 0, "n_terminus": 0})
        e["n"] += 1
        e["n_terminus"] += 1 if h["at_terminus"] else 0
        e["bands"][h["band"]] = e["bands"].get(h["band"], 0) + 1
        e["prefabs"][h["prefab"]] = e["prefabs"].get(h["prefab"], 0) + 1
        e["lat_min"] = min(e["lat_min"], h["lat_m"])
        e["lat_max"] = max(e["lat_max"], h["lat_m"])
        e["stations"].add(h["station"])
        e["along"].append(h["along_m"])
    for e in out.values():
        s = sorted(e.pop("stations"))
        e["station_min"], e["station_max"] = s[0], s[-1]
        al = sorted(e.pop("along"))
        e["along_min_m"], e["along_max_m"] = al[0], al[-1]
        e["run_m"] = round(al[-1] - al[0], 1)
        # THE VERDICT IS THE WHOLE CLUSTER'S, not the piece's: one piece of a
        # body inside the terminus window does not make the body a
        # destination, and one piece outside it does not make a destination a
        # through case.  The cluster is at the terminus when EVERY piece of it
        # is, which is the conservative reading -- it can only ever move a
        # case INTO the reported defect list.
        e["verdict_scope"] = ("TERMINUS" if e["n_terminus"] == e["n"]
                              else "THROUGH")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--census-dir", default="/tmp/roadclean")
    ap.add_argument("--out", default="/tmp/roadclean/structures_network.json")
    ap.add_argument("--segment", action="append")
    a = ap.parse_args()

    segs = segment_map()
    claims = Claims()
    cdir = Path(a.census_dir)
    # THE CENSUS FILE PER SEGMENT, named explicitly.  A glob would silently
    # pick a stale `_after` over a later `close_`, and an existence check on a
    # directory is not a completeness check on 15 segments.
    files = {}
    for sid in WRITTEN:
        short = sid.split("-")[0]
        for cand in (f"close_census_{short}.json", f"census_{short}_after.json",
                     f"pass_census_{short}_after.json"):
            p = cdir / cand
            if p.exists():
                files[sid] = p
                break
    missing = [s for s in WRITTEN if s not in files]
    if missing:
        print(f"NO CENSUS for {missing} -- refusing to report a network-wide "
              f"number over {len(files)}/15 segments", file=sys.stderr)
        return 2

    # EVERY census's markers registered BEFORE any segment is resolved.  A
    # body straddling two segments' corridors is found by both, and the
    # marker may only fall in one of the two cell sets: resolving T5 against
    # T5's markers alone answered LOOSE for the house at (-46, -68) that
    # T13's census sees the proxy for.
    for sid in WRITTEN:
        claims.add_proxies(json.loads(files[sid].read_text())["objects"])

    want = set(a.segment or WRITTEN)
    report = {"world": "Ulfsland", "segments": {},
              "instrument": "roads/structures.py", "sources": {},
              "location_markers": len(claims.locs)}
    for sid in WRITTEN:
        if sid not in want:
            continue
        doc = json.loads(files[sid].read_text())
        assert doc["segment"] == sid, (doc["segment"], sid)
        cen = census_segment(segs[sid], doc["objects"], claims)
        ru = roll_up(cen)
        report["sources"][sid] = str(files[sid])
        report["segments"][sid] = {
            "objects_censused": len(doc["objects"]),
            "length_m": cen["length_m"],
            "hits": cen["hits"],
            "by_claim": [
                {k: (sorted(v.items(), key=lambda kv: -kv[1])
                     if k == "prefabs" else v)
                 for k, v in e.items()}
                for e in sorted(ru.values(), key=lambda e: -e["n"])],
        }
    Path(a.out).write_text(json.dumps(report, indent=1))

    print(f"{'segment':28s} {'cway':>5s} {'shld':>5s} {'erth':>5s}  "
          f"{'thru-cway':>9s}  claims (T=terminus, X=through)")
    tot = {"CARRIAGEWAY": 0, "SHOULDER": 0, "EARTHWORK": 0}
    thru_tot = 0
    for sid, s in report["segments"].items():
        b = {"CARRIAGEWAY": 0, "SHOULDER": 0, "EARTHWORK": 0}
        for h in s["hits"]:
            b[h["band"]] += 1
        for k in b:
            tot[k] += b[k]
        thru = sum(e["bands"].get("CARRIAGEWAY", 0) for e in s["by_claim"]
                   if e["verdict_scope"] == "THROUGH")
        thru_tot += thru
        # "TERM" / "THRU", never the first letter: `TERMINUS` and `THROUGH`
        # both start with T, and a one-letter tag printed the two opposite
        # verdicts identically -- a table that says every T4 cluster is a
        # terminus while its own through-count reads 280.
        who = ", ".join(
            f"{e['site_id']}/{e['claim'][0]}-"
            f"{'TERM' if e['verdict_scope'] == 'TERMINUS' else 'THRU'}"
            f":{e['bands'].get('CARRIAGEWAY', 0)}c/{e['n']}"
            f"@{e['along_min_m']:.0f}-{e['along_max_m']:.0f}m"
            for e in s["by_claim"][:6])
        print(f"{sid:28s} {b['CARRIAGEWAY']:5d} {b['SHOULDER']:5d} "
              f"{b['EARTHWORK']:5d}  {thru:9d}  {who}")
    print(f"{'NETWORK':28s} {tot['CARRIAGEWAY']:5d} {tot['SHOULDER']:5d} "
          f"{tot['EARTHWORK']:5d}  {thru_tot:9d}")
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
