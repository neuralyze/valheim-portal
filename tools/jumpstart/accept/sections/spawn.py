#!/usr/bin/env python3
"""SPAWN - where the world puts a player who has just died, and whether they
can stand up there.

THE DEFECT THIS EXISTS FOR.  A character spawn on this world was once pointed
at (-4673, 73, -350) ON THE STRENGTH OF A PLAN.  A live census of that place
later found 0 smelters, 0 kilns, 0 chests, 0 workbenches, 0 torches,
0 portals and 826 natural objects: empty forest, 4.7 km west of anything
anyone had built.  Every check that had been run on it was a check on the
PLAN, and the plan said there was a settlement there.

So a spawn seat is judged by four questions, all asked of the world:

  PENETRATION  is the walker's own capsule inside a solid at that point?  The
               quantity is SIGNED and negative means buried.  A probe that
               returned 0.0 both for "just touching" and for "1 m inside" is
               the same blindness that let a 212-piece watchtower stand in
               13.5 m of air while four censuses reported zero floating.
  CLEARANCE    how much room is there round the seat at standing height?
               Less than the player's own measured capsule radius and they
               are wedged.
  HEADROOM     how much clear height above the surface?  Less than the
               player's own measured capsule height and they are inside the
               ceiling.
  DROP         how far is the fall from the seat to the surface under it?
  REACH        how far is the nearest thing anybody built?  This is the one
               that would have caught the forest.

AND THE SEAT TEST IS PROVED TO FIRE.  It is not enough that the current seat
passes: the selftest points the same probe at a portal-hall ARCH BAY and at a
PILLAR CELL - two real cells of a real floor, by their measured world
coordinates - and requires a refusal from both.  A seat test that has only
ever been shown to pass has never been shown to be a test.

POPULATION: the world's START SPAWN (`StartTemple`, from the seed's own
location dump, matched to its live `LocationProxy` where one stands) plus
every claimable BED ZDO in the save.  `goblin_bed` and `dvergrprops_bed` are
location dressing and are NOT claimable; they are excluded by name and the
exclusion is counted, because a population is not a population until the
exclusions are on the page.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

from . import _approach as A
from .. import termination as T
from ..registry import Case, Finding, Section, SectionResult, register


# WHY EACH REFUSAL THIS SECTION CAN EMIT CAN OR CANNOT BE CLOSED.
#
# THE POPULATION IS `termination.emittable("spawn")` - read out of this
# file, not off a world - and `termination.coverage` refuses the gate's own
# selftest if any of it is unnamed here.  On Ulfsland this section refused
# nothing, so its silence was indistinguishable from coverage until the
# fresh seed Fresh42 put a refusal in it.
#
# A SEAT IS NOT A CARRIAGEWAY AND THE CLASSES ARE NOT THE ROAD'S, but three
# of the five world-facing checks are the SAME MECHANISM as 1D: something
# is STANDING in the space the player needs.  The remedy is to retire the
# piece or to re-site the seat, never a terrain cut, and the shared
# `_obstructed` predicate is what says so - fed a COUNTED body, never an
# assumed one.
#
#   PENETRATION / CLEARANCE / HEADROOM  a named piece occupies the seat.
#     1D, off the body count.  A hard row that names NO piece is this
#     section refusing without saying what it measured, and that is a
#     defect in its source: 1C, never a clear one and never UNKNOWN.
#   DROP  the surface under the seat is too far below it.  The ground would
#     have to come UP and every terrain instrument in this tree is cut-only
#     by rule, so this is 1C BY DIRECTION - stated through the shared
#     `repair_direction`, which reads the sign this check already publishes.
#   REACH  nothing is built within reach of the seat.  Closed by BUILDING a
#     destination or by RE-SITING the seat, and nothing in this tree does
#     either from a refusal row.  1C.
#   SURFACE  neither ground nor piece offered anything to stand on - which
#     `standing_surface` returns ONLY when the applied ground under the
#     seat was not read, and the ground instrument is a tool in this tree.
#     1E, naming it.  It was UNKNOWN (`NOT_MEASURED`) until drive 2.
_GROUND = ("the ground instrument (accept ground() -> terraform/"
           "run_waterproof.sh FROM_SAVE=1)")
_UNNAMED = (
    "SOURCE_CHANGE",
    "this arm refused without naming the piece or the drop it measured, so "
    "no body census and no sign stands behind the row; making the arm "
    "publish what it measured is a change to this section's source")


def _piece_family(nums):
    """A named piece in the seat is a BODY; an unnamed one is a refusal
    this section's source did not finish writing."""
    if not nums.get("piece"):
        return _UNNAMED
    nums["obstruction_bodies"] = 1
    return ("BODY_IN_THE_WAY",
            f"{nums['piece']} occupies the space this seat needs; the "
            f"remedy is to retire it or to re-site the seat, and no terrain "
            f"cut removes a building")


def _drop_family(nums):
    d = nums.get("drop_m")
    if not isinstance(d, (int, float)) or d <= 0:
        return _UNNAMED
    # THE SIGN, PUBLISHED WHERE THE SHARED ACCESSOR LOOKS.  A seat above
    # its own surface is closed by ground coming UP; `repair_direction`
    # reads `judged_as`/`rise_m` and the shared classifier turns a raise
    # into 1C_UNSERVED.  Nothing here decides the class.
    nums["judged_as"] = "riser"
    nums["rise_m"] = d
    return ("GROUND_MUST_RISE",
            f"the surface under this seat is {d:.3f} m below it and every "
            f"terrain instrument in this tree is cut-only; the other remedy "
            f"is to move the seat, which is a plan change")


def _instrument_family(nums):
    if nums.get("dump"):
        return ("CENSUS_NOT_TAKEN",
                f"re-run tools/seedscan/run_locscan.sh to produce the seed's "
                f"location dump at {nums['dump']}; the world's START SPAWN "
                f"cannot be located at all without it")
    return ("CENSUS_NOT_TAKEN",
            f"{_GROUND} must read the applied ground under the "
            f"{nums.get('seats')} spawn seat(s) it refused for; a seat whose "
            f"ground is unread is unmeasured")


TERMINATION_FAMILIES = {
    "instrument": _instrument_family,
    "seat_surface": (
        "CENSUS_NOT_TAKEN",
        f"{_GROUND} must read the applied ground under this seat: no piece "
        f"covers it, and a seat with no surface read is unmeasured"),
    "seat_penetration": _piece_family,
    "seat_clearance": _piece_family,
    "seat_headroom": _piece_family,
    "seat_drop": _drop_family,
    "seat_reach": (
        "NO_DESTINATION",
        "the seat was chosen from a plan and the world does not agree; "
        "closing it means BUILDING something within reach or RE-SITING the "
        "seat, and no tool in this tree does either from a refusal row"),
}

