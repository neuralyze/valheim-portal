#!/usr/bin/env python3
"""The Ulfsland road network specification: nodes and edges, with provenance.

THE MAIN CONTINENT IS SPAWN ISLAND + WEST ISLE, JOINED BY TWO BRIDGES.  That
sentence exists to stop a future reader re-litigating the archipelago question.
MEASURED (tools/jumpstart/roads/archipelago.py, script, no shared state): the
largest land component in the entire world on seed Pirate68 is 5.4559 km2 at 16 m
cells, bounding box x[-1520, 1904] z[-928, 2720], 50 % Meadows / 43 %
BlackForest -- and that box is exactly the union of spawn island
(x[-326, 1920] z[-938, 2128], 2.7754 km2 at 1 m) and west isle
(x[-1534, 12] z[-322, 2700], 2.6343 km2 at 1 m).  The two read as ONE landmass
at 16 m because the only thing between them is a 12.0 m channel and a 34 m
channel, both MEASURED by `Crossings` at 1 m off the same field.  So the
operator's "main continent" is not missing; it is two islands and two bridges,
and it already holds spawn, StartTemple, the portal hall, the mead hall, the
station hub and the dock.

COMPONENT COUNTS ARE A STATEMENT ABOUT CELL SIZE, NOT ABOUT THE WORLD.  MEASURED
sweep at the same threshold (h > 30.5 against `ZoneSystem::c_WaterLevel` = 30.0)
and the same permissive 8-connectivity and MAX-pooled decimation:
    region patch x[-4750,4750] z[-5000,4500]:  4 m -> 7,794 components
                                               8 m -> 1,991
                                              16 m ->   294
    whole world:                              16 m -> 4,773
                                              32 m ->   824
                                              64 m ->   162
Quote the resolution with the number or do not quote the number.

COORDINATE PROVENANCE, per node kind:
  installation  `solved` centre from tools/jumpstart/worlds/Ulfsland/*/
                placements.yaml at commit 8d0eab4.  MEASURED for this seed and
                unchanged by the world rebuild (same seed Pirate68 / 147627509).
  spawn_point   StartTemple from Settlements' MOD-FREE location dump
                /tmp/settle/loc2/f6fe167f4fcd.json.  MEASURED.
  abutment      MEASURED by `Crossings` via a Euclidean distance transform
                between the two islands' land masks at 1 m, snapped by agreement
                to 2 m multiples (its deck pitch: wood_floor is a
                2.000 x 0.130 x 2.000 collider solid).
  extremity     MEASURED by tools/jumpstart/roads/survey.py: the farthest sample
                from the landmass centroid in each 45 deg sector that is at
                least 25 m from any water edge.  The inland condition is the
                whole point -- the farthest sample outright is a one-pixel spit,
                and a road that ends on a spit ends in the sea.
"""

from __future__ import annotations

# The water-crossing datum.  `Crossings` proposed 30.6 because the existing
# early-dock placement already declares target_y 30.6 as "a dock DECK, not a sea
# floor", so every deck in the world -- bridge and dock alike -- sits at one
# height and no join has a step in it.  Adopted rather than picking a second
# number.  Water plane is 30.0 (MEASURED, ZoneSystem::c_WaterLevel).
DECK_DATUM_Y = 30.6

