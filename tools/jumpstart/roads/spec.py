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

# node id -> the `placements.yaml` record that SOLVED it.  `dockshore` is solved
# to the same centre by two presets (deepnorth-sandbox#sandbox-harbour and
# pre-elder#early-dock); either answers, and a disagreement between them is
# REFUSED below rather than resolved by whichever file was globbed first.
PLACEMENT_OF: dict[str, tuple[str, ...]] = {
    "portalhub": ("sandbox-portal-hub",),
    "meadhall": ("meadows-starter-hall",),
    "stathub": ("complete-station-hub",),
    "dockshore": ("sandbox-harbour", "early-dock"),
    "blacbase": ("blackforest-walled-base",),
    "plainsfm": ("plains-farm-base",),
    "workshop": ("iron-era-workshop",),
    "mtnpost": ("mountain-outpost",),
}

# A seed whose world has no placement set is REFUSED -- it has not sited its
# portal hall yet, and that is a missing prerequisite (stage 7), not a crossing
# the planet cannot have.


def installation_xz(seed: str | None = None,
                    world: str | None = None) -> dict[str, tuple[float, float]]:
    """Where this seed's 8 authored installations actually stand.

    Read from `worlds/<world>/*/placements.yaml` FOR THE SEED BEING PLANNED,
    through `worldseed.require_scope`, so a placement set solved on another seed
    is refused rather than transcribed, and refuses when this world has NO
    placement set at all -- because a road planned to a portal hall nobody has
    sited is the 59% defect wearing a different header.
    """
    import yaml                                             # noqa: PLC0415
    want_s = seed or worldseed.seed()
    want_w = world or worldseed.world()
    root, pattern, key = worldseed.installations_read(want_w)
    sets = sorted(root.glob(pattern)) if root.is_dir() else []
    found: dict[str, tuple[float, float, str]] = {}
    for path in sets:
        doc = yaml.safe_load(path.read_text())
        worldseed.require_scope(
            "installation placement set", path, found_seed=doc.get("seed"),
            found_world=doc.get("world"), seed_=want_s, world_=want_w,
            produced_by="blueprints/solve_placements.py, which solves this "
                        "world's buildings onto this seed's ground")
        for rec in doc.get("placements") or []:
            for nid, ids in PLACEMENT_OF.items():
                if rec.get("id") not in ids:
                    continue
                solved = rec.get(key) or {}
                if solved.get("x") is None or solved.get("z") is None:
                    continue
                xz = (float(solved["x"]), float(solved["z"]))
                prior = found.get(nid)
                if prior is not None and (prior[0], prior[1]) != xz:
                    raise SystemExit(
                        f"REFUSING: installation {nid!r} is solved to "
                        f"{(prior[0], prior[1])} in {prior[2]} and to {xz} in "
                        f"{path}. Two presets of the same world disagree about "
                        f"where one building stands, and a road cannot be "
                        f"planned to both.")
                found[nid] = (xz[0], xz[1], str(path))
    if found:
        missing = sorted(set(PLACEMENT_OF) - set(found))
        if missing:
            raise SystemExit(
                f"REFUSING: world {want_w!r} has a placement set for seed "
                f"{want_s!r} but it does not site {', '.join(missing)}. "
                f"{len(missing)} road node(s) would fall back to another "
                f"world's coordinates inside a plan the rest of which is this "
                f"seed's -- which is exactly the join of two planets this "
                f"module refuses everywhere else. Solve them with "
                f"blueprints/solve_placements.py for this world.")
        return {nid: (x, z) for nid, (x, z, _p) in found.items()}
    raise SystemExit(
        f"REFUSING: seed {want_s!r} has no sited installations. The 8 "
        f"installation and shoreline nodes are WHERE THIS PROJECT PUT ITS "
        f"BUILDINGS, and on seed {worldseed.DEFAULT_SEED!r} the "
        f"{len(PLACEMENT_OF)} of them carry 12 of the 40 EDGES and, MEASURED "
        f"on Fresh42, 59% of the planned metreage. "
        f"There is no frozen copy to fall back on: a frozen copy is "
        f"how a Fresh42 plan came to run 830.3 m from Ulfsland's portal hall "
        f"to Fresh42's own temple in one segment. Produce them with:\n"
        f"  blueprints/solve_placements.py for world {want_w!r} on seed "
        f"{want_s!r}")


