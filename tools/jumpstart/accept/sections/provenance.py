"""PROVENANCE: every seed-scoped input the planner reads is THIS seed's.

WHAT THIS SECTION EXISTS TO CATCH, and how it was found.  On 2026-09-21 a
throwaway seed was driven through the planner for the first time -- the claim
every fix that week had been defended with, executed.  `VH_SEED=Fresh42
roads/network.py plan` ran to completion and emitted a plan document.  It did
that while reading THREE seed-scoped artefacts, of which only one was checked:

  roads/field.py::require_seed      REFUSED a foreign height field.  Correct.
  roads/precensus.py::require       REFUSED a foreign census.  Correct.
  roads/poi.py::load                ACCEPTED 12,301 locations out of
                                    /tmp/settle/loc3/f6fe167f4fcd.json, whose
                                    own header reads
                                    `"seedNameFromWorld": "Pirate68"`.
  roads/spec.py::load_solved_sites  ACCEPTED all 16 sites at Ulfsland's
                                    coordinates out of settlements/sites.yaml,
                                    whose own header reads `world: Ulfsland`,
                                    `seed: Pirate68`, `seed_hash: 147627509`.

Both unguarded artefacts DECLARE THEIR OWN SCOPE IN THEIR OWN HEADER and
neither declaration was read.  That is the defect class: not a missing
measurement, an unread one.  And it is invisible from inside the router, which
asked for the keep-outs and got 12,301 of them.

WHY IT IS A SECTION AND NOT A UNIT TEST.  A plan is the JOIN of its inputs, so
one guarded artefact out of three is worth the same as none: the road is still
fitted to another planet, just via a different field.  The property is
therefore about the SET of inputs, which is the shape of a gate section and not
of any one module's selftest.  `spec.py`'s pin layer is the worked example of
getting this exactly half right -- `PIN_SCOPE` correctly declined to apply a
single Ulfsland pin on Fresh42, on top of an UNPINNED base that handed over the
whole geography.  A scoped pin over an unscoped base is not scoped.

POPULATION.  Every artefact the planner reads whose content is a function of
the seed.  Named one by one below rather than discovered by a predicate,
because "the predicate happened to miss it" is how the second one survived the
first one being fixed.

THIS SECTION NEEDS NO WORLD, NO SAVE AND NO SERVER.  That is deliberate and it
is the property being asserted: a check that cannot run until a world exists
cannot tell you whether a world should be built.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from .. import termination as T
from ..registry import Case, Finding, Section, register

HERE = Path(__file__).resolve().parent
JUMPSTART = HERE.parent.parent


# WHY EACH REFUSAL THIS SECTION CAN EMIT CAN OR CANNOT BE CLOSED.
#
# A CORPUS DECLARING THE WRONG WORLD IS NOT CONTESTED GROUND.  Every class
# this gate had before tonight was about a TERRAIN CUT - who owns the
# ground, who else wrote it, which way it would have to move - and not one
# of this section's refusals is about ground at all.  They are about SCOPE:
# whether the artefact this run read was measured on the seed this run
# plans.  The remedy is never a cut and never a demolition; it is to RUN
# THE PRODUCER AGAIN, or to change what the producer writes.
#
#   MEASURED ON ANOTHER SEED   the producer exists in this tree and has not
#     run for this seed.  1E, FACTORY WORK, and the row names the reader
#     and the producer it is waiting on.
#   NO SCOPE DECLARATION AT ALL  re-running the producer writes the same
#     header-less file again; the remedy is a change to the producer's
#     source, which no tool in this tree performs.  1C.
#
# AND THE SPLIT IS TAKEN OFF THE ROW'S OWN NUMBERS, never off the message.
# `_check_one` publishes `path` on the arm where the file could not be READ
# and `want_seed` on the arm where it was read and DECLARES NOTHING; those
# are two different remedies under one check name, so the declaration is a
# callable of the numbers rather than one token that would be wrong for
# half the population.
def _scope_family(nums):
    if nums.get("path"):
        return ("PRODUCER_NOT_RUN",
                f"re-run {nums.get('reader') or 'this artefact'}'s producer "
                f"for this seed; the file on disk could not be read at all, "
                f"so nothing can prove which world it describes")
    return ("SOURCE_CHANGE",
            "the artefact was read and carries NO seed declaration, so "
            "re-running its producer writes the same unscoped file again; "
            "the remedy is a scope header in the producer's own source")


TERMINATION_FAMILIES = {
    "artefact_declares_its_scope": _scope_family,
    "artefact_scope_matches_this_run": lambda nums: (
        "PRODUCER_NOT_RUN",
        f"re-run {nums.get('reader') or 'this artefact'}'s producer for "
        f"seed {nums.get('want_seed')!r}"
        + (f" (it declares {nums.get('found_seed')!r})"
           if nums.get("found_seed") else "")
        + (f" and world {nums.get('want_world')!r}"
           if nums.get("want_world") else "")),
}


def _mods():
    """`worldseed`, `poi` and the sites path, with the import order that works.

    APPEND the jumpstart root, never insert it: `tools/jumpstart/network/` is a
    package that shadows `roads/network.py` for every later importer if it gets
    in front of `roads/`.  Same trap `roads/spec.py` documents at its own
    import, same remedy.
    """
    roads = JUMPSTART / "roads"
    if str(roads) not in sys.path:
        sys.path.insert(0, str(roads))
    if str(JUMPSTART) not in sys.path:
        sys.path.append(str(JUMPSTART))
    import poi as poimod                                   # noqa: PLC0415
    import worldseed                                       # noqa: PLC0415
    return worldseed, poimod


# ---------------------------------------------------------------------------
# THE POPULATION: one entry per seed-scoped artefact, with the reader that is
# REQUIRED to refuse it and the function that extracts what it says about
# ITSELF.  Adding an artefact here without a reader that refuses is what the
# `declares_a_reader` check below exists to reject.
# ---------------------------------------------------------------------------
def _sites_scope(path: Path):
    import yaml                                            # noqa: PLC0415
    doc = yaml.safe_load(path.read_text())
    return doc.get("seed"), doc.get("world")


def _dump_scope(path: Path):
    _ws, poimod = _mods()
    return poimod.dump_seed(json.loads(path.read_text())), None


def _field_scope(path: Path):
    roads = JUMPSTART / "roads"
    if str(roads) not in sys.path:
        sys.path.insert(0, str(roads))
    import field as fieldmod                                # noqa: PLC0415
    return fieldmod.header(path)["seed"], None


def _placements_scope(path: Path):
    import yaml                                            # noqa: PLC0415
    doc = yaml.safe_load(path.read_text())
    return doc.get("seed"), doc.get("world")


def _placement_sets(ctx):
    root = JUMPSTART / "worlds" / (getattr(ctx.args, "world", None)
                                   or _mods()[0].world())
    return sorted(root.glob("*/placements.yaml")) if root.is_dir() else []


def _geography_scope(path: Path):
    """`roads/geography.yaml`'s own declaration.  `world` is deliberately null:
    worldgen geography is a function of the SEED alone, so the file is valid in
    any world built on that seed and claiming otherwise would be a refusal
    overstating its evidence."""
    import yaml                                            # noqa: PLC0415
    doc = yaml.safe_load(path.read_text())
    return doc.get("seed"), doc.get("world")


def _want_seed(ctx):
    """The seed THIS RUN is about, the same way every reader resolves it."""
    return getattr(ctx.args, "seed", None) or _mods()[0].seed()


def _geography_mod():
    roads = JUMPSTART / "roads"
    if str(roads) not in sys.path:
        sys.path.insert(0, str(roads))
    import geography                                        # noqa: PLC0415
    return geography


def _dump_path(ctx):
    """This seed's location dump, or None when it has none.

    `poi.dump_for_seed` REFUSES when no dump exists for the seed, which is the
    right behaviour for a planner -- routing against zero keep-outs is the
    defect it was written to stop.  Here it is a legitimate state: a world
    mid-build has not scanned one yet, and a section that reddened over that
    could not measure any world before it was finished.  So the refusal is
    caught and the artefact reported ABSENT, which `run()` already handles.
    """
    try:
        return _mods()[1].dump_for_seed(_want_seed(ctx))
    except SystemExit:
        return None


# `guard` names the reader that is REQUIRED to refuse, or is None when this
# artefact is KNOWN UNGUARDED -- in which case `owner` and `unguarded_why` are
# mandatory and the finding is ADVISORY.  An unguarded artefact is REPORTED,
# NAMED and COUNTED; it does not redden a verdict over a ticket somebody else
# is holding.  What it may NOT do is go quiet: `_case_every_artefact...` below
# refuses an unguarded entry that names no owner, so the only way to add one
# silently is to lie in writing.
ARTEFACTS = (
    {"label": "solved site set",
     # RESOLVED, NOT FROZEN.  This row named a fixed path while its reader
     # learned to resolve per seed, so on a fresh seed the gate would have
     # scope-checked a DIFFERENT FILE than the planner reads -- a check
     # pointed at the wrong artefact, which is the one thing this section
     # exists to catch.  Both must come from the SAME resolver, never from
     # two places that happen to agree today.
     "path": lambda ctx: _mods()[0].sites_path(_want_seed(ctx)),
     "reader": "roads/spec.py::load_solved_sites",
     "guard": "roads/spec.py::load_solved_sites",
     "scope": _sites_scope,
     "why": "16 site coordinates and pad radii; the road network's node set"},
    {"label": "location dump",
     # Resolved, not frozen -- see the note on the row above.  `dump_for_seed`
     # RAISES when this seed has no dump, which is a legitimate state for a
     # world mid-build, so it is caught and reported as absent rather than
     # allowed to redden a partial run.
     "path": _dump_path,
     "reader": "roads/poi.py::load",
     "guard": "roads/poi.py::load",
     "scope": _dump_scope,
     "why": "12,301 keep-out instances; the router's hard-radius mask"},
    {"label": "height field",
     "path": lambda ctx: getattr(ctx.args, "field", None),
     "reader": "roads/field.py::require_seed",
     "guard": "roads/field.py::require_seed",
     "scope": _field_scope,
     "why": "the ground every grade, batter and bridge span is measured on"},
    # MEASURED 2026-09-21 by SeedArgv, confirmed here: 9 files under
    # worlds/<World>/<preset>/, every one carrying `schema_version`, `world`
    # and `seed`, and SIX readers that never open the header --
    # network/verify_site.py:133, network/relocate_pad.py:106,
    # network/recost_pad.py:175 build the path from `args.world`;
    # blueprints/write_datum_blocks.py:53, blueprints/solve_placements.py:616,
    # network/waypoints.py:551 glob it.
    #
    # AND IT IS A WEAKER CLAIM THAN THE LOCATION DUMP, stated weakly on
    # purpose.  The dump sat at a fixed /tmp path that CANNOT vary with
    # --world, so a Fresh42 run measurably read Pirate68's 12,301 locations.
    # Here the path is worlds/<World>/..., so path and header AGREE today and
    # the live exposure is narrower: `args.world` defaults to the literal
    # "Ulfsland", and a copied or renamed preset directory would lie.  That is
    # UNEXERCISED, not wrong.  Recording it as equivalent would be a refusal
    # overstating its own evidence.
    # GUARD LANDED 2026-09-21 by SeedArgv and VERIFIED HERE, not taken on
    # report: terraform/flatten.py::load_placements reads the header once and
    # calls `worldseed.require_scope`, and `placement()` delegates to it so
    # every existing flatten caller inherits it.  The case below re-reads that
    # module's source, so this row cannot be turned green by editing this line.
    {"label": "placement set",
     "path": _placement_sets,
     "reader": "terraform/flatten.py::load_placements, reached from "
               "network/verify_site.py, network/recost_pad.py, "
               "network/relocate_pad.py::load_place",
     "guard": "terraform/flatten.py::load_placements",
     "scope": _placements_scope,
     "why": "every building's solved position, rotation and pad datum"},
    # THE FIFTH, and a different shape from the other four: this one did not
    # read a foreign artefact, it read NOTHING AND CALLED IT ZERO.
    # network/cli.py::_locations fell out of its loop and returned `[]` when
    # the dump directory held files for other seeds, so a run routed against
    # ZERO keep-outs and reported `zonesystem_locations_loaded: 0` as though
    # that were a measurement.  EMPTY IS NOT THE SAME CLAIM AS ABSENT.  It now
    # refuses, naming the seeds it does hold; a directory with no dump at all
    # still returns [], because there is no artefact there to be mis-scoped.
    {"label": "ZoneSystem location dump",
     "path": lambda ctx: sorted(Path("/tmp/seedscan/loc").glob("*.json")),
     "reader": "network/cli.py::_locations",
     "guard": "network/cli.py::_locations",
     "scope": _dump_scope,
     "why": "the seedscan keep-out set network/cli.py routes against"},
    # THE SIXTH, found 2026-09-21 while answering a peer who was about to EDIT
    # it, which is the only reason anybody looked.  `roads/segments.yaml` is
    # 93,353 lines of BUILT road network fitted to Ulfsland's coordinates, and
    # it declares `seed: Pirate68` and `world: Ulfsland` at top level beside
    # `schema_version` and `owner`.  TWELVE readers load it -- boardwalk,
    # census_equiv, clear, deviate, junction, precensus, ribbon, structures,
    # network, north, spec, poi -- and NOT ONE opens the header.  The four of
    # those that do call require_seed/require_scope are guarding the HEIGHT
    # FIELD or the sites/dump, never this file.
    #
    # IT WAS RECORDED UNGUARDED RATHER THAN LEFT OUT, and the recorded unguarded
    # count moved 0 -> 1 because of it.  Omitting it would have kept the count
    # at zero and the gate green, which is the exact move this section exists
    # to make impossible: the cheapest way to pass a population check is to
    # shrink the population.  `deviate.py` is THE TOOL THAT MOVES A ROAD OFF A
    # BUILDING and `junction.py` reconciles two roads' datums; handed a second
    # world's segments.yaml, either would re-route a road across the wrong
    # planet with every number in the report looking normal.
    #
    # GUARDED 2026-09-22 by OutlierSites, and the count goes back to 0.  The
    # `guard` names WHICH readers it covers, not all twelve: a declaration
    # wider than the code is the defect one layer up, and four readers --
    # ribbon.py's --segments path, north.py, junction.py::load and deviate.py's
    # own duplicate of the same loop -- are a NAMED RESIDUAL, not a claim.
    #
    # AND THE PROSE THIS REPLACES WAS WRONG IN A SECOND WAY.  It said the
    # seed/world keys "are never read", which was true, and that was broadcast
    # three times as "north_segments.yaml declares nothing", which was false:
    # MEASURED 2026-09-22, both files carry both keys -- segments.yaml at lines
    # 106 and 93,436, north_segments.yaml at 104 and 33,005.  They are 32,900
    # lines apart because `yaml.safe_dump(sort_keys=True)` sorts `seed` to the
    # head and `world` past six segments of centreline, so anyone reading the
    # first screen sees `segments:` and stops.  UNREAD IS NOT UNDECLARED.
    {"label": "built road network",
     # EVERY PLAN FILE OF THIS RUN'S SEED, as a LIST -- `run()` scope-checks
     # every element, the way the nine placements.yaml are done.  On
     # Pirate68 that is segments.yaml AND north_segments.yaml, a SECOND
     # artefact of the identical class read by the SAME structures.py loop;
     # naming only one would report the class guarded having looked at one
     # of its two files.  AND THE LIST IS THE SEED'S, NOT PIRATE68'S: this
     # row named both Ulfsland files literally, so on Fresh42 it
     # scope-checked Ulfsland's plan and never segments.Fresh42.yaml
     # (SeedScope, 2026-09-25).  `worldseed.segment_files` is the resolver.
     "path": lambda ctx: list(_mods()[0].segment_files(_want_seed(ctx))),
     "reader": "roads/{boardwalk,census_equiv,clear,deviate,junction,"
               "precensus,ribbon,structures,network,north,spec,poi}.py -- "
               "twelve readers, of which the structures.segment_map path is "
               "now guarded",
     "guard": "roads/structures.py::load_segment_doc",
     "scope": _sites_scope,
     "why": "the seed's built centreline, station profiles and crossing "
            "datums, across every plan file it has -- what deviate.py "
            "re-routes and junction.py reconciles"},
    # THE SEVENTH, added 2026-09-21 by SurveyNodes as part of REMOVING the
    # defect it guards rather than merely reporting it.  `roads/geography.yaml`
    # is this seed's DERIVED road geography -- 11 extremity, 4 abutment and 1
    # spawn_point coordinate -- and it exists because those 16 numbers were
    # LITERALS in roads/spec.py with no declaration of any kind.  An
    # undeclared constant is the worst member of this population: there is no
    # header to read, so `_check_one` could never have caught it and no count
    # here would ever have moved.  Making it a file with a scope is what put it
    # inside this section's reach at all.
    {"label": "derived road geography",
     # Resolved, not frozen, taken in this hunk at SurveyNodes' request rather
     # than in a second write minutes later -- guard-before-artefact and
     # hunk-one-before-hunk-two are the same window, and this file has already
     # paid for it once tonight.  `geography_path` takes an optional seed and
     # falls back to `worldseed.seed()`, so it is safe either way.
     "path": lambda ctx: _geography_mod().geography_path(_want_seed(ctx)),
     "reader": "roads/spec.py, at import, via roads/geography.py::load",
     "guard": "roads/geography.py::load",
     "scope": _geography_scope,
     # UPSTREAM, named so a reader can tell one bad scan from a systemic one:
     # this file is derived from the 1 m VHPATCH1 HEIGHT FIELD and the
     # GENERATOR LOCATION DUMP -- both rows above -- and NOT from
     # site_finder's coarse biome grid. The chain is four deep:
     # coarse grid -> height field -> derived road geography -> segments.yaml.
     "why": "16 road-network node coordinates -- every cape the trunk reaches "
            "and both bridge crossings between the two halves of the "
            "continent; derived from the height field and the location dump "
            "rows above"},
)


def _check_one(a, path, want_seed, want_world) -> Finding:
    """One artefact file, scope-checked.  `ok=False` only when it disagrees.

    An artefact with no guard is ADVISORY: reported with its numbers and its
    owner, counted in the population, and NOT deciding the exit code.  That is
    the registry's own `advisory` contract -- a reading whose precision is
    known to be worse than the judgement -- applied to a reading whose GUARD is
    known to be missing.  Reddening a verdict over a ticket somebody else is
    holding buys nothing; going quiet about it buys a lost check.
    """
    adv = a["guard"] is None
    owner = f" Owner: {a.get('owner', '')}." if adv else ""
    try:
        found_seed, found_world = a["scope"](path)
    except Exception as exc:                                # noqa: BLE001
        return Finding(
            check="artefact_declares_its_scope", ok=False, advisory=adv,
            message=(f"{a['label']} {path} could not be read for its own seed "
                     f"declaration: {type(exc).__name__}: {exc}.{owner}"),
            numbers={"reader": a["reader"], "path": str(path)})
    if not found_seed:
        return Finding(
            check="artefact_declares_its_scope", ok=False, advisory=adv,
            message=(f"{a['label']} {path} carries NO seed declaration, so "
                     f"nothing can prove it belongs to seed {want_seed!r} -- "
                     f"and it supplies {a['why']}.{owner}"),
            numbers={"reader": a["reader"], "want_seed": want_seed})
    if str(found_seed) != str(want_seed):
        return Finding(
            check="artefact_scope_matches_this_run", ok=False, advisory=adv,
            message=(f"{a['label']} {path} was measured on seed {found_seed!r} "
                     f"and this run plans {want_seed!r}; it supplies "
                     f"{a['why']}.{owner}"),
            numbers={"reader": a["reader"], "found_seed": str(found_seed),
                     "want_seed": want_seed})
    if found_world and want_world and str(found_world) != str(want_world):
        return Finding(
            check="artefact_scope_matches_this_run", ok=False, advisory=adv,
            message=(f"{a['label']} {path} was measured in world "
                     f"{found_world!r} and this run builds "
                     f"{want_world!r}.{owner}"),
            numbers={"reader": a["reader"], "found_world": str(found_world),
                     "want_world": want_world})
    return Finding(
        check="artefact_scope_matches_this_run", ok=True,
        message=(f"{a['label']} {path} declares seed {found_seed!r}, matching "
                 f"this run" + (" (UNGUARDED: no reader refuses it; "
                                f"{a.get('owner', 'unowned')})" if adv else "")),
        numbers={"reader": a["reader"], "found_seed": str(found_seed),
                 "guarded": not adv})


def run(ctx) -> list[Finding]:
    worldseed, _poi = _mods()
    want_seed = getattr(ctx.args, "seed", None) or worldseed.seed()
    want_world = getattr(ctx.args, "world", None) or worldseed.world()
    out: list[Finding] = []

    for a in ARTEFACTS:
        got = a["path"](ctx)
        # One entry may name a SET of files -- there is one placements.yaml per
        # preset and they are nine separate declarations, not one.  Checking
        # the first and calling the artefact done is how eight go unread.
        paths = ([] if got is None else
                 [Path(p) for p in (got if isinstance(got, (list, tuple))
                                    else [got])])
        present = [p for p in paths if p.exists()]
        if not present:
            # NOT a refusal.  A stage that was not asked to run has no
            # artefact, and calling that a defect would make this section red
            # on every partial run -- which is every run of a world still
            # being built.
            out.append(Finding(
                check="artefact_present", ok=True, advisory=True,
                message=(f"{a['label']} absent"
                         + (f" at {paths[0]}" if paths else "")
                         + f"; {a['reader']} has nothing to scope-check"),
                numbers={"reader": a["reader"]}))
            continue
        for p in present:
            out.append(_check_one(a, p, want_seed, want_world))
    # AND EVERY REFUSAL ABOVE GETS ITS CLASS, THROUGH THE ONE CLASSIFIER.
    return out + T.classify(sys.modules[__name__], out, "provenance")


# ---------------------------------------------------------------------------
# CASES.  Every fixture below CONSTRUCTS the artefact it tests in a temporary
# directory and never borrows one off this host: the defect this section
# records was found because a real /tmp dump was present, and a fixture that
# leaned on that dump would go QUIET the day somebody cleared /tmp rather than
# going red.
# ---------------------------------------------------------------------------
def _dump_bytes(seed: str | None) -> str:
    doc: dict = {"worldGenVersion": 2, "locationTypes": [],
                 "locations": [{"name": "StartTemple", "x": -64.68, "z": 3.31,
                                "y": 75.05, "exteriorRadius": 25,
                                "quantity": 1, "prioritized": True}]}
    if seed is not None:
        doc["seed"] = seed
        doc["seedNameFromWorld"] = seed
    return json.dumps(doc)


def _with_dump(seed: str | None, plan_seed: str):
    """Load a CONSTRUCTED dump declaring `seed` while planning `plan_seed`."""
    _ws, poimod = _mods()
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "loc.json"
        p.write_text(_dump_bytes(seed))
        return poimod.load(p, seed=plan_seed)


def _case_foreign_dump_is_refused():
    try:
        got = _with_dump("Pirate68", "Fresh42")
    except SystemExit as exc:
        return Finding(
            check="foreign_dump_refused", ok=False,
            message=(f"a Pirate68 location dump was refused for a Fresh42 "
                     f"plan: {str(exc)[:120]}"),
            numbers={"found_seed": "Pirate68", "want_seed": "Fresh42"})
    return Finding(
        check="foreign_dump_refused", ok=True,
        message=(f"NOT REFUSED: {len(got)} Pirate68 locations accepted into a "
                 f"Fresh42 plan"),
        numbers={"accepted": len(got)})


def _case_undeclared_dump_is_refused():
    try:
        got = _with_dump(None, "Fresh42")
    except SystemExit as exc:
        return Finding(
            check="undeclared_dump_refused", ok=False,
            message=(f"a dump with no seed declaration was refused: "
                     f"{str(exc)[:120]}"),
            numbers={"found_seed": None, "want_seed": "Fresh42"})
    return Finding(
        check="undeclared_dump_refused", ok=True,
        message=(f"NOT REFUSED: {len(got)} locations with no seed declaration "
                 f"accepted into a Fresh42 plan"),
        numbers={"accepted": len(got)})


def _case_this_seeds_dump_is_accepted():
    """THE OTHER DIRECTION.  A guard that refuses everything blocks every
    build, including the right one, and would pass both cases above."""
    got = _with_dump("Fresh42", "Fresh42")
    assert len(got) == 1, f"fixture dump must load its 1 location, got {len(got)}"
    return []


def _case_scope_is_read_from_the_header_not_the_path():
    """A FILENAME IS NOT A DECLARATION.  `f6fe167f4fcd.json` is Pirate68's own
    content hash and reads like provenance; it is not.  Both directions, under
    that exact filename, so a check that ever starts matching on the path is
    caught by whichever direction it gets wrong."""
    _ws, poimod = _mods()
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "f6fe167f4fcd.json"
        p.write_text(_dump_bytes("Fresh42"))
        n = len(poimod.load(p, seed="Fresh42"))
        assert n == 1, f"Pirate68's filename must not refuse a Fresh42 dump: {n}"
        p.write_text(_dump_bytes("Pirate68"))
        try:
            poimod.load(p, seed="Fresh42")
        except SystemExit:
            return []
        raise AssertionError(
            "Pirate68's filename must not ACCEPT a Pirate68 dump for Fresh42")


def _neuter_the_dump_reader():
    """RESTORE THE DEFECT VERBATIM: `poi.load` as it stood before 2026-09-21 --
    parse the header, never look at the seed."""
    _ws, poimod = _mods()
    saved = poimod.load

    # `path` has NO default any more.  It used to be `poimod.DUMP`, a frozen
    # content hash that was removed on 2026-09-24 when four road instruments
    # were found reading Pirate68's locations on a Fresh42 run; every caller
    # of the real `load` passes a path or lets it resolve by seed, and a
    # neuter that kept a default nobody has would be restoring a different
    # defect from the one it names.
    def blind(path, *, seed=None):
        d = json.loads(Path(path).read_text())
        reach: dict[str, float] = {}
        for t in d.get("locationTypes") or []:
            key = t.get("prefab") or t.get("name")
            if key:
                reach[key] = float(t.get("znviewReachM") or 0.0)
        out = []
        for r in d["locations"]:
            declared = float(max(r.get("exteriorRadius") or 0,
                                 r.get("interiorRadius") or 0,
                                 reach.get(r.get("prefab") or r.get("name"), 0.0)))
            q = int(r.get("quantity") or 0)
            prot = (bool(r.get("prioritized")) or bool(r.get("centerFirst"))
                    or (0 < q <= 5))
            out.append(poimod.Poi(r["name"], float(r["x"]), float(r["z"]),
                                  float(r.get("y") or 0.0), declared, prot, q))
        return out

    poimod.load = blind

    def undo():
        poimod.load = saved
    return undo


def _sites_bytes(seed: str, world: str) -> str:
    return (f"world: {world}\nseed: {seed}\nseed_hash: 147627509\n"
            "sites:\n"
            "- id: stenvik\n  type: village\n  xz: [496.0, 904.0]\n"
            "  patch: mainland\n  pad_radius_m: 30.0\n  road_priority: 1\n"
            "  portal_tag: stenvik\n  pad_y: 34.0\n")


def _case_foreign_site_set_is_refused():
    """THE SECOND ARTEFACT, SEPARATELY.  Not a parameterised row of the dump
    case: these are two readers in two modules and fixing one is exactly what
    left the other standing for a week."""
    roads = JUMPSTART / "roads"
    if str(roads) not in sys.path:
        sys.path.insert(0, str(roads))
    import spec as specmod                                  # noqa: PLC0415
    ws, _p = _mods()
    saved = ws.os.environ.get(ws.ENV_SEED), ws.os.environ.get(ws.ENV_WORLD)
    try:
        ws.os.environ[ws.ENV_SEED] = "Fresh42"
        ws.os.environ[ws.ENV_WORLD] = "Fresh42"
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sites.yaml"
            p.write_text(_sites_bytes("Pirate68", "Ulfsland"))
            try:
                got = specmod.load_solved_sites(p)
            except SystemExit as exc:
                return Finding(
                    check="foreign_site_set_refused", ok=False,
                    message=("a Pirate68/Ulfsland site set was refused for a "
                             f"Fresh42 plan: {str(exc)[:120]}"),
                    numbers={"found_seed": "Pirate68", "want_seed": "Fresh42"})
            return Finding(
                check="foreign_site_set_refused", ok=True,
                message=(f"NOT REFUSED: {len(got)} Ulfsland sites adopted into "
                         f"a Fresh42 plan at Ulfsland's coordinates "
                         f"{list(got.values())[0]['xz'] if got else '-'}"),
                numbers={"accepted": len(got)})
    finally:
        for k, v in zip((ws.ENV_SEED, ws.ENV_WORLD), saved):
            if v is None:
                ws.os.environ.pop(k, None)
            else:
                ws.os.environ[k] = v


def _neuter_the_site_reader():
    """RESTORE THE DEFECT VERBATIM: `spec.load_solved_sites` with the scope
    call removed and nothing else changed."""
    roads = JUMPSTART / "roads"
    if str(roads) not in sys.path:
        sys.path.insert(0, str(roads))
    import spec as specmod                                  # noqa: PLC0415
    ws, _p = _mods()
    saved = ws.require_scope

    def permissive(*_a, **_k):
        return {}

    ws.require_scope = permissive

    def undo():
        ws.require_scope = saved
    return undo


UNTIL_KINDS = ("derived", "asserted", "manual")


def evaluate_until(until: dict | None) -> tuple[str, str]:
    """`(verdict, detail)` for an unguarded row's stated deferral condition.

    WHY A REASON NEEDS A CONDITION AT ALL.  Design by `SurveyNodes`, amended by
    four other agents, after the one defect class of the night with no
    instrument: A WRONG REASON ATTACHED TO A CORRECT NUMBER.  Every other class
    eventually collides with a measurement; a false justification never does,
    because nothing ever recomputes it.  One row in the emission section read
    "not mine to land inside another agent's module" about a module its own
    owner had created that evening -- true-looking, never contradicted, and
    found only when the owner happened to reread their own entry.

    THE PROPERTY THAT SEPARATES A USABLE REASON FROM AN UNFALSIFIABLE ONE IS
    NOT DERIVATION -- a deferral CANNOT be derived, nothing knows why a human
    did not land a line -- IT IS FALSIFIABILITY.  "Not mine to land" names no
    checkable condition and survives forever.  "Deferred behind two live
    console writes" names a condition that EXPIRES, and something can say so.

    VERDICTS, and three of the four are not passes:
      still_deferred  the condition holds; the row is honestly still deferred
      expired         THE CONDITION IS NO LONGER TRUE -> REFUSE, demand the flip.
                      A deferral that has already happened stops being one.
      unresolvable    the condition cannot be evaluated at all -- a misspelled
                      symbol, a path whose parent never existed.  REFUSES, and
                      is counted separately.  A green-forever condition that
                      reads as "still deferred" is `moved = 0` inside the field
                      built to stop `moved = 0`.
      never_tried     `kind: manual` -- nobody converted the prose into an
                      assertion.  PERMITTED AND COUNTED.

    `never_tried` IS THE NUMBER THAT MATTERS, NOT "UNCHECKABLE".  The worked
    instance is `SpawnableRepair`'s: a docstring claiming a re-run reported
    zero inverted panels, where the measured answer was sixteen.  It was
    checkable the whole time and survived for months because nobody tried.  So
    the count is of reasons NEVER CONVERTED INTO AN ASSERTION, not of reasons
    that could not be.

    AND `manual` IS PREFERRED OVER A CONDITION THAT LOOKS CHECKABLE AND
    SILENTLY IS NOT.  An honest uncheckable reason costs one increment of a
    counted number; a fake checkable one costs the whole mechanism -- and a
    condition that has never once been false is indistinguishable from
    `manual` with better manners.
    """
    if not until:
        return "unresolvable", "no condition given"
    kind = until.get("kind")
    if kind not in UNTIL_KINDS:
        return "unresolvable", f"kind {kind!r} is not one of {UNTIL_KINDS}"
    if kind == "manual":
        return "never_tried", "prose never converted into an assertion"
    check = until.get("check")
    if check == "symbol_absent":
        mod, sym = until.get("module"), until.get("symbol")
        if not mod or not sym:
            return "unresolvable", "symbol_absent names no module/symbol"
        src = JUMPSTART / str(mod).split("jumpstart/")[-1]
        if not src.exists():
            return "unresolvable", f"{mod} is not in this tree"
        return (("still_deferred", f"{sym} absent from {mod}")
                if sym not in src.read_text()
                else ("expired", f"{sym} IS NOW PRESENT in {mod}"))
    if check == "path_absent":
        path = until.get("path")
        if not path:
            return "unresolvable", "path_absent names no path"
        p = JUMPSTART / str(path).split("jumpstart/")[-1]
        if not p.parent.exists():
            return "unresolvable", f"{path}'s parent is not in this tree"
        return (("still_deferred", f"{path} absent") if not p.exists()
                else ("expired", f"{path} NOW EXISTS"))
    return "unresolvable", f"check {check!r} is not evaluable in this tree"


def _case_every_artefact_names_a_reader_required_to_refuse():
    """THE POPULATION IS THE CHECK, AND AN UNGUARDED ROW MUST BE OWNED.

    An artefact listed here with no reader that refuses is a row in a table,
    not a guard; and the whole defect was one artefact nobody had listed at
    all.  So two things are asserted, and the second is the one that keeps
    this honest as the list grows:

      * a row claiming a `guard` must have one -- the named module must
        actually contain a refusal, verified in its source, so a guard cannot
        be claimed by writing its name here;
      * a row with `guard=None` must name an OWNER and a REASON.  That is the
        only way an unguarded artefact is allowed to exist, and it is what
        stops the advisory class becoming the place defects go to be quiet.
    """
    import collections                                      # noqa: PLC0415
    from ..registry import SECTIONS                         # noqa: PLC0415
    ws, _p = _mods()
    assert callable(ws.require_scope), "worldseed.require_scope must exist"
    guarded = unguarded = 0
    # PRINTED UNCONDITIONALLY, not when someone is suspicious. Every one of the
    # discriminator catches tonight came from an output that happened to be
    # verbose, not from anyone suspecting anything.
    coverage: dict[str, int] = collections.Counter()
    for a in ARTEFACTS:
        assert a["reader"], f"{a['label']} names no reader"
        assert a["why"], f"{a['label']} does not say what it supplies"
        if a["guard"] is None:
            unguarded += 1
            assert a.get("owner"), \
                (f"{a['label']} is UNGUARDED and names no owner. An unguarded "
                 f"artefact with nobody's name on it is how the location dump "
                 f"survived the height field being fixed.")
            assert a.get("unguarded_why"), \
                f"{a['label']} is UNGUARDED and does not say why"
            # AND THE PROSE MUST POINT AT A CONDITION THE TREE CAN EVALUATE.
            # `unguarded_why` alone is the one defect shape with no instrument:
            # a wrong reason attached to a correct number is never contradicted,
            # because nothing recomputes it.
            until = a.get("unguarded_until")
            verdict, detail = evaluate_until(until)
            coverage[verdict] += 1
            assert verdict != "unresolvable", \
                (f"{a['label']}'s deferral condition CANNOT BE EVALUATED "
                 f"({detail}). That is not 'still deferred' -- it is a row "
                 f"asserting a condition about something that is not there, "
                 f"which is `missing: []` inside the field built to stop "
                 f"`missing: []`. Counting it would leave it green forever; "
                 f"refusing it surfaces the typo the day it is written.")
            assert verdict != "expired", \
                (f"{a['label']} is still listed UNGUARDED but its own stated "
                 f"reason is NO LONGER TRUE ({detail}). A deferral that has "
                 f"already happened stops being a deferral: flip the row to "
                 f"guarded, or state a new reason.")
            if (until or {}).get("kind") == "asserted":
                # THE COUNT THAT MATTERS IS DERIVED, NEVER SELF-REPORTED.
                # `manual` was gameable by a condition that can never be false;
                # `never-tried` was gameable because no artefact knows whether
                # an author ATTEMPTED a case. This one is not an intention: the
                # named case must exist in this section AND carry a neuter,
                # which run_cases already RESTORES THE DEFECT against. Gameable
                # only by writing a neuter that genuinely restores the defect,
                # which is the work itself.
                cname = until.get("case")
                named = {c.name: c for c in SECTIONS["provenance"].cases}
                assert cname in named, \
                    (f"{a['label']} claims kind 'asserted' and names case "
                     f"{cname!r}, which is not registered in this section")
                assert named[cname].neuter is not None, \
                    (f"{a['label']}'s asserted case {cname!r} carries NO "
                     f"NEUTER, so nothing has ever restored the defect it "
                     f"claims to assert. An asserted reason with an "
                     f"unfalsifiable case is `manual` with better manners.")
            continue
        guarded += 1
        mod, _, _fn = a["guard"].partition("::")
        src = JUMPSTART / mod.split("jumpstart/")[-1]
        assert src.exists(), f"{a['guard']} names a module that is not there"
        text = src.read_text()
        assert "require_scope" in text or "require_seed" in text, \
            (f"{a['guard']} claims to guard {a['label']} and contains no "
             f"refusal; it supplies {a['why']}")
    assert guarded >= 6, f"only {guarded} artefacts reach a reader that refuses"
    # COVERAGE OF THE DEFERRAL CONDITIONS, PRINTED WHETHER OR NOT ANYONE ASKS.
    # `still_deferred` is the only bucket that is a pass; `unresolvable` and
    # `expired` already raised above. With zero unguarded rows every bucket is
    # zero, and that is itself the reading: nothing is deferred on prose.
    print(f"      deferral conditions: "
          f"{dict(coverage) or 'none -- no unguarded rows'}")
    # The count is ASSERTED, not merely derived, so that adding an unguarded
    # artefact is a decision somebody has to write down.  ITS HISTORY IS THE
    # POINT: 1 (placement set) -> 0 when SeedArgv's loader landed -> 1 again
    # forty minutes later when roads/segments.yaml turned out to be a
    # seed-scoped artefact nobody had listed.  A count that only ever falls is
    # a count nobody is really taking.
    #
    # AND THE PREDICATE IS STILL NOT ALLOWED TO FIND THEM.  segments.yaml was
    # found by a peer announcing they were about to edit it, not by a sweep,
    # so the honest reading of this number is "at least this many", and the
    # `owner`/`unguarded_why` fields are what stop the next one being quietly
    # omitted to keep this line green -- shrinking the population is the
    # cheapest way to pass a population check.
    # 2026-09-22: back to 0 when OutlierSites landed
    # roads/structures.py::load_segment_doc. It is `== 0`, not `<= 0`, for the
    # same reason the line above is a floor: a count that can only be satisfied
    # by having fewer is a count that rewards omission.
    #
    # `== 0` IS A STATEMENT OF TODAY'S MEASURED STATE, NOT A TARGET TO DEFEND.
    # It is also the most tempting number in this file to protect by
    # omission: under `<= 1` a newly discovered unguarded artefact could be
    # added honestly and this line stayed green, but under `== 0` the next
    # agent to find one faces failing the gate or not adding the row -- and
    # the second is free, silent, and looks like nothing happened.  SO:
    # DISCOVERING A NEW UNGUARDED SEED-SCOPED ARTEFACT MEANS MOVING THIS
    # LITERAL AND WRITING THE REASON BESIDE IT, NEVER OMITTING THE ROW.
    # Otherwise this assertion stops being a record of the population and
    # becomes a reason to keep the population small.
    assert unguarded == 0, \
        (f"{unguarded} artefacts are unguarded; the recorded state on "
         f"2026-09-22 is exactly zero -- roads/segments.yaml was the last one "
         f"and reaches roads/structures.py::load_segment_doc. An unguarded "
         f"artefact is allowed here ONLY with an owner and a reason")
    return []


def _fake_save(root: Path, world: str) -> Path:
    """A 1.0.12 save DIRECTORY for `world`, constructed, never borrowed."""
    d = root / world
    d.mkdir(parents=True)
    (d / "_main.2.fwl2").write_bytes(b"\x00" * 171)
    (d / "_main.2.db2").write_bytes(b"\x00" * 64)
    return d


def _case_gate_refuses_another_worlds_save():
    """THE GATE'S OWN INSTANCE OF THIS CLASS, and the one that matters most:
    a planner that reads the wrong world produces a bad plan, but a GATE that
    reads the wrong world produces a bad VERDICT, and a verdict is what stops
    everything else being checked."""
    from .. import instruments as I                       # noqa: PLC0415
    with tempfile.TemporaryDirectory() as td:
        src = _fake_save(Path(td), "Ulfsland")
        try:
            I.require_world_save(src, "Fresh42")
        except SystemExit as exc:
            return Finding(
                check="gate_refuses_a_foreign_save", ok=False,
                message=("the gate refused Ulfsland's save for a Fresh42 run: "
                         f"{str(exc)[:130]}"),
                numbers={"found_world": "Ulfsland", "want_world": "Fresh42"})
    return Finding(
        check="gate_refuses_a_foreign_save", ok=True,
        message=("NOT REFUSED: accept --world Fresh42 would measure Fresh42's "
                 "ledger against Ulfsland's ground and report it as Fresh42"),
        numbers={"found_world": "Ulfsland", "want_world": "Fresh42"})


def _case_gate_accepts_this_worlds_save():
    """Both directions, and the FROZEN-COPY direction specifically: every
    authoritative verdict this project takes is measured on a frozen copy, so
    a check that refused one would stop the gate rather than protect it."""
    from .. import instruments as I                       # noqa: PLC0415
    with tempfile.TemporaryDirectory() as td:
        frozen = _fake_save(Path(td) / "frozen-copy", "Ulfsland")
        got = I.require_world_save(frozen, "Ulfsland")
        assert got == "Ulfsland", got
        # A save directory that names its world but sits under any parent at
        # all is this world's save; the parent is not the declaration.
        assert "frozen-copy" in str(frozen)
    return []


def _neuter_the_gate_save_check():
    """RESTORE THE DEFECT VERBATIM: Ctx took `--world-src` on trust."""
    from .. import instruments as I                       # noqa: PLC0415
    saved = I.require_world_save

    def trusting(world_src, world):
        return world

    I.require_world_save = trusting

    def undo():
        I.require_world_save = saved
    return undo


def _fwl2(name: str, seed_name: str, seed: int, uid: int, gen: int = 2) -> bytes:
    """A CONSTRUCTED 1.0.12 `.fwl2` header, in the layout `ledger/writer.py::
    read_fwl` parses: outer length, int32 version, name, seedName, int32 seed,
    int64 uid, int32 worldGenVersion, a flag byte, an empty modifier list."""
    import struct                                          # noqa: PLC0415

    def zstr(s: str) -> bytes:
        raw, n, out = s.encode(), len(s.encode()), bytearray()
        while True:
            byte = n & 0x7F
            n >>= 7
            out.append(byte | (0x80 if n else 0))
            if not n:
                break
        return bytes(out) + raw

    body = (struct.pack("<i", 41) + zstr(name) + zstr(seed_name)
            + struct.pack("<i", seed) + struct.pack("<q", uid)
            + struct.pack("<i", gen) + b"\x00" + struct.pack("<i", 0))
    return struct.pack("<i", len(body)) + body


def _install(root: Path, world: str, uid: int) -> Path:
    """`<root>/<world>/config_merged/worlds_local/<world>` holding a save of
    world uid `uid`; the SAME name and seed whatever the uid, which is the
    case the name check cannot tell apart."""
    d = root / world / "config_merged" / "worlds_local" / world
    d.mkdir(parents=True)
    (d / "_main.7.fwl2").write_bytes(_fwl2(world, "Pirate68", 147627509, uid))
    (d / "_main.7.db2").write_bytes(b"\x00" * 64)
    return d


def _manifest_ledger(path: Path, **pins) -> Path:
    rec = {"seq": 0, "op": "world_manifest",
           "params": dict(world="Nyworld", seed_name="Pirate68", **pins)}
    path.write_text(json.dumps(rec) + "\n")
    return path


def _case_gate_refuses_a_same_named_save_of_another_world():
    """THE CLASS THE NAME CHECK CANNOT SEE: two worlds on one host, one name,
    one seed.  MEASURED 2026-10-05: the from-scratch Ulfsland regeneration
    (install `UlfStaging`, uid 1008370488) and the live Ulfsland (uid
    2212643513) are both `Ulfsland` on Pirate68, and ribbon.py's regrade
    census passed `require_world_save` on the live world's save."""
    from .. import instruments as I                       # noqa: PLC0415
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        mine = _install(td / "staging", "Nyworld", 1008370488)
        other = _install(td / "production", "Nyworld", 2212643513)
        ledger = _manifest_ledger(td / "ledger.jsonl",
                                  world_uid=1008370488,
                                  fwl_sha256=I.save_identity(mine)["fwl_sha256"])
        assert I.require_world_save(other, "Nyworld") == "Nyworld"
        try:
            I.require_save_is_the_ledgers(other, "Nyworld", ledger)
        except SystemExit as exc:
            return Finding(
                check="gate_refuses_a_same_named_save_of_another_world",
                ok=False,
                message=("refused world uid 2212643513's save for a ledger "
                         f"about uid 1008370488: {str(exc)[:110]}"),
                numbers={"save_uid": 2212643513, "ledger_uid": 1008370488})
    return Finding(
        check="gate_refuses_a_same_named_save_of_another_world", ok=True,
        message=("NOT REFUSED: a census would measure the live Ulfsland's "
                 "ground under the ledger of the world being built"),
        numbers={"save_uid": 2212643513, "ledger_uid": 1008370488})