# Player-buildable beds.  `Bed.Interact` is what claims a spawn, and only
# these two prefabs carry a Bed a player can own on this deployment.
CLAIMABLE_BEDS = ("bed", "piece_bed02")
PROP_BEDS = ("goblin_bed", "dvergrprops_bed")
START_LOCATION = "StartTemple"

# DESIGN, not measured, and labelled so nobody quotes it as a measurement.
# There is no instrument on this host that reads Valheim's fall-damage
# threshold without a client; 4 m is the documented height at which a fall
# starts to hurt.  The DROP ITSELF is measured and printed, so a reader who
# disagrees with the threshold can still see the number.
DROP_LIMIT_M = 4.0
# DESIGN.  A spawn is "within reach of something" if a built piece stands
# within this radius.  The defect it is shaped around was 4,700 m out, so the
# exact value is not load-bearing; the MEASURED distance is printed on every
# seat either way.
REACH_M = 50.0
# How far the clearance probe looks before it reports "capped".  A capped
# answer is reported as capped and never as "plenty".
CLEAR_CAP_M = 3.0
HEADROOM_CAP_M = 6.0


def _start_temple(seed_dump: Path | None) -> tuple[float, float, float] | None:
    """The seed's own StartTemple entry.  SEED-DERIVED: it survives a world
    regeneration and can be re-measured before a single piece is placed,
    which is exactly the property a spawn seat should have."""
    if not seed_dump or not seed_dump.exists():
        return None
    doc = json.loads(seed_dump.read_text())
    for loc in doc.get("locations", []):
        if loc.get("name") == START_LOCATION:
            return (float(loc["x"]), float(loc["y"]), float(loc["z"]))
    return None


def _find_seed_dump(ctx) -> Path | None:
    """THIS SEED's location dump, asked of the resolver that owns the
    question, and only then of the plan that happens to name one.

    THE OLD READ WAS THE SAME DEFECT AS THE THREE CORPUS READS.
    `settlements/plan.json` is a repo SINGLETON - one path, one seed - and
    this function took its `locations_dump` with no scope check, so
    `--seed Fresh42` followed Pirate68's dump and seated a fresh world's
    spawn on Ulfsland's StartTemple.  `settlements::_markers` reads the same
    function for every LocationProxy radius in that section, so one
    unguarded read fed two sections.

    AND REFUSING IT ALONE WOULD HAVE BEEN HALF A FIX.  MEASURED 2026-09-23
    on the Fresh42 drive: guarding the plan turned three instrument answers
    into three refusals WHILE THAT RUN'S OWN DUMP - 12,376 locations,
    `"seed": "Fresh42"` - sat unread in /tmp/seedfield/loc.  A refusal that
    names no reachable remedy is an invitation to work around it, which is
    the sentence `roads/poi.py::dump_for_seed` was written under, and that
    function IS the resolver: it reads each root's own `index.tsv`, the
    producer's own statement of which file belongs to which seed.  So it is
    asked first, and this run's OWN ledger pipeline root is offered to it
    beside the `/tmp` ones so a dump survives a scratch wipe.

    The plan remains as the fallback, AT ITS OWN PER-SEED SLOT and
    SCOPE-CHECKED.  `roads/spec.py::plan_path` answers `plan.json` for the
    default seed and `plan.<seed>.json` for any other, honouring
    `$VH_SETTLEMENT_PLAN` over both - the same shape as `sites_path`, and
    the same rule: ask the resolver that owns the question rather than
    freezing a name here.  `require_world=False` is a declaration about the
    artefact: a worldgen dump is a function of the SEED alone, carries
    `world: null` on purpose, and is valid in any world built on that seed.
    """
    cand = getattr(ctx.args, "locations_dump", None)
    if cand:
        return Path(cand)
    I = A.instruments()
    want_seed, _want_world = I.run_scope(ctx)
    roads = str(I.JUMPSTART / "roads")
    if roads not in sys.path:
        sys.path.insert(0, roads)
    import poi as poimod                                   # noqa: PLC0415
    roots = poimod.DUMP_ROOTS + (Path(ctx.ledger_dir) / "pipeline" / "loc",)
    try:
        return poimod.dump_for_seed(want_seed, roots=roots)
    except SystemExit as exc:
        ctx.log(str(exc))
    doc, refusals = I.read_scoped_corpus(
        ctx, "the settlements plan", resolve=I._worldseed().plan_path,
        produced_by="settlements/plan.py::main", require_world=False)
    if refusals:
        for f in refusals:
            ctx.log(f.message)
        return None
    try:
        p = Path(doc["locations_dump"])
    except (KeyError, TypeError):
        return None
    return p if p.exists() else None


