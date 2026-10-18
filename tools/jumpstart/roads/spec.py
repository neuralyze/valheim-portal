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
  abutment      DERIVED PER SEED by `geography.py` from `crossings/survey.py`:
                a Euclidean distance transform between the two trunk landmasses'
                land masks at 1 m, snapped to 2 m multiples (Crossings' deck
                pitch: wood_floor is a 2.000 x 0.130 x 2.000 collider solid),
                then walked outward to the first sample above the water plane.
  extremity     DERIVED PER SEED by `geography.py` from `roads/survey.py`: the
                farthest sample from the landmass centroid in each 45 deg sector
                that is at least 25 m from any water edge.  The inland condition
                is the whole point -- the farthest sample outright is a
                one-pixel spit, and a road that ends on a spit ends in the sea.

  The two DERIVED kinds were literals here until 2026-09-21 and reproduce to the
  last decimal; see the note above `NODES.update(DERIVED_GEOGRAPHY)`.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import yaml

_HERE = Path(__file__).resolve().parent
# APPEND the jumpstart root, never insert it: `tools/jumpstart/network/` is a
# package that shadows `roads/network.py` for every later importer if it gets
# in front of this directory. All this root has to supply is `worldseed`.
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
if str(_HERE.parent) not in sys.path:
    sys.path.append(str(_HERE.parent))

import declared_sites as DS  # noqa: E402  the owner's declared sites, as nodes
import installation_sites  # noqa: E402
import geography  # noqa: E402
import worldseed  # noqa: E402

# The water-crossing datum, DEFINED IN `geography.py` because that is what
# stamps it onto each derived abutment's `level_y`; re-exported here because
# this module is where a reader looks for it.  `Crossings` proposed 30.6
# because the existing early-dock placement already declares target_y 30.6 as
# "a dock DECK, not a sea floor", so every deck in the world -- bridge and dock
# alike -- sits at one height and no join has a step in it.  Adopted rather
# than picking a second number.  Water plane is 30.0 (MEASURED,
# ZoneSystem::c_WaterLevel).
DECK_DATUM_Y = geography.DECK_DATUM_Y

NODES: dict[str, dict] = {
    # ---- spawn island (the east half of the main continent) ----
    # `temple` IS NOT HERE either: it is a GENERATOR LOCATION (StartTemple) and
    # is read out of this seed's own location dump below, like the extremities
    # and the abutments.
    "portalhub": {
        "island": "spawn", "patch": "mainland",
        "kind": "installation",
        "why": "sandbox-portal-hub, the portal hall: the hub every portal arrival "
               "lands at.",
    },
    "meadhall": {
        "island": "spawn", "patch": "mainland",
        "kind": "installation",
        "why": "meadows-starter-hall",
    },
    "dockshore": {
        "island": "spawn", "patch": "mainland",
        "kind": "shoreline",
        "why": "early-dock / sandbox-harbour solved to water_dist_m 0.0. The ROAD "
               "stops on the shoreline; the pier is Crossings' over-water "
               "structure and MUST NOT be flattened under -- levelling the water "
               "out from under a pier is the defect the operator condemned.",
    },
    "stathub": {
        "island": "spawn", "patch": "mainland",
        "kind": "installation",
        "why": "complete-station-hub",
    },
    # ---- the 11 extremity and 4 abutment nodes ARE NOT HERE ----
    # They are GEOGRAPHY, they are DERIVED PER SEED, and they are merged in
    # below from `geography.load()`.  See the note above `NODES.update`.
    # ---- outlying islands: portal-reached, local roads only ----
    "blacbase": {"island": "blackforest", "patch": "blackforest6",
                 "kind": "installation"},
    "plainsfm": {"island": "plains", "patch": "plains23",
                 "kind": "installation"},
    "workshop": {"island": "workshop", "patch": "workshop3054",
                 "kind": "installation"},
    "mtnpost": {"island": "mountain", "patch": "mtn7114",
                "kind": "installation"},
}

# ---------------------------------------------------------------------------
# THE 8 AUTHORED NODES ABOVE ARE AUTHORED IN *WHICH*, NOT IN *WHERE*.
#
# "AUTHORED DESIGN, legitimately frozen" was true of the node IDS and was let to
# carry the COORDINATES, which it never covered.  MEASURED 2026-09-22 by driving
# seed Fresh42 end to end: all 8 came out as Ulfsland's coordinates VERBATIM TO
# THE DECIMAL, 11 of 20 planned segments terminated at one, and that is 12,849
# of 21,938 m -- 59% of the paved metreage.  T1-portalhub-temple ran 830.3 m
# from (-292.1, 213.2), a point chosen for a different planet, to Fresh42's OWN
# derived temple at (-24.1, -30.8): ONE SEGMENT, TWO WORLDS.  The run exited 0,
# stamped `seed: Fresh42`, and reported 0 POI violations.
#
# THE DECLARATION EXISTED AND THE GUARD EXISTED; THE READER DID NOT.
# `worlds/<World>/<preset>/placements.yaml` declares `world` and `seed` and is
# already scope-checked where it is read (terraform/flatten.py:128,
# blueprints/solve_placements.py:715).  `spec.py` had never opened it: these 8
# numbers were transcribed out of it at commit 8d0eab4 and frozen.  Same shape
# as `forbidden_footprints()` -- measured, wired to one consumer, not the other.
#
# SO THE COORDINATE IS READ FOR THE SEED BEING PLANNED, and NOTHING IS KEPT AS
# A FALLBACK.  There used to be an `ANCHORED_INSTALLATIONS` table -- the 8
# coordinates the default seed's placement set had solved, copied out of it --
# answered when a world had no placement set.  Stage 7 solves these for every
# seed, the default pair included, so the copy only ever answered a question
# stage 7 had not been asked yet; it was frozen solver output, and it is gone.
#
# `dockshore` IS `shoreline`, NOT `installation`, AND IS IN THE TABLE ANYWAY.
# Its whole point is `water_dist_m` 0.0 -- a road terminus ON the shore with
# Crossings' pier over the water -- so a table keyed on `kind` drops it and
# takes T5 and T9 with it.  The table is keyed on the PLACEMENT, which is the
# thing that actually supplies the number.
#
# THE `patch` AND `island` FIELDS STAY AUTHORED and are NOT read from the
# placement set, which does not carry them.  They are ROLE names resolved
# through `worldseed.FUNCTION_OF`, so `mainland` reaches a fresh seed's
# `trunk-primary`; that indirection is why they survive a change of planet and
# the coordinates did not.
# ---------------------------------------------------------------------------



