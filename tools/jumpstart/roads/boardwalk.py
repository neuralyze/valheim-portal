#!/usr/bin/env python3
"""THE OTHER HALF OF THE NORTHERN ROAD: the sixteen decked channel crossings.

A road that is `WALKABLE` on every leg can still be a road nobody can walk.
MEASURED against the canonical Ulfsland ledger: the six northern legs contain
16 crossings classified `cause=bridge`, 714.5 m in total, with ZERO pieces
standing at any of them.  A player leaving the S2 deck walks 51.4 m, swims
20.3 m, then walks 3,204.7 m -- and every leg's own verdict says WALKABLE,
truthfully, because `WALKABLE` is a statement about a leg's TERRAIN and never
a claim about continuity.  This module builds the structures that make the
statement about the ROAD true.

WHAT IS AND IS NOT DECIDED HERE
-------------------------------
Nothing is routed, classified or solved here.  `roads/north_spans.json` is the
handed-over spec: per span the two bank coordinates and their ground heights,
the bed minimum, the REQUIRED deck height, the legal deck interval, the pile
reach fraction and `single_H_both_banks`.  This module turns that spec into
pieces, measures the result against the surface a player will actually stand
on, and emits the records.  Geometry comes from `crossings/assemble.py`
(`bridge`, `verify`, `command`) because that generator is what built S1 and S2
live, and a second bridge generator beside it would be a second convention.

THE DECK DATUM IS PER SPAN AND `DECK_DATUM_Y` IS NOT IT
-------------------------------------------------------
`spec.DECK_DATUM_Y = 30.6` is the datum for a SEA deck: a harbour dock, a
jetty, the two coastal bridges.  These sixteen are INLAND channels whose banks
stand at 31.00-34.92 m and whose road profile was solved to a single level `H`
per crossing (`network.build_segment` writes the same number into
`bank_level.level_at_y` and into `crossings[].required_deck_y`).  The road
arrives at `H`.  A deck at 30.6 would leave a step of up to 1.9 m at BOTH
abutments -- section 4.4's arrival defect with the sign reversed, and a defect
that reads as a correct build because every piece is where the plan said.

`deck_datum_guard` therefore refuses a deck at `DECK_DATUM_Y` whenever the
span's own required `H` differs from it, and refuses any deck outside the
span's recorded `legal_deck_y_interval`.  It is proven in BOTH directions by
`--prove-guards`, which re-runs every guard against a deliberately wrong
30.6 m deck and requires each one to REFUSE: a guard shown only to pass has
not been shown to be a guard.

THE SURFACE THE PIECES ARE MEASURED AGAINST IS THE APPLIED ONE
--------------------------------------------------------------
Pile length, deck-tile air gap, tile burial and the abutment step are all
measured against `applied.Applied.surface_at` -- generated height plus the
live union of every `terrain_write` delta in the chain, composed in FILE ORDER
and blended bilinearly exactly as `Heightmap` renders the mesh.  Against the
GENERATED height instead, every number here would be the state of the world
BEFORE the road was built: internally consistent, reproducible, and wrong by
the whole depth of the abutment bench.  That is the same signature as an
unpaged ZDO iterator reporting a dredged basin at its pre-dredge depth.

ORDER: TERRAIN FIRST, ALWAYS
----------------------------
A record carrying `flatten: "FORBIDDEN"` arms `writer._flatten_forbidden_check`
against every LATER `terrain_write` whose blob moves the ground by more than
0.01 m inside its pieces' footprint.  A deck placed before its leg's terrain
write therefore refuses that write at the abutment -- the ramp the deck needs
is the ramp the deck's own protection forbids.  `--require-terrain` refuses to
build a span whose segment has no `terrain_write` in the chain.

THE POSTCONDITION IS MEASURED WITH THE INSTRUMENT, NOT DERIVED FROM THE PLAN
----------------------------------------------------------------------------
`objects_count id=<prefab> pos=<x>,<z> max=<r>` is a Y-BLIND XZ CYLINDER: a
two-pole pile column at one xz answers 2, not 1, and a deck tile directly over
a cap beam is a separate row at the same xz.  It is also measured the moment
its record lands, so the expectation must be the state AT THAT MOMENT and not
the finished span.  Both are why every `expect.prefab_count` here is
(live baseline measured in this disc immediately before record 1) + (pieces
this run has sent into this disc through this record), never a count read off
the design.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(JUMPSTART))
sys.path.insert(0, str(JUMPSTART / "ledger"))
sys.path.insert(0, str(JUMPSTART / "crossings"))
sys.path.insert(0, str(JUMPSTART / "terraform"))

import applied as appliedmod            # noqa: E402
import assemble as asm                  # noqa: E402
import ribbon as ribbonmod              # noqa: E402
import spec as specmod                  # noqa: E402
import route as routemod                # noqa: E402
import worldseed                        # noqa: E402
from accept import instruments as road_instruments  # noqa: E402
from accept.sections import roads as road_gate      # noqa: E402

SPANS = HERE / "north_spans.json"
SEED = ribbonmod.SEED
# BOARDWALK_WORLD_DEPENDS_ON: the world being written.  `segment_terrain_written`
# defaulted its `world` to the literal "Ulfsland" and `main`'s build path opened
# the pre-write chain on the same literal WHILE THE WRITE ITSELF went through
# `LiveBuilder`, which resolves `worldseed.world()` -- so on any world but
# Ulfsland the before/after sha was taken on a DIFFERENT CHAIN than the one
# being appended to, and the quarantined ledger was the one read.
WORLD = ribbonmod.WORLD
WATER_LEVEL_M = 30.0

# `spec.DECK_DATUM_Y`, read rather than retyped so this refusal cannot drift
# away from the constant it refuses.
SEA_DECK_DATUM_Y = specmod.DECK_DATUM_Y

# A deck tile's walking surface must meet the road bench it abuts.  0.35 m is
# the step a player walks up without a jump and without the 0.5 m auto-step
# looking like a kerb; the defect being prevented is the 1.9 m step a sea
# datum would leave, which is 5.4x this.
ABUTMENT_STEP_MAX_M = 0.35
# A deck tile over the bank may be buried by the bench it keys into, but a
# buried WALKING surface is a hole in the road.
BURIAL_MAX_M = 0.30
# Air under a tile that has no pile and no bank under it.  Every interior tile
# is carried by the deck itself, so this is measured per tile against the
# support verdict, not against the ground.
AIR_VISIBLE_M = 0.25
# Every pile column's lowest piece must overlap the terrain layer: that
# overlap is what pins it to maxSupport (WearNTear::UpdateSupport).  Less than
# this and the column is a post standing on nothing.
PILE_EMBED_MIN_M = 0.10
# Pieces per record.  The console sink is the subsystem that has failed four
# times in one session; 48 is the batch every landed spawn_plan on this world
# has used.
BATCH = 48


# --------------------------------------------------------------------------
# The spec
# --------------------------------------------------------------------------
#
# WHY THIS MODULE STOPPED AT SIXTEEN.  `north_spans.json` is a HAND-WRITTEN
# hand-over covering the six northern legs and nothing else, and it was the
# only thing this file could be pointed at.  MEASURED on Ulfsland
# 2026-09-22: `roads/segments.yaml` carries 14 crossing records of its own,
# every one `kind: bridge`, `owner: Crossings`,
# `why_terrain_failed: water_body` -- and SIX OF THEM ARE ON SEGMENTS THE
# LEDGER HAS ALREADY WRITTEN TERRAIN FOR, carrying 347.5 m of declared deck
# with ZERO bridge_body_place records between them:
#
#   T4-meadhall-stathub-x01   11.31 m   deck 32.672
#   T4-meadhall-stathub-x02   48.08 m   deck 32.500
#   T5-temple-dockshore-x01   28.28 m   deck 32.500
#   W5-brgs1-wsouth-x01       89.44 m   deck 32.500
#   W10-brgs1-treenear-x01    89.44 m   deck 32.500
#   S12-brgs1-boathouse-x01   80.99 m   deck 33.870
#
# Every one of those roads reads WALKABLE on its own terrain and ends in the
# air at a bank, which is the exact defect the module header opens with --
# reproduced on the half of the network the hand-over did not cover.  A
# builder that can only build what somebody typed into a JSON file is not a
# builder of roads, it is a builder of that file, and the next seed will
# arrive with no `north_spans.json` at all.
#
# So the SPEC IS READ OUT OF THE NETWORK.  `network.build_segment` already
# writes every field this module consumes into each segment's own
# `crossings[]` record; `spans_from_network` renames them and nothing else.
# Nothing is derived, guessed or defaulted: a crossing missing any field
# this module needs is REFUSED BY NAME rather than filled in.

def load_spans(path: Path = SPANS, only: list[str] | None = None,
               segments: list[str] | None = None) -> list[dict]:
    doc = json.loads(path.read_text())
    out = doc["spans"]
    if segments:
        out = [s for s in out if s["segment"] in segments]
    if only:
        want = set(only)
        have = {s["crossing_id"] for s in out}
        missing = want - have
        if missing:
            raise SystemExit(f"no such span(s): {sorted(missing)}; have "
                             f"{sorted(have)}")
        out = [s for s in out if s["crossing_id"] in want]
    return out


# The spec fields this module reads, and the `crossings[]` field each one is
# a rename of.  A crossing that cannot fill all of them is refused with the
# missing names, because a deck built on a defaulted datum is worse than no
# deck: it is a walkable-looking step nobody measured.
SPAN_FROM_CROSSING = {
    "crossing_id": "crossing_id",
    "segment": "segment_id",
    "span_m": "span_m",
    "deck_y_REQUIRED": "required_deck_y",
    "legal_deck_y_interval": "legal_deck_y_interval",
    "deck_width_m": "ribbon_width_m",
    "single_H_both_banks": "single_H_both_banks",
    "bank_level_run_m": "bank_level_run_m",
    "why_terrain_failed": "why_terrain_failed",
    "water_surface_y": "water_surface_y",
}


# THE CORPUS IS THE RUN'S SEED'S, RESOLVED WHEN ASKED.  This was the literal
# `HERE / "crossings.yaml"` -- Pirate68's contract -- so on any other seed
# every default reader judged Ulfsland's crossings: MEASURED on the drive-2
# gate, "the builder judged 17 crossing record(s) from roads/crossings.yaml:
# 15 it REFUSES" against a world whose own `crossings.Fresh42.yaml` holds 9.
def crossings_yaml() -> Path:
    return worldseed.crossings_path()


def crossing_corpus(path: Path | None = None) -> tuple[list[dict], dict]:
    """THE CORPUS THE ACCEPTANCE GATE JUDGES, read here instead of a mirror.

    `declared` UNION `detected`, deduplicated by `crossing_id` with DECLARED
    winning, per the file's own `precedence` line -- byte for byte the
    population `accept/sections/roads.py::crossing_spans` counts.

    WHY THIS REPLACED `segments.yaml`.  MEASURED on Ulfsland 2026-09-23:

      roads/crossings.yaml   3 declared + 14 detected = 17 crossings
      roads/segments.yaml    15 crossings[] records, one per segment

    and the two missing from the mirror are S1 and S2, the two crossings
    whose `segment_id` IS NULL because they JOIN TWO SEGMENTS instead of
    lying on one.  A crossing that joins two segments belongs to no segment,
    so a builder that enumerates segments cannot see it -- and S1 is the
    12.0 m channel whose deck is the named remedy for THREE separate gate
    refusals: its own crossings row and the `boathouse` and `brgs1`
    connectivity rows, both of which publish `link_crossing: "S1"` and
    `no_terrain_repair: "the remedy is a deck on S1, which writes no
    terrain"`.

    Two populations for one obligation is the defect class this project keeps
    finding.  The gate counted 17 and refused; the only builder in the tree
    counted 15 and could not be pointed at the difference.
    """
    import yaml                                          # noqa: PLC0415
    p = path or crossings_yaml()
    doc = yaml.safe_load(p.read_text())
    by_id: dict[str, dict] = {}
    order: list[str] = []
    for key in ("detected", "declared"):       # declared written LAST: it wins
        for c in (doc.get(key) or ()):
            cid = c.get("crossing_id")
            if cid is None:
                continue
            if cid not in by_id:
                order.append(cid)
            rec = dict(c)
            rec["status"] = rec.get("status", key)
            by_id[cid] = rec
    return ([by_id[cid] for cid in order],
            {"contract": doc.get("contract") or {},
             "abutment_handoffs": list(doc.get("abutment_handoffs") or ()),
             "declared": len(doc.get("declared") or ()),
             "detected": len(doc.get("detected") or ()),
             "distinct_ids": len(by_id),
             "source": str(p),
             "precedence": doc.get("precedence", "")})


def _handoffs_by_node(handoffs: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for h in handoffs:
        if h.get("node"):
            out.setdefault(h["node"], []).append(h)
    return out


# DECLARED_WIDTH_DEPENDS_ON: A DECLARED DECK IS AS WIDE AS THE WIDEST ROAD THAT
# MEETS EITHER BANK, AND DISAGREEING ROAD WIDTHS ARE NOT A REASON TO BUILD
# NOTHING.
#
# `fill_from_the_contract` took `ribbon_width_m` from the `abutment_handoffs`
# of BOTH banks' nodes and filled it only when the two banks named ONE width
# and BOTH had a road, "because a deck has one width".  A deck has one width;
# that does not make two widths a refusal -- it makes the widest the deck's.
# MEASURED, Ulfsland regen, step 6.5 on both declared crossings: `S1
# REFUSED as a span: missing ribbon_width_m` (brg-s1-e is met by the 6.0 m
# trunk T2 and the 4.0 m spur S12, brg-s1-w by two 4.0 m spurs) and `S2
# REFUSED ... missing ribbon_width_m` (brg-s2-e 4.0 m, brg-s2-w no road in
# the plan), so the unit named `S2` was `no such span(s)` and S1 -- the
# deck named as the remedy of THREE gate refusals -- never reached a builder
# at all: both declared crossings are Crossings' own measurements and the
# tool refused them for a number it holds in the same file.  The same width
# refusal would meet any declared crossing whose banks are served by two
# classes of road, on any seed.
# THE RULE: the deck is as wide as the widest carriageway of any handoff at
# either bank's own node; a bank with no road names no width but does not veto
# the other's (the abutment guard judges what stands beyond that bank, with
# its own numbers); a crossing no road reaches at all is still refused by name.
# The pinch the width rule exists to avoid is a wide road arriving at a narrow
# deck (roads/spec.py: "bringing a 6 m trunk ribbon to a 4 m jetty CREATES
# the pinch"); the widest deck cannot make one.
# NEUTER `_DECLARED_WIDTH_MODE = "agree"`: the shipped fill -- both banks
# must have a road and name ONE width.  Proven by `census_selftest` (arm
# `declared deck width`).
DECLARED_WIDTH_DEPENDS_ON = ("the_widest_carriageway_that_meets_either_bank",)
_DECLARED_WIDTH_MODE = "widest"    # widest | agree (the shipped fill)


def declared_deck_width(by_end: dict) -> float | None:
    """The deck width a declared crossing takes from the handoff widths at its
    two banks (`{end: {width, ...}}`), or None when nothing names one
    (DECLARED_WIDTH_DEPENDS_ON)."""
    flat = {w for s in by_end.values() for w in s}
    if not flat:
        return None
    if _DECLARED_WIDTH_MODE == "agree":
        return next(iter(flat)) if len(flat) == 1 and len(by_end) == 2 else None
    return max(flat)


def fill_from_the_contract(c: dict, corpus: dict) -> tuple[dict, list[str]]:
    """Fields a DECLARED crossing does not carry, READ from named authorities.

    NOTHING HERE IS DEFAULTED AND NOTHING IS AVERAGED.  Every value comes
    from a record in the same file that says it, and a field with no such
    record is left missing so the caller REFUSES BY NAME.  A deck built on a
    defaulted datum is worse than no deck.

      bank_level_run_m       `contract.bank_level_run_m`
      required_deck_y        `bank_a.road_deck_y`, and only when `bank_b`'s
                             AGREES -- that agreement IS `single_H_both_banks`
                             and is therefore measured rather than asserted
      ribbon_width_m         the WIDEST carriageway the `abutment_handoffs`
                             records of this crossing's banks' own nodes
                             name (DECLARED_WIDTH_DEPENDS_ON): one deck, one
                             width, and a road narrower than it arrives at
                             a wider deck, never the other way round
      legal_deck_y_interval  the degenerate interval [deck_datum_y,
                             deck_datum_y], and ONLY when this crossing's own
                             required deck IS that datum.  `network.py` states
                             the rule it is read from: "A declared abutment's
                             height is NOT mine to choose: Crossings owns the
                             deck at exactly y = 30.6 ... so the last 6 m of
                             ribbon is PINNED there".  A pinned deck has no
                             freedom, so its legal set is the one point the
                             contract names -- STRICTLY NARROWER than any
                             interval `network.build_segment` computes, so
                             this can only refuse more, never less.
    """
    out = dict(c)
    why: list[str] = []
    contract = corpus["contract"]
    nodes = _handoffs_by_node(corpus["abutment_handoffs"])
    if out.get("bank_level_run_m") is None and contract.get("bank_level_run_m"):
        out["bank_level_run_m"] = contract["bank_level_run_m"]
        why.append("bank_level_run_m from contract.bank_level_run_m")
    ya = (out.get("bank_a") or {}).get("road_deck_y")
    yb = (out.get("bank_b") or {}).get("road_deck_y")
    if out.get("single_H_both_banks") is None and ya is not None:
        out["single_H_both_banks"] = bool(yb is not None
                                          and abs(ya - yb) <= 1e-6)
        why.append(f"single_H_both_banks MEASURED from the two banks' own "
                   f"road_deck_y ({ya} / {yb})")
    if out.get("required_deck_y") is None and out.get("single_H_both_banks"):
        out["required_deck_y"] = ya
        why.append("required_deck_y from the banks' agreed road_deck_y")
    datum = contract.get("deck_datum_y")
    if (out.get("legal_deck_y_interval") is None and datum is not None
            and out.get("required_deck_y") is not None
            and abs(out["required_deck_y"] - datum) <= 1e-6):
        out["legal_deck_y_interval"] = [datum, datum]
        why.append(f"legal_deck_y_interval pinned to the single point "
                   f"contract.deck_datum_y {datum} -- this crossing's deck "
                   f"IS that datum, and network.py pins the last "
                   f"{contract.get('bank_level_run_m')} m of ribbon to it")
    if out.get("ribbon_width_m") is None:
        widths = {}
        for end in ("bank_a", "bank_b"):
            node = (out.get(end) or {}).get("node")
            for h in nodes.get(node, ()):
                if h.get("ribbon_width_m") is not None:
                    widths.setdefault(end, set()).add(h["ribbon_width_m"])
        got = declared_deck_width(widths)
        if got is not None:
            out["ribbon_width_m"] = got
            why.append(
                f"ribbon_width_m {got} = the widest carriageway of the "
                f"abutment_handoffs at the banks' own nodes "
                f"({(out['bank_a'] or {}).get('node')}: "
                f"{sorted(widths.get('bank_a', ()))} / "
                f"{(out['bank_b'] or {}).get('node')}: "
                f"{sorted(widths.get('bank_b', ()))}) "
                f"[DECLARED_WIDTH_DEPENDS_ON]")
    out["filled_from"] = why
    return out, why


def abutment_segments(c: dict, corpus: dict) -> list[str]:
    """The segments whose TERRAIN must already be written for this deck.

    A crossing ON a segment has one.  A crossing that JOINS two segments has
    its own `segment_id` NULL and its precondition is BOTH of the segments
    its two abutment nodes belong to -- which is why `segment_key(
    span["segment"])` could only ever answer for the first kind.
    """
    if c.get("segment_id"):
        return [c["segment_id"]]
    nodes = _handoffs_by_node(corpus["abutment_handoffs"])
    out: list[str] = []
    for end in ("bank_a", "bank_b"):
        node = (c.get(end) or {}).get("node")
        for h in nodes.get(node, ()):
            if h.get("segment_id") and h["segment_id"] not in out:
                out.append(h["segment_id"])
    return out


def spans_from_network(path: Path | None = None) -> tuple[list[dict], dict]:
    """Every crossing the ROAD NETWORK declares, as spans, with its census.

    Returns `(spans, population)`.  The population is the two-number form the
    project requires of every count: how many crossings the network declares
    and how many of them this call could turn into a buildable span, so an
    empty answer is legible as a bug rather than as a world with no water in
    it.
    """
    records, corpus = crossing_corpus(path)
    spans, refused = [], []
    for raw in records:
        c, filled = fill_from_the_contract(raw, corpus)
        seg_id = c.get("segment_id")
        missing = [src for dst, src in SPAN_FROM_CROSSING.items()
                   if src != "segment_id" and c.get(src) is None]
        segs = abutment_segments(c, corpus)
        if not segs:
            missing.append("segment_id/abutment_handoffs[node].segment_id")
        for end in ("bank_a", "bank_b"):
            b = c.get(end) or {}
            if b.get("xz") is None or b.get("ground_y") is None:
                missing.append(f"{end}.xz/ground_y")
        if missing:
            refused.append(dict(crossing_id=c.get("crossing_id", "?"),
                                segment=seg_id, missing=sorted(missing)))
            continue
        span = {dst: c.get(src) for dst, src in SPAN_FROM_CROSSING.items()}
        # `segment` is the one this deck is FILED under; `terrain_segments`
        # is the set whose ground must already be written, and for a crossing
        # that joins two segments those are not the same thing.
        span["segment"] = seg_id or segs[0]
        span["terrain_segments"] = segs
        span["bank_a"] = dict(c["bank_a"])
        span["bank_b"] = dict(c["bank_b"])
        span["bed_min_y"] = c.get("gap_min_y")
        span["status"] = c.get("status")
        # A bank the detector found at its segment's OWN end has no bench
        # beyond it (network.CROSSING_BANKS_DEPENDS_ON); carried so the
        # abutment refusal can name the terminus as the owner.
        span["terminal_banks"] = list(c.get("terminal_banks") or ())
        span["filled_from"] = filled
        span["depth_below_plane_m"] = (
            c.get("clamp_evidence") or {}).get("max_depth_below_plane_m")
        span["pile_bed_to_deck_m"] = (
            None if c.get("gap_min_y") is None
            else round(c["required_deck_y"] - c["gap_min_y"], 3))
        span["spec_source"] = (f"{Path(corpus['source']).name}:"
                               f"{c.get('status')}:{c['crossing_id']}")
        spans.append(span)
    pop = dict(declared=len(records), enumerated=len(spans),
               refused=refused, source=corpus["source"],
               declared_in_file=corpus["declared"],
               detected_in_file=corpus["detected"],
               instrument="roads/boardwalk.py::crossing_corpus over "
                          "roads/crossings.yaml `declared` UNION `detected`, "
                          "deduplicated by crossing_id with declared winning "
                          "per that file's own `precedence` -- the same "
                          "population accept/sections/roads.py judges")
    return spans, pop


def network_segments_yaml() -> Path:
    """The run's own seed's network plan -- see `crossings_yaml`."""
    return worldseed.segments_path()


def project_onto_centreline(spans: list[dict],
                            path: Path | None = None) -> list[dict]:
    """Put each abutment ON THE ROAD, using the crossing's own recorded line.

    `north_spans.json`'s `bank_a`/`bank_b` are the WATER-EDGE coordinates the
    crossing search found; they are not guaranteed to sit on the centreline
    the ribbon actually wrote.  MEASURED over all 16 northern crossings, the
    lateral offset of a bank coordinate from its own crossing's recorded
    `centreline_straight_from_to` runs 0.00 to 5.05 m -- and a 6 m trunk
    ribbon benches only +/-4 m, so an offset past that lands the abutment on
    natural ground beside the road.

    That is exactly what refused two spans, and the refusal was about the
    COORDINATE and not about the road:

        N1-x05 bank_a  lat -4.54 m   applied 31.122  step +1.518 m
               projected onto the line  applied 32.640  step +0.000 m
        N3-x01 bank_a  lat +5.05 m   applied 31.508  step +0.992 m
               projected onto the line  applied 32.493  step +0.007 m

    The ground never moved.  The probe was 5 m off the road, reading the
    batter and reporting it as the bench -- a check confidently answering a
    question it was not measuring.

    `centreline_straight_from_to` is a RECORDED field written by
    `network.build_segment` beside `required_deck_y`, so this reads the
    authority rather than inventing a line.  The correction is recorded per
    span in `bank_projection` and never applied silently.
    """
    import yaml  # noqa: PLC0415
    # BOTH NETWORK FILES, ALWAYS -- ON THE SEED THAT HAS TWO.  A crossing's
    # recorded centreline lives in whichever segments file its own segment
    # was routed into - the northern chain in `north_segments.yaml`,
    # everything else in the seed's own plan - and reading only one of them
    # silently returns "no centreline recorded" for every crossing in the
    # other, which is the projection SKIPPED rather than refused.  The
    # northern chain is Pirate68's alone (`segment_files`), so a fresh seed
    # reads its own plan and never Ulfsland's.  An explicit `path` is read
    # LAST, so it wins where the same crossing_id appears twice.
    cross: dict[str, dict] = {}
    for p in (*segment_files(), *((path,) if path else ())):
        if not p.exists():
            continue
        doc = yaml.safe_load(p.read_text())
        for s in doc.get("segments") or ():
            for c in s.get("crossings", []):
                cross[c["crossing_id"]] = c
    out = []
    for span in spans:
        c = cross.get(span["crossing_id"])
        line = (c or {}).get("centreline_straight_from_to")
        if not line:
            span = dict(span)
            span["bank_projection"] = {
                "applied": False,
                "why": "no centreline_straight_from_to recorded for this "
                       "crossing; the spec bank coordinates are used as given"}
            out.append(span)
            continue
        (p0x, p0z), (p1x, p1z) = line
        dx, dz = p1x - p0x, p1z - p0z
        L = math.hypot(dx, dz)
        ux, uz = dx / L, dz / L
        span = json.loads(json.dumps(span))       # deep copy, spec untouched
        proj = {"applied": True, "centreline": line, "ends": {}}
        for end in ("bank_a", "bank_b"):
            x, z = span[end]["xz"]
            t = (x - p0x) * ux + (z - p0z) * uz
            lat = (x - p0x) * uz - (z - p0z) * ux
            px, pz = p0x + ux * t, p0z + uz * t
            proj["ends"][end] = {"spec_xz": [x, z],
                                 "lateral_off_centreline_m": round(lat, 3),
                                 "projected_xz": [round(px, 3), round(pz, 3)]}
            span[end]["xz"] = [round(px, 3), round(pz, 3)]
        span["bank_projection"] = proj
        out.append(span)
    return out


# The reconciliation samples at the same 1 m the gate's carriage walk uses,
# so a wet metre counted here is a wet metre counted there.
RECONCILE_SAMPLE_M = 1.0
# A projection is a LATERAL move; it must not change how long the crossing
# is.  0.5 m is half a sample, so a span that moved by a whole sample cannot
# pass as unchanged.
RECONCILE_SPAN_TOLERANCE_M = 0.5


def _line_census(ax: float, az: float, bx: float, bz: float,
                 surface, deck_y: float, water_y: float = WATER_LEVEL_M
                 ) -> dict:
    """What a crossing IS, measured along one line: length, wet, bed-vs-deck."""
    L = math.hypot(bx - ax, bz - az)
    n = max(int(round(L / RECONCILE_SAMPLE_M)), 1) + 1
    wet = bed_above = 0
    worst = 0.0
    for i in range(n):
        t = L * i / (n - 1)
        x, z = ax + (bx - ax) * t / L, az + (bz - az) * t / L
        bed = surface(x, z)
        if bed <= water_y:
            wet += 1
        if bed > deck_y:
            bed_above += 1
            worst = max(worst, bed - deck_y)
    return {"span_m": round(L, 3), "samples": n, "wet": wet,
            "bed_above_deck": bed_above, "worst_bed_above_deck_m": round(worst, 3)}


def reconcile_declared_lines(records: list[dict], surface,
                             water_y: float = WATER_LEVEL_M) -> dict:
    """PUT EACH CROSSING'S DECLARED BANKS ON ITS OWN RECORDED CENTRELINE.

    THE REPAIR `span_coverage_guard` HAS BEEN PRESCRIBING SINCE IT WAS
    WRITTEN, performed on the corpus instead of printed at a human.  A
    crossing record carries TWO lines: `bank_a`->`bank_b`, which the detector
    found at the water's edge, and `centreline_straight_from_to`, which
    `network.build_segment` recorded as the line the ribbon actually wrote.
    The gate walks the FIRST and the deck must stand on the SECOND, and
    MEASURED over this corpus they are up to 6.07 m apart -- twice the deck's
    own half-width, so on five declarations no deck that stands on the road
    can ever cover the span its own record declares.

    THE MOVE IS PURELY LATERAL AND IT IS REFUSED IF IT IS NOT.  Each bank is
    projected onto the recorded line, which preserves its along-track
    position exactly, and the record is patched ONLY IF all three hold:

      the span length changes by less than RECONCILE_SPAN_TOLERANCE_M;
      the projected line is WET on at least as many metres as the declared
        one -- a reconciliation that walks the crossing onto dry land has
        deleted an obligation rather than located it;
      the projected line's bed stands above the declared deck on no more
        metres than the declared line's -- the premise may not get worse.

    A record failing any of them is REFUSED BY NAME and left exactly as it
    was, because a corpus edit that silently shortens a crossing is the one
    outcome worse than the defect.
    """
    out = {"reconciled": [], "refused": [], "no_centreline": [],
           "already_on_the_line": [],
           "instrument": ("roads/boardwalk.py::reconcile_declared_lines, "
                          "orthogonal projection onto the record's own "
                          "`centreline_straight_from_to`, both lines "
                          f"censused at {RECONCILE_SAMPLE_M:g} m against the "
                          "APPLIED surface")}
    for c in records:
        cid = c.get("crossing_id")
        line = c.get("centreline_straight_from_to")
        deck_y = c.get("required_deck_y")
        if not line or deck_y is None:
            out["no_centreline"].append(cid)
            continue
        (p0x, p0z), (p1x, p1z) = line
        dx, dz = p1x - p0x, p1z - p0z
        L = math.hypot(dx, dz)
        if L <= 0.0:
            out["refused"].append({"crossing_id": cid,
                                   "why": "the recorded centreline has zero "
                                          "length"})
            continue
        ux, uz = dx / L, dz / L
        ends, lats = {}, {}
        for end in ("bank_a", "bank_b"):
            x, z = c[end]["xz"]
            t = (x - p0x) * ux + (z - p0z) * uz
            lats[end] = round((x - p0x) * uz - (z - p0z) * ux, 3)
            ends[end] = [round(p0x + ux * t, 3), round(p0z + uz * t, 3)]
        if max(abs(v) for v in lats.values()) <= 1e-3:
            out["already_on_the_line"].append(cid)
            continue
        was = _line_census(*c["bank_a"]["xz"], *c["bank_b"]["xz"],
                           surface, deck_y, water_y)
        now = _line_census(*ends["bank_a"], *ends["bank_b"],
                           surface, deck_y, water_y)
        row = {"crossing_id": cid, "lateral_off_centreline_m": lats,
               "was_xz": {k: list(c[k]["xz"]) for k in ("bank_a", "bank_b")},
               "now_xz": ends, "declared_line": was, "projected_line": now}
        why = []
        if abs(now["span_m"] - was["span_m"]) > RECONCILE_SPAN_TOLERANCE_M:
            why.append(f"the span would change from {was['span_m']:.3f} m to "
                       f"{now['span_m']:.3f} m, more than "
                       f"{RECONCILE_SPAN_TOLERANCE_M} m: this is not a "
                       f"lateral move")
        if now["wet"] < was["wet"]:
            why.append(f"the projected line is wet on {now['wet']} of "
                       f"{now['samples']} metres against {was['wet']} of "
                       f"{was['samples']} declared: the crossing would be "
                       f"walked onto dry land, which deletes the obligation "
                       f"instead of locating it")
        if now["bed_above_deck"] > was["bed_above_deck"]:
            why.append(f"the projected line's bed stands above the declared "
                       f"deck on {now['bed_above_deck']} metres against "
                       f"{was['bed_above_deck']}: the premise gets worse")
        if why:
            row["why"] = why
            out["refused"].append(row)
            continue
        out["reconciled"].append(row)
    return out


def apply_reconciliation(recon: dict, paths: list[Path]) -> dict:
    """Write the accepted projections into every file that carries them.

    BOTH FILES, ALWAYS.  `roads/crossings.yaml` is what the gate judges and
    `roads/segments.yaml` is what the rest of the network tooling reads; a
    reconciliation applied to one of them recreates the two-populations
    defect it exists to end.  The original coordinate and the lateral offset
    are recorded on the record itself, so the move is auditable from the
    corpus without this report.
    """
    import yaml                                          # noqa: PLC0415
    by_id = {r["crossing_id"]: r for r in recon["reconciled"]}
    touched: dict[str, list[str]] = {}
    for p in paths:
        if not p.exists():
            continue
        doc = yaml.safe_load(p.read_text())
        hit = []
        buckets = [doc.get("declared") or [], doc.get("detected") or []]
        buckets += [s.get("crossings") or [] for s in (doc.get("segments") or [])]
        for bucket in buckets:
            for c in bucket:
                r = by_id.get(c.get("crossing_id"))
                if r is None:
                    continue
                c["bank_reconciled_to_centreline"] = {
                    "by": "roads/boardwalk.py::reconcile_declared_lines",
                    "was_xz": r["was_xz"],
                    "lateral_off_centreline_m": r["lateral_off_centreline_m"],
                    "declared_line": r["declared_line"],
                    "projected_line": r["projected_line"]}
                c["bank_a"]["xz"] = list(r["now_xz"]["bank_a"])
                c["bank_b"]["xz"] = list(r["now_xz"]["bank_b"])
                hit.append(c["crossing_id"])
        if hit:
            p.write_text(yaml.safe_dump(doc, default_flow_style=False,
                                        sort_keys=True))
        touched[p.name] = sorted(hit)
    return touched


def bearing_of(span: dict) -> float:
    """Valheim bearing A->B: 0 = +Z, 90 = +X, clockwise from above."""
    ax, az = span["bank_a"]["xz"]
    bx, bz = span["bank_b"]["xz"]
    return math.degrees(math.atan2(bx - ax, bz - az)) % 360.0


def span_zones(span: dict, margin_m: float = 24.0) -> list[tuple[int, int]]:
    ax, az = span["bank_a"]["xz"]
    bx, bz = span["bank_b"]["xz"]
    zs = set()
    lo_x, hi_x = min(ax, bx) - margin_m, max(ax, bx) + margin_m
    lo_z, hi_z = min(az, bz) - margin_m, max(az, bz) + margin_m
    x = lo_x
    while x <= hi_x + 1e-9:
        z = lo_z
        while z <= hi_z + 1e-9:
            zs.add((math.floor((x + 32.0) / 64.0),
                    math.floor((z + 32.0) / 64.0)))
            z += 16.0
        x += 16.0
    return sorted(zs)


# --------------------------------------------------------------------------
# THE DUPLICATE-DECLARATION CENSUS
# --------------------------------------------------------------------------
#
# `roads` COUNTS DECLARATIONS AND CALLS THEM CROSSINGS.  MEASURED on Ulfsland
# 2026-09-22 over the network's own 15 crossing records plus the 2 detected
# ones, three pairs are ONE PHYSICAL CHANNEL DECLARED TWICE:
#
#   W5-brgs1-wsouth-x01  == W10-brgs1-treenear-x01   (-326,-66)->(-310,-154)
#   W7-wwest-wnorthwest-x01 == W9-vestvik-lhwest-x01 (-1142,1778)->(-1164,1788)
#   T4-meadhall-stathub-x01 == T7-stathub-stenvik-x02  SHARED endpoint
#                                                      (568,1042), other ends
#                                                      2.83 m apart
#
# That defect is threefold and it is one sentence.  The DENOMINATOR of
# "crossings carrying a walker" double-counts; the undecked METRE TOTAL
# charges one channel twice; and the NUMERATOR credits ONE BODY TWICE -- the
# 59-piece deck landed at seq 5377-5379 answers both T4-x01 and T7-x02, PROVEN
# by whole-save ZDO census, 43 pieces within 3.5 m of T4-x01's line and all 43
# of them inside T7-x02's 59.  And a builder that takes the declarations at
# face value puts a second deck in the same cubic metres as the first.
#
# NEITHER OBVIOUS TEST WORKS, AND BOTH FAILURES ARE MEASURED:
#   ENDPOINT EQUALITY   misses T4-x01/T7-x02 -- the ends differ by 2.83 m.
#   PIECE OVERLAP       misses W7-x01/W9-x01 -- BOTH corridors hold ZERO build
#                       pieces, so there is nothing to intersect; and it
#                       FALSE-POSITIVES on S1/S12-x01, which share 14 of
#                       S12's 17 pieces (0.824 min-side) and are DIFFERENT
#                       CHANNELS.  Collapsing those two would have silently
#                       halved a real obligation.
#
# So the test is CORRIDOR AREA at the deck's own MEASURED half-width.  Over
# all 17 declarations the nearest duplicate scores 0.728 and the nearest
# non-duplicate 0.136 -- a 5.4x empty band, so 0.5 is not a tuned tolerance,
# it is the middle of nothing.

# MEASURED off the solid, never the nominal: `assemble.bridge` places
# `width_tiles=3` wood_floor at lateral -2.0/0.0/+2.0 m and `wood_floor` is a
# 2.000 m solid, so the decked surface runs -3.000..+3.000 m about the
# centreline.
DECK_HALF_WIDTH_M = 3.000
# One overhang station beyond each bank, the same 2 m station pitch
# `assemble.bridge` uses.
CORRIDOR_OVERHANG_M = 2.0
CORRIDOR_SAMPLE_M = 0.5
CORRIDOR_DUPLICATE_FRACTION = 0.5


def corridor_of(span: dict, half_w: float = DECK_HALF_WIDTH_M,
                overhang_m: float = CORRIDOR_OVERHANG_M) -> dict:
    """The rectangle a deck for this span would actually occupy."""
    ax, az = span["bank_a"]["xz"]
    bx, bz = span["bank_b"]["xz"]
    length = math.hypot(bx - ax, bz - az)
    if length <= 0.0:
        raise SystemExit(f"{span.get('crossing_id')}: zero-length span, so it "
                         f"has no corridor to compare")
    ux, uz = (bx - ax) / length, (bz - az) / length
    return dict(ox=ax - ux * overhang_m, oz=az - uz * overhang_m,
                ux=ux, uz=uz, length=length + 2 * overhang_m, half_w=half_w)


def _inside(c: dict, x: float, z: float) -> bool:
    dx, dz = x - c["ox"], z - c["oz"]
    t = dx * c["ux"] + dz * c["uz"]
    if t < 0.0 or t > c["length"]:
        return False
    lat = dx * c["uz"] - dz * c["ux"]
    return abs(lat) <= c["half_w"]


def corridor_overlap(a: dict, b: dict,
                     sample_m: float = CORRIDOR_SAMPLE_M) -> dict:
    """Fraction of one corridor's area lying inside the other, MAX of the two
    directions, by lattice sampling at `sample_m`.

    The max rather than the mean, because a short span wholly inside a long
    one is a duplicate of it and the mean would dilute exactly that case
    below any threshold.
    """
    ca, cb = corridor_of(a), corridor_of(b)
    out = []
    for src, dst in ((ca, cb), (cb, ca)):
        n_t = max(1, int(round(src["length"] / sample_m)))
        n_l = max(1, int(round(2 * src["half_w"] / sample_m)))
        inside = 0
        for i in range(n_t):
            t = (i + 0.5) * src["length"] / n_t
            for j in range(n_l):
                lat = -src["half_w"] + (j + 0.5) * 2 * src["half_w"] / n_l
                x = src["ox"] + src["ux"] * t + src["uz"] * lat
                z = src["oz"] + src["uz"] * t - src["ux"] * lat
                inside += _inside(dst, x, z)
        out.append(inside / (n_t * n_l))
    return dict(fraction=round(max(out), 3),
                both_directions=[round(v, 3) for v in out],
                sample_m=sample_m, half_width_m=DECK_HALF_WIDTH_M,
                instrument="roads/boardwalk.py::corridor_overlap, lattice "
                           "sampling of each corridor against the other")


def collapse_duplicate_declarations(
        spans: list[dict],
        threshold: float = CORRIDOR_DUPLICATE_FRACTION,
        sample_m: float = CORRIDOR_SAMPLE_M) -> tuple[list[dict], dict]:
    """One span per PHYSICAL CHANNEL, and every collapsed declaration NAMED.

    Returns `(kept, census)`.  The census carries both numbers the project
    requires of any count -- how many declarations went in and how many
    distinct channels came out -- plus every pair that met the threshold with
    its measured fraction, so a collapse can be argued with rather than
    merely trusted.
    """
    groups: list[list[int]] = []
    pairs = []
    owner: dict[int, int] = {}
    for i in range(len(spans)):
        for j in range(i + 1, len(spans)):
            ov = corridor_overlap(spans[i], spans[j], sample_m)
            if ov["fraction"] < threshold:
                continue
            pairs.append(dict(a=spans[i]["crossing_id"],
                              b=spans[j]["crossing_id"], **ov))
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
    dropped: dict[str, str] = {}
    for g in groups:
        if len(g) < 2:
            continue
        keep = min(g)
        for k in sorted(g):
            if k != keep:
                dropped[spans[k]["crossing_id"]] = spans[keep]["crossing_id"]
    kept = [s for s in spans if s["crossing_id"] not in dropped]
    census = dict(
        declarations=len(spans), distinct_channels=len(kept),
        collapsed=dropped,
        pairs_over_threshold=sorted(pairs, key=lambda p: -p["fraction"]),
        threshold=threshold, sample_m=sample_m,
        half_width_m=DECK_HALF_WIDTH_M,
        declared_m=round(sum(s["span_m"] for s in spans), 2),
        distinct_m=round(sum(s["span_m"] for s in kept), 2),
        instrument="roads/boardwalk.py::collapse_duplicate_declarations over "
                   "corridor area at the MEASURED deck half-width; endpoint "
                   "equality misses a 2.83 m divergence and piece overlap "
                   "both misses an unbuilt pair and false-positives on "
                   "S1/S12-x01")
    return kept, census


# THE FIXTURE, VERBATIM FROM THE GATE'S OWN SPAN CENSUS ON SAVE
# 2a6940a934b531ade393fdc9ba0a459ac7e8e7598a88d9cedbe010d50bad22fc.  These
# three are the pair the REJECTED ALTERNATIVE gets wrong: S1's bank_a
# (-312,-64) is 2.0 m from S12-x01's bank_a (-312,-62), which gives a piece-
# overlap census 0.824 min-side coverage -- 14 of S12-x01's 17 pieces -- on
# two channels that are not the same channel.  S1 and S2 are `declared`
# records in roads/crossings.yaml with segment_id null, which is why they are
# not in segments.yaml and why a census that only reads segments.yaml never
# meets them.
CENSUS_FIXTURE = {
    "S1": dict(bank_a=(-312.0, -64.0), bank_b=(-324.0, -64.0), span_m=12.0),
    "S12-brgs1-boathouse-x01": dict(bank_a=(-312.0, -62.0),
                                    bank_b=(-284.0, -138.0), span_m=81.0),
    "S2": dict(bank_a=(-318.0, 230.0), bank_b=(-348.0, 246.0), span_m=34.0),
}


def _fixture_span(cid: str) -> dict:
    f = CENSUS_FIXTURE[cid]
    return {"crossing_id": cid, "span_m": f["span_m"],
            "bank_a": {"xz": list(f["bank_a"])},
            "bank_b": {"xz": list(f["bank_b"])}}


# THE KNOWN-BAD STATE OF ARM 1, CONSTRUCTED: ONE CHANNEL DECLARED TWICE.
# `route.find_crossings` runs per segment, so a channel two segments both
# have to get across is detected twice under two names, with banks that
# need not coincide: here B's banks are 2.83 m from A's at each end (2 m
# along, 2 m across), which an endpoint-equality census (0.5 m) misses and
# the corridor-area census (0.63 of the strip, over the 0.5 threshold)
# collapses.  This was read off the committed Ulfsland corpus's own
# duplicates, so a regenerated world with none made `nothing collapsed` a
# statement about the world; the rule's check is this pair.
DUPLICATE_FIXTURE = {
    "DUP-a-x01": dict(bank_a=(0.0, 0.0), bank_b=(40.0, 0.0), span_m=40.0),
    "DUP-b-x01": dict(bank_a=(2.0, 2.0), bank_b=(42.0, 2.0), span_m=40.0),
}


def _duplicate_fixture_spans() -> list[dict]:
    return [{"crossing_id": cid, "span_m": f["span_m"],
             "bank_a": {"xz": list(f["bank_a"])},
             "bank_b": {"xz": list(f["bank_b"])}}
            for cid, f in DUPLICATE_FIXTURE.items()]


def _endpoint_equality_collapse(spans: list[dict],
                                tol_m: float = 0.5) -> dict[str, str]:
    """THE REJECTED ALTERNATIVE #1, implemented only so the case can show it
    failing: collapse two declarations when their endpoint pairs coincide in
    either order."""
    out: dict[str, str] = {}
    for i in range(len(spans)):
        for j in range(i + 1, len(spans)):
            a, b = spans[i], spans[j]
            ends = [(a["bank_a"]["xz"], a["bank_b"]["xz"]),
                    (b["bank_a"]["xz"], b["bank_b"]["xz"])]
            same = all(math.dist(ends[0][k], ends[1][k]) <= tol_m
                       for k in (0, 1))
            flip = all(math.dist(ends[0][k], ends[1][1 - k]) <= tol_m
                       for k in (0, 1))
            if same or flip:
                out[b["crossing_id"]] = a["crossing_id"]
    return out


class _FrameTile:
    """The smallest thing `tile_frame` can be asked about: a placed square.

    CONSTRUCTED, NEVER BORROWED.  The fixture below must not lean on a span,
    a save, a corpus or a real prefab, because the moment it does it inherits
    that artefact's mutability and goes QUIET instead of red.
    """

    __slots__ = ("x", "z", "yaw")

    def __init__(self, x: float, z: float, yaw: float):
        self.x, self.z, self.yaw = x, z, yaw


# 22.5 deg is the WORST case and not a taste: the defect rotates the tested
# square by 2*yaw and a square is invariant under 90 deg, so 2*yaw = 45 deg is
# the furthest a square can be from itself.  Ulfsland's own W10 sits at
# 170.346 deg -> 2*yaw = 340.7 deg, 19.3 deg off square, which is 43% of this.
FRAME_FIXTURE_YAW_DEG = 22.5


def _place_local(tile: "_FrameTile", along: float, lat: float
                 ) -> tuple[float, float]:
    """Where a point at (along, lat) IN THE TILE'S FRAME lands in the world.

    This is `assemble.Placed.aabb`'s own forward map, written out, so the
    fixture is generated by the PLACEMENT convention and `tile_frame` has to
    invert it rather than agree with a second copy of itself.
    """
    y = math.radians(tile.yaw)
    sy, cy = math.sin(y), math.cos(y)
    return (tile.x + lat * cy + along * sy,
            tile.z - lat * sy + along * cy)


def _neuter_tile_frame():
    """RESTORE THE DEFECT: rotate the compass yaw FORWARD, as shipped.

    Returns the undo.  A case that cannot show its check refusing the known-
    bad state has not shown it is a check.
    """
    real = globals()["tile_frame"]

    def forward(p, px: float, pz: float) -> tuple[float, float]:
        yaw = math.radians(p.yaw)
        c, s = math.cos(-yaw), math.sin(-yaw)
        dx, dz = px - p.x, pz - p.z
        return dx * c - dz * s, dx * s + dz * c

    globals()["tile_frame"] = forward

    def undo():
        globals()["tile_frame"] = real
    return undo


def _neuter_tile_covers_everything():
    """THE OTHER DIRECTION'S NEUTER: a coverage test that never refuses.

    ONE NEUTER PER DIRECTION, NOT ONE PER PREDICATE.  `_neuter_tile_frame`
    restores the forward rotation, which can only ever REMOVE coverage, so
    the arm that must refuse ground beyond a tile survives it untouched and
    would be scored unproven.  The defect that arm exists for is the
    opposite one -- "the repair is to widen the tile until nothing is a hole"
    -- and this is it in its purest form.
    """
    real = globals()["tile_covers"]

    def everywhere(p, px: float, pz: float, half: float = TILE_HALF_M) -> bool:
        return True

    globals()["tile_covers"] = everywhere

    def undo():
        globals()["tile_covers"] = real
    return undo


def tile_frame_arms() -> list[dict]:
    """The two directions `tile_covers` must answer, and what the defect does.

    Arm INTERIOR  a point 0.990 m along and 0.990 m lateral of a tile's own
                  centre is INSIDE a half-extent of 1.000 and MUST be carried.
                  Under the forward rotation it maps to 1.400 m on one axis
                  and is refused -- the exact shape of W10's eleven phantom
                  holes.
    Arm OUTSIDE   a point 1.500 m ALONG the tile's own axis is outside and
                  MUST NOT be carried, so the repair is not "cover
                  everything".

    Returned as data so the acceptance gate can register the same two arms
    without a second implementation of either of them.
    """
    t = _FrameTile(0.0, 0.0, FRAME_FIXTURE_YAW_DEG)
    out = []
    for name, along, lat, want in (
            ("interior_point_is_carried", 0.990, 0.990, True),
            ("point_beyond_the_tile_is_not_carried", 1.500, 0.0, False)):
        px, pz = _place_local(t, along, lat)
        got = tile_covers(t, px, pz)
        a, l = tile_frame(t, px, pz)
        out.append(dict(name=name, local=(along, lat), world=(px, pz),
                        want=want, got=got, ok=got == want,
                        read_back=(round(a, 4), round(l, 4))))
    return out


# --------------------------------------------------------------------------
# THE SPAN POPULATION'S DECLARED DEPENDENCY
# --------------------------------------------------------------------------
#
# AN INDEX MUST NAME THE THING IT IS A FUNCTION OF, AND THE NAMING MUST BE
# CHECKED IN BOTH DIRECTIONS.  MEASURED on Ulfsland 2026-09-23, four separate
# instruments in this tree were keyed on a SUPERSET or a SUBSET of what their
# quantity actually depends on, and every one of them was silent:
#
#   ground cache   keyed on the WHOLE-SAVE fingerprint; a bed height is a
#                  function of the TERRAIN alone    -> 2504.6 s re-read for a
#                  deck that moved no ground
#   corridor mask  keyed on BUILT CENTRELINE STATIONS; a declared crossing is
#                  by construction the water the ribbon REFUSED to write and
#                  so contributes no station -> a complete 319-piece deck read
#                  84 of 90, and S1's long-standing deck was invisible to
#                  every gate run ever shipped
#   span population  THIS ONE
#   deck coverage  keyed on a FORWARD rotation of a compass yaw
#
# Over-keying is silent and EXPENSIVE; under-keying is silent and FALSE.  The
# obvious repair for one is the other, so the declaration below is checked by
# a neuter in EACH DIRECTION: a record the key must include, and a record it
# must still include when the neuter fires, so that "the neuter flipped" can
# never be satisfied by a key that simply emptied.
SPAN_POPULATION_DEPENDS_ON = (
    "roads/crossings.yaml `declared` UNION `detected`, deduplicated by "
    "crossing_id with declared winning per that file's own `precedence`. NOT "
    "roads/segments.yaml segments[].crossings[]: a crossing that JOINS two "
    "segments carries segment_id NULL and appears in no segment, so a "
    "population derived from segments is a strict SUBSET of the obligation "
    "the gate judges.")


def _neuter_span_population_segments_only():
    """RESTORE THE DEFECT: key the population on the SEGMENTS, as shipped.

    Drops exactly the records a segment-derived population cannot reach --
    the ones whose `segment_id` is null -- and nothing else, so the arm that
    must SURVIVE is untouched and cannot be passed by an empty answer.
    """
    real = globals()["crossing_corpus"]

    def segments_only(path: Path | None = None):
        records, corpus = real(path)
        return ([c for c in records if c.get("segment_id")], corpus)

    globals()["crossing_corpus"] = segments_only

    def undo():
        globals()["crossing_corpus"] = real
    return undo


# The two records the declaration is checked on, and why each one.  A JOINER
# is a crossing whose own `segment_id` is null; S1 is the only one on this
# world with an obligation outstanding, and its deck is the named remedy for
# three separate gate refusals.
SPAN_POPULATION_JOINER = "FIX-joiner-x01"
SPAN_POPULATION_ON_A_SEGMENT = "FIX-built-x01"


def _span_population_fixture_doc() -> dict:
    """The population these arms judge, CONSTRUCTED: a crossings file with one
    JOINER (`segment_id` null, as S1/S2 are) and several crossings that lie
    on a segment.  The arms read `roads/crossings.yaml` of whatever world was
    running, so a world with no joiner (Fresh42) had nothing for the segments
    key to lose and the neuter "did not flip" -- red for a reason about a
    WORLD.  The rule is a check of the population KEY, on a population built
    for the check; a world's own corpus is not read here."""
    fx = _remedy_fixtures()["records"]
    detected = [dict(fx[c], status="detected")
                for c in ("FIX-built-x01", "FIX-refused-x01", "FIX-short-x01",
                          "FIX-long-x01")]
    joiner = dict(fx["FIX-joiner-x01"], status="declared", segment_id=None)
    return {"declared": [joiner], "detected": detected,
            "contract": {"bank_level_run_m": 6.0, "deck_datum_y": 30.6},
            "abutment_handoffs": [],
            "precedence": "declared wins over detected"}