NODES: dict[str, dict] = {
    # ---- spawn island (the east half of the main continent) ----
    "temple": {
        "island": "spawn", "patch": "mainland", "xz": (-64.68, 3.31),
        "kind": "spawn_point", "provenance": "MEASURED, Settlements loc2 dump",
        "why": "StartTemple (-64.68, 75.05, 3.31): where any character without a "
               "bed respawns, 314 m from the portal hall with nothing pointing at "
               "it. A road from here is the physical half of WayFinding's fix. "
               "It is also a PROTECTED location with exteriorRadius 25, so the "
               "road terminates on its clearance ring, not in its courtyard.",
    },
    "portalhub": {
        "island": "spawn", "patch": "mainland", "xz": (-292.5, 213.5),
        "kind": "installation", "provenance": "MEASURED, placements.yaml 8d0eab4",
        "why": "sandbox-portal-hub, the portal hall: the hub every portal arrival "
               "lands at.",
    },
    "meadhall": {
        "island": "spawn", "patch": "mainland", "xz": (303.5, 385.5),
        "kind": "installation", "provenance": "MEASURED, placements.yaml 8d0eab4",
        "why": "meadows-starter-hall",
    },
    "dockshore": {
        "island": "spawn", "patch": "mainland", "xz": (675.4, -85.6),
        "kind": "shoreline", "provenance": "MEASURED, placements.yaml 8d0eab4",
        "why": "early-dock / sandbox-harbour solved to water_dist_m 0.0. The ROAD "
               "stops on the shoreline; the pier is Crossings' over-water "
               "structure and MUST NOT be flattened under -- levelling the water "
               "out from under a pier is the defect the operator condemned.",
    },
    "stathub": {
        "island": "spawn", "patch": "mainland", "xz": (661.5, 1088.5),
        "kind": "installation", "provenance": "MEASURED, placements.yaml 8d0eab4",
        "why": "complete-station-hub",
    },
    "brg-s1-e": {
        "island": "spawn", "patch": "mainland", "xz": (-312.0, -64.0),
        "kind": "abutment", "provenance": "MEASURED by Crossings, snapped to 2 m",
        "level_y": DECK_DATUM_Y,
        "why": "east abutment of S1, the 12.0 m channel between the two halves of "
               "the main continent. Crossings measured bank ground_y 30.72, "
               "channel max depth 2.52 m, bed 27.48, bearing 270.",
    },
    "brg-s2-e": {
        "island": "spawn", "patch": "mainland", "xz": (-318.0, 230.0),
        "kind": "abutment", "provenance": "MEASURED by Crossings, snapped to 2 m",
        "level_y": DECK_DATUM_Y,
        "why": "east abutment of S2, the 34 m channel. 30 m from the portal hall, "
               "so the spur to it is nearly free and it closes a LOOP with S1 "
               "instead of leaving an out-and-back.",
    },
    "northcape": {
        "island": "spawn", "patch": "mainland", "xz": (758.0, 2090.0),
        "kind": "extremity", "provenance": "MEASURED, survey.py sector 0",
    },
    "eastlobe": {
        "island": "spawn", "patch": "mainland", "xz": (1728.0, 1162.0),
        "kind": "extremity", "provenance": "MEASURED, survey.py sector 90",
    },
    "northeast": {
        "island": "spawn", "patch": "mainland", "xz": (1822.0, 1586.0),
        "kind": "extremity", "provenance": "MEASURED, survey.py sector 45",
    },
    "southeast": {
        "island": "spawn", "patch": "mainland", "xz": (1148.0, 474.0),
        "kind": "extremity", "provenance": "MEASURED, survey.py sector 135",
    },
    "southcape": {
        "island": "spawn", "patch": "mainland", "xz": (72.0, -862.0),
        "kind": "extremity", "provenance": "MEASURED, survey.py sector 180",
    },
    # ---- west isle (the west half of the main continent) ----
    "brg-s1-w": {
        "island": "west", "patch": "westisle", "xz": (-325.0, -64.0),
        "kind": "abutment", "provenance": "MEASURED by Crossings; first DRY sample "
                                          "west of its (-324, -64) snap, found by "
                                          "survey probe",
        "level_y": DECK_DATUM_Y,
        "why": "west abutment of S1. Crossings' snapped bank (-324, -64) samples "
               "at h 29.48 on the westisle lattice; the first land sample is "
               "x = -325 at h 31.08, so the ribbon starts there and the deck "
               "covers the extra metre.",
    },
    "brg-s2-w": {
        "island": "west", "patch": "westisle", "xz": (-349.0, 246.0),
        "kind": "abutment", "provenance": "MEASURED by Crossings; first DRY sample "
                                          "west of its (-348, 246) snap",
        "level_y": DECK_DATUM_Y,
        "why": "west abutment of S2, land at h 31.08.",
    },
    "w-north": {
        "island": "west", "patch": "westisle", "xz": (-186.0, 2582.0),
        "kind": "extremity", "provenance": "MEASURED, survey.py sector 0",
    },
    "w-mid": {
        "island": "west", "patch": "westisle", "xz": (-132.0, 1420.0),
        "kind": "extremity", "provenance": "MEASURED, survey.py sector 90",
    },
    "w-west": {
        "island": "west", "patch": "westisle", "xz": (-1288.0, 974.0),
        "kind": "extremity", "provenance": "MEASURED, survey.py sector 270",
    },
    "w-southwest": {
        "island": "west", "patch": "westisle", "xz": (-1258.0, 166.0),
        "kind": "extremity", "provenance": "MEASURED, survey.py sector 225",
    },
    "w-south": {
        "island": "west", "patch": "westisle", "xz": (-774.0, -290.0),
        "kind": "extremity", "provenance": "MEASURED, survey.py sector 180",
    },
    "w-northwest": {
        "island": "west", "patch": "westisle", "xz": (-1408.0, 2184.0),
        "kind": "extremity", "provenance": "MEASURED, survey.py sector 315",
    },
    # ---- outlying islands: portal-reached, local roads only ----
    "blacbase": {"island": "blackforest", "patch": "blackforest6",
                 "xz": (-3045.5, -4101.5), "kind": "installation",
                 "provenance": "MEASURED, placements.yaml 8d0eab4"},
    "plainsfm": {"island": "plains", "patch": "plains23", "xz": (750.5, -4278.5),
                 "kind": "installation", "provenance": "MEASURED, placements.yaml 8d0eab4"},
    "workshop": {"island": "workshop", "patch": "workshop3054", "xz": (-4673.5, -310.5),
                 "kind": "installation", "provenance": "MEASURED, placements.yaml 8d0eab4"},
    "mtnpost": {"island": "mountain", "patch": "mtn7114", "xz": (-7.5, 3939.5),
                "kind": "installation", "provenance": "MEASURED, placements.yaml 8d0eab4"},
}

