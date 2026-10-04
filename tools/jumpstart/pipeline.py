#!/usr/bin/env python3
"""THE ELEVEN STAGES, DECLARED IN SOURCE, IN ORDER, WITH THEIR ARTEFACTS.

WHY THIS FILE EXISTS.  Ulfsland was built by a sequence of agent decisions.
Every individual stage is a real tool with a real CLI and a real refusal, and
the ORDER they go in was never anywhere but in the head of whoever was driving.
MEASURED 2026-09-23 on seed `Fresh42`: the world provisioned, the grid scanned
and the solver solved, and the gate then exited 2 at `accept/__main__.py:490`
with `Fresh42 HAS NO BUILD LEDGER` -- eleven sections named, none of them run.
Nothing in the tree could say WHICH stage was supposed to have produced that
ledger, or what that stage needed first.  A pipeline that exists as a sequence
of decisions cannot be re-run, and a fresh seed is exactly a re-run.

WHAT THIS FILE IS.  A TABLE, not a script.  Each stage DECLARES what it
produces and what it requires, by label, and `audit_graph()` refuses a table in
which any requirement is unowned or owned by a LATER stage.  The refusal is at
import time, like `accept/registry.py`'s, for the same reason: a driver that can
be started with an incoherent order is not a driver.

THE DEFECT CLASS THIS IS AN INSTANCE OF -- and the reason the table names
artefacts by PATH AND SCOPE rather than by path alone -- is the one this project
found seven times: A KEY THAT NAMES A SUPERSET OR A SUBSET OF WHAT THE QUANTITY
ACTUALLY DEPENDS ON.  An artefact path that does not carry the seed is a key
that names a superset: `settlements/sites.yaml` is ONE SLOT FOR EVERY WORLD, and
a Fresh42 solve written there is Ulfsland's corpus destroyed.  So every artefact
row carries `scoped`: whether the file declares its own seed/world, and the
status pass READS THAT DECLARATION through `worldseed.require_scope` rather than
trusting the name.

TWO ROWS IN THE TABLE ARE BLOCKED ON THE PRODUCER SIDE AND SAY SO.  They are not
omitted and they are not worked around:

  sites     the READER has a per-seed slot (`worldseed.sites_path` ->
            `settlements/sites.<seed>.yaml`) and the PRODUCER has none
            (`settlements/plan.py` writes `<out>/sites.yaml`, main() ~1216).
  route     NEITHER end has one: `roads/network.py::emit` writes the fixed names
            `segments.yaml` / `crossings.yaml` into `--out`, and `LIVE_OUT` is
            the repo's own `roads/` directory (network.py:938-939).  A Fresh42
            emit onto the live path OVERWRITES Ulfsland's corpus in place.

`status` reports those as PRODUCER_SLOT_MISSING, which is a distinct state from
MISSING: the difference between "run the stage" and "the stage cannot be run for
this seed without destroying another world's corpus".

DURABILITY.  The default work directory is `ledger/runs/<World>/pipeline`, not
`/tmp`.  Four of the stages hand their outputs to the next one through paths
that defaulted into `/tmp` (`roads/network.py:938` `DEFAULT_OUT`,
`blueprints/resolve.sh:30` `WORK`), and `/tmp` does not survive a reboot: a
pipeline whose interchange is in `/tmp` is a pipeline that can only be finished
in one sitting.

PRODUCER CURRENCY.  "Satisfied" used to mean "the stage's artefacts are
present and in scope" -- keyed on the artefact's EXISTENCE, never on WHICH
VERSION OF ITS PRODUCER made it.  MEASURED 2026-09-25: stage 7's solver was
fixed twice, `worlds/Fresh42/*/placements.yaml` was the pre-fix solver's, and
`status` read stage 7 `ok`; the only escape was `--force`, which re-runs
worldgen.  A stage's output depends on its inputs AND its producer, so every
successful stage now records its producer's fingerprint in
`<work>/producers.json`, `status` compares it (STALE names the file that
changed; no record is UNKNOWN PRODUCER, never ok), `run` re-runs exactly the
stages that are not current and their descendants, `--from K` re-runs K and
later and never anything earlier, and a stage that WRITES THE LIVE WORLD is
refused rather than re-run.  See STAGE_CURRENCY_DEPENDS_ON.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import worldseed                                            # noqa: E402

REPO = HERE.parent.parent
SEEDSCAN = REPO / "tools" / "seedscan"

# The one default that is a MEASUREMENT of this host rather than a choice.
VH_SRC_DEFAULT = "/media/big4/projects/game/valheim/Ulfsland/data/bepinex"
WORLD_ROOT_DEFAULT = "/media/big4/projects/game/valheim"


# ---------------------------------------------------------------------------
# scope
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Scope:
    """One run: which seed, which world, and where its interchange lives."""

    seed: str
    world: str
    work: Path
    world_root: Path
    vh_src: Path

    @property
    def seed_hash(self) -> str:
        import hashlib                                      # noqa: PLC0415
        return hashlib.md5(self.seed.encode()).hexdigest()[:12]

    @property
    def sandbox(self) -> Path:
        """The server mirror the scanning stages run out of.  DISPOSABLE.

        Deliberately NOT under `work`: the durability rule is about the
        INTERCHANGE -- the artefacts one stage hands the next -- and a 4 GB
        rsync mirror of the game install is neither an artefact nor something a
        world's ledger directory should carry.  It is rebuilt from `vh_src` by
        the scripts themselves whenever it is absent.
        """
        return Path("/tmp/jumpstart-sandbox") / self.seed

    @property
    def world_src(self) -> Path:
        return self.world_root / self.world / "config_merged" / "worlds_local" / self.world

    def env(self) -> dict:
        """Every child gets the seed and the world THROUGH THE ENVIRONMENT.

        `worldseed.seed()` / `worldseed.world()` is the resolver eight modules
        already read; handing each stage a `--seed` flag it may or may not have
        is how a seed literal survives in the ninth.
        """
        e = dict(os.environ)
        e[worldseed.ENV_SEED] = self.seed
        e[worldseed.ENV_WORLD] = self.world
        return e


# ---------------------------------------------------------------------------
# artefacts
# ---------------------------------------------------------------------------
# WHAT AN ARTEFACT DECLARES ABOUT ITSELF, and how to read it.  `scoped=True`
# means the file carries its own seed (and usually world) and MUST be checked
# against the run's -- see worldseed.require_scope, which is already the single
# authority for five readers in this tree.
SCOPE_BINARY = "vhpatch"      # VHPATCH1 header, read by roads/field.py
SCOPE_YAML = "yaml"           # top-level `seed:` / `world:` keys
SCOPE_NONE = ""               # a reading of the world generator, seed implicit
                              # in the file's own name


@dataclass(frozen=True)
class Artefact:
    label: str
    path: Path
    scope_kind: str = SCOPE_NONE
    # Set when the PRODUCER has no per-seed slot for this artefact, i.e. the
    # stage cannot be run for a non-default seed without overwriting the
    # default seed's copy.  The string is the measured citation.
    producer_slot_missing: str = ""
    # When set, `path` is a DIRECTORY and the artefact is "at least one file
    # matching this pattern under it".  `worlds/<World>/*/placements.yaml` is
    # nine files on Ulfsland and the readers glob it
    # (blueprints/solve_placements.py:657, roads/spec.py:197-208), so a single
    # path here would be a key naming a SUBSET of what the stage needs.
    glob: str = ""
    # A key every `placements[]` record must carry for the artefact to count as
    # PRODUCED rather than merely AUTHORED.  `solve_placements.py --write`
    # fills the `solved` block of a placement that already exists, so the file
    # is the same file before and after: without this, the stage that solves
    # would read as satisfied by the template it has not yet touched.
    member_key: str = ""
    # WHICH THING THIS IS ACROSS THE WRITES THE PIPELINE ITSELF MAKES INTO IT:
    # `(lineage key, position along it)`, or None when it holds nothing.  Set
    # only on an artefact a later stage WRITES IN PLACE -- the world save,
    # which stages 10 and 12 write into and the server re-saves every 30 min.
    # See IN_PLACE_DEPENDS_ON in `status`.
    lineage: Callable[[Path], tuple[str, int] | None] | None = None


@dataclass(frozen=True)
class Stage:
    ordinal: int
    name: str
    owner: str                                  # the tool that runs it
    produces: Callable[[Scope], list[Artefact]]
    requires: tuple[str, ...] = ()              # labels of earlier artefacts
    command: Callable[[Scope], list[str]] | None = None
    # A stage with no command is one no tool in this tree can run unattended.
    # It is DECLARED, never skipped: that is the whole finding.
    manual: str = ""
    # Runs after `command` exits 0.  For a stage whose producer writes into a
    # scratch directory the readers do not read -- see `_adopt_solve`.
    post: Callable[[Scope], None] | None = None
    note: str = ""
    # WHAT THIS STAGE'S READER OPENS THAT ANOTHER STAGE PRODUCES, as
    # `(directory-or-file, glob, member key)` triples, ASKED OF THE READER
    # MODULE ITSELF (never restated here).  `requires` is a label list typed
    # by hand; this is the reader's own answer, and `audit_reads` refuses a
    # table in which the two disagree.  See READS_DEPENDS_ON.
    reads: Callable[[Scope], list[tuple[Path, str, str]]] | None = None
    # NON-EMPTY WHEN THIS STAGE WRITES THE LIVE WORLD, and the string says how.
    # Such a stage is NEVER re-run because a file changed: `run` without
    # `--from`/`--force` runs it only to CREATE its output (no producer record
    # and an artefact missing), and REFUSES it otherwise with its reason and
    # the command that would redo it.  Regenerating a world is an operator's
    # decision; a file edit is not one.  See WORLD_GUARD in `run`.
    writes_world: str = ""
    # argv flags whose VALUE is a measurement of the host or the agent, not a
    # choice that decides the output (`--budget-mb` is free disk, `--actor` is
    # whoever runs it).  Masked in the row fingerprint, so a disk filling up
    # does not make a 1.1 GB field read as made by a different producer.
    volatile: tuple[str, ...] = ()
    # A FIXED POINT WITH AN EARLIER STAGE (CENSUS_LOOP_DEPENDS_ON):
    # `(earlier stage's name, how many units this stage's own artefact still
    # wants of it, the argv added to the earlier stage's command to produce
    # exactly those)`.  After this stage succeeds `run` re-runs the pair --
    # the earlier stage's own producer, then this one -- until nothing is
    # wanted, for at most CENSUS_ROUNDS_MAX rounds.
    # A 4th member names what the refining stage leaves UNSERVED that no
    # earlier-stage reading can serve: a fixed point with any is refused.
    refines: tuple[str, Callable[[Scope], int],
                   Callable[[Scope], list[str]],
                   Callable[[Scope], list[str]]] | None = None


# ---------------------------------------------------------------------------
# THE TABLE
# ---------------------------------------------------------------------------
def _grid(s: Scope) -> list[Artefact]:
    return [Artefact("grid", s.work / f"grid-{s.seed_hash}" / "00000.biome")]


def _locations(s: Scope) -> list[Artefact]:
    return [Artefact("locations", s.work / "loc" / f"{s.seed_hash}.json")]


_VALHEIM_WORLD = None


def save_lineage(world_src: Path) -> tuple[str, int] | None:
    """`(seed/seed value/uid, save generation)` of a Valheim save directory.

    The UID is drawn when the game CREATES a world and every later save of it
    carries the same one; the generation N of `_main.<N>.fwl2` is incremented
    by every save and never goes back.  So a re-created world has a different
    key and a copy put back over a live one has a LOWER N, while an autosave,
    a census writing zones and a build writing pieces only move N forward.
    MEASURED 2026-09-25: the freeze, drive 1's built world (`_main.120`) and
    this drive's restored copy all read uid -2868312218 -- a restore of the
    same freeze keeps the key and resets N to 1.
    """
    global _VALHEIM_WORLD
    if _VALHEIM_WORLD is None:
        import importlib.util                               # noqa: PLC0415
        spec = importlib.util.spec_from_file_location(
            "valheim_world", REPO / "tools" / "valheim_world.py")
        _VALHEIM_WORLD = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_VALHEIM_WORLD)
    fwl = worldseed.newest_save(Path(world_src), "fwl2")
    if fwl is None:
        return None
    meta = _VALHEIM_WORLD.parse(fwl)
    return (f"{meta['seed']}/{meta['seed_value']}/{meta['uid']}",
            int(fwl.name.split(".")[1]))


def _save(s: Scope) -> list[Artefact]:
    return [Artefact("save", s.world_src, lineage=save_lineage)]


# FIELD_WINDOWS_DEPENDS_ON: THE DEFAULT SEED IS SCANNED ON ITS DECLARED WINDOWS.
# Stage 4 only ever DERIVED windows from the seed's land components, which is
# right for a fresh seed and wrong for the one the committed corpus was built
# on: for Pirate68 it caps `trunk-primary` at a 3200 m window around the spawn
# and names the next landmass `trunk-secondary`, 7000 m away, so the west half
# of the main continent -- where vestvik stands -- is in no window and the town
# search finds 1 district candidate for 2 declared towns (MEASURED on the
# from-scratch Ulfsland regeneration, stage 8: "seed 'Pirate68' yields 1 town
# district candidate(s) and this plan declares 2 towns").  The default seed is
# scanned with `worldseed.ULFSLAND_WINDOWS`, whose `trunk-secondary` is that
# west half; every other seed still derives.  NEUTER `_FIELD_WINDOWS_MODE =
# "derived"`.  Proven by `_default_seed_scans_declared`.
FIELD_WINDOWS_DEPENDS_ON = ("declared_windows_for_the_default_seed",)
_FIELD_WINDOWS_MODE = "declared"    # declared | derived


def field_window_flags(seed: str) -> list[str]:
    """The extra `roads/field.py request` arguments for this seed."""
    if _FIELD_WINDOWS_MODE == "declared" and seed == worldseed.DEFAULT_SEED:
        return ["--declared-windows"]
    return []


def _field(s: Scope) -> list[Artefact]:
    return [Artefact("field", s.work / f"{s.seed}_1m.bin", SCOPE_BINARY)]


def _geography(s: Scope) -> list[Artefact]:
    import importlib                                        # noqa: PLC0415
    g = importlib.import_module("roads.geography")
    return [Artefact("geography", g.geography_path(s.seed), SCOPE_YAML)]


def _sites(s: Scope) -> list[Artefact]:
    # TWO documents, not one. `settlements/plan.py` emits the plan AND the site
    # set from one solve, and the gate reads BOTH: `accept/sections/spawn.py`
    # takes the seed's LOCATION DUMP PATH out of the plan's provenance, and
    # that read is shared by spawn and settlements::_markers. Both now have a
    # per-seed slot (`worldseed.sites_path` / `worldseed.plan_path`); until 2026-09-23
    # the plan had NONE at either end, so on a fresh seed both sections lost
    # their instrument outright -- MEASURED on Fresh42: "the seed's location
    # dump is not readable, so no seat can be tested against generated
    # content", twice, while this run's own Fresh42 dump sat in
    # <work>/loc/<hash>.json.
    return [Artefact("sites", worldseed.sites_path(s.seed), SCOPE_YAML),
            Artefact("settlement_plan", worldseed.plan_path(s.seed), SCOPE_YAML)]


def _render_world_tree(s: Scope) -> None:
    """`derive.py build` -- the half of stage 6 (`world_facts`) that renders the DERIVED tree.

    `init` provisions a world that does not exist: `world.yaml` plus one
    `placements.yaml` per preset, and it never overwrites a solve.  It does
    NOT render `<preset>/settings/{world-modifiers.env,overrides.yaml,
    chest-manifest.yaml}` or `keys.yaml` -- those come off the world-agnostic
    tier policy through `derive.py build` (derive.py:2066-2068), and this
    stage named only `init`.

    MEASURED 2026-09-24: `derive.py check Fresh42` reported 53 problems, all
    MISSING, on a world that had completed stages 1-11 and five build steps;
    build step 6 `fixtures` then died on all 13 placements with a
    FileNotFoundError for `settings/chest-manifest.yaml`.  A stage that runs
    half its tool produces half its artefacts, and the artefact list said so
    only after the third artefact was declared.

    ITS RC IS NOT THE VERDICT AND THAT IS MEASURED, NOT ASSUMED.
    `derive.py build` exits 1 on ADVISORIES as well as on failures: on
    Fresh42 it wrote all 36 files `ok` and still exited 1 over 9 SPAWN rows
    (no `spawn.yaml` yet -- that needs `derive.py spawn`, which boots a
    sandboxed server) and 8 KIT rows (a preset's equip list overflowing the
    32-slot grid).  Refusing on the rc would make stage 6 unpassable for
    every world that has not yet had its spawn probed, which is every fresh
    seed.  So the verdict is taken on THE ARTEFACT THIS STAGE DECLARES --
    absent means the stage produced nothing, and the rc is reported beside it.
    """
    import subprocess as _sp                                # noqa: PLC0415
    rc = _sp.call([sys.executable, str(HERE / "worlds" / "derive.py"),
                   "build", s.world], cwd=str(HERE), env=s.env())
    made = sorted((HERE / "worlds" / s.world)
                  .glob("*/settings/chest-manifest.yaml"))
    sys.stderr.write(f"      derive.py build {s.world}: rc={rc}, "
                     f"{len(made)} settings tree(s) rendered\n")
    if not made:
        raise SystemExit(f"worlds/derive.py build {s.world} rc={rc} rendered "
                         f"NO settings tree: `world_settings` is stage 6's "
                         f"own artefact and build step 6 reads it")


# ADOPT_RESTAMP_DEPENDS_ON: an adopted solve that is byte-identical to the
# slot it lands in is RE-VALIDATED, so its mtime must say so -- the freshness
# rule reads mtime against the inputs it was derived from.  The neuter `skip`
# is the shipped early `continue`.
ADOPT_RESTAMP_DEPENDS_ON = ("adopt_restamp",)
_ADOPT_MODE = "restamp"         # restamp | skip (the shipped early continue)


def _adopt_solve(s: Scope) -> None:
    """MOVE the solve into the per-seed slots the readers actually read.

    `--out` is a scratch directory, so nothing this stage does can reach the
    default seed's corpus even on the default seed's own run.  The NAMES are
    asked of `worldseed`, never transcribed: the producer and the reader agree
    because they call the same function, which is the property that was missing
    when `sites.yaml` had a per-seed slot and `plan.json` did not.
    """
    import shutil                                           # noqa: PLC0415
    for src, dst in ((s.work / "sites" / worldseed.sites_name(s.seed),
                      worldseed.sites_path(s.seed)),
                     (s.work / "sites" / worldseed.plan_name(s.seed),
                      worldseed.plan_path(s.seed))):
        if not src.exists():
            continue
        if dst.exists() and dst.read_bytes() == src.read_bytes():
            # RE-VALIDATED, SO RE-STAMPED (ADOPT_RESTAMP_DEPENDS_ON): the
            # solve just answered the question again with the same bytes.
            # Skipping the copy left the slot's mtime older than the inputs
            # it was re-derived from, and stage 9 refused it STALE --
            # MEASURED on Fresh42 drive 6 (`--from 4`: sites.Fresh42.yaml
            # kept a 19:47 mtime after an 00:45 stage-8 solve).
            if _ADOPT_MODE == "restamp":
                os.utime(dst)
                sys.stderr.write(f"      re-stamped {dst} (identical to "
                                 f"{src.name})\n")
            continue
        shutil.copyfile(src, dst)
        sys.stderr.write(f"      adopted {src} -> {dst}\n")


def _world_facts(s: Scope) -> list[Artefact]:
    """THE TWO ARTEFACTS NO TOOL IN THIS TREE PRODUCED, AND NOW ONE DOES.

    THE ANSWER, MEASURED 2026-09-23: NO PRODUCER HAD EVER EXISTED.  Not a
    seed-blind one, not a path-keyed one -- none.  `worlds/derive.py` READS
    `world.yaml` (`load_world`, ~:166, `SystemExit` when absent) and its only
    two write sites (~:2125, ~:2212) write `<preset>/` files; `derive.py list`
    DISCOVERS worlds by globbing `*/world.yaml`.  The single write to
    `worlds/**` anywhere in the repo was a tempdir fixture in
    `accept/sections/emission.py:315-317`.  A hand-authored artefact sat in the
    middle of an automated pipeline and every fresh seed had always been
    blocked on it; nobody had run far enough to find out.

    WHY IT COULD BE DERIVED.  Both files were keys naming a SUPERSET.  Five of
    `world.yaml`'s thirteen top-level keys -- `baseline`, `inventory`,
    `skills`, `servercharacters`, `spawn_points_source` -- are properties of
    the DEPLOYED MOD SET and the FLEET's launch policy and do not depend on the
    world at all; three more are the world's own NAME lowercased with a role
    suffix.  And `placements.yaml` says of itself, in `resolve_note`, "Every
    `solved` block below is seed-specific and disposable. The durable part is
    `requirement`" -- a file declaring its own content seed-independent while
    living in a directory keyed by world.

    So `derive.py init <World> --seed <Seed> --write` writes both from
    `worlds/world-defaults.yaml` and `worlds/placement-templates/<preset>.yaml`
    plus the world's name.  MEASURED: `derive.py check-facts Ulfsland` re-derives
    all 8 shared keys of the ONLY pre-existing world exactly, so the extraction
    dropped nothing, and Ulfsland's own file was never written.

    `solve_placements.py --write` is still NOT the producer.  It globs
    `worlds/<World>/*/placements.yaml` (solve_placements.py:657, :806) and
    fills the `solved` block of each placement it finds; given no files it
    prints "no placements.yaml under <world>" and exits.  That is why stage 7's
    artefact carries `member_key="solved"`: the template this stage writes must
    not read as the solve that stage has not yet done.
    """
    return [
        Artefact("world_facts", HERE / "worlds" / s.world / "world.yaml",
                 SCOPE_YAML),
        Artefact("placement_templates", HERE / "worlds" / s.world, SCOPE_YAML,
                 glob="*/placements.yaml"),
        # THE THIRD THING THIS STAGE WRITES, AND IT WAS NOT DECLARED.
        # `derive.py::derived` renders `settings/world-modifiers.env`,
        # `settings/overrides.yaml` and `settings/chest-manifest.yaml` per
        # preset (derive.py:2066-2068) from the world-agnostic tier policy.
        # Naming only two of the stage's outputs is an artefact list naming a
        # SUBSET of what the stage produces, and the cost is measured:
        # 2026-09-24, Fresh42's first ever run of build step 6 `fixtures`
        # died on all 13 placements with a FileNotFoundError TRACEBACK for
        # `worlds/Fresh42/<preset>/settings/chest-manifest.yaml`, 50 minutes
        # into a live build, because nothing between stage 6 and that read
        # had an opinion about whether the file existed. Declared, it is a
        # named missing input refused before step 1.
        # NO SCOPE HEADER, AND THAT IS A DECLARATION RATHER THAN A WAIVER.
        # `SCOPE_YAML` demands a machine-readable seed because an artefact
        # holding COORDINATES must say which world it measured them in --
        # MEASURED, 12,301 Pirate68 locations once passed for Fresh42's.  A
        # chest manifest holds no coordinates and no seed: it is rendered
        # from a world-agnostic tier policy plus `worlds/<World>/world.yaml`,
        # and the world it belongs to is the DIRECTORY it is in.  That is the
        # same attribution `placements` is declared with in
        # `buildstage._inputs`. Demanding a seed stamp here would refuse a
        # file that has nothing seed-shaped to stamp.
        Artefact("world_settings", HERE / "worlds" / s.world,
                 glob="*/settings/chest-manifest.yaml"),
    ]


def _placements(s: Scope) -> list[Artefact]:
    return [Artefact("placements", HERE / "worlds" / s.world, SCOPE_YAML,
                     glob="*/placements.yaml", member_key="solved")]


def _route_provisional(s: Scope) -> list[Artefact]:
    """THE CYCLE, BROKEN WHERE THE TOOLS ALREADY BREAK IT.

    The first table this file carried put `route` before `census` and required
    the census, and `audit_graph` REFUSED IT AT IMPORT on the first run --
    INVERTED, stage 8 wants stage 9's artefact.  The check was right and the
    table was wrong, and the resolution is not to reorder two stages but to
    notice that ROUTE HAPPENS TWICE.  `roads/network.py --provisional` exists
    for exactly this: "route with NO census, to give precensus.py survey a line
    to read along. Every segment is stamped PROVISIONAL and ribbon.py refuses
    to pave it" (network.py, --provisional help).  SITE_PREPARATION_ORDER.md
    0-0c says the same thing from the other end.

    So the provisional plan goes to the WORK directory, never to roads/: it is
    an intermediate, and writing an unpaveable plan over the corpus the gate
    reads is the overwrite this table exists to refuse.
    """
    # The NAME comes from the same resolver `emit` writes under: an
    # intermediate whose name this table spells itself is a second home for
    # the key, and the moment `emit` started carrying the seed this row would
    # have gone MISSING on every seed while the file sat there.
    return [Artefact("segments_provisional",
                     s.work / "route-provisional"
                     / worldseed.road_plan_names(s.seed)["segments"],
                     SCOPE_YAML)]


def _route(s: Scope) -> list[Artefact]:
    """THE SLOT THAT DID NOT EXIST AT EITHER END, AND NOW EXISTS AT BOTH.

    This row reported PRODUCER_SLOT_MISSING and refused to run: `network.py`
    wrote the fixed names `segments.yaml` / `crossings.yaml` into `--out`, and
    `LIVE_OUT` is `roads/` itself, so an emit for any seed but the default
    overwrote the 1.1 MB plan the live Ulfsland build was routed, profiled,
    stamped and ledgered against.  `worldseed.segments_path` /
    `crossings_path` are that slot -- third file family, same shape as
    `sites_path` and `plan_path`, default seed keeps the bare name -- and
    `network.py::emit` now asks the resolver for the name rather than spelling
    it, so the producer and the reader cannot disagree about it.
    """
    return [Artefact("segments", worldseed.segments_path(s.seed), SCOPE_YAML),
            Artefact("crossings", worldseed.crossings_path(s.seed), SCOPE_YAML)]


def _route_plan_wanted(s: Scope) -> dict:
    import yaml                                             # noqa: PLC0415
    p = worldseed.segments_path(s.seed)
    if not p.exists():
        return {}
    doc = yaml.load(p.read_text(),
                    Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader)) or {}
    return doc.get("census_wanted") or {}


def _route_census_wanted(s: Scope) -> int:
    """How many cells the routed plan still wants the census to read
    (`network.CENSUS_BAND_DEPENDS_ON`, the plan's `census_wanted`)."""
    return len(_route_plan_wanted(s).get("cells") or [])


def _route_servable_unrouted(s: Scope) -> list[str]:
    """Legs the routed plan could serve over read ground and did not route
    (`network.CENSUS_ADOPT_DEPENDS_ON`, `census_wanted.servable_unrouted`)."""
    return [str(u.get("id")) for u in
            _route_plan_wanted(s).get("servable_unrouted") or []]


def scan_budget_mb(work: Path) -> float:
    """How much 1 m field this HOST can hold, measured, not assumed.

    `worldseed.DEFAULT_WINDOW_BUDGET_BYTES` is 512 MiB.  MEASURED 2026-09-23 on
    seed Fresh42: `field.py request` refused at 17.1 s with

        26 landmasses over the 1,742,400 m2 floor, 1,106 MB against a 537 MB
        budget -- for scale, Ulfsland's own seven windows cost 231 MB

    and the refusal is CORRECT -- it drops nothing silently.  But the number it
    refuses against is a RESOURCE CEILING, and a resource ceiling that is a
    literal is a key naming a superset: it says "this much land" when what it
    can actually know is "this much disk".  Ulfsland fits under 512 MiB and
    Fresh42 does not, and nothing about that is a property of either seed.

    So the pipeline measures the host and hands the number down.  No seed
    appears in it, and a seed whose land genuinely does not fit still gets the
    refusal with its own numbers in it.
    """
    import shutil                                           # noqa: PLC0415
    probe = work if work.exists() else work.parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    # A quarter of free space, never less than the module default and never
    # more than 8 GiB: the first bound keeps a fresh seed from being refused by
    # a number fitted to another world, the second keeps a full disk from being
    # the way this run fails.
    want = max(free // 4, worldseed.DEFAULT_WINDOW_BUDGET_BYTES)
    return min(want, 8 * 1024 * 1024 * 1024) / (1024 * 1024)


def _census(s: Scope) -> list[Artefact]:
    """THE WHOLE-NETWORK CENSUS, which until 2026-09-23 NOTHING WROTE.

    THREE SPELLINGS OF ONE KEY, NO TWO OF THEM THE SAME.  This table declared
    `roads/census/<World>-census.json`; `precensus.require()`'s own refusal
    text names `roads/census/<world>.json`; and every producer -- `survey`,
    `from_save`, `placed` -- wrote ONE FILE PER SEGMENT,
    `<world>-<segment>[-save].json`.  MEASURED: `roads/census/` holds 13
    files, all of them `Ulfsland-<segment>-save.json`, and
    `roads/segments.yaml` records `census_file: None` -- so the LIVE Ulfsland
    plan was itself routed without a census, and the whole-network artefact
    stage 11 asks for has never existed on any world.  `survey` even computed
    the merged form into a local called `merged` and returned without writing
    it.

    `precensus.network_census_path()` is now the one home for that name and
    both producers call it, so this row names what a producer writes.
    """
    return [Artefact("census", HERE / "roads" / "census" / f"{s.world}-census.json")]


def _ledger(s: Scope) -> list[Artefact]:
    """The world's ledger AND the build's own journal.

    THE LEDGER ALONE IS A KEY NAMING A SUPERSET of what stage 12 produced:
    stage 10's census survey appends its `zones_generate` records to the same
    `ledger/runs/<World>/ledger.jsonl` (MEASURED on Fresh42: 548
    zones_generate rows beside the build's), so the ledger exists before the
    build has laid a stone.  `<work>/build/buildstage.json` is written by
    `buildstage.py run` and by nothing else, which is what lets the world
    guard tell "never built" (run it) from "built by an unknown producer"
    (refuse it) on a from-scratch world.
    """
    return [Artefact("ledger", HERE / "ledger" / "runs" / s.world / "ledger.jsonl"),
            Artefact("build_journal", s.work / "build" / "buildstage.json")]


def _verdict(s: Scope) -> list[Artefact]:
    return [Artefact("verdict", s.work / f"gate-{s.world}.json")]


STAGES: tuple[Stage, ...] = (
    Stage(1, "worldgen", "tools/valheim_worldgen.py", _save,
          command=lambda s: [sys.executable, str(REPO / "tools" / "valheim_worldgen.py"),
                             s.world, s.seed],
          note="creates the save through the game's own generator with "
               "NeuralyzeWorldSeed pinning the seed name",
          writes_world="creates the world save"),
    Stage(2, "grid", "tools/seedscan/run_scan.sh", _grid,
          command=lambda s: ["env",
                             f"VH_SRC={s.vh_src}",
                             f"SEEDS={s.work / ('seed-' + s.seed_hash + '.txt')}",
                             f"OUT={s.work / ('grid-' + s.seed_hash)}",
                             f"SANDBOX={s.sandbox}",
                             "STEP=8", "HEIGHT=1", "PREGEN=1",
                             str(SEEDSCAN / "run_scan.sh")],
          note="8 m biome+height grid. PREGEN=1 is not optional: without it "
               "AddRivers reads an empty river dictionary"),
    Stage(3, "locations", "tools/seedscan/run_locscan.sh", _locations,
          command=lambda s: ["env",
                             f"VH_SRC={s.vh_src}",
                             f"SEEDS={s.work / ('seed-' + s.seed_hash + '.txt')}",
                             f"OUT={s.work / 'loc'}",
                             f"SANDBOX={s.sandbox}",
                             str(SEEDSCAN / "run_locscan.sh")],
          note="ZoneSystem location dump, one world boot"),
    Stage(4, "field", "roads/field.py request", _field,
          requires=("grid", "locations"),
          command=lambda s: [sys.executable, str(HERE / "roads" / "field.py"),
                             "request",
                             "--grid", str(s.work / f"grid-{s.seed_hash}" / "00000.biome"),
                             "--locations", str(s.work / "loc" / f"{s.seed_hash}.json"),
                             "--out", str(s.work / f"{s.seed}_1m.bin"),
                             "--vh-src", str(s.vh_src),
                             "--sandbox", str(s.sandbox),
                             "--budget-mb", f"{scan_budget_mb(s.work):.0f}",
                             *field_window_flags(s.seed),
                             "--seed", s.seed],
          note="derives this seed's patch windows off site_finder.land_components "
               "and runs PatchScan on them, under a budget measured off the HOST",
          volatile=("--budget-mb",)),
    Stage(5, "geography", "roads/geography.py emit", _geography,
          requires=("field",),
          command=lambda s: [sys.executable, str(HERE / "roads" / "geography.py"),
                             "emit", "--field", str(s.work / f"{s.seed}_1m.bin"),
                             "--seed", s.seed],
          note="the seed's own road nodes. The portal-hall --anchor is "
               "AUTHORED design, read from the prior document when there is "
               "one; with none, only a seed whose crossing is costed from it "
               "refuses, at that crossing (geography.ANCHOR_DEPENDS_ON)"),
    # THE ORDER BELOW WAS `sites, world_facts, placements` UNTIL 2026-09-24,
    # with world_facts and placements declaring they needed `sites`.  Neither
    # reader opens a site set (derive.py and solve_placements.py have no read
    # of one); `settlements/plan.py` DOES open this world's SOLVED placements
    # -- its Picker keeps every settlement off the world's installations.
    # MEASURED 2026-09-24, Fresh42 from scratch: stage 6 refused `seed
    # 'Fresh42' has no sited installations` at 137.5 s (plan.py then imported
    # roads/spec.py, whose import-time `installation_xz` refuses a world with
    # none), and on every earlier drive it had run only because a previous
    # drive left a worlds/Fresh42/ behind -- while reading ULFSLAND's 13
    # installations, because the Picker's world defaulted to worlds/Ulfsland.
    # The `reads` declarations are the readers' own, and `audit_reads` now
    # refuses the old table (see `_prefix_table`).
    Stage(6, "world_facts", "worlds/derive.py init + build", _world_facts,
          command=lambda s: [sys.executable, str(HERE / "worlds" / "derive.py"),
                             "init", s.world, "--seed", s.seed, "--write"],
          post=_render_world_tree,
          note="which buildings this world gets, and where their requirements "
               "say they may stand. Was the WALL: rc 4, NO COMMAND, because "
               "no tool in this tree had ever written a worlds/<World>/. It is "
               "derived now -- worlds/world-defaults.yaml holds the mod-set "
               "half that never depended on the world, placement-templates/ "
               "holds the durable requirement half the placement files "
               "themselves declare seed-independent"),
    Stage(7, "placements", "blueprints/solve_placements.py", _placements,
          # `field`: resolve.sh hands <work>/<seed>_1m.bin to the solver, which
          # judges a waterfront the roads serve on the router's own land
          # labelling (solve_placements NETWORK_LAND_DEPENDS_ON).
          requires=("grid", "locations", "field", "placement_templates"),
          # resolve.sh reads its scope out of WORLD/SEED/WORK/VH_SRC, NOT out
          # of VH_WORLD/VH_SEED, and every one of those has a default that
          # points somewhere else: WORK defaults to /tmp/jumpstart-solve, a
          # single directory keyed by nothing, so two seeds solved on one host
          # read each other's cached grid.  Handing it this run's scope
          # explicitly is what makes the stage repeatable for ANY seed; the
          # grid and locations it would otherwise re-scan are the ones stages
          # 2 and 3 already produced, so this also stops a 50 s + 95 s re-scan.
          command=lambda s: ["env",
                             f"VH_SRC={s.vh_src}",
                             f"WORLD={HERE / 'worlds' / s.world}",
                             f"SEED={s.seed}",
                             f"WORK={s.work}",
                             f"SANDBOX={s.sandbox}",
                             "WRITE=1",
                             str(HERE / "blueprints" / "resolve.sh")],
          note="coarse search -> 1 m patch scan -> verdict, per installation. "
               "FILLS the solved block of a placement that already exists"),
    Stage(8, "sites", "settlements/plan.py", _sites,
          requires=("field", "locations", "placements"),
          command=lambda s: [sys.executable, str(HERE / "settlements" / "plan.py"),
                             "--patches", str(s.work / f"{s.seed}_1m.bin"),
                             "--locations", str(s.work / "loc" / f"{s.seed_hash}.json"),
                             "--out", str(s.work / "sites")],
          post=_adopt_solve,
          reads=lambda s: [worldseed.installations_read(s.world)],
          note="the solved site set AND the full plan. plan.py writes both into "
               "a scratch --out under the names worldseed decides; "
               "_adopt_solve copies each into the per-seed slot its reader reads"),
    Stage(9, "route_provisional", "roads/network.py plan --provisional",
          _route_provisional,
          requires=("field", "geography", "sites", "settlement_plan",
                    "placements"),
          # network.planned_pad_claims (PAD_CLAIMS_DEPENDS_ON) reads the
          # solved installations AND the settlement plan: every pad the build
          # will lay is a keep-out the router bends round.
          reads=lambda s: [worldseed.installations_read(s.world),
                           (worldseed.plan_path(s.seed), "", "")],
          command=lambda s: [sys.executable, str(HERE / "roads" / "network.py"),
                             "plan", "--field", str(s.work / f"{s.seed}_1m.bin"),
                             "--provisional",
                             "--out", str(s.work / "route-provisional"),
                             "--seed", s.seed, "--world", s.world],
          note="an UNPAVEABLE plan whose only job is to give the census a line "
               "to read along. ribbon.py refuses to pave a PROVISIONAL segment"),
    Stage(10, "census", "roads/precensus.py survey", _census,
          requires=("save", "segments_provisional"),
          command=lambda s: [sys.executable,
                             str(HERE / "roads" / "precensus.py"), "survey",
                             "--plan", str(s.work / "route-provisional"
                                           / f"segments.{s.seed}.yaml"),
                             "--field", str(s.work / f"{s.seed}_1m.bin"),
                             "--out", str(HERE / "roads" / "census"),
                             # THE ACTOR IS WHOEVER RUNS THIS, NOT A WORD.
                             # This row passed the literal "pipeline", so
                             # every zone the survey generated was recorded
                             # in the world's ledger under an agent that
                             # does not exist -- the exact provenance defect
                             # stage 12's row names.  Empty makes precensus
                             # REFUSE ("--actor is required").  See
                             # ACTOR_DEPENDS_ON / audit_actors.
                             "--actor", os.environ.get("VH_ACTOR", "")],
          manual="THIS ROW HAD NO COMMAND UNTIL 2026-09-23 AND THE REASON WAS "
                 "NOT THE SERVER. `survey` GENERATES ZONES AND THEN READS "
                 "THEM, so it needs a live server for this world with RCON "
                 "reachable -- a REAL prerequisite, and the pipeline inherits "
                 "its address from the environment: $VALHEIM_RCON_HOST, "
                 "$VALHEIM_RCON_CONFIG and $VALHEIM_SERVER_LOG address a "
                 "server that is NOT a docker container, which is what every "
                 "fresh seed's server is. THE FOUR THINGS THAT ACTUALLY "
                 "BLOCKED IT, all MEASURED on Fresh42 and all now fixed in "
                 "the tools: (1) `LiveBuilder(world=\"Ulfsland\")` was a "
                 "constant and 36 of 40 callsites passed no world, so this "
                 "stage would have appended its zones_generate records to "
                 "ULFSLAND'S ledger; (2) the RCON host was always "
                 "`docker inspect valheim-server-<World>`; (3) the console "
                 "echo was read only through `docker exec`, so every command "
                 "raised OutputNotFound; (4) every Upgrade World verb is "
                 "registered IsCheat=true and Terminal.m_cheat is FALSE in a "
                 "fresh server, so `zones_generate` returned a SUCCESSFUL "
                 "RCON echo for a command the game refused -- "
                 "`replay.Server.require_cheats` now probes for it and "
                 "LiveBuilder refuses the session otherwise. The role "
                 "`road_census` was also unregistered, so this stage had "
                 "never once been run through the ledger on any world. "
                 "THE OTHER OP, `precensus.py from_save`, IS NOW REACHABLE "
                 "AND IS STILL NOT A SUBSTITUTE, and both halves are "
                 "MEASURED. Reachable: `accept --zdo-census` runs ahead of "
                 "the build-ledger check because the ZDO dump depends on the "
                 "save alone (instruments.ZDO_CENSUS_DEPENDS_ON), and "
                 "from_save acquires its own reading -- 81 ZDO rows off "
                 "Fresh42's frozen unbuilt save, rc 0, 4.9 s, no server. Not "
                 "a substitute: on that same save W9-vestvik-lhwest read 0 of "
                 "460 band cells, 460 in an UNGENERATED zone, 0 bodies, "
                 "against the LIVE survey's 24 of 24 cells and 6 blocking "
                 "bodies on S8. `band_gate` used to answer ROOM on that "
                 "nothing and now answers UNCENSUSED",
          note="generate the zones, then census what generating them created -- "
               "a road is PLANNED before its zones exist, so the plan cannot "
               "see what generating those zones creates",
          writes_world="generates zones in the live world over RCON and "
                       "appends them to its ledger",
          volatile=("--actor",)),
    Stage(11, "route", "roads/network.py plan --census", _route,
          requires=("field", "geography", "sites", "settlement_plan",
                    "census", "placements"),
          reads=lambda s: [worldseed.installations_read(s.world),
                           (worldseed.plan_path(s.seed), "", "")],
          # `--out roads/` is now SAFE for any seed and was not before: the
          # emitted names carry the seed (worldseed.road_plan_names), so
          # this write cannot reach the default seed's two documents. Stage 12
          # and the gate read the plan from `roads/`, so an intermediate in
          # the work directory would have to be adopted afterwards -- and an
          # adoption step exists only because a producer and a reader disagree
          # about a key, which here they no longer do.
          command=lambda s: [sys.executable, str(HERE / "roads" / "network.py"),
                             "plan", "--field", str(s.work / f"{s.seed}_1m.bin"),
                             "--census", str(HERE / "roads" / "census" / f"{s.world}-census.json"),
                             "--out", str(HERE / "roads"),
                             "--seed", s.seed, "--world", s.world],
          note="census-before-route is the order, not an option",
          # THE CENSUS READS WHERE THE ROUTE WILL STAND (CENSUS_LOOP_DEPENDS_ON):
          # the routed plan names the unread cells of every line it writes or
          # would write (`census_wanted`), and stage 10's own producer reads
          # exactly those before this stage routes again.
          refines=("census", _route_census_wanted,
                   lambda s: ["--wanted", str(worldseed.segments_path(s.seed))],
                   _route_servable_unrouted)),
    Stage(12, "build", "buildstage.py run", _ledger,
          requires=("save", "placements", "segments", "crossings", "census"),
          # THE GAP THIS ROW NAMED IS CLOSED.  It used to read `command=None`
          # with the manual "the one stage with no single entry point in this
          # tree: clear.py, flatten.py, sites.py, stock.py, ribbon.py and the
          # settlements builders are separate drivers" -- MEASURED by
          # StageWall, six `def main`s and ZERO files invoking any of them as a
          # subprocess.  `buildstage.py` is that entry point: the BUILD_STEPS
          # table (ten rows, including 6.5 `decks`), whose
          # order is declared in source and refused at import by THIS file's
          # own `audit_graph`, not by a second copy of it.  `--actor` is
          # required there and passed here, because the ledger is the only
          # place a defect's provenance can be found.
          command=lambda s: [sys.executable, str(HERE / "buildstage.py"), "run",
                             "--world", s.world, "--seed", s.seed,
                             "--work", str(s.work),
                             # NOT DEFAULTED TO A NAME.  Empty makes
                             # buildstage REFUSE and say which variable to
                             # set; a literal like "pipeline" would record
                             # every write in this world's ledger under an
                             # agent that does not exist, and provenance that
                             # is wrong reads as evidence.
                             "--actor", os.environ.get("VH_ACTOR", "")],
          note="the only stage that produces the gate's population, and the "
               "only one that runs a table of its own",
          writes_world="builds into the live world over RCON and appends to "
                       "its ledger",
          volatile=("--actor",)),
    Stage(13, "gate", "accept", _verdict, requires=("save", "ledger"),
          command=lambda s: [sys.executable, "-m", "accept",
                             "--world", s.world, "--seed", s.seed,
                             "--world-src", str(s.world_src),
                             "--json", str(s.work / f"gate-{s.world}.json")],
          note="eleven SECTIONS, one process -- the gate is one STAGE"),
)


# ---------------------------------------------------------------------------
# THE SEQUENCING CHECK
# ---------------------------------------------------------------------------
# WHAT THE ORDER DEPENDS ON, DECLARED.  Each name is a thing `audit_graph`,
# `artefact_state` or `first_unmet` actually reads.
#
# AND EVERY ONE OF THEM IS NEUTERED IN BOTH DIRECTIONS, because a one-direction
# neuter is passed by an index that returned NOTHING.  MEASURED on this file,
# first run of its own selftest: four cases were neutered by SWITCHING A
# DEPENDENCY OFF, and all four still came out the way they had to -- three
# accept arms, because an instrument that checks nothing accepts everything,
# and one refuse arm, because `first_unmet` refuses an unowned label whether or
# not it can name the owner.  Switching a check OFF only ever protects the
# accepting arm.  So each dependency has an OFF neuter, which the REFUSING arm
# must flip under, and a WRONG neuter -- reversed, blind, inverted -- which the
# ACCEPTING arm must flip under.  `survivors()` then reports which of the OTHER
# three still refuse, expressed as a VERDICT and never as an assert, so a
# neuter that emptied the instrument cannot stamp a case PROVEN.
AUDIT_DEPENDS_ON = ("ordinal", "owner_map", "scope_header", "presence",
                    "freshness", "reads", "actor")

# Swapped out by the selftest neuters.  Module-level so a neuter is a rebind
# and not a monkeypatch of a closure.
_ORDINAL_MODE = "strict"        # strict | off | reversed
_OWNER_MODE = "real"            # real   | off
_SCOPE_MODE = "read"            # read   | off | blind
_PRESENCE_MODE = "real"         # real   | present | absent
_FRESHNESS_MODE = "real"        # real   | off     | always
_READS_MODE = "real"            # real   | off     | foreign
_ACTOR_MODE = "real"            # real   | off     | constant


def owner_of(stages: tuple[Stage, ...], scope: Scope) -> dict[str, Stage]:
    """Which stage produces each artefact label."""
    out: dict[str, Stage] = {}
    for st in stages:
        for a in st.produces(scope):
            out[a.label] = st
    return out


def audit_graph(stages: tuple[Stage, ...] = STAGES,
                scope: Scope | None = None) -> list[str]:
    """Every ordering defect in the table, as one line each.  Empty is good.

    TWO DEFECTS, and they are different:

      UNOWNED    a stage requires an artefact NO stage produces.  That is a
                 prerequisite the pipeline expects a human to have made, which
                 is precisely the state that makes a pipeline unrepeatable.
      INVERTED   a stage requires an artefact a LATER stage produces.  The run
                 appears to work the first time -- the file is there, left by
                 the previous world -- and is wrong on a fresh seed.
    """
    scope = scope or Scope("Selftest", "Selftest", Path("/nonexistent"),
                           Path("/nonexistent"), Path("/nonexistent"))
    owners = owner_of(stages, scope) if _OWNER_MODE == "real" else {}
    bad: list[str] = []
    for st in stages:
        for label in st.requires:
            src = owners.get(label)
            if src is None:
                if _OWNER_MODE == "real":
                    bad.append(
                        f"UNOWNED: stage {st.ordinal} {st.name!r} requires "
                        f"{label!r} and no stage in this table produces it")
                continue
            late = (src.ordinal >= st.ordinal if _ORDINAL_MODE == "strict"
                    else src.ordinal <= st.ordinal if _ORDINAL_MODE == "reversed"
                    else False)
            if late:
                bad.append(
                    f"INVERTED: stage {st.ordinal} {st.name!r} requires "
                    f"{label!r}, which stage {src.ordinal} {src.name!r} "
                    f"produces -- that is not earlier")
    return bad


_GRAPH_DEFECTS = audit_graph()
if _GRAPH_DEFECTS:                                          # pragma: no cover
    raise SystemExit("REFUSING to load the pipeline table:\n  "
                     + "\n  ".join(_GRAPH_DEFECTS))


# ---------------------------------------------------------------------------
# THE READ CHECK
# ---------------------------------------------------------------------------
# READS_DEPENDS_ON: what a stage's READER actually opens, asked of the reader.
#
# `audit_graph` checks the table against ITSELF: a `requires` list typed by
# hand, against a `produces` list typed by hand.  Both can be internally
# consistent and still not be what the stage's tool reads -- a key naming a
# SUBSET of what the stage depends on -- and that is exactly the state
# MEASURED 2026-09-24 on Fresh42 from scratch: `sites` (then stage 6)
# declared `field, locations`, and `settlements/plan.py` also opens
# `worlds/<World>/*/placements.yaml` for its SOLVED blocks, which `placements`
# (then stage 8) produces.  audit_graph PASSED that table; the stage refused
# at 137.5 s.  So each stage that reads another stage's artefact carries a
# `reads` callable answered BY THE READER MODULE, and this audit refuses:
#
#   UNPRODUCED READ  the reader opens a file no stage of THIS world produces --
#                    a reader resolving another world (the Picker read
#                    worlds/Ulfsland for every seed) lands here;
#   INVERTED READ    it is produced by this stage or a later one;
#   UNDECLARED READ  it is produced earlier but its label is not in
#                    `requires`, so freshness and first_unmet never look.
#
# `_READS_MODE` "off" answers nothing (the refusing arm must flip under it);
# "foreign" asks the reader for a world other than the scope's -- the exact
# failure the Picker had -- which the accepting arm must flip under.
_FOREIGN_WORLD = "Ulfsland"


def audit_reads(stages: tuple[Stage, ...], scope: Scope) -> list[str]:
    """Every file a stage's reader opens that the table does not put before
    it, as one line each.  Empty is good."""
    if _READS_MODE == "off":
        return []
    asked = scope
    if _READS_MODE == "foreign":
        other = _FOREIGN_WORLD if scope.world != _FOREIGN_WORLD else "Fresh42"
        asked = Scope(scope.seed, other, scope.work, scope.world_root,
                      scope.vh_src)
    made = [(st, a) for st in stages for a in st.produces(scope)]
    bad: list[str] = []
    for st in stages:
        if st.reads is None:
            continue
        for path, glob, key in st.reads(asked):
            what = f"{path}/{glob}" + (f" [{key}]" if key else "")
            owners = [(o, a) for o, a in made
                      if Path(a.path) == Path(path) and a.glob == glob
                      and a.member_key == key]
            if not owners:
                bad.append(f"UNPRODUCED READ: stage {st.ordinal} {st.name!r} "
                           f"reads {what} and no stage of world "
                           f"{scope.world!r} produces it")
                continue
            for o, a in owners:
                if o.ordinal >= st.ordinal:
                    bad.append(f"INVERTED READ: stage {st.ordinal} "
                               f"{st.name!r} reads {what}, which stage "
                               f"{o.ordinal} {o.name!r} produces as "
                               f"{a.label!r} -- that is not earlier")
                elif a.label not in st.requires:
                    bad.append(f"UNDECLARED READ: stage {st.ordinal} "
                               f"{st.name!r} reads {what}, stage {o.ordinal} "
                               f"{o.name!r}'s {a.label!r}, and does not "
                               f"require it")
    return bad


# ---------------------------------------------------------------------------
# THE ACTOR CHECK
# ---------------------------------------------------------------------------
# ACTOR_DEPENDS_ON: every `--actor` a stage passes is $VH_ACTOR, the agent
# actually running the pipeline -- the only value provenance may carry.
#
# MEASURED 2026-09-24: stage 12's row already refused to default the actor to
# a name ("a literal like 'pipeline' would record every write in this world's
# ledger under an agent that does not exist"), and stage 10's row, eighty
# lines above it, passed exactly that literal: every `zones_generate` the
# census survey writes -- the first records of a from-scratch world's ledger
# -- would have been owned by `pipeline`.  So the argv is EVALUATED with a
# sentinel in $VH_ACTOR and every `--actor` value must be that sentinel.
# "off" checks nothing (the refusing arm flips); "constant" expects the old
# literal instead of the environment (the accepting arm flips).
_ACTOR_SENTINEL = "ActorAuditSentinel"
_OLD_ACTOR_LITERAL = "pipeline"


def audit_actors(stages: tuple[Stage, ...], scope: Scope) -> list[str]:
    """Every stage whose argv carries an `--actor` that is not $VH_ACTOR."""
    if _ACTOR_MODE == "off":
        return []
    want = _ACTOR_SENTINEL if _ACTOR_MODE == "real" else _OLD_ACTOR_LITERAL
    prior = os.environ.get("VH_ACTOR")
    os.environ["VH_ACTOR"] = _ACTOR_SENTINEL
    bad: list[str] = []
    try:
        for st in stages:
            if st.command is None:
                continue
            argv = st.command(scope)
            for i, tok in enumerate(argv[:-1]):
                if tok == "--actor" and argv[i + 1] != want:
                    bad.append(f"CONSTANT ACTOR: stage {st.ordinal} "
                               f"{st.name!r} passes --actor "
                               f"{argv[i + 1]!r}, not $VH_ACTOR")
    finally:
        if prior is None:
            os.environ.pop("VH_ACTOR", None)
        else:
            os.environ["VH_ACTOR"] = prior
    return bad


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------
MISSING = "MISSING"
PRESENT = "PRESENT"
OUT_OF_SCOPE = "OUT_OF_SCOPE"
PRODUCER_SLOT_MISSING = "PRODUCER_SLOT_MISSING"
# PRESENT, declaring this seed, and DERIVED FROM AN OLDER INPUT THAN THE ONE ON
# DISK.  A distinct state because the remedy is distinct: MISSING says run the
# stage, STALE says run it AGAIN.
STALE = "STALE"


def _members(a: Artefact) -> list[Path]:
    """Every file this artefact IS.  One, or every match of its glob."""
    if not a.glob:
        return [a.path]
    return sorted(a.path.glob(a.glob)) if a.path.is_dir() else []


def _header_scope(path: Path, kind: str) -> tuple[str | None, str | None]:
    """What the artefact says about ITSELF.  Never what its name implies."""
    if kind == SCOPE_YAML:
        import yaml                                         # noqa: PLC0415
        doc = yaml.safe_load(path.read_text())
        if not isinstance(doc, dict):
            return None, None
        return doc.get("seed"), doc.get("world")
    if kind == SCOPE_BINARY:
        # `field.header()` and NOT `field.load()`: load() parses the whole
        # 1,105,747,603-byte file and THROWS THE HEADER AWAY (field.py:263
        # skips the seed hash, :265 skips the name), so a scope read built on
        # it reports "carries no seed declaration" about a file whose first
        # 4 KiB name it verbatim. MEASURED on this run, 2026-09-23.
        import importlib                                    # noqa: PLC0415
        fieldmod = importlib.import_module("roads.field")
        head = fieldmod.header(str(path))
        return head.get("seed"), None
    return None, None


def artefact_state(a: Artefact, scope: Scope) -> tuple[str, str]:
    """(state, detail) for one artefact.  Reads the declaration, not the name.

    EVERY member is scope-checked, not the first: a world directory holding
    nine presets of which one still declares another seed is exactly the join
    of two worlds, and a reader that stops at the first match would call it
    clean.
    """
    if a.producer_slot_missing:
        return PRODUCER_SLOT_MISSING, a.producer_slot_missing
    members = _members(a) if _PRESENCE_MODE == "real" else (
        [a.path] if _PRESENCE_MODE == "present" else [])
    here = bool(members) and (_PRESENCE_MODE != "real"
                              or all(p.exists() for p in members))
    if not here:
        return MISSING, (f"{a.path}/{a.glob} matches nothing" if a.glob
                         else f"{a.path} does not exist")
    if _SCOPE_MODE == "off" or not a.scope_kind:
        return PRESENT, str(a.path)
    for p in members:
        if _SCOPE_MODE == "blind":
            # THE OTHER WAY THE SAME DEPENDENCY BREAKS: a reader that looks up
            # the wrong key reads None out of a file that DOES declare itself,
            # and require_scope then refuses an artefact that was in scope all
            # along.
            found_seed, found_world = None, None
        else:
            try:
                found_seed, found_world = _header_scope(p, a.scope_kind)
            except Exception as exc:                        # noqa: BLE001
                return OUT_OF_SCOPE, f"{p}: unreadable declaration ({exc!r})"
        try:
            worldseed.require_scope(a.label, p, found_seed=found_seed,
                                    found_world=found_world,
                                    seed_=scope.seed, world_=scope.world)
        except SystemExit as exc:
            return OUT_OF_SCOPE, str(exc)
    if a.member_key and _PRESENCE_MODE == "real":
        import yaml                                         # noqa: PLC0415
        total = filled = 0
        for p in members:
            doc = yaml.safe_load(p.read_text()) or {}
            for rec in doc.get("placements") or []:
                total += 1
                filled += bool(rec.get(a.member_key))
        if total and filled < total:
            return MISSING, (f"{a.path}/{a.glob}: {filled} of {total} "
                             f"placements carry a {a.member_key!r} block -- "
                             f"the templates are authored, the stage that "
                             f"fills them has not run")
    return PRESENT, str(a.path)


def _newest(a: Artefact) -> float:
    """The newest mtime this artefact has, or 0 when it has none.

    Gated on `_PRESENCE_MODE` because "when was it written" is the SAME
    filesystem question as "is it there": a neuter that blinds presence and
    leaves the mtime probe sighted would let freshness answer presence's
    question and stamp presence PROVEN off a reading it did not make.
    """
    if _PRESENCE_MODE != "real":
        return 0.0
    out = 0.0
    for p in _members(a):
        try:
            out = max(out, p.stat().st_mtime)
        except OSError:
            pass
    return out


# ---------------------------------------------------------------------------
# PRODUCER CURRENCY
# ---------------------------------------------------------------------------
# STAGE_CURRENCY_DEPENDS_ON: a stage's output is CURRENT only when BOTH hold.
#
#   artefacts_present     every declared artefact is PRESENT and in scope --
#                         `artefact_state`, unchanged, switched by the same
#                         `_PRESENCE_MODE` the audit neuters;
#   producer_fingerprint  the producer that made them is the producer on disk:
#                         the sha256 of every file in the population recorded
#                         when the stage last SUCCEEDED, plus the stage's own
#                         row (its argv template and post hook).
#
# THE DEFECT, MEASURED 2026-09-25.  `run` skipped a stage when `satisfied`,
# and `satisfied` was the first term alone -- a key naming a SUBSET of what a
# stage's output depends on.  `blueprints/solve_placements.py` was fixed twice
# (pads sized at the body's yaw; a clamp breach ranked behind every site that
# fits), `worlds/Fresh42/*/placements.yaml` was the PRE-FIX solver's, and
# `pipeline.py status` read stage 7 `ok` -- 11 of 13 stages `ok`, stage 12
# among them with a build running.  The only escape was `--force`, which
# re-runs stage 1 and re-creates the save.
#
# THE POPULATION, AND HOW IT WAS CHOSEN.  A static import closure was
# MEASURED and is wrong in both directions.  Counting every import in every
# function, stage 4 (`roads/field.py`) reaches 87 files -- the whole tree,
# through route.py's lazy `import ribbon` and ribbon's selftest `import
# buildstage` -- so an edit to the gate would re-run a 1.1 GB PatchScan.
# Counting module-level imports only, stage 7 loses
# `blueprints/to_rcon_plan.py`, which solve_placements imports INSIDE
# `container_census`: exactly a fix that would be missed.  So the population
# is ASKED OF THE PRODUCER WHILE IT RUNS, the way `reads` is asked of the
# reader: every Python process a stage starts carries an audit hook
# (`_TRACE_SITE`) reporting the modules it loaded, the files it opened
# read-only and the programs it started, under `tools/`; shell scripts are
# closed over by the paths their text names (`$HERE/build.sh` -> `*.cs`).
# Removed: declared artefacts of any stage, the work directory,
# `ledger/runs/`, bytecode caches, and anything the stage itself WROTE.  What
# remains is the code and versioned data (placement templates, piece
# geometry) that decided this output -- no less, because it is what executed,
# and no more, because nothing that did not execute is in it.  The game
# install, its plugins and the interpreter are the HOST, not the producer.
#
# UNREAD IS NOT CLEAR.  Artefacts on disk with no record for this seed/world
# -- everything made before this existed, or by a tool run outside
# `pipeline.py run` -- are UNKNOWN PRODUCER, never satisfied.  So is a record
# whose population was not read in full (a Python process that left no trace,
# or a Python child started with an environment that dropped the hook).
STAGE_CURRENCY_DEPENDS_ON = ("artefacts_present", "producer_fingerprint")
# What `run` decides with it.  descendants: a stage downstream (by `requires`)
# of one that ran in THIS run re-runs.  from_bound: `--from K` never runs a
# stage below K.  world_guard: a stage that writes the live world runs, unless
# the operator named it, only to CREATE its output.
RUN_DEPENDS_ON = ("descendants", "from_bound", "world_guard")

CURRENT = "CURRENT"
UNKNOWN_PRODUCER = "UNKNOWN PRODUCER"
NOT_PRODUCED = "NOT PRODUCED"
PRODUCER_JOURNAL = "producers.json"
# `tools/` and not `tools/jumpstart/`: stages 1-3 run `tools/valheim_worldgen.py`
# and `tools/seedscan/`.
PRODUCER_ROOT = REPO / "tools"

_PRODUCER_MODE = "real"         # real | off | blind | tool-only
_DESCENDANT_MODE = "real"       # real | off | ordinal
_FROM_MODE = "real"             # real | ignored
_WORLD_GUARD_MODE = "real"      # real | off | always


def _under(p: Path, root: Path) -> bool:
    return str(p).startswith(str(root) + os.sep)


def _rel(p: Path) -> str:
    """Repo-relative inside the repo, so a moved checkout is the same producer."""
    try:
        return str(p.relative_to(REPO))
    except ValueError:
        return str(p)


def _abs(rel: str) -> Path:
    p = Path(rel)
    return p if p.is_absolute() else REPO / p


def _file_sha(p: Path) -> str | None:
    import hashlib                                          # noqa: PLC0415
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return None


def producer_records(scope: Scope) -> dict[str, dict]:
    """This seed/world's producer records, by stage name.

    A record for another seed or world is not this world's, even in this
    file: `--work` can be shared, and a record read across worlds is the
    12,301-Pirate68-locations defect in a new place.
    """
    if _PRODUCER_MODE == "blind":
        # THE WRONG KEY: a reader that looks records up by something other
        # than what they are stored under finds none, and every stage reads
        # UNKNOWN PRODUCER for ever.
        return {}
    try:
        doc = json.loads((scope.work / PRODUCER_JOURNAL).read_text())
    except (OSError, ValueError):
        return {}
    return {name: rec for name, rec in (doc.get("stages") or {}).items()
            if rec.get("seed") == scope.seed and rec.get("world") == scope.world}


def _store_record(scope: Scope, name: str, rec: dict | None) -> None:
    """Replace (or with None, DROP) one stage's record.  Atomic."""
    path = scope.work / PRODUCER_JOURNAL
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError):
        doc = {}
    stages = doc.setdefault("stages", {})
    if rec is None and name not in stages:
        return
    if rec is None:
        stages.pop(name, None)
    else:
        stages[name] = rec
    doc["schema"] = 1
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True))
    os.replace(tmp, path)


