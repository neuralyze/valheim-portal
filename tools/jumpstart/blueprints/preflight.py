#!/usr/bin/env python3
"""THE EMITTER'S OWN ACCEPTANCE PASS - the gate's questions, asked BEFORE
the world is written instead of after.

WHY THIS FILE EXISTS
====================
Every structures defect this world shipped was DECLARED CORRECT by something.
Three of them are properties of the BLUEPRINT ITSELF and were therefore
already decided before a single `spawn_object` left the socket:

  GROUNDING   `hs_meadows_stone_manor.blueprint` carries a 20-slab stone
              plinth at local y -2.0..-1.0 and its ENTIRE timber storey at
              local -0.2 and above, on the SAME 20 xz positions.  MEASURED
              from the file: the plinth top is -1.000 and the lowest timber
              solid (`wood_beam`) is -0.200, so the storey stands 0.800 m
              clear of the only thing under it, against a 0.781 m limit, and
              the door sill stands 0.990 m above the plinth - THE BUILDING
              CANNOT BE WALKED INTO.  `floor_datum` reported no violation,
              because `BaseProfile` is a SILHOUETTE: it keeps the LOWEST
              solid per column, so the plinth hid the void above it and the
              body read as fully grounded.  A silhouette cannot see an
              INTERNAL void, and this is the check that can.

  ORIENTATION the same three blueprints carry gable-end `wood_wall_roof`
              triangles rotated 180 degrees from the slope they close: at
              local z=0 all four rise toward -x while the two courses at
              x=1,3 rise toward +x.  MEASURED: manor 4 inverted of 28
              checked, house 2 of 12, cottage 2 of 10 - EXACTLY the counts
              the gate found in the world.  A 180 degree yaw moves neither
              the footprint nor the AABB, so the prefab census, the position
              census at tolerance 0 and the plane-coverage test all read
              identical either way.  Only the SIGNED slope sees it.

  DUPLICATES two rows of the same prefab at the same pivot are two
              `spawn_object` lines and two ZDOs, and the second one is
              invisible.

WHAT IT REUSES AND WHY IT REUSES IT
===================================
Nothing here is a second implementation.  `accept/instruments.py` and
`accept/sections/structures.py` are the authority for these three questions
and this module calls THEM, with the same constants and the same verdict
function, so a blueprint that passes here and a world that passes the gate
are answering to one definition apiece.  A second census has cost this
project twice; a second DEFINITION would cost it worse, because the emitter
and the gate would disagree and both would be sincere.

THE PAD IS THE IDEALISATION AND IT IS THE RIGHT ONE
===================================================
`--align floor-center` exists to put a body on a LEVELLED PAD at its own
floor datum.  So the grounding question the emitter can answer honestly is
the gate's question asked over a PERFECTLY FLAT ground at `base_y`: every
column reads the same height, which is the best case the placement can
possibly get.  A body that floats on a dead-flat pad floats on every pad.
The check is therefore a LOWER BOUND on the defect and never a false
refusal for terrain reasons - real terrain can only make it worse.
"""
from __future__ import annotations

import collections
import copy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
if str(JUMPSTART) not in sys.path:
    sys.path.insert(0, str(JUMPSTART))

from accept import instruments as I                       # noqa: E402
from accept.sections import _approach as A                # noqa: E402
from accept.sections import spawn as SPAWN                # noqa: E402
from accept.sections import structures as ST              # noqa: E402
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import reseat as RS                                       # noqa: E402

# One body of one or two pieces is a bench or a sign post; the grounding
# question is about BUILDINGS.  Same bound the gate uses, imported rather
# than restated.
MIN_BODY_PIECES = ST.MIN_BODY_PIECES
SUPPORT_LIMIT_M = ST.SUPPORT_LIMIT_M
DUP_TOL_M = ST.DUP_TOL_M
_CORPUS: list = []


def _gate_corpus() -> tuple[dict[str, list], set[str]]:
    """`accept.instruments.load_geometry()`, read once and HELD for the life
    of the process - see `preflight` for why holding it is load-bearing."""
    if not _CORPUS:
        _CORPUS.append(I.load_geometry())
    return _CORPUS[0]


class Refusal:
    """One measured reason not to emit this blueprint.

    `code` is stable and greppable, `detail` carries the numbers, and
    `override` names the exact flag that would let it through - because a
    refusal a caller cannot act on is noise, and an override nobody can find
    becomes `--force`.
    """

    __slots__ = ("code", "detail", "override", "numbers")

    def __init__(self, code: str, detail: str, override: str, **numbers):
        self.code = code
        self.detail = detail
        self.override = override
        self.numbers = numbers

    def __str__(self) -> str:
        return f"  {self.code}: {self.detail}"


def solids_of(objs, geom: dict[str, list], build: set[str] | None = None):
    """Blueprint rows as PLACED SOLIDS, in blueprint-local coordinates.

    Rows whose prefab has no measured solid are dropped and COUNTED, never
    guessed at: a piece with no geometry cannot answer a geometric question
    and pretending it is a point would answer a different one.

    The ROW INDEX travels with each solid, because a repair has to name the
    row it moves and `objs` is what the emitter will write.
    """
    out, rows, unmeasured = [], [], []
    for i, o in enumerate(objs):
        parts = geom.get(o.prefab)
        if not parts:
            unmeasured.append(o.prefab)
            continue
        if build is not None and o.prefab not in build:
            continue
        out.append(I.mk_solid(o.prefab, tuple(float(q) for q in o.pos),
                              tuple(float(q) for q in o.rot), parts))
        rows.append(i)
    return out, rows, unmeasured


def flat_ground(solids, base_y: float) -> dict:
    """The gate's ground dictionary for a DEAD FLAT pad at `base_y`.

    Built from the same `piece_columns` / `gkey` pair the section uses, so
    the keys are the ones `check_grounding` will look up.  Any miss would
    silently become an `unmeasured_pieces` count rather than a refusal, and
    that is the failure mode this project has already paid for twice.
    """
    g = {}
    for s in solids:
        for (x, z) in ST.piece_columns(s):
            g[ST.gkey(x, z)] = base_y
    return g


def check_support(solids, base_y: float, geom: dict[str, list]
                  ) -> list[Refusal]:
    """Does every body in this blueprint reach the pad it is placed on?

    THE BODY IS THE UNIT, not the blueprint.  `hs_meadows_stone_manor` is one
    file and TWO bodies: a 38-piece stone plinth that touches the pad and a
    114-piece timber house 0.800 m above it, connected to nothing.  Splitting
    on the gate's own 0.35 m slack is what makes that visible; a whole-file
    minimum is the silhouette again and reports 0.000 m.
    """
    if not solids:
        return []
    bodies = [b for b in I.bodies(solids, slack=0.35)
              if len(b) >= MIN_BODY_PIECES]
    if not bodies:
        return []
    ground = flat_ground(solids, base_y)
    named = [(b, [], f"body-{i + 1}") for i, b in enumerate(bodies)]
    out: list[Refusal] = []
    for f in ST.check_grounding(named, ground, ST.Columns(geom), geom):
        if f.check != "grounding" or f.ok:
            continue
        n = f.numbers
        out.append(Refusal(
            "unsupported_body",
            f"{n['pieces']} of this blueprint's pieces form a body whose "
            f"best-supported piece ({n.get('lowest_piece')}) stands "
            f"{n.get('support_gap_m')} m clear of a DEAD FLAT pad at "
            f"base_y {base_y:+.3f}, against the "
            f"{SUPPORT_LIMIT_M:.3f} m limit (tan 38 deg, "
            f"Character::c_MinSlideDegreesPlayer). On real ground it can "
            f"only be worse. Nothing in this file is under it.",
            "--allow-unsupported-bodies",
            pieces=n["pieces"], support_gap_m=n.get("support_gap_m"),
            lowest_piece=n.get("lowest_piece"), base_y=round(base_y, 3)))
    return out


# ---------------------------------------------------------------------------
# SUB-GRADE ROWS, AND WHAT "WHOLLY UNDER THE PAD" IS ALLOWED TO DEPEND ON.
#
# THE SHIPPED QUESTION READ A KEY THAT NAMED A SUBSET OF ITS DEPENDENCIES.
# The top of a row was the top of whatever CROSS-SECTION a 1 m lattice column
# cut through it - for a piece that spans height between two lattice lines,
# that is its foot, its threshold sill or the low end of its diagonal.  The
# gate had already retired exactly that arithmetic (`structures.
# _BURIED_PER_COLUMN`: two false `buried` refusals on Ulfsland, a DOOR 1.911 m
# in the air and a 45 degree beam 0.536 m proud) while this module kept a
# private copy of it, so the REPAIR deleted what the GATE would have kept.
# MEASURED 2026-09-24 over all 528 resolvable library bodies, seat repair
# applied, dead flat pad at each body's own datum:
#
#   shipped cross-section rule    15,887 rows in 272 bodies "wholly under"
#   the gate's whole-solid rule   14,764 rows in 227 bodies
#   dropped by the first and NOT buried by the second: 1,124 rows in 159
#   bodies, 941 of them with their OWN TOP ABOVE THE PAD, in 134 bodies,
#   up to 33.588 m above it (`Piece_grausten_pillarbeam_medium` 117,
#   `wood_beam_26` 96, `wood_beam_45` 69, `wood_stepladder` 47, ...)
#
# and on Fresh42's own 17 bodies, 19 VISIBLE rows the selection gate's
# repair removed: 18 `woodiron_beam_45` standing 0.536 m proud at
# sudrberg-keep and one `stake_wall` 1.297 m proud at vestvik-workshop-1,
# plus 11 more whose own top is inside the gate's tolerance.
#
# So a row is wholly under the pad when - and ONLY when - these hold, and
# the drop of it depends on one thing more:
SUBGRADE_DEPENDS_ON = (
    # its OWN WHOLE-SOLID top - every collider part, `Solid.ymax` - never
    # the top of the slice a lattice column happens to cut
    "own_top",
    # is under the LOWEST pad reading under EVERY lattice column of its own
    # footprint (dead flat in the emitter; `ground` is a parameter so a real
    # pad can be handed in and is judged by the same code)
    "lowest_pad",
    # by more than the gate's own `BURIED_TOL_M`
    "buried_tol",
    # judged ONE ROW AT A TIME - never its body, its course or its prefab
    "row",
    # and no KEPT row it touches is left without the pad under it: the pad
    # must take over every load the drop removes.  "Reaches the pad" is the
    # exact mirror of "wholly under it" - the kept row's OWN lowest point
    # against the LOWEST pad under it - and never a lattice cross-section,
    # which on Fresh42's `creator-looney-swamp-starter-home` read five
    # `wood_wall_roof_top_45` braces whose feet are in the ground as
    # standing 0.140-0.434 m clear of it and refused a sound body
    "support",
)
# A kept row within this distance of a dropped row is RESTING ON IT.  It is
# the burial tolerance ON PURPOSE: a dropped row's own top is more than
# BURIED_TOL_M under the lowest pad beneath it, so whatever touches it within
# that distance is itself under the pad there and the ground carries it.  The
# guard is therefore SILENT while the burial verdict is right and LOUD the
# moment it is not - the selftest shows it refusing under the shipped
# cross-section key - and on a pad that is not flat it refuses a row that
# holds something up over lower ground.
SUBGRADE_CONTACT_M = ST.BURIED_TOL_M


def _subgrade_units(solids, rows):
    """THE UNIT OF JUDGEMENT: ONE ROW, ALONE.

    A function rather than an inline loop so the selftest can put a SUPERSET
    key back - the row's BODY - and show what it costs: a skirt joined to the
    house it holds up is never "wholly under" as a body, so nothing is
    dropped, nothing is refused, and the skirt is emitted unseen.
    """
    return [([s], [r]) for s, r in zip(solids, rows)]


def subgrade_rows(solids, rows, ground: dict, geom: dict[str, list]
                  ) -> list[dict]:
    """Which rows of this blueprint lie COMPLETELY under the pad - top and all.

    NOT A SECOND IMPLEMENTATION.  Each row is handed to the gate's own
    `check_grounding` as a body of one, and it is sub-grade exactly when the
    gate says `buried`: its own top under the lowest ground anywhere under it
    by more than `BURIED_TOL_M`.  A row the gate cannot read ground for is
    KEPT - unread is not buried - and so is every row whose prefab has no
    measured solid, because it never became a solid to ask about.

    WHY A FLOOR-CENTRE PLACEMENT BURIES THINGS.  `--align floor-center` seats
    the blueprint's WALKABLE FLOOR on the pad (`base_geometry.floor_datum`),
    and a pad is FLAT GROUND, not an excavation - so every authored course
    below that floor plane ends up below the terrain.  MEASURED on Ulfsland,
    where the acceptance gate refused 45 bodies over 414 such pieces, and on
    Fresh42's 17 bodies, where 5 carry one (163 rows):

      hs_meadows_madzobuild_modernstarterhouse  a raised FOUNDATION: 32
          `wood_beam` sill plates at local -0.201..0.199 and 32
          `wood_wall_half` at -0.001..0.999 under the edges and bearing lines
          of a floor at 0.967..1.096 that RESTS ON THEM, 4 `wood_pole` and 4
          `wood_pole2` posts, and the LOWER flight of both entrance stairs
          (3 `wood_stair` to 0.047).  75 rows; every one of the 132 kept rows
          they touch has its own underside under the pad, so the pad carries
          all of it.  The UPPER flight (3 `wood_stair`, own top 1.047) reaches
          the floor and is kept: 0.049 m under is inside the gate's tolerance.
      hs_meadows_stilt_house          8 `wood_pole2` stilts
      hs_mistlands_sheepshank_trophytowerv2  39 rows of a basement storey
      creator-looney-swamp-starter-home      32
      hs_meadows_aodi_the_hive_1_0            9

    Returned with the row index, the prefab, the local position, the row's
    own top and its depth, so the emitter can name every row it drops.
    """
    cols = ST.Columns(geom)
    out = []
    for unit, unit_rows in _subgrade_units(solids, rows):
        buried = None
        for f in ST.check_grounding([(unit, [], "row")], ground, cols, geom):
            if f.check == "buried":
                buried = f
        if buried is None or buried.numbers["buried_pieces"] < len(unit):
            continue
        for s, r in zip(unit, unit_rows):
            out.append(dict(row=r, prefab=s.name,
                            depth_m=buried.numbers["worst_m"],
                            own_top=round(s.ymax, 4),
                            local=[round(v, 4) for v in s.pos]))
    out.sort(key=lambda r: (-r["depth_m"], r["row"]))
    return out


def _carried_clearance(k, ground: dict) -> float | None:
    """How far a kept row's OWN lowest point stands above the LOWEST pad
    reading anywhere under it; None when there is no reading at all.

    The mirror of the burial test and for the same reason: a solid is
    touching the ground when its own lowest point is at or under the lowest
    ground beneath it, wherever between the lattice lines that point is.
    """
    gs = [g for g in (ground.get(ST.gkey(x, z)) for (x, z) in
                      ST.piece_columns(k)) if g is not None]
    return None if not gs else k.ymin - min(gs)


def subgrade_carried(solids, rows, plan: list[dict], ground: dict
                     ) -> list[dict]:
    """Which KEPT rows would lose what holds them up if `plan` were dropped.

    "A stair that reaches the floor is not a skirt."  A sub-grade row is only
    droppable while the pad does its job for it: every kept row RESTING ON IT
    - solid within `SUBGRADE_CONTACT_M` - must reach the pad ITSELF, its own
    lowest point at or under the lowest ground beneath it.  A kept row that
    does not is being carried by the row the drop would take, and the drop is
    refused rather than leave it in the air.  A kept row with no ground
    reading at all is counted as NOT reaching: unread is not clear.
    """
    gone = {r["row"] for r in plan}
    if not gone:
        return []
    kept = [(s, r) for s, r in zip(solids, rows) if r not in gone]
    row_of = {id(s): r for s, r in kept}
    grid = I.Grid([s for s, _ in kept], cell=4.0)
    tol = SUBGRADE_CONTACT_M
    reach: dict[int, float | None] = {}
    out = []
    for d, dr in zip(solids, rows):
        if dr not in gone:
            continue
        for k in grid.near((d.xmin + d.xmax) / 2, (d.zmin + d.zmax) / 2,
                           max(d.xmax - d.xmin, d.zmax - d.zmin) / 2 + tol):
            if not (d.xmin - tol <= k.xmax and k.xmin - tol <= d.xmax
                    and d.zmin - tol <= k.zmax and k.zmin - tol <= d.zmax
                    and d.ymin - tol <= k.ymax and k.ymin - tol <= d.ymax):
                continue
            if id(k) not in reach:
                reach[id(k)] = _carried_clearance(k, ground)
            c = reach[id(k)]
            if c is not None and c <= tol:
                continue
            out.append(dict(row=dr, prefab=d.name, carries_row=row_of[id(k)],
                            carries=k.name,
                            clearance_m=None if c is None else round(c, 4)))
    out.sort(key=lambda r: (r["row"], r["carries_row"]))
    return out


def check_subgrade(plan: list[dict], carried: list[dict], base_y: float
                   ) -> list[Refusal]:
    """A row that will be invisible the moment it is placed, and a drop that
    would take away what holds a visible row up."""
    out: list[Refusal] = []
    if plan:
        by = collections.Counter(r["prefab"] for r in plan)
        worst = plan[0]
        out.append(Refusal(
            "subgrade_rows",
            f"{len(plan)} row(s) of this blueprint lie COMPLETELY under a "
            f"DEAD FLAT pad at base_y {base_y:+.3f} - own top and all - so "
            f"every one of them is a `spawn_object`, a ZDO and an acceptance "
            f"refusal that nobody can see. Worst {worst['prefab']} "
            f"{worst['depth_m']:.3f} m under, own top at local "
            f"{worst['own_top']:+.4f}. By prefab: "
            + ", ".join(f"{k} x{v}" for k, v in by.most_common(6))
            + ". `--align floor-center` seats the WALKABLE FLOOR on the pad "
            "and a pad is flat ground, not an excavation, so an authored "
            "sub-floor course has nowhere to go. The emitter DROPS exactly "
            "these rows by default and names every one; this refusal standing "
            "means the drop was switched off (--no-drop-subgrade-rows) or "
            "refused (subgrade_structural). Overriding emits them.",
            "--allow-subgrade-rows",
            subgrade_rows=len(plan), worst_m=worst["depth_m"],
            worst_prefab=worst["prefab"], base_y=round(base_y, 3),
            by_prefab=dict(by.most_common(8))))
    if carried:
        pairs = sorted({(c["row"], c["prefab"], c["carries_row"],
                         c["carries"], c["clearance_m"]) for c in carried})
        out.append(Refusal(
            "subgrade_structural",
            f"{len({p[0] for p in pairs})} sub-grade row(s) CARRY "
            f"{len({p[2] for p in pairs})} kept row(s) that do not reach the "
            f"pad themselves, so they are not a skirt - they hold up "
            f"something a walker can see - and the drop is REFUSED: "
            + "; ".join(f"row {a} {b} carries row {c} {d} "
                        f"({'no ground reading' if e is None else f'{e:.3f} m clear of the pad'})"
                        for a, b, c, d, e in pairs)
            + ". Overriding emits every row as authored.",
            "--allow-subgrade-rows",
            carried=len(pairs), base_y=round(base_y, 3)))
    return out


# ---------------------------------------------------------------------------
# THE REPAIRS.  Detection alone turns a defective blueprint into NO
# building, which is a worse world than a wrong one and not what the gate is
# for.  Every repair here - seat, upright, reorient, co-located copies and
# the sub-grade drop - is DETERMINISTIC, is derived from the same
# measurement that raised the refusal, and is reported piece by piece - so
# the emitter can hand a future world a building that stands and a roof that
# sheds, out of the same third-party file.  `shelter_plan` is computed and
# NOT applied; its own docstring says why.
#
# AND THE EMITTER APPLIES THEM BY DEFAULT.  They were opt-in flags, and the
# selection gate (`settlements/spawnable.py`) passed them while the
# orchestrated build (`settlements/build.py::spawn_plan_file`) did not.
# MEASURED 2026-09-24 over Fresh42's 43 placed units: with the flags 43 of
# 43 emit, without them 31 of 43 REFUSE (subgrade_rows 9, inverted_panels
# 26, unsupported_body 5) - the gate certified a configuration step 5 never
# ran.  A repair that needs a human to remember a flag is not a repair on a
# fresh seed; `--no-...` exists to reproduce the unrepaired emitter.
#
# AND "OUT OF THE SAME THIRD-PARTY FILE" IS WHY A REPAIR LIVES HERE.  The
# bodies this project builds from are VENDORED: `library/staging/` is
# gitignored, `web.py --fetch` refills it from pinned commits, and
# `materialise.py --verify` hashes every one of them against
# `library_manifest.json`.  MEASURED 2026-09-22: appending 18 panels to
# `hs_plains_ulf_the_builder_lighthouse.blueprint` to close its apex took
# that verify from "528 verified, 0 drifted" to "527 verified, 1 DRIFTED",
# and one `--fetch` would have erased the edit.  A repair written into a
# vendored body is not inherited by anything; a repair written HERE is
# applied to every body, every seed, every time.  And the population was
# never one file: `check_shelter`'s own docstring records 12 shelter
# refusals over SIX bodies emitted from TWO files besides the lighthouse.
# ---------------------------------------------------------------------------
def seat_plan(solids, rows, base_y: float, geom: dict[str, list]
              ) -> list[dict]:
    """How far each unsupported body must come DOWN to land on what is
    beneath it, and which rows move.

    THE DROP IS THE MEASURED GAP ITSELF.  `check_grounding`'s
    `support_gap_m` is `min over the body's pieces and their own lattice
    columns of (piece underside - ground)`; lowering the body by exactly
    that number brings its FIRST point of contact to zero and no point
    below zero.  Any larger drop drives the body into the thing under it;
    any smaller leaves it in the air.  MEASURED on
    `hs_meadows_stone_manor.blueprint`: the timber body's minimum is the
    `wood_beam` joists at 0.800 m, not the `wood_floor` at 0.967 m, so the
    joists land on the plinth and the floor sits its own 0.167 m above
    them - the internal geometry of the body is untouched, which is the
    whole point of moving a BODY and not a piece.

    The ground used is the same DEAD FLAT pad the check refused against,
    re-read after each body moves so a body seated onto another body's new
    top is measured against where that top now is.
    """
    if not solids:
        return []
    cols = ST.Columns(geom)
    ground = flat_ground(solids, base_y)
    bodies = [b for b in I.bodies(solids, slack=0.35)
              if len(b) >= MIN_BODY_PIECES]
    row_of = {id(s): r for s, r in zip(solids, rows)}
    out = []
    for body in bodies:
        gap = None
        low = None
        for s in body:
            for (x, z) in ST.piece_columns(s):
                g = ground.get(ST.gkey(x, z))
                if g is None:
                    continue
                sp = cols.span(s, x, z)
                lo = s.ymin if sp is None else sp[0]
                c = lo - g
                if gap is None or c < gap:
                    gap, low = c, s
        if gap is None or gap <= SUPPORT_LIMIT_M:
            continue
        out.append({
            "drop_m": round(gap, 4),
            "pieces": len(body),
            "lowest_piece": low.name,
            "rows": sorted(row_of[id(s)] for s in body),
        })
    return out


