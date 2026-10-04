#!/usr/bin/env python3
"""Furnish and stock a flattened site: stations, props, chests, and chest contents.

WHAT THIS OWNS, AND WHAT IT DOES NOT
------------------------------------
The BLUEPRINT owns the POSITION of everything it carries. This file owns only
what the blueprint does not carry, plus every chest's CONTENTS.

That split is measured, and getting it wrong is what the operator walked into.
MEASURED on `pre-bonemass/iron-era-workshop` on 2026-09-15, box-testing what
this file placed against all 1,906 solids of the body that was placed before it:
12 of its 18 fixtures were INSIDE the structure. `portal_wood` at pad-offset
(-6.00, 0.00) overlapped a `stone_wall_4x2` by 1.00 x 2.00 x 1.18 m -- the
operator's "there are portals in the middle of walls". `piece_maypole` pierced
22 solids, `smelter` 20, `piece_workbench` 11; the minimum fixture-to-wall gap
across the whole set was 0.000 m, because nothing was checking.

Two separate faults, both fixed here:

  * DUPLICATION. The pad offsets were solved when the body was anchored on its
    own corner and the middle of the pad was empty. They are also spawns of
    prefabs the body already has: this body carries all NINE of the preset's
    stations plus `piece_cartographytable`, so those spawns were second copies
    standing in the walls of the first. `blueprints/fixtures.carried()` is now
    the authority, and a station the blueprint carries is NOT re-placed -- only
    the extensions needed to reach the preset's required LEVEL are added, beside
    the blueprint's own station, at that station's own floor height.
  * POSITION. What genuinely is missing (`fire_pit`, the beds, `piece_maypole`,
    the bulk-material chest bank) is placed at a position SEARCHED against the
    body's real solids -- `blueprints/fixtures.free_spot()` -- and then audited,
    rather than at an offset that was once true.

Three things this does that a blueprint paste cannot:

  * STATIONS AT THE REQUIRED LEVEL.  A preset names each station with the level
    it must reach, and a station's level is 1 + the number of StationExtension
    pieces in range (CraftingStation::GetLevel).  There is no "level" field on
    the ZDO to set -- the game counts neighbours at runtime -- so reaching
    level 3 means placing two extensions close enough to be counted.
  * CHESTS WITH CONTENTS.  `spawn` carries no object data, so a pasted
    blueprint's chests arrive empty and the blueprint's own `data` column is
    lost.  Chests are therefore placed here and filled with
    `addItemToContainer`, which is the one RCON verb that writes inventory.
  * READ-BACK.  Every chest is re-read with `showContainer` and the reported
    stacks are compared against what was requested, because "the add command
    answered OK" and "the chest holds it" are different claims.

Capacity is discovered, not assumed: items are added until the container
refuses, then the next chest takes over.

WRITE PATH
----------
Every spawn is `flatten.install_emit` inside one `LiveBuilder` session under
`--actor`: the record (`fixture_record`: op `spawn`, or `portal` for a tagged
portal, role `site_prop`, `site_id` = the placement id) is appended to the
world's ledger FIRST, then sent as `spawn_object`, then its postcondition is
measured. Step 6 used to drive raw RCON and append nothing, so every later
guard that reads the ledger -- the ground freeze, the two-ends-per-tag portal
guard, buildstage replay -- was blind to it. See `terraform/flatten.py::
INSTALLATION_WRITE_PATH_DEPENDS_ON`. The chest FILL is the one write that is
not a ledger op (there is no container postcondition); each chest's
read-back is appended as a MEASURED `observe` instead.

`--plan` does the whole placement decision offline -- body transform, carried
census, free-spot search, audit -- and touches no server, so what this file is
about to do can be inspected and tested without a world.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
JUMPSTART = HERE.parent
sys.path.insert(0, str(JUMPSTART / "blueprints"))
sys.path.append(str(JUMPSTART))

from rcon import Rcon  # noqa: E402
import fixtures  # noqa: E402  (jumpstart/blueprints/fixtures.py)
import to_rcon_plan  # noqa: E402
import flatten  # noqa: E402  THE placements reader
import worldseed  # noqa: E402

ADDED_RE = re.compile(r"Item (\S+) x(\d+) added to")
# showContainer prints a header line, then "Items (N):", then one line per slot:
#   [0] Coal Stack: 50 Quality: 1 Crafter: Server (-1)
# MEASURED on Ulfsland.  The Stack figure is the important one, because
# addItemToContainer reports the count it was ASKED for and adds one stack.
STACK_RE = re.compile(r"^\[(\d+)\]\s+(\S+)\s+Stack:\s*(\d+)\s+Quality:\s*(\d+)")

CHEST_PREFAB = "piece_chest"

# Props every finished base wants and no preset spells out piece by piece.
#
# The offsets are PREFERENCES, not positions: the placement search starts from
# them and moves until the prop clears the building. They were positions once,
# and that is precisely how `portal_wood` ended up inside a `stone_wall_4x2`.
# A prop whose prefab the blueprint already carries is not placed at all.
#
# THE ORDER OF THE INDOOR PROPS IS A DECISION, not a list. Indoor space is
# scarce and the first claim wins it: MEASURED on `halvar-master-refinery`,
# exactly ONE of its 39 indoor covered cells can take a `bed` and the same
# region is the only one that can take a `fire_pit`, so whichever is listed
# first gets the room and the other is refused. With `fire_pit` first the site
# came out with a fire pit indoors and BOTH beds refused. A bed is a spawn
# point -- it is the single most load-bearing fixture a base has -- and a fire
# pit is warmth and light next to it, so the beds are claimed first and the
# fire pit takes what is left. Stations come before props either way, which is
# also deliberate: a `hearth` the PRESET asks for outranks a decorative pit.
PROPS = [
    ("portal_wood", (6.0, 0.0), 0.0),
    ("bed", (-4.0, 4.0), 0.0),
    ("bed", (-6.0, 4.0), 0.0),
    ("fire_pit", (0.0, 4.0), 0.0),
    ("piece_cartographytable", (4.0, -4.0), 180.0),
    ("piece_maypole", (0.0, -8.0), 0.0),
]

# How far a StationExtension may sit from its station and still be counted.
#
# MEASURED from the deployed `assembly_valheim.dll`, so it is the game's rule
# and not a guess: `StationExtension::.ctor` sets `m_maxStationDistance` to
# `ldc.r4 5.` (5.0 m), and `CraftingStation::FindStationsInRange` accepts a
# station when `Vector3.Distance(station.transform.position, center) < maxRange`
# -- full 3-D distance, STRICT less-than. That number was previously written
# down as "not readable from RCON": true, but it is readable from the assembly,
# and a bound nobody measured is how a placement ends up legal and useless.
MAX_STATION_DISTANCE_M = 5.0

# The search half-width, deliberately well inside the game's 5.0 m: an extension
# is meant to stand BESIDE its station, and the second pass widens to 3.5 m only
# because MEASURED on `deepnorth-sandbox/complete-station-hub` the tighter one
# leaves two extensions with nowhere to stand at all.
EXT_SEARCH_M = 2.5


def rotate(dx: float, dz: float, yaw_deg: float) -> tuple[float, float]:
    """Unity yaw: +yaw turns clockwise seen from above, i.e. x' = x cos + z sin."""
    import math

    r = math.radians(yaw_deg)
    return dx * math.cos(r) + dz * math.sin(r), -dx * math.sin(r) + dz * math.cos(r)


def load_preset(preset: str) -> dict:
    return yaml.safe_load((JUMPSTART / "presets" / f"{preset}.yaml").read_text())


def load_manifest(world: str, preset: str) -> dict:
    path = JUMPSTART / "worlds" / world / preset / "settings" / "chest-manifest.yaml"
    return yaml.safe_load(path.read_text())


def load_placement(world: str, preset: str, pid: str) -> dict:
    # Through THE placements reader, so the records' own `world:`/`seed:`
    # header is read.  This stage SPAWNS fixtures at these coordinates.
    doc = flatten.load_placements(world, preset)
    for p in doc["placements"]:
        if p["id"] == pid:
            return p
    raise SystemExit(f"no placement {pid}")


def load_body(place: dict, cx: float, cz: float, pad_y: float, yaw: float):
    """The placed blueprint this site is being stocked around.

    Returns None when the placement names no blueprint -- a bare pad still gets
    stocked, it just has nothing to collide with. The transform is
    `fixtures.place_body`, which is the SAME arithmetic `to_rcon_plan.py` emits
    with, so the solids tested against are the solids in the world. Verified:
    reconstructing eight of this body's fixtures and comparing against their
    positions read back from the live server agrees to 0.007 m.
    """
    name = place.get("blueprint")
    if not name:
        return None
    # Resolved by the library's own resolver, not by root order: MEASURED, 26
    # file names exist in both corpus roots and `PuP_Minicastle.blueprint` is
    # two DIFFERENT castles, 3,334 rows against 3,810. Fixtures are audited
    # against this body's solids, so resolving the wrong one is how a station
    # ends up inside a wall that the audit believed was somewhere else.
    # `locate_by_filename` verifies the SHA-256 and refuses an ambiguous name.
    #
    # Loaded by PATH: `blueprints/materialise.py` (the body copier) and
    # `library/materialise.py` (the resolver) share a module name, and
    # `blueprints` is already on sys.path here because `fixtures` lives there.
    import importlib.util  # noqa: PLC0415

    spec = importlib.util.spec_from_file_location(
        "library_materialise", JUMPSTART / "library" / "materialise.py")
    lib = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lib)
    locate_by_filename = lib.locate_by_filename

    path = locate_by_filename(name)
    if path is None:
        raise SystemExit(f"placement {place['id']} names blueprint {name}, which the "
                         f"library cannot resolve to a body; cannot check fixtures "
                         f"against it")
    datum = place.get("blueprint_datum") or {}
    override = datum.get("override_base_y")
    objs = to_rcon_plan.read_objects(path)
    # The placement's `drop_prefabs` never reached the world, so they are not
    # solids to keep clear of and not stations the body CARRIES. Both halves
    # matter: `blueprints/fixtures.carried()` reads this same object list to
    # decide which of the preset's stations already exist, so a dropped
    # `piece_artisanstation` left in here would suppress nothing (the preset
    # does not ask for one) while a dropped station the preset DOES ask for
    # would make stock skip the only copy and leave the site without it.
    drop = set(place.get("drop_prefabs") or [])
    # And the EMITTER's own drop: `to_rcon_plan` never emits a carried
    # portal, so none stands and none is carried -- counting one here would
    # make stock skip the site's only portal as "the blueprint's".
    if to_rcon_plan.CARRIED_PORTALS == "drop":
        drop |= set(fixtures.PORTAL_PREFABS)
    objs = [o for o in objs if o.prefab not in drop]
    return fixtures.place_body(objs, at=(cx, pad_y, cz), yaw_deg=yaw,
                               base_y=None if override is None else float(override))


def indoor_centre(mask, cx: float, cz: float) -> tuple[float, float] | None:
    """The middle of the largest indoor covered region, as a world point.

    An INDOOR fixture searched from a pad-relative offset starts outside the
    walls and walks in, which is how `free_spot`'s nearest-first search gets
    to the doorway cell and stops. Starting from the middle of the interior
    means "near a wall" is measured against the room the fixture is in.
    """
    cells = mask.of_class(fixtures.INDOOR_COVERED) if mask else []
    if not cells:
        return None
    mx = sum(c[0] for c in cells) / len(cells)
    mz = sum(c[1] for c in cells) / len(cells)
    return (cx + mx * mask.cell_m, cz + mz * mask.cell_m)


# ---------------------------------------------------------------------------
# A FIXTURE STANDS ON THE GROUND THAT WAS LEVELLED FOR IT.
#
# Every fixture this file places stands at the PAD height (`fixtures.
# fixture_box`: its y IS the surface it stands on).  The search reached the
# SITE square, `requirement.footprint_m / 2`, while `flatten.build` levels
# the body's rectangle.  MEASURED on Fresh42 drive 2, against the drive-2
# applied ground (PatchScan heightmap + every ledger terrain_write):
#   * pre-queen#mistlands-blackforge-base, 18.8 x 12.8 m pad, footprint_m
#     80: 28 of 32 fixtures off the pad, from 2.95 m in the air (the forge
#     row on ground 37.62) to 4.71 m under the bank; the cartography table
#     (seq 1978, offset (-4, +8)) 1.6 m past the pad's edge with its top
#     1.982 m below the ground;
#   * the portal hall's own offline solve, 34.8 m pad, footprint_m 50: 15 of
#     16 stations, 18 of 19 extensions, 2 of 11 installation arches and 13 of
#     16 settlement arches off it -- u-lhwest's 3.29 m arch under 3.46 m of
#     hillside.
#
# TWO CURES WERE MEASURED, AND THE PAD WON.
#   * Seat each fixture on the ground under it, refused where that ground is
#     not level (0.5 m, `base_geometry.BASE_PROFILE_FLAT_TOL_M`): blackforge
#     32 of 32 -- but its station row then stands 2.2-2.9 m below the pad,
#     1.6 m from its edge, across a bank a player cannot walk up from the
#     building's open face; the hall 11 of 11 installation arches but 2 of
#     16 settlement arches (0 of 16 at 0.25 m), because the ground round it
#     rises 5.6 m and the stations take the pad's corners first.
#   * Level the ground where they go (`flatten.PAD_EXTENT_DEPENDS_ON`
#     `fixture_ring`, sized by `fixture_ring` below at stage 7): every
#     fixture of both sites on the pad, inside the +/-8 m clamp (blackforge a
#     44.8 x 34.8 m pad, cut 7.14 m; the hall 56.8 m, cut 6.25 m), for 4,028
#     against 399 m3 and 5,214 against 1,095 m3 of earthwork.
# So the pad is sized for its ring, and a fixture the written pad does not
# reach is REFUSED with its number (`seat_miss`) rather than stood at the
# pad height over ground nobody levelled.  Where the clamp forbids the ring
# pad, stage 7 writes the body's pad and says so, and the fixtures that
# needed the ring are the ones refused here.
#
# SEAT_DEPENDS_ON = ("written_pad",): `SEAT_MODE` "site_square" is the
# shipped rule -- anywhere in the site square, at the pad height -- and
# exists for the neuter in this module's selftest.
# ---------------------------------------------------------------------------
SEAT_DEPENDS_ON = ("written_pad",)
SEAT_MODE = "written_pad"


class PadSeat:
    """`seat(foot)` for `fixtures.free_spot(ground=...)`: the pad height when
    every lattice vertex the engine answers the footprint `(xmin, xmax, zmin,
    zmax)` with is one `flatten.build` stamps for this pad (`flatten.stamps`
    over `flatten.answering_vertices`, the writer's own predicate), else
    None."""

    def __init__(self, cx: float, cz: float, w: float, d: float,
                 pad_y: float, apron_m: float = 0.0):
        self.cx, self.cz, self.w, self.d = float(cx), float(cz), w, d
        self.pad_y, self.apron_m = float(pad_y), apron_m

    def miss(self, foot) -> dict:
        """How far this footprint stands off the pad: vertices under it,
        how many the pad does not stamp, and the metres its farthest vertex
        lies beyond the pad's edge."""
        hw, hd = self.w / 2 + self.apron_m, self.d / 2 + self.apron_m
        s = flatten.tcdata.SCALE
        xs = flatten.answering_vertices(foot[0], foot[1])
        zs = flatten.answering_vertices(foot[2], foot[3])
        sx = sum(1 for v in xs if flatten.stamps(v * s - self.cx, hw))
        sz = sum(1 for v in zs if flatten.stamps(v * s - self.cz, hd))
        out = max([0.0] + [abs(v * s - self.cx) - hw for v in xs]
                  + [abs(v * s - self.cz) - hd for v in zs])
        return {"vertices": len(xs) * len(zs),
                "unstamped": len(xs) * len(zs) - sx * sz,
                "outside_pad_m": round(out, 3),
                "pad": [self.w, self.d], "pad_centre": [self.cx, self.cz]}

    def __call__(self, foot) -> dict | None:
        if self.miss(foot)["unstamped"]:
            return None
        return {"y": self.pad_y, "on": "written_pad"}


def site_seat(si: dict) -> PadSeat | None:
    """The seat for a site from its `site_inputs`: its written pad, or None
    under the shipped rule or where no pad is recorded."""
    if SEAT_MODE != "written_pad" or si.get("pad") is None:
        return None
    return PadSeat(si["cx"], si["cz"], *si["pad"], si["base_y"])


def foot_of(p: dict) -> tuple[float, float, float, float]:
    """A solved fixture's TIGHT xz footprint, `fixture_box` without margin."""
    b = fixtures.fixture_box(p["prefab"], p["x"], p["y"], p["z"],
                             p.get("yaw", 0.0))
    return (b[0], b[1], b[4], b[5])


def seated_spot(seat, prefab: str, *a, **kw):
    """`fixtures.free_spot` on the levelled ground: `(found, unseated)`.
    `unseated` is the refusal WITH ITS NUMBER when the site square holds a
    legal spot and the written pad does not: that spot, and how far off the
    pad it stands (`PadSeat.miss`).  None when there is nothing to refuse."""
    found = fixtures.free_spot(prefab, *a, ground=seat, **kw)
    if found is not None or seat is None:
        return found, None
    shipped = fixtures.free_spot(prefab, *a, **kw)
    if shipped is None:
        return None, None
    return None, {"prefab": prefab,
                  "site_square_spot": [shipped["x"], shipped["y"],
                                       shipped["z"]],
                  **seat.miss(foot_of(shipped))}


