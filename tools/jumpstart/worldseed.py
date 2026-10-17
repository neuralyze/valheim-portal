#!/usr/bin/env python3
"""THE SEED, THE WORLD NAME AND THE PATCH WINDOWS, IN ONE PLACE.

WHY THIS MODULE EXISTS.  MEASURED 2026-09-21 by a readiness audit: the string
`Pirate68` was a module constant in eight separate modules

    roads/ribbon.py  roads/network.py  roads/north.py  roads/lamps.py
    settlements/build.py  settlements/portal_hall.py  settlements/seat_table.py
    settlements/terminus_camp.py

with no argument and no environment override anywhere, so "build this factory
on a new seed" was not a thing a caller could ASK FOR -- it was an eight-file
edit.  Every one of those modules now reads `seed()` from here.

TWO RULES, AND THEY ARE THE WHOLE CONTRACT:

  1. THE DEFAULT IS THE LIVE WORLD.  `Ulfsland` / `Pirate68` are the defaults
     because Ulfsland is a live world with an operator in it and 4,703 ledger
     records behind it.  A run with no `--seed` and no `VH_SEED` MUST produce
     what it produced yesterday, byte for byte.  That is not an assertion: see
     `selftest()`, which compares a default resolution against the literals.

  2. A SEED IS NOT A WORLD NAME.  `VH_SEED` picks the world GENERATOR input;
     `VH_WORLD` picks the save/ledger name.  They default together but they are
     independent, because two worlds can share a seed and a ledger keyed on the
     seed would merge them.

PRECEDENCE, stated once: explicit argument  >  environment  >  default.
`apply()` also PUBLISHES the resolved seed back into the environment, because
several stages shell out (`blueprints/run_patchscan.sh` reads `$SEED`) and a
subprocess that disagrees with its parent about the seed is the worst failure
mode available here -- it succeeds and returns the wrong ground.

---------------------------------------------------------------------------
PATCH WINDOWS ARE NAMED BY FUNCTION, NOT BY ULFSLAND GEOGRAPHY.
---------------------------------------------------------------------------
The 1 m PatchScan windows this build routes and sites against were named after
Ulfsland landmasses -- `mainland`, `westisle` -- and that name is carried in
`spec.NODES[*]["patch"]`, in `settlements/plan.py`, in `sites.yaml`, in the
binary field files themselves and in `/tmp/roads/req1m.tsv`.  On a fresh seed
there is no "west isle", so a window called `westisle` is a name with nothing
behind it: the next seed's second landmass is north, or absent.

So each window now has a FUNCTIONAL id saying what the window is FOR, and the
Ulfsland landmass name is kept as a LEGACY ALIAS -- kept, not deleted, because
the field binaries already on disk (`/tmp/roads/h1m.bin`, 230 MB, seven
windows) carry the old id inside them and re-scanning them costs a game
process.  `resolve()` accepts either and is the only lookup any caller should
use.

The functional ids:

    trunk-primary     the window holding the world SPAWN POINT and the trunk
                      network the operator walks out of spawn onto
    trunk-secondary   the second landmass the build routes on.  IT SAYS
                      NOTHING ABOUT HOW YOU GET THERE, and the older wording
                      here -- "reached from trunk-primary by bridge rather
                      than by portal" -- was a fact about Ulfsland written
                      into an id.  MEASURED 2026-09-21: Ulfsland's two trunk
                      landmasses are a 12 m channel apart and their windows
                      OVERLAP, while Fresh42's are 4,346.6 m apart and their
                      windows do not touch at all.  A reader that infers
                      "bridgeable" from overlapping trunk windows is fitting
                      a topology to one coastline; ask
                      `blueprints/site_finder.py::separation_m`, which
                      measures it, or `site_finder.overland`, which answers
                      "different landmass -- needs a boat" outright.
    outlier-<tag>     a window around one portal-reached installation; these
                      ids were already functional (`plains23` = the plains
                      farm on land component 23) and are left alone

WINDOW GEOMETRY IS NOW DERIVED, NOT BORROWED.  `ULFSLAND_WINDOWS` below is
still MEASURED for Pirate68 and is still what the default path uses, byte for
byte.  What changed is that it is no longer the ONLY window set that exists:
`windows_for_components()` builds the same shape of answer out of whatever land
a seed actually has, off `blueprints/site_finder.py::land_components`, and
`roads/field.py::request_for_seed` runs PatchScan on it.  DRIVEN 2026-09-21 on
seed `Fresh42` with no world and no save: 446 land components measured, the
spawn's own landmass identified (component 194, Meadows, 4.0 km2), nine windows
derived and a 115,200,404-byte VHPATCH1 field produced whose header declares
`Fresh42` -- and which `require_seed` then refuses to a Pirate68 run.
"""

from __future__ import annotations

import contextlib
import math
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SEED = "Pirate68"
DEFAULT_WORLD = "Ulfsland"
ENV_SEED = "VH_SEED"
ENV_WORLD = "VH_WORLD"


def seed(default: str = DEFAULT_SEED) -> str:
    """The world-generator seed: `$VH_SEED` if set and non-empty, else default."""
    return os.environ.get(ENV_SEED) or default


def world(default: str = DEFAULT_WORLD) -> str:
    """The save / ledger name: `$VH_WORLD` if set and non-empty, else default."""
    return os.environ.get(ENV_WORLD) or default


# THE INSTALL ROOT, AND IT HAS TWO ENVIRONMENT NAMES THAT USED TO DISAGREE.
#
# `<root>/<World>/config_merged/worlds_local/<World>` is where a world's save
# lives, and the toolchain read the root from TWO variables: `$VALHEIM_ROOT`
# (ledger/writer.py's world manifest, precensus.py's save census, jumpstart.py)
# and `$VALHEIM_WORLD_ROOT` (terraform/rcon.py, accept/instruments.py).  MEASURED
# 2026-10-05 on the from-scratch Ulfsland regeneration, whose operator
# environment names its staging install through `$VALHEIM_ROOT` alone: the
# ledger pinned UlfStaging's `.fwl2` (sha256 d31017ca...), while ribbon.py's
# regrade census, reading `$VALHEIM_WORLD_ROOT` -> the production default,
# froze and censused the LIVE Ulfsland's save (sha256 8833746a..., chunks dated
# 2026-09-19..23).  19 of 30 road legs were refused for burying "standing build
# pieces" that stand in the production world and in no byte of the one being
# built; a live read of the staging server at all 65 reported bury/undermine
# positions found none of the named prefabs.  `require_world_save` could not
# tell, because the world is called `Ulfsland` in both installs.  Fresh42 never
# met it: a different world NAME resolves to a different directory under any
# root.  A same-named world on another root is the case it could not exercise.
#
# ONE RESOLVER: the explicit, more specific name wins, then the general one,
# then the host's production install.
ENV_WORLD_ROOT = "VALHEIM_WORLD_ROOT"
ENV_VALHEIM_ROOT = "VALHEIM_ROOT"
DEFAULT_WORLD_ROOT = "/media/big4/projects/game/valheim"


def world_root() -> Path:
    """The install root holding `<World>/config_merged/...`: `$VALHEIM_WORLD_ROOT`,
    else `$VALHEIM_ROOT`, else the host's production install.  Asked at CALL
    time; every reader of a world's save, ledger manifest or RCON config goes
    through here so that no two of them can name different installs."""
    return Path(os.environ.get(ENV_WORLD_ROOT)
                or os.environ.get(ENV_VALHEIM_ROOT) or DEFAULT_WORLD_ROOT)


@contextlib.contextmanager
def scope(seed_: str, world_: str):
    """Run a block as seed `seed_` / world `world_`, then restore the caller's
    environment exactly (unset stays unset).  For a selftest case that asserts
    about a NAMED world: without it the case silently depends on whatever
    `$VH_SEED`/`$VH_WORLD` the runner happened to export, and passes or fails
    by the shell rather than by the code (MEASURED 2026-09-26:
    `the_hub_is_THIS_worlds_and_NEUTER_reads_Ulfsland` passed under
    VH_SEED=Fresh42 and failed with the environment unset)."""
    saved = {k: os.environ.get(k) for k in (ENV_SEED, ENV_WORLD)}
    os.environ[ENV_SEED], os.environ[ENV_WORLD] = seed_, world_
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def add_arguments(ap, *, world_arg: bool = True) -> None:
    """Add `--seed` (and `--world`) to a stage's parser.

    `default=None` on purpose: argparse must not shadow `$VH_SEED`.  The
    three-way precedence is resolved in `apply()`, in one place, so no stage
    can get it subtly different.
    """
    ap.add_argument("--seed", default=None,
                    help=f"world-generator seed (default: ${ENV_SEED} or "
                         f"{DEFAULT_SEED})")
    if world_arg:
        ap.add_argument("--world", default=None,
                        help=f"world / ledger name (default: ${ENV_WORLD} or "
                             f"{DEFAULT_WORLD})")


def apply(args=None) -> tuple[str, str]:
    """Resolve (seed, world) as argument > environment > default, and PUBLISH.

    Publishing to `os.environ` is deliberate: `field.request` and
    `flatten.run_patchscan` hand `$SEED` to `run_patchscan.sh`, and a helper
    module that resolved the seed independently would be a second source of
    truth.  Returns the pair so the caller can rebind its own module constant.
    """
    s = (getattr(args, "seed", None) or None) or seed()
    w = (getattr(args, "world", None) or None) or world()
    os.environ[ENV_SEED] = s
    os.environ[ENV_WORLD] = w
    return s, w


def is_default(s: str | None = None, w: str | None = None) -> bool:
    """True when this run is the live Ulfsland build and nothing else.

    A stage that carries a number MEASURED off the default pair's ground gates
    it on this (the `spec.seed_scoped` node tables), so a fresh
    seed gets the solver's answer instead of a number measured off a different
    world's ground.
    """
    return (s if s is not None else seed()) == DEFAULT_SEED and \
           (w if w is not None else world()) == DEFAULT_WORLD


# ---------------------------------------------------------------------------
# THE HUMAN-DECLARED SITES.  THE ONLY SITES A PERSON CHOOSES; EVERYTHING ELSE
# IS THE SOLVER'S.
#
# This product is the logic that builds a world from a seed with the human out
# of the loop, so a coordinate a person typed is an exception and has to read
# as one: named, with the person's own words as the reason, scoped to the seed
# whose map it was read off.  A site is HUMAN-DECLARED only when the owner
# chose it by looking at the map.  A number that came out of the solver on a
# previous run -- the 15 `spec.PINNED_SITES` records and the 16 coordinates of
# `plan.ANCHORED_TOWNS` / `plan.ANCHORED_OUTLIERS`, all deleted -- is FROZEN
# SOLVER OUTPUT: it kept an old world's roads from moving, it decided nothing,
# and it meant a regeneration replayed a recording instead of applying the
# rules.  For Pirate68 the owner declared two sites and no others:
#
#   ULFSGARD  the walled harbour CITY wrapping the natural harbour near
#             (1050, 720)
#   SUDRVIK   the harbour VILLAGE below it near (1190, 444), "a road
#             connecting through the meadows"
#
# `xz` IS THE REQUEST, EXACTLY AS THE OWNER GAVE IT, and it is not a position
# the build uses: MEASURED on Pirate68's own 1 m field, 3.19 m of sea stands
# over (1050, 720) and 3.80 m over (1190, 444).  The owner picked a harbour off
# the map and was right about the harbour; the module that owns the kind
# (`settlements/harbour_site.py`, called by settlements/plan.py at stage 8)
# measures the real inlet and the real landing, and THAT is the road network's
# node.  A declared site is therefore a request with a refusal behind it:
# nothing is moved silently, and a request the field cannot honour stops the
# stage with the numbers.
#
# `node` is the road-network node id the site becomes; `road_to` lists the
# declared sites the owner asked to be joined by road.  A declared site no road
# reaches is joined to the nearest trunk node of its own landmass by
# `roads/spec.py::declared_edges` -- by rule, never by a typed leg.
#
# SEED-SCOPED LIKE EVERY OTHER SEED-SCOPED ARTEFACT.  The table is keyed by the
# seed whose map the request was read off; `human_sites()` answers {} for any
# other seed, so a seed with no declaration is solved by the rules alone and is
# byte-for-byte unaffected.  `roads/spec.py` and `settlements/plan.py` then
# REFUSE a published `sites.yaml` that does not carry exactly this set
# (`require_scope` is the same refusal, one layer up).
# ---------------------------------------------------------------------------
HUMAN_SITE_KINDS = ("harbour_city", "harbour_village")

HUMAN_SITES: dict[str, dict[str, dict]] = {
    DEFAULT_SEED: {
        "harbour-city-ulfsgard": dict(
            kind="harbour_city", xz=(1050, 720), node="ulfsgard", road_to=(),
            why='the owner, looking at the map: "there is a great natural '
                'harbor at 1050, 720. just put a city near there in the '
                'meadows. it should be a large city which wraps around the '
                'natural harbor. the harbour should have a wall with a gate '
                'to keep raiding ships out."'),
        "harbour-village-sudrvik": dict(
            kind="harbour_village", xz=(1190, 444), node="sudrvik",
            road_to=("harbour-city-ulfsgard",),
            why='the owner, looking at the map: "another harbor at 1190, 444 '
                'which is below the first one.. another village here and a '
                'road connecting through the meadows."'),
    },
}