def upright_plan(solids, rows, geom: dict[str, list]) -> list[dict]:
    """Which rows must be yawed 180 degrees to rise toward their own ridge.

    A 180 degree yaw about the piece's OWN pivot is the only correction
    available - Valheim cannot mirror a piece - and it is exactly the
    operation that inverted the panel in the first place, so applying it
    again is the inverse and nothing else moves.  The pivot is unchanged by
    construction.

    THE REPAIR'S POPULATION MUST BE THE CHECK'S POPULATION, AND IT WAS NOT.
    This function used to partition `I.bodies(SOLIDS, slack=1.2)` - every
    solid, walls and floors included - and take the roof pieces out of each
    cluster afterwards, while `I.check_orientation` partitions
    `bodies(ROOFS, slack=1.2)`, roof-class solids alone.  On a building with
    ONE roof the two clusterings coincide and the repair converged:
    crimsonchaostownhall 26 -> 0, villa 6 -> 0, manor 4 -> 0, cottage /
    hut / stone_house / stilt_house 2 -> 0, log_cabin 10 -> 0.  On a file
    whose roofs form FOUR separate bodies they do not, and every panel was
    then measured against another wing's ridge.  MEASURED on
    `hs_ashlands_swampytreehouse.blueprint`, from the file alone:

      before   17 inverted of 111 checked
      plan     13 rows proposed for a flip
      after    16 inverted of 111, and the plan is then EMPTY

    - so the repair declared itself FINISHED having cured one panel of
    seventeen, and this docstring claimed `check_orientation` re-run over the
    result reports zero.  It reported sixteen.  A repair that derives its own
    population answers a different question from the check that will judge
    it, which is the same defect the gate exists to catch, one layer down.

    AND THE COPY IS NOW GONE, BECAUSE "RESTATED NOWHERE" WAS NOT TRUE.  This
    function used to re-type the roof set, the body partition, the ridge
    rule and both skips, and claim by comment that they were the
    instrument's.  They were a SECOND IMPLEMENTATION of them, and the day
    `check_orientation` stopped choosing one ridge point by a distance that
    ties on a symmetric roof, this copy would have gone on proposing 180
    degree flips for 14 panels the check calls correct - which is the same
    failure that rotated ten sound panels on a clean roof and signed it as
    a repair.  `check_orientation` now PUBLISHES the rows it counts as
    inverted (`inverted_at`, keyed by prefab and full 3D position, which is
    the key the retires use because ZDO ids are reassigned on load) and this
    function does nothing but map them back to blueprint rows.  There is one
    implementation and one population.

    AND THE MAPPING BACK WAS ITSELF A KEY THAT NAMED A SUBSET.  The instrument
    publishes `pos` rounded to 4 decimals; this function re-rounded that to 3
    and compared it with the solid's own position rounded to 3 once.  Two
    roundings are not one: 23.91052 publishes as 23.9105, which re-rounds to
    23.91, while the solid rounds to 23.911.  MEASURED on Fresh42's
    `pre-elder/blackforest-walled-base` (`fox-0123456.blueprint`): the check
    named 35 inverted rows, this plan returned 31, the emitter yawed 31 and
    then refused the body for the 4 it had never been handed - `wood_roof` at
    (23.91,7.00,12.72), `wood_wall_roof_upsidedown` at (29.52,3.75,15.81),
    `wood_wall_roof` at (24.29,7.00,13.65) and (28.12,7.00,22.88).  The key
    is now `UPRIGHT_KEY_DEPENDS_ON`, read at the instrument's own precision.

    AND A POSITION IS NOT A ROW WHEN TWO ROWS SHARE IT.  The published key
    carries no rotation, and two roofs meeting in a valley share a pivot at
    opposite yaw (`structures.check_duplicates`: 417 such pairs on
    Ulfsland).  Where a key holds more solids than the instrument published
    rows for, each one is yawed ALONE and kept only if the instrument's own
    inverted count falls - the check picks the row, never the key.

    AN UPSIDE-DOWN ROW IS NOT YAWED.  A yaw cannot change which way is up; it
    only reverses the slope, so a panel lying face-down in the right plane
    comes out of it face-down in the wrong one.  Those rows belong to
    `reorient_plan`.
    """
    if not solids:
        return []
    row_of = {id(s): r for s, r in zip(solids, rows)}
    at: dict[tuple, list] = {}
    for s in solids:
        at.setdefault(_published_key(s.name, s.pos), []).append(s)
    res = I.check_orientation(solids, geom)
    published: dict[tuple, list[dict]] = {}
    for r in res["inverted_at"]:
        if r.get("upside_down"):
            continue
        published.setdefault(_published_key(r["prefab"], r["pos"]),
                             []).append(r)
    out = []
    for key, entries in published.items():
        here = at.get(key, [])
        if len(here) > len(entries):
            here = _disambiguate_yaw(solids, here, geom, res["inverted"])
        for s, r in zip(here, entries):
            out.append({"row": row_of[id(s)], "prefab": s.name,
                        "toward": r["margin_m"],
                        "at": [round(s.pos[0], 3), round(s.pos[1], 3),
                               round(s.pos[2], 3)]})
    out.sort(key=lambda r: r["row"])
    return out


# THE KEY A PUBLISHED ORIENTATION ROW IS MATCHED BY, AND EVERYTHING IT
# DEPENDS ON.  The instrument's `inverted_at` names a row by prefab and full
# 3D position, rounded to PUBLISHED_POS_DECIMALS, because ZDO ids are
# reassigned on load.  A blueprint solid is keyed by the SAME rounding of its
# OWN position, once - so the two sides are one float operation on one float,
# never a rounding of a rounding.  Rotation is NOT in the published key, so a
# key holding more solids than published rows is resolved by the instrument
# (`_disambiguate_yaw`), not by the key.
PUBLISHED_POS_DECIMALS = 4
UPRIGHT_KEY_DEPENDS_ON = (
    "prefab",
    f"position x, y and z, each rounded ONCE to {PUBLISHED_POS_DECIMALS} "
    "decimals - the precision `check_orientation` publishes `inverted_at` at",
    "for a key two solids share (a valley pair on one pivot), the instrument's "
    "own inverted count with each of them yawed alone",
)


def _published_key(name: str, pos) -> tuple:
    """`UPRIGHT_KEY_DEPENDS_ON`, applied identically to a published row and to
    a solid.  Rounding is idempotent at one precision, so the published side
    (already rounded by the instrument) and the solid side agree exactly."""
    d = PUBLISHED_POS_DECIMALS
    return (name, round(float(pos[0]), d), round(float(pos[1]), d),
            round(float(pos[2]), d))


def _yawed(s, geom: dict[str, list]):
    """The same row yawed 180 degrees about its own pivot, as a solid - the
    emitter's `qmul(yaw_quat(180), rot)`."""
    return I.mk_solid(s.name, s.pos, I.qmul(I.yaw(180.0), s.quat),
                      geom[s.name])


def _disambiguate_yaw(solids, here, geom: dict[str, list], inverted: int):
    """Which of several solids sharing one published key the yaw CURES,
    asked of the instrument one solid at a time."""
    picked = []
    for s in here:
        trial = [(_yawed(t, geom) if t is s else t) for t in solids]
        if I.check_orientation(trial, geom)["inverted"] < inverted:
            picked.append(s)
    return picked


def shelter_plan(solids, rows, geom: dict[str, list]) -> list[dict]:
    """Which panels must be ADDED to close every hole `check_shelter` names.

    ONE PROPOSAL PER ENCLOSED-AND-OPEN DECK COLUMN, and the columns are
    `check_shelter`'s own published `holes_at` - not a second walk of the
    eight rays, not a re-derived roof line.  `upright_plan` is the whole
    argument for that: it derived its own population, agreed with the check
    on single-roof buildings, and on a four-wing roof proposed 13 flips for
    17 inverted panels and left 16.  A repair that derives its own
    population answers a different question from the check that judges it.

    AND THE POPULATION MUST BE WHOLE, NOT A SAMPLE.  `holes_at` was capped
    at 12 rows while it was only a display field; it is now complete,
    because a repair fed 12 of 36 holes closes 12, proposes nothing further
    and reports itself finished - the identical shape, one layer down.

    THE PANEL IS THE BODY'S OWN, NEVER A CATALOGUE CHOICE.  For each hole
    the donor is the roof piece of THAT body whose pivot is nearest the open
    column in xz; the proposal is one copy of the donor - same prefab, same
    course height, same rotation - slid horizontally so its pivot sits over
    the column.  Nothing is invented: if a body has a roof at all it has a
    panel to copy, and if it has none `check_shelter` never refused it.

    ORIENTATION IS THEN ASKED OF THE INSTRUMENT, NOT ASSUMED.  A donor's yaw
    was authored for the donor's place in the course, so a copy moved
    inboard can end up rising away from the ridge.  The candidate body is
    handed to `I.check_orientation` and any proposal it names in
    `inverted_at` is yawed 180 degrees about its own pivot - the same
    correction, from the same instrument and the same published population,
    that `upright_plan` applies to authored rows.  A donor with any roll or
    pitch is left alone and reported unflipped rather than guessed at.
    MEASURED on the lighthouse: 10 proposals, 6 of them yawed, and the
    repaired body answers shelter 0, orientation 0, duplicates 0 with the
    plan re-run over it EMPTY.

    AND THAT IS WHY THE EMITTER DOES NOT APPLY IT.  A hole the check names is
    a column with roof on all eight sides and sky above, and a FLUE is
    exactly that.  MEASURED, every hole in the three bodies this repair was
    ever pointed at, against the fire-class pieces within 1 m of the column
    in xz: the lighthouse 10 of 10 (its beacon `bonfire`), Fresh42's
    `iron-era-workshop` 11 of 12 (`smelter`, `charcoal_kiln`, `forge`) and
    `sandbox-portal-hub` 1 of 1 (`piece_brazierfloor01`) - 22 of 23.  And
    `Fireplace::CheckUnderTerrain` switches a fire off when
    `SmokeSpawner.IsBlocked()` (see `settlements/terminus_camp.py`), so a
    panel over the flue is a roof that puts the fire out.  Six of the twelve
    workshop proposals are also `wood_wall_roof_top` - a vertical gable
    triangle - slid over the column, which covers the sample line and roofs
    nothing.  The plan stays a report; the refusal stays with its number.
    """
    if not solids:
        return []
    cols = ST.Columns(geom)
    bodies = [b for b in I.bodies(solids, slack=0.35)
              if len(b) >= MIN_BODY_PIECES]
    row_of = {id(s): r for s, r in zip(solids, rows)}
    out: list[dict] = []
    for i, body in enumerate(bodies):
        f = ST.check_shelter([(body, [], f"body-{i + 1}")], cols)
        if not f or f[0].ok or f[0].advisory:
            continue
        roofs = [s for s in body if ST._is_roof(s.name) and s.verts]
        if not roofs:
            continue
        for (hx, htop, hz) in f[0].numbers["holes_at"]:
            d = min(roofs, key=lambda s: (s.pos[0] - hx) ** 2
                    + (s.pos[2] - hz) ** 2)
            out.append({"copy_of_row": row_of[id(d)], "prefab": d.name,
                        "pos": [round(hx, 3), round(d.pos[1], 4),
                                round(hz, 3)],
                        "rot": [round(q, 7) for q in d.quat],
                        "closes": [hx, htop, hz], "yawed_180": False})
    if not out:
        return []

    def placed(e):
        return I.mk_solid(e["prefab"], tuple(e["pos"]), tuple(e["rot"]),
                          geom[e["prefab"]])

    candidate = list(solids) + [placed(e) for e in out]
    inverted = {_published_key(r["prefab"], r["pos"])
                for r in I.check_orientation(candidate, geom)["inverted_at"]}
    for e in out:
        key = _published_key(e["prefab"], e["pos"])
        qx, qy, qz, qw = e["rot"]
        if key not in inverted or abs(qx) > 1e-6 or abs(qz) > 1e-6:
            continue
        e["rot"] = [round(v, 7) for v in
                    I.yaw(I.math.degrees(2 * I.math.atan2(qy, qw)) + 180.0)]
        e["yawed_180"] = True
    out.sort(key=lambda r: (r["copy_of_row"], r["pos"]))
    return out


# ---------------------------------------------------------------------------
# UPSIDE DOWN IS NOT A YAW.
#
# `check_orientation` refuses two things: a panel whose high edge is FARTHER
# from its ridge than its low edge (`inverted`), and a panel whose own high
# edge is placed BELOW its own low edge (`upside_down`).  `upright_plan` has
# only ever answered the first, with a 180 degree yaw - and a yaw cannot
# change which way is up.  MEASURED on Fresh42's `pre-fader/ashlands-forward-
# base` (`hs_ashlands_compact_endgame_base1.blueprint`): four `ashwood_wall_
# roof_26[_upsidedown]` rows rolled face-down, one of them ALSO inverted; the
# emitter yawed that one, which left it face-down and rising the other way,
# and refused the body "0 of 64 rise AWAY ... (4 of them upside down).
# Worst: " - an empty worst list, because the survivors were never inverted.
#
# THE REPAIR KEEPS THE SOLID AND CHANGES ONLY WHICH WAY IS UP.  A row the
# author rolled face-down still OCCUPIES the space the author saw; what is
# wrong is the piece's own up.  So the candidate poses are every pose that
# reproduces the authored occupied solid within REORIENT_SOLID_TOL_M on every
# collider vertex, both ways: the row's own prefab under each of the 24
# axis-aligned rotations of its own frame, and its mirror twin (`<name>` <->
# `<name>_upsidedown`, which Valheim ships as the vertical mirror of the
# same collider) - each translated so its solid lands on the authored one.
# Of those, the one taken is the one the INSTRUMENT, asked about the whole
# body, counts as neither upside down nor inverted: the body's own ridge
# chooses the pose, and no second ridge rule is written here.  A row with no
# such pose is LEFT AS AUTHORED and named with its number - its only
# solid-keeping upright pose rises away from the ridge, so turning it over
# would trade one refusal for the other, and moving it would change the
# building.
# ---------------------------------------------------------------------------
REORIENT_SOLID_TOL_M = 0.01      # the section's "identical occupied space at 1 cm"
UPSIDE_DOWN_TWIN = "_upsidedown"
REORIENT_DEPENDS_ON = (
    "the row's OWN OCCUPIED SOLID: every collider vertex of the new pose "
    f"within {REORIENT_SOLID_TOL_M} m of the authored solid, and every "
    "authored vertex within that of the new one",
    "the row's own frame: any of the 24 axis-aligned rotations of it, not "
    "only a yaw",
    f"the pose's prefab: the row's own, or its mirror twin (`<name>` <-> "
    f"`<name>{UPSIDE_DOWN_TWIN}`) when that twin is measured, a build piece "
    "and spawnable",
    "the instrument's verdict on the WHOLE BODY with the new pose: one fewer "
    "upside-down piece and no more inverted ones",
)


def _cube_rotations() -> list[tuple[float, float, float, float]]:
    """The 24 rotations that map a piece's own axes onto themselves, in a
    fixed order: identity first, then by angle, then lexicographically."""
    s = 2 ** -0.5
    gens = [(1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0), (0.0, 0.0, 1.0, 0.0),
            (s, 0.0, 0.0, s), (0.0, s, 0.0, s), (0.0, 0.0, s, s)]
    seen = {(0.0, 0.0, 0.0, 1.0)}
    frontier = list(seen)
    while frontier:
        nxt = []
        for g in frontier:
            for h in gens:
                q = I.qmul(g, h)
                lead = next((c for c in (q[3], q[0], q[1], q[2])
                             if abs(c) > 1e-9), 1.0)
                if lead < 0:
                    q = tuple(-c for c in q)
                q = tuple(round(c, 9) + 0.0 for c in q)
                if q not in seen:
                    seen.add(q)
                    nxt.append(q)
        frontier = nxt
    return sorted(seen, key=lambda q: (-abs(q[3]), q))


_CUBE = _cube_rotations()


def _twin(name: str) -> str:
    return (name[:-len(UPSIDE_DOWN_TWIN)] if name.endswith(UPSIDE_DOWN_TWIN)
            else name + UPSIDE_DOWN_TWIN)


def _same_solid(a: list, b: list, tol: float) -> float | None:
    """The larger of the two one-sided vertex distances, or None past `tol`.
    Both directions: a pose whose vertices all lie on the authored solid but
    leave part of it bare is not the same solid."""
    worst = 0.0
    for p, q in ((a, b), (b, a)):
        for u in p:
            d = min((u[0] - v[0]) ** 2 + (u[1] - v[1]) ** 2 + (u[2] - v[2]) ** 2
                    for v in q) ** 0.5
            if d > tol:
                return None
            worst = max(worst, d)
    return worst


def _upside_down(s, geom: dict[str, list]) -> bool:
    """The instrument's own predicate for ONE solid, via its own
    `rise_edges`: the piece's high edge placed below its low edge."""
    e = I.rise_edges(geom.get(s.name) or [], s.quat, s.pos)
    return e is not None and e[0][1] < e[1][1]


def _poses(s, geom: dict[str, list], build: set[str] | None, spawnable):
    """Every pose that reproduces this row's occupied solid and stands the
    right way up, in a fixed order: own prefab before twin, rotations in
    `_CUBE` order."""
    if not s.verts:
        return
    n = len(s.verts)
    cen = [sum(v[k] for v in s.verts) / n for k in range(3)]
    for name in (s.name, _twin(s.name)):
        parts = geom.get(name)
        if not parts:
            continue
        if name != s.name and ((build is not None and name not in build)
                               or (spawnable is not None
                                   and not spawnable(name))):
            continue
        for g in _CUBE:
            q = I.qmul(s.quat, g)
            t0 = I.mk_solid(name, (0.0, 0.0, 0.0), q, parts)
            if len(t0.verts) != n:
                continue
            c0 = [sum(v[k] for v in t0.verts) / n for k in range(3)]
            pos = tuple(cen[k] - c0[k] for k in range(3))
            t = I.mk_solid(name, pos, q, parts)
            dev = _same_solid(t.verts, s.verts, REORIENT_SOLID_TOL_M)
            if dev is None or _upside_down(t, geom):
                continue
            yield t, round(dev, 4)


def reorient_plan(solids, rows, geom: dict[str, list],
                  build: set[str] | None = None, spawnable=None
                  ) -> list[dict]:
    """Which upside-down rows can be turned the right way up IN THEIR OWN
    SOLID, to what, and which cannot and why.  See `REORIENT_DEPENDS_ON`.

    THE POPULATION IS THE INSTRUMENT'S COUNT.  `check_orientation` publishes
    `upside_down` as a number and names only the rows that are ALSO
    inverted, so the candidates are every solid its own `rise_edges` puts
    face-down, and a candidate is this repair's business only if the
    instrument COUNTS it: the body without that row must read one fewer
    upside-down piece.  Upside down is a property of the piece's own pose, so
    removing it changes no other piece's own `hy < ly`; a face-down piece in
    a two-panel body or a body with no ridge is not counted and not touched.

    Each entry is `status: "reposed"` with the new prefab, position and
    rotation, or `status: "authored"` with the reason and, where one exists,
    the margin its best solid-keeping upright pose would have had.  Accepted
    poses keep the solid, so they move no AABB, no body and no ridge: every
    later candidate is judged against the same ridge as the first.
    """
    if not solids:
        return []
    base = I.check_orientation(solids, geom)
    if not base["upside_down"]:
        return []
    row_of = {id(s): r for s, r in zip(solids, rows)}
    live = list(solids)
    down, inv = base["upside_down"], base["inverted"]
    out: list[dict] = []
    for i, s in enumerate(solids):
        if not _upside_down(s, geom):
            continue
        without = live[:i] + live[i + 1:]
        if I.check_orientation(without, geom)["upside_down"] != down - 1:
            continue
        entry = {"row": row_of[id(s)], "prefab": s.name,
                 "at": [round(c, 4) for c in s.pos]}
        best = None
        tried = 0
        for t, dev in _poses(s, geom, build, spawnable):
            tried += 1
            trial = live[:i] + [t] + live[i + 1:]
            v = I.check_orientation(trial, geom)
            key = _published_key(t.name, t.pos)
            margin = next((r["margin_m"] for r in v["inverted_at"]
                           if _published_key(r["prefab"], r["pos"]) == key),
                          None)
            if (v["upside_down"] == down - 1 and v["inverted"] <= inv
                    and margin is None):
                live = trial
                down, inv = v["upside_down"], v["inverted"]
                entry.update(status="reposed", to_prefab=t.name,
                             pos=[round(c, 4) for c in t.pos],
                             rot=[round(c, 7) for c in t.quat],
                             solid_deviation_m=dev)
                break
            if margin is not None and (best is None or margin > best):
                best = margin
        else:
            if not tried:
                entry.update(status="authored", margin_m=None,
                             why="no pose of this prefab or its mirror twin "
                                 "keeps its solid and stands the right way up")
            else:
                entry.update(status="authored", margin_m=best,
                             why=f"every one of its {tried} pose(s) that keep "
                                 f"its solid and stand the right way up rises "
                                 f"AWAY from its ridge"
                                 + (f" (best {best:+.3f} m)" if best is not None
                                    else "")
                                 + "; turning it over would trade upside_down "
                                   "for inverted, and moving it would change "
                                   "the building")
        out.append(entry)
    return out