# ---------------------------------------------------------------------------
# THE SEAT PROBE - one function, used by the world and by the selftest
# ---------------------------------------------------------------------------
def probe_seat(label: str, x: float, y: float, z: float, bed: float | None,
               grid, walker_r: float, walker_h: float,
               nearest_piece_m: float | None = None,
               skip=(), record: dict | None = None) -> list[Finding]:
    """Everything that can be said about one seat, as findings.

    `bed` is the APPLIED ground read back through the engine, or None when
    the ground was not read here - in which case the surface can still come
    from a floor piece, and if neither exists the seat is UNMEASURED and that
    is a refusal, not a pass.

    `nearest_piece_m` IS MEASURED WITHOUT THE SEAT'S OWN PIECE IN IT, and
    the caller owes that exclusion - see `run()`.  A bed IS a build piece,
    so a `nearest` taken over every build piece in the save returns 0.000 m
    for every bed in every world: the reference came from the artefact
    under test and the check could not fire.  MEASURED on Ulfsland: all 18
    claimable beds read 0.000 m, and the only reason `seat_reach` looked
    alive was that the selftest hands it a literal 4700.0.

    `skip` IS THE SEAT'S OWN SOLID, BY IDENTITY, AND NOT BY PREFAB NAME.
    A bed is not an obstruction to the player the world delivers INTO it,
    and until this argument was passed the probe reported every
    `piece_bed02` as burying its own occupant: MEASURED on Ulfsland,
    piece_bed02#17 at (-113.36, 3456.12) read '1.150 m INSIDE piece_bed02'
    - exactly the prefab's own 1.15 m x half-extent - and its headroom was
    capped by its own 1.455 m headboard.  That is not a defect in the
    world, it is the instrument answering a question about the seat with a
    measurement of the seat, and it would refuse every claimable
    `piece_bed02` in every world this pipeline ever builds.

    BY IDENTITY because a SECOND bed standing in the first one IS a defect
    (the structures section refuses co-located pieces for the same reason),
    so skipping by name would hide a real one to fix a false one.
    """
    # THE SURFACE IS CAPPED AT THE SEAT'S OWN HEIGHT, and this is not a
    # detail.  Without the cap the probe picks the highest flat top over the
    # cell - which for an arch bay is the ARCH's own 3.29 m crown - and then
    # reports a clear stand on top of the very piece the player is delivered
    # inside.  A player falls DOWN to their surface; a surface above the
    # seat is not one they land on.
    surf, carrier = A.standing_surface(x, z, grid, bed, ceiling=y + 1e-3,
                                       skip=skip)
    out: list[Finding] = []
    if record is not None:
        record.update(seat=label, x=round(x, 3), y=round(y, 3),
                      z=round(z, 3), surface_y=None, carrier=None,
                      penetration_m=None, clearance_m=None, headroom_m=None,
                      drop_m=None,
                      reach_m=(None if nearest_piece_m is None
                               else round(nearest_piece_m, 3)))
    if surf is None:
        return [Finding(
            check="seat_surface", ok=False,
            message=(f"spawn seat {label} at ({x:.2f}, {z:.2f}): neither the "
                     "applied ground nor any piece offers a surface to stand "
                     "on here, so the seat is UNMEASURED"),
            where=(x, y, z))]

    # THE PRECISION RE-RUN.  58 % of the solid corpus (6,956 of 11,972
    # non-render colliders) is a MeshCollider's `sharedMesh.bounds` - an
    # UPPER BOUND on a shape, not the shape.  `Beech1` is two of them and no
    # capsule at all, so it is carried as a 19.08 x 21.50 m prefab-local box,
    # 28-30 m once rotated, and it REFUSED bed#8 as "1.087 m INSIDE Beech1"
    # with the tree's pivot 23.2 m away.
    #
    # The test is a RE-RUN, not a reason string: measure again with every
    # bounds-only solid dropped, and let the TIGHT reading decide.  If the
    # refusal survives, it was a shape and the world is wrong.  If it
    # disappears, the bounding box was deciding the verdict - name it,
    # report it, and do not fail the world on a reading whose precision is
    # worse than the judgement.  It is not silently a pass either: an
    # advisory finding prints the box that produced it.
    loose = [s for s in grid.near(x, z, CLEAR_CAP_M + 2.0) if s.bounds_only]
    tight_skip = list(skip) + loose

    free, who = A.free_radius(x, z, surf, grid, walker_h, CLEAR_CAP_M,
                              skip=tight_skip)
    head, cap = A.headroom(x, z, surf, grid, walker_r, HEADROOM_CAP_M,
                           skip=tight_skip)
    bfree, bwho = ((free, who) if not loose else
                   A.free_radius(x, z, surf, grid, walker_h, CLEAR_CAP_M,
                                 skip=skip))
    bhead, bcap = ((head, cap) if not loose else
                   A.headroom(x, z, surf, grid, walker_r, HEADROOM_CAP_M,
                              skip=skip))
    drop = y - surf

    def _box(s) -> dict:
        return dict(piece=s.name,
                    bounding_box_m=[round(s.xmax - s.xmin, 2),
                                    round(s.ymax - s.ymin, 2),
                                    round(s.zmax - s.zmin, 2)],
                    pivot_distance_m=round(math.hypot(s.pos[0] - x,
                                                      s.pos[2] - z), 2))
    # THE FIVE NUMBERS, FOR EVERY SEAT, PASS OR FAIL.  A gate that prints a
    # number only when it refuses cannot be diffed across a repair: the
    # seats that now pass would vanish from the artefact and a reader could
    # not tell a fixed seat from a seat that stopped being measured.
    if record is not None:
        record.update(
            surface_y=round(surf, 3),
            carrier=(carrier.name if carrier else "applied ground"),
            penetration_m=round(-free, 3) if free < 0.0 else 0.0,
            clearance_m=round(free, 3),
            clearance_capped=(free >= CLEAR_CAP_M),
            clearance_piece=(who.name if who else None),
            headroom_m=round(head, 3),
            headroom_capped=(head >= HEADROOM_CAP_M),
            headroom_piece=(cap.name if cap else None),
            drop_m=round(drop, 3),
            bounds_only_near=sorted({s.name for s in loose}))

    # EVERY PIECE IN THE SEAT'S SPACE, NAMED BY PREFAB AND PIVOT - not only
    # the nearest one - because retiring the nearest does not free the seat
    # while another stands inside the capsule, and the ledger can only say
    # whose they are if they are named.  MEASURED on drive 3: 7 beds under
    # a `wood_beam` of our own town_body plans, all classed 1D as if a
    # player had built over them.  Same predicates `free_radius` and
    # `headroom` apply, over the same tight solid set.
    _tol = A.instruments().STEP_LIMIT_PER_M
    in_room = [T.piece_ref(s) for s in grid.near(x, z, walker_r + 2.0)
               if s not in tight_skip
               and A.blocks_at_height(s, surf, walker_h, _tol)
               and A.hull_distance(s, x, z) < walker_r]
    overhead = [T.piece_ref(s) for s in grid.near(x, z, walker_r + 1.0)
                if s not in tight_skip and s.verts
                and s.ymin > surf + 0.05 and s.ymin - surf < walker_h
                and A.hull_distance(s, x, z) <= walker_r]
    if free < 0.0:
        out.append(Finding(
            check="seat_penetration", ok=False,
            message=(f"spawn seat {label} at ({x:.2f}, {z:.2f}) is "
                     f"{-free:.3f} m INSIDE "
                     f"{who.name if who else 'a solid'}: the player arrives "
                     "buried in it"),
            where=(x, surf, z), measured=free, limit=0.0,
            numbers=dict(penetration_m=round(-free, 3),
                         piece=who.name if who else None,
                         surface_y=round(surf, 3),
                         blocker_pieces=in_room)))
    elif free < walker_r:
        out.append(Finding(
            check="seat_clearance", ok=False,
            message=(f"spawn seat {label} at ({x:.2f}, {z:.2f}) has "
                     f"{free:.3f} m of room and the player's own measured "
                     f"capsule radius is {walker_r:.3f} m"
                     + (f"; the nearest obstruction is {who.name}"
                        if who else "")),
            where=(x, surf, z), measured=free, limit=walker_r,
            numbers=dict(free_radius_m=round(free, 3),
                         walker_radius_m=walker_r,
                         piece=who.name if who else None,
                         blocker_pieces=in_room)))
    if bfree < walker_r <= free and bwho is not None:
        out.append(Finding(
            check="seat_clearance_bounds_only", ok=False, advisory=True,
            message=(f"spawn seat {label} at ({x:.2f}, {z:.2f}) reads "
                     f"{bfree:.3f} m of room against {bwho.name}, but "
                     f"{bwho.name} has NO tight collider in the corpus - "
                     "every part of it is a MeshCollider bounding box "
                     f"{_box(bwho)['bounding_box_m']} m whose pivot is "
                     f"{_box(bwho)['pivot_distance_m']} m away. Re-measured "
                     f"with the bounds dropped the seat has {free:.3f} m. "
                     "REPORTED, NOT DECIDED: the reading's precision is "
                     "worse than the judgement."),
            where=(x, surf, z), measured=bfree, limit=walker_r,
            numbers=dict(bounds_only_free_radius_m=round(bfree, 3),
                         tight_free_radius_m=round(free, 3),
                         walker_radius_m=walker_r, **_box(bwho))))

    if head < walker_h:
        out.append(Finding(
            check="seat_headroom", ok=False,
            message=(f"spawn seat {label} at ({x:.2f}, {z:.2f}) has "
                     f"{head:.3f} m of headroom over its surface "
                     f"{surf:.3f} and the player is {walker_h:.3f} m tall"
                     + (f"; the ceiling is {cap.name}" if cap else "")),
            where=(x, surf, z), measured=head, limit=walker_h,
            numbers=dict(headroom_m=round(head, 3), walker_height_m=walker_h,
                         piece=cap.name if cap else None,
                         blocker_pieces=overhead)))
    if bhead < walker_h <= head and bcap is not None:
        out.append(Finding(
            check="seat_headroom_bounds_only", ok=False, advisory=True,
            message=(f"spawn seat {label} at ({x:.2f}, {z:.2f}) reads "
                     f"{bhead:.3f} m of headroom against {bcap.name}, whose "
                     "whole solid in the corpus is a MeshCollider bounding "
                     f"box {_box(bcap)['bounding_box_m']} m with its pivot "
                     f"{_box(bcap)['pivot_distance_m']} m away. With the "
                     f"bounds dropped the headroom is {head:.3f} m. "
                     "REPORTED, NOT DECIDED."),
            where=(x, surf, z), measured=bhead, limit=walker_h,
            numbers=dict(bounds_only_headroom_m=round(bhead, 3),
                         tight_headroom_m=round(head, 3),
                         walker_height_m=walker_h, **_box(bcap))))

    if drop > DROP_LIMIT_M:
        out.append(Finding(
            check="seat_drop", ok=False,
            message=(f"spawn seat {label} at ({x:.2f}, {z:.2f}) is "
                     f"{drop:.3f} m above the surface under it "
                     f"({surf:.3f}"
                     + (f", carried by {carrier.name}" if carrier else
                        ", the applied ground") + f"); the DECLARED "
                     f"fall-damage threshold is {DROP_LIMIT_M:.1f} m"),
            where=(x, y, z), measured=drop, limit=DROP_LIMIT_M,
            numbers=dict(drop_m=round(drop, 3), surface_y=round(surf, 3))))

    if nearest_piece_m is not None and nearest_piece_m > REACH_M:
        out.append(Finding(
            check="seat_reach", ok=False,
            message=(f"spawn seat {label} at ({x:.2f}, {z:.2f}) has NOTHING "
                     f"built within {REACH_M:.0f} m - the nearest build "
                     f"piece in the whole save is {nearest_piece_m:.1f} m "
                     "away. This is the spawn-in-empty-forest defect: the "
                     "seat was chosen from a plan and the world does not "
                     "agree."),
            where=(x, y, z), measured=nearest_piece_m, limit=REACH_M,
            numbers=dict(nearest_build_piece_m=round(nearest_piece_m, 1))))
    return out


