#!/usr/bin/env python3
"""EVERY REFUSAL THIS GATE CAN EMIT, AND THE CLASS IT WOULD COME BACK WITH.

`NO_CLASS 0` WAS NEVER COVERAGE.  IT WAS LUCK.
-----------------------------------------------------------------------
MEASURED 2026-09-23.  Ulfsland's board reported 105 refusals with
`NO_CLASS 0`, and every agent who read it - including the one who wrote
the last section to start classifying - read that as "the board is fully
classified".  It was not.  FOUR sections classify: roads, harbours,
settlements, structures.  The other SEVEN - emission, provenance,
portals, spawn, lighting, annulus, townsites - publish no
`termination_class` at all, and on Ulfsland they happened to have ZERO
REFUSALS, so the hole in the sum was indistinguishable from a zero in it.

The first world whose refusals landed in those seven said so at once:
the fresh seed Fresh42, built from nothing, came back with THIRTEEN
refusals and `NO_CLASS 13`.

THAT IS THIS PROJECT'S DEFINING DEFECT, IN THE INSTRUMENT THAT MEASURES
IT: A KEY THAT NAMES A SUPERSET OR A SUBSET OF WHAT THE QUANTITY
ACTUALLY DEPENDS ON.  "The board is fully classified" was keyed on THE
SECTIONS THAT HAPPENED TO REFUSE.  It depends on THE SECTIONS THAT CAN
REFUSE, and those two sets are equal only by accident of which world you
are looking at.

SO THE POPULATION COMES FROM THE SOURCE, NOT FROM A WORLD
-----------------------------------------------------------------------
`emittable()` reads every `Finding(ok=False)` construction site
REACHABLE FROM A SECTION'S `run()`, across module boundaries, out of the
PARSE TREE of this repository.  A world can only ever show which of
those fired tonight; the source shows which CAN fire, on any world, ever.
That is the denominator the coverage claim depends on, and it is
unpatchable by any neuter that switches a classifier off - a neuter can
empty the classifier, it cannot edit the files on disk.

AND THE CLASS COMES FROM `settlements.termination_class`, NOT FROM HERE
-----------------------------------------------------------------------
This module produces the NUMBERS a refusal's remedy family implies, one
honest set per family, and hands them to THE ONE CLASSIFIER.  A second
implementation of a shared helper has cost this project four times in
one session, so there is no cascade in this file and no fifth vocabulary:
`FAMILIES` is a table of numbers, `classify` is a loop, and every class
except `1E_OUT_OF_ORDER` is computed by the shared function.

`1E_OUT_OF_ORDER` IS THE ONE THE CALLER ASSERTS, and that is the
vocabulary's own contract (see `settlements.TERMINATION_CLASSES`): there
is no measurement of "a prerequisite has not run" that the shared helper
can take, so the caller that OWNS the builder's preconditions states it -
exactly as `1D_OBSTRUCTED` is stated off the body census.  A row
carrying 1E MUST NAME THE PREREQUISITE, so `_numbers` and `out_of_order`
refuse to produce a 1E that names nothing.

AND NO REFUSAL LEAVES THIS GATE UNKNOWN, AS A PROPERTY
-----------------------------------------------------------------------
UNKNOWN is `NO_CLASS` spelled again.  MEASURED on drive 2's frozen Fresh42
save a521b70356e7: 23 checks named a family whose class was UNKNOWN, the
coverage audit counted them and passed, and roads put 6 UNKNOWN rows on
the board.  Three places close it, one per way a row can arrive bare:
  SOURCE    `coverage`/`verdict` REFUSE a family outside `FAMILIES` or with
            class UNKNOWN (callable families by every family they can
            return), and any reachable site that writes UNKNOWN into a row.
  IN PLACE  `settle` turns an unread instrument into 1E naming it.
  RUNTIME   `backstop` classes any row still UNKNOWN as SOURCE_CHANGE and
            hands its section a `termination` refusal naming it.

THE THREE-STATE CONTRACT IS THE CALLER'S, AND THIS IS THE CALLER
-----------------------------------------------------------------------
  owned / held   the ANSWER field NAMES the record
  asked, clear   the ANSWER field is None, the REACH field is a NUMBER
  never asked    BOTH are None

`repair_footprint_m = 0.0` for a no-repair row, NEVER None.  NONE MAY
NEVER STAND FOR AN ANSWER IN EITHER DIRECTION.  UNREAD IS NOT CLEAR.
"""
from __future__ import annotations

import ast
import collections
import math
import re
from pathlib import Path

# WHAT THE ENUMERATED POPULATION IS A FUNCTION OF, DECLARED IN SOURCE.
# Each arm is neuter-proven in `sections/provenance.py`: one arm LOSES a
# named dependency and another SURVIVES the same neuter, because a
# one-direction proof is passed by an enumerator that returned NOTHING.
EMITTABLE_DEPENDS_ON = (
    "the section module's own `run()` and every function reachable from it",
    "the GATE MODULES that run() reaches through - a refusal emitted by "
    "`instruments.read_scoped_corpus` lands on the section's board and is "
    "the section's to classify",
    "the `check=` keyword at each construction site, and the `check=` "
    "DEFAULT of a shared emitter when the caller does not override it",
)

_HERE = Path(__file__).resolve().parent

# REFUSALS THE HARNESS EMITS ON A SECTION'S BEHALF.  `__main__` turns a
# section that RAISES into `section_raised` rather than letting one
# traceback erase eight verdicts - so every section can emit it, no
# section's source contains it, and a coverage claim that read only the
# section files would miss it on all eleven at once.  `backstop` emits
# `termination` on the same terms when a row reaches the board unclassed.
GATE_EMITTED = {"section_raised": "__main__:section_raised",
                "termination": "termination.backstop"}


# ---------------------------------------------------------------------------
# THE POPULATION, READ OUT OF THE PARSE TREE
# ---------------------------------------------------------------------------
def _module_path(dotted: str) -> Path:
    if dotted.startswith("sections."):
        return _HERE / "sections" / (dotted.split(".", 1)[1] + ".py")
    return _HERE / (dotted + ".py")


class _Mod:
    """One gate module's parse tree, indexed the three ways this needs."""

    __slots__ = ("funcs", "aliases", "imports", "path")

    def __init__(self, dotted: str, path: Path):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        self.path = path
        self.funcs: dict[str, list] = collections.defaultdict(list)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.funcs[node.name].append(node)
        # `from .. import instruments as I` -> {"I": "instruments"}
        self.aliases: dict[str, str] = {}
        # `from .settlements import check_arrival` -> {"check_arrival":
        #  ("sections.settlements", "check_arrival")}
        self.imports: dict[str, tuple[str, str]] = {}
        inside_sections = dotted.startswith("sections.")
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.level:
                continue
            # level 1 from a section module is `accept.sections`; level 2 is
            # `accept`.  From `accept` itself, level 1 is `accept`.
            if inside_sections:
                pkg = "sections" if node.level == 1 else ""
            else:
                pkg = "" if node.level == 1 else None
            if pkg is None:
                continue
            base = node.module or ""
            for al in node.names:
                if base:
                    tgt = f"{pkg}.{base}".lstrip(".")
                    self.imports[al.asname or al.name] = (tgt, al.name)
                else:
                    self.aliases[al.asname or al.name] = \
                        f"{pkg}.{al.name}".lstrip(".")


_MODS: dict[str, _Mod | None] = {}
# SWITCHED OFF BY A NEUTER, NEVER BY A CALLER.  See
# `provenance._neuter_the_enumerator_to_one_file`: with cross-module
# reachability off, a section's population loses exactly the refusals its
# shared instruments emit and KEEPS the ones its own file emits, which is
# how a proof tells a lost dependency from an emptied instrument.
_ENUMERATOR_READS_ONE_FILE = False


def _mod(dotted: str) -> _Mod | None:
    if dotted not in _MODS:
        p = _module_path(dotted)
        _MODS[dotted] = _Mod(dotted, p) if p.exists() else None
    return _MODS[dotted]


def _literal(node):
    try:
        return ast.literal_eval(node)
    except Exception:                                      # noqa: BLE001
        return None


def _callees(call: ast.Call, here: str, m: _Mod) -> list[tuple[str, str]]:
    """Which gate function(s) this call reaches, resolved through IMPORTS.

    RESOLVED THROUGH THE IMPORT TABLE, NEVER BY BARE NAME, and the
    difference is measurable: `spawn.run` and `settlements.run` both call
    something named `check_population`, and a name-keyed resolver walks
    from one section into another's checks and inflates that section's
    population with eight refusals it cannot emit.  A key that names a
    SUPERSET of what the quantity depends on, in the very function
    written to catch them.
    """
    f = call.func
    if isinstance(f, ast.Name):
        if f.id in m.funcs:
            return [(here, f.id)]
        if f.id in m.imports and not _ENUMERATOR_READS_ONE_FILE:
            tm, ta = m.imports[f.id]
            if (om := _mod(tm)) and ta in om.funcs:
                return [(tm, ta)]
        return []
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
        if _ENUMERATOR_READS_ONE_FILE:
            return []
        tm = m.aliases.get(f.value.id)
        if tm and (om := _mod(tm)) and f.attr in om.funcs:
            return [(tm, f.attr)]
    return []