# ---------------------------------------------------------------------------
# CO-LOCATED COPIES, DROPPED BY THE SECTION'S OWN SENTENCE.
#
# `check_duplicates` refuses a row that is a second copy of a sibling -
# SAME SOLID or BURIED COPY - with a sentence that is also the whole case for
# removing it: "remove it and nothing moves, nothing opens, nobody can tell".
# It publishes COUNTS, not rows, so this repair asks the section's own
# `buried_fraction` of each row against the KEPT members of the section's own
# group.  MEASURED on Fresh42, the refusals it turns into buildings:
#
#   complete-station-hub  1 buried copy: `Piece_grausten_wall_arch` yawed 180
#                         and 2 cm off its sibling, 100 % inside it
#   early-dock            6: `wood_wall_log_4x0.5` pairs, each yawed 180 on
#                         one pivot, a symmetric log laid twice
#   ashlands-fwd-base    12: `wood_wall_quarter` pairs, same
#
# and the 11 SHARED pivots in those files (`ashwood_arch_big` half-arches on
# one pivot, 0.359 inside each other) are kept, because each fills space
# nothing else fills.  The emitter re-runs the section afterwards: the repair
# is accepted by `co_located_rows` coming out clean, never by this list.
# ---------------------------------------------------------------------------
COPIES_DEPENDS_ON = (
    "prefab",
    f"pivot within DUP_TOL_M ({DUP_TOL_M} m) of another row of that prefab "
    "on ALL THREE axes, transitively - the section's own cube and grouping",
    "the row's own solid at least DUP_BURIED_FRACTION inside the KEPT "
    "members of that group (`structures.buried_fraction`) - never inside a "
    "member this plan has already dropped",
)


def _co_located_groups(solids) -> list[list[int]]:
    """The section's grouping: same prefab, pivots within DUP_TOL_M on all
    three axes, closed transitively."""
    tol = DUP_TOL_M
    cells: dict[tuple, list[int]] = collections.defaultdict(list)
    for i, s in enumerate(solids):
        cells[(s.name,) + tuple(int(I.math.floor(c / tol)) for c in s.pos)
              ].append(i)
    parent = list(range(len(solids)))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for (nm, cx, cy, cz), members in cells.items():
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for j in cells.get((nm, cx + dx, cy + dy, cz + dz), ()):
                        for i in members:
                            if i != j and all(
                                    abs(solids[i].pos[k] - solids[j].pos[k])
                                    <= tol for k in range(3)):
                                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = collections.defaultdict(list)
    for i in range(len(solids)):
        groups[find(i)].append(i)
    return [g for g in groups.values() if len(g) > 1]


def _copy_containers(g: list[int], i: int, kept: set[int]) -> list[int]:
    """The members row `i` may be inside: the OTHER members of its group
    that are still KEPT.  A member already dropped holds nothing up."""
    return [j for j in g if j != i and j in kept]


def _copy_fraction(s, others: list, geom: dict[str, list]) -> float:
    """The section's own `buried_fraction`: how much of `s` those fill."""
    return ST.buried_fraction(s, others, geom)


def copies_plan(solids, rows, geom: dict[str, list]) -> list[dict]:
    """Which rows are invisible copies of a sibling, per `COPIES_DEPENDS_ON`.

    Within a group the LATEST-authored row is asked first, so a pair keeps
    the row the author placed first and a triple keeps one.  A row with no
    measured solid is never dropped: it cannot be shown to be inside
    anything, and the section's `unmeasured` count keeps it refused.
    """
    out: list[dict] = []
    for g in _co_located_groups(solids):
        kept = set(g)
        for i in sorted(g, key=lambda k: rows[k], reverse=True):
            s = solids[i]
            inside = [j for j in _copy_containers(g, i, kept)
                      if solids[j].verts]
            if not s.verts or not inside:
                continue
            f = _copy_fraction(s, [solids[j] for j in inside], geom)
            if f >= ST.DUP_BURIED_FRACTION:
                kept.discard(i)
                out.append({"row": rows[i], "prefab": s.name,
                            "at": [round(c, 4) for c in s.pos],
                            "inside_rows": sorted(rows[j] for j in inside),
                            "buried_fraction": round(f, 3)})
    out.sort(key=lambda r: r["row"])
    return out


# ---------------------------------------------------------------------------
# A PROP SUNK UNDER THE FLOOR PLANE.
#
# `base_geometry.floor_datum` lays the pad at the FLOOR plane and reports
# `props_below_floor` when a ground-resting piece stands lower - and on a flat
# pad at that plane such a piece is SUNK into the ground by the difference.
# For a fire that is not cosmetic: `Fireplace::CheckUnderTerrain` blocks the
# fire when the ground is more than `m_checkTerrainOffset` (hearth 0.40 m)
# above its pivot (`settlements/terminus_camp.py`, from the IL).  MEASURED on
# Fresh42's `pre-fader/ashlands-forward-base`:
#
#   hearth             0.500 m under the plane; lifted onto it, it enters no
#                      physical solid - SEATED
#   piece_table_round  0.553 m under, embedded in the authored `stone_floor`
#                      course directly beneath the `blackforge`; lifted, it
#                      enters blackforge, dvergrprops_wood_floor and
#                      piece_dvergr_wood_wall - LEFT, and named
#
# THE COLLISION IS ASKED OF PHYSICAL SOLIDS ONLY.  The gate's corpus carries
# `piece_artisanstation`'s 40 x 200 x 40 m trigger volume, which reads every
# prop in that building as "inside something" wherever it goes;
# `base_geometry.Geometry` drops trigger volumes from build pieces for exactly
# this kind of question, and its parts are what the lift is tested against.
# ---------------------------------------------------------------------------
PROPS_DEPENDS_ON = (
    "ground-resting class: base_geometry.Geometry.is_ground_resting (name AND "
    "a measured pivot-at-base solid) - the datum's own population",
    "the floor plane the datum chose, and base_geometry.FLOOR_TOL_M below it - "
    "the datum's own threshold for `props_below_floor`",
    "every OTHER row's PHYSICAL solid the lifted prop would enter "
    "(structures.buried_fraction > 0), trigger volumes excluded",
)
_BG: list = []


def _base_geometry():
    """`base_geometry.Geometry`, loaded once: the emitter re-measures after
    every repair and the corpus is the same every time."""
    if not _BG:
        if str(HERE) not in sys.path:
            sys.path.insert(0, str(HERE))
        import base_geometry                               # noqa: PLC0415
        _BG.append((base_geometry, base_geometry.Geometry()))
    return _BG[0]


def _enters(s, t, phys: dict[str, list]) -> bool:
    """Does the lifted prop `s` put any of its own interior inside `t`?  The
    section's own interior probe grid, so a prop RESTING on a floor - touching
    it face to face - enters nothing."""
    return ST.buried_fraction(s, [t], phys) > 0


def props_plan(objs, base_y: float, physical=None, resting=None
               ) -> list[dict]:
    """Which sunk ground-resting rows can be lifted onto the floor plane, and
    which cannot and what they would enter.  See `PROPS_DEPENDS_ON`.

    `physical(prefab) -> parts | None` is the collision corpus and
    `resting(prefab) -> bool` the ground-resting class; the defaults are
    `base_geometry.Geometry.physical_parts` and `.is_ground_resting`, the
    datum's own.  The lift is the measured depth itself - the pivot of a
    pivot-at-base piece IS its underside - so the prop lands on the plane
    and no point goes below it.
    """
    bgmod, bg = _base_geometry()
    physical = physical or bg.physical_parts
    resting = resting or bg.is_ground_resting
    below = [i for i, o in enumerate(objs)
             if resting(o.prefab)
             and float(o.pos[1]) < base_y - bgmod.FLOOR_TOL_M]
    if not below:
        return []
    phys: dict[str, list] = {}
    placed = []
    for i, o in enumerate(objs):
        parts = physical(o.prefab)
        if parts:
            phys[o.prefab] = parts
            placed.append((i, I.mk_solid(o.prefab, tuple(float(c) for c in o.pos),
                                         tuple(float(c) for c in o.rot),
                                         parts)))
    out = []
    for i in below:
        o = objs[i]
        lift = base_y - float(o.pos[1])
        entry = {"row": i, "prefab": o.prefab,
                 "at": [round(float(c), 4) for c in o.pos],
                 "depth_m": round(lift, 4)}
        parts = phys.get(o.prefab)
        if not parts:
            entry.update(status="left", why="no measured physical solid, so "
                         "nothing can say what lifting it would enter")
            out.append(entry)
            continue
        s = I.mk_solid(o.prefab, (float(o.pos[0]), base_y, float(o.pos[2])),
                       tuple(float(c) for c in o.rot), parts)
        hits = sorted({t.name for j, t in placed if j != i
                       and t.xmin < s.xmax and s.xmin < t.xmax
                       and t.ymin < s.ymax and s.ymin < t.ymax
                       and t.zmin < s.zmax and s.zmin < t.zmax
                       and _enters(s, t, phys)})
        if hits:
            entry.update(status="left", enters=hits,
                         why=f"lifting it {lift:.3f} m onto the floor plane "
                             f"drives it into {', '.join(hits)}")
        else:
            entry.update(status="seated", lift_m=round(lift, 4))
        out.append(entry)
    return out



def _counted_upside_down(solids, geom: dict[str, list]) -> list[int]:
    """Indices of the solids the INSTRUMENT counts as upside down.

    `check_orientation` publishes that population as a number only.  A
    candidate is a solid its own `rise_edges` puts face-down; it is counted
    if the body without it reads one fewer - upside down being a property of
    the piece's own pose, no other piece's predicate moves with it."""
    base = I.check_orientation(solids, geom)["upside_down"]
    if not base:
        return []
    return [i for i, s in enumerate(solids) if _upside_down(s, geom)
            and I.check_orientation(solids[:i] + solids[i + 1:], geom)
            ["upside_down"] == base - 1]


def check_orientation(solids, geom: dict[str, list]) -> list[Refusal]:
    """Does every oriented piece rise toward its own body's ridge, the right
    way up?

    The instrument is `accept.instruments.check_orientation`, unchanged and
    unwrapped, because the whole lesson of this class is that a SECOND way of
    asking produced "349/349 at tolerance 0" over 349 inverted panels.

    TWO POPULATIONS, BOTH NAMED.  The instrument refuses a panel that rises
    away from its ridge AND a panel whose own high edge is placed below its
    low edge, and the second is not a subset of the first.  This refusal
    used to read "0 of 64 oriented pieces rise AWAY ... (4 of them upside
    down) ... Worst: " - a count that called the upside-down four a subset of
    an empty set, and a worst list with nothing in it, because the instrument
    lists only inverted rows.  Each population is now named with its rows.
    """
    if not solids:
        return []
    r = I.check_orientation(solids, geom)
    if not (r["inverted"] or r["upside_down"]):
        return []
    worst = ", ".join(
        f"{nm} at local ({x:.2f},{y:.2f},{z:.2f}) toward {t:+.3f}"
        for t, nm, x, y, z in r["worst"][:4]) or "none"
    down = _counted_upside_down(solids, geom) if r["upside_down"] else []
    faces = ", ".join(
        f"{solids[i].name} at local ({solids[i].pos[0]:.2f},"
        f"{solids[i].pos[1]:.2f},{solids[i].pos[2]:.2f})" for i in down[:4])
    return [Refusal(
        "inverted_panels",
        f"{r['inverted']} of {r['checked']} oriented pieces rise AWAY from "
        f"their own body's ridge and {r['upside_down']} stand UPSIDE DOWN "
        f"(own high edge placed below own low edge), out of {r['pieces']} "
        f"roof-class pieces in {r['bodies']} bodies. A 180 degree yaw moves "
        f"neither the footprint nor the AABB, so no count and no position "
        f"census can see this; the signed slope can. Worst rising away: "
        f"{worst}"
        + (f". Upside down: {faces}" if faces else ""),
        "--allow-inverted-panels",
        inverted=r["inverted"], checked=r["checked"],
        upside_down=r["upside_down"], pieces=r["pieces"],
        upside_down_at=[[solids[i].name] + [round(c, 4)
                                            for c in solids[i].pos]
                        for i in down])]


def check_shelter(solids, geom: dict[str, list]) -> list[Refusal]:
    """Is any deck column of this blueprint ENCLOSED BY WALLS and OPEN TO THE
    SKY?

    A PROPERTY OF THE FILE, NOT OF THE SITE.  Deck columns, enclosure and sky
    are all decided by the pieces' own relative geometry - the gate's
    `check_shelter` never reads the ground - so a blueprint that leaves a
    walled deck sample uncovered leaves it uncovered at every site it is ever
    placed at, which is exactly what the world shows.

    MEASURED on Ulfsland: 12 shelter refusals over six bodies emitted from
    TWO files.  `hs_ashlands_neletit_portalhubtower` produced portal-hall
    (8 enclosed-and-open deck columns of 872, 263 open in all, under a
    232-panel roof) AND hognest-castle (8 of 875) - the same count twice,
    from the same file.  `outposttower.blueprint` produced wt-peak, wt-east
    and wt-spawn at 8, 8 and 5.  Six bodies, two files, and no procedural
    roof loop anywhere in the placer: the settlement drivers select a
    blueprint and transform it, so the roof's extent is whatever the file
    authored.  Asking the file is therefore the only place the answer is
    cheap, and the only place a fix carries to a new seed.

    A TERRACE IS NOT A DEFECT and this check does not make it one: the
    section counts ONLY columns that are both enclosed on all eight rays AND
    uncovered, which is why wt-peak's 61-column parapet walk passes and its
    8 walled columns do not.  Nothing here reinterprets that predicate; it is
    called unwrapped, for the same reason `check_orientation` is.
    """
    if not solids:
        return []
    bodies = [b for b in I.bodies(solids, slack=0.35)
              if len(b) >= MIN_BODY_PIECES]
    if not bodies:
        return []
    named = [(b, [], f"body-{i + 1}") for i, b in enumerate(bodies)]
    cols = ST.Columns(geom)
    out: list[Refusal] = []
    for f in ST.check_shelter(named, cols):
        if f.ok or f.advisory:
            continue
        n = f.numbers
        out.append(Refusal(
            "unsheltered_deck",
            f"{n.get('inside_and_open')} deck column(s) of this blueprint "
            f"are ENCLOSED BY WALLS and OPEN TO THE SKY "
            f"({n.get('open')} open in all, of {n.get('deck_columns')} deck "
            f"columns, under a {n.get('roof_panels')}-panel roof). A walled "
            f"sample with nothing above it is a hole in the roof, and it is "
            f"a property of THIS FILE: the placer transforms the blueprint "
            f"and does not author a roof, so every site this file reaches "
            f"gets the same hole.",
            "--allow-unsheltered-deck",
            inside_and_open=n.get("inside_and_open"), open=n.get("open"),
            deck_columns=n.get("deck_columns"),
            roof_panels=n.get("roof_panels"),
            worst_at=n.get("worst_at"),
            # THE REFUSAL CARRIES THE POPULATION `shelter_plan` ACTS ON, in
            # full, so the number and the correction cannot drift apart.
            holes_at=n.get("holes_at")))
    return out


# SKIN_AT_PLACEMENT_YAW_DEPENDS_ON: the yaw the body will be emitted at.
# Neuter `False`: the shipped emitter, which asked the roof skin nothing
# before emission and so let the gate find it in the built world.  Set to
# `authored`, the check is asked at the file's own frame (yaw 0) - the
# question a blueprint-level chooser could ask without a placement - and is
# MEASURED to answer "clean" for the two town bodies the gate refused.
SKIN_AT_PLACEMENT_YAW_DEPENDS_ON = "placement"


def yawed_solids(solids, geom: dict[str, list], yaw_deg: float):
    """The same solids after `--rotate yaw_deg` about the blueprint's origin
    - the emitter's own transform (`qrot(yq, pos)`, `qmul(yq, rot)`) - and
    nothing else.  A translation is deliberately NOT applied: the gate's
    lattice is anchored on the roof's own minimum, so it moves with the
    body, and the verdict was MEASURED identical over random `--at`."""
    if not yaw_deg:
        return list(solids)
    q = I.yaw(yaw_deg)
    return [I.mk_solid(s.name, I.rotate(q, s.pos), I.qmul(q, s.quat),
                       geom[s.name]) for s in solids]


def check_skin(solids, geom: dict[str, list], yaw_deg: float = 0.0
               ) -> list[Refusal]:
    """Is the roof SKIN of this blueprint, AS IT WILL STAND, continuous?

    The gate's `structures/roof_skin` question, asked with the gate's own
    function over the body the emitter is about to write.  It was asked of
    nobody before emission: the emitter judged `check_shelter` (a covered
    deck) and never the skin, so a step between adjacent roof columns with
    nothing in the gap reached the world and was refused there.

    A PROPERTY OF THE FILE *AT A YAW*, NOT OF THE FILE.  The gate samples the
    skin on a 0.5 m lattice whose axes are the WORLD's, so a body turned by
    the placer is sampled at different columns, and a step narrower than one
    column is seen at some yaws and not at others.  MEASURED over whole
    degrees, from the files the Ulfsland towns are built of:
    `hs_blackforest_kin_01_woodhouse` carries a hole at 11 of 360 yaws,
    `hs_blackforest_tester_meadows_log_cabin` at 63 of 360,
    `hs_ashlands_neletit_portalhubtower` at 15 of 72 five-degree yaws - and at
    yaw 0, the only yaw a blueprint-level question can be asked at, the first
    two are CLEAN.  So this takes `yaw_deg`, the emitter's `--rotate`, and a
    caller that cannot name the placement's yaw is answering a different
    question (`SKIN_AT_PLACEMENT_YAW_DEPENDS_ON`).

    A hole here is closed by building the missing material, which this tree
    does not solve (`structures.REMEDY_IS_IN_NO_TOOL_HERE["roof_skin"]`): the answer is a checksum-bound
    authored addition (`blueprints/data/authored_repairs.json`), and a body
    with none is refused, with the numbers, rather than emitted."""
    if not solids:
        return []
    if SKIN_AT_PLACEMENT_YAW_DEPENDS_ON != "placement":
        yaw_deg = 0.0
    placed = yawed_solids(solids, geom, yaw_deg)
    bodies = [b for b in I.bodies(placed, slack=0.35)
              if len(b) >= MIN_BODY_PIECES]
    if not bodies:
        return []
    named = [(b, [], f"body-{i + 1}") for i, b in enumerate(bodies)]
    cols = ST.Columns(geom)
    out: list[Refusal] = []
    back = I.yaw(-yaw_deg)
    for f in ST.check_skin(named, cols, geom):
        if f.ok or f.advisory:
            continue
        n = f.numbers
        wx, wt, wz = f.where
        lx, _ly, lz = I.rotate(back, (wx, 0.0, wz))
        out.append(Refusal(
            "roof_skin_gap",
            f"{n.get('holes')} place(s) where this blueprint's roof skin "
            f"jumps {n.get('allowance_m')} m or more between adjacent 0.5 m "
            f"columns with NOTHING IN THE GAP, worst {n.get('worst_step_m')} "
            f"m, when emitted at yaw {yaw_deg:g} (about "
            f"({lx:.1f}, {lz:.1f}) in the blueprint's own frame; "
            f"{n.get('spanned_steps')} further steps are spanned by the "
            f"body, {n.get('second_layer_steps')} stand on a skin that "
            f"carries on underneath). That is daylight through the roof, "
            f"and it is decided by the FILE at this yaw: the placer turns "
            f"the body and does not author a roof. Close it with a "
            f"checksum-bound addition in blueprints/data/authored_repairs.json "
            f"and re-run its yaw sweep, or use another body.",
            "--allow-roof-skin-gaps",
            holes=n.get("holes"), worst_step_m=n.get("worst_step_m"),
            allowance_m=n.get("allowance_m"), yaw_deg=yaw_deg,
            spanned_steps=n.get("spanned_steps"),
            second_layer_steps=n.get("second_layer_steps"),
            steepest_pitch=n.get("steepest_pitch"),
            thickest_panel_m=n.get("thickest_panel_m"),
            roof_columns=n.get("roof_columns"),
            at_local=[round(lx, 1), round(lz, 1)]))
    return out


