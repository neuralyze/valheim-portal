#!/usr/bin/env python3
"""WORLD ACCEPTANCE GATE - a built world is not finished until a walker can
use it.

WHY THIS EXISTS.  Every defect a human found in this world had already been
declared correct by a check: "WALKABLE, worst 8 m gradient 0.0801, 0 holes,
0 gaps" over 84 impassable locations; "349/349 at tolerance 0" over 480
inverted roofs; "184 objects left standing" over 48 bodies in the
carriageway; "portal hall -> Vestvik 3,248.7 m" over a 6.12 m wall.  Those
checks did not merely miss the defects, they COULD NOT HAVE CAUGHT THEM, and
the four reasons are the rules the whole gate is built on:

  1. THE REFERENCE MUST NOT COME FROM THE ARTEFACT UNDER TEST.  Everything
     here is read back out of the SAVE through the engine, or out of the
     SEED; never out of the plan, the ledger's heights, or the datum a write
     chose.
  2. THE BASELINE MUST BE THE ONE THE FAILURE HAPPENS ON.  A 6 m cliff
     averaged over an 8 m baseline is a gradient of 0.75 and passes a 0.78
     limit.
  3. A COUNT MUST NAME ITS POPULATION.  Every section declares its
     population as a registration requirement and cannot be added without
     one.
  4. A CHECK MUST HAVE A STATE IT IS REQUIRED TO REFUSE.  A section with no
     failing selftest case cannot be registered and the gate will not start.

WHY IT IS SECTIONED AND NOT IN roads/.  It never was a road gate: two of its
original four checks - orientation, over 385 inverted panels in 74 bodies
world-wide, and obstruction, over structures standing in corridors - are
world properties that happen to have been written while looking at a road.
Each defect class this project has produced gets a section, each section
declares what it measures, on which instrument, over which population, and
each proves itself in BOTH DIRECTIONS with no world and no server.

WHAT IT NEVER DOES: it never writes to a world, it never takes the write
token, it never opens the live save (every instrument works on a COPY), and
it never deletes anything.  It is a read.

Usage:
    tools/jumpstart/accept/__main__.py                    # every section
    tools/jumpstart/accept/__main__.py --section roads
    tools/jumpstart/accept/__main__.py --list             # what each asserts
    tools/jumpstart/accept/__main__.py --selftest         # both directions
    tools/jumpstart/accept/__main__.py --json /tmp/accept.json
    python3 -m accept ...                                 # from tools/jumpstart

Exit code 0 only if EVERY selected section passes over the scoped population.

EXIT 1 IS THE PRODUCT, NOT A BREAKAGE.  A gate that refuses a world exits
non-zero, so every supervisor, CI step and job runner will report a CORRECT
REFUSAL AS A FAILED PROCESS - `hub ps` showed this run as "failed exit=1"
while it was working exactly as designed.  Anyone wiring this into
automation must distinguish the three outcomes before treating non-zero as
breakage:
    0   every selected section passed
    1   the gate RAN and REFUSED the world; the report names what and where
    2   the gate could not run - a section would not import, an unknown
        section was asked for, or nothing is registered.  This is the only
        exit code that means the gate itself is broken.
A check whose success looks like failure to its own supervisor is a trap
with a long fuse, so it is written down here rather than learned twice.
"""
from __future__ import annotations

import argparse
import importlib
import pkgutil
import sys
import traceback
from pathlib import Path

if __package__ in (None, ""):                              # run by path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "accept"

from . import instruments as I                             # noqa: E402
from . import termination as T                             # noqa: E402
from .registry import (SECTIONS, Finding, SectionResult, normalise,  # noqa: E402
                       run_cases)

# Sections are discovered, not listed: a file in `sections/` that calls
# `register()` is in the gate.  The order below is only for the REPORT -
# roads first because it is the one a builder reads most - and anything not
# named here follows alphabetically, so adding a section needs no edit here.
PREFERRED_ORDER = ("roads", "structures", "harbours", "portals",
                   "settlements", "spawn", "lighting", "duplicates")


def discover(only: set[str] | None = None) -> list[str]:
    """Import every section module, or just the ones asked for.

    A MODULE THAT WILL NOT IMPORT IS A FAILURE, NOT A SKIP.  `WaterProof.cs`
    would not compile for a while and nobody noticed because nobody had run
    it; a gate that quietly drops a section it cannot import is the same
    silence with a green tick on the end.
    """
    import accept.sections as pkg                          # noqa: PLC0415
    errs: list[str] = []
    for mod in sorted(m.name for m in pkgutil.iter_modules(pkg.__path__)):
        if only is not None and mod not in only:
            continue
        try:
            importlib.import_module(f"accept.sections.{mod}")
        except Exception:                                  # noqa: BLE001
            errs.append(f"section module {mod!r} FAILED TO IMPORT:\n"
                        + traceback.format_exc())
    return errs