def _string_defaults(fn) -> dict[str, str]:
    out: dict[str, str] = {}
    args, defs = fn.args.args, fn.args.defaults
    for a, d in zip(args[len(args) - len(defs):], defs):
        if isinstance(v := _literal(d), str):
            out[a.arg] = v
    for a, d in zip(fn.args.kwonlyargs, fn.args.kw_defaults):
        if d is not None and isinstance(v := _literal(d), str):
            out[a.arg] = v
    return out


def _stamps_unknown(call: ast.Call) -> bool:
    """Does this `Finding(...)` write `termination_class="UNKNOWN"` into its
    own numbers as a LITERAL at the construction site - drive 2's spelling,
    `numbers=dict(..., termination_class="UNKNOWN", **t["nums"])`?"""
    for k in call.keywords:
        v = k.value
        if k.arg == "numbers" and isinstance(v, ast.Call) \
                and isinstance(v.func, ast.Name) and v.func.id == "dict":
            if any(kk.arg == "termination_class"
                   and _literal(kk.value) == "UNKNOWN" for kk in v.keywords):
                return True
        if k.arg == "numbers" and isinstance(v, ast.Dict):
            if any(kk is not None and _literal(kk) == "termination_class"
                   and _literal(vv) == "UNKNOWN"
                   for kk, vv in zip(v.keys, v.values)):
                return True
    return False


def _may_be_unknown(expr) -> bool:
    """Can this expression evaluate to the literal "UNKNOWN" - directly, or
    down either arm of a conditional or an `or`?  A COMPARISON against
    "UNKNOWN" is not a stamp and is not matched."""
    if isinstance(expr, ast.Constant):
        return expr.value == "UNKNOWN"
    if isinstance(expr, ast.IfExp):
        return _may_be_unknown(expr.body) or _may_be_unknown(expr.orelse)
    if isinstance(expr, ast.BoolOp):
        return any(_may_be_unknown(v) for v in expr.values)
    return False


def _assigns_unknown(node) -> bool:
    """Does this statement or call write UNKNOWN into a `termination_class`
    IN PLACE - the in-section classifier's spelling, which no `Finding(...)`
    site carries?  MEASURED on drive 2's frozen Fresh42 save: all 6 roads
    UNKNOWN rows were written by `road_termination`'s
    `n["termination_class"] = "UNKNOWN"`, which `_stamps_unknown` could not
    see because the row already existed when its class was written.

      n["termination_class"] = <may be UNKNOWN>
      n.setdefault("termination_class", <may be UNKNOWN>)
      n.update(termination_class=<may be UNKNOWN>)
      dict(..., termination_class=<may be UNKNOWN>)
    """
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) \
            else [node.target]
        return node.value is not None and _may_be_unknown(node.value) and any(
            isinstance(t, ast.Subscript)
            and _literal(t.slice) == "termination_class" for t in targets)
    if isinstance(node, ast.Call):
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr == "setdefault" \
                and len(node.args) == 2 \
                and _literal(node.args[0]) == "termination_class":
            return _may_be_unknown(node.args[1])
        if (isinstance(f, ast.Attribute) and f.attr == "update") or (
                isinstance(f, ast.Name) and f.id == "dict"):
            return any(k.arg == "termination_class"
                       and _may_be_unknown(k.value) for k in node.keywords)
    return False


def emittable(section: str, stamped_unknown: dict | None = None
              ) -> dict[str, tuple[str, ...]]:
    """Every hard-refusal `check` name this section CAN emit, and where.

    A SITE WHOSE `ok` IS AN EXPRESSION COUNTS AS EMITTABLE.  `emission`'s
    `output_is_guarded_at_emission` is `ok=not adv, advisory=adv` and can
    therefore never produce a HARD refusal - the two fields are the same
    variable - and this function cannot prove that without evaluating the
    section.  So it counts the site.  The conservative direction is to
    demand a class for a refusal that may never fire; the other direction
    silently drops one that can, which is the defect this file exists for.

    `stamped_unknown`, when given, collects `{check: [site, ...]}` for every
    reachable site that stamps its own row UNKNOWN (`_stamps_unknown`) - off
    the same walk, so the audit below reads one population, not two - and
    `{"<in place: fn>": [site, ...]}` for every reachable statement that
    writes UNKNOWN into an existing row's class (`_assigns_unknown`).
    """
    here = f"sections.{section}"
    m = _mod(here)
    if m is None:
        raise KeyError(f"no such section module: {section}")
    # 1. Reachability from run(), carrying the `check=` a caller passes to a
    #    shared emitter so a parameterised construction site can be named.
    seen: set[tuple[str, str]] = set()
    passed: dict[tuple[str, str], set[str]] = collections.defaultdict(set)
    work = [(here, "run")]
    while work:
        mod_name, fn_name = work.pop()
        if (mod_name, fn_name) in seen:
            continue
        om = _mod(mod_name)
        if om is None or fn_name not in om.funcs:
            continue
        seen.add((mod_name, fn_name))
        for fn in om.funcs[fn_name]:
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call):
                    continue
                kw = {k.arg: k.value for k in node.keywords if k.arg}
                for tgt in _callees(node, mod_name, om):
                    work.append(tgt)
                    if isinstance(v := _literal(kw.get("check")), str):
                        passed[tgt].add(v)
    # 2. Every Finding site in the reachable set that is not a declared pass
    #    and not a declared advisory.
    out: dict[str, list[str]] = collections.defaultdict(list)
    for mod_name, fn_name in sorted(seen):
        om = _mod(mod_name)
        for fn in om.funcs[fn_name]:
            params = ({a.arg for a in fn.args.args}
                      | {a.arg for a in fn.args.kwonlyargs})
            defaults = _string_defaults(fn)
            for node in ast.walk(fn):
                if stamped_unknown is not None and _assigns_unknown(node):
                    stamped_unknown.setdefault(
                        f"<in place: {mod_name}.{fn_name}>", []).append(
                        f"{mod_name}.{fn_name}:{node.lineno}")
                if not isinstance(node, ast.Call):
                    continue
                nf = node.func
                name = (nf.id if isinstance(nf, ast.Name)
                        else nf.attr if isinstance(nf, ast.Attribute) else "")
                if name != "Finding":
                    continue
                kw = {k.arg: k.value for k in node.keywords if k.arg}
                if "ok" in kw and _literal(kw["ok"]) is True:
                    continue
                if "advisory" in kw and _literal(kw["advisory"]) is True:
                    continue
                cn = kw.get("check")
                site = f"{mod_name}.{fn_name}:{node.lineno}"
                if isinstance(v := _literal(cn), str):
                    names = {v}
                elif isinstance(cn, ast.Name) and cn.id in params:
                    names = set(passed.get((mod_name, fn_name), ()))
                    if cn.id in defaults:
                        names.add(defaults[cn.id])
                    names = names or {f"<unresolved:{cn.id}>"}
                else:
                    names = {"<unresolved>"}
                for nm in names:
                    out[nm].append(site)
                    if stamped_unknown is not None \
                            and _stamps_unknown(node):
                        stamped_unknown.setdefault(nm, []).append(site)
    for nm, site in GATE_EMITTED.items():
        out[nm].append(site)
    return {k: tuple(v) for k, v in sorted(out.items())}


