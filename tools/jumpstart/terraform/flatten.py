#!/usr/bin/env python3
"""Level a building pad headlessly, by writing terrain data instead of using a hoe.

ROUTE, and why it is this one (all three measured on Ulfsland tonight):

  1. World Edit Commands' `terrain` command IS reachable from RCON through
     `consoleCommand` -- it parses its arguments and prints its own errors -- but
     it selects ground with `Heightmap::FindHeightmap`, which scans the static
     list of INSTANTIATED zone heightmaps.  A dedicated server with no players
     instantiates no zones, so the selection is always empty: the command
     returns silently, creates no `_TerrainCompiler`, and changes nothing.
     MEASURED: `terrain from=241.5,353.5,78 circle=8 level=77.47` produced no
     console output and no ZDO.
  2. ValheimRcon's `modifyObject` cannot reach ZDO byte arrays -- its own
     `list` output offers only -position, -rotation, -health, -tag, -prefab.
     So plain RCON cannot write TCData.
  3. WEC's `spawn_object <prefab> from=x,z,y data=<entry>` CAN.  A WEC data
     entry is YAML in <BepInEx config>/data/*.yaml and its `bytes:` field is a
     list of "<key>, <base64>" strings, hashed with the ZDO key hash and
     base64-decoded straight into the ZDO's byte-array map (MEASURED from
     WorldEditCommands.dll IL: Convert::FromBase64String into
     Data.DataEntry::ByteArrays).  `spawn_object` needs no player: it is the
     same headless path ValheimRcon's `spawn` uses.

So: synthesise TCData offline, drop it in a data entry, and have the server
spawn a `_TerrainCompiler` already carrying it.  No byte-level save surgery, no
stop/start, and the zone does not need to be loaded.

HEIGHTS.  The delta is applied to the generated height, so the generated height
has to be known per sample.  That comes from PatchScan
(tools/jumpstart/blueprints/run_patchscan.sh), requested at half=32.5 step=1
centred on each zone centre, which lands its 65x65 lattice exactly on that
zone's TerrainComp samples.

Usage:
    flatten.py plan   --world Ulfsland --preset pre-eikthyr --id meadows-starter-hall
    flatten.py apply  --world Ulfsland --preset pre-eikthyr --id meadows-starter-hall
                      --actor <you>    # one ledgered terrain_write per --id
    flatten.py selftest    # PAD_EXTENT_DEPENDS_ON and INSTALLATION_WRITE_PATH_DEPENDS_ON,
                           # both neutered on in-memory geometry and an in-memory world
"""

from __future__ import annotations

import argparse
import math
import re
import os
import subprocess
import sys
import time
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.append(str(HERE.parent))

import tcdata  # noqa: E402
import heights as patchheights  # noqa: E402
import locations  # noqa: E402
import worldseed  # noqa: E402
import rcon  # noqa: E402
from rcon import Rcon  # noqa: E402
from console import run_console  # noqa: E402

JUMPSTART = HERE.parent
REPO = JUMPSTART.parent.parent
# THE INSTALL THIS TOOL WRITES INTO IS NOT A CONSTANT, AND MAKING IT ONE COST
# A LIVE WORLD ITS DATA DIRECTORY.  MEASURED 2026-09-23 by BuildOrch, driving
# the stage-12 entry point against the fresh seed Fresh42: `flatten.py plan`
# -- an op that is supposed to touch nothing -- wrote THIRTEEN Fresh42
# terrain-delta entry files into
# /media/big4/.../Ulfsland/config_merged/bepinex/data, the WATCHED directory of
# a different world, in 101 seconds.  Nothing was destroyed only because none
# of the 13 placement ids collides with any of the 438 entry files Ulfsland's
# build actually used -- which is luck, not a guard.
#
# It is this project's own defect class, one level below where it was last
# found: A KEY THAT NAMES A SUPERSET OF WHAT THE QUANTITY ACTUALLY DEPENDS ON.
# The data directory depends on WHICH WORLD IS BEING FLATTENED; a module
# constant names one slot for every world.  `terraform/rcon.py` already keys
# exactly this path on the world (rcon.py:106-113), so the root and the world
# come from there rather than from a second literal.
def _install(world: str | None = None) -> Path:
    return rcon.WORLD_ROOT / (world or worldseed.world())


# AND THE GAME BINARIES ARE NOT WORLD-SCOPED, WHICH IS THE SAME CLASS IN THE
# OTHER DIRECTION.  `VH_SRC` is the BepInEx game install PatchScan runs out of;
# it depends on the GAME BUILD, not on the world.  MEASURED 2026-09-23 while
# landing the fix above: /media/big4/.../Fresh42 holds config_merged,
# provision.sh and a freeze and NO `data/` at all, so keying this on the world
# would name a SUBSET and refuse every fresh seed at the patch scan. It stays a
# host measurement, exactly as pipeline.py:73 already declares it.
VH_SRC = Path("/media/big4/projects/game/valheim/Ulfsland/data/bepinex")

# The ONE name for "where the running server watches for WEC data entries".
# Named once here because `ledger/replay.py` reads the same variable for the
# same files on the replay path.
DATA_DIR_ENV = "WEC_DATA_DIR"


def data_dir(world: str | None = None) -> Path:
    """The WATCHED WEC data directory of the world being flattened.

    ServerDevcommands.Yaml::SetupWatcher reloads this directory live, so a file
    dropped here is a live definition in THAT world and nothing else's.

    AND "THAT WORLD" IS SERVED BY AN INSTALL, WHICH IS THE HALF THE WORLD KEY
    DOES NOT NAME.  The fix above moved this path off a module constant and on
    to the world, which stopped Fresh42's entries landing in Ulfsland's
    directory -- and it still named the wrong directory, one layer down.
    MEASURED 2026-09-23 driving stage 12 on Fresh42: all 18 of
    `flatten.py apply`'s `spawn_object _TerrainCompiler ... data=<entry>`
    commands came back `Error: Can't find data entry: <entry>` -- 18 of 18,
    zero landed, rc 0 -- because Fresh42's server is a sandbox install at
    /tmp/cr/vh whose ServerDevcommands watches ITS OWN
    `<BepInEx>/config/data`, while these files were written to
    `<world>/config_merged/bepinex/data`. Ulfsland's container mounts
    `config_merged/bepinex` AS its BepInEx directory, so there the two
    coincide; for any server that is not that container they do not.

    So the directory depends on WHICH INSTALL SERVES THIS WORLD, and
    `$WEC_DATA_DIR` names it -- the same shape as `$VALHEIM_RCON_HOST` and
    `$VALHEIM_SERVER_LOG`, which named the socket and the console of exactly
    this server for exactly this reason.

    THE NAME IS `WEC_DATA_DIR` BECAUSE THIS TREE ALREADY HAD IT.
    `ledger/replay.py::_materialise_data_entries` -- the OTHER writer of these
    same YAML files, on the replay path -- has read `$WEC_DATA_DIR` all along.
    A second variable for one quantity is two things to keep true and a
    guaranteed disagreement the first time only one is set, so that one is
    adopted here and `_materialise_data_entries` now calls THIS function
    instead of re-deriving the path.

    THE OVERRIDE IS SCOPED TO THE WORLD THIS PROCESS IS DRIVING, and that is
    not decoration: an install serves ONE world at a time, so an env var that
    answered for every `world` argument would make `data_dir("Ulfsland")`
    return Fresh42's sandbox while Fresh42 is being built -- the original
    cross-world write, restored by the fix for it. `buildstage.world_scope_
    defects` asks this resolver for two different worlds and requires two
    different answers; with the guard it still gets them.
    """
    env = os.environ.get(DATA_DIR_ENV, "")
    if env and (world or worldseed.world()) == worldseed.world():
        return Path(env)
    return _install(world) / "config_merged" / "bepinex" / "data"


def declared_data_dirs() -> dict[str, str]:
    """`{world: path}` for every data directory an OPERATOR named explicitly.

    The attribution `buildstage.world_scope_defects` needs when a path cannot
    carry the world in its text: `$WEC_DATA_DIR` is read together with
    `$VH_WORLD` and is a declaration about THAT world and no other.
    """
    env = os.environ.get(DATA_DIR_ENV, "")
    return {worldseed.world(): env} if env else {}


SCRATCH = Path("/tmp/terraform")
# The per-type location dump (tools/seedscan/LocScan.cs): name, position and the
# ZoneLocation's own exteriorRadius / interiorRadius / quantity / prioritized /
# centerFirst.  MOD-FREE by construction -- run_locscan.sh excludes BepInEx --
# so it cannot see More_World_Locations' POIs, which is exactly why
# locations.py cross-reads it against LIVE markers instead of trusting it.
#
# IT IS THIS SEED'S DUMP, asked at CALL time of `roads/poi.py::location_dump`
# (the one resolver, keyed on the seed, header-checked).  It was Pirate68's
# file by its content hash for every seed: MEASURED, every Fresh42 drive-2
# installation pad (e.g. seq 564) records that dump in its location_check.
# `loc2` is searched FIRST so the default seed resolves to the very file this
# stage always read -- the two Pirate68 dumps hold the same 12,301 locations
# and only `loc3` adds `locationTypes`, which this gate does not read -- and
# the record's `dump` stays byte-identical on Pirate68.
LOCATION_ROOT_FIRST = Path("/tmp/settle/loc2")


def location_dump_path(seed_: str | None = None) -> Path:
    """The generator location dump for `seed_` (default the run's)."""
    roads = str(JUMPSTART / "roads")
    if roads not in sys.path:
        sys.path.append(roads)       # appended: roads/clear.py must not shadow
    import poi                                             # noqa: PLC0415
    roots = (LOCATION_ROOT_FIRST,) + tuple(
        r for r in poi.DUMP_ROOTS if r != LOCATION_ROOT_FIRST)
    return poi.location_dump(seed_, roots=roots)


def placements_path(world: str, preset: str, override: str | None) -> Path:
    """Where the placement records live.  `override` exists so an ORDERING
    experiment can be run against a throwaway pad without inventing a preset
    under worlds/, which would read as a thirteenth installation."""
    if override:
        return Path(override)
    return JUMPSTART / "worlds" / world / preset / "placements.yaml"


def load_placements_file(path, world: str | None = None) -> dict:
    """THE reader for `placements.yaml`, header included.

    Seven readers reach this file across the tree (verify_site.py,
    recost_pad.py, relocate_pad.py, remove_placement.py, write_datum_blocks.py,
    terraform/sites.py, clearing/area.py) and until now ZERO of them read its
    header, although every one of the 9 files under
    `worlds/Ulfsland/*/placements.yaml` declares `world:` and `seed:` in its
    own first four lines.  They all trusted the PATH.

    CLASSIFY THIS HONESTLY, it is NOT the location-dump defect.  The dump sat
    at a fixed `/tmp` path that cannot vary with `--world`, so a Fresh42 run
    demonstrably read Pirate68's file -- 12,301 locations, MEASURED.  Here the
    path usually contains the world name, so path and header AGREE today and no
    such read has been observed.  What is real is narrower and is exactly what
    item 5 opens up: `--world` was the literal string `"Ulfsland"` in every one
    of those readers, and `--placements <path>` -- which `flatten.py` and
    `clearing/clear.py` both take, and which carries NO world name at all --
    hands this stage another world's absolute coordinates under whatever
    `--world` name the caller chose.  UNEXERCISED, not wrong; and a record that
    states its own scope should be made to state it out loud before a second
    world exists, not after.

    PATH-LEVEL ON PURPOSE.  `clearing/area.py` only ever holds a path, and a
    reader that could only be called with (world, preset) would have left the
    one caller that takes an arbitrary file outside the check -- which is the
    caller that needs it most.

    So the header is read here, once, and refused here, once.
    """
    path = Path(path)
    if not path.is_file():
        # NAMED, not a traceback.  With `--world` live, the commonest way to
        # reach this is a world that has no records yet, and "FileNotFoundError"
        # does not say which world was asked for or where it was looked for.
        raise SystemExit(
            f"REFUSING: no placement set at {path}"
            + (f" for world {world!r}" if world else "")
            + ". Solve that world's placements first "
              "(blueprints/solve_placements.py); there is nothing here to "
              "read, and a stage that carried on would be planning against "
              "records it does not have.")
    doc = yaml.safe_load(path.read_text())
    worldseed.require_scope("placement set", path,
                            found_seed=doc.get("seed"),
                            found_world=doc.get("world"),
                            world_=world,
                            produced_by="blueprints/solve_placements.py")
    return doc


def load_placements(world: str, preset: str, override: str | None = None) -> dict:
    """`load_placements_file` for a caller that has a world and a preset."""
    return load_placements_file(placements_path(world, preset, override), world)


def placement(world: str, preset: str, pid: str, override: str | None = None) -> dict:
    doc = load_placements(world, preset, override)
    for p in doc["placements"]:
        if p["id"] == pid:
            return p
    raise SystemExit(f"no placement {pid} in "
                     f"{placements_path(world, preset, override)}")


# ---------------------------------------------------------------------------
# A DECLARED DUPLICATE IS ITS CANONICAL'S GROUND, AND ONE WRITE LEVELS IT.
#
# `duplicate_of: <preset>#<id>` says a placement is the SAME physical
# structure as another at the same solved centre (`terraform/sites.py` builds
# one body for the pair and refuses a pair whose body or centre differs).
# MEASURED on Fresh42 drive 2: deepnorth-sandbox#sandbox-harbour
# (`duplicate_of: pre-elder#early-dock`) was flattened at ledger seq 565 and
# early-dock flattened the identical 42.8 x 36.8 m pad at (-7.5, -416.5) and
# the same target 26.98 at seq 568 -- two terrain writes, one pad.  Clearing
# and stocking already skipped it, each with its own copy of the test; this
# is the ONE predicate the three stages ask (`clearing/clear.py`,
# `terraform/stock.py`, `flatten.py main`).
#
# DUPLICATE_DEPENDS_ON = ("declared_duplicate",): `_DUPLICATE_MODE`
# "ignored" is the shipped reading -- every placement is its own site -- and
# exists for the neuter in this module's selftest.
# ---------------------------------------------------------------------------
DUPLICATE_DEPENDS_ON = ("declared_duplicate",)
_DUPLICATE_MODE = "honoured"


