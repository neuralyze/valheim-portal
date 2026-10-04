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
    # ---- the 11 extremity and 4 abutment nodes ARE NOT HERE ----
    # They are GEOGRAPHY, they are DERIVED PER SEED, and they are merged in
    # below from `geography.load()`.  See the note above `NODES.update`.
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
# SO THE COORDINATE IS READ FOR THE SEED BEING PLANNED, exactly as
# `plan.ANCHORED_TOWNS` and `plan.ANCHORED_OUTLIERS` do one directory over, and
# `ANCHORED_INSTALLATIONS` is DERIVED FROM THE RECORDS ABOVE rather than
# retyped: a restated population is the next thing to drift from the first.
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

# THE AUTHORED FALLBACK, one entry per seed that has one, taken from the records
# above so the two can never disagree.  A seed with no declaration and no
# placement set is REFUSED -- it has not sited its portal hall yet, and that is
# a missing prerequisite, not a crossing the planet cannot have.
ANCHORED_INSTALLATIONS: dict[str, dict[str, tuple[float, float]]] = {
    worldseed.DEFAULT_SEED: {_n: NODES[_n]["xz"] for _n in PLACEMENT_OF},
}


def installation_xz(seed: str | None = None,
                    world: str | None = None) -> dict[str, tuple[float, float]]:
    """Where this seed's 8 authored installations actually stand.

    Read from `worlds/<world>/*/placements.yaml` FOR THE SEED BEING PLANNED,
    through `worldseed.require_scope`, so a placement set solved on another seed
    is refused rather than transcribed.  Falls back to `ANCHORED_INSTALLATIONS`
    only when this world has NO placement set at all, and refuses when it has
    neither -- because a road planned to a portal hall nobody has sited is the
    59% defect wearing a different header.
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
    if want_s in ANCHORED_INSTALLATIONS:
        return dict(ANCHORED_INSTALLATIONS[want_s])
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
        f"{want_s!r}\n"
        f"or declare them for this seed in spec.ANCHORED_INSTALLATIONS.")


for _nid, _xz in installation_xz().items():
    NODES[_nid]["xz"] = _xz
    NODES[_nid]["provenance"] = (
        "MEASURED, worlds/<world>/*/placements.yaml solved centre for this seed"
        if worldseed.seed() not in ANCHORED_INSTALLATIONS
        or (_HERE.parent / "worlds" / str(worldseed.world())).is_dir()
        else "DECLARED, spec.ANCHORED_INSTALLATIONS for this seed")

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
# Now: the SOLVER is the source, and the numbers this project has MEASURED
# against standing ground are a PIN LAYER on top of it -- visible, per field,
# each with its reason, and applied only to the world they were measured in.
#
# THE PIN LAYER IS NOT A CONVENIENCE.  Several of these datums were measured
# off objects that are STANDING, and a solver figure is not better just because
# it is newer.  The worked case is `vestvik`: `sites.yaml` publishes
# `pad_y: 33.99`, which is the DISTRICT figure, and no ground in the village is
# at it -- the u-vestvik portal_wood sits at 35.29 on vestvik-hall-1's levelled
# pad (terrain_write seq 425), re-verified live 2026-09-19 against the
# save-derived applied ground, which reads 35.2900 against a generated 36.6006.
# 33.99 is 1.30 m LOW.  See `R3R4_NODES["r3-vestvik"]`, which already routes to
# 35.29 and would silently disagree with this table if the solver figure were
# taken.  A measured datum must never be replaced by a solved one without the
# replacement being visible; that is what `pad_y_measured` below is for.
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
# constant: it named Pirate68's file in this module's pin refusal and in the
# `source` every adoption reports, on every seed.


# THE PINS ARE SCOPED TO THE WORLD THEY WERE MEASURED IN.  On any other
# seed/world they are not applied at all and the solver's answer stands, which
# is the whole point: a number measured off Ulfsland's ground is meaningless on
# a fresh seed and carrying it over is how a fresh build gets Ulfsland's
# coordinates.
PIN_SCOPE = {"world": worldseed.DEFAULT_WORLD, "seed": worldseed.DEFAULT_SEED}

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
    """`settlements/sites.yaml` as this module's site records, UNPINNED.

    No fallback.  A missing solver file used to be survivable because a frozen
    copy sat here; that copy is what went stale.  Refusing loudly is the only
    honest behaviour left.

    AND A PRESENT FILE IS NOT AUTOMATICALLY THIS SEED'S FILE.  MEASURED
    2026-09-21: with `PIN_SCOPE` correctly declining to apply a single pin on
    seed `Fresh42`, this function still handed that run all 16 of Ulfsland's
    solved sites at Ulfsland's coordinates -- `stenvik` at (496.0, 904.0),
    `vestvik` at (-906.0, 1846.0) -- out of a file whose first three lines read
    `world: Ulfsland`, `seed: Pirate68`, `seed_hash: 147627509`.  The pin layer
    above was the guard on the roof; this is the floor it was standing on.  A
    scoped pin over an unscoped base is not scoped.
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
        }
    return out