# ---------------------------------------------------------------------------
# THE REMEDY FAMILIES - NUMBERS, NOT CLASSES
# ---------------------------------------------------------------------------
# WHAT CLOSES A REFUSAL IS A PROPERTY OF THE CHECK, not of where it
# happened, so a section names a FAMILY per check and this table turns that
# into the honest three-state numbers the shared classifier reads.  The
# `class` column is the DECLARED outcome and it is not what stamps the row:
# `classify` computes the class and `coverage` refuses if the two disagree,
# so a family that claims 1C and produces ACTIONABLE is a failing gate
# rather than a quiet lie on the board.
FAMILIES: dict[str, dict] = {
    # --- the factory owes this, and can discharge it by running its own
    #     tools in the right order.  1E, asserted here because no
    #     measurement of "a prerequisite has not run" is available to the
    #     shared helper.  EVERY 1E ROW MUST NAME THE PREREQUISITE.
    "CENSUS_NOT_TAKEN": dict(
        cls="1E_OUT_OF_ORDER", needs_prerequisite=True,
        why="the reading this refusal is about was never taken; the census "
            "that takes it is in this tree and has not run"),
    "PRODUCER_NOT_RUN": dict(
        cls="1E_OUT_OF_ORDER", needs_prerequisite=True,
        why="the artefact is absent, unreadable or scoped to another run, "
            "and its producer is in this tree"),
    "UPSTREAM_STAGE_NOT_RUN": dict(
        cls="1E_OUT_OF_ORDER", needs_prerequisite=True,
        why="the population this check measures is produced by an earlier "
            "stage of this tree that has not run for this world"),
    # --- no tool in this tree performs the remedy.  1C.
    "SOURCE_CHANGE": dict(
        cls="1C_UNSERVED", needs_prerequisite=False,
        why="the remedy is a change to a producer's source or to a corpus "
            "it writes, and no tool in this tree performs either"),
    "NO_DESTINATION": dict(
        cls="1C_UNSERVED", needs_prerequisite=False,
        why="the remedy is to BUILD something that is not there or to RETIRE "
            "what points at it, and nothing in this tree does either"),
    "GROUND_MUST_RISE": dict(
        cls="1C_UNSERVED", needs_prerequisite=False,
        why="the ground would have to come UP and every terrain instrument "
            "in this tree is cut-only by rule",
        riser=True),
    # --- a body is standing in the way.  1D, off the shared `_obstructed`
    #     predicate, which is a POSITIVE measurement and not a tiebreak.
    "BODY_IN_THE_WAY": dict(
        cls="1D_OBSTRUCTED", needs_prerequisite=False,
        why="a standing piece occupies the space this check requires; the "
            "remedy is demolition or a re-route, which is an operator "
            "decision and not a terrain cut",
        body=True),
    # --- a repair exists, the writer permits it, it destroys nothing.
    "RETIRE_AND_RESPAWN": dict(
        cls="ACTIONABLE", needs_prerequisite=False,
        why="the piece is retired and re-placed by a tool in this tree; no "
            "terrain is written and the piece is replaced, not removed"),
    "PLACE_A_PIECE": dict(
        cls="ACTIONABLE", needs_prerequisite=False,
        why="a tool in this tree places the missing piece; it writes no "
            "terrain and removes nothing"),
    # --- the blocker is THIS FACTORY'S OWN OUTPUT.  Never declared by a
    #     check: `own_blockers` stamps it at gate time, off this world's
    #     ledger, on a row whose operator class named a piece or a claim
    #     the ledger itself placed.  See `own_blockers`.
    "OWN_BLOCKER": dict(
        cls="ACTIONABLE", needs_prerequisite=False,
        why="every piece or claim this refusal is blocked by was placed by "
            "this world's own ledger, so the build step that placed it "
            "re-places, retires or reconciles it; no operator decision is "
            "involved and nobody else's work is destroyed"),
    # --- AND NO FAMILY WHOSE CLASS IS UNKNOWN.  `NOT_MEASURED` stood here
    #     with class UNKNOWN, and UNKNOWN is `NO_CLASS` spelled again: a
    #     class a reader cannot route is no class.  MEASURED on drive 2's
    #     frozen Fresh42 save after StructFix: 23 checks named it (harbours
    #     16, roads 5, spawn 1, structures 1), the coverage audit counted
    #     them without refusing, and roads put 6 UNKNOWN rows on the board.
    #     "An instrument did not answer" HAS a class in this vocabulary, and
    #     it is one of two, by whether a run of a tool changes the answer:
    #       CENSUS_NOT_TAKEN  a tool in this tree takes the reading and did
    #                         not take it for this row - 1E, naming it;
    #       SOURCE_CHANGE     no run of any tool changes it: the instrument
    #                         cannot answer this question as written - 1C.
    #     `coverage` refuses any declaration naming a family outside this
    #     table or one whose class is UNKNOWN.
}


# THE TWO OWNERSHIP READINGS EVERY IN-PLACE CLASSIFIER TAKES, AND THE
# PREREQUISITE THAT TAKES EACH.  One spelling for roads, harbours and
# structures, so a 1E row names the same reading in every section.
READS_OWNERS = (
    "read the flatten=FORBIDDEN extents off this world's ledger chain "
    "(settlements.forbidden_extents over settlements.live_records): the "
    "owner set this refusal's repair is judged against was not read")
READS_WRITES = (
    "read the recorded terrain_write footprints off this world's ledger "
    "chain (harbours.write_footprints -> WriteIndex): whose ground this "
    "refusal's repair lands in was not asked")


def unread(owners, writes) -> dict[str, str]:
    """`settle`'s `unread` for the two ownership instruments: each one that
    came back None, mapped to the prerequisite that takes it."""
    return {k: v for k, v, got in (("owners", READS_OWNERS, owners),
                                   ("writes", READS_WRITES, writes))
            if got is None}


def out_of_order(nums: dict, *prerequisites: str) -> str:
    """Stamp `nums` 1E, naming what it waits on, and return the class.  A 1E
    that names nothing is refused here, as `_numbers` refuses it."""
    named = sorted({p for p in prerequisites if p})
    if not named:
        raise ValueError("1E_OUT_OF_ORDER must name its prerequisite")
    nums["termination_family"] = "CENSUS_NOT_TAKEN"
    nums["termination_family_why"] = FAMILIES["CENSUS_NOT_TAKEN"]["why"]
    nums["prerequisite"] = "; ".join(named)
    return "1E_OUT_OF_ORDER"


def settle(nums: dict, unread: dict[str, str] | None = None) -> str:
    """The class of one refusal an IN-PLACE classifier has filled.  One
    implementation for every section that classifies against readings only
    it holds (roads, harbours, structures).

    `settlements.termination_class` returns UNKNOWN when a question it
    reads was never asked - an owner set, a footprint index, a builder - and
    the CALLER knows which, so the caller says so: `unread` maps each
    reading this row depended on and did not get to THE PREREQUISITE THAT
    TAKES IT.  That is 1E_OUT_OF_ORDER by the vocabulary's own predicate - a
    prerequisite this tree performs has not run for this row - and a 1E row
    names what it waits on, so the prerequisite is published beside it.

    UNKNOWN WITH NOTHING UNREAD IS RETURNED, NOT PAPERED OVER.  Every
    reading arrived and the row's numbers still decide no class, so the
    classifier left a three-state field unfilled: a defect in ITS source.
    Classing that here would hide it from the very cases written to catch
    it (structures' `None` footprint neuter is exactly this shape).  It
    goes out UNKNOWN and `backstop` - after every section, on every run -
    classes it SOURCE_CHANGE and hands the section a `termination` refusal
    naming it, so the board never carries it and never absorbs it.
    """
    from .sections.settlements import termination_class    # noqa: PLC0415
    cls = termination_class(nums)
    if cls == "UNKNOWN" and any((unread or {}).values()):
        return out_of_order(nums, *(unread or {}).values())
    return cls


# ---------------------------------------------------------------------------
# WHOSE BLOCKER: THE FACTORY'S OWN OUTPUT IS NEVER AN OPERATOR DECISION
# ---------------------------------------------------------------------------
# 1B, 1C and 1D are OPERATOR classes because on a live world the thing in
# the way may be a player's house or the seed's own geology.  A blocker THIS
# WORLD'S LEDGER PLACED is neither: it is this factory's output, and the
# build step that placed it is the owner of the repair.
#
# MEASURED on drive 3's frozen Fresh42 save 05eb7c6c05de: the gate reported
# 49 refusals and ACTIONABLE 0, and the rows named our own pieces as the
# blockers - `roads/obstruction` "36 of 134 pieces of one body stand in the
# carriageway: wood_pole", all 36 of them placed by T1-portalhub-temple-x01's
# own `bridge_body_place` records (seq 2682-2684, build step 6.5 `decks`);
# 15 `portals/approach_ray` rows stopped by our own boards and arches; 14
# `spawn/seat_*` rows under a `wood_beam` of our own town_body plans; a
# groundtorch our lamps placed, contested by our own road's write; five
# road termini contesting our own station-hub pad.  A work queue of 0 over
# a board of our own mistakes is the classifier hiding the factory's work
# on an operator's desk.
#
# THE ATTRIBUTION IS `structures.anchor_of`'s, and it is read at GATE TIME:
# a piece is ours when a `spawn_object` this world's ledger put on the wire
# names its prefab with its pivot within `structures.ATTRIBUTION_M` in x and
# z; a claim is ours when the terrain_write it names is a record of this
# chain.  Nothing is typed - a foreign blocker keeps its operator class, and
# an UNREAD or UNNAMED blocker is not foreign: it is 1E naming the reading.
#
# THE NEUTER SURFACE, ONE MODE PER DIRECTION:
#   "ledger"             the rule
#   "blind"              THE SHIPPED RULE: ownership is never read, every
#                        blocker is an operator's
#   "all_ours"           the over-eager direction: every named blocker is
#                        claimed, so a foreign body becomes factory work
#   "unread_is_foreign"  an unread or unnamed blocker keeps its operator
#                        class - absence of a reading read as an answer
_BLOCKER_OWNERSHIP = "ledger"