for _nid, _xz in installation_xz().items():
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

# The window a site sits in decides which land component a road may reach it
# over.  `network.main` refuses an edge whose ends disagree, so this mapping is
# load-bearing, not decorative.  ONE TABLE, in `geography.py`, because the
# derived extremity and abutment nodes are keyed off the same island roles and
# two copies of a load-bearing mapping is two things to keep true.  A window
# with no entry is its own island, which is the safe answer -- an unknown
# landmass gets no road rather than a road to somewhere it cannot reach.
ISLAND_OF_WINDOW = geography.ISLAND_OF_WINDOW
_island_of = geography.island_of_window


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
        out[sid] = {
            "type": s["type"],
            "xz": (float(s["xz"][0]), float(s["xz"][1])),
            "island": _island_of(s["patch"]),
            "patch": s["patch"],
            "pad_radius_m": float(s["pad_radius_m"]),
            "priority": s["road_priority"],
            "tag": s["portal_tag"],
            "pad_y": s.get("pad_y"),
            "solver_id": s["id"],
            # THE ROAD VERDICT, or None for a publication that predates it:
            # nothing is then known, and nothing is withheld on its account.
            "road": s.get("road"),
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
    routes to nothing.  A publication that predates the verdict (`road` is
    None) withholds nothing: every site in it is adopted, as before.
    """
    solved = load_solved_sites() if solved is None else solved
    sites: dict[str, dict] = {}
    portal_only: dict[str, dict] = {}
    for sid, s in solved.items():
        road = s.get("road")
        if road is not None and not road["roaded"]:
            portal_only[sid] = {
                "solver_id": s["solver_id"], "xz": list(s["xz"]),
                "window": s["patch"], "class": road["class"],
                "reason": road["reason"], "why": road["why"],
                "evidence": road.get("evidence") or {}}
            continue
        sites[sid] = {k: v for k, v in s.items() if k != "solver_id"}
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

# ---------------------------------------------------------------------------
# MEASURED_SEED_DEPENDS_ON: A NODE MEASURED ON ONE SEED IS NOT A NODE OF
# ANOTHER.
#
# `CROSSING_TERMINALS` above is Crossings' survey of PIRATE68's shore (a scipy
# label over that seed's 1 m land mask) and `R3R4_NODES` below is
# VestvikBuild's live reading of ULFSLAND's standing N0b, Vestvik and quay --
# and both were merged into `NODES` on EVERY seed.  MEASURED 2026-09-26 on
# Fresh42 drive 4 (stage 11, /tmp/regen2/d4_stage11f.log, gate on save
# af96bfb62427): T13-temple-harboursouth was routed and PAVED 260.0 m to
# harbour-south's Pirate68 point (11.5, -258.5), which on Fresh42 is open
# water at h=24.82 m; S13-stathub-dock 950.5 m toward dock-stathub (502.5,
# 1062.5), open sea at h=0.00 with no land within 255 m; S14, R3 and R4
# "outside the corridor grid".  The gate found both paved far ends meeting
# nothing (settlements terminus_unmeasured, 1C).  It is the one-segment-two-
# worlds defect `installation_xz` closed for the installations, one table
# further down.
#
# THE RULE IS EVERY SEED-SCOPED TABLE'S: a measured node applies on the seed it was
# measured on and on no other.  WITHHELD, NOT DROPPED: every leg naming one
# leaves `EDGES` for `FOREIGN_EDGES` carrying the node, the seed it was
# measured on and its OWNER, and `network.py plan` refuses it with the number
# read off THIS seed's field at that point.  On the default seed nothing
# moves: same nodes, same order, same edges.
#   the_seed_the_node_was_measured_on   the table's own seed, compared with
#       the seed being planned.
# NEUTER `_MEASURED_SEED_MODE = "unscoped"`: the shipped read, every seed gets
# Pirate68's nodes.  Proven by `network.selftest_foreign_nodes`.
# ---------------------------------------------------------------------------
MEASURED_SEED_DEPENDS_ON = ("the_seed_the_node_was_measured_on",)
_MEASURED_SEED_MODE = "scoped"       # scoped | unscoped (the shipped read)

# node id -> its record, withheld on this seed, with `measured_on` and `owner`.
FOREIGN_NODES: dict[str, dict] = {}


def seed_scoped(table: dict[str, dict], *, measured_on: str, owner: str,
                seed_: str | None = None) -> tuple[dict, dict]:
    """`(nodes this seed may use, nodes withheld)` for a table of node
    records MEASURED on `measured_on` (MEASURED_SEED_DEPENDS_ON)."""
    run = seed_ or worldseed.seed()
    if _MEASURED_SEED_MODE == "unscoped" or run == measured_on:
        return dict(table), {}
    return {}, {nid: {**rec, "measured_on": measured_on, "owner": owner}
                for nid, rec in table.items()}


_KEPT, _WITHHELD = seed_scoped(
    {_tid: {
        "island": _t["island"], "patch": _t["patch"], "xz": _t["xz"],
        "kind": "waterfront_terminal", "owner": _t["owner"],
        "arrive_bearing_deg": _t["arrive_bearing_deg"],
        "jetty_bearing_deg": _t["jetty_bearing_deg"],
        "ribbon_width_m": _t["width_m"],
        "keep_clear": _t.get("keep_clear", []),
        "provenance": "MEASURED by Crossings, published over hub",
        "why": _t.get("note", "waterfront terminal owned by Crossings"),
    } for _tid, _t in CROSSING_TERMINALS.items()},
    measured_on=worldseed.DEFAULT_SEED,
    owner="Crossings: no per-seed waterfront terminal derivation")
NODES.update(_KEPT)
FOREIGN_NODES.update(_WITHHELD)

# ferry-terminal-eastisle is DELIBERATELY ABSENT from the road network.
# MEASURED by Crossings: its root (1951.5, 556.5) sits on land component 273,
# 0.396 km2, bbox x[1655.5,2397.5] z[-117.5,1048.5], 99 % BlackForest -- a
# SEPARATE ISLAND from the spawn island's component 7.  So it is portal-only and
# the operator reaches it by portal and rides the ferry BACK, which on a world
# with `portals_casual` is the only way a ferry gets used at all.  Recorded here
# because "why is there no road to the far ferry terminal" must have an answer.
NO_ROAD: dict[str, str] = {
    "ferry-terminal-eastisle": "on land component 273 (0.396 km2), a separate "
                               "island from the spawn island; portal only",
}

# ---------------------------------------------------------------------------
# R3 / R4: the roads to Vestvik and to its quay.  MEASURED 2026-09-19 by
# VestvikRoad.  The operator's report was "vestvik doesnt seem to have any
# roads to it", and it was true: the nearest BUILT centreline to Vestvik's
# portal arch is N0b at 742.8 m, twenty-five times the 29.0 m shared-corridor
# width, so this is a new ribbon and not an extension of anything.
#
# The GEOMETRY of both legs lives in `segments.yaml` and must be re-fitted with
# `network.build_segment(fixed_pts=...)`, never re-searched: both lines were
# routed and profiled against a HEIGHTMAP-surface PatchScan
# (/tmp/vestvik/vestroad.heightmap.bin, centre (-610, 2020) half 520, step 1),
# because 3 of their 40 batter-reach zones are biome NON-UNIFORM and
# `network.py`'s default field is the WORLDGEN surface.  MEASURED difference in
# those zones: up to +5.708 m zone-wide, +1.338 m under R3's own centreline.
# Re-searching on the default field returns a different road, fitted to ground
# the engine does not draw.
# ---------------------------------------------------------------------------
R3R4_NODES: dict[str, dict] = {
    "r3-n0b-junction": {
        "island": "west", "patch": "westisle", "xz": (-214.0, 2146.3),
        "kind": "junction", "level_y": 45.6087,
        "provenance": "MEASURED LIVE 2026-09-19 by VestvikBuild, and it is NOT "
                      "the number the ledger holds. N0b's terrain_write "
                      "TARGETED 45.639 (its own fitted profile node at this "
                      "station, ledger seq 2641) and the world got 30 mm less "
                      "of it: the carriageway that is STANDING reads 45.6087. "
                      "Instrument: Heightmap.GetHeight inside a sandbox server "
                      "that instantiates the SAVE's own _TerrainCompiler ZDOs "
                      "(terraform/run_waterproof.sh FROM_SAVE=1; the live world "
                      "is copied, never opened). It is a levelled PLANE and not "
                      "a sampling artefact: x = -216, -215, -214, -213, -212 at "
                      "z = 2146.3 all read 45.6087 to 4 dp, spanning the whole "
                      "6 m carriageway, while the GENERATED surface under them "
                      "runs 45.0759..45.6667 -- 0.59 m of natural relief flattened "
                      "to one number. Across N0b's stations 415-421 the written "
                      "surface sits -0.054..+0.023 m off its own design profile. "
                      "This is NOT the R1 failure mode: zone (-3, 34) HOLDS its "
                      "compiler, objects_count _TerrainCompiler at the zone "
                      "centre with no ignore mask answers Total 1, and the "
                      "applied delta at the station is +0.0281 m. Per §4.6 the "
                      "ledger says what the surface SHOULD be and only a live "
                      "read says what it IS, so R3 arrives on 45.6087",
        "why": "the junction that MINIMISES NEW ROAD over 20 ranked candidates "
               "on N0b and N0 (next best 1250.4 m of A* against 1154.9). The "
               "cheapest-to-WALK junctions all cost 1.5-1.8 km more ribbon and "
               "run parallel to N0 for ~700 m, which is one corridor, not two "
               "roads",
    },
    "r3-vestvik": {
        "island": "west", "patch": "westisle", "xz": (-912.89, 1896.47),
        "kind": "settlement_arrival", "level_y": 35.29,
        "provenance": "MEASURED from WHAT IS STANDING: the u-vestvik "
                      "portal_wood sits at (-915.0, 35.29, 1883.4), seated on "
                      "vestvik-hall-1's levelled pad (terrain_write seq 425, "
                      "target_y 35.29). NOT from plan.json and NOT from "
                      "SETTLEMENT_SITES' pad_y 33.99, which is 1.30 m low and "
                      "is a district figure no ground in the village is at. "
                      "RE-VERIFIED LIVE 2026-09-19 by VestvikBuild and it "
                      "AGREES to 0.000 m, by two independent instruments: the "
                      "standing portal_wood's own ZDO reads (-915.000, 35.290, "
                      "1883.400) on a prefab-scoped findObjects paired with "
                      "objects_count Total 1; and the SAVE-derived applied "
                      "ground at that XZ reads 35.2900 against a generated "
                      "36.6006, i.e. the pad's own -1.3106 m cut is on the "
                      "world",
        "why": "13.24 m from the portal a player steps out of, and as close as "
               "the write gate allows: vestvik-hall-1's hard radius is 19.57 m "
               "and this point is 20.09 m out",
    },
    "r4-harbour-gateway": {
        "island": "west", "patch": "westisle", "xz": (-1015.0, 1636.0),
        "kind": "waterfront_terminal", "level_y": 32.869,
        "provenance": "MEASURED: fb_fortress_wall_gateway (seq 2105) stands at "
                      "(-1010.2, 32.869, 1641.59) yaw 125, its footing taken by "
                      "waterfront.py::port_curtain from the composed applied "
                      "surface at its own XZ. RE-VERIFIED LIVE 2026-09-19 by "
                      "VestvikBuild: the standing gateway reads (-1010.200, "
                      "32.870, 1641.590), so the pin is right to 0.001 m. The "
                      "save-derived applied GROUND at that XZ is 32.7991, 0.070 "
                      "m below the piece's own origin, which is the piece "
                      "sitting on its footing rather than a disagreement",
        "why": "7.37 m short of the gateway itself, which is the closest any "
               "ribbon class can legally get -- see NO_ROAD",
    },
}
_KEPT, _WITHHELD = seed_scoped(
    R3R4_NODES, measured_on=worldseed.DEFAULT_SEED,
    owner="Crossings/VestvikBuild: no per-seed derivation of the Vestvik "
          "quay road -- r3-n0b-junction, r3-vestvik and r4-harbour-gateway "
          "were read live off Ulfsland's standing N0b, portal and quay "
          "gateway")
NODES.update(_KEPT)
FOREIGN_NODES.update(_WITHHELD)

NO_ROAD.update({
    "harbour-vestvik-gateway-itself": "the quay's own gateway "
        "fb_fortress_wall_gateway (-1010.2, 1641.59) stands 6.82 m from a "
        "VANILLA WoodHouse1 at (-1005.2, 1646.3) whose write-gate radius is "
        "13.0 m for a 6 m trunk and 12.0 m for a 4 m spur -- so the gateway is "
        "inside its own keep-out by 6.18 m and NO ribbon can put a carriageway "
        "sample on it. MEASURED by an exhaustive 1 m legality sweep over "
        "x[-1036,-998] z[1626,1672]: 534 of 1,833 cells are legal and the "
        "closest reachable one is (-1015, 1636) at 7.37 m. R4 terminates there, "
        "pinned to the gateway's own footing datum, and the last 7.37 m is a "
        "walk through the arch",
    "vestvik_direct_from_brgs2": "W8-brgs2-vestvik (2836.1 m, planned, never "
        "built) shares a corridor with the BUILT N0 for its first ~700 m at "
        "4.2-46.2 m separation, i.e. inside the 29.0 m shared-corridor width for "
        "much of it. Building it would grade two batters toward each other and "
        "dig the ditch that already bit T12 at the temple. R3 merges into the "
        "network instead and costs 814.8 m against 2836.1",
})

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
    # R3/R4: geometry is HELD in segments.yaml (see R3R4_NODES). These edges
    # exist so `network.py plan --only ...` has an entry to name and so the
    # network file is self-describing -- but a bare re-plan re-SEARCHES them on
    # the worldgen field and will not reproduce the built line.
    {"id": "R3-n0bjunction-vestvik", "a": "r3-n0b-junction", "b": "r3-vestvik",
     "cls": "trunk", "pri": 3, "width_m": 6.0,
     "why": "the road to Vestvik. 814.8 m, 8% design grade with NO relaxation, "
            "0 fords, 0 bridges, 0 samples past the apply clamp, location gate "
            "clear over 12,301 generator instances and 25 mod keep-outs, "
            "WALKABLE at 0.0801 over the 8 m baseline"},
    {"id": "R4-vestvik-harbour", "a": "r3-vestvik", "b": "r4-harbour-gateway",
     "cls": "trunk", "pri": 3, "width_m": 6.0,
     "why": "the spur from Vestvik to its quay: 388.0 m, one 7.09 m ford 0.34 m "
            "deep kept as terrain, WALKABLE at 0.0804. It MUST be routed "
            "land-only: the first route took an 80 m shoreline shortcut that "
            "find_crossings merged into a 50.6 m water crossing, and the "
            "crossing-window straighten then pulled the line to 1.13 m of a "
            "WoodHouse1 the mask had correctly cleared"},
]
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
# landmass reaches.  `J<n>` ids; nothing is typed.  A publication that
# predates the verdict has no roaded site by this definition and gets none:
# its authored topology is all it ever had.
# ---------------------------------------------------------------------------
_ROADED_SITES = {sid: s for sid, s in SETTLEMENT_SITES.items()
                 if s.get("road") and s["road"]["roaded"]}
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