# ---------------------------------------------------------------------------
# the section
# ---------------------------------------------------------------------------
def run(ctx) -> SectionResult:
    findings: list[Finding] = []
    walker_r, walker_h = ctx.walker
    grid = ctx.grid_all()
    build = ctx.build_pieces

    seats: list[tuple[str, float, float, float]] = []
    dump = _find_seed_dump(ctx)
    st = _start_temple(dump)
    if st is None:
        findings.append(Finding(
            check="instrument", ok=False,
            message=("the seed's location dump is not readable, so the "
                     "world's START SPAWN cannot be located at all. Re-run "
                     "tools/seedscan/run_locscan.sh; a spawn that cannot be "
                     "found is not a spawn that is fine."),
            numbers=dict(dump=str(dump))))
    else:
        # The live marker, where one stands: the dump is the generator's
        # REQUEST list and reports `placed: false` on all 12,301 entries, so
        # the marker ZDO is the thing that is actually there.
        best, bd = st, math.inf
        for name, x, y, z, *_q in ctx.census.rows:
            if name != "LocationProxy":
                continue
            d = math.hypot(x - st[0], z - st[2])
            if d < bd:
                best, bd = (x, y, z), d
        seats.append(("StartTemple" + (f" (live marker {bd:.1f} m from the "
                                       f"seed's own entry)" if bd < 25 else
                                       " (seed entry; no live marker within "
                                       "25 m)"),
                      *(best if bd < 25 else st)))

    beds = [(n, x, y, z) for (n, x, y, z, *_q) in ctx.census.rows
            if n in CLAIMABLE_BEDS]
    props = sum(1 for (n, *_r) in ctx.census.rows if n in PROP_BEDS)
    # THE SEAT'S OWN SOLID, resolved BY IDENTITY: same prefab name AND the
    # same pivot to within a millimetre.  `own[i]` is what `probe_seat`
    # skips, and it is a list of at most one solid: a SECOND bed at the
    # same pivot is a duplicate and must stay visible to the probe.
    #
    # `mine[i]` is the SAME EXCLUSION applied to the reach population.  It
    # has to be: the reach question is "is there anything built near this
    # seat", a claimable bed IS a build piece, and a nearest taken over
    # every build piece therefore returned 0.000 m for every bed in the
    # save - the seat measuring itself.  See `probe_seat`'s docstring.
    own: dict[int, list] = {}
    mine: dict[int, tuple] = {}
    for i, (n, x, y, z) in enumerate(beds):
        seats.append((f"{n}#{i}", x, y, z))
        own[len(seats) - 1] = [
            s for s in grid.near(x, z, 0.0)
            if s.name == n and abs(s.pos[0] - x) < 1e-3
            and abs(s.pos[1] - y) < 1e-3 and abs(s.pos[2] - z) < 1e-3]
        mine[len(seats) - 1] = (n, x, y, z)

    # The applied ground under every seat, in ONE batched read.
    pts = [(f"spawn{i}", s[1], s[3]) for i, s in enumerate(seats)]
    beds_y: dict[str, float] = {}
    if pts:
        try:
            beds_y = ctx.ground(pts)
        except Exception as exc:                        # noqa: BLE001
            findings.append(Finding(
                check="instrument", ok=False,
                message=(f"the ground instrument refused for the {len(pts)} "
                         f"spawn seats ({type(exc).__name__}: {exc}); a seat "
                         "whose ground is unread is unmeasured, and the "
                         "piece surfaces below are all that could be used"),
                numbers=dict(seats=len(pts))))

    # Nearest build piece to each seat, from the census, so "within reach of
    # anything" is a measurement of the world and not of the plan - AND NOT
    # A MEASUREMENT OF THE SEAT.  The seat's own ZDO is removed from the
    # population by identity (prefab + full 3D position, never an id: ZDO
    # ids are not durable across a restart), so a bed alone in a forest
    # reads the distance to the forest and not 0.000 m to itself.
    piece_xz = [(n, x, y, z) for (n, x, y, z, *_q) in ctx.census.rows
                if n in build]
    records: list[dict] = []
    for i, (label, x, y, z) in enumerate(seats):
        self_row = mine.get(i)
        nearest = math.inf
        for pn, px, py, pz in piece_xz:
            if (self_row is not None and pn == self_row[0]
                    and abs(px - self_row[1]) < 1e-3
                    and abs(py - self_row[2]) < 1e-3
                    and abs(pz - self_row[3]) < 1e-3):
                continue
            d = math.hypot(px - x, pz - z)
            if d < nearest:
                nearest = d
        rec: dict = {}
        records.append(rec)
        findings += probe_seat(label, x, y, z, beds_y.get(f"spawn{i}"), grid,
                               walker_r, walker_h, nearest,
                               skip=own.get(i, []), record=rec)

    findings.append(Finding(
        check="population", ok=True,
        message=("{} spawn seat(s) judged: the world's StartTemple plus {} "
                 "claimable bed ZDO(s) ({}); {} bed-shaped location props "
                 "({}) are NOT claimable and were excluded by name. Walker "
                 "{:.3f} m radius x {:.3f} m tall, MEASURED off the Player "
                 "prefab this run.".format(
                     len(seats), len(beds), ", ".join(CLAIMABLE_BEDS), props,
                     ", ".join(PROP_BEDS), walker_r, walker_h)),
        numbers=dict(seats=len(seats), claimable_beds=len(beds),
                     excluded_prop_beds=props)))
    # AND EVERY REFUSAL ABOVE GETS ITS CLASS, THROUGH THE ONE CLASSIFIER -
    # asked whether this world's ledger placed the pieces it names.
    findings += T.classify(sys.modules[__name__], findings, "spawn",
                           placements=T.Placements.read(ctx.ledger))
    return SectionResult(findings=findings, report=dict(seats=records))