def _corpus_truth(path: Path | None = None) -> list[dict]:
    """The corpus AS THE FILE HOLDS IT, unreachable by any neuter.

    A fixture's denominator may never come from the instrument under test.
    Both span-population neuters replace the module-global
    `crossing_corpus`, so an arm that counted its own subset through that
    name would lose numerator and denominator together and report a perfect
    score over an empty world.
    """
    import yaml                                          # noqa: PLC0415
    doc = yaml.safe_load((path or crossings_yaml()).read_text())
    out, seen = [], set()
    for key in ("detected", "declared"):
        for c in (doc.get(key) or ()):
            cid = c.get("crossing_id")
            if cid is None or cid in seen:
                continue
            seen.add(cid)
            out.append(c)
    return out


def span_population_arms(path: Path | None = None) -> list[dict]:
    """Is every crossing the GATE judges ENUMERATED by the BUILDER?

    Arm JOINER       EVERY crossing with segment_id NULL must be enumerated.
                     This is the subset the segments-keyed population loses,
                     and losing it is losing an obligation silently.
    Arm ON_SEGMENT   EVERY crossing that lies on a segment must be enumerated
                     -- under the neuter as well.  Without this arm a
                     population that returned nothing at all would score as
                     "the neuter flipped".

    NON-EMPTY IS NOT DISCRIMINATING, AND THIS ARM PAIR WAS WRITTEN BREAKING
    THAT RULE BY THE AGENT WHO STATED IT.  As first shipped, each arm named
    ONE crossing -- S1 and W10-brgs1-treenear-x01 -- so against a population
    of 17 declarations the EVIDENCE was 1 and 1.  An arm with one
    discriminating row is one accident from having none: fold S1 into a
    segment and the loss arm stops describing anything and goes QUIET rather
    than RED, which is exactly how three cases in two sections died on
    2026-09-21.  The repair is CacheKey's rule 2 -- assert on the whole
    population at risk, not on one member of it -- so both arms now compare
    the FULL SUBSET and report POPULATION and EVIDENCE separately.  The two
    named constants survive only as the rows the refusal quotes first.

    ENUMERATION, NOT BUILDABILITY, is the property asserted.  The defect was
    that `segments.yaml` could not SEE a joiner at all; whether a seen record
    then yields a buildable span is a separate question with its own refusals
    -- S2 is enumerated, and since DECLARED_WIDTH_DEPENDS_ON it is a span
    too (its deck takes the widest road at either bank), so a check that
    demanded S2 be buildable is the abutment guard's to answer, not this
    population's.
    """
    import tempfile                                          # noqa: PLC0415
    import yaml                                              # noqa: PLC0415
    if path is None:
        with tempfile.TemporaryDirectory() as _td:
            fx_path = Path(_td) / "crossings.fixture.yaml"
            fx_path.write_text(yaml.safe_dump(_span_population_fixture_doc(),
                                              sort_keys=False))
            return span_population_arms(fx_path)
    records, _corpus = crossing_corpus(path)
    seen = {c.get("crossing_id") for c in records}
    # THE POPULATION AT RISK IS READ FROM THE FILE, NEVER FROM THE
    # (POSSIBLY NEUTERED) ENUMERATION.  `crossing_corpus` is the thing both
    # neuters replace, so taking the denominator from it would shrink the
    # denominator with the numerator and every arm would pass VACUOUSLY --
    # the degenerate-survivor trap arriving inside the fixture built to
    # catch it.  `_corpus_truth` re-reads the yaml and cannot be patched.
    truth = _corpus_truth(path)
    joiners = [c["crossing_id"] for c in truth if not c.get("segment_id")]
    on_seg = [c["crossing_id"] for c in truth if c.get("segment_id")]
    out = []
    for name, subset, first in (
            ("a_joiner_is_a_buildable_span", joiners,
             SPAN_POPULATION_JOINER),
            ("a_crossing_on_a_segment_is_a_buildable_span", on_seg,
             SPAN_POPULATION_ON_A_SEGMENT)):
        missing = [c for c in subset if c not in seen]
        out.append(dict(name=name, crossing_id=first,
                        got=not missing, want=True, ok=not missing,
                        declared=len(truth), population=len(truth),
                        evidence=len(subset), subset=subset,
                        missing=missing, enumerated=len(seen)))
    return out


def _neuter_span_population_empty():
    """THE OTHER DIRECTION'S NEUTER: a population that returns NOTHING.

    The survivor arm exists so that "the neuter flipped" cannot be satisfied
    by a key that simply emptied, and an arm asserting a record is PRESENT is
    not disarmed by `_neuter_span_population_segments_only` -- dropping the
    joiners cannot remove a crossing that has a segment.  So this arm needs
    its own, and the defect it models is the degenerate over-correction: a
    population so narrowly keyed that it names no obligation at all, which
    reads as "nothing to build" and is the quietest possible failure.
    """
    real = globals()["crossing_corpus"]

    def empty(path: Path | None = None):
        _records, corpus = real(path)
        return ([], corpus)

    globals()["crossing_corpus"] = empty

    def undo():
        globals()["crossing_corpus"] = real
    return undo

# --------------------------------------------------------------------------
# CAN THIS TREE PRODUCE A DECK FOR THIS CROSSING?  THE BUILDER'S OWN ANSWER.
# --------------------------------------------------------------------------
#
# THE STATE THIS EXISTS FOR.  `accept/sections/roads.py` classified every
# undecked crossing whose PREMISE was sound as ACTIONABLE -- "a repair
# exists, the writer permits it, and it destroys nothing" -- on the strength
# of one sentence: "the remedy is a deck, which writes no terrain".  IT NEVER
# ASKED WHETHER A DECK COULD BE PRODUCED.  MEASURED on Ulfsland
# 7632b3a5674842bd, all EIGHT of them are refused by THIS builder before a
# single tile is placed:
#
#   S6-stenvik-hognest-x01    0/41   no terrain_write; declaration 3.652 m off
#   S13-stathub-dock-x01      0/44   no terrain_write; declaration 5.439 m off
#   W7-wwest-wnorthwest-x01   0/25   no terrain_write
#   W9-vestvik-lhwest-x02     0/72   no terrain_write; declaration 4.628 m off
#   S2-stathub-eastlobe-x01   7/53   no terrain_write; declaration 6.069 m off
#   T5-temple-dockshore-x01   0/29   declaration 3.618 m off
#   T4-meadhall-stathub-x02  19/49   declaration 4.077 m off, AND ITS 217-PIECE
#                                    DECK ALREADY STANDS: no deck on the road
#                                    can cover a line 4.077 m off the road
#   T9-wttown-dockshore-sb01  0/7    declared deck 30.600 stands 0.400 m BELOW
#                                    its own bank's APPLIED-measured ground
#
# A ROW THE ONLY BUILDER IN THE TREE CANNOT BUILD IS NOT A WORK ITEM.  Eight
# of the gate's twelve remaining ACTIONABLE rows were manufactured work.
DECK_REMEDY_DEPENDS_ON = (
    "the crossing's OWN record in roads/crossings.yaml -- its declared banks, "
    "its deck datum and its segment's recorded `centreline_straight_from_to` "
    "-- UNION the ledger's terrain_write set. NOT on the generated bed: the "
    "abutment step and the structural verdict are taken against the APPLIED "
    "surface at the deck's own landfall, which this predicate does not read. "
    "MEASURED, and this is why the generated bed may never stand in for it: "
    "W10-brgs1-treenear-x01's corpus ground_y puts its bank_a step at +0.950 "
    "m, over the 0.35 m limit, while the APPLIED surface one metre inland -- "
    "where its outermost surviving tile actually lands -- puts it at +0.260 "
    "m, and that span's 319-piece deck STANDS. A predicate keyed on the "
    "generated bed would refuse a deck that is built. AND THAT IS A "
    "POPULATION, NOT AN ANECDOTE. APPLIED MINUS GENERATED IS ITS OWN "
    "QUANTITY, and it is not the adjacent-vertex gradient and not the "
    "bilinear-versus-governing-vertex offset -- three different numbers "
    "that have each been quoted for the others tonight. MEASURED by "
    "CacheKey over 57 generated tiles against the live applied index: "
    "393,533 paired vertices, median 0.0254 m, p90 0.0703, p99 1.7765, "
    "worst 5.9861, signed -5.9861..+4.7616. 86,104 of them -- 21.9 % -- "
    "disagree by more than the 0.05 m burial tolerance, 13,735 by more "
    "than the +0.260 m step W10 actually takes, and 7,602 by more than "
    "the +0.950 m the generated bed would have refused it on. So an "
    "abutment verdict this predicate cannot take is reported ABSENT, "
    "never CLEAR, and a crossing with no refusal here is UNJUDGED rather "
    "than buildable.")

# EVERY PRECONDITION THIS FUNCTION NAMES, ASKED OR NOT -- and it is DERIVED
# FROM the function rather than declared beside it, because the first version
# of this tuple was NEITHER A SUPERSET NOR A SUBSET of the truth.
#
# MEASURED, on my own file, after CacheKey audited theirs the same way: the
# tuple declared 3 names, `deck_remedy` produces 5, the intersection is 2, one
# declared name (`deck_above_its_own_bank`) was never produced by any branch,
# and three produced names (`reconciliation`, `abutment`, `structural`) were
# never declared.  READERS OF THE TUPLE: ZERO.  READERS OF THE
# `APPLIED_BED_TOKEN` CONSTANT BESIDE IT: ZERO.  An inventory nothing reads
# cannot be wrong loudly, so it was wrong quietly -- the key class, in the
# declaration written to state the key.
#
# `deck_remedy_vector_arms` now scores the union of `asked` and `unasked`
# against this tuple on a live record, so NARROWING THE FUNCTION TO STOP
# ASKING SOMETHING REDDENS THE GATE instead of passing green.
DECK_REMEDY_PRECONDITIONS = ("segment_terrain_write",
                             "span_coverage",
                             "reconciliation",
                             "abutment",
                             "bank_regrade_reach",
                             "structural")
# CONDITIONAL MEANS "ASKED ONLY DOWN ONE BRANCH", NEVER "MAY BE ABSENT".
# `reconciliation` is only asked when the span-coverage offset is over the
# half-width, and `bank_regrade_reach` only when `abutment_guard` has run AND
# refused a bank; on every other record each is reported UNASKED BY NAME.
#
# THIS TUPLE HAD ZERO READERS when it was written -- CrossClass's own third
# instance of the key class, in the constant added to state the key -- so it
# is READ HERE: `deck_remedy_reach_arms` requires every conditional name to
# be NAMED on a record that does not reach its branch, which is the only
# property a conditional precondition has that an unconditional one does not.
DECK_REMEDY_CONDITIONAL = ("reconciliation", "bank_regrade_reach")

# THE CLASS, AND THE ONLY WAY TO GET ONE.  BuryFix's audit of my own
# chokepoint claim, and they were right: `deck_remedy` makes `buildable=True`
# structurally unreachable, so "assume a deck closes this" cannot be had by
# FORGETTING to ask -- but the remaining wrong answer moved into the CONSUMER,
# because `buildable is None` means UNJUDGED and a caller writing
# `if not ans["buildable"]` gets the opposite of what it reads like.  A
# sentence in a docstring warning about that is a RULE, not a chokepoint.
#
# So the consumer never reads `buildable` at all.  It calls this, and this
# CANNOT RETURN "ACTIONABLE": the string is not reachable from any branch, for
# any answer, including `None`.  The wrong answer is unspellable rather than
# discouraged.
DECK_REMEDY_CLASSES = ("1C_UNSERVED", "1E_OUT_OF_ORDER", "UNKNOWN")


def deck_remedy_class(answer) -> str:
    """The termination class implied by a builder answer.  NEVER ACTIONABLE.

    `answer` is `deck_remedy`'s return, or None when the builder was not
    asked at all.  Both absences -- not asked, and asked with nothing this
    predicate owns refusing -- return UNKNOWN, because the abutment and
    structural verdicts need the APPLIED surface and were not taken.

    AND IT REFUSES ANYTHING THAT IS NOT ONE OF THOSE TWO THINGS.  This
    accessor was written over a live name collision and the collision has
    since been REMOVED rather than documented: `buildable` was TWO
    QUANTITIES under one key -- this function's tri-state verdict, and an
    INTEGER COUNT of enumerated spans in `span_population_arms` and in
    `spans_from_network`'s `pop`, an alias DeckBuild kept only to avoid
    editing `accept/sections/roads.py` while another agent held it.  The
    count is now `enumerated` everywhere and the word `buildable` names
    exactly one quantity in this module.

    THE RAISE STAYS, because the reason it was added outlives the
    collision: an accessor that read a FALSY value as "unjudged" would
    have silently accepted the count 0, and 0 enumerated spans is exactly
    the state the degenerate-over-correction neuter produces -- an
    instrument certifying a blind one, in the hardening written to stop
    it.  The general rule the rename came from is A PUBLISHED KEY MUST
    NAME ITS REDUCTION, NOT JUST ITS SUBJECT: `buildable` named the
    subject and left the second reduction nowhere to go.  `None` is the
    ONLY absence this function accepts, and it means "not asked".
    """
    if answer is None:
        return "UNKNOWN"
    if not isinstance(answer, dict) or "refused_by" not in answer:
        raise TypeError(
            f"deck_remedy_class was handed {type(answer).__name__} "
            f"{answer!r}, which is not a `deck_remedy` answer. Only None "
            f"-- meaning THE BUILDER WAS NOT ASKED -- is accepted as an "
            f"absence; a count, a bool or an empty dict is a different "
            f"quantity and reading it as 'unjudged' is the mislabel this "
            f"accessor exists to refuse.")
    got = answer.get("implies")
    return got if got in DECK_REMEDY_CLASSES else "UNKNOWN"


def _bank_offsets(record: dict) -> dict | None:
    """Each declared bank's lateral distance from its segment's OWN recorded
    centreline, or None when the record declares no centreline."""
    cl = record.get("centreline_straight_from_to")
    if not cl or len(cl) != 2:
        return None
    ax, az = (float(v) for v in cl[0])
    bx, bz = (float(v) for v in cl[1])
    L = math.hypot(bx - ax, bz - az)
    if L <= 0.0:
        return None
    ux, uz = (bx - ax) / L, (bz - az) / L
    out = {}
    for end in ("bank_a", "bank_b"):
        px, pz = (float(v) for v in record[end]["xz"])
        out[end] = round((px - ax) * uz - (pz - az) * ux, 3)
    return out


# WHICH SEGMENT FILES CARRY THE STATIONS.  BOTH, ALWAYS: Ulfsland's two road
# networks are in different files and a reach computed from one of them
# reports "no station outboard" for every crossing on the other -- the
# undeliverable verdict, handed out by construction to a whole network.
def segment_files() -> tuple[Path, ...]:
    """The plans a bank's approach is looked up in: the run's own seed's
    network plan, and the northern network, which is Pirate68's alone --
    `worldseed.segment_files`, the one resolver of that pair."""
    return worldseed.segment_files()


# HOW FAR OFF THE SPAN AXIS A STATION MAY LIE and still be this bank's
# approach.  The ribbon's own half-bench is 4 m at its widest trunk width, so
# 8 m is two of them: wide enough that a bank whose approach curves away is
# still credited with it, narrow enough that a DIFFERENT leg passing nearby
# is not counted as this abutment's bench.
BANK_REACH_LATERAL_M = 8.0

BANK_REGRADE_REACH_DEPENDS_ON = (
    "the crossing's OWN `segment_id` and both declared bank coordinates; "
    "that segment's `nodes` AND its `is_bridge` flags, from BOTH segment "
    "files; and the record's own `bank_level_run_m`. EVERY ONE OF THOSE IS "
    "LOAD-BEARING AND TWO OF THEM ARE THE WHOLE POINT.  `is_bridge` is what "
    "separates a station the ribbon WRITES from one it deliberately SKIPS "
    "because a deck is meant to carry it -- counting a bridged station as "
    "regradeable is claiming a bench on the ground under the bridge.  And "
    "the DIRECTION is what separates the approach OUTBOARD of the bank from "
    "the span's own inboard stations: T5-temple-dockshore carries 677.69 m "
    "of its own road INBOARD of bank_a and 0.00 m outboard of bank_b, and a "
    "reach that drops the sign reports the second as 677 m of bench.\n"
    "\n"
    "WHAT THIS IS NOT: it is NOT the abutment step and it may never be read "
    "as one.  The step is taken against the APPLIED surface at the deck's "
    "own landfall by `abutment_guard`, and a BANK-COORDINATE probe is "
    "MEASURED WRONG on a deck that stands -- W10-brgs1-treenear-x01's "
    "bank_a reads +0.987 m at the bank coordinate and its 319-piece deck is "
    "in the ground, because its outermost surviving tile lands where the "
    "ground has already reached H.  So this answers only the SECOND "
    "question, asked after that guard has already refused: IS THERE ANY "
    "GROUND HERE THAT AN EMISSION OF THIS SEGMENT COULD EVER REGRADE.")


def bank_regrade_reach(record: dict, segments: dict | None = None,
                       lateral_m: float = BANK_REACH_LATERAL_M) -> dict | None:
    """Per bank: how many metres of REGRADEABLE station of the crossing's own
    segment lie OUTBOARD of it -- or None when the question was never asked.

    THE THIRD STATE IS REAL AND IT IS NOT ZERO.  A crossing that JOINS two
    segments has no segment of its own and a corpus whose segment file has
    been filtered has no stations; both are NEVER ASKED, and returning 0.0 m
    for either would hand out the most terminal class this module can
    produce on the strength of a missing input.  `0.0` here means MEASURED
    ZERO: the bank is the segment's own terminus and there is nothing past
    it.

    MEASURED over Ulfsland's 15 crossings with a segment, 2026-09-23: 11 of
    30 banks declare a `bank_level.level_at_y` their own segment has no
    station to carry, and 7 of those have ZERO metres outboard.  On
    T5-temple-dockshore-x01 bank_b the number is 0.00 m of a declared 6.0 m
    run, because that bank IS station 525 of 526 and `is_bridge[525]` is
    True -- the last station of the road is inside the bridged run, so the
    far abutment's bench is ground no emission of that segment touches.
    """
    sid = record.get("segment_id")
    if not sid:
        return None
    if segments is None:
        segments = {}
        for p in segment_files():
            if not p.exists():
                continue
            import yaml                                   # noqa: PLC0415
            doc = yaml.safe_load(p.read_text()) or {}
            for s in doc.get("segments") or ():
                segments[s["id"]] = s
    seg = segments.get(sid)
    if seg is None or not seg.get("nodes"):
        return None
    try:
        ax, az = (float(v) for v in record["bank_a"]["xz"])
        bx, bz = (float(v) for v in record["bank_b"]["xz"])
    except (KeyError, TypeError, ValueError):
        return None
    L = math.hypot(bx - ax, bz - az)
    if L <= 0.0:
        return None
    ux, uz = (bx - ax) / L, (bz - az) / L
    nodes = seg["nodes"]
    is_bridge = seg.get("is_bridge") or [False] * len(nodes)
    run = float(record.get("bank_level_run_m") or 0.0)
    out = {"segment": sid, "run_required_m": run,
           "stations": len(nodes),
           "instrument": "roads/boardwalk.py::bank_regrade_reach",
           "depends_on": BANK_REGRADE_REACH_DEPENDS_ON}
    for end, px, pz, sign in (("bank_a", ax, az, -1.0),
                              ("bank_b", bx, bz, +1.0)):
        any_m = regrade_m = 0.0
        for i, n in enumerate(nodes):
            t = sign * ((n[0] - px) * ux + (n[1] - pz) * uz)
            if t <= 0.05:
                continue                       # inboard, or the bank itself
            if abs((n[0] - px) * uz - (n[1] - pz) * ux) > lateral_m:
                continue                       # a different leg passing by
            any_m = max(any_m, t)
            if not is_bridge[i]:
                regrade_m = max(regrade_m, t)
        out[end] = {"outboard_m": round(any_m, 3),
                    "regradeable_m": round(regrade_m, 3),
                    "reaches_the_declared_run":
                        (regrade_m + 1e-6 >= run) if run > 0.0 else None}
    return out