def _case_gate_accepts_the_ledgers_own_save_under_every_pin():
    """The other direction, per pin.  A uid pin is decisive; a hash-only pin
    that DIFFERS is not refused (Ulfsland's live `.fwl2` no longer hashes to
    what its own ledger pinned -- the same world, 8833746a vs c5fdbf8f), it is
    reported `unverifiable`; no ledger is `unbuilt`; and a world's own manifest
    carries the uid the gate compares."""
    import os                                              # noqa: PLC0415
    from .. import instruments as I                       # noqa: PLC0415
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        mine = _install(td / "staging", "Nyworld", 1008370488)
        h = I.save_identity(mine)["fwl_sha256"]
        got = {
            "uid": I.require_save_is_the_ledgers(
                mine, "Nyworld", _manifest_ledger(
                    td / "a.jsonl", world_uid=1008370488, fwl_sha256="0" * 64)),
            "hash_equal": I.require_save_is_the_ledgers(
                mine, "Nyworld", _manifest_ledger(
                    td / "b.jsonl", fwl_sha256=h)),
            "hash_differs_no_uid": I.require_save_is_the_ledgers(
                mine, "Nyworld", _manifest_ledger(
                    td / "c.jsonl", fwl_sha256="f" * 64)),
            "no_pin": I.require_save_is_the_ledgers(
                mine, "Nyworld", _manifest_ledger(td / "d.jsonl")),
            "no_ledger": I.require_save_is_the_ledgers(
                mine, "Nyworld", td / "absent.jsonl"),
        }
        want = {"uid": "same", "hash_equal": "same",
                "hash_differs_no_uid": "unverifiable", "no_pin": "unpinned",
                "no_ledger": "unbuilt"}
        prior = {k: os.environ.pop(k, None)
                 for k in ("VALHEIM_WORLD_ROOT", "VALHEIM_ROOT")}
        os.environ["VALHEIM_ROOT"] = str(td / "staging")
        try:
            m = I._ledger_writer().fingerprint(
                "Nyworld", heavy=False, probe_modifiers=False)
        finally:
            for k, v in prior.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v
    bad = {k: (got[k], want[k]) for k in want if got[k] != want[k]}
    if m.get("world_uid") != 1008370488:
        bad["manifest_world_uid"] = (m.get("world_uid"), 1008370488)
    if m.get("fwl_sha256") != h:
        bad["manifest_fwl_sha256"] = (m.get("fwl_sha256"), h)
    return Finding(
        check="gate_accepts_the_ledgers_own_save_under_every_pin",
        ok=not bad,
        message=(f"disagreeing arms (got, want): {bad}" if bad else
                 f"every pin arm answered as required: {got}"),
        numbers={"arms": len(want) + 2, "disagreeing": len(bad)})