# Build order is Main's, and it is the order the operator's value accrues in:
# spawn-island trunk first (every metre of it gets walked), then the west-isle
# network behind S1, then the outlying locals.
EDGES: list[dict] = [
    # priority 1 -- spawn-island trunk, in the order it can be walked
    {"id": "T1-portalhub-temple", "a": "portalhub", "b": "temple", "cls": "trunk", "pri": 1},
    {"id": "T2-temple-brgs1", "a": "temple", "b": "brg-s1-e", "cls": "trunk", "pri": 1,
     "why": "the operator's first walk out of StartTemple, ending on the S1 bridge"},
    {"id": "T3-temple-meadhall", "a": "temple", "b": "meadhall", "cls": "trunk", "pri": 1},
    {"id": "T4-meadhall-stathub", "a": "meadhall", "b": "stathub", "cls": "trunk", "pri": 1},
    {"id": "T5-temple-dockshore", "a": "temple", "b": "dockshore", "cls": "trunk", "pri": 1},
    {"id": "S1-portalhub-brgs2", "a": "portalhub", "b": "brg-s2-e", "cls": "spur", "pri": 1,
     "grade_max": 0.25,
     "why": "30 m ramp from the portal hall down onto the S2 bridge deck. The hall "
            "stands 6.8 m above the abutment 30 m away, so no 12 % profile reaches "
            "it -- interval propagation PROVED 2 stations infeasible at 12 %. The "
            "measured requirement is 22 %, which is under the network's 25 % ceiling "
            "and less than a third of the 38 deg (78 %) angle at which "
            "Character::GetSlideAngle makes a player slide. A short bridge ramp is "
            "the right answer here; a 60 m switchback to save 10 % of grade over 30 m "
            "is worse for someone on foot."},
    # priority 2 -- spawn island reach
    {"id": "T6-stathub-northcape", "a": "stathub", "b": "northcape", "cls": "trunk", "pri": 2},
    {"id": "S2-stathub-eastlobe", "a": "stathub", "b": "eastlobe", "cls": "spur", "pri": 2},
    {"id": "S3-eastlobe-northeast", "a": "eastlobe", "b": "northeast", "cls": "spur", "pri": 2},
    {"id": "S4-meadhall-southeast", "a": "meadhall", "b": "southeast", "cls": "spur", "pri": 2},
    {"id": "S5-temple-southcape", "a": "temple", "b": "southcape", "cls": "spur", "pri": 2},
    # priority 3 -- west isle
    # W1 (brg-s1-w -> brg-s2-w direct along the west shore) IS DELIBERATELY ABSENT.
    # MEASURED: routed, it came out 346 m of which 330 m was a single bridged run
    # over water deeper than the 1.0 m causeway line -- i.e. the two abutments
    # are 310 m apart along a shore with no land between them, and "closing the
    # loop" that way means a 330 m bridge, which is 3.4x Crossings' MEASURED 96 m
    # absolute span ceiling and a structure nobody asked for. The loop closes
    # INLAND instead, brg-s1-w -> w-south -> ... -> w-mid -> brg-s2-w, which is
    # longer to walk and is actually buildable.
    {"id": "W1-brgs1-wmid", "a": "brg-s1-w", "b": "w-mid", "cls": "trunk", "pri": 3,
     "why": "closes the loop across the west half INLAND, because the direct "
            "shore line between the two abutments is 330 m of open water"},
    {"id": "W2-brgs2-wmid", "a": "brg-s2-w", "b": "w-mid", "cls": "trunk", "pri": 3},
    {"id": "W3-wmid-wnorth", "a": "w-mid", "b": "w-north", "cls": "trunk", "pri": 3},
    {"id": "W4-wmid-wwest", "a": "w-mid", "b": "w-west", "cls": "trunk", "pri": 3},
    {"id": "W5-brgs1-wsouth", "a": "brg-s1-w", "b": "w-south", "cls": "spur", "pri": 3},
    {"id": "W6-wwest-wsouthwest", "a": "w-west", "b": "w-southwest", "cls": "spur", "pri": 3},
    {"id": "W7-wwest-wnorthwest", "a": "w-west", "b": "w-northwest", "cls": "spur", "pri": 3},
]