for _nid, _xz in installation_sites.installation_xz().items():
    NODES[_nid]["xz"] = _xz
    NODES[_nid]["provenance"] = (
        "MEASURED, worlds/<world>/*/placements.yaml solved centre for this seed")

# ---------------------------------------------------------------------------
# THE 15 GEOGRAPHY NODES.  DERIVED PER SEED, NOT FROZEN HERE.
#
# WHAT THIS REPLACED AND WHY.  Until 2026-09-21 this block held 11 `extremity`
# and 4 `abutment` records with literal coordinates.  Each one's `provenance`
# named the tool that produced it -- "MEASURED, survey.py sector 0", "MEASURED
# by Crossings, snapped to 2 m" -- and BOTH of those tools already existed,
# already took a field as an argument, and were already seed-parameterised.
# Nothing called them.  The numbers had been read off their output ONCE and
# typed in, so a fresh seed's network terminated at Ulfsland's capes and
# crossed Ulfsland's channel.  Exactly the `sites.yaml` story below, one layer
# further down: the solver could already answer the question and nobody asked.
#
# `geography.py` is the call.  It runs `roads/survey.py::survey_patch` and
# `crossings/survey.py::approaches` against whatever field it is given, snaps
# to the ribbon's own 2 m pitch and writes `geography.yaml`, which is read here
# through `worldseed.require_scope` -- so a run on another seed is REFUSED
# rather than silently handed this world's ground.
#
# MEASURED 2026-09-21: the derivation reproduces all 15 of the literals it
# replaces EXACTLY, to the last decimal, on seed Pirate68 against
# /tmp/roads/h1m.bin.  The hand-copying was faithful. It just was not a
# function, and a constant cannot be evaluated at a different seed.
#
# WHAT STAYS AUTHORED.  The 7 `installation` nodes above, every node id, and
# all 40 `EDGES`.  An edge carries NO COORDINATE -- it is a topology over node
# IDS -- so the edge list survives any seed whose node ids survive, and the id
# scheme is what `geography.py` makes stable: an extremity is named for its
# island ROLE and its compass SECTOR, an abutment for its crossing ROLE and its
# bank SIDE, and none of those four things is a position.
# ---------------------------------------------------------------------------
DERIVED_GEOGRAPHY = geography.load()
NODES.update(DERIVED_GEOGRAPHY)

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
# SETTLEMENTS' SITES.  READ FROM `settlements/sites.yaml`, NOT FROZEN HERE.
#
# WHAT THIS REPLACED AND WHY.  Until 2026-09-21 this block was 15 hand-copied
# site records -- coordinates, pad radii, pad datums, road priorities -- a
# snapshot of a `sites.yaml` publication, pasted.  `settlements/plan.py` WRITES
# that file and `blueprints/site_finder.py` is genuinely seed-parameterised, so
# the solver could already answer this question for any seed; nothing read it.
# A fresh seed got Ulfsland's coordinates.
#
# Now: the SOLVER is the source, and NOTHING is layered over it.  A person
# declares a site only through `worldseed.HUMAN_SITES` -- for Pirate68 the two
# harbours, Ulfsgard and Sudrvik -- and that enters below as its own nodes with
# its own legs.  Every other site is `sites.yaml`'s, to the coordinate and to
# the datum.
#
# THERE USED TO BE A PIN LAYER, AND IT WAS A RECORDING.  15 `PINNED_SITES`
# records (stenvik, vestvik, hognest, wtspawn, wtsouth, wttown, wtpeak,
# wtnorth, wteast, lhsouth, lheast, lhwest, treesth, treewest, treenear) held
# "the PROVISIONAL publication the live Ulfsland network was routed against":
# FROZEN SOLVER OUTPUT, kept so the roads already in the ground would not
# move.  No person decided one of those numbers.  They overrode the solver on
# the default pair with an earlier run's answer -- coordinates, pad radii,
# datums, one road priority -- so a regeneration replayed a recording instead
# of applying the rules to the seed, and the tool could never be seen to
# solve what it was being handed.  Deleted, with `plan.ANCHORED_TOWNS` and
# `plan.ANCHORED_OUTLIERS`, which pinned the same sites one stage earlier.
# What a measured datum off standing ground means for a world that is being
# regenerated from nothing is nothing: there is no ground standing.
#
# `pad_radius_m` is Settlements' keep-out: my ribbon terminates at the pad edge
# and does not pave inside it, because inside it is its earthwork and a road
# levelling ground under a building is the defect class this build exists to
# avoid.
# ---------------------------------------------------------------------------
# THE SOLVER'S OUTPUT IS PER SEED AND ITS PATH WAS NOT.
#
# This was the LAST frozen artefact path on the planning chain, and it was the
# third instance of one defect tonight.  Each time the shape was identical: a
# header check landed, a fresh-seed run correctly REFUSED instead of silently
# adopting Ulfsland's numbers -- and then had no way to comply, because the
# path it would have to supply was written in.
#
#   roads/poi.py          DUMP was one seed's content hash; a Fresh42 dump of
#                         12,376 locations sat on disk unreachable.  Now
#                         resolved through each root's own index.tsv.
#   roads/geography.py    GEOGRAPHY_YAML was a single path; now
#                         `geography.<seed>.yaml` with `$VH_GEOGRAPHY` over it.
#   here                  `sites.yaml`, same fix, same shape.
#
# A REFUSAL THAT NAMES NO REACHABLE REMEDY IS AN INVITATION TO WORK AROUND IT.
# The remedy has to EXIST, not merely be named -- and the cheapest way around
# this one was always to point the run back at Ulfsland's file.
#
# The default seed keeps the bare name, so Ulfsland's file does not move and no
# path in this repo changes.  `$VH_SITES` overrides absolutely, matching
# `geography.py`'s convention rather than inventing a second one.
#
# THE RESOLVERS ARE NOT HERE ANY MORE: `worldseed.sites_path`, `plan_path`,
# `sites_name`, `plan_name`, `segments_path`, `crossings_path` and
# `road_plan_names`.  Importing THIS module loads the node table -- the solved
# site set, the installations, the geography -- so a file NAME asked of it
# required the file to exist; the site set's own producer refused on it
# (worldseed.py, SLOTS_DEPENDS_ON).  Nor is a default-seed `SITES_YAML`
# constant: it named Pirate68's file in this module's refusal and in the
# `source` every adoption reports, on every seed.