# The row, asked for NO world: a fingerprint of the row that carried this
# run's paths would call the same producer different on another --work.
_ROW_PROBE = Scope("<seed>", "<world>", Path("/<work>"), Path("/<world_root>"),
                   Path("/<vh_src>"))


def row_declaration(st: Stage) -> list[str]:
    """The stage's own row: its argv template and its post hook's source.

    Part of the producer because it IS the invocation -- `STEP=8`, `PREGEN=1`
    and `_adopt_solve` decide output and live in this file, not in the tool.
    The interpreter, the repo's location and every value after a declared
    `volatile` flag are masked: those are the host and the agent.
    """
    argv = [str(t) for t in st.command(_ROW_PROBE)] if st.command else []
    out: list[str] = []
    mask = False
    for tok in argv:
        if mask:
            out.append("<volatile>")
            mask = False
            continue
        tok = "<python>" if tok == sys.executable else tok.replace(str(REPO), "<repo>")
        out.append(tok)
        mask = tok in st.volatile
    if st.post is not None:
        import inspect                                      # noqa: PLC0415
        out.append("post:" + inspect.getsource(st.post))
    if st.refines is not None:
        # The fixed point decides what the earlier stage reads, so it is
        # part of this row's invocation (CENSUS_LOOP_DEPENDS_ON).
        out.append("refines:" + st.refines[0] + " " + " ".join(
            str(t).replace(str(REPO), "<repo>")
            for t in st.refines[2](_ROW_PROBE)))
    return out