def _neuter_the_ledger_identity_check():
    """RESTORE THE DEFECT VERBATIM: the save was identified by its NAME alone."""
    from .. import instruments as I                       # noqa: PLC0415
    saved = I._SAVE_IDENTITY_MODE
    I._SAVE_IDENTITY_MODE = "off"

    def undo():
        I._SAVE_IDENTITY_MODE = saved
    return undo


def _case_one_install_root_for_every_reader():
    """THE SPLIT THAT SENT THE CENSUS TO THE WRONG INSTALL.  The ledger's
    manifest read `$VALHEIM_ROOT`; `accept/instruments.py` and `rcon.py` read
    `$VALHEIM_WORLD_ROOT`.  The regeneration's environment set only the first,
    so the manifest pinned UlfStaging and the census froze production."""
    import os                                              # noqa: PLC0415
    from .. import instruments as I                       # noqa: PLC0415
    W = I._worldseed()
    keys = (W.ENV_WORLD_ROOT, W.ENV_VALHEIM_ROOT)
    saved = {k: os.environ.pop(k, None) for k in keys}
    got = {}
    try:
        got["default"] = str(W.world_root())
        os.environ[W.ENV_VALHEIM_ROOT] = "/scratch/staging"
        got["only_valheim_root"] = I.world_src_default("Nyworld")
        os.environ[W.ENV_WORLD_ROOT] = "/scratch/explicit"
        got["both_set"] = I.world_src_default("Nyworld")
        got["argument"] = I.world_src_default("Nyworld", root="/scratch/arg")
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
    tail = "/Nyworld/config_merged/worlds_local/Nyworld"
    want = {"default": W.DEFAULT_WORLD_ROOT,
            "only_valheim_root": "/scratch/staging" + tail,
            "both_set": "/scratch/explicit" + tail,
            "argument": "/scratch/arg" + tail}
    bad = {k: (got[k], want[k]) for k in want if got[k] != want[k]}
    return Finding(
        check="one_install_root_serves_every_reader", ok=not bad,
        message=(f"disagreeing arms (got, want): {bad}" if bad else
                 "default, $VALHEIM_ROOT alone, both variables and an "
                 "argument each resolve to the root they name"),
        numbers={"arms": len(want), "disagreeing": len(bad)})


def _neuter_the_split_install_root():
    """RESTORE THE DEFECT VERBATIM: the resolver read `$VALHEIM_WORLD_ROOT`
    alone, as `accept/instruments.py` and `terraform/rcon.py` did."""
    import os                                              # noqa: PLC0415
    from .. import instruments as I                       # noqa: PLC0415
    W = I._worldseed()
    saved = W.world_root

    def shipped():
        return Path(os.environ.get(W.ENV_WORLD_ROOT, W.DEFAULT_WORLD_ROOT))

    W.world_root = shipped

    def undo():
        W.world_root = saved
    return undo


def _case_a_run_hands_its_children_the_root_it_was_given():
    """`--world-root` was parsed into `pipeline.Scope` and then dropped on the
    floor: `Scope.env()` carried the seed and the world and no child inherited
    the root, so a child that resolves it for itself read its own
    environment.  An explicit flag beats the ambient environment."""
    import os                                              # noqa: PLC0415
    if str(JUMPSTART) not in sys.path:
        sys.path.insert(0, str(JUMPSTART))
    import pipeline                                        # noqa: PLC0415
    sc = pipeline.Scope("Pirate68", "Nyworld", Path("/w"),
                        Path("/install/staging"), Path("/vh"))
    prior = os.environ.get("VALHEIM_WORLD_ROOT")
    os.environ["VALHEIM_WORLD_ROOT"] = "/ambient/production"
    try:
        got = sc.env().get("VALHEIM_WORLD_ROOT")
    finally:
        if prior is None:
            os.environ.pop("VALHEIM_WORLD_ROOT", None)
        else:
            os.environ["VALHEIM_WORLD_ROOT"] = prior
    return Finding(
        check="a_run_hands_its_children_the_root_it_was_given",
        ok=got == "/install/staging",
        message=(f"a child of a run built on /install/staging inherited "
                 f"{got!r}"),
        numbers={"inherited": str(got)})


def _neuter_the_dropped_world_root():
    """RESTORE THE DEFECT VERBATIM: `Scope.env()` without the root line."""
    if str(JUMPSTART) not in sys.path:
        sys.path.insert(0, str(JUMPSTART))
    import pipeline                                        # noqa: PLC0415
    saved = pipeline.Scope.env

    def shipped(self):
        e = saved(self)
        e.pop("VALHEIM_WORLD_ROOT", None)
        import os                                          # noqa: PLC0415
        if os.environ.get("VALHEIM_WORLD_ROOT") is not None:
            e["VALHEIM_WORLD_ROOT"] = os.environ["VALHEIM_WORLD_ROOT"]
        return e

    pipeline.Scope.env = shipped

    def undo():
        pipeline.Scope.env = saved
    return undo


