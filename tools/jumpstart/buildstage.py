#!/usr/bin/env python3
"""STAGE 12, THE BUILD: THE STEPS, DECLARED IN SOURCE, IN ORDER, AND REFUSED
AT IMPORT WHEN INCOHERENT.

WHY THIS FILE EXISTS.  `pipeline.py` row 12 read, verbatim, "It is the one
stage with no single entry point in this tree: clear.py, flatten.py, sites.py,
stock.py, ribbon.py and the settlements builders are separate drivers.  THIS IS
THE ORCHESTRATOR GAP NAMED CONCRETELY -- every other row here has one command."
MEASURED by `StageWall`: those six files each carry their own `def main` and
`if __name__` (clearing/clear.py:426, terraform/flatten.py:599,
terraform/sites.py:263, terraform/stock.py:1149, roads/ribbon.py:5439,
settlements/build.py:3268), ZERO files invoke any of them as a subprocess, and
ZERO shell scripts name three or more.  So the build stage has only ever been a
sequence of agent decisions.

AN ORDER THAT CANNOT BE RE-RUN CANNOT BE CHECKED, and that is not a convenience
argument.  Ulfsland's `1E_OUT_OF_ORDER` refusal class exists because five road
segments carry no grade record at all: nothing could say which step owed it.

WHAT THIS FILE IS.  A TABLE, in `pipeline.py`'s idiom, of the build's own
steps.  It does not reimplement the sequencing check -- it calls
`pipeline.audit_graph`, the same function that refuses `STAGES`, and refuses
this table at import for the same two reasons (UNOWNED, INVERTED).  That
function caught a real cycle on its own first run; a second copy of it is a
second place for it to be wrong.

THE TWO ORDERING DEFECTS THIS TABLE MAKES UNSPELLABLE
====================================================
Both are one rule -- EVERY TERRAIN WRITE PRECEDES EVERY PIECE -- and the rule
is declared, not commented:

  A DECK IS WRITTEN `flatten: FORBIDDEN`, so the instant it stands it OWNS THE
  GROUND THAT WOULD FREE ITS OWN BURIED TILES.  `writer._flatten_forbidden_check`
  has no owner exception; it excludes only RETIRED records.  MEASURED on
  Ulfsland: `regrade_cut` is SIXTEEN `terrain_write` records at seq 5393-5415,
  appended after the deck bodies at seq 5383-5388, and its own meta names the
  seqs it collided with (`flatten_guard.triggered_by_seq` [12, 14, 15, 16, 18,
  19, 20, 30, 31, 32, 35, 36, 37, 2056, 2057, 2058, 2171], `forbidden_live`
  362).  In this table `bodies` REQUIRES `pad_ground` and `road_ground`, so a
  terrain step after a piece step is INVERTED and refused at import.

  ROADS GRADED AFTER THE BUILDINGS THAT STAND IN THEM.  14 of Ulfsland's 47
  walker-blocking bodies are OUR OWN furniture (road_lighting seq 2769-2784,
  decks 2803-2867, one settlement wall at seq 388 against T9's grade at seq
  234).  A router fixes the other 33; these 14 are a build-order defect and
  nothing in the tree guarded it.  The guard here is the LAST step, `recheck`,
  which re-reads the live corridor and REQUIRES `fixtures` and `roadside` --
  so it cannot be run before the pieces it exists to find, and a unit that
  never ran it is reported UNVERIFIED rather than silently passed.  That is
  the `1E_OUT_OF_ORDER` class, named at build time instead of at the gate.

  A DECLARED CROSSING WITH NO BUILDER.  Pipeline row 12 hands this build the
  seed's crossing contract (`crossings`) and, until step 6.5 `decks`, no row
  consumed it: MEASURED on Fresh42, 9 declared crossings and
  S4-meadhall-southeast-x01 carrying 0 of 27 m, and every one of Ulfsland's
  22 bridges laid by an agent running a tool by hand.  `audit_graph` refuses
  a label REQUIRED and unproduced; `unbuilt_defects` refuses the other
  direction, a label HANDED and unconsumed, at import.  The deck step sits
  after `bodies` (the last step that still writes ground, so a deck's
  flatten: FORBIDDEN can never refuse a pad) and before `roadside` and
  `recheck`, which read it; a crossing whose bank's road was refused is
  BLOCKED by the pairing its corpus states (`stands_on`), and a deck is owed
  the recheck of the road it stands on.

THE DEFECT CLASS THIS IS AN INSTANCE OF is the one this project has now found
ten times: A KEY THAT NAMES A SUPERSET OR A SUBSET OF WHAT THE QUANTITY
ACTUALLY DEPENDS ON.  The replay's unit key is the obvious place to spell it
wrong: `params.site_id` alone names a SUBSET (every road record on Ulfsland
carries `meta.segment` and no `site_id`), and one global key names a SUPERSET
(two units built back to back read as one interleaved unit).  So `unit_of` is
declared, both spellings are the two neuters of the `unit_key` dependency, and
a mutating record that resolves to NO key at all is reported -- UNREAD IS NOT
CLEAR, and NONE MAY NEVER STAND FOR AN ANSWER IN EITHER DIRECTION.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, replace as dc_replace
from pathlib import Path
from typing import Callable, Iterable

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import pipeline                                            # noqa: E402
import worldseed                                           # noqa: E402

Scope = pipeline.Scope
Artefact = pipeline.Artefact
PY = sys.executable


# ---------------------------------------------------------------------------
# what a build step is
# ---------------------------------------------------------------------------
# A build step is a `pipeline.Stage` plus the two things a STAGE never needed:
# it runs once PER UNIT (a placement, a settlement, a road segment) rather than
# once, and it leaves a RECORD SHAPE in the ledger by which a finished world can
# be replayed against this declaration.  `ordinal`, `name`, `requires` and
# `produces` are spelled exactly as `pipeline.Stage` spells them, because
# `pipeline.audit_graph` reads those four and nothing else.
@dataclass(frozen=True)
class BuildStep:
    # A NUMBER THAT ORDERS, NOT A POSITION THAT COUNTS.  A step inserted
    # between two existing ones takes a FRACTION (`decks` is 6.5) and nothing
    # is renumbered, because `meta.build_step` is stamped with this value on
    # every record a step appends: MEASURED on Fresh42's ledger, 1,519
    # records carry the stamps "1".."8".  Renumbering 7 and 8 to make room
    # would re-attribute every recorded roadside lamp to the new step and
    # every recorded recheck to roadside -- a key that names a position
    # instead of the step.  `audit_graph` compares ordinals and reads nothing
    # else about them.
    ordinal: float
    name: str
    owner: str                                  # the tool that runs it
    produces: Callable[[Scope], list[Artefact]]
    requires: tuple[str, ...] = ()
    # WHAT THIS STEP CONSUMES AND PRODUCES, in the module's own words, with the
    # citation.  The order below is DERIVED from these and from nothing else.
    evidence: str = ""
    # The units this step runs over, read off the corpus.  Empty tuple means
    # "this world has none", which is a fact and not a failure.
    units: Callable[[Scope], list[str]] | None = None
    # The commands for one unit, in order.  A step with none is DECLARED, never
    # skipped -- that is how stage 12 itself was found.
    argv: Callable[["Runtime", str], list[list[str]]] | None = None
    manual: str = ""
    # The (kind, op) pairs this step's records carry in the ledger.  This is
    # the replay key; `_RANK` is derived from it, so the declaration and the
    # replay cannot drift apart.
    records: tuple[tuple[str, str], ...] = ()
    # WHETHER `records` IS A SEQUENCE THE STEP OBEYS, or only a list of the
    # shapes it owns.  `site_prepare` generates a zone BEFORE it clears it and
    # `road_ground` generates BEFORE it writes: order is part of the step.
    # `bodies`, `fixtures` and `roadside` interleave spawns and portals by
    # nature.  MEASURED on Fresh42's first full drive: reading every tuple as
    # a sequence reported 38 OUT_OF_ORDER rows, ALL `fixtures` after
    # `fixtures` inside one unit -- a stock.py spawn after its own portal.
    # The replay's docstring always said OUT_OF_ORDER is an earlier STEP after
    # a later one; the within-step index contradicted it.
    records_ordered: bool = False
    # An input row rather than a step: produced by an EARLIER PIPELINE STAGE.
    # Declared here so `audit_graph` can see an owner for every label, which is
    # the difference between "the build needs a census" and "UNOWNED".
    inherited: bool = False
    verify: Callable[[Scope], list[str]] | None = None
    # WHICH CORPUS THIS STEP'S UNIT STRINGS COME FROM.  Two steps sharing a
    # domain name unit THE SAME THINGS, so `T1-portalhub-temple` in step 7
    # and in step 4 are one piece of ground; two steps in different domains
    # share no unit vocabulary at all and a string match between them would
    # be a coincidence.  DECLARED rather than inferred from `units` being the
    # same function object, because the inference is silently right until
    # someone writes a second reader of the same corpus.  Empty for the
    # inherited row, which runs over nothing.  See UNIT_BLOCK_DEPENDS_ON.
    domain: str = ""
    # THE GROUND ONE OF THIS STEP'S UNITS STANDS ON IN ANOTHER DOMAIN, read
    # off the corpus: {unit: {domain: (units, ...)}}.  A crossing is not a
    # segment, so the domain rule alone can only report a refused segment
    # as a DEBT to the deck on it -- and a deck whose bank was never graded
    # has no bench to step from.  This names the pairing EXPLICITLY, from
    # the corpus that states it, instead of guessing it from the strings.
    # See STANDS_ON_DEPENDS_ON.
    stands_on: Callable[[Scope], dict[str, dict[str, tuple[str, ...]]]] \
        | None = None


@dataclass
class Runtime:
    """Everything a step's argv needs that is not in `Scope`."""

    scope: Scope
    actor: str
    dry: bool = False

    @property
    def work(self) -> Path:
        return self.scope.work


# ---------------------------------------------------------------------------
# the corpus the build runs over
# ---------------------------------------------------------------------------
def _placement_files(s: Scope) -> list[Path]:
    return sorted((HERE / "worlds" / s.world).glob("*/placements.yaml"))


def _placement_units(s: Scope) -> list[str]:
    """`<preset>/<id>` for every SOLVED placement.  The solve is what makes a
    placement buildable: `solve_placements.py --write` fills the `solved` block
    of a placement that already exists, so an unsolved row is a template.
    """
    import yaml                                             # noqa: PLC0415
    out: list[str] = []
    for p in _placement_files(s):
        try:
            doc = yaml.safe_load(p.read_text()) or {}
        except Exception:                                   # pragma: no cover
            continue
        preset = doc.get("preset") or p.parent.name
        for row in doc.get("placements") or []:
            if row.get("solved") and row.get("id"):
                out.append(f"{preset}/{row['id']}")
    return out


def _segment_units(s: Scope) -> list[str]:
    import yaml                                             # noqa: PLC0415
    path = worldseed.segments_path(s.seed)
    if not path.exists():
        return []
    doc = yaml.safe_load(path.read_text()) or {}
    return [x["id"] for x in (doc.get("segments") or []) if x.get("id")]


def _settlement_units(s: Scope) -> list[str]:
    """Every `--site` `settlements/build.py` accepts, read off the SAME plan it
    reads: `plan["towns"]` is keyed by town name and `plan["outliers"]` is a
    list of records keyed by `id` (build.py:3102-3103 resolves a site's kind by
    looking in exactly those two).  Read through `worldseed.plan_path`, which
    is the per-seed slot -- `settlements/plan.json` alone is ONE SLOT FOR EVERY
    WORLD, which is this project's own defect class.
    """
    plan = worldseed.plan_path(s.seed)
    if not plan.exists():
        return []
    doc = json.loads(plan.read_text())
    towns = sorted(doc.get("towns") or {})
    outliers = [r["id"] for r in (doc.get("outliers") or []) if r.get("id")]
    return towns + outliers


# DECLARED_UNITS_DEPENDS_ON: a site a person declared is a unit of step 5.
# NEUTER `off`: the shipped table, which built no declared site at all.
_DECLARED_UNITS_MODE = "on"      # on | off


def _declared_sites(s: Scope) -> dict[str, str]:
    """`{site id: kind}` for every site a PERSON declared for this seed
    (`worldseed.HUMAN_SITES`), read off the SAME plan the other settlement
    units are -- `plan["declared_sites"]`, which settlements/plan.py measured
    at stage 8 and writes only for a seed that declares any.  A declared site
    is a unit of step 5, beside the towns and the outliers: it is built by its
    own module (`harbour_site.BUILDERS`) and it follows every rule of the
    step -- after the ground of every road (`road_ground`), before the
    fixtures, the decks and the lamps that stand on it."""
    if _DECLARED_UNITS_MODE == "off":
        return {}
    plan = worldseed.plan_path(s.seed)
    if not plan.exists():
        return {}
    doc = json.loads(plan.read_text())
    return {d["id"]: d["kind"] for d in (doc.get("declared_sites") or [])
            if d.get("id")}


def _declared_units(s: Scope) -> list[str]:
    return list(_declared_sites(s))


# THE ONE BUILDER OF A DECLARED CROSSING, AND THE ONE READER OF THEIR CORPUS.
# `crossings/` holds the GEOMETRY (`assemble.bridge`, `assemble.verify`) and
# Ulfsland's hand-declared harbour driver (`plan.py` -> `structures.yaml` ->
# `build_live.py`: S1, S2 and the docks, coordinates typed for Pirate68).
# `roads/boardwalk.py --from-network` is the builder that reads a SEED'S
# crossings file, and it generates through `assemble` -- so there is one
# generator and one seed-driven driver, and this table runs that driver.
# Imported by name, once, because `roads/deck_census.py` already imports it
# under the same name.
def _boardwalk():
    roads = str(HERE / "roads")
    if roads not in sys.path:
        sys.path.insert(0, roads)
    import boardwalk                                        # noqa: PLC0415
    return boardwalk


def _crossing_corpus(s: Scope) -> tuple[list[dict], dict] | None:
    path = worldseed.crossings_path(s.seed)
    if not path.exists():
        return None
    return _boardwalk().crossing_corpus(path)


def _crossing_units(s: Scope) -> list[str]:
    """One unit per DECLARED crossing -- `declared` UNION `detected` of the
    seed's own crossings file, deduplicated by `crossing_id` exactly as the
    gate counts them (`boardwalk.crossing_corpus`).  NOT the builder's
    `spans_from_network` population: a record the builder cannot turn into a
    span must still be a unit, so its refusal names its missing fields
    instead of the record silently never being a unit at all.
    """
    got = _crossing_corpus(s)
    return [c["crossing_id"] for c in got[0]] if got else []


def _crossing_ground(s: Scope) -> dict[str, dict[str, tuple[str, ...]]]:
    """The SEGMENTS each crossing's two banks belong to, by the builder's own
    rule (`boardwalk.abutment_segments`): its `segment_id` when it lies on
    one, both abutment nodes' segments when it joins two.  Those are the
    roads whose ground must already be graded for a deck to step onto."""
    got = _crossing_corpus(s)
    if not got:
        return {}
    bw = _boardwalk()
    records, corpus = got
    return {c["crossing_id"]: {"segment": tuple(bw.abutment_segments(c, corpus))}
            for c in records}


def _split(unit: str) -> tuple[str, str]:
    preset, _, pid = unit.partition("/")
    return preset, pid


def _placements_path(s: Scope, preset: str) -> Path:
    return HERE / "worlds" / s.world / preset / "placements.yaml"


# ---------------------------------------------------------------------------
# THE TABLE
# ---------------------------------------------------------------------------
def _inputs(s: Scope) -> list[Artefact]:
    return [
        Artefact("placements", HERE / "worlds" / s.world, glob="*/placements.yaml",
                 member_key="solved"),
        Artefact("segments", worldseed.segments_path(s.seed), pipeline.SCOPE_YAML),
        # THE CROSSING CONTRACT, stage 11's other output, spelled exactly as
        # `pipeline._route` spells it.  Pipeline row 12 has REQUIRED it since
        # the build became a stage, and until 2026-09-25 no row of this table
        # consumed it: every declared crossing was handed to a build with no
        # step that builds one.  See UNBUILT_DEPENDS_ON.
        Artefact("crossings", worldseed.crossings_path(s.seed), pipeline.SCOPE_YAML),
        Artefact("census", HERE / "roads" / "census" / f"{s.world}-census.json"),
        # THE CORRIDOR'S ZONES ARE NOT THE BUILD'S TO GENERATE.  `precensus.py
        # survey` generates a zone and reads it IN THE SAME CALL (stage 10), so
        # no cell is ever censused before it exists; the census artefact IS the
        # evidence they were generated, and there is no second producer.
        Artefact("corridor_zones", HERE / "roads" / "census" / f"{s.world}-census.json"),
        # THE PER-WORLD SETTINGS `worlds/derive.py` RENDERS (pipeline stage 6).
        # Step 6 reads `<preset>/settings/chest-manifest.yaml` and a world
        # that has never been derived has none -- MEASURED on Fresh42, all 13
        # placements died on a FileNotFoundError traceback 50 minutes into a
        # live build. Declared here so the missing input is NAMED before step
        # 1 instead of found by a stack trace after step 5.
        # Path-attributed, like `placements` above: a chest manifest holds no
        # coordinates and no seed. See pipeline._world_facts.
        Artefact("world_settings", HERE / "worlds" / s.world,
                 glob="*/settings/chest-manifest.yaml"),
    ]


def _pads(s: Scope) -> list[Artefact]:
    return [Artefact("pad_zones", s.work / "build" / "pad_zones.json"),
            Artefact("pad_cleared", s.work / "build" / "pad_cleared.json")]


def _pad_ground(s: Scope) -> list[Artefact]:
    return [Artefact("pad_ground", s.work / "build" / "pad_ground.json")]


def _corridor(s: Scope) -> list[Artefact]:
    return [Artefact("corridor_cleared", s.work / "build" / "corridor_cleared.json"),
            Artefact("placed_objects", s.work / "build" / "placed-objects.json")]


def _road_ground(s: Scope) -> list[Artefact]:
    return [Artefact("road_ground", s.work / "build" / "road_ground.json")]


def _bodies(s: Scope) -> list[Artefact]:
    return [Artefact("bodies", s.work / "build" / "bodies.json")]


def _fixtures(s: Scope) -> list[Artefact]:
    return [Artefact("fixtures", s.work / "build" / "fixtures.json")]


def _roadside(s: Scope) -> list[Artefact]:
    return [Artefact("roadside", s.work / "build" / "roadside.json")]


def _decks(s: Scope) -> list[Artefact]:
    return [Artefact("decks", s.work / "build" / "decks")]


def _verdict(s: Scope) -> list[Artefact]:
    return [Artefact("corridor_verdict", s.work / "build" / "recheck")]


# STEPS 1, 2, 5 AND 6 WRITE THROUGH THE LEDGER, and `--actor` is how each
# record says who wrote it.  They drove raw RCON and appended nothing until
# 2026-09-25 -- MEASURED on the previous Fresh42 drive, 26 + 13 unit-runs at rc
# 0 and zero records naming any of the 13 installations, while 10 of the 36
# installation-pad zones sit under a later road write on the current route.
# See `terraform/flatten.py::INSTALLATION_WRITE_PATH_DEPENDS_ON`.  The dry
# form of each is its offline plan and writes nothing, so it carries no actor.
def _writer(rt: Runtime) -> list[str]:
    return [] if rt.dry else ["--actor", rt.actor]


def _av_site_prepare(rt: Runtime, unit: str) -> list[list[str]]:
    preset, pid = _split(unit)
    return [[PY, str(HERE / "clearing" / "clear.py"),
             "plan" if rt.dry else "apply",
             "--placements", str(_placements_path(rt.scope, preset)), "--id", pid,
             "--world", rt.scope.world, "--seed", rt.scope.seed] + _writer(rt)]


def _av_site_ground(rt: Runtime, unit: str) -> list[list[str]]:
    preset, pid = _split(unit)
    return [[PY, str(HERE / "terraform" / "flatten.py"),
             "plan" if rt.dry else "apply",
             "--placements", str(_placements_path(rt.scope, preset)), "--id", pid,
             "--world", rt.scope.world, "--seed", rt.scope.seed] + _writer(rt)]


def _av_road_clear(rt: Runtime, unit: str) -> list[list[str]]:
    out = rt.work / "build" / "corridor"
    cen = out / f"{unit}.census.json"
    base = [PY, str(HERE / "roads" / "clear.py")]
    segs = ["--segments", str(worldseed.segments_path(rt.scope.seed))]
    return [
        base + ["census", "--segment", unit] + segs
        + ["--out", str(cen), "--actor", rt.actor],
        base + ["emit", "--segment", unit] + segs
        + ["--census", str(cen), "--actor", rt.actor]
        + (["--dry"] if rt.dry else []),
        # placed_objects IS the ribbon's gate input, and it is derived from the
        # same live census that authorised the removals: one measurement
        # answers both "may I delete this" and "may I move the ground under
        # it".  THE TWO ANSWERS ARE NOT THE SAME ANSWER -- this comment used
        # to claim "the two answers cannot disagree" and they disagree 40 to
        # 1.  MEASURED on Fresh42: of 11,814 kept objects, 296 are property or
        # POI content and 11,518 are CLEARABLE vegetation.  The artefact
        # therefore carries the PREFAB and `ribbon.batter_freezes_on` keys the
        # ground-movement gate on it (clear.py::placed_objects).
        base + ["placed", "--census-glob", str(out / "*.census.json"),
                "--out", str(rt.work / "build" / "placed-objects.json"),
                "--actor", rt.actor],
    ]


def _av_road_ground(rt: Runtime, unit: str) -> list[list[str]]:
    cmd = [PY, str(HERE / "roads" / "ribbon.py"), "build", "--segment", unit,
           "--segments", str(worldseed.segments_path(rt.scope.seed)),
           "--census", str(HERE / "roads" / "census"
                           / f"{rt.scope.world}-census.json"),
           "--placed-objects", str(rt.work / "build" / "placed-objects.json"),
           "--actor", rt.actor]
    return [cmd + (["--validate"] if rt.dry else [])]


# STEP 5'S INSTALLATION HALF IS ONE UNIT PER SITE, keyed exactly as steps 1,
# 2 and 6 key it (`<preset>/<id>`, `_placement_units`), and each body unit
# STANDS ON its own placement unit (`_body_ground`) -- so a site steps 1/2
# refused is BLOCKED BY NAME here, and a site whose own plan refuses is
# refused BY NAME, while its siblings are built and recorded as built.
# `terraform/sites.py --only` builds one site; duplicates and the count
# radius's sibling bound are still resolved against the whole world
# (sites.py::placements, sites.py::sibling_units), so narrowing the call
# narrows what is BUILT and never what is COMPARED.
#
# BODY_UNIT_DEPENDS_ON: the installation SITE is the unit.  THE DEFECT,
# MEASURED on Fresh42 drive 3 (/tmp/regen2/d3_build8.log, step 5):
# `pre-bonemass/iron-era-workshop` was REFUSED at step 1 (clamp: 442 samples
# beyond +/-8 m) and BLOCKED at step 2, and step 5 ran ALL 13 installations
# as ONE unit `*placements*` -- which re-tried it (plan refused rc 4,
# preflight unsheltered_deck), printed "11 of 12 stand", and turned the WHOLE
# unit rc 1: `PARTIAL BUILD ... bodies: ['*placements*']`.  The 11 bodies
# that stood (spawn_plan seq 1990-2000) were journalled as REFUSED, the
# site_prepare refusal reached this step only as a cross-domain DEBT, and
# steps 6-8 saw one refused unit `*placements*` in a domain none of them
# could key against.  A key naming a SUPERSET of what a body depends on.
# The neuter `world` restores that single unit and forgets the pairing.
BODY_UNIT_DEPENDS_ON = ("installation_site",)
_BODY_UNIT_MODE = "site"    # site | world (the shipped single `*placements*`)
ALL_PLACEMENTS = "*placements*"     # the shipped world-wide unit (neuter only)


def _body_units_from(placements: Iterable[str],
                     settlements: Iterable[str]) -> list[str]:
    placements = list(placements)
    if _BODY_UNIT_MODE == "world":
        return ([ALL_PLACEMENTS] if placements else []) + list(settlements)
    return placements + list(settlements)


def _body_ground_from(placements: Iterable[str]
                      ) -> dict[str, dict[str, tuple[str, ...]]]:
    """Each installation body stands on its own placement unit."""
    if _BODY_UNIT_MODE == "world":
        return {}
    return {u: {"placement": (u,)} for u in placements}


def _body_units(s: Scope) -> list[str]:
    return _body_units_from(_placement_units(s),
                            _settlement_units(s) + _declared_units(s))


def _body_ground(s: Scope) -> dict[str, dict[str, tuple[str, ...]]]:
    return _body_ground_from(_placement_units(s))


def _fixture_ground_from(placements: Iterable[str]
                         ) -> dict[str, dict[str, tuple[str, ...]]]:
    """Each installation's fixtures stand on that installation's BODY: a body
    refused at step 5 BLOCKS its own stock instead of being stocked around.
    Under the shipped single unit there is no per-site body to name."""
    if _BODY_UNIT_MODE == "world":
        return {}
    return {u: {"body": (u,)} for u in placements}


def _fixture_ground(s: Scope) -> dict[str, dict[str, tuple[str, ...]]]:
    return _fixture_ground_from(_placement_units(s))


def _av_bodies(rt: Runtime, unit: str) -> list[list[str]]:
    if unit == ALL_PLACEMENTS or unit in _placement_units(rt.scope):
        # `--apply` SPAWNS the plan it can make, through the ledger, and
        # refuses rc 1 naming the site when it cannot: a step that only wrote
        # plans is how 4 'ok' installation bodies were never built.
        return [[PY, str(HERE / "terraform" / "sites.py"), "--flattened",
                 "--out", str(rt.work / "build" / "plans"),
                 "--world", rt.scope.world, "--seed", rt.scope.seed]
                + ([] if unit == ALL_PLACEMENTS else ["--only", unit])
                + ([] if rt.dry else ["--apply", "--actor", rt.actor])]
    declared = _declared_sites(rt.scope)
    if unit in declared:
        # A SITE A PERSON DECLARED is built by the module that owns its kind.
        # The seed and world reach it through the environment, as for step 4;
        # `--actor` is whoever runs this, not a word the module chose.
        module, tail = _declared_builder(declared[unit])
        return [[PY, str(HERE / "settlements" / module), *tail,
                 "--site", unit, "--actor", rt.actor,
                 "--report", str(rt.work / "build" / "declared"
                                 / f"{unit}.json")]
                + (["--dry"] if rt.dry else [])]
    return [[PY, str(HERE / "settlements" / "build.py"),
             "plan" if rt.dry else "apply", "--site", unit,
             "--actor", rt.actor,
             "--world", rt.scope.world, "--seed", rt.scope.seed]]