def _row_sha(st: Stage) -> str:
    import hashlib                                          # noqa: PLC0415
    return hashlib.sha256(json.dumps(row_declaration(st)).encode()).hexdigest()


_SHELL_REF = None       # compiled on first use


def _shell_refs(path: Path, root: Path) -> set[Path]:
    """Every file a shell script's TEXT names that exists under `root`.

    `$HERE/build.sh`, `"$REPO/tools/seedscan/run_scan.sh"`, `$HERE/SeedScan.cs`:
    the longest tail of the named path that resolves against the script's own
    directory, tools/jumpstart, tools/ or the repo wins.  A script is not
    traced (it is not Python), so what it names is its population.
    """
    global _SHELL_REF
    import re                                               # noqa: PLC0415
    if _SHELL_REF is None:
        _SHELL_REF = re.compile(r"((?:[\w.-]+/)*[\w.-]+\.(?:sh|py|cs))\b")
    out: set[Path] = set()
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return out
    bases = (path.parent, HERE, REPO / "tools", REPO)
    for m in _SHELL_REF.finditer(text):
        parts = m.group(1).split("/")
        hit = None
        for k in range(len(parts)):
            tail = Path(*parts[k:])
            hit = next(((b / tail).resolve() for b in bases
                        if (b / tail).is_file()), None)
            if hit is not None:
                break
        if hit is not None and _under(hit, root):
            out.add(hit)
    return out