def neuter_blocker_ownership(mode: str):
    """A registered neuter restoring one direction of the ownership defect
    (see `_BLOCKER_OWNERSHIP`): `blind` is the shipped rule, `all_ours` the
    over-eager one, `unread_is_foreign` the unread-is-clear one."""
    if mode not in ("blind", "all_ours", "unread_is_foreign"):
        raise ValueError(f"{mode!r} is not an ownership neuter")

    def neuter():
        global _BLOCKER_OWNERSHIP
        was = _BLOCKER_OWNERSHIP
        _BLOCKER_OWNERSHIP = mode

        def undo():
            global _BLOCKER_OWNERSHIP
            _BLOCKER_OWNERSHIP = was
        return undo
    return neuter


OPERATOR_CLASSES = ("1B_CONTESTED", "1C_UNSERVED", "1D_OBSTRUCTED")
READS_PLACEMENTS = (
    "read this world's ledger chain as placements (termination.Placements."
    "read over ctx.ledger): whose piece or claim blocks this refusal was not "
    "asked, and an unread owner is not a foreign one")
# How many owner records one row publishes; the counts are always whole.
_OWNERS_SHOWN = 12


def _build_steps():
    """`{ordinal: BuildStep}` off `buildstage.BUILD_STEPS` as it stands when
    the gate runs - the one declaration of which tool each step runs - or
    `{}` when it cannot be imported (the record still names its stamp)."""
    try:
        import buildstage                                  # noqa: PLC0415
        return {float(st.ordinal): st for st in buildstage.BUILD_STEPS}
    except Exception:                                     # noqa: BLE001
        return {}


def owner_of(rec: dict, steps: dict | None = None) -> dict:
    """The owner of a repair on a piece or claim `rec` placed: the record,
    its role and actor, and THE BUILD STEP AND TOOL THAT WROTE IT - read off
    `meta.build_step`, which `ledger/live.py` stamps on every record a build
    step appends (a measurement, not the role's inference)."""
    p, m = rec.get("params") or {}, rec.get("meta") or {}
    stamp = m.get("build_step")
    st = None
    if stamp is not None:
        try:
            st = (steps if steps is not None else _build_steps()).get(
                float(str(stamp).split(":", 1)[0]))
        except ValueError:
            st = None
    return dict(seq=rec.get("seq"), op=rec.get("op"), role=p.get("role"),
                site_id=p.get("site_id") or p.get("name"),
                unit=m.get("build_unit"), actor=rec.get("actor"),
                build_step=stamp, step=(st.name if st else None),
                tool=(st.owner if st else None))


def _step_label(o: dict) -> str:
    if o.get("step"):
        return f"{o['build_step']} {o['step']} ({o['tool']})"
    return (f"unstamped {o.get('op')} {o.get('role')} seq {o.get('seq')}"
            if o.get("build_step") is None else f"{o['build_step']} (no "
            f"declared step)")


class Placements:
    """This world's ledger, read as WHO PLACED WHAT.

    `piece(prefab, x, z)` is the owner of the spawn record whose wire placed
    that piece, `record(name)` of the terrain_write a contest names, and
    `actor(name)` of the record a refusal's own claimant is.  Each returns
    an owner dict or None (the ledger placed nothing there: FOREIGN).
    `unreadable` counts records whose wire could not be read whole; a piece
    miss while any exist is UNREAD, not foreign, via `piece_state`."""

    _CACHE: dict = {}
    # The ops that CLAIM ground or place a piece; a ground contest prefers
    # the actor's terrain_write (see `actor`).
    CLAIM_OPS = ("terrain_write", "spawn", "spawn_plan", "portal")

    def __init__(self, records, source: str = "") -> None:
        from .sections.settlements import _actor_key, wire_parser  # noqa
        from .sections.structures import ATTRIBUTION_M     # noqa: PLC0415
        self.source = source
        self.tol = ATTRIBUTION_M
        self._key = _actor_key
        steps = _build_steps()
        parse = wire_parser()
        self.by_seq: dict = {}
        self._pieces: dict = collections.defaultdict(list)
        self._actors: dict = collections.defaultdict(list)
        self.commands = 0
        self.unreadable: list = []
        retired: set = set()
        for r in records:
            if r.get("op") == "retire":
                retired.update(int(s) for s in ((r.get("params") or {})
                                                .get("retires") or ())
                               if isinstance(s, (int, float)))
        for r in records:
            seq = r.get("seq")
            self.by_seq[seq] = r
            own = owner_of(r, steps)
            own["retired"] = seq in retired
            r["_owner"] = own
            p, m = r.get("params") or {}, r.get("meta") or {}
            # A CLAIM IS WHAT AN ACTOR WROTE OR PLACED.  An `observe`
            # recheck, a `zones_generate` or an `objects_clear` carries the
            # same unit name and claims nothing - MEASURED on drive 3, the
            # road termini's claimant resolved to their step-8 recheck.
            if r.get("op") in self.CLAIM_OPS:
                for nm in {p.get("name"), p.get("site_id"),
                           m.get("build_unit")}:
                    k = _actor_key(nm)
                    if k:
                        self._actors[k].append(r)
            wire = r.get("wire")
            if not wire or r.get("op") not in ("spawn", "spawn_plan",
                                               "portal"):
                continue
            text = "\n".join(str(w) for w in wire)
            cmds = parse(text, p.get("command_count"))
            if cmds is None:
                # A SHORT READ IS NOT A SMALL BODY.  What was read still
                # attributes; a MISS is then unread rather than foreign.
                self.unreadable.append(seq)
                cmds = parse(text) or []
            for (name, x, z) in cmds:
                self._pieces[(name, round(x / self.tol),
                              round(z / self.tol))].append((x, z, r))
                self.commands += 1

    @classmethod
    def read(cls, ledger) -> "Placements | None":
        """This chain's placements, or None when it cannot be read - and
        None is UNREAD, which `own_blockers` classes 1E, never foreign."""
        import json                                          # noqa: PLC0415
        try:
            path = Path(ledger)
            stt = path.stat()
            key = (str(path.resolve()), stt.st_mtime_ns, stt.st_size)
            if key not in cls._CACHE:
                recs = [json.loads(ln) for ln in
                        path.read_text().splitlines() if ln.strip()]
                cls._CACHE.clear()
                cls._CACHE[key] = cls(recs, str(path))
            return cls._CACHE[key]
        except Exception:                                  # noqa: BLE001
            return None

    def _latest(self, recs) -> dict | None:
        return (max(recs, key=lambda r: r.get("seq") or 0)["_owner"]
                if recs else None)

    def piece(self, prefab, x, z) -> dict | None:
        tol = self.tol
        cx, cz = round(float(x) / tol), round(float(z) / tol)
        got = [r for dx in (-1, 0, 1) for dz in (-1, 0, 1)
               for (px, pz, r) in self._pieces.get((prefab, cx + dx, cz + dz),
                                                   ())
               if math.hypot(px - float(x), pz - float(z)) <= tol]
        return self._latest(got)

    def piece_state(self, b: dict) -> tuple[str, dict | None]:
        """`("ours", owner)`, `("foreign", None)` or `("unread", None)`."""
        if _BLOCKER_OWNERSHIP == "all_ours":
            return "ours", dict(seq=None, op="neuter", role="all_ours",
                                build_step=None, step=None, tool=None)
        try:
            o = self.piece(b["prefab"], b["x"], b["z"])
        except (KeyError, TypeError, ValueError):
            return "unread", None
        if o is not None:
            return "ours", o
        return ("unread", None) if self.unreadable else ("foreign", None)

    def record(self, name) -> dict | None:
        """The terrain_write (or any record) a contest string names:
        `terrain_write <actor> (seq N)`.  The seq must be a record of THIS
        chain AND carry the named actor, or it is not ours."""
        if _BLOCKER_OWNERSHIP == "all_ours":
            return dict(seq=None, op="neuter", role="all_ours",
                        build_step=None, step=None, tool=None)
        s = str(name or "")
        mt = re.search(r"\bseq\s+(\d+)", s)
        want = self._key(s)
        if mt:
            r = self.by_seq.get(int(mt.group(1)))
            if r is None:
                return None
            p, m = r.get("params") or {}, r.get("meta") or {}
            keys = {self._key(v) for v in (p.get("name"), p.get("site_id"),
                                           p.get("role"), m.get("build_unit"))}
            return r["_owner"] if want in keys else None
        return self.actor(s)

    def actor(self, name) -> dict | None:
        if _BLOCKER_OWNERSHIP == "all_ours":
            return dict(seq=None, op="neuter", role="all_ours",
                        build_step=None, step=None, tool=None)
        k = self._key(name)
        recs = self._actors.get(k) or [] if k else []
        writes = [r for r in recs if r.get("op") == "terrain_write"]
        return self._latest(writes or recs)


def piece_ref(s) -> dict:
    """A standing piece, NAMED so its owner can be read: prefab and pivot.
    Every check that names a blocker publishes it in this shape."""
    return dict(prefab=s.name, x=round(float(s.pos[0]), 4),
                y=round(float(s.pos[1]), 4), z=round(float(s.pos[2]), 4))


def _dedup(owners: list) -> list:
    seen, out = set(), []
    for o in owners:
        k = o.get("seq")
        if k in seen:
            continue
        seen.add(k)
        out.append(o)
    return out