def _declared_builder(kind: str) -> tuple[str, tuple[str, ...]]:
    settlements = str(HERE / "settlements")
    if settlements not in sys.path:
        sys.path.insert(0, settlements)
    import harbour_site                                     # noqa: PLC0415
    if kind not in harbour_site.BUILDERS:
        raise SystemExit(
            f"REFUSING: a declared site of kind {kind!r} has no builder; "
            f"settlements/harbour_site.py::BUILDERS knows "
            f"{sorted(harbour_site.BUILDERS)}")
    return harbour_site.BUILDERS[kind]


def _av_fixtures(rt: Runtime, unit: str) -> list[list[str]]:
    preset, pid = _split(unit)
    return [[PY, str(HERE / "terraform" / "stock.py"),
             "--preset", preset, "--id", pid,
             "--world", rt.scope.world, "--seed", rt.scope.seed]
            + (["--plan"] if rt.dry else _writer(rt))]


def _av_roadside(rt: Runtime, unit: str) -> list[list[str]]:
    # `--actor` IS NOT OPTIONAL HERE AND THE TOOL SAYS SO.  MEASURED
    # 2026-09-24, the first time step 7 has ever been run on any world:
    # `lamps.py --place` without it refuses outright -- "every record would be
    # appended under 'Lampposts', which is this module's author and not
    # whoever is running it" -- and this row omitted it, so the step was
    # unrunnable by construction and nothing had ever found out.  Both laid
    # segments, S7-stathub-wtnorth and T13-temple-harboursouth, refused rc 1
    # on the argv rather than on anything about the road.
    return [[PY, str(HERE / "roads" / "lamps.py"), "--segments", unit,
             "--actor", rt.actor,
             "--plan" if rt.dry else "--place"]]


def _av_decks(rt: Runtime, unit: str) -> list[list[str]]:
    # ONE CROSSING PER CALL, SELECTED AFTER THE CHANNEL COLLAPSE.  `--spans`
    # narrows what is BUILT, never what is COMPARED: boardwalk collapses the
    # seed's whole declared population into one span per physical channel
    # first, so a unit whose channel another declaration already owns (on
    # Fresh42 T8-stenvik-wttown-x01 is T3-temple-meadhall-x01's water) exits
    # 0 naming the unit that decks it, instead of putting a second deck in
    # the same cubic metres.  `plan` is the offline form: guards, structural
    # verdict and the terrain precondition, nothing appended or sent.  The
    # seed and world reach it through the environment, as for step 4.
    return [[PY, str(HERE / "roads" / "boardwalk.py"),
             "plan" if rt.dry else "build", "--from-network", "--spans", unit,
             "--out", str(rt.work / "build" / "decks" / unit),
             "--actor", rt.actor]]


def _av_recheck(rt: Runtime, unit: str) -> list[list[str]]:
    return [[PY, str(HERE / "roads" / "clear.py"), "census", "--recheck",
             "--segment", unit,
             "--segments", str(worldseed.segments_path(rt.scope.seed)),
             "--out", str(rt.work / "build" / "recheck" / f"{unit}.json"),
             "--actor", rt.actor]]


BUILD_STEPS: tuple[BuildStep, ...] = (
    BuildStep(
        0, "inputs", "pipeline.py stages 7, 10, 11", _inputs, inherited=True,
        evidence="CONSUMES nothing. PRODUCES the four labels the build is "
                 "handed: the solved placements (stage 7), this seed's "
                 "segments (stage 11), the whole-network census (stage 10) "
                 "and -- as the same artefact -- the proof the corridor's "
                 "zones exist, because precensus.py survey generates a zone "
                 "and reads it in the SAME call. Declared as a row so every "
                 "label the build requires has an owner; without it "
                 "audit_graph reports UNOWNED, which is the honest reading of "
                 "a build that expects a human to have made something.",
        # RECORD_STEP_DEPENDS_ON, arm one: the SURVEY, not every road zone.
        # This row read `records=(("road","zones_generate"),)` and step 4's
        # own evidence says the ribbon "generates every zone it touches as
        # its own STEP 0" -- so ONE key owned TWO emitters in two different
        # steps, and `_record_owner_defects` could not see it because only
        # one row spelled the key.  MEASURED on Fresh42's full ledger: 2
        # OUT_OF_ORDER rows, seq 1454 (S7-stathub-wtnorth) and seq 1458
        # (T13-temple-harboursouth), both `zones_generate` of role
        # `road_segment` emitted by ribbon.py INSIDE step 4 and ranked as
        # step 0, therefore reported as arriving 156 and 118 seqs AFTER the
        # step-3 clearing they legitimately follow.  THE BUILD WAS CORRECT
        # AND THE TABLE WAS WRONG.  The two emitters are separable by the
        # role the ledger already carries -- `road_census` is stage 10's
        # whole-NETWORK survey and names no segment, `road_segment` is one
        # segment's own zones -- so the census gets its own kind and this
        # row declares that, and only that.
        records=(("network", "zones_generate"),),
        verify=lambda s: [],
    ),
    BuildStep(
        1, "site_prepare", "clearing/clear.py apply", _pads,
        requires=("placements",),
        evidence="CONSUMES worlds/<World>/<preset>/placements.yaml -- "
                 "requirement.footprint_m, requirement.max_flat_m, "
                 "requirement.location_clearance_m and rotation.yaw "
                 "(clear.py::main -> clearing_area). Those are seed-independent "
                 "declarations available before any terrain is touched, so "
                 "this step depends on nothing the build makes. PRODUCES "
                 "pad_zones (Upgrade World zones_generate) and pad_cleared "
                 "(objects_remove). MEASURED on a sandbox of the same build: a "
                 "fresh world had ghost-generated 81 zones whose _ZoneCtrl all "
                 "sat at (1000000, 0, 1000000) and ZERO vegetation anywhere, so "
                 "a delete-only step has nothing to delete -- generation is a "
                 "step, not a convenience.",
        units=_placement_units, argv=_av_site_prepare, domain="placement",
        records=(("site", "zones_generate"), ("site", "objects_clear")),
        records_ordered=True,
    ),
    BuildStep(
        2, "site_ground", "terraform/flatten.py apply", _pad_ground,
        requires=("pad_zones",),
        evidence="CONSUMES solved.flatten_cost.target_y and pad_zones. "
                 "PRODUCES the levelled pad. THE EDGE IS MEASURED AND IT IS "
                 "THE ONE ORDERING RULE PROVEN BY BREAKING IT: a compiler "
                 "written into an ungenerated zone is ignored by "
                 "Heightmap::ApplyModifiers (it scans INSTANTIATED "
                 "TerrainComp::s_instances, and a server with no peers "
                 "instantiates none), so the zone planted all 190 objects on "
                 "the unmodified generated height, up to 7.68 m above the "
                 "finished pad, permanently, and ignored the paint's cleared "
                 "alpha. flatten.py apply now REFUSES a zone with no _ZoneCtrl "
                 "at its centre. AND IT DOES NOT REQUIRE pad_cleared: MEASURED "
                 "on two pads 256 m apart the same day, generate->clear->flatten "
                 "and generate->flatten->clear both ended with the same two "
                 "survivors (_TerrainCompiler, _ZoneCtrl), because "
                 "objects_remove's pos/max filter is a vertical cylinder on "
                 "Utils.DistanceXZ with no height bound. So that edge is NOT "
                 "declared, and reordering this step against a step it does not "
                 "consume must SURVIVE.",
        units=_placement_units, argv=_av_site_ground, domain="placement",
        records=(("site", "terrain_write"),),
    ),
    BuildStep(
        3, "road_clear", "roads/clear.py census|emit|placed", _corridor,
        requires=("segments", "census", "corridor_zones"),
        evidence="CONSUMES the segment corpus and a LIVE census taken in the "
                 "same call (a census taken minutes earlier is a census of a "
                 "different world). PRODUCES corridor_cleared and "
                 "placed_objects -- every object we did NOT put there and will "
                 "NOT remove, by position. Requires corridor_zones for the same "
                 "measured reason step 1 generates before it deletes.",
        units=_segment_units, argv=_av_road_clear, domain="segment",
        records=(("road", "objects_clear"),),
    ),
    BuildStep(
        4, "road_ground", "roads/ribbon.py build --census", _road_ground,
        requires=("placed_objects", "segments", "census"),
        evidence="CONSUMES placed_objects, AND THE EDGE IS IN THE TOOL'S OWN "
                 "ARGUMENT PARSER: ribbon.py's --placed-objects help says "
                 "'Without this file the batter is REFUSED, because an ungated "
                 "batter is that defect at scale' -- MEASURED by SeatCheck, the "
                 "hub portal u-lheast already floats 0.536 m because a ribbon "
                 "cut under it after it was seated. PRODUCES the graded, paved "
                 "ribbon: one _TerrainCompiler per zone carrying a TCData blob, "
                 "because piece_pavedroad has no persistent ZNetView and zero "
                 "road pieces exist in any fleet save. GENERATES EVERY ZONE "
                 "IT TOUCHES AS ITS OWN STEP 0 and OBSERVES a _ZoneCtrl "
                 "before writing -- that generate is a record of THIS step, "
                 "emitted immediately before the write it enables, and it is "
                 "declared below in that order.",
        units=_segment_units, argv=_av_road_ground, domain="segment",
        records=(("road", "zones_generate"), ("road", "terrain_write")),
        records_ordered=True,
    ),
    BuildStep(
        5, "bodies", "terraform/sites.py --apply + settlements/build.py apply", _bodies,
        requires=("pad_ground", "road_ground", "placements"),
        evidence="CONSUMES pad_ground: the blueprint's FLOOR PLANE lands on "
                 "flatten_cost.target_y (to_rcon_plan.py --align floor-center); "
                 "solved.y is the HIGHEST terrain cell in the footprint and was "
                 "never the right altitude once the pad exists -- the old "
                 "ground-center alignment floated pre-bonemass/iron-era-workshop "
                 "by 1.50 m. CONSUMES road_ground FOR THE SECOND HALF OF THE "
                 "SAME RULE: a body may declare flatten: FORBIDDEN, and "
                 "writer._flatten_forbidden_check then refuses every "
                 "terrain_write whose footprint overlaps it, with NO owner "
                 "exception -- it excludes only RETIRED records. So the instant "
                 "a deck stands it owns the ground that would free its own "
                 "buried tiles. EVERY TERRAIN WRITE PRECEDES EVERY PIECE, and "
                 "that is why this row requires both ground labels. A SITE A "
                 "PERSON DECLARED (worldseed.HUMAN_SITES: Ulfsgard, Sudrvik) "
                 "is one of this row's units, built by the module that owns "
                 "its kind (harbour_site.BUILDERS) after the ground of every "
                 "road and before the fixtures, decks and lamps that stand on "
                 "it. PRODUCES the standing bodies.",
        units=_body_units, argv=_av_bodies, domain="body",
        # An installation body stands on its own placement unit, so a site
        # steps 1/2 refused is BLOCKED here by name -- see
        # BODY_UNIT_DEPENDS_ON.  A settlement names no placement.
        stands_on=_body_ground,
        # A BRIDGE BODY IS NOT HERE ANY MORE.  `("road", "spawn_plan")`,
        # `("road", "spawn")` and `("road", "portal")` were declared on this
        # row because the first replay of Ulfsland's ledger reported one
        # UNCLASSIFIED record, seq 5389, a `bridge_body_place` spawn -- the
        # honest answer while no step built a crossing.  A bridge's records
        # are kind `bridge` and belong to step 6.5 `decks`; no other road
        # role on either ledger spawns a piece (MEASURED: Ulfsland's and
        # Fresh42's road-role piece records are 87 + 1 + 3 bridge records and
        # nothing else), so a future one reads UNCLASSIFIED, which is right.
        records=(("site", "spawn_plan"),),
    ),
    BuildStep(
        6, "fixtures", "terraform/stock.py", _fixtures,
        requires=("bodies", "world_settings"),
        evidence="CONSUMES the standing body: positions are searched against "
                 "the body's REAL SOLIDS (blueprints/fixtures.free_spot, "
                 "fixtures.carried). MEASURED on pre-bonemass/iron-era-workshop "
                 "against all 1,906 solids of the body placed before it: 12 of "
                 "18 fixtures were INSIDE the structure, portal_wood overlapped "
                 "a stone_wall_4x2 by 1.00 x 2.00 x 1.18 m, piece_maypole "
                 "pierced 22 solids, and the minimum fixture-to-wall gap was "
                 "0.000 m because nothing was checking. PRODUCES the stations, "
                 "props, chests and portals.",
        units=_placement_units, argv=_av_fixtures, domain="placement",
        # Stock stands on its own site's body: a body refused at step 5 blocks
        # its fixtures by name rather than being stocked around.
        stands_on=_fixture_ground,
        records=(("site", "spawn"), ("site", "portal")),
    ),
    BuildStep(
        6.5, "decks", "roads/boardwalk.py build --from-network --spans", _decks,
        requires=("crossings", "road_ground", "bodies"),
        evidence="CONSUMES crossings: one unit per crossing the seed's own "
                 "crossings file declares (boardwalk.crossing_corpus, the "
                 "population the gate judges). CONSUMES road_ground, AND THE "
                 "EDGE IS IN THE TOOL: boardwalk --require-terrain (default "
                 "ON) refuses a span whose abutment segment has no bed-moving "
                 "record, because a deck's abutment is a step from the road "
                 "BENCH and on an ungraded road there is none -- so a crossing "
                 "whose segment step 4 refused is BLOCKED by name (see "
                 "STANDS_ON_DEPENDS_ON). CONSUMES bodies FOR THE RULE THIS "
                 "TABLE EXISTS FOR: every deck record carries flatten: "
                 "FORBIDDEN, and step 5 still WRITES GROUND -- MEASURED on "
                 "Fresh42, 33 site_pad terrain_writes stamped step 5 -- so a "
                 "deck placed before it would refuse a settlement pad in its "
                 "footprint; MEASURED on Ulfsland, regrade_cut's 16 writes at "
                 "seq 5393-5415 came after the deck at 5383-5388 and collided "
                 "with 17 seqs. EVERY TERRAIN WRITE PRECEDES EVERY PIECE. "
                 "PRODUCES the decks. THE STEP THAT DID NOT EXIST: none of "
                 "the 8 steps built a crossing, so every declared crossing on "
                 "a fresh world stayed a hole (MEASURED on Fresh42: 9 "
                 "declared, S4-meadhall-southeast-x01 carries 0 of 27 m and "
                 "the southeast is unreachable without it). Every one of "
                 "Ulfsland's 22 bridge units was laid by a session agent "
                 "running a tool by hand -- 20 boardwalk decks under five "
                 "actors (RoadNorthLand, RoadsReach, BuildDecks, DeckBuild, "
                 "OrderRun) and S1/S2 by crossings/build_live.py -- so a "
                 "from-scratch regen of that world would come back bridgeless.",
        units=_crossing_units, argv=_av_decks, domain="crossing",
        stands_on=_crossing_ground,
        # NOT a sequence: `bridge-s1-temple-strait` is TWO structures under
        # one site_id (generate, clear, place, then generate, clear, place --
        # Ulfsland seq 12-20), so a within-step order would call the second
        # half's generate OUT_OF_ORDER against the first half's deck.
        records=(("bridge", "zones_generate"), ("bridge", "objects_clear"),
                 ("bridge", "spawn_plan"), ("bridge", "spawn")),
    ),
    BuildStep(
        7, "roadside", "roads/lamps.py --place", _roadside,
        requires=("road_ground", "bodies", "decks"),
        evidence="CONSUMES road_ground: a lamp's datum is recorded as 'each "
                 "lamp's own applied surface + 0.65360 m, the prefab's measured "
                 "pivot offset', and its own record carries flatten: FORBIDDEN "
                 "with the reason 'lamps stand ON the road the segment already "
                 "graded'. CONSUMES bodies because a lamp sited where a "
                 "building stands is the same collision in the other direction. "
                 "CONSUMES decks FOR THE SAME REASON, AND THE TOOL SAYS SO: "
                 "lamps.py keeps out of 'ribbon.protected_piece_positions for "
                 "bridge and harbour decks', which reads the decks the ledger "
                 "holds -- a lamp sited before its deck stands is sited "
                 "against a crossing with nothing on it. "
                 "PRODUCES the roadside furniture -- and this is the step whose "
                 "records ARE 14 of Ulfsland's 47 walker-blocking bodies, which "
                 "is why step 8 exists and requires it.",
        units=_segment_units, argv=_av_roadside, domain="segment",
        records=(("roadside", "spawn_plan"), ("roadside", "spawn"),
                 ("roadside", "portal")),
    ),
    BuildStep(
        8, "recheck", "roads/clear.py census (re-read)", _verdict,
        requires=("fixtures", "roadside", "decks", "corridor_cleared"),
        evidence="CONSUMES the FINISHED world: every body, fixture and lamp. "
                 "PRODUCES the only reading of a corridor taken after the "
                 "things that block it were placed. THE STEP THAT DID NOT "
                 "EXIST: ribbon.py re-checks coverage and census freshness AT "
                 "THE WRITE, which is before this furniture stands, so the 14 "
                 "walker-blocking bodies we placed ourselves were invisible to "
                 "every check in the tree. Requiring fixtures and roadside is "
                 "what makes 'placed after the road with nothing re-reading the "
                 "corridor' unspellable: a unit with pieces and no recheck "
                 "record is reported UNVERIFIED. A DECK IS RE-READ WITH THE "
                 "ROAD IT CARRIES: its unit is a crossing, not a segment, so "
                 "it is owed the recheck of the segment(s) it stands on, "
                 "taken AFTER its last piece -- the decks at Ulfsland seq "
                 "2803-2867 are in the 14 bodies nothing re-read.",
        units=_segment_units, argv=_av_recheck, domain="segment",
        records=(("road", "corridor_recheck"),),
    ),
)


# ---------------------------------------------------------------------------
# THE SEQUENCING CHECK -- INHERITED, NOT REIMPLEMENTED
# ---------------------------------------------------------------------------
def audit() -> list[str]:
    """`pipeline.audit_graph` on THIS table.  Same function, same two defects.

    It reads `ordinal`, `name`, `requires` and `produces(scope)` and nothing
    else, which is why `BuildStep` spells those four exactly as `Stage` does.
    """
    return pipeline.audit_graph(BUILD_STEPS)                # type: ignore[arg-type]


def _record_owner_defects(steps: tuple[BuildStep, ...] | None = None
                          ) -> list[str]:
    """Two steps claiming one record shape is a key naming a SUPERSET.

    The replay's rank comes from `records`, so a (kind, op) pair owned twice
    makes one of the two steps unobservable -- which is the same defect as an
    unowned requirement, one level down.
    """
    seen: dict[tuple[str, str], BuildStep] = {}
    bad: list[str] = []
    # THE TABLE IS A PARAMETER so a neuter can hand in the SHIPPED table and
    # watch this refuse it.  With the census folded back into kind `road`,
    # step 0 `inputs` and step 4 `road_ground` both claim
    # `("road","zones_generate")` and this returns DOUBLE-OWNED -- which is
    # the defect that produced Fresh42's 2 OUT_OF_ORDER rows, now unloadable
    # rather than merely corrected.  See RECORD_KIND_DEPENDS_ON.
    for st in (steps if steps is not None else BUILD_STEPS):
        for key in st.records:
            other = seen.get(key)
            if other is not None:
                bad.append(f"DOUBLE-OWNED: record {key} is claimed by step "
                           f"{other.ordinal} {other.name!r} and by step "
                           f"{st.ordinal} {st.name!r}")
            seen[key] = st
    return bad


# ---------------------------------------------------------------------------
# EVERY CORPUS THE BUILD IS HANDED HAS A BUILDER
# ---------------------------------------------------------------------------
# UNBUILT_DEPENDS_ON: the labels PIPELINE ROW 12 hands this build, and the
# rows of this table that consume each one with a command.
#
# THE DEFECT, MEASURED.  `pipeline.py` row 12 `build` REQUIRES `crossings`
# (stage 11's crossing contract), so the pipeline has always handed every
# declared crossing to this table -- and until 2026-09-25 no row of it
# consumed that label.  `audit_graph` could not see it: it refuses a label
# a step REQUIRES and nobody produces, never a label that is PRODUCED and
# nobody requires.  So the build ran every step it had, exited 0 on the
# steps it ran, and left every declared crossing a hole: Fresh42 declares
# 9, and S4-meadhall-southeast-x01 carries 0 of 27 m.  The same shape as
# UNOWNED, in the other direction -- WORK NOBODY DOES.
#
# `save` is the one handed label that is not a corpus: it is the world
# every step writes INTO, not a list of units any step builds.
UNBUILT_DEPENDS_ON = ("handed_labels", "consuming_step")
_UNBUILT_MODE = "real"      # real | off (look at nothing)
#                           #      | no_decks (the shipped 9-row table)
DECK_STEP = "decks"
_WORLD_LABEL = "save"
_UNIT_NOUN = {"crossings": "crossing", "placements": "placement",
              "segments": "segment"}


def _build_stage() -> "pipeline.Stage":
    """The pipeline row that runs THIS file, found by its owner."""
    return next(st for st in pipeline.STAGES
                if st.owner.split()[0] == Path(__file__).name)


def unbuilt_defects(steps: tuple[BuildStep, ...] | None = None,
                    handed: tuple[str, ...] | None = None) -> list[str]:
    """Every label the pipeline hands the build that no step BUILDS.

    Two lines, different defects: HANDED-UNDECLARED is a label this table
    does not even list as an input (the shipped state for `crossings`),
    UNBUILT is one it lists and no step with a command consumes (the state
    the neuter `deck step dropped` restores).  Both say what is left unbuilt
    in the words the gate would use for it.
    """
    if _UNBUILT_MODE == "off":
        return []
    if steps is None:
        steps = (BUILD_STEPS if _UNBUILT_MODE == "real" else
                 tuple(s for s in BUILD_STEPS if s.name != DECK_STEP))
    row = _build_stage()
    if handed is None:
        handed = tuple(lab for lab in row.requires if lab != _WORLD_LABEL)
    probe = Scope("Selftest", "Selftest", Path("/nonexistent"),
                  Path("/nonexistent"), Path("/nonexistent"))
    declared = {a.label for st in steps if st.inherited
                for a in st.produces(probe)}
    bad: list[str] = []
    for lab in handed:
        noun = _UNIT_NOUN.get(lab, f"{lab!r} entry")
        if lab not in declared:
            bad.append(f"HANDED, UNDECLARED: pipeline stage {row.ordinal} "
                       f"{row.name!r} hands the build {lab!r} and no row of "
                       f"this table declares it as an input -- a declared "
                       f"{noun} with no builder")
            continue
        if not any(lab in st.requires for st in steps
                   if not st.inherited and st.argv is not None):
            bad.append(f"UNBUILT: pipeline stage {row.ordinal} {row.name!r} "
                       f"hands the build {lab!r} and no step with a command "
                       f"consumes it -- a declared {noun} with no builder")
    return bad


_DEFECTS = audit() + _record_owner_defects() + unbuilt_defects()
if _DEFECTS:                                                # pragma: no cover
    raise SystemExit("REFUSING to load the build table:\n  "
                     + "\n  ".join(_DEFECTS))


# RECORD_SEQUENCE_DEPENDS_ON: the within-step index is part of the rank ONLY
# for a step that declares `records_ordered`.  The neuter `every_step_ordered`
# restores the shipped reading (every tuple a sequence), which must bring
# Fresh42's 38 `fixtures`-after-`fixtures` rows back.
RECORD_SEQUENCE_DEPENDS_ON = ("records_ordered",)


def rank_table(steps: tuple[BuildStep, ...],
               depends_on: tuple = RECORD_SEQUENCE_DEPENDS_ON
               ) -> dict[tuple[str, str], tuple[int, int]]:
    """(kind, op) -> (step ordinal, index within the step), DERIVED from the
    table.  The index is 0 for every record of a step whose `records` is not a
    declared sequence, so two of its records never out-rank each other.  A
    parameter so a neuter arm can replay against a variant table without a
    second implementation of the derivation.
    """
    honour = "records_ordered" in depends_on
    return {key: (st.ordinal, i if (st.records_ordered or not honour) else 0)
            for st in steps for i, key in enumerate(st.records)}


_RANK: dict[tuple[str, str], tuple[int, int]] = rank_table(BUILD_STEPS)
# The ordinals of every step in the terminal step's domain -- see
# RECHECK_DOMAIN_DEPENDS_ON.  The terminal step is the LAST row of the table.
_RECHECK_DOMAIN_STEPS: frozenset[int] = frozenset(
    st.ordinal for st in BUILD_STEPS if st.domain == BUILD_STEPS[-1].domain)


# ---------------------------------------------------------------------------
# THE REPLAY, AND WHAT IT DEPENDS ON
# ---------------------------------------------------------------------------
# Each name is a thing `replay()` actually reads.  Every one is neutered IN
# BOTH DIRECTIONS: an OFF arm that makes the check stop looking -- which only a
# must-REFUSE case can flip under, because a check that looks at nothing
# accepts everything -- and a WRONG arm that makes it look wrongly, which is
# what a must-ACCEPT case flips under.  A one-direction neuter is passed by an
# index that returned NOTHING.
REPLAY_DEPENDS_ON = ("step_rank", "record_kind", "unit_key", "ordinal",
                     "terminal_step", "ground_freeze", "ground_freeze_scope",
                     "world_scope", "transport_world")

_RANK_MODE = "real"         # real | off (collapse) | blind (name nothing)
_UNIT_MODE = "real"         # real | off (unique per record) | global (one key)
_ORDER_MODE = "real"        # real | off | reversed
_TERMINAL_MODE = "real"     # real | off | always
# RECHECK_DOMAIN_DEPENDS_ON: the steps whose unit domain the terminal step
# re-reads.  DERIVED from the table, so a step added in that domain is owed a
# recheck without anyone remembering to say so.  The neuter `all` restores the
# shipped superset: every unit that placed a piece is owed one.
RECHECK_DOMAIN_DEPENDS_ON = ("terminal_step_domain",)
_RECHECK_MODE = "domain"     # domain | all
_FREEZE_MODE = "real"       # real | off | always
# GROUND_FREEZE_SCOPE_DEPENDS_ON, the neuter.  `world` restores the shipped
# superset -- every write after the world's FIRST piece anywhere, which MUST
# bring Fresh42's 42 and Ulfsland's 191 back.  `none` makes the zone test
# answer NO for every write, which a must-REFUSE case flips under.  Both arms
# named because a SCOPE is a dependency: it says what the finding is about.
_FREEZE_SCOPE = "zone"      # zone | world (shipped superset) | none
# TRANSPORT_WORLD_DEPENDS_ON, the neuter.  `literal` restores the constant
# terraform/rcon.py carried until 2026-09-24 -- the run resolves its ledger
# world and the socket still answers Ulfsland -- and `blind` stops comparing
# the two at all, which is the shipped state in the other direction: the
# comparison did not exist.
_TRANSPORT_MODE = "real"    # real | literal (socket stuck on one world)
                            #      | blind (nothing compares the two)