def check_duplicates(solids, geom: dict[str, list]) -> list[Refusal]:
    """Two rows of the same prefab in the same place.

    `accept.sections.structures.check_duplicates` decides SAME SOLID by the
    piece's own rotated vertex set at 1 cm, so a 180 degree yaw of a
    symmetric piece is correctly a duplicate and an asymmetric one correctly
    is not.  Grading on the quaternion would let the first through.
    """
    if not solids:
        return []
    # `geom` is REQUIRED by the section's own signature; calling without it
    # raised TypeError and took the whole emitter down.
    _f, n = ST.check_duplicates(solids, geom, tol=DUP_TOL_M)
    # THE KEYS THAT MEAN "A ROW NOBODY CAN SEE", named one at a time, because
    # the section deliberately reports a fourth count -
    # `shared_pivot_not_a_duplicate` - that is NOT one: two different pieces
    # legitimately share a pivot.  Counting it here would refuse every
    # blueprint with a beam through a post.
    counted = ("same_solid", "buried_copy", "unmeasured")
    have = [k for k in counted if k in n]
    if not have:
        return [Refusal(
            "co_located_rows",
            "the section's duplicate census returned none of the counts "
            f"this refusal is built from ({', '.join(counted)}); it "
            f"returned {sorted(n)}. A check that cannot find its own "
            "numbers is not a check that passed.",
            "--allow-co-located-rows", returned=sorted(n))]
    total = sum(int(n[k]) for k in have)
    if not total:
        return []
    return [Refusal(
        "co_located_rows",
        f"{total} rows of this blueprint are CO-LOCATED with another row of "
        f"the same prefab within {DUP_TOL_M:.2f} m on all three axes ("
        + ", ".join(f"{n[k]} {k}" for k in have)
        + "). Each is a second `spawn_object` and a second ZDO that nobody "
        "can see.",
        "--allow-co-located-rows",
        **{k: v for k, v in n.items() if k != "sites"})]


# ---------------------------------------------------------------------------
# SPAWN SEATS - the gate's `spawn` probe, asked of the FILE
# ---------------------------------------------------------------------------
# THE DEFECT, MEASURED ON DRIVE 3 OF Fresh42.  The gate's `spawn` section
# refused seven claimable beds fourteen times, every one of them
#     bed#6, #9, #10, #13, #16, #17, #18   0.431-0.432 m of room against the
#     player's 0.490 m capsule radius, 0.449 m of headroom against his
#     1.850 m, and `wood_beam` both times.
# All seven are ONE ROW OF ONE FILE: `hs_blackforest_kin_01_woodhouse
# .blueprint` row 32, a `bed` at local (2.289, 0.117, 0.714) laid 0.714 m
# off the z = 0 wall, whose rail - row 73, a `wood_beam` rolled 45 degrees
# at local y 0.828 - reaches 0.283 m into the room with its underside
# 0.449 m over the floor.  Seven spawn_plans placed it: stenvik-house-1..3
# and vestvik-house-1..4 (ledger seqs 2098-2100, 2194-2197).  The same
# 0.432 / 0.449 m pair refused four Ulfsland beds, and `reseat.py` was
# written to move them one at a time in the world; the file kept emitting
# them.  A property of the FILE is decided here, before a byte is written.
#
# ONE DEFINITION.  The verdict is `accept.sections.spawn.probe_seat` itself,
# called over this blueprint's own solids on the same dead-flat pad every
# other check here uses, with the seat's own solid skipped BY IDENTITY as the
# gate skips it.  The repair is `reseat.candidates` - the chooser the world
# repair already uses, which measures with the gate's own `standing_surface`,
# `free_radius` and `headroom` - asked for the SMALLEST move inside the bed's
# own `reseat.room`.  So the emitter and the gate cannot disagree about a
# seat except through what is not in the file (terrain and neighbours), and
# a seat the chooser offers is re-probed by the gate's function before the
# emitter accepts it.
SEAT_PREFABS = SPAWN.CLAIMABLE_BEDS
# A re-seated bed turns only in quarter turns from its authored yaw: a bed is
# laid against a wall, and a room's walls are square to one another in every
# body on this list.  DESIGN, and the turn is recorded on every row it moves.
SEAT_YAW_STEP_DEG = 90.0
# SEAT_DEPENDS_ON = ("gate_probe", "own_solid", "room", "other_seats").
# One neuter each:
#   _SEAT_CHECK_MODE  "off"   the shipped emitter, which never asked
#   _SEAT_SKIP_MODE   "none"  the seat's own solid left in the probe
#   _SEAT_ROOM_MODE   "off"   the chooser free to move a bed through a wall
#   _SEAT_OTHERS_MODE "self"  a move judged on its own seat alone
_SEAT_CHECK_MODE = "gate"       # gate | off
_SEAT_SKIP_MODE = "own"         # own | none
_SEAT_ROOM_MODE = "room"        # room | off
_SEAT_OTHERS_MODE = "all"       # all | self


def walker_capsule(geom: dict[str, list]) -> tuple[float, float] | None:
    """The player's own capsule (radius, height), MEASURED off `Player` in
    the gate's corpus - 0.490 x 1.850 m, the figure the gate reads off the
    same prefab through its harness.  None when the corpus has no `Player`:
    a seat is then UNJUDGED, which `check_seats` refuses."""
    parts = geom.get("Player")
    if not parts:
        return None
    x0, x1, y0, y1, z0, z1 = RS._local_box(parts)
    return (max(abs(x0), abs(x1), abs(z0), abs(z1)), y1 - y0)


def seat_space(objs, solids, rows, base_y: float, geom: dict[str, list],
               walker: tuple[float, float]):
    """This blueprint as a `reseat.SeatSpace`: its own solids, the dead-flat
    pad at `base_y` as the applied ground, and every claimable bed as a
    seat.  Returns the space and each seat's row in `objs`."""
    seats, seat_rows = [], []
    for s, r in zip(solids, rows):
        if s.name in SEAT_PREFABS:
            seats.append((s.name, *s.pos,
                          tuple(float(q) for q in objs[r].rot)))
            seat_rows.append(r)
    space = RS.SeatSpace(I.Grid(solids, cell=4.0), geom, walker[0],
                         walker[1], seats,
                         [(s.name, *s.pos) for s in solids], ground_y=base_y)
    return space, seat_rows


def probe(space, i: int, label: str) -> tuple[dict, list]:
    """`spawn.probe_seat` at seat `i`: its five numbers and its HARD
    findings.  Reach is not asked - a bed inside its own building is within
    reach of it by construction, and the world's reach is the gate's."""
    n, x, y, z, _q = space.seats[i]
    rec: dict = {}
    fs = SPAWN.probe_seat(label, x, y, z, space.ground_y, space.grid,
                          space.walker_r, space.walker_h, None,
                          skip=(space.own(i) if _SEAT_SKIP_MODE == "own"
                                else ()),
                          record=rec)
    return rec, [f for f in fs if not f.ok and not f.advisory]


def spawn_seats(objs, solids, rows, base_y: float, geom: dict[str, list]
                ) -> tuple[object, list[dict]]:
    """Every claimable bed in this blueprint, judged by the gate's probe.
    Returns the space (None if there is no bed or no measured walker) and one
    entry per bed carrying its row, its five numbers and its hard checks."""
    names = [s.name for s in solids]
    if not any(n in SEAT_PREFABS for n in names):
        return None, []
    walker = walker_capsule(geom)
    if walker is None:
        return None, [{"row": r, "prefab": s.name, "blocked": ["unjudged"],
                       "why": "the corpus has no `Player`, so the walker's "
                              "own capsule is UNMEASURED"}
                      for s, r in zip(solids, rows) if s.name in SEAT_PREFABS]
    space, seat_rows = seat_space(objs, solids, rows, base_y, geom, walker)
    out = []
    for i, r in enumerate(seat_rows):
        rec, hard = probe(space, i, f"{space.seats[i][0]} row {r}")
        out.append({"row": r, "prefab": space.seats[i][0], "seat": i,
                    "local": [round(v, 4) for v in space.seats[i][1:4]],
                    "blocked": [f.check for f in hard],
                    "walker_radius_m": space.walker_r,
                    "walker_height_m": space.walker_h, **{
                        k: rec.get(k) for k in (
                            "surface_y", "carrier", "penetration_m",
                            "clearance_m", "clearance_piece", "headroom_m",
                            "headroom_piece", "drop_m")}})
    return space, out


def check_seats(seats: list[dict], plan: list[dict]) -> list[Refusal]:
    """A claimable bed whose seat the gate's probe refuses, and why, with the
    player's own numbers beside the seat's and, per bed, the re-seat
    `reseat_plan` offers or the reason it offers none.  The emitter applies
    the offered moves and re-runs this, so what survives to the caller is
    only what no move inside the room clears."""
    if _SEAT_CHECK_MODE == "off":
        return []
    bad = [s for s in seats if s["blocked"]]
    if not bad:
        return []
    fix = {e["row"]: e for e in plan}
    parts = []
    for s in bad:
        if s["blocked"] == ["unjudged"]:
            parts.append(f"row {s['row']} {s['prefab']}: {s['why']}")
            continue
        e = fix.get(s["row"], {})
        parts.append(
            f"row {s['row']} {s['prefab']} at local ({s['local'][0]:.3f},"
            f"{s['local'][1]:.3f},{s['local'][2]:.3f}): "
            + ", ".join(s["blocked"])
            + f" - {s['clearance_m']:.3f} m of room"
            + (f" against {s['clearance_piece']}" if s["clearance_piece"]
               else "")
            + f", {s['headroom_m']:.3f} m of headroom"
            + (f" under {s['headroom_piece']}" if s["headroom_piece"]
               else "")
            + f"; the player is {s['walker_radius_m']:.3f} m x "
              f"{s['walker_height_m']:.3f} m; "
            + (f"reseat_plan moves it {e['moved_m']:.3f} m"
               if e.get("status") == "reseated"
               else f"NOT REPAIRABLE: {e['why']}" if e
               else "cleared where it stands by another bed's move in "
                    "reseat_plan"))
    return [Refusal(
        "blocked_seat",
        f"{len(bad)} claimable bed(s) of this blueprint are spawn seats a "
        "player cannot stand up in, by the acceptance gate's own "
        "spawn.probe_seat: " + "; ".join(parts) + ".",
        "--allow-blocked-seats",
        seats=[{k: s.get(k) for k in ("row", "prefab", "local", "blocked",
                                      "clearance_m", "clearance_piece",
                                      "headroom_m", "headroom_piece",
                                      "penetration_m")}
               | {"repair": fix.get(s["row"], {}).get("status")}
               for s in bad])]


def _judge_all(space, solids, poses) -> tuple[dict, dict]:
    """Every seat of `poses` probed by the gate over `solids`: `{seat:
    record}` and `{seat: [hard checks]}`, only the blocked ones in the
    second."""
    now = RS.SeatSpace(I.Grid(solids, cell=4.0), space.geom, space.walker_r,
                       space.walker_h, poses, [], space.ground_y)
    recs, bad = {}, {}
    for i in range(len(poses)):
        rec, hard = probe(now, i, f"{poses[i][0]} seat {i}")
        recs[i] = rec
        if hard:
            bad[i] = [f.check for f in hard]
    return recs, bad


def reseat_plan(space, seats: list[dict], solids) -> list[dict]:
    """For each blocked seat, the SMALLEST move that clears it, or why none
    does.

    A move is admitted only if (i) it stays in the bed's own `reseat.room`,
    on its own floor, (ii) `reseat.candidates` passes it with the chooser's
    margins over the walker's capsule, (iii) the bed's OWN body enters
    nothing deeper than it did as authored - a repair may not trade a seat
    for a bed through a table - and (iv) the gate's `probe_seat`, re-run over
    EVERY seat of the body with this bed where it will stand, clears this
    seat and blocks no seat that was clear.

    THE STATE IS LIVE, NOT PLACED-AND-RETIRED.  The first version carried
    earlier moves as `placed` the way `reseat.plan` does in a world, and
    judged each move against the seats AFTER it at their authored poses -
    MEASURED over the library's 134 bed-carrying bodies:
    `god-shadowstone-fortress` had four beds "re-seated" that were still
    refused once all ten moves stood, and one other body had a clear bed
    blocked by a neighbour's move.  So every move is chosen on the solids as
    they stand after the moves before it, and (iv) is asked of all of them.
    A seat an earlier move already cleared is not moved at all.
    """
    if space is None or _SEAT_CHECK_MODE == "off":
        return []
    current = list(solids)
    poses = list(space.seats)
    own_of = {i: space.own(i) for i in range(len(poses))}
    _recs, blocked = _judge_all(space, current, poses)
    out = []
    for s in seats:
        if not s["blocked"] or s["blocked"] == ["unjudged"]:
            continue
        i = s["seat"]
        if i not in blocked:
            continue
        n, x, y, z, q0 = poses[i]
        live = RS.SeatSpace(I.Grid(current, cell=4.0), space.geom,
                            space.walker_r, space.walker_h, poses,
                            space.pieces, space.ground_y)
        own = own_of[i]
        yaw0 = A.yaw_deg(q0)
        tol, _p, _u = RS.body_overlap(live, n, x, y, z, yaw0, own,
                                      s["surface_y"])
        cells = (RS.room(live, i, own) if _SEAT_ROOM_MODE == "room"
                 else None)
        tried = {}

        def confirm(c, i=i, n=n, q0=q0, yaw0=yaw0, own=own):
            turn = (c["yaw_deg"] - yaw0 + 180.0) % 360.0 - 180.0
            rot = q0 if abs(turn) < 1e-6 else I.qmul(I.yaw(turn), q0)
            moved = I.mk_solid(n, c["pos"], rot, space.geom[n])
            trial = [t for t in current if t not in own] + [moved]
            tposes = list(poses)
            tposes[i] = (n, *c["pos"], rot)
            recs, bad = _judge_all(space, trial, tposes)
            if i in bad or (_SEAT_OTHERS_MODE == "all"
                            and not set(bad) <= set(blocked) - {i}):
                return False
            tried.update(turn=turn, rot=rot, moved=moved, trial=trial,
                         tposes=tposes, rec=recs[i], bad=bad)
            return True

        got = RS.candidates(
            live, i, first=True,
            admit=(None if cells is None else lambda ix, iz: (ix, iz) in cells),
            body_tol=tol, yaw_step=SEAT_YAW_STEP_DEG, confirm=confirm)
        entry = {"row": s["row"], "prefab": n, "from": s["local"],
                 "before": {k: s[k] for k in ("clearance_m", "headroom_m",
                                              "penetration_m",
                                              "clearance_piece",
                                              "headroom_piece")},
                 "room_cells": None if cells is None else len(cells),
                 "authored_body_overlap_m": tol}
        if not got:
            entry.update(status="left", why=(
                f"no cell of the {len(cells) if cells else 0}-cell room on "
                f"this bed's own floor within {RS.SEARCH_R_M:.0f} m clears "
                "the walker's capsule, with the bed's body entering nothing "
                f"deeper than its authored {tol:.3f} m and no other seat "
                "blocked by the move"))
            out.append(entry)
            continue
        c, rec = got[0], tried["rec"]
        entry.update(
            status="reseated", to=list(c["pos"]),
            rot=list(tried["rot"]), turn_deg=round(tried["turn"], 2),
            moved_m=c["moved_m"], body_overlap_m=c["body_overlap_m"],
            after={k: rec.get(k) for k in ("clearance_m", "headroom_m",
                                           "penetration_m",
                                           "clearance_piece",
                                           "headroom_piece")})
        out.append(entry)
        current, poses, blocked = (tried["trial"], tried["tposes"],
                                   tried["bad"])
        own_of[i] = [tried["moved"]]
    return out


def apply_reseat(objs, plan: list[dict]) -> list[dict]:
    """Move every row `reseat_plan` re-seated, in place.  Returns the entries
    it applied.  The ONE application: the emitter and the selftest both call
    it, so the case proves the pose the emitter writes."""
    done = []
    for e in plan:
        if e.get("status") != "reseated":
            continue
        o = objs[e["row"]]
        o.pos = tuple(e["to"])
        o.rot = tuple(e["rot"])
        done.append(e)
    return done


_SEAT_MEMO: dict = {}


def judge_seats(objs, solids, rows, base_y: float, geom: dict[str, list]
                ) -> tuple[list[dict], list[dict]]:
    """`spawn_seats` then `reseat_plan`, remembered for the body's exact rows.

    The emitter re-runs `preflight` after every repair and most repairs do
    not touch a bed's room; MEASURED on `cozy_house.blueprint` (6,034 rows,
    4 blocked beds) one judgement is 17 s, and the emitter asks up to six
    times.  The key is every row's prefab, position and rotation, the datum,
    the corpus and the three seat modes, so any change to the geometry - or a
    neuter - is a new judgement; a copy is handed out so no caller can edit
    the remembered one."""
    key = (base_y, id(geom), len(solids), _SEAT_CHECK_MODE, _SEAT_SKIP_MODE,
           _SEAT_ROOM_MODE, _SEAT_OTHERS_MODE,
           tuple((o.prefab, tuple(o.pos), tuple(o.rot)) for o in objs))
    if key not in _SEAT_MEMO:
        space, seats = spawn_seats(objs, solids, rows, base_y, geom)
        _SEAT_MEMO.clear()
        _SEAT_MEMO[key] = (seats, reseat_plan(space, seats, solids))
    return copy.deepcopy(_SEAT_MEMO[key])



def preflight(objs, base_y: float | None, geom: dict[str, list] | None = None,
              build: set[str] | None = None, spawnable=None,
              yaw_deg: float = 0.0) -> tuple[list[Refusal], dict]:
    """Every question the gate will ask that this file already answers.

    The corpus defaults to `accept.instruments.load_geometry`, which is the
    EXACT reader the gate uses - raw `piece_geometry.json`, no trigger-volume
    drop.  `base_geometry.Geometry` reads the same file but discards solids
    over 16 m of vertical extent from build pieces, which is right for pad
    clearance and wrong here: the emitter and the gate must be looking at
    the same 8 vertices per part or the two verdicts are not comparable.

    Returns the refusals and a report block, so a caller that overrides one
    still RECORDS the number it overrode.  `base_y is None` means the datum
    could not be established, and the support question is then unanswerable
    rather than passed: it is reported as skipped and the caller's existing
    `--allow-unplaceable` path owns the decision.

    `yaw_deg` is the emitter's `--rotate`: the roof-skin question is a
    property of the body AT ITS YAW (`check_skin`), so it is the one question
    here that cannot be answered from the file alone.

    `spawnable(name) -> bool` is asked before `reorient_plan` may turn a row
    into its mirror twin - a prefab the blueprint may not carry, so the
    emitter's own evidence has to answer for it.  None means only measured
    build pieces are offered.

    THE CORPUS IS READ ONCE PER PROCESS, and that is a correctness rule, not
    a speed one.  `structures._basis` caches each part's basis in a
    module-level dict keyed by `id(part)` and never evicts it; this function
    used to call `I.load_geometry()` on every call, and the emitter calls it
    after every repair, so each call freed the previous corpus and the next
    one's part lists could be allocated at a freed part's address and read
    ITS basis - reproduced in isolation: a y 5..6 box read as 0..1.  2 of
    ~120 early-dock runs under that per-call reload refused
    `unsheltered_deck` on a body the rest emitted [cause inferred, not
    re-produced].  A corpus that lives as long as the process frees no
    part, so no address is ever reused.
    """
    if geom is None or build is None:
        g, b = _gate_corpus()
        geom = g if geom is None else geom
        build = b if build is None else build
    solids, rows, unmeasured = solids_of(objs, geom, build)
    refusals: list[Refusal] = []
    refusals += check_orientation(solids, geom)
    refusals += check_duplicates(solids, geom)
    refusals += check_shelter(solids, geom)
    refusals += check_skin(solids, geom, yaw_deg)
    skipped = []
    seat: list[dict] = []
    sub: list[dict] = []
    carried: list[dict] = []
    seats: list[dict] = []
    reseat: list[dict] = []
    if base_y is None:
        skipped.append("unsupported_body: no floor datum, nothing to "
                       "measure a pad against")
        skipped.append("subgrade_rows: no floor datum, so there is no pad "
                       "plane to be under")
        skipped.append("blocked_seat: no floor datum, so no seat has a "
                       "floor to stand on")
    else:
        refusals += check_support(solids, base_y, geom)
        seat = seat_plan(solids, rows, base_y, geom)
        # AFTER the seat plan, and measured on the SEATED geometry, because
        # lowering a body onto what is beneath it is exactly the operation
        # that can push its lowest course under the pad.  `seat_plan` does
        # not mutate `solids`, so the rows it would move are re-measured
        # here at their authored height; the caller re-runs the whole
        # preflight after applying a repair, which is where the seated
        # geometry is judged.
        # The drop is judged against the SAME dead flat pad, handed over as
        # the gate's own ground dictionary, and it is judged twice: which
        # rows are wholly under it, and whether any kept row rests on one of
        # them without reaching the pad itself.
        ground = flat_ground(solids, base_y)
        sub = subgrade_rows(solids, rows, ground, geom)
        carried = subgrade_carried(solids, rows, sub, ground)
        refusals += check_subgrade(sub, carried, base_y)
        seats, reseat = judge_seats(objs, solids, rows, base_y, geom)
        refusals += check_seats(seats, reseat)
    report = {
        "rows": len(objs),
        "measured_solids": len(solids),
        "rows_without_geometry": len(unmeasured),
        "prefabs_without_geometry": sorted(set(unmeasured))[:8],
        "base_y": None if base_y is None else round(base_y, 3),
        "refusals": [{"code": r.code, "detail": r.detail,
                      "override": r.override, **r.numbers}
                     for r in refusals],
        "skipped": skipped,
        # The repairs, computed whether or not the caller asked for them,
        # so a refusal always carries the correction next to the number.
        "seat_plan": seat,
        "upright_plan": upright_plan(solids, rows, geom),
        "reorient_plan": reorient_plan(solids, rows, geom, build, spawnable),
        "copies_plan": copies_plan(solids, rows, geom),
        "props_plan": [] if base_y is None else props_plan(objs, base_y),
        # Computed and REPORTED, never applied: see `shelter_plan`.
        "shelter_plan": shelter_plan(solids, rows, geom),
        "subgrade_plan": sub,
        # Non-empty means the drop is REFUSED: each entry is a sub-grade row
        # and the kept row it holds up.
        "subgrade_carried": carried,
        # Every claimable bed, with the gate's five numbers, and the move
        # that clears each blocked one (or why none does).
        "spawn_seats": seats,
        "reseat_plan": reseat,
    }
    return refusals, report