def own_blockers(findings, placements, section: str = "") -> dict:
    """Read whose blocker every OPERATOR-classed hard refusal names, and
    class it again off the answer.  In place; returns the census.

    WHAT "THE BLOCKER" IS, PER CLASS - and a check that does not name it is
    an UNNAMED blocker, which is 1E, never foreign:
      1D  `blocker_pieces`, the standing pieces the check counted, with
          `blocker_alternatives` (lists of indices) when any ONE set cleared
          closes the refusal - an arch needs one clear face, not two.
      1B  the claims: the contesters in `contested_by` other than the
          refusal's own actor, AND the refusal's own claimant
          (`claimant_pieces`, else `own_actor`/`segment`).  Both ours is
          ours to reconcile, and the owner is the LATER claim - the step
          that built against the other's standing work.
      1C  `blocker_pieces` when the check names one (a riser onto a standing
          top); a 1C that names no piece is not a blocker row.

    ALL OURS -> the shared `termination_class` returns ACTIONABLE off
    `blocker_ownership == "ours"`, family OWN_BLOCKER, owner the placing
    step.  ANY FOREIGN -> the class stands.  UNREAD/UNNAMED -> 1E naming
    the reading.  `placements=None` is UNREAD.
    """
    from .sections.settlements import (                    # noqa: PLC0415
        _contesters_other_than_self, repair_direction, termination_class)
    census: collections.Counter = collections.Counter()
    if _BLOCKER_OWNERSHIP == "blind":
        return dict(census, mode="blind")
    for f in findings:
        if f.ok or f.advisory:
            continue
        if not isinstance(f.numbers, dict):
            f.numbers = dict(f.numbers or {})
        n = f.numbers
        cls = n.get("termination_class")
        if cls not in OPERATOR_CLASSES:
            continue
        pieces = list(n.get("blocker_pieces") or ())
        if cls == "1C_UNSERVED" and not pieces:
            continue
        census["asked"] += 1
        state, owners, foreign, claim = None, [], [], None
        if cls == "1B_CONTESTED":
            contest = _contesters_other_than_self(n)
            cl_pieces = list(n.get("claimant_pieces") or ())
            cl_name = n.get("own_actor") or n.get("segment")
            if not contest or not (cl_pieces or cl_name):
                state = "unnamed"
            elif placements is None:
                state = "unread"
            else:
                got = [(c, placements.record(c)) for c in contest]
                if cl_pieces:
                    cs = [placements.piece_state(b) for b in cl_pieces]
                    cl_state = ("ours" if all(s == "ours" for s, _o in cs)
                                else "foreign" if any(s == "foreign"
                                                      for s, _o in cs)
                                else "unread")
                    claim = cs[0][1] if cl_state == "ours" else None
                else:
                    claim = placements.actor(cl_name)
                    cl_state = "ours" if claim is not None else "foreign"
                foreign = [str(c) for c, o in got if o is None]
                if cl_state == "foreign":
                    foreign.append(f"claimant {cl_name or cl_pieces[0]}")
                owners = [o for _c, o in got if o is not None]
                state = ("foreign" if foreign else
                         "unread" if cl_state == "unread" else "ours")
                if state == "ours":
                    n["claimant_owner"] = claim
                    # THE LATER CLAIM BUILT AGAINST THE EARLIER ONE'S
                    # STANDING WORK, so it is the one that reconciles.
                    owners = sorted(owners + [claim],
                                    key=lambda o: o.get("seq") or 0)[-1:]
        else:
            if not pieces:
                state = "unnamed"
            elif placements is None:
                state = "unread"
            else:
                att = [placements.piece_state(b) for b in pieces]
                alts = n.get("blocker_alternatives") or [
                    list(range(len(pieces)))]
                ours_alt = next((a for a in alts if a and all(
                    att[i][0] == "ours" for i in a)), None)
                if ours_alt is not None:
                    state = "ours"
                    owners = [att[i][1] for i in ours_alt]
                elif any(a and all(att[i][0] != "foreign" for i in a)
                         for a in alts):
                    state = "unread"
                else:
                    state = "foreign"
                foreign = sorted({pieces[i]["prefab"] for i, (s, _o)
                                  in enumerate(att) if s == "foreign"})
                n["blockers_ours"] = sum(1 for s, _o in att if s == "ours")
        n["blockers_named"] = (len(pieces) if cls != "1B_CONTESTED" else
                               len(_contesters_other_than_self(n)))
        n["blockers_foreign"] = foreign[:_OWNERS_SHOWN]
        if state in ("unread", "unnamed") \
                and _BLOCKER_OWNERSHIP == "unread_is_foreign":
            state = "foreign"
        n["blocker_ownership"] = state
        census[state] += 1
        if state == "ours":
            owners = _dedup(owners)
            n["blocker_owner"] = owners[:_OWNERS_SHOWN]
            n["owner_step"] = sorted({_step_label(o) for o in owners})
            n["termination_reclassed_from"] = cls
            n["termination_family"] = "OWN_BLOCKER"
            n["termination_family_why"] = FAMILIES["OWN_BLOCKER"]["why"]
            n["termination_class"] = termination_class(n)
            n["repair_direction"] = repair_direction(n)
            census[f"{cls}->{n['termination_class']}"] += 1
        elif state in ("unread", "unnamed"):
            n["termination_reclassed_from"] = cls
            n["termination_class"] = out_of_order(
                n, READS_PLACEMENTS if state == "unread" else (
                    f"the {section or '?'} {f.check} check must NAME the "
                    f"piece or claim it is blocked by (blocker_pieces / "
                    f"contested_by + own_actor), so whose it is can be "
                    f"read off this world's ledger; an unnamed blocker is "
                    f"not a foreign one"))
            census[f"{cls}->1E_OUT_OF_ORDER"] += 1
    return dict(census, mode=_BLOCKER_OWNERSHIP)


def _numbers(family: str, detail: str, nums: dict) -> dict:
    """The honest three-state numbers this family implies, in place.

    A NAME OUTSIDE `FAMILIES` IS REFUSED HERE, not looked up into a
    KeyError: `classify` and `backstop` read a ValueError as "this family
    cannot stand for this row" and class the row by the remedy that is
    actually true - a change to the declaring section's source."""
    if family not in FAMILIES:
        raise ValueError(f"{family!r} is not a remedy family in "
                         f"termination.FAMILIES")
    spec = FAMILIES[family]
    nums["termination_family"] = family
    nums["termination_family_why"] = spec["why"]
    if spec["needs_prerequisite"]:
        # A CLASS THAT SAYS "SOMETHING ELSE MUST HAPPEN FIRST" WITHOUT
        # SAYING WHAT IS AN INSTRUCTION TO GO AND FIND OUT, which is the
        # shape this whole gate exists to refuse.  So it is unspellable
        # here rather than warned about in a docstring.
        if not detail:
            raise ValueError(f"{family} must name its prerequisite")
        nums["prerequisite"] = detail
    elif detail:
        nums["no_terrain_repair"] = detail
    # NO CUT IS PROPOSED BY ANY FAMILY IN THIS TABLE.  Every one of them is
    # closed by running a tool, placing or retiring a piece, changing a
    # source, or not at all - so the footprint a terrain write would need is
    # 0.0, MEASURED, and never None.  0.0 says the question was asked and a
    # repair would need no ground; None says nobody looked.
    #
    # EXCEPT THE RISER, AND THE EXCEPTION IS LOAD-BEARING.
    # `no_cut_proposed` is the FIRST thing `repair_direction` reads and it
    # returns None on it - "no repair was proposed at all" - so a riser that
    # also declared no_cut_proposed loses its own SIGN, the raise branch of
    # the shared classifier never fires, and the row publishes ACTIONABLE:
    # a permitted write nobody in this tree can issue, sitting on the work
    # queue as if a planner could take it.  MEASURED here on the first run
    # of `family_outcomes`, which is why that function exists.
    nums["no_cut_proposed"] = not spec.get("riser")
    nums["ground_owned_by"] = None
    nums["ground_owner_distance_m"] = None
    nums["contested_by"] = None
    nums["repair_footprint_m"] = 0.0
    nums["contested_within_m"] = 0.0
    if spec.get("body"):
        # 1D IS A POSITIVE MEASUREMENT FROM A BODY CENSUS, and the caller
        # states the count.  `_obstructed` reads an int > 0; None means the
        # obstruction arm never ran, and None is not 0.
        nums["repairable_by_terrain_write"] = False
        n = nums.get("obstruction_bodies")
        if not (isinstance(n, int) and not isinstance(n, bool) and n > 0):
            raise ValueError(f"{family} must count the bodies it names")
        return nums
    if spec.get("riser"):
        # WHICH WAY, THROUGH THE SHARED ACCESSOR.  `repair_direction`
        # returns "raise" off `judged_as`/`rise_m` and the shared classifier
        # turns a raise into 1C_UNSERVED; nothing here decides that.
        nums["repairable_by_terrain_write"] = True
        nums.setdefault("judged_as", "riser")
        if not isinstance(nums.get("rise_m"), (int, float)):
            raise ValueError(f"{family} must measure the rise it names")
        return nums
    if spec["cls"] == "1C_UNSERVED":
        nums["repairable_by_terrain_write"] = False
        return nums
    # ACTIONABLE and 1E: the writer has nothing to permit or forbid, because
    # no family here writes terrain.
    nums["repairable_by_terrain_write"] = True
    return nums