def _argv_files(argv: list[str], root: Path) -> set[Path]:
    """The tool files the stage's own argv names (`-m pkg` is its __main__)."""
    out: set[Path] = set()
    for i, tok in enumerate(argv):
        if tok == "-m" and i + 1 < len(argv):
            for base in (HERE, REPO / "tools"):
                d = base.joinpath(*argv[i + 1].split("."))
                for c in (d / "__main__.py", d / "__init__.py", d.with_suffix(".py")):
                    if c.is_file() and _under(c.resolve(), root):
                        out.add(c.resolve())
            continue
        p = Path(tok)
        if p.is_absolute() and p.is_file() and _under(p.resolve(), root):
            out.add(p.resolve())
    return out


# THE HOOK EVERY PYTHON PROCESS OF A STAGE CARRIES.  Written into a temporary
# directory at PYTHONPATH's head for the length of ONE stage, and removed with
# it; it chains to the sitecustomize it shadows (Ubuntu's installs apport's
# hook) so tracing a stage changes nothing else its interpreter does.  Each
# process writes a `.start` marker when it starts and a `.json` report at
# exit: a marker with no report is a process whose population was NOT read,
# and the record says so rather than being current on a partial reading.
_TRACE_SITE = '''\
"""Written by tools/jumpstart/pipeline.py for ONE stage run; deleted after it."""
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))


def _chain():
    import importlib.machinery as _m
    import importlib.util as _u
    rest = [p for p in _sys.path if _os.path.abspath(p or ".") != _HERE]
    spec = _m.PathFinder.find_spec("sitecustomize", rest)
    if spec is None or spec.loader is None:
        return
    try:
        spec.loader.exec_module(_u.module_from_spec(spec))
    except Exception:
        pass


def _install():
    out = _os.environ.get("VH_PRODUCER_TRACE", "")
    root = _os.environ.get("VH_PRODUCER_ROOT", "")
    if not out or not root:
        return
    import atexit
    import json
    import shlex
    import uuid
    token = "%d-%s" % (_os.getpid(), uuid.uuid4().hex[:12])
    root = _os.path.realpath(root) + _os.sep
    cwd0 = _os.getcwd()
    wflags = (_os.O_WRONLY | _os.O_RDWR | _os.O_APPEND | _os.O_CREAT
              | _os.O_TRUNC)
    read, wrote, execd, untraced = set(), set(), set(), []

    def full(p, cwd=None):
        try:
            p = _os.fsdecode(p)
        except TypeError:
            return None
        if not _os.path.isabs(p):
            p = _os.path.join(cwd or _os.getcwd(), p)
        return _os.path.realpath(p)

    def started(exe, argv, cwd, env):
        toks = []
        for t in ([exe] if exe else []) + list(argv or []):
            try:
                t = _os.fsdecode(t)
            except TypeError:
                continue
            try:
                toks.extend(shlex.split(t) if " " in t else [t])
            except ValueError:
                toks.extend(t.split())
        pyish = False
        for t in toks:
            if "python" in _os.path.basename(t) or t.endswith(".py"):
                pyish = True
            a = full(t, cwd)
            if a and a.startswith(root) and _os.path.isfile(a):
                execd.add(a)
        if pyish and env is not None and \\
                env.get("VH_PRODUCER_TRACE") != out:
            untraced.append(" ".join(toks)[:300])

    def hook(ev, args):
        try:
            if ev == "open":
                p, mode, flags = args
                if isinstance(p, int):
                    return
                a = full(p)
                if not a or not a.startswith(root):
                    return
                w = ((isinstance(flags, int) and flags & wflags)
                     or (isinstance(mode, str)
                         and any(c in mode for c in "wax+")))
                (wrote if w else read).add(a)
            elif ev == "subprocess.Popen":
                started(args[0], args[1], args[2], args[3])
            elif ev in ("os.exec", "os.posix_spawn"):
                started(args[0], args[1], None, args[2])
        except Exception:
            pass

    _sys.addaudithook(hook)
    with open(_os.path.join(out, token + ".start"), "w"):
        pass

    def dump():
        mods = set()
        for m in list(_sys.modules.values()):
            f = getattr(m, "__file__", None)
            if isinstance(f, str):
                a = full(f, cwd0)
                if a and a.startswith(root):
                    mods.add(a)
        doc = {"pid": _os.getpid(), "modules": sorted(mods),
               "read": sorted(read), "wrote": sorted(wrote),
               "execd": sorted(execd), "untraced": untraced}
        tmp = _os.path.join(out, token + ".json.tmp")
        with open(tmp, "w") as fh:
            json.dump(doc, fh)
        _os.replace(tmp, _os.path.join(out, token + ".json"))

    atexit.register(dump)


_chain()
_install()
'''


def _trace_begin(root: Path) -> tuple[Path, dict[str, str]]:
    """A trace directory for one stage, and the environment that arms it."""
    import tempfile                                         # noqa: PLC0415
    d = Path(tempfile.mkdtemp(prefix="vh-producer-trace-"))
    (d / "site").mkdir()
    (d / "out").mkdir()
    (d / "site" / "sitecustomize.py").write_text(_TRACE_SITE)
    path = [str(d / "site")] + ([os.environ["PYTHONPATH"]]
                                if os.environ.get("PYTHONPATH") else [])
    return d, {"VH_PRODUCER_TRACE": str(d / "out"),
               "VH_PRODUCER_ROOT": str(root),
               "PYTHONPATH": os.pathsep.join(path)}


def _trace_read(d: Path) -> dict:
    """Every process's report, merged, and the processes that sent none."""
    got: dict = {"modules": set(), "read": set(), "wrote": set(),
                 "execd": set(), "untraced": [], "silent": [], "processes": 0}
    out = d / "out"
    started = {p.name[:-len(".start")] for p in out.glob("*.start")}
    reported = set()
    for p in out.glob("*.json"):
        try:
            doc = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        reported.add(p.name[:-len(".json")])
        for k in ("modules", "read", "wrote", "execd"):
            got[k].update(Path(x) for x in doc.get(k, []))
        got["untraced"].extend(doc.get("untraced", []))
    got["processes"] = len(started)
    got["silent"] = sorted(started - reported)
    return got


def _not_producer(scope: Scope, stages: tuple[Stage, ...]) -> Callable[[Path], bool]:
    """What a stage touches that is NOT its producer: artefacts (any stage's),
    the work directory, the world ledgers, bytecode caches."""
    fences = [scope.work.resolve(), (HERE / "ledger" / "runs").resolve()]
    files: set[Path] = set()
    for st in stages:
        for a in st.produces(scope):
            p = a.path.resolve()
            if a.glob or p.is_dir():
                fences.append(p)
            else:
                files.add(p)

    def excluded(p: Path) -> bool:
        if "__pycache__" in p.parts or p.suffix == ".pyc" or p in files:
            return True
        return any(p == f or _under(p, f) for f in fences)
    return excluded


def producer_population(st: Stage, scope: Scope, stages: tuple[Stage, ...],
                        argv: list[str], trace: dict | None, root: Path
                        ) -> tuple[dict[str, str], list[str]]:
    """`({file: how it entered}, [why the reading is incomplete])`."""
    how: dict[Path, str] = {p: "argv" for p in sorted(_argv_files(argv, root))}
    incomplete: list[str] = []
    if _PRODUCER_MODE != "tool-only":
        if trace is not None:
            for p in sorted(trace["modules"]):
                how.setdefault(p, "import")
            for p in sorted(trace["execd"]):
                how.setdefault(p, "exec")
            for p in sorted(trace["read"] - trace["wrote"]):
                how.setdefault(p, "read")
            incomplete += [f"a Python process ({t}) left no trace"
                           for t in trace["silent"]]
            incomplete += [f"a Python child started without the trace: {u}"
                           for u in trace["untraced"]]
        todo = [p for p in how if p.suffix == ".sh"]
        while todo:
            sh = todo.pop()
            for r in sorted(_shell_refs(sh, root)):
                if r not in how:
                    how[r] = f"named by {_rel(sh)}"
                    if r.suffix == ".sh":
                        todo.append(r)
    excluded = _not_producer(scope, stages)
    return ({_rel(p): h for p, h in sorted(how.items())
             if _under(p, root) and not excluded(p)}, incomplete)


def _record_success(scope: Scope, st: Stage, stages: tuple[Stage, ...],
                    argv: list[str], trace: dict | None, root: Path) -> dict:
    pop, incomplete = producer_population(st, scope, stages, argv, trace, root)
    # A file the run touched and that is gone by the time it succeeded was a
    # scratch file, not a producer: nothing can be compared against it.
    files = {rel: sha for rel in pop
             if (sha := _file_sha(_abs(rel))) is not None}
    # WHICH WORLD this stage wrote into, read AFTER it ran: its own writes are
    # part of the lineage it leaves, not a later change to it.
    lineage: dict[str, list] = {}
    by_label = {a.label: a for s in stages for a in s.produces(scope)}
    for lbl, a in _in_place_inputs(st, by_label).items():
        try:
            got = _read_lineage(a)
        except Exception:                                   # noqa: BLE001
            got = None
        if got is not None:
            lineage[lbl] = list(got)
    rec = {"seed": scope.seed, "world": scope.world, "ordinal": st.ordinal,
           "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "row": _row_sha(st), "files": files,
           "via": {rel: pop[rel] for rel in files}, "incomplete": incomplete,
           "processes": trace["processes"] if trace else 0,
           "lineage": lineage}
    _store_record(scope, st.name, rec)
    return rec


def producer_state(st: Stage, rec: dict | None, on_disk: bool) -> dict:
    """`{state, recorded, detail, changed}` for one stage's producer."""
    if _PRODUCER_MODE == "off":
        # THE SHIPPED BEHAVIOUR: the producer term dropped, satisfied is
        # presence alone.
        return {"state": CURRENT, "recorded": rec is not None, "changed": [],
                "detail": "producer term not read"}
    if rec is None:
        if on_disk:
            return {"state": UNKNOWN_PRODUCER, "recorded": False, "changed": [],
                    "detail": f"artefacts on disk and NO producer record for "
                              f"this seed/world in {PRODUCER_JOURNAL}: made "
                              f"before producer recording existed, or outside "
                              f"`pipeline.py run`. UNREAD IS NOT CLEAR"}
        return {"state": NOT_PRODUCED, "recorded": False, "changed": [],
                "detail": "nothing on disk and no producer record"}
    if rec.get("incomplete"):
        return {"state": UNKNOWN_PRODUCER, "recorded": True, "changed": [],
                "detail": "the producing run's population was not read in "
                          "full: " + "; ".join(rec["incomplete"][:3])}
    changed = []
    for rel, sha in sorted((rec.get("files") or {}).items()):
        now = _file_sha(_abs(rel))
        if now != sha:
            changed.append(f"{rel} {'DELETED' if now is None else 'changed'}")
    if _row_sha(st) != rec.get("row"):
        changed.append(f"pipeline.py stage {st.ordinal} row (argv template / "
                       f"post hook) changed")
    if changed:
        more = f" (+{len(changed) - 4} more)" if len(changed) > 4 else ""
        return {"state": STALE, "recorded": True, "changed": changed,
                "detail": f"producer changed since this stage succeeded at "
                          f"{rec.get('recorded_at')}: "
                          + ", ".join(changed[:4]) + more}
    return {"state": CURRENT, "recorded": True, "changed": [],
            "detail": f"{len(rec.get('files') or {})} producer files and the "
                      f"row unchanged since {rec.get('recorded_at')}"}