# RECORD_KIND_DEPENDS_ON: the ROLE, and the SCOPE that role works at.
# `merged` folds the network survey back into `road`, which is the shipped
# state and MUST bring the 2 Fresh42 OUT_OF_ORDER rows back.  `site` makes
# nothing a road at all, which must make the correct case UNCLASSIFIED --
# the arm that proves the kind is READ and not decoration.  `bridge_as_road`
# folds the bridge roles back into `road`, the shipped reading before step
# 6.5 existed, and MUST bring Ulfsland's 4 bridge OUT_OF_ORDER rows back.
_KIND_MODE = "real"         # real | merged (network -> road) | site (no roads)
#                           #      | bridge_as_road (no bridge kind)

# STANDS_ON_DEPENDS_ON: the ground a crossing's deck stands on -- the
# segments of its two banks -- read at BUILD time off the seed's crossings
# corpus (`BuildStep.stands_on`) and at REPLAY time off the deck's own
# records (`stands_on_of`).  Two readers, one quantity, one neuter: `off`
# forgets it in both, which is the shipped state -- a refused segment was a
# DEBT the deck on it built through, and a deck was NO_RECHECK_STEP.
_STANDS_ON_MODE = "real"    # real | off

# Which ledger roles belong to which kind.  The ROLE is what the ledger already
# records per operation; the kind is the only thing the rank needs from it.
ROADSIDE_ROLES = frozenset({"road_lighting", "waypoint_sign", "boat_spawn_moor"})
ROAD_ROLES = frozenset({"road_segment", "road_surface_clearing", "regrade_cut",
                        "ford"})
# A BRIDGE IS NOT A ROAD RECORD.  These two roles were in ROAD_ROLES, so a
# bridge's `zones_generate` ranked as step 4 and its `objects_clear` as step
# 3: MEASURED on Ulfsland, `bridge-s1-temple-strait` and
# `bridge-s2-portalhall-strait` (Crossings, seq 12-23: generate, clear,
# place, in the proven order) read as 4 OUT_OF_ORDER rows -- a clear "after"
# a road grade that never happened.  Their own kind gives them their own step.
BRIDGE_ROLES = frozenset({"bridge_assembly", "bridge_body_place"})
# THE SURVEY IS NOT A SEGMENT.  Registered in ledger/schema.py by CensusRun on
# 2026-09-23 for stage 10: `precensus.py survey` had never once been run
# through the ledger on ANY world, so its zones_generate records had no role
# until now.  It was put in ROAD_ROLES, which made `kind_of` answer "road" for
# it and gave step 0 `inputs` and step 4 `road_ground` THE SAME RANK KEY
# `("road","zones_generate")` for two different emitters -- the defect this
# kind exists to separate.  MEASURED on Fresh42: 568 records carry
# `road_census` and NONE of them names a segment; 2 carry `road_segment`
# zones_generate and BOTH name one.  A key that cannot tell those apart names
# a SUPERSET of what the step attribution depends on.
NETWORK_ROLES = frozenset({"road_census"})

# Ops that move nothing and therefore carry no order.  DECLARED, not dropped:
# `retire` is a REPAIR that names the seq it retires, `save` is a flush, and
# `observe`/`note`/`world_manifest`/`chain_reset`/`zones_restore` are readings.
NON_ORDERING_OPS = frozenset({"observe", "note", "save", "retire", "modify",
                              "world_manifest", "chain_reset", "zones_restore"})
# The ops that put a PIECE in the world.  `ground_freeze` is expressed over
# these because `flatten: FORBIDDEN` is a claim on ground made world-wide, not
# per unit: writer._flatten_forbidden_check scans every live record.
PIECE_OPS = frozenset({"spawn_plan", "spawn", "portal"})
GROUND_OPS = frozenset({"terrain_write"})

RECHECK_ROLE = "corridor_recheck"


# ---------------------------------------------------------------------------
# WHERE A STEP IS ALLOWED TO WRITE
# ---------------------------------------------------------------------------
# THE FIRST THING THIS ENTRY POINT FOUND, AND IT FOUND IT BY DOING IT.
# MEASURED 2026-09-23: `buildstage.py run --world Fresh42 --dry --through 2`
# completed rc 0 in 101 s and wrote THIRTEEN Fresh42 terrain-delta entry files
# into /media/big4/.../Ulfsland/config_merged/bepinex/data -- the WATCHED data
# directory of a different, finished world -- because `terraform/flatten.py`
# resolved that directory from a module constant naming one install, and
# because `write_entries` ran BEFORE `if args.op == "plan": return 0`.  A plan
# that drops live definitions into another world's watched directory is not a
# plan, and an install path that does not carry the world is a key naming a
# SUPERSET of what the quantity depends on.  Both are fixed in flatten.py;
# this is the check, and it FAILS on the known-bad resolver.
_SCOPE_MODE = "real"            # real | off | always


def world_scope_defects(resolver: Callable[[str], Path],
                        worlds: tuple[str, ...] = ("Ulfsland", "Fresh42"),
                        declared: dict[str, str] | None = None
                        ) -> list[str]:
    """Every way `resolver` fails to key a write path on its world.

    TWO defects, and they are different: SHARED is two worlds landing in one
    directory, which is the destructive one; UNNAMED is a path that does not
    carry the world at all, which is how SHARED goes unnoticed.

    `declared` is the resolver's OWN record of paths an operator named for a
    specific world -- `{world: path}`.  A path only has to carry the world in
    its text when nothing else attributes it; an explicit declaration IS the
    attribution, and demanding the substring as well would forbid every
    install that is not laid out as `<root>/<World>/...`.  MEASURED
    2026-09-23: Fresh42's server is a sandbox at /tmp/cr/vh, so its watched
    data directory cannot contain the word Fresh42 and the world's own
    `config_merged/bepinex/data` -- which does -- is watched by nothing.
    SHARED is unaffected and is still the destructive half: two worlds
    resolving to one directory is refused however either was named.
    """
    if _SCOPE_MODE == "off":
        return []
    bad: list[str] = []
    seen: dict[str, str] = {}
    named = declared or {}
    for w in worlds:
        p = str(resolver(w))
        if _SCOPE_MODE == "always" or (w not in p.split("/")
                                       and named.get(w) != p):
            bad.append(f"UNNAMED: the write path for world {w!r} is {p!r}, "
                       f"which does not carry that world")
        other = seen.get(p)
        if other is not None:
            bad.append(f"SHARED: worlds {other!r} and {w!r} both write into "
                       f"{p!r} -- one slot for every world")
        seen[p] = w
    return bad


# ---------------------------------------------------------------------------
# THE SAME QUESTION, ASKED OF THE LEDGER
# ---------------------------------------------------------------------------
# LEDGER_WORLD_DEPENDS_ON: the world the run is driving, and nothing else.
#
# `Ledger.open(world, ...)` CREATES `ledger/runs/<world>/` and appends a
# `world_manifest` when the chain is empty, so a hard-coded world there is a
# WRITE into that world, not a read of it.  This project holds exactly one
# untouchable ledger -- Ulfsland, 26,619,627 bytes -- and
# `roads/regrade.py:548` and `:637` opened it FROM A LITERAL, so running
# regrade.py on ANY world appended to the quarantined chain.  Same class as
# `LiveBuilder(world="Ulfsland")` (36 of 40 callsites passing none) and
# `applied.Applied(world="Ulfsland")` (17 of 19), both already closed.
#
# THE CHECK IS NOT A GREP, and it must not be: `"Ulf" + "sland"` is the same
# defect and a substring search passes it.  Each `Ledger.open` call's FIRST
# ARGUMENT is parsed out and EVALUATED TWICE, in a namespace where every free
# name resolves to the world being asked about.  An expression that names
# anything at all -- `WORLD`, `_world(args)`, `world or WORLD`, `TC.WORLD` --
# answers differently in the two arms.  A constant answers the same both
# times, and `world_scope_defects` then reports it SHARED (two worlds, one
# ledger) and UNNAMED.  One resolver, the same two defects, the same neuter.
_LEDGER_OPEN = re.compile(r"Ledger\s*\.\s*open\s*\(")


class _Scoped:
    """Any free name, standing for `the world this run is driving`."""

    def __init__(self, world: str):
        self._world = world

    def __getattr__(self, _name: str) -> "_Scoped":
        return self

    def __call__(self, *_a, **_k) -> "_Scoped":
        return self

    def __bool__(self) -> bool:
        return True

    def __str__(self) -> str:
        return self._world


class _ScopedNames(dict):
    def __missing__(self, key: str):
        return _Scoped(self["__world__"])


def ledger_world_calls(root: Path | None = None) -> list[tuple[str, int, str]]:
    """(file, line, first-argument source) for every `Ledger.open` in the tree.

    Parsed, never matched: the argument is an AST node, so a call spread over
    three lines is one row and a comment mentioning the call is none.
    """
    base = Path(root) if root else HERE
    out: list[tuple[str, int, str]] = []
    for path in sorted(base.rglob("*.py")):
        rel = str(path.relative_to(base))
        if rel.startswith("library/staging/"):
            continue
        src = path.read_text(errors="replace")
        if not _LEDGER_OPEN.search(src):
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:                                 # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if not (isinstance(f, ast.Attribute) and f.attr == "open"):
                continue
            owner = f.value
            name = (owner.attr if isinstance(owner, ast.Attribute)
                    else owner.id if isinstance(owner, ast.Name) else "")
            if not name.endswith("Ledger"):
                continue
            arg = (ast.unparse(node.args[0]) if node.args
                   else next((ast.unparse(k.value) for k in node.keywords
                              if k.arg == "world"), ""))
            out.append((rel, node.lineno, arg))
    return out


def ledger_world_defects(root: Path | None = None,
                         calls: list[tuple[str, int, str]] | None = None
                         ) -> list[str]:
    """`world_scope_defects` on every ledger the tree opens."""
    bad: list[str] = []
    for rel, line, arg in (calls if calls is not None
                           else ledger_world_calls(root)):
        def resolve(w: str, _a=arg) -> Path:
            if not _a:
                # NO WORLD ARGUMENT AT ALL falls to `Ledger.open`'s own
                # default, which resolves `worldseed.world()`.
                return Path("ledger/runs") / w
            ns = _ScopedNames(__world__=w)
            try:
                return Path("ledger/runs") / str(eval(_a, ns))   # noqa: S307
            except Exception:                               # pragma: no cover
                # UNREADABLE IS NOT CLEAR: an expression this cannot evaluate
                # is reported as one fixed slot, which refuses.
                return Path("ledger/runs") / "UNREADABLE"
        bad += [f"{rel}:{line} `{arg}`: {d}"
                for d in world_scope_defects(resolve)]
    return bad


def kind_of(role: str) -> str:
    """The SCOPE a role works at: one roadside fixture, one road segment, one
    network-wide survey, one crossing's bridge, or one site.  See
    RECORD_KIND_DEPENDS_ON.
    """
    if _KIND_MODE == "site":
        return "site"
    if role in ROADSIDE_ROLES:
        return "roadside"
    if role in BRIDGE_ROLES:
        return "road" if _KIND_MODE == "bridge_as_road" else "bridge"
    if role in NETWORK_ROLES:
        return "road" if _KIND_MODE == "merged" else "network"
    if role in ROAD_ROLES:
        return "road"
    return "site"


def role_rank_of(rec: dict) -> tuple[int, int] | None:
    """The step the record's ROLE names -- the reading for records written
    before `meta.build_step` existed, and the cross-check for those after."""
    op = rec.get("op") or ""
    role = ((rec.get("params") or {}).get("role")) or ""
    if op == "observe" and role == RECHECK_ROLE:
        key = ("road", RECHECK_ROLE)
    elif op in NON_ORDERING_OPS:
        return None
    else:
        key = (kind_of(role), op)
    return _RANK.get(key)


# STEP_ATTRIBUTION_DEPENDS_ON: which step wrote a record.  `meta.build_step`
# is stamped by the build that ran the command (see ledger/live.py), so it is
# a measurement; the role is an inference that is wrong wherever one role has
# two emitters (MEASURED: `waypoint_sign`, written by step 5's settlement
# guideposts and by step 7's roadside).  The neuter `role` restores the
# shipped reading and must bring 15 settlements back as UNVERIFIED.
STEP_ATTRIBUTION_DEPENDS_ON = ("stamped_build_step",)
_ATTRIBUTION_MODE = "stamp"   # stamp | role


def stamped_rank_of(rec: dict) -> tuple[int, int] | None:
    """The step the build STAMPED on the record, or None when it carries none.
    Within an ordered step the index is the op's place in its declaration."""
    raw = ((rec.get("meta") or {}).get("build_step"))
    if _ATTRIBUTION_MODE != "stamp" or raw is None:
        return None
    # A NUMBER, NOT AN INTEGER: step 6.5 stamps "6.5".  Matched by value, so
    # every "5" already in a ledger still names step 5.
    try:
        ordinal = float(str(raw).split(":", 1)[0])
    except ValueError:
        return None
    st = next((s for s in BUILD_STEPS if s.ordinal == ordinal), None)
    if st is None:
        return None
    if not st.records_ordered:
        return (st.ordinal, 0)
    ops = [op for _, op in st.records]
    op = rec.get("op") or ""
    return (st.ordinal, ops.index(op) if op in ops else 0)


def rank_of(rec: dict) -> tuple[int, int] | None:
    """Which declared step this record is, or None when the table cannot name
    it.  None is REPORTED as UNCLASSIFIED and never treated as clean.  The
    stamped step wins; the role is the fallback.
    """
    if _RANK_MODE == "blind":
        return None
    if _RANK_MODE == "off":
        return (0, 0)
    op = rec.get("op") or ""
    role = ((rec.get("params") or {}).get("role")) or ""
    if op in NON_ORDERING_OPS and not (op == "observe" and role == RECHECK_ROLE):
        return None
    return stamped_rank_of(rec) or role_rank_of(rec)


def unit_of(rec: dict) -> str:
    """The piece of ground this record belongs to, or "" when it names none.

    `params.site_id` alone names a SUBSET: MEASURED on Ulfsland, all 292
    `zones_generate`, 100 `terrain_write` and 2,041 `objects_clear` records of
    the road roles carry no `site_id` at all -- the segment is in `meta.segment`
    for the ribbon and nowhere machine-readable for the clearing.  So both are
    read, in that order, and a record that answers neither is "" -- which the
    replay reports rather than skipping, because UNREAD IS NOT CLEAR.
    """
    if _UNIT_MODE == "global":
        return "*"
    if _UNIT_MODE == "off":
        return f"seq{rec.get('seq')}"
    p = rec.get("params") or {}
    m = rec.get("meta") or {}
    for v in (p.get("site_id"), m.get("segment"), p.get("segment")):
        if isinstance(v, str) and v:
            return v
    return ""


def stands_on_of(rec: dict) -> frozenset[str]:
    """The units of ANOTHER domain this record's unit stands on, as the record
    itself states them.  See STANDS_ON_DEPENDS_ON.

    `meta.stands_on` is what `roads/boardwalk.py` stamps on every deck batch
    (the segments of both banks).  Decks laid before that stamp existed say it
    once, on batch 1, as `meta.span_measurement.segment` -- MEASURED on
    Ulfsland: all 20 boardwalk decks carry it, and S1/S2 (Crossings'
    hand-declared bridges, no segment) carry neither, so they name nothing.
    """
    if _STANDS_ON_MODE == "off":
        return frozenset()
    m = rec.get("meta") or {}
    got = m.get("stands_on")
    if isinstance(got, (list, tuple)):
        return frozenset(g for g in got if isinstance(g, str) and g)
    seg = (m.get("span_measurement") or {}).get("segment")
    return frozenset([seg]) if isinstance(seg, str) and seg else frozenset()


def _later(a: tuple[int, int], b: tuple[int, int]) -> bool:
    """Is rank `a` declared LATER than rank `b`?"""
    if _ORDER_MODE == "off":
        return False
    if _ORDER_MODE == "reversed":
        return a < b
    return a > b


ZONE_M = 64.0


def piece_zone(rec: dict) -> tuple[int, int] | None:
    """The 64 m zone a PIECE stands in, or None when the record does not say.

    None is not "nowhere": callers MUST read it as unreadable and fail closed.
    MEASURED 2026-09-24: 11 of Fresh42's 23 piece records and 437 of
    Ulfsland's 764 carry no `params.pos` at all -- a `spawn_plan` states its
    body's position as `params.anchor {x, y, z, yaw}` -- so a reader that
    knows only `pos` answers "no piece anywhere" for nearly half the world
    and every ordering question against it comes back clean.  UNREAD IS NOT
    CLEAR: that reading turned an 18-row finding into a 0.
    """
    p = rec.get("params") or {}
    a = p.get("anchor")
    if isinstance(a, dict) and "x" in a and "z" in a:
        try:
            return (round(float(a["x"]) / ZONE_M), round(float(a["z"]) / ZONE_M))
        except (TypeError, ValueError):
            return None
    for key in ("pos", "from"):
        v = p.get(key)
        if isinstance(v, (list, tuple)) and len(v) >= 3:
            try:
                return (round(float(v[0]) / ZONE_M), round(float(v[2]) / ZONE_M))
            except (TypeError, ValueError):
                return None
    return None


def write_zones(rec: dict) -> set[tuple[int, int]] | None:
    """The zones a `terrain_write` MOVES, or None when it does not say them."""
    entries = ((rec.get("params") or {}).get("entries")) or []
    out = {tuple(e["zone"]) for e in entries
           if isinstance(e, dict) and isinstance(e.get("zone"), (list, tuple))
           and len(e["zone"]) == 2}
    return out or None


def replay(records: Iterable[dict]) -> dict:
    """Read a recorded build against this declaration.  Five findings.

    OUT_OF_ORDER    within one unit, a record of an EARLIER declared step
                    appended after a record of a LATER one.
    NO_UNIT         a mutating record that names no piece of ground, so no
                    ordering question can be asked about it at all.
    UNCLASSIFIED    a mutating record whose (kind, op) this table cannot name.
    UNVERIFIED      a unit that placed pieces and has no `recheck` record: the
                    1E_OUT_OF_ORDER class, at build time.
    TERRAIN_AFTER_PIECE   a terrain_write that moves ground in a zone where a
                    piece ALREADY STANDS. See GROUND_FREEZE_SCOPE_DEPENDS_ON.
    GROUND_ELSEWHERE      a terrain_write after the world's first piece, in
                    ground no piece occupies. REPORTED, never a finding.
    """
    per_unit: dict[str, list[tuple[int, tuple[int, int], str, str]]] = {}
    ground_of: dict[str, set[str]] = {}
    out_of_order: list[dict] = []
    no_unit: list[dict] = []
    network_wide: list[dict] = []
    unclassified: list[dict] = []
    unverified: list[str] = []
    no_recheck_step: list[str] = []
    stamp_disagrees: list[dict] = []
    after_piece: list[dict] = []
    ground_elsewhere: list[dict] = []
    first_piece: int | None = None
    counted = 0

    rows = list(records)
    for rec in rows:
        op = rec.get("op") or ""
        if op in PIECE_OPS and first_piece is None:
            first_piece = rec.get("seq")

    for rec in rows:
        op = rec.get("op") or ""
        seq = rec.get("seq")
        role = ((rec.get("params") or {}).get("role")) or ""
        if op in NON_ORDERING_OPS and not (op == "observe"
                                           and role == RECHECK_ROLE):
            continue
        counted += 1
        rank = rank_of(rec)
        if rank is None:
            unclassified.append({"seq": seq, "op": op, "role": role})
            continue
        stamped, by_role = stamped_rank_of(rec), role_rank_of(rec)
        if stamped and by_role and stamped[0] != by_role[0]:
            stamp_disagrees.append({"seq": seq, "op": op, "role": role,
                                    "stamped_step": _name_of(stamped),
                                    "role_step": _name_of(by_role)})
        unit = unit_of(rec)
        if not unit:
            # A STEP THAT DECLARES NO UNITS HAS NO UNIT TO NAME, and calling
            # its records NO_UNIT makes a coherent build read as broken.
            # MEASURED on Fresh42: the full ledger replayed 568 NO_UNIT rows,
            # every one of them a `zones_generate` of role `road_census` --
            # stage 10's whole-network survey, which step 0 `inputs` DECLARES
            # as its own (`records=(("road","zones_generate"),)`) while being
            # `inherited` with no `units` callable at all.  The survey covers
            # the NETWORK; there is no segment it belongs to, and inventing
            # one would be the subset key in the other direction.  This is
            # read from the TABLE -- `st.units is None` -- so it cannot drift
            # from the declaration and it says nothing about any step that
            # does declare units: a `terrain_write` naming no ground is still
            # NO_UNIT, which is the arm the selftest flips.
            if rank[0] in _UNITLESS_STEPS:
                network_wide.append({"seq": seq, "op": op, "role": role,
                                     "step": _name_of(rank)})
            else:
                no_unit.append({"seq": seq, "op": op, "role": role})
            continue
        per_unit.setdefault(unit, []).append((seq, rank, op, role))
        stands = stands_on_of(rec)
        if stands:
            ground_of.setdefault(unit, set()).update(stands)

    recheck_seqs = {u: [s for s, _, _, role in rows_u if role == RECHECK_ROLE]
                    for u, rows_u in per_unit.items()}

    for unit, rows_u in per_unit.items():
        rows_u.sort(key=lambda r: r[0])
        best: tuple[int, int] = (-1, -1)
        best_seq = -1
        for seq, rank, op, role in rows_u:
            if _later(best, rank):
                out_of_order.append({
                    "unit": unit, "seq": seq, "op": op, "role": role,
                    "step": _name_of(rank), "after_seq": best_seq,
                    "after_step": _name_of(best)})
            if rank > best:
                best, best_seq = rank, seq
        placed = any(op in PIECE_OPS for _, _, op, _ in rows_u)
        rechecked = any(role == RECHECK_ROLE for _, _, _, role in rows_u)
        # RECHECK_DOMAIN_DEPENDS_ON: the terminal step re-reads ONE domain's
        # ground.  A unit is owed a recheck only if one of its records belongs
        # to a step of that domain.  MEASURED on Fresh42's first drive: 40 of
        # 44 UNVERIFIED were settlements and installations, which no step
        # rechecks -- REPORTED as NO_RECHECK_STEP below, never dropped.
        owed = (_RECHECK_MODE == "all"
                or any(rank[0] in _RECHECK_DOMAIN_STEPS
                       for _, rank, _, _ in rows_u))
        # A DECK IS RE-READ WITH THE ROAD IT CARRIES.  Its unit is a crossing,
        # which no step re-reads by name, so on the domain rule alone every
        # deck is NO_RECHECK_STEP -- the reading under which Ulfsland's decks
        # at seq 2803-2867 stood among the 14 walker-blocking bodies nothing
        # ever looked at again.  A unit that STANDS ON units of the rechecked
        # domain is owed THEIR recheck, taken AFTER its own last piece: a
        # recheck appended before the deck landed read a corridor with a hole
        # where the deck now is, which is not a reading of this deck.
        stands = ground_of.get(unit, set())
        if placed and not owed and stands:
            owed = True
            last = max((s for s, _, op, _ in rows_u if op in PIECE_OPS),
                       default=-1)
            rechecked = all(any(s > last for s in recheck_seqs.get(g, ()))
                            for g in stands)
        flag = (placed and owed and not rechecked) if _TERMINAL_MODE == "real" \
            else (False if _TERMINAL_MODE == "off" else True)
        if flag:
            unverified.append(unit)
        elif placed and not owed and _TERMINAL_MODE == "real":
            no_recheck_step.append(unit)

    # GROUND_FREEZE_SCOPE_DEPENDS_ON: the ZONES THIS WRITE MOVES and the zones
    # pieces already stand in -- not "has any piece anywhere been placed yet".
    #
    # This rule read `rec.seq > first_piece` world-wide and cited
    # `writer._flatten_forbidden_check` as "no owner exception".  THE CITATION
    # WAS WRONG AND THE GUARD SAYS SO IN ITS OWN DOCSTRING: it is
    # "FOOTPRINT-GRANULAR, not zone-granular", zone overlap is only its
    # TRIGGER, and its population is records carrying `keep_out_kind`, not
    # every piece.  MEASURED 2026-09-24 off the guard's OWN recorded census
    # (`meta.flatten_guard`): on all 42 of Fresh42's flagged writes
    # `reached_this_write` is 0 -- 18 of them with 1 or 2 live FORBIDDEN
    # records in the world -- so world-wide scope reported 42 rows the guard
    # it cited had already measured and cleared.
    #
    # AND THE NARROWER RULE IS NOT THE EMPTY ONE.  Scoped to the ground each
    # write moves: Fresh42 42 -> 18, Ulfsland 191 -> 126.  Eighteen pad writes
    # DID move ground in a zone already holding a standing body, which is the
    # settlement-order defect fixed in settlements/build.py by finishing a
    # settlement's ground before any of its bodies stand.  The scope was a
    # SUPERSET; the rule underneath it was real.
    #
    # THE EIGHTEEN, SETTLED 2026-09-24, AND THE EMITTER IS THE WRONG SIDE.
    # Enumerated off Fresh42's ledger: all 18 are `role=site_pad`, ten in
    # stenvik and eight in vestvik, and EVERY ONE is blocked by a piece of
    # ANOTHER UNIT OF ITS OWN SETTLEMENT -- zone (8,14) held since seq 1486,
    # (9,14) since 1479, (9,15) since 1503, (-14,29) since 1584, (-15,28)
    # since 1597, (-14,28) since 1611.  Not one is a foreign claim and not
    # one is elsewhere in the world, so the declaration is NOT too strong
    # and no row is reclassified to make the count fall.
    #
    # `settlements/build.py`'s two passes made "a settlement's ground is
    # finished before any of its bodies stand" true FOR ONE INVOCATION, and
    # the unit set of an invocation is `--site` narrowed by `--from`,
    # `--limit` and `--only` -- a resume, which is the normal path.  The
    # guarantee's key was THE PROCESS; the quantity depends on the
    # SETTLEMENT'S WHOLE LIFETIME.  `emit_pad` now refuses at write time on
    # exactly this rule, reading THIS function's own `piece_zone` and
    # `PIECE_OPS` rather than restating them, so a write-time refusal and a
    # replay finding cannot drift apart.  See
    # `settlements/build.py::GROUND_FREEZE_ACROSS_INVOCATIONS_DEPENDS_ON`.
    #
    # FAIL CLOSED IN BOTH READS.  A piece whose position cannot be read
    # poisons every zone (`occupied_unreadable`), and a terrain_write that
    # names no zones is flagged outright -- "I could not measure it" is not
    # "it is fine".
    occupied: dict[tuple[int, int], int] = {}
    occupied_unreadable = False
    if _FREEZE_MODE != "off":
        for rec in rows:
            op = rec.get("op") or ""
            seq = rec.get("seq")
            if op in GROUND_OPS and first_piece is not None:
                # A WRITE THAT PRECEDES EVERY PIECE IS NOT IN EITHER BUCKET.
                # It is the declared order, not a narrowed finding, and
                # reporting it as GROUND_ELSEWHERE would inflate the reported
                # count with the build doing exactly what it was told.
                if _FREEZE_MODE != "always" and (seq is None
                                                 or seq <= first_piece):
                    continue
                zones = write_zones(rec)
                if _FREEZE_MODE == "always":
                    hit, why = True, "neutered: always"
                elif _FREEZE_SCOPE == "world":
                    hit, why = True, "any piece had been placed"
                elif zones is None:
                    hit, why = True, "this write names no zones"
                elif occupied_unreadable:
                    hit, why = True, "a standing piece names no position"
                else:
                    shared = sorted(zones & set(occupied))
                    hit = bool(shared) and _FREEZE_SCOPE != "none"
                    why = (f"zone {shared[0]} already held the piece at seq "
                           f"{occupied[shared[0]]}") if shared else ""
                row = {"seq": seq, "unit": unit_of(rec),
                       "role": ((rec.get("params") or {}).get("role")) or "",
                       "first_piece_seq": first_piece, "why": why}
                (after_piece if hit else ground_elsewhere).append(row)
            if op in PIECE_OPS:
                z = piece_zone(rec)
                if z is None:
                    occupied_unreadable = True
                else:
                    occupied.setdefault(z, seq)

    return {"units": len(per_unit), "ordering_records": counted,
            "OUT_OF_ORDER": out_of_order, "NO_UNIT": no_unit,
            # REPORTED, NEVER SILENT.  These are not a finding and they are
            # not dropped either: a record the table places at a step that
            # declares no units is network-wide BY DECLARATION, and the count
            # is printed so "568 records nobody looked at" can never hide
            # behind a clean NO_UNIT of 0.
            "NETWORK_WIDE": network_wide,
            "UNCLASSIFIED": unclassified, "UNVERIFIED": sorted(unverified),
            "NO_RECHECK_STEP": sorted(no_recheck_step),
            # REPORTED, never a finding: the record's role names a different
            # step from the one the build stamped on it.  The stamp is used.
            "STAMP_DISAGREES": stamp_disagrees,
            "TERRAIN_AFTER_PIECE": after_piece,
            # REPORTED, NEVER SILENT, same contract as NETWORK_WIDE: a write
            # that moved ground nothing stands on, after something stood
            # somewhere else.  Printed so "24 writes nobody looked at" cannot
            # hide behind a narrowed TERRAIN_AFTER_PIECE.
            "GROUND_ELSEWHERE": ground_elsewhere,
            "coherent": not (out_of_order or no_unit or unclassified
                             or unverified or after_piece)}