def resolve(module, check: str, nums: dict) -> tuple[str, str] | None:
    """`(family, detail)` for one refusal, off the section's declaration.

    A value may be a `(family, detail)` pair or a CALLABLE of the row's own
    numbers, because several checks carry two remedy shapes under one name
    and the numbers separate them exactly - `spawn/instrument` is a seed
    dump that can be re-scanned OR a ground instrument that raised, and
    both are 1E, each naming the tool that takes its reading.  The
    DECLARATION is the key set, which is what `coverage` audits; the split
    inside a key is the section's own and stays with the section - and
    `coverage` reads every family name a callable can return, so a split
    cannot hide an unclassed arm either.
    """
    table = dict(GATE_FAMILIES)
    table.update(getattr(module, "TERMINATION_FAMILIES", {}) or {})
    got = table.get(check)
    if got is None:
        return None
    if callable(got):
        got = got(nums)
    fam, detail = got
    return fam, detail


# THE BACKSTOP AS IT SHIPPED, restorable by a neuter: a row arriving with
# `termination_class` UNKNOWN "already carries a class" and is kept.  False
# is the fix - UNKNOWN is `NO_CLASS` spelled again, so it is a hole.
_BACKSTOP_KEEPS_UNKNOWN = False


def _bare(n: dict) -> bool:
    """Does this row carry no class a reader can route?"""
    cls = n.get("termination_class")
    return not cls or (cls == "UNKNOWN" and not _BACKSTOP_KEEPS_UNKNOWN)


def fill(module, check: str, n: dict, section: str) -> str:
    """Class ONE row that reached a pass with no routable class.  Returns
    how it was filled: "family", "unnamed" or "unknown".

    THREE HOLES, ONE ANSWER EACH, AND NONE OF THEM IS UNKNOWN.

      the check's family is DECLARED   the family's numbers, through the
          shared classifier.  A family that refuses the row - a 1D with no
          counted body, a riser with no rise, a name outside FAMILIES - is
          not that family, and what is true instead is that the declaring
          section's source owes the measurement: SOURCE_CHANGE.
      the check is named by NO family  the remedy is to name it, which is a
          statement in source: SOURCE_CHANGE.  `coverage` makes this
          unreachable on the shipped tree; this says so if it is reached.
      the row ARRIVED UNKNOWN          the section's own in-place
          classifier returned no class for a row it owned.  Its measured
          numbers are KEPT - they are the evidence - and the class is the
          one remedy that is true of every such row: a change to that
          classifier's source.  1C, asserted, and the row says so.
    """
    from .sections.settlements import (repair_direction,    # noqa: PLC0415
                                       termination_class)
    if n.get("termination_class") == "UNKNOWN":
        n["termination_arrived_unknown"] = True
        n["termination_family"] = "SOURCE_CHANGE"
        n["termination_family_why"] = FAMILIES["SOURCE_CHANGE"]["why"]
        n["termination_hole"] = (
            f"the {section} section's own classifier returned UNKNOWN for "
            f"this {check} row; its numbers are kept as the evidence, and "
            f"closing it is a change to that classifier's source")
        n["termination_class"] = "1C_UNSERVED"
        return "unknown"
    # NOT A ROAD, AND THE FIELD SAYS SO RATHER THAN BEING ABSENT.  1D is a
    # positive measurement these sections mostly never take; None is "never
    # asked", which is exactly true, and it is not 0.
    n.setdefault("obstruction_bodies", None)
    # `module` None still reads `GATE_FAMILIES`: a section whose import
    # failed still owes `section_raised` its class.
    got = resolve(module, check, n)
    how = "family"
    if got is None:
        n["termination_unnamed"] = True
        fam, detail = ("SOURCE_CHANGE",
                       f"{check} is named by no remedy family; closed by "
                       f"naming it in the {section} section's "
                       f"TERMINATION_FAMILIES, a statement in source that "
                       f"no tool in this tree writes")
        how = "unnamed"
    else:
        fam, detail = got
    try:
        _numbers(fam, detail, n)
    except ValueError as exc:
        # A FAMILY WHOSE POSITIVE MEASUREMENT IS MISSING IS NOT THAT FAMILY,
        # and a raise out of `run()` would turn a classification hole into a
        # section that produced no verdict at all.  What IS true is that the
        # section declared a family its own row cannot carry.
        n["termination_family_refused"] = str(exc)
        _numbers("SOURCE_CHANGE",
                 f"the {section} section declares {fam} for {check} and "
                 f"this row does not carry what {fam} requires ({exc}); "
                 f"closed by a change to the declaring section's source", n)
        fam = "SOURCE_CHANGE"
    n["termination_class"] = (
        "1E_OUT_OF_ORDER" if FAMILIES[fam]["cls"] == "1E_OUT_OF_ORDER"
        else termination_class(n))
    n["repair_direction"] = repair_direction(n)
    return how


def classify(module, findings, section: str = "", placements=None) -> list:
    """Give every hard refusal in `findings` its `termination_class`.

    Returns the section's own termination row, exactly as the four sections
    that already classify publish one, so the board can add the eleven up.

    `placements` is this world's `Placements`; every operator-classed row
    is then asked whose blocker it names (`own_blockers`).  None is UNREAD,
    and a row that names a blocker is then 1E - never a foreign one.
    """
    from .registry import Finding                           # noqa: PLC0415
    from .sections.settlements import (repair_direction,    # noqa: PLC0415
                                       termination_class)
    hard = [f for f in findings if not f.ok and not f.advisory]
    name = section or getattr(module, "__name__", "?").rsplit(".", 1)[-1]
    by_class: collections.Counter = collections.Counter()
    unnamed: list[str] = []
    arrived_unknown: list[str] = []
    for f in hard:
        n = f.numbers = dict(f.numbers or {})
        if not _bare(n):
            # A ROW THAT ALREADY CARRIES A CLASS KEEPS IT, AND THIS IS THE
            # LINE THAT MAKES THE REPAIR SAFE TO LAND.  Four sections
            # classify their own rows against measurements only they hold -
            # a harbours restoration's per-vertex attribution, a road's own
            # disposal depth - and a pass that re-derived those from a
            # remedy family would overwrite 105 measured classes on
            # Ulfsland with a coarser answer.  This function FILLS HOLES; it
            # does not re-decide.  UNKNOWN is a hole, not a class.
            continue
        how = fill(module, f.check, n, name)
        if how == "unnamed":
            unnamed.append(f.check)
        elif how == "unknown":
            arrived_unknown.append(f.check)
    # AND THEN WHOSE BLOCKER, off the ledger - the one re-decision this
    # function makes, and it is a MEASUREMENT: an operator class whose
    # every blocker this world's own ledger placed is the factory's work.
    ownership = own_blockers(hard, placements, name)
    for f in hard:
        by_class[(f.numbers or {})["termination_class"]] += 1
    act = by_class.get("ACTIONABLE", 0)
    # THE CHECK NAME IS A LITERAL, AND THAT IS NOT A STYLE CHOICE.  An
    # f-string here is unreadable to the enumerator that audits this gate's
    # coverage: `f"{name}/termination"` resolves to no name at all in the
    # parse tree, so THE ONE ROW THAT REPORTS UNNAMED CHECKS WOULD ITSELF
    # BE UNNAMEABLE.  The section is already on every Finding - `normalise`
    # stamps it - so the literal loses nothing.
    own = dict(refusals=len(hard), actionable=act, section=name,
               by_class=dict(sorted(by_class.items())),
               by_check={c: dict(sorted(collections.Counter(
                   (g.numbers or {}).get("termination_class")
                   or "UNCLASSIFIED"
                   for g in hard if g.check == c).items()))
                   for c in sorted({g.check for g in hard})},
               unnamed_checks=sorted(set(unnamed)),
               arrived_unknown=sorted(set(arrived_unknown)),
               blocker_ownership=ownership)
    holes = sorted(set(unnamed) | set(arrived_unknown))
    if holes:
        # AND WHEN THIS ROW REFUSES, IT CARRIES ITS OWN CLASS.  A
        # termination row that reported a hole in the board and then left a
        # second hole where its own class should be would be the defect
        # reporting itself and committing it in the same finding.
        _numbers("SOURCE_CHANGE",
                 f"class {holes} in this section's source - name it in "
                 f"TERMINATION_FAMILIES, or make its in-place classifier "
                 f"return a class; no tool in this tree writes either",
                 own)
        own["termination_class"] = termination_class(own)
        own["repair_direction"] = repair_direction(own)
    return [Finding(
        check="termination", ok=not holes,
        message=(f"{name}: {len(hard)} refusal(s), {act} ACTIONABLE"
                 + ("; by class "
                    + (", ".join(f"{k} {v}"
                                 for k, v in sorted(by_class.items()))
                       or "none"))
                 + (f"; {len(unnamed)} NAMED BY NO REMEDY FAMILY: "
                    f"{sorted(set(unnamed))}" if unnamed else "")
                 + (f"; {len(arrived_unknown)} ARRIVED UNKNOWN from this "
                    f"section's own classifier: {sorted(set(arrived_unknown))}"
                    if arrived_unknown else "")),
        measured=float(act), limit=float(len(hard)), numbers=own,
        population="every hard refusal this section produced in this run")]