def deck_remedy(record: dict, *, terrain_written, reconciled=None,
                abutment_steps=None, segments=None,
                half_width_m: float = DECK_HALF_WIDTH_M) -> dict:
    """Would this builder produce a deck for this crossing?  THREE STATES.

      buildable False   at least one precondition THIS BUILDER tests refuses,
                        so the remedy a gate would call ACTIONABLE cannot be
                        produced by the only tool that performs it.
      buildable None    every precondition this function owns is clear AND the
                        ones it does not own were never asked.  UNJUDGED.  It
                        is never True, because the abutment and structural
                        verdicts need the applied surface -- see
                        `DECK_REMEDY_DEPENDS_ON`.  A consumer that reads None
                        as "a deck closes this" is committing the defect this
                        function exists to refuse.

    EACH REFUSAL CARRIES THE CLASS IT IMPLIES, because the two are different
    facts and only this function holds the second one:

      1E_OUT_OF_ORDER  every instrument the remedy needs EXISTS IN THIS TREE
                       and a PREREQUISITE has not run.  FACTORY WORK.
      1C_UNSERVED      no tool in this tree performs the remedy at all.

    `terrain_written` is the mapping `segment_terrain_written` returns, or
    None when the chain was not read.  `reconciled` maps a crossing_id to
    `reconcile_declared_lines`' own verdict for it, or None when that tool
    was not run.  `abutment_steps` maps a bank name to the SIGNED step in
    metres `abutment_guard` measured there against the APPLIED surface, or
    None when that guard was not run -- and it is the only way the abutment
    branch below becomes ASKED.  UNREAD IS NOT CLEAR on any of the three:
    an unasked precondition is reported UNASKED, never passed.
    """
    cid = record.get("crossing_id")
    refused: list[dict] = []
    asked: list[str] = []
    unasked: list[str] = []
    nums: dict = {}

    # (1) BUILD ORDER.  `--require-terrain` defaults ON, and it is not a
    # preference: a deck's abutment is a step from the ROAD BENCH, and on a
    # segment nobody has graded there is no bench to step from.
    #
    # THE ATTRIBUTION KEY IS `params.name`, AND THE ANSWER STATES SO.
    # `segment_terrain_written` asks `instruments.record_moves_bed` - the
    # WIRE the game received rather than the op the writer chose - but
    # MEASURED on Ulfsland's 5,433 records, 548 of the 737 bed-moving
    # records carry NO name at all (zones_generate 420, retire 123,
    # objects_clear 3, zones_restore 2), so 74 % of bed movements are
    # unattributable and the honest sentence is "NO RECORD ATTRIBUTES A BED
    # MOVEMENT TO THIS SEGMENT", never "no ground moved here".
    seg = record.get("segment_id")
    if terrain_written is None:
        unasked.append("segment_terrain_write")
    elif not seg:
        # A crossing that JOINS two segments has no segment of its own, so
        # this precondition has no subject.  Stated, not silently passed.
        unasked.append("segment_terrain_write")
        nums["segment_terrain_write_has_no_subject"] = True
    else:
        asked.append("segment_terrain_write")
        got = terrain_written.get(segment_key(seg))
        nums["segment_terrain_write"] = got
        if got is None:
            refused.append(dict(
                precondition="segment_terrain_write",
                sub_class="build_order", implies="1E_OUT_OF_ORDER",
                prerequisite=(f"segment {seg}: no bed-moving record in this "
                              f"chain is attributed to "
                              f"{segment_key(seg)!r}"),
                performed_by="roads/network.py + roads/ribbon.py",
                why=(f"{cid}: NO bed-moving record in this chain names "
                     f"{segment_key(seg)!r}, so this builder refuses before "
                     f"any piece is generated: a deck's abutment is a step "
                     f"from the road BENCH and there is no bench. The "
                     f"remedy is not a deck, it is GRADE THE ROAD AND THEN "
                     f"DECK IT -- two tools in this tree, in an order that "
                     f"has not run.")))

    # (2) SPAN COVERAGE, AND THEN ITS OWN REMEDY'S VERDICT.  A declared bank
    # further off its segment's recorded centreline than the deck is
    # half-wide can never be covered by any deck this tree builds on that
    # road: the declared line and the built line are two different lines and
    # a deck stands on one of them.
    #
    # BUT THE OFFSET IS THE SYMPTOM, NOT THE CLASS.  `reconcile_declared_lines`
    # PERFORMS this repair - it projects the record onto its own centreline -
    # and it refuses by name when the projection loses a wet metre or puts
    # the bed above the deck.  MEASURED over this corpus: S6 (3.652 m off),
    # T5 (3.618) and W9-x02 (4.628) RECONCILE CLEANLY, while T4-x02 (4.077),
    # S2-eastlobe (6.069) and S13 (5.439) are REFUSED BY THE TOOL - the
    # record and the centreline describe DIFFERENT WATER.  The offset does
    # not order them; only the guards do.  So a big offset whose
    # reconciliation PASSES is 1E - a tool performs it and has not run - and
    # only a REFUSED reconciliation is 1C.
    off = _bank_offsets(record)
    # A CONDITIONAL PRECONDITION IS STILL NAMED ON A RECORD THAT DOES NOT
    # REACH IT.  `reconciliation` used to vanish entirely from both lists
    # whenever the offset was inside the half-width -- 8 of the 15 records
    # with a segment -- so the inventory said "this answer does not depend
    # on the reconciler" where it meant "this record never had to ask".
    # The same class the tuple itself was written to state, one branch
    # further in; caught by `every_conditional_precondition_is_still_named`
    # the first time `DECK_REMEDY_CONDITIONAL` was given a reader.
    if off is None or abs(off[max(off, key=lambda k: abs(off[k]))]) \
            <= half_width_m:
        unasked.append("reconciliation")
    if off is None:
        unasked.append("span_coverage")
        nums["centreline_recorded"] = False
    else:
        asked.append("span_coverage")
        nums["centreline_recorded"] = True
        nums["bank_offsets_m"] = off
        nums["deck_half_width_m"] = half_width_m
        worst = max(off, key=lambda k: abs(off[k]))
        nums["worst_bank_offset_m"] = off[worst]
        if abs(off[worst]) > half_width_m:
            verdict = (reconciled or {}).get(cid)
            nums["reconciliation"] = verdict
            # ASKED IS RECORDED WHEN IT IS ASKED.  The first form of this
            # branch only ever appended `reconciliation` to `unasked`, so a
            # record whose reconciliation WAS consulted reported neither --
            # the precondition vector came back 4 of 5 and the name simply
            # vanished.  A precondition that disappears from the inventory
            # when it is used is the inventory naming a SUBSET of what the
            # answer depends on, which is the whole class; caught by
            # `deck_remedy_vector_arms` on its first run.
            if reconciled is None or verdict is None:
                # The remedy's own tool was NOT RUN.  That is not a licence
                # to call the row either served or unserved.
                unasked.append("reconciliation")
            elif verdict.get("verdict") == "refused":
                asked.append("reconciliation")
                refused.append(dict(
                    precondition="span_coverage",
                    sub_class="declaration_off_its_own_centreline",
                    implies="1C_UNSERVED",
                    performed_by=None,
                    why=(f"{cid}: {worst} is {abs(off[worst]):.3f} m off its "
                         f"own segment's recorded centreline, MORE THAN THE "
                         f"DECK'S {half_width_m:.3f} m half-width, AND "
                         f"`reconcile_declared_lines` REFUSES to move it: "
                         + "; ".join(verdict.get("why") or ())
                         + f". The record and the centreline describe "
                         f"DIFFERENT WATER, so no deck and no projection "
                         f"closes this. THE REMEDY IS A CORRECTION TO THE "
                         f"PRODUCER -- roads/route.py::find_crossings, whose "
                         f"bank coordinates are taken from the PRE-STRAIGHTEN "
                         f"polyline and never re-read after "
                         f"roads/network.py::build_segment straightens the "
                         f"crossing window -- and no tool in this tree "
                         f"performs it.")))
            else:
                asked.append("reconciliation")
                refused.append(dict(
                    precondition="span_coverage",
                    sub_class="declaration_off_its_own_centreline",
                    implies="1E_OUT_OF_ORDER",
                    prerequisite=(f"{cid}: `boardwalk.py reconcile` has not "
                                  f"been run on this record"),
                    performed_by="roads/boardwalk.py reconcile",
                    why=(f"{cid}: {worst} is {abs(off[worst]):.3f} m off its "
                         f"own segment's recorded centreline, MORE THAN THE "
                         f"DECK'S {half_width_m:.3f} m half-width, so no "
                         f"deck on the road covers the declared line -- but "
                         f"`reconcile_declared_lines` ACCEPTS the projection "
                         f"for this record, so the remedy is RECONCILE THE "
                         f"CORPUS AND THEN DECK IT, in an order that has not "
                         f"run.")))

    # (3) THE ABUTMENT, AND THE SECOND QUESTION NOBODY WAS ASKING.
    #
    # The STEP itself is not taken here -- it needs the APPLIED surface at
    # the deck's own landfall, and `DECK_REMEDY_DEPENDS_ON` holds the W10
    # measurement that forbids substituting the generated bed for it.  When
    # the caller HAS run `abutment_guard` it hands the signed steps in and
    # this branch becomes ASKED; otherwise the absence is published, exactly
    # as before, and no consumer may read it as a pass.
    #
    # WHAT IS NEW IS THE SECOND QUESTION.  A refused abutment used to be an
    # unjudged row, because "the bench is 1.5 m low" does not say whether
    # anything in this tree can raise it.  IT IS DECIDED BY REACH, NOT BY
    # THE STEP: `ribbon` writes a segment's NON-BRIDGED stations, so a bank
    # with regradeable stations outboard of it is a REGRADE THAT HAS NOT RUN
    # (1E, factory work), and a bank with NONE is ground no emission of that
    # segment ever touches (1C, and the only remaining direction is a FILL
    # on ground nobody owns).
    #
    # MEASURED, Ulfsland 2026-09-23: T5-temple-dockshore-x01's bank_b is the
    # segment's LAST station, 525 of 526, and `is_bridge[525]` is True --
    # 0.00 m of its own declared 6.0 m run, against a +1.500 m step.  11 of
    # 30 banks over 8 of 15 crossings declare a bank level their own segment
    # has no station to carry; 7 have ZERO metres outboard.
    reach = bank_regrade_reach(record, segments)
    nums["bank_regrade_reach"] = reach
    over = {} if not abutment_steps else {
        k: float(v) for k, v in abutment_steps.items()
        if v is not None and abs(float(v)) > ABUTMENT_STEP_MAX_M}
    # UNREAD IS NOT CLEAR.  `ctx.ground` returns None for a point the save
    # never wrote, so a caller that probed this crossing and got nothing
    # back hands in a mapping of Nones.  Read as "the guard ran and refused
    # nothing" that is absence-reads-as-fine, on the one branch in this
    # function that can publish a terminal class -- so a probe that read NO
    # bank is the SAME state as not having run at all.
    unread = [k for k, v in (abutment_steps or {}).items() if v is None]
    if abutment_steps is not None and len(unread) == len(abutment_steps):
        abutment_steps, over = None, {}
        nums["abutment_probe_read_no_bank"] = True
    if unread and abutment_steps is not None:
        # A PARTIALLY read probe still judges the banks it DID read, and
        # names the ones it did not: the honest middle state.
        nums["abutment_banks_unread"] = sorted(unread)
    if abutment_steps is None:
        unasked.append("abutment")
        unasked.append("bank_regrade_reach")
    elif not over:
        # The guard RAN and refused nothing.  Asked, and clear -- the one
        # state the shipped form could not express at all.
        asked.append("abutment")
        unasked.append("bank_regrade_reach")
        nums["abutment_steps_m"] = dict(abutment_steps)
    elif reach is None:
        asked.append("abutment")
        unasked.append("bank_regrade_reach")
        nums["abutment_steps_m"] = dict(abutment_steps)
        nums["bank_regrade_reach_has_no_subject"] = True
    else:
        asked.append("abutment")
        asked.append("bank_regrade_reach")
        nums["abutment_steps_m"] = dict(abutment_steps)
        for end, step in sorted(over.items()):
            r = reach.get(end) or {}
            got = r.get("regradeable_m")
            run = reach.get("run_required_m") or 0.0
            if (_TERMINAL_LANDING_MODE == "lands"
                    and end in (record.get("terminal_banks") or ())
                    and 0.0 < step <= terminal_landing_reach_m(record)):
                # THE BANK IS ITS SEGMENT'S OWN END, SO THE DECK LANDS ITSELF
                # (TERMINAL_LANDING_DEPENDS_ON): the ground sits below the deck
                # by no more than a landing can bring it down, and no road
                # bench was ever going to be there.  Not a refusal of this
                # row -- the build's own guards judge the landing it lays.
                nums.setdefault("terminal_landing", {})[end] = dict(
                    step_m=step,
                    reach_m=round(terminal_landing_reach_m(record), 3))
                continue
            if got is not None and got + 1e-6 >= run and got > 0.0:
                refused.append(dict(
                    precondition="abutment",
                    sub_class="bench_not_graded_to_the_deck",
                    implies="1E_OUT_OF_ORDER",
                    prerequisite=(f"segment {reach['segment']}: the last "
                                  f"{run:g} m of approach at {end} has not "
                                  f"been graded to this crossing's own "
                                  f"bank level"),
                    performed_by="roads/network.py + roads/ribbon.py",
                    repair_direction=("raise" if step > 0 else "cut"),
                    why=(f"{cid}: {end} steps {step:+.3f} m from the applied "
                         f"ground onto the deck, over the "
                         f"{ABUTMENT_STEP_MAX_M:g} m limit -- but "
                         f"{got:.2f} m of NON-BRIDGED station of "
                         f"{reach['segment']} lies outboard of that bank, so "
                         f"an emission of this segment REACHES the bench. "
                         f"The remedy is GRADE THE APPROACH AND THEN DECK "
                         f"IT, in an order that has not run.")))
            else:
                refused.append(dict(
                    precondition="abutment",
                    sub_class="bench_beyond_every_station",
                    implies="1C_UNSERVED",
                    performed_by=None,
                    repair_direction=("raise" if step > 0 else "cut"),
                    why=(f"{cid}: {end} steps {step:+.3f} m from the applied "
                         f"ground onto the deck, over the "
                         f"{ABUTMENT_STEP_MAX_M:g} m limit, and "
                         f"{reach['segment']} has {got:.2f} m of "
                         f"regradeable station outboard of that bank "
                         f"against a declared {run:g} m run "
                         f"({(reach.get(end) or {}).get('outboard_m')} m of "
                         f"station of ANY kind). NO EMISSION OF THIS "
                         f"SEGMENT REACHES THAT GROUND -- the bank is the "
                         f"road's own terminus or lies inside its bridged "
                         f"run -- so the only remaining direction is a "
                         f"{'FILL' if step > 0 else 'CUT'} on ground no "
                         f"segment in this tree owns. THIS ROW SETS ITS OWN "
                         f"`implies`; it does not go through "
                         f"settlements.termination_class."
                         + (f" THIS BANK IS THE SEGMENT'S OWN END, so the "
                            f"deck lands itself "
                            f"(TERMINAL_LANDING_DEPENDS_ON) -- but a landing "
                            f"brings a deck down "
                            f"{terminal_landing_reach_m(record):.2f} m at most "
                            f"(a deck over water stands "
                            f"{routemod.DECK_CLEARANCE_M:g} m above it) and "
                            f"this ground is {step:+.3f} m from it: no "
                            f"landing meets it -- a slope or open water, "
                            f"not a shore."
                            if end in (record.get("terminal_banks") or ())
                            else ""))))
    unasked.append("structural")

    return dict(crossing_id=cid, buildable=(False if refused else None),
                refused_by=refused, asked=asked, unasked=unasked,
                sub_classes=sorted({r["sub_class"] for r in refused}),
                # 1C IS TERMINAL AND 1E IS NOT, so a row that is both is
                # 1C: grading the road does not make a declaration the
                # reconciler has refused any more coverable.
                implies=("1C_UNSERVED"
                         if any(r["implies"] == "1C_UNSERVED"
                                for r in refused)
                         else ("1E_OUT_OF_ORDER" if refused else None)),
                depends_on=DECK_REMEDY_DEPENDS_ON, numbers=nums)


# The two records the declaration is checked on, and why each one.  REFUSED is
# a crossing whose projection `reconcile_declared_lines` REFUSES BY NAME -- it
# loses a wet metre AND puts the bed above the deck on a metre where it was 0
# -- so nothing in this tree closes it; SURVIVOR is one this builder HAS
# BUILT, a 319-piece deck standing at y 32.403 over 90 of its 90 declared
# metres, so an over-correction that refused everything could not pass by
# agreeing with the refusing arm.
DECK_REMEDY_REFUSED = "FIX-refused-x01"
DECK_REMEDY_SURVIVOR = "FIX-built-x01"


def _remedy_fixtures() -> dict:
    """The crossings these arms judge, CONSTRUCTED HERE: no world, no corpus.

    These arms were keyed on `T4-meadhall-stathub-x02` and
    `W10-brgs1-treenear-x01` -- rows of ONE world's committed corpus -- and
    on `T5`/`S6` for the reach.  A regenerated world carries none of them
    (the Ulfsland regen's W10 was a rejected leg), so every arm answered
    "the corpus holds no record" and the checks of this TOOL's logic were
    red for a reason that is about a WORLD.  A check of a rule is a
    constructed case of the rule (`_reach_fixture`, `tile_frame_arms`).

    Every record is a straight 60 m crossing along +x from (0, 0), its
    centreline recorded on that line, with the segment it lies on:

      FIX-built-x01    both banks ON the centreline; the segment carries
                       road outboard of both: a deck that STANDS (offset 0)
      FIX-refused-x01  bank_b 4.077 m off the centreline -- MORE than the
                       3.0 m deck half-width (T4-x02's measured offset)
      FIX-short-x01    bank_b IS the segment's last station: 0 m outboard
                       (and 90 m of road outboard of bank_a)
      FIX-long-x01     the same bank with 77.54 m of non-bridged road
                       outboard of it (S6's measured reach)
      FIX-joiner-x01   `segment_id` null, as S1/S2: it lies on no segment
    """
    def segment(sid, xs, bridged_to):
        return {"id": sid, "nodes": [[float(x), 0.0] for x in xs],
                "is_bridge": [0.0 < x <= bridged_to for x in xs]}

    def record(cid, sid, bank_b_z=0.0):
        return {"crossing_id": cid, "segment_id": sid,
                "bank_a": {"xz": [0.0, 0.0]},
                "bank_b": {"xz": [60.0, bank_b_z]},
                "centreline_straight_from_to": [[0.0, 0.0], [60.0, 0.0]],
                "bank_level_run_m": 6.0, "status": "detected"}
    around = [-30.0 + 10.0 * k for k in range(13)]          # -30 .. 90
    upto = [10.0 * k for k in range(7)]                     # 0 .. 60
    segments = {
        "FIX-built": segment("FIX-built", around, 60.0),
        "FIX-refused": segment("FIX-refused", around, 60.0),
        # 90 m of road OUTBOARD OF bank_a and none of bank_b: T5's shape
        # (677 m behind its bank_a, 0 m past its bank_b), the state in which
        # a reach that loses the DIRECTION reports the far bank as served
        "FIX-short": segment("FIX-short", [-90.0 + 10.0 * k for k in range(9)]
                             + upto[1:], 60.0),
        "FIX-long": segment("FIX-long", upto + [70.0, 80.0, 90.0, 100.0,
                                                110.0, 120.0, 137.54], 60.0)}
    records = {r["crossing_id"]: r for r in (
        record("FIX-built-x01", "FIX-built"),
        record("FIX-refused-x01", "FIX-refused", bank_b_z=4.077),
        record("FIX-short-x01", "FIX-short"),
        record("FIX-long-x01", "FIX-long"),
        record("FIX-joiner-x01", None))}
    return {"records": records, "segments": segments}

# The reconciler's OWN verdict for the refusing arm, quoted from the run that
# produced it rather than re-derived: the projection is a surface measurement
# and these arms may not depend on a save being present.
DECK_REMEDY_REFUSED_VERDICT = {
    "verdict": "refused",
    "why": ["projecting onto its own centreline loses a wet metre "
            "(42/49 -> 41/49) and puts the bed ABOVE the deck on 1 metre "
            "where it was 0"]}


def deck_remedy_arms(path: Path | None = None) -> list[dict]:
    """Both directions of the builder's own buildability answer.

    Arm REFUSED    a crossing whose declaration lies further off its own
                   centreline than the deck is half-wide AND whose
                   reconciliation the tool refuses MUST come back
                   not-buildable, as 1C_UNSERVED.  This is the arm the
                   shipped classifier lost by never asking.
    Arm SURVIVOR   a crossing this builder HAS BUILT must NOT come back
                   refused -- under the never-ask neuter as well, so "the
                   neuter flipped" can never be satisfied by a predicate
                   that simply refuses everything.

    BOTH ARMS ARE VERDICTS, NEVER ASSERTIONS.  `run_cases` reads an
    AssertionError as a flip, so an assert-shaped survivor lets a neuter
    that EMPTIED the instrument be stamped PROVEN LOAD-BEARING: a missing
    record would raise, and raising is indistinguishable from refusing.
    Every arm below returns its own `ok` with the numbers beside it.

    `terrain_written` is deliberately NOT read: these arms are about the
    geometry, and passing None reports the build-order precondition
    UNASKED rather than making the answer depend on which segments a
    world happens to have graded.

    THE RECORDS ARE CONSTRUCTED (`_remedy_fixtures`), NEVER READ FROM A
    WORLD'S CORPUS: a check of the builder's rule that names `W10-...` is a
    check of one world, and is red on every regenerated one.  Should the
    fixture ever lack a record the arm still returns the keys its readers
    (accept/sections/roads.py) index, as a failing verdict.
    """
    by_id = _remedy_fixtures()["records"]
    segs = _remedy_fixtures()["segments"]
    recon = {DECK_REMEDY_REFUSED: DECK_REMEDY_REFUSED_VERDICT}
    out = []
    for name, cid, want_refused, want_class in (
            ("a_declaration_its_own_reconciler_refuses_is_not_buildable",
             DECK_REMEDY_REFUSED, True, "1C_UNSERVED"),
            ("a_crossing_this_builder_has_built_is_not_refused",
             DECK_REMEDY_SURVIVOR, False, None)):
        rec = by_id.get(cid)
        if rec is None:
            out.append(dict(name=name, crossing_id=cid, present=False,
                            want_refused=want_refused, got_refused=None,
                            want_class=want_class, got_class=None,
                            ok=False, offsets=None, half_width_m=None,
                            worst_offset_m=None, buildable=None,
                            why="the fixture holds no record under this id"))
            continue
        ans = deck_remedy(rec, terrain_written=None, reconciled=recon,
                          segments=segs)
        hit = [r for r in ans["refused_by"]
               if r["precondition"] == "span_coverage"]
        got_class = ans["implies"]
        out.append(dict(name=name, crossing_id=cid, present=True,
                        want_refused=want_refused, got_refused=bool(hit),
                        want_class=want_class, got_class=got_class,
                        ok=(bool(hit) == want_refused
                            and got_class == want_class),
                        precondition="span_coverage",
                        offsets=ans["numbers"].get("bank_offsets_m"),
                        half_width_m=ans["numbers"].get("deck_half_width_m"),
                        worst_offset_m=ans["numbers"].get(
                            "worst_bank_offset_m"),
                        buildable=ans["buildable"]))
    return out


def deck_remedy_vector_arms(path: Path | None = None) -> list[dict]:
    """THE WHOLE PRECONDITION VECTOR, NOT ONE PAIR OF IT.

    CacheKey's rule, turned on my own file after they turned it on theirs:
    a case that asserts one or two rows of an N-row truth table leaves the
    other N-2 free to go silently un-asserted.  `deck_remedy` names FIVE
    preconditions and the two buildability arms above score exactly one of
    them, so narrowing the function to stop asking `reconciliation` -- which
    is what separates a producer defect from a prerequisite nobody ran --
    would have left every case green.

    Arm VECTOR     the union of `asked` and `unasked` on a live record must
                   be exactly `DECK_REMEDY_PRECONDITIONS`, less the
                   conditional names this record does not reach.
    Arm NO_YES     `deck_remedy_class` must never return ACTIONABLE, for
                   every record in the corpus AND for `None`.  That is the
                   consumer-side hole BuryFix found: `buildable is None`
                   means UNJUDGED, and `if not ans["buildable"]` reads like
                   the opposite of what it does.

    THE DENOMINATOR IS NOT TAKEN FROM ANYTHING A NEUTER REPLACES.
    `DECK_REMEDY_PRECONDITIONS` is a module constant and the record comes
    from the yaml, so a neutered `deck_remedy` shrinks the OBSERVED set
    while the REQUIRED set stands -- DeckBuild's unpatchable-denominator
    discipline, which is how a fixture avoids scoring perfectly over an
    empty world.
    """
    fx = _remedy_fixtures()
    try:
        corpus_records, _corpus = crossing_corpus(path)
    except (FileNotFoundError, OSError):
        corpus_records = []
    # the fixtures decide the arms; the world's records only ADD population
    records = list(fx["records"].values()) + list(corpus_records)
    rec = fx["records"][DECK_REMEDY_REFUSED]
    out = []
    ans = deck_remedy(rec, terrain_written=None,
                      reconciled={DECK_REMEDY_REFUSED:
                                  DECK_REMEDY_REFUSED_VERDICT},
                      segments=fx["segments"])
    observed = sorted(set(ans["asked"]) | set(ans["unasked"]))
    required = sorted(DECK_REMEDY_PRECONDITIONS)
    missing = [n for n in required if n not in observed]
    unexpected = [n for n in observed if n not in required]
    out.append(dict(name="the_whole_precondition_vector_is_named",
                    ok=not missing and not unexpected,
                    crossing_id=DECK_REMEDY_REFUSED,
                    required=required, observed=observed,
                    missing=missing, unexpected=unexpected,
                    population=len(required), evidence=len(observed)))
    # ARM NO_YES - over the WHOLE corpus, in the conditions that reach EVERY
    # class this predicate can produce, plus the not-asked case.  The claim
    # is "this accessor cannot say yes", and an arm run only in conditions
    # where nothing refuses proves nothing: every answer would be UNKNOWN
    # and ACTIONABLE would be absent for want of any verdict at all rather
    # than by construction.  So the corpus is judged with an EMPTY chain
    # (so build_order fires) and with the reconciler REFUSING every
    # over-limit record in turn and ACCEPTING it in turn, which is what
    # reaches 1C and 1E.
    said = {}
    for verdict, tag in (({"verdict": "refused", "why": []}, "refused"),
                         ({"verdict": "reconciled", "why": []}, "clean"),
                         (None, "unrun")):
        recon = (None if verdict is None
                 else {r.get("crossing_id"): verdict for r in records})
        for r in records:
            said[f"{r.get('crossing_id')}|{tag}"] = deck_remedy_class(
                deck_remedy(r, terrain_written={}, reconciled=recon,
                            segments=(fx["segments"]
                                      if r.get("crossing_id") in fx["records"]
                                      else None)))
    said["<builder not asked>"] = deck_remedy_class(None)
    bad = sorted(k for k, v in said.items() if v not in DECK_REMEDY_CLASSES)
    out.append(dict(name="no_answer_can_produce_ACTIONABLE",
                    ok=not bad and len(set(said.values())) > 1,
                    population=len(said), offenders=bad,
                    classes=sorted(set(said.values())),
                    allowed=list(DECK_REMEDY_CLASSES)))
    return out


# THE THREE RECORDS THE REACH IS CHECKED ON, AND WHY EACH ONE.
#
# SHORT     T5-temple-dockshore-x01.  Its bank_b IS the segment's LAST
#           station -- 525 of 526, `is_bridge[525]` True -- so 0.00 m of its
#           own declared 6.0 m run lies outboard of it, against a MEASURED
#           +1.500 m step from applied ground 31.000 onto a deck at 32.500.
#           This is the row the shipped classifier could only call UNJUDGED.
# LONG      S6-stenvik-hognest-x01.  77.54 m of non-bridged station outboard
#           of the same bank.  Identical step, OPPOSITE class -- so an
#           over-correcting reach that answers 0 for everything is caught
#           here rather than being stamped proven by the SHORT arm alone.
# UNTOUCHED W10-brgs1-treenear-x01, whose 319-piece deck STANDS.  Asked with
#           NO abutment verdict, exactly as every current caller asks, it
#           must come back unrefused with both conditional names UNASKED.
#           A bank-COORDINATE probe refuses it (+0.987 m) and would redden
#           `a_crossing_this_builder_has_built_is_not_refused`; this arm is
#           that trap, registered where it can be run rather than remembered.
BANK_REACH_SHORT = "FIX-short-x01"
BANK_REACH_LONG = "FIX-long-x01"
BANK_REACH_UNTOUCHED = DECK_REMEDY_SURVIVOR
# The step both live arms are judged at.  MEASURED at T5's bank_b, and used
# for S6 too so the ONLY difference between the two arms is the reach.
BANK_REACH_STEP_M = 1.500


def _reach_fixture() -> tuple[dict, dict]:
    """A crossing and a segment built here, with no world and no corpus.

    `is_bridge` is a DECLARED dependency of `bank_regrade_reach` that
    TODAY'S CORPUS CANNOT PROVE: MEASURED over all 30 banks, `outboard_m`
    and `regradeable_m` are equal on every one of them, so no live record
    separates "a station exists" from "a station the ribbon writes".  A
    dependency proven on a population that cannot discriminate it is
    declared, not demonstrated -- so the discriminating case is CONSTRUCTED:
    10 m of station outboard of bank_b, EVERY ONE OF THEM BRIDGED.
    """
    nodes = [[0.0, 0.0], [10.0, 0.0], [20.0, 0.0],
             [22.0, 0.0], [24.0, 0.0], [26.0, 0.0], [30.0, 0.0]]
    seg = {"id": "FIX-reach", "nodes": nodes,
           # stations 0..2 are the inboard approach; 3..6 lie OUTBOARD of
           # bank_b at x=20 and are all inside the bridged run.
           "is_bridge": [False, False, False, True, True, True, True]}
    rec = {"crossing_id": "FIX-reach-x01", "segment_id": "FIX-reach",
           "bank_a": {"xz": [0.0, 0.0]}, "bank_b": {"xz": [20.0, 0.0]},
           "bank_level_run_m": 6.0}
    return rec, {"FIX-reach": seg}


def deck_remedy_reach_arms(path: Path | None = None) -> list[dict]:
    """Whether a refused abutment is FACTORY WORK or UNSERVED, both ways.

    FIVE ARMS, and the last two are about the inventory rather than the
    geometry:

      SHORT       a bank with no regradeable station outboard of it must
                  come back 1C_UNSERVED -- no emission of that segment
                  reaches the ground, so the remedy is a fill nobody owns.
      LONG        the SAME step at a bank with 77 m of it must come back
                  1E_OUT_OF_ORDER.  This is the arm an emptied reach fails.
      UNTOUCHED   the built deck, asked the way every caller asks, must stay
                  unrefused with both conditional names UNASKED.
      PARTITION   `asked` and `unasked` must be DISJOINT.  The vector arm
                  takes their UNION, so a name landing in both is invisible
                  to it -- CrossClass named that gap in their own arm and
                  this is the reader for it.
      CONDITIONAL every name in `DECK_REMEDY_CONDITIONAL` must be NAMED on a
                  record that reaches NEITHER branch.  `DECK_REMEDY_CONDITIONAL`
                  had ZERO readers; unread, "conditional" and "sometimes
                  absent" are the same string.

    EVERY ARM IS A VERDICT.  `run_cases` reads an AssertionError as a flip,
    so an assert-shaped arm lets a neuter that merely EMPTIED the corpus be
    stamped proven: a missing record raises, and raising is indistinguishable
    from refusing.
    """
    fx = _remedy_fixtures()
    by_id = fx["records"]
    records = list(by_id.values())
    out: list[dict] = []
    for name, cid, want_class, want_sub in (
            ("a_bank_no_emission_reaches_is_UNSERVED",
             BANK_REACH_SHORT, "1C_UNSERVED", "bench_beyond_every_station"),
            ("the_same_step_with_road_outboard_is_OUT_OF_ORDER",
             BANK_REACH_LONG, "1E_OUT_OF_ORDER",
             "bench_not_graded_to_the_deck")):
        rec = by_id[cid]
        ans = deck_remedy(rec, terrain_written=None,
                          abutment_steps={"bank_b": BANK_REACH_STEP_M},
                          segments=fx["segments"])
        hit = [r for r in ans["refused_by"]
               if r["precondition"] == "abutment"]
        reach = (ans["numbers"].get("bank_regrade_reach") or {}).get("bank_b")
        out.append(dict(
            name=name, crossing_id=cid, present=True,
            want_class=want_class, got_class=ans["implies"],
            want_sub=want_sub,
            got_sub=(hit[0]["sub_class"] if hit else None),
            ok=(len(hit) == 1 and hit[0]["sub_class"] == want_sub
                and ans["implies"] == want_class),
            step_m=BANK_REACH_STEP_M,
            regradeable_m=(reach or {}).get("regradeable_m"),
            outboard_m=(reach or {}).get("outboard_m"),
            repair_direction=(hit[0].get("repair_direction") if hit
                              else None),
            population=len(records), evidence=1))
    # THE CONSTRUCTED ARM: `is_bridge` is the only thing between these two
    # answers, and no live record can say so.
    frec, fsegs = _reach_fixture()
    fr = bank_regrade_reach(frec, fsegs) or {}
    fb = fr.get("bank_b") or {}
    out.append(dict(
        name="a_bridged_station_is_not_a_bench",
        crossing_id=frec["crossing_id"], present=bool(fr),
        want_class="1C_UNSERVED",
        got_class=("1C_UNSERVED" if fb.get("regradeable_m") == 0.0
                   else "1E_OUT_OF_ORDER"),
        want_sub=None, got_sub=None,
        ok=(fb.get("outboard_m") == 10.0 and fb.get("regradeable_m") == 0.0),
        outboard_m=fb.get("outboard_m"), regradeable_m=fb.get("regradeable_m"),
        population=len(fsegs["FIX-reach"]["nodes"]), evidence=1,
        why=("10 m of station outboard of bank_b, every one of them bridged: "
             "a station the ribbon SKIPS is not a bench")))
    # THE UNTOUCHED ARM, plus the two inventory arms taken on the same answer.
    rec = by_id[BANK_REACH_UNTOUCHED]
    ans = deck_remedy(rec, terrain_written=None, segments=fx["segments"])
    asked, unasked = list(ans["asked"]), list(ans["unasked"])
    both = sorted(set(asked) & set(unasked))
    missing_cond = [n for n in DECK_REMEDY_CONDITIONAL
                    if n not in set(asked) | set(unasked)]
    out.append(dict(
        name="a_built_deck_is_not_refused_by_the_reach",
        crossing_id=BANK_REACH_UNTOUCHED, present=True,
        ok=(not ans["refused_by"] and ans["implies"] is None
            and "abutment" in unasked and "bank_regrade_reach" in unasked),
        got_class=ans["implies"], refusals=len(ans["refused_by"]),
        unasked=unasked, population=len(records), evidence=1,
        why=("this deck STANDS; a reach that refuses it has measured the "
             "bank coordinate instead of the deck's landfall")))
    out.append(dict(
        name="asked_and_unasked_are_disjoint",
        crossing_id=BANK_REACH_UNTOUCHED, present=True,
        ok=not both, in_both=both,
        population=len(set(asked) | set(unasked)), evidence=len(asked),
        why="the vector arm takes the UNION, so a double-listed name is "
            "invisible to it"))
    out.append(dict(
        name="every_conditional_precondition_is_still_named",
        crossing_id=BANK_REACH_UNTOUCHED, present=True,
        ok=not missing_cond, missing=missing_cond,
        required=list(DECK_REMEDY_CONDITIONAL),
        population=len(DECK_REMEDY_CONDITIONAL),
        evidence=len(DECK_REMEDY_CONDITIONAL),
        why="conditional means ASKED DOWN ONE BRANCH, never MAY BE ABSENT"))
    return out


def _neuter_reach_ignores_the_direction():
    """LOSE THE SIGN: count every station of the segment, inboard included.

    The plausible-looking simplification -- "how much road is near this
    bank" -- and it is the one that hides the whole defect. MEASURED on
    T5-temple-dockshore: 677.69 m of its own carriageway lies INBOARD of
    bank_a and 0.00 m outboard of bank_b, so a reach without the sign
    reports the terminus bank as having 677 m of bench and files the row as
    factory work forever.
    """
    real = globals()["bank_regrade_reach"]

    def unsigned(record, segments=None,
                 lateral_m: float = BANK_REACH_LATERAL_M):
        got = real(record, segments, lateral_m)
        if got is None:
            return None
        got = dict(got)
        pair = [got[e]["regradeable_m"] for e in ("bank_a", "bank_b")]
        for end in ("bank_a", "bank_b"):
            row = dict(got[end])
            row["regradeable_m"] = max(pair)
            row["outboard_m"] = max(row["outboard_m"], max(pair))
            row["reaches_the_declared_run"] = (
                max(pair) + 1e-6 >= (got.get("run_required_m") or 0.0))
            got[end] = row
        return got

    globals()["bank_regrade_reach"] = unsigned

    def undo():
        globals()["bank_regrade_reach"] = real
    return undo


def _neuter_reach_answers_zero():
    """THE DEGENERATE OVER-CORRECTION: every bank has nothing outboard.

    The neuter the LONG arm needs, and neither of the other two reaches it.
    Losing the DIRECTION only ever makes a reach BIGGER and losing
    `is_bridge` only ever makes it bigger, so both leave a bank with 77.54 m
    of road outboard answering exactly as it did -- a registered case whose
    own discriminator cannot make it red, which is a case that does not
    describe its own fix.  MEASURED by T9Close against the live selftest,
    which is how this was found.

    The defect it models is the one that matters: a reach that answers 0
    everywhere files every refused abutment as terminal and retires a real
    obligation -- 1E work the factory could do, reported as ground nothing
    in this tree owns.  That is the quietest possible failure and it reads
    as triage.
    """
    real = globals()["bank_regrade_reach"]

    def nothing_anywhere(record, segments=None,
                         lateral_m: float = BANK_REACH_LATERAL_M):
        got = real(record, segments, lateral_m)
        if got is None:
            return None
        got = dict(got)
        for end in ("bank_a", "bank_b"):
            row = dict(got[end])
            row["regradeable_m"] = 0.0
            row["reaches_the_declared_run"] = False
            got[end] = row
        return got

    globals()["bank_regrade_reach"] = nothing_anywhere

    def undo():
        globals()["bank_regrade_reach"] = real
    return undo


def _neuter_reach_ignores_is_bridge():
    """LOSE `is_bridge`: every station counts, bridged or not.

    The other named dependency, and the one no live record can flip -- which
    is exactly why it is neutered against the CONSTRUCTED arm. A bridged
    station is ground the ribbon deliberately does not write because a deck
    is meant to carry it; counting it as a bench claims an abutment on the
    air under the bridge.
    """
    real = globals()["bank_regrade_reach"]

    def blind(record, segments=None,
              lateral_m: float = BANK_REACH_LATERAL_M):
        got = real(record, segments, lateral_m)
        if got is None:
            return None
        got = dict(got)
        for end in ("bank_a", "bank_b"):
            row = dict(got[end])
            row["regradeable_m"] = row["outboard_m"]
            row["reaches_the_declared_run"] = (
                row["outboard_m"] + 1e-6 >= (got.get("run_required_m") or 0.0))
            got[end] = row
        return got

    globals()["bank_regrade_reach"] = blind

    def undo():
        globals()["bank_regrade_reach"] = real
    return undo


def _neuter_deck_remedy_stops_asking_reconciliation():
    """THE NARROWING, RESTORED: `deck_remedy` stops naming `reconciliation`.

    This is the defect the vector arm exists for and the one the two
    buildability arms cannot see.  Without the reconciler's verdict the
    producer defect (T4-x02, whose projection loses a wet metre) and the
    prerequisite nobody ran (S6, T5, W9-x02, whose projections are clean)
    become the same row -- six crossings filed under one class, and every
    other case in this module still green.
    """
    real = globals()["deck_remedy"]

    # THE STAND-IN TAKES THE WHOLE SIGNATURE, INCLUDING KEYWORDS ADDED
    # AFTER IT WAS WRITTEN.  A neuter that cannot be CALLED raises
    # TypeError, and the harness reads any exception from inside the code
    # under test as proving nothing -- so a signature drift silently turns a
    # registered proof into an unproven case.  MEASURED tonight: adding
    # `abutment_steps` to `deck_remedy` did exactly that to
    # `_neuter_deck_remedy_refuses_everything`.
    def narrowed(record, *, terrain_written, reconciled=None,
                 abutment_steps=None, segments=None,
                 half_width_m: float = DECK_HALF_WIDTH_M):
        ans = dict(real(record, terrain_written=terrain_written,
                        reconciled=reconciled,
                        abutment_steps=abutment_steps, segments=segments,
                        half_width_m=half_width_m))
        ans["asked"] = [n for n in ans["asked"] if n != "reconciliation"]
        ans["unasked"] = [n for n in ans["unasked"] if n != "reconciliation"]
        return ans

    globals()["deck_remedy"] = narrowed

    def undo():
        globals()["deck_remedy"] = real
    return undo


def _neuter_deck_remedy_never_asks_the_builder():
    """RESTORE THE SHIPPED DEFECT: the remedy is a deck, so a deck closes it.

    This is `accept/sections/roads.py`'s own branch verbatim -- a crossing
    whose premise is sound is ACTIONABLE because `assemble.bridge` exists.
    The predicate answers "nothing refuses this" for every record, which is
    what the gate believed for the whole life of the crossings arm.
    """
    real = globals()["deck_remedy"]

    def never_asks(record, *, terrain_written, reconciled=None,
                   abutment_steps=None, segments=None,
                   half_width_m: float = DECK_HALF_WIDTH_M):
        return dict(crossing_id=record.get("crossing_id"), buildable=True,
                    refused_by=[], asked=[], unasked=[], sub_classes=[],
                    implies=None,
                    depends_on="nothing: the remedy is a deck",
                    numbers={})

    globals()["deck_remedy"] = never_asks

    def undo():
        globals()["deck_remedy"] = real
    return undo