# ---------------------------------------------------------------------------
# THE SELFTEST
# ---------------------------------------------------------------------------
def _yaw(deg: float):
    h = math.radians(deg) / 2.0
    return (0.0, math.sin(h), 0.0, math.cos(h))


def _piece(name: str, x: float, y: float, z: float, yaw: float = 0.0):
    I = A.instruments()
    return I.mk_solid(name, (x, y, z), _yaw(yaw),
                      I.load_geometry()[0].get(name) or [])


def _hall_floor(cx: float, cz: float, y: float = 37.1, half: int = 4):
    """A patch of the portal hall's own floor: stone_floor_2x2 on a 2 m
    lattice, tops at y.  The prefab's pivot is its MID-SLAB, which is why a
    clean site's worst burial is exactly -1.000 m and why the slab is
    written here at y - 0.5 rather than at y."""
    out = []
    for i in range(-half, half + 1):
        for j in range(-half, half + 1):
            out.append(_piece("stone_floor_2x2", cx + 2.0 * i, y - 0.5,
                              cz + 2.0 * j))
    return out


def _case_clear_hall_floor():
    """The seat the hall actually offers: MEASURED on Ulfsland at
    (-273.04, 216.01), 3.000 m of clearance (capped) and 6.000 m of
    headroom."""
    I = A.instruments()
    pieces = _hall_floor(-273.04, 216.01)
    grid = I.Grid(pieces, cell=4.0)
    return probe_seat("clear hall floor", -273.04, 37.1 + 0.9, 216.01, 36.1,
                      grid, 0.49, 1.85, nearest_piece_m=0.5)


def _case_seat_in_an_arch_bay():
    """PROOF THE SEAT TEST FIRES.  The u-stenvik arch at its own measured
    world position and yaw: a seat on that cell puts the player inside the
    arch frame.  MEASURED with this probe on the live save: -0.442 m."""
    I = A.instruments()
    pieces = _hall_floor(-273.6617, 228.7303)
    arch = I.mk_solid("portal_wood", (-273.6617, 37.1, 228.7303),
                      _yaw(-157.5), I.load_geometry()[0]["portal_wood"])
    grid = I.Grid(pieces + [arch], cell=4.0)
    fs = probe_seat("arch bay", -273.6617, 37.1, 228.7303, 36.1, grid,
                    0.49, 1.85, nearest_piece_m=0.0)
    assert any(f.check == "seat_penetration" for f in fs), (
        "a seat inside an arch frame must be refused as PENETRATION, not as "
        "a tight fit")
    return fs


def _case_seat_in_a_pillar_cell():
    """PROOF THE SEAT TEST FIRES, second instrument reading: the hall's
    Piece_flametal_pillar at (-294.04, 218.99).  MEASURED with this probe on
    the live save: -0.971 m."""
    I = A.instruments()
    pieces = _hall_floor(-294.04, 218.99)
    pillar = _piece("Piece_flametal_pillar", -294.04, 37.1, 218.99)
    grid = I.Grid(pieces + [pillar], cell=4.0)
    fs = probe_seat("pillar cell", -294.04, 37.1, 218.99, 36.1, grid,
                    0.49, 1.85, nearest_piece_m=0.0)
    assert any(f.check in ("seat_penetration", "seat_clearance")
               for f in fs), "a seat in a pillar cell must be refused"
    return fs


def _case_spawn_in_empty_forest():
    """THE DEFECT, VERBATIM: the spawn pointed at (-4673, 73, -350), where a
    live census later found 0 smelters, 0 kilns, 0 chests, 0 workbenches,
    0 torches, 0 portals and 826 natural objects.  Nothing is in the way -
    clearance and headroom are both perfect - and that is precisely why a
    clearance check alone could not catch it."""
    I = A.instruments()
    grid = I.Grid([], cell=4.0)
    return probe_seat("plan spawn", -4673.0, 73.0, -350.0, 73.0, grid,
                      0.49, 1.85, nearest_piece_m=4700.0)


