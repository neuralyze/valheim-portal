#!/usr/bin/env python3
"""THE LIPSCHITZ-CONE DISPOSAL, AS A TOOL THAT READS THE GATE'S OWN REFUSALS.

WHY IT EXISTS, AND IT IS THE CARRY-FORWARD HALF OF A NIGHT'S WORK.

The cone disposal was PROVEN twice on Ulfsland and both times it lived in a
scratch script under /tmp with the world's coordinates typed into it -
`ROAD_Y = 35.290`, a two-element `lines` list holding the vestvik approach,
a `cluster.json` produced by a second scratch script.  A fresh seed inherits
NOTHING from that: the arithmetic is general and the instrument was not.

So this is the same arithmetic with the world taken out of it.  Its INPUT is
an `accept` artefact - the gate's own refusal rows - and its OUTPUT is a
`roads/regrade.py --cuts` file.  Nothing between the two is typed by hand.

THE DISPOSAL, AND WHY A CONE RATHER THAN A CAP.

    f[v] = min over anchors s of ( t[s] + CONE_PER_M * dist(s, v) )
    H'   = min(H, f)

`f` is CONE_PER_M-Lipschitz on the mesh, so any edge of H' is either an edge
of H (pre-existing) or bounded by CONE_PER_M times its own run.  A cone
therefore CANNOT CAUSE a rim step - that is structural, not measured, and it
is the whole reason to prefer it.  A FLAT CAP over the same population closes
the same refusals and `junction.rim_transfer` REFUSES it: MEASURED on the
vestvik cluster, 4 steps CAUSED, worst 1.1252 m.

EIGHT-CONNECTED, AND THE CONNECTIVITY IS NOT A DETAIL.  With L1 propagation
two diagonal neighbours may differ by 2 * CONE_PER_M = 1.540 m while the
judge allows STEP_LIMIT_PER_M * sqrt(2) = 1.1047 m - MEASURED, a 4-connected
cone left (-905,1895)->(-904,1894) at 1.1324 m, over by 0.0275 m.  A cone's
connectivity MUST MATCH THE JUDGE'S, so a diagonal is charged its own
CONE_PER_M * sqrt(2) = 1.0889 m and both conventions hold at once.

THE DOMAIN IS GROWN UNTIL THE CUT STOPS TOUCHING IT, never fixed by hand.  A
cut that lands on the boundary of the window it was solved in is a repair
sized to the WINDOW rather than to the defect, and the scratch script could
only warn about that because its window was an argument.  Here it is an
output: the window grows until no cut sits on its rim, or the tool refuses
with the radius it reached.

WHAT IT WILL NOT DO.  It proposes CUTS ONLY.  A refusal whose remedy is a
RAISE - a carriageway BELOW its destination, a riser whose high side is the
destination's own deck - is REFUSED HERE WITH ITS NUMBER, because every
sanctioned write path in this tree is cut-only (`regrade.py` asserts it,
`harbours.restoration_set` raises on it) and a planner that quietly emitted
a fill would be the one place the rule is not enforced.

    cone.py --findings <accept.json> --row <section>:<check>:<match> ...
            --name <id> --out <cuts.json> [--report <json>] --actor <you>

Then: `regrade.py plan --cuts <cuts.json> --name <id> --out <plan>` and
`regrade.py build --plan <plan> --actor <you>`.
"""
from __future__ import annotations