def _neuter_deck_remedy_refuses_everything():
    """THE OTHER DIRECTION'S NEUTER: a predicate that refuses every crossing.

    The survivor arm exists so that "the neuter flipped" cannot be satisfied
    by a predicate that simply says no to everything, and an arm asserting a
    record is NOT refused is not disarmed by
    `_neuter_deck_remedy_never_asks_the_builder` -- never asking cannot
    make a passing record fail. So this arm needs its own, and the defect it
    models is the degenerate over-correction: a gate that wires in a
    buildability question so blunt that it retires every crossing in the
    world as unservable, which reads as "nothing to build" and is the
    quietest possible failure.
    """
    real = globals()["deck_remedy"]

    def refuses_everything(record, *, terrain_written, reconciled=None,
                           abutment_steps=None, segments=None,
                           half_width_m: float = DECK_HALF_WIDTH_M):
        ans = dict(real(record, terrain_written=terrain_written,
                        reconciled=reconciled,
                        abutment_steps=abutment_steps, segments=segments,
                        half_width_m=half_width_m))
        ans["refused_by"] = list(ans["refused_by"]) + [dict(
            precondition="span_coverage", sub_class="over_correction",
            implies="1C_UNSERVED", performed_by=None,
            why="every crossing is refused")]
        ans["buildable"] = False
        ans["implies"] = "1C_UNSERVED"
        ans["sub_classes"] = sorted({r["sub_class"]
                                     for r in ans["refused_by"]})
        return ans

    globals()["deck_remedy"] = refuses_everything

    def undo():
        globals()["deck_remedy"] = real
    return undo


def declared_width_arms() -> list[dict]:
    """DECLARED_WIDTH_DEPENDS_ON on the shapes the Ulfsland regen's S1 and S2
    carried and the committed world's S2: `fill_from_the_contract` over a
    declared crossing and the `abutment_handoffs` of its banks' nodes."""
    def handoff(node, w):
        return {"node": node, "ribbon_width_m": w, "segment_id": "x"}

    def declared(a, b):
        return {"crossing_id": "X", "status": "declared",
                "bank_a": {"node": a, "road_deck_y": 30.6},
                "bank_b": {"node": b, "road_deck_y": 30.6}}
    cases = [
        ("S1: a 6.0 trunk and a 4.0 spur at one bank, 4.0 spurs at the other",
         [handoff("e", 6.0), handoff("e", 4.0), handoff("w", 4.0),
          handoff("w", 4.0)], 6.0),
        ("S2 of the committed world: 4.0 at one bank, 6.0 at the other",
         [handoff("e", 4.0), handoff("w", 6.0)], 6.0),
        ("S2 of the regen: a road at one bank only",
         [handoff("e", 4.0)], 4.0),
        ("no road at either bank (REQUIRED: not filled, refused by name)",
         [], None),
    ]
    out = []
    for name, handoffs, want in cases:
        c, _why = fill_from_the_contract(
            declared("e", "w"),
            {"contract": {"bank_level_run_m": 6.0, "deck_datum_y": 30.6},
             "abutment_handoffs": handoffs})
        got = c.get("ribbon_width_m")
        out.append(dict(name=name, got=got, want=want, ok=got == want))
    return out


def _neuter_declared_width():
    """Restore the shipped fill (both banks name ONE width); returns undo."""
    global _DECLARED_WIDTH_MODE
    was = _DECLARED_WIDTH_MODE
    _DECLARED_WIDTH_MODE = "agree"

    def undo():
        global _DECLARED_WIDTH_MODE
        _DECLARED_WIDTH_MODE = was
    return undo


def census_selftest(path: Path | None = None) -> int:
    """Both arms, on the network's OWN declarations plus the measured fixture.

    ARM 1 (must collapse): every pair the corridor census finds, named, with
    its fraction -- and the endpoint-equality alternative run over the SAME
    population, which MUST miss at least one of them.  If equality ever stops
    missing, corridor area is no longer load-bearing and this stops being a
    case.

    ARM 2 (must NOT collapse): S1 against S12-brgs1-boathouse-x01, the pair
    whose banks are 2.0 m apart and whose piece sets overlap 0.824 min-side.
    A census that collapses these has silently halved a real obligation, and
    that is a worse failure than missing a duplicate because nothing
    afterwards can see it.
    """
    spans, pop = spans_from_network(path)
    spans = project_onto_centreline(spans)
    # the known-bad state is CONSTRUCTED (DUPLICATE_FIXTURE): the world's own
    # declarations only add population, a world with no duplicate is not a
    # census that does not fire
    spans = spans + _duplicate_fixture_spans()
    kept, c = collapse_duplicate_declarations(spans)
    print(f"roads/boardwalk.py duplicate-declaration census, over "
          f"{pop['enumerated']} of {pop['declared']} network declaration(s) "
          f"plus {len(DUPLICATE_FIXTURE)} constructed duplicate(s) and "
          f"{len(CENSUS_FIXTURE)} measured fixture span(s)\n")
    print(f"  corridor area at half-width {DECK_HALF_WIDTH_M:.3f} m "
          f"(MEASURED off wood_floor), {CORRIDOR_SAMPLE_M:g} m sampling, "
          f"threshold {CORRIDOR_DUPLICATE_FRACTION}")
    print(f"  {c['declarations']} declaration(s) -> {c['distinct_channels']} "
          f"distinct channel(s); {c['declared_m']:.1f} m -> "
          f"{c['distinct_m']:.1f} m of distinct ground")
    bad = 0
    if not c["collapsed"]:
        print("  [FAIL] nothing collapsed: the census does not fire on "
              "today's declaration")
        bad += 1
    for p in c["pairs_over_threshold"]:
        print(f"    {p['fraction']:.3f}  {p['a']} == {p['b']}")
    missed = set(c["collapsed"]) - set(_endpoint_equality_collapse(spans))
    if not missed:
        print("  [FAIL] THE NEUTER DID NOT FLIP: endpoint equality collapses "
              "every pair corridor area does, so corridor area is not "
              "load-bearing here")
        bad += 1
    else:
        print(f"  [ok  ] endpoint equality MISSES {len(missed)} of "
              f"{len(c['collapsed'])} collapsed declaration(s): "
              f"{sorted(missed)}")
    # ARM 2, the pair the piece-overlap alternative gets wrong.
    pair = [_fixture_span("S1"), _fixture_span("S12-brgs1-boathouse-x01")]
    ov = corridor_overlap(*pair)
    _, c2 = collapse_duplicate_declarations(pair)
    if c2["collapsed"]:
        print(f"  [FAIL] S1 and S12-brgs1-boathouse-x01 COLLAPSED at "
              f"{ov['fraction']:.3f}: two distinct channels made one, and a "
              f"real obligation silently halved")
        bad += 1
    else:
        print(f"  [ok  ] S1 / S12-brgs1-boathouse-x01 stay DISTINCT at "
              f"corridor {ov['fraction']:.3f} despite banks 2.0 m apart and "
              f"0.824 min-side piece coverage -- the state a piece-overlap "
              f"census gets wrong")
    # THE BAND, printed rather than asserted: a threshold is only a
    # discriminator if the gap around it is empty.
    over = [p["fraction"] for p in c["pairs_over_threshold"]]
    under = []
    for i in range(len(spans)):
        for j in range(i + 1, len(spans)):
            f = corridor_overlap(spans[i], spans[j])["fraction"]
            if f < CORRIDOR_DUPLICATE_FRACTION:
                under.append(f)
    under.append(ov["fraction"])
    lo, hi = (min(over) if over else 0.0), (max(under) if under else 0.0)
    print(f"  band: nearest duplicate {lo:.3f}, nearest non-duplicate "
          f"{hi:.3f}, over {len(over)} + {len(under)} pair(s) -- the "
          f"threshold {CORRIDOR_DUPLICATE_FRACTION} sits inside it")
    if hi >= CORRIDOR_DUPLICATE_FRACTION or (over and lo <= hi):
        print("  [FAIL] the band is not empty: the threshold is a tolerance, "
              "not a discriminator")
        bad += 1
    # ARM 3: THE TILE FRAME.  Constructed, not borrowed -- one square, one
    # yaw, two points, no span and no save.
    for arm in tile_frame_arms():
        if arm["ok"]:
            print(f"  [ok  ] tile_frame {arm['name']}: local "
                  f"{arm['local']} reads back {arm['read_back']}, "
                  f"carried={arm['got']}")
        else:
            print(f"  [FAIL] tile_frame {arm['name']}: local "
                  f"{arm['local']} reads back {arm['read_back']}, "
                  f"carried={arm['got']}, required {arm['want']}")
            bad += 1
    undo = _neuter_tile_frame()
    try:
        neutered = tile_frame_arms()
    finally:
        undo()
    flipped = [a["name"] for a, b in zip(tile_frame_arms(), neutered)
               if a["got"] != b["got"]]
    if flipped:
        print(f"  [ok  ] THE NEUTER FLIPS: restoring the forward rotation "
              f"changes the verdict on {flipped} -- interior "
              f"{neutered[0]['local']} reads back {neutered[0]['read_back']} "
              f"and is refused")
    else:
        print("  [FAIL] THE NEUTER DID NOT FLIP: the forward rotation answers "
              "exactly as the inverse does, so tile_frame is not "
              "load-bearing and this is not a case")
        bad += 1
    # ARM 4: THE SPAN POPULATION'S DECLARED DEPENDENCY, both directions.
    real = span_population_arms(path)
    for arm in real:
        # POPULATION AND EVIDENCE, ALWAYS SEPARATELY.  The population is how
        # many crossings exist; the EVIDENCE is how many rows this arm can
        # actually be wrong about, and only the second decides anything.
        if arm["ok"]:
            print(f"  [ok  ] span population {arm['name']}: all "
                  f"{arm['evidence']} of them enumerated "
                  f"({arm['enumerated']} of {arm['population']} declarations "
                  f"reachable) -- evidence {arm['evidence']}, population "
                  f"{arm['population']}, first row {arm['crossing_id']}")
        else:
            print(f"  [FAIL] span population {arm['name']}: "
                  f"{len(arm['missing'])} of {arm['evidence']} NOT "
                  f"enumerated -- {arm['missing']} -- so the builder cannot "
                  f"be pointed at an obligation the gate judges")
            bad += 1
    undo = _neuter_span_population_segments_only()
    try:
        neutered = span_population_arms(path)
    finally:
        undo()
    flipped = [b["missing"] for a, b in zip(real, neutered)
               if a["got"] != b["got"]]
    survived = [b["name"] for b in neutered if b["got"]]
    if flipped and survived:
        print(f"  [ok  ] THE NEUTER FLIPS AND DOES NOT MERELY EMPTY: keying "
              f"the population on the SEGMENTS loses {flipped} "
              f"({real[0]['enumerated']} enumerated -> "
              f"{neutered[0]['enumerated']} of {real[0]['population']}) "
              f"while {survived} survives on all "
              f"{neutered[1]['evidence']} of its rows -- under-keying is "
              f"silent and FALSE, over-keying is silent and EXPENSIVE, and "
              f"only checking both arms tells them apart")
    elif not flipped:
        print("  [FAIL] THE NEUTER DID NOT FLIP: a segments-keyed population "
              "reaches the joiner too, so crossings.yaml is not load-bearing "
              "here and this is not a case")
        bad += 1
    else:
        print("  [FAIL] THE NEUTER EMPTIED THE POPULATION: every arm fell, so "
              "the flip proves nothing about the key")
        bad += 1
    # ARM 5: THE ABUTMENT REACH, both named dependencies, both directions.
    real = deck_remedy_reach_arms(path)
    for arm in real:
        if arm["ok"]:
            print(f"  [ok  ] reach {arm['name']}: "
                  f"{arm.get('crossing_id')} regradeable "
                  f"{arm.get('regradeable_m')} m of "
                  f"{arm.get('outboard_m')} m outboard -> "
                  f"{arm.get('got_class')}")
        else:
            print(f"  [FAIL] reach {arm['name']}: {arm.get('crossing_id')} "
                  f"got {arm.get('got_class')}/{arm.get('got_sub')} "
                  f"against required {arm.get('want_class')}/"
                  f"{arm.get('want_sub')}; regradeable "
                  f"{arm.get('regradeable_m')} of {arm.get('outboard_m')} m "
                  f"outboard -- {arm.get('why', '')}")
            bad += 1
    for label, neuter, loses in (
            ("the DIRECTION", _neuter_reach_ignores_the_direction,
             "T5's 677.69 m of INBOARD carriageway is credited to its "
             "terminus bank"),
            ("`is_bridge`", _neuter_reach_ignores_is_bridge,
             "the fixture's four bridged outboard stations read as 10 m "
             "of bench"),
            # THE THIRD DIRECTION, AND THE ONLY ONE THE LONG ARM HAS.  Both
            # neuters above can only make a reach BIGGER, so a bank with
            # 77.54 m of road outboard answers identically under each of
            # them -- a registered case whose own discriminator cannot make
            # it red.  Found by T9Close reading the live selftest, not by
            # the author reading his own arm.
            ("the REACH ITSELF", _neuter_reach_answers_zero,
             "every bank answers 0 m outboard, so 1E factory work is "
             "retired as ground nothing in this tree owns")):
        undo = neuter()
        try:
            neutered = deck_remedy_reach_arms(path)
        finally:
            undo()
        flipped = [b["name"] for a, b in zip(real, neutered)
                   if a["ok"] != b["ok"]]
        survived = [b["name"] for b in neutered if b["ok"]]
        if flipped and survived:
            print(f"  [ok  ] THE NEUTER FLIPS AND DOES NOT MERELY EMPTY: "
                  f"losing {label} flips {flipped} ({loses}) while "
                  f"{len(survived)} of {len(neutered)} arm(s) survive -- "
                  f"{survived}")
        elif not flipped:
            print(f"  [FAIL] THE NEUTER DID NOT FLIP: dropping {label} "
                  f"changes no arm, so it is not load-bearing and this is "
                  f"not a case")
            bad += 1
        else:
            print(f"  [FAIL] THE NEUTER EMPTIED THE POPULATION: every arm "
                  f"fell when {label} was dropped, so the flip proves "
                  f"nothing about it")
            bad += 1
    # ARM 6: THE BURIAL GUARD, constructed, both named dependencies.
    real = burial_guard_arms()
    for arm in real:
        if arm["ok"]:
            print(f"  [ok  ] burial {arm['name']}: tile top "
                  f"{arm['tile_top_m']} m, pivot ground "
                  f"{arm['pivot_ground_m']} m, footprint minimum "
                  f"{arm['footprint_min_ground_m']} m -> "
                  f"refused={arm['got_refused']}")
        else:
            print(f"  [FAIL] burial {arm['name']}: refused="
                  f"{arm['got_refused']} against required "
                  f"{arm['want_refused']}; tile top {arm['tile_top_m']} m, "
                  f"pivot {arm['pivot_ground_m']} m, footprint minimum "
                  f"{arm['footprint_min_ground_m']} m, limit "
                  f"{arm['limit_m']} m")
            bad += 1
    for label, neuter, loses in (
            ("the GUARD ITSELF", _neuter_burial_guard_absent,
             "the state that shipped: five guards pass and 35 pieces go out "
             "with 2 of them under the ground"),
            ("the FOOTPRINT SAMPLE", _neuter_burial_samples_the_pivot,
             "a tile whose centre stands under a mound and whose corner is "
             "open air is retired as buried"),
            ("the GATE'S LIMIT", _neuter_burial_uses_the_overhang_limit,
             f"the builder passes at {BURIAL_MAX_M:g} m what the gate "
             f"refuses at {BURIED_MAX_M:g} m"),
            ("the GATE'S REDUCTION", _neuter_burial_reads_its_own_samples,
             "the builder refuses a pole the gate accepts -- "
             "T3-temple-meadhall-x01's deck, on laid road, refused at 0.083 m "
             "on a pole whose top sits exactly on the vertex under it")):
        undo = neuter()
        try:
            neutered = burial_guard_arms()
        finally:
            undo()
        flipped = [b["name"] for a, b in zip(real, neutered)
                   if a["ok"] != b["ok"]]
        survived = [b["name"] for b in neutered if b["ok"]]
        if flipped and survived:
            print(f"  [ok  ] THE NEUTER FLIPS AND DOES NOT MERELY EMPTY: "
                  f"losing {label} flips {flipped} ({loses}) while "
                  f"{len(survived)} of {len(neutered)} arm(s) survive")
        elif not flipped:
            print(f"  [FAIL] THE NEUTER DID NOT FLIP: dropping {label} "
                  f"changes no arm, so it is not load-bearing")
            bad += 1
        else:
            print(f"  [FAIL] THE NEUTER EMPTIED THE POPULATION: every arm "
                  f"fell when {label} was dropped")
            bad += 1
    # ARMS 7 AND 8: THE DECK'S RAILS OFF ITS CARRIAGEWAY, and THE OVERHANG
    # TRIM ASKING THE BURIAL GUARD'S QUESTION.  Constructed, both ways.
    for arms, what, neuters in (
            (carriageway_guard_arms, "carriageway", (
                ("the BENCH WIDTH", _neuter_deck_as_wide_as_the_carriageway,
                 "the shipped deck, exactly as wide as the carriageway, "
                 "stands its rail posts 0.2 m inside it -- drive 3: 36 of 36 "
                 "on T1-x01, 32 of 32 on S4-x01"),
                ("the GUARD ITSELF", _neuter_carriageway_guard_absent,
                 "the state that shipped: nothing asked the gate's "
                 "obstruction question before the build"))),
            (cross_section_arms, "cross-section", (
                ("the CROSS-SECTION SPACING", _neuter_cross_sections_at_stations,
                 "the state that shipped: a 0.4 m post between two of the "
                 "gate's 1 m cross-sections is invisible -- T3-x01's rail post "
                 "at (200.65,-37.84) stood 0.72 m off S11's centreline, "
                 "0.1 mm clear of station 1105, and went out twice"),
                ("the WRITTEN HEIGHT", _neuter_crossing_judged_on_its_bed,
                 "an undecked crossing is read against its 29 m bed and "
                 "T3's under-deck pile is a 3 m post in S11's road"))),
            (overhang_trim_arms, "overhang trim", (
                ("the GUARD'S READING", _neuter_overhang_trim_keeps_its_own_slack,
                 "the trim keeps a tile the gate calls buried at 0.065 m and "
                 "the whole deck is refused -- S13-stathub-dock-x01 on "
                 "drive 3"),)),
            (tile_seam_arms, "tile seam", (
                ("the SEAM TOLERANCE", _neuter_tile_covers_to_the_exact_half,
                 "a 4-row deck's centreline on the seam of two abutting "
                 "tiles reads as an uncovered metre -- T13-x01 'covers 7 of "
                 "8' on the drive-3 re-emit"),)),
            (terminal_landing_arms, "terminal landing", (
                ("the DECK'S OWN LANDING", _neuter_terminal_landing,
                 "a terminal bank is refused with the terminus as owner and "
                 "nothing lands it -- T9-x03 and T13-x01 of the Ulfsland "
                 "regen, 136.6 m of deck with T2, never built"),
                ("the END BENT", _neuter_terminal_end_bent,
                 "the end row of a terminal bank carries no bent of its "
                 "own: support 17.90 and 19.76 against 20.00"))),
            (terminal_descent_arms, "terminal descent", (
                ("the DESCENT INSIDE THE SPAN", _neuter_terminal_descent,
                 "a landing alone from the datum: T5-x01's deck at 34.094 m "
                 "over a shore at 29.7 m is 4.4 m, past 3.05 m, and is "
                 "refused"),)),
            (deck_overlap_arms, "deck overlap", (
                ("the OVERLAP GUARD", _neuter_deck_overlap_off,
                 "a deck is emitted through one that stands -- T5-x01's "
                 "landing and T9-x03's, one dock"),)),
            (terminal_remedy_arms, "terminal remedy", (
                ("the DECK'S OWN LANDING", _neuter_terminal_landing,
                 "the builder files a bank its deck would land as "
                 "1C_UNSERVED, ground nothing in the tree owns"),))):
        real = arms()
        for arm in real:
            print(f"  [{'ok  ' if arm['ok'] else 'FAIL'}] {what} "
                  f"{arm['name']}: refused={arm['got_refused']} (required "
                  f"{arm['want_refused']}) -- "
                  + ", ".join(f"{k} {v}" for k, v in arm.items()
                              if k not in ("name", "ok", "want_refused",
                                           "got_refused", "why"))
                  + ("" if arm["ok"] else f"; {arm['why']}"))
            bad += not arm["ok"]
        for label, neuter, loses in neuters:
            undo = neuter()
            try:
                neutered = arms()
            finally:
                undo()
            flipped = [b["name"] for a, b in zip(real, neutered)
                       if a["ok"] != b["ok"]]
            survived = [b["name"] for b in neutered if b["ok"]]
            if flipped and survived:
                print(f"  [ok  ] THE NEUTER FLIPS AND DOES NOT MERELY EMPTY: "
                      f"losing {label} flips {flipped} ({loses}) while "
                      f"{len(survived)} of {len(neutered)} arm(s) survive")
            elif not flipped:
                print(f"  [FAIL] THE NEUTER DID NOT FLIP: dropping {label} "
                      f"changes no {what} arm, so it is not load-bearing")
                bad += 1
            else:
                print(f"  [FAIL] THE NEUTER EMPTIED THE POPULATION: every "
                      f"{what} arm fell when {label} was dropped")
                bad += 1
    for arm in route_carriageway_arms():
        print(f"  [{'ok  ' if arm['ok'] else 'FAIL'}] network carriageway "
              f"{arm['name']}: {arm}")
        bad += not arm["ok"]
    for arm in landfall_seating_arms():
        print(f"  [{'ok  ' if arm['ok'] else 'FAIL'}] land seating "
              f"{arm['name']}: {arm}")
        bad += not arm["ok"]
    undo_land = _neuter_land_seating()
    try:
        old_land = landfall_seating_arms()
    finally:
        undo_land()
    flipped_land = [r["name"] for r in old_land if not r["ok"]]
    global _WET_TEST_MODE
    _prior_wet, _WET_TEST_MODE = _WET_TEST_MODE, "footprint"
    try:
        wet_old = {r["name"]: r["ok"] for r in landfall_seating_arms()}
    finally:
        _WET_TEST_MODE = _prior_wet
    print(f"  [{'ok  ' if not wet_old['margin_tile_clipping_waterline_is_not_wet'] else 'FAIL'}] "
          f"wet-lane neuter: the footprint test refuses the margin-clip bank")
    bad += bool(wet_old["margin_tile_clipping_waterline_is_not_wet"])
    survived_land = [r["name"] for r in old_land if r["ok"]]
    print(f"  land seating neuter: flipped {flipped_land}; "
          f"survived {survived_land}")
    bad += not (flipped_land and survived_land)
    for label, neuter in (
            ("shared floor reuse", _neuter_shared_deck_reuse),
            ("shared datum solve", _neuter_shared_deck_datum)):
        undo = neuter()
        try:
            rows = shared_deck_arms()
        finally:
            undo()
        flipped = [r["name"] for r in rows if not r["ok"]]
        survived = [r["name"] for r in rows if r["ok"]]
        ok = bool(flipped and survived)
        print(f"  [{'ok  ' if ok else 'FAIL'}] {label} neuter: "
              f"flipped {flipped}; survived {survived}")
        bad += not ok
    # ARM: THE DECLARED DECK'S WIDTH (DECLARED_WIDTH_DEPENDS_ON), both ways.
    rows = declared_width_arms()
    for arm in rows:
        print(f"  [{'ok  ' if arm['ok'] else 'FAIL'}] declared deck width "
              f"{arm['name']}: got {arm['got']}, required {arm['want']}")
        bad += not arm["ok"]
    undo = _neuter_declared_width()
    try:
        neutered = declared_width_arms()
    finally:
        undo()
    flipped = [r["name"] for r in neutered if not r["ok"]]
    survived = [r["name"] for r in neutered if r["ok"]]
    ok = bool(flipped and survived)
    print(f"  [{'ok  ' if ok else 'FAIL'}] declared deck width neuter "
          f"(both banks must name ONE width): flipped {flipped}; survived "
          f"{survived}")
    bad += not ok
    print(f"\n  {'ALL ARMS ANSWER AS REQUIRED' if not bad else f'{bad} arm(s) did not answer as required'}")
    return 1 if bad else 0

# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------

# Bent spacings tried, widest first.  8 m is the measured wood figure from
# `crossings/README.md` ("wood bent spacing 8 m, mid-span support ~25 against
# a min of 10"), and ~25 against a 2x safety floor of 20 is a 25% margin, not
# a comfortable one.  When a span's own geometry lands under it the answer is
# ANOTHER BENT, not a lower safety factor: a bent is four poles and a cap.
BENT_SPACINGS_M = (8.0, 6.0, 4.0)

# THE DECK IS AS WIDE AS THE BENCH IT CONTINUES -- DECK_WIDTH_DEPENDS_ON.
#
# `assemble.bridge` insets its rail line 0.2 m from the deck's own edge, so a
# deck exactly as wide as the carriageway stands its rail posts ON the
# carriageway.  MEASURED on the drive-3 save (ledger wire, against each
# segment's own centreline): every wood_pole on all three decks built is a
# rail post 0.20 m inside the carriageway edge.  T1-portalhub-temple-x01 has
# 36 of 36 at |lateral| 2.80 m against a 3.0 m half-width, T3-temple-meadhall-x01
# 24 of 24 at 2.80 m, and S4-meadhall-southeast-x01 32 of 32 at 1.80 m against
# 2.0 m.  None of them is a pile (the piles are wood_pole_log_4, under the
# deck).  The gate refused T1-x01 and S4-x01 for exactly those posts:
# `roads/obstruction` "36 of 134 pieces of one body stand in the carriageway
# (go round); wood_pole" and "32 of 101", both 1D_OBSTRUCTED.
#
# The road the deck continues is not the carriageway alone.  The ribbon
# LEVELS `route.footprint_half_m(width)` either side of the centreline, which
# is the carriageway plus a 1 m shoulder each side (8 m on a 6 m trunk, 6 m on
# a 4 m spur).  A deck that wide carries the whole bench, and its rails stand
# on the shoulder line, outside the paved width, as every lamp does
# (3.44-3.55 m lateral).
DECK_WIDTH_DEPENDS_ON = (
    "the carriageway the deck carries (the crossing's own `ribbon_width_m`)",
    "the shoulder the ribbon levels either side of it "
    "(route.footprint_half_m, the one place it is computed)",
    "the rail line assemble.bridge insets 0.2 m from the deck's edge")
# Swapped out by the selftest neuter: "bench" | "carriageway" (shipped: the
# deck is exactly as wide as the carriageway, and so are its rails).
_DECK_WIDTH_OF = "bench"


def deck_tiles(span: dict) -> int:
    """2 m tiles across the deck (DECK_WIDTH_DEPENDS_ON)."""
    carriage = float(span["deck_width_m"])
    width = (carriage if _DECK_WIDTH_OF == "carriageway"
             else 2.0 * routemod.footprint_half_m(carriage))
    return int(math.ceil(width / 2.0 - 1e-9))


LAND_TILE_STEP_M = 0.30
# Swapped out by the selftest neuter: "carriageway" (shipped) | "footprint"
# (the state that refused S1: any clipped margin tile makes the station wet).
_WET_TEST_MODE = "carriageway"
LAND_RAMP_MAX_TILES = 6
LAND_RAMP_PITCH_M = 1.0

# TERMINAL_LANDING_DEPENDS_ON: A BANK THAT IS ITS SEGMENT'S OWN END IS LANDED BY
# THE DECK, BECAUSE NOTHING ELSE CAN.
#
# A crossing whose free run reaches the last station of its road records that
# bank as `terminal_banks` (network.CROSSING_BANKS_DEPENDS_ON): the ribbon
# writes the non-bridged stations only, so no emission of the segment ever
# benches the ground beyond it.  `abutment_guard` refused such a bank and
# named "the terminus" as the owner, and the terminus never took it: the
# terminal trim is a candidate the writer's water score may decline
# (network.TERMINAL_CROSSING_TRIM_DEPENDS_ON), and the Ulfsland regen's
# three BUILT legs T9-wttown-dockshore, T13-temple-harboursouth and
# T2-temple-brgs1 were all declined it -- three decks, 136.6 m, never
# built, the gate's `crossings` finding.  An owner that does not act is not
# an owner.
#
# So the deck lands itself: from the bank row it keeps descending, one row
# every LAND_RAMP_PITCH_M and at most LAND_TILE_STEP_M a row (the raised
# landfall's own limits, mirrored), until the walking surface is within the
# bank-step limit of the APPLIED ground at its end -- never lower than the
# ground it hugs, and every row carries a bent at its own height
# (assemble.bridge).  The budget is not a number of this module's choosing:
# a deck over water stands route.DECK_CLEARANCE_M above the plane, and a
# terminal bank is where the road ends ON A SHORE, so what a landing has to
# absorb is that clearance.  A bank that needs more descent than that is not a
# shore, it is a SLOPE the road's own profile never reached, and it is
# REFUSED with the numbers: no deck follows a hillside at <= 0.30 m a row
# down a cliff, and `flatten: FORBIDDEN` forbids cutting one for it.
# NON-terminal banks are untouched: their bench is the road's, and a deck
# that landed itself there would hide the road's refusal.
# Swapped out by the selftest neuter: "lands" (shipped) | "refuses" (the state
# that built nothing: a terminal bank is refused with the terminus as owner).
LAND_DESCENT_MAX_TILES = int(math.ceil(
    routemod.DECK_CLEARANCE_M / LAND_TILE_STEP_M - 1e-9))
TERMINAL_LANDING_DEPENDS_ON = (
    "the crossing's own `terminal_banks` (network.CROSSING_BANKS_DEPENDS_ON)",
    "the APPLIED ground beyond that bank, at the deck's own rows",
    "LAND_TILE_STEP_M, the raised landfall's per-row limit, mirrored",
    "route.DECK_CLEARANCE_M, the height a deck over water stands above it")
_TERMINAL_LANDING_MODE = "lands"
# The same bank's other half: with no bench beyond it the END ROW stands on its
# own bent (assemble.bridge `end_bents`), because "the abutment end is carried
# by the bank" is only true where the bank touches the tile.  MEASURED on
# T9-wttown-dockshore-x03 and T13-temple-harboursouth-x01: the end tiles read
# support 17.90 and 19.76 against the 20.00 the safety factor needs, the only
# structural refusals either deck had besides the step.  Swapped out by the
# selftest neuter: "own" (shipped) | "none" (the state that refused them).
_TERMINAL_END_BENT_MODE = "own"
# The ground a landing can meet below a deck: LAND_DESCENT_MAX_TILES rows at
# LAND_TILE_STEP_M each, then the bank step a walker takes off the last row.
TERMINAL_LANDING_REACH_M = (LAND_DESCENT_MAX_TILES * LAND_TILE_STEP_M
                            + ABUTMENT_STEP_MAX_M)
# THE DECK COMES DOWN INSIDE THE SPAN TOO.  A road can arrive above the
# clearance a deck over water keeps (T5-temple-dockshore-x01: 34.094 m against
# 32.5 m, over a shore at 29.7 m -- 4.4 m, past a landing's 3.05 m), and the
# span is free to give that height back: from the bank row it keeps descending
# at the landing's own step, but never under the ground it stands over and
# never, over water, under the clearance.  The search takes the SHORTEST
# descent inside the span that a landing can finish (none at all where the
# landing alone does, which is every deck laid before this was written).
# Swapped out by the selftest neuter: "span" (shipped) | "landing" (the state
# that refused T5: a landing alone, from the datum).
_TERMINAL_DESCENT_MODE = "span"


def terminal_landing_reach_m(record: dict) -> float:
    """The ground a terminal bank's deck can land on below it, in metres: a
    landing's reach, plus what the span itself can give back above the
    clearance floor (`required_deck_y` - clearance, at most a tile step per
    metre of the half of the span a descent may use)."""
    reach = TERMINAL_LANDING_REACH_M
    if _TERMINAL_DESCENT_MODE != "span":
        return reach
    try:
        H = float(record["required_deck_y"])
        L = float(record["span_m"])
    except (KeyError, TypeError, ValueError):
        return reach
    clear = float(record.get("water_surface_y") or 30.0) + \
        routemod.DECK_CLEARANCE_M
    return reach + min(max(H - clear, 0.0),
                       int(L / 2.0) * LAND_TILE_STEP_M)


def land_seated_deck_stations(span: dict, surface, deck_y: float,
                              report: dict | None = None) -> list[tuple[float, float]]:
    """Seat dry tiles and bounded bank ramps; never raise a wet tile's ceiling.

    The datum is still the water span's datum. A naturally dry island or bank
    is land only when EVERY tile footprint in the station is above the water
    plane. Height changes propagate to neighbours at <=0.30m per tile and at
    the gate's unchanged grade limit. The assembler supplies each raised row
    with supports at that row's actual height.
    """
    ax, az = span["bank_a"]["xz"]
    bx, bz = span["bank_b"]["xz"]
    length = math.hypot(bx - ax, bz - az)
    ux, uz = (bx - ax) / length, (bz - az) / length
    yaw = bearing_of(span)
    width = deck_tiles(span)
    offsets = [(i - (width - 1) / 2.0) * 2.0 for i in range(width)]
    half_way = float(span["deck_width_m"]) / 2.0
    floor_top = asm.facts()["wood_floor"].hi[1]
    water_y = float(span.get("water_surface_y", 30.0))
    ceiling = float(span.get("legal_deck_y_interval", [deck_y, deck_y])[1])
    terrain = {}
    carriage_low = {}

    def ground(t):
        if t not in terrain:
            lows = []
            way = []
            for off in offsets:
                p = asm.Placed("wood_floor", ax + ux * t + uz * off,
                               deck_y - floor_top, az + uz * t - ux * off,
                               yaw, "profile/deck")
                low = asm.bed_under(p, surface)
                lows.append(low)
                if abs(off) + 1.0 <= half_way + 1e-9:
                    way.append(low)
            terrain[t] = (min(lows), max(way or lows))
            # WET MEANS THE WALKED LANES ARE WET.  A margin tile outside the
            # carriageway that clips the waterline at a diagonal shore does
            # not make the station water: MEASURED on Pirate68 S1 bank_a,
            # station -1 m, lanes [-2, 0, +2] m: 29.75 / 30.94 / 29.95 m
            # against a 30.0 m water plane -- the carriageway tile is dry by
            # 0.94 m and the two margin tiles each clip it, and the whole
            # span was refused for it (and so was every road to the west).
            carriage_low[t] = min(way or lows)
        return terrain[t]

    def wet(t):
        ground(t)
        return (carriage_low[t] if _WET_TEST_MODE == "carriageway"
                else terrain[t][0]) < water_y

    def solve(wanted):
        ts = sorted(wanted)
        hs = [wanted[t] for t in ts]
        for i in range(len(ts) - 2, -1, -1):
            limit = min(LAND_TILE_STEP_M,
                        (ts[i + 1] - ts[i]) * road_instruments.STEP_LIMIT_PER_M)
            hs[i] = max(hs[i], hs[i + 1] - limit)
        for i in range(1, len(ts)):
            limit = min(LAND_TILE_STEP_M,
                        (ts[i] - ts[i - 1]) * road_instruments.STEP_LIMIT_PER_M)
            hs[i] = max(hs[i], hs[i - 1] - limit)
        for t, h in zip(ts, hs):
            low, _way = ground(t)
            if h > ceiling + 1e-6 and wet(t):
                raise SystemExit(
                    f"{span['crossing_id']}: land seating needs {h:.3f} m at "
                    f"station {t:.3f} m, but its footprint reaches wet ground "
                    f"{low:.3f} m; wet ceiling {ceiling:.3f} m cannot be raised.")
        return dict(zip(ts, hs))

    wanted = {}
    for t in asm._bridge_station_distances(length, 1):
        _low, way = ground(t)
        wanted[t] = (max(deck_y, way) if 0.0 <= t <= length
                     and way > deck_y + BURIED_MAX_M else deck_y)
    heights = solve(wanted)
    ramps = []
    for end, anchor, sign in (("bank_a", 0.0, -1.0),
                              ("bank_b", length, 1.0)):
        edge_t = anchor + sign
        edge_ground = surface(ax + ux * edge_t, az + uz * edge_t)
        rise = edge_ground - heights[anchor]
        if rise <= ABUTMENT_STEP_MAX_M:
            continue  # Low banks remain the existing guard's obligation.
        needed = math.ceil((rise - ABUTMENT_STEP_MAX_M) / LAND_TILE_STEP_M)
        if needed > LAND_RAMP_MAX_TILES:
            raise SystemExit(
                f"{span['crossing_id']} {end}: landfall rise {rise:.3f} m "
                f"needs at least {needed} land tiles at <={LAND_TILE_STEP_M:.2f} m "
                f"per tile; budget is {LAND_RAMP_MAX_TILES}. REFUSED, no datum "
                f"or bank-step limit was relaxed.")
        trial = {t: h for t, h in wanted.items()
                 if (t >= anchor if sign < 0.0 else t <= anchor)}
        solved = None
        for k in range(1, LAND_RAMP_MAX_TILES + 1):
            t = anchor + sign * k * LAND_RAMP_PITCH_M
            low, way = ground(t)
            if wet(t):
                raise SystemExit(
                    f"{span['crossing_id']} {end}: proposed land ramp station "
                    f"{t:.3f} m reaches wet ground {low:.3f} m; REFUSED.")
            exit_t = t + sign
            g_exit = surface(ax + ux * exit_t, az + uz * exit_t)
            trial[t] = max(deck_y, way, surface(ax + ux * t, az + uz * t),
                           g_exit - ABUTMENT_STEP_MAX_M)
            candidate = solve(trial)
            ordered = sorted(candidate, reverse=sign > 0)
            raised_count = 0
            for at in ordered:
                if candidate[at] <= deck_y + 1e-6:
                    break
                raised_count += 1
            if raised_count > LAND_RAMP_MAX_TILES:
                raise SystemExit(
                    f"{span['crossing_id']} {end}: landfall needs "
                    f"{raised_count} consecutively raised land tiles at "
                    f"<={LAND_TILE_STEP_M:.2f} m per step; budget is "
                    f"{LAND_RAMP_MAX_TILES}. REFUSED.")
            if abs(candidate[t] - g_exit) <= ABUTMENT_STEP_MAX_M + 1e-9:
                solved = candidate
                break
        if solved is None:
            raise SystemExit(
                f"{span['crossing_id']} {end}: no landfall within "
                f"{LAND_RAMP_MAX_TILES} land tiles meets the unchanged "
                f"{ABUTMENT_STEP_MAX_M:.2f} m bank-step limit; REFUSED.")
        wanted = trial
        heights = solved
        ramps.append(dict(bank=end, outboard_tiles=k, raised_tiles=raised_count,
                          initial_step_m=round(-rise, 6),
                          final_step_m=round(heights[t] - g_exit, 6)))
    # THE TERMINAL LANDING (TERMINAL_LANDING_DEPENDS_ON): a bank that is its
    # segment's own end has no bench beyond it, so a deck standing above the
    # ground there lands ITSELF -- the raised landfall above, mirrored.
    for end, anchor, sign in (("bank_a", 0.0, -1.0), ("bank_b", length, 1.0)):
        if (_TERMINAL_LANDING_MODE != "lands"
                or end not in (span.get("terminal_banks") or ())):
            continue

        def at(t):
            return surface(ax + ux * t, az + uz * t)

        top = heights[anchor]
        # the end the bridge lays by default: the overhang row, 2 m out, whose
        # tile ends 1 m further.  A bank the deck already lands on keeps it.
        if top - at(anchor + sign * 3.0) <= ABUTMENT_STEP_MAX_M:
            continue
        clear = water_y + routemod.DECK_CLEARANCE_M

        def floor_at(t, j):
            """The lowest walking surface row `j` (metres inboard of the bank
            row, negative beyond it) may take: never under the ground it
            stands over, and -- inside the span, over water -- never under
            the clearance a deck over water keeps."""
            low, way = ground(t)
            return max(way, clear) if (j >= 0 and low < water_y) else way

        solved, tried_best = None, None
        m_max = int(length / 2.0) if _TERMINAL_DESCENT_MODE == "span" else 0
        for m in range(0, m_max + 1):
            # rows of the span inside the descent's reach are laid again at the
            # landing's own 1 m pitch, so no row drops more than a tile step;
            # a row of the old lattice within the gate's duplicate cube of the
            # first new one IS that row (T5-x01: 14.0 against 14.0255)
            trial = {t: h for t, h in wanted.items()
                     if (anchor - t) * sign > m + 1e-9}
            for j in range(m, 0, -1):
                t = anchor - sign * j * LAND_RAMP_PITCH_M
                if j == m and any(abs(t - kt) < asm.min_bay_m()
                                  for kt in trial):
                    continue    # the old row IS the descent's first row
                trial[t] = max(top - (m - j) * LAND_TILE_STEP_M,
                               floor_at(t, j))
            trial[anchor] = max(top - m * LAND_TILE_STEP_M,
                                floor_at(anchor, 0))
            for k in range(1, LAND_DESCENT_MAX_TILES + 1):
                t = anchor + sign * k * LAND_RAMP_PITCH_M
                trial[t] = max(top - (m + k) * LAND_TILE_STEP_M,
                               floor_at(t, -k))
                candidate = solve(trial)
                g_exit = at(t + sign)
                gap = candidate[t] - g_exit
                if tried_best is None or abs(gap) < abs(tried_best[0]):
                    tried_best = (gap, m, k, candidate[t], g_exit)
                if abs(gap) <= ABUTMENT_STEP_MAX_M + 1e-9:
                    solved = candidate
                    break
            if solved is not None:
                break
        if solved is None:
            gap, bm, bk, h_end, g_end = tried_best
            raise SystemExit(
                f"{span['crossing_id']} {end}: terminal landing: this bank is "
                f"its segment's own end (`terminal_banks`), so no road bench "
                f"lies beyond it and the deck must land itself -- but the "
                f"deck stands {top:.3f} m and no descent meets the ground: "
                f"at <={LAND_TILE_STEP_M:.2f} m a row the deck comes down "
                f"{m_max} rows inside the span (never under the "
                f"{clear:.3f} m a deck over water keeps, "
                f"route.DECK_CLEARANCE_M {routemod.DECK_CLEARANCE_M:g} m) and "
                f"{LAND_DESCENT_MAX_TILES} rows beyond the bank, and the "
                f"nearest it gets is {h_end:.3f} m over applied ground "
                f"{g_end:.3f} m ({gap:+.3f} m, limit "
                f"{ABUTMENT_STEP_MAX_M} m; {bm} rows in the span + {bk} "
                f"beyond). That is a slope or open water, not a shore: its "
                f"road's own profile never reached the bank. REFUSED, no "
                f"datum or bank-step limit was relaxed.")
        wanted = trial
        heights = solved
        ramps.append(dict(bank=end, direction="down", outboard_tiles=k,
                          span_tiles=m,
                          lowered_tiles=sum(1 for tt, hh in heights.items()
                                            if (tt - anchor) * sign > 1e-9
                                            and hh < top - 1e-6),
                          lowered_in_span_tiles=sum(
                              1 for tt, hh in heights.items()
                              if -1e-9 <= (anchor - tt) * sign
                              and hh < top - 1e-6),
                          initial_step_m=round(top - at(anchor + sign * 3.0), 6),
                          final_step_m=round(heights[t] - g_exit, 6)))
    if report is not None:
        report["landfall_ramps"] = ramps
        report["land_stations_seated"] = [
            dict(station_m=round(t, 6),
                 xz=[round(ax + ux * t, 6), round(az + uz * t, 6)],
                 top_y=round(h, 6), nominal_y=deck_y,
                 footprint_min_y=round(ground(t)[0], 6),
                 land=ground(t)[0] >= water_y,
                 beyond_bank=t < 0.0 or t > length)
            for t, h in sorted(heights.items()) if abs(h - deck_y) > 1e-6]
        pairs = list(zip(sorted(heights), sorted(heights)[1:]))
        report["land_seating_limits"] = dict(
            tile_step_max_m=LAND_TILE_STEP_M, ramp_tile_budget=LAND_RAMP_MAX_TILES,
            grade_max=road_instruments.STEP_LIMIT_PER_M,
            measured_tile_step_max_m=max(
                (abs(heights[b] - heights[a]) for a, b in pairs), default=0.0),
            measured_grade_max=max(
                (abs(heights[b] - heights[a]) / (b - a) for a, b in pairs), default=0.0))
    return sorted(heights.items())