def _not_current(row: dict) -> str:
    """Why a status row is not satisfied, in one line."""
    bits = [f"{a['label']} {a['state']}" for a in row["artefacts"]
            if a["state"] != PRESENT]
    p = row["producer"]
    if p["state"] != CURRENT:
        bits.append(f"producer {p['state']}: {p['detail']}")
    return "; ".join(bits) or "current"


def _world_guard_refuses(row: dict) -> bool:
    """WORLD_GUARD: may a plain `run` (re-)run this world-writing stage?

    Only to CREATE its output: no producer record AND an artefact missing.
    Anything else -- a changed producer, an unknown one, a re-run upstream --
    is re-making a world, and that is an operator's decision.
    """
    if _WORLD_GUARD_MODE == "off":
        return False
    if _WORLD_GUARD_MODE == "always":
        return True
    creating = (not row["producer"]["recorded"]
                and any(a["state"] == MISSING for a in row["artefacts"]))
    return not creating


def _redo_command(scope: Scope, k: int, through: int) -> str:
    """The command an operator runs to redo stage `k` and everything after."""
    import shlex                                            # noqa: PLC0415
    argv = ["python3", _rel(HERE / "pipeline.py"), "run", "--seed", scope.seed,
            "--world", scope.world, "--from", str(k), "--through", str(through)]
    if scope.work.resolve() != (HERE / "ledger" / "runs" / scope.world
                                / "pipeline").resolve():
        argv += ["--work", str(scope.work)]
    if str(scope.world_root) != WORLD_ROOT_DEFAULT:
        argv += ["--world-root", str(scope.world_root)]
    if str(scope.vh_src) != VH_SRC_DEFAULT:
        argv += ["--vh-src", str(scope.vh_src)]
    return " ".join(shlex.quote(a) for a in argv)


# IN_PLACE_DEPENDS_ON: what a WORLD-WRITING stage's output depends on in the
# world it writes into -- WHICH world it is (`lineage`), not when it was last
# written.
#
# THE DEFECT, MEASURED 2026-09-25.  `status` keyed every input on its mtime,
# and the save is an input that stages 10 and 12 WRITE IN PLACE and that the
# server re-saves every 30 minutes (MEASURED: 08:28:53, 30 min after this
# drive's server start, on a world nobody had touched).  So the census was
# STALE as soon as the next autosave landed, and the build's ledger as soon as
# its own writes were saved -- drive 1's `status` read census STALE and ledger
# STALE against the save of its own graceful shutdown.  A key naming a
# SUPERSET: the save's mtime moves for the stage's OWN writes, for writes
# downstream of it, and for no write at all.  The consequence is not
# cosmetic: `first_unmet` refuses on a STALE census, so a plain run could not
# take a built world to stage 13, and `run` re-reads it before every stage.
#
# `lineage` is the key the save exposes for "the world this was made in":
# the world UID and the save generation (`save_lineage`).  The consuming
# stage's record carries it; a later reading with a different UID (re-created)
# or a LOWER generation (a copy put back over it) is STALE, anything that only
# moved it forward is not.  Only for a stage that WRITES THE WORLD: the gate
# reads the save's CONTENT and keeps the mtime rule.  A record without a
# lineage (made before this existed) keeps the mtime rule too.
IN_PLACE_DEPENDS_ON = ("lineage",)
_LINEAGE_MODE = "real"          # real | mtime (the shipped key) | off


def _in_place_inputs(st: Stage, by_label: dict[str, Artefact]
                     ) -> dict[str, Artefact]:
    """The inputs `st` writes into: required artefacts that declare a
    lineage, when `st` writes the world."""
    if _LINEAGE_MODE == "mtime" or not st.writes_world:
        return {}
    return {lbl: by_label[lbl] for lbl in st.requires
            if lbl in by_label and by_label[lbl].lineage is not None}


def _read_lineage(a: Artefact) -> tuple[str, int] | None:
    got = a.lineage(a.path)
    return None if got is None else (str(got[0]), int(got[1]))


def _lineage_breaks(in_place: dict[str, Artefact], rec: dict | None
                    ) -> tuple[list[str], set[str]]:
    """`(why each in-place input is no longer the world `rec` was made in,
    labels whose lineage `rec` does not carry)`."""
    breaks: list[str] = []
    unrecorded: set[str] = set()
    held = (rec or {}).get("lineage") or {}
    for lbl, a in in_place.items():
        was = held.get(lbl)
        if not was:
            unrecorded.add(lbl)
            continue
        if _LINEAGE_MODE == "off":
            continue
        try:
            now = _read_lineage(a)
        except Exception as exc:                            # noqa: BLE001
            breaks.append(f"{lbl}: lineage unreadable ({exc!r})")
            continue
        if now is None:
            breaks.append(f"{lbl}: holds no save any more")
        elif now[0] != was[0]:
            breaks.append(f"{lbl} is a DIFFERENT WORLD ({was[0]} -> {now[0]}): "
                          f"re-created since this stage wrote into it")
        elif now[1] < int(was[1]):
            breaks.append(f"{lbl} was ROLLED BACK (generation {was[1]} -> "
                          f"{now[1]}): an older copy was put back over the "
                          f"world this stage wrote into")
    return breaks, unrecorded


def status(scope: Scope, stages: tuple[Stage, ...] = STAGES) -> list[dict]:
    """Every stage's artefacts, and whether the stage still owes work.

    THE FIFTH BLOCKER, EXPRESSED AS A STATE.  "The solver's own inputs were
    never re-derived after they changed, and only an actual re-solve reaches
    them" is not a missing artefact -- every file is there and every header
    declares the right seed.  It is an artefact OLDER THAN THE INPUT IT WAS
    DERIVED FROM, and nothing in this tree looked.  MEASURED 2026-09-23 on
    Fresh42: `roads/geography.Fresh42.yaml` was derived on 2026-09-22 from a
    NINE-window 115,200,404-byte field, and this run's field is 26 windows;
    a status pass that only asked "does it exist, does it say Fresh42" calls
    that geography good.

    mtime and not a content hash, deliberately, because it is the rule that
    needs NOTHING FROM THE ARTEFACT: a hash-of-inputs check can only cover the
    artefacts that already record their inputs, which is the subset that was
    never the problem.

    AND `satisfied` IS NO LONGER PRESENCE ALONE.  `present` is that (every
    artefact PRESENT); `producer` is the stage's producer currency; and
    `satisfied` is both -- STAGE_CURRENCY_DEPENDS_ON.  A stage whose tool was
    fixed after it ran is STALE here with the changed file named, and one
    with no record is UNKNOWN PRODUCER, never ok.
    """
    rows = []
    records = producer_records(scope)
    newest: dict[str, float] = {}
    by_label: dict[str, Artefact] = {}
    for st in stages:
        for a in st.produces(scope):
            newest[a.label] = _newest(a)
            by_label[a.label] = a
    for st in stages:
        arts = []
        mine = st.produces(scope)
        in_place = _in_place_inputs(st, by_label)
        breaks, unrecorded = _lineage_breaks(in_place, records.get(st.name))
        # An in-place input is read by LINEAGE; one whose lineage this stage's
        # record does not carry falls back to the mtime rule, never to clear.
        timed = [lbl for lbl in st.requires
                 if lbl not in in_place or lbl in unrecorded]
        want = max((newest.get(lbl, 0.0) for lbl in timed), default=0.0)
        for a in mine:
            state, detail = artefact_state(a, scope)
            if _FRESHNESS_MODE == "always" and state == PRESENT and st.requires:
                state, detail = STALE, f"{a.path}: freshness neutered"
            elif (_FRESHNESS_MODE == "real" and state == PRESENT and want
                    and _newest(a) < want):
                late = [lbl for lbl in timed
                        if newest.get(lbl, 0.0) > _newest(a)]
                state = STALE
                detail = (f"{a.path} is OLDER than the input it is derived "
                          f"from ({', '.join(late)}); it declares the right "
                          f"seed and is still an answer to a different "
                          f"question")
            elif _FRESHNESS_MODE == "real" and state == PRESENT and breaks:
                state = STALE
                detail = (f"{a.path} was made in a world that is no longer "
                          f"the one on disk: " + "; ".join(breaks))
            arts.append({"label": a.label, "path": str(a.path),
                         "state": state, "detail": detail})
        present = all(a["state"] == PRESENT for a in arts)
        on_disk = any(a["state"] not in (MISSING, PRODUCER_SLOT_MISSING)
                      for a in arts)
        prod = producer_state(st, records.get(st.name), on_disk)
        rows.append({"ordinal": st.ordinal, "name": st.name, "owner": st.owner,
                     "requires": list(st.requires), "manual": st.manual,
                     "runnable": st.command is not None, "artefacts": arts,
                     "writes_world": st.writes_world, "present": present,
                     "producer": prod,
                     "satisfied": present and prod["state"] == CURRENT})
    return rows


def first_unmet(scope: Scope, stages: tuple[Stage, ...] = STAGES
                ) -> tuple[Stage, str, str] | None:
    """The FIRST stage that cannot run, the label it wants, and why.

    "First" and not "any": a report that lists ten unmet stages when the first
    one is the only one anybody can act on is a report that has to be read
    backwards.
    """
    rows = {r["name"]: r for r in status(scope, stages)}
    produced: dict[str, dict] = {}
    for st in stages:
        for a in rows[st.name]["artefacts"]:
            produced[a["label"]] = a
    for st in stages:
        for label in st.requires:
            a = produced.get(label)
            if a is None or a["state"] != PRESENT:
                src = owner_of(stages, scope).get(label)
                return (st, label,
                        f"{a['state'] if a else 'UNOWNED'}: {a['detail'] if a else label}"
                        + (f" -- produced by stage {src.ordinal} {src.name!r}"
                           if src else ""))
        # `present`, not `satisfied`: this asks whether the pipeline CAN
        # proceed, and a stage whose producer changed can still be re-run.
        if not rows[st.name]["present"] and st.command is None:
            bad = [a for a in rows[st.name]["artefacts"] if a["state"] != PRESENT]
            return (st, bad[0]["label"],
                    f"{bad[0]['state']}: {bad[0]['detail']} -- and this stage "
                    f"HAS NO COMMAND: {st.manual}")
    return None


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------
def _reproduced_upstream(st: Stage, owners: dict[str, Stage],
                         ran: list[str]) -> list[str]:
    """The stages that ran in THIS run and whose artefacts `st` requires."""
    if _DESCENDANT_MODE == "off":
        return []
    if _DESCENDANT_MODE == "ordinal":
        # THE WRONG KEY: "after" a re-run stage instead of "downstream of"
        # it -- a superset, and every edit re-runs the rest of the table.
        return list(ran)
    return sorted({owners[lbl].name for lbl in st.requires
                   if lbl in owners and owners[lbl].name in ran})


def run(scope: Scope, through: int, stages: tuple[Stage, ...] = STAGES,
        force: bool = False, replace_live: bool = False,
        from_: int | None = None, producer_root: Path | None = None) -> int:
    """Run stages in order up to `through`, STOPPING at the first refusal.

    The journal is written after every stage, in the durable work directory, so
    a run killed halfway leaves a readable record of exactly how far it got.

    AND IT REFUSES THE LIVE WORLD OUTRIGHT.  Four of these stages write into
    the repository's own corpus -- `settlements/sites.yaml`,
    `roads/geography.yaml`, `roads/segments.yaml`, `roads/crossings.yaml` --
    and on the DEFAULT seed those are the finished Ulfsland documents eleven
    gate sections read.  `status` and `graph` are read-only and answer for any
    world; `run` is not, so the one world whose corpus cannot be re-derived
    from a re-run is the one world this refuses to re-run.

    WHICH STAGES RUN (STAGE_CURRENCY_DEPENDS_ON, RUN_DEPENDS_ON):

      plain      every stage that is not CURRENT, and every stage downstream
                 of one that ran in this run; a CURRENT stage is left alone.
      --from K   K and every later stage through `through`, unconditionally,
                 and NOTHING below K whatever its state: a stage below K that
                 is not current is printed as left standing, never run.
      --force    every stage through `through` -- unchanged, a deliberate
                 full redo, stage 1 included.

    WORLD_GUARD.  A stage that WRITES THE LIVE WORLD (`writes_world`) is run
    by a plain `run` only to CREATE its output.  Stale, unknown producer or
    downstream of a re-run, it is REFUSED (exit 6) with the reason and the
    `--from` command that would redo it: an operator regenerates a world, a
    file edit does not.

    A stage's record is DROPPED before it runs and written only when it and
    its post hook succeed, with the population its processes reported -- a
    stage that failed halfway has no producer, not the last good one's.
    """
    if worldseed.is_default(scope.seed, scope.world) and not replace_live:
        sys.stderr.write(
            f"REFUSING to run the pipeline for seed {scope.seed!r} / world "
            f"{scope.world!r}: that is the live default pair, and stages 5, 8 "
            f"and 11 write geography.yaml, sites.yaml, segments.yaml and "
            f"crossings.yaml -- the corpus the gate reads -- IN PLACE. Use "
            f"`status` to read it, a different --seed/--world to build "
            f"another, or --replace-live if replacing the live corpus is "
            f"the intention.\n")
        return 5
    # THE READERS, ASKED FOR THIS SCOPE, BEFORE ANY STAGE RUNS.  A table
    # whose `requires` is a subset of what its tools open runs the first
    # stage whose missing input it did not declare and dies inside it.
    read_defects = audit_reads(stages, scope) + audit_actors(stages, scope)
    if read_defects:
        sys.stderr.write("REFUSING to run: a stage's reader opens an artefact "
                         "the table does not order before it, or a stage "
                         "records its writes under an actor that is not "
                         "$VH_ACTOR:\n  "
                         + "\n  ".join(read_defects) + "\n")
        return 3
    scope.work.mkdir(parents=True, exist_ok=True)
    journal = scope.work / "pipeline.json"
    log: list[dict] = []
    seedfile = scope.work / f"seed-{scope.seed_hash}.txt"
    seedfile.write_text(scope.seed + "\n")

    root = Path(producer_root or PRODUCER_ROOT).resolve()
    bound = 1 if force else from_
    if bound is not None and _FROM_MODE == "ignored":
        # THE NEUTER: the world before `--from`, where the only way to re-run
        # stage K was `--force`, and `--force` re-runs stage 1.
        bound = 1
    owners = owner_of(stages, scope)
    ran: list[str] = []

    for st in stages:
        if st.ordinal > through:
            break
        rows = {r["name"]: r for r in status(scope, stages)}
        row = rows[st.name]
        named = bound is not None and st.ordinal >= bound
        if bound is not None and st.ordinal < bound:
            entry = {"stage": st.ordinal, "name": st.name, "rc": None,
                     "not_run": f"below --from {bound}"}
            if not row["satisfied"]:
                entry["left_standing"] = _not_current(row)
                sys.stderr.write(
                    f"  stage {st.ordinal} {st.name!r} is NOT CURRENT and "
                    f"--from {bound} leaves it standing (never run): "
                    f"{_not_current(row)[:300]}\n")
            log.append(entry)
            continue
        upstream = _reproduced_upstream(st, owners, ran)
        if not named and row["satisfied"] and not upstream:
            log.append({"stage": st.ordinal, "name": st.name, "rc": 0,
                        "wall_s": 0.0,
                        "skipped": "CURRENT: artefacts present and in scope, "
                                   "producer recorded and unchanged"})
            continue
        if named:
            why = "--force" if force else f"named by --from {bound}"
        else:
            why = "; ".join(
                ([] if row["satisfied"] else [_not_current(row)])
                + ([f"downstream of stage(s) re-run in this run: "
                    f"{', '.join(upstream)}"] if upstream else []))
        if st.writes_world and not named and _world_guard_refuses(row):
            redo = _redo_command(scope, st.ordinal, through)
            log.append({"stage": st.ordinal, "name": st.name, "rc": None,
                        "refused": f"WRITES THE LIVE WORLD ({st.writes_world}) "
                                   f"and is not current: {why}",
                        "redo": redo})
            journal.write_text(json.dumps(log, indent=1))
            keep = (f"To keep what stage {st.ordinal} made and redo only the "
                    f"stages after it, use --from {st.ordinal + 1}.\n"
                    if st.ordinal < through else "")
            sys.stderr.write(
                f"\nREFUSING at stage {st.ordinal} {st.name!r}: it WRITES THE "
                f"LIVE WORLD ({st.writes_world}) and would re-run because "
                f"{why}.\nA file edit does not regenerate a world; an "
                f"operator does. Nothing at or after stage {st.ordinal} ran. "
                f"To redo it and every later stage, from the repo root:\n"
                f"  {redo}\n{keep}The journal is {journal}.\n")
            return 6
        unmet = first_unmet(scope, stages)
        if unmet and unmet[0].ordinal <= st.ordinal:
            blocker, label, reason = unmet
            log.append({"stage": st.ordinal, "name": st.name, "rc": None,
                        "refused": f"stage {blocker.ordinal} {blocker.name!r} "
                                   f"wants {label!r}: {reason}"})
            journal.write_text(json.dumps(log, indent=1))
            verb = ("cannot produce" if blocker.command is None
                    and label in {a.label for a in blocker.produces(scope)}
                    else "requires")
            sys.stderr.write(
                f"\nREFUSING at stage {blocker.ordinal} {blocker.name!r}: it "
                f"{verb} {label!r} and {reason}\n"
                f"Nothing later ran. The journal is {journal}.\n")
            return 3
        if st.command is None:
            log.append({"stage": st.ordinal, "name": st.name, "rc": None,
                        "refused": f"NO COMMAND: {st.manual}"})
            journal.write_text(json.dumps(log, indent=1))
            sys.stderr.write(
                f"\nSTOPPING at stage {st.ordinal} {st.name!r}: no tool in this "
                f"tree runs it unattended.\n  {st.manual}\n")
            return 4
        rc = _execute(scope, st, stages, st.command(scope), why, root, log,
                      journal)
        if rc != 0:
            return rc
        ran.append(st.name)
        if st.refines is not None:
            rc = _refine(scope, st, stages, root, log, journal, ran, bound)
            if rc != 0:
                return rc
    return 0


def _execute(scope: Scope, st: Stage, stages: tuple[Stage, ...],
             cmd: list[str], why: str, root: Path, log: list[dict],
             journal: Path) -> int:
    """Run ONE stage command traced, journal it, and record its producer on
    success.  The record is dropped first: a stage that fails halfway has no
    producer, not the last good one's."""
    _store_record(scope, st.name, None)
    t0 = time.time()
    sys.stderr.write(f"\n[{st.ordinal}/{len(stages)}] {st.name}: "
                     f"{' '.join(cmd)}\n      because: {why[:400]}\n")
    tdir, tenv = _trace_begin(root)
    saved = {k: os.environ.get(k) for k in tenv}
    os.environ.update(tenv)
    try:
        rc = subprocess.call(cmd, cwd=str(HERE), env=scope.env())
        wall = time.time() - t0
        rows = {r["name"]: r for r in status(scope, stages)}
        produced = [{"label": a["label"], "state": a["state"]}
                    for a in rows[st.name]["artefacts"]]
        log.append({"stage": st.ordinal, "name": st.name, "rc": rc,
                    "wall_s": round(wall, 1), "because": why,
                    "produced": produced})
        journal.write_text(json.dumps(log, indent=1))
        sys.stderr.write(f"[{st.ordinal}/{len(stages)}] {st.name}: rc={rc} "
                         f"wall={wall:.1f}s\n")
        if rc != 0:
            return rc
        if st.post is not None:
            st.post(scope)
            rows = {r["name"]: r for r in status(scope, stages)}
            log[-1]["produced"] = [{"label": a["label"], "state": a["state"]}
                                   for a in rows[st.name]["artefacts"]]
        trace = _trace_read(tdir)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        import shutil                                   # noqa: PLC0415
        shutil.rmtree(tdir, ignore_errors=True)
    rec = _record_success(scope, st, stages, cmd, trace, root)
    log[-1]["producer"] = {"files": len(rec["files"]),
                           "processes": rec["processes"],
                           "incomplete": rec["incomplete"]}
    journal.write_text(json.dumps(log, indent=1))
    return 0