def _case_producer_stamps_the_seed_it_ran_on():
    """THE HOLE A DECLARATION CHECK LEAVES: a FALSE declaration passes it.

    `settlements/plan.py` writes the `seed:` line every check above reads, and
    until 2026-09-21 that line was the literal `Pirate68` (plan.py:241) beside
    a literal `world: Ulfsland` (plan.py:520), with no `--seed` argument on the
    module at all.  So a plan solved on ANY seed declared itself Pirate68, and
    every scope check downstream would have been comparing a constant to a
    constant and passing.  A false declaration is strictly worse than none: it
    is the same wrong plan with a signature on it.

    Both directions, on CONSTRUCTED VHPATCH1 headers, so this case cannot go
    quiet if /tmp is cleared."""
    import importlib.util                                  # noqa: PLC0415
    import struct                                          # noqa: PLC0415
    # LOADED BY PATH, NOT BY NAME.  There are three `plan.py` under
    # tools/jumpstart -- settlements/, crossings/ and network/ -- and whichever
    # section imports one first owns `sys.modules["plan"]` for the whole run.
    # MEASURED: `import plan` inside a single-section selftest gave
    # settlements/plan.py and inside the full 204-case run gave a sibling,
    # which surfaced as `module 'plan' has no attribute 'scope_of'`. A fixture
    # that resolves differently depending on what ran before it is not a
    # fixture.
    roots = JUMPSTART / "settlements"
    if str(roots) not in sys.path:
        sys.path.insert(0, str(roots))
    spec = importlib.util.spec_from_file_location(
        "_provenance_settlements_plan", roots / "plan.py")
    planmod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(planmod)

    def field_of(seed: str, seed_hash: int) -> bytes:
        nb = seed.encode()
        return (b"VHPATCH1" + struct.pack("<i", seed_hash)
                + struct.pack("<i", len(nb)) + nb + struct.pack("<i", 0))

    with tempfile.TemporaryDirectory() as td:
        a = Path(td) / "a.bin"
        a.write_bytes(field_of("Nyseed", 4242))
        got = planmod.scope_of(str(a), "")
        assert got["seed"] == "Nyseed", \
            f"the solver stamped {got['seed']!r} for a Nyseed field"
        assert got["seed_hash"] == 4242, got
        assert got["world"] is None, \
            (f"the solver GUESSED world {got['world']!r} for a non-default "
             f"seed; a guessed world is the same false declaration as a "
             f"literal one")
        # And the default pair must be unchanged, byte for byte, because
        # settlements/sites.yaml on disk carries exactly these three values.
        b = Path(td) / "b.bin"
        b.write_bytes(field_of("Pirate68", 147627509))
        dflt = planmod.scope_of(str(b), "")
        assert dflt == {"seed": "Pirate68", "seed_hash": 147627509,
                        "world": "Ulfsland"}, dflt
    return []


def _case_an_unbuilt_world_is_not_a_crash_and_is_not_a_pass():
    """A CHECK THAT CANNOT RUN ON A NEW WORLD IS A CHECK THAT WAS NEVER GENERAL.

    MEASURED 2026-09-21 driving a fresh seed: with no
    `ledger/runs/<World>/ledger.jsonl`, three sections did not report an empty
    population, they RAISED -- sections/roads.py:71 and sections/structures.py
    :380 and :446 on `ledger.read_text()`, and lighting through the same
    helper.  The gate reported a FileNotFoundError where the true statement is
    "nothing has been built in this world yet".

    TWO DIRECTIONS AND BOTH MATTER.  A world with no build must not CRASH --
    that is a check that cannot measure a world under construction, which is
    every world before it is finished.  And it must not PASS -- an empty world
    reported in the same shape as an accepted one is the worst output this gate
    could produce, and it is one `ok=True, advisory=True` away at all times.
    So the exit code is the no-verdict 2, asserted here, not 0.
    CONFINED TO `--section provenance`, AND THAT IS NOT A DETAIL.  The neuter
    below cuts the no-build branch out of the source so the run proceeds into
    the sections -- which is the whole proof -- and the FIRST version of this
    case did that with every section selected.  It drove `run_harness` against
    a fabricated save, took the project-wide flock, and contended with a
    sibling's authoritative run for five minutes before it was killed.  A
    FIXTURE MUST NOT REACH A SHARED INSTRUMENT.  Confining the selection keeps
    the neutered path inside this section's own `run()`, which reads headers
    and touches nothing.
    """
    import contextlib                                      # noqa: PLC0415
    import io                                              # noqa: PLC0415
    with tempfile.TemporaryDirectory() as td:
        save = _fake_save(Path(td), "Nyworld")
        m = _load_accept_main("_provenance_accept_main")
        # The run under test PRINTS its own verdict, and in the neutered
        # direction that is a bare "FAIL" landing in the middle of the
        # selftest's own report, where it reads as a failing case. Captured,
        # not silenced: it is returned to nobody, but the exit code -- the
        # thing actually being asserted -- is untouched.
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = m.main(["--world", "Nyworld", "--seed", "Nyseed",
                         "--section", "provenance",
                         "--world-src", str(save),
                         "--cache", str(Path(td) / "cache"),
                         "--ledger", str(Path(td) / "nothing-here.jsonl")])
    assert rc == 2, \
        (f"an unbuilt world exited {rc}; 0 would report an empty world in the "
         f"same shape as an accepted one")
    return []


# The case above loads `accept/__main__.py` through this, so a neuter can hand
# it a SOURCE-EDITED copy with the no-build branch cut out.  Patching the
# already-imported module would not do it: the branch is a statement in
# `main()`, not a function that can be swapped, and a neuter that cannot
# actually restore the defect proves nothing.
_MAIN_SOURCE_EDIT = None