# Structures another agent owns at a node of mine.  DECLARED, with THEIR
# measurements, never re-derived here: if my numbers disagree with theirs at a
# crossing they have already measured, theirs win and mine are marked superseded.

# ---------------------------------------------------------------------------
# Settlements' sites, MEASURED by it at 1 m off a hardlink of this project's own
# PatchScan field (/tmp/settle/h1m.bin == /tmp/roads/h1m.bin), each pad
# clamp-checked and cleared against the per-type POI stand-off.  Published to me
# over `hub` as PROVISIONAL; taken verbatim, never re-derived, because a
# coordinate re-derived by the consumer is a second source of truth.
#
# `pad_radius_m` is Settlements' keep-out: my ribbon terminates at the pad edge
# and does not pave inside it, because inside it is its earthwork and a road
# levelling ground under a building is the defect class this build exists to
# avoid.
# ---------------------------------------------------------------------------
SETTLEMENT_SITES: dict[str, dict] = {
    "stenvik":   {"type": "town",       "xz": (496.0, 904.0),    "island": "spawn", "patch": "mainland",
                  "pad_radius_m": 100.0, "priority": "trunk", "tag": "u-stenvik", "pad_y": 45.57},
    "vestvik":   {"type": "village",    "xz": (-906.0, 1846.0),  "island": "west",  "patch": "westisle",
                  "pad_radius_m": 100.0, "priority": "trunk", "tag": "u-vestvik", "pad_y": 33.99},
    "hognest":   {"type": "castle",     "xz": (530.0, 1348.0),   "island": "spawn", "patch": "mainland",
                  "pad_radius_m": 32.0,  "priority": "spur",  "tag": "u-castle",  "pad_y": 110.95},
    "wtspawn":   {"type": "watchtower", "xz": (110.0, -104.0),   "island": "spawn", "patch": "mainland",
                  "pad_radius_m": 12.0,  "priority": "trunk", "tag": "u-wtspawn", "pad_y": 76.03},
    "wtsouth":   {"type": "watchtower", "xz": (538.0, 40.0),     "island": "spawn", "patch": "mainland",
                  "pad_radius_m": 12.0,  "priority": "trunk", "tag": "u-wtsouth", "pad_y": 82.99},
    "wttown":    {"type": "watchtower", "xz": (820.0, 738.0),    "island": "spawn", "patch": "mainland",
                  "pad_radius_m": 12.0,  "priority": "trunk", "tag": "u-wttown",  "pad_y": 39.86},
    "wtpeak":    {"type": "watchtower", "xz": (1418.0, 1530.0),  "island": "spawn", "patch": "mainland",
                  "pad_radius_m": 12.0,  "priority": "spur",  "tag": "u-wtpeak",  "pad_y": 103.64},
    "wtnorth":   {"type": "watchtower", "xz": (152.0, 1194.0),   "island": "spawn", "patch": "mainland",
                  "pad_radius_m": 12.0,  "priority": "trunk", "tag": "u-wtnorth", "pad_y": 32.80},
    "wteast":    {"type": "watchtower", "xz": (1858.0, 146.0),   "island": "spawn", "patch": "mainland",
                  "pad_radius_m": 12.0,  "priority": "spur",  "tag": "u-wteast",  "pad_y": 55.44},
    "lhsouth":   {"type": "lighthouse", "xz": (-102.0, -916.0),  "island": "spawn", "patch": "mainland",
                  "pad_radius_m": 12.0,  "priority": "spur",  "tag": "u-lhsouth", "pad_y": 32.52},
    "lheast":    {"type": "lighthouse", "xz": (2354.0, 156.0),   "island": "spawn", "patch": "mainland",
                  "pad_radius_m": 12.0,  "priority": "none",  "tag": "u-lheast",  "pad_y": 31.48,
                  "note": "x=2354 is OUTSIDE the mainland patch (x <= 2398 but the "
                          "island's own bbox ends at 1920), so it is on a separate "
                          "landmass and gets no road. Portal only."},
    "lhwest":    {"type": "lighthouse", "xz": (-1350.0, 2358.0), "island": "west",  "patch": "westisle",
                  "pad_radius_m": 12.0,  "priority": "spur",  "tag": "u-lhwest",  "pad_y": 33.01},
    "treesth":   {"type": "treehouse",  "xz": (546.0, 482.0),    "island": "spawn", "patch": "mainland",
                  "pad_radius_m": 10.0,  "priority": "spur",  "tag": "u-treesth", "pad_y": 75.46},
    "treewest":  {"type": "treehouse",  "xz": (-530.0, 850.0),   "island": "west", "patch": "westisle",
                  "pad_radius_m": 10.0,  "priority": "spur",  "tag": "u-treewest", "pad_y": 41.39,
                  "note": "x=-530 is WEST of the spawn island's bbox (x >= -326), so "
                          "this sits on West Isle, not the spawn island. Routed from "
                          "the west-isle network, and Settlements told."},
    "treenear":  {"type": "treehouse",  "xz": (-614.0, -236.0),  "island": "west",  "patch": "westisle",
                  "pad_radius_m": 10.0,  "priority": "spur",  "tag": "u-treenear", "pad_y": 35.20},
}