# WHICH STEPS HAVE NO UNIT TO NAME, read off the table rather than listed.
# A step with no `units` callable does not act on a piece of ground; its
# records are the network's.  Derived, so a step that gains units later stops
# being exempt without anyone remembering to edit a set.
_UNITLESS_STEPS = frozenset(st.ordinal for st in BUILD_STEPS
                            if st.units is None)


def _name_of(rank: tuple[int, int]) -> str:
    for st in BUILD_STEPS:
        if st.ordinal == rank[0]:
            return st.name
    return f"step{rank[0]}"


def read_ledger(path: Path) -> list[dict]:
    out: list[dict] = []
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


# ---------------------------------------------------------------------------
# WHICH UNITS A REFUSAL ACTUALLY BLOCKS
# ---------------------------------------------------------------------------
# THE SAME DEFECT CLASS THIS FILE WAS WRITTEN AGAINST, ONE LEVEL OUT.  A step
# row's `requires` names a LABEL -- `road_ground` -- and a label is WORLD-WIDE.
# But the thing a lamp stands on is not `road_ground`, it is
# `road_ground(T1-portalhub-temple)`.  `run` read the label key and did the
# only thing a world-wide key permits: it stopped the entire build at the
# first refused unit.
#
# MEASURED 2026-09-24 on Fresh42, serial offline `--validate` over all 14
# segments of step 4: 5 segments clear the rim gate, 2 of those (S7-stathub-
# wtnorth, T13-temple-harboursouth) clear every gate and are rc 0, and the
# first unit in the list -- T1-portalhub-temple -- is rc 8.  Under the
# world-wide key that is a build which lays NOTHING: `run` returns at T1 and
# the two segments that are fully buildable are never reached, never recorded
# and never refused either.  A key naming a SUPERSET of what the quantity
# depends on, and its cost here is the whole road.
#
# The other spelling is just as wrong and is the reason this is a declared
# dependency rather than a `continue`: if a refusal blocked NOTHING, step 7
# would lamp T1 -- a segment whose carriageway was never graded -- and step 8
# would then read a corridor nobody wrote.  That is the `1E_OUT_OF_ORDER`
# class arriving by the front door.
#
# So the rule is: a refusal blocks the SAME UNIT in every later step that
# transitively consumes the refused step's output AND draws its units from the
# SAME DOMAIN.  Across domains a unit string means nothing, so nothing is
# blocked and the outstanding work is REPORTED as a DEBT instead -- silence
# there would be the third spelling of the same mistake.
#
# UNLESS THE CORPUS STATES THE PAIRING.  A crossing's two banks belong to
# named segments -- `crossings.<Seed>.yaml` says so per record, and the deck
# builder refuses on exactly that pairing (`boardwalk --require-terrain`).
# MEASURED on Fresh42's drive 2: 5 of 14 segments laid, and 7 of the 9
# declared crossings stand on a segment that was not.  Under the domain rule
# alone each of those is a DEBT the deck step then RUNS, all the way into
# the builder's own refusal; with the pairing it is BLOCKED by name before a
# piece is solved.  `stands_on` is that pairing -- see STANDS_ON_DEPENDS_ON.
UNIT_BLOCK_DEPENDS_ON = ("unit_domain", "step_ancestry", "stands_on")

_UNIT_BLOCK_MODE = "real"   # real | off (block nothing) | global (block all)

_PROBE_SCOPE = Scope("Selftest", "Selftest", Path("/nonexistent"),
                     Path("/nonexistent"), Path("/nonexistent"))


def step_ancestors(step: BuildStep,
                   steps: tuple[BuildStep, ...] = BUILD_STEPS) -> set[str]:
    """Every step whose output `step` transitively consumes, by name.

    Derived from `requires` and `produces` -- the same two fields
    `pipeline.audit_graph` reads -- through `pipeline.owner_of`, so the
    ancestry and the import-time order check cannot drift apart.
    """
    owners = pipeline.owner_of(steps, _PROBE_SCOPE)         # label -> step
    seen: set[str] = set()
    todo = [step]
    while todo:
        cur = todo.pop()
        for label in cur.requires:
            src = owners.get(label)
            if src is None or src.name in seen:
                continue
            seen.add(src.name)
            todo.append(src)
    return seen


def blocked_units(step: BuildStep, units: Iterable[str],
                  refused: dict[str, set[str]],
                  steps: tuple[BuildStep, ...] = BUILD_STEPS,
                  ground: dict[str, dict[str, tuple[str, ...]]] | None = None
                  ) -> set[str]:
    """Which of `units` a prior refusal makes unbuildable for `step`.

    `units` IS HANDED IN rather than re-read through `step.units(scope)`: the
    caller has already enumerated the corpus, and a second reader against a
    scope that is not the caller's answers an EMPTY list without saying so --
    which is exactly how a `global` arm that is supposed to block everything
    blocks nothing and passes.  UNREAD IS NOT CLEAR.

    `refused` is {step name: {unit}}.  Same domain, or across a domain the
    step's own `ground` ({unit: {domain: units}}) names -- see
    UNIT_BLOCK_DEPENDS_ON.
    """
    units = list(units)
    if _UNIT_BLOCK_MODE == "off":
        return set()
    anc = step_ancestors(step, steps)
    out: set[str] = set()
    for name, ru in refused.items():
        if name not in anc or not ru:
            continue
        if _UNIT_BLOCK_MODE == "global":
            # THE OLD WORLD-WIDE KEY, SPELLED OUT: any refusal anywhere
            # upstream blocks every unit of this step.
            return set(units) | set(ru)
        src = next((s for s in steps if s.name == name), None)
        if src is None or not src.domain:
            continue
        if src.domain == step.domain:
            out |= ru
        elif ground and _STANDS_ON_MODE == "real":
            out |= {u for u in units
                    if set((ground.get(u) or {}).get(src.domain, ())) & ru}
    return out


def cross_domain_debt(step: BuildStep, refused: dict[str, set[str]],
                      steps: tuple[BuildStep, ...] = BUILD_STEPS,
                      ground: dict[str, dict[str, tuple[str, ...]]] | None = None
                      ) -> dict[str, set[str]]:
    """Refused upstream units this step consumes but cannot be keyed against.

    Step 5 `bodies` requires `road_ground`, and a settlement is not a segment:
    no string in one domain names anything in the other.  Blocking on a guess
    would be a key naming a SUPERSET; saying nothing would let a body stand on
    ground a refused segment still owes a grade to, which is the flatten
    FORBIDDEN collision this table exists to make unspellable.  So it is
    NAMED, with its units, and the build's exit code already carries the
    refusal.  A domain the step's `ground` pairs its units with is NOT a debt:
    the pairing already answered which units it blocks, and a refused unit
    nothing here stands on is not consumed by this step at all.
    """
    anc = step_ancestors(step, steps)
    paired = ({d for g in (ground or {}).values() for d in g}
              if _STANDS_ON_MODE == "real" else set())
    out: dict[str, set[str]] = {}
    for name, units in refused.items():
        if name not in anc or not units:
            continue
        src = next((s for s in steps if s.name == name), None)
        if src is not None and src.domain != step.domain \
                and src.domain not in paired:
            out[name] = units
    return out


# ---------------------------------------------------------------------------
# WHAT MAKES A RECORDED SUCCESS THE SAME WORK
# ---------------------------------------------------------------------------
# THE THIRD SPELLING OF THE SAME KEY DEFECT, AND IT COST A LIVE RE-RUN.  The
# resume key was the whole argv, and the whole argv contains `--actor`.
# `--actor` names WHO ran the command; it is stamped on the records so a
# provenance that is wrong reads as evidence.  It is not an INPUT: it changes
# nothing the command does to the world, nothing it reads, and nothing it
# writes but the name on the receipt.
#
# MEASURED 2026-09-24: steps 1-3 of Fresh42 stand in the journal at rc 0 under
# actor `BuildFresh` -- 13 site_prepare units, 13 site_ground units and 85
# road_clear commands.  A second agent resuming the same build as itself was
# handed a key that matched NONE of them and began re-running step 3 LIVE from
# segment one; this file's own header measures that step at 156.3 minutes of
# RCON.  A key naming a SUPERSET of what the quantity depends on, and its
# price here is 2.6 hours of re-doing work that was already done correctly.
#
# The other direction is a real defect too, which is why this is a dependency
# and not a deletion: drop the argv from the key and a unit whose command was
# RE-POINTED -- a different census, a different placed-objects file -- reads
# as already done.  So the key is the argv WITHOUT the actor, and the actor
# the recorded success actually stands on is PRINTED with the skip.
RESUME_KEY_DEPENDS_ON = ("argv", "provenance")

_RESUME_MODE = "real"       # real | off (argv ignored) | superset (actor in)

_ACTOR_FLAG = "--actor"


# COLLAR_RETRY_DEPENDS_ON: A road_ground REFUSAL IS ASKED ONCE MORE WITHOUT
# THE PAD COLLAR.  See `run`.  NEUTER `_COLLAR_RETRY_MODE = "off"`: a refusal
# stands.  Proven by `_collar_retry_case`.
COLLAR_RETRY_DEPENDS_ON = ("the_alternate_arm_asked_of_the_real_writer",)
_COLLAR_RETRY_MODE = "retry"    # retry | off


def resume_key(ordinal: int, unit: str, cmd: Iterable[str]) -> tuple:
    """The identity of one step-unit command, for `--resume`.

    Everything the command does to the world, and nothing about who ran it.
    """
    if _RESUME_MODE == "off":
        return (ordinal, unit)
    parts = [str(c) for c in cmd]
    if _RESUME_MODE != "superset":
        kept: list[str] = []
        drop_next = False
        for p in parts:
            if drop_next:
                drop_next = False
                continue
            if p == _ACTOR_FLAG:
                drop_next = True
                continue
            kept.append(p)
        parts = kept
    return (ordinal, unit, tuple(parts))


def actor_of(cmd: Iterable[str]) -> str:
    """The actor stamped on a recorded command, or "" when it names none."""
    parts = [str(c) for c in cmd]
    for i, p in enumerate(parts):
        if p == _ACTOR_FLAG and i + 1 < len(parts):
            return parts[i + 1]
    return ""


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------
# LIVE_REPLACE_DEPENDS_ON: THE LIVE DEFAULT PAIR MAY BE BUILT ONLY OVER AN
# ARCHIVED LEDGER.  The default seed/world's ledger is append-only,
# hash-chained and cannot be re-derived from a re-run, so a build step -- which
# appends to it -- is refused outright (REFUSED rc 5).  The operator may
# replace that world on purpose (`--replace-live`, as `pipeline.py` already
# allows), and the one thing that makes it safe is that the ledger being
# replaced still exists: archived by RENAME beside it as
# `ledger/runs/<World>.archive-*/ledger.jsonl`, never deleted.  Without such an
# archive, or when the "archive" is the live ledger itself, the build is
# refused.  NEUTER `_LIVE_REPLACE_MODE = "unguarded"`: --replace-live alone is
# enough.  Proven by `_live_replace_case`.
LIVE_REPLACE_DEPENDS_ON = ("the_replaced_ledger_survives_as_an_archive",)
_LIVE_REPLACE_MODE = "archived"    # archived | unguarded


def live_replace_refusal(seed: str, world: str, replace_live: bool,
                         runs_dir: Path | None = None) -> str | None:
    """Why this build may not run on the live default pair, or None."""
    if not worldseed.is_default(seed, world):
        return None
    runs = runs_dir if runs_dir is not None else HERE / "ledger" / "runs"
    if not replace_live:
        return (f"REFUSING to build seed {seed!r} / world {world!r}: that is "
                f"the live default pair. Its ledger is append-only, "
                f"hash-chained and cannot be re-derived from a re-run, and a "
                f"build step is a write. Use a different --seed/--world, or "
                f"--replace-live to replace the world on purpose over an "
                f"archived ledger.")
    if _LIVE_REPLACE_MODE == "unguarded":
        return None
    live = runs / world / "ledger.jsonl"
    archives = sorted(runs.glob(f"{world}.archive-*/ledger.jsonl"))
    if not archives:
        return (f"REFUSING --replace-live for {world!r}: no archived ledger "
                f"beside it ({runs}/{world}.archive-*/ledger.jsonl). Archive "
                f"the ledger being replaced by RENAME first; it is the one "
                f"artefact that cannot be re-derived.")
    if live.exists() and any(a.stat().st_size == live.stat().st_size
                             and a.read_bytes() == live.read_bytes()
                             for a in archives):
        return (f"REFUSING --replace-live for {world!r}: the archived ledger "
                f"is byte-identical to the live one, so nothing was replaced "
                f"-- the build would append to the ledger it claims to "
                f"preserve.")
    return None


def run(rt: Runtime, through: int = 99,
        steps: tuple[BuildStep, ...] = BUILD_STEPS,
        resume: bool = False, replace_live: bool = False) -> int:
    """`_run` under the world's WRITER LOCK (pipeline.WRITER_LOCK_DEPENDS_ON):
    a build started while another run drives this world is refused, rc 75,
    before step 1.  The live default pair's own refusal comes first and takes
    no lock, so a refused run writes no file into the live ledger directory.
    A build that `pipeline.py run` started inherits that run's lock."""
    s = rt.scope
    if live_replace_refusal(s.seed, s.world, replace_live):
        return _run(rt, through, steps, resume, replace_live)
    lock = pipeline.acquire_writer_lock(s, "buildstage.py run")
    if isinstance(lock, str):
        sys.stderr.write(lock + "\n")
        return pipeline.WRITER_LOCK_EXIT
    try:
        return _run(rt, through, steps, resume, replace_live)
    finally:
        lock.release()


def _run(rt: Runtime, through: int = 99,
         steps: tuple[BuildStep, ...] = BUILD_STEPS,
         resume: bool = False, replace_live: bool = False) -> int:
    """Run the build steps IN THE DECLARED ORDER, refusing PER UNIT.

    A refused unit stops ITS OWN chain and every later step that consumes
    THAT unit; its siblings are different ground and are built. The build's
    exit code is the worst refusal it carried, so a partial network is
    honest rather than clean. See UNIT_BLOCK_DEPENDS_ON.

    THE LIVE DEFAULT PAIR IS REFUSED OUTRIGHT.  Every step here writes into a
    world over RCON and appends to that world's ledger; the default seed/world
    is the finished corpus eleven gate sections read, and its ledger is the one
    artefact in this tree that cannot be re-derived from a re-run.
    """
    s = rt.scope
    refusal = live_replace_refusal(s.seed, s.world, replace_live)
    if refusal:
        sys.stderr.write(refusal + "\n")
        return 5
    (s.work / "build").mkdir(parents=True, exist_ok=True)
    journal = s.work / "build" / "buildstage.json"
    # THE JOURNAL IS THIS BUILD'S OWN RECORD AND IT WAS WRITE-ONLY.  A nine
    # step build that refuses at step 4 had to re-run steps 1-3 to get back to
    # the refusal: MEASURED on Fresh42, 7.3 min + 6.0 min + 156.3 min of live
    # RCON to re-reach a 3.8 s refusal.  A guard that costs 2.8 hours to
    # re-test is a guard nobody tests, which is how a build gets forced past
    # one.  `--resume` skips a step-unit that ALREADY COMPLETED rc 0 in this
    # world's journal WITH THE SAME ARGV -- the argv is part of the key
    # because a command whose inputs were re-pointed is a different command,
    # and a recorded success for another one would be a key naming a superset.
    # It never skips a refusal, a step that did not run, or a step whose
    # command changed, and every skip is PRINTED with the rc and wall it is
    # standing on.  Default OFF: a from-scratch regen runs every step.
    log: list[dict] = []
    done: dict[tuple, dict] = {}
    # THIS RUN'S refusals only.  A refusal recorded by an EARLIER run is not
    # carried in: `--resume` re-runs anything that is not a recorded rc 0, and
    # a unit refused before the tool was fixed must get its second answer from
    # the tool, not from the journal.
    refused: dict[str, set[str]] = {}
    worst = 0
    if resume and journal.exists():
        try:
            log = json.loads(journal.read_text())
        except ValueError:
            log = []
        for e in log:
            if e.get("rc") == 0 and e.get("cmd"):
                done[resume_key(e.get("step"), e.get("unit"), e["cmd"])] = e

    for st in steps:
        if st.ordinal > through:
            break
        if st.inherited:
            # ONLY THE INPUTS THE STEPS IN RANGE ACTUALLY CONSUME.  Checking
            # all four would be a key naming a SUPERSET: `--through 2` runs the
            # pad steps, which consume `placements` and nothing else, and
            # refusing them for a missing `segments` would make a world that is
            # halfway up the pipeline unbuildable for a reason it does not have.
            # The set is DERIVED from the table's own `requires`, so it cannot
            # drift from the order.
            wanted = {lab for other in steps if other.ordinal <= through
                      for lab in other.requires}
            missing = [a.label for a in st.produces(s) if a.label in wanted
                       and pipeline.artefact_state(a, s)[0] != pipeline.PRESENT]
            log.append({"step": st.ordinal, "name": st.name,
                        "inputs_wanted": sorted(wanted & {a.label for a
                                                          in st.produces(s)}),
                        "inputs_missing": missing})
            journal.write_text(json.dumps(log, indent=1))
            if missing:
                sys.stderr.write(
                    f"\nREFUSING before step 1: the steps through {through} "
                    f"are handed {missing} by earlier pipeline stages and this "
                    f"world has none of them. Run `pipeline.py run --through "
                    f"11` first, or lower --through.\n")
                return 3
            continue
        if st.argv is None:
            log.append({"step": st.ordinal, "name": st.name,
                        "refused": f"NO COMMAND: {st.manual}"})
            journal.write_text(json.dumps(log, indent=1))
            sys.stderr.write(f"\nSTOPPING at step {st.ordinal} {st.name!r}: "
                             f"no tool in this tree runs it unattended.\n"
                             f"  {st.manual}\n")
            return 4
        units = st.units(s) if st.units else []
        if not units:
            log.append({"step": st.ordinal, "name": st.name, "units": 0,
                        "note": "this world declares no unit for this step"})
            journal.write_text(json.dumps(log, indent=1))
            continue
        ground = st.stands_on(s) if st.stands_on else {}
        blocked = blocked_units(st, units, refused, steps, ground)
        debt = cross_domain_debt(st, refused, steps, ground)
        for src, du in sorted(debt.items()):
            sys.stderr.write(
                f"[{st.ordinal}/{steps[-1].ordinal}] {st.name}: DEBT -- this step "
                f"consumes {src!r}, which refused {len(du)} unit(s) "
                f"{sorted(du)} in a DIFFERENT unit domain ({st.domain!r}). No "
                f"unit string here names any of them, so nothing is blocked "
                f"and nothing is claimed: those units still owe their ground, "
                f"and a piece standing over it will refuse that write later.\n")
        log.append({"step": st.ordinal, "name": st.name,
                    "blocked": sorted(blocked),
                    "debt": {k: sorted(v) for k, v in debt.items()}})
        for unit in units:
            if unit in blocked:
                # WHICH STEP REFUSED WHICH GROUND: the unit itself, or the
                # units of another domain it stands on.
                stood = {unit}
                for dom_units in (ground.get(unit) or {}).values():
                    stood |= set(dom_units)
                who = sorted(f"{n}: {sorted(u & stood)}"
                             for n, u in refused.items() if u & stood)
                sys.stderr.write(
                    f"[{st.ordinal}/{steps[-1].ordinal}] {st.name} {unit}: "
                    f"BLOCKED -- {who} refused this unit or the ground it "
                    f"stands on, and this step consumes it. Not run, not "
                    f"skipped: it has no ground to stand on.\n")
                log.append({"step": st.ordinal, "name": st.name, "unit": unit,
                            "blocked_by": who})
                journal.write_text(json.dumps(log, indent=1))
                continue
            unit_rc = 0
            for cmd in st.argv(rt, unit):
                key = resume_key(st.ordinal, unit, cmd)
                was = done.get(key)
                # A retry that LAID the unit is journalled under the flagged
                # command (COLLAR_RETRY_DEPENDS_ON); the unflagged one is the
                # same work and must not be re-run over it by --resume.
                if was is None and st.name == "road_ground":
                    was = done.get(resume_key(
                        st.ordinal, unit, list(cmd) + ["--no-connection-collar"]))
                if was is not None:
                    ran_by = actor_of(was.get("cmd") or ()) or "an unnamed actor"
                    mine = actor_of(cmd)
                    sys.stderr.write(
                        f"[{st.ordinal}/{steps[-1].ordinal}] {st.name} {unit}: "
                        f"RESUMED -- this exact command completed rc 0 in "
                        f"{was.get('wall_s')} s and is not re-run"
                        + (f" (RUN BY {ran_by}, not by {mine}: the actor is "
                           f"stamped on the records, not part of what the "
                           f"command does)" if mine and ran_by != mine else "")
                        + "\n")
                    continue
                t0 = time.time()
                sys.stderr.write(f"\n[{st.ordinal}/{steps[-1].ordinal}] {st.name} "
                                 f"{unit}: {' '.join(cmd)}\n")
                env = s.env()
                # Stamped onto every record the command appends (ledger/live.py
                # `meta.build_step` / `build_unit`), so the replay reads which
                # step wrote a record instead of inferring it from the role.
                env["VH_BUILD_STEP"] = str(st.ordinal)
                env["VH_BUILD_UNIT"] = unit
                rc = subprocess.call(cmd, cwd=str(HERE), env=env,
                                     pass_fds=pipeline.writer_lock_fds())
                wall = time.time() - t0
                # THE OTHER ARM, ON THE REAL WRITER (COLLAR_RETRY_DEPENDS_ON).
                # A refused write sends nothing, so the same leg can be asked
                # again from identical ground.  The pad collar closes a fill
                # onto a pad the road arrives at and, on another leg, leaves a
                # step the writer refuses; the plan cannot tell them apart,
                # because it stamps each leg on virgin ground and the writer
                # stamps them in order on the ground the earlier legs left.
                # MEASURED on Fresh42 drive 8: T6 refused with the collar
                # (2 steps, 1.5626 m), 0 without it.  So a road_ground refusal
                # is retried once without the collar.
                if (rc != 0 and st.name == "road_ground"
                        and _COLLAR_RETRY_MODE == "retry"
                        and "--no-connection-collar" not in cmd):
                    log.append({"step": st.ordinal, "name": st.name,
                                "unit": unit, "rc": rc,
                                "wall_s": round(wall, 1), "cmd": cmd,
                                "env": {k: env.get(k) for k in
                                        ("VH_WORLD", "VH_SEED", "VALHEIM_WORLD",
                                         "VALHEIM_CONTAINER",
                                         "VALHEIM_RCON_HOST",
                                         "VALHEIM_WORLD_ROOT")}})
                    sys.stderr.write(
                        f"\n[{st.ordinal}/{steps[-1].ordinal}] {st.name} "
                        f"{unit}: rc={rc} with the pad collar; retrying "
                        f"WITHOUT it (COLLAR_RETRY_DEPENDS_ON)\n")
                    cmd = list(cmd) + ["--no-connection-collar"]
                    t0 = time.time()
                    rc = subprocess.call(cmd, cwd=str(HERE), env=env,
                                         pass_fds=pipeline.writer_lock_fds())
                    wall = time.time() - t0
                # THE ENVIRONMENT, ON THE ROW, NOT JUST THE ARGV.  MEASURED
                # 2026-09-24: asked "did any of this build reach the wrong
                # server", this journal held 361 rows, 217 commands and their
                # full argv -- AND COULD NOT ANSWER, because the world a
                # command drives is carried in the ENVIRONMENT and nothing
                # here recorded it.  The answer took four forensic
                # measurements of two saves on disk.  These six keys are the
                # complete set that decides which world a child talks to
                # (`VALHEIM_WORLD_ROOT` is which INSTALL's save it reads: it
                # was missing, and a same-named world on another install went
                # unrecorded); a
                # key that is UNSET is written as null, because an absence
                # stated is evidence and an absence omitted reads as a
                # default.
                log.append({"step": st.ordinal, "name": st.name, "unit": unit,
                            "rc": rc, "wall_s": round(wall, 1),
                            "cmd": cmd,
                            "env": {k: env.get(k) for k in
                                    ("VH_WORLD", "VH_SEED", "VALHEIM_WORLD",
                                     "VALHEIM_CONTAINER",
                                     "VALHEIM_RCON_HOST",
                                     "VALHEIM_WORLD_ROOT")}})
                journal.write_text(json.dumps(log, indent=1))
                if rc != 0:
                    unit_rc = rc
                    # THE UNIT IS REFUSED, NOT THE BUILD.  The commands of a
                    # unit are a chain, so the rest of THIS unit's chain does
                    # not run; its siblings are different ground and are
                    # untouched by it.  See UNIT_BLOCK_DEPENDS_ON.
                    sys.stderr.write(
                        f"\nREFUSED: step {st.ordinal} {st.name!r} unit "
                        f"{unit!r} rc={rc}. This unit is not built and every "
                        f"later step that consumes it is BLOCKED; its "
                        f"siblings continue. The journal is {journal}.\n")
                    break
            if unit_rc:
                refused.setdefault(st.name, set()).add(unit)
                worst = worst or unit_rc
    if refused:
        total = sum(len(u) for u in refused.values())
        sys.stderr.write(
            f"\nPARTIAL BUILD: {total} unit(s) refused -- "
            + "; ".join(f"{k}: {sorted(v)}" for k, v in sorted(refused.items()))
            + f". Everything else in the declared order was built. Exit "
            f"{worst}: a build carrying a refusal NEVER reads as clean.\n")
    return worst


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------
_NEUTERS: dict[str, tuple[str, str, str, object]] = {
    # label: (dependency, module, global, value)
    # RECORD_SEQUENCE_DEPENDS_ON, both directions.  `every step ordered` is the
    # shipped reading and brings Fresh42's 38 fixtures rows back; `no step
    # ordered` drops the declared sequences, so generate-after-clear passes.
    "record sequence every step": (
        "records_ordered", __name__, "_RANK",
        rank_table(BUILD_STEPS, depends_on=())),
    "recheck domain all": ("terminal_step_domain", __name__, "_RECHECK_MODE",
                           "all"),
    "attribution by role": ("stamped_build_step", __name__,
                            "_ATTRIBUTION_MODE", "role"),
    "role registry off": ("role_registry", __name__, "_ROLE_REGISTRY_MODE",
                          "off"),
    "record sequence no step": (
        "records_ordered", __name__, "_RANK",
        rank_table(tuple(dc_replace(s, records_ordered=False)
                         for s in BUILD_STEPS))),
    "declared units off": ("declared_units", __name__, "_DECLARED_UNITS_MODE",
                           "off"),
    "step rank off": ("step_rank", __name__, "_RANK_MODE", "off"),
    "step rank blind": ("step_rank", __name__, "_RANK_MODE", "blind"),
    "unit key off": ("unit_key", __name__, "_UNIT_MODE", "off"),
    "unit key global": ("unit_key", __name__, "_UNIT_MODE", "global"),
    "ordinal off": ("ordinal", __name__, "_ORDER_MODE", "off"),
    "ordinal reversed": ("ordinal", __name__, "_ORDER_MODE", "reversed"),
    "terminal step off": ("terminal_step", __name__, "_TERMINAL_MODE", "off"),
    "terminal step always": ("terminal_step", __name__, "_TERMINAL_MODE",
                             "always"),
    "ground freeze off": ("ground_freeze", __name__, "_FREEZE_MODE", "off"),
    "ground freeze always": ("ground_freeze", __name__, "_FREEZE_MODE",
                             "always"),
    "ground freeze scope world": ("ground_freeze_scope", __name__,
                                  "_FREEZE_SCOPE", "world"),
    "ground freeze scope none": ("ground_freeze_scope", __name__,
                                 "_FREEZE_SCOPE", "none"),
    "transport world literal": ("transport_world", __name__,
                               "_TRANSPORT_MODE", "literal"),
    "transport world blind": ("transport_world", __name__,
                             "_TRANSPORT_MODE", "blind"),
    "record kind merged": ("record_kind", __name__, "_KIND_MODE", "merged"),
    "record kind site": ("record_kind", __name__, "_KIND_MODE", "site"),
    # THE BRIDGE KIND: folded back into `road`, a bridge's generate and clear
    # rank as steps 4 and 3 again and Ulfsland's S1/S2 read OUT_OF_ORDER.
    "record kind bridge as road": ("record_kind", __name__, "_KIND_MODE",
                                   "bridge_as_road"),
    # STANDS_ON_DEPENDS_ON, the shipped reading: a deck forgets the segments
    # it stands on, so an unlaid bank is a DEBT and a deck is never rechecked.
    "stands on off": ("stands_on", __name__, "_STANDS_ON_MODE", "off"),
    # UNBUILT_DEPENDS_ON, both directions.  `off` looks at nothing; `no decks`
    # hands the check the shipped 9-row table, which must bring back "a
    # declared crossing with no builder".
    "builder check off": ("unbuilt", __name__, "_UNBUILT_MODE", "off"),
    "deck step dropped": ("unbuilt", __name__, "_UNBUILT_MODE", "no_decks"),
    "world scope off": ("world_scope", __name__, "_SCOPE_MODE", "off"),
    "world scope always": ("world_scope", __name__, "_SCOPE_MODE", "always"),
    # THE TABLE CHECK'S OWN NEUTERS, REBOUND IN `pipeline` -- the same two arms
    # that already guard STAGES, applied to this table. Inherited, not copied.
    "table ordinal off": ("table", "pipeline", "_ORDINAL_MODE", "off"),
    "table ordinal reversed": ("table", "pipeline", "_ORDINAL_MODE", "reversed"),
    # THE PER-UNIT BLOCK, BOTH WAYS.  `off` blocks nothing (a lamp goes onto
    # a segment whose grade was refused); `global` blocks everything (one
    # refusal anywhere stops the whole road, which is what `run` did).
    "unit block off": ("unit_block", __name__, "_UNIT_BLOCK_MODE", "off"),
    "unit block global": ("unit_block", __name__, "_UNIT_BLOCK_MODE",
                          "global"),
    # THE RESUME KEY, BOTH WAYS.  `off` drops the argv (a re-pointed command
    # reads as done); `superset` puts `--actor` back in (the same work by
    # another agent reads as undone, and is re-run live).
    "resume key off": ("resume_key", __name__, "_RESUME_MODE", "off"),
    "resume key superset": ("resume_key", __name__, "_RESUME_MODE",
                            "superset"),
    # BODY_UNIT_DEPENDS_ON, the shipped reading: step 5's installations are
    # ONE unit `*placements*` that re-tries a site step 1 refused and turns
    # 11 standing bodies into one refused unit.
    "installation site world": ("installation_site", __name__,
                                "_BODY_UNIT_MODE", "world"),
    # THE INSTALLATION WRITE PATH, BOTH HALVES OF ONE EVENT -- rebound in
    # `terraform/flatten.py`, where `install_emit` reads it for steps 1, 2, 5
    # and 6 alike.  `raw` sends and never records (the SHIPPED path: a later
    # same-zone write erases the pad, and the replay cannot see the pieces);
    # `ghost` records and never sends (the chain claims ground and bodies
    # the world does not have).
    "installation write path raw": ("installation_write_path", "flatten",
                                    "_WRITE_PATH_MODE", "raw"),
    "installation write path ghost": ("installation_write_path", "flatten",
                                      "_WRITE_PATH_MODE", "ghost"),
    # COLLAR_RETRY_DEPENDS_ON: a road_ground refusal stands.
    "collar retry off": ("collar_retry", __name__, "_COLLAR_RETRY_MODE",
                         "off"),
    # LIVE_REPLACE_DEPENDS_ON: --replace-live alone is enough.
    "live replace unguarded": ("live_replace", __name__, "_LIVE_REPLACE_MODE",
                               "unguarded"),
    # pipeline.WRITER_LOCK_DEPENDS_ON, rebound where `run` reads it: nothing
    # is taken and a second build starts beside the first.
    "writer lock off": ("writer_lock", "pipeline", "_WRITER_LOCK_MODE", "off"),
    # replay.STAGED_LOCK_DEPENDS_ON: the stop/verb/start bracket is open to any
    # other process again.
    "staged lock off": ("staged_lock", "ledger.replay", "_STAGED_LOCK_MODE",
                        "off"),
}