def declared_duplicate(place: dict) -> str | None:
    """The canonical placement (`<preset>#<id>`) `place` declares itself a
    duplicate of, or None when it is a site of its own.  A duplicate is not
    cleared, levelled or stocked: its canonical's run does all three."""
    if _DUPLICATE_MODE != "honoured":
        return None
    return place.get("duplicate_of") or None


def pads_to_write(places: list[dict]) -> tuple[list[dict], list[dict]]:
    """`(to_write, skipped)`: the placements a `flatten.py` run levels, and
    each declared duplicate it does not, with the reason."""
    write, skipped = [], []
    for place in places:
        canon = declared_duplicate(place)
        if canon:
            skipped.append({"id": place.get("id"), "duplicate_of": canon,
                            "why": f"declares duplicate_of {canon}, whose own "
                                   f"run levels this same pad"})
        else:
            write.append(place)
    return write, skipped


def pad_extent(place: dict) -> tuple[float, float]:
    """The square pad the solver costed.  `footprint_orientation` in the solved
    block names it as '<w> m along x by <d> m along z'; that is the authority,
    because the flatten bill was computed over exactly that rectangle."""
    solved = place["solved"]
    text = solved.get("footprint_orientation", "")
    parts = text.replace("m along x by", "|").replace("m along z", "").split("|")
    if len(parts) == 2:
        try:
            return float(parts[0].strip()), float(parts[1].strip())
        except ValueError:
            pass
    fp = place.get("footprint_xzy_m") or place.get("footprint_xz_m")
    return float(fp[0]), float(fp[1])


def zones_for(cx: float, cz: float, w: float, d: float) -> list[tuple[int, int]]:
    """Every zone whose 65x65 sample lattice touches the pad, INCLUDING the zone
    on the far side of a shared edge.

    A zone's lattice is 65 vertices over 64 m, so its span is the CLOSED
    interval [64*zi - 32, 64*zi + 32] and the two end vertices are shared with
    the neighbours: the vertex at z = 32.0 is row 0 of zone 1 AND row 64 of
    zone 0.  Either heightmap may be the one `Heightmap.GetHeight` resolves the
    query into, and each zone renders its own mesh, so a shared vertex written
    in only one of them is a seam: the delta is there when the query lands in
    the written zone and gone when it lands in the other.

    THE DEFECT WAS HERE.  This took `zone_of` at the four pad corners, and
    `zone_of` = floor((c + 32) / 64) returns exactly ONE zone for a boundary
    coordinate - the one the vertex is row 0 of - so the far side was never
    stamped.  The docstring already said "edge samples belong to two zones";
    the code did not.

    MEASURED on Ulfsland: of 63 site_pad records, 3 left a boundary-sharing
    zone unwritten, and for ONE of them the unwritten edge is inside the pad's
    own rectangle.  wt-south is a 20 x 20 pad centred (536, 42), so its south
    edge is z = 32.0 = 64*1 - 32 exactly.  It declared zones (8,1) and (9,1),
    wrote the z = 32 row as row 0 of those two, and the applied surface read
    back from the save still has the NATURAL height along the whole 21-sample
    row: 81.206 to 82.913 against a target of 82.660, worst 1.454 m.  Every
    other one of its 420 in-rectangle samples reads 82.660 exactly.  So the
    engine answered that row from zone (8,0)/(9,0) row 64, which nothing wrote.

    The rule is now stated over the samples instead of the corners: zone zi is
    needed iff its closed span intersects the pad's own extent, i.e.
    ceil((v0 - 32)/64) <= zi <= floor((v1 + 32)/64).  For a pad with no edge on
    a boundary this returns exactly what the corner test did; it only ever ADDS
    the neighbour whose lattice shares a written vertex.  `build` still stamps
    only samples inside the rectangle, so the added zone receives exactly the
    shared row or column and nothing else, and contributes no entry when it
    receives none.
    """
    x0, x1 = cx - w / 2, cx + w / 2
    z0, z1 = cz - d / 2, cz + d / 2
    zx0 = math.ceil((x0 - 32.0) / tcdata.ZONE_SIZE)
    zx1 = math.floor((x1 + 32.0) / tcdata.ZONE_SIZE)
    zz0 = math.ceil((z0 - 32.0) / tcdata.ZONE_SIZE)
    zz1 = math.floor((z1 + 32.0) / tcdata.ZONE_SIZE)
    return [(zx, zz) for zx in range(zx0, zx1 + 1)
            for zz in range(zz0, zz1 + 1)]


# THE EDGE OF THE RECTANGLE IS INSIDE IT, AND `>` ON BINARY FLOATS IS NOT THE
# TEST THAT SAYS SO.  A solved centre is a decimal - 886.6, 546.8, -885.2 -
# and none of them is representable, so the distance from the centre to a
# lattice sample that sits EXACTLY on the rectangle's edge comes out a few
# ulps too large: 886.6 - 879.0 = 7.6000000000000085 against a half_d of 7.6,
# and the whole edge row is dropped.
#
# MEASURED over the 63 site_pad records on this chain, by differencing each
# record's own blobs against the previous state of the same zones: four
# FIRST writes lost exactly one full edge row or column this way and nothing
# else - stenvik-house-1 z=879 (20 samples), stenvik-cottage-3 x=552 (12),
# stenvik-hut-1 z=892 (10), vestvik-villa-2 x=-876 (17).  59 lattice samples
# inside a DECLARED pad rectangle that nothing ever wrote, left at their
# generated height under a building.
#
# They are also INVISIBLE to the acceptance gate: `pad_flatness` measures the
# samples a pad wrote and still owns, and a sample nobody wrote is in
# nobody's population.  That is why this is a tolerance and not a re-ordering
# of the comparison: the fix has to be in the writer.
#
# EPS is a quarter of a sample, which is far below the 1 m lattice pitch - it
# cannot admit a sample that is not on the rectangle - and far above the
# ~1e-13 m float error it exists to absorb.  `site_finder.WRITE_EDGE_EPS_M`
# restates it and `settlements/plan.py::certify_plan` certifies the two agree.
EDGE_EPS_M = 0.25


def stamps(offset: float, half: float) -> bool:
    """THE writer's per-axis test: `build` stamps a lattice sample iff this
    holds on both axes, for its offset from the pad centre and the pad's
    half-extent (apron included).  One predicate, so a question about which
    ground a pad levels is asked of the writer and not of a restatement."""
    return abs(offset) <= half + EDGE_EPS_M


def answering_vertices(lo: float, hi: float) -> range:
    """Every lattice index whose vertex the engine answers a ground query
    anywhere in [lo, hi] (world metres, one axis) with.

    `Heightmap.GetHeight` is a step function on the 1 m lattice, not a
    surface: a query reads its nearest vertex, and `accept/instruments.snap`
    fixes the half-way case UP.  So the ground a body stands on over [lo, hi]
    is exactly these vertices - the ones a pad has to stamp."""
    s = tcdata.SCALE
    return range(math.floor(lo / s + 0.5), math.floor(hi / s + 0.5) + 1)


# ===========================================================================
# PAD_EXTENT_DEPENDS_ON -- THE BODY'S FOOTPRINT AT THE YAW IT STANDS AT, OVER
# THE LATTICE VERTICES THE ENGINE ANSWERS IT WITH.  NOT THE FOOTPRINT OF THE
# BODY UNTURNED.
#
#   declared_pad     the rectangle the siting solver declared and billed.  A
#                    FLOOR, never shrunk: an outlier's `pad_m` is a siting
#                    decision, not a function of its body.
#   body_footprint   the body's declared footprint plus the declared margin
#                    (`settlements/bodies.PAD_MARGIN_M` on the settlement
#                    path) ...
#   body_yaw         ... taken AT THE YAW THE BODY IS PLACED AT, by the
#                    emitter's own measure: `base_geometry.xz_footprint` is
#                    the rectangle `--align floor-center` centres on the
#                    placement point.  The declared footprint grows by exactly
#                    what that measure grows when the body is turned, so a
#                    body at yaw 0 gets its declared pad back, byte for byte.
#   written_samples  ... and completed on the lattice: every vertex the
#                    engine answers a point of that rectangle with
#                    (`answering_vertices`) is one `stamps` admits.  At a 3 m
#                    margin this term is implied; at the preset path's zero
#                    margin it is the whole guarantee.
#
# THE PAD WAS KEYED ON THE BODY UNTURNED AND WRITTEN UNDER THE BODY TURNED.
# `settlements/layout.py` sizes a town pad as the yaw-0 footprint + 2 x 3 m,
# and this writer stamps it axis-aligned; the body is then placed at the
# street's bearing.  MEASURED 2026-09-24 with every body placed by
# `fixtures.place_body` at its unit's yaw, piece-exact, against the rectangle
# `build` stamps:
#   * plan.Fresh42.json, 49 units, 31 at a non-cardinal yaw: ONE body stands
#     partly off its pad - vestvik-hall-1 at yaw 50, 17.88 m across at its
#     yaw on a 14.4 m pad: 4.68 % of its footprint off the pad, worst corner
#     1.740 m out, 13 of the 172 vertices under it never stamped, the lowest
#     overhanging solid 0.297 m BELOW the datum (a ground course, not eaves);
#   * Ulfsland plan.json, 50 units, 36 non-cardinal: THREE, all WRITTEN and
#     standing - vestvik-hall-1 yaw 70 (8.53 %, 1.920 m, 19 of 169 vertices),
#     stenvik-longhouse-1 and -2 yaw 30 (0.46 %, 0.482 m, 4 and 6 of 169/170)
#     - on the 14.4 x 23.0 m pads ledger seqs 425, 97 and 104 wrote.  The
#     gate passed all three: `pad_coverage` asks about the samples of the
#     pad's own rectangle and `pad_flatness` about the samples it wrote, and
#     a vertex under a body but outside its pad is in neither population.
# stenvik-hall-1 is NOT one of them.  Its DECLARED 20.1 m square turned 30
# deg needs a 27.46 m box, but the body is not that square: measured at yaw
# 30 it spans 20.21 x 18.77 m and its 26.1 m pad stamps every vertex under
# it.  A rotated declared rectangle over-states the body; the emitter's own
# measure at the yaw is what the body occupies.
#
# THE POPULATION IS EVERY EMITTER, AND IT WAS FIXED FOR ONE OF SIX.  One key,
# six tools that decide the w x d a writer stamps under a body, and on
# 2026-09-24 only `settlements/build.py::written_pad` asked this rule.  The
# installation solver kept writing the rectangle its search ASSUMED, axis-
# aligned: MEASURED 2026-09-25, the live Fresh42 build's step 2 refused 8 of
# 13 installations on it (mountain-outpost, searched on the swapped 30.8 x
# 20.3 m rectangle and standing at yaw 180 across 21.7 x 33.5 m: 308 of 748
# vertices unstamped, 6.588 m); read-only, Ulfsland's own set holds the same
# 8 of 13 (sandbox-harbour 532 of 1672, 7.348 m).  Every emitter now asks
# this rule and none restates it:
#   settlements/build.py::written_pad          town and outlier units
#   blueprints/solve_placements.py::written_pad installations (stage 7), via
#   network/recost_pad.py, relocate_pad.py      `placed_pad_at_yaw` (below)
#   settlements/harbour_city.py::house_seat    harbour terraces (was a hand-
#                                              rotated AABB whose pivot turned
#                                              the wrong way: 8 of 11 Ulfsland
#                                              harbour-B bodies off their pads)
#   settlements/portal_hall.py::hall_pad       the portal hall (was a literal)
# The emitter-level neuters are in `solve_placements.py --selftest`.
#
#   fixture_ring     ... and every FIXTURE the site will seat.  `stock.py`
#                    stands its stations, props, chests and portals, and the
#                    hub its arches and their boards (`network/waypoints.py`),
#                    at the PAD height; they are not the body, so the terms
#                    above never saw them.  The ring is `stock.fixture_ring`:
#                    the solve step 6 runs, over the site square, as box
#                    offsets from the centre (`{"x": [lo, hi], "z": [lo, hi]}`),
#                    which do not depend on where the site is.  Completed on
#                    the lattice like the body (`ring_extent`).
#
# THE PAD WAS SIZED FOR THE BODY AND THE FIXTURES STOOD AROUND IT.  MEASURED
# on Fresh42 drive 2 against the drive-2 applied ground (PatchScan heightmap +
# every ledger terrain_write): at pre-queen#mistlands-blackforge-base, 28 of
# 32 fixtures stand off its 18.8 x 12.8 m pad at the pad height 40.57, from
# 2.95 m in the air (the forge row, ground 37.62) to 4.71 m under the bank
# (the cartography table, seq 1978: its top 1.98 m below ground); in the
# portal hall's offline solve, 13 of 16 settlement arches and 2 of 11
# installation arches stand off its 34.8 m pad, u-lhwest's 3.29 m arch under
# 3.46 m of hillside.  Stock searched the SITE square (`footprint_m` / 2)
# while the writer levelled the body's rectangle.
PAD_EXTENT_DEPENDS_ON = ("declared_pad", "body_footprint", "body_yaw",
                         "written_samples", "fixture_ring")
# A derived pad is written through `footprint_orientation` (`:g`) and read
# back by `pad_extent`, so it is carried at the footprint's own 0.1 m
# resolution, rounded UP - never below what the body needs.
PAD_EXTENT_STEP_M = 0.1
# The emitter's centring modes: the body's at-yaw rectangle is centred on the
# placement point, which is the geometry `body_cover` judges.  Any other
# alignment puts the body somewhere this module does not model.
CENTRED_ALIGNMENTS = ("floor-center", "center", "ground-center")


def _step_up(v: float) -> float:
    k = round(1.0 / PAD_EXTENT_STEP_M)
    return math.ceil(round(v * k, 6)) / k


def lattice_extent(c: float, extent: float, apron_m: float = 0.0) -> float:
    """The least pad width, centred on `c`, whose stamp admits every vertex
    the engine answers a body `extent` metres wide (centred on `c`) with -
    with half the edge tolerance to spare, so float error in a decimal
    centre cannot drop the outermost vertex."""
    vs = answering_vertices(c - extent / 2, c + extent / 2)
    reach = max(c - vs[0] * tcdata.SCALE, vs[-1] * tcdata.SCALE - c)
    return 2 * (reach - EDGE_EPS_M / 2 - apron_m)