import argparse
import heapq
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
for _p in (str(HERE), str(JUMPSTART), str(JUMPSTART / "ledger"),
           str(JUMPSTART / "terraform")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import tcdata                                              # noqa: E402
import ribbon as ribbonmod                                 # noqa: E402
import junction as junctionmod                             # noqa: E402
import applied as appliedmod                               # noqa: E402
from accept import instruments as AI                       # noqa: E402
import worldseed                                           # noqa: E402

# CONE_WORLD_DEPENDS_ON: the world whose forbidden extents are being read.
# This was the literal "Ulfsland" at the `Ledger.open` below AND at the
# `forbidden_extents` world argument, so a cone solved on a fresh seed was
# judged against Ulfsland's own no-build ground.  argument > $VH_WORLD >
# default, and with the environment unset it resolves what the literal did.
WORLD = worldseed.world()

LIMIT_PER_M = AI.STEP_LIMIT_PER_M
WATER_LEVEL_M = AI.WATER_LEVEL
# THE CONE'S OWN SLOPE, STRICTLY INSIDE THE JUDGE'S.  Solving ONTO a limit
# leaves a write whose legality depends on the last decimal of a float, and
# the gate re-reads the ground through a different path than the planner.
#
# READ FROM `accept.instruments`, NEVER COPIED.  The gate sizes every step
# refusal's ownership footprint from THIS number through
# `instruments.disposal_depth`, so a planner that solved at a different
# slope than the classifier assumed would propose cuts the guard refuses -
# or decline cuts it permits - and the disagreement would be invisible in
# both artefacts.  One number, one home.
CONE_PER_M = AI.DISPOSAL_SLOPE_PER_M
# `applied.AUTHOR_TOLERANCE_M`: below this the ground does not move and the
# write would take ownership of a sample without changing it.
MIN_CUT_M = appliedmod.AUTHOR_TOLERANCE_M
# The window starts here and doubles until no cut touches its rim.
R0 = 4
R_MAX = 64


# ---------------------------------------------------------------------------
# WHAT AN ANCHOR IS A FUNCTION OF, DECLARED, AND CHECKED IN EACH DIRECTION.
#
# A cone DESCENDS TOWARD ITS ANCHORS, so the anchor is the single number
# that decides how deep every cut in the disposal goes.  This tool shipped
# taking it as `the applied ground at the low end of the named edge` - one
# term, and a SUBSET of what the quantity actually depends on.
#
# MEASURED, Ulfsland 7632b3a5674842bd, `settlements/terminus_step`
# road_T9_wttown_dockshore B - the 2.174 m step at (677,-94)->(678,-94):
# the low end (678,-94) stands at applied 28.8265 m, which is 1.1735 m
# UNDER the 30.0 m water plane, because it is the CHANNEL BED of a
# DECLARED BRIDGE - `roads/crossings.yaml:declared` T9-wttown-dockshore-
# sb01, `required_deck_y: 30.6`, `gap_min_y: 28.826`,
# `drop_into_the_channel_m: 2.174`, the refusal's own number.  Anchored at
# the mud the cone solved the bank down to 29.5965 m and put THREE cut
# vertices under the sea, worst 0.4035 m.  `ribbon.water_sign` cannot see
# that (its population is keyed on the GENERATED height and a cut can
# never raise anything) and `cone.submerged_by_a_cut` catches it AFTER the
# fact.  The CAUSE is here: GROUND UNDER A DECK IS SEABED, and the surface
# the refusal was measured against is the DECK.
#
# So the anchor is the HIGHEST WALKING SURFACE ANY AUTHORITY DECLARES AT
# THAT VERTEX, and the three terms are named rather than implied:
#
#   applied_ground       what is standing there now, `Surface.h`
#   declared_deck_datum  a DECLARED crossing's own `required_deck_y` where
#                        its deck covers this vertex - read through
#                        `boardwalk.crossing_corpus`, the same corpus the
#                        gate judges
#   water_plane          `instruments.WATER_LEVEL`.  A carriageway anchor
#                        under the sea produces a carriageway under the
#                        sea; every anchor this tool takes is a WALKING
#                        SURFACE (the low end of a walked approach, or a
#                        cap on a travelled way), never a dredge target -
#                        a dredge is a CUT VERTEX, which is a different
#                        set and is exempted in `submerged_by_a_cut`.
#
# THE MAX, WHICH IS THE SAFE DIRECTION BY CONSTRUCTION: an anchor can only
# be RAISED by this rule, a higher anchor can only make the cone SHALLOWER,
# and a shallower cut can only take less ground.  Nothing this rule does
# can deepen a disposal.
#
# `selftest_anchor` neuters EACH of these three in turn and requires that
# each one FLIPS at least one case and that at least one case SURVIVES
# every neuter - so a declaration that names a term nothing depends on
# fails, and a neuter cannot pass by emptying the population.
ANCHOR_DEPENDS_ON = ("applied_ground", "declared_deck_datum", "water_plane")


def anchor_height(v, applied_y: float, datums: dict | None,
                  neuter: tuple = ()) -> tuple:
    """The walking surface at `v`, as `(height, which_term_won, considered)`.

    `neuter` drops a named term from `ANCHOR_DEPENDS_ON`; with every term
    dropped there is no anchor at all and `None` is returned, which the
    caller must refuse rather than default.
    """
    cands = []
    if "applied_ground" not in neuter and applied_y is not None:
        cands.append(("applied_ground", float(applied_y)))
    if "declared_deck_datum" not in neuter:
        d = (datums or {}).get(tuple(v))
        if d is not None:
            cands.append(("declared_deck_datum", float(d)))
    if "water_plane" not in neuter:
        cands.append(("water_plane", float(WATER_LEVEL_M)))
    if not cands:
        return None, "none", tuple(k for k in ANCHOR_DEPENDS_ON
                                   if k not in neuter)
    src, t = max(cands, key=lambda kv: kv[1])
    return t, src, tuple(k for k, _ in cands)


def declared_decks() -> list[dict]:
    """Every DECLARED deck in the corpus the gate judges, as a datum and a
    rectangle, read through `boardwalk.crossing_corpus`.

    ONE CORPUS, NOT A MIRROR.  `roads/boardwalk.py::crossing_corpus` is the
    `declared` UNION `detected` dedup by `crossing_id` that
    `accept/sections/roads.py::crossing_spans` counts, and
    `fill_from_the_contract` is what recovers `required_deck_y` for a
    declaration that states only its banks' `road_deck_y`.  A private
    re-read of the YAML here would be the second population for one
    obligation, which is the defect class this project keeps finding.

    THE DATUM PRECEDENCE IS THE GATE'S - `contract_deck_y`, then
    `required_deck_y`, then `bank_a.road_deck_y` - and where a refusal row
    names its own `deck_datum_y` the two are COMPARED rather than trusted
    (see `_deck_caps`).
    """
    import boardwalk as bw                               # noqa: PLC0415
    records, corpus = bw.crossing_corpus()
    out = []
    for raw in records:
        c, _why = bw.fill_from_the_contract(raw, corpus)
        a = (c.get("bank_a") or {}).get("xz")
        b = (c.get("bank_b") or {}).get("xz")
        y = (c.get("contract_deck_y") or c.get("required_deck_y")
             or (c.get("bank_a") or {}).get("road_deck_y"))
        if a is None or b is None or y is None:
            continue
        w = c.get("ribbon_width_m")
        out.append(dict(
            id=c.get("crossing_id"), status=c.get("status"),
            a=(float(a[0]), float(a[1])), b=(float(b[0]), float(b[1])),
            deck_y=float(y),
            # THE DECK'S OWN MEASURED HALF-WIDTH, never a nominal: the
            # declaration's `ribbon_width_m` when it carries one, else
            # `boardwalk.DECK_HALF_WIDTH_M`, which that module measures
            # off the solid `assemble.bridge` actually places.
            half_w=(float(w) / 2.0 if w else float(bw.DECK_HALF_WIDTH_M)),
            half_w_from=("the declaration's own ribbon_width_m"
                         if w else "boardwalk.DECK_HALF_WIDTH_M")))
    return out


def deck_datums(points, decks: list[dict] | None = None) -> tuple[dict, dict]:
    """`{vertex: deck_y}` for every vertex a declared deck stands over.

    Inside the span (no overhang) and inside the deck's own half-width.  On
    an overlap the HIGHER datum wins, which is again the safe direction: a
    higher anchor is a shallower cut.
    """
    decks = declared_decks() if decks is None else decks
    out: dict = {}
    why: dict = {}
    for d in decks:
        (ax, az), (bx, bz) = d["a"], d["b"]
        ux, uz = bx - ax, bz - az
        L = math.hypot(ux, uz)
        if L <= 0.0:
            continue
        ux, uz = ux / L, uz / L
        for v in points:
            x, z = float(v[0]), float(v[1])
            t = (x - ax) * ux + (z - az) * uz
            if t < -1e-9 or t > L + 1e-9:
                continue
            if abs((x - ax) * uz - (z - az) * ux) > d["half_w"] + 1e-9:
                continue
            key = tuple(v)
            if d["deck_y"] > out.get(key, -math.inf):
                out[key] = d["deck_y"]
                why[key] = (f"{d['id']} declares a deck at y={d['deck_y']} "
                            f"over this vertex ({d['half_w']:.3f} m "
                            f"half-width from {d['half_w_from']})")
    return out, why


def declared_stations(d: dict) -> list[tuple[float, float]]:
    """A declared crossing's own sample stations, BY THE GATE'S OWN RULE -
    `accept/sections/roads.py::crossing_spans`, `n = round(span / SAMPLE_M)`
    endpoints included.  The count is cross-checked against the refusal
    row's published `samples` in `_deck_caps`, so a rule that drifted from
    the gate's REFUSES rather than sizes a cut off a different population.
    """
    (ax, az), (bx, bz) = d["a"], d["b"]
    span = math.hypot(bx - ax, bz - az)
    n = max(1, int(round(span / AI.SAMPLE_M)))
    return [(ax + (bx - ax) * k / n, az + (bz - az) * k / n)
            for k in range(n + 1)]


# THE GROUND A DECK STANDS ON IS NOT THE GROUND THE GATE SAMPLES, AND THE
# DIFFERENCE IS THE OVERHANG.
#
# `accept/sections/roads.py::crossing_spans` samples BANK TO BANK, endpoints
# included, and `crossing_premise` judges "is the bed above the deck" over
# exactly that set.  `crossings/assemble.py::bridge` places its tiles at the
# same station pitch and runs ONE STATION BEYOND EACH BANK, which is what
# `boardwalk.CORRIDOR_OVERHANG_M` records.  So a bed can be clear over every
# declared station and still stand above the deck UNDER THE OUTERMOST TILE,
# and the gate's premise arm cannot see it: its population stops at the bank.
#
# MEASURED, Ulfsland, T9-wttown-dockshore-sb01 after terrain_write seq 5429:
# the declared set x=677..683 at z=-94 is CLEAR at every one of its 7 metres
# against the 30.600 m deck - the gate's premise refusal is gone - while
# `boardwalk`'s abutment guard REFUSES the deck at bank_a by -0.400 m,
# because the outermost tile stands 1.000 m outboard at (676,-94) where the
# applied bed is still 31.000, and (675,-94) behind it is 31.000 too.  TWO
# METRES, INVISIBLE TO THE POPULATION THAT NAMED THE ROW.
#
# So the cap population is the DECK'S OWN CORRIDOR CENTRELINE and the
# declared set is a SUBSET of it, carried separately so the cross-check
# against the refusal's published `samples` still has something to compare.
DECK_CAP_POPULATION_DEPENDS_ON = (
    "the declared bank-to-bank stations (`accept.crossing_spans`'s own rule, "
    "which is what the gate's premise refusal counts), UNION the OVERHANG "
    "stations the builder places beyond each bank "
    "(`boardwalk.CORRIDOR_OVERHANG_M`). NOT the declared set alone - a deck "
    "refused at its outermost tile is refused by a metre no declared station "
    "covers. NOT the corridor's full HALF-WIDTH either: the lateral tiles are "
    "`boardwalk`'s seating guard's population, not this one, and capping them "
    "would take ground no check named - MEASURED on T9, the full 3.000 m "
    "half-width puts 21 more vertices of a 31.000 m bench inside the set and "
    "`seating` passes on every one of them.")


def deck_corridor_stations(d: dict, overhang_m: float | None = None) -> list:
    """The declared stations EXTENDED BY THE BUILDER'S OWN OVERHANG, along
    the centreline.  See `DECK_CAP_POPULATION_DEPENDS_ON`."""
    import boardwalk as bw                               # noqa: PLC0415
    over = (bw.CORRIDOR_OVERHANG_M if overhang_m is None else overhang_m)
    (ax, az), (bx, bz) = d["a"], d["b"]
    span = math.hypot(bx - ax, bz - az)
    if span <= 0.0:
        return []
    ux, uz = (bx - ax) / span, (bz - az) / span
    ox, oz = ax - ux * over, az - uz * over
    total = span + 2 * over
    n = max(1, int(round(total / AI.SAMPLE_M)))
    return [(ox + ux * total * k / n, oz + uz * total * k / n)
            for k in range(n + 1)]


# ---------------------------------------------------------------------------
def select_rows(doc: dict, specs: list[str]) -> list[tuple[str, dict]]:
    """`<section>:<check>:<substring of the message>` -> the refusal rows.

    AN AMBIGUOUS SELECTION IS REFUSED, NOT RESOLVED.  A planner that silently
    took the first of two matching refusals would size a cut off one defect
    and file it under the other's name.
    """
    out: list[tuple[str, dict]] = []
    for spec in specs:
        try:
            sec, check, match = spec.split(":", 2)
        except ValueError:
            raise SystemExit(
                f"REFUSED: --row {spec!r} is not <section>:<check>:<match>")
        pool = (doc.get("sections", {}).get(sec, {}).get("findings") or [])
        hit = [f for f in pool
               if f.get("check") == check and match in (f.get("message") or "")
               and not f.get("advisory")]
        if not hit:
            raise SystemExit(
                f"REFUSED: --row {spec!r} matched NO refusal in "
                f"{sec}/{check} over {len(pool)} finding(s).")
        if len(hit) > 1:
            raise SystemExit(
                f"REFUSED: --row {spec!r} matched {len(hit)} refusals, and a "
                f"cut sized off the wrong one is filed under the right one's "
                f"name:\n  " + "\n  ".join(f["message"][:110] for f in hit))
        out.append((f"{sec}/{check}", hit[0]))
    return out


def edges_of(nums: dict) -> tuple[list[dict], list[dict]]:
    """Every transition this refusal names, as `(edges, unrepairable)`.

    THE SET, NOT ONLY THE WORST.  `repair_population` is the set the checks
    publish precisely so a repair is as wide as the check that named it; a
    cut sized off `at` alone moves the named edge under the limit and leaves
    the next one over.  A row that publishes only its worst is still read -
    it is one edge - but the count is carried so the caller can see which
    kind of row it got.
    """
    # A REFUSAL WHOSE REMEDY IS A RAISE IS REFUSED HERE, AT THE TOP, AND BY
    # THE FIELD THE GATE PUBLISHES FOR IT.  `repair_direction` is computed
    # by `settlements.repair_direction` off the signs the checks already
    # measure; a planner that re-derived it would be the fifth copy of a
    # question with one right answer.
    if nums.get("repair_direction") == "raise":
        return [], [dict(why=("the gate states this refusal's "
                              "repair_direction is RAISE: its high side is "
                              "the destination's own standing geometry, not "
                              "ground, and this tool cuts only"),
                         judged_as=nums.get("judged_as"),
                         error_m=nums.get("error_m"),
                         rise_m=nums.get("rise_m"),
                         destination_datum=(nums.get("destination_datum")
                                            or nums.get("datum_m")))]
    # A DECLARED CROSSING WHOSE OWN BED STANDS ABOVE ITS OWN DECK.  This
    # row names NO lattice edge, no riser and no `cap_m` - it names a
    # CROSSING and a DECK DATUM and a COUNT - so every arm below refused
    # it as "nothing to place a cut at" and the one ACTIONABLE refusal
    # whose remedy this tool performs was unreachable from its own input.
    #
    # THE POPULATION IS NOT `where`.  `where` is the WORST metre, and a
    # cut sized off the worst leaves the next one over - the defect this
    # whole file exists downstream of.  The gate carries the set on its
    # span row as `bed_above_deck_at` and does not publish it, so the set
    # is RE-DERIVED from the crossing's own declaration and RE-MEASURED
    # live in `_deck_caps`, where the derived station count is checked
    # against this row's published `samples` and the derived datum against
    # its published `deck_datum_y`.  A derivation that has drifted from
    # the gate's REFUSES; it never sizes a cut off a different population.
    if (nums.get("deck_datum_y") is not None and nums.get("crossing")
            and isinstance(nums.get("bed_above_deck_samples"), int)):
        if nums["bed_above_deck_samples"] <= 0:
            return [], [dict(why=("this crossing row names no metre whose "
                                  "bed stands above its declared deck, so "
                                  "there is no ground for a cut to take"),
                             crossing=nums.get("crossing"),
                             deck_datum_y=nums.get("deck_datum_y"))]
        return [dict(kind="deck", crossing=str(nums["crossing"]),
                     a=None, target=float(nums["deck_datum_y"]),
                     samples_published=nums.get("samples"),
                     bed_above_deck_published=nums["bed_above_deck_samples"],
                     worst_published_m=nums.get("worst_bed_above_deck_m"))], []
    pop = nums.get("repair_population")
    rows = list(pop) if isinstance(pop, list) and pop else None
    if rows is None:
        at = nums.get("at")
        rows = [dict(at=at, step_m=nums.get("step_m"),
                     rise_m=nums.get("rise_m"), datum_m=nums.get("datum_m"),
                     judged_as=nums.get("judged_as"),
                     onto=nums.get("onto"))] if at else []
    cap = nums.get("cap_m")
    edges, bad = [], []
    for r in rows:
        at = r.get("at")
        if isinstance(at, list) and len(at) == 4:
            edges.append(dict(kind="edge",
                              a=(float(at[0]), float(at[1])),
                              b=(float(at[2]), float(at[3])),
                              step_m=r.get("step_m"),
                              over_by_m=r.get("over_by_m")))
        elif (isinstance(at, list) and len(at) == 2
              and r.get("rise_m") is not None):
            # A RISER: one location, signed.  `rise_m = datum - ground`, so
            # a POSITIVE rise is the DESTINATION standing above the walk's
            # last footfall and no cut of ground closes it.
            rise = float(r["rise_m"])
            datum = r.get("datum_m")
            if rise > 0:
                bad.append(dict(why="the DESTINATION is the high side of "
                                    "this riser, so the remedy is a RAISE "
                                    "of ground or a rebuild of the piece, "
                                    "and this tool cuts only",
                                at=at, rise_m=rise, onto=r.get("onto")))
            elif datum is None:
                # THE ABSOLUTE TARGET OR NOTHING.  `ground + rise_m` would
                # recover a datum that has drifted by however much the
                # ground moved since the gate read it, and a planner that
                # cut to a drifted datum would be sizing off a stale delta.
                bad.append(dict(why="this riser names no `datum_m`, so the "
                                    "absolute height its cut must reach is "
                                    "unknown and reconstructing it from a "
                                    "stale delta would drift with the "
                                    "ground", at=at, rise_m=rise))
            else:
                edges.append(dict(kind="cap",
                                  a=(float(at[0]), float(at[1])),
                                  target=float(datum) + CONE_PER_M,
                                  step_m=r.get("step_m"),
                                  over_by_m=r.get("over_by_m")))
        elif (isinstance(at, list) and len(at) == 2
              and isinstance(cap, (int, float))):
            # A BERM SAMPLE: an absolute cap the whole travelled way must
            # come under, published by `terminus_berm` as `cap_m`.
            edges.append(dict(kind="cap", a=(float(at[0]), float(at[1])),
                              target=float(cap),
                              step_m=r.get("over_by_m"),
                              over_by_m=r.get("over_by_m")))
        else:
            bad.append(dict(why="this row names no 1 m lattice edge, no "
                                "signed riser and no capped sample, so "
                                "there is nothing to place a cut at",
                            row=r))
    return edges, bad


# ---------------------------------------------------------------------------
def judge(H: dict) -> tuple[list[dict], int]:
    """Every transition over the limit, under BOTH of the conventions this
    tree judges a diagonal by - because it has two and they disagree.

    THE TWO, READ OFF THE SHIPPING CODE, NOT CHOSEN:

      RUN     `accept/sections/roads.py` walks its chain leg by leg and
              compares the rise against `STEP_LIMIT_PER_M * d` where `d`
              is the LEG'S OWN LENGTH - so a diagonal is one 1.414 m move
              allowed 1.1047 m.
      CORNER  `settlements.check_arrival` and `junction.rim_transfer` both
              refuse to let a walker travel a diagonal: he crosses TWO 1 m
              mesh edges through the easier of the cell's two shared
              corners, and EACH of them is judged against the plain
              0.7813 m.

    Neither dominates the other.  A 1.05 m diagonal whose corner sits
    halfway is CLEAR under RUN and CLEAR under CORNER; the same 1.05 m
    diagonal whose corner sits level with one end is CLEAR under RUN and
    0.27 m OVER under CORNER.  A planner that picked one would publish a
    disposal the other half of the gate refuses, so this judges both and
    an edge is over when EITHER says so, with `by` naming which.

    THE SCRATCH SCRIPT THIS REPLACES JUDGED RUN ONLY, and that is exactly
    why it concluded a four-connected cone was unsafe: under RUN a
    4-connected cone lets diagonal neighbours differ by 2 * 0.770 =
    1.540 m against 1.1047 m.  Under CORNER a 4-connected cone is fine.
    EIGHT-CONNECTED at CONE_PER_M * sqrt(2) = 1.0889 m satisfies BOTH at
    once - 1.0889 < 1.1047 for RUN, and every 1 m edge still at 0.770 for
    CORNER - which is the reason the connectivity is not negotiable.
    """
    over, n = [], 0
    for (x, z), y in H.items():
        for dx, dz in ((1, 0), (0, 1), (1, 1), (1, -1)):
            q = (x + dx, z + dz)
            if q not in H:
                continue
            n += 1
            run = math.hypot(dx, dz)
            rise = H[q] - y
            by, worst = [], 0.0
            if abs(rise) > LIMIT_PER_M * run + 1e-9:
                by.append("run")
                worst = max(worst, abs(rise) - LIMIT_PER_M * run)
            if dx and dz:
                routes = []
                for c in ((x + dx, z), (x, z + dz)):
                    yc = H.get(c)
                    if yc is not None:
                        routes.append(max(abs(yc - y), abs(H[q] - yc)))
                if routes and min(routes) > LIMIT_PER_M + 1e-9:
                    by.append("corner")
                    worst = max(worst, min(routes) - LIMIT_PER_M)
            if by:
                over.append(dict(a=[x, z], b=list(q), run_m=round(run, 4),
                                 rise_m=round(rise, 4), by=by,
                                 over_by_m=round(worst, 4)))
    return over, n


def cone(anchors: dict, domain: set) -> dict:
    """`f[v] = min_s ( t[s] + CONE_PER_M * dist(s, v) )`, EIGHT-CONNECTED.

    Weighted, so Dijkstra rather than a BFS relaxation: a diagonal costs
    CONE_PER_M * sqrt(2) and a 1 m edge costs CONE_PER_M, which is the same
    mesh the judge walks and the reason the result cannot CAUSE a rim step.
    """
    steps = [(dx, dz, CONE_PER_M * math.hypot(dx, dz))
             for dx in (-1, 0, 1) for dz in (-1, 0, 1) if (dx, dz) != (0, 0)]
    f = dict(anchors)
    q = [(t, v) for v, t in anchors.items()]
    heapq.heapify(q)
    while q:
        fv, v = heapq.heappop(q)
        if fv > f.get(v, math.inf) + 1e-12:
            continue
        for dx, dz, w in steps:
            u = (v[0] + dx, v[1] + dz)
            if u not in domain:
                continue
            cand = fv + w
            if cand < f.get(u, math.inf) - 1e-12:
                f[u] = cand
                heapq.heappush(q, (cand, u))
    return f


# ---------------------------------------------------------------------------
class Surface:
    """The live applied surface and the seed under it, on the 1 m lattice.

    One reader, memoised, so the domain can be grown without re-reading what
    was already read.  `zone_patches` is asked for the zones a window needs
    the moment the window asks for them.
    """

    def __init__(self, actor: str, seed: str, name: str):
        self.live = appliedmod.Applied(actor=actor)
        self.seed = seed
        self.scratch = ribbonmod.SCRATCH
        self.scratch.mkdir(parents=True, exist_ok=True)
        self.name = name
        self.patches: dict = {}
        self._zones: set = set()
        self._h: dict = {}
        self._g: dict = {}

    def _need(self, zones: set) -> None:
        new = {z for z in zones if z not in self._zones}
        if not new:
            return
        self._zones |= new
        self.patches.update(ribbonmod.zone_patches(
            sorted(new), self.seed,
            self.scratch / f"cone_{self.name}_{len(self._zones)}.bin"))

    def read(self, pts) -> None:
        pts = [p for p in pts if p not in self._h]
        if not pts:
            return
        self._need({tcdata.zone_of(x, z) for (x, z) in pts})
        for (x, z) in pts:
            got = self.live.surface_at(self.patches, x, z)
            gen = appliedmod.generated_at(self.patches, x, z)
            self._h[(x, z)] = None if got is None else float(got[0])
            self._g[(x, z)] = None if gen is None else float(gen)

    def h(self, x, z):
        self.read([(x, z)])
        return self._h[(x, z)]

    def g(self, x, z):
        self.read([(x, z)])
        return self._g[(x, z)]


def writer_owners():
    """The `flatten=FORBIDDEN` extent set, THROUGH THE FUNCTION THE GUARD
    ITSELF CALLS - `settlements.forbidden_extents` over the live chain.

    TWO INSTRUMENTS ANSWER "who owns this ground" IN THIS TREE AND THEY DO
    NOT AGREE, so a planner must say which one it asked.
    `ribbon.protected_piece_positions` is the ROAD EMITTER'S keep-out and
    gives `harbour_place seq 24` a bounding disc of r=3.00 m;
    `forbidden_extents` is what `Ledger.append` refuses on and gives the
    SAME record r=0.0 - a point at the piece - plus the site's pieces.

    MEASURED, T13-temple-harboursouth station 423 on save 245daa7f: the
    ribbon set FREEZES the high vertex (14,-255) outright, while the
    writer's nearest owned point is 1.4314 m away and the repair footprint
    for the 0.0132 m cut that closes the step is 1.431 m - so the writer
    PERMITS it, by 0.8 mm, and at a depth of 0.0140 m it would not.  A
    planner that published only the ribbon answer would report "nothing to
    do here" over a row the guard would have accepted, and one that
    published only the writer answer would propose a cut the road emitter
    keeps out of.  BOTH ARE READ AND BOTH ARE REPORTED.
    """
    import sys as _sys                                     # noqa: PLC0415
    p = str(JUMPSTART / "ledger")
    if p not in _sys.path:
        _sys.path.insert(0, p)
    import writer as writermod                             # noqa: PLC0415
    from accept.sections import settlements as SE          # noqa: PLC0415
    led = writermod.Ledger.open(WORLD, actor="cone")
    live = SE.live_records(led.path)
    return SE.forbidden_extents(live, led.path.parent, WORLD), SE


def writer_verdict(v, depth_m, owners, SE) -> dict:
    """What `Ledger.append`'s own guard says about a cut of `depth_m` at
    `v`, read through `settlements.repair_ownership` - the SAME call the
    gate classifies every refusal with."""
    got = SE.repair_ownership(float(v[0]), float(v[1]), max(depth_m, 0.0),
                              owners, LIMIT_PER_M)
    return dict(xz=[v[0], v[1]], depth_m=round(depth_m, 4),
                repair_footprint_m=got["repair_footprint_m"],
                ground_owned_by=got["ground_owned_by"],
                ground_owner_distance_m=got["ground_owner_distance_m"],
                # THE DEBT TRAVELS WITH THE VERDICT.  A cut this planner
                # calls permitted may still stand on a `flatten: RESEAT`
                # claim - a roadside lamp - and `Ledger.append` refuses it
                # unless the write NAMES those seqs in `params.reseats`.
                # Publishing the verdict without the debt would hand
                # `regrade.py` a cut the guard refuses at the console, which
                # is the exact failure this function's own docstring is
                # about.
                reseats_required=got["reseats_required"],
                repairable_by_terrain_write=got[
                    "repairable_by_terrain_write"])


def _frozen(domain: set, owners, SE, probe_depth_m: float
            ) -> tuple[set, dict]:
    """Every vertex this disposal MAY NOT MOVE, decided by the guard that
    will actually judge the write, with the road emitter's wider keep-out
    read beside it as advice.

    THE GUARD, NOT A NEIGHBOURING EMITTER'S KEEP-OUT.  The write this tool
    feeds is `roads/regrade.py`, and the thing that refuses a regrade over
    owned ground is `Ledger.append` - read here through
    `settlements.repair_ownership`, which is the SAME call the gate
    classifies every refusal with.  `ribbon.protected_piece_positions` is
    the ROAD EMITTER'S keep-out; `regrade.py` has never consulted it and a
    planner that froze on it would decline repairs the guard permits.

    MEASURED, T13-temple-harboursouth station 423 on save 245daa7f: the
    ribbon set freezes the high vertex (14,-255) inside a r=3.00 m
    bounding disc around a harbour piece at (13.62,-256.38); the writer's
    nearest owned point is 1.4314 m away and the footprint of the 0.0132 m
    cut that closes the step is 1.431 m, so the guard PERMITS it - by 0.8
    mm - and would refuse 0.0140 m.  Freezing on ribbon published "0 cut
    sites" over a row the writer would have taken.

    THE PROBE DEPTH IS THE DEEPEST CUT THIS RUN COULD MAKE, so the freeze
    is never looser than the cut it gates.  `repair_ownership`'s footprint
    grows with depth, so asking at the worst depth and then re-asking each
    PROPOSED cut at its own depth is monotone in the safe direction.
    """
    zones = sorted({tcdata.zone_of(x, z) for (x, z) in domain})
    prot = ribbonmod.protected_piece_positions(zones)
    ppos = [(float(a), float(b)) for a, b in prot["pieces"]]
    pdisc = [(float(a), float(b), float(r)) for a, b, r in prot["discs"]]
    keepout = {}
    for (x, z) in domain:
        for (px, pz) in ppos:
            if max(abs(px - x), abs(pz - z)) <= 1.0:
                keepout[(x, z)] = "a protected piece stands within 1.0 m"
                break
        else:
            for (dx, dz, r) in pdisc:
                if math.hypot(dx - x, dz - z) <= r:
                    keepout[(x, z)] = (f"inside the ROAD EMITTER's keep-out: "
                                       f"a protected record's bounding disc "
                                       f"r={r:.2f} m at ({dx:.2f},{dz:.2f})")
                    break
    frozen, why = set(), {}
    for v in domain:
        got = SE.repair_ownership(float(v[0]), float(v[1]),
                                  max(probe_depth_m, 0.0), owners,
                                  LIMIT_PER_M)
        if got["repairable_by_terrain_write"] is False:
            frozen.add(v)
            why[v] = (f"the writer's own guard owns this ground: "
                      f"{got['ground_owned_by']} at "
                      f"{got['ground_owner_distance_m']} m, inside the "
                      f"{got['repair_footprint_m']} m footprint of a "
                      f"{probe_depth_m:.4f} m cut")
    return frozen, dict(pieces=len(ppos), discs=len(pdisc), why=why,
                        road_emitter_keepout=keepout,
                        road_emitter_keepout_count=len(keepout),
                        probe_depth_m=round(probe_depth_m, 4))


def _deck_caps(e: dict, surf: Surface) -> dict:
    """A DECLARED CROSSING row turned into one absolute cap per metre whose
    LIVE bed stands above its own declared deck.

    THREE CROSS-CHECKS, AND EACH ONE REFUSES RATHER THAN SIZES.  The
    station set is re-derived from the declaration and the overage
    re-measured off the live applied surface, so all three answers could
    drift from the gate's:

      the crossing must BE in the corpus            else REFUSED by name
      the derived station count must equal the row's published `samples`
      the corpus datum must equal the row's published `deck_datum_y`

    THE CUT TARGET IS THE DECLARED DATUM AND NOT THE TOLERANCE.  The gate
    refuses a metre at `bed - deck > CROSSING_BURIAL_MAX_M` (0.30 m, the
    same number `boardwalk.BURIAL_MAX_M` refuses a buried deck tile at), so
    cutting TO `deck + 0.30` would close the refusal by landing exactly on
    its limit - and nothing in this tree writes onto a limit.  The
    declaration states an absolute, `required_deck_y`, and that is the
    target: a deck that bridges something has its bed AT OR BELOW the deck,
    not 0.30 m short of it.
    """
    from accept.sections import roads as RD              # noqa: PLC0415
    decks = {d["id"]: d for d in declared_decks()}
    d = decks.get(e["crossing"])
    if d is None:
        return dict(caps=[], bad=[dict(
            why=(f"{e['crossing']} is not in the crossings corpus this tool "
                 f"reads (boardwalk.crossing_corpus over "
                 f"roads/crossings.yaml `declared` UNION `detected`), so "
                 f"its declared stations cannot be derived"),
            crossing=e["crossing"])])
    stations = declared_stations(d)
    pub = e.get("samples_published")
    if pub is not None and len(stations) != int(pub):
        return dict(caps=[], bad=[dict(
            why=(f"this tool derives {len(stations)} station(s) for "
                 f"{e['crossing']} where the refusal publishes {int(pub)}: "
                 f"two populations for one obligation, and a cut sized off "
                 f"the wrong one is filed under the right one's name"),
            crossing=e["crossing"], derived=len(stations), published=int(pub))])
    if abs(d["deck_y"] - float(e["target"])) > 1e-6:
        return dict(caps=[], bad=[dict(
            why=(f"the corpus declares {e['crossing']}'s deck at "
                 f"y={d['deck_y']} and the refusal was measured against "
                 f"y={e['target']}: a cut to a datum the gate did not judge "
                 f"closes nothing it named"),
            crossing=e["crossing"], corpus_deck_y=d["deck_y"],
            published_deck_y=float(e["target"]))])
    # THE DECLARED SET IS THE CROSS-CHECK; THE CORRIDOR IS THE POPULATION.
    # See `DECK_CAP_POPULATION_DEPENDS_ON`: the builder runs one station
    # beyond each bank and the gate's premise arm stops at the bank, so a
    # cap sized on the declared set alone leaves the outermost tile buried.
    declared_lat = sorted({(int(round(x)), int(round(z)))
                           for x, z in stations})
    corridor = deck_corridor_stations(d)
    lattice = sorted({(int(round(x)), int(round(z))) for x, z in corridor})
    surf.read(lattice)
    beds = {v: surf.h(*v) for v in lattice}
    unread = sorted(v for v, b in beds.items() if b is None)

    def _over(pool):
        return sorted(((v, beds[v] - d["deck_y"]) for v in pool
                       if beds.get(v) is not None
                       and beds[v] - d["deck_y"] > RD.CROSSING_BURIAL_MAX_M),
                      key=lambda t: -t[1])

    over = _over(lattice)
    over_declared = _over(declared_lat)
    census = dict(
        crossing=e["crossing"], deck_datum_y=d["deck_y"],
        stations_derived=len(stations),
        stations_declared_on_the_lattice=len(declared_lat),
        corridor_stations_on_the_lattice=len(lattice),
        corridor_overhang_m=round(
            (len(lattice) - len(declared_lat)) / 2.0 * AI.SAMPLE_M, 3),
        stations_unread=len(unread),
        samples_published=pub,
        bed_above_deck_measured_now=len(over),
        bed_above_deck_on_the_declared_stations_now=len(over_declared),
        bed_above_deck_in_the_overhang_now=len(over) - len(over_declared),
        bed_above_deck_published=e.get("bed_above_deck_published"),
        worst_measured_now_m=round(over[0][1], 4) if over else 0.0,
        worst_published_m=e.get("worst_published_m"),
        burial_tolerance_m=RD.CROSSING_BURIAL_MAX_M,
        population=DECK_CAP_POPULATION_DEPENDS_ON,
        cut_target_is="the declaration's own required_deck_y, never the "
                      "burial tolerance: nothing here writes onto a limit",
        wet_now=sum(1 for b in beds.values()
                    if b is not None and b < WATER_LEVEL_M))
    if not over:
        return dict(caps=[], census=census, bad=[dict(
            why=(f"{e['crossing']}: the refusal published "
                 f"{e.get('bed_above_deck_published')} metre(s) above the "
                 f"deck and NONE of the {len(lattice)} metre(s) of its own "
                 f"corridor stands above y={d['deck_y']} on the live "
                 f"surface today - the ground moved since the gate read it, "
                 f"so this artefact is stale and a cut sized off it would "
                 f"take ground nothing named"),
            **census)])
    caps = [dict(kind="cap", a=(float(v[0]), float(v[1])),
                 target=d["deck_y"], crossing=e["crossing"],
                 in_the_overhang=v not in set(declared_lat),
                 step_m=round(over_by, 4), over_by_m=round(over_by, 4))
            for v, over_by in over]
    return dict(caps=caps, census=census, bad=[])


# ---------------------------------------------------------------------------
def solve(rows: list[tuple[str, dict]], surf: Surface) -> dict:
    """The cone over every named transition, on a window grown until the cut
    stops touching it."""
    named, unrepairable, anchors_src = [], [], []
    deck_census = []
    for label, f in rows:
        nums = f.get("numbers") or {}
        edges, bad = edges_of(nums)
        # A DECLARED-CROSSING ROW IS EXPANDED AGAINST THE LIVE SURFACE
        # BEFORE ANYTHING ELSE, because its repair population is not in
        # the artefact: the row names a CROSSING and a DATUM and a COUNT,
        # and the metres are derived from the declaration and re-measured
        # here.  See `_deck_caps` for the three cross-checks that refuse a
        # derivation which has drifted from the gate's.
        expanded = []
        for e in edges:
            if e.get("kind") != "deck":
                expanded.append(e)
                continue
            got = _deck_caps(e, surf)
            if got.get("census"):
                deck_census.append(got["census"])
            bad = bad + got["bad"]
            expanded.extend(got["caps"])
        edges = expanded
        for b in bad:
            unrepairable.append(dict(row=label, message=f.get("message"), **b))
        for e in edges:
            named.append((label, e))
        anchors_src.append(dict(row=label, where=f.get("where"),
                                edges=len(edges), unrepairable=len(bad),
                                message=(f.get("message") or "")[:160]))
    if not named:
        return dict(verdict="REFUSED", why="no named transition is cuttable",
                    unrepairable=unrepairable, anchors_named=anchors_src,
                    declared_decks_read=deck_census)

    seeds = set()
    for _label, e in named:
        seeds.add(e["a"])
        if e.get("b"):
            seeds.add(e["b"])

    # THE WALKING SURFACE AT EVERY VERTEX THIS DISPOSAL MAY ANCHOR ON.
    # Read ONCE, over the seeds only, because an anchor is only ever taken
    # at a seed - see `ANCHOR_DEPENDS_ON`.
    datums, datum_why = deck_datums(seeds)

    # THE GUARD'S OWN OWNER SET, READ ONCE.  Every freeze decision and
    # every per-cut re-check below asks THIS, so the plan and the guard
    # cannot disagree about who owns the ground.
    owners, SE = writer_owners()
    # THE DEEPEST CUT THIS RUN COULD MAKE, WHICH IS THE OVERAGE AND NEVER
    # THE STEP.  `repair_ownership`'s footprint is `depth / slide_limit`
    # plus a lattice diagonal, so asking it about the whole 0.794 m step
    # instead of the 0.0132 m that closes it inflates the footprint from
    # 1.431 m to 2.430 m and freezes a vertex the guard permits.  MEASURED
    # on T13 station 423: that exact substitution published "0 cut sites"
    # over a row `Ledger.append` would have taken, which is the same
    # sentence as sizing a repair off the step rather than the overage -
    # a defect `check_arrival` fixed one check over and this tool
    # re-introduced in the freeze.
    probe = max([float(e.get("over_by_m")
                       if e.get("over_by_m") is not None
                       else max(float(e.get("step_m") or 0.0)
                                - LIMIT_PER_M, 0.0))
                 for _l, e in named] or [0.0])

    r = R0
    while True:
        domain = {(sx + dx, sz + dz) for (sx, sz) in seeds
                  for dx in range(-r, r + 1) for dz in range(-r, r + 1)}
        surf.read(sorted(domain))
        H0 = {v: surf.h(*v) for v in domain if surf.h(*v) is not None}
        unread = len(domain) - len(H0)
        frozen, prot = _frozen(set(H0), owners, SE, probe)

        # ---- THE ANCHORS, EACH ONE A REFUSAL RESTATED AS A CONSTRAINT ----
        anchors, anchor_why = {}, {}

        def anchor(v, t, tag):
            if v in H0 and t < anchors.get(v, math.inf):
                anchors[v] = t
                anchor_why[v] = tag

        missing, raised = [], []
        for _label, e in named:
            a = e["a"]
            if e["kind"] == "cap":
                # AN ABSOLUTE TARGET the vertex must come under: a berm
                # sample's `cap_m`, or a riser's destination datum plus the
                # cone's own slope.  Never reconstructed from a delta.
                if a not in H0:
                    missing.append(dict(at=list(a), why="unreadable"))
                    continue
                anchor(a, float(e["target"]),
                       "cap: an absolute height this vertex must come under")
                continue
            b = e["b"]
            ya, yb = H0.get(a), H0.get(b)
            if ya is None or yb is None:
                missing.append(dict(at=[*a, *b], why="unreadable"))
                continue
            lo = a if ya < yb else b
            # THE WALKING SURFACE, NOT THE GROUND.  See `ANCHOR_DEPENDS_ON`:
            # the low end of a step across a bridge channel is SEABED, and a
            # cone anchored on it solves the carriageway down into the sea.
            t, src, considered = anchor_height(lo, H0[lo], datums)
            if t is None:                       # every term neutered away
                missing.append(dict(at=[*lo], why="no anchor term survives"))
                continue
            if src != "applied_ground":
                raised.append(dict(
                    at=[lo[0], lo[1]], applied_ground_m=round(H0[lo], 4),
                    anchored_at_m=round(t, 4),
                    raised_by_m=round(t - H0[lo], 4), anchored_on=src,
                    why=datum_why.get(lo, f"the {src}"),
                    named_edge=[*a, *b]))
            anchor(lo, t, f"step: the LOW end of an over-limit edge, at its "
                          f"{src}")
        if missing:
            return dict(verdict="REFUSED",
                        why=("a named transition could not be read off the "
                             "live applied surface"),
                        missing=missing, unrepairable=unrepairable,
                        anchors_named=anchors_src)

        f = cone(anchors, set(H0))
        H = {k: (min(H0[k], f[k]) if k in f else H0[k]) for k in H0}

        # A FROZEN VERTEX IS PUT BACK, AND THE COST IS MEASURED, NOT ASSUMED.
        restored = sorted(k for k in frozen if H[k] < H0[k] - 1e-9)
        for k in restored:
            H[k] = H0[k]

        cuts = {k: round(H[k] - H0[k], 4)
                for k in H if H0[k] - H[k] >= MIN_CUT_M}
        for k in list(H):
            if k not in cuts:
                H[k] = H0[k]

        on_rim = [k for k in cuts
                  if any((k[0] + dx, k[1] + dz) not in H0
                         for dx in (-1, 0, 1) for dz in (-1, 0, 1))]
        if not on_rim or r >= R_MAX:
            break
        r = min(r * 2, R_MAX)

    fills = {k: v for k, v in cuts.items() if v > 0}
    if fills:
        return dict(verdict="REFUSED", why=f"the cone proposed {len(fills)} "
                    f"FILL(s) in a cut-only disposal", fills=list(fills)[:8])
    # AND EVERY PROPOSED CUT IS RE-ASKED AT ITS OWN DEPTH.  The freeze
    # above was sized to the deepest cut this run could make, which is a
    # bound and not an answer; `repair_ownership`'s footprint grows with
    # depth, so a shallow cut may be permitted where the bound refused.
    # This is the answer, per vertex, and it is what the guard will say.
    blocked = []
    for k, d in sorted(cuts.items()):
        got = writer_verdict(k, abs(d), owners, SE)
        if got["repairable_by_terrain_write"] is False:
            blocked.append(got)
    if blocked:
        return dict(
            verdict="REFUSED",
            why=("the cone needs to cut ground a flatten=FORBIDDEN record "
                 "owns; `Ledger.append` would refuse it and a planner that "
                 "proposed it anyway would be sending a repair at a guard"),
            frozen_cuts=blocked[:8], frozen_cuts_total=len(blocked),
            unrepairable=unrepairable, anchors_named=anchors_src)
    # THE WATER SIGN, THROUGH THE SHIPPING INSTRUMENT, AS A DELTA.
    #
    # The scratch script this replaces asserted its own drain test -
    # `seed under the plane AND after at or above it` - and that predicate
    # is a STATE, not a delta: for a CUT it fires on ground that was
    # already above the plane before the write and stays above it, which
    # is not a drain by anybody's definition.  MEASURED here on
    # road_T9_wttown_dockshore B: it refused 9 vertices as "first drains"
    # where every one of them is LOWERED and none crosses the plane in
    # either direction.  `ribbon.water_sign` is the function the ledger's
    # own emitters are judged by and it takes the three surfaces -
    # generated, after, now - precisely so the question is asked about the
    # MOVEMENT.  A planner with a private copy of a guard is a planner
    # that disagrees with the guard.
    wsign = ribbonmod.water_sign(
        {(int(x), int(z)): (surf.g(x, z) or 0.0) for (x, z) in cuts},
        {(int(x), int(z)): H[(x, z)] for (x, z) in cuts},
        {(int(x), int(z)): H0[(x, z)] for (x, z) in cuts})
    if wsign["verdict"] == "REFUSED":
        return dict(verdict="REFUSED",
                    why=f"the water sign refuses this cut: "
                        f"{wsign['population']}",
                    water_sign={k: v for k, v in wsign.items()
                                if not isinstance(v, (list, dict))},
                    unrepairable=unrepairable, anchors_named=anchors_src)
    # AND WHETHER THE WAY IS STILL DRY AFTER IT, WHICH `water_sign` DOES
    # NOT ASK AND DOES NOT CLAIM TO.
    #
    # `water_sign` guards WATER REMOVAL and it is right to: a cut adds
    # water, so a cut is always clear to it.  Its population is `the
    # samples whose GENERATED height is below the plane` and its verdict
    # is about whether this write RAISES one of them out.  Neither the
    # population nor the verdict is the question a carriageway has, and
    # the question a carriageway has is WILL A WALKER BE WADING: that
    # depends on the AFTER surface and on nothing else.
    #
    # THE TWO KEYS COME APART EXACTLY WHERE A PRIOR WRITE RAISED THE
    # GROUND OUT OF THE SEA, which is what a shore road IS.  MEASURED,
    # Ulfsland 7632b3a5674842bd, this tool's own solve for
    # `settlements/terminus_step` road_T9_wttown_dockshore B - the
    # 2.174 m step at (677,-94)->(678,-94):
    #
    #   9 cut sites, worst -1.4035 m, 6.417 m3, rim CLEAR 0 CAUSED,
    #   named transition CLOSED, and `water_sign` CLEAR - "0 are RAISED
    #   to or above it by this write".
    #   Every one of the 9 stands at applied 31.000 m today and every one
    #   of them is GENERATED below the plane (seed 28.857 to 29.390),
    #   because `terrain_write road_T9_wttown_dockshore` (seq 234) raised
    #   the whole shore out of the water.  So all 9 are inside
    #   `water_sign`'s population and none of them can trip it - a cut
    #   never raises anything.
    #   THREE OF THEM END UP UNDER THE 30.0 m PLANE:
    #       (677,-94)  31.000 -> 29.5965   0.4035 m under
    #       (678,-93)  31.000 -> 29.5965   0.4035 m under
    #       (677,-93)  31.000 -> 29.9154   0.0846 m under
    #   A carriageway 0.40 m under the sea is not a repair, and the one
    #   instrument asked about water said CLEAR.
    #
    # WHY THE CONE CUT THAT DEEP, because the guard is the symptom's
    # check and the cause is worth naming: `solve` anchors a step's LOW
    # END at its APPLIED GROUND, and at (678,-94) the ground is the
    # seabed at ~28.83 m while the walker's surface there is the dock's
    # standing top.  The refusal itself says so - `terminus_step` measured
    # "the approach to the STANDING TOP of wood_floor" - and the edge this
    # tool was handed carries two lattice coordinates and no datum, so the
    # cone solved down to the mud under the deck instead of to the deck.
    # The remedy at that vertex is 0.400 m to the declared deck datum of
    # 30.600, not 1.4035 m; MEASURED independently by
    # `crossings/assemble.py`, which refuses T9 with "walking surface
    # 0.400 m BELOW applied ground 31.000".  UNTIL AN EDGE CAN CARRY ITS
    # DESTINATION'S DATUM THIS TOOL MUST REFUSE THE ROW RATHER THAN
    # PUBLISH THE OVER-CUT.
    # THROUGH `submerged_by_a_cut`, so `selftest_submersion` drives THE
    # SHIPPING RULE rather than a copy of it - the same reason
    # `water_sign` lives in `ribbon` and not inline in its caller.
    # THE GENERATED SURFACE IS READ AND HANDED IN - as a witness to
    # DRYNESS for the exemption, never as the population key.  A vertex
    # the seed drew dry and a prior write drowned is NOT seabed, and
    # deepening it is not a dredge.
    gen_at = {k: surf.g(*k) for k in cuts}
    exemption_unread = sorted(k for k, v in gen_at.items() if v is None)
    drowned = [dict(r, seed=None if gen_at.get(tuple(r["xz"])) is None
                    else round(gen_at[tuple(r["xz"])], 4))
               for r in submerged_by_a_cut(H0, H, cuts, gen_at)]
    # AND THE REFUSAL NAMES ITS CAUSE, NOT ONLY ITS SYMPTOM.  A cone
    # descends toward its ANCHORS, so a disposal that ends up under the
    # plane almost always anchored under it: the low end of a named edge
    # is taken at its APPLIED GROUND, and where that ground is seabed
    # under a deck the cone solves to the mud rather than to the deck.
    # An author reading "3 vertices are wet" has to go and find that out;
    # an author reading "the anchor itself is 1.17 m under the plane" is
    # already holding it.
    wet_anchors = sorted(
        (dict(xz=[v[0], v[1]], applied_m=round(t, 4),
              under_the_plane_m=round(WATER_LEVEL_M - t, 4),
              anchored_as=anchor_why.get(v))
         for v, t in anchors.items() if t < WATER_LEVEL_M - 1e-9),
        key=lambda r: -r["under_the_plane_m"])
    if drowned:
        return dict(
            verdict="REFUSED",
            why=(f"this disposal puts {len(drowned)} of {len(cuts)} cut "
                 f"vertex(es) UNDER the {WATER_LEVEL_M} m water plane, "
                 f"worst {drowned[0]['under_the_plane_m']} m at "
                 f"{drowned[0]['xz']}, and every one of them stands ABOVE "
                 f"the plane today - a carriageway a walker has to wade is "
                 f"not a repair.  `ribbon.water_sign` cannot see this: its "
                 f"population is keyed on the GENERATED height and this "
                 f"shore was raised out of the sea by a prior write, so "
                 f"all of these samples are inside its population and a "
                 f"cut can never trip it"
                 + (f".  THE CAUSE: {len(wet_anchors)} of {len(anchors)} "
                    f"anchor(s) this cone descends toward are THEMSELVES "
                    f"under the plane, worst "
                    f"{wet_anchors[0]['under_the_plane_m']} m at "
                    f"{wet_anchors[0]['xz']} - the low end of a named edge "
                    f"is taken at its APPLIED GROUND, and ground under a "
                    f"deck is seabed, so the cone solved to the mud "
                    f"instead of to the walking surface the refusal "
                    f"measured against.  An edge that cannot carry its "
                    f"destination's datum cannot be disposed of by a cut"
                    if wet_anchors else "")),
            wet_anchors=wet_anchors, wet_anchors_total=len(wet_anchors),
            anchors=len(anchors),
            submerged_by_this_cut=drowned,
            submerged_by_this_cut_total=len(drowned),
            water_plane_m=WATER_LEVEL_M,
            submerged_exemption_keyed_on=(
                "EVER-DRY: a vertex is exempt as a dredge only when the "
                "applied surface AND the generated surface both say it was "
                "already water"
                if not exemption_unread else
                f"EVER-DRY, but the generated surface was unreadable at "
                f"{len(exemption_unread)} of {len(cuts)} cut vertex(es), so "
                f"the exemption fell back to the applied surface there - "
                f"UNREAD IS NOT DRY and those rows may be under-refused"),
            submerged_exemption_generated_unread_total=len(exemption_unread),
            water_sign={k: v for k, v in wsign.items()
                        if not isinstance(v, (list, dict))},
            cut_sites=len(cuts),
            cut_worst_m=round(min(cuts.values()), 4) if cuts else 0.0,
            unrepairable=unrepairable, anchors_named=anchors_src)

    before, edges_judged = judge(H0)
    after, _ = judge(H)
    # WHAT THIS DISPOSAL WAS ASKED TO CLOSE, AND WHAT IT LEFT.  Three
    # populations, kept apart on purpose: the NAMED transitions are the
    # refusal's own repair population and are the only thing a verdict of
    # CLEAR is about; an edge that was over BEFORE and is still over is
    # PRE-EXISTING and belongs to whatever refusal names it, not to this
    # write; and an edge that was CLEAR before and is over after is CAUSED,
    # which a cone cannot do and which is therefore worth measuring rather
    # than asserting.  Conflating the first with the second reports
    # INCOMPLETE over ground this disposal was never pointed at.
    def _key(e):
        return (tuple(e["a"]), tuple(e["b"]))

    named_edges = {frozenset((t["a"], t["b"])) for _l, t in named
                   if t["kind"] == "edge"}
    # A CAP IS JUDGED BY ITS CAP, AND THIS IS THE SAME SUPERSET DEFECT
    # THE REST OF THIS FILE EXISTS DOWNSTREAM OF.  `still` used to take
    # EVERY after-edge touching a capped vertex, which is a SUPERSET of
    # what the cap's satisfaction depends on: a cap says "this vertex must
    # come under this absolute height" and says nothing whatever about the
    # ground on the other side of its neighbours.
    #
    # MEASURED, Ulfsland 7632b3a5674842bd, `roads/crossings`
    # T9-wttown-dockshore-sb01: the cap at (677,-94) to the declared deck
    # 30.600 is SATISFIED by a 0.400 m cut, and the same vertex still has
    # four over-limit edges into the bridge channel it is the bank of -
    # (678,-94) at 28.8265, (678,-95) at 28.8101 - every one of them OVER
    # BEFORE, every one of them SMALLER AFTER, and not one of them
    # anything this cap named.  Under the old key the disposal reported
    # INCOMPLETE and refused to publish the one cut that closes the row,
    # which is a repair declined because of ground the refusal never
    # mentioned.  A CHANNEL IS NOT A DEFECT OF ITS OWN BANK.
    #
    # Nothing is dropped: those edges are still judged, still counted in
    # `edges_over_after`, and `caused` still catches anything this write
    # makes worse - which is the population that actually belongs to the
    # write rather than to the world it found.
    named_caps = {t["a"]: float(t["target"]) for _l, t in named
                  if t["kind"] == "cap"}
    after_keys = {_key(e) for e in after}
    before_keys = {_key(e) for e in before}
    still = [e for e in after
             if frozenset((tuple(e["a"]), tuple(e["b"]))) in named_edges]
    caused = [e for e in after if _key(e) not in before_keys]
    closed = [e for e in before
              if frozenset((tuple(e["a"]), tuple(e["b"]))) in named_edges
              and _key(e) not in after_keys]
    caps_still = [dict(at=[v[0], v[1]], target_m=round(t, 4),
                       applied_before=round(H0[v], 4),
                       applied_after=round(H[v], 4),
                       over_by_m=round(H[v] - t, 4))
                  for v, t in sorted(named_caps.items())
                  if v in H and H[v] - t > MIN_CUT_M]
    caps_closed = [dict(at=[v[0], v[1]], target_m=round(t, 4),
                        applied_before=round(H0[v], 4),
                        applied_after=round(H[v], 4),
                        came_down_m=round(H0[v] - H[v], 4))
                   for v, t in sorted(named_caps.items())
                   if v in H and H[v] - t <= MIN_CUT_M]
    # WHY A NAMED TRANSITION DID NOT CLOSE, ASKED OF BOTH OWNER SETS.
    #
    # "0 cut sites, still over" is an absence, and an absence reads as
    # fine.  When the cone could not move the high end of a transition it
    # was pointed at, the reason is worth exactly as much as the repair
    # would have been: this asks the ROAD EMITTER'S keep-out (which
    # vertex it froze and why) and, at the depth that would actually have
    # closed the step, `Ledger.append`'s OWN guard through
    # `repair_ownership`.  MEASURED, T13 station 423: ribbon FREEZES
    # (14,-255) inside a 3.00 m bounding disc while the writer PERMITS a
    # 0.0132 m cut there by 0.8 mm of footprint and refuses 0.0140 m.
    # Two instruments, two answers, one row - and the difference is the
    # whole finding.
    diagnosis = []
    if still:
        for e in still:
            a, b = tuple(e["a"]), tuple(e["b"])
            hi = a if H.get(a, -1e9) > H.get(b, -1e9) else b
            diagnosis.append(dict(
                at=[*a, *b], over_by_m=e["over_by_m"], by=e["by"],
                high_end=list(hi),
                high_end_frozen_by_the_writer=prot["why"].get(hi),
                high_end_in_the_road_emitters_keepout=prot[
                    "road_emitter_keepout"].get(hi),
                the_writers_own_answer=writer_verdict(
                    hi, e["over_by_m"], owners, SE)))
    # AND A NAMED EDGE WHOSE LOW END IS A DECLARED DECK IS NOT A GROUND
    # STEP AT ALL - IT IS AN UNBUILT SPAN, AND NO CUT CLOSES IT.
    #
    # This is the cause-side half of `submerged_by_a_cut`.  That guard
    # catches the disposal AFTER it has solved a carriageway into the sea;
    # this names the reason it would have, at the anchor, BEFORE the cone
    # runs - and it is the only one of the two that can see the case where
    # the over-cut is DRY.  MEASURED, T9, dropping `declared_deck_datum`
    # alone: the cone anchors on the 30.0 m plane, cuts (677,-94) to
    # 30.770, and that cut is dry, rim-clean and invisible to every
    # after-the-fact guard in this file - while leaving the bed 0.170 m
    # ABOVE the declared deck it was supposed to come under.
    unbuilt = []
    by_low = {(r["at"][0], r["at"][1]): r for r in raised
              if r["anchored_on"] == "declared_deck_datum"}
    for e in still:
        for v in (tuple(e["a"]), tuple(e["b"])):
            r = by_low.get(v)
            if r is None:
                continue
            unbuilt.append(dict(
                at=r["named_edge"], over_by_m=e["over_by_m"], by=e["by"],
                low_end=[v[0], v[1]],
                low_end_applied_ground_m=r["applied_ground_m"],
                low_end_declared_walking_surface_m=r["anchored_at_m"],
                ground_below_the_declared_deck_m=r["raised_by_m"],
                declared_by=r["why"],
                termination_class="1E_OUT_OF_ORDER",
                why=("the low end of this edge is not ground a walker "
                     "stands on, it is the BED of a declared span: the "
                     "walking surface there is the deck the corpus "
                     "declares, and the remedy is to BUILD IT.  A cut of "
                     "the high end would solve the carriageway down "
                     "toward the bed, which is what this tool did before "
                     "`ANCHOR_DEPENDS_ON` named the datum"),
                remedy_performed_by=("roads/boardwalk.py (spans) into "
                                     "crossings/assemble.py::bridge"),
                prerequisite=("every declared station's bed must stand AT "
                              "OR BELOW the declared deck first - the "
                              "`roads/crossings` premise refusal on the "
                              "same crossing")))
            break
    return dict(
        why_a_named_transition_did_not_close=diagnosis,
        named_transitions_refused_as_an_unbuilt_span=unbuilt,
        named_transitions_refused_as_an_unbuilt_span_total=len(unbuilt),
        anchors_raised_off_their_applied_ground=raised,
        anchors_raised_off_their_applied_ground_total=len(raised),
        anchor_depends_on=list(ANCHOR_DEPENDS_ON),
        declared_decks_read=deck_census,
        named_caps_closed=caps_closed,
        named_caps_still_over=caps_still,
        verdict=("CLEAR" if not still and not caps_still and not caused
                 else ("REFUSED" if caused else "INCOMPLETE")),
        window_radius_m=r, window_grown_to_clear_its_own_rim=not on_rim,
        vertices_read=len(H0), vertices_unreadable=unread,
        vertices_frozen=len(frozen), frozen_restored_after_the_cone=len(restored),
        protected_pieces_in_these_zones=prot["pieces"],
        protected_discs_in_these_zones=prot["discs"],
        anchors=len(anchors), anchors_named=anchors_src,
        anchor_reasons=sorted(set(anchor_why.values())),
        transitions_named=len(named),
        unrepairable_by_a_cut=unrepairable,
        edges_judged=edges_judged,
        edges_over_before=len(before), edges_over_after=len(after),
        named_transitions_closed=[dict(e) for e in closed],
        named_transitions_still_over=still,
        edges_over_after_that_were_clear_before=caused,
        cut_sites=len(cuts),
        cut_worst_m=round(min(cuts.values()), 4) if cuts else 0.0,
        cut_total_m3=round(sum(-v for v in cuts.values()), 4),
        cuts_at_the_edge_of_the_window=[list(k) for k in on_rim],
        cone_per_m=CONE_PER_M, limit_per_m=round(LIMIT_PER_M, 4),
        min_cut_m=MIN_CUT_M,
        zones=[list(z) for z in sorted({tcdata.zone_of(x, z) for (x, z) in cuts})],
        water_sign={k: v for k, v in wsign.items()
                    if not isinstance(v, (list, dict))},
        # ASKED AND CLEAR IS A NUMBER, NOT AN ABSENCE.  0 says this
        # disposal leaves no cut vertex under the plane; a missing field
        # would say nobody looked, and the two must never read alike.
        submerged_by_this_cut_total=0, water_plane_m=WATER_LEVEL_M,
        submerged_exemption_generated_unread_total=len(exemption_unread),
        cuts=[dict(xz=list(k), delta_m=v, applied_before=round(H0[k], 4),
                   applied_after=round(H[k], 4), seed=surf.g(*k))
              for k, v in sorted(cuts.items())],
        _H=H)


def rim_preflight(H: dict, cuts: list, surf: Surface) -> dict:
    """`junction.rim_transfer` over THIS disposal's own taken set, BEFORE the
    emitter is asked.  A cone cannot cause a rim step by construction; this
    is the measurement that says so out loud."""
    taken = {(int(c["xz"][0]), int(c["xz"][1])): float(c["applied_after"])
             for c in cuts}

    # A CONE'S TAKEN SET IS EVERY VERTEX IT WRITES -- `cuts` IS the emission,
    # so nothing outside it moves and the after surface at the rim IS the
    # current one.  Stated rather than defaulted: `rim_transfer` makes every
    # caller answer, because `ribbon.py`'s answer is different.
    def height_now(sx, sz):
        return surf.h(float(sx), float(sz))

    return junctionmod.rim_transfer(taken, height_now, height_after=height_now)


def submerged_by_a_cut(H0: dict, H: dict, cuts, gen: dict | None = None
                       ) -> list:
    """Every vertex this disposal takes UNDER the water plane that was dry
    on ANY surface we hold.  Keyed on the AFTER surface for the refusal and
    on EVER-DRY for the exemption, because those are two questions.

    THE REFUSAL IS THE AFTER SURFACE AND NOTHING ELSE.  "Will a walker be
    wading" depends on where the ground ends up; `ribbon.water_sign` asks a
    different question (does this write REMOVE water) over a population
    keyed on the GENERATED height, and a cut can never trip it.

    THE EXEMPTION IS "WAS IT EVER DRY", NOT "IS IT WET NOW", AND THAT IS A
    RESIDUAL IN THE FIRST VERSION OF THIS FUNCTION, FOUND BY MEASURING ITS
    OWN EXEMPTION RATHER THAN BY REASONING ABOUT IT.  Deepening water is a
    DREDGE and is the one remedy a drained harbour has, so a vertex already
    under the plane must be allowed to go deeper.  The first version read
    "already under the plane" off the APPLIED surface alone - which means a
    vertex a PRIOR WRITE drowned is indistinguishable from seabed and is
    exempted as a dredge.

    MEASURED, CacheKey, 393,533 paired vertices on Ulfsland, WATER 30.0 m -
    and the two populations are not the same thing at a glance:

        wet because THE SEED made it wet   n=200,187
                                           depth median 8.161 m, p90 29.935
        wet because A WRITE drowned it     n=    465
                                           depth median 0.019 m, p90 0.059,
                                           max 0.486; 459 under 0.10 m

    The seed's water is a median 8.161 m deep: that is sea.  The
    write-made water is a median NINETEEN MILLIMETRES: that is a film on
    land, and nobody dredges 19 mm.  So `gen` is consulted - NOT as the
    population key, which is the defect this whole guard exists for, but
    as a second WITNESS TO DRYNESS.  A vertex is exempt only when EVERY
    surface we hold says it was already water.

    THE TRADE, STATED AS A TRADE.  The applied key removed a 1,551-vertex
    blind spot and left this 465-vertex one; ever-dry closes that too.  The
    asymmetry is why it was worth taking either way: a factory that raises
    shores MANUFACTURES the first set - 1,551 raised against 465 drowned -
    so a generated-keyed guard gets blinder every time the factory works,
    while only accidents make the second and it shrinks.

    `gen=None` means the generated surface was not read, and the exemption
    then falls back to the applied surface alone.  It is reported rather
    than silently assumed: see `submerged_exemption_keyed_on` in the
    caller.  UNREAD IS NOT DRY.
    """
    out = []
    for k in cuts:
        if H[k] >= WATER_LEVEL_M - 1e-9:
            continue
        g = None if gen is None else gen.get(k)
        ever_dry = H0[k] >= WATER_LEVEL_M - 1e-9 or (
            g is not None and g >= WATER_LEVEL_M - 1e-9)
        if not ever_dry:
            continue
        out.append(dict(
            xz=[k[0], k[1]], applied_before=round(H0[k], 4),
            applied_after=round(H[k], 4),
            under_the_plane_m=round(WATER_LEVEL_M - H[k], 4),
            dry_on=("the applied surface this write starts from"
                    if H0[k] >= WATER_LEVEL_M - 1e-9
                    else "the GENERATED surface only: a prior write drowned "
                         f"this vertex by {round(g - H0[k], 4)} m and this "
                         "cut would deepen it, which is not a dredge")))
    return sorted(out, key=lambda r: -r["under_the_plane_m"])


def selftest_submersion(verbose: bool = True) -> dict:
    """THE GUARD, AND BOTH WAYS IT CAN BE WRONG.

    THE STATE IT IS REQUIRED TO REFUSE IS THIS TOOL'S OWN OUTPUT, and it
    is reproduced here as three vertices rather than borrowed from a save:
    a fixture that leans on an artefact inherits that artefact's
    mutability.  The numbers are the ones MEASURED on Ulfsland
    7632b3a5674842bd for `settlements/terminus_step`
    road_T9_wttown_dockshore B - the shore standing at applied 31.000 m
    over a seed of 28.857-29.390 m, because `terrain_write
    road_T9_wttown_dockshore` (seq 234) raised it out of the sea, and the
    cone solving it down to 29.5965 m.

    ARM 1, THE REFUSAL, AND THE NEUTER IS THE INSTRUMENT THAT WAS ALREADY
    THERE.  `ribbon.water_sign` is keyed on the GENERATED height and asks
    whether the write RAISES a drawn-submerged sample out of the water.
    Every one of these samples is generated below the plane, so all three
    are inside its population, and a CUT can never raise anything - it
    returns CLEAR over the exact set this refuses.  That is the neuter and
    it is not hypothetical: it is what the tool shipped with.

    ARM 2, THE SURVIVOR, AND IT IS A VERDICT AND NOT AN ASSERTION.  Keying
    on the AFTER surface alone can only ever ADD refusals, so a guard that
    simply refused every cut near water would satisfy arm 1 and be worth
    nothing.  A DREDGE - SEABED, wet on every surface we hold, cut deeper -
    must pass, because deepening water is the one remedy a drained harbour
    has and `water_sign`'s own selftest exists to protect it.

    ARM 3, THE EXEMPTION'S OWN BLIND SPOT, AND IT WAS FOUND BY MEASURING
    THE EXEMPTION RATHER THAN BY REASONING ABOUT IT.  The first version of
    this guard read "already under the plane" off the APPLIED surface
    alone, so a vertex a PRIOR WRITE drowned was indistinguishable from
    seabed and was exempted as a dredge.  MEASURED, CacheKey, 393,533
    paired vertices: the seed's water is a median 8.161 m deep and the
    write-made water is a median 0.019 m deep - 465 vertices, 459 of them
    under 0.10 m.  NOBODY DREDGES NINETEEN MILLIMETRES.  This arm is that
    film: dry on the generated surface, drowned by a hair, and a cut that
    deepens it MUST be refused.

    All three arms are scored into ONE verdict, so an empty guard, a
    blanket guard and an applied-only exemption fail alike.
    """
    # ARM 1 - THE T9 SHAPE: above the plane today, under it after the cone.
    H0 = {(677, -94): 31.0, (678, -93): 31.0, (677, -92): 31.0}
    H = {(677, -94): 29.5965, (678, -93): 29.5965, (677, -92): 30.6854}
    gen = {(677, -94): 29.138, (678, -93): 28.857, (677, -92): 29.289}
    drowned = submerged_by_a_cut(H0, H, H0, gen)
    # ARM 2 - A DREDGE: SEABED, wet on the seed AND wet today, cut deeper.
    # It must NOT appear, or the guard has taken the dredge with it.  The
    # depths are the seed-water population's own shape: metres, not a film.
    dH0 = {(0, 0): 21.8, (1, 0): 20.3}
    dgen = {(0, 0): 21.9, (1, 0): 20.4}
    dH = {(0, 0): 20.6, (1, 0): 19.1}
    dredge = submerged_by_a_cut(dH0, dH, dH0, dgen)
    # ARM 3 - THE FILM: the seed drew this DRY at 30.019 m and a prior
    # write drowned it by 19 mm to 29.981.  Deepening it is not a dredge,
    # and an exemption keyed on "is it wet now" cannot tell it from arm 2.
    fH0 = {(4, 4): 29.981}
    fgen = {(4, 4): 30.019}
    fH = {(4, 4): 29.2}
    film = submerged_by_a_cut(fH0, fH, fH0, fgen)
    # AND WHAT THE INSTRUMENT THAT WAS ALREADY THERE SAYS ABOUT ARM 1.
    wsign = ribbonmod.water_sign(gen, H, H0)
    ok = (len(drowned) == 2 and drowned[0]["under_the_plane_m"] == 0.4035
          and not dredge and len(film) == 1
          and wsign["verdict"] == "CLEAR")
    out = dict(
        verdict="CLEAR" if ok else "REFUSED",
        refused_by_the_after_key=len(drowned),
        refused_worst_m=drowned[0]["under_the_plane_m"] if drowned else 0.0,
        refused_at=drowned[0]["xz"] if drowned else None,
        the_dredge_that_must_survive=len(dredge),
        the_film_that_must_be_refused=len(film),
        the_film_dry_on=film[0]["dry_on"] if film else None,
        what_the_generated_key_says=wsign["verdict"],
        what_the_generated_key_counts=wsign["drained_samples"],
        generated_key_population=wsign["population"],
        population="3 shore vertices a prior write raised out of the sea, "
                   "a 2-vertex SEABED dredge 8 m down, and 1 vertex a prior "
                   "write drowned by 19 mm - the seed-water and write-water "
                   "populations differ by a median 8.161 m against 0.019 m")
    if verbose:
        print(json.dumps(out, indent=1))
        print(f"\nthe AFTER key refuses {len(drowned)} vertex(es), worst "
              f"{out['refused_worst_m']} m under the {WATER_LEVEL_M} m "
              f"plane; the GENERATED key says {wsign['verdict']} over the "
              f"same three; the SEABED dredge survives ({len(dredge)} "
              f"refused) and the 19 mm FILM does not ({len(film)} refused)")
    assert ok, out
    return out


# THE POPULATION, AS A MODULE-LEVEL LITERAL, SO THE DENOMINATOR CANNOT BE
# PATCHED BY THE THING UNDER TEST.  `anchor_height`'s neuter is an argument
# to `anchor_height`; these rows are constants it cannot reach, so a
# degenerate implementation cannot score perfectly by emptying the set it is
# scored over.  Every number is MEASURED on Ulfsland 7632b3a5674842bd.
ANCHOR_CASES = (
    # 1. THE BANK OF A DECLARED BRIDGE.  (678,-94) is the channel bed of
    #    `roads/crossings.yaml:declared` T9-wttown-dockshore-sb01 -
    #    required_deck_y 30.6, gap_min_y 28.826, drop_into_the_channel_m
    #    2.174, which is the settlements refusal's own step.  The DECK
    #    dominates: 30.6 > plane 30.0 > ground 28.8265.
    dict(name="the bed of a DECLARED span: the deck term dominates",
         v=(678, -94), applied=28.8265, datums={(678, -94): 30.6},
         t=30.6, src="declared_deck_datum",
         high=(677, -94), high_applied=31.0, high_after=31.0),
    # 2. THE SAME BED WITH NO DECLARATION OVER IT - a DETECTED crossing
    #    carries no `required_deck_y` at all.  The PLANE dominates, and it
    #    must, or the cone solves a carriageway into the sea.  This arm is
    #    why `water_plane` is not an unasserted term: on case 1 it is
    #    DOMINATED by the deck and contributes nothing.
    dict(name="a wet low end with NO declaration: the plane term dominates",
         v=(678, -94), applied=28.8265, datums={},
         t=30.0, src="water_plane",
         high=(677, -94), high_applied=31.0, high_after=30.77),
    # 3. HONEST DRY GROUND, AND IT IS THE SURVIVOR.  Raising an anchor can
    #    only ever make a cone shallower, so a degenerate rule that simply
    #    raised every anchor to the plane or to a datum would satisfy
    #    arms 1 and 2 and be worth nothing.  This is the vestvik approach
    #    the cone was proved on: ground at 34.520, cut to 35.290 one metre
    #    away, and BOTH other terms are below it.
    dict(name="honest dry ground: the applied term dominates - THE SURVIVOR",
         v=(-905, 1895), applied=34.52, datums={},
         t=34.52, src="applied_ground",
         high=(-904, 1895), high_applied=36.0, high_after=35.29),
)


def selftest_anchor(verbose: bool = True) -> dict:
    """`ANCHOR_DEPENDS_ON`, CHECKED IN EVERY DIRECTION IT DECLARES.

    THREE OBLIGATIONS, SCORED INTO ONE VERDICT, and `run_cases` reads an
    `AssertionError` as a flip - so every survivor here is a VERDICT and
    not an assertion.

      1. THE SHIPPED ANSWER IS THE DECLARED ANSWER.  Each case states the
         anchor height, which term won it, and what the real `cone()` then
         does to a vertex 1 m away.  An anchor rule that is merely
         arithmetic would pass 1 alone, so the CONSEQUENCE is scored too.
      2. EVERY DECLARED TERM IS LOAD-BEARING.  Each name in
         `ANCHOR_DEPENDS_ON` is neutered ON ITS OWN and must flip at least
         one case.  A one-direction neuter is satisfied by an index that
         returned nothing, so:
      3. EVERY NEUTER LEAVES A SURVIVOR.  Each single-term neuter must
         also leave at least one case UNCHANGED, or "the neuter flipped"
         was bought by emptying the population.

    AND THE ARM THAT MUST GO RED IS THE BEHAVIOUR THIS FILE SHIPPED WITH.
    Neutering `declared_deck_datum` AND `water_plane` together IS the old
    rule - `anchor at the applied ground` - and on case 1 it anchors at
    28.8265 and takes the bank to 29.5965, which is 0.4035 m UNDER the
    plane.  That number is `selftest_submersion`'s arm 1, measured from
    the other end: this is the CAUSE, that is the SYMPTOM, and they are
    scored here against each other so neither can drift alone.

    THE SINGLE-TERM NEUTER OF `declared_deck_datum` IS THE SHARPER ARM,
    and it is the one no after-the-fact guard can see.  It anchors on the
    plane, cuts the bank to 30.770 - DRY, rim-clean, `submerged_by_a_cut`
    CLEAR - and leaves the bed 0.170 m ABOVE the deck the crossing
    declares.  A disposal can be legal at every guard in this file and
    still be the wrong cut, and only the cause-side rule catches it.
    """
    total = len(ANCHOR_CASES)

    def run(neuter):
        got = []
        for c in ANCHOR_CASES:
            t, src, _considered = anchor_height(c["v"], c["applied"],
                                                c["datums"], neuter)
            if t is None:
                got.append((None, src, None))
                continue
            f = cone({c["v"]: t}, {c["v"], c["high"]})
            after = min(c["high_applied"], f[c["high"]])
            got.append((round(t, 4), src, round(after, 4)))
        return got

    shipped = run(())
    declared = [(round(c["t"], 4), c["src"], round(c["high_after"], 4))
                for c in ANCHOR_CASES]
    agrees = sum(1 for a, b in zip(shipped, declared) if a == b)
    arms = []
    for term in ANCHOR_DEPENDS_ON:
        got = run((term,))
        flipped = [ANCHOR_CASES[i]["name"] for i in range(total)
                   if got[i] != shipped[i]]
        survived = [ANCHOR_CASES[i]["name"] for i in range(total)
                    if got[i] == shipped[i]]
        arms.append(dict(
            neutered=term, of=total,
            flipped=len(flipped), survived=len(survived),
            flipped_cases=flipped, survivor=survived[0] if survived else None,
            answers=[dict(case=ANCHOR_CASES[i]["name"],
                          anchored_at_m=got[i][0], anchored_on=got[i][1],
                          the_cut_lands_at_m=got[i][2]) for i in range(total)]))
    # THE SHIPPED RULE, RE-CREATED BY NEUTERING BOTH DERIVED TERMS AT ONCE,
    # AND ITS CONSEQUENCE MEASURED THROUGH THE REAL SUBMERSION GUARD.
    old = run(("declared_deck_datum", "water_plane"))
    c1 = ANCHOR_CASES[0]
    old_after = old[0][2]
    # AN ANCHOR THAT DOES NOT EXIST IS A VERDICT, NOT A TRACEBACK.  An
    # implementation that answers `None` everywhere would take
    # `submerged_by_a_cut` into a comparison against None and raise - and
    # `run_cases` reads a raise as a FLIP, which would stamp an anchor
    # rule that answers nothing as PROVEN.  Every arm in this fixture is
    # scored, never asserted.
    drowned = ([] if old_after is None else
               submerged_by_a_cut({c1["high"]: c1["high_applied"]},
                                  {c1["high"]: old_after},
                                  {c1["high"]: None},
                                  {c1["high"]: 29.1376}))
    ok = (agrees == total
          and all(a["flipped"] >= 1 and a["survived"] >= 1 for a in arms)
          and len(drowned) == 1
          and drowned[0]["under_the_plane_m"] == 0.4035)
    out = dict(
        verdict="CLEAR" if ok else "REFUSED",
        anchor_depends_on=list(ANCHOR_DEPENDS_ON),
        cases=total,
        the_shipped_rule_agrees_with_its_declaration=f"{agrees} of {total}",
        shipped=[dict(case=ANCHOR_CASES[i]["name"], anchored_at_m=shipped[i][0],
                      anchored_on=shipped[i][1],
                      the_cut_lands_at_m=shipped[i][2]) for i in range(total)],
        every_declared_term_is_load_bearing=all(a["flipped"] >= 1
                                                for a in arms),
        every_neuter_leaves_a_survivor=all(a["survived"] >= 1 for a in arms),
        neuters=arms,
        the_rule_this_file_shipped_with=dict(
            neutered=["declared_deck_datum", "water_plane"],
            anchored_at_m=old[0][0], anchored_on=old[0][1],
            the_cut_lands_at_m=old_after,
            under_the_plane_m=(drowned[0]["under_the_plane_m"]
                               if drowned else 0.0),
            refused_by=("cone.submerged_by_a_cut, which is the SYMPTOM side "
                        "of the same defect")),
        the_single_term_neuter_no_guard_can_see=dict(
            neutered="declared_deck_datum",
            anchored_at_m=arms[1]["answers"][0]["anchored_at_m"],
            the_cut_lands_at_m=arms[1]["answers"][0]["the_cut_lands_at_m"],
            still_above_the_declared_deck_m=(
                None if arms[1]["answers"][0]["the_cut_lands_at_m"] is None
                else round(arms[1]["answers"][0]["the_cut_lands_at_m"]
                           - ANCHOR_CASES[0]["datums"][ANCHOR_CASES[0]["v"]],
                           4)),
            why=("dry, rim-clean and CLEAR to every after-the-fact guard in "
                 "this file, and still 0.170 m above the deck the corpus "
                 "declares")),
        population=("3 anchors, one per declared term, each chosen so THAT "
                    "term dominates the other two - the bed of a declared "
                    "span, the same bed with no declaration, and dry ground "
                    "above both. The denominator is the module-level "
                    "ANCHOR_CASES literal, which the neuter cannot reach."))
    if verbose:
        print(json.dumps(out, indent=1))
        print(f"\nanchor: shipped agrees {agrees}/{total}; " + "; ".join(
            f"neuter {a['neutered']} flips {a['flipped']}/{a['of']} and "
            f"{a['survived']} survive" for a in arms))
        print(f"the rule this file shipped with anchors at {old[0][0]} and "
              f"lands the cut at {old_after}, "
              f"{out['the_rule_this_file_shipped_with']['under_the_plane_m']}"
              f" m UNDER the {WATER_LEVEL_M} m plane")
    return out

# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true",
                    help="run this tool's own fixtures and return 0/1")
    ap.add_argument("--findings")
    ap.add_argument("--row", action="append",
                    help="<section>:<check>:<substring of the message>")
    ap.add_argument("--name")
    ap.add_argument("--out", help="a regrade.py --cuts file")
    ap.add_argument("--report", default=None)
    ap.add_argument("--actor")
    ap.add_argument("--seed", default=ribbonmod.SEED)
    args = ap.parse_args()

    if args.selftest:
        a = selftest_anchor()
        print()
        s = selftest_submersion()
        both = [("selftest_anchor", a["verdict"]),
                ("selftest_submersion", s["verdict"])]
        print("\n" + "  ".join(f"{n} {v}" for n, v in both))
        return 0 if all(v == "CLEAR" for _n, v in both) else 1
    for need in ("findings", "row", "name", "out", "actor"):
        if getattr(args, need) in (None, []):
            raise SystemExit(f"REFUSED: --{need} is required without "
                             f"--selftest")

    doc = json.loads(Path(args.findings).read_text())
    rows = select_rows(doc, args.row)
    surf = Surface(args.actor, args.seed, args.name)
    got = solve(rows, surf)
    H = got.pop("_H", None)
    if got["verdict"] == "REFUSED":
        print(json.dumps(got, indent=1))
        print(f"\nREFUSED: {got['why']}")
        return 2

    rim = rim_preflight(H, got["cuts"], surf)
    got["rim_transfer"] = {k: v for k, v in rim.items()
                           if not isinstance(v, list)}
    got["rim_caused_rows"] = rim["caused_rows"][:8]
    print(json.dumps({k: v for k, v in got.items()
                      if k not in ("cuts", "anchors_named")}, indent=1))
    print(f"\n{got['cut_sites']} cut site(s), worst "
          f"{got['cut_worst_m']:+.4f} m, {got['cut_total_m3']} m3; rim "
          f"{rim['verdict']} - {rim['caused']} CAUSED, "
          f"{rim['pre_existing']} pre-existing over "
          f"{rim['rim_pairs_judged']} pair(s)")
    for u in got.get("unrepairable_by_a_cut") or []:
        print(f"  NOT CUTTABLE: {u['row']} - {u['why']}")
    if args.report:
        Path(args.report).write_text(json.dumps(got, indent=1))
    if rim["verdict"] == "REFUSED":
        print(f"REFUSING to publish cuts: the rim pre-flight names "
              f"{rim['caused']} CAUSED step(s), worst "
              f"{rim['worst_caused_m']} m at {rim['worst_caused_at']}.")
        return 7
    if got["verdict"] != "CLEAR":
        print(f"REFUSING to publish cuts: "
              f"{len(got['named_transitions_still_over'])} named "
              f"transition(s) and "
              f"{len(got['named_caps_still_over'])} named cap(s) are still "
              f"over after the cone, and "
              f"{len(got['edges_over_after_that_were_clear_before'])} "
              f"edge(s) over after were CLEAR before.")
        for e in (got["named_transitions_still_over"]
                  + got["edges_over_after_that_were_clear_before"])[:8]:
            print(f"   {e['a']} -> {e['b']} rise {e['rise_m']:+.4f} over "
                  f"{e['run_m']:.3f} m, OVER BY {e['over_by_m']:+.4f}")
        for c in got["named_caps_still_over"][:8]:
            print(f"   cap {c['at']} target {c['target_m']:.4f}, still "
                  f"{c['over_by_m']:+.4f} m above it")
        for u in got["named_transitions_refused_as_an_unbuilt_span"][:8]:
            print(f"   UNBUILT SPAN {u['at']}: the low end {u['low_end']} "
                  f"stands {u['ground_below_the_declared_deck_m']:.4f} m "
                  f"BELOW the walking surface declared over it "
                  f"({u['low_end_declared_walking_surface_m']:.3f} m) - "
                  f"{u['termination_class']}, remedy "
                  f"{u['remedy_performed_by']}")
        return 3
    if not got["cuts"]:
        print("REFUSED: the cone proposes no cut at all.")
        return 2
    why = ("; ".join(a["message"] for a in got["anchors_named"])[:700]
           + f" | cut-only LIPSCHITZ CONE at {CONE_PER_M} m per 1 m over "
             f"{got['anchors']} anchor(s), EIGHT-CONNECTED, window radius "
             f"{got['window_radius_m']} m grown until no cut touched its own "
             f"rim; {got['vertices_frozen']} frozen vertex(es); rim "
             f"pre-flight {rim['caused']} CAUSED")
    Path(args.out).write_text(json.dumps(dict(
        why=why,
        cuts=[dict(xz=c["xz"], applied_after=c["applied_after"])
              for c in got["cuts"]]), indent=1))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