def _neuter(label: str) -> Callable[[], Callable[[], None]]:
    """Break exactly ONE declared dependency, in exactly ONE direction."""
    _, modname, key, value = _NEUTERS[label]

    def apply() -> Callable[[], None]:
        g = (globals() if modname == __name__
             else sys.modules[modname].__dict__)
        prior = g[key]
        g[key] = value

        def undo() -> None:
            g[key] = prior
        return undo
    return apply


# -- fixtures.  CONSTRUCTED, never borrowed: a case that reads this checkout's
# -- real ledger stops describing anything the day that ledger moves.
def _rec(seq: int, op: str, role: str, unit: str | None = None,
         **params) -> dict:
    p = {"role": role, **params}
    if unit is not None:
        p["site_id"] = unit
    return {"seq": seq, "op": op, "params": p}


# THE FIXTURES CARRY GROUND, because the rule does.  Before
# GROUND_FREEZE_SCOPE_DEPENDS_ON the freeze fixtures named no position and no
# zone at all, so the world-wide rule they exercised could not tell "after a
# piece HERE" from "after a piece anywhere" -- and a fixture that cannot
# distinguish the two cannot fail when the scope is wrong.  `_Z` is the zone
# every one of these sits in unless it says otherwise.
_Z = (8, 14)                       # 64 m zone; anchors below land inside it
_Z_XZ = {"x": _Z[0] * 64.0, "z": _Z[1] * 64.0}
_Z_FAR = (99, -99)


def _pad(seq: int, unit: str, zone: tuple[int, int] = _Z) -> dict:
    """A `terrain_write role=site_pad` that MOVES one named zone."""
    return _rec(seq, "terrain_write", "site_pad", unit,
                entries=[{"zone": [zone[0], zone[1]]}])


def _body(seq: int, unit: str, zone: tuple[int, int] = _Z) -> dict:
    """A `spawn_plan` standing at the centre of one named zone.  The position
    is `params.anchor`, which is where `spawn_plan` actually states it -- see
    `piece_zone`."""
    return _rec(seq, "spawn_plan", "town_body", unit,
                anchor={"x": zone[0] * 64.0, "y": 30.0,
                        "z": zone[1] * 64.0, "yaw": 0.0})


def _clean_site(base: int = 1, unit: str = "u-a") -> list[dict]:
    """One site built in the declared order, through to its recheck."""
    return [_rec(base + 0, "zones_generate", "site_pad", unit),
            _rec(base + 1, "objects_clear", "clearing", unit),
            _pad(base + 2, unit),
            _body(base + 3, unit),
            _rec(base + 4, "spawn", "site_prop", unit, pos=[512.0, 30.0, 896.0]),
            {"seq": base + 5, "op": "observe",
             "params": {"role": RECHECK_ROLE, "site_id": unit}}]


def _terrain_after_body() -> list[dict]:
    """THE DECK DEFECT, IN ITS SHAPE.  MEASURED on Ulfsland: `regrade_cut` is
    16 terrain_write records at seq 5393-5415, after the deck spawn_plans at
    5383-5388, and its own meta names 17 seqs its flatten guard collided with.
    The second write moves THE ZONE THE DECK IS STANDING IN, which is what
    makes it the defect rather than a coincidence of sequence numbers.
    """
    u = "u-deck"
    return [_rec(1, "zones_generate", "site_pad", u),
            _rec(2, "objects_clear", "clearing", u),
            _pad(3, u),
            _body(4, u),
            _pad(5, u),
            {"seq": 6, "op": "observe",
             "params": {"role": RECHECK_ROLE, "site_id": u}}]


def _settlement_interleaved() -> list[dict]:
    """FRESH42'S STEP 5, IN ITS SHAPE.  `settlements/build.py` ran one pass
    per unit -- generate, clear, flatten, PLACE -- so every unit after the
    first levelled ground while the previous unit's body stood.  MEASURED
    2026-09-24 on Fresh42: 42 pad writes after first_piece_seq 1479, and 18 of
    them into a zone that already held a body (e.g. seq 1492
    stenvik-house-1 into zone (8, 14), held since seq 1486).
    """
    return [_rec(1, "zones_generate", "site_pad", "u-a"),
            _pad(2, "u-a"), _body(3, "u-a"),
            _rec(4, "zones_generate", "site_pad", "u-b"),
            _pad(5, "u-b"), _body(6, "u-b"),
            {"seq": 7, "op": "observe",
             "params": {"role": RECHECK_ROLE, "site_id": "u-a"}},
            {"seq": 8, "op": "observe",
             "params": {"role": RECHECK_ROLE, "site_id": "u-b"}}]


def _settlement_two_pass() -> list[dict]:
    """THE SAME TWO UNITS, THE SAME ZONE, THE SAME RECORDS -- ground pass
    first.  Only the ORDER differs from `_settlement_interleaved`, so a check
    that cannot accept this one is refusing the sequence rather than the
    defect.
    """
    return [_rec(1, "zones_generate", "site_pad", "u-a"),
            _rec(2, "zones_generate", "site_pad", "u-b"),
            _pad(3, "u-a"), _pad(4, "u-b"),
            _body(5, "u-a"), _body(6, "u-b"),
            {"seq": 7, "op": "observe",
             "params": {"role": RECHECK_ROLE, "site_id": "u-a"}},
            {"seq": 8, "op": "observe",
             "params": {"role": RECHECK_ROLE, "site_id": "u-b"}}]


def _write_in_unoccupied_ground() -> list[dict]:
    """A pad written in EMPTY ground long after a body stood 6.3 km away.
    24 of Fresh42's 42 flagged writes are this, and none of them can bury or
    float anything: GROUND_ELSEWHERE, reported, never a finding.
    """
    return [_rec(1, "zones_generate", "site_pad", "u-a"),
            _pad(2, "u-a"), _body(3, "u-a"),
            _rec(4, "zones_generate", "site_pad", "u-far"),
            _pad(5, "u-far", _Z_FAR), _body(6, "u-far", _Z_FAR),
            {"seq": 7, "op": "observe",
             "params": {"role": RECHECK_ROLE, "site_id": "u-a"}},
            {"seq": 8, "op": "observe",
             "params": {"role": RECHECK_ROLE, "site_id": "u-far"}}]


def _unreadable_ground() -> list[dict]:
    """A body that states no position and a write that names no zone.  NEITHER
    may read as clear: UNREAD IS NOT CLEAR, so both fail closed.
    """
    return [_rec(1, "zones_generate", "site_pad", "u-a"),
            _rec(2, "spawn_plan", "town_body", "u-a"),   # no anchor, no pos
            _rec(3, "terrain_write", "site_pad", "u-a"),  # no entries
            {"seq": 4, "op": "observe",
             "params": {"role": RECHECK_ROLE, "site_id": "u-a"}}]


def _unrechecked_corridor() -> list[dict]:
    """A road graded, then lit, and never read again -- the 14 walker-blocking
    bodies we placed ourselves.  MEASURED: road_lighting seq 2769-2784 stands
    on T-north's grade and nothing in the tree ever looked at that corridor
    again.
    """
    return [{"seq": 1, "op": "zones_generate",
             "params": {"role": "road_segment"}, "meta": {"segment": "T9"}},
            {"seq": 2, "op": "objects_clear",
             "params": {"role": "road_surface_clearing"},
             "meta": {"segment": "T9"}},
            {"seq": 3, "op": "terrain_write",
             "params": {"role": "road_segment"}, "meta": {"segment": "T9"}},
            _rec(4, "spawn_plan", "road_lighting", "T9")]


def _unkeyed_ground() -> list[dict]:
    """A terrain_write that names no ground.  MEASURED: all 16 `regrade_cut`
    terrain_writes carry neither `site_id` nor `meta.segment`.
    """
    return _clean_site() + [{"seq": 20, "op": "terrain_write",
                             "params": {"role": "regrade_cut"}}]


def _unnamed_record() -> list[dict]:
    """A mutating record this table cannot name.  A fresh world invents roles;
    the answer must be a report, never silence.
    """
    return _clean_site() + [{"seq": 20, "op": "teleport_all",
                             "params": {"role": "town_body",
                                        "site_id": "u-a"}}]


def _two_units() -> list[dict]:
    """Two sites built back to back, each in the declared order."""
    return _clean_site(1, "u-a") + _clean_site(10, "u-b")


def _out_of_order_refused() -> bool:
    return bool(replay(_terrain_after_body())["OUT_OF_ORDER"])


def _declared_order_accepted() -> bool:
    return not replay(_clean_site())["OUT_OF_ORDER"]


def _interleaved_fixtures() -> list[dict]:
    """FRESH42'S STEP 6, IN ITS SHAPE: stock.py spawns a prop, places its
    portal, then spawns another prop for the same unit.  Measured: 38 rows of
    exactly this read OUT_OF_ORDER when every tuple was taken as a sequence."""
    u = "u-stock"
    return [_rec(1, "zones_generate", "site_pad", u),
            _rec(2, "objects_clear", "clearing", u),
            _pad(3, u), _body(4, u),
            _rec(5, "portal", "site_prop", u, pos=[512.0, 30.0, 896.0]),
            _rec(6, "spawn", "site_prop", u, pos=[513.0, 30.0, 897.0]),
            {"seq": 7, "op": "observe",
             "params": {"role": RECHECK_ROLE, "site_id": u}}]


def _generate_after_clear() -> list[dict]:
    """A SEQUENCE THE STEP DOES DECLARE, broken: a site cleared before its zone
    was generated, so the clear ran over ground the game had not made yet."""
    u = "u-early-clear"
    return [_rec(1, "objects_clear", "clearing", u),
            _rec(2, "zones_generate", "site_pad", u),
            _pad(3, u), _body(4, u),
            {"seq": 5, "op": "observe",
             "params": {"role": RECHECK_ROLE, "site_id": u}}]


def _interleaved_step_accepted() -> bool:
    return not replay(_interleaved_fixtures())["OUT_OF_ORDER"]


def _declared_sequence_refused() -> bool:
    return bool(replay(_generate_after_clear())["OUT_OF_ORDER"])


def _settlement_built() -> list[dict]:
    """A SETTLEMENT UNIT, built in order, with pieces and no corridor recheck
    -- as every Fresh42 settlement and installation was.  No step rechecks a
    placement's ground, so it is reported NO_RECHECK_STEP, never UNVERIFIED."""
    u = "u-house"
    return [_rec(1, "zones_generate", "site_pad", u),
            _rec(2, "objects_clear", "clearing", u),
            _pad(3, u), _body(4, u),
            _rec(5, "spawn", "site_prop", u, pos=[512.0, 30.0, 896.0])]


def _settlement_not_unverified() -> bool:
    rep = replay(_settlement_built())
    return (not rep["UNVERIFIED"]) and rep["NO_RECHECK_STEP"] == ["u-house"]


def _settlement_with_guidepost() -> list[dict]:
    """A settlement whose step-5 build ends with its own guidepost -- role
    `waypoint_sign`, which by role alone ranks as step 7 roadside.  The build
    stamped it step 5, as on Fresh42 (60 of them, 15 settlements)."""
    recs = _settlement_built()
    sign = _rec(6, "spawn", "waypoint_sign", "u-house",
                pos=[512.0, 30.0, 896.0])
    sign["meta"] = {"build_step": "5", "build_unit": "u-house"}
    return recs + [sign]


def _stamped_guidepost_not_unverified() -> bool:
    rep = replay(_settlement_with_guidepost())
    return (not rep["UNVERIFIED"]) and len(rep["STAMP_DISAGREES"]) == 1


# ROLE_REGISTRY_DEPENDS_ON: every role this table NAMES must be a role the
# ledger will ACCEPT.  MEASURED on Fresh42's second drive: step 8 appended its
# `corridor_recheck` observe on 5 of 5 laid corridors and the ledger refused
# all 5 ("role 'corridor_recheck' is not registered"), because the replay's
# fixtures never pass through ledger/schema.py.  The neuter `off` stops
# comparing, which is the shipped state: nothing compared them.
_ROLE_REGISTRY_MODE = "schema"    # schema | off


def unregistered_roles(known: set[str] | frozenset[str] | None = None
                       ) -> list[str]:
    """The roles buildstage names that `known` (default: the ledger schema's
    KNOWN_ROLES) would refuse to append."""
    if _ROLE_REGISTRY_MODE == "off":
        return []
    if known is None:
        from ledger import schema                              # noqa: PLC0415
        known = schema.KNOWN_ROLES
    named = (set(ROADSIDE_ROLES) | set(NETWORK_ROLES) | set(ROAD_ROLES)
             | set(BRIDGE_ROLES) | {RECHECK_ROLE})
    return sorted(r for r in named if r not in known)


def _role_registry_holds() -> bool:
    from ledger import schema                                  # noqa: PLC0415
    shipped_bad = frozenset(schema.KNOWN_ROLES) - {RECHECK_ROLE}
    return (unregistered_roles() == []
            and unregistered_roles(shipped_bad) == [RECHECK_ROLE])



def _unverified_refused() -> bool:
    return bool(replay(_unrechecked_corridor())["UNVERIFIED"])


def _verified_accepted() -> bool:
    return not replay(_clean_site())["UNVERIFIED"]


def _unkeyed_refused() -> bool:
    return bool(replay(_unkeyed_ground())["NO_UNIT"])


def _two_units_accepted() -> bool:
    return not replay(_two_units())["OUT_OF_ORDER"]


def _unnamed_refused() -> bool:
    return bool(replay(_unnamed_record())["UNCLASSIFIED"])


def _named_accepted() -> bool:
    return not replay(_clean_site())["UNCLASSIFIED"]


def _ground_after_piece_refused() -> bool:
    return bool(replay(_terrain_after_body())["TERRAIN_AFTER_PIECE"])


def _ground_before_piece_accepted() -> bool:
    return not replay(_clean_site())["TERRAIN_AFTER_PIECE"]


def _interleaved_settlement_refused() -> dict:
    """The Fresh42 shape, and WHICH zone it names."""
    rep = replay(_settlement_interleaved())
    return {"flagged": len(rep["TERRAIN_AFTER_PIECE"]),
            "elsewhere": len(rep["GROUND_ELSEWHERE"]),
            "why": (rep["TERRAIN_AFTER_PIECE"] or [{}])[0].get("why", "")}


def _two_pass_settlement_accepted() -> bool:
    """Same records, ground pass first: no write lands under a standing body."""
    return not replay(_settlement_two_pass())["TERRAIN_AFTER_PIECE"]


def _empty_ground_is_not_a_finding() -> dict:
    """A write in ground nothing occupies is REPORTED, not a finding -- and
    the SHIPPED world-wide scope must call the same records a finding."""
    rows = _write_in_unoccupied_ground()
    narrow = replay(rows)
    global _FREEZE_SCOPE
    was, _FREEZE_SCOPE = _FREEZE_SCOPE, "world"
    try:
        wide = replay(rows)
    finally:
        _FREEZE_SCOPE = was
    return {"flagged": len(narrow["TERRAIN_AFTER_PIECE"]),
            "reported": len(narrow["GROUND_ELSEWHERE"]),
            "flagged_under_shipped_world_scope":
                len(wide["TERRAIN_AFTER_PIECE"])}


def _unreadable_ground_fails_closed() -> bool:
    """A body with no position and a write with no zones BOTH refuse."""
    return bool(replay(_unreadable_ground())["TERRAIN_AFTER_PIECE"])


def _shipped_rank_returns_the_known_bad() -> dict:
    """THE NEUTER FOR RECORD_KIND, DRIVEN ON THE REAL LEDGER IS NOT AVAILABLE
    HERE -- so the same shape, in memory: with the census folded back into
    kind `road`, step 0 and step 4 both claim `("road","zones_generate")` and
    the table's own owner audit REFUSES it.
    """
    import dataclasses                                     # noqa: PLC0415
    folded = tuple(dataclasses.replace(st, records=(("road",
                                                     "zones_generate"),))
                   if st.ordinal == 0 else st for st in BUILD_STEPS)
    return {"double_owned": len(_record_owner_defects(folded)),
            "clean": len(_record_owner_defects(BUILD_STEPS))}


def _census_keeps_its_home() -> dict:
    """Stage 10's whole-network survey ranked where it belongs.

    One `zones_generate role=road_census` naming no segment, beside a segment
    that generates and writes its own.  The survey must land in NETWORK_WIDE
    (step 0 declares no units) and NOT in NO_UNIT.
    """
    rows = [{"seq": 1, "op": "zones_generate",
             "params": {"role": "road_census"}},
            {"seq": 2, "op": "objects_clear",
             "params": {"role": "road_surface_clearing"},
             "meta": {"segment": "S7"}},
            {"seq": 3, "op": "zones_generate",
             "params": {"role": "road_segment"}, "meta": {"segment": "S7"}},
            {"seq": 4, "op": "terrain_write",
             "params": {"role": "road_segment"}, "meta": {"segment": "S7"}},
            {"seq": 5, "op": "observe",
             "params": {"role": RECHECK_ROLE}, "meta": {"segment": "S7"}}]
    rep = replay(rows)
    return {"network_wide": len(rep["NETWORK_WIDE"]),
            "no_unit": len(rep["NO_UNIT"]),
            "out_of_order": len(rep["OUT_OF_ORDER"])}


def _ribbons_own_generate_is_step_four() -> bool:
    """A segment that generates its zones and then writes them, in that order,
    is IN order -- the 2 Fresh42 OUT_OF_ORDER rows were this, mis-ranked.
    """
    seg = [{"seq": 1, "op": "objects_clear",
            "params": {"role": "road_surface_clearing"},
            "meta": {"segment": "S7"}},
           {"seq": 2, "op": "zones_generate",
            "params": {"role": "road_segment"}, "meta": {"segment": "S7"}},
           {"seq": 3, "op": "terrain_write",
            "params": {"role": "road_segment"}, "meta": {"segment": "S7"}},
           {"seq": 4, "op": "observe",
            "params": {"role": RECHECK_ROLE}, "meta": {"segment": "S7"}}]
    return not replay(seg)["OUT_OF_ORDER"]


def _swapped(a: str, b: str) -> tuple[BuildStep, ...]:
    """The real table with two steps' ORDINALS exchanged, and nothing else."""
    by = {st.name: st for st in BUILD_STEPS}
    oa, ob = by[a].ordinal, by[b].ordinal
    out = []
    for st in BUILD_STEPS:
        if st.name == a:
            out.append(BuildStep(ob, st.name, st.owner, st.produces,
                                 st.requires, records=st.records))
        elif st.name == b:
            out.append(BuildStep(oa, st.name, st.owner, st.produces,
                                 st.requires, records=st.records))
        else:
            out.append(st)
    return tuple(sorted(out, key=lambda s: s.ordinal))


def _dependent_swap_refused() -> bool:
    """`road_ground` consumes `placed_objects`, which `road_clear` produces --
    the edge is in ribbon.py's own argument parser.  Exchanging them must be
    refused at import.
    """
    return bool(pipeline.audit_graph(_swapped("road_clear", "road_ground")))


def _independent_swap_accepted() -> bool:
    """`site_ground` and `road_clear` consume nothing of each other's: disjoint
    ground, and the clear/flatten equivalence is MEASURED (two pads 256 m apart,
    both orderings, same two survivors).  Exchanging them must SURVIVE --
    otherwise "the order is checked" is satisfied by a rule that refuses every
    permutation.
    """
    return not pipeline.audit_graph(_swapped("site_ground", "road_clear"))