def human_sites(seed_: str | None = None) -> dict[str, dict]:
    """The sites a person declared for `seed_` (default: this run's), keyed by
    site id in declaration order; `{}` for a seed nobody declared anything for.

    VALIDATED, because a declaration is read by three stages and a typo in it
    must refuse here once rather than surface as a missing node ten minutes
    into a route: a known kind, an xz pair, a node id, a `why`, and a
    `road_to` naming only sites this seed declares.
    """
    want = seed_ if seed_ is not None else seed()
    table = HUMAN_SITES.get(want) or {}
    for sid, rec in table.items():
        if rec.get("kind") not in HUMAN_SITE_KINDS:
            raise SystemExit(
                f"REFUSING: HUMAN_SITES[{want!r}][{sid!r}] has kind "
                f"{rec.get('kind')!r}; the kinds with a siting module are "
                f"{list(HUMAN_SITE_KINDS)}.")
        xz = rec.get("xz")
        if not (isinstance(xz, (tuple, list)) and len(xz) == 2):
            raise SystemExit(f"REFUSING: HUMAN_SITES[{want!r}][{sid!r}] has no "
                             f"(x, z) request: {xz!r}")
        if not rec.get("node") or not rec.get("why"):
            raise SystemExit(f"REFUSING: HUMAN_SITES[{want!r}][{sid!r}] needs a "
                             f"`node` id and the owner's words as `why`")
        unknown = [t for t in rec.get("road_to", ()) if t not in table]
        if unknown:
            raise SystemExit(f"REFUSING: HUMAN_SITES[{want!r}][{sid!r}] asks "
                             f"for a road to {unknown}, which this seed does "
                             f"not declare")
    return {sid: dict(rec) for sid, rec in table.items()}



# ---------------------------------------------------------------------------
# THE SEED-SCOPE REFUSAL, FOR EVERY SEED-SCOPED ARTEFACT, IN ONE PLACE.
#
# `roads/field.py::require_seed` proved the shape: an artefact measured on one
# seed, handed to a run planning another, SUCCEEDS SILENTLY and returns a plan
# fitted to a planet that does not exist.  It was written for the height field
# and stayed there.  MEASURED 2026-09-21 by driving seed `Fresh42` through
# `roads/network.py plan` on this host, with the height field and the census
# both correctly refused:
#
#   roads/poi.py::load          12,301 keep-out locations accepted, from
#                               /tmp/settle/loc3/f6fe167f4fcd.json, whose own
#                               header reads `"seed": "Pirate68"`.  The router
#                               masked a fresh seed's corridors against
#                               Ulfsland's locations and said nothing.
#   roads/spec.py::load_solved_sites
#                               all 16 sites adopted at Ulfsland's coordinates,
#                               from settlements/sites.yaml, whose own header
#                               reads `world: Ulfsland / seed: Pirate68 /
#                               seed_hash: 147627509`.
#
# In BOTH cases the declaration needed to refuse is INSIDE THE FILE and was
# never read.  That is the class: not a missing measurement, an unread one.
#
# ONE REFUSAL, not one per reader.  A second copy of this text is a second
# thing to keep true; `field.require_seed` now delegates here rather than
# carrying its own, so the wording a stage prints is the same wording every
# stage prints.
#
# AN ARTEFACT THAT DECLARES NOTHING IS REFUSED TOO.  "It did not say" is
# exactly how Ulfsland's numbers walk into a fresh build: the whole defect
# above is an unread declaration, and treating an absent one as permission
# re-opens it for every artefact that has not got the field yet.
# ---------------------------------------------------------------------------

def require_scope(label: str, path, *, found_seed=None, found_world=None,
                  seed_: str | None = None, world_: str | None = None,
                  produced_by: str = "") -> dict:
    """REFUSE an artefact whose declared seed/world is not this run's.

    `found_seed` / `found_world` are what the artefact says about ITSELF, read
    out of the artefact by its own reader.  `seed_` / `world_` default to this
    run's resolved pair.  Fails CLOSED, by raising, and names the producer so
    the refusal carries its own remedy.

    A `found_world` of None is tolerated when `found_seed` matches: several
    artefacts are seed-scoped and world-agnostic by construction (a world
    generator dump is a function of the seed alone).  A `found_seed` of None is
    NOT tolerated -- see the note above.
    """
    want_s = seed_ if seed_ is not None else seed()
    want_w = world_ if world_ is not None else world()
    how = f" Produced by: {produced_by}." if produced_by else ""
    if not found_seed:
        raise SystemExit(
            f"REFUSING: {label} {path} carries no seed declaration, so it "
            f"cannot be shown to belong to seed {want_s!r}. An artefact that "
            f"does not say which world it was measured in is how one world's "
            f"coordinates end up in another's plan -- MEASURED: 12,301 "
            f"Pirate68 locations and 16 Pirate68 sites were both accepted by a "
            f"Fresh42 run before this check existed.{how}")
    if str(found_seed) != str(want_s):
        raise SystemExit(
            f"REFUSING: {label} {path} was measured on seed {found_seed!r} but "
            f"this run is planning seed {want_s!r}. Planning one seed against "
            f"another's measurements succeeds silently and returns an answer "
            f"fitted to ground the world does not have.{how}")
    if found_world and want_w and str(found_world) != str(want_w):
        raise SystemExit(
            f"REFUSING: {label} {path} was measured in world {found_world!r} "
            f"but this run is building {want_w!r}. A measurement is a reading "
            f"of ONE world's ground.{how}")
    return {"label": label, "path": str(path), "seed": str(found_seed),
            "world": None if found_world is None else str(found_world)}

# ---------------------------------------------------------------------------
# PER-SEED FILE SLOTS -- which file a seed's site set, settlement plan and road
# plan live in.  A FUNCTION OF THE SEED AND NOTHING ELSE.
# ---------------------------------------------------------------------------
# SLOTS_DEPENDS_ON = ("seed", the `$VH_*` override).  These seven resolvers
# lived in `roads/spec.py` until 2026-09-24, and importing THAT module loads
# the road network's node table: this seed's solved site set, its solved
# installations and its geography, each refused when absent.  So asking "what
# is this seed's site set CALLED" required the site set to EXIST.  MEASURED on
# Fresh42 from scratch: `settlements/plan.py` -- the PRODUCER of the site set
# -- solved for 157.2 s and then refused `settlements/sites.Fresh42.yaml is
# missing` at the one line that asked spec for its own output's name, and
# threw the solve away.  A key naming a SUPERSET of what the quantity depends
# on, one import deep.  The default seed keeps the bare names, so Ulfsland's
# files do not move.
_JUMPSTART = Path(__file__).resolve().parent
SITES_ENV = "VH_SITES"
PLAN_ENV = "VH_SETTLEMENT_PLAN"
SEGMENTS_ENV = "VH_SEGMENTS"
CROSSINGS_ENV = "VH_CROSSINGS"

# `(directory, stem, suffix, override env)` per resolver: the ONE table the
# resolvers below and the fixed-name check both read, so the names the check
# looks for are the names the resolvers produce.
_SLOTS = {
    "sites_path": ("settlements", "sites", ".yaml", SITES_ENV),
    "plan_path": ("settlements", "plan", ".json", PLAN_ENV),
    "segments_path": ("roads", "segments", ".yaml", SEGMENTS_ENV),
    "crossings_path": ("roads", "crossings", ".yaml", CROSSINGS_ENV),
    "north_path": ("roads", "north_itinerary", ".yaml", "VH_NORTH_ITINERARY"),
}


def _slot_path(s: str, directory: str, stem: str, suffix: str) -> Path:
    """THE NAMING RULE, once: the default seed keeps the bare name."""
    if s == DEFAULT_SEED:
        return _JUMPSTART / directory / f"{stem}{suffix}"
    return _JUMPSTART / directory / f"{stem}.{s}{suffix}"


def _slot(seed_: str | None, directory: str, stem: str, suffix: str,
          env: str) -> Path:
    override = os.environ.get(env)
    if override:
        return Path(override)
    return _slot_path(seed_ or seed(), directory, stem, suffix)


def sites_path(seed_: str | None = None) -> Path:
    """The solved site set for a seed: `settlements/sites.yaml` for the
    default seed, `sites.<seed>.yaml` otherwise, `$VH_SITES` over both."""
    return _slot(seed_, *_SLOTS["sites_path"])


def plan_path(seed_: str | None = None) -> Path:
    """The full settlement solve for a seed: `settlements/plan.json` for the
    default seed, `plan.<seed>.json` otherwise, `$VH_SETTLEMENT_PLAN` over
    both."""
    return _slot(seed_, *_SLOTS["plan_path"])


def sites_name(seed_: str | None = None) -> str:
    """The FILE NAME `plan.py` writes inside its `--out`, for the site set.
    The producer writes into a scratch directory and the reader reads out of
    `settlements/`, so they cannot share a path -- they MUST share a name."""
    return sites_path(seed_).name


def plan_name(seed_: str | None = None) -> str:
    """The FILE NAME `plan.py` writes inside its `--out`, for the plan."""
    return plan_path(seed_).name


def segments_path(seed_: str | None = None) -> Path:
    """The paved road plan for a seed: `roads/segments.yaml` for the default
    seed, `segments.<seed>.yaml` otherwise, `$VH_SEGMENTS` over both."""
    return _slot(seed_, *_SLOTS["segments_path"])


def crossings_path(seed_: str | None = None) -> Path:
    """The crossing contract for a seed: `roads/crossings.yaml` for the
    default seed, `crossings.<seed>.yaml` otherwise, `$VH_CROSSINGS` over
    both."""
    return _slot(seed_, *_SLOTS["crossings_path"])


def north_path(seed_: str | None = None) -> Path:
    """The generated north itinerary; never the historical measured north plan."""
    return _slot(seed_, *_SLOTS["north_path"])


def road_plan_names(seed_: str | None = None) -> dict[str, str]:
    """The two FILE NAMES `network.py emit` writes inside its `--out`, keyed
    by the stem the rest of the tree says."""
    return {"segments": segments_path(seed_).name,
            "crossings": crossings_path(seed_).name}


# WHERE A WORLD'S SOLVED INSTALLATIONS LIVE -- the one declaration both
# readers (`roads/spec.py::installation_xz`, `settlements/sites.py::
# existing_installations`) glob, and the one `pipeline.audit_reads` asks.
# Here and not in either reader because importing `roads/spec.py` loads the
# node table, so a declaration living there could not be asked on a fresh seed.
INSTALLATIONS_GLOB = "*/placements.yaml"
INSTALLATIONS_KEY = "solved"


def installations_read(world_: str) -> tuple[Path, str, str]:
    """(directory, glob, member key) a reader opens for `world_`'s solved
    installations."""
    return _JUMPSTART / "worlds" / str(world_), INSTALLATIONS_GLOB, \
        INSTALLATIONS_KEY


# ---------------------------------------------------------------------------
# THE REST OF WHAT A READER OPENS BY NAME: the road network's plan FILES, and
# a world's ledger.
# ---------------------------------------------------------------------------
# Generated north road-ground is emitted in the canonical segments document.
# The old separate Pirate68 plan is historical diagnostic evidence only: it
# must never augment a regenerated world's required or built population.
NORTH_SEGMENTS_NAME = "north_segments.yaml"


def north_segments_path(seed_: str | None = None) -> Path | None:
    """Historical Pirate68 diagnostic plan, never a generated network input."""
    if (seed_ or seed()) != DEFAULT_SEED:
        return None
    return _JUMPSTART / "roads" / NORTH_SEGMENTS_NAME


def segment_files(seed_: str | None = None) -> tuple[Path, ...]:
    """All generated road-ground, north included, has one canonical plan."""
    return (segments_path(seed_),)


def ledger_path(world_: str | None = None) -> Path:
    """A world's build ledger, `ledger/runs/<world>/ledger.jsonl`."""
    return _JUMPSTART / "ledger" / "runs" / str(world_ or world()) \
        / "ledger.jsonl"


# EVERY RESOLVER ABOVE THAT ANSWERS "WHICH FILE", by name -- the population the
# fixed-name check below treats as READ THROUGH THE SEED, and the calls it
# refuses at import time.
PATH_RESOLVERS = ("sites_path", "plan_path", "sites_name", "plan_name",
                  "segments_path", "crossings_path", "road_plan_names", "north_path",
                  "installations_read", "north_segments_path",
                  "segment_files", "ledger_path")


def default_seed_names() -> dict[str, str]:
    """`{file name: resolver}` -- the names the DEFAULT seed's files carry,
    asked of the naming rule the resolvers use, never transcribed."""
    out = {_slot_path(DEFAULT_SEED, d, stem, suf).name: fn
           for fn, (d, stem, suf, _env) in _SLOTS.items()}
    out[NORTH_SEGMENTS_NAME] = "segment_files"
    return out


# ---------------------------------------------------------------------------
# Patch windows
# ---------------------------------------------------------------------------

TRUNK_PRIMARY = "trunk-primary"
TRUNK_SECONDARY = "trunk-secondary"


@dataclass(frozen=True)
class Window:
    """One PatchScan request window: `(id, cx, cz, half, step)` plus its role."""
    fid: str
    cx: float
    cz: float
    half: float
    step: float
    role: str
    legacy: str = ""