# A SITE IS A NODE OF THIS NETWORK WHEN A ROAD REACHES IT, and the solver says
# so in `sites.yaml` (`road`, settlements/road_reach.py): the declared road
# class (`road_priority`) read against the router's own graph.  There used to
# be a table here, `NOT_ADOPTED`, naming `sudrberg-keep` and the landmass of
# the default pair's map it stood on -- a fact about one seed's accident,
# attached to a NAME, and the name is whatever the solver ranked.  A site no
# road reaches (or declared `none`) is PORTAL-ONLY: it is not a node, no leg
# names it, and `PORTAL_ONLY` / `NO_ROAD` carry why, with the numbers.

# `sites.yaml` ids are the SOLVER's; this module's node ids are the road
# network's.  The rule is "drop the hyphens", with exactly one exception, and
# the exception is listed rather than special-cased in the function.
SOLVER_ID_ALIASES = {"hognest-castle": "hognest"}

# A site's measurement window need not be its routing window. Roaded sites use
# the covering rooted window and actual landmass role in their road evidence;
# declared sites use their separately published routing window. ONE TABLE in
# geography.py resolves window roles for geography and declared nodes alike.
ISLAND_OF_WINDOW = geography.ISLAND_OF_WINDOW
_island_of = geography.island_of_window


def _require_site_road(road, site_id: str, source) -> dict:
    """Refuse publications predating the routing evidence, never infer a road."""
    missing = []
    if not isinstance(road, dict) or not isinstance(road.get("roaded"), bool):
        missing.append("road.roaded")
    else:
        if not road.get("window"):
            missing.append("road.window")
        if road["roaded"]:
            if not road.get("topology_window"):
                missing.append("road.topology_window")
            if not (road.get("evidence") or {}).get("island"):
                missing.append("road.evidence.island")
    if missing:
        raise SystemExit(
            f"REFUSING: {source} site {site_id!r} lacks required routing "
            f"metadata {', '.join(missing)}. Re-run settlements/plan.py "
            f"(pipeline stage 8) for this seed to regenerate its road "
            f"topology evidence; a measurement patch is not connectivity.")
    return road