# THE MEASURED REFUSAL, AS A FIXTURE.  Fresh42 step 4, 2026-09-24, serial
# offline `--validate` over all 14 segments: T1-portalhub-temple rc 8 (water
# sign), S7-stathub-wtnorth rc 0.  T1 is FIRST in the unit list.
_REFUSED_SEGMENT = "T1-portalhub-temple"
_BUILT_SEGMENT = "S7-stathub-wtnorth"
_GROUND_REFUSED = {"road_ground": {_REFUSED_SEGMENT}}
# THE CORPORA, CONSTRUCTED.  Two segments and two bodies, in memory, so the
# case keeps describing the defect the day Fresh42's corpus moves -- and so
# the fixture can never answer an empty list the way a probe scope pointed at
# /nonexistent does.
_SEGMENT_CORPUS = (_REFUSED_SEGMENT, _BUILT_SEGMENT)
# THE INSTALLATIONS, CONSTRUCTED from Fresh42's drives.  iron-era-workshop
# refused step 1 on drive 3 (clamp: 442 samples beyond +/-8 m) and, re-tried
# inside `*placements*`, refused step 5 too (unsheltered_deck);
# meadows-starter-hall stood (spawn_plan seq 1992); early-dock is the site
# whose OWN plan refused step 5 on an earlier drive (co_located_rows, see
# terraform/sites.py) -- a refusal step 5 must still report as its own.
_SITE_REFUSED_UPSTREAM = "pre-bonemass/iron-era-workshop"
_SITE_BUILT = "pre-eikthyr/meadows-starter-hall"
_SITE_REFUSES_ITSELF = "pre-elder/early-dock"
_SETTLEMENT = "stenvik"
_INSTALLATIONS = (_SITE_REFUSED_UPSTREAM, _SITE_BUILT, _SITE_REFUSES_ITSELF)
_BODY_CORPUS = _INSTALLATIONS + (_SETTLEMENT,)


def _step(name: str) -> BuildStep:
    return next(st for st in BUILD_STEPS if st.name == name)


def _lamp_on_ungraded_segment_refused() -> bool:
    """A segment whose carriageway was REFUSED must not be lit.

    `roadside` requires `road_ground` and draws from the same domain, so the
    refused segment is blocked BY NAME.
    """
    return _REFUSED_SEGMENT in blocked_units(
        _step("roadside"), _SEGMENT_CORPUS, _GROUND_REFUSED)


def _sibling_segment_still_built() -> bool:
    """A segment whose own ground WAS written is not blocked by its neighbour.

    This is the arm the world-wide label key failed: under it the build
    returned at T1 and S7 -- rc 0, fully buildable -- was never reached.
    """
    return _BUILT_SEGMENT not in blocked_units(
        _step("roadside"), _SEGMENT_CORPUS, _GROUND_REFUSED)


def _cross_domain_debt_named() -> bool:
    """A refused SEGMENT is not a refused BODY, and is reported rather than
    guessed at: `bodies` consumes `road_ground` across a domain boundary, so
    nothing is blocked and the outstanding grade is NAMED.
    """
    bodies = _step("bodies")
    return (not blocked_units(bodies, _BODY_CORPUS, _GROUND_REFUSED)
            and cross_domain_debt(bodies, _GROUND_REFUSED)
            .get("road_ground") == {_REFUSED_SEGMENT})


def _upstream_refused_site_blocked() -> bool:
    """The body of a site step 1 refused is BLOCKED by name at step 5."""
    return _SITE_REFUSED_UPSTREAM in blocked_units(
        _step("bodies"), _body_units_from(_INSTALLATIONS, (_SETTLEMENT,)),
        {"site_prepare": {_SITE_REFUSED_UPSTREAM}},
        ground=_body_ground_from(_INSTALLATIONS))


def _installation_bodies_run() -> dict:
    """Steps 1, 2 and 5 of THIS table, driven by `run` itself over the
    constructed corpus in a throwaway work dir.  Each command is a stand-in
    that exits the rc the real tool did on the drive: step 1 refuses
    iron-era-workshop; step 5 refuses iron-era-workshop (when re-tried) and
    early-dock; everything else is rc 0.  A unit covering several sites
    refuses when any site it covers does -- which is what the one-unit
    `*placements*` call did.  Read back from the journal `run` wrote.
    """
    import contextlib                                       # noqa: PLC0415
    import io                                               # noqa: PLC0415
    import tempfile                                         # noqa: PLC0415
    fails = {"site_prepare": {_SITE_REFUSED_UPSTREAM},
             "bodies": {_SITE_REFUSED_UPSTREAM, _SITE_REFUSES_ITSELF}}

    def stand_in(name: str):
        def av(_rt: Runtime, unit: str) -> list[list[str]]:
            covers = set(_INSTALLATIONS) if unit == ALL_PLACEMENTS else {unit}
            rc = 1 if covers & fails.get(name, set()) else 0
            return [[PY, "-S", "-c", f"raise SystemExit({rc})"]]
        return av

    steps = (
        dc_replace(_step("site_prepare"), units=lambda s: list(_INSTALLATIONS),
                   argv=stand_in("site_prepare")),
        dc_replace(_step("site_ground"), units=lambda s: list(_INSTALLATIONS),
                   argv=stand_in("site_ground")),
        dc_replace(_step("bodies"),
                   units=lambda s: _body_units_from(_INSTALLATIONS,
                                                    (_SETTLEMENT,)),
                   stands_on=lambda s: _body_ground_from(_INSTALLATIONS),
                   argv=stand_in("bodies")),
    )
    with tempfile.TemporaryDirectory(prefix="buildstage-bodies-") as tmp:
        scope = Scope("SelftestBodies", "SelftestBodies", Path(tmp),
                      Path(tmp), Path(tmp))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            exit_rc = run(Runtime(scope, "Selftest"), steps=steps)
        log = json.loads((Path(tmp) / "build" / "buildstage.json").read_text())
    rows = [e for e in log if e.get("name") == "bodies" and e.get("unit")]
    return {"exit": exit_rc,
            "rc": {e["unit"]: e["rc"] for e in rows if "rc" in e},
            "blocked_by": {e["unit"]: e["blocked_by"] for e in rows
                           if "blocked_by" in e},
            "partial": next((ln for ln in err.getvalue().splitlines()
                             if ln.startswith("PARTIAL BUILD")), "")}


def _collar_retry_run(resume_after: bool = False) -> dict:
    """COLLAR_RETRY_DEPENDS_ON, driven by `run` itself.  A `road_ground` stand-in
    that exits rc 7 unless it is handed `--no-connection-collar` -- the T6
    shape of Fresh42 drive 8 -- and a sibling that exits 0 either way.  Read
    back from the journal `run` wrote."""
    import contextlib                                       # noqa: PLC0415
    import io                                               # noqa: PLC0415
    import tempfile                                         # noqa: PLC0415

    def av(_rt: Runtime, unit: str) -> list[list[str]]:
        code = ("import sys; raise SystemExit(0 if %r != 'T6' or "
                "'--no-connection-collar' in sys.argv else 7)" % unit)
        return [[PY, "-S", "-c", code]]

    steps = (dc_replace(_step("road_ground"),
                        units=lambda s: ["T6", "S7"], argv=av),)
    with tempfile.TemporaryDirectory(prefix="buildstage-collar-") as tmp:
        scope = Scope("SelftestCollar", "SelftestCollar", Path(tmp),
                      Path(tmp), Path(tmp))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            exit_rc = run(Runtime(scope, "Selftest"), steps=steps)
            before = len(json.loads(
                (Path(tmp) / "build" / "buildstage.json").read_text()))
            if resume_after:
                run(Runtime(scope, "Selftest"), steps=steps, resume=True)
        log = json.loads((Path(tmp) / "build" / "buildstage.json").read_text())
    resumed = [(e["unit"], e["rc"]) for e in log[before:]
               if e.get("name") == "road_ground" and e.get("unit")
               and "rc" in e]
    rows = [e for e in log if e.get("name") == "road_ground"
            and e.get("unit") and "rc" in e]
    return {"exit": exit_rc, "resumed_rcs": resumed,
            "rcs": [(e["unit"], e["rc"]) for e in rows],
            "flagged": [e["unit"] for e in rows
                        if "--no-connection-collar" in e["cmd"]]}


def _declared_site_unit_case() -> bool:
    """A site a person declared is a unit of step 5, after the towns and the
    outliers, built by the module that owns its kind -- run as whoever runs
    the build, in its dry form when the build is dry -- and a seed that
    declares none gains no unit."""
    import os                                               # noqa: PLC0415
    import tempfile                                         # noqa: PLC0415
    with tempfile.TemporaryDirectory() as td:
        plan = Path(td) / worldseed.plan_name("Testseed")
        base = {"seed": "Testseed", "towns": {"stenvik": {}},
                "outliers": [{"id": "wt-spawn"}]}
        plan.write_text(json.dumps({**base, "declared_sites": [
            {"id": "harbour-city-x", "kind": "harbour_city"},
            {"id": "harbour-village-y", "kind": "harbour_village"}]}))
        prior = os.environ.get(worldseed.PLAN_ENV)
        os.environ[worldseed.PLAN_ENV] = str(plan)
        try:
            scope = Scope("Testseed", "Testseed", Path(td) / "work",
                          Path(td), Path(td))
            step = next(st for st in BUILD_STEPS if st.name == "bodies")
            units = step.units(scope)
            wet = Runtime(scope, "Tester")
            dry = Runtime(scope, "Tester", dry=True)
            city = step.argv(wet, "harbour-city-x")
            village = step.argv(wet, "harbour-village-y")
            city_dry = step.argv(dry, "harbour-city-x")
            plan.write_text(json.dumps(base))
            none = step.units(scope)
        finally:
            if prior is None:
                os.environ.pop(worldseed.PLAN_ENV, None)
            else:
                os.environ[worldseed.PLAN_ENV] = prior

    def runs(cmds, module, tail, unit) -> bool:
        return (len(cmds) == 1 and cmds[0][1].endswith(f"settlements/{module}")
                and cmds[0][2] == tail
                and cmds[0][cmds[0].index("--site") + 1] == unit
                and cmds[0][cmds[0].index("--actor") + 1] == "Tester"
                and "--report" in cmds[0])

    return (units == ["stenvik", "wt-spawn", "harbour-city-x",
                      "harbour-village-y"]
            and runs(city, "harbour_city.py", "ubuild", "harbour-city-x")
            and runs(village, "harbour_b.py", "build", "harbour-village-y")
            and "--dry" not in city[0] and "--dry" in city_dry[0]
            and none == ["stenvik", "wt-spawn"])


def _collar_retry_case() -> bool:
    """T6 is refused with the collar and laid without it; S7 is asked once; a
    --resume afterwards re-runs NOTHING (the laid retry is the unit's answer).
    Under the `collar retry off` NEUTER T6's refusal stands and this is False."""
    got = _collar_retry_run()
    again = _collar_retry_run(resume_after=True)
    return (got["exit"] == 0
            and got["rcs"] == [("T6", 7), ("T6", 0), ("S7", 0)]
            and got["flagged"] == ["T6"]
            and again["resumed_rcs"] == [])


def _live_replace_case() -> bool:
    """The live pair is refused without --replace-live; with it, only over an
    archived ledger that is not the live one.  Under the `live replace
    unguarded` NEUTER the archive is not asked for and this is False."""
    import tempfile                                         # noqa: PLC0415
    seed, world = worldseed.DEFAULT_SEED, worldseed.DEFAULT_WORLD
    with tempfile.TemporaryDirectory(prefix="buildstage-live-") as tmp:
        runs = Path(tmp)
        (runs / world).mkdir()
        (runs / world / "ledger.jsonl").write_text("new\n")
        plain = live_replace_refusal(seed, world, False, runs)
        no_archive = live_replace_refusal(seed, world, True, runs)
        (runs / f"{world}.archive-x").mkdir()
        (runs / f"{world}.archive-x" / "ledger.jsonl").write_text("new\n")
        same_file = live_replace_refusal(seed, world, True, runs)
        (runs / f"{world}.archive-x" / "ledger.jsonl").write_text("old\n")
        ok = live_replace_refusal(seed, world, True, runs)
        other = live_replace_refusal("Fresh42", "Fresh42", False, runs)
    return (bool(plain) and bool(no_archive) and bool(same_file)
            and ok is None and other is None)


def _second_build_refused_case() -> bool:
    """A build started while another run holds the world is REFUSED rc 75 before
    step 1 -- nothing runs, no journal is written -- and the same build runs
    once the holder is gone.  The live default pair's own refusal (rc 5) comes
    first and leaves NO lock file in the directory it refused to touch.  Under
    the `writer lock off` NEUTER the second build starts and this is False."""
    import contextlib                                       # noqa: PLC0415
    import io                                               # noqa: PLC0415
    import tempfile                                         # noqa: PLC0415
    with tempfile.TemporaryDirectory(prefix="buildstage-lock-") as tmp:
        marker = Path(tmp) / "ran"

        def av(_rt: Runtime, unit: str) -> list[list[str]]:
            return [[PY, "-S", "-c",
                     f"open({str(marker)!r}, 'a').write({unit!r})"]]

        steps = (dc_replace(_step("road_ground"), units=lambda s: ["T6"],
                            argv=av),)
        scope = Scope("SelftestLock", "SelftestLock", Path(tmp),
                      Path(tmp), Path(tmp))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            holder = pipeline.acquire_writer_lock(scope, "another run")
            if isinstance(holder, str):
                return False
            try:
                while_held = run(Runtime(scope, "Selftest"), steps=steps)
            finally:
                holder.release()
            ran_while_held = marker.exists()
            journal_while_held = (Path(tmp) / "build" / "buildstage.json").exists()
            after = run(Runtime(scope, "Selftest"), steps=steps)
        live_work = Path(tmp) / "live"
        live = Scope(worldseed.DEFAULT_SEED, worldseed.DEFAULT_WORLD,
                     live_work, live_work, live_work)
        with contextlib.redirect_stderr(err):
            live_rc = run(Runtime(live, "Selftest"), steps=steps)
        no_live_file = not (live_work / "writer.lock").exists()
        return (while_held == pipeline.WRITER_LOCK_EXIT
                and not ran_while_held and not journal_while_held
                and after == 0 and marker.exists()
                and live_rc == 5 and no_live_file)


def _staged_ops_interleave_safely() -> bool:
    """Two processes each stage an `objects_remove` on ONE Upgrade World queue
    (`stop` empties it, the verb stages into it, `start` runs it), the second
    arriving while the first is between its `stop` and its `start` -- the
    13:02:41-13:02:43 sequence in the Ulfsland staging server's log.  Both
    removals must run and both postconditions hold; a holder that never lets
    go is refused after its wait with nothing sent.  Under the `staged lock
    off` NEUTER the second `stop` empties the first's staged op and the first
    postcondition reads the census count back."""
    import importlib                                        # noqa: PLC0415
    import tempfile                                         # noqa: PLC0415
    import threading                                        # noqa: PLC0415
    import types                                            # noqa: PLC0415
    _install_modules()                       # puts terraform/ on sys.path
    R = importlib.import_module("ledger.replay")
    world = {"objs": {"A": 1, "B": 1}, "queue": []}
    a_staged, b_started = threading.Event(), threading.Event()

    class QueueServer(R.Server):
        """ONE staged queue for every connection, as measured."""

        def __init__(self) -> None:
            super().__init__(dry=False)
            self.rc = types.SimpleNamespace(host="127.0.0.1", port=24560)

        def command(self, cmd: str) -> str:
            if cmd == "consoleCommand stop":
                world["queue"].clear()
            elif cmd == "consoleCommand start":
                for ident in world["queue"]:
                    world["objs"][ident] = 0
                world["queue"].clear()
            return f"Command '{cmd}' executed."

        def console(self, cmd: str, settle: float = 2.0) -> list[str]:
            t = cmd.split()
            kv = dict(a.split("=", 1) for a in t[1:] if "=" in a)
            if t[0] == "objects_remove":
                world["queue"].append(kv["id"])
                if kv["id"] == "A":      # A is mid-bracket when B arrives
                    a_staged.set()
                    b_started.wait(5.0)
                    time.sleep(0.3)
                return ["Removing 1 object."]
            if t[0] == "objects_count":
                n = world["objs"].get(kv["id"], 0)
                return [f"Total: {n}"] + ([f"{kv['id']}: {n}"] if n else [])
            return []

    def rec(ident: str) -> dict:
        return {"wire": [f"objects_remove id={ident} ignore=_* pos=0,0 max=5"],
                "params": {},
                "expect": {"objects_count": {
                    "ids": ident, "ignore": "_*", "max": 5, "pos": [0, 0],
                    "tolerance": 0, "total": 0}}}

    out: dict[str, bool] = {}

    def drive(ident: str) -> None:
        if ident == "B":
            a_staged.wait(5.0)
            b_started.set()
        _, checks, _ = R.send_and_verify(QueueServer(), rec(ident))
        out[ident] = all(c["ok"] for c in checks)

    saved = (R.STAGED_SETTLE_S, R.STAGED_POLL_S, R._STAGED_LOCK_DIR)
    with tempfile.TemporaryDirectory(prefix="buildstage-staged-") as tmp:
        R.STAGED_SETTLE_S, R.STAGED_POLL_S = 0.4, 0.05
        R._STAGED_LOCK_DIR = Path(tmp)
        try:
            threads = [threading.Thread(target=drive, args=(i,))
                       for i in ("A", "B")]
            for t in threads:
                t.start()
            for t in threads:
                t.join(30.0)
            both = out == {"A": True, "B": True}
            # A holder that never lets go: refused after the wait, nothing sent.
            srv = QueueServer()
            sent_before = len(srv.sent)
            refused = False
            with R.staged_exclusive(srv, rec("A")["wire"]):
                try:
                    with R.staged_exclusive(srv, rec("B")["wire"], wait_s=0.2):
                        pass
                except R.LedgerError:
                    refused = True
            refused = refused or R._STAGED_LOCK_MODE == "off"
            return both and refused and len(srv.sent) == sent_before
        finally:
            R.STAGED_SETTLE_S, R.STAGED_POLL_S, R._STAGED_LOCK_DIR = saved


def _upstream_refusal_blocked_at_bodies(r: dict) -> bool:
    """iron-era-workshop is BLOCKED by name, naming step 1, and never run."""
    return (_SITE_REFUSED_UPSTREAM not in r["rc"]
            and any(b.startswith("site_prepare:")
                    for b in r["blocked_by"].get(_SITE_REFUSED_UPSTREAM, ())))


def _siblings_built_own_refusal_kept(r: dict) -> bool:
    """Its siblings stand as their own units, early-dock refuses as its own
    unit, and the build still does not read as clean."""
    return (r["rc"].get(_SITE_BUILT) == 0 and r["rc"].get(_SETTLEMENT) == 0
            and (r["rc"].get(_SITE_REFUSES_ITSELF) or 0) != 0
            and r["exit"] != 0
            and f"bodies: {[_SITE_REFUSES_ITSELF]}" in r["partial"])


# THE CROSSINGS, CONSTRUCTED from Fresh42's drive-2 state.  S4-meadhall-
# southeast-x01 stands on S4, which step 4 laid (rc 0, seq 1498);
# T5-temple-dockshore-x01 stands on T5, which step 4 refused (water_fill).
# In memory, so the case keeps describing the defect when that corpus moves.
_DECK_ON_LAID = "S4-meadhall-southeast-x01"
_DECK_ON_UNLAID = "T5-temple-dockshore-x01"
_LAID_SEGMENT = "S4-meadhall-southeast"
_UNLAID_SEGMENT = "T5-temple-dockshore"
_CROSSING_CORPUS = (_DECK_ON_UNLAID, _DECK_ON_LAID)
_CROSSING_GROUND = {_DECK_ON_LAID: {"segment": (_LAID_SEGMENT,)},
                    _DECK_ON_UNLAID: {"segment": (_UNLAID_SEGMENT,)}}
_BANK_REFUSED = {"road_ground": {_UNLAID_SEGMENT}}


def _deck_on_unlaid_bank_blocked() -> bool:
    """A crossing whose bank's road was REFUSED is BLOCKED by name."""
    return _DECK_ON_UNLAID in blocked_units(
        _step(DECK_STEP), _CROSSING_CORPUS, _BANK_REFUSED,
        ground=_CROSSING_GROUND)


def _deck_on_laid_bank_built() -> bool:
    """Its sibling on a LAID road is not blocked, and a refusal the pairing
    answers is not also reported as a debt."""
    decks = _step(DECK_STEP)
    return (_DECK_ON_LAID not in blocked_units(
                decks, _CROSSING_CORPUS, _BANK_REFUSED, ground=_CROSSING_GROUND)
            and not cross_domain_debt(decks, _BANK_REFUSED,
                                      ground=_CROSSING_GROUND))


def _decked_road(recheck: str) -> list[dict]:
    """S4 cleared and graded (steps 3, 4), then its deck (step 6.5, unit the
    crossing, stamped with the segment it stands on as boardwalk now stamps
    every batch), then S4's recheck AFTER the deck, BEFORE it, or never.
    The deck's anchor is in the zone the grade moved, so the ground freeze
    is asked a real question and answers it clean."""
    seg, cid = _LAID_SEGMENT, _DECK_ON_LAID
    graded = [{"seq": 1, "op": "objects_clear",
               "params": {"role": "road_surface_clearing"},
               "meta": {"segment": seg}},
              {"seq": 2, "op": "zones_generate",
               "params": {"role": "road_segment"}, "meta": {"segment": seg}},
              {"seq": 3, "op": "terrain_write",
               "params": {"role": "road_segment",
                          "entries": [{"zone": [6, -7]}]},
               "meta": {"segment": seg}}]
    deck = {"op": "spawn_plan",
            "params": {"role": "bridge_body_place", "site_id": cid,
                       "anchor": {"x": 396.0, "y": 33.452, "z": -437.0,
                                  "yaw": 180.0},
                       "flatten": "FORBIDDEN"},
            "meta": {"stands_on": [seg]}}
    check = {"op": "observe", "params": {"role": RECHECK_ROLE, "site_id": seg}}
    order = {"after": [deck, check], "before": [check, deck],
             "never": [deck]}[recheck]
    return graded + [dict(r, seq=4 + i) for i, r in enumerate(order)]


def _deck_never_re_read_refused() -> bool:
    """A deck whose road was never re-read, or re-read before it landed, is
    UNVERIFIED -- both, because a recheck of the hole is not a recheck of
    the deck."""
    return all(replay(_decked_road(w))["UNVERIFIED"] == [_DECK_ON_LAID]
               for w in ("never", "before"))


def _deck_re_read_with_its_road() -> dict:
    rep = replay(_decked_road("after"))
    deck = next(r for r in _decked_road("after")
                if (r.get("params") or {}).get("role") == "bridge_body_place")
    return {"out_of_order": len(rep["OUT_OF_ORDER"]),
            "unverified": len(rep["UNVERIFIED"]),
            "no_recheck_step": len(rep["NO_RECHECK_STEP"]),
            "unclassified": len(rep["UNCLASSIFIED"]),
            "terrain_after_piece": len(rep["TERRAIN_AFTER_PIECE"]),
            "deck_step": _name_of(rank_of(deck) or (-1, -1))}


def _two_part_bridge() -> list[dict]:
    """ULFSLAND'S S1, IN ITS SHAPE: one site_id, two structures, each
    generated, cleared and placed in the proven order (seq 12-20)."""
    u = "bridge-s1-temple-strait"
    ops = ("zones_generate", "zones_generate", "objects_clear", "spawn_plan",
           "zones_generate", "objects_clear", "spawn_plan")
    return [_rec(12 + i, op, "bridge_assembly", u,
                 **({"anchor": {"x": -318.0, "y": 30.6, "z": -64.0,
                                "yaw": 0.0}} if op == "spawn_plan" else {}))
            for i, op in enumerate(ops)]


def _bridge_ranked_as_decks() -> dict:
    rows = _two_part_bridge()
    rep = replay(rows)
    return {"out_of_order": len(rep["OUT_OF_ORDER"]),
            "unclassified": len(rep["UNCLASSIFIED"]),
            "steps": sorted({_name_of(rk) for rk in map(rank_of, rows) if rk})}


def _nine_row_table() -> tuple[BuildStep, ...]:
    """THE SHIPPED TABLE, rebuilt: no `decks` row, and an inputs row that
    does not declare the crossing contract pipeline row 12 hands it."""
    def shipped_inputs(s: Scope) -> list[Artefact]:
        return [a for a in _inputs(s) if a.label != "crossings"]
    return tuple(dc_replace(st, produces=shipped_inputs) if st.inherited
                 else st for st in BUILD_STEPS if st.name != DECK_STEP)


def _nine_row_table_refused() -> list[str]:
    return unbuilt_defects(_nine_row_table())


def _deck_step_swaps_refused() -> dict:
    """Ahead of the road it stands on, or ahead of the last step that still
    writes ground: both INVERTED at import."""
    return {"road_ground": pipeline.audit_graph(_swapped("road_ground",
                                                         DECK_STEP)),
            "bodies": pipeline.audit_graph(_swapped("bodies", DECK_STEP))}


def _deck_step_placed() -> dict:
    by = {st.name: st.ordinal for st in BUILD_STEPS}
    return {"audit": audit(),
            "after": [n for n in ("road_ground", "bodies")
                      if by[n] < by[DECK_STEP]],
            "before": [n for n in ("roadside", "recheck")
                       if by[DECK_STEP] < by[n]]}


# THE COMMANDS, CONSTRUCTED.  Step 3's road_clear census for one segment, as
# the journal holds it and as a second agent spells it: identical but for the
# name on the receipt.  The others differ in something that REACHES THE
# WORLD, which is a different command however it is signed.
#
# `--actor` IS NOT INERT and the declaration must not pretend it is: it is
# threaded into clear.py and ribbon.py and lands in the ledger records as
# provenance, which is why `run` refuses to default it.  It is dropped from
# the key because it changes THE RECORD AND NOT THE GROUND -- the work is
# already done, already recorded, and already attributed to whoever did it.
# Re-running it would not re-attribute anything; it would append a SECOND set
# of records for one piece of work.
_CMD_BUILDFRESH = ["python3", "roads/clear.py", "census", "--segment", "T1",
                   "--segments", "/w/segments.yaml", "--world", "Fresh42",
                   "--out", "/w/T1.census.json", "--actor", "BuildFresh"]
_CMD_LAYROAD = [("LayRoad" if c == "BuildFresh" else c)
                for c in _CMD_BUILDFRESH]
# One re-pointing per flag that reaches the world.
_CMD_REPOINTED = {
    "--segment": "T9", "--segments": "/w/OTHER.yaml", "--world": "Nyworld",
    "--out": "/w/OTHER.census.json",
}


def _repointed_command_is_rerun() -> bool:
    """A command whose INPUTS moved is not the work the journal recorded.

    Every world-reaching flag is re-pointed ONE AT A TIME and each must give a
    different key; a single example would pass a key that happened to notice
    one argument.
    """
    base = resume_key(3, "T1", _CMD_BUILDFRESH)
    for flag, value in _CMD_REPOINTED.items():
        cmd = list(_CMD_BUILDFRESH)
        cmd[cmd.index(flag) + 1] = value
        if resume_key(3, "T1", cmd) == base:
            return False
    return True


def _same_work_by_another_agent_resumes() -> bool:
    """Who ran it is stamped on the record, not part of what it did."""
    return (resume_key(3, "T1", _CMD_BUILDFRESH)
            == resume_key(3, "T1", _CMD_LAYROAD)
            and actor_of(_CMD_BUILDFRESH) == "BuildFresh"
            and actor_of(_CMD_LAYROAD) == "LayRoad")