# MEASURED for seed Pirate68: this is `/tmp/roads/req1m.tsv` as it was actually
# run, promoted out of a hand-edited scratch TSV into code so that a reader can
# see what the 230 MB field file contains without a hex editor.  The `legacy`
# column is the id INSIDE that binary.
ULFSLAND_WINDOWS: tuple[Window, ...] = (
    Window(TRUNK_PRIMARY, 798.0, 600.0, 1600.0, 1.0,
           "holds the spawn point, StartTemple, the portal hall and the whole "
           "priority-1 trunk", legacy="mainland"),
    Window(TRUNK_SECONDARY, -760.0, 1188.0, 1600.0, 1.0,
           "the other half of the main continent, reached over the S1/S2 "
           "bridges rather than by portal", legacy="westisle"),
    Window("outlier-south33", 1742.0, -3302.0, 1700.0, 1.0,
           "the southern keep's landmass", legacy="south33"),
    Window("outlier-blackforest6", -3292.0, -3714.0, 1300.0, 1.0,
           "the black-forest base", legacy="blackforest6"),
    Window("outlier-plains23", 578.0, -4478.0, 700.0, 1.0,
           "the plains farm", legacy="plains23"),
    Window("outlier-workshop3054", -4134.0, -906.0, 950.0, 1.0,
           "the workshop island", legacy="workshop3054"),
    Window("outlier-mtn7114", 46.0, 3668.0, 660.0, 1.0,
           "the mountain post", legacy="mtn7114"),
)

# Both directions, so a caller holding either name can reach the other.
LEGACY_OF = {w.fid: w.legacy for w in ULFSLAND_WINDOWS if w.legacy}
FUNCTION_OF = {w.legacy: w.fid for w in ULFSLAND_WINDOWS if w.legacy}


def aliases(name: str) -> tuple[str, ...]:
    """Every id this window is known by, most-canonical first, no duplicates."""
    out = [name]
    for other in (FUNCTION_OF.get(name), LEGACY_OF.get(name)):
        if other and other not in out:
            out.append(other)
    return tuple(out)


def resolve(fields: dict, name: str):
    """Look a patch window up in a loaded field dict by EITHER of its names.

    Raises with both names and the file's actual contents rather than a bare
    KeyError, because "KeyError: 'trunk-primary'" against a binary whose ids
    are Ulfsland landmass names is a five-minute detour every single time.
    """
    for alias in aliases(name):
        if alias in fields:
            return fields[alias]
    raise KeyError(
        f"patch window {name!r} (aliases {aliases(name)}) is not in this field; "
        f"it holds {sorted(fields)}")


def request_rows(windows: tuple[Window, ...] = ULFSLAND_WINDOWS,
                 *, functional: bool = True) -> list[tuple[str, float, float, float, float]]:
    """PatchScan request tuples for `field.request`.

    `functional=False` reproduces the ids inside the field binaries already on
    disk, which is what a re-scan intended to match the existing 230 MB file
    must use.  `functional=True` is what a FRESH seed should be scanned with.
    """
    return [((w.fid if functional else (w.legacy or w.fid)),
             w.cx, w.cz, w.half, w.step) for w in windows]


# ---------------------------------------------------------------------------
# WINDOW GEOMETRY DERIVED FROM THE SEED'S OWN LAND.
#
# `ULFSLAND_WINDOWS` above is MEASURED for Pirate68 and is the only window set
# this build has ever had; a fresh seed that reads it plans against Ulfsland's
# coastline.  What follows derives the same SHAPE of answer from whatever land
# a seed actually has, so `VH_SEED=<new>` can be scanned rather than borrowed.
#
# The measurement is NOT here -- `blueprints/site_finder.py::land_components`
# owns it, because it already owns the labelling that decides what one landmass
# is, and a second definition of "landmass" in this file would disagree with
# the router's on exactly the seeds where it matters.  What is here is pure
# geometry over plain dicts, so it is exercisable with no grid, no game and no
# numpy.
# ---------------------------------------------------------------------------

# PatchScan writes n = round(2*half/step) samples per side, each a float32
# height plus a uint8 biome code (PatchScan.cs:291 and its wire-format note at
# :121-123).  CHECKED against the file this build actually routes on: the seven
# ULFSLAND_WINDOWS give 46,112,400 samples, 230,562,000 bytes of patch bodies,
# and /tmp/roads/h1m.bin is 230,562,258 bytes -- the 258 is the header and the
# seven per-patch descriptors.
WINDOW_BYTES_PER_SAMPLE = 5

# The largest half-extent Ulfsland asked for, and the reason a window is not
# simply "the whole landmass": the spawn component alone is tens of km across,
# and 1 m over it is gigabytes.  A window is a BUDGETED view of a landmass.
WINDOW_HALF_MAX_M = 1600.0
WINDOW_STEP_M = 1.0

# The smallest window this build has ever requested is outlier-mtn7114, half
# 660 m.  A landmass smaller than that window's own footprint is not worth a
# 1 m scan of its own, and without a floor a seed with a thousand skerries
# requests a thousand patches.  It is a DECLARED floor, not a silent one: every
# landmass above it is scanned or the derivation refuses.
MIN_COMPONENT_AREA_M2 = (2.0 * 660.0) ** 2

# Ulfsland's own seven windows cost 230 MB.  The default leaves room for a seed
# whose land is differently shaped without letting one quietly ask for 2 GB.
DEFAULT_WINDOW_BUDGET_BYTES = 512 * 1024 * 1024


def window_samples(half: float, step: float = WINDOW_STEP_M) -> int:
    """Samples per side PatchScan will write for this window (PatchScan.cs:291)."""
    return max(2, int(round((2.0 * half) / step)))


def window_bytes(half: float, step: float = WINDOW_STEP_M) -> int:
    """Bytes of patch body one window costs, heights plus biome codes."""
    n = window_samples(half, step)
    return n * n * WINDOW_BYTES_PER_SAMPLE


def windows_bytes(windows) -> int:
    """Bytes of patch body a whole window set costs."""
    return sum(window_bytes(w.half, w.step) for w in windows)


def windows_for_components(components, spawn_xz, spawn_label, *,
                           half_max_m: float = WINDOW_HALF_MAX_M,
                           step_m: float = WINDOW_STEP_M,
                           min_area_m2: float = MIN_COMPONENT_AREA_M2,
                           budget_bytes: int = DEFAULT_WINDOW_BUDGET_BYTES,
                           separation_of=None,
                           ) -> tuple[Window, ...]:
    """Scan windows for THIS SEED's landmasses, in this module's own naming.

    `components` is `site_finder.land_components(grid)` -- plain dicts measured
    off the seed's coarse height plane.  `spawn_xz` / `spawn_label` are
    `site_finder.component_at(grid, spawn)` for the seed's own spawn point.

    THE RULES, and each one is the definition already written down above:

      * `trunk-primary` is "the window holding the world SPAWN POINT".  So it
        is CENTRED ON THE SPAWN, not on the landmass centroid -- a centroid
        window on a long coastline can miss the spawn entirely, and a window
        called trunk-primary that does not contain the spawn is a lie of the
        same kind as a seed literal.
      * `trunk-secondary` is the next biggest landmass, centred on its own land
        centroid.
      * every further landmass gets `outlier-<name>`, where `<name>` is the
        site-finder's `biome+label` -- the convention Ulfsland's own ids use.
      * half-extent is what it takes to cover the landmass from that centre,
        CLAMPED to `half_max_m`, and when the clamp bites the role text says so
        with the coverage it actually achieved.  A clamped window is an honest
        partial view; a silent one is Ulfsland's mistake with a new coat.

    REFUSES, rather than quietly scanning less:
      * no spawn -> there is no trunk-primary to name.
      * a landmass over the floor that the budget cannot hold -> the operator
        raises the budget or the floor; this function does not choose for them.
    """
    if spawn_xz is None or spawn_label is None:
        raise SystemExit(
            "cannot derive scan windows: this seed's SPAWN POINT is not known, "
            "and `trunk-primary` is defined as the window holding it. The spawn "
            "is the StartTemple instance in the generator's own location dump "
            "-- produce one with tools/seedscan/run_locscan.sh for this seed "
            "and pass it through blueprints/site_finder.py::component_at.")
    keep = [c for c in components if c["area_m2"] >= min_area_m2]
    if not keep:
        raise SystemExit(
            f"cannot derive scan windows: this seed has {len(components)} land "
            f"components and none reaches the {min_area_m2:,.0f} m2 floor "
            f"(biggest {max((c['area_m2'] for c in components), default=0):,.0f} "
            f"m2). Either the grid is not this seed's land or the floor "
            f"(min_area_m2) is wrong for it.")
    spawn_comp = next((c for c in keep if c["label"] == spawn_label), None)
    if spawn_comp is None:
        raise SystemExit(
            f"cannot derive scan windows: the spawn at {spawn_xz} stands on land "
            f"component {spawn_label}, which is below the "
            f"{min_area_m2:,.0f} m2 floor or absent from the measurement. The "
            f"spawn landmass is never optional -- lower min_area_m2.")
    ordered = [spawn_comp] + [c for c in keep if c["label"] != spawn_label]

    out: list[Window] = []
    for rank, comp in enumerate(ordered):
        centre = spawn_xz if rank == 0 else (comp["cx"], comp["cz"])
        need = max(abs(centre[0] - comp["x0"]), abs(comp["x1"] - centre[0]),
                   abs(centre[1] - comp["z0"]), abs(comp["z1"] - centre[1]))
        half = min(half_max_m, math.ceil(need / step_m) * step_m)
        # COVERAGE IS AN INTERSECTION, NOT AN AREA RATIO.  MEASURED wrong once,
        # on the Fresh42 drive: `(2*half)^2 / bbox_area` reported "covers 100%"
        # for outlier-plains215, whose bbox is 2656 x 3680 m -- 480 m of that
        # landmass is outside a 3200 m window no matter how the areas divide,
        # and a window that says it holds a coast it does not is the same false
        # declaration this whole module exists to stop.
        covered = 1.0
        for lo, hi, c in ((comp["x0"], comp["x1"], centre[0]),
                          (comp["z0"], comp["z1"], centre[1])):
            span = hi - lo
            inside = min(hi, c + half) - max(lo, c - half)
            covered *= 1.0 if span <= 0 else max(0.0, min(inside, span)) / span
        if rank == 0:
            fid, what = TRUNK_PRIMARY, "holds the world spawn point"
        elif rank == 1:
            fid, what = TRUNK_SECONDARY, "the second biggest landmass"
        else:
            fid, what = f"outlier-{comp['name']}", "a further landmass"
        clamp = ("" if half < half_max_m else
                 f", CLAMPED at the {half_max_m:g} m half-extent budget")
        # HOW FAR THIS LANDMASS IS FROM THE SPAWN'S, IN THE WINDOW ITSELF.
        # Without it the only thing a reader has is whether two windows
        # overlap, and overlap is a fact about half-extents, not about water:
        # Ulfsland's trunk windows overlap across a 12 m channel, Fresh42's
        # do not touch across 4,346.6 m, and a consumer inferring "bridgeable"
        # from the former gets Ulfsland's topology on every seed.
        gap = ""
        if rank and separation_of is not None:
            metres = separation_of(comp["label"])
            gap = (f"; {metres:,.0f} m of open water from the spawn landmass "
                   f"(site_finder.separation_m) -- NOT walkable, and whether "
                   f"anything can span it is the crossing planner's call")
        out.append(Window(
            fid, round(centre[0], 1), round(centre[1], 1), float(half), float(step_m),
            f"{what}: land component {comp['label']} ({comp['biome']}), "
            f"{comp['area_m2'] / 1e6:.1f} km2 of land, bbox "
            f"{comp['x1'] - comp['x0']:.0f}x{comp['z1'] - comp['z0']:.0f} m; "
            f"this window covers {covered * 100:.0f}% of that bbox{clamp}{gap}"))

    cost = windows_bytes(out)
    if cost > budget_bytes:
        raise SystemExit(
            f"cannot derive scan windows: this seed has {len(ordered)} landmasses "
            f"over the {min_area_m2:,.0f} m2 floor and scanning them at "
            f"{step_m:g} m costs {cost / 1e6:,.0f} MB against a "
            f"{budget_bytes / 1e6:,.0f} MB budget. NOTHING IS DROPPED SILENTLY: "
            f"raise budget_bytes, raise min_area_m2 to scan fewer landmasses, or "
            f"lower half_max_m to scan each one smaller. For scale, Ulfsland's "
            f"own seven windows cost {windows_bytes(ULFSLAND_WINDOWS) / 1e6:,.0f} MB.")
    return tuple(out)