class Ctx:
    """Everything a section is allowed to measure with, and nothing else.

    Every instrument is LAZY and SHARED: the 491,792-ZDO census is read once
    per run no matter how many sections ask for it, and the frozen snapshot
    is taken before the first of them, so two sections cannot be looking at
    two different worlds.
    """

    def __init__(self, args):
        self.args = args
        self.cache = Path(args.cache) / args.world
        self.cache.mkdir(parents=True, exist_ok=True)
        live = Path(args.world_src or I.world_src_default(args.world))
        # THE SAVE MUST BE THIS WORLD'S SAVE.  `--world` moved the cache
        # directory and the ledger and did NOT move the save: `--world-src`
        # was an independent flag defaulting to `instruments.
        # WORLD_SRC_DEFAULT`, a hardcoded Ulfsland path (it now defaults to
        # `--world`'s own save).  MEASURED 2026-09-21, the first time this
        # gate was pointed at a world that is not Ulfsland:
        #
        #   accept --world Fresh42
        #     ledger  ledger/runs/Fresh42/ledger.jsonl        (Fresh42's)
        #     save    .../worlds_local/Ulfsland, fingerprint
        #             038e9256ad5f5c873a7733951577b6b00fba8603ef8bc6de7efb37f2d52a6c97
        #             (Ulfsland's, byte-identical to the concurrent Ulfsland
        #              run's) -- filed under /tmp/accept/Fresh42/
        #
        # i.e. a verdict that is the JOIN OF TWO WORLDS, reported as one.  It
        # never fired before because nothing had ever been run on a second
        # world, which is the whole reason this defect class keeps surviving:
        # the check is correct on the one world it has ever seen.
        #
        # The save names its own world -- `worlds_local/<World>/` is the
        # layout `valheim_worldgen.py` detects -- so the declaration is on
        # disk and reading it is the fix, exactly as it was for the location
        # dump and the solved site set.  An explicit `--world-src` whose stem
        # matches is accepted, which is how a FROZEN COPY of this world's save
        # is analysed; a frozen copy under a different name is refused with
        # both names, because that is indistinguishable from this defect.
        I.require_world_save(live, args.world)
        # AND IT MUST BE THE WORLD THE LEDGER IS ABOUT.  The name check above
        # cannot tell a same-named world on another install (the from-scratch
        # Ulfsland regeneration beside the live Ulfsland); the ledger's
        # seq-0 manifest pins the world's uid.  A world with no ledger yet is
        # `unbuilt` and has nothing to disagree with.
        I.require_save_is_the_ledgers(
            live, args.world,
            Path(args.ledger or (I.JUMPSTART / "ledger" / "runs" / args.world
                                 / "ledger.jsonl")))
        # THE SNAPSHOT IS TAKEN HERE, ONCE, BEFORE ANY SECTION RUNS.  The
        # dedicated server rewrites the live save every few minutes; batch 1
        # and batch 5 were otherwise two readings of two different worlds
        # joined by one verdict.
        self.fp = I.save_fingerprint(live)
        # THE FROZEN COPY IS NAMED AFTER THE WORLD, because `save_world_name`
        # reads the DIRECTORY NAME and `require_world_save` refuses on it.
        # MEASURED 2026-09-22: the snapshot was written to `<fp>/world`, so
        # re-running one section against this run's own frozen save - the
        # whole point of freezing it, and the only way to compare a repair
        # against the state it was sized on after the live save has moved -
        # was refused with "--world-src ... is world 'world''s save".  The
        # gate's own artefact was unreadable by the gate.  The fingerprint
        # hashes file names and contents, not the directory, so this renames
        # nothing the cache is keyed on.
        self.args.world_src = str(I.snapshot(live, self.cache / self.fp
                                             / args.world))
        self.ledger = Path(args.ledger or (I.JUMPSTART / "ledger" / "runs"
                                           / args.world / "ledger.jsonl"))
        self.ledger_dir = self.ledger.parent
        # THE GROUND CACHE IS NOT KEYED ON THE SAVE.  `self.fp` is the
        # identity of the whole save and is right for the ZDO census and the
        # string census, which ARE functions of the whole save.  A bed height
        # is not: it is a function of the terrain, so it is filed under the
        # terrain epoch and survives every build-only write.  Computed off
        # the LIVE save's own ledger, before any section runs, so one run
        # cannot straddle two epochs - the same reason the snapshot is taken
        # where it is.
        # AND A WORLD WITH NO LEDGER IS NOT AN ERROR HERE.  `main()` reports
        # an unbuilt world as the no-verdict exit 2, and it does that AFTER
        # this constructor runs; raising here would turn "nothing has been
        # built in this world yet" back into a stack trace, which is the
        # exact defect `an_unbuilt_world_is_not_a_crash_and_is_not_a_pass`
        # exists to hold shut.  The epoch is None and `ground()` refuses by
        # name if a section reaches for it anyway.
        self.epoch = (I.terrain_epoch(self.ledger, save_fp=self.fp,
                                      world_src=live)
                      if self.ledger.exists() else None)
        self.scope = {s.strip() for s in args.scope.split(",") if s.strip()}
        self.WATER_LEVEL = I.WATER_LEVEL
        self._census = None
        self._geom = None
        self._clear = None
        self._strings = None
        self._solids = None
        self._grid = None

    # -- instruments --------------------------------------------------------
    @property
    def census(self) -> I.Census:
        if self._census is None:
            self._census = I.census(self.cache, self.fp, self.args)
        return self._census

    @property
    def walker(self) -> tuple[float, float]:
        """radius, height - MEASURED off the Player prefab this run.
        `census()` raises rather than substituting a guess, so reading this
        is what makes the refusal reachable."""
        c = self.census
        return c.walker_radius, c.walker_height

    @property
    def strings(self) -> I.Strings:
        if self._strings is None:
            self._strings = I.strings(self.cache, self.fp, self.args)
        return self._strings

    @property
    def geom(self) -> dict:
        if self._geom is None:
            self._geom = I.load_geometry()
        return self._geom[0]

    @property
    def build_pieces(self) -> set:
        if self._geom is None:
            self._geom = I.load_geometry()
        return self._geom[1]

    @property
    def clearable(self) -> set:
        if self._clear is None:
            self._clear = I.load_clearable()
        return self._clear[0]

    @property
    def not_property(self) -> set:
        if self._clear is None:
            self._clear = I.load_clearable()
        return self._clear[1]

    def ground(self, pts):
        """applied bed y for labelled points, batched, with the overlapping
        station cross-check that ABORTS the run if one station reads two
        heights."""
        if self.epoch is None:
            raise RuntimeError(
                f"REFUSING to read ground for {self.args.world}: there is no "
                f"ledger at {self.ledger}, so there is no terrain history to "
                "file the reading under.  A bed height cached with no record "
                "of which terrain it is a reading of is the cache this "
                "change exists to stop.")
        return I.ground(self.cache, self.epoch, pts, self.args)

    def generated(self, pts, step_m: float = 1.0, tile_m: float = 128.0):
        """the SEED's bed - the only reference that is not the artefact under
        test.  Raises if the instrument is unavailable; never returns a
        partial field."""
        return I.generated(self.cache, self.args, pts, step_m, tile_m)

    def solids_all(self):
        """Every ZDO in the save as a placed Solid.  World scale: a section
        that only needs a corridor should mask first, as `roads` does."""
        if self._solids is None:
            self._solids = I.solids_from_census(self.census, self.geom)
        return self._solids

    def grid_all(self):
        if self._grid is None:
            self._grid = I.Grid(self.solids_all())
        return self._grid

    def __getattr__(self, name: str):
        """Every instrument, reachable by its own name off `ctx`.

        A section asking for `ctx.rotate` gets THE rotate - the one in
        `instruments.py` - rather than writing a second one because the
        window did not expose it.  Anything not in the instrument module
        raises here and NAMES itself, so a section can refuse with the
        missing instrument in the message instead of silently doing without.
        """
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return getattr(I, name)
        except AttributeError:
            raise AttributeError(
                f"no instrument named {name!r}: ctx exposes the readings "
                "(census, ground, generated, strings, geom, clearable, "
                "solids_all, grid_all, walker) and every name in "
                "accept/instruments.py.  Ask WorldGateScope to add it there "
                "rather than writing a second implementation.") from None

    def log(self, msg: str) -> None:
        sys.stderr.write(f"[accept] {msg}\n")