# THE KNOWN-BAD RESOLVER, VERBATIM.  This is `terraform/flatten.py:66` as it
# stood when it wrote 13 Fresh42 entry files into Ulfsland's watched data
# directory: a module constant, taking a world and ignoring it.  Kept as a
# CONSTRUCTED fixture rather than by reaching for git history, so the case
# describes the defect for as long as the defect is worth describing.
_ULFSLAND_DATA_DIR_LITERAL = Path(
    "/media/big4/projects/game/valheim/Ulfsland/config_merged/bepinex/data")


def _shared_install_refused() -> bool:
    """A write path that ignores the world it is given must be refused."""
    return bool(world_scope_defects(lambda w: _ULFSLAND_DATA_DIR_LITERAL))


# THE SHIPPED CALLS, AS A FIXTURE, BUILT IN MEMORY.  The tree they name is
# fixed; a check that reads the live files could be passed by deleting the
# files.  The last row is the reason this is not a grep: `"Ulf" + "sland"` is
# the same defect and a substring search accepts it.
_SHIPPED_LEDGER_OPENS = (
    ("roads/regrade.py", 548, '"Ulfsland"'),
    ("roads/regrade.py", 637, '"Ulfsland"'),
    ("roads/boardwalk.py", 3879, '"Ulfsland"'),
    ("roads/cone.py", 626, '"Ulfsland"'),
    ("roads/north.py", 949, '"Ulfsland"'),
    ("roads/north.py", 1764, '"Ulfsland"'),
    ("obfuscated.py", 1, '"Ulf" + "sland"'),
)


def _hardcoded_ledger_world_refused() -> dict:
    """The six shipped literals plus one a grep cannot see."""
    bad = ledger_world_defects(calls=list(_SHIPPED_LEDGER_OPENS))
    return {"defects": len(bad),
            "callsites": len(_SHIPPED_LEDGER_OPENS),
            "obfuscated_caught": sum(1 for b in bad
                                     if b.startswith("obfuscated.py"))}


def _every_ledger_open_keys_on_its_world() -> dict:
    """THE LIVE SWEEP.  Reads the real tree, not a fixture: the fixture above
    proves the check can refuse, and only this proves the tree is out of the
    known-bad state.
    """
    calls = ledger_world_calls()
    return {"callsites": len(calls),
            "defects": len(ledger_world_defects(calls=calls))}


def _transport_agreement() -> dict:
    """THE SECOND ENV HOME, BOTH ARMS, WITHOUT A SOCKET.

    A session recording into Fresh42, judged against the transport the tree
    resolves for it.  Arm one hands `transport_scope` the SHIPPED literal
    "Ulfsland" it carried until 2026-09-24 and the check must REFUSE; arm two
    lets it resolve and the same run must OPEN.  Arm three is a socket nobody
    attributed, which must be RECORDED rather than refused -- a fresh seed's
    bare server is addressed exactly that way.
    """
    import os                                              # noqa: PLC0415
    sys.path.insert(0, str(HERE / "terraform"))
    import rcon                                            # noqa: PLC0415
    keys = ("VALHEIM_WORLD", "VALHEIM_CONTAINER", "VALHEIM_RCON_HOST")
    saved = {k: os.environ.get(k) for k in keys}
    for k in keys:
        os.environ.pop(k, None)
    try:
        def ask(scope):
            if _TRANSPORT_MODE == "blind":
                return ""
            return rcon.transport_disagreement("Fresh42", scope)

        # `literal` is the shipped constant: the socket answers Ulfsland no
        # matter what world the run resolved, so BOTH arms see it.
        good = "Ulfsland" if _TRANSPORT_MODE == "literal" else "Fresh42"
        shipped = ask(rcon.transport_scope(resolved="Ulfsland"))
        resolved = ask(rcon.transport_scope(resolved=good))
        socket_only = ask({"world": "", "declared": False,
                           "how": "$VALHEIM_RCON_HOST names a socket"})
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return {"shipped_refuses": bool(shipped),
            "resolved_opens": not resolved,
            "undeclared_is_recorded_not_refused": not socket_only}


def _scoped_install_accepted() -> bool:
    """The LIVE resolver every terrain step writes through must key on the
    world.  This one case reads the real tool rather than a fixture, on
    purpose: the fixture proves the check can refuse, and only reading
    `flatten.data_dir` proves the tree is not back in the known-bad state.

    Its own declaration is handed in, because the resolver is allowed to be
    pointed at an install an operator named -- see `world_scope_defects`.  A
    case that ignored it would refuse every run driving a server whose
    directory is not `<root>/<World>/...`, which is every fresh seed.
    """
    import importlib                                       # noqa: PLC0415
    sys.path.insert(0, str(HERE / "terraform"))
    flatten = importlib.import_module("flatten")
    return not world_scope_defects(flatten.data_dir,
                                   declared=flatten.declared_data_dirs())


# ---------------------------------------------------------------------------
# INSTALLATION_WRITE_PATH_DEPENDS_ON (terraform/flatten.py), READ FROM THIS
# SIDE.  One installation built through steps 1, 2, 5 and 6 by the REAL
# emitters -- `clear.records`, `flatten.installation_pad_record`,
# `sites.body_record`, `stock.fixture_record`, each sent by
# `flatten.install_emit` -- into `flatten.memory_session()`: a real
# `LiveBuilder.emit`, a real `writer.Ledger` in a temporary directory with
# every cross-record guard armed, and an in-memory world.  Then step 5's own
# settlement pad chain writes the same zone.  The replay is handed the chain
# that produced; nothing below is a hand-written record.
# ---------------------------------------------------------------------------
_INSTALL_CHAIN: dict[str, dict] = {}


def _install_modules() -> dict:
    """`flatten` by the name every tool imports it under (the neuter rebinds
    `sys.modules["flatten"]`), and the three step drivers BY PATH, because
    `clear`, `sites` and `stock` are each a second module's name too
    (roads/clear.py, network/sites.py)."""
    import importlib                                        # noqa: PLC0415
    import importlib.util                                   # noqa: PLC0415
    tf = str(HERE / "terraform")
    if tf not in sys.path:
        sys.path.insert(0, tf)
    mods = {"flatten": importlib.import_module("flatten")}
    for key, rel in (("clear", "clearing/clear.py"),
                     ("sites", "terraform/sites.py"),
                     ("stock", "terraform/stock.py")):
        name = f"_buildstage_{key}"
        if name not in sys.modules:
            spec = importlib.util.spec_from_file_location(name, HERE / rel)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            spec.loader.exec_module(mod)
        mods[key] = sys.modules[name]
    return mods


def _installation_chain() -> dict:
    """The records the chain holds after one installation (steps 1, 2, 5, 6)
    and one later settlement pad in its zone, under the CURRENT write-path
    mode.  Cached per mode: it depends on nothing else, and the replay-side
    neuters re-read the same records."""
    import contextlib                                       # noqa: PLC0415
    import hashlib                                          # noqa: PLC0415
    import io                                               # noqa: PLC0415
    m = _install_modules()
    FL = m["flatten"]
    mode = FL._WRITE_PATH_MODE
    if mode in _INSTALL_CHAIN:
        return _INSTALL_CHAIN[mode]
    place = FL._FX_INSTALL
    x, z = float(place["solved"]["x"]), float(place["solved"]["z"])
    y = float(place["solved"]["flatten_cost"]["target_y"])
    with FL.memory_session("BuildstageSelftest") as b, \
            contextlib.redirect_stdout(io.StringIO()):
        C = m["clear"]
        for rec in C.records(C.clearing_area(place, 0.0), C.DEFAULT_KEEP,
                             "fixture"):
            FL.install_emit(b, rec)                          # step 1
        FL._fx_install(b, place)                             # step 2
        plan = b.root.parent / "fixture-body.plan"           # step 5
        plan.write_text("".join(
            f"spawn_object wood_floor pos={pz:g},{px:g},{y:g} rot=0,0,0 "
            f"from=0,0,0\n" for px, pz in ((x - 2, z - 2), (x, z), (x + 2, z - 2))))
        row = {"preset": "fixture", "plan": str(plan), "at": [x, y, z, 0.0]}
        unit = m["sites"].body_unit(place, row,
                                    hashlib.sha256(plan.read_bytes()).hexdigest())
        FL.install_emit(b, m["sites"].body_record(b, place, row, unit, [unit]))
        FL.install_emit(b, m["stock"].fixture_record(         # step 6
            place["id"], "station",
            {"prefab": "piece_workbench", "x": x + 3.0, "y": y, "z": z + 2.0,
             "yaw": 0.0}, "fixture"))
        FL._fx_later_settlement_pad(b, FL._FX_TOWN)          # step 5, later
        _INSTALL_CHAIN[mode] = {"records": b.led.records(),
                                "install": place["id"],
                                "later": FL._FX_TOWN["id"]}
    return _INSTALL_CHAIN[mode]


def _installation_piece_under_a_later_write_refused() -> bool:
    """The later pad moves the zone an installation body and fixture stand
    in: TERRAIN_AFTER_PIECE, IF the replay can see them."""
    ch = _installation_chain()
    return any(r["unit"] == ch["later"]
               for r in replay(ch["records"])["TERRAIN_AFTER_PIECE"])


def _installation_read_in_declared_order() -> dict:
    """Every installation record ranked, keyed on its site, in step order."""
    ch = _installation_chain()
    mine = [r for r in ch["records"] if unit_of(r) == ch["install"]]
    rep = replay(mine)
    return {"records": len(mine),
            "steps": sorted({rk[0] for rk in map(rank_of, mine) if rk}),
            "findings": len(rep["OUT_OF_ORDER"]) + len(rep["NO_UNIT"])
            + len(rep["UNCLASSIFIED"])}


def _installation_ground() -> dict:
    """`flatten.installation_write_scenario` under the current mode AND under
    the shipped path, so a survivor can be read as a verdict."""
    FL = _install_modules()["flatten"]
    return {"now": FL.installation_write_scenario(),
            "shipped": FL.installation_write_scenario("raw")}



def survivors(label: str) -> list[str]:
    """Which OTHER declared dependencies still refuse with `label` broken.

    A list this function EARNS by re-running each probe under the neuter, never
    an assert: `run_cases` reads an AssertionError as a flip, so an
    assert-shaped survivor lets a neuter that named nothing be stamped PROVEN.
    """
    dropped = _NEUTERS.get(label, ("", "", "", ""))[0]
    probes = (("step_rank", _unnamed_refused),
              ("unit_key", _unkeyed_refused),
              ("ordinal", _out_of_order_refused),
              ("terminal_step", _unverified_refused),
              ("ground_freeze", _ground_after_piece_refused),
              ("world_scope", _shared_install_refused),
              ("table", _dependent_swap_refused),
              ("unit_block", _lamp_on_ungraded_segment_refused),
              ("resume_key", _repointed_command_is_rerun),
              ("stands_on", _deck_on_unlaid_bank_blocked),
              ("installation_site", _upstream_refused_site_blocked),
              ("unbuilt", lambda: bool(_nine_row_table_refused())),
              ("installation_write_path",
               _installation_piece_under_a_later_write_refused),
              ("collar_retry", _collar_retry_case),
              ("live_replace", _live_replace_case),
              ("writer_lock", _second_build_refused_case),
              ("staged_lock", _staged_ops_interleave_safely))
    return [n for n, probe in probes if n != dropped and probe()]