def unseated_reason(u: dict) -> str:
    return (f"not on levelled ground: the written {u['pad'][0]:g} x "
            f"{u['pad'][1]:g} m pad does not reach it -- its spot in the site "
            f"square stands {u['outside_pad_m']} m past the pad's edge with "
            f"{u['unstamped']} of {u['vertices']} vertices under it unlevelled")


# ---------------------------------------------------------------------------
# AN ARCH IS A DOOR, AND A DOOR KEEPS ITS WALK-IN -- BY THE GATE'S OWN RULE.
#
# MEASURED on Fresh42 drive 3, gate `portals` approach_ray on the frozen save
# 05eb7c6c05de: 10 of the hall's 25 arches had no clear walk-in on either
# face, and every blocker was OURS.  u-wtspawn (-206.87, -396.39): +Z
# stopped at 4.25 m and -Z at 5.25 m, both by a neighbouring portal_wood;
# u-sudrberg (-201.87, -403.39): +Z stopped at 3.75 m by a board 0.497 m
# into the walker, -Z at 3.75 m by an arch; u-stathub, u-meadhall and
# u-blacbase stood 2 m apart on ONE facing axis, indoors, each in the
# other's doorway.  The packing tested every arch as a BOX among boxes:
# nothing overlapped, so nothing was wrong, and the doors faced each other.
#
# So every fixture a solve stands -- `solve_placement`, `hub_arch`, and the
# hall's settlement arches and boards (`settlements/build.py::
# pack_settlement_arches` through `hub_arch` and `waypoints.
# portal_guidepost`) -- is admitted only if, with it standing, EVERY arch
# still has at least one face a walker walks in on.  That is `accept/
# sections/portals.py::check_approach`'s own question, asked with its own
# instrument: its APPROACH_SPAN_M / APPROACH_START_M / APPROACH_STEP_M,
# `_approach.approach_reach` sweeping the walker over the gate's own
# `instruments.Solid`s, and the capsule `fixtures.walker_capsule` reads off
# the Player prefab -- the 0.490 x 1.850 m the drive-3 gate printed.  An arch
# with no such spot left is REFUSED with its number (`Walkins.refusal`),
# and the ring (`fixture_ring`) is solved the same way, so stage 7 sizes the
# pad for the arches' walk-ins as well as for the arches.
#
# WALKIN_DEPENDS_ON = ("reserved_walkins",): WALKIN_MODE "none" is the
# shipped packing -- an arch a box among boxes -- for the neuters in this
# module's and `settlements/build.py`'s selftests.
#
# AND THE BOOK HOLDS WHAT THE GATE WALKS THROUGH THAT WE DID NOT PLACE.
# MEASURED on Fresh42 drive 10, gate `portals` approach_ray on the frozen
# save 3fba48fcd1e3: u-stathub (94.50, 348.50) of deepnorth-sandbox/
# complete-station-hub, ONE row.  Its +Z face was stopped at 4.00 m by a
# Birch1 whose pivot stands 31.5 m from the pad centre -- past the 26.73 m
# step-1 clearing, its mesh box reaching the corridor -- and its -Z face at
# 1.25 m by two of OUR chests (seq 2736, 2737: the bank's last two, 2 and 4
# m out on that axis).  The book was solved against the body alone, so it
# believed the +Z face open, admitted the chests on the -Z one as "not the
# LAST open face", and the tree closed the other.  The settlement site end
# had been given the seed in drive 4 (`settlements/build.py::SITE_SEED_
# DEPENDS_ON`) and the installations' step 6 never was.
#
# SITED, NOT CLEARED, as at the site end: the step-1 clearing is the pad's
# own cylinder and what lies past it is the seed's -- a corridor cleared
# round every arch would delete ZDOs on mesh bounding boxes the gate itself
# books as upper bounds.  The seed round the pad is read off the LIVE world
# before the solve (`read_seed`, `settlements/build.py::seed_census`) and
# held in the book, so a fixture that would close the last face the trees
# left is moved, never the tree.
#
# THE SEED IS `settlements/build.py::NATURAL_RE`, the clearing's own list of
# what may be deleted -- the trees, rocks, bushes and pickables.  MEASURED
# over the frozen save's 11 stocked sites: the book finds a placeable spot
# for every arch (0 refused, every fixture still stands) and moves three
# advisory ones off their forest spots (u-meadhall 1.4 m, u-dnshrine 11 m,
# u-mistfrge 7.1 m).  Every non-build mesh object held instead (SnowFirTree,
# cliff_*) REFUSES u-dnshrine's only arch -- a mesh box on every face of its
# pad, which the gate books as an upper bound and reports, not counts -- and
# leaves three sites' discs too dense for one bounded listing to read whole.
#
# SEED_DEPENDS_ON = ("seed_objects",): SEED_MODE "none" is the book over the
# body alone -- the neuter in this module's selftest, which stands the chests
# where drive 10 stood them.  `--plan` reads no world, and says so.
# ---------------------------------------------------------------------------
WALKIN_DEPENDS_ON = ("reserved_walkins",)
WALKIN_MODE = "reserved"
SEED_DEPENDS_ON = ("seed_objects",)
SEED_MODE = "read"

_GATE_GEOM: dict = {}


def _gate():
    """The acceptance gate's walk-in, imported rather than restated:
    `(instruments, _approach, portals)`."""
    from accept import instruments as I  # noqa: PLC0415
    from accept.sections import _approach as A  # noqa: PLC0415
    from accept.sections import portals as GP  # noqa: PLC0415
    if not _GATE_GEOM:
        _GATE_GEOM.update(I.load_geometry()[0])
    return I, A, GP


def _body_solid(I, s):
    """A body solid (`fixtures.Solid`) as the gate's `instruments.Solid`:
    the corners of its ORIENTED boxes, exact for a box collider and the
    collider's own local box otherwise -- never smaller than the gate's."""
    parts = []
    for c, axes, ext in s.obbs:
        corner = []
        for a in (-1.0, 1.0):
            for b in (-1.0, 1.0):
                for d in (-1.0, 1.0):
                    corner += [c[k] + a * ext[0] * axes[0][k]
                               + b * ext[1] * axes[1][k]
                               + d * ext[2] * axes[2][k] for k in range(3)]
        parts.append(["p", *corner])
    if not parts:
        parts = [["b", s[0], s[2], s[4], s[1], s[3], s[5]]]
    return I.Solid(s.prefab, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), parts,
                   True)


def gate_solids(body) -> list:
    """Every solid of a placed body as the gate's `instruments.Solid`."""
    I, _A, _GP = _gate()
    return [_body_solid(I, s) for s in body.solids]


def gate_solid(prefab: str, x: float, y: float, z: float, quat) -> object:
    """A standing object -- a seed tree or rock read off the world -- as the
    gate's `instruments.Solid`, from the gate's own geometry and the object's
    own rotation.  A prefab the geometry does not know is an UNMEASURED
    solid, exactly as the gate books it."""
    I, _A, _GP = _gate()
    parts = _GATE_GEOM.get(prefab)
    return I.Solid(prefab, (float(x), float(y), float(z)),
                   tuple(float(v) for v in quat), parts or [],
                   parts is not None)


class _Near:
    """`instruments.Grid.near` over several populations at once."""

    def __init__(self, grids=(), extra=()):
        self.grids, self.extra = grids, list(extra)

    def near(self, x: float, z: float, r: float) -> list:
        out = list(self.extra)
        for g in self.grids:
            out += g.near(x, z, r)
        return out


class Walkins:
    """Every arch's walk-in on one site, as the gate walks it (see above).

    A row is `(prefab, x, y, z, yaw)` with `y` the surface it stands on.
    `admit(rows, pending)` -- may these stand, `pending` standing too, and
    every arch keep a face?  `commit(rows)` stands them.  `accept` is the
    `fixtures.free_spot` veto; `refusal()` is the number an arch that found
    no admissible spot is refused with.

    `extra` is everything else standing on the site that an arch must keep a
    face against, as the gate's own `instruments.Solid`s: the settlement's
    other bodies (`gate_solids`) and the seed's own trees and rocks read off
    the world (`gate_solid`).  The gate walks the walker through all of them;
    a book that knows only the anchor body passes an arch the gate refuses."""

    def __init__(self, body=None, extra=()):
        I, A, GP = _gate()
        self._I, self._A = I, A
        self.arch = GP.PORTAL_PREFAB
        self.span = GP.APPROACH_SPAN_M
        self.start = GP.APPROACH_START_M
        self.step = GP.APPROACH_STEP_M
        self.radius, self.height = fixtures.walker_capsule()
        self._body = I.Grid([_body_solid(I, s)
                             for s in (body.solids if body else ())]
                            + list(extra), cell=4.0)
        self._placed: list = []
        self._grid = I.Grid([], cell=4.0)
        self._open: list[tuple] = []        # (arch Solid, open faces)
        self.begin()

    def fork(self) -> "Walkins":
        """An independent copy: a caller that continues a memoised packing
        must not stand its arches in the memo's book."""
        other = object.__new__(Walkins)
        other.__dict__.update(self.__dict__)
        other._placed = list(self._placed)
        other._open = list(self._open)
        other.begin()
        return other

    # -- the gate's own question ------------------------------------------
    def _solid(self, prefab: str, x: float, y: float, z: float, yaw: float):
        parts = _GATE_GEOM.get(prefab)
        return self._I.Solid(prefab, (float(x), float(y), float(z)),
                             self._I.yaw(float(yaw)), parts or [],
                             parts is not None)

    def _reach(self, s, grid, face: int) -> tuple[float, tuple | None]:
        return self._A.approach_reach(s, grid, s.pos[1], self.radius,
                                      self.height, face, self.span,
                                      self.start, self.step)

    def _passes(self, reach: float) -> bool:
        # `check_approach`'s own pass: `best_reach >= APPROACH_SPAN_M - 1e-9`
        return reach >= self.span - 1e-9

    def _clear(self, s, grid, face: int) -> bool:
        return self._passes(self._reach(s, grid, face)[0])

    def _reaches(self, arch, s) -> bool:
        """Can `s` stand anywhere on `arch`'s two rays?"""
        dx = max(s.xmin - arch.pos[0], 0.0, arch.pos[0] - s.xmax)
        dz = max(s.zmin - arch.pos[2], 0.0, arch.pos[2] - s.zmax)
        return math.hypot(dx, dz) <= self.span + self.radius + 0.01

    def _standing(self, row) -> bool:
        return any(s.name == row[0] and abs(s.pos[0] - row[1]) < 1e-6
                   and abs(s.pos[1] - row[2]) < 1e-6
                   and abs(s.pos[2] - row[3]) < 1e-6 for s in self._placed)

    def faces(self, s, extra=()) -> tuple[int, ...]:
        """The faces of arch `s` a walker walks in on, against the body,
        everything committed and `extra`."""
        grid = _Near((self._body, self._grid),
                     [e for e in extra if e is not s])
        out, best = [], (-1.0, None)
        for face in (1, -1):
            reach, worst = self._reach(s, grid, face)
            if self._passes(reach):
                out.append(face)
            elif reach > best[0]:
                best = (reach, worst)
        self._last_best = best if not out else None
        return tuple(out)

    # -- the book ---------------------------------------------------------
    def admit(self, rows, pending=()) -> bool:
        new = [self._solid(*r) for r in rows]
        pend = [self._solid(*r) for r in pending if not self._standing(r)]
        # 1. nothing new closes the LAST open face of any arch standing (an
        #    arch committed with none -- a caller that did not ask -- has
        #    nothing left to close, and must not veto the whole site)
        watch = list(self._open) + [
            (s, self.faces(s, pend)) for s in pend if s.name == self.arch]
        for s, open_ in watch:
            near = [p for p in new if self._reaches(s, p)] if open_ else ()
            if near and not any(self._clear(s, _Near(extra=near), f)
                                for f in open_):
                return False
        # 2. every new arch has a face of its own against everything
        for s in new:
            if s.name == self.arch and not self.faces(s, pend + new):
                if self._last_best is not None and (
                        self._veto["best_reach_m"] is None
                        or self._last_best[0] > self._veto["best_reach_m"]):
                    worst = self._last_best[1]
                    self._veto.update(
                        best_reach_m=round(self._last_best[0], 3),
                        blocked_by=None if worst is None else worst[1].name)
                return False
        return True

    def commit(self, rows) -> None:
        new = [self._solid(*r) for r in rows]
        self._placed += new
        self._grid = self._I.Grid(self._placed, cell=4.0)
        for i, (s, open_) in enumerate(self._open):
            near = [p for p in new if self._reaches(s, p)]
            if near:
                self._open[i] = (s, tuple(
                    f for f in open_ if self._clear(s, _Near(extra=near), f)))
        for s in new:
            if s.name == self.arch:
                self._open.append((s, self.faces(s)))

    def blocked(self) -> list[tuple]:
        """Arches standing in this book with no face open: `(x, z)`."""
        return [(s.pos[0], s.pos[2]) for s, open_ in self._open if not open_]

    # -- the free_spot veto, and the number a refusal carries -------------
    def begin(self) -> None:
        self._veto = {"vetoed": 0, "best_reach_m": None, "blocked_by": None}
        self._last_best = None

    def accept(self, prefab: str, x: float, y: float, z: float,
               yaw: float) -> bool:
        ok = self.admit([(prefab, x, y, z, yaw)])
        if not ok:
            self._veto["vetoed"] += 1
        return ok

    def refusal(self) -> dict | None:
        """`{vetoed, best_reach_m, blocked_by, span_m, walker_radius_m}` for
        the last search, or None when it vetoed nothing."""
        if not self._veto["vetoed"]:
            return None
        return dict(self._veto, span_m=self.span,
                    walker_radius_m=self.radius)


def walkin_reason(w: dict) -> str:
    return (f"no walk-in: {w['vetoed']} otherwise legal spot(s) refused "
            f"because an arch would be left with no face a "
            f"{w['walker_radius_m']:.2f} m walker walks {w['span_m']:g} m "
            f"into" + (f"; the best of this arch's own reached "
                       f"{w['best_reach_m']:.2f} m, stopped by "
                       f"{w['blocked_by']}" if w["best_reach_m"] is not None
                       else ""))


def book_for(body, seed=()) -> "Walkins | None":
    """The site's walk-in book, or None under the shipped rule.  `seed` is
    the standing trees and rocks round the site (`read_seed`), held under
    SEED_MODE "read"; a caller with no world to read (the ring, the hub
    packing) leaves it empty and its book is over the bodies alone."""
    if WALKIN_MODE != "reserved":
        return None
    return Walkins(body, seed if SEED_MODE == "read" else ())


def walk_reach(half_diagonal_m: float) -> float:
    """How far from a site's centre an arch standing within `half_diagonal_m`
    of it, and the walker walking into it, reach: that distance, the gate's
    span and the walker's radius.  A seed object whose solid comes within it
    can stand in a walk-in of that site; one beyond cannot."""
    _I, _A, GP = _gate()
    r, _h = fixtures.walker_capsule()
    return half_diagonal_m + GP.APPROACH_SPAN_M + r


def seed_reach(si: dict, seat) -> float:
    """`walk_reach` for a site's fixtures: they stand in the site square
    (`pad_half`) and, under a seat, only where the written pad levelled the
    ground -- its half extents and the lattice cell a footprint's vertices
    answer from."""
    hw = hd = si["pad_half"]
    if seat is not None:
        slack = flatten.tcdata.SCALE
        hw = min(hw, seat.w / 2 + seat.apron_m + slack)
        hd = min(hd, seat.d / 2 + seat.apron_m + slack)
    return walk_reach(math.hypot(hw, hd))


def arch_may_stand(portal_tag: str | None,
                   extra_portal_tags: tuple[str, ...]) -> bool:
    """Can this site's solve stand an arch at all?  A portal with no usable
    tag is never placed (`solve_placement`'s `portal_refusal`), so a site
    with neither a usable tag nor an extra one has no walk-in to keep and
    nothing in the world to read for one."""
    return bool(extra_portal_tags) or fixtures.portal_tag_problem(
        portal_tag) is None


def read_seed(world: str, actor: str, si: dict, seat) -> tuple[list, dict]:
    """The seed round a site, off the LIVE world, as the gate's own solids:
    `(solids, report)` (`settlements/build.py::seed_census`, which also
    reads the site end's).  Through a session of the one sanctioned kind,
    so the socket is the world's own (`LiveBuilder`'s transport agreement)
    and its console can run `objects_count`; nothing is written."""
    with flatten.live_builder()(actor=actor, world=world) as rb:
        return flatten._settlement_emitter().seed_census(
            rb.srv, si["cx"], si["cz"], si["base_y"], seed_reach(si, seat))


def row_of(p: dict, prefab: str | None = None) -> tuple:
    """A solved fixture as a `Walkins` row."""
    return (prefab or p["prefab"], p["x"], p["y"], p["z"],
            float(p.get("yaw", 0.0)))