def _case_seat_under_a_beam():
    """Room to stand and nothing overhead is two different questions."""
    I = A.instruments()
    pieces = _hall_floor(0.0, 0.0, y=10.0)
    beam = _piece("wood_beam", 0.0, 11.2, 0.0)
    grid = I.Grid(pieces + [beam], cell=4.0)
    return probe_seat("under a beam", 0.0, 10.0, 0.0, 9.0, grid, 0.49, 1.85,
                      nearest_piece_m=0.5)


def _case_a_bed_under_our_own_beam_is_factory_work():
    """A SEAT BLOCKED BY A PIECE THE LEDGER PLACED IS THE FACTORY'S WORK.
    MEASURED on drive 3, save 05eb7c6c05de: 7 beds (bed#6 stenvik-house-3
    and six more) with 0.449 m of headroom under a `wood_beam` of our own
    town_body plan (seq 2100, step 5), 14 seat_* rows classed 1D."""
    I = A.instruments()
    pieces = _hall_floor(0.0, 0.0, y=10.0)
    beam = _piece("wood_beam", 0.0, 11.2, 0.0)
    grid = I.Grid(pieces + [beam], cell=4.0)
    fs = probe_seat("under our beam", 0.0, 10.0, 0.0, 9.0, grid, 0.49, 1.85,
                    nearest_piece_m=0.5)
    chain = T.Placements([dict(
        seq=2100, op="spawn_plan", actor="fixture",
        params=dict(role="town_body", site_id="stenvik-house-3",
                    command_count=1),
        meta=dict(build_step="5", build_unit="stenvik"),
        wire=["spawn_object wood_beam pos=0.0,0.0,11.2 rot=0,0,0"])],
        "fixture: the house that placed the beam")
    T.classify(sys.modules[__name__], fs, "spawn", placements=chain)
    head = next(f for f in fs if f.check == "seat_headroom")
    n = head.numbers
    return [Finding(
        check="termination", ok=n.get("termination_class") == "ACTIONABLE"
        and "bodies" in " ".join(n.get("owner_step") or ()),
        message=(f"a bed under our own beam classed "
                 f"{n.get('termination_class')}, owner {n.get('owner_step')}"),
        where=(0.0, 0.0), numbers=dict(cls=n.get("termination_class")))]


def _case_seat_over_a_drop():
    """A seat 9 m over its own floor is a seat that costs health on arrival;
    the 0.9 m the portal hall's floor gives is not."""
    I = A.instruments()
    pieces = _hall_floor(0.0, 0.0, y=10.0)
    grid = I.Grid(pieces, cell=4.0)
    return probe_seat("over a drop", 0.0, 19.0, 0.0, 9.0, grid, 0.49, 1.85,
                      nearest_piece_m=0.5)


def _case_no_surface_at_all():
    """No ground read and no piece: UNMEASURED, and unmeasured is a refusal.
    The one answer a gate may never give is "I could not measure it, so it
    is fine"."""
    I = A.instruments()
    grid = I.Grid([], cell=4.0)
    return probe_seat("unread", 100.0, 50.0, 100.0, None, grid, 0.49, 1.85,
                      nearest_piece_m=1.0)


def _case_bed_does_not_bury_its_own_occupant():
    """THE CHECK DEFECT THIS PINS, MEASURED ON ULFSLAND.

    `piece_bed02#17` at (-113.36, 3456.12) was refused TWICE by this
    section - `seat_penetration` '1.150 m INSIDE piece_bed02' and
    `seat_headroom` '1.690 m over its surface' - and the piece doing both
    was THE BED ITSELF.  1.150 m is exactly the prefab's own x
    half-extent and 1.455 m is exactly its own headboard: the instrument
    was answering a question about the seat with a measurement of the
    seat.  Only ONE `piece_bed02` stands in this world (census, whole
    save), so there was nothing else it could have been.

    Unskipped, that refusal fires for EVERY claimable `piece_bed02` in
    every world this pipeline builds, which is why it is a check defect
    and not a world defect.  This case is the ACCEPT half.
    """
    I = A.instruments()
    pieces = _hall_floor(-113.36, 3456.12, y=232.258)
    bed = _piece("piece_bed02", -113.36, 232.303, 3456.119)
    grid = I.Grid(pieces + [bed], cell=4.0)
    fs = probe_seat("piece_bed02#17", -113.36, 232.303, 3456.119, 232.258,
                    grid, 0.49, 1.85, nearest_piece_m=0.0, skip=[bed])
    assert not any(f.check == "seat_penetration" for f in fs), (
        "a bed is not something the player who wakes in it is buried in: "
        f"{[f.message for f in fs]}")
    return fs


def _case_bed_unskipped_still_reports_itself():
    """THE REFUSED BEHAVIOUR, PINNED SO IT CANNOT COME BACK BY DEFAULT.

    The same seat with `skip` NOT passed is the state this world is in
    today: -1.150 m of penetration by its own frame.  Kept as a case
    because the fix is one keyword argument at one call site, and a
    silently dropped keyword would restore a refusal that fires on every
    world.  This case asserts the OLD number so the fix is observable.
    """
    I = A.instruments()
    pieces = _hall_floor(-113.36, 3456.12, y=232.258)
    bed = _piece("piece_bed02", -113.36, 232.303, 3456.119)
    grid = I.Grid(pieces + [bed], cell=4.0)
    fs = probe_seat("piece_bed02 unskipped", -113.36, 232.303, 3456.119,
                    232.258, grid, 0.49, 1.85, nearest_piece_m=0.0)
    pen = [f for f in fs if f.check == "seat_penetration"]
    assert pen and abs(pen[0].measured + 1.15) < 0.01, (
        "the unskipped probe must still report the bed's own 1.150 m half "
        f"extent, so the fix stays observable: {[f.message for f in fs]}")
    return fs


def _case_second_bed_in_the_same_bed_refuses():
    """SKIPPING IS BY IDENTITY, NOT BY PREFAB NAME, AND THIS PROVES IT.

    Two `piece_bed02` on the same pivot is a duplicate - the structures
    section refuses co-located pieces for the same reason - and a
    name-based skip would hide it while fixing the self-report.  The
    second bed is NOT in `skip`, so the probe must still refuse.
    """
    I = A.instruments()
    pieces = _hall_floor(-113.36, 3456.12, y=232.258)
    mine = _piece("piece_bed02", -113.36, 232.303, 3456.119)
    other = _piece("piece_bed02", -113.36, 232.303, 3456.119, yaw=90.0)
    grid = I.Grid(pieces + [mine, other], cell=4.0)
    fs = probe_seat("two beds one pivot", -113.36, 232.303, 3456.119,
                    232.258, grid, 0.49, 1.85, nearest_piece_m=0.0,
                    skip=[mine])
    assert any(f.check == "seat_penetration" for f in fs), (
        "a SECOND bed standing in this one must still bury the occupant: "
        f"{[f.message for f in fs]}")
    return fs