# CENSUS_LOOP_DEPENDS_ON: THE CENSUS AND THE ROUTE ARE ONE FIXED POINT, NOT
# TWO STAGES IN A ROW.
#
# Stage 10 reads a band about the lines stage 9 found; stage 11 routes
# against what it read and may stand a line -- or want to -- on ground stage
# 10 never read (network.CENSUS_BAND_DEPENDS_ON; MEASURED on Fresh42 drive 3:
# 20 unread cells within 16 m of five written lines, 184 more under four
# legs the band refused).  Until this existed the only remedy was an
# operator re-running the census by hand, with a band width the router
# printed for a body on another leg.  Now `run`, after a stage that declares
# `refines`, asks that stage's own artefact how much it still wants
# (`the_route_plans_census_wanted`), re-runs the EARLIER stage's own producer
# with exactly that (`the_census_stages_own_producer` -- its record is
# rewritten, so pipeline currency sees one census, the one on disk), then
# the refining stage, and stops when nothing is wanted or after
# `a_bound_on_rounds`, naming what is still wanted.  A world-writing earlier
# stage is re-run only when this invocation may run it at all (named by
# `--from`/`--force`, or created in this run) -- the WORLD_GUARD rule.
CENSUS_LOOP_DEPENDS_ON = ("the_route_plans_census_wanted",
                          "the_census_stages_own_producer",
                          "a_bound_on_rounds")
CENSUS_ROUNDS_MAX = 3
# real | off (the shipped single pass) | blind | cells-only (the round-2
# drive-4 rule: a fixed point is "0 cells wanted", servable legs unasked)
_CENSUS_LOOP_MODE = "real"


def _refine(scope: Scope, st: Stage, stages: tuple[Stage, ...], root: Path,
            log: list[dict], journal: Path, ran: list[str],
            bound: int | None) -> int:
    """The fixed point `st.refines` declares (CENSUS_LOOP_DEPENDS_ON)."""
    if _CENSUS_LOOP_MODE == "off":
        return 0
    name, wants, extra, unserved = st.refines
    early = next(s for s in stages if s.name == name)
    for rnd in range(1, CENSUS_ROUNDS_MAX + 1):
        n = wants(scope)
        if n == 0 and _CENSUS_LOOP_MODE != "blind":
            # NOTHING LEFT TO READ IS NOT A FIXED POINT while a leg the
            # route could serve over read ground is unrouted: that is the
            # route's own contradiction, and it is refused with its owner.
            left = [] if _CENSUS_LOOP_MODE == "cells-only" else unserved(scope)
            if left:
                log.append({"stage": st.ordinal, "name": st.name, "rc": 9,
                            "refused": f"NOT A FIXED POINT: {st.name} wants "
                                       f"nothing of {name} and leaves "
                                       f"{len(left)} servable leg(s) "
                                       f"unrouted: {left}"})
                journal.write_text(json.dumps(log, indent=1))
                sys.stderr.write(
                    f"\nREFUSING at stage {st.ordinal} {st.name!r}: it wants "
                    f"nothing more of {name!r} and still leaves servable "
                    f"leg(s) unrouted: {left}. Owner: "
                    f"network.CENSUS_ADOPT_DEPENDS_ON.\n")
                return 9
            log.append({"stage": st.ordinal, "name": st.name,
                        "fixed_point": f"converged after {rnd - 1} round(s): "
                                       f"{st.name} wants nothing more of "
                                       f"{name}"})
            journal.write_text(json.dumps(log, indent=1))
            return 0
        may = (name in ran or (bound is not None and early.ordinal >= bound)
               or not early.writes_world)
        if not may:
            redo = _redo_command(scope, early.ordinal, st.ordinal)
            log.append({"stage": st.ordinal, "name": st.name, "rc": 0,
                        "fixed_point": f"{st.name} wants {n} unit(s) of "
                                       f"{name} and this run may not re-run "
                                       f"it (it WRITES THE LIVE WORLD)",
                        "redo": redo})
            journal.write_text(json.dumps(log, indent=1))
            sys.stderr.write(
                f"\n{st.name!r} WANTS {n} unit(s) its {name!r} stage has not "
                f"read, and {name!r} writes the live world, so this run does "
                f"not re-run it. The plan stands with those legs refused and "
                f"the cells named. To read them and re-route:\n  {redo}\n")
            return 0
        why = (f"fixed point round {rnd}/{CENSUS_ROUNDS_MAX}: {st.name!r} "
               f"wants {n} unit(s) {name!r} has not read "
               f"(CENSUS_LOOP_DEPENDS_ON)")
        rc = _execute(scope, early, stages, early.command(scope)
                      + extra(scope), why, root, log, journal)
        if rc != 0:
            return rc
        if name not in ran:
            ran.append(name)
        rc = _execute(scope, st, stages, st.command(scope),
                      f"fixed point round {rnd}: re-route on what {name!r} "
                      f"has now read", root, log, journal)
        if rc != 0:
            return rc
    n = wants(scope)
    if n:
        log.append({"stage": st.ordinal, "name": st.name, "rc": 0,
                    "fixed_point": f"NOT CONVERGED after {CENSUS_ROUNDS_MAX} "
                                   f"round(s): {st.name} still wants {n} "
                                   f"unit(s) of {name}; its artefact names "
                                   f"them, per leg"})
        journal.write_text(json.dumps(log, indent=1))
        sys.stderr.write(f"\n{st.name!r} still wants {n} unit(s) of {name!r} "
                         f"after {CENSUS_ROUNDS_MAX} round(s) "
                         f"(CENSUS_ROUNDS_MAX); the plan names them.\n")
    return 0


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------
# ONE ROW PER (DEPENDENCY, DIRECTION).  `off` is the arm that makes the check
# stop looking -- a check that looks at nothing ACCEPTS everything, so only a
# must-REFUSE case can flip under it.  The second arm makes the check look
# WRONGLY -- reversed, blind, absent -- which is what a must-ACCEPT case flips
# under.  Neither is invented: each is a real way this exact code fails.
_NEUTERS: dict[str, tuple[str, str, object]] = {
    "ordinal off": ("ordinal", "_ORDINAL_MODE", "off"),
    "ordinal reversed": ("ordinal", "_ORDINAL_MODE", "reversed"),
    "produced-by map off": ("owner_map", "_OWNER_MODE", "off"),
    "self-declaration off": ("scope_header", "_SCOPE_MODE", "off"),
    "self-declaration blind": ("scope_header", "_SCOPE_MODE", "blind"),
    "presence always-present": ("presence", "_PRESENCE_MODE", "present"),
    "presence always-absent": ("presence", "_PRESENCE_MODE", "absent"),
    "freshness off": ("freshness", "_FRESHNESS_MODE", "off"),
    "freshness always-stale": ("freshness", "_FRESHNESS_MODE", "always"),
    # IN_PLACE_DEPENDS_ON.
    "lineage keyed on mtime": ("lineage", "_LINEAGE_MODE", "mtime"),
    "lineage off": ("lineage", "_LINEAGE_MODE", "off"),
    "reads off": ("reads", "_READS_MODE", "off"),
    "reads foreign": ("reads", "_READS_MODE", "foreign"),
    "actor off": ("actor", "_ACTOR_MODE", "off"),
    "actor constant": ("actor", "_ACTOR_MODE", "constant"),
    # STAGE_CURRENCY_DEPENDS_ON.  `artefacts_present` is switched by the
    # presence rows above -- one quantity, one switch.
    "producer off": ("producer_fingerprint", "_PRODUCER_MODE", "off"),
    "producer blind": ("producer_fingerprint", "_PRODUCER_MODE", "blind"),
    "producer tool-file-only": ("producer_fingerprint", "_PRODUCER_MODE",
                                "tool-only"),
    # RUN_DEPENDS_ON.
    "descendants off": ("descendants", "_DESCENDANT_MODE", "off"),
    "descendants by ordinal": ("descendants", "_DESCENDANT_MODE", "ordinal"),
    "from ignored": ("from_bound", "_FROM_MODE", "ignored"),
    "world guard off": ("world_guard", "_WORLD_GUARD_MODE", "off"),
    "world guard always": ("world_guard", "_WORLD_GUARD_MODE", "always"),
    # CENSUS_LOOP_DEPENDS_ON.
    "census loop off": ("census_loop", "_CENSUS_LOOP_MODE", "off"),
    "census loop blind": ("census_loop", "_CENSUS_LOOP_MODE", "blind"),
    "census loop counts cells only": ("census_loop", "_CENSUS_LOOP_MODE",
                                      "cells-only"),
    # ADOPT_RESTAMP_DEPENDS_ON.
    "adopt skips identical": ("adopt_restamp", "_ADOPT_MODE", "skip"),
    # FIELD_WINDOWS_DEPENDS_ON.
    "field windows derived": ("field_windows", "_FIELD_WINDOWS_MODE", "derived"),
}


def _neuter(label: str) -> Callable[[], Callable[[], None]]:
    """Break exactly ONE declared dependency, in exactly ONE direction."""
    _, key, value = _NEUTERS[label]

    def apply() -> Callable[[], None]:
        g = globals()
        prior = g[key]
        g[key] = value

        def undo() -> None:
            g[key] = prior
        return undo
    return apply


def survivors(label: str) -> list[str]:
    """Which OTHER declared dependencies still refuse with `label` broken.

    Expressed as a list this function EARNED by re-running each check under the
    neuter, never as an assertion: `run_cases` reads an AssertionError as a
    flip, so an assert-shaped survivor lets a neuter that named nothing be
    stamped PROVEN.  A neuter with NO survivors is refused by `selftest` --
    that is the degenerate case, and it is the one that stamps a blind
    instrument green.
    """
    dropped = _NEUTERS.get(label, ("", "", ""))[0]
    probes = (("ordinal", _inverted_table_refused),
              ("owner_map", _unowned_table_refused),
              ("scope_header", _foreign_header_refused),
              ("presence", _missing_artefact_refused),
              ("freshness", _stale_artefact_refused),
              ("lineage", _replaced_world_refused),
              ("reads", _prefix_table_read_refused),
              ("actor", _constant_actor_refused),
              ("producer_fingerprint", _changed_producer_rerun),
              ("descendants", _downstream_of_rerun_rerun),
              ("world_guard", _stale_world_stage_refused),
              ("census_loop", _wanted_ground_read_and_rerouted),
              ("adopt_restamp", _identical_adoption_restamped),
              ("field_windows", _default_seed_scans_declared))
    return [n for n, probe in probes if n != dropped and probe()]


def _prefix_table() -> tuple[Stage, ...]:
    """THE KNOWN-BAD TABLE, rebuilt FROM THE REAL ROWS: the order and the
    `requires` this file carried until 2026-09-24 -- sites 6 (field,
    locations), world_facts 7 (sites), placements 8 (grid, locations, sites,
    placement_templates), and neither route stage requiring placements.  The
    `reads` callables are the real readers', so this is the state the
    Fresh42 from-scratch drive refused in, not a drawing of it."""
    import dataclasses                                      # noqa: PLC0415
    old = {"sites": (6, ("field", "locations")),
           "world_facts": (7, ("sites",)),
           "placements": (8, ("grid", "locations", "sites",
                              "placement_templates")),
           "route_provisional": (9, ("field", "geography", "sites")),
           "route": (11, ("field", "geography", "sites", "census"))}
    rows = []
    for st in STAGES:
        if st.name in old:
            o, req = old[st.name]
            st = dataclasses.replace(st, ordinal=o, requires=req)
        rows.append(st)
    return tuple(sorted(rows, key=lambda st: st.ordinal))


def _prefix_table_read_refused() -> bool:
    """The pre-fix table must be refused by the READ audit.  (`audit_graph`
    passed it -- the whole finding.)"""
    import tempfile                                         # noqa: PLC0415
    with tempfile.TemporaryDirectory() as td:
        return bool(audit_reads(_prefix_table(), _selftest_scope(Path(td))))


def _foreign_site_reader_refused() -> bool:
    """The real table with the site reader answering for ANOTHER world --
    `Picker(world_dir=worlds/Ulfsland)` for every seed -- must be refused."""
    import dataclasses, tempfile                            # noqa: PLC0415, E401
    ulf = _FOREIGN_WORLD
    rows = tuple(
        dataclasses.replace(st, reads=lambda s: [
            worldseed.installations_read(ulf)])
        if st.name == "sites" else st for st in STAGES)
    with tempfile.TemporaryDirectory() as td:
        return bool(audit_reads(rows, _selftest_scope(Path(td))))


def _real_readers_accepted() -> bool:
    import tempfile                                         # noqa: PLC0415
    with tempfile.TemporaryDirectory() as td:
        return not audit_reads(STAGES, _selftest_scope(Path(td)))


def _constant_actor_table() -> tuple[Stage, ...]:
    """THE KNOWN-BAD ROW, rebuilt from the real one: stage 10's argv with the
    `--actor` value it carried until 2026-09-24, the literal `pipeline`."""
    import dataclasses                                      # noqa: PLC0415

    def old(st: Stage) -> Stage:
        real = st.command

        def cmd(s: Scope) -> list[str]:
            argv = list(real(s))
            i = argv.index("--actor")
            argv[i + 1] = _OLD_ACTOR_LITERAL
            return argv
        return dataclasses.replace(st, command=cmd)
    return tuple(old(st) if st.name == "census" else st for st in STAGES)


def _constant_actor_refused() -> bool:
    import tempfile                                         # noqa: PLC0415
    with tempfile.TemporaryDirectory() as td:
        return bool(audit_actors(_constant_actor_table(),
                                 _selftest_scope(Path(td))))


def _real_actors_accepted() -> bool:
    import tempfile                                         # noqa: PLC0415
    with tempfile.TemporaryDirectory() as td:
        return not audit_actors(STAGES, _selftest_scope(Path(td)))


def _missing_artefact_refused() -> bool:
    """An artefact that is not on disk must not read as PRESENT."""
    import tempfile                                         # noqa: PLC0415
    with tempfile.TemporaryDirectory() as td:
        a = Artefact("absent", Path(td) / "nothing-here")
        state, _ = artefact_state(a, _selftest_scope(Path(td)))
        return state == MISSING


def _stale_fixture(tmp: Path, derived_first: bool) -> tuple[Stage, ...]:
    """Two stages: an input, and a thing derived from it.  CONSTRUCTED.

    `derived_first=True` writes the derived artefact BEFORE its input, which is
    the Fresh42 state exactly: a `geography.<seed>.yaml` on disk, declaring the
    right seed, derived from a field that has since been re-scanned.
    """
    src, out = tmp / "input.bin", tmp / "derived.yaml"
    if derived_first:
        out.write_text("seed: Fresh42\nworld: Fresh42\n")
        time.sleep(0.02)
        src.write_text("x")
    else:
        src.write_text("x")
        time.sleep(0.02)
        out.write_text("seed: Fresh42\nworld: Fresh42\n")
    return (Stage(1, "input", "x", lambda s: [Artefact("input", src)],
                  command=lambda s: ["true"]),
            Stage(2, "derived", "x",
                  lambda s: [Artefact("derived", out, SCOPE_YAML)],
                  requires=("input",), command=lambda s: ["true"]))


def _stale_artefact_refused() -> bool:
    """An artefact older than the input it derives from must not read PRESENT."""
    import tempfile                                         # noqa: PLC0415
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        rows = status(_selftest_scope(tmp), _stale_fixture(tmp, True))
        return rows[1]["artefacts"][0]["state"] == STALE


def _fresh_artefact_accepted() -> bool:
    import tempfile                                         # noqa: PLC0415
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        rows = status(_selftest_scope(tmp), _stale_fixture(tmp, False))
        return rows[1]["artefacts"][0]["state"] == PRESENT


def _selftest_scope(tmp: Path) -> Scope:
    return Scope("Fresh42", "Fresh42", tmp, tmp / "root", tmp / "vh")


def _inverted_table_refused() -> bool:
    """A table whose stage 2 needs stage 3's artefact must be refused."""
    a = Stage(1, "one", "x", lambda s: [Artefact("a", Path("/x/a"))])
    b = Stage(2, "two", "x", lambda s: [Artefact("b", Path("/x/b"))],
              requires=("c",))
    c = Stage(3, "three", "x", lambda s: [Artefact("c", Path("/x/c"))])
    return bool(audit_graph((a, b, c)))


def _unowned_table_refused() -> bool:
    """A table requiring an artefact nothing produces must be refused."""
    a = Stage(1, "one", "x", lambda s: [Artefact("a", Path("/x/a"))])
    b = Stage(2, "two", "x", lambda s: [Artefact("b", Path("/x/b"))],
              requires=("nobody_makes_this",))
    return bool(audit_graph((a, b)))


def _foreign_header_refused() -> bool:
    """A Fresh42 run handed an Ulfsland-declared corpus must be refused.

    CONSTRUCTED, never borrowed: the fixture writes its own two-line document
    so it cannot stop describing anything when a real corpus moves.
    """
    import tempfile                                         # noqa: PLC0415
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "sites.yaml"
        p.write_text("world: Ulfsland\nseed: Pirate68\nsites: []\n")
        scope = _selftest_scope(Path(td))
        state, _ = artefact_state(Artefact("sites", p, SCOPE_YAML), scope)
        return state == OUT_OF_SCOPE


def _own_header_accepted() -> bool:
    import tempfile                                         # noqa: PLC0415
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "sites.yaml"
        p.write_text("world: Fresh42\nseed: Fresh42\nsites: []\n")
        scope = _selftest_scope(Path(td))
        state, _ = artefact_state(Artefact("sites", p, SCOPE_YAML), scope)
        return state == PRESENT


def _real_table_accepted() -> bool:
    return not audit_graph(STAGES)


# THE SHAPE OF THE REAL TABLE, CONSTRUCTED.  A fixture must CONSTRUCT the state
# it tests and never BORROW it: pointing these two cases at STAGES would make
# them read this checkout's `roads/geography.Fresh42.yaml` and
# `settlements/sites.Fresh42.yaml`, and the day either file appears or moves the
# case stops describing anything without going red.  So the three-row table
# below carries the one relation that matters -- a terminal stage requiring an
# artefact whose producer HAS NO COMMAND -- and nothing else.
def _mirror_table() -> tuple[Stage, ...]:
    def save(s: Scope) -> list[Artefact]:
        return [Artefact("save", s.work / "save")]

    def ledger(s: Scope) -> list[Artefact]:
        return [Artefact("ledger", s.work / "ledger.jsonl")]

    def verdict(s: Scope) -> list[Artefact]:
        return [Artefact("verdict", s.work / "gate.json")]

    return (
        Stage(1, "save", "worldgen", save, command=lambda s: ["true"]),
        Stage(2, "build", "the build", ledger, requires=("save",), command=None,
              # The fixture's SHAPE is a producer with no command, which is
              # what `first_unmet` must name. It is no longer a statement
              # about stage 12: that row runs `buildstage.py`.
              manual="a producer no tool runs unattended"),
        Stage(3, "gate", "accept", verdict, requires=("save", "ledger"),
              command=lambda s: ["true"]),
    )