for _sid, _s in SETTLEMENT_SITES.items():
    NODES[_sid] = {
        "island": _s["island"], "patch": _s["patch"], "xz": _s["xz"],
        "kind": _s["type"], "pad_radius_m": _s["pad_radius_m"],
        "portal_tag": _s["tag"],
        "provenance": "MEASURED by Settlements at 1 m, published over hub as PROVISIONAL",
        "why": _s.get("note", f"{_s['type']} sited by Settlements"),
    }

# Settlements' own recommended sweep loop, taken as the trunk ordering:
#   Stenvik -> wttown -> the dock -> wtsouth -> wtspawn -> StartTemple
# which the operator can walk out of spawn and back.
SETTLEMENT_EDGES: list[dict] = [
    {"id": "T7-stathub-stenvik",  "a": "stathub",  "b": "stenvik",  "cls": "trunk", "pri": 1},
    {"id": "T8-stenvik-wttown",   "a": "stenvik",  "b": "wttown",   "cls": "trunk", "pri": 1},
    {"id": "T9-wttown-dockshore", "a": "wttown",   "b": "dockshore", "cls": "trunk", "pri": 1},
    # T10 (dockshore -> wtsouth direct) IS DELIBERATELY ABSENT.  MEASURED: the
    # dock's solved centre (675.4, -85.6) has NO land within 6 m -- it is over
    # water by 7.0 m to a 0.031 km2 ISLET, not on the main island -- so a direct
    # dock -> watchtower road needs 168 m of open water.  Settlements' requested
    # sweep loop still closes, one node longer: dockshore -> temple (T5) ->
    # wtspawn (T12) -> wtsouth (T11), all on land.
    {"id": "T10-wtspawn-wtsouth", "a": "wtspawn", "b": "wtsouth", "cls": "trunk", "pri": 1,
     "why": "replaces the direct dock -> wtsouth link, which needs 168 m of "
            "open water because the dock is on an islet"},
    {"id": "T11-wtsouth-wtspawn", "a": "wtsouth",  "b": "wtspawn",  "cls": "trunk", "pri": 1},
    {"id": "T12-wtspawn-temple",  "a": "wtspawn",  "b": "temple",   "cls": "trunk", "pri": 1},
    {"id": "S6-stenvik-hognest",  "a": "stenvik",  "b": "hognest",  "cls": "mountain", "pri": 2,
     "why": "castle switchback; Settlements accepts a spur terminating below the summit"},
    {"id": "S7-stathub-wtnorth",  "a": "stathub",  "b": "wtnorth",  "cls": "spur", "pri": 2},
    {"id": "S8-wtsouth-treesth",  "a": "wtsouth",  "b": "treesth",  "cls": "spur", "pri": 2},
    # S9 re-origined from `northeast`, not `eastlobe`.  MEASURED: wtpeak
    # (1418, 1530) IS on the main land component, but A* found no admissible
    # path from eastlobe (1728, 1162) -- the corridor between them is either
    # steeper than the 25 % ceiling everywhere or inside a protected keep-out.
    # `northeast` (1822, 1586) is 415 m away on the same component and routes.
    {"id": "S9-northeast-wtpeak", "a": "northeast", "b": "wtpeak", "cls": "spur", "pri": 2},
    # S10 IS DELIBERATELY ABSENT.  MEASURED: watchtower `wteast` (1858, 146)
    # sits on land component 224, 0.3955 km2 -- a SEPARATE ISLAND from the spawn
    # island's component 5.  Routed anyway it produced a 562 m bridge.  It is
    # portal-only and Settlements has been told.
    {"id": "S11-southcape-lhsouth", "a": "southcape", "b": "lhsouth", "cls": "spur", "pri": 2},
    {"id": "W8-brgs2-vestvik",    "a": "brg-s2-w", "b": "vestvik",  "cls": "trunk", "pri": 3},
    {"id": "W9-vestvik-lhwest",   "a": "vestvik",  "b": "lhwest",   "cls": "spur", "pri": 3},
    {"id": "W10-brgs1-treenear",  "a": "brg-s1-w", "b": "treenear", "cls": "spur", "pri": 3},
    {"id": "W11-wmid-treewest",   "a": "w-mid",    "b": "treewest", "cls": "spur", "pri": 3},
]
EDGES.extend(SETTLEMENT_EDGES)