def ring_extent(c: float, lo: float, hi: float, apron_m: float = 0.0) -> float:
    """The least pad width, centred on `c`, whose stamp admits every vertex
    the engine answers ground over [c + lo, c + hi] with: `lattice_extent`
    for a span that is NOT centred on the pad, because a fixture ring is
    wherever the fixtures went (the apron row of a body facing south is all
    on the south side)."""
    vs = answering_vertices(c + lo, c + hi)
    reach = max(c - vs[0] * tcdata.SCALE, vs[-1] * tcdata.SCALE - c)
    return 2 * (reach - EDGE_EPS_M / 2 - apron_m)


def pad_extent_at_yaw(declared, footprint, extents: dict, centre,
                      margin_m: float, apron_m: float = 0.0,
                      depends_on: tuple = PAD_EXTENT_DEPENDS_ON,
                      ring: dict | None = None) -> dict:
    """THE pad a body needs, from the body's footprint at the yaw it stands
    at.  See PAD_EXTENT_DEPENDS_ON.

    `declared` is the siting solver's (w, d); `footprint` the body's declared
    unturned (w, d); `extents` is `body_extents`' measurement of the same
    body - `unturned` and `at_yaw` - and `centre` the placement point the
    emitter centres the body on.  Returns the pad `w` x `d` and the terms,
    and hands a declared number back UNTOUCHED wherever the body needs no
    more, which is what keeps a yaw-0 pad byte-identical."""
    w, d = declared[0], declared[1]
    out = {"declared": [declared[0], declared[1]], "margin_m": margin_m,
           "depends_on": list(depends_on)}
    if "body_footprint" not in depends_on:
        return {**out, "w": w, "d": d, "grew": False}
    (w0, d0) = extents["unturned"]
    (wy, dy) = (extents["at_yaw"] if "body_yaw" in depends_on
                else extents["unturned"])
    want = [float(footprint[0]) + (wy - w0) + 2 * margin_m,
            float(footprint[1]) + (dy - d0) + 2 * margin_m]
    if "written_samples" in depends_on:
        want[0] = max(want[0], lattice_extent(float(centre[0]), wy, apron_m))
        want[1] = max(want[1], lattice_extent(float(centre[1]), dy, apron_m))
    ring = ring if ring and "x" in ring and not ring.get("refused") else None
    if "fixture_ring" in depends_on and ring:
        want[0] = max(want[0], ring_extent(float(centre[0]), *ring["x"],
                                           apron_m=apron_m))
        want[1] = max(want[1], ring_extent(float(centre[1]), *ring["z"],
                                           apron_m=apron_m))
    if "declared_pad" in depends_on:
        w = declared[0] if want[0] <= declared[0] else _step_up(want[0])
        d = declared[1] if want[1] <= declared[1] else _step_up(want[1])
    else:
        w, d = _step_up(want[0]), _step_up(want[1])
    return {**out, "w": w, "d": d,
            "grew": (w, d) != (declared[0], declared[1]),
            "body_at_yaw": [round(wy, 3), round(dy, 3)],
            "wanted": [round(want[0], 3), round(want[1], 3)],
            **({"fixture_ring": {k: ring[k] for k in ("x", "z", "fixtures")
                                 if k in ring}}
               if "fixture_ring" in depends_on and ring else {})}


def body_cover(centre, pad_w: float, pad_d: float, body_w: float,
               body_d: float, apron_m: float = 0.0) -> dict:
    """A VERDICT: does a `pad_w` x `pad_d` pad centred on `centre` stamp every
    vertex the engine answers the body's at-yaw rectangle (`body_w` x
    `body_d`, centred on the same point, as floor-center places it) with?

    The rectangle is the body's per-axis TIGHT extent, so a refusal here is a
    piece standing on a vertex nothing levelled; the unstamped COUNT is over
    the rectangle and is an upper bound on the vertices pieces touch."""
    cx, cz = float(centre[0]), float(centre[1])
    hw, hd = pad_w / 2 + apron_m, pad_d / 2 + apron_m
    xs = answering_vertices(cx - body_w / 2, cx + body_w / 2)
    zs = answering_vertices(cz - body_d / 2, cz + body_d / 2)
    sx = sum(1 for v in xs if stamps(v * tcdata.SCALE - cx, hw))
    sz = sum(1 for v in zs if stamps(v * tcdata.SCALE - cz, hd))
    under = len(xs) * len(zs)
    ox, oz = max(body_w / 2 - hw, 0.0), max(body_d / 2 - hd, 0.0)
    # NONE MAY NEVER STAND FOR AN ANSWER: a body with no vertex under it is
    # an extent nobody measured, and zero unstamped of zero is not a cover.
    verdict = ("empty" if under == 0
               else "covered" if sx * sz == under else "overhangs")
    return {"verdict": verdict,
            "vertices_under_body": under, "unstamped": under - sx * sz,
            "overhang_x_m": round(ox, 3), "overhang_z_m": round(oz, 3),
            "corner_overhang_m": round(math.hypot(ox, oz), 3),
            "pad": [pad_w, pad_d],
            "body_at_yaw": [round(body_w, 3), round(body_d, 3)]}


def _blueprints_on_path() -> None:
    bp = str(JUMPSTART / "blueprints")
    if bp not in sys.path:
        sys.path.append(bp)


def body_extents(objects, yaw_deg: float, geom=None) -> dict:
    """The body's XZ footprint (width x, depth z), unturned and at `yaw_deg`,
    by the emitter's own measure: `base_geometry.xz_footprint`, collider
    corners, the rectangle `--align floor-center` centres on the placement
    point.  `geom` lets a fixture hand in geometry built in memory."""
    _blueprints_on_path()
    import base_geometry
    f0 = base_geometry.xz_footprint(objects, 0.0, geom)
    fy = base_geometry.xz_footprint(objects, yaw_deg, geom)
    return {"unturned": (f0.max_x - f0.min_x, f0.max_z - f0.min_z),
            "at_yaw": (fy.max_x - fy.min_x, fy.max_z - fy.min_z),
            "yaw_deg": float(yaw_deg), "pivot_only": len(fy.pivot_only)}


_BODY_EXTENTS: dict[tuple[str, float], dict] = {}
_LIBMAT = None


def placed_body_extents(blueprint: str, yaw_deg: float) -> dict:
    """`body_extents` of a library body, resolved the way
    `settlements/build.py::body_of` resolves it.  Cached per (body, yaw);
    a body that does not resolve RAISES - there is no default footprint."""
    global _LIBMAT
    key = (str(blueprint), float(yaw_deg))
    hit = _BODY_EXTENTS.get(key)
    if hit is not None:
        return hit
    _blueprints_on_path()
    import importlib.util
    from to_rcon_plan import read_objects
    if _LIBMAT is None:
        spec = importlib.util.spec_from_file_location(
            "library_materialise", JUMPSTART / "library/materialise.py")
        _LIBMAT = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_LIBMAT)
    path = _LIBMAT.locate_by_filename(blueprint)
    if path is None:
        raise FileNotFoundError(f"no body resolves for {blueprint}")
    hit = {**body_extents(read_objects(path), yaw_deg),
           "blueprint": str(blueprint)}
    _BODY_EXTENTS[key] = hit
    return hit


# THE PRESET PATH'S MARGIN.  An installation pad levels the body at zero
# margin (`flatten.py apply` stamps at apron 0, `buildstage._av_site_ground`
# passes none), so on this path `written_samples` guarantees the body and
# `fixture_ring` everything `stock.py` stands round it.
INSTALLATION_PAD_MARGIN_M = 0.0


def recorded_ring(place: dict) -> dict | None:
    """The fixture ring stage 7 recorded for `place` (`solved.fixture_ring`),
    when the ring was levelled; None when there is none or it was refused.
    A re-cost or a relocation keeps it: the ring is box offsets from the
    centre, so it moves with the site."""
    ring = (place.get("solved") or {}).get("fixture_ring")
    return ring if ring and not ring.get("refused") else None


def placed_pad_at_yaw(place: dict, declared, centre, apron_m: float = 0.0,
                      depends_on: tuple = PAD_EXTENT_DEPENDS_ON,
                      ring: dict | None = None) -> dict:
    """`pad_extent_at_yaw` for an INSTALLATION: the pad a solver or a re-cost
    writes into `place`'s `footprint_orientation` for a body centred on
    `centre`, given the rectangle it `declared` (searched and billed).

    The body is the one `place` names, measured by `placed_body_extents` at
    the yaw `place` names; its declared unturned footprint is
    `footprint_xzy_m` (the emitter's own unturned measure where a placement
    declares none).  `ring` is the fixture ring (`stock.fixture_ring`);
    absent, the one `place` recorded (`recorded_ring`).  RAISES on anything
    it cannot measure - no yaw, an alignment that does not centre the body,
    a body that does not resolve - because a pad sized without the body is
    the defect, not a default."""
    name = place.get("blueprint")
    yaw = (place.get("rotation") or {}).get("yaw")
    align = place.get("align", "floor-center")
    if not name:
        raise ValueError(f"{place.get('id')}: no body named")
    if yaw is None:
        raise ValueError(f"{place.get('id')}: the body's yaw is unread")
    if align not in CENTRED_ALIGNMENTS:
        raise ValueError(f"{place.get('id')}: align {align!r} does not centre "
                         f"the body on the pad")
    ext = placed_body_extents(name, float(yaw))
    fp = place.get("footprint_xzy_m") or place.get("footprint_xz_m")
    foot = (float(fp[0]), float(fp[1])) if fp else ext["unturned"]
    return {**pad_extent_at_yaw(declared, foot, ext, centre,
                                INSTALLATION_PAD_MARGIN_M, apron_m,
                                depends_on,
                                ring if ring is not None
                                else recorded_ring(place)),
            "yaw_deg": float(yaw), "blueprint": str(name)}


def placed_body_cover(place: dict, apron_m: float = 0.0) -> dict:
    """`body_cover` for the pad `build` will stamp for `place` and the body
    `place` names, at the yaw it names.  A VERDICT: anything it cannot
    measure is named and is not `covered`."""
    solved = place["solved"]
    name = place.get("blueprint")
    yaw = (place.get("rotation") or {}).get("yaw")
    align = place.get("align", "floor-center")
    base = {"id": place.get("id"), "blueprint": name, "yaw": yaw,
            "align": align}
    if not name:
        return {**base, "verdict": "no_body_named"}
    if yaw is None:
        return {**base, "verdict": "no_yaw",
                "why": "the body's yaw is unread, so the ground it stands on "
                       "is unknown; unread is not clear"}
    if align not in CENTRED_ALIGNMENTS:
        return {**base, "verdict": "unmeasured_alignment",
                "why": f"align {align!r} does not centre the body on the pad"}
    try:
        ext = placed_body_extents(name, float(yaw))
    except Exception as e:                                # noqa: BLE001
        return {**base, "verdict": "unresolved",
                "why": f"{type(e).__name__}: {e}"}
    w, d = pad_extent(place)
    bw, bd = ext["at_yaw"]
    return {**base, **body_cover((float(solved["x"]), float(solved["z"])),
                                 w, d, bw, bd, apron_m)}



# The flatten datum's surface.  NOT a knob: `worldgen` here is the defect that
# put wt-peak's watchtower in the air.
#
# MEASURED from the deployed assembly's IL (md5 47df8869abdf95abedf859fce3deb022)
# and re-measured against the live world:
#
#   WorldGenerator::GetHeight(x, z) = GetBiomeHeight(GetBiome(x, z), x, z)
#       -- the height function of THE POINT'S OWN biome.
#   HeightmapBuilder::Build         = per-vertex SmoothStep blend of
#       GetBiomeHeight over THE ZONE'S FOUR CORNER biomes, with a fast path
#       that evaluates corner b0's function over the WHOLE zone when the four
#       corners agree.
#
# `ApplyToHeightmap` adds the TCData delta to the SECOND of those, so a delta
# computed against the first only flattens where a zone holds no biome
# boundary.  On Ulfsland zone (22,24) -- four corners Meadows, 2,736 of 4,225
# interior samples Mountain -- they differ by up to 8.807 m, and wt-peak's pad
# came out as 6.59 m of slope where 0.00 was asked for.
#
# Validated in both directions against 1,186 worldgen-placed trees and rocks
# standing on unwritten ground, whose ZDO y IS their ground contact point:
# residual against the heightmap surface median -0.001 m with 8 of 1,186 over
# 1 m; against GetHeight, median -0.001 m but 43 over 1 m and a -10.516 m tail.
# The median agrees because most of the world has no boundary in its zone; the
# TAIL is the whole defect.
DATUM_SURFACE = "heightmap"


def run_patchscan(zones: list[tuple[int, int]], seed: str, out: Path,
                  surface: str = DATUM_SURFACE) -> dict[str, patchheights.Patch]:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    req = SCRATCH / "patch_req.tsv"
    lines = []
    for zx, zz in zones:
        cx, cz = tcdata.zone_centre(zx, zz)
        lines.append(f"z_{zx}_{zz}\t{cx:g}\t{cz:g}\t32.5\t1")
    req.write_text("\n".join(lines) + "\n")
    # SANDBOX is OVERRIDABLE, and this is MEASURED rather than tidiness:
    # `run_patchscan.sh` begins with `rm -rf "$SANDBOX/BepInEx"`, so if an
    # earlier run staged that tree as ROOT the script cannot clear it and every
    # later caller gets `patchscan produced nothing` explained only by
    # `Permission denied`. That is exactly how lh-north was refused AFTER its
    # clearing had already run. An agent without root cannot repair the shared
    # path, so it must be able to use its own.
    env = {
        "VH_SRC": str(VH_SRC), "SEED": seed, "REQ": str(req), "OUT": str(out),
        "SURFACE": surface,
        "SANDBOX": __import__("os").environ.get("PATCHSCAN_SANDBOX",
                                                "/tmp/patchscan/vh"),
    }
    cmd = ["bash", str(JUMPSTART / "blueprints" / "run_patchscan.sh")]
    proc = subprocess.run(cmd, env={**dict(__import__("os").environ), **env},
                          capture_output=True, text=True)
    if not out.exists() or out.stat().st_size == 0:
        raise SystemExit(f"patchscan produced nothing:\n{proc.stderr[-2000:]}")
    return patchheights.load(out)