# ---------------------------------------------------------------------------
# WHAT STILL STOPS A FRESH SEED BEING DRIVEN END TO END.
#
# Recorded here, with file and line, because "the seed is a parameter now" is
# true and is not the same sentence as "a fresh seed builds".
#
# RE-MEASURED 2026-09-21 BY ACTUALLY DRIVING ONE.  The previous version of this
# block was written from a reading of the code and it was wrong in both
# directions, which is the reason the entry `silently_planned_against_ulfsland`
# below now exists and the reason two others have gone:
#
#   IT OVERSTATED `site_ids_and_edges_are_not_seed_derived` as the whole site
#     problem.  `spec.load_solved_sites` + `PIN_SCOPE` already take sites from
#     the solver and already decline every Ulfsland pin on a fresh seed.  What
#     was actually broken was one layer down and invisible from a read.
#   IT MISSED, ENTIRELY, that two of the three seed-scoped inputs had no scope
#     check at all, so a fresh-seed run did not fail -- it SUCCEEDED, against
#     Ulfsland's geography, and printed a plan.  A list of prerequisites cannot
#     find that; only running it can.
#
# THE DRIVE, for the record: `VH_SEED=Fresh42 roads/network.py plan
# --provisional --field <a Fresh42 VHPATCH1> --dry-run` on a scratch root, with
# no world, no save and no server.
# ---------------------------------------------------------------------------
UNRESOLVED = {
    # ---- prerequisites a fresh seed simply has not got yet ----
    "window_geometry_is_not_seed_derived":
        "CLOSED 2026-09-21 BY DRIVING IT, not by reading it. A fresh seed now "
        "gets a REAL height field: blueprints/site_finder.py::land_components "
        "measures the seed's own landmasses off its coarse height plane, "
        "site_finder::component_at finds the one the spawn stands on, "
        "worldseed.windows_for_components turns them into trunk-primary / "
        "trunk-secondary / outlier-* geometry and roads/field.py::"
        "request_for_seed runs blueprints/run_patchscan.sh over it. MEASURED "
        "end to end on seed Fresh42, no world, no save, no server, on a scratch "
        "root: 446 land components, spawn on component 194 (Meadows, 4.0 km2), "
        "9 windows, 115,200,404 bytes of VHPATCH1 in 131 s, header `Fresh42`, "
        "refused to a Pirate68 run by require_seed. THE REMAINING COST IS "
        "INFRASTRUCTURE, NOT CODE: both scans boot a sandbox copy of the "
        "dedicated server (tools/seedscan/run_scan.sh:66 and "
        "blueprints/run_patchscan.sh:77), so $VH_SRC must hold a Valheim "
        "install with BepInEx (run_patchscan.sh:32-33). Nothing borrows and "
        "nothing is synthetic: an absent field now refuses by naming these two "
        "stages (roads/field.py::require_field) instead of raising errno 2.",
    "node_geometry_is_not_seed_derived":
        "MOSTLY CLOSED 2026-09-21 BY CALLING THE DERIVATIONS THAT ALREADY "
        "EXISTED. MEASURED: roads/spec.py held 43 literal `xz` coordinates (not "
        "53), and they were TWO DIFFERENT THINGS nobody had separated. "
        "GEOGRAPHY, which a fresh seed must re-derive: 11 `extremity`, 4 "
        "`abutment`, 1 `spawn_point`, 1 `shoreline`, 6 `waterfront_terminal` "
        "(not 2 -- the inherited count was low), 1 `junction`, 1 "
        "`settlement_arrival`. AUTHORED DESIGN, legitimately frozen: the 7 "
        "`installation` nodes and all 40 `EDGES`, which are a TOPOLOGY over "
        "node ids and carry no coordinate at all -- so the edge list was never "
        "the blocker it was recorded as; it survives any seed whose node IDS "
        "survive. "
        "WHAT LANDED: roads/geography.py is the call site. It runs "
        "roads/survey.py::survey_patch for the extremities, "
        "crossings/survey.py::approaches for the abutments and roads/poi.py::"
        "load for the spawn point, snaps to the ribbon's own 2 m pitch, and "
        "writes roads/geography.yaml, which spec.py reads through "
        "`require_scope`. 16 of the 21 geography coordinates are now a "
        "function of the field they are handed; spec.py is down from 43 literal "
        "`xz` tuples to 27. "
        "IT REPRODUCES ULFSLAND EXACTLY: all 16 come back bit-for-bit equal to "
        "the literals they replace on seed Pirate68 against /tmp/roads/h1m.bin "
        "-- 47 nodes, 40 edges, 15 adopted sites unchanged, and "
        "T1-portalhub-temple still plans 666.7 m / grade max 8.00% / clamp8 0 / "
        "margin7 0 / poi-viol 0 to the same segments.yaml sha256. The "
        "hand-copying had been faithful; it just was not a function, and a "
        "constant cannot be evaluated at a second seed. Proven load-bearing by "
        "neuter: with `require_scope` disabled a VH_SEED=Fresh42 import of "
        "spec.py adopts all 16 Ulfsland coordinates silently; with it live the "
        "same import exits 1. "
        "THE ID SCHEME IS WHAT MAKES THE 40 EDGES SURVIVE: an extremity is "
        "keyed by (island ROLE, compass SECTOR) and an abutment by (crossing "
        "ROLE, bank SIDE). None of those four is a position, so the ids are "
        "stable across seeds and every edge still resolves; a sector that "
        "yields no sample REFUSES by name rather than emitting a partial set. "
        "STILL OPEN, 5 nodes and all of them BUILD-TIME or PLACEMENT, not "
        "worldgen: 1 `shoreline` + the 7 `installation` are hand-copies of "
        "worlds/<world>/<preset>/placements.yaml, which DOES declare `world` "
        "and `seed` and is already scope-checked where it is READ "
        "(terraform/flatten.py:128, blueprints/solve_placements.py:715) -- "
        "spec.py just does not read it. 6 `waterfront_terminal` are Crossings' "
        "structures, and crossings/plan.py holds 10 literal coordinate tuples "
        "of its own (4 bridge banks, which roads/geography.py now re-derives "
        "and reproduces exactly, plus 6 waterfront roots); the producer for the "
        "roots exists and is uncalled in the same way -- `crossings/survey.py "
        "bays` scores every shoreline sample for shelter, water fraction and "
        "berth depth. 1 `junction` and 1 `settlement_arrival` are not worldgen "
        "at all: r3-n0b-junction is a point ON A BUILT CENTRELINE (chosen from "
        "20 ranked candidates, 1154.9 m of new road against the next best "
        "1250.4 m) and r3-vestvik is 20.09 m out from vestvik-hall-1's 19.57 m "
        "hard radius, and both carry a `level_y` that only a LIVE read of "
        "standing ground can supply (45.6087 and 35.29). They cannot be derived "
        "before the road they sit on exists, and recording them as worldgen "
        "geography was itself part of the miscount.",
    "silently_planned_against_ulfsland":
        "CLOSED 2026-09-21, and it was never on this list because nobody had "
        "run it. THREE seed-scoped inputs, TWO guards: roads/field.py::"
        "require_seed and roads/precensus.py:850 `require` both refused a "
        "foreign seed, while roads/poi.py::load accepted 12,301 Pirate68 "
        "locations (from a dump whose own header reads "
        "`seedNameFromWorld: Pirate68`) and roads/spec.py::load_solved_sites "
        "adopted all 16 Ulfsland sites (from a file whose own header reads "
        "`world: Ulfsland / seed: Pirate68`). A fourth instance sat in the "
        "GATE: accept/__main__.py Ctx took `--world-src` on trust, so "
        "`accept --world Fresh42` would have measured Fresh42's ledger against "
        "Ulfsland's save. All four now go through `require_scope` / "
        "`instruments.require_world_save`, pinned by accept section "
        "`provenance` (8 cases, 4 REQUIRED to refuse, 4 proven load-bearing by "
        "neuter).",
    "settlement_town_centres_were_ulfslands_two_coordinates":
        "CLOSED 2026-09-21. settlements/plan.py::build carried "
        "want={'stenvik': (496, 904), 'vestvik': (-906, 1846)} and refused to "
        "plan unless the district search returned a candidate at exactly those "
        "two points, so NO seed but Pirate68 could produce a sites.yaml -- the "
        "artefact that supplies 16 of the road network's nodes. The refusal "
        "itself was CORRECT and is kept; what was wrong is what it compared "
        "against. It now compares against a DECLARATION scoped to the seed "
        "being solved (`plan.ANCHORED_TOWNS`, or `--expect-towns id=x,z`), and "
        "a seed with nothing declared takes the district search's own "
        "top-ranked candidates on its own land, justified by what was measured "
        "THERE. A SECOND Ulfsland literal was in the same path and would have "
        "made the first fix useless on a real field: `sites.Picker.town` "
        "defaulted to `patches=('mainland', 'westisle')` and "
        "`plan.island_membership` iterated the same pair, so a DERIVED field "
        "-- whose windows are named `trunk-primary`, `trunk-secondary`, "
        "`outlier-*` -- searched the EMPTY SET for districts and answered "
        "'separate landmass, portal-only' for every site without reading any "
        "land. Both now resolve by ROLE through `worldseed.aliases` "
        "(`sites.trunk_patches`), which is the same move the extremity ids "
        "made. AND THE SAME CUT WAS THEN MADE ONE LAYER DOWN, where the town "
        "fix was measured to stop: with towns resolved, a real derived field "
        "got THROUGH town selection and raised `KeyError: 'mainland'` at "
        "plan.py's first outlier, because all 14 `OUTLIERS` records carried "
        "Ulfsland's x, z AND ITS WINDOW NAME -- `patch='mainland'` is not "
        "'which landmass', it is 'which of Ulfsland's seven scan windows'. "
        "`OUTLIERS` is now authored design alone (id/kind/tag/body/pad/ "
        "cluster/priority) with `ANCHORED_OUTLIERS['Pirate68']` carrying all "
        "14 coordinates, windows and measured prose verbatim; "
        "`select_outliers` takes a declared site where it is declared and an "
        "undeclared one from its own kind's search, REFUSING with the kind, "
        "the count and the candidates rather than returning the empty set as "
        "an answer; `resolve_patch` refuses a window the field lacks by name. "
        "MEASURED end to end on the first real derived field for another seed, "
        "/tmp/seedfield/fresh42_1m.bin (9 windows, header seed Fresh42): "
        "sites.yaml emitted with ALL 16 SITES -- 2 towns at (315.9, -248.8) "
        "and (-938.1, 585.2) plus 14 outliers -- stamped `seed: Fresh42 / "
        "seed_hash: -1894280788 / world: null`, and ZERO Ulfsland strings in "
        "the artefact (no 496/904, -906/1846, Pirate68, Ulfsland, mainland, "
        "westisle, mtn7114, south33). `VH_SEED=Fresh42 VH_SITES=<it> python3 "
        "-c 'import spec'` then IMPORTS CLEAN: 16 sites adopted, 44 nodes, 32 "
        "edges, the 8 missing edges being the S1/S2 crossings that seed's "
        "geography measured impossible. Ulfsland re-emits "
        "settlements/sites.yaml BYTE-IDENTICAL with all 16 sites, and spec "
        "still adopts 15 SETTLEMENT_SITES at digest 79714eb35093a5d0. Pinned "
        "by accept section `townsites` -- 14 cases, 7 REQUIRED to refuse, 14 "
        "proven load-bearing by neuter as at 2026-09-22, AND THAT COUNT "
        "IS A TRANSCRIPTION: it was 11/5/11 forty minutes earlier in the same "
        "session and drifted the moment the section grew. Re-derive it with "
        "`accept/__main__.py --selftest --section townsites` rather than "
        "citing this line; the authority is the run, not the prose. "
        "SUPERSEDED 2026-10-07: `plan.ANCHORED_TOWNS`, `plan.ANCHORED_OUTLIERS` "
        "and `spec.PINNED_SITES` -- the declarations this entry describes -- "
        "were FROZEN SOLVER OUTPUT (the places the same search returned on an "
        "earlier run) and are deleted. The only declared sites are "
        "`worldseed.HUMAN_SITES` (Ulfsgard and Sudrvik, the owner's, measured "
        "by settlements/harbour_site.py); every other site, the two towns "
        "included, is the search's own answer.",
    "no_orchestrator":
        "RE-MEASURED 2026-09-21 and UNCHANGED as a fact, with sharper numbers: "
        "94 modules under tools/jumpstart carry their own "
        "`if __name__ == \"__main__\"`, and 83 hardcoded `/tmp/...` literals "
        "are how stages hand files to each other. tools/jumpstart/jumpstart.py "
        "is a PRESET APPLIER over RCON, not a stage driver: nothing anywhere "
        "in this repo runs stage N and then stage N+1. Naming the stages is "
        "not the same as building the front door; that is a separate ticket. "
        "The two /tmp paths that are load-bearing INPUTS rather than scratch "
        "-- roads/poi.py:72 `/tmp/settle/loc3/f6fe167f4fcd.json` and "
        "roads/field.py's patch files -- are now at least SCOPE-CHECKED, so a "
        "stale one refuses instead of planning; that closes the silent half of "
        "this and none of the durability half.",

    # ---- the two ordering defects: MEASURED, architectural, NOT fixed here --
    "ordering_defect_A_road_written_before_it_can_see_what_stands_there":
        "roads/ribbon.py:3960 emits zones_generate for every zone the ribbon "
        "touches -- which is the moment a mod location is CREATED -- and "
        "roads/ribbon.py:3983 then asks only `objects_count id=_ZoneCtrl`, "
        "i.e. 'is this zone generated', never 'what is standing in it'. 42 of "
        "48 obstructing bodies were already standing when the road was "
        "written. Owner: routing + Crossings.",
    "ordering_defect_B_routes_to_the_site_centre_not_the_pad_edge":
        "roads/network.py:258 searches site CENTRE to site CENTRE, although "
        "roads/spec.py:274 states the ribbon must terminate at the PAD EDGE; "
        "`pad_radius_m` is never applied to the search endpoints and "
        "roads/network.py:553 COMPUTES AND REPORTS `endpoint_standoff_m` "
        "without enforcing it. settlements/build.py then drops the body on "
        "that same centre without reading a road. Owner: routing + "
        "Settlements.",

    # ---- the contract question, left alone deliberately -------------------
    "stamp_vs_guard_who_owns_the_ground_under_a_deck":
        "roads/ribbon.py:1213 skips a sample inside a protected over-water "
        "disc ONLY when the generated height is below the y=30 water plane "
        "(`return \"underwater\"`), while "
        "tools/jumpstart/ledger/writer.py:1170 `_flatten_forbidden_check` "
        "refuses ANY movement past tolerance anywhere in the same disc, from "
        "the same `_site_evidence`. Two rules, one population. It is why N1, "
        "N2 and N3 keep 37 impassable steps. This is a bridge/road ownership "
        "contract question -- who owns the ground under a deck -- not a bug "
        "to patch on one side.",
}