def load_solved_sites(path: Path | None = None) -> dict[str, dict]:
    """`settlements/sites.yaml` as this module's site records, as solved.

    No fallback.  A missing solver file used to be survivable because a frozen
    copy sat here; that copy is what went stale.  Refusing loudly is the only
    honest behaviour left.

    AND A PRESENT FILE IS NOT AUTOMATICALLY THIS SEED'S FILE.  MEASURED
    2026-09-21: with a pin layer correctly declining to apply a single pin on
    seed `Fresh42`, this function still handed that run all 16 of Ulfsland's
    solved sites at Ulfsland's coordinates -- `stenvik` at (496.0, 904.0),
    `vestvik` at (-906.0, 1846.0) -- out of a file whose first three lines read
    `world: Ulfsland`, `seed: Pirate68`, `seed_hash: 147627509`.  A scoped
    layer over an unscoped base is not scoped; the layer is gone and this
    refusal is the whole guard.
    """
    path = Path(path) if path is not None else worldseed.sites_path()
    if not path.exists():
        raise SystemExit(
            f"{path} is missing. It is this seed's SOLVED SITE SET and there "
            f"is no frozen copy to fall back on -- a frozen copy is exactly "
            f"what went stale. Produce it with:\n"
            f"  settlements/plan.py --patches <VHPATCH1 scanned on this seed> "
            f"--locations <this seed's LocScan dump> --out <dir>\n"
            f"then place it at {path}, or point $VH_SITES at it. Naming the "
            f"command matters: a refusal whose remedy is unreachable is an "
            f"invitation to point the run back at Ulfsland's file, which is "
            f"the defect this check exists to stop.")
    doc = yaml.safe_load(path.read_text())
    worldseed.require_scope(
        "solved site set", path, found_seed=doc.get("seed"),
        found_world=doc.get("world"),
        produced_by="settlements/plan.py, which writes this file for the seed "
                    "it solved; run the settlements planner for this seed")
    out: dict[str, dict] = {}
    for s in doc["sites"]:
        sid = SOLVER_ID_ALIASES.get(s["id"], s["id"].replace("-", ""))
        road = _require_site_road(s.get("road"), s["id"], path)
        out[sid] = {
            "type": s["type"],
            "xz": (float(s["xz"][0]), float(s["xz"][1])),
            "island": (road["evidence"]["island"] if road["roaded"]
                       else (road.get("evidence") or {}).get("island")),
            "patch": (road["topology_window"] if road["roaded"]
                      else s["patch"]),
            "measurement_patch": s["patch"],
            "pad_radius_m": float(s["pad_radius_m"]),
            "priority": s["road_priority"],
            "tag": s["portal_tag"],
            "pad_y": s.get("pad_y"),
            "solver_id": s["id"],
            "road": road,
        }
    return out


def adopt_sites(solved: dict[str, dict] | None = None
                ) -> tuple[dict[str, dict], dict]:
    """The solver's sites a road reaches, and a RECORD of the ones none does.

    A site is the solver's, to the coordinate and to the datum, and it is a
    NODE of this network when its published `road` verdict says `roaded`.  One
    that is not -- declared `none`, in a window no crossing roots, or standing
    where the router's own graph has no way in (settlements/road_reach.py) --
    is PORTAL-ONLY: returned in `provenance["portal_only"]` with the verdict's
    reason and numbers, and not a node, so no edge can name it and none
    routes to nothing. Missing road/topology evidence is refused with a stage 8
    remedy, not treated as permission to adopt a site.
    """
    solved = load_solved_sites() if solved is None else solved
    sites: dict[str, dict] = {}
    portal_only: dict[str, dict] = {}
    for sid, s in solved.items():
        road = _require_site_road(
            s.get("road"), s["solver_id"], worldseed.sites_path())
        if not road["roaded"]:
            portal_only[sid] = {
                "solver_id": s["solver_id"], "xz": list(s["xz"]),
                "window": s["measurement_patch"], "class": road["class"],
                "reason": road["reason"], "why": road["why"],
                "evidence": road.get("evidence") or {}}
            continue
        sites[sid] = {k: v for k, v in s.items() if k != "solver_id"}
        sites[sid].update(patch=road["topology_window"],
                          island=road["evidence"]["island"])
    provenance = {
        "source": str(worldseed.sites_path()),
        "portal_only": portal_only,
    }
    return sites, provenance


SETTLEMENT_SITES, SITE_PROVENANCE = adopt_sites()
PORTAL_ONLY: dict[str, dict] = SITE_PROVENANCE["portal_only"]

for _sid, _s in SETTLEMENT_SITES.items():
    NODES[_sid] = {
        "island": _s["island"], "patch": _s["patch"], "xz": _s["xz"],
        "measurement_patch": _s["measurement_patch"],
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
    # THERE IS NO TYPED LEG FOR THE EAST WATCHTOWER (`S10`, `wteast`), THE
    # EAST LIGHTHOUSE OR ANY SITE A LEG WAS NEVER TYPED FOR.  A name here is
    # whatever the solver bound it to, and `wteast` was once a watchtower on
    # a separate island "portal-only, a 562 m bridge" -- a fact about one
    # accident, kept by name.  Whether a road reaches a site is MEASURED
    # (settlements/road_reach.py) and a roaded site no leg connects to the
    # network gets one DERIVED (`declared_sites.site_legs`, `J<n>`); one no
    # road reaches is in `PORTAL_ONLY`, and `NO_ROAD` says why with numbers.
    {"id": "S11-southcape-lhsouth", "a": "southcape", "b": "lhsouth", "cls": "spur", "pri": 2},
    {"id": "W8-brgs2-vestvik",    "a": "brg-s2-w", "b": "vestvik",  "cls": "trunk", "pri": 3},
    {"id": "W9-vestvik-lhwest",   "a": "vestvik",  "b": "lhwest",   "cls": "spur", "pri": 3},
    {"id": "W10-brgs1-treenear",  "a": "brg-s1-w", "b": "treenear", "cls": "spur", "pri": 3},
    {"id": "W11-wmid-treewest",   "a": "w-mid",    "b": "treewest", "cls": "spur", "pri": 3},
]
EDGES.extend(SETTLEMENT_EDGES)