def backstop(section: str, findings) -> int:
    """Class any hard refusal that reached the board with NO ROUTABLE class.

    ONE PLACE, FOR ALL ELEVEN SECTIONS AND FOR BOTH PATHS OUT OF A RUN.
    `__main__` calls this after every section, including the one that
    RAISED - `section_raised` is constructed by the harness, passes through
    no section's pass, and had no class on any world.

    IT FILLS HOLES AND RE-DECIDES NOTHING.  A row that already carries a
    class is untouched, which is why landing it cannot move Ulfsland's 105:
    all 105 arrive classified by their own sections.

    AND UNKNOWN IS A HOLE.  It shipped as "a row that carries a class", so
    drive 2's frozen Fresh42 save put 6 roads rows on the board UNKNOWN -
    the S4-x01 and T3-x01 decks and the four S4 steps inside S4-x01's span
    - through a backstop that reported 0 filled.  Such a row is classed by
    `fill`, which keeps its evidence, and the section is handed ONE MORE
    refusal, `termination`, naming the checks its classifier left unclassed
    - so the hole is closed on the board AND loud there, never absorbed.

    IT IS NOT THE COVERAGE CLAIM, EITHER.  What makes the number mean
    something is `coverage()`, which reads the SOURCE: every emittable
    refusal must be NAMED by a family with a class, no reachable site may
    stamp UNKNOWN, and the gate's own selftest refuses otherwise.  This
    function is what happens when that audit has been passed and a row
    still arrives bare - reported with its check name, never silently.
    """
    import importlib                                        # noqa: PLC0415
    from .registry import Finding                           # noqa: PLC0415
    try:
        mod = importlib.import_module(f".sections.{section}", __package__)
    except Exception:                                      # noqa: BLE001
        mod = None
    n_filled = 0
    holes: dict[str, list[str]] = collections.defaultdict(list)
    for f in list(findings):
        if f.ok or f.advisory:
            continue
        n = f.numbers = dict(f.numbers or {})
        if not _bare(n):
            continue
        how = fill(mod, f.check, n, section)
        if how != "family":
            holes[how].append(f.check)
        n["termination_by_backstop"] = True
        n_filled += 1
    if holes:
        # THE HOLE, LOUD, AND CARRYING ITS OWN CLASS.  `termination` is in
        # `GATE_FAMILIES` for all eleven sections and in `GATE_EMITTED`,
        # because this line emits it on any section's behalf.
        own = dict(section=section, obstruction_bodies=None,
                   unnamed_checks=sorted(set(holes.get("unnamed", ()))),
                   arrived_unknown=sorted(set(holes.get("unknown", ()))))
        fill(None, "termination", own, section)
        findings.append(Finding(
            check="termination", ok=False,
            message=(f"{section}: {sum(len(v) for v in holes.values())} "
                     f"refusal(s) reached the board with no routable class "
                     f"and were classed by the backstop: "
                     + "; ".join(f"{k} {sorted(set(v))}"
                                 for k, v in sorted(holes.items()))),
            measured=float(sum(len(v) for v in holes.values())), limit=0.0,
            numbers=own,
            population="every hard refusal this section put on the board"))
    return n_filled


# THE HARNESS'S OWN REFUSAL, CLASSIFIED ONCE FOR ALL ELEVEN.  A section
# that raised measured nothing, so there is no repair to ask an ownership
# question about - and the remedy is a fix to the section's own source,
# which no tool in this tree performs.  That sentence IS 1C_UNSERVED, so it
# is classed 1C here.  It used to be UNKNOWN "deliberately", and UNKNOWN is
# `NO_CLASS` spelled again: a class a reader cannot route is no class.
GATE_FAMILIES: dict[str, tuple[str, str]] = {
    "section_raised": (
        "SOURCE_CHANGE",
        "the section raised before it could measure anything; the traceback "
        "on stderr names the line, and a fix to the section's own source is "
        "the remedy, which no tool in this tree performs"),
    # AND THE ROW THIS MODULE ITSELF EMITS.  `classify` publishes one per
    # section and it REFUSES when some check of that section is named by no
    # remedy family, so it is an emittable refusal like any other and the
    # coverage audit demands a class for it.  It already carries one by the
    # time it is built (see `classify`); this entry is what makes the
    # DECLARATION complete for all eleven sections at once.
    "termination": (
        "SOURCE_CHANGE",
        "a check named by no remedy family is closed by naming it in its "
        "section's TERMINATION_FAMILIES, and a row an in-place classifier "
        "left UNKNOWN by making that classifier return a class; both are "
        "statements in source and no tool in this tree writes one"),
}


# A REMEDY-FAMILY NAME, AS A TOKEN: `CENSUS_NOT_TAKEN`, `SOURCE_CHANGE`.
# Read out of a callable family's own code so its RETURNS are audited, not
# just its key - `spawn`'s `_instrument_family` returned `CENSUS_NOT_TAKEN`
# for a dump and `NOT_MEASURED` for a ground instrument under ONE key, and
# an audit of the key saw a callable and passed it.
_FAMILY_TOKEN = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")


def _returnable_families(value, module, _depth: int = 0) -> set[str] | None:
    """Every family name a declaration can hand `classify`, or None when it
    names none this audit can read.

    A `(family, detail)` pair names its first element.  A CALLABLE names
    every family-shaped string constant in its code, nested code included,
    plus the first element of every module-level `(family, detail)` pair it
    reads and - two levels deep - every same-module helper it calls.  That
    is a SUPERSET of what it can return, and the superset is the
    conservative direction: it demands a class for an arm that may never
    run, and it can never pass an arm that can.
    """
    if isinstance(value, tuple):
        return {value[0]} if value and isinstance(value[0], str) else None
    code = getattr(value, "__code__", None)
    if code is None:
        return None
    out: set[str] = set()
    # A CONSTANT `return ("FAMILY", "detail")` IS FOLDED INTO ONE TUPLE
    # CONSTANT by the compiler, so tuples are opened as well as code.
    stack: list = [code]
    while stack:
        c = stack.pop()
        for k in (c.co_consts if hasattr(c, "co_consts") else c):
            if isinstance(k, str) and _FAMILY_TOKEN.match(k):
                out.add(k)
            elif hasattr(k, "co_consts") or isinstance(k, (tuple,
                                                           frozenset)):
                stack.append(k)
        for nm in getattr(c, "co_names", ()):
            g = getattr(module, nm, None)
            if isinstance(g, tuple) and len(g) == 2 \
                    and isinstance(g[0], str) and _FAMILY_TOKEN.match(g[0]):
                out.add(g[0])
            elif (_depth < 2 and g is not value and callable(g)
                  and getattr(g, "__module__", None)
                  == getattr(module, "__name__", None)):
                out |= _returnable_families(g, module, _depth + 1) or set()
    return out or None


def unclassed(module) -> dict[str, str]:
    """`{check: why}` for every declaration in `module` (and in
    `GATE_FAMILIES`) that CAN hand a refusal no class: a family outside
    `FAMILIES`, a family whose class is UNKNOWN, or a callable whose returns
    name no family at all."""
    table = {**GATE_FAMILIES,
             **(getattr(module, "TERMINATION_FAMILIES", {}) or {})}
    out: dict[str, str] = {}
    for check, value in sorted(table.items()):
        fams = _returnable_families(value, module)
        if not fams:
            out[check] = ("a callable family whose returns name no family "
                          "this audit can read")
            continue
        bad = sorted(f for f in fams
                     if f not in FAMILIES or FAMILIES[f]["cls"] == "UNKNOWN")
        if bad:
            out[check] = (f"can return {bad}, "
                          + ("which is not a remedy family"
                             if any(f not in FAMILIES for f in bad)
                             else "whose class is UNKNOWN")
                          + " - so a row of it reaches the board with no "
                            "class a reader can route")
    return out