def build(place: dict, patches: dict[str, patchheights.Patch], zones: list[tuple[int, int]],
          paint: str, apron: float,
          comps: dict[tuple[int, int], tcdata.Compiler],
          op_y: dict[tuple[int, int], float]) -> dict:
    """Stamp one placement's pad into the shared per-zone compilers.

    Compilers are shared rather than one-per-placement because a zone holds
    exactly ONE `_TerrainCompiler`: `Heightmap::GetAndCreateTerrainCompiler`
    returns the first it finds, so a second compiler in the same zone is dead
    weight whose data is never applied.  Two pads in one zone therefore have to
    merge into one blob.
    """
    solved = place["solved"]
    cx, cz = float(solved["x"]), float(solved["z"])
    w, d = pad_extent(place)
    cost = solved.get("flatten_cost") or {}
    target = float(cost.get("target_y") or solved.get("y_centre_m") or solved["y"])
    colour = tcdata.PAINTS[paint]
    stats = {"id": place["id"], "pad_w": w, "pad_d": d, "target_y": target, "samples": 0,
             "max_cut": 0.0, "max_fill": 0.0, "over_clamp": 0, "checks": []}
    half_w, half_d = w / 2 + apron, d / 2 + apron
    # The edge tolerance and why it is 0.25 m: see `stamps`, which is THE
    # per-axis test this loop applies and the one `body_cover` asks.
    for zx, zz in zones:
        patch = patches[f"z_{zx}_{zz}"]
        comp = comps.setdefault((zx, zz), tcdata.Compiler(zone_x=zx, zone_z=zz))
        zcx, zcz = comp.centre
        touched = 0
        for y in range(tcdata.PITCH):
            for x in range(tcdata.PITCH):
                wx, wz = tcdata.sample_world(zcx, zcz, x, y)
                if not (stamps(wx - cx, half_w) and stamps(wz - cz, half_d)):
                    continue
                # Sample (x, y) of this zone is sample (i=y, j=x) of the patch: the
                # request is centred on the same point with the same step, so
                # patch j == mask x and patch i == mask y.
                generated = patch.at(y, x)
                delta = target - generated
                comp.set_height(x, y, delta)
                comp.set_paint(x, y, colour)
                touched += 1
                stats["max_cut"] = min(stats["max_cut"], delta)
                stats["max_fill"] = max(stats["max_fill"], delta)
                if abs(delta) > tcdata.CLAMP_M:
                    stats["over_clamp"] += 1
        if touched == 0:
            continue
        stats["samples"] += touched
        # The pad height this zone was levelled to, for the compiler's op
        # record.  Two pads sharing a zone is rare and their targets differ by
        # less than the grass reset cares about (it compares x and z only), so
        # the last writer wins and nothing depends on which.
        op_y[(zx, zz)] = target
        # One verification per zone: a sample at a named world position, its
        # generated height, and the height the game will end up with.
        mid_x, mid_y = tcdata.vertex_mask_index(zcx, zcz, cx, cz)
        if 0 <= mid_x < tcdata.PITCH and 0 <= mid_y < tcdata.PITCH:
            i = mid_y * tcdata.PITCH + mid_x
            stats["checks"].append({
                "zone": [zx, zz],
                "sample": [mid_x, mid_y],
                "world": list(tcdata.sample_world(zcx, zcz, mid_x, mid_y)),
                "generated_m": round(patch.at(mid_y, mid_x), 3),
                "level_delta_m": round(comp.level_delta[i], 3),
                "result_m": round(patch.at(mid_y, mid_x) + comp.level_delta[i], 3),
            })
    stats["max_cut"] = round(stats["max_cut"], 3)
    stats["max_fill"] = round(stats["max_fill"], 3)
    return stats


def write_entries(name: str, comps: dict[tuple[int, int], tcdata.Compiler],
                  op_y: dict[tuple[int, int], float], *,
                  world: str | None = None,
                  watched: bool = True) -> tuple[Path, list[dict]]:
    """One WEC data entry per zone.  The entry name carries the zone because
    each zone gets different bytes, and the file is one YAML list so the whole
    run reloads in a single watcher event.

    `op_y` is the pad height stamped into each zone's `m_lastOpPoint`.  See
    tcdata's header: that record and `m_operations` decide which grass a
    client's `ClutterSystem` throws away when it loads the compiler, and a blob
    that gets them wrong leaves the pad's grass floating at its old height.
    The self-check reports them so a wrong one is visible in the run output
    rather than only in the operator's screenshot.
    """
    import base64

    entries = []
    doc = []
    for (zx, zz), comp in sorted(comps.items()):
        blob = comp.blob(op_y.get((zx, zz), 0.0))
        entry = f"{name}_z{zx}_{zz}".replace("-", "_")
        doc.append({"name": entry, "bytes": ["TCData, " + base64.b64encode(blob).decode()]})
        cx, cz = comp.centre
        entries.append({"entry": entry, "zone": [zx, zz], "centre": [cx, cz],
                        "blob_bytes": len(blob), "counts": comp.counts(),
                        "selfcheck": {k: v for k, v in tcdata.parse(blob).items()
                                      if k in ("version", "samples", "plain_bytes", "blob_bytes",
                                               "operations", "last_op_point", "last_op_radius")}})
    # `watched=False` is the PLAN path: the entries are still built and still
    # digested, so the plan reports the same bytes it would write, but they go
    # to scratch. A plan that drops a live definition into a watched directory
    # is not a plan -- MEASURED, `flatten.py plan` wrote 13 of them.
    out_dir = data_dir(world) if watched else SCRATCH / "entries"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"terraform_{name}.yaml".replace("-", "_")
    path.write_text(yaml.safe_dump(doc, default_flow_style=False, width=10**9, sort_keys=False))
    return path, entries


ZONE_CTRL = "_ZoneCtrl"
TOTAL_RE = re.compile(r"^Total:?\s*(\d+)\s*$", re.M)