# ---------------------------------------------------------------------------
def do_list(w, selected) -> int:
    """What every section asserts, WITHOUT running the gate.

    When this gate refuses a world months from now the first question will be
    "what does this section actually assert", and the answer must not require
    reading the source.
    """
    w(f"\nWORLD ACCEPTANCE GATE - {len(selected)} sections\n\n")
    for name in selected:
        s = SECTIONS[name]
        fails = s.failing_cases()
        w(f"  {name}{'  (ADVISORY)' if s.advisory else ''}\n")
        w(f"      measures    {s.measures}\n")
        w(f"      instrument  {s.instrument}\n")
        w(f"      population  {s.population}\n")
        w(f"      selftest    {len(s.cases)} cases, {len(fails)} of them "
          f"states it is REQUIRED to refuse\n")
        for c in fails:
            w(f"                  REFUSES: {c.name}\n")
            if c.prior_state:
                w(f"                           (real prior state: "
                  f"{c.prior_state})\n")
        w("\n")
    return 0


def order(sections) -> list[str]:
    known = [n for n in PREFERRED_ORDER if n in sections]
    return known + sorted(n for n in sections if n not in PREFERRED_ORDER)


def do_selftest(w, selected) -> int:
    total = bad = 0
    for name in selected:
        s = SECTIONS[name]
        rows = run_cases(s)
        w(f"  {name}\n")
        for case, ok, why in rows:
            total += 1
            bad += 0 if ok else 1
            w(f"    {'ok  ' if ok else 'FAIL'}  {case.name}"
              f"{'' if ok else '   ' + why}\n")
        # A section that cannot fail is unregisterable, so this line is a
        # restatement of a guarantee rather than a hope.
        w(f"      {len(s.failing_cases())} of {len(rows)} cases are states "
          f"this section is REQUIRED to refuse\n")
        # AND THE ABSENCE, AS A NUMBER.  `Case.neuter` is optional on
        # purpose - a mandatory field would be filled in with whatever
        # passes, which is how `retires: [0]` happened - but an absence that
        # is invisible can never shrink.  A case with no neuter is UNPROVEN
        # BY NEUTER, not wrong: nothing has shown that it still fails when
        # the defect it names is put back.  Counted here so a future agent
        # can drive it down, and so it visibly RISES the day somebody adds a
        # case without one.
        proven = [c for c in s.cases if c.neuter is not None]
        w(f"      {len(proven)} of {len(rows)} PROVEN LOAD-BEARING BY "
          f"NEUTER (the case was re-run with its own discriminator "
          f"switched off and had to come out the other way); "
          f"{len(rows) - len(proven)} unproven\n")
        # AND THE SECOND ABSENCE, WHICH IS THE ONE THAT WAS INVISIBLE UNTIL
        # TONIGHT.  `run_cases` reads an AssertionError from a neutered run
        # as "the other way", and that is correct - a case whose fixture
        # invariant fires HAS been proved.  But A NEUTER THAT EMPTIES THE
        # INSTRUMENT RAISES THE SAME EXCEPTION, so the two are
        # indistinguishable to this harness.  MEASURED 2026-09-23: two
        # provenance cases shared a neuter that switched the ground-key
        # classifier off entirely; 0 of 4 dependencies survived it, the key
        # was a CONSTANT, and both were stamped PROVEN LOAD-BEARING by an
        # index naming nothing.  The repair - a SURVIVOR arm expressed as a
        # VERDICT rather than an assert - is what let the harness report
        # THIS CASE IS NOT LOAD-BEARING instead.
        #
        # So the weak form gets its own number, for exactly the reason the
        # unproven count has one: it can be driven down, and it RISES
        # visibly the day somebody lands an assert-shaped proof.
        weak = [c for c in s.cases if c.proof_mode == "assert"]
        strong = [c for c in s.cases if c.proof_mode == "verdict"]
        w(f"      of those, {len(strong)} by a FLIPPED VERDICT and "
          f"{len(weak)} only by the case's OWN ASSERTION raising - which a "
          f"neuter that EMPTIED the instrument would also produce, so those "
          f"{len(weak)} are proofs this harness cannot tell from a blind "
          f"one\n")
    w(f"\n  selftest {total - bad}/{total}  "
      f"{'PASS' if not bad else 'FAIL'}\n")
    return 0 if not bad else 1