def _load_accept_main(tag: str):
    import importlib.util                                  # noqa: PLC0415
    src = (JUMPSTART / "accept" / "__main__.py").read_text()
    if _MAIN_SOURCE_EDIT is not None:
        src = _MAIN_SOURCE_EDIT(src)
    spec = importlib.util.spec_from_file_location(
        tag, JUMPSTART / "accept" / "__main__.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[tag] = m
    exec(compile(src, str(JUMPSTART / "accept" / "__main__.py"), "exec"),
         m.__dict__)
    return m


def _neuter_the_no_build_branch():
    """RESTORE THE DEFECT VERBATIM: cut the no-build branch out of the source,
    so the sections are handed a ledger that is not there and raise one by one
    exactly as they did before it existed.

    THE ANCHOR IS THE BRANCH THIS SOURCE ACTUALLY HAS, AND KEEPING IT CURRENT
    IS THE CASE'S OWN JOB.  MEASURED 2026-09-23: this neuter was anchored on
    `    if not ctx.ledger.exists():`, which `accept/__main__.py` stopped
    spelling when the MEASURE/NO_BUILD/RUN disposition was named -- so the
    case raised its own assertion and the whole eleven-section selftest read
    378/379 FAIL. The assertion did its job: a neuter that cannot restore the
    defect proves nothing, and it said so rather than passing quietly.
    """
    global _MAIN_SOURCE_EDIT
    marker = '    if disposition == "NO_BUILD":'
    src = (JUMPSTART / "accept" / "__main__.py").read_text()
    assert marker in src, "the no-build branch is gone; this case tests nothing"

    def cut(text: str) -> str:
        head, _, tail = text.partition(marker)
        # Drop the branch body up to the line that resumes the run.
        _body, sep, rest = tail.partition("    results = {}")
        assert sep, "could not find the end of the no-build branch"
        return head + sep + rest

    _MAIN_SOURCE_EDIT = cut

    def undo():
        global _MAIN_SOURCE_EDIT
        _MAIN_SOURCE_EDIT = None
    return undo

# ---------------------------------------------------------------------------
# THE DERIVED ROAD GEOGRAPHY, AND THE ONE DEFECT IN THIS SECTION THAT HAD NO
# HEADER TO READ.
#
# The other six artefacts were FILES that declared a seed nobody read.  This
# one was 16 LITERAL COORDINATES in `roads/spec.py` -- 11 `extremity`, 4
# `abutment`, 1 `spawn_point` -- with no header, no path and no declaration of
# any kind, so there was nothing for `_check_one` to read and no count in this
# section would ever have moved.  A constant is the quietest possible member of
# this population.
#
# Both producers already existed and were already seed-parameterised
# (`roads/survey.py::survey_patch`, `crossings/survey.py`, `roads/poi.py::load`)
# and NOTHING CALLED THEM; the numbers had been read off their output once and
# typed in.  `roads/geography.py` is the call, and turning the constants into a
# scoped artefact is what put them inside this section's reach.
# ---------------------------------------------------------------------------

# THE DEFECT, VERBATIM.  These are the 16 records exactly as they stood in
# `roads/spec.py::NODES` before 2026-09-21, reduced to the fields the defect is
# about (id, island, patch, coordinate, kind).  The neuter below re-installs
# them, so the case below fails whenever the literals are what answers.
#
# A FIXTURE MUST CONSTRUCT THE STATE IT TESTS.  These are written out here and
# NOT read back from geography.yaml, a save, or a /tmp dump: a case that
# borrowed the artefact under test would go QUIET the day that artefact moved,
# instead of red.
_FROZEN_NODES = {
    "temple": ("spawn", "mainland", (-64.68, 3.31), "spawn_point"),
    "brg-s1-e": ("spawn", "mainland", (-312.0, -64.0), "abutment"),
    "brg-s2-e": ("spawn", "mainland", (-318.0, 230.0), "abutment"),
    "northcape": ("spawn", "mainland", (758.0, 2090.0), "extremity"),
    "eastlobe": ("spawn", "mainland", (1728.0, 1162.0), "extremity"),
    "northeast": ("spawn", "mainland", (1822.0, 1586.0), "extremity"),
    "southeast": ("spawn", "mainland", (1148.0, 474.0), "extremity"),
    "southcape": ("spawn", "mainland", (72.0, -862.0), "extremity"),
    "brg-s1-w": ("west", "westisle", (-325.0, -64.0), "abutment"),
    "brg-s2-w": ("west", "westisle", (-349.0, 246.0), "abutment"),
    "w-north": ("west", "westisle", (-186.0, 2582.0), "extremity"),
    "w-mid": ("west", "westisle", (-132.0, 1420.0), "extremity"),
    "w-west": ("west", "westisle", (-1288.0, 974.0), "extremity"),
    "w-southwest": ("west", "westisle", (-1258.0, 166.0), "extremity"),
    "w-south": ("west", "westisle", (-774.0, -290.0), "extremity"),
    "w-northwest": ("west", "westisle", (-1408.0, 2184.0), "extremity"),
}


def _geography_mod():
    roads = JUMPSTART / "roads"
    if str(roads) not in sys.path:
        sys.path.insert(0, str(roads))
    import geography as geomod                              # noqa: PLC0415
    return geomod


def _geography_bytes(seed) -> str:
    """A geography document, CONSTRUCTED, never copied off disk."""
    import yaml                                             # noqa: PLC0415
    doc = {"anchors": {"portalhub": [-292.5, 213.5]},
           "nodes": [{"id": "northcape", "island": "spawn", "patch": "mainland",
                      "xz": [758.0, 2090.0], "kind": "extremity",
                      "provenance": "constructed by the provenance section"}]}
    if seed is not None:
        doc["seed"] = seed
    return yaml.safe_dump(doc, sort_keys=False)


def _load_geography_under_seed(seed_bytes, want_seed: str):
    ws, _p = _mods()
    geomod = _geography_mod()
    saved = ws.os.environ.get(ws.ENV_SEED)
    try:
        ws.os.environ[ws.ENV_SEED] = want_seed
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "geography.yaml"
            p.write_text(seed_bytes)
            try:
                return None, geomod.load(p)
            except SystemExit as exc:
                return exc, None
    finally:
        if saved is None:
            ws.os.environ.pop(ws.ENV_SEED, None)
        else:
            ws.os.environ[ws.ENV_SEED] = saved


def _case_foreign_road_geography_is_refused():
    exc, got = _load_geography_under_seed(_geography_bytes("Pirate68"), "Fresh42")
    if exc is not None:
        return Finding(
            check="foreign_road_geography_refused", ok=False,
            message=("a Pirate68 road geography was refused for a Fresh42 "
                     f"plan: {str(exc)[:120]}"),
            numbers={"found_seed": "Pirate68", "want_seed": "Fresh42"})
    return Finding(
        check="foreign_road_geography_refused", ok=True,
        message=(f"NOT REFUSED: {len(got)} Ulfsland road nodes adopted into a "
                 f"Fresh42 plan at Ulfsland's coordinates "
                 f"{list(got.values())[0]['xz'] if got else '-'}"),
        numbers={"accepted": len(got)})


def _case_undeclared_road_geography_is_refused():
    """THE HALF PEOPLE SKIP.  `require_scope` refuses an artefact that declares
    NOTHING, and a guard that only catches a WRONG declaration is a weaker guard
    wearing the same name -- 'it did not say' is the same permission as 'it said
    Pirate68', and the defect this file replaces said nothing at all."""
    exc, got = _load_geography_under_seed(_geography_bytes(None), "Pirate68")
    if exc is not None:
        return Finding(
            check="undeclared_road_geography_refused", ok=False,
            message=("a road geography carrying no seed declaration was "
                     f"refused: {str(exc)[:120]}"),
            numbers={"found_seed": None, "want_seed": "Pirate68"})
    return Finding(
        check="undeclared_road_geography_refused", ok=True,
        message=(f"NOT REFUSED: {len(got)} undeclared road nodes adopted"),
        numbers={"accepted": len(got)})


def _neuter_the_geography_reader():
    """RESTORE THE DEFECT VERBATIM: `geography.load` answering from the frozen
    literals, with no file read and no scope call -- which is exactly what
    `roads/spec.py` did when the coordinates were constants in its own source."""
    geomod = _geography_mod()
    saved = geomod.load

    def frozen(path=None):
        return {nid: {"island": isl, "patch": patch, "xz": xz, "kind": kind,
                      "provenance": "MEASURED, survey.py"}
                for nid, (isl, patch, xz, kind) in _FROZEN_NODES.items()}

    geomod.load = frozen

    def undo():
        geomod.load = saved
    return undo


def _case_road_geography_is_derived_and_not_a_constant():
    """THE NODES A RUN ACTUALLY USES CAME OUT OF A SCOPED FILE.

    Not "the loader is importable" -- that passes with the constants still in
    place.  This asserts the two things the defect violated: the ids `spec.py`
    publishes as derived are the ones `geography.load` returned, and asking for
    them on ANOTHER SEED raises instead of answering.  Under the neuter, `load`
    answers from `_FROZEN_NODES` for every seed and both halves fail.
    """
    roads = JUMPSTART / "roads"
    if str(roads) not in sys.path:
        sys.path.insert(0, str(roads))
    import spec as specmod                                   # noqa: PLC0415
    geomod = _geography_mod()
    ws, _p = _mods()

    derived = dict(getattr(specmod, "DERIVED_GEOGRAPHY", {}) or {})
    kinds = {"extremity", "abutment", "spawn_point"}
    published = {n for n, v in specmod.NODES.items() if v.get("kind") in kinds}
    missing = sorted(published - set(derived))
    if missing:
        return Finding(
            check="road_geography_is_derived", ok=True,
            message=(f"{len(missing)} geography node(s) are NOT derived and can "
                     f"only be constants in roads/spec.py: "
                     f"{', '.join(missing[:6])}"),
            numbers={"published": len(published), "derived": len(derived),
                     "undecided": len(missing)})

    # THE SECOND HALF, AND IT MOVED ONCE ALREADY.  It used to ask for seed
    # `Fresh42` and require a refusal; `geography.py` then learned to resolve
    # one document PER SEED, a real `geography.Fresh42.yaml` appeared, and the
    # case went red for being right.  A constant does not care which seed you
    # ask for, so the question that still separates the two is a seed NOBODY has
    # a document for: a derivation refuses and names the remedy, a constant
    # answers.  The seed name is constructed here and cannot collide with a real
    # emission.
    ghost = "NoSeedHasThisName42"
    saved = ws.os.environ.get(ws.ENV_SEED)
    try:
        ws.os.environ[ws.ENV_SEED] = ghost
        try:
            got = geomod.load()
        except SystemExit:
            return Finding(
                check="road_geography_is_derived", ok=False,
                message=(f"all {len(published)} geography coordinates come from "
                         f"geography.load(), and a seed with no emitted document "
                         f"is refused with its remedy rather than answered"),
                numbers={"published": len(published), "derived": len(derived)})
        return Finding(
            check="road_geography_is_derived", ok=True,
            message=(f"NOT REFUSED: geography.load() answered {len(got)} node(s) "
                     f"for seed {ghost} -- "
                     f"{list(got.values())[0]['xz'] if got else '-'} -- so the "
                     f"coordinates are not a function of the seed"),
            numbers={"accepted": len(got)})
    finally:
        if saved is None:
            ws.os.environ.pop(ws.ENV_SEED, None)
        else:
            ws.os.environ[ws.ENV_SEED] = saved

# ---------------------------------------------------------------------------
# ABSENT IS NOT EMPTY.
#
# A seed may genuinely have no bridge: MEASURED on Fresh42, the two trunk
# landmasses are separate islands whose bounding boxes are 4,776 m apart
# (window centres 7,358 m), against Crossings' 96 m span ceiling -- so S1 and S2
# do not exist there and their four abutment nodes cannot be sited.  That is a
# fact about the planet and the project already answers it: a separate island is
# PORTAL-ONLY.
#
# The hazard is one level up.  A document with NO `no_crossing` key and one with
# `no_crossing: []` are DIFFERENT CLAIMS -- "nothing was ruled out" versus "this
# emitter never considered crossings" -- and reading the first as the second is
# the same error as `network/cli.py::_locations` reporting
# `zonesystem_locations_loaded: 0` for a dump it never found.  It read nothing
# and called it zero.  So `absent_nodes` REFUSES a document without the key, and
# `spec.py` refuses an edge whose missing end is not named in it.
# ---------------------------------------------------------------------------

def _case_an_absent_crossing_carries_its_reason():
    import yaml                                             # noqa: PLC0415
    geomod = _geography_mod()
    doc = yaml.safe_load(_geography_bytes("Pirate68"))
    doc.pop("no_crossing", None)
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "geography.yaml"
        p.write_text(yaml.safe_dump(doc, sort_keys=False))
        try:
            got = geomod.absent_nodes(p)
        except SystemExit as exc:
            return Finding(
                check="absent_crossing_carries_its_reason", ok=False,
                message=("a geography document with no no_crossing record was "
                         f"refused rather than read as empty: {str(exc)[:110]}"),
                numbers={"no_crossing_key": "absent"})
        return Finding(
            check="absent_crossing_carries_its_reason", ok=True,
            message=(f"NOT REFUSED: a document that never considered crossings "
                     f"was read as {len(got)} explained absences, so an edge "
                     f"could lose a terminus with nothing recorded"),
            numbers={"no_crossing_key": "absent", "read_as": len(got)})


def _neuter_absent_is_empty():
    """RESTORE THE DEFECT VERBATIM: `absent_nodes` with the presence check
    dropped, so a missing record reads as an empty one."""
    geomod = _geography_mod()
    saved = geomod.absent_nodes

    def permissive(path=geomod.GEOGRAPHY_YAML):
        import yaml                                         # noqa: PLC0415
        doc = yaml.safe_load(Path(path).read_text())
        out = {}
        for c in doc.get("no_crossing") or []:
            for nid in c["node_ids"]:
                out[nid] = {"why": c["reason"]}
        return out

    geomod.absent_nodes = permissive

    def undo():
        geomod.absent_nodes = saved
    return undo


# ---------------------------------------------------------------------------
# THE AUTHORED ANCHOR, DEMANDED WHERE IT IS CONSUMED (geography.ANCHOR_DEPENDS_ON).
#
# The fields below are CONSTRUCTED: 1 m rasters at the water plane +/- 10 m,
# one with a single landmass (no channel, so no crossing reads an anchor) and
# one with two landmasses 20 m apart whose far window's main landmass is the
# near window's second one (bridgeable, so S2 is costed from the anchor).
# ---------------------------------------------------------------------------
def _anchor_fields(bridgeable: bool):
    import numpy as np                                      # noqa: PLC0415
    W = _geography_mod().crossings_survey().W

    def fld(fid, cx, n, land_cols):
        h = np.full((n, n), 20.0, dtype=np.float32)
        for a, b in land_cols:
            h[:, a:b] = 40.0
        return W.Field(fid, cx, 0.0, n / 2.0, 1.0, n, h,
                       np.ones((n, n), np.uint8))
    if not bridgeable:
        lone = fld("near", 0.0, 128, [(0, 70)])
        return lone, lone
    return (fld("near", 0.0, 128, [(0, 70), (90, 128)]),
            fld("far", 64.0, 128, [(26, 128)]))


def _first_emit_crossings(bridgeable: bool):
    """`emit`'s anchor resolution with NO --anchor and NO prior document, then
    the S2 (`cheapest_from_anchor`) crossing on the constructed fields.
    `(refusal, abutment ids)`."""
    geomod = _geography_mod()
    s2 = tuple(c for c in geomod.CROSSING_PLAN
               if c["rule"] == "cheapest_from_anchor")
    near, far = _anchor_fields(bridgeable)
    with tempfile.TemporaryDirectory() as td:
        try:
            anchors = geomod.resolve_anchors(
                None, Path(td) / "geography.Fresh42.yaml")
            got, _why = geomod.crossing_nodes(near, far, anchors, plan=s2)
        except SystemExit as exc:
            return exc, []
    return None, sorted(got)


def _case_a_first_emit_with_no_crossing_needs_no_anchor():
    exc, got = _first_emit_crossings(False)
    if exc is not None:
        return Finding(
            check="first_emit_needs_no_unconsumed_anchor", ok=False,
            message=(f"REFUSED a first emit on a seed with no crossing, for an "
                     f"anchor nothing on it reads: {str(exc)[:110]}"),
            numbers={"crossings": 0, "anchor_supplied": False})
    return Finding(
        check="first_emit_needs_no_unconsumed_anchor", ok=True,
        message=("a first emit with no --anchor on a seed with no crossing "
                 "derived its nodes and recorded the crossing absent"),
        numbers={"crossings": 0, "abutments": len(got)})


def _case_a_crossing_costed_from_an_unsupplied_anchor_is_refused():
    exc, got = _first_emit_crossings(True)
    if exc is not None:
        return Finding(
            check="consumed_anchor_is_authored", ok=False,
            message=("a bridgeable S2 with no authored anchor was refused: "
                     f"{str(exc)[:110]}"),
            numbers={"crossings": 1, "anchor_supplied": False})
    return Finding(
        check="consumed_anchor_is_authored", ok=True,
        message=(f"NOT REFUSED: S2 was sited at {got} from an anchor nobody "
                 f"authored"),
        numbers={"crossings": 1, "abutments": len(got)})


def _neuter_anchor(mode: str):
    """RESTORE ONE WRONG KEY in `geography.resolve_anchors`: "upfront" is the
    shipped refusal of any first emit without --anchor, "invented" is a
    default anchor made up where none was authored."""
    def apply():
        geomod = _geography_mod()
        saved = geomod._ANCHOR_MODE
        geomod._ANCHOR_MODE = mode

        def undo():
            geomod._ANCHOR_MODE = saved
        return undo
    return apply


# ---------------------------------------------------------------------------
# THE OUTPUT SIDE, WHICH THE POPULATION STATEMENT ABOVE DOES NOT COVER.
#
# Every other case here is about an artefact the planner READS.  MEASURED
# 2026-09-22 by driving seed Fresh42 through `roads/network.py plan
# --provisional` on a scratch root with ALL SIX INPUT GUARDS LIVE AND PASSING:
# the run EXITED 0, stamped `seed: Fresh42 / world: Fresh42` on its
# crossings.yaml, and published ULFSLAND'S TWO BRIDGES in its `declared:` block
# -- brg-s1-e (-312, -64), brg-s1-w (-324, -64), brg-s2-w (-348, 246) -- each
# carrying `measured_by: Crossings, 1 m distance transform over
# /tmp/roads/h1m.bin`, WHICH NAMES ULFSLAND'S FIELD TWICE INSIDE A FRESH42
# ARTEFACT, plus `260 m from StartTemple` and a `poi_clear` reading of
# Ulfsland's WoodHouse7.
#
# THE RUN CONTAINED ITS OWN REFUTATION.  The SAME import had already moved 8
# edges into `NO_ROAD` because `geography.absent_nodes()` measured those four
# nodes PORTAL-ONLY at 4,926.7 m against a 96 m ceiling.  ONE POPULATION, TWO
# RULES: `EDGES` was filtered against it and `DECLARED_CROSSINGS` against
# nothing.  A check over inputs cannot see that -- every input WAS this seed's,
# and the join of six correct inputs still published another planet's bridges.
# ---------------------------------------------------------------------------

_NEUTER_DECLARED_FILTER = False


def _crossings_under_a_seed_with_no_buildable_crossing():
    """Import `spec` in a CHILD interpreter against a geography whose crossings
    were measured impossible, and report what the Crossings consumer is handed.

    A child, not a reload: `spec` filters at module scope and twelve modules in
    this process hold the imported object, so re-executing it here would hand
    every later case a different `spec` than the one it was written against.

    THE STATE UNDER TEST IS CONSTRUCTED, NOT BORROWED.  The four abutment
    records are removed and a `no_crossing` record is written with a reason
    string that CANNOT COME FROM ANY REAL EMISSION, so the reason surfacing in
    the child's output proves it travelled from this document and not from a
    Fresh42 file somebody left on the host.  The twelve remaining nodes are the
    default seed's own, unchanged: they are scaffolding the rule does not touch,
    and inventing coordinates for them would only test the invention.
    """
    import subprocess                                       # noqa: PLC0415
    import yaml                                             # noqa: PLC0415
    geomod = _geography_mod()
    reason = ("CONSTRUCTED BY THE PROVENANCE SECTION: no crossing is buildable "
              "in this document.")
    doc = yaml.safe_load(geomod.geography_path("Pirate68").read_text())
    banks = [n["id"] for n in doc["nodes"] if n.get("kind") == "abutment"]
    doc["nodes"] = [n for n in doc["nodes"] if n.get("kind") != "abutment"]
    doc["no_crossing"] = [
        {"id": cid, "node_ids": [b for b in banks if b.startswith(f"brg-{cid.lower()}-")],
         "reason": reason, "measured": {"constructed": True}}
        for cid in ("S1", "S2")]
    script = (
        "import json, sys\n"
        f"sys.path.insert(0, {str(JUMPSTART / 'roads')!r})\n"
        "import spec\n"
        "declared = [c['crossing_id'] for c in spec.DECLARED_CROSSINGS]\n"
        "unbuildable = spec.UNBUILDABLE_CROSSINGS\n"
        + ("declared += [u['crossing_id'] for u in unbuildable]\n"
           "unbuildable = []\n" if _NEUTER_DECLARED_FILTER else "")
        + "print(json.dumps({'declared': declared,"
          " 'banks': [c[s]['xz'] for c in spec.DECLARED_CROSSINGS"
          "           for s in ('bank_a', 'bank_b')],"
          " 'cites': sorted({c['measured_by'] for c in spec.DECLARED_CROSSINGS}),"
          " 'unbuildable': [{'id': u['crossing_id'], 'why': u['why_not_on_this_seed'],"
          "                  'nodes': u['absent_nodes']} for u in unbuildable]}))\n")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "geography.yaml"
        p.write_text(yaml.safe_dump(doc, sort_keys=False))
        env = dict(_mods()[0].os.environ)
        env[geomod.GEOGRAPHY_ENV] = str(p)
        out = subprocess.run([sys.executable, "-c", script], capture_output=True,
                             text=True, env=env, cwd=str(JUMPSTART / "roads"))
    if out.returncode != 0:
        raise RuntimeError(f"child spec import failed: {out.stderr[-400:]}")
    return json.loads(out.stdout), reason


def _case_an_unbuildable_crossing_is_not_published():
    got, reason = _crossings_under_a_seed_with_no_buildable_crossing()
    if got["declared"]:
        return Finding(
            check="unbuildable_crossing_not_published", ok=True,
            message=(f"NOT REFUSED: {len(got['declared'])} crossing(s) "
                     f"{', '.join(got['declared'])} published as DECLARED at "
                     f"banks {got['banks'][:2]} citing {got['cites']}, for a "
                     f"seed whose own geography measured them impossible"),
            numbers={"declared": len(got["declared"]),
                     "unbuildable": len(got["unbuildable"])})
    explained = [u for u in got["unbuildable"] if u["why"] == reason]
    if len(explained) != 2:
        return Finding(
            check="unbuildable_crossing_not_published", ok=True,
            message=(f"NOT REFUSED: nothing was published, and nothing said why "
                     f"-- {len(got['unbuildable'])} unbuildable record(s), "
                     f"{len(explained)} carrying this document's own reason. An "
                     f"absent crossing with no reason is the silent drop"),
            numbers={"declared": 0, "unbuildable": len(got["unbuildable"]),
                     "explained": len(explained)})
    return Finding(
        check="unbuildable_crossing_not_published", ok=False,
        message=(f"0 crossings published as DECLARED and both recorded "
                 f"unbuildable with this document's own measured reason, for "
                 f"nodes {sorted(n for u in explained for n in u['nodes'])}"),
        numbers={"declared": 0, "unbuildable": len(explained)})


def _neuter_the_declared_crossing_filter():
    """RESTORE THE DEFECT VERBATIM: the authored `DECLARED_CROSSINGS` list as the
    Crossings consumer read it before the filter existed -- every authored
    crossing published, nothing recorded unbuildable."""
    global _NEUTER_DECLARED_FILTER
    _NEUTER_DECLARED_FILTER = True

    def undo():
        global _NEUTER_DECLARED_FILTER
        _NEUTER_DECLARED_FILTER = False
    return undo


def _spec_mod():
    roads = JUMPSTART / "roads"
    if str(roads) not in sys.path:
        sys.path.insert(0, str(roads))
    import spec as specmod                                   # noqa: PLC0415
    return specmod


def _case_installations_are_sited_for_this_seed():
    """THE 59%.  A node id is authored; a node's COORDINATE is not.

    MEASURED 2026-09-22 on Fresh42: all 8 installation and shoreline nodes came
    back as Ulfsland's coordinates VERBATIM TO THE DECIMAL, 11 of 20 planned
    segments terminated at one, and T1-portalhub-temple ran 830.3 m from
    (-292.1, 213.2) -- a point chosen for a different planet -- to Fresh42's own
    derived temple.  The remedy is not a tolerance and not a deletion: the
    coordinate is READ from `worlds/<world>/*/placements.yaml` for the seed
    being planned, and a seed that has not sited its buildings is REFUSED.
    """
    specmod = _spec_mod()
    ghost_s, ghost_w = "NoSeedHasThisName42", "NoWorldHasThisName42"
    try:
        got = specmod.installation_xz(seed=ghost_s, world=ghost_w)
    except SystemExit as exc:
        return Finding(
            check="installations_sited_for_this_seed", ok=False,
            message=(f"a seed with no placement set and no declaration was "
                     f"refused rather than handed the default seed's "
                     f"{len(specmod.PLACEMENT_OF)} building centres: "
                     f"{str(exc)[:110]}"),
            numbers={"nodes": len(specmod.PLACEMENT_OF),
                     "declared_seeds": len(specmod.ANCHORED_INSTALLATIONS)})
    anchored = specmod.ANCHORED_INSTALLATIONS[specmod.worldseed.DEFAULT_SEED]
    same = sorted(n for n, xz in got.items()
                  if n in anchored and tuple(xz) == tuple(anchored[n]))
    return Finding(
        check="installations_sited_for_this_seed", ok=True,
        message=(f"NOT REFUSED: seed {ghost_s} was handed {len(got)} building "
                 f"centres, {len(same)} of them byte-identical to "
                 f"{specmod.worldseed.DEFAULT_SEED}'s -- "
                 f"{[got[n] for n in same[:2]]}"),
        numbers={"handed": len(got), "identical_to_default_seed": len(same)})


def _neuter_the_installation_reader():
    """RESTORE THE DEFECT VERBATIM: the 8 coordinates as frozen literals,
    answered for every seed with no placement read and no refusal -- which is
    what `spec.py` did when they were transcribed into the NODES table."""
    specmod = _spec_mod()
    saved = specmod.installation_xz
    frozen = dict(specmod.ANCHORED_INSTALLATIONS[specmod.worldseed.DEFAULT_SEED])

    def constant(seed=None, world=None):
        return dict(frozen)

    specmod.installation_xz = constant

    def undo():
        specmod.installation_xz = saved
    return undo


def _case_a_deferral_reason_must_name_a_condition_that_can_expire():
    """EXERCISED, NOT DORMANT.  Every verdict `evaluate_until` can return is
    constructed here in code.

    WHY THIS CASE EXISTS AT ALL.  `provenance` currently has SEVEN artefacts and
    ZERO unguarded rows, so the `unguarded_until` field has no live user.  A
    field that ships unexercised is the one most likely to be wrong the first
    time somebody needs it -- the accepting-direction caution applied to a
    schema.  So the fixture supplies its own rows and never borrows one.

    THE DEFECT CLASS IT SERVES: a wrong reason attached to a correct number is
    never contradicted, because nothing recomputes it.  `manual` and
    `never-tried` were both proposed as the count and both rejected as
    SELF-REPORTED -- the cheapest path is to claim you tried.  The surviving
    count is DERIVED: an `asserted` row must name a Case that carries a NEUTER,
    which `run_cases` already restores the defect against.  Gameable only by
    writing a neuter that genuinely restores the defect, which is the work.
    """
    # (1) A condition that still holds -- the row is honestly deferred.
    v, d = evaluate_until({"kind": "asserted", "check": "symbol_absent",
                           "module": "roads/geography.py",
                           "symbol": "a_symbol_that_is_not_there_xyzzy"})
    assert v == "still_deferred", (v, d)

    # (2) THE CONDITION HAS EXPIRED.  `require_scope` IS in worldseed.py, so a
    #     row deferring until it appears is asserting something already false.
    v, d = evaluate_until({"kind": "asserted", "check": "symbol_absent",
                           "module": "worldseed.py", "symbol": "require_scope"})
    assert v == "expired", (v, d)
    assert "IS NOW PRESENT" in d, d

    # (3) UNRESOLVABLE IS NOT A PASS.  A misspelled module and an unknown check
    #     both sit green forever if they are merely counted, which is
    #     `missing: []` inside the field built to stop `missing: []`.
    for bad in ({"kind": "asserted", "check": "symbol_absent",
                 "module": "roads/nosuchmodule.py", "symbol": "x"},
                {"kind": "asserted", "check": "invented_check"},
                {"kind": "not_a_kind"},
                None):
        v, _d = evaluate_until(bad)
        assert v == "unresolvable", (bad, v)

    # (4) `manual` is PERMITTED and lands in its own bucket -- an honest
    #     uncheckable reason costs one increment; a fake checkable one costs
    #     the whole mechanism.
    v, _d = evaluate_until({"kind": "manual"})
    assert v == "never_tried", v

    # (5) AND THE PATH VARIANT, both directions, against this tree only.
    v, _d = evaluate_until({"kind": "asserted", "check": "path_absent",
                            "path": "roads/no_such_file_xyzzy.yaml"})
    assert v == "still_deferred", v
    v, d = evaluate_until({"kind": "asserted", "check": "path_absent",
                           "path": "roads/reemit.py"})
    assert v == "expired" and "NOW EXISTS" in d, (v, d)
    return []


# ---------------------------------------------------------------------------
# THE GROUND CACHE'S OWN PROVENANCE: which TERRAIN a cached bed height
# belongs to
# ---------------------------------------------------------------------------
# This section's rule is that a reading must declare which world it is of.
# `ground()` broke the same rule one level down: a bed height is a reading of
# the TERRAIN, and it was filed under the whole save, so the cache could
# neither reuse a reading that was still true nor notice one that was not.
# Both halves are defects and they point opposite ways, so both directions
# are cases here and each carries the neuter that restores ITS half.
#
# THE LEDGERS BELOW ARE BUILT IN CODE.  They are shaped like Ulfsland's
# records around seq 5415-5428 - that is what makes the fixture recognisable
# - but not one byte is borrowed from the file, so the cases cannot go quiet
# the day the ledger is appended to.
def _ledger(tmp: Path, records: list[dict]) -> Path:
    tmp.mkdir(parents=True, exist_ok=True)
    p = tmp / "ledger.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in records))
    return p


def _rec(seq: int, op: str, wire: list, **params) -> dict:
    return {"seq": seq, "op": op, "wire": wire, "params": params,
            "ts": f"2026-09-23T00:00:{seq % 60:02d}Z"}


def _terrain_write(seq: int, zx: int, zz: int) -> dict:
    """The shape of a real one: the compiler is deleted and rewritten."""
    return _rec(seq, "terrain_write",
                [f"deleteObjects -zone {zx} {zz} -prefab _TerrainCompiler "
                 "-force",
                 f"spawn_object _TerrainCompiler from={zx * 64},{zz * 64},0 "
                 f"data=pad_z{zx}_{zz}"],
                entries=[{"zone": [zx, zz], "centre": [zx * 64.0, zz * 64.0]}])


def _deck_plank(seq: int, x: float, z: float) -> dict:
    return _rec(seq, "spawn_plan",
                [f"spawn_object wood_floor pos={z},{x},33.5 rot=0,0,0 "
                 "from=0,0,0"],
                anchor={"x": x, "y": 33.5, "z": z}, reach_m=12.5,
                prefabs={"wood_floor": 1}, site_id="bridge-s1")


def _save(seq: int) -> dict:
    return _rec(seq, "save", ["save"], role="bridge_body_place")


_EPOCH_BASE = [_rec(1, "world_manifest", []),
               _terrain_write(2, -5, -2),
               _save(3)]


def _keys_for(before: list[dict], after: list[dict]) -> tuple:
    """The ground-cache directory name for two states of one world."""
    from .. import instruments as I                       # noqa: PLC0415
    with tempfile.TemporaryDirectory() as td:
        a = I.terrain_epoch(_ledger(Path(td) / "a", before))
        b = I.terrain_epoch(_ledger(Path(td) / "b", after))
    return a, b


# ---------------------------------------------------------------------------
# THE WHOLE TRUTH TABLE THE GROUND KEY IS HELD IN PLACE BY
# ---------------------------------------------------------------------------
# MEASURED 2026-09-23, and it is the reason this vector exists rather than
# three cases each asserting on one pair:
#
#   dependency vector this fixture can exercise      8
#   evidence the three key cases actually asserted   1-2
#
# Narrow `record_moves_bed` to miss `zones_restore` or the unfenced
# `objects_remove` and FOUR OF SIX LOSS DEPENDENCIES GO SILENTLY
# UN-ASSERTED WHILE EVERY CASE STAYS GREEN.  An arm has a POPULATION and it
# has EVIDENCE, and only the second decides anything; the population here is
# eight and the evidence was one row.  So every row is named, exercised and
# compared, and each case owns a NAMED SUBSET of them - union of the three
# subsets is all eight, and each is decided as a VERDICT rather than an
# assert, so a neuter that empties the classifier cannot satisfy any of them.
#
# `must_move` is the DANGEROUS direction: the key failing to move means a
# stale height served silently.  `not must_move` is the EXPENSIVE direction:
# the key moving needlessly costs one cold read and no truth.
def _dependency_rows() -> tuple:
    """(name, the record, must_move) for every way this key can be wrong."""
    return (
        ("terrain_write", _terrain_write(5, -5, -2), True),
        ("zones_generate",
         _rec(5, "zones_generate",
              ["zones_generate pos=-318.0,-64.0 max=81.0"],
              pos=[-318.0, -64.0]), True),
        ("zones_restore",
         _rec(5, "zones_restore", ["zones_restore pos=2368,-3328 max=1"],
              pos=[2368.0, -3328.0]), True),
        ("unscoped_deleteObjects",
         _rec(5, "retire", ["deleteObjects -near 1 2 3 20 -force"],
              pos=[1.0, 2.0, 3.0]), True),
        ("unfenced_objects_remove",
         _rec(5, "objects_clear", ["objects_remove id=* pos=1,2 max=5"],
              centre=[1.0, 2.0]), True),
        ("unknown_verb",
         _rec(5, "spawn_plan", ["hoe_level pos=-318,-64 radius=8"]), True),
        ("build_only_spawn_plan", _deck_plank(5, -318.0, -64.0), False),
        ("lone_save", _save(5), False),
    )


def _dependency_vector() -> dict:
    """name -> (must_move, did_move), all eight rows, measured now."""
    out = {}
    for name, record, must in _dependency_rows():
        a, b = _keys_for(_EPOCH_BASE, _EPOCH_BASE + [record])
        out[name] = (must, a.key != b.key)
    return out


_MUST_MOVE = tuple(n for n, _r, m in _dependency_rows() if m)
_MUST_KEEP = tuple(n for n, _r, m in _dependency_rows() if not m)
# The fail-closed rows: the ones an ALLOWLIST key ("only an op literally
# called terrain_write moves the ground") drops on the floor.
_FAIL_CLOSED = tuple(n for n in _MUST_MOVE if n != "terrain_write")

def _case_build_only_writes_share_one_ground_directory():
    """THE REUSE THE OLD KEY COULD NOT MAKE, and what it cost.

    MEASURED 2026-09-23 on /tmp/accept/Ulfsland: 81 save fingerprints, 5.2 GB,
    and the pair 245daa7f (save seq 5420) / 7632b3a5 (save seq 5428, seven
    build-only spawn_plan records later) shares 1,919 lattice vertices with
    ZERO disagreements and a maximum delta of 0.0000 m.  The wider pair
    a087af62 / 0571968d - two saves with no ledger record at all between them,
    the second one the dedicated server's own autosave - shares 421,634
    vertices, also with zero disagreements.  Under the save fingerprint those
    readings were two directories that never met, and the second of each pair
    paid a full cold read to learn the first one's answer.
    """
    # THIS CASE OWNS THE `must_keep` SUBSET OF THE EIGHT-ROW VECTOR: the two
    # ways the key can be wrong in the EXPENSIVE direction.  Deciding on the
    # whole subset rather than on one seven-plank pair is the repair for
    # evidence 1 against a population of 8; the saturation survivor that
    # used to be an `assert` on the refusing path is now simply the
    # `lone_save` row, decided as a verdict like every other.
    v = _dependency_vector()
    moved = [n for n in _MUST_KEEP if v[n][1]]
    after = _EPOCH_BASE + [_deck_plank(4 + i, -318.0 + 16 * i, -64.0)
                           for i in range(7)] + [_save(11)]
    a, b = _keys_for(_EPOCH_BASE, after)
    if moved or a.key != b.key:
        return Finding(
            check="build_only_write_keeps_the_ground_key", ok=False,
            message=("records that move no ground moved the ground cache "
                     f"key: {', '.join(moved) or 'the seven-plank deck'} "
                     f"({a.key} -> {b.key}), so a cold re-read of every "
                     "lattice vertex is about to be paid for terrain that "
                     "provably did not move"),
            numbers={"key_before": a.key, "key_after": b.key,
                     "must_keep_population": len(_MUST_KEEP),
                     "must_keep_violated": len(moved),
                     "violated": ",".join(moved),
                     "last_bed_moving_seq": b.last_move_seq})
    # NO TRAILING `assert` ON THE ACCEPTING PATH.  It used to check the
    # fixture's own `last_move_seq`, and MEASURED just now that made this
    # case come out PROVEN-BY-ASSERT under a DEGENERATE neuter: a key that
    # names nothing leaves `last_move_seq` at 0, the assert raised, and
    # `run_cases` read the raise as a flip.  The eight-row vector above
    # already exercises the fixture far harder than that one field did, so
    # the weak path goes rather than being explained.  Under a degenerate
    # neuter this case now simply ACCEPTS - the same verdict as the fixed
    # code - and the harness says THIS CASE IS NOT LOAD-BEARING.
    return []


def _case_a_terrain_write_starts_a_new_ground_directory():
    """THE OTHER HALF, and the one that must never be traded away for speed.

    MEASURED on the same cache: a087af62 (save seq 5416) against 245daa7f
    (seq 5420), with terrain_write 5417 and 5419 between them, shares 451,702
    vertices of which 11 DISAGREE, maximum delta 0.2700 m.  Eleven in four
    hundred and fifty thousand is what a wrong reuse looks like: not a crash,
    eleven wrong heights carried silently into every slope, clearance and
    freeboard number downstream.
    """
    after = (_EPOCH_BASE + [_deck_plank(4, -318.0, -64.0),
                            _terrain_write(5, -5, -2), _save(6)])
    a, b = _keys_for(_EPOCH_BASE, after)
    # THE SURVIVOR ARM, AND IT IS A VERDICT RATHER THAN AN ASSERT.
    #
    # MEASURED while writing this, twice.  First: the original neuter for
    # this case switched `record_moves_bed` off entirely, which makes the
    # key a CONSTANT - 0 of 4 dependencies survived it, so "the case
    # flipped" was satisfied by a key that names nothing.  Second, and the
    # reason this is not an `assert`: `run_cases` counts an AssertionError
    # as the other way, so a survivor expressed as an assert would have
    # RAISED under the degenerate neuter and still been stamped PROVEN
    # LOAD-BEARING.  Expressed as a REFUSAL it comes out the same way as the
    # fixed code does, the verdict does not flip, and the harness reports
    # THIS CASE IS NOT LOAD-BEARING - which is the true statement.
    # THE SURVIVOR ARM IS NOW THE WHOLE `_FAIL_CLOSED` SUBSET - five rows,
    # not one.  A neuter that empties the classifier takes all five with it;
    # the one this case names (the classifier that forgot the compiler)
    # takes only `terrain_write`, so all five must come through.  Sizing the
    # arm on the population rather than on one row is the repair for
    # evidence 2 against a population of 8.
    v = _dependency_vector()
    lost = [n for n in _FAIL_CLOSED if not v[n][1]]
    if len(lost) == len(_FAIL_CLOSED):
        return Finding(
            check="the_neuter_lost_a_dependency_and_did_not_empty_the_key",
            ok=False,
            message=("the ground key stopped separating ALL "
                     f"{len(_FAIL_CLOSED)} of {', '.join(_FAIL_CLOSED)} as "
                     "well, so it is not a key that lost the compiler - it "
                     "is a key that names nothing, and nothing here is "
                     "evidence about terrain writes"),
            numbers={"survivor_population": len(_FAIL_CLOSED),
                     "survivors_lost": len(lost)})
    if not v["terrain_write"][1] or a.key == b.key:
        return []                    # the reuse was allowed: that is the bug
    assert b.last_move_seq == 5, b
    return Finding(
        check="a_terrain_write_starts_a_new_ground_directory", ok=False,
        message=("REFUSING the reuse: a terrain_write at seq 5 rewrote a "
                 f"_TerrainCompiler, so the ground key moved {a.key} -> "
                 f"{b.key} and the cached bed heights are a reading of the "
                 "terrain that was, not the terrain that is"),
        numbers={"key_before": a.key, "key_after": b.key,
                 "last_bed_moving_seq": b.last_move_seq})


def _case_an_unrecognised_console_verb_moves_the_ground_key():
    """FAIL CLOSED, WHICH IS THE WHOLE REASON THIS KEY IS SAFE TO SHIP.

    The classifier cannot enumerate every command that will ever be sent to
    this world, so it does not try: a verb it does not recognise is
    terrain-moving.  The cost of being wrong that way is one cold read.  The
    cost of the other way is the 11 silent heights above.  This case is the
    proof that the default is the safe one and not the convenient one - and
    it is the case that will fire the day somebody adds a command.
    """
    # THE SURVIVOR ARM FIRST: the neuter for this case is the ALLOWLIST key
    # - "only a terrain_write moves the ground" - which is the tempting
    # wrong answer RoadsTerm named as candidate one.  Under it a terrain
    # write must STILL separate, or the flip below is an emptying rather
    # than the loss of the fail-closed default.
    c, d = _keys_for(_EPOCH_BASE, _EPOCH_BASE + [_terrain_write(5, -5, -2)])
    if c.key == d.key:
        # A REFUSAL AND NOT AN ASSERT: `run_cases` reads an AssertionError
        # as the other way, so an assert here would let a key that names
        # NOTHING be stamped PROVEN LOAD-BEARING.  Refusing makes the
        # verdict match the fixed code's, the flip does not happen, and the
        # harness says THIS CASE IS NOT LOAD-BEARING.
        return Finding(
            check="the_neuter_lost_a_dependency_and_did_not_empty_the_key",
            ok=False,
            message=("the ground key stopped separating a terrain_write "
                     "too, so it is not a key that dropped the fail-closed "
                     "default - it is a key that names nothing, and nothing "
                     "here is evidence about unknown verbs"),
            numbers={"survivor": "terrain_write", "key": c.key})
    # AND THE REFUSING ARM IS THE WHOLE `_FAIL_CLOSED` SUBSET, not just the
    # unknown verb.  All five are rows an ALLOWLIST key drops on the floor,
    # and asserting on one of them left the other four un-evidenced while
    # the case stayed green - evidence 2 against a population of 8.
    v = _dependency_vector()
    missing = [n for n in _FAIL_CLOSED if not v[n][1]]
    if missing:
        return []
    a, b = _keys_for(_EPOCH_BASE,
                     _EPOCH_BASE + [_rec(4, "spawn_plan",
                                         ["hoe_level pos=-318,-64 "
                                          "radius=8"])])
    return Finding(
        check="unrecognised_verb_moves_the_ground_key", ok=False,
        message=("REFUSING the reuse: all "
                 f"{len(_FAIL_CLOSED)} fail-closed rows move the key - "
                 f"{', '.join(_FAIL_CLOSED)} - so a command this classifier "
                 "has never seen is terrain-moving by default "
                 f"({a.key} -> {b.key})"),
        numbers={"key_before": a.key, "key_after": b.key,
                 "fail_closed_population": len(_FAIL_CLOSED),
                 "fail_closed_evidenced": len(_FAIL_CLOSED) - len(missing),
                 "op_called_itself": "spawn_plan"})


def _case_one_save_in_two_terrain_epochs_is_refused():
    """A SAVE IS ONE WORLD AT ONE INSTANT, SO IT IS IN ONE TERRAIN STATE.

    The hazard the shared directory creates: point the gate at a FROZEN copy
    taken before a terrain write while the live ledger has moved past it, and
    the ledger-derived key files an old save's readings into the new epoch.
    Nothing in the heights themselves says so.  What does say so is the save's
    own generation counter - `N` in `_main.N.fwl2`, bumped by
    `SaveSystem::GetSaveNumber` on every save - appearing under two keys.
    """
    from .. import instruments as I                       # noqa: PLC0415
    with tempfile.TemporaryDirectory() as td:
        cache = Path(td) / "cache"
        (cache / "ground").mkdir(parents=True)
        (cache / "ground" / "occupants.tsv").write_text(
            "aaaaaaaaaaaaaaaaaaaaaaaa\tfp_old\t681\t5420\t"
            "2026-09-23T04:10:36Z\n")
        epoch = I.TerrainEpoch(key="bbbbbbbbbbbbbbbbbbbbbbbb", head_seq=5432,
                               last_move_seq=5430, moves=738, records=5433,
                               save_fp="fp_old", save_generation=681)
        try:
            I.ground_admit(cache, epoch)
        except RuntimeError as exc:
            return Finding(
                check="one_save_in_two_terrain_epochs_is_refused", ok=False,
                message=f"REFUSED: {str(exc)[:160]}",
                numbers={"generation": 681, "epoch_a": "aaaaaaaa",
                         "epoch_b": "bbbbbbbb"})
    return []


def _neuter_the_ground_key_on_every_write():
    """RESTORE THE DEFECT VERBATIM, HALF ONE: the key as shipped, which moved
    on EVERY write.  `save_fingerprint()` hashes the whole save, so any
    record that changed a byte started a new directory - which is exactly a
    classifier that calls every record terrain-moving."""
    from .. import instruments as I                       # noqa: PLC0415
    saved = I.record_moves_bed
    I.record_moves_bed = lambda rec: "the whole save is the key"

    def undo():
        I.record_moves_bed = saved
    return undo


def _neuter_the_ground_key_blind_to_compilers():
    """RESTORE THE DEFECT VERBATIM, HALF TWO: the classifier that forgot the
    compiler.  `wire_moves_bed` stops recognising `_TerrainCompiler` on
    `spawn_object` and `deleteObjects`, so a terrain_write reads as inert and
    reuses every cached height - including the 11 that moved.

    AND IT LOSES ONE DEPENDENCY RATHER THAN EMPTYING THE KEY.  MEASURED
    while writing this, against the neuter this replaced (which switched
    `record_moves_bed` off entirely):

        dependency                     FIXED       that neuter
        terrain_write                  separates   COLLIDES
        zones_generate                 separates   COLLIDES
        unknown verb hoe_level         separates   COLLIDES
        build-only spawn_plan          collides    collides
        survivors under the neuter: 0 of 4

    0 of 4 is a DEGENERATE INDEX PASSING A ONE-DIRECTION NEUTER: the key had
    become a constant, every pair collided, and "the case flipped" was
    satisfied by a key that names nothing.  That is verdict-equality
    certifying a blind instrument, inside the machinery built to stop
    verdict-equality certifying a blind instrument.  Under THIS neuter
    `zones_generate` and the unknown verb both survive, and the case asserts
    that they do.
    """
    from .. import instruments as I                       # noqa: PLC0415
    saved = I.wire_moves_bed

    def blind(line):
        why = saved(line)
        return "" if "_TerrainCompiler" in why else why

    I.wire_moves_bed = blind

    def undo():
        I.wire_moves_bed = saved
    return undo


def _neuter_the_ground_key_allowlisted_to_terrain_write():
    """RESTORE THE DEFECT THAT WAS NEVER SHIPPED BECAUSE IT WAS REFUSED:
    "key the ground on the seq of the last `terrain_write`", which is
    candidate one in RoadsTerm's own note and the reason the work was handed
    over rather than landed.  Only an op LITERALLY CALLED `terrain_write`
    moves the key, so the fail-closed default is gone: `zones_generate`,
    an unscoped `deleteObjects` and any verb nobody has seen all read as
    inert.  A terrain_write still separates, which is what makes the flip a
    LOST DEPENDENCY and not an emptying."""
    from .. import instruments as I                       # noqa: PLC0415
    saved = I.record_moves_bed

    def allowlist(rec):
        return ("terrain_write" if (rec or {}).get("op") == "terrain_write"
                else "")

    I.record_moves_bed = allowlist

    def undo():
        I.record_moves_bed = saved
    return undo


def _neuter_the_epoch_admission():
    """RESTORE THE DEFECT: take the fingerprint's epoch on trust, which is
    what every cache in this project did until a symlink passed every
    identity check and delivered no world."""
    from .. import instruments as I                       # noqa: PLC0415
    saved = I.ground_occupants
    I.ground_occupants = lambda cache: []

    def undo():
        I.ground_occupants = saved
    return undo


# ---------------------------------------------------------------------------
# THE BOARD'S OWN PROVENANCE: EVERY REFUSAL THIS GATE CAN EMIT IS CLASSIFIED
# ---------------------------------------------------------------------------
# THIS SECTION IS WHERE IT BELONGS BECAUSE IT IS THE SAME QUESTION.  Every
# other case here asks whether a reading is ABOUT THIS RUN.  These ask
# whether the gate's own summary line is about the gate: `NO_CLASS 0` was
# read for weeks as "every refusal is classified", and it meant "the
# sections that refused tonight are classified".
#
# MEASURED 2026-09-23.  Ulfsland: 105 refusals, NO_CLASS 0, and SEVEN of
# eleven sections publishing no `termination_class` at all - they simply
# had no refusals on that world.  Fresh42, a seed the factory had never
# seen, built from nothing: 13 refusals, NO_CLASS 13, every one of them in
# those seven.  The board could triage none of them.
#
# THE DENOMINATOR IS THE SOURCE AND IT IS UNPATCHABLE BY THE NEUTERS BELOW.
# `termination.emittable` reads every `Finding(ok=False)` construction site
# reachable from a section's `run()` out of the PARSE TREE of this
# repository, across module boundaries.  A neuter can empty a declaration
# in memory; it cannot edit the files the enumerator parses.
def _case_every_emittable_refusal_is_classified():
    """THE REAL CLAIM, over the real eleven sections.

    NO ASSERT ANYWHERE IN HERE, DELIBERATELY.  `run_cases` reads an
    AssertionError from a neutered run as a proof, so a premise expressed
    as an assertion stamps this case PROVEN LOAD-BEARING whether the audit
    measured eleven sections or none.  The denominator - eleven sections,
    and the emittable set each was read for - is a VERDICT, and it comes
    out of the files on disk, which no neuter can patch.
    """
    from .. import termination as T                         # noqa: PLC0415
    return T.verdict(T.coverage(), want_sections=11)


def _case_a_section_that_can_emit_an_unnamed_refusal_is_refused():
    """THE KNOWN-BAD STATE, constructed: a section with one emittable
    refusal its own table does not name.  This is Fresh42's shape."""
    from .. import termination as T                         # noqa: PLC0415
    return T.verdict([
        dict(section="a_section", emittable=3, named=2,
             missing=["a_refusal_nobody_classed"],
             missing_sites={"a_refusal_nobody_classed":
                            ("sections.a_section.run:1",)},
             stale=[], checks=["x", "y", "a_refusal_nobody_classed"])])


def _neuter_one_sections_declaration():
    """RESTORE THE DEFECT: a section that can emit a refusal it does not
    name - which is the state all SEVEN unclassifying sections were in
    until tonight.  One entry, out of one real table, in memory only."""
    from . import spawn                                     # noqa: PLC0415
    saved = dict(spawn.TERMINATION_FAMILIES)
    spawn.TERMINATION_FAMILIES.pop("seat_surface")

    def undo():
        spawn.TERMINATION_FAMILIES.clear()
        spawn.TERMINATION_FAMILIES.update(saved)
    return undo


def _case_a_section_that_stamps_its_refusal_unknown_is_refused():
    """UNKNOWN IS NO_CLASS SPELLED AGAIN.  Drive 2's settlements shape,
    constructed: every check named, one site stamping its own row UNKNOWN,
    exactly as `terminus_unmeasured` did at settlements.run."""
    from .. import termination as T                         # noqa: PLC0415
    return T.verdict([
        dict(section="settlements", emittable=2, named=2, missing=[],
             missing_sites={}, stale=[], unclassed={},
             stamped_unknown={"terminus_unmeasured":
                              ("sections.settlements.run:2627",)},
             checks=["terminus_step", "terminus_unmeasured"])])


def _neuter_unknown_is_a_class():
    """RESTORE THE DEFECT: the audit reads a site that stamps UNKNOWN as a
    classified one, which is what made drive 2's board NO_CLASS 0."""
    from .. import termination as T                         # noqa: PLC0415
    was = T._COVERAGE_UNKNOWN_IS_A_CLASS
    T._COVERAGE_UNKNOWN_IS_A_CLASS = True

    def undo():
        T._COVERAGE_UNKNOWN_IS_A_CLASS = was
    return undo


# A FAMILY THAT HANDS ITS ROW NO CLASS IS REFUSED, NOT COUNTED.  Drive 2's
# shape, restored IN MEMORY into one REAL table and audited by the REAL
# coverage over the REAL source: harbours' `berth_depth/unread` named
# `NOT_MEASURED` (class UNKNOWN), and GateInstr's audit printed it among 23
# such checks and passed.  The injected entry is the drive-2 line verbatim.
_DRIVE2_UNREAD = (
    "NOT_MEASURED",
    "check_termination classifies every refusal this section produces off "
    "its own restoration attribution; a row arriving with no class means "
    "that pass never ran for it")


def _case_a_family_that_hands_its_row_no_class_is_refused():
    from .. import termination as T                         # noqa: PLC0415
    from . import harbours                                  # noqa: PLC0415
    saved = harbours.TERMINATION_FAMILIES["berth_depth/unread"]
    harbours.TERMINATION_FAMILIES["berth_depth/unread"] = _DRIVE2_UNREAD
    try:
        return T.verdict(T.coverage(["harbours"]))
    finally:
        harbours.TERMINATION_FAMILIES["berth_depth/unread"] = saved


def _neuter_unclassed_is_counted():
    """RESTORE THE DEFECT: the audit names a family that hands its rows no
    class, counts it in its message, and passes - GateInstr's audit, the
    one that reported 23 such checks on drive 2 and stayed green."""
    from .. import termination as T                         # noqa: PLC0415
    was = T._COVERAGE_COUNTS_UNCLASSED
    T._COVERAGE_COUNTS_UNCLASSED = True

    def undo():
        T._COVERAGE_COUNTS_UNCLASSED = was
    return undo


# AND AT RUNTIME: A ROW THAT ARRIVES UNKNOWN LEAVES THE BOARD CLASSED, AND
# THE SECTION IS REFUSED FOR IT.  Drive 2's roads board, constructed: six
# rows stamped UNKNOWN in place by `road_termination`, which `backstop`
# read as "already carries a class" and passed.  The static audit makes
# that unreachable on the shipped tree; this is the property on the run.
def _case_a_refusal_that_arrives_unknown_leaves_the_board_classed():
    from .. import termination as T                         # noqa: PLC0415
    from ..registry import Finding                          # noqa: PLC0415
    from .settlements import TERMINATION_CLASSES            # noqa: PLC0415
    rows = [Finding(check="crossings", ok=False, message="S4-x01, no deck",
                    numbers=dict(crossing="S4-meadhall-southeast-x01",
                                 termination_class="UNKNOWN",
                                 remedy_is_a_deck=True)),
            Finding(check="traversability", ok=False, message="S4 341",
                    numbers=dict(inside_crossing="S4-meadhall-southeast-x01",
                                 termination_class="UNKNOWN"))]
    board = list(rows)
    T.backstop("roads", board)
    got = [(f.numbers or {}).get("termination_class") for f in rows]
    routable = [c for c in got
                if c in TERMINATION_CLASSES and c != "UNKNOWN"]
    loud = [f for f in board if f.check == "termination" and not f.ok
            and (f.numbers or {}).get("termination_class")
            not in (None, "UNKNOWN")]
    ok = len(routable) == len(rows) and len(loud) == 1
    return [Finding(
        check="every_refusal_on_the_board_carries_a_class", ok=ok,
        message=(f"{len(rows)} roads rows arrived UNKNOWN and left the "
                 f"backstop as {got}, with {len(loud)} classed `termination` "
                 f"refusal naming them" if ok else
                 f"{len(rows) - len(routable)} of {len(rows)} rows that "
                 f"arrived UNKNOWN reached the board with no routable class "
                 f"({got}), and {len(loud)} `termination` refusal(s) said "
                 f"so - drive 2's roads board, 6 UNKNOWN of 18"),
        measured=float(len(routable)), limit=float(len(rows)),
        numbers=dict(classes=got, termination_refusals=len(loud)),
        population="two drive-2 roads rows stamped UNKNOWN in place")]


def _neuter_backstop_keeps_unknown():
    """RESTORE THE DEFECT: UNKNOWN is read as a class the row already has."""
    from .. import termination as T                         # noqa: PLC0415
    was = T._BACKSTOP_KEEPS_UNKNOWN
    T._BACKSTOP_KEEPS_UNKNOWN = True

    def undo():
        T._BACKSTOP_KEEPS_UNKNOWN = was
    return undo



def _neuter_the_coverage_to_trust_the_section():
    """RESTORE THE DEFECT ONE LEVEL UP: audit the section's own declaration
    instead of auditing it AGAINST THE SOURCE.  That is exactly the key
    this whole repair is about - the claim keyed on what a section says
    rather than on what it can emit."""
    from .. import termination as T                         # noqa: PLC0415
    T._COVERAGE_TRUSTS_THE_SECTION = True

    def undo():
        T._COVERAGE_TRUSTS_THE_SECTION = False
    return undo


# WHAT THE ENUMERATED POPULATION DEPENDS ON, AND THE PROOF THAT IT DEPENDS
# ON IT - WITH A SURVIVOR, BECAUSE A ONE-DIRECTION PROOF IS PASSED BY AN
# INDEX THAT RETURNED NOTHING.
#
# `EMITTABLE_DEPENDS_ON` names the CALL GRAPH, not the section file.  Three
# sections - townsites, annulus, roads - emit refusals they do not contain:
# `instruments.read_scoped_corpus` raises them on those sections' behalf and
# they land on those sections' boards.  An enumerator that read one file
# would miss every one of them and report FULL COVERAGE while the hole was
# still there.
#
# So the case measures BOTH ARMS of one neuter, and BOTH are VERDICTS
# rather than assertions: with cross-module reachability off, townsites
# must LOSE `corpus_is_about_this_world` AND KEEP
# `published_sites_are_the_declared_set`.  A neuter that merely EMPTIED the
# enumerator would take the survivor too, and this case says so in the
# finding instead of stamping itself PROVEN off an AssertionError the
# harness cannot tell from a blind one.
def _enumerator_arms():
    from .. import termination as T                         # noqa: PLC0415
    can = T.emittable("townsites")
    return ("corpus_is_about_this_world" in can,
            "published_sites_are_the_declared_set" in can, sorted(can))


def _case_the_enumerator_reads_the_call_graph_not_one_file():
    from ..registry import Finding                          # noqa: PLC0415
    lost, survivor, got = _enumerator_arms()
    ok = lost and survivor
    return [Finding(
        check="enumerator_reads_the_call_graph", ok=ok,
        message=(
            "townsites' emittable set holds both the refusal its own file "
            "raises and the one instruments.read_scoped_corpus raises for "
            f"it: {got}" if ok else
            ("the CROSS-MODULE arm is gone - townsites can emit "
             "corpus_is_about_this_world through read_scoped_corpus and the "
             "enumerator no longer sees it" if not lost else "")
            + ("; THE SURVIVOR IS GONE TOO, so this enumerator was EMPTIED "
               "rather than made blind to one dependency, and nothing about "
               "the call graph is proved by it"
               if not survivor else "")
            + f" -- got {got}"),
        measured=float(lost + survivor), limit=2.0,
        numbers=dict(cross_module_arm=lost, own_file_arm=survivor,
                     emittable=got),
        population="townsites' emittable refusal set, one arm from its own "
                   "file and one from a shared instrument")]


def _neuter_the_enumerator_to_one_file():
    """RESTORE THE DEFECT: read the section file and nothing it calls."""
    from .. import termination as T                         # noqa: PLC0415
    T._ENUMERATOR_READS_ONE_FILE = True
    T._MODS.clear()

    def undo():
        T._ENUMERATOR_READS_ONE_FILE = False
        T._MODS.clear()
    return undo


register(Section(
    name="provenance",
    measures=("that every artefact the planner reads whose content is a "
              "function of the world seed DECLARES which seed it was measured "
              "on, and that the declaration is this run's seed -- so a plan is "
              "never the join of two different planets"),
    instrument=("each artefact's own header, read by its own reader: "
                "roads/field.py::header, roads/poi.py::dump_seed, and the "
                "world/seed keys settlements/plan.py writes into sites.yaml; "
                "compared by worldseed.require_scope"),
    population=("every seed-scoped artefact this build reads, named one by one "
                "in ARTEFACTS and never discovered by a predicate -- because "
                "'the predicate happened to miss it' is how the second one "
                "survived the first being fixed: settlements/sites.yaml, the "
                "generator location dump, the PatchScan height field, every "
                "worlds/<World>/<preset>/placements.yaml, the seedscan "
                "ZoneSystem dump, roads/segments.yaml and roads/geography.yaml. "
                "ALL of them now reach a reader REQUIRED to refuse a foreign "
                "scope -- segments.yaml was the last, twelve readers and zero "
                "header reads, and it was reported ADVISORY and UNOWNED for a "
                "day rather than omitted, because shrinking the population is "
                "the cheapest way to pass a population check. Both counts are "
                "asserted, so the next artefact cannot be added silently in "
                "either direction. "
                "AND THE POPULATION IS NOW TWO-SIDED. Every row above is an "
                "artefact this build READS, and a run that consumes only its "
                "own inputs can still EMIT another world's coordinates: "
                "MEASURED 2026-09-22, a Fresh42 plan with all six input guards "
                "passing published Ulfsland's two bridges and all 8 of "
                "Ulfsland's building centres, 59% of its own paved metreage. "
                "The last two cases are that side -- what the run WROTE -- and "
                "they are single-population by construction: each is decided "
                "from the emitted value plus a declaration this run already "
                "holds for this seed, never from another world's plan"),
    run=run,
    cases=[
        Case("foreign_location_dump_is_refused", True,
             _case_foreign_dump_is_refused,
             prior_state=("2026-09-21: VH_SEED=Fresh42 network.py plan loaded "
                          "12,301 locations from "
                          "/tmp/settle/loc3/f6fe167f4fcd.json, whose header "
                          "reads seedNameFromWorld: Pirate68, and masked a "
                          "fresh seed's corridors against Ulfsland's temples "
                          "and crypts without a word"),
             neuter=_neuter_the_dump_reader,
             neuters="poi.load as it stood: parse the header, never read seed"),
        Case("foreign_solved_site_set_is_refused", True,
             _case_foreign_site_set_is_refused,
             prior_state=("2026-09-21: the same run adopted all 16 Ulfsland "
                          "sites at Ulfsland's coordinates -- stenvik "
                          "(496.0, 904.0), vestvik (-906.0, 1846.0) -- from a "
                          "sites.yaml whose first three lines read world: "
                          "Ulfsland / seed: Pirate68 / seed_hash: 147627509, "
                          "while spec.PIN_SCOPE correctly applied 0 pins"),
             neuter=_neuter_the_site_reader,
             neuters="load_solved_sites with the require_scope call removed"),
        Case("undeclared_artefact_is_refused", True,
             _case_undeclared_dump_is_refused,
             prior_state=("no artefact on this host lacks the field, which is "
                          "why it must be pinned: 'it did not say' is the "
                          "same permission as 'it said Pirate68'"),
             neuter=_neuter_the_dump_reader,
             neuters="poi.load as it stood: parse the header, never read seed"),
        Case("this_seeds_dump_is_accepted", False,
             _case_this_seeds_dump_is_accepted,
             prior_state=("the direction a refuse-everything guard passes: it "
                          "would block the correct build too")),
        Case("scope_is_read_from_the_header_not_the_path", False,
             _case_scope_is_read_from_the_header_not_the_path,
             prior_state=("f6fe167f4fcd.json is Pirate68's own content hash "
                          "and reads like provenance; it is not one")),
        Case("every_artefact_names_a_reader_required_to_refuse", False,
             _case_every_artefact_names_a_reader_required_to_refuse,
             prior_state=("three seed-scoped inputs, two guards: the field and "
                          "the census refused, the dump and the site set did "
                          "not, and nothing anywhere counted the set")),
        Case("gate_refuses_another_worlds_save", True,
             _case_gate_refuses_another_worlds_save,
             prior_state=("2026-09-21, the first time this gate was pointed "
                          "at a world that is not Ulfsland: accept --world "
                          "Fresh42 built a Ctx whose ledger was "
                          "ledger/runs/Fresh42/ledger.jsonl and whose save "
                          "was a snapshot of worlds_local/Ulfsland, "
                          "fingerprint 038e9256ad5f5c873a7733951577b6b00f, "
                          "filed under /tmp/accept/Fresh42/"),
             neuter=_neuter_the_gate_save_check,
             neuters="Ctx taking --world-src on trust"),
        Case("gate_accepts_a_frozen_copy_of_this_world", False,
             _case_gate_accepts_this_worlds_save,
             prior_state=("every authoritative verdict this project takes is "
                          "measured on a frozen copy; a check that refused "
                          "one would stop the gate rather than protect it")),
        Case("gate_refuses_a_same_named_save_of_another_world", True,
             _case_gate_refuses_a_same_named_save_of_another_world,
             prior_state=("2026-10-05, the from-scratch Ulfsland regeneration: "
                          "ribbon.py's regrade census froze the LIVE "
                          "Ulfsland's save (uid 2212643513, .fwl2 sha256 "
                          "8833746a...) while the ledger pinned UlfStaging's "
                          "(uid 1008370488, d31017ca...), both named Ulfsland "
                          "on Pirate68; 19 of 30 road legs were refused for "
                          "burying pieces that stand only in the live world"),
             neuter=_neuter_the_ledger_identity_check,
             neuters="the save identified by its directory name alone"),
        Case("gate_accepts_the_ledgers_own_save_under_every_pin", False,
             _case_gate_accepts_the_ledgers_own_save_under_every_pin,
             prior_state=("Ulfsland's live .fwl2 hashes to 8833746a... while "
                          "its ledger pins c5fdbf8f...: a hash-only pin that "
                          "differs is the same world, so it cannot refuse"),
             neuter=_neuter_the_ledger_identity_check,
             neuters="the save identified by its directory name alone"),
        Case("one_install_root_serves_every_reader", False,
             _case_one_install_root_for_every_reader,
             prior_state=("the ledger manifest read $VALHEIM_ROOT while "
                          "accept/instruments.py and terraform/rcon.py read "
                          "$VALHEIM_WORLD_ROOT; the regeneration's environment "
                          "set only $VALHEIM_ROOT"),
             neuter=_neuter_the_split_install_root,
             neuters="a resolver that reads $VALHEIM_WORLD_ROOT alone"),
        Case("a_run_hands_its_children_the_root_it_was_given", False,
             _case_a_run_hands_its_children_the_root_it_was_given,
             prior_state=("buildstage run --world-root /media/big4/.../"
                          "UlfStaging: Scope.env() dropped it, so ribbon.py "
                          "resolved the production install for itself"),
             neuter=_neuter_the_dropped_world_root,
             neuters="Scope.env() without the install-root line"),
        Case("producer_stamps_the_seed_it_ran_on", False,
             _case_producer_stamps_the_seed_it_ran_on,
             prior_state=("settlements/plan.py:241 wrote the literal "
                          "{'seed': 'Pirate68', 'seed_hash': 147627509} and "
                          ":520 the literal world: Ulfsland, with no --seed on "
                          "the module, so every sites.yaml ever produced "
                          "declared Pirate68 whatever it was solved on and "
                          "every check above would have read a constant")),
        Case("an_unbuilt_world_is_not_a_crash_and_is_not_a_pass", False,
             _case_an_unbuilt_world_is_not_a_crash_and_is_not_a_pass,
             prior_state=("2026-09-21, driving a fresh seed: with no "
                          "ledger/runs/<World>/ledger.jsonl three sections "
                          "RAISED instead of reporting an empty population -- "
                          "sections/roads.py:71 and sections/structures.py:380 "
                          "and :446 on ledger.read_text(), lighting through "
                          "the same helper -- so the gate reported a "
                          "FileNotFoundError where the true statement is "
                          "'nothing has been built in this world yet'"),
             neuter=_neuter_the_no_build_branch,
             neuters="accept/__main__.py with the no-build branch cut out of "
                     "its source, so the sections raise one by one again"),
        Case("foreign_road_geography_is_refused", True,
             _case_foreign_road_geography_is_refused,
             prior_state=("until 2026-09-21 there was no file to refuse: 16 "
                          "road-network coordinates were LITERALS in "
                          "roads/spec.py -- northcape (758.0, 2090.0), "
                          "brg-s1-w (-325.0, -64.0), temple (-64.68, 3.31) -- "
                          "so every seed got Ulfsland's capes and Ulfsland's "
                          "bridges and nothing anywhere could have noticed"),
             neuter=_neuter_the_geography_reader,
             neuters="geography.load answering from the frozen literals, which "
                     "is what spec.py did when they were constants"),
        Case("undeclared_road_geography_is_refused", True,
             _case_undeclared_road_geography_is_refused,
             prior_state=("the constants declared nothing at all, which is the "
                          "case this pins: a guard that only catches a WRONG "
                          "declaration would not have caught them"),
             neuter=_neuter_the_geography_reader,
             neuters="geography.load answering from the frozen literals, which "
                     "is what spec.py did when they were constants"),
        Case("road_geography_is_derived_and_not_a_constant", True,
             _case_road_geography_is_derived_and_not_a_constant,
             prior_state=("MEASURED 2026-09-21: roads/spec.py held 43 literal "
                          "xz tuples, 21 of them geography. The derivations "
                          "existed and were seed-parameterised -- "
                          "roads/survey.py::survey_patch for the 11 "
                          "extremities, crossings/survey.py for the 4 "
                          "abutments, roads/poi.py::load for the spawn point "
                          "-- and NOTHING CALLED THEM. They now do, and they "
                          "reproduce all 16 literals exactly on Pirate68"),
             neuter=_neuter_the_geography_reader,
             neuters="geography.load answering from the frozen literals for "
                     "every seed, so a Fresh42 run gets Ulfsland's coordinates "
                     "back and the case fails in both halves"),
        Case("an_absent_crossing_carries_its_reason", True,
             _case_an_absent_crossing_carries_its_reason,
             prior_state=("MEASURED 2026-09-21 on Fresh42: the two trunk "
                          "landmasses are separate islands with 4,926.7 m of "
                          "open water between them against a 96 m span ceiling, "
                          "so S1 and S2 do not exist and their four abutment "
                          "nodes cannot be sited. Before the record existed, "
                          "the derivation simply refused -- right verdict, "
                          "wrong diagnosis -- and a document that never "
                          "considered crossings was indistinguishable from one "
                          "that considered them and found none"),
             neuter=_neuter_absent_is_empty,
             neuters="absent_nodes with the presence check dropped, so a "
                     "missing record reads as an empty one and an edge may "
                     "lose a terminus with nothing recorded"),
        Case("a_first_emit_with_no_crossing_needs_no_anchor", False,
             _case_a_first_emit_with_no_crossing_needs_no_anchor,
             prior_state=("MEASURED 2026-09-25, Fresh42 from scratch: "
                          "pipeline stage 5 `geography.py emit` refused "
                          "`--anchor is required on a first emit` (rc 1, "
                          "0.8 s) on a seed whose S1 and S2 are both PORTAL "
                          "ONLY -- the anchor is read by no crossing it has, "
                          "and stage 5 passes no --anchor, so no fresh seed "
                          "could pass stage 5 without a human typing one"),
             neuter=_neuter_anchor("upfront"),
             neuters="resolve_anchors refusing any first emit without "
                     "--anchor, which is what emit shipped"),
        Case("a_crossing_costed_from_an_unsupplied_anchor_is_refused", True,
             _case_a_crossing_costed_from_an_unsupplied_anchor_is_refused,
             prior_state=("the refusal that must survive the fix: on Pirate68 "
                          "S2 IS bridgeable and costed from the portal hall "
                          "(-292.5, 213.5), so an emit with no authored "
                          "anchor must still stop at that crossing"),
             neuter=_neuter_anchor("invented"),
             neuters="resolve_anchors inventing (0, 0) where none was "
                     "authored, so S2 is sited from a made-up hall"),
        Case("an_unbuildable_crossing_is_not_published", True,
             _case_an_unbuildable_crossing_is_not_published,
             prior_state=("MEASURED 2026-09-22 by driving Fresh42 end to end "
                          "with every input guard live and passing: the run "
                          "exited 0, stamped seed: Fresh42 / world: Fresh42, "
                          "and published S1 and S2 as DECLARED at Ulfsland's "
                          "banks -- brg-s1-e (-312,-64), brg-s1-w (-324,-64), "
                          "brg-s2-w (-348,246) -- citing "
                          "/tmp/roads/h1m.bin, Ulfsland's field, TWICE inside "
                          "a Fresh42 artefact. The same import had already "
                          "dropped 8 EDGES for those four nodes: one "
                          "population, two rules, and only the edges were "
                          "filtered"),
             neuter=_neuter_the_declared_crossing_filter,
             neuters="the authored DECLARED_CROSSINGS as the Crossings "
                     "consumer read it before the filter existed: every "
                     "crossing published, nothing recorded unbuildable"),
        Case("installations_are_sited_for_this_seed", True,
             _case_installations_are_sited_for_this_seed,
             prior_state=("MEASURED 2026-09-22 on Fresh42: all 8 installation "
                          "and shoreline nodes came back as Ulfsland's "
                          "coordinates VERBATIM TO THE DECIMAL, 11 of 20 "
                          "segments terminated at one, and that is 12,849 of "
                          "21,938 m -- 59% of the paved metreage. "
                          "T1-portalhub-temple ran 830.3 m from Ulfsland's "
                          "portal hall to Fresh42's own derived temple in ONE "
                          "segment. worlds/<world>/*/placements.yaml declares "
                          "world and seed and is scope-checked where it is "
                          "read; spec.py had never opened it"),
             neuter=_neuter_the_installation_reader,
             neuters="installation_xz answering from the frozen literals for "
                     "every seed, with no placement read and no refusal, which "
                     "is what spec.py did when they were transcribed into "
                     "NODES"),
        Case("a_deferral_reason_must_name_a_condition_that_can_expire", False,
             _case_a_deferral_reason_must_name_a_condition_that_can_expire,
             prior_state=("2026-09-22, the one defect class of the night with "
                          "no instrument: a WRONG REASON attached to a CORRECT "
                          "NUMBER is never contradicted, because nothing "
                          "recomputes it. An emission row read 'not mine to "
                          "land inside another agent's module' about a module "
                          "its own owner had created that evening -- found "
                          "only when that owner happened to reread their own "
                          "entry. Design SurveyNodes, broken three times by "
                          "its author and twice by peers; `manual` and "
                          "`never-tried` both rejected as SELF-REPORTED, the "
                          "surviving count derived from neuter machinery that "
                          "already runs")),
        Case("build_only_writes_share_one_ground_directory", False,
             _case_build_only_writes_share_one_ground_directory,
             prior_state=("2026-09-23: /tmp/accept/Ulfsland held 81 save "
                          "fingerprints and 5.2 GB, mostly re-reads of "
                          "identical ground. The ground cache was keyed on "
                          "save_fingerprint(), the hash of the WHOLE save, "
                          "while the quantity it holds is a function of the "
                          "terrain alone -- so seven build-only spawn_plan "
                          "records (seq 5421-5427) invalidated 540,642 "
                          "cached lattice vertices that could not have "
                          "changed, and bought a 36,644-point cold read"),
             neuter=_neuter_the_ground_key_on_every_write,
             neuters="the key as shipped: every record moves it, which is "
                     "what hashing the whole save amounts to"),
        Case("a_terrain_write_starts_a_new_ground_directory", True,
             _case_a_terrain_write_starts_a_new_ground_directory,
             prior_state=("MEASURED 2026-09-23 by intersecting two "
                          "fingerprints' point indexes: a087af62 (save seq "
                          "5416) vs 245daa7f (seq 5420), terrain_write 5417 "
                          "and 5419 between them -- 451,702 shared vertices, "
                          "11 DISAGREEMENTS, max delta 0.2700 m. That is what "
                          "a wrong reuse looks like: not a crash, eleven "
                          "wrong heights in four hundred and fifty thousand"),
             neuter=_neuter_the_ground_key_blind_to_compilers,
             neuters="the classifier that forgot the compiler: "
                     "_TerrainCompiler stops being recognised on "
                     "spawn_object and deleteObjects, so a terrain_write "
                     "reuses every cached height including the 11 that "
                     "moved -- while zones_generate SURVIVES, which is what "
                     "makes the flip a lost dependency and not an emptying "
                     "(the neuter this replaced left 0 of 4 survivors)"),
        Case("an_unrecognised_console_verb_moves_the_ground_key", True,
             _case_an_unrecognised_console_verb_moves_the_ground_key,
             prior_state=("the classifier cannot enumerate every command "
                          "this world will ever be sent, so the default has "
                          "to be the safe one. Ulfsland's 5,433 records use "
                          "nine verbs today; a tenth costs one cold read "
                          "under this default and eleven silent heights "
                          "under the other"),
             neuter=_neuter_the_ground_key_allowlisted_to_terrain_write,
             neuters="the key that was refused rather than shipped: the seq "
                     "of the last op LITERALLY CALLED terrain_write, so the "
                     "fail-closed default is gone and zones_generate, an "
                     "unscoped deleteObjects and any unknown verb all read "
                     "inert -- while a terrain_write still separates"),
        Case("one_save_in_two_terrain_epochs_is_refused", True,
             _case_one_save_in_two_terrain_epochs_is_refused,
             prior_state=("the hazard a SHARED directory creates and a "
                          "per-fingerprint one could not: point the gate at a "
                          "frozen copy taken before a terrain write while the "
                          "live ledger has moved past it, and the "
                          "ledger-derived key files an old save's heights "
                          "into the new epoch. The save's own generation "
                          "counter, N in _main.N.fwl2, is what says so"),
             neuter=_neuter_the_epoch_admission,
             neuters="ground_occupants answering with nothing, i.e. the "
                     "cache taking a fingerprint's epoch on trust"),
        Case("every_emittable_refusal_is_named_by_its_section", False,
             _case_every_emittable_refusal_is_classified,
             prior_state=(
                 "2026-09-23, the board on two worlds: Ulfsland 105 "
                 "refusals NO_CLASS 0 with SEVEN of eleven sections "
                 "publishing no termination_class at all, and Fresh42 - a "
                 "seed the factory had never seen, provisioned from "
                 "nothing and built - 13 refusals NO_CLASS 13, every one of "
                 "them in those seven. The zero on Ulfsland was never "
                 "coverage, it was which sections happened to refuse"),
             neuter=_neuter_one_sections_declaration,
             neuters="one real section left able to emit a refusal it does "
                     "not name - spawn/seat_surface - which is the state "
                     "all seven unclassifying sections shipped in"),
        Case("a_section_that_can_emit_an_unnamed_refusal_is_refused", True,
             _case_a_section_that_can_emit_an_unnamed_refusal_is_refused,
             prior_state=(
                 "Fresh42's shape, constructed: a section whose source can "
                 "raise a refusal its own remedy table does not name, so "
                 "the row reaches the board as NO_CLASS"),
             neuter=_neuter_the_coverage_to_trust_the_section,
             neuters="the audit keyed on the section's own declaration "
                     "instead of on what its source can emit - the same "
                     "superset key one level up"),
        Case("a_section_that_stamps_its_refusal_unknown_is_refused", True,
             _case_a_section_that_stamps_its_refusal_unknown_is_refused,
             prior_state=(
                 "drive 2, frozen Fresh42 a521b70356e7: settlements "
                 "terminus_unmeasured named by its section's table - "
                 "coverage green, NO_CLASS 0 - and stamped UNKNOWN at its "
                 "own construction site: the board's only two UNKNOWN rows"),
             neuter=_neuter_unknown_is_a_class,
             neuters="the NO_CLASS-only audit as it stood on drive 2: a "
                     "named check passes whatever class its site stamps"),
        Case("a_family_that_hands_its_row_no_class_is_refused", True,
             _case_a_family_that_hands_its_row_no_class_is_refused,
             prior_state=(
                 "drive 2, frozen Fresh42 a521b70356e7: 23 checks named "
                 "NOT_MEASURED (class UNKNOWN) - harbours 16, roads 5, "
                 "spawn 1, structures 1 - counted by the coverage audit and "
                 "passed; roads then put 6 UNKNOWN rows on the board"),
             neuter=_neuter_unclassed_is_counted,
             neuters="the audit as GateInstr left it: a family that hands "
                     "its rows no class is counted in the message and passed"),
        Case("a_refusal_that_arrives_unknown_leaves_the_board_classed", False,
             _case_a_refusal_that_arrives_unknown_leaves_the_board_classed,
             prior_state=(
                 "drive 2, frozen Fresh42 a521b70356e7: roads 18 refusals, "
                 "6 UNKNOWN - S4-x01 and T3-x01 undecked, and the four S4 "
                 "steps inside S4-x01's span - with backstop filling 0"),
             neuter=_neuter_backstop_keeps_unknown,
             neuters="the backstop as shipped: a row stamped UNKNOWN "
                     "'already carries a class' and is passed through"),
        Case("the_enumerator_reads_the_call_graph_not_one_file", False,
             _case_the_enumerator_reads_the_call_graph_not_one_file,
             prior_state=(
                 "three sections - townsites, annulus, roads - emit "
                 "refusals their own files do not contain: "
                 "instruments.read_scoped_corpus raises them on those "
                 "sections' behalf and they land on those sections' "
                 "boards. A one-file enumerator reports FULL COVERAGE with "
                 "the hole still open"),
             neuter=_neuter_the_enumerator_to_one_file,
             neuters="the enumerator reading the section file and nothing "
                     "it calls; the case's SURVIVOR arm - the refusal the "
                     "section's own file raises - is what separates that "
                     "from an enumerator emptied outright"),
    ],
))