def build_pieces(span: dict, surface, deck_y: float | None = None,
                 bent_spacing_m: float = 8.0,
                 report: dict | None = None, *, routes: tuple = (),
                 existing: tuple = ()) -> list:
    """`assemble.bridge` at the span's OWN solved H.

    `width_tiles` comes from the span's own `deck_width_m` through
    `deck_tiles`, so the deck carries the ribbon's whole levelled bench: a
    narrower deck IS the pinch that matching widths exist to avoid (section
    8.4), and one exactly as wide as the carriageway stands its own rails on
    it (DECK_WIDTH_DEPENDS_ON).

    `report` carries back the bent legs `assemble.bridge` declined to emit
    over ground that had already reached the deck's underside, so the
    measurement names them rather than the span quietly having fewer piles
    than its bay count implies.
    """
    h = shared_deck_datum(span, existing) if deck_y is None else deck_y
    stations = land_seated_deck_stations(span, surface, h, report)
    pieces = asm.bridge(
        tuple(span["bank_a"]["xz"]), tuple(span["bank_b"]["xz"]),
        bearing_of(span), h, surface, width_tiles=deck_tiles(span),
        carriageway_half_m=float(span["deck_width_m"]) / 2.0,
        bent_spacing_m=bent_spacing_m, name=span["crossing_id"], report=report,
        deck_stations=stations,
        end_bents=tuple(end[-1] for end in (span.get("terminal_banks") or ())
                        if _TERMINAL_END_BENT_MODE == "own"))
    pieces, shared = omit_shared_deck_pieces(pieces, existing)
    if report is not None:
        report["deck_tiles_carried_by_existing_bridge"] = [
            p for p in shared if p["prefab"] == "wood_floor"]
        report["rails_inside_shared_deck"] = [
            p for p in shared if p["prefab"] != "wood_floor"]
    # A rail is an edge, not a fence across an intersecting road. Open the
    # furniture at those junctions; load-bearing bents remain. The final
    # assembly, including its existing carriers, is structurally verified
    # by prepare(), after this and the buried-overhang trim.
    blocked = route_obstructions(pieces, surface, routes, rail_openings=True,
                                 existing=existing)
    openings = {i: hits for i, hits in blocked.items()
                if pieces[i].role.endswith(("/rail", "/rail_post"))}
    if report is not None:
        report["rail_openings_at_roads"] = [
            dict(prefab=pieces[i].prefab, role=pieces[i].role,
                 xz=[pieces[i].x, pieces[i].z], roads=hits)
            for i, hits in openings.items()]
        floor_top = asm.facts()["wood_floor"].hi[1]
        report["land_tiles_seated"] = [
            dict(prefab=p.prefab, xz=[round(p.x, 6), round(p.z, 6)],
                 top_y=round(p.y + floor_top, 6), nominal_y=h,
                 footprint_min_y=round(asm.bed_under(p, surface), 6),
                 land=asm.bed_under(p, surface) >= float(span.get("water_surface_y", 30.0)),
                 beyond_bank=p.overhang)
            for p in pieces if p.prefab == "wood_floor"
            and abs(p.y + floor_top - h) > 1e-6]
    return [p for i, p in enumerate(pieces) if i not in openings]


def existing_bridges(spans: list[dict]) -> dict[str, tuple]:
    """Nearby complete bridge bodies surviving the ledger's final-state fold.

    Reuse the same retire/clear/restore semantics as replay, not raw historical
    spawn rows. These are context, never pieces to put on the wire again.
    """
    from project import Projection

    projection = Projection(worldseed.ledger_path())
    projection.fold()
    bodies: dict[str, list] = {}
    roles = {"wood_floor": "deck", "wood_beam": "rail",
             "wood_pole": "rail_post", "wood_pole_log_4": "pile",
             "wood_wall_log_4x0.5": "cap"}
    for q in projection.placements.values():
        if q["role"] != "bridge_body_place":
            continue
        yaw, pitch, roll = map(float, (q["rot"] or "0,0,0").split(","))
        if abs(pitch % 360.0) > 1e-6 or abs(roll % 360.0) > 1e-6:
            raise ValueError(f"existing bridge {q['site_id']} has a tilted "
                             f"piece the boardwalk support solver cannot model")
        role = roles[q["prefab"]]
        bodies.setdefault(q["site_id"], []).append(asm.Placed(
            q["prefab"], q["u"], q["y"], q["v"], yaw,
            f"existing/{q['site_id']}/{role}"))
    out = {}
    for span in spans:
        ax, az = span["bank_a"]["xz"]
        bx, bz = span["bank_b"]["xz"]
        # a terminal bank's deck runs a landing out past its bank, so the
        # decks it can meet are that much further off (DECK_OVERLAP_DEPENDS_ON)
        margin = deck_tiles(span) + 4.0 + (
            LAND_DESCENT_MAX_TILES * LAND_RAMP_PITCH_M
            if span.get("terminal_banks") else 0.0)
        out[span["crossing_id"]] = tuple(
            piece for cid, body in bodies.items()
            if cid != span["crossing_id"] and any(
                min(ax, bx) - margin <= p.x <= max(ax, bx) + margin
                and min(az, bz) - margin <= p.z <= max(az, bz) + margin
                for p in body)
            for piece in body)
    return out


def _clip_polygon(poly: list, signed_distance, inside: bool) -> list:
    """Clip a convex polygon against one half-plane, retaining its boundary."""
    result = []
    for a, b in zip(poly, poly[1:] + poly[:1]):
        da, db = signed_distance(a), signed_distance(b)
        ka, kb = (da <= 0.0, db <= 0.0) if inside else (da >= 0.0, db >= 0.0)
        if ka:
            result.append(a)
        if ka != kb:
            t = da / (da - db)
            result.append((a[0] + t * (b[0] - a[0]),
                           a[1] + t * (b[1] - a[1])))
    return result


def omit_shared_deck_pieces(pieces: list, existing: tuple) -> tuple[list, list]:
    """Reuse fully covered floors and remove rails inside the joined deck.

    Polygon subtraction checks the union, not nine probes or a bounding box:
    a tiny uncovered strip remains a required tile. The 0.1mm expansion of
    existing squares accommodates the emitter's four-decimal wire rounding.
    Height compatibility uses the same limit as a deck's bank step.
    A rail resting on that shared floor is now an interior fence, not an edge.
    """
    floors = [p for p in existing if p.prefab == "wood_floor"]
    if not floors:
        return pieces, []
    kept, shared = [], []
    facts = asm.facts()
    floor_top = facts["wood_floor"].hi[1]
    for p in pieces:
        if (p.prefab != "wood_floor"
                and not p.role.endswith(("/rail", "/rail_post"))):
            kept.append(p)
            continue
        a = math.radians(p.yaw)
        ca, sa = math.cos(a), math.sin(a)
        f = facts[p.prefab]
        x0, x1, z0, z1 = f.lo[0], f.hi[0], f.lo[2], f.hi[2]
        uncovered = [[(p.x + x * ca + z * sa, p.z - x * sa + z * ca)
                      for x, z in ((x0, z0), (x1, z0), (x1, z1), (x0, z1))]]
        deck_top = (p.y + floor_top if p.prefab == "wood_floor" else
                    p.y - (1.0 if p.role.endswith("/rail") else 0.5))
        carriers = []
        for old in floors:
            if (abs(deck_top - old.y - floor_top) > ABUTMENT_STEP_MAX_M
                    or math.hypot(p.x - old.x, p.z - old.z) > 3.0):
                continue
            remaining = []
            for polygon in uncovered:
                inside = polygon
                for axis, sign in ((0, 1), (0, -1), (1, 1), (1, -1)):
                    def distance(pt):
                        return sign * tile_frame(old, *pt)[axis] - 1.0001

                    outside = _clip_polygon(inside, distance, False)
                    if len(outside) >= 3:
                        remaining.append(outside)
                    inside = _clip_polygon(inside, distance, True)
                    if not inside:
                        break
            uncovered = remaining
            carriers.append(old.role)
            if not uncovered:
                break
        if uncovered:
            kept.append(p)
        else:
            shared.append(dict(prefab=p.prefab, xz=[p.x, p.z], y=p.y,
                               carried_by=sorted(set(carriers))))
    return kept, shared


# THE TRIM ASKS THE GUARD'S QUESTION TOO -- OVERHANG_TRIM_DEPENDS_ON.
#
# `trim_buried_overhang` exists to drop an overhang station the bank has
# swallowed.  It asked with its own reading (one bilinear sample at the
# tile's pivot) against its own 0.30 m slack.  `burial_guard` then asks the
# gate's question of the same tile: the LOWEST lattice vertex under its
# rotated footprint against its own top, at 0.05 m.  A tile swallowed by
# 0.05-0.30 m therefore survived the trim and refused the whole deck.
# MEASURED on drive 3, S13-stathub-dock-x01: "2 of 177 emitted piece(s) lie
# COMPLETELY under the applied surface, worst wood_floor ... at
# (689.3,33.530,830.4) whose OWN TOP 33.627 m is 0.065 m below the lowest
# ground 33.692 m".  That tile stands 1.85 m BEYOND the projected bank_a,
# which is the overhang station, and only the trim may drop it.  A station
# whose tile the guard would refuse is swallowed by definition.  So the trim
# drops it on EITHER reading and still never trims inside the crossing.
OVERHANG_TRIM_DEPENDS_ON = (
    "the trim's own slack (BURIAL_MAX_M at the tile's pivot)",
    "the burial guard's own reading and limit (BURIED_MAX_M at the lowest "
    "lattice vertex under the tile's footprint)",
    "the bank coordinates, inside which nothing is ever trimmed")
# Swapped out by the selftest neuter: "the_guard" | "own_slack" (shipped).
_OVERHANG_TRIM_ASKS = "the_guard"


def trim_buried_overhang(span: dict, pieces: list, surface,
                         deck_y: float) -> tuple[list, list[dict]]:
    """Drop an OVERHANG station whose tiles the bank has already swallowed.

    `assemble.bridge` lays `overhang_stations` extra 2 m stations BEYOND each
    bank coordinate, and its reason is measured and correct: on S1 the agreed
    bank coordinate sat at generated height 30.10, a tenth of a metre above
    the water plane, and one station of overhang is what put the end of the
    deck on real ground "whether or not the road ribbon has been levelled
    yet".

    Here the ribbon HAS been levelled -- that is what the three terrain legs
    were for -- so at an abutment whose bench is already at H the overhang
    buys nothing, and where the ground keeps rising past the bank it buries
    the station it was meant to seat.  MEASURED on N1-x04, whose far bank
    stands at 34.92 m against a 33.24 m deck: the overhang tile's walking
    surface lands 2.703 m UNDER the applied ground.  A buried walking surface
    is a hole in the road, which is the same defect as a floating one with
    the sign reversed.

    So the overhang is SOLVED per end against the applied surface rather than
    configured: keep the station when its tiles sit on or above the ground,
    drop the whole station when any of them is buried past the limit -- by
    this trim's own slack OR by the burial guard's reading
    (OVERHANG_TRIM_DEPENDS_ON).  Never trims inside the bank coordinates --
    only the stations beyond them -- so no trim can shorten the crossing
    itself.

    Returns (kept pieces, a record of every station dropped).  A SHRINK IS
    SILENT unless it is written down, so the record is returned rather than
    logged and forgotten, and it rides in the span's ledger meta.
    """
    ax, az = span["bank_a"]["xz"]
    bx, bz = span["bank_b"]["xz"]
    length = math.hypot(bx - ax, bz - az)
    ux, uz = (bx - ax) / length, (bz - az) / length
    f_floor = asm.facts()["wood_floor"]

    def along(p) -> float:
        return (p.x - ax) * ux + (p.z - az) * uz

    # Station key beyond either bank, rounded to the 2 m pitch.
    buried_stations: dict[int, dict] = {}
    for p in pieces:
        if not p.role.endswith("/deck"):
            continue
        t = along(p)
        if -0.5 <= t <= length + 0.5:
            continue                      # inside the crossing: never trimmed
        key = int(round(t / 2.0))
        g = surface(p.x, p.z)
        top = p.y + f_floor.hi[1]
        depth = g - top
        lowest = (_lowest_ground_under(p, surface)
                  if _OVERHANG_TRIM_ASKS == "the_guard" else None)
        guard_depth = None if lowest is None else lowest - top
        if depth > BURIAL_MAX_M or (guard_depth is not None
                                    and guard_depth > BURIED_MAX_M):
            worst = max(depth, guard_depth if guard_depth is not None
                        else depth)
            prev = buried_stations.get(key)
            if prev is None or worst > prev["worst_burial_m"]:
                buried_stations[key] = {
                    "station_t_m": round(t, 2), "xz": [round(p.x, 2),
                                                       round(p.z, 2)],
                    "applied_ground_y": round(g, 3),
                    "lowest_ground_under_y": (None if lowest is None
                                              else round(lowest, 3)),
                    "worst_burial_m": round(worst, 3),
                    "end": "bank_a" if t < 0 else "bank_b"}
    if not buried_stations:
        return pieces, []
    keys = set(buried_stations)
    kept = [p for p in pieces
            if not (int(round(along(p) / 2.0)) in keys
                    and not (-0.5 <= along(p) <= length + 0.5))]
    dropped = sorted(buried_stations.values(), key=lambda d: d["station_t_m"])
    for d in dropped:
        d["pieces_dropped"] = len(pieces) - len(kept)
        d["why"] = ("overhang station beyond the bank coordinate, buried past "
                    f"{BURIAL_MAX_M} m at its pivot or past the burial "
                    f"guard's {BURIED_MAX_M} m at the lowest vertex under it, "
                    "by ground that keeps rising; the deck still reaches the "
                    "bank coordinate itself, where the road's own bench "
                    "stands at the deck's H")
    return kept, dropped


# --------------------------------------------------------------------------
# The guards.  Each returns a list of refusal strings: empty is a pass.
# --------------------------------------------------------------------------

def shared_deck_datum(span: dict, existing: tuple) -> float:
    """Join a standing deck at its height, without moving it or its rails.

    Only an overlapping floor within the legal height band and one legal
    bank-step of the planned road datum can supply a candidate. All actual
    bank, burial, support and obstruction checks still run at that height.
    """
    required = float(span["deck_y_REQUIRED"])
    if not existing:
        return required
    lo, hi = span["legal_deck_y_interval"]
    ax, az = span["bank_a"]["xz"]
    bx, bz = span["bank_b"]["xz"]
    length = math.hypot(bx - ax, bz - az)
    ux, uz = (bx - ax) / length, (bz - az) / length
    half = deck_tiles(span)
    candidates: dict[float, int] = {}
    top_offset = asm.facts()["wood_floor"].hi[1]
    for p in existing:
        if p.prefab != "wood_floor":
            continue
        top = round(p.y + top_offset, 4)
        along = (p.x - ax) * ux + (p.z - az) * uz
        lateral = (p.x - ax) * uz - (p.z - az) * ux
        if (lo <= top <= hi and abs(top - required) <= ABUTMENT_STEP_MAX_M
                and -2.0 <= along <= length + 2.0 and abs(lateral) < half):
            candidates[top] = candidates.get(top, 0) + 1
    return (min(candidates, key=lambda y: (-candidates[y], abs(y - required), y))
            if candidates else required)


def deck_datum_guard(span: dict, deck_y: float, *,
                     existing: tuple = ()) -> list[str]:
    """The solved datum, including a compatible existing-deck join, is binding."""
    bad = []
    required = float(span["deck_y_REQUIRED"])
    resolved = shared_deck_datum(span, existing)
    lo, hi = (float(v) for v in span["legal_deck_y_interval"])
    if abs(deck_y - resolved) > 1e-3:
        bad.append(
            f"{span['crossing_id']}: deck at {deck_y:.3f} m is not this "
            f"span's resolved H {resolved:.3f} m (planned {required:.3f} m). "
            f"Only an overlapping existing floor inside the legal band and "
            f"within {ABUTMENT_STEP_MAX_M:g} m of the planned datum can "
            f"change H; both actual bank steps must still pass.")
    if not (lo - 1e-6 <= deck_y <= hi + 1e-6):
        bad.append(
            f"{span['crossing_id']}: deck at {deck_y:.3f} m is outside the "
            f"recorded legal interval [{lo:.3f}, {hi:.3f}] m.")
    if abs(deck_y - SEA_DECK_DATUM_Y) <= 1e-3 and abs(required - SEA_DECK_DATUM_Y) > 1e-3:
        bad.append(
            f"{span['crossing_id']}: deck at the SEA datum "
            f"DECK_DATUM_Y {SEA_DECK_DATUM_Y:.1f} m on an INLAND channel "
            f"whose banks stand at {span['bank_a']['ground_y']:.2f} / "
            f"{span['bank_b']['ground_y']:.2f} m and whose solved H is "
            f"{required:.3f} m. That is a "
            f"{required - SEA_DECK_DATUM_Y:.2f} m step at both abutments.")
    if not span.get("single_H_both_banks"):
        bad.append(
            f"{span['crossing_id']}: `single_H_both_banks` is false, so a FLAT "
            f"deck cannot meet both banks and this generator is the wrong "
            f"tool for it.")
    return bad


def deck_landfall(span: dict, pieces: list) -> dict[str, dict]:
    """Where each end of the deck ACTUALLY MEETS THE GROUND, per bank.

    `assemble.bridge` lays overhang stations BEYOND each bank coordinate and
    `trim_buried_overhang` removes the ones the bank has swallowed, so the
    outermost SURVIVING deck tile - not the bank coordinate - is where a
    walker's last ground step is taken.  Returned in the span's own frame so
    the caller never has to re-derive the axis.
    """
    ax, az = span["bank_a"]["xz"]
    bx, bz = span["bank_b"]["xz"]
    L = math.hypot(bx - ax, bz - az)
    ux, uz = ((bx - ax) / L, (bz - az) / L) if L > 0 else (0.0, 0.0)
    tiles = [p for p in pieces if p.role.endswith("/deck")]
    out: dict[str, dict] = {}
    for end, anchor, sign in (("bank_a", 0.0, -1.0), ("bank_b", L, +1.0)):
        if not tiles:
            out[end] = {"has_deck": False, "along_m": anchor,
                        "xz": [ax + ux * anchor, az + uz * anchor],
                        "outboard_of_bank_m": 0.0}
            continue
        alongs = [(p.x - ax) * ux + (p.z - az) * uz for p in tiles]
        edge = (min(alongs) - TILE_HALF_M if sign < 0
                else max(alongs) + TILE_HALF_M)
        centre_t = min(alongs) if sign < 0 else max(alongs)
        end_tile = min((p for p, t in zip(tiles, alongs)
                        if abs(t - centre_t) < 1e-5),
                       key=lambda p: abs(lateral_of(span, p)))
        out[end] = {"has_deck": True, "along_m": round(edge, 3),
                    "xz": [ax + ux * edge, az + uz * edge],
                    "outboard_of_bank_m": round(abs(edge - anchor), 3),
                    "deck_y": end_tile.y + asm.facts()["wood_floor"].hi[1]}
    return out


def abutment_guard(span: dict, pieces: list, surface, deck_y: float) -> list[str]:
    """The step from the road bench onto the deck, at BOTH banks, against the
    APPLIED surface and against the deck's OWN H.

    THE STEP IS TAKEN WHERE THE DECK ENDS, NOT WHERE THE WATER STARTS, AND
    FOR SIXTEEN SPANS IT WAS MEASURED AT THE WATER.  The bank coordinate is
    by construction the WET EDGE of the channel - `route.find_crossings`
    walks inland until the bed leaves the water plane - so it is the LOWEST
    point of the approach, and probing the ground there and calling it "the
    bench" is the longitudinal twin of the lateral defect
    `project_onto_centreline` exists for.

    MEASURED on Ulfsland 2026-09-23, W10-brgs1-treenear-x01 bank_a, applied
    surface, deck 32.500 m:

        at the bank coordinate (-326.00,-66.00)   31.513   step +0.987  REFUSED
        1 m inland                                32.240   step +0.260
        2 m inland                                32.096   step +0.404
        4 m inland                                31.928   step +0.572

    The bank coordinate sits in a 0.727 m pit relative to the ground one
    metre behind it, and the deck's own outermost surviving tile reaches
    1.000 m inland of it - so the step a walker takes there is +0.260 m and
    the guard refused the whole 89.44 m channel for a number nobody walks on.

    THE CORRECTION IS PROVEN NOT TO HAVE DISARMED THE GUARD IN THE SAME RUN:
    T5-temple-dockshore-x01 bank_b reads 31.000 m FLAT for 4 m inland, so its
    +1.500 m step survives the move and is still refused.  A dock shore that
    is genuinely a metre and a half below its bridge stays refused; a road
    whose last metre dips into the water does not.

    Both numbers are reported, always.  The bank-coordinate step is the old
    reading and hiding it would make this correction unauditable.
    """
    bad = []
    land = deck_landfall(span, pieces)
    for end in ("bank_a", "bank_b"):
        bx_, bz_ = span[end]["xz"]
        at_bank = deck_y - surface(bx_, bz_)
        lf = land[end]
        lx, lz = lf["xz"]
        landing_y = lf.get("deck_y", deck_y)
        step = landing_y - surface(lx, lz)
        if abs(step) > ABUTMENT_STEP_MAX_M:
            owner = (
                f" This bank IS its segment's own terminus "
                f"(`terminal_banks`, network.CROSSING_BANKS_DEPENDS_ON): no "
                f"emission of {span.get('segment')} benches the ground "
                f"beyond it, so THIS DECK lands itself "
                f"(TERMINAL_LANDING_DEPENDS_ON, `land_seated_deck_stations`) "
                f"and the landing it laid does not meet the ground."
                if end in (span.get("terminal_banks") or ()) else
                " The bench this abutment needs is the road's, and it is "
                "measured where the walker meets it rather than assumed.")
            bad.append(
                f"{span['crossing_id']} {end}: step {step:+.3f} m from the "
                f"applied ground {surface(lx, lz):.3f} m at "
                f"({lx:.2f},{lz:.2f}) - where this deck's outermost tile "
                f"ends, {lf['outboard_of_bank_m']:.3f} m outboard of the bank "
                f"coordinate - onto a deck at {landing_y:.3f} m exceeds "
                f"{ABUTMENT_STEP_MAX_M} m (at the bank coordinate itself the "
                f"step reads {at_bank:+.3f} m).{owner}")
    return bad

def lateral_of(span: dict, p) -> float:
    """Signed distance of a piece from the crossing's centreline, in metres."""
    ax, az = span["bank_a"]["xz"]
    bx, bz = span["bank_b"]["xz"]
    length = math.hypot(bx - ax, bz - az)
    ux, uz = (bx - ax) / length, (bz - az) / length
    return (p.x - ax) * uz - (p.z - az) * ux



def seating_guard(span: dict, pieces: list, surface) -> list[str]:
    """Piles must reach the bed; the TRAVELLED WAY must not be buried.

    THE MEASUREMENT NOW LIVES IN `crossings/assemble.py::check_seating` AND
    THIS IS ITS CALLER.  It was written here first, against N1-x04 and
    N4-x01, and for a while it was the only place in the tree that asked the
    question -- which is exactly why every pile stack and deck that was NOT a
    boardwalk span went unasked, and why 24 of the acceptance gate's 45
    `buried` bodies are over-water structures.  Duplicating it there and
    keeping a copy here would leave two answers to one question, so the copy
    is gone and the generators run the same function before they return.

    TWO POPULATIONS, AND CONFLATING THEM IS THE WHOLE MISTAKE.  A 6 m deck
    arriving at a bank with any cross-slope puts its OUTER tile row into the
    bank -- that is the deck keying in, and it is what `assemble.bridge`'s
    overhang comment describes as putting "the end of the deck on real
    ground".  MEASURED on N1-x04 and N4-x01: the outer row at the abutment is
    buried 0.610-2.703 m while the centre row stands clear at H.  The scoping
    to the travelled way moved into `check_seating` with the rest, and the
    `lateral` it scopes on is now RECORDED BY THE GENERATOR rather than
    recomputed from span geometry here.

    The narrowing is proven not to have disarmed the guard: at the sea datum
    the whole deck drops below both banks, so the CENTRE row is buried too
    and `--prove-guards` still has to see this guard refuse.  A scope change
    that makes the guard stop firing on the defect it exists for would abort
    the run.
    """
    m = asm.check_seating(pieces, surface)
    cid = span["crossing_id"]
    bad = []
    for r in m["unfounded_columns"]:
        bad.append(
            f"{cid}: pile column at ({r['xz'][0]:.2f}, {r['xz'][1]:.2f}) "
            f"has its foot at {r['foot_y']:.3f} m against applied ground "
            f"{r['applied_ground_y']:.3f} m -- overlap {r['embed_m']:+.3f} m, "
            f"under the {asm.PILE_EMBED_MIN_M} m needed to pin it to "
            f"maxSupport. A pile that stops at the bed is a pile with no "
            f"foundation.")
    for r in m["piles_under_the_bed"]:
        bad.append(
            f"{cid}: pile at ({r['xz'][0]:.2f}, {r['xz'][1]:.2f}) has its TOP "
            f"{r['depth_m']:.3f} m under applied ground "
            f"{r['applied_ground_y']:.3f} m -- an invisible pole.")
    # THE SAME POPULATION `check_seating` NOW JUDGES, AND NOT A NARROWER ONE.
    # A second reader of one measurement that reports fewer of its lists than
    # the measurement produces is the two-instruments defect in miniature:
    # `refuse_on_seating` would refuse this span and this guard would call it
    # clean.  See `assemble.SEATING_POPULATION_DEPENDS_ON`.
    for r in m["structure_under_the_bed"]:
        bad.append(
            f"{cid}: {r['prefab']} ({r['role']}) at ({r['xz'][0]:.2f}, "
            f"{r['xz'][1]:.2f}) has its TOP {r['depth_m']:.3f} m under "
            f"applied ground {r['applied_ground_y']:.3f} m -- a piece nobody "
            f"can see or use.")
    for r in m["pieces_unjudged"]:
        bad.append(
            f"{cid}: {r['prefab']} ({r['role']}) at ({r['xz'][0]:.2f}, "
            f"{r['xz'][1]:.2f}) was judged by NO ARM of the seating check.")
    for r in m["travelled_way_buried"]:
        bad.append(
            f"{cid}: TRAVELLED-WAY deck tile at ({r['xz'][0]:.2f}, "
            f"{r['xz'][1]:.2f}) has its walking surface {r['depth_m']:.3f} m "
            f"BELOW the applied ground {r['applied_ground_y']:.3f} m. A "
            f"buried centre tile is a hole in the road.")
    return bad


def structural_guard(verdict: dict) -> list[str]:
    bad = []
    if not verdict["ok"]:
        for f in verdict["failures"][:12]:
            bad.append(
                f"support {f['support']:.2f} < {f['needed']:.2f} needed "
                f"({f['prefab']} {f['role']} at {f['xz']} y={f['y']})"
                + (" -- DEAD, it would not survive a player walking up to it"
                   if f["dead"] else ""))
        for r in verdict["no_in_water_violations"]:
            bad.append(f"{r}: prefab refuses to be placed in water")
        if not verdict["failures"] and not verdict["no_in_water_violations"]:
            bad.append("verify() reported not-ok with no named failure")
    return bad


# The DECLARED span is sampled at the same 1 m the gate's traversability walk
# uses, so a shortfall here is the shortfall the gate will report.
COVERAGE_SAMPLE_M = 1.0
# `wood_floor` is a MEASURED 2.000 m square, so a tile covers +/-1.000 m about
# its own origin in its own rotated frame.
TILE_HALF_M = 1.0
# A SEAM IS UNDER BOTH TILES, NOT UNDER NEITHER.  Tiles are laid at exactly
# the 2 m pitch, so a point on the line between two of them reads |1.000| in
# both frames, and floating point puts it a hair outside each.  A deck with
# an EVEN number of rows (the bench-width deck, DECK_WIDTH_DEPENDS_ON) lays
# its centreline exactly on a seam.  MEASURED on the drive-3 re-emit,
# T13-temple-harboursouth-x01, a 4-row deck at yaw 121.06: bank_a read
# (along 0.0, lat 1.0) from its nearest tile and was refused as an uncovered
# metre ("covers 7 of 8") that two abutting tiles carry.  The tolerance is a
# micrometre, far below any gap a walker could fall through.  Its neuter is
# `_neuter_tile_covers_to_the_exact_half`.
TILE_SEAM_M = 1e-6


def tile_frame(p, px: float, pz: float) -> tuple[float, float]:
    """(along, lateral) of a world point IN THIS TILE'S OWN FRAME.

    THE ROTATION MUST BE THE INVERSE OF THE ONE THAT PLACED THE TILE, AND FOR
    THREE MONTHS IT WAS THE FORWARD ONE.  `assemble._unit` and
    `assemble.Placed.aabb` place a piece with the VALHEIM COMPASS yaw --
    0 = +Z, 90 = +X, clockwise from above -- so a local offset (lat, along)
    lands at

        world_dx = lat*cos(y) + along*sin(y)
        world_dz = -lat*sin(y) + along*cos(y)

    and the inverse, the map this function needs, is

        along = dx*sin(y) + dz*cos(y)
        lat   = dx*cos(y) - dz*sin(y)

    The code here used `cos(-y), sin(-y)` in the MATH convention, which is
    the FORWARD map again.  Forward instead of inverse is a rotation of the
    tested square by 2*yaw, and a square is only invariant under multiples of
    90 degrees -- so the guard tested a square up to 45 degrees off the tile
    it named.

    MEASURED on Ulfsland 2026-09-23, W10-brgs1-treenear-x01, yaw 170.346 deg
    (2*yaw = 340.7 deg = 19.3 deg off square): the 319-piece deck this module
    generates covers 90 of its 90 declared metres, and `span_coverage_guard`
    REFUSED it at 79 of 90.  The eleven it called holes sit at |along| 0.995
    .. 0.946 and |lat| 0.989 .. 0.874 inside a tile of half-extent 1.000 --
    strictly interior on both axes, by up to 0.126 m.  A guard that refuses a
    deck for not covering ground it is standing on does not stop a bad deck,
    it stops a good one, and it costs 11 m of span per 90 on any crossing
    whose bearing is not near a right angle.
    """
    yaw = math.radians(p.yaw)
    sy, cy = math.sin(yaw), math.cos(yaw)
    dx, dz = px - p.x, pz - p.z
    return dx * sy + dz * cy, dx * cy - dz * sy


def tile_covers(p, px: float, pz: float,
                half: float = TILE_HALF_M) -> bool:
    """Is (px, pz) inside this tile's own rotated 2 m square, its edge
    included (TILE_SEAM_M)?

    The tile is yawed to the span's bearing, so an axis-aligned test would
    both miss covered ground and claim uncovered ground at the corners.
    """
    along, lat = tile_frame(p, px, pz)
    return abs(along) <= half + TILE_SEAM_M and abs(lat) <= half + TILE_SEAM_M