# ---------------------------------------------------------------------------
# THE BLIND QUANTITY: a bed IS a build piece, so a reach taken over every
# build piece is a measurement of the seat
# ---------------------------------------------------------------------------
def _reach_population(seat: tuple, pieces: list[tuple], exclude_self: bool
                      ) -> float:
    """`run()`'s reach arithmetic, in one place so the selftest exercises the
    SHIPPED rule and not a copy of it.  `pieces` is (name, x, y, z) rows."""
    n, sx, sy, sz = seat
    best = math.inf
    for pn, px, py, pz in pieces:
        if (exclude_self and pn == n and abs(px - sx) < 1e-3
                and abs(py - sy) < 1e-3 and abs(pz - sz) < 1e-3):
            continue
        best = min(best, math.hypot(px - sx, pz - sz))
    return best


def _case_reach_counted_the_seat_itself():
    """THE BLIND QUANTITY, PINNED.  MEASURED ON ULFSLAND: ALL EIGHTEEN
    claimable beds read `nearest_build_piece_m` 0.000.

    `seat_reach` asks "is there anything built within 50 m of this seat".
    A claimable bed is a BUILD PIECE, and the population it was minimised
    over was every build piece in the save - INCLUDING THE BED. Every bed
    therefore measured 0.000 m to the nearest built thing, which was
    itself, and `seat_reach` COULD NOT FIRE ON A BED IN ANY WORLD.  The
    only reason it looked alive is that `spawn_in_empty_forest` hands the
    probe a literal 4700.0 instead of a population.

    This is an ACCEPT case on purpose: it pins the QUANTITY, both ways.
    The bed alone in a forest is 4,700 m from the nearest other piece and
    0.000 m from itself, and the case fails if those two ever converge
    again.  Same family as `bed_does_not_bury_its_own_occupant`: the
    instrument must not answer a question about the seat with a
    measurement of the seat.
    """
    seat = ("bed", -4673.0, 73.0, -350.0)
    world = [seat, ("wood_floor", 1000.0, 40.0, 1300.0)]
    blind = _reach_population(seat, world, exclude_self=False)
    honest = _reach_population(seat, world, exclude_self=True)
    assert abs(blind) < 1e-9, (
        "the blind population must still read 0.000 m - the seat measuring "
        f"itself - or this case has stopped pinning anything: {blind}")
    assert honest > REACH_M, (
        "with the seat's own ZDO excluded by identity the same world must "
        f"read the real distance, not 0.000: {honest}")
    I = A.instruments()
    grid = I.Grid([], cell=4.0)
    fs = probe_seat("bed alone in a forest", -4673.0, 73.0, -350.0, 73.0,
                    grid, 0.49, 1.85, nearest_piece_m=honest)
    assert any(f.check == "seat_reach" for f in fs), (
        "a bed 4.7 km from anything else must be refused on reach once its "
        f"own piece is out of the population: {[f.message for f in fs]}")
    return [f for f in fs if f.check != "seat_reach"]


def _case_reach_blind_population_would_pass():
    """THE SAME WORLD JUDGED THE OLD WAY, so the fix is observable.

    A bed 4,700 m from the nearest other build piece, with the blind
    population, hands the probe 0.000 m and the seat sails through
    `seat_reach`.  The case is an ACCEPT case because the probe genuinely
    produces no refusal here - and that acceptance IS the defect, which is
    why the assertion below is what carries the meaning.
    """
    seat = ("bed", -4673.0, 73.0, -350.0)
    world = [seat, ("wood_floor", 1000.0, 40.0, 1300.0)]
    blind = _reach_population(seat, world, exclude_self=False)
    I = A.instruments()
    grid = I.Grid([], cell=4.0)
    fs = probe_seat("bed alone, blind reach", -4673.0, 73.0, -350.0, 73.0,
                    grid, 0.49, 1.85, nearest_piece_m=blind)
    assert not any(f.check == "seat_reach" for f in fs), (
        "this case exists to record that the blind population PASSES the "
        f"empty-forest seat; if it now refuses, delete the case: {fs}")
    return fs


# ---------------------------------------------------------------------------
# PRECISION: a solid known only to its bounding box must not decide a verdict
# ---------------------------------------------------------------------------
def _case_bounding_box_tree_does_not_refuse_a_seat():
    """MEASURED ON ULFSLAND: bed#8 at (-981.93, 1799.86) was refused
    `seat_penetration` '1.087 m INSIDE Beech1' - and the Beech1 in
    question stands at (-963.48, 1785.78), 23.2 m away.

    `Beech1` carries TWO MeshCollider rows and NO capsule at all, so the
    whole tree is recorded as its own 19.08 x 21.50 m prefab-local
    bounding box, 28-30 m once rotated.  The box is an UPPER BOUND on the
    tree, so a point inside it is not a point inside the tree, and a hard
    refusal from it is a verdict with worse precision than the judgement.
    It also blocked EVERY re-seat candidate within 12 m of bed#9 and
    bed#12, which is how a bounding box stops a world being fixed.

    The rule is a RE-RUN, not a reason string: measure again with the
    bounds dropped.  Here that leaves a clear floor, so the refusal must
    become an advisory that NAMES the box.
    """
    I = A.instruments()
    # THE ZDO'S OWN QUATERNION, read off the save, not a yaw typed here.
    # It matters: this Beech1 is tilted, and the tilt is what swings the
    # 19.08 x 21.50 m box out over a house 23.2 m away.  A case built with
    # yaw=0 would put the box nowhere near the seat and would pass for the
    # wrong reason.
    tree = I.mk_solid("Beech1", (-963.48, 33.831, 1785.777),
                      (-0.1702, 0.9706, 0.1372, 0.1009),
                      I.load_geometry()[0]["Beech1"])
    assert tree.bounds_only, (
        "this case is only meaningful while Beech1 has no tight collider; "
        f"n_tight={tree.n_tight}")
    assert A.hull_distance(tree, -981.927, 1799.861) < 0.0, (
        "the case must reproduce the state it is named for: the seat must "
        "be INSIDE the tree's bounding-box footprint, measured "
        f"{A.hull_distance(tree, -981.927, 1799.861):.3f} m")
    pieces = _hall_floor(-981.93, 1799.86, y=34.2)
    grid = I.Grid(pieces + [tree], cell=4.0)
    fs = probe_seat("bed#8", -981.927, 34.22, 1799.861, 34.2, grid,
                    0.49, 1.85, nearest_piece_m=0.5)
    hard = [f for f in fs if not f.ok and not f.advisory]
    assert not hard, (
        "a tree recorded only as a 19 x 21 m bounding box, pivot 23.2 m "
        f"away, must not hard-refuse a seat: {[f.message for f in hard]}")
    assert any(f.advisory and "Beech1" in f.message for f in fs), (
        "and it must not silently pass either - the bounding box has to be "
        f"named: {[f.message for f in fs]}")
    return hard