# THE HUMAN-DECLARED SITES, AS NODES.  Their legs are derived below, after the
# fixed terminal legs, so the leg numbering cannot collide with a typed id.
#
# A PUBLICATION THAT PREDATES THE DECLARATION IS NOT A CRASH AT IMPORT -- every
# tool that wants a helper from this module would die with it -- and it is not
# an absent node either: `DECLARED_STALE` carries the refusal, and
# `roads/network.py plan` (the one consumer that would route without the
# node) stops on it before it routes anything.
try:
    DECLARED_SITES = DS.load(None, None, _island_of)
    DECLARED_STALE = ""
except SystemExit as _exc:
    DECLARED_SITES, DECLARED_STALE = {}, str(_exc)
for _nid, _d in DECLARED_SITES.items():
    if _nid in NODES:
        raise SystemExit(
            f"REFUSING: declared site {_d['site_id']!r} wants node id "
            f"{_nid!r}, which this network already has; rename its `node` in "
            f"worldseed.HUMAN_SITES.")
    NODES[_nid] = {
        "island": _d["island"], "patch": _d["patch"], "xz": _d["xz"],
        "measurement_patch": _d["measurement_patch"],
        "raw_xz": _d["requested_xz"], "kind": _d["type"],
        "pad_radius_m": _d["pad_radius_m"], "portal_tag": None,
        "provenance": ("DECLARED by worldseed.HUMAN_SITES (the owner's "
                       "request, raw_xz) and MEASURED by "
                       "settlements/harbour_site.py at stage 8 (the landing)"),
        "why": _d["why"],
    }

# Outlying islands: portal-reached, so a LOCAL road only.  Each installation is
# joined to the two farthest inland points of its own landmass, which gives the
# operator something to walk after stepping out of a portal instead of a stub.
OUTLYING_NODES: dict[str, tuple[float, float]] = {}


# Waterfront coordinates are disposable solver output on EVERY seed.
# The ordinary producer runs after placements/sites and before importing this
# network specification. Importing spec hydrates the scoped publication only;
# it never scans, imports old geometry or guesses a missing terminal.
import shores

WATERFRONT_SITES = shores.load(required=False)
CROSSING_TERMINALS: dict[str, dict] = {
    s["node"]: dict(xz=tuple(s["root"]), island=s["island"], patch=s["patch"],
                   arrive_bearing_deg=s["arrive_bearing_deg"],
                   jetty_bearing_deg=s["bearing"], width_m=s["width_m"],
                   cls=s["cls"], owner="Crossings")
    for s in WATERFRONT_SITES
}
NODES.update(shores.road_nodes(WATERFRONT_SITES))

# Foreign survey records may still be diagnosed by the generic guard, but no
# waterfront requirement is sourced from a seed-specific survey table.
MEASURED_SEED_DEPENDS_ON = ("the_seed_the_node_was_measured_on",)
_MEASURED_SEED_MODE = "scoped"
FOREIGN_NODES: dict[str, dict] = {}
if not WATERFRONT_SITES:
    # This is an unmet producer obligation, NOT an empty waterfront design.
    # Numeric importers may supply their own geometry; actual CLI planning runs
    # shores.produce(--field) before reloading this module.
    FOREIGN_NODES.update({
        r["node"]: dict(owner="roads.shores.produce after placements/sites",
                        measured_on=worldseed.seed(), requirement=r["id"])
        for r in shores.REQUIREMENTS
    })
    for _nid in ("ferry-eastisle-inland", "ferry-terminal-eastisle"):
        FOREIGN_NODES[_nid] = dict(owner="roads.shore_transport.solve_pair",
            measured_on=worldseed.seed(), requirement="ferry-east-isle destination access")


def seed_scoped(table: dict[str, dict], *, measured_on: str, owner: str,
                seed_: str | None = None) -> tuple[dict, dict]:
    """`(nodes this seed may use, nodes withheld)` for a table of node
    records MEASURED on `measured_on` (MEASURED_SEED_DEPENDS_ON)."""
    run = seed_ or worldseed.seed()
    if _MEASURED_SEED_MODE == "unscoped" or run == measured_on:
        return dict(table), {}
    return {}, {nid: {**rec, "measured_on": measured_on, "owner": owner}
                for nid, rec in table.items()}



NO_ROAD: dict[str, str] = {}
REQUIRED_TRANSPORT = ([s["pair_requirement"] for s in WATERFRONT_SITES
                      if s.get("pair_requirement")] or [dict(shores.FERRY_REQUIREMENT)])



TERMINAL_EDGES: list[dict] = [
    {"id": "T13-temple-harboursouth", "a": "temple", "b": "harbour-south",
     "cls": "trunk", "pri": 1, "width_m": 6.0,
     "why": "the harbour on the temple's own south shore; trunk because its deck "
            "is 6 m"},
    {"id": "S12-brgs1-boathouse", "a": "brg-s1-e", "b": "boathouse-strait",
     "cls": "spur", "pri": 1, "width_m": 4.0,
     "why": "boathouse serving this seed's generated S1 source bank"},
    {"id": "S13-stathub-dock", "a": "stathub", "b": "dock-stathub",
     "cls": "spur", "pri": 2, "width_m": 4.0},
    {"id": "S14-eastlobe-ferryeast", "a": "eastlobe", "b": "ferry-east",
     "cls": "spur", "pri": 2, "width_m": 4.0},
    {"id": "S15-vestvik-harbour", "a": "vestvik", "b": "harbour-vestvik",
     "cls": "trunk", "pri": 3, "width_m": 6.0,
     "why": "the generated village's measured harbour approach, not a frozen quay"},
    {"id": "S16-dockshore-dock", "a": "dockshore", "b": "dock-dockshore",
     "cls": "trunk", "pri": 2, "width_m": 6.0},
    {"id": "S17-stenvik-port", "a": "stenvik", "b": "harbour-stenvik-port",
     "cls": "trunk", "pri": 2, "width_m": 6.0},
    {"id": "S18-northcape-dock", "a": "northcape", "b": "dock-northcape",
     "cls": "trunk", "pri": 2, "width_m": 6.0},
    {"id": "F1-ferry-eastisle-inland", "a": "ferry-eastisle-inland",
     "b": "ferry-terminal-eastisle", "cls": "spur", "pri": 2,
     "width_m": 4.0, "transport_access": "ferry-east-isle",
     "why": "required generated inward road on the actual ferry destination component"},
]
for _edge in TERMINAL_EDGES:
    if _edge["b"] in CROSSING_TERMINALS:
        _edge["patch"] = CROSSING_TERMINALS[_edge["b"]]["patch"]