def selftest() -> int:
    """The cases, through `accept/registry.py`'s own runner."""
    from accept import registry as R                        # noqa: PLC0415

    def finding(ok: bool, msg: str, **n) -> R.Finding:
        return R.Finding(check="buildstage", ok=ok, message=msg,
                         numbers=n or {"n": 1})

    cases = [
        R.Case("a_terrain_write_after_a_body_is_refused", True,
               lambda: finding(not _out_of_order_refused(),
                               "a terrain_write appended after the body "
                               "standing on that ground is OUT_OF_ORDER",
                               regrade_cut_writes=16),
               prior_state="MEASURED on Ulfsland: regrade_cut is 16 "
                           "terrain_write records at seq 5393-5415, after the "
                           "deck spawn_plans at 5383-5388; its own meta names "
                           "17 seqs its flatten guard collided with and 362 "
                           "live flatten=FORBIDDEN records",
               neuter=_neuter("ordinal off"), neuters="ordinal off"),
        R.Case("a_unit_built_in_the_declared_order_is_accepted", False,
               lambda: finding(_declared_order_accepted(),
                               "generate -> clear -> flatten -> place -> stock "
                               "-> recheck has no ordering defect", steps=6),
               neuter=_neuter("ordinal reversed"), neuters="ordinal reversed"),
        R.Case("a_step_that_interleaves_its_own_shapes_is_accepted", False,
               lambda: finding(_interleaved_step_accepted(),
                               "a prop spawned after its own portal, inside "
                               "step 6, is not OUT_OF_ORDER", n=1),
               prior_state="MEASURED on Fresh42's first full drive: 38 "
                           "OUT_OF_ORDER rows, all fixtures-after-fixtures in "
                           "one unit, from reading every step's record tuple "
                           "as a sequence",
               neuter=_neuter("record sequence every step"),
               neuters="record sequence every step"),
        R.Case("a_declared_sequence_broken_is_refused", True,
               lambda: finding(not _declared_sequence_refused(),
                               "a site cleared before its zone was generated "
                               "is OUT_OF_ORDER inside site_prepare", n=1),
               neuter=_neuter("record sequence no step"),
               neuters="record sequence no step"),
        R.Case("a_corridor_with_pieces_and_no_recheck_is_refused", True,
               lambda: finding(not _unverified_refused(),
                               "a unit that placed pieces and never re-read "
                               "its corridor is UNVERIFIED", blocking_bodies=14),
               prior_state="MEASURED on Ulfsland: 14 of 47 walker-blocking "
                           "bodies are our own furniture placed AFTER the road "
                           "(road_lighting seq 2769-2784, decks 2803-2867, a "
                           "settlement wall at seq 388 against T9's grade at "
                           "seq 234) and nothing in the tree guarded it",
               neuter=_neuter("terminal step off"), neuters="terminal step off"),
        R.Case("a_unit_whose_corridor_was_re_read_is_accepted", False,
               lambda: finding(_verified_accepted(),
                               "a unit carrying a corridor_recheck record is "
                               "not UNVERIFIED", n=1),
               neuter=_neuter("terminal step always"),
               neuters="terminal step always"),
        R.Case("a_settlement_is_not_owed_a_corridor_recheck", False,
               lambda: finding(_settlement_not_unverified(),
                               "a placement unit with pieces is reported "
                               "NO_RECHECK_STEP, not UNVERIFIED, because no "
                               "step re-reads a placement's ground", n=1),
               prior_state="MEASURED on Fresh42's first drive: 40 of 44 "
                           "UNVERIFIED were settlements and installations",
               neuter=_neuter("recheck domain all"),
               neuters="recheck domain all"),
        R.Case("the_live_pair_is_built_only_over_an_archived_ledger", False,
               lambda: finding(_live_replace_case(),
                               "the live default pair is refused without "
                               "--replace-live and without an archived "
                               "ledger beside it", cases=5),
               prior_state="the shipped guard refused the live pair outright, "
                           "so the operator's final regeneration of it could "
                           "not run at all",
               neuter=_neuter("live replace unguarded"),
               neuters="live replace unguarded"),
        R.Case("a_build_started_beside_a_running_one_is_refused", True,
               lambda: finding(not _second_build_refused_case(),
                               "a build started while another run holds the "
                               "world is refused rc 75 before step 1, runs "
                               "once the holder is gone, and the live pair's "
                               "own refusal leaves no lock file behind",
                               holders=1),
               prior_state="MEASURED 2026-10-05 on the Ulfsland regeneration: "
                           "two `buildstage.py run` ran 12.5 h against one "
                           "server and one ledger; the server log shows run "
                           "A's staged objects_remove emptied by run B's "
                           "`stop` a second later (6 road_clear units "
                           "refused want 0 got 1), and each run met the "
                           "other's bodies as standing pieces",
               neuter=_neuter("writer lock off"),
               neuters="writer lock off"),
        R.Case("a_staged_operation_survives_another_processes_stop", False,
               lambda: finding(_staged_ops_interleave_safely(),
                               "two processes staging on one Upgrade World "
                               "queue each run their own operation and each "
                               "postcondition holds; a wedged holder is "
                               "refused after its wait with nothing sent",
                               processes=2),
               prior_state="MEASURED 2026-10-05 in the Ulfsland staging "
                           "server's log: run A `stop`, `objects_remove "
                           "id=Beech_small1` (\"Removing 1 object\") at "
                           "13:02:41; run B `stop` + its own removal at "
                           "13:02:42; one `start` at 13:02:43 ran B's alone "
                           "and A's count read back `Beech_small1: 1`",
               neuter=_neuter("staged lock off"),
               neuters="staged lock off"),
        R.Case("a_declared_site_is_a_unit_of_the_bodies_step", False,
               lambda: finding(_declared_site_unit_case(),
                               "Ulfsgard and Sudrvik are units of step 5, "
                               "built by harbour_city.py ubuild and "
                               "harbour_b.py build as the run's actor; a "
                               "seed that declares none gains no unit",
                               declared=2),
               prior_state="MEASURED 2026-10-07: no row of BUILD_STEPS named "
                           "a declared site -- step 5's units were the "
                           "solved installations, towns and outliers -- so a "
                           "from-scratch regeneration built neither harbour",
               neuter=_neuter("declared units off"),
               neuters="declared units off"),
        R.Case("a_road_ground_refusal_is_retried_without_the_pad_collar", False,
               lambda: finding(_collar_retry_case(),
                               "a road_ground unit the collar leaves refused "
                               "is asked once more without it and laid; a "
                               "clean unit is asked once", units=2),
               prior_state="MEASURED on Fresh42 drive 8: T6-stathub-northcape "
                           "refused at step 4 with the collar (2 steps, "
                           "1.5626 m beside a second pad) and 0 without it",
               neuter=_neuter("collar retry off"),
               neuters="collar retry off"),
        R.Case("a_step_stamped_on_a_record_outranks_its_role", False,
               lambda: finding(_stamped_guidepost_not_unverified(),
                               "a settlement guidepost stamped by step 5 does "
                               "not make its settlement a road owed a recheck; "
                               "the role's other step is REPORTED", n=1),
               prior_state="MEASURED on Fresh42's first drive: 60 settlement "
                           "guideposts (role waypoint_sign) read as step 7 "
                           "roadside, putting 15 settlements in UNVERIFIED",
               neuter=_neuter("attribution by role"),
               neuters="attribution by role"),
        R.Case("every_role_the_build_names_is_one_the_ledger_accepts", False,
               lambda: finding(_role_registry_holds(),
                               "every role named by the build table is in "
                               "ledger/schema.KNOWN_ROLES, and a registry "
                               "missing corridor_recheck is REPORTED", n=1),
               prior_state="MEASURED on Fresh42's second drive: step 8's "
                           "corridor_recheck observe was refused by the "
                           "ledger on 5 of 5 laid corridors (rc 1 each)",
               neuter=_neuter("role registry off"),
               neuters="role registry off"),
        R.Case("a_mutating_record_that_names_no_ground_is_refused", True,
               lambda: finding(not _unkeyed_refused(),
                               "a terrain_write carrying neither site_id nor "
                               "meta.segment is NO_UNIT, never clean",
                               unkeyed_regrade_writes=16),
               prior_state="MEASURED on Ulfsland: all 16 regrade_cut "
                           "terrain_writes, all 292 road_segment "
                           "zones_generate and all 2,041 road_surface_clearing "
                           "objects_clear carry no site_id",
               neuter=_neuter("unit key off"), neuters="unit key off"),
        R.Case("two_units_built_back_to_back_are_accepted", False,
               lambda: finding(_two_units_accepted(),
                               "two units each in the declared order do not "
                               "read as one interleaved unit", units=2),
               neuter=_neuter("unit key global"), neuters="unit key global"),
        R.Case("a_record_this_table_cannot_name_is_refused", True,
               lambda: finding(not _unnamed_refused(),
                               "a mutating record with no declared step is "
                               "UNCLASSIFIED, never silently dropped", n=1),
               prior_state="a fresh seed invents roles this table has never "
                           "seen; silence there is the reading that let stage "
                           "12 have no entry point for as long as it did",
               neuter=_neuter("step rank off"), neuters="step rank off"),
        R.Case("every_step_of_a_declared_build_is_named", False,
               lambda: finding(_named_accepted(),
                               "each record of a declared build maps to "
                               "exactly one step", steps=len(BUILD_STEPS)),
               neuter=_neuter("step rank blind"), neuters="step rank blind"),
        R.Case("a_terrain_write_under_a_standing_piece_is_refused", True,
               lambda: finding(not _ground_after_piece_refused(),
                               "a write that moves the ground a piece is "
                               "STANDING IN buries or floats it, whoever "
                               "owns both", n=1),
               prior_state="MEASURED on Ulfsland: regrade_cut is 16 "
                           "terrain_write records at seq 5393-5415, after the "
                           "deck spawn_plans at 5383-5388, and its own meta "
                           "names 17 seqs its flatten guard collided with",
               neuter=_neuter("ground freeze off"), neuters="ground freeze off"),
        R.Case("a_build_that_finished_its_ground_first_is_accepted", False,
               lambda: finding(_ground_before_piece_accepted(),
                               "a unit whose terrain precedes its pieces is "
                               "not TERRAIN_AFTER_PIECE", n=1),
               neuter=_neuter("ground freeze always"),
               neuters="ground freeze always"),
        R.Case("a_settlement_that_pads_between_bodies_is_refused", True,
               lambda: (lambda r: finding(
                   not (r["flagged"] == 1 and "already held the piece" in r["why"]),
                   "one pass per unit puts every pad write after the previous "
                   "unit's body, and it names the zone it collided in: "
                   f"{r['why']}",
                   fresh42_writes_after_first_piece=42,
                   fresh42_writes_into_an_occupied_zone=18,
                   flagged=r["flagged"]))(_interleaved_settlement_refused()),
               prior_state="MEASURED 2026-09-24 on Fresh42, the first "
                           "correctly-ordered step 5 any fresh seed has had: "
                           "42 site_pad writes after first_piece_seq 1479 and "
                           "18 of them into a zone that already held a body "
                           "(seq 1492 stenvik-house-1 into zone (8, 14), held "
                           "since seq 1486). The guard the declaration CITED "
                           "recorded reached_this_write 0 on all 42, so the "
                           "world-wide scope was a SUPERSET -- and 18 is not "
                           "0, so the rule underneath it was real",
               neuter=_neuter("ground freeze scope none"),
               neuters="ground freeze scope none"),
        R.Case("the_same_settlement_with_its_ground_pass_first_survives", False,
               lambda: finding(_two_pass_settlement_accepted(),
                               "the SAME records in ground-pass-then-body-pass "
                               "order carry no write under a standing piece: "
                               "settlements/build.py now runs "
                               "apply_unit_ground over every unit before "
                               "apply_unit_body over any", units=2),
               neuter=_neuter("ground freeze always"),
               neuters="ground freeze always"),
        R.Case("a_write_in_ground_nothing_occupies_is_reported_not_refused",
               False,
               lambda: (lambda r: finding(
                   r["flagged"] == 0 and r["reported"] == 1
                   and r["flagged_under_shipped_world_scope"] == 1,
                   "a pad written 6.3 km from the nearest piece can bury "
                   "nothing: GROUND_ELSEWHERE, counted and printed, never "
                   "dropped -- and the SHIPPED world-wide scope calls the "
                   "same record a finding, which is what made Fresh42 read "
                   "42 instead of 18",
                   reported=r["reported"], flagged=r["flagged"],
                   under_shipped_scope=r["flagged_under_shipped_world_scope"],
                   fresh42_elsewhere=24))(_empty_ground_is_not_a_finding()),
               neuter=_neuter("ground freeze scope world"),
               neuters="ground freeze scope world"),
        R.Case("ground_that_cannot_be_read_is_refused_not_cleared", True,
               lambda: finding(not _unreadable_ground_fails_closed(),
                               "a spawn_plan stating no position and a "
                               "terrain_write naming no zone BOTH fail "
                               "closed: UNREAD IS NOT CLEAR", n=2),
               prior_state="MEASURED 2026-09-24: 11 of Fresh42's 23 piece "
                           "records and 437 of Ulfsland's 764 state their "
                           "position as params.anchor, not params.pos. A "
                           "reader that knows only `pos` answered 'no piece "
                           "anywhere' and turned an 18-row finding into 0",
               neuter=_neuter("ground freeze off"), neuters="ground freeze off"),
        R.Case("the_network_survey_is_not_ranked_as_a_segments_own_zones",
               True,
               lambda: (lambda r, d: finding(
                   not (r["network_wide"] == 1 and r["no_unit"] == 0),
                   "stage 10's whole-network survey names no segment and is "
                   "NETWORK_WIDE by declaration; ranked as step 4's "
                   "per-segment generate it becomes NO_UNIT, which is how "
                   "one key owned by two emitters reads from the replay side",
                   network_wide=r["network_wide"], no_unit=r["no_unit"],
                   folding_it_back_is_double_owned=d["double_owned"],
                   this_table_double_owned=d["clean"],
                   fresh42_out_of_order_before=2,
                   fresh42_out_of_order_after=0))(
                       _census_keeps_its_home(),
                       _shipped_rank_returns_the_known_bad()),
               prior_state="MEASURED on Fresh42's full-ledger replay: 2 "
                           "OUT_OF_ORDER rows, seq 1454 and 1458, both "
                           "zones_generate role=road_segment emitted by "
                           "ribbon.py INSIDE step 4 and ranked as step 0 -- "
                           "a correct build reported as incoherent because "
                           "ONE KEY WAS OWNED BY TWO EMITTERS, and the audit "
                           "could not see it because only one row spelled the "
                           "key. Step 4 now spells it, so folding the census "
                           "back into kind `road` REFUSES THE TABLE AT IMPORT",
               neuter=_neuter("record kind merged"), neuters="record kind merged"),
        R.Case("a_segment_that_generates_then_writes_its_zones_is_in_order",
               False,
               lambda: finding(_ribbons_own_generate_is_step_four(),
                               "the ribbon's own zones_generate is step 4's "
                               "first record, emitted immediately before the "
                               "write it enables -- in order, not out of it",
                               fresh42_out_of_order_before=2,
                               fresh42_out_of_order_after=0),
               neuter=_neuter("record kind site"), neuters="record kind site"),
        R.Case("a_write_path_that_ignores_its_world_is_refused", True,
               lambda: finding(not _shared_install_refused(),
                               "a data-entry path resolved from a module "
                               "constant puts every world in one slot",
                               entries_written_into_the_wrong_world=13),
               prior_state="MEASURED 2026-09-23: this entry point's own first "
                           "Fresh42 dry run, `run --world Fresh42 --dry "
                           "--through 2`, exited 0 in 101 s and wrote 13 "
                           "Fresh42 terrain-delta entry files into "
                           "/media/big4/.../Ulfsland/config_merged/bepinex/"
                           "data, the watched directory of a finished world -- "
                           "on the PLAN op, which writes nothing by contract. "
                           "terraform/flatten.py:66 was a literal naming one "
                           "install and write_entries ran before `if op == "
                           "plan: return 0`. None of the 13 names collides "
                           "with any of the 438 entry files Ulfsland's build "
                           "actually used, so nothing was destroyed -- which "
                           "is luck, not a guard",
               neuter=_neuter("world scope off"), neuters="world scope off"),
        R.Case("every_terrain_step_writes_into_its_own_worlds_install", False,
               lambda: finding(_scoped_install_accepted(),
                               "terraform/flatten.py::data_dir keys the "
                               "watched data directory on the world being "
                               "flattened", worlds=2),
               neuter=_neuter("world scope always"),
               neuters="world scope always"),
        R.Case("a_ledger_opened_on_a_hardcoded_world_is_refused", True,
               lambda: (lambda r: finding(
                   not (r["defects"] == 14 and r["obfuscated_caught"] == 2),
                   "seven `Ledger.open` calls whose world cannot vary with "
                   "the run: each is SHARED (two worlds, one chain) and "
                   "UNNAMED, and the last is `\"Ulf\" + \"sland\"`, which a "
                   "substring search accepts",
                   callsites=r["callsites"], defects=r["defects"],
                   obfuscated_caught=r["obfuscated_caught"]))(
                       _hardcoded_ledger_world_refused()),
               prior_state="roads/regrade.py:548 and :637 read "
                           "`Ledger.open(\"Ulfsland\", actor=args.actor)` "
                           "FROM A LITERAL, and `Ledger.open` CREATES the run "
                           "directory and appends a world_manifest when the "
                           "chain is empty -- so running regrade.py on ANY "
                           "world appended to the one ledger this project "
                           "holds untouchable (26,619,627 bytes, md5 "
                           "d4a75dc4acd6f39d1173c82394da4270). Six more calls "
                           "in cone.py, north.py x2 and boardwalk.py did the "
                           "same; boardwalk's took its before/after sha on "
                           "Ulfsland's chain WHILE LiveBuilder appended to "
                           "the resolved world's",
               neuter=_neuter("world scope off"), neuters="world scope off"),
        R.Case("every_ledger_this_tree_opens_keys_on_its_own_world", False,
               lambda: (lambda r: finding(
                   r["defects"] == 0 and r["callsites"] >= 21,
                   "every `Ledger.open` in the tree resolves its world from "
                   "the run -- `worldseed.world()`, a module WORLD read from "
                   "it, `--world`, or an argument the caller states",
                   callsites=r["callsites"], defects=r["defects"]))(
                       _every_ledger_open_keys_on_its_world()),
               neuter=_neuter("world scope always"),
               neuters="world scope always"),
        R.Case("a_session_driving_another_worlds_server_is_refused", True,
               lambda: (lambda r: finding(
                   not r["shipped_refuses"],
                   "the world a run RECORDS INTO and the world its socket "
                   "belongs to are one quantity; the tree kept them in two "
                   "independent env keys and NOTHING COMPARED THEM",
                   ledger_world="Fresh42", transport_world="Ulfsland"))(
                       _transport_agreement()),
               prior_state="MEASURED 2026-09-24: pipeline.Scope.env() "
                           "publishes VH_SEED and VH_WORLD and nothing else, "
                           "terraform/rcon.py:106 keyed the container on "
                           "$VALHEIM_WORLD else the literal 'Ulfsland', and "
                           "ledger/replay.py:447 builds Rcon(timeout=30.0) "
                           "with no config -- so `buildstage.py run --world "
                           "Fresh42` with no transport override would have "
                           "RECORDED INTO FRESH42'S LEDGER AND SENT EVERY "
                           "COMMAND TO valheim-server-Ulfsland. LATENT, not "
                           "live: Ulfsland's save is byte-identical to the "
                           "2026-09-23 freeze in 38 of 40 files, the two "
                           "exceptions being the documented drift chunks "
                           "22_1c (+55 ZDOs) and 22_1e (+57) written 75 "
                           "minutes AFTER the build's last record, no new "
                           "chunk file appeared, and Fresh42's own save "
                           "closed at 17:17:10Z one second after its own "
                           "closing `save` record",
               neuter=_neuter("transport world blind"),
               neuters="transport world blind"),
        R.Case("a_session_driving_its_own_worlds_server_opens", False,
               lambda: (lambda r: finding(
                   r["resolved_opens"]
                   and r["undeclared_is_recorded_not_refused"],
                   "a run whose socket resolves the SAME world it records "
                   "into opens, and a socket nobody attributed "
                   "($VALHEIM_RCON_HOST names an address, not a world) is "
                   "RECORDED on every record rather than refused -- a fresh "
                   "seed's bare server is addressed exactly that way and "
                   "refusing it would forbid every fresh build",
                   undeclared_refused=0))(_transport_agreement()),
               neuter=_neuter("transport world literal"),
               neuters="transport world literal"),
        R.Case("exchanging_two_dependent_steps_is_refused_at_import", True,
               lambda: finding(not _dependent_swap_refused(),
                               "road_ground before road_clear is INVERTED: "
                               "ribbon.py REFUSES the batter without the "
                               "placed-objects file clear.py produces", n=1),
               prior_state="the ordering was in the head of whoever was "
                           "driving; STAGE 12 HAD NO ENTRY POINT AT ALL",
               neuter=_neuter("table ordinal off"),
               neuters="table ordinal off"),
        R.Case("exchanging_two_independent_steps_survives", False,
               lambda: finding(_independent_swap_accepted(),
                               "site_ground and road_clear consume nothing of "
                               "each other's, so exchanging them is NOT a "
                               "defect -- a rule that refuses every "
                               "permutation checks nothing", n=1),
               neuter=_neuter("table ordinal reversed"),
               neuters="table ordinal reversed"),
        R.Case("a_lamp_on_a_segment_whose_grade_was_refused_is_refused", True,
               lambda: finding(not _lamp_on_ungraded_segment_refused(),
                               "roadside consumes road_ground and draws the "
                               "same units, so a refused segment is BLOCKED "
                               "by name", refused_segments=1),
               prior_state="MEASURED 2026-09-24 on Fresh42: 12 of 14 segments "
                           "refuse step 4 and T1-portalhub-temple is FIRST in "
                           "the unit list. With the block off, step 7 would "
                           "lamp a carriageway that was never graded -- the "
                           "1E_OUT_OF_ORDER class arriving by the front door",
               neuter=_neuter("unit block off"), neuters="unit block off"),
        R.Case("a_segment_whose_own_ground_was_written_is_still_built", False,
               lambda: finding(_sibling_segment_still_built()
                               and _cross_domain_debt_named(),
                               "a refusal blocks the SAME unit, not the "
                               "world: S7-stathub-wtnorth is rc 0 and is "
                               "built, and a refused SEGMENT is reported as "
                               "a DEBT to the BODY domain rather than "
                               "guessed at", buildable_segments=2),
               prior_state="MEASURED 2026-09-24: under the world-wide label "
                           "key `run` returned at T1-portalhub-temple and the "
                           "2 segments that pass every gate were never "
                           "reached -- a build that laid NO road at all",
               neuter=_neuter("unit block global"),
               neuters="unit block global"),
        R.Case("a_site_refused_upstream_is_blocked_by_name_at_bodies", True,
               lambda: (lambda r: finding(
                   not _upstream_refusal_blocked_at_bodies(r),
                   f"{_SITE_REFUSED_UPSTREAM}, refused at step 1, is BLOCKED "
                   f"by name at step 5 and never re-tried: blocked_by "
                   f"{r['blocked_by'].get(_SITE_REFUSED_UPSTREAM)}, step-5 "
                   f"units run {sorted(r['rc'])}",
                   blocked=len(r["blocked_by"]), run=len(r["rc"])))(
                       _installation_bodies_run()),
               prior_state="MEASURED on Fresh42 drive 3: iron-era-workshop "
                           "refused step 1 (clamp, 442 samples beyond +/-8 "
                           "m), was BLOCKED at step 2, and was RE-TRIED inside "
                           "the one step-5 unit `*placements*` (plan refused "
                           "rc 4, unsheltered_deck), turning that whole unit "
                           "rc 1",
               neuter=_neuter("installation site world"),
               neuters="installation site world"),
        R.Case("its_sibling_sites_stand_and_an_own_refusal_still_refuses",
               False,
               lambda: (lambda r: finding(
                   _siblings_built_own_refusal_kept(r),
                   f"step 5 per site: {r['rc']}; a site refusing for its "
                   f"OWN reason is refused by name and the build exits "
                   f"{r['exit']}: {r['partial']!r}",
                   built=sum(1 for v in r["rc"].values() if v == 0),
                   refused=sum(1 for v in r["rc"].values() if v)))(
                       _installation_bodies_run()),
               prior_state="MEASURED on Fresh42 drive 3: 11 of 12 "
                           "installation bodies stood (spawn_plan seq "
                           "1990-2000) and were journalled as ONE refused "
                           "unit, `PARTIAL BUILD ... bodies: ['*placements*']`",
               neuter=_neuter("installation site world"),
               neuters="installation site world"),
        R.Case("a_command_whose_inputs_moved_is_re_run", True,
               lambda: finding(not _repointed_command_is_rerun(),
                               "the resume key is the argv the command acts "
                               "on: re-point --segment, --segments, --world "
                               "or --out and the recorded success is not this "
                               "work", repointed_flags=len(_CMD_REPOINTED)),
               prior_state="a key that ignored the argv would read any "
                           "recorded rc 0 for this step-unit as this "
                           "command's, which is how a build gets resumed past "
                           "a re-pointed input",
               neuter=_neuter("resume key off"), neuters="resume key off"),
        R.Case("the_same_command_run_by_another_agent_is_not_re_run", False,
               lambda: finding(_same_work_by_another_agent_resumes(),
                               "--actor changes THE RECORD AND NOT THE "
                               "GROUND, so it is stamped on the receipt and "
                               "kept out of the key", steps_already_done=3),
               prior_state="MEASURED 2026-09-24: Fresh42's journal holds "
                           "steps 1-3 at rc 0 under actor BuildFresh (13 + "
                           "13 site units, 85 road_clear commands). A second "
                           "agent resuming as itself matched NONE of them and "
                           "began re-running step 3 LIVE from segment one -- "
                           "this file measures that step at 156.3 minutes of "
                           "RCON, re-doing work already done correctly and "
                           "appending a SECOND set of records for it",
               neuter=_neuter("resume key superset"),
               neuters="resume key superset"),
        R.Case("an_installation_piece_under_a_later_write_is_refused", True,
               lambda: finding(not _installation_piece_under_a_later_write_refused(),
                               "an installation body and fixture built "
                               "through steps 5 and 6 stand in the zone a "
                               "later settlement pad moves: "
                               "TERRAIN_AFTER_PIECE, because the replay can "
                               "SEE them", pieces=2),
               prior_state="MEASURED on the previous Fresh42 drive "
                           "(archive-contaminated-20260924T2145): steps 1+2 "
                           "ran 26 unit-runs and step 6 13, all rc 0, and of "
                           "1,822 ledger records ZERO named any of the 13 "
                           "installations -- every installation piece was "
                           "invisible to this replay, the ground freeze and "
                           "the pad claims",
               neuter=_neuter("installation write path raw"),
               neuters="installation write path raw"),
        R.Case("an_installation_is_read_by_the_replay_in_the_declared_order",
               False,
               lambda: (lambda r: finding(
                   r["records"] == 5 and r["steps"] == [1, 2, 5, 6]
                   and r["findings"] == 0,
                   f"generate, clear, pad, body, fixture: {r['records']} "
                   f"records keyed on the installation, ranked as steps "
                   f"{r['steps']}, {r['findings']} ordering findings",
                   **r))(_installation_read_in_declared_order()),
               neuter=_neuter("installation write path raw"),
               neuters="installation write path raw"),
        R.Case("an_installation_pad_survives_a_later_write_in_its_zone", False,
               lambda: (lambda g: finding(
                   g["now"]["shared"]["verdict"] == "preserved",
                   f"step 5's union carries the installation pad forward: "
                   f"{g['now']['shared']['held']} of "
                   f"{g['now']['shared']['wrote']} samples on the compiler "
                   f"after the later write "
                   f"({g['now']['shared']['verdict']}); on the shipped path "
                   f"{g['shipped']['shared']['held']} "
                   f"({g['shipped']['shared']['verdict']})",
                   held=g["now"]["shared"]["held"],
                   wrote=g["now"]["shared"]["wrote"],
                   held_on_the_shipped_path=g["shipped"]["shared"]["held"]))(
                       _installation_ground()),
               prior_state="MEASURED on the CURRENT route "
                           "(route-provisional/segments.Fresh42.yaml) at the "
                           "ribbon's write set: 10 of 36 installation-pad "
                           "zones hold a later road write, 2,092 pad samples "
                           "(complete-station-hub 525, sandbox-portal-hub "
                           "1,225, meadows-starter-hall 342) erased by "
                           "construction on the shipped path",
               neuter=_neuter("installation write path raw"),
               neuters="installation write path raw"),
        R.Case("an_installation_no_later_write_touches_lands_identically",
               False,
               lambda: (lambda g: finding(
                   g["now"]["alone"]["identical"]
                   and g["shipped"]["alone"]["identical"],
                   f"SURVIVOR: an installation in a zone no later write names "
                   f"lands the bytes the shipped apply spawned -- through the "
                   f"ledger {g['now']['alone']['verdict']}/"
                   f"{'identical' if g['now']['alone']['identical'] else 'differs'}, "
                   f"on the shipped path {g['shipped']['alone']['verdict']}/"
                   f"{'identical' if g['shipped']['alone']['identical'] else 'differs'}",
                   samples=g["now"]["alone"]["wrote"],
                   held_now=g["now"]["alone"]["held"],
                   held_on_the_shipped_path=g["shipped"]["alone"]["held"]))(
                       _installation_ground()),
               neuter=_neuter("installation write path ghost"),
               neuters="installation write path ghost"),
        # ---- step 6.5 `decks`: the step that builds a crossing ------------
        R.Case("every_label_the_build_is_handed_has_a_builder", False,
               lambda: (lambda d: finding(
                   not d,
                   "every label pipeline row 12 hands the build is consumed "
                   "by a step with a command -- the crossing contract by "
                   "step 6.5 `decks`" + (f"; {d}" if d else ""),
                   unbuilt=len(d), fresh42_crossings_declared=9))(
                       unbuilt_defects()),
               prior_state="MEASURED 2026-09-25: pipeline row 12 REQUIRES "
                           "`crossings` and none of the 8 build steps "
                           "consumed it, so every declared crossing on a "
                           "fresh world stayed a hole -- Fresh42 declares 9 "
                           "and S4-meadhall-southeast-x01 (banks (396,-424) "
                           "-> (396,-450)) carries 0 of 27 m. Every one of "
                           "Ulfsland's 22 bridges was laid by an agent "
                           "running a tool by hand",
               neuter=_neuter("deck step dropped"),
               neuters="deck step dropped"),
        R.Case("a_build_table_with_no_deck_step_is_refused", True,
               lambda: (lambda d: finding(
                   not any("a declared crossing with no builder" in x
                           for x in d),
                   "the shipped 9-row table, handed `crossings` by pipeline "
                   "row 12, is refused: " + ("; ".join(d) or "NOTHING"),
                   defects=len(d)))(_nine_row_table_refused()),
               prior_state="the table as it stood: step 0 did not even list "
                           "the crossing contract as an input, and "
                           "audit_graph refuses only a label REQUIRED and "
                           "unproduced, never one HANDED and unconsumed",
               neuter=_neuter("builder check off"),
               neuters="builder check off"),
        R.Case("a_crossing_whose_banks_are_unlaid_is_blocked", True,
               lambda: finding(not _deck_on_unlaid_bank_blocked(),
                               "T5-temple-dockshore-x01 stands on T5, whose "
                               "grade step 4 refused: BLOCKED by name before "
                               "a piece is solved",
                               refused_banks=1),
               prior_state="MEASURED on Fresh42's drive 2: 5 of 14 segments "
                           "laid and 7 of the 9 declared crossings stand on "
                           "one that was not. Under the domain rule alone a "
                           "crossing is not a segment, so each was a DEBT "
                           "and the deck step would RUN over an ungraded "
                           "bank",
               neuter=_neuter("stands on off"), neuters="stands on off"),
        R.Case("a_crossing_whose_banks_are_laid_is_still_built", False,
               lambda: finding(_deck_on_laid_bank_built(),
                               "S4-meadhall-southeast-x01 stands on S4, "
                               "which step 4 laid: not blocked by its "
                               "neighbour's refusal, and that refusal is not "
                               "also reported as a debt", buildable=1),
               neuter=_neuter("unit block global"),
               neuters="unit block global"),
        R.Case("a_crossing_declared_after_the_road_it_depends_on_is_ordered",
               False,
               lambda: (lambda p: finding(
                   not p["audit"] and p["after"] == ["road_ground", "bodies"]
                   and p["before"] == ["roadside", "recheck"],
                   f"decks (6.5) follows {p['after']} and precedes "
                   f"{p['before']}; the table audits clean",
                   audit_defects=len(p["audit"])))(_deck_step_placed()),
               neuter=_neuter("table ordinal reversed"),
               neuters="table ordinal reversed"),
        R.Case("a_deck_step_ahead_of_the_road_it_stands_on_is_refused", True,
               lambda: (lambda d: finding(
                   not (d["road_ground"] and d["bodies"]),
                   "decks before road_ground, or before the last step that "
                   "writes ground (bodies, 33 Fresh42 site_pad writes), is "
                   "INVERTED at import", ahead_of_road=len(d["road_ground"]),
                   ahead_of_bodies=len(d["bodies"])))(_deck_step_swaps_refused()),
               prior_state="MEASURED on Ulfsland: regrade_cut's 16 "
                           "terrain_writes at seq 5393-5415 came after the "
                           "T4-x02 deck at 5383-5388 and its flatten guard "
                           "collided with 17 seqs",
               neuter=_neuter("table ordinal off"),
               neuters="table ordinal off"),
        R.Case("a_deck_its_road_never_re_read_is_unverified", True,
               lambda: finding(not _deck_never_re_read_refused(),
                               "a deck whose segment has no recheck, or one "
                               "taken BEFORE the deck landed, is UNVERIFIED -- "
                               "a reading of the hole is not a reading of "
                               "the deck", arms=2),
               prior_state="MEASURED on Ulfsland: 20 boardwalk decks, 0 "
                           "corridor_recheck records, and every deck read "
                           "NO_RECHECK_STEP -- the decks at seq 2803-2867 "
                           "are among the 14 walker-blocking bodies nothing "
                           "re-read",
               neuter=_neuter("stands on off"), neuters="stands on off"),
        R.Case("a_deck_re_read_with_the_road_it_carries_is_accepted", False,
               lambda: (lambda r: finding(
                   r["deck_step"] == DECK_STEP and not any(
                       r[k] for k in ("out_of_order", "unverified",
                                      "no_recheck_step", "unclassified",
                                      "terrain_after_piece")),
                   "grade, deck, then the segment's recheck: ranked as step "
                   f"{r['deck_step']!r}, carried by the recheck of the road "
                   "it stands on, no finding", **r))(
                       _deck_re_read_with_its_road()),
               neuter=_neuter("terminal step always"),
               neuters="terminal step always"),
        R.Case("a_bridge_built_in_two_parts_is_one_deck_step", False,
               lambda: (lambda r: finding(
                   r["out_of_order"] == 0 and r["unclassified"] == 0
                   and r["steps"] == [DECK_STEP],
                   f"generate, clear, place twice under one site_id: steps "
                   f"{r['steps']}, {r['out_of_order']} OUT_OF_ORDER",
                   **{k: v for k, v in r.items() if k != "steps"}))(
                       _bridge_ranked_as_decks()),
               prior_state="MEASURED on Ulfsland's replay: 4 OUT_OF_ORDER "
                           "rows at seq 15, 18, 19 and 22 -- S1's and S2's "
                           "own generate and clear, ranked as road steps 4 "
                           "and 3 because the bridge roles were ROAD_ROLES",
               neuter=_neuter("record kind bridge as road"),
               neuters="record kind bridge as road"),
    ]
    section = R.Section(
        name="buildstage",
        measures="the ORDER of the build's own steps and the order the ledger "
                 "records prove a finished world was actually built in",
        instrument="pipeline.audit_graph over BUILD_STEPS + buildstage.replay",
        population=f"the {len(BUILD_STEPS)} rows of BUILD_STEPS and every "
                   f"(kind, op) record shape they declare",
        run=lambda ctx: [], cases=cases)

    results = R.run_cases(section)
    bad = 0
    for c, ok, why in results:
        surv: list[str] = []
        if c.neuter is not None:
            undo = c.neuter()
            try:
                surv = survivors(c.neuters)
            finally:
                undo()
        mark = "PASS" if ok else "FAIL"
        if not ok:
            bad += 1
        print(f"  {mark}  {c.name}  proof={c.proof_mode or 'unproven'}  "
              f"neuter={c.neuters or '-'}  survivors={','.join(surv) or 'NONE'}"
              + (f"  {why}" if why else ""))
        if c.neuter is not None and not surv:
            print(f"        REFUSING this proof: the neuter {c.neuters!r} left "
                  f"NO other declared dependency refusing, so it cannot be "
                  f"told apart from an emptied instrument")
            bad += 1
    print(f"\nbuildstage selftest: {len(results) - bad}/{len(results)} "
          f"({'PASS' if not bad else 'FAIL'}), "
          f"declared dependencies "
          f"{REPLAY_DEPENDS_ON + ('table',) + UNIT_BLOCK_DEPENDS_ON + RESUME_KEY_DEPENDS_ON + UNBUILT_DEPENDS_ON + BODY_UNIT_DEPENDS_ON}"
          f" + installation_write_path "
          f"{_install_modules()['flatten'].INSTALLATION_WRITE_PATH_DEPENDS_ON}")
    return 0 if not bad else 1

# ---------------------------------------------------------------------------
# cli
# ---------------------------------------------------------------------------
def _render_order(scope: Scope) -> None:
    print(f"world {scope.world}  seed {scope.seed}")
    for st in BUILD_STEPS:
        units = st.units(scope) if st.units else []
        kind = "INPUT" if st.inherited else ("CMD " if st.argv else "MAN ")
        labels = ",".join(a.label for a in st.produces(scope))
        print(f"{kind} {st.ordinal:>4g} {st.name:14s} {st.owner}")
        print(f"        requires {st.requires or '()'}")
        print(f"        produces {labels}")
        print(f"        units    {len(units)}")
        if st.stands_on:
            ground = st.stands_on(scope)
            doms = sorted({d for g in ground.values() for d in g})
            print(f"        stands on {doms}: "
                  + ", ".join(f"{u} -> {list(v for d in doms for v in g.get(d, ()))}"
                              for u, g in ground.items()))
        print(f"        records  {st.records or '()'}")


def _render_replay(rep: dict, limit: int = 12) -> None:
    print(f"units {rep['units']}  ordering records {rep['ordering_records']}  "
          f"coherent {rep['coherent']}")
    for key in ("OUT_OF_ORDER", "TERRAIN_AFTER_PIECE", "NO_UNIT",
                "UNCLASSIFIED"):
        rows = rep[key]
        print(f"  {key:22s} {len(rows)}")
        for r in rows[:limit]:
            print(f"      {r}")
        if len(rows) > limit:
            print(f"      ... {len(rows) - limit} more")
    print(f"  {'NETWORK_WIDE':22s} "
          f"{len(rep.get('NETWORK_WIDE') or [])}  (a step that declares no "
          f"units has no unit to name -- reported, not a finding)")
    print(f"  {'GROUND_ELSEWHERE':22s} "
          f"{len(rep.get('GROUND_ELSEWHERE') or [])}  (a write after the "
          f"world's first piece, in ground no piece occupies -- reported, "
          f"not a finding)")
    print(f"  {'UNVERIFIED':22s} {len(rep['UNVERIFIED'])}")
    for u in rep["UNVERIFIED"][:limit]:
        print(f"      {u}")
    if len(rep["UNVERIFIED"]) > limit:
        print(f"      ... {len(rep['UNVERIFIED']) - limit} more")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("op", choices=["order", "graph", "replay", "run",
                                   "selftest"])
    worldseed.add_arguments(ap)
    ap.add_argument("--work", default=None)
    ap.add_argument("--world-root", default=pipeline.WORLD_ROOT_DEFAULT)
    ap.add_argument("--vh-src", default=pipeline.VH_SRC_DEFAULT)
    ap.add_argument("--ledger", default=None,
                    help="the ledger to replay; default this world's own")
    ap.add_argument("--actor", default="",
                    help="the agent id that is ACTUALLY running this build. "
                         "Required by `run`, stated rather than defaulted: a "
                         "module constant once recorded seq 933 under one "
                         "agent's name for another's work")
    ap.add_argument("--through", type=int, default=99)
    ap.add_argument("--resume", action="store_true",
                    help="skip a step-unit that already completed rc 0 in "
                         "this world's journal WITH THE SAME ARGV. Never "
                         "skips a refusal or a step that did not run; every "
                         "skip is printed with the rc and wall it stands on. "
                         "OFF by default -- a from-scratch regen runs the "
                         "whole table.")
    ap.add_argument("--replace-live", action="store_true",
                    help="build the live default pair on purpose, over an "
                         "archived ledger (LIVE_REPLACE_DEPENDS_ON)")
    ap.add_argument("--dry", action="store_true",
                    help="every step in its own offline/plan form")
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)

    if args.op == "selftest":
        return selftest()
    if args.op == "graph":
        bad = _DEFECTS
        for line in bad:
            print(line)
        print(f"{len(BUILD_STEPS)} build steps, {len(bad)} ordering defects")
        return 1 if bad else 0

    seed, world = worldseed.apply(args)
    work = Path(args.work) if args.work else \
        HERE / "ledger" / "runs" / world / "pipeline"
    scope = Scope(seed, world, work, Path(args.world_root), Path(args.vh_src))

    if args.op == "order":
        _render_order(scope)
        return 0
    if args.op == "replay":
        path = Path(args.ledger) if args.ledger else \
            HERE / "ledger" / "runs" / world / "ledger.jsonl"
        if not path.exists():
            sys.stderr.write(f"no ledger at {path}\n")
            return 2
        rep = replay(read_ledger(path))
        rep["ledger"] = str(path)
        _render_replay(rep)
        if args.json:
            Path(args.json).write_text(json.dumps(rep, indent=1))
        return 0 if rep["coherent"] else 1
    if not args.actor:
        ap.error("run needs --actor: the agent id that is ACTUALLY running "
                 "this build. pipeline.py passes $VH_ACTOR through, so set "
                 "that when driving from there. There is no default, because "
                 "every record this build appends carries the name and "
                 "provenance that is wrong reads as evidence")
    return run(Runtime(scope, args.actor, args.dry),
               through=args.through, resume=args.resume,
               replace_live=args.replace_live)


if __name__ == "__main__":
    raise SystemExit(main())