# ---------------------------------------------------------------------------
# THE CASE FOR THE REPAIR ITSELF.
#
# `upright_plan`'s acceptance was a SENTENCE: "check_orientation re-run over
# the result reports zero inverted".  It reported sixteen, and nothing in the
# tree could tell.  A claim about a repair belongs in a case, and the case has
# to CONSTRUCT the state it tests - geometry supplied here, no corpus, no
# blueprint, no world - because a fixture that borrows one inherits that
# artefact's mutability and then goes quiet rather than red.
# ---------------------------------------------------------------------------
_CASE_PANEL = ["p", -0.9, 0.0, -0.9, 0.9, 0.0, -0.9, -0.9, 0.9, 0.9,
               0.9, 0.9, 0.9, -0.9, 0.1, -0.9, 0.9, 0.1, -0.9,
               -0.9, 1.0, 0.9, 0.9, 1.0, 0.9]
_CASE_WALL = ["b", -10.5, -4.5, -1.0, 10.5, 2.5, 1.0]
_CASE_GEOM = {"wood_roof": [_CASE_PANEL], "wood_wall": [_CASE_WALL]}

# The shelter fixture's own corpus.  A 13 x 13 m deck, four walls high enough
# to enclose it, and a roof laid on a 2 m grid - every part defined here, so
# the case cannot go quiet when a blueprint or the piece corpus moves.
_CASE_FLOOR = ["b", -6.5, -0.2, -6.5, 6.5, 0.0, 6.5]
_CASE_ROOM_WALL = ["b", -6.5, 0.0, -0.2, 6.5, 3.0, 0.2]
_SHELTER_GEOM = {"wood_roof": [_CASE_PANEL], "wood_wall": [_CASE_ROOM_WALL],
                 "wood_floor": [_CASE_FLOOR],
                 # the walker the shelter crack radius is read off
                 "Player": [["b", -0.49, 0.0, -0.49, 0.49, 1.85, 0.49]]}
_APERTURE = (-2, 0, 2)


def _case_hut(hole: bool):
    """A WALLED ROOM UNDER A ROOF WITH A 5 x 5 m HOLE PUNCHED IN THE MIDDLE.

    The roof is 7 x 7 panels at 2 m centres over a 13 x 13 m deck.  Dropping
    the inner 3 x 3 leaves panels on every side of the opening - a hole, not
    a full-depth vent and not a courtyard - and it opens THIRTY-SIX deck
    columns, which is the number that matters: it is more than the twelve
    `holes_at` used to publish, so a repair reading the old display sample
    can be shown closing twelve and stopping.
    """
    s = [I.mk_solid("wood_floor", (0.0, 0.0, 0.0), I.yaw(0.0), [_CASE_FLOOR])]
    for z in (-6.5, 6.5):
        s.append(I.mk_solid("wood_wall", (0.0, 0.0, z), I.yaw(0.0),
                            [_CASE_ROOM_WALL]))
    for x in (-6.5, 6.5):
        s.append(I.mk_solid("wood_wall", (x, 0.0, 0.0), I.yaw(90.0),
                            [_CASE_ROOM_WALL]))
    for x in (-6, -4, -2, 0, 2, 4, 6):
        for z in (-6, -4, -2, 0, 2, 4, 6):
            if hole and x in _APERTURE and z in _APERTURE:
                continue
            s.append(I.mk_solid("wood_roof", (float(x), 3.0, float(z)),
                                I.yaw(0.0), [_CASE_PANEL]))
    return s, list(range(len(s)))


def _case_holes_left(solids, plan) -> int:
    """`check_shelter`'s OWN verdict on the body the plan would produce.

    The repair is scored by re-running the check over solids + proposals,
    never by counting what the repair proposed.  "The plan named ten things"
    and "the roof no longer leaks" are different sentences, and
    `upright_plan` shipped for a session claiming the first while the second
    was false.
    """
    built = list(solids) + [
        I.mk_solid(e["prefab"], tuple(e["pos"]), tuple(e["rot"]),
                   _SHELTER_GEOM[e["prefab"]]) for e in plan]
    bodies = [b for b in I.bodies(built, slack=0.35)
              if len(b) >= MIN_BODY_PIECES]
    if not bodies:
        return -1
    f = ST.check_shelter([(max(bodies, key=len), [], "hut")],
                         ST.Columns(_SHELTER_GEOM))
    return 0 if not f or f[0].ok else f[0].numbers["inside_and_open"]


def _neuter_the_hole_sample():
    """THE NEUTER: `holes_at` back to the twelve-row DISPLAY SAMPLE it was.

    Restores the field verbatim as it shipped - `inside[:12]` - so the case
    shows the repair reading a truncated population, closing twelve of
    thirty-six holes and reporting itself done.  Returns its own undo.

    This is the one neuter that cannot be written as "delete the check": the
    check is untouched and still refuses, still with the right count in
    `inside_and_open`.  Only the population it HANDS ON is clipped, and
    everything downstream agrees with itself anyway.
    """
    real = ST.check_shelter

    def sampled(bodies_named, cols):
        out = real(bodies_named, cols)
        for f in out:
            if "holes_at" in f.numbers:
                f.numbers["holes_at"] = f.numbers["holes_at"][:12]
        return out

    ST.check_shelter = sampled

    def undo():
        ST.check_shelter = real
    return undo


def _case_state(flip_row: int | None = None):
    """TWO ROOFS THAT SHARE A WALL AND NOTHING ELSE.

    An east wing climbing toward +z to a ridge at z=+4, and a west wing 20 m
    away climbing toward -z to a HIGHER ridge at z=-4, joined by one wall that
    is not a roof.  That is the whole defect in four objects:

      bodies(ROOFS)  -> TWO bodies, each measured against its own ridge
      bodies(SOLIDS) -> ONE body, because the wall bridges them, and every
                        east panel is then asked to climb toward the WEST
                        wing's ridge, which is behind it.

    Nothing here is borrowed: the panel is a wedge defined above, the wall is
    a box, and `_CASE_GEOM` is the only corpus either function sees.
    """
    def wing(x, zs, ys, rising):
        q = I.yaw(0.0 if rising > 0 else 180.0)
        return [I.mk_solid("wood_roof", (x, y, z), q, [_CASE_PANEL])
                for z, y in zip(zs, ys)]

    solids = (wing(0.0, [0.0, 2.0, 4.0], [0.0, 1.0, 2.0], +1)
              + wing(20.0, [0.0, -2.0, -4.0], [3.0, 4.0, 5.0], -1)
              + [I.mk_solid("wood_wall", (10.0, 3.5, 0.0), I.yaw(0.0),
                            [_CASE_WALL])])
    if flip_row is not None:
        s = solids[flip_row]
        solids[flip_row] = I.mk_solid(s.name, s.pos, I.yaw(180.0),
                                      [_CASE_PANEL])
    return solids, list(range(len(solids)))


def _case_inverted_rows(solids, rows):
    """`check_orientation`'s own verdict, as a set of ROWS.

    Taken from the instrument's `worst` list rather than re-derived, so the
    case compares the repair against THE CHECK and never against a second
    opinion - which is the entire defect it exists to hold shut.
    """
    r = I.check_orientation(solids, _CASE_GEOM)
    at = {(round(x, 1), round(y, 2), round(z, 1))
          for _t, _n, x, y, z in r["worst"]}
    return sorted(rows[i] for i, s in enumerate(solids)
                  if (round(s.pos[0], 1), round(s.pos[1], 2),
                      round(s.pos[2], 1)) in at), r


def _case_old_upright_plan(solids, rows, geom):
    """THE NEUTER: the shipped `upright_plan`, restored VERBATIM.

    It partitions `I.bodies(SOLIDS)` and takes the roof pieces out of each
    cluster afterwards.  Kept here so the case can show it going red on a
    state built in code, instead of asserting from prose that it used to be
    wrong.
    """
    row_of = {id(s): r for s, r in zip(solids, rows)}
    out = []
    for body in I.bodies(solids, slack=1.2):
        if len(body) < 3:
            continue
        roofy = [s for s in body
                 if "roof" in s.name.lower() or "_shingle" in s.name.lower()]
        if not roofy:
            continue
        top = max(s.ymax for s in roofy)
        ridge = [((s.xmin + s.xmax) / 2, (s.zmin + s.zmax) / 2)
                 for s in roofy if top - s.ymax <= 0.5]
        for s in roofy:
            rv = I.rise_vector(geom.get(s.name) or [], s.quat)
            if rv is None:
                continue
            (rx, _ry, rz), _p = rv
            cx, cz = (s.xmin + s.xmax) / 2, (s.zmin + s.zmax) / 2
            rx0, rz0 = min(ridge, key=lambda p: (p[0] - cx) ** 2
                           + (p[1] - cz) ** 2)
            dx, dz = rx0 - cx, rz0 - cz
            dlen = I.math.hypot(dx, dz)
            rlen = I.math.hypot(rx, rz)
            if dlen < 0.75 or rlen < 1e-6:
                continue
            if (rx * dx + rz * dz) / (rlen * dlen) < 0:
                out.append(row_of[id(s)])
    return sorted(out)


# ---------------------------------------------------------------------------
# THE SUB-GRADE FIXTURE.  A 4 x 4 m hut on a pad at y = 0, every part defined
# here: four deck slabs whose TOP is the pad, four walls standing on them, and
# a FOUNDATION SKIRT of four half-walls under the deck edges with their own
# tops 0.15 m under the pad - the course a floor-centre placement buries.
# Beside it, on the same pad, the two shapes the shipped key could not see:
#
#   a DOOR whose only solid on a lattice line is its threshold sill, 0.10 m
#     under the pad, its leaf standing 2 m proud BETWEEN the lines - the
#     vestvik-villa-3 `wood_door` the gate stopped calling buried;
#   a POST whose foot is sunk 0.20 m and whose shaft rises 1.5 m between the
#     lines, carrying a LINTEL that touches nothing else and never reaches
#     the pad - a row with a structural role for something above it;
#   a BRACE against the skirt whose body stands 0.10 m proud across the
#     lattice lines while its toe is sunk 0.30 m BETWEEN them - the
#     `wood_wall_roof_top_45` shape on Fresh42's looney swamp home, standing
#     in the ground and carried by it.
#
# And a LEDGE on its own: a sill buried 0.15 m in the high side of a stepped
# pad, carrying a plank out over ground 1 m lower - the one state in which a
# correctly buried row really does hold something up.
# ---------------------------------------------------------------------------
_SG_SLAB = ["b", -1.0, -0.2, -1.0, 1.0, 0.0, 1.0]
_SG_SKIRT = ["b", -2.0, -0.5, -0.1, 2.0, 0.5, 0.1]
_SG_WALL = ["b", -2.0, 0.0, -0.1, 2.0, 2.0, 0.1]
_SG_SILL = ["b", -0.5, -0.12, -0.5, 0.5, -0.10, 0.5]
_SG_LEAF = ["b", -0.5, 0.0, 0.30, 0.5, 2.0, 0.40]
_SG_FOOT = ["b", -0.5, -0.40, -0.5, 0.5, -0.20, 0.5]
_SG_SHAFT = ["b", -0.05, -0.20, 0.30, 0.05, 1.50, 0.40]
_SG_LINTEL = ["b", -0.1, 0.0, -0.1, 1.5, 0.2, 0.1]
_SG_BRACE = ["b", -1.0, 0.1, -0.5, 1.0, 0.5, 0.5]
_SG_TOE = ["b", -0.95, -0.30, 0.30, -0.85, 0.1, 0.40]
_SG_LOG = ["b", -1.0, -0.1, -0.1, 1.0, 0.1, 0.1]
_SG_PLANK = ["b", 0.0, 0.0, -0.5, 3.0, 0.1, 0.5]
_SUBGRADE_GEOM = {"wood_floor": [_SG_SLAB], "wood_wall_half": [_SG_SKIRT],
                  "wood_wall": [_SG_WALL], "wood_door": [_SG_SILL, _SG_LEAF],
                  "wood_pole": [_SG_FOOT, _SG_SHAFT],
                  "wood_beam": [_SG_LINTEL],
                  "wood_wall_roof_top_45": [_SG_BRACE, _SG_TOE],
                  "darkwood_beam": [_SG_LOG], "wood_floor_1x1": [_SG_PLANK]}


def _case_subgrade(door: bool = False, post: bool = False,
                   brace: bool = False):
    """The hut, plus the door, the post-and-lintel or the brace.  Returns the
    solids, their rows, and a label for each row the case talks about."""
    s, names = [], {}

    def put(prefab, pos, deg, label=None):
        if label:
            names[len(s)] = label
        s.append(I.mk_solid(prefab, pos, I.yaw(deg), _SUBGRADE_GEOM[prefab]))

    for x, z in ((1.0, 1.0), (3.0, 1.0), (1.0, 3.0), (3.0, 3.0)):
        put("wood_floor", (x, 0.0, z), 0.0)
    for pos, deg in (((2.0, -0.65, 0.1), 0.0), ((2.0, -0.65, 3.9), 0.0),
                     ((0.1, -0.65, 2.0), 90.0), ((3.9, -0.65, 2.0), 90.0)):
        put("wood_wall_half", pos, deg, "skirt")
    for pos, deg in (((2.0, 0.0, 0.1), 0.0), ((2.0, 0.0, 3.9), 0.0),
                     ((0.1, 0.0, 2.0), 90.0), ((3.9, 0.0, 2.0), 90.0)):
        put("wood_wall", pos, deg)
    if door:
        put("wood_door", (6.5, 0.0, 6.5), 0.0, "door")
    if post:
        put("wood_pole", (10.5, 0.0, 10.5), 0.0, "post")
        put("wood_beam", (10.5, 1.5, 10.85), 0.0, "lintel")
    if brace:
        put("wood_wall_roof_top_45", (5.0, 0.0, 0.5), 0.0, "brace")
    return s, list(range(len(s))), names


def _case_ledge():
    """The buried sill and the plank it carries over the low side of a
    stepped pad, with that pad: 0 under the sill, -1 under the plank."""
    s = [I.mk_solid("darkwood_beam", (21.0, -0.25, 20.4), I.yaw(0.0),
                    [_SG_LOG]),
         I.mk_solid("wood_floor_1x1", (21.5, -0.15, 20.5), I.yaw(0.0),
                    [_SG_PLANK])]
    ground = flat_ground(s, 0.0)
    for (x, z) in ST.piece_columns(s[1]):
        ground[ST.gkey(x, z)] = -1.0
    return s, [0, 1], {0: "sill", 1: "plank"}, ground


def _case_subgrade_verdict(solids, rows, names, ground: dict | None = None,
                           drop: bool = True) -> dict:
    """WHAT THE EMITTER DOES with this hut, as VERDICTS - never an assert.

    `emitter` is the whole outcome in one string and each labelled row gets
    its own KEPT / DROPPED, so a neuter that empties the index reads as an
    emitter that EMITS every row rather than as an exception nobody can tell
    from a proof.
    """
    if ground is None:
        ground = flat_ground(solids, 0.0)
    plan = subgrade_rows(solids, rows, ground, _SUBGRADE_GEOM)
    carried = subgrade_carried(solids, rows, plan, ground)
    codes = [r.code for r in check_subgrade(plan, carried, 0.0)]
    gone = sorted(r["row"] for r in plan)
    if "subgrade_structural" in codes:
        emitter = "REFUSED subgrade_structural"
    elif plan and drop:
        emitter = f"DROPS rows {gone}"
    elif plan:
        emitter = "REFUSED subgrade_rows"
    else:
        emitter = "EMITS every row"
    out = {"emitter": emitter, "plan": plan}
    for label in sorted(set(names.values())):
        said = {"DROPPED" if emitter.startswith("DROPS") and row in gone
                else "KEPT" for row, lb in names.items() if lb == label}
        out[label] = said.pop() if len(said) == 1 else "SPLIT"
    return out


def _neuter_own_top():
    """THE NEUTER THAT LOSES `own_top` (and `lowest_pad` with it): the
    gate's own switch back to the SHIPPED cross-section arithmetic - the top
    of the slice a lattice column cuts, against the ground in that column -
    which is also, verbatim, what this module's `subgrade_rows` computed
    before it asked the gate.  Returns its own undo."""
    real = ST._BURIED_PER_COLUMN
    ST._BURIED_PER_COLUMN = True

    def undo():
        ST._BURIED_PER_COLUMN = real
    return undo


def _neuter_row_is_body():
    """THE NEUTER THAT KEEPS A DEPENDENCY IT DOES NOT HAVE: the unit of
    judgement is the row's BODY (the gate's 0.35 m partition), so a row is
    sub-grade only when everything it touches is too.  Returns its undo."""
    real = globals()["_subgrade_units"]

    def by_body(solids, rows):
        row_of = {id(s): r for s, r in zip(solids, rows)}
        return [(b, [row_of[id(s)] for s in b])
                for b in I.bodies(solids, slack=0.35)]
    globals()["_subgrade_units"] = by_body

    def undo():
        globals()["_subgrade_units"] = real
    return undo


def _neuter_support_guard():
    """THE NEUTER THAT REMOVES `support`: nothing a dropped row carries is
    ever looked at.  Returns its undo."""
    real = globals()["subgrade_carried"]
    globals()["subgrade_carried"] = lambda *a, **k: []

    def undo():
        globals()["subgrade_carried"] = real
    return undo


def _neuter_reach_by_column():
    """THE NEUTER THAT READS `support` FROM A SUBSET: a kept row reaches the
    pad only if the slice a lattice column cuts through it does - the
    guard's own first version, which refused Fresh42's looney swamp home for
    five braces standing in the ground.  Returns its undo."""
    real = globals()["_carried_clearance"]
    cols = ST.Columns(_SUBGRADE_GEOM)

    def by_column(k, ground):
        c = None
        for (x, z) in ST.piece_columns(k):
            g = ground.get(ST.gkey(x, z))
            if g is None:
                continue
            sp = cols.span(k, x, z)
            lo = k.ymin if sp is None else sp[0]
            c = lo - g if c is None else min(c, lo - g)
        return c
    globals()["_carried_clearance"] = by_column

    def undo():
        globals()["_carried_clearance"] = real
    return undo


# ---------------------------------------------------------------------------
# THE KEY, THE POSE, THE COPY AND THE PROP.  Four states, every part defined
# here - a wedge, a log, a slab, a few boxes - because a fixture that borrows
# a corpus, a blueprint or a world inherits its mutability and goes quiet.
# Each state carries a row that MUST be repaired and a row that MUST
# SURVIVE, so an index that returns nothing and an index that returns
# everything both go red.
# ---------------------------------------------------------------------------
# MEASURED on `fox-0123456.blueprint`: a `wood_roof` at x = 23.91052
# publishes as 23.9105, which re-rounds to 23.91 while the solid rounds to
# 23.911.
_KEY_X = 23.91052


def _case_key():
    """`_case_state(flip_row=0)` slid to x = _KEY_X: one inverted panel whose
    published position re-rounds away from its own."""
    solids, rows = _case_state(flip_row=0)
    return [I.mk_solid(s.name, (s.pos[0] + _KEY_X, s.pos[1], s.pos[2]),
                       s.quat, _CASE_GEOM[s.name]) for s in solids], rows


def _case_shared_key():
    """The clean roof pair plus a fourth east panel ON ROW 1's PIVOT, yawed
    180 - an inverted row that shares its whole published key with a sound
    one, as a valley pair does."""
    solids, rows = _case_state()
    s = solids[1]
    solids.append(I.mk_solid(s.name, s.pos, I.qmul(I.yaw(180.0), s.quat),
                             [_CASE_PANEL]))
    return solids, list(range(len(solids)))


def _neuter_key_rerounded():
    """THE NEUTER THAT LOSES THE PUBLISHED PRECISION: the shipped key, which
    rounded both sides to 3 - a rounding of the instrument's rounding on one
    side and a single rounding on the other.  Returns its undo."""
    real = globals()["_published_key"]

    def shipped(name, pos):
        return (name, round(float(pos[0]), 3), round(float(pos[1]), 3),
                round(float(pos[2]), 3))
    globals()["_published_key"] = shipped

    def undo():
        globals()["_published_key"] = real
    return undo


def _neuter_key_takes_all():
    """THE NEUTER THAT LOSES THE PER-SOLID VERDICT: every solid at a
    published key is yawed, as the shipped code did.  Returns its undo."""
    real = globals()["_disambiguate_yaw"]
    globals()["_disambiguate_yaw"] = lambda solids, here, geom, inv: here

    def undo():
        globals()["_disambiguate_yaw"] = real
    return undo