# Outlying islands: portal-reached, so a LOCAL road only.  Each installation is
# joined to the two farthest inland points of its own landmass, which gives the
# operator something to walk after stepping out of a portal instead of a stub.
OUTLYING_NODES: dict[str, tuple[float, float]] = {}


# ---------------------------------------------------------------------------
# Crossings' waterfront structures that a road has to ARRIVE at, with the
# approach bearings it asked for.  MEASURED by Crossings (scipy label over the
# 1 m land mask of the same PatchScan field, script
# tools/jumpstart/crossings/survey.py) and taken verbatim.
#
# The approach bearing is not decoration.  A jetty runs out on one bearing and a
# spur arriving on the RECIPROCAL puts the operator at the landward end facing
# down the deck; arriving perpendicular puts them walking onto it sideways,
# which is a thing they would see on the first step.
#
# The ribbon width per terminal is Crossings' deck width, not my road class
# default: bringing a 6 m trunk ribbon to a 4 m jetty CREATES the pinch that
# matching widths avoids.
# ---------------------------------------------------------------------------
CROSSING_TERMINALS: dict[str, dict] = {
    "harbour-south":   {"xz": (11.5, -258.5),    "island": "spawn", "patch": "mainland",
                        "arrive_bearing_deg": 45.0,  "width_m": 6.0, "cls": "trunk",
                        "jetty_bearing_deg": 225.0, "owner": "Crossings"},
    "dock-stathub":    {"xz": (502.5, 1062.5),   "island": "spawn", "patch": "mainland",
                        "arrive_bearing_deg": 295.0, "width_m": 4.0, "cls": "spur",
                        "jetty_bearing_deg": 115.0, "owner": "Crossings"},
    "boathouse-strait": {"xz": (-303.5, -99.5),  "island": "spawn", "patch": "mainland",
                        "arrive_bearing_deg": 10.0,  "width_m": 4.0, "cls": "spur",
                        "jetty_bearing_deg": 190.0, "owner": "Crossings"},
    "ferry-east":      {"xz": (1746.5, 655.5),   "island": "spawn", "patch": "mainland",
                        "arrive_bearing_deg": 290.0, "width_m": 4.0, "cls": "spur",
                        "jetty_bearing_deg": 110.0, "owner": "Crossings",
                        "keep_clear": [(1745.47, 654.47, 1.0)],
                        "note": "sign and post stand 3 m inland on bearing 290; the "
                                "ribbon centreline keeps 1 m clear of it"},
    "harbour-vestvik": {"xz": (-1031.5, 1656.5), "island": "west", "patch": "westisle",
                        "arrive_bearing_deg": 125.0, "width_m": 6.0, "cls": "trunk",
                        "jetty_bearing_deg": 305.0, "owner": "Crossings",
                        "note": "Settlements' Vestvik is 227.3 m away: Crossings "
                                "MEASURED that Vestvik's own 94 m shoreline is too "
                                "shallow for a 22 m pier at any of 72 bearings, so "
                                "the quay is here and the road joins the two"},
}