EDGES.extend(TERMINAL_EDGES)

# THE DECLARED SITES' LEGS, derived (`declared_sites.legs`) and appended last.
DECLARED_EDGES: list[dict] = DS.legs(DECLARED_SITES, NODES, EDGES)
EDGES.extend(DECLARED_EDGES)
_dup = sorted({e["id"] for e in EDGES if sum(1 for q in EDGES
                                             if q["id"] == e["id"]) > 1})
if _dup:
    raise SystemExit(f"REFUSING: duplicate edge id(s) {_dup} in EDGES")

# A LEG TO A NODE MEASURED ON ANOTHER SEED (MEASURED_SEED_DEPENDS_ON) is not
# routed on this one: it leaves `EDGES` for `FOREIGN_EDGES`, and
# `network.py plan` refuses it with this seed's own reading at the point.  A
# leg with an end this seed's GEOGRAPHY measured absent keeps that reason
# instead (below): that is the prior, measured cause.


def split_foreign(edges: list[dict], nodes: dict,
                  foreign: dict[str, dict]) -> list[dict]:
    """Take out of `edges` every leg naming a node in `foreign` whose other
    end this seed has; return them as `{"edge", "nodes"}`."""
    out = []
    for e in list(edges):
        gone = [n for n in (e["a"], e["b"]) if n in foreign]
        if not gone or any(n not in nodes and n not in foreign
                           for n in (e["a"], e["b"])):
            continue
        edges.remove(e)
        out.append({"edge": e, "nodes": gone})
    return out


FOREIGN_EDGES: list[dict] = split_foreign(EDGES, NODES, FOREIGN_NODES)
for _fe in FOREIGN_EDGES:
    _foreign = _fe["nodes"]
    NO_ROAD[_fe["edge"]["id"]] = (
        f"{', '.join(_foreign)} {'was' if len(_foreign) == 1 else 'were'} "
        f"MEASURED ON SEED {FOREIGN_NODES[_foreign[0]]['measured_on']!r}, not "
        f"on this one (MEASURED_SEED_DEPENDS_ON); owner "
        f"{FOREIGN_NODES[_foreign[0]]['owner']}")

# WHAT A CROSSING'S RECORD IS MADE OF.  `joins` and `significance` are DESIGN
# (why the crossing matters) and are authored once.  Everything else -- the
# banks, their ground, the span, the bearing, the lowest bed under it -- is a
# MEASUREMENT OF THIS SEED and is read from the abutment nodes
# `geography.crossing_nodes` derived for it.  These two records were literals
# copied from Ulfsland's one field (`brg-s1-e` at (-312, -64) and the rest),
# so a seed whose derivation sited a crossing anywhere else would have been
# published with Ulfsland's banks inside its own document.  A literal that
# happens to equal the derivation is a coincidence; when the derivation is
# corrected (CROSSING_REACH_DEPENDS_ON moved S1 to (-310, -74) on Pirate68)
# it stops being one.
_AUTHORED_CROSSINGS = (
    ("S1", "brg-s1-e", "brg-s1-w", ["spawn", "west"],
     "the join between the two halves of the largest landmass in the world"),
    ("S2", "brg-s2-e", "brg-s2-w", ["spawn", "west"],
     "second join; makes the network a loop rather than an out-and-back"),
)


def _declared_crossing(cid: str, a: str, b: str, joins: list, why: str,
                       nodes: dict) -> dict:
    rec = {"crossing_id": cid, "owner": "Crossings", "status": "declared",
           "measured_by": "geography.crossing_nodes: 1 m distance transform, "
                          "banks filtered to the router's reachable component",
           "joins": joins, "significance": why,
           "bank_a": {"node": a}, "bank_b": {"node": b},
           "water_surface_y": 30.0, "why_terrain_failed": "water_body"}
    if a in nodes and b in nodes:
        na, nb = nodes[a], nodes[b]
        m = na.get("measured") or {}
        bearing = float(m["bearing_deg"])
        rec["bank_a"].update(xz=list(na["xz"]), ground_y=na["ground_y"],
                             road_deck_y=DECK_DATUM_Y, bearing_deg=bearing)
        rec["bank_b"].update(xz=list(nb["xz"]), ground_y=nb["ground_y"],
                             road_deck_y=DECK_DATUM_Y,
                             bearing_deg=round((bearing + 180.0) % 360.0, 2))
        rec["span_m"] = float(m["span_m"])
        rec["gap_min_y"] = m["gap_min_y"]
    return rec