def _case_upside(repairable: bool):
    """`_case_state()` with the east wing's middle panel replaced by the SAME
    OCCUPIED SOLID posed face-down.  Repairable: the solid of the sound panel
    (rising toward the ridge), rolled 180 about x.  Not: the solid of that
    panel YAWED 180 - rising away - rolled 180 about z, so the only pose that
    keeps its solid and stands upright rises away (ashlands' row 44)."""
    solids, rows = _case_state()
    q = (1.0, 0.0, 0.0, 0.0) if repairable else (0.0, 0.0, 1.0, 0.0)
    solids[1] = I.mk_solid("wood_roof", (0.0, 2.0, 2.0), q, [_CASE_PANEL])
    return solids, rows


def _solid_gap(a: list, b: list) -> float:
    """The larger one-sided vertex distance between two solids."""
    return max(max(min(sum((u[k] - v[k]) ** 2 for k in range(3)) ** 0.5
                       for v in q) for u in p) for p, q in ((a, b), (b, a)))


def _case_reorient_verdict(solids, rows) -> str:
    """What the repair does to row 1, as a VERDICT."""
    plan = reorient_plan(solids, rows, _CASE_GEOM, set(_CASE_GEOM))
    e = next((p for p in plan if p["row"] == 1), None)
    if e is None:
        return "NOT JUDGED"
    if e["status"] != "reposed":
        return "AUTHORED"
    t = I.mk_solid(e["to_prefab"], tuple(e["pos"]), tuple(e["rot"]),
                   _CASE_GEOM[e["to_prefab"]])
    gap = _solid_gap(t.verts, solids[1].verts)
    return ("REPOSED, solid kept" if gap <= REORIENT_SOLID_TOL_M
            else "REPOSED, solid MOVED")


def _neuter_yaw_only():
    """THE NEUTER THAT LOSES THE ROW'S OWN FRAME: the pose search offered
    only yaws, which is all `upright_plan` ever had.  Returns its undo."""
    real = globals()["_CUBE"]
    globals()["_CUBE"] = [q for q in real if abs(q[0]) < 1e-9
                          and abs(q[2]) < 1e-9]

    def undo():
        globals()["_CUBE"] = real
    return undo


def _neuter_any_upright_pose():
    """THE NEUTER THAT LOSES THE SOLID: any upright pose the instrument
    likes is taken, whether or not it is the space the author filled.
    Returns its undo."""
    real = globals()["_same_solid"]
    globals()["_same_solid"] = lambda a, b, tol: 0.0

    def undo():
        globals()["_same_solid"] = real
    return undo


_CP_LOG = ["b", -2.0, -0.25, -0.25, 2.0, 0.25, 0.25]
_COPY_GEOM = {"wood_wall_log": [_CP_LOG], "wood_roof": [_CASE_PANEL]}


def _case_copies():
    """Three co-located groups, labelled.

      offset  a symmetric log laid twice, the second yawed 180 and 2 cm off -
              `complete-station-hub`'s `Piece_grausten_wall_arch`: one copy
      twin    a log laid twice on one pivot: one copy, and the FIRST survives
      valley  two roof panels on one pivot at yaw 90 and 270, each filling
              its own half of the X: no copy at all
    """
    s: list = []
    names: dict[int, str] = {}

    def put(prefab, pos, deg, label):
        names[len(s)] = label
        s.append(I.mk_solid(prefab, pos, I.yaw(deg), _COPY_GEOM[prefab]))

    put("wood_wall_log", (0.0, 0.0, 0.0), 0.0, "offset-first")
    put("wood_wall_log", (0.02, 0.019, 0.0), 180.0, "offset-copy")
    put("wood_wall_log", (10.0, 0.0, 0.0), 0.0, "twin-first")
    put("wood_wall_log", (10.0, 0.0, 0.0), 0.0, "twin-copy")
    put("wood_roof", (20.0, 0.0, 0.0), 90.0, "valley-a")
    put("wood_roof", (20.0, 0.0, 0.0), 270.0, "valley-b")
    return s, list(range(len(s))), names


def _case_copies_verdict(solids, rows, names) -> dict:
    """What the emitter does, as VERDICTS: the outcome - judged by the
    SECTION re-run over what is kept, at the section's own cube - and each
    labelled row KEPT or DROPPED."""
    plan = copies_plan(solids, rows, _COPY_GEOM)
    gone = {p["row"] for p in plan}
    _f, n = ST.check_duplicates([t for i, t in enumerate(solids)
                                 if i not in gone], _COPY_GEOM,
                                tol=ST.DUP_TOL_M)
    left = n["same_solid"] + n["buried_copy"] + n["unmeasured"]
    out = {"emitter": (f"REFUSED co_located_rows ({left} left)" if left
                       else f"DROPS rows {sorted(gone)}" if gone
                       else "EMITS every row")}
    for i, label in names.items():
        out[label] = "DROPPED" if i in gone else "KEPT"
    return out


def _neuter_exact_pivot():
    """THE NEUTER THAT NAMES A SUBSET: a group is rows on the SAME pivot,
    not within the section's 5 cm cube.  Returns its undo."""
    real = globals()["DUP_TOL_M"]
    globals()["DUP_TOL_M"] = 1e-6

    def undo():
        globals()["DUP_TOL_M"] = real
    return undo


def _neuter_dropped_contain():
    """THE NEUTER THAT NAMES A SUPERSET: a row may be 'inside' a member this
    plan has already dropped.  Returns its undo."""
    real = globals()["_copy_containers"]
    globals()["_copy_containers"] = lambda g, i, kept: [j for j in g if j != i]

    def undo():
        globals()["_copy_containers"] = real
    return undo


def _neuter_pivot_only():
    """THE NEUTER THAT LOSES OCCUPANCY: sharing a pivot is being a copy.
    Returns its undo."""
    real = globals()["_copy_fraction"]
    globals()["_copy_fraction"] = lambda s, others, geom: 1.0

    def undo():
        globals()["_copy_fraction"] = real
    return undo


_PR_FLOOR = ["b", -3.0, -1.0, -3.0, 3.0, 0.0, 3.0]
_PR_HEARTH = ["b", -0.5, 0.0, -0.5, 0.5, 0.6, 0.5]
_PR_TABLE = ["b", -1.0, 0.0, -1.0, 1.0, 0.8, 1.0]
_PR_FORGE = ["b", -0.8, 0.0, -0.8, 0.8, 0.9, 0.8]
_PR_BENCH = ["b", -1.0, 0.0, -0.5, 1.0, 1.0, 0.5]
_PR_TRIGGER = ["b", -20.0, -100.0, -20.0, 20.0, 100.0, 20.0]
_PROPS_RAW = {"stone_floor": [_PR_FLOOR], "hearth": [_PR_HEARTH],
              "piece_table_round": [_PR_TABLE], "forge": [_PR_FORGE],
              "piece_artisanstation": [_PR_BENCH, _PR_TRIGGER]}
_PROPS_PHYSICAL = dict(_PROPS_RAW, piece_artisanstation=[_PR_BENCH])
_PROPS_RESTING = frozenset(("hearth", "piece_table_round", "forge",
                            "piece_artisanstation"))


class _Row:
    __slots__ = ("prefab", "pos", "rot")

    def __init__(self, prefab, pos):
        self.prefab, self.pos, self.rot = prefab, pos, (0.0, 0.0, 0.0, 1.0)


def _case_props() -> list:
    """ashlands-forward-base in five rows, on a plane at y = 0: a stone floor
    course whose top IS the plane, a hearth sunk 0.50 m in it, a table sunk
    0.55 m with a forge standing on the floor directly over it, and an
    artisan station 8 m away carrying its 40 x 200 x 40 m trigger volume."""
    return [_Row("stone_floor", (0.0, 0.0, 0.0)),
            _Row("hearth", (-2.0, -0.50, 0.0)),
            _Row("piece_table_round", (2.0, -0.55, 0.0)),
            _Row("forge", (2.0, 0.0, 0.0)),
            _Row("piece_artisanstation", (8.0, 0.0, 0.0))]


def _case_props_verdict(corpus: dict) -> dict:
    """Each sunk prop SEATED or LEFT, with what it would enter."""
    out = {}
    for e in props_plan(_case_props(), 0.0, physical=corpus.get,
                        resting=_PROPS_RESTING.__contains__):
        out[e["prefab"]] = (f"SEATED {e['lift_m']:.2f} m"
                            if e["status"] == "seated"
                            else f"LEFT, enters {e.get('enters')}")
    return out


def _neuter_lift_blind():
    """THE NEUTER THAT LOSES THE OTHER SOLIDS: every sunk prop is lifted,
    whatever stands where it lands.  Returns its undo."""
    real = globals()["_enters"]
    globals()["_enters"] = lambda s, t, phys: False

    def undo():
        globals()["_enters"] = real
    return undo


# THE SEAT CASES.  Geometry is the gate corpus's own parts for these
# prefabs, COPIED here so the case constructs its state (see the note above
# `_CASE_PANEL`); `Player` is the capsule `walker_capsule` reads.
_SEAT_GEOM = {
    "bed": [["b", -0.6, -0.02, -1.4, 0.6, 0.38, 1.4, "box"]],
    "wood_beam": [["b", -0.9855, -0.2, -0.2, 1.0145, 0.2, 0.2, "box"]],
    "wood_floor": [["b", -1.0096, -0.0329, -1.0148, 0.9904, 0.0969, 0.9852,
                    "box"]],
    "wood_wall_half": [["b", -1.0, -0.5, -0.15, 1.0, 0.5, 0.15, "box"]],
    "Player": [["b", -0.49, 0.0, -0.49, 0.49, 1.85, 0.49, "capsule"]],
    "piece_bed02": [["b", -1.15, -0.045, -1.5, 1.15, 0.425, 1.5, "box"],
                    ["b", -1.15, 0.005, -1.6, 1.15, 1.455, -1.26, "box"],
                    ["b", -1.15, 0.084, 1.605, 1.15, 0.784, 1.835, "box"]],
    "rug_fur": [["b", -1.28, -0.025, -1.785, 1.28, 0.025, 1.785, "box"]],
    # corpus, verbatim: its mesh top is 0.1165 m over its pivot
    "jute_carpet": [
        ["b", -1.785, -0.025, -1.28, 1.785, 0.025, 1.28, "box"],
        ["p", -1.9932, 0.0483, -1.5, 1.9932, 0.0517, -1.5, -1.9932, 0.113,
         -1.5, 1.9931, 0.1165, -1.5, -1.9932, 0.0483, 1.5, 1.9932, 0.0517,
         1.5, -1.9932, 0.113, 1.5, 1.9931, 0.1165, 1.5, "mesh"]],
}
# The drive-3 floor datum of `hs_blackforest_kin_01_woodhouse.blueprint`,
# from the emitter's own stenvik-house-1.plan.repairs.json.
_WOODHOUSE_BASE_Y = 0.09690033323865233
# The drive-3 datum of `hs_ashlands_20999_valheimian.blueprint`, from
# deepnorth-sandbox__complete-station-hub.txt.repairs.json.
_HUB_BASE_Y = 1.5


def _seat_row(prefab, pos, rot):
    r = _Row(prefab, pos)
    r.rot = rot
    return r


def _case_woodhouse(beam: bool = True) -> list:
    """THE DRIVE-3 ROWS, VERBATIM: `hs_blackforest_kin_01_woodhouse
    .blueprint` rows 16 and 24 (floor), 32 (the bed), 40 and 42 (the z = 0
    half wall) and 73 (its `wood_beam` rail, rolled 45 degrees at y 0.828).
    Seven beds on drive 3 were this row 32 under this row 73.  `beam=False`
    is the same room with the rail gone: a seat with nothing wrong."""
    rows = [
        _seat_row("wood_floor", (1.0, 0.0, 1.0),
                  (-7.911741e-09, 0.7071071, -1.182215e-07, 0.7071065)),
        _seat_row("wood_floor", (3.0, 3.814697e-06, 1.0),
                  (7.80008e-08, 1.0, -8.91897e-08, -5.205486e-07)),
        _seat_row("bed", (2.289368, 0.1168785, 0.7143707),
                  (1.182215e-07, 0.7071065, -7.911705e-09, -0.7071071)),
        _seat_row("wood_wall_half", (1.0, 0.3284302, 0.0),
                  (0.0, 1.28547e-07, 0.0, 1.0)),
        _seat_row("wood_wall_half", (3.0, 0.3284302, 0.0),
                  (0.0, 1.28547e-07, 0.0, 1.0)),
    ]
    if beam:
        rows.append(_seat_row("wood_beam", (2.0, 0.8284302, 0.0),
                              (0.3826835, -7.380495e-08, -4.315738e-08,
                               -0.9238795)))
    return rows


def _case_loft() -> list:
    """THE DRIVE-3 ROWS, VERBATIM: `hs_ashlands_20999_valheimian.blueprint`
    row 553, a `piece_bed02` on its upper floor, and the two `rug_fur` rows
    it stands on (509, 514).  Drive 3 read this seat clear (piece_bed02#19,
    1.212 m of room, 3.442 m of headroom).  Its own 1.455 m headboard is
    what the probe must NOT be handed: unskipped, the gate reported the
    same prefab on Ulfsland as '1.150 m INSIDE piece_bed02'."""
    return [
        _seat_row("rug_fur", (3.598595, 6.0, 15.88818),
                  (0.0, -7.017672e-06, 0.0, 1.0)),
        _seat_row("rug_fur", (5.388496, 6.0, 15.74739),
                  (0.0, -7.017672e-06, 0.0, 1.0)),
        _seat_row("piece_bed02", (3.702667, 6.07, 16.58827),
                  (0.0, 0.7071087, 0.0, 0.7071048)),
    ]


def _case_alcove() -> list:
    """A 1.5 m ALCOVE, walled in on all four sides by the drive-3 half wall,
    with the same bed laid along it under two `wood_beam` rails running its
    length 0.05 m off the pivot.  The nearest cell that clears the walker
    is 1.75 m WEST - through the wall, out of the room.  The nearest one
    INSIDE the alcove is 2.75 m along it, past the rails' ends."""
    wall_z = (0.0, 0.7071068, 0.0, 0.7071068)          # wall runs along z
    rows = [_seat_row("bed", (0.0, 0.02, 0.0), (0.0, 0.0, 0.0, 1.0))]
    for x in (-0.9, 0.9):
        for z in (-4.0, -2.0, 0.0, 2.0, 4.0):
            rows.append(_seat_row("wood_wall_half", (x, 0.5, z), wall_z))
    for z in (-4.5, 4.5):
        rows.append(_seat_row("wood_wall_half", (0.0, 0.5, z),
                              (0.0, 0.0, 0.0, 1.0)))
    for z in (-1.0, 1.0):
        rows.append(_seat_row("wood_beam", (0.25, 0.83, z), wall_z))
    return rows


def _case_platform() -> list:
    """TWO SEATS, ONE MOVE.  A bed on a raised `wood_floor` platform under a
    `wood_beam`, and a CLEAR bed on the ground 0.61 m off the platform's
    edge.  The nearest pose that clears the platform bed is 0.75 m EAST,
    where its body overhangs the edge 0.25 m from the ground bed's pivot and
    buries that seat.  The next is 1.00 m WEST, which blocks nothing.  The
    library shape it stands for is `god-shadowstone-fortress`'s bunks: ten
    moves, each judged alone, left a clear bed refused."""
    along_z = (0.0, 0.7071068, 0.0, 0.7071068)
    return [_seat_row("wood_floor", (0.25, 0.8, 0.0), (0.0, 0.0, 0.0, 1.0)),
            _seat_row("bed", (0.25, 0.917, 0.0), (0.0, 0.0, 0.0, 1.0)),
            _seat_row("wood_beam", (0.05, 1.6, 0.0), along_z),
            _seat_row("bed", (1.85, 0.02, 0.0), (0.0, 0.0, 0.0, 1.0))]


def _case_carpet() -> list:
    """THE LIGHTHOUSE'S BED, REDUCED: `hs_plains_ulf_the_builder_lighthouse
    .blueprint` row 180 lies on a `jute_carpet` whose top stands 0.119 m over
    the floor - over `reseat.FLOOR_TOL_M` - under a `wood_beam` that blocks
    its seat.  The carpet fills every 4-neighbour of the pivot in the bed's
    own band, so a room that counts it as a wall is ONE cell and the bed
    cannot be moved at all."""
    rows = [_seat_row("wood_floor", (float(x), 0.0, float(z)),
                      (0.0, 0.0, 0.0, 1.0))
            for x in (-4, -2, 0, 2, 4) for z in (-4, -2, 0, 2, 4)]
    rows.append(_seat_row("jute_carpet", (0.0, 0.1, 0.0),
                          (0.0, 0.0, 0.0, 1.0)))
    rows.append(_seat_row("bed", (0.0, 0.117, 0.0), (0.0, 0.0, 0.0, 1.0)))
    rows.append(_seat_row("wood_beam", (0.0, 0.83, 0.0),
                          (0.0, 0.7071068, 0.0, 0.7071068)))
    return rows


def _case_seat_verdict(objs, base_y: float) -> dict:
    """WHAT THE EMITTER DOES with this body's beds, and what the GATE then
    says about the seat it wrote - as verdicts, never an assert.

    The emitter is followed exactly: measure, `apply_reseat`, re-measure,
    and refuse on whatever `check_seats` still names.  The gate half is
    `spawn.probe_seat` itself with the seat's own solid skipped by identity,
    called here directly so no neuter of this module can reach it."""
    geom = _SEAT_GEOM
    solids, rows, _u = solids_of(objs, geom, set(geom))
    space, seats = spawn_seats(objs, solids, rows, base_y, geom)
    applied = apply_reseat(objs, reseat_plan(space, seats, solids))
    solids, rows, _u = solids_of(objs, geom, set(geom))
    space, seats = spawn_seats(objs, solids, rows, base_y, geom)
    left = check_seats(seats, reseat_plan(space, seats, solids))
    if left:
        emitter = "REFUSED blocked_seat"
    elif applied:
        emitter = "RESEATS " + ", ".join(
            f"row {e['row']} {e['moved_m']:.2f} m" for e in applied)
    else:
        emitter = "EMITS as authored"
    grid = I.Grid(solids, cell=4.0)
    gate, where = [], []
    for s in solids:
        if s.name not in SEAT_PREFABS:
            continue
        fs = SPAWN.probe_seat(s.name, *s.pos, base_y, grid, 0.49, 1.85, None,
                              skip=[s])
        gate += [f.check for f in fs if not f.ok and not f.advisory]
        where.append((round(s.pos[0], 2), round(s.pos[2], 2)))
    return {"emitter": emitter, "gate": gate or "clear", "bed_at": where}


def _neutered_seat(flag: str, value: str):
    """Set one `_SEAT_*_MODE` flag; returns its undo."""
    real = globals()[flag]
    globals()[flag] = value

    def undo():
        globals()[flag] = real
    return undo



def _case_authored_roof_closures() -> list[str]:
    """The owned repair closes a whole seam and leaves the flue sides open.

    No third-party body is needed: probe the added solids across the measured
    course joint and both chimney ends, including their connections to the
    lintels. These are material-occupancy checks, not piece-count assertions.
    """
    import to_rcon_plan as TP

    geom, build = _gate_corpus()
    addition = HERE / "data" / "repairs" / "fox-0123456-roof-closures.blueprint"
    solids, _rows, _missing = solids_of(TP.read_objects(addition), geom, build)
    cols = ST.Columns(geom)
    bad = []

    def filled(frame, x, y, z):
        px, py, pz = TP.qrot(TP.yaw_quat(frame), (x, y, z))
        return any((sp := cols.span(s, px, pz)) is not None
                   and sp[0] <= py <= sp[1] for s in solids)

    # The 30 mm crack is crossed over the FULL common width of both courses,
    # not just the gate lattice's three observed positions.
    if not all(filled(22.5, 22.25 + i * 0.05, 7.0, z)
               for i in range(77) for z in (21.81, 21.82, 21.84)):
        bad.append("roof course joint still has daylight")
    for z in (27.95, 31.7):
        if not all(filled(67.5, -5.3 + i * 0.05, y, z)
                   for i in range(52) for y in (8.1, 8.8, 9.7)):
            bad.append(f"chimney end {z} still has daylight")
    for z0, y in ((27.95, 9.9), (31.4, 9.75)):
        if not all(filled(67.5, -5.3 + i * 0.05, y, z0 + j * 0.025)
                   for i in range(52) for j in range(13)):
            bad.append(f"chimney end {z0} is disconnected from its lintel")
    # A cap does not cure a fire if it seals the exhaust: both long sides
    # and the vertical shaft below the canopy must stay free of additions.
    if any(filled(67.5, x, y, z)
           for x in (-5.43, -2.60, -4.0)
           for y in (8.2, 8.8, 9.5)
           for z in (28.8, 29.8, 30.8)):
        bad.append("chimney shaft or long-side exhaust was sealed")
    return bad