def ungenerated_zones(rc: Rcon, zones: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Which of `zones` the world has never generated.

    THIS IS A PRECONDITION, NOT A DIAGNOSTIC, and it is the measured fix for
    the floating-vegetation defect.  MEASURED on Ulfsland at zone (-18, 28) on
    2026-09-15: a compiler was spawned into an ungenerated zone, the zone was
    then generated, and all 190 objects it planted landed on the UNMODIFIED
    generated height -- median offset from the patchscan height 0.000 m, worst
    0.42 m -- which stood them up to 7.68 m ABOVE the flattened pad, as real
    ZDOs, permanently.  The paint's vegetation-cleared alpha was ignored too:
    Bush01, RaspberryBush, Beech1 and eleven more prefabs were placed on ground
    painted alpha 0.

    The cause, from IL: `Heightmap::Generate` ends by calling `ApplyModifiers`,
    which locates its compiler through `TerrainComp::FindTerrainCompiler` -- a
    scan of the static `s_instances` list of INSTANTIATED components.  A
    dedicated server with no peers instantiates no `_TerrainCompiler`
    ZNetView, so the list is empty, no deltas and no cleared mask reach the
    heightmap, and `ZoneSystem::SpawnZone` then calls `PlaceVegetation`
    straight away -- whose `GetGroundData` is a downward `Physics.Raycast`
    against that unmodified collider.

    A `_ZoneCtrl` is planted at the zone CENTRE by `PlaceZoneCtrl` (MEASURED:
    exactly (-1152, 0, 1408) for zone (-18, 22)), and `objects_count`'s
    `pos`/`max` filter is a vertical cylinder on `Utils.DistanceXZ`, so a
    1 m probe at the zone centre is an exact test with a two-line answer.
    """
    return [z for z, p in zone_probes(rc, zones).items() if p["count"] == 0]


def zone_probes(rc: Rcon, zones: list[tuple[int, int]]) -> dict:
    """`{zone: {cmd, count, centre}}`: the `_ZoneCtrl` reading `ungenerated_zones`
    decides on, KEPT, because a terrain_write record must carry the exact
    command that observed its zone was generated (`schema.py` refuses
    `zone_generated_before` without a `zone_generated_probe` naming _ZoneCtrl).
    The same shape `settlements/build.py::zone_probe` records."""
    out: dict[tuple[int, int], dict] = {}
    for zx, zz in zones:
        cx, cz = tcdata.zone_centre(zx, zz)
        cmd = f"objects_count id={ZONE_CTRL} pos={cx:g},{cz:g} max=1"
        lines = run_console(rc, cmd, settle=3.0)
        found = TOTAL_RE.search("\n".join(lines))
        if not found:
            raise SystemExit(
                f"zone {zx},{zz}: objects_count printed no Total line, so whether the zone "
                f"is generated is unknown. Refusing to write terrain blind; console said "
                f"{lines!r}")
        out[(zx, zz)] = {"cmd": cmd, "count": int(found.group(1)),
                         "centre": [cx, cz]}
    return out


def _pad_markers(rc: Rcon, place: dict, apron: float, dump: Path):
    """The live LocationProxy markers around a pad, typed against the dump."""
    solved = place["solved"]
    cx, cz = float(solved["x"]), float(solved["z"])
    w, d = pad_extent(place)
    half_w, half_d = w / 2 + apron, d / 2 + apron
    catalogue = locations.load_dump(dump)
    probe = locations.probe_radius_m(catalogue, half_w, half_d)
    target = float((solved.get("flatten_cost") or {}).get("target_y")
                   or solved.get("y_centre_m") or solved["y"])
    # Prefab-scoped and radius-bounded: `findObjects` refuses without both, and
    # an unbounded listing is what wedges the console.
    cmd = (f"findObjects -prefab {locations.LOCATION_MARKER} "
           f"-near {cx:g} {target:g} {cz:g} {probe:.2f} -detailed")
    reply = rc.command(cmd)
    markers = locations.describe(catalogue, locations.live_markers(reply),
                                 cx, cz, half_w, half_d)
    hits = locations.violations(markers, cx, cz, half_w, half_d)
    return markers, hits, (cx, cz, half_w, half_d), cmd


def pad_location_check(rc: Rcon, place: dict, apron: float,
                       dump: Path) -> tuple[list, str]:
    """Generated locations whose stand-off this pad's rectangle reaches into.

    Separate from `clearing/area.py`'s check on purpose: that one guards the
    CLEARING cylinder and only runs when clearing runs, and MEASURED on
    2026-09-15 a flatten cut 6.65 m out from under a `LocationProxy` holding a
    `TreasureChest_meadows_buried` with nothing complaining.  A terrain write
    is destructive on its own and needs its own gate.

    Two sources, because neither is sufficient alone -- the live world sees
    modded POIs but cannot name a marker's type, and the mod-free dump names
    the type and carries its declared radii but contains no modded location.
    See locations.py for the authority ordering and for which term of the
    stand-off is measured, which is inferred, and which is taste.
    """
    markers, hits, (cx, cz, half_w, half_d), _cmd = _pad_markers(
        rc, place, apron, dump)
    return hits, locations.report(markers, hits, cx, cz, half_w, half_d)


def location_record(markers, hits, rect, dump, probe_cmd: str) -> dict:
    """`terrain_write.location_check`, in THIS gate's own words.

    The schema requires {dump, method, nearest, standoff_m, verdict} on every
    terrain write and refuses the append unless the verdict is `clear`, so an
    installation pad carries the reading `pad_location_check` refused on --
    not a restatement of it."""
    cx, cz, half_w, half_d = rect
    near = markers[0] if markers else None
    gap = (locations.rect_distance_m(near.x, near.z, cx, cz, half_w, half_d)
           if near else None)
    return {
        "dump": str(dump),
        "method": (f"MEASURED live: every {locations.LOCATION_MARKER} inside "
                   f"the probe, answered by `findObjects`, typed against the "
                   f"mod-free LocScan dump for its per-type radius "
                   f"max(exteriorRadius, interiorRadius); stand-off = radius "
                   f"+ {locations.PIECE_OVERSHOOT_M:g} m piece overshoot "
                   f"[INFERRED] + {locations.SAFETY_MARGIN_M:g} m margin "
                   f"[taste]; a marker the dump cannot name takes the worst "
                   f"declared radius and is protected"),
        "tool": "tools/jumpstart/terraform/flatten.py::pad_location_check",
        "probe": probe_cmd,
        "nearest": ({"name": near.name, "dist_m": round(gap, 2),
                     "clearance_m": round(near.standoff_m, 2),
                     "identified": near.identified} if near else
                    {"name": None, "dist_m": None, "clearance_m": None,
                     "why": "no marker inside the probe"}),
        "standoff_m": round(near.standoff_m, 2) if near else None,
        "verdict": "clear" if not hits else "violates",
        "violations": [{"name": m.name, "x": round(m.x, 2), "z": round(m.z, 2),
                        "gap_m": round(g, 2),
                        "standoff_m": round(m.standoff_m, 2)} for m, g in hits],
    }


def pad_location_record(rc: Rcon, place: dict, apron: float,
                        dump: Path) -> tuple[list, str, dict]:
    """`pad_location_check`'s hits and report, plus the record the ledger keeps."""
    markers, hits, rect, cmd = _pad_markers(rc, place, apron, dump)
    return (hits, locations.report(markers, hits, *rect),
            location_record(markers, hits, rect, dump, cmd))


# ---------------------------------------------------------------------------
# INSTALLATION_WRITE_PATH_DEPENDS_ON: THE RECORD AND THE GROUND ARE ONE EVENT.
#
# A zone holds exactly ONE `_TerrainCompiler` and every terrain write in this
# tree deletes it before it spawns its own (`deleteObjects -zone ... -prefab
# _TerrainCompiler`).  A later write keeps an earlier one only by re-reading
# it out of the LEDGER and unioning it in (`settlements/build.py::
# union_prior`, `roads/ribbon.py`, both through `roads/applied.Applied`) -- so
# a write the ledger never saw is a write the next write in its zone DESTROYS,
# and nothing downstream can tell: `record_moves_bed`, `buildstage replay`, the
# ground freeze and the pad claims all read the same ledger.
#
# THE INSTALLATION CHAIN WAS THAT WRITE.  `clearing/clear.py apply` (step 1),
# this file's `apply` (step 2) and `terraform/stock.py` (step 6) drove raw
# RCON and appended nothing; `terraform/sites.py` (step 5) wrote plan files
# nothing executed.  MEASURED 2026-09-24 off the archived previous Fresh42
# drive (ledger/runs/Fresh42/archive-contaminated-20260924T2145): steps 1 and
# 2 ran 26 unit-runs at rc 0, step 6 13 at rc 0, and of its 1,822 ledger
# records ZERO name any of the 13 installations; step 5 wrote 4 'ok' plans
# (1,704 spawn commands) and 0 spawn_plan records.  On the CURRENT route
# (route-provisional/segments.Fresh42.yaml), at the ribbon's WRITE set
# (`segment_zones(batter_m=BATTER_MAX_M)`): 10 of the 36 installation-pad
# zones hold a later road write -- complete-station-hub 4 zones / 525 samples,
# sandbox-portal-hub 4 / 1,225, meadows-starter-hall 2 / 342, 2,092 pad
# samples erased by construction.  (The carriageway column says 8 of 36; it
# is the narrower set, see roads/network.py::ZONES_SCOPE.)
#
# So every installation write -- zones_generate, objects_clear, terrain_write,
# spawn_plan, spawn, portal -- goes through `install_emit`, which is
# `LiveBuilder.emit`: APPENDED FIRST, then sent, then verified.  And a pad
# write unions the ledger's claims in its zones like every other pad.
#
# THE TWO TERMS ARE THE TWO HALVES OF ONE EVENT, and each neuter drops one:
#   ledger_record  `raw`:   sent, never appended -- THE SHIPPED PATH.  A later
#                  same-zone write erases the pad again.
#   world_send     `ghost`: appended, never sent -- the record claims ground
#                  the world does not have.  An installation in a zone no
#                  later write touches is then not written at all.
# Neither may be spelled in production: the mode is `ledger`.
INSTALLATION_WRITE_PATH_DEPENDS_ON = ("ledger_record", "world_send")
_WRITE_PATH_MODE = "ledger"     # ledger | raw (no record) | ghost (no send)


def install_emit(b, rec: dict) -> dict:
    """THE ONE SEND for every installation write, build steps 1, 2, 5 and 6.

    `b` is a `ledger.live.LiveBuilder`; `rec` is `{op, params, wire,
    requires, expect, meta}`.  Returns what `LiveBuilder.emit` returns.
    """
    if _WRITE_PATH_MODE == "raw":
        # THE SHIPPED PATH, restored for the neuter only: every line reaches
        # the world and the ledger never hears of it.
        R = getattr(sys.modules.get(type(b).__module__), "R", None) \
            or _ledger("replay")
        if rec["op"] == "terrain_write":
            R._materialise_data_entries(b.led, rec)
        replies = R.send_wire(b.srv, rec["wire"])
        return {"seq": None, "op": rec["op"],
                "role": rec["params"].get("role"),
                "replies": [str(r)[:200] for r in replies],
                "status": "SENT OFF-LEDGER (neuter: ledger_record dropped)"}
    if _WRITE_PATH_MODE == "ghost":
        got = b.led.append(rec["op"], params=rec["params"], wire=rec["wire"],
                           requires=rec.get("requires"),
                           expect=rec.get("expect"), meta=rec.get("meta"))
        return {"seq": got["seq"], "op": rec["op"],
                "role": rec["params"].get("role"),
                "status": "RECORDED, NEVER SENT (neuter: world_send dropped)"}
    return b.emit(rec["op"], params=rec["params"], wire=rec["wire"],
                  requires=rec.get("requires"), expect=rec.get("expect"),
                  meta=rec.get("meta"))


def _ledger(name: str):
    """A `ledger/` module by the top-level name `live.py` itself resolves
    (`import replay as R` when `ledger/` is on the path), so the neuter, the
    fixture and the live session all hold the SAME module objects."""
    import importlib                                        # noqa: PLC0415
    led_dir = str(JUMPSTART / "ledger")
    if led_dir not in sys.path:
        sys.path.insert(0, led_dir)
    return importlib.import_module(name)


def live_builder():
    """`ledger.live.LiveBuilder`, the one sanctioned write session."""
    return _ledger("live").LiveBuilder


def _settlement_emitter():
    """`settlements/build.py`: THE pad emitter's union and record shape.

    Driven as a library in the other direction too: that module drives this
    one for its stamp, and this one drives that one for `union_prior`,
    `terrain_entries` and `terrain_record`, so an installation pad and a
    settlement pad are the same record written by the same code."""
    sett = str(JUMPSTART / "settlements")
    if sett not in sys.path:
        sys.path.insert(0, sett)
    import build as SB                                      # noqa: PLC0415
    return SB


def installation_pad_record(b, place: dict, preset: str, comps: dict,
                            op_y: dict, patches: dict, probes: dict,
                            location_check: dict, stats: dict, *,
                            paint: str, apron: float) -> dict:
    """ONE placement's `terrain_write role=site_pad`, unioned with the zone's
    ledger claims, in the record shape settlement pads already carry.

    `comps` is THIS placement's own stamp (`build` into an empty dict): the
    ledger, not a shared in-process dict, is what carries a second pad in the
    same zone forward, so two installations sharing a zone compose exactly as
    a road and a pad do."""
    SB = _settlement_emitter()
    solved = place["solved"]
    w, d = pad_extent(place)
    unit = {"id": place["id"], "x": float(solved["x"]), "z": float(solved["z"]),
            "target_y": stats["target_y"], "pad_w": w, "pad_d": d}
    merged = SB.union_prior(b, comps, op_y)
    entries, wire, blobs = SB.terrain_entries(b, unit, comps, op_y, patches,
                                              probes, merged)
    rec = SB.terrain_record(unit, preset, entries, wire, blobs, stats,
                            location_check)
    rec["params"].update(paint=paint, apron_m=apron)
    meta = {k: v for k, v in rec["meta"].items() if k != "settlement"}
    meta.update(
        installation=f"{preset}/{place['id']}",
        merged={f"{z[0]},{z[1]}": m["merged_from"] for z, m in merged.items()},
        why=("one compiler per zone, deleted before spawn so the op is "
             "idempotent; the ledger's claims in this pad's zones are unioned "
             "in first (INSTALLATION_WRITE_PATH_DEPENDS_ON), so a later write "
             "in the same zone carries this pad forward instead of erasing it"))
    rec["meta"] = meta
    return rec


# ---------------------------------------------------------------------------
# SELFTEST: PAD_EXTENT_DEPENDS_ON, neutered in each direction, on geometry
# BUILT IN MEMORY - a collider slab handed straight to the emitter's own
# `base_geometry.xz_footprint`, zone heights made of zeros.  No library, no
# scan, no plan file.  Every survivor is a verdict; nothing here asserts.
# ---------------------------------------------------------------------------
class _MemGeometry:
    """Collider solids built in memory: `xz_footprint` reads `_prefabs`."""

    def __init__(self, prefabs: dict):
        self._prefabs = prefabs


def _slab(w: float, d: float):
    from types import SimpleNamespace
    obj = SimpleNamespace(prefab="fixture_slab", pos=(0.0, 0.0, 0.0),
                          rot=(0.0, 0.0, 0.0, 1.0), scale=(1.0, 1.0, 1.0))
    return [obj], _MemGeometry(
        {"fixture_slab": [["b", -w / 2, 0.0, -d / 2, w / 2, 0.2, d / 2]]})


# stenvik-hall-1 AS plan.Fresh42.json DECLARES IT: a 20.1 m square body at yaw
# 30 on a 26.1 m pad, centred (350.1, -208.8), 3 m margin.  The square is the
# declared footprint made solid - the shape the inherited 27.46 m figure
# assumed (the real body is not square; see PAD_EXTENT_DEPENDS_ON).
_HALL = {"centre": (350.1, -208.8), "yaw": 30.0, "footprint": (20.1, 20.1),
         "declared": (26.1, 26.1), "margin": 3.0}
# A treehouse member as plan.Fresh42.json places it: 4.4 x 8.4 at yaw 120 on
# the site's declared 16 m pad.
_TREE = {"centre": (1000.3, 2000.7), "yaw": 120.0, "footprint": (4.4, 8.4),
         "declared": (16.0, 16.0), "margin": 3.0}
# complete-station-hub's shape on the preset path: a 20.06 x 24.00 m body on
# a 20.1 x 24.0 m pad with NO margin, centred on a half-metre in x.
_HUB = {"centre": (100.5, 200.0), "yaw": 0.0, "footprint": (20.1, 24.0),
        "declared": (20.1, 24.0), "margin": 0.0, "body": (20.06, 24.0)}


def _fixture_pad(fx: dict, depends_on: tuple = PAD_EXTENT_DEPENDS_ON,
                 yaw: float | None = None) -> tuple[dict, dict]:
    """The derived pad for fixture `fx`, and `body_cover` of THE BODY AT ITS
    OWN YAW on it - the neuter changes the derivation, never the body."""
    objs, geom = _slab(*fx.get("body", fx["footprint"]))
    ext = body_extents(objs, fx["yaw"] if yaw is None else yaw, geom)
    pad = pad_extent_at_yaw(fx["declared"], fx["footprint"], ext,
                            fx["centre"], fx["margin"], 0.0, depends_on)
    return pad, body_cover(fx["centre"], pad["w"], pad["d"], *ext["at_yaw"])


def _identical(a, b) -> str:
    """VERDICT: are two (w, d) pads the same bytes - same type, same value?"""
    return ("identical" if all(type(x) is type(y) and repr(x) == repr(y)
                               for x, y in zip(a, b)) else "differs")


def _without(term: str) -> tuple:
    return tuple(t for t in PAD_EXTENT_DEPENDS_ON if t != term)


def _brief(pad: dict, cover: dict) -> str:
    return (f"pad {pad['w']} x {pad['d']} for a body {cover['body_at_yaw']}: "
            f"{cover['verdict']}, {cover['unstamped']} of "
            f"{cover['vertices_under_body']} vertices unstamped, overhang x "
            f"{cover['overhang_x_m']} z {cover['overhang_z_m']} m")


def _case_the_yawed_hall_stands_on_ground_the_writer_levels() -> list[str]:
    pad, cover = _fixture_pad(_HALL)
    return [] if cover["verdict"] == "covered" else [
        f"the derived pad leaves the yawed hall off its ground: "
        f"{_brief(pad, cover)}"]


def _case_NEUTER_drop_body_yaw_the_hall_overhang_RETURNS() -> list[str]:
    """LOSS ARM: measured unturned, the hall gets its declared 26.1 m pad
    back, and the body standing at yaw 30 overhangs it again."""
    pad, cover = _fixture_pad(_HALL, _without("body_yaw"))
    return [] if cover["verdict"] == "overhangs" else [
        f"without body_yaw the hall is still covered ({_brief(pad, cover)}), "
        f"so the yaw term carries nothing"]


def _case_NEUTER_drop_body_footprint_the_hall_overhang_RETURNS() -> list[str]:
    """LOSS ARM: without the body there is only the declared pad."""
    pad, cover = _fixture_pad(_HALL, _without("body_footprint"))
    return [] if cover["verdict"] == "overhangs" else [
        f"without body_footprint the hall is still covered "
        f"({_brief(pad, cover)})"]


def _case_a_yaw_0_body_SURVIVES_byte_identical() -> list[str]:
    """SURVIVOR: an unturned body gets today's pad, byte for byte - with
    every term, and with the yaw term dropped."""
    out = []
    for label, terms in (("every term", PAD_EXTENT_DEPENDS_ON),
                         ("body_yaw dropped", _without("body_yaw"))):
        pad, _cover = _fixture_pad(_HALL, terms, yaw=0.0)
        v = _identical((pad["w"], pad["d"]), _HALL["declared"])
        if v != "identical":
            out.append(f"yaw 0, {label}: pad {pad['w']!r} x {pad['d']!r} "
                       f"{v} from the declared {_HALL['declared']!r}")
    return out


def _case_NEUTER_drop_declared_pad_the_site_pad_SHRINKS() -> list[str]:
    """LOSS ARM: a treehouse's 16 m pad is the SITE's, sited and billed; the
    body alone at yaw 120 would shrink it under what was billed.  With every
    term the pad is the declared one, byte for byte (a verdict)."""
    full, _c = _fixture_pad(_TREE)
    bare, _c2 = _fixture_pad(_TREE, _without("declared_pad"))
    out = []
    if _identical((full["w"], full["d"]), _TREE["declared"]) != "identical":
        out.append(f"with every term the treehouse pad became "
                   f"{full['w']} x {full['d']}, not its declared 16 x 16")
    if _identical((bare["w"], bare["d"]), _TREE["declared"]) == "identical":
        out.append("without declared_pad the treehouse pad is still 16 x 16, "
                   "so the declared term carries nothing")
    return out


def _case_NEUTER_drop_written_samples_the_edge_vertex_RETURNS() -> list[str]:
    """LOSS ARM at ZERO margin: a 20.06 m body on a 20.1 m pad centred on a
    half-metre reaches vertices 10.5 m out, and the stamp admits 10.30."""
    pad, cover = _fixture_pad(_HUB, _without("written_samples"))
    full, fcover = _fixture_pad(_HUB)
    out = []
    if cover["verdict"] != "overhangs":
        out.append(f"without written_samples the hub is still covered "
                   f"({_brief(pad, cover)})")
    if fcover["verdict"] != "covered":
        out.append(f"with written_samples the hub is not covered "
                   f"({_brief(full, fcover)})")
    return out


def _case_NEUTER_drop_written_samples_at_3m_margin_SURVIVES() -> list[str]:
    """SURVIVOR: at the settlement margin the lattice term is implied, so
    dropping it must leave the hall's pad unchanged (a verdict)."""
    full, _c = _fixture_pad(_HALL)
    bare, _c2 = _fixture_pad(_HALL, _without("written_samples"))
    v = _identical((bare["w"], bare["d"]), (full["w"], full["d"]))
    return [] if v == "identical" else [
        f"dropping written_samples moved the hall's pad from {full['w']} x "
        f"{full['d']} to {bare['w']} x {bare['d']} at a 3 m margin"]


def _case_the_derived_pad_is_what_the_writer_stamps_and_the_bill_names(
        ) -> list[str]:
    """The pad goes out through `footprint_orientation` and comes back
    through `pad_extent`; `build` then stamps it on zones of zeros, and the
    samples it ACTUALLY set are compared with `site_finder.written_samples`
    (the bill's population) and with every vertex under the yawed hall."""
    pad, _cover = _fixture_pad(_HALL)
    (cx, cz) = _HALL["centre"]
    place = {"id": "fixture", "solved": {
        "x": cx, "z": cz, "flatten_cost": {"target_y": 50.0},
        "footprint_orientation": (f"{pad['w']:g} m along x by "
                                  f"{pad['d']:g} m along z")}}
    out = []
    back = pad_extent(place)
    if _identical(back, (pad["w"], pad["d"])) != "identical":
        out.append(f"footprint_orientation round-trips {pad['w']} x "
                   f"{pad['d']} as {back}")
    zones = zones_for(cx, cz, *back)
    flat = [0.0] * (tcdata.PITCH * tcdata.PITCH)
    patches = {f"z_{zx}_{zz}": patchheights.Patch(
        f"z_{zx}_{zz}", zx * tcdata.ZONE_SIZE, zz * tcdata.ZONE_SIZE, 32.5,
        1.0, tcdata.PITCH, flat, [0] * len(flat)) for zx, zz in zones}
    comps: dict = {}
    st = build(place, patches, zones, "paved_cleared", 0.0, comps, {})
    written = set()
    for (zx, zz), comp in comps.items():
        zcx, zcz = comp.centre
        for i, on in enumerate(comp.modified_height):
            if on:
                written.add(tcdata.sample_world(zcx, zcz, i % tcdata.PITCH,
                                                i // tcdata.PITCH))
    _blueprints_on_path()
    import site_finder
    billed = sum(len(r) * len(c) for _z, r, c in
                 site_finder.written_samples(cx, cz, *back))
    if billed != st["samples"]:
        out.append(f"the bill names {billed} samples, the writer stamped "
                   f"{st['samples']}")
    objs, geom = _slab(*_HALL["footprint"])
    bw, bd = body_extents(objs, _HALL["yaw"], geom)["at_yaw"]
    under = {(float(x), float(z))
             for x in answering_vertices(cx - bw / 2, cx + bw / 2)
             for z in answering_vertices(cz - bd / 2, cz + bd / 2)}
    if not under or not written:
        out.append(f"an empty population: {len(under)} vertices under the "
                   f"hall, {len(written)} stamped")
    missing = under - written
    if missing:
        out.append(f"{len(missing)} of {len(under)} vertices under the yawed "
                   f"hall were not stamped by build, e.g. {sorted(missing)[:3]}")
    return out


def _case_UNREAD_is_not_covered() -> list[str]:
    """A placement whose yaw or alignment is unread gets no `covered`."""
    base = {"id": "fixture", "blueprint": "fixture.blueprint",
            "solved": {"x": 0.0, "z": 0.0,
                       "footprint_orientation": "30 m along x by 30 m along z"}}
    got = {"no yaw": placed_body_cover(base)["verdict"],
           "raw alignment": placed_body_cover(
               {**base, "rotation": {"yaw": 0.0}, "align": "raw"})["verdict"]}
    return [f"{k}: {v}" for k, v in got.items() if v == "covered"]


# pre-queen#mistlands-blackforge-base AS FRESH42 DRIVE 2 PLACED IT: a body
# measuring 19.087 x 12.433 m at yaw 180 (its recorded `body_at_yaw`),
# declared 17.5 x 10.9 m at (-8163.5, 4417.5) -- written as 18.8 x 12.8 m --
# and its 32 step-6 fixtures (ledger seqs 1954-1985), whose footprints span
# these offsets from the centre.  The cartography table, seq 1978 at
# (-8167.5, 40.57, 4425.5), is the operator's buried one.
_BLACKFORGE = {"centre": (-8163.5, 4417.5), "yaw": 180.0,
               "footprint": (17.5, 10.9), "declared": (17.5, 10.9),
               "margin": 0.0, "body": (19.087, 12.433)}
_BLACKFORGE_RING = {"x": [-22.102, 20.45], "z": [-17.474, 11.041],
                    "fixtures": 32}
_TABLE_1978 = (-5.899, -1.969, 7.092, 9.044)


def _ring_pad(fx: dict, ring, depends_on: tuple = PAD_EXTENT_DEPENDS_ON
              ) -> dict:
    objs, geom = _slab(*fx.get("body", fx["footprint"]))
    ext = body_extents(objs, fx["yaw"], geom)
    return pad_extent_at_yaw(fx["declared"], fx["footprint"], ext,
                             fx["centre"], fx["margin"], 0.0, depends_on,
                             ring)


def _unstamped(centre, pad: dict, box) -> tuple[int, int]:
    """(vertices the pad leaves unstamped, vertices) under a box of offsets
    (xlo, xhi, zlo, zhi) from `centre` - the writer's own predicate."""
    cx, cz = centre
    xs = answering_vertices(cx + box[0], cx + box[1])
    zs = answering_vertices(cz + box[2], cz + box[3])
    sx = sum(1 for v in xs if stamps(v * tcdata.SCALE - cx, pad["w"] / 2))
    sz = sum(1 for v in zs if stamps(v * tcdata.SCALE - cz, pad["d"] / 2))
    return len(xs) * len(zs) - sx * sz, len(xs) * len(zs)


def _case_the_fixture_ring_is_levelled_where_it_stands() -> list[str]:
    """The pad derived with the ring stamps every vertex under the 32
    fixtures' extent, the buried cartography table's included."""
    pad = _ring_pad(_BLACKFORGE, _BLACKFORGE_RING)
    r = _BLACKFORGE_RING
    out = []
    for label, box in (("the ring", (*r["x"], *r["z"])),
                       ("the seq-1978 table", _TABLE_1978)):
        miss, n = _unstamped(_BLACKFORGE["centre"], pad, box)
        if miss or not n:
            out.append(f"{label}: {miss} of {n} vertices unstamped by the "
                       f"{pad['w']} x {pad['d']} m ring pad")
    return out


def _case_NEUTER_drop_fixture_ring_the_table_stands_OFF_its_pad() -> list[str]:
    """LOSS ARM: without the ring the derivation gives drive 2's written
    18.8 x 12.8 m pad back, and the cartography table's ground is unlevelled
    -- the operator's buried table."""
    pad = _ring_pad(_BLACKFORGE, _BLACKFORGE_RING, _without("fixture_ring"))
    miss, n = _unstamped(_BLACKFORGE["centre"], pad, _TABLE_1978)
    out = []
    if (pad["w"], pad["d"]) != (18.8, 12.8):
        out.append(f"the ringless pad is {pad['w']} x {pad['d']}, not drive "
                   f"2's written 18.8 x 12.8")
    if not miss:
        out.append(f"without fixture_ring the table's {n} vertices are all "
                   f"stamped, so the ring term carries nothing")
    return out


def _case_no_ring_SURVIVES_byte_identical() -> list[str]:
    """SURVIVOR: a site with no ring (a settlement unit, a solve that could
    not read one) gets today's pad byte for byte."""
    base, _c = _fixture_pad(_HALL)
    out = []
    for label, ring in (("ring None", None), ("ring {}", {}),
                        ("ring unread", {"unread": "x"})):
        objs, geom = _slab(*_HALL["footprint"])
        ext = body_extents(objs, _HALL["yaw"], geom)
        pad = pad_extent_at_yaw(_HALL["declared"], _HALL["footprint"], ext,
                                _HALL["centre"], _HALL["margin"], 0.0,
                                PAD_EXTENT_DEPENDS_ON, ring)
        if _identical((pad["w"], pad["d"]), (base["w"], base["d"])) \
                != "identical":
            out.append(f"{label}: {pad['w']} x {pad['d']} against "
                       f"{base['w']} x {base['d']}")
    return out


# The drive-2 pair, as ledger seqs 565 and 568 wrote it.
_D2_DOCK = {"id": "early-dock", "solved": {
    "x": -7.5, "z": -416.5, "flatten_cost": {"target_y": 26.98},
    "footprint_orientation": "42.8 m along x by 36.8 m along z"}}
_D2_HARBOUR = dict(_D2_DOCK, id="sandbox-harbour",
                   duplicate_of="pre-elder#early-dock")


def _stamped_samples(place: dict) -> set:
    cx, cz = place["solved"]["x"], place["solved"]["z"]
    zones = zones_for(cx, cz, *pad_extent(place))
    flat = [0.0] * (tcdata.PITCH * tcdata.PITCH)
    patches = {f"z_{zx}_{zz}": patchheights.Patch(
        f"z_{zx}_{zz}", zx * tcdata.ZONE_SIZE, zz * tcdata.ZONE_SIZE, 32.5,
        1.0, tcdata.PITCH, flat, [0] * len(flat)) for zx, zz in zones}
    comps: dict = {}
    build(place, patches, zones, "paved_cleared", 0.0, comps, {})
    return {(z, i, round(c.level_delta[i], 3)) for z, c in comps.items()
            for i, on in enumerate(c.modified_height) if on}


def _case_a_declared_duplicate_is_levelled_once() -> list[str]:
    write, skipped = pads_to_write([_D2_HARBOUR, _D2_DOCK])
    got = ([p["id"] for p in write], [s["id"] for s in skipped])
    return [] if got == (["early-dock"], ["sandbox-harbour"]) else [
        f"levelled {got[0]}, skipped {got[1]}"]


def _case_NEUTER_ignore_duplicate_of_the_pad_is_levelled_TWICE() -> list[str]:
    """LOSS ARM: every placement its own site, and the harbour writes the
    dock's pad again, sample for sample -- seqs 565 and 568."""
    global _DUPLICATE_MODE  # noqa: PLW0603
    _DUPLICATE_MODE = "ignored"
    try:
        write, _skipped = pads_to_write([_D2_HARBOUR, _D2_DOCK])
    finally:
        _DUPLICATE_MODE = "honoured"
    if len(write) != 2:
        return [f"the shipped reading levels {len(write)} pad(s), not 2"]
    a, b = (_stamped_samples(p) for p in write)
    return [] if a and a == b else [
        f"the two writes are not one pad twice ({len(a)} vs {len(b)} samples)"]


_PAD_EXTENT_CASES = (
    ("the_yawed_hall_stands_on_ground_the_writer_levels",
     _case_the_yawed_hall_stands_on_ground_the_writer_levels),
    ("NEUTER_drop_body_yaw_the_hall_overhang_RETURNS",
     _case_NEUTER_drop_body_yaw_the_hall_overhang_RETURNS),
    ("NEUTER_drop_body_footprint_the_hall_overhang_RETURNS",
     _case_NEUTER_drop_body_footprint_the_hall_overhang_RETURNS),
    ("a_yaw_0_body_SURVIVES_byte_identical",
     _case_a_yaw_0_body_SURVIVES_byte_identical),
    ("NEUTER_drop_declared_pad_the_site_pad_SHRINKS",
     _case_NEUTER_drop_declared_pad_the_site_pad_SHRINKS),
    ("NEUTER_drop_written_samples_the_edge_vertex_RETURNS",
     _case_NEUTER_drop_written_samples_the_edge_vertex_RETURNS),
    ("NEUTER_drop_written_samples_at_3m_margin_SURVIVES",
     _case_NEUTER_drop_written_samples_at_3m_margin_SURVIVES),
    ("the_derived_pad_is_what_the_writer_stamps_and_the_bill_names",
     _case_the_derived_pad_is_what_the_writer_stamps_and_the_bill_names),
    ("UNREAD_is_not_covered", _case_UNREAD_is_not_covered),
    ("the_fixture_ring_is_levelled_where_it_stands",
     _case_the_fixture_ring_is_levelled_where_it_stands),
    ("NEUTER_drop_fixture_ring_the_table_stands_OFF_its_pad",
     _case_NEUTER_drop_fixture_ring_the_table_stands_OFF_its_pad),
    ("no_ring_SURVIVES_byte_identical", _case_no_ring_SURVIVES_byte_identical),
)

_DUPLICATE_CASES = (
    ("a_declared_duplicate_is_levelled_once",
     _case_a_declared_duplicate_is_levelled_once),
    ("NEUTER_ignore_duplicate_of_the_pad_is_levelled_TWICE",
     _case_NEUTER_ignore_duplicate_of_the_pad_is_levelled_TWICE),
)


# ---------------------------------------------------------------------------
# SELFTEST: INSTALLATION_WRITE_PATH_DEPENDS_ON, both terms neutered, on a
# WORLD BUILT IN MEMORY.  The write goes through the REAL `LiveBuilder.emit`
# into a REAL `writer.Ledger` in a temporary directory -- so `schema.validate`
# and every cross-record guard (the one-compiler-per-zone clobber check above
# all) run for real -- and `replay.send_wire` delivers it to `_MemServer`, a
# `replay.Server` whose zones are a dict.  The later write is
# `settlements/build.py`'s own pad chain (union_prior -> terrain_entries ->
# terrain_record -> emit), exactly as step 5 runs it.  No socket, no save, no
# patchscan: generated heights are a flat 30.0 m field.
# ---------------------------------------------------------------------------
_SELFTEST_MANIFEST = {
    "schema_version": "1.0", "world": "Selftest", "seed_name": "Selftest",
    "seed": 0, "world_version": 0, "world_gen_version": 0,
    "game_build_anchor": "0" * 64, "modifiers": {}, "mods": [],
    "tool": {"name": "terraform/flatten.py selftest", "in_memory": True}}


class _NoWatcherLatency:
    """`replay`'s `time`, minus the sleep that waits for a file watcher an
    in-memory world does not have."""

    def __getattr__(self, name):
        return getattr(time, name)

    @staticmethod
    def sleep(_s):
        return None


def _mem_server(R):
    """An in-memory world behind `replay.Server`'s own interface."""
    import base64                                           # noqa: PLC0415
    import fnmatch                                          # noqa: PLC0415

    class _MemServer(R.Server):
        def __init__(self):
            super().__init__(dry=False)
            self.compilers: dict[tuple[int, int], bytes] = {}
            self.objects: list[tuple[str, float, float, float]] = []
            self.generated: set[tuple[int, int]] = set()

        def probe(self) -> float:
            return 0.0

        def command(self, cmd: str) -> str:
            self.sent.append(cmd)
            t = cmd.split()
            if t[:1] == ["players"]:
                return "Online 0"
            if t[:1] == ["deleteObjects"] and "-zone" in t:
                i = t.index("-zone")
                gone = self.compilers.pop((int(t[i + 1]), int(t[i + 2])), None)
                return f"Deleted {0 if gone is None else 1} objects"
            return f"Command '{cmd}' executed."

        def console(self, cmd: str, settle: float = 2.0) -> list[str]:
            self.sent.append(cmd)
            t = cmd.split()
            if not t or t[0] not in ("zones_generate", "objects_count",
                                     "objects_remove"):
                return []
            kv = dict(a.split("=", 1) for a in t[1:] if "=" in a)
            px, pz = (float(v) for v in kv["pos"].split(","))
            r = float(kv["max"])
            if t[0] == "zones_generate":
                zx0, zz0 = tcdata.zone_of(px, pz)
                k = int(r // 64) + 2
                self.generated |= {(zx, zz) for zx in range(zx0 - k, zx0 + k + 1)
                                   for zz in range(zz0 - k, zz0 + k + 1)
                                   if math.hypot(zx * 64 - px, zz * 64 - pz) <= r}
                return []
            ids = kv.get("id", "*").split(",")
            ign = [s for s in kv.get("ignore", "").split(",") if s]

            def hit(p, x, z):
                return (any(fnmatch.fnmatchcase(p, i) for i in ids)
                        and not any(fnmatch.fnmatchcase(p, g) for g in ign)
                        and math.hypot(x - px, z - pz) <= r)
            if t[0] == "objects_remove":
                self.objects = [o for o in self.objects
                                if not hit(o[0], o[1], o[3])]
                return []
            per: dict[str, int] = {}
            for p, x, _y, z in self.everything():
                if hit(p, x, z):
                    per[p] = per.get(p, 0) + 1
            return ([f"Total: {sum(per.values())}"]
                    + [f"{p}: {n}" for p, n in sorted(per.items())])

        def console_echo(self, cmd: str) -> str:
            self.sent.append(cmd)
            for line in cmd.split(";"):
                t = line.split()
                if len(t) < 2 or t[0] != "spawn_object":
                    continue
                kv = dict(a.split("=", 1) for a in t[2:] if "=" in a)
                if t[1] == "_TerrainCompiler":
                    cx, cz = (float(v) for v in kv["from"].split(",")[:2])
                    self.compilers[tcdata.zone_of(cx, cz)] = self.entry(kv["data"])
                else:
                    z, x, y = (float(v) for v in kv["pos"].split(",")[:3])
                    self.objects.append((t[1], x, y, z))
            return f"Command '{cmd}' executed."

        def entry(self, name: str) -> bytes:
            """Resolved the way ServerDevcommands resolves it: out of the
            watched directory, by name, at spawn time."""
            for f in sorted(data_dir().glob("*.yaml")):
                for e in yaml.safe_load(f.read_text()) or []:
                    if e.get("name") == name:
                        return base64.b64decode(e["bytes"][0].split(", ", 1)[1])
            raise KeyError(f"Can't find data entry: {name}")

        def everything(self):
            return (list(self.objects)
                    + [("_TerrainCompiler", cx, 0.0, cz) for cx, cz in
                       (tcdata.zone_centre(*z) for z in self.compilers)]
                    + [("_ZoneCtrl", zx * 64.0, 0.0, zz * 64.0)
                       for zx, zz in self.generated])

    return _MemServer()


class memory_session:
    """`with memory_session() as b:` -- a `LiveBuilder` whose ledger is a real
    `writer.Ledger` in a temporary directory and whose server is `_MemServer`.
    `$WEC_DATA_DIR` points inside the same directory for the duration."""

    def __init__(self, actor: str = "selftest",
                 world: str = _SELFTEST_MANIFEST["world"]):
        # THE WORLD IS THE CALLER'S, like every `Ledger.open` in this tree
        # (`buildstage.ledger_world_defects`): here it only labels a chain
        # that lives in a temporary directory and dies with the session.
        self.actor, self.world = actor, world

    def __enter__(self):
        import tempfile                                     # noqa: PLC0415
        LB = live_builder()
        self.R = sys.modules[LB.__module__].R
        writer = sys.modules[LB.__module__].W
        self._td = tempfile.TemporaryDirectory(prefix="installpath_")
        root = Path(self._td.name)
        self._env = os.environ.get(DATA_DIR_ENV)
        os.environ[DATA_DIR_ENV] = str(root / "data")
        self._time, self.R.time = self.R.time, _NoWatcherLatency()
        b = object.__new__(LB)
        b.actor, b.world, b.dry, b.root = self.actor, self.world, False, root / "run"
        b.led = writer.Ledger.open(self.world, actor=self.actor, root=b.root,
                                   manifest=dict(_SELFTEST_MANIFEST,
                                                 world=self.world))
        b.srv = _mem_server(self.R)
        b.transport = {"world": self.world, "declared": True,
                       "how": "in-memory world: no socket"}
        return b

    def __exit__(self, *exc):
        self.R.time = self._time
        if self._env is None:
            os.environ.pop(DATA_DIR_ENV, None)
        else:
            os.environ[DATA_DIR_ENV] = self._env
        self._td.cleanup()


_FX_GROUND_Y = 30.0


def _fx_place(pid: str, x: float, z: float, w: float, d: float,
              target_y: float) -> dict:
    """A solved placement, in the shape `placements.yaml` carries."""
    return {"id": pid, "blueprint": f"{pid}.blueprint",
            "rotation": {"yaw": 0.0}, "align": "floor-center",
            "solved": {"x": x, "z": z, "y": target_y,
                       "footprint_orientation": f"{w:g} m along x by {d:g} m along z",
                       "flatten_cost": {"target_y": target_y}}}


def _fx_patches(zones) -> dict:
    return {f"z_{zx}_{zz}": patchheights.Patch(
        f"z_{zx}_{zz}", *tcdata.zone_centre(zx, zz), 32.5, 1.0, tcdata.PITCH,
        [_FX_GROUND_Y] * tcdata.SAMPLES, [1] * tcdata.SAMPLES)
        for zx, zz in zones}


def _fx_stamp(place: dict):
    w, d = pad_extent(place)
    zones = zones_for(float(place["solved"]["x"]), float(place["solved"]["z"]), w, d)
    comps: dict = {}
    op_y: dict = {}
    patches = _fx_patches(zones)
    stats = build(place, patches, zones, "paved_cleared", 0.0, comps, op_y)
    probes = {z: {"cmd": f"objects_count id={ZONE_CTRL} pos={tcdata.zone_centre(*z)[0]:g},"
                         f"{tcdata.zone_centre(*z)[1]:g} max=1", "count": 1}
              for z in comps}
    w_, d_ = pad_extent(place)
    rect = (float(place["solved"]["x"]), float(place["solved"]["z"]), w_ / 2, d_ / 2)
    lc = location_record([], [], rect, "(in-memory: no dump)",
                         "(in-memory: no marker probe)")
    return comps, op_y, patches, probes, lc, stats


def _fx_install(b, place: dict, preset: str = "fixture") -> tuple[dict, dict]:
    """Step 2, as `apply` runs it: stamp, record, `install_emit`."""
    comps, op_y, patches, probes, lc, stats = _fx_stamp(place)
    rec = installation_pad_record(b, place, preset, comps, op_y, patches,
                                  probes, lc, stats, paint="paved_cleared",
                                  apron=0.0)
    return install_emit(b, rec), {z: (c, op_y.get(z, 0.0))
                                  for z, c in comps.items()}


def _fx_later_settlement_pad(b, place: dict) -> dict:
    """Step 5's own pad chain, verbatim from `settlements/build.py::emit_pad`
    past its live gates: union_prior -> terrain_entries -> terrain_record ->
    emit.  THE LATER LEDGERED WRITE: it unions the LEDGER's claims only."""
    SB = _settlement_emitter()
    comps, op_y, patches, probes, lc, stats = _fx_stamp(place)
    w, d = pad_extent(place)
    unit = {"id": place["id"], "x": float(place["solved"]["x"]),
            "z": float(place["solved"]["z"]), "target_y": stats["target_y"],
            "pad_w": w, "pad_d": d}
    merged = SB.union_prior(b, comps, op_y)
    entries, wire, blobs = SB.terrain_entries(b, unit, comps, op_y, patches,
                                              probes, merged)
    rec = SB.terrain_record(unit, "fixture-town", entries, wire, blobs, stats, lc)
    return b.emit("terrain_write", params=rec["params"], wire=rec["wire"],
                  requires=rec["requires"], expect=rec["expect"],
                  meta=rec["meta"])


def _pad_on_world(b, zone, comp_op) -> dict:
    """A VERDICT: does the compiler the WORLD holds in `zone` carry every
    sample the installation pad wrote there, at the height it wrote?"""
    comp, op_y = comp_op
    ours = {i: comp.level_delta[i] for i in range(tcdata.SAMPLES)
            if comp.modified_height[i]}
    blob = b.srv.compilers.get(zone)
    if blob is None:
        return {"verdict": "absent", "held": 0, "wrote": len(ours),
                "identical": False}
    got = tcdata.parse(blob)["heights"]
    held = sum(1 for i, v in ours.items()
               if i in got and abs(got[i][0] - v) < 1e-3)
    return {"verdict": ("preserved" if held == len(ours) else
                        "erased" if held == 0 else "partly erased"),
            "held": held, "wrote": len(ours),
            "identical": blob == comp.blob(op_y)}


# The zone every shared-ground fixture sits in: (8, 14), centre (512, 896).
# The installation pad and the later settlement pad are DISJOINT rectangles
# in ONE zone -- the shape of complete-station-hub's four zones against six
# stathub roads on the current route -- and the survivor sits alone in
# (-5, 20), a zone no later write names.
_FX_SHARED = (8, 14)
_FX_ALONE = (-5, 20)
_FX_INSTALL = _fx_place("fixture-installation", 500.0, 890.0, 10.0, 10.0, 31.0)
_FX_TOWN = _fx_place("fixture-town-pad", 530.0, 905.0, 6.0, 6.0, 32.0)
_FX_LONE = _fx_place("fixture-lone-installation", -320.0, 1280.0, 8.0, 8.0, 29.5)


def installation_write_scenario(mode: str | None = None) -> dict:
    """Two installations written by step 2, then one settlement pad written by
    step 5 in the FIRST installation's zone, under `mode` (default: the
    production path).  Returns the world's own verdict for both zones."""
    import contextlib                                       # noqa: PLC0415
    import io                                               # noqa: PLC0415
    g = globals()
    prior = g["_WRITE_PATH_MODE"]
    g["_WRITE_PATH_MODE"] = mode or prior
    try:
        with memory_session() as b, contextlib.redirect_stdout(io.StringIO()):
            first, shared = _fx_install(b, _FX_INSTALL)
            _lone_res, alone = _fx_install(b, _FX_LONE)
            _fx_later_settlement_pad(b, _FX_TOWN)
            recs = b.led.records()
            ai = _ledger_instruments()
            pad_recs = [r for r in recs if r["op"] == "terrain_write"
                        and r["params"].get("site_id") == _FX_INSTALL["id"]]
            return {
                "mode": g["_WRITE_PATH_MODE"],
                "shared": _pad_on_world(b, _FX_SHARED, shared[_FX_SHARED]),
                "alone": _pad_on_world(b, _FX_ALONE, alone[_FX_ALONE]),
                "install_records": len(pad_recs),
                "record_moves_bed": [ai.record_moves_bed(r) for r in pad_recs],
                "first_seq": first.get("seq"),
            }
    finally:
        g["_WRITE_PATH_MODE"] = prior


def _ledger_instruments():
    if str(JUMPSTART) not in sys.path:
        sys.path.append(str(JUMPSTART))
    import accept.instruments as AI                         # noqa: PLC0415
    return AI


def _case_a_ledgered_pad_SURVIVES_a_later_write_in_its_zone() -> list[str]:
    """THE FIX.  The installation pad is on the ledger, so step 5's union
    carries every one of its samples forward -- and `record_moves_bed`, the
    accept ground cache's key, can see it."""
    s = installation_write_scenario()
    bad = []
    if s["shared"]["verdict"] != "preserved":
        bad.append(f"shared zone {_FX_SHARED}: {s['shared']['held']} of "
                   f"{s['shared']['wrote']} installation samples on the "
                   f"compiler after the later write ({s['shared']['verdict']})")
    if s["install_records"] != 1 or not all(s["record_moves_bed"]):
        bad.append(f"{s['install_records']} ledger record(s) for the pad, "
                   f"record_moves_bed {s['record_moves_bed']}")
    return bad


def _case_NEUTER_drop_ledger_record_the_later_write_ERASES_the_pad() -> list[str]:
    """LOSS ARM, and it is THE SHIPPED PATH: sent over RCON, never appended.
    Step 5's union finds no claim in the zone, deletes the compiler and spawns
    its own -- every installation sample in that zone is gone."""
    s = installation_write_scenario("raw")
    return [] if (s["shared"]["verdict"] == "erased"
                  and s["install_records"] == 0) else [
        f"with the record dropped the pad came out {s['shared']['verdict']} "
        f"({s['shared']['held']} of {s['shared']['wrote']} held, "
        f"{s['install_records']} records): the fixture no longer "
        f"describes the defect"]


def _case_an_untouched_zone_is_written_byte_identically_SURVIVES() -> list[str]:
    """SURVIVOR, AS A VERDICT: an installation in a zone no later write names
    lands the SAME bytes whether the write path records it or not -- the
    bytes the shipped `apply` spawned (`comp.blob(op_y)` of its own stamp).
    The ledger path changes what the chain KNOWS, never what this pad is."""
    got = {m: installation_write_scenario(m)["alone"] for m in ("ledger", "raw")}
    verdicts = {m: ("identical" if v["identical"] else "differs")
                for m, v in got.items()}
    return [] if set(verdicts.values()) == {"identical"} else [
        f"untouched zone {_FX_ALONE}: {verdicts} "
        f"({ {m: v['verdict'] for m, v in got.items()} })"]


def _case_NEUTER_drop_world_send_the_untouched_pad_is_ABSENT() -> list[str]:
    """LOSS ARM, the other half: appended, never sent.  The chain claims a
    pad the world does not have -- THE RECORD AND THE GROUND DISAGREE in the
    other direction, and the survivor above must see it."""
    s = installation_write_scenario("ghost")
    return [] if (s["alone"]["verdict"] == "absent"
                  and s["install_records"] == 1) else [
        f"with the send dropped the lone pad came out {s['alone']['verdict']} "
        f"with {s['install_records']} record(s): the survivor cannot tell a "
        f"recorded write from a written one"]


_WRITE_PATH_CASES = (
    ("a_ledgered_pad_SURVIVES_a_later_write_in_its_zone",
     _case_a_ledgered_pad_SURVIVES_a_later_write_in_its_zone),
    ("NEUTER_drop_ledger_record_the_later_write_ERASES_the_pad",
     _case_NEUTER_drop_ledger_record_the_later_write_ERASES_the_pad),
    ("an_untouched_zone_is_written_byte_identically_SURVIVES",
     _case_an_untouched_zone_is_written_byte_identically_SURVIVES),
    ("NEUTER_drop_world_send_the_untouched_pad_is_ABSENT",
     _case_NEUTER_drop_world_send_the_untouched_pad_is_ABSENT),
)


def clamp_refusals(builds, depends_on: tuple = tcdata.PAD_CLAMP_DEPENDS_ON) -> list[dict]:
    """Every placement in `builds` whose ground cannot reach its datum, each
    as `tcdata.clamp_verdict`'s own numbers plus the placement id.  `builds`
    is `(place, comps, op_y, stats)`, the shape `main` assembles."""
    out = []
    for _place, _comps, _op_y, stats in builds:
        v = tcdata.clamp_verdict(stats, depends_on)
        if v["verdict"] == "refused":
            out.append({"id": stats.get("id"), **v})
    return out


# iron-era-workshop exactly as step 2 printed it before writing seq 561, and a
# neighbour from the same run that fits.  Numbers, not a world.
_IRON_ERA = {"id": "iron-era-workshop", "samples": 5041, "max_cut": -10.54,
             "max_fill": 9.569, "over_clamp": 442}
_STARTER = {"id": "meadows-starter-hall", "samples": 342, "max_cut": -0.618,
            "max_fill": 1.086, "over_clamp": 0}


def _c_the_installation_writer_refuses_iron_era() -> list[str]:
    got = clamp_refusals([(None, None, None, _IRON_ERA), (None, None, None, _STARTER)])
    ids = [v["id"] for v in got]
    return [] if ids == ["iron-era-workshop"] else [f"refused {ids}, required ['iron-era-workshop']"]


def _c_NEUTER_no_clamp_terms_iron_era_is_WRITTEN() -> list[str]:
    # The shipped writer: it measured over_clamp and asked nothing of it.
    got = clamp_refusals([(None, None, None, _IRON_ERA)], depends_on=())
    return [] if got == [] else [f"NEUTER DID NOT FLIP: still refused {got}"]


def _c_the_sample_count_alone_carries_it() -> list[str]:
    # Extremes held inside the clamp; only the count says the ground falls short.
    st = dict(_IRON_ERA, max_cut=-7.9, max_fill=7.9)
    got = clamp_refusals([(None, None, None, st)])
    blind = clamp_refusals([(None, None, None, st)],
                           depends_on=("max_fill_m", "max_cut_m"))
    probs = []
    if [v["id"] for v in got] != ["iron-era-workshop"]:
        probs.append(f"with the count: refused {got}, required iron-era-workshop")
    if blind:
        probs.append(f"NEUTER DID NOT FLIP: extremes alone refused {blind}")
    return probs


def _c_a_pad_that_fits_SURVIVES() -> list[str]:
    got = clamp_refusals([(None, None, None, _STARTER)])
    return [] if got == [] else [f"a pad inside the clamp was refused: {got}"]


_CLAMP_CASES = (
    ("the_installation_writer_refuses_iron_era", _c_the_installation_writer_refuses_iron_era),
    ("NEUTER_no_clamp_terms_iron_era_is_WRITTEN", _c_NEUTER_no_clamp_terms_iron_era_is_WRITTEN),
    ("the_sample_count_alone_carries_it", _c_the_sample_count_alone_carries_it),
    ("a_pad_that_fits_SURVIVES", _c_a_pad_that_fits_SURVIVES),
)


def selftest() -> int:
    bad_total = 0
    for title, cases in (("pad extent", _PAD_EXTENT_CASES),
                         ("installation write path", _WRITE_PATH_CASES),
                         ("installation clamp", _CLAMP_CASES),
                         ("declared duplicate", _DUPLICATE_CASES)):
        bad = 0
        for name, fn in cases:
            try:
                problems = fn()
            except BaseException as e:                     # noqa: BLE001
                problems = [f"raised {type(e).__name__}: {e}"]
            bad += bool(problems)
            print(f"  {'ok  ' if not problems else 'FAIL'}  {name}")
            for p in problems:
                print(f"          {p}")
        print(f"\n  {title} selftest {len(cases) - bad}/{len(cases)}  "
              f"{'PASS' if not bad else 'FAIL'}\n")
        bad_total += bad
    return 0 if not bad_total else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("op", choices=["plan", "apply"])
    ap.add_argument("--preset")
    ap.add_argument("--placements",
                    help="placements.yaml path; overrides --world/--preset, "
                         "for ordering experiments on a throwaway pad")
    ap.add_argument("--id", action="append", required=True,
                    help="placement id; repeat to flatten several pads in one pass. "
                         "Each pad is its OWN ledger record, written in the order "
                         "given, so two pads in one zone compose through the ledger")
    worldseed.add_arguments(ap)
    ap.add_argument("--paint", default="paved_cleared", choices=sorted(tcdata.PAINTS))
    ap.add_argument("--apron", type=float, default=0.0,
                    help="extra metres of levelled ground outside the costed footprint")
    ap.add_argument("--locations", default=None,
                    help="per-type location dump from tools/seedscan (default: "
                         "THIS seed's, `location_dump_path`)")
    ap.add_argument("--actor",
                    help="the agent running this. REQUIRED for apply: every pad is "
                         "appended to the world's ledger before it is sent "
                         "(INSTALLATION_WRITE_PATH_DEPENDS_ON)")
    args = ap.parse_args()
    seed_, world_ = worldseed.apply(args)

    if not args.preset and not args.placements:
        ap.error("one of --preset or --placements is required")
    if args.op == "apply" and not args.actor:
        ap.error("apply writes through the ledger (ledger/live.py LiveBuilder) and "
                 "the record carries who ran it: pass --actor. A pad the ledger "
                 "never saw is a pad the next write in its zone erases -- "
                 "MEASURED 10 of 36 installation-pad zones on the current Fresh42 "
                 "route")
    preset = args.preset or Path(args.placements).parent.name
    places, duplicates = pads_to_write(
        [placement(world_, args.preset, pid, args.placements) for pid in args.id])
    for dup in duplicates:
        print(f"{dup['id']}: SKIPPED -- {dup['why']}; nothing levelled")
    if not places:
        return 0
    zone_set: set[tuple[int, int]] = set()
    spans: list[tuple[dict, list[tuple[int, int]]]] = []
    for place in places:
        solved = place["solved"]
        w, d = pad_extent(place)
        zones = zones_for(float(solved["x"]), float(solved["z"]),
                          w + 2 * args.apron, d + 2 * args.apron)
        spans.append((place, zones))
        zone_set.update(zones)

    out = SCRATCH / f"patch_{args.id[0]}.bin"
    patches = run_patchscan(sorted(zone_set), seed_, out)

    # ONE STAMP PER PLACEMENT.  This used to merge every --id into one shared
    # compiler dict in-process, which composed two pads correctly INSIDE one
    # call and erased the first of them across two -- and buildstage runs one
    # --id per call.  The ledger is what composes pads now, for every caller.
    builds: list[tuple[dict, dict, dict, dict]] = []
    uncovered: list[dict] = []
    for place, zones in spans:
        comps: dict[tuple[int, int], tcdata.Compiler] = {}
        op_y: dict[tuple[int, int], float] = {}
        stats = build(place, patches, zones, args.paint, args.apron, comps, op_y)
        builds.append((place, comps, op_y, stats))
        solved = place["solved"]
        print(f"placement {stats['id']} at ({solved['x']}, {solved['z']}) "
              f"pad {stats['pad_w']} x {stats['pad_d']} m target_y {stats['target_y']}")
        print(f"  zones {zones}, {stats['samples']} samples, cut {stats['max_cut']} m "
              f"fill {stats['max_fill']} m, {stats['over_clamp']} beyond the +/-8 m apply clamp")
        for check in stats["checks"]:
            print(f"  check zone {check['zone']} sample {check['sample']} at "
                  f"({check['world'][0]:.1f}, {check['world'][1]:.1f}): generated "
                  f"{check['generated_m']} + delta {check['level_delta_m']} = {check['result_m']}")
        cover = placed_body_cover(place, args.apron)
        print(f"  body cover {cover['verdict']}: "
              + (f"{cover['unstamped']} of {cover['vertices_under_body']} vertices "
                 f"under the body at yaw {cover['yaw']} ({cover['body_at_yaw'][0]} x "
                 f"{cover['body_at_yaw'][1]} m) unstamped, overhang x "
                 f"{cover['overhang_x_m']} z {cover['overhang_z_m']} m"
                 if "unstamped" in cover else cover.get("why", "")))
        if cover["verdict"] != "covered":
            uncovered.append(cover)

    # THE PAD HAS TO BE UNDER THE BODY.  See PAD_EXTENT_DEPENDS_ON.  Offline,
    # so before any round trip: a pad that leaves a vertex under its own body
    # at natural height is a building standing on ground nobody levelled, and
    # nothing downstream sees it - the gate's pad populations are the pad's
    # rectangle and the pad's writes, never the body's ground.
    if args.op == "apply" and uncovered:
        print("REFUSED: " + "; ".join(
            f"{c['id']} {c['verdict']}"
            + (f" ({c['unstamped']} vertices under its body at yaw {c['yaw']} "
               f"unstamped, overhang x {c['overhang_x_m']} z {c['overhang_z_m']} m)"
               if "unstamped" in c else f" ({c.get('why', '')})")
            for c in uncovered)
            + ". Nothing was written. Re-solve the pad from the body's footprint "
              "AT ITS YAW (flatten.pad_extent_at_yaw); do not delete the building.")
        return 1

    # THE GROUND HAS TO REACH THE DATUM.  See tcdata.PAD_CLAMP_DEPENDS_ON.  The
    # line above has always PRINTED over_clamp; iron-era-workshop printed 442
    # and was written anyway (Fresh42 seq 561).  Offline, before any round trip.
    unreachable = clamp_refusals(builds)
    if args.op == "apply" and unreachable:
        print("REFUSED: " + "; ".join(
            f"{v['id']} ({v['over_clamp_samples']} samples beyond the "
            f"+/-{v['clamp_m']:g} m apply clamp, cut {v['max_cut_m']} m, fill "
            f"{v['max_fill_m']} m)" for v in unreachable)
            + ". Nothing was written. The engine would clamp the delta and the "
              "ground would stop short of the datum; re-solve the placement "
              "(stage 7 ranks a clamp breach behind every site that fits and "
              "refuses the unit by name when none does).")
        return 1

    if args.op == "plan":
        for place, comps, op_y, _stats in builds:
            path, entries = write_entries(place["id"], comps, op_y, world=world_,
                                          watched=False)
            print(f"data entries -> {path}")
            for e in entries:
                print(f"  {e['entry']}: {e['blob_bytes']} B gzip, "
                      f"{e['counts']['height_modified']} samples, selfcheck {e['selfcheck']}")
            # A zone holds one compiler, so an existing one is replaced rather
            # than doubled: `deleteObjects -zone` is scoped to exactly the zones
            # being written -- which is why `apply` unions the ledger's claims
            # in those zones first.
            for e in entries:
                print(f"  deleteObjects -zone {e['zone'][0]} {e['zone'][1]} "
                      f"-prefab _TerrainCompiler -force")
            for e in entries:
                print(f"  spawn_object _TerrainCompiler from={e['centre'][0]:g},"
                      f"{e['centre'][1]:g},0 data={e['entry']}")
        return 0
    return _apply_through_ledger(args, world_, preset, builds, patches)


def _apply_through_ledger(args, world_: str, preset: str, builds: list,
                          patches: dict) -> int:
    """Step 2, THROUGH THE LEDGER: one `terrain_write role=site_pad` per
    placement, appended before it is sent.  See
    INSTALLATION_WRITE_PATH_DEPENDS_ON."""
    zones = sorted({z for _p, comps, _o, _s in builds for z in comps})
    with live_builder()(actor=args.actor, world=world_) as b:
        # THE ORDER IS A PRECONDITION, checked before ANYTHING is written.
        # Every zone about to receive a compiler must already be generated:
        # see `ungenerated_zones`.  Checked rather than trusted, because
        # clearing and flattening are separate commands and nothing else stops
        # them being run the wrong way round.  The readings are KEPT: each
        # entry of the record names the command that observed its zone.
        probes = zone_probes(b.srv.rc, zones)
        missing = [z for z, p in probes.items() if p["count"] == 0]
        if missing:
            print(f"REFUSED: zones {missing} have never been generated (no {ZONE_CTRL} at "
                  f"their centres), and nothing was written. Writing terrain into an "
                  f"ungenerated zone makes the zone plant its vegetation on the UNMODIFIED "
                  f"generated height, leaving it standing in the air over the finished pad "
                  f"-- MEASURED to 7.68 m at zone (-18, 28) on 2026-09-15. Run "
                  f"clearing/clear.py (zones_generate) for this pad first.")
            return 1
        # Second gate: a terrain write is destructive on its own, and
        # `clearing/area.py`'s stand-off only runs when clearing runs.  NO
        # OVERRIDE: the ledger's schema refuses a terrain_write whose
        # location_check verdict is not `clear`, for every writer alike.
        checks: dict[str, dict] = {}
        refused = False
        for place, *_ in builds:
            hits, text, checks[place["id"]] = pad_location_record(
                b.srv.rc, place, args.apron,
                Path(args.locations) if args.locations
                else location_dump_path(worldseed.seed()))
            print(f"  location check {place['id']}:")
            print(text)
            refused |= bool(hits)
        if refused:
            print("REFUSED: the pad rectangle reaches a generated location's stand-off, "
                  "and nothing was written. MEASURED on 2026-09-15: a flatten cut 6.65 m "
                  "out from under a LocationProxy holding a TreasureChest_meadows_buried "
                  "without complaining, and marker-based stand-off had already deleted "
                  "POI content in the pre-wipe world because a location's PIECES reach "
                  "past its marker. Re-solve the placement further out or shrink the apron.")
            return 1
        for place, comps, op_y, stats in builds:
            rec = installation_pad_record(
                b, place, preset, comps, op_y, patches,
                {z: probes[z] for z in comps}, checks[place["id"]], stats,
                paint=args.paint, apron=args.apron)
            res = install_emit(b, rec)
            print(f"  {place['id']}: terrain_write seq {res.get('seq')} "
                  f"{res.get('status')}, zones "
                  f"{[e['zone'] for e in rec['params']['entries']]}, merged "
                  f"{rec['meta']['merged'] or 'nothing'}")
            for c in rec["wire"]:
                print(f"    {c}")
        print("   ", b.srv.command("save").strip())
    return 0


if __name__ == "__main__":
    sys.exit(selftest() if sys.argv[1:2] == ["selftest"] else main())