DECLARED_CROSSINGS: list[dict] = [
    _declared_crossing(cid, a, b, joins, why, NODES)
    for cid, a, b, joins, why in _AUTHORED_CROSSINGS]

# ---------------------------------------------------------------------------
# EDGES WHOSE ENDS THIS SEED DOES NOT HAVE.
#
# The 40 edges are a TOPOLOGY over node ids and survive any seed whose node ids
# survive -- but "survive" is not automatic for the crossings.  `S1` is stated
# as "the join between the two halves of the main continent", and that is a
# claim about ULFSLAND'S SHAPE, not about every planet.  MEASURED by
# FreshSeedDrive on seed Fresh42: `trunk-primary` is centred (-6.1, 3.2) and
# `trunk-secondary` (7282.9, -1001.8), 7,358 m apart with 1600 m half-extents,
# so the two windows DO NOT TOUCH.  There is no channel to bridge; there are two
# ISLANDS -- and this project already has the answer for that, in `NO_ROAD`'s
# own ferry-terminal-eastisle: "a SEPARATE ISLAND ... so it is portal-only and
# the operator reaches it by portal".
#
# So an edge may lose an end, and there are exactly two ways that can happen:
#   * the derivation MEASURED the crossing impossible and SAID SO -- the edge
#     moves into `NO_ROAD` carrying that measurement, and the far island is
#     reached by portal.
#   * the end is simply missing, with nothing recorded.  That is REFUSED.  An
#     absent terminus with no reason is the dangling edge `geography.py` already
#     refuses for an empty sector, and letting it through here would make the
#     crossing the one place a node is allowed to vanish quietly.
# ---------------------------------------------------------------------------
GEOGRAPHY_ABSENT = geography.absent_nodes()


def _absent_why(nid: str) -> str | None:
    """Why this seed's network has no node `nid`, from the measurement that
    ruled it out: its geography, or its road verdict (`PORTAL_ONLY`)."""
    if nid in GEOGRAPHY_ABSENT:
        return GEOGRAPHY_ABSENT[nid]["why"]
    if nid in PORTAL_ONLY:
        return f"{nid} is portal-only: {PORTAL_ONLY[nid]['why']}"
    return None


def drop_edges_without_nodes(edges: list[dict], nodes: dict, why_of,
                             foreign: dict, no_road: dict) -> list[dict]:
    """Take out of `edges` every leg naming a node this seed does not have,
    record why in `no_road`, and return the legs taken out.  A node absent
    for NO recorded reason (`why_of` knows none, and it is not `foreign`) is
    REFUSED -- a terminus that vanishes quietly is a road to nowhere."""
    dropped: list[dict] = []
    for e in list(edges):
        gone = [n for n in (e["a"], e["b"]) if n not in nodes]
        if not gone:
            continue
        unexplained = [n for n in gone
                       if why_of(n) is None and n not in foreign]
        if unexplained:
            raise SystemExit(
                f"REFUSING: edge {e['id']!r} names node(s) "
                f"{', '.join(unexplained)} that this seed's geography does not "
                f"have and does not explain. A terminus that is absent for no "
                f"recorded reason is a road to nowhere; re-emit "
                f"roads/geography.yaml for this seed, or the authored EDGES "
                f"have to say why this leg does not exist.")
        edges.remove(e)
        dropped.append(e)
        no_road[e["id"]] = why_of(next(n for n in gone if why_of(n) is not None))
    return dropped


UNREACHABLE_EDGES: list[dict] = drop_edges_without_nodes(
    EDGES, NODES, _absent_why, FOREIGN_NODES, NO_ROAD)

# EVERY PORTAL-ONLY SITE SAYS WHY, with the numbers it was judged on -- so
# "why is there no road to the keep" has an answer that is a measurement and
# not a sentence typed about one seed's accident.
for _sid, _p in PORTAL_ONLY.items():
    NO_ROAD[_sid] = _absent_why(_sid)