def _case_workshop_hip_end(*, include_addition: bool = True) -> dict:
    """Missing straight hip course between two corners, not a chimney cap.

    Reconstruct only the regular 2 m roof grid, without the third-party body.
    Removing the owned addition is the shipped-defect neuter: the two corners
    still pass roof_skin, but the enclosed column and entire course stay open.
    """
    import to_rcon_plan as TP

    geom, build = _gate_corpus()
    frame = TP.yaw_quat(-22.5)
    corners = [
        I.mk_solid("wood_roof_ocorner", TP.qrot(frame, (5.0, 8.0, z)),
                   TP.yaw_quat(yaw), geom["wood_roof_ocorner"])
        for z, yaw in ((-1.0, 67.5), (3.0, 337.5))]
    path = HERE / "data" / "repairs" / "halvar-master-refinery-roof-closure.blueprint"
    added = solids_of(TP.read_objects(path), geom, build)[0] if include_addition else []
    cols = ST.Columns(geom)

    def top(x, z):
        px, _py, pz = TP.qrot(frame, (x, 0.0, z))
        return max((sp[1] for s in corners + added
                    if (sp := cols.span(s, px, pz)) is not None), default=None)

    closed = all(top(4.1 + i * 0.1, 0.1 + j * 0.1) is not None
                 for i in range(19) for j in range(19))
    # The hip falls OUTWARD to the existing corners; a reversed panel would
    # cover the same columns while making a gutter and a one-metre step.
    high, low = top(4.1, 1.0), top(5.9, 1.0)
    closed = closed and high is not None and low is not None and high - low > 0.7
    return {
        "course_closed": closed,
        "outlets_open": all(top(x, z) is None for x, z in (
            (-3.758, 0.938), (1.208, 0.906), (6.226, 1.028))),
        "skin_clear": not ST.check_skin(
            [(corners + added, [], "workshop hip end")], cols, geom),
    }


def _case_workshop_gable_pose(*, apply_pose: bool = True) -> bool:
    """Gate verdict for the original gable at frozen-census precision.

    These five neighbouring faces are the complete seam-probe population
    for this triangle in drive 12. A synthetic ridge joins the slice so the
    gate nominates the same triangle, without vendoring the workshop body.
    Exact plan coordinates cleared it; the actual census did not. The
    neuter omits the authored pose, not the gate's seam discriminator.
    """
    import json
    import materialise as M
    import to_rcon_plan as TP

    geom, _build = _gate_corpus()
    obj = TP.Obj("wood_wall_roof_45", (-4.71228, 4.0, -9.528671),
                 (0.0, 0.5555744, 0.0, 0.8314669), (1, 1, 1), "", "")
    if apply_pose:
        entry = json.loads(M.AUTHORED_REPAIRS.read_text("utf-8"))[
            "halvar-master-refinery.blueprint"]
        pose = next(p for p in entry["poses"] if p["row"] == 573)
        TP.apply_authored_poses([obj], [dict(pose, row=0)])
    world_rot = tuple(round(v, 5) for v in TP.qmul(TP.yaw_quat(180), obj.rot))
    target_pos = (-839.9578, 65.66, 5003.581)
    target = I.mk_solid(obj.prefab, target_pos, world_rot, geom[obj.prefab])
    neighbours = [
        ("wood_wall_roof_45", (-839.1924, 65.66, 5001.733),
         (0, 0.55557, 0, 0.83147)),
        ("wood_roof_ocorner_45", (-839.7991, 65.66, 5005.811),
         (0, 0.83147, 0, -0.55557)),
        ("wood_wall_roof_45", (-841.2643, 65.66, 5004.122),
         (0, 0.980785, 0, 0.19509)),
        ("wood_roof_ocorner", (-838.2684, 67.66, 5002.115),
         (0, 0.199368, 0, -0.979925)),
        ("wood_roof_ocorner_45", (-840.8817, 67.66, 5003.198),
         (0, 0.199368, 0, -0.979925)),
    ]
    solids = [target] + [I.mk_solid(n, p, q, geom[n]) for n, p, q in neighbours]
    solids.append(I.mk_solid("wood_roof", (-840.0, 70.0, 5001.0),
                             I.yaw(0), geom["wood_roof"]))
    verdict = ST.orientation_by_building(solids, [], geom)
    return any(tuple(row["pos"]) == target_pos
               for row in verdict["inverted_at"])


def _case_authored_repair_identity() -> list[str]:
    """A repair is bound to both the source body and the addition's content."""
    import json
    import tempfile
    import materialise as M
    import to_rcon_plan as TP

    bad = []
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        source = directory / "hut.blueprint"
        addition = directory / "closure.blueprint"
        source.write_text("#Pieces\nwood_roof;;0;2;0;0;0;0;1;;1;1;1\n")
        addition.write_text("#Pieces\nwoodwall;;0;1;0;0;0;0;1;;1;1;1\n")
        registry = directory / "repairs.json"
        registry.write_text(json.dumps({source.name: {
            "source_sha256": M.sha256(source),
            "addition": addition.name,
            "addition_sha256": M.sha256(addition)}}))
        previous = M.AUTHORED_REPAIRS
        M.AUTHORED_REPAIRS = registry
        try:
            assembled = TP.read_objects(source)
            geom = {"wood_roof": [_CASE_PANEL], "woodwall": [_CASE_WALL]}
            placed, _rows, _missing = solids_of(assembled, geom)
            if ST.Columns(geom).span(placed[-1], 0.0, 0.0) != (-3.5, 3.5):
                bad.append("composed body is missing the closure material")
            identity = {"row": 0, "prefab": "wood_roof", "pos": [1, 2, 0],
                        "from_rot": [0, 0, 0, 1], "to_rot": [0, 1, 0, 0]}
            entries = json.loads(registry.read_text())
            entries[source.name]["poses"] = [identity]
            registry.write_text(json.dumps(entries))
            try:
                TP.read_objects(source)
                bad.append("repair accepted a mismatched source-row identity")
            except ValueError:
                pass
            del entries[source.name]["poses"]
            registry.write_text(json.dumps(entries))
            original = source.read_bytes()
            source.write_bytes(original + b"#changed\n")
            try:
                TP.read_objects(source)
                bad.append("repair accepted a different source body")
            except ValueError:
                pass
            source.write_bytes(original)
            addition.write_text("#Pieces\n")
            try:
                TP.read_objects(source)
                bad.append("repair accepted changed addition bytes")
            except ValueError:
                pass
        finally:
            M.AUTHORED_REPAIRS = previous
    return bad


# ---------------------------------------------------------------------------
# THE ROOF-SKIN CASES.  `check_skin` is the gate's question asked of the body
# AT ITS YAW, and the two files that taught it to take one are reconstructed
# here as the SLICE the verdict depends on - every roof row, so the 0.5 m
# lattice (anchored on the roof's own minimum) sits where it sits on the real
# body, and only the non-roof rows that bear on the gap - at the positions and
# yaws the corpus files author them, in the file's own frame, with no piece
# of either file's geometry beyond what the question reads.  MEASURED: the
# slice carries a hole at exactly the whole-5-degree yaws the whole file does
# (log cabin 10 of 72, woodhouse 2 of 72), and is clean at yaw 0.
#
#   hs_blackforest_tester_meadows_log_cabin   the south-east tower: its cap
#       stands over an open pole storey, 4.441 m above the roof beside it,
#       and the ONLY thing in the gap is a 0.4 m head beam.
#   hs_blackforest_kin_01_woodhouse           the porch cap's eave stands
#       1.2 m below the main roof's overhang at both gable ends.
# ---------------------------------------------------------------------------
_R2 = 2.0 * 2.0 ** 0.5
_SKIN_SLICES = {
    "log_cabin": (
    ('wood_wall_roof', (1.0, 4.2, 0.0), 0),
    ('wood_roof', (1.0, 4.2, 1.0), 270),
    ('wood_roof', (1.0, 4.2, 3.0), 270),
    ('wood_roof', (1.0, 4.2, 5.0), 270),
    ('wood_roof', (1.0, 4.2, 7.0), 270),
    ('wood_roof', (1.0, 4.2, 9.0), 270),
    ('wood_roof', (1.0, 4.2, 11.0), 270),
    ('wood_roof', (1.0, 4.2, 13.0), 270),
    ('wood_roof', (1.0, 4.2, 15.0), 270),
    ('wood_wall_roof', (1.0, 4.2, 16.0), 0),
    ('wood_roof', (7.0, 4.2, 5.0), 90),
    ('wood_roof', (7.0, 4.2, 7.0), 90),
    ('wood_roof', (7.0, 4.2, 9.0), 90),
    ('wood_roof', (7.0, 4.2, 11.0), 90),
    ('wood_roof', (7.0, 4.2, 13.0), 90),
    ('wood_roof', (7.0, 4.2, 15.0), 90),
    ('wood_wall_roof', (7.0, 4.2, 16.0), 180),
    ('wood_wall_roof', (3.0, 5.2, 0.0), 0),
    ('wood_roof', (3.0, 5.2, 1.0), 270),
    ('wood_roof', (3.0, 5.2, 3.0), 270),
    ('wood_roof', (3.0, 5.2, 5.0), 270),
    ('wood_roof', (3.0, 5.2, 7.0), 270),
    ('wood_roof', (3.0, 5.2, 9.0), 270),
    ('wood_roof', (3.0, 5.2, 11.0), 270),
    ('wood_roof', (3.0, 5.2, 13.0), 270),
    ('wood_roof', (3.0, 5.2, 15.0), 270),
    ('wood_wall_roof', (3.0, 5.2, 16.0), 0),
    ('wood_wall_roof', (5.0, 5.2, 0.0), 180),
    ('wood_roof', (5.0, 5.2, 1.0), 90),
    ('wood_roof', (5.0, 5.2, 3.0), 90),
    ('wood_roof', (5.0, 5.2, 5.0), 90),
    ('wood_roof', (5.0, 5.2, 7.0), 90),
    ('wood_roof', (5.0, 5.2, 9.0), 90),
    ('wood_roof', (5.0, 5.2, 11.0), 90),
    ('wood_roof', (5.0, 5.2, 13.0), 90),
    ('wood_roof', (5.0, 5.2, 15.0), 90),
    ('wood_wall_roof', (5.0, 5.2, 16.0), 180),
    ('wood_pole2', (6.0, 5.2, 0.0), 270),
    ('woodwall', (6.0, 5.2, 1.0), 270),
    ('woodwall', (6.0, 5.2, 3.0), 270),
    ('woodwall', (7.0, 5.2, 0.0), 180),
    ('woodwall', (7.0, 5.2, 4.0), 0),
    ('wood_pole2', (8.0, 5.2, 0.0), 270),
    ('woodwall', (8.0, 5.2, 1.0), 90),
    ('woodwall', (8.0, 5.2, 3.0), 90),
    ('wood_pole2', (8.0, 5.2, 4.0), 270),
    ('wood_pole2', (6.0, 7.2, 0.0), 270),
    ('wood_pole2', (6.0, 7.2, 4.0), 270),
    ('wood_pole2', (8.0, 7.2, 0.0), 270),
    ('wood_pole2', (8.0, 7.2, 4.0), 270),
    ('wood_beam', (6.0, 8.2, 1.0), 90),
    ('wood_beam', (6.0, 8.2, 3.0), 90),
    ('wood_beam', (7.0, 8.2, 0.0), 180),
    ('wood_roof_top_45', (7.0, 8.2, 1.0), 270),
    ('wood_roof_top_45', (7.0, 8.2, 3.0), 270),
    ('wood_beam', (7.0, 8.2, 4.0), 180),
    ('wood_beam', (8.0, 8.2, 1.0), 90),
    ('wood_beam', (8.0, 8.2, 3.0), 90),
    ),
    "woodhouse": (
    ('wood_roof_top_45', (-1.0, 2.828427, -1.0), 90),
    ('wood_roof', (5.3244, 3.828427, 2.2168), 157.5),
    ('wood_roof', (5.631, 3.828427, 2.9396), 90),
    ('wood_roof_ocorner_45', (-4.0, 3.828427, 0.0), 180),
    ('wood_roof_ocorner_45', (-4.0, 3.828427, 6.0), 270),
    ('wood_roof_ocorner_45', (4.0, 3.828427, 0.0), 90),
    ('wood_roof_ocorner_45', (4.0, 3.828427, 6.0), 0),
    ('wood_roof', (5.3244, 3.828427, 3.7832), 22.5),
    ('wood_roof_45', (-4.0, 4.828427, 2.0), 270),
    ('wood_roof_45', (-4.0, 4.828427, 4.0), 270),
    ('wood_roof_45', (-2.0, 4.828427, 0.0), 180),
    ('wood_roof_45', (-2.0, 4.828427, 6.0), 0),
    ('wood_roof_45', (0.0, 4.828427, 0.0), 180),
    ('wood_roof_45', (0.0, 4.828427, 6.0), 0),
    ('wood_roof_45', (2.0, 4.828427, 0.0), 180),
    ('wood_roof_45', (2.0, 4.828427, 6.0), 0),
    ('wood_roof_45', (4.0, 4.828427, 2.0), 90),
    ('wood_roof_45', (4.0, 4.828427, 4.0), 90),
    ('wood_roof', (-2.0, 5.828427, 2.0), 180),
    ('wood_roof', (-2.0, 5.828427, 4.0), 0),
    ('wood_roof', (0.0, 5.828427, 2.0), 180),
    ('wood_roof', (0.0, 5.828427, 4.0), 0),
    ('wood_roof', (2.0, 5.828427, 2.0), 180),
    ('wood_roof', (2.0, 5.828427, 4.0), 0),
    ('wood_beam', (-3.0, 5.828427, 2.0), (-0.2706, 0.6533, -0.2706, -0.6533)),
    ('wood_beam', (-3.0, 5.828427, 4.0), (-0.2706, 0.6533, -0.2706, -0.6533)),
    ('wood_beam', (3.0, 5.828427, 2.0), (-0.2706, 0.6533, -0.2706, -0.6533)),
    ('wood_beam', (3.0, 5.828427, 4.0), (-0.2706, 0.6533, -0.2706, -0.6533)),
    ),
}
# The yaw each town building of the Ulfsland regeneration was turned to, and
# the addition that closes the file.
_SKIN_PLACED = {"log_cabin": 70.0, "woodhouse": 80.0}
_SKIN_ADDITION = {
    "log_cabin": "hs_blackforest_tester_meadows_log_cabin-tower-brace.blueprint",
    "woodhouse": "hs_blackforest_kin_01_woodhouse-porch-fascia.blueprint"}


def _skin_slice(which: str, closed: bool) -> list:
    import to_rcon_plan as TP

    # A row's rotation is a yaw in degrees, or a quaternion for the rows the
    # file tilts (the woodhouse's four rafters).
    rows = [TP.Obj(p, pos, I.yaw(d) if isinstance(d, (int, float)) else d,
                   (1.0, 1.0, 1.0), "", "")
            for p, pos, d in _SKIN_SLICES[which]]
    if closed:
        rows += TP._read_objects(HERE / "data" / "repairs"
                                 / _SKIN_ADDITION[which])
    return rows


def _skin_refused_yaws(which: str, closed: bool, step: float = 5.0
                       ) -> list[float]:
    """Every yaw (whole `step` degrees) at which the emitter would refuse the
    slice - asked once per (slice, repaired, step) and held, because each is
    72 measurements of the gate's own instrument."""
    key = (which, closed, step)
    if key not in _SKIN_SWEEPS:
        geom, build = _gate_corpus()
        solids, _rows, _missing = solids_of(_skin_slice(which, closed), geom,
                                            build)
        _SKIN_SWEEPS[key] = [i * step for i in range(int(360 / step))
                             if check_skin(solids, geom, i * step)]
    return _SKIN_SWEEPS[key]


_SKIN_SWEEPS: dict = {}


def _case_skin_is_a_property_of_the_yaw() -> dict:
    """The known-bad state: clean at yaw 0, refused at the placed yaw."""
    out = {}
    for which in _SKIN_SLICES:
        bad = _skin_refused_yaws(which, closed=False)
        out[which] = {"refused_at_placed_yaw": _SKIN_PLACED[which] in bad,
                      "clean_at_yaw_0": 0.0 not in bad,
                      "refused_somewhere": bool(bad)}
    return out


def _case_skin_through_preflight(which: str, yaw: float) -> dict:
    """The same verdict through `preflight()`, the emitter's own entry: the
    refusal, its override flag, and the numbers it carries."""
    geom, build = _gate_corpus()
    refusals, _report = preflight(_skin_slice(which, closed=False), None,
                                  geom, build, yaw_deg=yaw)
    r = [r for r in refusals if r.code == "roof_skin_gap"]
    return {"codes": sorted(x.code for x in refusals),
            "override": r[0].override if r else None,
            "holes": r[0].numbers["holes"] if r else 0,
            "worst_over_allowance": bool(r) and (
                r[0].numbers["worst_step_m"] > r[0].numbers["allowance_m"]),
            "yaw_recorded": r[0].numbers["yaw_deg"] if r else None}


def _case_skin_closed_at_every_yaw() -> dict:
    """The repair: with the owned addition composed, NO yaw refuses."""
    return {which: _skin_refused_yaws(which, closed=True)
            for which in _SKIN_SLICES}


def _neuter_skin_at_the_authored_yaw():
    """The question a blueprint-level chooser can ask without a placement."""
    global SKIN_AT_PLACEMENT_YAW_DEPENDS_ON
    real = SKIN_AT_PLACEMENT_YAW_DEPENDS_ON
    SKIN_AT_PLACEMENT_YAW_DEPENDS_ON = "authored"

    def undo():
        global SKIN_AT_PLACEMENT_YAW_DEPENDS_ON
        SKIN_AT_PLACEMENT_YAW_DEPENDS_ON = real
    return undo


def _case_registry_additions_are_intact() -> list[str]:
    """Every registered addition exists, matches its recorded SHA-256, and
    carries only prefabs the gate's corpus has measured - so an edited
    addition, or one whose pieces the gate cannot read, fails here and not at
    the first emission that composes it."""
    import json
    import materialise as M
    import to_rcon_plan as TP

    geom, _build = _gate_corpus()
    bad = []
    for name, entry in json.loads(M.AUTHORED_REPAIRS.read_text("utf-8")).items():
        add = M.AUTHORED_REPAIRS.parent / entry["addition"]
        if not add.exists():
            bad.append(f"{name}: addition {entry['addition']} is missing")
            continue
        if M.sha256(add) != entry["addition_sha256"]:
            bad.append(f"{name}: addition bytes do not match their SHA-256")
        bad += [f"{name}: {o.prefab} has no measured geometry"
                for o in TP._read_objects(add) if o.prefab not in geom]
    return bad