for _tid, _t in CROSSING_TERMINALS.items():
    NODES[_tid] = {
        "island": _t["island"], "patch": _t["patch"], "xz": _t["xz"],
        "kind": "waterfront_terminal", "owner": _t["owner"],
        "arrive_bearing_deg": _t["arrive_bearing_deg"],
        "jetty_bearing_deg": _t["jetty_bearing_deg"],
        "ribbon_width_m": _t["width_m"],
        "keep_clear": _t.get("keep_clear", []),
        "provenance": "MEASURED by Crossings, published over hub",
        "why": _t.get("note", "waterfront terminal owned by Crossings"),
    }

# ferry-terminal-eastisle is DELIBERATELY ABSENT from the road network.
# MEASURED by Crossings: its root (1951.5, 556.5) sits on land component 273,
# 0.396 km2, bbox x[1655.5,2397.5] z[-117.5,1048.5], 99 % BlackForest -- a
# SEPARATE ISLAND from the spawn island's component 7.  So it is portal-only and
# the operator reaches it by portal and rides the ferry BACK, which on a world
# with `portals_casual` is the only way a ferry gets used at all.  Recorded here
# because "why is there no road to the far ferry terminal" must have an answer.
NO_ROAD: dict[str, str] = {
    "wteast": "watchtower on land component 224 (0.3955 km2), a separate island "
              "from the spawn island; portal only -- MEASURED, and routed anyway "
              "it needs a 562 m bridge",
    "dockshore_direct_to_wtsouth": "the dock's solved centre is over water, 7.0 m "
                                   "from a 0.031 km2 islet; a direct road to the "
                                   "south watchtower needs 168 m of open water",
    "ferry-terminal-eastisle": "on land component 273 (0.396 km2), a separate "
                               "island from the spawn island; portal only",
    "lheast": "x=2354 is beyond the spawn island's own bbox (x <= 1920); separate "
              "landmass, portal only",
}