# NEWEST_SAVE_DEPENDS_ON -- WHICH SAVE IS "THE" SAVE.
#
# The dedicated server keeps ONE save set and ROTATES it: it writes
# `_main.<N+1>.*`, then deletes `_main.<N>.*`.  For the length of each save
# both generations exist, and the one that is about to vanish is the OLDER.
#   * A LEXICAL sort puts `_main.9` after `_main.10`, so "the newest" was the
#     file the rotation deletes next -- MEASURED: a live Fresh42 build unit died
#     with FileNotFoundError on `_main.9.db2` inside writer.fingerprint.
#   * An `st_mtime` sort key STATS INSIDE THE SORT, so a file removed between
#     the glob and the key raises in the middle of choosing.
# The server writes the generation into the name; that number is the key, and
# the only stat is on the file already chosen, re-resolved if it vanished.
NEWEST_SAVE_DEPENDS_ON = ("generation_number", "vanished_during_rotation")


def _save_generation_of(p: Path) -> int | None:
    parts = p.name.split(".")
    if len(parts) == 3 and parts[0] == "_main" and parts[1].isdigit():
        return int(parts[1])
    return None


def newest_save(worlds: Path, ext: str, *, attempts: int = 40,
                pause_s: float = 0.25,
                depends_on: tuple = NEWEST_SAVE_DEPENDS_ON,
                lister=None) -> Path | None:
    """The newest `_main.<N>.<ext>` in a world directory, by N.  None when the
    directory holds no save of that kind.  Raises FileNotFoundError only if the
    chosen generation keeps vanishing for `attempts` rounds -- a directory that
    never settles is not a directory that has no save.

    `lister(worlds, ext)` replaces the glob; it exists so the selftest can
    reproduce the race a real directory cannot (a name listed, then gone)."""
    import time

    worlds = Path(worlds)
    last = None
    for _ in range(max(1, attempts)):
        if lister is not None:
            found = list(lister(worlds, ext))
        else:
            found = list(worlds.glob(f"_main.*.{ext}")) if worlds.is_dir() else []
        if "generation_number" in depends_on:
            ranked = [(g, p) for p in found
                      if (g := _save_generation_of(p)) is not None]
            if not ranked:
                return None
            last = max(ranked)[1]
        else:  # the shipped lexical key, kept for the neuter
            if not found:
                return None
            last = sorted(found)[-1]
        if "vanished_during_rotation" not in depends_on or last.exists():
            return last
        time.sleep(pause_s)
    raise FileNotFoundError(
        f"{worlds}: the newest _main.*.{ext} ({last.name if last else None}) kept "
        f"vanishing for {attempts} rounds -- the server is rotating its save and "
        "the directory never settled")


# ---------------------------------------------------------------------------
# NO TOOL READS ANOTHER SEED'S ARTEFACT BY A FIXED NAME.
#
# FIXED_NAME_DEPENDS_ON: which file a reader opens depends on THE RUN'S SEED
# AND WORLD, asked AT CALL TIME, and on nothing spelled in its source.
#
# FOUND FIVE TIMES IN ONE SESSION, ONE INSTANCE AT A TIME, before a check
# existed: the gate read Ulfsland's `roads/crossings.yaml` for Fresh42 and
# prescribed a 1.885 m terrain cut at a declared bridge's landing; settlements
# read Pirate68's location dump by its hash; `roads/boardwalk.py` read
# `crossings.yaml`; settlements used Ulfsland's hub; `terraform/flatten.py`
# resolved Ulfsland's data directory and wrote 13 Fresh42 entry files into
# it.  Three more were still standing when this was written:
# `roads/structures.py:103`, `roads/precensus.py:1083`, `roads/deviate.py:944`.
# A defect found five times by hand is a class, and a class is closed by a
# check over the whole tree, not by a sixth fix.
#
# THE CHECK IS AN AST WALK, NOT A GREP, for three reasons a grep passes:
#   * `"north_seg" + "ments.yaml"` is the same file.  String expressions are
#     FOLDED: concatenation, constant f-strings, module string constants.
#   * `deviate.py:944` spells no name at all: `for p in ST.SEGMENT_FILES`
#     reads ANOTHER MODULE'S import-time tuple.  A module-level name built
#     from a fixed name -- an assignment, or a function or class whose
#     DEFAULTS are -- is tainted, and every reference to it from another
#     module is a hit.
#   * `SEGMENT_FILES = worldseed.segment_files()` at module level asks the
#     resolver ONCE, at import, before `apply(args)` has published `--seed`.
#     A resolver called at import time is a hit.
#
# WHAT A FIXED NAME IS, each DERIVED rather than listed:
#   NAME   a path whose file name is the DEFAULT seed's name for a per-seed
#          artefact -- `default_seed_names()`, the resolvers' own rule
#   HASH   a path carrying the default seed's content hash, the id
#          `pipeline.Scope.seed_hash` names the location dump and grid by
#   WORLD  a path with the default world or seed as a component, or a path
#          join with either as a part or a fallback: `ledger/runs/Ulfsland/`
#
# THIS MODULE IS THE ONE HOME for those names and is not scanned.  Anything
# else is a hit unless `FIXED_NAME_EXEMPT` names it with a reason -- and an
# exemption whose site has gone is a hit too, because a list of exemptions
# that outlives its sites is a second list nobody re-reads.
# ---------------------------------------------------------------------------
FIXED_NAME_DEPENDS_ON = ("folded_literal", "imported_constant", "call_time",
                         "resolved_read")
_FIXED_NAME_MODE = "real"   # real | grep | resolver_blind | import_time_blind

# label: (the dependency it breaks, module, module global, value); module ""
# is this one.
FIXED_NAME_NEUTERS = {
    # A SUBSTRING SEARCH, which is what every instance above survived: it
    # sees the name where it is spelled whole and nothing else.
    "fixed name grep": ("folded_literal+imported_constant", "",
                        "_FIXED_NAME_MODE", "grep"),
    # CANNOT TELL A RESOLVED READ FROM A FIXED ONE: every resolver call is
    # judged as the literal it answers on the default seed.
    "fixed name resolver blind": ("resolved_read", "", "_FIXED_NAME_MODE",
                                  "resolver_blind"),
    # WHEN IS NOT ASKED: an import-time resolution passes as a resolved one.
    "fixed name import time blind": ("call_time", "", "_FIXED_NAME_MODE",
                                     "import_time_blind"),
    # THE SAVE A CENSUS READS, `accept/instruments.py::world_save`, one
    # neuter per direction: the shipped `Path(AI.WORLD_SRC_DEFAULT)` -- one
    # world's save for every world, unchecked -- and a resolver keyed on the
    # world that does not refuse a save it cannot find.
    "world save literal": ("world_being_built", "accept.instruments",
                           "_WORLD_SAVE_MODE", "literal"),
    "world save unchecked": ("save_declares_it", "accept.instruments",
                             "_WORLD_SAVE_MODE", "unchecked"),
}

_FIXED_NAME_SKIP = ("library/staging/", "worldseed.py")

# NOT A WORLD'S ARTEFACT, whatever its path says.  Keyed on the VALUE, because
# the same host measurement is spelled in several modules.
FIXED_NAME_HOST = {
    "/media/big4/projects/game/valheim/Ulfsland/data/bepinex":
        "the GAME BUILD PatchScan and the server binaries run out of "
        "(`VH_SRC`): it depends on the build, not on the world -- MEASURED "
        "2026-09-23, Fresh42's install holds no `data/` at all "
        "(terraform/flatten.py:90-97)",
}

# `(file, enclosing function, text)` -> why this site is NOT a read of another
# seed's artefact.  Each row was read, not pattern-matched.  Three kinds only:
# a NEUTER that restores the defect on purpose, a FIXTURE that writes the name
# into a scratch tree it built, and a DISPATCH that hands the bare family name
# straight to this module's resolver.
_NEUTER = ("the registered neuter that RESTORES the fixed name on purpose, so "
           "its case can be shown to fail on it")
_FIXTURE = ("a constructed fixture: the name is written into a scratch tree "
            "or an argv the case built, never read from this checkout")
FIXED_NAME_EXEMPT: dict[tuple[str, str, str], str] = {
    ("accept/sections/roads.py", "crossings_corpus_path", "crossings.yaml"):
        _NEUTER + " (CROSSINGS_CORPUS_BY_SEED = False)",
    ("settlements/plan.py", "_names_without_the_seed", "plan.json"):
        _NEUTER + " (the loss arm: names without the seed)",
    ("settlements/plan.py", "_names_without_the_seed", "sites.yaml"):
        _NEUTER + " (the loss arm: names without the seed)",
    ("settlements/plan.py",
     "_case_NEUTER_names_drop_the_seed_the_solve_lands_in_the_DEFAULT_slot",
     "plan.json"): _NEUTER + " (its case asserts the default slot's names)",
    ("settlements/plan.py",
     "_case_NEUTER_names_drop_the_seed_the_solve_lands_in_the_DEFAULT_slot",
     "sites.yaml"): _NEUTER + " (its case asserts the default slot's names)",
    ("settlements/build.py", "hub_placement",
     "worlds/Ulfsland/deepnorth-sandbox"):
        _NEUTER + " (HUB_SOURCE == 'ulfsland', the loss arm of "
                  "_case_the_hub_is_THIS_worlds_and_NEUTER_reads_Ulfsland)",
    ("buildstage.py", "<module>",
     "/media/big4/projects/game/valheim/Ulfsland/config_merged/bepinex/data"):
        "_ULFSLAND_DATA_DIR_LITERAL: the known-bad resolver kept verbatim as "
        "the fixture `world_scope_defects` must refuse",
    ("buildstage.py", "<module>", "/w/segments.yaml"):
        _FIXTURE + " (_CMD_BUILDFRESH, the argv the resume-key cases compare)",
    ("pipeline.py", "_foreign_header_refused", "sites.yaml"): _FIXTURE,
    ("pipeline.py", "_own_header_accepted", "sites.yaml"): _FIXTURE,
    ("accept/sections/provenance.py", "_case_foreign_site_set_is_refused",
     "sites.yaml"): _FIXTURE,
    ("accept/sections/provenance.py",
     "_case_scope_is_read_from_the_header_not_the_path",
     "f6fe167f4fcd.json"):
        _FIXTURE + " -- named with Pirate68's hash ON PURPOSE, to prove a "
                   "file name is not a declaration",
    ("accept/sections/roads.py",
     "_case_a_fresh_seeds_own_crossings_corpus_is_the_one_read",
     "crossings.yaml"):
        _FIXTURE + " -- the default seed's corpus stood BESIDE this seed's, "
                   "to prove the other one is read",
    ("emitscope.py", "resolve_citation", "sites.yaml"):
        "a DISPATCH: a citation naming the bare family `sites.yaml` is "
        "resolved by `worldseed.sites_path(seed)` on the next line",
}


def _fold(n, consts: dict[str, str]) -> str | None:
    """The string an expression evaluates to, when that is knowable from the
    source alone: constants, `+`, constant f-strings, module string names."""
    import ast                                              # noqa: PLC0415
    if isinstance(n, ast.Constant):
        return n.value if isinstance(n.value, str) else None
    if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add):
        a = _fold(n.left, consts)
        b = None if a is None else _fold(n.right, consts)
        return None if b is None else a + b
    if isinstance(n, ast.JoinedStr):
        parts = []
        for v in n.values:
            if isinstance(v, ast.FormattedValue):
                if v.format_spec is not None or v.conversion not in (-1, 115):
                    return None
                v = v.value
            s = _fold(v, consts)
            if s is None:
                return None
            parts.append(s)
        return "".join(parts)
    if isinstance(n, ast.Name):
        return consts.get(n.id)
    return None


def _default_component(c: str) -> bool:
    for w in (DEFAULT_WORLD, DEFAULT_SEED):
        if c.startswith(w) and c[len(w):len(w) + 1] in ("", "-", "_", "."):
            return True
    return False