def _case_tight_solid_still_refuses_a_seat():
    """THE OTHER HALF, so the precision rule cannot become an amnesty.

    `FirTree` has a real 0.8 m trunk CAPSULE as well as a canopy bounds
    row, so a seat at its trunk is refused by a TIGHT reading and the
    re-run changes nothing.  Without this case the fix above reads as
    "ignore anything that is not a build piece", which would let a tree
    standing in a bedroom pass.
    """
    I = A.instruments()
    trunk = _piece("FirTree", 0.0, 10.0, 0.0)
    assert not trunk.bounds_only and trunk.n_tight, (
        f"FirTree must still carry a tight trunk: n_tight={trunk.n_tight}")
    pieces = _hall_floor(0.0, 0.0, y=10.0)
    grid = I.Grid(pieces + [trunk], cell=4.0)
    fs = probe_seat("seat at a fir trunk", 0.0, 10.0, 0.0, 9.0, grid,
                    0.49, 1.85, nearest_piece_m=0.5)
    assert any(f.check in ("seat_penetration", "seat_clearance")
               and not f.advisory for f in fs), (
        "a 0.8 m trunk capsule under the seat is a MEASURED shape and must "
        f"still refuse: {[f.message for f in fs]}")
    return fs


CASES = [
    Case("clear_hall_floor", False, _case_clear_hall_floor,
         prior_state="the seat the hall offers today: 3.000 m clearance "
                     "(capped), 6.000 m headroom, 0.9 m drop"),
    Case("seat_in_an_arch_bay", True, _case_seat_in_an_arch_bay,
         prior_state="the u-stenvik arch cell, at its measured world "
                     "position and yaw"),
    Case("seat_in_a_pillar_cell", True, _case_seat_in_a_pillar_cell,
         prior_state="the hall's Piece_flametal_pillar at "
                     "(-294.04, 218.99)"),
    Case("spawn_in_empty_forest", True, _case_spawn_in_empty_forest,
         prior_state="this world's spawn pointed at (-4673, 73, -350) on "
                     "the strength of a plan; a live census found 0 of every "
                     "built thing and 826 natural objects"),
    Case("seat_under_a_beam", True, _case_seat_under_a_beam),
    Case("a_bed_under_our_own_beam_is_factory_work", False,
         _case_a_bed_under_our_own_beam_is_factory_work,
         prior_state="drive 3: 7 beds under a wood_beam of our own "
                     "town_body plans, 14 seat_* rows classed 1D",
         neuter=T.neuter_blocker_ownership("blind"),
         neuters="ownership blind: every blocker an operator's"),
    Case("seat_over_a_drop", True, _case_seat_over_a_drop),
    Case("seat_with_no_surface", True, _case_no_surface_at_all),
    Case("bed_does_not_bury_its_own_occupant", False,
         _case_bed_does_not_bury_its_own_occupant,
         prior_state="live on Ulfsland: piece_bed02#17 at "
                     "(-113.36, 3456.12) refused as '1.150 m INSIDE "
                     "piece_bed02' - its own x half-extent - and its "
                     "headroom capped by its own 1.455 m headboard"),
    Case("bed_unskipped_still_reports_itself", True,
         _case_bed_unskipped_still_reports_itself,
         prior_state="the same seat with the seat's own solid NOT skipped, "
                     "pinned so one dropped keyword cannot quietly restore "
                     "a refusal that fires on every world"),
    Case("second_bed_in_the_same_bed_refuses", True,
         _case_second_bed_in_the_same_bed_refuses,
         prior_state="skipping by PREFAB NAME instead of by identity would "
                     "hide a duplicate bed while fixing the self-report"),
    Case("reach_counted_the_seat_itself", False,
         _case_reach_counted_the_seat_itself,
         prior_state="live on Ulfsland: ALL 18 claimable beds read "
                     "nearest_build_piece_m 0.000 because a bed is a build "
                     "piece and was in its own reach population - the "
                     "check could not fire on a bed in any world"),
    Case("reach_blind_population_would_pass", False,
         _case_reach_blind_population_would_pass,
         prior_state="the same bed, 4,700 m from the nearest other piece, "
                     "PASSING seat_reach on the blind population - the "
                     "acceptance that is the defect"),
    Case("bounding_box_tree_does_not_refuse_a_seat", False,
         _case_bounding_box_tree_does_not_refuse_a_seat,
         prior_state="live on Ulfsland: bed#8 refused '1.087 m INSIDE "
                     "Beech1' by a tree 23.2 m away, because Beech1 is two "
                     "MeshCollider bounds and no capsule - a 19.08 x 21.50 "
                     "m prefab-local box"),
    Case("tight_solid_still_refuses_a_seat", True,
         _case_tight_solid_still_refuses_a_seat,
         prior_state="the other half of the precision rule: FirTree's own "
                     "0.8 m trunk CAPSULE is a measured shape and must "
                     "still refuse, so the fix cannot become an amnesty"),
]

register(Section(
    name="spawn",
    measures=("every seat the world can deliver a player to - the start "
              "spawn and every claimable bed - offers the player's own "
              "measured capsule room to stand, height to stand up in, a "
              "survivable drop onto a real surface, and something built "
              "within reach"),
    instrument=("the seed's location dump for the StartTemple entry and the "
                "save's own LocationProxy ZDO for where it actually stands; "
                "the object census plus each prefab's rotated solid for "
                "clearance, headroom and penetration; run_waterproof.sh "
                "FROM_SAVE=1 Heightmap.GetHeight for the ground under each "
                "seat; the Player prefab's own capsule for the walker"),
    population=("the world's StartTemple spawn plus every claimable bed ZDO "
                "in the save (bed, piece_bed02); goblin_bed and "
                "dvergrprops_bed are location dressing, are excluded by "
                "name, and the excluded count is printed"),
    run=run,
    cases=CASES))
