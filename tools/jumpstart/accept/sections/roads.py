#!/usr/bin/env python3
"""ROADS - can a walker get from one end of this world to the other.

Moved out of `roads/accept.py` unchanged.  Its four checks are the ones that
were written and proved first, and the gate FAILS Ulfsland on them today:

  TRAVERSABILITY  every built centreline at 1 m, every adjacent pair against
                  tan(38 deg) = 0.7813 m per metre.  There is NO step-height
                  constant in Valheim: on terrain the slide angle is the
                  entire limit, so it is the only limit used, for ground and
                  for pieces alike.
  CONNECTIVITY    walked, not computed.  A graph edge is not a connection:
                  the route that produced "portal hall -> Vestvik 3,248.7 m"
                  had a 6.12 m wall in it.  Here the graph's edges ARE the
                  steps, so an edge cannot exist across a step a player
                  cannot take.
  OBSTRUCTION     the save's own object census, classified by each prefab's
                  own rotated geometry.  A piece is a BLOCKER only if it
                  rises out of the walking surface by more than the same
                  0.7813 m AND leaves no lateral gap wide enough for the
                  player's own capsule - both measured, neither assumed.
  COVERAGE        what the instrument could not see: an object in a corridor
                  whose prefab the geometry corpus has never measured.  It is
                  its own FAILING check, not an assumption of harmlessness.

WHAT LEFT.  ORIENTATION was never a road property - 385 inverted panels in
74 bodies are spread over the whole world and only a handful are near a road.
The instrument stayed (`instruments.check_orientation`, unchanged); the
section that reports it is `structures`.
"""
from __future__ import annotations

import ast
import heapq
import json
import math
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace

from .. import instruments as I
from .. import termination as T
from ..registry import Case, Finding, Section, SectionResult, register
from . import north_transport as NT

STEP_LIMIT_PER_M = I.STEP_LIMIT_PER_M
SAMPLE_M = I.SAMPLE_M
# Valheim's sea level, and the same number `roads/crossings.yaml` records as
# `water_surface_y` on every declared crossing.
WATER_Y = 30.0
# TWO VERTICES OF DIFFERENT SEGMENTS THIS CLOSE ARE ONE JUNCTION.  Named
# rather than written as a literal inside the graph build, because the
# severance diagnosis has to be able to say "these two roads miss each other
# by 4.1 m" instead of silently declining to join them.
JUNCTION_M = 3.0
# HOW FAR THE SEVERANCE DIAGNOSIS LOOKS for the cheapest missing link between
# two components.  Wider than JUNCTION_M on purpose: a near miss has to be
# reported AS a near miss, with its metre, and not as an unexplained void.
LINK_SEARCH_M = 12.0
# A PROPOSED CUT SMALLER THAN THIS IS NOT A CUT, IT IS NOISE IN THE READING.
# The same number `roads/applied.py::AUTHOR_TOLERANCE_M` uses to decide
# whether a write AUTHORS a sample and `roads/regrade.py::MIN_CUT_M` refuses
# below, stated here rather than imported because this section may never
# depend on the tools it judges -- and pinned by a selftest case so the two
# cannot drift apart silently.
DISPOSAL_MIN_CUT_M = 0.01
# HOW FAR THE DISPOSAL IS ALLOWED TO REACH before the read that sized it is
# declared too small.  The cone decays at the slide limit per metre, so a
# rise of R metres closes inside ceil(R / limit) + DISPOSAL_PAD_M of its own
# seed; the pad is there so the cone's last ring and the field's own boundary
# are never the same samples.
DISPOSAL_PAD_M = 3


# ---------------------------------------------------------------------------
# POPULATION - which roads exist, and where their centrelines run
# ---------------------------------------------------------------------------
def built_segments(ledger) -> dict[str, dict]:
    """Every road segment the ledger records a terrain write for.

    The ledger defines the POPULATION - which roads exist and where they run -
    and nothing else.  Not one height in this file comes from it.  The newest
    write wins, because a re-route (T4 was re-routed around huts, 203 of its
    729 stations moved) means the earlier polyline is abandoned ground and not
    road any more.
    """
    latest: dict[str, dict] = {}
    counts: dict[str, int] = defaultdict(int)
    for line in ledger.read_text().splitlines():
        if '"road_segment"' not in line:
            continue
        d = json.loads(line)
        p = d.get("params") or {}
        if p.get("role") != "road_segment" or d.get("op") != "terrain_write":
            continue
        name = p["name"]
        counts[name] += 1
        prof = p["profile"]
        latest[name] = dict(
            name=name,
            seg=name[len("road_"):].replace("_", "-"),
            actor=d.get("actor"),
            half_width_m=float(prof["half_width_m"]),
            shoulder_m=float(prof["shoulder_m"]),
            nodes=[(float(a), float(b)) for a, b, *_ in prof["nodes"]])
    for name, s in latest.items():
        s["writes"] = counts[name]
        parts = s["seg"].split("-")
        s["code"], s["from"], s["to"] = parts[0], parts[1], parts[-1]
    return latest


# ---------------------------------------------------------------------------
# THE STEP PREDICATE - one definition, used by traversability, by
# connectivity, and by the selftest cases, so the case proves the rule the
# gate actually applies rather than a restatement of it.
# ---------------------------------------------------------------------------
def min_step(a: list, b: list) -> float:
    """The smallest height change between any surface here and any there.

    `min`, because a walker picks his surface: a deck over a hole is a second
    surface at the same xz and taking it is what makes a bridge not a defect.
    """
    return min(abs(p[0] - q[0]) for p in a for q in b)


def edge_ok(a: list, b: list, d: float) -> bool:
    """Can a walker step from one station to the next.  This IS the
    connectivity rule: an edge exists only where a step exists."""
    if not a or not b:
        return False
    return min_step(a, b) <= STEP_LIMIT_PER_M * max(d, 1e-6)


# ---------------------------------------------------------------------------
# WHICH SURFACE THE WALKER IS ON - AND IT IS NOT ANSWERABLE ONE STATION AT A
# TIME.
# ---------------------------------------------------------------------------
# `min_step` above says it in one line: "a walker picks his surface: a deck
# over a hole is a second surface at the same xz and taking it is what makes
# a bridge not a defect."  `surfaces_at` says the same thing: "the walk uses
# the one it can reach from the abutment".  The OBSTRUCTION check did not.
# It chose the walker's surface at each station INDEPENDENTLY, climbing from
# the ground directly beneath him:
#
#     y = surf[0][0]
#     for cand in surf[1:]:
#         if cand[0] - y <= STEP_LIMIT_PER_M:
#             y = cand[0]
#
# A deck is therefore only ever "underfoot" where the GROUND UNDER IT is
# within one step of it - i.e. at the abutment, and nowhere over the span.
# Over the middle of its own bridge the instrument puts the walker in the
# water and reports his deck as a wall across the carriageway.
#
# MEASURED, N1-wnorth-shelf on frozen save 04e68ec3.  A 42 m x 6 m deck of
# `wood_floor` runs x -88..-48 at z 2708/2710/2712, deck top y 32.500,
# carried on `wood_pole_log_4` piles down to y 28.370.  Along z=2710 the
# applied bed reads 31.773, 31.772, 31.662, 30.020, 29.848, 29.707 - it
# falls off a 1.64 m drop into water (WATER_LEVEL 30.0), which is the entire
# reason the deck is there.  Station by station:
#
#     x=-85  32.500 - 31.772 = 0.728 <= 0.7813   walker ON the deck
#     x=-84  32.500 - 31.662 = 0.838 >  0.7813   walker DROPPED to the bed,
#                                                his own deck becomes a
#                                                blocker, widest gap 0.01 m
#
# and `severance` then refused the edge as `blocked`, category c_structure,
# prefabs ["wood_floor"], BEFORE `edge_ok` was ever consulted - which would
# have answered `min_step([31.772, 32.500], [31.662, 32.500]) = 0.000`, a
# clean transition along the deck.  Two definitions of "the surface the
# walker is on" in one section, and where they disagreed the one that had
# never been written down silently severed a network the other called
# walkable.  Four destinations were gated on it, and the remedy the report
# named - move the structure - is a remedy that would have forbidden the
# carriageway to itself.
#
# SO REACHABILITY IS ALONG THE ROAD, NOT ONLY FROM UNDERNEATH.  The walker
# may stand on a surface he can reach either from the ground beneath him
# (the rule this check already applied, unchanged) or from the surface he is
# standing on at an ADJACENT STATION, judged at the same tan(38 deg) per
# metre as everything else.  A deck with a hole in it still refuses: there
# is no deck SURFACE at the hole's stations, so nothing propagates across
# it, and `crossings` keeps its own metre-by-metre verdict.
#
# A SURFACE KNOWN ONLY TO A BOUNDING BOX IS NOT A PLACE THIS GATE WILL PUT A
# WALKER IN ORDER TO EXCUSE AN OBSTRUCTION.  58 % of the collider corpus is
# MeshCollider bounds, an UPPER bound on the solid, and here the error runs
# in the excusing direction - a box top is a floor nobody measured.  The
# standing rule is that a bounds-only verdict is advisory and never a hard
# refusal; its mirror is that a bounds-only top is never a hard excuse.  The
# ground-up climb keeps whatever it already did, because that is not a new
# licence this function is handing out.
#
# AND THE RULE IS DIRECTIONAL, WHICH IS HOW IT CAME TO BE HALF-IMPLEMENTED.
# Written first, it guarded only the RECEIVING side: propagation may not step
# ONTO a box.  It could still propagate FROM a station whose own surface IS
# one - and that is the EXCUSING direction, an upper bound carried outwards
# and spent again at every station it reaches.
#
# THE CASE BELOW IS CONSTRUCTED, AND THAT IS DELIBERATE, BECAUSE THE REAL
# SIGHTING THAT PROMPTED THIS WAS MIS-ATTRIBUTED AND THE MIS-ATTRIBUTION IS
# WORTH MORE THAN THE SIGHTING.  It was reported that N0b station 656
# (-138.0, 1998.6) - bed 43.540, carried ONLY by `stone_stair`, a
# MeshCollider bound, top 43.953 - propagated that over-claimed height
# backward six stations and cleared four blocked links.  MEASURED
# 2026-09-22 on frozen save 04e68ec3, both arms over the identical station
# list: over-limit links 1 -> 1.  DELETING THE STAIR FROM THE SURFACE LISTS
# ENTIRELY changes only st656's own surface (43.953 -> 43.540) and nothing
# else.  THE STAIR CARRIES NOTHING BUT ITSELF.  The four links come from
# st655's OWN ground-up climb - bed 43.360 to `stone_floor_2x2` at 43.953, a
# 0.593 m step onto a MEASURED solid - propagating backward along the tight
# stone floor.  st656 is INERT BY CONSTRUCTION: st655 is already at 43.953
# so there is nothing to raise, and st657 has no surface at 43.953 at all,
# its solids being `stone_pillar` at 44.953 and 46.953.  A LEAF CARRIER
# CANNOT PROPAGATE.
#
# So the detector was right and the causal story was not.  The hole in the
# rule is real and this function closes it; it simply never fired there.  A
# fixture resting on that reading would have pinned a state that never
# existed.
#
# A STATION ON THE GROUND HAS NO CARRIER AT ALL AND MUST KEEP PROPAGATING.
# `surfaces_at` always returns `(bed, None)` as its first entry, so "no solid
# holds this surface up" is the TERRAIN, which is measured.  Collapsing that
# into the refusal would switch propagation off for every ordinary road and
# re-open the defect this relaxation was written to close.
def standing_surfaces(pts, surfs, limit_per_m: float = STEP_LIMIT_PER_M
                      ) -> list[float | None]:
    """The y a walker is STANDING ON at each station of one chain.

    `pts` is the station positions in order, `(x, z)` each; `surfs` the
    `instruments.surfaces_at` list for each, ascending.  Returns one height
    per station, or None where nothing was read.

    Two sources of reach, applied to a fixpoint because a deck is reached
    from its abutment and then carried along itself:
      * from the ground beneath - the prior rule, verbatim;
      * from the chosen surface of a neighbouring station, over the
        separation those two stations actually are apart, and only when that
        neighbour's own surface is MEASURED rather than merely bounded.
    """
    ys: list[float | None] = []
    for ss in surfs:
        if not ss:
            ys.append(None)
            continue
        y = ss[0][0]
        for cand in ss[1:]:
            if cand[0] - y <= limit_per_m:
                y = cand[0]
        ys.append(y)

    def _carried_only_by_bounds(k: int) -> bool:
        """Is every solid whose top IS station k's chosen surface a bound?

        An EMPTY carrier list is the ground - `surfaces_at`'s `(bed, None)`
        entry - and the ground propagates.  That distinction is the whole
        correctness of this predicate.
        """
        car = [sol for top, sol in surfs[k]
               if sol is not None and abs(top - ys[k]) <= 1e-9]
        return bool(car) and all(sol.bounds_only for sol in car)

    # Only ever RAISES a station's surface, and only onto a top that is
    # measured, so the relaxation terminates: each update moves one station
    # to a strictly higher member of its own finite surface list.
    work = [j for j, y in enumerate(ys) if y is not None]
    while work:
        j = work.pop()
        # THE SENDING SIDE.  A walker standing on nothing but a bounding box
        # is standing on an upper bound; he may keep it, but he may not lend
        # it to a neighbour.
        if _carried_only_by_bounds(j):
            continue
        for nb in (j - 1, j + 1):
            if not 0 <= nb < len(ys) or ys[nb] is None or ys[j] is None:
                continue
            d = math.hypot(pts[nb][0] - pts[j][0], pts[nb][1] - pts[j][1])
            lim = limit_per_m * max(d, 1e-6)
            best = ys[nb]
            for top, sol in surfs[nb]:
                if top <= best or abs(top - ys[j]) > lim:
                    continue
                if sol is not None and sol.bounds_only:
                    continue
                best = top
            if best > ys[nb]:
                ys[nb] = best
                work.append(nb)
    return ys


# THE CROSSINGS CORPUS HAS A PER-SEED SLOT, AND THIS SECTION READ THE DEFAULT
# SEED'S.  `worldseed.crossings_path` is the slot - `roads/crossings.yaml` for
# the default seed, `crossings.<seed>.yaml` for any other - and
# `roads/network.py::emit` writes through it, so on a fresh seed the planner
# publishes the per-seed file and nothing ever writes the bare name.  Every
# read in this section still opened the bare name.  MEASURED on the Fresh42
# drive-2 gate (save a521b70356e7, /tmp/regen2/d2_gate.json):
#
#   roads/crossings.yaml          world Ulfsland / seed Pirate68  -> refused
#                                 "measured on seed 'Pirate68'", 1C_UNSERVED
#   roads/crossings.Fresh42.yaml  world Fresh42 / seed Fresh42, 9 detected
#                                 crossings - NEVER READ
#
# and with no span in the population, S4-meadhall-southeast-x01 - a DECLARED
# 26 m bridge from bank (396,-424) to (396,-450), 14 `is_bridge` stations
# the ribbon stopped at by construction - was walked as ORDINARY ROAD GROUND:
# four "traversability" refusals at stations 341-346 (1.138 m worst) and a
# connectivity refusal, all ACTIONABLE, each prescribing a TERRAIN CUT inside
# a water crossing whose only remedy is a deck.  Step 4's own walkability
# line had already named the run a bridge; the gate did not know it was one.
# A GUARD THAT REFUSES A WORLD WHICH HAS A CORRECT CORPUS OF ITS OWN IS A
# BLOCKER WEARING A GUARD'S CLOTHES (`instruments.read_scoped_corpus`).
#
# The NAME is the producer's resolver's, never a second one; the ROOT is this
# gate's, so a fixture that rebinds `instruments.JUMPSTART` to a scratch tree
# is read there; `$VH_CROSSINGS` - the resolver's own override - wins over
# both, exactly as it does for the producer.
CROSSINGS_CORPUS_BY_SEED = True      # the neuter surface: False is the defect


def crossings_corpus_path(seed: str) -> Path:
    """The crossings corpus THIS RUN'S SEED owns."""
    if not CROSSINGS_CORPUS_BY_SEED:
        return Path(I.JUMPSTART) / "roads" / "crossings.yaml"
    ws = I._worldseed()
    p = ws.crossings_path(seed)
    if os.environ.get(ws.CROSSINGS_ENV):
        return p
    return Path(I.JUMPSTART) / "roads" / p.name



def crossing_spans(ctx) -> tuple[list[dict], list[Finding]]:
    """Every crossing THE NETWORK DECLARES, resampled at the same 1 m.

    A declared crossing is one that joins TWO segments across water.  It is
    the only part of the network that can carry no `terrain_write` at all -
    `why_terrain_failed: water_body` - so it is the only part this section's
    population, "every road segment the ledger records a terrain write for",
    structurally cannot contain.  The banks come from the plan; NOTHING
    about whether a deck exists or is walkable comes from it.  That is read
    from the save, metre by metre, like every other surface in this file.

    AND "DECLARED" IS NOT ONE KEY IN ONE FILE.  This function read
    `crossings.yaml:declared` and nothing else, and reported `declared: 2`
    with no denominator beside it.  MEASURED on Ulfsland 2026-09-22 the
    network declares SIXTEEN crossings and this gate was judging TWO:

      crossings.yaml  declared   2   S1, S2           - Crossings' own
      crossings.yaml  detected  14                    - the router's
      segments.yaml   crossings[] 14 (the same ids, per segment)

    Six of the fourteen sit on segments the ledger HAS written terrain for -
    T4 x01/x02, T5 x01, W5 x01, W10 x01, S12 x01, 347.5 m of declared deck -
    and not one of them carries a `bridge_body_place` record.  Every one of
    those roads reads WALKABLE on its own terrain and ends in the air at a
    bank; the gate could not say so because the span was not in its
    population.  `S12-brgs1-boathouse` is the visible end of it: 46 of its
    74 planned stations are `is_bridge`, so its BUILT ribbon is 51.2 m
    against a planned 144.9 m, and the settlements section reported that as
    a terminus 74.6 m from its own planned A rather than as a missing deck.

    `crossings.yaml:precedence` settles the overlap in the file itself -
    "Where they describe the same channel, Crossings' numbers win" - so a
    DECLARED row replaces a DETECTED row of the same `crossing_id` and the
    substitution is recorded on the row rather than done silently.

    RETURNS `(rows, refusals)`, AND THE REFUSALS ARE NOT OPTIONAL.  This read
    used to answer `[]` for three completely different states - the file is
    absent, the file is corrupt, the file is about ANOTHER WORLD - and an
    empty list reads as A WORLD WITH NO CROSSINGS IN IT.  Every crossing
    check downstream then passed vacuously, which on `--world Fresh42` meant
    this section judged Ulfsland's sixteen channels or none of them,
    depending only on whether the repository happened to hold the file.  The
    corpus is read through `instruments.read_scoped_corpus`, the one
    gate-side guard, and a caller that ignores the second return value gets
    an empty list with the reason printed beside it.
    """
    doc, refusals = I.read_scoped_corpus(
        ctx, "the crossings corpus", resolve=crossings_corpus_path,
        produced_by="roads/route.py::find_crossings + Crossings' own "
                    "declared rows", check="crossings")
    if refusals:
        return [], refusals
    # DETECTED FIRST, DECLARED OVER THE TOP - the file's own precedence.
    rows: dict[str, tuple[str, dict]] = {}
    for c in doc.get("detected") or ():
        cid = c.get("crossing_id")
        if cid:
            rows[cid] = ("detected", c)
    for c in doc.get("declared") or ():
        cid = c.get("crossing_id")
        if cid:
            rows[cid] = ("declared", c)
    out = []
    for cid, (status, c) in sorted(rows.items()):
        a = tuple(float(v) for v in c["bank_a"]["xz"])
        b = tuple(float(v) for v in c["bank_b"]["xz"])
        span = math.hypot(b[0] - a[0], b[1] - a[1])
        n = max(1, int(round(span / SAMPLE_M)))
        joins = list(c.get("joins") or ())
        if not joins and c.get("segment_id"):
            joins = [c["segment_id"]]
        out.append(dict(
            id=cid, joins=joins, status=status,
            segment_id=c.get("segment_id"),
            a=a, b=b, span_m=span,
            deck_y=c.get("contract_deck_y")
            or c.get("required_deck_y")
            or c["bank_a"].get("road_deck_y"),
            samples=[(a[0] + (b[0] - a[0]) * k / n,
                      a[1] + (b[1] - a[1]) * k / n) for k in range(n + 1)]))
    return out, []


CORRIDOR_CELL_M = 4.0


# AN INDEX MUST NAME THE THING IT IS A FUNCTION OF, AND THE NAMING MUST BE
# CHECKED IN BOTH DIRECTIONS.  Four instruments in this tree were keyed on a
# SUPERSET or a SUBSET of what their quantity actually depends on, and every
# one of them was silent; this is the one that lives here.  Over-keying is
# silent and EXPENSIVE, under-keying is silent and FALSE, and the obvious
# repair for one is the other - so the declaration below is checked by a
# neuter in EACH DIRECTION: a sample the key must include, and a sample it
# must STILL include when the first neuter fires, so that "the neuter
# flipped" can never be satisfied by a mask that simply emptied.
CORRIDOR_MASK_DEPENDS_ON = (
    "every metre of BUILT CENTRELINE (`stations`) UNION every metre of every "
    "DECLARED CROSSING (`spans`). NOT the stations alone: a crossing is by "
    "construction the water the ribbon REFUSED to write, so it contributes "
    "NO STATION AT ALL and the two populations are disjoint rather than "
    "nested. A mask derived from one arm's population and consumed by "
    "another's is the two-populations fault one level down - in the "
    "instrument rather than in the corpus - and it makes a deck that STANDS "
    "read as a hole.")


def corridor_mask(stations, spans=(), cell: float = CORRIDOR_CELL_M,
                  reach_m: float = None) -> set:
    """The cells an object has to be in to be able to matter to this section.

    TWO POPULATIONS FEED IT, AND THE SECOND ONE WAS MISSING.  `stations` is
    every metre of built centreline; `spans` is every metre of every
    DECLARED CROSSING.  The second is not a refinement of the first - it is
    disjoint from it by construction, because a crossing is exactly the
    water the ribbon refused to write and so contributes no station at all.

    MEASURED, W10-brgs1-treenear-x01 at fp 7632b3a5674842bd: with `spans`
    empty, 7 of its 90 declared samples (k=0..6, at bank_a) fall outside
    the mask built from all 22,000 stations, so a complete 319-piece deck
    over them is not in the grid at all and the span reads 84 of 90 metres
    carried.  A gate that cannot see a deck reports the builder's correct
    work as a hole and leaves the row open.
    """
    reach = int(math.ceil((I.CORRIDOR_MASK_M if reach_m is None else reach_m)
                          / cell))
    out: set = set()
    for x, z in list(stations) + list(spans):
        cx, cz = int(x // cell), int(z // cell)
        for dx in range(-reach, reach + 1):
            for dz in range(-reach, reach + 1):
                out.add((cx + dx, cz + dz))
    return out


# ---------------------------------------------------------------------------
# ONE PHYSICAL CHANNEL, ONE OBLIGATION - the corpus check the detector needs
# ---------------------------------------------------------------------------
# THE DETECTOR RUNS PER SEGMENT.  `roads/network.py` builds `detected` as
# `[c for sg in segments for c in sg["crossings"]]`, so a channel that TWO
# routed segments both have to get across is emitted TWICE, once under each
# segment's name.  Nothing downstream ever compared the two records, and the
# consequence lands on BOTH sides of this gate's own fraction:
#
#   THE DENOMINATOR charges one channel twice - "17 declared crossings".
#   THE NUMERATOR credits ONE BUILT DECK twice - T4-x01 and T7-x02 are one
#   deck, and the headline "3 of 17 carry a walker" counted it as two of its
#   three passes.  A PASS COUNT INFLATED BY REDUNDANT RECORDS RATHER THAN BY
#   BUILT DECK.
#   AND THE SPANS THEMSELVES CROSS-CONTAMINATE: T4-x02 measured 19 of 49
#   metres on a piece because the one deck's pieces are scored against every
#   neighbouring declaration's stations as well as its own.
#
# MEASURED over roads/crossings.yaml as it stands, 3 declared + 14 detected:
#   17 declarations -> 13 DISTINCT CHANNELS, 714.33 m declared -> 539.38 m.
#   W10-brgs1-treenear-x01 = W5-brgs1-wsouth-x01      fraction 1.000
#   W7-wwest-wnorthwest-x01 = W9-vestvik-lhwest-x01   fraction 1.000
#   T4-meadhall-stathub-x01 = T7-stathub-stenvik-x02  fraction 0.761
#   T4-meadhall-stathub-x02 = T7-stathub-stenvik-x01  fraction 0.659
#   NEAREST NON-DUPLICATE 0.279 - S1 against S12-brgs1-boathouse-x01, whose
#   banks are 2.0 m apart and which are NOT the same channel.  All 136 pairs
#   measured: nothing at all lies between 0.279 and 0.659, so 0.5 is the
#   middle of an empty interval rather than a tuned tolerance.
#
# NEITHER OBVIOUS TEST WORKS AND BOTH FAILURES ARE MEASURED (the derivation
# is `roads/boardwalk.py`'s; the numbers are restated here, not imported,
# because `accept/` must not depend on the builder it judges - a gate that
# borrowed the builder's collapse could not refuse the builder's collapse):
#   ENDPOINT EQUALITY misses T4-x01/T7-x02 - their far ends differ by 2.83 m.
#   PIECE OVERLAP misses W7-x01/W9-x01 - BOTH corridors hold zero pieces, so
#     there is nothing to intersect - and FALSE-POSITIVES on S1/S12-x01,
#     which share 14 of S12's 17 pieces at 0.824 min-side and are DIFFERENT
#     CHANNELS.  Collapsing those two would silently halve a real obligation.
# So the test is CORRIDOR AREA at the deck's own measured half-width.
#
# `assemble.bridge` places `width_tiles=3` wood_floor at lateral -2/0/+2 m and
# `wood_floor` is a 2.000 m solid, so a deck occupies -3.000..+3.000 m about
# its centreline; the overhang is one 2 m station beyond each bank.
CROSSING_DECK_HALF_WIDTH_M = 3.000
CROSSING_CORRIDOR_OVERHANG_M = 2.0
CROSSING_CORRIDOR_SAMPLE_M = 0.5
CROSSING_DUPLICATE_FRACTION = 0.5


# A TRAVERSABILITY STEP INSIDE A DECLARED DECK'S CORRIDOR IS THE DECK'S.
# Both ends of the lattice edge must lie between the two banks and within
# the deck's own half-width of the bank-to-bank line: that is the ground the
# ribbon refuses to write BY CONSTRUCTION and the deck is declared over, so
# its remedy is the crossing's and its class is the crossing's.  An edge with
# one end on the approach is not in it: that ground is the road's.
TRAVERSABILITY_INSIDE_A_SPAN = True      # the neuter surface


def declared_span_of(at, spans: dict,
                     half_width_m: float = CROSSING_DECK_HALF_WIDTH_M
                     ) -> str | None:
    """The declared crossing whose deck corridor holds BOTH ends of the
    lattice edge `at` = [ax, az, bx, bz], or None.  `spans` maps crossing
    id to its span row (`bank_a`, `bank_b`)."""
    if not at or len(at) != 4:
        return None
    for cid, sp in sorted((spans or {}).items()):
        a, b = (sp or {}).get("bank_a"), (sp or {}).get("bank_b")
        if not a or not b:
            continue
        ux, uz = float(b[0]) - float(a[0]), float(b[1]) - float(a[1])
        span = math.hypot(ux, uz)
        if span <= 0.0:
            continue
        inside = True
        for px, pz in ((at[0], at[1]), (at[2], at[3])):
            dx, dz = float(px) - float(a[0]), float(pz) - float(a[1])
            t = (dx * ux + dz * uz) / span
            off = abs(dx * uz - dz * ux) / span
            if not (-1e-6 <= t <= span + 1e-6 and off <= half_width_m + 1e-6):
                inside = False
                break
        if inside:
            return cid
    return None



# ---------------------------------------------------------------------------
# THE PRODUCER DEFECT - a declaration further off its own centreline than a
# deck is wide
# ---------------------------------------------------------------------------
# NAMED, WITH ITS FILE AND ITS LINES.  A crossing record carries TWO lines and
# they are produced from TWO DIFFERENT POLYLINES:
#
#   roads/route.py::find_crossings line 741-746 takes `bank_a`/`bank_b` from
#     `pts[a]` / `pts[b]` - the route's polyline AS IT STANDS IN PASS ONE -
#     and snaps them to the piece grid.
#   roads/network.py::build_segment line 576 then runs
#     `pts = straighten(pts, s, wa, wb)` over EVERY crossing window, which
#     REPLACES that run of the polyline with its chord, and
#   roads/network.py::build_segment lines 677-687 writes
#     `centreline_straight_from_to` from the POST-straighten `pts[wa]`,
#     `pts[wb]`.
#
# `c["bank_a"]["xz"]` IS NEVER RE-READ AFTER THE STRAIGHTEN THAT MOVED THE
# LINE IT NAMES.  The bank is keyed on the pre-straighten polyline; the
# centreline it is supposed to lie on is keyed on the post-straighten one.
# That is this session's own defect class - A KEY THAT NAMES A DIFFERENT
# POPULATION THAN THE QUANTITY DEPENDS ON - in the producer, and it is why
# T4-meadhall-stathub-x02's reconciliation REFUSES TO MOVE in either
# direction: the record and the centreline describe DIFFERENT WATER.
#
# MEASURED over roads/crossings.yaml as it stands, 17 records, 15 of which
# declare a centreline (S1 and S2 are hand-declared and carry none):
#
#   S2-stathub-eastlobe-x01  6.069   T7-stathub-stenvik-x01   4.405
#   S13-stathub-dock-x01     5.439   T4-meadhall-stathub-x02  4.077
#   S12-brgs1-boathouse-x01  5.133   S6-stenvik-hognest-x01   3.652
#   W9-vestvik-lhwest-x02    4.628   T5-temple-dockshore-x01  3.618
#                                    T4-meadhall-stathub-x01  3.024
#   -- then W9-x01 1.871, W7 1.762, W10 1.016, W5 1.016, T7-x02 0.789,
#      T9-sb01 0.000.
#
# NINE OF THE FIFTEEN ARE FURTHER OFF THEIR OWN CENTRELINE THAN A DECK IS
# HALF-WIDE, so for nine records NO DECK THIS TREE BUILDS ON THAT ROAD COVERS
# THE LINE THE RECORD DECLARES.  13 of the 15 are off by 1.016 m or more.
def declaration_off_centreline(
        records, half_width_m: float = CROSSING_DECK_HALF_WIDTH_M) -> dict:
    """Each record's worst bank offset from its OWN recorded centreline.

    Takes the raw corpus records rather than a world, so the refusal can be
    exercised on a constructed corpus with no save present.  A record that
    declares no centreline is NOT scored - it is named under `no_centreline`,
    because "this producer never wrote a line for it" and "the line it wrote
    is 6 m away" are different facts and merging them hides both.
    """
    rows, none = [], []
    for c in records or ():
        cid = c.get("crossing_id")
        cl = c.get("centreline_straight_from_to")
        if not cl or len(cl) != 2:
            none.append(cid)
            continue
        (ax, az), (bx, bz) = ((float(v) for v in cl[0]),
                              (float(v) for v in cl[1]))
        L = math.hypot(bx - ax, bz - az)
        if L <= 0.0:
            none.append(cid)
            continue
        ux, uz = (bx - ax) / L, (bz - az) / L
        off = {}
        for end in ("bank_a", "bank_b"):
            px, pz = (float(v) for v in (c.get(end) or {}).get("xz", (0, 0)))
            off[end] = round((px - ax) * uz - (pz - az) * ux, 3)
        worst = max(off, key=lambda k: abs(off[k]))
        rows.append(dict(crossing_id=cid, bank=worst,
                         offset_m=off[worst], offsets=off,
                         over=abs(off[worst]) > half_width_m))
    rows.sort(key=lambda r: -abs(r["offset_m"]))
    over = [r for r in rows if r["over"]]
    return dict(half_width_m=half_width_m, judged=len(rows),
                no_centreline=sorted(x for x in none if x),
                over=over, rows=rows,
                worst_m=(abs(rows[0]["offset_m"]) if rows else 0.0))


def _corridor(a, b, half_w: float = CROSSING_DECK_HALF_WIDTH_M,
              overhang_m: float = CROSSING_CORRIDOR_OVERHANG_M):
    """The rectangle a deck for this span would actually occupy."""
    length = math.hypot(b[0] - a[0], b[1] - a[1])
    if length <= 0.0:
        return None
    ux, uz = (b[0] - a[0]) / length, (b[1] - a[1]) / length
    return dict(ox=a[0] - ux * overhang_m, oz=a[1] - uz * overhang_m,
                ux=ux, uz=uz, length=length + 2 * overhang_m, half_w=half_w)


def corridor_overlap(a1, b1, a2, b2,
                     sample_m: float = CROSSING_CORRIDOR_SAMPLE_M) -> float:
    """Fraction of one corridor's area inside the other, MAX of both
    directions.  The max rather than the mean, because a short span wholly
    inside a long one IS a duplicate of it and the mean would dilute exactly
    that case below any threshold."""
    ca, cb = _corridor(a1, b1), _corridor(a2, b2)
    if ca is None or cb is None:
        return 0.0
    best = 0.0
    for src, dst in ((ca, cb), (cb, ca)):
        n_t = max(1, int(round(src["length"] / sample_m)))
        n_l = max(1, int(round(2 * src["half_w"] / sample_m)))
        inside = 0
        for i in range(n_t):
            t = (i + 0.5) * src["length"] / n_t
            px = src["ox"] + src["ux"] * t
            pz = src["oz"] + src["uz"] * t
            for j in range(n_l):
                lat = -src["half_w"] + (j + 0.5) * 2 * src["half_w"] / n_l
                x, z = px + src["uz"] * lat, pz - src["ux"] * lat
                dx, dz = x - dst["ox"], z - dst["oz"]
                t2 = dx * dst["ux"] + dz * dst["uz"]
                if 0.0 <= t2 <= dst["length"] and abs(
                        dx * dst["uz"] - dz * dst["ux"]) <= dst["half_w"]:
                    inside += 1
        best = max(best, inside / (n_t * n_l))
    return round(best, 3)


def collapse_crossing_declarations(
        rows: list[dict],
        threshold: float = CROSSING_DUPLICATE_FRACTION) -> dict:
    """ONE SPAN PER PHYSICAL CHANNEL, and every collapsed record NAMED.

    Returns a census carrying both numbers this project requires of any
    count - how many declarations went in, how many distinct channels came
    out - plus every pair that met the threshold with its measured fraction,
    so a collapse can be argued with rather than merely trusted.  The
    survivor of a group is the FIRST by sorted id, which is stable across
    runs and independent of which segment happened to route first.
    """
    groups: list[list[int]] = []
    owner: dict[int, int] = {}
    pairs = []
    # A CONSERVATIVE BOUND, NOT A SECOND TEST.  Two corridors can only share
    # area if their bounding circles touch, so a pair whose centres are
    # further apart than the sum of their reaches cannot overlap and is not
    # sampled.  It changes no verdict; it removes 122 of 136 lattice sweeps
    # on this corpus.
    reach = [0.5 * r["span_m"] + CROSSING_CORRIDOR_OVERHANG_M
             + CROSSING_DECK_HALF_WIDTH_M for r in rows]
    mid = [(0.5 * (r["a"][0] + r["b"][0]), 0.5 * (r["a"][1] + r["b"][1]))
           for r in rows]
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            if math.hypot(mid[j][0] - mid[i][0],
                          mid[j][1] - mid[i][1]) > reach[i] + reach[j]:
                continue
            f = corridor_overlap(rows[i]["a"], rows[i]["b"],
                                 rows[j]["a"], rows[j]["b"])
            if f < threshold:
                continue
            pairs.append(dict(a=rows[i]["id"], b=rows[j]["id"], fraction=f))
            gi, gj = owner.get(i), owner.get(j)
            if gi is None and gj is None:
                owner[i] = owner[j] = len(groups)
                groups.append([i, j])
            elif gi is None:
                owner[i] = gj
                groups[gj].append(i)
            elif gj is None:
                owner[j] = gi
                groups[gi].append(j)
            elif gi != gj:
                for k in groups[gj]:
                    owner[k] = gi
                groups[gi].extend(groups[gj])
                groups[gj] = []
    collapsed: dict[str, str] = {}
    for g in groups:
        if len(g) < 2:
            continue
        keep = min(g)
        for k in sorted(g):
            if k != keep:
                collapsed[rows[k]["id"]] = rows[keep]["id"]
    return dict(
        declarations=len(rows),
        distinct_channels=len(rows) - len(collapsed),
        collapsed=collapsed,
        pairs_over_threshold=sorted(pairs, key=lambda p: -p["fraction"]),
        threshold=threshold,
        declared_m=round(sum(r["span_m"] for r in rows), 2),
        distinct_m=round(sum(r["span_m"] for r in rows
                             if r["id"] not in collapsed), 2),
        half_width_m=CROSSING_DECK_HALF_WIDTH_M,
        instrument="accept/sections/roads.py::collapse_crossing_declarations, "
                   "corridor area at the deck's MEASURED half-width "
                   f"{CROSSING_DECK_HALF_WIDTH_M:.3f} m, lattice sampled at "
                   f"{CROSSING_CORRIDOR_SAMPLE_M} m, max of both directions. "
                   "Endpoint equality misses a 2.83 m divergence and piece "
                   "overlap both misses an unbuilt pair and false-positives "
                   "on two different channels 2.0 m apart.")


# A deck tile whose walking surface is buried is a hole in the road, and
# 0.30 m is `roads/boardwalk.py::BURIAL_MAX_M` - the same number that module
# refuses a buried travelled-way tile at.  Stated here rather than imported
# because `accept/` must not depend on the builder it judges.
CROSSING_BURIAL_MAX_M = 0.30


def crossing_premise(beds: list[float], deck_y) -> dict:
    """IS THERE WATER UNDER THIS BRIDGE.  The other sign of the same check.

    Every crossing in this world exists because routing wrote
    `why_terrain_failed: water_body` against it - a statement about the
    GENERATED field, made once, at plan time, and never re-measured against
    anything.  Its consequence is total: the ribbon writer skips those
    stations (`is_bridge`), so no terrain is ever written there, and the
    deck builder owns them instead.  A crossing that is WRONG therefore
    deletes a stretch of road from BOTH owners' populations at once, and
    neither one's gate can see it - the road falls between them and every
    check on both sides stays green.

    MEASURED on Ulfsland 2026-09-22, engine bed at 1 m from the save through
    `run_waterproof.sh FROM_SAVE=1`, over the 6 crossings that sit on BUILT
    segments - wet metres, and metres whose BED STANDS ABOVE THE DECK:

        T4-meadhall-stathub-x01    8 of 12 wet     0 of 12 above deck
        T4-meadhall-stathub-x02   41 of 49 wet     0 of 49
        T5-temple-dockshore-x01   17 of 29 wet     0 of 29
        W5-brgs1-wsouth-x01       39 of 90 wet     0 of 90
        W10-brgs1-treenear-x01    39 of 90 wet     0 of 90
        S12-brgs1-boathouse-x01    2 of 82 wet    32 of 82, worst +2.926 m

    S12's span is DRY LAND OVER A RIDGE rising to 36.796 m against its own
    declared deck of 33.870, and its record claims `gap_min_y: 25.24`,
    `max_depth_below_plane_m: 4.76`, `stations_flagged: 45`.  Forty-six of
    that road's 74 planned stations were struck out of the terrain writer's
    population by a water body that is not there, which is why its BUILT
    ribbon is 51.2 m against a planned 144.9 m and why its A terminus reads
    33.870 - the road was benched AT DECK LEVEL and then stopped.

    THE DISCRIMINATOR IS NOT A WET-METRE THRESHOLD.  "How much water is
    enough water" has no principled answer and W5 is only 43 % wet.  The
    threshold-free statement is the physical one: A DECK MUST BE ABOVE THE
    GROUND IT CROSSES.  Where the bed stands above the deck the structure is
    not a bridge over anything, it is a slab buried in a hillside, and the
    stations belong to the RIBBON.  On this world that separates the one
    mis-detected crossing from the other five by 32 metres to 0.

    `beds` are engine readings; `deck_y` is the crossing's own declared
    datum.  A crossing with no declared datum cannot be judged this way and
    says so instead of passing.
    """
    n = len(beds)
    if not n:
        return dict(**{"pass": True}, judged=False, samples=0,
                    why="no bed was read on this span, so its premise is "
                        "UNMEASURED rather than true")
    wet = sum(1 for b in beds if b < I.WATER_LEVEL)
    if deck_y is None:
        return dict(**{"pass": True}, judged=False, samples=n,
                    wet_samples=wet,
                    why="this crossing declares no deck datum, so 'is the "
                        "bed above the deck' has nothing to compare against")
    over = [b - deck_y for b in beds if b - deck_y > CROSSING_BURIAL_MAX_M]
    return dict(**{"pass": not over}, judged=True, samples=n,
                wet_samples=wet,
                bed_above_deck_samples=len(over),
                worst_bed_above_deck_m=round(max(over), 3) if over else 0.0,
                limit_m=CROSSING_BURIAL_MAX_M,
                owner=("Ribbon" if over else "Crossings"),
                why=("the bed stands above this crossing's own declared deck "
                     "on these metres: there is no water body here and these "
                     "stations belong to the TERRAIN WRITER, which skipped "
                     "them as is_bridge"
                     if over else
                     "the bed is below the declared deck on every metre "
                     "read, so a deck here does bridge something"))


def carriage_is_judged(row: dict) -> dict:
    """WHICH ARM OWNS THIS ROW - and a row is owned by exactly one.

    THE GATE MAY NOT REFUSE A PREMISE AND THEN JUDGE WHAT STANDS ON IT.
    MEASURED on Ulfsland, gate run C: `T7-stathub-stenvik-x01` carried TWO
    refusals simultaneously -

      "IS NOT A CROSSING: its own bed stands above its own declared deck
       y=32.519 on 1 of 51 metres ... these stations belong to Ribbon, not
       to a deck"
      "does not carry a walker: 44 breaks over a 49.7 m span, 6 of 51 metres
       stand on a piece"

    - the gate saying in one breath that no deck may be built there and in
    the next that the deck does not carry.  The second sentence is not a
    defect of the world, it is a consequence of the first being true.

    AND THE CONSEQUENCE IS GENERAL, WHICH IS WHY THIS IS LOGIC AND NOT
    TIDINESS.  The remedy the premise arm itself prescribes is to hand the
    stations back to the RIBBON, which writes terrain across them - and the
    first piece that lands on such a span flips `carried_by_a_piece` off
    zero, which was the ONLY thing suppressing the carriage refusal.  So
    REPAIRING THE PREMISE-REFUSED SET COULD ONLY EVER INCREASE THE REFUSAL
    COUNT.  That is exactly how this check went 14 -> 15 between runs B and
    C, with no road made worse.  A CHECK WHOSE REPAIR MAKES ITS OWN NUMBER
    WORSE IS NOT A CHECK.

    The row still FAILS either way - `pass` carries the premise verdict - so
    nothing here weakens the gate; what it removes is a SECOND refusal, of a
    different owner, for a deck this gate has already ruled must not exist.

    The same sentence answers the duplicate corpus: where two declarations
    describe ONE channel, the hole in that one deck is ONE defect with ONE
    repair.  The surviving declaration is judged; the redundant one is
    owned by the corpus refusal, which is where its actual defect lives.
    """
    if not row["premise"]["pass"]:
        return dict(judged=False, owner="premise",
                    why="this crossing's PREMISE has been refused - the bed "
                        "stands above its own declared deck - so the gate "
                        "has already ruled that no deck may stand here and "
                        "may not also fail the deck for not carrying")
    if row.get("duplicate_of"):
        return dict(judged=False, owner="corpus",
                    why=f"this declaration describes the same physical "
                        f"channel as {row['duplicate_of']}, which is judged "
                        f"in its place; the duplication itself is refused "
                        f"against roads/crossings.yaml and its detector")
    return dict(judged=True, owner="carriage",
                why="this crossing crosses something and is the only "
                    "declaration of it, so what stands on it is its own")


def crossing_termination(row: dict, owners, writes=None,
                         step_limit: float = STEP_LIMIT_PER_M) -> dict:
    """CAN THIS REFUSAL EVER BE CLOSED, AND IF NOT, WHAT OWNS THE GROUND.

    A REFUSAL THAT PRESCRIBES A WRITE ITS OWN WRITER FORBIDS CANNOT
    TERMINATE, AND A GATE THAT COUNTS IT BESIDE AN ACTIONABLE ONE IS
    REPORTING A WORK QUEUE AND AN OPERATOR DECISION LIST ADDED TOGETHER.

    The premise arm prescribes exactly one repair and names it in its own
    message: these stations belong to the TERRAIN WRITER, which skipped
    them as `is_bridge`.  So the repair is a `terrain_write` across the
    metres whose bed stands above the deck - and `ledger/writer.py` refuses
    a terrain_write that moves the ground at a `flatten=FORBIDDEN` record's
    own points by more than FLATTEN_TOLERANCE_M.

    AND A DECK'S OWN GROUND IS FORBIDDEN BY CONSTRUCTION.  So the moment a
    deck stands on a crossing, that crossing's premise refusal is
    unclosable BY CONSTRUCTION, and no amount of work by the road builder
    changes it.  MEASURED on Ulfsland, ledger 0ebe71d95cf2506d / 5,421
    lines, 132,148 owned points over 397 live FORBIDDEN records - stations
    within the zero-depth footprint of an owned point, per span:

        S1 13/13 · S2 34/35 · T4-x02 41/49 · T7-x02 13/13 · T4-x01 10/12
        · T7-x01 13/51 · S12-x01 11/82 · S2-eastlobe-x01 8/53
        · W5-x01 2/90 · W10-x01 2/90 · the other six 0

    `T7-stathub-stenvik-x01`, the row that carried two refusals at once in
    run C, is writer-REFUSED on 13 of its 51 metres, nearest owner
    `bridge_body_place seq 5383` at 0.055 m.

    `owners=None` means the set WAS NOT READ, and the class is then
    1E_OUT_OF_ORDER naming that read - never ACTIONABLE: a guard that
    reports a repair open when it cannot see is the defect this gate keeps
    finding, and it has been committed twice tonight by instruments built
    to find it.  `writes=None` is the same statement about the SECOND
    question - whose recorded terrain_write already holds this ground.  And
    never UNKNOWN either, which is `NO_CLASS` spelled again: the reading
    that was not taken is a tool in this tree, so the row names it
    (`termination.settle`).

    THE CLASS ITSELF IS `settlements.termination_class`, NOT A SECOND
    VOCABULARY.  Three sections found three mechanisms independently
    tonight; a gate whose sections each name them differently cannot add
    its own rows up.  This function's job is to produce the NUMBERS that
    classifier reads, measured on a crossing's own prescribed write.
    """
    from .settlements import (contested_ground,        # noqa: PLC0415
                              repair_ownership)
    at = row.get("bed_above_deck_at") or []
    if not at:
        # NONE MAY NEVER STAND FOR AN ANSWER - AND HERE IT IS NOT STANDING
        # FOR ONE.  `termination_class`'s contract is: the ANSWER field
        # NAMES the record when something holds the ground; the ANSWER is
        # None with the REACH field a NUMBER when the question was asked
        # and nothing does; BOTH are None only when nobody looked.
        #
        # AND THE HARBOURS CASE IS NOT THIS CASE, WHICH IS WHY ITS FIX IS
        # NOT COPIED HERE.  There, a row with no restoration IS A REFUSAL
        # WITH NO REMEDY, so the question was asked and the answer is "a
        # repair would need no ground" - footprint 0.0, class 1C_UNSERVED.
        # Here, `bed_above_deck_at` is empty EXACTLY WHEN THE PREMISE
        # PASSED: it and `crossing_premise`'s own overage list are built
        # from the same beds, the same datum and the same
        # CROSSING_BURIAL_MAX_M, so premise-refused IFF this list is
        # non-empty.  An empty list is therefore NOT a remedyless refusal,
        # it is NOT A REFUSAL AT ALL - nobody looked, because there was
        # nothing to look at.  All-None is the honest reading, and a 0.0
        # borrowed from harbours would be this file asserting a
        # measurement it never took.
        return dict(termination="not_applicable", judged=False,
                    stations=0, stations_refused=0,
                    repairable_by_terrain_write=None, ground_owned_by=None,
                    ground_owner_distance_m=None, repair_footprint_m=None,
                    contested_by=None, contested_within_m=None,
                    ground_owners={}, contested_by_all={},
                    stations_owned_by_nobody=0,
                    why="this row is not a refusal - its premise passed - "
                        "so it prescribes no repair and there is nothing "
                        "to classify")
    # THE WORST METRE DECIDES THE CLASS, AND EVERY METRE IS COUNTED.  A
    # write is one write: if any metre of it is forbidden the whole repair
    # is refused, so the class comes from the worst.  BUT THE POPULATION IS
    # NOT THE WORST - naming one owner for a many-owner refusal is
    # `check_repair_is_named`'s defect (36 counted, 1 named) applied to
    # OWNERSHIP, and it was MEASURED wrong in exactly that direction
    # tonight: a harbour refusal reported "inside road_T9_wttown_dockshore"
    # over 85 T9 vertices AND 27 road_T5_temple_dockshore ones. So every
    # distinct owner is carried with the number of metres it holds, and the
    # metres owned by NOBODY are carried too - those are the ones needing
    # no decision from anybody, and they are invisible inside an
    # owner-named refusal.
    worst = None
    refused = 0
    contested = None
    ground_owners: dict[str, int] = defaultdict(int)
    contesters: dict[str, int] = defaultdict(int)
    unowned = 0
    for x, z, depth in at:
        o = repair_ownership(x, z, depth, owners, step_limit)
        c = contested_ground(x, z, o["repair_footprint_m"], writes)
        if o["ground_owned_by"]:
            ground_owners[o["ground_owned_by"]] += 1
        if c["contested_by"]:
            contesters[c["contested_by"]] += 1
        if not o["ground_owned_by"] and not c["contested_by"] \
                and o["repairable_by_terrain_write"] is True \
                and c["contested_within_m"] is not None:
            unowned += 1
        if o["repairable_by_terrain_write"] is False:
            refused += 1
            if worst is None or worst[0]["repairable_by_terrain_write"] \
                    is not False:
                worst = (o, c)
        if c["contested_by"] and contested is None:
            contested = c
        if worst is None:
            worst = (o, c)
    o, c = worst
    # THE REFUSAL'S OWN IDENTITY TRAVELS WITH ITS NUMBERS.  A crossing's
    # prescribed write lands on its own segment's carriageway BY
    # CONSTRUCTION, so without this the shared classifier cannot tell "this
    # road is repairing itself" from "this write destroys somebody else's
    # work" and every road refusal on every world reads 1B_CONTESTED.
    # MEASURED: T9-wttown-dockshore-sb01 was contested by
    # `terrain_write road_T9_wttown_dockshore (seq 234)` - its own segment.
    nums = dict(repairable_by_terrain_write=o["repairable_by_terrain_write"],
                ground_owned_by=o["ground_owned_by"],
                ground_owner_distance_m=o["ground_owner_distance_m"],
                repair_footprint_m=o["repair_footprint_m"],
                own_actor=row.get("segment_id") or row.get("crossing"),
                **(contested or c))
    klass = T.settle(nums, T.unread(owners, writes))
    return dict(termination=klass, judged=True,
                stations=len(at), stations_refused=refused, **nums,
                ground_owners=dict(sorted(ground_owners.items(),
                                          key=lambda kv: -kv[1])),
                contested_by_all=dict(sorted(contesters.items(),
                                             key=lambda kv: -kv[1])),
                stations_owned_by_nobody=unowned,
                why={
                    "1A_FORBIDDEN":
                        "the terrain write this refusal prescribes is "
                        "REFUSED by ledger/writer.py's own flatten guard, "
                        "so no work by this refusal's owner can close it - "
                        "it is an OPERATOR decision, not a work item",
                    "1B_CONTESTED":
                        "the write is permitted and lands inside another "
                        "actor's recorded terrain_write, so performing it "
                        "would destroy work somebody else did. NO GUARD "
                        "EXISTS FOR THIS",
                    "1E_OUT_OF_ORDER":
                        "an instrument this class depends on was not read, "
                        "and the reading is a tool in this tree: "
                        + str(nums.get("prerequisite")),
                }.get(klass,
                      "the terrain write this refusal prescribes is "
                      "permitted at every metre it would have to touch and "
                      "holds nobody else's ground"))



def owner_instruments(ctx):
    """The two ownership instruments, or `None` for whichever did not read.

    A CTX-WIRING BUG MAY NEVER BE LAUNDERED AS AN UNREADABLE CHAIN, and
    that is the entire reason this is a function rather than four lines
    inside `run`.

    MEASURED, the full gate on save 245daa7f (/tmp/hc_phase3.json).  The
    shipped form of this read asked for `ctx.world`.  `Ctx` has no such
    attribute - `accept/__main__.py` stores the world's name on
    `ctx.args.world` - so `Ctx.__getattr__` raised `AttributeError: no
    instrument named 'world'` INSIDE a `try` whose `except Exception` was
    written to survive an unparseable ledger.  The gate then logged "the
    owner set is unreadable" and carried on, and the consequence is the
    whole gate's headline: every crossings row carries
    `repairable_by_terrain_write: null`, `ground_owners: {}`, and
    1A_FORBIDDEN is 0 OF 106 REFUSALS GATE-WIDE - not because nothing on
    this world is forbidden, but because nothing was ever asked.
    Settlements committed the identical line in the same run.

    So the arguments this function KNOWS the shape of are resolved BEFORE
    the guard and raise where they are wrong; only the reads that depend on
    the world's own files are guarded, because those are the ones a bad
    chain can legitimately break.
    """
    from .settlements import (forbidden_extents,          # noqa: PLC0415
                              live_records)
    from .harbours import WriteIndex, write_footprints    # noqa: PLC0415
    world_name = ctx.args.world
    ledger = ctx.ledger
    ledger_dir = ctx.ledger_dir
    try:
        owners = forbidden_extents(live_records(ledger), ledger_dir,
                                   world_name)
    except Exception as exc:                              # noqa: BLE001
        ctx.log(f"flatten=FORBIDDEN owner set unreadable ({exc}); every "
                f"refusal's termination class is UNKNOWN rather than "
                f"actionable")
        owners = None
    # AND THE SECOND QUESTION, WHICH THE WRITER HAS NO OPINION ON: whose
    # recorded terrain_write already holds this ground.  Off the LEDGER,
    # never off `ctx.generated` - applied-minus-generated measures the
    # DEPTH of written material and not WHO wrote it, and it costs a
    # patchscan launch per unwarmed tile.
    try:
        writes = WriteIndex(write_footprints(ledger), margin_max=8.0)
    except Exception as exc:                              # noqa: BLE001
        ctx.log(f"recorded terrain_write footprints unreadable ({exc}); no "
                f"refusal in this section can say whether the ground it "
                f"proposes to write belongs to another actor")
        writes = None
    return owners, writes


def deck_remedy_index(ctx):
    """THE BUILDER'S OWN VERDICT ON EVERY CROSSING, or None if it did not
    answer.

    THE THIRD INSTRUMENT, ON THE SAME THREE-STATE CONTRACT AS THE OTHER TWO.
    `owner_instruments` answers "may this ground be written" and "whose
    ground is it"; this answers "IS THERE A REPAIR AT ALL", which is the
    FIRST clause of ACTIONABLE and the one the crossings arm never asked.

    NOTHING HERE IS A SECOND IMPLEMENTATION.  The predicate, the corpus and
    the reconciliation are all `roads/boardwalk.py`'s and are imported: the
    builder is what decides whether a deck is ever placed, so a gate that
    restated its preconditions would be the third answer to a question that
    has already cost this tree twice.  What this function owns is the
    SURFACE - `reconcile_declared_lines` needs a bed reader, and the gate
    already has one that has read these zones.

    TWO PASSES, BECAUSE `ctx.ground` IS BATCHED AND THE SAMPLING IS THE
    RECONCILER'S.  Pass one runs the reconciler against a RECORDING surface
    that answers a constant and writes down every point it was asked for;
    pass two reads all of those in one batch and runs it again against the
    readings.  Replicating the reconciler's own sample set here to
    pre-batch it would be exactly the duplicated-population defect this
    file keeps refusing.
    """
    p = str(I.JUMPSTART / "roads")
    if p not in sys.path:
        sys.path.insert(0, p)
    # RESOLVED BEFORE THE GUARD, for the reason `owner_instruments` states
    # at length: a ctx-wiring bug may never be laundered as an unreadable
    # corpus.  `ctx.args.world` is the attribute that exists.
    world_name = ctx.args.world
    want_seed, _want_world = I.run_scope(ctx)
    # THE BUILDER IS HANDED THE CORPUS THIS SECTION JUDGES, AND ONLY AFTER
    # THE ONE GUARD HAS PASSED IT.  `boardwalk.crossing_corpus()` with no
    # argument opens the DEFAULT seed's file and asks it nothing: MEASURED on
    # the Fresh42 drive-2 gate, "the builder judged 17 crossing record(s)
    # from roads/crossings.yaml: 15 it REFUSES to build" - Ulfsland's
    # seventeen channels, abutment-probed against Fresh42's ground, while
    # `crossing_spans` had refused that same file as another world's.  A
    # corpus the guard refused yields no builder verdict at all, which is
    # UNKNOWN, never a foreign one.
    _scoped, refused = I.read_scoped_corpus(
        ctx, "the crossings corpus", resolve=crossings_corpus_path,
        produced_by="roads/route.py::find_crossings + Crossings' own "
                    "declared rows", check="crossings")
    if refused:
        ctx.log(f"the crossings corpus was refused by the scope guard "
                f"({refused[0].message[:160]}); every deck refusal's "
                f"termination class is UNKNOWN rather than actionable")
        return None
    try:
        import boardwalk as bw                            # noqa: PLC0415
        records, corpus = bw.crossing_corpus(crossings_corpus_path(want_seed))
    except Exception as exc:                              # noqa: BLE001
        ctx.log(f"the crossings corpus is unreadable to the builder "
                f"({exc}); every deck refusal's termination class is "
                f"UNKNOWN rather than actionable")
        return None
    try:
        written = bw.segment_terrain_written("accept", world=world_name)
    except Exception as exc:                              # noqa: BLE001
        ctx.log(f"the bed-moving record set is unreadable ({exc}); the "
                f"build-order precondition is UNASKED")
        written = None
    # THE RECONCILER'S OWN VERDICT, never the lateral offset alone.  A
    # declaration 3.652 m off its centreline that PROJECTS CLEANLY is a
    # prerequisite nobody ran; one that loses a wet metre is a producer
    # defect no tool closes, and only `reconcile_declared_lines` separates
    # them.
    reconciled = None
    try:
        asked: list[tuple[float, float]] = []

        def recorder(x, z):
            asked.append((float(x), float(z)))
            return 0.0

        bw.reconcile_declared_lines(records, recorder)
        want = {f"dr{i}": xz for i, xz in enumerate(sorted(set(asked)))}
        read = ctx.ground([(lb, x, z) for lb, (x, z) in want.items()])
        beds = {want[lb]: y for lb, y in read.items() if y is not None}

        def surface(x, z):
            return beds.get((float(x), float(z)), 0.0)

        rec = bw.reconcile_declared_lines(records, surface)
        reconciled = {}
        for key, verdict in (("reconciled", "reconciled"),
                             ("refused", "refused"),
                             ("already_on_the_line", "already_on_the_line"),
                             ("no_centreline", "no_centreline")):
            for r in rec.get(key) or ():
                cid = r["crossing_id"] if isinstance(r, dict) else r
                row = r if isinstance(r, dict) else {}
                reconciled[cid] = dict(verdict=verdict,
                                       why=list(row.get("why") or ()),
                                       declared_line=row.get("declared_line"),
                                       projected_line=row.get(
                                           "projected_line"))
        ctx.log(f"the reconciler answered for {len(reconciled)} of "
                f"{len(records)} crossing record(s): "
                + ", ".join(f"{k} {v}" for k, v in sorted(Counter(
                    r["verdict"] for r in reconciled.values()).items())))
    except Exception as exc:                              # noqa: BLE001
        ctx.log(f"the declared-line reconciliation could not run ({exc}); a "
                f"declaration off its own centreline is UNJUDGED rather "
                f"than unserved")
        reconciled = None
    # THE ABUTMENT STEP, TAKEN WHERE THE DECK CAN ACTUALLY LAND.
    #
    # A BANK-COORDINATE PROBE IS MEASURED WRONG ON A DECK THAT STANDS:
    # W10-brgs1-treenear-x01's bank_a reads +0.987 m at its bank coordinate
    # and its 319-piece deck is in the ground, because `abutment_guard`
    # takes the step at the outermost SURVIVING tile and the bank
    # coordinate is by construction the WET EDGE -- the lowest point of the
    # approach.  So the probe walks OUTBOARD over the crossing's own
    # declared `bank_level_run_m` and keeps the best landing it finds,
    # which is the same question the guard asks without building a piece.
    #
    # MEASURED over all 16 records on 7632b3a5674842bd: W10 +0.260 m at
    # 1.00 m out (deck_remedy's own recorded figure for the deck that
    # stands), W5 the same, S1 -0.028, T9 -0.000 -- and
    # T5-temple-dockshore-x01 bank_b +1.500 m with NOTHING inside its 6 m
    # run, which is the row this probe exists to reach.
    steps = {}
    try:
        want, probes = {}, {}
        for r in records:
            cid = r.get("crossing_id")
            H = r.get("required_deck_y")
            if not cid or H is None:
                continue
            try:
                ax, az = (float(v) for v in r["bank_a"]["xz"])
                bx, bz = (float(v) for v in r["bank_b"]["xz"])
            except (KeyError, TypeError, ValueError):
                continue
            L = math.hypot(bx - ax, bz - az)
            if L <= 0.0:
                continue
            ux, uz = (bx - ax) / L, (bz - az) / L
            run = float(r.get("bank_level_run_m") or 6.0)
            for end, px, pz, sign in (("bank_a", ax, az, -1.0),
                                      ("bank_b", bx, bz, +1.0)):
                pts = []
                for k in range(int(run * 4) + 1):
                    t = sign * k * 0.25
                    lb = f"ab{len(want)}"
                    want[lb] = (px + ux * t, pz + uz * t)
                    pts.append(lb)
                probes[(cid, end)] = (H, pts)
        got = ctx.ground([(lb, x, z) for lb, (x, z) in want.items()])
        for (cid, end), (H, pts) in probes.items():
            best = None
            for lb in pts:
                y = got.get(lb)
                if y is None:
                    continue                       # UNREAD IS NOT DRY
                d = float(H) - float(y)
                if best is None or abs(d) < abs(best):
                    best = d
            steps.setdefault(cid, {})[end] = (None if best is None
                                              else round(best, 3))
        judged = sum(1 for v in steps.values()
                     if any(x is not None for x in v.values()))
        ctx.log(f"the abutment probe read {judged} of {len(steps)} "
                f"crossing record(s) over their own bank-level runs; "
                f"{sum(1 for v in steps.values() for x in v.values() if x is not None and abs(x) > bw.ABUTMENT_STEP_MAX_M)} "
                f"bank(s) step further than {bw.ABUTMENT_STEP_MAX_M} m")
    except Exception as exc:                              # noqa: BLE001
        ctx.log(f"the abutment probe could not run ({exc}); every abutment "
                f"is UNJUDGED rather than clear")
        steps = {}
    out = {}
    for r in records:
        cid = r.get("crossing_id")
        if cid:
            out[cid] = bw.deck_remedy(r, terrain_written=written,
                                      reconciled=reconciled,
                                      abutment_steps=steps.get(cid))
    ctx.log(f"the builder judged {len(out)} crossing record(s) from "
            f"{corpus['source']}: "
            f"{sum(1 for a in out.values() if a['buildable'] is False)} it "
            f"REFUSES to build")
    return out


def shadowed_closures(src: str) -> list[dict]:
    """Names a NESTED function reads from its enclosing scope and then
    REBINDS - which makes them locals of the nested function for its WHOLE
    body, so the earlier read raises `UnboundLocalError` on every call.

    MEASURED IN THIS FILE, TWICE IN ONE FUNCTION.  `render` reads `c` (the
    connectivity report) at its connectivity line and later bound
    `c = x["channels"]` inside a branch; it reads `v` (the coverage report)
    and later ran `for k, v in bt.items()`.  Python decides scope
    STATICALLY, so neither binding had to execute: every render of the
    largest section in this gate raised `UnboundLocalError: cannot access
    local variable 'c'` and the section's whole report was a traceback -
    from a branch that only runs when a channel is declared twice, and a
    loop that only runs when a premise is refused.

    THE FAULT IS INVISIBLE TO EVERY OTHER CHECK IN THIS TREE.  It is not a
    syntax error, the module imports, the section RUNS, the JSON is
    complete and the verdict is correct - only the human-readable report
    dies, and it dies after printing enough to look like it worked.

    Returns one row per (function, name), with the line that READS and the
    line that REBINDS, so the refusal carries its population.  Takes source
    rather than a module so a fixture can CONSTRUCT the state it tests.
    """
    out: list[dict] = []
    # A SCOPE ENDS AT THE NEXT SCOPE.  A comprehension, a lambda and a
    # nested def each have a namespace of their own, so a walk that
    # descends into them attributes their loop variables to the function
    # they sit in - which reports `for k, v in ...` inside a generator
    # expression as a rebinding of the enclosing `v`.  MEASURED while
    # building this: the first form reported 40 rows on this module, of
    # which 2 were the real fault and 38 were comprehension targets.
    _SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda,
               ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)

    def own(node, root=True):
        """Every node of `node`'s own scope.  A nested scope is YIELDED -
        so the enclosing function knows the name it binds - and never
        DESCENDED INTO, so its loop variables stay its own."""
        yield node
        if not root and isinstance(node, _SCOPES):
            return
        for child in ast.iter_child_nodes(node):
            yield from own(child, root=False)

    def nested(fn):
        """The functions defined directly in `fn`'s own scope."""
        return [n for n in own(fn) if n is not fn
                and isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]

    def bound_in(fn) -> set:
        names = {a.arg for a in (fn.args.args + fn.args.posonlyargs
                                 + fn.args.kwonlyargs)}
        for n in own(fn):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                names.add(n.id)
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and n is not fn:
                names.add(n.name)
        return names

    def scan(outer):
        outer_names = bound_in(outer)
        for inner in nested(outer):
            uses: dict = {}
            for sub in own(inner):
                if isinstance(sub, ast.Name):
                    uses.setdefault(sub.id, []).append(
                        (sub.lineno,
                         "store" if isinstance(sub.ctx, ast.Store)
                         else "load"))
            args = {a.arg for a in (inner.args.args + inner.args.posonlyargs
                                    + inner.args.kwonlyargs)}
            # `nonlocal x` / `global x` IS THE DECLARATION THAT MAKES THE
            # REBIND DELIBERATE, and it is the whole difference between a
            # mutable cursor and this defect.  MEASURED: without this,
            # structures.py's byte reader - `nonlocal off; off += 4`, the
            # correct idiom - is reported twice, and a check that refuses
            # the correct form teaches everyone to ignore it.
            declared = {nm for sub in own(inner)
                        if isinstance(sub, (ast.Nonlocal, ast.Global))
                        for nm in sub.names}
            for name, hits in uses.items():
                if (name not in outer_names or name in args
                        or name in declared):
                    continue
                loads = sorted(ln for ln, k in hits if k == "load")
                stores = sorted(ln for ln, k in hits if k == "store")
                # A REBIND IS ONLY A FAULT IF SOMETHING READ IT FIRST.  A
                # nested function that binds a name before using it is
                # using its own variable, which is legal and everywhere.
                if loads and stores and loads[0] < stores[0]:
                    out.append(dict(function=inner.name, name=name,
                                    reads_at=loads[0],
                                    rebinds_at=stores[0]))
            scan(inner)

    # ONCE PER FUNCTION, FROM THE TOP.  `scan` recurses into what it finds,
    # so walking every FunctionDef in the tree would visit an inner
    # function once as a child and again in its own right and double every
    # row.
    def top(body):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scan(node)
            elif isinstance(node, ast.ClassDef):
                top(node.body)
    top(ast.parse(src).body)
    return out


# ---------------------------------------------------------------------------
# EVERY REFUSAL THIS SECTION MAKES, CLASSIFIED - not only the thirteen the
# crossings arm owns.
# ---------------------------------------------------------------------------
# MEASURED, the full gate on save 245daa7f (/tmp/hc_phase3.json): roads
# refused 87 times and 84 of the 87 carried NO TERMINATION CLASS AT ALL.
# Obstruction 53, connectivity 11 and traversability 10 emitted no class key,
# so 74 refusals - the largest section in the tree - were invisible to the
# triage every other section had.  The operator was handed "87" and asked to
# sort it by hand into work, decisions and things no tool here can do.
#
# THE FOUR ARMS PRESCRIBE FOUR DIFFERENT REMEDIES, and that is the whole
# content of the classification:
#
#   traversability  an over-limit step: LOWER THE HIGH SIDE by the overage.
#                   A terrain write, so the writer's flatten guard and the
#                   recorded footprints both have an opinion.
#   obstruction     a BODY standing in the carriageway.  No cut removes a
#                   building: 1D_OBSTRUCTED, and the ground question is
#                   answered honestly (a repair would need no ground) rather
#                   than left unasked.
#   connectivity    the FIRST missing link on the repair chain, and its
#                   remedy is the LINK'S OWN reason - a cut for a step, a
#                   deck for a crossing, a building for a body, and for a
#                   gap of 72-163 m nothing this tree performs at all.
#   crossings       premise refusals are `crossing_termination`'s, already
#                   classified; a carriage refusal on a SOUND premise is
#                   closed by `assemble.bridge`, needs no terrain write and
#                   is the one genuinely ACTIONABLE population in here.
#
# AND THE CLASS IS `settlements.termination_class`, NOT A FIFTH VOCABULARY.
# `1D_OBSTRUCTED` was added to that shared enum by agreement rather than
# invented here, for the same reason: a gate whose sections each name their
# mechanisms differently cannot add its own rows up.
_LINK_CUT_REASONS = ("step", "junction")
# A link whose remedy is a terrain cut.  `near_miss` is NOT one of them: two
# roads that pass 4-12 m apart are not joined by lowering anything, they are
# joined by a segment nobody has planned.
_LINK_UNSERVED_REASONS = ("gap", "near_miss")


# WHAT THE CONNECTIVITY REPAIR KEY IS A FUNCTION OF, DECLARED IN SOURCE.
#
# Five times tonight this tree found ONE defect class: A KEY THAT NAMES A
# SUPERSET OR A SUBSET OF WHAT THE QUANTITY ACTUALLY DEPENDS ON.  None was
# found by a check; three were found because a number refused to reconcile
# with another number.  The remedy that carries forward is not the fix, it
# is that an index DECLARES ITS OWN DEPENDENCY where the next author will
# read it, and that the declaration is NEUTERED IN EACH DIRECTION so a
# degenerate key cannot pass the same proof a correct one does.
CONNECTIVITY_REPAIR_DEPENDS_ON = (
    "the 1 m SUB-EDGE the walk refused - `link_step_at` with the signed "
    "ground at its two ends and its own `link_step_m` - and NEVER the "
    "link's own endpoints or its straight-line separation.  A diagonal "
    "link is TWO 1 m mesh edges through the easier corner and the walk "
    "judges each against the plain slide limit, so the link's endpoints "
    "are a DIFFERENT PAIR from the edge that failed and the run "
    "allowance over a 1.414 m diagonal (1.1047 m) is not the allowance "
    "the walk applied (0.7813 m).  And the depth is "
    "`instruments.disposal_depth`, never `step - step_limit`: the latter "
    "is the depth that leaves the transition EXACTLY ON the limit, which "
    "no disposal in this tree writes.")


def _connectivity_repair_key(n: dict, x, z, step_limit: float):
    """`(hx, hz, depth_m, what_this_was_keyed_on)` for the cut a severed
    link prescribes.  See `CONNECTIVITY_REPAIR_DEPENDS_ON`.

    A SEPARATE FUNCTION SO THE DEPENDENCY CAN BE TAKEN AWAY AND THE
    DIFFERENCE MEASURED.  `_neuter_connectivity_key_run_at_the_endpoint`
    restores the shipped key (the run convention at the link's first
    endpoint, sized onto the limit) and
    `_neuter_connectivity_key_sized_off_the_whole_step` puts in the
    degenerate over-correction; one arm proves the fix is load-bearing,
    the other proves the flip is a LOST DEPENDENCY and not an index that
    condemns everything.
    """
    at = n.get("link_step_at")
    ga, gb = n.get("link_step_ground_a_m"), n.get("link_step_ground_b_m")
    step = n.get("link_step_m")
    if (at and len(at) == 4 and ga is not None and gb is not None
            and step is not None):
        hx, hz = (at[0], at[1]) if ga >= gb else (at[2], at[3])
        return (hx, hz, I.disposal_depth(float(step), step_limit),
                "the 1 m sub-edge the walk refused, at its high end, for "
                "the depth a disposal writes")
    # NO SUB-EDGE, AND IT SAYS SO.  A `junction` link comes off the
    # proximity scan rather than off the walk and has no refused mesh edge
    # to name; the beds of its two ends are all there is.  The depth is
    # still the one a disposal writes - `disposal_depth` scaled to the run,
    # so there is one home for how far inside a limit a repair is solved -
    # and the high end is still the end a cut lowers.
    ba, bb = n.get("link_bed_a"), n.get("link_bed_b")
    sep = max(float(n.get("link_separation_m") or 1.0), 1.0)
    why = ("the link's own two beds over its straight-line run: the walk "
           "published no refused sub-edge for this link")
    if ba is None or bb is None:
        return (*(n.get("link_at") or (x, z))[:2], 0.0, why)
    hx, hz = ((n.get("link_at") or (x, z)) if ba >= bb
              else (n.get("link_to") or (x, z)))[:2]
    return (hx, hz,
            I.disposal_depth(abs(ba - bb) / sep, step_limit) * sep, why)


# EVERY REFUSAL THIS SECTION CAN EMIT, NAMED - AND TWO OF THEM NEVER REACH
# THE CLASSIFIER BELOW.
#
# `termination.coverage` enumerates this section's emittable refusals OUT
# OF THE SOURCE and refuses the gate's own selftest if one is unnamed here.
# Running that audit for the first time found something a world could not:
# `run()` RETURNS EARLY on an empty population and on a corpus about
# another world, BEFORE `road_termination` is ever called, so those two
# rows reach the board with no class at all.  Neither can fire on a world
# that has roads and a matching corpus, which is why Ulfsland never showed
# them - and both are exactly what a FRESH SEED produces.
#
#   `population` / `corpus_is_about_this_world`  live, and filled by
#     `termination.backstop`: an unbuilt world owes its ROAD BUILD, and a
#     corpus about another world owes a re-run of its producer.  1E both,
#     FACTORY WORK, each naming what it waits on.
#   EVERYTHING ELSE  is classified in place by `road_termination` against
#     measurements only this section holds - a disposal depth, a per-link
#     repair key, the deck builder's own answer.  The family named here is
#     the BACKSTOP: a row of these checks that reached the board with no
#     class means THAT PASS DID NOT CLASS IT, which is a defect in this
#     section's source and nothing else - SOURCE_CHANGE, 1C.  It was
#     `NOT_MEASURED` (class UNKNOWN) until drive 2's frozen Fresh42 save,
#     where termination.coverage counted it on all five and passed them.
_IN_SECTION = (
    "SOURCE_CHANGE",
    "road_termination classifies this check in place, against this "
    "section's own ownership, contest and body readings; a row of it that "
    "arrives with no class means that pass did not class it, and closing "
    "that is a change to this section's source")
TERMINATION_FAMILIES = {
    **NT.TERMINATION_FAMILIES,
    "north_transport": (
        "UPSTREAM_STAGE_NOT_RUN",
        "physically accept every north dock, paired boat, sea passage and "
        "the final departure lighthouse/large ship before journey acceptance"),
    "north_artifact": (
        "PRODUCER_NOT_RUN",
        "produce a complete non-provisional north itinerary for this seed "
        "and world; missing, unreadable or foreign north data cannot be "
        "replaced by the population the ledger happens to contain"),
    "north_routes": (
        "UPSTREAM_STAGE_NOT_RUN",
        "build every declared north trunk through the ordinary road network "
        "writer before accepting the applied ground and crossing decks"),
    "population": (
        "UPSTREAM_STAGE_NOT_RUN",
        "no road segment was ever written to this world; the prerequisite "
        "is the ROAD BUILD stage of this tree, and there is nothing to "
        "accept until it has run"),
    "corpus_is_about_this_world": (
        "PRODUCER_NOT_RUN",
        "re-run this corpus's producer for this world and seed; a corpus "
        "declaring another world is a reading of a DIFFERENT WORLD"),
    "traversability": _IN_SECTION,
    "obstruction": _IN_SECTION,
    "crossings": _IN_SECTION,
    "connectivity": _IN_SECTION,
    "coverage": _IN_SECTION,
    "roads/termination": (
        "SOURCE_CHANGE",
        "this row refuses when some refusal above came back UNCLASSIFIED; "
        "closing it is a change to this section's own classifier, which no "
        "tool in this tree performs"),
}


# A MISSING DECK ON A DECLARED CROSSING IS WORK IFF THIS TREE'S BUILD LAYS
# DECKS - AND THAT IS READ OFF THE BUILD, NEVER TYPED HERE.
#
# MEASURED on drive 2's frozen Fresh42 save a521b70356e7: S4-meadhall-
# southeast-x01 and T3-temple-meadhall-x01 carry no deck, the builder's own
# preconditions refuse neither, and `abutment`/`structural` are the
# builder's to judge against the applied surface AT BUILD TIME.  They went
# to the board UNKNOWN, and four S4 steps inside S4-x01's span inherited
# it: 6 of 18 roads rows.  But whether a deck gets laid is not unknown -
# it is a question about `buildstage.BUILD_STEPS`, which is source:
#
#   a declared step runs the deck builder   ACTIONABLE - running the build
#       lays it, and that step takes the abutment and structural verdicts
#       it is the only judge of; if it refuses, the next gate says so.
#   no declared step runs it                1C_UNSERVED - no tool in this
#       tree's build performs the remedy (the 8 steps drive 2 ran had none).
#
# The builder is identified by ITS OWN MODULE PATH, the module whose
# `deck_remedy` this section asks - never by a step name or ordinal.
# `_MISSING_DECK_CLASS_TYPED` is the neuter: a class written down instead of
# derived, which is right for exactly one state of the build table.
_MISSING_DECK_CLASS_TYPED: str | None = None
MISSING_DECK_CLASS_DEPENDS_ON = (
    "buildstage.BUILD_STEPS, read at call time: a non-inherited step with "
    "commands whose `owner` names the deck builder's own module",
    "the deck builder's module path (roads/boardwalk.py), taken from the "
    "module `road_termination` asks `deck_remedy_class` of",
)


def crossing_build_step(steps=None):
    """`(step, why)`: the declared build step that runs the deck builder, or
    `(None, why)` when none does.  `why` says which, and says so when the
    build table itself could not be read - an unreadable table is not an
    empty one."""
    for p in (str(I.JUMPSTART), str(I.JUMPSTART / "roads")):
        if p not in sys.path:
            sys.path.insert(0, p)
    import boardwalk                                   # noqa: PLC0415
    builder = Path(boardwalk.__file__).resolve().relative_to(
        I.JUMPSTART.resolve()).as_posix()
    if steps is None:
        try:
            import buildstage                          # noqa: PLC0415
            steps = buildstage.BUILD_STEPS
        except Exception as exc:                      # noqa: BLE001
            return None, (f"buildstage.BUILD_STEPS could not be read "
                          f"({type(exc).__name__}: {exc}), so no declared "
                          f"step can be shown to run {builder}")
    for st in steps:
        if st.argv is not None and not st.inherited and builder in st.owner:
            return st, (f"build step {st.ordinal} `{st.name}` runs "
                        f"{builder}: running the build lays this deck, and "
                        f"that step judges the abutment and structural "
                        f"verdicts only it can take")
    return None, (f"no step of buildstage.BUILD_STEPS runs {builder} "
                  f"({len(steps)} steps declared), so no tool in this "
                  f"tree's build lays this deck")


def missing_deck_class(steps=None) -> tuple[str, str]:
    """`(class, why)` for a declared crossing with no deck that the builder's
    own preconditions do not refuse."""
    st, why = crossing_build_step(steps)
    if _MISSING_DECK_CLASS_TYPED is not None:
        return _MISSING_DECK_CLASS_TYPED, "typed, not derived"
    return ("ACTIONABLE" if st is not None else "1C_UNSERVED"), why


def road_termination(findings, owners, writes=None, spans=None,
                     step_limit: float = STEP_LIMIT_PER_M,
                     deck_remedy=None, placements=None) -> list:
    """Assign every hard refusal its termination class, in place.

    Returns the census finding - "N refusals, M ACTIONABLE" with the split -
    because a classification nobody can read without opening the JSON is a
    count without its population one level up.

    `owners=None` means the flatten=FORBIDDEN set was not read and every
    class that depends on it is 1E_OUT_OF_ORDER naming that read rather than
    ACTIONABLE; `writes=None` says the same of the recorded terrain_write
    footprints.  UNREAD IS NOT CLEAR, on both instruments - and it is not
    UNKNOWN either, which is `NO_CLASS` spelled again (`termination.settle`).

    `spans` maps a crossing id to its span row, so a connectivity refusal
    whose first missing link IS a declared crossing inherits THAT crossing's
    own class instead of guessing one.  MEASURED on Ulfsland: four of the
    eleven unreachable destinations funnel through one coordinate,
    (-184,-22), and their blocker is S12-brgs1-boathouse-x01, whose premise
    refusal is already classified by the arm that owns it.

    `deck_remedy` maps a crossing id to `roads/boardwalk.py::deck_remedy`'s
    answer for it - THE BUILDER'S OWN VERDICT ON WHETHER IT WOULD LAY A DECK
    THERE.  `None` means the builder was NOT ASKED, and every deck row is
    then 1E naming that question rather than ACTIONABLE.  This is the third
    instrument on the same three-state contract as `owners` and `writes`,
    and it is the one whose absence used to be read as a pass: a crossing
    with a sound premise was published ACTIONABLE because a deck writes no
    terrain, which answers the SECOND and THIRD clauses of ACTIONABLE and
    never the first.  When the builder WAS asked and refuses nothing, the
    class is `missing_deck_class` - read off the build, not typed.

    `placements` is this world's `termination.Placements`: every operator
    row is asked whether its blocker is the factory's own piece or claim
    (`termination.own_blockers`).  None is the fourth instrument NOT READ.
    """
    from .settlements import (contested_ground,        # noqa: PLC0415
                              repair_direction, repair_ownership)
    # THE BUILDER'S OWN ACCESSOR, not a reading of its `buildable` field.
    # See `boardwalk.deck_remedy_class`: it cannot return ACTIONABLE for any
    # answer, including None, so this arm has no way to promote an UNJUDGED
    # crossing to work by reading a tri-state field as a boolean.
    _bwp = str(I.JUMPSTART / "roads")
    if _bwp not in sys.path:
        sys.path.insert(0, _bwp)
    from boardwalk import deck_remedy_class            # noqa: PLC0415
    spans = spans or {}
    hard = [f for f in findings if not f.ok and not f.advisory]
    # THE READINGS THIS PASS DEPENDS ON THAT DID NOT ARRIVE, each mapped to
    # the prerequisite that takes it, so no row it classes is UNKNOWN.
    unread = T.unread(owners, writes)
    # AND WHETHER THIS TREE'S BUILD LAYS A DECK AT ALL, once per pass, off
    # `buildstage.BUILD_STEPS` as it stands when the gate runs.
    deck_class = missing_deck_class()

    def cut(nums, x, z, overage):
        """The refusal prescribes a cut of `overage` at (x, z)."""
        o = repair_ownership(x, z, max(float(overage), 0.0), owners,
                             step_limit)
        nums.update(o)
        nums.update(contested_ground(x, z, o["repair_footprint_m"], writes))

    def no_cut(nums, why):
        """The refusal prescribes NO terrain write, and says so with numbers.

        A REFUSAL WITH NO PROPOSED REPAIR HAS A FOOTPRINT OF 0.0, NOT OF
        NOTHING.  0.0 says the question was asked and a repair would need no
        ground; None says nobody looked, and `termination_class` reads the
        two differently by design - passing None here turned six correct
        1C_UNSERVED rows into UNKNOWN in harbours.
        """
        nums["no_terrain_repair"] = why
        nums["repair_footprint_m"] = 0.0 if owners is not None else None
        nums["repairable_by_terrain_write"] = (False if owners is not None
                                               else None)
        nums["ground_owned_by"] = None
        nums["ground_owner_distance_m"] = None
        nums["contested_by"] = None
        nums["contested_within_m"] = None

    # CROSSINGS BEFORE CONNECTIVITY, so the inheritance below reads a class
    # that has already been decided rather than one that has not.  A
    # connectivity row blocked by a crossing must carry the CROSSING'S
    # class - one authority per fact - and the two arms are in one loop, so
    # the order is what makes the answer available.  Every other check is
    # independent of this ordering.
    crossing_rows: dict = {}

    def inherit_crossing(nums, who, lead):
        """INHERIT THE CLASS OF THE CROSSING WHOSE DECK IS THE REMEDY.

        One implementation for every row whose remedy is a declared
        crossing's deck - a connectivity link and a traversability step
        inside the span alike.  `spans` (the premise arm's reading) wins;
        the crossing's own refusal row is second."""
        t = (spans.get(who) or {}).get("termination") or {}
        own = crossing_rows.get(who)
        if t.get("judged"):
            src, cls = t, t["termination"]
        elif own and own.get("termination_class"):
            src, cls = own, own["termination_class"]
            nums["inherited_from_the_crossings_refusal"] = True
        else:
            # NEITHER PLACE HAS AN ANSWER, AND THAT IS NOT "UNKNOWN".  A
            # crossing this run never judged cannot be reported as a deck
            # somebody can lay: that is a class invented to avoid an
            # absence.  But both arms that judge a crossing DID run on it -
            # its premise passed and it refused no carriage - and this row
            # is still blocked AT it.  Two arms of one section disagreeing
            # about one crossing is a defect in this section's source, and
            # no run of any tool changes it: 1C, stating which.
            no_cut(nums, f"{lead} {who}, and NO arm of this run judged it "
                         "- it is in neither the segment corpus nor this "
                         "run's crossings refusals - so the crossings arms "
                         "and this row disagree about one crossing, which is "
                         "closed by a change to this section's source")
            nums["repair_footprint_m"] = 0.0
            nums["repairable_by_terrain_write"] = False
            nums["contested_within_m"] = 0.0
            nums["termination_family"] = "SOURCE_CHANGE"
            nums["termination_class"] = T.settle(nums)
            nums["repair_direction"] = repair_direction(nums)
            return
        for k in ("repair_footprint_m", "repairable_by_terrain_write",
                  "ground_owned_by", "ground_owner_distance_m",
                  "contested_by", "contested_within_m"):
            nums[k] = src.get(k)
        nums["inherited_from"] = who
        nums["termination_class"] = cls
        nums["repair_direction"] = repair_direction(nums)

    for f in sorted(hard, key=lambda g: g.check != "crossings"):
        n = f.numbers = dict(f.numbers or {})
        x, z = (f.where or (None, None))[:2]
        if f.check in ("north_artifact", "north_routes", "north_transport"):
            n["termination_class"] = T.out_of_order(
                n, TERMINATION_FAMILIES[f.check][1])
            continue
        if f.check == "traversability":
            # A STEP INSIDE A DECLARED CROSSING'S SPAN IS THAT CROSSING'S.
            # The ribbon stops at the bank by construction and writes no
            # terrain between the banks, so the ground this step is
            # measured on is the channel the DECK is declared over, and the
            # remedy is the deck - never a cut.  MEASURED on the Fresh42
            # drive-2 save: S4-meadhall-southeast stations 341-346, 1.138 m
            # worst, all four inside S4-meadhall-southeast-x01 (banks
            # (396,-424) and (396,-450)), were published ACTIONABLE with a
            # 1.885 m cut at the bank the deck is to land on, while the
            # crossing's own row said "the remedy is a deck".  Two remedies
            # for one hole; the crossing's is the one that closes it.
            inside = (declared_span_of(n.get("at"), spans)
                      if TRAVERSABILITY_INSIDE_A_SPAN else None)
            if inside is not None:
                n["inside_crossing"] = inside
                inherit_crossing(n, inside, "this step lies inside the "
                                            "declared span of crossing")
                continue
            if n.get("no_cut_proposed"):
                no_cut(n, "this refusal is not a step: an unread or "
                          "two-valued vertex has no measured rise to lower, "
                          "and no tool in this tree reconciles two "
                          "instruments that disagree about one vertex")
            else:
                # AT THE HIGH END, AND FOR THE DEPTH A DISPOSAL WILL USE.
                # Two corrections to one call, and they are the same
                # sentence twice:
                #
                #  * `f.where` is the leg's FIRST vertex, which is the high
                #    side only by luck.  A cut-only repair LOWERS THE HIGH
                #    SIDE, so the ground whose ownership decides the class
                #    is the high end's, and now that the refusal publishes
                #    `at` and a signed `ground_rise_m` it can be named
                #    instead of guessed.  Asking at the wrong end of a
                #    transition is the defect `terminus_berm` fixed by
                #    asking at the berm's own station rather than at the
                #    terminus.
                #  * `overage_m` is `step - STEP_LIMIT_PER_M`: the depth
                #    that leaves the step EXACTLY ON the limit, which no
                #    disposal in this tree writes.  See
                #    `instruments.disposal_depth`.
                #
                # MEASURED, T13-temple-harboursouth station 423: the pair
                # of them is the difference between ACTIONABLE (footprint
                # 1.430 m, owner None) and 1A_FORBIDDEN (footprint
                # 1.445 m, harbour_place seq 24 at 1.431 m).
                at, rise = n.get("at"), n.get("ground_rise_m")
                if at and len(at) == 4 and rise is not None:
                    hx, hz = ((at[0], at[1]) if rise <= 0
                              else (at[2], at[3]))
                else:
                    hx, hz = x, z
                cut(n, hx, hz,
                    I.disposal_depth(f.measured, step_limit)
                    if f.measured is not None
                    else (n.get("overage_m") or 0.0))
        elif f.check == "obstruction":
            # THE GROUND QUESTION IS ASKED AND ANSWERED, not skipped.  A
            # body in the carriageway prescribes demolition or a re-route;
            # a terrain write closes none of it, so the footprint such a
            # write would need is 0.0 - measured, not absent.  The class
            # comes off the BODY census, which is a different instrument
            # and a positive answer.
            no_cut(n, "a body of standing pieces is in the carriageway and "
                      "no cut removes a building")
        elif f.check == "crossings":
            if n.get("termination"):
                # The premise arm already classified this row against its
                # own prescribed write.  One authority per row.
                n["termination_class"] = n["termination"]
            elif n.get("remedy_is_a_deck"):
                # A SOUND PREMISE AND NO DECK ON IT - AND THE BUILDER IS
                # ASKED WHETHER IT WOULD LAY ONE.  For the whole life of
                # this arm the answer was assumed: "`assemble.bridge`
                # performs this remedy, it writes no terrain, and it
                # destroys nothing", which is every clause of ACTIONABLE
                # except the FIRST - that a repair EXISTS.  THE CLASSIFIER
                # ASKED ONLY THE TERRAIN-OWNERSHIP QUESTION AND NEVER ASKED
                # THE BUILDER.
                #
                # MEASURED on Ulfsland 7632b3a5674842bd: all SEVEN deck
                # rows on the work queue are refused by the builder's own
                # preconditions before a tile is placed - five for a
                # segment no bed-moving record is attributed to, and
                # T4-x02, S2-eastlobe and S13 for a declaration further off
                # its own segment's centreline than the deck is half-wide
                # whose reconciliation the reconciler REFUSES BY NAME.  A
                # ROW THE ONLY BUILDER IN THIS TREE CANNOT BUILD IS NOT A
                # WORK ITEM, and eight of the gate's twelve remaining
                # ACTIONABLE rows were manufactured work.
                #
                # `deck_remedy` is the BUILDER'S function, imported rather
                # than restated: `roads/boardwalk.py::deck_remedy`, which
                # declares what it is a function of and is neuter-proven in
                # both directions there.  `deck_remedy=None` means the
                # builder was NOT ASKED, and that is 1E naming the question
                # - never ACTIONABLE, and never UNKNOWN.  UNREAD IS NOT
                # CLEAR, on this instrument too.
                no_cut(n, "the remedy is a deck, which writes no terrain")
                n["repairable_by_terrain_write"] = True
                n["contested_within_m"] = 0.0
                ans = (deck_remedy or {}).get(n.get("crossing"))
                n["deck_remedy_asked"] = deck_remedy is not None
                if owners is None or deck_remedy is None or ans is None:
                    n["termination_class"] = T.out_of_order(
                        n, unread.get("owners", ""),
                        "" if deck_remedy is not None and ans is not None
                        else f"ask the deck builder (roads/boardwalk.py "
                             f"deck_remedy, through roads.deck_remedy_index "
                             f"over this seed's crossings corpus) whether it "
                             f"would lay {n.get('crossing')}")
                    if deck_remedy is None or ans is None:
                        n["no_terrain_repair"] = (
                            "the remedy is a deck and THE BUILDER WAS NOT "
                            "ASKED whether it would lay one")
                elif deck_remedy_class(ans) != "UNKNOWN":
                    # THE BUILDER'S REFUSAL CARRIES ITS OWN CLASS, because
                    # "can this be built" and "why not" are one answer and
                    # only the builder holds it.  1C is terminal; 1E says
                    # the factory still owes this.
                    #
                    # AND THE CLASS COMES THROUGH `deck_remedy_class`, NOT
                    # OFF `ans["buildable"]`.  BuryFix's audit of this arm:
                    # `buildable` is tri-state, `None` means UNJUDGED, and
                    # `if not ans["buildable"]` reads like the opposite of
                    # what it does - so the tree was right by DISCIPLINE and
                    # one character from two opposite defects.  The accessor
                    # cannot return ACTIONABLE for any answer including
                    # None, so the wrong answer is unspellable here rather
                    # than warned about in a docstring.
                    n["termination_class"] = deck_remedy_class(ans)
                    n["deck_remedy_refused_by"] = [
                        r["precondition"] for r in ans["refused_by"]]
                    n["unserved_because"] = ans["sub_classes"]
                    n["deck_remedy_prerequisite"] = [
                        r["prerequisite"] for r in ans["refused_by"]
                        if r.get("prerequisite")]
                    n["deck_remedy_performed_by"] = sorted(
                        {r["performed_by"] for r in ans["refused_by"]
                         if r.get("performed_by")})
                    n["deck_remedy_why"] = [r["why"]
                                            for r in ans["refused_by"]]
                    n["no_terrain_repair"] = "; ".join(
                        r["why"] for r in ans["refused_by"])
                    n.update({f"deck_remedy_{k}": v
                              for k, v in (ans.get("numbers") or {}).items()})
                else:
                    # Nothing this predicate owns refuses, and the arms it
                    # does not own - the abutment step and the structural
                    # verdict, both taken against the APPLIED surface - are
                    # the BUILDER'S, taken when it builds.  So whether this
                    # closes is whether this tree's build RUNS the builder,
                    # and that is read off `buildstage.BUILD_STEPS`: see
                    # `missing_deck_class`.  It was UNKNOWN, and six rows on
                    # drive 2's frozen board inherited it.
                    cls, why = deck_class
                    n["termination_class"] = cls
                    n["deck_remedy_unasked"] = ans.get("unasked")
                    n["deck_build_step"] = why
                    if cls != "ACTIONABLE":
                        n["repairable_by_terrain_write"] = False
                        n["termination_family"] = "NO_DESTINATION"
                    n["no_terrain_repair"] = (
                        "the remedy is a deck; no precondition this gate can "
                        "read refuses it, the abutment and structural "
                        "verdicts are the builder's to take at build time, "
                        "and " + why)
            else:
                # The corpus refusal: one physical channel declared twice.
                # The remedy is a change to roads/network.py's per-segment
                # emission and to the file it writes, and no tool in this
                # tree collapses a corpus.
                no_cut(n, "the remedy is a correction to the crossings "
                          "corpus and its producer, which no tool in this "
                          "tree performs")
                n.setdefault("own_actor", n.get("segment"))
                n["termination_class"] = T.settle(n, unread)
            # AND THE ANSWER IS FILED UNDER THE CROSSING'S OWN NAME, so a
            # connectivity row blocked by this crossing inherits the class
            # this arm decided instead of manufacturing a second one.
            if n.get("crossing"):
                crossing_rows[n["crossing"]] = dict(n)
            n["repair_direction"] = repair_direction(n)
            continue
        elif f.check == "connectivity":
            reason = n.get("first_link")
            if reason in _LINK_CUT_REASONS:
                # THE SUB-EDGE THE WALKER FAILED, AT ITS HIGH END, FOR
                # THE DEPTH A DISPOSAL WRITES.  Three corrections to one
                # call and they are one sentence: ASK ABOUT THE REPAIR
                # THIS REFUSAL PRESCRIBES, NOT ABOUT THE SHAPE OF THE
                # ROW THAT REPORTS IT.
                #
                #  * THE CONVENTION.  `abs(bed_a - bed_b) - limit * sep`
                #    is the RUN convention over the link's straight-line
                #    separation.  The WALK does not use it: a diagonal
                #    move is TWO 1 m mesh edges through the easier corner
                #    and EACH is judged against the plain limit
                #    (`legs_between`, and `roads/cone.py` judges both
                #    conventions for exactly this reason - neither
                #    dominates).  On a 1.414 m diagonal the run allowance
                #    is 1.1047 m against the corner's 0.7813 m, so a step
                #    the walk refused comes back CLEAR and the overage is
                #    NEGATIVE.
                #  * THE VERTEX.  `link_at` is the link's FIRST endpoint.
                #    On a diagonal the edge that failed is between a
                #    CORNER and one end, so `link_at` is frequently not
                #    even on it, and a cut-only repair lowers THE HIGH END
                #    OF THE FAILED EDGE.  Same correction the
                #    `traversability` arm above makes, for the same
                #    reason.
                #  * THE DEPTH.  `step - step_limit` is the depth that
                #    leaves the transition EXACTLY ON the limit, which no
                #    disposal in this tree writes; see
                #    `instruments.disposal_depth`.
                #
                # MEASURED, Ulfsland 7632b3a5674842bd, artefact
                # /tmp/gate_final2.json, the `harboursouth` row - and the
                # tell is that THE ROW CONTRADICTED ITSELF IN TWO
                # ADJACENT FIELDS:
                #
                #   category          b_operator  <- `_link_category`
                #                     with frozen=True: "the ground is
                #                     protected; no write moves it"
                #   link_frozen_by    harbour-temple-south seq 24, piece
                #                     (13.62,-256.38), clear 1.0
                #   termination_class ACTIONABLE  <- this call's second
                #                     opinion
                #
                #   shipped key   link_at (13,-255), depth
                #                 0.794 - 0.7813*1.414 = -0.311 -> 0.0
                #                 -> footprint 1.414 m (the BARE lattice
                #                 diagonal: an index answering with no
                #                 dependency at all)
                #                 -> owner None at 1.513 m -> ACTIONABLE
                #   this key      sub-edge (14,-255)->(14,-256), high end
                #                 (14,-255), depth
                #                 disposal_depth(0.794) = 0.024
                #                 -> footprint 1.445 m
                #                 -> harbour_place seq 24 at 1.431 m
                #                 -> 1A_FORBIDDEN
                #
                # which is the answer the `traversability` row naming that
                # same sub-edge - T13-temple-harboursouth station 423 -
                # had already published in the same artefact.  Two
                # instruments, one fact, and the disagreement was a key
                # that named the straight-line RUN and the link's own
                # endpoint instead of the population the quantity depends
                # on.
                hx, hz, over, why = _connectivity_repair_key(
                    n, x, z, step_limit)
                n["repair_keyed_on"] = why
                n["repair_at"] = [hx, hz]
                n["repair_depth_m"] = round(float(over), 4)
                cut(n, hx, hz, over)
            elif n.get("obstruction_bodies"):
                no_cut(n, "the first missing link is a body standing across "
                          "the carriageway")
            elif reason == "crossing":
                # INHERIT THE CLASS OF THE THING THAT BLOCKS IT.  The
                # crossing's own arm measured this; re-deriving it here
                # would be a second opinion on one fact.
                #
                # AND THE CROSSING'S OWN REFUSAL ROW IS THE SECOND PLACE
                # THAT ANSWER LIVES.  `spans` is indexed by the SEGMENT
                # corpus, so a crossing that belongs to no segment is
                # missing from it entirely - and this arm then invented
                # ACTIONABLE for a crossing it had not asked about.
                #
                # MEASURED, Ulfsland 245daa7f: the `boathouse` and `brgs1`
                # connectivity rows both name `link_crossing S1`, and S1
                # is a `declared` crossing with `segment_id` null that
                # appears in NO segment.  `spans` has no S1, so both rows
                # were published ACTIONABLE - "a deck closes this" -
                # while the crossings arm one branch over had ALREADY
                # classified S1's own refusal in the same pass.  Two rows
                # on the work queue whose authority was sitting in the
                # same loop.  See `inherit_crossing`.
                inherit_crossing(n, n.get("link_crossing"),
                                 "the first missing link is crossing")
                continue
            elif reason in _LINK_UNSERVED_REASONS:
                no_cut(n, f"the nearest other component is "
                          f"{n.get('link_separation_m')} m away; a {reason} "
                          f"between two pieces of the network is the "
                          f"planner's to close (roads/network.py "
                          f"ORPHAN_JOIN_DEPENDS_ON joins a piece cut off "
                          f"from the root at plan time, and the plan's "
                          f"`orphan_join` record says why this one was not)")
            elif reason == "unresolved":
                # A DIAGONAL WHOSE TWO SHARED CORNERS WERE NEVER READ.  The
                # reading is the ground instrument's, a tool in this tree,
                # and it simply was not taken at those corners: 1E, naming
                # them - the same answer settlements gives
                # `terminus_step_unresolved`.
                no_cut(n, "the link's shared lattice corners were never "
                          "read, so no step is measured to lower")
                n["termination_class"] = T.out_of_order(
                    n, "the ground instrument (accept ground() -> terraform/"
                       "run_waterproof.sh FROM_SAVE=1) must read the shared "
                       "lattice corners of the link {} -> {}".format(
                           n.get("link_at"), n.get("link_to")))
                n.setdefault("own_actor", n.get("segment"))
                n["repair_direction"] = repair_direction(n)
                continue
            else:
                # `no_link_found`, or a reason this function has never
                # seen: THE SEVERANCE GRAPH AND THE WALK DISAGREE about
                # which vertices exist, or the walk published a link this
                # classifier cannot read.  Both are this section's own
                # instruments disagreeing with each other, and no run of any
                # tool changes that - a change to this section's source is
                # the remedy.  1C, stating which.  It was UNKNOWN.
                no_cut(n, f"the connectivity walk published first link "
                          f"{reason!r}, which names no link this section's "
                          f"severance graph holds; closed by a change to "
                          f"this section's source")
                n["repair_footprint_m"] = 0.0
                n["repairable_by_terrain_write"] = False
                n["contested_within_m"] = 0.0
                n["termination_family"] = "SOURCE_CHANGE"
        else:
            no_cut(n, "this refusal names no repair this classifier can "
                      "measure")
        # THE REFUSAL'S OWN IDENTITY, so a road contesting its own
        # carriageway is not reported as destroying somebody else's work.
        # Every road's repair lands inside its own terrain_write BY
        # CONSTRUCTION, so a 1B that does not exclude self-contest
        # reclassifies every road repair on every world as an operator
        # decision - MEASURED, all three of this section's 1B rows.
        n.setdefault("own_actor", n.get("segment"))
        n["termination_class"] = T.settle(n, unread)
        # WHICH WAY, BESIDE WHETHER.  A class without a direction routes a
        # cut-only planner at a row whose remedy is a RAISE.  See
        # `settlements.repair_direction`; `n` is this refusal's own numbers
        # and already carries every signed field the checks publish.
        n["repair_direction"] = repair_direction(n)

    # AND WHOSE BLOCKER, off this world's ledger.  A body in the carriageway
    # our own deck put there, or a contest between two of our own writes, is
    # the factory's work with the placing step as its owner - not an
    # operator's decision.  `placements=None` is UNREAD: a row that names a
    # blocker is then 1E, never foreign.  See `termination.own_blockers`.
    ownership = T.own_blockers(hard, placements, "roads")
    by_class: dict[str, int] = defaultdict(int)
    for f in hard:
        by_class[(f.numbers or {}).get("termination_class")
                 or "UNCLASSIFIED"] += 1
    act = by_class.get("ACTIONABLE", 0)
    return [Finding(
        check="roads/termination", ok=not by_class.get("UNCLASSIFIED"),
        message=(f"{len(hard)} refusal(s), {act} ACTIONABLE"
                 + ("; by class " + ", ".join(f"{k} {v}" for k, v in
                                              sorted(by_class.items())))
                 + (f" - {by_class['UNCLASSIFIED']} refusal(s) carry NO "
                    "CLASS AT ALL, which is the state this census exists to "
                    "refuse" if by_class.get("UNCLASSIFIED") else "")),
        measured=float(act), limit=float(len(hard)),
        numbers=dict(refusals=len(hard), actionable=act,
                     by_class=dict(sorted(by_class.items())),
                     by_check={
                         c: dict(sorted(
                             Counter((f.numbers or {}).get(
                                 "termination_class") or "UNCLASSIFIED"
                                 for f in hard if f.check == c).items()))
                         for c in sorted({f.check for f in hard})},
                     owners_read=owners is not None,
                     writes_read=writes is not None,
                     blocker_ownership=ownership),
        population="every hard refusal this section produced in this run")]

# ---------------------------------------------------------------------------
# WHY a destination is unreachable - the missing links, named and classified
# ---------------------------------------------------------------------------
# "27 of 28 destinations UNREACHABLE" is a count without a cause, which is
# the one thing this gate is not allowed to produce.  MEASURED on this world
# before this was written: of 27 unreachable destinations, 25 carried NO
# diagnosis at all - `walk_ends_at`/`stopped_by` only ever fires when the
# destination's OWN segment is partly reached, and 25 of the 27 sit on
# segments the walk never sets foot on.  The operator was being handed
# "unreachable" and asked to guess between a re-grade, a missing bridge deck
# and a house in the road, which are three different people's work.
#
# So the walkable components are treated as the nodes of a SECOND graph whose
# edges are THE MISSING LINKS, each one carrying the reason it is missing and
# the metre it is missing at.  A destination's diagnosis is the cheapest
# chain of missing links from the origin's component to its own.
#
# The reason is never inferred from a name or a list.  It is one of:
#   step        the walk itself refused this transition - an over-limit rise
#               between two adjacent lattice vertices.  REGRADEABLE.
#   blocked     the walk itself refused it because a built body leaves no
#               gap across the carriageway.  A STRUCTURE IS IN THE WAY.
#   unresolved  the walk could not measure it.  Neither a pass nor a fix.
#   junction    two roads of DIFFERENT segments pass within JUNCTION_M of
#               each other and the STEP between them is over the limit.
#               REGRADEABLE.
#   near_miss   they pass within LINK_SEARCH_M but further than JUNCTION_M:
#               the two roads never actually meet.  Needs road, not grading.
#   gap         nothing of the other component comes within LINK_SEARCH_M at
#               all.  The distance is reported so nobody calls a 480 m hole a
#               grading defect.
#   crossing    a DECLARED crossing joins these two segments and its own span
#               check refuses it.  THE DECK IS THE FIX.
# and the category is derived from the reason AND the water under it, because
# an over-limit step whose own bed is below sea level cannot be regraded - the
# ribbon is forbidden to move protected over-water ground, so that is a deck
# to build and an OPERATOR DECISION, not a re-emit.
CAT_REGRADE = "a_regradeable"
CAT_OPERATOR = "b_operator"
CAT_STRUCTURE = "c_structure"
CAT_UNMEASURED = "d_unmeasured"
# Preference when one component pair has several missing links: report the
# one whose fix is cheapest and most local, because that is the one that
# reconnects the pair.  A link that IS fixable outranks one that is not.
_CAT_RANK = {CAT_REGRADE: 0, CAT_STRUCTURE: 1, CAT_OPERATOR: 2,
             CAT_UNMEASURED: 3}


# GROUND A ROAD IS FORBIDDEN TO MOVE IS NOT GROUND ANYBODY CAN RE-GRADE, and
# calling it `a_regradeable` sends an author to do a re-emit that writes
# nothing and moves 0.000 m.
#
# MEASURED, T13-temple-harboursouth on save b1966e7b.  `harboursouth` is 3.2 m
# from walkable behind two links, both reported `step` / `a_regradeable`
# because their bed (31.436 m) is above the water plane:
#     (13,-255) 32.230 -> (14,-256) 31.436   0.794 m over 1 m, limit 0.7813
#     (14,-256) 31.436 -> (15,-257) 32.230   0.794 m over 1 m, limit 0.7813
# The plan authors 32.23 at ALL THREE vertices.  The middle one reads 0.794 m
# low because seq 5322's rasteriser REFUSED it: `wood_pole2` + `sign`
# (harbour-temple-south, seqs 27/28, `flatten: FORBIDDEN`) stand at
# (13.62, -256.38), and `ribbon.stamp` skips every carriageway sample within
# `PROTECT_CLEAR_M` = 1.0 m Chebyshev of a protected piece -- (13,-256),
# (14,-256), (13,-257) and (14,-257), which is exactly the notch.  Seq 5322's
# own record says so: `skipped.beside_protected_piece: 48`, gap at
# (13.3, -255.3) `rasteriser_refused {protected: 6}`, worst `full_m` 1.147 at
# (13.0, -256.0).  A re-emit refuses the same four samples for the same
# reason and the step does not move.
#
# AND THE PROTECTION IS CURRENTLY MAKING THE THING IT PROTECTS FLOAT: the
# signpost's own recorded base is y 32.316, i.e. the carriageway datum 32.23
# plus 0.086 m, and the frozen ground under it reads 31.436 -- so it stands
# 0.880 m in the air because the road it was placed against was never written
# beneath it.  That is an OPERATOR decision about a piece, not a grading job,
# and the gate must say which.
def _link_category(reason: str, bed_a: float, bed_b: float,
                   over_water: bool, frozen: bool = False) -> str:
    if reason == "crossing" or over_water:
        return CAT_OPERATOR
    if reason == "blocked":
        return CAT_STRUCTURE
    if reason == "unresolved":
        return CAT_UNMEASURED
    if reason == "gap":
        return CAT_OPERATOR          # a hole with no road in it is not a grade
    if frozen:
        return CAT_OPERATOR          # the ground is protected; no write moves it
    return CAT_REGRADE               # step, junction, near_miss


# ---------------------------------------------------------------------------
# THE REPAIR IS NOT THE LINK.  `_link_category` above asks whether the LINK'S
# OWN TWO VERTICES are frozen and hands out `a_regradeable`.  That is the
# population of the DEFECT, not the population of the REPAIR, and they are
# different sets:
#
#   An over-limit step can only be closed CUT-ONLY by lowering the HIGH side
#   -- raising the low side is a fill and `roads/regrade.py` will not emit one
#   -- and lowering one vertex moves the cliff to THAT VERTEX'S OWN
#   NEIGHBOURS.  The disposal that does not manufacture its own rim is the
#   LIPSCHITZ CONE seeded at the LOW vertex:
#
#       f[v] = min over seeds s of ( h[s] + LIMIT * L1(s, v) ),  taken f < h
#
#   `f` is 1-Lipschitz at LIMIT on the 4-CONNECTED MESH -- the mesh this
#   section already judges, one orthogonal 1 m edge each, a diagonal being
#   two of them through the easier corner -- so every rim pair is inside the
#   limit BY CONSTRUCTION.  It is the smallest cut-only set that closes the
#   step without transferring it, and it is therefore THE population a
#   `a_regradeable` verdict is a claim about.
#
# MEASURED on save 2a6940a934b5, and this is the state the check must refuse:
#   N1-wnorth-shelf (433,3315)->(433,3316), 2.541 m of ground over 1 m.
#     reported `a_regradeable`, `ground_frozen_by: null`,
#     `ground_frozen_measured: true`, gating shelf/mid1/mid2/terminus.
#     ALL SEVEN vertices of its cut-only disposal lie inside the protected
#     extent of `bridge_body_place` seq 2809 at (434,3289) r 33.13 m, so
#     `writer._flatten_forbidden_check` refuses the write and the re-emit the
#     report asks for moves 0.000 m.
#   N4-mid2-terminus (-906,7714)->(-907,7714), 2.538 m: 2 of its 4 disposal
#     vertices are inside the Chebyshev keep-out of `road_lighting` seq
#     2784's piece at (-907.414, 7713.005).
#
# That is verbatim the harboursouth defect in the comment above -- a re-emit
# sent at ground no write may move -- fixed for the LINK and never asked
# about the SET.  A REPAIR IS ONLY EVER AS WIDE AS THE CHECK THAT NAMES ITS
# POPULATION, and a categoriser naming two vertices for a repair of eleven is
# naming the wrong one.
def cut_disposal(lo: tuple[int, int], hi: tuple[int, int],
                 height: dict, limit: float = STEP_LIMIT_PER_M) -> dict:
    """The cut-only lattice set that closes the step `lo`->`hi`.

    `height` maps integer lattice vertices to the applied bed there; it must
    cover a neighbourhood large enough for the cone to decay to zero inside
    it, and `reached_boundary` says when it did not.  Returns the taken set
    as `{vertex: height after}`, never including a sample it would RAISE.

    PURE.  No world, no ledger, no save -- so the selftest can put a known
    cliff in front of it and read the answer.
    """
    if lo not in height or hi not in height:
        return dict(taken={}, reached_boundary=False, measured=False)
    best = {lo: height[lo]}
    pq = [(height[lo], lo)]
    while pq:
        v, p = heapq.heappop(pq)
        if v > best.get(p, float("inf")) + 1e-12:
            continue
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            q = (p[0] + dx, p[1] + dz)
            if q not in height:
                continue
            nv = v + limit
            if nv >= height[q] - 1e-12:      # the cone is above the ground
                continue                     # here: nothing to cut
            if nv < best.get(q, float("inf")) - 1e-12:
                best[q] = nv
                heapq.heappush(pq, (nv, q))
    taken = {p: best[p] for p in best
             if height[p] - best[p] >= DISPOSAL_MIN_CUT_M}
    xs = [p[0] for p in height]
    zs = [p[1] for p in height]
    x0, x1, z0, z1 = min(xs), max(xs), min(zs), max(zs)
    edge = [p for p in taken
            if p[0] in (x0, x1) or p[1] in (z0, z1)]
    return dict(taken=taken, reached_boundary=bool(edge), measured=True)

# The Chebyshev set `ribbon.stamp` refuses around every protected piece.  NOT
# re-derived here: imported from the rasteriser, because a second spelling of
# the keep-out is how the gate ends up disagreeing with the tool about which
# ground is movable.
def frozen_ground(ctx):
    """Which ground a `flatten: FORBIDDEN` piece forbids a road to move.

    Returns `(frozen_at, population)`.  `frozen_at(x, z)` answers the site
    and piece that freezes that vertex, or None; it is None ENTIRELY when
    the ledger could not be read, so a caller can tell "nothing is frozen"
    from "nobody asked" - absence is not zero.

    THE RECOVERY IS THE GUARD'S OWN, `writer._site_evidence`, reached through
    `ribbon.protected_piece_positions`.  `ribbon.py`'s docstring records what
    a second recovery cost: on N0b-wmid-wnorth the two disagreed, one finding
    1,381 pieces the other never saw, and the write was refused at append
    after the zones had been generated.  One implementation, and this is not
    it - this is a caller of it.
    """
    led = getattr(getattr(ctx, "args", None), "world", None)
    try:
        for sub in ("ledger", "roads", "terraform"):
            p = str(I.JUMPSTART / sub)
            if p not in sys.path:
                sys.path.insert(0, p)
        import ribbon as ribbonmod                       # noqa: PLC0415
        import writer as writermod                       # noqa: PLC0415
        lg = writermod.Ledger.open(led, actor="accept-roads")
        recs = lg.records()
        retired = writermod.retired_seqs(recs)
        pieces: list[tuple[float, float, str, int]] = []
        for rec in recs:
            p = rec["params"]
            # BOTH KEEP-OUT KINDS.  This is the FROZEN-GROUND census behind
            # `ground_frozen_measured`, and a RESEAT lamp does freeze ground
            # for anyone who has not declared a re-seat.  Matching the
            # literal string would have silently emptied every lamp out of
            # this census the moment the emitters started writing RESEAT -
            # a guard reporting clear exactly where it stopped looking.
            if writermod.keep_out_kind(p) is None or rec["seq"] in retired:
                continue
            site_pieces, _discs = writermod._site_evidence(rec, recs, lg)
            sid = p.get("site_id") or p.get("role") or f"seq{rec['seq']}"
            for px, pz in site_pieces:
                pieces.append((float(px), float(pz), sid, rec["seq"]))
        clear = ribbonmod.PROTECT_CLEAR_M
    except Exception as exc:                             # pragma: no cover
        ctx.log(f"protected pieces unreadable ({exc}); severance links will "
                "say ground_frozen_measured false, which is NOT a claim that "
                "nothing is frozen")
        return None, {"measured": False, "pieces": 0, "sites": 0}
    # A LATTICE BUCKET, because this is asked once per offered link over a
    # 19,000-vertex graph and the piece list is four figures.
    buckets: dict[tuple[int, int], list] = defaultdict(list)
    for px, pz, sid, seq in pieces:
        buckets[(int(math.floor(px)), int(math.floor(pz)))].append(
            (px, pz, sid, seq))

    def frozen_at(x: float, z: float):
        xi, zi = int(math.floor(x)), int(math.floor(z))
        for dx in (-2, -1, 0, 1, 2):
            for dz in (-2, -1, 0, 1, 2):
                for px, pz, sid, seq in buckets.get((xi + dx, zi + dz), ()):
                    if abs(x - px) <= clear and abs(z - pz) <= clear:
                        return {"site_id": sid, "seq": seq,
                                "piece_xz": [round(px, 2), round(pz, 2)],
                                "clear_m": clear}
        return None

    pop = {"measured": True, "pieces": len(pieces),
           "sites": len({p[2] for p in pieces}),
           "clear_m": clear,
           "recovery": "ledger/writer.py::_site_evidence via "
                       "roads/ribbon.py::protected_piece_positions' own "
                       "population, Chebyshev PROTECT_CLEAR_M - the same set "
                       "ribbon.stamp::refusal skips and the same set "
                       "writer._flatten_forbidden_check refuses movement at"}
    return frozen_at, pop


# ---------------------------------------------------------------------------
# A NEAR MISS IS NOT A FACT ABOUT ROADS.  IT IS AN ABSENT MEASUREMENT.
# ---------------------------------------------------------------------------
# `near_miss` reads "they pass within LINK_SEARCH_M but further than
# JUNCTION_M: the two roads never actually meet".  That sentence is a claim
# about the GROUND made entirely from the spacing of two STATION LISTS, and
# the ground was never read.
#
# MEASURED on Ulfsland, save f544ede6, and it is the second time this exact
# artefact has cost this world a whole region.  `T8-stenvik-wttown` ends at
# `wttown` (820.0, 738.0); `R1-ulfsgard-t8junction` ends at `t8junction`
# (824.0, 738.0).  4.000 m apart, so no edge, so `t8junction` and `ulfsgard`
# were reported UNREACHABLE behind a link the gate called "needs road, not
# grading".  THE ROAD IS THERE.  Decoded out of this ledger's own terrain
# blobs, the applied surface along z = 738 runs
#
#   x   817     818     819     820     821     822     823     824     825
#   y   39.821  39.745  39.672  39.785  39.706  39.653  39.613  39.530  39.499
#
# every vertex MODIFIED, worst adjacent step 0.113 m against a 0.7813 m
# limit, and the same continuity holds across z 736..742.  Both roads are
# 6 m wide with a 1 m shoulder, so each one's terminal stamp alone paves the
# whole 4 m between them.  The two carriageways physically overlap and the
# graph had no edge because neither centreline puts a STATION inside 3.0 m of
# the other's -- an end-to-end merge of two 2 m-station roads can be 4.000 m
# at its closest by construction.
#
# THIS IS THE CROSSING DEFECT AGAIN.  Read the graph build's own words: a
# declared crossing "contributed NO node ... its two banks sat 37.6 m apart
# against a 3.0 m junction threshold and everything beyond was unreachable",
# and the fix was to SAMPLE THE DECK and judge every metre of it with the
# same `edge_ok`.  A near miss is the same shape with a smaller number, and
# the same instrument answers it.
#
# So: no threshold is widened and nothing is assumed walkable.  Every pair
# that would have been dismissed as a near miss gets its straight line read
# from the SAVE at 1 m on the lattice, through `ctx.ground` and
# `I.surfaces_at` -- the same two calls the deck uses -- and becomes an edge
# ONLY if every step along it passes `edge_ok` at the same tan(38 deg).
# IT REFUSES, and the refusal is the other near miss on this same world:
# `S8-wtsouth-treesth` to `T4-meadhall-stathub` at 8.485 m has an UNWRITTEN
# lattice vertex at (477, 437) with a 1.803 m drop onto it, so that pair
# stays severed and now carries the metre it fails at instead of a spacing.
#
# BOUNDED BY THE DAMAGE.  Candidates are only pairs in DIFFERENT walkable
# components -- a pair already joined needs no bridge and is never sampled --
# and only the `BRIDGE_TRIES` shortest per component pair.  One extra
# `ctx.ground` batch, and the reading is cached per POINT, so it is not a
# second read of anything already read.
BRIDGE_SAMPLE_M = 1.0
# HOW MANY OF THE SHORTEST CANDIDATES PER COMPONENT PAIR ARE READ.  More than
# one because the shortest line between two components can be the one that
# crosses the ditch beside the junction rather than the junction itself;
# bounded because this is a live ground read and an unbounded one is how a
# diagnosis turns into a second full survey.
BRIDGE_TRIES = 4
# THE DISCRIMINATOR THIS RULE STANDS ON, named so it can be switched OFF and
# the case re-run the other way.  `Heightmap` renders the 1 m lattice
# TRIANGULATED: a diagonal move is two 1 m mesh edges through a shared
# corner, never one 1.414 m step, and this section already carries the case
# that pins what the hypotenuse reading hides.  A bridge line that is
# axis-aligned - and the one this rule was written for, T8|R1 along z = 738,
# is exactly that - would never exercise it, so it is proved on a fixture
# instead of on the world's luck.
BRIDGE_JUDGES_CORNERS = True


def near_miss_bridges(npos: dict, node_surf: dict, comp: dict, seg_of: dict,
                      read_ground, surfaces_at) -> tuple[list[tuple], dict]:
    """Which near misses are actually joined ground, MEASURED not assumed.

    `read_ground(points)` takes `[(label, x, z), ...]` and returns
    `{label: bed_y}` -- `ctx.ground`, the same reader the deck spans use.
    `surfaces_at(x, z, bed)` returns the walkable surfaces at a sample --
    `I.surfaces_at` bound to this run's `Grid` and walker radius.

    Returns `(edges, report)`.  `edges` are `(a, b)` node-key pairs whose
    whole line was read and passed; `report` carries the population and
    EVERY refusal with the metre and the step it failed at, because a bridge
    that is not there has to be reported as a measured hole and not as a
    silence.
    """
    # 1. CANDIDATES.  Different segments, different components, inside the
    #    same window the severance diagnosis already searches.
    cell = LINK_SEARCH_M
    grid: dict[tuple[int, int], dict[str, list]] = defaultdict(
        lambda: defaultdict(list))
    for k, (x, z) in npos.items():
        grid[(int(x // cell), int(z // cell))][seg_of.get(k)].append((k, x, z))
    reach2 = LINK_SEARCH_M * LINK_SEARCH_M
    jreach2 = JUNCTION_M * JUNCTION_M
    per_pair: dict[tuple[int, int], list] = defaultdict(list)
    for (gx, gz), groups in grid.items():
        near: dict[str, list] = defaultdict(list)
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for sg, rows in grid.get((gx + dx, gz + dz), {}).items():
                    near[sg].extend(rows)
        for sga, here in groups.items():
            for sgb, there in near.items():
                if sgb == sga:
                    continue
                for a, ax, az in here:
                    ca = comp.get(a)
                    if ca is None:
                        continue
                    for b, bx, bz in there:
                        cb = comp.get(b)
                        if cb is None or cb == ca or a >= b:
                            continue
                        d2 = (bx - ax) ** 2 + (bz - az) ** 2
                        if d2 <= jreach2 or d2 > reach2:
                            continue
                        per_pair[(min(ca, cb), max(ca, cb))].append(
                            (math.sqrt(d2), a, b))
    cands = []
    for key, rows in per_pair.items():
        rows.sort()
        cands.extend((d, a, b) for d, a, b in rows[:BRIDGE_TRIES])

    # 2. ONE BATCH, AND IT IS A LATTICE WALK, NOT A STRAIGHT LINE.
    #
    #    `Heightmap` renders the 1 m lattice TRIANGULATED, so a move from a
    #    vertex to its DIAGONAL neighbour does not travel the diagonal: it
    #    crosses two 1 m mesh edges through one of the two shared corners,
    #    and this gate already has a case pinning what happens when that is
    #    forgotten - "a diagonal read as ONE 1.414 m step accepts a wall in
    #    the cell", which is how N1 (433,3318)->(433,3319) stayed invisible
    #    at 6.349 m.  A bridge judged on the raw 1.414 m hypotenuse would
    #    re-introduce exactly that, and the T8|R1 line is axis-aligned so it
    #    would never have shown it.  So the CORNERS ARE IN THE POPULATION:
    #    every diagonal move on every candidate line reads both of them, the
    #    walker takes the EASIER corner, and a move whose corners were both
    #    unreadable is UNRESOLVED and refuses the bridge.
    lines: list[tuple] = []
    want: dict[str, tuple[float, float]] = {}

    def ask(x: float, z: float) -> tuple[str, float, float]:
        lb, sx, sz = I.gpoint(x, z, "nmb")
        want[lb] = (sx, sz)
        return lb, sx, sz

    diagonals = 0
    for d, a, b in cands:
        ax, az = npos[a]
        bx, bz = npos[b]
        n = max(1, int(math.ceil(d / BRIDGE_SAMPLE_M)))
        # THE ENDS ARE THE ROADS' OWN VERTICES and keep the walk's own
        # surfaces; only the ground BETWEEN them has to be read.
        walk: list[tuple] = [(npos[a], a)]
        for i in range(1, n):
            t = i / n
            lb, sx, sz = ask(ax + (bx - ax) * t, az + (bz - az) * t)
            if walk[-1][0] == (sx, sz):
                continue
            walk.append(((sx, sz), lb))
        if walk[-1][0] != (bx, bz):
            walk.append(((bx, bz), b))
        corners: list[tuple] = []
        for i in range(len(walk) - 1):
            (px, pz), _ = walk[i]
            (qx, qz), _ = walk[i + 1]
            if abs(px - qx) < 1e-9 or abs(pz - qz) < 1e-9:
                corners.append(())
                continue
            diagonals += 1
            corners.append((ask(px, qz), ask(qx, pz)))
        lines.append((d, a, b, walk, corners))
    pop = {"measured": bool(lines), "candidate_pairs": len(lines),
           "component_pairs": len(per_pair), "samples": len(want),
           "diagonal_moves": diagonals,
           "sample_m": BRIDGE_SAMPLE_M, "tries_per_component_pair":
           BRIDGE_TRIES, "search_m": LINK_SEARCH_M,
           "instrument": "the line between the two vertices WALKED ON THE "
                         "LATTICE and read from the SAVE through the same "
                         "ctx.ground + surfaces_at the declared crossings "
                         "use. A diagonal move is TWO 1 m mesh edges "
                         "through the EASIER of its two shared corners - "
                         "both corners are in this population - and each "
                         "leg is judged by the same edge_ok at the same "
                         "tan(38 deg) as every other step in this section. "
                         "A move with no readable corner is UNRESOLVED and "
                         "refuses the bridge; so does an unread sample."}
    if not want:
        return [], {**pop, "bridged": 0, "refused": [], "refused_total": 0,
                    "unread": 0}
    beds = read_ground([(lb, x, z) for lb, (x, z) in want.items()])

    # 3. THE VERDICT, MESH EDGE BY MESH EDGE.  An unread sample is NOT a
    #    clean line: it is a question nobody answered, and it refuses.
    surf_cache: dict[tuple[float, float], list] = {}
    unread = 0

    def vsurf(pt, lb):
        """The walkable surfaces at a bridge sample, or None if unread."""
        if lb in npos:                       # a road vertex: the walk's own
            return node_surf.get(lb, [])
        got = surf_cache.get(pt)
        if got is None:
            y = beds.get(lb)
            if y is None or y != y:
                return None
            got = _dry(surfaces_at(pt[0], pt[1], y))
            surf_cache[pt] = got
        return got

    edges: list[tuple] = []
    refusals: list[dict] = []
    for d, a, b, walk, corners in lines:
        broke = None
        for i in range(len(walk) - 1):
            pt, plb = walk[i]
            qt, qlb = walk[i + 1]
            sa, sb = vsurf(pt, plb), vsurf(qt, qlb)
            if not sa or not sb:
                # UNREAD and NOTHING TO STAND ON are both "no surface here",
                # and both refuse: `_dry` returns an empty set over water,
                # which is exactly the reading that stopped a sea floor
                # being mistaken for a bridge deck.
                unread += 1
                broke = dict(at=[pt[0], pt[1]], to=[qt[0], qt[1]],
                             why="no_walkable_surface" if sa is not None
                             and sb is not None else "ground_unread")
                break
            if not corners[i]:                       # orthogonal: one edge
                if edge_ok(sa, sb, 1.0):
                    continue
                broke = dict(at=[pt[0], pt[1]], to=[qt[0], qt[1]],
                             why="step", step_m=round(min_step(sa, sb), 3),
                             limit_m=round(STEP_LIMIT_PER_M, 4), legs=1)
                break
            if not BRIDGE_JUDGES_CORNERS:
                # THE DEFECT, restored only by `_neuter_bridge_corners`: the
                # diagonal read as ONE 1.414 m step, which is how a wall
                # standing on both of a cell's corners becomes invisible.
                if edge_ok(sa, sb, math.hypot(qt[0] - pt[0], qt[1] - pt[1])):
                    continue
                broke = dict(at=[pt[0], pt[1]], to=[qt[0], qt[1]],
                             why="step", step_m=round(min_step(sa, sb), 3),
                             limit_m=round(STEP_LIMIT_PER_M * 1.4142, 4),
                             legs=1)
                break
            # DIAGONAL: two 1 m legs through whichever corner is easier.  A
            # corner nobody could read is not a corner, and NEITHER IS ONE
            # WITH NOTHING TO STAND ON: `_dry` drops every surface under the
            # water plane, so a corner in the sea returns an EMPTY set and
            # is no more a route than an unread one.
            best = None
            for clb, cx, cz in corners[i]:
                sc = vsurf((cx, cz), clb)
                if not sc:
                    continue
                worst = max(min_step(sa, sc), min_step(sc, sb))
                if best is None or worst < best[0]:
                    best = (worst, (cx, cz))
            if best is None:
                unread += 1
                broke = dict(at=[pt[0], pt[1]], to=[qt[0], qt[1]],
                             why="corners_unread", legs=2)
                break
            if best[0] <= STEP_LIMIT_PER_M:
                continue
            broke = dict(at=[pt[0], pt[1]], to=[qt[0], qt[1]], why="step",
                         step_m=round(best[0], 3),
                         limit_m=round(STEP_LIMIT_PER_M, 4), legs=2,
                         through_corner=[best[1][0], best[1][1]])
            break
        if broke is None:
            edges.append((a, b))
        else:
            refusals.append(dict(
                segments=[seg_of.get(a), seg_of.get(b)],
                components=[comp.get(a), comp.get(b)],
                separation_m=round(d, 3),
                a=[round(npos[a][0], 1), round(npos[a][1], 1)],
                b=[round(npos[b][0], 1), round(npos[b][1], 1)], **broke))
    return edges, {**pop, "bridged": len(edges),
                   "refused": sorted(refusals,
                                     key=lambda r: r["separation_m"])[:24],
                   "refused_total": len(refusals), "unread": unread}


def severance(npos: dict, node_surf: dict, comp: dict, src: str,
              refused: list[dict], seg_of: dict,
              failed_crossings: list[dict],
              frozen_at=None, height_field=None) -> tuple[list[dict], dict]:
    """The missing links between walkable components, and the cheapest chain
    of them from the origin's component to every other.

    `refused` is the list of transitions THE WALK ITSELF refused, with its own
    reason - never re-derived here, because a second implementation of the
    step rule is how a gate ends up disagreeing with itself.
    `failed_crossings` are the DECLARED crossings whose own span check
    refused them; a missing link standing at one of their banks is that
    crossing's missing deck and is reported as such rather than as a hole in
    the ground somebody could fill.
    `frozen_at(x, z)` answers which `flatten: FORBIDDEN` piece forbids the
    ground at a vertex to move, or None.  A step on frozen ground is an
    OPERATOR decision about that piece, never a re-grade: see
    `_link_category`.  None means the evidence was not available, and the
    links then say `ground_frozen_by: null`, which is "not asked" and is not
    the same claim as "nothing is frozen here".
    `height_field(pts)` answers the applied bed at a list of integer lattice
    vertices, as `{(x, z): y}`.  It is what lets this function measure THE
    REPAIR rather than only the defect: every link it is about to hand out as
    `a_regradeable` gets its cut-only disposal derived by `cut_disposal` and
    every vertex of THAT SET tested against `frozen_at`.  Absent, the
    disposal is not measured and every link says so in `disposal_measured`,
    because "nobody asked" is not "nothing is frozen" here either.

    Returns (links, reach) where `reach[component]` is the cheapest chain.
    """
    def bed(k):
        s = node_surf.get(k)
        return s[0][0] if s else float("nan")

    def at_a_failed_crossing(p, q):
        for c in failed_crossings:
            for bank in (c["bank_a"], c["bank_b"]):
                for r in (p, q):
                    if math.hypot(r[0] - bank[0],
                                  r[1] - bank[1]) <= LINK_SEARCH_M:
                        return c
        return None

    best: dict[tuple[int, int], dict] = {}
    alts: dict[tuple[int, int], int] = defaultdict(int)
    # THE WALK'S OWN VERDICT ON A PAIR IS FINAL.  Without this, a transition
    # the obstruction check refused because a body leaves a 0.12 m gap is
    # ALSO seen by the proximity scan below, re-offered as a plain junction
    # step, and - because a re-grade outranks a demolition - reported as
    # ground to move.  That is a structure defect wearing a grading label,
    # which is the exact substitution this gate exists to stop.
    judged: set[tuple[str, str]] = set()

    def offer(a: str, b: str, reason: str, d: float, **extra) -> None:
        ca, cb = comp.get(a), comp.get(b)
        if ca is None or cb is None or ca == cb:
            return
        if (min(a, b), max(a, b)) in judged:
            return
        judged.add((min(a, b), max(a, b)))
        ba, bb = bed(a), bed(b)
        xing = at_a_failed_crossing(npos[a], npos[b])
        if xing is not None:
            reason = "crossing"
            extra = dict(crossing=xing["crossing"], breaks=xing["breaks"],
                         carried_by_a_piece=xing["carried_by_a_piece"],
                         span_samples=xing["samples"])
        water = (ba == ba and bb == bb) and min(ba, bb) < WATER_Y
        froze = None
        if frozen_at is not None:
            froze = (frozen_at(*npos[a]) or frozen_at(*npos[b]))
        cat = _link_category(reason, ba, bb, water, frozen=bool(froze))
        key = (min(ca, cb), max(ca, cb))
        alts[key] += 1
        cur = best.get(key)
        rank = (_CAT_RANK[cat], round(d, 3))
        if cur is not None and cur["_rank"] <= rank:
            return
        best[key] = dict(
            _rank=rank, components=[ca, cb], reason=reason, category=cat,
            at=[round(npos[a][0], 1), round(npos[a][1], 1)],
            to=[round(npos[b][0], 1), round(npos[b][1], 1)],
            segments=[seg_of.get(a), seg_of.get(b)],
            separation_m=round(d, 3),
            bed_a=None if ba != ba else round(ba, 3),
            bed_b=None if bb != bb else round(bb, 3),
            over_water=water, ground_frozen_by=froze,
            ground_frozen_measured=frozen_at is not None, **extra)

    # 1. THE TRANSITIONS THE WALK REFUSED.  Authoritative: the reason comes
    #    from the check that refused it, not from a re-measurement.
    for r in refused:
        offer(r["a"], r["b"], r["reason"], r["separation_m"],
              **{k: v for k, v in r.items()
                 if k not in ("a", "b", "reason", "separation_m")})

    # 2. EVERY OTHER PAIR OF VERTICES CLOSE ENOUGH TO MATTER.  A component
    #    pair with no refused transition between them is still severed for a
    #    reason, and the reason is either a junction whose step is too big or
    #    two roads that never meet.
    #
    #    THE SCAN OBEYS THE SAME TWO RULES THE GRAPH DOES, or it invents
    #    links the gate would never make.  (i) Two vertices of the SAME
    #    centreline are joined only where they are CONSECUTIVE, and those
    #    are already in `refused`; offering a k-to-k+2 hop here would route
    #    a walker AROUND the very break the walk refused, which is how a
    #    severed road gets reported as connected.  (ii) A pair inside
    #    JUNCTION_M whose step is legal IS an edge, so it cannot be a
    #    missing link - if one turns up, the graph and this scan disagree
    #    and the pair is left out rather than guessed at.
    #
    #    THE CELLS ARE GROUPED BY SEGMENT because almost every pair inside
    #    one is the same road with itself: a 12 m cell on a 1 m lattice
    #    holds tens of consecutive vertices of ONE centreline, and comparing
    #    them pairwise is the whole cost of this scan for none of its
    #    answers.  Grouping first turns 19,000 x ~900 pair tests into
    #    19,000 x the handful of vertices belonging to a DIFFERENT road.
    cell = LINK_SEARCH_M
    grid: dict[tuple[int, int], dict[str, list]] = defaultdict(
        lambda: defaultdict(list))
    for k, (x, z) in npos.items():
        grid[(int(x // cell), int(z // cell))][seg_of.get(k)].append(
            (k, x, z))
    reach2 = LINK_SEARCH_M * LINK_SEARCH_M
    jreach2 = JUNCTION_M * JUNCTION_M
    for (gx, gz), groups in grid.items():
        near: dict[str, list] = defaultdict(list)
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for sg, rows in grid.get((gx + dx, gz + dz), {}).items():
                    near[sg].extend(rows)
        for sga, here in groups.items():
            for sgb, there in near.items():
                if sgb == sga:
                    continue
                for a, ax, az in here:
                    ca = comp[a]
                    for b, bx, bz in there:
                        if comp[b] == ca or a >= b:
                            continue
                        d2 = (bx - ax) ** 2 + (bz - az) ** 2
                        if d2 > reach2:
                            continue
                        d = math.sqrt(d2)
                        if d2 <= jreach2:
                            if edge_ok(node_surf.get(a, []),
                                       node_surf.get(b, []), d):
                                continue
                            offer(a, b, "junction", d)
                        else:
                            offer(a, b, "near_miss", d)

    # 2b. THE REPAIR'S OWN POPULATION, BEFORE THE CATEGORY IS HANDED OUT.
    #
    #     Every link still standing as `a_regradeable` is about to be handed
    #     to an author as "a re-emit fixes it".  That claim is about the SET
    #     A WRITE WOULD HAVE TO MOVE, and until here nothing has measured it:
    #     `offer` asked `frozen_at` at the link's own two vertices, which is
    #     the population of the DEFECT.  `cut_disposal` derives the smallest
    #     cut-only set that closes the step without transferring it, and the
    #     verdict is taken over THAT.
    #
    #     Run as a post-pass over `best` rather than inside `offer` because
    #     `offer` is called for every close pair in the world and this needs
    #     a dense height read; `best` is the set that is actually reported,
    #     so this measures exactly the population the report hands out and
    #     no more.
    cands = [lk for lk in best.values()
             if lk["category"] == CAT_REGRADE and lk["reason"] == "step"]
    for lk in cands:                      # ABSENT, AND IT SAYS SO
        lk["disposal_measured"] = False
    if height_field is not None and cands:
        want: set[tuple[int, int]] = set()
        boxes = []
        for lk in cands:
            ba, bb = lk.get("bed_a"), lk.get("bed_b")
            if ba is None or bb is None:
                boxes.append(None)
                continue
            a = (int(round(lk["at"][0])), int(round(lk["at"][1])))
            b = (int(round(lk["to"][0])), int(round(lk["to"][1])))
            lo, hi = (a, b) if ba <= bb else (b, a)
            R = int(math.ceil(abs(ba - bb) / STEP_LIMIT_PER_M)) + DISPOSAL_PAD_M
            box = [(x, z)
                   for x in range(min(lo[0], hi[0]) - R,
                                  max(lo[0], hi[0]) + R + 1)
                   for z in range(min(lo[1], hi[1]) - R,
                                  max(lo[1], hi[1]) + R + 1)]
            want.update(box)
            boxes.append((lo, hi, box))
        field = height_field(sorted(want)) if want else {}
        for lk, bx in zip(cands, boxes):
            if bx is None:
                continue
            lo, hi, box = bx
            h = {p: field[p] for p in box if p in field}
            d = cut_disposal(lo, hi, h)
            if not d["measured"] or not d["taken"]:
                continue
            froze = None
            nfroze = 0
            for (px, pz) in d["taken"]:
                f = frozen_at(float(px), float(pz)) if frozen_at else None
                if f is not None:
                    nfroze += 1
                    froze = froze or f
            lk["disposal_measured"] = True
            lk["disposal_samples"] = len(d["taken"])
            lk["disposal_cut_m3"] = round(
                sum(h[p] - d["taken"][p] for p in d["taken"]), 4)
            lk["disposal_reached_read_boundary"] = d["reached_boundary"]
            lk["disposal_frozen_samples"] = nfroze
            lk["disposal_frozen_by"] = froze
            if nfroze:
                # THE REPAIR IS ON GROUND NO WRITE MAY MOVE.  Not a re-emit:
                # an operator decision about the piece that freezes it.
                lk["category"] = CAT_OPERATOR
                lk["_rank"] = (_CAT_RANK[CAT_OPERATOR], lk["_rank"][1])

    links = sorted(best.values(), key=lambda r: r["_rank"])

    # 3. THE CHEAPEST CHAIN from the origin's component to every other,
    #    counting OPERATOR blockers first: a route that needs one re-grade is
    #    better news than one that needs a bridge built, even if it is longer.
    adj_c: dict[int, list[dict]] = defaultdict(list)
    for lk in links:
        adj_c[lk["components"][0]].append(lk)
        adj_c[lk["components"][1]].append(lk)
    c0 = comp[src]
    reach: dict[int, dict] = {c0: dict(cost=(0, 0), chain=[])}
    pq: list = [((0, 0), c0, [])]
    while pq:
        cost, cur, chain = heapq.heappop(pq)
        if reach.get(cur, {}).get("cost", cost) < cost:
            continue
        for lk in adj_c[cur]:
            nxt = (lk["components"][1] if lk["components"][0] == cur
                   else lk["components"][0])
            nc = (cost[0] + (1 if lk["category"] in
                             (CAT_OPERATOR, CAT_UNMEASURED) else 0),
                  cost[1] + 1)
            if nxt in reach and reach[nxt]["cost"] <= nc:
                continue
            reach[nxt] = dict(cost=nc, chain=chain + [lk])
            heapq.heappush(pq, (nc, nxt, chain + [lk]))

    # 4. A COMPONENT NOTHING COMES WITHIN LINK_SEARCH_M OF.  Reported with the
    #    distance to the nearest reached vertex, so "unreachable" never stands
    #    alone.  Only computed for components the chain search could not
    #    reach, so the cost is bounded by the damage.
    reached_nodes = [(k, npos[k]) for k in npos if comp[k] in reach]
    if reached_nodes:
        by_comp: dict[int, list[str]] = defaultdict(list)
        for k in npos:
            by_comp[comp[k]].append(k)
        coarse: dict[tuple[int, int], list[tuple[str, tuple]]] = \
            defaultdict(list)
        for k, p in reached_nodes:
            coarse[(int(p[0] // 64), int(p[1] // 64))].append((k, p))
        for cid, members in by_comp.items():
            if cid in reach:
                continue
            # NEAREST REACHED VERTEX, EXACTLY, WITHOUT WALKING THE WORLD PER
            # MEMBER.  The ring scan stops as soon as no unscanned cell can
            # beat the best pair found so far: a cell at ring r is at least
            # (r-1)*64 m away, so once that exceeds `bestd` the rest of the
            # plane cannot improve it.  Without the bound a 2,500-vertex
            # component 400 m from the network scans a 2,000 m plane 2,500
            # times, which is a diagnosis nobody would wait for.
            bestd = None
            for k in members:
                x, z = npos[k]
                cx, cz = int(x // 64), int(z // 64)
                ring = 0
                found = None
                while ring <= 64:
                    lo = (ring - 1) * 64.0
                    if bestd is not None and lo > bestd[0]:
                        break
                    if found is not None and lo > found[0]:
                        break
                    # THE PERIMETER OF THE RING, not the square inside it.
                    # Walking the filled square makes each ring cost
                    # (2r+1)^2 cells instead of 8r, and the difference is
                    # the whole runtime once a component is a few hundred
                    # metres from the network.
                    if ring == 0:
                        cells = [(0, 0)]
                    else:
                        cells = [(dx, dz)
                                 for dx in (-ring, ring)
                                 for dz in range(-ring, ring + 1)]
                        cells += [(dx, dz)
                                  for dz in (-ring, ring)
                                  for dx in range(-ring + 1, ring)]
                    for dx, dz in cells:
                        for k2, p2 in coarse.get((cx + dx, cz + dz), ()):
                            dd = math.hypot(p2[0] - x, p2[1] - z)
                            if found is None or dd < found[0]:
                                found = (dd, k2)
                    ring += 1
                if found is not None and (bestd is None
                                          or found[0] < bestd[0]):
                    bestd = (found[0], k, found[1])
            if bestd is None:
                continue
            d, a, b = bestd
            ba, bb = bed(a), bed(b)
            water = (ba == ba and bb == bb) and min(ba, bb) < WATER_Y
            xing = at_a_failed_crossing(npos[a], npos[b])
            lk = dict(_rank=(_CAT_RANK[CAT_OPERATOR], d),
                      components=[comp[b], cid],
                      reason="crossing" if xing else "gap",
                      category=CAT_OPERATOR,
                      at=[round(npos[b][0], 1), round(npos[b][1], 1)],
                      to=[round(npos[a][0], 1), round(npos[a][1], 1)],
                      segments=[seg_of.get(b), seg_of.get(a)],
                      separation_m=round(d, 1),
                      bed_a=None if bb != bb else round(bb, 3),
                      bed_b=None if ba != ba else round(ba, 3),
                      over_water=water,
                      **(dict(crossing=xing["crossing"],
                              breaks=xing["breaks"]) if xing else {}))
            links.append(lk)
            reach[cid] = dict(cost=(1, 1), chain=[lk])
    # 5. AND THE BUCKET IS NOT A DRAIN.  A component that reaches the end of
    #    this with no chain gets an EXPLICIT entry saying so and WHY, not
    #    silence: forcing "nothing unaccounted" is worth nothing if the
    #    residual can absorb the problem, because the identity balances just
    #    as neatly at 25 of 27 as at 0 of 27.  So the residual is counted in
    #    the verdict line beside the other three, and each member carries
    #    the instrument that returned nothing - which is what makes it a
    #    work queue instead of a permanent category.  (ParBuried's guard;
    #    his own version read 0 of 385 and was only worth reading because
    #    the bucket size was printed beside the population.)
    for cid in set(comp.values()):
        if cid in reach:
            continue
        reach[cid] = dict(
            cost=(99, 99), chain=[],
            unmeasured="no vertex of any component this walk reached lies "
                       "within 4,096 m of any vertex of this one, so there "
                       "is no missing link for this gate to name: either "
                       "the origin's own component is the whole of what "
                       "was read, or the ground instrument returned "
                       "nothing for the span between them"
            if reached_nodes else
            "the walk reached no vertex at all, so there is nothing to "
            "measure a missing link against")
    for lk in links:
        lk["alternatives"] = alts.get((min(lk["components"]),
                                       max(lk["components"])), 1)
        lk.pop("_rank", None)
    return links, reach


# ---------------------------------------------------------------------------
# the section
# ---------------------------------------------------------------------------
def run(ctx) -> SectionResult:
    args = ctx.args
    scope = ctx.scope
    segs = built_segments(ctx.ledger)
    report: dict = {}
    findings: list[Finding] = I.north_road_population(ctx, segs)
    if not segs:
        return SectionResult(
            findings=findings + [Finding(
                check="population", ok=False,
                message="no road segment was ever written to this world - "
                        "nothing to accept",
                numbers=dict(segments=0), where=None,
                population="terrain_write records with role=road_segment in "
                           f"{ctx.ledger}")],
            report=dict(segments=0))

    # --- stations -----------------------------------------------------------
    stations: dict[str, list[tuple[float, float, float]]] = {}
    pts: list[tuple[str, float, float]] = []
    codes = sorted(segs, key=lambda n: segs[n]["code"])
    for i, name in enumerate(codes):
        st = I.resample(segs[name]["nodes"], SAMPLE_M)
        stations[name] = st
        for j, (_s, x, z) in enumerate(st):
            pts.append((f"{i}|{j}", x, z))

    ctx.log(f"{len(segs)} built segments, {len(pts)} stations at "
            f"{SAMPLE_M:g} m, save {ctx.fp[:12]}")

    cen = ctx.census
    geom, build_pieces = ctx.geom, ctx.build_pieces
    clearable, not_property = ctx.clearable, ctx.not_property
    bed = ctx.ground(pts)
    # READ BEFORE THE MASK, BECAUSE THE MASK NEEDS THEM.  This read used to
    # happen 800 lines below, after `grid` had already been built from the
    # built centrelines alone - which is precisely how the crossings arm
    # came to query an instrument whose population excluded the crossings.
    # It depends on nothing but `ctx` and roads/crossings.yaml, so moving it
    # up costs nothing and closes the ordering that made the fault possible.
    # AND ITS REFUSALS GO STRAIGHT ONTO THE SECTION'S OWN OUTPUT.  A corpus
    # that is absent, corrupt or ABOUT ANOTHER WORLD used to return `[]`
    # here and every crossing check below passed over an empty population.
    crossing_declarations, _corpus_refusals = crossing_spans(ctx)
    findings += _corpus_refusals

    # BUILD THE SOLIDS THAT CAN MATTER, and say which those are.  Rotating
    # and hulling half a million objects is a gigabyte of Python for no
    # reading: an object 300 m from any road cannot obstruct one.  The mask is
    # the corridor dilated by CORRIDOR_MASK_M, which is wide enough that a
    # body reaching into the road is taken whole rather than clipped.
    #
    # AND THE DECLARED CROSSINGS ARE IN IT, because the crossings arm asks
    # `surfaces_at` at coordinates that are BY CONSTRUCTION NOT ON ANY BUILT
    # ROAD.  That is what a crossing IS: water the ribbon refused to write,
    # `why_terrain_failed: water_body`, contributing no station to the very
    # population this mask was derived from.  A mask built from one arm's
    # population and consumed by another's is the same two-populations fault
    # this gate keeps finding, one level down - in the instrument rather
    # than in the corpus.
    #
    # MEASURED on W10-brgs1-treenear-x01 at fp 7632b3a5674842bd, DeckBuild's
    # 319-piece deck: the deck landed COMPLETE - 94 wood_floor at lateral
    # exactly +/-1.000, along 0,2,...,92, every one at y 32.403 - and this
    # section reported 84 of 90 metres standing on a piece with 4 breaks,
    # first break at (-325.6,-68.0).  SEVEN of the ninety samples, k=0..6 at
    # bank_a, lie OUTSIDE the station-only mask, so the tiles above them
    # were never built into `grid` and `surfaces_at` could only return the
    # bed.  Rebuilt from the run's own cached station points: 22,000
    # stations -> 77,227 cells, and samples 0-6 are in none of them.  The
    # deck was there; the instrument was not looking.  At 84/90 the row
    # stays open, crossings ACTIONABLE stays 10, and a correct deck is
    # reported as a holed one - the gate refusing work that was done.
    mask = corridor_mask(
        [(x, z) for st in stations.values() for _s, x, z in st],
        [pt for c in crossing_declarations for pt in c["samples"]])
    keep = [r for r in cen.rows
            if (int(r[1] // 4), int(r[3] // 4)) in mask
            or "roof" in r[0].lower()]
    solids = I.solids_from_census(
        I.Census(keep, cen.walker_radius, cen.walker_height, 0), geom)
    built = [s for s in solids
             if s.name not in clearable and s.name not in not_property]
    grid = I.Grid(built)
    ctx.log(f"{len(keep)} objects within {I.CORRIDOR_MASK_M:g} m of a "
            f"centreline or a declared crossing (or a roof anywhere), "
            f"{len(built)} of them non-vegetation")

    report["population"] = dict(segments=len(segs), stations=len(pts),
                                objects_in_corridor=len(keep),
                                built_objects=len(built))

    # --- CHECK 1: traversability, WALKED ON THE INSTRUMENT'S OWN LATTICE ---
    #
    # WHY THIS IS NOT A 1 m STATION-PAIR CHECK ANY MORE.  `Heightmap.GetHeight`
    # QUANTISES its query to the nearest 1 m vertex, so it is a step function
    # and not a surface: MEASURED at population scale on this world
    # (RoadsRemeasure), 18,609 of 18,610 vertex groups hold IDENTICAL heights,
    # stations up to 1.4 m apart returning the same number to the last
    # decimal.  Comparing 1 m ARCLENGTH stations against it produced two
    # faults, and fixing only the second is the direction this project has
    # been wrong in before:
    #   2,878 of 21,954 adjacent pairs (13.11 %) resolved to THE SAME VERTEX.
    #       Their step was 0.000 BY CONSTRUCTION - a genuine cliff between
    #       those two stations was INVISIBLE.  That is "WALKABLE, 0 holes,
    #       0 gaps" with a new mechanism, and it makes 48 a FLOOR, not a count.
    #   7,556 pairs (34.42 %) resolved to vertices 1.414 m apart while the
    #       allowance was computed from the 1 m station spacing.  14 of the 79
    #       ground pairs were over 0.7813 m and under the 1.1049 m that
    #       actually applies - FALSE REFUSALS.
    # So the walk is stated on the lattice: the ordered, consecutive-deduped
    # VERTICES each centreline passes through, each adjacent pair judged
    # against ITS OWN separation.  A shared-vertex pair stops existing rather
    # than reading 0.000, and a diagonal step is judged at 1.414 m.  Terrain
    # has no detail finer than the lattice - the surface inside a cell is
    # determined by that cell's corners - so the vertex walk is not a coarser
    # check, it is the whole of what the instrument can say.
    # It costs NO extra harness: the vertex heights are the station readings
    # regrouped.
    walk_surface: dict[str, list[tuple[float, I.Solid | None]]] = {}
    seg_rows = []
    over_all: list[dict] = []
    # THE GROUND-ONLY SET, named rather than only tallied: see the comment
    # at the increment.  It is the other half of the pair of populations the
    # docstring above already names - "reporting only the first would call
    # every bridge a defect; reporting only the second would let a deck hide
    # a hole in the road under it" - and only one of the two was ever
    # emitted as a set.
    g_only: list[dict] = []
    # A STATION THE INSTRUMENT COULD NOT READ IS A FAILURE, NOT A SKIP.  The
    # old check excluded boundary steps by construction and then reported
    # "0 holes, 0 gaps"; silently stepping over an unreadable sample would
    # rebuild exactly that.
    unread: list[dict] = []
    # A VERTEX THAT ANSWERED TWICE WITH TWO HEIGHTS IS AMBIGUOUS, NOT AVERAGED.
    # This gate snaps half-way cases UP and Unity's RoundToInt snaps them to
    # EVEN, so a station sitting exactly on the half-lattice line can be
    # assigned by the engine to the vertex this walk did not pick.  Measured
    # here rather than assumed: 1 of 18,610 groups on this world.
    ambiguous: list[dict] = []
    vheight: dict[tuple[float, float], float] = {}
    vsource: dict[tuple[float, float], tuple[str, int]] = {}
    chains: dict[str, list[dict]] = {}
    st_node: dict[str, int] = {}
    collapsed = 0

    def note_vertex(v, h, seg, j):
        if v in vheight and abs(vheight[v] - h) > 1e-3:
            ambiguous.append(dict(
                segment=seg, station=j, x=v[0], z=v[1],
                first=round(vheight[v], 4), second=round(h, 4),
                difference_m=round(abs(vheight[v] - h), 4),
                first_from=vsource[v],
                why="two queries resolve to this vertex and the engine "
                    "returned two heights: the query sits on the "
                    "half-lattice line, where this gate snaps up and "
                    "Unity's RoundToInt snaps to even"))
        else:
            vheight[v] = h
            vsource[v] = (seg, j)

    for i, name in enumerate(codes):
        s = segs[name]
        st = stations[name]
        chain: list[dict] = []
        for j, (_d, x, z) in enumerate(st):
            b = bed.get(f"{i}|{j}")
            if b is None or b != b:
                unread.append(dict(segment=s["seg"], station=j,
                                   x=round(x, 1), z=round(z, 1),
                                   why="no applied bed returned by "
                                       "Heightmap.GetHeight"))
                continue
            v = I.snap(x, z)
            note_vertex(v, b, s["seg"], j)
            if chain and chain[-1]["v"] == v:
                chain[-1]["stations"].append(j)
                collapsed += 1
            else:
                chain.append(dict(v=v, stations=[j]))
            st_node[f"{i}|{j}"] = len(chain) - 1
        chains[name] = chain

    # THE CORNERS A DIAGONAL MOVE ACTUALLY CROSSES.  A walker going from one
    # vertex to its diagonal neighbour does NOT travel the diagonal: the mesh
    # is the lattice triangulated, so he crosses two 1 m MESH EDGES through
    # one of the two shared corners.  Those corners are not on the centreline
    # and were never in the station population - which is where the steps
    # hide.  MEASURED (RoadsRemeasure): on the diagonal transitions whose
    # corners happened to be read, the edge walk finds N1
    # (433,3318)->(433,3319) at 6.349 m, against a station-walk worst of
    # 2.552 m for the whole of N1.  So the corners are READ, not assumed, and
    # a corner nobody could read makes its transition UNRESOLVED rather than
    # clean.
    corner_pts: dict[tuple[float, float], str] = {}
    for name, chain in chains.items():
        for k in range(len(chain) - 1):
            (ax, az), (bx, bz) = chain[k]["v"], chain[k + 1]["v"]
            if abs(ax - bx) > 1e-9 and abs(az - bz) > 1e-9:
                for c in ((ax, bz), (bx, az)):
                    if c not in vheight:
                        corner_pts.setdefault(c, f"c|{c[0]:.0f}|{c[1]:.0f}")
    if corner_pts:
        ctx.log(f"{len(corner_pts)} lattice corners of diagonal moves are "
                "not on any centreline; reading them")
        cbed = ctx.ground([(lb, c[0], c[1])
                           for c, lb in sorted(corner_pts.items())])
        for c, lb in corner_pts.items():
            h = cbed.get(lb)
            if h is not None and h == h:
                note_vertex(c, h, "corner", -1)

    surf_cache: dict[tuple[float, float], list] = {}

    def vsurf(v):
        got = surf_cache.get(v)
        if got is None:
            got = I.surfaces_at(v[0], v[1], vheight[v], grid,
                                cen.walker_radius)
            surf_cache[v] = got
        return got

    def legs_between(a, b):
        """The 1 m MESH EDGES a walker crosses from vertex a to vertex b.

        Orthogonal: one edge.  Diagonal: two, through one of the two shared
        corners - and the walker takes the EASIER route, so the transition is
        judged on whichever corner gives the smaller worst leg.  Returns
        (legs, route) or (None, None) when no corner was readable, which is
        an UNRESOLVED transition and never a clean one.
        """
        (ax, az), (bx, bz) = a, b
        if abs(ax - bx) < 1e-9 or abs(az - bz) < 1e-9:
            return [(a, b)], None
        best = None
        for c in ((ax, bz), (bx, az)):
            if c not in vheight:
                continue
            legs = [(a, c), (c, b)]
            worst = max(min_step(vsurf(p), vsurf(q)) for p, q in legs)
            if best is None or worst < best[0]:
                best = (worst, legs, c)
        if best is None:
            return None, None
        return best[1], best[2]

    unresolved: list[dict] = []
    for i, name in enumerate(codes):
        s = segs[name]
        st = stations[name]
        chain = chains[name]
        # The station-level surface the OBSTRUCTION check needs.  Pieces are
        # not lattice-quantised, so they are still judged at the station's own
        # position - only the BED under it comes from the vertex.
        for j, (_d, x, z) in enumerate(st):
            k = st_node.get(f"{i}|{j}")
            if k is None:
                continue
            walk_surface[f"{i}|{j}"] = I.surfaces_at(
                x, z, vheight[chain[k]["v"]], grid, cen.walker_radius)
        worst = 0.0
        worst_at = None
        over = []
        # TWO READINGS OF THE SAME PAIR, and the difference between them is
        # the bridges.  `ground_pairs` is the road's OWN SURFACE, which is
        # what a re-grade has to fix.  `over` is what a WALKER meets, which
        # may be a deck laid over a gap the ground never closed.  Reporting
        # only the first would call every bridge a defect; reporting only the
        # second would let a deck hide a hole in the road under it.
        g_pairs = 0
        g_locs = 0
        g_run = False
        diagonal = 0
        edges = 0
        for k in range(len(chain) - 1):
            a, b = chain[k]["v"], chain[k + 1]["v"]
            legs, _route = legs_between(a, b)
            if legs is None:
                diagonal += 1
                unresolved.append(dict(
                    segment=s["seg"], station=chain[k]["stations"][0],
                    x=round(a[0], 1), z=round(a[1], 1),
                    x2=round(b[0], 1), z2=round(b[1], 1),
                    why="a diagonal move whose two shared lattice corners "
                        "were never read: the two 1 m mesh edges the walker "
                        "actually crosses cannot be measured, and a "
                        "transition this gate could not measure is not a "
                        "transition it may call clean"))
                chain[k]["unresolved"] = True
                continue
            if len(legs) > 1:
                diagonal += 1
            chain[k]["ok_to_next"] = True
            for p, q in legs:
                edges += 1
                d = math.hypot(q[0] - p[0], q[1] - p[1])
                lim = STEP_LIMIT_PER_M * d
                sp, sq = vsurf(p), vsurf(q)
                gonly = abs(sp[0][0] - sq[0][0])
                if gonly > lim:
                    g_pairs += 1
                    if not g_run:
                        g_locs += 1
                    # NAMED, NOT ONLY COUNTED.  `g_pairs` and `g_locs` are
                    # TWO DIFFERENT SETS - pairs collapse into runs, and
                    # which row OPENED a run lives only in `g_run` and was
                    # destroyed before the report was written.  The
                    # difference between this tally and `over` is, in this
                    # check's own words, "ground a deck carries the walker
                    # over": exactly the set somebody needs the day they ask
                    # whether a deck may be removed, whether the ground
                    # under a crossing is still bad, or whether a cut under
                    # a deck did anything.  A count answers none of those
                    # three and a set answers all three, and the walk is
                    # already standing on the answer when it increments.
                    g_only.append(dict(
                        segment=s["seg"],
                        station=chain[k]["stations"][0],
                        x=round(p[0], 1), z=round(p[1], 1),
                        x2=round(q[0], 1), z2=round(q[1], 1),
                        ground_step_m=round(gonly, 3),
                        limit_m=round(lim, 4), lattice_m=round(d, 3),
                        new_location=not g_run,
                        carried_by_a_surface=min_step(sp, sq) <= lim))
                    g_run = True
                else:
                    g_run = False
                best = min_step(sp, sq)
                if best > worst:
                    worst, worst_at = best, p
                if best > lim:
                    chain[k]["ok_to_next"] = False
                    rec = dict(segment=s["seg"],
                               station=chain[k]["stations"][0],
                               x=round(p[0], 1), z=round(p[1], 1),
                               x2=round(q[0], 1), z2=round(q[1], 1),
                               step_m=round(best, 3), limit_m=round(lim, 4),
                               lattice_m=round(d, 3),
                               ground_only=round(gonly, 3),
                               # THE GROUND AT BOTH ENDS, SIGNED.  A refusal
                               # that publishes only `step_m` cannot say
                               # WHICH END IS HIGH, and a cut-only repair is
                               # a lowering of the high side: without the
                               # sign every consumer has to go back to the
                               # world to find out which half of the
                               # disposal applies, which is the same defect
                               # `terminus_step`'s riser already fixed one
                               # check over.
                               ground_a_m=round(sp[0][0], 3),
                               ground_b_m=round(sq[0][0], 3),
                               surfaces=f"{len(sp)}->{len(sq)}")
                    over.append(rec)
                    over_all.append(rec)
                    # KEEP THE REFUSAL ON THE TRANSITION ITSELF.  The
                    # severance diagnosis has to be able to say WHICH step
                    # severed the network without re-deriving it from a
                    # station index that a diagonal's corner does not share.
                    prev = chain[k].get("worst_over")
                    if prev is None or rec["step_m"] > prev["step_m"]:
                        chain[k]["worst_over"] = rec
        seg_unres = [u for u in unresolved if u["segment"] == s["seg"]]
        seg_rows.append(dict(
            segment=s["seg"], code=s["code"], stations=len(st),
            vertices=len(chain), diagonal_moves=diagonal,
            mesh_edges_judged=edges,
            unresolved_transitions=len(seg_unres),
            writes=s["writes"],
            worst_step_m=round(worst, 3),
            worst_at=None if worst_at is None else
            dict(x=round(worst_at[0], 1), z=round(worst_at[1], 1)),
            over_limit=len(over),
            ground_over_limit_pairs=g_pairs,
            ground_over_limit_locations=g_locs,
            verdict="PASS" if not over and not seg_unres else "FAIL"))
    scoped_rows = [r for r in seg_rows if not scope or r["code"] in scope]
    report["traversability"] = dict(
        instrument="engine applied bed from the save (Heightmap.GetHeight "
                   "with the save's own _TerrainCompiler ZDOs instantiated), "
                   "compared along the 1 m MESH EDGES a walker actually "
                   "crosses.  GetHeight is NEAREST-VERTEX, not interpolated "
                   "- 243 of 243 same-cell groups returned identical "
                   "heights where bilinear would have had to differ, median "
                   "spread 0.0744 m and max 1.5830 m - so the instrument is "
                   "quantised to the lattice while the surface is the "
                   "lattice TRIANGULATED.  A diagonal move is therefore two "
                   "1 m edges through a shared corner, judged at the 1 m "
                   "allowance each by the EASIER of the two corners, never "
                   "one 1.414 m step; walking surface = the vertex bed or "
                   "any level piece top the walker can reach, so a bridge "
                   "deck is detected FROM THE BODIES and never excluded by "
                   "hand",
        population=f"{sum(r['mesh_edges_judged'] for r in seg_rows)} mesh "
                   f"edges over {sum(len(c) for c in chains.values())} "
                   f"lattice vertices, from {len(pts)} stations on "
                   f"{len(segs)} built centrelines ({collapsed} stations "
                   "shared a vertex with their neighbour and are one place, "
                   f"not a 0.000 m step; {len(corner_pts)} diagonal corners "
                   "were read because they are crossed but lie off every "
                   "centreline)",
        limit_m_per_m=round(STEP_LIMIT_PER_M, 4),
        vertices_walked=sum(len(c) for c in chains.values()),
        mesh_edges_judged=sum(r["mesh_edges_judged"] for r in seg_rows),
        stations_collapsed_into_a_shared_vertex=collapsed,
        diagonal_moves=sum(r["diagonal_moves"] for r in seg_rows),
        corners_read=len(corner_pts),
        unresolved_transitions=len(unresolved),
        unresolved=unresolved[:10],
        ambiguous_vertices=len(ambiguous),
        ambiguous=ambiguous[:10],
        segments=seg_rows,
        over_limit_locations=len(over_all),
        over_limit_in_scope=sum(r["over_limit"] for r in scoped_rows),
        ground_over_limit_pairs=sum(r["ground_over_limit_pairs"]
                                    for r in seg_rows),
        ground_over_limit_locations=sum(r["ground_over_limit_locations"]
                                        for r in seg_rows),
        # AND THE SPLIT INSIDE IT, AS A NUMBER.  The list below carries
        # `carried_by_a_surface` per row and nothing counted it, so the one
        # figure that decides what the operator is looking at - how much of
        # this ground a piece top is standing over TODAY - was readable only
        # by summing a list by hand.  MEASURED on Ulfsland 0571968d: 44
        # pairs, 35 carried, 9 not, and the 9 are the ones the walk stops
        # at.
        ground_over_limit_carried=sum(1 for r in g_only
                                      if r["carried_by_a_surface"]),
        ground_over_limit_not_carried=sum(1 for r in g_only
                                          if not r["carried_by_a_surface"]),
        # THE SET, BESIDE THE COUNT.  `ground_over_limit_locations` counts
        # RUNS and `ground_over_limit_pairs` counts EDGES, so they are two
        # views of one set and neither is that set: `new_location` is the
        # flag that opened a run and is the only way a consumer can get from
        # the pairs to the locations.  `carried_by_a_surface` is why this
        # list is ADVISORY rather than a refusal - a piece top carries the
        # walker over this metre today - and it is exactly the row somebody
        # reads the day they ask whether that piece may be removed.
        ground_over_limit=g_only,
        unreadable_stations=len(unread),
        unreadable=unread[:10],
        worst=sorted(over_all, key=lambda r: -r["step_m"])[:25],
        **{"pass": all(r["verdict"] == "PASS" for r in scoped_rows)
           and not [u for u in unread
                    if not scope or u["segment"].split("-")[0] in scope]
           and not ambiguous and not unresolved})
    # THE GROUND-ONLY SET, INDEXED BY THE TRANSITION IT WAS MEASURED ON, so
    # a refusal can publish `carried_by_a_surface` off the walk that took
    # the reading instead of a flag re-derived beside it.  MEASURED over
    # this world's 44 ground-only over-limit pairs: 35 are carried by a
    # piece top TODAY and 9 are not, and the two populations are different
    # decisions - "may this deck be removed" against "the walk stops here".
    carried_by = {(r["x"], r["z"], r["x2"], r["z2"]): r["carried_by_a_surface"]
                  for r in g_only}
    for u in unresolved:
        if scope and u["segment"].split("-")[0] not in scope:
            continue
        findings.append(Finding(
            check="traversability", ok=False,
            message=f"{u['segment']} station {u['station']}: "
                    f"({u['x']},{u['z']})->({u['x2']},{u['z2']}) {u['why']}",
            where=(u["x"], u["z"]),
            numbers=dict(station=u["station"], segment=u["segment"],
                         # NOT A STEP, SO NOT A CUT.  A transition whose two
                         # shared corners were never read has no measured
                         # rise to lower, so there is no repair whose
                         # ownership could be asked about - and the remedy,
                         # reading ground this run could not read, is
                         # performed by no tool in this tree.
                         no_cut_proposed=True,
                         carried_by_a_surface=carried_by.get(
                             (u["x"], u["z"], u["x2"], u["z2"])))))
    for r in sorted(over_all, key=lambda r: -r["step_m"]):
        if scope and r["segment"].split("-")[0] not in scope:
            continue
        findings.append(Finding(
            check="traversability", ok=False,
            message=f"{r['segment']} station {r['station']}: "
                    f"{r['step_m']} m rise across {r['lattice_m']} m of "
                    f"lattice, limit {r['limit_m']} m",
            where=(r["x"], r["z"]), measured=r["step_m"],
            limit=r["limit_m"],
            numbers=dict(ground_only_m=r["ground_only"],
                         lattice_m=r["lattice_m"],
                         surfaces=r["surfaces"],
                         segment=r["segment"],
                         station=r["station"],
                         # THE CUT THIS REFUSAL PRESCRIBES, and its depth is
                         # the OVERAGE and not the step: a cut-only repair
                         # lowers the high side until the rise is inside the
                         # slide limit, never to the low side's own height.
                         overage_m=round(r["step_m"] - r["limit_m"], 4),
                         # WHERE THE CUT GOES, AND WHICH END IT COMES OFF.
                         # This refusal published `where` - ONE endpoint -
                         # and a step has two.  MEASURED on Ulfsland
                         # 245daa7f: all ten traversability refusals carried
                         # a depth (`overage_m`) and a footprint
                         # (`repair_footprint_m`) and NOT ONE carried the
                         # edge those numbers belong to, so every one of
                         # them was classified ACTIONABLE while being
                         # impossible to plan a cut from without going back
                         # to the world.  `at` is the transition, in the
                         # SAME [ax, az, bx, bz] shape `terminus_step`
                         # publishes, and `ground_rise_m` is signed b minus
                         # a so a planner can tell the high side from the
                         # low one.  `repair_population` is this check's
                         # population of exactly one - each over-limit
                         # transition is already its own refusal - and it
                         # is named so a disposal can consume every step
                         # check in this gate through one shape.
                         at=[r["x"], r["z"], r["x2"], r["z2"]],
                         judged_as="lattice_edge",
                         ground_a_m=r["ground_a_m"],
                         ground_b_m=r["ground_b_m"],
                         ground_rise_m=round(r["ground_b_m"]
                                             - r["ground_a_m"], 3),
                         repair_population=[dict(
                             at=[r["x"], r["z"], r["x2"], r["z2"]],
                             step_m=r["step_m"],
                             over_by_m=round(r["step_m"] - r["limit_m"], 4))],
                         carried_by_a_surface=carried_by.get(
                             (r["x"], r["z"], r["x2"], r["z2"])))))
    for a in ambiguous:
        findings.append(Finding(
            check="traversability", ok=False,
            message=f"{a['segment']} station {a['station']}: the vertex at "
                    f"({a['x']:.0f},{a['z']:.0f}) returned two heights "
                    f"{a['difference_m']} m apart - {a['why']}",
            where=(a["x"], a["z"]), measured=a["difference_m"],
            numbers=dict(first=a["first"], second=a["second"],
                         segment=a["segment"],
                         # AN AMBIGUITY IS NOT A STEP AND NO CUT CLOSES ONE.
                         # MEASURED, N4-mid2-terminus (-949,7663): two
                         # heights 0.0579 m apart, on the half-lattice line
                         # where this gate snaps up and Unity's RoundToInt
                         # snaps to even.  Lowering either reading leaves
                         # the vertex two-valued; the remedy is a
                         # disagreement between two instruments and nothing
                         # in this tree performs it.
                         no_cut_proposed=True)))
    for u in unread:
        if scope and u["segment"].split("-")[0] not in scope:
            continue
        findings.append(Finding(
            check="traversability", ok=False,
            message=f"{u['segment']} station {u['station']}: {u['why']}",
            where=(u["x"], u["z"]),
            numbers=dict(station=u["station"], segment=u["segment"],
                         no_cut_proposed=True)))

    # --- CHECK 3: obstruction ----------------------------------------------
    #
    # TWO POPULATIONS, NAMED, BECAUSE THEY ARE KNOWN TO DIFFERENT PRECISIONS.
    #   built   - a placed PIECE, geometry from its own primitive colliders.
    #             This is the population the question "what is standing in my
    #             road" is about, and it decides the verdict.
    #   natural - a rock or a prop whose collider is a mesh, recorded only as
    #             the mesh's BOUNDING BOX.  Reported with that caveat and
    #             advisory, because failing a world for a boulder whose true
    #             footprint nobody measured would be a number without its
    #             precision, which is the fault this gate exists to end.
    blocked_built: list[dict] = []
    blocked_natural: list[dict] = []
    blocking_pieces: dict[int, I.Solid] = {}
    climb_pieces: dict[int, I.Solid] = {}
    climb_in_scope: dict[int, I.Solid] = {}
    unknown_geometry: dict[str, int] = defaultdict(int)
    unknown_in_scope: dict[str, int] = defaultdict(int)
    # BODIES ARE CLUSTERED FROM ALL BUILT PIECES, NOT FROM THE INTRUDING ONES.
    # A wall that pokes into the road belongs to a building, and clustering
    # only the three panels that cross the kerb would report three bodies
    # where there is one house.  A body count that is really a fragment count
    # is the same fault as a count without its population.
    all_bodies = I.bodies([s for s in built
                           if s.name in build_pieces and s.verts])
    body_of: dict[int, int] = {}
    for bi, b in enumerate(all_bodies):
        for p in b:
            body_of[id(p)] = bi
    # A ROAD'S OWN DESTINATION IS NOT AN OBSTRUCTION OF IT.  A road that ends
    # at a hall ends INSIDE the hall; the hall is where the walker was going.
    # Derived, not listed: a body is a terminus if it reaches the segment's
    # own first or last station.  Nine of our bridge decks were once the
    # biggest "blockers" for the same reason, and those are already excluded
    # by being the walking surface.
    terminus_bodies: set[int] = set()
    for i, name in enumerate(codes):
        st = stations[name]
        span = segs[name]["half_width_m"] + segs[name]["shoulder_m"]
        for end in (st[0], st[-1]):
            for s2 in grid.near(end[1], end[2], span):
                bi = body_of.get(id(s2))
                if bi is None:
                    continue
                if (s2.xmin - span <= end[1] <= s2.xmax + span
                        and s2.zmin - span <= end[2] <= s2.zmax + span):
                    terminus_bodies.add(bi)
    for i, name in enumerate(codes):
        s = segs[name]
        hw = s["half_width_m"]
        st = stations[name]
        in_scope = (not scope) or s["code"] in scope
        # THE WALKER'S SURFACE IS A PROPERTY OF THE CHAIN, NOT OF ONE
        # STATION.  See `standing_surfaces`: chosen once per segment so a
        # deck reached at its abutment is still underfoot over its span.
        stand = standing_surfaces(
            [(x, z) for _d, x, z in st],
            [walk_surface.get(f"{i}|{j}") or [] for j in range(len(st))])
        for j, (_d, x, z) in enumerate(st):
            surf = walk_surface.get(f"{i}|{j}")
            if not surf:
                continue
            y = stand[j]
            k = min(j + 1, len(st) - 1)
            dx = st[k][1] - st[max(j - 1, 0)][1]
            dz = st[k][2] - st[max(j - 1, 0)][2]
            dl = math.hypot(dx, dz) or 1.0
            ux, uz = -dz / dl, dx / dl          # unit vector ACROSS the road
            on_surface = {id(t[1]) for t in surf if t[1] is not None
                          and abs(t[0] - y) <= 0.05}
            bl = [t for t in I.blockers_at(x, z, y, ux, uz, hw, grid,
                                           clearable, not_property)
                  if id(t[2]) not in on_surface]
            if not bl:
                continue
            for _a, _b, sol in bl:
                if not sol.known:
                    unknown_geometry[sol.name] += 1
                    if in_scope:
                        unknown_in_scope[sol.name] += 1
            built_int = [(a, b, sol) for a, b, sol in bl
                         if sol.name in build_pieces
                         and body_of.get(id(sol)) not in terminus_bodies]
            # THE CLASSIFICATION THE TICKET ASKS FOR: climb, or go round.
            # Every built piece here already rises further out of the walking
            # surface than the ground itself is allowed to, so a walker who
            # keeps the centreline must CLIMB it.  Whether he can instead go
            # ROUND is the width question, answered by the widest free gap
            # across the paved width against his own measured capsule.
            for _a, _b, sol in built_int:
                climb_pieces[id(sol)] = sol
                if in_scope:
                    climb_in_scope[id(sol)] = sol
            for pop, ivs, sink in (("built", built_int, blocked_built),
                                   ("any", bl, blocked_natural)):
                gap = I.widest_gap([(a, b) for a, b, _ in ivs], hw)
                if gap >= 2 * cen.walker_radius:
                    continue
                # THE BODIES, NOT ONLY THE PIECES.  A station blocked by
                # eleven wall panels of one house and one blocked by
                # eleven separate posts are different worlds, and the
                # termination class asks "is a BODY standing in the
                # carriageway" - a question `pieces` cannot answer.
                bodies = {body_of.get(id(sol)) for _a, _b, sol in ivs}
                bodies.discard(None)
                rec = dict(segment=s["seg"], station=j, x=round(x, 1),
                           z=round(z, 1), widest_gap_m=round(gap, 2),
                           needs_m=round(2 * cen.walker_radius, 2),
                           pieces=len(ivs), bodies=len(bodies),
                           prefabs=sorted({sol.name for _a, _b, sol in ivs}),
                           # NAMED, so whose they are can be read off the
                           # ledger (`termination.own_blockers`).
                           blocker_pieces=[T.piece_ref(sol)
                                           for _a, _b, sol in ivs])
                if pop == "built":
                    sink.append(rec)
                    for _a, _b, sol in ivs:
                        blocking_pieces[id(sol)] = sol
                elif not built_int or I.widest_gap(
                        [(a, b) for a, b, _ in built_int], hw) >= \
                        2 * cen.walker_radius:
                    sink.append(rec)        # blocked ONLY once the coarse
                    #                         bounding boxes are counted
    # Attribute every intruding piece to the WHOLE body it belongs to.
    climb_by_body: dict[int, list[I.Solid]] = defaultdict(list)
    for pid, sol in climb_pieces.items():
        climb_by_body[body_of.get(pid, -1)].append(sol)
    scope_ids = set(climb_in_scope)
    bodies_in_scope = {body_of.get(pid, -1) for pid in scope_ids}
    body_rows = []
    for bi, members in sorted(climb_by_body.items(),
                              key=lambda kv: -len(kv[1])):
        whole = all_bodies[bi] if bi >= 0 else members
        names: dict[str, int] = defaultdict(int)
        for s in members:
            names[s.name] += 1
        body_rows.append(dict(
            pieces_in_road=len(members), pieces_in_body=len(whole),
            at=[round(sum(q.pos[0] for q in members) / len(members), 1),
                round(sum(q.pos[2] for q in members) / len(members), 1)],
            must_go_round=not any(id(p) in blocking_pieces for p in members),
            in_scope=bi in bodies_in_scope,
            top_prefabs=sorted(names.items(), key=lambda kv: -kv[1])[:3],
            # THE PIECES IN THE ROAD, NAMED BY PREFAB AND PIVOT - they are
            # what a repair retires or re-places, and whose they are is read
            # off the ledger, not assumed.  MEASURED on drive 3: all 36
            # wood_poles of "36 of 134 pieces of one body" were
            # T1-portalhub-temple-x01's own deck (seq 2682-2684, step 6.5).
            blocker_pieces=[T.piece_ref(q) for q in members]))
    blocking_body_ids = {body_of.get(pid, -1) for pid in blocking_pieces}
    scoped_blocked = [r for r in blocked_built
                      if not scope or r["segment"].split("-")[0] in scope]
    report["obstruction"] = dict(
        instrument="save ZDO census; each prefab's own solid rotated by its "
                   "own quaternion, footprint taken as the CONVEX HULL of "
                   "the rotated solid and clipped against the station's "
                   "cross-section; a piece counts only if it rises more than "
                   f"{STEP_LIMIT_PER_M:.4f} m out of the walking surface AND "
                   "its underside is low enough to be in the way",
        population="DECISIVE: non-clearable BUILD PIECES inside the paved "
                   "width. Our own lamps sit at 3.44-3.55 m lateral and fall "
                   "outside a 3.0 m half-width by geometry, not by a name "
                   "list; a piece that IS the walking surface at a station (a "
                   "bridge deck, a quay) is the road there and is not an "
                   "obstruction of it, which is why nine of our own bridge "
                   "decks are no longer the biggest blockers.",
        classification="MUST CLIMB = rises out of the walking surface by "
                       f"more than {STEP_LIMIT_PER_M:.4f} m inside the paved "
                       "width. MUST GO ROUND = must climb, but a gap at "
                       "least the walker's own capsule wide remains across "
                       "the carriageway. BLOCKED = must climb and no such "
                       "gap remains.",
        must_climb_pieces=len(climb_pieces),
        must_climb_bodies=len(climb_by_body),
        must_climb_bodies_in_scope=len(bodies_in_scope),
        terminus_bodies_excluded=len(terminus_bodies),
        blocked_stations=len(blocked_built),
        blocked_in_scope=len(scoped_blocked),
        blocking_bodies=len(blocking_body_ids),
        blocking_pieces=len(blocking_pieces),
        worst=sorted(blocked_built, key=lambda r: r["widest_gap_m"])[:25],
        bodies=body_rows[:80],
        advisory_natural=dict(
            population="rocks and props whose collider is a MESH; "
                       "PieceGeometry.cs records those only as the mesh's "
                       "bounding box, so the footprint is an over-estimate "
                       "and these are reported, not counted against the "
                       "world",
            blocked_stations=len(blocked_natural),
            worst=sorted(blocked_natural,
                         key=lambda r: r["widest_gap_m"])[:10]),
        **{"pass": not scoped_blocked and not bodies_in_scope})
    # THE FOUR NUMBERS THE 1D_OBSTRUCTED PREDICATE READS, on every row.
    # `obstruction_bodies` IS the predicate - a positive int means a body
    # of standing pieces intersects the carriageway, and the remedy is
    # demolition or a re-route, neither of which is a terrain cut.  The
    # gap and the BLOCKS flag are SEVERITY and decide nothing: MEASURED on
    # this world, 45 of 47 bodies leave a walker-wide gap and classing
    # those ACTIONABLE would assert that a 457-piece dvergr town in the
    # road is a repair the writer permits and that destroys nothing.  The
    # three-state rule holds here too - None means this arm never ran.
    for r in sorted(blocked_built, key=lambda r: r["widest_gap_m"]):
        if scope and r["segment"].split("-")[0] not in scope:
            continue
        findings.append(Finding(
            check="obstruction", ok=False,
            message=f"{r['segment']} station {r['station']}: widest gap "
                    f"{r['widest_gap_m']} m < the walker's own "
                    f"{r['needs_m']} m; {','.join(r['prefabs'][:3])}",
            where=(r["x"], r["z"]), measured=r["widest_gap_m"],
            limit=r["needs_m"],
            numbers=dict(pieces=r["pieces"], segment=r["segment"],
                         obstruction_bodies=r["bodies"],
                         obstruction_pieces=r["pieces"],
                         obstruction_gap_m=r["widest_gap_m"],
                         obstruction_blocks=True,
                         walker_width_m=r["needs_m"],
                         blocker_pieces=r["blocker_pieces"])))
    for b in body_rows:
        if not b["in_scope"]:
            continue
        findings.append(Finding(
            check="obstruction", ok=False,
            message=f"{b['pieces_in_road']} of {b['pieces_in_body']} pieces "
                    f"of one body stand in the carriageway "
                    f"({'go round' if b['must_go_round'] else 'BLOCKS'}); "
                    f"{b['top_prefabs'][0][0]}",
            where=(b["at"][0], b["at"][1]),
            numbers=dict(pieces_in_road=b["pieces_in_road"],
                         pieces_in_body=b["pieces_in_body"],
                         obstruction_bodies=1,
                         obstruction_pieces=b["pieces_in_road"],
                         # A BODY ROW IS NOT A STATION ROW AND HAS NO GAP.
                         # The widest free gap is measured ACROSS one
                         # station's cross-section; a body that leaves a
                         # walker-wide gap at every station it touches
                         # produced no blocked station at all, so there is
                         # no blocking metre to quote.  None here says
                         # exactly that and never "the gap is zero".
                         obstruction_gap_m=None,
                         obstruction_blocks=not b["must_go_round"],
                         walker_width_m=round(2 * cen.walker_radius, 2),
                         blocker_pieces=b["blocker_pieces"])))
    for rec in sorted(blocked_natural, key=lambda r: r["widest_gap_m"])[:10]:
        findings.append(Finding(
            check="obstruction", ok=False, advisory=True,
            message=f"{rec['segment']} station {rec['station']}: mesh-"
                    f"collider prop, widest gap {rec['widest_gap_m']} m "
                    "(footprint known only to its bounding box)",
            where=(rec["x"], rec["z"]), measured=rec["widest_gap_m"]))

    # --- COVERAGE: what the instrument could not see -----------------------
    report["coverage"] = dict(
        instrument="prefab names from the census against "
                   "blueprints/data/piece_geometry.json",
        population="every object inside a road corridor whose prefab the "
                   "geometry corpus has never measured",
        unmeasured_prefabs=dict(sorted(unknown_geometry.items(),
                                       key=lambda kv: -kv[1])[:20]),
        unmeasured_in_scope=dict(sorted(unknown_in_scope.items(),
                                        key=lambda kv: -kv[1])[:20]),
        remedy="run tools/jumpstart/blueprints/run_piecegeometry.sh in a "
               "sandbox with the CONTENT MODS staged and rebuild the corpus; "
               "until then this gate cannot answer whether these objects "
               "block a road, and says so rather than assuming they do not",
        **{"pass": not unknown_in_scope})
    for nm, n in sorted(unknown_in_scope.items(), key=lambda kv: -kv[1]):
        findings.append(Finding(
            check="coverage", ok=False,
            message=f"{n} objects of prefab {nm!r} stand in a road corridor "
                    "and the geometry corpus has never measured it",
            numbers=dict(objects=n, prefab=nm)))

    # --- CHECK 2: connectivity, WALKED ON THE SAME LATTICE -----------------
    # The graph's nodes are the VERTICES the traversability walk used, not the
    # 1 m stations: two stations that share a vertex are one place, and an
    # edge between them would be an edge a walker never takes.  Its edges are
    # still THE STEPS - an edge exists only where the step between two
    # adjacent vertices is inside the slide limit for THEIR separation.
    # A BRIDGE IS PART OF THE ROAD AND THIS GRAPH COULD NOT SEE ONE.
    #
    # The nodes below are the vertices of the 25 centrelines the ledger
    # records a `terrain_write` for.  A DECLARED crossing - one that joins
    # TWO segments across water - has no terrain write by construction (the
    # ribbon stops at the bank; `why_terrain_failed: water_body`), so it
    # contributes NO nodes, and the two segments it joins sit at opposite
    # ends of a hole in the graph.
    #
    # MEASURED, and this is why it is a fix and not a preference.  Crossing
    # S2 joins S1-portalhub-brgs2's last station (-316.0, 230.0) to
    # N0-brgs2-wmid's first (-350.0, 246.0): 37.6 m apart, against a 3.0 m
    # junction threshold, so no edge.  The deck between them IS BUILT - 60
    # `wood_floor` ZDOs at y 30.5 from (-317.2, 227.3) to (-348, 246), read
    # out of this save's own census - and both roads declare `road_deck_y`
    # 30.600 at their abutments.  A walker crosses it.  The graph said he
    # could not, and every destination on the far side of the strait was
    # reported unreachable for a reason that was an artefact of the
    # population rather than a property of the world.
    #
    # THIS DOES NOT WEAKEN ANYTHING.  The deck is found FROM THE BODIES by
    # the same `surfaces_at` the traversability walk already uses, its bed
    # is read from the save like every other point, and every step along it
    # is judged at the SAME tan(38 deg) limit.  What changes is that the
    # gate can now REFUSE a declared crossing - today a bridge with a hole
    # in it and a bridge with no deck at all score identically with a
    # perfect one, because all three are invisible.
    # THE SAME ROWS THE MASK WAS BUILT FROM.  Read once, at the top of
    # `run`, so the grid that answers "what stands on this metre" and the
    # rows that ask it can never again be drawn from two different
    # populations.
    declared = crossing_declarations
    # ONE PHYSICAL CHANNEL, ONE OBLIGATION.  Computed ONCE, here, over the
    # whole corpus, because "is this record a duplicate" is a property of
    # the corpus and not of any one row.  Every row keeps its own
    # measurement; `duplicate_of` only says which of them are the same
    # water.  See `collapse_crossing_declarations`.
    channel_census = collapse_crossing_declarations(declared)
    for c in declared:
        c["duplicate_of"] = channel_census["collapsed"].get(c["id"])
    # WHICH SEGMENTS EXIST, from the same loader that defines this section's
    # whole population.  A crossing is judged either way; this only says
    # whether the road it belongs to has been built, and the two cases must
    # never be reported in the same shape.
    built_ids = {s["seg"] for s in segs.values()}
    span_pts: list[tuple[str, float, float]] = []
    for ci, c in enumerate(declared):
        for k, (x, z) in enumerate(c["samples"]):
            span_pts.append((f"x{ci}|{k}", x, z))
    span_bed = ctx.ground(span_pts) if span_pts else {}

    npos: dict[str, tuple[float, float]] = {}
    node_surf: dict[str, list] = {}

    def node_v(i: int, k: int) -> tuple[float, float]:
        return chains[codes[i]][k]["v"]

    node_keys: dict[str, str] = {}
    adj: dict[str, list[str]] = defaultdict(list)
    blocked_set = {(r["segment"], r["station"]) for r in blocked_built}
    blocked_rec = {(r["segment"], r["station"]): r for r in blocked_built}
    # WHICH SEGMENT EACH GRAPH NODE BELONGS TO, and every transition the walk
    # REFUSED together with the reason it refused it.  Both are inputs to the
    # severance diagnosis; neither is a second measurement.
    seg_of: dict[str, str] = {}
    refused: list[dict] = []
    for i, name in enumerate(codes):
        s = segs[name]
        chain = chains[name]
        blocked_node: dict[int, dict] = {}
        for k, nd in enumerate(chain):
            for j in nd["stations"]:
                if (s["seg"], j) in blocked_set:
                    blocked_node[k] = blocked_rec[(s["seg"], j)]
                    break
        for k, nd in enumerate(chain):
            node_surf[f"{i}|{k}"] = vsurf(nd["v"])
            npos[f"{i}|{k}"] = nd["v"]
            seg_of[f"{i}|{k}"] = s["seg"]
        for k in range(len(chain) - 1):
            ka, kb = f"{i}|{k}", f"{i}|{k+1}"
            sep = math.hypot(chain[k + 1]["v"][0] - chain[k]["v"][0],
                             chain[k + 1]["v"][1] - chain[k]["v"][1])
            if k in blocked_node or k + 1 in blocked_node:
                br = blocked_node.get(k) or blocked_node[k + 1]
                refused.append(dict(
                    a=ka, b=kb, reason="blocked", separation_m=sep,
                    widest_gap_m=br["widest_gap_m"], needs_m=br["needs_m"],
                    # THE BODY COUNT TRAVELS WITH THE LINK, because the
                    # destination that this link severs is classified off
                    # it: `1D_OBSTRUCTED` asks how many BODIES stand across
                    # the carriageway, and a prefab list cannot answer that.
                    bodies=br.get("bodies"), pieces=br["pieces"],
                    prefabs=br["prefabs"][:3],
                    blocker_pieces=br.get("blocker_pieces")))
                continue
            # THE EDGE IS THE ONE THE TRAVERSABILITY WALK JUDGED.  A diagonal
            # move is two mesh edges through a corner and an edge exists only
            # if BOTH are inside the limit; a transition whose corners were
            # never read has no `ok_to_next` at all and cannot become an edge
            # on the strength of not having been measured.
            if not chain[k].get("ok_to_next"):
                wo = chain[k].get("worst_over")
                # AND THE SUB-EDGE THE WALK ACTUALLY FAILED TRAVELS WITH
                # THE LINK, SIGNED.  A diagonal move is TWO 1 m mesh edges
                # through a corner and `worst_over` names the one that
                # refused - its own two vertices, its own ground at each
                # end, its own 1 m run.  Until here only `step_m` came
                # across, so a consumer holding the link had the SIZE of
                # the step and not the EDGE, and the only way back to the
                # edge was to re-derive it from the link's two endpoints
                # under a convention the walk did not use.
                #
                # MEASURED, Ulfsland 7632b3a5674842bd, the harboursouth
                # connectivity row: the link is the DIAGONAL
                # (13,-255)->(14,-256), separation 1.414 m, and the walk
                # refused it through the corner (14,-255) on the 1 m
                # sub-edge (14,-255)->(14,-256), 0.794 m.  Re-derived from
                # the link's own endpoints under the RUN convention that
                # step is 0.794 against 0.7813 * 1.414 = 1.1047 and is
                # CLEAR - the classifier read a NEGATIVE overage, clamped
                # it to zero, and asked the ownership question about a
                # repair of depth 0.  See `road_termination`.
                refused.append(dict(
                    a=ka, b=kb, separation_m=sep,
                    reason="unresolved" if chain[k].get("unresolved")
                    else "step",
                    **(dict(step_m=wo["step_m"], limit_m=wo["limit_m"],
                            ground_only_m=wo["ground_only"],
                            step_at=[wo["x"], wo["z"], wo["x2"], wo["z2"]],
                            step_ground_a_m=wo["ground_a_m"],
                            step_ground_b_m=wo["ground_b_m"],
                            step_lattice_m=wo["lattice_m"])
                       if wo else {})))
                continue
            adj[ka].append(kb)
            adj[kb].append(ka)
        if chain:
            node_keys[s["from"]] = node_keys.get(s["from"]) or f"{i}|0"
            node_keys[s["to"]] = (node_keys.get(s["to"])
                                  or f"{i}|{len(chain)-1}")
    # the deck's own nodes, and the metre-by-metre verdict on each span
    span_rows = []
    for ci, c in enumerate(declared):
        keys: list[str | None] = []
        unread = 0
        for k, (x, z) in enumerate(c["samples"]):
            b = span_bed.get(f"x{ci}|{k}")
            if b is None or b != b:
                keys.append(None)
                unread += 1
                continue
            key = f"x{ci}|{k}"
            npos[key] = (x, z)
            seg_of[key] = f"crossing:{c['id']}"
            node_surf[key] = _dry(
                I.surfaces_at(x, z, b, grid, cen.walker_radius))
            keys.append(key)
        broke = []
        for k in range(len(keys) - 1):
            a, b = keys[k], keys[k + 1]
            if a is None or b is None:
                broke.append(k)
                continue
            d = math.hypot(npos[b][0] - npos[a][0], npos[b][1] - npos[a][1])
            if edge_ok(node_surf[a], node_surf[b], d):
                adj[a].append(b)
                adj[b].append(a)
            else:
                broke.append(k)
        live = [k for k in keys if k is not None]
        # AND THE CROSSING'S OWN PREMISE, on the SAME beds.  See
        # `crossing_premise` - free, because `span_bed` is already read.
        beds = [span_bed[f"x{ci}|{k}"] for k in range(len(c["samples"]))
                if span_bed.get(f"x{ci}|{k}") is not None
                and span_bed[f"x{ci}|{k}"] == span_bed[f"x{ci}|{k}"]]
        prem = crossing_premise(beds, c["deck_y"])
        # AND WHERE THE REMEDY WOULD HAVE TO LAND.  A refused premise
        # prescribes exactly one repair - hand these stations back to the
        # RIBBON, which writes terrain across them - so the stations whose
        # bed stands above the deck, with the overage that must come down,
        # ARE the write. Carried so the refusal can be asked whether that
        # write is one the writer would accept. See `crossing_termination`.
        over_at = []
        if c["deck_y"] is not None:
            for k, (x, z) in enumerate(c["samples"]):
                b = span_bed.get(f"x{ci}|{k}")
                if b is None or b != b:
                    continue
                if b - c["deck_y"] > CROSSING_BURIAL_MAX_M:
                    over_at.append((x, z, b - c["deck_y"]))
        span_rows.append(dict(
            crossing=c["id"], joins=c["joins"], status=c.get("status"),
            segment_id=c.get("segment_id"),
            # THE SAME PHYSICAL CHANNEL, DECLARED TWICE.  Carried onto the
            # row so that neither the headline's numerator nor its
            # denominator counts one deck as two, and so that a hole in one
            # deck is refused ONCE rather than once per declaration.  The
            # row is measured either way; nothing is dropped.
            duplicate_of=c.get("duplicate_of"),
            # ON A ROAD THAT EXISTS, OR NOT.  A crossing on a segment the
            # ledger has never written terrain for is a hole in a road
            # nobody has built; a crossing on a BUILT segment is a road
            # that ends in the air, and only the second one is this
            # world's defect.  The distinction is printed, never used to
            # drop a row from the population.
            on_built_segment=bool(c.get("segment_id")
                                  and c["segment_id"] in built_ids),
            bank_a=list(c["a"]), bank_b=list(c["b"]),
            span_m=round(c["span_m"], 1),
            samples=len(c["samples"]), samples_unread=unread,
            deck_datum_y=c["deck_y"],
            carried_by_a_piece=sum(
                1 for k in live
                if any(s is not None for _y, s in node_surf[k])),
            breaks=len(broke),
            first_break_at=(
                [round(v, 1) for v in c["samples"][broke[0]]]
                if broke else None),
            premise=prem,
            bed_above_deck_at=[[round(v, 3) for v in t] for t in over_at],
            **{"pass": not broke and not unread and prem["pass"]}))
    # junctions: two vertices of DIFFERENT segments close enough to step
    # between.  A deck node is its own "segment" for this purpose, which is
    # exactly what joins it to the abutment it lands on.
    jgrid: dict[tuple[int, int], list[tuple[str, float, float, list]]] = \
        defaultdict(list)
    for key, (x, z) in npos.items():
        jgrid[(int(x // 4), int(z // 4))].append(
            (key, x, z, node_surf[key]))
    junctions = 0
    for cellkey, items in jgrid.items():
        cands = []
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                cands.extend(jgrid.get((cellkey[0] + dx, cellkey[1] + dz), ()))
        for k1, x1, z1, s1 in items:
            for k2, x2, z2, s2 in cands:
                if k1 >= k2 or k1.split("|")[0] == k2.split("|")[0]:
                    continue
                d = math.hypot(x2 - x1, z2 - z1)
                if d > JUNCTION_M:
                    continue
                if not edge_ok(s1, s2, d):
                    continue
                adj[k1].append(k2)
                adj[k2].append(k1)
                junctions += 1
    # HOW MANY PIECES IS THE NETWORK IN.  "Nothing is reachable from the
    # portal hall" is true and useless on its own: it does not say whether
    # the road is broken everywhere or severed in one place.  The component
    # census answers that, and it is the difference between "re-grade one
    # step" and "re-grade the world".
    def components() -> tuple[dict[str, int], list[int]]:
        comp: dict[str, int] = {}
        comps: list[int] = []
        for k in npos:
            if k in comp:
                continue
            cid = len(comps)
            stack = [k]
            comp[k] = cid
            n = 0
            while stack:
                cur = stack.pop()
                n += 1
                for nxt in adj[cur]:
                    if nxt not in comp:
                        comp[nxt] = cid
                        stack.append(nxt)
            comps.append(n)
        return comp, comps

    comp, comps = components()
    # AND THE NEAR MISSES, READ RATHER THAN ASSUMED.  Two roads whose nearest
    # STATIONS are further apart than JUNCTION_M can still share paved
    # ground - an end-to-end merge of two 2 m-station roads is 4.000 m at its
    # closest by construction, and `T8-stenvik-wttown` / `R1-ulfsgard-
    # t8junction` is exactly that.  Only pairs still in DIFFERENT components
    # are candidates, so this runs on the damage and not on the network.
    bridge_edges, bridge_pop = near_miss_bridges(
        npos, node_surf, comp, seg_of, lambda pts: ctx.ground(pts),
        lambda x, z, bed: I.surfaces_at(x, z, bed, grid, cen.walker_radius))
    for ka, kb in bridge_edges:
        adj[ka].append(kb)
        adj[kb].append(ka)
    if bridge_edges:
        comp, comps = components()
    # Preserve the physical walking graph and its component census. A ferry
    # is NOT a bridge or a walkable step: only independently accepted saved
    # docks/boats/access/full-hull sea passages enter this journey graph.
    transport_links, transport_findings, transport_report = (
        NT.road_transport_links(ctx, npos, seg_of))
    findings += transport_findings
    report["north_transport"] = transport_report
    travel_adj = {key: list(neighbours) for key, neighbours in adj.items()}
    voyages = {}
    for link in transport_links:
        ka, kb = link["ka"], link["kb"]
        travel_adj.setdefault(ka, []).append(kb)
        travel_adj.setdefault(kb, []).append(ka)
        voyages[(ka, kb)] = voyages[(kb, ka)] = link
    origin_name = "portalhub" if "portalhub" in node_keys else \
        sorted(node_keys)[0]
    src = node_keys[origin_name]
    # WHY, not just WHETHER.  The components are the nodes of a second graph
    # whose edges are THE MISSING LINKS; every unreachable destination gets
    # the cheapest chain of them and the CATEGORY of each, because a
    # re-grade, a bridge deck and a house in the road are three different
    # people's work and "unreachable" does not tell them apart.
    frozen_at, frozen_pop = frozen_ground(ctx)
    # THE REPAIR'S OWN GROUND, read with the same instrument as everything
    # else here: `ctx.ground` is the engine's applied bed off this run's
    # frozen copy, batched, so the disposal a category is a claim about is
    # measured on the save rather than on the plan.
    def _disposal_field(pts):
        if not pts:
            return {}
        got = ctx.ground([(f"d{i}", float(x), float(z))
                          for i, (x, z) in enumerate(pts)])
        out = {}
        for i, (x, z) in enumerate(pts):
            y = got.get(f"d{i}")
            if y is not None:
                out[(x, z)] = float(y)
        return out

    sev_links, sev_reach = severance(
        npos, node_surf, comp, src, refused, seg_of,
        [r for r in span_rows if not r["pass"]], frozen_at=frozen_at,
        height_field=_disposal_field)
    # DIJKSTRA OVER THE STEPS, so the distance reported is a distance somebody
    # could actually walk.  The figure this replaces - "portal hall ->
    # Vestvik 3,248.7 m" - was a shortest path over a PLANNED graph, and the
    # route it measured had a 6.12 m wall in it 773 m short of the far end.
    # Here an edge exists only where a step exists, so the number and the
    # verdict cannot disagree.
    dist: dict[str, float] = {src: 0.0}
    sailed: dict[str, float] = {src: 0.0}
    pq = [(0.0, src)]
    while pq:
        dcur, cur = heapq.heappop(pq)
        if dcur > dist.get(cur, math.inf) + 1e-9:
            continue
        cx, cz = npos[cur]
        for nxt in travel_adj.get(cur, ()):
            nx, nz = npos[nxt]
            voyage = voyages.get((cur, nxt))
            nd = dcur + (voyage["sailing_m"] + voyage["walking_m"]
                         if voyage else math.hypot(nx - cx, nz - cz))
            if nd < dist.get(nxt, math.inf) - 1e-9:
                dist[nxt] = nd
                sailed[nxt] = sailed[cur] + (voyage["sailing_m"] if voyage else 0.0)
                heapq.heappush(pq, (nd, nxt))
    north_doc, _north_contract_findings = I.north_itinerary(ctx)
    findings += required_connection_findings(north_doc, node_keys, dist)
    over_by_seg: dict[str, list[dict]] = defaultdict(list)
    for r in over_all:
        over_by_seg[r["segment"]].append(r)
    dests = []
    for nm, key in sorted(node_keys.items()):
        if nm == origin_name:
            continue
        di, dj = (int(v) for v in key.split("|"))
        seg = segs[codes[di]]
        row = dict(destination=nm, segment=seg["seg"],
                   reachable=key in dist)
        if key in dist:
            row["walked_m"] = round(dist[key] - sailed[key], 1)
            row["sailed_m"] = round(sailed[key], 1)
            row["journey_m"] = round(dist[key], 1)
        else:
            # HOW FAR DOES THE WALK GET, AND WHAT STOPS IT.  A bare
            # "unreachable" is the same non-answer as a graph distance: the
            # useful report names the metre the walk ends on.
            reached = [k for k in range(len(chains[codes[di]]))
                       if f"{di}|{k}" in dist]
            best = None
            if reached:
                k = min(reached, key=lambda k: abs(k - dj))
                best = (f"{di}|{k}", k)
            if best is not None:
                bx, bz = node_v(di, best[1])
                dx, dz = node_v(di, dj)
                row["walk_ends_at"] = [round(bx, 1), round(bz, 1)]
                row["walked_m_before_stopping"] = round(dist[best[0]], 1)
                row["short_of_destination_m"] = round(
                    math.hypot(dx - bx, dz - bz), 1)
                stop_stations = chains[codes[di]][best[1]]["stations"]
                near = [q for q in over_by_seg[seg["seg"]]
                        if any(abs(q["station"] - j) <= 2
                               for j in stop_stations)]
                if near:
                    row["stopped_by"] = max(near, key=lambda q: q["step_m"])
            # AND WHAT HAS TO BE FIXED TO GET THERE.  The chain of missing
            # links from the origin's component to this one, each with the
            # category of work it needs.  `blocked_by` is the FIRST link on
            # that chain - the one nearest the walker - because that is the
            # one whose repair extends the reachable set.
            ch = sev_reach.get(comp.get(key), {}).get("chain")
            if ch:
                row["blocked_by"] = ch[0]
                row["repair_chain"] = [
                    dict(reason=lk["reason"], category=lk["category"],
                         at=lk["at"], separation_m=lk["separation_m"],
                         segments=lk["segments"]) for lk in ch]
                row["repairs_needed"] = len(ch)
                row["category"] = max(
                    (lk["category"] for lk in ch),
                    key=lambda c: _CAT_RANK[c])
            else:
                # A RESIDUAL WITH ITS OWN REASON, so it is a work queue and
                # not a permanent category.  `severance` always supplies one.
                row["category"] = CAT_UNMEASURED
                row["repairs_needed"] = 0
                row["blocked_by"] = dict(
                    reason="no_link_found", category=CAT_UNMEASURED,
                    why=sev_reach.get(comp.get(key), {}).get(
                        "unmeasured",
                        "this component is not in the severance graph at "
                        "all, which means the graph and the walk disagree "
                        "about which vertices exist"))
        row["component"] = comp.get(key)
        row["component_stations"] = (comps[comp[key]] if key in comp
                                     else 0)
        dests.append(row)
    unreachable = [d for d in dests if not d["reachable"]]
    big = max(comps) if comps else 0
    report["connectivity"] = dict(
        instrument="Dijkstra over separate walking and journey graphs. Walking "
                   "edges are measured unblocked mesh steps; voyage edges "
                   "require BOTH saved docks, boats, walk-in access and a "
                   "full-hull saved sea passage. Voyages never enter the "
                   "walking graph or its physical component census.",
        transport_edges=transport_links,
        population=f"{len(node_keys)} named endpoints of {len(segs)} built "
                   f"segments, walked from '{origin_name}'",
        origin=origin_name,
        junction_steps=junctions,
        # THE NEAR MISSES THIS RUN READ RATHER THAN ASSUMED, with the count
        # it joined AND every one it refused: a bridging rule that only ever
        # reported what it joined would be a way of making the network look
        # connected.
        near_miss_bridges=bridge_pop,
        stations_reached=len(dist),
        components=len(comps),
        largest_component_stations=big,
        largest_component_share=round(big / max(len(comp), 1), 4),
        origin_component_stations=comps[comp[src]] if src in comp else 0,
        reachable=len(dests) - len(unreachable), total=len(dests),
        destinations=dests,
        unreachable=[d["destination"] for d in unreachable],
        # THE RANKING THE OPERATOR ASKED FOR.  Which of the unreachable
        # destinations are a re-grade, which are a structure to move, and
        # which are work only a human can authorise - stated as a count with
        # its population, not as a list of names with no cause.
        severance=dict(
            instrument="the walkable components are the nodes of a second "
                       "graph whose EDGES ARE THE MISSING LINKS; each link "
                       "carries the reason the first graph has no edge "
                       "there, taken from the check that refused it and "
                       "never re-derived, and the cheapest chain of links "
                       "from the origin's component to each other one is "
                       "that destination's repair list",
            population=f"{len(sev_links)} missing links between "
                       f"{len(comps)} walkable components",
            frozen_ground=frozen_pop,
            # THE REPAIR'S OWN POPULATION, and its verdict.  `frozen_ground`
            # above is what the guard protects; THIS is how much of it the
            # repair would have had to move.  A category is a claim about a
            # WRITE, so the set it is a claim about is the set that write
            # takes - never the two vertices of the defect.
            disposal=dict(
                instrument="`cut_disposal`: the Lipschitz cone seeded at "
                           "the link's LOW vertex over the engine applied "
                           "bed, f[v] = min(h[seed] + limit * L1(seed,v)), "
                           "taken where it is below the ground - the "
                           "smallest CUT-ONLY set that closes the step "
                           "without transferring it to its own rim",
                population="every reported link whose category was "
                           "a_regradeable and whose reason is a step",
                candidates=sum(1 for lk in sev_links
                               if "disposal_measured" in lk),
                measured=sum(1 for lk in sev_links
                             if lk.get("disposal_measured")),
                vertices=sum(lk.get("disposal_samples", 0)
                             for lk in sev_links),
                cut_m3=round(sum(lk.get("disposal_cut_m3", 0.0)
                                 for lk in sev_links), 3),
                on_frozen_ground=sum(1 for lk in sev_links
                                     if lk.get("disposal_frozen_samples")),
                read_boundary_limited=sum(
                    1 for lk in sev_links
                    if lk.get("disposal_reached_read_boundary")),
                why="a link whose disposal touches frozen ground is "
                    "RE-CATEGORISED b_operator here: the repair a "
                    "a_regradeable verdict promises is a write, and a write "
                    "`writer._flatten_forbidden_check` refuses moves "
                    "0.000 m"),
            categories={
                CAT_REGRADE: "an over-limit step or a junction two roads "
                             "miss, ON GROUND A WRITE MAY MOVE: A RE-EMIT "
                             "FIXES IT",
                CAT_STRUCTURE: "a built body leaves no gap across the "
                               "carriageway: A STRUCTURE IS IN THE WAY",
                CAT_OPERATOR: "over water, at a declared crossing with no "
                              "walkable deck, a hole with no road in it at "
                              "all, or a step whose ground a "
                              "`flatten: FORBIDDEN` piece forbids any write "
                              "to move (`ground_frozen_by`): ONLY AN "
                              "OPERATOR CAN AUTHORISE THIS",
                CAT_UNMEASURED: "this gate could not measure the "
                                "transition"},
            destinations_by_category={
                c: sorted(d["destination"] for d in unreachable
                          if d.get("category") == c)
                for c in (CAT_REGRADE, CAT_STRUCTURE, CAT_OPERATOR,
                          CAT_UNMEASURED)},
            first_link_by_category={
                c: sorted(d["destination"] for d in unreachable
                          if (d.get("blocked_by") or {}).get("category") == c)
                for c in (CAT_REGRADE, CAT_STRUCTURE, CAT_OPERATOR,
                          CAT_UNMEASURED)},
            links_by_reason=dict(sorted(
                Counter(lk["reason"] for lk in sev_links).items(),
                key=lambda kv: -kv[1])),
            # HOW MANY DESTINATIONS EACH MISSING LINK GATES.  This is the
            # number an operator decides on: "one stone_wall_4x2 stands
            # between the portal hall and nine places" is a decision, and
            # "22 blocked links" is a backlog.  Counted over the repair
            # chains, so a link that appears on nine chains gates nine.
            links=sorted(
                ({**lk, "gates_destinations": sum(
                    1 for d in unreachable
                    for q in (d.get("repair_chain") or ())
                    if q["reason"] == lk["reason"] and q["at"] == lk["at"])}
                 for lk in sev_links),
                key=lambda r: -r["gates_destinations"])[:80]),
        **{"pass": not unreachable})
    # CAN EACH REFUSAL EVER BE CLOSED.  Read ONCE, and only when this
    # section has actually refused something - the owner set is ~132,000
    # points over 397 live records and costs ~13 s, which is not worth
    # paying on a world with nothing to classify.  `None` propagates as
    # UNKNOWN rather than as "repairable": see `crossing_termination`.
    #
    # AND IT IS READ FOR EVERY ARM, NOT ONLY FOR THE CROSSINGS PREMISE.
    # This read was gated on `any premise refusal` while obstruction,
    # connectivity and traversability - 74 of this section's 87 refusals -
    # carried no termination class at all.  The gate that pays 13 s to
    # classify 13 rows and hands the operator the other 74 unsorted is
    # answering the cheap question.
    x_owners = x_writes = x_deck = None
    hard_so_far = [f for f in findings if not f.ok and not f.advisory]
    if hard_so_far or unreachable or any(not r["premise"]["pass"]
                                         for r in span_rows):
        x_owners, x_writes = owner_instruments(ctx)
        # AND THE THIRD INSTRUMENT: WOULD THE BUILDER LAY THE DECK.  Read
        # in the same place and on the same condition as the other two,
        # because a class that depends on three answers and reads two of
        # them is the state this arm was in - ACTIONABLE asserted from the
        # ownership answer alone.
        x_deck = deck_remedy_index(ctx)
    for r in span_rows:
        r["termination"] = crossing_termination(r, x_owners, x_writes)
    # THE FILE THIS RUN READ, BY ITS OWN NAME.  "roads/crossings.yaml" in a
    # message about a Fresh42 run names Ulfsland's corpus.
    _cp = crossings_corpus_path(I.run_scope(ctx)[0])
    corpus_name = str(_cp.relative_to(I.JUMPSTART)
                      if _cp.is_relative_to(I.JUMPSTART) else _cp)
    report["crossings"] = dict(
        corpus=corpus_name,
        instrument="the deck is found FROM THE BODIES at each metre of the "
                   "declared span (instruments.surfaces_at over the save's "
                   "own ZDO census), its bed is read from the save like "
                   "every other point, and consecutive metres are judged at "
                   f"the same {STEP_LIMIT_PER_M:.4f} m per metre limit as "
                   "the ground. No prefab is named and no span is excluded "
                   "by hand.",
        population="every crossing THE NETWORK DECLARES - "
                   f"{corpus_name} `declared` (Crossings' own) UNION "
                   "`detected` (the router's), deduplicated by crossing_id "
                   "with `declared` winning, per that file's own "
                   "`precedence`. These are the ones that join TWO segments "
                   "across water, which is exactly the set that can carry "
                   "no terrain write and so contributed no node to this "
                   "graph at all until now. This gate read `declared` ALONE "
                   "until 2026-09-22 and judged 2 of 16.",
        # THE TWO-NUMBER FORM.  `declared: 2` is unreadable; `2 of 16` is
        # self-evidently a bug.  Both numbers, always, and the split by
        # whether the road under the crossing has actually been built.
        declared=len(span_rows),
        declared_in_file=dict(
            declared=sum(1 for r in span_rows if r["status"] == "declared"),
            detected=sum(1 for r in span_rows if r["status"] == "detected")),
        on_built_segments=sum(1 for r in span_rows if r["on_built_segment"]),
        no_deck_on_a_built_segment=sorted(
            r["crossing"] for r in span_rows
            if r["on_built_segment"] and not r["carried_by_a_piece"]),
        undecked_m_on_built_segments=round(sum(
            r["span_m"] for r in span_rows
            if r["on_built_segment"] and not r["carried_by_a_piece"]), 1),
        # AND THE PREMISE, AS ITS OWN CENSUS.  "How many of these crossings
        # are crossing anything" is a different question from "how many of
        # them carry a walker", and a world can score 16/16 on the second
        # while a road is missing because of the first.
        premise_judged=sum(1 for r in span_rows if r["premise"]["judged"]),
        premise_refused=sorted(
            r["crossing"] for r in span_rows if not r["premise"]["pass"]),
        wet_metres_by_crossing={
            r["crossing"]: [r["premise"].get("wet_samples"), r["samples"]]
            for r in span_rows},
        # ONE CHANNEL, ONE OBLIGATION.  `declared` counts RECORDS; this
        # counts CHANNELS, and the two differ whenever the per-segment
        # detector emits the same water twice.  Both, always - a single
        # number here is the bug that made "3 of 17 carry a walker" read as
        # three built decks when two of the three were one deck.
        channels=channel_census,
        distinct_channels=channel_census["distinct_channels"],
        duplicate_declarations=sorted(channel_census["collapsed"]),
        carry_a_walker_records=sum(1 for r in span_rows if r["pass"]),
        carry_a_walker_channels=sum(1 for r in span_rows
                                    if r["pass"] and not r["duplicate_of"]),
        # DEFECT A, MADE VISIBLE RATHER THAN SILENT.  A crossing whose
        # PREMISE was refused is not judged for carriage - the gate may not
        # rule that no deck can exist here and in the same breath fail the
        # deck for not carrying.  The rows are named so the suppression is
        # auditable instead of being an unexplained absence of findings.
        carriage_not_judged_premise_refused=sorted(
            r["crossing"] for r in span_rows if not r["premise"]["pass"]),
        carriage_not_judged_duplicate=sorted(
            r["crossing"] for r in span_rows if r["duplicate_of"]
            and r["premise"]["pass"]),
        # AND WHETHER EACH REMAINING REFUSAL CAN EVER BE CLOSED.  "N
        # refusals" is a work queue and an operator decision list added
        # together unless each one says which it is.  See
        # `crossing_termination`.
        premise_refusals_by_termination={
            k: sorted(r["crossing"] for r in span_rows
                      if not r["premise"]["pass"]
                      and r["termination"]["termination"] == k)
            for k in ("ACTIONABLE", "1A_FORBIDDEN", "1B_CONTESTED",
                      "1C_UNSERVED", "1D_OBSTRUCTED", "1E_OUT_OF_ORDER")},
        spans=span_rows,
        **{"pass": all(r["pass"] for r in span_rows)
           and not channel_census["collapsed"]})
    # THE CORPUS ITSELF, BEFORE ANY ROW IN IT.  A detector that emits one
    # record per SEGMENT emits two records for a channel two segments have
    # to get across, and every number computed over that corpus is wrong in
    # both directions at once.  This is refused ONCE, against the file and
    # its producer, with its whole population - not once per row, because
    # the rows are not the defect.
    if channel_census["collapsed"]:
        cc = channel_census
        pairs = "; ".join(
            f"{p['a']} = {p['b']} ({p['fraction']:.3f})"
            for p in cc["pairs_over_threshold"])
        findings.append(Finding(
            check="crossings", ok=False,
            message=f"{corpus_name} DECLARES ONE PHYSICAL CHANNEL "
                    f"MORE THAN ONCE: {cc['declarations']} declarations "
                    f"describe {cc['distinct_channels']} distinct channels, "
                    f"{len(cc['collapsed'])} of them redundant - "
                    f"{cc['declared_m']} m of declared span is "
                    f"{cc['distinct_m']} m of channel. The detector builds "
                    f"`detected` as one record PER SEGMENT "
                    f"(roads/network.py), so a channel two segments must "
                    f"both get across is emitted twice under two names. "
                    f"That inflates this check's DENOMINATOR and credits "
                    f"ONE BUILT DECK to EVERY declaration that shares its "
                    f"corridor, and it would put a second deck in the same "
                    f"cubic metres as the first. Overlapping pairs, by "
                    f"corridor area at the deck's measured "
                    f"{cc['half_width_m']:.3f} m half-width: {pairs}",
            where=tuple(next(r["bank_a"] for r in span_rows
                             if r["crossing"] == sorted(cc["collapsed"])[0])),
            measured=max(p["fraction"] for p in cc["pairs_over_threshold"]),
            limit=cc["threshold"],
            numbers=dict(declarations=cc["declarations"],
                         distinct_channels=cc["distinct_channels"],
                         redundant=len(cc["collapsed"]),
                         collapsed=cc["collapsed"],
                         declared_m=cc["declared_m"],
                         distinct_m=cc["distinct_m"],
                         pairs=cc["pairs_over_threshold"])))
    # AND THE SECOND CORPUS REFUSAL, AGAINST THE SAME FILE AND A DIFFERENT
    # PRODUCER LINE.  A crossing record carries TWO lines and they are built
    # from TWO DIFFERENT POLYLINES: `roads/route.py::find_crossings` writes
    # `bank_a`/`bank_b` from the route polyline of pass one (lines 741-746,
    # then snapped to the piece grid), `roads/network.py::build_segment`
    # STRAIGHTENS every crossing window at line 576, and the same function
    # writes `centreline_straight_from_to` from the POST-straighten points
    # at lines 677-687.  THE BANK IS NEVER RE-READ AFTER THE STRAIGHTEN THAT
    # MOVED THE LINE IT NAMES - a key naming a different population than the
    # quantity depends on, in the producer.
    #
    # Refused ONCE against the file, with its whole population, not once per
    # row: the rows are not the defect.
    #
    # THE SAME FILE, THE SAME GUARD, AND THE SECOND READ DOES NOT GET ITS OWN
    # RULES.  This one opened the fixed repository path directly and logged
    # an unreadable corpus, so on `--world Fresh42` it ran the declaration
    # census over ULFSLAND's seventeen records.  `crossing_spans` above has
    # already refused in that case and its refusal is on `findings`; this
    # read takes the same answer and contributes an EMPTY census rather than
    # a foreign one.
    _corpus_doc, _ = I.read_scoped_corpus(
        ctx, "the crossings corpus", resolve=crossings_corpus_path,
        produced_by="roads/route.py::find_crossings + "
                    "roads/network.py::build_segment", check="crossings")
    _corpus_records = ([] if _corpus_doc is None else
                       list(_corpus_doc.get("detected") or ())
                       + list(_corpus_doc.get("declared") or ()))
    offline = declaration_off_centreline(_corpus_records)
    report["crossings"]["declaration_off_centreline"] = offline
    if offline["over"]:
        worst = offline["over"][0]
        findings.append(Finding(
            check="crossings", ok=False,
            message=(
                f"{corpus_name} DECLARES BANKS THAT DO NOT LIE ON "
                f"THE CENTRELINE IT DECLARES FOR THEM: "
                f"{len(offline['over'])} of {offline['judged']} record(s) "
                f"that carry a `centreline_straight_from_to` put a bank "
                f"further off it than the deck's "
                f"{offline['half_width_m']:.3f} m half-width, worst "
                f"{worst['crossing_id']} {worst['bank']} "
                f"{abs(worst['offset_m']):.3f} m. NO DECK THIS TREE BUILDS "
                f"ON THAT ROAD COVERS THE LINE THESE RECORDS DECLARE: a "
                f"deck stands on the segment's own bench, and the declared "
                f"line is somewhere else. The producer is "
                f"roads/route.py::find_crossings, which takes bank_a/bank_b "
                f"from the PRE-STRAIGHTEN route polyline (lines 741-746), "
                f"against roads/network.py::build_segment, which STRAIGHTENS "
                f"each crossing window (line 576) and then writes "
                f"centreline_straight_from_to from the POST-STRAIGHTEN "
                f"points (lines 677-687) without ever re-reading the banks. "
                f"The repair is NEVER to move a deck off its bench. Over "
                f"the limit: "
                + ", ".join(f"{r['crossing_id']} {abs(r['offset_m']):.3f}"
                            for r in offline["over"])),
            where=tuple(next(
                (c["bank_a"]["xz"] for c in _corpus_records
                 if c.get("crossing_id") == worst["crossing_id"]),
                (0.0, 0.0))),
            measured=offline["worst_m"], limit=offline["half_width_m"],
            numbers=dict(
                over=len(offline["over"]), judged=offline["judged"],
                half_width_m=offline["half_width_m"],
                worst_m=offline["worst_m"],
                no_centreline=offline["no_centreline"],
                offsets={r["crossing_id"]: r["offset_m"]
                         for r in offline["rows"]},
                producer="roads/route.py::find_crossings lines 741-746 vs "
                         "roads/network.py::build_segment lines 576, "
                         "677-687"),
            population=f"every crossing record in {corpus_name} that "
                       "declares a `centreline_straight_from_to`"))
    for r in span_rows:
        # THE PREMISE IS ITS OWN REFUSAL AND ITS OWN OWNER.  Reported before
        # the deck verdict, because "build a deck here" is the WRONG answer
        # to a crossing that is dry land: the stations belong to the ribbon,
        # and a deck built on this one would be buried 2.9 m in a hillside.
        p = r["premise"]
        t = r["termination"]
        if not p["pass"]:
            findings.append(Finding(
                check="crossings", ok=False,
                message=f"{r['status']} crossing {r['crossing']} "
                        f"({r['segment_id'] or '/'.join(r['joins'])}) IS NOT "
                        f"A CROSSING: its own bed stands above its own "
                        f"declared deck y={r['deck_datum_y']} on "
                        f"{p['bed_above_deck_samples']} of {p['samples']} "
                        f"metres, worst +{p['worst_bed_above_deck_m']:.3f} m, "
                        f"and only {p['wet_samples']} of {p['samples']} "
                        f"metres are under the {I.WATER_LEVEL:g} m water "
                        f"plane. Its record claims why_terrain_failed: "
                        f"water_body. These stations belong to "
                        f"{p['owner']}, not to a deck builder"
                        + (" - and the segment is BUILT, so this is road "
                           "that was never written"
                           if r["on_built_segment"] else "")
                        # WHETHER THIS REFUSAL CAN EVER BE CLOSED, ON THE
                        # REFUSAL.  The remedy it prescribes is a terrain
                        # write; the writer's own flatten guard is asked
                        # whether that write would be accepted, and a
                        # refusal that prescribes a forbidden write is an
                        # OPERATOR decision rather than a work item.
                        + (f". TERMINATION {t['termination']}: "
                           f"{t['stations_refused']} of {t['stations']} of "
                           f"the metres this repair must write are owned by "
                           f"{t['ground_owned_by']} at "
                           f"{t['ground_owner_distance_m']} m against a "
                           f"{t['repair_footprint_m']} m footprint, so "
                           f"ledger/writer.py REFUSES the only repair this "
                           f"refusal names - no work by this section's "
                           f"owner can close it"
                           if t["termination"] == "1A_FORBIDDEN" else
                           f". TERMINATION {t['termination']}: the write is "
                           f"permitted but lands inside "
                           f"{t['contested_by']}'s own recorded "
                           f"terrain_write within {t['contested_within_m']} "
                           f"m, so performing it destroys work somebody "
                           f"else did and NO GUARD REFUSES IT"
                           if t["termination"] == "1B_CONTESTED" else
                           f". TERMINATION {t['termination']}: {t['why']}"
                           if t["judged"] else ""),
                where=tuple(r["bank_a"]),
                measured=p["worst_bed_above_deck_m"], limit=p["limit_m"],
                numbers=dict(crossing=r["crossing"], segment=r["segment_id"],
                             samples=p["samples"],
                             wet_samples=p["wet_samples"],
                             bed_above_deck_samples=p[
                                 "bed_above_deck_samples"],
                             deck_datum_y=r["deck_datum_y"],
                             on_built_segment=r["on_built_segment"],
                             owner=p["owner"], **t)))
        if r["pass"]:
            continue
        # WHICH ARM OWNS THIS ROW.  One predicate, one place, so the rule
        # can be stated once and tested without a world: a row whose PREMISE
        # was refused, or that is a second declaration of a channel already
        # judged, is NOT judged for carriage.  See `carriage_is_judged` for
        # the measurement that makes this logic rather than tidiness.  The
        # suppressed rows are named in the report under
        # `carriage_not_judged_premise_refused` / `..._duplicate`.
        if not carriage_is_judged(r)["judged"]:
            continue
        # NOT ONE DEFECT, TWO.  "a bridge with a hole in it" and "a bridge
        # that was never built" read identically as `breaks`, and only the
        # second one is answered by placing a body.  `carried_by_a_piece`
        # already separates them; saying so in the message is what makes
        # the refusal actionable without reading the JSON.
        nobody = not r["carried_by_a_piece"]
        findings.append(Finding(
            check="crossings", ok=False,
            message=f"{r['status']} crossing {r['crossing']} "
                    f"({'/'.join(r['joins'])}"
                    + (", on a BUILT segment" if r["on_built_segment"]
                       else ", on a segment with no terrain write")
                    + ") does not carry a walker: "
                    + ("NO PIECE STANDS ON ANY OF ITS "
                       f"{r['samples']} METRES - the deck was declared at "
                       f"y={r['deck_datum_y']} and never built"
                       if nobody else
                       f"{r['breaks']} break(s) and {r['samples_unread']} "
                       f"unread metre(s) over a {r['span_m']} m span, "
                       f"{r['carried_by_a_piece']} of {r['samples']} metres "
                       "stand on a piece")
                    + (f"; first break at {r['first_break_at']}"
                       if r["first_break_at"] else ""),
            where=(tuple(r["first_break_at"]) if r["first_break_at"]
                   else tuple(r["bank_a"])),
            numbers=dict(breaks=r["breaks"], span_m=r["span_m"],
                         samples=r["samples"],
                         carried_by_a_piece=r["carried_by_a_piece"],
                         status=r["status"],
                         # THE CROSSING'S OWN NAME, ON ITS OWN REFUSAL.
                         # It was in the MESSAGE and nowhere else, so the
                         # only way to get from a connectivity row's
                         # `link_crossing` to the class this row was given
                         # was to parse English.  MEASURED: the boathouse
                         # and brgs1 connectivity rows both name
                         # link_crossing S1, and S1 is a `declared`
                         # crossing with segment_id null that appears in
                         # NO segment - so `spans` has no entry for it and
                         # the inheritance below could not find the row
                         # that had already classified it.
                         crossing=r["crossing"],
                         segment=r["segment_id"],
                         on_built_segment=r["on_built_segment"],
                         deck_datum_y=r["deck_datum_y"],
                         # THE REMEDY, AND THIS ONE IS SERVED.  A crossing
                         # whose PREMISE passed is real water, and the
                         # metres with nothing standing on them are closed
                         # by `assemble.bridge` / `bridge_body_place` -
                         # a tool that exists in this tree, needs no
                         # terrain write and so has no ownership question
                         # to ask.  That is the definition of ACTIONABLE,
                         # and it is why roads' ACTIONABLE is not zero:
                         # eight of these nine decks were declared and
                         # never built at all.
                         deck_metres_missing=r["samples"]
                         - r["carried_by_a_piece"],
                         remedy_is_a_deck=True)))
    for d in unreachable:
        bb = d.get("blocked_by") or {}
        where = tuple(d["walk_ends_at"]) if "walk_ends_at" in d else (
            tuple(bb["at"]) if "at" in bb else None)
        findings.append(Finding(
            check="connectivity", ok=False,
            message=f"{d['destination']} is NOT WALKABLE from {origin_name} "
                    f"via {d['segment']}"
                    + (f"; the walk ends after "
                       f"{d['walked_m_before_stopping']} m, "
                       f"{d['short_of_destination_m']} m short"
                       if "walk_ends_at" in d else "")
                    + f"; {d.get('repairs_needed', 0)} missing link(s), "
                      f"worst {d.get('category', '?')}, nearest is "
                      f"{bb.get('reason', '?')}"
                    + (f" at ({bb['at'][0]},{bb['at'][1]})"
                       if "at" in bb else "")
                    + (f", {bb['separation_m']} m"
                       if "separation_m" in bb else ""),
            where=where,
            numbers=dict(component_stations=d["component_stations"],
                         category=d.get("category"),
                         first_link=bb.get("reason"),
                         repairs_needed=d.get("repairs_needed"),
                         segment=d.get("segment"),
                         # THE FIRST LINK'S OWN MEASUREMENTS, ON THE ROW
                         # THAT IS CLASSIFIED BY THEM.  The remedy for an
                         # unreachable destination is the remedy for the
                         # link nearest the walker, and until here that
                         # link's beds, its separation, the crossing it
                         # names and the body standing across it lived only
                         # in the severance report - so the refusal could
                         # not be classified without re-deriving them from
                         # a coordinate.
                         link_at=bb.get("at"),
                         link_to=bb.get("to"),
                         link_bed_a=bb.get("bed_a"),
                         link_bed_b=bb.get("bed_b"),
                         link_separation_m=bb.get("separation_m"),
                         link_crossing=bb.get("crossing"),
                         link_over_water=bb.get("over_water"),
                         link_frozen_by=bb.get("ground_frozen_by"),
                         # THE SUB-EDGE THE WALK REFUSED, AND IT IS THE
                         # ONLY THING THE OWNERSHIP QUESTION MAY BE KEYED
                         # ON.  A diagonal link is two 1 m mesh edges
                         # through a corner; `link_step_at` names the one
                         # that failed and `link_step_ground_a_m` /
                         # `link_step_ground_b_m` say WHICH OF ITS TWO
                         # ENDS IS HIGH, which is the end a cut-only
                         # repair lowers.  `link_bed_a`/`link_bed_b` above
                         # are the LINK'S endpoints and are a DIFFERENT
                         # pair on every diagonal.  None on all five means
                         # the walk published no worst transition for this
                         # link - a junction found by the proximity scan
                         # rather than by the walk - and `road_termination`
                         # then says in `repair_keyed_on` that it fell
                         # back, rather than pretending it had an edge.
                         link_step_m=bb.get("step_m"),
                         link_step_limit_m=bb.get("limit_m"),
                         link_step_at=bb.get("step_at"),
                         link_step_ground_a_m=bb.get("step_ground_a_m"),
                         link_step_ground_b_m=bb.get("step_ground_b_m"),
                         link_step_lattice_m=bb.get("step_lattice_m"),
                         # AND WHETHER A PIECE CARRIES A WALKER OVER THIS
                         # LINK TODAY, off the walk that measured it.  35
                         # of this world's 44 ground-only over-limit pairs
                         # ARE carried, and "may this deck be removed" is a
                         # different decision from "the walk stops here".
                         carried_by_a_surface=carried_by.get(
                             tuple((bb.get("at") or [None, None])
                                   + (bb.get("to") or [None, None]))),
                         **(dict(obstruction_bodies=bb.get("bodies"),
                                 obstruction_pieces=bb.get("pieces"),
                                 obstruction_gap_m=bb.get("widest_gap_m"),
                                 obstruction_blocks=True,
                                 walker_width_m=bb.get("needs_m"),
                                 blocker_pieces=bb.get("blocker_pieces"))
                            if bb.get("reason") == "blocked" else {}),
                         **({"stopped_by_step_m": d["stopped_by"]["step_m"]}
                            if "stopped_by" in d else {}))))

    # THE POPULATION THIS SECTION'S VERDICT IS OVER, AS A PASSING FINDING.
    # The standing rule is that no gate may report a verdict over a
    # population it did not print the size of - and roads' only
    # `population` finding was a REFUSAL ("no road segment was ever written
    # to this world"), so a section that PASSED said nothing at all and
    # counted as silently compliant.  MEASURED by `__main__`'s own
    # advisory: 4 of 11 sections report one, and roads was listed among the
    # silent on every run in which it had roads to judge.
    findings.append(Finding(
        check="population", ok=True,
        message=f"{len(segs)} built segment(s), {len(pts)} stations at 1 m "
                f"resolving to {report['traversability']['vertices_walked']} "
                f"lattice vertices, "
                f"{report['traversability']['mesh_edges_judged']} mesh edges "
                f"judged, {len(built)} non-vegetation objects within "
                f"{I.CORRIDOR_MASK_M:g} m of a centreline, "
                f"{report['crossings']['distinct_channels']} distinct water "
                f"channels, {len(dests)} declared destinations",
        measured=float(len(pts)), limit=float(len(pts)),
        numbers=dict(
            segments=len(segs), stations=len(pts),
            vertices=report["traversability"]["vertices_walked"],
            mesh_edges=report["traversability"]["mesh_edges_judged"],
            corridor_objects=len(built),
            crossings_declared=report["crossings"]["declared"],
            distinct_channels=report["crossings"]["distinct_channels"],
            destinations=len(dests)),
        population="every road segment this ledger records a terrain write "
                   "for, every station on it at 1 m, and every non-"
                   "vegetation object inside the corridor mask"))

    # EVERY REFUSAL THIS SECTION MADE, CLASSIFIED - after the last arm, so
    # no arm can be classified and forgotten.  See `road_termination`.
    term = road_termination(findings, x_owners, x_writes,
                            spans={r["crossing"]: r for r in span_rows},
                            deck_remedy=x_deck,
                            placements=T.Placements.read(ctx.ledger))
    findings.extend(term)
    report["termination"] = dict(
        instrument="settlements.termination_class over each refusal's own "
                   "prescribed remedy, with the flatten=FORBIDDEN owner set "
                   "and the recorded terrain_write footprints read from "
                   "this run's own ledger, and - for a refusal whose remedy "
                   "is a DECK - roads/boardwalk.py::deck_remedy, the "
                   "builder's own verdict on whether it would lay one",
        deck_remedy_read=x_deck is not None,
        deck_remedy_refused=(None if x_deck is None else
                             sorted(c for c, a in x_deck.items()
                                    if a["buildable"] is False)),
        population="every hard refusal this section produced",
        **term[0].numbers)

    t, c, o, v = (report["traversability"], report["connectivity"],
                  report["obstruction"], report["coverage"])
    passed = all(x["pass"] for x in (t, c, o, v))

    def render(w):
        w(f"  population: {len(segs)} segments, {len(pts)} stations resolving "
          f"to {t['vertices_walked']} lattice vertices "
          f"({t['stations_collapsed_into_a_shared_vertex']} stations shared a "
          f"vertex with their neighbour), {len(built)} non-vegetation objects "
          f"within {I.CORRIDOR_MASK_M:g} m of a centreline\n")
        w(f"  TRAVERSABILITY {'PASS' if t['pass'] else 'FAIL'}  "
          f"{t['over_limit_locations']} over-limit steps a walker cannot take "
          f"({t['over_limit_in_scope']} in scope); the ROAD SURFACE alone is "
          f"over the limit at {t['ground_over_limit_pairs']} adjacent pairs in "
          f"{t['ground_over_limit_locations']} distinct locations "
          f"(the difference is ground a deck carries the walker over); "
          f"{t['mesh_edges_judged']} mesh edges judged at "
          f"{t['limit_m_per_m']} m each, {t['diagonal_moves']} of the moves "
          f"diagonal (two edges through a corner); "
          f"{t['unresolved_transitions']} transitions UNRESOLVED; "
          f"{t['ambiguous_vertices']} vertices answered twice; "
          f"{t['unreadable_stations']} stations unread\n")
        for r in t["worst"][:8]:
            w(f"      {r['segment']:26s} ({r['x']:8.1f},{r['z']:8.1f}) "
              f"step {r['step_m']:6.3f} m across {r['lattice_m']:.3f} m\n")
        w(f"  CONNECTIVITY   {'PASS' if c['pass'] else 'FAIL'}  "
          f"{c['reachable']}/{c['total']} destinations WALKABLE from "
          f"{origin_name} ({c['stations_reached']} of "
          f"{t['vertices_walked']} vertices "
          f"reached, {c['junction_steps']} junction steps); the network falls "
          f"into {c['components']} walkable components, the largest holding "
          f"{c['largest_component_stations']} vertices "
          f"({100*c['largest_component_share']:.0f} %), the portal hall's own "
          f"{c['origin_component_stations']}\n")
        sv = c["severance"]
        w(f"      {sv['population']}; "
          + ", ".join(f"{n} {r}" for r, n in sv["links_by_reason"].items())
          + "\n")
        for cat in (CAT_REGRADE, CAT_STRUCTURE, CAT_OPERATOR,
                    CAT_UNMEASURED):
            names = sv["destinations_by_category"][cat]
            w(f"      {cat:14s} {len(names):3d} destination(s)"
              + (f": {', '.join(names)}" if names else "") + "\n")
        for d in sorted((x for x in c["destinations"] if not x["reachable"]),
                        key=lambda x: (x.get("category", ""),
                                       x.get("repairs_needed", 99))):
            tail = ""
            if "walk_ends_at" in d:
                tail = (f" - walk ends at ({d['walk_ends_at'][0]},"
                        f"{d['walk_ends_at'][1]}) after "
                        f"{d['walked_m_before_stopping']} m, "
                        f"{d['short_of_destination_m']} m short")
                if "stopped_by" in d:
                    tail += f", stopped by a {d['stopped_by']['step_m']} m step"
            bb = d.get("blocked_by") or {}
            w(f"      UNREACHABLE {d['destination']:14s} via "
              f"{d['segment']:26s} {d.get('category', '?'):14s} "
              f"{d.get('repairs_needed', 0):2d} link(s), nearest "
              f"{bb.get('reason', '?')}"
              + (f" at ({bb['at'][0]},{bb['at'][1]}) "
                 f"{bb.get('separation_m', 0)} m" if "at" in bb else "")
              + f"{tail}\n")
        w(f"  OBSTRUCTION    {'PASS' if o['pass'] else 'FAIL'}  "
          f"{o['must_climb_pieces']} pieces in {o['must_climb_bodies']} bodies a "
          f"walker MUST CLIMB ({o['must_climb_bodies_in_scope']} bodies in "
          f"scope); of those, {o['blocked_stations']} stations leave no way "
          f"ROUND ({o['blocking_bodies']} bodies, {o['blocking_pieces']} "
          f"pieces)\n")
        for r in o["worst"][:8]:
            w(f"      {r['segment']:26s} ({r['x']:8.1f},{r['z']:8.1f}) "
              f"widest gap {r['widest_gap_m']:5.2f} m < {r['needs_m']:.2f} m  "
              f"{','.join(r['prefabs'][:3])}\n")
        for b in o["bodies"][:8]:
            w(f"      body {b['pieces_in_road']:4d} of {b['pieces_in_body']:4d} "
              f"pieces in the road at "
              f"({b['at'][0]:8.1f},{b['at'][1]:8.1f}) "
              f"{'go round' if b['must_go_round'] else 'BLOCKS'}  "
              f"{b['top_prefabs'][0][0]}\n")
        adv = o["advisory_natural"]
        w(f"      advisory, mesh-collider props (footprint known only to its "
          f"bounding box): {adv['blocked_stations']} further stations\n")
        w(f"  COVERAGE       {'PASS' if v['pass'] else 'FAIL'}  "
          f"{sum(v['unmeasured_in_scope'].values())} objects in scope whose "
          f"prefab the geometry corpus has never measured\n")
        if v["unmeasured_in_scope"]:
            w(f"      {v['unmeasured_in_scope']}\n"
              f"      remedy: {v['remedy']}\n")
        # AND THE CROSSINGS, WHICH THIS REPORT NEVER PRINTED AT ALL.  The
        # rows were in the JSON and the refusals were in the finding list,
        # and the one line a builder reads did not mention them - so "0 of
        # 13 span metres standing on a piece" was only ever visible to
        # somebody who went looking for it.
        x = report["crossings"]
        # THE HEADLINE IS COUNTED IN CHANNELS, NOT IN RECORDS.  "3 of 17
        # declared crossings carry a walker" was true of the FILE and false
        # of the WORLD: two of those three passes were one built deck read
        # twice, because the per-segment detector declared one channel under
        # two segments' names.  Records stay on the line - the corpus defect
        # must not become invisible the moment it is divided out.
        w(f"  CROSSINGS      {'PASS' if x['pass'] else 'FAIL'}  "
          f"{x['carry_a_walker_channels']} of {x['distinct_channels']} "
          f"DISTINCT CHANNELS carry a walker "
          f"({x['carry_a_walker_records']} of {x['declared']} records: "
          f"{x['declared_in_file']['declared']} declared + "
          f"{x['declared_in_file']['detected']} detected in "
          f"{x['corpus']}, "
          f"{len(x['duplicate_declarations'])} of them "
          f"REDUNDANT, {x['on_built_segments']} on segments this ledger has "
          f"built)\n")
        if x["duplicate_declarations"]:
            # NAMED `chan`, NOT `c`.  `c` is this closure's CONNECTIVITY
            # report, read 80 lines above; binding it here made `c` a LOCAL
            # of `render` for the whole function by Python's static scoping
            # rule, so the connectivity line raised UnboundLocalError on
            # EVERY world - whether or not this branch ever ran.  MEASURED:
            # the section renders through to the crossings block and dies
            # there, and the gate's own report for the largest section in
            # the tree was a traceback.
            chan = x["channels"]
            w(f"      {len(x['duplicate_declarations'])} DUPLICATE "
              f"DECLARATION(S) of a channel already declared - "
              f"{chan['declared_m']} m of declared span is "
              f"{chan['distinct_m']} m of channel: "
              + "; ".join(f"{p['a']}={p['b']} ({p['fraction']:.3f})"
                          for p in chan["pairs_over_threshold"]) + "\n")
        if x["carriage_not_judged_premise_refused"]:
            w(f"      {len(x['carriage_not_judged_premise_refused'])} "
              f"crossing(s) NOT JUDGED FOR CARRIAGE because their PREMISE "
              f"was refused - the gate may not rule that no deck can stand "
              f"here and fail the deck for not carrying: "
              f"{', '.join(x['carriage_not_judged_premise_refused'])}\n")
        if x["no_deck_on_a_built_segment"]:
            w(f"      {len(x['no_deck_on_a_built_segment'])} crossing(s) on "
              f"a BUILT road have NO PIECE on any metre - "
              f"{x['undecked_m_on_built_segments']} m of declared deck that "
              f"was never built: "
              f"{', '.join(x['no_deck_on_a_built_segment'])}\n")
        if x["premise_refused"]:
            w(f"      {len(x['premise_refused'])} of {x['premise_judged']} "
              f"judged crossing(s) ARE NOT CROSSING ANYTHING - the bed "
              f"stands above the declared deck, so these stations belong to "
              f"the RIBBON and no deck can be built there: "
              f"{', '.join(x['premise_refused'])}\n")
            # AND WHETHER ANY OF THEM IS WORK.  "N refusals" is a work queue
            # and an operator decision list added together unless each one
            # says which it is.  ACTIONABLE is the only number that answers
            # the question the factory actually has.
            bt = x["premise_refusals_by_termination"]
            # AND THE LOOP VARIABLE IS `klass`/`rows`, NOT `k`/`v`.  `v` is
            # this closure's COVERAGE report, read 65 lines above; a plain
            # `for k, v in ...` here binds it as a LOCAL of `render` for the
            # whole function, and the coverage line then raises
            # UnboundLocalError on every world whether or not this branch
            # runs.  The same latent fault as `c` above, twice in one
            # function.
            w("      TERMINATION  "
              + " · ".join(f"{klass} {len(rows)}"
                           for klass, rows in bt.items())
              + "\n")
            for klass, rows in bt.items():
                if rows and klass != "ACTIONABLE":
                    w(f"        {klass}: {', '.join(rows)}\n")
        for r in sorted(x["spans"], key=lambda r: (r["pass"], -r["span_m"])):
            if r["pass"]:
                continue
            p = r["premise"]
            w(f"      {r['status'][:8]:8s} {r['crossing']:26s} "
              f"{r['span_m']:6.1f} m  "
              f"{r['carried_by_a_piece']:3d}/{r['samples']:3d} metres on a "
              f"piece, {r['breaks']:3d} break(s)  deck y={r['deck_datum_y']}"
              f"  {p.get('wet_samples', '?')}/{p['samples']} wet"
              + (f"  BED ABOVE DECK on {p['bed_above_deck_samples']}"
                 f"/{p['samples']}, worst "
                 f"+{p['worst_bed_above_deck_m']:.3f} m"
                 if not p["pass"] else "")
              + (f"  DUPLICATE OF {r['duplicate_of']}"
                 if r["duplicate_of"] else "")
              + f"  {'BUILT road' if r['on_built_segment'] else 'no road'}\n")
        # AND THE ONE LINE THE OPERATOR ACTUALLY TRIAGES FROM.  "87
        # refusals" is a work queue and a decision list added together; the
        # split by class and by ARM is what says which of the four owners
        # each row belongs to, and it goes last so it is the last thing
        # read.
        z = report["termination"]
        w(f"\n  TERMINATION  {z['refusals']} refusal(s), "
          f"{z['actionable']} ACTIONABLE"
          + (f"  (owner set {'READ' if z['owners_read'] else 'UNREAD'}, "
             f"write footprints "
             f"{'READ' if z['writes_read'] else 'UNREAD'})") + "\n")
        w("      by class  "
          + " · ".join(f"{k} {v}" for k, v in z["by_class"].items()) + "\n")
        for chk, split in z["by_check"].items():
            w(f"      {chk:16s}"
              + " · ".join(f"{k} {v}" for k, v in split.items()) + "\n")

    return SectionResult(findings=findings, report=report, render=render,
                         passed=passed)


# ---------------------------------------------------------------------------
# THE CASES - every check proved in BOTH DIRECTIONS, no world, no server
# ---------------------------------------------------------------------------
# A check shown only to pass is not a check.  Each case is a STATE and a
# verdict the check is REQUIRED to reach on it.  Several of the accepting
# cases are the OLD BLIND CHECK being shown to accept the known-bad state:
# that is the record of why the defect shipped and it belongs in the gate.
#
#   BASELINE   a 6 m cliff inside 1 m averaged over an 8 m baseline is a
#              gradient of 0.75, under the 0.7813 limit.  That is exactly how
#              "worst 8 m gradient 0.0801" was reported over an 8.93 m step.
#   DECK       a deck at its own datum over ground 3 m below is not a defect.
#              The span is accepted WITHOUT any bridge being named, and a
#              hole in the ground with no deck over it is still refused.
#   WIDTH      "there is something in the road" and "a walker cannot get
#              past" are different claims; this gate makes the second.
#   COVERAGE   an object whose prefab was never measured is a FAILING check,
#              not a harmless one.
_FLOOR = [["b", -1.0, -0.03, -1.0, 1.0, 0.10, 1.0]]
_SHORT_WALL = [["b", -2.0, 0.0, -0.2, 2.0, 2.0, 0.2]]
_FULL_WALL = [["b", -3.0, 0.0, -0.2, 3.0, 2.0, 0.2]]
_KERB = [["b", -3.0, 0.0, -0.2, 3.0, 0.5, 0.2]]
_LAMP = [["b", -0.13, -0.65, -0.13, 0.12, 0.82, 0.12]]
_CLIFF = [0.0] * 8 + [6.0] * 8            # a 6 m step at the middle metre


def _f(check, ok, msg, **kw):
    return Finding(check=check, ok=ok, message=msg, **kw)


def _case_eight_metre_baseline():
    """THE OLD CHECK, reproduced so the bug stays named: an 8 m baseline
    averages a 6 m cliff down to 0.75 m/m and accepts it."""
    grad = (_CLIFF[-1] - _CLIFF[0]) / (len(_CLIFF) - 1)
    return [_f("traversability_8m_baseline", grad <= STEP_LIMIT_PER_M,
               f"8 m baseline reads {grad:.4f} m/m over a 6.000 m cliff",
               measured=round(grad, 4), limit=round(STEP_LIMIT_PER_M, 4))]


def _case_one_metre_baseline():
    worst = max(abs(_CLIFF[i + 1] - _CLIFF[i]) for i in range(len(_CLIFF) - 1))
    return [_f("traversability", worst <= STEP_LIMIT_PER_M,
               f"adjacent 1 m samples read {worst:.3f} m",
               measured=worst, limit=round(STEP_LIMIT_PER_M, 4))]


def _case_just_under_limit():
    step = 0.78
    return [_f("traversability", step <= STEP_LIMIT_PER_M,
               f"a {step} m step is inside the slide angle",
               measured=step, limit=round(STEP_LIMIT_PER_M, 4))]


def _deck_surfaces(with_deck: bool):
    deck = [I.mk_solid("wood_floor", (float(x), 10.0, 0.0), I.yaw(0), _FLOOR)
            for x in range(-3, 4)]
    g = I.Grid(deck if with_deck else [])
    beds = {x: (10.0 if abs(x) > 3 else 7.0) for x in range(-6, 7)}
    return {x: I.surfaces_at(float(x), 0.0, beds[x], g, 0.49) for x in beds}


def _case_deck_spans_gap():
    s = _deck_surfaces(True)
    bad = [x for x in range(-6, 6) if not edge_ok(s[x], s[x + 1], 1.0)]
    return [_f("traversability", not bad,
               f"a deck over a 3 m gap leaves {len(bad)} unwalkable pairs",
               numbers=dict(unwalkable_pairs=len(bad)))]


def _case_hole_with_no_deck():
    s = _deck_surfaces(False)
    bad = [x for x in range(-6, 6) if not edge_ok(s[x], s[x + 1], 1.0)]
    return [_f("traversability", not bad,
               f"the same 3 m gap with no deck leaves {len(bad)} unwalkable "
               "pairs", numbers=dict(unwalkable_pairs=len(bad)),
               where=(float(bad[0]), 0.0) if bad else None)]


# --- THE CROSSING CASES ----------------------------------------------------
# THE STATE THESE PIN is the one this section shipped with: a DECLARED
# crossing joins two segments across water, carries no terrain write, and so
# contributed NO node to the connectivity graph.  Its two banks sat 37.6 m
# apart against a 3.0 m junction threshold and everything beyond was
# "unreachable".  Case (c) below is that old behaviour, kept permanently and
# asserted to REFUSE, so the fix cannot be undone quietly; (a) and (b) are
# the two directions of the new reading.
def _dry(surfaces, water_y=WATER_Y):
    """A surface a walker can stand on ACROSS A STRAIT.

    THE SEA FLOOR IS NOT A BRIDGE.  `surfaces_at` returns the bed plus every
    level piece top, and over a declared crossing the bed is continuous --
    it is the channel's own floor.  Judged with the bed in the set, a strait
    with NO deck at all reads ZERO breaks, because `min_step` correctly
    observes that 27.5 m of sea floor is level with 27.5 m of sea floor.
    MEASURED as a fixture failure before it was a rule: the no-deck case
    scored 0 breaks and "far bank reachable" until the bed was dropped.
    A declared crossing exists precisely BECAUSE the ground there is under
    water (`why_terrain_failed: water_body`), so on these samples the only
    walking surfaces are the ones at or above the water plane.
    """
    return [t for t in surfaces if t[0] >= water_y - 1e-6]


def _span_walk(deck_xs):
    """A 12 m strait: bank at x=-1 and x=13 at y 30.6, water bed 27.5
    between, `deck_xs` carrying wood_floor at 30.55 (top 30.6).  Returns
    (breaks, far_bank_reachable) judged with the section's own `edge_ok`."""
    deck = [I.mk_solid("wood_floor", (float(x), 30.55, 0.0), I.yaw(0), _FLOOR)
            for x in deck_xs]
    g = I.Grid(deck)
    xs = list(range(-1, 14))
    beds = {x: (30.6 if x in (-1, 13) else 27.5) for x in xs}
    surf = {x: _dry(I.surfaces_at(float(x), 0.0, beds[x], g, 0.49))
            for x in xs}
    adj = defaultdict(list)
    breaks = 0
    for x in xs[:-1]:
        if edge_ok(surf[x], surf[x + 1], 1.0):
            adj[x].append(x + 1)
            adj[x + 1].append(x)
        else:
            breaks += 1
    seen, stack = {-1}, [-1]
    while stack:
        for n in adj[stack.pop()]:
            if n not in seen:
                seen.add(n)
                stack.append(n)
    return breaks, 13 in seen


def _case_crossing_with_a_hole_is_refused():
    # THE HOLE HAS TO BE WIDER THAN THE PIECE.  `wood_floor` is 2 m square,
    # so a floor centred at x covers x-1..x+1: dropping only x=5..8 leaves
    # the neighbours' overhang bridging it and the case reads 0 breaks,
    # which is a fixture bug that would have shipped as a passing refusal
    # case.  Drop 4..9 and the uncovered metres are genuinely uncovered.
    breaks, far = _span_walk([x for x in range(0, 13) if not 4 <= x <= 9])
    return [_f("crossings", breaks == 0,
               f"a declared 12 m crossing with a 4 m hole in its deck: "
               f"{breaks} break(s), far bank reachable={far}",
               numbers=dict(breaks=breaks), where=(5.0, 0.0))]


def _case_crossing_with_a_deck_is_accepted():
    breaks, far = _span_walk(list(range(0, 13)))
    return [_f("crossings", breaks == 0 and far,
               f"the same crossing fully decked: {breaks} break(s), far "
               f"bank reachable={far}", numbers=dict(breaks=breaks))]


def _case_crossing_with_no_deck_is_refused():
    breaks, far = _span_walk([])
    return [_f("crossings", breaks == 0,
               "a declared crossing whose deck was never built: "
               f"{breaks} break(s), far bank reachable={far}. THIS IS THE "
               "OLD BEHAVIOUR, pinned: before declared crossings were in "
               "the population this span produced no node, no break and no "
               "finding, and scored identically to a perfect bridge",
               numbers=dict(breaks=breaks), where=(0.0, 0.0))]


# --- THE POPULATION THE CROSSING CHECK WAS DRAWN FROM ----------------------
# THE THREE CASES ABOVE ALL JUDGE A SPAN THAT IS IN THE POPULATION.  None of
# them can fire for a span that was never selected, and for 14 of this
# world's 16 crossings that is exactly what happened: `crossing_spans` read
# `crossings.yaml:declared` and nothing else, so `detected` - the router's
# own 14, written into the same file under the same schema, six of them on
# segments the ledger has BUILT - produced no samples, no nodes, no breaks
# and no findings.  `declared: 2` went into the report with no denominator
# beside it and read as a world with two crossings in it.
#
# The fixture CONSTRUCTS the file rather than borrowing this world's: a case
# that greps the real crossings.yaml would go quiet the day somebody moves a
# key, which is the failure mode `Case.neuter` exists for.
# AND THE FIXTURE DECLARES A SCOPE, because the corpus it stands for does.
# The names are CONSTRUCTED and are neither `Ulfsland` nor `Pirate68`, so a
# guard that has quietly gone back to matching this build's defaults cannot
# pass here by coincidence - the same rule this file's coordinates follow.
_FIXTURE_WORLD = "Kelpmoor"
_FIXTURE_SEED = "Brine19"
_CROSSINGS_FIXTURE = {
    "seed": _FIXTURE_SEED,
    "world": _FIXTURE_WORLD,
    "precedence": "DECLARED wins over DETECTED for the same channel",
    "declared": [dict(
        crossing_id="S1", joins=["spawn", "west"],
        bank_a=dict(xz=[-312.0, -64.0], road_deck_y=30.6),
        bank_b=dict(xz=[-324.0, -64.0], road_deck_y=30.6))],
    "detected": [
        dict(crossing_id="S1", segment_id="SHOULD-BE-SUPERSEDED",
             bank_a=dict(xz=[0.0, 0.0], road_deck_y=99.0),
             bank_b=dict(xz=[1.0, 0.0], road_deck_y=99.0)),
        dict(crossing_id="S12-brgs1-boathouse-x01",
             segment_id="S12-brgs1-boathouse", required_deck_y=33.87,
             bank_a=dict(xz=[-312.0, -62.0], road_deck_y=33.87),
             bank_b=dict(xz=[-284.0, -138.0], road_deck_y=33.87))],
}


class _FixtureCtx:
    """Just enough ctx for `crossing_spans`: a log, a root, AND `args`.

    `args` is not decoration.  `instruments.run_scope` reads `ctx.args.world`
    OUTSIDE the guard precisely so a ctx that cannot answer it CRASHES the
    section, and a fixture that did not carry one would be testing a path no
    gate run takes.
    """

    def __init__(self, root, *, seed=_FIXTURE_SEED, world=_FIXTURE_WORLD):
        self.root = root
        self.args = SimpleNamespace(seed=seed, world=world)
        self.lines: list[str] = []

    def log(self, msg):
        self.lines.append(msg)


def _spans_over_fixture(doc=_CROSSINGS_FIXTURE, *, seed=_FIXTURE_SEED,
                        world=_FIXTURE_WORLD, write=True, also=None):
    """`crossing_spans` over a CONSTRUCTED corpus, in a scratch tree.

    `doc=None` with `write=False` leaves the file absent; `write=False` with
    a `doc` writes bytes that are not YAML, so all four states this read can
    be in - present-and-scoped, absent, unreadable, foreign - are reachable
    from one fixture.

    `doc` is written at the name the PRODUCER'S resolver gives THIS RUN'S
    seed - `crossings.<seed>.yaml`, or the bare name on the default seed -
    because that is the only file `roads/network.py::emit` ever writes for
    it.  `also` maps further file names under `roads/` to documents, for the
    state where the default seed's corpus stands BESIDE this seed's own.
    """
    import tempfile                                      # noqa: PLC0415
    import yaml                                          # noqa: PLC0415
    from pathlib import Path as _P                       # noqa: PLC0415
    with tempfile.TemporaryDirectory() as td:
        (_P(td) / "roads").mkdir()
        was = I.JUMPSTART
        I.JUMPSTART = _P(td)
        try:
            for name, other in (also or {}).items():
                (_P(td) / "roads" / name).write_text(yaml.safe_dump(other))
            if doc is not None:
                name = I._worldseed().crossings_path(seed).name
                (_P(td) / "roads" / name).write_text(
                    yaml.safe_dump(doc) if write else "[unparseable: {")
            return crossing_spans(_FixtureCtx(_P(td), seed=seed, world=world))
        finally:
            I.JUMPSTART = was


def _case_a_detected_crossing_is_in_the_population():
    rows, _ = _spans_over_fixture()
    ids = sorted(r["id"] for r in rows)
    got = "S12-brgs1-boathouse-x01" in ids
    return [_f("crossings", not got,
               f"a crossing recorded under `detected` on a BUILT segment is "
               f"in the population: {len(rows)} of 2 distinct crossing_ids "
               f"selected, ids={ids}. THE OLD READER TOOK `declared` ALONE "
               f"and selected 1 of 2 here, which on Ulfsland was 2 of 16 - "
               f"347.5 m of declared deck on six BUILT roads that no check "
               f"could refuse because no check was looking at it",
               numbers=dict(selected=len(rows), distinct_ids=2,
                            detected_selected=int(got)))]


def _case_declared_supersedes_detected_for_one_channel():
    rows = {r["id"]: r for r in _spans_over_fixture()[0]}
    s1 = rows.get("S1")
    ok = (s1 is not None and s1["status"] == "declared"
          and s1["deck_y"] == 30.6 and s1["segment_id"] is None)
    return [_f("crossings", not ok,
               "the same channel recorded twice resolves to Crossings' own "
               "DECLARED row, per crossings.yaml's own `precedence`: S1 "
               f"status={None if s1 is None else s1['status']}, deck_y="
               f"{None if s1 is None else s1['deck_y']} (the detected "
               "duplicate declares 99.0 and must not win)",
               numbers=dict(status=None if s1 is None else s1["status"],
                            deck_y=None if s1 is None else s1["deck_y"]))]


def _neuter_declared_only():
    """Put the OLD READER back: `declared` and nothing else."""
    was = globals()["crossing_spans"]

    def blind(ctx):
        rows, refusals = was(ctx)
        return [r for r in rows if r["status"] == "declared"], refusals

    globals()["crossing_spans"] = blind

    def undo():
        globals()["crossing_spans"] = was
    return undo


# --- THE CORPUS THIS SECTION JUDGES IS ABOUT THIS RUN'S WORLD --------------
# THE EIGHTH INSTANCE OF THE CLASS, AND THE FIRST ON THE GATE SIDE.  A key
# that names a SUPERSET or a SUBSET of what the quantity actually depends on:
# `roads/crossings.yaml` and `settlements/sites.yaml` are keyed on THE
# REPOSITORY PATH, and what they describe depends on THE WORLD.  MEASURED
# 2026-09-23 across all eleven sections: three of them - roads (twice),
# annulus and townsites - opened one of those two files with no scope check
# whatsoever, and both files declare `world: Ulfsland / seed: Pirate68` in
# their own headers.  The guard is `instruments.read_scoped_corpus`, ONE
# function for all four reads, delegating every comparison to
# `worldseed.require_scope` - the same refusal `roads/spec.py` and
# `roads/structures.py::load_segment_doc` have used on the planner side all
# along.
#
# THE CASES BELOW ARE THE GUARD'S, NOT THIS SECTION'S, and they run it
# through this section's own reader over a CONSTRUCTED corpus, so they hold
# the guard in place for `annulus` and `townsites` as well.
def _guard_refusals(**kw) -> list:
    """The refusals `crossing_spans` returns for one fixture state."""
    return _spans_over_fixture(**kw)[1]


def _hard(fs) -> list:
    return [f for f in fs if not f.ok and not f.advisory]


def _case_a_corpus_about_another_world_is_refused():
    """THE DEFECT, RESTORED AS A STATE: one world's corpus, another world's
    run.

    THE SEED MATCHES ON PURPOSE.  Two saves can be built from one seed, and
    `settlements/sites.yaml` records where THIS world's pads were dug - so
    the WORLD alone is varied here and the case isolates the one dependency
    `require_scope`'s own seed-scoped tolerance would have dropped.  The
    seed direction and both run-side directions are counted separately by
    `_case_every_declared_scope_dependency_is_load_bearing`.
    """
    foreign = dict(_CROSSINGS_FIXTURE, world="Ulfsland")
    rows, refusals = _spans_over_fixture(doc=foreign)
    hard = _hard(refusals)
    return [_f("crossings", not hard,
               f"a corpus declaring world='Ulfsland' read by a run asked "
               f"about world={_FIXTURE_WORLD!r} (same seed "
               f"{_FIXTURE_SEED!r}) is REFUSED and yields NO population: "
               f"{len(hard)} refusal(s), {len(rows)} crossing(s) adopted. "
               f"UNGUARDED THIS READ SUCCEEDED - and townsites, on the same "
               f"kind of unguarded corpus, reported PASS about the world it "
               f"was not asked about",
               numbers=dict(refusals=len(hard), adopted=len(rows),
                            declared_world="Ulfsland",
                            run_world=_FIXTURE_WORLD))]


def _case_a_corpus_declaring_nothing_is_refused():
    """UNREAD IS NOT CLEAR.  A header-less corpus is not a corpus that
    matches: "it did not say" is exactly how one world's numbers walk into
    another's verdict, and both of these files gained their headers late."""
    bare = {k: v for k, v in _CROSSINGS_FIXTURE.items()
            if k not in ("seed", "world")}
    refusals = _hard(_guard_refusals(doc=bare))
    return [_f("crossings", not refusals,
               f"a corpus carrying NO seed and NO world declaration is "
               f"refused, not tolerated: {len(refusals)} refusal(s) over a "
               f"document holding "
               f"{len(bare.get('detected', [])) + len(bare.get('declared', []))}"
               f" crossing records",
               numbers=dict(refusals=len(refusals), declared_seed=None,
                            declared_world=None))]


def _case_an_unreadable_corpus_is_refused():
    """A SECTION THAT CANNOT READ ITS CORPUS REFUSES.  Until 2026-09-23 this
    read logged the exception and returned `[]`, so a corrupt file scored
    identically to a world with no crossings in it."""
    refusals = _hard(_guard_refusals(write=False))
    return [_f("crossings", not refusals,
               f"an UNREADABLE corpus is a refusal, not an empty population: "
               f"{len(refusals)} refusal(s). The shipped reader logged "
               f"`crossings.yaml unreadable (...)` and returned no rows, and "
               f"every crossing check downstream then passed vacuously",
               numbers=dict(refusals=len(refusals), readable=False))]


def _case_a_corpus_about_this_world_is_accepted():
    """THE SURVIVOR, AND IT IS A VERDICT.

    A guard that refuses everything scores perfectly against the three cases
    above and blocks every world that is fine.  This arm reads a corpus that
    DOES declare this run's pair and requires the whole population through:
    2 distinct crossing_ids, no refusal.
    """
    rows, refusals = _spans_over_fixture()
    hard = _hard(refusals)
    ok = not hard and len(rows) == 2
    return [_f("crossings", ok,
               f"a corpus declaring world={_FIXTURE_WORLD!r} "
               f"seed={_FIXTURE_SEED!r}, read by a run asked about exactly "
               f"that pair, passes the guard WHOLE: {len(rows)} of 2 "
               f"distinct crossing_ids adopted, {len(hard)} refusal(s)",
               numbers=dict(adopted=len(rows), distinct_ids=2,
                            refusals=len(hard)))]


def _case_a_fresh_seeds_own_crossings_corpus_is_the_one_read():
    """THE DEFAULT SEED'S CORPUS STANDS BESIDE THIS SEED'S OWN, AND THE GATE
    MUST READ THIS SEED'S.

    The state is the one every fresh world is in: `roads/crossings.yaml`
    declares the default pair (Ulfsland / Pirate68), and the planner has
    published this seed's own `crossings.<seed>.yaml` next to it.  A run on
    this seed must adopt its own population whole - 2 distinct crossing_ids,
    no refusal.  The shipped reader opened the bare name, refused it as
    another world's and judged NO crossing at all, so S4-meadhall-southeast
    -x01's 26 m of declared deck was walked as road ground and refused four
    times as ACTIONABLE terrain cuts.
    """
    default = dict(_CROSSINGS_FIXTURE, seed="Pirate68", world="Ulfsland",
                   detected=[], declared=[dict(
                       crossing_id="ULF-x01", joins=["a", "b"],
                       bank_a=dict(xz=[900.0, 900.0], road_deck_y=30.6),
                       bank_b=dict(xz=[912.0, 900.0], road_deck_y=30.6))])
    rows, refusals = _spans_over_fixture(also={"crossings.yaml": default})
    hard = _hard(refusals)
    ids = sorted(r["id"] for r in rows)
    ok = not hard and ids == ["S1", "S12-brgs1-boathouse-x01"]
    return [_f("crossings", ok,
               f"with the default seed's corpus (Ulfsland / Pirate68, 1 "
               f"crossing) beside this seed's own ({_FIXTURE_WORLD} / "
               f"{_FIXTURE_SEED}, 2 crossings), a {_FIXTURE_SEED} run adopted "
               f"{ids} with {len(hard)} refusal(s) - its OWN two, from "
               f"{I._worldseed().crossings_path(_FIXTURE_SEED).name}",
               numbers=dict(adopted=len(rows), refusals=len(hard),
                            ids=ids))]


def _neuter_crossings_corpus_default_seed_only():
    """THE DEFECT, VERBATIM: every read opens `roads/crossings.yaml`."""
    global CROSSINGS_CORPUS_BY_SEED
    CROSSINGS_CORPUS_BY_SEED = False

    def undo():
        global CROSSINGS_CORPUS_BY_SEED
        CROSSINGS_CORPUS_BY_SEED = True
    return undo



def _case_every_declared_scope_dependency_is_load_bearing():
    """THE CENSUS, OVER THE GUARD'S OWN DECLARED DEPENDENCY LIST.

    `instruments.CORPUS_SCOPE_DEPENDS_ON` names the four fields the guard
    must be able to change its verdict on.  This varies each one in turn,
    ALONE, and refuses if any of them has stopped mattering.

    THE DENOMINATOR IS THE TUPLE, NOT THE SURVIVORS.  That is the whole
    point: a neuter can empty the comparison, but it cannot shrink the list
    of names the comparison claims to read, so an index that has gone blind
    reports 2 of 4 rather than 4 of 4 and this case flips.  MEASURED
    2026-09-23 on this harness's own machinery: two provenance cases shared a
    neuter that switched their classifier off entirely, 0 of 4 dependencies
    survived it, and both were stamped PROVEN LOAD-BEARING by an index that
    named nothing.
    """
    mine = dict(_CROSSINGS_FIXTURE)
    probes = {
        # each entry: (corpus override, run override) that must REFUSE
        "corpus_seed": (dict(mine, seed="Pirate68"), {}),
        "corpus_world": (dict(mine, world="Ulfsland"), {}),
        "run_seed": (mine, {"seed": "Pirate68"}),
        "run_world": (mine, {"world": "Ulfsland"}),
    }
    survivors = []
    for dep in I.CORPUS_SCOPE_DEPENDS_ON:
        doc, run = probes[dep]
        if _hard(_guard_refusals(doc=doc, **run)):
            survivors.append(dep)
    lost = [d for d in I.CORPUS_SCOPE_DEPENDS_ON if d not in survivors]
    return [_f("crossings", not lost,
               f"{len(survivors)} of {len(I.CORPUS_SCOPE_DEPENDS_ON)} "
               f"declared corpus-scope dependencies are load-bearing: "
               f"varying {sorted(survivors)} alone makes the guard refuse, "
               f"and {sorted(lost)} no longer change its answer",
               numbers=dict(survivors=len(survivors),
                            declared=len(I.CORPUS_SCOPE_DEPENDS_ON),
                            lost=sorted(lost)))]


def _neuter_scope_ignores_the_world():
    """ONE ARM LOSES ONE NAMED DEPENDENCY: the WORLD comparison, and only
    it.  The seed comparison is left intact, so this is the guard a reader
    gets by copying `require_scope`'s seed-scoped tolerance onto a
    world-scoped corpus - which is the mistake, not a strawman."""
    was = I.corpus_scope

    def world_blind(label, path, doc, *, want_seed, want_world, **kw):
        d = doc if isinstance(doc, dict) else {}
        return was(label, path, doc, want_seed=want_seed,
                   want_world=str(d.get("world") or want_world), **kw)

    I.corpus_scope = world_blind

    def undo():
        I.corpus_scope = was
    return undo


def _neuter_scope_tolerates_an_absent_declaration():
    """THE OTHER DIRECTION OF THE SAME ARM: absence read as permission.  A
    corpus that declares nothing is treated as one that matches, which is
    what every reader in this tree did before `require_scope` existed."""
    was = I.corpus_scope

    def absence_is_clear(label, path, doc, *, want_seed, want_world, **kw):
        d = doc if isinstance(doc, dict) else {}
        if not d.get("seed") and not d.get("world"):
            return None
        return was(label, path, doc, want_seed=want_seed,
                   want_world=want_world, **kw)

    I.corpus_scope = absence_is_clear

    def undo():
        I.corpus_scope = was
    return undo


def _neuter_the_corpus_read_never_fails():
    """An unreadable corpus back to an empty population and a log line."""
    was = I.read_scoped_corpus

    def quiet(ctx, label, relpath=None, **kw):
        doc, refusals = was(ctx, label, relpath, **kw)
        if any(f.numbers.get("readable") is False for f in refusals):
            return {}, []
        return doc, refusals

    I.read_scoped_corpus = quiet

    def undo():
        I.read_scoped_corpus = was
    return undo


def _neuter_scope_refuses_everything():
    """THE DEGENERATE GUARD, WHICH IS WHAT THE SURVIVOR ARM EXISTS TO CATCH.

    A check that refuses every corpus passes all three refusing cases above
    and blocks every world that is fine.  Only an ACCEPTING case can see it,
    and only if that case is expressed as a VERDICT - `run_cases` reads an
    AssertionError as a flip, so an assert-shaped survivor would stamp this
    PROVEN having proved nothing.
    """
    was = I.corpus_scope

    def refuse_all(label, path, doc, *, want_seed, want_world, **kw):
        return (f"REFUSING: {label} {path} (the degenerate guard: every "
                f"corpus is foreign)")

    I.corpus_scope = refuse_all

    def undo():
        I.corpus_scope = was
    return undo


# --- IS THERE WATER UNDER THIS BRIDGE --------------------------------------
# The beds below are the SHAPES of two crossings, not this world's readings
# copied in: a fixture that borrows a save inherits the save's mutability,
# which is the failure `Case.neuter` exists for.  `_DRY_RIDGE` is a span
# whose bed climbs OVER its own declared deck - S12-brgs1-boathouse-x01's
# shape, MEASURED 32 of 82 metres above deck 33.870, worst +2.926 - and
# `_REAL_CHANNEL` is a span whose bed drops under the water plane and stays
# below the deck, which is every other crossing in this world.
_DECK = 33.87
_DRY_RIDGE = ([29.2, 29.7] + [31.1 + 0.4 * i for i in range(14)]
              + [36.8 - 0.1 * i for i in range(40)] + [33.9] * 26)
_REAL_CHANNEL = ([31.2, 30.8] + [28.9] * 24 + [30.7, 31.1])


def _case_a_dry_ridge_is_not_a_crossing():
    p = crossing_premise(_DRY_RIDGE, _DECK)
    return [_f("crossings", p["pass"],
               "a crossing whose own bed stands above its own declared deck "
               f"on {p['bed_above_deck_samples']} of {p['samples']} metres, "
               f"worst +{p['worst_bed_above_deck_m']:.3f} m, with "
               f"{p['wet_samples']} of {p['samples']} metres under the water "
               f"plane: owner={p['owner']}. This is "
               "S12-brgs1-boathouse-x01, declared `why_terrain_failed: "
               "water_body` with `gap_min_y: 25.24` - and 46 of that road's "
               "74 stations were struck out of the terrain writer's "
               "population by a water body that is not there",
               measured=p["worst_bed_above_deck_m"], limit=p["limit_m"],
               numbers=dict(bed_above_deck=p["bed_above_deck_samples"],
                            samples=p["samples"],
                            wet=p["wet_samples"], owner=p["owner"]))]


def _case_a_real_channel_keeps_its_deck():
    p = crossing_premise(_REAL_CHANNEL, 32.5)
    return [_f("crossings", p["pass"],
               "a crossing whose bed drops to 28.9 m under a 32.5 m deck is "
               f"still a crossing: {p['bed_above_deck_samples']} metre(s) "
               f"above the deck, {p['wet_samples']} of {p['samples']} wet, "
               f"owner={p['owner']}. THE PREMISE CHECK MUST NOT TAKE A DECK "
               "AWAY FROM A REAL CHANNEL - W5-brgs1-wsouth-x01 is only 39 "
               "of 90 metres wet and is a genuine 89.44 m water crossing",
               numbers=dict(bed_above_deck=p["bed_above_deck_samples"],
                            wet=p["wet_samples"], owner=p["owner"]))]


def _case_an_unjudgeable_premise_says_so():
    p = crossing_premise(_REAL_CHANNEL, None)
    q = crossing_premise([], 32.5)
    return [_f("crossings", p["pass"] and q["pass"]
               and not p["judged"] and not q["judged"],
               "a crossing with no declared deck datum, and one with no bed "
               f"read at all, are UNMEASURED not true: judged={p['judged']}"
               f"/{q['judged']}, and both still pass rather than inventing "
               "a verdict",
               numbers=dict(no_datum_judged=p["judged"],
                            no_bed_judged=q["judged"],
                            no_bed_samples=q["samples"]))]


def _neuter_premise_never_asked():
    """Put the OLD CHECK back: ask only whether a deck carries a walker,
    never whether there is anything under it to carry him over."""
    was = globals()["crossing_premise"]

    def blind(beds, _deck_y):
        # THE SHAPE IS THE SAME, THE QUESTION IS GONE.  A neuter that
        # changes the shape raises inside the case instead of letting the
        # case's own assertion fire, and an exception proves nothing.
        return dict(**{"pass": True}, judged=False, samples=len(beds),
                    wet_samples=0, bed_above_deck_samples=0,
                    worst_bed_above_deck_m=0.0,
                    limit_m=CROSSING_BURIAL_MAX_M, owner="Crossings",
                    why="the old check never asked")

    globals()["crossing_premise"] = blind

    def undo():
        globals()["crossing_premise"] = was
    return undo


# --- WHICH ARM OWNS A ROW: THE GATE MAY NOT REFUSE A PREMISE AND THEN -------
# --- JUDGE WHAT STANDS ON IT ------------------------------------------------
# THE STATE THESE PIN is gate run C's `T7-stathub-stenvik-x01`, which carried
# TWO refusals at once: "IS NOT A CROSSING: its own bed stands above its own
# declared deck y=32.519 on 1 of 51 metres ... these stations belong to
# Ribbon" AND "does not carry a walker: 44 breaks over a 49.7 m span, 6 of 51
# metres stand on a piece".  The row's SHAPE is reproduced here, never its
# reading borrowed, so the case keeps describing something after the world
# moves.
def _judgement_row(premise_pass: bool, carried: int, breaks: int,
                   samples: int = 51, duplicate_of=None) -> dict:
    """One `span_rows` row, built in code, with only the fields the
    ownership predicate reads."""
    prem = (dict(**{"pass": True}, judged=True, samples=samples,
                 wet_samples=samples, bed_above_deck_samples=0,
                 worst_bed_above_deck_m=0.0, limit_m=CROSSING_BURIAL_MAX_M,
                 owner="Crossings", why="")
            if premise_pass else
            dict(**{"pass": False}, judged=True, samples=samples,
                 wet_samples=13, bed_above_deck_samples=1,
                 worst_bed_above_deck_m=0.412,
                 limit_m=CROSSING_BURIAL_MAX_M, owner="Ribbon", why=""))
    return dict(crossing="X-a-b-x01", premise=prem, samples=samples,
                carried_by_a_piece=carried, breaks=breaks,
                duplicate_of=duplicate_of)


def _case_a_refused_premise_is_not_also_judged_for_carriage():
    r = _judgement_row(premise_pass=False, carried=6, breaks=44)
    arm = carriage_is_judged(r)
    return [_f("crossings", not arm["judged"],
               "a crossing whose PREMISE has been refused - its own bed "
               f"stands above its own declared deck on "
               f"{r['premise']['bed_above_deck_samples']} of {r['samples']} "
               f"metres - is NOT ALSO judged for carriage: owner="
               f"{arm['owner']}, judged={arm['judged']}, with "
               f"{r['carried_by_a_piece']} of {r['samples']} metres standing "
               f"on a piece and {r['breaks']} break(s) measured and reported "
               "either way. THIS IS RUN C's T7-stathub-stenvik-x01, WHICH "
               "CARRIED BOTH REFUSALS AT ONCE. And the defect is general, "
               "not cosmetic: the premise arm's own remedy is to hand these "
               "stations back to the RIBBON, which writes terrain across "
               "them - so the first piece that lands flips the old "
               "suppression off and buys a SECOND refusal. REPAIRING THE "
               "PREMISE-REFUSED SET COULD ONLY EVER INCREASE THE REFUSAL "
               "COUNT, which is how this check went 14 -> 15 between B and "
               "C with no road made worse",
               numbers=dict(judged=arm["judged"], owner=arm["owner"],
                            carried_by_a_piece=r["carried_by_a_piece"],
                            breaks=r["breaks"], samples=r["samples"],
                            bed_above_deck_samples=r["premise"][
                                "bed_above_deck_samples"]))]


def _case_a_real_crossing_with_a_hole_is_still_judged():
    r = _judgement_row(premise_pass=True, carried=19, breaks=30)
    arm = carriage_is_judged(r)
    return [_f("crossings", not arm["judged"],
               "a crossing that DOES cross water and has a hole in its deck "
               f"is still judged for carriage: owner={arm['owner']}, "
               f"judged={arm['judged']}, {r['carried_by_a_piece']} of "
               f"{r['samples']} metres on a piece, {r['breaks']} break(s). "
               "THE SUPPRESSION MUST NOT BECOME A BLANKET - a gate that "
               "stops judging carriage altogether scores a bridge with a "
               "hole in it identically with a perfect one, which is the "
               "defect the crossings check was added to close",
               numbers=dict(judged=arm["judged"], owner=arm["owner"],
                            breaks=r["breaks"],
                            carried_by_a_piece=r["carried_by_a_piece"]))]


def _neuter_carriage_independent_of_premise():
    """Put the OLD PREDICATE back: the carriage arm fires on its own, and is
    suppressed only when NOTHING AT ALL stands on the span - which is why a
    premise-refused crossing that anyone builds on buys a second refusal."""
    was = globals()["carriage_is_judged"]

    def blind(row):
        nobody = not row["carried_by_a_piece"]
        judged = not (nobody and not row["premise"]["pass"])
        return dict(judged=judged, owner="carriage" if judged else "premise",
                    why="the old predicate asked only whether anything at "
                        "all stood on the span")

    globals()["carriage_is_judged"] = blind

    def undo():
        globals()["carriage_is_judged"] = was
    return undo


def _neuter_carriage_never_judged():
    """THE OVER-CORRECTION, restored so the accepting arm has something to
    hold it: suppress the carriage arm on every row.  Refusal counts fall
    and a bridge with a hole in it reads exactly like a perfect one."""
    was = globals()["carriage_is_judged"]

    def blind(_row):
        return dict(judged=False, owner="premise",
                    why="the over-correction never judges carriage")

    globals()["carriage_is_judged"] = blind

    def undo():
        globals()["carriage_is_judged"] = was
    return undo


# --- ONE PHYSICAL CHANNEL, ONE OBLIGATION ----------------------------------
# THE STATE THESE PIN is `roads/crossings.yaml` as this world's detector
# wrote it: 17 declarations describing 13 channels, because `detected` is
# built as one record PER SEGMENT and two segments that must both get across
# one channel each declare it.  Both fixtures are CONSTRUCTED at the origin -
# the SHAPES are the measured ones, the coordinates are not this world's, so
# neither case can go quiet when a record moves.
#
# ARM 1 (must REFUSE): two declarations of one channel, offset 2.4 m and
# reversed, as T4-meadhall-stathub-x01 and T7-stathub-stenvik-x02 are.
# ARM 2 (must ACCEPT): two DIFFERENT channels whose near banks are 2.0 m
# apart, the shape of S1 against S12-brgs1-boathouse-x01 - measured 0.279,
# the nearest non-duplicate in the whole corpus.  A census that collapses
# these has silently HALVED A REAL OBLIGATION, which is worse than missing a
# duplicate because nothing downstream can see it afterwards.
_ONE_CHANNEL_TWICE = [
    dict(id="X1-alpha-beta-x01", a=(0.0, 0.0), b=(40.0, 0.0), span_m=40.0),
    dict(id="X2-beta-gamma-x01", a=(38.0, 2.4), b=(-1.0, 2.4), span_m=39.0),
    dict(id="X3-delta-eps-x01", a=(0.0, 120.0), b=(40.0, 120.0), span_m=40.0),
]
_TWO_CHANNELS_ONE_BANK = [
    dict(id="Y1-alpha-beta-x01", a=(0.0, 0.0), b=(-12.0, 0.0), span_m=12.0),
    dict(id="Y2-alpha-gamma-x01", a=(0.0, 2.0), b=(28.0, -74.0),
         span_m=80.99),
]


def _case_one_channel_declared_twice_is_refused():
    cen = collapse_crossing_declarations(_ONE_CHANNEL_TWICE)
    pairs = cen["pairs_over_threshold"]
    return [_f("crossings",
               cen["distinct_channels"] == cen["declarations"],
               f"{cen['declarations']} declarations describe "
               f"{cen['distinct_channels']} distinct channel(s) - "
               f"{len(cen['collapsed'])} redundant, {cen['declared_m']} m of "
               f"declared span is {cen['distinct_m']} m of channel; "
               + "; ".join(f"{p['a']}={p['b']} ({p['fraction']:.3f})"
                           for p in pairs)
               + ". THE DETECTOR EMITS ONE RECORD PER SEGMENT, so a channel "
                 "two segments must both get across is declared twice under "
                 "two names. That charges the DENOMINATOR twice and credits "
                 "ONE BUILT DECK to EVERY declaration sharing its corridor - "
                 "on Ulfsland it made 'carry a walker' read 3 of 17 when two "
                 "of the three passes were one deck",
               measured=max((p["fraction"] for p in pairs), default=0.0),
               limit=cen["threshold"],
               numbers=dict(declarations=cen["declarations"],
                            distinct_channels=cen["distinct_channels"],
                            collapsed=cen["collapsed"], pairs=pairs))]


def _case_two_channels_sharing_a_bank_are_not_collapsed():
    cen = collapse_crossing_declarations(_TWO_CHANNELS_ONE_BANK)
    f = corridor_overlap(*[v for r in _TWO_CHANNELS_ONE_BANK
                           for v in (r["a"], r["b"])])
    return [_f("crossings", cen["distinct_channels"] == 2,
               f"two DIFFERENT channels leaving banks 2.0 m apart are NOT "
               f"collapsed: corridor overlap {f:.3f} against a "
               f"{cen['threshold']} threshold, "
               f"{cen['distinct_channels']} channel(s) from "
               f"{cen['declarations']} declarations. This is the shape of S1 "
               "against S12-brgs1-boathouse-x01, which SHARE 14 OF S12's 17 "
               "BUILT PIECES - the rejected piece-overlap test scores them "
               "0.824 and collapses them, silently halving a real "
               "obligation that nothing downstream could then see",
               measured=f, limit=cen["threshold"],
               numbers=dict(distinct_channels=cen["distinct_channels"],
                            declarations=cen["declarations"],
                            fraction=f, collapsed=cen["collapsed"]))]


def _neuter_corpus_taken_at_face_value():
    """Put the OLD CORPUS back: every declaration is its own channel, which
    is what this gate believed until a census was run over the file."""
    was = globals()["collapse_crossing_declarations"]

    def blind(rows, threshold=CROSSING_DUPLICATE_FRACTION):
        cen = was(rows, threshold)
        return dict(cen, distinct_channels=len(rows), collapsed={},
                    pairs_over_threshold=[],
                    distinct_m=round(sum(r["span_m"] for r in rows), 2))

    globals()["collapse_crossing_declarations"] = blind

    def undo():
        globals()["collapse_crossing_declarations"] = was
    return undo


def _neuter_collapse_on_endpoint_proximity():
    """THE REJECTED ALTERNATIVE, restored so the accepting arm has something
    to hold it: collapse whenever any two banks are within 3 m of each other.
    It false-positives on two channels that merely leave the same headland."""
    was = globals()["collapse_crossing_declarations"]

    def blind(rows, threshold=CROSSING_DUPLICATE_FRACTION):
        cen = was(rows, threshold)
        collapsed = dict(cen["collapsed"])
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                if any(math.hypot(p[0] - q[0], p[1] - q[1]) <= 3.0
                       for p in (rows[i]["a"], rows[i]["b"])
                       for q in (rows[j]["a"], rows[j]["b"])):
                    collapsed.setdefault(rows[j]["id"], rows[i]["id"])
        return dict(cen, collapsed=collapsed,
                    distinct_channels=len(rows) - len(collapsed))

    globals()["collapse_crossing_declarations"] = blind

    def undo():
        globals()["collapse_crossing_declarations"] = was
    return undo


# --- THE INSTRUMENT THAT COULD NOT SEE THE DECK ----------------------------
# CONSTRUCTED, NEVER BORROWED: one road ending at a bank, and a declared
# crossing running 90 m out across water from that bank.  The numbers are
# W10-brgs1-treenear-x01's shape in miniature and the real sighting is
# quoted in `corridor_mask`.
def _mask_world():
    """(stations, span samples) for a road that stops at its own bank."""
    stations = [(float(x), 0.0) for x in range(-40, 1)]
    span = [(float(k), 0.0) for k in range(0, 91)]
    return stations, span


def _case_a_declared_crossing_is_inside_the_corridor_mask():
    """A DECK OVER WATER IS NOT WITHIN 24 M OF A ROAD, BY CONSTRUCTION.

    The mask exists so half a million objects are not hulled for nothing,
    and it was derived from the BUILT CENTRELINES alone.  The crossings arm
    then asks `surfaces_at` at DECLARED CROSSING samples - coordinates that
    are by construction not on any built road, because a crossing is
    exactly the water the ribbon refused to write.  Every deck tile beyond
    the mask is absent from the grid, and the arm reads bed only.

    MEASURED, W10-brgs1-treenear-x01 at fp 7632b3a5674842bd: a COMPLETE
    319-piece deck - 94 wood_floor at lateral +/-1.000, along 0..92, all at
    y 32.403 - reported 84 of 90 metres carried with 4 breaks.  Seven
    samples, k=0..6, are outside the mask built from all 22,000 stations
    (77,227 cells).  The builder was right and the gate refused the work.
    """
    stations, span = _mask_world()
    with_span = corridor_mask(stations, span)
    without = corridor_mask(stations)
    blind = [k for k, (x, z) in enumerate(span)
             if (int(x // CORRIDOR_CELL_M),
                 int(z // CORRIDOR_CELL_M)) not in without]
    seen = [k for k, (x, z) in enumerate(span)
            if (int(x // CORRIDOR_CELL_M),
                int(z // CORRIDOR_CELL_M)) not in with_span]
    return [_f("crossings", not seen and bool(blind),
               f"with the declared crossing in the mask, "
               f"{len(span) - len(seen)} of {len(span)} span metres can be "
               f"seen by the obstruction grid; with the built centrelines "
               f"ALONE, {len(blind)} of {len(span)} are invisible and a "
               "deck standing on them reads as absent. A MASK DERIVED FROM "
               "ONE ARM'S POPULATION AND CONSUMED BY ANOTHER'S is the "
               "two-populations fault one level down - in the instrument "
               "rather than in the corpus - and it makes a correct deck "
               "read as a holed one",
               measured=float(len(span) - len(seen)), limit=float(len(span)),
               numbers=dict(invisible_without_spans=len(blind),
                            invisible_with_spans=len(seen),
                            span_samples=len(span),
                            mask_cells=len(with_span)),
               population="one 40 m road and one 90 m declared crossing "
                          "leaving its bank")]


def _neuter_the_mask_is_built_from_centrelines_alone():
    """RESTORE THE SHIPPED MASK: built from the stations and nothing else,
    so every metre of every crossing more than CORRIDOR_MASK_M from a built
    road is invisible to the instrument that decides whether a deck stands
    there."""
    was = globals()["corridor_mask"]

    def blind(stations, spans=(), cell=CORRIDOR_CELL_M, reach_m=None):
        return was(stations, (), cell, reach_m)

    globals()["corridor_mask"] = blind

    def undo():
        globals()["corridor_mask"] = was
    return undo


def _case_a_station_adjacent_span_metre_survives_the_centrelines_only_mask():
    """THE SURVIVOR ARM, AND IT IS WHAT MAKES THE OTHER ONE A PROOF.

    `_neuter_the_mask_is_built_from_centrelines_alone` drops the crossings
    from the mask's population.  Dropping a population cannot make a cell
    APPEAR, so an arm that only asserts "the crossing metres are visible"
    is satisfied by any mask that shrank - INCLUDING ONE THAT RETURNED
    NOTHING AT ALL.  A DEGENERATE INDEX PASSES A ONE-DIRECTION NEUTER, and
    that is verdict-equality certifying a blind instrument inside the
    machinery built to stop verdict-equality certifying a blind instrument.

    So this arm names a sample the mask must STILL hold when the first
    neuter fires: the span metres within CORRIDOR_MASK_M of the road's own
    last station, which the STATIONS alone already cover.  If they vanish
    too, the flip was a SHRUNK MASK and not a LOST DEPENDENCY.

    MEASURED on the fixture, which is W10-brgs1-treenear-x01's shape in
    miniature: 28 of the 91 span metres (k=0..27) survive the
    centrelines-only mask, and 0 of 91 survive a mask that names nothing.

    A VERDICT, NEVER AN ASSERT.  `run_cases` reads an AssertionError as a
    flip, so a survivor expressed as an assertion lets a neuter that
    emptied the instrument be stamped PROVEN LOAD-BEARING.
    """
    stations, span = _mask_world()
    mask = corridor_mask(stations)         # THE STATIONS ALONE, on purpose
    seen = [k for k, (x, z) in enumerate(span)
            if (int(x // CORRIDOR_CELL_M),
                int(z // CORRIDOR_CELL_M)) in mask]
    return [_f("crossings", bool(seen),
               f"{len(seen)} of {len(span)} declared span metres lie within "
               f"{I.CORRIDOR_MASK_M:g} m of a BUILT station and are in the "
               f"mask on the stations alone"
               + (f" (k={seen[0]}..{seen[-1]})" if seen else "")
               + ". A mask that lost its crossings keeps these; a mask that "
                 "NAMES NOTHING loses them too, and only the second is a "
                 "shrunk index rather than a lost dependency. Without this "
                 "arm, 'the neuter flipped' is satisfied by an index that "
                 "simply returned nothing",
               measured=float(len(seen)), limit=1.0,
               numbers=dict(station_adjacent_span_metres=len(seen),
                            span_samples=len(span), mask_cells=len(mask),
                            depends_on=CORRIDOR_MASK_DEPENDS_ON),
               population="the same 40 m road and 90 m declared crossing, "
                          "masked on the BUILT STATIONS ALONE")]


def _neuter_the_mask_names_nothing():
    """THE OTHER DIRECTION'S NEUTER: a mask with no cells in it at all.

    The degenerate over-correction, and the one a one-direction proof
    cannot see: an index so narrowly keyed that it names nothing reports
    every object as outside every corridor, which reads as "there is
    nothing here to judge" and is the quietest possible failure.
    """
    was = globals()["corridor_mask"]

    def nothing(stations, spans=(), cell=CORRIDOR_CELL_M, reach_m=None):
        return set()

    globals()["corridor_mask"] = nothing

    def undo():
        globals()["corridor_mask"] = was
    return undo



# --- THE PRODUCER DEFECT ---------------------------------------------------
# CONSTRUCTED, NEVER BORROWED: two records on one straight centreline, one
# whose bank sits 4.077 m off it and one 1.016 m off it - T4-x02's and W10's
# measured offsets, to the millimetre, in a fixture with no save behind it.
def _offset_corpus():
    """Two crossing records on the same recorded centreline."""
    def rec(cid, lat):
        return {"crossing_id": cid, "segment_id": cid.split("-")[0],
                "centreline_straight_from_to": [[0.0, 0.0], [50.0, 0.0]],
                "bank_a": {"xz": [5.0, 0.0]},
                "bank_b": {"xz": [45.0, lat]}}
    return [rec("OVER", 4.077), rec("UNDER", 1.016),
            {"crossing_id": "NOLINE", "bank_a": {"xz": [0.0, 0.0]},
             "bank_b": {"xz": [1.0, 0.0]}}]


def _case_a_declaration_further_off_its_line_than_a_deck_is_wide():
    """NO DECK ON THE ROAD COVERS A LINE THE DECK IS NOT WIDE ENOUGH TO REACH.

    THE PRODUCER DEFECT, BY FILE AND BY LINE.  `roads/route.py`'s
    `find_crossings` writes `bank_a`/`bank_b` from the route polyline of
    PASS ONE (line 741-746, snapped to the piece grid).
    `roads/network.py::build_segment` then STRAIGHTENS every crossing
    window - `pts = straighten(pts, s, wa, wb)`, line 576 - and writes
    `centreline_straight_from_to` from the POST-straighten points (lines
    677-687).  The bank is never re-read.  The bank is keyed on one
    polyline and the line it is supposed to lie on is keyed on another.

    MEASURED over roads/crossings.yaml: 15 of 17 records declare a
    centreline, 13 of those 15 are 1.016-6.069 m off it, and NINE are
    further off than the deck's 3.000 m half-width.  T4-x02 is the proof
    that this is a producer defect and not a coverage one: projecting the
    record onto its own centreline LOSES A WET METRE (42/49 -> 41/49) and
    puts the bed ABOVE the deck on a metre where it was 0, so the record
    and the centreline describe DIFFERENT WATER and no deck reconciles
    them.

    BOTH DIRECTIONS, ON ONE FIXTURE: the 4.077 m record must be refused and
    the 1.016 m record must NOT be, so the repair can never be "refuse
    every declaration".  A VERDICT, NEVER AN ASSERT.
    """
    got = declaration_off_centreline(_offset_corpus())
    over = {r["crossing_id"] for r in got["over"]}
    ok = over == {"OVER"} and got["judged"] == 2 \
        and got["no_centreline"] == ["NOLINE"]
    return [_f("crossings", ok,
               f"of {got['judged']} record(s) that declare a centreline, "
               f"{len(got['over'])} lie further off it than the deck's "
               f"{got['half_width_m']:.3f} m half-width: "
               + (", ".join(f"{r['crossing_id']} {abs(r['offset_m']):.3f} m"
                            for r in got["over"]) or "none")
               + f"; refused {sorted(over)}, required ['OVER']. A record "
                 f"1.016 m off its line is NOT refused, so the rule is not "
                 f"'refuse every declaration'. The producer is "
                 f"roads/route.py::find_crossings (banks from the "
                 f"PRE-straighten polyline, lines 741-746) against "
                 f"roads/network.py::build_segment (the straighten at line "
                 f"576, the centreline written from the POST-straighten "
                 f"points at lines 677-687)",
               measured=float(len(got["over"])), limit=0.0,
               numbers=dict(refused=sorted(over), judged=got["judged"],
                            no_centreline=got["no_centreline"],
                            worst_m=got["worst_m"],
                            half_width_m=got["half_width_m"]),
               population="3 constructed crossing records on one recorded "
                          "centreline: 4.077 m off, 1.016 m off, and one "
                          "with no centreline declared at all")]


def _neuter_the_declaration_is_measured_against_itself():
    """RESTORE THE STATE THAT SHIPPED: measure each bank against the line the
    RECORD ITSELF draws between its own two banks, which is a line every bank
    lies on by construction.  The offset is then identically zero and the
    corpus is never refused - a check confidently answering a question it is
    not measuring."""
    was = globals()["declaration_off_centreline"]

    def against_itself(records, half_width_m=CROSSING_DECK_HALF_WIDTH_M):
        patched = []
        for c in records or ():
            c = dict(c)
            if c.get("centreline_straight_from_to"):
                c["centreline_straight_from_to"] = [
                    list(c["bank_a"]["xz"]), list(c["bank_b"]["xz"])]
            patched.append(c)
        return was(patched, half_width_m)

    globals()["declaration_off_centreline"] = against_itself

    def undo():
        globals()["declaration_off_centreline"] = was
    return undo


# --- THE BUILDER IS ASKED WHETHER THE REMEDY EXISTS ------------------------
# The arms live in `roads/boardwalk.py`, imported rather than restated: the
# BUILDER is what decides whether a deck is ever placed, so a gate that
# reimplemented its preconditions would be the third answer to a question
# that has already cost this tree twice.
def _deck_remedy_arms():
    p = str(I.JUMPSTART / "roads")
    if p not in sys.path:
        sys.path.insert(0, p)
    import boardwalk as bw                                # noqa: PLC0415
    return bw, bw.deck_remedy_arms()


def _case_a_crossing_the_builder_refuses_is_not_actionable():
    """ACTIONABLE ASSERTS A REPAIR EXISTS, AND NOBODY WAS ASKING THE BUILDER.

    MEASURED on Ulfsland 7632b3a5674842bd: eight of the gate's twelve
    remaining ACTIONABLE rows were crossings, and `DeckBuild` measured that
    the only builder in this tree REFUSES EVERY ONE OF THEM before a tile is
    placed - five for a segment no bed-moving record is attributed to, three
    for a declaration further off its own centreline than the deck is
    half-wide whose reconciliation the reconciler refuses by name.  The
    crossings arm asked only the TERRAIN-OWNERSHIP question - "a deck writes
    no terrain, so nothing forbids it" - which answers the second and third
    clauses of ACTIONABLE and never the first.
    """
    _bw, arms = _deck_remedy_arms()
    a = next(x for x in arms if x["want_refused"])
    return [_f("crossings", a["ok"],
               f"{a['crossing_id']}: the builder's own predicate returns "
               f"refused={a['got_refused']} class={a['got_class']}, "
               f"required refused={a['want_refused']} "
               f"class={a['want_class']}; its worst bank is "
               f"{abs(a['worst_offset_m'] or 0.0):.3f} m off its own "
               f"centreline against a {a['half_width_m']} m deck "
               f"half-width and `reconcile_declared_lines` REFUSES the "
               f"projection. A row the only builder in this tree cannot "
               f"build is not a work item",
               measured=float(abs(a["worst_offset_m"] or 0.0)),
               limit=float(a["half_width_m"] or 0.0),
               numbers={k: a[k] for k in
                        ("crossing_id", "present", "got_refused",
                         "got_class", "want_class", "offsets", "buildable")},
               population="the live crossings corpus, one record whose "
                          "reconciliation its own tool refuses")]


def _case_a_crossing_the_builder_has_built_is_not_refused():
    """THE OTHER DIRECTION, and it is what stops the repair being 'refuse
    every crossing'.  W10-brgs1-treenear-x01 carries a 319-piece deck over
    90 of its 90 declared metres; a buildability predicate that retired it
    would empty the work queue and read like triage."""
    _bw, arms = _deck_remedy_arms()
    a = next(x for x in arms if not x["want_refused"])
    return [_f("crossings", a["ok"],
               f"{a['crossing_id']}: the builder's own predicate returns "
               f"refused={a['got_refused']} class={a['got_class']}, "
               f"required refused=False class=None; its worst bank is "
               f"{abs(a['worst_offset_m'] or 0.0):.3f} m off its own "
               f"centreline, INSIDE the {a['half_width_m']} m half-width. "
               f"A predicate that refused this one would be satisfied by "
               f"saying no to everything",
               measured=float(abs(a["worst_offset_m"] or 0.0)),
               limit=float(a["half_width_m"] or 0.0),
               numbers={k: a[k] for k in
                        ("crossing_id", "present", "got_refused",
                         "got_class", "offsets", "buildable")},
               population="the live crossings corpus, one record whose "
                          "deck STANDS")]


def _neuter_the_builder_is_never_asked():
    _bw, _ = _deck_remedy_arms()
    return _bw._neuter_deck_remedy_never_asks_the_builder()


def _neuter_the_builder_refuses_everything():
    _bw, _ = _deck_remedy_arms()
    return _bw._neuter_deck_remedy_refuses_everything()


def _case_the_builders_whole_precondition_vector_is_named():
    """ONE OR TWO ROWS OF AN N-ROW TRUTH TABLE IS NOT A PROOF OF THE TABLE.

    MEASURED on my own file, after CacheKey ran the same audit on theirs:
    `DECK_REMEDY_PRECONDITIONS` declared 3 names while `deck_remedy`
    produced 5, the intersection was 2, one declared name was produced by
    no branch, three produced names were declared nowhere, and the tuple
    had ZERO readers - so it could not be wrong loudly and was wrong
    quietly. The key class, in the declaration written to state the key.

    AND THE VECTOR ARM FOUND A SECOND ONE ON ITS FIRST RUN: `deck_remedy`
    recorded `reconciliation` in `unasked` when the reconciler had NOT run
    and recorded it NOWHERE when it HAD - so the one precondition that
    separates a producer defect from a prerequisite nobody ran vanished
    from the inventory exactly when it was consulted.

    Both arms are VERDICTS. The required vector is a module constant and
    the record comes off the yaml, so a neutered `deck_remedy` shrinks the
    OBSERVED set while the REQUIRED set stands - the denominator is not
    takeable from the thing the neuter replaces.
    """
    _bw, _ = _deck_remedy_arms()
    arms = _bw.deck_remedy_vector_arms()
    a = next(x for x in arms if x["name"].startswith("the_whole"))
    return [_f("crossings", a["ok"],
               f"the builder names {len(a['observed'])} of "
               f"{len(a['required'])} preconditions on "
               f"{a.get('crossing_id')}: required {a['required']}, observed "
               f"{a['observed']}"
               + (f", MISSING {a['missing']}" if a["missing"] else "")
               + (f", UNDECLARED {a['unexpected']}" if a["unexpected"]
                  else "")
               + ". A predicate that stops asking something its answer "
                 "depends on, while every other case stays green, is the "
                 "defect this arm exists for",
               measured=float(len(a["observed"])),
               limit=float(len(a["required"])),
               numbers=dict(required=a["required"], observed=a["observed"],
                            missing=a["missing"],
                            unexpected=a["unexpected"]),
               population="every precondition roads/boardwalk.py::"
                          "deck_remedy names, asked or not")]


def _case_no_builder_answer_can_produce_ACTIONABLE():
    """THE SURVIVOR, AND IT IS THE CONSUMER-SIDE HOLE BuryFix FOUND.

    `deck_remedy` makes `buildable=True` structurally unreachable, so
    "assume a deck closes this" cannot be had by forgetting to ask - but
    `buildable is None` means UNJUDGED, and a consumer writing
    `if not ans["buildable"]` gets the opposite of what it reads like. A
    docstring warning about that is a RULE. `deck_remedy_class` is the
    chokepoint: ACTIONABLE is not reachable from any branch for any answer
    including None.

    Scored over the whole corpus in the conditions that reach EVERY class
    the predicate can produce - empty chain, reconciler refusing, reconciler
    accepting, reconciler unrun - so the arm cannot pass by being run where
    nothing refuses. It survives the narrowing neuter (which removes a
    precondition, not the accessor) and falls to a neuter that lets the
    accessor say yes.
    """
    _bw, _ = _deck_remedy_arms()
    arms = _bw.deck_remedy_vector_arms()
    a = next(x for x in arms if x["name"].startswith("no_answer"))
    return [_f("crossings", a["ok"],
               f"over {a['population']} builder answers - every crossing "
               f"record under a refusing, an accepting and an unrun "
               f"reconciler, plus the builder-not-asked case - the classes "
               f"produced are {a['classes']} against the permitted "
               f"{a['allowed']}, and ACTIONABLE is not among them. "
               + (f"OFFENDERS {a['offenders']}" if a["offenders"] else
                  "The accessor cannot say yes"),
               measured=float(len(a["offenders"])), limit=0.0,
               numbers=dict(population=a["population"],
                            classes=a["classes"], allowed=a["allowed"],
                            offenders=a["offenders"]),
               population="every crossing record x every reconciler state, "
                          "plus the builder-not-asked case")]


def _neuter_the_builder_stops_asking_reconciliation():
    _bw, _ = _deck_remedy_arms()
    return _bw._neuter_deck_remedy_stops_asking_reconciliation()


def _reach_arms():
    _bw, _ = _deck_remedy_arms()
    return _bw, _bw.deck_remedy_reach_arms()


def _reach(name):
    _bw, arms = _reach_arms()
    return next(x for x in arms if x["name"] == name)


def _case_a_bank_no_emission_reaches_is_UNSERVED():
    """A REFUSED ABUTMENT USED TO BE AN UNJUDGED ROW.

    "the bench is 1.5 m low" does not say whether anything in this tree can
    raise it, so a crossing whose only remaining refusal was its abutment
    fell out of every class the gate can act on.  The deciding fact is not
    the step, it is the REACH: `ribbon` writes a segment's NON-BRIDGED
    stations, so a bank with none of them outboard of it is ground no
    emission of that segment ever touches.

    MEASURED, Ulfsland 2026-09-23, after `boardwalk reconcile` retired
    T5-temple-dockshore-x01's span-coverage refusal: its bank_b is the
    segment's LAST station, 525 of 526, and is_bridge[525] is True - 0.00 m
    of its own declared 6.0 m bank-level run against a +1.500 m step from
    applied ground 31.000 onto a deck at 32.500.  Corpus-wide, 11 of 30
    banks over 8 of 15 crossings declare a bank level their own segment has
    no station to carry and 7 have ZERO metres outboard.
    """
    a = _reach("a_bank_no_emission_reaches_is_UNSERVED")
    return [_f("crossings", a["ok"],
               f"{a['crossing_id']}: a {a.get('step_m')} m abutment step at "
               f"a bank with {a.get('regradeable_m')} m of regradeable "
               f"station outboard of it returns class {a['got_class']} "
               f"({a.get('got_sub')}), required {a['want_class']} "
               f"({a['want_sub']}); repair direction "
               f"{a.get('repair_direction')}. No emission of that segment "
               f"reaches the ground the bench needs",
               measured=float(a.get("regradeable_m") or 0.0),
               limit=6.0,
               numbers={k: a.get(k) for k in
                        ("crossing_id", "present", "got_class", "want_class",
                         "got_sub", "step_m", "regradeable_m", "outboard_m",
                         "repair_direction", "population", "evidence")},
               population="the live crossings corpus, one record whose "
                          "far bank is its own segment's terminus")]


def _case_the_same_step_with_road_outboard_is_OUT_OF_ORDER():
    """THE OTHER DIRECTION, AND THE ONLY THING THAT SEPARATES THE TWO.

    S6-stenvik-hognest-x01 takes the SAME 1.500 m step at the same bank
    name and carries 77.54 m of non-bridged station outboard of it, so an
    emission of its segment reaches the bench and the row is FACTORY WORK,
    not unserved.  A reach that answered 0 for everything would file both
    as terminal and retire a real obligation, which is the quiet failure.
    """
    a = _reach("the_same_step_with_road_outboard_is_OUT_OF_ORDER")
    return [_f("crossings", a["ok"],
               f"{a['crossing_id']}: the same {a.get('step_m')} m step at a "
               f"bank with {a.get('regradeable_m')} m of regradeable "
               f"station outboard returns class {a['got_class']} "
               f"({a.get('got_sub')}), required {a['want_class']} "
               f"({a['want_sub']}) - the reach, not the step, is what "
               f"orders them",
               measured=float(a.get("regradeable_m") or 0.0),
               limit=6.0,
               numbers={k: a.get(k) for k in
                        ("crossing_id", "present", "got_class", "want_class",
                         "got_sub", "step_m", "regradeable_m", "outboard_m",
                         "population", "evidence")},
               population="the live crossings corpus, one record with 77.54 "
                          "m of its own road outboard of the refused bank")]


def _case_a_bridged_station_is_not_a_bench():
    """A DECLARED DEPENDENCY TODAY'S CORPUS CANNOT PROVE, PROVEN ANYWAY.

    MEASURED over all 30 banks: `outboard_m` and `regradeable_m` are EQUAL
    on every one of them, so no live record separates "a station exists"
    from "a station the ribbon writes".  A dependency demonstrated on a
    population that cannot discriminate it has been declared, not
    demonstrated - so this arm is CONSTRUCTED: 10 m of station outboard of
    bank_b, every one of them inside the bridged run.  A bridged station is
    ground the ribbon deliberately does not write because a deck is meant
    to carry it; counting it as a bench claims an abutment on the air under
    the bridge.
    """
    a = _reach("a_bridged_station_is_not_a_bench")
    return [_f("crossings", a["ok"],
               f"{a['crossing_id']} (constructed): {a.get('outboard_m')} m "
               f"of station outboard of bank_b, all bridged -> regradeable "
               f"{a.get('regradeable_m')} m, class {a['got_class']}, "
               f"required {a['want_class']}",
               measured=float(a.get("regradeable_m") or 0.0),
               limit=float(a.get("outboard_m") or 0.0),
               numbers={k: a.get(k) for k in
                        ("crossing_id", "present", "got_class", "want_class",
                         "outboard_m", "regradeable_m", "population",
                         "evidence")},
               population="one constructed segment, seven stations, four of "
                          "them outboard and bridged - no world, no corpus")]


def _case_a_built_deck_is_not_refused_by_the_reach():
    """THE TRAP THE BANK COORDINATE SETS, REGISTERED RATHER THAN REMEMBERED.

    MEASURED: W10-brgs1-treenear-x01's bank_a has 0.00 m outboard AND reads
    +0.987 m at the bank coordinate, and its 319-piece deck IS IN THE
    GROUND - because `abutment_guard` measures at the deck's outermost
    surviving tile, where the ground has already reached H.  So the reach
    may only ever be asked AFTER that guard has refused; asked the way every
    current caller asks - with no abutment verdict at all - it must leave
    both conditional names UNASKED and refuse nothing.
    """
    a = _reach("a_built_deck_is_not_refused_by_the_reach")
    return [_f("crossings", a["ok"],
               f"{a['crossing_id']}: asked with NO abutment verdict the "
               f"builder returns {a.get('refusals')} refusal(s), class "
               f"{a.get('got_class')}, unasked={a.get('unasked')} - a deck "
               f"that stands is not refused by a bank-coordinate probe",
               measured=float(a.get("refusals") or 0),
               limit=0.0,
               numbers={k: a.get(k) for k in
                        ("crossing_id", "present", "got_class", "refusals",
                         "unasked", "population", "evidence")},
               population="the live crossings corpus, the one record whose "
                          "deck this tree has actually built")]


def _neuter_the_reach_ignores_the_direction():
    _bw, _ = _deck_remedy_arms()
    return _bw._neuter_reach_ignores_the_direction()


def _neuter_the_reach_ignores_is_bridge():
    _bw, _ = _deck_remedy_arms()
    return _bw._neuter_reach_ignores_is_bridge()


def _neuter_the_reach_answers_zero():
    _bw, _ = _deck_remedy_arms()
    return _bw._neuter_reach_answers_zero()


def _burial_arms():
    _bw, _ = _deck_remedy_arms()
    return _bw, _bw.burial_guard_arms()


def _burial(name):
    _bw, arms = _burial_arms()
    return next(x for x in arms if x["name"] == name)


def _case_a_piece_under_the_ground_is_refused_before_it_is_sent():
    """THE BUILDER PLACED TWO TILES UNDER THE GROUND AND EVERY GUARD PASSED.

    MEASURED, OrderRun's own write 2026-09-23: T9-wttown-dockshore-sb01
    built 35 pieces with deck_datum, abutment, seating, structural and
    span_coverage all PASSING and its own report printing `worst burial
    0.0 m` - and `structures/buried` then found 2 of those 35 COMPLETELY
    under the applied surface, wood_floor tiles at (677,-92) and (679,-92)
    whose tops are 0.400 m below the ground.  W10-brgs1-treenear-x01 is the
    same state at 4 of 319.

    Neither neighbouring guard owns the question: `seating` asks whether a
    piece has ground UNDER it, and `trim_buried_overhang` asks about burial
    but only of `/deck` pieces STRICTLY BEYOND a bank, because its job is to
    shorten an overhang and it may not shorten the crossing.  A tile buried
    INSIDE the crossing was nobody's question.

    AND FINDING OUT LATE IS TERMINAL: a deck is written flatten=FORBIDDEN,
    so the moment it stands `writer._flatten_forbidden_check` closes its own
    footprint to the cut that would free those pieces - no owner exception,
    only retirement.  The gate classed both rows 1A_FORBIDDEN naming the
    deck's own seq (5421 and 5433).  Refusing before the build is the only
    time the repair is free.
    """
    a = _burial("ground_above_the_tile_everywhere_is_buried")
    b = _burial("ground_below_the_tile_top_is_not_buried")
    ok = a["ok"] and b["ok"]
    return [_f("crossings", ok,
               f"constructed 2 m tile, top {a['tile_top_m']} m: ground "
               f"{a['footprint_min_ground_m']} m over it -> "
               f"refused={a['got_refused']} (required "
               f"{a['want_refused']}); ground "
               f"{b['footprint_min_ground_m']} m under it -> "
               f"refused={b['got_refused']} (required {b['want_refused']}), "
               f"limit {a['limit_m']} m",
               measured=float(a["footprint_min_ground_m"]
                              - a["tile_top_m"]),
               limit=float(a["limit_m"]),
               numbers={"buried_arm": a, "clear_arm": b},
               population="two constructed beds under one tile - no world, "
                          "no save, no corpus")]


def _case_burial_is_judged_at_the_gates_own_limit():
    """ONE QUESTION, ONE LIMIT.  `BURIAL_MAX_M` is 0.30 m and belongs to
    `trim_buried_overhang`, a SHRINK decision that wants slack;
    `structures/buried` refuses at 0.05 m.  A builder judging at its own
    looser number passes pieces the gate then refuses - MEASURED on
    T9-wttown-dockshore-sb01, 0.30 m catches 1 of the 2 tiles the gate
    found and 0.05 m catches both.
    """
    a = _burial("a_tile_inside_the_overhang_slack_is_still_buried")
    return [_f("crossings", a["ok"],
               f"a tile buried {a['footprint_min_ground_m'] - a['tile_top_m']:.3f} "
               f"m - between the overhang slack and the gate's limit - is "
               f"refused={a['got_refused']} (required {a['want_refused']}) "
               f"at limit {a['limit_m']} m",
               measured=float(a["footprint_min_ground_m"] - a["tile_top_m"]),
               limit=float(a["limit_m"]),
               numbers=a,
               population="one constructed bed between the two candidate "
                          "limits")]


def _case_a_corner_of_open_air_is_not_buried():
    """THE DIRECTION A PIVOT SAMPLE IS ACTUALLY WRONG IN.

    Burial is "under the ground EVERYWHERE", so the governing reading is the
    footprint MINIMUM and a single HIGH corner buries nothing.  The way a
    pivot sample fails is therefore a FALSE REFUSAL: the centre of a 2 m
    tile stands under a mound while one corner is open air, and a
    pivot-only guard retires a piece a walker can see.  This arm was first
    written the other way round, was green under its own neuter, and
    asserted a falsehood - it is registered in the direction the instrument
    can be wrong.
    """
    a = _burial("a_corner_of_open_air_the_pivot_cannot_see_is_not_buried")
    return [_f("crossings", a["ok"],
               f"pivot ground {a['pivot_ground_m']} m over a tile top "
               f"{a['tile_top_m']} m but footprint minimum "
               f"{a['footprint_min_ground_m']} m under it -> "
               f"refused={a['got_refused']} (required {a['want_refused']})",
               measured=float(a["pivot_ground_m"]
                              - a["footprint_min_ground_m"]),
               limit=float(a["limit_m"]),
               numbers=a,
               population="one constructed bed with a single open corner")]


def _neuter_burial_guard_absent():
    _bw, _ = _deck_remedy_arms()
    return _bw._neuter_burial_guard_absent()


def _neuter_burial_samples_the_pivot():
    _bw, _ = _deck_remedy_arms()
    return _bw._neuter_burial_samples_the_pivot()


def _neuter_burial_uses_the_overhang_limit():
    _bw, _ = _deck_remedy_arms()
    return _bw._neuter_burial_uses_the_overhang_limit()


def _neuter_the_class_accessor_can_say_yes():
    """THE CHOKEPOINT REMOVED: an accessor that promotes an UNJUDGED answer.

    This is `if not ans["buildable"]` written out - the one-character
    consumer defect the accessor exists to make unspellable. Under it an
    answer nothing refused reports ACTIONABLE, which is precisely the state
    the whole crossings arm was in.
    """
    _bw, _ = _deck_remedy_arms()
    real = _bw.deck_remedy_class

    def says_yes(answer):
        got = real(answer)
        return "ACTIONABLE" if got == "UNKNOWN" else got

    _bw.deck_remedy_class = says_yes

    def undo():
        _bw.deck_remedy_class = real
    return undo


# --- CAN THIS REFUSAL EVER BE CLOSED ---------------------------------------
# THE STATE THESE PIN is T7-stathub-stenvik-x01's premise refusal, whose own
# prescribed remedy - "these stations belong to the TERRAIN WRITER" - is a
# terrain_write that `ledger/writer.py`'s flatten guard REFUSES, because a
# deck already stands there and a deck's own ground is flatten=FORBIDDEN by
# construction.  A gate that counts such a refusal beside an actionable one
# is reporting a work queue and an operator decision list added together.
# The owner tuples are `(x, z, radius_m, role, seq)`, the shape
# `settlements.forbidden_extents` returns; built here, never borrowed.
# TWO OWNERS AND ONE UNOWNED METRE, DELIBERATELY.  A refusal that names ONE
# owner for a many-owner repair is `check_repair_is_named`'s defect applied
# to ownership - MEASURED wrong in exactly that direction tonight, where a
# refusal reported "inside road_T9_wttown_dockshore" over a population that
# was 85 T9 AND 27 road_T5_temple_dockshore - so the fixture has two owners
# and a metre owned by nobody, and the case asserts the whole population.
_DECK_OWNER = [(10.0, 0.0, 0.5, "bridge_body_place", 5383),
               (12.0, 0.0, 0.5, "bridge_assembly", 21)]
_FAR_OWNER = [(400.0, 400.0, 0.5, "boathouse_place", 30)]


class _Writes:
    """The `WriteIndex.at` surface `contested_ground` uses, and nothing
    else: `name` is whose recorded terrain_write covers the point."""

    def __init__(self, name=""):
        self.name = name

    def at(self, _x, _z, margin=0.0, exact_only=False):
        return self.name


_NO_WRITES = _Writes("")
_ROAD_WRITES = _Writes("terrain_write T7-stathub-stenvik seq 2411")


def _termination_row(over_m: float = 0.412, many: bool = False):
    at = [[10.0, 0.0, over_m]]
    if many:
        at += [[12.0, 0.0, over_m], [200.0, 0.0, over_m]]
    return dict(crossing="X-a-b-x01",
                premise=dict(**{"pass": False}),
                bed_above_deck_at=at)


def _case_a_refusal_whose_repair_the_writer_forbids_says_so():
    t = crossing_termination(_termination_row(many=True), _DECK_OWNER,
                             _NO_WRITES)
    return [_f("crossings",
               t["termination"] == "ACTIONABLE"
               or len(t["ground_owners"]) < 2
               or t["stations_owned_by_nobody"] != 1,
               f"a premise refusal whose prescribed terrain write lands on "
               f"flatten=FORBIDDEN deck records is classed "
               f"{t['termination']}: {t['stations_refused']} of "
               f"{t['stations']} metre(s) refused, owners "
               f"{t['ground_owners']}, and "
               f"{t['stations_owned_by_nobody']} metre(s) owned by NOBODY. "
               "THE REFUSAL PRESCRIBES A WRITE ITS OWN WRITER FORBIDS, so "
               "no work by this section's owner can close it - it is an "
               "OPERATOR decision counted as a work item. AND EVERY OWNER "
               "IS NAMED, not just the worst: naming one owner for a "
               "many-owner repair is ' 36 counted, 1 named' applied to "
               "ownership, and the unowned metres - the only ones needing "
               "no decision from anybody - are invisible inside an "
               "owner-named refusal. MEASURED on Ulfsland: "
               "T7-stathub-stenvik-x01 is writer-refused on 13 of its 51 "
               "metres, nearest owner bridge_body_place seq 5383 at "
               "0.055 m",
               numbers=dict(termination=t["termination"],
                            stations_refused=t["stations_refused"],
                            ground_owners=t["ground_owners"],
                            owned_by_nobody=t["stations_owned_by_nobody"]))]


def _case_an_unread_instrument_is_out_of_order_not_actionable():
    row = _termination_row()
    no_owners = crossing_termination(row, None, _NO_WRITES)
    no_writes = crossing_termination(row, _FAR_OWNER, None)
    clear = crossing_termination(row, _FAR_OWNER, _NO_WRITES)
    contested = crossing_termination(row, _FAR_OWNER, _ROAD_WRITES)
    return [_f("crossings",
               no_owners["termination"] == "1E_OUT_OF_ORDER"
               and "flatten=FORBIDDEN" in str(no_owners.get("prerequisite"))
               and no_writes["termination"] == "1E_OUT_OF_ORDER"
               and "terrain_write" in str(no_writes.get("prerequisite"))
               and clear["termination"] == "ACTIONABLE"
               and contested["termination"] == "1B_CONTESTED",
               f"UNREAD IS NOT CLEAR, AND IT IS NOT UNKNOWN, on BOTH "
               f"instruments: owner set "
               f"unread -> {no_owners['termination']}, write footprints "
               f"unread -> {no_writes['termination']}, both read and "
               f"nobody holding the ground -> {clear['termination']}, both "
               f"read and another actor holding it -> "
               f"{contested['termination']} by {contested['contested_by']} "
               f"within {contested['contested_within_m']} m. "
               "`contested_by` is None BOTH when nobody holds the ground "
               "AND when the footprints could not be read, so a class that "
               "conflated them would report ACTIONABLE precisely where the "
               "gate cannot see - and a vertex can be WRITABLE AND STILL "
               "NOT BE YOURS, which no guard in this tree refuses",
               numbers=dict(owners_unread=no_owners["termination"],
                            writes_unread=no_writes["termination"],
                            clear=clear["termination"],
                            contested=contested["termination"]))]


def _neuter_termination_is_never_asked():
    """Put the OLD REFUSAL back: it names a remedy and never asks whether
    the one sanctioned write path would perform it, so an unclosable
    refusal is counted as work."""
    was = globals()["crossing_termination"]

    def blind(row, _owners, _writes=None, step_limit=STEP_LIMIT_PER_M):
        # THE SHAPE IS THE SAME, THE QUESTION IS GONE.  A neuter that drops
        # a key raises inside the case instead of letting the case's own
        # assertion fire, and an exception proves nothing.
        at = row.get("bed_above_deck_at") or []
        return dict(termination="ACTIONABLE", judged=True, stations=len(at),
                    stations_refused=0,
                    repairable_by_terrain_write=True, ground_owned_by=None,
                    ground_owner_distance_m=None, repair_footprint_m=None,
                    contested_by=None, contested_within_m=None,
                    ground_owners={}, contested_by_all={},
                    stations_owned_by_nobody=len(at),
                    why="the old refusal never asked")

    globals()["crossing_termination"] = blind

    def undo():
        globals()["crossing_termination"] = was
    return undo


def _neuter_termination_is_always_forbidden():
    """THE OVER-CORRECTION: class every premise refusal unclosable, which
    empties the work queue by declaring the work impossible."""
    was = globals()["crossing_termination"]

    def blind(row, _owners, _writes=None, step_limit=STEP_LIMIT_PER_M):
        at = row.get("bed_above_deck_at") or []
        return dict(termination="1A_FORBIDDEN", judged=True,
                    stations=len(at), stations_refused=len(at),
                    repairable_by_terrain_write=False,
                    ground_owned_by="whatever seq 0",
                    ground_owner_distance_m=0.0, repair_footprint_m=1.0,
                    contested_by=None, contested_within_m=1.0,
                    ground_owners={"whatever seq 0": len(at)},
                    contested_by_all={}, stations_owned_by_nobody=0,
                    why="the over-correction refuses everything")

    globals()["crossing_termination"] = blind

    def undo():
        globals()["crossing_termination"] = was
    return undo


# --- EVERY REFUSAL, NOT ONLY THE CROSSINGS ---------------------------------
# THE STATE THESE PIN is the one the full gate shipped on save 245daa7f:
# roads refused 87 times and 84 of the 87 carried NO TERMINATION CLASS AT
# ALL - obstruction 53, connectivity 11 and traversability 10, all silent -
# so the largest section in the tree was the only one whose refusals nobody
# could triage.  The fixture is built here and borrows no world: one refusal
# per arm, each carrying exactly the evidence its own emission site
# publishes.
_ROAD_OWNER = [(10.0, 0.0, 0.5, "bridge_body_place", 2809)]
_ROAD_FAR_OWNER = [(4000.0, 4000.0, 0.5, "boathouse_place", 30)]
_OWN_WRITES = _Writes("terrain_write road_N1_wnorth_shelf (seq 2754)")
_OTHER_WRITES = _Writes("terrain_write harbour_place (seq 24)")


def _road_refusals():
    """One hard refusal from each arm, shaped as `run` emits them."""
    def row(check, where=(10.0, 0.0), **n):
        return Finding(check=check, ok=False, message=f"{check} refusal",
                       where=where, numbers=dict(n))
    return [
        # an over-limit step: the remedy IS a terrain cut
        row("traversability", segment="N1-wnorth-shelf", overage_m=1.4417,
            carried_by_a_surface=False),
        # a vertex that returned two heights: no cut closes an ambiguity
        row("traversability", segment="N4-mid2-terminus",
            no_cut_proposed=True),
        # 25 of 457 pieces of a dvergr town standing in the carriageway -
        # NAMED, as `run` names them, and placed by no ledger record
        row("obstruction", segment="N4-mid2-terminus", obstruction_bodies=1,
            obstruction_pieces=25, obstruction_gap_m=None,
            obstruction_blocks=False, walker_width_m=0.98,
            blocker_pieces=[dict(prefab="blackmarble_2x2x2", x=10.0, y=31.0,
                                 z=0.5)]),
        # a destination severed by 163 m of nothing
        row("connectivity", segment="W5-brgs1-wsouth", first_link="gap",
            link_separation_m=163.4),
        # a destination severed by a building
        row("connectivity", segment="S7-stathub-wtnorth",
            first_link="blocked", obstruction_bodies=1,
            obstruction_pieces=10, obstruction_gap_m=0.85,
            obstruction_blocks=True, walker_width_m=0.98,
            blocker_pieces=[dict(prefab="stone_wall_4x2", x=10.0, y=31.0,
                                 z=-0.5)]),
        # a declared crossing with no deck on it, premise sound
        row("crossings", segment="S1", remedy_is_a_deck=True,
            deck_metres_missing=13),
    ]


def _case_every_refusal_this_section_makes_carries_a_class():
    fs = _road_refusals()
    census = road_termination(fs, _ROAD_OWNER, _NO_WRITES, spans={})
    got = [(f.check, (f.numbers or {}).get("termination_class")) for f in fs]
    missing = [c for c, k in got if not k]
    by = census[0].numbers["by_class"]
    return [_f("termination", not missing and not by.get("UNCLASSIFIED"),
               f"every arm's refusal carries a termination class: {got}; "
               f"census {by}. THE SHIPPED STATE WAS 84 OF 87 WITH NO CLASS "
               "AT ALL - obstruction 53, connectivity 11, traversability 10 "
               "emitted no class key, and only the crossings arm answered. "
               "A section that hands the operator 87 refusals and sorts 3 "
               "of them is reporting a work queue and a decision list added "
               "together, which is the one output this gate exists to "
               "refuse",
               measured=float(len(got) - len(missing)),
               limit=float(len(got)),
               numbers=dict(classes=dict(got), by_class=by),
               population="one refusal from each of the four arms")]


def _neuter_only_the_crossings_arm_is_classified():
    """RESTORE THE SHIPPED STATE VERBATIM: the classifier runs over the
    crossings rows and skips every other arm, which is what `run` did when
    `crossing_termination` was the only caller."""
    was = globals()["road_termination"]

    def crossings_only(findings, owners, writes=None, spans=None,
                       step_limit=STEP_LIMIT_PER_M, deck_remedy=None):
        return was([f for f in findings if f.check == "crossings"],
                   owners, writes, spans, step_limit, deck_remedy)

    globals()["road_termination"] = crossings_only

    def undo():
        globals()["road_termination"] = was
    return undo


def _case_a_blocked_destination_inherits_its_blockers_class():
    """A CONNECTIVITY ROW BLOCKED BY A CROSSING CARRIES THE CROSSING'S
    CLASS, AND WHERE NOTHING JUDGED THE CROSSING IT CARRIES NONE.

    MEASURED, Ulfsland 245daa7f, artefact /tmp/final_gate.json: the
    `boathouse` and `brgs1` connectivity rows both publish
    `link_crossing: "S1"`, `link_separation_m: 134.0` and
    `no_terrain_repair: "the remedy is a deck on S1, which writes no
    terrain"`, and both shipped ACTIONABLE.  Nothing had asked about S1.

    S1 is a `declared` crossing with `segment_id` null that belongs to NO
    segment - the gate reads 3 declared + 14 detected from
    roads/crossings.yaml while the builder reads 15 from
    roads/segments.yaml `segments[].crossings[]`, two populations for one
    obligation - so `spans`, which is indexed by the SEGMENT corpus, has
    no entry for it.  The inheritance therefore missed and the arm fell
    through to a hardcoded "a deck closes this", which is a class
    MANUFACTURED for a crossing this run had never judged.  Meanwhile the
    crossings arm, one branch over IN THE SAME LOOP, had already
    classified S1's own refusal.

    AND THE ANSWER MATTERS: DeckBuild measured S1's abutments at
    (-312,-62) applied 29.193 and (-326,-66) applied 31.513 against one
    contracted deck datum of 30.600 - a 0.95 m CUT at the west end and a
    1.354 m FILL at the east, with the east ground already under the
    30.0 m water plane.  Two rows sat on the work queue as "lay a deck"
    for a deck whose own abutment needs a fill nothing here writes.

    THREE STATES, ALL PINNED: inherited from `spans`, inherited from the
    crossing's own refusal row, and - where neither exists - 1C by
    SOURCE_CHANGE: both crossings arms passed a crossing this row is
    blocked at, which is the section disagreeing with itself.  Never
    ACTIONABLE, and never UNKNOWN, which is `NO_CLASS` spelled again.

    EVERY ARM IS A VERDICT, NEVER AN ASSERT, AND THAT IS NOT A STYLE
    CHOICE.  `run_cases` reads an `AssertionError` as a flip, so an
    assert-shaped proof of an INDEX is indistinguishable from a neuter that
    EMPTIED the index: the lookup raises, the harness reads the raise as
    the case coming out the other way, and a case that proved nothing is
    stamped PROVEN LOAD-BEARING.  This case's whole subject is a class
    inherited THROUGH an index, so it is the one most exposed to it.  Arm 4
    is the SURVIVOR: a destination whose crossing IS in `spans` must still
    inherit correctly under the same neuter, so a flip can never be
    satisfied by an inheritance that stopped answering at all.
    """
    def row(check, where=(0.0, 0.0), **n):
        return Finding(check=check, ok=False, message=f"{check} refusal",
                       where=where, numbers=dict(n))

    # 1 - THE SHAPE THAT SHIPPED: S1 in no segment corpus, its own
    # crossings refusal present in the same run.
    s1 = row("crossings", crossing="S1", remedy_is_a_deck=True,
             deck_metres_missing=13, segment=None)
    dest = row("connectivity", segment="S12-brgs1-boathouse",
               first_link="crossing", link_crossing="S1",
               link_separation_m=134.0)
    road_termination([s1, dest], _ROAD_OWNER, _NO_WRITES, spans={})
    arms = []

    def arm(name, ok, got, want):
        arms.append(dict(name=name, ok=bool(ok), got=got, want=want))

    arm("names its blocker",
        dest.numbers.get("inherited_from") == "S1",
        dest.numbers.get("inherited_from"), "S1")
    arm("inherits from the crossing's OWN refusal row",
        dest.numbers.get("inherited_from_the_crossings_refusal") is True,
        dest.numbers.get("inherited_from_the_crossings_refusal"), True)
    arm("carries exactly the crossing's class",
        dest.numbers.get("termination_class")
        == s1.numbers.get("termination_class"),
        dest.numbers.get("termination_class"),
        s1.numbers.get("termination_class"))

    # 2 - AND WHEN THE CROSSING BECOMES OPERATOR-BLOCKED, SO DOES THE
    # DESTINATION.  This is the whole point: the class is a pointer, not
    # a copy taken once.  `termination` is what the premise arm sets.
    s1b = row("crossings", crossing="S1", termination="1C_UNSERVED",
              repairable_by_terrain_write=False, repair_footprint_m=0.0,
              segment=None)
    dest2 = row("connectivity", segment="S12-brgs1-boathouse",
                first_link="crossing", link_crossing="S1",
                link_separation_m=134.0)
    road_termination([s1b, dest2], _ROAD_OWNER, _NO_WRITES, spans={})
    arm("an operator-blocked crossing blocks its destination",
        dest2.numbers.get("termination_class") == "1C_UNSERVED",
        dest2.numbers.get("termination_class"), "1C_UNSERVED")

    # 3 - NOTHING JUDGED IT, SO NO DECK IS A KNOWN REMEDY.  Not ACTIONABLE:
    # a class invented to avoid an absence is the defect this classifier
    # exists to refuse.  And not UNKNOWN: two arms of this section
    # disagreeing about one crossing is closed by a change to its source.
    alone = row("connectivity", segment="S12-brgs1-boathouse",
                first_link="crossing", link_crossing="S99",
                link_separation_m=134.0)
    road_termination([alone], _ROAD_OWNER, _NO_WRITES, spans={})
    arm("a crossing NOTHING judged makes the destination 1C by source",
        alone.numbers.get("termination_class") == "1C_UNSERVED"
        and alone.numbers.get("termination_family") == "SOURCE_CHANGE",
        (alone.numbers.get("termination_class"),
         alone.numbers.get("termination_family")),
        ("1C_UNSERVED", "SOURCE_CHANGE"))
    arm("and it proposes no write",
        alone.numbers.get("repairable_by_terrain_write") is False,
        alone.numbers.get("repairable_by_terrain_write"), False)

    # 4 - THE INHERITANCE FROM `spans` STILL WORKS AND STILL WINS.  It is
    # the richer reading - the premise arm measured the crossing against
    # its own prescribed write - so a second source must not shadow it.
    viaspans = row("connectivity", segment="S12-brgs1-boathouse",
                   first_link="crossing", link_crossing="S1",
                   link_separation_m=134.0)
    road_termination(
        [row("crossings", crossing="S1", remedy_is_a_deck=True,
             deck_metres_missing=13, segment=None), viaspans],
        _ROAD_OWNER, _NO_WRITES,
        spans={"S1": {"termination": {"judged": True,
                                      "termination": "1A_FORBIDDEN",
                                      "repairable_by_terrain_write": False,
                                      "repair_footprint_m": 0.0,
                                      "ground_owned_by": "deck_place seq 7",
                                      "ground_owner_distance_m": 0.4,
                                      "contested_by": None,
                                      "contested_within_m": 0.0}}})
    # ARM 4 IS THE SURVIVOR, and it is what makes the neuter a proof.  The
    # neuter restores "a crossing `spans` does not hold is assumed
    # buildable", which can only reach rows whose lookup MISSED.  A
    # destination whose crossing IS in `spans` must therefore come through
    # it unchanged - and if it does not, the neuter emptied the
    # inheritance rather than restoring the defect.
    arm("a crossing `spans` HAS still wins, under the neuter as well",
        viaspans.numbers.get("termination_class") == "1A_FORBIDDEN",
        viaspans.numbers.get("termination_class"), "1A_FORBIDDEN")
    arm("and `spans` is not shadowed by the refusal-row index",
        not viaspans.numbers.get("inherited_from_the_crossings_refusal"),
        viaspans.numbers.get("inherited_from_the_crossings_refusal"), False)
    bad = [a for a in arms if not a["ok"]]
    return [_f("connectivity/inheritance", not bad,
               ("a destination blocked by a crossing inherits that "
                "crossing's class: S1 in no segment corpus is inherited "
                "from its own refusal row, an operator-blocked S1 makes "
                "the destination operator-blocked, an unjudged crossing "
                "makes it 1C by source, and `spans` still wins where it has "
                f"an answer - all {len(arms)} arms hold"
                if not bad else
                "the inheritance did not hold: "
                + "; ".join(f"{a['name']} got {a['got']!r}, "
                            f"required {a['want']!r}" for a in bad)),
               measured=float(len(arms) - len(bad)), limit=float(len(arms)),
               numbers=dict(
                   arms=arms,
                   from_the_crossings_refusal=dest.numbers.get(
                       "termination_class"),
                   operator_blocked=dest2.numbers.get("termination_class"),
                   never_judged=alone.numbers.get("termination_class"),
                   from_spans=viaspans.numbers.get("termination_class")),
               population="the four states a crossing-blocked destination "
                          "can be in, each scored as a verdict")]


def _neuter_a_missing_crossing_is_assumed_buildable():
    """RESTORE THE SHIPPED STATE: when `spans` has no entry for the
    crossing that blocks a destination, assume a deck closes it.

    That is the line that put `boathouse` and `brgs1` on the work queue
    for a deck whose own abutment needs a 1.354 m fill.
    """
    was = globals()["road_termination"]

    def assume_buildable(findings, owners, writes=None, spans=None,
                         step_limit=STEP_LIMIT_PER_M, deck_remedy=None):
        out = was(findings, owners, writes, spans, step_limit, deck_remedy)
        for f in findings:
            n = f.numbers or {}
            if (f.check == "connectivity"
                    and n.get("first_link") == "crossing"
                    and not ((spans or {}).get(n.get("link_crossing")) or {})
                    .get("termination", {}).get("judged")):
                n.pop("inherited_from", None)
                n.pop("inherited_from_the_crossings_refusal", None)
                n["repairable_by_terrain_write"] = True
                n["repair_footprint_m"] = 0.0
                n["contested_by"] = None
                n["contested_within_m"] = 0.0
                n["termination_class"] = ("ACTIONABLE" if owners is not None
                                          else "UNKNOWN")
        return out

    globals()["road_termination"] = assume_buildable

    def undo():
        globals()["road_termination"] = was
    return undo


def _case_a_step_inside_a_declared_span_is_the_crossings():
    """A STEP BETWEEN THE BANKS OF A DECLARED CROSSING CARRIES THE
    CROSSING'S CLASS; A STEP ON THE APPROACH STAYS THE ROAD'S.

    MEASURED on the Fresh42 drive-2 save a521b70356e7: four S4-meadhall-
    southeast traversability refusals, stations 341-346, 1.138 m worst,
    every one inside S4-meadhall-southeast-x01's banks (396,-424) and
    (396,-450), were published ACTIONABLE with a 1.885 m cut prescribed at
    the bank the deck lands on - while that crossing's own row, in the same
    loop, said the remedy is a deck.  The shape is rebuilt here to scale.

    TWO ARMS, BOTH VERDICTS.  The inside step must inherit the crossing's
    class and name it; the approach step, one metre short of the bank, must
    come out EXACTLY as it does with no span declared at all - so a neuter
    that made every step inherit could not pass either.
    """
    def row(check, where=(396.0, -424.0), **n):
        return Finding(check=check, ok=False, message=f"{check} refusal",
                       where=where, measured=n.pop("measured", None),
                       numbers=dict(n))

    def step(at):
        return row("traversability", where=(at[0], at[1]), measured=1.138,
                   segment="S4-meadhall-southeast", station=341,
                   ground_only_m=1.138, lattice_m=1.0, overage_m=0.3567,
                   at=list(at), ground_rise_m=-1.138,
                   carried_by_a_surface=False)

    spans = {"S4-meadhall-southeast-x01": {"bank_a": [396.0, -424.0],
                                           "bank_b": [396.0, -450.0]}}
    crossing = row("crossings", crossing="S4-meadhall-southeast-x01",
                   termination="1C_UNSERVED", repair_footprint_m=0.0,
                   repairable_by_terrain_write=False,
                   segment="S4-meadhall-southeast")
    inside = step((396.0, -424.0, 396.0, -425.0))
    approach = step((396.0, -423.0, 396.0, -424.0))
    road_termination([crossing, inside, approach], _ROAD_FAR_OWNER,
                     _NO_WRITES, spans=spans)
    control = step((396.0, -423.0, 396.0, -424.0))
    road_termination([control], _ROAD_FAR_OWNER, _NO_WRITES, spans={})
    ni, na, nc = inside.numbers, approach.numbers, control.numbers
    arms = [
        ("the inside step names its crossing",
         ni.get("inside_crossing"), "S4-meadhall-southeast-x01"),
        ("and carries exactly the crossing's class",
         ni.get("termination_class"), "1C_UNSERVED"),
        ("and proposes no cut", ni.get("repairable_by_terrain_write"),
         False),
        ("the approach step is not the crossing's",
         na.get("inside_crossing"), None),
        ("and is classified as if no span were declared",
         na.get("termination_class"), nc.get("termination_class")),
    ]
    bad = [(nm, got, want) for nm, got, want in arms if got != want]
    return [_f("traversability/inheritance", not bad,
               ("a step between the banks of S4-meadhall-southeast-x01 "
                "carries that crossing's class and proposes no cut, and the "
                f"approach step one metre short is classified "
                f"{na.get('termination_class')} exactly as with no span - "
                f"all {len(arms)} arms hold")
               if not bad else
               "the inheritance did not hold: " + "; ".join(
                   f"{nm} got {got!r}, required {want!r}"
                   for nm, got, want in bad),
               measured=float(len(arms) - len(bad)), limit=float(len(arms)),
               numbers=dict(inside=ni.get("termination_class"),
                            approach=na.get("termination_class"),
                            control=nc.get("termination_class"),
                            failed=[nm for nm, _g, _w in bad]),
               population="two traversability steps one metre apart, one "
                          "inside a declared span and one on its approach")]


def _neuter_a_step_in_a_span_is_the_roads():
    """THE DEFECT: every traversability step is a terrain cut, including
    the ones between the banks of a declared deck."""
    global TRAVERSABILITY_INSIDE_A_SPAN
    TRAVERSABILITY_INSIDE_A_SPAN = False

    def undo():
        global TRAVERSABILITY_INSIDE_A_SPAN
        TRAVERSABILITY_INSIDE_A_SPAN = True
    return undo


def _case_a_missing_deck_is_work_iff_the_build_lays_decks():
    """THE CLASS OF A MISSING DECK IS READ OFF THE BUILD, NOT TYPED.

    MEASURED on drive 2's frozen Fresh42 save a521b70356e7: S4-meadhall-
    southeast-x01 and T3-temple-meadhall-x01 carry no deck, the builder's
    own preconditions refuse neither, and both went to the board UNKNOWN -
    with the four S4 steps inside S4-x01's span inheriting it, 6 of 18
    roads rows.  Whether a deck gets laid is a question about
    `buildstage.BUILD_STEPS`, and the drive-2 table had no step running the
    deck builder.

    THREE BUILD TABLES, ONE ROW EACH, ALL VERDICTS.  The shipped table with
    any builder step removed must say 1C_UNSERVED; the same table plus a
    step whose argv runs roads/boardwalk.py must say ACTIONABLE, and the
    step inside the span must carry it; a step that NAMES the builder but
    runs no command is not a builder, and stays 1C.  The swap is of
    `buildstage.BUILD_STEPS` itself, so the arm proves the class follows
    the table the gate reads at run time - the moment a step is declared.
    """
    from dataclasses import replace as dc_replace          # noqa: PLC0415
    crossing_build_step()                    # puts buildstage on sys.path
    import boardwalk                                        # noqa: PLC0415
    import buildstage                                       # noqa: PLC0415
    builder = Path(boardwalk.__file__).resolve().relative_to(
        I.JUMPSTART.resolve()).as_posix()
    shipped = buildstage.BUILD_STEPS
    without = tuple(st for st in shipped if builder not in st.owner)
    template = next(st for st in without if st.argv is not None)
    decks = dc_replace(template, ordinal=99, name="decks_fixture",
                       owner=f"{builder} --from-network", inherited=False)
    named_only = dc_replace(decks, name="decks_declared_only", argv=None)
    x01 = "S4-meadhall-southeast-x01"
    spans = {x01: {"bank_a": [396.0, -424.0], "bank_b": [396.0, -450.0]}}
    unjudged = {x01: dict(crossing_id=x01, buildable=None, refused_by=[],
                          asked=["segment_terrain_write", "span_coverage"],
                          unasked=["abutment", "bank_regrade_reach",
                                   "structural"],
                          sub_classes=[], implies=None, numbers={})}

    def classed(table):
        deck = Finding(check="crossings", ok=False, message="no deck",
                       where=(396.0, -424.0),
                       numbers=dict(crossing=x01, remedy_is_a_deck=True,
                                    segment="S4-meadhall-southeast"))
        step = Finding(check="traversability", ok=False, message="a step",
                       where=(396.0, -424.0), measured=1.138,
                       numbers=dict(segment="S4-meadhall-southeast",
                                    at=[396.0, -424.0, 396.0, -425.0],
                                    ground_rise_m=-1.138))
        buildstage.BUILD_STEPS = table
        try:
            road_termination([deck, step], _ROAD_FAR_OWNER, _NO_WRITES,
                             spans=spans, deck_remedy=unjudged)
        finally:
            buildstage.BUILD_STEPS = shipped
        return (deck.numbers.get("termination_class"),
                step.numbers.get("termination_class"))

    arms = [("no step runs the builder", classed(without),
             ("1C_UNSERVED", "1C_UNSERVED")),
            ("a step runs the builder", classed(without + (decks,)),
             ("ACTIONABLE", "ACTIONABLE")),
            ("a step that names the builder and runs nothing",
             classed(without + (named_only,)),
             ("1C_UNSERVED", "1C_UNSERVED"))]
    bad = [(nm, got, want) for nm, got, want in arms if got != want]
    return [_f("crossings", not bad,
               (f"a missing deck the builder does not refuse is "
                f"{arms[1][1][0]} when a build step runs {builder} and "
                f"{arms[0][1][0]} when none does; the step inside its span "
                f"carries the same class; all {len(arms)} tables hold")
               if not bad else
               "the missing-deck class did not follow the build table: "
               + "; ".join(f"{nm}: got (deck, step) {got}, required {want}"
                           for nm, got, want in bad),
               measured=float(len(arms) - len(bad)), limit=float(len(arms)),
               numbers=dict(arms={nm: got for nm, got, _w in arms},
                            builder=builder, failed=[b[0] for b in bad]),
               population="one undecked crossing and one step inside its "
                          "span, under three build tables")]


def _neuter_the_missing_deck_class_is_typed():
    """THE DEFECT ONE STEP ON: a class written down instead of derived.
    `1C_UNSERVED` is right for the drive-2 table and wrong the moment a
    deck step is declared - the same row, typed."""
    global _MISSING_DECK_CLASS_TYPED
    _MISSING_DECK_CLASS_TYPED = "1C_UNSERVED"

    def undo():
        global _MISSING_DECK_CLASS_TYPED
        _MISSING_DECK_CLASS_TYPED = None
    return undo


def _case_a_body_in_the_carriageway_is_not_a_missing_tool():
    """1D_OBSTRUCTED, AND THE TWO CLASSES IT IS NOT.

    A standing body prescribes demolition or a re-route.  Both of those
    ARE performed in this tree, so `1C_UNSERVED` - 'no tool here does it' -
    is false; and neither is a terrain write, so ACTIONABLE - 'a repair
    exists, the writer permits it, it destroys nothing' - is false in all
    three clauses.  MEASURED, the worst of the 47: `blackmarble_2x2x2`, a
    dvergr town at (-1093.2,7484.4), 25 of 457 pieces inside the
    carriageway.
    """
    fs = [f for f in _road_refusals() if f.check == "obstruction"]
    road_termination(fs, _ROAD_OWNER, _NO_WRITES, spans={},
                     placements=_empty_chain())
    n = fs[0].numbers
    return [_f("termination", n["termination_class"] == "1D_OBSTRUCTED",
               f"a body of {n['obstruction_pieces']} standing pieces in the "
               f"carriageway is classed {n['termination_class']} with the "
               f"terrain question ASKED and answered - repair footprint "
               f"{n['repair_footprint_m']} m, writer says "
               f"{n['repairable_by_terrain_write']}, owner "
               f"{n['ground_owned_by']}. NO CUT REMOVES A BUILDING, and a "
               "row that says so by claiming no tool in this tree performs "
               "the remedy is wrong twice over: two do, and both are "
               "destructive, which is exactly the decision an operator is "
               "owed",
               numbers=dict(termination_class=n["termination_class"],
                            repair_footprint_m=n["repair_footprint_m"],
                            bodies=n["obstruction_bodies"]))]


def _neuter_a_body_has_no_class_of_its_own():
    """RESTORE THE FORCED FIT: an obstruction refusal classified purely on
    its terrain answer, with the body census never consulted.  This is the
    shape the class would have had without `1D_OBSTRUCTED` - 'no tool in
    this tree performs the remedy' over 54 rows whose remedy two tools
    perform."""
    was = globals()["road_termination"]

    def no_bodies(findings, owners, writes=None, spans=None,
                  step_limit=STEP_LIMIT_PER_M, deck_remedy=None, **kw):
        out = was(findings, owners, writes, spans, step_limit,
                  deck_remedy, **kw)
        from .settlements import termination_class      # noqa: PLC0415
        for f in findings:
            if f.ok or f.advisory or not f.numbers:
                continue
            n = dict(f.numbers)
            n.pop("obstruction_bodies", None)
            f.numbers["termination_class"] = termination_class(n)
        return out

    globals()["road_termination"] = no_bodies

    def undo():
        globals()["road_termination"] = was
    return undo


def _case_a_road_contesting_its_own_carriageway_is_not_1B():
    """SELF-CONTEST IS NOT CONTEST, and uncorrected it swallows every road
    on every world.

    MEASURED by RoadsClassify: all three of this section's 1B_CONTESTED
    rows named the refusal's OWN segment as the contesting write - T13's
    step against `road_T13_temple_harboursouth` seq 338, N4's vertex
    against `road_N4_mid2_terminus` seq 2754.  A road's own carriageway is
    ALWAYS inside its own recorded `terrain_write` by construction, so a
    1B that does not exclude the actor's own record reclassifies every road
    repair anybody ever makes as an operator decision about somebody else's
    work.  The shared helper excludes it - and it can only do so if THIS
    section states the refusal's own identity, which is what this case
    pins.
    """
    mine = _road_refusals()[:1]
    road_termination(mine, _ROAD_FAR_OWNER, _OWN_WRITES, spans={},
                     placements=_empty_chain())
    theirs = _road_refusals()[:1]
    road_termination(theirs, _ROAD_FAR_OWNER, _OTHER_WRITES, spans={},
                     placements=_empty_chain())
    a = mine[0].numbers["termination_class"]
    b = theirs[0].numbers["termination_class"]
    return [_f("termination", a == "ACTIONABLE" and b == "1B_CONTESTED",
               f"a cut inside the refusal's OWN segment's terrain_write is "
               f"{a}; the same cut inside ANOTHER actor's write is {b}. "
               "Both directions, because a rule that only ever drops rows "
               "out of 1B is a way of emptying the decision list rather "
               "than of reading it",
               numbers=dict(self_contested=a, other_contested=b))]


def _neuter_the_refusal_does_not_state_its_own_actor():
    """RESTORE THE DEFECT ON THIS SIDE OF THE WIRE: roads stops telling the
    shared classifier whose refusal this is, so `_contesters_other_than_self`
    has nothing to compare against and every road repair reads 1B."""
    was = globals()["road_termination"]

    def anonymous(findings, owners, writes=None, spans=None,
                  step_limit=STEP_LIMIT_PER_M, deck_remedy=None, **kw):
        out = was(findings, owners, writes, spans, step_limit,
                  deck_remedy, **kw)
        from .settlements import termination_class      # noqa: PLC0415
        for f in findings:
            if f.ok or f.advisory or not f.numbers:
                continue
            n = {k: v for k, v in f.numbers.items()
                 if k not in ("own_actor", "segment")}
            f.numbers["termination_class"] = termination_class(n)
        return out

    globals()["road_termination"] = anonymous

    def undo():
        globals()["road_termination"] = was
    return undo


def _empty_chain():
    """A ledger that was READ and placed nothing: every blocker a fixture
    names is then FOREIGN, which is the world these operator-class cases
    describe (a dvergr town, a stranger's write).  Not None - None is an
    unread chain, and an unread blocker is 1E, never foreign."""
    return T.Placements([], "fixture: a chain that placed nothing")


# THE DRIVE-3 SHAPE, CONSTRUCTED.  T1-portalhub-temple-x01's deck: step 6.5
# `decks` wrote `bridge_body_place` records and their wire put wood_poles
# on the carriageway the road section then refused as "36 of 134 pieces of
# one body".  Three poles here; the wire is z,x,y as the game parses it.
_DECK_POLES = ((-131.0, 32.9, -410.8), (-130.2, 32.9, -412.1),
               (-129.4, 32.9, -409.6))


def _deck_chain():
    """A chain whose step-6.5 deck placed `_DECK_POLES`, plus a road write
    and a pad write for the contest arm."""
    wire = "; ".join(f"spawn_object wood_pole pos={z},{x},{y} rot=0,0,0"
                     for x, y, z in _DECK_POLES)
    return T.Placements([
        dict(seq=631, op="terrain_write", actor="fixture",
             params=dict(role="site_pad", name="complete-station-hub",
                         site_id="complete-station-hub"),
             meta=dict(build_step="2", build_unit="complete-station-hub")),
        dict(seq=1988, op="terrain_write", actor="fixture",
             params=dict(role="road_segment", name="road_S13_stathub_dock"),
             meta=dict(build_step="4", build_unit="S13-stathub-dock")),
        dict(seq=2682, op="spawn_plan", actor="fixture",
             params=dict(role="bridge_body_place",
                         site_id="T1-portalhub-temple-x01",
                         command_count=len(_DECK_POLES)),
             meta=dict(build_step="6.5",
                       build_unit="T1-portalhub-temple-x01"),
             wire=[wire])], "fixture: drive-3 deck chain")


def _body_row(pieces):
    return Finding(
        check="obstruction", ok=False,
        message=f"{len(pieces)} of 134 pieces of one body stand in the "
                "carriageway (go round); wood_pole",
        where=(-131.0, -410.8),
        numbers=dict(pieces_in_road=len(pieces), pieces_in_body=134,
                     obstruction_bodies=1, obstruction_pieces=len(pieces),
                     obstruction_gap_m=None, obstruction_blocks=False,
                     walker_width_m=0.98, blocker_pieces=pieces))


def _case_our_own_deck_in_our_own_carriageway_is_factory_work():
    """A BODY THE LEDGER PLACED IS THE FACTORY'S WORK, NOT AN OPERATOR'S.

    MEASURED on drive 3's save 05eb7c6c05de: `roads/obstruction` "36 of 134
    pieces of one body stand in the carriageway: wood_pole" - every one of
    the 36 a `bridge_body_place` command of T1-portalhub-temple-x01 (seq
    2682-2684, build step 6.5 `decks`) - was 1D_OBSTRUCTED, "an operator
    decision", on a board that reported ACTIONABLE 0."""
    row = _body_row([dict(prefab="wood_pole", x=x, y=y, z=z)
                     for x, y, z in _DECK_POLES])
    road_termination([row], _ROAD_OWNER, _NO_WRITES, spans={},
                     placements=_deck_chain())
    n = row.numbers
    steps = " ".join(n.get("owner_step") or ())
    ok = (n["termination_class"] == "ACTIONABLE"
          and n.get("blocker_ownership") == "ours"
          and "decks" in steps and "roads/boardwalk.py" in steps
          and [o["seq"] for o in n.get("blocker_owner") or ()] == [2682])
    return [_f("termination", ok,
               f"3 wood_poles our step-6.5 deck placed in our own "
               f"carriageway classed {n['termination_class']} "
               f"(ownership {n.get('blocker_ownership')}, owner "
               f"{n.get('owner_step')}); the placing step owns the repair",
               where=(-131.0, -410.8),
               numbers=dict(termination_class=n["termination_class"],
                            owner_step=n.get("owner_step")))]


def _case_a_foreign_body_in_the_carriageway_stays_1D():
    """THE OTHER DIRECTION: a body the ledger did NOT place - the same
    shape, the same prefab, one metre off every commanded pivot - keeps its
    operator class.  A rule that only ever moves rows out of 1D is a way of
    emptying the decision list rather than of reading it."""
    row = _body_row([dict(prefab="wood_pole", x=x + 1.0, y=y, z=z)
                     for x, y, z in _DECK_POLES])
    road_termination([row], _ROAD_OWNER, _NO_WRITES, spans={},
                     placements=_deck_chain())
    n = row.numbers
    return [_f("termination", n["termination_class"] == "1D_OBSTRUCTED"
               and n.get("blocker_ownership") == "foreign",
               f"3 wood_poles no ledger record placed classed "
               f"{n['termination_class']} (ownership "
               f"{n.get('blocker_ownership')})", where=(-130.0, -410.8),
               numbers=dict(termination_class=n["termination_class"]))]


def _case_an_unread_chain_is_not_a_foreign_blocker():
    """UNREAD IS NOT FOREIGN.  With the chain unreadable the same deck body
    is 1E naming the reading - the factory owes it - never 1D."""
    row = _body_row([dict(prefab="wood_pole", x=x, y=y, z=z)
                     for x, y, z in _DECK_POLES])
    road_termination([row], _ROAD_OWNER, _NO_WRITES, spans={},
                     placements=None)
    n = row.numbers
    return [_f("termination", n["termination_class"] == "1E_OUT_OF_ORDER"
               and "Placements" in str(n.get("prerequisite")),
               f"a body whose chain was not read classed "
               f"{n['termination_class']}, prerequisite "
               f"{n.get('prerequisite')!r}", where=(-131.0, -410.8),
               numbers=dict(termination_class=n["termination_class"]))]


def _case_a_road_contesting_our_own_pad_is_ours_to_reconcile():
    """1B BETWEEN TWO OF OUR OWN CLAIMS IS OURS TO RECONCILE.  MEASURED on
    drive 3: road_S13_stathub_dock A (and four others) climbs 0.9072 m onto
    `complete-station-hub`, and the cut lands in the pad's own write (seq
    631, step 2) - classed 1B as if the pad were a stranger's.  The LATER
    claim built against the earlier one's standing work, so it owns it."""
    row = Finding(check="traversability", ok=False, message="a step",
                  where=(91.0, 362.0), measured=0.9072,
                  numbers=dict(segment="road_S13_stathub_dock",
                               overage_m=0.126, carried_by_a_surface=False))
    road_termination([row], _ROAD_FAR_OWNER,
                     _Writes("terrain_write complete-station-hub (seq 631)"),
                     spans={}, placements=_deck_chain())
    n = row.numbers
    steps = " ".join(n.get("owner_step") or ())
    return [_f("termination", n["termination_class"] == "ACTIONABLE"
               and n.get("termination_reclassed_from") == "1B_CONTESTED"
               and "road_ground" in steps,
               f"a road cut inside our own pad's write classed "
               f"{n['termination_class']} (from "
               f"{n.get('termination_reclassed_from')}), owner "
               f"{n.get('owner_step')}", where=(91.0, 362.0),
               numbers=dict(termination_class=n["termination_class"]))]


class _StubCtx:
    """A ctx with `Ctx`'s OWN attribute contract: anything the harness did
    not put on it raises `AttributeError` by the same route
    `accept/__main__.py` raises it, naming itself."""

    def __init__(self, **kw):
        self.__dict__.update(kw)
        self.logged: list[str] = []

    def log(self, msg):
        self.logged.append(msg)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        raise AttributeError(
            f"no instrument named {name!r}: ctx exposes the readings and "
            "every name in accept/instruments.py")


class _Args:
    def __init__(self, world):
        self.world = world


def _case_a_ctx_wiring_bug_is_never_an_unreadable_chain():
    """THE DEFECT THAT MADE 1A_FORBIDDEN ZERO OF 106 REFUSALS GATE-WIDE.

    `owner_instruments` used to resolve `ctx.world` INSIDE the try whose
    `except Exception` exists to survive an unparseable ledger.  `Ctx` has
    no `world` - the name lives on `ctx.args.world` - so every run logged
    "the owner set is unreadable" and classified every refusal UNKNOWN
    while reporting a clean instrument failure.  MEASURED on save 245daa7f:
    every crossings row `repairable_by_terrain_write: null`,
    `ground_owners: {}`, and not one 1A on a world with 132,148 owned
    points.  Settlements committed the identical line in the same run.

    The two directions: a bad CHAIN is survivable and logged; a bad
    ARGUMENT this function knows the shape of must RAISE.
    """
    bad_chain = _StubCtx(args=_Args("Ulfsland"),
                         ledger="/nonexistent/ledger.jsonl",
                         ledger_dir="/nonexistent")
    owners, _writes = owner_instruments(bad_chain)
    survived = owners is None and bool(bad_chain.logged)
    no_args = _StubCtx(ledger="/nonexistent/ledger.jsonl",
                       ledger_dir="/nonexistent")
    try:
        owner_instruments(no_args)
        raised = False
    except AttributeError:
        raised = True
    return [_f("termination", survived and raised,
               f"an unreadable CHAIN is survived and logged "
               f"({survived}); a ctx that cannot answer for the world's "
               f"NAME raises ({raised}) instead of being reported as an "
               "unreadable chain. THE SHIPPED FORM SWALLOWED BOTH: "
               "`ctx.world` on a Ctx that only has `ctx.args.world` raised "
               "AttributeError inside the ledger guard, so the owner set "
               "was never read on any run and 1A_FORBIDDEN was 0 of 106 "
               "refusals gate-wide - not because nothing is forbidden but "
               "because nothing was asked",
               numbers=dict(bad_chain_survived=survived,
                            bad_ctx_raised=raised,
                            logged=bad_chain.logged[:1]))]


def _neuter_the_world_name_is_resolved_inside_the_guard():
    """RESTORE THE DEFECT VERBATIM: every argument resolved inside the try,
    so a ctx-wiring bug comes back as `owners=None` and a log line."""
    was = globals()["owner_instruments"]

    def laundered(ctx):
        from .settlements import (forbidden_extents,    # noqa: PLC0415
                                  live_records)
        from .harbours import WriteIndex, write_footprints  # noqa: PLC0415
        owners = writes = None
        try:
            owners = forbidden_extents(live_records(ctx.ledger),
                                       ctx.ledger_dir, ctx.world)
        except Exception as exc:                        # noqa: BLE001
            ctx.log(f"flatten=FORBIDDEN owner set unreadable ({exc})")
        try:
            writes = WriteIndex(write_footprints(ctx.ledger), margin_max=8.0)
        except Exception as exc:                        # noqa: BLE001
            ctx.log(f"terrain_write footprints unreadable ({exc})")
        return owners, writes

    globals()["owner_instruments"] = laundered

    def undo():
        globals()["owner_instruments"] = was
    return undo

# --- THE REPORT THAT KILLED ITSELF -----------------------------------------
# THE FIXTURE IS CONSTRUCTED, and the two sources below differ by exactly
# one token each - the name of a local - because that token IS the state
# under test.  The real sighting is quoted in `shadowed_closures`; borrowing
# it would make this case go quiet the day that function is renamed.
_SHADOWED_SRC = '''
def run(ctx):
    report = {"a": 1}
    channels = {"m": 2}

    def render(w):
        w(report["a"])
        if w:
            report = channels
            w(report)
    return render
'''
_CLEAN_SRC = _SHADOWED_SRC.replace("report = channels", "chan = channels") \
                          .replace("w(report)\n", "w(chan)\n")
# AND THE LEGAL IDIOM, because a check that refuses the correct form is
# worse than no check: everyone learns to ignore it.  `nonlocal` is the
# declaration that makes a rebind deliberate.
_NONLOCAL_SRC = '''
def run(ctx):
    off = 0

    def i32():
        nonlocal off
        v = off
        off += 4
        return v
    return i32
'''


def _case_a_report_never_shadows_the_report_it_reads():
    """A NESTED FUNCTION THAT REBINDS A NAME IT ALREADY READ IS DEAD ON
    EVERY CALL, and nothing else in this gate can see it.

    MEASURED IN THIS FILE, TWICE IN ONE FUNCTION.  `render` read `c` - the
    connectivity report - at its CONNECTIVITY line and later bound
    `c = x["channels"]` inside the duplicate-declaration branch; it read
    `v` - the coverage report - and later ran `for k, v in bt.items()`.
    Python decides scope STATICALLY, so neither binding had to execute:
    every render of the largest section in this gate raised
    `UnboundLocalError: cannot access local variable 'c'`, after printing
    the population and the traversability block - far enough to look like
    it had worked.  The module imports, the section RUNS, the JSON is
    complete and the verdict is right; only the thing a human reads dies.
    """
    bad = shadowed_closures(_SHADOWED_SRC)
    clean = shadowed_closures(_CLEAN_SRC)
    legal = shadowed_closures(_NONLOCAL_SRC)
    mine = shadowed_closures(
        Path(__file__).read_text(encoding="utf-8"))
    return [_f("termination",
               len(bad) == 1 and bad[0]["name"] == "report"
               and not clean and not legal and not mine,
               f"a nested function that READS a name at line "
               f"{bad[0]['reads_at'] if bad else '?'} and REBINDS it at "
               f"{bad[0]['rebinds_at'] if bad else '?'} is refused "
               f"({len(bad)} row); the same function with its own name for "
               f"its own variable is accepted ({len(clean)}); a declared "
               f"`nonlocal` cursor is accepted ({len(legal)}); and THIS "
               f"MODULE carries {len(mine)}. A check that only refused "
               "would refuse the correct idiom too, and a check that only "
               "accepted is how this shipped",
               measured=float(len(bad)), limit=1.0,
               numbers=dict(defect=bad, clean=len(clean),
                            nonlocal_idiom=len(legal), this_module=mine),
               population="two constructed sources differing by one "
                          "identifier, the legal nonlocal idiom, and every "
                          "nested function in this module")]


def _neuter_shadowing_is_never_looked_for():
    """RESTORE THE STATE THAT SHIPPED: nobody asks, so a report that cannot
    render reads exactly like one that can."""
    was = globals()["shadowed_closures"]
    globals()["shadowed_closures"] = lambda _src: []

    def undo():
        globals()["shadowed_closures"] = was
    return undo


def _case_an_unread_owner_set_classifies_nothing_as_work():
    """UNREAD IS NOT CLEAR, over the whole section rather than one arm.

    With the owner set unread, no refusal whose remedy is a terrain write
    may be reported ACTIONABLE - and none whose remedy is a deck either,
    because the gate cannot then say what it is looking at.  A body in the
    carriageway is the ONE class that survives, and deliberately: it is a
    positive measurement from a different instrument, so it does not
    become unknown because a second instrument went dark.
    """
    fs = _road_refusals()
    # THE LEDGER'S PLACEMENTS ARE READ (and placed none of these bodies):
    # this case isolates the two OWNERSHIP instruments; an unread chain is
    # `_case_an_unread_chain_is_not_a_foreign_blocker`'s.
    census = road_termination(fs, None, None, spans={},
                              placements=_empty_chain())
    by = census[0].numbers["by_class"]
    obstructed = sum(1 for f in fs
                     if f.numbers["termination_class"] == "1D_OBSTRUCTED")
    return [_f("termination",
               by.get("ACTIONABLE", 0) == 0 and obstructed == 2
               and census[0].numbers["owners_read"] is False,
               f"with both ownership instruments unread the census is {by} "
               f"- 0 ACTIONABLE, and the {obstructed} rows with a BODY in "
               "them keep their class because a standing body is a "
               "measured positive answer from an instrument that did "
               "answer. A classifier that reported work available while "
               "blind would be reporting green precisely where it cannot "
               "see",
               numbers=dict(by_class=by, obstructed=obstructed))]


def _neuter_unread_is_read_as_clear():
    """RESTORE THE CONFLATION: an ownership instrument that did not answer,
    substituted with an EMPTY one - 'nobody could tell me' read as 'nobody
    owns it'.  This is the single way a termination classifier manufactures
    green, and it is the direction this gate has caught three times."""
    was = globals()["road_termination"]

    def blind(findings, owners, writes=None, spans=None,
              step_limit=STEP_LIMIT_PER_M, deck_remedy=None, **kw):
        return was(findings, [] if owners is None else owners,
                   _NO_WRITES if writes is None else writes, spans,
                   step_limit, deck_remedy, **kw)

    globals()["road_termination"] = blind

    def undo():
        globals()["road_termination"] = was
    return undo


def _obstruct(parts, name, at, half=3.0, surf=10.0, clearable=(),
              not_property=()):
    g = I.Grid([I.mk_solid(name, at, I.yaw(0), parts)])
    bl = I.blockers_at(0.0, 0.0, surf, 1.0, 0.0, half, g,
                       set(clearable), set(not_property))
    gap = I.widest_gap([(a, b) for a, b, _ in bl], half)
    return bl, gap


def _case_four_metre_wall_go_round():
    bl, gap = _obstruct(_SHORT_WALL, "stone_wall_4x2", (0.0, 10.0, 0.0))
    return [_f("obstruction", gap >= 0.98,
               f"a 4 m wall in a 6 m carriageway leaves {gap:.2f} m, the "
               "walker needs 0.98 m", measured=round(gap, 2), limit=0.98),
            _f("obstruction_climb", not bl,
               f"{len(bl)} pieces must be CLIMBED at this station",
               advisory=True, numbers=dict(pieces=len(bl)))]


def _case_full_wall_blocks():
    _bl, gap = _obstruct(_FULL_WALL, "stone_wall_4x2", (0.0, 10.0, 0.0))
    return [_f("obstruction", gap >= 0.98,
               f"a wall across the whole carriageway leaves {gap:.2f} m, the "
               "walker needs 0.98 m", measured=round(gap, 2), limit=0.98,
               where=(0.0, 0.0))]


def _case_kerb_is_not_an_obstruction():
    bl, _gap = _obstruct(_KERB, "stone_wall_4x2", (0.0, 10.0, 0.0))
    return [_f("obstruction", not bl,
               f"a 0.5 m kerb is under the {STEP_LIMIT_PER_M:.4f} m slide "
               f"limit; {len(bl)} pieces counted",
               numbers=dict(pieces=len(bl)))]


def _case_our_own_lamp_is_beside_the_road():
    bl, _gap = _obstruct(_LAMP, "piece_groundtorch", (3.55, 10.0, 0.0))
    return [_f("obstruction", not bl,
               f"a lamp at 3.55 m lateral against a 3.0 m half-width: "
               f"{len(bl)} pieces counted", numbers=dict(pieces=len(bl)))]


def _case_deck_underfoot_is_not_an_obstruction():
    deck = [I.mk_solid("wood_floor", (float(x), 10.0, 0.0), I.yaw(0), _FLOOR)
            for x in range(-3, 4)]
    bl = I.blockers_at(0.0, 0.0, 10.10, 1.0, 0.0, 3.0, I.Grid(deck),
                       set(), set())
    over = [t for t in bl if t[2].ymax > 10.10 + STEP_LIMIT_PER_M]
    return [_f("obstruction", not over,
               f"the deck a walker stands ON counts {len(over)} times "
               "against him", numbers=dict(pieces=len(over)))]


# ---------------------------------------------------------------------------
# A DECK OVER A BED THAT HAS FALLEN AWAY.  Built here in code, never borrowed:
# 13 stations 1 m apart, a bed that drops 4.5 m into a gully, and a
# three-tile deck across the whole 6 m carriageway at a constant top.  That
# is the shape of N1-wnorth-shelf on frozen save 04e68ec3 - 42 m of
# `wood_floor` at top 32.500 on `wood_pole_log_4` piles, over a bed reading
# 31.773, 31.772, 31.662, 30.020, 29.848, 29.707 into water - and the shape
# of every bridge anyone will ever build here.
#
# THE BAND IS AS THIN AS THE DECK, WHICH IS WHY NOBODY SAW IT.  `blockers_at`
# walks UNDER anything whose underside clears `surf + 0.7813`, so a deck only
# counts against a walker while his bed sits in [ymin - 0.7813, ymax -
# 0.7813) - 0.13 m wide for a `wood_floor`, [31.5887, 31.7187) at N1.  Two
# stations' beds landed in it, 31.662 and one more, and those two severed a
# road that is decked from end to end.  The fixture's -3 and +3 stations sit
# in the same window by construction.
_GULLY_BED = {-6: 10.0, -5: 10.0, -4: 9.9, -3: 9.65, -2: 9.0, -1: 7.0,
              0: 6.0, 1: 7.0, 2: 9.0, 3: 9.65, 4: 9.9, 5: 10.0, 6: 10.0}
_DECK_TOP = 10.5


def _decked_gully(hole_xs=()):
    """(stations, points, surfaces, grid) for the fixture.  `_FLOOR` tiles
    are 2 m square, so three of them at z -2/0/2 cover the full 6 m
    carriageway, exactly as the measured deck does at z 2708/2710/2712."""
    deck = [I.mk_solid("wood_floor", (float(x), _DECK_TOP - 0.1, float(zt)),
                       I.yaw(0), _FLOOR)
            for x in range(-5, 6) if x not in hole_xs for zt in (-2, 0, 2)]
    grid = I.Grid(deck)
    xs = sorted(_GULLY_BED)
    pts = [(float(x), 0.0) for x in xs]
    surfs = [I.surfaces_at(float(x), 0.0, _GULLY_BED[x], grid, 0.49)
             for x in xs]
    return xs, pts, surfs, grid


def _blocked_stations(xs, pts, surfs, grid):
    """The stations the OBSTRUCTION check calls blocked, by its own rule."""
    ys = standing_surfaces(pts, surfs)
    out = []
    for j, x in enumerate(xs):
        y = ys[j]
        on = {id(t[1]) for t in surfs[j]
              if t[1] is not None and abs(t[0] - y) <= 0.05}
        bl = [t for t in I.blockers_at(float(x), 0.0, y, 0.0, 1.0, 3.0,
                                       grid, set(), set())
              if id(t[2]) not in on]
        if bl and I.widest_gap([(a, b) for a, b, _ in bl], 3.0) < 0.98:
            out.append(x)
    return out, ys


def _case_a_deck_over_a_fallen_bed_is_not_a_wall():
    xs, pts, surfs, grid = _decked_gully()
    # FIXTURE INVARIANTS, measured off the BED alone so they cannot be
    # satisfied by the thing under test: at station -3 the deck really does
    # span the whole 6 m, and that bed really is out of reach of it.
    raw = I.blockers_at(-3.0, 0.0, _GULLY_BED[-3], 0.0, 1.0, 3.0, grid,
                        set(), set())
    assert I.widest_gap([(a, b) for a, b, _ in raw], 3.0) == 0.0, (
        "the fixture's deck no longer spans the carriageway")
    assert _DECK_TOP - _GULLY_BED[-3] > STEP_LIMIT_PER_M, (
        "the fixture's bed is no longer out of reach of its own deck")
    blocked, ys = _blocked_stations(xs, pts, surfs, grid)
    return [_f("obstruction", not blocked,
               f"a 10 m deck at a constant {_DECK_TOP:.3f} over a bed "
               f"falling to {_GULLY_BED[0]:.3f}: {len(blocked)} of "
               f"{len(xs)} stations call the deck a wall across their own "
               f"carriageway (walker's surface "
               f"{min(y for y in ys if y is not None):.3f}.."
               f"{max(y for y in ys if y is not None):.3f})",
               measured=float(len(blocked)), limit=0.0, where=(0.0, 0.0),
               numbers=dict(stations=len(xs), blocked_at=blocked))]


def _neuter_surface_chosen_per_station():
    """Restore the defect: choose the walker's surface at each station on
    its own, climbing only from the ground directly beneath him."""
    mod = sys.modules[__name__]
    orig = mod.standing_surfaces

    def per_station(pts, surfs, limit_per_m=STEP_LIMIT_PER_M):
        out = []
        for ss in surfs:
            if not ss:
                out.append(None)
                continue
            y = ss[0][0]
            for cand in ss[1:]:
                if cand[0] - y <= limit_per_m:
                    y = cand[0]
            out.append(y)
        return out

    mod.standing_surfaces = per_station
    return lambda: setattr(mod, "standing_surfaces", orig)


def _case_a_hole_in_that_deck_is_still_refused():
    """The fix must not buy walkability with blindness.  Same gully, same
    deck, three tiles missing at the bottom: nothing propagates across a
    station that HAS no deck surface, so the walker is on the bed there and
    the transition on to the far half of the deck is over the limit."""
    xs, pts, surfs, grid = _decked_gully(hole_xs=(-1, 0, 1))
    ys = standing_surfaces(pts, surfs)
    j = xs.index(0)
    assert surfs[j] == [(_GULLY_BED[0], None)], (
        "the fixture's hole is no longer a hole")
    steps = [(xs[k], round(min_step(surfs[k], surfs[k + 1]), 3))
             for k in range(len(xs) - 1)]
    worst = max(s for _x, s in steps)
    return [_f("traversability", worst <= STEP_LIMIT_PER_M,
               f"a 3 m hole in the deck over the gully: worst step "
               f"{worst:.3f} m against {STEP_LIMIT_PER_M:.4f} m, walker's "
               f"surface at the hole {ys[j]:.3f}",
               measured=worst, limit=round(STEP_LIMIT_PER_M, 4),
               where=(0.0, 0.0))]


# ---------------------------------------------------------------------------
# A CARRIER THAT IS ONLY A BOUNDING BOX.  CONSTRUCTED HERE, NEVER BORROWED:
# the two ramps below differ by exactly one token - the trailing `"mesh"`
# collider-kind tag - and that token IS the state under test.  Borrowing
# `stone_stair` from the corpus would make this case go QUIET rather than RED
# the day that prefab's collider is re-measured, and 58 % of that corpus is
# mesh bounds.
_BOX_TILE = [["b", -1.0, -0.13, -1.0, 1.0, 0.0, 1.0]]
_RAMP_BOUNDS = [["b", -1.0, -0.70, -1.0, 1.0, 0.0, 1.0, "mesh"]]
_RAMP_TIGHT = [["b", -1.0, -0.70, -1.0, 1.0, 0.0, 1.0]]
_CARRY_BED = 10.0
_CARRY_DECK_TOP = 10.85     # 0.85 m up: OUT of ground-up reach (> 0.7813),
                            # and inside the band where it blocks that bed
_CARRY_RAMP_TOP = 10.70     # 0.70 m up: INSIDE ground-up reach


def _bounds_carrier_chain(ramp_parts):
    """13 stations on flat ground: a TIGHT deck over stations -6..3 that the
    walker cannot reach from the bed, and a ramp over stations 4..6 that he
    can.

    THE TWO FOOTPRINTS MUST NOT OVERLAP, and the assertion below is why: if
    one station saw both, the PRE-EXISTING ground-up climb would walk
    ramp -> deck at that station and the deck would be reached without
    propagating at all - the case would pass for a reason that has nothing to
    do with the rule under test.  This fixture had exactly that leak when it
    was first built, which is why the invariant is here and not in prose.
    """
    solids = [I.mk_solid("fx_deck_tile",
                         (float(x), _CARRY_DECK_TOP, float(zt)),
                         I.yaw(0), _BOX_TILE)
              for x in range(-5, 3) for zt in (-2, 0, 2)]
    solids += [I.mk_solid("fx_ramp", (5.0, _CARRY_RAMP_TOP, float(zt)),
                          I.yaw(0), ramp_parts) for zt in (-2, 0, 2)]
    grid = I.Grid(solids)
    xs = list(range(-6, 7))
    pts = [(float(x), 0.0) for x in xs]
    surfs = [I.surfaces_at(float(x), 0.0, _CARRY_BED, grid, 0.49) for x in xs]
    assert _CARRY_DECK_TOP - _CARRY_BED > STEP_LIMIT_PER_M, (
        "the fixture's deck is in ground-up reach of its own bed")
    assert _CARRY_RAMP_TOP - _CARRY_BED <= STEP_LIMIT_PER_M, (
        "the fixture's ramp is out of ground-up reach")
    assert abs(_CARRY_DECK_TOP - _CARRY_RAMP_TOP) <= STEP_LIMIT_PER_M, (
        "the fixture's ramp cannot reach the deck at all")
    raw = I.blockers_at(0.0, 0.0, _CARRY_BED, 0.0, 1.0, 3.0, grid,
                        set(), set())
    assert I.widest_gap([(a, b) for a, b, _ in raw], 3.0) == 0.0, (
        "the fixture's deck no longer spans the carriageway")
    for j, x in enumerate(xs):
        names = {s.name for _t, s in surfs[j] if s is not None}
        assert names != {"fx_deck_tile", "fx_ramp"}, (
            f"station {x} sees ramp AND deck; the ground-up climb would reach "
            f"the deck without propagating")
    return xs, pts, surfs, grid


def _carry_finding(ramp_parts, want_bounds_only):
    xs, pts, surfs, grid = _bounds_carrier_chain(ramp_parts)
    ramp = next(s for _t, s in surfs[xs.index(5)] if s is not None)
    assert ramp.bounds_only is want_bounds_only, (
        f"the fixture's ramp bounds_only is {ramp.bounds_only}, "
        f"the case is about {want_bounds_only}")
    blocked, ys = _blocked_stations(xs, pts, surfs, grid)
    kind = "a MeshCollider BOUND" if want_bounds_only else "a MEASURED box"
    return [_f("obstruction", not blocked,
               f"a ramp whose top {_CARRY_RAMP_TOP:.3f} is known to {kind}, "
               f"beside a measured deck at {_CARRY_DECK_TOP:.3f} that is "
               f"{_CARRY_DECK_TOP - _CARRY_BED:.3f} m over the bed and out of "
               f"reach of it: {len(blocked)} of {len(xs)} stations blocked, "
               f"walker's surface "
               f"{min(y for y in ys if y is not None):.3f}.."
               f"{max(y for y in ys if y is not None):.3f}",
               measured=float(len(blocked)), limit=0.0, where=(0.0, 0.0),
               numbers=dict(stations=len(xs), blocked_at=blocked))]


def _case_a_bounding_box_does_not_carry_a_walker_along_the_road():
    return _carry_finding(_RAMP_BOUNDS, True)


def _case_a_measured_ramp_does_carry_him():
    return _carry_finding(_RAMP_TIGHT, False)


def _neuter_bounds_only_may_propagate():
    """Restore the defect VERBATIM: the guard on the RECEIVING side only, so
    a station carried by nothing but a bounding box may lend that
    over-claimed top to its neighbours again."""
    mod = sys.modules[__name__]
    orig = mod.standing_surfaces

    def receiving_side_only(pts, surfs, limit_per_m=STEP_LIMIT_PER_M):
        ys = []
        for ss in surfs:
            if not ss:
                ys.append(None)
                continue
            y = ss[0][0]
            for cand in ss[1:]:
                if cand[0] - y <= limit_per_m:
                    y = cand[0]
            ys.append(y)
        work = [j for j, y in enumerate(ys) if y is not None]
        while work:
            j = work.pop()
            for nb in (j - 1, j + 1):
                if not 0 <= nb < len(ys) or ys[nb] is None or ys[j] is None:
                    continue
                d = math.hypot(pts[nb][0] - pts[j][0], pts[nb][1] - pts[j][1])
                lim = limit_per_m * max(d, 1e-6)
                best = ys[nb]
                for top, sol in surfs[nb]:
                    if top <= best or abs(top - ys[j]) > lim:
                        continue
                    if sol is not None and sol.bounds_only:
                        continue
                    best = top
                if best > ys[nb]:
                    ys[nb] = best
                    work.append(nb)
        return ys

    mod.standing_surfaces = receiving_side_only
    return lambda: setattr(mod, "standing_surfaces", orig)


def _case_a_wall_top_is_not_reached_along_the_road():
    """The reach rule must not lift a walker on to a two-metre wall just
    because he was standing on flat ground one metre earlier."""
    wall = [I.mk_solid("stone_wall_4x2", (0.0, 10.0, 0.0), I.yaw(90),
                       _FULL_WALL)]
    grid = I.Grid(wall)
    xs = list(range(-6, 7))
    pts = [(float(x), 0.0) for x in xs]
    surfs = [I.surfaces_at(float(x), 0.0, 10.0, grid, 0.49) for x in xs]
    blocked, ys = _blocked_stations(xs, pts, surfs, grid)
    return [_f("obstruction", not blocked,
               f"a 2 m wall across the carriageway on flat ground: "
               f"{len(blocked)} station(s) blocked, walker's surface "
               f"{max(y for y in ys if y is not None):.3f}",
               measured=float(len(blocked)), limit=0.0, where=(0.0, 0.0))]


def _case_vegetation_is_not_in_the_population():
    clearable, not_property = I.load_clearable()
    bl, _gap = _obstruct(_FULL_WALL, "Beech1", (0.0, 10.0, 0.0),
                         clearable=clearable, not_property=not_property)
    return [_f("obstruction", not bl,
               f"a Beech1 across the whole carriageway counts {len(bl)} "
               "pieces against the road", numbers=dict(pieces=len(bl)))]


def _case_a_built_wall_is_in_the_population():
    clearable, not_property = I.load_clearable()
    _bl, gap = _obstruct(_FULL_WALL, "stone_wall_4x2", (0.0, 10.0, 0.0),
                         clearable=clearable, not_property=not_property)
    return [_f("obstruction", gap >= 0.98,
               f"a stone_wall_4x2 across the whole carriageway leaves "
               f"{gap:.2f} m with the real CLEARABLE list loaded",
               measured=round(gap, 2), limit=0.98, where=(0.0, 0.0))]


def _walk_chain(step_m: float):
    """Three stations in a line; the middle-to-last riser is `step_m`.

    The edge predicate is the gate's own `edge_ok`, so this case proves the
    rule connectivity actually applies and not a restatement of it.
    """
    surf = {0: [(10.0, None)], 1: [(10.0, None)],
            2: [(10.0 + step_m, None)]}
    adj = defaultdict(list)
    for j in (0, 1):
        if edge_ok(surf[j], surf[j + 1], 1.0):
            adj[j].append(j + 1)
            adj[j + 1].append(j)
    seen, stack = {0}, [0]
    while stack:
        for n in adj[stack.pop()]:
            if n not in seen:
                seen.add(n)
                stack.append(n)
    return seen


def _case_connectivity_refuses_a_wall():
    seen = _walk_chain(6.12)               # the wall 773 m short of Vestvik
    return [_f("connectivity", 2 in seen,
               "the far end is not reachable across a 6.120 m step "
               f"({len(seen)} of 3 stations walked)",
               measured=6.12, limit=round(STEP_LIMIT_PER_M, 4),
               where=(2.0, 0.0))]


def _case_connectivity_accepts_a_walkable_step():
    seen = _walk_chain(0.5)
    return [_f("connectivity", 2 in seen,
               f"the far end across a 0.500 m step: {len(seen)} of 3 "
               "stations walked", measured=0.5,
               limit=round(STEP_LIMIT_PER_M, 4))]


# --- THE LATTICE CASES -----------------------------------------------------
# Three faults, each with the state that exposes it and the blind quantity
# that accepts it.  All three were MEASURED on this world before they were
# written down (RoadsRemeasure), which is the standard: a case that encodes a
# real prior state is worth more than an invented one.
def _lattice_probe(heights: dict, a, b):
    """Judge one move from vertex a to vertex b the way the gate does:
    orthogonal = one 1 m mesh edge; diagonal = two, through the EASIER of the
    two shared corners; a corner nobody read = UNRESOLVED.  Returns
    (worst_step, resolved)."""
    (ax, az), (bx, bz) = a, b
    if abs(ax - bx) < 1e-9 or abs(az - bz) < 1e-9:
        return abs(heights[b] - heights[a]), True
    best = None
    for c in ((ax, bz), (bx, az)):
        if c not in heights:
            continue
        worst = max(abs(heights[c] - heights[a]), abs(heights[b] - heights[c]))
        best = worst if best is None else min(best, worst)
    return (best, best is not None)


# A cell whose two read corners are level and whose UNREAD corners carry the
# wall: the diagonal move reads 0.000 while the walker meets 6.349 m.  That
# is N1-wnorth-shelf (433,3318)->(433,3319) in miniature, whose whole-segment
# worst under the old station walk was 2.552 m.
_CELL = {(0.0, 0.0): 10.0, (1.0, 1.0): 10.0,
         (1.0, 0.0): 16.349, (0.0, 1.0): 16.349}


def _case_diagonal_read_as_one_step_hides_a_wall():
    """THE BLIND QUANTITY: the diagonal treated as a single 1.414 m step."""
    h = _CELL
    step = abs(h[(1.0, 1.0)] - h[(0.0, 0.0)])
    lim = STEP_LIMIT_PER_M * math.sqrt(2)
    return [_f("traversability_diagonal_as_one_step", step <= lim,
               f"the corner-to-corner difference is {step:.3f} m against a "
               f"{lim:.4f} m diagonal allowance",
               measured=round(step, 3), limit=round(lim, 4))]


def _case_mesh_edges_find_the_wall_in_the_cell():
    worst, resolved = _lattice_probe(_CELL, (0.0, 0.0), (1.0, 1.0))
    return [_f("traversability", resolved and worst <= STEP_LIMIT_PER_M,
               f"the easier of the two 1 m mesh edges through this cell "
               f"still climbs {worst:.3f} m",
               measured=round(worst, 3), limit=round(STEP_LIMIT_PER_M, 4),
               where=(0.0, 0.0))]


def _case_diagonal_judged_at_the_one_metre_allowance():
    """A 1.05 m rise spread over a diagonal is NOT forgiven by the 1.414 m
    allowance: each mesh edge carries it, and 1.05 > 0.7813."""
    h = {(0.0, 0.0): 10.0, (1.0, 0.0): 11.05, (0.0, 1.0): 11.05,
         (1.0, 1.0): 11.05}
    worst, resolved = _lattice_probe(h, (0.0, 0.0), (1.0, 1.0))
    return [_f("traversability", resolved and worst <= STEP_LIMIT_PER_M,
               f"a 1.050 m rise across a diagonal is {worst:.3f} m on its "
               "own mesh edge",
               measured=round(worst, 3), limit=round(STEP_LIMIT_PER_M, 4),
               where=(0.0, 0.0))]


def _case_a_walkable_diagonal_is_accepted():
    h = {(0.0, 0.0): 10.0, (1.0, 0.0): 10.4, (0.0, 1.0): 10.7,
         (1.0, 1.0): 10.7}
    worst, resolved = _lattice_probe(h, (0.0, 0.0), (1.0, 1.0))
    return [_f("traversability", resolved and worst <= STEP_LIMIT_PER_M,
               f"the easier route through this cell climbs {worst:.3f} m, "
               "inside the slide limit on both edges",
               measured=round(worst, 3), limit=round(STEP_LIMIT_PER_M, 4))]


def _case_unread_corner_is_unresolved_not_clean():
    h = {(0.0, 0.0): 10.0, (1.0, 1.0): 10.0}      # both corners never read
    worst, resolved = _lattice_probe(h, (0.0, 0.0), (1.0, 1.0))
    return [_f("traversability", resolved,
               "a diagonal move whose two shared corners were never read is "
               f"UNRESOLVED (corner-to-corner would have read "
               f"{abs(h[(1.0,1.0)] - h[(0.0,0.0)]):.3f} m and looked clean)",
               where=(0.0, 0.0), numbers=dict(corners_read=0),
               measured=None if resolved else 0.0)]

# --- THE NEAR-MISS CASES ---------------------------------------------------
# BOTH GEOMETRIES ARE REAL AND BOTH ARE THIS WORLD'S, decoded out of the
# ledger's own terrain blobs on save f544ede6 rather than invented, because
# the whole question this rule settles is whether the ground between two
# roads was READ or GUESSED and a made-up fixture cannot show that.
def _near_miss_world(a_xz, b_xz, heights, seg_a, seg_b, unread=()):
    """Two one-station roads and the surface between them.

    `heights` is the APPLIED surface at each 1 m lattice vertex on the line,
    including both ends.  `unread` names vertices the ground reader could
    not answer for, which is a different state from flat ground and has to
    stay different all the way to the verdict."""
    npos = {"0|0": a_xz, "1|0": b_xz}
    node_surf = {"0|0": [(heights[a_xz], None)],
                 "1|0": [(heights[b_xz], None)]}
    comp = {"0|0": 0, "1|0": 1}
    seg_of = {"0|0": seg_a, "1|0": seg_b}
    asked: list[tuple] = []

    def read_ground(pts):
        out = {}
        for lb, x, z in pts:
            asked.append((x, z))
            if (x, z) in unread:
                continue
            out[lb] = heights[(x, z)]
        return out

    def surfaces_at(x, z, bed):
        return [(bed, None)]

    edges, rep = near_miss_bridges(npos, node_surf, comp, seg_of,
                                   read_ground, surfaces_at)
    return edges, rep, asked


# MEASURED, save f544ede6, decoded from the terrain blobs of seqs 5122 (T8)
# and 2914 (R1): the applied surface along z = 738.  Every vertex MODIFIED.
_T8_R1 = {(820.0, 738.0): 39.785, (821.0, 738.0): 39.706,
          (822.0, 738.0): 39.653, (823.0, 738.0): 39.613,
          (824.0, 738.0): 39.530}
# MEASURED, same save, zone (7,7) last written by seq 5327 (S8): the diagonal
# from S8's vertex to T4's, AND BOTH SHARED CORNERS OF EVERY MOVE ON IT,
# because a diagonal is two 1 m mesh edges and the corner is where the wall
# stands.  `modified = 0` on (477,436), (477,437) and (478,437) - ground NO
# ROAD HAS EVER WRITTEN, 1.6-1.8 m below S8's batter.
_S8_T4 = {(480.0, 434.0): 83.436, (479.0, 435.0): 83.325,
          (478.0, 436.0): 83.272, (477.0, 437.0): 81.469,
          (476.0, 438.0): 81.211, (475.0, 439.0): 81.191,
          (474.0, 440.0): 81.274,
          # the corners, in the same population
          (480.0, 435.0): 83.489, (479.0, 434.0): 83.393,
          (479.0, 436.0): 83.311, (478.0, 435.0): 83.344,
          (478.0, 437.0): 81.431, (477.0, 436.0): 81.688,
          (477.0, 438.0): 81.072, (476.0, 437.0): 81.476,
          (476.0, 439.0): 81.041, (475.0, 438.0): 81.405,
          (475.0, 440.0): 81.088, (474.0, 439.0): 81.522}


# A SYNTHETIC CELL, and it is here because the world's own bridged line is
# AXIS-ALIGNED and therefore cannot exercise the corner rule at all.  Four
# lattice vertices at 40.000 - above the y = 30 water plane, or `_dry` would
# refuse them for the wrong reason - on a 3,3 diagonal, with a 2.000 m wall
# standing on BOTH corners of the middle cell: read as one 1.414 m
# hypotenuse the move is a 0.000 m step and the bridge is clean, which is
# the N1 (433,3318)->(433,3319) defect at 6.349 m wearing different clothes.
_DIAG_WALL = {(0.0, 0.0): 40.0, (1.0, 1.0): 40.0, (2.0, 2.0): 40.0,
              (3.0, 3.0): 40.0,
              (0.0, 1.0): 40.0, (1.0, 0.0): 40.0,
              (1.0, 2.0): 42.0, (2.0, 1.0): 42.0,
              (2.0, 3.0): 40.0, (3.0, 2.0): 40.0}
_DIAG_CLEAR = {**_DIAG_WALL, (1.0, 2.0): 40.2, (2.0, 1.0): 40.2}


def _case_a_near_miss_over_joined_ground_is_an_edge():
    """THE KNOWN-BAD STATE, and it cost this world two destinations.

    `T8-stenvik-wttown` ends at (820,738) and `R1-ulfsgard-t8junction` ends
    at (824,738).  4.000 m apart, so no pair of stations inside JUNCTION_M
    = 3.0, so no edge, so `t8junction` and `ulfsgard` were reported
    UNREACHABLE behind a link the gate called `near_miss` - "the two roads
    never actually meet.  Needs road, not grading."

    THE ROAD IS THERE.  Both are 6 m trunks with a 1 m shoulder; R1's own
    terminal stamp alone paves the 4 m.  The applied surface between them
    steps 0.079, 0.053, 0.040 and 0.083 m against a 0.7813 m limit, and R1's
    `why` in segments.yaml says it outright: the terminal window was PINNED
    to T8's measured carriageway datum 39.608, which both ends read.  The
    two roads were built to meet, they DO meet, and the gate was measuring
    the spacing of two station lists."""
    edges, rep, asked = _near_miss_world(
        (820.0, 738.0), (824.0, 738.0), _T8_R1,
        "T8-stenvik-wttown", "R1-ulfsgard-t8junction")
    return [_f("connectivity", not edges,
               f"two roads whose nearest STATIONS are 4.000 m apart are "
               f"joined by {len(edges)} measured edge over "
               f"{len(asked)} lattice samples read from the save, worst "
               "step 0.083 m against 0.7813 - a near miss is an absent "
               "measurement, not a fact about roads",
               where=(822.0, 738.0), measured=0.083,
               limit=round(STEP_LIMIT_PER_M, 4),
               numbers=dict(bridged=rep["bridged"], samples=rep["samples"],
                            refused=rep["refused_total"]))]


def _case_a_near_miss_over_a_cliff_is_still_refused():
    """THE OTHER DIRECTION, and without it the rule above is a way of making
    every near miss disappear.

    The SAME world's other near miss: `S8-wtsouth-treesth` (480,434) to
    `T4-meadhall-stathub` (474,440), 8.485 m, six diagonal moves.  The line
    drops off S8's batter (83.272) onto ground NO ROAD HAS EVER WRITTEN:
    (477,436), (477,437) and (478,437) are all `modified = 0` in every blob
    covering zone (7,7).  Judged the way the walker actually crosses a cell
    - two 1 m mesh edges through the EASIER corner - the move
    (478,436)->(477,437) is 1.584 m through (477,436) and 1.841 m through
    (478,437), against a 0.7813 m allowance per mesh edge.  The pair stays
    severed AND now carries the metre and the corner it fails at instead of
    a station spacing."""
    edges, rep, _asked = _near_miss_world(
        (480.0, 434.0), (474.0, 440.0), _S8_T4,
        "S8-wtsouth-treesth", "T4-meadhall-stathub")
    bad = rep["refused"][0] if rep["refused"] else {}
    return [_f("connectivity", bool(edges) or not bad,
               f"an 8.485 m near miss whose easier mesh edge still drops "
               f"{bad.get('step_m')} m onto unwritten ground at "
               f"{bad.get('at')}->{bad.get('to')} through corner "
               f"{bad.get('through_corner')} is REFUSED with its metre, not "
               f"bridged ({len(edges)} edges)",
               where=tuple(bad.get("at", (478.0, 436.0))),
               measured=bad.get("step_m"), limit=bad.get("limit_m"),
               numbers=dict(bridged=len(edges), legs=bad.get("legs"),
                            refused=rep["refused_total"]))]


def _case_a_wall_on_the_cell_corners_refuses_the_bridge():
    """THE CORNER RULE, PROVED ON A FIXTURE BECAUSE THE WORLD CANNOT.

    `T8|R1` runs along z = 738 and is axis-aligned, so the line this rule
    was written for never crosses a cell at all.  A bridge that judged a
    diagonal as ONE 1.414 m step would pass this section's own pinned
    defect straight back in - "a diagonal read as ONE 1.414 m step accepts
    a wall in the cell", the reading that kept N1 (433,3318)->(433,3319)
    invisible at 6.349 m.

    Four vertices at 10.000 on a 3,3 diagonal and a 2.000 m wall standing on
    BOTH corners of the middle cell.  Endpoint to endpoint the move is a
    0.000 m step; across the mesh it is 2.000 m through either corner."""
    edges, rep, _asked = _near_miss_world(
        (0.0, 0.0), (3.0, 3.0), _DIAG_WALL, "A-hub-mid", "B-mid-far")
    bad = rep["refused"][0] if rep["refused"] else {}
    return [_f("connectivity", bool(edges) or not bad,
               f"a wall on BOTH corners of a cell refuses the bridge at "
               f"{bad.get('step_m')} m over {bad.get('legs')} mesh edges - "
               "endpoint to endpoint the same move reads 0.000 m and looks "
               "clean",
               where=(1.0, 1.0), measured=bad.get("step_m"),
               limit=bad.get("limit_m"),
               numbers=dict(bridged=len(edges),
                            diagonal_moves=rep["diagonal_moves"]))]


def _case_a_clear_cell_still_bridges():
    """AND THE SAME GEOMETRY WITH NO WALL IN IT, because a corner rule that
    refuses every diagonal is not a corner rule.  Corners at 40.200: worst
    mesh edge 0.200 m against 0.7813, so the bridge stands."""
    edges, rep, _asked = _near_miss_world(
        (0.0, 0.0), (3.0, 3.0), _DIAG_CLEAR, "A-hub-mid", "B-mid-far")
    return [_f("connectivity", bool(edges) and not rep["refused_total"],
               f"the same three diagonal moves with a 0.200 m corner are "
               f"bridged ({len(edges)} edge, {rep['diagonal_moves']} "
               "diagonal moves judged on their corners, "
               f"{rep['refused_total']} refused)",
               where=(1.0, 1.0), measured=0.2,
               limit=round(STEP_LIMIT_PER_M, 4),
               numbers=dict(bridged=len(edges),
                            refused=rep["refused_total"]))]


def _neuter_bridge_corners():
    """RESTORE THE DEFECT: the bridge judging a diagonal as ONE 1.414 m
    hypotenuse instead of two 1 m mesh edges, so a wall standing on both of
    a cell's corners is a 0.000 m step and the bridge is clean.  This is the
    reading that kept a 6.349 m wall on N1 invisible, pointed at the new
    rule."""
    import sys as _s
    mod = _s.modules[__name__]
    was = mod.BRIDGE_JUDGES_CORNERS
    mod.BRIDGE_JUDGES_CORNERS = False

    def undo():
        mod.BRIDGE_JUDGES_CORNERS = was
    return undo


def _case_a_near_miss_nobody_could_read_is_refused():
    """ABSENT IS NOT FLAT.  Take the T8/R1 line - the one that passes - and
    withhold ONE of its three interior samples from the ground reader.  A
    line with a hole in its evidence must refuse, because the alternative is
    a bridge built out of a reading nobody took, which is the failure this
    whole file keeps paying for."""
    edges, rep, _asked = _near_miss_world(
        (820.0, 738.0), (824.0, 738.0), _T8_R1,
        "T8-stenvik-wttown", "R1-ulfsgard-t8junction",
        unread=((822.0, 738.0),))
    bad = rep["refused"][0] if rep["refused"] else {}
    return [_f("connectivity", bool(edges),
               f"the same 4.000 m line with (822,738) withheld from the "
               f"ground reader is REFUSED - {bad.get('why')} on the move "
               f"{bad.get('at')}->{bad.get('to')} - not bridged",
               where=(822.0, 738.0),
               numbers=dict(bridged=len(edges), unread=rep["unread"]))]


def _neuter_near_miss_bridging():
    """RESTORE THE DEFECT: the graph build with no near-miss reading in it,
    so two roads whose carriageways overlap but whose nearest stations are
    4.000 m apart are declared severed on the strength of JUNCTION_M and
    every destination behind them is reported UNREACHABLE.  That is the
    state `t8junction` and `ulfsgard` were handed out in."""
    import sys as _s
    mod = _s.modules[__name__]
    was = mod.near_miss_bridges

    def blind(npos, node_surf, comp, seg_of, read_ground, surfaces_at):
        return [], {"measured": False, "candidate_pairs": 0,
                    "component_pairs": 0, "samples": 0, "bridged": 0,
                    "refused": [], "refused_total": 0, "unread": 0}

    mod.near_miss_bridges = blind

    def undo():
        mod.near_miss_bridges = was
    return undo

def _coverage_unknown(known: bool):
    s = I.Solid("SomeModdedThing", (0.0, 10.0, 0.0), I.yaw(0),
                _FULL_WALL if known else [], known)
    bl = I.blockers_at(0.0, 0.0, 10.0, 1.0, 0.0, 3.0, I.Grid([s]),
                       set(), set())
    return [sol for _a, _b, sol in bl if not sol.known]


def _case_coverage_refuses_an_unmeasured_prefab():
    unk = _coverage_unknown(False)
    return [_f("coverage", not unk,
               f"{len(unk)} objects in the corridor whose prefab the "
               "geometry corpus has never measured",
               numbers=dict(objects=len(unk), prefab="SomeModdedThing"))]


def _case_coverage_accepts_a_measured_corridor():
    unk = _coverage_unknown(True)
    return [_f("coverage", not unk,
               f"{len(unk)} unmeasured prefabs once the corpus knows the "
               "piece", numbers=dict(objects=len(unk)))]


# --- THE SEVERANCE CASES ---------------------------------------------------
# THE STATE THESE PIN is the one this section shipped with: 27 of 28
# destinations reported UNREACHABLE and 25 of the 27 carrying no cause at
# all, because `walk_ends_at` only fires when the destination's own segment
# is partly reached.  An operator handed that list cannot tell a re-grade
# from a bridge he has to authorise, which is the same fault as a count
# without its population wearing different clothes.
def _sever_world(bed_left: float, bed_right: float, blocked: bool = False):
    """Two one-metre roads whose ends are 1 m apart and cannot be stepped
    between.  Everything else about them is walkable, so the ONLY thing the
    diagnosis can be about is that one link."""
    npos = {"0|0": (0.0, 0.0), "0|1": (1.0, 0.0),
            "1|0": (2.0, 0.0), "1|1": (3.0, 0.0)}
    surf = {"0|0": [(bed_left, None)], "0|1": [(bed_left, None)],
            "1|0": [(bed_right, None)], "1|1": [(bed_right, None)]}
    comp = {"0|0": 0, "0|1": 0, "1|0": 1, "1|1": 1}
    seg_of = {"0|0": "A-hub-mid", "0|1": "A-hub-mid",
              "1|0": "B-mid-far", "1|1": "B-mid-far"}
    refused = ([dict(a="0|1", b="1|0", reason="blocked", separation_m=1.0,
                     widest_gap_m=0.12, needs_m=0.98, prefabs=["stone_wall"])]
               if blocked else [])
    return severance(npos, surf, comp, "0|0", refused, seg_of, [])


def _case_severance_names_a_regradeable_step():
    links, reach = _sever_world(40.0, 46.0)
    cat = links[0]["category"] if links else None
    return [_f("connectivity", cat != CAT_REGRADE,
               f"two dry components severed by one junction step are "
               f"refused as {cat} with a repair chain of "
               f"{len(reach.get(1, {}).get('chain', []))} link(s) - a "
               "re-emit fixes it and the report says so",
               where=(1.0, 0.0),
               numbers=dict(category=cat, links=len(links)))]


def _neuter_water_test():
    """RESTORE THE DEFECT: the severance categoriser with no water test in
    it, so an over-limit link whose own bed is below sea level comes out
    REGRADEABLE and the report sends a re-emit at ground the ribbon is
    forbidden to move."""
    import sys as _s
    mod = _s.modules[__name__]
    was = mod.WATER_Y
    mod.WATER_Y = -1.0e9

    def undo():
        mod.WATER_Y = was
    return undo


def _neuter_structure_category():
    """RESTORE THE DEFECT: the categoriser with no `blocked` arm in it, so a
    severance the OBSTRUCTION check refused - a built body leaving a 0.12 m
    gap across the carriageway - is reported as GROUND TO MOVE.  That sends
    a re-emit at a house, and the report says the world is one regrade from
    connected."""
    import sys as _s
    mod = _s.modules[__name__]
    was = mod._link_category

    def blind(reason, bed_a, bed_b, over_water, frozen=False):
        if reason == "blocked":
            reason = "junction"
        return was(reason, bed_a, bed_b, over_water, frozen=frozen)

    mod._link_category = blind

    def undo():
        mod._link_category = was
    return undo


def _neuter_frozen_test():
    """RESTORE THE DEFECT: the categoriser with no frozen-ground arm, so a
    step standing on ground a `flatten: FORBIDDEN` piece freezes comes out
    REGRADEABLE.  That is the state this ticket was written in - two links
    reported "BOTH a_regradeable, no operator authorisation needed" over
    four lattice samples no write may touch."""
    import sys as _s
    mod = _s.modules[__name__]
    was = mod._link_category

    def blind(reason, bed_a, bed_b, over_water, frozen=False):
        return was(reason, bed_a, bed_b, over_water, frozen=False)

    mod._link_category = blind

    def undo():
        mod._link_category = was
    return undo


def _sever_frozen(frozen: bool):
    """T13's own two links, as the graph build actually produces them.

    The chain vertices are (13,-255) 32.230 and (14,-256) 31.436, the walk
    REFUSED the diagonal between them (0.794 m over 1 m of lattice through
    the easier corner, against 0.7813), and the low vertex is inside the
    Chebyshev keep-out of harbour-temple-south's signpost.  Both vertices
    belong to ONE segment, which is why the refusal has to come through
    `refused` - the proximity scan never offers a segment to itself."""
    npos = {"0|0": (12.0, -254.0), "0|1": (13.0, -255.0),
            "1|0": (14.0, -256.0), "1|1": (15.0, -257.0)}
    surf = {"0|0": [(32.230, None)], "0|1": [(32.230, None)],
            "1|0": [(31.436, None)], "1|1": [(32.230, None)]}
    comp = {"0|0": 0, "0|1": 0, "1|0": 1, "1|1": 1}
    seg_of = dict.fromkeys(npos, "T13-temple-harboursouth")
    refused = [dict(a="0|1", b="1|0", reason="step", separation_m=1.414,
                    step_m=0.794, limit_m=0.7813, ground_only_m=0.794)]

    def frozen_at(x, z):
        # the real signpost: wood_pole2 (13.62, -256.38), Chebyshev 1.0
        if abs(x - 13.62) <= 1.0 and abs(z + 256.38) <= 1.0:
            return {"site_id": "harbour-temple-south", "seq": 27,
                    "piece_xz": [13.62, -256.38], "clear_m": 1.0}
        return None

    return severance(npos, surf, comp, "0|0", refused, seg_of, [],
                     frozen_at=frozen_at if frozen else None)


def _case_severance_refuses_to_call_frozen_ground_regradeable():
    """THE KNOWN-BAD STATE, and it is the one that wrote this ticket.

    `harboursouth` was handed out as 3.2 m from walkable behind two links
    "BOTH a_regradeable, no operator authorisation needed".  MEASURED on
    save b1966e7b the ground under them cannot be written at all: the road's
    own plan authors 32.23 at (13,-255), (14,-256) and (15,-257), and the
    middle vertex reads 31.436 because `ribbon.stamp` refused it - the
    `wood_pole2` + `sign` of harbour-temple-south (seqs 27/28,
    `flatten: FORBIDDEN`) stand at (13.62, -256.38) and freeze the four
    lattice samples inside PROTECT_CLEAR_M.  Seq 5322's own record agrees:
    `skipped.beside_protected_piece: 48`, `rasteriser_refused {protected: 6}`,
    worst `full_m` 1.147 at (13.0, -256.0).  A re-emit writes nothing there
    and moves the step 0.000 m."""
    links, _reach = _sever_frozen(True)
    lk = links[0] if links else {}
    cat = lk.get("category")
    return [_f("connectivity", cat != CAT_OPERATOR,
               f"a 0.794 m step whose low vertex is frozen by "
               f"{(lk.get('ground_frozen_by') or {}).get('site_id')} seq "
               f"{(lk.get('ground_frozen_by') or {}).get('seq')} is {cat} - "
               "an OPERATOR decision about that piece, not a re-emit at "
               "ground no write may move",
               where=(14.0, -256.0), measured=0.794, limit=STEP_LIMIT_PER_M,
               numbers=dict(category=cat))]


def _case_severance_still_calls_movable_ground_regradeable():
    """THE OTHER DIRECTION, and without it the case above passes on a
    categoriser that calls every step an operator decision.  Identical
    geometry, no protected piece: it is a re-emit and the report must say
    so."""
    links, _reach = _sever_frozen(False)
    lk = links[0] if links else {}
    cat = lk.get("category")
    assert cat == CAT_REGRADE, (
        "with nothing protecting the ground the SAME 0.794 m step must stay "
        f"a re-emit, not become an operator decision; got {cat}")
    return [_f("connectivity", cat != CAT_REGRADE,
               f"the same 0.794 m step on ground nobody has protected is "
               f"{cat} - a re-emit fixes it "
               f"(ground_frozen_measured="
               f"{lk.get('ground_frozen_measured')})",
               where=(14.0, -256.0), measured=0.794, limit=STEP_LIMIT_PER_M,
               numbers=dict(category=cat))]


# --- THE REPAIR'S OWN POPULATION ------------------------------------------
# THE KNOWN-BAD STATE, and it is a real one on save 2a6940a934b5:
# N1-wnorth-shelf (433,3315)->(433,3316), 2.541 m of ground over 1 m, handed
# out `a_regradeable` with `ground_frozen_by: null` and
# `ground_frozen_measured: true`, gating shelf, mid1, mid2 and terminus.
# NEITHER of its two vertices is frozen.  ALL SEVEN vertices the cut-only
# repair must move are inside protected extent, so the re-emit the report
# asks for is refused by `writer._flatten_forbidden_check` and moves
# 0.000 m.  The fixture below is that shape in miniature - the freeze is
# placed at a vertex the link does not touch, which is the ONLY arrangement
# a two-vertex test cannot see.
_DISPOSAL_LO_Y = 32.924
_DISPOSAL_HI_Y = 35.466


def _sever_disposal(frozen_xz):
    """One segment, one over-limit step, and a dense height field around it.

    `frozen_xz` is the single lattice vertex a `flatten: FORBIDDEN` piece
    protects, or None.  The link itself is (0,0)->(0,1); anything at z >= 2
    is OUTSIDE the link and inside the disposal.
    """
    npos = {"0|0": (0.0, -1.0), "0|1": (0.0, 0.0),
            "1|0": (0.0, 1.0), "1|1": (0.0, 2.0)}
    surf = {"0|0": [(_DISPOSAL_LO_Y, None)], "0|1": [(_DISPOSAL_LO_Y, None)],
            "1|0": [(_DISPOSAL_HI_Y, None)], "1|1": [(_DISPOSAL_HI_Y, None)]}
    comp = {"0|0": 0, "0|1": 0, "1|0": 1, "1|1": 1}
    seg_of = dict.fromkeys(npos, "N1-wnorth-shelf")
    refused = [dict(a="0|1", b="1|0", reason="step", separation_m=1.0,
                    step_m=2.541, limit_m=round(STEP_LIMIT_PER_M, 4),
                    ground_only_m=2.541)]

    def height_field(pts):
        return {(x, z): (_DISPOSAL_LO_Y if z <= 0 else _DISPOSAL_HI_Y)
                for (x, z) in pts}

    def frozen_at(x, z):
        if frozen_xz is not None and (int(x), int(z)) == frozen_xz:
            return {"site_id": "brgs-n1", "seq": 2809,
                    "piece_xz": [float(x), float(z)], "clear_m": 1.0}
        return None

    return severance(npos, surf, comp, "0|0", refused, seg_of, [],
                     frozen_at=frozen_at, height_field=height_field)


def _case_severance_refuses_a_step_whose_REPAIR_is_frozen():
    """A CATEGORY IS A CLAIM ABOUT A WRITE, SO ITS POPULATION IS THAT
    WRITE'S.  The freeze here is at (0,2) - a vertex the link does not
    touch and the old two-vertex test cannot reach - and the cut-only
    disposal has to move it, so the promised re-emit is refused."""
    links, _reach = _sever_disposal((0, 2))
    lk = links[0] if links else {}
    cat = lk.get("category")
    return [_f("connectivity", cat != CAT_OPERATOR,
               f"a 2.541 m step whose OWN TWO VERTICES are movable but "
               f"{lk.get('disposal_frozen_samples')} of "
               f"{lk.get('disposal_samples')} vertices of its cut-only "
               f"disposal are frozen by "
               f"{(lk.get('disposal_frozen_by') or {}).get('site_id')} seq "
               f"{(lk.get('disposal_frozen_by') or {}).get('seq')} is "
               f"{cat} - the repair is the population, not the defect "
               f"(ground_frozen_by at the link itself: "
               f"{lk.get('ground_frozen_by')})",
               where=(0.0, 1.0), measured=2.541, limit=STEP_LIMIT_PER_M,
               numbers=dict(category=cat,
                            disposal=lk.get("disposal_samples"),
                            frozen=lk.get("disposal_frozen_samples")))]


def _neuter_disposal_population():
    """RESTORE THE DEFECT: the repair's population is the DEFECT's two
    vertices.  This is exactly what `offer` did on its own, and it is why
    five of tonight's twenty-five `closable` steps were not."""
    mod = sys.modules[__name__]
    was = mod.cut_disposal

    def defect_only(lo, hi, height, limit=STEP_LIMIT_PER_M):
        taken = {p: height[p] - 1.0 for p in (lo, hi) if p in height}
        return dict(taken=taken, reached_boundary=False,
                    measured=bool(taken))

    mod.cut_disposal = defect_only

    def undo():
        mod.cut_disposal = was
    return undo


def _case_severance_still_regrades_a_step_whose_repair_is_movable():
    """THE OTHER DIRECTION, and without it the case above passes on a
    categoriser that calls every step an operator decision.  Identical
    geometry, nothing protected anywhere in the cone."""
    links, _reach = _sever_disposal(None)
    lk = links[0] if links else {}
    cat = lk.get("category")
    assert cat == CAT_REGRADE, (
        "with nothing protecting any vertex of the disposal the SAME "
        f"2.541 m step must stay a re-emit; got {cat}")
    return [_f("connectivity", cat != CAT_REGRADE,
               f"the same 2.541 m step whose whole "
               f"{lk.get('disposal_samples')}-vertex, "
               f"{lk.get('disposal_cut_m3')} m3 cut-only disposal is "
               f"movable ground is {cat} - a re-emit fixes it, and the "
               f"disposal was MEASURED "
               f"(disposal_measured={lk.get('disposal_measured')})",
               where=(0.0, 1.0), measured=2.541, limit=STEP_LIMIT_PER_M,
               numbers=dict(category=cat,
                            disposal=lk.get("disposal_samples")))]


def _case_the_disposal_closes_its_step_and_never_raises_a_sample():
    """THE DISPOSAL'S THREE PROPERTIES, on a bare cliff and with no world:
    it CLOSES the step it was derived for, it is CUT-ONLY, and IT MAKES NO
    PAIR WORSE THAN IT ALREADY WAS.

    THE THIRD ONE IS STATED AS A DELTA AND THAT IS NOT A WEAKENING, it is
    the same rule `junction.rim_transfer` applies and the same rule
    `reemit.py` exists for: a cut into the FACE of a cliff leaves the
    cliff's own flank still over the limit, and that flank was over the
    limit before anybody arrived.  Judging it as a STATE would refuse every
    cut-only repair of every cliff in the world, which is how a gate ends
    up forbidding the only shape the ground will accept.  What a repair may
    never do is CAUSE one, and that is what this measures.
    """
    h = {(x, z): (10.0 if z <= 0 else 13.0)
         for x in range(-8, 9) for z in range(-8, 9)}
    d = cut_disposal((0, 0), (0, 1), h)
    tk = d["taken"]
    after = dict(h)
    after.update(tk)
    raised = [p for p in tk if tk[p] > h[p]]
    step = abs(after[(0, 1)] - after[(0, 0)])
    caused, judged = [], 0
    for (x, z) in tk:
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            q = (x + dx, z + dz)
            if q not in after:
                continue
            judged += 1
            was = abs(h[q] - h[(x, z)])
            now = abs(after[q] - after[(x, z)])
            if now > STEP_LIMIT_PER_M and now > was + 1e-9:
                caused.append((x, z, q, round(was, 4), round(now, 4)))
    ok = (not raised and not caused and step <= STEP_LIMIT_PER_M
          and not d["reached_boundary"])
    return [_f("connectivity", ok,
               f"a 3.000 m cliff disposed of cut-only in {len(tk)} "
               f"vertices: the step closes at {step:.4f} m against "
               f"{STEP_LIMIT_PER_M:.4f}, {len(raised)} sample(s) RAISED, "
               f"{len(caused)} of {judged} judged pair(s) made WORSE, "
               f"reached the read boundary {d['reached_boundary']}",
               measured=float(len(caused)), limit=0.0, where=(0.0, 1.0),
               numbers=dict(taken=len(tk), raised=len(raised),
                            pairs_judged=judged))]


def _neuter_disposal_is_a_cone():
    """RESTORE THE OBVIOUS WRONG ANSWER: lower ONLY the high vertex of the
    step.  It closes the step and it is cut-only, and it moves the cliff one
    metre along - which is what a repair sized off a finding's `at` does,
    and why a disposal has to be judged at its own rim."""
    mod = sys.modules[__name__]
    was = mod.cut_disposal

    def just_the_high_one(lo, hi, height, limit=STEP_LIMIT_PER_M):
        if lo not in height or hi not in height:
            return dict(taken={}, reached_boundary=False, measured=False)
        return dict(taken={hi: height[lo] + limit}, reached_boundary=False,
                    measured=True)

    mod.cut_disposal = just_the_high_one

    def undo():
        mod.cut_disposal = was
    return undo

def _case_a_diagnosable_severance_never_lands_in_the_residual():
    """PIN THE DRAIN SHUT.  Forcing "nothing unaccounted" is worth nothing
    if the residual bucket can absorb the problem: the identity balances
    exactly as neatly at 25 of 27 as at 0 of 27.  So a severance that CAN
    be diagnosed must not be allowed to fall into `d_unmeasured` - without
    this case a future widening of the residual turns the whole diagnosis
    into one bucket that always adds up.  (ParBuried's guard.)"""
    _links, reach = _sever_world(40.0, 46.0)
    far = reach.get(1, {})
    drained = not far.get("chain") or "unmeasured" in far
    return [_f("connectivity", not drained,
               "a severance with a measurable missing link carries a "
               f"{len(far.get('chain', []))}-link repair chain and is NOT "
               "in the unmeasured residual"
               + (f" (it is: {far.get('unmeasured', 'no chain')})"
                  if drained else ""),
               numbers=dict(chain=len(far.get("chain", [])),
                            in_residual=drained))]


def _case_severance_accepts_one_whole_network():
    """A network in ONE piece has no missing links and nothing to
    categorise.  Without this the three cases above are only shown to
    refuse, and a diagnosis that fires on a healthy world is noise."""
    npos = {"0|0": (0.0, 0.0), "0|1": (1.0, 0.0), "0|2": (2.0, 0.0)}
    surf = {k: [(40.0, None)] for k in npos}
    comp = dict.fromkeys(npos, 0)
    seg_of = dict.fromkeys(npos, "A-hub-far")
    links, reach = severance(npos, surf, comp, "0|0", [], seg_of, [])
    return [_f("connectivity", not links,
               f"{len(links)} missing links across a network that is all "
               f"one component, {len(reach)} component(s) reachable",
               numbers=dict(links=len(links), components=len(reach)))]


def _case_severance_refuses_to_call_an_over_water_link_regradeable():
    """THE OPERATOR BLOCKER, and the direction it must never be reported in.
    The ribbon is forbidden to move protected over-water ground, so an
    over-limit link whose own bed is below sea level is a DECK TO BUILD.
    Calling it regradeable would send a re-emit at ground it cannot move and
    report the world as one command from fixed."""
    links, _reach = _sever_world(27.5, 46.0)
    cat = links[0]["category"] if links else None
    return [_f("connectivity", cat != CAT_OPERATOR,
               f"a severance whose own bed is 27.5 m, below the "
               f"{WATER_Y:g} m water plane, is {cat} - a DECK TO BUILD, and "
               "this gate refuses the world rather than sending a re-emit "
               "at ground the ribbon is forbidden to move",
               where=(1.0, 0.0), measured=27.5, limit=WATER_Y,
               numbers=dict(category=cat))]


def _case_severance_names_the_structure_not_the_ground():
    links, _reach = _sever_world(40.0, 40.2, blocked=True)
    cat = links[0]["category"] if links else None
    return [_f("connectivity", cat != CAT_STRUCTURE,
               f"two components severed by a body leaving a 0.12 m gap are "
               f"refused as {cat} - A STRUCTURE IS IN THE WAY - and not as "
               "a grading defect, though the beds are only 0.200 m apart "
               "and the proximity scan would have called it a junction",
               where=(1.0, 0.0), measured=0.12, limit=0.98,
               numbers=dict(category=cat))]


def _case_an_unreachable_component_without_a_cause_is_refused():
    """THE BLIND STATE: a destination reported unreachable with nothing said
    about why.  Here the far component is 400 m away with nothing in between,
    which is exactly the shape 25 of the 27 had - no refused transition, no
    near miss, no diagnosis under the old report."""
    npos = {"0|0": (0.0, 0.0), "0|1": (1.0, 0.0),
            "1|0": (400.0, 0.0), "1|1": (401.0, 0.0)}
    surf = {k: [(40.0, None)] for k in npos}
    comp = {"0|0": 0, "0|1": 0, "1|0": 1, "1|1": 1}
    seg_of = {k: ("A-hub-mid" if k[0] == "0" else "B-mid-far") for k in npos}
    links, reach = severance(npos, surf, comp, "0|0", [], seg_of, [])
    got = reach.get(1, {}).get("chain") or []
    return [_f("connectivity", not (got and got[0]["reason"] == "gap"),
               f"a component 400 m from anything reachable is refused with "
               f"its cause named - {got[0]['reason'] if got else 'NOTHING'} "
               f"at {got[0]['separation_m'] if got else 0} m - and not as a "
               "bare 'unreachable' with no diagnosis at all",
               where=(400.0, 0.0),
               measured=got[0]["separation_m"] if got else None,
               numbers=dict(links=len(links)))]


def _wire_only_bridge_record(n: int = 48) -> dict:
    """A `spawn_plan` shaped exactly like every `bridge_body_place` this
    project has ever written: `requires.blobs` EMPTY, a `plan_sha256` naming a
    blob nobody stored, and the literal command list inline in `wire`.

    CONSTRUCTED, NEVER BORROWED.  The chain really does hold 79 of these, but
    a fixture that reads them inherits their mutability -- so the state is
    built here and the real chain is only what MEASURED it.
    """
    wire = [f"spawn_object wood_floor pos={1000 + 2 * i},{500 + i},32.4 "
            f"rot=0,0,0 from=0,0,0" for i in range(n)]
    return {"op": "spawn_plan", "seq": -1, "wire": wire,
            "requires": {"blobs": [], "mods": [], "prefabs": ["wood_floor"]},
            "params": {"role": "bridge_body_place", "site_id": "fixture-x01",
                       "flatten": "FORBIDDEN", "command_count": n,
                       "plan_sha256": "0" * 64,
                       "anchor": {"x": 1048.0, "y": 32.5, "z": 524.0},
                       "reach_m": 28.15}}


class _NoBlobs:
    """A ledger that has none of the blobs the record names, which is the
    state of every one of them."""

    def read_blob(self, _sha):
        import writer as writermod                        # noqa: PLC0415
        raise writermod.LedgerError("no such blob")


def _bridge_piece_evidence(n: int = 48):
    for sub in ("ledger", "roads", "terraform"):
        p = str(I.JUMPSTART / sub)
        if p not in sys.path:
            sys.path.insert(0, p)
    import writer as writermod                            # noqa: PLC0415
    rec = _wire_only_bridge_record(n)
    got = writermod._protected_pieces(rec, _NoBlobs())
    return writermod, rec, got


def _case_a_decks_pieces_are_recoverable_from_its_own_record():
    """THE CHECK THAT WOULD HAVE CAUGHT EVERY DECK STERILISING 2,000-3,500 m2.

    A `flatten: FORBIDDEN` body whose pieces cannot be recovered falls back to
    its record's BOUNDING DISC, and `_flatten_forbidden_check` then refuses
    every later `terrain_write` anywhere inside it.  MEASURED on this chain
    2026-09-22: all 79 `bridge_body_place` records recovered ZERO pieces and
    1,552 discs of 26.16 to 33.13 m, so NOTHING COULD EVER BE GRADED NEAR ANY
    BRIDGE THIS PROJECT BUILDS -- three of one agent's nineteen validated road
    repairs were refused by that fallback in one evening, one of them by a
    deck landed an hour earlier.

    The evidence was never missing.  `boardwalk` hands `emit` the wire inline
    and `emit` persists it, so the record carries its own command list;
    `_protected_pieces` looked only in the blob store.  `structures.py`'s
    reader had read `rec["wire"]` all along -- one reader learned it and the
    guard next door never did.
    """
    n = 48
    _w, _rec, got = _bridge_piece_evidence(n)
    if got is None:
        return Finding(
            check="bridge_piece_evidence", ok=False, measured=0, limit=n,
            message=(f"a bridge body carrying its own {n}-command wire "
                     f"recovered NO pieces, so the flatten guard falls back "
                     f"to its bounding disc and sterilises that whole area "
                     f"against grading"),
            numbers={"recovered": 0, "declared": n})
    if len(got) != n:
        return Finding(
            check="bridge_piece_evidence", ok=False, measured=len(got),
            limit=n,
            message=(f"recovered {len(got)} of {n} declared pieces: a short "
                     f"read looks like a small body, which is worse than the "
                     f"disc because it looks like evidence"),
            numbers={"recovered": len(got), "declared": n})
    return Finding(
        check="bridge_piece_evidence", ok=True, measured=len(got), limit=n,
        message=(f"a bridge body with no stored blob yields all {len(got)} "
                 f"of its {n} declared piece positions from its own record"),
        numbers={"recovered": len(got), "declared": n},
        population=f"{n} spawn_object commands of one constructed "
                   f"bridge_body_place record")


def _case_a_short_wire_is_unreadable_not_a_small_body():
    """THE OTHER DIRECTION, and it is the one that keeps the fix honest: a
    record whose inline wire does not produce the count it DECLARED must come
    back UNREADABLE, so the guard fails closed onto the disc rather than
    protecting a fraction of a body it thinks it has measured."""
    for sub in ("ledger", "roads", "terraform"):
        p = str(I.JUMPSTART / sub)
        if p not in sys.path:
            sys.path.insert(0, p)
    import writer as writermod                            # noqa: PLC0415
    rec = _wire_only_bridge_record(48)
    rec["wire"] = rec["wire"][:3]          # the lh-north shape: 3 of 64
    got = writermod._protected_pieces(rec, _NoBlobs())
    if got is not None:
        return Finding(
            check="bridge_piece_evidence", ok=False, measured=len(got),
            limit=48,
            message=(f"a record declaring 48 commands and carrying 3 was read "
                     f"as a {len(got)}-piece body instead of UNREADABLE"),
            numbers={"recovered": len(got), "declared": 48})
    return Finding(
        check="bridge_piece_evidence", ok=True,
        message=("a wire yielding 3 of its 48 declared commands is "
                 "UNREADABLE, so the guard fails closed onto the disc"),
        numbers={"carried": 3, "declared": 48},
        population="one constructed record, wire truncated to 3 of 48")


def _neuter_bridge_piece_evidence():
    """Put the defect back: the reader consults ONLY the blob store."""
    for sub in ("ledger", "roads", "terraform"):
        p = str(I.JUMPSTART / sub)
        if p not in sys.path:
            sys.path.insert(0, p)
    import writer as writermod                            # noqa: PLC0415
    was = writermod.READ_RECORD_WIRE
    writermod.READ_RECORD_WIRE = False

    def undo():
        writermod.READ_RECORD_WIRE = was
    return undo

def _tile_frame_arms():
    """`roads/boardwalk.py`'s own two arms, imported rather than restated.

    The instrument this section scores lives in the BUILDER, because the
    builder is what decides whether a deck is ever placed: a crossing the
    gate refuses for carrying nobody is a crossing `span_coverage_guard`
    refused to build.  Restating the arms here would be a second
    implementation of the same question, which is how this tree got two
    answers to it in the first place.
    """
    p = str(I.JUMPSTART / "roads")
    if p not in sys.path:
        sys.path.insert(0, p)
    import boardwalk as bw                                # noqa: PLC0415
    return bw, bw.tile_frame_arms()


def _case_a_tile_carries_the_ground_it_stands_on():
    """A POINT STRICTLY INSIDE A DECK TILE MUST BE CARRIED BY IT.

    The state this encodes is the one every crossing on this world was in
    until 2026-09-23: `tile_covers` rotated the piece's VALHEIM COMPASS yaw
    with the MATH-convention FORWARD matrix instead of its inverse, so the
    square it tested was rotated 2*yaw from the tile it named.  A square is
    invariant only under multiples of 90 degrees, so the defect is INVISIBLE
    on an axis-aligned span and worst at 2*yaw = 45 degrees -- which is why
    the fixture is built at yaw 22.5 and not at a convenient angle.

    MEASURED: W10-brgs1-treenear-x01, yaw 170.346 deg, 319 generated pieces
    covering 90 of its 90 declared metres, REFUSED at 79 of 90.  The eleven
    phantom holes read |along| 0.995..0.946 and |lat| 0.989..0.874 against a
    half-extent of 1.000 -- interior on both axes by up to 0.126 m.  The
    deck was never placed, so the 89.44 m channel stayed open.
    """
    _, arms = _tile_frame_arms()
    bad = [a for a in arms if not a["ok"]]
    if bad:
        a = bad[0]
        return Finding(
            check="deck_tile_frame", ok=False,
            measured=max(abs(v) for v in a["read_back"]), limit=1.0,
            message=(f"a point at {a['local']} in a deck tile's OWN frame "
                     f"reads back {a['read_back']} and is "
                     f"{'carried' if a['got'] else 'NOT carried'}; required "
                     f"{a['want']}. The rotation is not the inverse of the "
                     f"one that placed the tile."),
            numbers={"local": list(a["local"]), "read_back": list(a["read_back"]),
                     "carried": a["got"], "required": a["want"],
                     "fixture_yaw_deg": 22.5})
    return Finding(
        check="deck_tile_frame", ok=True,
        message=("a deck tile carries every point strictly inside its own "
                 "2 m square and nothing beyond it, at a yaw 22.5 deg off "
                 "square -- the worst angle for this defect"),
        numbers={"arms": len(arms), "fixture_yaw_deg": 22.5},
        population="one constructed 2 m tile at yaw 22.5 deg, two points: "
                   "(0.990, 0.990) interior and (1.500, 0.000) beyond")


def _case_a_tile_does_not_carry_ground_beyond_it():
    """THE OTHER DIRECTION.  The repair must not be 'cover everything'.

    Without this arm the fix is indistinguishable from widening the tile, and
    a coverage test that carries ground a deck is not over would credit every
    span with metres nobody can stand on -- the failure that is worse than
    the one being fixed, because nothing afterwards can see it.
    """
    _, arms = _tile_frame_arms()
    out = [a for a in arms if a["want"] is False]
    if not out:
        return Finding(
            check="deck_tile_frame", ok=False, measured=0, limit=1,
            message="the builder publishes no ACCEPTING arm for tile_frame, "
                    "so the coverage test has never been shown to refuse",
            numbers={"accepting_arms": 0})
    a = out[0]
    if a["got"]:
        return Finding(
            check="deck_tile_frame", ok=False,
            measured=max(abs(v) for v in a["read_back"]), limit=1.0,
            message=(f"a point {a['local'][0]:.3f} m ALONG a tile's own axis "
                     f"-- {a['local'][0] - 1.0:.3f} m past its edge -- is "
                     f"reported carried"),
            numbers={"local": list(a["local"]),
                     "read_back": list(a["read_back"])})
    return Finding(
        check="deck_tile_frame", ok=True,
        message=(f"a point {a['local'][0]:.3f} m along a tile's own axis is "
                 f"NOT carried: the frame refuses as well as accepts"),
        numbers={"local": list(a["local"]),
                 "read_back": list(a["read_back"])},
        population="one constructed 2 m tile at yaw 22.5 deg")


def _neuter_tile_frame():
    """Put the defect back: rotate the compass yaw FORWARD, as shipped."""
    bw, _ = _tile_frame_arms()
    return bw._neuter_tile_frame()


def _neuter_tile_covers_everything():
    """Put the OPPOSITE defect in: a coverage test that never refuses."""
    bw, _ = _tile_frame_arms()
    return bw._neuter_tile_covers_everything()


def _span_population_arms():
    bw, _ = _tile_frame_arms()
    return bw, bw.span_population_arms()


def _case_a_crossing_that_joins_two_segments_is_buildable():
    """EVERY CROSSING THIS GATE JUDGES MUST BE REACHABLE BY THE BUILDER.

    AN INDEX MUST NAME THE THING IT IS A FUNCTION OF.  `roads/boardwalk.py`
    enumerated `roads/segments.yaml` segments[].crossings[] while this
    section judges `roads/crossings.yaml` declared UNION detected, and a
    crossing that JOINS two segments carries `segment_id: null` and lies in
    NO segment -- so the only deck builder in the tree could not be pointed
    at it.  MEASURED on Ulfsland 2026-09-23: the gate counted 17, the builder
    counted 15, and the two missing were S1 and S2.

    S1's 12.0 m deck is the named remedy for THREE refusals - its own
    crossings row and the `boathouse` and `brgs1` connectivity rows, which
    both publish `link_crossing: "S1"`.

    Under-keying an index is SILENT AND FALSE; over-keying it is SILENT AND
    EXPENSIVE, and the obvious repair for either is the other.  That is why
    this arm has a partner below and why neither is scored alone.
    """
    _, arms = _span_population_arms()
    a = next(x for x in arms if x["name"] == "a_joiner_is_a_buildable_span")
    if not a["ok"]:
        return Finding(
            check="span_population", ok=False, measured=a["enumerated"],
            limit=a["declared"],
            message=(f"crossing {a['crossing_id']} is judged by this gate and "
                     f"is NOT a buildable span: {a['enumerated']} of "
                     f"{a['declared']} declarations are reachable by the "
                     f"builder, and a crossing that joins two segments is "
                     f"not among them"),
            numbers={"crossing": a["crossing_id"], "enumerated": a["enumerated"],
                     "declared": a["declared"]})
    return Finding(
        check="span_population", ok=True,
        message=(f"a crossing with segment_id NULL ({a['crossing_id']}) is "
                 f"one of the {a['enumerated']} buildable spans the builder "
                 f"takes from the {a['declared']} this gate judges"),
        numbers={"crossing": a["crossing_id"], "enumerated": a["enumerated"],
                 "declared": a["declared"]},
        population="roads/crossings.yaml declared UNION detected, the same "
                   "corpus accept/sections/roads.py::crossing_spans reads")


def _case_the_span_population_is_not_merely_empty():
    """THE PARTNER ARM, and without it the other one is worthless.

    `_neuter_span_population_segments_only` flips the joiner arm by REMOVING
    records, and so would a population that returned nothing at all.  A
    proof that a key is load-bearing must therefore also show that a record
    the key is NOT supposed to lose survives it -- otherwise "the neuter
    flipped" is satisfied by the degenerate over-correction, which reads as
    "nothing to build" and is the quietest failure available.
    """
    bw, arms = _span_population_arms()
    a = next(x for x in arms
             if x["name"] == "a_crossing_on_a_segment_is_a_buildable_span")
    undo = bw._neuter_span_population_segments_only()
    try:
        under = bw.span_population_arms()
    finally:
        undo()
    b = next(x for x in under
             if x["name"] == "a_crossing_on_a_segment_is_a_buildable_span")
    if not (a["ok"] and b["got"]):
        return Finding(
            check="span_population", ok=False, measured=b["enumerated"],
            limit=a["enumerated"],
            message=(f"{a['crossing_id']} lies on a segment and must survive "
                     f"a segments-keyed population; buildable went "
                     f"{a['enumerated']} -> {b['enumerated']} and it did not"),
            numbers={"crossing": a["crossing_id"],
                     "enumerated": a["enumerated"],
                     "enumerated_under_the_segments_key": b["enumerated"]})
    return Finding(
        check="span_population", ok=True,
        message=(f"{a['crossing_id']} lies on a segment and survives the "
                 f"segments-keyed population ({a['enumerated']} -> "
                 f"{b['enumerated']} buildable), so the joiner arm's flip is "
                 f"a lost DEPENDENCY and not an emptied index"),
        numbers={"crossing": a["crossing_id"], "enumerated": a["enumerated"],
                 "enumerated_under_the_segments_key": b["enumerated"]},
        population="roads/crossings.yaml declared UNION detected, measured "
                   "twice: once as keyed and once under the segments key")


def _neuter_span_population_segments_only():
    """Put the defect back: key the span population on the SEGMENTS."""
    bw, _ = _span_population_arms()
    return bw._neuter_span_population_segments_only()


def _neuter_span_population_empty():
    """Put the OPPOSITE defect in: a population that names nothing."""
    bw, _ = _span_population_arms()
    return bw._neuter_span_population_empty()


def _connectivity_key_arms() -> dict:
    """The two links, classified by the SHIPPING `road_termination`.

    ONE HARBOURSOUTH-SHAPED ROW, whose repair the writer's own guard
    refuses, AND ONE CLEAN ROW the same guard permits - both measured, both
    returned, so the arms below can express the survivor AS A VERDICT.

    A SURVIVOR EXPRESSED AS AN `assert` PROVES NOTHING HERE.  `run_cases`
    counts an `AssertionError` under a neuter as "the case came out the
    other way", so a case whose survivor is an assertion is stamped PROVEN
    by a neuter that merely EMPTIED the instrument - the degenerate index
    passing while wearing a proof.  Both arms below therefore return a
    Finding whose `ok` is computed from BOTH numbers.
    """
    from .settlements import repair_ownership   # noqa: PLC0415,F401
    lim = STEP_LIMIT_PER_M

    class _OwnWrite:
        """The segment's OWN terrain_write.  Self-contest is excluded, so
        the class turns on the OWNERSHIP answer and on nothing else."""

        def at(self, x, z, margin=0.0, exact_only=True):
            return "terrain_write road_T13_temple_harboursouth (seq 338)"

    def classify(owners, **kw):
        n = dict(component_stations=14, category="b_operator",
                 first_link="step", repairs_needed=2,
                 segment="T13-temple-harboursouth",
                 link_crossing=None, link_over_water=False,
                 carried_by_a_surface=None,
                 own_actor="T13-temple-harboursouth")
        n.update(kw)
        f = Finding(check="connectivity", ok=False, where=(n["link_at"][0],
                                                           n["link_at"][1]),
                    message="a severed destination", numbers=n)
        road_termination([f], owners, writes=_OwnWrite(), spans={},
                         step_limit=lim)
        return f.numbers

    # A - THE DIAGONAL, AND THE REAL ONE.  Ulfsland 7632b3a5674842bd:
    # harboursouth's first missing link is the diagonal (13,-255)->
    # (14,-256) at 1.414 m; the walk refused it through the corner
    # (14,-255) on the 1 m sub-edge (14,-255)->(14,-256), 0.794 m.
    # `harbour_place seq 24` stands at (13.62,-256.38).
    diagonal = classify(
        [(13.62, -256.38, 0.0, "harbour_place", 24)],
        link_at=[13.0, -255.0], link_to=[14.0, -256.0],
        link_bed_a=32.23, link_bed_b=31.436, link_separation_m=1.414,
        link_frozen_by=dict(site_id="harbour-temple-south", seq=24),
        stopped_by_step_m=0.794,
        link_step_m=0.794, link_step_limit_m=round(lim, 4),
        link_step_at=[14.0, -255.0, 14.0, -256.0],
        link_step_ground_a_m=32.23, link_step_ground_b_m=31.436,
        link_step_lattice_m=1.0)
    # B - THE SURVIVOR.  A straight 1 m sub-edge, where the RUN and the
    # CORNER conventions agree by construction, and the nearest owned
    # point stands 2.000 m off - outside the 1.581 m a 0.130 m disposal
    # needs and INSIDE the 2.566 m that sizing the cut off the WHOLE
    # 0.900 m step would demand.  It must stay ACTIONABLE.
    clean = classify(
        [(102.0, 0.0, 0.0, "harbour_place", 24)],
        link_at=[100.0, 0.0], link_to=[101.0, 0.0], link_bed_a=31.0,
        link_bed_b=30.1, link_separation_m=1.0,
        link_frozen_by=None, stopped_by_step_m=0.9,
        link_step_m=0.9, link_step_limit_m=round(lim, 4),
        link_step_at=[100.0, 0.0, 101.0, 0.0],
        link_step_ground_a_m=31.0, link_step_ground_b_m=30.1,
        link_step_lattice_m=1.0)
    return dict(diagonal=diagonal, clean=clean)


def _connectivity_key_finding(headline: str):
    """THE VERDICT IS A CONJUNCTION OF BOTH ARMS, and that is the whole
    design: the row the key must MOVE and the row the key must LEAVE
    ALONE are scored together, so an index that condemns everything fails
    this case exactly as loudly as one that condemns nothing."""
    got = _connectivity_key_arms()
    d, c = got["diagonal"], got["clean"]
    right = (d["termination_class"] == "1A_FORBIDDEN"
             and c["termination_class"] == "ACTIONABLE")
    msg = (f"{headline}: the harboursouth diagonal classifies "
           f"{d['termination_class']} at ({d['repair_at'][0]:.0f},"
           f"{d['repair_at'][1]:.0f}) depth {d['repair_depth_m']} m "
           f"footprint {d['repair_footprint_m']} m against "
           f"{d['ground_owned_by']} at {d['ground_owner_distance_m']} m, "
           f"while the clean 1 m link classifies {c['termination_class']} "
           f"at footprint {c['repair_footprint_m']} m with its owner "
           f"{c['ground_owner_distance_m']} m off")
    return Finding(
        check="connectivity_repair_key", ok=not right,
        where=(d["repair_at"][0], d["repair_at"][1]),
        measured=d["repair_footprint_m"], limit=d["ground_owner_distance_m"],
        message=msg,
        numbers=dict(
            depends_on=CONNECTIVITY_REPAIR_DEPENDS_ON,
            diagonal_class=d["termination_class"],
            diagonal_keyed_on=d["repair_keyed_on"],
            diagonal_repair_at=d["repair_at"],
            diagonal_depth_m=d["repair_depth_m"],
            diagonal_footprint_m=d["repair_footprint_m"],
            diagonal_owner=d["ground_owned_by"],
            diagonal_owner_distance_m=d["ground_owner_distance_m"],
            clean_class=c["termination_class"],
            clean_footprint_m=c["repair_footprint_m"],
            clean_owner_distance_m=c["ground_owner_distance_m"]),
        population="two severed links: one diagonal whose failed sub-edge "
                   "is not between its own endpoints, one straight 1 m "
                   "edge where every convention agrees")


def _case_a_severed_link_is_keyed_on_the_sub_edge_the_walk_failed():
    """THE ROW THAT CONTRADICTED ITSELF IN TWO ADJACENT FIELDS.

    MEASURED, Ulfsland save 7632b3a5674842bd, artefact
    /tmp/gate_final2.json, `roads/connectivity` harboursouth:

        category           b_operator   <- `_link_category(frozen=True)`,
                           "the ground is protected; no write moves it"
        link_frozen_by     harbour-temple-south seq 24, piece
                           (13.62,-256.38), clear 1.0
        termination_class  ACTIONABLE   <- the classifier's SECOND OPINION

    and one check over, `roads/traversability` T13-temple-harboursouth
    station 423 - THE SAME 1 m EDGE, (14,-255)->(14,-256), 0.794 m - was
    already 1A_FORBIDDEN at footprint 1.445 m against `harbour_place
    seq 24` at 1.431 m.  One fact, two instruments, two answers.

    THE CAUSE IS THE KEY.  `road_termination` sized the connectivity
    repair from `abs(bed_a - bed_b) - step_limit * link_separation_m` at
    `link_at`: the RUN convention over the link's 1.414 m straight line,
    at the link's FIRST endpoint, for the depth that leaves the step
    EXACTLY ON the limit.  0.794 - 0.7813 * 1.414 = -0.311, clamped to
    0.0 - so the ownership question was asked about a repair of DEPTH
    ZERO and came back with the BARE lattice diagonal, 1.414 m, which is
    an index answering with no dependency at all.  The owner sits at
    1.513 m from `link_at` and at 1.431 m from the sub-edge's high end,
    and the two footprints straddle it.

    The walk does not use the run convention: a diagonal is TWO 1 m mesh
    edges through the easier corner, each judged against the plain
    0.7813 m - which is why it refused at all, and why `roads/cone.py`
    judges BOTH conventions rather than picking one.  See
    `CONNECTIVITY_REPAIR_DEPENDS_ON`.
    """
    return _connectivity_key_finding(
        "the repair a severed link prescribes is keyed on the sub-edge "
        "the walk refused")


def _case_the_connectivity_key_leaves_an_unowned_link_alone():
    """THE PARTNER ARM, AND WITHOUT IT THE OTHER ONE IS WORTHLESS.

    The corner convention and the disposal depth can only ever make a
    footprint LARGER, so a key that simply declared every severed link
    owned would flip the arm above and be stamped PROVEN.  This arm is
    scored on the same conjunction and is protected by the OPPOSITE
    neuter: sizing the cut off the WHOLE step rather than off the part
    above the limit - the over-correction `roads/cone.py` names in its own
    freeze (0.794 m instead of 0.0127 m inflates the footprint from
    1.431 m to 2.430 m and freezes a vertex the guard permits).

    MEASURED here: the clean link's own 0.900 m step needs a 0.130 m
    disposal and a 1.581 m footprint, its owner stands 2.000 m off and it
    is ACTIONABLE; sized off the whole 0.900 m it needs 2.566 m and the
    same owner condemns it.  The two arms therefore separate A LOST
    DEPENDENCY from AN INDEX THAT CONDEMNS EVERYTHING, which one arm
    cannot.
    """
    return _connectivity_key_finding(
        "a severed link whose repair nobody owns survives the sub-edge key")


def _neuter_connectivity_key_run_at_the_endpoint():
    """RESTORE THE SHIPPED KEY: the RUN convention over the link's
    straight-line separation, at the link's FIRST endpoint, sized onto the
    walker's limit rather than onto the slope a disposal solves at."""
    import sys as _sys                                     # noqa: PLC0415
    mod = _sys.modules[__name__]
    real = mod._connectivity_repair_key

    def shipped(n, x, z, step_limit):
        ba, bb = n.get("link_bed_a"), n.get("link_bed_b")
        sep = max(float(n.get("link_separation_m") or 1.0), 1.0)
        over = (abs(ba - bb) - step_limit * sep
                if ba is not None and bb is not None else 0.0)
        lx, lz = (n.get("link_at") or (x, z))[:2]
        return lx, lz, max(over, 0.0), "the shipped key"

    mod._connectivity_repair_key = shipped

    def undo():
        mod._connectivity_repair_key = real
    return undo


def _neuter_connectivity_key_sized_off_the_whole_step():
    """PUT THE OPPOSITE DEFECT IN: key the repair on the WHOLE step rather
    than on the part of it above the limit, so the footprint is sized for
    a cut no disposal makes and the index condemns ground the guard would
    have let anyone move."""
    import sys as _sys                                     # noqa: PLC0415
    mod = _sys.modules[__name__]
    real = mod._connectivity_repair_key

    def whole_step(n, x, z, step_limit):
        hx, hz, _depth, why = real(n, x, z, step_limit)
        step = n.get("link_step_m")
        if step is None:
            ba, bb = n.get("link_bed_a"), n.get("link_bed_b")
            step = abs(ba - bb) if ba is not None and bb is not None else 0.0
        return hx, hz, float(step), why

    mod._connectivity_repair_key = whole_step

    def undo():
        mod._connectivity_repair_key = real
    return undo


# ---------------------------------------------------------------------------
# WHICH WORLD'S LOCATIONS DOES A ROAD GATE READ -- `poi.LOCATION_DUMP_DEPENDS_ON`
# ---------------------------------------------------------------------------
#
# THE STATE THAT SHIPPED, MEASURED 2026-09-24 on Fresh42.  `roads/ribbon.py
# build --segment S7-stathub-wtnorth --validate` exits 3 and prints, in ONE
# run of ONE file:
#
#   plan-time location gate: clear over 12376 generator instances, 0 firm
#   VIOLATION ShipSetting01 (124.2,263.8) min_dist 18.2 required 25.0
#                                                        short_by 6.8
#   REFUSING: a CARRIAGEWAY sample is inside a location's hard radius ...
#             Re-route the leg
#
# 12,376 is Fresh42's instance count.  The refusing `ShipSetting01` is
# PIRATE68's: within `reach + 9 m` of S7's 87-node centreline Pirate68 holds
# exactly one instance and Fresh42 holds ZERO.  The plan-time gate asked
# `poi.load()`, which resolves the dump by SEED; `location_check` and
# `poi_standoff_discs` named `/tmp/settle/loc3/f6fe167f4fcd.json` -- one
# seed's content hash -- with no seed check at all.  The same frozen hash
# chose the batter's keep-out discs, so all 957 `poi` wall clips across the
# 14 Fresh42 segments were clipped against Ulfsland's locations.
#
# S7's own earthwork was CLEAN when it was refused: rim transfer 0 caused,
# 0 pre-existing.
#
# THE FIXTURE IS BUILT IN MEMORY AND THE NEUTER CANNOT REACH IT.  Two dumps,
# a HOME seed and a FOREIGN one, each written from a dict in this function to
# a temp root with the `index.tsv` the producer writes; one 40 m carriageway
# strip; one location type of reach 24.0 m, standing 60.0 m off the strip in
# HOME and 18.2 m off it in FOREIGN.  A neuter may swap only the RESOLVER --
# which file a seed names -- never the ground, never the strip and never the
# radius.  The verdict itself comes from `settlements/clearance.py`, the
# canonical instrument, unmodified.
LOCATION_POPULATION_HOME_SEED = "AcceptHome"
LOCATION_POPULATION_FOREIGN_SEED = "AcceptForeign"
LOCATION_POPULATION_REACH_M = 24.0
LOCATION_POPULATION_HOME_OFFSET_M = 60.0
LOCATION_POPULATION_FOREIGN_OFFSET_M = 18.2


def _location_population_fixture(td):
    """TWO WORLDS ON DISK, both authored here, with the producer's index.

    Returns the temp root.  Nothing is read from any real dump: a fixture
    that borrowed one would inherit that file's mutability, which is the
    failure mode `Case.neuter` exists to catch.
    """
    root = Path(td)
    lines = []
    for seed, off in ((LOCATION_POPULATION_HOME_SEED,
                       LOCATION_POPULATION_HOME_OFFSET_M),
                      (LOCATION_POPULATION_FOREIGN_SEED,
                       LOCATION_POPULATION_FOREIGN_OFFSET_M)):
        doc = {
            "seedNameFromWorld": seed,
            "locationTypes": [{"prefab": "ShipSetting01",
                               "name": "ShipSetting01",
                               "exteriorRadius": LOCATION_POPULATION_REACH_M,
                               "interiorRadius": 0.0,
                               "znviewPieces": 9,
                               "znviewReachM": 0.0}],
            "locations": [{"name": "ShipSetting01", "prefab": "ShipSetting01",
                           "x": 20.0, "z": off, "y": 30.0,
                           "exteriorRadius": LOCATION_POPULATION_REACH_M,
                           "interiorRadius": 0.0, "quantity": 200,
                           "prioritized": False, "centerFirst": False}],
        }
        (root / f"{seed}.json").write_text(json.dumps(doc))
        lines.append(f"{seed}\t{seed}.json")
    (root / "index.tsv").write_text("\n".join(lines) + "\n")
    return root


def _location_population_arms() -> dict:
    """Ask the CANONICAL gate's question of each seed, through the resolver.

    The consumer modelled here is the one that shipped: an instrument that
    wants "the locations of this world", resolves a file, hands it to
    `clearance.load` and asks `violations_for_samples` over its carriageway.
    Only the RESOLVER is swappable; `clearance.py` is untouched.
    """
    import tempfile                                        # noqa: PLC0415
    p = str(I.JUMPSTART / "roads")
    if p not in sys.path:
        sys.path.insert(0, p)
    q = str(I.JUMPSTART)
    if q not in sys.path:
        sys.path.insert(0, q)
    import poi as poimod                                   # noqa: PLC0415
    from settlements import clearance                      # noqa: PLC0415

    strip = [(float(x), 0.0) for x in range(0, 41)]
    out = {}
    with tempfile.TemporaryDirectory() as td:
        root = _location_population_fixture(td)
        for seed in (LOCATION_POPULATION_HOME_SEED,
                     LOCATION_POPULATION_FOREIGN_SEED):
            try:
                dump = poimod.location_dump(seed, roots=(root,))
            except SystemExit as exc:                      # noqa: PERF203
                out[seed] = {"verdict": "UNRESOLVED", "why": str(exc),
                             "dump": None, "violations": []}
                continue
            L = clearance.load(str(dump))
            v = L.violations_for_samples(strip, half_width_m=0.0,
                                         destructive=False)
            out[seed] = {
                "verdict": "refuse" if v else "clear",
                "dump": Path(dump).name,
                "dump_seed": poimod.dump_seed(json.loads(Path(dump).read_text())),
                "violations": v,
                "instances": len(L),
            }
    return out


def _location_population_finding(headline: str):
    """THE VERDICT IS A CONJUNCTION OF BOTH ARMS.

    HOME must come out CLEAR and FOREIGN must come out REFUSE, from the SAME
    resolver over the SAME ground.  A resolver that always returns the
    foreign file reddens the first arm; one that always returns the home file
    reddens the second; one that returns nothing at all reddens both.  No
    single degenerate answer satisfies this.
    """
    got = _location_population_arms()
    h = got[LOCATION_POPULATION_HOME_SEED]
    f = got[LOCATION_POPULATION_FOREIGN_SEED]
    right = (h["verdict"] == "clear" and f["verdict"] == "refuse"
             and h["dump_seed"] == LOCATION_POPULATION_HOME_SEED
             and f["dump_seed"] == LOCATION_POPULATION_FOREIGN_SEED)
    short = (f["violations"][0]["short_by_m"] if f["violations"] else None)
    msg = (f"{headline}: seed {LOCATION_POPULATION_HOME_SEED} reads "
           f"{h['dump']} (declared {h['dump_seed']}) and its carriageway is "
           f"{h['verdict']}, while seed {LOCATION_POPULATION_FOREIGN_SEED} "
           f"reads {f['dump']} (declared {f['dump_seed']}) and the SAME "
           f"carriageway is {f['verdict']}"
           + (f", short by {short} m" if short is not None else ""))
    return Finding(
        section="roads", check="location_population_is_keyed_on_the_seed",
        ok=not right,
        measured=(0.0 if short is None else float(short)),
        limit=LOCATION_POPULATION_FOREIGN_OFFSET_M,
        message=msg,
        numbers=dict(
            depends_on=_poi_location_dump_depends_on(),
            home_seed=LOCATION_POPULATION_HOME_SEED,
            home_dump=h["dump"], home_dump_seed=h["dump_seed"],
            home_verdict=h["verdict"], home_instances=h.get("instances"),
            foreign_seed=LOCATION_POPULATION_FOREIGN_SEED,
            foreign_dump=f["dump"], foreign_dump_seed=f["dump_seed"],
            foreign_verdict=f["verdict"],
            foreign_short_by_m=short,
            reach_m=LOCATION_POPULATION_REACH_M,
            home_offset_m=LOCATION_POPULATION_HOME_OFFSET_M,
            foreign_offset_m=LOCATION_POPULATION_FOREIGN_OFFSET_M),
        population="two authored location dumps, one carriageway strip, one "
                   "location type of reach 24.0 m -- the ground is identical "
                   "in both arms and only the resolver differs")


def _poi_location_dump_depends_on():
    p = str(I.JUMPSTART / "roads")
    if p not in sys.path:
        sys.path.insert(0, p)
    import poi as poimod                                   # noqa: PLC0415
    return poimod.LOCATION_DUMP_DEPENDS_ON


def _case_a_road_gate_reads_THIS_seeds_locations():
    """THE ARM THE FROZEN PATH KILLS.

    A road on the HOME seed, whose only nearby location stands 60.0 m off a
    40 m carriageway against a 25.0 m requirement, must come out CLEAR.  It
    is the S7 state: an alignment that clears every location in its own
    world.
    """
    return _location_population_finding(
        "the location set a road gate reads is keyed on the run's seed")


def _case_the_seed_key_still_refuses_a_REAL_breach():
    """THE SURVIVOR, AND WITHOUT IT THE OTHER ARM IS WORTHLESS.

    A resolver that returned an empty set, or that always named the home
    dump, would make the first arm green for the wrong reason.  This arm
    stands on the FOREIGN seed, where the identical strip runs 18.2 m from a
    24.0 m-reach location and the gate must still refuse by 6.8 m.  Scored on
    the same conjunction, so a resolver that has lost its population fails
    here exactly as loudly as one that reads the wrong world fails there.
    """
    return _location_population_finding(
        "a seed-keyed location set still refuses a carriageway inside a "
        "real stand-off")


def _neuter_location_dump_frozen_to_one_content_hash():
    """RESTORE THE SHIPPED RESOLVER: a fixed tuple of paths, first one that
    exists, no seed and no header check -- `ribbon.py:1967`, `ribbon.py:2073`,
    `north.py:778` and `clear.py:2020` as they stood.  Here it lands on the
    FOREIGN dump, so the HOME arm's clean road is refused by a location
    standing in another world, which is S7 exactly."""
    p = str(I.JUMPSTART / "roads")
    if p not in sys.path:
        sys.path.insert(0, p)
    import poi as poimod                                   # noqa: PLC0415
    real = poimod.location_dump

    def frozen(seed=None, roots=poimod.DUMP_ROOTS):
        for root in roots:
            q = Path(root) / f"{LOCATION_POPULATION_FOREIGN_SEED}.json"
            if q.exists():
                return q
        return real(seed, roots)

    poimod.location_dump = frozen

    def undo():
        poimod.location_dump = real
    return undo


def _neuter_location_dump_always_the_home_world():
    """THE OPPOSITE OVER-CORRECTION, and the one a fix aimed at the first arm
    invites: a resolver that always names the dump the caller hoped for.  It
    makes every road clear -- including the FOREIGN seed's carriageway 18.2 m
    inside a 25.0 m stand-off -- so the survivor arm reddens and the harness
    reports the key as not load-bearing rather than stamping it PROVEN."""
    p = str(I.JUMPSTART / "roads")
    if p not in sys.path:
        sys.path.insert(0, p)
    import poi as poimod                                   # noqa: PLC0415
    real = poimod.location_dump

    def always_home(seed=None, roots=poimod.DUMP_ROOTS):
        for root in roots:
            q = Path(root) / f"{LOCATION_POPULATION_HOME_SEED}.json"
            if q.exists():
                return q
        return real(seed, roots)

    poimod.location_dump = always_home

    def undo():
        poimod.location_dump = real
    return undo


# ---------------------------------------------------------------------------
# MAY A WET RUN STAY TERRAIN -- `route.FORD_DEPENDS_ON`
# ---------------------------------------------------------------------------
#
# THE STATE THAT SHIPPED, MEASURED 2026-09-24.  `ribbon.py`'s water-sign gate
# refuses a write that raises a sample the SEED drew below the 30.0 m plane to
# or above it: the generated field is the only record the water was there, and
# no later write restores it.  `route.find_crossings` decided whether a wet
# run stayed TERRAIN (a "ford") on its LENGTH and then on its DEPTH, and never
# on that.  A ford is filled to `WATER_LEVEL + ROAD_FREEBOARD_M`, so every
# submerged station under one ends a metre ABOVE the plane by construction.
#
# Over all three plans -- Fresh42 22 fords on 9 of 14 segments, Ulfsland 32 on
# 19, the northern trunk 51 on 5 -- 618 of 618 submerged centreline stations
# under those 105 fords end at or above the plane, worst +6.97 m
# (S13-stathub-dock).  Fresh42's T1-portalhub-temple reached the gate: 3
# fords, 8 submerged centreline stations raised by up to 4.23 m, `ribbon.py
# --validate` rc=8, "369 of 411 submerged sample(s) raised, worst +5.119 m at
# [-105,-274]", on the same run as "crossing premise: CLEAR -- 0 of 0
# crossing(s) on this segment asked".
#
# THE FIXTURE IS TWO NUMBERS, NOT A CORPUS.  A run 1.00 m below the plane --
# exactly the old `ford_max_depth_m` -- and a run 0.50 m ABOVE it and inside
# the freeboard band.  A neuter may swap only the RULE.
FORD_CASE_SUBMERGED_DEPTH_M = 1.00
FORD_CASE_SHORE_DEPTH_M = -0.50
FORD_CASE_RUN_M = 6.0


def _ford_rule_arms() -> dict:
    p = str(I.JUMPSTART / "roads")
    if p not in sys.path:
        sys.path.insert(0, p)
    import route as routemod                              # noqa: PLC0415
    fb = routemod.WATER_LEVEL + routemod.ROAD_FREEBOARD_M
    drowns = routemod.ford_rule(
        True, fb - (routemod.WATER_LEVEL - FORD_CASE_SUBMERGED_DEPTH_M),
        FORD_CASE_SUBMERGED_DEPTH_M)
    shore = routemod.ford_rule(
        True, fb - (routemod.WATER_LEVEL - FORD_CASE_SHORE_DEPTH_M),
        FORD_CASE_SHORE_DEPTH_M)
    return {"drowns_is_ford": bool(drowns), "shore_is_ford": bool(shore),
            "freeboard_y": float(fb),
            "water_level": float(routemod.WATER_LEVEL),
            "depends_on": routemod.FORD_DEPENDS_ON}


def _ford_rule_finding(headline: str):
    """THE VERDICT IS A CONJUNCTION OF BOTH ARMS: the run that drowns water
    must NOT be a ford, and the shore that drowns none must STILL be one.  A
    rule that calls everything a ford reddens the first; a rule that calls
    nothing a ford reddens the second."""
    got = _ford_rule_arms()
    right = (got["drowns_is_ford"] is False and got["shore_is_ford"] is True)
    msg = (f"{headline}: a run {FORD_CASE_SUBMERGED_DEPTH_M:.2f} m BELOW the "
           f"{got['water_level']:.1f} m plane is "
           f"{'a ford' if got['drowns_is_ford'] else 'a CROSSING'}, while a "
           f"shore {-FORD_CASE_SHORE_DEPTH_M:.2f} m ABOVE it and inside the "
           f"{got['freeboard_y']:.1f} m freeboard band is "
           f"{'a ford' if got['shore_is_ford'] else 'a CROSSING'}")
    return Finding(
        section="roads", check="a_ford_is_keyed_on_the_water_it_removes",
        ok=not right,
        measured=FORD_CASE_SUBMERGED_DEPTH_M,
        limit=0.0,
        message=msg,
        numbers=dict(depends_on=got["depends_on"],
                     submerged_run_depth_m=FORD_CASE_SUBMERGED_DEPTH_M,
                     submerged_run_is_ford=got["drowns_is_ford"],
                     shore_run_depth_m=FORD_CASE_SHORE_DEPTH_M,
                     shore_run_is_ford=got["shore_is_ford"],
                     water_level_m=got["water_level"],
                     freeboard_y_m=got["freeboard_y"],
                     run_m=FORD_CASE_RUN_M),
        population="two wet runs, one below the water plane and one above it "
                   "inside the freeboard band -- the only thing that differs "
                   "is whether the fill removes water")


def _case_a_run_that_DROWNS_water_is_not_terrain():
    """THE ARM THE DEPTH KEY KILLS.  1.00 m of water is exactly the old
    `ford_max_depth_m`, so the shipped rule called it a causeway and the road
    filled it to freeboard -- which is the write the water-sign gate refuses,
    after the whole rasterisation."""
    return _ford_rule_finding(
        "a wet run is terrain only if the road removes no water")


def _case_a_shore_that_DROWNS_NOTHING_stays_terrain():
    """THE SURVIVOR.  'No wet run is ever a ford' would flip the other arm and
    be stamped PROVEN while handing Crossings a deck over dry shore.  MEASURED
    that this population is not empty: 6 of Fresh42's 22 fords, 12 of
    Ulfsland's 32 and 15 of the northern trunk's 51 sit entirely at or above
    the plane."""
    return _ford_rule_finding(
        "a shore inside the freeboard band is still terrain")


def _neuter_ford_keyed_on_depth_and_length():
    """RESTORE THE SHIPPED KEY: `depth_m <= 1.0 or run_m <= 8.0`, which calls
    a metre of standing water a causeway."""
    p = str(I.JUMPSTART / "roads")
    if p not in sys.path:
        sys.path.insert(0, p)
    import route as routemod                              # noqa: PLC0415
    real = routemod.ford_rule

    def shipped(any_wet, need_fill_m, depth_m):
        return (bool(any_wet) and need_fill_m <= routemod.CUT_FILL_MAX
                and (depth_m <= 1.0 or FORD_CASE_RUN_M <= 8.0))

    routemod.ford_rule = shipped

    def undo():
        routemod.ford_rule = real
    return undo


def _neuter_ford_abolished_entirely():
    """THE OPPOSITE OVER-CORRECTION: no wet run is ever terrain.  It satisfies
    the drowning arm for the wrong reason and hands Crossings a deck over a
    shore with no water under it, so the survivor arm must redden."""
    p = str(I.JUMPSTART / "roads")
    if p not in sys.path:
        sys.path.insert(0, p)
    import route as routemod                              # noqa: PLC0415
    real = routemod.ford_rule

    def never(any_wet, need_fill_m, depth_m):
        return False

    routemod.ford_rule = never

    def undo():
        routemod.ford_rule = real
    return undo


# ---------------------------------------------------------------------------
# WHICH STATIONS ARE OVER WATER -- `route.WET_DEPENDS_ON`
# ---------------------------------------------------------------------------
#
# THE STATE THAT SHIPPED, MEASURED 2026-09-24, AFTER `ford_rule` had already
# landed.  `route.find_crossings` decided a station was over water by reading
# the CENTRELINE generated height, `t`.  The write benches a LEVEL surface out
# to `route.footprint_half_m(width)` -- 4.0 m either side on a 6 m trunk --
# `TerrainComp::ApplyToHeightmap` clamps PER SAMPLE, and `ribbon.water_sign`
# judges EVERY sample of that bench.  The centreline is a strict SUBSET of
# what the write moves.
#
# So a road on the crest of a bank, with its shoulder hanging over the bay,
# was never a crossing candidate at all: the run was never flagged, ford_rule
# was never asked, and the writer filled the bay.
#
# MEASURED on Fresh42 `T1-portalhub-temple` with its three fords ALREADY
# turned into three declared bridges by `ford_rule`: `ribbon.py --validate`
# still refused at `water_sign`, 274 of 343 submerged samples raised, and 179
# OF THOSE 274 WERE CARRIAGEWAY-BAND (lateral <= 4.75 m) on stations `t`
# called dry.  Median lateral offset of the survivors 3.75 m -- inside the
# 4.0 m bench, outside the line that was being measured.  Re-keying the test
# on the footprint minimum took the whole network's drained samples from
# 1,753 to 1,111 and turned `T13-temple-harboursouth` from rc 8 to rc 0.
#
# THIS IS THE FOURTH TIME ONE OBJECT HAS BEEN CERTIFIED ON THE CENTRELINE AND
# CONSUMED OVER THE FOOTPRINT.  `network.transverse_window` already closed it
# for the APPLY CLAMP after N4-mid2-terminus reported `stations_over_clamp: 0`
# against 71 real breaches, every one at lat 3.99-4.12 m.  The wet test was
# left behind, and nothing compared the two.
#
# THE FIXTURE IS FOUR NUMBERS AND IT IS FIXED IN EVERY ARM.  A neuter may swap
# only the PREDICATE; it may not touch which stations exist or what height any
# of them reads.  `_flat_patch` in `ribbon.py` is built in memory for the same
# reason -- a fixture whose own cover is the variable is where a defect hides.
WET_CASE_STATIONS = 21
WET_CASE_BANK = (8, 12)
# The centreline: a full 0.60 m ABOVE the freeboard line, so the OLD rule
# reads it dry with room to spare and the case cannot pass by rounding.
WET_CASE_CENTRELINE_Y = 31.60
# The bench edge 4.0 m out, over the bay: 1.60 m BELOW the plane.
WET_CASE_FOOTPRINT_Y = 28.40
# The SURVIVOR's bench: the whole levelled surface above the freeboard line.
WET_CASE_DRY_FOOTPRINT_Y = 31.60
# What the BATTER REACH would read 10.5 m out on the survivor -- under the
# plane, and NOT this write's to answer for.  Present in every arm, read only
# by the over-reach neuter.
WET_CASE_BATTER_Y = 29.20


def _wet_key_arms() -> dict:
    """Both arms over ONE fixture, through the SHIPPING function.

    Arm 1, THE BANK: centreline dry, bench edge under the plane.  It must come
    back as exactly one crossing record covering exactly the bank stations.
    Arm 2, THE SURVIVOR: the whole bench above the freeboard line.  It must
    come back with NOTHING -- expressed as a VERDICT rather than an assert,
    because `run_cases` reads an AssertionError as a flip and an assert-shaped
    survivor would stamp a predicate that flags every station PROVEN.
    """
    import numpy as np                                     # noqa: PLC0415
    p = str(I.JUMPSTART / "roads")
    if p not in sys.path:
        sys.path.insert(0, p)
    import route as routemod                               # noqa: PLC0415
    n = WET_CASE_STATIONS
    a, b = WET_CASE_BANK
    pts = [(2.0 * k, 0.0) for k in range(n)]
    s = np.arange(n, dtype=np.float64) * 2.0
    t = np.full(n, WET_CASE_CENTRELINE_Y, dtype=np.float64)
    foot = np.full(n, WET_CASE_CENTRELINE_Y, dtype=np.float64)
    foot[a:b + 1] = WET_CASE_FOOTPRINT_Y
    dry = np.full(n, WET_CASE_DRY_FOOTPRINT_Y, dtype=np.float64)

    bank_x, bank_flag, bank_f = routemod.find_crossings(
        "wet-case-bank", pts, s, t, [], t_foot=foot)
    dry_x, dry_flag, dry_f = routemod.find_crossings(
        "wet-case-dry", pts, s, t, [], t_foot=dry)

    bank_recs = list(bank_x) + list(bank_f)
    rec = bank_recs[0] if len(bank_recs) == 1 else None
    return {
        "bank_records": len(bank_recs),
        "bank_stations": list(rec["stations"]) if rec else [],
        "bank_flagged": [int(k) for k in np.nonzero(np.asarray(bank_flag))[0]],
        "bank_footprint_min_y": (rec["clamp_evidence"]["footprint_min_y"]
                                 if rec else None),
        "bank_centreline_min_y": (rec["clamp_evidence"]["centreline_min_y"]
                                  if rec else None),
        "dry_records": len(list(dry_x)) + len(list(dry_f)),
        "dry_flagged": int(np.count_nonzero(np.asarray(dry_flag))),
        "depends_on": routemod.WET_DEPENDS_ON,
        "bench_half_m": routemod.footprint_half_m(6.0),
        "freeboard_y": routemod.WATER_LEVEL + routemod.ROAD_FREEBOARD_M,
    }


def _wet_key_finding(headline: str):
    """THE VERDICT IS A CONJUNCTION OF BOTH ARMS.  The bank must be found and
    the dry bench must be left alone.  A predicate that reads the centreline
    reddens the first; a predicate that reaches out to the batter -- or one
    that simply calls everything wet -- reddens the second."""
    got = _wet_key_arms()
    a, b = WET_CASE_BANK
    found = (got["bank_records"] == 1
             and got["bank_stations"] == [a, b]
             and got["bank_flagged"] == list(range(a, b + 1))
             and got["bank_footprint_min_y"] == WET_CASE_FOOTPRINT_Y)
    survived = got["dry_records"] == 0 and got["dry_flagged"] == 0
    right = found and survived
    msg = (f"{headline}: a station whose centreline reads "
           f"{WET_CASE_CENTRELINE_Y:.2f} m -- "
           f"{WET_CASE_CENTRELINE_Y - got['freeboard_y']:+.2f} m against the "
           f"{got['freeboard_y']:.1f} m freeboard line, so DRY on the line -- "
           f"with its bench edge {got['bench_half_m']:g} m out reading "
           f"{WET_CASE_FOOTPRINT_Y:.2f} m, "
           f"{'IS' if found else 'IS NOT'} a crossing "
           f"({got['bank_records']} record(s), stations "
           f"{got['bank_stations']}, footprint floor "
           f"{got['bank_footprint_min_y']} against centreline floor "
           f"{got['bank_centreline_min_y']}); and a bench entirely above the "
           f"freeboard line "
           f"{'is left alone' if survived else 'IS ALSO CALLED WET'} "
           f"({got['dry_records']} record(s), {got['dry_flagged']} station(s) "
           f"flagged)")
    return Finding(
        section="roads", check="wet_is_keyed_on_the_bench_the_write_levels",
        ok=not right,
        measured=WET_CASE_FOOTPRINT_Y,
        limit=got["freeboard_y"],
        message=msg,
        numbers=dict(depends_on=got["depends_on"],
                     centreline_y=WET_CASE_CENTRELINE_Y,
                     footprint_y=WET_CASE_FOOTPRINT_Y,
                     dry_footprint_y=WET_CASE_DRY_FOOTPRINT_Y,
                     batter_reach_y=WET_CASE_BATTER_Y,
                     bench_half_m=got["bench_half_m"],
                     freeboard_y=got["freeboard_y"],
                     bank_records=got["bank_records"],
                     bank_stations=got["bank_stations"],
                     bank_footprint_min_y=got["bank_footprint_min_y"],
                     bank_centreline_min_y=got["bank_centreline_min_y"],
                     dry_records=got["dry_records"],
                     dry_flagged=got["dry_flagged"]),
        population="one 21-station line at two footprint readings -- a bench "
                   "hanging over a bay and a bench entirely above the "
                   "freeboard line. The centreline height is IDENTICAL in "
                   "both, so the only thing that can separate them is which "
                   "set of samples the rule reads")


def _case_a_bench_over_a_bay_is_a_crossing():
    """THE ARM THE CENTRELINE KEY KILLS.  The line stands 0.60 m clear of the
    freeboard band and its shoulder hangs 1.60 m under the plane, so the
    shipped rule saw dry ground and the writer filled the bay -- which is the
    write `water_sign` refuses, after the whole rasterisation."""
    return _wet_key_finding(
        "a station is over water if the BENCH is, not if the line is")


def _case_a_bench_clear_of_the_water_is_left_alone():
    """THE SURVIVOR.  'Reach further out' is the obvious over-correction and
    it is wrong in a way that costs the whole network: the BATTER follows
    ground at grade instead of filling to road height, so water inside its
    reach is not water this write removes. Keyed on the 10.5 m batter reach
    instead of the 4.0 m bench, every shoreline road in the world becomes a
    deck. MEASURED that this population is not empty: with the bench key
    landed, 5 of Fresh42's 14 segments read water sign CLEAR and 365 of the
    1,111 samples still refused are batter-band on terrain stations, none of
    which the bench rule claims."""
    return _wet_key_finding(
        "a bench clear of the freeboard line is terrain")


def _neuter_wet_keyed_on_the_centreline():
    """RESTORE THE SHIPPED KEY: read the CENTRELINE height, which is what
    `find_crossings` did until the footprint window was handed to it.  The
    fixture is untouched -- only the predicate changes, and it changes to the
    exact expression that shipped: `t <= WATER_LEVEL + ROAD_FREEBOARD_M`."""
    import numpy as np                                     # noqa: PLC0415
    p = str(I.JUMPSTART / "roads")
    if p not in sys.path:
        sys.path.insert(0, p)
    import route as routemod                               # noqa: PLC0415
    real = routemod.footprint_wet

    def centreline(t_foot):
        # The shipped rule read `t`, and in this fixture `t` is one constant.
        line = np.full(len(np.asarray(t_foot)), WET_CASE_CENTRELINE_Y)
        return line <= routemod.WATER_LEVEL + routemod.ROAD_FREEBOARD_M

    routemod.footprint_wet = centreline

    def undo():
        routemod.footprint_wet = real
    return undo


def _neuter_wet_keyed_on_the_batter_reach():
    """THE OPPOSITE OVER-CORRECTION: widen the extent from the LEVEL BENCH to
    the BATTER REACH.  It satisfies the bank arm for the wrong reason and
    calls a bench standing well clear of the water wet because water lies
    10.5 m away, which decks every shoreline road there is -- so the survivor
    arm must redden.  Predicate only; the fixture's heights are unchanged."""
    import numpy as np                                     # noqa: PLC0415
    p = str(I.JUMPSTART / "roads")
    if p not in sys.path:
        sys.path.insert(0, p)
    import route as routemod                               # noqa: PLC0415
    real = routemod.footprint_wet

    def overreach(t_foot):
        tf = np.asarray(t_foot, dtype=np.float64)
        reach = np.full(len(tf), WET_CASE_BATTER_Y)
        line = routemod.WATER_LEVEL + routemod.ROAD_FREEBOARD_M
        return (tf <= line) | (reach <= line)

    routemod.footprint_wet = overreach

    def undo():
        routemod.footprint_wet = real
    return undo


# ---------------------------------------------------------------------------
# WHAT A BATTER MAY DO TO WATER -- `route.BATTER_WATER_DEPENDS_ON`
# ---------------------------------------------------------------------------
#
# THE STATE THAT SHIPPED, MEASURED 2026-09-24, AFTER both `ford_rule` AND
# `WET_DEPENDS_ON` had landed.  `ribbon.batter_water_gate` refused a batter
# sample that takes seed-submerged ground DOWN and PERMITTED every RAISE, at
# any resulting height, deferring the raise to `ribbon.water_sign` over the
# whole taken set.  Its own declared key said so out loud:
# `BATTER_WATER_GATE_DEPENDS_ON = ("generated_height", "water_plane",
# "earthwork_direction")`, with a comment reading "`resulting_height` IS NOT
# ONE OF THEM, AND IT WAS TRIED".
#
# THE DIVISION OF LABOUR IT SET UP HAS AN UNREACHABLE REMEDY.  `water_sign`
# refuses and names the ROUTE as the fix, but the batter's extent is a
# function of how far the road stands above the ground beside it -- no
# re-route and no deck removes a toe.  So the permit handed the final gate a
# population nothing upstream could act on, and the refusal arrived after the
# whole rasterisation with no remedy attached.
#
# AND THE CLIFF THE PERMIT TRADED AGAINST NO LONGER EXISTS.  Its recorded
# justification is that clipping the raise "does not stop a causeway -- IT
# LEAVES THE CAUSEWAY'S WHOLE FLANK STANDING AS A CLIFF", measured when T1
# drained 206 BATTER samples AND 163 CARRIAGEWAY ones.  `ford_rule` and
# `WET_DEPENDS_ON` emptied the carriageway half between that measurement and
# this one.  MEASURED over all fourteen Fresh42 segments, footprint minimum
# from `network.transverse_window`: 158 stations stand with their LEVELLED
# FOOTPRINT below the 30.0 m plane and 0 OF 158 ARE UNBRIDGED.  A bridged
# station writes no carriageway and `ribbon.stamp` writes no batter where
# there is no road, so a causeway flank is not reachable from any plan this
# router emits.
#
# WHAT THE CLIP LEAVES INSTEAD, MEASURED offline through `ribbon.stamp` on
# the recorded Fresh42 zone patches, all fourteen segments:
#     seed-submerged samples raised to/above the plane   750 -> 0
#     carriageway samples (paved + shoulder)             UNCHANGED, 14 of 14
#     batter samples                                  45,106 -> 44,025
#     batter laid on submerged ground, still UNDER the plane  423 -> 201
#     `water_fill` clips                                       0 -> 530
#     walls recorded at those clips                                    419
#     of those 419, standing on SEED-SUBMERGED ground                  419
#     standing on land                                                   0
# Every wall the rule leaves is under the water surface: a SHORELINE, not a
# cliff a player walks off.  That is the difference from the experiment that
# was reverted, which cut a real causeway flank on dry-side ground.
#
# THE FIXTURE IS THREE (generated, delta) PAIRS AND IT IS FIXED IN EVERY ARM.
# No world, no patch, no rasterisation: a neuter may swap only the RULE, and
# the ground it is asked about is three numbers written here.
BATTER_WATER_CASE_BED_Y = 28.00          # 2.00 m under the plane
# THE FILL THAT CLEARS THE PLANE -- the taper that deletes the bay.  Lands at
# 31.00, a full metre above the plane, so the case cannot pass by rounding.
BATTER_WATER_CASE_DRAIN_DELTA_M = 3.00
# THE SURVIVOR -- a toe grading into a shoal that stays 0.50 m UNDER the
# plane.  It removes no water and must be LAID.
BATTER_WATER_CASE_SHOAL_DELTA_M = 1.50
# THE HAZARD A RE-ROUTE CANNOT UNDO -- the bank cut down to the waterline.
BATTER_WATER_CASE_CUT_DELTA_M = -1.00


def _batter_water_arms() -> dict:
    """Three arms over one bed, through the SHIPPING rule."""
    p = str(I.JUMPSTART / "roads")
    if p not in sys.path:
        sys.path.insert(0, p)
    import route as routemod                               # noqa: PLC0415
    bed = BATTER_WATER_CASE_BED_Y
    return {
        "drain_cause": routemod.batter_water_rule(
            bed, BATTER_WATER_CASE_DRAIN_DELTA_M),
        "shoal_cause": routemod.batter_water_rule(
            bed, BATTER_WATER_CASE_SHOAL_DELTA_M),
        "cut_cause": routemod.batter_water_rule(
            bed, BATTER_WATER_CASE_CUT_DELTA_M),
        "depends_on": routemod.BATTER_WATER_DEPENDS_ON,
        "bed_y": bed,
        "water_level": routemod.WATER_LEVEL,
        "drain_lands_y": bed + BATTER_WATER_CASE_DRAIN_DELTA_M,
        "shoal_lands_y": bed + BATTER_WATER_CASE_SHOAL_DELTA_M,
    }


def _batter_water_finding(headline: str):
    """THE VERDICT IS A CONJUNCTION OF ALL THREE ARMS: the fill that clears
    the plane must be REFUSED, the toe that stays under it must be LAID, and
    the cut to the waterline must still be refused as `water_edge`.  The
    shipped permit reddens the first; "no batter over water" reddens the
    second; losing the direction term reddens the third."""
    got = _batter_water_arms()
    refused = got["drain_cause"] == "water_fill"
    laid = got["shoal_cause"] is None
    cut = got["cut_cause"] == "water_edge"
    right = refused and laid and cut
    msg = (f"{headline}: a batter sample over a bed at {got['bed_y']:.2f} m, "
           f"{got['water_level'] - got['bed_y']:.2f} m under the "
           f"{got['water_level']:.1f} m plane -- filled to "
           f"{got['drain_lands_y']:.2f} m, ABOVE the plane, is "
           f"{got['drain_cause'] or 'PERMITTED'}; filled to "
           f"{got['shoal_lands_y']:.2f} m, still under it, is "
           f"{got['shoal_cause'] or 'laid'}; and cut to "
           f"{got['bed_y'] + BATTER_WATER_CASE_CUT_DELTA_M:.2f} m is "
           f"{got['cut_cause'] or 'PERMITTED'}")
    return Finding(
        section="roads", check="a_batter_may_not_lift_the_bed_out_of_the_water",
        ok=not right,
        measured=BATTER_WATER_CASE_BED_Y + BATTER_WATER_CASE_DRAIN_DELTA_M,
        limit=30.0,
        message=msg,
        numbers=dict(depends_on=got["depends_on"],
                     bed_y=got["bed_y"],
                     water_level_m=got["water_level"],
                     drain_delta_m=BATTER_WATER_CASE_DRAIN_DELTA_M,
                     drain_lands_y=got["drain_lands_y"],
                     drain_cause=got["drain_cause"],
                     shoal_delta_m=BATTER_WATER_CASE_SHOAL_DELTA_M,
                     shoal_lands_y=got["shoal_lands_y"],
                     shoal_cause=got["shoal_cause"],
                     cut_delta_m=BATTER_WATER_CASE_CUT_DELTA_M,
                     cut_cause=got["cut_cause"]),
        population="three batter samples over ONE submerged bed -- a fill "
                   "that clears the water plane, a fill that stays under it, "
                   "and a cut. The ground is identical in all three, so the "
                   "only thing that can separate them is the rule")


def _case_a_batter_may_not_fill_the_bay_to_make_a_taper():
    """THE ARM THE SHIPPED PERMIT KILLS.  A raise was allowed at ANY resulting
    height, so the taper filled the bay and `water_sign` refused the whole
    write at the end of the rasterisation, naming a remedy -- the route --
    that cannot move a batter toe.  MEASURED across all fourteen Fresh42
    segments: 750 seed-submerged samples raised to or above the plane, 0 of
    them carriageway."""
    return _batter_water_finding(
        "a batter may not lift the bed out of the water to make a taper")


def _case_a_toe_that_stays_UNDER_the_plane_is_still_laid():
    """THE SURVIVOR.  'No batter over water' satisfies the other arm for the
    wrong reason and reinstates the cliff the batter exists to remove.
    MEASURED that this population is not empty: with the rule landed, 201
    batter samples across Fresh42 are still written ON seed-submerged ground
    because they stay under the plane and remove no water."""
    return _batter_water_finding(
        "a toe grading into a shoal removes no water and is laid")


def _neuter_batter_water_permits_every_raise():
    """RESTORE THE SHIPPED RULE: `delta < 0 and generated < WATER_LEVEL`, and
    nothing about where the fill LANDS.  This is `resulting_height` dropped
    from the key, which is the exact state that shipped, and the bay comes
    back filled."""
    p = str(I.JUMPSTART / "roads")
    if p not in sys.path:
        sys.path.insert(0, p)
    import route as routemod                               # noqa: PLC0415
    real = routemod.batter_water_rule

    def shipped(generated, delta):
        if delta < 0.0 and generated < routemod.WATER_LEVEL:
            return "water_edge"
        return None

    routemod.batter_water_rule = shipped

    def undo():
        routemod.batter_water_rule = real
    return undo


def _neuter_batter_water_refuses_every_sample_over_water():
    """THE OPPOSITE OVER-CORRECTION: nothing may be written over submerged
    ground at all.  It satisfies the drain arm for the wrong reason, takes
    the 201 legitimate shoal samples with it and reinstates the cliff at the
    first submerged sample on every ray, so the survivor arm must redden.
    Rule only; the bed and the three deltas are unchanged."""
    p = str(I.JUMPSTART / "roads")
    if p not in sys.path:
        sys.path.insert(0, p)
    import route as routemod                               # noqa: PLC0415
    real = routemod.batter_water_rule

    def never(generated, delta):
        if generated < routemod.WATER_LEVEL:
            return "water_edge" if delta < 0.0 else "water_fill"
        return None

    routemod.batter_water_rule = never

    def undo():
        routemod.batter_water_rule = real
    return undo


# ---------------------------------------------------------------------------
# A ROUTE BESIDE ITSELF -- `route.SELF_ADJACENCY_DEPENDS_ON`
# ---------------------------------------------------------------------------
#
# THE STATE THAT SHIPPED, MEASURED 2026-09-24.  Fresh42's road step 4 held at
# 2 of 14 segments for three waves.  Five segments reached `ribbon.py`'s rim
# gate and were refused there -- T4 22 caused steps, T6 3, T7 7, T9 49, S13 1,
# worst wall 5.486 m -- and 60 of their 71 caused steps had their two samples
# claimed by feet 9-30 m apart ALONG THE POLYLINE: a switchback's two legs,
# beside each other.  Five earthwork reconciliations were tried at step 4 and
# every one made it worse, and the refusal key shipped with NO GROUND TERM:
# `|y1 - y2| - BATTER_GRADE * (run1 + run2)`.  A fact about where the line
# was routed and how it was profiled -- and nothing at plan time asked it.
#
# THE FIXTURES ARE THREE LINES AND ONE FLAT PLANE, built here: a U whose legs
# stand 4 m apart with both ends pinned 6 m apart (no profile joins them), the
# same U 30 m long with one end pinned 3 m under the ground (a landing joins
# it), and a U whose legs stand 30 m apart (nothing to join).  "Step 4" is
# the shipping `ribbon.stamp` over the plane and the shipping
# `junction.rim_transfer` over what it moves -- the rim gate's own pair.
SELF_ADJ_GROUND_Y = 56.0
SELF_ADJ_UNJOINABLE = ([(0.0, 0.0), (40.0, 0.0), (40.0, 4.0), (0.0, 4.0)],
                       50.0, 56.0)
SELF_ADJ_LANDING = ([(0.0, 0.0), (30.0, 0.0), (30.0, 4.0), (0.0, 4.0)],
                    53.0, None)
SELF_ADJ_SWITCHBACK = ([(0.0, 0.0), (40.0, 0.0), (40.0, 30.0), (0.0, 30.0)],
                       53.0, None)


def _sa_mods():
    for sub in ("roads", "terraform"):
        p = str(I.JUMPSTART / sub)
        if p not in sys.path:
            sys.path.insert(0, p)
    import ribbon                                          # noqa: PLC0415
    import route as routemod                               # noqa: PLC0415
    import junction                                        # noqa: PLC0415
    return ribbon, routemod, junction


def _sa_plan(fixture) -> dict:
    """The line through the ROUTER's own ladder: `route.fit_ladder`, the
    function `network.build_segment` calls, over the flat plane's cut/fill
    box with the fixture's pins."""
    import numpy as np                                     # noqa: PLC0415
    R, rt, _j = _sa_mods()
    nodes, pin0, pin1 = fixture
    pts, s = rt.resample(nodes, 2.0)
    s = np.asarray(s, dtype=np.float64)
    t = np.full(len(pts), SELF_ADJ_GROUND_Y)
    lo, hi = t - rt.CUT_FILL_MAX, t + rt.CUT_FILL_MAX
    if pin0 is not None:
        lo[0] = hi[0] = pin0
    if pin1 is not None:
        lo[-1] = hi[-1] = pin1
    ladder = [rt.GRADE["trunk"], rt.GRADE["spur"], rt.GRADE["mountain"],
              rt.GRADE_CEILING]
    fl = rt.fit_ladder(t, s, lo, hi, np.zeros(len(pts), dtype=bool), ladder,
                       pts=pts, width_m=6.0, grade=R.BATTER_GRADE,
                       batter_m=R.BATTER_MAX_M, lattice_m=R.tcdata.SCALE)
    design = rt.fit_profile(t, s, ladder[0], lo=lo, hi=hi)["y"]
    j = fl["joined"]
    return {"pts": pts, "design": design, "verdict": j["verdict"],
            "emitted": None if j["verdict"] == "REFUSED" else fl["y"],
            "unchanged": bool(np.array_equal(fl["y"], design)),
            "pairs_before": j["pairs_before"], "pairs_after": j["pairs_after"],
            "worst": j["worst_before"], "rung": fl["rung"],
            "stations_moved": j["stations_moved"]}


def _sa_step4(pts, y) -> dict:
    """The rim gate on what the plan emitted: `ribbon.stamp`, then
    `junction.rim_transfer` over the samples the write MOVES, exactly as
    `ribbon.main` assembles it (no live writes, so `now` is the plane)."""
    R, _rt, junction = _sa_mods()
    tc = R.tcdata

    def ground(_x, _z):
        return SELF_ADJ_GROUND_Y
    seg = {"id": "fixture", "nodes": [list(p) for p in pts],
           "profile_y": [float(v) for v in y],
           "is_bridge": [False] * len(pts), "width_m": 6.0, "length_m": 0.0}
    zones = R.segment_zones(seg["nodes"], seg["is_bridge"], 6.0,
                            batter_m=R.BATTER_MAX_M)
    patches = {f"z_{zx}_{zz}": R._flat_patch(zx, zz, ground)
               for zx, zz in zones}
    comps, _st = R.stamp(seg, patches, list(zones), R.PadKeepOut([], []))
    taken = {}
    for (zx, zz), comp in comps.items():
        cx0, cz0 = tc.zone_centre(zx, zz)
        for i in range(tc.SAMPLES):
            if not comp.modified_height[i]:
                continue
            gy, gx = divmod(i, tc.PITCH)
            wx, wz = tc.sample_world(cx0, cz0, gx, gy)
            sx, sz = int(round(wx)), int(round(wz))
            after = SELF_ADJ_GROUND_Y + R.delta_at(comps, float(sx), float(sz))
            if abs(after - SELF_ADJ_GROUND_Y) > R.appliedmod.AUTHOR_TOLERANCE_M:
                taken[(sx, sz)] = after

    def h_after(x, z):
        return SELF_ADJ_GROUND_Y + (R.delta_at(comps, float(x), float(z))
                                    if tc.zone_of(x, z) in comps else 0.0)
    return junction.rim_transfer(taken, ground, None, height_after=h_after)


def _sa_numbers(p: dict, rim: dict | None) -> dict:
    _R, rt, _j = _sa_mods()
    return dict(depends_on=rt.SELF_ADJACENCY_DEPENDS_ON,
                plan_verdict=p["verdict"], rung=p["rung"],
                lattice_edges_before=p["pairs_before"],
                lattice_edges_after=p["pairs_after"],
                stations_moved=p["stations_moved"],
                worst_before=p["worst"],
                step4_rim=(None if rim is None else rim["verdict"]),
                step4_caused=(None if rim is None else rim["caused"]),
                step4_worst_caused_m=(None if rim is None
                                      else rim["worst_caused_m"]))


def _case_an_unjoinable_switchback_is_refused_at_plan_time():
    """THE ARM THE SHIPPED ROUTER FAILS.  Legs 4 m apart, both ends pinned
    6 m apart: no profile joins them.  The router must REFUSE the line with
    its number, so step 4 is handed nothing -- and with the check dropped the
    line goes through and the rim gate refuses it after the rasterisation."""
    p = _sa_plan(SELF_ADJ_UNJOINABLE)
    rim = None if p["emitted"] is None else _sa_step4(p["pts"], p["emitted"])
    w = p["worst"] or {}
    numbered = (p["verdict"] == "REFUSED"
                and (w.get("unabsorbable_m") or 0.0) > 0.0
                and w.get("stations") and w.get("design_y")
                and w.get("allowance_m") is not None)
    ok = bool(numbered) if rim is None else (rim["caused"] == 0)
    return [_f("a_route_beside_itself_is_refused_before_step_4", ok,
               f"plan-time {p['verdict']}: {p['pairs_before']} lattice "
               f"edge(s) out of reach, worst {w.get('unabsorbable_m')} m "
               f"unabsorbable between stations {w.get('stations')} at design "
               f"heights {w.get('design_y')} (allowance "
               f"{w.get('allowance_m')} m); step 4 "
               + ("handed nothing" if rim is None else
                  f"rim {rim['verdict']}, {rim['caused']} caused, worst "
                  f"{rim['worst_caused_m']} m at {rim['worst_caused_at']}"),
               measured=(None if rim is None else rim["worst_caused_m"]),
               limit=0.0, numbers=_sa_numbers(p, rim),
               population="one U, legs 4 m apart, ends pinned at 50.0 and "
                          "56.0 m over a plane at 56.0 m")]


def _case_a_joinable_switchback_gets_its_landing():
    """WHERE A RE-PROFILE EXISTS THE ROUTER TAKES IT.  The same 4 m U, one
    end pinned 3 m under the ground: the design profile climbs 3 m through
    the turn and leaves 3 m across the legs; a landing -- the legs held
    within one lattice edge of slide of each other -- joins them inside the
    box, and step 4 then moves nothing a walker cannot take."""
    p = _sa_plan(SELF_ADJ_LANDING)
    rim = None if p["emitted"] is None else _sa_step4(p["pts"], p["emitted"])
    ok = (p["verdict"] == "JOINED" and rim is not None
          and rim["caused"] == 0)
    return [_f("a_joinable_switchback_is_re_profiled", ok,
               f"plan-time {p['verdict']} at rung {p['rung']}: "
               f"{p['pairs_before']} -> {p['pairs_after']} lattice edge(s) "
               f"out of reach, {p['stations_moved']} station(s) moved; step 4 "
               + ("handed nothing" if rim is None else
                  f"rim {rim['verdict']}, {rim['caused']} caused, worst "
                  f"{rim['worst_caused_m']} m at {rim['worst_caused_at']}"),
               measured=(None if rim is None else rim["worst_caused_m"]),
               limit=0.0, numbers=_sa_numbers(p, rim),
               population="one U, legs 4 m apart, start pinned at 53.0 m "
                          "over a plane at 56.0 m")]


def _case_an_absorbable_switchback_plans_unchanged():
    """THE SURVIVOR.  Legs 30 m apart: every claim is within the slide limit
    of every other, so the line must come back from the ladder CLEAR and
    byte-identical to `fit_profile`'s answer.  A key that refuses on geometry
    alone -- or one that forgets the lattice edge a walker may take --
    refuses this line, and this verdict reddens."""
    p = _sa_plan(SELF_ADJ_SWITCHBACK)
    ok = p["verdict"] == "CLEAR" and p["unchanged"]
    return [_f("an_absorbable_switchback_is_left_alone", ok,
               f"plan-time {p['verdict']}, profile "
               f"{'UNCHANGED' if p['unchanged'] else 'MOVED'}, "
               f"{p['pairs_before']} lattice edge(s) out of reach",
               measured=float(p["pairs_before"]), limit=0.0,
               numbers=_sa_numbers(p, None),
               population="one U, legs 30 m apart, start pinned at 53.0 m "
                          "over a plane at 56.0 m")]


def _sa_patch(name: str, fn):
    _R, rt, _j = _sa_mods()
    real = getattr(rt, name)
    setattr(rt, name, fn(rt, real))

    def undo():
        setattr(rt, name, real)
    return undo


def _neuter_self_adjacency_never_asked():
    """RESTORE THE SHIPPED ROUTER: nothing at plan time asks whether a line's
    own branches can be joined, so every line comes back CLEAR."""
    return _sa_patch("self_adjacency_violations",
                     lambda rt, real: (lambda y, cand: []))


def _neuter_self_adjacency_run_from_the_centreline():
    """ONE ARM LOSES ONE NAMED DEPENDENCY: `run_past_each_claims_bench_edge`
    measured from the CENTRELINE instead, which credits the grade with the
    level bench it cannot slope across -- a superset of the run."""
    return _sa_patch("claim_run_m",
                     lambda rt, real: (lambda lat, h: real(lat, 0.0)))


def _neuter_self_adjacency_blind_to_height():
    """THE OPPOSITE OVER-CORRECTION: `design_height_at_both_claiming_feet`
    dropped.  Every lattice edge between two claims is refused whatever the
    two roads' heights, so a switchback that is fine is refused too."""
    return _sa_patch("unabsorbable_m",
                     lambda rt, real: (lambda dy, run_sum, grade, lattice_m:
                                       real(grade * (run_sum + 2.0 * lattice_m),
                                            run_sum, grade, lattice_m)))


def _neuter_self_adjacency_no_lattice_edge():
    """`one_lattice_edge_of_slide` dropped: two claims one sample apart must
    stand at EXACTLY one height, so a graded road's own paved samples are
    refused against each other."""
    return _sa_patch("unabsorbable_m",
                     lambda rt, real: (lambda dy, run_sum, grade, lattice_m:
                                       real(dy, run_sum, grade, 0.0)))



# ---------------------------------------------------------------------------
# THE ROAD CENSUS'S REACHABILITY, WHICH IS A PROPERTY OF THE GATE'S ENTRY AND
# NOT OF ANY SECTION -- and it is checked HERE because `roads` is the section
# whose own instrument is the whole-save ZDO census, and because the artefact
# the deadlock withheld is `roads/census/<World>-census.json`.
#
# `accept/__main__.no_build_disposition` is the predicate.  The two arms need
# OPPOSITE neuters, per the rule the harbours roof cases established: one arm
# falls to the shipped defect, the other to the degenerate over-correction,
# and the fact the neuters cannot touch is `ledger_exists`, which is passed
# in by the case.
def _accept_main():
    import importlib                                       # noqa: PLC0415
    return importlib.import_module("accept.__main__")


def _case_an_unbuilt_world_can_still_be_MEASURED():
    """A census request on a world with no build ledger is ANSWERED."""
    M = _accept_main()
    asked = SimpleNamespace(zdo_census=True)
    got = M.no_build_disposition(asked, ledger_exists=False)
    return [Finding(
        section="roads", check="save_census_reachable_without_a_build",
        ok=(got == "MEASURE"),
        message=(f"a whole-save ZDO census asked for on a world with NO "
                 f"build ledger was dispositioned {got!r}; the census "
                 f"depends on the save alone and stage 12 requires it, so "
                 f"anything but MEASURE is the deadlock"),
        numbers=dict(disposition=got, ledger_exists=False,
                     wants_a_verdict=M.wants_a_verdict(asked)))]


def _case_an_unbuilt_world_still_gets_NO_VERDICT():
    """A VERDICT request on a world with no build ledger is still REFUSED."""
    M = _accept_main()
    asked = SimpleNamespace(zdo_census=False)
    got = M.no_build_disposition(asked, ledger_exists=False)
    return [Finding(
        section="roads", check="an_unbuilt_world_is_not_an_accepted_world",
        ok=(got != "NO_BUILD"),
        message=(f"a VERDICT asked for on a world with NO build ledger was "
                 f"dispositioned {got!r}: an empty world is not an accepted "
                 f"world and must not reach the sections"),
        numbers=dict(disposition=got, ledger_exists=False,
                     wants_a_verdict=M.wants_a_verdict(asked)))]


def _neuter_everything_is_a_verdict():
    """THE SHIPPED DEFECT: the no-build exit keyed on the LEDGER ALONE, so
    every invocation -- including one that only wants to read the save -- is
    treated as a request for a verdict on the build."""
    M = _accept_main()
    real = M.wants_a_verdict
    M.wants_a_verdict = lambda _args: True

    def undo():
        M.wants_a_verdict = real
    return undo


def _neuter_nothing_is_a_verdict():
    """THE DEGENERATE OVER-CORRECTION: let everything past the no-build exit
    because SOMETHING about this world is measurable.  Reads as 'the census
    is reachable now' and quietly hands an unbuilt world to the sections."""
    M = _accept_main()
    real = M.wants_a_verdict
    M.wants_a_verdict = lambda _args: False

    def undo():
        M.wants_a_verdict = real
    return undo


# ---------------------------------------------------------------------------
# THE PRECONDITION STAGE 10 NEVER DECLARED: A CONSOLE THAT CAN RUN THE VERBS.
#
# Every Upgrade World verb the road pipeline emits -- `zones_generate`,
# `objects_count`, `stop`, `start` -- is registered with `IsCheat = true`
# (MEASURED by monodis on the deployed UpgradeWorld.dll 1.82:
# `Helper::Command` passes isCheat=1 at IL_0008 on BOTH the ConsoleEvent and
# the ConsoleEventFailable overload, i.e. for every command it registers),
# and `Terminal.m_cheat` is FALSE in a freshly started server process.
#
# MEASURED 2026-09-23 on the fresh seed Fresh42, stage 10, first zone: the
# RCON echo came back "Command 'zones_generate pos=-704,-384 max=32'
# executed." -- a SUCCESSFUL REPLY -- while the game printed "'zones_generate'
# is not valid in the current context."  A write session was reporting
# success for writes the game had refused.  Ulfsland only ever worked because
# somebody had typed `devcommands` into that server's console long ago; it
# had never been a declared prerequisite of anything.
#
# THE OPPOSITE DEFECT IS THE REASON THIS IS A PROBE AND NOT A SEND.
# `devcommands` is a TOGGLE, so a session that sends it at open turns cheats
# OFF on exactly the servers that already had them on -- which is every
# server this toolchain has ever built with.  One neuter per direction.
def _fake_console_server(replies):
    """A `replay.Server` whose console answers a scripted sequence."""
    import importlib                                       # noqa: PLC0415
    R = importlib.import_module("replay")
    srv = R.Server.__new__(R.Server)
    srv.dry = False
    srv.rc = None
    srv.sent = []
    queue = [list(r) for r in replies]

    def console(cmd, settle=2.0):
        srv.sent.append(cmd)
        return queue.pop(0) if queue else []
    srv.console = console
    return srv


_CHEATS_OFF = ["'objects_count' is not valid in the current context."]
_CHEATS_ON = ["Total: 1", "_ZoneCtrl: 1"]


def _case_a_console_that_cannot_run_the_verbs_is_REFUSED():
    """Cheats off and `devcommands` does not turn them on -> no session."""
    import importlib                                       # noqa: PLC0415
    R = importlib.import_module("replay")
    srv = _fake_console_server([_CHEATS_OFF, ["Dev commands: False"],
                                _CHEATS_OFF])
    try:
        got = R.Server.require_cheats(srv)
        refused, detail = False, repr(got)
    except Exception as exc:                               # noqa: BLE001
        refused, detail = type(exc).__name__ == "LedgerError", str(exc)[:120]
    return [Finding(
        section="roads", check="write_session_needs_a_console_that_obeys",
        ok=not refused,
        message=(f"a console whose cheat commands are refused was "
                 f"{'REFUSED' if refused else 'ACCEPTED'} as a write "
                 f"session; every zones_generate would return a successful "
                 f"RCON echo for a command the game rejects ({detail})"),
        numbers=dict(refused=refused, probes_sent=len(srv.sent),
                     sent=srv.sent))]


def _case_a_console_that_ALREADY_OBEYS_IS_NOT_TOGGLED():
    """Cheats already on -> open the session and send NO `devcommands`."""
    import importlib                                       # noqa: PLC0415
    R = importlib.import_module("replay")
    srv = _fake_console_server([_CHEATS_ON])
    got = R.Server.require_cheats(srv)
    toggled = "devcommands" in srv.sent
    return [Finding(
        section="roads", check="devcommands_is_a_toggle_not_a_setting",
        ok=not toggled and not got.get("toggled"),
        message=(f"a server whose console ALREADY obeys was sent "
                 f"{srv.sent!r}: sending `devcommands` here turns cheats "
                 f"OFF, so the next write would be silently refused by the "
                 f"game while its RCON echo still reported success"),
        numbers=dict(sent=srv.sent, toggled=toggled,
                     reported=got.get("cheats")))]


def _neuter_no_cheat_precondition():
    """THE SHIPPED DEFECT: open the write session without asking the console
    whether it can run the verbs the session is about to emit."""
    import importlib                                       # noqa: PLC0415
    R = importlib.import_module("replay")
    real = R.Server.require_cheats
    R.Server.require_cheats = lambda self: {"cheats": "not checked",
                                            "toggled": False}

    def undo():
        R.Server.require_cheats = real
    return undo


def _neuter_devcommands_sent_blind():
    """THE DEGENERATE OVER-CORRECTION: send `devcommands` at every session
    open.  Fixes the fresh server and breaks every server that was fine."""
    import importlib                                       # noqa: PLC0415
    R = importlib.import_module("replay")
    real = R.Server.require_cheats

    def blind(self):
        said = self.console("devcommands")
        return {"cheats": "sent blind", "toggled": True,
                "devcommands_said": said[:2]}
    R.Server.require_cheats = blind

    def undo():
        R.Server.require_cheats = real
    return undo


# ---------------------------------------------------------------------------
# THE OTHER HALF OF THE SAME PRECONDITION, AND THE ELEVENTH INSTANCE OF THE
# KEY CLASS: `require_cheats` KEYED A SESSION'S WRITABILITY ON A SUBSET.
#
# `Terminal.TryRunCommand` refuses in TWO ways while `RconProxy` still returns
# `Command '<cmd>' executed.`:
#     registered + IsCheat + m_cheat false -> "is not valid in the current
#                                              context"      <- require_cheats
#     NOT REGISTERED (plugin absent)       -> "is not a recognized command"
#                                                             <- NOTHING
#
# MEASURED 2026-09-23 driving stage 12 on the fresh seed Fresh42:
# `require_cheats()` returned `enabled by this session`, and
# `terraform/flatten.py apply` then reported **rc 0 on 13 of 13 pads** while
# every one of its 33 `spawn_object _TerrainCompiler` commands came back
# `'spawn_object' is not a recognized command.`  Zero cubic metres moved and
# the build walked on to step 3.  `spawn_object` and `deleteObjects` are
# registered by `World_Edit_Commands`; Ulfsland's server carries it among 100
# plugins a human installed, a freshly provisioned server carries three.
#
# ONE NEUTER PER DIRECTION.  OFF: no verb precondition, which is the shipped
# state and which only a must-REFUSE case can flip under.  WRONG: treat ANY
# console output as a refusal, which refuses every healthy server -- the
# must-ACCEPT case is what flips under that.
_VERB_OK = ["Usage: spawn_object <prefab> ..."]
# BOTH WORDINGS, BECAUSE BOTH WERE MEASURED ON ONE SERVER TWENTY SECONDS
# APART: vanilla `Terminal.TryRunCommand` says the first, the Terminal that
# `Server_devcommands` installs over it says the second. A checker that knew
# only one would pass every unregistered verb on every server carrying the
# mod that the rest of this toolchain requires -- a key naming a SUBSET of
# the ways the answer is spelled.
_VERB_MISSING = {
    "vanilla Terminal": ["'spawn_object' is not a recognized command. "
                         "Type 'help' to see a list of valid commands."],
    "Server_devcommands Terminal": ["Unknown command 'spawn_object'. "
                                    "Type 'help' to see a list of valid "
                                    "commands"],
}


def _case_a_console_that_DOES_NOT_KNOW_THE_VERB_is_REFUSED():
    """An unregistered verb is a write that cannot land -> no session.

    Asked once PER WORDING: one arm passing is not the check passing.
    """
    import importlib                                       # noqa: PLC0415
    R = importlib.import_module("replay")
    out = []
    for terminal, reply in _VERB_MISSING.items():
        srv = _fake_console_server([reply])
        try:
            got = R.Server.require_verbs(srv, ["spawn_object"])
            refused, detail = False, repr(got)
        except Exception as exc:                           # noqa: BLE001
            refused = type(exc).__name__ == "LedgerError"
            # 400, not 160: `names_the_plugin` below is read off THIS string,
            # and a slice that cuts before the plugin name measures the slice
            # rather than the refusal.
            detail = str(exc)[:400]
        out.append(Finding(
            section="roads", check="write_session_needs_the_verbs_it_emits",
            ok=not refused,
            message=(f"a {terminal} with no `spawn_object` was "
                     f"{'REFUSED' if refused else 'ACCEPTED'} as a write "
                     f"session; MEASURED on Fresh42 this state returned rc 0 "
                     f"for 13 of 13 pads and wrote no terrain at all "
                     f"({detail})"),
            numbers=dict(terminal=terminal, refused=refused,
                         names_the_plugin=("World_Edit_Commands" in detail),
                         sent=srv.sent)))
    return out


def _case_a_console_that_KNOWS_THE_VERB_is_ACCEPTED():
    """A registered verb answers its own usage; that is not a refusal."""
    import importlib                                       # noqa: PLC0415
    R = importlib.import_module("replay")
    srv = _fake_console_server([_VERB_OK])
    try:
        got = R.Server.require_verbs(srv, ["spawn_object"])
        refused, detail = False, repr(got)
    except Exception as exc:                               # noqa: BLE001
        refused, detail = True, str(exc)[:160]
    return [Finding(
        section="roads", check="a_verb_that_answers_is_not_a_missing_verb",
        ok=not refused,
        message=(f"a server that DOES register `spawn_object` was "
                 f"{'REFUSED' if refused else 'accepted'}: a usage line is "
                 f"the registered verb answering, and refusing it would shut "
                 f"every working server -- Ulfsland included -- out of its "
                 f"own ledger ({detail})"),
        numbers=dict(refused=refused, sent=srv.sent))]


def _neuter_no_verb_precondition():
    """THE SHIPPED DEFECT: open the session having checked only cheats, so an
    unregistered verb is discovered by the world not changing."""
    import importlib                                       # noqa: PLC0415
    R = importlib.import_module("replay")
    real = R.Server.require_verbs
    R.Server.require_verbs = lambda self, verbs=None: {"verbs": "not checked",
                                                       "missing": []}

    def undo():
        R.Server.require_verbs = real
    return undo


def _neuter_any_output_is_a_missing_verb():
    """THE DEGENERATE OVER-CORRECTION: call a verb missing whenever the
    console says anything.  Refuses every server that works."""
    import importlib                                       # noqa: PLC0415
    R = importlib.import_module("replay")
    real = R.Server.require_verbs

    def paranoid(self, verbs=None):
        from replay import LedgerError                     # noqa: PLC0415
        want = tuple(verbs) if verbs is not None else self.VERB_PROBES
        for verb in want:
            if self.console(verb):
                raise LedgerError(f"this server does not know [{verb!r}]")
        return {"verbs": {}, "missing": []}
    R.Server.require_verbs = paranoid

    def undo():
        R.Server.require_verbs = real
    return undo


# ---------------------------------------------------------------------------
# UNREAD IS NOT CLEAR, AT THE ONE GATE THAT WAS SAYING OTHERWISE.
#
# `precensus.band_gate` was keyed on the widest body the census FOUND and on
# the band SURVEYED, and not on WHETHER THE BAND WAS READ.  A census that
# read nothing has `widest_hard_m == 0.00`, so it answered ROOM.
#
# MEASURED 2026-09-23 on the fresh seed Fresh42, `precensus.py from_save`
# over the frozen unbuilt save: W9-vestvik-lhwest came back "0 bodies, 0 of
# 460 band cells censused, 460 in an UNGENERATED zone" and the gate said
# `{'verdict': 'ROOM', 'widest_hard_m': 0.0, 'band_half_needed_m': 12.31}`.
# The census's own instrument line already said an ungenerated zone's
# emptiness "is an absence of reading, not a clear corridor"; the gate beside
# it contradicted that in one word.
def _precensus():
    import importlib                                       # noqa: PLC0415
    return importlib.import_module("precensus")


def _unread_census(P):
    """460 band cells wanted, none read, no bodies -- Fresh42's W9."""
    return P.Census(world="Fresh42", seed="Fresh42",
                    measured_utc="2026-09-23T17:53:14Z",
                    instrument="fixture: a save whose corridor zones have "
                               "never been generated",
                    band_half_m=16.0, cells=[], bodies=[], zones=[],
                    stats={"band_cells_wanted": 460,
                           "band_cells_censused": 0,
                           "band_cells_in_an_ungenerated_zone": 460})


def _read_census_with_a_wide_body(P):
    """Every wanted cell read, and one body too wide for the band."""
    body = P.Body(name="fixture-hall", x=8.0, z=8.0, y=30.0,
                  declared_m=40.0, pieces=120, blocking=120,
                  prefabs={"wood_wall": 120}, source="fixture",
                  measured_utc="2026-09-23T17:53:14Z")
    return P.Census(world="Fresh42", seed="Fresh42",
                    measured_utc="2026-09-23T17:53:14Z",
                    instrument="fixture: a fully read band with one wide body",
                    band_half_m=16.0, cells=[(0, 0), (0, 1)], bodies=[body],
                    zones=[(0, 0)],
                    stats={"band_cells_wanted": 2, "band_cells_censused": 2,
                           "band_cells_in_an_ungenerated_zone": 0})


def _case_an_unread_band_is_not_a_clear_one():
    P = _precensus()
    g = P.band_gate(_unread_census(P), 3.0)
    return [Finding(
        section="roads", check="unread_is_not_clear",
        ok=(g["verdict"] == "UNCENSUSED"),
        message=(f"a census that read {g.get('band_cells_read')} of "
                 f"{g.get('band_cells_wanted')} band cells was answered "
                 f"{g['verdict']!r} at widest_hard_m "
                 f"{g['widest_hard_m']}: a body nobody looked for is not a "
                 f"body that is not there, and ROOM here sends the router "
                 f"through ground no instrument has seen"),
        numbers=dict(verdict=g["verdict"], widest_hard_m=g["widest_hard_m"],
                     band_cells_read=g.get("band_cells_read"),
                     band_cells_wanted=g.get("band_cells_wanted")))]


def _case_a_READ_band_still_gets_its_width_verdict():
    P = _precensus()
    g = P.band_gate(_read_census_with_a_wide_body(P), 3.0)
    return [Finding(
        section="roads", check="a_read_band_is_still_measured",
        ok=(g["verdict"] == "BAND_TOO_NARROW"),
        message=(f"a FULLY read band holding a {g['widest_hard_m']} m "
                 f"keep-out inside a {g['band_half_m']} m half-band was "
                 f"answered {g['verdict']!r}; the coverage test must not "
                 f"swallow the width verdict, or every census reads "
                 f"UNCENSUSED and the re-survey width is never printed"),
        numbers=dict(verdict=g["verdict"], widest_hard_m=g["widest_hard_m"],
                     band_half_needed_m=g["band_half_needed_m"],
                     resurvey_band_half_m=g.get("resurvey_band_half_m")))]


def _neuter_band_gate_ignores_coverage():
    """THE SHIPPED DEFECT: judge the band on the widest body FOUND alone, so
    a census that found nothing because it read nothing answers ROOM."""
    P = _precensus()
    real = P.band_gate

    def blind(census, half_width_m):
        g = real(census, half_width_m)
        if g["verdict"] == "UNCENSUSED":
            g["verdict"] = ("ROOM" if g["band_half_needed_m"]
                            <= g["band_half_m"] else "BAND_TOO_NARROW")
        return g
    P.band_gate = blind

    def undo():
        P.band_gate = real
    return undo


def _neuter_band_gate_refuses_every_census():
    """THE DEGENERATE OVER-CORRECTION: call every census uncensused.  Looks
    like the fix, and buries the width verdict and its re-survey number."""
    P = _precensus()
    real = P.band_gate

    def always(census, half_width_m):
        g = real(census, half_width_m)
        g["verdict"] = "UNCENSUSED"
        return g
    P.band_gate = always

    def undo():
        P.band_gate = real
    return undo


# ---------------------------------------------------------------------------
# AN EMPTY POPULATION IS NOT A FAILED READ, AT THE HARNESS'S OWN GATE.
#
# `run_harness` refused any secondary artefact with zero rows.  MEASURED
# 2026-09-23 by FreshDrive on the fresh seed Fresh42, whose save holds 81
# ZDOs and no portals and no guideposts: the ZDO FIELD census is correctly
# EMPTY, and the harness raised "harness wrote zdostr.tsv with ZERO rows", so
# the portals section reported a broken instrument on a world whose true
# answer is "there are none".
#
# The distinguishing fact is on disk and always was: the harness appends
# `zdo_census <path> <n>` (WaterProof.cs:220) and `zdo_fields <path> <rows>`
# (WaterProof.cs:942) to its meta file AFTER writing each artefact.  A pass
# that ran says so; a pass that did not run says nothing.  The fixtures below
# build both states as files in a tempdir and never touch a save, a server or
# a lock -- `run_harness` is monkeypatched at its subprocess boundary so the
# ONLY thing under test is the zero-row decision.
def _harness_zero_row_verdict(meta_lines, extra_rows):
    """(did it refuse, detail) for one artefact state, with no harness run."""
    import importlib, tempfile                              # noqa: PLC0415, E401
    from pathlib import Path as _P                          # noqa: PLC0415
    I = importlib.import_module("accept.instruments")
    tmp = _P(tempfile.mkdtemp(prefix="roads_zero_row_"))
    extra = tmp / "zdostr.tsv"
    extra.write_text("".join(f"zdofield\trow{i}\n" for i in range(extra_rows)))
    meta = tmp / "meta.tsv"
    real_run, real_lock = I.subprocess.run, I.harness_lock

    class _Lock:
        def __enter__(self):
            return 3459

        def __exit__(self, *_exc):
            return False

    # THE META IS WRITTEN BY THE PATCHED RUN, NEVER BEFORE IT.  `run_harness`
    # returns early when its artefact already exists with rows ("produced by
    # another run while we queued"), so a pre-written meta skips every check
    # this fixture exists to exercise and the case would pass vacuously.
    def fake_run(*_a, **_k):
        meta.write_text("".join(ln.format(extra=extra) + "\n"
                                for ln in meta_lines))
        return SimpleNamespace(stderr="", stdout="", returncode=0)
    I.harness_lock = lambda _note: _Lock()
    I.subprocess.run = fake_run
    try:
        I.run_harness({"ZDO_FIELDS": str(extra)}, meta, "vh", "ws", "W", "sb")
        return False, "accepted"
    except RuntimeError as exc:
        return True, str(exc)[:160]
    finally:
        I.subprocess.run, I.harness_lock = real_run, real_lock


_PASS_RAN = ["water_level_engine\tx\t30", "zdo_fields\t{extra}\t0"]
_PASS_ABSENT = ["water_level_engine\tx\t30"]


def _case_a_world_with_no_portals_is_not_a_broken_instrument():
    refused, detail = _harness_zero_row_verdict(_PASS_RAN, 0)
    return [Finding(
        section="roads", check="an_empty_population_is_not_a_failed_read",
        ok=not refused,
        message=(f"a field census the harness REPORTED running and which "
                 f"found 0 rows was {'REFUSED' if refused else 'accepted'}: "
                 f"Fresh42 holds 81 ZDOs and no portals, so zero is the true "
                 f"answer and refusing it hands the portals section a broken "
                 f"instrument instead of an empty world ({detail})"),
        numbers=dict(refused=refused, rows=0, pass_reported=True))]


def _case_a_pass_that_never_RAN_is_still_a_failed_read():
    refused, detail = _harness_zero_row_verdict(_PASS_ABSENT, 0)
    return [Finding(
        section="roads", check="a_pass_that_never_ran_is_a_failed_read",
        ok=refused,
        message=(f"a zero-row artefact the harness NEVER reported writing "
                 f"was {'refused' if refused else 'ACCEPTED'}: with no "
                 f"`zdo_fields` line the reading did not happen, and an "
                 f"instrument that cannot tell 'nothing to see' from 'I "
                 f"never looked' is the fault this check exists for "
                 f"({detail})"),
        numbers=dict(refused=refused, rows=0, pass_reported=False))]


def _neuter_every_zero_row_artefact_is_a_fault():
    """THE SHIPPED DEFECT: refuse any secondary artefact with zero rows,
    whether or not the harness reported taking the reading."""
    import importlib                                        # noqa: PLC0415
    from pathlib import Path as _P                          # noqa: PLC0415
    I = importlib.import_module("accept.instruments")
    real = I.run_harness

    def blind(env_extra, out, *a, **k):
        real(env_extra, out, *a, **k)
        for extra in (env_extra.get("ZDOS"), env_extra.get("ZDO_FIELDS")):
            if extra and _P(extra).exists() and I._rows_in(_P(extra)) == 0:
                raise RuntimeError(f"harness wrote {extra} with ZERO rows")
        return ""
    I.run_harness = blind

    def undo():
        I.run_harness = real
    return undo


def _neuter_every_zero_row_artefact_is_fine():
    """THE DEGENERATE OVER-CORRECTION: accept every zero-row artefact,
    because 'an empty population is not a failed read' -- which also accepts
    the port that was never bound and the pass that never ran."""
    import importlib                                        # noqa: PLC0415
    I = importlib.import_module("accept.instruments")
    real = I.run_harness

    def permissive(env_extra, out, *a, **k):
        try:
            return real(env_extra, out, *a, **k)
        except RuntimeError:
            return ""
    I.run_harness = permissive

    def undo():
        I.run_harness = real
    return undo


CASES = [
    Case("8 m baseline would PASS a 6 m cliff", False,
         _case_eight_metre_baseline,
         "the reported 'worst 8 m gradient 0.0801' over an 8.93 m step"),
    Case("1 m baseline REFUSES the same cliff", True,
         _case_one_metre_baseline,
         "84 impassable locations on the built roads"),
    Case("1 m baseline ACCEPTS a step just under the slide angle", False,
         _case_just_under_limit),
    Case("a deck over a 3 m gap is ACCEPTED, with no bridge named", False,
         _case_deck_spans_gap,
         "nine of our own bridge decks were once the biggest 'blockers'"),
    Case("the same gap with no deck over it is REFUSED", True,
         _case_hole_with_no_deck),
    Case("a DECLARED crossing whose deck carries a walker is ACCEPTED",
         False, _case_crossing_with_a_deck_is_accepted,
         "crossing S2 joins S1's (-316,230) to N0's (-350,246) across "
         "37.6 m with 60 wood_floor at y 30.5; both roads declare deck "
         "30.600"),
    Case("a DECLARED crossing with a hole in its deck is REFUSED", True,
         _case_crossing_with_a_hole_is_refused),
    Case("a DECLARED crossing with NO deck at all is REFUSED", True,
         _case_crossing_with_no_deck_is_refused,
         "the state this section shipped with: a declared crossing "
         "contributed no node, so a missing bridge and a perfect one "
         "scored the same"),
    Case("a DETECTED crossing on a BUILT segment is IN THE POPULATION",
         True, _case_a_detected_crossing_is_in_the_population,
         "crossing_spans read crossings.yaml:declared alone and judged 2 "
         "of 16; the other 14 are under `detected` in the same file, six "
         "of them on BUILT segments carrying 347.5 m of declared deck with "
         "no bridge_body_place record - T4 x01/x02, T5 x01, W5 x01, "
         "W10 x01, S12 x01. S12-brgs1-boathouse built 51.2 m of a planned "
         "144.9 m because 46 of its 74 stations are is_bridge",
         neuter=_neuter_declared_only,
         neuters="the old reader: crossings.yaml:declared and nothing else"),
    Case("the same channel declared TWICE resolves to Crossings' own row",
         True, _case_declared_supersedes_detected_for_one_channel,
         "crossings.yaml:precedence - 'Where they describe the same "
         "channel, Crossings' numbers win and mine are marked superseded'"),
    # --- THE CORPUS IS ABOUT THIS RUN'S WORLD, OR THERE IS NO VERDICT -----
    Case("a corpus declaring ANOTHER WORLD is REFUSED, not read",
         True, _case_a_corpus_about_another_world_is_refused,
         "MEASURED 2026-09-23 across all eleven sections: roads (twice), "
         "annulus and townsites opened roads/crossings.yaml or "
         "settlements/sites.yaml - both declaring `world: Ulfsland / seed: "
         "Pirate68` in their own headers - at a FIXED repository path with "
         "no scope check at all, so `--world Fresh42` judged Ulfsland",
         neuter=_neuter_scope_ignores_the_world,
         neuters="the world comparison dropped, seed left intact: the "
                 "guard a reader gets by copying require_scope's "
                 "seed-scoped tolerance onto a world-scoped corpus"),
    Case("a corpus DECLARING NOTHING is REFUSED - unread is not clear",
         True, _case_a_corpus_declaring_nothing_is_refused,
         "every artefact in this tree that now carries a seed/world header "
         "gained it late, and 12,301 Pirate68 locations plus 16 Pirate68 "
         "sites were accepted by a Fresh42 run while the declaration was "
         "merely unread",
         neuter=_neuter_scope_tolerates_an_absent_declaration,
         neuters="an absent declaration read as permission"),
    Case("an UNREADABLE corpus is a REFUSAL, not an empty population",
         True, _case_an_unreadable_corpus_is_refused,
         "accept/sections/roads.py:328-336 as it shipped: the exception was "
         "logged and `[]` returned, so a corrupt corpus scored identically "
         "to a world with no crossings in it",
         neuter=_neuter_the_corpus_read_never_fails,
         neuters="an unreadable corpus back to an empty document and a log "
                 "line"),
    Case("a corpus about THIS world is ACCEPTED WHOLE - the survivor",
         False, _case_a_corpus_about_this_world_is_accepted,
         "the direction a refuse-everything guard passes: three refusing "
         "cases above are all satisfied by a guard that rejects every "
         "corpus, and such a guard blocks every world that is fine",
         neuter=_neuter_scope_refuses_everything,
         neuters="the degenerate guard: every corpus is foreign"),
    Case("a FRESH SEED'S OWN crossings corpus is the one read, not the "
         "default seed's", False,
         _case_a_fresh_seeds_own_crossings_corpus_is_the_one_read,
         "MEASURED on the Fresh42 drive-2 gate (save a521b70356e7): every "
         "read opened roads/crossings.yaml (Ulfsland / Pirate68) and refused "
         "it, while roads/crossings.Fresh42.yaml - 9 detected crossings, "
         "written through worldseed.crossings_path by roads/network.py::emit "
         "- was never read; S4-meadhall-southeast-x01's declared 26 m deck "
         "span was walked as road ground and refused 4 times ACTIONABLE "
         "(1.138 m worst) plus a connectivity row, each prescribing a terrain "
         "cut in a water crossing",
         neuter=_neuter_crossings_corpus_default_seed_only,
         neuters="every crossings read back on the fixed default-seed name"),
    Case("every DECLARED corpus-scope dependency still changes the answer",
         False, _case_every_declared_scope_dependency_is_load_bearing,
         "MEASURED 2026-09-23 on this harness's own machinery: two "
         "provenance cases shared a neuter that switched their classifier "
         "off entirely, 0 of 4 dependencies survived it, the key became a "
         "CONSTANT, and both were stamped PROVEN LOAD-BEARING by an index "
         "that named nothing",
         neuter=_neuter_scope_ignores_the_world,
         neuters="the world comparison dropped: 2 of the 4 declared "
                 "dependencies stop changing the guard's answer"),
    Case("a crossing whose bed stands ABOVE its own deck IS NOT A CROSSING",
         True, _case_a_dry_ridge_is_not_a_crossing,
         "S12-brgs1-boathouse-x01: 2 of 82 metres under the water plane, "
         "32 of 82 with the bed ABOVE its declared deck 33.870, worst "
         "+2.926 m - while its record claims gap_min_y 25.24 and "
         "max_depth_below_plane_m 4.76. Engine bed at 1 m from save "
         "9a242873. The ribbon skipped 46 stations as is_bridge and the "
         "deck builder refused all 82 as buried: neither owner's gate "
         "could see the road that went missing between them",
         neuter=_neuter_premise_never_asked,
         neuters="the old check: whether a deck carries a walker, never "
                 "whether there is anything under it to carry him over"),
    Case("a REAL channel keeps its deck - the premise check cuts both ways",
         False, _case_a_real_channel_keeps_its_deck,
         "W5-brgs1-wsouth-x01 is 39 of 90 metres wet and is a genuine "
         "89.44 m crossing; a wet-metre threshold would have taken its "
         "deck away"),
    Case("a premise with no deck datum or no bed read is UNMEASURED",
         False, _case_an_unjudgeable_premise_says_so),
    Case("a crossing whose PREMISE was refused is NOT ALSO judged for "
         "carriage", False,
         _case_a_refused_premise_is_not_also_judged_for_carriage,
         "gate run C, save a087af6253a0: T7-stathub-stenvik-x01 carried TWO "
         "refusals at once - 'IS NOT A CROSSING: its own bed stands above "
         "its own declared deck y=32.519 on 1 of 51 metres ... these "
         "stations belong to Ribbon, not to a deck' AND 'does not carry a "
         "walker: 44 breaks over a 49.7 m span, 6 of 51 metres stand on a "
         "piece'. The gate ruled no deck may stand there and failed the "
         "deck for not carrying. It was the single NEW refusal in C "
         "(crossings 14 -> 15) and it surfaced only because pieces landed "
         "on it, so REPAIRING THE PREMISE-REFUSED SET COULD ONLY EVER "
         "INCREASE THE REFUSAL COUNT",
         neuter=_neuter_carriage_independent_of_premise,
         neuters="the old predicate: the carriage arm fires independently "
                 "and is suppressed only when NOTHING stands on the span"),
    Case("a REAL crossing with a hole in its deck is STILL judged", True,
         _case_a_real_crossing_with_a_hole_is_still_judged,
         "the other direction of the same rule: the premise suppression "
         "must not become a blanket, or a bridge with a hole scores "
         "identically with a perfect one - the exact defect the crossings "
         "check was added to close",
         neuter=_neuter_carriage_never_judged,
         neuters="the over-correction: suppress the carriage arm on every "
                 "row"),
    Case("ONE PHYSICAL CHANNEL DECLARED TWICE is REFUSED, with its "
         "population", True, _case_one_channel_declared_twice_is_refused,
         "roads/crossings.yaml: 17 declarations describe 13 distinct "
         "channels - W10-x01=W5-x01 (1.000), W7-x01=W9-x01 (1.000), "
         "T4-x01=T7-x02 (0.761), T4-x02=T7-x01 (0.659) - because "
         "roads/network.py builds `detected` as one record PER SEGMENT. "
         "The headline '3 of 17 declared crossings carry a walker' stood "
         "in runs A, B and C, and TWO of those three passes were ONE built "
         "deck: the pass count was inflated by redundant records rather "
         "than by built deck",
         neuter=_neuter_corpus_taken_at_face_value,
         neuters="the old corpus: every declaration is its own channel and "
                 "nothing ever compared two of them"),
    Case("two DIFFERENT channels leaving banks 2.0 m apart are NOT "
         "collapsed", False,
         _case_two_channels_sharing_a_bank_are_not_collapsed,
         "S1 against S12-brgs1-boathouse-x01: banks 2.0 m apart, sharing 14 "
         "of S12's 17 built pieces (0.824 min-side by the rejected "
         "piece-overlap test) and measured 0.279 by corridor area - the "
         "nearest non-duplicate in the whole corpus. Collapsing them would "
         "halve a real obligation where nothing downstream could see it",
         neuter=_neuter_collapse_on_endpoint_proximity,
         neuters="the rejected alternative: collapse whenever any two banks "
                 "are within 3 m"),
    Case("a refusal whose prescribed repair the WRITER FORBIDS says so",
         True, _case_a_refusal_whose_repair_the_writer_forbids_says_so,
         "T7-stathub-stenvik-x01's premise arm prescribes 'these stations "
         "belong to the TERRAIN WRITER'. MEASURED on ledger "
         "0ebe71d95cf2506d / 5,421 lines over 132,148 owned points: the "
         "write is REFUSED on 13 of its 51 metres, nearest owner "
         "bridge_body_place seq 5383 at 0.055 m. A deck's own ground is "
         "flatten=FORBIDDEN by construction, so every crossing with a deck "
         "standing on it is unclosable BY CONSTRUCTION - and the gate "
         "counted it beside an actionable defect",
         neuter=_neuter_termination_is_never_asked,
         neuters="the old refusal: name a remedy and never ask whether the "
                 "one sanctioned write path would perform it"),
    Case("an UNREAD instrument is 1E naming the read, never ACTIONABLE",
         False, _case_an_unread_instrument_is_out_of_order_not_actionable,
         "a guard that reports a repair open when it cannot see is the "
         "defect this gate keeps finding; twice tonight it was committed "
         "by instruments built to find it",
         neuter=_neuter_termination_is_always_forbidden,
         neuters="the over-correction: class every premise refusal "
                 "unclosable, emptying the work queue by declaring the work "
                 "impossible"),
    Case("EVERY refusal this section makes carries a termination class",
         False, _case_every_refusal_this_section_makes_carries_a_class,
         "the full gate on save 245daa7f: roads refused 87 times and 84 of "
         "the 87 carried NO CLASS AT ALL - obstruction 53, connectivity 11 "
         "and traversability 10 emitted no class key, so the largest "
         "section in the tree was the only one whose refusals nobody could "
         "triage. The operator was handed 87 and asked to sort work from "
         "decisions by hand",
         neuter=_neuter_only_the_crossings_arm_is_classified,
         neuters="the shipped state: only the crossings arm is classified "
                 "and the other three are silent"),
    Case("a destination blocked by a CROSSING inherits that crossing's "
         "class", False,
         _case_a_blocked_destination_inherits_its_blockers_class,
         "the full gate on save 245daa7f: the `boathouse` and `brgs1` "
         "connectivity rows both publish link_crossing S1 and both "
         "shipped ACTIONABLE - 'the remedy is a deck on S1' - and NOTHING "
         "in that run had asked about S1. The inheritance reads "
         "spans[link_crossing], spans is indexed by the SEGMENT corpus, "
         "and S1 is a `declared` crossing with segment_id null belonging "
         "to NO segment (the gate reads 17 crossings from "
         "roads/crossings.yaml, the builder 15 from segments.yaml - two "
         "populations, one obligation). So the lookup missed and the arm "
         "manufactured a class for a crossing it had never judged, while "
         "the crossings arm in the SAME LOOP had already classified S1's "
         "own refusal. S1's abutments MEASURE 29.193 at (-312,-62) and "
         "31.513 at (-326,-66) against one contracted deck datum of "
         "30.600: a 0.95 m cut at the west end and a 1.354 m FILL at the "
         "east, whose ground is already under the 30.0 m water plane. Two "
         "rows on the work queue for a deck nothing here can abut",
         neuter=_neuter_a_missing_crossing_is_assumed_buildable,
         neuters="the inheritance, back to the shipped state: a crossing "
                 "absent from `spans` is assumed buildable and its "
                 "destination published ACTIONABLE"),
    Case("a traversability step INSIDE a declared crossing's span carries "
         "the crossing's class", False,
         _case_a_step_inside_a_declared_span_is_the_crossings,
         "MEASURED on the Fresh42 drive-2 save a521b70356e7: S4-meadhall-"
         "southeast stations 341-346 (1.138, 1.037, 0.781, 0.781 m), all "
         "inside S4-meadhall-southeast-x01's banks (396,-424)-(396,-450), "
         "published ACTIONABLE with a 1.885 m cut at the deck's own landing "
         "while the crossing's row said the remedy is a deck",
         neuter=_neuter_a_step_in_a_span_is_the_roads,
         neuters="every traversability step back on the cut path, inside "
                 "a declared span or not"),
    Case("a MISSING DECK is ACTIONABLE iff a declared build step lays decks",
         False, _case_a_missing_deck_is_work_iff_the_build_lays_decks,
         "drive 2, frozen Fresh42 a521b70356e7: S4-meadhall-southeast-x01 "
         "and T3-temple-meadhall-x01 undecked, refused by no builder "
         "precondition, published UNKNOWN; the four S4 steps inside "
         "S4-x01's span inherited it - 6 of 18 roads rows",
         neuter=_neuter_the_missing_deck_class_is_typed,
         neuters="the class typed as 1C_UNSERVED instead of read off "
                 "buildstage.BUILD_STEPS"),
    Case("a BODY in the carriageway is 1D_OBSTRUCTED, not 'no tool does "
         "this'", False, _case_a_body_in_the_carriageway_is_not_a_missing_tool,
         "54 rows on Ulfsland - 47 distinct bodies, 439 must-climb pieces, "
         "45 leaving a walker-wide gap and 2 blocking outright, worst "
         "blackmarble_2x2x2 at (-1093.2,7484.4) with 25 of 457 pieces in "
         "the carriageway. The ground under them is writer-clear and "
         "uncontested and the road is still shut",
         neuter=_neuter_a_body_has_no_class_of_its_own,
         neuters="the forced fit: classify an obstruction on its TERRAIN "
                 "answer alone, which reads 1C_UNSERVED - 'no tool in this "
                 "tree performs the remedy' - over 54 rows whose remedy two "
                 "tools perform"),
    Case("a road contesting its OWN carriageway is not 1B_CONTESTED",
         False, _case_a_road_contesting_its_own_carriageway_is_not_1B,
         "all three of this section's 1B rows named the refusal's own "
         "segment as the contester - T13's step against seq 338, N4's "
         "vertex against seq 2754. A road's carriageway is ALWAYS inside "
         "its own terrain_write, so an unexcluded self-contest makes every "
         "road repair on every world an operator decision",
         neuter=_neuter_the_refusal_does_not_state_its_own_actor,
         neuters="roads stops stating whose refusal it is, so the shared "
                 "classifier has nothing to compare the contester against"),
    Case("a ctx-wiring bug RAISES; only a bad chain is survived", False,
         _case_a_ctx_wiring_bug_is_never_an_unreadable_chain,
         "the owner-set read asked for `ctx.world` on a Ctx that only has "
         "`ctx.args.world`, inside the try whose except exists to survive "
         "an unparseable ledger. MEASURED on save 245daa7f: every crossings "
         "row carries repairable_by_terrain_write null and ground_owners "
         "{}, and 1A_FORBIDDEN is 0 of 106 refusals gate-wide on a world "
         "with 132,148 owned points. Settlements committed the identical "
         "line in the same run",
         neuter=_neuter_the_world_name_is_resolved_inside_the_guard,
         neuters="every argument resolved inside the guard, so a "
                 "ctx-wiring bug comes back as owners=None and a log line"),
    Case("OUR OWN deck's poles in OUR OWN carriageway are factory work, "
         "owned by the step that placed them", False,
         _case_our_own_deck_in_our_own_carriageway_is_factory_work,
         "drive 3, frozen Fresh42 05eb7c6c05de: roads/obstruction '36 of "
         "134 pieces of one body stand in the carriageway: wood_pole', all "
         "36 placed by T1-portalhub-temple-x01's bridge_body_place records "
         "(seq 2682-2684, step 6.5 decks), classed 1D_OBSTRUCTED on a board "
         "of 49 refusals and ACTIONABLE 0",
         neuter=T.neuter_blocker_ownership("blind"),
         neuters="ownership blind: the shipped rule, every blocker an "
                 "operator's whoever placed it"),
    Case("a FOREIGN body in the carriageway stays 1D_OBSTRUCTED", False,
         _case_a_foreign_body_in_the_carriageway_stays_1D,
         "the same shape one metre off every commanded pivot: no ledger "
         "record placed it, so it may be a player's or the seed's",
         neuter=T.neuter_blocker_ownership("all_ours"),
         neuters="every named blocker claimed as ours, so a stranger's "
                 "body becomes factory work"),
    Case("an UNREAD chain makes a body 1E, never a foreign 1D", False,
         _case_an_unread_chain_is_not_a_foreign_blocker,
         "a ledger that cannot be read answers nothing about whose piece "
         "stands in the road; reading that silence as 'foreign' is the "
         "unread-is-clear defect in the ownership direction",
         neuter=T.neuter_blocker_ownership("unread_is_foreign"),
         neuters="an unread or unnamed blocker keeps its operator class"),
    Case("a road cut into OUR OWN pad's write is ours to reconcile, owned "
         "by the later claim", False,
         _case_a_road_contesting_our_own_pad_is_ours_to_reconcile,
         "drive 3: five termini (S13, S2, S7, T4, T6) climb 0.9072 m onto "
         "complete-station-hub and the cut lands in the pad's own write "
         "(seq 631, step 2); classed 1B_CONTESTED as if the pad were a "
         "stranger's",
         neuter=T.neuter_blocker_ownership("blind"),
         neuters="ownership blind: both claims ours still read as a "
                 "contest between strangers"),
    Case("an UNREAD owner set classifies NOTHING as work", False,
         _case_an_unread_owner_set_classifies_nothing_as_work,
         "the same save: with the owner set never read, ACTIONABLE must be "
         "0 across the section - and the rows with a BODY in them keep "
         "their class, because a standing body is a positive answer from an "
         "instrument that did answer",
         neuter=_neuter_unread_is_read_as_clear,
         neuters="the conflation: an owner set that could not be read "
                 "substituted with an EMPTY one, so 'nobody could tell me' "
                 "becomes 'nobody owns it' and the gate reports work "
                 "available precisely where it cannot see"),
    Case("a nested report function never REBINDS a name it READ", False,
         _case_a_report_never_shadows_the_report_it_reads,
         "roads' own `render` read `c` (the connectivity report) and later "
         "bound `c = x['channels']`, and read `v` (the coverage report) and "
         "later ran `for k, v in bt.items()`. Python scopes statically, so "
         "neither binding had to run: EVERY render of this section raised "
         "UnboundLocalError after printing the population and the "
         "traversability block. The module imports, the section runs, the "
         "JSON is complete and the verdict is right - only the thing a "
         "human reads dies, and it dies far enough in to look like it "
         "worked",
         neuter=_neuter_shadowing_is_never_looked_for,
         neuters="the state that shipped: nobody asks, so a report that "
                 "cannot render reads exactly like one that can"),
    Case("a DECLARED CROSSING is inside the corridor mask", False,
         _case_a_declared_crossing_is_inside_the_corridor_mask,
         "W10-brgs1-treenear-x01 at fp 7632b3a5674842bd: a COMPLETE "
         "319-piece deck - 94 wood_floor at lateral +/-1.000, along 0..92, "
         "every one at y 32.403 - was reported as 84 of 90 metres carried "
         "with 4 break(s), first break (-325.6,-68.0). Seven samples, "
         "k=0..6 at bank_a, lie OUTSIDE the mask built from all 22,000 "
         "stations (77,227 cells), so the tiles above them were never in "
         "the grid and surfaces_at could only return the bed. A crossing "
         "is by construction water the ribbon refused to write, so it "
         "contributes no station to the population the mask was derived "
         "from: the gate refused work that had been done",
         neuter=_neuter_the_mask_is_built_from_centrelines_alone,
         neuters="the shipped mask: built from the built centrelines alone, "
                 "so every crossing metre further than CORRIDOR_MASK_M from "
                 "a road is invisible to the instrument that decides "
                 "whether a deck stands there"),
    Case("a 4 m wall in a 6 m carriageway is CLIMB but GO ROUND", False,
         _case_four_metre_wall_go_round),
    Case("a wall across the WHOLE carriageway BLOCKS", True,
         _case_full_wall_blocks,
         "N0 (-326.5,1234.0) stone_wall_4x2, no way round"),
    Case("a 0.5 m kerb is under the slide limit and is NOT an obstruction",
         False, _case_kerb_is_not_an_obstruction),
    Case("our own lamp at 3.55 m lateral is OUTSIDE the paved width", False,
         _case_our_own_lamp_is_beside_the_road,
         "road lamps stand at exactly half_width + 0.55 m lateral"),
    Case("a deck a walker is standing ON is not an obstruction of it", False,
         _case_deck_underfoot_is_not_an_obstruction),
    Case("a deck over a bed that has FALLEN AWAY is not a wall", False,
         _case_a_deck_over_a_fallen_bed_is_not_a_wall,
         "N1-wnorth-shelf (-85,2710)+(-84,2710): our own 42 m x 6 m "
         "wood_floor deck at top 32.500 over a bed falling 31.773 -> "
         "29.707 into water was reported `blocked`, category c_structure, "
         "prefabs ['wood_floor'], gating 4 destinations - and the remedy "
         "the report named was to move the structure",
         neuter=_neuter_surface_chosen_per_station,
         neuters="the walker's surface chosen at each station on its own, "
                 "climbing only from the ground directly beneath him"),
    Case("a HOLE in that deck is still REFUSED", True,
         _case_a_hole_in_that_deck_is_still_refused),
    Case("a bounding-box top does NOT carry a walker along the road", True,
         _case_a_bounding_box_does_not_carry_a_walker_along_the_road,
         "'a bounds-only top is never a hard excuse' was implemented on the "
         "RECEIVING side only -- propagation could not step ONTO a box but "
         "could still propagate FROM a station carried by one. NOTE THE "
         "PRIOR STATE IS THE RULE, NOT A SIGHTING: the N0b st656 reading "
         "that prompted it was MIS-ATTRIBUTED (measured 2026-09-22 on "
         "frozen 04e68ec3 -- deleting the stair moves only its own surface, "
         "over-limit links 1 -> 1, the carrier is a leaf with nothing to "
         "lend), so this case CONSTRUCTS a carrier that does have a "
         "measured neighbour in reach rather than pinning that reading",
         neuter=_neuter_bounds_only_may_propagate,
         neuters="the bounds-only guard restored on the RECEIVING side "
                 "only, so a station carried by a bounding box propagates "
                 "its over-claimed surface again"),
    Case("the SAME ramp with a MEASURED collider DOES carry him", False,
         _case_a_measured_ramp_does_carry_him),
    Case("a 2 m wall top is NOT reached by walking along the road", True,
         _case_a_wall_top_is_not_reached_along_the_road),
    Case("vegetation is excluded from the obstruction population", False,
         _case_vegetation_is_not_in_the_population,
         "'184 objects left standing' was a census of clearable vegetation"),
    Case("a built wall is NOT in the vegetation population", True,
         _case_a_built_wall_is_in_the_population),
    Case("connectivity REFUSES a destination behind a step no walker can "
         "take", True, _case_connectivity_refuses_a_wall,
         "'portal hall -> Vestvik 3,248.7 m' over a 6.12 m wall"),
    Case("connectivity ACCEPTS the same destination once the step is inside "
         "the limit", False, _case_connectivity_accepts_a_walkable_step),
    Case("a diagonal read as ONE 1.414 m step accepts a wall in the cell",
         False, _case_diagonal_read_as_one_step_hides_a_wall,
         "the vertex-chain proposal: 1.414 m allowance 1.1049 m, which "
         "would have kept N1 (433,3318)->(433,3319) 6.349 m invisible"),
    Case("the 1 m MESH EDGES find the wall the diagonal hid", True,
         _case_mesh_edges_find_the_wall_in_the_cell,
         "N1-wnorth-shelf (433,3318)->(433,3319) worst leg 6.349 m, against "
         "a station-walk worst of 2.552 m for the whole segment"),
    Case("a 1.05 m rise across a diagonal is REFUSED at the 1 m allowance",
         True, _case_diagonal_judged_at_the_one_metre_allowance,
         "14 of the 79 ground pairs sat between 0.7813 m and 1.1049 m"),
    Case("a diagonal a walker can actually take is ACCEPTED", False,
         _case_a_walkable_diagonal_is_accepted),
    Case("a diagonal whose corners were never read is UNRESOLVED, not clean",
         True, _case_unread_corner_is_unresolved_not_clean,
         "7,272 of 19,076 transitions had corners in nobody's population"),
    Case("coverage REFUSES an object whose prefab was never measured", True,
         _case_coverage_refuses_an_unmeasured_prefab,
         "8 modded prefabs, 76 objects, in road corridors with no corpus "
         "entry"),
    Case("coverage ACCEPTS a corridor whose every prefab is in the corpus",
         False, _case_coverage_accepts_a_measured_corridor),
    Case("severance names a dry over-limit link REGRADEABLE", True,
         _case_severance_names_a_regradeable_step,
         "38 over-limit locations on the built roads"),
    Case("a diagnosable severance never lands in the residual", False,
         _case_a_diagnosable_severance_never_lands_in_the_residual),
    Case("severance is SILENT on a network that is all one piece", False,
         _case_severance_accepts_one_whole_network),
    Case("severance REFUSES to call an over-water link regradeable", True,
         _case_severance_refuses_to_call_an_over_water_link_regradeable,
         "T4's six over-limit transitions are two never-declared crossings "
         "with no deck, bed 27.22-28.30 below the y=30 plane",
         neuter=_neuter_water_test,
         neuters="the water test removed from the severance categoriser"),
    Case("severance names the STRUCTURE when a body leaves no gap", True,
         _case_severance_names_the_structure_not_the_ground,
         "15 stations with no way round, 7 bodies",
         neuter=_neuter_structure_category,
         neuters="the `blocked` arm removed from the categoriser, so a body "
                 "in the carriageway reads as ground to move"),
    Case("severance REFUSES to call FROZEN ground regradeable", True,
         _case_severance_refuses_to_call_frozen_ground_regradeable,
         "harboursouth's last two links, 0.794 m each against 0.7813, over "
         "four lattice samples seq 5322 refused beside harbour-temple-south's "
         "signpost; they were handed out as a_regradeable and a re-emit "
         "moves them 0.000 m",
         neuter=_neuter_frozen_test,
         neuters="the frozen-ground test removed from the severance "
                 "categoriser"),
    Case("severance still names MOVABLE ground regradeable", True,
         _case_severance_still_calls_movable_ground_regradeable,
         "the same geometry with nothing protecting it: a_regradeable, so "
         "the frozen arm discriminates instead of refusing every step"),
    Case("severance REFUSES a step whose REPAIR stands on frozen ground",
         True, _case_severance_refuses_a_step_whose_REPAIR_is_frozen,
         "N1-wnorth-shelf (433,3315)->(433,3316) on save 2a6940a934b5: "
         "a_regradeable, ground_frozen_by null at BOTH its own vertices, "
         "gating shelf/mid1/mid2/terminus - and all 7 vertices of the "
         "cut-only disposal that closes it are inside protected extent, so "
         "the re-emit the report asks for is refused and moves 0.000 m. "
         "Five of that save's twenty-five over-limit steps are this shape",
         neuter=_neuter_disposal_population,
         neuters="the repair's population put back to the DEFECT's own two "
                 "vertices, which is what the categoriser measured before "
                 "`cut_disposal` existed"),
    Case("severance still regrades a step whose REPAIR is movable", True,
         _case_severance_still_regrades_a_step_whose_repair_is_movable,
         "the same geometry with nothing protected anywhere in the cone: "
         "a_regradeable, so the disposal arm discriminates instead of "
         "refusing every step that needs more than two vertices"),
    Case("the cut-only disposal closes its step and never RAISES a sample",
         False, _case_the_disposal_closes_its_step_and_never_raises_a_sample,
         "the shape roads/regrade.py emits: cut-only, and Lipschitz on the "
         "4-connected mesh so the rim is inside the limit BY CONSTRUCTION "
         "rather than by inspection - every rim refusal this project has "
         "had (T3 217 caused, S12 313, T12 97) came from a profile re-fit "
         "with no reason to respect its own boundary",
         neuter=_neuter_disposal_is_a_cone,
         neuters="the disposal reduced to lowering ONLY the step's own high "
                 "vertex - cut-only, closes the step, and moves the cliff "
                 "one metre along"),
    Case("an unreachable component with no cause is REFUSED", True,
         _case_an_unreachable_component_without_a_cause_is_refused,
         "25 of 27 unreachable destinations carried no diagnosis at all"),
    Case("a NEAR MISS over joined ground is an EDGE, measured", True,
         _case_a_near_miss_over_joined_ground_is_an_edge,
         "T8-stenvik-wttown (820,738) and R1-ulfsgard-t8junction (824,738) "
         "are 4.000 m apart against a 3.0 m station threshold, their "
         "carriageways overlap, both ends read the same pinned datum "
         "39.608, and `t8junction` + `ulfsgard` were handed out UNREACHABLE "
         "behind a link labelled `needs road, not grading`",
         neuter=_neuter_near_miss_bridging,
         neuters="the near-miss ground reading removed from the graph "
                 "build, so two overlapping carriageways are severed on "
                 "the strength of a station spacing"),
    Case("a NEAR MISS over a cliff is still REFUSED, with its metre", True,
         _case_a_near_miss_over_a_cliff_is_still_refused,
         "S8-wtsouth-treesth (480,434) to T4-meadhall-stathub (474,440), "
         "8.485 m; the EASIER mesh edge of (478,436)->(477,437) still drops "
         "1.584 m through corner (477,436), all three of (477,436), "
         "(477,437) and (478,437) `modified = 0` in every blob covering "
         "zone (7,7)"),
    Case("a NEAR MISS with an unread sample is REFUSED, not assumed flat",
         True, _case_a_near_miss_nobody_could_read_is_refused,
         "absent is not zero: the same 4.000 m line with one of its three "
         "interior samples withheld from the ground reader"),
    Case("a wall on BOTH CORNERS of a bridged cell REFUSES the bridge",
         True, _case_a_wall_on_the_cell_corners_refuses_the_bridge,
         "the world's own bridged line (T8|R1 along z=738) is AXIS-ALIGNED "
         "and never crosses a cell, so the corner rule is proved on a "
         "fixture: endpoint to endpoint the move reads 0.000 m, across the "
         "mesh it is 2.000 m - the same reading that kept a 6.349 m wall on "
         "N1 invisible",
         neuter=_neuter_bridge_corners,
         neuters="the bridge judging a diagonal as ONE 1.414 m hypotenuse "
                 "instead of two 1 m mesh edges through a shared corner"),
    Case("a bridged cell with NO wall in it still bridges", False,
         _case_a_clear_cell_still_bridges,
         "the same three diagonal moves with a 0.200 m corner: a corner "
         "rule that refuses every diagonal is not a corner rule"),
    Case("a DECK's own record yields its pieces, not just a disc", False,
         _case_a_decks_pieces_are_recoverable_from_its_own_record,
         "every bridge_body_place record this project has written carries "
         "requires.blobs EMPTY and a plan_sha256 of a blob nobody stored, "
         "with the command list inline in `wire`: MEASURED on this chain, "
         "79 of 79 recovered ZERO pieces and 1,552 discs of 26.16-33.13 m, "
         "so three of one agent's nineteen validated road repairs were "
         "refused by a bounding circle - one of them by a deck landed an "
         "hour earlier",
         neuter=_neuter_bridge_piece_evidence,
         neuters="the reader consulting ONLY the blob store, so a body that "
                 "carries its own wire recovers nothing and the guard falls "
                 "back to its bounding disc"),
    Case("a SHORT wire is UNREADABLE, not a small body", False,
         _case_a_short_wire_is_unreadable_not_a_small_body,
         "the lh-north shape: a record declaring 64 commands read as 3, "
         "4.7% of a protected body, which is worse than the disc because it "
         "looks like evidence"),
    Case("a point INSIDE a deck tile is carried by it", False,
         _case_a_tile_carries_the_ground_it_stands_on,
         "roads/boardwalk.py::tile_covers rotated a VALHEIM COMPASS yaw with "
         "the MATH-convention FORWARD matrix instead of its inverse, testing "
         "a square 2*yaw off the tile it named. MEASURED on "
         "W10-brgs1-treenear-x01, yaw 170.346 deg: a 319-piece deck covering "
         "90 of 90 declared metres was refused at 79 of 90, the eleven "
         "phantom holes reading |along| 0.995 / |lat| 0.989 against a "
         "half-extent of 1.000 - strictly interior. The deck was never "
         "placed and the 89.44 m channel stayed open",
         neuter=_neuter_tile_frame,
         neuters="the FORWARD rotation, under which the interior point "
                 "(0.990, 0.990) reads back (1.4001, 0.0000) and is refused"),
    Case("a point BEYOND a deck tile is not carried", False,
         _case_a_tile_does_not_carry_ground_beyond_it,
         "the repair must not be 'widen the tile': a coverage test that "
         "carries ground no deck is over credits every span with metres "
         "nobody can stand on, and nothing afterwards can see it",
         neuter=_neuter_tile_covers_everything,
         neuters="a coverage test that answers True everywhere - the purest "
                 "form of 'cover everything'. The FORWARD-rotation neuter "
                 "cannot reach this arm, because removing coverage can only "
                 "ever make a refusal more certain: one neuter per "
                 "DIRECTION, not one per predicate"),
    Case("a crossing that JOINS two segments is a buildable span", False,
         _case_a_crossing_that_joins_two_segments_is_buildable,
         "roads/boardwalk.py enumerated roads/segments.yaml while this "
         "section judges roads/crossings.yaml: MEASURED, the gate counted 17 "
         "crossings and the only builder in the tree counted 15, missing S1 "
         "and S2 because a crossing that JOINS two segments carries "
         "segment_id null and lies in no segment. S1's deck is the named "
         "remedy for three refusals and no tool could be pointed at it",
         neuter=_neuter_span_population_segments_only,
         neuters="the segments-derived population, which drops every record "
                 "with a null segment_id - 16 buildable spans become 15 and "
                 "S1 is the one lost"),
    Case("a segments-keyed population is a LOSS, not an emptying", False,
         _case_the_span_population_is_not_merely_empty,
         "a neuter that removes records is satisfied by a key that returns "
         "nothing at all, so the partner arm asserts that a crossing the key "
         "must NOT lose survives it. Over-keying is silent and expensive, "
         "under-keying is silent and false, and only both arms tell them "
         "apart",
         neuter=_neuter_span_population_empty,
         neuters="a population that names no crossing at all - the "
                 "degenerate over-correction, which reads as 'nothing to "
                 "build'"),
    Case("a station-adjacent span metre SURVIVES the centrelines-only mask",
         False,
         _case_a_station_adjacent_span_metre_survives_the_centrelines_only_mask,
         "dropping the crossings from the mask's population cannot make a "
         "cell APPEAR, so the refusing arm alone is satisfied by a mask "
         "that simply SHRANK - including one that returned nothing. "
         "MEASURED on the fixture: 28 of 91 span metres survive the "
         "centrelines-only mask and 0 of 91 survive a mask that names "
         "nothing, so only the second is a lost dependency rather than an "
         "empty index",
         neuter=_neuter_the_mask_names_nothing,
         neuters="a corridor mask with no cells in it at all - the "
                 "degenerate over-correction, which reports every object as "
                 "outside every corridor and reads as 'there is nothing "
                 "here to judge'"),
    Case("a declaration further off its own line than a deck is WIDE is "
         "refused", False,
         _case_a_declaration_further_off_its_line_than_a_deck_is_wide,
         "roads/route.py::find_crossings takes bank_a/bank_b from the "
         "PRE-straighten route polyline (lines 741-746) and "
         "roads/network.py::build_segment writes "
         "centreline_straight_from_to from the POST-straighten points "
         "(straighten at line 576, write at 677-687) - the bank is never "
         "re-read after the straighten that moved the line it names. "
         "MEASURED over roads/crossings.yaml: 15 of 17 records declare a "
         "centreline, 13 of those are 1.016-6.069 m off it and NINE are "
         "beyond the deck's 3.000 m half-width, so no deck on the road "
         "covers the line the record declares",
         neuter=_neuter_the_declaration_is_measured_against_itself,
         neuters="the state that shipped: each bank measured against the "
                 "line the RECORD draws between its own two banks, which "
                 "every bank lies on by construction, so the offset is "
                 "identically zero and the corpus is never refused"),
    Case("a crossing the BUILDER refuses is not ACTIONABLE", False,
         _case_a_crossing_the_builder_refuses_is_not_actionable,
         "the crossings arm published ACTIONABLE for every undecked "
         "crossing with a sound premise on the strength of one sentence - "
         "'the remedy is a deck, which writes no terrain' - which answers "
         "the SECOND and THIRD clauses of ACTIONABLE and never the first. "
         "MEASURED on 7632b3a5674842bd: eight of the gate's twelve "
         "remaining ACTIONABLE rows were crossings and the only builder in "
         "this tree refuses every one of them before a tile is placed",
         neuter=_neuter_the_builder_is_never_asked,
         neuters="the shipped assumption: the predicate answers 'nothing "
                 "refuses this' for every record, which is what the gate "
                 "believed for the whole life of the crossings arm"),
    Case("a crossing the BUILDER has built is not refused", False,
         _case_a_crossing_the_builder_has_built_is_not_refused,
         "never asking cannot make a passing record fail, so the refusing "
         "arm's neuter cannot reach this one and a predicate that simply "
         "said no to everything would be stamped PROVEN. "
         "W10-brgs1-treenear-x01 carries a 319-piece deck over 90 of its "
         "90 declared metres and its worst bank is 1.016 m off its own "
         "centreline, inside the 3.000 m half-width",
         neuter=_neuter_the_builder_refuses_everything,
         neuters="a buildability predicate that refuses every crossing - "
                 "the degenerate over-correction, which retires the whole "
                 "world's work queue and reads like triage"),
    Case("the builder's WHOLE precondition vector is named", False,
         _case_the_builders_whole_precondition_vector_is_named,
         "DECK_REMEDY_PRECONDITIONS declared 3 names while deck_remedy "
         "produced 5 - intersection 2, one declared name produced by no "
         "branch, three produced names declared nowhere - and the tuple had "
         "ZERO readers, so it could not be wrong loudly. On its first run "
         "the arm found a second: `reconciliation` was recorded in `unasked` "
         "when the reconciler had NOT run and NOWHERE when it HAD, so the "
         "one precondition separating a producer defect from a prerequisite "
         "nobody ran vanished from the inventory exactly when it was used",
         neuter=_neuter_the_builder_stops_asking_reconciliation,
         neuters="the narrowing: deck_remedy stops naming `reconciliation`, "
                 "which merges T4-x02's refused projection with S6/T5/"
                 "W9-x02's clean ones and leaves every other case green"),
    Case("NO builder answer can produce ACTIONABLE", False,
         _case_no_builder_answer_can_produce_ACTIONABLE,
         "deck_remedy makes buildable=True unreachable, so the remaining "
         "wrong answer moved into the CONSUMER: buildable is None means "
         "UNJUDGED and `if not ans['buildable']` reads as its own opposite. "
         "MEASURED tree-wide, every consumer tests `is False` today - the "
         "tree is right by discipline, one character from two opposite "
         "defects. deck_remedy_class cannot return ACTIONABLE from any "
         "branch for any answer including None",
         neuter=_neuter_the_class_accessor_can_say_yes,
         neuters="the chokepoint removed: an accessor that promotes an "
                 "UNJUDGED answer to ACTIONABLE - `if not "
                 "ans['buildable']` written out, which is the state the "
                 "whole crossings arm was in"),
    Case("a bank NO EMISSION REACHES is UNSERVED, not unjudged", False,
         _case_a_bank_no_emission_reaches_is_UNSERVED,
         "a refused abutment carried no class at all: 'the bench is 1.5 m "
         "low' does not say whether anything in this tree can raise it, so "
         "a crossing whose only remaining refusal was its abutment fell out "
         "of every class the gate can act on. MEASURED after `boardwalk "
         "reconcile` retired T5-temple-dockshore-x01's span-coverage "
         "refusal: bank_b IS the segment's last station, 525 of 526, "
         "is_bridge True, 0.00 m of its own declared 6.0 m run against a "
         "+1.500 m step from applied 31.000 onto a deck at 32.500. "
         "Corpus-wide 11 of 30 banks over 8 of 15 crossings declare a bank "
         "level their own segment has no station to carry, 7 with ZERO "
         "metres outboard",
         neuter=_neuter_the_reach_ignores_the_direction,
         neuters="the plausible simplification - 'how much road is near "
                 "this bank' - which loses the sign: T5 carries 677.69 m of "
                 "its own carriageway INBOARD of bank_a and 0.00 m outboard "
                 "of bank_b, so an unsigned reach files the terminus bank "
                 "as having 677 m of bench and calls it factory work "
                 "forever"),
    Case("the SAME step with road outboard is OUT OF ORDER", False,
         _case_the_same_step_with_road_outboard_is_OUT_OF_ORDER,
         "the survivor direction, and it is what stops the reach being "
         "'refuse every abutment'. S6-stenvik-hognest-x01 takes the same "
         "1.500 m step at the same bank name with 77.54 m of non-bridged "
         "station outboard of it, so an emission of its segment reaches the "
         "bench and the row is FACTORY WORK. A reach that answered 0 "
         "everywhere would file both as terminal and retire a real "
         "obligation",
         neuter=_neuter_the_reach_answers_zero,
         neuters="the degenerate over-correction: every bank answers 0 m "
                 "outboard, so this row and T5's become the same terminal "
                 "class and a real obligation is retired as ground nothing "
                 "in this tree owns. This case FIRST SHIPPED POINTED AT THE "
                 "`is_bridge` NEUTER AND CAME OUT ACCEPT UNDER IT: both the "
                 "direction and the is_bridge neuters can only make a reach "
                 "BIGGER, so a bank with 77.54 m of road outboard answers "
                 "identically under each - a registered case whose own "
                 "discriminator could not make it red"),
    Case("a BRIDGED station is not a bench", False,
         _case_a_bridged_station_is_not_a_bench,
         "`is_bridge` is a declared dependency TODAY'S CORPUS CANNOT PROVE: "
         "MEASURED over all 30 banks, outboard_m and regradeable_m are "
         "EQUAL on every one, so no live record separates 'a station "
         "exists' from 'a station the ribbon writes'. A dependency shown on "
         "a population that cannot discriminate it has been declared, not "
         "demonstrated, so this arm is constructed: 10 m outboard, all of "
         "it bridged",
         neuter=_neuter_the_reach_ignores_is_bridge,
         neuters="`is_bridge` dropped: the four bridged stations outboard "
                 "of the fixture's bank_b read as 10 m of bench and the "
                 "row flips from UNSERVED to factory work"),
    Case("a piece UNDER THE GROUND is refused before it is sent", False,
         _case_a_piece_under_the_ground_is_refused_before_it_is_sent,
         "MEASURED, OrderRun's own write: T9-wttown-dockshore-sb01 built 35 "
         "pieces with deck_datum, abutment, seating, structural and "
         "span_coverage ALL PASSING and its own report printing `worst "
         "burial 0.0 m`, and structures/buried then found 2 of those 35 "
         "COMPLETELY under the applied surface at (677,-92) and (679,-92), "
         "tops 0.400 m below the ground; W10-brgs1-treenear-x01 is the same "
         "state at 4 of 319. Neither neighbour owns the question - seating "
         "asks what is UNDER a piece, trim_buried_overhang asks about "
         "burial only BEYOND a bank - so a tile buried INSIDE the crossing "
         "was nobody's. And a deck is written flatten=FORBIDDEN, so once it "
         "stands writer._flatten_forbidden_check closes its own footprint "
         "to the cut that would free those pieces: the gate classed both "
         "rows 1A_FORBIDDEN naming the deck's own seq, 5421 and 5433",
         neuter=_neuter_burial_guard_absent,
         neuters="the state that shipped: no burial guard at all, five "
                 "guards passing, and the pieces go out under the ground"),
    Case("burial is judged at the GATE'S OWN limit", False,
         _case_burial_is_judged_at_the_gates_own_limit,
         "BURIAL_MAX_M is 0.30 m and belongs to trim_buried_overhang, a "
         "SHRINK decision that wants slack; structures/buried refuses at "
         "0.05 m. A builder judging at its own looser number passes pieces "
         "the gate then refuses - MEASURED on T9-wttown-dockshore-sb01, "
         "0.30 m catches 1 of the 2 tiles the gate found and 0.05 m catches "
         "both",
         neuter=_neuter_burial_uses_the_overhang_limit,
         neuters="the builder judges at its own 0.30 m overhang slack, so "
                 "it passes what the gate refuses"),
    Case("a corner of OPEN AIR is not buried", False,
         _case_a_corner_of_open_air_is_not_buried,
         "burial is 'under the ground EVERYWHERE', so the governing reading "
         "is the footprint MINIMUM and a single HIGH corner buries nothing. "
         "The way a pivot sample fails is therefore a FALSE REFUSAL: the "
         "centre of a 2 m tile under a mound with one corner in open air, "
         "retired as buried. This arm was first written the other way "
         "round, came out green under its own neuter and asserted a "
         "falsehood",
         neuter=_neuter_burial_samples_the_pivot,
         neuters="the shipped sample: one reading at the centre of a 2 m "
                 "tile decides whether the whole tile is under the ground"),
    Case("a deck that STANDS is not refused by the reach", False,
         _case_a_built_deck_is_not_refused_by_the_reach,
         "the trap a bank-coordinate probe sets, registered rather than "
         "remembered. MEASURED: W10-brgs1-treenear-x01's bank_a has 0.00 m "
         "outboard AND reads +0.987 m at the bank coordinate, and its "
         "319-piece deck IS IN THE GROUND - abutment_guard measures at the "
         "outermost surviving tile, where the ground has already reached H. "
         "So the reach may only be asked after that guard refuses; asked "
         "the way every current caller asks it must leave both conditional "
         "names UNASKED and refuse nothing",
         neuter=_neuter_the_builder_refuses_everything,
         neuters="a buildability predicate that refuses every crossing - "
                 "the degenerate over-correction this arm exists to catch"),
    Case("a severed link is keyed on the SUB-EDGE the walk refused", True,
         _case_a_severed_link_is_keyed_on_the_sub_edge_the_walk_failed,
         "harboursouth published category b_operator and link_frozen_by "
         "harbour-temple-south seq 24 beside termination_class ACTIONABLE, "
         "because the repair was keyed on the RUN convention over the "
         "link's 1.414 m straight line at the link's first endpoint: "
         "0.794 - 0.7813*1.414 = -0.311 clamped to 0.0, footprint 1.414 m "
         "(the bare lattice diagonal), owner None at 1.513 m. The "
         "traversability row naming that same 1 m sub-edge was already "
         "1A_FORBIDDEN at 1.445 m against the owner at 1.431 m",
         neuter=_neuter_connectivity_key_run_at_the_endpoint,
         neuters="the shipped key: the run convention over the link's "
                 "straight-line separation, at its first endpoint, sized "
                 "onto the walker's limit"),
    Case("the sub-edge key is a LOSS restored, not a blanket condemnation",
         True, _case_the_connectivity_key_leaves_an_unowned_link_alone,
         "the corner convention and the disposal depth can only make a "
         "footprint LARGER, so a key that declared every severed link "
         "owned would flip the other arm and be stamped PROVEN. This arm "
         "is scored on the same conjunction and falls to the opposite "
         "defect - sizing the cut off the WHOLE step, 0.900 m needing "
         "2.566 m against an owner 2.000 m off, where the 0.130 m a "
         "disposal actually writes needs 1.581 m and is permitted",
         neuter=_neuter_connectivity_key_sized_off_the_whole_step,
         neuters="a repair sized off the whole step rather than off the "
                 "part above the limit - the degenerate over-correction, "
                 "which reads as 'the operator must decide this'"),
    Case("a road gate reads THIS SEED'S locations", True,
         _case_a_road_gate_reads_THIS_seeds_locations,
         "MEASURED 2026-09-24 on Fresh42, `roads/ribbon.py build --segment "
         "S7-stathub-wtnorth --validate` rc=3: one run of one file printed "
         "'plan-time location gate: clear over 12376 generator instances' "
         "and then refused on a ShipSetting01 at (124.2,263.8) 18.2 m from "
         "a carriageway sample needing 25.0 m. 12,376 is Fresh42's count; "
         "that ShipSetting01 is PIRATE68's - within reach+9 m of S7's "
         "centreline Pirate68 holds 1 instance and Fresh42 holds 0. The "
         "plan-time gate asked poi.load(), which resolves by SEED; "
         "location_check and poi_standoff_discs named "
         "/tmp/settle/loc3/f6fe167f4fcd.json, one seed's content hash, with "
         "no seed check. S7's own earthwork was clean when it was refused: "
         "rim transfer 0 caused, 0 pre-existing. The same frozen hash chose "
         "the batter's keep-out discs, so all 957 poi wall clips over the "
         "14 Fresh42 segments were clipped against Ulfsland's locations",
         neuter=_neuter_location_dump_frozen_to_one_content_hash,
         neuters="the shipped resolver: a fixed tuple of paths, first one "
                 "that exists, no seed and no header check - ribbon.py:1967, "
                 "ribbon.py:2073, north.py:778 and clear.py:2020 as they "
                 "stood"),
    Case("the seed key is a POPULATION restored, not a blanket clearance",
         True, _case_the_seed_key_still_refuses_a_REAL_breach,
         "the other direction, and the one a fix aimed at S7 invites: a "
         "resolver that always names the dump the caller hoped for, or that "
         "hands back an empty set, makes every road clear and would flip "
         "the first arm to PROVEN having proved nothing. This arm stands on "
         "the FOREIGN seed where the identical 40 m strip runs 18.2 m from "
         "a 24.0 m-reach location and the canonical gate must still refuse "
         "it by 6.8 m. Both arms are scored on one conjunction, so no "
         "single degenerate answer satisfies them",
         neuter=_neuter_location_dump_always_the_home_world,
         neuters="a resolver that always returns the HOME dump whatever the "
                 "seed - the degenerate over-correction, which reads as "
                 "'every road clears every location'"),
    Case("a wet run is TERRAIN only if the road removes no water", True,
         _case_a_run_that_DROWNS_water_is_not_terrain,
         "MEASURED 2026-09-24 over all three plans: Fresh42 22 fords on 9 of "
         "14 segments, Ulfsland 32 on 19, the northern trunk 51 on 5, and "
         "618 of 618 submerged CENTRELINE stations under those 105 fords end "
         "at or above the 30.0 m plane, worst +6.97 m (S13-stathub-dock). A "
         "ford is filled to WATER_LEVEL + ROAD_FREEBOARD_M, so it raises "
         "every submerged station it covers BY CONSTRUCTION - and "
         "ribbon.py's water-sign gate refuses exactly that, because the "
         "generated field is the only record the water was there. Fresh42's "
         "T1-portalhub-temple reached the gate: 3 fords, 8 submerged "
         "centreline stations raised by up to 4.23 m, rc=8, '369 of 411 "
         "submerged sample(s) raised, worst +5.119 m at [-105,-274]', on the "
         "same run as 'crossing premise: CLEAR -- 0 of 0 crossing(s) asked'",
         neuter=_neuter_ford_keyed_on_depth_and_length,
         neuters="the shipped key: depth <= 1.0 m OR run <= 8.0 m, which "
                 "calls a metre of standing water a causeway"),
    Case("a shore that drowns NOTHING is still terrain", True,
         _case_a_shore_that_DROWNS_NOTHING_stays_terrain,
         "the opposite over-correction, and the one a fix aimed at T1 "
         "invites: 'no wet run is ever a ford' satisfies the other arm for "
         "the wrong reason and hands Crossings a deck over dry shore. "
         "MEASURED that the surviving population is not empty - 6 of "
         "Fresh42's 22 fords, 12 of Ulfsland's 32 and 15 of the northern "
         "trunk's 51 lie entirely at or above the plane and have no water to "
         "remove. Both arms are scored on one conjunction",
         neuter=_neuter_ford_abolished_entirely,
         neuters="no wet run is ever terrain - the degenerate "
                 "over-correction, which reads as 'every shore is a bridge'"),
    Case("a station is over water if the BENCH is, not if the LINE is", True,
         _case_a_bench_over_a_bay_is_a_crossing,
         "MEASURED 2026-09-24 on Fresh42 T1-portalhub-temple AFTER ford_rule "
         "had already turned its three fords into three declared bridges: "
         "`ribbon.py --validate` still refused at water_sign, 274 of 343 "
         "submerged samples raised, and 179 OF THOSE 274 WERE "
         "CARRIAGEWAY-BAND (lateral <= 4.75 m) on stations the centreline "
         "test called DRY, median lateral offset 3.75 m -- inside the 4.0 m "
         "bench, outside the line that was measured. `find_crossings` read "
         "`t`, the CENTRELINE generated height, while ribbon.py benches a "
         "LEVEL surface out to route.footprint_half_m and water_sign judges "
         "every sample of it. Re-keying on the footprint minimum took the "
         "network's drained samples 1,753 -> 1,111 and turned "
         "T13-temple-harboursouth from rc 8 to rc 0. This is the FOURTH "
         "instance of one object certified on the centreline and consumed "
         "over the footprint: network.transverse_window closed it for the "
         "APPLY CLAMP after N4-mid2-terminus reported stations_over_clamp 0 "
         "against 71 real breaches, all at lat 3.99-4.12 m, and the wet test "
         "was left behind",
         neuter=_neuter_wet_keyed_on_the_centreline,
         neuters="the shipped key: `t <= WATER_LEVEL + ROAD_FREEBOARD_M` on "
                 "the CENTRELINE, a strict subset of the samples the write "
                 "levels"),
    Case("a bench CLEAR of the freeboard line is left as terrain", True,
         _case_a_bench_clear_of_the_water_is_left_alone,
         "the opposite over-correction, and the obvious one: reach further "
         "out. The BATTER is a plane at grade that follows ground down, not "
         "a fill to road height, so water inside its 10.5 m reach is not "
         "water this write removes -- keyed on the batter reach instead of "
         "the 4.0 m bench, every shoreline road in the world becomes a deck. "
         "MEASURED that the surviving population is not empty: with the "
         "bench key landed, 5 of Fresh42's 14 segments read water sign CLEAR "
         "(T6, T7, T8, T13, S7) and 365 of the 1,111 samples still refused "
         "are batter-band on terrain stations, none of which the bench rule "
         "claims. Both arms are scored on one conjunction",
         neuter=_neuter_wet_keyed_on_the_batter_reach,
         neuters="the extent widened from the LEVEL BENCH to the BATTER "
                 "REACH, which calls a bench standing clear of the water wet "
                 "because water lies 10.5 m away"),
    Case("a batter may not lift the bed OUT of the water", True,
         _case_a_batter_may_not_fill_the_bay_to_make_a_taper,
         "MEASURED 2026-09-24 over all fourteen Fresh42 segments, AFTER both "
         "ford_rule and WET_DEPENDS_ON had landed: 750 seed-submerged "
         "samples raised to or above the 30.0 m plane, and 0 OF THEM "
         "CARRIAGEWAY. Every one is BATTER, written at ribbon.py:2327 after "
         "`batter_water_gate` permitted the raise -- T1 109, T9 111, T4 127, "
         "S4 78, S5 72, S11 79, T3 76, T5 76, T8 19, S13 3. S5's centreline "
         "terrain minimum is 33.07 m and its profile minimum 34.87 m: the "
         "road is nowhere near the sea and its TAPER filled the bay by up "
         "to 6.33 m. The permit's declared handoff is `water_sign`, whose "
         "named remedy is THE ROUTE, and the batter's extent is a function "
         "of how far the road stands above the ground beside it -- no "
         "re-route and no deck moves a toe, so the refusal arrived after "
         "the whole rasterisation with no remedy attached. The cliff the "
         "permit traded against is GONE: 158 stations have their levelled "
         "footprint below the plane and 0 OF 158 ARE UNBRIDGED, so a "
         "causeway flank is unreachable from any plan this router emits. "
         "With the rule landed the 750 go to 0, the carriageway is "
         "UNCHANGED on 14 of 14, and of the 419 walls recorded at the 530 "
         "clips, 419 STAND ON SEED-SUBMERGED GROUND and 0 on land -- a "
         "shoreline, not a cliff",
         neuter=_neuter_batter_water_permits_every_raise,
         neuters="the shipped rule: refuse the cut, PERMIT every raise "
                 "whatever height it lands at - `resulting_height` dropped "
                 "from the key, which is the exact state that shipped"),
    Case("a toe that stays UNDER the plane is still laid", True,
         _case_a_toe_that_stays_UNDER_the_plane_is_still_laid,
         "the opposite over-correction, and the one a fix aimed at S5 "
         "invites: refuse every batter sample over submerged ground. It "
         "satisfies the other arm for the wrong reason, reinstates the "
         "cliff at the first submerged sample on every ray, and takes the "
         "legitimate toe with it -- a fill that grades into a shoal and "
         "stays under the plane removes no water at all. MEASURED that the "
         "surviving population is not empty: with the rule landed, 201 "
         "batter samples across Fresh42 are still written ON seed-submerged "
         "ground because they end below the plane. Both arms are scored on "
         "one conjunction, so no single degenerate answer satisfies them, "
         "and the third arm - the bank CUT to the waterline, the hazard a "
         "re-route genuinely cannot undo - is unchanged in every arm",
         neuter=_neuter_batter_water_refuses_every_sample_over_water,
         neuters="nothing may be written over submerged ground at all - the "
                 "degenerate over-correction, which reads as 'every "
                 "shoreline road ends in a cliff'"),
    Case("a switchback NO profile can join is REFUSED AT PLAN TIME", False,
         _case_an_unjoinable_switchback_is_refused_at_plan_time,
         "MEASURED 2026-09-24 on Fresh42: road step 4 held at 2 of 14 for "
         "three waves, five segments refused at ribbon.py's rim gate (T4 22 "
         "caused, T6 3, T7 7, T9 49, S13 1, worst wall 5.486 m) and 60 of "
         "their 71 caused steps claimed by feet 9-30 m apart along the "
         "polyline -- a switchback's legs beside each other. The refusal key "
         "`|y1 - y2| - BATTER_GRADE * (run1 + run2)` has no ground term, so "
         "it is a fact about the ROUTE, and nothing at plan time asked it. "
         "On this fixture the shipped router emits the line and the rim gate "
         "refuses it with 55 caused steps, worst 6.0 m",
         neuter=_neuter_self_adjacency_never_asked,
         neuters="the shipped router: no plan-time question about the "
                 "line's own branches, so the known-bad U reaches step 4"),
    Case("a switchback a landing CAN join is re-profiled and PLANS", False,
         _case_a_joinable_switchback_gets_its_landing,
         "where a re-profile exists the router takes it: on Fresh42 the "
         "lattice key joins T4, T6, T7, T8, T9, T13, S5 and S13 at plan "
         "time and refuses none. Dropping the bench width from the run -- "
         "measuring it from the centreline -- clears this U as it stands and "
         "the rim gate refuses it with 94 caused steps, worst 3.0 m",
         neuter=_neuter_self_adjacency_run_from_the_centreline,
         neuters="`run_past_each_claims_bench_edge` measured from the "
                 "centreline, a superset of the run that credits the grade "
                 "with the level bench"),
    Case("an ABSORBABLE switchback plans UNCHANGED - the survivor", False,
         _case_an_absorbable_switchback_plans_unchanged,
         "the direction a refuse-everything key passes: both arms above are "
         "satisfied by a router that refuses every line with a turn in it. "
         "Legs 30 m apart are within the slide limit of each other and must "
         "come back CLEAR and byte-identical; MEASURED that the surviving "
         "population is real -- T1, T3, T5 and S7 plan CLEAR and unchanged "
         "on Fresh42",
         neuter=_neuter_self_adjacency_blind_to_height,
         neuters="`design_height_at_both_claiming_feet` dropped: every edge "
                 "between two claims refused whatever the heights"),
    Case("the same switchback keeps its ONE LATTICE EDGE of slide", False,
         _case_an_absorbable_switchback_plans_unchanged,
         "the third declared dependency, proved on the same survivor: the "
         "rim gate allows one 1 m edge of slide between two distinct "
         "samples, and a key without it demands two paved samples of one "
         "graded road stand at exactly one height",
         neuter=_neuter_self_adjacency_no_lattice_edge,
         neuters="`one_lattice_edge_of_slide` dropped from the allowance"),
    Case("a world with NO BUILD can still have its SAVE censused", False,
         _case_an_unbuilt_world_can_still_be_MEASURED,
         "MEASURED 2026-09-23 on the fresh seed Fresh42: `accept --section "
         "roads --world Fresh42 --world-src <its save>` exited 2 in 0.35 s "
         "with 'Fresh42 HAS NO BUILD LEDGER', so accept/instruments.py's "
         "census was never reached and /tmp/accept/Fresh42/<fp>/zdos.tsv "
         "was never written - which is the one input "
         "`roads/precensus.py from_save` has. The remedy the gate printed "
         "was 'build something first', i.e. stage 12, which pipeline.py "
         "declares as REQUIRING that census. Every fresh seed was held "
         "shut by a measurement filed behind a verdict",
         neuter=_neuter_everything_is_a_verdict,
         neuters="the shipped key: the no-build exit applied to every "
                 "invocation, so the LEDGER gates reaching any reading of "
                 "the world including ones that depend on the save alone"),
    Case("an unbuilt world still gets NO VERDICT", True,
         _case_an_unbuilt_world_still_gets_NO_VERDICT,
         "the other direction of the same repair, and the one a fix aimed "
         "at reachability breaks: 'ok=True, advisory=True' would let an "
         "unbuilt world read as a PASS. A save that can be MEASURED must "
         "still not be ACCEPTED - the three sections that once raised "
         "FileNotFoundError here (sections/roads.py:71, "
         "sections/structures.py:380 and :446) must stay unreached",
         neuter=_neuter_nothing_is_a_verdict,
         neuters="the degenerate over-correction: treat every invocation "
                 "as a measurement because something about the world is "
                 "measurable, which opens the no-build exit entirely"),
    Case("a console that CANNOT RUN THE VERBS is not a write session", True,
         _case_a_console_that_cannot_run_the_verbs_is_REFUSED,
         "MEASURED 2026-09-23 on the fresh seed Fresh42, stage 10, zone 1 of "
         "295: the RCON reply was \"Command 'zones_generate pos=-704,-384 "
         "max=32' executed.\" while the game printed \"'zones_generate' is "
         "not valid in the current context.\" Every Upgrade World verb is "
         "registered IsCheat=true (monodis, UpgradeWorld.dll 1.82, "
         "Helper::Command IL_0008 on both overloads) and Terminal.m_cheat "
         "is false in a fresh server process, so a freshly provisioned "
         "world reported 295 zones generated that were never generated",
         neuter=_neuter_no_cheat_precondition,
         neuters="the shipped state: no precondition at all - the session "
                 "opens without asking the console whether it can run the "
                 "verbs the session is about to emit"),
    Case("a console that ALREADY OBEYS is not toggled off", False,
         _case_a_console_that_ALREADY_OBEYS_IS_NOT_TOGGLED,
         "the direction a fix aimed at the fresh server destroys. "
         "`devcommands` is a TOGGLE, not a setting: sending it at every "
         "session open turns cheats OFF on every server that already had "
         "them on - which is every server this project has ever built "
         "with, Ulfsland included - and the next write is then refused by "
         "the game while its RCON echo still reports success",
         neuter=_neuter_devcommands_sent_blind,
         neuters="the degenerate over-correction: send `devcommands` "
                 "unconditionally at session open instead of probing first"),
    Case("a console that DOES NOT KNOW the verb is not a write session", True,
         _case_a_console_that_DOES_NOT_KNOW_THE_VERB_is_REFUSED,
         "THE ELEVENTH INSTANCE OF THE KEY CLASS. `require_cheats` keys a "
         "session's writability on Terminal.m_cheat alone, which is a SUBSET: "
         "TryRunCommand also refuses a verb NO PLUGIN REGISTERED, and the "
         "RCON socket answers `Command '...' executed.` either way. MEASURED "
         "2026-09-23 driving stage 12 on Fresh42: cheats probed ON and "
         "`flatten.py apply` returned rc 0 for 13 of 13 pads while all 33 of "
         "its `spawn_object _TerrainCompiler` commands came back "
         "\"'spawn_object' is not a recognized command.\" -- zero terrain "
         "written, 13 pads recorded as levelled. spawn_object and "
         "deleteObjects come from World_Edit_Commands, which Ulfsland has "
         "among 100 plugins and a fresh server does not have at all",
         neuter=_neuter_no_verb_precondition,
         neuters="the shipped state: cheats are checked and the verbs are "
                 "not, so a missing plugin is discovered by the world "
                 "failing to change"),
    Case("a verb that ANSWERS is not a missing verb", False,
         _case_a_console_that_KNOWS_THE_VERB_is_ACCEPTED,
         "the direction a fix aimed at the fresh server destroys. A "
         "registered verb probed bare prints its own usage or argument "
         "error; calling that a refusal shuts every working server -- "
         "Ulfsland included -- out of its own ledger, which is strictly "
         "worse than the defect, because the defect at least built worlds",
         neuter=_neuter_any_output_is_a_missing_verb,
         neuters="the degenerate over-correction: any console output at all "
                 "counts as an unregistered verb"),
    Case("an UNREAD band is not a clear one", False,
         _case_an_unread_band_is_not_a_clear_one,
         "MEASURED 2026-09-23, `precensus.py from_save` on the fresh seed "
         "Fresh42's frozen unbuilt save: W9-vestvik-lhwest reported '0 "
         "bodies, 0 of 460 band cells censused, 460 in an UNGENERATED "
         "zone' and band_gate answered {'verdict': 'ROOM', "
         "'widest_hard_m': 0.0, 'band_half_needed_m': 12.31}. The census's "
         "own instrument line says an ungenerated zone's emptiness is 'an "
         "absence of reading, not a clear corridor'; the gate beside it "
         "said ROOM",
         neuter=_neuter_band_gate_ignores_coverage,
         neuters="the shipped key: the widest body FOUND and the band "
                 "SURVEYED, with no term for whether the band was read"),
    Case("a READ band still gets its WIDTH verdict", False,
         _case_a_READ_band_still_gets_its_width_verdict,
         "the direction a coverage test destroys if it is added carelessly. "
         "BAND_TOO_NARROW is the verdict that carries resurvey_band_half_m, "
         "the one number a router can act on; a gate that answers "
         "UNCENSUSED for every census has stopped measuring width at all "
         "and reads, wrongly, as the stricter tool",
         neuter=_neuter_band_gate_refuses_every_census,
         neuters="the degenerate over-correction: call every census "
                 "uncensused, which buries the width verdict and its "
                 "re-survey number under a refusal to speak"),
    Case("a world with NO PORTALS is not a broken instrument", False,
         _case_a_world_with_no_portals_is_not_a_broken_instrument,
         "MEASURED 2026-09-23 on the fresh seed Fresh42, which holds 81 "
         "ZDOs and no portals and no guideposts: the harness wrote "
         "zdostr.tsv with zero rows -- the TRUE answer -- and run_harness "
         "raised, so the portals section reported its instrument broken on "
         "a world that simply has none of the thing being counted",
         neuter=_neuter_every_zero_row_artefact_is_a_fault,
         neuters="the shipped key: any secondary artefact with zero rows is "
                 "a failed read, with no term for whether the harness "
                 "reported taking the reading"),
    Case("a pass that never RAN is still a failed read", False,
         _case_a_pass_that_never_RAN_is_still_a_failed_read,
         "the direction the repair destroys if it is written as 'zero rows "
         "is fine'. A lost port, a refused bind and a plugin that never "
         "loaded all produce a zero-row artefact too, and accepting those "
         "is exactly the instrument that cannot tell 'nothing to see' from "
         "'I never looked' - the fault _rows_in's own docstring names",
         neuter=_neuter_every_zero_row_artefact_is_fine,
         neuters="the degenerate over-correction: swallow every zero-row "
                 "refusal, so a reading that never happened is cached as an "
                 "empty world"),
]


register(Section(
    name="roads",
    measures="that a walker can stand on every metre of every built "
             "centreline, step between adjacent metres, reach every named "
             "destination on foot, and is not stopped by a piece standing in "
             "the carriageway",
    instrument="applied bed from the save via run_waterproof.sh FROM_SAVE=1 "
               "(the save's own _TerrainCompiler ZDOs instantiated, then "
               "Heightmap.GetHeight) on a content-hashed COPY; objects from "
               "the whole-save ZDO census; walker capsule MEASURED off the "
               "Player prefab; limit tan(38 deg) from "
               "Character::c_MinSlideDegreesPlayer",
    population="every road segment the ledger records a terrain_write for, "
               "resampled at 1 m; and every non-clearable object within "
               "24 m of one of those centrelines",
    run=run,
    cases=CASES,
))


def required_connection_findings(doc, node_keys, reached):
    """Standing repair IDs cannot erase the solved town/real harbour duties."""
    expected = {"vestvik_to_network", "vestvik_to_harbour"}
    rows = doc.get("required_connections") if isinstance(doc, dict) else None
    if (not isinstance(rows, list)
            or {r.get("id") for r in rows if isinstance(r, dict)} != expected):
        return [Finding(check="north_routes", ok=False,
                        message="Generated Vestvik/network/harbour connection requirements are missing",
                        numbers={"required_connections": len(expected)})]
    findings = []
    for row in rows:
        a, b = row.get("a"), row.get("b")
        present = a in node_keys and b in node_keys
        ok = not row.get("refusal") and present and all(
            node_keys[n] in reached for n in (a, b))
        findings.append(Finding(
            check="north_routes", ok=bool(ok),
            message=f"{row['id']}: actual generated endpoints {a!r} and {b!r} "
                    f"must both be root-reachable; refusal={row.get('refusal')}",
            numbers={"a_present": int(a in node_keys), "b_present": int(b in node_keys),
                     "root_reachable": int(bool(ok)),
                     "producer": row.get("producer")}))
    return findings