def _judge(s: str, names: dict[str, str], hash_: str) -> str | None:
    """NAME / HASH / WORLD, or None: is this string a path to a default-seed
    artefact?  A string with whitespace in it is prose, not a path."""
    if not s or s.split(None, 1) != [s] or s in FIXED_NAME_HOST:
        return None
    t = s.replace("\\", "/")
    last = t.rstrip("/").rpartition("/")[2]
    if last in names:
        return "NAME"
    if hash_ in t:
        return "HASH"
    if ((DEFAULT_WORLD in t or DEFAULT_SEED in t)
            and ("/" in t or "." in last[1:])
            and any(_default_component(c) for c in t.split("/"))):
        return "WORLD"
    return None


def _fixed_name_basis() -> tuple[dict[str, str], str]:
    import types                                            # noqa: PLC0415
    import pipeline                                         # noqa: PLC0415
    hash_ = pipeline.Scope.seed_hash.fget(
        types.SimpleNamespace(seed=DEFAULT_SEED))
    return default_seed_names(), hash_


def _resolve_module(importer: str, dotted: str, level: int, files: set[str],
                    by_name: dict[str, list[str]]) -> list[str]:
    """The file(s) an import names, inside the scanned tree."""
    parts = [p for p in (dotted or "").split(".") if p]
    here = importer.rpartition("/")[0]
    if level:
        for _ in range(level - 1):
            here = here.rpartition("/")[0]
        rel = "/".join(([here] if here else []) + parts) + ".py"
        return [rel] if parts and rel in files else []
    if not parts:
        return []
    if len(parts) > 1:
        rel = "/".join(parts) + ".py"
        return [rel] if rel in files else []
    name = parts[0] + ".py"
    for c in (f"{here}/{name}" if here else name, name):
        if c in files:
            return [c]
    # A BARE IMPORT resolves through whichever `sys.path.insert` the tool
    # made; every same-named module is a candidate.
    return by_name.get(name, [])


def _statements(body):
    """Every statement, at any depth, without descending into expressions:
    an import is a statement, and walking every expression to find one
    costs a second full traversal of the tree."""
    stack = list(body)
    while stack:
        st = stack.pop()
        yield st
        for f in ("body", "orelse", "finalbody", "handlers", "cases"):
            v = getattr(st, f, None)
            if isinstance(v, list):
                stack.extend(v)


class _FixedNameModule:
    """One parsed file: its string constants, its imports, its exports."""

    def __init__(self, rel: str, src: str, files: set[str],
                 by_name: dict[str, list[str]]):
        import ast                                          # noqa: PLC0415
        self.rel = rel
        self.tree = ast.parse(src)
        self.consts: dict[str, str] = {}
        self.imports: dict[str, list[str]] = {}
        self.from_names: dict[str, tuple[list[str], str]] = {}
        self.ws_aliases = {"worldseed"}
        self.ws_funcs: dict[str, str] = {}
        for st in self.tree.body:
            if (isinstance(st, ast.Assign) and isinstance(st.value, ast.Constant)
                    and isinstance(st.value.value, str)):
                for t in st.targets:
                    if isinstance(t, ast.Name):
                        self.consts[t.id] = st.value.value
        for n in _statements(self.tree.body):
            if isinstance(n, ast.Import):
                for a in n.names:
                    if a.asname is None and "." in a.name:
                        continue              # `import a.b` binds `a` only
                    local = a.asname or a.name
                    if a.name.split(".")[-1] == "worldseed":
                        self.ws_aliases.add(local)
                    self.imports[local] = _resolve_module(
                        rel, a.name, 0, files, by_name)
            elif isinstance(n, ast.ImportFrom):
                mod = n.module or ""
                for a in n.names:
                    local = a.asname or a.name
                    sub = _resolve_module(rel, f"{mod}.{a.name}".strip("."),
                                          n.level, files, by_name)
                    if sub:                   # `from pkg import module`
                        self.imports[local] = sub
                        if a.name == "worldseed":
                            self.ws_aliases.add(local)
                        continue
                    if mod.split(".")[-1] == "worldseed":
                        if a.name in PATH_RESOLVERS:
                            self.ws_funcs[local] = a.name
                        continue
                    self.from_names[local] = (
                        _resolve_module(rel, mod, n.level, files, by_name),
                        a.name)

    def exports(self) -> dict[str, list]:
        """Module-level names -> the expressions evaluated AT IMPORT to build
        them: assignments, and function / method defaults."""
        import ast                                          # noqa: PLC0415
        out: dict[str, list] = {}

        def defaults(args) -> list:
            return list(args.defaults) + [d for d in args.kw_defaults if d]

        def visit(stmts) -> None:
            for st in stmts:
                if isinstance(st, ast.Assign):
                    for t in st.targets:
                        for nm in ast.walk(t):
                            if isinstance(nm, ast.Name):
                                out.setdefault(nm.id, []).append(st.value)
                elif (isinstance(st, ast.AnnAssign) and st.value is not None
                      and isinstance(st.target, ast.Name)):
                    out.setdefault(st.target.id, []).append(st.value)
                elif isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.setdefault(st.name, []).extend(defaults(st.args))
                elif isinstance(st, ast.ClassDef):
                    for b in st.body:
                        if isinstance(b, (ast.FunctionDef,
                                          ast.AsyncFunctionDef)):
                            out.setdefault(st.name, []).extend(
                                defaults(b.args))
                        elif isinstance(b, (ast.Assign, ast.AnnAssign)) \
                                and b.value is not None:
                            out.setdefault(st.name, []).append(b.value)
                elif isinstance(st, ast.If) and not _is_main_guard(st.test):
                    visit(st.body)
                    visit(st.orelse)
                elif isinstance(st, (ast.Try, ast.With)):
                    visit(st.body)
                    for h in getattr(st, "handlers", ()):
                        visit(h.body)
                    visit(getattr(st, "orelse", ()))
                    visit(getattr(st, "finalbody", ()))
        visit(self.tree.body)
        return out


def _is_main_guard(test) -> bool:
    import ast                                              # noqa: PLC0415
    return (isinstance(test, ast.Compare) and isinstance(test.left, ast.Name)
            and test.left.id == "__name__")


def _path_parts(n) -> list | None:
    """The parts of a path join -- `a / b / c`, `Path(a, b)`, `os.path.join`."""
    import ast                                              # noqa: PLC0415
    if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div):
        parts = []
        while isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div):
            parts.append(n.right)
            n = n.left
        parts.append(n)
        return parts[::-1]
    if isinstance(n, ast.Call):
        f = n.func
        name = f.id if isinstance(f, ast.Name) else (
            f.attr if isinstance(f, ast.Attribute) else "")
        if name in ("Path", "PurePath", "PosixPath", "joinpath") or (
                name == "join" and isinstance(f, ast.Attribute)
                and isinstance(f.value, ast.Attribute)
                and f.value.attr == "path"):
            return list(n.args)
    return None


def _grep_fixed_names(mods: dict[str, str], names, hash_) -> list[dict]:
    """THE NEUTERED INSTRUMENT: a substring search over the source text."""
    out = []
    needles = tuple(names) + (hash_,)
    for rel, src in mods.items():
        for i, line in enumerate(src.splitlines(), 1):
            for nd in needles:
                if nd in line:
                    out.append({"file": rel, "line": i, "kind": "GREP",
                                "text": nd, "where": "", "why": "grep"})
                    break
    return out


def fixed_name_reads(root: Path | None = None,
                     sources: dict[str, str] | None = None) -> dict:
    """EVERY PATH IN THE TREE THAT NAMES ANOTHER SEED'S ARTEFACT.

    `sources` (`{relative path: source}`) scans a tree built in memory, which
    is how the known-bad state is kept as a fixture; otherwise every `*.py`
    under `root` (default: this directory) is read.

    Returns `{"files", "hits", "exempt", "stale", "resolved"}`: `hits` are
    the unexempted findings, `resolved` the resolver calls made at call time
    -- the reads this tree DOES make through the seed.
    """
    import ast                                              # noqa: PLC0415
    live = sources is None
    if live:
        base = Path(root) if root else _JUMPSTART
        sources = {}
        for p in sorted(base.rglob("*.py")):
            rel = str(p.relative_to(base))
            if not rel.startswith(_FIXED_NAME_SKIP):
                sources[rel] = p.read_text(errors="replace")
    names, hash_ = _fixed_name_basis()
    mode = _FIXED_NAME_MODE
    if mode == "grep":
        return {"files": len(sources), "resolved": 0, "exempt": [],
                "stale": [],
                "hits": _grep_fixed_names(sources, names, hash_)}

    files = set(sources)
    by_name: dict[str, list[str]] = {}
    for f in sorted(files):
        by_name.setdefault(f.rpartition("/")[2], []).append(f)
    mods: dict[str, _FixedNameModule] = {}
    hits: list[dict] = []
    for rel, src in sources.items():
        try:
            mods[rel] = _FixedNameModule(rel, src, files, by_name)
        except SyntaxError as exc:
            # UNREADABLE IS NOT CLEAR.
            hits.append({"file": rel, "line": exc.lineno or 0,
                         "kind": "UNPARSED", "text": "", "where": "<module>",
                         "why": f"cannot be parsed, so cannot be cleared: {exc}"})

    Constant, Name, Attribute, Call = (ast.Constant, ast.Name, ast.Attribute,
                                       ast.Call)
    BinOp, JoinedStr, Lambda = ast.BinOp, ast.JoinedStr, ast.Lambda
    FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)
    INERT = (ast.expr_context, ast.operator, ast.boolop, ast.unaryop,
             ast.cmpop, ast.Name, ast.arg, ast.Import, ast.alias, ast.Pass)
    WORLD_OR_SEED = (DEFAULT_WORLD, DEFAULT_SEED)

    def why(kind: str, text: str) -> str:
        if kind == "NAME":
            last = text.replace("\\", "/").rstrip("/").rpartition("/")[2]
            return (f"the default seed's `{last}` by name; ask "
                    f"worldseed.{names[last]}() at call time")
        if kind == "HASH":
            return (f"the default seed's content hash {hash_} in a path; "
                    f"resolve THIS seed's artefact")
        return ("the default world/seed as a path component; resolve the "
                "run's world")

    def local_hits(m: _FixedNameModule, n) -> tuple[list, bool]:
        """(hits, covered): the fixed names ONE node spells, as `(node, kind,
        text, why)`, and whether that answers for its whole subtree."""
        t = type(n)
        if t is Constant:
            v = n.value
            k = _judge(v, names, hash_) if type(v) is str else None
            return ([(n, k, v, why(k, v))] if k else []), True
        if t is JoinedStr or (t is BinOp and type(n.op) is ast.Add):
            s = _fold(n, m.consts)
            if s is not None:
                k = _judge(s, names, hash_)
                return ([(n, k, s, why(k, s))] if k else []), True
        out = []
        if t is BinOp or t is Call:
            for part in _path_parts(n) or ():
                s = _fold(part, m.consts)
                if s is not None:
                    if ("/" not in s and _judge(s, names, hash_) is None
                            and _default_component(s)):
                        out.append((part, "WORLD", s,
                                    f"`{s}` joined into a path; resolve the "
                                    f"run's world"))
                    continue
                # A FALLBACK IS A PART TOO: `(cen.world or "Ulfsland")`.
                for sub in ast.walk(part):
                    if type(sub) is Constant and sub.value in WORLD_OR_SEED:
                        out.append((sub, "WORLD", sub.value,
                                    f"`{sub.value}` as a path part's "
                                    f"fallback; an artefact that declares no "
                                    f"world is refused, not defaulted"))
        return out, False

    def expr_hits(m: _FixedNameModule, e) -> list:
        out, stack = [], [e]
        while stack:
            n = stack.pop()
            if isinstance(n, INERT):
                continue
            h, covered = local_hits(m, n)
            out += h
            if not covered:
                stack.extend(ast.iter_child_nodes(n))
        return out

    def is_resolver(m: _FixedNameModule, f) -> str:
        if (type(f) is Attribute and type(f.value) is Name
                and f.value.id in m.ws_aliases and f.attr in PATH_RESOLVERS):
            return f.attr
        if type(f) is Name and f.id in m.ws_funcs:
            return m.ws_funcs[f.id]
        return ""

    def import_time_calls(m: _FixedNameModule, e) -> list:
        """Resolver calls made when `e` is evaluated -- not a lambda's body."""
        out, stack = [], [e]
        while stack:
            n = stack.pop()
            if type(n) is Lambda:
                stack.extend(list(n.args.defaults)
                             + [d for d in n.args.kw_defaults if d])
                continue
            if type(n) is Call and is_resolver(m, n.func):
                out.append(n)
            stack.extend(ast.iter_child_nodes(n))
        return out

    def ref_taint(m: _FixedNameModule, n) -> str:
        """Where the value this reference reads was fixed, or ''."""
        if type(n) is Attribute and type(n.value) is Name \
                and n.value.id in m.imports:
            for c in m.imports[n.value.id]:
                if c != m.rel and n.attr in tainted.get(c, {}):
                    return tainted[c][n.attr]
        elif type(n) is Name and n.id in m.from_names:
            cands, attr = m.from_names[n.id]
            for c in cands:
                if c != m.rel and attr in tainted.get(c, {}):
                    return tainted[c][attr]
        return ""

    # TAINT: which module-level names are built from a fixed name, or from a
    # resolver asked at import time.  Then to a fixpoint, across modules.
    tainted: dict[str, dict[str, str]] = {rel: {} for rel in mods}
    refs: dict[tuple[str, str], list] = {}
    for rel, m in mods.items():
        for name, exprs in m.exports().items():
            origin = ""
            for e in exprs:
                lh = expr_hits(m, e)
                if lh:
                    origin = f"{rel}:{lh[0][0].lineno}"
                    break
                it = ([] if mode == "import_time_blind"
                      else import_time_calls(m, e))
                if it:
                    origin = f"{rel}:{it[0].lineno} (import time)"
                    break
            if origin:
                tainted[rel][name] = origin
            else:
                refs[(rel, name)] = [
                    n for e in exprs for n in ast.walk(e)
                    if type(n) is Name
                    or (type(n) is Attribute and type(n.value) is Name)]
    changed = True
    while changed:
        changed = False
        for (rel, name), nodes in refs.items():
            if name in tainted[rel]:
                continue
            m = mods[rel]
            for n in nodes:
                o = ((tainted[rel].get(n.id, "") if type(n) is Name else "")
                     or ref_taint(m, n))
                if o:
                    tainted[rel][name] = o
                    changed = True
                    break

    resolved = 0
    for rel, m in mods.items():
        def emit(node, kind, text, where, why_, _rel=rel):
            hits.append({"file": _rel, "line": getattr(node, "lineno", 0),
                         "kind": kind, "text": text, "where": where,
                         "why": why_})

        def visit(n, where: str, runtime: bool, m=m, emit=emit) -> None:
            nonlocal resolved
            if isinstance(n, INERT):
                return
            t = type(n)
            if t in FUNCS or t is ast.ClassDef:
                inner = n.name if where == "<module>" else f"{where}.{n.name}"
                pre = list(n.decorator_list) + (
                    list(n.bases) if t is ast.ClassDef
                    else list(n.args.defaults)
                    + [d for d in n.args.kw_defaults if d])
                for d in pre:
                    visit(d, where, runtime)
                for s in n.body:
                    visit(s, inner, runtime if t is ast.ClassDef else True)
                return
            if t is Lambda:
                for d in (list(n.args.defaults)
                          + [d for d in n.args.kw_defaults if d]):
                    visit(d, where, runtime)
                visit(n.body, where, True)
                return
            if t is ast.If and _is_main_guard(n.test):
                for s in n.body:
                    visit(s, where, True)
                for s in n.orelse:
                    visit(s, where, runtime)
                return
            if t is ast.AnnAssign:                # the annotation is no read
                if n.value is not None:
                    visit(n.value, where, runtime)
                return
            if t is ast.ImportFrom:
                for a in n.names:
                    origin = ref_taint(m, Name(id=a.asname or a.name))
                    if origin:
                        emit(n, "IMPORTED", a.name, where,
                             f"imports `{a.name}`, built from a fixed name "
                             f"at {origin}")
                return
            h, covered = local_hits(m, n)
            for node, kind, text, why_ in h:
                emit(node, kind, text, where, why_)
            if covered:
                return
            if t is Attribute:
                origin = ref_taint(m, n)
                if origin:
                    emit(n, "IMPORTED", ast.unparse(n), where,
                         f"another module's value, built from a fixed name "
                         f"at {origin}")
                    return
            elif t is Call:
                r = is_resolver(m, n.func)
                if r and mode == "resolver_blind":
                    emit(n, "RESOLVER", ast.unparse(n), where,
                         "NEUTERED: judged as the default seed's literal")
                elif r and not runtime and mode != "import_time_blind":
                    emit(n, "IMPORT_TIME", ast.unparse(n), where,
                         f"`{r}` asked at IMPORT answers for the seed the "
                         f"process started with, before apply(args) "
                         f"publishes --seed; ask it at call time")
                elif r:
                    resolved += 1
            for ch in ast.iter_child_nodes(n):
                visit(ch, where, runtime)

        for st in m.tree.body:
            visit(st, "<module>", False)

    # ONE ROW PER SITE, then the exemptions -- each of which must still name
    # a site that exists.
    seen: set = set()
    rows = []
    for h in sorted(hits, key=lambda h: (h["file"], h["line"], h["kind"],
                                         h["text"])):
        k = (h["file"], h["line"], h["kind"], h["text"])
        if k not in seen:
            seen.add(k)
            rows.append(h)
    exempt, open_ = [], []
    used: set = set()
    for h in rows:
        key = (h["file"], h["where"], h["text"])
        if key in FIXED_NAME_EXEMPT and mode == "real":
            used.add(key)
            exempt.append({**h, "exempt": FIXED_NAME_EXEMPT[key]})
        else:
            open_.append(h)
    stale = ([{"file": f, "line": 0, "kind": "STALE_EXEMPTION", "text": t,
               "where": w, "why": "an exemption whose site is gone"}
              for (f, w, t) in FIXED_NAME_EXEMPT if (f, w, t) not in used]
             if live and mode == "real" else [])
    return {"files": len(sources), "hits": open_ + stale, "exempt": exempt,
            "stale": stale, "resolved": resolved}


