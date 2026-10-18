"""EMISSION: every seed-scoped artefact this build WRITES is about THIS world.

THE GAP THIS SECTION EXISTS FOR, AND IT IS WHY IT IS A SECOND SECTION RATHER
THAN SEVEN MORE ROWS IN `provenance`.

`provenance` declares its population as EVERY SEED-SCOPED ARTEFACT THIS BUILD
READS.  All seven rows are INPUTS.  `worldseed.require_scope` refuses a foreign
artefact on the way in and five inputs are genuinely closed that way -- AND A
RUN CAN CONSUME ONLY ITS OWN INPUTS AND STILL EMIT AN OUTPUT PINNED TO ANOTHER
WORLD, BECAUSE NOTHING READS THE OUTPUT BACK.  MEASURED twice within one hour
on 2026-09-22, both inside a clean exit 0:

  crossings.yaml stamped `seed: Fresh42` whose `measured_by` named
    /tmp/roads/h1m.bin -- ULFSLAND'S FIELD -- twice inside its own evidence.
  segments.yaml stamped `seed: Fresh42 / world: Fresh42`, POI violations 0, of
    which 12,849 of 21,938 m (59% of the paved metreage) terminated at one of 8
    coordinates that are Ulfsland's, verbatim, to the decimal.  T1-portalhub-
    temple ran 830.3 m from a point chosen for a different planet to Fresh42's
    OWN derived temple, IN ONE SEGMENT.

THE PARTITION, because it is not input-versus-output:

  SELF-EVIDENCING   the artefact declares something checkable against the run's
                    seed -- its header, its citation fields.  ONE POPULATION.
  REFERENTIAL       coordinates.  A number cannot be shown foreign by
                    inspection.  The only legitimate second population is AN
                    INPUT THIS RUN ALREADY HOLDS FOR THIS SEED.

A check that needed ULFSLAND'S PLAN to detect FRESH42'S contamination would be
one more instance of the class it is meant to close, and would pass vacuously
on the first world that has no reference.  Nothing here opens another world's
artefact: rung C resolves `worlds/<world>/*/placements.yaml` for the world
being built and for no other.

A HEADER STAMP IS NECESSARY AND DEMONSTRABLY NOT SUFFICIENT -- the artefact
that motivated this section stamps its seed CORRECTLY -- which is the whole
reason rungs B and C exist and the reason `_case_a_correct_header_is_not_enough`
below is the one that would have caught the 59%.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

from .. import termination as T
from ..registry import Case, Finding, Section, register

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent.parent

# WHY EACH REFUSAL THIS SECTION CAN EMIT CAN OR CANNOT BE CLOSED.
#
# THE POPULATION IS `termination.emittable("emission")` - every
# `Finding(ok=False)` site reachable from `run()`, READ OUT OF THE SOURCE -
# and `termination.coverage` refuses this gate's own selftest if any of
# them is missing from this table.  Until 2026-09-23 this section published
# no class at all and Ulfsland's board read `NO_CLASS 0` only because this
# section happened to have no refusals on that world; the fresh seed
# Fresh42 put FIVE here and the board could triage none of them.
#
# THE TWO FAMILIES, AND THEY ARE DIFFERENT QUESTIONS.
#
#   AN ARTEFACT THAT IS ABOUT ANOTHER WORLD is closed by RE-EMITTING it
#     under this seed, and the emitter that would do it is named in the
#     row's own `OUTPUTS` entry - a tool in this tree, a prerequisite that
#     has not run.  1E, and the factory owes it.
#   AN ARTEFACT WITH NO EMIT-SIDE GUARD is closed by WRITING one into its
#     producer, or by naming an owner for it.  That is a change to source,
#     and no tool in this tree performs a change to source.  1C.
TERMINATION_FAMILIES = {
    "emitted_artefact_is_about_this_world": (
        "PRODUCER_NOT_RUN",
        "re-emit this artefact under this run's seed and world with the "
        "writer its own OUTPUTS row names; an artefact that cites another "
        "seed is a reading of a DIFFERENT WORLD, not contested ground"),
    "every_emitted_artefact_is_guarded_or_owned": (
        "SOURCE_CHANGE",
        "an emitted artefact with no emit-side guard AND no owner is closed "
        "by writing a guard into its producer or by naming an owner for it; "
        "this tree has no tool that edits a producer"),
    "output_is_guarded_at_emission": (
        "SOURCE_CHANGE",
        "an output with no emit-side guard is closed by writing one into "
        "its producer, which no tool in this tree performs"),
}


def _mods():
    """`emitscope`, `worldseed` and `roads/network.py`, in the import order
    that works.  `tools/jumpstart/network/` is a PACKAGE that shadows
    `roads/network.py` for every later importer, so `roads` goes in FRONT."""
    roads = JUMPSTART / "roads"
    if str(roads) not in sys.path:
        sys.path.insert(0, str(roads))
    if str(JUMPSTART) not in sys.path:
        sys.path.append(str(JUMPSTART))
    import emitscope                                        # noqa: PLC0415
    import worldseed                                        # noqa: PLC0415
    spec = importlib.util.spec_from_file_location(
        "_emission_network", roads / "network.py")
    netmod = sys.modules.get("_emission_network")
    if netmod is None:
        netmod = importlib.util.module_from_spec(spec)
        sys.modules["_emission_network"] = netmod
        spec.loader.exec_module(netmod)
    return emitscope, worldseed, netmod


# ---------------------------------------------------------------------------
# THE POPULATION: one row per artefact THIS BUILD WRITES.
#
# `guard` names the emitter that is REQUIRED to refuse before the bytes land,
# or None when the artefact is KNOWN UNGUARDED -- in which case `owner` and
# `unguarded_why` are MANDATORY and the finding is ADVISORY.  An unguarded
# output is REPORTED, NAMED and COUNTED; what it may NOT do is go quiet.
#
# `writers` is every code path that writes this file, not just the one that is
# guarded.  A guard at one writer while three others rewrite the same document
# is a narrow representation standing in for the written one -- MEASURED:
# `roads/segments.yaml` is written by `network.py::emit`, and rewritten whole by
# `deviate.py` and `junction.py --apply` through a safe_load/safe_dump
# round-trip.  The round-trip PRESERVES the guarded block (it re-dumps the
# parsed document) but does NOT re-run the guard, and that is stated here
# rather than implied by a count.
#
# `slot` is the `worldseed` resolver that says WHICH FILE the row is for the
# seed being judged, asked at CALL time.  These rows used to carry the default
# seed's file names -- `roads/segments.yaml` and four more -- and `run` read
# `JUMPSTART / name`, so on the frozen Fresh42 save this section refused FIVE
# TIMES, every one "stamps seed 'Pirate68'", about Ulfsland's files, and never
# opened one of Fresh42's.  MEASURED 2026-09-25.
# ---------------------------------------------------------------------------
OUTPUTS = (
    {"slot": "segments_path", "rung_c": True,
     "writers": ("roads/network.py::emit",
                 "roads/deviate.py --apply (whole-document round-trip)",
                 "roads/junction.py --apply (whole-document round-trip)"),
     "declares_scope": True,
     "declaration_is": "DERIVED -- `world`/`seed` are the module's SEED/WORLD, "
                       "rebound from `worldseed.apply(args)` in main()",
     "guard": "roads/network.py::emit -> emitscope.require_emission_scope",
     "rungs": "A+B+C (coordinates: node_provenance)"},
    {"slot": "crossings_path",
     "writers": ("roads/network.py::emit",),
     "declares_scope": True,
     "declaration_is": "DERIVED -- same SEED/WORLD as segments.yaml",
     "guard": "roads/network.py::emit -> emitscope.require_emission_scope",
     "rungs": "A+B (this document states no endpoint of its own; its declared "
              "crossings are `spec.DECLARED_CROSSINGS`, which ReDrive's filter "
              "scopes at the source)"},
    {"name": "roads/geography.<seed>.yaml",
     "writers": ("roads/geography.py::main",),
     "declares_scope": True,
     "declaration_is": "DERIVED -- `seed` from the resolved run seed, "
                       "`provenance.height_field` from the `--field` ARGUMENT "
                       "actually opened, `scanned_windows` from the loaded "
                       "fields. `world` is deliberately null: a generator dump "
                       "is a function of the seed alone.",
     "guard": None, "owner": "SurveyNodes",
     "unguarded_why":
         "Its emitter already reads every value it writes off the artefact it "
         "opened -- MEASURED on two seeds, zero measured values byte-identical "
         "between geography.yaml (Pirate68, 7 windows, 16 nodes) and "
         "geography.Fresh42.yaml (Fresh42, 9 windows, 12 nodes), all constants "
         "identical. CORRECTED BY ITS OWNER: the emitter's owner IS SurveyNodes "
         "-- roads/geography.py was created tonight and no other agent has ever "
         "written it -- so the one-line emit-side call is DEFERRED BEHIND TWO "
         "LIVE CONSOLE WRITES, not blocked on module ownership. My original "
         "reason said 'another agent's module' and was wrong; I had no way to "
         "know and the correction is theirs.",
     "rungs": "A+B would pass today, unenforced. Rung C is NOT owed here and "
              "that is stated rather than inferred: every coordinate in this "
              "document is DERIVED from the field rather than copied out of a "
              "second artefact, so equality against another population is not "
              "the question rung C asks."},
    {"slot": "sites_path",
     "writers": ("settlements/plan.py::main",),
     "declares_scope": True,
     "declaration_is": "DERIVED -- world/seed/seed_hash and "
                       "provenance.height_field / provenance.locations_dump "
                       "are read off the handles the solve actually opened",
     "guard": "settlements/plan.py::main -> emitscope.require_emission_scope",
     "rungs": "A+B (this document states solved CENTRES; the road nodes derived "
              "from them are checked at rung C in segments.yaml, where the "
              "derivation is declared)"},
    {"slot": "plan_path",
     "writers": ("settlements/plan.py::main",),
     "declares_scope": True,
     "declaration_is": "DERIVED -- same fields as sites.yaml, same handles",
     "guard": "settlements/plan.py::main -> emitscope.require_emission_scope",
     "rungs": "A+B (written by the same line as sites.yaml; guarding one of "
              "the pair and not the other would be a guard on half a write)"},
    {"slot": "north_path", "rung_c": True,
     "writers": ("roads/north.py",),
     "declares_scope": True,
     "declaration_is": "DERIVED -- seed/world, measured field, routed road "
                       "ids and paired terminals belong to this run",
     "guard": None, "owner": "NorthItineraryBuild",
     "unguarded_why":
         "North itinerary schema, scope and provisional status are refused "
         "by north_transport and the physical acceptance gate. This row "
         "does not claim the generic emitscope coordinate-citation guard "
         "has run at the planner's write boundary.",
     "rungs": "A+B re-read here; physical road/transport acceptance is "
              "mandatory in roads and north_transport"},
)

GUARDED = tuple(o for o in OUTPUTS if o["guard"])
UNGUARDED = tuple(o for o in OUTPUTS if not o["guard"])


def _artefact_path(o: dict, seed: str | None = None) -> Path | None:
    """The file a row IS for `seed` (default this run's), asked of worldseed
    at call time. None only for an inventory row with no path slot."""
    if not o.get("slot"):
        return None
    _e, ws, _n = _mods()
    return getattr(ws, o["slot"])(seed or ws.seed())


def _label(o: dict, seed: str | None = None) -> str:
    """How a finding names a row: the file it resolves to for `seed`."""
    p = _artefact_path(o, seed)
    if p is None:
        return o.get("name") or f"{o['slot']}({seed}): none for this seed"
    try:
        return str(p.relative_to(JUMPSTART))
    except ValueError:
        return str(p)



def run(ctx) -> list[Finding]:
    emitscope, worldseed, netmod = _mods()
    out: list[Finding] = []

    want_seed = getattr(ctx.args, "seed", None) or worldseed.seed()
    want_world = getattr(ctx.args, "world", None) or worldseed.world()

    for o in OUTPUTS:
        adv = o["guard"] is None
        label = _label(o, want_seed)
        out.append(Finding(
            check="output_is_guarded_at_emission",
            ok=not adv,
            message=(f"{label}: guarded by {o['guard']} [{o['rungs']}]"
                     if not adv else
                     f"{label}: NO EMIT-SIDE GUARD. owner={o['owner']}. "
                     f"{o['unguarded_why']}"),
            advisory=adv,
            numbers={"writers": len(o["writers"]),
                     "declares_scope": o["declares_scope"],
                     "guarded": not adv}))

    # THE LIVE ARTEFACTS, SCOPE-CHECKED AS OUTPUTS.  A file that is missing is
    # NOT a pass: a check that needs an artefact a fresh seed does not have is
    # a fixture-only check, so an absent output is REPORTED as absent and the
    # count says how many were actually read.
    read = 0
    for o in OUTPUTS:
        p = _artefact_path(o, want_seed)
        if p is None:
            continue
        label = _label(o, want_seed)
        if not p.exists():
            # AN EMPTY READING AND AN ABSENT READING ARE DIFFERENT CLAIMS, and
            # only one of them is evidence.  `ok=False, advisory=True` is the
            # only disposal that REPORTS, NAMES AND COUNTS this without
            # reddening a world over a file a fresh seed has not built yet; a
            # green row here would be "I looked and found none" printed for "I
            # never looked".
            out.append(Finding(
                check="emitted_artefact_is_about_this_world", ok=False,
                advisory=True,
                message=f"{label}: not present in this checkout, so this "
                        f"run read nothing about it -- absent is not a pass.",
                numbers={"present": False}))
            continue
        import yaml                                         # noqa: PLC0415
        doc = yaml.safe_load(p.read_text())
        # RUNG C ONLY WHERE THE EMITTER HAS ALREADY WRITTEN THE BLOCK.
        # GUARD BEFORE ARTEFACT IS A STOP-THE-WORLD: `roads/segments.yaml` on
        # disk was emitted before `node_provenance` existed, and demanding it
        # here would redden a correct world over a block only the NEXT emit can
        # add.  Absent is REPORTED with its remedy, never scored as a pass --
        # and the emitter itself refuses an emission that omits it, so this
        # tolerance cannot outlive the next run of the stage.
        wants_c = bool(o.get("rung_c"))
        has_c = isinstance(doc, dict) and isinstance(
            doc.get("node_provenance"), dict)
        if wants_c and not has_c:
            out.append(Finding(
                check="emitted_artefact_says_where_its_endpoints_came_from",
                ok=False, advisory=True,
                message=f"{label}: predates the node_provenance block, so "
                        f"NONE of its endpoint coordinates says where it came "
                        f"from and rung C could not run on it. Its writer is "
                        f"{o['writers'][0]}"
                        + (", which writes the block and refuses without it."
                           if o["guard"] else
                           ", which has no emit-side guard yet (owner="
                           f"{o.get('owner')}), so the block is owed there "
                           "before rung C can reach this artefact."),
                numbers={"node_provenance": "absent", "rung_c_ran": False}))
        rep = emitscope.audit(label, doc, seed=want_seed, world=want_world,
                              coordinates=("node_provenance"
                                           if wants_c and has_c else None),
                              root=JUMPSTART)
        read += 1
        out.append(Finding(
            check="emitted_artefact_is_about_this_world",
            ok=not rep["violations"],
            message=(f"{label}: {rep['counts']['citations']} citations, "
                     f"{rep['counts']['coordinates_attributed']} of "
                     f"{rep['counts']['coordinates']} endpoint coordinates "
                     f"attributed to an artefact this run holds for seed "
                     f"{want_seed}, {rep['counts']['coordinates_unreachable']} "
                     f"named unreachable"
                     if not rep["violations"]
                     else f"{label}: {rep['violations'][0]}"),
            numbers=rep["counts"]))

    # THE POPULATION IS DERIVED, NEVER TRANSCRIBED, AND THAT IS A DELIBERATE
    # CHOICE AGAINST THE HOUSE LITERAL.
    #
    # The obvious form here is `guarded == 2 and unguarded == 4`, asserted in
    # both directions so that adding a row moves a number.  It is also an
    # INCENTIVE DEFECT: under an exact equality the next agent who DISCOVERS a
    # seventh emitted artefact chooses between reddening the gate and not
    # adding the row, and the second is free, silent and looks like nothing
    # happened.  A green number that punishes disclosure will get the
    # disclosure it can live with -- the same inversion as a waiver becoming
    # routine.
    #
    # So the INVARIANT is asserted instead of the COUNT: every row is either
    # guarded at emission or owned by name, and the two classes exhaust the
    # population.  A seventh artefact widens the check automatically, and
    # there is no literal left to be tempted by.
    unowned = [_label(o, want_seed) for o in UNGUARDED if not o.get("owner")]
    out.append(Finding(
        check="every_emitted_artefact_is_guarded_or_owned",
        ok=(not unowned and len(GUARDED) + len(UNGUARDED) == len(OUTPUTS)),
        message=(f"{len(OUTPUTS)} emitted artefacts, {len(GUARDED)} guarded at "
                 f"emission, {len(UNGUARDED)} unguarded-and-owned, {read} read "
                 f"back in this run"
                 if not unowned else
                 f"emitted artefacts with no emit-side guard AND no owner: "
                 f"{unowned}"),
        numbers={"outputs": len(OUTPUTS), "guarded": len(GUARDED),
                 "unguarded": len(UNGUARDED), "unowned": len(unowned),
                 "read_back": read}))
    # AND EVERY REFUSAL ABOVE GETS ITS CLASS, THROUGH THE ONE CLASSIFIER.
    # `termination.classify` produces the numbers each remedy family
    # implies and hands them to `settlements.termination_class`; this
    # section owns the FAMILY of each of its checks and owns no cascade.
    return out + T.classify(sys.modules[__name__], out, "emission")


# ---------------------------------------------------------------------------
# CASES.  EVERY FIXTURE BELOW CONSTRUCTS ITS OWN ARTEFACTS IN A TEMPORARY
# DIRECTORY.  Both measured instances are on disk on this host right now and
# NEITHER IS TOUCHED: a case that borrowed /tmp/freshseed/FINAL2/segments.yaml
# or ReDrive's Fresh42 crossings.yaml would go QUIET the day those are
# regenerated instead of going red, and this whole section exists because an
# artefact stopped describing what everyone believed it described.
#
# No case here reaches a shared instrument, opens a save, or takes the
# project-wide flock.
# ---------------------------------------------------------------------------
_F = "Fresh42"


def _fresh_root(tmp: Path, *, with_placements: bool) -> Path:
    """A CONSTRUCTED jumpstart root for seed/world Fresh42.

    `with_placements` builds the one input a Fresh42 run would legitimately
    hold -- its own `worlds/Fresh42/*/placements.yaml` -- so rung C has
    something to resolve.  Without it, the world simply has no placement set,
    which is the state a genuinely fresh seed is in.
    """
    import yaml                                             # noqa: PLC0415
    root = tmp / "root"
    (root / "worlds" / _F / "outpost").mkdir(parents=True, exist_ok=True)
    if with_placements:
        (root / "worlds" / _F / "outpost" / "placements.yaml").write_text(
            yaml.safe_dump({
                "schema_version": 1, "world": _F, "seed": _F,
                "owner": "BlueprintHunt",
                "placements": [{"id": "fresh-portal-hub",
                                "solved": {"x": 812.5, "z": -344.5,
                                           "y": 41.0}}]},
                sort_keys=False))
    return root


def _fresh_field(tmp: Path, seed: str) -> Path:
    """A CONSTRUCTED artefact declaring `seed`, for a citation to name."""
    import yaml                                             # noqa: PLC0415
    p = tmp / f"field_{seed}.yaml"
    p.write_text(yaml.safe_dump({"seed": seed, "world": seed,
                                 "owner": "PatchScan"}, sort_keys=False))
    return p


# WHAT A CONSTRUCTED PLAN IS CALLED IN A FINDING.  A label, not a file: the
# default seed's file name here read as "this checkout's plan" in every
# message a fixture produced.
_FIXTURE_LABEL = "the constructed plan fixture"


def _audit(doc, *, root: Path, seed: str = _F, world: str = _F,
           coordinates: str | None = "node_provenance"):
    emitscope, _ws, _n = _mods()
    return emitscope.audit(_FIXTURE_LABEL, doc, seed=seed, world=world,
                           coordinates=coordinates, root=root)


def _refusal(name: str, rep, *, numbers) -> list[Finding]:
    return [Finding(check=name, ok=not rep["violations"],
                    message=(rep["violations"][0] if rep["violations"]
                             else "accepted"),
                    numbers=numbers)]


# --- THE TWO MEASURED INSTANCES, RECONSTRUCTED -----------------------------
def _case_a_correct_header_is_not_enough():
    """THE 59%, AND THE ONE A HEADER CHECK PASSES CLEAN.

    The document stamps `seed: Fresh42 / world: Fresh42` CORRECTLY and its
    endpoints are Ulfsland's installation coordinates, verbatim, to the
    decimal.  Rung A accepts it.  Rung C refuses it, because the coordinate
    says it was copied out of `worlds/Fresh42/*/placements.yaml` and Fresh42
    has no such placement.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        root = _fresh_root(tmp, with_placements=False)
        fld = _fresh_field(tmp, _F)
        doc = {"seed": _F, "world": _F,
               "height_source": {"field": str(fld)},
               "node_provenance": {
                   # Ulfsland's, to the decimal, as MEASURED on Fresh42.
                   "portalhub": {"xz": [-292.5, 213.5], "kind": "installation",
                                 "verbatim": True,
                                 "source": "worlds/<world>/*/placements.yaml",
                                 "cites": "MEASURED, placements.yaml 8d0eab4"},
                   "stathub": {"xz": [661.5, 1088.5], "kind": "installation",
                               "verbatim": True,
                               "source": "worlds/<world>/*/placements.yaml",
                               "cites": "MEASURED, placements.yaml 8d0eab4"}}}
        rep = _audit(doc, root=root)
        header_ok = not any("stamps seed" in v or "declares no seed" in v
                            for v in rep["violations"])
        if not header_ok:
            raise AssertionError(
                "the point of this case is that THE HEADER IS CORRECT; if "
                "rung A refuses it the fixture no longer encodes the defect")
        return _refusal("a_correct_header_is_not_enough", rep,
                        numbers={"header_ok": True,
                                 "coordinates": 2,
                                 "attributed": rep["counts"][
                                     "coordinates_attributed"]})


def _case_a_citation_naming_another_seeds_field_is_refused():
    """ReDrive's INSTANCE: `seed: Fresh42` over `measured_by` naming Ulfsland's
    field.  A SELF-CONTRADICTION INSIDE ONE FILE -- no second artefact, no
    coordinate comparison, and it is the cheapest rung there is.

    A DERIVED ARTEFACT INHERITS THE SCOPE OF EVERY ARTEFACT IT CITES: this is a
    violation EVEN WHEN THE NUMBERS WERE COMPUTED CORRECTLY.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        root = _fresh_root(tmp, with_placements=True)
        ulf = _fresh_field(tmp, "Pirate68")     # CONSTRUCTED Ulfsland field
        doc = {"seed": _F, "world": _F,
               "declared": [{"id": "S1", "measured_by": f"crossings/survey.py "
                                                        f"over {ulf}"}],
               "height_source": {"field": str(ulf)}}
        rep = _audit(doc, root=root, coordinates=None)
        return _refusal("a_citation_naming_another_seeds_field_is_refused",
                        rep, numbers={"cited_seed": "Pirate68",
                                      "declared_seed": _F,
                                      "citations": rep["counts"]["citations"]})


# --- ABSENT IS NOT PERMISSION, ON EVERY RUNG -------------------------------
def _case_an_output_that_declares_no_seed_is_refused():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        root = _fresh_root(tmp, with_placements=True)
        fld = _fresh_field(tmp, _F)
        doc = {"world": _F, "height_source": {"field": str(fld)},
               "node_provenance": {}}
        rep = _audit(doc, root=root)
        return _refusal("an_output_that_declares_no_seed_is_refused", rep,
                        numbers={"seed_key": "absent"})


def _case_an_output_that_cites_nothing_is_refused():
    """A self-evidencing check works by asking the artefact what it claims; AN
    ARTEFACT THAT CLAIMS NOTHING ANSWERS NOTHING.  A guard whose coverage
    depends on the artefact having VOLUNTEERED a field is a guard the artefact
    can opt out of."""
    with tempfile.TemporaryDirectory() as td:
        root = _fresh_root(Path(td), with_placements=True)
        rep = _audit({"seed": _F, "world": _F, "segments": []},
                     root=root, coordinates=None)
        return _refusal("an_output_that_cites_nothing_is_refused", rep,
                        numbers={"citation_fields": 0})


def _case_a_coordinate_with_no_provenance_is_refused():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        root = _fresh_root(tmp, with_placements=True)
        fld = _fresh_field(tmp, _F)
        doc = {"seed": _F, "world": _F, "height_source": {"field": str(fld)},
               "node_provenance": {"mystery": {"xz": [1.0, 2.0]}}}
        rep = _audit(doc, root=root)
        return _refusal("a_coordinate_with_no_provenance_is_refused", rep,
                        numbers={"provenance": "absent"})


def _case_a_verbatim_claim_is_checked_by_looking():
    """A CITATION IS A CLAIM ABOUT WHERE A NUMBER CAME FROM.  The placement set
    EXISTS for this seed here, so the citation resolves -- and the coordinate
    is still not in it.  This separates "the file is missing" from "the file is
    there and does not say that", which are different refusals."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        root = _fresh_root(tmp, with_placements=True)
        fld = _fresh_field(tmp, _F)
        doc = {"seed": _F, "world": _F, "height_source": {"field": str(fld)},
               "node_provenance": {
                   "portalhub": {"xz": [-292.5, 213.5], "verbatim": True,
                                 "source": "worlds/<world>/*/placements.yaml",
                                 "cites": "MEASURED, placements.yaml"}}}
        rep = _audit(doc, root=root)
        resolved = any(r.get("resolved") for r in rep["rows"]
                       if r["rung"] == "C-coordinate")
        if not resolved:
            raise AssertionError(
                "this case is about a citation that RESOLVES and disagrees; if "
                "nothing resolved it has become the missing-file case")
        return _refusal("a_verbatim_claim_is_checked_by_looking", rep,
                        numbers={"resolved": True, "present": False})


# --- THE ACCEPTING DIRECTION.  A GUARD NEVER SEEN TO SAY YES IS THE FAILURE
# --- ONE LEVEL UP.
def _case_a_clean_fresh_seed_output_is_accepted():
    """THE SAME DOCUMENT SHAPE, ON A SEED THAT ACTUALLY HAS THE ARTEFACTS.

    Constructed: Fresh42's own placement set, Fresh42's own field, and an
    endpoint at Fresh42's own solved centre.  If this refuses, the check
    refuses every correct world and the 59% catch is worthless.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        root = _fresh_root(tmp, with_placements=True)
        fld = _fresh_field(tmp, _F)
        doc = {"seed": _F, "world": _F, "height_source": {"field": str(fld)},
               "node_provenance": {
                   "portalhub": {"xz": [812.5, -344.5], "kind": "installation",
                                 "verbatim": True,
                                 "source": "worlds/<world>/*/placements.yaml",
                                 "cites": "MEASURED, placements.yaml"}}}
        rep = _audit(doc, root=root)
        if rep["violations"]:
            raise AssertionError(
                f"a clean Fresh42 output must be ACCEPTED: {rep['violations']}")
        if rep["counts"]["coordinates_attributed"] != 1:
            raise AssertionError(
                "the accepting direction must ATTRIBUTE the coordinate, not "
                "merely fail to refuse it: an unreachable row is not a pass")
        return []


def _case_a_transformed_coordinate_is_named_not_passed():
    """THE HONEST RESIDUAL, FIXTURED SO IT CANNOT QUIETLY BECOME A PASS.

    A road node is the site's APPROACH, derived from the solved centre and not
    equal to it -- MEASURED, `wtspawn` at (110,-104) against `sites.yaml`'s
    (114,-108).  Equality is the wrong question there, and the answer is to
    NAME IT UNREACHABLE, never to widen the test into a tolerance.  A byte
    identity check that grows an exclusion list one entry at a time is a
    classification quietly becoming an authorisation.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        root = _fresh_root(tmp, with_placements=True)
        fld = _fresh_field(tmp, _F)
        doc = {"seed": _F, "world": _F, "height_source": {"field": str(fld)},
               "node_provenance": {
                   "approach": {"xz": [808.5, -340.5], "verbatim": False,
                                "source": "worlds/<world>/*/placements.yaml",
                                "cites": "MEASURED by Settlements at 1 m"}}}
        rep = _audit(doc, root=root)
        if rep["violations"]:
            raise AssertionError(
                f"a declared-derived coordinate must not be refused: "
                f"{rep['violations']}")
        if rep["counts"]["coordinates_unreachable"] != 1:
            raise AssertionError(
                "a derived coordinate must be COUNTED AS UNREACHABLE, not "
                "silently attributed -- the count is what makes it a ticket")
        return []


def _case_declared_endpoints_are_attributed_and_derived_ones_are_named():
    """A WORLD'S OWN COORDINATES MUST NOT BE REFUSED, AND MUST BE ATTRIBUTED.

    This was `ulfsland_stays_green`, the one case that read a real artefact
    (the repo's `roads/segments.yaml`) and demanded `>= 20` attributed
    endpoints -- a number that encodes the COMMITTED 39-leg plan.  On a
    regenerated world (29 legs, 16 of 34 endpoints resolved) it was red for a
    reason about a WORLD, and no tool had been wrong.  The purpose stays: the
    SHIPPING emitter (`network.node_provenance`) over a node table, audited by
    the SHIPPING audit -- "a check that cannot tell this world's coordinate
    from another world's is not a measurement".  The topology is CONSTRUCTED:

      fx-hub-a, fx-hub-b   declared placements (`PLACEMENT_OF`): verbatim
                           claims, each present in the world's placements.yaml
                           -> must be ATTRIBUTED, not merely un-refused
      fx-approach          a settlement site's road APPROACH, 4 m from its
                           site: derived, so it must be NAMED UNREACHABLE and
                           not refused (equality is the wrong question there)

    The counts asserted are the fixture's own (2 and 1), never a world's.
    `spec`'s classification tables are extended for the call and restored.
    """
    import yaml                                             # noqa: PLC0415
    import installation_sites                              # noqa: PLC0415
    emitscope, _w, netmod = _mods()
    sp = netmod.specmod
    nodes = {
        "fx-hub-a": {"xz": (812.5, -344.5), "kind": "installation"},
        "fx-hub-b": {"xz": (100.5, 200.5), "kind": "installation"},
        "fx-approach": {"xz": (808.5, -340.5), "kind": "settlement"}}
    segs = [{"from": "fx-hub-a", "to": "fx-approach"},
            {"from": "fx-hub-b", "to": "fx-hub-a"}]
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        root = _fresh_root(tmp, with_placements=True)
        (root / "worlds" / _F / "outpost" / "placements.yaml").write_text(
            yaml.safe_dump({
                "schema_version": 1, "world": _F, "seed": _F,
                "owner": "BlueprintHunt",
                "placements": [
                    {"id": "fx-hub-a",
                     "solved": {"x": 812.5, "z": -344.5, "y": 41.0}},
                    {"id": "fx-hub-b",
                     "solved": {"x": 100.5, "z": 200.5, "y": 38.0}}]},
                sort_keys=False))
        fld = _fresh_field(tmp, _F)
        added = [(installation_sites.PLACEMENT_OF, "fx-hub-a", ()),
                 (installation_sites.PLACEMENT_OF, "fx-hub-b", ()),
                 (sp.SETTLEMENT_SITES, "fx-approach", {})]
        # the settlement-site table the approach cites: the world's own is
        # another seed's file here, so a Fresh42 one is constructed
        sites = tmp / "fixture_site_table.yaml"
        sites.write_text(yaml.safe_dump({"seed": _F, "world": _F,
                                         "owner": "Settlements"},
                                        sort_keys=False))
        real_sites_path = netmod.worldseed.sites_path
        netmod.worldseed.sites_path = lambda *a, **k: sites
        for table, key, val in added:
            table[key] = val
        try:
            doc = {"seed": _F, "world": _F,
                   "height_source": {"field": str(fld)},
                   "node_provenance": netmod.node_provenance(segs, nodes)}
        finally:
            netmod.worldseed.sites_path = real_sites_path
            for table, key, _val in added:
                table.pop(key, None)
        rep = emitscope.audit(_FIXTURE_LABEL, doc, seed=_F, world=_F,
                              coordinates="node_provenance", root=root)
    if rep["violations"]:
        raise AssertionError(
            f"a world's own declared coordinates must stay green: "
            f"{rep['violations'][:3]}")
    got = (rep["counts"]["coordinates_attributed"],
           rep["counts"]["coordinates_unreachable"])
    if got != (2, 1):
        raise AssertionError(
            f"the two declared endpoints must be ATTRIBUTED and the derived "
            f"approach NAMED UNREACHABLE, not merely un-refused: (attributed, "
            f"unreachable) = {got}, required (2, 1) of "
            f"{rep['counts']['coordinates']} endpoints")
    return []


def _case_every_unguarded_output_names_an_owner():
    """THE POPULATION IS THE CHECK, AND AN UNGUARDED ROW MUST BE OWNED.

    An output recorded with `guard=None` and no owner is how a gap goes quiet;
    the only way to add one silently is to lie in writing.
    """
    bad = [_label(o) for o in UNGUARDED
           if not o.get("owner") or len(o.get("unguarded_why", "")) < 40]
    if bad:
        raise AssertionError(
            f"unguarded outputs with no owner or no stated reason: {bad}")
    for o in GUARDED:
        src = (JUMPSTART / o["guard"].split("::")[0]).read_text()
        if "require_emission_scope" not in src:
            raise AssertionError(
                f"{_label(o)} claims {o['guard']} refuses at emission and that "
                f"module does not call require_emission_scope -- a guard "
                f"cannot be claimed by typing its name")
    return []


# ---------------------------------------------------------------------------
# NEUTERS: RESTORE THE DEFECT VERBATIM.  Each one puts back the emitter as it
# stood before this section existed -- an `audit` that reads the header and
# nothing else -- which is precisely the state in which a Fresh42 run stamped
# its seed correctly and paved 59% of its metreage to another planet.
# ---------------------------------------------------------------------------
def _neuter_header_only():
    """THE DEFECT: an output guard that checks the stamp and stops."""
    emitscope, _ws, _n = _mods()
    real = emitscope.audit

    def header_only(label, doc, *, seed, world, coordinates=None, root=None):
        rep = real(label, doc, seed=seed, world=world, coordinates=None,
                   root=root)
        rep["violations"] = [v for v in rep["violations"]
                             if "stamps" in v or "declares no seed" in v]
        return rep

    emitscope.audit = header_only

    def undo():
        emitscope.audit = real
    return undo


def _neuter_absent_is_permission():
    """THE DEFECT IN ITS OTHER FORM: treat a missing declaration as consent,
    which is exactly how one world's coordinates walk into another's plan."""
    emitscope, _ws, _n = _mods()
    real = emitscope.audit

    def tolerant(label, doc, *, seed, world, coordinates=None, root=None):
        rep = real(label, doc, seed=seed, world=world,
                   coordinates=coordinates, root=root)
        rep["violations"] = [
            v for v in rep["violations"]
            if not ("declares no seed" in v or "names no artefact" in v
                    or "carries no provenance" in v or "carries no" in v
                    or "does not exist for seed" in v
                    or "cannot resolve" in v)]
        return rep

    emitscope.audit = tolerant

    def undo():
        emitscope.audit = real
    return undo


def _neuter_equality_over_every_record():
    """THE DEFECT THE ACCEPTING CASES GUARD AGAINST: the byte-identity rule
    stated at more than its true width.

    "No value may be identical to another seed's" applied to EVERY record --
    rather than to the records an emitter DECLARES were copied verbatim --
    refuses `wtspawn` (110,-104) against `sites.yaml`'s (114,-108), refuses
    every physical constant this project has, and would redden Ulfsland on its
    own correct artefacts.  A check that refuses correct worlds is not a
    stricter check, it is a broken one, and the only remedy on offer would be
    a hand-maintained exclusion list -- a tolerance standing in for a
    population.
    """
    emitscope, _ws, _n = _mods()
    real = emitscope.audit

    def always_equal(label, doc, *, seed, world, coordinates=None, root=None):
        recs = (doc.get(coordinates) if coordinates and isinstance(doc, dict)
                else None)
        if isinstance(recs, dict):
            for rec in recs.values():
                rec["verbatim"] = True
                rec.setdefault("source", "worlds/<world>/*/placements.yaml")
        return real(label, doc, seed=seed, world=world,
                    coordinates=coordinates, root=root)

    emitscope.audit = always_equal

    def undo():
        emitscope.audit = real
    return undo


def _neuter_transcribed_citation_path():
    """THE DEFECT ONE LEVEL UNDER THE CITATION: a path written for ONE world.

    `spec.py` cites `worlds/<world>/*/placements.yaml` and the reader resolves
    the placeholder for the world being built.  Freeze it to the fixture world
    instead -- which is exactly how a constant gets typed -- and a clean fresh
    seed's own placement set stops being findable, so the accepting arm goes
    red.  That is the proof the accepting arm is load-bearing rather than a
    decoration that could never fail.
    """
    emitscope, worldseed, _n = _mods()
    real = emitscope.resolve_citation

    def frozen(token, *, seed, world, root=None):
        return real(token, seed=worldseed.DEFAULT_SEED,
                    world=worldseed.DEFAULT_WORLD, root=root)

    emitscope.resolve_citation = frozen

    def undo():
        emitscope.resolve_citation = real
    return undo


def _neuter_an_unowned_row():
    """THE DEFECT: a row claiming a guard that does not exist.

    A guard cannot be claimed by typing its name, so point the claim at a
    module that never calls the refusal and the case must go red.
    """
    was = GUARDED[0]["guard"]
    GUARDED[0]["guard"] = "roads/spec.py::emit"

    def undo():
        GUARDED[0]["guard"] = was
    return undo


# ---------------------------------------------------------------------------
# THE SECOND THING AN EMITTED PLAN MUST NOT LIE ABOUT: THE ZONES IT WILL WRITE.
#
# Same sentence as the installation nodes, one column over -- AN EMITTED
# ARTEFACT DECLARING A SET THAT IS NOT WHAT THE WRITER WRITES.  `sg["zones"]`
# is the CARRIAGEWAY set; `ribbon.main()` writes to `segment_zones(...,
# batter_m=BATTER_MAX_M)`, which is wider by construction.
#
# MEASURED over all 39 segments of roads/segments.yaml at BATTER_MAX_M = 10.5,
# through ribbon's OWN segment_zones: 702 carriageway zones against 950 at
# batter reach, 37 OF 39 SEGMENTS SHORT, 248 zones missing -- a 26%
# under-declaration.  Three agents hand-derived it on three segments (55->75,
# 60->82, 6->10) and each read it as a quirk of their own closure.  It cost one
# real overlap at zone (-5,4), found only because an agent re-derived instead
# of trusting the column.
#
# The fixture below CONSTRUCTS a segment whose batter provably reaches a zone
# its carriageway does not, so the case does not depend on roads/segments.yaml
# still having that property.
# ---------------------------------------------------------------------------
# THE ZONE PITCH THIS PROJECT ROUTES ON IS 64 m, so a zone boundary sits at
# x = 32.  A centreline at x = 26 keeps its CARRIAGEWAY (half-width 3 m plus a
# 1 m shoulder, so 30 m) inside zone (0,0) while its BATTER (10.5 m, so 36.5 m)
# crosses into (1,0).  MEASURED through ribbon's own segment_zones:
#     x=26  carriageway [(0,0)]          batter [(0,0), (1,0)]
#     x=28  carriageway [(0,0), (1,0)]   batter [(0,0), (1,0)]   <- NO DIFFERENCE
# The first is the only geometry that can tell the two apart, and the fixture
# ASSERTS the difference rather than assuming it -- a case whose fixture has
# quietly stopped discriminating is the drift that goes silent rather than red.
ZONE_X = 26.0


def _zone_doc(declared) -> dict:
    """A plan document whose ONE segment runs beside a zone boundary."""
    seg = {"id": "Z1-a-b", "from": "a", "to": "b", "width_m": 6.0,
           "nodes": [[ZONE_X, 0.0], [ZONE_X, 2.0], [ZONE_X, 4.0],
                     [ZONE_X, 6.0]],
           "is_bridge": [False, False, False, False]}
    return {"seed": "Fresh42", "world": "Fresh42", "segments": [seg],
            "zones_write": declared}


def _carriageway_zones(segments) -> list[list[int]]:
    """THE DEFECT'S OWN SET: zones at carriageway width, batter excluded.

    This is what `sg["zones"]` holds and what every consumer read as a lock
    set.  Computed here, never taken from `write_zones`, so the neuter below
    cannot move both sides of the comparison at once.
    """
    import ribbon as ribbonmod                             # noqa: PLC0415
    out: set = set()
    for sg in segments:
        out.update(ribbonmod.segment_zones(
            sg["nodes"], sg["is_bridge"], sg["width_m"], batter_m=0.0))
    return [list(z) for z in sorted(out)]


def _case_the_carriageway_column_is_refused_as_a_write_set():
    """THE DEFECT, EXACTLY: a plan declaring the carriageway column as the set
    it will write.  That is the 248 zones, constructed down to one."""
    _e, _w, netmod = _mods()
    segs = _zone_doc(None)["segments"]
    carriage = _carriageway_zones(segs)
    batter = netmod.write_zones(segs)
    if len(carriage) >= len(batter):
        raise AssertionError(
            f"this fixture exists to make the batter set WIDER than the "
            f"carriageway set; it measured carriageway {carriage} against "
            f"batter {batter}, so the case discriminates nothing")
    try:
        netmod.require_write_zones_declared(_FIXTURE_LABEL,
                                            _zone_doc(carriage))
    except SystemExit as exc:
        return [Finding(
            check="the_carriageway_column_is_refused_as_a_write_set", ok=False,
            message=str(exc).splitlines()[0][:200],
            numbers={"declared_carriageway": len(carriage),
                     "derived_batter": len(batter)})]
    return [Finding(
        check="the_carriageway_column_is_refused_as_a_write_set", ok=True,
        message=f"a carriageway-only write-zone declaration ({carriage}) was "
                f"ACCEPTED against a batter set of {batter}",
        numbers={"declared_carriageway": len(carriage),
                 "derived_batter": len(batter)})]


def _case_a_plan_that_declares_no_write_set_is_refused():
    """ABSENT IS NOT PERMISSION, on this rung too."""
    _e, _w, netmod = _mods()
    try:
        netmod.require_write_zones_declared(_FIXTURE_LABEL, _zone_doc(None))
    except SystemExit as exc:
        return [Finding(
            check="a_plan_that_declares_no_write_set_is_refused", ok=False,
            message=str(exc).splitlines()[0][:200],
            numbers={"zones_write": "absent"})]
    return [Finding(
        check="a_plan_that_declares_no_write_set_is_refused", ok=True,
        message="a document declaring NO write set was ACCEPTED",
        numbers={"zones_write": "absent"})]


def _batter_zones(segments) -> list[list[int]]:
    """The TRUE write set, computed here through ribbon at BATTER_MAX_M.

    Deliberately NOT `netmod.write_zones`. An accepting arm that declares
    whatever the checker derives COMPARES THE CHECKER WITH ITSELF and accepts
    under every possible defect -- the always-says-yes signature, in the
    fixture rather than in the instrument. MEASURED: the first version of this
    case did exactly that and the harness caught it, reporting ACCEPT under a
    neuter that had made the checker's reach six times the writer's.
    """
    import ribbon as ribbonmod                             # noqa: PLC0415
    out: set = set()
    for sg in segments:
        out.update(ribbonmod.segment_zones(
            sg["nodes"], sg["is_bridge"], sg["width_m"],
            batter_m=ribbonmod.BATTER_MAX_M))
    return [list(z) for z in sorted(out)]


def _case_an_honest_zone_declaration_is_accepted():
    """THE ACCEPTING DIRECTION. A refusal that fires on the honest document
    refuses every plan this project will ever emit."""
    _e, _w, netmod = _mods()
    segs = _zone_doc(None)["segments"]
    declared = _batter_zones(segs)
    if len(declared) < 2:
        raise AssertionError(
            f"the accepting arm must declare a write set with real content "
            f"({declared}), or it proves only that a vacuous claim is "
            f"trivially satisfied")
    # `require_write_zones_declared` refuses by RAISING SystemExit, which the
    # case runner does not catch -- it is not an `Exception`. Under the neuter
    # this arm MUST come out the other way, and "the other way" has to be a
    # reported failure rather than the whole gate process exiting.
    try:
        netmod.require_write_zones_declared(_FIXTURE_LABEL,
                                            _zone_doc(declared))
    except SystemExit as exc:
        raise AssertionError(
            f"an honest write-zone declaration must be ACCEPTED: {exc}") from exc
    return []


def _neuter_the_carriageway_column():
    """RESTORE THE DEFECT VERBATIM: the write set taken at CARRIAGEWAY width.

    That is what `sg["zones"]` holds and what three agents took a zone lock
    from.  Under it, a carriageway-only declaration is no longer narrow and the
    refusing case must come out ACCEPT.
    """
    _e, _w, netmod = _mods()
    real = netmod.write_zones
    netmod.write_zones = _carriageway_zones

    def undo():
        netmod.write_zones = real
    return undo


def _neuter_an_overwide_reach():
    """THE OTHER WAY THIS CHECK CAN BE WRONG, and the one the accepting arm
    holds in place: a checker whose reach is WIDER than the writer's.

    It would refuse every honest document forever, and the only visible symptom
    would be a gate nobody can make green -- which is how a check gets weakened
    into a tolerance rather than fixed.
    """
    _e, _w, netmod = _mods()
    real = netmod.write_zones

    def overwide(segments):
        import ribbon as ribbonmod                         # noqa: PLC0415
        out: set = set()
        for sg in segments:
            out.update(ribbonmod.segment_zones(
                sg["nodes"], sg["is_bridge"], sg["width_m"],
                batter_m=ribbonmod.BATTER_MAX_M * 6.0))
        return [list(z) for z in sorted(out)]

    netmod.write_zones = overwide

    def undo():
        netmod.write_zones = real
    return undo


def _neuter_absence_is_permitted():
    """RESTORE THE DEFECT: a document with no write set waved through."""
    _e, _w, netmod = _mods()
    real = netmod.require_write_zones_declared

    def tolerant(name, doc):
        if isinstance(doc, dict) and doc.get("zones_write") is None:
            return
        return real(name, doc)

    netmod.require_write_zones_declared = tolerant

    def undo():
        netmod.require_write_zones_declared = real
    return undo


# ---------------------------------------------------------------------------
# THE SAME DECLARATION, READ BACK IN. THE THREE CASES ABOVE ARE EMIT-SIDE.
#
# `require_write_zones_declared` runs at EMIT only, so it protects documents
# NOT YET WRITTEN and leaves the one on disk exactly as it is.  MEASURED
# 2026-09-22: `roads/segments.yaml` -- the artefact all 39 segments live in and
# the one every tool in `roads/` opens -- CARRIES NO `zones_write` KEY AT ALL,
# and neither does `roads/north_segments.yaml`.  A check pointed at the output
# while the input goes unguarded is the same shape as the defect it closes.
#
# SO THE COLUMN IS RENAMED AT THE READ BOUNDARY rather than on disk.  The
# on-disk rename was REFUSED with its number: the carriageway column lives in
# 3 artefacts, 62 segment records, 1,129 zone entries -- roads/segments.yaml
# (git-TRACKED, 39 segments, 702 entries), roads/preflight.json (git-TRACKED,
# 17, 191) and roads/north_segments.yaml (untracked, 6, 236).  Renaming a key
# across committed artefacts is a DATA MIGRATION, and a forked on-disk format
# is worse than an ambiguous name.  On disk it stays `zones`; past
# `structures.load_segment_doc` it is `zones_carriageway` and `zones` DOES NOT
# EXIST, so a consumer reaching for it gets a KeyError at its own line.
#
# AND THE DEFERRAL THAT HELD THIS BACK WAS ITSELF UNMEASURED.  `network.py`
# said the rename "touches structures.py, ribbon.py, junction.py and every
# lock-taker".  MEASURED over 137 .py files under tools/jumpstart -- 540 bare
# `zones` tokens, of which 68 are the string KEY -- all three of those files
# read the column ZERO times; 32 of the 68 are the LEDGER's zone-control op
# param (557 committed ledger lines) and 24 are modules' own report fields.
# A blast radius estimated instead of counted, sitting in the file as the
# reason not to act.
# ---------------------------------------------------------------------------


def _stmod():
    """`roads/structures.py`, which owns the sanctioned parse."""
    _e, _w, _n = _mods()                       # puts `roads` at sys.path front
    import structures as stmod                             # noqa: PLC0415
    return stmod


def _zone_plan_file(tmp: Path, declared=None) -> Path:
    """A plan FILE, on disk, in the on-disk format, scoped to THIS run.

    Written rather than borrowed: the case must construct the state it tests,
    and `roads/segments.yaml` is the artefact under discussion.  The header is
    this run's own seed/world because `load_segment_doc` refuses a foreign one
    and that refusal is a different case's subject.
    """
    import yaml                                            # noqa: PLC0415
    _e, worldseed, _n = _mods()
    doc = _zone_doc(declared)
    doc["seed"], doc["world"] = worldseed.seed(), worldseed.world()
    seg = doc["segments"][0]
    # THE ON-DISK NAME, deliberately. A fixture written in the memory name
    # would prove the loader converts something nothing ever writes.
    seg["zones"] = _carriageway_zones(doc["segments"])
    if declared is None:
        doc.pop("zones_write", None)
    p = tmp / worldseed.segments_path().name
    p.write_text(yaml.safe_dump(doc, sort_keys=True))
    return p


def _segment_keys_on_disk(p: Path) -> set:
    """The keys of the FIRST segment record AS THE BYTES ON DISK CARRY THEM."""
    import yaml                                            # noqa: PLC0415
    return set(yaml.safe_load(p.read_text())["segments"][0].keys())


def _case_a_lock_taken_from_an_unparsed_plan_is_refused():
    """THE KNOWN-BAD READ, VERBATIM, AND IT IS STILL HOW MOST OF `roads/`
    OPENS THE PLAN.

    MEASURED 2026-09-22: twelve modules read the two plan files and only the
    `structures.segment_map` path goes through the sanctioned loader --
    ribbon's `--segments`, north.py, junction.py::load and deviate.py's own
    duplicate loop are a NAMED RESIDUAL in `provenance`.  A document opened
    with a bare `yaml.safe_load` still carries the on-disk column, has been
    through NO declaration check, and is exactly what the three agents were
    holding when each hand-derived a 26% shortfall and read it as a quirk of
    their own closure.

    Asking that document for a lock MUST be a refusal, not a narrow answer.
    """
    import yaml                                            # noqa: PLC0415
    stmod = _stmod()
    with tempfile.TemporaryDirectory() as td:
        p = _zone_plan_file(Path(td))
        if "zones" not in _segment_keys_on_disk(p):
            raise AssertionError(
                "the fixture must be written in the ON-DISK name or this case "
                "proves the refusal fires on something nothing ever writes")
        raw = yaml.safe_load(p.read_text())
        carriage = _carriageway_zones(raw["segments"])
        try:
            got = stmod.segment_lock_zones(raw)
        except SystemExit as exc:
            return [Finding(
                check="a_lock_taken_from_an_unparsed_plan_is_refused",
                ok=False, message=str(exc).splitlines()[0][:200],
                numbers={"column_zones_offered": len(carriage),
                         "network_carriageway_sum": 702,
                         "network_batter_sum": 950})]
    return [Finding(
        check="a_lock_taken_from_an_unparsed_plan_is_refused", ok=True,
        message=f"a bare-parsed plan document still carrying the on-disk "
                f"column was ACCEPTED as a lock source and answered "
                f"{len(got)} zone(s) against a carriageway column of "
                f"{len(carriage)}",
        numbers={"lock_returned": len(got),
                 "column_zones_offered": len(carriage)})]


def _case_a_stale_write_set_on_disk_is_refused_on_read():
    """THE GUARD POINTED THE OTHER WAY, and the reason it has to be.

    `require_write_zones_declared` runs at EMIT, so it protects documents not
    yet written.  MEASURED 2026-09-22: roads/segments.yaml, the artefact all 39
    segments live in, CARRIES NO `zones_write` KEY AT ALL -- the guard exists,
    is correct, and has never run against the one document every tool opens.
    A declaration on disk goes stale exactly the way the carriageway column
    did: silently, and only visible to whoever re-derives.

    The state constructed here is the 702-against-950 reading written INTO the
    file as its declared write set.
    """
    stmod = _stmod()
    with tempfile.TemporaryDirectory() as td:
        segs = _zone_doc(None)["segments"]
        carriage = _carriageway_zones(segs)
        batter = _batter_zones(segs)
        if len(carriage) >= len(batter):
            raise AssertionError(
                f"this fixture exists to make the batter set WIDER than the "
                f"carriageway set; it measured carriageway {carriage} against "
                f"batter {batter}, so the case discriminates nothing")
        p = _zone_plan_file(Path(td), declared=carriage)
        try:
            stmod.load_segment_doc(p)
        except SystemExit as exc:
            return [Finding(
                check="a_stale_write_set_on_disk_is_refused_on_read",
                ok=False, message=str(exc).splitlines()[0][:200],
                numbers={"declared_carriageway": len(carriage),
                         "derived_batter": len(batter),
                         "network_carriageway_sum": 702,
                         "network_batter_sum": 950})]
    return [Finding(
        check="a_stale_write_set_on_disk_is_refused_on_read", ok=True,
        message=f"a plan file on disk declaring the CARRIAGEWAY set "
                f"({len(carriage)}) as its write set was READ without "
                f"refusal, against a batter set of {len(batter)}",
        numbers={"declared_carriageway": len(carriage),
                 "derived_batter": len(batter)})]


def _case_a_plan_read_through_the_loader_yields_the_batter_set():
    """THE ACCEPTING DIRECTION, and it carries the cutover.

    Past the loader the column is `zones_carriageway` and `zones` DOES NOT
    EXIST -- fail-closed, no alias.  An alias would be the ambiguity with extra
    steps: every consumer that under-declared would go on under-declaring and
    the rename would have bought a second name for the same trap.  And the
    lock the sanctioned accessor hands back must be the BATTER set, for a
    document that declares nothing, which is the state segments.yaml is in.
    """
    stmod = _stmod()
    with tempfile.TemporaryDirectory() as td:
        p = _zone_plan_file(Path(td))
        doc = stmod.load_segment_doc(p)
        seg = doc["segments"][0]
        if "zones" in seg:
            raise AssertionError(
                f"the carriageway column survived the sanctioned parse as "
                f"`zones` ({len(seg['zones'])} zones), so every consumer can "
                f"still take a lock off it")
        if "zones_carriageway" not in seg:
            raise AssertionError(
                "the loader dropped the carriageway column instead of "
                "renaming it; a boundary that loses data is not a boundary")
        carriage = {tuple(z) for z in _carriageway_zones(doc["segments"])}
        batter = {tuple(z) for z in _batter_zones(doc["segments"])}
        if not carriage < batter:
            raise AssertionError(
                f"this fixture exists to make the batter set WIDER than the "
                f"carriageway set; it measured carriageway {sorted(carriage)} "
                f"against batter {sorted(batter)}, so the case "
                f"discriminates nothing")
        try:
            lock = {tuple(z) for z in stmod.segment_lock_zones(doc)}
        except SystemExit as exc:
            raise AssertionError(
                f"a plan read through the sanctioned loader must yield a "
                f"lock: {exc}") from exc
        if lock != batter:
            raise AssertionError(
                f"the sanctioned lock accessor returned {sorted(lock)} where "
                f"ribbon writes {sorted(batter)} -- {len(batter - lock)} "
                f"missing. That is the carriageway column handed out as a "
                f"lock: 702 against 950 network-wide, 248 short on 37 of 39 "
                f"segments, which cost one real overlap at zone (-5,4)")
    return []


def _neuter_the_column_survives_the_parse():
    """RESTORE THE DEFECT VERBATIM: the loader hands `zones` straight back.

    That is every reader in `roads/` as it stood before this boundary, and it
    is the state `roads/segments.yaml` is still in ON DISK -- so this neuter is
    not invented, it is the file.
    """
    # THE MODULE THE LOADER ACTUALLY CALLS, not `_mods()`'s copy.
    # `roads/network.py` is loaded BY PATH in two places under two private
    # names -- `_emission_network` here and `_structures_network` inside
    # `structures._netmod` -- because `tools/jumpstart/network/` is a package
    # that shadows it.  Patching the wrong one is a neuter that changes
    # nothing and a case that reports itself proven while proving nothing,
    # which is the exact failure mode this field exists to prevent.
    netmod = _stmod()._netmod()
    real = netmod.carriageway_from_disk
    netmod.carriageway_from_disk = lambda doc: doc

    def undo():
        netmod.carriageway_from_disk = real
    return undo


def _neuter_the_write_set_is_checked_only_at_emission():
    """RESTORE THE DEFECT: the refusal reaches the OUTPUT and not the INPUT.

    That is the literal shipped state -- `require_write_zones_declared` called
    from `network.emit` and from nowhere else -- and it is why
    roads/segments.yaml sat unguarded with no `zones_write` at all while the
    gate reported the class closed.  Patched on the module `load_segment_doc`
    ACTUALLY calls, never `_mods()`'s copy: roads/network.py is loaded by path
    under two private names and patching the wrong one is a neuter that
    changes nothing.
    """
    netmod = _stmod()._netmod()
    real = netmod.require_write_zones_declared
    netmod.require_write_zones_declared = lambda name, doc, **kw: None

    def undo():
        netmod.require_write_zones_declared = real
    return undo


def _neuter_the_lock_is_the_carriageway_column():
    """RESTORE THE DEFECT AT THE POINT OF USE: the lock IS the column.

    702 against 950.  This is what three agents did by hand on three segments
    (55->75, 60->82, 6->10), each reading it as a quirk of their own closure.
    """
    stmod = _stmod()
    real = stmod.segment_lock_zones

    def off_the_column(doc):
        net = stmod._netmod()
        out: set = set()
        for s in net._each_segment(doc):
            col = s.get(net.ZONES_COLUMN_IN_MEMORY) or s.get(
                net.ZONES_COLUMN_ON_DISK) or []
            out.update((int(z[0]), int(z[1])) for z in col)
        return sorted(out)

    stmod.segment_lock_zones = off_the_column

    def undo():
        stmod.segment_lock_zones = real
    return undo


# ---------------------------------------------------------------------------
# THE ZONE SET A WRITE NAMES vs THE ZONE SET A CLIENT HOLDS
# ---------------------------------------------------------------------------
# The three cases above ask whether a plan DECLARES the zone set it will write.
# This pair asks the other half of the same question: whether that declared set
# is a set the write can actually HAVE.  A `terrain_write` is a delete of the
# zone's `_TerrainCompiler` followed by a spawn, and a peer inside
# `ZoneSystem.m_activeArea` owns those ZDOs -- so a write into an occupied zone
# loses the race, and the postcondition still finds a compiler there because it
# finds the CLIENT'S.  That is a record of something that did not happen and
# cannot be detected afterwards.
#
# THE STATE IS THIS WORLD'S, MEASURED 2026-09-22 off `players` on the live
# server, and it is TRANSCRIBED here rather than borrowed: the fixture must
# construct the state it tests.
KNOWN_OCCUPIED_ROSTER = (
    "Online 1\n"
    "Sssf Steam ID:76561197987967077 Position: (-1032.52 37.55 1641.07) "
    "Zone: (-16 26) Player ID:-490895177 HP:0/25 Public position: False "
    "Platform name: neuralyze")
# The one zone R4-vestvik-harbour's B cut-only disposal names, 8 vertices,
# 0.5156 m3.
R4B_WRITE = {"params": {"entries": [{"zone": [-16, 26]}]}}


def _livemod():
    import sys                                             # noqa: PLC0415
    from pathlib import Path                               # noqa: PLC0415
    here = Path(__file__).resolve().parents[2]
    for p in (str(here / "ledger"), str(here / "terraform"), str(here)):
        if p not in sys.path:
            sys.path.insert(0, p)
    import live as livemod                                 # noqa: PLC0415
    return livemod


def _case_a_write_into_an_occupied_zone_is_refused():
    """THE KNOWN-BAD STATE, VERBATIM: one peer, one zone, and that zone is the
    only zone in the write."""
    L = _livemod()
    r = L.loaded_zone_check(R4B_WRITE, KNOWN_OCCUPIED_ROSTER)
    assert r["verdict"] == "REFUSED", r
    assert r["intersection"] == [[-16, 26]], r["intersection"]
    assert r["zones_named"] == 1 and r["players_whose_position_was_read"] == 1
    # AND THE RING, not just the centre zone: `m_activeArea` is 1, so the peer
    # holds 9 zones and a write to the neighbour is refused for the same
    # reason.  A guard that only knew the peer's own zone would wave this
    # through, and the neighbour is where a road corridor actually runs.
    n = L.loaded_zone_check({"params": {"entries": [{"zone": [-15, 26]}]}},
                            KNOWN_OCCUPIED_ROSTER)
    assert n["verdict"] == "REFUSED" and n["zones_held_by_a_client"] == 9, n
    # AND AN UNREADABLE ROSTER IS NOT AN EMPTY ONE.
    u = L.loaded_zone_check(R4B_WRITE, "Online 2\nSssf Position: "
                                       "(-1032.52 37.55 1641.07) "
                                       "Zone: (-16 26)")
    assert u["verdict"] == "REFUSED" and "UNREADABLE" in u["why"], u
    return [Finding(
        check="terrain_write_into_a_client_held_zone", ok=False,
        message=(f"a terrain_write naming {r['zones_named']} zone(s) "
                 f"{r['zones_named_by_the_write']} is REFUSED: "
                 f"{r['intersecting_zones']} of them {r['intersection']} are "
                 f"held by {r['occupants']}. {r['population']}"),
        where=(-1032.52, 1641.07),
        measured=float(r["intersecting_zones"]), limit=0.0,
        numbers=dict(intersection=r["intersection"],
                     zones_named=r["zones_named"],
                     zones_held_by_a_client=r["zones_held_by_a_client"],
                     active_area_zones=r["active_area_zones"],
                     players_read=r["players_whose_position_was_read"]))]


def _case_a_write_into_a_quiet_zone_is_accepted():
    """BOTH SIGNS.  A guard that refuses every terrain write stops the world
    being built, and the first thing a blocked agent does is find the path
    that does not go through it."""
    L = _livemod()
    quiet = L.loaded_zone_check(R4B_WRITE, "Online 0")
    assert quiet["verdict"] == "CLEAR" and quiet["zones_held_by_a_client"] == 0
    # A peer who IS online but three zones away does not hold this one.
    far = L.loaded_zone_check({"params": {"entries": [{"zone": [-13, 26]}]}},
                              KNOWN_OCCUPIED_ROSTER)
    assert far["verdict"] == "CLEAR", far
    # The harbour and boathouse sets published this session are disjoint from
    # the occupied zone and must stay writable while that peer stands there.
    for z in ([-5, -1], [26, 8]):
        o = L.loaded_zone_check({"params": {"entries": [{"zone": z}]}},
                                KNOWN_OCCUPIED_ROSTER)
        assert o["verdict"] == "CLEAR", (z, o)
    return []


def _neuter_no_loaded_zone_guard():
    """RESTORE THE DEFECT: the write path as it stood all session -- `players`
    read into `meta` as PROVENANCE and never consulted as a GUARD."""
    L = _livemod()
    real = L.loaded_zone_check

    def provenance_only(rec, players_text, active_area=L.ACTIVE_AREA_ZONES):
        out = real(rec, players_text, active_area)
        out["verdict"] = "CLEAR"
        out["why"] = "players recorded into meta, never judged"
        return out

    L.loaded_zone_check = provenance_only

    def undo():
        L.loaded_zone_check = real
    return undo


def _neuter_occupancy_taken_as_anybody_online():
    """RESTORE THE FAILURE MODE OF THE REJECTED ALTERNATIVE, which is the one
    the ACCEPTING arm exists to catch: judge occupancy by "is anybody online"
    rather than by ZONE INTERSECTION.

    That guard refuses correctly on the known-bad state and ALSO refuses every
    honest write on a server with one peer standing 3 km away -- which is a
    gate nobody can make green, and the first thing a blocked agent does is
    find the path that does not go through it.  A neuter aimed at the absence
    of the rule could never show this, because the rule IS present here; it is
    the discriminator that is wrong.
    """
    L = _livemod()
    real = L.loaded_zone_check

    def anybody_online(rec, players_text, active_area=L.ACTIVE_AREA_ZONES):
        out = real(rec, players_text, active_area)
        if out["players_whose_position_was_read"]:
            out["verdict"] = "REFUSED"
            out["why"] = "somebody is online, somewhere"
        return out

    L.loaded_zone_check = anybody_online

    def undo():
        L.loaded_zone_check = real
    return undo


# ---------------------------------------------------------------------------
# A UNION PERFORMED AND NOT DECLARED
# ---------------------------------------------------------------------------
# A zone holds exactly ONE `_TerrainCompiler` and a terrain write deletes it
# before it spawns, so a write into an already-written zone must carry the
# zone's prior claims forward AND NAME THEM: `writer._zone_clobber_check`
# refuses unless `entries[].merged_from` lists the prior seqs.
#
# `applied.restore_into` PERFORMED that union all session -- walking the
# per-sample `owner`, reading each prior's blob, and re-checking every
# restored sample against the live applied surface -- and returned
# `samples_inherited`, `samples_checked_against_applied` and `merge_policy`
# and NEVER SAID WHICH SEQS.  The function knew the answer, the return value
# dropped it, and the guard then refused the caller for not naming what the
# function had just read.
#
# MEASURED 2026-09-22, two correct writes refused within an hour:
#   seq 5393  RegradeSteps   zone (0,0)     12 priors, 1,494 inherited
#             SettleVestvik  zone (-14,30)   5 priors, 1,061 inherited
#
# AND THE INHERITANCE ANSWER IS THE WRONG POPULATION, which is what this
# case is really for.  `zone()` unions priors in FILE ORDER so `owner[i]` is
# the LATEST claim on each sample; a prior wholly superseded by a later one
# owns nothing and never appears in the walk -- while remaining a record
# whose compiler the op destroys.  On all four zones this session's refusals
# named, THE OWNER WALK SEES EXACTLY ONE SEQ: 1 of 5, 1 of 12, 1 of 33,
# 1 of 14.
ZONE_0_0_PRIORS = [181, 196, 202, 967, 1002, 1709, 1722, 4638, 4648, 5091,
                   5101, 5332]
# The one of the twelve the owner walk still sees, MEASURED on this chain.
ZONE_0_0_STILL_OWNS = {5332: 1499}


def _clobber_check(rec, prior):
    """`writer._zone_clobber_check`, the SHIPPING guard, called unbound.

    It reads only `rec` and `prior`, so it needs no ledger -- which is what
    lets this case construct its own state instead of borrowing one.
    """
    import sys                                             # noqa: PLC0415
    from pathlib import Path                               # noqa: PLC0415
    here = Path(__file__).resolve().parents[2]
    for p in (str(here / "ledger"), str(here / "roads"),
              str(here / "terraform"), str(here)):
        if p not in sys.path:
            sys.path.insert(0, p)
    import writer as writermod                             # noqa: PLC0415
    return writermod.Ledger._zone_clobber_check(None, rec, prior)


def _appliedmod():
    _clobber_check({"params": {"entries": []}}, [])         # path set-up
    import applied as appliedmod                           # noqa: PLC0415
    return appliedmod


def _zone_state(zone, priors, declared=None):
    """A chain that has written `zone` once per prior seq, and one more write
    into it declaring `declared`."""
    prior = [{"op": "terrain_write", "seq": s,
              "params": {"entries": [{"zone": list(zone)}]}}
             for s in priors]
    entry = {"zone": list(zone), "merge_policy": "union"}
    if declared is not None:
        entry["merged_from"] = list(declared)
    return {"op": "terrain_write", "seq": max(priors) + 1,
            "params": {"entries": [entry]}}, prior


def _case_a_union_performed_and_not_declared_is_refused():
    """THE KNOWN-BAD STATE AND THE NEAR-MISS, both on this chain's own zone.

    Arm 1 is the refusal two agents hit: declare nothing, get refused.
    Arm 2 is the one that matters: declare THE INHERITANCE ANSWER -- the
    priors whose bytes are still on the compiler -- and get refused anyway,
    for the eleven the walk cannot see.  That is the design the supervisor
    and both writers proposed first.
    """
    A = _appliedmod()
    zone = (0, 0)

    # (1) PERFORMED AND UNDECLARED.
    rec, prior = _zone_state(zone, ZONE_0_0_PRIORS)
    bad = _clobber_check(rec, prior)
    assert bad, "a union that names no prior must be refused"

    # (2) THE INHERITANCE-ONLY DECLARATION, which is the near-miss.
    walk = sorted(ZONE_0_0_STILL_OWNS)
    rec2, _p = _zone_state(zone, ZONE_0_0_PRIORS, declared=walk)
    bad2 = _clobber_check(rec2, prior)
    assert bad2, (
        "declaring only the priors whose samples are still on the compiler "
        "must STILL be refused: the guard asks what this op DELETES, not "
        "what it carries forward")
    missing = [s for s in ZONE_0_0_PRIORS if s not in walk]
    assert len(missing) == 11, missing

    # (3) THE DECLARATION `merged_declaration` PRODUCES CLOSES IT.
    d = A.merged_declaration(ZONE_0_0_PRIORS, ZONE_0_0_STILL_OWNS)
    assert d["merged_from"] == sorted(ZONE_0_0_PRIORS), d["merged_from"]
    assert d["priors_with_no_sample_on_the_compiler"] == missing, d
    rec3, _p = _zone_state(zone, ZONE_0_0_PRIORS, declared=d["merged_from"])
    assert not _clobber_check(rec3, prior), _clobber_check(rec3, prior)

    # (4) AND THE INHERITANCE ANSWER IS STILL PUBLISHED, because a set
    #     declared without it asserts claims this write may carry none of.
    #     An undeclared union is REFUSED; a MIS-declared union APPENDS.
    assert d["merged_sample_counts"][5332] == 1499
    assert all(d["merged_sample_counts"][s] == 0 for s in missing)

    return [Finding(
        check="zone_union_performed_and_not_declared", ok=False,
        message=(f"a terrain_write into zone {list(zone)} that inherits "
                 f"{ZONE_0_0_STILL_OWNS[5332]} sample(s) from "
                 f"{len(ZONE_0_0_PRIORS)} prior claim(s) and declares "
                 f"merged_from=[] is REFUSED; declaring only the "
                 f"{len(walk)} prior(s) whose bytes are still on the "
                 f"compiler is ALSO refused, for the {len(missing)} the "
                 f"owner walk cannot see. {d['merged_from_population']}"),
        measured=float(len(missing)), limit=0.0,
        numbers=dict(priors_in_zone=len(ZONE_0_0_PRIORS),
                     priors_the_owner_walk_sees=len(walk),
                     priors_left_unnamed_by_the_owner_walk=missing,
                     samples_inherited=ZONE_0_0_STILL_OWNS[5332]))]


def _case_a_virgin_zone_declares_nothing_and_is_accepted():
    """BOTH SIGNS.  A zone nobody has written has no prior to name, and a
    guard that demanded one there would refuse the first write into every
    zone on every future world."""
    A = _appliedmod()
    d = A.merged_declaration([], {})
    assert d["merged_from"] == [] and d["merged_sample_counts"] == {}
    rec = {"op": "terrain_write", "seq": 9001,
           "params": {"entries": [{"zone": [77, 77], "merge_policy": "union"}]}}
    assert not _clobber_check(rec, []), _clobber_check(rec, [])
    # AND A ZONE **WITH** PRIORS, ALL OF THEM DECLARED, STAYS ACCEPTED -- so
    # the accepting arm is not satisfied by emptiness alone.  The declaration
    # is DERIVED from `merged_declaration` rather than written out, which is
    # what makes this arm load-bearing: with the inheritance-only defect
    # restored it names 1 of (-14,30)'s 5 priors, the guard refuses, and this
    # case's own assertion fires.
    zone = (-14, 30)
    priors = [495, 516, 3335, 3340, 5372]
    honest = A.merged_declaration(priors, {5372: 1064})
    rec2, prior = _zone_state(zone, priors, declared=honest["merged_from"])
    bad = _clobber_check(rec2, prior)
    assert not bad, (
        f"the declaration this tree PRODUCES must be accepted by the guard "
        f"it is produced for; it named {honest['merged_from']} of "
        f"{priors} and was refused: {bad}")
    return []


def _neuter_merged_from_is_the_inheritance_answer():
    """RESTORE THE DEFECT THAT WAS RATIFIED AND WITHDRAWN: `merged_from` as
    the priors whose bytes are still on the compiler.

    Not the ABSENCE of the key -- the key present and holding the other
    population, which is what a supervisor and two writers each proposed
    before one of them measured it.
    """
    A = _appliedmod()
    real = A.merged_declaration

    def inheritance_only(priors_in_zone, owned_counts):
        out = real(priors_in_zone, owned_counts)
        out["merged_from"] = sorted(s for s in out["merged_from"]
                                    if out["merged_sample_counts"][s])
        return out

    A.merged_declaration = inheritance_only

    def undo():
        A.merged_declaration = real
    return undo


register(Section(
    name="emission",
    measures="that every seed-scoped artefact this build WRITES is a truthful "
             "description of what this run did: it declares this run's seed, "
             "it cites only artefacts in that scope, every endpoint coordinate "
             "it claims was copied from an artefact is actually in that "
             "artefact for THIS seed, and it declares the zone set it will "
             "actually write rather than the narrower one it drives on",
    instrument="emitscope.audit and network.require_write_zones_declared, both "
               "run at the point of emission on the PARSED document before the "
               "bytes land (roads/network.py::emit, settlements/plan.py::main), "
               "and re-run here over the emitted files on disk. The zone set is "
               "derived through ribbon's OWN segment_zones at BATTER_MAX_M, "
               "never a second definition of which zones a segment touches.",
    population="the 6 seed-scoped artefacts this build writes -- "
               "roads/segments.yaml, roads/crossings.yaml, "
               "roads/geography.<seed>.yaml, roads/north_itinerary.<seed>.yaml, "
               "settlements/sites.yaml, settlements/plan.json -- of which 4 "
               "are guarded at emission and 2 are unguarded-and-owned; plus "
               "the canonical segments file's endpoint provenance and "
               "complete write-zone set (including north roads)",
    run=run,
    cases=[
        Case("a_correct_header_is_not_enough", True,
             _case_a_correct_header_is_not_enough,
             prior_state="MEASURED 2026-09-22: a Fresh42 run stamped "
                         "`seed: Fresh42 / world: Fresh42`, reported POI "
                         "violations 0, exited 0, and 12,849 of 21,938 m of "
                         "its road network -- 59% -- terminated at Ulfsland "
                         "installation coordinates, 8 of 8 verbatim to the "
                         "decimal.",
             neuter=_neuter_header_only,
             neuters="an output guard that checks the stamp and stops, which "
                     "is what every guard landed before this one was"),
        Case("a_citation_naming_another_seeds_field_is_refused", True,
             _case_a_citation_naming_another_seeds_field_is_refused,
             prior_state="MEASURED 2026-09-22: crossings.yaml stamped "
                         "`seed: Fresh42` carrying `measured_by ... over "
                         "/tmp/roads/h1m.bin` -- Ulfsland's field named twice "
                         "inside a Fresh42 artefact.",
             neuter=_neuter_header_only,
             neuters="the same header-only guard"),
        Case("an_output_that_declares_no_seed_is_refused", True,
             _case_an_output_that_declares_no_seed_is_refused,
             prior_state="`require_scope` already refuses an INPUT that "
                         "declares nothing; nothing refused an output that "
                         "did.",
             neuter=_neuter_absent_is_permission,
             neuters="absent-is-permission on every rung"),
        Case("an_output_that_cites_nothing_is_refused", True,
             _case_an_output_that_cites_nothing_is_refused,
             prior_state="A single-population output check can still be "
                         "vacuous: a provenance check fires only on artefacts "
                         "that CARRY a provenance string.",
             neuter=_neuter_absent_is_permission,
             neuters="absent-is-permission on every rung"),
        Case("a_coordinate_with_no_provenance_is_refused", True,
             _case_a_coordinate_with_no_provenance_is_refused,
             neuter=_neuter_absent_is_permission,
             neuters="absent-is-permission on every rung"),
        Case("a_verbatim_claim_is_checked_by_looking", True,
             _case_a_verbatim_claim_is_checked_by_looking,
             prior_state="The 8 installation nodes' provenance read "
                         "'MEASURED, placements.yaml 8d0eab4' and no reader "
                         "ever opened placements.yaml. THE DECLARATION "
                         "EXISTED. THE GUARD EXISTED. THE READER DID NOT.",
             neuter=_neuter_header_only,
             neuters="an output guard that checks the stamp and stops"),
        Case("a_clean_fresh_seed_output_is_accepted", False,
             _case_a_clean_fresh_seed_output_is_accepted,
             prior_state="Six refusing cases prove a check FIRES; nothing "
                         "proves it can PASS.",
             neuter=_neuter_transcribed_citation_path,
             neuters="a citation path frozen to the fixture world instead of "
                     "resolved for the world being built"),
        Case("a_transformed_coordinate_is_named_not_passed", False,
             _case_a_transformed_coordinate_is_named_not_passed,
             prior_state="MEASURED: wtspawn stands at (110,-104) while "
                         "sites.yaml says (114,-108) -- the road node is the "
                         "site's approach, and demanding equality there "
                         "refuses a correct artefact.",
             neuter=_neuter_equality_over_every_record,
             neuters="the byte-identity rule applied to every record rather "
                     "than to the declared-verbatim ones"),
        Case("declared_endpoints_are_attributed_and_derived_ones_are_named",
             False,
             _case_declared_endpoints_are_attributed_and_derived_ones_are_named,
             prior_state="The fixture world legitimately carries its own "
                         "coordinates; a check that cannot tell this world's "
                         "number from another world's is not a measurement.",
             neuter=_neuter_equality_over_every_record,
             neuters="the same over-wide rule, which reddens the fixture "
                     "world on its own correct artefacts"),
        Case("every_unguarded_output_names_an_owner", False,
             _case_every_unguarded_output_names_an_owner,
             prior_state="FreshSeedDrive recorded segments.yaml as UNGUARDED "
                         "with owner=UNOWNED rather than keep a count green; "
                         "that is the standard this row enforces.",
             neuter=_neuter_an_unowned_row,
             neuters="a row claiming a guard whose module never calls the "
                     "refusal"),
        Case("the_carriageway_column_is_refused_as_a_write_set", True,
             _case_the_carriageway_column_is_refused_as_a_write_set,
             prior_state="MEASURED 2026-09-22 over all 39 segments at "
                         "BATTER_MAX_M 10.5: the record's carriageway column "
                         "holds 702 zones where ribbon writes 950. 37 of 39 "
                         "segments short, 248 zones missing -- a 26% "
                         "under-declaration. Three agents hand-derived it on "
                         "three segments (55->75, 60->82, 6->10) and each read "
                         "it as a quirk of their own closure. It cost one real "
                         "overlap at zone (-5,4), found only because an agent "
                         "re-derived rather than trusting the column.",
             neuter=_neuter_the_carriageway_column,
             neuters="the write set taken at carriageway width, which is the "
                     "narrowing every consumer read off the record column"),
        Case("a_plan_that_declares_no_write_set_is_refused", True,
             _case_a_plan_that_declares_no_write_set_is_refused,
             prior_state="No plan document declared a write set at all until "
                         "this block existed, so every consumer took the "
                         "carriageway column by default.",
             neuter=_neuter_absence_is_permitted,
             neuters="a document with no write set waved through, which is "
                     "absent-is-permission on this rung"),
        Case("an_honest_zone_declaration_is_accepted", False,
             _case_an_honest_zone_declaration_is_accepted,
             prior_state="A refusal that fires on the honest document refuses "
                         "every plan this project will ever emit, and the only "
                         "symptom is a gate nobody can make green -- which is "
                         "how a check gets weakened into a tolerance rather "
                         "than fixed. An accepting arm declaring an EMPTY set "
                         "would prove only that a vacuous claim is satisfied.",
             neuter=_neuter_an_overwide_reach,
             neuters="a checker whose reach is WIDER than the writer's, which "
                     "refuses every honest declaration forever"),
        Case("a_lock_taken_from_an_unparsed_plan_is_refused", True,
             _case_a_lock_taken_from_an_unparsed_plan_is_refused,
             prior_state="MEASURED 2026-09-22: TWELVE modules open the two "
                         "plan files and only the structures.segment_map path "
                         "goes through the sanctioned loader -- ribbon's "
                         "--segments, north.py, junction.py::load and "
                         "deviate.py's duplicate loop are a NAMED RESIDUAL. A "
                         "bare yaml.safe_load still hands back the on-disk "
                         "carriageway column, checked by nothing, and that is "
                         "what three agents were holding when each "
                         "hand-derived a 26% shortfall on one segment "
                         "(55->75, 60->82, 6->10) and read it as a quirk of "
                         "their own closure. It cost one real overlap at zone "
                         "(-5,4).",
             neuter=_neuter_the_lock_is_the_carriageway_column,
             neuters="the lock taken silently off the carriageway column, "
                     "which is the 702-against-950 reading itself at the "
                     "point of use"),
        Case("a_stale_write_set_on_disk_is_refused_on_read", True,
             _case_a_stale_write_set_on_disk_is_refused_on_read,
             prior_state="MEASURED 2026-09-22: "
                         "`require_write_zones_declared` was called from "
                         "network.emit and NOWHERE ELSE, so it protected "
                         "documents not yet written and never ran against the "
                         "one every tool opens -- roads/segments.yaml carries "
                         "NO `zones_write` key at all, and neither does "
                         "roads/north_segments.yaml. Over all 39 segments at "
                         "BATTER_MAX_M 10.5, PER-SEGMENT SUMS: carriageway "
                         "702, batter reach 950, 248 missing (26.1%), 37 of "
                         "39 short; on the network-wide UNION, 489 against "
                         "627, 138 missing.",
             neuter=_neuter_the_write_set_is_checked_only_at_emission,
             neuters="the refusal reaching the OUTPUT and not the INPUT, "
                     "which is the literal shipped state and is why the gate "
                     "reported this class closed over an unguarded artefact"),
        Case("a_plan_read_through_the_loader_yields_the_batter_set", False,
             _case_a_plan_read_through_the_loader_yields_the_batter_set,
             prior_state="THE ON-DISK RENAME WAS REFUSED WITH ITS NUMBER: the "
                         "carriageway column lives in 3 artefacts, 62 segment "
                         "records, 1,129 zone entries -- segments.yaml "
                         "(git-TRACKED, 702), preflight.json (git-TRACKED, "
                         "191), north_segments.yaml (untracked, 236) -- so a "
                         "blanket rename is a DATA MIGRATION and a forked "
                         "on-disk format is worse than an ambiguous name. The "
                         "rename therefore lands at the READ boundary, and a "
                         "boundary that refused every honest plan, or that "
                         "kept `zones` as an ALIAS beside the new name, would "
                         "buy a second name for the same trap. This arm holds "
                         "both: the column is gone AND the lock is the batter "
                         "set.",
             neuter=_neuter_the_column_survives_the_parse,
             neuters="the loader handing `zones` straight back -- the alias "
                     "outcome, which is literally the file on disk and which "
                     "makes the sanctioned accessor refuse"),
        Case("a_terrain_write_into_a_client_held_zone_is_refused", True,
             _case_a_write_into_an_occupied_zone_is_refused,
             prior_state="MEASURED 2026-09-22 on the live server: `players` "
                         "returned one peer, Sssf at "
                         "(-1032.52, 37.55, 1641.07), zone (-16 26), HP 0/25 "
                         "-- standing on R4-vestvik-harbour's B approach, "
                         "which is the ONLY zone the R4 B cut-only disposal "
                         "names. Every terrain emitter in the tree already "
                         "read `players` into its own meta and NOT ONE of "
                         "them consulted the reading: it was provenance, "
                         "never a guard. A terrain_write deletes the zone's "
                         "_TerrainCompiler before it spawns, the peer inside "
                         "m_activeArea owns those ZDOs and pushes its copy "
                         "back, and `verify_expect` then finds a compiler in "
                         "the zone and calls the write applied -- because it "
                         "finds the CLIENT'S.",
             neuter=_neuter_no_loaded_zone_guard,
             neuters="the write path as it stood all session: `players` "
                     "recorded into meta and never judged"),
        Case("a_terrain_write_into_a_quiet_zone_is_accepted", False,
             _case_a_write_into_a_quiet_zone_is_accepted,
             prior_state="A loaded-zone guard that refused every write would "
                         "stop this world being built at all, and the first "
                         "thing a blocked agent does is find the path that "
                         "does not go through the guard. The accepting arm "
                         "names the two harbour zone sets published this "
                         "session, (-5,-1) and (26,8), which MUST stay "
                         "writable while that peer stands in (-16,26).",
             neuter=_neuter_occupancy_taken_as_anybody_online,
             neuters="occupancy judged as 'is anybody online' instead of by "
                     "zone intersection -- the rejected alternative, which "
                     "refuses the known-bad state correctly AND refuses every "
                     "honest write on a server with one peer 3 km away"),
        Case("a_zone_union_performed_and_not_declared_is_refused", True,
             _case_a_union_performed_and_not_declared_is_refused,
             prior_state="MEASURED 2026-09-22: `applied.restore_into` "
                         "performed the zone union on every call -- walking "
                         "the per-sample owner, reading each prior's blob, "
                         "re-checking every restored sample against the live "
                         "applied surface -- and returned samples_inherited, "
                         "samples_checked_against_applied and merge_policy "
                         "and NEVER SAID WHICH SEQS. Two correct writes were "
                         "refused within one hour for not naming what the "
                         "function had just read: RegradeSteps seq 5393 zone "
                         "(0,0), 12 priors over 1,494 inherited, and "
                         "SettleVestvik zone (-14,30), 5 priors over 1,061. "
                         "merge_policy had ALREADY been fixed in that same "
                         "return value -- prose to enum, after wt-south seq "
                         "5358 failed on exactly its three zones with a "
                         "prior claim -- so one half of one guard's demand "
                         "was learned and the other half, five lines up in "
                         "the same refusal message, was not.",
             neuter=_neuter_merged_from_is_the_inheritance_answer,
             neuters="merged_from as THE INHERITANCE ANSWER -- the priors "
                     "whose bytes are still on the compiler -- which is the "
                     "design two agents and the supervisor each proposed "
                     "first, and which the owner walk reduces to exactly ONE "
                     "seq in all four zones this session's refusals named "
                     "(1 of 5, 1 of 12, 1 of 33, 1 of 14)"),
        Case("a_virgin_zone_declares_nothing_and_is_accepted", False,
             _case_a_virgin_zone_declares_nothing_and_is_accepted,
             prior_state="A guard demanding a prior where none exists would "
                         "refuse the first write into every zone on every "
                         "future world. The accepting arm also names a zone "
                         "WITH priors, all of them declared, so it is not "
                         "satisfied by emptiness alone.",
             neuter=_neuter_merged_from_is_the_inheritance_answer,
             neuters="the same inheritance-only declaration, which drops 4 "
                     "of (-14,30)'s 5 priors and makes the honest "
                     "declaration refuse"),
    ],
))