def solve_placement(place: dict, preset: dict, n_chests: int,
                    cx: float, cz: float, pad_y: float, yaw: float,
                    pad_half: float, portal_tag: str | None = None,
                    portal_tag_source: str = "none",
                    extra_portal_tags: tuple[str, ...] = (),
                    props=None, body=None, seat=None, walkins=None) -> dict:
    """Decide WHERE everything goes, before anything is spawned.

    Offline and total: every station, extension, prop and chest is either
    declared owned by the blueprint, given a position that has been box-tested
    against the body AND satisfies its declared placement preference, or
    reported as unplaceable. Nothing here talks to a server, which is what makes
    it testable and what makes `--plan` honest.

    `portal_tag` is REQUIRED for a portal to be placed at all. Without it no
    portal is placed and the refusal is in the report: MEASURED from
    assembly_valheim.dll, a blank tag pairs at random with any other blank-tag
    portal, and this world holds 26 of them in mod world locations.

    `extra_portal_tags` are ADDITIONAL portals to stand at THIS site, one per
    tag, on top of the site's own spoke portal. They exist because a tag with
    one member is a portal that leads nowhere -- MEASURED from
    `Game::ConnectPortals`, an unpaired portal simply stays unconnected -- and
    the far end of every spoke edge in this world is a portal in the HUB's
    portal room. So the hub site is stocked with its own tag plus one extra per
    spoke, and every tag in the world then has exactly two members.

    They are solved LAST, after the props and the chest bank, so the `taken`
    list already holds every other fixture's box and free_spot cannot stand a
    portal on a bed.

    `seat` (`PadSeat`) is the ground levelled for them: a fixture whose every
    spot in the site square is off it is REFUSED with its number (the row's
    `unseated`, and `out["unseated"]`), never stood at the pad height over
    ground nobody levelled.  None is the shipped search, which is also how
    `fixture_ring` asks where a site's fixtures go before its pad exists.

    `walkins` (`Walkins`, see WALKIN_DEPENDS_ON) is the site's walk-in book:
    every fixture is admitted only if every arch keeps a face a walker walks
    in on, and a caller that continues the packing (`hub_ends`) hands in
    the book it will keep.  None: this solve keeps its own, under
    WALKIN_MODE "reserved".
    """
    if body is None:
        body = load_body(place, cx, cz, pad_y, yaw)
    index = body.index() if body else None
    mask = body.interior() if body else None
    carried = dict(body.carried) if body else {}
    taken: list = []
    book = walkins if walkins is not None else book_for(body)
    accept = None if book is None else book.accept
    out: dict = {
        "blueprint": place.get("blueprint"),
        "carried_by_blueprint": carried,
        "body_solids": len(body.solids) if body else 0,
        "base_y": None if body is None else (
            None if body.datum.base_y is None else round(body.datum.base_y, 3)),
        "datum_method": None if body is None else body.datum.method,
        "interior": None if mask is None else mask.summary(),
        "portal": {"tag": portal_tag, "tag_source": portal_tag_source,
                   "problem": fixtures.portal_tag_problem(portal_tag)},
        "stations": [], "props": [], "chests": [], "skipped": [],
        "extra_portals": [], "preference_unplaceable": [], "unseated": [],
        "walkin_refused": [],
    }
    # The last spot's refusal for want of levelled ground, and for want of
    # a walk-in, read by the caller that owns the row.
    last: dict = {"unseated": None, "walkin": None}

    def walkin(role: str, prefab: str, found) -> dict | None:
        """Stand `found` in the book, or record why nothing stood."""
        last["walkin"] = None
        if book is None:
            return None
        if found is not None:
            book.commit([row_of(found, prefab)])
            return None
        refusal = book.refusal()
        if refusal is not None:
            last["walkin"] = dict(refusal, prefab=prefab, role=role)
            out["walkin_refused"].append(last["walkin"])
        return last["walkin"]

    def unseat(role: str, rec: dict | None) -> dict | None:
        last["unseated"] = None if rec is None else dict(rec, role=role)
        if rec is not None:
            out["unseated"].append(last["unseated"])
        return last["unseated"]

    def indoor_prefer() -> tuple[float, float] | None:
        return indoor_centre(mask, cx, cz)

    def spot(prefab: str, prefer_offset, want_yaw: float,
             centre=None, y=None, half=None, step=1.0, headroom=2.0,
             margin=None, want=None, role: str = "fixture"):
        unseat(role, None)
        last["walkin"] = None
        c = centre or (cx, cz)
        px, pz = rotate(*prefer_offset, yaw) if centre is None else prefer_offset
        if body is None:
            bare = {"prefab": prefab, "x": round(c[0] + px, 3), "y": y or pad_y,
                    "z": round(c[1] + pz, 3), "yaw": want_yaw,
                    "offset": [round(px, 3), round(pz, 3)],
                    "clearance_m": None, "from_prefer_m": 0.0,
                    "want": want or fixtures.preference(prefab),
                    "box": None, "note": "no blueprint on this placement to check against"}
            if seat is not None and seat(foot_of(bare)) is None:
                unseat(role, {"prefab": prefab,
                              "site_square_spot": [bare["x"], bare["y"],
                                                   bare["z"]],
                              **seat.miss(foot_of(bare))})
                return None
            if book is not None:
                book.commit([row_of(bare, prefab)])
            return bare
        want = want or fixtures.preference(prefab)
        prefer = (c[0] + px, c[1] + pz)
        yaws: tuple[float, ...] = ()
        if want == fixtures.WANT_INDOOR:
            # Indoors the room decides the orientation, not the placement's
            # facing: MEASURED on halvar-master-refinery, a `bed` fits at yaw 0
            # in two cells and at no other yaw anywhere in the building, so
            # fixing the yaw to the body's own would leave it homeless.
            yaws = (0.0, 90.0, 180.0, 270.0)
            inside = indoor_prefer()
            if inside is not None:
                prefer = inside
        if book is not None:
            book.begin()
        found, unseated = seated_spot(
            seat, prefab, body, index, c, y if y is not None else pad_y,
            half if half is not None else pad_half,
            prefer=prefer, yaw_deg=want_yaw, step_m=step,
            headroom_m=headroom, taken=taken, want=want, yaws=yaws,
            margin_m=margin, mask=mask, accept=accept)
        if found and found.get("box"):
            taken.append(tuple(found["box"]))
        walkin(role, prefab, found)
        if unseat(role, unseated) is not None:
            return None
        if found is None and want != fixtures.WANT_ANY:
            # LOUD, and never a silent fallback to somewhere legal: the yard
            # fallback IS the defect the operator reported.
            out["preference_unplaceable"].append({
                "prefab": prefab, "want": want,
                "indoor_covered_cells": 0 if mask is None
                                        else len(mask.of_class(fixtures.INDOOR_COVERED)),
                "reason": f"no position on this pad satisfies {want} for {prefab} "
                          f"with {fixtures.INDOOR_MARGIN_M if want == fixtures.WANT_INDOOR else 0.5} m "
                          f"clearance and 2.0 m headroom",
            })
        return found

    def apron_offset(i: int, n: int, spacing: float = 3.0,
                     clear: float = 2.0) -> tuple[float, float]:
        """A WORLD-frame offset for the i-th loose station: a row on the apron
        just outside the body's OPEN face.

        The offset this replaced was a fixed pad-relative row at
        `(-10 + 5i, -10)`, which assumed the body fills the pad. MEASURED when
        `pre-bonemass/iron-era-workshop` went from a 65.5 x 66.0 m body to a
        22.3 x 23.9 m one on the same 70 m pad: every one of those preferred
        points landed INSIDE the new building, so `free_spot` walked outwards
        until it left the walls and put the cauldron, portal, hearth and
        stonecutter 20, 26 and 31 m away across bare ground. Nothing intersected
        -- the audit was clean -- and the result was still wrong, because the
        thing a player does with a stonecutter is walk to it.

        Measured from the body rather than from the pad: `PlacedBody.footprint`
        is the occupied XZ box AFTER the placement yaw, so its extents are
        already in world axes. The open face is the direction the placement's
        `rotation.facing` claims, `(sin yaw, cos yaw)` per
        `placements.yaml: rotation_convention`, and the row runs along the
        perpendicular. `free_spot` still has the last word: this is a preference
        it searches out from, never a position it accepts unchecked.
        """
        rad = math.radians(yaw)
        ox, oz = math.sin(rad), math.cos(rad)
        tx, tz = math.cos(rad), -math.sin(rad)
        reach = (abs(ox) * body.footprint.size_x
                 + abs(oz) * body.footprint.size_z) / 2.0 + clear
        along = (i - (n - 1) / 2.0) * spacing
        return (ox * reach + tx * along, oz * reach + tz * along)

    def portal_refusal(prefab: str) -> str | None:
        """Why this portal must NOT be placed, or None when it may be.

        The gate is the whole portal fix. MEASURED from
        `Game::FindRandomUnconnectedPortal` in the deployed
        `assembly_valheim.dll`: a portal pairs with a UNIFORMLY RANDOM member of
        the set of unconnected portals whose tag string is EQUAL to its own, and
        `""` is a tag. MEASURED in this world: 26 blank-tag `portal_wood`
        instances stand in mod-added world locations. So an untagged portal here
        is a random one-way door to a roadside shrine, which is exactly what the
        operator walked through. A portal with no tag is therefore not placed at
        all -- a missing portal is a visible, fixable absence; a portal that
        teleports you somewhere unrecoverable is not.
        """
        if not fixtures.is_portal(prefab):
            return None
        return fixtures.portal_tag_problem(portal_tag)

    # --- stations. The blueprint owns any station it carries; only the
    #     extensions needed for the preset's LEVEL are added, beside the
    #     blueprint's own station and at that station's own floor height.
    #
    # The ones the body does NOT carry are laid out as a row on the apron of
    # its open face, so they are indexed among THEMSELVES rather than by
    # position in the preset's list -- otherwise a body that happens to carry
    # the first four stations leaves four gaps in the row.
    loose = [s["prefab"] for s in (preset.get("stations") or [])
             if not carried.get(s["prefab"], 0)]
    for station in preset.get("stations") or []:
        prefab = station["prefab"]
        level = int(station.get("level") or 1)
        exts = station.get("extensions") or []
        have = carried.get(prefab, 0)
        row: dict = {"prefab": prefab, "required_level": level,
                     "carried_by_blueprint": have, "extensions": []}
        if have:
            row["place"] = None
            row["owner"] = "blueprint"
            anchors = [s for s in body.solids if s[6] == prefab] if body else []
            # the blueprint's own copies, highest floor last, so the extensions
            # go to the one a player is most likely to be standing at: the
            # LOWEST, which is the one reachable from the pad.
            anchors.sort(key=lambda s: s[2])
            row["blueprint_positions"] = [
                [round((s[0] + s[1]) / 2, 2), round(s[2], 2), round((s[4] + s[5]) / 2, 2)]
                for s in anchors]
            anchor = anchors[0] if anchors else None
        else:
            refusal = portal_refusal(prefab)
            if refusal:
                row["owner"] = "refused"
                row["place"] = None
                row["refused"] = refusal
                out["skipped"].append({"prefab": prefab, "reason": refusal,
                                       "role": "station"})
                out["stations"].append(row)
                continue
            row["owner"] = "stock"
            found = spot(prefab,
                         apron_offset(loose.index(prefab), len(loose))
                         if body is not None
                         else (-10.0 + len(out["stations"]) * 5.0, -10.0),
                         yaw,
                         centre=(cx, cz) if body is not None else None,
                         role="station")
            row["place"] = found
            if last["unseated"] is not None:
                row["unseated"] = last["unseated"]
            if last["walkin"] is not None:
                row["walkin"] = last["walkin"]
            if found is not None:
                found["tag"] = portal_tag if fixtures.is_portal(prefab) else None
            anchor = None
            if found:
                anchor = (found["x"], found["x"], found["y"], found["y"],
                          found["z"], found["z"], prefab)
        already = sum(carried.get(e, 0) for e in exts)
        need = max(0, level - 1 - already)
        row["extensions_carried"] = already
        row["extensions_needed"] = need
        for i in range(need):
            if not exts:
                break
            ext = exts[i % len(exts)]
            if anchor is None:
                row["extensions"].append({"prefab": ext, "place": None,
                                          "note": "no anchor station position known"})
                continue
            ax, ay, az = (anchor[0] + anchor[1]) / 2, anchor[2], (anchor[4] + anchor[5]) / 2
            # Two attempts, coarse then fine. An extension goes beside a station
            # the builder already walled in, so the free space around it is
            # centimetres wide; the fine pass exists because MEASURED on
            # `deepnorth-sandbox/complete-station-hub` the coarse one leaves a
            # `blackforge_ext4` and a `piece_magetable_ext` with nowhere to
            # stand, and half a preset's station levels is worse than a 0.25 m
            # grid. Neither pass will accept an intersection.
            found = None
            unseated = None
            if book is not None:
                book.begin()
            for half, step, margin in ((EXT_SEARCH_M, 0.5, 0.1),
                                       (EXT_SEARCH_M + 1.0, 0.25, 0.05)):
                found, unseated = seated_spot(
                    seat, ext, body, index, (ax, az), ay, half,
                    prefer=(ax, az), yaw_deg=yaw, step_m=step, margin_m=margin,
                    headroom_m=0.5, taken=taken,
                    accept=accept) if body else (None, None)
                if found:
                    break
            if found and found.get("box"):
                taken.append(tuple(found["box"]))
            refused = walkin("extension", ext, found)
            if found is None and unseated is not None:
                row["extensions"].append({
                    "prefab": ext, "place": None,
                    "unseated": unseat("extension", unseated)})
                continue
            if found is None and refused is not None:
                row["extensions"].append({"prefab": ext, "place": None,
                                          "walkin": refused})
                continue
            row["extensions"].append({
                "prefab": ext, "place": found,
                "distance_m": None if not found else found["from_prefer_m"],
                # EXT_SEARCH_M is the search BOUND, not the game's rule: the grid
                # is square so its corners reach further than its half-width.
                "within_search_bound": bool(found)
                                       and found["from_prefer_m"] <= EXT_SEARCH_M,
                # The GAME's rule, MEASURED from the deployed
                # assembly_valheim.dll: `StationExtension::FindStationsInRange`
                # calls `CraftingStation::FindStationsInRange(name, center,
                # m_maxStationDistance, ...)`, whose test is
                # `Vector3.Distance(station.transform.position, center) <
                # maxRange` -- full 3-D distance, strict less-than -- and
                # `StationExtension::.ctor` initialises `m_maxStationDistance`
                # to `ldc.r4 5.`, i.e. 5.0 m. An extension is placed at its
                # station's own Y, so the 3-D distance IS this horizontal one.
                "within_game_range": bool(found)
                                     and found["from_prefer_m"] < MAX_STATION_DISTANCE_M,
            })
        out["stations"].append(row)

    # --- props
    #
    # A prop is skipped when the blueprint carries it AND when this run has
    # already placed it as one of the preset's STATIONS. The second test was
    # missing: `portal_wood` is both a `PROPS` entry and a station in every
    # preset that names one, so a site whose body carries no portal got TWO,
    # 14 m apart -- MEASURED on pre-bonemass/iron-era-workshop. `carried`
    # could not catch it because neither copy is carried; both are stock's.
    placed_stations = {r["prefab"] for r in out["stations"] if r.get("place")}
    for prefab, offset, extra_yaw in (PROPS if props is None else props):
        if carried.get(prefab):
            out["skipped"].append({"prefab": prefab, "reason": "carried by the blueprint",
                                   "count_in_body": carried[prefab]})
            continue
        if prefab in placed_stations:
            out["skipped"].append({"prefab": prefab,
                                   "reason": "already placed as one of the preset's stations"})
            continue
        refusal = portal_refusal(prefab)
        if refusal:
            out["skipped"].append({"prefab": prefab, "reason": refusal, "role": "prop"})
            continue
        placed = spot(prefab, offset, yaw + extra_yaw, role="prop")
        if placed is not None and fixtures.is_portal(prefab):
            placed["tag"] = portal_tag
        out["props"].append({"prefab": prefab, "place": placed,
                             **({"unseated": last["unseated"]}
                                if last["unseated"] is not None else {}),
                             **({"walkin": last["walkin"]}
                                if last["walkin"] is not None else {})})

    # --- the bulk-material chest bank. Its own prefab, `piece_chest`, is
    #     deliberately not one the bodies carry (they use `piece_chest_wood`),
    #     so the bank is always stock's and is always placed by search.
    for i in range(n_chests):
        placed = spot(CHEST_PREFAB, (-((n_chests - 1) * 0.8) + i * 1.6, 12.0),
                      yaw, role="chest")
        out["chests"].append({"prefab": CHEST_PREFAB, "place": placed,
                              **({"unseated": last["unseated"]}
                                 if last["unseated"] is not None else {}),
                              **({"walkin": last["walkin"]}
                                 if last["walkin"] is not None else {})})
    # --- the far ends of other sites' edges, standing HERE.
    #
    # TWO ATTEMPTS, and the order is the design's own words: the hub end belongs
    # in "the hub's portal room", so it is searched as an INDOOR fixture first
    # and only then as a WANT_ANY one. A portal is WANT_ANY by declaration
    # (`PLACEMENT_PREFERENCE`) because a doorway outdoors is not a defect, so
    # the one-attempt version put every hub end on bare ground -- MEASURED on
    # `salty-dick-portal-hub-final`, all 11 of them, even though that body has
    # 305 m2 of indoor covered floor under 4.66-13.45 m of cover and 197
    # (cell x yaw) spots that take a 3.294 m tall `portal_wood` outright. The
    # nearest-legal-cell rule simply found open ground first, because the
    # centroid of a ring-shaped interior is in the courtyard.
    #
    # The FALLBACK is deliberate and is not the yard defect: a hub end that is
    # refused leaves the spoke it answers with a single-member tag, i.e. a
    # portal that leads nowhere, which is strictly worse than a portal on a
    # pad. Which of the two happened is recorded per portal.
    for tag in extra_portal_tags:
        prefab = "portal_wood"
        problem = fixtures.portal_tag_problem(tag)
        if problem:
            out["extra_portals"].append({"prefab": prefab, "tag": tag,
                                         "place": None, "refused": problem})
            out["skipped"].append({"prefab": prefab, "reason": problem,
                                   "role": "hub_portal", "tag": tag})
            continue
        unseated = None
        refused = None
        if body is None:
            placed = spot(prefab, (0.0, 0.0), yaw, centre=(cx, cz),
                          role="hub_portal")
            unseated = last["unseated"]
        else:
            placed, unseated = hub_arch(body, index, mask, cx, cz, pad_y,
                                        pad_half, yaw, taken, indoor_prefer(),
                                        seat=seat, walkins=book)
            if placed and placed.get("box"):
                taken.append(tuple(placed["box"]))
            if unseated is not None:
                unseated = unseat("hub_portal", unseated)
            refused = walkin("hub_portal", prefab, placed)
        if placed is not None:
            placed["tag"] = tag
        out["extra_portals"].append({"prefab": prefab, "tag": tag,
                                     "place": placed,
                                     "in_portal_room":
                                         bool(placed)
                                         and placed.get("class")
                                         == fixtures.INDOOR_COVERED,
                                     **({"unseated": unseated}
                                        if unseated is not None else {}),
                                     **({"walkin": refused,
                                         "refused": walkin_reason(refused)}
                                        if refused is not None
                                        and unseated is None else {})})

    return out