def format_fixed_name(h: dict) -> str:
    where = f" in {h['where']}" if h.get("where") else ""
    return (f"{h['file']}:{h['line']} [{h['kind']}] `{h['text']}`{where} -- "
            f"{h['why']}")


# THE KNOWN-BAD STATE, AS SHIPPED, BUILT IN MEMORY at the line numbers it
# shipped at.  The tree it names is fixed; a check that reads the live files
# could be passed by deleting them.  The last file is the reason this is not
# a grep.
_SHIPPED_FIXED_NAME_READS = {
    "roads/structures.py": {
        98: 'SEGMENTS = HERE / "segments.yaml"',
        103: 'SEGMENT_FILES = (SEGMENTS, HERE / "north_segments.yaml")',
        104: 'SETTLE_PLAN = JUMPSTART / "settlements" / "plan.json"',
        105: 'LEDGER = JUMPSTART / "ledger" / "runs" / "Ulfsland" / '
             '"ledger.jsonl"'},
    "roads/precensus.py": {
        1083: 'SEGMENT_FILES = (HERE / "segments.yaml", '
              'HERE / "north_segments.yaml")'},
    "roads/deviate.py": {
        77: "import structures as ST  # noqa: E402",
        942: "def main():",
        943: "    file_of = {}",
        944: "    for p in ST.SEGMENT_FILES:",
        945: "        file_of[p] = p"},
    "obfuscated.py": {
        1: 'SEGMENT_FILES = (HERE / ("north_seg" + "ments.yaml"),)'},
    # AND THE SAVE: ribbon's regrade census, reading another module's
    # constant, which names Ulfsland's save for every world it is asked about.
    "accept/instruments.py": {
        177: 'WORLD_SRC_DEFAULT = ("/media/big4/projects/game/valheim/'
             'Ulfsland/config_merged"',
        178: '                     "/worlds_local/Ulfsland")'},
    "roads/ribbon.py": {
        8520: "def main():",
        9822: "        import accept.instruments as AI",
        9825: "        wsrc = Path(AI.WORLD_SRC_DEFAULT)",
        9826: "        return wsrc"},
}
_SHIPPED_MUST_CATCH = (("roads/structures.py", 103),
                       ("roads/precensus.py", 1083),
                       ("roads/deviate.py", 944),
                       ("obfuscated.py", 1),
                       ("roads/ribbon.py", 9825))

# THE SAME THREE, FIXED THE WAY THEY WERE FIXED: asked at call time.
_CALL_TIME_READS = {
    "roads/structures.py": {
        1: "import worldseed",
        3: "def segment_files():",
        4: "    return worldseed.segment_files()"},
    "roads/precensus.py": {
        1: "import worldseed",
        3: "def replay(segments_yaml=None):",
        4: "    return [p for p in (segments_yaml or worldseed.segment_files())"
           " if p.exists()]"},
    "roads/deviate.py": {
        1: "import structures as ST",
        3: "def main():",
        4: "    for p in ST.segment_files():",
        5: "        print(p)"},
}

# AND "FIXED" AT IMPORT, which reads the seed the process started with.
_IMPORT_TIME_READS = {
    "roads/structures.py": {
        1: "import worldseed",
        2: "SEGMENT_FILES = worldseed.segment_files()"},
    "roads/deviate.py": {
        1: "import structures as ST",
        3: "def main():",
        4: "    for p in ST.SEGMENT_FILES:",
        5: "        print(p)"},
}


def _fixture_sources(rows: dict[str, dict[int, str]]) -> dict[str, str]:
    return {rel: "\n".join(lines.get(i, "") for i in
                           range(1, max(lines) + 1)) + "\n"
            for rel, lines in rows.items()}


def _fixed_name_neuter(label: str):
    """Break exactly ONE declared dependency, in exactly ONE direction."""
    import importlib                                        # noqa: PLC0415
    _dep, modname, key, value = FIXED_NAME_NEUTERS[label]

    def apply():
        g = (globals() if not modname
             else importlib.import_module(modname).__dict__)
        prior = g[key]
        g[key] = value

        def undo() -> None:
            g[key] = prior
        return undo
    return apply


# The live tree's call-time resolver reads, MEASURED when the check landed,
# as a floor: an accepting sweep that saw none of them saw no tree.
_FIXED_NAME_RESOLVED_FLOOR = 40


def _instruments():
    """`accept/instruments.py`, with this directory APPENDED to the path (a
    package under it shadows `roads/network.py` if it is put in front)."""
    import importlib                                        # noqa: PLC0415
    import sys                                              # noqa: PLC0415
    if str(_JUMPSTART) not in sys.path:
        sys.path.append(str(_JUMPSTART))
    return importlib.import_module("accept.instruments")


def _world_save_in_scratch(world: str) -> tuple[str, str]:
    """(what `instruments.world_save` answered for `world`, the world THAT
    save says it is) over a scratch root holding Ulfsland's and Fresh42's
    saves and nothing else.  A refusal is an answer, not an exception."""
    import tempfile                                         # noqa: PLC0415
    AI = _instruments()
    prior = os.environ.pop("JUMPSTART_WORLD_SRC", None)
    try:
        with tempfile.TemporaryDirectory() as td:
            for w in (DEFAULT_WORLD, "Fresh42"):
                d = Path(td) / w / "config_merged" / "worlds_local" / w
                d.mkdir(parents=True)
                (d / "_main.1.fwl2").write_bytes(b"")
            try:
                got = AI.world_save(world, root=td)
            except SystemExit as exc:
                return "REFUSED", str(exc)
            return (str(got.relative_to(td)),
                    str(AI.save_world_name(got) if got.exists() else None))
    finally:
        if prior is not None:
            os.environ["JUMPSTART_WORLD_SRC"] = prior


def fixed_name_cases(R) -> list:
    """The fixed-name cases, as `accept.registry` cases, for any runner."""
    def finding(ok: bool, msg: str, **n):
        return R.Finding(check="no_fixed_name_reads", ok=ok, message=msg,
                         numbers=n or {"n": 1})

    def caught(r: dict) -> set:
        return {(h["file"], h["line"]) for h in r["hits"]}

    def shipped():
        r = fixed_name_reads(sources=_fixture_sources(
            _SHIPPED_FIXED_NAME_READS))
        missed = [f"{f}:{ln}" for f, ln in _SHIPPED_MUST_CATCH
                  if (f, ln) not in caught(r)]
        return finding(
            bool(missed),
            f"the three shipped plan-file reads, ribbon's read of Ulfsland's "
            f"save through another module, and one name split across `+`: "
            f"{len(r['hits'])} hits, missed {missed or 'none'}",
            hits=len(r["hits"]), must_catch=len(_SHIPPED_MUST_CATCH),
            missed=len(missed))

    def call_time():
        r = fixed_name_reads(sources=_fixture_sources(_CALL_TIME_READS))
        return finding(not r["hits"],
                       f"the same three reads asked of worldseed at call "
                       f"time: {len(r['hits'])} hits, {r['resolved']} "
                       f"resolved reads"
                       + (f"; {format_fixed_name(r['hits'][0])}"
                          if r["hits"] else ""),
                       hits=len(r["hits"]), resolved=r["resolved"])

    def import_time():
        r = fixed_name_reads(sources=_fixture_sources(_IMPORT_TIME_READS))
        got = {(h["file"], h["line"], h["kind"]) for h in r["hits"]}
        want = {("roads/structures.py", 2, "IMPORT_TIME"),
                ("roads/deviate.py", 4, "IMPORTED")}
        return finding(not want <= got,
                       f"a resolver asked at import time, and read from "
                       f"another module: caught {sorted(got & want)} of "
                       f"{sorted(want)}",
                       hits=len(r["hits"]), must_catch=len(want),
                       caught=len(got & want))

    def live():
        r = fixed_name_reads()
        return finding(
            not r["hits"] and r["resolved"] >= _FIXED_NAME_RESOLVED_FLOOR,
            f"{r['files']} files: {len(r['hits'])} fixed-name reads open, "
            f"{len(r['exempt'])} exempt by name, {len(r['stale'])} stale "
            f"exemptions, {r['resolved']} reads resolved through worldseed "
            f"at call time"
            + (f"; first: {format_fixed_name(r['hits'][0])}"
               if r["hits"] else ""),
            files=r["files"], open=len(r["hits"]), exempt=len(r["exempt"]),
            stale=len(r["stale"]), resolved=r["resolved"])

    def world_save_is_the_builds():
        got, name = _world_save_in_scratch("Fresh42")
        return finding(name == "Fresh42",
                       f"the regrade census of a Fresh42 build reads {got}, "
                       f"the save of world {name!r}",
                       worlds_in_root=2)

    def unresolvable_save_refused():
        got, why = _world_save_in_scratch("Ghost")
        return finding(got != "REFUSED",
                       f"a world with no save under the root: {got} "
                       f"({why[:80]})", worlds_in_root=2)

    return [
        R.Case("a_seed_scoped_artefact_read_by_a_fixed_name_is_refused", True,
               shipped,
               prior_state="roads/structures.py:103, roads/precensus.py:1083 "
                           "and roads/deviate.py:944 read `segments.yaml` + "
                           "`north_segments.yaml` through an import-time "
                           "tuple of fixed names, so on VH_SEED=Fresh42 all "
                           "three read Ulfsland's 45 planned runs and never "
                           "roads/segments.Fresh42.yaml",
               neuter=_fixed_name_neuter("fixed name grep"),
               neuters="fixed name grep"),
        R.Case("the_same_reads_asked_of_worldseed_at_call_time_are_accepted",
               False, call_time,
               neuter=_fixed_name_neuter("fixed name resolver blind"),
               neuters="fixed name resolver blind"),
        R.Case("a_resolver_asked_at_import_time_is_refused", True, import_time,
               neuter=_fixed_name_neuter("fixed name import time blind"),
               neuters="fixed name import time blind"),
        R.Case("every_seed_scoped_artefact_this_tree_reads_is_its_seeds",
               False, live,
               neuter=_fixed_name_neuter("fixed name resolver blind"),
               neuters="fixed name resolver blind"),
        R.Case("the_census_of_a_build_reads_the_save_of_the_world_built",
               False, world_save_is_the_builds,
               prior_state="roads/ribbon.py:9825 `wsrc = Path(AI."
                           "WORLD_SRC_DEFAULT)` -- the regrade census "
                           "fingerprinted, snapshotted and censused "
                           "ULFSLAND'S live save on every world ribbon "
                           "built, with no require_world_save",
               neuter=_fixed_name_neuter("world save literal"),
               neuters="world save literal"),
        R.Case("a_build_whose_save_cannot_be_resolved_is_refused", True,
               unresolvable_save_refused,
               neuter=_fixed_name_neuter("world save unchecked"),
               neuters="world save unchecked"),
    ]