# ---------------------------------------------------------------------------
# THE COVERAGE CLAIM
# ---------------------------------------------------------------------------
def coverage(sections=None) -> list[dict]:
    """Per section: what it CAN emit, what it NAMES, and the gap.

    `missing` is the number that makes `NO_CLASS 0` mean something.  It is
    computed from the SOURCE - every reachable construction site in this
    repository - and never from a world's output, because a world can only
    show which refusals fired tonight.
    """
    import importlib                                        # noqa: PLC0415
    # THE SHIPPED FILES, NOT THE REGISTRY, AND THE DIFFERENCE IS A HOLE.
    # `SECTIONS` holds only what THIS INVOCATION imported: `accept
    # --selftest --section provenance` registers two sections, so an audit
    # keyed on the registry would report FULL COVERAGE over two of eleven
    # and call it a pass.  MEASURED here, first run of this case.  The
    # population is the eleven files in `sections/`, which is what "every
    # section of this gate" actually means.
    names = sorted(sections or _shipped_sections())
    rows = []
    for name in names:
        mod = importlib.import_module(f".sections.{name}", __package__)
        stamped: dict = {}
        can = emittable(name, stamped)
        named = set(GATE_FAMILIES) | set(
            getattr(mod, "TERMINATION_FAMILIES", {}) or {})
        missing = sorted(set(can) - named)
        own = set(getattr(mod, "TERMINATION_FAMILIES", {}) or {})
        rows.append(dict(
            section=name, emittable=len(can), named=len(named),
            missing=missing,
            missing_sites={k: can[k] for k in missing},
            # STALE IS THE SECTION'S OWN ENTRIES ONLY.  `GATE_FAMILIES` is
            # declared for all eleven by construction - `section_raised` is
            # emitted by the harness and `termination` only by the sections
            # that call `classify` - so counting those as this section's
            # dead entries would report rot that is not there.
            stale=sorted(own - set(can)),
            checks=sorted(can),
            # UNKNOWN IS NO_CLASS SPELLED AGAIN, AND BOTH ARE REFUSED.
            # `stamped_unknown`: a reachable site that writes UNKNOWN into a
            # row - at its construction (drive 2's settlements
            # `terminus_unmeasured`) or in place (drive 2's roads
            # `road_termination`, 6 rows).  `unclassed`: a declaration that
            # can hand a row no class - drive 2's `NOT_MEASURED`, which this
            # audit COUNTED on 23 checks and passed.  Only emittable checks
            # are audited; a stale entry is reported as stale, not twice.
            stamped_unknown={k: tuple(v) for k, v in sorted(stamped.items())},
            unclassed={c: why for c, why in unclassed(mod).items()
                       if c in can}))
    return rows


# THE ONE WAY THIS AUDIT CAN BE MADE TO PASS WITHOUT BEING TRUE, off, and
# reachable so the selftest can put it back: read the section's own
# declaration as the population instead of auditing it against the source.
# That is the shipped defect one level up - "the board is fully classified"
# keyed on what a section SAYS rather than on what it CAN EMIT.
_COVERAGE_TRUSTS_THE_SECTION = False


def verdict(rows, want_sections: int = 0) -> list:
    """The refusal a gate owes when a section can emit a refusal it cannot
    class.  `rows` is `coverage()`'s output, or a fixture shaped like it.

    `want_sections` is the DENOMINATOR AS A VERDICT rather than as an
    assertion.  A coverage claim over two of eleven sections is the same
    shape as the defect it audits, and `run_cases` reads an AssertionError
    as a proof - so a premise expressed as `assert` would stamp this case
    PROVEN off an audit that measured nothing.  It is a Finding.
    """
    from .registry import Finding                           # noqa: PLC0415
    out = []
    total = sum(r["emittable"] for r in rows)
    bad = [r for r in rows if r["missing"]]
    if _COVERAGE_TRUSTS_THE_SECTION:
        bad = []
    if want_sections and len(rows) < want_sections:
        out.append(Finding(
            check="every_emittable_refusal_is_classified", ok=False,
            message=(f"this audit covered {len(rows)} section(s) and this "
                     f"gate ships {want_sections}: a coverage claim over a "
                     f"subset of the sections is the defect it audits"),
            measured=float(len(rows)), limit=float(want_sections),
            numbers=dict(audited=[r["section"] for r in rows],
                         want_sections=want_sections)))
    for r in bad:
        out.append(Finding(
            check="every_emittable_refusal_is_classified", ok=False,
            message=(f"the {r['section']} section can emit "
                     f"{len(r['missing'])} refusal(s) named by no remedy "
                     f"family, so each would reach the board as NO_CLASS: "
                     f"{r['missing']}; first site(s) "
                     f"{ {k: v[:2] for k, v in r['missing_sites'].items()} }"),
            measured=float(len(r["missing"])), limit=0.0,
            numbers=dict(section=r["section"], missing=r["missing"],
                         emittable=r["emittable"],
                         sites=r["missing_sites"])))
    # UNKNOWN IS THE SAME HOLE, AND THE SAME AUDIT REFUSES IT.  A site that
    # stamps its own row UNKNOWN passes `missing` - it is named - and reaches
    # the board unroutable.  `_COVERAGE_UNKNOWN_IS_A_CLASS` restores the
    # NO_CLASS-only property as it stood on drive 2.
    for r in ([] if _COVERAGE_UNKNOWN_IS_A_CLASS else rows):
        st = r.get("stamped_unknown") or {}
        if st:
            out.append(Finding(
                check="every_emittable_refusal_is_classified", ok=False,
                message=(f"the {r['section']} section stamps "
                         f"termination_class UNKNOWN at its own "
                         f"site(s) for {sorted(st)}: named, so NO_CLASS 0 "
                         f"holds, and unroutable, so it is the same hole; "
                         f"first site(s) { {k: v[:2] for k, v in st.items()} }"),
                measured=float(len(st)), limit=0.0,
                numbers=dict(section=r["section"], stamped_unknown=st)))
    # AND A DECLARATION THAT CAN HAND A ROW NO CLASS IS REFUSED, NOT
    # COUNTED.  Drive 2's audit named `NOT_MEASURED` on 23 checks in this
    # very message and passed them: harbours 16, roads 5, spawn 1,
    # structures 1.  `_COVERAGE_COUNTS_UNCLASSED` restores that.
    unc = {r["section"]: dict(r.get("unclassed") or {}) for r in rows
           if r.get("unclassed")}
    unc_bad = {} if _COVERAGE_COUNTS_UNCLASSED else unc
    for sec, u in sorted(unc_bad.items()):
        out.append(Finding(
            check="every_emittable_refusal_is_classified", ok=False,
            message=(f"the {sec} section can emit {len(u)} refusal(s) whose "
                     f"remedy family hands them no class a reader can "
                     f"route: {u}"),
            measured=float(len(u)), limit=0.0,
            numbers=dict(section=sec, unclassed=u)))
    n_unc = sum(len(v) for v in unc.values())
    out.append(Finding(
        check="every_emittable_refusal_is_classified",
        ok=not bad and not unc_bad,
        message=(f"{total} emittable hard refusal(s) across "
                 f"{len(rows)} section(s), every one named by a remedy "
                 f"family" if not bad else
                 f"{sum(len(r['missing']) for r in bad)} of {total} "
                 f"emittable refusal(s) are named by no remedy family")
                + (f"; {n_unc} of them are named by a family that hands "
                   f"them no class: {unc}" if unc else
                   "; every family named has a class"),
        measured=float(total - sum(len(r["missing"]) for r in bad) - n_unc),
        limit=float(total),
        numbers=dict(sections=len(rows), emittable=total,
                     missing=sum(len(r["missing"]) for r in bad),
                     unclassed=unc,
                     by_section={r["section"]: r["emittable"] for r in rows}),
        population="every Finding(ok=False) construction site reachable "
                   "from each section's run(), read out of the source"))
    return out


# The audit as it stood on drive 2, restorable by a neuter: only an UNNAMED
# check is a hole, and a site that stamps UNKNOWN passes.  False is the fix.
_COVERAGE_UNKNOWN_IS_A_CLASS = False
# The audit as GateInstr left it, restorable by a neuter: a family that
# hands its rows no class is COUNTED in the message and passed.  False is
# the fix.
_COVERAGE_COUNTS_UNCLASSED = False


def _shipped_sections() -> list[str]:
    return sorted(p.stem for p in (_HERE / "sections").glob("*.py")
                  if not p.stem.startswith("_"))


def family_outcomes() -> list[tuple[str, str, str]]:
    """`(family, declared class, computed class)` for every family.

    A FAMILY THAT CLAIMS ONE CLASS AND PRODUCES ANOTHER IS A LIE ON THE
    BOARD, and the declaration is only worth having if something checks it
    against the shared classifier rather than against a comment.
    """
    from .sections.settlements import termination_class     # noqa: PLC0415
    out = []
    for fam, spec in sorted(FAMILIES.items()):
        nums: dict = {"obstruction_bodies": None}
        if spec.get("body"):
            nums["obstruction_bodies"] = 1
        if spec.get("riser"):
            nums["rise_m"] = 1.0
        _numbers(fam, "a named prerequisite" if spec["needs_prerequisite"]
                 else "", nums)
        got = ("1E_OUT_OF_ORDER" if spec["cls"] == "1E_OUT_OF_ORDER"
               else termination_class(nums))
        out.append((fam, spec["cls"], got))
    return out