# ---------------------------------------------------------------------------
# THE SAME POPULATION, AND IT HAD TWO RULES.
#
# `DECLARED_CROSSINGS` names the SAME FOUR ABUTMENT NODES the edge loop above is
# filtered against, and it was filtered against nothing.  MEASURED 2026-09-22 by
# driving seed Fresh42 through `roads/network.py plan --provisional` on a
# scratch root: the run EXITED 0, stamped `seed: Fresh42 / world: Fresh42` on
# its crossings.yaml, and published S1 and S2 as DECLARED at ULFSLAND's banks --
# brg-s1-e (-312, -64), brg-s1-w (-324, -64), brg-s2-w (-348, 246) -- carrying
# `measured_by: Crossings, 1 m distance transform over /tmp/roads/h1m.bin`,
# Ulfsland's field, named twice inside a Fresh42 artefact, plus `260 m from
# StartTemple` and a `poi_clear` reading of Ulfsland's WoodHouse7.
#
# THE RUN CONTAINED ITS OWN REFUTATION.  The very same import had already moved
# 8 edges into `NO_ROAD` because `geography.absent_nodes()` measured those four
# nodes PORTAL-ONLY -- 4,926.7 m of open water against Crossings' 96 m absolute
# span ceiling.  One population, two rules, and the UNFILTERED one is the one
# the Crossings consumer reads.  That is a false success, not a stale file: the
# header is right, the exit code is right, and the content is another planet's.
#
# THE EXCLUSION IS DERIVED FROM `GEOGRAPHY_ABSENT`, never from a second list of
# node ids.  A restated population is the next thing to drift from the first --
# which is the whole defect being closed here.
#
# AN UNBUILDABLE CROSSING KEEPS ONLY WHAT IS AUTHORED.  `joins` and
# `significance` are design (why this crossing matters); the banks, the span,
# the gap and the `poi_clear` are MEASUREMENTS OF ULFSLAND and must not be
# carried into another seed's document even inside a record that says the
# crossing does not exist.  What replaces them is this seed's own measurement of
# why it cannot be built.
# ---------------------------------------------------------------------------
UNBUILDABLE_CROSSINGS: list[dict] = []
for _c in list(DECLARED_CROSSINGS):
    _banks = [_c[_side]["node"] for _side in ("bank_a", "bank_b")]
    _gone = [n for n in _banks if n not in NODES]
    if not _gone:
        continue
    _unexplained = [n for n in _gone if n not in GEOGRAPHY_ABSENT]
    if _unexplained:
        raise SystemExit(
            f"REFUSING: declared crossing {_c['crossing_id']!r} names bank "
            f"node(s) {', '.join(_unexplained)} that this seed's geography does "
            f"not have and does not explain. An abutment that is absent for no "
            f"recorded reason is a bridge to nowhere, and a declared crossing "
            f"is the one artefact Crossings BUILDS FROM; re-emit "
            f"roads/geography.yaml for this seed, or the authored "
            f"DECLARED_CROSSINGS have to say why this span does not exist.")
    DECLARED_CROSSINGS.remove(_c)
    UNBUILDABLE_CROSSINGS.append({
        "crossing_id": _c["crossing_id"],
        "status": "unbuildable",
        "joins": _c["joins"],
        "significance": _c["significance"],
        "absent_nodes": _gone,
        "why_not_on_this_seed": GEOGRAPHY_ABSENT[_gone[0]]["why"],
        "measured": GEOGRAPHY_ABSENT[_gone[0]].get("measured", {}),
    })

# ---------------------------------------------------------------------------
# THE LEGS A ROADED SOLVED SITE STILL NEEDS.
#
# Everything above that names a solved site is AUTHORED TOPOLOGY: "this site
# hangs from that node", typed once for the sites a seed was expected to have.
# What a seed actually has is measured (`PORTAL_ONLY` above is what no road
# reaches), so the topology is checked against what is LEFT of it, with the
# one join rule a declared site already has (`declared_sites.site_legs`):
# every roaded site no leg connects to the portal hall -- because its
# authored partner is portal-only, stands on another landmass, or was never
# typed (the east watchtower and the east lighthouse never were) -- gets ONE
# derived leg, in its own declared class, to the nearest trunk node its
# landmass reaches. `J<n>` ids; nothing is typed. Publications predating the
# required routing evidence were refused at load/adoption, not given the old
# permissive authored-only topology.
# ---------------------------------------------------------------------------
_ROADED_SITES = SETTLEMENT_SITES
SITE_JOINS, SITE_UNJOINED = DS.site_legs(_ROADED_SITES, NODES, EDGES,
                                         DECLARED_CROSSINGS)
EDGES.extend(SITE_JOINS)
for _sid, _why in SITE_UNJOINED.items():
    NO_ROAD[f"join-{_sid}"] = (f"{_sid} is roaded and no leg of the network "
                               f"joins it to the portal hall: {_why}")
_dup = sorted({e["id"] for e in EDGES if sum(1 for q in EDGES
                                             if q["id"] == e["id"]) > 1})
if _dup:
    raise SystemExit(f"REFUSING: duplicate edge id(s) {_dup} in EDGES")


def connection_requirements(nodes=None, portal_only=None, declared=None):
    """Semantic replacements for the old standing-world R3/R4 repairs.

    Existing generated W8/site_legs connect the solved town to the network;
    generated declared-site legs or T5/T9 connect the REAL harbour. The gate
    requires physical journey reachability of both, not the old repair IDs.
    """
    nodes = NODES if nodes is None else nodes
    portal_only = PORTAL_ONLY if portal_only is None else portal_only
    declared = DECLARED_SITES if declared is None else declared
    town = nodes.get("vestvik") or portal_only.get("vestvik") or {}
    harbours = [n for n in ["dockshore", *declared]
                if n in nodes and (n == "dockshore"
                    or nodes[n].get("kind") in worldseed.HUMAN_SITE_KINDS)]
    if town.get("xz") and harbours:
        harbour = min(harbours, key=lambda n: (
            nodes[n].get("island") != town.get("island"),
            math.hypot(nodes[n]["xz"][0]-town["xz"][0],
                       nodes[n]["xz"][1]-town["xz"][1]), n))
    else:
        harbour = None
    refusal = (None if "vestvik" in nodes else
               {"reason": "solved Vestvik has no generated road node",
                "measurement": portal_only.get("vestvik")})
    return [
        {"id": "vestvik_to_network", "a": "portalhub", "b": "vestvik",
         "producer": "W8 / declared_sites.site_legs from the solved town",
         "refusal": refusal},
        {"id": "vestvik_to_harbour", "a": "vestvik", "b": harbour,
         "producer": ("declared_sites.legs" if harbour in declared else
                      "T5/T9 to solved pre-elder early-dock / sandbox-harbour"),
         "refusal": refusal or (None if harbour else
                                 {"reason": "0 solved harbour landings"})},
    ]