def hub_arch(body, index, mask, cx: float, cz: float, pad_y: float,
             pad_half: float, yaw: float, taken: list,
             indoor_centre: tuple[float, float] | None,
             seat=None, walkins=None) -> tuple[dict | None, dict | None]:
    """ONE hub arch, searched the way every hub arch is: indoors first, then
    anywhere on the levelled ground (`seat`, see SEAT_DEPENDS_ON) from the
    interior's middle.  Returns `(arch, unseated)` as `seated_spot` does.
    Appends nothing: the caller owns `taken`.

    `prefer` is left at the pad centre ON PURPOSE for the indoor attempt, and
    it is the one knob that matters here. Fitting eleven identical 4.23 x
    1.18 m portals into one room is a PACKING problem, and this search is
    greedy: its objective is `wall_gap` and `prefer` only breaks ties -- of
    which there are many, because a room has a lot of wall at the same 0.09 m.
    MEASURED on `salty-dick-portal-hub-final`, 305 m2 of interior, eleven
    tags: tie-breaking from the pad centre seats 10 indoors, tie-breaking from
    the interior CENTROID seats 4 and spills 7 onto the pad. Same objective,
    same constraints, 2.5x the result -- so the tie-break is chosen by
    measurement and the count achieved is reported per portal rather than
    assumed.  The indoor attempt stands on the body's own floor, which the
    pad covers (`flatten.placed_body_cover`), so only the outdoor one asks
    the seat.

    `walkins` (`Walkins`): a spot is taken only where this arch has a face a
    walker walks in on and no arch already standing loses its last one --
    the gate's `approach_ray`, asked of the spot before it is chosen.  The
    caller commits what it keeps; `walkins.refusal()` is the number when
    nothing was left."""
    prefab = "portal_wood"
    placed = None
    accept = None
    if walkins is not None:
        walkins.begin()
        accept = walkins.accept
    if mask is not None and mask.of_class(fixtures.INDOOR_COVERED):
        placed = fixtures.free_spot(
            prefab, body, index, (cx, cz), pad_y, pad_half,
            yaw_deg=yaw, step_m=1.0,
            yaws=(0.0, 90.0, 180.0, 270.0), headroom_m=0.0,
            taken=taken, want=fixtures.WANT_INDOOR, mask=mask, accept=accept)
    if placed is not None:
        return placed, None
    c = indoor_centre or (cx, cz)
    return seated_spot(
        seat, prefab, body, index, c, pad_y, pad_half, prefer=c, yaw_deg=yaw,
        step_m=1.0, headroom_m=2.0, taken=taken,
        want=fixtures.preference(prefab), yaws=(), margin_m=None, mask=mask,
        accept=accept)


# The mods a `spawn_object` record needs, as `settlements/waterfront.py::
# spawn_op` declares them: World Edit Commands owns the verb and Server
# Devcommands parses its `pos=`/`rot=`/`from=` arguments.
SPAWN_MODS = ["WorldEditCommands", "ServerDevcommands"]


def exact_radius(p: dict, to_place: list[tuple[str, dict]]) -> float:
    """The radius inside which `p` must be the ONLY object of its prefab.

    `fixture_record`'s postcondition is an EXACT count of 1, so the disc it
    counts in may hold no other same-prefab fixture of this site: the radius is
    `min(0.5, half the XZ distance to the nearest other same-prefab position in
    to_place)`. XZ and not 3-D because `objects_count` -- the instrument
    `prefab_count` and the replay guard both use -- is a vertical cylinder.
    Two chests 0.8 m apart get 0.4 m each and cannot count each other.
    """
    near = [math.hypot(q["x"] - p["x"], q["z"] - p["z"])
            for _r, q in to_place if q is not p and q["prefab"] == p["prefab"]]
    return min([0.5] + [d / 2.0 for d in near])


def standing_body_plans(records: list[dict], site_id: str,
                        retired: set[int]) -> list[int]:
    """Seqs of the live `spawn_plan` records that stood this site's body.

    THE BODY MUST STAND BEFORE ITS FIXTURES: buildstage step 6 `requires`
    `bodies`, and every position here is searched against the body's solids.
    MEASURED on the previous Fresh42 drive: step 6 stocked 13 of 13 sites at
    rc 0 while 0 installation bodies had ever been spawned (step 5 wrote 4
    plans and executed none). The ledger is the only record of a body having
    been built, so a site that names a blueprint and has no live spawn_plan
    in it is refused before anything is spawned.
    """
    return [r["seq"] for r in records
            if r["op"] == "spawn_plan"
            and (r.get("params") or {}).get("site_id") == site_id
            and r["seq"] not in retired]


# ---------------------------------------------------------------------------
# A PORTAL PAIR LANDS WHOLE OR NOT AT ALL, AND ITS OWNER PLACES BOTH ENDS.
#
# `placements.yaml` declares the graph: ONE hub placement (`portal.role: hub`,
# `portal.tags`) and one spoke per edge (`portal.role: spoke`, `portal.tag`,
# `portal.peer`).  This file placed a spoke's OWN end from its `portal_tag`
# and the far end only on `--hub-portal-tag`, which `buildstage.py` never
# passes -- so no build ever placed a hub end.  MEASURED on Fresh42 drive 2
# (`/tmp/regen2/d2_gate.json`, section `portals`): 9 installation tags stood
# ONE-ENDED -- u-stathub, u-meadhall, u-blacbase, u-dock, u-dnshrine,
# u-dncamp, u-mtnpost, u-mistfrge, u-plainsfm -- each a door that pairs at
# random with a blank-tag mod-location portal.  The hub body had also been
# refused at step 5, and a hub run that DID stand the far ends would strand
# its own whenever a spoke refused later.
#
# So the SPOKE owns its pair: its run places its own end AND the hub end
# reserved for its tag, in one session, only when BOTH bodies stand in this
# world's ledger and both positions solved.  Otherwise NEITHER end is placed,
# the reason is noted in the ledger, and the rest of the site is still
# stocked.  The hub's own run places no declared end.  Settlements follow the
# same rule in `settlements/build.py::apply_portals`.
#
# THE HUB END IS ONE DETERMINISTIC PACKING (`hub_ends`): the hub's own solve
# with its declared tags as `extra_portal_tags`, which `solve_placement`
# solves LAST -- after every station, prop and chest -- and in order.  So the
# hub's own run (no extra tags) solves the same stations, and every spoke
# asking for its end gets the same arch.  Settlement arches CONTINUE this
# packing afterwards (`settlements/build.py::hub_spots`, through `hub_arch`,
# each with its board), so they move none of these.
#
# PAIR_DEPENDS_ON = ("hub_body_stands", "hub_end_solved"): `PAIR_MODE`
# "spoke_alone" restores the shipped rule -- the spoke's end placed whatever
# the hub holds -- and exists for the neuter in `settlements/build.py`'s
# selftest (`NEUTER_spoke_alone_the_one_ended_tag_RETURNS`).
# ---------------------------------------------------------------------------
PAIR_MODE = "both_or_neither"