def _unbuilt_world_refused() -> bool:
    """THE KNOWN-BAD STATE, IN ITS SHAPE.

    MEASURED 2026-09-23: Fresh42's save existed, its 34,426,919-byte grid and
    its 12,555-entry location dump existed, and `accept --world Fresh42 --seed
    Fresh42 --world-src <freeze>` exited 2 at accept/__main__.py:490 with
    `HAS NO BUILD LEDGER`.  The world HAD a save and did NOT have a ledger, and
    nothing in the tree could say which stage owed it.  `first_unmet` must name
    that stage BEFORE the gate is started: a gate that has to be run to discover
    its own missing prerequisite is the ordering being nowhere in source.
    """
    import tempfile                                         # noqa: PLC0415
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        scope = _selftest_scope(tmp)
        (tmp / "save").mkdir(parents=True)
        unmet = first_unmet(scope, _mirror_table())
        return unmet is not None and unmet[0].name == "build"


def _built_world_accepted() -> bool:
    """The same table with the ledger present must have NO unmet prerequisite.

    The accepting arm exists because a check that refuses everything is as
    useless as one that refuses nothing, and because `first_unmet` returning a
    stage unconditionally would satisfy the refusing arm on its own.
    """
    import tempfile                                         # noqa: PLC0415
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        scope = _selftest_scope(tmp)
        (tmp / "save").mkdir(parents=True)
        (tmp / "ledger.jsonl").write_text("{}\n")
        (tmp / "gate.json").write_text("{}")
        return first_unmet(scope, _mirror_table()) is None


# ---------------------------------------------------------------------------
# PRODUCER-CURRENCY FIXTURES.  Every one is a REAL `run()` over REAL tools in
# a temp dir: shell scripts that source a helper only their TEXT names, and
# one Python tool whose helper only the TRACE sees.  Each tool appends one
# line to `ran<i>` per execution, so "was it re-run" is a count, not a
# belief.  The seed/world is not the live default pair, so `run` does not
# refuse it as live.
# ---------------------------------------------------------------------------
_FX = "CurrencyFixture"


def _fx(tmp: Path, n: int, *, world: tuple[int, ...] = (),
        requires: dict[int, tuple[int, ...]] | None = None,
        noop: tuple[int, ...] = ()) -> tuple[Scope, tuple[Stage, ...]]:
    """`n` stages over `tmp/src/tool<i>.sh` + `lib<i>.sh`.  A `noop` stage
    leaves an existing artefact untouched, so its re-run moves no mtime and
    only the descendant rule can see that it ran."""
    src, work = tmp / "src", tmp / "work"
    src.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    rows = []
    for i in range(1, n + 1):
        tool = src / f"tool{i}.sh"
        if not tool.exists():
            (src / f"lib{i}.sh").write_text(f"OUT=out{i}\n")
            keep = '[ -e "$W/$OUT" ] || ' if i in noop else ""
            tool.write_text('HERE="$(dirname "$0")"\n'
                            f'. "$HERE/lib{i}.sh"\n'
                            'W="$1"\n'
                            f'echo x >> "$W/ran{i}"\n'
                            f'{keep}echo made > "$W/$OUT"\n')
        rows.append(Stage(
            i, f"s{i}", f"tool{i}.sh",
            (lambda s, i=i: [Artefact(f"a{i}", s.work / f"out{i}")]),
            requires=tuple(f"a{j}" for j in (requires or {}).get(i, ())),
            command=(lambda s, t=tool: ["sh", str(t), str(s.work)]),
            writes_world="the fixture's world" if i in world else ""))
    return Scope(_FX, _FX, work, tmp / "root", tmp / "vh"), tuple(rows)


def _fx_run(scope: Scope, stages: tuple[Stage, ...], **kw) -> int:
    import contextlib, io                                   # noqa: PLC0415, E401
    with contextlib.redirect_stderr(io.StringIO()):
        return run(scope, kw.pop("through", len(stages)), stages,
                   producer_root=scope.work.parent / "src", **kw)


def _ran(scope: Scope, i: int) -> int:
    p = scope.work / f"ran{i}"
    return len(p.read_text().splitlines()) if p.exists() else 0


def _touch_source(p: Path) -> None:
    """THE FIX: an edit to a producer file."""
    with p.open("a") as fh:
        fh.write("# the fix\n")


def _row(scope: Scope, stages: tuple[Stage, ...], name: str) -> dict:
    return {r["name"]: r for r in status(scope, stages)}[name]


def _changed_producer_rerun() -> bool:
    """THE KNOWN-BAD STATE: a stage whose tool was fixed after it ran.  It
    must read STALE, name the file, and be the one stage `run` re-runs."""
    import tempfile                                         # noqa: PLC0415
    with tempfile.TemporaryDirectory() as td:
        scope, st = _fx(Path(td), 2, requires={2: (1,)})
        _fx_run(scope, st)
        _touch_source(Path(td) / "src" / "tool2.sh")
        row = _row(scope, st, "s2")
        named = row["producer"]["state"] == STALE and any(
            c.startswith(_rel((Path(td) / "src" / "tool2.sh").resolve()))
            for c in row["producer"]["changed"])
        rc = _fx_run(scope, st)
        return (named and not row["satisfied"] and rc == 0
                and _ran(scope, 1) == 1 and _ran(scope, 2) == 2)


def _unknown_producer_rerun() -> bool:
    """Artefacts made before producer recording: UNKNOWN, not satisfied, run."""
    import tempfile                                         # noqa: PLC0415
    with tempfile.TemporaryDirectory() as td:
        scope, st = _fx(Path(td), 1)
        (scope.work / "out1").write_text("made before producer recording\n")
        row = _row(scope, st, "s1")
        rc = _fx_run(scope, st)
        return (row["producer"]["state"] == UNKNOWN_PRODUCER
                and not row["satisfied"] and rc == 0 and _ran(scope, 1) == 1)


def _reached_file_in_population() -> bool:
    """A Python tool's imported helper (seen only by the trace) and a shell
    tool's sourced helper (seen only in its text): editing either must stale
    its stage.  A population of the argv's tool files misses both."""
    import dataclasses, tempfile                            # noqa: PLC0415, E401
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        scope, st = _fx(tmp, 2)
        src = tmp / "src"
        (src / "helper.py").write_text(
            "def make(w):\n"
            "    open(w + '/out1', 'w').write('made')\n"
            "    open(w + '/ran1', 'a').write('x\\n')\n")
        (src / "tool.py").write_text("import sys\nimport helper\n"
                                     "helper.make(sys.argv[1])\n")
        py = dataclasses.replace(st[0], command=lambda s: [
            sys.executable, str(src / "tool.py"), str(s.work)])
        st = (py, st[1])
        _fx_run(scope, st)
        _touch_source(src / "helper.py")
        _touch_source(src / "lib2.sh")
        r1, r2 = _row(scope, st, "s1"), _row(scope, st, "s2")
        rc = _fx_run(scope, st)
        return (r1["producer"]["state"] == STALE
                and any("helper.py" in c for c in r1["producer"]["changed"])
                and r2["producer"]["state"] == STALE
                and any("lib2.sh" in c for c in r2["producer"]["changed"])
                and rc == 0 and _ran(scope, 1) == 2 and _ran(scope, 2) == 2)


def _recorded_but_gone_rerun() -> bool:
    """A current record does not make a deleted artefact present."""
    import tempfile                                         # noqa: PLC0415
    with tempfile.TemporaryDirectory() as td:
        scope, st = _fx(Path(td), 1)
        _fx_run(scope, st)
        (scope.work / "out1").unlink()
        rc = _fx_run(scope, st)
        return rc == 0 and _ran(scope, 1) == 2


def _unchanged_producer_kept() -> bool:
    """THE SURVIVOR: nothing changed, so a second run runs NOTHING and every
    stage reads satisfied with its producer CURRENT."""
    import tempfile                                         # noqa: PLC0415
    with tempfile.TemporaryDirectory() as td:
        scope, st = _fx(Path(td), 2, requires={2: (1,)})
        _fx_run(scope, st)
        rc = _fx_run(scope, st)
        rows = status(scope, st)
        return (rc == 0 and _ran(scope, 1) == 1 and _ran(scope, 2) == 1
                and all(r["satisfied"] and r["producer"]["state"] == CURRENT
                        for r in rows))


def _downstream_of_rerun_rerun() -> bool:
    """Stage 1 re-runs and rewrites nothing (so no mtime moves); stage 2,
    which requires it, re-runs; stage 3, which does not, does not."""
    import tempfile                                         # noqa: PLC0415
    with tempfile.TemporaryDirectory() as td:
        scope, st = _fx(Path(td), 3, requires={2: (1,)}, noop=(1,))
        _fx_run(scope, st)
        _touch_source(Path(td) / "src" / "tool1.sh")
        rc = _fx_run(scope, st)
        return (rc == 0 and _ran(scope, 1) == 2 and _ran(scope, 2) == 2
                and _ran(scope, 3) == 1)


def _independent_stage_left() -> bool:
    """A stage NOT downstream of the re-run one is not re-run."""
    import tempfile                                         # noqa: PLC0415
    with tempfile.TemporaryDirectory() as td:
        scope, st = _fx(Path(td), 2)
        _fx_run(scope, st)
        _touch_source(Path(td) / "src" / "tool1.sh")
        rc = _fx_run(scope, st)
        return rc == 0 and _ran(scope, 1) == 2 and _ran(scope, 2) == 1


def _from_bound_held() -> bool:
    """`--from 3` with stage 1 STALE: stages 1 and 2 never run, 3 and 4 do."""
    import tempfile                                         # noqa: PLC0415
    with tempfile.TemporaryDirectory() as td:
        scope, st = _fx(Path(td), 4, requires={2: (1,), 3: (2,), 4: (3,)})
        _fx_run(scope, st)
        _touch_source(Path(td) / "src" / "tool1.sh")
        rc = _fx_run(scope, st, from_=3)
        return (rc == 0 and _ran(scope, 1) == 1 and _ran(scope, 2) == 1
                and _ran(scope, 3) == 2 and _ran(scope, 4) == 2)


def _stale_world_stage_refused() -> bool:
    """A world-writing stage whose tool changed: a plain run REFUSES it (exit
    6) and runs neither it nor anything after it."""
    import tempfile                                         # noqa: PLC0415
    with tempfile.TemporaryDirectory() as td:
        scope, st = _fx(Path(td), 2, world=(1,), requires={2: (1,)})
        _fx_run(scope, st)
        _touch_source(Path(td) / "src" / "tool1.sh")
        rc = _fx_run(scope, st)
        return rc == 6 and _ran(scope, 1) == 1 and _ran(scope, 2) == 1


def _world_stage_created() -> bool:
    """A world-writing stage that has never produced RUNS: a fresh world."""
    import tempfile                                         # noqa: PLC0415
    with tempfile.TemporaryDirectory() as td:
        scope, st = _fx(Path(td), 2, world=(1,), requires={2: (1,)})
        rc = _fx_run(scope, st)
        return rc == 0 and _ran(scope, 1) == 1 and _ran(scope, 2) == 1


# IN_PLACE_DEPENDS_ON FIXTURES.  Stage 1 makes a "world" (`out1`, holding
# `<uid> <generation>`); stage 2 WRITES IT IN PLACE and derives `out2` from it
# -- the save and the census, CONSTRUCTED, run through the real `run()`.
def _lineage_fx(tmp: Path) -> tuple[Scope, tuple[Stage, ...], Path]:
    import dataclasses                                      # noqa: PLC0415
    scope, rows = _fx(tmp, 2, world=(2,), requires={2: (1,)})
    (tmp / "src" / "tool1.sh").write_text(
        'W="$1"\necho x >> "$W/ran1"\necho "made 5" > "$W/out1"\n')

    def lineage(p: Path) -> tuple[str, int] | None:
        if not p.exists():
            return None
        uid, gen = p.read_text().split()
        return uid, int(gen)
    first = dataclasses.replace(
        rows[0], produces=lambda s: [Artefact("a1", s.work / "out1",
                                              lineage=lineage)])
    return scope, (first, rows[1]), scope.work / "out1"


def _save_again(world: Path, text: str, derived: Path) -> None:
    """The world written after the stage that derived from it: a LATER mtime
    whatever it now holds."""
    world.write_text(text + "\n")
    base = derived.stat().st_mtime if derived.exists() else time.time()
    os.utime(world, (base + 10.0, base + 10.0))


def _resaved_world_accepted() -> bool:
    """THE KNOWN-BAD STATE: the same world, saved again after the stage that
    wrote into it (an autosave, or the stage's own writes landing) -- must
    NOT make that stage's output STALE."""
    import tempfile                                         # noqa: PLC0415
    with tempfile.TemporaryDirectory() as td:
        scope, st, world = _lineage_fx(Path(td))
        rc = _fx_run(scope, st)
        _save_again(world, "made 9", scope.work / "out2")
        return (rc == 0 and _row(scope, st, "s2")["artefacts"][0]["state"]
                == PRESENT)


def _replaced_world_refused() -> bool:
    """A re-created world (another uid) and an older copy put back (a lower
    generation) must each make the stage that wrote into it STALE."""
    import tempfile                                         # noqa: PLC0415
    with tempfile.TemporaryDirectory() as td:
        scope, st, world = _lineage_fx(Path(td))
        rc = _fx_run(scope, st)
        out = scope.work / "out2"
        seen = []
        for text in ("remade 5", "made 2"):
            _save_again(world, text, out)
            seen.append(_row(scope, st, "s2")["artefacts"][0]["state"])
        return rc == 0 and seen == [STALE, STALE]


def _default_seed_scans_declared() -> bool:
    """Stage 4 asks for the declared windows on the default seed and for
    derived windows on every other."""
    import dataclasses                                      # noqa: PLC0415
    stage = next(st for st in STAGES if st.name == "field")
    ulf = dataclasses.replace(_ROW_PROBE, seed=worldseed.DEFAULT_SEED)
    fresh = dataclasses.replace(_ROW_PROBE, seed="Fresh42")
    return ("--declared-windows" in [str(a) for a in stage.command(ulf)]
            and "--declared-windows" not in [str(a) for a in stage.command(fresh)])


def _identical_adoption_restamped() -> bool:
    """THE DRIVE-6 SHAPE: a stage-8 solve byte-identical to the slot it is
    adopted into, whose inputs are newer than that slot.  After adoption the
    slot must be newer than those inputs (re-validated), or stage 9 refuses
    it STALE.  Slots are redirected into a temp dir -- never this checkout."""
    import tempfile                                         # noqa: PLC0415
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        work, slots = root / "work", root / "slots"
        (work / "sites").mkdir(parents=True)
        slots.mkdir()
        keep = (worldseed.sites_path, worldseed.plan_path)
        worldseed.sites_path = lambda seed=None: slots / "sites.FX.yaml"
        worldseed.plan_path = lambda seed=None: slots / "plan.FX.json"
        try:
            for name in ("sites.FX.yaml", "plan.FX.json"):
                (slots / name).write_text("same")
                os.utime(slots / name, (1_000_000_000, 1_000_000_000))
            src_names = (worldseed.sites_name("FX"), worldseed.plan_name("FX"))
            for name in src_names:
                (work / "sites" / name).write_text("same")
            inputs = root / "field.bin"
            inputs.write_text("x")
            os.utime(inputs, (1_000_000_100, 1_000_000_100))
            scope = type("S", (), {"work": work, "seed": "FX"})()
            _adopt_solve(scope)
            return all((slots / n).stat().st_mtime > inputs.stat().st_mtime
                       for n in ("sites.FX.yaml", "plan.FX.json"))
        finally:
            worldseed.sites_path, worldseed.plan_path = keep


# CENSUS_LOOP_DEPENDS_ON FIXTURES.  Stage 1 is the "census" (world-writing):
# it reads cells `a b`, or appends exactly the cells `--wanted` names.  Stage
# 2 is the "route": it needs `target` and writes the cells it still wants --
# the drive-3 census and route, CONSTRUCTED, run through the real `run()`.
def _loop_fx(tmp: Path, target: str, unserved: str = ""
             ) -> tuple[Scope, tuple[Stage, ...]]:
    import dataclasses                                      # noqa: PLC0415
    scope, rows = _fx(tmp, 2, world=(1,), requires={2: (1,)})
    src = tmp / "src"
    (src / "tool1.sh").write_text(
        'W="$1"\necho x >> "$W/ran1"\n'
        'if [ "$2" = "--wanted" ]; then cat "$3" >> "$W/out1"; '
        'else printf "a\\nb\\n" > "$W/out1"; fi\n')
    (src / "tool2.sh").write_text(
        'W="$1"\necho x >> "$W/ran2"\n'
        f'printf "%s\\n" {target} | grep -vxF -f "$W/out1" > "$W/out2" '
        '|| true\n'
        f'printf "%s" "{unserved}" > "$W/unserved"\n')

    def wants(s: Scope) -> int:
        p = s.work / "out2"
        return len(p.read_text().split()) if p.exists() else 0

    def left(s: Scope) -> list[str]:
        p = s.work / "unserved"
        return p.read_text().split() if p.exists() else []
    route = dataclasses.replace(
        rows[1], refines=("s1", wants,
                          lambda s: ["--wanted", str(s.work / "out2")], left))
    return scope, (rows[0], route)


def _servable_unrouted_is_no_fixed_point() -> bool:
    """THE DRIVE-4 STATE: the route wants 0 cells and leaves a leg it could
    serve over read ground unrouted.  `run` must REFUSE, not converge."""
    import tempfile                                         # noqa: PLC0415
    with tempfile.TemporaryDirectory() as td:
        scope, st = _loop_fx(Path(td), "a b", unserved="S13-stathub-dock")
        rc = _fx_run(scope, st)
        return rc == 9 and _ran(scope, 1) == 1 and _ran(scope, 2) == 1


def _wanted_ground_read_and_rerouted() -> bool:
    """THE KNOWN-BAD STATE: a route that wants a cell its census never read.
    The census's own producer must read exactly it and the route must run
    again, leaving nothing wanted."""
    import tempfile                                         # noqa: PLC0415
    with tempfile.TemporaryDirectory() as td:
        scope, st = _loop_fx(Path(td), "a b c")
        rc = _fx_run(scope, st)
        return (rc == 0 and _ran(scope, 1) == 2 and _ran(scope, 2) == 2
                and st[1].refines[1](scope) == 0
                and "c" in (scope.work / "out1").read_text().split()
                and _row(scope, st, "s1")["producer"]["state"] == CURRENT)


def _nothing_wanted_nothing_reread() -> bool:
    """A route whose ground was all read: the census runs once, never
    again."""
    import tempfile                                         # noqa: PLC0415
    with tempfile.TemporaryDirectory() as td:
        scope, st = _loop_fx(Path(td), "a b")
        rc = _fx_run(scope, st)
        return rc == 0 and _ran(scope, 1) == 1 and _ran(scope, 2) == 1