# ---------------------------------------------------------------------------
# THE PIN LAYER.  One entry per site this network holds a number against the
# solver for, listing ONLY the fields that differ and WHY each one differs.
# Anything not listed comes from `sites.yaml`.
#
# HOW THESE 15 CAME TO EXIST, stated once rather than per row: they are the
# PROVISIONAL publication the live Ulfsland network was routed, profiled,
# stamped and ledgered against.  `roads/segments.yaml` -- 1.1 MB, 37 segments,
# in the ground -- is fitted to THESE coordinates.  `sites.yaml` has since been
# re-solved with a wider pad-radius convention (a watchtower's keep-out went
# 12.0 -> 16.1) and with re-measured districts; adopting those silently would
# move the plan off the road.  So they are pinned, and the pin says so.
# ---------------------------------------------------------------------------
PINNED_SITES: dict[str, dict] = {
    "stenvik": {},
    "vestvik": {
        "pad_y_measured": 35.29,
        "pad_y_why":
            "sites.yaml's 33.99 is the DISTRICT figure and is 1.30 m LOW: the "
            "u-vestvik portal_wood stands at (-915.0, 35.29, 1883.4) on "
            "vestvik-hall-1's levelled pad (terrain_write seq 425, target_y "
            "35.29), re-verified live 2026-09-19 -- save-derived applied ground "
            "35.2900 against a generated 36.6006, i.e. the pad's own -1.3106 m "
            "cut is on the world. R3 already arrives on 35.29 "
            "(R3R4_NODES['r3-vestvik']); taking 33.99 here would put this "
            "table 1.30 m below the road that reaches it.",
        # The number the BUILT network was routed against, kept so the plan
        # does not move under the road. See `pad_y_measured` for the truth.
        "pad_y": 33.99,
        "pad_y_as_built_why":
            "what segments.yaml was fitted to; W8/W12 are in the ground at it. "
            "Changing it here re-plans a written road, which is a separate "
            "decision from recording the correct datum.",
    },
    "hognest":  {"pad_radius_m": 32.0,
                 "why": "routed against the 32.0 m keep-out; sites.yaml's 33.1 "
                        "is the re-solved convention"},
    "wtspawn":  {"xz": (110.0, -104.0), "pad_radius_m": 12.0, "pad_y": 76.03},
    "wtsouth":  {"xz": (538.0, 40.0),   "pad_radius_m": 12.0, "pad_y": 82.99},
    "wttown":   {"xz": (820.0, 738.0),  "pad_radius_m": 12.0, "pad_y": 39.86},
    "wtpeak":   {"xz": (1418.0, 1530.0), "pad_radius_m": 12.0, "pad_y": 103.64},
    "wtnorth":  {"xz": (152.0, 1194.0), "pad_radius_m": 12.0, "pad_y": 32.80},
    "wteast":   {"pad_radius_m": 12.0,  "pad_y": 55.44},
    "lhsouth":  {"xz": (-102.0, -916.0), "pad_radius_m": 12.0, "pad_y": 32.52},
    "lheast":   {"xz": (2354.0, 156.0), "pad_radius_m": 12.0, "pad_y": 31.48,
                 "priority": "none",
                 "note": "x=2354 is OUTSIDE the mainland patch (x <= 2398 but the "
                         "island's own bbox ends at 1920), so it is on a separate "
                         "landmass and gets no road. Portal only.",
                 "why": "sites.yaml says road_priority spur; MEASURED here it is "
                        "on its own landmass, so the priority is overridden to "
                        "none rather than left to produce an unroutable edge"},
    "lhwest":   {"xz": (-1350.0, 2358.0), "pad_radius_m": 12.0, "pad_y": 33.01},
    "treesth":  {"xz": (546.0, 482.0),  "pad_radius_m": 10.0, "pad_y": 75.46},
    "treewest": {"xz": (-530.0, 850.0), "pad_radius_m": 10.0, "pad_y": 41.39,
                 "note": "x=-530 is WEST of the spawn island's bbox (x >= -326), so "
                         "this sits on West Isle, not the spawn island. Routed from "
                         "the west-isle network, and Settlements told."},
    "treenear": {"xz": (-614.0, -236.0), "pad_radius_m": 10.0, "pad_y": 35.20},
}

# Solver sites this network does NOT adopt, and why.  Listed rather than
# filtered by a predicate, because "the predicate happened to exclude it" is
# not an answer to "why is there no road to the keep".
NOT_ADOPTED: dict[str, str] = {
    "sudrberg-keep":
        "sites.yaml adds it on window south33, a landmass with no road network "
        "and no edge in EDGES. It was not in the publication the built network "
        "was routed against, so adopting it now would add a node that "
        "roads/segments.yaml has never seen. Portal only until an edge exists. "
        "NOT pinned geometry -- a fresh seed adopts it, because a fresh seed "
        "has no built network to disagree with.",
}