# ---------------------------------------------------------------------------
# NO_BUILD_GATE_DEPENDS_ON: (does this invocation want a VERDICT, is there a
# build ledger).  BOTH.  Neither alone.
#
# It used to depend on the ledger alone: `if not ctx.ledger.exists(): return
# 2`, applied to every invocation, which made the LEDGER the key for reaching
# ANY reading of this world.  That is the superset key this project keeps
# finding.  `instruments.census` -- the whole-save ZDO dump -- depends on the
# SAVE and on nothing else, and it sat behind a check about the BUILD.
#
# MEASURED 2026-09-23 on the fresh seed Fresh42: `accept --section roads
# --world Fresh42 --world-src <its save>` exited 2 in 0.35 s with "Fresh42
# HAS NO BUILD LEDGER ... an empty world is not an accepted world", and the
# only remedy it could print was "build something first" -- stage 12, which
# `pipeline.py` declares as requiring the census this invocation was taking.
# A GUARD THAT CAN ONLY SPEAK AFTER THE BUILD IS A GUARD WHOSE REMEDY THE
# BUILD HAS ALREADY FORBIDDEN, and that cycle held every fresh seed shut.
#
# THE REPAIR MUST NOT OPEN THE GATE, and that is the second arm of its proof.
# An unbuilt world still gets NO_BUILD and exit 2 for anything that asks for
# a verdict; only a request that asks for a MEASUREMENT of the save is let
# past, and that request cannot return a section, a finding or an `ok`.
def wants_a_verdict(args) -> bool:
    """Does this invocation ask what the gate THINKS of the build?"""
    return not getattr(args, "zdo_census", False)