def selftest() -> int:
    """The cases, through `accept/registry.py`'s own runner.

    Reused rather than reimplemented: `run_cases` is the thing that knows a
    verdict flip from an assert, and a second copy of that logic is a second
    place for it to be wrong.
    """
    from accept import registry as R                        # noqa: PLC0415

    def finding(ok: bool, msg: str, **n) -> R.Finding:
        return R.Finding(check="pipeline", ok=ok, message=msg, numbers=n or {"n": 1})

    cases = [
        R.Case("a_stage_that_needs_a_later_stages_artefact_is_refused", True,
               lambda: finding(not _inverted_table_refused(),
                               "inverted table refused", inverted=1),
               prior_state="MEASURED 2026-09-23 on this file's own first run: "
                           "the table put route before census and required it, "
                           "and audit_graph refused at import -- the resolution "
                           "was network.py --provisional, which already existed",
               neuter=_neuter("ordinal off"), neuters="ordinal off"),
        R.Case("a_stage_that_needs_an_artefact_nothing_produces_is_refused", True,
               lambda: finding(not _unowned_table_refused(),
                               "unowned table refused", unowned=1),
               prior_state="the ledger the gate reads had no declared producer "
                           "anywhere in the tree",
               neuter=_neuter("produced-by map off"), neuters="produced-by map off"),
        R.Case("a_foreign_corpus_is_refused_for_this_seed", True,
               lambda: finding(not _foreign_header_refused(),
                               "Ulfsland-declared sites.yaml refused for Fresh42",
                               foreign=1),
               prior_state="12,301 Pirate68 locations and 16 Pirate68 sites "
                           "were accepted by a Fresh42 run",
               neuter=_neuter("self-declaration off"), neuters="self-declaration off"),
        R.Case("an_unbuilt_world_is_refused_before_the_gate_is_started", True,
               lambda: finding(not _unbuilt_world_refused(),
                               "unbuilt Fresh42 refused with its blocking stage "
                               "named", unmet=1),
               prior_state="MEASURED 2026-09-23: accept --world Fresh42 exited 2 "
                           "at accept/__main__.py:490 HAS NO BUILD LEDGER",
               neuter=_neuter("presence always-present"),
               neuters="presence always-present"),
        R.Case("this_tables_own_order_is_accepted", False,
               lambda: finding(_real_table_accepted(),
                               "the declared stage table has no ordering defect",
                               stages=len(STAGES)),
               neuter=_neuter("ordinal reversed"), neuters="ordinal reversed"),
        R.Case("the_default_seed_is_scanned_on_its_declared_windows", False,
               lambda: finding(_default_seed_scans_declared(),
                               "stage 4 scans Pirate68 on ULFSLAND_WINDOWS and "
                               "derives windows for any other seed", seeds=2),
               prior_state="MEASURED on the from-scratch Ulfsland regeneration: "
                           "derived windows left vestvik's west half of the "
                           "main continent in no window, and stage 8 refused "
                           "'1 town district candidate for 2 towns'",
               neuter=_neuter("field windows derived"),
               neuters="field windows derived"),
        R.Case("an_artefact_declaring_this_seed_is_accepted", False,
               lambda: finding(_own_header_accepted(),
                               "Fresh42-declared sites.yaml accepted for Fresh42",
                               own=1),
               neuter=_neuter("self-declaration blind"),
               neuters="self-declaration blind"),
        R.Case("an_artefact_older_than_its_own_input_is_refused", True,
               lambda: finding(not _stale_artefact_refused(),
                               "a derived artefact older than its input is "
                               "STALE, not PRESENT", stale=1),
               prior_state="MEASURED 2026-09-23: geography.Fresh42.yaml was "
                           "derived from a NINE-window 115,200,404-byte field "
                           "and the field on disk is now 26 windows; every "
                           "header still says Fresh42",
               neuter=_neuter("freshness off"), neuters="freshness off"),
        R.Case("an_artefact_newer_than_its_own_input_is_accepted", False,
               lambda: finding(_fresh_artefact_accepted(),
                               "a derived artefact newer than its input is "
                               "PRESENT", fresh=1),
               neuter=_neuter("freshness always-stale"),
               neuters="freshness always-stale"),
        # ---- IN_PLACE_DEPENDS_ON, both ways ----
        R.Case("a_world_saved_again_leaves_its_writers_current", False,
               lambda: finding(_resaved_world_accepted(),
                               "a world-writing stage's output stays PRESENT "
                               "when the same world is saved again after it",
                               resaved=1),
               prior_state="MEASURED 2026-09-25: drive 1's `status` read the "
                           "census and the ledger STALE against the save of "
                           "the server's own graceful shutdown, and this "
                           "drive's server autosaved an untouched world 30 "
                           "min after start -- so a plain run could never "
                           "take a built world past `first_unmet`",
               neuter=_neuter("lineage keyed on mtime"),
               neuters="lineage keyed on mtime"),
        R.Case("a_world_re_created_or_rolled_back_is_refused", True,
               lambda: finding(not _replaced_world_refused(),
                               "a re-created world and an older copy put back "
                               "each make the stage that wrote into it STALE",
                               replaced=2),
               prior_state="the guard the mtime key gave for free and must "
                           "not be lost: a bare freeze restored over a "
                           "censused world (this drive did it twice) is the "
                           "same uid at a LOWER generation",
               neuter=_neuter("lineage off"), neuters="lineage off"),
        # ---- ADOPT_RESTAMP_DEPENDS_ON ----
        R.Case("an_identical_solve_adopted_is_re_stamped", False,
               lambda: finding(_identical_adoption_restamped(),
                               "a stage-8 solve identical to its slot leaves "
                               "the slot newer than the inputs it was just "
                               "re-derived from", restamped=2),
               prior_state="MEASURED on Fresh42 drive 6: `--from 4` re-ran "
                           "stage 8 at 00:45; sites.Fresh42.yaml kept its "
                           "19:47 mtime (identical bytes, copy skipped) and "
                           "stage 9 refused it STALE, rc 3",
               neuter=_neuter("adopt skips identical"),
               neuters="adopt skips identical"),
        R.Case("a_world_whose_ledger_exists_has_no_unmet_prerequisite", False,
               lambda: finding(_built_world_accepted(),
                               "a world with every declared artefact present "
                               "reports no unmet prerequisite", met=1),
               neuter=_neuter("presence always-absent"),
               neuters="presence always-absent"),
        R.Case("a_table_whose_site_solve_reads_a_later_stages_placements_is_refused",
               True,
               lambda: finding(not _prefix_table_read_refused(),
                               "the pre-2026-09-24 table (sites before "
                               "placements) refused by the read audit",
                               inverted_reads=1),
               prior_state="MEASURED 2026-09-24, Fresh42 from scratch: "
                           "audit_graph passed this table, stage 6 `sites` ran "
                           "137.5 s and refused `seed 'Fresh42' has no sited "
                           "installations` -- it reads worlds/Fresh42/*/"
                           "placements.yaml [solved], which stage 8 produced",
               neuter=_neuter("reads off"), neuters="reads off"),
        R.Case("a_site_reader_answering_for_another_world_is_refused", True,
               lambda: finding(not _foreign_site_reader_refused(),
                               "a site reader resolving worlds/Ulfsland for a "
                               "Fresh42 scope refused", foreign_reads=1),
               prior_state="MEASURED 2026-09-24: settlements/sites.Picker "
                           "defaulted world_dir to worlds/Ulfsland and plan.py "
                           "passed none, so the certified plan.Fresh42.json "
                           "kept off Ulfsland's 13 solved installations and "
                           "read none of Fresh42's 13",
               neuter=_neuter("reads off"), neuters="reads off"),
        R.Case("this_tables_readers_are_ordered_and_declared", False,
               lambda: finding(_real_readers_accepted(),
                               "every stage's reader opens only artefacts an "
                               "earlier, required stage produces",
                               stages=len(STAGES)),
               neuter=_neuter("reads foreign"), neuters="reads foreign"),
        R.Case("a_stage_recording_its_writes_under_a_literal_actor_is_refused",
               True,
               lambda: finding(not _constant_actor_refused(),
                               "stage 10 with --actor 'pipeline' refused",
                               constant_actor=1),
               prior_state="MEASURED 2026-09-24: stage 10 passed --actor "
                           "pipeline, so a from-scratch world's first ledger "
                           "records -- the census survey's zones_generate -- "
                           "belonged to an agent that does not exist",
               neuter=_neuter("actor off"), neuters="actor off"),
        R.Case("every_stage_actor_is_the_running_agent", False,
               lambda: finding(_real_actors_accepted(),
                               "every --actor in the table is $VH_ACTOR",
                               stages=len(STAGES)),
               neuter=_neuter("actor constant"), neuters="actor constant"),
        # ---- STAGE_CURRENCY_DEPENDS_ON, both ways ----
        R.Case("a_stage_whose_producer_changed_is_stale_and_re_run", True,
               lambda: finding(not _changed_producer_rerun(),
                               "a stage whose tool changed after it ran reads "
                               "STALE naming the file, and only it re-runs",
                               stale=1),
               prior_state="MEASURED 2026-09-25: blueprints/solve_placements."
                           "py was fixed twice, worlds/Fresh42/*/placements."
                           "yaml was the pre-fix solver's, and `pipeline.py "
                           "status` read stage 7 `ok` -- satisfied was keyed "
                           "on the artefact's existence alone",
               neuter=_neuter("producer off"), neuters="producer off"),
        R.Case("artefacts_with_no_producer_record_are_unknown_not_satisfied",
               True,
               lambda: finding(not _unknown_producer_rerun(),
                               "an artefact with no producer record reads "
                               "UNKNOWN PRODUCER and is re-run", unknown=1),
               prior_state="MEASURED 2026-09-25: every Fresh42 artefact "
                           "predates producer recording and 11 of 13 stages "
                           "read `ok`, stage 12 among them mid-build",
               neuter=_neuter("producer off"), neuters="producer off"),
        R.Case("a_file_the_producer_reaches_only_at_run_time_is_its_producer",
               True,
               lambda: finding(not _reached_file_in_population(),
                               "an imported helper and a sourced helper each "
                               "stale their stage when edited", reached=2),
               prior_state="MEASURED 2026-09-25: a module-level import "
                           "closure of stage 7 omits blueprints/to_rcon_plan."
                           "py, which solve_placements imports inside "
                           "container_census",
               neuter=_neuter("producer tool-file-only"),
               neuters="producer tool-file-only"),
        R.Case("a_recorded_stage_whose_artefact_is_gone_is_re_run", True,
               lambda: finding(not _recorded_but_gone_rerun(),
                               "a current record does not make a deleted "
                               "artefact present", gone=1),
               neuter=_neuter("presence always-present"),
               neuters="presence always-present"),
        R.Case("a_stage_whose_producer_did_not_change_is_not_re_run", False,
               lambda: finding(_unchanged_producer_kept(),
                               "nothing changed: a second run runs nothing and "
                               "every stage is satisfied and CURRENT", kept=2),
               neuter=_neuter("producer blind"), neuters="producer blind"),
        R.Case("a_current_stage_whose_artefacts_are_present_is_not_re_run",
               False,
               lambda: finding(_unchanged_producer_kept(),
                               "a present, current stage is left alone",
                               kept=2),
               neuter=_neuter("presence always-absent"),
               neuters="presence always-absent"),
        # ---- RUN_DEPENDS_ON, both ways ----
        R.Case("a_stage_downstream_of_a_re_run_stage_is_re_run", True,
               lambda: finding(not _downstream_of_rerun_rerun(),
                               "the stage requiring a re-run stage re-runs "
                               "though no mtime moved", downstream=1),
               prior_state="the only escape was `--force`, and nothing re-ran "
                           "a stage built on an input that had been redone",
               neuter=_neuter("descendants off"), neuters="descendants off"),
        R.Case("a_stage_not_downstream_of_a_re_run_stage_is_left_alone", False,
               lambda: finding(_independent_stage_left(),
                               "an independent stage is not re-run",
                               independent=1),
               neuter=_neuter("descendants by ordinal"),
               neuters="descendants by ordinal"),
        R.Case("from_k_never_runs_a_stage_below_k", False,
               lambda: finding(_from_bound_held(),
                               "--from 3 with stage 1 STALE runs 3 and 4 and "
                               "neither 1 nor 2", below=0),
               prior_state="MEASURED 2026-09-25: there was no way to re-run "
                           "from stage K; --force re-ran every stage <= "
                           "--through, stage 1 worldgen included",
               neuter=_neuter("from ignored"), neuters="from ignored"),
        R.Case("a_stale_world_writing_stage_is_refused_not_re_run", True,
               lambda: finding(not _stale_world_stage_refused(),
                               "a world-writing stage whose tool changed is "
                               "refused with exit 6 and not re-run",
                               world=1),
               prior_state="a producer edit would have re-created the save, "
                           "re-generated census zones, or rebuilt the world",
               neuter=_neuter("world guard off"), neuters="world guard off"),
        R.Case("a_world_writing_stage_that_never_produced_runs", False,
               lambda: finding(_world_stage_created(),
                               "a fresh world's world-writing stage runs",
                               created=1),
               neuter=_neuter("world guard always"),
               neuters="world guard always"),
        # ---- CENSUS_LOOP_DEPENDS_ON, both ways ----
        R.Case("a_route_wanting_unread_ground_has_it_read_and_re_routes", True,
               lambda: finding(not _wanted_ground_read_and_rerouted(),
                               "the census's own producer read exactly the "
                               "wanted cell and the route ran again wanting "
                               "nothing", rounds=1),
               prior_state="MEASURED 2026-09-26, Fresh42 drive 3: stage 11 "
                           "stood written lines 22.6 m (S5) and 24.3 m (S8) "
                           "off the lines stage 10 read, and four legs the "
                           "band refused (S2 S3 S9 W9) routed band-free over "
                           "184 unread cells; nothing re-read them and the "
                           "only remedy printed was an operator's re-survey "
                           "at a width sized for a body on another leg",
               neuter=_neuter("census loop off"), neuters="census loop off"),
        R.Case("a_route_wanting_nothing_does_not_re_census", False,
               lambda: finding(_nothing_wanted_nothing_reread(),
                               "a route whose ground was all read leaves the "
                               "census as it ran, once", census_runs=1),
               neuter=_neuter("census loop blind"),
               neuters="census loop blind"),
        R.Case("a_route_leaving_a_servable_leg_unrouted_is_no_fixed_point",
               True,
               lambda: finding(not _servable_unrouted_is_no_fixed_point(),
                               "0 cells wanted with a servable leg unrouted "
                               "is refused (rc 9) with its owner, not called "
                               "converged", unserved=1),
               prior_state="MEASURED 2026-09-26, Fresh42 drive 4: round 2 "
                           "wanted 0 cells and declared a fixed point with "
                           "S13 (950.5 m) and W9 (2577.0 m) band-free "
                           "SERVABLE, 0 stations unread, unrouted",
               neuter=_neuter("census loop counts cells only"),
               neuters="census loop counts cells only"),
    ]
    section = R.Section(
        name="pipeline", measures="the ORDER of the build stages and the scope "
                                  "of every artefact handed between them",
        instrument="pipeline.audit_graph + pipeline.audit_reads + "
                   "pipeline.audit_actors + pipeline.first_unmet + "
                   "pipeline.status producer currency + pipeline.run",
        population=f"the {len(STAGES)} rows of STAGES and every artefact they "
                   f"declare, and fixture tables run for real in a temp dir",
        run=lambda ctx: [], cases=cases)

    results = R.run_cases(section)
    bad = 0
    for c, ok, why in results:
        # MEASURED WITH THE NEUTER APPLIED, which is the only moment the
        # question means anything: `run_cases` has already undone it by the
        # time it hands the result back, so asking here without re-applying
        # would report the survivors of a healthy instrument -- all of them,
        # every time, which is the reassuring answer and the useless one.
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
                  f"NO other declared dependency refusing, so it cannot be told "
                  f"apart from an emptied instrument")
            bad += 1
    print(f"\npipeline selftest: {len(results) - bad}/{len(results)} "
          f"({'PASS' if not bad else 'FAIL'}), "
          f"declared dependencies {AUDIT_DEPENDS_ON} + "
          f"{STAGE_CURRENCY_DEPENDS_ON} + {RUN_DEPENDS_ON} + "
          f"{IN_PLACE_DEPENDS_ON} + {CENSUS_LOOP_DEPENDS_ON} + "
          f"{ADOPT_RESTAMP_DEPENDS_ON}")
    return 0 if not bad else 1


# ---------------------------------------------------------------------------
# cli
# ---------------------------------------------------------------------------
def _render(scope: Scope) -> None:
    print(f"world {scope.world}  seed {scope.seed}  work {scope.work}")
    print("ok = artefacts present and in scope AND producer CURRENT (the files "
          "recorded when the stage last succeeded are unchanged). W = writes "
          "the live world: never re-run by a file edit; when not current a "
          "plain `run` REFUSES it and names the --from command.")
    for r in status(scope):
        mark = "ok  " if r["satisfied"] else ("MAN " if not r["runnable"] else "--  ")
        ww = "W" if r["writes_world"] else " "
        print(f"{mark}{r['ordinal']:2d} {ww} {r['name']:11s} {r['owner']}")
        for a in r["artefacts"]:
            print(f"        {a['state']:22s} {a['label']:10s} {a['path']}")
            if a["state"] != PRESENT:
                print(f"            {a['detail'].splitlines()[0][:150]}")
        p = r["producer"]
        print(f"        producer {p['state']:16s} {p['detail'][:200]}")
        for c in p["changed"][4:12]:
            print(f"            also: {c}")
        if r["writes_world"] and not r["satisfied"]:
            print(f"        WORLD-WRITING ({r['writes_world']}): a plain run "
                  f"REFUSES it; to redo it: "
                  f"{_redo_command(scope, r['ordinal'], len(STAGES))}")
    unmet = first_unmet(scope)
    if unmet is None:
        print("\nevery declared prerequisite is present and in scope")
    else:
        st, label, why = unmet
        verb = ("CANNOT PRODUCE" if st.command is None
                and label in {a.label for a in st.produces(scope)}
                else "requires")
        print(f"\nFIRST UNMET: stage {st.ordinal} {st.name!r} {verb} "
              f"{label!r}\n  {why.splitlines()[0][:400]}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("op", choices=["status", "run", "selftest", "graph"])
    worldseed.add_arguments(ap)
    ap.add_argument("--work", default=None,
                    help="durable interchange directory (default: "
                         "ledger/runs/<World>/pipeline)")
    ap.add_argument("--world-root", default=WORLD_ROOT_DEFAULT)
    ap.add_argument("--vh-src", default=VH_SRC_DEFAULT)
    ap.add_argument("--through", type=int, default=len(STAGES))
    again = ap.add_mutually_exclusive_group()
    again.add_argument("--force", action="store_true",
                       help="re-run EVERY stage <= --through, stage 1 "
                            "worldgen included: a deliberate full redo")
    again.add_argument("--from", dest="from_", type=int, default=None,
                       metavar="K",
                       help="re-run stage K and every later stage <= "
                            "--through, and never a stage below K; the "
                            "operator's way to redo a world-writing stage")
    ap.add_argument("--replace-live", action="store_true",
                    help="allow a run on the live default seed/world, which "
                         "rewrites the corpus the gate reads")
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)

    if args.op == "selftest":
        return selftest()
    if args.op == "graph":
        bad = audit_graph()
        for line in bad:
            print(line)
        print(f"{len(STAGES)} stages, {len(bad)} ordering defects")
        return 1 if bad else 0

    seed, world = worldseed.apply(args)
    work = Path(args.work) if args.work else \
        HERE / "ledger" / "runs" / world / "pipeline"
    scope = Scope(seed, world, work, Path(args.world_root), Path(args.vh_src))

    if args.op == "status":
        _render(scope)
        if args.json:
            Path(args.json).write_text(json.dumps(
                {"seed": seed, "world": world, "work": str(work),
                 "stages": status(scope)}, indent=1))
        return 0 if first_unmet(scope) is None else 3
    if args.from_ is not None and not 1 <= args.from_ <= args.through:
        ap.error(f"--from {args.from_} must be between 1 and --through "
                 f"{args.through}")
    return run(scope, args.through, force=args.force,
               replace_live=args.replace_live, from_=args.from_)


if __name__ == "__main__":
    raise SystemExit(main())