# Which fields a pin is allowed to carry as VALUES (everything else in a pin
# entry is prose: `why`, `pad_y_why`, `pad_y_as_built_why`, `note`).
_PIN_VALUE_FIELDS = ("xz", "pad_radius_m", "pad_y", "priority", "type",
                     "tag", "patch", "island")


def adopt_sites(solved: dict[str, dict] | None = None,
                pins: dict[str, dict] | None = None,
                *, seed: str | None = None, world: str | None = None,
                ) -> tuple[dict[str, dict], dict]:
    """Solver sites + the pin layer, and a RECORD of every override applied.

    The record is the visible half.  A silent merge would be the same defect
    as the frozen table: a consumer reading `pad_y` cannot tell whether it is
    the solver's or a pin's, and for vestvik those are 1.30 m apart.
    """
    solved = load_solved_sites() if solved is None else solved
    pins = PINNED_SITES if pins is None else pins
    in_scope = worldseed.is_default(seed, world)

    sites: dict[str, dict] = {}
    applied: dict[str, dict] = {}
    datum_conflicts: list[dict] = []
    for sid, s in solved.items():
        if s["solver_id"] in NOT_ADOPTED and in_scope:
            continue
        rec = {k: v for k, v in s.items() if k != "solver_id"}
        pin = pins.get(sid) if in_scope else None
        if pin is not None:
            deltas = {}
            for f in _PIN_VALUE_FIELDS:
                if f in pin and pin[f] != rec.get(f):
                    deltas[f] = {"solved": rec.get(f), "pinned": pin[f]}
                    rec[f] = pin[f]
            if "note" in pin:
                rec["note"] = pin["note"]
            if "pad_y_measured" in pin:
                # NOT applied to `pad_y`: it would move a road that is in the
                # ground. Carried beside it so no reader can mistake the
                # as-built datum for the measured one.
                rec["pad_y_measured"] = pin["pad_y_measured"]
                datum_conflicts.append({
                    "site": sid,
                    "solved_pad_y": solved[sid]["pad_y"],
                    "as_built_pad_y": rec["pad_y"],
                    "measured_pad_y": pin["pad_y_measured"],
                    "measured_minus_solved_m":
                        round(pin["pad_y_measured"] - solved[sid]["pad_y"], 2),
                    "why": pin["pad_y_why"],
                })
            if deltas:
                applied[sid] = deltas
        sites[sid] = rec
    unknown = sorted(set(pins) - set(sites))
    if unknown and in_scope:
        # A pin naming a site the solver no longer emits is a pin nobody will
        # ever see fire. It has to be an error: this is the failure mode that
        # lets a "fixed" datum quietly stop applying.
        raise SystemExit(
            f"PINNED_SITES names {unknown}, which {worldseed.sites_path()} "
            f"does not contain. A pin that cannot fire is a correction that has "
            f"silently stopped being applied.")
    provenance = {
        "source": str(worldseed.sites_path()),
        "pin_scope": PIN_SCOPE,
        "pins_in_scope": in_scope,
        "pins_applied": applied,
        "not_adopted": NOT_ADOPTED if in_scope else {},
        "datum_conflicts": datum_conflicts,
        "why_not_in_scope": None if in_scope else
            f"seed/world is not {PIN_SCOPE}; every pin is a number MEASURED "
            f"on Ulfsland's ground and does not describe this world. The "
            f"solver's answer stands unmodified.",
    }
    return sites, provenance


SETTLEMENT_SITES, SITE_PROVENANCE = adopt_sites()

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
# THE RULE IS `PINNED_SITES`'s: a measured node applies on the seed it was
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

UNREACHABLE_EDGES: list[dict] = []
for _e in list(EDGES):
    _gone = [n for n in (_e["a"], _e["b"]) if n not in NODES]
    if not _gone:
        continue
    _unexplained = [n for n in _gone
                    if n not in GEOGRAPHY_ABSENT and n not in FOREIGN_NODES]
    if _unexplained:
        raise SystemExit(
            f"REFUSING: edge {_e['id']!r} names node(s) "
            f"{', '.join(_unexplained)} that this seed's geography does not "
            f"have and does not explain. A terminus that is absent for no "
            f"recorded reason is a road to nowhere; re-emit "
            f"roads/geography.yaml for this seed, or the authored EDGES have "
            f"to say why this leg does not exist.")
    EDGES.remove(_e)
    UNREACHABLE_EDGES.append(_e)
    NO_ROAD[_e["id"]] = GEOGRAPHY_ABSENT[
        next(n for n in _gone if n in GEOGRAPHY_ABSENT)]["why"]

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