def no_build_disposition(args, ledger_exists: bool) -> str:
    """MEASURE (the save) | NO_BUILD (refuse, exit 2) | RUN (the sections)."""
    if not wants_a_verdict(args):
        return "MEASURE"
    return "RUN" if ledger_exists else "NO_BUILD"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="accept", description=__doc__.split("\n")[0])
    ap.add_argument("--world", default=I.WORLD_DEFAULT)
    ap.add_argument("--seed", default="Pirate68",
                    help="world seed, for the GENERATED-bed instrument")
    ap.add_argument("--vh-src", default=I.VH_SRC_DEFAULT)
    ap.add_argument("--world-src", default=None,
                    help="the save measured (default: --world's own, "
                         "instruments.world_src_default)")
    ap.add_argument("--sandbox", default=I.SANDBOX_DEFAULT)
    ap.add_argument("--cache", default=I.CACHE_DEFAULT)
    ap.add_argument("--ledger", default=None)
    ap.add_argument("--section", default="",
                    help="comma list of SECTIONS to run; default is all of "
                         "them, and the exit code is non-zero if ANY "
                         "selected section fails")
    ap.add_argument("--scope", default="",
                    help="comma list of segment CODES (N0,N0b,R3) to "
                         "restrict the verdict to; everything else is "
                         "measured and reported but does not decide the "
                         "exit code")
    ap.add_argument("--zone-batch", type=int, default=110)
    ap.add_argument("--json", default=None)
    ap.add_argument("--list", action="store_true",
                    help="what each section measures, on what instrument, "
                         "over what population - without running anything")
    ap.add_argument("--selftest", action="store_true",
                    help="prove every section in BOTH DIRECTIONS, with no "
                         "world and no server; exit non-zero if any case "
                         "fails")
    ap.add_argument("--zdo-census", action="store_true",
                    help="ACQUIRE AND PRINT THE WHOLE-SAVE ZDO DUMP AND "
                         "STOP. A measurement of the save, not a verdict on "
                         "it: it runs BEFORE the build-ledger check, because "
                         "the census depends on the save alone "
                         "(instruments.ZDO_CENSUS_DEPENDS_ON) while a "
                         "verdict depends on a build. This is the only way a "
                         "world with no build can be censused, and "
                         "`roads/precensus.py from_save` is the caller that "
                         "needs it")
    args = ap.parse_args(argv)
    w = sys.stdout.write

    want = {s.strip() for s in args.section.split(",") if s.strip()}
    load_errs = discover(want or None)
    # Import failures are reported FIRST.  A section that raised on import is
    # missing from the registry, and "no such section: ['roads']" would name
    # the wrong fault - the same shape as a check that reports the population
    # it happened to count rather than the one it was asked about.
    if load_errs:
        for e in load_errs:
            sys.stderr.write(e + "\n")
        w(f"\n  {len(load_errs)} SECTION MODULES FAILED TO IMPORT - a "
          "section that will not load is a check that is not running, and "
          "this gate will not report a verdict over it\n")
        return 2
    if want:
        missing = want - set(SECTIONS)
        if missing:
            # Import the rest before naming what exists: only the requested
            # modules were loaded, so the registry is not yet the answer to
            # "what sections are there".
            discover(None)
            w(f"no such section: {sorted(missing)}; registered: "
              f"{order(SECTIONS)}\n")
            return 2
    if not SECTIONS:
        w("no sections are registered - this gate asserts nothing\n")
        return 2
    # Explicit, not implied by which modules were imported: one section
    # module may import another's helper, and `--section roads` must then
    # still be roads and nothing else.
    selected = [n for n in order(SECTIONS) if not want or n in want]

    if args.list:
        return do_list(w, selected)
    if args.selftest:
        return do_selftest(w, selected)

    ctx = Ctx(args)

    # A MEASUREMENT OF THE SAVE RUNS BEFORE THE VERDICT ON THE BUILD, and
    # WHICH OF THE TWO THIS INVOCATION IS gets a name rather than an `if`.
    #
    # `Ctx.__init__` above needs no ledger -- it pins the world, fingerprints
    # the save and freezes a copy -- and the whole-save ZDO census needs
    # nothing but that (instruments.ZDO_CENSUS_DEPENDS_ON).  Nothing in the
    # MEASURE arm reads a section, a finding or a verdict, so no unbuilt
    # world can come out of it looking accepted: it prints a path and exits.
    disposition = no_build_disposition(args, ctx.ledger.exists())
    if disposition == "MEASURE":
        tsv = I.zdo_census_tsv(ctx.cache, ctx.fp, ctx.args)
        rows = sum(1 for ln in tsv.open(encoding="utf-8", errors="ignore")
                   if ln[:1] != "#" and ln.strip())
        w(f"{tsv}\n")
        w(f"  world {args.world}  save fingerprint {ctx.fp}\n")
        w(f"  {rows} ZDO rows  depends on "
          f"{', '.join(I.ZDO_CENSUS_DEPENDS_ON)}  NOT on the build ledger\n")
        if args.json:
            Path(args.json).write_text(__import__("json").dumps(
                {"world": args.world, "save_fingerprint": ctx.fp,
                 "zdos_tsv": str(tsv), "rows": rows,
                 "depends_on": list(I.ZDO_CENSUS_DEPENDS_ON)}, indent=1))
        return 0
    report: dict = dict(
        world=args.world, save_fingerprint=ctx.fp,
        scope=sorted(ctx.scope) or "ALL",
        sections_run=selected,
        instruments=dict(
            ground="run_waterproof.sh FROM_SAVE=1 -> the save's own "
                   "_TerrainCompiler ZDOs instantiated by the game, then "
                   "Heightmap.GetHeight, on a COPY of the save",
            objects="WaterProof.cs WATERPROOF_ZDOS -> ZDOMan.m_objectsByID, "
                    "the whole save in one read, no paging and no radius",
            limit=f"tan({I.SLIDE_DEG:g} deg) = {I.STEP_LIMIT_PER_M:.4f} m "
                  f"per {I.SAMPLE_M:g} m, from "
                  "Character::c_MinSlideDegreesPlayer"),
        sections={})

    # A WORLD WITH NO BUILD IS AN EMPTY POPULATION, NOT A BROKEN GATE.
    #
    # MEASURED 2026-09-21 driving a fresh seed: with no `ledger/runs/<World>/
    # ledger.jsonl`, three sections did not report an empty population -- they
    # RAISED. roads at sections/roads.py:71 and structures at
    # sections/structures.py:380 and :446 both on `ledger.read_text()`, and
    # lighting through the same helper. Their findings became
    # `section_raised`, i.e. the gate reported a FileNotFoundError where the
    # true statement is "nothing has been built in this world yet".
    #
    # That is the class Step 3 of the fresh-seed drive was looking for: A
    # CHECK THAT CANNOT RUN ON A NEW WORLD IS A CHECK THAT WAS NEVER GENERAL.
    # It is stated ONCE here rather than patched into six section modules,
    # because it is a fact about the RUN, not about any section's instrument --
    # and because six copies of "if the ledger is missing" is six things to
    # keep true.
    #
    # THE EMPTY VERDICT IS ADVISORY AND THE RUN STILL FAILS ON `--world` THAT
    # NAMES NOTHING. `ok=True, advisory=True` would let an unbuilt world read
    # as a PASS, and "this world has no build" must never be reported in the
    # same shape as "this world's build was accepted". So the gate says so and
    # returns the no-verdict exit code, ONCE, instead of nine tracebacks.
    if disposition == "NO_BUILD":
        w(f"\n  {args.world} HAS NO BUILD LEDGER at {ctx.ledger}\n"
          f"  Nothing has been built in this world, so every section's "
          f"population is empty and there is no verdict to take. This is not "
          f"a failure of the checks and it is not a pass: an empty world is "
          f"not an accepted world.\n"
          f"  Sections that would have run: {', '.join(selected)}\n"
          f"  Build something first, or pass --ledger to point at the ledger "
          f"this world's build actually wrote.\n")
        if args.json:
            report["sections"] = {n: dict(
                declares=dict(measures=SECTIONS[n].measures,
                              instrument=SECTIONS[n].instrument,
                              population=SECTIONS[n].population),
                refusals=0, advisory_findings=0, verdict="NO_BUILD")
                for n in selected}
            report["no_build"] = str(ctx.ledger)
            Path(args.json).write_text(__import__("json").dumps(report, indent=1,
                                                               default=str))
        return 2

    results = {}
    for name in selected:
        sec = SECTIONS[name]
        # A SECTION THAT RAISES IS A FAILING SECTION, NOT A DEAD RUN.  One
        # section throwing must not erase the verdicts the others measured -
        # a builder who cannot see the 48 over-limit steps because the
        # harbour check hit a TypeError has been given silence, and silence
        # reads as green.  The traceback goes to stderr, the refusal goes in
        # the report, and the exit code is non-zero either way.
        try:
            res = normalise(sec, sec.run(ctx))
        except Exception as exc:                           # noqa: BLE001
            sys.stderr.write(f"[accept] section {name!r} RAISED:\n"
                             + traceback.format_exc())
            res = SectionResult(findings=[Finding(
                section=name, check="section_raised", ok=False,
                message=f"the {name} section could not complete: "
                        f"{type(exc).__name__}: {exc}",
                numbers=dict(exception=type(exc).__name__))])
        # NO REFUSAL LEAVES THIS LOOP WITHOUT A CLASS, AND THIS IS THE ONE
        # PLACE THAT CAN SAY SO FOR ALL ELEVEN SECTIONS AND FOR BOTH PATHS.
        #
        # `NO_CLASS` on the board was never a section's decision - it was
        # the ABSENCE of one, and absence is indistinguishable from a zero
        # in the sum.  MEASURED: Ulfsland read `NO_CLASS 0` with SEVEN of
        # eleven sections publishing no class at all, because those seven
        # happened to have no refusals on that world; the fresh seed
        # Fresh42 put thirteen refusals in them and the board could triage
        # none of them.
        #
        # The backstop FILLS HOLES and re-decides nothing - a row that
        # already carries a class keeps it - so it cannot move a class any
        # section measured.  `section_raised` above reaches it too, which
        # is the only path by which that row has ever had a class.  What
        # makes `NO_CLASS 0` MEAN something is not this line but
        # `termination.coverage`, which reads every emittable refusal out
        # of the SOURCE and reddens the gate's own selftest if one of them
        # is named by no remedy family.
        T.backstop(name, res.findings)
        results[name] = res
        # The section's own report keys are kept at the top level of its
        # block, UNCHANGED, so a reader diffing this gate against the one it
        # replaces sees the same keys with the same numbers.  The
        # registration prose goes under `declares` rather than colliding
        # with a section's own `population` block.
        report["sections"][name] = dict(
            declares=dict(measures=sec.measures, instrument=sec.instrument,
                          population=sec.population),
            refusals=len([f for f in res.findings
                          if not f.ok and not f.advisory]),
            advisory_findings=len([f for f in res.findings
                                   if f.advisory and not f.ok]),
            verdict="PASS" if res.verdict() else "FAIL",
            # EVERY REFUSAL, NOT THE FIRST TWELVE.  The text report prints
            # twelve per section because a terminal is read by a human; the
            # JSON is read by whoever has to FIX them, and an artefact that
            # names 12 of 32 sends a builder to close a class he can only
            # see a third of.  MEASURED cost of this line: the seven-section
            # artefact went from 68 kB to 221 kB.
            findings=[dict(check=f.check, message=f.message, where=f.where,
                           measured=f.measured, limit=f.limit,
                           numbers=f.numbers, advisory=f.advisory)
                      for f in res.findings if not f.ok],
            **res.report)

    # The walker is read AFTER the sections have run, because it is the
    # census that measures it and the census is lazy; naming it in the header
    # before any section asked for it would be a number with no reading
    # behind it.
    cen = ctx._census
    report["instruments"]["walker"] = (
        f"Player prefab capsule MEASURED this run: radius "
        f"{cen.walker_radius:.3f} m, height {cen.walker_height:.3f} m"
        if cen else "not read: no section asked for the object census")
    if cen:
        report["population"] = dict(objects_in_save=len(cen.rows),
                                    objects_unnamed=cen.unresolved)

    ok = all(r.verdict() for r in results.values())
    report["verdict"] = "PASS" if ok else "FAIL"

    # Computed HERE, above the JSON write, not beside the line that prints
    # it: the artefact is read by whoever has to fix this, and a coverage
    # count that exists only on a terminal is the same omission it
    # measures.  MEASURED: the first version set this after the write and
    # shipped `population_coverage: null`.
    with_pop = sorted(n for n, r in results.items()
                      if any(f.ok and f.check == "population"
                             for f in r.findings))
    missing = sorted(set(results) - set(with_pop))
    report["population_coverage"] = dict(
        sections_run=len(results), sections_reporting=len(with_pop),
        reporting=with_pop, not_reporting=missing)

    if args.json:
        # THE REPORT OUTLIVES THE RUN, OR THE RUN WAS FOR NOTHING.  This
        # write happens AFTER every section has run and BEFORE the text
        # report, so an exception here discards a complete measurement that
        # can only be retaken by repeating it.  MEASURED 2026-09-22: a
        # `--json` under a directory that did not exist raised
        # FileNotFoundError and threw away 110 minutes of gate, after all 11
        # sections had finished and before a single line of the verdict was
        # printed.  The parent is created, and a path that still cannot be
        # written is reported to stderr and the TEXT REPORT IS STILL
        # PRINTED - the artefact is the convenience, the measurement is the
        # product.
        try:
            p = Path(args.json)
            if p.parent and not p.parent.exists():
                p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(__import__("json").dumps(
                report, indent=1, default=str))
        except OSError as exc:
            sys.stderr.write(
                f"--json {args.json} could not be written ({exc}); the "
                f"verdict below is the whole measurement and is printed "
                f"rather than discarded\n")

    w(f"\nWORLD ACCEPTANCE GATE  {args.world}  save {ctx.fp[:12]}  "
      f"scope {report['scope']}\n")
    w(f"  limit {I.STEP_LIMIT_PER_M:.4f} m per {I.SAMPLE_M:g} m "
      f"(tan {I.SLIDE_DEG:g} deg, Character::c_MinSlideDegreesPlayer)"
      + (f"; walker {2*cen.walker_radius:.2f} m wide (Player capsule, "
         f"MEASURED)\n" if cen else "\n"))
    if cen:
        w(f"  population: {len(cen.rows)} ZDOs in the save, "
          f"{cen.unresolved} of them unnamed\n")
    for name in order(results):
        res = results[name]
        w(f"\n  [{name}]  {'PASS' if res.verdict() else 'FAIL'}\n")
        # THE POPULATION GOES ON THE VERDICT PAGE, NOT ONLY IN THE JSON,
        # AND IT GOES THERE FOR EVERY SECTION.  STANDING RULE: no gate
        # may report a verdict over a population it did not print the
        # size of - AND THE SIZE OF THE SET IT WAS DRAWN FROM.
        #
        # This reporter printed REFUSALS ONLY, so every section's ok=True
        # `population` finding - the one that says what was counted and
        # out of what - was invisible on the page a human reads, in all
        # seven sections at once.  `0 samples` reads as "unreadable";
        # `0 of a 288-sample rectangle` reads as "broken", and the
        # difference cost four hours on a dead-flat pad.
        #
        # IT IS ABOVE THE `render` BRANCH BECAUSE THE FIRST VERSION WAS
        # INSIDE THE `else` AND THEREFORE SKIPPED EXACTLY THE SECTION
        # THAT NEEDED IT MOST.  `roads` is the one section with a custom
        # renderer, and MEASURED the same night: its render printed
        # traversability, connectivity, obstruction and coverage and
        # NEVER PRINTED CROSSINGS, so "0 of 13 span metres standing on a
        # piece" was visible only to somebody who went looking in the
        # JSON - while the crossings census itself judged 2 of 16.  A
        # rule enforced by the reporter must not be enforceable only for
        # the sections that did not write their own.
        for f in res.findings:
            if f.ok and f.check == "population":
                w(f"      population           {f.message}\n")
        if res.render:
            res.render(w)
        else:
            hard = [f for f in res.findings if not f.ok and not f.advisory]
            adv = [f for f in res.findings if f.advisory and not f.ok]
            w(f"      {len(hard)} refusals, {len(adv)} advisory\n")
            for f in hard[:12]:
                where = ("" if f.where is None else
                         "  (" + ",".join(f"{q:.1f}" for q in f.where) + ")")
                w(f"      {f.check:20s} {f.message}{where}\n")
            for f in adv[:4]:
                w(f"      advisory {f.check:11s} {f.message}\n")

    # HOW MANY SECTIONS REPORT A POPULATION BESIDE THEIR VERDICT, OUT OF
    # HOW MANY RAN.  ADVISORY, COUNTED, PRINTED - not a refusal, yet.
    #
    # STANDING RULE: no gate may report a verdict over a population it did
    # not print the size of, and the size of the set it was drawn from.
    # The reporter above now prints every PASSING population a section
    # emits; this line is the other half, because a section that emits
    # NONE is silently compliant and "absent" reads as "fine", which is
    # the disease itself.
    #
    # MEASURED 2026-09-22 over all 11 registered sections: FOUR report one
    # (roads, settlements, portals, spawn, lighting - and roads' only
    # `population` finding is a REFUSAL, so it says nothing when the
    # section passes), and SIX emit none at all: harbours, structures,
    # annulus, emission, provenance, townsites.
    #
    # WHY ADVISORY AND NOT A REFUSAL: making absence a refusal turns six
    # currently-passing sections red in one commit, mid-flight, over a
    # reporting rule rather than a defect in the world.  A COUNT ON THE
    # PAGE IS A BACKLOG, and a backlog on the page is what drove this
    # gate's neuter coverage from 17 of 34 to 34 of 34 in one section in
    # under an hour - it was cheap because it was visible.  Promote this
    # to a refusal when the count reaches N of N; by then the promotion
    # costs nobody anything and there is nothing left to argue about.
    w(f"\n  advisory  {len(with_pop)} of {len(results)} section(s) report a "
      f"POPULATION beside their verdict"
      + (f"; silent: {', '.join(missing)}" if missing else "")
      + "\n            a verdict over a population nobody printed the size "
        "of is the defect class this gate keeps finding; absent reads as "
        "fine\n")
    w(f"\n  {report['verdict']}\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