def fixed_name_selftest(verbose: bool = True) -> dict:
    """The four fixed-name cases through `accept/registry.py`'s own runner,
    each re-run under its neuter; the open sites printed when the live sweep
    refuses."""
    from accept import registry as R                       # noqa: PLC0415
    section = R.Section(
        name="worldseed",
        measures="every file a tool opens for a seed-scoped artefact is "
                 "resolved for the run's seed and world at call time",
        instrument="worldseed.fixed_name_reads, an AST walk with folded "
                   "string expressions and cross-module taint",
        population="every *.py under tools/jumpstart except library/staging "
                   "and this module",
        run=lambda ctx: [], cases=fixed_name_cases(R))
    rows = R.run_cases(section)
    out = {c.name: {"ok": ok, "proof": c.proof_mode or "unproven",
                    "neuter": c.neuters, "why": why} for c, ok, why in rows}
    if verbose:
        for c, ok, why in rows:
            print(f"  {'PASS' if ok else 'FAIL'}  {c.name}  "
                  f"proof={c.proof_mode or 'unproven'}  neuter={c.neuters}"
                  + (f"\n        {why}" if why else ""))
        if not all(ok for _c, ok, _w in rows):
            for h in fixed_name_reads()["hits"]:
                print(f"        OPEN  {format_fixed_name(h)}")
    return out


def selftest(verbose: bool = True) -> dict:
    """PROVE THE DEFAULT PATH IS UNCHANGED, AND PROVE AN OVERRIDE IS HONOURED.

    Both directions, because a resolver that ignored its inputs entirely would
    pass a default-only check, and a resolver that lost its default would pass
    an override-only check.
    """
    import argparse

    saved = {k: os.environ.get(k) for k in (ENV_SEED, ENV_WORLD)}
    try:
        for k in (ENV_SEED, ENV_WORLD):
            os.environ.pop(k, None)
        out: dict = {}

        # (a) bare default
        out["default"] = (seed(), world())
        assert out["default"] == (DEFAULT_SEED, DEFAULT_WORLD), out["default"]
        assert is_default()

        # (b) environment alone
        os.environ[ENV_SEED] = "Freshling"
        out["env"] = (seed(), world())
        assert out["env"] == ("Freshling", DEFAULT_WORLD), out["env"]
        assert not is_default()

        # (c) argument beats environment
        ap = argparse.ArgumentParser()
        add_arguments(ap)
        out["arg_over_env"] = apply(ap.parse_args(["--seed", "Argwins"]))
        assert out["arg_over_env"] == ("Argwins", DEFAULT_WORLD), out["arg_over_env"]
        assert os.environ[ENV_SEED] == "Argwins", "apply() must publish"

        # (d) no argument, environment survives  -- the direction that catches
        #     an `apply()` that silently forces the default.
        os.environ[ENV_SEED] = "Envwins"
        out["env_survives_apply"] = apply(ap.parse_args([]))
        assert out["env_survives_apply"] == ("Envwins", DEFAULT_WORLD)

        # (e) window resolution, both names, and a miss that says so
        fake = {"mainland": "A", "outlier-plains23": "B"}
        out["legacy_name"] = resolve(fake, TRUNK_PRIMARY)
        out["functional_name"] = resolve(fake, "mainland")
        assert out["legacy_name"] == out["functional_name"] == "A"
        assert resolve(fake, "outlier-plains23") == "B"
        assert resolve(fake, "plains23") == "B"
        try:
            resolve(fake, TRUNK_SECONDARY)
        except KeyError as exc:
            out["miss_message"] = str(exc)
        else:
            raise AssertionError("resolve() must refuse a window it has not got")

        # (f) the request rows round-trip to the ids in the binary on disk
        legacy_ids = [r[0] for r in request_rows(functional=False)]
        assert legacy_ids[:2] == ["mainland", "westisle"], legacy_ids
        assert [r[0] for r in request_rows()][:2] == [TRUNK_PRIMARY, TRUNK_SECONDARY]

        # (g) the derived window set: spawn landmass first even when it is not
        #     the biggest, the spawn inside its own window, and the floor
        #     dropping a skerry.  Plain dicts, so this needs no grid and no
        #     game -- the geometry is the part that can be wrong silently.
        comps = [
            {"label": 2, "cells": 9000, "area_m2": 9.0e6, "name": "plains2",
             "biome": "Plains", "x0": 0.0, "x1": 3000.0, "z0": 0.0, "z1": 3000.0,
             "cx": 1500.0, "cz": 1500.0},
            {"label": 7, "cells": 4000, "area_m2": 4.0e6, "name": "meadows7",
             "biome": "Meadows", "x0": -3000.0, "x1": -1000.0,
             "z0": -3000.0, "z1": -1000.0, "cx": -2000.0, "cz": -2000.0},
            {"label": 9, "cells": 40, "area_m2": 4.0e4, "name": "meadows9",
             "biome": "Meadows", "x0": 5000.0, "x1": 5200.0,
             "z0": 5000.0, "z1": 5200.0, "cx": 5100.0, "cz": 5100.0},
        ]
        derived = windows_for_components(comps, (-2500.0, -2500.0), 7)
        out["derived_ids"] = [w.fid for w in derived]
        assert out["derived_ids"] == [TRUNK_PRIMARY, TRUNK_SECONDARY], out["derived_ids"]
        primary = derived[0]
        assert (primary.cx, primary.cz) == (-2500.0, -2500.0)
        assert abs(primary.cx - (-2500.0)) <= primary.half
        assert primary.cx + primary.half >= -1000.0, "primary must cover its landmass"
        out["derived_bytes"] = windows_bytes(derived)
        try:
            windows_for_components(comps, (-2500.0, -2500.0), 7, budget_bytes=1024)
        except SystemExit as exc:
            assert "NOTHING IS DROPPED SILENTLY" in str(exc)
        else:
            raise AssertionError("a budget it cannot meet must refuse, not scan less")
        try:
            windows_for_components(comps, None, None)
        except SystemExit as exc:
            assert "run_locscan.sh" in str(exc)
        else:
            raise AssertionError("no spawn must refuse: trunk-primary holds it")

        # (h) the default path is arithmetic, not a derivation: Ulfsland's seven
        #     windows still price out to the file this build routes on.
        assert windows_bytes(ULFSLAND_WINDOWS) == 230_562_000

        # (i) NEWEST_SAVE_DEPENDS_ON, neutered in each direction on a directory
        #     that reproduces the server's rotation: _main.9 and _main.10 both
        #     present, _main.9 about to be deleted.
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            for n in (9, 10):
                (d / f"_main.{n}.db2").write_bytes(b"x" * n)
            (d / "10_24__1_2.chunk").write_bytes(b"")  # not a save; ignored
            out["newest_save"] = newest_save(d, "db2").name
            assert out["newest_save"] == "_main.10.db2", out["newest_save"]
            # LOSS: the shipped lexical key picks the generation being deleted.
            lexical = newest_save(d, "db2", depends_on=("vanished_during_rotation",))
            out["newest_save_NEUTER_lexical"] = lexical.name
            assert lexical.name == "_main.9.db2", (
                "NEUTER DID NOT FLIP: the lexical key must pick _main.9")
            # LOSS: the rotation removes a generation BETWEEN the listing and
            #       the use.  The lister returns _main.12 (already deleted) on
            #       the first round and the settled directory after it.  With
            #       the vanish term dropped the dead path is returned and the
            #       caller's stat raises; with it, the live one is re-resolved.
            (d / "_main.11.db2").write_bytes(b"new")
            rounds = {"n": 0}

            def racing(worlds_, ext_):
                rounds["n"] += 1
                live = list(worlds_.glob(f"_main.*.{ext_}"))
                return live + [worlds_ / f"_main.12.{ext_}"] if rounds["n"] == 1 else live

            out["newest_save_after_rotation"] = newest_save(
                d, "db2", lister=racing, pause_s=0.0).name
            assert out["newest_save_after_rotation"] == "_main.11.db2"
            rounds["n"] = 0
            dead = newest_save(d, "db2", lister=racing, pause_s=0.0,
                               depends_on=("generation_number",))
            out["newest_save_NEUTER_no_vanish"] = (dead.name, dead.exists())
            assert (dead.name, dead.exists()) == ("_main.12.db2", False), (
                "NEUTER DID NOT FLIP: without the vanish term the dead name must win")
            # SURVIVOR (a verdict, not a crash): one generation, any key.
            single = Path(td) / "one"
            single.mkdir()
            (single / "_main.3.db2").write_bytes(b"y")
            out["newest_save_single"] = (
                newest_save(single, "db2").name,
                newest_save(single, "db2", depends_on=("vanished_during_rotation",)).name)
            assert out["newest_save_single"] == ("_main.3.db2", "_main.3.db2")
            out["newest_save_empty"] = newest_save(Path(td) / "missing", "db2")
            assert out["newest_save_empty"] is None

        # (j) THE READ SLOTS THE FIXED-NAME CHECK ASKS FOR, both seeds: the
        #     default keeps Ulfsland's two plan files and its ledger byte for
        #     byte; a fresh seed gets its own plan and no northern network.
        for k in (SEGMENTS_ENV, SITES_ENV, PLAN_ENV, CROSSINGS_ENV):
            assert not os.environ.get(k), f"${k} is set; (j) would lie"
        out["segment_files_default"] = [str(p.relative_to(_JUMPSTART))
                                        for p in segment_files(DEFAULT_SEED)]
        assert out["segment_files_default"] == ["roads/segments.yaml"]
        out["segment_files_fresh"] = [str(p.relative_to(_JUMPSTART))
                                      for p in segment_files("Fresh42")]
        assert out["segment_files_fresh"] == ["roads/segments.Fresh42.yaml"]
        assert ledger_path(DEFAULT_WORLD) == (
            _JUMPSTART / "ledger" / "runs" / "Ulfsland" / "ledger.jsonl")
        assert sorted(default_seed_names()) == sorted(
            ["sites.yaml", "plan.json", "segments.yaml", "crossings.yaml",
             "north_segments.yaml"]), default_seed_names()

        # (k) NO TOOL READS ANOTHER SEED'S ARTEFACT BY A FIXED NAME.
        out["fixed_names"] = fixed_name_selftest(verbose)
        failed = [n for n, r in out["fixed_names"].items() if not r["ok"]]
        assert not failed, f"fixed-name cases FAILED: {failed}"

        if verbose:
            for k, v in out.items():
                print(f"  {k}: {v}")
            print("worldseed selftest OK")
        return out
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["fixed-names"]:
        _r = fixed_name_reads()
        for _h in _r["hits"]:
            print(format_fixed_name(_h))
        for _h in _r["exempt"]:
            print(f"EXEMPT {format_fixed_name(_h)} :: {_h['exempt']}")
        print(f"{_r['files']} files, {len(_r['hits'])} open, "
              f"{len(_r['exempt'])} exempt, {len(_r['stale'])} stale, "
              f"{_r['resolved']} resolved at call time")
        sys.exit(1 if _r["hits"] else 0)
    selftest()