TERMINAL_EDGES: list[dict] = [
    {"id": "T13-temple-harboursouth", "a": "temple", "b": "harbour-south",
     "cls": "trunk", "pri": 1, "width_m": 6.0,
     "why": "the harbour on the temple's own south shore; trunk because its deck "
            "is 6 m"},
    {"id": "S12-brgs1-boathouse", "a": "brg-s1-e", "b": "boathouse-strait",
     "cls": "spur", "pri": 1, "width_m": 4.0,
     "why": "36 m from the S1 east abutment, so almost free off the same trunk"},
    {"id": "S13-stathub-dock", "a": "stathub", "b": "dock-stathub",
     "cls": "spur", "pri": 2, "width_m": 4.0},
    {"id": "S14-eastlobe-ferryeast", "a": "eastlobe", "b": "ferry-east",
     "cls": "spur", "pri": 2, "width_m": 4.0},
    {"id": "W12-vestvik-harbour", "a": "vestvik", "b": "harbour-vestvik",
     "cls": "trunk", "pri": 3, "width_m": 6.0},
]
EDGES.extend(TERMINAL_EDGES)

DECLARED_CROSSINGS: list[dict] = [
    {
        "crossing_id": "S1", "owner": "Crossings", "status": "declared",
        "measured_by": "Crossings, 1 m distance transform over /tmp/roads/h1m.bin",
        "joins": ["spawn", "west"],
        "bank_a": {"node": "brg-s1-e", "xz": [-312.0, -64.0], "ground_y": 30.72,
                   "road_deck_y": DECK_DATUM_Y, "bearing_deg": 270.0},
        "bank_b": {"node": "brg-s1-w", "xz": [-324.0, -64.0], "ground_y": 30.64,
                   "road_deck_y": DECK_DATUM_Y, "bearing_deg": 90.0},
        "span_m": 12.0, "gap_min_y": 27.48, "water_surface_y": 30.0,
        "why_terrain_failed": "water_body",
        "significance": "the join between the two halves of the largest landmass "
                        "in the world; 260 m from StartTemple",
        "poi_clear": "nearest WoodHouse7 at 47.9 m (exteriorRadius 8); "
                     "Eikthyrnir altar 146.5 m -- MEASURED by Crossings",
    },
    {
        "crossing_id": "S2", "owner": "Crossings", "status": "declared",
        "measured_by": "Crossings, 1 m distance transform over /tmp/roads/h1m.bin",
        "joins": ["spawn", "west"],
        "bank_a": {"node": "brg-s2-e", "xz": [-318.0, 230.0], "ground_y": 30.26,
                   "road_deck_y": DECK_DATUM_Y, "bearing_deg": 298.07},
        "bank_b": {"node": "brg-s2-w", "xz": [-348.0, 246.0], "ground_y": 30.02,
                   "road_deck_y": DECK_DATUM_Y, "bearing_deg": 118.07},
        "span_m": 34.0, "gap_min_y": 25.14, "water_surface_y": 30.0,
        "why_terrain_failed": "water_body",
        "significance": "second join; makes the network a loop rather than an "
                        "out-and-back",
        "poi_clear": "nearest WoodHouse5 at 46.1 m (exteriorRadius 8) -- MEASURED "
                     "by Crossings",
    },
]