def site_inputs(world: str, preset_name: str, pid: str, *, chests: int = 0,
                scope: str | None = None, place: dict | None = None) -> dict:
    """Everything `solve_placement` is handed for one placement, derived ONE
    way: the hub is solved by every run that needs an arch in it, so its
    inputs must not depend on which process asks.  `place` stands in for the
    recorded one (`fixture_ring` at a stage-7 candidate); `pad` is the
    rectangle `flatten.build` stamps (`flatten.pad_extent`), None where the
    placement records none."""
    place = place or load_placement(world, preset_name, pid)
    solved = place["solved"]
    cost = solved.get("flatten_cost") or {}
    preset = load_preset(preset_name)
    items = dict(load_manifest(world, preset_name).get("items") or {})
    # SCOPE, resolved before anything is solved. `portal_only` empties the
    # station ladder and the prop list rather than filtering at spawn time, so
    # the PLAN and the audit describe what the site gets -- a report that
    # solves 30 fixtures and then spawns one is a report nobody can read.
    scope = scope or place.get("stock_scope") or "full"
    if scope not in ("full", "portal_only"):
        raise SystemExit(f"placement {pid} declares stock_scope={scope!r}, "
                         f"which is neither `full` nor `portal_only`")
    if scope == "portal_only":
        preset = dict(preset, stations=[
            s for s in (preset.get("stations") or [])
            if fixtures.is_portal(s["prefab"])])
        items = {}
    # A reinforced chest holds a fixed grid of slots and a manifest line of
    # 300 units needs one slot per max-stack, so the bank is sized on
    # estimated STACKS (50 units is the common material stack) and not on the
    # number of item kinds.
    est_stacks = sum(max(1, -(-int(c) // 50)) for c in items.values())
    return {
        "place": place, "preset": preset, "items": items, "scope": scope,
        "cx": float(solved["x"]), "cz": float(solved["z"]),
        "base_y": float(cost.get("target_y") or solved["y"]),
        "yaw": float((place.get("rotation") or {}).get("yaw") or 0.0),
        "pad_half": float((place.get("requirement") or {})
                          .get("footprint_m") or 70.0) / 2.0,
        "pad": written_pad_of(place),
        "n_chests": (0 if scope == "portal_only"
                     else chests or max(2, -(-est_stacks // 18))),
    }


def written_pad_of(place: dict) -> tuple[float, float] | None:
    """The (w, d) `flatten.build` stamps for `place` -- `flatten.pad_extent`,
    the writer's own reader -- or None where the record declares no pad at
    all (then nothing is levelled, and there is no seat to ask)."""
    try:
        return flatten.pad_extent(place)
    except (KeyError, TypeError, ValueError):
        return None


def plan_fixtures(plan: dict) -> list[dict]:
    """Every fixture a solve PLACED, as `{prefab, x, y, z, yaw, box, ...}`."""
    out = []
    for row in plan["stations"]:
        for prefab, p in [(row["prefab"], row.get("place"))] + [
                (e["prefab"], e.get("place")) for e in row["extensions"]]:
            if p:
                out.append(dict(p, prefab=prefab))
    for row in plan["props"] + plan["chests"] + plan["extra_portals"]:
        if row.get("place"):
            out.append(dict(row["place"], prefab=row["prefab"]))
    return out


def solve_site(si: dict, body, *, extra_portal_tags: tuple[str, ...] = (),
               seat=None, walkins=None) -> dict:
    """`solve_placement` over one site's `site_inputs`, the one way every
    caller asks it."""
    return solve_placement(
        si["place"], si["preset"], si["n_chests"], si["cx"], si["cz"],
        si["base_y"], si["yaw"], si["pad_half"],
        portal_tag=si["place"].get("portal_tag"),
        portal_tag_source="placement", extra_portal_tags=extra_portal_tags,
        props=[] if si["scope"] == "portal_only" else None, body=body,
        seat=seat, walkins=walkins)


def packing_of(si: dict, preset_name: str, body, plan: dict, seat,
               walkins=None) -> dict:
    """The hub packing's shape (see `hub_ends`) for a solved hub site."""
    mask = body.interior() if body else None
    return {"hub": {"preset": preset_name, "id": si["place"]["id"],
                    "x": si["cx"], "z": si["cz"], "pad_y": si["base_y"],
                    "yaw": si["yaw"], "pad_half": si["pad_half"],
                    "pad": si["pad"]},
            "ends": {row["tag"]: row.get("place")
                     for row in plan["extra_portals"]},
            "unseated": {row["tag"]: row["unseated"]
                         for row in plan["extra_portals"]
                         if row.get("unseated")},
            "walkin": {row["tag"]: row["walkin"]
                       for row in plan["extra_portals"]
                       if row.get("walkin")},
            "boxes": [tuple(p["box"]) for p in plan_fixtures(plan)
                      if p.get("box")],
            "body": body, "index": body.index() if body else None,
            "mask": mask, "seat": seat,
            "indoor_centre": indoor_centre(mask, si["cx"], si["cz"]),
            "walkins": walkins}


def portal_graph(world: str) -> dict:
    """The declared hub and spokes of this world: `{"hub": {preset, id,
    tags} | None, "spokes": {tag: {preset, id, peer}}}`.  A second hub is a
    refusal: which hall a tag's far end stands in would be a coin toss."""
    hub = None
    spokes: dict[str, dict] = {}
    for path in sorted((JUMPSTART / "worlds" / world).glob("*/placements.yaml")):
        preset = path.parent.name
        for p in flatten.load_placements(world, preset)["placements"]:
            portal = p.get("portal") or {}
            if not p.get("solved"):
                continue
            if portal.get("role") == "hub":
                if hub is not None:
                    raise SystemExit(
                        f"{world} declares two portal hubs, {hub['preset']}/"
                        f"{hub['id']} and {preset}/{p['id']}")
                hub = {"preset": preset, "id": p["id"],
                       "tags": list(portal.get("tags") or [])}
            elif portal.get("role") == "spoke" and portal.get("tag"):
                spokes[portal["tag"]] = {"preset": preset, "id": p["id"],
                                         "peer": portal.get("peer")}
    return {"hub": hub, "spokes": spokes}


_HUB_ENDS: dict[str, dict] = {}


def hub_ends(world: str) -> dict:
    """THE hub packing: every DECLARED hub tag, solved in order against the
    hub body after its stations, props and chests, on the hub's written pad
    (`site_seat`).  Returns `{"hub": {preset, id, x, z, pad_y, yaw,
    pad_half, pad}, "ends": {tag: place | None}, "unseated": {tag: refusal},
    "boxes": [every solved fixture box, i.e. the packing's `taken`], "body",
    "index", "mask", "seat", "indoor_centre", "walkin": {tag: walk-in
    refusal}, "walkins"}` -- the body, index, mask, seat and walk-in book
    (`Walkins`) so a caller that stands MORE arches in the hall
    (`settlements/build.py::hub_spots`) continues this packing with
    `hub_arch` instead of starting another; it continues a `fork()` of the
    book, never the memo's own.  `{"hub": None, ...}` when the
    world declares no hub.  Memoised per process: one solve reads the whole
    hub body (MEASURED 33.7 s on Fresh42's 1,043-piece hall)."""
    if world in _HUB_ENDS:
        return _HUB_ENDS[world]
    hub = portal_graph(world)["hub"]
    if hub is None:
        out = {"hub": None, "ends": {}, "unseated": {}, "walkin": {},
               "boxes": [], "body": None, "index": None, "mask": None,
               "seat": None, "indoor_centre": None, "walkins": None}
        _HUB_ENDS[world] = out
        return out
    si = site_inputs(world, hub["preset"], hub["id"])
    body = load_body(si["place"], si["cx"], si["cz"], si["base_y"], si["yaw"])
    seat = site_seat(si)
    book = book_for(body)
    plan = solve_site(si, body, extra_portal_tags=tuple(hub["tags"]),
                      seat=seat, walkins=book)
    out = packing_of(si, hub["preset"], body, plan, seat, walkins=book)
    _HUB_ENDS[world] = out
    return out


# ---------------------------------------------------------------------------
# THE FIXTURE RING: where a site's fixtures go, asked before its pad exists.
#
# `flatten.PAD_EXTENT_DEPENDS_ON` `fixture_ring` levels the ground under
# them, so stage 7 (`blueprints/solve_placements.py::written_pad`) needs it
# for every candidate site.  It is the solve step 6 runs -- the same
# stations, extensions, props, chests and portals, the hub's declared arches,
# and for the hub the settlement arches and their boards the settlement
# build stands (`settlements/build.py::pack_settlement_arches`) -- over the
# SITE square with no seat, as box offsets from the centre.  Those do not
# depend on where the site is (the body, the search grid and every box move
# with the centre), so one solve serves every candidate, and step 6 then
# finds each fixture where this found it: on a pad that covers the ring
# the seat refuses nothing the site square would have placed.
#
# The settlement tags are the DECLARED ones (`settlements/plan.py` TOWNS and
# OUTLIERS, in the plan's own order): stage 8, which writes the plan, runs
# after stage 7.
# ---------------------------------------------------------------------------
def ring_of(feet: list[tuple[str, tuple]], cx: float, cz: float) -> dict:
    """The ring of `(role, footprint)` pairs, as offsets from (cx, cz)."""
    if not feet:
        return {"x": [0.0, 0.0], "z": [0.0, 0.0], "fixtures": 0,
                "by_role": {}}
    by_role: dict[str, int] = {}
    for role, _f in feet:
        by_role[role] = by_role.get(role, 0) + 1
    return {"x": [round(min(f[0] for _r, f in feet) - cx, 3),
                  round(max(f[1] for _r, f in feet) - cx, 3)],
            "z": [round(min(f[2] for _r, f in feet) - cz, 3),
                  round(max(f[3] for _r, f in feet) - cz, 3)],
            "fixtures": len(feet), "by_role": by_role}


def declared_settlement_tags() -> list[str]:
    """`settlements/plan.py`'s TOWNS then OUTLIERS tags, read from its
    source: importing it would pull the settlement solver into stage 7."""
    import ast  # noqa: PLC0415
    tree = ast.parse((JUMPSTART / "settlements" / "plan.py").read_text())
    got: dict[str, list[str]] = {}
    for node in tree.body:
        target = (node.target if isinstance(node, ast.AnnAssign)
                  else node.targets[0] if isinstance(node, ast.Assign)
                  else None)
        if not isinstance(target, ast.Name) or target.id not in (
                "TOWNS", "OUTLIERS") or not isinstance(node.value, ast.List):
            continue
        got[target.id] = [kw.value.value for call in node.value.elts
                          if isinstance(call, ast.Call)
                          for kw in call.keywords
                          if kw.arg == "tag"
                          and isinstance(kw.value, ast.Constant)]
    if set(got) != {"TOWNS", "OUTLIERS"}:
        raise SystemExit("settlements/plan.py declares no TOWNS/OUTLIERS "
                         "list literal, so the hall's settlement arches "
                         "cannot be counted before stage 8")
    return got["TOWNS"] + got["OUTLIERS"]


def fixture_ring(world: str, preset_name: str, place: dict,
                 settlement_tags: list[str] | None = None) -> dict:
    """`{"x": [lo, hi], "z": [lo, hi], "fixtures": n, "by_role": {...}}`:
    the extents of every fixture footprint site `place` will seat, as
    offsets from its centre (see the block above).  `place` needs `solved`
    `x`/`z` only; its target height does not move the ring.  Solved with the
    site's walk-in book (WALKIN_DEPENDS_ON), so the ring holds every arch
    where it keeps its walk-in and the pad stage 7 levels is sized for that.
    It reads no world, so its book holds the bodies alone; step 6's also
    holds the seed round the pad (SEED_DEPENDS_ON), and a fixture that moves
    off the ring's pad for it is refused by number like any other (`seat`)."""
    si = site_inputs(world, preset_name, place["id"], place=place)
    body = load_body(si["place"], si["cx"], si["cz"], si["base_y"], si["yaw"])
    portal = place.get("portal") or {}
    is_hub = portal.get("role") == "hub"
    book = book_for(body)
    plan = solve_site(si, body, extra_portal_tags=tuple(
        portal.get("tags") or ()) if is_hub else (), walkins=book)
    feet = [("fixture", foot_of(p)) for p in plan_fixtures(plan)]
    if is_hub:
        tags = (declared_settlement_tags() if settlement_tags is None
                else settlement_tags)
        sett = str(JUMPSTART / "settlements")
        if sett not in sys.path:
            sys.path.insert(0, sett)
        import build as SB  # noqa: PLC0415  settlements/build.py
        packed = SB.pack_settlement_arches(
            packing_of(si, preset_name, body, plan, None, walkins=book), tags)
        for tag in tags:
            h = packed[tag]
            if h["arch"]:
                feet.append(("settlement_arch", foot_of(h["arch"])))
            for prefab, x, y, z, yaw in h["board"] or ():
                feet.append(("board", foot_of({"prefab": prefab, "x": x,
                                               "y": y, "z": z, "yaw": yaw})))
    return ring_of(feet, si["cx"], si["cz"])


def pair_verdict(records: list[dict], retired: set[int], hub_id: str | None,
                 own_end: dict | None, hub_end: dict | None) -> str | None:
    """Why this spoke's pair must NOT be placed, or None when both ends may.
    Pure: the ledger decides whether the hub body stands."""
    if PAIR_MODE == "spoke_alone":
        return None
    if hub_id is None:
        return "this world declares no portal hub, so the far end has no hall"
    if not standing_body_plans(records, hub_id, retired):
        return (f"the hub {hub_id} has NO live spawn_plan in this world's "
                f"ledger: its body was never built, so its end has nothing "
                f"to stand in")
    if own_end is None:
        return "this site's own end has no legal position"
    if hub_end is None:
        return f"the hub {hub_id} has no legal position left for this tag"
    return None


def pair_spawn_list(spawn_list: list[tuple[str, dict]], pair: dict | None,
                    why: str | None) -> list[tuple[str, dict]]:
    """What a spoke's run spawns of its pair: BOTH ends, or neither.  The
    shipped rule (`PAIR_MODE` "spoke_alone") spawned its own end and never
    the far one."""
    if pair is None:
        return spawn_list
    if PAIR_MODE == "spoke_alone":
        return [(r, p) for r, p in spawn_list if r != "pair_hub_end"]
    if why:
        return [(r, p) for r, p in spawn_list
                if not (fixtures.is_portal(p["prefab"])
                        and p.get("tag") == pair["tag"])]
    return spawn_list


def fixture_record(site_id: str, fixture_role: str, p: dict, preset: str,
                   radius: float = 0.5) -> dict:
    """ONE fixture as the ledger record `flatten.install_emit` appends, sends
    and verifies: `{op, params, wire, requires, expect, meta}`. Pure.

    A TAGGED portal is op `portal` -- its own op kind so the writer's
    two-ends-per-tag guard sees it -- and its wire is exactly
    `fixtures.portal_spawn_command`, whose `data=` payload is
    `fixtures.portal_data(tag)`, recorded as `data_b64` with its decoded form
    in `zdo_strings`. Everything else is op `spawn` over `spawn_object`: NOT
    ValheimRcon's native `spawn`, because `replay.send_wire` routes a
    non-native verb through the consoleCommand bridge where `spawn` is a
    different command, so a record carrying it could never be replayed.

    `radius` bounds both the postcondition (count 1 inside it) and the replay's
    already-present guard; see `exact_radius` for why the caller must shrink
    it below 0.5 m when two same-prefab fixtures stand close. Fixtures claim
    no ground, so there is no `flatten` param (unlike waterfront's
    FORBIDDEN): a later pad or road write here is not refused on their behalf.
    """
    prefab = p["prefab"]
    x, y, z = float(p["x"]), float(p["y"]), float(p["z"])
    yaw = float(p.get("yaw", 0.0))
    radius = float(radius)
    params = {"prefab": prefab, "pos": [round(x, 4), round(y, 4), round(z, 4)],
              "yaw_deg": yaw, "guard_radius_m": radius,
              "role": "site_prop", "site_id": site_id}
    count = {"prefab": prefab, "pos": [round(x, 4), round(z, 4)],
             "max": radius, "count": 1, "tolerance": 0}
    tag = p.get("tag")
    if fixtures.is_portal(prefab) and tag:
        op = "portal"
        wire = [fixtures.portal_spawn_command(prefab, x, y, z, yaw, tag)]
        params.update(tag=tag, pair_tag=tag, zdo_strings={"tag": tag},
                      data_b64=fixtures.portal_data(tag))
        expect = {"prefab_count": [count], "tag_readback": True}
    else:
        op = "spawn"
        wire = [f"spawn_object {prefab}"
                f" pos={to_rcon_plan.pos_arg(x, y, z)}"
                f" rot={to_rcon_plan.rot_arg(0.0, yaw, 0.0)}"
                f" {to_rcon_plan.FROM_ORIGIN}"]
        expect = {"prefab_count": [count]}
    return {"op": op, "params": params, "wire": wire,
            "requires": {"mods": list(SPAWN_MODS), "prefabs": [prefab],
                         "blobs": []},
            "expect": expect,
            "meta": {"fixture_role": fixture_role,
                     "installation": f"{preset}/{site_id}",
                     "offset": p.get("offset"),
                     "clearance_m": p.get("clearance_m"),
                     **({"pair_owner": p["pair_owner"]}
                        if p.get("pair_owner") else {})}}


def show_container(rc: Rcon, zid: str) -> tuple[int, dict[str, int], int]:
    """Read a container back.  Returns (slots used, item -> total units, capacity
    if the header reports it)."""
    reply = rc.command(f"showContainer {zid}")
    totals: dict[str, int] = {}
    slots = 0
    for line in reply.splitlines():
        found = STACK_RE.match(line.strip())
        if not found:
            continue
        slots += 1
        totals[found.group(2)] = totals.get(found.group(2), 0) + int(found.group(3))
    header = re.search(r"Container: (\d+) items", reply)
    return slots, totals, int(header.group(1)) if header else slots


# One `findObjects` listing line. MEASURED on Ulfsland tonight:
#
#   -Prefab: portal_wood Id: 91870:1 Position: (-4672.5 70.91 -325.5) Creator: 0
#    Health: 400 Portal tag:  (author )
#
# The portal's TAG is printed, so the world can be ASKED what a portal holds
# rather than told. That line was the instrument that proved the pre-existing
# workshop portal was blank-tagged, and it is the only read-back this pipeline
# has for a tag: `spawn_object` goes through `consoleCommand`, whose reply is a
# fixed echo. The tag group is optional so the same pattern reads any prefab.
# BOTH ZDOID components are signed 32-bit: MEASURED `Id: 82755:-322487405`,
# and an unsigned pattern once left every chest id unread and the fill undone.
OBJECT_LINE_RE = re.compile(
    r"Prefab:\s*(\S+)\s+Id:\s*(-?\d+:-?\d+)\s+Position:\s*\(([^)]*)\)")
TAG_FIELD_RE = re.compile(r"Portal tag:\s*(.*?)\s*\(author")

# The resolved prefab names the game can actually spawn, as
# `to_rcon_plan.py` uses to refuse an unspawnable body piece. Stock had no such
# check, and MEASURED that cost `pre-queen#mistlands-blackforge-base` three of
# its four cauldron tiers: the preset asked for `piece_cauldron_ext1_spice`,
# `piece_cauldron_ext3_butchertable` and `piece_cauldron_ext4_pans`, the real
# prefabs are `cauldron_ext1_spice`, `cauldron_ext3_butchertable` and
# `cauldron_ext4_pots`, and `spawn` answered nothing while the report recorded
# `"id": null` and carried on. A name the game does not know is a station the
# site silently never gets.
EVIDENCE_PATH = JUMPSTART / "blueprints" / "data" / "prefab_evidence.json"


def unspawnable(prefabs) -> list[str]:
    """Which of these names the prefab evidence cannot resolve.

    Empty when the evidence file is absent: this is a guard against typos, not
    a licence to refuse work when the evidence has not been generated, and
    saying so is cheaper than a mysterious refusal.
    """
    if not EVIDENCE_PATH.exists():
        return []
    doc = json.loads(EVIDENCE_PATH.read_text())
    known = doc.get("resolution") or {}
    return sorted({p for p in prefabs
                   if known.get(p, "MISSING") == "MISSING"})


def find_at(rc: Rcon, prefab: str, x: float, y: float, z: float,
            half: float = 2.0) -> list[dict]:
    """Every object of `prefab` inside a `half`-metre cube of this point, with
    its ZDOID, its position and -- for a portal -- the tag the ZDO holds.

    `half` is small ON PURPOSE. `findObjects`' per-object listing IS the text
    the plugin hands to `Log.Message` on the Unity main thread BEFORE the
    4050-byte reply cap applies, so the bounded thing has to be the QUERY: a 2 m
    cube around a point one fixture was spawned at holds that fixture, and one
    line cannot wedge anything. `guard()` refuses the unscoped form outright.
    """
    reply = rc.command(f"findObjects -prefab {prefab} -near {x:.2f} {y:.2f} "
                       f"{z:.2f} {half:.2f}")
    out: list[dict] = []
    for line in reply.splitlines():
        found = OBJECT_LINE_RE.search(line)
        if not found:
            continue
        px, py, pz = (float(v) for v in found.group(3).replace(",", " ").split())
        tag = TAG_FIELD_RE.search(line)
        out.append({"prefab": found.group(1), "id": found.group(2),
                    "at": [round(px, 2), round(py, 2), round(pz, 2)],
                    "tag": tag.group(1) if tag else None,
                    "distance_m": round(math.dist((x, y, z), (px, py, pz)), 3)})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    worldseed.add_arguments(ap)
    ap.add_argument("--preset", required=True)
    ap.add_argument("--id", required=True)
    ap.add_argument("--chests", type=int, default=0,
                    help="how many chests to place; 0 = enough for the manifest at 8 stacks each")
    ap.add_argument("--plan", action="store_true",
                    help="decide and audit every position offline, spawn nothing, "
                         "touch no server")
    ap.add_argument("--report", help="write a JSON report here")
    ap.add_argument("--actor",
                    help="the agent running this. REQUIRED unless --plan: every "
                         "spawn is appended to the ledger under this actor")
    ap.add_argument("--portal-tag",
                    help="the portal tag for THIS site. Without it no portal is "
                         "placed at all: MEASURED, a blank-tag portal pairs with a "
                         "uniformly random other blank-tag portal in the world, and "
                         "Ulfsland holds 26 of them in mod world locations. Falls "
                         "back to the placement's own `portal_tag:` key.")
    ap.add_argument("--hub-portal-tag", action="append", default=[],
                    metavar="TAG",
                    help="an ADDITIONAL portal to stand at this site, carrying "
                         "this tag; repeatable. This is how the FAR END of "
                         "another site's edge gets built: every spoke tag needs "
                         "exactly two members or the spoke leads nowhere.")
    ap.add_argument("--skip-portals", action="store_true",
                    help="solve exactly as normal -- so every other fixture lands "
                         "in the same place it otherwise would -- but spawn no "
                         "portal piece. For when the far end of the edge does not "
                         "exist yet: a one-ended tag is the defect, so the portal "
                         "waits rather than shipping half a pair.")
    ap.add_argument("--only-portals", action="store_true",
                    help="the other half of --skip-portals: same solve, spawn ONLY "
                         "the portal pieces, place and fill no chests. Run once "
                         "both ends can be built. The solve is deterministic for a "
                         "given site, so the position a portal gets here is the one "
                         "the earlier --skip-portals run reserved for it.")
    ap.add_argument("--repair", action="store_true",
                    help="solve as normal, then READ THE WORLD at every solved "
                         "position and spawn only what is not already there. For "
                         "a site already stocked: re-running plain would double "
                         "every fixture, and doing nothing leaves whatever failed "
                         "to spawn missing. Chests are never re-placed or "
                         "re-filled in this mode.")
    ap.add_argument("--probe-half", type=float, default=1.5,
                    help="half-edge of the cube --repair looks in for an already "
                         "placed fixture. Small because findObjects' listing is "
                         "logged on the Unity main thread before truncation.")
    ap.add_argument("--scope", choices=("full", "portal_only"), default=None,
                    help="what this SITE gets, as opposed to what this RUN "
                         "spawns. `portal_only` is for a terminus: a site whose "
                         "body is a portal shack and which should never carry a "
                         "station ladder. MEASURED case: "
                         "pre-kall#deepnorth-landing-portal is a 133-piece 4x7 m "
                         "shack, and the pre-kall ladder would stand 16 stations, "
                         "13 extensions, two beds, a fire pit, a maypole and six "
                         "chests in the snow around it. Defaults to the "
                         "placement's own `stock_scope:` key, then to `full`.")
    args = ap.parse_args()
    _seed, world = worldseed.apply(args)
    if args.skip_portals and args.only_portals:
        ap.error("--skip-portals and --only-portals are opposites; pick one")
    if not args.plan and not args.actor:
        ap.error("--actor is required unless --plan: every spawn is appended to "
                 "the ledger under this actor before it is sent")

    si = site_inputs(world, args.preset, args.id, chests=args.chests,
                     scope=args.scope)
    place, preset, items, scope = si["place"], si["preset"], si["items"], si["scope"]
    cx, cz, base_y, yaw = si["cx"], si["cz"], si["base_y"], si["yaw"]
    pad_half, n_chests = si["pad_half"], si["n_chests"]

    # A DECLARED DUPLICATE HAS NO FIXTURES OF ITS OWN.  `duplicate_of` says
    # this placement is the SAME physical structure as another placement at
    # the same solved coordinates; `terraform/sites.py` builds one body for
    # the two and skips this one, so its fixtures are the canonical's
    # fixtures and stocking it would stand a second station set in the same
    # walls.  MEASURED on Fresh42 drive 2: step 6 refused
    # deepnorth-sandbox/sandbox-harbour rc 1 for "no live spawn_plan with
    # site_id=sandbox-harbour" -- true, and the wrong question: the body
    # stands, as pre-elder/early-dock's spawn_plan seq 1511.
    # `flatten.declared_duplicate`: the one predicate clear, flatten and
    # stock ask.
    canon = flatten.declared_duplicate(place)
    if canon:
        print(json.dumps({"site": args.id, "preset": args.preset,
                          "duplicate_of": canon,
                          "stocked": 0,
                          "why": (f"{args.preset}/{args.id} declares "
                                  f"duplicate_of {canon}: its "
                                  f"body and fixtures are that site's, stocked "
                                  f"by that site's own run")}, indent=1))
        return 0

    report: dict = {"site": args.id, "preset": args.preset, "centre": [cx, cz],
                    "y": base_y, "yaw": yaw, "stations": [], "props": [], "chests": []}

    portal_tag = args.portal_tag
    portal_tag_source = "cli"
    if not portal_tag:
        portal_tag = place.get("portal_tag")
        portal_tag_source = "placement" if portal_tag else "none"

    # Decide everything first, then audit the decision, then spawn. The audit is
    # the guard the operator had to act as: 12 of the 18 fixtures this file used
    # to place stood inside the building, and no check said so.  On the
    # written pad (SEAT_DEPENDS_ON): a fixture it does not reach is refused
    # with its number, below, and the rest of the site is stocked.
    seat = site_seat(si)
    # THE SEED ROUND THE PAD, READ BEFORE ANYTHING IS SOLVED (see
    # SEED_DEPENDS_ON): the trees and rocks the gate walks the walker
    # through are not ours to place and not ours to clear, so the book that
    # keeps every arch a face holds them.  Only a site that can stand an arch
    # has a walk-in, and only a run that touches the server can read one.
    body = load_body(place, cx, cz, base_y, yaw)
    seed: list = []
    seen: dict = {"read": False}
    if args.plan:
        seen["why"] = ("--plan touches no server: the walk-ins are kept "
                       "against the body alone")
    elif WALKIN_MODE != "reserved" or SEED_MODE != "read":
        seen["why"] = (f"WALKIN_MODE {WALKIN_MODE!r}, SEED_MODE "
                       f"{SEED_MODE!r}: no seed is held")
    elif not arch_may_stand(portal_tag, tuple(args.hub_portal_tag)):
        seen["why"] = "this site stands no arch, so it has no walk-in to keep"
    else:
        seed, census = read_seed(world, args.actor, si, seat)
        seen = dict(census, read=True)
        if seen["unlisted"]:
            raise SystemExit(
                f"refusing to stock {args.preset}/{args.id}: the seed round "
                f"its pad was not read whole: {len(seen['unlisted'])} "
                f"disc(s) held more than one bounded listing can carry, and "
                f"unread is not clear. Nothing was spawned.")
    plan = solve_placement(place, preset, n_chests, cx, cz, base_y, yaw, pad_half,
                           portal_tag=portal_tag, portal_tag_source=portal_tag_source,
                           extra_portal_tags=tuple(args.hub_portal_tag),
                           props=[] if scope == "portal_only" else None,
                           body=body, seat=seat, walkins=book_for(body, seed))
    report["seed"] = seen
    report["seat"] = (None if seat is None else
                      {"on": "written_pad", "pad": [seat.w, seat.d],
                       "pad_y": seat.pad_y})
    report["scope"] = scope
    report["plan"] = plan
    report["portal_tag"] = portal_tag

    # THIS SITE'S PAIR, when it is a declared spoke: the hub end reserved for
    # its tag in THE hub packing, placed in this run or not at all.
    graph = portal_graph(world)
    spoke = graph["spokes"].get(portal_tag) if portal_tag else None
    pair: dict | None = None
    if spoke is not None and spoke["id"] == args.id and graph["hub"]:
        packing = hub_ends(world)
        pair = {"tag": portal_tag, "hub_id": graph["hub"]["id"],
                "hub_preset": graph["hub"]["preset"],
                "hub_end": packing["ends"].get(portal_tag),
                "hub_unseated": packing["unseated"].get(portal_tag),
                "hub_walkin": packing["walkin"].get(portal_tag)}
    elif spoke is not None and spoke["id"] == args.id:
        pair = {"tag": portal_tag, "hub_id": None, "hub_preset": None,
                "hub_end": None}
    report["pair"] = None if pair is None else {
        k: v for k, v in pair.items()
        if k not in ("hub_end", "hub_unseated", "hub_walkin")}
    to_place: list[tuple[str, dict]] = []
    unplaceable: list[dict] = []

    def cannot_place(prefab: str, role: str, row: dict | None = None,
                     **extra) -> None:
        """Record a fixture with nowhere to go, and say whether that is FATAL.

        Fatal means the site cannot be stocked: a prop or a chest with no legal
        position anywhere. NOT fatal, but loud, is a fixture whose declared
        PREFERENCE this body cannot satisfy -- a bed wants a covered indoor cell
        and a dock does not have one. The two were one list, which meant the
        only way to keep a build running was to let the bed stand in the rain.
        Nor is one the written pad does not reach (`row["unseated"]`): it is
        refused with its number and the rest of the site is stocked, because
        standing it at the pad height over unlevelled ground is the defect.
        Nor is one refused because it would close the last walk-in of an arch
        (`row["walkin"]`, WALKIN_DEPENDS_ON): the same, with that number.
        """
        want = fixtures.preference(prefab)
        unseated = (row or {}).get("unseated")
        if unseated is not None:
            unplaceable.append({"prefab": prefab, "role": role, "want": want,
                                "fatal": False, "unseated": unseated,
                                "refused": unseated_reason(unseated), **extra})
            return
        walk = (row or {}).get("walkin")
        if walk is not None:
            unplaceable.append({"prefab": prefab, "role": role, "want": want,
                                "fatal": False, "walkin": walk,
                                "refused": walkin_reason(walk), **extra})
            return
        unplaceable.append({
            "prefab": prefab, "role": role, "want": want,
            "fatal": role != "extension" and want == fixtures.WANT_ANY,
            **extra})

    for row in plan["stations"]:
        if row["place"]:
            to_place.append(("station", dict(row["place"], prefab=row["prefab"])))
        elif row["owner"] == "stock":
            cannot_place(row["prefab"], "station", row)
        elif row["owner"] == "refused":
            unplaceable.append({"prefab": row["prefab"], "role": "station",
                                "want": fixtures.preference(row["prefab"]),
                                "fatal": False, "refused": row["refused"]})
        for ext in row["extensions"]:
            if ext["place"]:
                to_place.append(("extension", dict(ext["place"], prefab=ext["prefab"])))
            else:
                cannot_place(ext["prefab"], "extension", ext, of=row["prefab"])
    for row in plan["props"]:
        if row["place"]:
            to_place.append(("prop", dict(row["place"], prefab=row["prefab"])))
        else:
            cannot_place(row["prefab"], "prop", row)
    for row in plan["chests"]:
        if row["place"]:
            to_place.append(("chest", dict(row["place"], prefab=row["prefab"])))
        else:
            cannot_place(row["prefab"], "chest", row)
    for row in plan["extra_portals"]:
        if row["place"]:
            to_place.append(("hub_portal",
                             dict(row["place"], prefab=row["prefab"])))
        elif row.get("unseated"):
            cannot_place(row["prefab"], "hub_portal", row, tag=row["tag"])
        else:
            # FATAL regardless of preference: a hub end that does not get built
            # leaves the spoke it answers with a single-member tag, and a
            # single-member tag is a portal that leads nowhere.
            unplaceable.append({"prefab": row["prefab"], "role": "hub_portal",
                                "want": fixtures.preference(row["prefab"]),
                                "tag": row["tag"], "fatal": True,
                                **({"refused": row["refused"]}
                                   if row.get("refused") else {})})
    # THE PAIR'S FAR END rides right behind this site's own end, so the two
    # are one decision below and one pass through the spawn loop.  It stands
    # at the HUB, so it is not audited against THIS body: its position is the
    # hub packing's, box-tested there against the hub's own solids.
    own_end = None
    if pair is not None:
        own_end = next((p for _r, p in to_place
                        if fixtures.is_portal(p["prefab"])
                        and p.get("tag") == pair["tag"]), None)
        if own_end is not None and pair["hub_end"] is not None:
            at = next(i for i, (_r, p) in enumerate(to_place) if p is own_end)
            to_place.insert(at + 1, ("pair_hub_end", dict(
                pair["hub_end"], prefab="portal_wood", tag=pair["tag"],
                site_id=pair["hub_id"], preset=pair["hub_preset"],
                pair_owner=f"{args.preset}/{args.id}")))
    for u in plan["unseated"]:
        print(f"  REFUSED, NOT ON LEVELLED GROUND: {u['prefab']} ({u['role']}): "
              f"{unseated_reason(u)}", file=sys.stderr)
    report["unplaceable"] = unplaceable

    # A NAME THE GAME DOES NOT KNOW, caught offline. Refused rather than
    # warned, because the failure mode it replaces is silence: `spawn` answered
    # nothing, the report recorded `"id": null`, and the site was simply
    # missing a station nobody noticed until a player tried to cook with it.
    report["unspawnable_prefabs"] = unspawnable(p["prefab"] for _r, p in to_place)
    if report["unspawnable_prefabs"]:
        print(json.dumps(report, indent=1))
        for name in report["unspawnable_prefabs"]:
            print(f"  PREFAB THE GAME CANNOT SPAWN: {name} is not resolvable in "
                  f"{EVIDENCE_PATH.name}", file=sys.stderr)
        raise SystemExit("refusing to spawn: a fixture names a prefab the game "
                         "does not have, so it would be silently absent from the "
                         "site. Fix the name in the preset.")

    if body is not None:
        report["audit"] = fixtures.audit(
            [(p["prefab"], p["x"], p["y"], p["z"], p.get("yaw", 0.0), p.get("tag"))
             for role, p in to_place if role != "pair_hub_end"], body)
        audit = report["audit"]
        # Three separate refusals, because they are three separate wrongs.
        if audit["failures"]:
            print(json.dumps(report, indent=1))
            for f in audit["failures"]:
                print(f"  FIXTURE INSIDE THE STRUCTURE: {f}", file=sys.stderr)
            raise SystemExit("refusing to spawn: a fixture would stand inside the "
                             "building. That is the defect the operator reported as "
                             "'portals in the middle of walls'.")
        if audit["portal_failures"]:
            print(json.dumps(report, indent=1))
            for f in audit["portal_failures"]:
                print(f"  PORTAL WITH NO USABLE TAG: {f}", file=sys.stderr)
            raise SystemExit("refusing to spawn: a portal would be placed with no "
                             "usable tag, and would pair at random with an unknown "
                             "portal somewhere in the world. That is the defect the "
                             "operator reported as 'i went through portal and on the "
                             "other side there wasnt a portal to return with'.")
        if audit["preference_failures"]:
            print(json.dumps(report, indent=1))
            for f in audit["preference_failures"]:
                print(f"  FIXTURE IN THE WRONG KIND OF PLACE: {f}", file=sys.stderr)
            raise SystemExit("refusing to spawn: a fixture stands somewhere legal "
                             "and wrong -- see above. A bed in the rain is not a "
                             "placement, and silently accepting one is the defect "
                             "the operator reported as 'there were hearth and portal "
                             "and some things outside the building'.")
        # LOUD, on stderr, and NOT fatal: this body cannot house these, and that
        # is a fact about the body rather than a bug in the placement. It is the
        # thing that used to be papered over by standing them in the yard.
        for row in plan["preference_unplaceable"]:
            print(f"  NOT PLACED, PREFERENCE UNSATISFIABLE: {row['prefab']} wants "
                  f"{row['want']} and this body offers "
                  f"{row['indoor_covered_cells']} indoor covered cell(s)",
                  file=sys.stderr)
        for note in audit["preference_unsatisfiable"]:
            print(f"  THIS BODY HAS NO INTERIOR: {note}", file=sys.stderr)
        for w in audit["portal_warnings"]:
            print(f"  PORTAL WARNING: {w}", file=sys.stderr)
        for row in plan["stations"]:
            for ext in row["extensions"]:
                if ext["place"] and not ext.get("within_game_range", True):
                    print(f"  EXTENSION OUT OF ITS STATION'S RANGE: {ext['prefab']} "
                          f"is {ext['distance_m']:.3f} m from {row['prefab']}, and "
                          f"`StationExtension::m_maxStationDistance` is "
                          f"{MAX_STATION_DISTANCE_M} m (MEASURED)", file=sys.stderr)

    if args.plan:
        print(json.dumps(report, indent=1))
        if args.report:
            Path(args.report).write_text(json.dumps(report, indent=1))
        return 0

    # WHICH of the solved fixtures this run actually spawns. The SOLVE is
    # untouched by the phase -- every position, including the portal's, is
    # decided from the same deterministic search over the same `taken` list --
    # so a `--skip-portals` run and a later `--only-portals` run agree on where
    # everything stands. Filtering at spawn time rather than at solve time is
    # the whole reason the two halves compose.
    def in_phase(prefab: str) -> bool:
        if args.skip_portals:
            return not fixtures.is_portal(prefab)
        if args.only_portals:
            return fixtures.is_portal(prefab)
        return True

    report["phase"] = ("skip_portals" if args.skip_portals
                       else "only_portals" if args.only_portals else "all")
    report["not_spawned_this_phase"] = [
        {"prefab": p["prefab"], "role": role, "tag": p.get("tag"),
         "at": [round(p["x"], 2), round(p["y"], 2), round(p["z"], 2)]}
        for role, p in to_place if not in_phase(p["prefab"])]
    spawn_list = [(role, p) for role, p in to_place if in_phase(p["prefab"])]

    # THE WRITE SESSION. Every spawn below is `flatten.install_emit`: appended
    # to `ledger/runs/<world>/ledger.jsonl` under `--actor` FIRST, then sent,
    # then its postcondition measured. `b.srv.rc` is the raw socket and is
    # used for READS only (findObjects, showContainer).
    postcondition_failed = flatten._ledger("live").PostconditionFailed
    with flatten.live_builder()(actor=args.actor, world=world) as b:
        if body is not None:
            records = b.led.records()
            if not standing_body_plans(
                    records, args.id,
                    flatten._ledger("writer").retired_seqs(records)):
                raise SystemExit(
                    f"refusing to stock {args.preset}/{args.id}: it names "
                    f"blueprint {place.get('blueprint')} and the {world} ledger "
                    f"holds NO live spawn_plan with site_id={args.id}, so its "
                    f"body has never been built. The body must stand before "
                    f"its fixtures (buildstage step 6 requires `bodies`). "
                    f"MEASURED on the previous Fresh42 drive: step 6 stocked "
                    f"13 of 13 sites at rc 0 while 0 installation bodies had "
                    f"ever been spawned (step 5 wrote 4 plans and executed "
                    f"none). Nothing was spawned.")
        # THE SEED THE WALK-INS WERE KEPT AGAINST, on the ledger with how it
        # was read: the placement it produced is not reproducible from the
        # ledger alone without it.  After the body check, so a refused site
        # appends nothing.
        if seen["read"]:
            b.observe(f"seed objects round {args.id}'s walk-ins",
                      "MEASURED: `objects_count id=*` tiles for the names, "
                      "then a bounded, height-stepped `findObjects "
                      "-detailed` per natural prefab for position and "
                      "rotation; kept where the gate's own solid for it "
                      "reaches the pad's walk-ins",
                      "tools/jumpstart/settlements/build.py::seed_census",
                      seen, role="site_prop", site_id=args.id)
        # --- THE PAIR: both ends, or neither, decided against THIS ledger
        #     before anything is sent.  The rest of the site is stocked
        #     either way: a missing pair is visible and names its cause, a
        #     one-ended tag is a random door.
        if pair is not None:
            now = b.led.records()
            why = pair_verdict(now, flatten._ledger("writer").retired_seqs(now),
                               pair["hub_id"], own_end, pair["hub_end"])
            if why and pair.get("hub_unseated") and pair["hub_end"] is None:
                why += f" ({unseated_reason(pair['hub_unseated'])})"
            elif why and pair.get("hub_walkin") and pair["hub_end"] is None:
                why += f" ({walkin_reason(pair['hub_walkin'])})"
            spawn_list = pair_spawn_list(spawn_list, pair, why)
            report["pair"]["verdict"] = why or "both ends placed in this run"
            if why:
                msg = (f"NO PORTAL PAIR for {args.preset}/{args.id} "
                       f"({pair['tag']}): {why}. Neither end was placed; the "
                       f"pair is this site's to place once the cause is gone.")
                b.note(msg, role="site_prop", site_id=args.id)
                print(f"  {msg}", file=sys.stderr)
        chest_ids: list[str] = []
        portals_spawned: list[dict] = []
        spawn_failures: list[dict] = []

        # --- REPAIR: ask the world what is already standing, and spawn only the
        #     rest. The alternative to this mode is a choice between doubling a
        #     site's fixtures and leaving a failed spawn missing forever, and
        #     both of those have happened.
        if args.repair:
            already: list[dict] = []
            keep: list = []
            for role, p in spawn_list:
                if role == "chest":
                    already.append({"prefab": p["prefab"], "role": role,
                                    "skipped": "chests are never re-placed by "
                                               "--repair"})
                    continue
                found = find_at(b.srv.rc, p["prefab"], p["x"], p["y"], p["z"],
                                args.probe_half)
                if found:
                    already.append({
                        "prefab": p["prefab"], "role": role,
                        "id": found[0]["id"], "at": found[0]["at"],
                        "tag": found[0]["tag"],
                        "distance_m": found[0]["distance_m"]})
                else:
                    keep.append((role, p))
                time.sleep(0.15)
            report["repair_already_present"] = already
            report["repair_to_spawn"] = [
                {"prefab": p["prefab"], "role": role, "tag": p.get("tag"),
                 "at": [round(p["x"], 2), round(p["y"], 2), round(p["z"], 2)]}
                for role, p in keep]
            spawn_list = keep
            print(f"repair: {len(already)} already standing, "
                  f"{len(keep)} to spawn", file=sys.stderr)

        for role, p in spawn_list:
            rec = fixture_record(p.get("site_id", args.id), role, p,
                                 p.get("preset", args.preset),
                                 exact_radius(p, to_place))
            at = [round(p["x"], 2), round(p["y"], 2), round(p["z"], 2)]
            # A postcondition that does not hold is THIS fixture's failure,
            # not the run's: the record stays in the ledger (that is the
            # point), the fixture is reported, and the rest of the site is
            # still stocked. A REFUSED record (`LedgerError`: schema, or a
            # third end for a portal tag) is not caught -- nothing was sent.
            try:
                seq, failed = flatten.install_emit(b, rec)["seq"], None
            except postcondition_failed as exc:
                seq, failed = None, str(exc)[:400]
            if rec["op"] == "portal":
                # `spawn_object` carries the tag, and the record's own
                # `tag_readback` has already asked the world for it; the batch
                # read-back below is kept as the report's census.
                entry = {
                    "prefab": p["prefab"], "id": None, "tag": p["tag"],
                    "role": role, "at": at, "ledger_seq": seq,
                    "offset": p.get("offset"), "clearance_m": p.get("clearance_m"),
                    "note": "spawn_object carries the tag; consoleCommand's reply is "
                            "a fixed echo, so the tag is read back from the world"}
                if failed:
                    entry.update(spawn_failed=True, why=failed)
                    spawn_failures.append(dict(entry))
                report["props"].append(entry)
                portals_spawned.append(dict(entry, want_x=p["x"], want_y=p["y"],
                                            want_z=p["z"]))
                continue
            entry = {"prefab": p["prefab"], "at": at, "ledger_seq": seq,
                     "offset": p.get("offset"), "clearance_m": p.get("clearance_m")}
            oid = None
            if role == "chest" and not failed:
                # `spawn_object`'s reply is a fixed echo, so a chest's ZDOID --
                # which `addItemToContainer` needs -- is READ BACK: the nearest
                # piece of this prefab within 1.0 m of where it was sent.
                mine = [f for f in find_at(b.srv.rc, p["prefab"], p["x"],
                                           p["y"], p["z"])
                        if f["distance_m"] <= 1.0]
                if mine:
                    oid = min(mine, key=lambda f: f["distance_m"])["id"]
                else:
                    failed = (f"findObjects found no {p['prefab']} within 1.0 m "
                              f"of {at}, so the chest has no ZDOID to fill")
                entry["id"] = oid
            if failed:
                # Only the CHEST branch used to notice a spawn that produced
                # nothing, which is how three cauldron extensions came back
                # `"id": null` at `mistlands-blackforge-base` and stayed absent
                # from the world with a zero exit status.
                entry.update(spawn_failed=True, why=failed)
                spawn_failures.append(dict(entry, role=role))
            if role == "chest":
                if oid:
                    chest_ids.append(oid)
            elif role == "prop":
                report["props"].append(entry)
            else:
                report["stations"].append(dict(entry, role=role))
        report["owned_by_blueprint"] = plan["carried_by_blueprint"]
        report["not_placed_because_the_blueprint_carries_them"] = plan["skipped"]
        report["spawn_failures"] = spawn_failures
        for f in spawn_failures:
            print(f"  SPAWN FAILED: {f['prefab']} ({f['role']}) at {f['at']} -- "
                  f"{f['why']}", file=sys.stderr)

        # --- read every spawned portal's tag OUT OF THE WORLD.
        #
        # The acceptance question is not "did we send the right base64" but
        # "what does the ZDO hold", and MEASURED on Ulfsland tonight
        # `findObjects` prints it: `-Prefab: portal_wood Id: 91870:1 Position:
        # (...) Creator: 0 Health: 400 Portal tag:  (author )`. A 2 m cube
        # around a known point matches exactly one portal, so the reply is one
        # line -- which matters, because `findObjects`' listing IS the logged
        # text and an unbounded one wedges the main thread.
        if portals_spawned:
            checks = []
            for entry in portals_spawned:
                found = find_at(b.srv.rc, entry["prefab"], entry["want_x"],
                                entry["want_y"], entry["want_z"])
                mine = [f for f in found if f["distance_m"] <= 1.0]
                checks.append({
                    "prefab": entry["prefab"], "wanted_tag": entry["tag"],
                    "at": entry["at"], "role": entry["role"],
                    "found": found,
                    "tag_in_world": mine[0]["tag"] if mine else None,
                    "id": mine[0]["id"] if mine else None,
                    "matches": bool(mine) and mine[0]["tag"] == entry["tag"],
                    "tag_len": len(entry["tag"]),
                    "within_tag_cap": len(entry["tag"]) <= fixtures.PORTAL_TAG_MAX_CHARS,
                })
                time.sleep(0.2)
            report["portal_verification"] = checks
            bad = [c for c in checks if not c["matches"]]
            for c in bad:
                print(f"  PORTAL TAG NOT IN THE WORLD: wanted {c['wanted_tag']!r} at "
                      f"{c['at']}, world holds {c['tag_in_world']!r}", file=sys.stderr)
            report["portal_verification_ok"] = not bad

        if args.only_portals or args.repair or scope == "portal_only":
            # Nothing else belongs here. For the two PHASES the chests were
            # placed and filled by the run that stocked the site, and re-placing
            # them would double the bank. For `portal_only` SCOPE there is no
            # bank at all and no manifest to fill it from, so an empty
            # `chest_ids` is the correct answer rather than a failure.
            # `save` is not a ledger op (the settlements builders send it the
            # same way): it changes no world state a replay must reproduce.
            b.srv.command("save")
            time.sleep(1)
            print(json.dumps(report, indent=1))
            if args.report:
                Path(args.report).write_text(json.dumps(report, indent=1))
            return 1 if spawn_failures else 0
        if not chest_ids:
            raise SystemExit("no chests placed")

        # --- fill.
        #
        # MEASURED: `addItemToContainer <id> Coal -count 300` answers
        # "Item Coal x300 added to ..." but the chest ends up holding ONE stack of 50.
        # The verb adds a single ItemDrop whose stack the game clamps to the item's
        # own max, so a 300-unit line needs six calls, and the max stack per item is
        # not knowable up front.  So: add once, read the container to learn the stack
        # size that item actually took, then repeat until the manifest figure is met.
        # Capacity is discovered the same way - the chest refuses when it is full.
        #
        # THE FILL IS OFF-LEDGER, AND SAYS SO. `addItemToContainer` is not a
        # spawn, and the ledger has no container postcondition: `replay.
        # check_expect` measures no container contents, and an op whose
        # `expect` it cannot measure FAILS as `none` -- so there is no record
        # this file could append that would verify. It goes through
        # `b.srv.command` (so it is at least in the session's `sent` list), and
        # each chest's read-back below is appended as ONE MEASURED `observe`:
        # the ledger then holds what the chest was found to contain, not a
        # claim that the fill was replayable.
        pending = [(name, int(count)) for name, count in sorted(items.items())]
        # A PER-ITEM SEARCH OVER EVERY CHEST, not one advancing cursor. The
        # cursor was monotonic: the first item that filled a chest moved it on
        # permanently, so every later item started from the new chest and the
        # earlier ones were never offered another line. MEASURED on
        # `pre-elder#blackforest-walled-base` and `pre-elder#early-dock`: 300
        # Wood reported as overflow while all three chests held 19, 22 and 19
        # slots -- none of them full. A chest that refuses ONE item is not a
        # chest that is full, it is a chest with no free slot for THAT stack.
        requested: dict[str, dict[str, int]] = {z: {} for z in chest_ids}
        overflow: list[list] = []
        errors: list[list] = []
        for name, target in pending:
            remaining = target
            guard = 0
            full: set[str] = set()
            while remaining > 0 and guard < 64:
                guard += 1
                open_chests = [z for z in chest_ids if z not in full]
                if not open_chests:
                    overflow.append([name, remaining])
                    break
                zid = open_chests[0]
                before = show_container(b.srv.rc, zid)[1].get(name, 0)
                reply = b.srv.command(
                    f"addItemToContainer {zid} {name} -count {remaining}").strip()
                if not ADDED_RE.search(reply):
                    if "Failed to add item" in reply:
                        full.add(zid)
                        continue
                    errors.append([name, reply.splitlines()[0][:140]])
                    break
                after = show_container(b.srv.rc, zid)[1].get(name, 0)
                gained = after - before
                if gained <= 0:
                    # Reported success but nothing landed: no free slot here for
                    # this item. Another chest may still have one.
                    full.add(zid)
                    continue
                requested[zid][name] = requested[zid].get(name, 0) + gained
                remaining -= gained

        # --- read back every chest from the world
        for zid in chest_ids:
            slots, totals, reported = show_container(b.srv.rc, zid)
            want = requested[zid]
            matches = want == {k: v for k, v in totals.items() if k in want}
            report["chests"].append({
                "id": zid, "slots_used": slots, "container_reports": reported,
                "requested": want, "read_back": totals, "matches": matches,
            })
            b.observe(f"chest_contents {zid}",
                      "MEASURED: showContainer read-back of a chest this run "
                      "filled with addItemToContainer (the fill itself is not a "
                      "ledger op: the ledger has no container postcondition)",
                      "tools/jumpstart/terraform/stock.py",
                      {"id": zid, "requested": want, "read_back": totals,
                       "matches": matches, "slots_used": slots},
                      role="site_prop", site_id=args.id)
        report["overflow_not_placed"] = overflow
        report["errors"] = errors
        b.srv.command("save")
        time.sleep(1)

    print(json.dumps(report, indent=1))
    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=1))
    return 0


# ---------------------------------------------------------------------------
# SELFTEST: SEAT_DEPENDS_ON, neutered, on the drive-2 blackforge site as the
# ledger records it -- centre (-8163.5, 4417.5), the written 18.8 x 12.8 m
# pad at 40.57, the 80 m site square -- with a one-slab body built in memory
# from the real piece geometry, so every search is `fixtures.free_spot`'s
# own.  The cartography table is searched from the offset step 6 recorded for
# it (seq 1978, (-4, +8)).  No world, no ledger, no library body.
# ---------------------------------------------------------------------------
_BF = {"cx": -8163.5, "cz": 4417.5, "base_y": 40.57, "pad": (18.8, 12.8),
       "pad_half": 40.0}
_TABLE_OFFSET = (-4.0, 8.0)
_TABLE_1978 = {"prefab": "piece_cartographytable", "x": -8167.5, "y": 40.57,
               "z": 4425.5, "yaw": 0.0}


def _scene_body():
    from types import SimpleNamespace  # noqa: PLC0415
    slab = SimpleNamespace(prefab="stone_floor_2x2", pos=(0.0, 0.0, 0.0),
                           rot=(0.0, 0.0, 0.0, 1.0), scale=(1.0, 1.0, 1.0))
    return fixtures.place_body([slab], at=(_BF["cx"], _BF["base_y"],
                                           _BF["cz"]))


def _scene(props, seat, n_chests: int = 0) -> dict:
    return solve_placement({"id": "selftest"}, {"stations": []}, n_chests,
                           _BF["cx"], _BF["cz"], _BF["base_y"], 0.0,
                           _BF["pad_half"], props=props, body=_scene_body(),
                           seat=seat)


def _off_pad(plan: dict, pad) -> list[str]:
    """Every PLACED fixture whose footprint the pad leaves unlevelled."""
    judge = PadSeat(_BF["cx"], _BF["cz"], *pad, _BF["base_y"])
    return [f"{p['prefab']} at ({p['x']}, {p['z']}): "
            f"{judge.miss(foot_of(p))['unstamped']} vertices unlevelled"
            for p in plan_fixtures(plan) if judge(foot_of(p)) is None]


def _case_the_recorded_table_is_OFF_its_pad() -> list[str]:
    """The drive-2 row itself, judged by the seat: its ground is unlevelled
    by 2.6 m -- the operator's buried table, as a verdict."""
    seat = PadSeat(_BF["cx"], _BF["cz"], *_BF["pad"], _BF["base_y"])
    miss = seat.miss(foot_of(_TABLE_1978))
    return [] if seat(foot_of(_TABLE_1978)) is None and \
        miss["outside_pad_m"] > 2.0 else [f"seq 1978 reads seated: {miss}"]


def _case_the_table_searched_from_its_offset_lands_ON_the_pad() -> list[str]:
    plan = _scene([("piece_cartographytable", _TABLE_OFFSET, 0.0)],
                  site_seat(_BF))
    placed = plan_fixtures(plan)
    if len(placed) != 1:
        return [f"{len(placed)} placed, unseated {plan['unseated']}"]
    return _off_pad(plan, _BF["pad"])


def _case_NEUTER_site_square_the_table_stands_OFF_its_pad() -> list[str]:
    """LOSS ARM: the shipped rule stands it where drive 2 did -- (-4, +8),
    at the pad height, past the pad's edge."""
    global SEAT_MODE  # noqa: PLW0603
    SEAT_MODE = "site_square"
    try:
        plan = _scene([("piece_cartographytable", _TABLE_OFFSET, 0.0)],
                      site_seat(_BF))
    finally:
        SEAT_MODE = "written_pad"
    off = _off_pad(plan, _BF["pad"])
    at = [(p["offset"], p["y"]) for p in plan_fixtures(plan)]
    return [] if off and at == [([-4.0, 8.0], 40.57)] else [
        f"the shipped rule placed {at}, off pad: {off}"]


def _case_a_fixture_the_pad_cannot_hold_is_REFUSED_by_number() -> list[str]:
    """A pad with no room -- the slab's own 2 m -- places nothing off it:
    the table is refused, with how far off its site-square spot stands."""
    plan = _scene([("piece_cartographytable", _TABLE_OFFSET, 0.0)],
                  PadSeat(_BF["cx"], _BF["cz"], 2.0, 2.0, _BF["base_y"]))
    u = plan["unseated"]
    out = []
    if plan_fixtures(plan):
        out.append(f"placed {plan_fixtures(plan)} on a 2 m pad")
    if len(u) != 1 or u[0]["outside_pad_m"] <= 0 or not u[0]["unstamped"]:
        out.append(f"refusal carries no number: {u}")
    return out


_RING_PROPS = [("piece_cartographytable", _TABLE_OFFSET, 0.0),
               ("piece_maypole", (0.0, -8.0), 0.0)]
# A pad sized for the body alone, as every drive-2 installation pad was: the
# slab and a 2 m margin.
_BODY_PAD = (6.0, 6.0)


def _ringed(depends_on: tuple) -> tuple[dict, tuple[float, float]]:
    """The scene's ring (the unseated solve, as `fixture_ring` takes it) and
    the pad `flatten.pad_extent_at_yaw` derives for it at the written pad."""
    free = _scene(_RING_PROPS, None, n_chests=2)
    ring = ring_of([("fixture", foot_of(p)) for p in plan_fixtures(free)],
                   _BF["cx"], _BF["cz"])
    ext = {"unturned": (2.0, 2.0), "at_yaw": (2.0, 2.0)}
    pe = flatten.pad_extent_at_yaw(_BODY_PAD, (2.0, 2.0), ext,
                                   (_BF["cx"], _BF["cz"]), 0.0, 0.0,
                                   depends_on, ring)
    return free, (pe["w"], pe["d"])


def _case_the_ring_pad_seats_all_the_site_square_placed() -> list[str]:
    """SURVIVOR (monotone): on the pad sized for its ring, every fixture the
    site square placed is seated, at the same spot, and none is refused."""
    free, pad = _ringed(flatten.PAD_EXTENT_DEPENDS_ON)
    seated = _scene(_RING_PROPS, PadSeat(_BF["cx"], _BF["cz"], *pad,
                                         _BF["base_y"]), n_chests=2)
    key = lambda plan: [(p["prefab"], p["x"], p["y"], p["z"])  # noqa: E731
                        for p in plan_fixtures(plan)]
    out = []
    if not key(free) or key(seated) != key(free):
        out.append(f"seated {key(seated)} against site square {key(free)}")
    if seated["unseated"]:
        out.append(f"refused on the ring pad {pad}: {seated['unseated']}")
    return out


def _case_NEUTER_drop_fixture_ring_fixtures_are_REFUSED_not_off_pad(
        ) -> list[str]:
    """LOSS ARM of the pair: without the ring the pad stays the body's and
    what it cannot hold is refused by number -- none of it stands off it."""
    _free, pad = _ringed(tuple(t for t in flatten.PAD_EXTENT_DEPENDS_ON
                               if t != "fixture_ring"))
    seated = _scene(_RING_PROPS, PadSeat(_BF["cx"], _BF["cz"], *pad,
                                         _BF["base_y"]), n_chests=2)
    out = []
    if pad != _BODY_PAD:
        out.append(f"the ringless pad is {pad}, not the body's {_BODY_PAD}")
    if not seated["unseated"]:
        out.append("nothing refused on the ringless pad: the ring carries "
                   "nothing")
    out += _off_pad(seated, pad)
    return out


# ---------------------------------------------------------------------------
# SELFTEST: WALKIN_DEPENDS_ON, neutered, on a COLUMN of arches -- drive 3's
# indoor trio (u-stathub / u-blacbase / u-meadhall, 2 m apart on one facing
# axis) reduced to its arithmetic.  Three tags at yaw 0 on the blackforge
# scene's slab, a written pad one arch wide (`_COLUMN_PAD`): the box packing
# stands them at z -3, 0, +3, each clear of the others' BOXES and the middle
# one in both doorways.  Every verdict is the GATE's own
# (`accept/sections/portals.py::check_approach`), not this module's.
# ---------------------------------------------------------------------------
_COLUMN_TAGS = ("u-a", "u-b", "u-c")
_COLUMN_PAD = (6.0, 12.0)
_COLUMN_HALF = 6.0


def _column(seat) -> dict:
    return solve_placement({"id": "selftest"}, {"stations": []}, 0,
                           _BF["cx"], _BF["cz"], _BF["base_y"], 0.0,
                           _COLUMN_HALF, props=[], body=_scene_body(),
                           seat=seat, extra_portal_tags=_COLUMN_TAGS)


def _column_seat(pad=_COLUMN_PAD) -> PadSeat:
    return PadSeat(_BF["cx"], _BF["cz"], *pad, _BF["base_y"])


def _gate_refuses(plan, seed=()) -> dict:
    """`{(dx, dz): blockers}`: every arch of `plan` the gate finds with no
    walk-in, at its offset from the scene centre -- among the plan's
    fixtures and `seed`, the world's own objects the plan did not place."""
    I, _A, GP = _gate()
    geom, build = I.load_geometry()
    ss = [I.Solid(p["prefab"], (p["x"], p["y"], p["z"]),
                  I.yaw(p.get("yaw", 0.0)), geom.get(p["prefab"]) or [],
                  p["prefab"] in geom) for p in plan_fixtures(plan)]
    ss += list(seed)
    r, h = fixtures.walker_capsule()
    fs = GP.check_approach([s for s in ss if s.name == GP.PORTAL_PREFAB],
                           I.Grid(ss), r, h, {}, build)
    return {(round(f.where[0] - _BF["cx"], 2), round(f.where[2] - _BF["cz"], 2)):
            f.numbers["blockers"] for f in fs}


def _stood(plan) -> list:
    return sorted((round(r["place"]["x"] - _BF["cx"], 2),
                   round(r["place"]["z"] - _BF["cz"], 2))
                  for r in plan["extra_portals"] if r["place"])


def _case_NEUTER_boxes_only_the_middle_arch_stands_in_both_doorways(
        ) -> list[str]:
    """LOSS ARM: the shipped packing -- an arch a box among boxes -- stands
    all three, and the gate refuses the middle one, blocked on both faces
    by our own arches."""
    global WALKIN_MODE  # noqa: PLW0603
    WALKIN_MODE = "none"
    try:
        plan = _column(_column_seat())
    finally:
        WALKIN_MODE = "reserved"
    got = _gate_refuses(plan)
    return [] if _stood(plan) == [(0.0, -3.0), (0.0, 0.0), (0.0, 3.0)] and \
        got == {(0.0, 0.0): ["portal_wood", "portal_wood"]} else [
            f"shipped packing stood {_stood(plan)}, gate refused {got}"]


def _case_the_arch_the_pad_cannot_give_a_walk_in_is_REFUSED_by_number(
        ) -> list[str]:
    """With the book: two stand, each with a walk-in; the third's only
    walk-in spot is off the one-arch pad, so it is refused with that number
    rather than stood in a doorway."""
    plan = _column(_column_seat())
    out = [f"gate refused {t}: {b}" for t, b in _gate_refuses(plan).items()]
    if _stood(plan) != [(0.0, -3.0), (0.0, 0.0)]:
        out.append(f"stood {_stood(plan)}")
    third = plan["extra_portals"][2]
    u = third.get("unseated") or {}
    if third["place"] or not u.get("outside_pad_m", 0) > 0:
        out.append(f"u-c not refused by number: {third}")
    return out


def _case_the_ring_pad_holds_every_arch_with_its_walk_in() -> list[str]:
    """SURVIVOR (monotone): the ring (`fixture_ring`'s unseated solve, with
    the book) grows the pad, and on it all three stand where the ring put
    them, each with a walk-in -- 3 of 3, as the shipped packing stood them,
    and none in a doorway."""
    free = _column(None)
    ring = ring_of([("fixture", foot_of(p)) for p in plan_fixtures(free)],
                   _BF["cx"], _BF["cz"])
    ext = {"unturned": (2.0, 2.0), "at_yaw": (2.0, 2.0)}
    pe = flatten.pad_extent_at_yaw(_COLUMN_PAD, (2.0, 2.0), ext,
                                   (_BF["cx"], _BF["cz"]), 0.0, 0.0,
                                   flatten.PAD_EXTENT_DEPENDS_ON, ring)
    seated = _column(_column_seat((pe["w"], pe["d"])))
    out = [f"gate refused {t}: {b}" for t, b in _gate_refuses(seated).items()]
    if len(_stood(seated)) != 3 or _stood(seated) != _stood(free):
        out.append(f"ring pad {pe['w']} x {pe['d']} stood {_stood(seated)}, "
                   f"the ring {_stood(free)}")
    if (pe["w"], pe["d"]) == _COLUMN_PAD:
        out.append("the ring did not grow the pad")
    return out


def _case_a_walk_in_refusal_carries_its_number() -> list[str]:
    """`hub_arch` in a one-arch-wide lane with two arches standing (z 0 and
    z -3): every box-legal spot left would close the last walk-in of one of
    them, so it returns nothing -- and `refusal()` says how many spots were
    refused and why."""
    body = _scene_body()
    book = Walkins(body)
    y = _BF["base_y"]
    rows = [("portal_wood", _BF["cx"], y, _BF["cz"] + dz, 0.0)
            for dz in (0.0, -3.0)]
    book.commit(rows)
    taken = [fixtures.fixture_box(*r, pad=0.5) for r in rows]
    taken += [(_BF["cx"] + sx * 2.7 - (6.0 if sx < 0 else 0.0),
               _BF["cx"] + sx * 2.7 + (6.0 if sx > 0 else 0.0),
               y - 1.0, y + 10.0, _BF["cz"] - 10.0, _BF["cz"] + 10.0)
              for sx in (-1.0, 1.0)]
    arch, unseated = hub_arch(body, body.index(), body.interior(), _BF["cx"],
                              _BF["cz"], y, _COLUMN_HALF, 0.0, taken, None,
                              walkins=book)
    w = book.refusal()
    if arch is not None or unseated is not None:
        return [f"stood {arch} / {unseated} in a lane with no walk-in left"]
    if not w or w["vetoed"] < 1 or w["span_m"] != 6.0:
        return [f"refused without its number: {w}"]
    text = walkin_reason(w)
    return [] if str(w["vetoed"]) in text and "0.49 m walker" in text else [
        f"the refusal does not read its numbers: {text}"]


# ---------------------------------------------------------------------------
# SELFTEST: SEED_DEPENDS_ON, neutered, on drive 10's u-stathub reduced to its
# arithmetic.  One arch (a prop, the pad turned half a turn so the chest bank
# prefers the face the arch looks out of), three chests, and the Birch1 that
# stood 15 m out on the arch's OTHER face -- its pivot past the clearing, its
# mesh box in the corridor.  Drive 10's own rotation for it, its position
# taken through the arch.  Every verdict is the GATE's own.
# ---------------------------------------------------------------------------
_SEED_PAD = (30.0, 40.0)
_SEED_HALF = 20.0
_SEED_QUAT = (0.020763, -0.9149, 0.014695, 0.402878)


def _seed_row(dx: float = 2.16, dz: float = 6.96) -> tuple:
    """The tree as the seed world holds it: `(prefab, x, y, z, qx..qw)`."""
    return ("Birch1", _BF["cx"] + dx, _BF["base_y"], _BF["cz"] + dz,
            *_SEED_QUAT)


def _seed_solids(rows) -> list:
    return [gate_solid(r[0], *r[1:4], r[4:8]) for r in rows]


def _seeded_site(seed) -> dict:
    """The scene solved through `book_for(body, seed)`, as `main` solves a
    site whose seed it has read."""
    body = _scene_body()
    return solve_placement({"id": "selftest"}, {"stations": []}, 3,
                           _BF["cx"], _BF["cz"], _BF["base_y"], 180.0,
                           _SEED_HALF, portal_tag="u-seed",
                           props=[("portal_wood", (0.0, 8.0), 0.0)],
                           body=body, seat=_column_seat(_SEED_PAD),
                           walkins=book_for(body, seed))


def _standing(plan, prefab: str) -> list:
    return [(round(p["x"] - _BF["cx"], 2), round(p["z"] - _BF["cz"], 2))
            for p in plan_fixtures(plan) if p["prefab"] == prefab]


def _case_a_tree_closing_one_face_moves_the_chest_off_the_other(
        ) -> list[str]:
    """With the seed in the book the arch's one open face is held and the
    chest that stood in it is moved aside: the gate finds a walk-in, the
    arch stands where it did, and the chests are NOT where the book over
    the body alone put them."""
    seed = _seed_solids([_seed_row()])
    plan, free = _seeded_site(seed), _seeded_site(())
    out = [f"gate refuses {at}: {b}"
           for at, b in _gate_refuses(plan, seed).items()]
    if _standing(plan, "portal_wood") != [(0.0, -8.0)]:
        out.append(f"the arch stood {_standing(plan, 'portal_wood')}")
    if len(_standing(plan, "piece_chest")) != 3:
        out.append(f"{len(_standing(plan, 'piece_chest'))} of 3 chests stand")
    if _standing(plan, "piece_chest") == _standing(free, "piece_chest"):
        out.append("the tree moved no chest: this case passes without it")
    return out


def _case_NEUTER_seed_unread_the_chest_closes_the_other_face() -> list[str]:
    """LOSS ARM: the book over the body alone believes the arch's far face
    open, stands a chest 4 m out on the near one -- drive 10's seq 2736 --
    and the gate refuses the arch, blocked by our chest and the Birch1."""
    global SEED_MODE  # noqa: PLW0603
    seed = _seed_solids([_seed_row()])
    SEED_MODE = "none"
    try:
        plan = _seeded_site(seed)
    finally:
        SEED_MODE = "read"
    got = _gate_refuses(plan, seed)
    return [] if list(got) == [(0.0, -8.0)] and sorted(
        got[(0.0, -8.0)]) == ["Birch1", "piece_chest"] else [
        f"the unread solve stood the chests {_standing(plan, 'piece_chest')} "
        f"and the gate refused {got}"]


def _case_a_tree_beyond_every_walk_in_moves_nothing() -> list[str]:
    """SURVIVOR (monotone): the seed is held, and a tree 60 m from the pad
    is in no arch's corridor, so every fixture stands where the book over
    the body alone put it."""
    far = _seed_solids([_seed_row(dz=60.0)])
    held, free = _seeded_site(far), _seeded_site(())
    key = lambda plan: [(p["prefab"], p["x"], p["z"])  # noqa: E731
                        for p in plan_fixtures(plan)]
    return [] if key(held) == key(free) and key(free) else [
        f"a tree 60 m away moved fixtures: {key(held)} against {key(free)}"]


def _case_the_seed_read_off_the_world_is_what_the_book_holds() -> list[str]:
    """The whole path `main` takes, offline: the world serves the tree and one
    Birch1 200 m away; `seed_census` over `seed_reach` reads the first and
    not the second, and the plan solved over what it read is the plan solved
    over the tree itself."""
    SB = flatten._settlement_emitter()
    world = SB._SeedWorld([_seed_row(), _seed_row(dx=200.0, dz=0.0)])
    seat = _column_seat(_SEED_PAD)
    solids, seen = SB.seed_census(world, _BF["cx"], _BF["cz"], _BF["base_y"],
                                  seed_reach({"pad_half": _SEED_HALF}, seat))
    out = []
    if seen["within_reach"] != {"Birch1": 1} or seen["unlisted"]:
        out.append(f"read {seen['within_reach']} (unlisted "
                   f"{seen['unlisted']}), not the one tree in reach")
    true_seed = _seed_solids([_seed_row()])
    read, held = _seeded_site(solids), _seeded_site(true_seed)
    if _standing(read, "piece_chest") != _standing(held, "piece_chest"):
        out.append(f"the chests over the read seed stand "
                   f"{_standing(read, 'piece_chest')}, over the tree itself "
                   f"{_standing(held, 'piece_chest')}")
    out += [f"gate refuses {at}: {b}"
            for at, b in _gate_refuses(read, true_seed).items()]
    return out


def _case_a_site_with_no_arch_to_stand_reads_no_world() -> list[str]:
    """The world is read for a walk-in only where an arch can stand: a
    usable tag of its own, or hub ends to stand for other sites' tags."""
    got = {"tag": arch_may_stand("u-stathub", ()),
           "hub ends": arch_may_stand(None, ("u-a", "u-b")),
           "no tag": arch_may_stand(None, ()),
           "blank tag": arch_may_stand("", ()),
           "unusable tag": arch_may_stand("u stathub!", ())}
    want = {"tag": True, "hub ends": True, "no tag": False,
            "blank tag": False, "unusable tag": False}
    return [] if got == want else [f"arch_may_stand answered {got}"]


def _read_past_the_pad():
    """What `seed_census` reads of a world holding one Birch1 whose box
    stands 29.23 m from the centre: 3.6 m past the pad's far corner
    (25.61 m), where an arch has its walker's 6 m walk-in."""
    SB = flatten._settlement_emitter()
    world = SB._SeedWorld([_seed_row(dx=0.0, dz=-40.0)])
    return SB.seed_census(world, _BF["cx"], _BF["cz"], _BF["base_y"],
                          seed_reach({"pad_half": _SEED_HALF},
                                     _column_seat(_SEED_PAD)))


def _case_the_seed_read_reaches_past_the_pad_into_the_walk_in() -> list[str]:
    """SURVIVOR: an arch at the pad's far corner walks 6 m out of it, so a
    tree whose box stands past the pad is read, with the walker's radius."""
    solids, seen = _read_past_the_pad()
    return [] if len(solids) == 1 and not seen["unlisted"] else [
        f"read {seen['within_reach']} of the tree past the corner "
        f"(reach {seen['reach_m']} m)"]


def _case_NEUTER_reach_stops_at_the_pad_the_tree_past_it_is_unread(
        ) -> list[str]:
    """LOSS ARM: a reach of the pad's half-diagonal alone -- no span, no
    walker -- never sees the tree a walker at the far corner's arch walks
    into."""
    global walk_reach  # noqa: PLW0603
    real = walk_reach
    walk_reach = lambda half_diagonal: half_diagonal  # noqa: E731
    try:
        solids, seen = _read_past_the_pad()
    finally:
        walk_reach = real
    return [] if not solids else [
        f"a reach of the pad alone still read {seen['within_reach']}"]


_SEAT_CASES = (
    ("the_recorded_table_is_OFF_its_pad", _case_the_recorded_table_is_OFF_its_pad),
    ("the_table_searched_from_its_offset_lands_ON_the_pad",
     _case_the_table_searched_from_its_offset_lands_ON_the_pad),
    ("NEUTER_site_square_the_table_stands_OFF_its_pad",
     _case_NEUTER_site_square_the_table_stands_OFF_its_pad),
    ("a_fixture_the_pad_cannot_hold_is_REFUSED_by_number",
     _case_a_fixture_the_pad_cannot_hold_is_REFUSED_by_number),
    ("the_ring_pad_seats_all_the_site_square_placed",
     _case_the_ring_pad_seats_all_the_site_square_placed),
    ("NEUTER_drop_fixture_ring_fixtures_are_REFUSED_not_off_pad",
     _case_NEUTER_drop_fixture_ring_fixtures_are_REFUSED_not_off_pad),
    ("NEUTER_boxes_only_the_middle_arch_stands_in_both_doorways",
     _case_NEUTER_boxes_only_the_middle_arch_stands_in_both_doorways),
    ("the_arch_the_pad_cannot_give_a_walk_in_is_REFUSED_by_number",
     _case_the_arch_the_pad_cannot_give_a_walk_in_is_REFUSED_by_number),
    ("the_ring_pad_holds_every_arch_with_its_walk_in",
     _case_the_ring_pad_holds_every_arch_with_its_walk_in),
    ("a_walk_in_refusal_carries_its_number",
     _case_a_walk_in_refusal_carries_its_number),
    ("a_tree_closing_one_face_moves_the_chest_off_the_other",
     _case_a_tree_closing_one_face_moves_the_chest_off_the_other),
    ("NEUTER_seed_unread_the_chest_closes_the_other_face",
     _case_NEUTER_seed_unread_the_chest_closes_the_other_face),
    ("a_tree_beyond_every_walk_in_moves_nothing",
     _case_a_tree_beyond_every_walk_in_moves_nothing),
    ("the_seed_read_off_the_world_is_what_the_book_holds",
     _case_the_seed_read_off_the_world_is_what_the_book_holds),
    ("a_site_with_no_arch_to_stand_reads_no_world",
     _case_a_site_with_no_arch_to_stand_reads_no_world),
    ("the_seed_read_reaches_past_the_pad_into_the_walk_in",
     _case_the_seed_read_reaches_past_the_pad_into_the_walk_in),
    ("NEUTER_reach_stops_at_the_pad_the_tree_past_it_is_unread",
     _case_NEUTER_reach_stops_at_the_pad_the_tree_past_it_is_unread),
)


def selftest() -> int:
    bad = 0
    for name, fn in _SEAT_CASES:
        try:
            problems = fn()
        except BaseException as e:                         # noqa: BLE001
            problems = [f"raised {type(e).__name__}: {e}"]
        bad += bool(problems)
        print(f"  {'ok  ' if not problems else 'FAIL'}  {name}")
        for p in problems:
            print(f"          {p}")
    print(f"\n  fixture seat selftest {len(_SEAT_CASES) - bad}/"
          f"{len(_SEAT_CASES)}  {'PASS' if not bad else 'FAIL'}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(selftest() if sys.argv[1:2] == ["selftest"] else main())