def span_coverage_guard(span: dict, pieces: list,
                        sample_m: float = COVERAGE_SAMPLE_M, *,
                        existing: tuple = ()) -> list[str]:
    """DOES THIS DECK ACTUALLY COVER THE CROSSING IT WAS ASKED TO CROSS?

    THE CHECK THAT WOULD HAVE CAUGHT A DECK BEING CREDITED WITH A CHANNEL IT
    IS THREE METRES AWAY FROM.  `project_onto_centreline` moves each abutment
    onto the segment's OWN recorded centreline, and it is RIGHT to: an
    abutment more than the ribbon's half-bench off the line probes natural
    ground and reports it as the bench.  But the gate walks the crossing
    record's DECLARED span, metre by metre, asking whether a piece top
    carries a walker -- and when the record and the centreline disagree,
    those are two different lines and the deck can only stand on one.

    MEASURED on Ulfsland 2026-09-22, T4-meadhall-stathub-x02: the projection
    moves bank_a 2.31 m and bank_b 4.08 m, so the declared line and the built
    line are 2.315 m apart at one end and 4.076 m at the other AND NEVER
    COINCIDE.  The landed deck carries 49 of 49 metres of the road it stands
    on and 18 of 49 of the line its own crossing record declares.  Both
    numbers are true; only one of them is what the builder claimed.

    11 of the 15 crossings this network declares carry a non-zero projection
    offset, up to 6.07 m, so this is a property of the DECLARATIONS and not
    of one crossing.

    THE REPAIR IS NEVER TO MOVE THE DECK OFF THE BENCH.  The refusal names
    the uncovered metres and the lateral offsets so the crossing record can
    be reconciled with its own segment's centreline, which is where the road
    -- and therefore the walker -- actually is.
    """
    pj = span.get("bank_projection") or {}
    ends = pj.get("ends") or {}
    ax, az = (ends.get("bank_a") or {}).get("spec_xz", span["bank_a"]["xz"])
    bx, bz = (ends.get("bank_b") or {}).get("spec_xz", span["bank_b"]["xz"])
    length = math.hypot(bx - ax, bz - az)
    if length <= 0.0:
        return [f"{span['crossing_id']}: declared span has zero length, so "
                f"its coverage cannot be measured"]
    ux, uz = (bx - ax) / length, (bz - az) / length
    tiles = [p for p in pieces if p.role.endswith("/deck")]
    floor_top = asm.facts()["wood_floor"].hi[1]
    tiles.extend(p for p in existing if p.prefab == "wood_floor"
                 and abs(p.y + floor_top - span["deck_y_REQUIRED"])
                 <= ABUTMENT_STEP_MAX_M)
    n = int(round(length / sample_m)) + 1
    # The BUILT line, to say HOW FAR the declared one is from it rather than
    # only that it is not covered.
    cx, cz = span["bank_a"]["xz"]
    dx_, dz_ = span["bank_b"]["xz"]
    blen = math.hypot(dx_ - cx, dz_ - cz)
    bux, buz = ((dx_ - cx) / blen, (dz_ - cz) / blen) if blen > 0 else (0.0, 0.0)
    uncovered = []
    for i in range(n):
        t = length * i / (n - 1) if n > 1 else 0.0
        px, pz = ax + ux * t, az + uz * t
        if any(tile_covers(p, px, pz) for p in tiles):
            continue
        lat = (px - cx) * buz - (pz - cz) * bux
        uncovered.append((round(t, 1), round(px, 2), round(pz, 2),
                          round(lat, 3)))
    if not uncovered:
        return []
    worst = max(uncovered, key=lambda r: abs(r[3]))
    return [f"{span['crossing_id']}: the deck covers "
            f"{n - len(uncovered)} of {n} declared metres. "
            f"{len(uncovered)} metre(s) of the span this crossing DECLARES "
            f"-- ({ax:g},{az:g}) to ({bx:g},{bz:g}) -- have no tile over "
            f"them, worst at ({worst[1]},{worst[2]}), {abs(worst[3]):.3f} m "
            f"off the line actually being built. The abutments were "
            f"projected onto the segment's own recorded centreline (bank_a "
            f"{(ends.get('bank_a') or {}).get('lateral_off_centreline_m', 0.0):+.2f} m, bank_b "
            f"{(ends.get('bank_b') or {}).get('lateral_off_centreline_m', 0.0):+.2f} m), which is "
            f"correct for the bench and leaves the declared line somewhere "
            f"else. THE REPAIR IS TO RECONCILE THE CROSSING RECORD WITH ITS "
            f"SEGMENT'S CENTRELINE, never to move the deck off the bench: a "
            f"deck built on the declared line would stand off the road."]


BURIAL_GUARD_DEPENDS_ON = (
    "every EMITTED piece, not just the deck tiles and not just the ones "
    "beyond a bank; each piece's OWN prefab extent from "
    "`crossings/assemble.facts()`, so the TOP is the piece's top and not "
    "its pivot; and the APPLIED surface at every LATTICE VERTEX under that "
    "piece's own rotated FOOTPRINT (the one vertex nearest it, for a piece "
    "too small to cover any), taking the LOWEST reading -- read through "
    "`assemble.bed_under`, which is the construction "
    "`accept/sections/structures.py::piece_columns` asks of the finished "
    "body.\n"
    "\n"
    "IT IS A SEPARATE GUARD BECAUSE THE TWO NEIGHBOURING ONES BOTH ANSWER "
    "SOMETHING ELSE AND BOTH PASSED THE WRITE THAT PROVED IT.  `seating` "
    "asks whether a piece has ground UNDER it to stand on; `trim_buried_"
    "overhang` asks the burial question but ONLY of `/deck` pieces STRICTLY "
    "BEYOND a bank coordinate, because its job is to shorten an overhang "
    "and it is forbidden to shorten the crossing itself.  A tile buried "
    "INSIDE the crossing is therefore nobody's question.\n"
    "\n"
    "MEASURED, OrderRun's own write, 2026-09-23: T9-wttown-dockshore-sb01 "
    "built 35 pieces with all five shipped guards PASSING and its own "
    "report printing `worst burial 0.0 m`, and `structures/buried` then "
    "found 2 of those 35 COMPLETELY under the applied surface -- wood_floor "
    "tiles at (677,-92) and (679,-92), tops 0.400 m below the ground.  The "
    "data was in the builder's hands before a single piece was sent: this "
    "guard re-measures the same pieces off the same surface and refuses.\n"
    "\n"
    "AND THE COST OF FINDING OUT LATE IS TERMINAL.  A deck is written with "
    "`flatten: FORBIDDEN`, so the moment it stands its own footprint is "
    "closed to every later terrain write -- `writer._flatten_forbidden_"
    "check` has no owner exception and retires only RETIRED records.  The "
    "gate classed both rows 1A_FORBIDDEN with `ground_owned_by` naming the "
    "deck's own seq: W10's 4 of 319 under seq 5421, T9's 2 of 35 under seq "
    "5433.  Refusing BEFORE the build is the only time this repair is "
    "free.")


# THE GATE'S OWN LIMIT, NOT THIS MODULE'S.  `BURIAL_MAX_M` is 0.30 m and it
# belongs to `trim_buried_overhang`, whose question is "has the bank
# swallowed this overhang station" -- a shrink decision that wants slack.
# `accept/sections/structures.py::buried` refuses at 0.05 m, and a builder
# that used its own looser number would pass pieces the gate then refuses:
# MEASURED on T9-wttown-dockshore-sb01, 0.30 m catches 1 of the 2 tiles the
# gate found and 0.05 m catches both.  One question, one limit.
BURIED_MAX_M = 0.05

# THE REDUCTION, NOT JUST THE LIMIT.  The limit above was taken from the gate
# so the two could not disagree -- and then the lowest ground under a piece
# was taken by a DIFFERENT reduction: nine BILINEAR samples at the footprint's
# corners, edges and centre.  The gate (`accept/sections/structures.py::
# piece_columns`) and the generator (`assemble.bed_under`, which seats every
# extra pile pole with its top EXACTLY on the ground) both read the LATTICE
# VERTICES under the piece's own rotated footprint, falling back to the one
# vertex nearest a piece too small to cover any -- `Heightmap.GetHeight` is
# nearest-vertex.  MEASURED on Fresh42, T3-temple-meadhall-x01 (road laid,
# every other guard PASSING): bent2's extra pole at (212.42, -42.95) has its
# top 29.316 exactly on vertex (212,-43) -- 0.000 m by the gate's reduction
# -- and the nine samples inside that cell read 29.399, so this guard
# refused the deck at "0.083 m buried" on a pole the gate accepts.  A builder
# that refuses what the gate accepts is the same disagreement in the other
# direction.  `footprint_samples` restores the shipped reduction (the neuter).
_BURIAL_READS = "vertex"            # vertex | footprint_samples (shipped)


def _lowest_ground_under(p, surface) -> float:
    if _BURIAL_READS == "vertex":
        return asm.bed_under(p, surface)
    fa = asm.facts()[p.prefab]
    hw = max(abs(fa.hi[0]), abs(fa.lo[0]))
    hd = max(abs(fa.hi[2]), abs(fa.lo[2]))
    return min(surface(p.x + dx, p.z + dz)
               for dx in (-hw, 0.0, hw) for dz in (-hd, 0.0, hd))


def burial_guard(span: dict, pieces: list, surface,
                 limit_m: float = BURIED_MAX_M) -> list[str]:
    """No emitted piece may lie COMPLETELY under the applied surface.

    The footprint minimum is used, never the pivot: a tile 2 m across on a
    bed that falls away is not buried because its centre reads high, and a
    tile on a bench that rises under one corner is.  The minimum is taken
    over the lattice vertices under the piece -- the gate's own reduction,
    read through `assemble.bed_under` -- so the builder and the gate cannot
    disagree about the same pieces.  See `_BURIAL_READS`.
    """
    f = asm.facts()
    bad_pieces = []
    for p in pieces:
        fa = f.get(p.prefab)
        if fa is None:
            continue
        top = p.y + fa.hi[1]
        lo = _lowest_ground_under(p, surface)
        depth = lo - top
        if depth > limit_m:
            bad_pieces.append((depth, p, top, lo))
    if not bad_pieces:
        return []
    bad_pieces.sort(key=lambda r: -r[0])
    depth, p, top, lo = bad_pieces[0]
    return [f"{span['crossing_id']}: {len(bad_pieces)} of {len(pieces)} "
            f"emitted piece(s) lie COMPLETELY under the applied surface, "
            f"worst {p.prefab} ({p.role}) at ({p.x:.1f},{p.y:.3f},{p.z:.1f}) "
            f"whose OWN TOP {top:.3f} m is {depth:.3f} m below the lowest "
            f"ground {lo:.3f} m anywhere under its footprint, against a "
            f"{limit_m:g} m limit. A deck carries flatten=FORBIDDEN, so "
            f"once it stands `writer._flatten_forbidden_check` closes its "
            f"own footprint to the very cut that would free these pieces "
            f"-- the gate reads that as 1A_FORBIDDEN with the deck's own "
            f"seq as the owner. CUT THE GROUND FIRST; this is the only "
            f"moment the repair is free."]


def burial_guard_arms() -> list[dict]:
    """Five constructed arms: four over one 2 m tile, one over one pile pole.

    CLEAR        ground below the tile's top: nothing refuses.
    BURIED       ground above it everywhere: refused.
    CORNER       the PIVOT reads clear and ONE FOOTPRINT CORNER does not.
                 This is the arm the shipped pivot sample gets wrong, and
                 it is the whole reason the guard takes a footprint
                 minimum -- MEASURED on T9-wttown-dockshore-sb01, whose two
                 buried tiles read 0.400 m at the pivot and 0.394/0.349 m
                 at the footprint minimum, so the pivot happens to agree
                 THERE and cannot be relied on to.
    LIMIT        buried between the two candidate limits.
    VERTEX       a pole seated with its top EXACTLY on the one vertex under
                 it, in a cell whose other readings stand higher -- see
                 `_BURIAL_READS`.

    The pieces are `assemble.Placed`, the type the generator emits, because
    the reduction reads each piece's own rotated footprint.  EVERY ARM IS A
    VERDICT with its own numbers; none of them asserts.
    """
    f = asm.facts()["wood_floor"]
    span = {"crossing_id": "FIX-burial-x01"}
    tile = asm.Placed("wood_floor", 0.0, 30.5, 0.0, 0.0, "FIX-burial-x01/deck")
    top = tile.y + f.hi[1]
    # T3-temple-meadhall-x01's bent2 extra pole, as the generator placed it.
    fp = asm.facts()["wood_pole_log_4"]
    pole_top = 29.316
    pole = asm.Placed("wood_pole_log_4", 212.42, pole_top - fp.hi[1], -42.949,
                      0.0, "FIX-burial-x01/bent2/pile")
    out = []
    for name, piece, bed, want in (
            ("ground_below_the_tile_top_is_not_buried", tile,
             (lambda x, z: top - 0.500), False),
            ("ground_above_the_tile_everywhere_is_buried", tile,
             (lambda x, z: top + 0.400), True),
            # THE ARM ONLY THE SAMPLE CAN FLIP, AND ITS DIRECTION IS THE
            # OPPOSITE OF THE OBVIOUS ONE.  Burial is "under the ground
            # EVERYWHERE", so the governing reading is the footprint
            # MINIMUM -- a single HIGH corner buries nothing.  The way a
            # pivot sample is wrong is therefore a FALSE REFUSAL: the
            # centre of the tile stands under a mound while one corner is
            # open air, and a pivot-only guard retires a piece a walker can
            # see. Written the other way round this arm was green under its
            # own neuter and asserted a falsehood; it is here in the
            # direction the instrument can actually be wrong.  THE OPEN AIR
            # IS ONE LATTICE COLUMN, x = -1 -- the tile's west edge column
            # (wood_floor spans x -1.0096..0.9904) -- because the lattice is
            # the only place the engine has a height; a corner defined at a
            # non-vertex x is a surface no world can hold.
            ("a_corner_of_open_air_the_pivot_cannot_see_is_not_buried", tile,
             (lambda x, z: (top - 0.500) if x <= -1.0 + 1e-9 else top + 0.400),
             False),
            # THE ARM ONLY THE LIMIT CAN FLIP.  Every other arm here is
            # buried by more than BOTH candidate limits, so a neuter that
            # loosens 0.05 m to 0.30 m would leave all of them green -- the
            # monotone trap: if every neuter you hold pushes the quantity
            # the same way, the other direction is untested no matter how
            # many neuters there are.  0.150 m sits between the two.
            ("a_tile_inside_the_overhang_slack_is_still_buried", tile,
             (lambda x, z: top + 0.150), True),
            # THE ARM ONLY THE REDUCTION CAN FLIP.  The one vertex under the
            # pole reads exactly its top; everywhere else in that cell reads
            # the 29.399 m the shipped nine samples found.  The gate reads
            # the vertex and accepts; the shipped guard refused the deck.
            ("a_pole_seated_on_its_own_vertex_is_not_buried", pole,
             (lambda x, z: pole_top if (x, z) == (212.0, -43.0) else 29.399),
             False)):
        got = burial_guard(span, [piece], bed)
        out.append(dict(name=name, want_refused=want, got_refused=bool(got),
                        ok=(bool(got) == want),
                        tile_top_m=round(piece.y + asm.facts()[piece.prefab]
                                         .hi[1], 3),
                        pivot_ground_m=round(bed(piece.x, piece.z), 3),
                        footprint_min_ground_m=round(
                            _lowest_ground_under(piece, bed), 3),
                        limit_m=BURIED_MAX_M, population=1, evidence=1,
                        why=(got[0] if got else "nothing refused")))
    return out


def _neuter_burial_reads_its_own_samples():
    """RESTORE THE SHIPPED REDUCTION: nine bilinear samples across the
    footprint instead of the lattice vertices the gate and the generator
    read.  Must refuse the pole the gate accepts."""
    global _BURIAL_READS
    was, _BURIAL_READS = _BURIAL_READS, "footprint_samples"

    def undo():
        global _BURIAL_READS
        _BURIAL_READS = was
    return undo


def _neuter_burial_guard_absent():
    """RESTORE THE STATE THAT SHIPPED: there is no burial guard at all.

    The other two neuters only move WHERE and AT WHAT LIMIT the guard
    reads, so neither can flip an arm buried past both candidate limits at
    every corner -- the monotone trap, caught twice tonight before this.
    The defect this one models is the one that actually happened: the guard
    did not exist, five other guards passed, and 35 pieces went out with 2
    of them under the ground.
    """
    real = globals()["burial_guard"]

    def absent(span, pieces, surface, limit_m: float = BURIED_MAX_M):
        return []

    globals()["burial_guard"] = absent

    def undo():
        globals()["burial_guard"] = real
    return undo


def _neuter_burial_samples_the_pivot():
    """RESTORE THE SHIPPED SAMPLE: read the bed at the piece's PIVOT.

    The defect BuryFix measured in `check_seating` and the one this guard
    was written against: a single reading at the centre of a 2 m tile
    decides whether the whole tile is under the ground.  It cannot see a
    corner, and a corner is what a bench that rises under one edge of a
    deck produces.
    """
    real = globals()["burial_guard"]

    def pivot_only(span, pieces, surface, limit_m: float = BURIED_MAX_M):
        def centre(x, z, _s=surface, _px=None, _pz=None):
            return _s(x, z)

        f = asm.facts()
        bad = []
        for p in pieces:
            fa = f.get(p.prefab)
            if fa is None:
                continue
            if centre(p.x, p.z) - (p.y + fa.hi[1]) > limit_m:
                bad.append(p)
        if not bad:
            return []
        return [f"{span['crossing_id']}: {len(bad)} of {len(pieces)} piece(s) "
                f"are buried, measured at the pivot"]

    globals()["burial_guard"] = pivot_only

    def undo():
        globals()["burial_guard"] = real
    return undo


def _neuter_burial_uses_the_overhang_limit():
    """RESTORE THE OTHER HALF: judge at this module's own 0.30 m slack.

    `BURIAL_MAX_M` belongs to a SHRINK decision and the gate refuses at
    0.05 m.  MEASURED on T9-wttown-dockshore-sb01 the difference is one of
    the two buried tiles -- a builder that passes a piece the gate then
    refuses, which is the disagreement this guard exists to end.
    """
    real = globals()["burial_guard"]

    def loose(span, pieces, surface, limit_m: float = BURIED_MAX_M):
        return real(span, pieces, surface, limit_m=BURIAL_MAX_M)

    globals()["burial_guard"] = loose

    def undo():
        globals()["burial_guard"] = real
    return undo


# THE GATE'S QUESTION, ASKED BEFORE THE BUILD -- CARRIAGEWAY_GUARD_DEPENDS_ON.
# `accept/instruments.py::blockers_at` counts a built piece against a road
# when its solid rises more than tan(38 deg) = 0.7813 m out of the walking
# surface, its underside is not above that height (a gantry is walked
# under), and its footprint reaches into the paved half-width.  A deck's
# rail posts (wood_pole, deck_y .. deck_y + 1.0) meet the first two
# conditions by design, so only the third keeps them off the road.  The top
# rail (wood_beam, underside deck_y + 0.8) is a gantry.  This guard asks the
# same three conditions of every emitted piece, against the deck's own
# walking surface and the carriageway it carries.
CARRIAGEWAY_GUARD_DEPENDS_ON = (
    "every EMITTED piece's own rotated solid (assemble.facts)",
    "the deck's walking surface, deck_y",
    "the step limit tan(38 deg), the same one the gate uses "
    "(ribbon.SLIDE_GRADIENT)",
    "the carriageway half-width the deck carries, deck_width_m / 2")


def _lateral_extent(span: dict, p) -> tuple[float, float]:
    """The piece's rotated footprint, projected across the span's line."""
    ax, az = span["bank_a"]["xz"]
    bx, bz = span["bank_b"]["xz"]
    L = math.hypot(bx - ax, bz - az)
    ux, uz = (bx - ax) / L, (bz - az) / L
    f = asm.facts()[p.prefab]
    a = math.radians(p.yaw)
    ca, sa = math.cos(a), math.sin(a)
    lats = []
    for px in (f.lo[0], f.hi[0]):
        for pz in (f.lo[2], f.hi[2]):
            wx = p.x + px * ca + pz * sa
            wz = p.z - px * sa + pz * ca
            lats.append((wx - ax) * uz - (wz - az) * ux)
    return min(lats), max(lats)


def carriageway_guard(span: dict, pieces: list, deck_y: float) -> list[str]:
    """No piece a walker would have to climb stands inside the carriageway
    this deck carries (CARRIAGEWAY_GUARD_DEPENDS_ON)."""
    hw = float(span["deck_width_m"]) / 2.0
    step = ribbonmod.SLIDE_GRADIENT
    f = asm.facts()
    bad = []
    for p in pieces:
        fa = f.get(p.prefab)
        if fa is None:
            continue
        if p.y + fa.hi[1] <= deck_y + step:
            continue                    # a step no bigger than terrain's own
        if p.y + fa.lo[1] > deck_y + step:
            continue                    # a gantry: walked under
        lo, hi = _lateral_extent(span, p)
        if lo < hw - 1e-6 and hi > -hw + 1e-6:
            bad.append((min(abs(lo), abs(hi)) if lo * hi > 0 else 0.0,
                        p, lo, hi))
    if not bad:
        return []
    bad.sort(key=lambda r: r[0])
    _d, p, lo, hi = bad[0]
    return [f"{span['crossing_id']}: {len(bad)} of {len(pieces)} emitted "
            f"piece(s) a walker would have to climb stand inside the "
            f"{2 * hw:g} m carriageway this deck carries, worst {p.prefab} "
            f"({p.role}) whose footprint spans lateral {lo:+.2f}..{hi:+.2f} m "
            f"against +/-{hw:g} m, rising {p.y + f[p.prefab].hi[1] - deck_y:.2f}"
            f" m out of the deck. The gate refuses these as `roads/"
            f"obstruction` (1D_OBSTRUCTED). A deck's own furniture stands "
            f"outside the road it carries, as every lamp does "
            f"(DECK_WIDTH_DEPENDS_ON)."]


def _fixture_bed(fn, note: str):
    return asm.Bed.applied(fn, f"boardwalk selftest fixture: {note}")


# THE GATE'S QUESTION, ASKED OF THE WHOLE CARRIAGEWAY -- CROSS_SECTION_DEPENDS_ON.
#
# `accept/instruments.py::blockers_at` clips one CROSS-SECTION LINE per 1 m
# station against each piece's footprint.  A wood_pole is 0.4 m across, so
# one standing between two stations is invisible to every one of them: the
# station spacing, not the road, decides whether it is "in" it.  MEASURED on
# Fresh42 drive 10: T3-temple-meadhall-x01's rail post at (200.6495,-37.8391)
# stands 0.72 m off S11-southcape-lhsouth's centreline (half-width 2.0 m) and
# its hull ends 0.0001 m short of station 1105's cross-section line.  The
# builder asked at the gate's stations, rounded that 0.1 mm the wrong way
# (a yaw-only quaternion; the census float32 one rounds it the other way),
# and shipped the post twice -- T3 emitted it with S11 already laid, and
# S11-x01's guard then judged it among the 85 existing pieces and passed it
# -- while the gate, asking the same predicate at the same stations with the
# census quaternion, counted it: `roads/obstruction` "1 of 138 pieces of one
# body stand in the carriageway (go round); wood_pole", 1D_OBSTRUCTED.
#
# The builder therefore asks the gate's UNCHANGED predicate at every point of
# the carriageway it can reach: each gate station's cross-section, with the
# gate's own tangent and standing surface, repeated at offsets along that
# tangent so the cells tile the road with a spacing no wider than half the
# thinnest piece this builder emits.  Offset 0 is the gate's own station, so
# everything the gate can count the builder counts too; what the builder adds
# is the metres between stations -- wherever a walker can stand, which is on
# dry ground or on a deck, not on the bed of a crossing nobody has decked.
CROSS_SECTION_DEPENDS_ON = (
    "the gate's blocker predicate, accept.instruments.blockers_at, unchanged",
    "the gate's station spacing and tangent, accept.instruments.SAMPLE_M and "
    "resample, so offset 0 IS a gate station",
    "the thinnest footprint among the prefabs this builder emits "
    "(assemble.facts)",
    "the height each built road was written to at every node (the ledger's "
    "road_segment profile), for a crossing nobody has decked yet")
DECK_PREFABS = ("wood_floor", "wood_beam", "wood_pole", "wood_pole_log_4",
                "wood_wall_log_4x0.5")
# Swapped out by the selftest neuter: "continuous" (shipped) | "stations"
# (the state that shipped drive 10: the gate's own cross-section lines only).
_CROSS_SECTIONS = "continuous"
# Swapped out by the selftest neuter: "written" (shipped: a gap in a road is
# judged at the height the road was written to) | "bed" (the surface under it).
_CROSSING_SURFACE = "written"


