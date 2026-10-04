#!/usr/bin/env python3
"""SETTLEMENTS - a road must ARRIVE on the datum of the thing it meets, and a
body must not be seated on content the world generated.

TWO CHECKS, TWO DEFECT CLASSES, BOTH WITH A LIVE INSTANCE ON THIS WORLD.

  1. ARRIVAL.  A road is not finished where its last station is; it is
     finished where a walker steps off it onto the thing it was built to
     reach.  R2-ulfsgard-harbourb was profiled to the floor of pad
     `sudrvik-fjordhead-12` at y 38.219 and its last station landed 4.0 m
     OUTSIDE that pad, leaving a 1.23 m BERM between the carriageway and
     the village street.  Nothing was wrong with either datum: the road hit
     38.219 and the pad was at 38.219.  What nobody measured was the GROUND
     IN BETWEEN.  A 13th terrace (`sudrvik-roadhead`, 12.0 x 8.0 m at
     (1210, 557), target_y 38.219) closed it.

     The same class, priced elsewhere in this project: a profile solved
     from terrain alone that stopped 10.414 m below Stenvik's floor; a road
     ending 0.648 m above a dock's DECK_DATUM_Y; `step_across_m -7.644`
     sitting unread in a segment's own metadata.  So the check reads the
     APPLIED surface at the terminus, at the destination, and AT EVERY
     METRE BETWEEN THEM, and it judges three things: the datum error, the
     worst 1 m step, and the BERM - how far the ground between rises above
     both ends.  A berm is invisible to any check that compares two
     endpoints, which is why every previous check passed.

     The tolerance is not a taste: it is HALF THE PLAYER'S OWN MEASURED
     CAPSULE RADIUS, the only measured length scale this gate has.  A datum
     out by more than half a body-width is a step the operator sees.

  2. SEATED ON GENERATED CONTENT.  Two village seats on this world were
     proposed on top of a GENERATED village near (1218, 512) and were
     correctly refused.  The check has to find the CLASS, not that instance,
     and the discriminator has to work at world scale without asking a
     human which buildings are ours.

     A piece is GENERATED here when BOTH of these hold:
        it stands inside the reach of a live `LocationProxy` marker (the
        marker's OWN declared radius from the seed's location dump, or the
        world's worst declared radius when the dump cannot identify it),
        AND
        no live ledger record declares a placement of that prefab within
        0.6 m of it.
     BOTH, because either alone is wrong: our own watchtower at wt-north
     has 8 pieces inside an unidentified marker's worst-case radius and
     every one of them is declared, while 3,576 pieces of our own castle at
     sudrberg-keep are inside no marker at all.  MEASURED on Ulfsland: the
     conjunction reports 0 generated pieces inside all 16 declared site
     rectangles, and 55 inside a 20 x 20 m seat centred on the refused
     village - so it separates the two cases this world actually contains.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

from . import _approach as A
from ..registry import Case, Finding, Section, register

# How far from a terminus a destination is still the thing that road meets.
ARRIVAL_REACH_M = 30.0
# The 1 m lattice, the same one the engine's heightmap uses and the same one
# the roads section compares adjacent samples on.
SAMPLE_M = 1.0
# How close a live piece must be to a declared placement to count as
# declared.  `spawn_object` positions are written to 4 dp and the world
# rounds; 0.6 m is well inside one piece's own size and well outside that.
DECLARED_TOL_M = 0.6
# A SINGLE STEP UP, the walker's own height.  A top higher than this above
# what the walker can climb to is not a thing he steps onto; it is a thing
# he looks at.  Used both for the reachability envelope and as the ceiling
# on the standing probe, so the two cannot drift apart.
REACH_UP_M = 1.85
# HOW FAR PAST THE DESTINATION'S OWN EDGE THE WALKER'S FIRST FOOTFALL LANDS.
# A road is finished where a walker STANDS ON the thing it was built to
# reach, and the edge sample is the last metre of the approach, not the
# first metre of the destination.  Four metres is two of this world's 2 m
# floor slabs: far enough to be on the pad, near enough that it is still
# the place the road arrives at.
INSIDE_PROBE_M = 4.0
# How close two termini must be to be THE SAME JUNCTION.  One lattice edge:
# the walker's own step allowance is computed per 1 m edge and two termini
# inside one metre of each other share a vertex, so they are one place.
JUNCTION_TOL_M = 1.0
# WHERE A JOIN STOPS AND AN APPROACH BEGINS - A POPULATION SPLIT, NOT A
# TOLERANCE THAT GROWS WITH DISTANCE.  `terminus_datum` asks a JOIN
# question: is the road AT the height of the thing it meets.  That is the
# right question when the road's last station and the destination share a
# lattice cell - the walker steps straight from one to the other and there
# is no ground in between for anything else to measure.  Over a REAL gap
# it is the wrong question, and it was being asked anyway.
#
# MEASURED on frozen save 9a242873, 61 of 61 lattice vertices, 0 unresolved
# of 44 transitions: R1 B (gap 10.77 m), T8 B and T9 A (7.21 m) were each
# refused by -0.842 m against the wt-town pad while their approaches are
# STRICTLY MONOTONE from 39.608 to 40.450 with max == the pad end - no berm
# by construction - and the worst single 1 m lattice edge on all three is
# 0.4229 m at (815,734)->(815,733), FIFTY-FOUR PER CENT of the walker's own
# 0.7813 m slide limit.  A walker already walks onto that pad on all three
# roads.  The 0.842 m is not a discontinuity: it is a hill spread over 13
# to 18 lattice edges, and the road is on it.
#
# ONE LATTICE EDGE, the same 1 m every other rule in this gate is written
# in.  It is a SPLIT and not a scaled tolerance on purpose: a tolerance
# that grows with the gap is a limit change wearing a population's
# clothes, and it would admit a genuine cliff at 30 m.  Past the split the
# APPROACH tests own the question - `terminus_berm` for the ground between
# and `terminus_step` for every 1 m edge of it INCLUDING THE LAST ONE ONTO
# THE DESTINATION, which is added below precisely so that nothing falls
# between the two populations.
JOIN_GAP_M = SAMPLE_M
# THE WRITER'S OWN NUMBER, not a second copy of it.  `ledger/writer.py`
# refuses a `terrain_write` that moves the ground at a protected point by
# more than this; anything at or below it is float noise from a bilinear
# blend of unwritten samples.  It is also the right floor for "is this
# ground natural", because it is the smallest movement the one sanctioned
# write path will admit happened at all.
FLATTEN_TOLERANCE_M = 0.01

def wire_parser():
    """`ledger/writer.py::parse_spawn_wire` - THE ONE READER OF THIS FORMAT.

    Imported where it is used rather than at module scope, for the reason
    `pad_claims` gives for the same wrapper: `accept/` is importable
    without the ledger on the path, and a declaration index is only ever
    asked for by a run that already has a chain in hand.

    THIS SECTION HELD THE FOURTH OF FOUR INDEPENDENT IMPLEMENTATIONS of
    "what did this record command", and three of the four were wrong the
    same way - an anchored pattern matched per LINE against a
    SEMICOLON-PACKED wire.  MEASURED on 2026-09-21:
      settlements' own index        13,138 -> 24,351 placements (+11,213)
      structures::plan_commands     226 of 421 records mis-sized
      writer::_protected_pieces     21 of 289 blobs short; lh-north read
                                    3 of 64 PROTECTED pieces - 4.7% - and
                                    that set gates every builder's
                                    keep-out and the terrain-write guard
      network/verify_site.py        same ceiling, cosmetic, NAMED not fixed
    A format with a packed form and a line-oriented reader grows a new
    wrong copy every time somebody needs the answer somewhere else, so
    this section keeps no private copy: the pattern, the semicolon split
    and the `command_count` assertion all live in one function and the
    fifth copy cannot drift from it because there is no fifth copy.
    """
    A.instruments()                       # JUMPSTART, and the import guard
    from ..instruments import JUMPSTART                    # noqa: PLC0415
    import sys                                            # noqa: PLC0415
    pth = str(JUMPSTART / "ledger")
    if pth not in sys.path:
        sys.path.insert(0, pth)
    import writer as writermod                            # noqa: PLC0415
    return writermod.parse_spawn_wire


def live_records(ledger_path: Path) -> list[dict]:
    recs = [json.loads(line) for line in
            ledger_path.read_text().splitlines() if line.strip()]
    gone: set[int] = set()
    for r in recs:
        if r.get("op") == "retire":
            gone.update(int(s) for s in (r.get("params", {})
                                         .get("retires") or []))
    return [r for r in recs if r.get("seq") not in gone]


def pad_claims(live: list[dict]) -> list[dict]:
    """`ledger/writer.py::_pad_claims` over the live chain.

    Imported where it is used rather than at module scope, for the reason
    `roads/ribbon.py` gives for the same wrapper: `accept/` is importable
    without the ledger on the path, and a pad population is only ever asked
    for by a run that already has a chain in hand.
    """
    A.instruments()                       # JUMPSTART, and the import guard
    from ..instruments import JUMPSTART                    # noqa: PLC0415
    import sys                                            # noqa: PLC0415
    p = str(JUMPSTART / "ledger")
    if p not in sys.path:
        sys.path.insert(0, p)
    import writer as writermod                            # noqa: PLC0415
    return writermod._pad_claims(live)


def forbidden_extents(live: list[dict], ledger_dir: Path,
                      world: str) -> list[tuple]:
    """Every point of ground a keep-out record OWNS, as
    `(x, z, radius_m, role, seq, kind)`.

    `kind` IS THE SIXTH FIELD AND IT IS NOT DECORATION.  `writer.py` now
    declares two kinds of keep-out and they answer this section's question
    DIFFERENTLY (`writer.KEEP_OUT_KINDS`):

      `FORBIDDEN`  the GROUND is the hazard -- a bridge pile stands on the
                   generated bed of a channel, and cutting it removes water
                   no tool in this tree can put back.  No write closes a
                   refusal here, which is what `1A_FORBIDDEN` means.
      `RESEAT`     the PIECE is the hazard and it is recoverable -- a
                   roadside lamp would be left hanging.  A write closes the
                   refusal by naming the seq in `params.reseats` and
                   re-seating afterwards, so a refusal owned only by these
                   is ACTIONABLE WITH A DEBT, never 1A.

    A five-field tuple from a fixture still reads as `FORBIDDEN`, which is
    the fail-closed direction: a keep-out whose kind nobody stated must
    refuse, never wave through.

    `ledger/writer.py` refuses a `terrain_write` that moves the ground at
    one of these by more than `FLATTEN_TOLERANCE_M` (0.01 m).  So this is
    not an opinion about who owns the ground under a bridge deck, a
    boathouse or a harbour: it is the set the ONE SANCTIONED WRITE PATH
    will refuse over, read through the SAME function the guard itself
    calls.

    AND IT IS `_site_evidence`, NOT `_protected_pieces`.  The guard unions
    every record belonging to the protected SITE - the FORBIDDEN record's
    own pieces plus its siblings' - and a set read one record at a time is
    SMALLER than the set that will refuse.  A smaller set answers
    "repairable" for ground that is owned, which is a guard reporting open
    precisely where it cannot see: the defect class this gate exists to
    find, committed by the check that reports it.  MEASURED on this chain,
    ledger md5 0ebe71d9: the per-record read returns 7,409 points and
    `_site_evidence` returns tens of thousands over the same 397 records,
    and it moves real verdicts - T13 B's nearest owned point reads 1.994 m
    one way and 0.707 m the other, against a 1.333 m footprint, which is
    the difference between REPAIRABLE and REFUSED.

    A record whose pieces cannot be read contributes its DISC, which is
    what the guard falls back to.  A world with no chain yet returns `[]`
    and every terminus is then reported repairable, which is true.
    """
    A.instruments()                       # JUMPSTART, and the import guard
    from ..instruments import JUMPSTART                    # noqa: PLC0415
    import sys                                            # noqa: PLC0415
    p = str(JUMPSTART / "ledger")
    if p not in sys.path:
        sys.path.insert(0, p)
    import writer as writermod                            # noqa: PLC0415
    # A STALE `.pyc` IS A SILENT WRONG ANSWER HERE, NOT AN ERROR.  MEASURED
    # by RoadsClassify: a session that imported `writer` from a stale cache
    # had no `READ_RECORD_WIRE`, `_protected_pieces` then returned None for
    # EVERY deck, and the ownership answer came back clean and inverted -
    # same numbers, opposite verdict.  Fail loudly instead.
    if not hasattr(writermod, "READ_RECORD_WIRE"):
        raise RuntimeError(
            "ledger/writer.py has no READ_RECORD_WIRE: this is a STALE "
            ".pyc, and every protected-piece read against it returns None "
            "- which reads as 'nobody owns this ground'. Re-run with "
            "python3 -X pycache_prefix=<your own dir>.")
    # THE CHAIN THIS RUN IS READING, not a default path.  `--ledger` moves
    # it and `--world-src` does not pin it, so a hard-coded world name here
    # would answer about a DIFFERENT chain than the one every other number
    # in this section came from.
    led = writermod.Ledger.open(world, actor="accept", root=ledger_dir)
    # AND THE SAME STALE-`.pyc` ARGUMENT APPLIES TO THE KIND.  A `writer`
    # without `keep_out_kind` is one where every lamp still reads FORBIDDEN,
    # and this section would then report four N4-mid2-terminus steps
    # unrepairable that the live guard would let through -- the same
    # "same numbers, opposite verdict" failure the line above exists for.
    if not hasattr(writermod, "keep_out_kind"):
        raise RuntimeError(
            "ledger/writer.py has no keep_out_kind: this is a STALE .pyc, "
            "and every keep-out read against it reads as FORBIDDEN - which "
            "reports a repairable step as owned forever. Re-run with "
            "python3 -X pycache_prefix=<your own dir>.")
    out: list[tuple] = []
    for r in live:
        par = r.get("params") or {}
        kind = writermod.keep_out_kind(par)
        if kind is None:
            continue
        role, seq = par.get("role"), r.get("seq")
        pieces, discs = writermod._site_evidence(r, live, led)
        if pieces:
            out += [(px, pz, 0.0, role, seq, kind) for px, pz in pieces]
        else:
            out += [(dx, dz, rad, role, seq, kind) for dx, dz, rad in discs]
    return out


def _station(term: dict, i: int) -> tuple | None:
    """The xz of the `i`-th station on this terminus's walk.

    `profile` is `[hbv.get(v) for v in vertices]`, so the index into one
    IS the index into the other, and a refusal that names a station by
    arc length can name it by coordinate too.
    """
    verts = term.get("vertices") or []
    return verts[i] if 0 <= i < len(verts) else None


def repair_ownership(x: float, z: float, depth_m: float,
                     owners: list[tuple] | None,
                     step_limit: float) -> dict:
    """Could a terrain write close a refusal of this DEPTH here, and if
    not, WHICH RECORD OWNS THE GROUND.

    THE FOOTPRINT IS DERIVED, NOT GUESSED, from two constants this tree
    already owns.  A Lipschitz cone - the one disposal in this tree proved
    not to CAUSE a rim step - lowers a vertex by `d` and must therefore
    touch every vertex out to `d / slide_limit`, because that is where its
    own cone reaches the surface again.  The engine's ground is that 1 m
    lattice interpolated, so moving a vertex changes the ground anywhere
    within `SAMPLE_M` of it.  The write is refused iff an owned point lies
    inside `d / slide_limit + SAMPLE_M`.  AND `d` IS THE OVERAGE, not the
    whole error: what has to move is the part above the limit, and sizing
    a footprint off the whole number refuses repairs that would land.

    WHY A REFUSAL OWES THIS.  Four of this section's nine refusals on gate
    C, save a087af6253a0, name ground that `writer`'s flatten guard will
    not let anyone move - MEASURED, nearest owned point 0.970, 1.413,
    0.970 and 0.707 m against required footprints of 1.394, 2.230, 1.256
    and 1.333 m - and NOTHING IN THE REFUSAL SAID SO.  The other five
    refusals on that save stand 7.4 to 23.0 m clear of any owned point.
    Every reader had to rediscover that split by hand, and the cost is
    paid again on every world this tooling builds.  A refusal that cannot
    state whether any tool can close it is an instruction to go and find
    out.

    `owners=None` means the set was not read, and the answer is then
    UNKNOWN rather than True: a guard reported open when it cannot see is
    the defect this gate keeps finding.
    """
    # THE RIM, NOT THE FOOTPRINT - AND THE RIM IS EIGHT-CONNECTED.
    # MEASURED tonight by HarbourClose, on the live surface, on a cut whose
    # every vertex was unowned AND writer-clear: 17 taken, 116 rim samples,
    # 128 pairs judged, EIGHT steps CAUSED, worst 1.3184 m against 0.7813 -
    # and SEVEN of the eight land on ground owned by road_N1_wnorth_shelf.
    # UNOWNED IS NECESSARY AND NOT SUFFICIENT: a cut takes a cone and the
    # cone lands on the NEIGHBOURS, so the contested population is the
    # owned RIM of the taken set and that set is strictly larger.
    #
    # A cone of depth `d` takes every vertex inside `d / slide_limit`; its
    # rim is the ring of lattice neighbours immediately outside that, and
    # the neighbours include DIAGONALS - the same eight-connectivity this
    # section's own step judge walks, and the same one that made a
    # four-connected cone leave a step it could not see.  So the reach is
    # widened by `SAMPLE_M * sqrt(2)` and not by `SAMPLE_M`: one metre
    # under-covers the diagonal rim by 0.414 m, which is over half the
    # slide limit.
    reach = round(abs(depth_m) / step_limit + SAMPLE_M * math.sqrt(2.0), 3)
    if owners is None:
        return dict(repair_footprint_m=reach,
                    repairable_by_terrain_write=None,
                    ground_owned_by=None,
                    ground_owner_distance_m=None,
                    reseats_required=None)
    # TWO NEAREST OWNERS, NOT ONE, BECAUSE THE TWO KINDS ANSWER DIFFERENT
    # QUESTIONS.  `best` is the nearest keep-out of ANY kind and is what the
    # refusal reports as "the nearest thing standing on this ground".
    # `block` is the nearest keep-out that NO WRITE CAN PASS - and that one
    # alone decides `repairable_by_terrain_write`.
    #
    # MEASURED, Ulfsland save bc484171e3039bc0: without this split, ONE
    # piece_groundtorch at (-907.414, 7713.005) - own footprint 0.233 x
    # 0.237 m, half-diagonal 0.166 m - answered NO for four N4-mid2-terminus
    # stations at 1.087 / 0.414 / 1.078 / 1.155 m, against repair cones of
    # 3.125 / 3.521 / 3.677 / 4.321 m.  The worst of them was a 3.041 m step
    # in a carriageway, declared unrepairable forever by a torch.
    best = block = None
    reseats: dict = {}
    for owner in owners:
        ox, oz, rad, role, seq = owner[:5]
        # A FIVE-FIELD TUPLE MEANS FORBIDDEN, and that is the fail-closed
        # direction: a kind nobody stated must refuse, never wave through.
        kind = owner[5] if len(owner) > 5 else "FORBIDDEN"
        d = math.hypot(ox - x, oz - z) - rad
        if best is None or d < best[0]:
            best = (d, role, seq)
        if kind == "RESEAT":
            if d <= reach:
                prev = reseats.get(seq)
                if prev is None or d < prev[0]:
                    reseats[seq] = (d, role)
            continue
        if block is None or d < block[0]:
            block = (d, role, seq)
    # THE DEBT, NAMED, IN THE ORDER A BUILDER WOULD PAY IT.  `None` when the
    # question was never asked; `[]` when it was and nothing is owed.
    owed = [{"seq": s, "role": reseats[s][1],
             "distance_m": round(reseats[s][0], 3)}
            for s in sorted(reseats, key=lambda s: reseats[s][0])]
    if block is None or block[0] > reach:
        return dict(repair_footprint_m=reach,
                    repairable_by_terrain_write=True,
                    ground_owned_by=None,
                    ground_owner_distance_m=(None if best is None
                                             else round(best[0], 3)),
                    reseats_required=owed)
    return dict(repair_footprint_m=reach,
                repairable_by_terrain_write=False,
                ground_owned_by=f"{block[1]} seq {block[2]}",
                ground_owner_distance_m=round(block[0], 3),
                reseats_required=owed)


def contested_ground(x: float, z: float, reach_m: float,
                     writes) -> dict:
    """WHOSE `terrain_write` ALREADY HOLDS the ground a repair would cut.

    THE SECOND HALF OF "WHO OWNS THIS GROUND", AND THE HALF NO GUARD
    ASKS.  `writer`'s flatten test says whether a cut is ALLOWED and has
    no opinion whatever on whose material it is.  A VERTEX CAN BE WRITABLE
    AND STILL NOT BE YOURS: lowering a carriageway's own embankment back
    toward the seed is a write the guard accepts and a decision nobody
    ever saw.  MEASURED elsewhere in this gate on save 0571968d, over the
    855 cut vertices the harbours draining repairs propose - 812 are
    writer-CLEAR, and all but ONE of those sit inside a road's own
    `terrain_write` footprint.  The single vertex owned by nobody was the
    only one needing no decision from an operator, and it was
    indistinguishable from the other 811.

    Answered off the CHAIN, not off a second height instrument.  Seed bed
    minus applied measures the DEPTH of written material and not WHO wrote
    it, and it costs a patchscan launch per unwarmed tile; the recorded
    footprints answer the better question for nothing and NAME the record.

    `writes=None` means the footprints were not read, and the answer is
    UNKNOWN rather than "unowned": a guard that reports clear when it
    cannot see is the shape this section keeps refusing.
    """
    if writes is None:
        return dict(contested_by=None, contested_within_m=None)
    # The index is exact only out to the margin it was built with, and it
    # RAISES rather than returning a near-miss as a miss - so the query is
    # clamped and the clamp is published beside the answer.
    m = min(float(reach_m), 8.0)
    who = writes.at(x, z, margin=m, exact_only=True) or ""
    return dict(contested_by=(who or None), contested_within_m=round(m, 3))


def _ledger_md5(path) -> str | None:
    """The chain's own digest, for a number that is a function of BOTH the
    save and the chain.  Returns None rather than raising: an unreadable
    ledger is already a refusal elsewhere and this is provenance, not a
    guard."""
    try:
        import hashlib                                    # noqa: PLC0415
        return hashlib.md5(Path(path).read_bytes()).hexdigest()[:16]
    except Exception:                                     # noqa: BLE001
        return None


def _ledger_lines(path) -> int | None:
    try:
        return sum(1 for ln in Path(path).read_text().splitlines()
                   if ln.strip())
    except Exception:                                     # noqa: BLE001
        return None

# THE ONE VOCABULARY.  Three sections found three termination mechanisms
# independently and a fourth found the fifth; a gate whose sections each
# name them differently cannot add its own rows up.  Every section that
# reports a class MUST report one of these, and `termination_class` is
# the only function that produces them.
#
# `1E_OUT_OF_ORDER` IS NOT A SIXTH WAY TO SAY 1C, AND THE DIFFERENCE IS
# THE TOOL INVENTORY.  1C_UNSERVED asserts that NO TOOL IN THIS TREE
# performs the remedy - a statement about this repository, and a false one
# for a crossing whose deck is merely waiting on the road's own terrain
# write.  MEASURED by CrossClass on Ulfsland 7632b3a5674842bd: of the 8
# crossings rows the gate called ACTIONABLE, FIVE are refused by the deck
# builder's own precondition "this segment carries no `terrain_write`" -
# and the remedy for that is `roads/regrade.py` followed by
# `crossings/assemble.bridge`, TWO TOOLS THAT EXIST HERE, in that order.
# Filing them 1C would state that the tree cannot build them, which would
# shrink the work queue by mislabelling it; filing them ACTIONABLE would
# hand a planner a deck it cannot lay today.  The mechanism is an ORDER,
# so it gets its own name and the planner can act on it.
#
# THE PREDICATE, AS RULED, AND IT IS NOT NEGOTIABLE HERE: *A PREREQUISITE
# THIS TREE PERFORMS HAS NOT RUN FOR THIS ROW.*  A row carrying this class
# MUST NAME THE PREREQUISITE - `segment <id> has no terrain_write in the
# chain` - because a class that says "something else must happen first"
# without saying WHAT is an instruction to go and find out, which is the
# shape every field in this file exists to refuse.
#
# AND IT IS COUNTED AS FACTORY WORK, NOT AS AN OPERATOR DECISION.  1A, 1B
# and 1D are all rows a human must decide; 1C is a row nobody can take;
# 1E is a row THE FACTORY OWES AND CAN DISCHARGE BY RUNNING ITS OWN TOOLS
# IN THE RIGHT ORDER.  Rolling it in with the operator classes would move
# five refusals onto a person who can do nothing about them.
#
# `termination_class` does not produce this one off a measurement, because
# there is no measurement of "a prerequisite has not run" that this helper
# can take: the caller that OWNS the builder's preconditions asserts it.
# That is the same contract `1D_OBSTRUCTED` has with the body census.
TERMINATION_CLASSES = ("ACTIONABLE", "1A_FORBIDDEN", "1B_CONTESTED",
                       "1C_UNSERVED", "1D_OBSTRUCTED", "1E_OUT_OF_ORDER",
                       "UNKNOWN")


def _actor_key(name) -> str | None:
    """The RECORD-AGNOSTIC identity of an actor, for comparing a refusal's
    owner with the owner of a `terrain_write` footprint.

    The two arrive in three different spellings of the same thing, all
    MEASURED in one artefact: settlements names its segment
    `road_T9_wttown_dockshore`, roads names the same segment
    `T9-wttown-dockshore`, and `contested_ground` names the record that
    holds the ground `terrain_write road_T9_wttown_dockshore (seq 234)`.
    A comparison done on the raw strings answers "different actor" for
    all three, which is the direction that manufactures an operator
    decision out of a road's own embankment.

    So the wrapper, the sequence and the `road_` role prefix come off and
    the separator is normalised.  Returns None for anything that does not
    reduce to a name, because None must not compare equal to None here -
    see `_contesters_other_than_self`.
    """
    if not isinstance(name, str):
        return None
    s = name.strip()
    # `terrain_write <role> (seq N)` / `<role> seq N`, both spellings in
    # use in this tree - see `repair_ownership` and `harbours.WriteIndex`.
    s = re.sub(r"^\s*terrain_write\s+", "", s)
    s = re.sub(r"\s*\(?\bseq\s+\d+\)?\s*$", "", s)
    s = s.strip().lower().replace("-", "_")
    s = re.sub(r"^road_", "", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or None


def _contesters_other_than_self(nums: dict) -> list:
    """The contesting writes that belong to somebody ELSE.

    `contested_by` is a single record name in roads and settlements and a
    LIST of them in harbours, and either may name the refusal's own
    segment.  The refusal states its own identity in `own_actor` when it
    has one, else in `segment`; a caller that states neither gets every
    contester back, which is the conservative direction - it can only
    leave a row in 1B, never move one out of it.
    """
    got = nums.get("contested_by")
    if not got:
        return []
    lst = list(got) if isinstance(got, (list, tuple, set)) else [got]
    mine = _actor_key(nums.get("own_actor") or nums.get("segment"))
    if mine is None:
        return [c for c in lst if c]
    return [c for c in lst if c and _actor_key(c) != mine]


def _obstructed(nums: dict) -> bool:
    """Is a body of standing pieces intersecting the carriageway.

    Blocker 2, found by RoadsClassify: 54 rows on Ulfsland - 47 distinct
    bodies, 439 must-climb pieces - where the ground is writer-clear and
    uncontested and the road is still obstructed because something is
    STANDING on it.  Not 1A (nobody owns the ground), not 1B (no competing
    write), and NOT 1C: a remedy exists in this tree - demolish or
    re-route - it is merely destructive, so 1C's "no tool performs the
    remedy" is false for it.

    THE GAP IS A SEVERITY, NOT THE PREDICATE, and the first form of this
    function had it the other way round.  MEASURED by RoadsClassify over
    the whole population on save 0571968d: of the 47 bodies, 45 are
    `must_go_round` - a walker-wide gap DOES remain beside them - and
    only 2 are BLOCK (gap 0.0 and 0.85 m).  A predicate of `bodies > 0
    AND gap < walker` therefore classifies 45 of the 54 rows ACTIONABLE,
    and ACTIONABLE asserts all three of: a repair exists, the writer
    permits it, it destroys nothing.  For a 457-piece dvergr town
    standing in the carriageway all three are false.  Squeezing past a
    building in the road and stopping dead at one have the SAME remedy
    set, and neither remedy is a terrain cut - which is the only thing
    1A, 1B, 1C and ACTIONABLE can talk about.  So the gap is published
    beside the class as `obstruction_gap_m` / `obstruction_blocks` and
    decides nothing.

    THE THREE-STATE CONTRACT APPLIES TO THIS FIELD TOO.
    `obstruction_bodies` is None when the obstruction arm never ran and 0
    when it ran and found nothing; None is not 0, and neither is a
    blocker.
    """
    n = nums.get("obstruction_bodies")
    return isinstance(n, int) and not isinstance(n, bool) and n > 0


def repair_direction(nums: dict) -> str | None:
    """WHICH WAY THE GROUND WOULD HAVE TO MOVE - `"cut"`, `"raise"`, or
    `None` when this refusal proposes no repair at all.

    `termination_class` answers WHETHER a repair may happen.  It never asks
    WHICH WAY, and the two are different questions with different consumers:
    every sanctioned terrain instrument in this tree that a refusal row can
    be fed to is CUT-ONLY - `roads/regrade.py` asserts it on every proposed
    delta and `harbours.restoration_set` RAISES on a set holding one fill -
    so an ACTIONABLE row whose remedy is a RAISE routes to a planner that
    must refuse it.

    MEASURED on Ulfsland 245daa7f, the row this exists for:
    `settlements/terminus_datum` road_T13_temple_harboursouth B at
    (12,-258) lands at applied 31.195 m against a wood_stair standing top
    of 31.700 - `error_m -0.505`, THE CARRIAGEWAY IS BELOW ITS
    DESTINATION.  It classified ACTIONABLE, correctly: a repair exists, the
    writer permits it and it destroys nothing.  It is still not a cut, and
    the one number that says so - the SIGN, which `check_arrival` already
    computes and already publishes - was not on the routing path.  The
    checks carry the sign precisely so no consumer has to go back to the
    world for it; this is the field that puts it where a consumer looks.

    THE SIGN IS ONLY UNCUTTABLE WHEN THE HIGH SIDE IS NOT GROUND.  A
    `lattice_edge` has ground at both ends and a cut of the high one always
    exists, whatever the sign - so an edge is `"cut"` either way.  The two
    shapes where the high side is the DESTINATION'S OWN STANDING GEOMETRY
    are the JOIN (`error_m` negative: the road is under the thing it meets)
    and the RISER (`rise_m` positive: `datum - ground`, the destination
    stands above the walk's last footfall).  Neither is closed by moving
    ground down, and the other remedy - retire and re-spawn the piece
    lower - is a building, which this tree does not delete.

    THREE-STATE, like every other field the classifier reads.  `None` here
    means NO REPAIR WAS PROPOSED, which is what `repair_footprint_m == 0.0`
    and `no_cut_proposed` both already say; it never means "nobody looked
    at the sign", because the sign is read off numbers the check either
    published or did not compute at all.
    """
    if nums.get("no_cut_proposed") is True:
        return None
    judged = nums.get("judged_as")
    err = nums.get("error_m")
    rise = nums.get("rise_m")
    if judged == "join" and isinstance(err, (int, float)) and err < 0:
        return "raise"
    if judged == "riser" and isinstance(rise, (int, float)) and rise > 0:
        return "raise"
    fp = nums.get("repair_footprint_m")
    if isinstance(fp, (int, float)) and fp > 0:
        return "cut"
    if nums.get("repair_population") or nums.get("at"):
        return "cut"
    return None


def termination_class(nums: dict) -> str:
    """WHY THIS REFUSAL CAN OR CANNOT BE CLOSED - computed, not curated.

    109 REFUSALS IS NOT A WORK QUEUE.  It is a work queue and an operator
    decision list added together with no way back, and the gate counts
    every member of both the same way.  MEASURED tonight, three distinct
    mechanisms by which a refusal cannot terminate, found independently by
    three sections:

      1A FORBIDDEN  the remedy is forbidden by its own writer.  A deck's
                    own ground is `flatten=FORBIDDEN` by construction, so
                    a refusal that prescribes "write terrain here" on a
                    built crossing is unclosable BY CONSTRUCTION.
      1B CONTESTED  the remedy is permitted and destroys somebody else's
                    work: writer-clear, and inside ANOTHER ACTOR's
                    recorded `terrain_write`.  NO GUARD EXISTS FOR THIS.
                    ANOTHER is load-bearing: a road's own carriageway is
                    always inside its own write, so a 1B that counts the
                    actor's own record swallows every road repair on
                    every world - see `_contesters_other_than_self`.
      1C UNSERVED   the remedy is performed by no tool in this tree, and
                    it has TWO shapes.  NO INSTRUMENT AT ALL: a road
                    arriving at unbuilt forest is closed by BUILDING a
                    destination or RETIRING the segment, and nothing here
                    does either.  NO INSTRUMENT OF THE REQUIRED SIGN:
                    `repair_direction` is `"raise"` and every sanctioned
                    terrain write in this tree is cut-only, so the guard
                    permitting the ground to move buys nothing - there is
                    nobody to move it.
      1D OBSTRUCTED the ground is writer-clear and uncontested and a
                    walker-blocking BODY of standing pieces intersects
                    the carriageway, so the remedy is demolition or
                    re-route rather than a terrain cut.  A remedy DOES
                    exist in this tree, which is what separates it from
                    1C - see `_obstructed`.

    ACTIONABLE is the residue, and it is the only number that answers the
    question the factory actually has: is there work left.

    THE CALLER'S CONTRACT, AND IT IS THREE-STATE ON EVERY FIELD.  This
    function decides "the question was asked" from the PRESENCE of
    `repair_footprint_m` and `contested_within_m`, never from the answers,
    because every answer field is `None` in BOTH of its interesting cases:
    `ground_owned_by` is None when nobody owns the ground AND when the
    owner set was unreadable, and `contested_by` likewise.  So a caller
    MUST distinguish:

      owned / held    the answer field NAMES the record
      asked, clear    the answer field is None, the reach field is a NUMBER
      never asked     BOTH are None

    A REFUSAL WITH NO PROPOSED REPAIR HAS A REPAIR FOOTPRINT OF 0.0, NOT
    OF NOTHING.  0.0 says the question was asked and a repair would need
    no ground; None says nobody looked.  MEASURED by HarbourClose one
    branch over from the defect above: passing `None` for the footprint on
    its no-remedy rows turned SIX correct 1C_UNSERVED rows into UNKNOWN -
    absence read as absence, but of the wrong thing, immediately after the
    same caller had read absence of a remedy as presence of an owner.
    `None` MAY NEVER STAND FOR AN ANSWER in either direction.
    """
    # A BLOCKER THIS WORLD'S LEDGER PLACED IS NOT AN OPERATOR DECISION.
    # 1B, 1C and 1D send a row to a person because the thing in the way
    # may be a player's or the seed's; `termination.own_blockers` asks the
    # ledger, and when EVERY blocker the row names is this factory's own
    # piece or claim it stamps `blocker_ownership="ours"`.  The operator
    # class the terrain/body cascade would give is then ACTIONABLE - the
    # step that placed the blocker owns the repair.  Every other class (1A,
    # UNKNOWN, ACTIONABLE) is untouched: this answers WHOSE the blocker is,
    # nothing else.  MEASURED on drive 3's save 05eb7c6c05de: 49 refusals,
    # ACTIONABLE 0, most of them naming our own decks, boards and beams.
    if nums.get("blocker_ownership") == "ours":
        under = termination_class(dict(nums, blocker_ownership=None))
        return ("ACTIONABLE" if under in ("1B_CONTESTED", "1C_UNSERVED",
                                          "1D_OBSTRUCTED") else under)
    # 1D IS FIRST, AND IT IS FIRST BECAUSE IT ANSWERS A DIFFERENT
    # QUESTION.  1A, 1B, 1C and ACTIONABLE are four answers to ONE
    # question - what does the writer say about the cut this refusal
    # prescribes - and they are all read off the terrain instruments.  1D
    # is a positive measurement from the BODY CENSUS: pieces are standing
    # in the carriageway.  No answer to the terrain question changes that
    # remedy set, so the body is not a tiebreak among the terrain classes,
    # it is PRIOR to them.  MEASURED by RoadsClassify, and this is why the
    # order is forced rather than chosen: an obstruction row's honest
    # terrain answer is `repairable_by_terrain_write=False` with
    # `repair_footprint_m=0.0` and no owner - the question WAS asked, and
    # a cut would need no ground because no cut removes a building - so
    # with 1D anywhere below it the 1C branch fires first and all 54 rows
    # read 1C_UNSERVED, "no tool in this tree performs the remedy", when
    # two tools do.  The only ways to dodge that from the caller are to
    # leave the ownership fields None after looking, or to write
    # `repairable=True` on a row no write closes; both are lies about an
    # instrument, which is the class this gate exists to refuse.  A row
    # that is BOTH owned by a FORBIDDEN record AND has a town standing on
    # it is 1D, because demolish-or-reroute is what the operator is
    # actually deciding there.  Agreed with RoadsTerm on the wire and
    # landed here so the vocabulary does not fork.
    if _obstructed(nums):
        return "1D_OBSTRUCTED"
    # 1A MUST NAME THE SITE THAT OWNS THE GROUND, OR IT IS NOT 1A.
    # MEASURED by HarbourClose on its own first classifier: SIX of eleven
    # harbours refusals came back 1A_FORBIDDEN with `ground_owned_by` None
    # on every one of them, because a refusal with no cut-only restoration
    # has no repair to ask an ownership question ABOUT and the caller had
    # set `repairable_by_terrain_write = False` for "there is nothing to
    # repair".  False means ASKED AND OWNED; the truth was THE QUESTION
    # WAS NEVER ASKED.  ABSENCE OF A REMEDY READ AS PRESENCE OF AN OWNER -
    # the same sentence as unread-read-as-clear, one branch over.  So the
    # class is derived from the ANSWER, not from the flag: no named owner
    # means no 1A, whatever the flag says.
    if (nums.get("repairable_by_terrain_write") is False
            and nums.get("ground_owned_by")):
        return "1A_FORBIDDEN"
    if nums.get("repairable_by_terrain_write") is False:
        return ("1C_UNSERVED" if nums.get("repair_footprint_m") is not None
                else "UNKNOWN")
    # 1C BY DIRECTION: THE WRITER PERMITS IT AND THERE IS NO WRITER.
    #
    # THE DECISION, STATED PLAINLY.  ACTIONABLE has three clauses - a
    # repair exists, THE WRITER PERMITS IT, and it destroys nothing - and
    # until here this function read the second one and inferred the first
    # from it.  `repairable_by_terrain_write` answers "would
    # `Ledger.append`'s flatten guard let a write land on this ground".
    # It has NO OPINION on whether any instrument in this tree emits a
    # write of the required SIGN, and for a RAISE the answer is that none
    # does:
    #
    #   roads/regrade.py         asserts cut-only on every proposed delta
    #   roads/cone.py            refuses a `repair_direction == "raise"`
    #                            row at the top, by this very field
    #   harbours.restoration_set RAISES on a set holding one fill
    #
    # and the other remedy - retire the piece and re-spawn it lower - is a
    # BUILDING, which this tree does not delete.  "The remedy is performed
    # by no tool in this tree" is, word for word, the definition of
    # 1C_UNSERVED, so that is what a raise is.  It was never ACTIONABLE;
    # it was a permitted write nobody can issue, sitting on the work queue
    # as if a planner could take it.
    #
    # MEASURED, Ulfsland save 7632b3a5674842bd, artefact
    # /tmp/gate_final2.json: `settlements/terminus_datum`
    # road_T13_temple_harboursouth B at (12,-258), applied 31.195 m under
    # a wood_stair standing top of 31.700 - `error_m -0.505`,
    # `repair_direction "raise"`, `repairable_by_terrain_write true`,
    # published ACTIONABLE.  It is the ONLY `raise` on a board of 105
    # refusals and it was one of the twelve rows the factory called work.
    #
    # AND THIS IS THE SAME DEFECT CLASS THE REST OF THE SESSION KEEPS
    # FINDING: A KEY THAT NAMES A SUBSET OF WHAT THE QUANTITY DEPENDS ON.
    # "Is there work here" depends on PERMISSION and on the EXISTENCE OF
    # AN INSTRUMENT THAT WRITES IN THE REQUIRED DIRECTION; the classifier
    # keyed on permission alone, and the missing half was already computed,
    # already published, and sitting one field away in the same dict.
    #
    # NO THREE-STATE GUARD HERE, DELIBERATELY, AND THE REASON IS WHAT THE
    # QUESTION IS ABOUT.  Every other branch of this cascade guards on
    # "was the question asked", because every other branch reads an
    # instrument pointed AT THE WORLD and an instrument can fail to see.
    # This one reads the TOOL INVENTORY OF THIS REPOSITORY, which is the
    # same on every save and cannot be unreadable: `repair_direction`
    # returns None when no repair was proposed at all, and `"raise"` is
    # only ever returned off a sign the check MEASURED.  Falling to
    # UNKNOWN when the ownership question went unanswered would make the
    # class of a row depend on a reading that cannot change it.
    if repair_direction(nums) == "raise":
        return "1C_UNSERVED"
    # SELF-CONTEST IS NOT CONTEST, AND UNCORRECTED IT SWALLOWS EVERY ROAD
    # ON EVERY WORLD.  MEASURED on Ulfsland 245daa7f, full gate: 6 of the
    # 13 1B_CONTESTED rows named THEIR OWN SEGMENT as the contester -
    # settlements road_N0_brgs2_wmid (seq 2629), road_S12_brgs1_boathouse
    # (seq 342), road_S1_portalhub_brgs2 (seq 417),
    # road_T13_temple_harboursouth (seq 338), road_T9_wttown_dockshore
    # (seq 234), and roads T9-wttown-dockshore-sb01 against the same
    # seq 234.  A road repairing its own carriageway destroys nobody's
    # work, and a road's own carriageway is ALWAYS inside its own
    # `terrain_write`, so a 1B that does not exclude the actor's own
    # record reclassifies every road repair on every world as an operator
    # decision.  The seventh settlements row - S12 terminus A against
    # `regrade_oc_boathouse_channel11` (seq 5393) - and all four harbours
    # rows name a DIFFERENT actor and are genuine.
    others = _contesters_other_than_self(nums)
    # 1B IS A CONJUNCTION AND BOTH HALVES MUST HAVE BEEN ASKED.  "the
    # write is PERMITTED and lands inside another actor's footprint" -
    # the permitted half is `repairable_by_terrain_write`, and when that
    # is None nobody asked the writer.  MEASURED on the same run: EVERY
    # settlements refusal carried `repairable_by_terrain_write: null`
    # because `run()` asked `ctx.world` of a Ctx that only has
    # `ctx.args.world`, and the gate reported six confident 1B rows off
    # half an answer.  AN UNASKED QUESTION MAY NEVER BE REPORTED AS AN
    # ANSWER, so the unanswered half falls to UNKNOWN below rather than
    # returning here.
    if others and nums.get("repairable_by_terrain_write") is True:
        return "1B_CONTESTED"
    # UNREAD IS NOT CLEAR.  `contested_by` is None both when nobody
    # holds the ground and when the footprints could not be read; only
    # `contested_within_m` separates the two, and a class that conflated
    # them would report ACTIONABLE precisely when it cannot see.
    if (nums.get("repairable_by_terrain_write") is None
            or nums.get("contested_within_m") is None):
        return "UNKNOWN"
    return "ACTIONABLE"


# EVERY REFUSAL THIS SECTION CAN EMIT, NAMED - AND THIS SECTION HAS REAL
# HOLES, WHICH IS WHY THE TABLE IS LIVE RATHER THAN DECORATIVE.
#
# This section classifies per ROW, inside `check_arrival`'s `owned()`, and
# that helper RETURNS AN EMPTY DICT when the flatten=FORBIDDEN extents
# could not be read (`owners == "unread"`).  So every terminus refusal on a
# run whose owner set is unreadable reaches the board WITH NO CLASS - and
# six of this section's ten emittable checks never pass through `owned()`
# at all.  On Ulfsland the owner set reads and those six do not fire, so
# the board showed `NO_CLASS 0` and nobody could tell that from coverage.
#
# `termination.backstop` fills exactly those rows and re-decides none: a
# refusal that already carries a class keeps it, which is why landing this
# moves no row on Ulfsland.
#
# AND THE CLASSES HERE ARE NOT ABOUT GROUND.  An unbuilt population, an
# unreadable location dump and a short declaration index are refusals about
# WHAT WAS MEASURED, not about who owns a cut.
#
# AND NOT ONE ENTRY HERE IS `NOT_MEASURED`, because that family's class is
# UNKNOWN and UNKNOWN is `NO_CLASS` spelled again.  MEASURED on drive 2's
# frozen Fresh42 save: the board's only two UNKNOWN rows were this section's
# `terminus_unmeasured`, named by this table, green on the coverage audit,
# and meaning nothing a reader could act on.  Every instrument this section
# reads through is a tool in this tree, so "it did not answer" has a class:
# 1E when the reading is simply not taken yet and the prerequisite can be
# named, 1C when no run of any tool changes the answer.
_ARRIVAL_UNREAD = (
    "CENSUS_NOT_TAKEN",
    "read the flatten=FORBIDDEN extents and the recorded terrain_write "
    "footprints off this world's chain: check_arrival classifies from them "
    "in place, and a row arriving with no class was judged without them")


def _unmeasured_family(nums: dict) -> tuple[str, str]:
    """`terminus_unmeasured`'s remedy, off the numbers the row carries - see
    `unstood_destination` for the three states and the measurement."""
    if nums.get("unread_vertices"):
        return ("CENSUS_NOT_TAKEN",
                "the ground instrument (accept ground() -> terraform/"
                "run_waterproof.sh FROM_SAVE=1) must read the {} lattice "
                "vertex/vertices inside {} it returned nothing for, first "
                "{}".format(nums["unread_vertices"], nums.get("destination"),
                            (nums.get("unread_at") or [None])[0]))
    if nums.get("inside_vertices") == 0:
        return ("SOURCE_CHANGE",
                "the destination's footprint holds no lattice vertex on the "
                "walk to its centre, so no ground in it can be read; closed "
                "by a change to its declared rectangle")
    return ("NO_DESTINATION",
            "nothing declares a datum, nothing reachable is standing and no "
            "other road meets this terminus; closed by BUILDING a "
            "destination or RETIRING the segment, and this tree does "
            "neither")


def _refused_family(nums: dict) -> tuple[str, str]:
    """`terminus_destination_refused`'s remedy (PLANNED_END_DEPENDS_ON): a
    planned leg here the build never attempted is a stage not run, 1E
    naming it; every cause a RECORDED refusal is 1C - re-running the build
    reproduces each, so each is closed in its own unit's source."""
    unbuilt = [p["segment"] for p in nums.get("partners") or []
               if not p.get("refusals")]
    if unbuilt:
        return ("UPSTREAM_STAGE_NOT_RUN",
                "build (buildstage road_ground) the planned leg(s) {} that "
                "end at this terminus and are not in the ledger".format(
                    ", ".join(unbuilt)))
    return ("SOURCE_CHANGE",
            "{}; re-running the build reproduces each refusal, so each is "
            "closed in its own unit's source".format(nums.get("owner")))


def _short_family(nums: dict) -> tuple[str, str]:
    """`terminus_short_of_destination`'s remedy: the walk to the pad READ
    says whose the gap is (PLANNED_END_DEPENDS_ON)."""
    if not nums.get("wet_vertices") and nums.get("unread_vertices"):
        return ("CENSUS_NOT_TAKEN",
                "the ground instrument (accept ground() -> terraform/"
                "run_waterproof.sh FROM_SAVE=1) must read the {} of {} "
                "vertices of the walk to {} it returned nothing for".format(
                    nums["unread_vertices"], nums.get("walk_vertices"),
                    nums.get("destination")))
    return ("SOURCE_CHANGE", str(nums.get("owner")))


TERMINATION_FAMILIES = {
    "terminus_datum": _ARRIVAL_UNREAD,
    "terminus_berm": _ARRIVAL_UNREAD,
    "terminus_step": _ARRIVAL_UNREAD,
    "terminus_step_unresolved": lambda nums: (
        "CENSUS_NOT_TAKEN",
        "the ground instrument (accept ground() -> terraform/"
        "run_waterproof.sh FROM_SAVE=1) must read the {} lattice "
        "vertex/vertices of {} transition(s) on this approach it returned "
        "nothing for".format(nums.get("unresolved"),
                             nums.get("transitions_judged"))),
    "terminus_unmeasured": _unmeasured_family,
    "terminus_destination_refused": _refused_family,
    "terminus_short_of_destination": _short_family,
    "terminus_obstructed": (
        "BODY_IN_THE_WAY",
        "every lattice vertex the walk took inside the destination was read "
        "and is occupied by a walker-blocking body; the road arrives at a "
        "building, not at a place to stand"),
    "destination_datum_unstood": lambda nums: (
        "SOURCE_CHANGE",
        "the PAD's own write did not level its rectangle to the number it "
        "declared - the site stage's pad solve must change, and re-running "
        "it reproduces the same pad"
        if nums.get("repair") == "pad" else
        "the DECLARED datum is a number nobody stands on - the site "
        "declaration must change, and no tool in this tree edits one"),
    "seat_on_generated": (
        "NO_DESTINATION",
        "a pad seated on generated content is closed by RE-SITING the site "
        "or by accepting the overwrite; this tree does not move a declared "
        "site from a refusal row and never deletes generated content"),
    "population": (
        "UPSTREAM_STAGE_NOT_RUN",
        "no segment, no terminus within reach of a declared pad, or no pad "
        "declaring its own centre - the prerequisite is the BUILD stage "
        "that writes them, and a verdict over an empty population is not a "
        "verdict"),
    "instrument": lambda nums: (
        ("CENSUS_NOT_TAKEN",
         f"re-run tools/seedscan/run_locscan.sh to produce the seed's "
         f"location dump at {nums.get('dump')}; a world whose generated "
         f"content cannot be located is unmeasured, not clean")
        if nums.get("dump") else
        ("CENSUS_NOT_TAKEN",
         f"the ground instrument (accept ground() -> terraform/"
         f"run_waterproof.sh FROM_SAVE=1) refused for the "
         f"{nums.get('points')} arrival samples this run; it must read "
         f"them before any arrival can be judged")),
    "declaration_index": (
        "SOURCE_CHANGE",
        "spawn_plan records whose blobs do not read back at their own "
        "command_count were written inconsistent by their producer, and no "
        "tool in this tree rewrites a ledger record"),
}


def declared_pads(live: list[dict]) -> list[dict]:
    """Every live site pad, with the rectangle it CLAIMED and the datum its
    own write ASKED FOR.

    RESOLVED THROUGH `writer._pad_claims`, NOT OFF `params.centre`, and the
    difference is three quarters of this world's destinations.  MEASURED on
    this chain: 66 live `site_pad` terrain_writes, of which only 16 carry a
    `centre` at all.  The first version of this function judged those 16
    and put the other 50 in a `pads_without_centre` count nobody could act
    on, so 11 of the 50 road termini that DO meet a declared pad were
    measured against the nearest standing thing within 30 m instead - or,
    where nothing was standing, against nothing at all and reported
    UNMEASURED.  `_pad_claims` is the same authority the write-time
    footprint guard and the road rasteriser resolve a pad with, so this
    section cannot disagree with either; its `hw`/`hd` already carry the
    apron.  The 16 that do carry a `centre` agree with it to within 0.05 m,
    so nothing this section had already measured moves.

    `datum` here is a DECLARATION - the height that write ASKED the ground
    to become - and is NEVER the height a road is judged to arrive on.  See
    `standing_datum`: the datum a terminus is judged against is read from
    the destination's own standing geometry.  A declaration that disagrees
    with what is standing is itself a finding.
    """
    target_of = {r.get("seq"): (r.get("params") or {}).get("target_y")
                 for r in live if r.get("op") == "terrain_write"}
    out, no_datum = [], 0
    for q in pad_claims(live):
        y = target_of.get(q["seq"])
        if y is None:
            no_datum += 1
            continue
        out.append(dict(name=q["name"], site=q["site_id"],
                        cx=float(q["x"]), cz=float(q["z"]),
                        hw=float(q["hw"]), hd=float(q["hd"]),
                        datum=float(y), seq=q["seq"]))
    for o in out:
        o["pads_without_centre"] = no_datum
    return out


def spawns_on(line: str) -> list[tuple]:
    """Every `spawn_object` command on ONE wire or blob line: (prefab, x, z).

    A thin name for the shared reader, kept because this section's own
    selftest cases and neuters address it - the parsing itself is
    `writer.parse_spawn_wire` and nothing here re-implements it.
    """
    return wire_parser()(line) or []


def record_placements(r: dict, blobs: Path) -> list[tuple]:
    """What ONE record commanded: the BLOB when it has one, else the wire.

    THE BLOB IS THE AUTHORITATIVE COMMAND LIST AND THE WIRE IS A SUMMARY
    OF IT, and unioning the two double-counts.  MEASURED, and this is how
    the accounting identity below was made to hold: reading wire AND blob
    made 159 of 377 live `spawn_plan` records read OVER their own
    `command_count` (typically by exactly the one line the wire repeats),
    and keying an index on (prefab, x, z) with no y made 99 read SHORT -
    lh-south declares 1,255 commands and has only 517 distinct xz,
    because a blueprint stacks wall courses at one footprint.  Neither
    number was a defect in the chain; both were the reader asking a
    question the chain does not answer.
    """
    shas = [s for s in (r.get("requires", {}).get("blobs") or [])
            if (blobs / s).exists()]
    if shas:
        out: list[tuple] = []
        for sha in shas:
            out += spawns_on((blobs / sha).read_text(errors="replace"))
        return out
    return spawns_on("\n".join(str(w) for w in (r.get("wire") or [])))


def placement_accounting(live: list[dict], blobs: Path) -> dict:
    """Does every plan's own command list read back at its declared size?

    THE HALF THAT MATTERS.  Splitting a packed line makes the index see
    more; this makes a SHORT READ LOUD.  A blob that parses to fewer
    commands than its record declares is UNREADABLE, not small - and a
    quietly short declaration index is what called 3,417 pieces of our
    own buildings generated content and refused five sudrvik pads.

    MEASURED on this chain after the packed-line fix: 377 of 377 live
    `spawn_plan` records parse to EXACTLY their own `command_count`, 0
    short and 0 over.  The identity holds, so a future world inherits a
    reader that can prove it read everything rather than one that hopes.
    """
    exact, short, over = 0, [], []
    for r in live:
        if r.get("op") != "spawn_plan":
            continue
        want = int((r.get("params") or {}).get("command_count") or 0)
        got = len(record_placements(r, blobs))
        if got == want:
            exact += 1
        else:
            (short if got < want else over).append(
                dict(seq=r.get("seq"),
                     site=(r.get("params") or {}).get("site_id"),
                     declared=want, parsed=got))
    return dict(exact=exact, short=short, over=over,
                plans=exact + len(short) + len(over))


def declared_placements(live: list[dict], blobs: Path) -> list[tuple]:
    """(prefab, x, z) for every placement this project's own record claims.

    Read from the spawn-plan BLOB where a record has one and from its
    `wire` lines where it does not, because a body's pieces live in one or
    the other depending on which builder emitted it, and a declaration
    index that sees only one of them calls our own castle foreign.

    AND EVERY COMMAND ON EACH LINE.  This index used a `^`-anchored
    `match`, so on a SEMICOLON-PACKED line it took the first command and
    dropped the rest - 226 of 421 `spawn_plan` records on this chain have
    `len(wire) != command_count` for exactly that reason.  MEASURED: the
    index resolved 13,138 placements where a packed-aware scan resolves
    24,351 (+11,213), `fb_*` whole-house prefabs went from 9 to 25, and
    five sudrvik pads stopped being refused as seated on generated
    content.  `placement_accounting` is the guard that keeps it honest.
    """
    out: list[tuple] = []
    for r in live:
        out += record_placements(r, blobs)
    return out


def rect_distance_m(px: float, pz: float, cx: float, cz: float,
                    hw: float, hd: float) -> float:
    """Distance from a point to an axis-aligned rectangle; 0.0 inside it.
    The same arithmetic `terraform/locations.py` uses, and it is imported
    from there rather than re-derived when that module is importable."""
    return math.hypot(max(abs(px - cx) - hw, 0.0),
                      max(abs(pz - cz) - hd, 0.0))


# ---------------------------------------------------------------------------
# THE GENERAL FIX - A TERMINUS DATUM IS READ FROM THE DESTINATION'S OWN
# STANDING GEOMETRY, NEVER FROM A SITE FILE AND NEVER FROM A PLAN
# ---------------------------------------------------------------------------
def standing_datum(inside: list[tuple[float, float]], hbv: dict, grid,
                   walker_h: float, bounds_only: set | frozenset = frozenset(),
                   geom: dict | None = None, why: dict | None = None
                   ) -> tuple[float | None, str | None, tuple | None]:
    """The height of the thing that IS STANDING where the walker's first
    footfall on the destination lands: `(y, what, at_xz)`.

    WHY THIS EXISTS, with the number.  `settlements/sites.yaml` publishes
    Vestvik's `pad_y` as 33.99.  That is the DISTRICT figure; no ground in
    the village is at it.  The u-vestvik portal_wood stands at
    (-915.0, 35.29, 1883.4) on vestvik-hall-1's levelled pad and the
    save-derived applied ground under it reads 35.2900 against a generated
    36.6006 - the pad's own -1.3106 m cut is on the world.  33.99 is
    1.30 m LOW, and a road built to it arrives at ground nobody stands on.
    The pad's own `terrain_write` `target_y` is no better in kind: it is
    what that write ASKED the ground to become, and the ground is what the
    engine then did.  Both are PLANS.  Only a reading is not.

    So the datum is `_approach.standing_surface` - the applied bed, or the
    highest walkable top covering that xz - taken at the first sample
    INSIDE the destination, which is where a walker's weight first lands.

    THE CEILING IS ONE WALKER-HEIGHT ABOVE THAT SAMPLE'S OWN BED, and the
    selftest is what settled it: the first version ceilinged the probe at
    the walker's reach ACROSS THE GAP - `road_y + gap * slide_limit +
    1.85`, which over an 8.3 m gap is 42.33 m - and a 41.29 m ROOF over
    the pad passed it and became the datum a road would have been judged
    against.  A walker standing on a pad steps onto what is at most his
    own height above the ground under his feet; anything higher is a thing
    he looks at.  The bound is local for the same reason the whole class
    exists - it must not be borrowed from the artefact under test, and
    `road_y` is the road.

    A ROAD DOES NOT ARRIVE ON A WALL, and the live run proved this line has
    to be here.  `standing_surface` offers the highest LEVEL face covering
    the sample, and a face is not a place.  At wt-south both S8 and T10
    terminate INSIDE the pad, in the watchtower's own column: floor top
    82.660, then `stone_wall_2x1` 82.660-83.660, `stone_wall_1x1`
    83.660-84.660, `stone_pillar` 83.660-85.660, and so on to a roof at
    91.745.  The wall's cap at 83.660 is a level face 1.000 m above the
    floor, well inside the walker's reach and 2.0 m2 - it passes an area
    floor - so it was taken as the datum, and two CORRECT arrivals
    (road 82.660 against a declaration of 82.660) were reported as 1.000 m
    misses with their pad's declaration 1.000 m "unstood".

    So the test is OCCUPANCY, not area: a candidate top is a datum only if
    a walker's own capsule FITS ABOVE IT, judged with `blocks_at_height` -
    the same predicate the approach probe uses for a wall.  At 83.660 the
    `stone_wall_1x1` directly above blocks it; at 82.660 the
    `stone_wall_2x1` blocks that too, so this sample is not a place a
    walker stands AT ALL and the probe walks on to the next one.  That is
    the honest reading: a road whose whole probe is inside a building has
    no arrival to measure, and it says so rather than inventing one.

    AND A BOUNDING BOX MUST NOT DECIDE THE WORLD.  58 % of this corpus's
    non-render colliders (6,956 of 11,972) are `MeshCollider.sharedMesh
    .bounds` - an UPPER BOUND on a shape, not the shape.  `Beech1` is two
    `mesh` rows and no capsule, so a birch is carried as a 19.08 x 21.50 m
    box 31.7 m tall, and MEASURED at S7's terminus (152.0, 1194.0) two
    Beech1 boxes spanning y 30.92-63.48 cover every sample of the probe.
    Vetoing on that is a tree refusing a road from 20 m away.  So a blocker
    whose prefab is known ONLY to bounding-box precision does not veto; it
    is NAMED in the answer, and the caller can say the verdict rests on a
    bound rather than on a shape.  Same ruling `GateHarbours` made for
    `rock4_coast`, tested by re-run rather than by a reason string.

    AND THE FACE IS READ IN THE COLUMN THE FOOTFALL LANDS IN.  With `geom`
    the height is `instruments.arrival_face` at this very sample rather
    than the piece's whole-solid `level_top()`: a piece that SPANS height -
    a stair, a ramp - has a different walkable face at each column, and its
    summit is not what a walker standing here is standing on.

    AND WHEN IT FINDS NOTHING IT SAYS WHY, in `why` when the caller passes
    one.  "Nothing standing could be measured" is three different states
    with three different remedies, and the refusal that printed "the
    applied surface ... was not read" over all three was an instrument
    failure reported as a finding about the world - MEASURED on drive 2,
    where all four vertices HAD been read, at exactly the declared 77.650:

      `unread`   inside vertices the ground instrument returned nothing
                 for - the reading was never taken;
      `blocked`  inside vertices that WERE read and on which every top,
                 ground included, had a walker-blocking solid in the way -
                 with the solids that blocked it, so a body is counted,
                 not inferred;
      `inside`   how many vertices of the walk were inside the destination
                 at all.
    """
    rec = why if why is not None else {}
    rec.update(inside=len(inside or []), unread=[], blocked=[],
               blocking_bodies={}, blocking_pieces={}, datum_piece=None)

    def _ref(s) -> dict | None:
        # PREFAB AND PIVOT, so whose piece it is can be read off the ledger
        # (`termination.piece_ref`'s shape).  A solid with no pivot cannot
        # be attributed, and says so by being None.
        p = getattr(s, "pos", None)
        return None if p is None else dict(
            prefab=s.name, x=round(float(p[0]), 4), y=round(float(p[1]), 4),
            z=round(float(p[2]), 4))
    for (vx, vz) in (inside or []):
        bed = hbv.get((vx, vz))
        if bed is None:
            rec["unread"].append((vx, vz))
            continue
        here = [s for s in grid.near(vx, vz, 0.0)
                if s.verts and s.covers(vx, vz)]
        tops: list[tuple[float, object | None]] = [(bed, None)]
        for s in here:
            t = (s.level_top() if geom is None
                 else A.instruments().arrival_face(s, geom, vx, vz)[0])
            if t is None or t < bed - 0.01 or t > bed + REACH_UP_M:
                continue
            if _hull_area_m2(s) < MIN_DESTINATION_AREA_M2:
                continue
            if s.name in bounds_only:
                continue          # a bound is not a face to stand on either
            tops.append((t, s))
        vetoed: dict[int, str] = {}
        for t, who in sorted(tops, key=lambda p: -p[0]):
            hard, soft = [], []
            for s in here:
                if s is who or not A.blocks_at_height(s, t, walker_h, 0.05):
                    continue
                (soft if s.name in bounds_only else hard).append(s)
            if hard:
                vetoed.update((id(s), s.name) for s in hard)
                rec["blocking_pieces"].update(
                    (id(s), r) for s in hard if (r := _ref(s)) is not None)
                continue
            what = ("the applied ground" if who is None
                    else f"the standing top of {who.name}")
            if soft:
                what += (" (clear only if {} is its bounding box rather "
                         "than its shape)".format(
                             sorted({s.name for s in soft})[0]))
            # THE PIECE WHOSE TOP IS THE DATUM, named, because a riser onto
            # it is blocked by THAT piece and its owner is a ledger reading.
            # MEASURED on drive 3: S5 terminus B steps +11.006 m onto "the
            # standing top of stone_wall_2x1" and nothing said whose wall.
            rec["datum_piece"] = _ref(who) if who is not None else None
            return t, what, (vx, vz)
        rec["blocked"].append(((vx, vz), sorted(set(vetoed.values()))))
        rec["blocking_bodies"].update(vetoed)
    return None, None, None


def _last_footfall(verts: list[tuple[float, float]], hbv: dict
                   ) -> tuple[tuple[float, float], float | None]:
    """The ground the walker LAST STANDS ON before he steps onto the
    destination: `(vertex, height)`, and the height is `None` when that
    vertex was never read.

    IT IS THE LAST VERTEX OR IT IS NOTHING.  The version this replaces
    scanned `reversed(verts)` for the first vertex that HAD been read and
    published `verts[-1]` as the place it had read - so a walk whose tail
    was unread measured "the step a walker takes last" from wherever the
    reading happened to stop, under the name of the vertex it did not
    read.  That is a postcondition satisfied by the wrong evidence, which
    is the defect class this whole gate keeps finding; an unread last
    footfall is `terminus_step_unresolved`, a refusal this section already
    owns, and not a licence to substitute a neighbour.
    """
    foot = verts[-1]
    return foot, hbv.get(foot)


def unstood_destination(t: dict, why: dict) -> Finding:
    """The refusal a DECLARED destination gets when `standing_datum` found
    nothing a walker stands on - SAYING WHICH OF THREE STATES IT IS.

    THE SHIPPED ROW WAS AN INSTRUMENT FAILURE REPORTED AS A FINDING ABOUT
    THE WORLD, and it was wrong about the instrument too.  MEASURED on drive
    2's frozen Fresh42 save a521b70356e7: S7 A and T6 A at (103.9, 341.2)
    were published "the applied surface 9.3-13.3 m in from its own edge was
    not read", `termination_class UNKNOWN`.  All four probe vertices inside
    complete-station-hub, (104, 351..354), HAD been read, every one at
    77.650 - exactly the declaration - and what vetoed them was
    piece_artisanstation's 40 x 200 x 40 m trigger volume standing over the
    whole pad (see `instruments.load_geometry`).  A row that cannot tell
    "never read" from "read and occupied" sends its reader to the wrong
    instrument, and UNKNOWN is the same hole `NO_CLASS` was, spelled again.

    So the three states are three rows with three classes, and none of them
    is UNKNOWN:

      READ AND OCCUPIED  `terminus_obstructed`, 1D_OBSTRUCTED: every inside
          vertex was read and a walker-blocking solid stands over every top
          on it, ground included.  The bodies are COUNTED and NAMED - 1D is
          a positive measurement, not a residue - and the remedy is
          demolition or a re-route, which is an operator decision.
      NEVER READ  `terminus_unmeasured`, 1E_OUT_OF_ORDER: the ground
          instrument returned nothing for some inside vertex, and the tool
          that reads it is in this tree; the prerequisite names the
          vertices.
      NOTHING INSIDE  `terminus_unmeasured`, 1C_UNSERVED: the walk crossed
          no lattice vertex inside the footprint at all, so no ground there
          can be read by any walk; closed by a change to the declaration.

    The class itself is the section's remedy-family declaration applied by
    `termination.backstop` - one implementation - off the numbers here.
    """
    label = (f"{t['segment']} terminus {t['end']} at "
             f"({t['x']:.1f}, {t['z']:.1f})")
    hbv = t.get("heights_by_vertex") or {}
    unread = list(why.get("unread") or [])
    blocked = list(why.get("blocked") or [])
    bodies = why.get("blocking_bodies") or {}
    n_in = int(why.get("inside") or 0)
    decl = t.get("declared_datum")
    nums = dict(declared_datum=decl, probed_m=INSIDE_PROBE_M,
                inside_vertices=n_in, unread_vertices=len(unread),
                occupied_vertices=len(blocked),
                unread_at=[list(v) for v in unread[:12]],
                **t["nums"])
    if unread:
        return Finding(
            check="terminus_unmeasured", ok=False,
            message=("{}: {} is within reach and DECLARES {:.3f} m, and {} "
                     "of the {} lattice vertices the walk took inside it "
                     "were never READ by the ground instrument (first {}), "
                     "so nothing standing there could be measured{}. A "
                     "declaration is not a datum.".format(
                         label, t["dest"], decl or 0.0, len(unread), n_in,
                         unread[0],
                         "" if not blocked else
                         "; the {} that were read are each occupied by {}"
                         .format(len(blocked),
                                 sorted(set(bodies.values()))))),
            where=(t["x"], t["z"]), measured=float(len(unread)),
            limit=0.0, numbers=nums)
    if blocked:
        names = sorted(set(bodies.values()))
        nums.update(obstruction_bodies=len(bodies), blockers=names,
                    read_y=[round(hbv[v], 4) for v, _b in blocked
                            if v in hbv],
                    # BY PREFAB AND PIVOT, so whose they are is a ledger
                    # reading (`termination.own_blockers`), not assumed.
                    blocker_pieces=list((why.get("blocking_pieces")
                                         or {}).values()))
        return Finding(
            check="terminus_obstructed", ok=False,
            message=("{}: every one of the {} lattice vertices the walk "
                     "took inside {} WAS read ({}) and on each a "
                     "walker-blocking solid stands over every top, the "
                     "ground included: {} ({} bod{}). The road arrives at a "
                     "body, not at a place a walker can stand; that is "
                     "closed by demolition or a re-route, not by a terrain "
                     "cut.".format(
                         label, n_in, t["dest"],
                         ", ".join(f"{hbv[v]:.3f}" for v, _b in blocked
                                   if v in hbv),
                         ", ".join(names), len(bodies),
                         "y" if len(bodies) == 1 else "ies")),
            where=(t["x"], t["z"]), measured=float(len(bodies)), limit=0.0,
            numbers=nums)
    return Finding(
        check="terminus_unmeasured", ok=False,
        message=("{}: the walk toward {}'s centre crossed no lattice vertex "
                 "inside its footprint, so no ground in it can be read by "
                 "any walk. It DECLARES {:.3f} m, and a declaration is not "
                 "a datum.".format(label, t["dest"], decl or 0.0)),
        where=(t["x"], t["z"]), measured=0.0, limit=1.0, numbers=nums)



# ---------------------------------------------------------------------------
# CHECK 1 - the road arrives on the datum of the thing it meets
# ---------------------------------------------------------------------------
def check_arrival(term: dict, profile: list[float | None], tol: float,
                  step_limit: float) -> list[Finding]:
    """One terminus, already measured.  `profile` is the applied surface at
    1 m from the terminus to the point the walker first stands on the
    destination, terminus first.

    `term['datum']` is a READING off the destination's own standing
    geometry (`standing_datum`).  `term['declared_datum']`, when the
    destination declared one, is the PLAN, and it is judged here rather
    than believed: a declaration that disagrees with what is standing is
    the defect that put eleven of this world's road termini on ground
    nobody stands on.
    """
    out: list[Finding] = []
    label = (f"{term['segment']} terminus {term['end']} at "
             f"({term['x']:.1f}, {term['z']:.1f})")
    road_y = profile[0] if profile else None
    if road_y is None:
        return [Finding(
            check="terminus_datum", ok=False,
            message=(f"{label}: the applied surface at the road's own last "
                     "station was not read, so its arrival is UNMEASURED"),
            where=(term["x"], term["z"]), numbers=dict(**term["nums"]))]

    # A CANDIDATE THIS ROAD CANNOT POSSIBLY MEET IS NOT A MISSED ARRIVAL.
    # The standing-surface fallback offers the nearest walkable top, and
    # "nearest" is not "reachable": on this world it offered an arch's own
    # 3.287 m crown to a terminus 0.1 m away, and a trollcave chest at
    # y 5066 - Valheim parks cave interiors ~5 km up - to a road at 57.983,
    # which came out as an arrival error of -5008.034 m.  A gate that
    # prints that number has stopped being believable, and the honest
    # reading is not "this road misses by 5 km" but "this road has no
    # destination I can measure".  The envelope is the walker's own: what a
    # player could climb over the gap at the slide limit, plus their own
    # height for a single step up onto a deck.  A DECLARED pad datum is
    # never rejected - the builder said that is the destination.
    if term["nums"].get("destination_kind") == "standing":
        envelope = term["gap_m"] * step_limit + REACH_UP_M
        if abs(road_y - term["datum"]) > envelope:
            n = term["nums"]
            drawn = ("" if n.get("build_pieces_within_reach") is None else
                     " It was the best of {} walkable top(s) out of {} "
                     "build piece(s) standing inside {:.0f} m: a verdict "
                     "over a population is only readable beside the set it "
                     "was drawn from.".format(
                         n.get("walkable_candidates"),
                         n.get("build_pieces_within_reach"),
                         ARRIVAL_REACH_M))
            return [Finding(
                check="terminus_unmeasured", ok=False,
                message=("{}: the nearest standing top is {} at {:.3f} m, "
                         "{:+.3f} m from the carriageway's {:.3f} m over a "
                         "{:.1f} m gap - outside the {:.3f} m a walker "
                         "could cover at the slide limit, so it is not the "
                         "thing this road meets. This terminus has NO "
                         "measurable destination: nothing declares a datum "
                         "here and nothing reachable is standing "
                         "here.{}".format(label, term["dest"], term["datum"],
                                          road_y - term["datum"], road_y,
                                          term["gap_m"], envelope, drawn)),
                where=(term["x"], term["z"]),
                measured=abs(road_y - term["datum"]), limit=envelope,
                numbers=dict(candidate=term["nums"].get("destination"),
                             candidate_y=round(term["datum"], 3),
                             applied_y=round(road_y, 3),
                             gap_m=round(term["gap_m"], 2),
                             walkable_candidates=n.get(
                                 "walkable_candidates"),
                             build_pieces_within_reach=n.get(
                                 "build_pieces_within_reach"),
                             reach_m=ARRIVAL_REACH_M,
                             # 1C: THE REMEDY IS PERFORMED BY NO TOOL HERE.
                             # Nothing reachable is standing, so there is
                             # no ground to cut and no depth to cut it by;
                             # this is closed by BUILDING a destination or
                             # RETIRING the segment, and this tree does
                             # neither.  Counting it beside an actionable
                             # defect is how 109 refusals became a work
                             # queue and a decision list added together.
                             termination_class="1C_UNSERVED"))]

    # THE DECLARATION IS JUDGED, NEVER BELIEVED.  A site file's `pad_y` and
    # a pad write's `target_y` are both statements of INTENT; the standing
    # surface is what the engine did with them.  Where they disagree the
    # road was built to the wrong number, and every downstream check that
    # compared the road against that same wrong number agreed with it -
    # which is precisely why this class survived four checks.
    decl = term.get("declared_datum")
    if decl is not None:
        gap_y = decl - term["datum"]
        if abs(gap_y) > tol:
            # WHICH OF THE TWO DEFECTS IT IS, decided by the OTHER
            # readings of the SAME pad rather than by a story about one
            # site.  A pad is a rectangle and its declaration is one
            # number: if some terminus's footfall DOES stand on that
            # number, the declaration is achievable and the pad is not
            # level; if none does, the declaration is a number nobody
            # stands on.  Both are refusals and they are repaired in
            # different places - the first by the pad, the second by the
            # declaration - so naming the wrong one sends a builder to
            # edit a figure that is right.
            stood = sorted(
                (sg, en, round(dy, 3))
                for sg, en, dy in (term.get("pad_readings") or ())
                if abs(decl - dy) <= tol)
            spread = [round(dy, 3) for _s, _e, dy
                      in (term.get("pad_readings") or ())]
            if stood:
                cause = (
                    "THE PAD IS NOT LEVEL, not the declaration: {} of this "
                    "destination's termini DO stand on {:.3f} m ({}), so "
                    "the number is achievable and the ground inside one "
                    "rectangle differs by {:.3f} m. Repair the PAD.".format(
                        len(stood), decl,
                        ", ".join(f"{s} {e} reads {d:.3f}"
                                  for s, e, d in stood),
                        max(spread) - min(spread)))
            else:
                cause = (
                    "THE DECLARATION IS THE DEFECT: not one of this "
                    "destination's {} measured termini stands on {:.3f} m "
                    "(readings {}). A road profiled to it lands on nothing "
                    "- sites.yaml's Vestvik pad_y is the DISTRICT figure "
                    "and is 1.30 m below the pad the village actually "
                    "stands on. Repair the DECLARATION.".format(
                        len(spread), decl,
                        ", ".join(f"{d:.3f}" for d in sorted(spread))))
            out.append(Finding(
                check="destination_datum_unstood", ok=False,
                message=("{}: {} DECLARES its datum at {:.3f} m but the "
                         "thing standing there is {} at {:.3f} m - the "
                         "declaration is {:+.3f} m off the ground a walker "
                         "arrives on. The datum a terminus is judged "
                         "against is the reading, not the plan. {}".format(
                             label, term["dest"], decl,
                             term.get("datum_source") or "the applied "
                             "ground", term["datum"], gap_y, cause)),
                where=(term["x"], term["z"]), measured=abs(gap_y), limit=tol,
                numbers=dict(declared_datum=round(decl, 3),
                             standing_datum=round(term["datum"], 3),
                             declaration_error_m=round(gap_y, 3),
                             datum_source=term.get("datum_source"),
                             datum_read_at=term.get("datum_at"),
                             repair=("pad" if stood else "declaration"),
                             pad_readings=spread,
                             pad_spread_m=(round(max(spread) - min(spread), 3)
                                           if spread else None),
                             termini_standing_on_the_declaration=len(stood),
                             **term["nums"])))
    # THE JOIN POPULATION, AND ONLY IT, IS ASKED THE JOIN QUESTION.  See
    # `JOIN_GAP_M`.  A terminus past the split is an APPROACH and the berm
    # and step checks below own it, INCLUDING the last edge onto the
    # destination.
    is_join = term["gap_m"] <= JOIN_GAP_M
    err = road_y - term["datum"]
    # WHO OWNS THE GROUND A REPAIR WOULD HAVE TO MOVE, both halves, and
    # why this refusal can or cannot terminate.  Read per REFUSAL and not
    # per terminus, because each names a different DEPTH at a different
    # station and the footprint is derived from the depth.  `owners` is
    # absent for a fixture, and the answer is then omitted rather than
    # guessed.
    owners = term.get("forbidden_extents", "unread")

    def owned(depth: float, at_xz: tuple | None = None, **shape) -> dict:
        if owners == "unread":
            return {}
        ax, az = at_xz or (term["x"], term["z"])
        got = repair_ownership(ax, az, depth, owners, step_limit)
        got.update(contested_ground(ax, az,
                                    got.get("repair_footprint_m", 0.0),
                                    term.get("write_index")))
        # WHO THIS REFUSAL BELONGS TO, stated so the classifier can tell a
        # contest from a road standing on its own embankment.  MEASURED,
        # full gate: 5 of this section's 6 1B_CONTESTED rows named THEIR
        # OWN segment's `terrain_write` as the contester, because a
        # carriageway is always inside its own write.  `term["nums"]`
        # carries `segment` for every terminus this section judges and it
        # was already going into the Finding - it just was not going into
        # the classifier, which is the whole defect.
        got["own_actor"] = (term.get("nums") or {}).get("segment")
        # WHICH WAY, BESIDE WHETHER - AND THE DIRECTION IS COMPUTED
        # FIRST, BECAUSE THE CLASS DEPENDS ON IT.  `shape` is the
        # refusal's own signed geometry - `judged_as` and `error_m` for a
        # JOIN, `rise_m` for a RISER - passed in rather than re-derived,
        # and it is CONSUMED here rather than merged into `got`: the
        # Finding states those keys itself and a dict that carried them
        # twice would be a duplicate keyword at the call site.
        #
        # THE CLASSIFIER WAS BEING HANDED `got` ALONE, WHICH IS THE
        # OWNERSHIP ANSWER WITH THE SIGN MISSING.  `termination_class`
        # sends a `repair_direction == "raise"` row to 1C_UNSERVED - a
        # permitted write no instrument in this tree issues - and it
        # reads that direction off `judged_as` and `error_m`, both of
        # which live in `shape` and neither of which was in the dict.  So
        # the class came back ACTIONABLE for the one raise on the board
        # while the direction beside it said `raise`: the same
        # key-names-a-subset defect as the branch above, two lines apart.
        # MEASURED on the fixture in `_raise_class_arms`: classified off
        # `got` the T13 join reads ACTIONABLE; off `{**shape, **got}` it
        # reads 1C_UNSERVED, and the cut-signed twin is unmoved.
        got["repair_direction"] = repair_direction({**shape, **got})
        got["termination_class"] = termination_class({**shape, **got})
        return got
    if is_join and abs(err) > tol:
        out.append(Finding(
            check="terminus_datum", ok=False,
            message=("{}: the carriageway lands at applied {:.3f} m and "
                     "{} sits at {:.3f} m - {:+.3f} m out, over a {:.1f} m "
                     "gap. This is a JOIN: the road's last station and its "
                     "destination share a lattice cell, so there is no "
                     "ground between them for the berm or step checks to "
                     "measure and the walker steps straight from one to "
                     "the other. Tolerance {:.3f} m is half the player's "
                     "own MEASURED capsule radius.".format(
                         label, road_y, term["dest"], term["datum"], err,
                         term["gap_m"], tol)),
            where=(term["x"], term["z"]), measured=abs(err), limit=tol,
            numbers=dict(applied_y=round(road_y, 3),
                         destination_datum=round(term["datum"], 3),
                         error_m=round(err, 3), gap_m=round(term["gap_m"], 2),
                         judged_as="join", join_gap_m=JOIN_GAP_M,
                         # THE PIECE WHOSE TOP IS THE DATUM, when a piece is,
                         # so a join under it is asked whose piece it is.
                         blocker_pieces=([term["datum_piece"]]
                                         if term.get("datum_piece") else []),
                         **owned(abs(err) - tol, judged_as="join",
                                 error_m=round(err, 3)),
                         **term["nums"])))

    known = [(i, y) for i, y in enumerate(profile) if y is not None]
    if len(known) >= 2:
        ends = max(profile[0], term["datum"])
        hump = max(y for _i, y in known) - ends
        if hump > tol:
            at = max(known, key=lambda t: t[1])
            # THE SET, NOT ONLY THE WORST.  A repair derived from `berm_m`
            # and `at_m_along` is ONE SAMPLE WIDE, and the check that
            # re-reads afterwards is `max()` over the WHOLE profile -- so
            # cutting the named sample alone moves the finding to the
            # SECOND-highest sample and reports the same refusal at new
            # coordinates, forever.  MEASURED on this world: R3 B's berm is
            # 0.643 m at 6 m along and R4 A's is 0.459 m at 4 m along over
            # the SAME pad, and 49 of the 167 lattice vertices inside the
            # two 6 m travelled ways stand above `ends + tol`.  A repair
            # sized off the worst would have taken 1 of those 49.
            #
            # A REPAIR IS ONLY EVER AS WIDE AS THE CHECK THAT NAMES ITS
            # POPULATION, so this check names it: every sample of the
            # profile that is over, with its own height and its own station,
            # and the cap they must all come under.
            cap = round(ends + tol, 3)
            # AND EVERY MEMBER CARRIES ITS OWN COORDINATE.  `at_m_along` is
            # an INDEX into this terminus's own walk and means nothing
            # outside it, so a disposal handed this population had no way
            # to place a cut without re-deriving the walk.  `_station` is
            # the function the ownership question already uses for exactly
            # this translation - it is simply also used here, so the set a
            # repair consumes is the set the check named, in coordinates.
            over = [dict(at_m_along=i, y=round(y, 3),
                         at=([round(v, 1) for v in _station(term, i)]
                             if _station(term, i) else None),
                         over_by_m=round(y - cap, 3))
                    for i, y in known if y > cap]
            out.append(Finding(
                check="terminus_berm", ok=False,
                message=("{}: a BERM {:.3f} m high stands between the "
                         "carriageway and {} - the ground {} m along the "
                         "{:.1f} m gap reads {:.3f} m, above both the road "
                         "({:.3f}) and the datum ({:.3f}). Two endpoints "
                         "that agree say nothing about the ground between "
                         "them. {} of the {} read sample(s) on this "
                         "approach stand above the {:.3f} m cap and ALL of "
                         "them are the repair: the check is a max() over "
                         "the profile, so cutting only the worst reports "
                         "the same refusal at the next one.".format(
                             label, hump, term["dest"], at[0],
                             term["gap_m"], at[1], profile[0],
                             term["datum"], len(over), len(known), cap)),
                where=(term["x"], term["z"]), measured=hump, limit=tol,
                numbers=dict(berm_m=round(hump, 3),
                             at_m_along=at[0], gap_m=round(term["gap_m"], 2),
                             cap_m=cap, ends_m=round(ends, 3),
                             samples_over_cap=len(over),
                             samples_read=len(known),
                             repair_population=over,
                             # AT THE BERM'S OWN STATION, not at the
                             # terminus: a berm 14 m along an approach is
                             # cut where it stands, and asking who owns
                             # the ground at the wrong end of the walk is
                             # the same defect the riser had.
                             **owned(hump - tol, _station(term, at[0])),
                             **term["nums"])))
        # THE WALKER CROSSES LATTICE EDGES, NOT DIAGONALS.  `GetHeight` is
        # NEAREST-VERTEX, MEASURED at population scale by RoadsRemeasure:
        # 243 of 243 multi-member vertex groups returned IDENTICAL heights
        # where bilinear would have spread them by up to 1.583 m.  So the
        # INSTRUMENT is quantised while the SURFACE - Heightmap's mesh - is
        # the lattice triangulated and therefore piecewise linear.  A
        # walker moving diagonally across a cell does not travel the
        # diagonal; he crosses two 1 m MESH EDGES, and the corner between
        # them is exactly where a step hides: judging the diagonal pair
        # against the 1.414 m allowance (1.1049 m) cleared a 6.349 m wall
        # on N1 that the corner vertices would have shown immediately.
        #
        # So every judged transition here is a 1 m EDGE at the 0.7813 m
        # allowance, a diagonal is the EASIER of its two corner routes (a
        # walker takes the way round that works), and a corner that was
        # never read makes the transition UNRESOLVED rather than clean.
        hbv = term.get("heights_by_vertex") or {}
        verts = term.get("vertices") or []
        # EVERY TRANSITION THAT IS OVER, not only the worst one.  `worst`
        # decides the VERDICT; `overs` is the REPAIR POPULATION, and they
        # are different questions.  A cut sized from `at` alone moves the
        # single named edge under the limit and leaves the next one over -
        # MEASURED on the two approaches to vestvik-hut-2: 22 of 586
        # eight-connected lattice edges inside the travelled ways are over,
        # and the two the findings name are the 4th and 14th worst of them.
        worst, worst_at, unresolved, overs = 0.0, None, 0, []
        # OVER AS READ, BUT BY LESS THAN THE READINGS CAN RESOLVE.  Two
        # ground readings are each rounded to `GROUND_QUANTUM_M`, so their
        # difference cannot place a transition on either side of a limit it
        # is within one quantum of.  MEASURED on drive 2: T3 B and S8 B were
        # refused at 0.7813 read, and the unrounded lattice under both is
        # 0.78128433 - UNDER the 0.78128563 limit.  Those are counted here
        # and published, never refused on the rounding and never dropped.
        within = []
        exceeds = A.instruments().exceeds_step_limit
        in_band = A.instruments().within_step_resolution
        # THE SIGN OF THE WORST TRANSITION, carried out with it.  A repair
        # can only CUT; a refusal that publishes `abs()` cannot be told
        # from its own numbers whether the ground is high (cuttable) or the
        # destination is high (not), so every consumer had to go back to
        # the world to find out which half of the disposal applied.
        worst_rise = None
        for i in range(len(verts) - 1):
            (ax, az), (bx, bz) = verts[i], verts[i + 1]
            ya, yb = hbv.get((ax, az)), hbv.get((bx, bz))
            if ya is None or yb is None:
                unresolved += 1
                continue
            if abs(ax - bx) < 1e-9 or abs(az - bz) < 1e-9:
                routes = [[ya, yb]]
            else:
                routes = []
                for cx, cz in ((bx, az), (ax, bz)):
                    yc = hbv.get((cx, cz))
                    if yc is not None:
                        routes.append([ya, yc, yb])
                if not routes:
                    unresolved += 1
                    continue
            cost = min(max(abs(r[k + 1] - r[k]) for k in range(len(r) - 1))
                       for r in routes)
            if exceeds(cost, step_limit):
                overs.append(dict(at=[round(v, 1) for v in (ax, az, bx, bz)],
                                  step_m=round(cost, 3),
                                  over_by_m=round(cost - step_limit, 3)))
            elif in_band(cost, step_limit):
                within.append(dict(at=[round(v, 1) for v in (ax, az, bx, bz)],
                                   step_m=round(cost, 6),
                                   over_by_m=round(cost - step_limit, 6)))
            if cost > worst:
                worst, worst_at = cost, (ax, az, bx, bz)
        # AND THE LAST EDGE IS THE ONE ONTO THE DESTINATION ITSELF.
        # Without it an APPROACH would have NOBODY measuring its final
        # step: the walk reads GROUND, the destination is a deck or a
        # tread, and the transition between the two is the one a walker
        # actually makes last.  The datum test used to catch it by
        # accident, from the wrong end of the approach; now that the join
        # population no longer includes these, it is measured here, by
        # the check that owns steps, at the same 1 m allowance.
        # A WALK THAT WAS NEVER TAKEN IS NOT AN UNREAD CORNER.  `verts`
        # is empty for a terminus whose destination was resolved without
        # a lattice walk, and inventing an unresolved transition there
        # would report a reading nobody asked for.
        #
        # AND THE RISER IS MEASURED AT THE WALK'S LAST FOOTFALL OR IT IS
        # NOT MEASURED AT ALL.  Two defects lived in the three lines this
        # replaces, and they are the same defect twice:
        #
        #  1. A SUBSTITUTED VERTEX.  `next(... for v in reversed(verts)
        #     if v in hbv)` walks BACKWARDS until something was read, so a
        #     walk whose last five vertices are unread measured "the step
        #     a walker takes last" five metres short of where he takes it
        #     and reported it clean - while `unresolved` stayed 0, because
        #     the `None` arm only fires when NOTHING on the walk was read.
        #     An unread last vertex is UNRESOLVED, which is a refusal this
        #     section already owns; it is not a licence to read another.
        #  2. A FABRICATED SECOND ENDPOINT.  The published `at` was
        #     `[verts[-1], term.x, term.z]` - the walk's far end and the
        #     ROAD'S OWN TERMINUS - described as "a single 1 m lattice
        #     edge".  Those two points are `gap_m` apart, and MEASURED on
        #     gate C's save a087af6253a0, S12 A published
        #     (-301,-125)->(-282,-131): 20.22 m apart, over a limit
        #     expressed per 1 m, in a refusal whose own arithmetic
        #     therefore could not be checked.  `repair_population` is what
        #     the Lipschitz-cone disposal consumes to size cuts, so that
        #     row would have sent 20 m of cut at ground that is not the
        #     defect.
        #
        # A RISER HAS ONE LOCATION, NOT TWO.  It is the ground the walker
        # last stands on before he steps onto the thing; it is published
        # as that single vertex, SIGNED, and named a riser rather than an
        # edge.
        if not is_join and verts:
            foot, last = _last_footfall(verts, hbv)
            if last is None:
                unresolved += 1
            else:
                rise = term["datum"] - last
                onto = abs(rise)
                if exceeds(onto, step_limit):
                    overs.append(dict(
                        at=[round(foot[0], 1), round(foot[1], 1)],
                        judged_as="riser", onto=term["dest"],
                        step_m=round(onto, 3), rise_m=round(rise, 3),
                        # THE ABSOLUTE NUMBER THE REPAIR TARGETS, not only
                        # the delta to it.  `rise_m` is `datum - ground`
                        # AT THE MOMENT OF THE READING, and a disposal
                        # re-reads the ground before it cuts - so a planner
                        # reconstructing the datum as `live_ground +
                        # rise_m` would recover a datum that has drifted by
                        # exactly however much the ground moved since.  The
                        # destination's standing top is the one number in
                        # this row that does not move when ground does.
                        datum_m=round(term["datum"], 3),
                        over_by_m=round(onto - step_limit, 3),
                        onto_the_destination=True))
                elif in_band(onto, step_limit):
                    within.append(dict(
                        at=[round(foot[0], 1), round(foot[1], 1)],
                        judged_as="riser", step_m=round(onto, 6),
                        over_by_m=round(onto - step_limit, 6)))
                if onto > worst:
                    worst, worst_at, worst_rise = onto, (foot[0],
                                                         foot[1]), rise
        # ONE DENOMINATOR FOR BOTH ARMS.  `terminus_step_unresolved` used
        # `len(verts) - 1` while `terminus_step` counted the riser as well,
        # so the same walk published two different population sizes and a
        # reader comparing "3 of 24 over" with "1 of 23 unresolved" was
        # comparing two sets.  The riser is a transition or it is not, in
        # both of them.
        judged = max(len(verts) - 1, 0) + (0 if is_join or not verts else 1)
        # THE BAND IS PUBLISHED WHETHER OR NOT THIS TERMINUS REFUSES, so a
        # transition the instrument could not place is on the page with its
        # own number rather than absorbed into a pass.
        term["step_within_resolution"] = within
        if unresolved:
            out.append(Finding(
                check="terminus_step_unresolved", ok=False,
                message=("{}: {} of {} lattice transitions on the approach "
                         "to {} could not be judged because a corner vertex "
                         "was never read. An unread corner is not a clean "
                         "transition, and neither is a last footfall the "
                         "walk never stood on.".format(
                             label, unresolved, judged, term["dest"])),
                where=(term["x"], term["z"]),
                measured=float(unresolved), limit=0.0,
                numbers=dict(unresolved=unresolved,
                             transitions_judged=judged, **term["nums"])))
        if exceeds(worst, step_limit):
            riser = len(worst_at) == 2
            if riser:
                msg = ("{}: the approach to {} steps {:+.4f} m from the "
                       "ground at ({:.0f},{:.0f}) - the walk's LAST "
                       "FOOTFALL - onto {} itself. That is a RISER at the "
                       "destination's own edge: it has ONE location, not "
                       "two, and the sign says which side is high. The "
                       "player's own slide limit is {:.4f} m and there is "
                       "no step-height constant in Valheim to appeal "
                       "to.".format(label, term["dest"], worst_rise,
                                    worst_at[0], worst_at[1], term["dest"],
                                    step_limit))
            else:
                msg = ("{}: the approach to {} climbs {:.4f} m across a "
                       "single 1 m lattice edge, at "
                       "({:.0f},{:.0f})->({:.0f},{:.0f}) - by the easier "
                       "of the two routes through that cell. The "
                       "player's own slide limit is {:.4f} m per 1 m and "
                       "there is no step-height constant in Valheim to "
                       "appeal to.".format(label, term["dest"], worst,
                                           *worst_at, step_limit))
            out.append(Finding(
                check="terminus_step", ok=False,
                message=msg,
                where=(term["x"], term["z"]), measured=worst,
                limit=step_limit,
                numbers=dict(step_m=round(worst, 3),
                             # A RISER HAS NO RUN, so it does not get one.
                             # `edge_m=1.0` on a transition whose two ends
                             # were 20.22 m apart is the number that made
                             # the old refusal uncheckable.
                             judged_as=("riser" if riser
                                        else "lattice_edge"),
                             **({"rise_m": round(worst_rise, 3),
                                 "datum_m": round(term["datum"], 3),
                                 "onto": term["dest"],
                                 # WHAT THE RISER STEPS ONTO, BY PREFAB AND
                                 # PIVOT, when it is a standing piece: the
                                 # blocker whose owner decides whether this
                                 # is the factory's own work.
                                 "blocker_pieces": (
                                     [term["datum_piece"]]
                                     if term.get("datum_piece") else [])}
                                if riser else {"edge_m": 1.0}),
                             at=[round(v, 1) for v in worst_at],
                             # THE SET, NOT ONLY THE WORST.  See the berm
                             # above: the verdict is a max() and the repair
                             # is the whole set, so a cut sized from `at`
                             # alone reports the same refusal at the next
                             # edge.  `transitions_judged` is the
                             # denominator, because a count of failures
                             # without the set it was drawn from is the
                             # defect class this gate keeps finding.
                             transitions_over_limit=len(overs),
                             transitions_judged=judged,
                             transitions_within_resolution=len(within),
                             resolution_m=A.instruments().GROUND_QUANTUM_M,
                             repair_population=overs,
                             # AT THE TRANSITION'S OWN LOCATION, and for
                             # the depth that actually closes it - the
                             # OVERAGE, not the whole step.  R4 B's 0.799
                             # over a 0.7813 limit is an 0.018 m cut, and
                             # calling its footprint 1.02 m rather than
                             # 2.02 m is the difference between a repair
                             # that lands and one that is refused.
                             #
                             # AND THE OVERAGE IS AGAINST THE SLOPE A
                             # DISPOSAL SOLVES AT, NOT AGAINST THE LIMIT.
                             # `step - step_limit` is the depth that
                             # leaves the transition EXACTLY ON the limit,
                             # and nothing in this tree writes onto a
                             # limit - `roads/cone.py` solves at
                             # `instruments.DISPOSAL_SLOPE_PER_M` for the
                             # reason stated there.  Asking who owns the
                             # ground at a depth no repair will use is
                             # asking about a repair that does not exist.
                             # See `instruments.disposal_depth` for the
                             # one row on Ulfsland this moves and the two
                             # footprints that straddle its owner.
                             **owned(A.instruments().disposal_depth(
                                         worst, step_limit),
                                     (worst_at[0], worst_at[1]),
                                     judged_as=("riser" if riser
                                                else "lattice_edge"),
                                     **({"rise_m": round(worst_rise, 3)}
                                        if riser else {})),
                             **term["nums"])))
    return out


# ---------------------------------------------------------------------------
# CHECK 2 - nothing of ours seated on generated content
# ---------------------------------------------------------------------------
def generated_inside(rect: tuple, pieces: list[tuple], markers: list[tuple],
                     is_declared) -> list[tuple]:
    """Build pieces inside `rect` that are inside a marker's reach AND that
    no live ledger record declares.  Returns (piece, marker) pairs."""
    cx, cz, hw, hd = rect
    hits = []
    for (name, x, z) in pieces:
        if abs(x - cx) > hw or abs(z - cz) > hd:
            continue
        if is_declared(name, x, z):
            continue
        for (mx, mz, radius, mname) in markers:
            if math.hypot(x - mx, z - mz) <= radius:
                hits.append(((name, x, z), (mx, mz, radius, mname)))
                break
    return hits


def anchored_prefabs_at(pad: dict, anchors, margin_m: float) -> set[str]:
    """The prefabs THIS PROJECT'S OWN CHAIN declared at this pad.

    OWNERSHIP IS A BODY QUESTION, AND ASKING IT PIECE BY PIECE IS WHY THIS
    CHECK CALLED FOUR OF OUR OWN BUILDINGS GENERATED CONTENT.
    `declared_placements` resolves a prefab AND A POSITION off the chain's
    wire lines and spawn-plan blobs; MEASURED on Ulfsland it yields 13,138
    placements against 124,980 build-piece ZDOs in the save - 10.5 % - so
    "declared by no live ledger record" is a TRUE SENTENCE ABOUT NINE
    TENTHS OF OUR OWN WORLD.  A `spawn_plan`'s per-piece commands live in
    a blob and only its batch lines are inline, and a plan that was later
    superseded is dropped from the live chain entirely while its building
    goes on standing.

    So the question is asked where the ledger can answer it: an anchored
    plan that REACHES this pad and DECLARES the piece's prefab, which is
    exactly `structures.our_bodies`'s own discriminator ("a body is OURS
    only if an anchor reaches it AND the body holds at least one piece of
    a prefab THAT ANCHOR'S OWN PLAN DECLARED") asked at pad scope.  The
    anchors come off the WHOLE chain, not the live chain: a retired plan
    is still evidence that WE built here, and retirement is bookkeeping,
    not demolition.

    MEASURED with this rule: portal-hall 2,249 hits -> 0 residual (one
    anchor, role `portal_hall_body`, 47 declared prefabs including
    `Piece_flametal_pillar` and `Piece_grausten_floor_1x1`); lh-west 798
    -> 0; stenvik-longhouse-1 206 -> 0; stenvik-longhouse-2 164 -> 0.
    3,417 pieces, every one of them ours, and the check said "content the
    world generated and nobody recorded" about all of them.

    It does not weaken the refusal.  The generated village at (1218, 512)
    has a `goblin_bed` this project has never placed and NO anchor within
    reach, so it still refuses - that case is in this section's selftest
    and it is the one that keeps this function honest.
    """
    out: set[str] = set()
    for a in anchors or ():
        if abs(a["x"] - pad["cx"]) <= pad["hw"] + margin_m \
                and abs(a["z"] - pad["cz"]) <= pad["hd"] + margin_m:
            out |= set(a.get("prefabs") or ())
    return out


def check_seat_on_generated(pads, pieces, markers, is_declared,
                            anchors=(), anchor_margin_m: float = 0.0
                            ) -> list[Finding]:
    out = []
    for pad in pads:
        hits = generated_inside((pad["cx"], pad["cz"], pad["hw"], pad["hd"]),
                                pieces, markers, is_declared)
        ours = anchored_prefabs_at(pad, anchors, anchor_margin_m)
        claimed = len(hits)
        if ours:
            hits = [h for h in hits if h[0][0] not in ours]
        if not hits:
            continue
        hist = Counter(p[0][0] for p in hits).most_common(5)
        mk = Counter(p[1][3] for p in hits).most_common(1)[0]
        out.append(Finding(
            check="seat_on_generated", ok=False,
            message=("site {!r} ({:.1f} x {:.1f} m at ({:.1f}, {:.1f})) is "
                     "seated on GENERATED content: {} build piece(s) inside "
                     "its rectangle stand within the reach of location "
                     "marker {!r}, are declared by no live ledger record, "
                     "and carry no prefab any anchored plan of ours "
                     "declares here ({} such prefab(s) reach this pad) - "
                     "{}. Building here overwrites content the world "
                     "generated and nobody recorded.".format(
                         pad["name"], pad["hw"] * 2, pad["hd"] * 2,
                         pad["cx"], pad["cz"], len(hits), mk[0], len(ours),
                         ", ".join(f"{n} x{c}" for n, c in hist))),
            where=(pad["cx"], pad["cz"]), measured=float(len(hits)),
            limit=0.0,
            numbers=dict(generated_pieces=len(hits), marker=mk[0],
                         histogram=dict(hist), site=pad["name"],
                         pieces_inside_a_marker=claimed,
                         cleared_by_our_own_anchors=claimed - len(hits),
                         our_prefabs_anchored_here=len(ours),
                         # EVERY REFUSED PIECE, so the finding is its own
                         # repair plan rather than a count to re-derive.
                         at=[[n, round(px, 2), round(pz, 2)]
                             for (n, px, pz), _m in hits[:200]])))
    return out


# ---------------------------------------------------------------------------
# the section
# ---------------------------------------------------------------------------
def _markers(ctx) -> tuple[list[tuple], dict]:
    """Live LocationProxy ZDOs, each with its OWN declared reach where the
    seed's dump can identify it and the world's worst declared radius where
    it cannot.  The identified/unidentified split is REPORTED, because on
    this world 1,192 of 1,294 markers are modded POIs the mod-free dump has
    never seen, and a check that hid that would be claiming a precision it
    does not have."""
    from . import spawn as S                              # noqa: PLC0415
    dump_path = S._find_seed_dump(ctx)
    if not dump_path or not dump_path.exists():
        return [], dict(available=False, path=str(dump_path))
    doc = json.loads(dump_path.read_text())
    locs = doc.get("locations") or []
    worst = max(max(float(l.get("exteriorRadius") or 0.0),
                    float(l.get("interiorRadius") or 0.0)) for l in locs)
    index = {(round(float(l["x"]), 1), round(float(l["z"]), 1)): l
             for l in locs}
    out, ident = [], 0
    for name, x, _y, z, *_q in ctx.census.rows:
        if name != "LocationProxy":
            continue
        match = None
        for ddx in (-0.1, 0.0, 0.1):
            for ddz in (-0.1, 0.0, 0.1):
                cand = index.get((round(x + ddx, 1), round(z + ddz, 1)))
                if cand and math.hypot(float(cand["x"]) - x,
                                       float(cand["z"]) - z) <= 0.2:
                    match = cand
                    break
            if match:
                break
        if match:
            ident += 1
            r = max(float(match.get("exteriorRadius") or 0.0),
                    float(match.get("interiorRadius") or 0.0))
            out.append((x, z, r, str(match.get("name"))))
        else:
            out.append((x, z, worst, "unidentified"))
    return out, dict(available=True, markers=len(out), identified=ident,
                     unidentified=len(out) - ident, worst_radius_m=worst)


# The smallest footprint that counts as a thing a road can ARRIVE on.  A
# 2x2 floor slab is 4 m2; a lamp post's shaft is 0.06 m2 and a road does
# not arrive on a lamp post.  The criterion is geometric rather than a list
# of prefab names, so a world built out of different pieces is still
# measured.
MIN_DESTINATION_AREA_M2 = 1.0


def _hull_area_m2(solid) -> float:
    h = solid.hull()
    if len(h) < 3:
        return 0.0
    a = 0.0
    for i in range(len(h)):
        x1, z1 = h[i]
        x2, z2 = h[(i + 1) % len(h)]
        a += x1 * z2 - x2 * z1
    return abs(a) / 2.0


def _blocks_above(grid, s, cx: float, cz: float, top: float,
                  walker_h: float, bounds_only) -> bool:
    """Is a walker's own capsule blocked from standing on this top?

    The wt-south ruling - a road does not arrive on a wall - asked where
    the destination is CHOSEN and not only where its height is read.

    AND A BOUND MAY NOT VETO EITHER.  A blocker known only to
    bounding-box precision is exactly the birch that covered every sample
    of S7's probe from 20 m away, so it does not disqualify a destination
    any more than it can become one.
    """
    return any(o is not s and o.verts and o.name not in bounds_only
               and o.covers(cx, cz)
               and A.blocks_at_height(o, top, walker_h, 0.05)
               for o in grid.near(cx, cz, 0.0))


def _nearest_standing(x: float, z: float, grid, build_pieces: set,
                      walker_h: float = 0.0,
                      bounds_only: set | frozenset = frozenset(),
                      _reach_up_m: float = REACH_UP_M,
                      geom: dict | None = None
                      ) -> tuple | None:
    """The nearest WALKABLE top a road could arrive on: (gap, x, z, y,
    name, walkable, considered).

    THE CHOOSER OBEYS THE SAME THREE RULINGS AS THE DATUM READER, and it
    did not, which is how this world got three wrong destinations.
    `standing_datum` refuses a bounding box, refuses a face a walker's own
    capsule cannot occupy, and walks on; this function chose the nearest
    `level_top` in xz and handed the answer to it, so the refusal that came
    out was measured against a thing no walker arrives on.  MEASURED on
    Ulfsland, all three:

      * A BOUNDING BOX.  N2 B / N3 A at (-650, 5402) were given
        `TreasureChest_trollcave`, top y 5066.017 - Valheim parks cave
        interiors about 5 km up - 28.0 m away and 5,008.034 m off the
        carriageway.  That prefab is `bounds_only`: two MeshCollider
        bounds rows and no tight collider anywhere, so its extent is an
        UPPER BOUND and must not pick a road's destination.
      * A FACE NOBODY CAN STAND ON.  W5 B at (-774, -290) was given a
        `stone_wall_2x1` cap at 45.026 m with another `stone_wall_2x1`
        standing directly on it: a walker's capsule does not fit there at
        all.  This is the wt-south ruling - a road does not arrive on a
        wall - applied where the destination is CHOSEN rather than only
        where its height is read.
      * A TOP OUT OF REACH OF ITS OWN FOOTING.  `level_top` returns the
        HIGHEST level face of a solid, and for an arch that is its crown.
        N4 B at (-846, 7730) was given `portal_wood`'s crown 3.294 m above
        its own sill, 0.06 m away, and reported UNMEASURED at 3.287 m off
        the road - while the `wood_floor` 3.52 m away tops out at 33.556 m
        and the carriageway lands at 33.556 m, an EXACT arrival.  R2 A got
        the same arch 11.06 m away and was refused by 3.076 m against a
        `wood_stair` landing 0.104 m off its road.  A walker steps onto a
        top from the ground the piece itself stands on, so a face more
        than `REACH_UP_M` above that piece's own lowest point is a thing
        he looks at - the same bound, and the same reason, as the ceiling
        on the standing probe.

    What is left is a deck, a floor, a quay or a stair landing - the
    things a road actually meets.

    AND THE HEIGHT IS THE FACE AT HIS FEET, NOT THE PIECE'S SUMMIT.  With
    `geom` in hand the datum is `instruments.arrival_face` - the highest
    LEVEL face of any PART covering the point of this piece's footprint
    nearest the walker - so a `wood_stair` answers with the tread he steps
    onto rather than the 0.098 m ledge at the top of the flight.  Without
    it the answer is the old whole-solid `level_top()`, which is that
    ledge; see the module note on `arrival_face` for both live numbers.

    WHAT THIS FILTER STILL CANNOT SEE, MEASURED, AND WHY BOTH WIDER
    RULES WERE REFUSED.  W5 B at (-774, -290), applied 33.208, is this
    world's one UNMEASURED terminus with anything standing near it.  The
    chooser offers a `stone_wall_2x1` BATTLEMENT at 46.026 m, 11.57 m
    away in plan and 12.818 m straight up, on top of a twelve-metre
    stone curtain whose lowest build piece is 34.026 m: nothing stands
    above that top course so the occupancy test clears it, and
    `46.026 - 45.026 = 1.000 m` clears the reach test, because THE ONE
    THING WALLS ALWAYS DO IS STACK and a course's own `ymin` is the
    course beneath it.  `check_arrival`'s envelope then reports the
    terminus UNMEASURED, which is honest.  Two wider rules were written
    for it and BOTH WERE MEASURED AND REFUSED, on this save:

      the walker-reach envelope, moved into the chooser.  It admits the
        SAME wall's 44.026 m course at gap 12.30 - 12.30 m of gap buys
        9.6 m of climb at the slide limit - and turns "no measurable
        destination" into a confident -10.818 m refusal against a wall.
      the footing taken from the BODY rather than the piece.  It does
        reject every course of that curtain, and it also rejects EVERY
        PIER DECK IN THE WORLD: a `wood_floor` at 30.600 is connected
        through its own piles to the seabed, so its body footing is
        metres down.  MEASURED: N0 A's deck answer became a
        `wood_stair` 26.80 m away, S1 B's became a `blackmarble_floor`
        23.29 m away at -6.055 m, and S12 A/B, R4 B, T9 B, R1 A and
        T13 B all became UNMEASURED.  A rule that loses seven measured
        arrivals to recover one is not a fix.

    So W5 B stays UNMEASURED and the finding is about the WORLD, not the
    chooser: `w-south` has no site pad anywhere in the chain, the nearest
    declared rectangle is 143.2 m away, and the keep's lowest build piece
    is 0.818 m above the carriageway with 11.31 m of unbuilt ground in
    between.  That is a MISSING ARRIVAL SURFACE, and it is named as one.

    AND THE POPULATION IS PRINTED BESIDE THE VERDICT, WITH ITS
    DENOMINATOR.  A count alone is not legible: `population 0` reads as
    "unreadable" while `0 of a 288-sample rectangle` reads as "broken",
    and that distinction cost four hours on a dead-flat pad elsewhere in
    this gate.  So this chooser carries out BOTH numbers - `walkable`,
    how many tops survived every ruling above and could have been the
    answer, and `considered`, how many build pieces stood inside
    `ARRIVAL_REACH_M` at all - and the finding prints them as
    `W of C`.

    W5 B is the argument: the refusal named ONE `stone_wall_2x1` and
    never said what it was picked out of, so a reader could not tell
    "the one thing standing here" from "the nearest of a crowd of
    courses of one curtain".  THE SHORT-CIRCUIT ON `gap` WAS REMOVED TO
    MAKE THE DENOMINATOR TRUE: a count that skipped the work for
    candidates that could not win would be a population nobody measured,
    which is the defect this rule exists to stop.
    """
    best = None
    walkable = considered = 0
    for s in grid.near(x, z, ARRIVAL_REACH_M):
        if s.name not in build_pieces or not s.verts:
            continue
        if s.name in bounds_only:
            continue
        d = A.hull_distance(s, x, z)
        gap = max(d, 0.0)
        if gap > ARRIVAL_REACH_M:
            continue
        considered += 1
        if _hull_area_m2(s) < MIN_DESTINATION_AREA_M2:
            continue
        if geom is None:
            top = s.level_top()
            cx, cz = (s.xmin + s.xmax) / 2.0, (s.zmin + s.zmax) / 2.0
        else:
            top, cx, cz = A.instruments().arrival_face(s, geom, x, z)
        if top is None:
            continue
        if top - s.ymin > _reach_up_m:
            continue
        if _blocks_above(grid, s, cx, cz, top, walker_h, bounds_only):
            continue
        walkable += 1
        if best is None or gap < best[0]:
            best = (gap, cx, cz, top, s.name)
            # THE PIECE ITSELF, BY PREFAB AND PIVOT, so a refusal against
            # this top can ask the ledger whose piece it is.
            p = getattr(s, "pos", None)
            best_ref = None if p is None else dict(
                prefab=s.name, x=round(float(p[0]), 4),
                y=round(float(p[1]), 4), z=round(float(p[2]), 4))
    return None if best is None else (*best, walkable, considered, best_ref)


def ray_entry_m(x: float, z: float, ux: float, uz: float, cx: float,
                cz: float, hw: float, hd: float) -> float | None:
    """Arc length along the unit bearing (ux, uz) from (x, z) at which the
    walk ENTERS the axis-aligned rectangle - 0.0 when it starts inside,
    None when the bearing never meets it.  The slab test, per axis."""
    t0, t1 = -math.inf, math.inf
    for p, u, lo, hi in ((x, ux, cx - hw, cx + hw), (z, uz, cz - hd, cz + hd)):
        if abs(u) < 1e-12:
            if p < lo or p > hi:
                return None
            continue
        a, b = sorted(((lo - p) / u, (hi - p) / u))
        t0, t1 = max(t0, a), min(t1, b)
    if t0 > t1 or t1 < 0.0:
        return None
    return max(t0, 0.0)


def probe_walk(x: float, z: float, ux: float, uz: float, n: int,
               gap: float, contains, snap
               ) -> tuple[list[tuple[float, float]],
                          list[tuple[float, float]]]:
    """The lattice walk from a terminus into its destination:
    `(every vertex in order, the vertices INSIDE the destination)`.

    Sampled at half a metre and COLLAPSED, because `Heightmap.GetHeight`
    is nearest-vertex: consecutive 1 m stations along an arbitrary bearing
    can share a vertex, and a pair that shares a vertex has a step of
    0.000 BY CONSTRUCTION.

    "INSIDE THE DESTINATION" IS A CLAIM ABOUT THE DESTINATION'S
    FOOTPRINT, NOT ABOUT ARC LENGTH, and conflating the two put this
    section's datum outside the pad it was reading.  `s_m >= gap` is
    measured on the UNSNAPPED point; `snap` then moves it up to 0.5 m per
    axis - 0.707 m on a diagonal - which can push it back OUT of the
    thing being probed.

    MEASURED, and it cost a whole wrong diagnosis: R3 B's datum was read
    at (-905.0, 1893.0), and vestvik-hut-2's own resolved claim spans
    x -904.8..-894.4, so the "first sample INSIDE the pad" was 0.200 m
    OUTSIDE it, on natural ground, and read 35.214 against the pad's
    declared 34.370.  This section then reported the pad as 0.844 m out
    of level - while the structures section, whose pad_flatness /
    pad_coverage population is 17 pads including BOTH of vestvik-hut-2's
    Vestvik neighbours (vestvik-house-1 1.490 m, vestvik-villa-2
    1.589 m), finds no sample on vestvik-hut-2 more than 0.100 m off
    34.370 on either save.  The pad was flat.  The probe was outside it.

    The vertex is requested from the ground instrument either way, so the
    predicate costs no extra reading.
    """
    verts: list[tuple[float, float]] = []
    inside: list[tuple[float, float]] = []
    for k in range(2 * n + 1):
        s_m = k * 0.5
        vx, vz = snap(x + ux * s_m, z + uz * s_m)
        if verts and (vx, vz) == verts[-1]:
            continue
        verts.append((vx, vz))
        if s_m >= gap and contains(vx, vz) \
                and (not inside or (vx, vz) != inside[-1]):
            inside.append((vx, vz))
    return verts, inside


def centreline_distance(nodes, px: float, pz: float) -> tuple[float, tuple]:
    """Distance from a point to a polyline, and the point on it.

    Segment-wise with the projection clamped to each span, so a terminus
    beyond a road's end measures to that end rather than to an infinite
    line.
    """
    best, at = math.inf, (px, pz)
    for i in range(len(nodes) - 1):
        ax, az = nodes[i]
        bx, bz = nodes[i + 1]
        vx, vz = bx - ax, bz - az
        ll = vx * vx + vz * vz
        t = 0.0 if ll == 0 else max(0.0, min(
            1.0, ((px - ax) * vx + (pz - az) * vz) / ll))
        cx, cz = ax + t * vx, az + t * vz
        d = math.hypot(px - cx, pz - cz)
        if d < best:
            best, at = d, (cx, cz)
    return best, at


def on_another_carriageway(name: str, x: float, z: float, segs: dict
                           ) -> tuple[str, float, tuple] | None:
    """The road this terminus LANDS ON, if any: (segment, distance, where).

    A T-JUNCTION IS A JUNCTION, and testing only other segments' TERMINI
    missed every one of them.  MEASURED on Ulfsland: R3's A end at
    (-216.0, 2146.0) lies 2.000 m from N0b-wmid-wnorth's centreline, and
    segments.yaml declares R3's own `endpoint_standoff_m` for
    `r3-n0b-junction` as 2.0 - the road stopped EXACTLY where it said it
    would, on the carriageway of the road it was routed to meet, and this
    section reported it as "a road that arrives nowhere measurable",
    alongside its nearest build piece: a `piece_groundtorch` 17.5 m away
    and a declared pad 435.6 m away.

    The tolerance is not a taste pick and not `JUNCTION_TOL_M`: it is the
    OTHER segment's OWN RECORDED PROFILE, `half_width_m + shoulder_m`, so
    the question is literally "does this terminus stand on that road's
    paved surface or its shoulder".  N0b's is 3.0 + 1.0 = 4.0 m and R3 A
    is 2.000 m in, well inside the carriageway.

    MEASURED over all 50 termini, this tier claims exactly three, and two
    of them are already claimed by the tier above it: R1 B is 4.000 m
    inside T8's carriageway and R4 A is 0.016 m inside R3's, and both
    resolve to a DECLARED pad first - which is what keeps R1 B's 0.707 m
    and R4 A's 0.920 m miss on their villages measured instead of
    deferred.  Only R3 A moves, and R3 A is the one that was wrong.
    """
    best = None
    for n2, s2 in segs.items():
        if n2 == name:
            continue
        reach = float(s2["half_width_m"]) + float(s2["shoulder_m"])
        d, at = centreline_distance(s2["nodes"], x, z)
        if d <= reach and (best is None or d < best[1]):
            best = (n2, d, at)
    return best



def resolve_destination(name: str, end: str, x: float, z: float, pads,
                        terminus_at: dict, grid, build_pieces: set,
                        walker_h: float,
                        bounds_only: set | frozenset = frozenset(),
                        other_end: tuple[float, float] | None = None,
                        segs: dict | None = None,
                        geom: dict | None = None
                        ) -> tuple[str, object]:
    """WHAT THIS TERMINUS MEETS, in three tiers of EVIDENCE, best first.

    A DECLARATION outranks a MEASURED COINCIDENCE, and a measured
    coincidence outranks a SEARCH RESULT.  That middle tier was missing
    and the order was the defect: the junction test used to be the last
    resort, reached only when `_nearest_standing` returned nothing at all,
    so a road/road junction that happened to have anything walkable within
    `ARRIVAL_REACH_M` was judged as a settlement arrival against whatever
    was nearest.

    MEASURED on Ulfsland, 25 built segments, 50 termini: 29 termini stand
    within `JUNCTION_TOL_M` of another built segment's terminus and are
    therefore junctions, and 6 of those 29 were being judged here instead
    of deferred - N0b B and N1 A at (-154, 2506) against a `wood_floor`
    16.5 m away (-1.780 m, reported twice), N2 B and N3 A at (-650, 5402)
    against a `TreasureChest_trollcave` 5,008.034 m off the carriageway
    (reported twice).  Four refusals about a question this section does
    not own; `junction.py` measures junction spread against the same
    slide limit.

    The pad tier stays on top because a pad claim is a DECLARATION - the
    builder said that is the destination.  That is what keeps T8 B and
    T9 A's real 0.842 m miss on wt-town a REFUSAL rather than a deferral,
    even though they share a terminus, and what keeps the six passing
    arrivals at portal-hall, wt-south and wt-spawn measured.

    AND A SEGMENT'S TWO ENDS ARE TWO PLACES.  One pad is the destination
    of the END THAT IS NEARER IT, and the other end must look further
    down the tiers - otherwise the section measures one destination twice
    and judges a road against a pad it was never routed to.  MEASURED,
    and it is one segment on this world: S1-portalhub-brgs2's A end sits
    ON portal-hall (gap 0.000 m, applied 37.100 against a declared and
    standing 37.100 - a PASS), and its B end 39.6 m away was ALSO given
    portal-hall, 17.3 m outside the rectangle, and refused four times -
    destination_datum_unstood 1.932, terminus_datum 4.124, terminus_berm
    1.932, terminus_step 1.932, every one of them a statement about the
    hillside between the bridge and the hall.  What B actually stands on
    is a `wood_floor` bridge deck at 30.600 m AT GAP 0.000, and
    segments.yaml's own crossing contract declares `deck_datum_y: 30.6`.
    The road is +0.445 m above its deck; that is the finding, and it is
    one refusal about the right thing instead of four about the wrong one.

    Returns `("declared", (gap, pad))`, `("junction", [partner, ...])`,
    `("standing", (gap, x, z, y, prefab))` or `("none", None)`.
    """
    best = None
    for pad in pads:
        g = rect_distance_m(x, z, pad["cx"], pad["cz"], pad["hw"],
                            pad["hd"])
        if other_end is not None:
            # THE OTHER END'S CLAIM ON THE SAME PAD, measured the same
            # way.  Strictly nearer wins, so a tie leaves both ends on it
            # rather than letting a floating-point hair decide which road
            # end a village belongs to.
            og = rect_distance_m(other_end[0], other_end[1], pad["cx"],
                                 pad["cz"], pad["hw"], pad["hd"])
            if og < g:
                continue
        if best is None or g < best[0]:
            best = (g, pad)
    if best is not None and best[0] <= ARRIVAL_REACH_M:
        return "declared", best
    met = sorted(f"{n2} {e2}" for (n2, e2), (x2, z2) in terminus_at.items()
                 if (n2, e2) != (name, end)
                 and math.hypot(x2 - x, z2 - z) <= JUNCTION_TOL_M)
    if met:
        return "junction", met
    # A T-JUNCTION IS A JUNCTION.  The tier above compares TERMINI; a road
    # that runs into the MIDDLE of another road shares no vertex with it
    # and fell straight through to the standing search.  See
    # `on_another_carriageway` for the one this claims and the two it does
    # not.
    if segs:
        onto = on_another_carriageway(name, x, z, segs)
        if onto is not None:
            n2, d, at = onto
            return "junction", [
                "{} carriageway, {:.3f} m from its centreline at "
                "({:.1f}, {:.1f})".format(n2, d, at[0], at[1])]
    std = _nearest_standing(x, z, grid, build_pieces, walker_h, bounds_only,
                            geom=geom)
    return ("standing", std) if std is not None else ("none", None)


# ---------------------------------------------------------------------------
# EXTREMITY_END_DEPENDS_ON: A ROAD TO A CAPE ARRIVES ON THE CAPE.
#
# `roads/spec.py` DECLARES what an `extremity` node is: "the farthest sample
# from the landmass centroid in each 45 deg sector that is at least 25 m from
# any water edge" -- a road's END by design, with nothing built there to
# arrive on (`roads/geography.py` derives them per seed into
# `geography.<seed>.yaml`).  This section had no tier for it, so an extremity
# terminus fell through to the NEAREST STANDING TOP within 30 m and was judged
# against whatever that was.  MEASURED, Fresh42 drive 4 (gate on save
# af96bfb62427): S5-temple-southcape B at (-472.1, -694.8), 2.1 m from the
# `southcape` extremity (-474.0, -694.0), was refused 1C terminus_step
# +8.24 m "onto the standing top of stone_wall_2x1" at (-444.5, -686.25) --
# a course of the seed ruin StoneTowerRuins09, whose centre stands 33.7 m
# from the node, OUTSIDE its 7 m keep-out.  The road was never routed to the
# ruin; the ruin was the nearest thing standing.
#
# THE RULE: after the pad and junction tiers (a declaration and a coincidence
# still outrank it), a terminus whose segment NAMES an extremity of this
# seed's geography as that end, within `ARRIVAL_REACH_M` of it, arrives on
# the cape's own ground: it is reported in the population, never measured
# against a stranger's wall and never UNMEASURED.  The ground it stands on is
# the carriageway's, which the roads section walks.
#   the_node_the_segment_names_at_this_end   parsed from the segment's name,
#       the same way roads.built_segments reads `from`/`to`.
#   this_seeds_derived_extremities   geography.<seed>.yaml, scope-checked.
# NEUTER `_neuter_extremity_tier`: no extremity is recognised, so S5 B is
# judged against the ruin's wall again.
# ---------------------------------------------------------------------------
EXTREMITY_END_DEPENDS_ON = ("the_node_the_segment_names_at_this_end",
                            "this_seeds_derived_extremities")


def planned_extremities(seed: str) -> tuple[dict | None, str]:
    """This seed's geography EXTREMITY nodes keyed as a road's name spells
    them (hyphens dropped): `({id: (x, z)}, source)`, or `(None, why)`."""
    import yaml                                              # noqa: PLC0415
    import sys                                               # noqa: PLC0415
    roads = Path(_jumpstart()) / "roads"
    if str(roads) not in sys.path:
        sys.path.insert(0, str(roads))
    import geography                                         # noqa: PLC0415
    p = geography.geography_path(seed)
    if not p.exists():
        return None, f"{p} is absent"
    doc = yaml.safe_load(p.read_text()) or {}
    if str(doc.get("seed")) != str(seed):
        return None, (f"{p} declares seed {doc.get('seed')!r}, not this "
                      f"run's {seed!r}")
    return ({str(n["id"]).replace("-", ""): (float(n["xz"][0]),
                                             float(n["xz"][1]))
             for n in doc.get("nodes") or [] if n.get("kind") == "extremity"},
            str(p))


def _jumpstart():
    return A.instruments().JUMPSTART


def extremity_end(name: str, end: str, x: float, z: float,
                  extremities: dict | None) -> tuple[str, float] | None:
    """`(node, gap)` when this terminus's segment names an extremity at this
    end and stands within `ARRIVAL_REACH_M` of it (EXTREMITY_END_DEPENDS_ON)."""
    if not extremities:
        return None
    parts = name[len("road_"):].split("_") if name.startswith("road_") \
        else name.split("-")
    if len(parts) < 3:
        return None
    nid = parts[1] if end == "A" else parts[-1]
    at = extremities.get(nid)
    if at is None:
        return None
    gap = math.hypot(x - at[0], z - at[1])
    return (nid, round(gap, 2)) if gap <= ARRIVAL_REACH_M else None


def terminus_tier(name: str, end: str, x: float, z: float, pads,
                  terminus_at: dict, grid, build_pieces: set, walker_h: float,
                  bounds_only=frozenset(), other_end=None, segs=None,
                  geom=None, extremities: dict | None = None,
                  planned: dict | None = None) -> tuple[str, object]:
    """`resolve_destination`, then EXTREMITY_END_DEPENDS_ON: a terminus the
    pad and junction tiers leave to a search result arrives on its cape
    when its segment names one there.  Returns `("extremity", (node, gap))`
    for that; else PLANNED_END_DEPENDS_ON's `("district" | "short" |
    "refused", detail)` when the node the plan routes this end to answers
    for it; else `resolve_destination`'s answer."""
    tier, payload = resolve_destination(name, end, x, z, pads, terminus_at,
                                        grid, build_pieces, walker_h,
                                        bounds_only, other_end, segs, geom)
    if tier in ("standing", "none"):
        cape = extremity_end(name, end, x, z, extremities)
        if cape is not None:
            return "extremity", cape
        got = planned_end(name, end, x, z, pads, other_end, planned)
        if got is not None:
            return got
    return tier, payload


# ---------------------------------------------------------------------------
# PLANNED_END_DEPENDS_ON: AN END IS JUDGED AGAINST WHERE THE PLAN ROUTED IT,
# AND WHERE THAT IS NOT THERE THE ROW SAYS WHY AND WHOSE.
#
# The tiers above ask what is NEAR a terminus.  None of them asks what the
# road was FOR, so a terminus 30.1 m from its own town and one whose
# village the build refused came back as the same sentence - "meets
# NOTHING this gate can read" - with one class and no owner.  MEASURED,
# Fresh42 drive 5 (gate on frozen save 8c8c54096789): 3 of 26 termini:
#   T7-stathub-stenvik B (335.9, -274.8): 32.8 m from the `stenvik` TOWN
#       node, INSIDE its declared 100 m district; no pad holds the node
#       (the town's centre is open ground between its buildings) and the
#       plan's own arrival record reads "no pad holds this node: nothing to
#       arrive on", by design - `network.pad_keepout` reads a town's radius
#       as its DISTRICT, and "a road arrives at the district".  Its nearest
#       live pad, stenvik-longhouse-2, is 32.85 m away, a building at the
#       district's south edge the road was never routed to.
#   T5-temple-dockshore B (3.7, -330.6): the `dockshore` node stands inside
#       the live early-dock pad, 67.46 m away.  The plan's own record:
#       "stopped 46.44 m short: water", 10 stations of wet tail trimmed.
#       Generated ground falls under the 30.0 m water plane 9 m out and
#       stays under it to the pad: the dock is across open water.
#   S8-wtsouth-treesth A (-672.1, -385.9): the plan arrived it on
#       wt-south's pad edge.  The build REFUSED wt-south's site_pad (ledger
#       seq 2077, step 5: its clearing cylinder holds 3
#       dungeon_forestcrypt_door) and REFUSED J-southcape-wtsouth at step 4
#       road_ground rc=7 - the leg the plan joined here, its B end 0.00 m
#       from this one.  Either one built and this terminus is measured.
#
# THE RULE: after the pad, junction and extremity tiers, a terminus whose
# built polyline IS the plan's at that end (within JUNCTION_TOL_M of it -
# otherwise the plan on disk belongs to another build and is not read) is
# resolved against the node the plan names there:
#   SHORT     a live pad HOLDS the node's own point (the plan routed this
#       end onto it) and it is past reach: `terminus_short_of_destination`,
#       with the straight walk to the pad READ - water on it names the
#       destination's PLACEMENT as the owner, dry ground names the leg's
#       ARRIVAL, unread names the reading.
#   DISTRICT  no pad holds the node, the node's site declares a radius that
#       holds the terminus and the site HAS live pads: the road arrives on
#       the district's own ground, exactly as a road to a cape arrives on
#       the cape's - reported in the population with its nearest pad and
#       never measured against a building it was not routed to.  MEASURED
#       the other way on this save: judged against stenvik-longhouse-2 the
#       row is a 0.513 m "berm" 5 m along 32.9 m of the town's natural
#       ground, ACTIONABLE - a cut ordered for an arrival nobody planned.
#   SHORT, again  the site has live pads, none holds the node, and the
#       terminus is OUTSIDE its district: the nearest is the destination.
#   REFUSED   the node has NO live pad and this chain RECORDS why: its
#       site's site_pad note REFUSED, or a planned leg ending within
#       JUNCTION_TOL_M of this terminus that the ledger does not hold, with
#       its build-journal refusal: `terminus_destination_refused`, naming
#       each refusal and its unit.
# Anything else is still `terminus_unmeasured`.
#   the_node_the_plan_names_at_this_end  segments.<seed>.yaml from/to,
#       scope-checked, read only for a polyline that IS the plan's.
#   that_node's_site_and_district        sites.<seed>.yaml, scope-checked.
#   the_live_pads_that_belong_to_it      `network.pad_keepout`'s rule: by
#       name, or by holding the node's own point.
#   this_chain's_own_refusals            site_pad REFUSED notes on the live
#       chain, and the build journal beside the ledger.
# NEUTERS `_neuter_planned_end(arm)`: that arm is not read, and its
# terminus is UNMEASURED again.
# ---------------------------------------------------------------------------
PLANNED_END_DEPENDS_ON = ("the_node_the_plan_names_at_this_end",
                          "that_node's_site_and_district",
                          "the_live_pads_that_belong_to_it",
                          "this_chain's_own_refusals")
_PLANNED_END_READS = frozenset({"district", "short", "refused"})


def planned_ends(seed: str, ledger: Path, live: list[dict]
                 ) -> tuple[dict | None, str]:
    """PLANNED_END_DEPENDS_ON's readings for this run: `(planned, source)`,
    or `(None, why)` when the plan is absent or another seed's.  `built`
    is filled by the caller from the ledger's own segments."""
    import sys                                               # noqa: PLC0415
    import yaml                                              # noqa: PLC0415
    js = str(_jumpstart())
    if js not in sys.path:
        sys.path.insert(0, js)
    import worldseed                                         # noqa: PLC0415
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    sp = worldseed.segments_path(seed)
    if not sp.exists():
        return None, f"{sp} is absent"
    doc = yaml.load(sp.read_text(), Loader=loader) or {}
    if str(doc.get("seed")) != str(seed):
        return None, (f"{sp} declares seed {doc.get('seed')!r}, not this "
                      f"run's {seed!r}")
    segs: dict[str, dict] = {}
    for s in doc.get("segments") or []:
        nodes = s.get("nodes") or []
        if len(nodes) < 2 or not s.get("from") or not s.get("to"):
            continue
        pe = s.get("pad_edge") or {}
        segs[str(s["id"])] = dict(
            a=str(s["from"]), b=str(s["to"]),
            ends={"A": (float(nodes[0][0]), float(nodes[0][1])),
                  "B": (float(nodes[-1][0]), float(nodes[-1][1]))},
            arrival=(pe.get("arrival") or {}).get("ends") or {},
            water_trim=s.get("water_trim") or {})
    nodes = {str(k): dict(xz=(float(v["xz"][0]), float(v["xz"][1])),
                          kind=v.get("kind"), cites=v.get("cites"))
             for k, v in (doc.get("node_provenance") or {}).items()
             if isinstance(v, dict) and v.get("xz")}
    sites: dict[str, dict] = {}
    tp = worldseed.sites_path(seed)
    why_sites = f"{tp} is absent"
    if tp.exists():
        sd = yaml.load(tp.read_text(), Loader=loader) or {}
        if str(sd.get("seed")) == str(seed):
            why_sites = str(tp)
            for s in sd.get("sites") or []:
                if s.get("xz") and s.get("pad_radius_m") is not None:
                    sites[str(s["id"]).replace("-", "")] = dict(
                        id=str(s["id"]), type=s.get("type"),
                        xz=(float(s["xz"][0]), float(s["xz"][1])),
                        radius=float(s["pad_radius_m"]))
        else:
            why_sites = (f"{tp} declares seed {sd.get('seed')!r}, not "
                         f"{seed!r}")
    site_refusals: dict[str, list] = defaultdict(list)
    for r in live:
        p = r.get("params") or {}
        text = str(p.get("text") or "")
        if r.get("op") == "note" and p.get("role") == "site_pad" \
                and text.startswith("REFUSED"):
            m = r.get("meta") or {}
            sid = str(p.get("site_id"))
            # The writer nests its own prefix ("REFUSED wt-south: REFUSED
            # wt-south: the clearing cylinder ..."); the reason is after it.
            why = text.split("\n")[0]
            while why.startswith(f"REFUSED {sid}: "):
                why = why[len(f"REFUSED {sid}: "):]
            site_refusals[sid].append(dict(
                seq=r.get("seq"), step=m.get("build_step"),
                unit=m.get("build_unit"), text=why[:240]))
    journal = Path(ledger).parent / "pipeline" / "build" / "buildstage.json"
    unit_refusals: dict[str, list] = defaultdict(list)
    if journal.exists():
        for rec in json.loads(journal.read_text()) or []:
            rc = rec.get("rc")
            if isinstance(rc, int) and rc != 0:
                unit_refusals[str(rec.get("unit"))].append(dict(
                    step=rec.get("step"), name=rec.get("name"), rc=rc))
    return (dict(segs=segs, nodes=nodes, sites=sites,
                 site_refusals=dict(site_refusals),
                 unit_refusals=dict(unit_refusals), built=set(),
                 journal=str(journal) if journal.exists() else None,
                 source=f"{sp}; sites {why_sites}"), str(sp))


def _holds_node(pad: dict, nx: float, nz: float) -> bool:
    """The pad's rectangle holds the node's own point: the plan routed this
    end ONTO it (`network.terminal_pads`)."""
    return abs(nx - pad["cx"]) <= pad["hw"] and abs(nz - pad["cz"]) <= pad["hd"]


def _belongs_to_node(pad: dict, nid: str, nx: float, nz: float) -> bool:
    """`network.pad_keepout`'s two ways a pad belongs to a node: by NAME
    (hyphens dropped, prefix-wise, so a town's buildings attach to the
    town) or by HOLDING the node's own point."""
    name = str(pad["name"])
    flat = name.replace("-", "")
    named = flat == nid or flat.startswith(nid) or name.startswith(nid + "-")
    return named or _holds_node(pad, nx, nz)


def planned_end(name: str, end: str, x: float, z: float, pads,
                other_end: tuple[float, float] | None,
                planned: dict | None) -> tuple[str, dict] | None:
    """PLANNED_END_DEPENDS_ON for one terminus: `("district" | "short" |
    "refused", detail)`, or None when the plan does not answer for it."""
    if not planned:
        return None
    seg = planned["segs"].get(name[len("road_"):].replace("_", "-")
                              if name.startswith("road_") else name)
    if seg is None:
        return None
    px, pz = seg["ends"][end]
    if math.hypot(px - x, pz - z) > JUNCTION_TOL_M:
        return None
    nid = seg["a"] if end == "A" else seg["b"]
    node = planned["nodes"].get(nid)
    if node is None:
        return None
    nx, nz = node["xz"]
    site = planned["sites"].get(nid.replace("-", ""))
    node_gap = round(math.hypot(x - nx, z - nz), 2)
    mine = []
    for pad in pads:
        if not _belongs_to_node(pad, nid.replace("-", ""), nx, nz):
            continue
        g = rect_distance_m(x, z, pad["cx"], pad["cz"], pad["hw"], pad["hd"])
        if other_end is not None and rect_distance_m(
                other_end[0], other_end[1], pad["cx"], pad["cz"], pad["hw"],
                pad["hd"]) < g:
            continue
        mine.append((g, pad))
    if mine:
        holders = [t for t in mine if _holds_node(t[1], nx, nz)]
        gap, pad = min(holders or mine, key=lambda t: t[0])
        if not holders and site is not None and node_gap <= site["radius"]:
            if "district" not in _PLANNED_END_READS:
                return None
            return "district", dict(node=nid, site=site["id"],
                                    site_type=site["type"],
                                    radius_m=site["radius"],
                                    node_gap_m=node_gap, gap=round(gap, 2),
                                    pad=pad)
        if "short" not in _PLANNED_END_READS:
            return None
        return "short", dict(node=nid, node_gap_m=node_gap,
                             gap=round(gap, 2), pad=pad,
                             holds_node=bool(holders),
                             site=site["id"] if site else None,
                             radius_m=site["radius"] if site else None,
                             placed_by=node.get("cites"),
                             arrival=seg["arrival"].get(nid),
                             water_trim=seg["water_trim"].get(nid))
    if "refused" not in _PLANNED_END_READS:
        return None
    site_ref = list(planned["site_refusals"].get(site["id"], [])) \
        if site else []
    partners = []
    for sid, s2 in sorted(planned["segs"].items()):
        if sid in planned["built"]:
            continue
        for e2, (qx, qz) in sorted(s2["ends"].items()):
            d = math.hypot(qx - x, qz - z)
            if d <= JUNCTION_TOL_M:
                partners.append(dict(
                    segment=sid, end=e2, gap_m=round(d, 2),
                    refusals=list(planned["unit_refusals"].get(sid, []))))
    if not site_ref and not partners:
        return None
    return "refused", dict(node=nid, site=site["id"] if site else None,
                           site_refusals=site_ref, partners=partners,
                           journal=planned.get("journal"))


def refused_destination(name: str, end: str, x: float, z: float,
                        d: dict) -> Finding:
    """`terminus_destination_refused`: the node this end was routed to has
    no live pad, and this chain records the refusals that left it so."""
    causes = []
    for r in d["site_refusals"]:
        causes.append(f"{d['site']}'s site_pad was REFUSED by build step "
                      f"{r['step']} (ledger seq {r['seq']}): {r['text']}")
    for p in d["partners"]:
        if p["refusals"]:
            causes.append("{} - planned to end {:.2f} m from here ({} end) - "
                          "was REFUSED by the build: {} (build journal)".format(
                              p["segment"], p["gap_m"], p["end"],
                              "; ".join(f"step {q['step']} {q['name']} rc="
                                        f"{q['rc']}" for q in p["refusals"])))
        else:
            causes.append(f"{p['segment']} - planned to end "
                          f"{p['gap_m']:.2f} m from here ({p['end']} end) - "
                          f"is not in the ledger and the build journal "
                          f"records no refusal of it: it was never built")
    return Finding(
        check="terminus_destination_refused", ok=False,
        message=("{} {} at ({:.1f}, {:.1f}) was routed to {!r}, which has NO "
                 "live pad on this chain, and no built road meets it here: "
                 "the build refused what it was to meet. {}. Each is closed "
                 "by its own unit, not by this road".format(
                     name, end, x, z, d["node"], "; ".join(causes))),
        where=(x, z), measured=float(len(causes)), limit=0.0,
        numbers=dict(segment=name, end=end, node=d["node"], site=d["site"],
                     site_refusals=d["site_refusals"],
                     partners=d["partners"], owner="; ".join(causes),
                     build_journal=d.get("journal"),
                     depends_on=list(PLANNED_END_DEPENDS_ON)))


def short_walk(x: float, z: float, pad: dict, snap
               ) -> list[tuple[float, float]]:
    """The lattice walk from a terminus to where the straight line toward
    its pad's centre enters the pad - the ground a walker would have to
    cross to arrive."""
    ux, uz = pad["cx"] - x, pad["cz"] - z
    L = math.hypot(ux, uz) or 1.0
    ux, uz = ux / L, uz / L
    entry = ray_entry_m(x, z, ux, uz, pad["cx"], pad["cz"], pad["hw"],
                        pad["hd"])
    reach = L if entry is None else entry
    verts, _ = probe_walk(x, z, ux, uz, int(math.ceil(reach)), reach,
                          lambda _vx, _vz: False, snap)
    return verts


def short_of_destination(name: str, end: str, x: float, z: float, d: dict,
                         verts: list, heights: list,
                         water_level: float) -> Finding:
    """`terminus_short_of_destination`: the node this end was routed to has
    a live pad, beyond reach and outside any district; the walk to it READ
    says whose the gap is."""
    pad = d["pad"]
    along = [math.hypot(vx - x, vz - z) for vx, vz in verts]
    read = [(k, h) for k, h in enumerate(heights) if h is not None]
    wet = [(k, h) for k, h in read if h < water_level]
    unread = len(heights) - len(read)
    arr = d.get("arrival") or {}
    plan_says = "; ".join(
        s for s in (arr.get("why"),
                    (f"{d['water_trim']} station(s) of wet tail trimmed"
                     if d.get("water_trim") else None)) if s) or "nothing"
    nums = dict(segment=name, end=end, node=d["node"],
                destination=pad["name"], gap_m=d["gap"],
                node_gap_m=d["node_gap_m"], district_m=d.get("radius_m"),
                walk_vertices=len(verts), unread_vertices=unread,
                wet_vertices=len(wet),
                first_wet_m=round(along[wet[0][0]], 1) if wet else None,
                last_wet_m=round(along[wet[-1][0]], 1) if wet else None,
                deepest_m=(round(water_level - min(h for _k, h in wet), 3)
                           if wet else None),
                plan_arrival=arr.get("why"), water_trim=d.get("water_trim"),
                placed_by=d.get("placed_by"),
                depends_on=list(PLANNED_END_DEPENDS_ON))
    if wet:
        nums["owner"] = (
            f"the placement of {pad['name']} ({d.get('placed_by')}): it "
            f"stands across water from the bank this leg ends on - the walk "
            f"to it is under the {water_level:.1f} m water plane from "
            f"{nums['first_wet_m']} m to {nums['last_wet_m']} m out, "
            f"{nums['deepest_m']} m deep - so no road reaches it; closed by "
            f"re-siting it onto a bank the network reaches or by a crossing "
            f"to it")
        state = "the ground between is WATER"
    elif unread:
        nums["owner"] = None
        state = f"{unread} of {len(verts)} vertices of that walk were never READ"
    else:
        nums["owner"] = (
            f"the leg's arrival (roads/network.py arrive_on_pads): it stopped "
            f"{d['gap']:.2f} m short of {pad['name']} over dry ground; the "
            f"plan's own record: {plan_says}")
        state = "the ground between is DRY"
    return Finding(
        check="terminus_short_of_destination", ok=False,
        message=("{} {} at ({:.1f}, {:.1f}) was routed to {!r}, whose live "
                 "pad {!r} it stops {:.2f} m short of - past the {:.0f} m "
                 "reach{} - and {}. The plan's own record: {}. Owner: "
                 "{}".format(
                     name, end, x, z, d["node"], pad["name"], d["gap"],
                     ARRIVAL_REACH_M,
                     (f", {d['node_gap_m']:.1f} m from the node and outside "
                      f"its {d['radius_m']:g} m district"
                      if d.get("radius_m") is not None else ""),
                     state, plan_says, nums["owner"] or "the reading")),
        where=(x, z), measured=float(d["gap"]), limit=ARRIVAL_REACH_M,
        numbers=nums)


def check_population(segs, termini, pads, tol: float, walker_r: float,
                     ledger: str = "", unmeasured: list | None = None,
                     deferred: list | None = None,
                     extremity: list | None = None,
                     district: list | None = None):
    """AN EMPTY POPULATION IS A REFUSAL, NOT A PASS.

    A check that measured nothing and reported PASS is the most dangerous
    state this gate can be in, and THIS SECTION WAS BRIEFLY IN IT: the road
    loader was asked of the wrong module, returned {}, and the section came
    back clean over ZERO termini on a world with 25 built roads.  It is
    guarded here, in a function with its own selftest case, because the
    same mistake in a section nobody re-ran would be invisible forever.
    """
    out: list[Finding] = []
    if not segs:
        out.append(Finding(
            check="population", ok=False,
            message=("no road segment was read from the ledger at all, so "
                     "NOT ONE arrival was measured. A verdict over an empty "
                     "population is not a verdict."),
            numbers=dict(segments=0, ledger=ledger)))
    elif not termini:
        out.append(Finding(
            check="population", ok=False,
            message=("{} built road segment(s) and NOT ONE of their {} "
                     "termini is within {:.0f} m of a pad that declares its "
                     "own centre and datum, so no arrival was measured. "
                     "Either the roads meet nothing, or the destinations "
                     "never recorded the datum a road is supposed to arrive "
                     "on - and both are findings.".format(
                         len(segs), 2 * len(segs), ARRIVAL_REACH_M)),
            numbers=dict(segments=len(segs), termini_judged=0,
                         pads_with_a_declared_rectangle=len(pads))))
    if not pads:
        out.append(Finding(
            check="population", ok=False,
            message=("no live site_pad write record declares its own "
                     "centre, so no destination datum and no seat rectangle "
                     "exists to judge anything against"),
            numbers=dict(pads=0)))
    if deferred:
        out.append(Finding(
            check="population", ok=True,
            message=("{} road terminus(es) meet ANOTHER ROAD, not a "
                     "settlement: their partner is another built segment's "
                     "terminus within {:.1f} m, so the question is "
                     "junction spread and `junction.py` measures it against "
                     "the same slide limit. Deferred, not dropped: "
                     "{}{}".format(
                         len(deferred), JUNCTION_TOL_M,
                         "; ".join(f"{n} {e} at ({x:.0f}, {z:.0f}) meets "
                                   f"{len(m)}" for n, e, x, z, m
                                   in deferred[:5]),
                         " ..." if len(deferred) > 5 else "")),
            numbers=dict(road_junction_termini=len(deferred),
                         junction_tol_m=JUNCTION_TOL_M,
                         owned_by="accept/sections/roads.py + junction.py",
                         at=sorted({(round(x), round(z))
                                    for _n, _e, x, z, _m in deferred}))))
    if extremity:
        out.append(Finding(
            check="population", ok=True,
            message=("{} road terminus(es) end at a geography EXTREMITY of "
                     "this seed - the farthest inland-safe sample of its "
                     "sector, a road's end by design with nothing built "
                     "there to arrive on - so each arrives on the cape's "
                     "own ground, which the roads section walks: {}".format(
                         len(extremity),
                         "; ".join(f"{n} {e} at ({x:.1f}, {z:.1f}) is "
                                   f"{g:.2f} m from {nid}"
                                   for n, e, x, z, (nid, g) in extremity))),
            numbers=dict(extremity_termini=len(extremity),
                         depends_on=list(EXTREMITY_END_DEPENDS_ON),
                         reach_m=ARRIVAL_REACH_M,
                         at=[(n, e, round(x, 1), round(z, 1), nid, g)
                             for n, e, x, z, (nid, g) in extremity])))
    if district:
        out.append(Finding(
            check="population", ok=True,
            message=("{} road terminus(es) end INSIDE the declared district "
                     "of the site their plan routes them to, on the open "
                     "ground no pad holds, past {:.0f} m of any building - "
                     "a road arrives at a town's district, not at one "
                     "building - so each arrives on the district's own "
                     "ground, which the roads section walks, and is never "
                     "judged against a building it was not routed to "
                     "(nearest named): {}".format(
                         len(district), ARRIVAL_REACH_M,
                         "; ".join(f"{n} {e} at ({x:.1f}, {z:.1f}) is "
                                   f"{d['node_gap_m']:.1f} m inside "
                                   f"{d['site']}'s {d['radius_m']:g} m, "
                                   f"{d['gap']:.2f} m from "
                                   f"{d['pad']['name']}"
                                   for n, e, x, z, d in district))),
            numbers=dict(district_termini=len(district),
                         depends_on=list(PLANNED_END_DEPENDS_ON),
                         at=[(n, e, round(x, 1), round(z, 1), d["site"],
                              d["pad"]["name"], round(d["gap"], 2))
                             for n, e, x, z, d in district])))
    if unmeasured:
        shown = "; ".join(f"{n} {e} at ({x:.1f}, {z:.1f})"
                          for n, e, x, z in unmeasured)
        out.append(Finding(
            check="terminus_unmeasured", ok=False,
            message=("{} road terminus(es) of {} meet NOTHING this gate can "
                     "read: no pad declaring a datum within {:.0f} m, "
                     "nothing standing with a walkable top, and no other "
                     "built segment's terminus within {:.1f} m either - so "
                     "they are not junctions and their arrival is "
                     "UNMEASURED: {}. A road that arrives nowhere "
                     "measurable is how a carriageway came to stop 4.0 m "
                     "short of the village it was built for.".format(
                         len(unmeasured), 2 * len(segs), ARRIVAL_REACH_M,
                         JUNCTION_TOL_M, shown)),
            where=(unmeasured[0][2], unmeasured[0][3]),
            measured=float(len(unmeasured)), limit=0.0,
            numbers=dict(unmeasured_termini=len(unmeasured),
                         # 1C, for the same reason as the envelope arm: a
                         # road that meets nothing is closed by building
                         # something or retiring the road.
                         termination_class="1C_UNSERVED",
                         termini=2 * len(segs),
                         at=[(n, e, round(x, 1), round(z, 1))
                             for n, e, x, z in unmeasured])))
    # THE SPLIT IS PRINTED BESIDE THE VERDICT, WITH ITS DENOMINATOR.  Two
    # populations are now judged by two different checks, and a reader who
    # cannot see how many landed in each cannot tell "the approaches are
    # clean" from "nothing was asked a join question".
    gaps = [t.get("gap_m") for t in termini if isinstance(t, dict)]
    joins = [g for g in gaps if (g or 0.0) <= JOIN_GAP_M]
    approaches = [g for g in gaps if (g or 0.0) > JOIN_GAP_M]
    out.append(Finding(
        check="population", ok=True,
        message=("{} built road segment(s), {} terminus(es) within {:.0f} m "
                 "of a pad that declares its own datum; arrival tolerance "
                 "{:.3f} m = half the MEASURED player capsule radius "
                 "{:.3f} m. Of those {} judged, {} are JOINS (gap <= "
                 "{:.1f} m, the road's last station and its destination "
                 "share a lattice cell, so terminus_datum asks whether the "
                 "road IS at the destination's height) and {} are "
                 "APPROACHES (there is ground in between, so terminus_berm "
                 "and terminus_step own it, including the last 1 m edge "
                 "onto the destination), out of {} termini on the "
                 "network.".format(
                     len(segs), len(termini), ARRIVAL_REACH_M, tol, walker_r,
                     len(termini), len(joins), JOIN_GAP_M, len(approaches),
                     2 * len(segs))),
        numbers=dict(segments=len(segs), termini_judged=len(termini),
                     termini_on_the_network=2 * len(segs),
                     judged_as_join=len(joins),
                     judged_as_approach=len(approaches),
                     join_gap_m=JOIN_GAP_M,
                     tolerance_m=round(tol, 3))))
    return out


def run(ctx) -> list[Finding]:
    I = A.instruments()
    findings: list[Finding] = []
    walker_r, _walker_h = ctx.walker
    tol = walker_r / 2.0
    live = live_records(ctx.ledger)
    pads = declared_pads(live)
    # THE SET THE ONE SANCTIONED WRITE PATH WILL REFUSE OVER, read once.
    # A refusal that proposes a lowering is carried with the answer to
    # "could a terrain write close this at all", because four of this
    # section's nine refusals on gate C named ground the writer owns and
    # none of them said so.  READ, not assumed: on a chain that cannot be
    # parsed the answer is UNKNOWN and every downstream field says so.
    #
    # THE WORLD NAME IS RESOLVED OUTSIDE THE try, AND THAT IS THE WHOLE
    # POINT OF THE LINE.  This read was `ctx.world` for the entire life of
    # this section, and `Ctx` has no `world` attribute - it keeps the
    # parsed arguments and exposes `ctx.args.world` (accept/__main__.py
    # 117-176), so `Ctx.__getattr__` raised `AttributeError: no instrument
    # named 'world'` on EVERY run.  The `except Exception` below then
    # relabelled a WIRING bug as "the chain could not be parsed" and
    # handed `owners=None` to every downstream ownership question.
    # MEASURED, full gate, Ulfsland 245daa7f: all nine settlements
    # refusals carried `repairable_by_terrain_write: null`,
    # `ground_owned_by: null`, `ground_owner_distance_m: null` while
    # `contested_by` and `repair_footprint_m` were populated - half an
    # answer, reported as six confident 1B_CONTESTED rows - and
    # `1A_FORBIDDEN` was 0 of 106 refusals across all eleven sections,
    # i.e. UNREACHABLE IN THE SHIPPED GATE.  The same section run without
    # the harness reported 4 x 1A_FORBIDDEN, every one naming its owner.
    # So the try now covers READING THE CHAIN and nothing else: an
    # argument this section asked for and did not get is a programming
    # error and must crash the section, never degrade its answers.
    world = ctx.args.world
    try:
        forbidden = forbidden_extents(live, ctx.ledger_dir, world)
    except Exception as exc:                              # noqa: BLE001
        findings.append(Finding(
            check="instrument", ok=False, advisory=True,
            message=("the flatten=FORBIDDEN extent set could not be read "
                     f"({type(exc).__name__}: {exc}), so no refusal can "
                     "say whether a terrain write could close it"),
            numbers=dict(live_records=len(live))))
        forbidden = None
    # ONE AUTHORITY for which roads exist: the ROADS section's own loader,
    # imported rather than copied.  The first version of this line asked
    # `instruments` for it, got nothing because it lives in the roads
    # section, and QUIETLY JUDGED ZERO TERMINI - a section that passed
    # Ulfsland by measuring nothing.  That is the exact failure this gate
    # exists to stop, which is why the empty-population refusal below is
    # not optional either.
    from .roads import built_segments                    # noqa: PLC0415
    segs = built_segments(ctx.ledger)

    # --- destinations, and the applied profile from each terminus to one ---
    #
    # WHICH THING a terminus meets is answered by the DECLARATION - a live
    # pad claim within reach, else the nearest walkable top in the census.
    # WHAT HEIGHT that thing is at is NEVER answered by a declaration.  A
    # pad that recorded a `target_y` said what it wanted the ground to
    # become; the engine's applied surface is what it became, and the two
    # part company by metres (Vestvik: 33.99 declared, 35.29 standing).  So
    # the datum is read with `standing_datum` at the first sample INSIDE
    # the destination, and the declaration is carried alongside to be
    # judged against it rather than substituted for it.
    #
    # A terminus with NEITHER a declared destination nor anything standing
    # is UNMEASURED and is reported as such; it is never reported as fine.
    grid = ctx.grid_all()
    # PREFABS KNOWN ONLY TO BOUNDING-BOX PRECISION, off the corpus's own
    # collider-kind column.  A prefab every one of whose parts is a
    # `MeshCollider.sharedMesh.bounds` row is an UPPER BOUND on a shape and
    # must not decide a verdict - see `standing_datum`.
    bounds_only = frozenset(
        nm for nm, parts in ctx.geom.items()
        if parts and all(isinstance(p[-1], str) and p[-1] == "mesh"
                         for p in parts))
    # EVERY BUILT TERMINUS, so a terminus that meets ANOTHER ROAD can be
    # told apart from one that meets nothing.  MEASURED on this world: 19
    # termini have neither a pad within 30 m nor anything standing, and 17
    # of them are ROAD/ROAD JUNCTIONS - the two ends of N0 and N0b at
    # (-134, 1422), five segments meeting at the temple (-56, -34), and so
    # on.  Seventeen of them had ONE build piece within 30 m and it was a
    # `piece_groundtorch`, a lamp.  A road meeting a road is not a
    # settlement arrival and this section cannot judge it; `junction.py`
    # measures exactly that question - the spread between participants at
    # the anchor, against the same slide limit - and it is in this gate.
    # So they are DEFERRED BY NAME, never silently dropped, and what is
    # left is the population this section really owns.
    terminus_at: dict[tuple[str, str], tuple[float, float]] = {}
    for _n, _s in sorted(segs.items()):
        _st = I.resample(_s["nodes"], SAMPLE_M)
        terminus_at[(_n, "A")] = (_st[0][1], _st[0][2])
        terminus_at[(_n, "B")] = (_st[-1][1], _st[-1][2])
    termini, pts, unmeasured, deferred = [], [], [], []
    # THE CAPES THIS SEED'S ROADS END AT (EXTREMITY_END_DEPENDS_ON).  An
    # absent or foreign geography is reported, never read as "no capes".
    extremity = []
    extremities, ext_source = planned_extremities(I.run_scope(ctx)[0])
    if extremities is None:
        findings.append(Finding(
            check="instrument", ok=False, advisory=True,
            message=("this seed's derived geography could not be read "
                     f"({ext_source}), so a road that ends at a cape is "
                     "judged against whatever stands nearest it"),
            numbers=dict(source=ext_source)))
    # WHERE EACH END WAS ROUTED, AND WHAT THE BUILD REFUSED
    # (PLANNED_END_DEPENDS_ON).  An absent or foreign plan is reported,
    # never read as "routed nowhere".
    district, short, refused = [], [], []
    planned, plan_source = planned_ends(I.run_scope(ctx)[0], ctx.ledger,
                                        live)
    if planned is None:
        findings.append(Finding(
            check="instrument", ok=False, advisory=True,
            message=("this seed's road plan could not be read "
                     f"({plan_source}), so a terminus that meets nothing "
                     "cannot be told apart by where it was routed"),
            numbers=dict(source=plan_source)))
    else:
        planned["built"] = {s["seg"] for s in segs.values()}
    for name, s in sorted(segs.items()):
        st = I.resample(s["nodes"], SAMPLE_M)
        for end, (_d, x, z) in (("A", st[0]), ("B", st[-1])):
            tier, payload = terminus_tier(
                name, end, x, z, pads, terminus_at, grid,
                ctx.build_pieces, _walker_h, bounds_only,
                terminus_at[(name, "B" if end == "A" else "A")], segs,
                ctx.geom, extremities, planned)
            if tier == "junction":
                deferred.append((name, end, x, z, payload))
                continue
            if tier == "extremity":
                extremity.append((name, end, x, z, payload))
                continue
            if tier == "refused":
                refused.append((name, end, x, z, payload))
                continue
            if tier == "short":
                short.append((name, end, x, z, payload))
                continue
            if tier == "district":
                district.append((name, end, x, z, payload))
                continue
            if tier == "none":
                unmeasured.append((name, end, x, z))
                continue
            if tier == "declared":
                gap, pad = payload
                tx, tz = pad["cx"], pad["cz"]
                datum, declared = None, pad["datum"]
                probe = INSIDE_PROBE_M
                dest = f"pad {pad['name']!r}"
                nums = dict(segment=name, destination=pad["name"],
                            pad_seq=pad["seq"], destination_kind="declared")
                # INSIDE MEANS INSIDE.  See the `inside` walk below: arc
                # length past `gap` is not the same claim as "this vertex
                # is in the destination's footprint", and this is the
                # predicate that makes it the same claim.
                _cx, _cz = pad["cx"], pad["cz"]
                _hw, _hd = pad["hw"], pad["hd"]

                def contains(vx, vz, _cx=_cx, _cz=_cz, _hw=_hw, _hd=_hd):
                    return abs(vx - _cx) <= _hw and abs(vz - _cz) <= _hd
            else:
                # A STANDING DESTINATION IS ALREADY A READING.  `level_top`
                # is the walkable face of a solid in the save; there is no
                # declaration to prefer it over and nothing to probe past -
                # walking on INTO the piece would read the ground under a
                # deck and call it a berm.
                gap, tx, tz, datum, who, walkable, considered = payload[:7]
                datum_piece = payload[7] if len(payload) > 7 else None
                declared, probe = None, 0.0
                dest = f"the standing top of {who}"
                nums = dict(segment=name, destination=who,
                            destination_kind="standing",
                            walkable_candidates=walkable,
                            build_pieces_within_reach=considered)

                def contains(_vx, _vz):
                    return True
            # THE VERTEX WALK, not a 1 m resample.  `Heightmap.GetHeight`
            # QUANTISES its query to the nearest lattice vertex - MEASURED
            # at population scale by RoadsRemeasure: 18,609 of 18,610
            # vertex groups hold identical heights, stations up to 1.4 m
            # apart returning the same number to the last decimal.  So a
            # line resampled at 1 m along an arbitrary bearing produces
            # CONSECUTIVE SAMPLES THAT SHARE A VERTEX (13.11 % of the road
            # population), whose step is 0.000 BY CONSTRUCTION - a cliff
            # between them is invisible - and OTHER pairs whose vertices
            # are 1.414 m apart while the limit is computed for 1 m.
            #
            # This walks the lattice instead: each sample is snapped, the
            # consecutive duplicates COLLAPSE rather than reading zero, and
            # each surviving pair carries its OWN separation so the slide
            # limit is computed from the distance that actually separates
            # the two readings.  One label is one question, via
            # instruments.gpoint.
            n = max(int(round((gap + probe) / SAMPLE_M)), 0)
            ux, uz = tx - x, tz - z
            L = math.hypot(ux, uz)
            if L < 1e-9:
                # A TERMINUS STANDING ON ITS OWN DESTINATION'S CENTRE HAS
                # NO DIRECTION TO PROBE IN, and a zero vector silently
                # collapses the whole walk onto ONE sample.  MEASURED: S8
                # and T10 both end exactly on wt-south's pad centre
                # (536.0, 42.0), so every probe sample was that sample, it
                # sits inside the watchtower's own stone column, and two
                # termini came back with no reading at all.  The walker
                # arrives ALONG THE ROAD, so the road's own last bearing is
                # the direction, and it is defined for every segment with
                # two stations.
                px, pz = (st[1][1], st[1][2]) if end == "A" \
                    else (st[-2][1], st[-2][2])
                ux, uz = x - px, z - pz
                L = math.hypot(ux, uz)
            L = L or 1.0
            ux, uz = ux / L, uz / L
            if tier == "declared":
                # THE PROBE IS `INSIDE_PROBE_M` PAST WHERE THE WALK ENTERS
                # THE FOOTPRINT, not past `gap`.  `gap` is the distance to
                # the NEAREST point of the rectangle and the walk heads for
                # its CENTRE, so off a corner the two part company and a
                # probe sized off `gap` can end before it is inside at all -
                # an empty `inside` read as "nothing standing".  On drive 2
                # the two agree to 0.2 m for all 7 declared termini; this
                # makes it a property of the walk rather than of the layout.
                entry = ray_entry_m(x, z, ux, uz, _cx, _cz, _hw, _hd)
                if entry is not None and entry > gap:
                    n = max(n, int(round((entry + probe) / SAMPLE_M)))
            idx = len(termini)
            verts, inside = probe_walk(x, z, ux, uz, n, gap, contains,
                                       I.snap)
            # EVERY VERTEX ON THE WALK, PLUS BOTH CORNERS OF EVERY DIAGONAL
            # TRANSITION.  The corners are where a step hides: a walker
            # crossing a cell diagonally crosses two 1 m mesh edges through
            # one of them, and a corner nobody read is a transition nobody
            # judged.  They are requested here rather than inferred.
            need = list(verts)
            for k in range(len(verts) - 1):
                (ax, az), (bx, bz) = verts[k], verts[k + 1]
                if abs(ax - bx) > 1e-9 and abs(az - bz) > 1e-9:
                    need += [(bx, az), (ax, bz)]
            seen_v = set()
            for k, (vx, vz) in enumerate(need):
                if (vx, vz) in seen_v:
                    continue
                seen_v.add((vx, vz))
                pts.append((f"arr{idx}|{k}", vx, vz))
            termini.append(dict(segment=name, end=end, x=x, z=z,
                                datum=datum, declared_datum=declared,
                                gap_m=gap, dest=dest, samples=len(verts),
                                vertices=verts, inside=inside, nums=nums,
                                forbidden_extents=forbidden,
                                datum_piece=(datum_piece if nums.get(
                                    "destination_kind") == "standing"
                                    else None)))
    # THE GROUND A SHORT END WOULD HAVE TO CROSS, read in the same batch.
    for j, (_n, _e, x, z, d) in enumerate(short):
        d["walk"] = short_walk(x, z, d["pad"], I.snap)
        pts.extend((f"short{j}|{k}", vx, vz)
                   for k, (vx, vz) in enumerate(d["walk"]))
    bed = {}
    if pts:
        try:
            bed = ctx.ground(pts)
        except Exception as exc:                          # noqa: BLE001
            findings.append(Finding(
                check="instrument", ok=False,
                message=(f"the ground instrument refused for the "
                         f"{len(pts)} arrival samples "
                         f"({type(exc).__name__}: {exc}); no arrival on "
                         "this world is measured"),
                numbers=dict(points=len(pts))))
    for j, (n, e, x, z, d) in enumerate(short):
        findings.append(short_of_destination(
            n, e, x, z, d, d["walk"],
            [bed.get(f"short{j}|{k}") for k in range(len(d["walk"]))],
            I.WATER_LEVEL))
    for n, e, x, z, d in refused:
        findings.append(refused_destination(n, e, x, z, d))
    # AND WHO ELSE ALREADY WROTE THE GROUND A REPAIR WOULD CUT - THE
    # SECOND HALF OF "WHO OWNS THIS GROUND", AND THE HALF NO GUARD ASKS.
    # `writer`'s flatten test says whether a cut is ALLOWED and has no
    # opinion whatever on whose material it is.  A VERTEX CAN BE WRITABLE
    # AND STILL NOT BE YOURS.
    #
    # READ FROM THE LEDGER, NOT FROM A SECOND HEIGHT INSTRUMENT.  The
    # first version of this asked `ctx.generated` for the seed bed at
    # every arrival vertex and subtracted, which measures the DEPTH of
    # written material but not WHO wrote it - and it would have cost this
    # wave a patchscan launch per unwarmed 128 m tile on a lock four
    # agents are queueing for, MEASURED: 12 tiles warm out of the ~50 the
    # termini span.  `write_footprints` + `WriteIndex` answer the better
    # question for nothing, off the chain this run is already reading, and
    # they NAME the record.
    from .harbours import WriteIndex, write_footprints  # noqa: PLC0415
    try:
        writes = WriteIndex(write_footprints(ctx.ledger), margin_max=8.0)
    except Exception as exc:                              # noqa: BLE001
        findings.append(Finding(
            check="instrument", ok=False, advisory=True,
            message=("the recorded terrain_write footprints could not be "
                     f"read ({type(exc).__name__}: {exc}), so no refusal "
                     "can say whether the ground it proposes to cut "
                     "belongs to another actor"),
            numbers=dict(ledger=str(ctx.ledger))))
        writes = None
    for i, t in enumerate(termini):
        # the PROFILE, for the datum and the berm, is the walk in order;
        # the VERTEX MAP, for the edge walk, is every vertex and corner
        hbv = {}
        for (lbl, vx, vz) in pts:
            if lbl.startswith(f"arr{i}|") and lbl in bed:
                hbv[(vx, vz)] = bed[lbl]
        t["heights_by_vertex"] = hbv
        t["write_index"] = writes
        t["profile"] = [hbv.get(v) for v in t["vertices"]]
        if t["datum"] is None:
            # THE DATUM IS READ HERE, off the standing geometry, and a
            # destination whose ground could not be read has NO datum -
            # which is a refusal, not a fallback to the declaration.  The
            # declaration is exactly what cannot be trusted.
            why: dict = {}
            y, what, at = standing_datum(t["inside"], hbv, grid, _walker_h,
                                         bounds_only, ctx.geom, why=why)
            if y is None:
                # WHICH OF THE THREE STATES, AND ITS CLASS - never UNKNOWN.
                findings.append(unstood_destination(t, why))
                t["unreadable"] = True
                continue
            t["datum"], t["datum_source"], t["datum_at"] = y, what, list(at)
            t["datum_piece"] = why.get("datum_piece")

    # ONE DESTINATION, EVERY READING OF IT, BEFORE ANY OF THEM IS JUDGED.
    # A pad is a RECTANGLE and its declaration is ONE NUMBER, so a
    # declaration that disagrees with the ground at one terminus's
    # footfall is TWO different defects and the finding has to say which:
    # a declaration nobody stands on anywhere (fix the DECLARATION), or a
    # pad that is not level across its own interior (fix the PAD).
    # MEASURED on Ulfsland, and it is the second: vestvik-hut-2 declares
    # 34.370 and is met by two termini - R4 A's footfall reads 34.370,
    # EXACTLY the declaration, and R3 B's reads 35.214, 0.844 m higher,
    # two points inside one 10.4 x 10.4 m pad.  The declaration is stood
    # on; the pad is not flat.  Printing sites.yaml's district-figure
    # story over that would name the wrong cause and send a builder to
    # edit a number that is right.
    by_pad: dict[str, list[tuple]] = defaultdict(list)
    for t in termini:
        if t.get("declared_datum") is not None and t.get("datum") is not None:
            by_pad[t["nums"]["destination"]].append(
                (t["segment"], t["end"], t["datum"]))
    for t in termini:
        if t.get("unreadable"):
            continue
        t["pad_readings"] = by_pad.get(t["nums"].get("destination"), [])
        findings += check_arrival(t, t["profile"], tol, I.STEP_LIMIT_PER_M)
    # THE TRANSITIONS THE GROUND INSTRUMENT COULD NOT PLACE, on the page with
    # their denominator.  Over the limit as read and by less than one
    # reading quantum, so neither refused on the rounding nor passed in
    # silence - see `instruments.GROUND_QUANTUM_M` for drive 2's two.
    band = [(t["segment"], t["end"], r) for t in termini
            for r in (t.get("step_within_resolution") or ())]
    findings.append(Finding(
        check="population", ok=True,
        message=("{} lattice transition(s) on the judged approaches read "
                 "OVER the {:.7f} m slide limit by no more than the ground "
                 "instrument's {:g} m quantum, so this instrument cannot say "
                 "which side of the limit they are on: not refused, not "
                 "dropped{}".format(
                     len(band), I.STEP_LIMIT_PER_M, I.GROUND_QUANTUM_M,
                     "" if not band else " - " + "; ".join(
                         f"{s} {e} at {r['at']} +{r['over_by_m']:.6f}"
                         for s, e, r in band[:6]))),
        measured=float(len(band)), limit=0.0,
        numbers=dict(within_resolution=len(band),
                     resolution_m=I.GROUND_QUANTUM_M,
                     at=[dict(segment=s, end=e, **r) for s, e, r in band])))

    # --- seated on generated content --------------------------------------
    markers, mstat = _markers(ctx)
    if not mstat.get("available"):
        findings.append(Finding(
            check="instrument", ok=False,
            message=("the seed's location dump is not readable, so no seat "
                     "can be tested against generated content. Re-run "
                     "tools/seedscan/run_locscan.sh. A world whose "
                     "generated content cannot be located is unmeasured, "
                     "not clean."),
            numbers=dict(dump=mstat.get("path"))))
    else:
        build = ctx.build_pieces
        pieces = [(n, x, z) for (n, x, _y, z, *_q) in ctx.census.rows
                  if n in build]
        decl = declared_placements(live, ctx.ledger_dir / "blobs")
        # A SHORT DECLARATION INDEX MUST BE LOUD, NOT SMALL.  Everything
        # this check calls generated content rests on the index being
        # complete, and a quietly short one is what called 3,417 pieces of
        # our own buildings foreign and refused five sudrvik pads.  So the
        # index is held to the plans' OWN declared sizes, and a plan that
        # will not read back at its `command_count` makes the ownership
        # question UNMEASURED rather than answered against a partial read.
        acct = placement_accounting(live, ctx.ledger_dir / "blobs")
        if acct["short"] or acct["over"]:
            findings.append(Finding(
                check="declaration_index", ok=False,
                message=("{} of {} live spawn_plan record(s) do not read "
                         "back at their own command_count ({} short, {} "
                         "over), so the declaration index this check "
                         "decides ownership with is INCOMPLETE and every "
                         "seat verdict below rests on a partial read. "
                         "Worst: {}. A blob that disagrees with its own "
                         "record is unreadable, not small.".format(
                             len(acct["short"]) + len(acct["over"]),
                             acct["plans"], len(acct["short"]),
                             len(acct["over"]),
                             "; ".join(
                                 "seq {} {!r} declared {} parsed {}".format(
                                     d["seq"], d["site"], d["declared"],
                                     d["parsed"])
                                 for d in (acct["short"] + acct["over"])[:3])
                             or "none")),
                measured=float(len(acct["short"]) + len(acct["over"])),
                limit=0.0,
                numbers=dict(plans=acct["plans"], exact=acct["exact"],
                             short=acct["short"][:20],
                             over=acct["over"][:20])))
        cell = 4.0
        dg = defaultdict(list)
        for k, (pf, x, z) in enumerate(decl):
            dg[(int(x // cell), int(z // cell))].append(k)

        def is_declared(pf, x, z, t=DECLARED_TOL_M):
            for ix in range(int((x - t) // cell), int((x + t) // cell) + 1):
                for iz in range(int((z - t) // cell),
                                int((z + t) // cell) + 1):
                    for k in dg.get((ix, iz), ()):
                        d = decl[k]
                        if d[0] == pf and abs(d[1] - x) <= t \
                                and abs(d[2] - z) <= t:
                            return True
            return False

        # ONE AUTHORITY FOR "WHOSE IS THIS BUILDING", imported rather than
        # copied, the same way this section takes the road population from
        # the roads section.  `ledger_anchors` and `ANCHOR_MARGIN_M` are
        # the structures section's own ownership instruments, and a second
        # implementation here would be a fifth divergence waiting to
        # happen.
        from .structures import (ANCHOR_MARGIN_M,          # noqa: PLC0415
                                 ledger_anchors)
        anchors = ledger_anchors(ctx.ledger)
        findings += check_seat_on_generated(pads, pieces, markers,
                                            is_declared, anchors,
                                            ANCHOR_MARGIN_M)
        findings.append(Finding(
            check="population", ok=True,
            message=("{} live site pad claim(s) resolved through "
                     "writer._pad_claims, each with a rectangle and a "
                     "DECLARED datum ({} more claim a rectangle but their "
                     "write recorded no target_y); {} build-piece ZDOs in "
                     "the save against {} live LocationProxy markers, {} of "
                     "which the seed's own dump can identify and {} of "
                     "which it cannot (those carry the world's worst "
                     "declared radius, {:.0f} m); {} declared placements "
                     "read from the ledger's wire lines and spawn-plan "
                     "blobs, which is {:.1f} % of the build pieces in the "
                     "save, so OWNERSHIP is decided by the {} anchored "
                     "plan(s) on the chain and not by that index "
                     "alone".format(
                         len(pads), pads[0]["pads_without_centre"] if pads
                         else 0, len(pieces), mstat["markers"],
                         mstat["identified"], mstat["unidentified"],
                         mstat["worst_radius_m"], len(decl),
                         100.0 * len(decl) / max(len(pieces), 1),
                         len(anchors))),
            numbers=dict(pads=len(pads), pads_without_a_datum=(
                pads[0]["pads_without_centre"] if pads else 0),
                pieces=len(pieces), **mstat,
                declared_placements=len(decl),
                declared_placement_coverage_pct=round(
                    100.0 * len(decl) / max(len(pieces), 1), 2),
                anchored_plans=len(anchors),
                anchor_margin_m=ANCHOR_MARGIN_M)))

    findings += check_population(segs, termini, pads, tol, walker_r,
                                 str(ctx.ledger), unmeasured, deferred,
                                 extremity, district)
    # THE SPLIT THE OPERATOR ACTUALLY HAS TO READ, BESIDE THE VERDICT.
    # A refusal count is a work queue and an operator decision list added
    # together with no way back, and this gate has been printing the sum.
    # Every refusal above carries its own `termination_class`; this is the
    # census of them WITH ITS DENOMINATOR, so "N refusals, M ACTIONABLE"
    # is readable without opening a single finding.
    # EVERY ROW THAT CARRIES NO CLASS OF ITS OWN IS CLASSED HERE, off this
    # section's own remedy families, by THE ONE implementation that does it
    # for the board - so the census below is the board's, not a count with
    # an `UNCLASSIFIED` hole the harness fills in afterwards.
    from .. import termination as T                       # noqa: PLC0415
    T.backstop("settlements", findings)
    hard = [f for f in findings if not f.ok and not f.advisory]
    # AND WHOSE BLOCKER, off this world's ledger: a road stepping onto our
    # own pad, or onto a piece our own plan stood there, is the factory's
    # work, owned by the step that built against the other.  MEASURED on
    # drive 3: 5 termini contesting our own complete-station-hub pad (seq
    # 631) were 1B, as if the pad were a stranger's.
    ownership = T.own_blockers(hard, T.Placements.read(ctx.ledger),
                               "settlements")
    by_class = Counter(
        (f.numbers or {}).get("termination_class") or "UNCLASSIFIED"
        for f in hard)
    findings.append(Finding(
        check="termination", ok=True,
        message=("{} refusal(s), {} ACTIONABLE - a repair exists, the "
                 "writer permits it, and it destroys nothing. The rest "
                 "cannot terminate, and the reason is MEASURED rather "
                 "than curated: {}. 1A the remedy is forbidden by its own "
                 "writer; 1B the writer permits it and it overwrites "
                 "another actor\'s recorded terrain_write; 1C no tool in "
                 "this tree performs the remedy at all. A count that adds "
                 "those to the actionable ones answers a question nobody "
                 "has.".format(
                     len(hard), by_class.get("ACTIONABLE", 0),
                     ", ".join("{} {}".format(k, v)
                               for k, v in sorted(by_class.items()))
                     or "none")),
        numbers=dict(refusals=len(hard),
                     actionable=by_class.get("ACTIONABLE", 0),
                     by_termination_class=dict(sorted(by_class.items())),
                     blocker_ownership=ownership,
                     # THE CHAIN THIS SPLIT WAS READ FROM, BESIDE THE
                     # SPLIT.  `--world-src` PINS THE SAVE AND NOT THE
                     # LEDGER, and this census is the one number in the
                     # section that is a function of BOTH: the beds come
                     # off the frozen save, the owners and the contesting
                     # writes come off the LIVE chain.  MEASURED by
                     # HarbourClose within minutes of the field existing -
                     # three of its 1B rows named `regrade_hc_unowned_
                     # spill2 seq 5419`, ITS OWN LANDING, while the bed
                     # under them was read off a save frozen before it.
                     # Correct behaviour, and unreadable without this
                     # line: a before/after that moves only because the
                     # chain moved is indistinguishable from one that
                     # moved because the world did.
                     ledger_md5=_ledger_md5(ctx.ledger),
                     ledger_lines=_ledger_lines(ctx.ledger))))
    return findings


# ---------------------------------------------------------------------------
# THE SELFTEST
# ---------------------------------------------------------------------------
def _term(gap: float, datum: float, seg="R2-ulfsgard-harbourb", end="B",
          x=1211.5, z=561.5):
    return dict(segment=seg, end=end, x=x, z=z, datum=datum, gap_m=gap,
                dest="pad 'sudrvik-fjordhead-12'", samples=int(gap) + 1,
                nums=dict(segment=seg, destination="sudrvik-fjordhead-12"))


def _case_arrival_on_the_datum():
    """The state after the 13th terrace: R2's last station and the roadhead
    pad are both at 38.219 and the ground between is the pad's own apron.
    The ledger's own record of it reads arrival_error_m 0.0."""
    t = _term(0.5, 38.219)
    return check_arrival(t, [38.219, 38.219], 0.245, 0.7813)


def _case_arrival_berm():
    """THE DEFECT, from the record that names it: R2 was profiled to the
    floor of pad 12 at 38.219 and its last station landed 4.0 m OUTSIDE
    that pad, leaving a 1.23 m berm.  BOTH ENDPOINTS AGREE - the road is at
    38.219 and the pad is at 38.219 - so an endpoint comparison reports a
    perfect arrival, and this case asserts that too."""
    t = _term(4.0, 38.219)
    profile = [38.219, 38.90, 39.449, 38.90, 38.219]
    assert abs(profile[0] - t["datum"]) < 1e-9, (
        "the two ENDPOINTS agree exactly - that is why every previous "
        "check passed")
    return check_arrival(t, profile, 0.245, 0.7813)


def _case_a_berm_repair_sized_off_the_worst_leaves_the_next_one():
    """A FINDING THAT PUBLISHES A WORST AND NO SET PRICES A REPAIR THAT
    CANNOT TERMINATE.

    `terminus_berm` is a `max()` over the whole profile and used to publish
    `berm_m` and `at_m_along` and nothing else.  The widest repair derivable
    from that finding is ONE SAMPLE - cut the named station under the cap -
    after which the check re-reads the same profile and reports THE SAME
    REFUSAL AT THE SECOND-HIGHEST SAMPLE'S COORDINATES.  Repeat forever.

    MEASURED on this world, and it is why the case is here rather than
    invented: R3 B's berm is 0.643 m at 6 m along and R4 A's is 0.459 m at
    4 m along, BOTH onto `vestvik-hut-2`, and 49 of the 167 lattice vertices
    inside the two 6 m travelled ways stand above `ends + tol`.  A repair
    sized off the two named stations would have taken 2 of 49.

    THE CASE IS THE REPAIR, RUN.  It cuts exactly what the finding names,
    re-asks the check, and REQUIRES it to still refuse -- which is the proof
    that the old finding was unactionable, not merely terse.  And it then
    requires the finding to NAME ITS POPULATION, so the repair that does
    terminate is derivable.
    """
    t = _term(4.0, 38.219)
    profile = [38.219, 38.90, 39.449, 39.100, 38.219]
    fs = check_arrival(t, profile, 0.245, 0.7813)
    berm = [f for f in fs if f.check == "terminus_berm"]
    assert len(berm) == 1, [f.check for f in fs]
    n = berm[0].numbers

    # (1) THE REPAIR THE WORST-ONLY FINDING PRICES: cut the named station
    #     to the cap and nothing else.
    cap = round(max(profile[0], t["datum"]) + 0.245, 3)
    one = list(profile)
    one[n["at_m_along"]] = cap
    again = check_arrival(t, one, 0.245, 0.7813)
    still = [f for f in again if f.check == "terminus_berm"]
    assert still, (
        "the one-sample repair closed the berm, so this fixture no longer "
        "encodes the defect: give it a second sample over the cap")
    assert still[0].numbers["at_m_along"] != n["at_m_along"], (
        "the refusal must move to a DIFFERENT station, which is what makes "
        "it non-terminating rather than merely unfixed")

    # (2) THE POPULATION, WHICH IS WHAT MAKES A TERMINATING REPAIR
    #     DERIVABLE.  Both samples over the cap, each with its own station.
    pop = n.get("repair_population")
    assert pop is not None, (
        "terminus_berm publishes no repair population: a worst and a count "
        "is not a set, and the widest repair derivable from it is one "
        "sample wide")
    want = sorted(i for i, y in enumerate(profile) if y is not None
                  and y > cap)
    assert sorted(r["at_m_along"] for r in pop) == want, (pop, want)
    assert n["samples_over_cap"] == len(want) and n["samples_read"] == 5

    # (3) AND THE SET IS SUFFICIENT, BOTH SIGNS.  Cutting every named
    #     sample closes the finding; a check whose population were too WIDE
    #     would be a different defect and this arm would not catch it.
    whole = list(profile)
    for r in pop:
        whole[r["at_m_along"]] = cap
    done = check_arrival(t, whole, 0.245, 0.7813)
    assert not [f for f in done if f.check == "terminus_berm"], done
    return berm


def _neuter_berm_worst_without_its_set():
    """RESTORE THE DEFECT: `terminus_berm` as it stood all session --
    `berm_m` and `at_m_along`, a worst and no set."""
    import accept.sections.settlements as mod                # noqa: PLC0415
    real = mod.check_arrival

    def worst_only(term, profile, tol, step_limit, *a, **k):
        out = real(term, profile, tol, step_limit, *a, **k)
        for f in out:
            if f.check in ("terminus_berm", "terminus_step"):
                for key in ("repair_population", "samples_over_cap",
                            "samples_read", "cap_m", "ends_m",
                            "transitions_over_limit", "transitions_judged"):
                    f.numbers.pop(key, None)
        return out

    mod.check_arrival = worst_only

    def undo():
        mod.check_arrival = real
    return undo


def _case_unreachable_candidate_is_unmeasured():
    """LIVE ON ULFSLAND, and the reason the envelope exists: the standing
    fallback offered N2's terminus a TreasureChest_trollcave whose top is
    y 5066.017 (Valheim parks cave interiors about 5 km up).  The arrival
    error came out -5008.034 m.  The right answer is not a 5 km miss, it is
    "no measurable destination", and the case requires that wording."""
    t = dict(segment="road_N2_shelf_mid1", end="B", x=-650.0, z=5402.0,
             datum=5066.017, gap_m=28.0,
             dest="the standing top of TreasureChest_trollcave", samples=2,
             nums=dict(segment="road_N2_shelf_mid1",
                       destination="TreasureChest_trollcave",
                       destination_kind="standing"))
    fs = check_arrival(t, [57.983, 57.9], 0.245, 0.7813)
    assert all(f.check == "terminus_unmeasured" for f in fs), (
        "an unreachable candidate must be reported as UNMEASURED, never as "
        "a 5,008 m arrival error")
    return fs


def _case_reachable_standing_top_is_judged():
    """A deck 0.920 m above the carriageway over a 15.0 m gap is well
    inside what a walker covers, so it IS the thing the road meets.
    R4-vestvik-harbour, live.

    WHAT THIS CASE DISCRIMINATES is REACHABLE from UNMEASURED - the
    envelope that keeps a trollcave chest 5,008 m off the carriageway
    from becoming a destination.  It used to assert `terminus_datum`,
    which is no longer the check that owns a 15.0 m gap: since the
    join/approach split the ground between is walked by `terminus_berm`
    and `terminus_step` and the datum test asks only about a JOIN.  The
    assertion moved to the thing the case is actually about, because
    re-pinning it to whichever verdict happens to come out would make it
    a decoration.
    """
    t = dict(segment="road_R4_vestvik_harbour", end="A", x=-912.0, z=1902.0,
             datum=34.370, gap_m=15.0,
             dest="the standing top of wood_floor", samples=2,
             nums=dict(segment="road_R4_vestvik_harbour",
                       destination="wood_floor",
                       destination_kind="standing"))
    fs = check_arrival(t, [35.290, 35.0], 0.245, 0.7813)
    assert not any(f.check == "terminus_unmeasured" for f in fs), (
        f"0.920 m over a 15.0 m gap is inside the walker's own envelope "
        f"(15.0 * 0.7813 + 1.85 = 13.57 m), so this deck IS the thing "
        f"the road meets and the arrival must be MEASURED, not written "
        f"off; got {[f.check for f in fs]}")
    return fs


# `_case_arrival_datum_miss` WAS HERE AND IS DELETED, NOT RE-PINNED.  It
# asserted that S7-stathub-wtnorth's 0.622 m over a 3.0 m gap "is
# walkable, and still not an arrival" - which is precisely the claim the
# operator's join/approach ruling overturned.  Re-pointing it at a join
# would have been a second copy of
# `a_join_is_judged_an_approach_is_walked`, which already asserts the
# datum refusal on ALL THREE of this world's live joins with their real
# numbers (N0 A +0.553, S1 B +0.445, S12 B +1.206) and is neuter-proven.


def _case_declared_datum_nobody_stands_on():
    """THE CLASS, from the world that named it.  `settlements/sites.yaml`
    publishes Vestvik's `pad_y` as 33.99; the u-vestvik portal_wood stands
    at (-915.0, 35.29, 1883.4) on vestvik-hall-1's levelled pad and the
    save-derived applied ground under it reads 35.2900 against a generated
    36.6006.  The DECLARATION IS 1.30 m LOW, and R3 was routed to 35.29
    while this table said 33.99.

    Judged the old way - road against DECLARATION - a carriageway landing
    on 33.99 was a perfect arrival, which is exactly how eleven termini on
    this world came to sit on ground nobody stands on.  Judged against the
    READING the same road is 1.300 m below the village street, and the
    declaration is named as the defect.
    """
    t = dict(segment="road_R3_n0bjunction_vestvik", end="B", x=-912.9,
             z=1896.5, datum=35.290, declared_datum=33.990, gap_m=8.3,
             dest="pad 'vestvik-hut-2'", samples=2,
             datum_source="the applied ground",
             datum_at=[-915.0, 1883.0],
             nums=dict(segment="road_R3_n0bjunction_vestvik",
                       destination="vestvik-hut-2",
                       destination_kind="declared"))
    fs = check_arrival(t, [33.990, 34.6], 0.245, 0.7813)
    kinds = {f.check for f in fs}
    assert "destination_datum_unstood" in kinds, (
        "a destination whose DECLARED datum is 1.30 m off the ground "
        "standing there must be refused; believing the declaration is the "
        "defect")
    # THE ARRIVAL HALF IS NOT THIS CASE'S SUBJECT AND NO LONGER ITS
    # VERDICT.  This terminus sits 8.3 m out, so since the join/approach
    # split the ground between it and the pad is walked by the berm and
    # step checks and `terminus_datum` asks only about a JOIN.  What this
    # case owns is the DECLARATION - a pad claiming a height nothing
    # stands on - and it is asserted above, on its own.  Asserting the
    # arrival here as well would pin this case to a second check's
    # population and make it go red whenever that population moves.
    assert "terminus_datum" not in kinds, (
        f"an 8.3 m gap is an APPROACH; the datum test must not come back "
        f"in by another door; got {sorted(kinds)}")
    return fs


def _case_declaration_that_agrees_is_accepted():
    """The same terminus once the declaration and the ground are the same
    number.  The check is not "a pad declared something", it is "the
    declaration and the reading agree"."""
    t = dict(segment="road_R3_n0bjunction_vestvik", end="B", x=-912.9,
             z=1896.5, datum=35.290, declared_datum=35.290, gap_m=8.3,
             dest="pad 'vestvik-hut-2'", samples=2,
             datum_source="the applied ground",
             nums=dict(segment="road_R3_n0bjunction_vestvik",
                       destination="vestvik-hut-2",
                       destination_kind="declared"))
    return check_arrival(t, [35.290, 35.29], 0.245, 0.7813)


class _Slab:
    """The smallest thing `_approach.standing_surface` will accept as a
    walkable top: a named solid with verts, a level face and a footprint."""

    def __init__(self, name, top, hw=2.0, cx=0.0, cz=0.0, hd=None,
                 thick=0.5):
        self.name, self._top, self._hw = name, top, hw
        self._hd = hw if hd is None else hd
        self._cx, self._cz = cx, cz
        self.ymin, self.ymax = top - thick, top
        self.verts = [(cx - hw, cz - self._hd), (cx + hw, cz + self._hd)]
        # The axis-aligned extent `_nearest_standing` reports a destination
        # by, so the chooser and the datum reader can be exercised on ONE
        # fixture rather than two that could drift apart.
        self.xmin, self.xmax = cx - hw, cx + hw
        self.zmin, self.zmax = cz - self._hd, cz + self._hd

    def hull(self):
        return [(self._cx - self._hw, self._cz - self._hd),
                (self._cx + self._hw, self._cz - self._hd),
                (self._cx + self._hw, self._cz + self._hd),
                (self._cx - self._hw, self._cz + self._hd)]

    def level_top(self):
        return self._top

    def covers(self, x, z):
        return (abs(x - self._cx) <= self._hw
                and abs(z - self._cz) <= self._hd)


class _Grid:
    def __init__(self, solids):
        self._s = solids

    def near(self, _x, _z, _r):
        return self._s


def _case_standing_datum_is_the_reading_not_the_plan():
    """THE RESOLVER, in both directions, on one destination.

    The pad declared 33.99.  The applied ground four metres inside its own
    edge reads 35.29 and a hall deck covering that sample tops out at
    35.60; a roof 6.00 m up also covers it.  The datum must be 35.60 - the
    highest thing a walker can actually stand on - and never 33.99 (the
    plan) and never 41.29 (the roof, which he cannot reach).
    A road does not arrive on a WALL either, so the third leg is wt-south.
    """
    at = I_SNAP = (-915.0, 1883.0)
    hbv = {at: 35.290}
    grid = _Grid([_Slab("wood_floor", 35.60, 4.0, at[0], at[1]),
                  _Slab("wood_roof", 41.29, 4.0, at[0], at[1])])
    y, what, where = standing_datum([at], hbv, grid, 1.8)
    assert abs(y - 35.60) < 1e-9, f"datum must be the reachable deck, got {y}"
    assert "wood_floor" in what and where == I_SNAP
    # AND THE OTHER DIRECTION: a destination whose ground was never read
    # has NO datum.  Falling back to the declaration here is the whole
    # defect, so the resolver must return nothing rather than 33.99.
    none = standing_datum([at], {}, grid, 1.8)
    assert none == (None, None, None), (
        f"an unread destination must have no datum at all, got {none}")
    # AND THE THIRD, WHICH THE LIVE RUN FOUND: wt-south.  S8 and T10 both
    # terminate INSIDE that pad, in the watchtower's own column, MEASURED
    # off the save: stone_floor_2x2 81.660-82.660 (4.0 m2), stone_wall_2x1
    # 82.660-83.660 (2.0 m2), stone_wall_1x1 83.660-84.660 (1.0 m2).  The
    # wall cap at 83.660 is a level face exactly 1.000 m above the floor
    # and passes any area floor, so the first version of this resolver took
    # it and turned two CORRECT arrivals - road 82.660 against a
    # declaration of 82.660 - into 1.000 m misses.  Nothing here is a place
    # a walker stands: the wall is on the floor and another wall is on the
    # wall.  The answer is NO DATUM, and the probe walks to the next
    # sample.
    col = _Grid([_Slab("stone_floor_2x2", 82.66, 0.49, 536.0, 42.0, hd=1.2,
                       thick=1.0),
                 _Slab("stone_wall_2x1", 83.66, 0.49, 536.0, 42.0, hd=1.2,
                       thick=1.0),
                 _Slab("stone_wall_1x1", 84.66, 0.49, 536.0, 42.0, hd=1.2,
                       thick=1.0)])
    inside = [(536.0, 42.0), (537.0, 42.0)]
    hb = {(536.0, 42.0): 82.66, (537.0, 42.0): 82.66}
    blocked = standing_datum([(536.0, 42.0)], hb, col, 1.8)
    assert blocked == (None, None, None), (
        f"a column of walls is not a destination datum, got {blocked}")
    # ONE METRE OUT, where only the floor stands, the floor IS the datum -
    # so the rule refuses a wall without refusing a deck, and the probe
    # WALKS ON from the blocked sample rather than giving up on it.
    both = _Grid(col._s + [_Slab("stone_floor_2x2", 82.66, 0.49, 537.0, 42.0,
                                 hd=1.2, thick=1.0)])
    fy, fwhat, fat = standing_datum(inside, hb, both, 1.8)
    assert abs(fy - 82.66) < 1e-9 and fat == (537.0, 42.0), (
        f"the probe must WALK ON from the blocked sample and read the next "
        f"one, got {fy} ({fwhat}) at {fat}")
    # AND THE FOURTH, WHICH THE SAME LIVE RUN FOUND: a BOUNDING BOX MUST
    # NOT DECIDE THE WORLD.  S7's terminus (152.0, 1194.0) is covered by
    # two `Beech1` solids spanning y 30.92-63.48 - two birches carried as
    # `MeshCollider.sharedMesh.bounds`, 19.08 x 21.50 m and 31.7 m tall.
    # Every sample of the probe is "inside a tree", so the occupancy rule
    # refused the whole approach and S7 came back with no reading.  Tested
    # by RE-RUN with the bound treated as a bound, not by a reason string:
    # the same grid answers 32.420 and NAMES the box it rests on.
    tree = _Slab("Beech1", 62.58, 9.54, 152.0, 1194.0, hd=10.75, thick=31.66)
    bark = _Grid([tree])
    hard = standing_datum([(152.0, 1194.0)], {(152.0, 1194.0): 32.42},
                          bark, 1.8)
    assert hard == (None, None, None), (
        f"a tree's bounding box does block the column when it is believed, "
        f"got {hard} - that is the state this leg exists to change")
    ty, twhat, _t = standing_datum([(152.0, 1194.0)], {(152.0, 1194.0): 32.42},
                                   bark, 1.8, bounds_only={"Beech1"})
    assert abs(ty - 32.42) < 1e-9 and "Beech1" in twhat, (
        f"a blocker known only to bounding-box precision must NAME itself "
        f"and let the reading through, got {ty} ({twhat})")
    return [Finding(check="standing_datum", ok=True,
                    message=("the destination datum is read off standing "
                             "geometry: 35.600 m deck taken, 41.290 m roof "
                             "refused as out of the walker's reach, "
                             "wt-south's 83.660 m wall cap refused because "
                             "a walker does not fit above it, the floor "
                             "one metre out taken at 82.660, S7's 32.420 m "
                             "let through with the Beech1 bounding box "
                             "NAMED rather than believed, and an unread "
                             "destination yielding no datum rather than "
                             "the plan's 33.990"),
                    numbers=dict(datum_m=35.60, roof_m=41.29, wall_m=83.66,
                                 floor_m=82.66, bounds_only_datum_m=32.42,
                                 declared_m=33.99))]


def _case_arrival_step():
    """A 1.0 m riser across one 1 m lattice EDGE at the join: over the
    walker's own slide limit, which is the only bar there is."""
    t = dict(_term(3.0, 38.219), samples=4,
             vertices=[(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0)],
             heights_by_vertex={(0.0, 0.0): 38.219, (1.0, 0.0): 38.3,
                                (2.0, 0.0): 39.4, (3.0, 0.0): 38.219})
    return check_arrival(t, [38.219, 38.3, 39.4, 38.219], 0.245, 0.7813)


def _case_arrival_unread():
    """The road's own last station was never read: UNMEASURED is a refusal."""
    t = _term(2.0, 38.219)
    return check_arrival(t, [None, None, 38.219], 0.245, 0.7813)


def _case_road_junction_is_deferred_isolated_is_refused():
    """THE 19, SPLIT, and the split is the finding.

    MEASURED on Ulfsland: 19 of 50 termini have no pad within 30 m and
    nothing standing with a walkable top.  Seventeen of them are ROAD/ROAD
    JUNCTIONS - N0 B and N0b A both at (-134, 1422); N1 B and N2 A at
    (-198, 4170); FIVE segments meeting at the temple (-56, -34) - and 17
    of the 19 had exactly one build piece within 30 m: a
    `piece_groundtorch`, a lamp.  Reporting those as "arrives nowhere" is
    a settlements section refusing a world for a question it does not own,
    and `junction.py` already measures junction spread against the same
    slide limit.

    TWO are genuinely isolated: R3 A at (-216.0, 2146.0) and T5 B at
    (643.3, -93.6).  They meet no pad, nothing standing and no other
    terminus, and they stay a REFUSAL - naming them, because those two are
    the ones somebody has to answer for.
    """
    segs = {f"road_{i}": object() for i in range(25)}
    deferred = [("road_N0_brgs2_wmid", "B", -134.0, 1422.0,
                 ["road_N0b_wmid_wnorth A"]),
                ("road_N0b_wmid_wnorth", "A", -134.0, 1422.0,
                 ["road_N0_brgs2_wmid B"])]
    isolated = [("road_R3_n0bjunction_vestvik", "A", -216.0, 2146.0),
                ("road_T5_temple_dockshore", "B", 643.3, -93.6)]
    fs = check_population(segs, [object()], [object()], 0.245, 0.49,
                          "ledger.jsonl", isolated, deferred)
    bad = [f for f in fs if not f.ok]
    assert len(bad) == 1 and bad[0].check == "terminus_unmeasured", (
        f"exactly the isolated pair must refuse, got "
        f"{[(f.check, f.ok) for f in fs]}")
    assert "(-216.0, 2146.0)" in bad[0].message \
        and "(643.3, -93.6)" in bad[0].message, (
        "an unmeasured terminus must be NAMED with its position, not "
        "counted")
    assert any(f.ok and "ANOTHER ROAD" in f.message for f in fs), (
        "a road/road junction must be DEFERRED BY NAME to junction.py, "
        "never silently dropped and never refused here")
    return fs


# -- WHAT A TERMINUS MEETS: the tier order, and the chooser's three rulings --
_JUNCTION_AT = {("road_N0b_wmid_wnorth", "B"): (-154.0, 2506.0),
                ("road_N1_wnorth_shelf", "A"): (-154.0, 2506.0),
                ("road_T8_stenvik_wttown", "B"): (820.0, 738.0),
                ("road_T9_wttown_dockshore", "A"): (820.0, 738.0)}


def _case_a_junction_outranks_the_nearest_standing_top():
    """THE KNOWN-BAD STATE, and it must not come back.

    N0b B and N1 A share one terminus at (-154, 2506) - a road/road
    junction, MEASURED at 0.000 m apart - and a `wood_floor` tops out
    35.622 m some 16.5 m away.  With the junction test as a LAST resort
    that floor became their destination and each end was refused by
    -1.780 m against it.  A measured coincidence is a fact; the nearest
    walkable top is a search result, and the fact outranks it.
    """
    deck = _Slab("wood_floor", 35.622, hw=2.0, cx=-140.0, cz=2506.0,
                 thick=0.13)
    tier, payload = resolve_destination(
        "road_N0b_wmid_wnorth", "B", -154.0, 2506.0, [], _JUNCTION_AT,
        _Grid([deck]), {"wood_floor"}, 1.8)
    assert tier == "junction" and payload == ["road_N1_wnorth_shelf A"], (
        f"a terminus sharing a vertex with another built segment's "
        f"terminus is junction.py's question, got {tier!r} {payload!r}")
    return []


def _case_a_declared_pad_outranks_the_junction():
    """THE REVERSE, and without it the fix above silences a real defect.

    T8 B and T9 A share one terminus at (820, 738) too, but wt-town's pad
    claim reaches it - 7.2 m from the rectangle - and a pad claim is a
    DECLARATION: the builder said that is where these roads go.  MEASURED
    on Ulfsland the carriageway lands at 39.608 m against the pad's
    standing 40.450 m, 0.842 m out, twice.  Deferring that to junction.py
    would lose it: junction spread is the agreement between the two
    ROADS, and they agree with each other perfectly while both miss the
    town.
    """
    pad = dict(name="wt-town", cx=804.0, cz=724.0, hw=10.0, hd=10.0,
               datum=40.45, seq=387)
    tier, payload = resolve_destination(
        "road_T8_stenvik_wttown", "B", 820.0, 738.0, [pad], _JUNCTION_AT,
        _Grid([]), set(), 1.8)
    assert tier == "declared" and payload[1]["name"] == "wt-town", (
        f"a declared pad within reach is the destination even at a "
        f"junction, got {tier!r}")
    assert abs(payload[0] - 7.211) < 0.01, (
        f"and the gap is to the RECTANGLE, not the centre: {payload[0]}")
    return []


def _case_a_bounding_box_is_not_a_destination():
    """N2 B / N3 A at (-650, 5402): the only walkable top within 30 m was
    a `TreasureChest_trollcave` at y 5066.017, and that prefab's whole
    extent is `MeshCollider.sharedMesh.bounds`.  A bound may not choose a
    road's destination any more than it may veto one - 58 % of this
    corpus's non-render colliders are bounds rows."""
    chest = _Slab("TreasureChest_trollcave", 5066.017, hw=0.66,
                  cx=-628.0, cz=5402.0, thick=0.54)
    assert _nearest_standing(-650.0, 5402.0, _Grid([chest]),
                             {"TreasureChest_trollcave"}, 1.8,
                             frozenset({"TreasureChest_trollcave"})) is None
    assert _nearest_standing(-650.0, 5402.0, _Grid([chest]),
                             {"TreasureChest_trollcave"}, 1.8) is not None, (
        "and the case must be the BOUND doing it: with the bound not "
        "declared, the same chest is still offered, which is the state "
        "this world was in")
    return []


def _case_a_crown_out_of_reach_is_not_a_destination():
    """N4 B at (-846, 7730), the exact inversion the ticket names.

    `level_top` returns a solid's HIGHEST level face, and for
    `portal_wood` that is its crown 3.294 m above its own sill.  Offered
    0.06 m from the terminus it became the destination and the arrival
    came out UNMEASURED at 3.287 m off the road - while the `wood_floor`
    3.52 m away tops out at 33.556 m and the carriageway lands at
    33.556 m, an EXACT arrival.  A walker steps onto a top from the
    ground the piece itself stands on.
    """
    arch = _Slab("portal_wood", 36.843, hw=1.12, cx=-846.0, cz=7730.5,
                 thick=3.294)
    deck = _Slab("wood_floor", 33.556, hw=2.0, cx=-846.0, cz=7734.5,
                 thick=0.13)
    got = _nearest_standing(-846.0, 7730.0, _Grid([arch, deck]),
                            {"portal_wood", "wood_floor"}, 1.8)
    assert got is not None and got[4] == "wood_floor" \
        and abs(got[3] - 33.556) < 1e-6, (
        f"the arch's crown is 3.294 m above its own sill and is not a "
        f"place a road arrives; got {got}")
    return []


def _case_a_face_a_walker_cannot_occupy_is_not_a_destination():
    """W5 B at (-774, -290): a `stone_wall_2x1` cap at 45.026 m with
    another `stone_wall_2x1` standing directly on it.  The wt-south
    ruling - a road does not arrive on a wall - applied where the
    destination is CHOSEN and not only where its height is read."""
    cap = _Slab("stone_wall_2x1", 45.026, hw=1.0, cx=-763.0, cz=-290.0,
                thick=1.0)
    above = _Slab("stone_wall_2x1", 46.026, hw=1.0, cx=-763.0, cz=-290.0,
                  thick=1.0)
    got = _nearest_standing(-774.0, -290.0, _Grid([cap, above]),
                            {"stone_wall_2x1"}, 1.8)
    assert got is not None and abs(got[3] - 46.026) < 1e-6, (
        f"the blocked cap must be passed over and the clear one taken; "
        f"got {got}")
    return []


def _case_a_generated_battlement_is_not_a_road_s_destination():
    """W5 B at (-774, -290), and the ruling on this world's one
    UNMEASURED terminus that has anything standing near it.

    `w-south` has NO site pad anywhere in the chain - 72 live `site_pad`
    terrain_writes, none within 80 m, nearest declared rectangle 143.2 m
    away - and the only thing within 30 m is GENERATED: a LocationProxy
    at (-782.6, 34.56, -271.5), 11.3 m out, carrying a stone curtain of
    72 `stone_wall_2x1` and 25 `stone_wall_4x2` from 34.026 m up to
    46.026 m.  The carriageway lands at 33.208 m.

    The refusal this section OWES that state is `terminus_unmeasured`,
    with the candidate NAMED.  What it must never do is take a course of
    that curtain as the datum and print a confident number: MEASURED,
    the walker-reach envelope moved into the chooser hands back the
    44.026 m course at gap 12.30 and reports -10.818 m, and a body-level
    footing rule that does reject the curtain also rejects every pier
    deck in the world.  So the finding is about the WORLD - w-south was
    never given an arrival surface - and this case pins the shape of it.
    """
    caps = [_Slab("stone_wall_2x1", 34.026 + k, hw=1.0, cx=-762.7,
                  cz=-290.0, thick=1.0) for k in range(1, 13)]
    got = _nearest_standing(-774.0, -290.0, _Grid(caps),
                            {"stone_wall_2x1"}, 1.8)
    assert got is not None and abs(got[3] - 46.026) < 1e-6, (
        f"every course but the top one is blocked by the course above "
        f"it, so the chooser lands on the battlement; got {got}")
    t = dict(segment="road_W5_brgs1_wsouth", end="B", x=-774.0, z=-290.0,
             datum=got[3], gap_m=got[0],
             dest=f"the standing top of {got[4]}",
             nums=dict(segment="road_W5_brgs1_wsouth", destination=got[4],
                       destination_kind="standing",
                       walkable_candidates=got[5],
                       build_pieces_within_reach=got[6]))
    fs = check_arrival(t, [33.208, 33.208], 0.245,
                       A.instruments().STEP_LIMIT_PER_M)
    assert [f.check for f in fs] == ["terminus_unmeasured"], (
        f"a battlement 12.818 m above the carriageway is outside the "
        f"reach envelope and the honest verdict is UNMEASURED, not a "
        f"datum miss; got {[f.check for f in fs]}")
    assert "stone_wall_2x1" in (fs[0].numbers or {}).get("candidate", ""), (
        f"and the candidate must be NAMED, or nobody can tell a missing "
        f"arrival surface from a broken instrument; got {fs[0].numbers}")
    n = fs[0].numbers or {}
    assert n.get("build_pieces_within_reach") == 12 \
        and n.get("walkable_candidates") == 1, (
        f"AND THE POPULATION MUST CARRY ITS DENOMINATOR: one walkable "
        f"top out of twelve courses of one curtain is self-evidently a "
        f"crowd, while a bare '1' reads as 'the one thing standing "
        f"here'; got walkable={n.get('walkable_candidates')} of "
        f"{n.get('build_pieces_within_reach')}")
    assert "1 walkable top(s) out of 12 build piece(s)" in fs[0].message, (
        f"and it must be on the PAGE, not only in the numbers dict; got "
        f"{fs[0].message}")
    return fs


def _case_a_join_is_judged_an_approach_is_walked():
    """THE RULING, BOTH POPULATIONS, ON THIS WORLD'S OWN NUMBERS.

    `terminus_datum` asks a JOIN question - is the road AT the height of
    the thing it meets - and it was being asked of APPROACHES too.

    THE JOINS MUST KEEP REFUSING.  All three of this world's gap-0 joins
    are roads standing ON a bridge deck at 30.600 m with their own bed
    above it: N0 A +0.553 at gap 0.000, S1 B +0.445 at gap 0.000, S12 B
    +1.206 at gap 0.376.  There is no ground between the road and the
    deck for the berm or step checks to measure, so if the datum test
    does not fire NOTHING DOES, and a deck buried half a metre in its own
    bank becomes a clean arrival.

    THE APPROACHES ARE WALKED.  R1 B at gap 10.77, T8 B and T9 A at 7.21,
    each refused -0.842 m against the wt-town pad - while MEASURED on
    frozen save 9a242873, 61 of 61 lattice vertices, 0 unresolved of 44
    transitions, all three approaches are STRICTLY MONOTONE from 39.608
    to 40.450 and the worst single 1 m edge on the whole board is
    0.4229 m, 54 % of the 0.7813 m slide limit.
    """
    lim = A.instruments().STEP_LIMIT_PER_M

    def term(gap, datum, x=-350.0, z=246.0, seg="road_N0_brgs2_wmid"):
        return dict(segment=seg, end="A", x=x, z=z, datum=datum, gap_m=gap,
                    dest="the standing top of wood_floor",
                    nums=dict(segment=seg, destination="wood_floor",
                              destination_kind="declared"))
    # -- the joins, one per live gap-0 refusal, all must still refuse ----
    for seg, gap, road, datum, err in (
            ("road_N0_brgs2_wmid", 0.0, 31.154, 30.600, 0.553),
            ("road_S1_portalhub_brgs2", 0.0, 31.045, 30.600, 0.445),
            ("road_S12_brgs1_boathouse", 0.376, 31.806, 30.600, 1.206)):
        fs = check_arrival(term(gap, datum, seg=seg), [road], 0.245, lim)
        assert [f.check for f in fs] == ["terminus_datum"], (
            f"{seg} at gap {gap} is a JOIN - the road's last station and "
            f"the deck share a lattice cell and nothing else measures "
            f"that transition - so it must refuse by {err:.3f} m; got "
            f"{[f.check for f in fs]}")
        assert (fs[0].numbers or {}).get("judged_as") == "join", (
            f"and it must SAY which population decided it; got "
            f"{fs[0].numbers}")
    # -- the approach: wt-town, its real profile, must NOT refuse --------
    prof = [39.608, 39.63, 39.71, 39.71, 39.77, 39.77, 39.84, 39.84,
            39.89, 40.32, 40.45, 40.45, 40.45, 40.45]
    verts = [(820.0 - k, 738.0) for k in range(len(prof))]
    t = dict(segment="road_T8_stenvik_wttown", end="B", x=820.0, z=738.0,
             datum=40.450, gap_m=7.21, dest="pad 'wt-town'",
             vertices=verts,
             heights_by_vertex={v: y for v, y in zip(verts, prof)},
             nums=dict(segment="road_T8_stenvik_wttown",
                       destination="wt-town", destination_kind="declared"))
    fs = check_arrival(t, prof, 0.245, lim)
    assert not fs, (
        f"T8 B is an APPROACH over 7.21 m: monotone 39.608 -> 40.450, no "
        f"berm by construction, worst 1 m edge 0.4229 m = 54 % of "
        f"{lim:.4f}. A walker already walks onto that pad and the 0.842 m "
        f"is a hill the road is on; got {[f.message for f in fs]}")
    return []


def _case_an_approach_that_is_not_walkable_still_refuses():
    """THE OTHER DIRECTION, AND WITHOUT IT THE SPLIT IS AN AMNESTY.

    A gap term that silently stops testing anything is the fourth face of
    "a gate that only asks one sign".  So: the SAME 7.21 m approach, with
    a cliff in it, and with a cliff at its LAST edge - the one onto the
    destination, which the walk itself never reads because the walk reads
    GROUND and the destination is a deck.  Both must refuse, through
    `terminus_step`, which is the check that owns a step.
    """
    lim = A.instruments().STEP_LIMIT_PER_M
    prof = [39.608, 39.63, 39.71, 39.71, 39.77, 39.77, 39.84, 39.84,
            39.89, 40.32, 40.45, 40.45, 40.45, 40.45]
    verts = [(820.0 - k, 738.0) for k in range(len(prof))]

    def t(p, datum=40.450):
        return dict(segment="road_T8_stenvik_wttown", end="B", x=820.0,
                    z=738.0, datum=datum, gap_m=7.21, dest="pad 'wt-town'",
                    vertices=verts,
                    heights_by_vertex={v: y for v, y in zip(verts, p)},
                    nums=dict(segment="road_T8_stenvik_wttown",
                              destination="wt-town",
                              destination_kind="declared"))
    cliff = list(prof)
    cliff[5] = 41.0                       # a 1.230 m riser mid-approach
    fs = check_arrival(t(cliff), cliff, 0.245, lim)
    assert "terminus_step" in [f.check for f in fs], (
        f"a 1.230 m riser across one 1 m edge is over the {lim:.4f} m "
        f"slide limit and must refuse however long the gap is; got "
        f"{[f.check for f in fs]}")
    # AND THE LAST EDGE, the one onto the destination itself.  The walk
    # reads ground and stops at 40.45; a deck at 42.0 is a 1.550 m step a
    # walker takes LAST, and nothing else in this section looks at it.
    fs = check_arrival(t(prof, datum=42.0), prof, 0.245, lim)
    assert "terminus_step" in [f.check for f in fs], (
        f"the final edge ONTO the destination must be judged too, or an "
        f"approach has nobody measuring the step a walker actually takes "
        f"last; got {[f.check for f in fs]}")
    assert not [f for f in fs if f.check == "terminus_datum"], (
        f"and it must be the STEP check that owns it, not the datum test "
        f"coming back in by another door; got {[f.check for f in fs]}")
    return fs


def _riser_term(datum: float, read_to: int = 20, n: int = 21):
    """A CONSTRUCTED approach 20 m long, terminus first, ground rising a
    quarter-metre per lattice edge - well under the slide limit, so the
    ONLY transition that can decide anything is the last one, the riser
    onto the destination.  `read_to` is how far the ground instrument got:
    a walk whose tail was never read is the second state this pair tests.

    Nothing here is borrowed from a save, a corpus or a prefab, because a
    fixture that leans on an artefact inherits that artefact's mutability
    - see `Case.neuter` for the three that quietly stopped describing
    anything.
    """
    verts = [(float(k), 0.0) for k in range(n)]
    hbv = {v: 30.0 + 0.25 * k for k, v in enumerate(verts) if k <= read_to}
    term = dict(segment="road_S12_brgs1_boathouse", end="A", x=0.0, z=0.0,
                datum=datum, gap_m=float(n - 1),
                dest="the standing top of wood_floor",
                vertices=verts, heights_by_vertex=hbv,
                nums=dict(segment="road_S12_brgs1_boathouse",
                          destination="wood_floor",
                          destination_kind="standing"))
    return term, [hbv.get(v) for v in verts]


def _case_the_last_step_names_the_footfall_it_measured():
    """A RISER HAS ONE LOCATION, NOT TWO, AND THE OLD REFUSAL PUBLISHED
    THE ROAD'S OWN TERMINUS AS THE FAR END OF IT.

    MEASURED on gate C, save a087af6253a0, settlements refusal 3 of 9:
    `road_S12_brgs1_boathouse` terminus A published

        step_m 3.852, edge_m 1.0, at [-301.0, -125.0, -281.6, -130.7]
        "climbs 3.852 m across a single 1 m lattice edge"

    and those two points are 20.22 m apart - `hypot(19.4, 5.7)` - because
    the second of them is not a lattice neighbour at all, it is `term.x,
    term.z`, the far end of the whole approach.  The refusal's own
    arithmetic therefore could not be checked from its own numbers, and
    `repair_population` - which is exactly what the Lipschitz-cone
    disposal consumes to size cuts - carried that row.  A cone built from
    it would have cut twenty metres of ground that is not the defect.

    So the last transition is published as what it is: ONE vertex, the
    walk's last footfall, SIGNED, named a riser.  The sign is not
    decoration - a disposal may only ever CUT, so `+3.852` (the
    destination is high, nothing to cut) and `-3.852` (the ground is high,
    cuttable) are two different tickets and `abs()` could not tell them
    apart.
    """
    lim = A.instruments().STEP_LIMIT_PER_M
    # the ground reaches 35.000 at the last footfall (20.0, 0.0)
    t_up, p_up = _riser_term(35.0 + 3.852)
    up = check_arrival(t_up, p_up, 0.245, lim)
    step = [f for f in up if f.check == "terminus_step"]
    assert len(step) == 1, [f.check for f in up]
    nm = step[0].numbers
    assert nm.get("judged_as") == "riser", (
        "the last transition onto a destination is a riser at the "
        f"destination's own edge, not a lattice edge; got {nm.get('at')!r}")
    assert nm["at"] == [20.0, 0.0], (
        "a riser is published at the ONE place it was measured - the "
        "walk's last footfall - and the old finding published "
        "[last_vertex, term.x, term.z], a pair gap_m apart described as "
        f"a 1 m lattice edge; got {nm['at']!r}")
    assert "edge_m" not in nm, (
        "a riser has no run, and `edge_m 1.0` on a transition whose ends "
        "were 20.22 m apart is the number that made gate C's S12 A "
        f"refusal uncheckable; got {nm!r}")
    assert round(nm["rise_m"], 3) == 3.852, nm
    pop = [r for r in nm["repair_population"]
           if r.get("onto_the_destination")]
    assert len(pop) == 1 and pop[0]["at"] == [20.0, 0.0], (
        "the repair population is what a disposal reads; a row naming the "
        f"road's own terminus sends the cut 20 m away; got {pop!r}")
    # AND THE OTHER SIGN, which `abs()` erased.  Same geometry, the
    # destination BELOW the ground the walk ends on: cuttable, and the
    # refusal has to say so.
    t_dn, p_dn = _riser_term(35.0 - 3.852)
    down = check_arrival(t_dn, p_dn, 0.245, lim)
    dn = [f for f in down if f.check == "terminus_step"][0].numbers
    assert round(dn["rise_m"], 3) == -3.852, (
        "the sign says which side is high, and a disposal that may only "
        f"cut cannot act without it; got {dn.get('rise_m')!r}")
    return up


def _case_an_unread_last_footfall_is_not_substituted():
    """A READING TAKEN SOMEWHERE ELSE, PUBLISHED UNDER THE NAME OF THE
    PLACE IT WAS NOT TAKEN.

    The version this holds in place read
    `next(hbv[v] for v in reversed(verts) if v in hbv)` - the first vertex
    going BACKWARDS that had been read - and then published `verts[-1]` as
    where it had read.  A walk whose tail the ground instrument never
    answered therefore reported "the step a walker takes LAST" from
    wherever the reading happened to stop, with a number, at full
    confidence.

    Here the instrument answers the first twenty vertices and not the
    twenty-first, and the destination stands 10.000 m above the ground.
    The honest verdict is `terminus_step_unresolved`: the last footfall
    was never read, so the riser was not measured.  The substituting
    version instead reads (19.0, 0.0), finds 10.250 m and publishes a
    confident `terminus_step` at a vertex it never touched.
    """
    lim = A.instruments().STEP_LIMIT_PER_M
    t_un, p_un = _riser_term(45.0, read_to=19)
    fs = check_arrival(t_un, p_un, 0.245, lim)
    kinds = [f.check for f in fs]
    assert "terminus_step_unresolved" in kinds, (
        "an unread last footfall is an unresolved transition, and this "
        f"section already owns that refusal; got {kinds}")
    assert "terminus_step" not in kinds, (
        "a riser measured at a vertex the walk never stood on is a "
        "reading published under the name of a place it was not taken - "
        f"the defect class this gate exists to find; got {kinds}")
    # AND THE DENOMINATOR IS ONE SET.  The unresolved arm counted
    # `len(verts) - 1` while the step arm counted the riser too, so one
    # walk published two population sizes.
    un = [f for f in fs if f.check == "terminus_step_unresolved"][0]
    assert un.numbers["transitions_judged"] == 21, un.numbers
    return fs


def _case_a_refusal_says_whether_a_write_could_close_it():
    """A REFUSAL THAT CANNOT SAY WHETHER ANY TOOL CAN CLOSE IT IS AN
    INSTRUCTION TO GO AND FIND OUT, AND THIS GATE ISSUED NINE OF THEM.

    MEASURED on gate C's refusals, ledger md5 0ebe71d9 at 5,421 lines,
    owned set read through `_site_evidence` - 132,148 points over 397
    live `flatten=FORBIDDEN` records.  `depth` is the OVERAGE, `reach` is
    `depth / slide_limit + 1 m`:

        N0  brgs2 wmid A   datum +0.553  reach 1.394  owner 0.970  REFUSED
        S1  portalhub B    datum +0.445  reach 1.256  owner 0.970  REFUSED
        S12 boathouse B    datum +1.206  reach 2.230  owner 1.413  REFUSED
        S12 boathouse A    riser 3.852   reach 4.930  owner 0.783  REFUSED
        T13 harboursouth B datum -0.505  reach 1.333  owner 1.994  free
        R4  vestvik B      step  0.799   reach 1.023  owner 3.606  free
        T9  wttown B       step  2.174   reach 2.783  owner 11.858 free

    `ledger/writer.py` refuses any `terrain_write` that moves the ground
    at an owned point by more than `FLATTEN_TOLERANCE_M` (0.01 m), so the
    four REFUSED rows cannot be closed by the one sanctioned write path at
    all and the three free ones can.  That distinction is the whole
    difference between work and a blocked ticket, it was rediscovered by
    hand every time, and it is derivable from numbers the check already
    has.

    AND THE SIZE OF THE OWNED SET DECIDES REAL VERDICTS.  Read one record
    at a time through `_protected_pieces` it is 7,409 points; read the way
    the guard reads it, 132,148.  On the small set T13 B comes back
    REFUSED at 0.707 m and S12 A comes back free - BOTH BACKWARDS.  See
    `forbidden_extents`.

    THE FOOTPRINT IS DERIVED, NOT CHOSEN: a Lipschitz cone lowering a
    vertex by `d` reaches the surface again at `d / slide_limit`, and the
    engine interpolates the 1 m lattice, so the ground moves anywhere
    within one more metre of that.  And the DEPTH is the OVERAGE, not the
    whole error: what has to move is the part above the limit.
    """
    lim = A.instruments().STEP_LIMIT_PER_M

    def join(owners, ox=0.97):
        t = dict(segment="road_N0_brgs2_wmid", end="A", x=0.0, z=0.0,
                 datum=30.600, gap_m=0.0,
                 dest="the standing top of wood_floor",
                 nums=dict(segment="road_N0_brgs2_wmid",
                           destination="wood_floor",
                           destination_kind="standing"))
        if owners is not False:
            t["forbidden_extents"] = (
                None if owners is None
                else [(ox, 0.0, 0.0, "bridge_assembly", 23)])
        return check_arrival(t, [31.153], 0.245, lim)

    owned_fs = join(True)
    nm = [f for f in owned_fs if f.check == "terminus_datum"][0].numbers
    assert "repairable_by_terrain_write" in nm, (
        "a refusal that proposes lowering the ground owes the reader "
        "whether the one sanctioned write path will accept that lowering; "
        f"got {sorted(nm)}")
    # (0.553 - 0.245) / 0.7813 + sqrt(2)
    assert nm["repair_footprint_m"] == 1.808, nm
    assert nm["repairable_by_terrain_write"] is False, nm
    assert nm["ground_owned_by"] == "bridge_assembly seq 23", nm
    assert nm["ground_owner_distance_m"] == 0.97, nm

    # THE OTHER DIRECTION, or the field is a rubber stamp: the same
    # geometry with the owned ground 20 m away is REPAIRABLE, and a check
    # that answered False for everything would be as useless as one that
    # answered True.
    clear = [f for f in join(True, ox=20.0)
             if f.check == "terminus_datum"][0].numbers
    assert clear["repairable_by_terrain_write"] is True, clear
    assert clear["ground_owned_by"] is None, clear

    # AND UNREADABLE IS NOT OPEN.  A set that could not be read answers
    # UNKNOWN; a guard that reports open precisely when it cannot see is
    # the defect class this gate keeps finding.
    blind = [f for f in join(None) if f.check == "terminus_datum"][0].numbers
    assert blind["repairable_by_terrain_write"] is None, blind
    return owned_fs


def _case_a_repair_says_whose_ground_it_would_eat():
    """A VERTEX CAN BE WRITABLE AND STILL NOT BE YOURS, AND NOTHING IN
    THIS TREE ASKED THE SECOND QUESTION.

    `writer`'s flatten guard answers whether a cut is ALLOWED.  It has no
    opinion on whether the material being cut is somebody else's work.
    MEASURED on save 0571968d by the harbours section, over the 855 cut
    vertices its draining repairs propose: 812 are writer-CLEAR, and all
    but ONE of those sit inside a road's own `terrain_write` footprint -
    so a repair the guard would wave through drowns an embankment another
    builder raised.  The one vertex owned by nobody was the only one that
    needed no decision from an operator, it is closable for 0.1492 m3,
    and it had survived gate A, gate B and gate C inside the same class as
    649 road-owned ones.

    AND THE THREE WAYS A REFUSAL CANNOT TERMINATE ARE THEN ONE FIELD.
    1A the writer forbids the remedy, 1B the writer permits it and it
    destroys another actor's work, 1C no tool in this tree performs it.
    ACTIONABLE is the residue, and it is the only count that answers "is
    there work left for the factory".
    """
    lim = A.instruments().STEP_LIMIT_PER_M

    class _Writes:
        """The recorded footprints, reduced to the one question asked of
        them.  CONSTRUCTED, not borrowed from the chain: a fixture that
        leans on the ledger inherits the ledger's mutability."""

        def __init__(self, who, within):
            self.who, self.within = who, within

        def at(self, x, z, margin=0.0, exact_only=True):
            return self.who if math.hypot(x, z) <= self.within + margin \
                else ""

    def join(writes):
        t = dict(segment="road_N0_brgs2_wmid", end="A", x=0.0, z=0.0,
                 datum=30.600, gap_m=0.0,
                 dest="the standing top of wood_floor",
                 heights_by_vertex={(0.0, 0.0): 31.153},
                 write_index=writes, forbidden_extents=[],
                 nums=dict(segment="road_N0_brgs2_wmid",
                           destination="wood_floor",
                           destination_kind="standing"))
        return [f for f in check_arrival(t, [31.153], 0.245, lim)
                if f.check == "terminus_datum"][0].numbers

    # THE CONTESTER IS ANOTHER ACTOR, AND THE FIRST FORM OF THIS FIXTURE
    # WAS THE DEFECT.  It named `road_N0_brgs2_wmid`'s OWN write as the
    # contester of `road_N0_brgs2_wmid`'s own repair and asserted 1B, so
    # it pinned the gate's self-contest bug in place.  The contester here
    # is the one MEASURED on the full gate against S12 terminus A -
    # `regrade_oc_boathouse_channel11` seq 5393 - which is a genuine
    # operator decision: a road repair inside a harbour regrade.
    inside = join(_Writes(
        "terrain_write regrade_oc_boathouse_channel11 seq 5393", 0.5))
    assert "contested_by" in inside, (
        "a refusal that proposes a cut owes the reader whether the ground "
        "it would cut is already held by another actor's terrain_write; "
        f"the writer's guard does not ask; got {sorted(inside)}")
    assert inside["contested_by"] == \
        "terrain_write regrade_oc_boathouse_channel11 seq 5393", inside
    assert inside["repairable_by_terrain_write"] is True, (
        "and this is the whole point of the class: the WRITER PERMITS IT. "
        f"1B is not 1A; got {inside}")
    assert inside["termination_class"] == "1B_CONTESTED", inside
    # AND 1A IS DERIVED FROM THE ANSWER, NOT FROM THE FLAG.  A caller that
    # sets `repairable_by_terrain_write = False` to mean "this refusal has
    # no repair at all" must NOT come back 1A: that is absence of a remedy
    # read as presence of an owner, and it printed six phantom FORBIDDEN
    # rows in another section tonight.
    assert termination_class(dict(repairable_by_terrain_write=False,
                                  ground_owned_by=None,
                                  repair_footprint_m=1.0)) == "1C_UNSERVED", (
        "1A must NAME the flatten=FORBIDDEN site that owns the ground; a "
        "False flag with no owner is a question that was never asked")
    assert termination_class(dict(repairable_by_terrain_write=False,
                                  ground_owned_by="boathouse_place seq 30",
                                  repair_footprint_m=1.0)) == "1A_FORBIDDEN"
    # AND THE THREE-STATE RULE ON THE REACH FIELD ITSELF.  A refusal with
    # no proposed repair has a footprint of 0.0, not of nothing; passing
    # None turned six correct 1C rows into UNKNOWN in another section.
    assert termination_class(dict(repairable_by_terrain_write=False,
                                  ground_owned_by=None,
                                  repair_footprint_m=0.0)) == "1C_UNSERVED", (
        "0.0 says the question was asked and a repair would need no "
        "ground; None says nobody looked, and the two must not share an "
        "answer")
    assert termination_class(dict(repairable_by_terrain_write=False,
                                  ground_owned_by=None,
                                  repair_footprint_m=None)) == "UNKNOWN"
    # the query is clamped to the index's own exactness, and says so
    assert inside["contested_within_m"] == 1.808, inside

    # THE OTHER DIRECTION: ground no recorded write holds, so the repair
    # is work rather than a decision.
    clear = join(_Writes("", 0.0))
    assert clear["contested_by"] is None, clear
    assert clear["termination_class"] == "ACTIONABLE", (
        "a writer-clear, uncontested repair is the ONLY kind that answers "
        f"'is there work left'; got {clear}")

    # AND UNREAD IS NOT UNOWNED.  Footprints that could not be read answer
    # UNKNOWN; answering "nobody owns it" when the instrument is blind is
    # how a repair walks into somebody's embankment with a clean report.
    blind = join(None)
    assert blind["contested_by"] is None \
        and blind["contested_within_m"] is None, blind
    assert blind["termination_class"] == "UNKNOWN", blind
    return [f for f in check_arrival(
        dict(segment="road_N0_brgs2_wmid", end="A", x=0.0, z=0.0,
             datum=30.600, gap_m=0.0, dest="the standing top of wood_floor",
             heights_by_vertex={(0.0, 0.0): 31.153},
             write_index=_Writes(
                 "terrain_write regrade_oc_boathouse_channel11 seq 5393",
                 0.5),
             forbidden_extents=[],
             nums=dict(segment="road_N0_brgs2_wmid", destination="wood_floor",
                       destination_kind="standing")),
        [31.153], 0.245, lim)]


def _case_a_refusal_states_which_way_its_repair_moves_ground():
    """A CLASS WITHOUT A DIRECTION ROUTES A CUT-ONLY PLANNER AT A RAISE.

    MEASURED, full gate, Ulfsland save 245daa7f719ab69d, artefact
    /tmp/final_gate.json: `settlements/terminus_datum`
    road_T13_temple_harboursouth terminus B at (12,-258) lands at applied
    31.195 m against a wood_stair standing top of 31.700 - `error_m
    -0.505`, a JOIN, THE CARRIAGEWAY IS BELOW ITS DESTINATION - and it was
    published `termination_class: ACTIONABLE` with no other field on the
    routing path.  It is not a CUT, and every terrain instrument in this
    tree a refusal row can be handed is cut-only: `roads/regrade.py`
    asserts it on every proposed delta, `roads/cone.py` refuses the row
    outright, `harbours.restoration_set` RAISES on a set holding one fill.

    THIS CASE ONCE ASSERTED THAT ACTIONABLE WAS CORRECT HERE, AND SAID SO
    IN SO MANY WORDS.  It is not, and the argument that says so is
    `termination_class`'s own: ACTIONABLE means a repair exists, the
    writer permits it, and it destroys nothing - and "a repair exists"
    was being inferred from "the writer permits it", which is a different
    question with a different answer.  A raise is served by NO instrument
    in this tree, which is the definition of 1C_UNSERVED, and that is
    where `termination_class` now sends it; see
    `_case_a_raise_is_unserved_not_actionable`.  The DIRECTION is still
    what this case is about - the class is asserted here only so the two
    cannot silently disagree.

    THE SIGN WAS ALREADY MEASURED AND ALREADY PUBLISHED.  `check_arrival`
    computes `err = road_y - datum` and prints it in the message; the
    riser arm carries `rise_m` signed and says in its own comment that it
    is signed so no consumer has to go back to the world.  The defect is
    not a missing measurement, it is a measurement that never reached the
    field a consumer routes on - which is why this case is END TO END
    through `check_arrival` rather than over a hand-built nums dict.

    AND A LATTICE EDGE IS A CUT WHATEVER ITS SIGN.  Ground stands at both
    ends of one, so lowering the high end always exists; only the two
    shapes whose high side is the DESTINATION'S OWN STANDING GEOMETRY - a
    JOIN under it, a RISER up onto it - are uncuttable.  A direction that
    fired on sign alone would take the 3 lattice-edge rows in this same
    artefact (T13 station 423 over by 0.0127 m, R4 B by 0.017 m, T9 B by
    1.392 m) off the work queue as well, and all three ARE cuts.
    """
    lim = A.instruments().STEP_LIMIT_PER_M

    class _OwnWrite:
        """THE SEGMENT'S OWN `terrain_write`, which is what the real row
        carries: `terrain_write road_T13_temple_harboursouth (seq 338)`.
        Self-contest is excluded, so the class lands on ACTIONABLE and
        this case is about the DIRECTION and nothing else."""

        def at(self, x, z, margin=0.0, exact_only=True):
            return "terrain_write road_T13_temple_harboursouth (seq 338)"

    def term(datum, road, **kw):
        return dict(segment="road_T13_temple_harboursouth", end="B",
                    x=12.0, z=-258.0, datum=datum, gap_m=0.99,
                    dest="the standing top of wood_stair",
                    heights_by_vertex={(12.0, -258.0): road},
                    write_index=_OwnWrite(), forbidden_extents=[],
                    nums=dict(segment="road_T13_temple_harboursouth",
                              destination="wood_stair",
                              destination_kind="standing"), **kw)

    # 1 - THE ROW, end to end: the carriageway 0.505 m UNDER the stair.
    below = [f for f in check_arrival(term(31.700, 31.195), [31.195],
                                      0.245, lim)
             if f.check == "terminus_datum"][0].numbers
    assert below["error_m"] == -0.505, below
    assert below["termination_class"] == "1C_UNSERVED", (
        "a permitted write nobody can issue is not work; see "
        "`_case_a_permitted_raise_is_unserved_not_actionable`", below)
    assert below["repair_direction"] == "raise", (
        "a JOIN whose carriageway is BELOW its destination is closed by "
        "RAISING ground or rebuilding the piece, and every sanctioned "
        f"terrain write in this tree is cut-only; got {below}")

    # 2 - THE OTHER SIGN OF THE SAME CHECK IS STILL A CUT.  Without this
    # the direction is a rubber stamp that empties the work queue: a road
    # standing 0.505 m ABOVE the same stair is lowered, and lowering is
    # the one thing this tree does.
    above = [f for f in check_arrival(term(31.195, 31.700), [31.700],
                                      0.245, lim)
             if f.check == "terminus_datum"][0].numbers
    assert above["error_m"] == 0.505, above
    assert above["repair_direction"] == "cut", above

    # 3 - A LATTICE EDGE IS A CUT, and the three 13-to-1392 mm rows this
    # gate calls work must survive the new field.  Ground at both ends.
    edge = dict(judged_as="lattice_edge", at=[677.0, -94.0, 678.0, -94.0],
                repair_footprint_m=3.196, repairable_by_terrain_write=True,
                contested_within_m=3.196, ground_rise_m=2.174)
    assert repair_direction(edge) == "cut", edge

    # 4 - A RISER'S SIGN IS THE OPPOSITE CONVENTION AND IT MATTERS.
    # `rise_m = datum - ground`, so POSITIVE is the destination standing
    # above the walk's last footfall - the uncuttable one - while the
    # JOIN's `error_m = road - datum` is uncuttable when NEGATIVE.  One
    # helper reading both conventions off one field name would have the
    # riser exactly backwards.
    assert repair_direction(dict(judged_as="riser", rise_m=0.94,
                                 repair_footprint_m=2.2)) == "raise"
    assert repair_direction(dict(judged_as="riser", rise_m=-0.94,
                                 repair_footprint_m=2.2)) == "cut"

    # 5 - THREE-STATE, like every other field the classifier reads.  A
    # refusal that proposes NO repair has no direction, and `None` here
    # means exactly that and never "nobody looked".
    assert repair_direction(dict(no_cut_proposed=True,
                                 repair_footprint_m=0.0)) is None
    assert repair_direction(dict(repair_footprint_m=0.0)) is None
    return [Finding(
        check="settlements/repair_direction", ok=False,
        message=("a JOIN whose carriageway lands 0.505 m BELOW the "
                 "wood_stair it meets is a RAISE and not a cut: "
                 "road_T13_temple_harboursouth B at (12,-258), applied "
                 "31.195 m against a standing top of 31.700 m"),
        where=(12.0, -258.0), measured=-0.505, limit=0.245,
        numbers=dict(error_m=below["error_m"],
                     termination_class=below["termination_class"],
                     repair_direction=below["repair_direction"],
                     the_other_sign=above["repair_direction"],
                     a_lattice_edge=repair_direction(edge)),
        population=("the four refusal shapes this section publishes a "
                    "signed number for: join, riser, lattice edge, berm"))]


def _neuter_every_repair_is_assumed_to_be_a_cut():
    """RESTORE THE SHIPPED STATE: there was no direction at all, so every
    consumer of an ACTIONABLE row assumed the remedy was a cut.

    The field is the whole change, so the neuter is the field reduced to
    that assumption - `"cut"` for anything with a repair - which is
    precisely what a reader of the shipped artefact had to infer.
    """
    def assume_cut(nums: dict) -> str | None:
        if nums.get("no_cut_proposed") is True:
            return None
        fp = nums.get("repair_footprint_m")
        if isinstance(fp, (int, float)) and fp > 0:
            return "cut"
        if nums.get("repair_population") or nums.get("at"):
            return "cut"
        return None
    return _swap("repair_direction", assume_cut)


def _raise_class_arms() -> dict:
    """The same JOIN measured with its sign BOTH ways, classified END TO
    END through `check_arrival` and `termination_class`.

    BOTH ARMS ARE RETURNED AS NUMBERS so the cases below can express the
    survivor AS A VERDICT.  `run_cases` counts an `AssertionError` under a
    neuter as "the case came out the other way", so a survivor written as
    an assertion is indistinguishable from a neuter that emptied the
    instrument - the degenerate classifier passing while wearing a proof.
    """
    lim = A.instruments().STEP_LIMIT_PER_M

    class _OwnWrite:
        """THE SEGMENT'S OWN `terrain_write`, which is what the real row
        carries.  Self-contest is excluded, so the class turns on the
        DIRECTION and on nothing else."""

        def at(self, x, z, margin=0.0, exact_only=True):
            return "terrain_write road_T13_temple_harboursouth (seq 338)"

    def join(datum, road):
        return [f for f in check_arrival(
            dict(segment="road_T13_temple_harboursouth", end="B",
                 x=12.0, z=-258.0, datum=datum, gap_m=0.99,
                 dest="the standing top of wood_stair",
                 heights_by_vertex={(12.0, -258.0): road},
                 write_index=_OwnWrite(), forbidden_extents=[],
                 nums=dict(segment="road_T13_temple_harboursouth",
                           destination="wood_stair",
                           destination_kind="standing")),
            [road], 0.245, lim) if f.check == "terminus_datum"][0].numbers
    return dict(under=join(31.700, 31.195), over=join(31.195, 31.700))


def _raise_class_finding(headline: str):
    """THE VERDICT IS A CONJUNCTION OF BOTH SIGNS.  The row the direction
    must MOVE and the row it must LEAVE ALONE are scored together, so a
    classifier that calls every permitted repair unserved fails this case
    exactly as loudly as one that calls every permitted repair work."""
    got = _raise_class_arms()
    u, o = got["under"], got["over"]
    right = (u["termination_class"] == "1C_UNSERVED"
             and o["termination_class"] == "ACTIONABLE")
    return Finding(
        check="settlements/termination_class", ok=not right,
        where=(12.0, -258.0), measured=u["error_m"], limit=0.245,
        message=(f"{headline}: the carriageway {abs(u['error_m'])} m UNDER "
                 f"the wood_stair classifies {u['termination_class']} "
                 f"(direction {u['repair_direction']}, writer permits it: "
                 f"{u['repairable_by_terrain_write']}), while the same "
                 f"join {o['error_m']} m OVER it classifies "
                 f"{o['termination_class']} (direction "
                 f"{o['repair_direction']})"),
        numbers=dict(
            under_error_m=u["error_m"],
            under_direction=u["repair_direction"],
            under_class=u["termination_class"],
            under_writer_permits=u["repairable_by_terrain_write"],
            under_footprint_m=u["repair_footprint_m"],
            over_error_m=o["error_m"],
            over_direction=o["repair_direction"],
            over_class=o["termination_class"],
            over_writer_permits=o["repairable_by_terrain_write"]),
        population="one JOIN measured with its sign both ways, classified "
                   "end to end through check_arrival")


def _case_a_permitted_raise_is_unserved_not_actionable():
    """A PERMITTED WRITE NOBODY CAN ISSUE IS NOT WORK.

    THE DECISION, AND THE DEFINITION THAT DECIDES IT.  `ACTIONABLE` has
    THREE clauses - a repair exists, THE WRITER PERMITS IT, and it
    destroys nothing - and `termination_class` read the second and
    inferred the first from it.  `repairable_by_terrain_write` answers
    what `Ledger.append`'s flatten guard would say about ground; it has NO
    OPINION on whether any instrument in this tree emits a write of the
    required SIGN, and for a RAISE none does:

        roads/regrade.py          asserts cut-only on every delta
        roads/cone.py             refuses a `repair_direction == "raise"`
                                  row at the top, by that field
        harbours.restoration_set  RAISES on a set holding one fill

    and the other remedy - retire the piece and re-spawn it lower - is a
    BUILDING, which this tree does not delete.  "The remedy is performed
    by no tool in this tree" is, word for word, `1C_UNSERVED`.

    MEASURED, Ulfsland save 7632b3a5674842bd, artefact
    /tmp/gate_final2.json: `settlements/terminus_datum`
    road_T13_temple_harboursouth B at (12,-258) lands at applied 31.195 m
    under a wood_stair standing top of 31.700 - `error_m -0.505`,
    `repair_direction "raise"`, `repairable_by_terrain_write true` - and
    was published ACTIONABLE.  It is the ONLY `raise` on a board of 105
    refusals and it sat on the factory's work queue as one of twelve rows
    a planner could take.  No planner could take it.

    AND IT IS THE SAME DEFECT CLASS THE REST OF THIS SESSION KEEPS
    FINDING: A KEY THAT NAMES A SUBSET OF WHAT THE QUANTITY DEPENDS ON.
    "Is there work here" depends on PERMISSION and on THE EXISTENCE OF AN
    INSTRUMENT THAT WRITES IN THE REQUIRED DIRECTION.  The classifier
    keyed on permission alone, and the missing half was already measured,
    already published, and sitting one field away in the same dict.
    """
    return _raise_class_finding(
        "a repair the writer permits and no instrument performs is "
        "1C_UNSERVED")


def _case_a_permitted_cut_is_still_actionable():
    """THE PARTNER ARM, AND WITHOUT IT THE OTHER ONE IS WORTHLESS.

    Routing a raise to 1C_UNSERVED can only ever REMOVE rows from the work
    queue, so a classifier that called EVERY permitted repair unserved
    would flip the arm above and be stamped PROVEN - an empty work queue
    reading as a converged world, which is the precise failure this whole
    classifier exists to refuse.  This arm is scored on the same
    conjunction and is protected by the OPPOSITE neuter.

    MEASURED here, the same join with its sign reversed: a carriageway
    0.505 m ABOVE the wood_stair is lowered, lowering is the one thing
    this tree does, and it stays ACTIONABLE.  The two arms therefore
    separate A LOST DEPENDENCY - the direction - from A CLASSIFIER THAT
    CONDEMNS EVERYTHING, which one arm cannot.
    """
    return _raise_class_finding(
        "a repair the writer permits and regrade.py performs is still "
        "ACTIONABLE")


def _neuter_a_permitted_write_is_assumed_to_have_a_writer():
    """RESTORE THE SHIPPED CLASSIFIER: read the writer's PERMISSION and
    infer from it that a repair exists, whatever direction it is in."""
    real = termination_class

    def permission_only(nums: dict) -> str:
        if repair_direction(nums) != "raise":
            return real(nums)
        blind = dict(nums)
        blind["error_m"], blind["rise_m"] = None, None
        blind["judged_as"] = "lattice_edge"
        return real(blind)
    return _swap("termination_class", permission_only)


def _neuter_every_permitted_repair_is_unserved():
    """PUT THE OPPOSITE DEFECT IN: no instrument serves anything, so every
    refusal that proposes a repair at all is 1C_UNSERVED and the work
    queue empties - the degenerate over-correction, which reads as a
    converged world."""
    real = termination_class

    def nothing_is_served(nums: dict) -> str:
        got = real(nums)
        if got == "ACTIONABLE":
            return "1C_UNSERVED"
        return got
    return _swap("termination_class", nothing_is_served)


def _case_a_footprint_is_sized_for_a_cut_that_can_be_written():
    """A REPAIR IS NEVER SOLVED ONTO THE LIMIT, SO ITS FOOTPRINT MAY NOT BE
    SIZED AS IF IT WERE.

    `repair_ownership` derives the ground a repair must touch from its
    DEPTH, and every step check in this gate was handing it
    `step - STEP_LIMIT_PER_M` - the depth that leaves the transition
    EXACTLY ON the walker's slide limit.  Nothing in this tree writes onto
    a limit: `roads/cone.py` solves at `DISPOSAL_SLOPE_PER_M` 0.770 m per
    1 m and says why where it is used - a write whose legality depends on
    the last decimal of a float is not a write, and the gate re-reads the
    ground through a different path than the planner did.  So the
    ownership question was being asked about a repair that cannot be
    written, and its answer was published as the row's termination class.

    MEASURED on Ulfsland 245daa7f against the live flatten=FORBIDDEN
    extent set, ALL SIX step-shaped refusals that name a 1 m edge, asked
    both ways.  EXACTLY ONE MOVES:

      roads/traversability T13-temple-harboursouth station 423, the
      0.794 m step at (14,-255)->(14,-256), owner `harbour_place seq 24`
      1.4314 m away:
        step - LIMIT = 0.0127 m -> footprint 1.430 m -> owner None
                                -> repairable True  -> ACTIONABLE
        step - SLOPE = 0.0240 m -> footprint 1.445 m -> harbour_place
                                   seq 24 -> repairable False
                                -> 1A_FORBIDDEN

    The two footprints STRADDLE the owner by 15 mm.  That row sat on the
    work queue through a whole convergence wave described as "13 mm over"
    while `Ledger.append` would have refused every cut that closes it -
    and `roads/cone.py` needs 0.0245 m there, not 0.0132, because a cone
    caps the high end at the low end plus its own slope.

    THE DIRECTION OF THIS CORRECTION IS AWAY FROM WORK, NOT TOWARDS IT.
    It moves a row OUT of ACTIONABLE and into the operator's column; it
    makes no check pass and it closes no refusal.  A correction that only
    ever made the queue shorter would be the thing this gate refuses.
    """
    inst = A.instruments()
    lim = inst.STEP_LIMIT_PER_M
    step = 0.7945

    class _Owner:
        """`harbour_place seq 24`, at its MEASURED 1.4314 m."""

    owners = [(13.62, -256.38, 0.0, "harbour_place", 24)]
    limit_depth = round(step - lim, 4)
    solve_depth = round(inst.disposal_depth(step, lim), 4)
    assert solve_depth > limit_depth, (
        "a disposal solves STRICTLY INSIDE the limit, so the depth that "
        "closes a step is strictly greater than the depth that lands on "
        "it; these two being equal means the footprint is being sized for "
        "a cut no tool in this tree writes - "
        f"onto-the-limit {limit_depth} m, disposal {solve_depth} m")
    at_limit = repair_ownership(14.0, -255.0, limit_depth, owners, lim)
    at_solve = repair_ownership(14.0, -255.0, solve_depth, owners, lim)
    assert at_limit["ground_owned_by"] is None, (
        "the fixture must reproduce the shipped reading or it proves "
        f"nothing; got {at_limit}")
    assert at_solve["ground_owned_by"] == "harbour_place seq 24", (
        "a cut that closes this step needs "
        f"{solve_depth} m, whose footprint {at_solve['repair_footprint_m']} "
        f"m reaches the owner 1.431 m away; got {at_solve}")
    assert at_solve["repairable_by_terrain_write"] is False, at_solve
    assert termination_class(dict(
        at_solve, contested_within_m=0.0,
        segment="T13-temple-harboursouth")) == "1A_FORBIDDEN", at_solve

    # AND THE CORRECTION IS NOT A BLANKET WIDENING.  R4 B's owner is
    # 3.606 m away and its 0.799 m step needs 0.029 m: both depths clear
    # it, and a change that moved THAT row too would be a tolerance
    # wearing a measurement's clothes.
    far = [(-1030.0, 1645.0, 0.0, "harbour_place", 99)]
    for d in (round(0.799 - lim, 4), round(inst.disposal_depth(0.799, lim),
                                           4)):
        o = repair_ownership(-1033.0, 1642.0, d, far, lim)
        assert o["ground_owned_by"] is None, (d, o)
    return [Finding(
        check="settlements/disposal_depth", ok=False,
        message=("a 0.794 m step 1.431 m from a flatten=FORBIDDEN owner is "
                 "1A_FORBIDDEN at the depth a disposal writes and "
                 "ACTIONABLE at the depth nothing writes: footprint "
                 f"{at_limit['repair_footprint_m']} m at {limit_depth} m "
                 f"against {at_solve['repair_footprint_m']} m at "
                 f"{solve_depth} m"),
        where=(14.0, -255.0), measured=solve_depth, limit=limit_depth,
        numbers=dict(step_m=step, slide_limit_m=round(lim, 4),
                     disposal_slope_m=inst.DISPOSAL_SLOPE_PER_M,
                     depth_at_the_limit_m=limit_depth,
                     depth_a_disposal_writes_m=solve_depth,
                     footprint_at_the_limit_m=at_limit["repair_footprint_m"],
                     footprint_a_disposal_needs_m=at_solve[
                         "repair_footprint_m"],
                     owner_distance_m=at_solve["ground_owner_distance_m"],
                     owner=at_solve["ground_owned_by"]),
        population=("the 6 step-shaped refusals on Ulfsland 245daa7f that "
                    "name a 1 m edge, asked at both depths; 1 moves"))]


def _neuter_a_footprint_sized_onto_the_limit():
    """RESTORE THE SHIPPED STATE: size the repair for the depth that leaves
    the transition EXACTLY ON the walker's slide limit - a depth no
    disposal in this tree writes."""
    def onto_the_limit(step_m, step_limit=None):
        lim = (A.instruments().STEP_LIMIT_PER_M if step_limit is None
               else step_limit)
        return max(float(step_m) - lim, 0.0)
    inst = A.instruments()
    real = inst.disposal_depth
    inst.disposal_depth = onto_the_limit

    def undo():
        inst.disposal_depth = real
    return undo


def _case_only_an_answered_question_becomes_a_class():
    """A TERMINATION CLASS IS AN ANSWER OR IT IS NOTHING, AND THE SHIPPED
    CLASSIFIER MANUFACTURED THREE KINDS OF CONFIDENCE IT DID NOT HAVE.

    MEASURED, full gate, Ulfsland save 245daa7f719ab69d, 106 refusals over
    eleven sections, artefact /tmp/hc_phase3.json:

      SELF-CONTEST.  6 of the 13 1B_CONTESTED rows named THEIR OWN
        SEGMENT'S terrain_write as the contester - settlements
        road_N0_brgs2_wmid seq 2629, road_S12_brgs1_boathouse seq 342,
        road_S1_portalhub_brgs2 seq 417, road_T13_temple_harboursouth
        seq 338, road_T9_wttown_dockshore seq 234, and roads
        T9-wttown-dockshore-sb01 against that same seq 234.  A road
        repairing its own carriageway destroys nobody's work, and a
        carriageway is ALWAYS inside its own write, so uncorrected 1B
        swallows every road step on every world this factory builds.
      HALF AN ANSWER REPORTED AS A WHOLE ONE.  Every settlements refusal
        in that artefact carried `repairable_by_terrain_write: null`,
        `ground_owned_by: null`, `ground_owner_distance_m: null` - the
        ownership half never ran - while `contested_by` was populated,
        and the classifier returned six confident 1B rows anyway.  1B is
        a CONJUNCTION: writer-permits AND another actor holds it.  With
        the writer half unasked the row is UNKNOWN.
      A SECOND BLOCKER WITH NO CLASS.  84 of the 106 had NO_CLASS, of
        which 54 roads rows are walker-blocking BODIES in the carriageway
        - 47 distinct bodies, 439 must-climb pieces - whose remedy is
        demolition or re-route and never a terrain cut.

    The three are one sentence: a class must be derived from what an
    instrument ANSWERED, never from the absence of an answer and never
    from an actor's own record.
    """
    lim = A.instruments().STEP_LIMIT_PER_M

    class _Writes:
        """The recorded footprints, CONSTRUCTED: a fixture that leans on
        the chain inherits the chain's mutability."""

        def __init__(self, who, within):
            self.who, self.within = who, within

        def at(self, x, z, margin=0.0, exact_only=True):
            return self.who if math.hypot(x, z) <= self.within + margin \
                else ""

    def join(writes):
        return [f for f in check_arrival(
            dict(segment="road_N0_brgs2_wmid", end="A", x=0.0, z=0.0,
                 datum=30.600, gap_m=0.0,
                 dest="the standing top of wood_floor",
                 heights_by_vertex={(0.0, 0.0): 31.153},
                 write_index=writes, forbidden_extents=[],
                 nums=dict(segment="road_N0_brgs2_wmid",
                           destination="wood_floor",
                           destination_kind="standing")),
            [31.153], 0.245, lim) if f.check == "terminus_datum"][0].numbers

    # 1 - THE ROW THE GATE GOT WRONG, END TO END through `check_arrival`
    # and not through a hand-built nums dict: N0 brgs2 wmid's own write
    # holding N0 brgs2 wmid's own repair.  Writer-clear, contested by
    # nobody else, therefore WORK.
    me = join(_Writes("terrain_write road_N0_brgs2_wmid seq 4011", 0.5))
    assert me["contested_by"] == \
        "terrain_write road_N0_brgs2_wmid seq 4011", me
    assert me["repairable_by_terrain_write"] is True, me
    assert me["termination_class"] == "ACTIONABLE", (
        "a road repairing its own carriageway contests NOBODY; counting "
        "the actor's own terrain_write as a contest puts every road step "
        f"on every world on the operator's desk; got {me}")
    # and the exclusion survives the THREE SPELLINGS the same actor
    # arrives under - roads names it `T9-wttown-dockshore`, settlements
    # `road_T9_wttown_dockshore`, the write index
    # `terrain_write road_T9_wttown_dockshore (seq 234)`.  A comparison
    # on the raw strings answers "different actor" for all three.
    assert termination_class(dict(
        segment="T9-wttown-dockshore",
        contested_by="terrain_write road_T9_wttown_dockshore (seq 234)",
        contested_within_m=1.926, repair_footprint_m=1.926,
        repairable_by_terrain_write=True)) == "ACTIONABLE", (
        "the roads and settlements spellings of ONE segment must resolve "
        "to one actor, or the exclusion works in exactly one section")
    # THE OTHER DIRECTION, or the exclusion is a rubber stamp that
    # empties 1B: a DIFFERENT actor's write is still a decision.
    assert termination_class(dict(
        segment="T9-wttown-dockshore",
        contested_by="terrain_write regrade_oc_boathouse_channel11 (seq "
                     "5393)",
        contested_within_m=1.926, repair_footprint_m=1.926,
        repairable_by_terrain_write=True)) == "1B_CONTESTED"
    # and a MIXED list - harbours passes a list - keeps the row in 1B on
    # the strength of the other actor alone
    assert termination_class(dict(
        segment="road_N1_wnorth_shelf",
        contested_by=["terrain_write road_N1_wnorth_shelf (seq 2656)",
                      "terrain_write regrade_oc_boathouse_channel11 "
                      "(seq 5393)"],
        contested_within_m=0.0, repair_footprint_m=5.59,
        repairable_by_terrain_write=True)) == "1B_CONTESTED"

    # 2 - HALF AN ANSWER IS NOT AN ANSWER.  Same contested row, ownership
    # half never asked, which is the state every settlements refusal in
    # the shipped artefact was actually in.
    assert termination_class(dict(
        segment="road_S12_brgs1_boathouse",
        contested_by="terrain_write regrade_oc_boathouse_channel11 (seq "
                     "5393)",
        contested_within_m=5.345, repair_footprint_m=5.345,
        repairable_by_terrain_write=None)) == "UNKNOWN", (
        "1B asserts the writer PERMITS the cut; with the writer half "
        "unasked that is a claim about an instrument that never ran")

    # 3 - THE SECOND BLOCKER, and the ordering that makes it reachable.
    # An obstruction row's HONEST terrain answer is False / 0.0 / no
    # owner: the question was asked, and a cut needs no ground because no
    # cut removes a building.  Below 1C that reads "no tool in this tree
    # performs the remedy" when demolition and re-route both do.
    body = dict(segment="S7-stathub-wtnorth",
                repairable_by_terrain_write=False, ground_owned_by=None,
                repair_footprint_m=0.0, contested_by=None,
                contested_within_m=0.0, obstruction_bodies=47,
                obstruction_pieces=439, obstruction_gap_m=0.0,
                # the width roads PUBLISHES beside the class, read off
                # its own `ctx.walker` census - carried here as evidence
                # the classifier must NOT consult
                walker_width_m=0.98)
    assert termination_class(body) == "1D_OBSTRUCTED", body
    # A WALKER-WIDE GAP BESIDE THE BODY IS A SEVERITY, NOT AN ABSENCE.
    # 45 of the 47 measured bodies are must_go_round, so a predicate of
    # `gap < walker` would class 45 of the 54 rows ACTIONABLE - "a repair
    # exists, the writer permits it, it destroys nothing" - about a
    # 457-piece town standing in the road.
    assert termination_class(dict(body, obstruction_gap_m=3.5)) == \
        "1D_OBSTRUCTED", "the gap is evidence; the body is the blocker"
    # and it is PRIOR to the terrain classes, not a tiebreak among them:
    # a row that is both owned by a FORBIDDEN record and has a town on it
    # is still demolish-or-reroute.
    assert termination_class(dict(
        body, ground_owned_by="boathouse_place seq 30")) == \
        "1D_OBSTRUCTED"
    # THREE-STATE ON THE BODY COUNT TOO.  0 is "the census ran and found
    # nothing" and None is "the census never ran"; neither is a blocker,
    # and both fall through to the terrain answer.
    assert termination_class(dict(body, obstruction_bodies=0)) == \
        "1C_UNSERVED", "0 bodies is an answer, and the answer is no body"
    assert termination_class(dict(body, obstruction_bodies=None)) == \
        "1C_UNSERVED"
    # and the whole vocabulary is ONE tuple, so four sections cannot fork
    # it - roads consumes this name rather than inventing a second.
    assert "1D_OBSTRUCTED" in TERMINATION_CLASSES, TERMINATION_CLASSES
    return [f for f in check_arrival(
        dict(segment="road_N0_brgs2_wmid", end="A", x=0.0, z=0.0,
             datum=30.600, gap_m=0.0, dest="the standing top of wood_floor",
             heights_by_vertex={(0.0, 0.0): 31.153},
             write_index=_Writes("terrain_write road_N0_brgs2_wmid seq 4011",
                                 0.5),
             forbidden_extents=[],
             nums=dict(segment="road_N0_brgs2_wmid", destination="wood_floor",
                       destination_kind="standing")),
        [31.153], 0.245, lim)]


def _case_a_stair_is_met_at_the_tread_under_the_walker():
    """A road that stops at the FOOT of a staircase has arrived; a gate
    that measures it against the TOP of the staircase has not.

    The fixture is a real `wood_stair` built from the corpus rather than
    a described ramp, because the whole claim is about which of its four
    parts the walker is standing on: two BOX parts (bottom tread, top
    tread, 0.951 m apart) and two swept prisms (the flights), and the
    prisms have no level face at all.  `level_top()` answers ONE number
    for the piece - its summit - and here the walker is 15.9 m out on the
    foot side, where the face at his feet is the bottom tread.  Against
    the summit the carriageway's applied 30.528 is refused by 1.021 m;
    against the face at his feet it is inside the 0.245 m tolerance.

    WHAT THIS CHANGED ON ULFSLAND, MEASURED on frozen save 9a242873, and
    it is less than the class is worth: both of this world's stair
    termini are approached from the stair's HIGH side, so the summit was
    not always the wrong end.
      T13 B (12.0, -258.0), applied 31.195, `wood_stair` at gap 0.99
        spanning 31.107..32.227.  Its summit is 32.227 and the terminus
        was refused by -1.032 m.  The arrival column is on the FLIGHT,
        whose upper plane reads 31.700 there, so the refusal is -0.505 m:
        the same defect, half the size, and now a true number.
      R1 A (1007.5, 721.5), applied 30.528, `wood_stair` at gap 15.93
        yawed 90 degrees so its TOP tread (31.549) faces the road and its
        bottom tread (30.599) faces the pier.  The face at the arrival
        column really is 31.549 and the -1.021 m refusal STANDS.
    """
    I = A.instruments()
    geom, _bp = I.load_geometry()
    stair = I.mk_solid("wood_stair", (1022.0, 30.50, 712.0), I.yaw(0.0),
                       geom["wood_stair"])
    grid = I.Grid([stair])
    summit = _nearest_standing(1007.5, 721.5, grid, {"wood_stair"}, 1.8)
    tread = _nearest_standing(1007.5, 721.5, grid, {"wood_stair"}, 1.8,
                              geom=geom)
    assert summit is not None and tread is not None, (
        f"the stair is 15.9 m away and inside ARRIVAL_REACH_M either way; "
        f"got summit={summit} tread={tread}")
    assert abs(summit[3] - stair.ymax) < 1e-6, (
        f"without the corpus the answer must still be the whole solid's "
        f"highest level face, {stair.ymax:.3f}; got {summit[3]:.3f}")
    assert tread[3] < summit[3] - 0.9, (
        f"the face at the walker's feet must be the BOTTOM tread, about "
        f"0.95 m below the summit {summit[3]:.3f}; got {tread[3]:.3f} - "
        f"this is the state R1 A and T13 B were refused in")
    # AND THE ARRIVAL ITSELF, ASKED AS A JOIN.  A road that ENDS on a
    # stair's bottom tread is the case where the face alone decides the
    # verdict: `gap_m` 0.0 puts it in the join population, so
    # `terminus_datum` is the only check looking and the answer is the
    # face and nothing else.  R1 A's own 15.93 m gap is an APPROACH and
    # its verdict is the berm and step checks' to give - that is the
    # operator's join/approach ruling, and this case must not re-litigate
    # it from the other side.
    road = 30.528

    def term(std):
        _gap, _tx, _tz, y, who, walkable, considered = std[:7]
        return dict(segment="road_R1_ulfsgard_t8junction", end="A",
                    x=1007.5, z=721.5, datum=y, gap_m=0.0,
                    dest=f"the standing top of {who}",
                    nums=dict(segment="road_R1_ulfsgard_t8junction",
                              destination=who, destination_kind="standing",
                              walkable_candidates=walkable,
                              build_pieces_within_reach=considered))
    bad = check_arrival(term(summit), [road], 0.245, I.STEP_LIMIT_PER_M)
    good = check_arrival(term(tread), [road], 0.245, I.STEP_LIMIT_PER_M)
    assert [f.check for f in bad] == ["terminus_datum"], (
        f"against the summit a road standing ON this stair is refused by "
        f"{abs(road - summit[3]):.3f} m, which is the known-bad verdict; "
        f"got {[f.check for f in bad]}")
    assert not good, (
        f"against the tread at his feet the road arrives to "
        f"{abs(road - tread[3]):.3f} m, inside 0.245 m; got "
        f"{[f.message for f in good]}")
    return []


def _case_a_pad_is_one_segment_end_s_destination():
    """S1-portalhub-brgs2, live, and the four refusals it produced.

    A at (-292, 214) stands ON portal-hall - gap 0.000 m, applied 37.100
    against a declared and standing 37.100, a PASS.  B at (-316, 230),
    39.6 m away, was ALSO given portal-hall at 17.3 m outside its
    rectangle and refused four times over the hillside between the two.
    What B stands on is a `wood_floor` bridge deck at 30.600 m AT GAP
    0.000, and segments.yaml's crossing contract declares
    `deck_datum_y: 30.6`.  A segment's two ends are two places: the
    nearer end takes the pad.
    """
    pad = dict(name="portal-hall", cx=-280.0, cz=216.0, hw=18.7, hd=18.7,
               datum=37.1, seq=801)
    at = {("road_S1_portalhub_brgs2", "A"): (-292.0, 214.0),
          ("road_S1_portalhub_brgs2", "B"): (-316.0, 230.0)}
    deck = _Slab("wood_floor", 30.600, hw=2.0, cx=-316.0, cz=230.0,
                 thick=0.13)
    grid = _Grid([deck])
    a_tier, a_pay = resolve_destination(
        "road_S1_portalhub_brgs2", "A", -292.0, 214.0, [pad], at, grid,
        {"wood_floor"}, 1.8, frozenset(), at[("road_S1_portalhub_brgs2",
                                              "B")])
    b_tier, b_pay = resolve_destination(
        "road_S1_portalhub_brgs2", "B", -316.0, 230.0, [pad], at, grid,
        {"wood_floor"}, 1.8, frozenset(), at[("road_S1_portalhub_brgs2",
                                              "A")])
    assert a_tier == "declared" and a_pay[1]["name"] == "portal-hall" \
        and a_pay[0] == 0.0, (
        f"the nearer end keeps the pad it stands on; got {a_tier} {a_pay}")
    assert b_tier == "standing" and b_pay[4] == "wood_floor" \
        and abs(b_pay[3] - 30.600) < 1e-6, (
        f"and the farther end must look further down the tiers, to the "
        f"deck it actually stands on; got {b_tier} {b_pay}")
    return []


def _case_the_first_inside_sample_is_inside_the_destination():
    """THE DEFECT THAT PRODUCED A WRONG DIAGNOSIS, not just a wrong
    number, and `ParBuried` caught it by cross-checking the section that
    owns pads.

    R3 B at (-912.89, 1896.47) meets vestvik-hut-2, whose resolved claim
    is 10.4 x 10.4 m at (-899.6, 1889.5) - so x -904.8..-894.4.  The
    rectangle gap is 8.281 m, and `s_m >= gap` is measured on the
    UNSNAPPED point: `snap` then put the first two "inside" samples at
    (-905.0, 1893.0) and (-905.0, 1892.0), both 0.200 m OUTSIDE the pad,
    on natural ground reading 35.214 against the pad's declared 34.370.
    This section duly reported vestvik-hut-2 as 0.844 m out of level -
    while the structures section finds no sample on that pad more than
    0.100 m off 34.370, and does report BOTH its Vestvik neighbours.
    With the footprint predicate the walk starts at (-904.0, 1892.0),
    which is in the pad.
    """
    cx, cz, hw, hd = -899.6, 1889.5, 5.2, 5.2
    x, z = -912.89, 1896.47
    ux, uz = cx - x, cz - z
    L = math.hypot(ux, uz)
    ux, uz = ux / L, uz / L
    gap = rect_distance_m(x, z, cx, cz, hw, hd)
    n = max(int(round((gap + INSIDE_PROBE_M) / SAMPLE_M)), 0)

    def contains(vx, vz):
        return abs(vx - cx) <= hw and abs(vz - cz) <= hd

    snap = A.instruments().snap
    _v, loose = probe_walk(x, z, ux, uz, n, gap, lambda *_a: True, snap)
    _v, tight = probe_walk(x, z, ux, uz, n, gap, contains, snap)
    assert loose[0] == (-905.0, 1893.0) and not contains(*loose[0]), (
        f"the case must reproduce the known-bad first sample and it must "
        f"be outside the pad; got {loose[:2]}")
    assert tight and all(contains(*v) for v in tight), (
        f"every sample called INSIDE must be inside the destination's own "
        f"footprint; got {tight[:3]}")
    assert tight[0] == (-904.0, 1892.0), (
        f"and the first of them is the first vertex on the walk that is "
        f"in the rectangle; got {tight[0]}")
    assert abs(gap - 8.281) < 0.001, f"the live gap, for the record: {gap}"
    return []


def _case_a_t_junction_onto_a_carriageway_is_a_junction():
    """R3's A end, and it is the last of this world's two "arrives
    nowhere" refusals that was not a road's fault.

    MEASURED: (-216.0, 2146.0) lies 2.000 m from N0b-wmid-wnorth's
    centreline, whose own recorded profile is half_width 3.0 m +
    shoulder 1.0 m, so the terminus stands ON that road's carriageway.
    segments.yaml declares R3's `endpoint_standoff_m` for
    `r3-n0b-junction` as 2.0 - the road stopped exactly where it said it
    would.  The section reported it beside its nearest build piece, a
    `piece_groundtorch` 17.5 m away, and its nearest declared pad,
    435.6 m away.

    AND THE REVERSE, on the terminus that must NOT move: R4's A end is
    0.016 m inside R3's own carriageway - closer than R3 A is to N0b -
    and it still resolves to the vestvik-hut-2 pad 10.3 m away, because a
    DECLARATION outranks a coincidence.  Without that ordering R4 A's
    0.920 m miss on its village would vanish into a deferral.
    """
    n0b = dict(nodes=[(-214.0, 1500.0), (-214.0, 2500.0)],
               half_width_m=3.0, shoulder_m=1.0)
    r3 = dict(nodes=[(-216.0, 2146.0), (-912.0, 1902.0)],
              half_width_m=3.0, shoulder_m=1.0)
    segs = {"road_N0b_wmid_wnorth": n0b,
            "road_R3_n0bjunction_vestvik": r3}
    at = {("road_R3_n0bjunction_vestvik", "A"): (-216.0, 2146.0),
          ("road_R3_n0bjunction_vestvik", "B"): (-912.0, 1902.0)}
    tier, payload = resolve_destination(
        "road_R3_n0bjunction_vestvik", "A", -216.0, 2146.0, [], at,
        _Grid([]), set(), 1.8, frozenset(), None, segs)
    assert tier == "junction" and "road_N0b_wmid_wnorth" in payload[0] \
        and "2.000 m" in payload[0], (
        f"a terminus standing on another road's paved surface is "
        f"junction.py's question, and the finding must carry the "
        f"distance; got {tier!r} {payload!r}")
    pad = dict(name="vestvik-hut-2", cx=-899.6, cz=1889.5, hw=5.2, hd=5.2,
               datum=34.37, seq=516)
    at4 = {("road_R4_vestvik_harbour", "A"): (-912.0, 1902.0),
           ("road_R4_vestvik_harbour", "B"): (-1015.0, 1636.0)}
    t4, p4 = resolve_destination(
        "road_R4_vestvik_harbour", "A", -912.0, 1902.0, [pad], at4,
        _Grid([]), set(), 1.8, frozenset(),
        at4[("road_R4_vestvik_harbour", "B")], segs)
    assert t4 == "declared" and p4[1]["name"] == "vestvik-hut-2", (
        f"and a terminus 0.016 m inside another carriageway that ALSO "
        f"has a declared pad within reach keeps the pad; got {t4!r} {p4!r}")
    return []


def _case_a_road_to_a_cape_arrives_on_the_cape():
    """S5-temple-southcape B, Fresh42 drive 4 (gate on save af96bfb62427).

    MEASURED: the terminus (-472.1, -694.8) is 2.1 m from the `southcape`
    extremity (-474.0, -694.0) and 28.9 m from a `stone_wall_2x1` course of
    the seed ruin StoneTowerRuins09 at (-444.5, -686.25), top 42.806 m; no
    pad within 30 m and no other road's end.  The section judged the road
    against the wall - "+8.2398 m ... onto the standing top of
    stone_wall_2x1", 1C - for a destination the road was never routed to.
    And the reverse: the same terminus with no extremity at this end (a
    road named for somewhere else) still falls to the standing search.
    """
    wall = _Slab("stone_wall_2x1", 42.806, hw=1.0, cx=-444.5, cz=-686.25,
                 thick=1.0)
    name = "road_S5_temple_southcape"
    at = {(name, "A"): (-28.1, -26.8), (name, "B"): (-472.1, -694.8)}
    capes = {"southcape": (-474.0, -694.0), "eastlobe": (874.0, 362.0)}
    tier, payload = terminus_tier(
        name, "B", -472.1, -694.8, [], at, _Grid([wall]), {"stone_wall_2x1"},
        1.8, frozenset(), at[(name, "A")], {}, None, capes)
    assert tier == "extremity" and payload == ("southcape", 2.06), (
        f"a road that ends 2.1 m from the cape it was routed to arrives on "
        f"that cape, not on a ruin's wall 28.9 m away; got {tier!r} "
        f"{payload!r}")
    other = "road_S5_temple_lhsouth"
    t2, p2 = terminus_tier(
        other, "B", -472.1, -694.8, [], {(other, "A"): (-28.1, -26.8),
                                         (other, "B"): (-472.1, -694.8)},
        _Grid([wall]), {"stone_wall_2x1"}, 1.8, frozenset(), (-28.1, -26.8),
        {}, None, capes)
    assert t2 == "standing" and p2[4] == "stone_wall_2x1", (
        f"a terminus whose segment names no cape here is still a search "
        f"result; got {t2!r} {p2!r}")
    return []


def _drive5_plan(**over) -> dict:
    """Fresh42 drive 5's plan, in miniature (PLANNED_END_DEPENDS_ON): the
    three ends that came back "meet NOTHING", what each was routed to, and
    what the build refused.  Coordinates are the plan's and the save's."""
    def seg(a, b, ea, eb, arrival=None, water_trim=None):
        return dict(a=a, b=b, ends={"A": ea, "B": eb},
                    arrival=arrival or {}, water_trim=water_trim or {})
    planned = dict(
        segs={
            "T7-stathub-stenvik": seg(
                "stathub", "stenvik", (88.05, 361.46), (335.9, -274.8),
                {"stenvik": {"why": "no pad holds this node: nothing to "
                                    "arrive on"}}),
            "T5-temple-dockshore": seg(
                "temple", "dockshore", (-24.1, -30.8), (3.74, -330.64),
                {"dockshore": {"why": "stopped 46.44 m short: water"}},
                {"dockshore": 10}),
            "S8-wtsouth-treesth": seg(
                "wtsouth", "treesth", (-672.1, -385.85), (-699.95, -282.34)),
            "J-southcape-wtsouth": seg(
                "southcape", "wtsouth", (-472.1, -694.8), (-672.1, -385.85)),
        },
        nodes={"stenvik": dict(xz=(315.9, -248.8), kind="town", cites=""),
               "dockshore": dict(xz=(-7.5, -416.5), kind="shoreline",
                                 cites="MEASURED, worlds/<world>/*/"
                                       "placements.yaml solved centre for "
                                       "this seed"),
               "wtsouth": dict(xz=(-672.1, -372.8), kind="watchtower",
                               cites="")},
        sites={"stenvik": dict(id="stenvik", type="town",
                               xz=(315.9, -248.8), radius=100.0),
               "wtsouth": dict(id="wt-south", type="watchtower",
                               xz=(-672.1, -372.8), radius=16.1)},
        site_refusals={"wt-south": [dict(
            seq=2077, step="5", unit="wt-south",
            text="the clearing cylinder holds {'CastleKit_groundtorch': 1, "
                 "'dungeon_forestcrypt_door': 3}, which are not in the "
                 "measured natural set")]},
        unit_refusals={"J-southcape-wtsouth": [dict(
            step=4, name="road_ground", rc=7)]},
        built={"T7-stathub-stenvik", "T5-temple-dockshore",
               "S8-wtsouth-treesth"},
        journal="pipeline/build/buildstage.json")
    planned.update(over)
    return planned


_DRIVE5_PADS = [
    dict(name="stenvik-longhouse-2", cx=337.8, cz=-319.8, hw=9.75, hd=12.15,
         datum=36.59, seq=1837),
    dict(name="stenvik-cottage-3", cx=359.1, cz=-236.2, hw=6.5, hd=6.85,
         datum=32.88, seq=1850),
    dict(name="early-dock", cx=-7.5, cz=-416.5, hw=26.4, hd=18.4,
         datum=26.94, seq=1700),
    dict(name="tree-sth-2", cx=-688.9, cz=-283.6, hw=8.0, hd=8.0,
         datum=61.43, seq=1790),
]


def _end_tier(name, end, x, z, other, planned, pads=None):
    """`terminus_tier` over drive 5's empty surroundings: no standing top,
    no other built road's end, no cape."""
    at = {(name, end): (x, z), (name, "B" if end == "A" else "A"): other}
    return terminus_tier(name, end, x, z, _DRIVE5_PADS if pads is None
                         else pads, at, _Grid([]), set(), 1.8, frozenset(),
                         other, {}, None, {}, planned)


def _end_findings(name, end, x, z, other, planned, ground=None,
                  pads=None) -> list[Finding]:
    """One terminus from `terminus_tier` to its rows, the way `run` routes
    each tier this block adds: a district or an unmeasured end through
    `check_population`, a short end through its walk READ by `ground(xz)`,
    a refused end through `refused_destination`."""
    tier, d = _end_tier(name, end, x, z, other, planned, pads)
    if tier == "short":
        walk = short_walk(x, z, d["pad"], A.instruments().snap)
        return [short_of_destination(name, end, x, z, d, walk,
                                     [ground(v) for v in walk], 30.0)]
    if tier == "refused":
        return [refused_destination(name, end, x, z, d)]
    return check_population(
        {name: object()}, [object()], [object()], 0.245, 0.49,
        "ledger.jsonl",
        [(name, end, x, z)] if tier == "none" else None, None, None,
        [(name, end, x, z, d)] if tier == "district" else None)


def _owned_refusals(fs: list[Finding]) -> list[Finding]:
    """The hard refusals that NAME AN OWNER: what this block adds to a row
    that used to say only "meets NOTHING"."""
    return [f for f in fs if not f.ok and not f.advisory
            and (f.numbers or {}).get("owner")]


def _case_a_road_to_a_town_arrives_on_its_district():
    """T7-stathub-stenvik B, Fresh42 drive 5 (gate on save 8c8c54096789).

    MEASURED: the terminus (335.9, -274.8) is 32.8 m from the `stenvik`
    town node, inside its declared 100 m district; no pad holds the node,
    and the nearest live pad, stenvik-longhouse-2, is 32.85 m away - so the
    section called it a road that meets NOTHING.  It arrives on the
    district's ground, reported with its nearest building.  And the two
    ways it must not: a pad HOLDING the node is that end's destination
    even inside the district (SHORT, never waved through as district), and
    a plan whose end is not this polyline's is another build's and is not
    read at all."""
    name = "road_T7_stathub_stenvik"
    fs = _end_findings(name, "B", 335.9, -274.8, (88.05, 361.46),
                       _drive5_plan())
    for f in fs:
        if (f.numbers or {}).get("district_termini"):
            assert "stenvik-longhouse-2" in f.message \
                and f.numbers["at"] == [(name, "B", 335.9, -274.8, "stenvik",
                                         "stenvik-longhouse-2", 32.85)], (
                f"the district row names the town and its nearest building; "
                f"got {f.numbers}")
    hall = dict(name="stenvik-hall-1", cx=315.9, cz=-248.8, hw=1.5, hd=1.5,
                datum=31.57, seq=1826)
    t2, d2 = _end_tier(name, "B", 335.9, -274.8, (88.05, 361.46),
                       _drive5_plan(), _DRIVE5_PADS + [hall])
    assert t2 == "short" and d2["pad"]["name"] == "stenvik-hall-1" \
        and d2["holds_node"], (
            f"a pad holding the node is where the plan routed this end, so "
            f"stopping short of it is SHORT, not a district arrival; got "
            f"{t2!r} {d2!r}")
    other = _drive5_plan()
    other["segs"] = dict(other["segs"])
    other["segs"]["T7-stathub-stenvik"] = dict(
        other["segs"]["T7-stathub-stenvik"],
        ends={"A": (88.05, 361.46), "B": (340.9, -274.8)})
    t3, _d3 = _end_tier(name, "B", 335.9, -274.8, (88.05, 361.46), other)
    assert t3 == "none", (
        f"a plan whose end is 5 m from this polyline's is another build's "
        f"plan and answers for nothing here; got {t3!r}")
    return fs


def _case_a_road_short_of_a_dock_across_water_names_the_placement():
    """T5-temple-dockshore B, Fresh42 drive 5 (gate on save 8c8c54096789).

    MEASURED: the `dockshore` node (-7.5, -416.5) stands inside the live
    early-dock pad, 67.46 m from the terminus (3.7, -330.6); the plan's own
    record is "stopped 46.44 m short: water" with 10 stations of wet tail
    trimmed, and the applied ground of the walk to the pad is under the
    30.0 m plane from 9.4 m to 68.9 m out, 6.05 m deep.  It was "meets
    NOTHING", 1C with no owner.  The walk READ names whose the gap is: the
    dock's placement when it is water, the leg's arrival when it is dry,
    and the reading itself when it was not taken."""
    name = "road_T5_temple_dockshore"
    x, z = 3.74, -330.64

    def bank_then_sea(v):
        return 31.5 if math.hypot(v[0] - x, v[1] - z) < 9.0 else 23.95
    fs = _end_findings(name, "B", x, z, (-24.1, -30.8), _drive5_plan(),
                       bank_then_sea)
    for f in _owned_refusals(fs):
        n = f.numbers
        assert f.check == "terminus_short_of_destination" \
            and n["destination"] == "early-dock" and n["gap_m"] == 67.46 \
            and n["wet_vertices"] > 0 and n["deepest_m"] == 6.05 \
            and "placement of early-dock" in n["owner"] \
            and _short_family(n)[0] == "SOURCE_CHANGE", (
                f"water between the bank and the dock names the dock's "
                f"PLACEMENT; got {f.check} {n}")
    for f in _owned_refusals(_end_findings(
            name, "B", x, z, (-24.1, -30.8), _drive5_plan(),
            lambda _v: 31.5)):
        assert "arrive_on_pads" in f.numbers["owner"] \
            and not f.numbers["wet_vertices"], (
                f"dry ground between names the leg's ARRIVAL; got "
                f"{f.numbers}")
    for f in _end_findings(name, "B", x, z, (-24.1, -30.8), _drive5_plan(),
                           lambda _v: None):
        if f.check == "terminus_short_of_destination":
            assert _short_family(f.numbers)[0] == "CENSUS_NOT_TAKEN", (
                f"a walk nobody read names the reading, not an owner; got "
                f"{f.numbers}")
    return _owned_refusals(fs)


def _case_a_road_whose_destination_the_build_refused_names_the_refusal():
    """S8-wtsouth-treesth A, Fresh42 drive 5 (gate on save 8c8c54096789).

    MEASURED: the plan arrived this end on wt-south's pad edge and joined
    it to the network with J-southcape-wtsouth, whose B end is 0.00 m from
    it.  The build REFUSED wt-south's site_pad (ledger seq 2077, step 5:
    the clearing cylinder holds 3 dungeon_forestcrypt_door) and REFUSED J
    at step 4 road_ground rc=7, so no pad and no road is here - and the
    section said "meets NOTHING", 1C, owner none.  The row must name both
    refusals and their units; a planned leg the build never attempted is
    a stage not run (1E), not a refusal."""
    name = "road_S8_wtsouth_treesth"
    x, z = -672.1, -385.85
    fs = _end_findings(name, "A", x, z, (-699.95, -282.34), _drive5_plan())
    for f in _owned_refusals(fs):
        n = f.numbers
        assert f.check == "terminus_destination_refused" \
            and "wt-south's site_pad was REFUSED by build step 5" \
            in n["owner"] and "J-southcape-wtsouth" in n["owner"] \
            and "rc=7" in n["owner"] \
            and _refused_family(n)[0] == "SOURCE_CHANGE", (
                f"both refusals and their units must be named; got "
                f"{f.check} {n}")
    for f in _owned_refusals(_end_findings(
            name, "A", x, z, (-699.95, -282.34),
            _drive5_plan(unit_refusals={}))):
        fam = _refused_family(f.numbers)
        assert fam[0] == "UPSTREAM_STAGE_NOT_RUN" \
            and "J-southcape-wtsouth" in fam[1], (
                f"a planned leg with no build record was never attempted: "
                f"1E naming it; got {fam}")
    return _owned_refusals(fs)



def _case_an_unstood_declaration_names_which_defect_it_is():
    """ONE CLASS, TWO REPAIRS, and the finding has to say which.

    vestvik-hut-2 declares 34.370 and is met by two termini.  R4 A's
    footfall reads 34.370 - EXACTLY the declaration - and R3 B's reads
    35.214, two points inside one 10.4 x 10.4 m pad.  So the number is
    achievable and THE PAD IS NOT LEVEL; repairing the declaration would
    edit a figure that is right.  Strip R4's reading and the same
    disagreement means the opposite: nobody stands on 34.370 and the
    DECLARATION is the defect.  The two states differ only in the
    population of readings, which is the point.
    """
    base = dict(segment="road_R3_n0bjunction_vestvik", end="B", x=-912.9,
                z=1896.5, datum=35.214, declared_datum=34.370, gap_m=8.3,
                dest="pad 'vestvik-hut-2'", samples=2,
                datum_source="the applied ground",
                nums=dict(segment="road_R3_n0bjunction_vestvik",
                          destination="vestvik-hut-2",
                          destination_kind="declared"))
    both = dict(base, pad_readings=[
        ("road_R4_vestvik_harbour", "A", 34.370),
        ("road_R3_n0bjunction_vestvik", "B", 35.214)])
    lone = dict(base, pad_readings=[
        ("road_R3_n0bjunction_vestvik", "B", 35.214)])
    fb = [f for f in check_arrival(both, [35.290, 35.29], 0.245, 0.7813)
          if f.check == "destination_datum_unstood"]
    fl = [f for f in check_arrival(lone, [35.290, 35.29], 0.245, 0.7813)
          if f.check == "destination_datum_unstood"]
    assert fb and fb[0].numbers["repair"] == "pad" \
        and abs(fb[0].numbers["pad_spread_m"] - 0.844) < 1e-6, (
        f"a declaration some terminus DOES stand on is achievable; the "
        f"pad is the repair: {fb[0].numbers if fb else None}")
    assert "NOT LEVEL" in fb[0].message and "34.370" in fb[0].message, (
        "and the message must name the cause and the number")
    assert fl and fl[0].numbers["repair"] == "declaration", (
        f"a declaration nobody stands on is the repair itself: "
        f"{fl[0].numbers if fl else None}")
    return fb + fl


# -- generated content ------------------------------------------------------
_VILLAGE_MARKER = (1217.8, 513.2, 32.0, "unidentified")


def _village_pieces():
    """The generated village at (1218, 512) as the census holds it: a wooden
    house cluster, and a goblin_bed, which this project has never placed."""
    out = []
    for i in range(6):
        for j in range(4):
            out.append(("wood_floor", 1214.0 + i, 510.0 + j))
    out += [("wood_roof_45", 1216.0, 511.0), ("wood_wall_half", 1217.0,
                                              512.0),
            ("goblin_bed", 1218.0, 512.0)]
    return out


def _case_seat_on_a_generated_village():
    """The two village seats that were correctly refused, reconstructed: a
    20 x 20 m seat centred on the generated village at (1218, 512).
    MEASURED with this rule against the live census: 55 generated pieces
    inside that rectangle."""
    pad = dict(name="sudrvik-proposed-seat", cx=1218.0, cz=512.0, hw=10.0,
               hd=10.0, datum=34.0, seq=0)
    fs = check_seat_on_generated([pad], _village_pieces(), [_VILLAGE_MARKER],
                                 lambda *a, **k: False)
    return fs


def _case_a_packed_wire_declares_every_position():
    """THE DECLARATION INDEX READ THE FIRST COMMAND OF EACH LINE.

    MEASURED on this chain: 226 of 421 spawn_plan records have
    `len(wire) != command_count`, because a record packs many
    `spawn_object` commands onto one line - the whole village
    `harbour-village-sudrvik` is ONE record, seq 3079, declaring
    `{fb_house_02: 4, fb_house_03: 3, fb_viking_house_04: 1,
    fb_viking_house_06: 2, fb_viking_house_08: 1}` = 11 whole-house
    prefabs from a single anchor at (1234.0, 491.3).  A `^`-anchored
    `match` saw the first of each line, so the index resolved 13,138
    placements where a packed-aware scan resolves 24,351 (+11,213) and
    `fb_*` placements went from 9 to 25 - and FIVE of this section's own
    pads were refused as seated on generated content, each on exactly one
    undeclared `fb_*` house that our own chain had placed.

    `pos=` is z,x,y on this wire format, so the case pins the ORDER too:
    a transposed read is an isometry under any distance test and has to
    be asserted directly.
    """
    line = ("spawn_object fb_house_02 pos=436.0,1116.0,32.0 rot=0,0,0; "
            "spawn_object fb_house_03 pos=510.0,1240.0,33.0 rot=0,0,0; "
            "spawn_object fb_viking_house_06 pos=478.0,1116.0,34.0")
    got = spawns_on(line)
    assert len(got) == 3, (
        f"every command on a packed line is a declaration, not just the "
        f"first; got {len(got)} of 3: {got}")
    assert got[0] == ("fb_house_02", 1116.0, 436.0), (
        f"pos= is z,x,y: the first command stands at x 1116.0, z 436.0, "
        f"and a transposed read is an isometry no distance test can "
        f"catch; got {got[0]}")
    assert [g[0] for g in got] == ["fb_house_02", "fb_house_03",
                                   "fb_viking_house_06"], got
    return []


def _case_a_short_declaration_index_is_loud_not_small():
    """THE HALF THAT MATTERS, both directions, with no world.

    Splitting a packed line makes the index see MORE; this makes a SHORT
    READ LOUD.  MEASURED on this chain after the packed-line fix: 377 of
    377 live spawn_plan records parse to EXACTLY their own
    `command_count`, 0 short and 0 over - and getting that identity to
    hold is what taught the reader its two errors, because wire UNION
    blob made 159 of 377 read OVER and keying on (prefab, x, z) with no y
    made 99 read SHORT (lh-south declares 1,255 commands and has 517
    distinct xz, since a blueprint stacks wall courses at one footprint).
    Neither was a defect in the chain; both were the reader asking a
    question the chain does not answer.
    """
    packed = ("spawn_object wood_floor pos=1.0,2.0,3.0; "
              "spawn_object wood_floor pos=4.0,5.0,6.0; "
              "spawn_object wood_beam pos=7.0,8.0,9.0")
    honest = dict(op="spawn_plan", seq=1,
                  params=dict(site_id="honest", command_count=3),
                  wire=[packed])
    lying = dict(op="spawn_plan", seq=2,
                 params=dict(site_id="lying", command_count=48),
                 wire=[packed])
    blobs = Path("/nonexistent-blobs")
    good = placement_accounting([honest], blobs)
    bad = placement_accounting([lying], blobs)
    assert good == dict(exact=1, short=[], over=[], plans=1), (
        f"a record whose commands read back at its declared size is "
        f"EXACT, and a packed line is three commands: {good}")
    assert len(bad["short"]) == 1 and bad["short"][0]["parsed"] == 3 \
        and bad["short"][0]["declared"] == 48, (
        f"and a record that declares 48 and yields 3 must be reported "
        f"SHORT with both numbers, never silently accepted: {bad}")
    return []


def _case_our_own_anchored_body_is_not_generated():
    """THE CLASS, from the live world: FOUR of our own buildings reported
    as content the world generated.

    portal-hall, MEASURED on save 2c7011d2: 2,249 build pieces inside the
    pad's own 37.4 x 37.4 m rectangle, inside an UNIDENTIFIED marker's
    substituted 32.0 m radius, declared by no live ledger record - and
    they are `Piece_flametal_pillar`, `Piece_grausten_pillarbase_medium`,
    `Piece_grausten_floor_1x1`, which is OUR PORTAL HALL.  The chain holds
    a `spawn_plan` anchored at (-280.0, 216.0) with role
    `portal_hall_body` declaring 47 prefabs including those three; it is
    not in the LIVE chain because a later record retired it, and
    `declared_placements` resolves 13,138 placements against 124,980
    build-piece ZDOs - 10.5 % - so silence there says nothing.

    Same shape at lh-west (798), stenvik-longhouse-1 (206) and
    stenvik-longhouse-2 (164).  3,417 pieces, residual 0 under this rule.
    """
    pad = dict(name="portal-hall", cx=-280.0, cz=216.0, hw=18.7, hd=18.7,
               datum=37.1, seq=801)
    ours = [("Piece_flametal_pillar", -278.0, 214.0),
            ("Piece_grausten_floor_1x1", -277.0, 215.0),
            ("iron_floor_1x1_v2", -276.0, 216.0)]
    # THE MARKER MUST ACTUALLY REACH THE PIECES OR THE CASE PROVES
    # NOTHING.  Live, portal-hall's capturing marker stands 34.4 m from
    # the pad centre with a 32.0 m substituted radius, so it covers the
    # near corner of a 37.4 m rectangle and not its middle; this one is
    # 24.0 m out so all three pieces are inside it, MEASURED here rather
    # than assumed - a fixture whose marker misses would accept for the
    # wrong reason.
    marker = (-280.0, 240.0, 32.0, "unidentified")
    anchors = [dict(role="portal_hall_body", site_id="portal-hall",
                    x=-280.0, z=216.0,
                    prefabs={"Piece_flametal_pillar",
                             "Piece_grausten_floor_1x1",
                             "iron_floor_1x1_v2"})]
    assert len(generated_inside((pad["cx"], pad["cz"], pad["hw"],
                                 pad["hd"]), ours, [marker],
                                lambda *a, **k: False)) == 3, (
        "all three pieces must be inside the marker's reach and declared "
        "by nothing, or this case is not the state it claims to be")
    fs = check_seat_on_generated([pad], ours, [marker],
                                 lambda *a, **k: False, anchors, 8.0)
    assert not [f for f in fs if not f.ok], (
        "a body an anchored plan of ours DECLARES at this pad is ours, "
        "however little of it the per-piece index happens to resolve")
    return fs


def _case_an_anchor_does_not_launder_a_village():
    """THE REVERSE, and it is what keeps the rule from being an amnesty.

    The same generated village at (1218, 512), with one of OUR anchors
    9.0 m away declaring `wood_floor` - a real possibility, since we build
    out of the same wood the world does.  Its `goblin_bed` and
    `wood_roof_45` are prefabs no plan of ours declares here, so the seat
    MUST still refuse.  An anchor clears the prefabs IT DECLARED and
    nothing else; ownership is not transitive across a rectangle.
    """
    pad = dict(name="sudrvik-proposed-seat", cx=1218.0, cz=512.0, hw=10.0,
               hd=10.0, datum=34.0, seq=0)
    anchors = [dict(role="town_body", site_id="sudrvik-proposed",
                    x=1218.0, z=521.0, prefabs={"wood_floor"})]
    fs = check_seat_on_generated([pad], _village_pieces(), [_VILLAGE_MARKER],
                                 lambda *a, **k: False, anchors, 8.0)
    bad = [f for f in fs if not f.ok]
    assert bad, ("an anchor that declares wood_floor must not clear a "
                 "goblin_bed this project has never placed")
    assert bad[0].numbers["cleared_by_our_own_anchors"] == 24 \
        and bad[0].numbers["generated_pieces"] == 3, (
        "the split must be REPORTED with both numbers, not folded into "
        f"one count: {bad[0].numbers}")
    return fs



def _case_our_own_body_is_not_generated():
    """wt-north: 8 of our own watchtower's pieces stand inside an
    UNIDENTIFIED marker's worst-case 32 m radius and every one of them is
    declared by the ledger.  A marker-only rule refuses this seat; the
    conjunction accepts it, and that is why the rule is a conjunction."""
    pad = dict(name="wt-north", cx=146.0, cz=1181.0, hw=10.0, hd=10.0,
               datum=32.42, seq=3099)
    ours = [("wood_beam", 148.8, 1185.0), ("stone_stair", 144.8, 1186.0),
            ("woodwall", 146.8, 1185.0)]
    marker = (146.0, 1155.0, 32.0, "unidentified")
    return check_seat_on_generated([pad], ours, [marker],
                                   lambda *a, **k: True)


def _case_our_own_body_outside_any_marker():
    """sudrberg-keep: 3,576 pieces of our own castle, inside no marker at
    all.  A declaration-only rule calls them foreign; the conjunction does
    not."""
    pad = dict(name="sudrberg-keep", cx=2406.0, cz=-3354.0, hw=22.0, hd=22.0,
               datum=121.84, seq=3098)
    ours = [("stone_pillar", 2406.0, -3354.0),
            ("wood_wall_log_4x0.5", 2407.0, -3355.0)]
    return check_seat_on_generated([pad], ours, [], lambda *a, **k: False)


def _case_diagonal_pair_uses_its_own_allowance():
    """THE LATTICE-EDGE WALK, BOTH HALVES.

    A walker crossing a cell diagonally does NOT travel the diagonal: he
    crosses two 1 m mesh edges through one of the two corners, and he
    takes the way round that works.  So:
      * a diagonal whose EASIER corner route climbs 0.5 m per edge is
        walkable and must be ACCEPTED, even though the corner-to-corner
        rise is 1.000 m;
      * a diagonal whose BOTH corners force a 1.000 m edge is a wall and
        must be REFUSED - and judging that pair at the 1.414 m allowance
        (1.1049 m) would clear it, which is exactly how a 6.349 m wall on
        N1 stayed invisible to the shipping check;
      * a corner nobody read is UNRESOLVED, never clean.
    """
    # THE DATUM IS THE HEIGHT THE WALK ENDS ON, so the last edge - from
    # the ground the walk ends on ONTO the destination - is 0.000 and
    # this case measures the DIAGONAL and nothing else.  With a datum
    # that disagrees with the walk's own end, the final-edge rule would
    # refuse all three fixtures and the diagonal would never be reached.
    base = dict(_term(2.0, 31.0), samples=2,
                vertices=[(0.0, 0.0), (1.0, 1.0)])
    prof = [30.0, 31.0]
    easy = dict(base, heights_by_vertex={(0.0, 0.0): 30.0, (1.0, 1.0): 31.0,
                                         (1.0, 0.0): 30.5, (0.0, 1.0): 31.5})
    wall = dict(base, heights_by_vertex={(0.0, 0.0): 30.0, (1.0, 1.0): 31.0,
                                         (1.0, 0.0): 31.0, (0.0, 1.0): 30.0})
    blind = dict(base, heights_by_vertex={(0.0, 0.0): 30.0, (1.0, 1.0): 31.0})
    e = [f for f in check_arrival(easy, prof, 0.245, 0.7813)
         if f.check == "terminus_step"]
    w = [f for f in check_arrival(wall, prof, 0.245, 0.7813)
         if f.check == "terminus_step"]
    u = [f for f in check_arrival(blind, prof, 0.245, 0.7813)
         if f.check == "terminus_step_unresolved"]
    assert not e, ("a diagonal whose easier corner route climbs 0.5 m per "
                   "1 m edge is walkable and must not be refused")
    assert w, ("a diagonal both of whose corner routes force a 1.000 m "
               "edge is a wall; the 1.414 m allowance would clear it")
    assert u, "an unread corner must be UNRESOLVED, never clean"
    return w + u


def _case_empty_population_is_refused():
    """THE STATE THIS SECTION WAS ACTUALLY IN, for about ten minutes: the
    road loader returned nothing, zero termini were judged, and the section
    reported PASS on a world with 25 built roads and a 0.622 m datum miss
    in it.  Every green check this project has paid for looked like this
    from the inside."""
    return check_population({}, [], [], 0.245, 0.49, "ledger.jsonl")


def _case_population_present_is_accepted():
    segs = {"road_A": object()}
    termini = [object()]
    pads = [object()]
    return check_population(segs, termini, pads, 0.245, 0.49)


def _case_roads_meet_no_declared_datum():
    """25 roads, 50 termini, and not one destination that recorded the
    datum a road is supposed to arrive on.  That is not a clean world; it
    is an unmeasured one, and the two must never print the same."""
    segs = {f"road_{i}": object() for i in range(25)}
    return check_population(segs, [], [object()], 0.245, 0.49)


# ---------------------------------------------------------------------------
# NEUTERS - each one RESTORES A DEFECT VERBATIM and returns its own undo.
#
# `registry.Case.neuter` is how a case proves it is still LOAD-BEARING
# today rather than on the day it was written, and it is the enforceable
# form of the fixture-drift rule: the question is not "does this case
# borrow" - every case must call the real check or it tests a copy - but
# IF THE THING IT TOUCHES MOVES, DOES THIS CASE GO RED OR QUIET.
#
# These were all run by hand before the harness could do it - each of the
# seven defects reproduced verbatim - so wiring them changes nothing
# about what they prove; it moves the proof out of a throwaway script and
# into the gate, where it is reviewable and cannot be lost.
# ---------------------------------------------------------------------------
def _swap(name: str, replacement):
    """Put `replacement` in this module under `name`; return the undo.

    Module-global rebinding rather than monkeypatching an import, because
    every call site in this file resolves the name through the module
    dict - so this restores the defect for the REAL code path and not for
    a copy the case happens to hold.
    """
    real = globals()[name]
    globals()[name] = replacement

    def undo():
        globals()[name] = real
    return undo


def _neuter_reach_from_own_footing():
    """Take the reach-from-own-footing filter back out of the chooser, so
    `level_top`'s highest face - an arch's crown - is offered again."""
    real = _nearest_standing
    return _swap("_nearest_standing",
                 lambda x, z, g, bp, wh=0.0, bo=frozenset():
                 real(x, z, g, bp, wh, bo, _reach_up_m=float("inf")))


def _neuter_arrival_face():
    """Read the whole solid's highest level face again, whatever corpus
    the caller hands in - the state in which a staircase's summit was the
    height a road at its foot was judged against."""
    real = _nearest_standing
    return _swap("_nearest_standing",
                 lambda x, z, g, bp, wh=0.0, bo=frozenset(),
                 _reach_up_m=REACH_UP_M, geom=None:
                 real(x, z, g, bp, wh, bo, _reach_up_m, geom=None))


def _neuter_reach_envelope():
    """Believe whatever the chooser handed back, however far above the
    road it is - the state in which a battlement 12.818 m overhead
    becomes a confident datum miss instead of an UNMEASURED terminus."""
    real = check_arrival

    def believe(term, profile, tol, step_limit):
        t = dict(term)
        t["nums"] = dict(term["nums"], destination_kind="declared")
        return real(t, profile, tol, step_limit)
    return _swap("check_arrival", believe)


def _neuter_join_split():
    """Call EVERY terminus an approach, so no join is ever asked the join
    question - the state in which a bridge deck buried half a metre in
    its own bank reads as a clean arrival, because at gap 0.000 there is
    no ground between road and deck for the berm or step checks to
    measure and nothing else looks."""
    real = globals()["JOIN_GAP_M"]
    globals()["JOIN_GAP_M"] = -1.0

    def undo():
        globals()["JOIN_GAP_M"] = real
    return undo


def _neuter_final_edge_onto_the_destination():
    """Stop judging the last 1 m edge - the one from the ground the walk
    ends on ONTO the destination itself - so an approach has nobody
    measuring the step a walker actually takes last."""
    real = check_arrival

    def no_last_edge(term, profile, tol, step_limit):
        return real(dict(term, gap_m=min(term["gap_m"], JOIN_GAP_M)),
                    profile, tol, step_limit)
    return _swap("check_arrival", no_last_edge)


def _neuter_riser_published_as_a_lattice_edge():
    """RESTORE THE DEFECT VERBATIM at the publication boundary: the last
    transition onto a destination republished as `[last_vertex, term.x,
    term.z]` - two points `gap_m` apart - described as a 1 m lattice edge,
    with the sign thrown away, exactly as gate C's S12 A refusal carried
    it."""
    real = check_arrival

    def as_an_edge(term, profile, tol, step_limit, *a, **k):
        out = real(term, profile, tol, step_limit, *a, **k)
        far = [round(term["x"], 1), round(term["z"], 1)]
        for f in out:
            n = f.numbers
            if f.check == "terminus_step" and n.get("judged_as") == "riser":
                n["at"] = list(n["at"]) + far
                n["judged_as"] = "lattice_edge"
                n["edge_m"] = 1.0
                n.pop("rise_m", None)
                n.pop("onto", None)
            for r in (n.get("repair_population") or []):
                if r.get("onto_the_destination"):
                    r["at"] = list(r["at"]) + far
                    r.pop("rise_m", None)
                    r.pop("judged_as", None)
        return out
    return _swap("check_arrival", as_an_edge)


def _neuter_last_footfall_substituted():
    """RESTORE THE DEFECT VERBATIM: scan BACKWARDS for the first vertex
    that was read and publish `verts[-1]` as the place it was read, so an
    unread last footfall is answered by a neighbour instead of refusing as
    unresolved."""
    def substituted(verts, hbv):
        for v in reversed(verts):
            if v in hbv:
                return verts[-1], hbv[v]
        return verts[-1], None
    return _swap("_last_footfall", substituted)


def _neuter_repair_ownership():
    """RESTORE THE DEFECT: publish the refusal with its number and its
    population and NOTHING about whether the one sanctioned write path
    would accept the lowering it proposes - the state all four of gate
    C's `terminus_datum` refusals shipped in."""
    return _swap("repair_ownership",
                 lambda _x, _z, _d, _o, _l: {})


def _neuter_contested_ground():
    """RESTORE THE DEFECT: propose a cut and say nothing about whether the
    ground being cut is already held by another actor's terrain_write -
    the state in which 811 of 812 writer-clear cut vertices were
    indistinguishable from the one that belonged to nobody."""
    return _swap("contested_ground",
                 lambda _x, _z, _r, _w: {})


def _neuter_termination_class_as_shipped():
    """RESTORE THE DEFECT, VERBATIM: the classifier exactly as it ran in
    the full gate on save 245daa7f719ab69d.

    Three behaviours come back together because they were one function:
    any `contested_by` is a contest INCLUDING the actor's own write, 1B
    is returned without ever asking whether the writer permits the cut,
    and there is no class at all for a body standing in the carriageway.
    That is the state that produced 6 self-contested 1B rows, 0
    1A_FORBIDDEN out of 106 refusals, and 84 NO_CLASS.
    """
    def shipped(nums: dict) -> str:
        if (nums.get("repairable_by_terrain_write") is False
                and nums.get("ground_owned_by")):
            return "1A_FORBIDDEN"
        if nums.get("repairable_by_terrain_write") is False:
            return ("1C_UNSERVED"
                    if nums.get("repair_footprint_m") is not None
                    else "UNKNOWN")
        if nums.get("contested_by"):
            return "1B_CONTESTED"
        if (nums.get("repairable_by_terrain_write") is None
                or nums.get("contested_within_m") is None):
            return "UNKNOWN"
        return "ACTIONABLE"
    return _swap("termination_class", shipped)


def _neuter_occupancy_test():
    """Stop asking whether a walker's capsule fits above a candidate top,
    so a `stone_wall_2x1` cap with another wall standing on it is offered
    as a destination again."""
    return _swap("_blocks_above", lambda *_a, **_k: False)


def _neuter_bounds_only_exclusion():
    """Let a prefab known only to bounding-box precision choose a road's
    destination again - the TreasureChest_trollcave at y 5066.017."""
    real = _nearest_standing
    return _swap("_nearest_standing",
                 lambda x, z, g, bp, wh=0.0, _bo=frozenset():
                 real(x, z, g, bp, wh, frozenset()))


def _neuter_junction_tier():
    """Put the junction tier back BELOW the nearest-standing search, the
    order that judged 6 junction termini as settlement arrivals."""
    real = resolve_destination

    def last_resort(name, end, x, z, pads, terminus_at, grid, build_pieces,
                    walker_h, bounds_only=frozenset(), other_end=None,
                    segs=None):
        tier, payload = real(name, end, x, z, pads, terminus_at, grid,
                             build_pieces, walker_h, bounds_only, other_end,
                             segs)
        if tier != "junction":
            return tier, payload
        std = _nearest_standing(x, z, grid, build_pieces, walker_h,
                                bounds_only)
        return ("standing", std) if std is not None else (tier, payload)
    return _swap("resolve_destination", last_resort)


def _neuter_pad_tier_precedence():
    """Promote the junction tier ABOVE the declared pad, which is the
    over-correction the pad tier exists to prevent: it would defer T8 B
    and T9 A's real 0.842 m miss on wt-town to junction.py, where the two
    roads agree with each other exactly and the town is not measured."""
    real = resolve_destination

    def junction_first(name, end, x, z, pads, terminus_at, grid,
                       build_pieces, walker_h, bounds_only=frozenset(),
                       other_end=None, segs=None):
        met = sorted(f"{n2} {e2}" for (n2, e2), (x2, z2)
                     in terminus_at.items()
                     if (n2, e2) != (name, end)
                     and math.hypot(x2 - x, z2 - z) <= JUNCTION_TOL_M)
        if met:
            return "junction", met
        return real(name, end, x, z, pads, terminus_at, grid, build_pieces,
                    walker_h, bounds_only, other_end, segs)
    return _swap("resolve_destination", junction_first)


def _neuter_sibling_end_exclusion():
    """Hand one pad to BOTH ends of a segment again, which gave S1's B end
    portal-hall 17.3 m outside its rectangle."""
    real = resolve_destination
    return _swap("resolve_destination",
                 lambda n, e, x, z, p, ta, g, bp, wh, bo=frozenset(),
                 _oe=None, sg=None:
                 real(n, e, x, z, p, ta, g, bp, wh, bo, None, sg))


def _neuter_centreline_tier():
    """Compare only other segments' TERMINI again, so a T-junction onto
    the middle of another road is invisible."""
    real = resolve_destination
    return _swap("resolve_destination",
                 lambda n, e, x, z, p, ta, g, bp, wh, bo=frozenset(),
                 oe=None, _sg=None:
                 real(n, e, x, z, p, ta, g, bp, wh, bo, oe, None))


def _neuter_extremity_tier():
    """Recognise no extremity, so a road to a cape is judged against the
    nearest thing standing again."""
    return _swap("extremity_end", lambda *_a, **_k: None)


def _neuter_planned_end(arm: str):
    """PLANNED_END_DEPENDS_ON with one arm not read: its terminus is
    "meets NOTHING", UNMEASURED and ownerless, again."""
    def neuter():
        return _swap("_PLANNED_END_READS", _PLANNED_END_READS - {arm})
    return neuter


def _neuter_footprint_after_snap():
    """Select the first inside sample by ARC LENGTH alone again, so the
    snap can push it back outside the destination it is probing."""
    real = probe_walk
    return _swap("probe_walk",
                 lambda x, z, ux, uz, n, gap, _contains, snap:
                 real(x, z, ux, uz, n, gap, lambda *_a: True, snap))


def _neuter_packed_wire_index():
    """Read only the FIRST command on each line again."""
    real = spawns_on
    return _swap("spawns_on", lambda line: real(line)[:1])


def _neuter_placement_accounting():
    """Read wire AND blob and union them - the shape that made 159 of 377
    records read OVER their own command_count."""
    real = record_placements
    return _swap("record_placements",
                 lambda r, blobs: real(r, blobs) + [
                     t for line in (r.get("wire") or [])
                     for t in spawns_on(str(line))])


def _neuter_anchored_ownership():
    """Ask ownership piece by piece again, against an index that resolves
    13,138 of 124,980 build-piece ZDOs."""
    return _swap("anchored_prefabs_at", lambda *_a, **_k: set())


def _neuter_ownership_into_an_amnesty():
    """Let an anchor clear every prefab in the rectangle rather than the
    ones ITS OWN PLAN declared."""
    return _swap("anchored_prefabs_at",
                 lambda pad, anchors, m: {"goblin_bed", "wood_roof_45",
                                          "wood_wall_half", "wood_floor"})


def _neuter_pad_readings():
    """Drop the pad's other readings, so every unstood declaration is
    blamed on the declaration instead of on the pad."""
    real = check_arrival
    return _swap("check_arrival",
                 lambda term, profile, tol, step_limit:
                 real(dict(term, pad_readings=()), profile, tol, step_limit))


# ---------------------------------------------------------------------------
# THE BACKLOG, DRIVEN TO N OF N.
#
# `Case.neuter` is the only operation in this gate that detects THE defect
# class of this whole session: an instrument that answers IDENTICALLY under
# a hypothesis and its negation.  `moved 0`, `missing []`, `population 0`,
# `declared: 2`, a reason string standing in for a re-run - every one of
# them scored the same whether the world was broken or fine.  A case
# without a neuter has never been shown to be ABLE to fail, and 17 of this
# section's 34 were in that state.
#
# TWO KINDS OF NEUTER APPEAR BELOW AND THEY ARE LABELLED:
#   RESTORES A DEFECT   a state this world was measurably in.  The case's
#                       own assertion fires and names the live numbers.
#   TIGHTENS THE        for an ACCEPT case with no historical defect to
#   DISCRIMINATOR       restore, the neuter makes the check FIRE on the
#                       case's own fixture.  That proves the case is
#                       reading a verdict rather than returning `[]` by
#                       construction - "an accept case that cannot fail is
#                       the same defect as a refuse case that cannot fire".
# ---------------------------------------------------------------------------
def _neuter_empty_population_guard():
    """RESTORES A DEFECT.  Take the empty-segment refusal back out, which
    is the state this section shipped in for about ten minutes: the road
    loader was asked of the wrong module, returned {}, and the section
    reported PASS over ZERO termini on a world with 25 built roads."""
    real = check_population
    return _swap("check_population",
                 lambda segs, *a, **k:
                 [f for f in real(segs, *a, **k) if f.ok])


def _neuter_population_guard_always_fires():
    """TIGHTENS THE DISCRIMINATOR.  Make the empty-population refusal fire
    over a population that is NOT empty, so a case asserting "a present
    population is accepted" has to come out the other way."""
    real = check_population
    return _swap("check_population",
                 lambda segs, *a, **k: real({}, *a, **k))


def _neuter_no_declared_datum_guard():
    """RESTORES A DEFECT.  Drop the "not one terminus meets a pad that
    declares a datum" refusal, so 25 roads meeting nothing measurable
    reads as an accepted world."""
    real = check_population
    return _swap("check_population",
                 lambda segs, termini, *a, **k:
                 [f for f in real(segs, termini, *a, **k)
                  if not (not f.ok
                          and (f.numbers or {}).get("termini_judged") == 0)])


def _neuter_unmeasured_is_dropped():
    """RESTORES A DEFECT.  Stop reporting the isolated termini at all,
    which is the state where R3 A and T5 B - the two nobody can answer
    for - vanish into the 19 that were all called "arrives nowhere"."""
    real = check_population
    return _swap("check_population",
                 lambda segs, termini, pads, tol, walker_r, ledger="",
                 unmeasured=None, deferred=None:
                 real(segs, termini, pads, tol, walker_r, ledger, None,
                      deferred))


def _neuter_envelope_is_believed():
    """RESTORES A DEFECT.  Judge an unreachable candidate as an arrival,
    which is how N2 B came back with an arrival error of -5008.034 m
    against a trollcave chest Valheim parks 5 km up."""
    real = check_arrival

    def believe(term, profile, tol, step_limit):
        t = dict(term, gap_m=0.0)
        t["nums"] = dict(term["nums"], destination_kind="declared")
        return real(t, profile, tol, step_limit)
    return _swap("check_arrival", believe)


def _neuter_envelope_to_nothing():
    """TIGHTENS THE DISCRIMINATOR.  Shrink the walker's reach envelope to
    zero so EVERY standing destination is "unreachable", and a case that
    asserts a reachable deck is MEASURED must come out the other way."""
    real = check_arrival
    up = globals()["REACH_UP_M"]
    globals()["REACH_UP_M"] = 0.0
    undo = _swap("check_arrival",
                 lambda term, profile, tol, step_limit:
                 real(term, profile, tol, 0.0))

    def both():
        globals()["REACH_UP_M"] = up
        undo()
    return both


def _neuter_tolerance_to_nothing():
    """TIGHTENS THE DISCRIMINATOR.  A negative tolerance makes the datum
    comparison fire on an EXACT arrival, so a case asserting that a road
    ON its datum is accepted has to come out the other way.  Without this
    such a case returns `[]` whether the check works or not."""
    real = check_arrival
    return _swap("check_arrival",
                 lambda term, profile, tol, step_limit:
                 real(term, profile, -1.0, step_limit))


def _neuter_berm_reads_only_its_endpoints():
    """RESTORES A DEFECT, and it is the one the berm check exists for:
    read the two ENDS of the approach and nothing between them.  R2's
    last station and pad 12 both read 38.219 with a 1.23 m berm standing
    between them."""
    real = check_arrival
    return _swap("check_arrival",
                 lambda term, profile, tol, step_limit:
                 real(term, ([profile[0], profile[-1]] if profile
                             else profile), tol, step_limit))


def _neuter_declaration_is_believed():
    """RESTORES A DEFECT.  Drop the declaration before it can be judged,
    which is the state in which sites.yaml's Vestvik pad_y of 33.99 - the
    DISTRICT figure, 1.30 m below the ground the village stands on - was
    believed, and a road built to it was a perfect arrival."""
    real = check_arrival
    return _swap("check_arrival",
                 lambda term, profile, tol, step_limit:
                 real(dict(term, declared_datum=None), profile, tol,
                      step_limit))


def _neuter_datum_comes_from_the_plan():
    """RESTORES A DEFECT.  Answer the datum question with the PLAN again -
    `settlements/sites.yaml`'s 33.99 - instead of reading the destination's
    own standing geometry."""
    return _swap("standing_datum",
                 lambda *_a, **_k: (33.99, "sites.yaml pad_y", (0.0, 0.0)))


def _neuter_step_judged_on_the_diagonal():
    """RESTORES A DEFECT.  Judge every transition at the DIAGONAL
    allowance, 1.414 * 0.7813 = 1.1049 m, instead of per 1 m mesh edge.
    That is what cleared a 6.349 m wall on N1 and it clears a 1.000 m
    riser too.

    THE BERM ROW IS DROPPED WITH IT, and that is not a second defect: a
    riser standing between the road and its destination IS a berm AND is
    a step, so both checks fire on the same fixture.  Leaving the berm in
    would make the verdict refuse whatever the step allowance is, and the
    case would be unfalsifiable for a reason that has nothing to do with
    the allowance it exists to hold in place."""
    real = check_arrival
    return _swap("check_arrival",
                 lambda term, profile, tol, step_limit:
                 [f for f in real(term, profile, tol,
                                  step_limit * math.sqrt(2.0))
                  if f.check != "terminus_berm"])


def _neuter_approach_is_not_walked():
    """RESTORES A DEFECT, and it is this section's own earliest one: judge
    the road's last station against the destination's datum and NEVER LOOK
    AT THE GROUND BETWEEN THEM.  Both endpoints of R2's approach read
    38.219 - a perfect arrival by that rule - with a 1.181 m riser
    standing in the middle of it.

    This is the neuter for the STEP case rather than the diagonal one
    because that fixture's riser is 1.181 m, which is over the 1.1049 m
    diagonal allowance too: widening the allowance cannot clear it, so
    widening the allowance cannot prove the case either.  What the case
    holds in place is that the approach is WALKED AT ALL.
    """
    real = check_arrival
    return _swap("check_arrival",
                 lambda term, profile, tol, step_limit:
                 [f for f in real(term, profile, tol, step_limit)
                  if f.check in ("terminus_datum",
                                 "destination_datum_unstood",
                                 "terminus_unmeasured")])


def _neuter_unread_station_is_filled_in():
    """RESTORES A DEFECT.  Substitute the next readable station for a
    station that was never read, so an UNMEASURED arrival reads as a
    clean one.  "I could not measure it" is not "it is fine"."""
    real = check_arrival

    def fill(term, profile, tol, step_limit):
        got = [y for y in (profile or []) if y is not None]
        return real(term, ([got[0]] + list(profile[1:]) if got and profile
                           else profile), tol, step_limit)
    return _swap("check_arrival", fill)


def _neuter_generated_content_is_invisible():
    """RESTORES A DEFECT.  See nothing generated inside any pad, so a seat
    proposed on top of Valheim's own village at (1218, 512) reads clean."""
    return _swap("generated_inside", lambda *_a, **_k: [])


def _neuter_every_piece_is_generated():
    """TIGHTENS THE DISCRIMINATOR.  Call EVERY piece in the rectangle
    generated content, so a case asserting that OUR OWN body is not
    generated - whether it sits inside a marker's radius or outside every
    marker - has to come out the other way."""
    return _swap(
        "generated_inside",
        lambda rect, pieces, markers, is_declared:
        [(p, (p[1], p[2], 0.0, "forced")) for p in pieces])


# ---------------------------------------------------------------------------
# THE KEEP-OUT'S KIND.  `KEEP_OUT_DEPENDS_ON` in `ledger/writer.py` names the
# terms; these three cases take one term away IN EACH DIRECTION and require
# the answer to move, with the term the neuter CANNOT patch standing beside
# it as the denominator.
#
# THE MEASURED STATE THEY ENCODE, Ulfsland save bc484171e3039bc0, gate C:
#
#   4 x 1A_FORBIDDEN  road_lighting seq 2784      N4-mid2-terminus stations
#                     2962/2964/2965/2967, rises 2.106 / 2.416 / 2.538 /
#                     3.041 m against a 0.7813 m slide limit, owner
#                     distances 1.087 / 0.414 / 1.078 / 1.155 m, repair
#                     cones 3.125 / 3.521 / 3.677 / 4.321 m
#   3 x 1A_FORBIDDEN  bridge_body_place 2809/2871
#   2 x 1A_FORBIDDEN  harbour_place seq 24
#
# ALL FOUR LAMP ROWS ARE ONE TORCH, at (-907.4138, 7713.005), whose own
# footprint is 0.233 x 0.237 m - `fixtures.fixture_box("piece_groundtorch")`,
# half-diagonal 0.166 m.  It named ground out to 4.321 m, a 26x overreach,
# and a 3.041 m step in a carriageway could never be repaired by anything.
# ---------------------------------------------------------------------------

# The two owners, MEASURED, and used by all three cases so that no case can
# pass by disagreeing with another about the world.
_KO_LAMP = (-907.4138, 7713.005, 0.0, "road_lighting", 2784, "RESEAT")
_KO_PILE = (433.0, 3318.0, 0.0, "bridge_body_place", 2809, "FORBIDDEN")
_KO_LAMP_STATION = (-908.0, 7714.0)      # N4-mid2-terminus station 2967
_KO_PILE_STATION = (433.0, 3319.0)       # N1-wnorth-shelf station 1585


def _ko_recast(kind: str):
    """RESTORE A DEFECT AT THE ONE SEAM THE KIND IS READ: rewrite every
    owner's kind to `kind` before `repair_ownership` weighs it.

    ONE SEAM, TWO DIRECTIONS, AND NEITHER IS DEGENERATE.  `"FORBIDDEN"`
    takes the `kind` term out of `KEEP_OUT_DEPENDS_ON` by widening - every
    keep-out freezes its ground, which is the shipped defect and puts the
    lamp's spurious freeze back.  `"RESEAT"` takes it out by narrowing -
    every keep-out is dischargeable, which is the opposite over-correction
    and is how a keep-out that refuses nothing would look.  Each one leaves
    the OTHER kind's own rows untouched: recasting to FORBIDDEN cannot
    change a pile that is already FORBIDDEN, and recasting to RESEAT cannot
    change a lamp that is already RESEAT.  So each case carries a
    denominator its own neuter is unable to patch.
    """
    real = repair_ownership

    def recast(x, z, depth_m, owners, step_limit):
        if owners is not None:
            owners = [tuple(o[:5]) + (kind,) for o in owners]
        return real(x, z, depth_m, owners, step_limit)
    return _swap("repair_ownership", recast)


def _neuter_every_keep_out_freezes_its_ground():
    return _ko_recast("FORBIDDEN")


def _neuter_every_keep_out_is_dischargeable():
    return _ko_recast("RESEAT")


def _ko_ask(station, depth_step, owners):
    """`repair_ownership` + `termination_class` for a step of `depth_step`
    at `station`, asked exactly as every traversability row asks it."""
    inst = A.instruments()
    lim = inst.STEP_LIMIT_PER_M
    got = repair_ownership(station[0], station[1],
                           round(inst.disposal_depth(depth_step, lim), 4),
                           list(owners), lim)
    nums = dict(got, contested_within_m=0.0, segment="fixture")
    return got, termination_class(nums)


def _case_a_lamp_does_not_freeze_the_road_it_stands_beside():
    """A 3.041 m STEP IN A CARRIAGEWAY, HELD BY A 0.166 m TORCH.

    The world state this reproduces is quoted above.  What the row must now
    say is ACTIONABLE WITH A NAMED DEBT: the cut is permitted, and it owes a
    re-seat of `road_lighting seq 2784`.

    AND THE SURVIVOR IS IN THIS CASE, AS A VERDICT AND NEVER AS AN ASSERT.
    `run_cases` reads an `AssertionError` as a flip, so a survivor asserted
    here would stamp a neuter that emptied the instrument PROVEN.  The pile
    row is therefore checked by RETURNING A REFUSAL, which is the same
    currency the lamp row is judged in - and the two arms cannot both be
    satisfied by any single-kind reading of the owner set.
    """
    out: list[Finding] = []
    owners = [_KO_LAMP, _KO_PILE]
    lamp, lamp_cls = _ko_ask(_KO_LAMP_STATION, 3.041, owners)
    if lamp_cls != "ACTIONABLE" or lamp["repairable_by_terrain_write"] is not True:
        out.append(Finding(
            check="keep_out_kind", ok=False,
            message=(f"a 3.041 m step in a carriageway came back "
                     f"{lamp_cls} owned by {lamp['ground_owned_by']!r}: a "
                     f"lamp whose own footprint is 0.233 x 0.237 m is "
                     f"naming ground "
                     f"{lamp['repair_footprint_m']} m away and no tool can "
                     f"ever close this step"),
            numbers=dict(lamp, termination_class=lamp_cls,
                         station=list(_KO_LAMP_STATION))))
    owed = lamp.get("reseats_required") or []
    if [d["seq"] for d in owed] != [2784]:
        out.append(Finding(
            check="keep_out_kind", ok=False,
            message=("the cut is permitted and the debt is NOT NAMED: a "
                     "write that regrades this ground must re-seat "
                     "road_lighting seq 2784, and a permission published "
                     "without it hands the builder a write `Ledger.append` "
                     "refuses at the console"),
            numbers=dict(reseats_required=owed,
                         station=list(_KO_LAMP_STATION))))
    # THE DENOMINATOR.  A pile stands in the bed of a channel; cutting it
    # takes away the water it is driven into and nothing puts water back.
    pile, pile_cls = _ko_ask(_KO_PILE_STATION, 6.349, owners)
    if pile_cls != "1A_FORBIDDEN" or pile["ground_owned_by"] is None:
        out.append(Finding(
            check="keep_out_kind", ok=False,
            message=(f"the bridge pile's keep-out was LOST while the lamp's "
                     f"was corrected: station 1585 came back {pile_cls} "
                     f"owned by {pile['ground_owned_by']!r}. A keep-out "
                     f"that refuses nothing is as broken as one that "
                     f"refuses everything"),
            numbers=dict(pile, termination_class=pile_cls,
                         station=list(_KO_PILE_STATION))))
    return out


def _case_a_bridge_pile_still_freezes_the_bed_it_stands_in():
    """THE KEEP-OUT THAT MUST SURVIVE THE CORRECTION.

    THE STATE THIS CASE IS REQUIRED TO REFUSE is a 6.349 m step whose
    repair cone reaches a bridge pile 1.0 m away - N1-wnorth-shelf station
    1585, MEASURED on save bc484171e3039bc0.  Levelling there removes the
    water the piles are driven into and nothing in this tree puts water
    back, so the row is 1A_FORBIDDEN and the REFUSAL IS THE PRODUCT.

    Asked with the pile as the ONLY owner, so the recast-to-RESEAT neuter
    has nothing else to hide behind: with every keep-out dischargeable this
    row reads repairable and the world is told to cut a bridge's bed out.

    AND THE DENOMINATOR IS SILENT UNLESS IT BREAKS, WHICH IS WHAT MAKES IT
    UNPATCHABLE.  `owners=None` means the set was never read, and
    `UNREAD IS NOT CLEAR`: that row must stay UNKNOWN whatever any kind
    says, because no kind was ever consulted.  The recast neuter cannot
    touch it - it rewrites owner tuples and there are none - so it stays
    quiet and the flip above is driven purely by the pile.  A neuter that
    EMPTIED `repair_ownership` instead would break it, it would speak, the
    case would keep refusing, and `run_cases` would report no flip rather
    than stamping an emptied instrument PROVEN.
    """
    inst = A.instruments()
    out: list[Finding] = []
    pile, cls = _ko_ask(_KO_PILE_STATION, 6.349, [_KO_PILE])
    if cls == "1A_FORBIDDEN":
        out.append(Finding(
            check="keep_out_kind", ok=False,
            message=(f"a repair of the 6.349 m step at station 1585 needs "
                     f"{pile['repair_footprint_m']} m of ground and "
                     f"{pile['ground_owned_by']} stands "
                     f"{pile['ground_owner_distance_m']} m away: levelling "
                     f"there removes the water the piles are driven into, "
                     f"and no tool in this tree puts water back"),
            numbers=dict(pile, termination_class=cls,
                         station=list(_KO_PILE_STATION))))
    unread = repair_ownership(_KO_PILE_STATION[0], _KO_PILE_STATION[1],
                              round(inst.disposal_depth(
                                  6.349, inst.STEP_LIMIT_PER_M), 4),
                              None, inst.STEP_LIMIT_PER_M)
    if (unread["repairable_by_terrain_write"] is not None
            or unread["reseats_required"] is not None
            or termination_class(dict(unread, contested_within_m=0.0,
                                      segment="fixture")) != "UNKNOWN"):
        out.append(Finding(
            check="keep_out_kind", ok=False,
            message=("UNREAD IS NOT CLEAR: with the keep-out set never "
                     "read, this row must be UNKNOWN and owe no named "
                     "debt. An instrument that answers anyway has stopped "
                     "measuring, and a kind it invented is worth less than "
                     "the silence it replaced"),
            numbers=dict(unread)))
    return out


def _neuter_a_forbidden_seq_may_be_laundered():
    """RESTORE THE OWNER EXCEPTION THIS GUARD HAS NEVER HAD: read every
    keep-out as RESEAT inside `writer`, so a `reseats` declaration naming a
    bridge pile is accepted and a write may cut the bed it stands in."""
    writermod = _keep_out_writer()
    real = writermod.keep_out_kind

    def all_reseat(p):
        return None if real(p) is None else "RESEAT"
    writermod.keep_out_kind = all_reseat

    def undo():
        writermod.keep_out_kind = real
    return undo


def _keep_out_writer():
    """`ledger/writer.py`, imported the way this section imports it."""
    A.instruments()
    from ..instruments import JUMPSTART                    # noqa: PLC0415
    import sys                                            # noqa: PLC0415
    p = str(JUMPSTART / "ledger")
    if p not in sys.path:
        sys.path.insert(0, p)
    import writer as writermod                            # noqa: PLC0415
    return writermod


def _case_a_reseat_debt_may_not_launder_a_forbidden_seq():
    """THE DISCHARGE IS THE ONLY WAY PAST A KEEP-OUT, SO IT IS THE ONLY
    THING WORTH FORGING.

    A `terrain_write` that names a `flatten: FORBIDDEN` seq in
    `params.reseats` is claiming it can put a bridge pile's water back.
    `writer._reseat_debt_check` must refuse it - and that refusal is what
    keeps this repair from becoming the owner exception the flatten guard
    has never had.  MEASURED cost of that exception when it existed by
    accident elsewhere: a deck buried its own tiles.
    """
    import tempfile                                        # noqa: PLC0415
    from pathlib import Path as _P                         # noqa: PLC0415
    W = _keep_out_writer()
    out: list[Finding] = []
    pile = {"seq": 2809, "op": "spawn",
            "params": {"role": "bridge_body_place", "site_id": "fixture-pile",
                       "pos": [433.0, 3.0, 3318.0], "yaw_deg": 0.0,
                       "prefab": "wood_pole",
                       "flatten": "FORBIDDEN",
                       "flatten_reason": "piles stand on the generated bed"}}
    lamp = {"seq": 2784, "op": "spawn",
            "params": {"role": "road_lighting", "site_id": "fixture-lamp",
                       "pos": [-907.4138, 3.0, 7713.005], "yaw_deg": 0.0,
                       "prefab": "piece_groundtorch",
                       "flatten": "RESEAT",
                       "flatten_reason": "a lamp's claim is its own seating"}}

    def write(seq_declared, zone):
        return {"seq": 9001, "op": "terrain_write",
                "params": {"name": "fixture", "paint": "paved",
                           "datum": "fixture", "location_check": {},
                           "reseats": [seq_declared],
                           "entries": [{"zone": list(zone),
                                        "centre": [0.0, 0.0],
                                        "blob_sha256": "0" * 64}]}}
    with tempfile.TemporaryDirectory() as td:
        led = W.Ledger(_P(td), "keep-out-case")
        # The pile sits in zone (7, 52); the lamp in (-14, 121).
        forged = led._reseat_debt_check(write(2809, (7, 52)), [pile, lamp])
        honest = led._reseat_debt_check(write(2784, (-14, 121)), [pile, lamp])
        absent = led._reseat_debt_check(write(12345, (7, 52)), [pile, lamp])
    if forged:
        out.append(Finding(
            check="keep_out_kind", ok=False,
            message=("terrain_write declared reseats=[2809], a "
                     "flatten=FORBIDDEN bridge pile, and the guard REFUSED "
                     "it - which is the product. Accepting it would be an "
                     "owner exception in the flatten guard reached through "
                     "the discharge field, and would let a write cut the "
                     "bed a pile stands in: " + forged[0]),
            numbers=dict(refusals=len(forged), declared=2809)))
    # TWO DENOMINATORS, BOTH SILENT UNLESS BROKEN, AND NEITHER PATCHABLE BY
    # THIS CASE'S NEUTER.  Flattening every kind to RESEAT cannot make a seq
    # that is not in the chain exist, and cannot make an honest lamp
    # discharge stop working.  A neuter that EMPTIED `_reseat_debt_check`
    # would break the first of them, it would speak, the case would keep
    # refusing, and no flip would be reported - which is the whole reason
    # the survivors are verdicts rather than asserts.
    if not absent:
        out.append(Finding(
            check="keep_out_kind", ok=False,
            message=("terrain_write declared reseats=[12345] and this "
                     "chain has no record at that seq, and the guard "
                     "ACCEPTED it. A discharge is the only thing that gets "
                     "a write past a keep-out; one that names nothing must "
                     "never be one"),
            numbers=dict(refusals=0, declared=12345)))
    if honest:
        out.append(Finding(
            check="keep_out_kind", ok=False,
            message=("an HONEST discharge - reseats=[2784], a live "
                     "flatten=RESEAT lamp whose site this write reaches - "
                     "was refused, so the only way past a lamp's keep-out "
                     "is closed and the 3.041 m step is unrepairable "
                     "again"),
            numbers=dict(refusals=honest, declared=2784)))
    return out


# -- DRIVE 2: an unstood destination, a trigger volume, the reading quantum --
# THE HUB AS DRIVE 2'S FROZEN SAVE HOLDS IT (a521b70356e7), census rows
# verbatim: the four probe vertices S7 A and T6 A took inside
# complete-station-hub, all read at 77.650 (its declared datum), and the
# pieces standing over them.  The artisan station is on the floor above,
# 8.6 m up and 12 m away - it is on this list only because its 200 m capsule
# reaches the pad.
_HUB_INSIDE = [(104.0, 351.0), (104.0, 352.0), (104.0, 353.0), (104.0, 354.0)]
_HUB_BED = 77.65
_HUB_PIECES = (
    ("piece_artisanstation", (106.7173, 86.2219, 365.4837),
     (0.0, 1.0, 0.0, 0.0)),
    ("piece_grausten_stonestair", (103.5308, 76.65, 351.5),
     (0.0, 1.0, 0.0, 0.0)),
    ("Piece_grausten_floor_2x2", (103.5308, 77.4, 353.5),
     (0.0, 0.707107, 0.0, -0.707107)))
_HUB_TERM = dict(segment="road_S7_stathub_wtnorth", end="A", x=103.9, z=341.2,
                 gap_m=9.3, dest="pad 'complete-station-hub'",
                 declared_datum=77.65,
                 heights_by_vertex={v: _HUB_BED for v in _HUB_INSIDE},
                 nums=dict(segment="road_S7_stathub_wtnorth",
                           destination="complete-station-hub", pad_seq=563,
                           destination_kind="declared"))


def _case_a_trigger_volume_is_not_a_body_on_the_pad():
    """THE CORPUS THE GATE READS, on the pad it read wrong.

    Built from `instruments.load_geometry()` - the gate's own reader - so
    the case is about the corpus and not about a fixture of it.  With the
    40 x 200 x 40 m capsule in, every one of the four vertices is vetoed and
    the destination has no datum: the drive-2 state.  With it out, the pad
    is read at 77.650."""
    inst = A.instruments()
    geom, _bp = inst.load_geometry()
    grid = inst.Grid([inst.Solid(n, p, q, geom.get(n) or [], n in geom)
                      for n, p, q in _HUB_PIECES])
    why: dict = {}
    y, what, at = standing_datum(_HUB_INSIDE, _HUB_TERM["heights_by_vertex"],
                                 grid, 1.85, frozenset(), geom, why=why)
    if y is None:
        return [unstood_destination(_HUB_TERM, why)]
    return [Finding(check="standing_datum", ok=True,
                    message=(f"complete-station-hub read at {y:.3f} m "
                             f"({what}) at {at}, the capsule not a body"),
                    numbers=dict(datum_m=round(y, 3)))]


def _case_an_unstood_destination_carries_its_class():
    """THE THREE STATES `terminus_unmeasured` USED TO SHARE ONE SENTENCE AND
    ONE NON-CLASS FOR.  Drive 2 published the first of them as "was not
    read", UNKNOWN; each must come back with the class its own numbers
    earn, through `termination.backstop` - the path the board takes."""
    from .. import termination as T                         # noqa: PLC0415
    art = {1: "piece_artisanstation"}
    states = (
        ("read and occupied", dict(
            inside=4, unread=[], blocking_bodies=art,
            blocked=[(v, ["piece_artisanstation"]) for v in _HUB_INSIDE]),
         "1D_OBSTRUCTED"),
        ("partly never read", dict(
            inside=4, unread=_HUB_INSIDE[2:], blocking_bodies=art,
            blocked=[(v, ["piece_artisanstation"])
                     for v in _HUB_INSIDE[:2]]),
         "1E_OUT_OF_ORDER"),
        ("no vertex inside", dict(inside=0, unread=[], blocked=[],
                                  blocking_bodies={}),
         "1C_UNSERVED"))
    out = []
    for name, why, want in states:
        f = unstood_destination(_HUB_TERM, why)
        T.backstop("settlements", [f])
        got = (f.numbers or {}).get("termination_class")
        if got != want:
            out.append(Finding(
                check="termination", ok=False,
                message=(f"an unstood destination, {name}, came back "
                         f"{f.check} {got!r}; its numbers earn {want}, and "
                         f"UNKNOWN is NO_CLASS spelled again"),
                numbers=dict(state=name, got=got, want=want)))
    return out


# T3 B's approach, drive 2, as the ground instrument served it - FOUR
# decimals, WaterProof's "0.####".  The unrounded lattice under the last
# edge is 33.360073 -> 34.141357 = 0.78128433, UNDER tan(38 deg).
_QUANTUM_VERTS = [(459.0, -107.0), (460.0, -107.0), (461.0, -107.0)]


def _quantum_term(last: float):
    hbv = dict(zip(_QUANTUM_VERTS, (33.2819, 33.3601, last)))
    t = dict(segment="road_T3_temple_meadhall", end="B", x=459.0, z=-107.0,
             datum=last, gap_m=3.0, dest="pad 'meadows-starter-hall'",
             samples=3, vertices=list(_QUANTUM_VERTS), heights_by_vertex=hbv,
             nums=dict(segment="road_T3_temple_meadhall",
                       destination="meadows-starter-hall",
                       destination_kind="declared"))
    return t, [hbv[v] for v in _QUANTUM_VERTS]


def _case_a_step_inside_the_reading_quantum_is_not_refused():
    """0.7813 read, 0.78128433 unrounded, 0.78128563 the limit: the
    refusal drive 2 published twice was the fourth decimal of two readings.
    The transition is COUNTED on the term, not refused and not dropped."""
    t, prof = _quantum_term(34.1414)
    fs = check_arrival(t, prof, 0.245, A.instruments().STEP_LIMIT_PER_M)
    if not t.get("step_within_resolution") and not fs:
        fs.append(Finding(
            check="terminus_step", ok=False,
            message=("a 0.7813 m transition read within one quantum of the "
                     "limit was neither refused nor COUNTED - absorbed into "
                     "a pass"),
            measured=0.7813, numbers=dict(within_resolution=0)))
    return fs


def _case_a_step_past_the_reading_quantum_is_still_refused():
    """THE PARTNER: the same edge read at 0.7815, 2.1e-4 over the limit and
    so over it by more than any rounding of two readings can explain."""
    t, prof = _quantum_term(34.1416)
    return check_arrival(t, prof, 0.245, A.instruments().STEP_LIMIT_PER_M)


def _neuter_the_corpus_keeps_trigger_volumes():
    """RESTORE THE DEFECT: the gate's corpus with every non-trigger
    collider kept, piece_artisanstation's 200 m capsule included."""
    inst = A.instruments()
    was = inst._CORPUS_KEEPS_TRIGGER_VOLUMES
    inst._CORPUS_KEEPS_TRIGGER_VOLUMES = True

    def undo():
        inst._CORPUS_KEEPS_TRIGGER_VOLUMES = was
    return undo


def _neuter_the_unstood_row_as_shipped():
    """RESTORE THE DEFECT VERBATIM: one sentence and UNKNOWN for all three
    states of an unstood destination.  Built HERE, outside the section's own
    call graph, so the coverage audit reads the source that ships and not a
    neuter's copy of the defect."""
    def shipped(t, _why):
        nums = dict(declared_datum=t.get("declared_datum"),
                    probed_m=INSIDE_PROBE_M, **t["nums"])
        nums["termination_class"] = "UNKNOWN"
        return Finding(
            check="terminus_unmeasured", ok=False,
            message=("{} terminus {} at ({:.1f}, {:.1f}): {} is within reach "
                     "and DECLARES {:.3f} m, but the applied surface "
                     "{:.1f}-{:.1f} m in from its own edge was not read, so "
                     "nothing standing there could be measured. A "
                     "declaration is not a datum.".format(
                         t["segment"], t["end"], t["x"], t["z"], t["dest"],
                         t.get("declared_datum") or 0.0, t["gap_m"],
                         t["gap_m"] + INSIDE_PROBE_M)),
            where=(t["x"], t["z"]), measured=0.0, limit=1.0, numbers=nums)
    return _swap("unstood_destination", shipped)


def _neuter_the_step_judged_finer_than_the_reading():
    """RESTORE THE DEFECT: the limit compared to more decimals than the
    reading has."""
    inst = A.instruments()
    was = inst._STEP_JUDGED_FINER_THAN_THE_READING
    inst._STEP_JUDGED_FINER_THAN_THE_READING = True

    def undo():
        inst._STEP_JUDGED_FINER_THAN_THE_READING = was
    return undo


def _neuter_every_step_is_inside_the_quantum():
    """THE OVER-CORRECTION: a resolution band so wide nothing is ever over
    the limit."""
    inst = A.instruments()
    real = inst.exceeds_step_limit
    inst.exceeds_step_limit = lambda *_a, **_k: False

    def undo():
        inst.exceeds_step_limit = real
    return undo



CASES = [
    Case("empty_population_is_refused", True,
         _case_empty_population_is_refused,
         neuter=_neuter_empty_population_guard,
         neuters="the empty-segment refusal, taken back out",
         prior_state="this section itself, before the road loader was "
                     "pointed at the roads section: PASS over zero termini"),
    Case("population_present_is_accepted", False,
         _case_population_present_is_accepted,
         neuter=_neuter_population_guard_always_fires,
         neuters="the empty-population refusal, fired over a population "
                 "that is NOT empty"),
    Case("roads_meet_no_declared_datum", True,
         _case_roads_meet_no_declared_datum,
         neuter=_neuter_no_declared_datum_guard,
         neuters="the no-declared-datum refusal, dropped"),
    Case("unreachable_candidate_is_unmeasured", True,
         _case_unreachable_candidate_is_unmeasured,
         neuter=_neuter_envelope_is_believed,
         neuters="the walker-reach envelope, so an unreachable candidate "
                 "is judged as an arrival",
         prior_state="live on Ulfsland: the standing fallback offered N2 a "
                     "trollcave chest at y 5066.017 and the first version "
                     "of this check reported a -5008.034 m arrival error"),
    Case("reachable_standing_top_is_judged", False,
         _case_reachable_standing_top_is_judged,
         neuter=_neuter_envelope_to_nothing,
         neuters="the walker-reach envelope, shrunk to zero so every standing destination is unreachable",
         prior_state="live on Ulfsland: R4 A's terminus is 0.920 m above "
                     "the harbour deck it meets over a 15.0 m gap. The "
                     "case asserts what it discriminates - REACHABLE from "
                     "UNMEASURED - and since the join/approach ruling a "
                     "15.0 m gap is an APPROACH, so the verdict is the "
                     "berm and step checks' to give and this case must "
                     "not pin a second check's population"),
    Case("arrival_on_the_datum", False, _case_arrival_on_the_datum,
         neuter=_neuter_tolerance_to_nothing,
         neuters="the arrival tolerance, made negative so an EXACT "
                 "arrival must refuse",
         prior_state="R2 after the 13th terrace: arrival_error_m 0.0"),
    Case("arrival_berm", True, _case_arrival_berm,
         neuter=_neuter_berm_reads_only_its_endpoints,
         neuters="the ground BETWEEN the endpoints, dropped",
         prior_state="R2's last station 4.0 m outside pad 12 with a 1.23 m "
                     "berm between - and both endpoints reading 38.219"),
    Case("a_berm_repair_sized_off_the_worst_leaves_the_next_one", True,
         _case_a_berm_repair_sized_off_the_worst_leaves_the_next_one,
         neuter=_neuter_berm_worst_without_its_set,
         neuters="terminus_berm and terminus_step as they stood all "
                 "session: a worst, a count, and NO SET",
         prior_state="MEASURED 2026-09-22 on save 2a6940a934b5 and "
                     "reproduced on 9c365043c0f8: terminus_berm published "
                     "berm_m 0.643 at_m_along 6 for R3 B and 0.459 at 4 for "
                     "R4 A, and terminus_step published step_m 1.148 at "
                     "(-905,1892)->(-904,1892) and 0.880 at "
                     "(-905,1895)->(-904,1894) -- four findings, four "
                     "numbers, no set. The two step edges they name are the "
                     "4th and 14th worst of 22 over-limit eight-connected "
                     "lattice edges among 586 judged over the 167 vertices "
                     "inside the two 6 m travelled ways, and 49 of those "
                     "167 stand above the berm cap. A repair authorised by "
                     "those findings takes 2 of 22 and 0 of 49, moves "
                     "ground, closes nothing, and the gate then reports the "
                     "same refusal at the next worst sample's coordinates. "
                     "The same defect was found the same evening in "
                     "harbours (channel_severed: 36 counted, 1 named) and "
                     "named in roads."),
    Case("declared_datum_nobody_stands_on", True,
         _case_declared_datum_nobody_stands_on,
         neuter=_neuter_declaration_is_believed,
         neuters="the declaration, believed instead of judged",
         prior_state="settlements/sites.yaml declares Vestvik's pad_y as "
                     "33.99, the DISTRICT figure; the village stands on "
                     "35.29 (applied ground under the u-vestvik portal, "
                     "generated 36.6006) and R3 was routed to 35.29. "
                     "Eleven of this world's fifty termini were judged "
                     "against a declaration instead of the ground"),
    Case("declaration_that_agrees_is_accepted", False,
         _case_declaration_that_agrees_is_accepted,
         neuter=_neuter_tolerance_to_nothing,
         neuters="the arrival tolerance, made negative so a declaration "
                 "that AGREES with the ground must refuse"),
    Case("standing_datum_is_the_reading_not_the_plan", False,
         _case_standing_datum_is_the_reading_not_the_plan,
         neuter=_neuter_datum_comes_from_the_plan,
         neuters="the datum, answered from sites.yaml's plan again"),
    Case("arrival_step_over_slide_limit", True, _case_arrival_step,
         neuter=_neuter_approach_is_not_walked,
         neuters="the approach, not walked at all - judge only the road's "
                 "last station against the datum",
         prior_state="R2's approach reads 38.219 at BOTH ENDS with a "
                     "1.181 m riser standing in the middle of it"),
    Case("diagonal_pair_uses_its_own_allowance", True,
         _case_diagonal_pair_uses_its_own_allowance,
         neuter=_neuter_step_judged_on_the_diagonal,
         neuters="the per-1 m-edge allowance, back to the 1.414 m diagonal",
         prior_state="MEASURED at population scale: 18,609 of 18,610 vertex "
                     "groups hold identical heights, 13.11 % of adjacent "
                     "road samples share a vertex and 34.42 % are 1.414 m "
                     "apart while judged against a 1 m allowance"),
    Case("arrival_unread_is_refused", True, _case_arrival_unread,
         neuter=_neuter_unread_station_is_filled_in,
         neuters="an unread station, filled in from the next readable one"),
    Case("road_junction_is_deferred_isolated_is_refused", True,
         _case_road_junction_is_deferred_isolated_is_refused,
         neuter=_neuter_unmeasured_is_dropped,
         neuters="the isolated termini, dropped instead of named",
         prior_state="19 termini reported as arriving nowhere, 17 of them "
                     "ROAD/ROAD junctions whose nearest build piece was a "
                     "piece_groundtorch and whose nearest declared pad was "
                     "135 m to 3,475 m away"),
    Case("seat_on_a_generated_village", True,
         _case_seat_on_a_generated_village,
         neuter=_neuter_generated_content_is_invisible,
         neuters="generated content inside a pad, made invisible",
         prior_state="two village seats proposed on the generated village "
                     "at (1218, 512) and correctly refused"),
    Case("our_body_inside_a_marker_radius", False,
         _case_our_own_body_is_not_generated,
         neuter=_neuter_every_piece_is_generated,
         neuters="ownership, so every piece in the rectangle is called generated",
         prior_state="wt-north: 8 declared pieces inside an unidentified "
                     "marker's worst-case radius"),
    Case("our_body_outside_every_marker", False,
         _case_our_own_body_outside_any_marker,
         neuter=_neuter_every_piece_is_generated,
         neuters="ownership, so every piece in the rectangle is called generated",
         prior_state="sudrberg-keep: 3,576 of our own pieces, no marker "
                     "within reach"),
    Case("a_packed_wire_declares_every_position", False,
         _case_a_packed_wire_declares_every_position,
         neuter=_neuter_packed_wire_index,
         neuters="the declaration index reading only the first command "
                 "on each line",
         prior_state="226 of 421 spawn_plan records pack many commands "
                     "onto one line; the index resolved 13,138 of the "
                     "24,351 placements it can, and five sudrvik pads "
                     "were refused as seated on generated content on one "
                     "undeclared fb_* house each, all placed by our own "
                     "record seq 3079"),
    Case("a_short_declaration_index_is_loud_not_small", False,
         _case_a_short_declaration_index_is_loud_not_small,
         neuter=_neuter_placement_accounting,
         neuters="the wire unioned with the blob, double-counting every "
                 "command the wire repeats",
         prior_state="the index had no accounting identity at all, so a "
                     "blob that read short was indistinguishable from a "
                     "small one - and 3,417 pieces of our own buildings "
                     "plus five sudrvik pads were refused on a partial "
                     "read"),
    Case("our_anchored_body_is_not_generated", False,
         _case_our_own_anchored_body_is_not_generated,
         neuter=_neuter_anchored_ownership,
         neuters="anchored-plan ownership, back to the per-piece index",
         prior_state="live on save 2c7011d2: FOUR of our own buildings - "
                     "portal-hall 2,249 pieces, lh-west 798, "
                     "stenvik-longhouse-1 206, stenvik-longhouse-2 164 - "
                     "refused as content the world generated, because "
                     "declared_placements resolves 13,138 of 124,980 "
                     "build-piece ZDOs (10.5 %) and their own spawn_plan "
                     "was retired off the live chain"),
    Case("an_anchor_does_not_launder_a_village", True,
         _case_an_anchor_does_not_launder_a_village,
         neuter=_neuter_ownership_into_an_amnesty,
         neuters="ownership as a blanket amnesty over the rectangle",
         prior_state="the reverse of the same rule: an anchor clears the "
                     "prefabs IT declared and nothing else"),
    Case("a_junction_outranks_the_nearest_standing_top", False,
         _case_a_junction_outranks_the_nearest_standing_top,
         neuter=_neuter_junction_tier,
         neuters="the junction tier demoted below the nearest-standing search",
         prior_state="N0b B / N1 A share a terminus at (-154, 2506) and "
                     "were each refused by -1.780 m against a wood_floor "
                     "16.5 m away, because the junction test was the LAST "
                     "resort instead of the second tier"),
    Case("a_declared_pad_outranks_the_junction", False,
         _case_a_declared_pad_outranks_the_junction,
         neuter=_neuter_pad_tier_precedence,
         neuters="the pad tier's precedence over a measured coincidence",
         prior_state="T8 B / T9 A share a terminus too and miss wt-town by "
                     "0.842 m; deferring that to junction.py would lose it, "
                     "because the two roads agree with each other exactly"),
    Case("a_bounding_box_is_not_a_destination", False,
         _case_a_bounding_box_is_not_a_destination,
         neuter=_neuter_bounds_only_exclusion,
         neuters="the bounds-only exclusion in the chooser",
         prior_state="N2 B / N3 A were given a TreasureChest_trollcave at "
                     "y 5066.017, a prefab whose whole extent is "
                     "MeshCollider bounds"),
    # NEUTERS READY BUT NOT WIRED - see `_neuter_reach_from_own_footing`.
    # MEASURED with one of them wired, the harness reports
    #   FAIL a_crown_out_of_reach_is_not_a_destination  case raised
    #   AssertionError: the arch's crown is 3.294 m above its own sill and
    #   is not a place a road arrives; got (0.0, -846.0, 7730.5, 36.843,
    #   'portal_wood')
    # which is the case PROVING it is load-bearing, counted as a broken
    # case.  The neuter pass asks "did it come out the OTHER way" by
    # comparing verdicts, but a case that asserts its own fixture
    # invariants expresses "the other way" as a RAISE, and that raise
    # reaches the outer handler, which cannot tell a case that proved
    # itself from one that is broken.  All seven of this section's
    # neuters prove themselves that way, so wiring them today would turn
    # seven green cases red.  Requested of the registry's owner: in the
    # NEUTER PASS ONLY, a raise IS the other way.
    Case("a_crown_out_of_reach_is_not_a_destination", False,
         _case_a_crown_out_of_reach_is_not_a_destination,
         neuter=_neuter_reach_from_own_footing,
         neuters="the reach-from-own-footing filter in the chooser",
         prior_state="N4 B was refused by 3.287 m against portal_wood's "
                     "crown while the wood_floor 3.52 m away matched its "
                     "carriageway to 0.000 m"),
    Case("a_face_a_walker_cannot_occupy_is_not_a_destination", False,
         _case_a_face_a_walker_cannot_occupy_is_not_a_destination,
         neuter=_neuter_occupancy_test,
         neuters="the walker-capsule occupancy test in the chooser",
         prior_state="W5 B was given a stone_wall_2x1 cap with another "
                     "stone_wall_2x1 standing on it"),
    Case("a_generated_battlement_is_not_a_road_s_destination", True,
         _case_a_generated_battlement_is_not_a_road_s_destination,
         neuter=_neuter_reach_envelope,
         neuters="the walker-reach envelope in check_arrival",
         prior_state="W5 B at (-774, -290), applied 33.208, is offered a "
                     "stone_wall_2x1 battlement at 46.026 on a GENERATED "
                     "curtain 11.57 m away; w-south has no site pad in "
                     "the chain and the nearest declared rectangle is "
                     "143.2 m away. The verdict owed is UNMEASURED with "
                     "the candidate named, and a confident datum miss "
                     "against a wall is the wrong answer"),
    Case("a_join_is_judged_an_approach_is_walked", False,
         _case_a_join_is_judged_an_approach_is_walked,
         neuter=_neuter_join_split,
         neuters="the join/approach population split, so every terminus "
                 "is an approach and no join is asked the join question",
         prior_state="terminus_datum asked a JOIN question of APPROACHES. "
                     "R1 B (gap 10.77 m), T8 B and T9 A (7.21 m) were "
                     "each refused -0.842 m against the wt-town pad while "
                     "their approaches are strictly monotone 39.608 -> "
                     "40.450 with no berm by construction and a worst 1 m "
                     "edge of 0.4229 m, 54 % of the 0.7813 m slide limit "
                     "- MEASURED 61 of 61 lattice vertices, 0 unresolved "
                     "of 44. The write it would have justified fills a "
                     "node a walker already crosses and transfers the "
                     "0.842 m backwards onto three carriageways"),
    Case("an_approach_that_is_not_walkable_still_refuses", True,
         _case_an_approach_that_is_not_walkable_still_refuses,
         neuter=_neuter_final_edge_onto_the_destination,
         neuters="the last 1 m edge, from the ground the walk ends on "
                 "ONTO the destination itself",
         prior_state="the other direction of the same split: a gap term "
                     "that silently stops testing anything is an amnesty. "
                     "The walk reads GROUND and the destination is a deck "
                     "or a tread, so the step a walker takes LAST was "
                     "caught only by accident, by the datum test, from "
                     "the wrong end of the approach"),
    Case("the_last_step_names_the_footfall_it_measured", True,
         _case_the_last_step_names_the_footfall_it_measured,
         neuter=_neuter_riser_published_as_a_lattice_edge,
         neuters="the riser republished as [last_vertex, term.x, term.z] "
                 "and called a 1 m lattice edge, with the sign discarded",
         prior_state="gate C, save a087af6253a0, settlements refusal 3 of "
                     "9: road_S12_brgs1_boathouse A published step_m "
                     "3.852, edge_m 1.0, at [-301.0,-125.0,-281.6,-130.7] "
                     "and the words 'a single 1 m lattice edge'. Those "
                     "two points are 20.22 m apart because the second is "
                     "the ROAD'S OWN TERMINUS, so the refusal's "
                     "arithmetic could not be checked from its own "
                     "numbers and the repair_population row the "
                     "Lipschitz-cone disposal consumes would have sized a "
                     "cut against 20 m of ground that is not the defect. "
                     "abs() also erased the sign, and a disposal that may "
                     "only CUT cannot act without it"),
    Case("an_unread_last_footfall_is_not_substituted", True,
         _case_an_unread_last_footfall_is_not_substituted,
         neuter=_neuter_last_footfall_substituted,
         neuters="the backwards scan for the first vertex that WAS read, "
                 "published under the name of the vertex that was not",
         prior_state="the riser was read with `next(hbv[v] for v in "
                     "reversed(verts) if v in hbv)` and published at "
                     "verts[-1], so a walk whose tail the ground "
                     "instrument never answered reported the step a "
                     "walker takes LAST from wherever the reading "
                     "happened to stop - with a number, at full "
                     "confidence, while `unresolved` stayed silent "
                     "because its None arm fires only when NOTHING on "
                     "the walk was read"),
    Case("a_refusal_says_whether_a_write_could_close_it", True,
         _case_a_refusal_says_whether_a_write_could_close_it,
         neuter=_neuter_repair_ownership,
         neuters="the flatten=FORBIDDEN ownership lookup, so a refusal "
                 "proposes a lowering and says nothing about whether the "
                 "one sanctioned write path would accept it",
         prior_state="gate C's nine refusals, re-read against the owned "
                     "set the guard itself uses (_site_evidence, 132,148 "
                     "points over 397 live flatten=FORBIDDEN records, "
                     "ledger md5 0ebe71d9): FOUR name ground writer will "
                     "not let anyone move - N0 A and S1 B 0.970 m from "
                     "bridge_assembly seq 21 against footprints 1.394 and "
                     "1.256, S12 B 1.413 m from boathouse_place seq 30 "
                     "against 2.230, S12 A's riser 0.783 m from the same "
                     "against 4.930 - and THREE stand clear: T13 B at "
                     "1.994 against 1.333, R4 B at 3.606 against 1.023, "
                     "T9 B at 11.858 against 2.783. Nothing in any of the "
                     "nine said which it was. And read one record at a "
                     "time instead of by site the owned set is 7,409 "
                     "points, on which T13 B and S12 A both come back "
                     "BACKWARDS"),
    Case("a_footprint_is_sized_for_a_cut_that_can_be_written", True,
         _case_a_footprint_is_sized_for_a_cut_that_can_be_written,
         neuter=_neuter_a_footprint_sized_onto_the_limit,
         neuters="the disposal depth, back to the shipped state: a step "
                 "refusal's footprint sized for `step - slide limit`, the "
                 "depth that lands EXACTLY ON the limit and that no "
                 "disposal in this tree writes",
         prior_state="Ulfsland 245daa7f719ab69d, all 6 step-shaped "
                     "refusals that name a 1 m edge, asked both ways "
                     "against the live flatten=FORBIDDEN extent set. ONE "
                     "moves: roads/traversability "
                     "T13-temple-harboursouth station 423, the 0.794 m "
                     "step at (14,-255)->(14,-256), owner harbour_place "
                     "seq 24 at 1.4314 m. At step-LIMIT the depth is "
                     "0.0127 m and the footprint 1.430 m, so the owner "
                     "is OUTSIDE and the row shipped ACTIONABLE; at "
                     "step-SLOPE the depth is 0.0240 m and the footprint "
                     "1.445 m, so the owner is INSIDE and the row is "
                     "1A_FORBIDDEN. The two footprints straddle the "
                     "owner by 15 mm and roads/cone.py needs 0.0245 m "
                     "there. That row spent a whole convergence wave on "
                     "the work queue as `13 mm over` while Ledger.append "
                     "would have refused every cut that closes it"),
    Case("a_repair_says_whose_ground_it_would_eat", True,
         _case_a_repair_says_whose_ground_it_would_eat,
         neuter=_neuter_contested_ground,
         neuters="the recorded-terrain_write lookup, so a refusal "
                 "proposes a cut and says nothing about whether the "
                 "ground being cut is already another actor's",
         prior_state="the OTHER half of the operator's own blocker, and "
                     "the half no guard asks: writer's flatten test says "
                     "whether a cut is ALLOWED and has no opinion on "
                     "whose material it is. MEASURED on 0571968d over the "
                     "855 cut vertices the harbours draining repairs "
                     "propose - 812 writer-CLEAR, and all but ONE of "
                     "those inside a road's own terrain_write footprint, "
                     "so the guard would wave through a repair that "
                     "drowns an embankment another builder raised. The "
                     "single vertex owned by nobody was the only one "
                     "needing no operator decision and was "
                     "indistinguishable from the other 811"),
    Case("a_refusal_states_which_way_its_repair_moves_ground", True,
         _case_a_refusal_states_which_way_its_repair_moves_ground,
         neuter=_neuter_every_repair_is_assumed_to_be_a_cut,
         neuters="the repair direction, back to the shipped state: there "
                 "was no such field, so every consumer of an ACTIONABLE "
                 "row had to assume the remedy was a CUT",
         prior_state="the full gate on Ulfsland 245daa7f719ab69d, "
                     "artefact /tmp/final_gate.json. "
                     "settlements/terminus_datum "
                     "road_T13_temple_harboursouth B at (12,-258) reads "
                     "applied 31.195 m against a wood_stair standing top "
                     "of 31.700 - error_m -0.505, the carriageway BELOW "
                     "its destination - and was published ACTIONABLE "
                     "with nothing on the routing path to say the "
                     "remedy is a RAISE. Every terrain instrument in "
                     "this tree that consumes a refusal row is "
                     "cut-only: regrade.py asserts it per delta and "
                     "harbours.restoration_set raises on one fill. The "
                     "sign was measured by check_arrival and printed in "
                     "the message; it simply never reached a field"),
    Case("a_permitted_raise_is_unserved_not_actionable", True,
         _case_a_permitted_raise_is_unserved_not_actionable,
         neuter=_neuter_a_permitted_write_is_assumed_to_have_a_writer,
         neuters="the shipped classifier: read the writer's PERMISSION "
                 "and infer from it that a repair exists, whatever "
                 "direction it is in",
         prior_state="the full gate on Ulfsland 7632b3a5674842bd, "
                     "artefact /tmp/gate_final2.json. "
                     "settlements/terminus_datum "
                     "road_T13_temple_harboursouth B at (12,-258): "
                     "error_m -0.505, repair_direction raise, "
                     "repairable_by_terrain_write true, published "
                     "ACTIONABLE. It is the ONLY raise on a board of 105 "
                     "refusals and it was one of the twelve rows the "
                     "factory called work - while regrade.py asserts "
                     "cut-only per delta, cone.py refuses the row by that "
                     "very field, and harbours.restoration_set raises on "
                     "a set holding one fill. ACTIONABLE's first clause - "
                     "a repair EXISTS - was being inferred from its "
                     "second - the writer PERMITS it"),
    Case("a_permitted_cut_is_still_actionable", True,
         _case_a_permitted_cut_is_still_actionable,
         neuter=_neuter_every_permitted_repair_is_unserved,
         neuters="the degenerate over-correction: nothing is served, so "
                 "every refusal proposing a repair is 1C_UNSERVED and the "
                 "work queue empties",
         prior_state="the partner arm. Routing a raise to 1C_UNSERVED can "
                     "only ever REMOVE rows from the work queue, so a "
                     "classifier that called every permitted repair "
                     "unserved would flip the other arm and be stamped "
                     "PROVEN - an empty queue reading as a converged "
                     "world. Scored as a conjunction on the same fixture "
                     "with its sign reversed: 0.505 m ABOVE the same "
                     "wood_stair is a CUT, regrade.py performs it, and it "
                     "stays ACTIONABLE"),
    Case("only_an_answered_question_becomes_a_class", True,
         _case_only_an_answered_question_becomes_a_class,
         neuter=_neuter_termination_class_as_shipped,
         neuters="the termination classifier, back to the form that ran "
                 "the full gate: any contested_by is a contest including "
                 "the actor's OWN write, 1B without ever asking whether "
                 "the writer permits the cut, and no class at all for a "
                 "body standing in the carriageway",
         prior_state="the full gate on Ulfsland 245daa7f719ab69d, 106 "
                     "refusals over eleven sections. SIX of the 13 "
                     "1B_CONTESTED rows were contested BY THEMSELVES - "
                     "road_N0_brgs2_wmid seq 2629, road_S12_brgs1_"
                     "boathouse seq 342, road_S1_portalhub_brgs2 seq "
                     "417, road_T13_temple_harboursouth seq 338, "
                     "road_T9_wttown_dockshore seq 234 and roads "
                     "T9-wttown-dockshore-sb01 against that same seq "
                     "234 - and a carriageway is always inside its own "
                     "write, so 1B swallows every road step on every "
                     "world. EVERY settlements refusal in that artefact "
                     "carried repairable_by_terrain_write, "
                     "ground_owned_by and ground_owner_distance_m all "
                     "null while contested_by was populated, so the six "
                     "1B rows were returned off half an answer and "
                     "1A_FORBIDDEN was 0 of 106 - UNREACHABLE - while "
                     "the same section run harness-free reported 4. And "
                     "84 of the 106 had NO_CLASS, 54 of them roads rows "
                     "whose blocker is 47 standing bodies / 439 "
                     "must-climb pieces in the carriageway"),
    Case("a_stair_is_met_at_the_tread_under_the_walker", False,
         _case_a_stair_is_met_at_the_tread_under_the_walker,
         neuter=_neuter_arrival_face,
         neuters="the per-column arrival face, back to the whole solid's "
                 "highest level face",
         prior_state="a wood_stair answered ONE height for the whole "
                     "piece - its summit - so a road at the foot of a "
                     "flight was judged against the top of it. MEASURED "
                     "on 9a242873: T13 B -1.032 m against the summit "
                     "32.227, -0.505 m against the flight at its own "
                     "arrival column 31.700; R1 A's stair faces the "
                     "road with its TOP tread and its -1.021 m stands"),
    Case("a_pad_is_one_segment_end_s_destination", False,
         _case_a_pad_is_one_segment_end_s_destination,
         neuter=_neuter_sibling_end_exclusion,
         neuters="the sibling-end exclusion on the pad tier",
         prior_state="S1's A end stands ON portal-hall and passes; its B "
                     "end 39.6 m away was given the same pad 17.3 m "
                     "outside its rectangle and refused four times over "
                     "the hillside between, while standing on a bridge "
                     "deck at gap 0.000"),
    Case("an_unstood_declaration_names_which_defect_it_is", True,
         _case_an_unstood_declaration_names_which_defect_it_is,
         neuter=_neuter_pad_readings,
         neuters="the pad's other readings, dropped",
         prior_state="vestvik-hut-2 declares 34.370; R4 A's footfall reads "
                     "exactly 34.370 and R3 B's reads 35.214, so the pad "
                     "is 0.844 m out of level across its own interior - "
                     "and the finding printed sites.yaml's district-figure "
                     "story, which is a different site and a different "
                     "cause"),
    Case("the_first_inside_sample_is_inside_the_destination", False,
         _case_the_first_inside_sample_is_inside_the_destination,
         neuter=_neuter_footprint_after_snap,
         neuters="the footprint test applied after the snap",
         prior_state="R3 B's datum was read at (-905.0, 1893.0), 0.200 m "
                     "OUTSIDE vestvik-hut-2's own claim, on natural "
                     "ground reading 35.214 against a declared 34.370 - "
                     "and this section reported the PAD as 0.844 m out of "
                     "level while the structures section, which reports "
                     "on both its neighbours, finds no sample on it more "
                     "than 0.100 m off"),
    Case("a_t_junction_onto_a_carriageway_is_a_junction", False,
         _case_a_t_junction_onto_a_carriageway_is_a_junction,
         neuter=_neuter_centreline_tier,
         neuters="the T-junction (centreline) tier",
         prior_state="R3 A stands 2.000 m from N0b's centreline - inside "
                     "its 4.0 m paved-plus-shoulder width, and exactly "
                     "the 2.0 m standoff segments.yaml declares for that "
                     "endpoint - and was reported as a road that arrives "
                     "nowhere measurable, beside a piece_groundtorch "
                     "17.5 m away and a pad 435.6 m away"),
    Case("a_road_to_a_cape_arrives_on_the_cape", False,
         _case_a_road_to_a_cape_arrives_on_the_cape,
         neuter=_neuter_extremity_tier,
         neuters="the extremity tier (EXTREMITY_END_DEPENDS_ON)",
         prior_state="Fresh42 drive 4, save af96bfb62427: S5-temple-"
                     "southcape B, 2.1 m from the southcape extremity, "
                     "refused 1C terminus_step +8.2398 m onto a "
                     "stone_wall_2x1 course of the seed ruin "
                     "StoneTowerRuins09 28.9 m away, which the road was "
                     "never routed to"),
    Case("a_road_to_a_town_arrives_on_its_district", False,
         _case_a_road_to_a_town_arrives_on_its_district,
         neuter=_neuter_planned_end("district"),
         neuters="the DISTRICT arm of PLANNED_END_DEPENDS_ON",
         prior_state="Fresh42 drive 5, save 8c8c54096789: T7-stathub-"
                     "stenvik B, 32.8 m inside the stenvik town's 100 m "
                     "district, no pad holding the node, nearest pad "
                     "stenvik-longhouse-2 32.85 m away - reported 1C "
                     "terminus_unmeasured, 'meets NOTHING this gate can "
                     "read'"),
    Case("a_road_short_of_a_dock_across_water_names_the_placement", True,
         _case_a_road_short_of_a_dock_across_water_names_the_placement,
         neuter=_neuter_planned_end("short"),
         neuters="the SHORT arm of PLANNED_END_DEPENDS_ON",
         prior_state="Fresh42 drive 5, save 8c8c54096789: T5-temple-"
                     "dockshore B 67.46 m short of the early-dock pad that "
                     "holds its node, across water 9.4-68.9 m out and "
                     "6.05 m deep - reported 1C terminus_unmeasured with no "
                     "owner"),
    Case("a_road_whose_destination_the_build_refused_names_the_refusal",
         True,
         _case_a_road_whose_destination_the_build_refused_names_the_refusal,
         neuter=_neuter_planned_end("refused"),
         neuters="the REFUSED arm of PLANNED_END_DEPENDS_ON",
         prior_state="Fresh42 drive 5, save 8c8c54096789: S8-wtsouth-"
                     "treesth A, its wt-south pad REFUSED at step 5 (seq "
                     "2077) and its joining leg J-southcape-wtsouth REFUSED "
                     "at step 4 road_ground rc=7 - reported 1C "
                     "terminus_unmeasured with no owner"),
    Case("a_lamp_does_not_freeze_the_road_it_stands_beside", False,
         _case_a_lamp_does_not_freeze_the_road_it_stands_beside,
         neuter=_neuter_every_keep_out_freezes_its_ground,
         neuters="the keep-out KIND, widened back: every keep-out freezes "
                 "its ground, so the torch's spurious refusal returns and "
                 "the pile's genuine one - which no widening can change - "
                 "still stands",
         prior_state="Ulfsland save bc484171e3039bc0: ONE piece_groundtorch "
                     "at (-907.4138, 7713.005), own footprint 0.233 x "
                     "0.237 m, held FOUR N4-mid2-terminus stations at "
                     "1A_FORBIDDEN - 2962/2964/2965/2967, rises 2.106 / "
                     "2.416 / 2.538 / 3.041 m against a 0.7813 m limit, "
                     "owner distances 0.414 to 1.155 m, repair cones "
                     "3.125 to 4.321 m. A 3.041 m step in a carriageway "
                     "that nothing could ever repair"),
    Case("a_bridge_pile_still_freezes_the_bed_it_stands_in", True,
         _case_a_bridge_pile_still_freezes_the_bed_it_stands_in,
         neuter=_neuter_every_keep_out_is_dischargeable,
         neuters="the keep-out KIND, narrowed the other way: every keep-out "
                 "is dischargeable, so a 6.349 m cut 1.0 m from a bridge "
                 "pile reads repairable",
         prior_state="N1-wnorth-shelf station 1585, bridge_body_place seq "
                     "2809 at 1.0 m, rise -6.349 m: the keep-out this "
                     "correction must not spend"),
    Case("a_reseat_debt_may_not_launder_a_forbidden_seq", True,
         _case_a_reseat_debt_may_not_launder_a_forbidden_seq,
         neuter=_neuter_a_forbidden_seq_may_be_laundered,
         neuters="writer.keep_out_kind, flattened to RESEAT, so a "
                 "terrain_write may declare reseats=[a bridge pile] and be "
                 "let through",
         prior_state="writer._flatten_forbidden_check has NO owner "
                     "exception by design - a deck buried its own tiles "
                     "because of it - and the discharge field must not "
                     "become one by the back door"),
    Case("a_trigger_volume_is_not_a_body_on_the_pad", False,
         _case_a_trigger_volume_is_not_a_body_on_the_pad,
         neuter=_neuter_the_corpus_keeps_trigger_volumes,
         neuters="instruments.load_geometry with every non-trigger collider "
                 "kept, piece_artisanstation's 40 x 200 x 40 m capsule in",
         prior_state="drive 2, frozen Fresh42 a521b70356e7: S7 A and T6 A "
                     "published UNMEASURED/UNKNOWN at complete-station-hub "
                     "with all four probe vertices READ at 77.650, the "
                     "declared datum, each vetoed by the capsule"),
    Case("an_unstood_destination_carries_its_class", False,
         _case_an_unstood_destination_carries_its_class,
         neuter=_neuter_the_unstood_row_as_shipped,
         neuters="the unstood-destination row as shipped: 'was not read' "
                 "and termination_class UNKNOWN for every state",
         prior_state="drive 2's board: settlements terminus_unmeasured "
                     "UNKNOWN x2, the only UNKNOWN rows of 82 refusals"),
    Case("a_step_inside_the_reading_quantum_is_not_refused", False,
         _case_a_step_inside_the_reading_quantum_is_not_refused,
         neuter=_neuter_the_step_judged_finer_than_the_reading,
         neuters="the step compared to tan(38 deg) at full precision "
                 "against a four-decimal reading",
         prior_state="drive 2: terminus_step ACTIONABLE on T3 B and S8 B "
                     "at 0.7813 read; unrounded lattice 0.78128433, under "
                     "the 0.78128563 limit"),
    Case("a_step_past_the_reading_quantum_is_still_refused", True,
         _case_a_step_past_the_reading_quantum_is_still_refused,
         neuter=_neuter_every_step_is_inside_the_quantum,
         neuters="a resolution band so wide no step is ever over the limit",
         prior_state="the same edge read at 0.7815, 2.1e-4 over the limit"),
]

register(Section(
    name="settlements",
    measures=("every built road terminus arrives on the DATUM OF THE THING "
              "THAT IS STANDING at its destination - a reading off the "
              "applied surface and the census, never a site file's pad_y "
              "and never a pad write's target_y - judged on the datum "
              "error, the worst 1 m step, the BERM in the ground between, "
              "and whether the destination's own DECLARATION agrees with "
              "what stands there; and no declared site is seated on "
              "content the world generated"),
    instrument=("run_waterproof.sh FROM_SAVE=1 Heightmap.GetHeight for the "
                "applied surface at the terminus, at every metre between, "
                "and 4 m INSIDE the destination's own edge where the "
                "walker's first footfall lands; _approach.standing_surface "
                "over the save's object census for the walkable top there; "
                "ledger/writer.py::_pad_claims for each destination's "
                "rectangle and its DECLARED datum, which is judged rather "
                "than believed; structures.py::ledger_anchors for WHOSE a "
                "building is; live LocationProxy ZDOs against the seed's "
                "own location dump for generated content; the Player "
                "capsule for the tolerance and for the reach envelope"),
    population=("every terminus of every road segment the ledger records a "
                "terrain_write for, resolved in THREE TIERS OF EVIDENCE: "
                "the nearest live site pad CLAIM within 30 m - through "
                "writer._pad_claims, not off params.centre, which 50 of "
                "this world's 66 live site_pad writes do not carry - else "
                "a MEASURED coincidence within 1 m of another built "
                "segment's terminus, which is a ROAD/ROAD JUNCTION and is "
                "deferred by name to junction.py, else the nearest "
                "walkable top in the census that is not a bounding box, "
                "not a face a walker's own capsule cannot occupy and not "
                "more than one step above the piece's own footing; a "
                "terminus none of those answer for is resolved against the "
                "node its plan routes it to (PLANNED_END_DEPENDS_ON): a "
                "town's district it stands in, the pad holding its node "
                "it stops short of, or the build refusals that left that "
                "node with nothing; and "
                "every live site pad rectangle against every build-piece "
                "ZDO in the save that neither a live ledger record nor any "
                "anchored plan of ours at that pad declares"),
    run=run,
    cases=CASES))