def selftest() -> int:
    """The repair names EXACTLY the rows the check calls inverted.

    Both directions, because a repair that proposes nothing is as useless as
    one that proposes everything - and because the accepting direction is the
    one that caught the worse half of this defect: on a roof with NOTHING
    wrong, the shipped repair proposed three flips.
    """
    fails = 0

    def want(label, got, expect):
        nonlocal fails
        ok = got == expect
        fails += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got}"
              + ("" if ok else f"  EXPECTED {expect}"))

    print("case: a CLEAN roof pair must produce no flips at all")
    solids, rows = _case_state()
    inv, r = _case_inverted_rows(solids, rows)
    want("check_orientation inverted rows", inv, [])
    want("repair plan", sorted(p["row"] for p in
                               upright_plan(solids, rows, _CASE_GEOM)), [])
    want("NEUTER: the shipped repair (must go red)",
         _case_old_upright_plan(solids, rows, _CASE_GEOM), [0, 1, 2])
    print("   ^ three CORRECT panels proposed for a 180 degree flip on a roof "
          "the check calls clean. That is the failure mode that rotated ten "
          "panels on hs_blackforest_tester_meadows_log_cabin and signed it as "
          "a repair.")

    print("case: one inverted panel -- the repair names IT, and only it")
    solids, rows = _case_state(flip_row=0)
    inv, r = _case_inverted_rows(solids, rows)
    want("check_orientation inverted rows", inv, [0])
    want("repair plan", sorted(p["row"] for p in
                               upright_plan(solids, rows, _CASE_GEOM)), [0])
    want("NEUTER: the shipped repair (must go red)",
         _case_old_upright_plan(solids, rows, _CASE_GEOM), [1, 2])
    print("   ^ it MISSES the inverted panel and flips two correct ones -- "
          "both halves of the defect, in one state.")

    print("case: a roof with NO hole must propose no panels at all")
    solids, rows = _case_hut(hole=False)
    want("check_shelter refusals", len(check_shelter(solids, _SHELTER_GEOM)),
         0)
    want("repair plan", len(shelter_plan(solids, rows, _SHELTER_GEOM)), 0)

    print("case: a 5 x 5 m hole -- the repair closes EVERY open column")
    solids, rows = _case_hut(hole=True)
    ref = check_shelter(solids, _SHELTER_GEOM)
    want("check_shelter refusals", len(ref), 1)
    want("columns the check calls enclosed and open",
         ref[0].numbers["inside_and_open"], 36)
    plan = shelter_plan(solids, rows, _SHELTER_GEOM)
    want("repair plan, one proposal per open column", len(plan), 36)
    want("the proposals stand EXACTLY over those columns",
         sorted((round(p["pos"][0], 1), round(p["pos"][2], 1))
                for p in plan),
         sorted((round(h[0], 1), round(h[2], 1))
                for h in ref[0].numbers["holes_at"]))
    want("check_shelter RE-RUN over solids + proposals",
         _case_holes_left(solids, plan), 0)

    print("case: NEUTER -- holes_at back to its 12-row display sample")
    undo = _neuter_the_hole_sample()
    try:
        nplan = shelter_plan(solids, rows, _SHELTER_GEOM)
        want("NEUTER: repair plan (must go red: 12, not 36)", len(nplan), 12)
        want("NEUTER: holes left after the repair (must go red: 24, not 0)",
             _case_holes_left(solids, nplan), 24)
    finally:
        undo()
    want("discriminator restored: plan is whole again",
         len(shelter_plan(solids, rows, _SHELTER_GEOM)), 36)
    print("   ^ the check is UNTOUCHED under the neuter and still refuses "
          "with the right count. Only the population it hands on is clipped, "
          "and the repair then closes 12 of 36 and reports itself finished -- "
          "17 inverted / 13 proposed / 16 remaining, one layer down.")

    # SUBGRADE_DEPENDS_ON, neutered IN EACH DIRECTION.  The first state has a
    # NON-EMPTY right answer (four skirt rows), so an index that returns
    # nothing cannot pass it; the second has a row that must SURVIVE (the
    # door), so an index that returns everything cannot pass it either.
    print("case: SUB-GRADE -- the buried skirt is DROPPED row by row, and "
          "the door a walker walks through is KEPT")
    hut = _case_subgrade(door=True)
    v = _case_subgrade_verdict(*hut)
    want("emitter", v["emitter"], "DROPS rows [4, 5, 6, 7]")
    want("skirt", v["skirt"], "DROPPED")
    want("door (SURVIVOR)", v["door"], "KEPT")
    want("every dropped row named, with its own depth",
         [(r["row"], r["prefab"], r["depth_m"]) for r in v["plan"]],
         [(i, "wood_wall_half", 0.15) for i in (4, 5, 6, 7)])
    print("case: the drop switched OFF -- the skirt is kept, so the emitter "
          "must REFUSE, never emit it")
    want("emitter", _case_subgrade_verdict(*hut, drop=False)["emitter"],
         "REFUSED subgrade_rows")

    print("case: NEUTER own_top -- the SHIPPED cross-section key "
          "(structures._BURIED_PER_COLUMN)")
    undo = _neuter_own_top()
    try:
        n = _case_subgrade_verdict(*hut)
        want("NEUTER: emitter (must go red: the door goes with the skirt)",
             n["emitter"], "DROPS rows [4, 5, 6, 7, 12]")
        want("NEUTER: door (must go red)", n["door"], "DROPPED")
    finally:
        undo()
    want("discriminator restored: the door stands again",
         _case_subgrade_verdict(*hut)["door"], "KEPT")
    print("   ^ its sill is the only solid on a lattice line, so the slice "
          "reads 0.100 m under while the leaf stands 2 m proud. 941 rows in "
          "134 library bodies read this way, 19 of them on Fresh42.")

    print("case: NEUTER row -> BODY -- a key naming MORE than the row")
    undo = _neuter_row_is_body()
    try:
        n = _case_subgrade_verdict(*hut)
        want("NEUTER: emitter (must go red: an index that returned NOTHING "
             "and a check that therefore refused nothing)",
             n["emitter"], "EMITS every row")
    finally:
        undo()
    want("discriminator restored: the skirt is dropped again",
         _case_subgrade_verdict(*hut)["emitter"], "DROPS rows [4, 5, 6, 7]")
    print("   ^ the skirt TOUCHES the deck it holds up, so as a body it is "
          "never wholly under anything: four invisible ZDOs ship silently.")

    print("case: SUPPORT -- a sunk post carrying a lintel is not a skirt")
    porch = _case_subgrade(post=True)
    v = _case_subgrade_verdict(*porch)
    want("emitter", v["emitter"], "DROPS rows [4, 5, 6, 7]")
    want("post (SURVIVOR)", v["post"], "KEPT")
    want("lintel (SURVIVOR)", v["lintel"], "KEPT")
    undo = _neuter_own_top()
    try:
        n = _case_subgrade_verdict(*porch)
        want("NEUTER own_top: the support guard REFUSES rather than take "
             "the post", n["emitter"], "REFUSED subgrade_structural")
        undo_guard = _neuter_support_guard()
        try:
            n = _case_subgrade_verdict(*porch)
            want("NEUTER own_top AND support: emitter (must go red: the "
                 "post goes and the lintel hangs 1.5 m up on nothing)",
                 n["emitter"], "DROPS rows [4, 5, 6, 7, 12]")
        finally:
            undo_guard()
    finally:
        undo()
    want("discriminator restored: the post stands again",
         _case_subgrade_verdict(*porch)["post"], "KEPT")

    print("case: SUPPORT, the refusing direction -- a sill buried in the high "
          "side of a stepped pad carries a plank over the low side")
    ledge = _case_ledge()
    v = _case_subgrade_verdict(*ledge)
    want("emitter", v["emitter"], "REFUSED subgrade_structural")
    want("sill (SURVIVOR: the drop is refused, so it stays)", v["sill"],
         "KEPT")
    undo = _neuter_support_guard()
    try:
        want("NEUTER support: emitter (must go red: the plank is left 0.85 m "
             "over the low ground on nothing)",
             _case_subgrade_verdict(*ledge)["emitter"], "DROPS rows [0]")
    finally:
        undo()

    print("case: SUPPORT, the accepting direction -- a brace whose toe is in "
          "the ground BETWEEN the lattice lines is carried by the pad")
    braced = _case_subgrade(brace=True)
    v = _case_subgrade_verdict(*braced)
    want("emitter", v["emitter"], "DROPS rows [4, 5, 6, 7]")
    want("brace (SURVIVOR)", v["brace"], "KEPT")
    undo = _neuter_reach_by_column()
    try:
        want("NEUTER support read from a lattice cross-section: emitter "
             "(must go red: a sound hut refused, as looney-swamp was)",
             _case_subgrade_verdict(*braced)["emitter"],
             "REFUSED subgrade_structural")
    finally:
        undo()
    want("discriminator restored: the hut is emitted again",
         _case_subgrade_verdict(*braced)["emitter"],
         "DROPS rows [4, 5, 6, 7]")

    def plan_rows(solids, rows):
        return sorted(p["row"] for p in upright_plan(solids, rows, _CASE_GEOM))

    # UPRIGHT_KEY_DEPENDS_ON, neutered in each direction: one arm loses the
    # published precision and the named row is never handed over; the other
    # loses the per-solid verdict and a sound row is yawed with it.
    print("case: UPRIGHT KEY -- the row the check names is the row the repair "
          "yaws, at x = 23.91052")
    solids, rows = _case_key()
    want("check_orientation inverted rows",
         _case_inverted_rows(solids, rows)[0], [0])
    want("repair plan", plan_rows(solids, rows), [0])
    undo = _neuter_key_rerounded()
    try:
        want("NEUTER the shipped key, re-rounded to 3 (must go red: the "
             "named row is never handed over - blackforest's 4 of 35)",
             plan_rows(solids, rows), [])
    finally:
        undo()
    want("discriminator restored", plan_rows(solids, rows), [0])

    print("case: SHARED KEY -- an inverted panel on a sound panel's pivot")
    solids, rows = _case_shared_key()
    want("instrument: inverted", I.check_orientation(solids, _CASE_GEOM)
         ["inverted"], 1)
    want("repair plan (row 1 is the SURVIVOR)", plan_rows(solids, rows), [7])
    undo = _neuter_key_takes_all()
    try:
        want("NEUTER every solid at the key is taken (must go red: the SOUND "
             "row 1 is yawed and the inverted row 7 is not)",
             plan_rows(solids, rows), [1])
    finally:
        undo()
    want("discriminator restored", plan_rows(solids, rows), [7])

    # THE RIDGE NOMINATES, THE SEAMS DECIDE (`instruments.SEAM_PROBE_M`),
    # asked of the EMITTER: which rows it would yaw, and what it refuses.
    # The hip cap is sandbox-portal-hub's top two courses (drive 2: four
    # caps "uncured" at -0.947 m both ways, body refused); the lower wing is
    # blackforest-walled-base's shape (35 correct panels yawed, roof_skin 3
    # holes / 12 spanned as authored -> 5 / 31 as emitted).
    for label, (solids, geom), shipped_rows in (
            ("hip cap", (ST._hip_cap(), ST._HIP_GEOM), [0, 1, 2, 3]),
            ("lower wing", (ST._two_houses()[0], {"probe_roof": ST._PANEL}),
             [1, 3])):
        print(f"case: {label.upper()} -- correct panels the body's one ridge "
              f"calls inverted")
        rows = list(range(len(solids)))

        def emitter_verdict():
            return (sorted(p["row"] for p in upright_plan(solids, rows, geom)),
                    [r.code for r in check_orientation(solids, geom)])
        want("yaw plan, refusals", emitter_verdict(), ([], []))
        was = I._RIDGE_ALONE_DECIDES
        I._RIDGE_ALONE_DECIDES = True
        try:
            want("NEUTER the ridge decides alone (must go red: correct rows "
                 "yawed or the body refused)", emitter_verdict(),
                 (shipped_rows, ["inverted_panels"]))
        finally:
            I._RIDGE_ALONE_DECIDES = was
        want("discriminator restored", emitter_verdict(), ([], []))

    # REORIENT_DEPENDS_ON: the frame and the solid.
    print("case: UPSIDE DOWN, repairable -- a sound panel's own solid, rolled "
          "face-down")
    solids, rows = _case_upside(True)
    v = I.check_orientation(solids, _CASE_GEOM)
    # Face-down in the SOUND panel's own solid: its seams are the sound
    # panel's, so the ridge's nomination is cleared - upside down, not
    # rising away (`instruments.SEAM_PROBE_M`).
    want("instrument: upside down, inverted", (v["upside_down"],
                                               v["inverted"]), (1, 0))
    want("row 1", _case_reorient_verdict(solids, rows), "REPOSED, solid kept")
    undo = _neuter_yaw_only()
    try:
        want("NEUTER yaw-only poses, all `upright_plan` ever had (must go "
             "red: it stays face-down and the body is refused)",
             _case_reorient_verdict(solids, rows), "AUTHORED")
    finally:
        undo()
    want("discriminator restored", _case_reorient_verdict(solids, rows),
         "REPOSED, solid kept")

    print("case: UPSIDE DOWN, not repairable -- its only upright pose rises "
          "away")
    solids, rows = _case_upside(False)
    v = I.check_orientation(solids, _CASE_GEOM)
    want("instrument: upside down, inverted", (v["upside_down"],
                                               v["inverted"]), (1, 0))
    want("row 1 (SURVIVOR: left as authored)",
         _case_reorient_verdict(solids, rows), "AUTHORED")
    want("the refusal names it, not an empty worst list",
         "Upside down: wood_roof at local (0.00,2.00,2.00)"
         in check_orientation(solids, _CASE_GEOM)[0].detail, True)
    undo = _neuter_any_upright_pose()
    try:
        want("NEUTER any upright pose, solid ignored (must go red: a "
             "different panel is built in its place)",
             _case_reorient_verdict(solids, rows), "REPOSED, solid MOVED")
    finally:
        undo()
    want("discriminator restored", _case_reorient_verdict(solids, rows),
         "AUTHORED")

    # COPIES_DEPENDS_ON, both ways and the occupancy that decides it.
    print("case: CO-LOCATED COPIES -- an offset copy and a twin dropped, a "
          "valley kept")
    cp = _case_copies()
    _f, n = ST.check_duplicates(cp[0], _COPY_GEOM, tol=ST.DUP_TOL_M)
    want("the section refuses, before", (n["same_solid"], n["buried_copy"],
                                         n["shared_pivot_not_a_duplicate"]),
         (1, 1, 1))
    v = _case_copies_verdict(*cp)
    want("emitter", v["emitter"], "DROPS rows [1, 3]")
    want("twin-first (SURVIVOR)", v["twin-first"], "KEPT")
    want("valley-a, valley-b (SURVIVORS)", (v["valley-a"], v["valley-b"]),
         ("KEPT", "KEPT"))
    undo = _neuter_exact_pivot()
    try:
        want("NEUTER a group is one exact pivot (must go red: the 2 cm offset "
             "copy survives and the body is refused, as complete-station-hub "
             "was)", _case_copies_verdict(*cp)["emitter"],
             "REFUSED co_located_rows (1 left)")
    finally:
        undo()
    undo = _neuter_dropped_contain()
    try:
        n = _case_copies_verdict(*cp)
        want("NEUTER a dropped row still contains (must go red: BOTH rows of "
             "each pair go, and the section re-run is clean)",
             (n["emitter"], n["twin-first"]), ("DROPS rows [0, 1, 2, 3]",
                                               "DROPPED"))
    finally:
        undo()
    undo = _neuter_pivot_only()
    try:
        n = _case_copies_verdict(*cp)
        want("NEUTER sharing a pivot is a copy (must go red: half the valley "
             "roof goes, and the section re-run is clean)",
             (n["emitter"], n["valley-b"]), ("DROPS rows [1, 3, 5]",
                                             "DROPPED"))
    finally:
        undo()
    want("discriminator restored", _case_copies_verdict(*cp)["emitter"],
         "DROPS rows [1, 3]")

    # PROPS_DEPENDS_ON: the other solids, and only the physical ones.
    print("case: SUNK PROPS -- the hearth is lifted, the table under the "
          "forge is not")
    want("props", _case_props_verdict(_PROPS_PHYSICAL),
         {"hearth": "SEATED 0.50 m",
          "piece_table_round": "LEFT, enters ['forge']"})
    undo = _neuter_lift_blind()
    try:
        want("NEUTER lift whatever stands there (must go red: the table is "
             "driven into the forge)",
             _case_props_verdict(_PROPS_PHYSICAL)["piece_table_round"],
             "SEATED 0.55 m")
    finally:
        undo()
    want("NEUTER the gate's raw corpus, trigger volumes in (must go red: the "
         "hearth stays sunk, and a sunk fire is off)",
         _case_props_verdict(_PROPS_RAW)["hearth"],
         "LEFT, enters ['piece_artisanstation']")
    want("discriminator restored", _case_props_verdict(_PROPS_PHYSICAL)
         ["hearth"], "SEATED 0.50 m")

    # SEAT_DEPENDS_ON = ("gate_probe", "own_solid", "room", "other_seats"),
    # one neuter each.
    print("case: SPAWN SEAT -- the drive-3 woodhouse bed under its own rail "
          "(7 beds, 14 gate refusals) is re-seated and the gate clears it")
    want("woodhouse", _case_seat_verdict(_case_woodhouse(), _WOODHOUSE_BASE_Y),
         {"emitter": "RESEATS row 2 0.25 m", "gate": "clear",
          "bed_at": [(2.29, 0.96)]})
    undo = _neutered_seat("_SEAT_CHECK_MODE", "off")
    try:
        want("NEUTER the seat never asked - the drive-3 emitter (must go red: "
             "the bed ships at 0.432 m of room / 0.449 m of headroom)",
             _case_seat_verdict(_case_woodhouse(), _WOODHOUSE_BASE_Y),
             {"emitter": "EMITS as authored",
              "gate": ["seat_clearance", "seat_headroom"],
              "bed_at": [(2.29, 0.71)]})
    finally:
        undo()
    want("discriminator restored",
         _case_seat_verdict(_case_woodhouse(), _WOODHOUSE_BASE_Y)["emitter"],
         "RESEATS row 2 0.25 m")
    print("case: SPAWN SEAT, nothing wrong -- the same room without its rail, "
          "and the drive-3 loft piece_bed02 on its rugs, are emitted as "
          "authored")
    want("woodhouse, no rail (SURVIVOR)",
         _case_seat_verdict(_case_woodhouse(beam=False), _WOODHOUSE_BASE_Y),
         {"emitter": "EMITS as authored", "gate": "clear",
          "bed_at": [(2.29, 0.71)]})
    want("loft piece_bed02 (SURVIVOR)",
         _case_seat_verdict(_case_loft(), _HUB_BASE_Y),
         {"emitter": "EMITS as authored", "gate": "clear",
          "bed_at": [(3.7, 16.59)]})
    undo = _neutered_seat("_SEAT_SKIP_MODE", "none")
    try:
        want("NEUTER the seat's own solid left in the probe (must go red: a "
             "clear piece_bed02 refused for its own headboard)",
             _case_seat_verdict(_case_loft(), _HUB_BASE_Y)["emitter"],
             "REFUSED blocked_seat")
    finally:
        undo()
    want("discriminator restored",
         _case_seat_verdict(_case_loft(), _HUB_BASE_Y)["emitter"],
         "EMITS as authored")
    print("case: SPAWN SEAT, the room -- the nearest clear cell is through "
          "the alcove wall; the re-seat stays inside")
    want("alcove", _case_seat_verdict(_case_alcove(), 0.0),
         {"emitter": "RESEATS row 0 2.75 m", "gate": "clear",
          "bed_at": [(0.0, -2.75)]})
    undo = _neutered_seat("_SEAT_ROOM_MODE", "off")
    try:
        want("NEUTER no room (must go red: the bed is put 1.75 m west, on the "
             "far side of its own wall)",
             _case_seat_verdict(_case_alcove(), 0.0)["bed_at"],
             [(-1.75, 0.0)])
    finally:
        undo()
    want("discriminator restored",
         _case_seat_verdict(_case_alcove(), 0.0)["bed_at"], [(0.0, -2.75)])
    print("case: SPAWN SEAT, the other seats -- a move that clears one bed "
          "by burying another is not a repair")
    want("platform", _case_seat_verdict(_case_platform(), 0.0),
         {"emitter": "RESEATS row 1 1.00 m", "gate": "clear",
          "bed_at": [(-0.75, 0.0), (1.85, 0.0)]})
    undo = _neutered_seat("_SEAT_OTHERS_MODE", "self")
    try:
        want("NEUTER a move judged on its own seat alone (must go red: the "
             "clear ground bed is buried and the body refused)",
             _case_seat_verdict(_case_platform(), 0.0),
             {"emitter": "REFUSED blocked_seat",
              "gate": ["seat_clearance", "seat_headroom"],
              "bed_at": [(1.0, 0.0), (1.85, 0.0)]})
    finally:
        undo()
    want("discriminator restored",
         _case_seat_verdict(_case_platform(), 0.0)["emitter"],
         "RESEATS row 1 1.00 m")
    print("case: SPAWN SEAT, the room -- what the bed was authored lying on "
          "is not a wall of its room (reseat.ROOM_WALLS_DEPENDS_ON)")
    want("carpet", _case_seat_verdict(_case_carpet(), 0.0969),
         {"emitter": "RESEATS row 26 1.75 m", "gate": "clear",
          "bed_at": [(0.0, -1.75)]})
    real_walls = RS.ROOM_WALLS_DEPENDS_ON
    RS.ROOM_WALLS_DEPENDS_ON = ()
    try:
        want("NEUTER the carpet walls the bed in (must go red: a one-cell room, "
             "the lighthouse's row 180)",
             _case_seat_verdict(_case_carpet(), 0.0969),
             {"emitter": "REFUSED blocked_seat",
              "gate": ["seat_penetration", "seat_headroom"],
              "bed_at": [(0.0, 0.0)]})
    finally:
        RS.ROOM_WALLS_DEPENDS_ON = real_walls
    want("discriminator restored",
         _case_seat_verdict(_case_carpet(), 0.0969)["emitter"],
         "RESEATS row 26 1.75 m")

    want("authored roof closure: full seam, end screens, smoke outlets",
         _case_authored_roof_closures(), [])
    want("authored repair identity: base and addition must match",
         _case_authored_repair_identity(), [])
    want("workshop hip end: close the course, keep smelter outlets",
         _case_workshop_hip_end(),
         {"course_closed": True, "outlets_open": True, "skin_clear": True})
    want("NEUTER missing workshop addition: skin alone cannot see the hole",
         _case_workshop_hip_end(include_addition=False),
         {"course_closed": False, "outlets_open": True, "skin_clear": True})
    want("workshop original gable: gate at frozen-census precision",
         _case_workshop_gable_pose(), False)
    want("NEUTER authored gable pose: gate detects the original inversion",
         _case_workshop_gable_pose(apply_pose=False), True)

    print("case: the roof skin is a property of the file AT A YAW")
    want("slices: refused at the placed yaw, clean at yaw 0",
         _case_skin_is_a_property_of_the_yaw(),
         {w: {"refused_at_placed_yaw": True, "clean_at_yaw_0": True,
              "refused_somewhere": True} for w in _SKIN_SLICES})
    want("preflight() at the log cabin's placed yaw 70 REFUSES it, naming "
         "the override and the numbers",
         _case_skin_through_preflight("log_cabin", 70.0),
         {"codes": ["roof_skin_gap"], "override": "--allow-roof-skin-gaps",
          "holes": 1, "worst_over_allowance": True, "yaw_recorded": 70.0})
    want("preflight() at the woodhouse's placed yaw 80 REFUSES it",
         _case_skin_through_preflight("woodhouse", 80.0),
         {"codes": ["roof_skin_gap"], "override": "--allow-roof-skin-gaps",
          "holes": 1, "worst_over_allowance": True, "yaw_recorded": 80.0})
    want("preflight() at yaw 0 passes both: only the placement knows",
         [_case_skin_through_preflight(w, 0.0)["codes"] for w in _SKIN_SLICES],
         [[], []])
    undo = _neuter_skin_at_the_authored_yaw()
    try:
        want("NEUTER the check asked at the file's own yaw (must go red): "
             "the known-bad log cabin and woodhouse PASS",
             [_case_skin_through_preflight("log_cabin", 70.0)["codes"],
              _case_skin_through_preflight("woodhouse", 80.0)["codes"]],
             [[], []])
    finally:
        undo()
    print("   ^ the shipped emitter asked nothing of the roof skin, and a "
          "chooser that asks at yaw 0 gets the same answer: both files are "
          "clean there and carry a hole at the yaw their street turns them to")
    want("authored closures: no whole-5-degree yaw refuses either file",
         _case_skin_closed_at_every_yaw(), {w: [] for w in _SKIN_SLICES})
    want("NEUTER the addition (must go red): the same slices refuse again",
         {w: bool(_skin_refused_yaws(w, closed=False))
          for w in _SKIN_SLICES}, {w: True for w in _SKIN_SLICES})
    want("registry: every addition is intact and readable by the gate",
         _case_registry_additions_are_intact(), [])

    print(f"\n{fails} case(s) failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(selftest())