def cross_section_offsets() -> tuple[float, ...]:
    """Offsets along a station's tangent at which its cell is asked
    (CROSS_SECTION_DEPENDS_ON): odd in number so 0.0 is among them, equal in
    spacing, no wider apart than half the thinnest piece emitted."""
    if _CROSS_SECTIONS == "stations":
        return (0.0,)
    facts = asm.facts()
    thin = min(min(facts[name].hi[0] - facts[name].lo[0],
                   facts[name].hi[2] - facts[name].lo[2])
               for name in DECK_PREFABS)
    count = math.ceil(road_instruments.SAMPLE_M / (thin / 2.0) - 1e-9)
    count += 1 - count % 2
    step = road_instruments.SAMPLE_M / count
    return tuple((i - count // 2) * step for i in range(count))


_ROAD_GEOMETRY = None


def route_obstructions(pieces: list, surface, routes: tuple, *,
                       rail_openings: bool = False,
                       existing: tuple = ()) -> dict[int, list]:
    """Emitted solids obstructing ANY built road, using the gate's geometry.

    Drive 6's T3 bridge kept its rails off its own axis but put 14 of them
    inside S11. Its deck datum also misclassified beams as overhead gantries
    for walkers standing on the neighbouring road. Sample the latest
    built profiles at the gate's metre spacing and propagate reachable deck
    surfaces before asking the gate's unchanged blocker predicate -- at the
    gate's stations AND between them (CROSS_SECTION_DEPENDS_ON): a 0.4 m
    post between two stations is in the carriageway all the same.
    """
    if not pieces or not routes:
        return {}
    global _ROAD_GEOMETRY
    if _ROAD_GEOMETRY is None:
        _ROAD_GEOMETRY, _ = road_instruments.load_geometry()
    offsets = cross_section_offsets()
    solids = []
    for p in (*pieces, *existing):
        a = math.radians(p.yaw) / 2.0
        parts = _ROAD_GEOMETRY.get(p.prefab)
        solids.append(road_instruments.Solid(
            p.prefab, (p.x, p.y, p.z), (0.0, math.sin(a), 0.0, math.cos(a)),
            parts or [], parts is not None))
    grid = road_instruments.Grid(solids)
    indices = {id(s): i for i, s in enumerate(solids)}
    xmin, xmax = min(s.xmin for s in solids), max(s.xmax for s in solids)
    zmin, zmax = min(s.zmin for s in solids), max(s.zmax for s in solids)
    blocked: dict[int, list] = {}
    for route in routes:
        half = route["half_width_m"]
        # Keep one full bench beyond any possible contact, so the propagation
        # sees the bank that lets a walker reach the deck. Preserve the
        # original station phase and tangents, including at the window ends.
        margin = half + 2.0
        stations = road_instruments.resample(route["nodes"],
                                             road_instruments.SAMPLE_M)
        runs = []
        run = []
        for j, (_d, x, z) in enumerate(stations):
            if (xmin - margin <= x <= xmax + margin
                    and zmin - margin <= z <= zmax + margin):
                run.append(j)
            elif run:
                runs.append(run)
                run = []
        if run:
            runs.append(run)
        for run in runs:
            pts = [(stations[j][1], stations[j][2]) for j in run]
            surfaces = [road_instruments.surfaces_at(
                x, z, surface(*road_instruments.snap(x, z)), grid, 0.0)
                for x, z in pts]
            standing = road_gate.standing_surfaces(pts, surfaces)
            # A CROSSING NOBODY HAS DECKED YET HAS NO WALKING SURFACE, AND ITS
            # DECK WILL NOT BE THE BED.  A station that stands more than one
            # abutment step under the height the road itself was written to
            # (its own node heights, interpolated along it) is a gap in the
            # road -- open water or its shore -- and is judged at that written
            # height: it is where the deck will stand, and it is what the gate
            # sees once the crossing is built.  MEASURED on Fresh42 drive 10:
            # S11's lane over T3's deck edge is water or 30.09 m shore until
            # S11-x01 lands, against 32.603 m written; read against that bed,
            # T3's bent2 pile (top 32.37, under its own deck) is a 3 m post in
            # S11's road, and its rail posts over the shore pass unopened.
            if route.get("levels") is not None and _CROSSING_SURFACE == "written":
                designed = [_design_level(route, stations[j][0]) for j in run]
                standing = [
                    want if y is not None and y < want - ABUTMENT_STEP_MAX_M
                    else y for y, want in zip(standing, designed)]
            for k, j in enumerate(run):
                x, z = pts[k]
                before, after = stations[max(0, j - 1)], stations[
                    min(len(stations) - 1, j + 1)]
                dx, dz = after[1] - before[1], after[2] - before[2]
                length = math.hypot(dx, dz) or 1.0
                tx, tz = dx / length, dz / length
                y = standing[k]
                carriers = {id(s) for top, s in surfaces[k]
                            if s is not None and abs(top - y) <= 0.05}
                # BETWEEN STATIONS ONLY WHERE A WALKER CAN STAND.  A station
                # still under the water plane (a road that records no node
                # heights) keeps the gate's own cross-section alone.
                for off in (offsets if y >= WATER_LEVEL_M else (0.0,)):
                    sx, sz = x + tx * off, z + tz * off
                    obstructions = road_instruments.blockers_at(
                        sx, sz, y, -tz, tx, half, grid, set(), set())
                    if rail_openings:
                        # Open the whole railing at a junction, not just its
                        # posts while leaving a top beam suspended across it.
                        # The rail's base is the deck it guards. A genuinely
                        # grade-separated road below that base stays untouched.
                        for solid in grid.near(sx, sz, half + 1.0):
                            i = indices[id(solid)]
                            if i >= len(pieces):
                                continue
                            p = pieces[i]
                            if not p.role.endswith("/rail"):
                                continue
                            base = p.y - 1.0
                            if not base <= y + road_instruments.STEP_LIMIT_PER_M < solid.ymax:
                                continue
                            interval = solid.lateral_span(sx, sz, -tz, tx, half)
                            if interval is not None:
                                obstructions.append((*interval, solid))
                    for _lo, _hi, solid in obstructions:
                        if indices[id(solid)] >= len(pieces):
                            continue
                        if id(solid) in carriers:
                            continue
                        hits = blocked.setdefault(indices[id(solid)], [])
                        if route["seg"] not in hits:
                            hits.append(route["seg"])
    return blocked


def road_design_levels(ledger) -> dict[str, list[float]]:
    """The height each node of every built road was written to, by road name.

    The newest write wins, which is `road_gate.built_segments`' own rule; that
    function keeps the nodes' x and z and drops the third number, and this one
    keeps the third.
    """
    latest: dict[str, list[float]] = {}
    for line in Path(ledger).read_text().splitlines():
        if '"road_segment"' not in line:
            continue
        d = json.loads(line)
        p = d.get("params") or {}
        if p.get("role") != "road_segment" or d.get("op") != "terrain_write":
            continue
        latest[p["name"]] = [float(n[2]) for n in p["profile"]["nodes"]
                             if len(n) > 2]
    return latest


def routes_with_design_levels(segments: dict[str, dict], ledger) -> tuple:
    """`built_segments`' routes, each carrying `levels` (one height per node)
    where the ledger recorded one for every node."""
    levels = road_design_levels(ledger)
    routes = []
    for name, route in segments.items():
        got = levels.get(name)
        routes.append(dict(route, levels=got)
                      if got is not None and len(got) == len(route["nodes"])
                      else route)
    return tuple(routes)


def _design_level(route: dict, d: float) -> float:
    """The road's own written height `d` metres along it (linear between the
    two nodes that bracket `d`; flat beyond the ends)."""
    nodes, levels = route["nodes"], route["levels"]
    s = 0.0
    for (ax, az), (bx, bz), ya, yb in zip(nodes, nodes[1:], levels, levels[1:]):
        length = math.hypot(bx - ax, bz - az)
        if length > 0.0 and d <= s + length:
            return ya + (yb - ya) * max(0.0, (d - s) / length)
        s += length
    return levels[-1]


def _distance_to_route(route: dict, x: float, z: float) -> float:
    """Metres from (x, z) to the route's own centreline polyline."""
    best = math.inf
    nodes = route["nodes"]
    for (ax, az), (bx, bz) in zip(nodes, nodes[1:]):
        dx, dz = bx - ax, bz - az
        span2 = dx * dx + dz * dz
        t = 0.0 if span2 == 0.0 else max(0.0, min(
            1.0, ((x - ax) * dx + (z - az) * dz) / span2))
        best = min(best, math.hypot(x - ax - t * dx, z - az - t * dz))
    return best


def route_carriageway_guard(span: dict, pieces: list, surface,
                            routes: tuple, *, existing: tuple = ()) -> list[str]:
    """No emitted piece, and no piece already standing, obstructs a built road.

    An EXISTING piece is never removed or moved here, so one standing in a
    carriageway cannot be cleared by building this span: the span is refused
    with the numbers rather than built into a road another deck obstructs.
    """
    combined = [*pieces, *existing]
    blocked = route_obstructions(combined, surface, routes)
    if not blocked:
        return []
    by_seg = {r["seg"]: r for r in routes}
    rows = []
    for i, segs in blocked.items():
        p = combined[i]
        near = min((_distance_to_route(by_seg[s], p.x, p.z), s) for s in segs)
        rows.append((near[0], i, p, near[1], by_seg[near[1]]["half_width_m"]))
    rows.sort(key=lambda r: (r[1] >= len(pieces), r[0]))
    fresh = sum(1 for i in blocked if i < len(pieces))
    stale = len(blocked) - fresh
    worst = "; ".join(
        f"{p.prefab} ({p.role}) at ({p.x:.3f},{p.z:.3f}) y {p.y:.3f}, "
        f"{d:.2f} m from {seg}'s centreline against a {hw:g} m half-width"
        for d, _i, p, seg, hw in rows[:3])
    return [f"{span['crossing_id']}: {fresh} new and {stale} existing "
            f"piece(s) obstruct built roads; {worst}. Decks and supports "
            f"must stay clear of every walkable route, not only their own "
            f"span axis."
            + (f" The {stale} existing piece(s) are never removed or moved "
               f"by this builder, so this span is REFUSED rather than built "
               f"into a road another deck already obstructs; the deck that "
               f"placed them must be re-placed with this road known "
               f"(`build_pieces(routes=...)` opens its rails there)."
               if stale else "")]


def landfall_seating_arms() -> list[dict]:
    rows = []
    for name, rise, island, dry_bank, want_refuse, near_bank, margin in (
            ("bank_076m_ramps", 0.76, False, True, False, False, False),
            ("near_bank_076m_ramps", 0.76, False, True, False, True, False),
            ("bank_020m_needs_no_ramp", 0.20, False, True, False, False, False),
            ("dry_island_floor_is_seated", 0.20, True, True, False, False, False),
            ("bank_exceeding_tile_budget_refuses", 3.0, False, True, True, False, False),
            ("ramp_cannot_raise_wet_span_ceiling", 0.76, False, False, True, False, False),
            # S1 bank_a on Pirate68: the margin tiles clip the waterline at a
            # diagonal shore, the walked lane is dry.
            ("margin_tile_clipping_waterline_is_not_wet", 0.76, False, True, False, False, True)):
        h, length = 32.5, 20.0

        def ground(_x, z):
            z = length - z if near_bank else z
            if margin and length < z <= length + 3.0 and abs(_x) >= 1.5:
                return 29.9
            if z > length + 0.25:
                return h + rise
            if z < 2.0 or (dry_bank and z >= 16.0) or z >= length:
                return h
            if island and 9.0 <= z <= 11.0:
                return h + 0.067
            return 29.0

        bed = _fixture_bed(ground, "bounded landfall and dry interior island")
        span = dict(crossing_id=f"FIX-{name}", deck_y_REQUIRED=h,
                    deck_width_m=4.0, legal_deck_y_interval=[h, h],
                    single_H_both_banks=True,
                    water_surface_y=30.0, bank_a=dict(xz=[0.0, 0.0]),
                    bank_b=dict(xz=[0.0, length]))
        report = {}
        try:
            pieces = build_pieces(span, bed, bent_spacing_m=4.0, report=report)
            pieces, _ = trim_buried_overhang(span, pieces, bed, h)
            verdict = asm.verify(pieces, bed)
            guards = run_guards(span, pieces, verdict, bed, h)
            refused = any(guards.values())
        except SystemExit as exc:
            rows.append(dict(name=name, ok=want_refuse, refused=str(exc)))
            continue
        if want_refuse:
            rows.append(dict(name=name, ok=refused, guards=guards))
            continue
        floor_top = asm.facts()["wood_floor"].hi[1]
        centre = sorted((p.z, p.y + floor_top) for p in pieces
                        if p.prefab == "wood_floor" and abs(p.x) < 1e-6)
        bounded = all(abs(bh - ah) <= LAND_TILE_STEP_M + 1e-6 and
                      abs(bh - ah) <= (bz - az) * road_instruments.STEP_LIMIT_PER_M + 1e-6
                      for (az, ah), (bz, bh) in zip(centre, centre[1:]))
        wet_preserved = all(top <= h + 1e-6 for z, top in centre
                            if ground(0.0, z) < 30.0)
        seated_island = (not island or any(
            abs(z - 10.0) < 1e-6 and top >= h + 0.067 - 1e-6
            for z, top in centre))
        ramped = bool(report.get("landfall_ramps"))
        rows.append(dict(
            name=name, ok=(not refused and bounded and wet_preserved
                           and seated_island and ramped == (rise > ABUTMENT_STEP_MAX_M)),
            guards=guards, bounded_steps_and_grade=bounded,
            wet_ceiling_preserved=wet_preserved, island_seated=seated_island,
            ramps=report.get("landfall_ramps", [])))
    return rows


def _neuter_land_seating():
    real = globals()["land_seated_deck_stations"]

    def fixed_datum(span, surface, deck_y, report=None):
        ax, az = span["bank_a"]["xz"]
        bx, bz = span["bank_b"]["xz"]
        return [(t, deck_y) for t in asm._bridge_station_distances(
            math.hypot(bx - ax, bz - az), 1)]

    globals()["land_seated_deck_stations"] = fixed_datum

    def undo():
        globals()["land_seated_deck_stations"] = real
    return undo


def _terminal_fixture(name: str, drop: float, terminal: tuple,
                      mirror: bool = False,
                      h: float = 32.5, length: float = 20.0):
    """A 20 m crossing along +z at deck `h`, built here with no world: the near
    bank (z<2) is a dry bench at the deck's level, the middle is water, and
    the far shore (z>=16) lies `drop` m BELOW the deck for as far as anyone
    can walk -- the ground beyond a road that ended inside the bridged run.
    `terminal` is what the network records as `terminal_banks`; `mirror`
    swaps which bank is the shore, so the same ground is `bank_a`'s."""
    def ground(_x, z):
        if z < 2.0:
            return h
        return 29.0 if z < 16.0 else h - drop
    bed = _fixture_bed(ground, f"terminal landing, shore {drop:g} m below")
    near, far = ([0.0, 0.0], [0.0, length])
    span = dict(crossing_id=f"FIX-{name}", deck_y_REQUIRED=h,
                deck_width_m=4.0, legal_deck_y_interval=[h, h],
                single_H_both_banks=True, water_surface_y=30.0,
                bank_a=dict(xz=far if mirror else near),
                bank_b=dict(xz=near if mirror else far),
                terminal_banks=list(terminal))
    return span, bed, h


def terminal_landing_arms() -> list[dict]:
    """TERMINAL_LANDING_DEPENDS_ON, each named dependency both ways.

    BANK A     the mirrored shore (`terminal_banks: bank_a`) is landed the same
               way, so the sign of the walk outboard is not one end's habit.

    LANDS      a shore 1.2 m below a terminal bank is met by a ramp that
               descends at <=0.30 m a row and ends within the bank-step limit.
    ROAD'S     the SAME shore below a bank the road is meant to bench is NOT
               landed: the abutment guard refuses it, so the deck never hides
               the road's own refusal.
    SLOPE      ground 8 m below a terminal bank is refused WITH ITS NUMBERS
               (the nearest the deck gets, the limit), never forced.
    ON GROUND  a terminal bank already within the step limit lays no ramp.
    END BENT   a terminal end 0.30 m over its shelf touches no ground and is
               carried by its own bent: the structural verdict is the only
               thing that tells it from the same deck without one.
    """
    out = []
    for name, drop, terminal, want_refused, want_text, mirror in (
            ("a_shore_below_a_terminal_bank_is_landed_by_a_ramp",
             1.2, ("bank_b",), False, None, False),
            ("a_shore_below_a_terminal_bank_a_is_landed_the_same_way",
             1.2, ("bank_a",), False, None, True),
            ("the_same_shore_below_a_road_bank_is_the_roads_to_bench",
             1.2, (), True, None, False),
            ("a_slope_below_a_terminal_bank_is_refused_with_its_numbers",
             8.0, ("bank_b",), True, "(+5.300 m, limit", False),
            ("a_terminal_bank_on_the_ground_lays_no_ramp",
             0.0, ("bank_b",), False, None, False),
            ("a_terminal_end_over_a_shelf_stands_on_its_own_bent",
             0.3, ("bank_b",), False, None, False)):
        span, bed, h = _terminal_fixture(name, drop, terminal, mirror)
        report: dict = {}
        try:
            pieces = build_pieces(span, bed, bent_spacing_m=8.0, report=report)
            pieces, _ = trim_buried_overhang(span, pieces, bed, h)
            verdict = asm.verify(pieces, bed)
            guards = run_guards(span, pieces, verdict, bed, h)
            refused = any(guards.values())
        except SystemExit as exc:
            out.append(dict(name=name, got_refused=True,
                            want_refused=want_refused,
                            ok=(want_refused and (want_text is None
                                                  or want_text in str(exc))),
                            refusal=str(exc)[:160],
                            why=f"refused as required but without "
                                f"{want_text!r} in its words"))
            continue
        ramps = report.get("landfall_ramps", [])
        floor_top = asm.facts()["wood_floor"].hi[1]
        centre = sorted((p.z, p.y + floor_top) for p in pieces
                        if p.prefab == "wood_floor" and abs(p.x) < 1e-6)
        bounded = all(
            abs(bh - ah) <= LAND_TILE_STEP_M + 1e-6
            and abs(bh - ah) <= (bz - az) * road_instruments.STEP_LIMIT_PER_M + 1e-6
            for (az, ah), (bz, bh) in zip(centre, centre[1:]))
        want_ramp = (not want_refused and bool(terminal)
                     and drop > ABUTMENT_STEP_MAX_M)
        ok = (refused == want_refused
              and (want_refused or (bounded and bool(ramps) == want_ramp)))
        out.append(dict(name=name, got_refused=refused,
                        want_refused=want_refused, ok=ok,
                        ramps=[(r["direction"], r["outboard_tiles"],
                                r["final_step_m"]) for r in ramps],
                        bounded_steps_and_grade=bounded,
                        why=f"guards {sorted(k for k, v in guards.items() if v)}"
                            f", ramped={bool(ramps)} (wanted {want_ramp}), "
                            f"bounded={bounded}"))
    return out


def terminal_descent_arms() -> list[dict]:
    """The deck gives its height back INSIDE the span too (TERMINAL_LANDING_
    DEPENDS_ON), on a 34.0 m deck whose far shelf lies `drop` m under it --
    T5-temple-dockshore-x01's shape, a road that arrives above the clearance.

    DRY SHELF  3.8 m under the deck, past a landing alone (3.05 m): comes
               down in the span, over the dry shelf rows, and lands.
    WET SHELF  4.2 m under it, the shelf under the plane: the rows over water
               stop at the clearance floor (32.5 m), the rest is the landing.
    LANDING    1.2 m under it: a landing alone meets it, nothing is lowered
               in the span.
    CLIFF      12 m under it: refused with the nearest the deck gets.
    """
    out = []
    clear = 30.0 + routemod.DECK_CLEARANCE_M
    for name, drop, want_refused, want_text in (
            ("a_road_above_the_clearance_comes_down_inside_the_span",
             3.8, False, None),
            ("over_water_the_descent_stops_at_the_clearance_floor",
             4.2, False, None),
            ("a_shore_a_landing_alone_meets_lowers_nothing_in_the_span",
             1.2, False, None),
            ("a_cliff_past_the_spans_descent_too_is_refused_with_numbers",
             12.0, True, "the nearest it gets is")):
        span, bed, h = _terminal_fixture(name, drop, ("bank_b",), False, 34.0)
        report: dict = {}
        try:
            pieces = build_pieces(span, bed, bent_spacing_m=4.0, report=report)
            pieces, _ = trim_buried_overhang(span, pieces, bed, h)
            verdict = asm.verify(pieces, bed)
            guards = run_guards(span, pieces, verdict, bed, h)
            refused = any(guards.values())
        except SystemExit as exc:
            out.append(dict(name=name, got_refused=True,
                            want_refused=want_refused,
                            ok=(want_refused and (want_text is None
                                                  or want_text in str(exc))),
                            refusal=str(exc)[:160],
                            why=f"refused as required but without "
                                f"{want_text!r} in its words"))
            continue
        ramps = report.get("landfall_ramps", [])
        in_span = ramps[0]["span_tiles"] if ramps else None
        floor_top = asm.facts()["wood_floor"].hi[1]
        over_water = [p.y + floor_top for p in pieces
                      if p.prefab == "wood_floor" and abs(p.x) < 1e-6
                      and p.z <= 20.0 and bed(p.x, p.z) < 30.0]
        kept_floor = all(top >= clear - 1e-6 for top in over_water)
        ok = (refused == want_refused and kept_floor
              and (want_refused or (
                  (in_span or 0) > 0
                  if (name.startswith(("a_road_above", "over_water")))
                  else in_span == 0)))
        out.append(dict(name=name, got_refused=refused,
                        want_refused=want_refused, ok=ok,
                        span_rows=in_span,
                        landing_rows=ramps[0]["outboard_tiles"] if ramps else None,
                        lowest_row_over_water=(round(min(over_water), 3)
                                               if over_water else None),
                        why=f"guards {sorted(k for k, v in guards.items() if v)}"
                            f", span rows {in_span}, floor kept "
                            f"{kept_floor}"))
    return out


def deck_overlap_arms() -> list[dict]:
    """DECK_OVERLAP_DEPENDS_ON, constructed: a 20 m deck at 32.5 m along +z,
    and a second one across it along +x.

    THROUGH    the second at 36.0 m stands its piles through the first's
               floors (3.5 m apart -- not one deck): REFUSED, with the pair.
    BESIDE     the second 6 m clear of the first: nothing in common, not
               refused.
    """
    out = []
    for name, deck2, z2, want_refused in (
            ("a_deck_through_a_standing_deck_at_another_height_is_refused",
             36.0, 11.0, True),
            ("a_deck_clear_of_a_standing_deck_is_not_refused",
             32.5, 28.0, False)):
        def ground(x, z, deck2=deck2):
            g = 29.0
            if z < 2.0 or z > 18.0:
                g = 32.5
            return max(g, deck2) if abs(x) >= 8.0 else g
        bed = _fixture_bed(ground, "two decks")
        first = dict(crossing_id="FIX-first", deck_y_REQUIRED=32.5,
                     deck_width_m=4.0, legal_deck_y_interval=[32.5, 32.5],
                     single_H_both_banks=True, water_surface_y=30.0,
                     bank_a=dict(xz=[0.0, 0.0]), bank_b=dict(xz=[0.0, 20.0]))
        second = dict(first, crossing_id="FIX-second", deck_y_REQUIRED=deck2,
                      legal_deck_y_interval=[deck2, deck2],
                      bank_a=dict(xz=[-10.0, z2]), bank_b=dict(xz=[10.0, z2]))
        standing = tuple(build_pieces(first, bed, bent_spacing_m=4.0))
        pieces = build_pieces(second, bed, bent_spacing_m=4.0,
                              existing=standing)
        got = deck_overlap_guard(second, pieces, existing=standing)
        out.append(dict(name=name, got_refused=bool(got),
                        want_refused=want_refused,
                        ok=bool(got) == want_refused,
                        refusal=(got[0][:140] if got else None),
                        why=f"overlap guard said {bool(got)}"))
    return out


def terminal_remedy_arms() -> list[dict]:
    """The builder's OWN classification of a terminal bank
    (TERMINAL_LANDING_DEPENDS_ON), constructed on the reach fixture whose bank_b
    IS its segment's last station -- 0 m of road outboard, the shape the gate
    filed as 1C_UNSERVED for T9, T13 and T2 while the deck could have landed
    two of them.

    WITHIN    a terminal bank whose ground is within a landing's reach is NOT
              refused: the deck lands itself.
    BEYOND    one past that reach stays 1C_UNSERVED, and says why in numbers.
    ROAD'S    the SAME step at a bank that is not flagged terminal stays
              UNSERVED: the bench is the road's.
    """
    fx = _remedy_fixtures()
    road = fx["records"][BANK_REACH_SHORT]
    terminal = dict(road, crossing_id="FIX-terminal-x01",
                    terminal_banks=["bank_b"])
    out = []
    for name, rec, step, want_refused, want_text in (
            ("a_terminal_bank_within_a_landings_reach_is_not_refused",
             terminal, 1.5, False, None),
            ("a_terminal_bank_beyond_a_landings_reach_is_UNSERVED_with_numbers",
             terminal, TERMINAL_LANDING_REACH_M + 1.0, True,
             "the deck lands itself"),
            ("the_same_step_at_a_road_bank_is_UNSERVED",
             road, 1.5, True, None)):
        ans = deck_remedy(rec, terrain_written=None,
                          abutment_steps={"bank_b": step},
                          segments=fx["segments"])
        hit = [r for r in ans["refused_by"] if r["precondition"] == "abutment"]
        refused = bool(hit)
        out.append(dict(
            name=name, got_refused=refused, want_refused=want_refused,
            step_m=round(step, 3), class_=ans["implies"],
            ok=(refused == want_refused
                and (not refused or (ans["implies"] == "1C_UNSERVED"
                                     and (want_text is None
                                          or want_text in hit[0]["why"])))),
            why=f"refused={refused}, class {ans['implies']}, wanted "
                f"{want_refused} with {want_text!r}"))
    return out


def _neuter_terminal_landing():
    """RESTORE THE STATE THAT BUILT NOTHING: a terminal bank is refused with
    the terminus as owner, and the deck never lands itself -- T9, T13 and T2
    of the Ulfsland regen, 136.6 m of deck, never built."""
    global _TERMINAL_LANDING_MODE
    was, _TERMINAL_LANDING_MODE = _TERMINAL_LANDING_MODE, "refuses"

    def undo():
        global _TERMINAL_LANDING_MODE
        _TERMINAL_LANDING_MODE = was
    return undo


def _neuter_terminal_descent():
    """RESTORE THE STATE THAT REFUSED T5-x01: a landing alone, from the
    datum, and nothing given back inside the span."""
    global _TERMINAL_DESCENT_MODE
    was, _TERMINAL_DESCENT_MODE = _TERMINAL_DESCENT_MODE, "landing"

    def undo():
        global _TERMINAL_DESCENT_MODE
        _TERMINAL_DESCENT_MODE = was
    return undo


def _neuter_terminal_end_bent():
    """RESTORE THE STATE THAT REFUSED THE END TILES: no bent of its own under
    a terminal bank's outermost row -- support 17.90 and 19.76 against the
    20.00 the safety factor needs, on T9-x03 and T13-x01."""
    global _TERMINAL_END_BENT_MODE
    was, _TERMINAL_END_BENT_MODE = _TERMINAL_END_BENT_MODE, "none"

    def undo():
        global _TERMINAL_END_BENT_MODE
        _TERMINAL_END_BENT_MODE = was
    return undo



def route_carriageway_arms() -> list[dict]:
    """Junction openings, skewed solid footprints, and grade separation."""
    out = []
    for name, yaw, route_nodes, road_y in (
            ("transverse_road_opens_posts_and_top_rails", 0.0,
             [(-12.0, 10.0), (12.0, 10.0)], 32.5),
            ("skewed_road_uses_rotated_footprints", 22.5,
             [(-12.0, 10.0), (12.0, 10.0)], 32.5),
            ("neighbour_height_makes_top_beams_obstructions", 22.5,
             [(4.5, 3.0), (4.5, 17.0)], 32.7),
            ("road_below_bridge_keeps_its_rails", 0.0,
             [(-12.0, 10.0), (12.0, 10.0)], 20.0)):
        a = math.radians(yaw)
        ca, sa = math.cos(a), math.sin(a)

        def world(x, z):
            return [x * ca + z * sa, -x * sa + z * ca]

        def ground(x, z):
            if road_y == 20.0:
                return road_y
            lx, lz = x * ca - z * sa, x * sa + z * ca
            return 29.0 if abs(lx) < 4.0 and 1.0 < lz < 19.0 else road_y

        span = dict(crossing_id="FIX-road-junction", deck_y_REQUIRED=32.5,
                    deck_width_m=6.0, bank_a=dict(xz=world(0.0, 0.0)),
                    bank_b=dict(xz=world(0.0, 20.0)))
        bed = _fixture_bed(ground, "channel with benched banks")
        routes = (dict(seg="FIX-intersecting-road", half_width_m=2.0,
                       nodes=[world(x, z) for x, z in route_nodes]),)
        original = build_pieces(span, bed)
        before = route_obstructions(original, bed, routes)
        fixed = build_pieces(span, bed, routes=routes)
        after = route_obstructions(fixed, bed, routes)
        structural = lambda ps: [p for p in ps
                                 if not p.role.endswith(("/rail", "/rail_post"))]
        grade_separated = road_y == 20.0
        ok = (not after and structural(original) == structural(fixed)
              and not span_coverage_guard(span, fixed)
              and (fixed == original if grade_separated else bool(before)))
        if not grade_separated:
            # An opening is not blanket deletion of bridge furniture.
            ok = ok and any(p.role.endswith("/rail") for p in fixed)
        if road_y == 32.7:
            ok = ok and any(original[i].role.endswith("/rail") for i in before)
        out.append(dict(name=name, ok=ok, before=len(before), after=len(after),
                        deck_coverage_pass=not span_coverage_guard(span, fixed),
                        supports_unchanged=structural(original) == structural(fixed)))
    # The guard must still refuse an actual support in the travelled way.
    intruder = asm.Placed("wood_pole", 0.0, 33.0, 10.0, 0.0,
                          "FIX-road-junction/bent/pile")
    routes = (dict(seg="FIX-support-collision", half_width_m=2.0,
                   nodes=[[-10.0, 10.0], [10.0, 10.0]]),)
    refused = route_carriageway_guard(
        span, [intruder], _fixture_bed(lambda x, z: 32.5, "road"), routes)
    out.append(dict(name="load_bearing_obstruction_is_still_refused",
                    ok=bool(refused), refusal=refused))
    out.extend(shared_deck_arms())
    return out



def shared_deck_arms() -> list[dict]:
    """Parallel piled decks reuse only real, height-compatible coverage."""
    bed = _fixture_bed(lambda x, z: 29.0 if 1.0 < z < 19.0 else 32.5,
                       "parallel channels with benched ends")
    base = dict(crossing_id="FIX-existing", deck_y_REQUIRED=32.5,
                deck_width_m=6.0, legal_deck_y_interval=[32.0, 35.0],
                bank_a=dict(xz=[0.0, 0.0]), bank_b=dict(xz=[0.0, 20.0]))
    # An existing open-sided piled platform. Its floors and every support are
    # real generated solids; none is changed or assumed terrain-anchored.
    existing = tuple(p for p in build_pieces(base, bed)
                     if not p.role.endswith(("/rail", "/rail_post")))
    before = tuple(asm.command(p) for p in existing)
    rows = []
    for offset, planned in ((2.0, 32.5), (3.5, 32.603), (5.0, 32.4),
                            (3.5, 33.5)):
        span = dict(base, crossing_id="FIX-parallel",
                    deck_y_REQUIRED=planned, bank_a=dict(xz=[offset, 0.0]),
                    bank_b=dict(xz=[offset, 20.0]))
        pieces = build_pieces(span, bed, existing=existing)
        compatible = abs(planned - 32.5) <= ABUTMENT_STEP_MAX_M
        # This interior tile's complete square lies over the existing deck;
        # the far outer tile does not. Neither expectation uses the subtractor.
        interior = any(p.prefab == "wood_floor" and
                       abs(p.x - (offset - 3.0)) < 1e-6 and abs(p.z - 10.0) < 1e-6
                       for p in pieces)
        outer = any(p.prefab == "wood_floor" and
                    abs(p.x - (offset + 3.0)) < 1e-6 and abs(p.z - 10.0) < 1e-6
                    for p in pieces)
        top = asm.facts()["wood_floor"].hi[1]
        floors_at_height = all(
            abs(p.y + top - (32.5 if compatible else planned)) < 1e-6
            for p in pieces if p.prefab == "wood_floor")
        covered = not span_coverage_guard(span, pieces, existing=existing)
        structural = asm.verify([*pieces, *existing], bed)["ok"]
        preserved = before == tuple(asm.command(p) for p in existing)
        # The incompatible slab sits 1m above both banks and its old carrier.
        # It must retain its own floors AND fail support, not borrow support
        # or claim an old floor at the wrong height as its walking surface.
        rows.append(dict(
            name=f"parallel_{offset:g}m_planned_{planned:g}",
            ok=(interior != compatible and outer and floors_at_height
                and covered and structural == compatible and preserved),
            duplicate_interior_floor=interior, uncovered_outer_floor_retained=outer,
            floor_heights_match=floors_at_height, coverage=covered,
            structural=structural, structural_required=compatible,
            existing_unchanged=preserved))
    # A 1mm uncovered edge is still water, not a fully covered floor. This
    # survives both neuters and prevents a pivot/centre-only overlap shortcut.
    floor_y = 32.5 - asm.facts()["wood_floor"].hi[1]
    tile = asm.Placed("wood_floor", 0.0, floor_y, 0.0, 0.0, "FIX/deck")
    shifted = asm.Placed("wood_floor", 0.001, floor_y, 0.0, 0.0, "OLD/deck")
    kept, omitted = omit_shared_deck_pieces([tile], (shifted,))
    rows.append(dict(name="uncovered_strip_is_not_existing_deck",
                     ok=kept == [tile] and not omitted))
    return rows


def _neuter_shared_deck_reuse():
    real = globals()["omit_shared_deck_pieces"]
    globals()["omit_shared_deck_pieces"] = lambda pieces, existing: (pieces, [])

    def undo():
        globals()["omit_shared_deck_pieces"] = real
    return undo


def _neuter_shared_deck_datum():
    real = globals()["shared_deck_datum"]
    globals()["shared_deck_datum"] = lambda span, existing: float(span["deck_y_REQUIRED"])

    def undo():
        globals()["shared_deck_datum"] = real
    return undo


def carriageway_guard_arms() -> list[dict]:
    """Two constructed arms over one 20 m deck on a skewed bearing.

    RAILS    the deck this builder emits: its rails must stand off the
             carriageway.  Only the deck's WIDTH can flip it.
    POST     the same deck plus one post on its centreline: refused.  Only
             the GUARD can flip it; it keeps a width fix from passing by
             switching the question off.
    """
    deck_y = 32.5
    span = {"crossing_id": "FIX-carriageway-x01", "bank_a": {"xz": [0.0, 0.0]},
            "bank_b": {"xz": [12.0, 16.0]}, "deck_y_REQUIRED": deck_y,
            "deck_width_m": 6.0}
    bed = _fixture_bed(lambda x, z: 29.0, "a flat channel bed 3.5 m under "
                       "the deck")
    pieces = build_pieces(span, bed)
    post = asm.Placed("wood_pole", 6.0, deck_y + 0.5, 8.0, bearing_of(span),
                      "FIX-carriageway-x01/intruder")
    out = []
    for name, ps, want in (
            ("a_deck_keeps_its_rails_off_the_carriageway", pieces, False),
            ("a_post_standing_on_the_carriageway_is_refused",
             pieces + [post], True)):
        got = carriageway_guard(span, ps, deck_y)
        rails = [_lateral_extent(span, p) for p in ps
                 if p.role.endswith("/rail_post")]
        out.append(dict(name=name, want_refused=want, got_refused=bool(got),
                        ok=(bool(got) == want), deck_tiles=deck_tiles(span),
                        rail_posts=len(rails),
                        rail_inner_edge_m=round(min(min(abs(a), abs(b))
                                                    for a, b in rails), 3),
                        carriageway_half_m=span["deck_width_m"] / 2.0,
                        why=(got[0] if got else "nothing refused")))
    return out


def cross_section_arms() -> list[dict]:
    """The drive-10 defect, constructed: a 0.4 m post standing in a road
    BETWEEN two of the gate's 1 m cross-sections of it (CROSS_SECTION_DEPENDS_ON).

    The lane runs along +x at z=10 on a flat 32.5 m bench, half-width 2.0 m,
    its stations at x = k + 0.5.  A wood_pole at x=4.0 spans 3.8..4.2: it
    reaches neither the station at 3.5 nor the one at 4.5.  Every row but the
    "beside" and "undecked" ones is refused by the gate's own station
    spacing -- only the SPACING can flip it.
    """
    bed = _fixture_bed(lambda x, z: 32.5, "a flat 32.5 m road bench")
    lane = dict(seg="FIX-lane", half_width_m=2.0,
                nodes=[[-11.5, 10.0], [12.5, 10.0]])
    routes = (lane,)
    span = {"crossing_id": "FIX-shared-x01"}

    def post(x, z, y=33.0, role="FIX-body/rail_post"):
        return asm.Placed("wood_pole", x, y, z, 0.0, role)

    global _ROAD_GEOMETRY
    if _ROAD_GEOMETRY is None:
        _ROAD_GEOMETRY, _ = road_instruments.load_geometry()
    parts = _ROAD_GEOMETRY["wood_pole"]
    hull = road_instruments.Solid("wood_pole", (0.0, 33.0, 0.0),
                                  (0.0, 0.0, 0.0, 1.0), parts, True).hull()
    reach = max(x for x, _z in hull)
    # drive 10's number: the post's hull ends 0.0001 m short of the cross-section
    knife = post(4.5 - 1e-4 - reach, 10.72)

    out = []

    def row(name, want, got, why, **numbers):
        out.append(dict(name=name, want_refused=want, got_refused=got,
                        ok=(want == got), why=why, **numbers))

    for name, piece in (
            ("a_post_between_two_cross_sections_is_in_the_carriageway",
             post(4.0, 10.72)),
            ("a_post_a_tenth_of_a_millimetre_clear_of_a_cross_section_is_seen",
             knife)):
        got = route_obstructions([piece], bed, routes)
        row(name, True, bool(got), f"blocked {got}",
            lateral_m=round(piece.z - 10.0, 3))
    # An EXISTING post cannot be removed: the span that would be built into
    # its road is refused, with the numbers.
    refusal = route_carriageway_guard(span, [], bed, routes,
                                      existing=(post(4.0, 10.72, role=
                                                     "existing/FIX-body/rail_post"),))
    row("an_existing_post_between_cross_sections_refuses_the_span", True,
        bool(refusal) and "existing" in refusal[0] and "0.72 m" in refusal[0],
        refusal[0] if refusal else "nothing refused")
    row("a_post_beside_the_carriageway_between_cross_sections_passes", False,
        bool(route_obstructions([post(4.0, 12.6)], bed, (lane,))),
        "its hull ends 0.4 m outside the 2.0 m half-width")
    # THE DECK BUILDER'S OWN RAILS.  Independent of the sampler: the verdict
    # reads each emitted rail's hull against the road band, not the
    # builder's own cross-sections.
    deck = dict(crossing_id="FIX-road-junction", deck_y_REQUIRED=32.5,
                deck_width_m=6.0, bank_a=dict(xz=[0.0, 0.0]),
                bank_b=dict(xz=[0.0, 20.0]))
    channel = _fixture_bed(
        lambda x, z: 29.0 if abs(x) < 4.0 and 1.0 < z < 19.0 else 32.5,
        "channel with benched banks")
    pieces = build_pieces(deck, channel, routes=routes)
    inside = []
    for p in pieces:
        if not p.role.endswith(("/rail", "/rail_post")):
            continue
        a = math.radians(p.yaw) / 2.0
        h = road_instruments.Solid(
            p.prefab, (p.x, p.y, p.z), (0.0, math.sin(a), 0.0, math.cos(a)),
            _ROAD_GEOMETRY[p.prefab], True).hull()
        if (min(z for _x, z in h) < 12.0 - 1e-6
                and max(z for _x, z in h) > 8.0 + 1e-6
                and max(x for x, _z in h) > -11.5
                and min(x for x, _z in h) < 12.5):
            inside.append((p.prefab, round(p.x, 2), round(p.z, 2)))
    row("a_deck_opens_every_rail_standing_in_a_road_between_cross_sections",
        False, bool(inside),
        f"rails left in the carriageway: {inside}" if inside
        else "no rail hull reaches the carriageway",
        rails_emitted=sum(1 for p in pieces
                          if p.role.endswith(("/rail", "/rail_post"))))
    # AN UNDECKED CROSSING'S DECK IS NOT ITS BED.  The road is written at
    # 32.6 m; between x=-5 and x=5 there is open water 3.6 m under it.
    water = _fixture_bed(lambda x, z: 29.0 if abs(x) < 5.0 else 32.6,
                         "a 10 m channel with no deck over it yet")
    gap = dict(seg="FIX-undecked", half_width_m=2.0,
               nodes=[[-12.0, 10.0], [-5.0, 10.0], [5.0, 10.0], [12.0, 10.0]],
               levels=[32.6, 32.6, 32.6, 32.6])
    pile = asm.Placed("wood_pole_log_4", 0.0, 30.37, 10.5, 0.0,
                      "FIX-body/bent/pile")
    row("a_pile_under_an_undecked_crossing_is_not_in_its_road", False,
        bool(route_obstructions([pile], water, (gap,))),
        "its top (32.37 m) is under the 32.6 m deck the road will have")
    row("a_rail_post_over_an_undecked_crossing_is_in_its_road", True,
        bool(route_obstructions([post(0.0, 10.5)], water, (gap,))),
        "rises 0.9 m out of the 32.6 m deck the road will have")
    return out


def _neuter_deck_as_wide_as_the_carriageway():
    """RESTORE THE SHIPPED WIDTH: the deck exactly as wide as the carriageway,
    so its rails stand 0.2 m inside it (drive 3: 36 of 36 on T1-x01)."""
    global _DECK_WIDTH_OF
    was, _DECK_WIDTH_OF = _DECK_WIDTH_OF, "carriageway"

    def undo():
        global _DECK_WIDTH_OF
        _DECK_WIDTH_OF = was
    return undo


def _neuter_cross_sections_at_stations():
    """RESTORE THE STATE THAT SHIPPED DRIVE 10: the gate's own 1 m
    cross-section lines and nothing between them."""
    global _CROSS_SECTIONS
    was, _CROSS_SECTIONS = _CROSS_SECTIONS, "stations"

    def undo():
        global _CROSS_SECTIONS
        _CROSS_SECTIONS = was
    return undo


def _neuter_crossing_judged_on_its_bed():
    """A gap in a road judged against the bed under it, not the height the
    road was written to."""
    global _CROSSING_SURFACE
    was, _CROSSING_SURFACE = _CROSSING_SURFACE, "bed"

    def undo():
        global _CROSSING_SURFACE
        _CROSSING_SURFACE = was
    return undo


def _neuter_carriageway_guard_absent():
    """RESTORE THE STATE THAT SHIPPED: no builder asked the gate's
    obstruction question, so a deck went out and the gate found its posts."""
    real = globals()["carriageway_guard"]

    def absent(span, pieces, deck_y):
        return []

    globals()["carriageway_guard"] = absent

    def undo():
        globals()["carriageway_guard"] = real
    return undo


def overhang_trim_arms() -> list[dict]:
    """Three constructed arms over one 10 m deck, pieces generated on a clean
    bench and then judged, trim first and burial guard second, against a
    surface that rises 0.065 m over one station's tiles -- S13-x01's number.

    OVERHANG  the rise is under the station BEYOND bank_a: the trim must drop
              it and the guard then passes.  Only the trim's READING can flip
              it.
    INSIDE    the same rise under a station INSIDE the crossing: nothing is
              trimmed and the guard refuses.  The trim may never shorten the
              crossing.
    BENCH     no rise: nothing is dropped and nothing refused.
    """
    deck_y = 32.5
    span = {"crossing_id": "FIX-overhang-x01", "bank_a": {"xz": [0.0, 0.0]},
            "bank_b": {"xz": [0.0, 10.0]}, "deck_y_REQUIRED": deck_y,
            "deck_width_m": 6.0}
    bench = deck_y - 0.13

    def ground(rise_lo: float | None, rise_hi: float | None):
        def f(x, z):
            if rise_lo is not None and rise_lo <= z <= rise_hi:
                return deck_y + 0.065
            return 29.0 if 0.0 < z < 10.0 else bench
        return f

    pieces = build_pieces(span, _fixture_bed(ground(None, None),
                                             "bench at both banks"))
    out = []
    for name, lo, hi, want in (
            ("an_overhang_the_gate_calls_buried_is_trimmed", -3.5, -1.5,
             False),
            ("a_station_inside_the_crossing_is_never_trimmed", 4.5, 6.5,
             True),
            ("an_overhang_on_its_bench_is_kept", None, None, False)):
        bed = _fixture_bed(ground(lo, hi), f"rise over z {lo}..{hi}")
        kept, dropped = trim_buried_overhang(span, pieces, bed, deck_y)
        got = burial_guard(span, kept, bed)
        out.append(dict(name=name, want_refused=want, got_refused=bool(got),
                        ok=(bool(got) == want) and (bool(dropped) ==
                                                    (lo is not None and lo < 0)),
                        stations_dropped=len(dropped),
                        pieces_dropped=len(pieces) - len(kept),
                        why=(got[0] if got else "nothing refused")))
    return out


def _neuter_overhang_trim_keeps_its_own_slack():
    """RESTORE THE SHIPPED TRIM: its own pivot reading at its own 0.30 m, so
    a tile the gate calls buried at 0.065 m survives the trim."""
    global _OVERHANG_TRIM_ASKS
    was, _OVERHANG_TRIM_ASKS = _OVERHANG_TRIM_ASKS, "own_slack"

    def undo():
        global _OVERHANG_TRIM_ASKS
        _OVERHANG_TRIM_ASKS = was
    return undo


def tile_seam_arms() -> list[dict]:
    """Two arms over the 4-row deck `build_pieces` lays for
    T13-temple-harboursouth-x01 on the drive-3 re-emit (banks
    (-10.11,-251.18) -> (-4.1,-254.8), yaw 121.06), on a flat applied bed.

    SEAM     bank_a itself, on the seam of the two centre rows: carried.
             Only the seam tolerance can flip it.
    BEYOND   a point 1.5 m past the deck's outer edge: NOT carried, so the
             tolerance is not "cover everything".
    """
    span = {"crossing_id": "FIX-seam-x01", "bank_a": {"xz": [-10.11, -251.18]},
            "bank_b": {"xz": [-4.1, -254.8]}, "deck_y_REQUIRED": 32.502,
            "deck_width_m": 6.0}
    pieces = build_pieces(span, _fixture_bed(lambda x, z: 20.0, "flat"))
    tiles = [p for p in pieces if p.role.endswith("/deck")]
    (ax, az), (bx, bz) = span["bank_a"]["xz"], span["bank_b"]["xz"]
    L = math.hypot(bx - ax, bz - az)
    lx, lz = (bz - az) / L, -(bx - ax) / L
    out = []
    for name, px, pz, want in (
            ("a_point_on_the_seam_of_two_tiles_is_carried", ax, az, True),
            ("a_point_beyond_the_deck_edge_is_not_carried",
             ax + lx * 5.5, az + lz * 5.5, False)):
        got = any(tile_covers(p, px, pz) for p in tiles)
        out.append(dict(name=name, want_refused=not want, got_refused=not got,
                        ok=got == want, deck_tiles=len(tiles),
                        why=f"carried={got} at ({px:.3f},{pz:.3f})"))
    return out


def _neuter_tile_covers_to_the_exact_half():
    """RESTORE THE SHIPPED TEST: |along|, |lat| <= 1.000 exactly, so a seam
    reads as a gap by floating point (drive-3 re-emit, T13-x01 bank_a)."""
    real = globals()["tile_covers"]

    def exact(p, px: float, pz: float, half: float = TILE_HALF_M) -> bool:
        along, lat = tile_frame(p, px, pz)
        return abs(along) <= half and abs(lat) <= half

    globals()["tile_covers"] = exact

    def undo():
        globals()["tile_covers"] = real
    return undo


# DECK_OVERLAP_DEPENDS_ON: TWO DECKS IN ONE PLACE ARE ONE DECK AT ONE HEIGHT, OR
# A REFUSAL.
#
# A terminal landing runs a deck out past its bank, onto ground several roads
# can converge on: T5-temple-dockshore-x01's landing and T9-wttown-dockshore-x03's
# both head for the early-dock shore.  `omit_shared_deck_pieces` joins a standing
# floor at a compatible height (within one bank step), and nothing judged the
# rest: a floor at another height in the same plan, or a pole standing through
# someone's deck, was emitted and left for the gate's duplicate/obstruction
# census.  This builder never moves or removes a standing piece, so the
# honest answer is the refusal with the numbers.
#   every emitted piece's own rotated footprint and height extent
#   (assemble.facts), against every piece of a deck that already stands near
#   it (`existing_bridges`, widened by a landing's reach for a terminal bank);
#   two floors within ABUTMENT_STEP_MAX_M of each other are ONE deck, the
#   join `omit_shared_deck_pieces` already makes, and are not judged here.
DECK_OVERLAP_DEPENDS_ON = (
    "every emitted piece's own rotated footprint and height extent",
    "every piece of a deck already standing within a landing's reach "
    "(existing_bridges)",
    "the bank step, below which two floors are one deck "
    "(ABUTMENT_STEP_MAX_M)")
DECK_OVERLAP_MIN_Y_M = 0.10      # vertical interpenetration that is a collision
DECK_OVERLAP_MIN_AREA_M2 = 0.01  # plan overlap that is a collision (10 cm x 10 cm)
# Swapped out by the selftest neuter: "refuses" (shipped) | "off" (the state
# that emitted a deck through another).
_DECK_OVERLAP_MODE = "refuses"


def _plan_polygon(p) -> list:
    """The piece's own footprint in the plan, rotated as `Placed` places it."""
    f = asm.facts()[p.prefab]
    a = math.radians(p.yaw)
    ca, sa = math.cos(a), math.sin(a)
    return [(p.x + x * ca + z * sa, p.z - x * sa + z * ca)
            for x, z in ((f.lo[0], f.lo[2]), (f.hi[0], f.lo[2]),
                         (f.hi[0], f.hi[2]), (f.lo[0], f.hi[2]))]


def _polygon_area(poly: list) -> float:
    return abs(sum(a[0] * b[1] - b[0] * a[1]
                   for a, b in zip(poly, poly[1:] + poly[:1]))) / 2.0


def _plan_overlap_m2(a: list, b: list) -> float:
    """Area common to two convex plan footprints (Sutherland-Hodgman, one
    half-plane of `b` at a time)."""
    sign = 1.0 if sum(p[0] * q[1] - q[0] * p[1]
                      for p, q in zip(b, b[1:] + b[:1])) > 0.0 else -1.0
    inside = a
    for p, q in zip(b, b[1:] + b[:1]):
        ex, ez = q[0] - p[0], q[1] - p[1]
        inside = _clip_polygon(
            inside, lambda pt, p=p, ex=ex, ez=ez:
            -sign * (ex * (pt[1] - p[1]) - ez * (pt[0] - p[0])), True)
        if len(inside) < 3:
            return 0.0
    return _polygon_area(inside)


def deck_overlap_guard(span: dict, pieces: list, *,
                       existing: tuple = ()) -> list[str]:
    """No emitted piece stands inside the solid of a deck that already stands
    (DECK_OVERLAP_DEPENDS_ON); two floors one bank step apart are one deck."""
    if _DECK_OVERLAP_MODE != "refuses" or not existing or not pieces:
        return []
    f = asm.facts()
    floor_top = f["wood_floor"].hi[1]
    old = [(q, _plan_polygon(q), q.y + f[q.prefab].lo[1],
            q.y + f[q.prefab].hi[1]) for q in existing if q.prefab in f]
    hits = []
    for p in pieces:
        if p.prefab not in f:
            continue
        lo_y, hi_y = p.y + f[p.prefab].lo[1], p.y + f[p.prefab].hi[1]
        poly = _plan_polygon(p)
        for q, qpoly, qlo, qhi in old:
            if math.hypot(p.x - q.x, p.z - q.z) > 4.0:
                continue
            vert = min(hi_y, qhi) - max(lo_y, qlo)
            if vert <= DECK_OVERLAP_MIN_Y_M:
                continue
            if (p.prefab == "wood_floor" and q.prefab == "wood_floor"
                    and abs(p.y - q.y) <= ABUTMENT_STEP_MAX_M):
                continue                # one deck, joined at a compatible height
            area = _plan_overlap_m2(poly, qpoly)
            if area > DECK_OVERLAP_MIN_AREA_M2:
                hits.append((area * vert, p, q, vert, area))
    if not hits:
        return []
    hits.sort(key=lambda r: -r[0])
    _v, p, q, vert, area = hits[0]
    return [f"{span['crossing_id']}: {len(hits)} emitted piece-pair(s) stand "
            f"in the same cubic metres as a deck that already stands, worst "
            f"{p.prefab} ({p.role}) at ({p.x:.2f},{p.y:.3f},{p.z:.2f}) "
            f"through {q.prefab} ({q.role}) at ({q.x:.2f},{q.y:.3f},"
            f"{q.z:.2f}): {vert:.3f} m of height over {area:.2f} m2 of plan "
            f"(floors within {ABUTMENT_STEP_MAX_M} m of each other would be "
            f"one deck; these are "
            f"{abs(p.y - q.y):.3f} m apart). This builder never moves or "
            f"removes a piece that stands, so two decks converging on one "
            f"landing are REFUSED here with the numbers; the second must be "
            f"re-planned onto the first's height or its own ground."]


def _neuter_deck_overlap_off():
    """RESTORE THE STATE THAT EMITTED A DECK THROUGH ANOTHER: nothing asks the
    pieces already standing -- T5-x01's landing and T9-x03's, one dock."""
    global _DECK_OVERLAP_MODE
    was, _DECK_OVERLAP_MODE = _DECK_OVERLAP_MODE, "off"

    def undo():
        global _DECK_OVERLAP_MODE
        _DECK_OVERLAP_MODE = was
    return undo


GUARDS = ("deck_datum", "abutment", "seating", "structural", "span_coverage",
          "burial", "carriageway", "deck_overlap")


def run_guards(span: dict, pieces: list, verdict: dict, surface,
               deck_y: float, *, existing: tuple = ()) -> dict[str, list[str]]:
    return {
        "deck_datum": deck_datum_guard(span, deck_y, existing=existing),
        "abutment": abutment_guard(span, pieces, surface, deck_y),
        "seating": seating_guard(span, pieces, surface),
        "structural": structural_guard(verdict),
        "span_coverage": span_coverage_guard(span, pieces, existing=existing),
        "burial": burial_guard(span, pieces, surface),
        "carriageway": carriageway_guard(span, pieces, deck_y),
        "deck_overlap": deck_overlap_guard(span, pieces, existing=existing),
    }


# --------------------------------------------------------------------------
# Measurement
# --------------------------------------------------------------------------

def measure(span: dict, pieces: list, verdict: dict, surface,
            deck_y: float) -> dict:
    """Every number the report quotes, with the population it was taken over."""
    prefabs: dict[str, int] = {}
    for p in pieces:
        prefabs[p.prefab] = prefabs.get(p.prefab, 0) + 1

    decks = [p for p in pieces if p.role.endswith("/deck")]
    piles = [p for p in pieces if p.role.endswith("/pile")]
    f_floor = asm.facts()["wood_floor"]

    # SPLIT BY POPULATION, because the guard is.  `centre` is the travelled
    # way the verdict is about; `edge` is the outer tile row, which keys into
    # the bank at an abutment by design and is reported so the narrowing can
    # be audited rather than taken on trust.
    air, burial, edge_buried = [], [], []
    for p in decks:
        top = p.y + f_floor.hi[1]
        bottom = p.y + f_floor.lo[1]
        g = surface(p.x, p.z)
        centre_row = abs(lateral_of(span, p)) <= 1.0
        if g <= bottom:
            air.append((round(bottom - g, 3), round(p.x, 2), round(p.z, 2)))
        elif centre_row:
            # THE SAME QUANTITY THE GUARD JUDGES: how far the WALKING SURFACE
            # sits below the ground.  The solid's own 0.13 m thickness under
            # the ground is not burial -- a tile resting exactly on the bench
            # reads 0.130 by that measure and 0.000 by this one, and quoting
            # the first beside a guard that judges the second is the
            # two-populations-one-name defect in a report.
            burial.append((round(max(0.0, g - top), 3),
                           round(p.x, 2), round(p.z, 2)))
        else:
            edge_buried.append((round(g - top, 3),
                                round(p.x, 2), round(p.z, 2)))
    worst_air = max(air) if air else (0.0, None, None)
    worst_bur = max(burial) if burial else (0.0, None, None)

    columns: dict[tuple[float, float], list] = {}
    for p in piles:
        columns.setdefault((round(p.x, 3), round(p.z, 3)), []).append(p)
    f_pile = asm.facts()["wood_pole_log_4"]
    embeds = []
    for (x, z), col in columns.items():
        foot = min(pc.y + f_pile.lo[1] for pc in col)
        embeds.append(round(surface(x, z) - foot, 3))

    # BOTH READINGS, ALWAYS.  `step_m` is the one the guard judges - taken
    # where this deck's outermost tile actually meets the ground - and
    # `step_at_bank_coordinate_m` is the old water-edge reading, kept so the
    # correction stays auditable rather than becoming a number that moved.
    steps = {}
    land = deck_landfall(span, pieces)
    for end in ("bank_a", "bank_b"):
        x, z = span[end]["xz"]
        g_bank = surface(x, z)
        lx, lz = land[end]["xz"]
        g = surface(lx, lz)
        steps[end] = {"xz": [round(lx, 3), round(lz, 3)],
                      "bank_xz": [x, z],
                      "outboard_of_bank_m": land[end]["outboard_of_bank_m"],
                      "applied_ground_y": round(g, 3),
                      "deck_y": round(land[end].get("deck_y", deck_y), 3),
                      "step_m": round(land[end].get("deck_y", deck_y) - g, 3),
                      "applied_ground_at_bank_coordinate_y": round(g_bank, 3),
                      "step_at_bank_coordinate_m": round(deck_y - g_bank, 3)}

    xs = [p.x for p in pieces]
    zs = [p.z for p in pieces]
    cx, cz = (min(xs) + max(xs)) / 2.0, (min(zs) + max(zs)) / 2.0
    reach = max(math.hypot(p.x - cx, p.z - cz) for p in pieces)

    return {
        "crossing_id": span["crossing_id"],
        "segment": span["segment"],
        "span_m": span["span_m"],
        "deck_y": round(deck_y, 3),
        "deck_y_required": span["deck_y_REQUIRED"],
        "deck_y_is_sea_datum": abs(deck_y - SEA_DECK_DATUM_Y) <= 1e-3,
        "bearing_deg": round(bearing_of(span), 2),
        "pieces": len(pieces),
        "prefabs": prefabs,
        "deck_tiles": len(decks),
        "pile_columns": len(columns),
        "pile_pieces": len(piles),
        "terrain_anchored": verdict["terrain_anchored"],
        "support_min_by_role": verdict["support_min_by_role"],
        "support_safety_factor": verdict["safety_factor"],
        "worst_air_under_a_deck_tile_m": worst_air[0],
        "worst_air_at": [worst_air[1], worst_air[2]],
        "worst_air_over_visible_threshold": worst_air[0] > AIR_VISIBLE_M,
        "worst_deck_burial_m": worst_bur[0],
        "worst_deck_burial_at": [worst_bur[1], worst_bur[2]],
        "worst_travelled_way_burial_m": worst_bur[0],
        "edge_tiles_keyed_into_bank": len(edge_buried),
        "worst_edge_tile_burial_m": (max(edge_buried)[0] if edge_buried
                                     else 0.0),
        "worst_edge_tile_at": (list(max(edge_buried)[1:]) if edge_buried
                               else [None, None]),
        "edge_tile_note": (
            "the OUTER tile row of a 6 m deck arriving at a bank with cross "
            "slope sits inside that bank: it is the deck keying in, not a "
            "hole. Counted here rather than refused; the travelled-way "
            "figure above is the one the seating guard judges."),
        "pile_embed_min_m": min(embeds) if embeds else None,
        "pile_embed_max_m": max(embeds) if embeds else None,
        "abutment_steps": steps,
        "census_disc": {"pos": [round(cx, 2), round(cz, 2)],
                        "max_m": round(reach + 2.0, 2)},
        "population": (f"{len(pieces)} pieces generated by "
                       f"crossings/assemble.bridge at deck_y={deck_y:.3f}, "
                       f"width {span['deck_width_m']:g} m, bent spacing 8.0 m; "
                       f"air/burial over {len(decks)} deck tiles, embed over "
                       f"{len(columns)} pile columns; heights are "
                       f"applied.Applied.surface_at (generated + the live "
                       f"union of every terrain_write delta, bilinear)"),
    }


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------

def plan_records(span: dict, pieces: list, meas: dict, verdict: dict,
                 baseline: dict[str, int], batch: int = BATCH) -> list[dict]:
    """One `spawn_plan` per batch, with a CUMULATIVE postcondition.

    `expect.prefab_count` is what the y-blind XZ cylinder at the span's census
    disc will read the moment THIS record lands: the live baseline measured in
    that same disc before record 1, plus everything this run has sent into it
    up to and including this batch.  Nothing here is read off the finished
    span.
    """
    disc = meas["census_disc"]
    out: list[dict] = []
    running = dict(baseline)
    total = (len(pieces) + batch - 1) // batch
    for i in range(0, len(pieces), batch):
        chunk = pieces[i:i + batch]
        n = i // batch + 1
        mine: dict[str, int] = {}
        for p in chunk:
            mine[p.prefab] = mine.get(p.prefab, 0) + 1
            running[p.prefab] = running.get(p.prefab, 0) + 1
        wire = [asm.command(p) for p in chunk]
        expect = {"prefab_count": [
            {"prefab": name, "pos": disc["pos"], "max": disc["max_m"],
             "count": running[name]}
            for name in sorted(mine)]}
        out.append({
            "params": {
                # `bridge_body_place` is Crossings' REGISTERED role for
                # exactly this op and the schema refuses an invented one: a
                # decked crossing is a structure, it leaves the road's terrain
                # entirely, and the operator greps one vocabulary. The batch
                # label lives in `meta`, which is the free-form half.
                "role": "bridge_body_place",
                "site_id": span["crossing_id"],
                "plan_sha256": hashlib.sha256(
                    "\n".join(wire).encode()).hexdigest(),
                "anchor": {"x": disc["pos"][0], "y": meas["deck_y"],
                           "z": disc["pos"][1],
                           "yaw": meas["bearing_deg"]},
                "reach_m": disc["max_m"],
                "command_count": len(chunk),
                "prefabs": mine,
                "datum": meas["deck_y"],
                "flatten": "FORBIDDEN",
                "flatten_reason": (
                    f"over-water structure: its piles stand on the generated "
                    f"bed of an inland channel "
                    + (f"{span['depth_below_plane_m']:.2f} m below the "
                       f"y={WATER_LEVEL_M:g} water plane"
                       if span.get("depth_below_plane_m") is not None
                       # A DECLARED RECORD CARRIES NO MEASURED DEPTH, and
                       # formatting None crashed the only builder in this
                       # tree on the whole declared population -- three
                       # records, one of them T9-wttown-dockshore-sb01,
                       # whose deck is the remedy for TWO refusals. The
                       # absence is STATED here rather than defaulted to a
                       # number, because a fabricated depth in a ledger
                       # record's own reason string is worse than no depth.
                       else "whose depth below the "
                            f"y={WATER_LEVEL_M:g} water plane this record "
                            f"does not declare")
                    + f" and its deck stands at the "
                    f"road's own solved H {meas['deck_y']:.3f} m. Levelling "
                    f"inside this footprint removes the water the structure is "
                    f"designed to stand in -- the measured early-dock defect -- "
                    f"and filling this channel to road freeboard would "
                    f"permanently drain it, which is the unrepairable "
                    f"direction (raising ground removes water)."),
            },
            "wire": wire,
            "requires": {"mods": ["WorldEditCommands", "ServerDevcommands",
                                  "ValheimRcon"],
                         "prefabs": sorted(mine), "blobs": []},
            "expect": expect,
            "meta": {
                "structure_id": f"{span['crossing_id']}#{n}of{total}",
                "batch": n, "of": total,
                # THE ROADS THIS DECK STANDS ON, ON EVERY BATCH.  The record's
                # unit is the crossing; `buildstage.replay` owes a deck the
                # recheck of these segments, taken after its last piece, and
                # batch 1's `span_measurement.segment` names only the one it
                # is FILED under -- a crossing joining two roads stands on
                # both.  See buildstage.STANDS_ON_DEPENDS_ON.
                "stands_on": list(span.get("terrain_segments")
                                  or [span["segment"]]),
                "cumulative_in_disc": dict(running),
                "baseline_in_disc": dict(baseline),
                "postcondition_is_measured_at_landing": (
                    "objects_count id=<prefab> pos=<x>,<z> max=<r> is a "
                    "Y-BLIND XZ cylinder measured the moment this record "
                    "lands, so the expected count is the live baseline in "
                    "this disc plus what this run has sent into it through "
                    "this batch -- never the finished span's bill of "
                    "materials. A pile column at one xz answers once per "
                    "pole, not once per column."),
                **({"span_measurement": meas,
                    "structural_verdict": verdict} if n == 1 else {}),
            },
        })
    return out


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------

def surface_fn(patches: dict, live) -> "asm.Bed":
    """The APPLIED surface: the seed bed via `ribbon.zone_patches`, with the
    ledger's own terrain writes composed on by `applied.Applied`.

    Returned as an `asm.Bed` carrying that provenance in words, because
    `assemble`'s generators now REFUSE a bed that cannot say where its
    heights came from -- this was the one of the tree's three pile
    implementations that got it right, and it is the shape the other two were
    corrected to.
    """
    def f(x: float, z: float) -> float:
        got = live.surface_at(patches, x, z)
        if got is None:
            raise SystemExit(
                f"no applied surface at ({x:.2f}, {z:.2f}): the zone was not "
                f"in the patch request, so every height derived from it would "
                f"be a guess. Widen --zone-margin.")
        return got[0]
    return asm.Bed.applied(
        f, "ribbon.zone_patches(seed bed) + applied.Applied(ledger deltas)")


def prepare(spans: list[dict], actor: str, out: Path,
            zone_margin_m: float = 24.0, deck_override: float | None = None
            ) -> tuple[list[dict], list, callable]:
    existing_by_span = existing_bridges(spans)
    zones = sorted({z for s in spans for z in span_zones(s, zone_margin_m)})
    zones = sorted(set(zones) | {
        z for body in existing_by_span.values() for p in body
        for z in span_zones({"bank_a": {"xz": [p.x, p.z]},
                             "bank_b": {"xz": [p.x, p.z]}}, 4.0)})
    patches = ribbonmod.zone_patches(zones, SEED, out / "boardwalk_zones.bin")
    live = appliedmod.Applied(actor=actor)
    surface = surface_fn(patches, live)
    routes = routes_with_design_levels(
        road_gate.built_segments(worldseed.ledger_path()),
        worldseed.ledger_path())
    prepared = []
    for span in spans:
        existing = existing_by_span[span["crossing_id"]]
        deck_y = (shared_deck_datum(span, existing) if deck_override is None
                  else deck_override)
        # SOLVE THE BENT SPACING against the game's own support algorithm
        # rather than declaring it: widest first, tightening only when the
        # structural verdict says this span's geometry needs it.  The overhang
        # is solved against the applied surface in the same loop, and the
        # verify runs on WHAT IS LEFT after the trim -- verifying before it
        # would certify pieces that are never sent, which is the
        # pre-flight/postcondition mismatch in miniature.
        # THE GENERATOR REFUSES BEFORE THIS FUNCTION EVER SEES A PIECE.
        # `assemble.bridge` now runs the same seating re-test as the guard
        # below and raises rather than returning an assembly whose travelled
        # way is under the ground.  That is a STRONGER answer than a guard
        # firing downstream, so it is recorded AS the seating refusal rather
        # than being allowed to abort the whole run: `--prove-guards` must
        # still see this span refused, and a span that cannot be built is
        # refused in the way that matters.
        chosen = None
        refusal = None
        bent_report: dict = {}
        for spacing in BENT_SPACINGS_M:
            bent_report = {}
            try:
                pieces = build_pieces(span, surface, deck_y,
                                      bent_spacing_m=spacing,
                                      report=bent_report, routes=routes,
                                      existing=existing)
            except SystemExit as e:
                refusal = str(e)
                break
            pieces, trimmed = trim_buried_overhang(span, pieces, surface,
                                                   deck_y)
            verdict = asm.verify(
                [*pieces, *existing], surface,
                note=f"{span['crossing_id']} at deck_y={deck_y:.3f}, "
                     f"bent spacing {spacing:g} m")
            chosen = (spacing, pieces, trimmed, verdict, bent_report)
            if verdict["ok"]:
                break
        if chosen is None:
            guards = {n: [] for n in GUARDS}
            guards["seating"] = [f"{span['crossing_id']}: REFUSED BY THE "
                                 f"GENERATOR, before any piece was returned "
                                 f"-- {refusal}"]
            prepared.append({"span": span, "pieces": [], "verdict": None,
                             "measurement": None, "guards": guards,
                             "deck_y": deck_y, "trimmed": [],
                             "bent_spacing_m": None,
                             "generator_refusal": refusal})
            continue
        spacing, pieces, trimmed, verdict, bent_report = chosen
        meas = measure(span, pieces, verdict, surface, deck_y)
        meas["overhang_stations_trimmed"] = trimmed
        meas["bent_spacing_m"] = spacing
        meas["bent_spacing_solved_from"] = list(BENT_SPACINGS_M)
        meas["bank_projection"] = span.get("bank_projection")
        meas["bents"] = bent_report
        meas["existing_bridge_pieces"] = len(existing)
        meas["datum_resolution"] = dict(
            planned_y=span["deck_y_REQUIRED"], emitted_y=deck_y,
            bank_step_limit_m=ABUTMENT_STEP_MAX_M,
            existing_floor_datums=sorted({
                round(p.y + asm.facts()["wood_floor"].hi[1], 4)
                for p in existing if p.prefab == "wood_floor"}),
            reason="join a compatible existing deck without moving its pieces"
                   if deck_y != span["deck_y_REQUIRED"] else "planned datum")
        guards = run_guards(span, pieces, verdict, surface, deck_y,
                            existing=existing)
        guards["carriageway"].extend(
            route_carriageway_guard(span, pieces, surface, routes,
                                    existing=existing))
        prepared.append({"span": span, "pieces": pieces, "verdict": verdict,
                         "measurement": meas, "guards": guards,
                         "deck_y": deck_y, "trimmed": trimmed,
                         "existing": existing,
                         "bent_spacing_m": spacing})
    return prepared, zones, surface


def segment_terrain_written(actor: str,
                            world: str = "") -> dict[str, dict]:
    """Which segments have had their GROUND MOVED, by FILE LINE and by REASON.

    FILE LINE, never seq: the chain carries a labelled fork at line 47 with
    duplicate seq labels 43-46, so seq is not a total order on this artefact.

    THE PREDICATE IS "DID A RECORD MOVE A BED", NOT "IS THIS RECORD CALLED
    terrain_write", AND THE DIFFERENCE IS 548 RECORDS.  This function used to
    test `rec["op"] != "terrain_write"`, which is the allowlist key CacheKey
    refused to ship for the ground cache and now keeps restored as a neuter.
    MEASURED on Ulfsland's 5,433 records: 737 can move a bed and only 189 of
    them are called `terrain_write` --

        terrain_write   189
        zones_generate  420   regenerates the zone; the bed moves
        zones_restore     2
        retire          123   deleteObjects with no -prefab, so unscoped
        objects_clear     3   objects_remove naming no id set

    so a segment graded by any route other than the one verb reads as
    UNGRADED here.  In this module that is not a cosmetic miscount: it is the
    precondition that REFUSES TO BUILD A DECK, on the stated grounds that
    decking first arms `writer._flatten_forbidden_check` against the leg's
    own later abutment ramp.  A segment whose ramp already exists would be
    refused a deck forever, and the refusal would name a prerequisite that
    had already run.

    `accept/instruments.py::record_moves_bed` is the SHARED answer and it is
    read rather than reimplemented: it classifies off the `wire` -- what the
    game actually received -- instead of the `op` -- what the writer called
    it -- and it FAILS CLOSED, counting an unknown verb, an unscoped
    deleteObjects and an unfenced wildcard as moving.  instruments.py's own
    header says a second implementation of a shared helper has cost this
    project twice; this would have been the third.

    The reason string travels with the answer so a refusal can name the
    prerequisite AND how it was recognised, rather than only that it is
    absent.
    """
    from writer import Ledger  # noqa: PLC0415
    sys.path.insert(0, str(JUMPSTART))
    from accept import instruments as AI  # noqa: PLC0415
    led = Ledger.open(world or WORLD, actor=actor)
    out: dict[str, dict] = {}
    for line, rec in enumerate(led.records()):
        why = AI.record_moves_bed(rec)
        if not why:
            continue
        name = (rec.get("params") or {}).get("name") or ""
        if not name:
            continue
        out[name] = {"file_line": line, "op": rec.get("op"),
                     "seq": rec.get("seq"), "moves_bed_because": why}
    return out


def segment_key(segment_id: str) -> str:
    """`N2-shelf-mid1` -> the `road_N2_shelf_mid1` a terrain_write is named."""
    return "road_" + segment_id.replace("-", "_")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("op", choices=["plan", "build", "census-selftest",
                                   "reconcile"])
    ap.add_argument("--spans", help="comma-separated crossing_ids")
    ap.add_argument("--segments", help="comma-separated segment ids")
    ap.add_argument("--spans-file", default=str(SPANS))
    ap.add_argument(
        "--from-network", action="store_true",
        help="take the spans from the ROAD NETWORK's own crossings[] "
             "records (roads/segments.yaml) instead of the hand-written "
             "north_spans.json. This is the only source that exists on a "
             "world nobody has written a spans file for, and it is the one "
             "that found the 6 declared crossings on BUILT segments with no "
             "body: 347.5 m of deck the hand-over never covered.")
    ap.add_argument("--actor", required=True,
                    help="the agent id ACTUALLY running this pass; every "
                         "record is appended under it")
    ap.add_argument("--out", default="/tmp/boardwalk")
    ap.add_argument("--batch", type=int, default=BATCH)
    ap.add_argument("--zone-margin", type=float, default=24.0)
    ap.add_argument("--dry", action="store_true",
                    help="validate the records and the wire without appending "
                         "or sending (LiveBuilder(dry=True) does not append)")
    ap.add_argument("--prove-guards", action="store_true",
                    help="re-run every guard against a deliberately wrong "
                         "DECK_DATUM_Y deck and require each to REFUSE. A "
                         "guard shown only to pass has not been shown to be a "
                         "guard.")
    ap.add_argument("--require-terrain", action="store_true", default=True)
    ap.add_argument("--no-require-terrain", dest="require_terrain",
                    action="store_false")
    ap.add_argument(
        "--no-project-abutments", dest="project_abutments",
        action="store_false", default=True,
        help="use north_spans.json's bank coordinates VERBATIM instead of "
             "projecting them onto the crossing's own recorded "
             "`centreline_straight_from_to`. Exists to reproduce the "
             "geometry of the fourteen spans landed before the offset was "
             "MEASURED (0.00-5.05 m lateral over the sixteen), not to ship "
             "it: an abutment more than the ribbon's 4 m half-bench off the "
             "line probes natural ground and reports it as the bench.")
    args = ap.parse_args()
    if args.op == "census-selftest":
        return census_selftest()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.op == "reconcile":
        records, corpus = crossing_corpus()
        spans, _ = spans_from_network()
        zones = sorted({z for s in spans
                        for z in span_zones(s, args.zone_margin)})
        patches = ribbonmod.zone_patches(zones, SEED,
                                         out / "reconcile_zones.bin")
        surface = surface_fn(patches, appliedmod.Applied(actor=args.actor))
        rec = reconcile_declared_lines(records, surface)
        print(f"{len(records)} crossing record(s) from {corpus['source']}\n")
        for r in rec["reconciled"]:
            lat = r["lateral_off_centreline_m"]
            w, n = r["declared_line"], r["projected_line"]
            print(f"  {r['crossing_id']:26s} RECONCILED  bank_a "
                  f"{lat['bank_a']:+.3f} m, bank_b {lat['bank_b']:+.3f} m off "
                  f"its own recorded centreline; span {w['span_m']:.2f} -> "
                  f"{n['span_m']:.2f} m, wet {w['wet']}/{w['samples']} -> "
                  f"{n['wet']}/{n['samples']}, bed-above-deck "
                  f"{w['bed_above_deck']} -> {n['bed_above_deck']}")
        for r in rec["refused"]:
            print(f"  {r['crossing_id']:26s} REFUSED: "
                  + "; ".join(r.get("why", [r.get("why", "?")])))
        print(f"\n  already on the line: {rec['already_on_the_line']}")
        print(f"  no centreline recorded: {rec['no_centreline']}")
        print(f"\n{len(rec['reconciled'])} reconciled, "
              f"{len(rec['refused'])} refused, "
              f"{len(rec['already_on_the_line'])} already on the line, "
              f"{len(rec['no_centreline'])} with no recorded centreline, "
              f"of {len(records)}")
        if args.dry:
            print("\n--dry: NOTHING WRITTEN")
        else:
            touched = apply_reconciliation(
                rec, [crossings_yaml(), network_segments_yaml()])
            for name, ids in touched.items():
                print(f"  {name}: patched {len(ids)} record(s)")
            rec["written"] = touched
        (out / "reconcile.json").write_text(
            json.dumps(rec, indent=1, default=str) + "\n")
        print(f"-> {out / 'reconcile.json'}")
        return 0

    # WHAT IS BUILT IS SELECTED AFTER WHAT IS COMPARED.  `--spans` used to cut
    # the population down BEFORE `collapse_duplicate_declarations`, so a call
    # naming one crossing compared it with nothing: MEASURED on Fresh42,
    # T8-stenvik-wttown-x01 and T3-temple-meadhall-x01 are one channel (banks
    # (216,-46)->(202,-34) and (202,-36)->(214,-50)), and two single-span
    # calls -- which is exactly how buildstage's step 6.5 drives this, one
    # unit per crossing -- would have decked it twice.  The collapse now runs
    # over the seed's whole population and the selection is applied to what
    # it keeps; a selected declaration it folded into another is SERVED BY
    # that one and builds nothing here.
    select: set[str] | None = None
    if args.from_network:
        spans, netpop = spans_from_network()
        src = netpop["source"]
        # THE DENOMINATOR, ALWAYS.  `0 spans` and `0 of 14 spans` are
        # different statements and only the second one is legible.
        print(f"{netpop['enumerated']} of {netpop['declared']} crossing(s) "
              f"the network declares are buildable spans, from {src}")
        for r in netpop["refused"]:
            print(f"  {r['crossing_id']:26s} REFUSED as a span: missing "
                  f"{', '.join(r['missing'])}")
        if args.spans:
            select = set(args.spans.split(","))
            have = {s["crossing_id"] for s in spans}
            if select - have:
                raise SystemExit(f"no such span(s): {sorted(select - have)}; "
                                 f"have {sorted(have)}")
        if args.segments:
            want = set(args.segments.split(","))
            on = {s["crossing_id"] for s in spans if s["segment"] in want}
            select = on if select is None else select & on
    else:
        src = args.spans_file
        spans = load_spans(Path(args.spans_file),
                           only=args.spans.split(",") if args.spans else None,
                           segments=args.segments.split(",") if args.segments
                           else None)
    print(f"{len(spans)} span(s), "
          f"{sum(s['span_m'] for s in spans):.1f} m, from {src}")
    if args.project_abutments:
        spans = project_onto_centreline(spans)
        for s in spans:
            pj = s.get("bank_projection", {})
            if not pj.get("applied"):
                print(f"  {s['crossing_id']:26s} NOT projected: {pj.get('why')}")
                continue
            a = pj["ends"]["bank_a"]["lateral_off_centreline_m"]
            b = pj["ends"]["bank_b"]["lateral_off_centreline_m"]
            if max(abs(a), abs(b)) > 0.005:
                print(f"  {s['crossing_id']:26s} abutments projected onto the "
                      f"recorded centreline: bank_a was {a:+.2f} m off, "
                      f"bank_b {b:+.2f} m off")

    # ONE SPAN PER PHYSICAL CHANNEL, BEFORE A SINGLE PIECE IS SOLVED.  Run on
    # the geometry that will actually be BUILT (post-projection), because two
    # decks in the same cubic metres is what this refuses, not two lines in a
    # file that happen to match.
    spans, dupes = collapse_duplicate_declarations(spans)
    print(f"\n{dupes['distinct_channels']} distinct physical channel(s) from "
          f"{dupes['declarations']} declaration(s); {dupes['distinct_m']:.1f} m "
          f"of distinct ground out of {dupes['declared_m']:.1f} m declared "
          f"(corridor area at the MEASURED half-width "
          f"{dupes['half_width_m']:.3f} m, {dupes['sample_m']:g} m sampling, "
          f"threshold {dupes['threshold']})")
    for p in dupes["pairs_over_threshold"]:
        print(f"  {p['a']} == {p['b']}: corridor overlap {p['fraction']:.3f} "
              f"({p['both_directions']})")
    for drop, keep in sorted(dupes["collapsed"].items()):
        print(f"  {drop:26s} COLLAPSED into {keep}: the same channel, "
              f"declared twice. Building both would put two decks in the "
              f"same cubic metres.")
    if not spans:
        raise SystemExit("REFUSING: every span collapsed away; there is "
                         "nothing left to build")
    if select is not None:
        served = {c: dupes["collapsed"][c] for c in sorted(select)
                  if c in dupes["collapsed"]}
        spans = [s for s in spans if s["crossing_id"] in select]
        for c, keep in served.items():
            print(f"  {c:26s} SERVED BY {keep}: the same channel, and that "
                  f"declaration's deck is the one built. Nothing is built "
                  f"for {c} here.")
        if not spans:
            (out / "boardwalk_report.json").write_text(json.dumps(
                {"spans": [], "served_by": served,
                 "duplicate_census": dupes}, indent=1, default=str) + "\n")
            print(f"\nNOTHING TO BUILD: every selected declaration "
                  f"{sorted(select)} is served by another one's deck.")
            return 0
        print(f"{len(spans)} span(s) selected AFTER the channel collapse: "
              f"{[s['crossing_id'] for s in spans]}")

    written = segment_terrain_written(args.actor)
    # EVERY SEGMENT THIS DECK ABUTS, not just the one it is filed under.  A
    # crossing that JOINS two segments has no segment of its own, so the
    # precondition is BOTH of its abutment nodes' segments; a crossing that
    # lies on one has exactly that one and this loop is unchanged for it.
    for s in spans:
        segs = s.get("terrain_segments") or [s["segment"]]
        unwritten = [g for g in segs if segment_key(g) not in written]
        if unwritten:
            msg = (f"{s['crossing_id']}: {len(unwritten)} of {len(segs)} "
                   f"abutment segment(s) have NO BED-MOVING RECORD in the "
                   f"chain -- {unwritten} (looked for params.name "
                   f"{[segment_key(g) for g in unwritten]} on any record "
                   f"accept/instruments.py::record_moves_bed accepts, which "
                   f"is the WIRE the game received and not the op name the "
                   f"writer chose). A deck carries flatten=FORBIDDEN, which "
                   f"arms writer._flatten_forbidden_check against every "
                   f"LATER terrain write inside its footprint -- so decking "
                   f"first refuses the leg's own abutment ramp. Build the "
                   f"terrain first.")
            if args.require_terrain:
                raise SystemExit("REFUSING: " + msg)
            print("WARNING: " + msg)
        else:
            print(f"  {s['crossing_id']:26s} bed moved at file line(s) "
                  + ", ".join(
                      f"{g}:{written[segment_key(g)]['file_line']}"
                      f"(seq {written[segment_key(g)]['seq']}, "
                      f"{written[segment_key(g)]['op']}, "
                      f"{written[segment_key(g)]['moves_bed_because']})"
                      for g in segs))

    prepared, zones, surface = prepare(spans, args.actor, out,
                                       args.zone_margin)
    print(f"patchscan: {len(zones)} zone(s)")

    report = {"spans": [], "zones": [list(z) for z in zones],
              "duplicate_census": dupes}
    refused = []
    for pr in prepared:
        m = pr["measurement"]
        g = pr["guards"]
        bad = [x for v in g.values() for x in v]
        if pr.get("generator_refusal"):
            print(f"\n{pr['span']['crossing_id']}  deck_y "
                  f"{pr['deck_y']:.3f}  NO PIECES -- the generator refused")
            for x in bad:
                print(f"      {x}")
            print(f"  guard {'seating':12s} REFUSES (raised by the deck "
                  f"generator -- land seating or crossings/assemble.py -- "
                  f"before returning)")
            refused.append(pr["span"]["crossing_id"])
            report["spans"].append({"measurement": None, "guards": g,
                                    "refusals": bad,
                                    "crossing_id": pr["span"]["crossing_id"],
                                    "generator_refusal":
                                        pr["generator_refusal"]})
            continue
        print(f"\n{m['crossing_id']}  {m['span_m']:.2f} m  deck_y "
              f"{m['deck_y']:.3f} (required {m['deck_y_required']:.3f}, sea "
              f"datum {SEA_DECK_DATUM_Y:.1f} -> "
              f"{'IS' if m['deck_y_is_sea_datum'] else 'is NOT'} the sea datum)")
        print(f"  {m['pieces']} pieces: {m['prefabs']}")
        print(f"  deck tiles {m['deck_tiles']}, pile columns "
              f"{m['pile_columns']} ({m['pile_pieces']} poles), "
              f"terrain-anchored {m['terrain_anchored']}")
        print(f"  support min by role {m['support_min_by_role']} "
              f"(safety x{m['support_safety_factor']})")
        print(f"  worst air under a deck tile {m['worst_air_under_a_deck_tile_m']} m "
              f"at {m['worst_air_at']}; worst burial "
              f"{m['worst_deck_burial_m']} m at {m['worst_deck_burial_at']}")
        print(f"  pile embed {m['pile_embed_min_m']} .. {m['pile_embed_max_m']} m")
        for end, st in m["abutment_steps"].items():
            print(f"  {end} step {st['step_m']:+.3f} m "
                  f"(applied ground {st['applied_ground_y']:.3f} -> deck "
                  f"{st['deck_y']:.3f})")
        print(f"  census disc pos={m['census_disc']['pos']} "
              f"max={m['census_disc']['max_m']} m")
        for name in GUARDS:
            print(f"  guard {name:12s} {'PASSES' if not g[name] else 'REFUSES'}")
            for x in g[name]:
                print(f"      {x}")
        if bad:
            refused.append(m["crossing_id"])
        report["spans"].append({"measurement": m, "guards": g,
                                "refusals": bad})

    if args.prove_guards:
        print("\n--- GUARDS PROVEN IN THE OTHER DIRECTION: the same geometry "
              f"at DECK_DATUM_Y {SEA_DECK_DATUM_Y:.1f} m ---")
        wrong, _, _ = prepare(spans, args.actor, out, args.zone_margin,
                              deck_override=SEA_DECK_DATUM_Y)
        proof = {}
        for pr in wrong:
            g = pr["guards"]
            cid = pr["span"]["crossing_id"]
            fired = [n for n in GUARDS if g[n]]
            proof[cid] = {"fired": fired,
                          "refusals": {n: g[n] for n in fired}}
            label = ", ".join(fired) if fired else \
                "NOTHING -- THE GUARD IS NOT A GUARD"
            print(f"  {cid:26s} REFUSED BY {label}")
        report["guard_proof_at_sea_datum"] = proof
        unproven = [c for c, v in proof.items() if not v["fired"]]
        if unproven:
            raise SystemExit(
                f"REFUSING: {len(unproven)} span(s) pass every guard at the "
                f"WRONG deck datum {SEA_DECK_DATUM_Y} m: {unproven}. A guard "
                f"that cannot refuse the defect it exists for is decoration.")

    (out / "boardwalk_report.json").write_text(
        json.dumps(report, indent=1, default=str) + "\n")
    print(f"\n-> {out / 'boardwalk_report.json'}")

    if refused:
        print(f"\nREFUSED {len(refused)} span(s): {refused}")
        return 4
    if args.op == "plan":
        totals = sum(m["measurement"]["pieces"] for m in report["spans"])
        print(f"\nALL {len(prepared)} span(s) pass every guard. "
              f"{totals} pieces, "
              f"{sum(len(plan_records(p['span'], p['pieces'], p['measurement'], p['verdict'], {}, args.batch)) for p in prepared)} "
              f"records at batch {args.batch}.")
        return 0

    # ---- build -----------------------------------------------------------
    from live import LiveBuilder  # noqa: PLC0415
    from writer import Ledger  # noqa: PLC0415

    led0 = Ledger.open(getattr(args, "world", None) or WORLD, actor=args.actor)
    pre_sha = hashlib.sha256(led0.path.read_bytes()).hexdigest()
    pre_lines = len(led0.path.read_text().splitlines())
    print(f"\nchain before record 1: {led0.path}\n  sha256 {pre_sha}\n"
          f"  {pre_lines} file lines")

    built, remaining = [], []
    with LiveBuilder(actor=args.actor, dry=args.dry) as b:
        print(f"players: {b.srv.command('players') if not args.dry else '(dry)'}")
        for pr in prepared:
            span, pieces, m = pr["span"], pr["pieces"], pr["measurement"]
            cid = span["crossing_id"]
            disc = m["census_disc"]
            # BASELINE MEASURED WITH THE INSTRUMENT, IN THIS DISC, NOW.  A
            # neighbouring span decked earlier in this same run is inside some
            # of these discs, and the only way the expectation can be right
            # about that is to measure rather than to assume zero.
            baseline: dict[str, int] = {}
            for name in sorted(m["prefabs"]):
                if args.dry:
                    baseline[name] = 0
                    continue
                got, _ = b.srv.count(name, disc["pos"][0], disc["pos"][1],
                                     disc["max_m"])
                baseline[name] = got
            print(f"\n{cid}: baseline in disc {baseline}")
            # THE ROSTER AND THE ZONE SET, PER SPAN, IMMEDIATELY BEFORE THE
            # FIRST APPEND FOR IT.  `LiveBuilder.emit` arms the loaded-zone
            # guard only for `terrain_write` -- a spawn does not delete a
            # zone's _TerrainCompiler and so cannot lose a client's copy of
            # it -- but a run that reads the roster once at the top and then
            # appends for twenty minutes is reporting a population it no
            # longer has.  Printed BY INDEX so the zones this span writes
            # into are on the record beside the players holding them.
            if not args.dry:
                roster = b.srv.command("players")
                zs = span_zones(span, args.zone_margin)
                print(f"  players before {cid}: {roster.strip()[:200]}")
                print(f"  zone set BY INDEX for {cid}: "
                      f"{[list(z) for z in zs]}")
            recs = plan_records(span, pieces, m, pr["verdict"], baseline,
                                args.batch)
            try:
                for r in recs:
                    res = b.emit("spawn_plan", params=r["params"],
                                 wire=r["wire"], requires=r["requires"],
                                 expect=r["expect"], meta=r["meta"])
                    print(f"  {r['meta']['structure_id']}: "
                          f"{res.get('status')} seq {res.get('seq')}")
                sv = b.emit("save", params={"role": "bridge_body_place",
                                            "site_id": cid},
                            wire=["save"],
                            expect={"prefab_count": [
                                {"prefab": "wood_floor",
                                 "pos": disc["pos"], "max": disc["max_m"],
                                 "count": baseline.get("wood_floor", 0)
                                          + m["prefabs"].get("wood_floor", 0)}]})
                print(f"  save: {sv.get('status')} seq {sv.get('seq')}")
                built.append(cid)
            except Exception as exc:  # noqa: BLE001
                print(f"  STOPPED at {cid}: {type(exc).__name__}: {exc}")
                remaining = [p["span"]["crossing_id"] for p in prepared
                             if p["span"]["crossing_id"] not in built]
                break
    if not remaining:
        remaining = [p["span"]["crossing_id"] for p in prepared
                     if p["span"]["crossing_id"] not in built]
    print(f"\nDECKED  {len(built)}: {built}")
    print(f"REMAIN  {len(remaining)}: {remaining}")
    report["decked"] = built
    report["remaining"] = remaining
    report["chain_before_record_1"] = {"sha256": pre_sha,
                                       "file_lines": pre_lines}
    (out / "boardwalk_report.json").write_text(
        json.dumps(report, indent=1, default=str) + "\n")
    return 0 if not remaining else 4


if __name__ == "__main__":
    sys.exit(main())
