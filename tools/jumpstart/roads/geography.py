#!/usr/bin/env python3
"""THE SEED'S OWN GEOGRAPHY, DERIVED -- the call site that never existed.

WHAT THIS IS AND WHY IT IS NEW.  `roads/spec.py::NODES` carried 15 hand-copied
coordinates: 11 `extremity` nodes and 4 `abutment` nodes.  Their `provenance`
strings said where they came from -- "MEASURED, survey.py sector 0", "MEASURED
by Crossings, snapped to 2 m" -- and both producers ALREADY EXIST and are
ALREADY seed-parameterised:

  * `roads/survey.py::survey_patch` computes, for each 45 deg sector from a
    landmass centroid, the farthest sample at least `inland_m` from any water
    edge.  That IS the extremity definition.  Nothing read its output.
  * `crossings/survey.py` computes the distance transform between two land
    components and returns every approach with the nearest sample on the far
    bank.  That IS the abutment definition.  Nothing read its output.

So the numbers were re-typed by hand and then frozen, and a fresh seed got
Ulfsland's capes and Ulfsland's bridges.  This module is the missing call: it
runs those two producers against WHATEVER FIELD IT IS GIVEN and writes the
answer to `geography.yaml`, which `spec.py` reads through
`worldseed.require_scope` -- so a fresh-seed run either has its OWN geography
or is refused, and can no longer silently inherit another world's.

THERE IS NO NEW MATHS HERE.  Everything below is a call site, an id scheme, a
selection rule and a lattice snap.

---------------------------------------------------------------------------
THE AUTHORED / DERIVED SPLIT, STATED ONCE.

DERIVED per seed (this module):   a node's COORDINATE, and the measurements
                                  that justify it.
AUTHORED, frozen (the plan tables
below, plus `spec.EDGES`):        WHICH nodes exist, WHAT they are called, and
                                  WHICH ones a road joins.

The 40 edges in `spec.py` carry NO COORDINATE.  They are a topology over node
IDS.  So the edge list survives any seed whose node ids survive, and the only
thing that has to be stable across seeds is the ID SCHEME.

---------------------------------------------------------------------------
THE ID SCHEME, AND WHY IT IS STABLE.

An extremity's id is a function of (ISLAND ROLE, COMPASS SECTOR) and of nothing
else:

    ("spawn", 0)  -> "northcape"      ("west", 0)   -> "w-north"
    ("spawn", 45) -> "northeast"      ("west", 90)  -> "w-mid"
    ("spawn", 90) -> "eastlobe"       ("west", 180) -> "w-south"
    ...

Neither half of that key is a coordinate:

  * THE ISLAND ROLE is the patch window's role, not its position.
    `trunk-primary` is "the window holding the spawn point and the priority-1
    trunk"; `trunk-secondary` is "the other half of the main continent".  A
    seed moves the landmass; it does not stop there being a spawn island.
  * THE SECTOR is a bearing from that landmass's OWN centroid on a fixed 45 deg
    rose.  `survey_patch` emits at most one extremity per sector, so the key is
    unique by construction, and it means the same thing on every planet: "the
    farthest inland-safe point of this landmass, north-east of its middle".

An abutment's id is a function of (CROSSING ROLE, BANK SIDE).  `S1` is the
NARROWEST crossing between the two trunk landmasses; `S2` is the crossing
costing the fewest metres of new bridge-plus-spur measured from the portal hall
-- see `CROSSING_PLAN`.  Again neither is a coordinate.

WHAT THE SCHEME COSTS.  A sector that yields nothing -- no sample on that
bearing survives the inland condition -- is a MISSING NODE, and every edge
naming it would dangle.  This module REFUSES rather than emitting a partial
set, because a network with a silently absent terminus is exactly the failure
this project keeps finding: the check that went quiet instead of red.

---------------------------------------------------------------------------
THE LATTICE SNAP.

Every derived coordinate is snapped to 2 m.  That is not a rounding
convenience, it is the ribbon's own pitch: `network.py` publishes
`bank_snap_m: 2.0` as the crossing contract with Crossings, `route.py`'s
`find_crossings` snaps at 2.0, and `north.py` routes on a 2 m lattice.  A
terminus off that lattice makes the last step of a ribbon a half step.

MEASURED on seed Pirate68, against `/tmp/roads/h1m.bin`: all 11 frozen
extremity coordinates and all 4 frozen abutment coordinates are EXACTLY what
this module derives.  The hand-copying was faithful; it was just never
re-runnable, and "faithful once" is not a property a second seed inherits.
"""

from __future__ import annotations

import argparse
import importlib.util
import math
import os
import sys
from pathlib import Path

import yaml

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
if str(_HERE.parent) not in sys.path:
    sys.path.append(str(_HERE.parent))

import field as fieldmod  # noqa: E402
import emitscope  # noqa: E402
import worldseed  # noqa: E402

# ---------------------------------------------------------------------------
# WHERE THIS SEED'S GEOGRAPHY LIVES.
#
# ONE FILE PER SEED, RESOLVED BY THE RUN'S SEED -- not one frozen path.
# MEASURED 2026-09-21 by FreshSeedDrive: with a single `geography.yaml`, a
# Fresh42 run held a VALID Fresh42 geography on disk and had NO WAY TO HAND IT
# TO `spec.py`, because `load()` defaulted to the one path and `spec` calls it
# at import with no argument. The scope refusal was correct and UNCOMPLIABLE,
# which is the same state `roads/poi.py` was in an hour earlier: a guard whose
# remedy cannot be carried out is an invitation to work around it, and working
# around it means borrowing Ulfsland's numbers again.
#
# The default seed keeps the bare name, so Ulfsland's file does not move and no
# path in this repo changes. `$VH_GEOGRAPHY` overrides absolutely, for a run
# that holds its answer somewhere else.
GEOGRAPHY_ENV = "VH_GEOGRAPHY"


def geography_path(seed: str | None = None) -> Path:
    """The geography document for a seed: `geography.yaml` for the default seed,
    `geography.<seed>.yaml` otherwise, `$VH_GEOGRAPHY` over both."""
    override = os.environ.get(GEOGRAPHY_ENV)
    if override:
        return Path(override)
    s = seed or worldseed.seed()
    if s == worldseed.DEFAULT_SEED:
        return _HERE / "geography.yaml"
    return _HERE / f"geography.{s}.yaml"


# The default-seed document, kept as a module constant because that is the name
# every existing caller and every test already says.
GEOGRAPHY_YAML = _HERE / "geography.yaml"

# The ribbon lattice pitch.  See the module docstring; the same 2.0 is
# `network.py`'s published `bank_snap_m`.
NODE_SNAP_M = 2.0

# An abutment must not be founded in the water.  `field.WATER_LEVEL` is
# ZoneSystem::c_WaterLevel (MEASURED).  NOTE this is the WATER PLANE and not
# `land()`'s 30.5 freeboard line, and the difference is load-bearing: MEASURED
# on Pirate68, S1's near bank snaps onto a sample reading 30.32 -- under the
# freeboard but genuinely above water, and covered by the 30.6 deck datum.
# Walking it inland on the freeboard test would move a bank Crossings has
# already built a bridge against.
ABUTMENT_MIN_Y = fieldmod.WATER_LEVEL

# Crossings' MEASURED absolute span ceiling.  No bridge longer than this exists
# in this build, so no gap wider than this is a crossing candidate.
BRIDGE_MAX_SPAN_M = 96.0

# How far outward a snapped bank may be walked before the site is called wet.
BANK_WALK_MAX_M = 64.0

# ---------------------------------------------------------------------------
# AUTHORED.  Which island role each patch window plays, which extremities the
# network has nodes for, what those nodes are called, and which crossings exist.
# This is DESIGN, in the same class as `spec.EDGES`, and it is NOT re-derived
# per seed.
# ---------------------------------------------------------------------------

ISLAND_OF_WINDOW = {
    worldseed.TRUNK_PRIMARY: "spawn",
    worldseed.TRUNK_SECONDARY: "west",
}

# island role -> {sector_deg: node id}.  `survey_patch` emits all eight sectors;
# these are the ones the network has EDGES for.  The omissions are deliberate
# and carry their reason rather than being left to a predicate -- MEASURED on
# Pirate68:
#   spawn 225  is 166 m from the 180 extremity: the same cape on another
#              bearing, and one cape does not need two roads.
#   spawn 270/315 face the channel that S1 and S2 already cross, so a road to
#              them duplicates the crossing spurs.
#   west 45    is 104 m from the 0 extremity.
#   west 135   is 176 m from the S2 west abutment, which already carries a trunk.
EXTREMITY_PLAN: dict[str, dict[int, str]] = {
    "spawn": {0: "northcape", 45: "northeast", 90: "eastlobe",
              135: "southeast", 180: "southcape"},
    "west": {0: "w-north", 90: "w-mid", 180: "w-south",
             225: "w-southwest", 270: "w-west", 315: "w-northwest"},
}

# The crossings between the two trunk landmasses, and the RULE that sites each.
# Both rules are the ones already recorded in prose by the agent that made the
# original choice (`crossings/plan.py::BRIDGES`) -- executed here instead of
# quoted:
#
#   S1  "the join between the two halves of the largest landmass in the world
#        ... separated HERE by 12.0 m of water"        -> narrowest gap.
#   S2  "MEASURED span 31.9 m ... its spawn-island bank is ~30 m from the portal
#        hall centre, so the spur to it is nearly free"
#                                                      -> fewest TOTAL metres of
#        new construction: bridge span plus spur from the anchor.
#
# MEASURED on Pirate68: the narrowest gap is 13.0 m at (-311.5, -64.5) and
# argmin(span + spur) is 65.1 m at (-317.5, 230.5).  Snapped, those are exactly
# the frozen `brg-s1-e` and `brg-s2-e`.  The second rule CANNOT be evaluated on
# `gap_between`'s suppressed top-N list -- S2 is not on it -- which is why
# `crossings/survey.py::approaches` exists.
CROSSING_PLAN: tuple[dict, ...] = (
    {"id": "S1", "rule": "narrowest",
     "near": "brg-s1-e", "far": "brg-s1-w",
     "why": "the join between the two halves of the main continent, at its "
            "narrowest -- the cheapest structure that makes the west isle "
            "reachable on foot at all"},
    {"id": "S2", "rule": "cheapest_from_anchor", "anchor": "portalhub",
     "near": "brg-s2-e", "far": "brg-s2-w",
     "why": "the second crossing of the same channel, sited where bridge span "
            "plus spur from the portal hall is least: it closes a LOOP with S1 "
            "instead of leaving an out-and-back, and the spur is nearly free"},
)

# Nodes that are a GENERATOR LOCATION, sited by world generation itself and
# therefore already in this seed's location dump -- the same 12,301-instance
# artefact `roads/poi.py` masks the corridors against, and which it ALREADY
# scope-checks.  The coordinate was a literal here for the same reason as the
# others: the producer existed, was seed-parameterised, and was not called.
#
# NOT SNAPPED.  A location's centre is where the generator put it, and the
# road terminates on its PROTECTED clearance ring measured from that centre;
# moving it a metre onto the ribbon lattice would move the ring the terminus is
# computed against.
LOCATION_PLAN: tuple[dict, ...] = (
    {"id": "temple", "prefab": "StartTemple", "kind": "spawn_point",
     "why": "StartTemple: where any character without a bed respawns, with "
            "nothing pointing at it. A road from here is the physical half of "
            "WayFinding's fix. It is also a PROTECTED location, so the road "
            "terminates on its clearance ring, not in its courtyard."},
)

# Two crossings resolving to the same bank means the network's loop does not
# exist on this seed.  Four snap pitches is the smallest separation at which
# they are still two distinct structures rather than one measured twice.
CROSSING_MIN_SEPARATION_M = 4.0 * NODE_SNAP_M

# The deck datum every bank levels to.  AUTHORED (see `spec.DECK_DATUM_Y`): a
# design decision that every deck in the world sits at one height, not a
# measurement of any particular bank.
DECK_DATUM_Y = 30.6


def island_of_window(patch: str) -> str:
    """The island role a patch window plays, under either of the window's names."""
    fid = worldseed.FUNCTION_OF.get(patch, patch)
    return ISLAND_OF_WINDOW.get(fid, fid)


def window_of_island(island: str) -> str:
    for fid, role in ISLAND_OF_WINDOW.items():
        if role == island:
            return fid
    raise KeyError(f"no patch window plays island role {island!r}")


def joined_islands(absent: dict | None = None) -> set[str]:
    """The island roles a walk from the spawn island can reach over this seed's
    CROSSINGS: the spawn island, and the second trunk island only when at
    least one crossing of `CROSSING_PLAN` has BOTH banks on this seed.

    The same fact `network.rooted_islands` reads off `spec.DECLARED_CROSSINGS`
    once the node table exists; asked here from the geography document
    alone, so the solver of the sites -- which runs BEFORE that table can be
    built -- is not a second opinion about which island a road can start on.
    `absent` defaults to this seed's `absent_nodes()`."""
    absent = absent_nodes() if absent is None else absent
    spawn = ISLAND_OF_WINDOW[worldseed.TRUNK_PRIMARY]
    west = ISLAND_OF_WINDOW[worldseed.TRUNK_SECONDARY]
    out = {spawn}
    if any(c["near"] not in absent and c["far"] not in absent
           for c in CROSSING_PLAN):
        out.add(west)
    return out


def snap(v: float, pitch: float = NODE_SNAP_M) -> float:
    """The lattice coordinate a node sits on.  Halves go AWAY FROM ZERO, always.

    On a 1 m field every sample centre is a half-integer, so `v / pitch` is a
    quarter and a tie never arises at the default pitch; the rule is stated
    anyway, because a coarser field produces ties and "whatever `round` did" is
    not a specification -- see `sample_at` for what that costs when it is left
    implicit.
    """
    q = v / pitch
    # `floor(|q| + 0.5)` on the MAGNITUDE, sign restored after: that is
    # half-away-from-zero for both signs with one expression. Writing the
    # negative branch as `ceil(-q - 0.5)` agrees everywhere EXCEPT on the ties
    # this rule exists to decide -- snap(-1.0) would return -0.0 instead of
    # -2.0 -- which is the whole failure mode of an unstated rounding rule.
    return math.copysign(pitch * math.floor(abs(q) + 0.5), v)


def sample_at(fld, x: float, z: float) -> tuple[float, tuple[float, float]]:
    """The height at the lattice sample nearest (x, z), TIES UP, and which
    sample that was.

    WHY NOT `Field.index`.  `Field.index` resolves with `int(round(...))`, which
    is Python's round-half-to-EVEN.  A derived node lands on an integer metre
    while a 1 m field's samples sit on half-integers, so every one of these
    queries is an exact tie -- and banker's rounding then resolves ADJACENT
    QUERIES IN OPPOSITE DIRECTIONS.  MEASURED on Pirate68 westisle at z = -64:
    `at(-324)` reads the sample at -323.5 and `at(-325)` reads the one at
    -325.5, so two queries 1 m apart read samples 2 m apart and the sample at
    -324.5 is never returned by either.  A bank walk stepping 1 m at a time over
    that lookup can step straight over its own answer.  Half-up is the
    convention this project already uses for `Heightmap.GetHeight` queries, and
    it is used here for the same reason: the answer must not depend on the
    parity of a float.
    """
    j = int(math.floor((x - fld.x0) / fld.step + 0.5))
    i = int(math.floor((z - fld.z0) / fld.step + 0.5))
    if not (0 <= i < fld.n and 0 <= j < fld.n):
        raise ValueError(f"({x}, {z}) is outside field {fld.id}")
    h = getattr(fld, "h", None)
    if h is None:  # crossings' `water.Field` names the same array `heights`
        h = fld.heights
    return float(h[i, j]), (fld.x0 + j * fld.step, fld.z0 + i * fld.step)


# ---------------------------------------------------------------------------
# DERIVATION -- the 11 extremities, from `roads/survey.py::survey_patch`
# ---------------------------------------------------------------------------

def _by_path(name: str, path: Path):
    """Load a module by PATH under a name that cannot collide.

    `roads/survey.py` and `crossings/survey.py` are both `survey`, and both
    prepend their own directory to `sys.path` when they load.  Whichever went
    first would own `sys.modules['survey']` for the rest of the process, so a
    plain `import survey` here returns a DIFFERENT module depending on what ran
    before it -- MEASURED: the first run of this module got
    `crossings/survey.py` and died on a missing `INLAND_M`. Loading by path is
    the only form of this import that means the same thing every time.
    """
    mod = sys.modules.get(name)
    if mod is not None:
        return mod
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def roads_survey():
    """`roads/survey.py`, loaded by path.  See `_by_path`."""
    return _by_path("roads_survey", _HERE / "survey.py")


# ---------------------------------------------------------------------------
# EXTREMITY_REACH_DEPENDS_ON: AN EXTREMITY IS WHERE A ROAD CAN GET TO.
#
# `survey_patch` picks the farthest sample of the window's largest LAND
# component at least `INLAND_M` from water.  Land is not road: the router
# walks a 4 m corridor over the achievable surface, refusing any neighbour
# step past `route.GRADE_CEILING` and every generator location's keep-out,
# and a sample of the big landmass can sit in a pocket of that graph with no
# way in.  MEASURED on Fresh42 (Fresh42_1m.bin, trunk corridor, generator
# keep-outs, band-free): `w-mid` (-6826, -1940) snaps onto a pocket of 10
# cells, `w-northwest` (-8046, -582) onto one of 7, `w-west` (-8642, -2070)
# stands in a generator keep-out whose nearest allowed cell is a pocket of
# 4, and `w-south` (-6988, -3094) is 0.5 m outside the corridor grid of its
# own clipped window -- against 222,817 cells in trunk-secondary's largest
# strong component.  W3/W4/W6/W7 came back UNROUTABLE on drive 4 -- "no
# route found" band-free, "no allowed cell within 200.0 m" under the census,
# which never read ground no line had reached.
#
# THE RULE: the farthest inland-safe sample of the sector whose ROUTER SNAP
# -- the nearest allowed cell within `Corridor.nearest_allowed`'s own search
# radius, exactly where a search at the node starts -- lies in the window's
# largest STRONG component of the trunk corridor's DIRECTED graph (a step
# into water carries no grade test, a step out of it does), the widest
# class, whose keep-outs hold every narrower one's.  When survey_patch's own
# answer qualifies it is kept to the decimal; when it does not, the node
# moves and records where from.  MEASURED re-derivation: Fresh42 moves
# w-mid, w-northwest, w-south and w-west (2-91 m) and keeps the five
# spawn-island capes; Pirate68 moves NOTHING -- all 16 nodes and every other
# key of geography.yaml re-derive identical.
#   the_routers_own_graph   `route.Corridor` at the trunk grade: its
#       achievable surface, span ceiling, GRADE_CEILING and snap radius.
#   the_generator_keep_outs   `poi.masks` over this seed's own dump, at the
#       trunk half-width, exactly as `network.py plan` masks it.
# NEUTER `_EXTREMITY_REACH_MODE = "land"`: the shipped rule, largest land
# component only.  Proven by `selftest_extremity_reach`.
# ---------------------------------------------------------------------------
EXTREMITY_REACH_DEPENDS_ON = ("the_routers_own_graph",
                              "the_generator_keep_outs")
_EXTREMITY_REACH_MODE = "traversable"   # traversable | land (the shipped)


# THE SAME GRAPH ANSWERS THE SAME QUESTION OF A SITE (ROAD_REACH_DEPENDS_ON
# in `settlements/road_reach.py`): a watchtower is "where a road can get to"
# on exactly the terms an extremity is, and the two were not asked the same
# thing -- MEASURED on Pirate68 with the 15 frozen site pins removed, the
# solver put `wt-spawn` (1432, 1546) in a 186-cell pocket of this graph and
# `wt-north`, `wt-east`, `lh-east`, `lh-west`, `tree-west`, `tree-near` on
# a 62,618-cell island of it, against 269,270 cells in the spawn window's
# largest strong component, and seven legs came back UNROUTABLE.  `RoadGraph`
# is that graph with its numbers kept; `traversable_reach` is the extremity
# derivation's reading of it, unchanged.
class RoadGraph:
    """The router's own graph over one window, labelled, with its numbers.

    `cls` is the carriageway class the graph is the router's for: its grade
    (the achievable surface the search walks) and its keep-out half-width
    (`poi.masks`, exactly as `network.py plan` masks that class).  `forbid`
    overrides the generator keep-out mask (a fixture has no dump); `loc`
    names the dump to read it from, default this seed's own.
    """

    HALF_WIDTH_M = {"trunk": 3.0, "spur": 2.0, "mountain": 2.0}

    def __init__(self, fld, forbid=None, cls: str = "trunk", loc=None, root=None):
        import numpy as np
        from scipy import ndimage, sparse
        from scipy.sparse import csgraph
        import route as routemod                        # noqa: PLC0415

        self.cls = cls
        self.corr = corr = routemod.Corridor(
            fld, cell_m=4.0, grade_max=routemod.GRADE[cls],
            bridge_entry_cost_m=300.0, bridge_per_m_cost=15.0)
        if forbid is None:
            poimod = _by_path("roads_poi", _HERE / "poi.py")
            forbid, _soft, _t = poimod.masks(
                poimod.load(loc), corr.x0, corr.z0, corr.cell_m, corr.m,
                self.HALF_WIDTH_M[cls])
        m = corr.m
        self.ok = ok = ~forbid & ~corr.water_forbidden
        idx = np.arange(m * m).reshape(m, m)
        rows, cols = [], []
        for di, dj in ((0, 1), (1, 0), (1, 1), (1, -1)):
            a = (slice(0, m - di), slice(max(0, -dj), m - max(0, dj)))
            b = (slice(di, m), slice(max(0, dj), m - max(0, -dj)))
            d = corr.cell_m * (math.sqrt(2.0) if di and dj else 1.0)
            g = np.abs(corr.S[b] - corr.S[a]) / d
            both = ok[a] & ok[b]
            # `_step_cost` grades a step onto LAND only: a step into water is a
            # bridge and carries no grade test.  So the graph is DIRECTED, and a
            # node a road can both reach and leave is a STRONG component's.
            fwd = both & ((g <= routemod.GRADE_CEILING) | corr.water[b])
            bwd = both & ((g <= routemod.GRADE_CEILING) | corr.water[a])
            rows += [idx[a][fwd], idx[b][bwd]]
            cols += [idx[b][fwd], idx[a][bwd]]
        r, c = np.concatenate(rows), np.concatenate(cols)
        _n, lab = csgraph.connected_components(
            sparse.coo_matrix((np.ones_like(r), (r, c)), shape=(m * m, m * m)),
            directed=True, connection="strong")
        self.lab = lab.reshape(m, m)
        self.sizes = (np.bincount(self.lab[ok], minlength=int(lab.max()) + 1)
                      if ok.any() else np.zeros(1, dtype=int))
        self.big = int(np.argmax(self.sizes))
        # THE WINDOW'S TRUNK LANDMASS: its largest LAND component, 8-connected
        # at the corridor's own cell (a cell is land when any sample of it is
        # above the water plane) -- the definition `survey_patch` and
        # `_sector_samples` already use for the extremities.  The graph above
        # lets a road cross open water up to the span ceiling, which is how the
        # two halves of the main continent are one; it is not a licence to
        # bridge to every islet within 96 m of the coast.  A site on ANOTHER
        # landmass is reached by a dock, a ferry or a portal, and `at` says
        # which landmass a point stands on so the caller can say so.
        land_lab, land_n = ndimage.label(~corr.water,
                                         structure=np.ones((3, 3)))
        self.land_lab = land_lab
        self.land_sizes = np.bincount(land_lab.ravel(), minlength=land_n + 1)
        self.land_sizes[0] = 0
        self.land_big = int(np.argmax(self.land_sizes))
        # THE ROUTER STANDS WHERE IT SNAPS.  `Corridor.nearest_allowed` moves a
        # node off a wet or forbidden cell to the nearest allowed one within
        # `SNAP_SEARCH_M`, so a node inside a keep-out whose snap lands on the
        # big component routes (MEASURED: Fresh42 northcape snaps 10 m out of
        # TrollCave02's keep-out and T6 routes).  What it can never stand on is
        # a snap in a pocket, or no snap at all.
        allowed = ~(corr.wet | forbid)
        self.dist, (self.si, self.sj) = ndimage.distance_transform_edt(
            ~allowed, return_indices=True)
        self.snap_m = routemod.Corridor.nearest_allowed.__defaults__[0]
        # A measured root can occupy a smaller component than an unrelated
        # landmass in an expanded survey. Never replace that root by the
        # survey's largest component, or snap it across open water.
        self.root = None if root is None else tuple(map(float, root))
        self.root_snap_m = None
        self.network_id, self.trunk_land_id = self.big, self.land_big
        if self.root is not None:
            self.network_id = self.trunk_land_id = -1
            j = int(round((self.root[0] - corr.x0) / corr.cell_m))
            i = int(round((self.root[1] - corr.z0) / corr.cell_m))
            if 0 <= i < m and 0 <= j < m:
                pi, pj = self.si[i, j], self.sj[i, j]
                self.root_snap_m = float(self.dist[i, j] * corr.cell_m)
                root_land = int(self.land_lab[i, j])
                if (self.root_snap_m <= self.snap_m and self.ok[pi, pj]
                        and root_land > 0
                        and int(self.land_lab[pi, pj]) == root_land):
                    self.network_id = int(self.lab[pi, pj])
                    self.trunk_land_id = root_land

    @property
    def network_cells(self) -> int:
        return 0 if self.network_id < 0 else int(self.sizes[self.network_id])


    @property
    def largest_cells(self) -> int:
        return int(self.sizes[self.big])

    @property
    def trunk_land_cells(self) -> int:
        return (0 if self.trunk_land_id < 0
                else int(self.land_sizes[self.trunk_land_id]))

    def at(self, xs, zs) -> dict:
        """Where each point stands in the graph, as arrays over the points:
        `reached` (on an allowed cell of the selected strong component, within
        the router's snap), `piece_cells` (the size of the component its snap
        stands in; 0 when it has none), `snap_m` (the distance to that snap),
        `inside` (the point is on the corridor grid at all), `land_cells`
        (the size of the LAND component its snap stands on) and
        `on_trunk_landmass` (that component is the selected trunk landmass)."""
        import numpy as np
        corr, m = self.corr, self.corr.m
        xs, zs = np.asarray(xs, dtype=float), np.asarray(zs, dtype=float)
        j = np.rint((xs - corr.x0) / corr.cell_m).astype(int)
        i = np.rint((zs - corr.z0) / corr.cell_m).astype(int)
        inside = (i >= 0) & (i < m) & (j >= 0) & (j < m)
        reached = np.zeros(xs.shape, dtype=bool)
        piece = np.zeros(xs.shape, dtype=int)
        land = np.zeros(xs.shape, dtype=int)
        on_land = np.zeros(xs.shape, dtype=bool)
        snap = np.full(xs.shape, np.inf)
        ii, jj = i[inside], j[inside]
        pi, pj = self.si[ii, jj], self.sj[ii, jj]
        near = self.dist[ii, jj] * corr.cell_m
        snap[inside] = near
        stands = (near <= self.snap_m) & self.ok[pi, pj]
        reached[inside] = stands & (self.lab[pi, pj] == self.network_id)
        piece[inside] = np.where(stands, self.sizes[self.lab[pi, pj]], 0)
        ll = self.land_lab[pi, pj]
        land[inside] = np.where(stands, self.land_sizes[ll], 0)
        on_land[inside] = stands & (ll == self.trunk_land_id)
        return {"reached": reached, "piece_cells": piece, "snap_m": snap,
                "inside": inside, "land_cells": land,
                "on_trunk_landmass": on_land}

    def reach(self, xs, zs):
        return self.at(xs, zs)["reached"]


def traversable_reach(fld, forbid=None, *, root=None):
    """`(ok(xs, zs) -> bool array, stats)`: which samples of a window stand
    on an allowed cell of the LARGEST component of the trunk corridor's own
    graph (EXTREMITY_REACH_DEPENDS_ON).  `forbid` overrides the generator
    keep-out mask (a fixture has no dump)."""
    graph = RoadGraph(fld, forbid, "trunk", root=root)
    return graph.reach, {"largest_cells": graph.largest_cells,
                         "cell_m": graph.corr.cell_m}


def _sector_samples(fld, inland_m: float, *, root=None):
    """`survey_patch`'s own arrays for the window's largest land component
    -- xs, zs, inland distance, radius and bearing from its centroid --
    recomputed so a sector can be asked for its farthest QUALIFYING
    sample.  Checked against `survey_patch` by `extremity_nodes`."""
    import numpy as np
    from scipy import ndimage
    rsurvey = roads_survey()
    land = fld.h > fieldmod.WATER_LEVEL + rsurvey.FREEBOARD_M
    lab, n = ndimage.label(land, structure=np.ones((3, 3)))
    sizes = ndimage.sum(land, lab, range(1, n + 1))
    component = (int(np.argmax(sizes)) + 1 if root is None else
                 rsurvey.component_at_root(fld, lab, root))
    m = lab == component
    ii, jj = np.nonzero(m)
    xs, zs = fld.x0 + jj, fld.z0 + ii
    di = ndimage.distance_transform_edt(m)[ii, jj]
    cx, cz = float(xs.mean()), float(zs.mean())
    ang = np.degrees(np.arctan2(xs - cx, zs - cz)) % 360.0
    rad = np.hypot(xs - cx, zs - cz)
    return {"xs": xs, "zs": zs, "di": di, "rad": rad, "ang": ang,
            "ii": ii, "jj": jj}


def extremity_nodes(fld, plan: dict[int, str], *,
                    inland_m: float | None = None,
                    seed_varying: set | None = None,
                    forbid=None, root=None, island=None) -> dict[str, dict]:
    """`survey_patch`'s extremities for one window's LARGEST land component,
    keyed by this network's node ids -- each moved, when it must be, to the
    farthest sample of its sector a road can reach
    (EXTREMITY_REACH_DEPENDS_ON).

    With a measured root, the component containing that root is authoritative;
    an unrelated larger island inside an expanded survey cannot replace it.
    """
    import numpy as np
    rsurvey = roads_survey()  # scipy; loaded only when a derivation runs

    if inland_m is None:
        inland_m = rsurvey.INLAND_M
    patch = rsurvey.survey_patch(fld, {}, inland_m=inland_m, root=root)
    comp = patch["components"][0]
    if root is None and not comp["is_largest"]:
        raise SystemExit(
            f"REFUSING: window {fld.id!r} ranks its components by area and the "
            f"first is not the largest in the patch; the derivation would site "
            f"a trunk on the wrong landmass.")
    by_sector = {int(e["sector_deg"]): e for e in comp["extremities"]}
    island = island if island is not None else island_of_window(fld.id)
    out: dict[str, dict] = {}
    # The caller's sink for "which fields this derivation MEASURED". Optional so
    # the function stays callable on its own; when `derive` passes one, the
    # emitted declaration is built from what was actually constructed.
    seed_varying = set() if seed_varying is None else seed_varying
    reach = samples = None
    if _EXTREMITY_REACH_MODE == "traversable":
        reach, reach_stats = traversable_reach(fld, forbid, root=root)
    for sector, nid in sorted(plan.items()):
        e = by_sector.get(sector)
        if e is None:
            raise SystemExit(
                f"REFUSING: node {nid!r} is the {sector} deg extremity of the "
                f"{island!r} landmass, and this seed's field has NO sample on "
                f"that bearing at least {inland_m} m from open water. Every "
                f"edge naming {nid!r} would dangle. Either that landmass is a "
                f"different shape on this seed and the authored EXTREMITY_PLAN "
                f"has to say so, or the patch window is wrong.")
        moved = None
        if reach is not None and not bool(reach([e["xz"][0]], [e["xz"][1]])[0]):
            if samples is None:
                samples = _sector_samples(fld, inland_m, root=root)
            s = samples
            sector_sel = ((np.abs((s["ang"] - sector + 180.0) % 360.0 - 180.0)
                           < 22.5) & (s["di"] >= inland_m))
            k0 = int(np.nonzero(sector_sel)[0][int(np.argmax(s["rad"][sector_sel]))])
            if (float(s["xs"][k0]), float(s["zs"][k0])) != tuple(e["xz"]):
                raise SystemExit(
                    f"REFUSING: the re-selection for {nid!r} does not "
                    f"reproduce survey_patch's own sector {sector} answer "
                    f"({[float(s['xs'][k0]), float(s['zs'][k0])]} vs "
                    f"{e['xz']}); moving a node off arrays that disagree with "
                    f"the producer would be a second extremity definition.")
            sel = sector_sel & reach(s["xs"], s["zs"])
            if not sel.any():
                raise SystemExit(
                    f"REFUSING: node {nid!r} (sector {sector} of {island!r}) "
                    f"has no sample at least {inland_m} m inland that stands "
                    f"on the largest traversable component "
                    f"({reach_stats['largest_cells']} cells of "
                    f"{reach_stats['cell_m']} m); every edge naming it would "
                    f"be unroutable.")
            k = int(np.nonzero(sel)[0][int(np.argmax(s["rad"][sel]))])
            moved = {"from_xz": [snap(e["xz"][0]), snap(e["xz"][1])],
                     "why": "survey_patch's sample stands off the largest "
                            "traversable component of the trunk corridor "
                            f"({reach_stats['largest_cells']} cells); moved to "
                            "the farthest sample of the sector on it "
                            "(EXTREMITY_REACH_DEPENDS_ON)"}
            e = {"sector_deg": sector,
                 "xz": [float(s["xs"][k]), float(s["zs"][k])],
                 "generated_y": round(float(fld.h[s["ii"][k], s["jj"][k]]), 2),
                 "biome": fieldmod.BIOME_NAMES.get(
                     int(fld.biome[s["ii"][k], s["jj"][k]]), "Unknown"),
                 "radius_m": round(float(s["rad"][k]), 1),
                 "inland_m": round(float(s["di"][k]), 1)}
        # MEASURED vs AUTHORED, SPLIT AT THE POINT OF CONSTRUCTION.
        # `seed_varying_fields` in the emitted document is built from
        # `measured.keys()` -- the dict that was actually assembled here -- and
        # is NOT a list restated elsewhere. Add a measured field to this literal
        # and the declaration gains it; a transcribed list would not, and that
        # was this module's own named residual.
        measured = {
            "xz": [snap(e["xz"][0]), snap(e["xz"][1])],
            "raw_xz": [float(e["xz"][0]), float(e["xz"][1])],
            "generated_y": e["generated_y"], "biome": e["biome"],
            "radius_m": e["radius_m"], "inland_m": e["inland_m"],
        }
        if moved is not None:
            measured["moved_for_reach"] = moved
        seed_varying.update(measured)
        out[nid] = {
            "id": nid, "island": island, "patch": fld.id,
            "kind": "extremity", "sector_deg": sector,
            "provenance": f"MEASURED, survey.py sector {sector}"
                          + ("" if moved is None else
                             ", moved onto the router's largest traversable "
                             "component"),
            **measured,
        }
    return out

# ---------------------------------------------------------------------------
# DERIVATION -- generator locations, from `roads/poi.py`
# ---------------------------------------------------------------------------

def island_of_point(x: float, z: float, windows: dict, *, roots=None) -> tuple[str, str]:
    """(island role, patch id) for a point, by LAND COMPONENT, not by bbox.

    The trunk windows OVERLAP -- MEASURED, the spawn point (-64.68, 3.31) is
    inside both -- so "which window contains it" has two answers and is not a
    rule.  "Which window's LARGEST land component it stands on" has one: at the
    spawn point that is component 7 of 2,775,296 cells in trunk-primary and
    component 1 of 1,129,732 in trunk-secondary, where the largest is component
    6.  The island a node belongs to is the landmass a road can reach it over,
    and that is a component question.
    """
    import numpy as np

    cs = crossings_survey()
    hits = []
    for pid, fld in windows.items():
        if not fld.contains(x, z, 1.0):
            continue
        lab, sizes = cs.label_land(fld)
        i, j = fld.index(x, z)
        cid = int(lab[i, j])
        target = (int(np.argmax(sizes[1:])) + 1 if roots is None else
                  roads_survey().component_at_root(fld, lab, roots[pid]))
        if cid and cid == target:
            hits.append((pid, int(sizes[cid])))
    if not hits:
        raise SystemExit(
            f"REFUSING: ({x}, {z}) does not stand on the largest land component "
            f"of any trunk window, so no trunk road can reach it on this seed.")
    hits.sort(key=lambda t: -t[1])
    pid = hits[0][0]
    return island_of_window(pid), pid


def location_nodes(windows: dict, plan: tuple[dict, ...] = LOCATION_PLAN,
                   seed_varying: set | None = None, *, roots=None) -> dict[str, dict]:
    """Nodes that ARE a generator location, read out of this seed's own dump."""
    pois = _by_path("roads_poi", _HERE / "poi.py").load()
    by_prefab: dict[str, list] = {}
    for p in pois:
        by_prefab.setdefault(p.name, []).append(p)
    out: dict[str, dict] = {}
    seed_varying = set() if seed_varying is None else seed_varying
    for c in plan:
        found = by_prefab.get(c["prefab"], [])
        if len(found) != 1:
            raise SystemExit(
                f"REFUSING: node {c['id']!r} is the generator location "
                f"{c['prefab']!r} and this seed's dump holds {len(found)} of "
                f"them, not one. A road terminus that names a location has to "
                f"name ONE; {len(found)} instances is a different question "
                f"(which one?) and the network has to answer it, not this "
                f"derivation.")
        p = found[0]
        island, pid = island_of_point(p.x, p.z, windows, roots=roots)
        # `protected` and `clearance_radius_m` are read from this seed's dump
        # but are NOT seed-varying: they are properties of the PREFAB, and
        # StartTemple carries the same exterior radius on every planet. Putting
        # them outside `measured` is the call that keeps a byte-identity check
        # from flagging a legitimate constant -- the low-cardinality trap.
        measured = {"xz": [float(p.x), float(p.z)],
                    "generated_y": float(p.y)}
        seed_varying.update(measured)
        out[c["id"]] = {
            "id": c["id"], "island": island, "patch": pid,
            "kind": c["kind"],
            "location_prefab": p.name,
            "protected": bool(p.protected),
            "clearance_radius_m": float(p.declared_m),
            "provenance": "MEASURED, the generator location dump this seed's "
                          "keep-out set is read from (roads/poi.py::load)",
            "why": c["why"],
            **measured,
        }
    return out


# ---------------------------------------------------------------------------
# DERIVATION -- the 4 abutments, from `crossings/survey.py`
# ---------------------------------------------------------------------------

def crossings_survey():
    """`crossings/survey.py`, loaded by path.  See `_by_path`."""
    return _by_path("crossings_survey", _HERE.parent / "crossings" / "survey.py")


def walk_to_bank(fld, xz: tuple[float, float],
                 away_from: tuple[float, float]) -> dict:
    """The first sample OUTWARD from the channel that is not under water.

    A snapped bank can land inside the water: the snap moves the point by up to
    half a pitch and a shoreline is precisely where the ground is nearest the
    plane.  MEASURED on Pirate68 -- S1's far bank snaps to (-324, -64), which
    reads 29.48, i.e. 0.52 m UNDER the water plane; one metre further out
    (-325, -64) reads 30.24 and is bank.  S2's far bank snaps to (-348, 246) at
    29.97 and walks to (-349, 246) at 30.53.  Both walked values are exactly the
    frozen `brg-s1-w` and `brg-s2-w`, and both NEAR banks walk zero metres.

    The walk is on the 1 m lattice along the crossing's DOMINANT AXIS, so the
    result stays on a lattice a ribbon can start on and each step is a whole
    sample rather than a diagonal fraction of one.
    """
    dx, dz = xz[0] - away_from[0], xz[1] - away_from[1]
    if abs(dx) >= abs(dz):
        step = (math.copysign(fld.step, dx), 0.0)
    else:
        step = (0.0, math.copysign(fld.step, dz))
    k = 0
    while k * fld.step <= BANK_WALK_MAX_M:
        px, pz = xz[0] + step[0] * k, xz[1] + step[1] * k
        try:
            y, sample = sample_at(fld, px, pz)
        except ValueError:
            break
        if y > ABUTMENT_MIN_Y:
            return {"xz": [px, pz], "ground_y": round(y, 2),
                    "walked_m": float(k) * fld.step, "sample_xz": list(sample)}
        k += 1
    raise SystemExit(
        f"REFUSING: no sample above the water plane within {BANK_WALK_MAX_M:g} m "
        f"outward of the snapped bank {xz} in window {fld.id!r}. An abutment "
        f"there is a pile in the sea, not a bank.")


def _bbox(i, j, fld, np) -> tuple[float, float, float, float]:
    x = fld.x0 + j * fld.step
    z = fld.z0 + i * fld.step
    return float(x.min()), float(x.max()), float(z.min()), float(z.max())


def _bbox_gap_m(a, b) -> float:
    """Separation of two axis-aligned boxes.

    NOT a bound on the landmasses' true separation, and it used to be described
    as one.  The boxes are of the SCANNED portions, and every window here may be
    CLAMPED at its half-extent -- MEASURED by SeedField on Fresh42,
    trunk-primary covers 60 % of its own landmass's bbox -- so a clipped box can
    sit further from its neighbour than the whole landmass does.  It is reported
    as what it is, a box separation within the scan, and never as the quantity a
    refusal is stated in.
    """
    dx = max(0.0, a[0] - b[1], b[0] - a[1])
    dz = max(0.0, a[2] - b[3], b[2] - a[3])
    return math.hypot(dx, dz)


# Stride, in samples, for the closest-approach measurement.  The answer is
# quantised to this, so it is REPORTED with it rather than presented as exact.
CLOSEST_APPROACH_STRIDE = 8

# How much clear water a CLAMPED scan must show before "portal only" may be
# concluded from it.  A window clipped at its half-extent can hide the very
# stretch of coast where the two landmasses come closest, so a narrow measured
# gap out of a clipped window is not evidence of anything; a gap this many times
# the span ceiling is, because no re-scan recovers two orders of magnitude.
# MEASURED on Fresh42: 4,926.7 m against 96 m is 51x, so the verdict holds
# there; SeedField's whole-landmass figure over the coarse grid is 4,346.6 m,
# 45x, and the two disagree by 580 m precisely BECAUSE both windows are clipped.
CLAMP_SAFETY_FACTOR = 10.0


def _touches_border(mask, np) -> bool:
    """Is this component cut off by the edge of its own window?"""
    return bool(mask[0].any() or mask[-1].any()
                or mask[:, 0].any() or mask[:, -1].any())


def _closest_approach_m(near_fld, near_mask, far_fld, far_mask, np):
    """The actual water gap between two landmasses: closest approach between
    their land samples, decimated to `CLOSEST_APPROACH_STRIDE`.

    WHY THIS EXISTS RATHER THAN A WINDOW SEPARATION.  MEASURED by SeedField on
    Fresh42: the two trunk WINDOW CENTRES are 7,358 m apart, and the two
    LANDMASSES are 4,346.6 m apart.  The first is a fact about half-extents --
    it would change if somebody re-centred a scan -- and the second is the
    quantity that decides whether a bridge can exist.  A refusal stated in the
    wrong quantity is narrative; stated in this one it is arithmetic a reader
    can check against the 96 m span ceiling, and it survives somebody proposing
    a longer bridge.
    """
    from scipy.spatial import cKDTree                        # noqa: PLC0415

    s = CLOSEST_APPROACH_STRIDE
    ai, aj = np.nonzero(near_mask[::s, ::s])
    bi, bj = np.nonzero(far_mask[::s, ::s])
    if not len(ai) or not len(bi):
        return None
    a = np.column_stack((near_fld.x0 + aj * s * near_fld.step,
                         near_fld.z0 + ai * s * near_fld.step))
    b = np.column_stack((far_fld.x0 + bj * s * far_fld.step,
                         far_fld.z0 + bi * s * far_fld.step))
    d, _ = cKDTree(b).query(a, k=1)
    return float(d.min())


def trunk_landmasses(near_fld, far_fld, near_lab, near_sizes, np, *, roots=None) -> dict:
    """Are the two trunk landmasses ONE BRIDGE APART, and which components are
    they in the primary window?

    WHY THIS IS A QUESTION AND NOT AN ASSERTION.  `CROSSING_PLAN` calls S1 "the
    join between the two halves of the main continent", and that is a TOPOLOGY
    claim about Ulfsland, not a fact about every planet.  MEASURED by
    FreshSeedDrive on seed Fresh42: trunk-primary is centred (-6.1, 3.2) and
    trunk-secondary (7282.9, -1001.8) -- 7,358 m apart with 1600 m half-extents,
    so the two windows DO NOT EVEN TOUCH.  There is no channel there to measure,
    and the first version of this function reported that as "no crossing can be
    measured from one field", which reads as a scanning limit when the true
    statement is a fact about the world: THESE ARE TWO SEPARATE ISLANDS.

    A separate island is not a defect and this project already has its answer --
    `spec.NO_ROAD`'s ferry-terminal-eastisle, "a SEPARATE ISLAND from the spawn
    island's component 7, so it is portal-only".  So this returns a REASON, the
    caller emits no abutments and records why, and the refusal that is kept is
    the one that means something.
    """
    a_id = (int(np.argmax(near_sizes[1:])) + 1 if roots is None else
            roads_survey().component_at_root(near_fld, near_lab, roots[0]))
    ai, aj = np.nonzero(near_lab == a_id)
    a_box = _bbox(ai, aj, near_fld, np)

    cs = crossings_survey()
    flab, fsizes = cs.label_land(far_fld)
    fmain = (int(np.argmax(fsizes[1:])) + 1 if roots is None else
             roads_survey().component_at_root(far_fld, flab, roots[1]))
    fi, fj = np.nonzero(flab == fmain)
    b_box = _bbox(fi, fj, far_fld, np)

    fx = far_fld.x0 + fj * far_fld.step
    fz = far_fld.z0 + fi * far_fld.step
    j = np.rint((fx - near_fld.x0) / near_fld.step).astype(np.int64)
    i = np.rint((fz - near_fld.z0) / near_fld.step).astype(np.int64)
    inside = (i >= 0) & (i < near_fld.n) & (j >= 0) & (j < near_fld.n)

    common = {
        "near_window": near_fld.id, "far_window": far_fld.id,
        "near_window_centre": [round(near_fld.cx, 1), round(near_fld.cz, 1)],
        "far_window_centre": [round(far_fld.cx, 1), round(far_fld.cz, 1)],
        "window_centre_separation_m":
            round(math.hypot(far_fld.cx - near_fld.cx, far_fld.cz - near_fld.cz), 1),
        "bridge_max_span_m": BRIDGE_MAX_SPAN_M,
    }

    if not inside.any():
        lower = _bbox_gap_m(a_box, b_box)
        gap = _closest_approach_m(near_fld, near_lab == a_id,
                                  far_fld, flab == fmain, np)
        s = CLOSEST_APPROACH_STRIDE
        clipped = (_touches_border(near_lab == a_id, np)
                   or _touches_border(flab == fmain, np))
        # A CLIPPED SCAN MAY NOT CONCLUDE "PORTAL ONLY" ON A NARROW GAP.  Both
        # windows are clamped at their half-extent, so the coast where these two
        # landmasses come closest can lie outside what was scanned -- which is
        # exactly why this module's 4,926.7 m and SeedField's whole-landmass
        # 4,346.6 m disagree on Fresh42.  Far out, the disagreement cannot
        # matter; near the ceiling it decides the answer, and a scan that cannot
        # see the crossing must say so instead of denying it exists.
        if clipped and gap is not None and gap < BRIDGE_MAX_SPAN_M * CLAMP_SAFETY_FACTOR:
            raise SystemExit(
                f"REFUSING: the two trunk landmasses are {gap:,.1f} m apart "
                f"WITHIN THE SCANNED WINDOWS, which is under "
                f"{CLAMP_SAFETY_FACTOR:g}x Crossings' {BRIDGE_MAX_SPAN_M:g} m "
                f"span ceiling -- and at least one of them is cut off by its "
                f"own window edge, so the place they actually come closest may "
                f"not have been scanned at all. This derivation will not "
                f"declare a crossing impossible from a view that cannot see it. "
                f"Re-scan with a larger --half-max-m (roads/field.py request) "
                f"and emit again.")
        shown = f"{gap:,.0f}" if gap is not None else f"at least {lower:,.0f}"
        return {"bridgeable": False, "a": a_id,
                "reason": (f"PORTAL ONLY: the two trunk landmasses are separate "
                           f"islands with {shown} m of open water between them "
                           f"-- closest approach of their land samples at a "
                           f"{s} m stride -- against Crossings' MEASURED "
                           f"{BRIDGE_MAX_SPAN_M:g} m absolute span ceiling. The "
                           f"landmass in window {far_fld.id!r} does not reach "
                           f"into {near_fld.id!r} at all. No bridge this "
                           f"project can build joins them on this seed; the "
                           f"operator reaches the second island by portal, "
                           f"exactly as ferry-terminal-eastisle is reached on "
                           f"Ulfsland."),
                # THE QUANTITY THE REFUSAL IS STATED IN IS THE WATER GAP, not
                # the window separation. MEASURED by SeedField on Fresh42: the
                # window CENTRES are 7,358 m apart and the LANDMASSES 4,346.6 m.
                # The first would change if somebody re-centred a scan; only the
                # second decides whether a bridge can exist, and only the second
                # survives someone proposing a longer bridge.
                "measured": dict(common,
                                 landmass_gap_m=(None if gap is None
                                                 else round(gap, 1)),
                                 landmass_gap_stride_m=s,
                                 landmass_bbox_gap_within_windows_m=round(lower, 1),
                                 scan_clipped_at_window_edge=clipped,
                                 windows_overlap=False)}

    ids = near_lab[i[inside], j[inside]]
    ids = ids[ids > 0]
    if not len(ids):
        return {"bridgeable": False, "a": a_id,
                "reason": (f"PORTAL ONLY: the overlap between {near_fld.id!r} "
                           f"and {far_fld.id!r} holds no labelled land in the "
                           f"primary window, so the secondary landmass is not "
                           f"visible from the window a road would leave."),
                "measured": dict(common, windows_overlap=True,
                                 overlapping_land_samples=0)}

    b_id = int(np.argmax(np.bincount(ids)))
    if b_id == a_id:
        return {"bridgeable": False, "a": a_id,
                "reason": (f"NO CROSSING NEEDED: both trunk windows resolve to "
                           f"the SAME land component ({a_id}). On this seed the "
                           f"'two halves of the main continent' are one "
                           f"landmass and a road reaches the far window over "
                           f"dry ground."),
                "measured": dict(common, windows_overlap=True,
                                 same_component=a_id)}
    return {"bridgeable": True, "a": a_id, "b": b_id, "measured": common}


# CROSSING_REACH_DEPENDS_ON: A BANK IS WHERE A ROAD CAN GET TO, NOT WHERE THE
# WATER IS NARROWEST.
#
# `crossing_nodes` ranked candidate bridge sites by gap alone ("narrowest",
# "cheapest_from_anchor").  The same defect EXTREMITY_REACH_DEPENDS_ON closed
# for the extremities, one derivation over: a bank sample can stand on land
# the router's graph cannot enter.  MEASURED on Pirate68 (Pirate68_1m.bin,
# trunk corridor, generator keep-outs): of 38,413 candidate approach pairs
# 30,711 have BOTH banks in the largest strong component of their window's
# graph; the narrowest pair (13.0 m, near (-311.5, -64.5)) has a near bank
# that is reachable and a far bank that is NOT.  S1 was sited there; the
# regeneration then spent 4 days routing roads to an abutment no road could
# reach (T2 fell back to a land route ending 118.7 m short of it, the deck
# was never built, and 7 sites came back unwalkable).  The reachable
# narrowest pair is 15.0 m at (-310.5, -73.5).
# THE RULE: a candidate is costed only if its near bank is in the near
# window's reachable component AND its far bank is in the far window's; when
# no candidate is, the crossing is ABSENT with the counts as its reason.
# NEUTER `_CROSSING_REACH_MODE = "land"`: the shipped rule, gap/anchor only.
# Proven by `selftest_crossing_reach`.
CROSSING_REACH_DEPENDS_ON = ("the_routers_own_graph", "the_generator_keep_outs")
_CROSSING_REACH_MODE = "traversable"    # traversable | land (the shipped)


def reachable_cost(cost, near_ok, far_ok):
    """`cost` with every candidate whose near or far bank the router cannot
    reach set to +inf (CROSSING_REACH_DEPENDS_ON).  `None` masks pass."""
    import numpy as np
    if _CROSSING_REACH_MODE == "land" or near_ok is None or far_ok is None:
        return cost
    return np.where(np.asarray(near_ok) & np.asarray(far_ok), cost, np.inf)


# Candidate eligibility reads the emitted deck and the legal approach raster,
# not the former two inset cross-sections (<=1 m spread did not prove a landing).
CROSSING_LANDING_DEPENDS_ON = (
    "content-bound rendered integer Heightmap",
    "canonical road profile and rasterised legal bank bench",
    "boardwalk.prepare_geometry emitted pieces and every physical guard")


def crossing_nodes(near_fld, far_fld, anchors: dict[str, tuple[float, float]],
                   plan: tuple[dict, ...] = CROSSING_PLAN,
                   seed_varying: set | None = None,
                   reach_near=None, reach_far=None, *, physical=None, roots=None,
                   workers=2
                   ) -> tuple[dict[str, dict], list[dict]]:
    """The abutment pairs for each authored crossing, and the crossings this
    seed does NOT have with the reason it does not have them.

    `near_fld` is the window the road APPROACHES FROM (trunk-primary, which
    holds the anchor); `far_fld` is the window the road continues in.  Both are
    `crossings/water.Field`.

    AN ABSENT ABUTMENT IS NEVER SILENT.  Every crossing that cannot be sited
    comes back as a record naming both node ids and the measurement that ruled
    it out, and `spec.py` refuses an edge whose missing end has no such record.
    A flag that skipped crossings quietly would create exactly the dangling
    terminus this module already refuses for an empty sector.
    """
    import numpy as np
    seed_varying = set() if seed_varying is None else seed_varying

    cs = crossings_survey()
    lab, sizes = cs.label_land(near_fld)
    if len(sizes) < 3:
        return {}, [dict(c, node_ids=[c["near"], c["far"]],
                         reason=(f"PORTAL ONLY: window {near_fld.id!r} holds "
                                 f"fewer than two land components, so there is "
                                 f"no channel in it to cross."),
                         measured={"near_window": near_fld.id,
                                   "land_components": max(0, len(sizes) - 1)})
                    for c in plan]

    verdict = trunk_landmasses(near_fld, far_fld, lab, sizes, np, roots=roots)
    if not verdict["bridgeable"]:
        return {}, [dict(c, node_ids=[c["near"], c["far"]],
                         reason=verdict["reason"],
                         measured=dict(verdict["measured"])) for c in plan]
    a_id, b_id = verdict["a"], verdict["b"]

    gaps, ni, nj, fi, fj = cs.approaches(near_fld, lab == a_id, lab == b_id,
                                         BRIDGE_MAX_SPAN_M)
    if not len(gaps):
        return {}, [dict(c, node_ids=[c["near"], c["far"]],
                         reason=(f"PORTAL ONLY: the two trunk landmasses in "
                                 f"{near_fld.id!r} are nowhere closer than "
                                 f"Crossings' MEASURED {BRIDGE_MAX_SPAN_M:g} m "
                                 f"absolute span ceiling, so no bridge this "
                                 f"project can build joins them."),
                         measured=dict(verdict["measured"])) for c in plan]
    near_x = near_fld.x0 + nj * near_fld.step
    near_z = near_fld.z0 + ni * near_fld.step
    far_x = near_fld.x0 + fj * near_fld.step
    far_z = near_fld.z0 + fi * near_fld.step

    near_ok = reach_near(near_x, near_z) if reach_near else None
    far_ok = reach_far(far_x, far_z) if reach_far else None
    # Reach and gap rank candidates; only the actual writer and emitted-piece
    # builder below authorise one. No gap-only top-2000 crop hides candidates.
    n_reach = (int((near_ok & far_ok).sum())
               if near_ok is not None and far_ok is not None else len(gaps))
    if n_reach == 0 and _CROSSING_REACH_MODE != "land":
        return {}, [dict(c, node_ids=[c["near"], c["far"]],
                         reason=(f"TRANSPORT UNMET: none of {len(gaps)} candidate "
                                 f"bridge sites between the two trunk "
                                 f"landmasses has both banks inside the "
                                 f"router's reachable component "
                                 f"({int(near_ok.sum())} near / "
                                 f"{int(far_ok.sum())} far banks reachable "
                                 f"alone), so no road can arrive at a bridge "
                                 f"here (CROSSING_REACH_DEPENDS_ON). The west "
                                 f"landmass remains a transport obligation."),
                         measured=dict(verdict["measured"],
                                       candidates=len(gaps),
                                       reachable_pairs=0),
                         unmet_need={"kind": "transport", "required": True,
                                     "crossing": c["id"], "status": "unserved"})
                    for c in plan]

    out: dict[str, dict] = {}
    chosen: list[tuple[str, tuple, tuple]] = []
    absent = []
    for c in plan:
        if c["rule"] == "narrowest":
            cost = gaps
        elif c["rule"] == "cheapest_from_anchor":
            if c["anchor"] not in anchors:
                raise SystemExit(
                    f"REFUSING: crossing {c['id']} is costed from {c['anchor']!r} "
                    f"and no such anchor was supplied. The anchor is AUTHORED "
                    f"design (where the portal hall stands); this module will "
                    f"not invent one.")
            ax, az = anchors[c["anchor"]]
            cost = gaps + np.hypot(near_x - ax, near_z - az)
        else:
            raise SystemExit(f"unknown crossing rule {c['rule']!r}")
        cost = reachable_cost(cost, near_ok, far_ok)
        ordered = np.argsort(cost, kind="stable")
        rejected = []
        unverified = []
        selected = None
        seen_banks = set()
        from collections import deque
        from contextlib import closing
        from time import monotonic
        from crossing_parallel import ordered_candidates
        from crossing_physical import CoverageError
        pending_refusals = deque()

        def requests():
            for rank, index in enumerate(ordered, 1):
                k = int(index)
                if not np.isfinite(cost[k]):
                    break
                nxz = (float(near_x[k]), float(near_z[k]))
                fxz = (float(far_x[k]), float(far_z[k]))
                try:
                    near_bank = walk_to_bank(near_fld, (snap(nxz[0]), snap(nxz[1])), fxz)
                    far_bank = walk_to_bank(far_fld, (snap(fxz[0]), snap(fxz[1])), nxz)
                except SystemExit as error:
                    pending_refusals.append((rank, {"stage":"bank_walk","reason":str(error)}))
                    continue
                pair = (*near_bank["xz"], *far_bank["xz"])
                if pair in seen_banks:
                    continue
                seen_banks.add(pair)
                reached = [
                    fn(np.array([bank["xz"][0]]), np.array([bank["xz"][1]]))[0]
                    for fn, bank in ((reach_near, near_bank), (reach_far, far_bank))
                    if fn is not None]
                reason = None
                if reached and not all(reached):
                    reason = "walked_bank_reach"
                elif any(min(math.dist(near_bank["xz"], pa),
                             math.dist(far_bank["xz"], pb))
                         < CROSSING_MIN_SEPARATION_M for _prior, pa, pb in chosen):
                    reason = "independent_crossing_separation"
                elif math.dist(near_bank["xz"], far_bank["xz"]) > BRIDGE_MAX_SPAN_M:
                    reason = "walked_span_ceiling"
                if reason is not None:
                    pending_refusals.append((rank, {"stage":reason,"banks":list(pair)}))
                    continue
                if physical is None:
                    raise SystemExit("crossing_nodes requires the current rendered "
                        "Heightmap physical context, not a half-grid water.Field")
                print(f"crossing {c['id']}: ranked pair {rank}/{len(ordered)}, "
                      f"walked banks {pair}", file=sys.stderr, flush=True)
                yield dict(rank=rank, k=k, nxz=nxz, fxz=fxz, pair=pair,
                    near_bank=near_bank, far_bank=far_bank, datum=DECK_DATUM_Y,
                    started=monotonic())

        try:
            with closing(ordered_candidates(physical, c, requests(), workers)) as results:
                for request, proof in results:
                    while pending_refusals and pending_refusals[0][0] <= request["rank"]:
                        rejected.append(pending_refusals.popleft()[1])
                    print(f"crossing {c['id']}: {proof['status']} in "
                          f"{monotonic()-request['started']:.2f}s",
                          file=sys.stderr, flush=True)
                    status = proof.get("status")
                    if status not in ("verified","refused","unverified_coverage") or (
                            bool(proof["ok"]) != (status == "verified")):
                        raise SystemExit("physical crossing query returned no consistent "
                                         "verified/refused/unverified_coverage status")
                    if not proof["ok"]:
                        target = unverified if status == "unverified_coverage" else rejected
                        target.append({"banks":list(request["pair"]), **proof})
                        continue
                    selected = (request["k"], request["nxz"], request["fxz"],
                                request["near_bank"], request["far_bank"], proof)
                    break
        except CoverageError as error:
            raise SystemExit(f"REFUSING crossing field coverage: {error}. "
                "Re-run roads/field.py request with complete trunk "
                "approach/batter/zone coverage; unread ground cannot "
                "be counted as a physically rejected candidate.") from error
        if selected is None:
            rejected.extend(refusal for _rank, refusal in pending_refusals)
        if selected is None:
            query_status = "unverified_coverage" if unverified else "refused"
            absent.append(dict(c, node_ids=[c["near"], c["far"]],status=query_status,
                reason=f"TRANSPORT UNMET: no covered independent {c['id']} "
                       f"construction was verified among {len(gaps)} measured "
                       f"approach pairs ({len(seen_banks)} distinct ranked walked "
                       "pairs tested). Optional handoffs beyond construction "
                       "coverage were not declared physically impossible. The "
                       "west landmass remains an explicit transport obligation; "
                       "no fake bridge or portal substitution is authorised.",
                measured=dict(verdict["measured"], candidates=len(gaps),
                    reachable_pairs=n_reach, distinct_walked_pairs=len(seen_banks),
                    query_status=query_status,
                    physical_refused_pairs=sum(r.get("status") == "refused" for r in rejected),
                    prefilter_refusals=sum("status" not in r for r in rejected),
                    unverified_coverage_pairs=len(unverified),
                    unverified_coverage=unverified,
                    construction_scope="measured Heightmap and canonical span request; "
                                       "optional unmeasured handoffs are unavailable, never authorised",
                    physical_refusals=rejected),
                unmet_need={"kind": "transport", "required": True,
                            "near": c["near"], "far": c["far"],
                            "crossing": c["id"], "status": "unserved"}))
            continue
        k, nxz, fxz, near_bank, far_bank, proof = selected
        chosen.append((c["id"], tuple(near_bank["xz"]), tuple(far_bank["xz"])))
        bx = far_bank["xz"][0] - near_bank["xz"][0]
        bz = far_bank["xz"][1] - near_bank["xz"][1]
        # the lowest bed anywhere under the span: the piles' height.  Read off
        # the two windows' own samples along the bank-to-bank line, 1 m apart.
        _n = max(int(math.hypot(bx, bz)), 1)
        _lo = []
        for _k in range(_n + 1):
            _px = near_bank["xz"][0] + bx * _k / _n
            _pz = near_bank["xz"][1] + bz * _k / _n
            for _f in (near_fld, far_fld):
                try:
                    _lo.append(sample_at(_f, _px, _pz)[0])
                    break
                except ValueError:
                    continue
        measured = {
            "gap_min_y": round(min(_lo), 2) if _lo else None,
            "rule": c["rule"],
            "gap_m": round(float(gaps[k]), 2),
            "cost_m": round(float(cost[k]), 2),
            "span_m": round(math.hypot(bx, bz), 2),
            "bearing_deg": round(math.degrees(math.atan2(bx, bz)) % 360.0, 2),
            "near_bank_y": near_bank["ground_y"],
            "far_bank_y": far_bank["ground_y"],
            "physical": proof,
            "earlier_ranked_refusals": rejected,
            "earlier_unverified_coverage": unverified,
        }
        for side, bank, fld_ in (("near", near_bank, near_fld),
                                 ("far", far_bank, far_fld)):
            nid = c[side]
            per_bank = {
                "xz": [bank["xz"][0], bank["xz"][1]],
                "raw_xz": list(nxz if side == "near" else fxz),
                "ground_y": bank["ground_y"], "walked_m": bank["walked_m"],
                # A COPY per bank, not the shared dict: `yaml.safe_dump` emits
                # an anchor/alias pair for a shared object, and a node record
                # that reads `*id001` on disk is a record nobody can check by
                # eye against the other bank.
                "measured": dict(measured),
            }
            seed_varying.update(per_bank)
            out[nid] = {
                "id": nid, "island": island_of_window(fld_.id), "patch": fld_.id,
                "kind": "abutment", "crossing": c["id"], "side": side,
                "level_y": DECK_DATUM_Y,
                "provenance": f"MEASURED, crossings/survey.py distance transform "
                              f"between the two trunk landmasses, rule "
                              f"{c['rule']}, snapped to {NODE_SNAP_M:g} m",
                "why": c["why"],
                **per_bank,
            }
    return out, absent


# ---------------------------------------------------------------------------
# EMIT / LOAD
# ---------------------------------------------------------------------------

def derive(field_path: str | Path,
           anchors: dict[str, tuple[float, float]], *, crossing_physical=None,
           workers=2) -> dict:
    """Every seed-derived road node, as the `geography.yaml` document."""
    field_path = str(field_path)
    cs = crossings_survey()
    nodes: dict[str, dict] = {}
    # THE SINK EVERY DERIVATION TAGS ITS MEASURED FIELDS INTO. The emitted
    # `seed_varying_fields` is built from THIS -- the keys that were actually
    # constructed -- so adding a measured field to any of the three record
    # builders gains it automatically. It was a hand-written list until now,
    # which survived its own audit only because the audit was run by hand.
    seed_varying: set[str] = set()

    fields = fieldmod.load(field_path)
    if crossing_physical is None:
        from crossing_physical import PhysicalFields, CoverageError
        try:
            crossing_physical = PhysicalFields.from_field(field_path, fields)
        except CoverageError as error:
            raise SystemExit(f"REFUSING crossing prerequisites: {error}") from error
    else:
        from crossing_physical import CoverageError
        try:
            crossing_physical.require_field(field_path)
        except CoverageError as error:
            raise SystemExit(f"REFUSING crossing prerequisites: {error}") from error
    nomination = crossing_physical.provenance.get("trunk_nomination")
    if not isinstance(nomination, dict):
        raise SystemExit(
            "REFUSING geography: rendered trunk nomination is absent; rerun "
            "roads/field.py request. Old role rectangles cannot authorize "
            "trunk identity, even when their terrain bytes are genuine.")
    roots = {}
    for role in ("primary", "secondary"):
        record = nomination.get(role)
        if not record:
            raise SystemExit(
                f"REFUSING geography: no measured {role} trunk nomination; "
                f"{len(nomination.get('candidates', []))} candidates meet "
                f"the {nomination.get('bridge_max_span_m')} m span and "
                f"{nomination.get('min_area_m2')} m2 area criteria; "
                "the required transport component remains unserved")
        field_id = record["field_id"]
        if field_id not in fields:
            raise SystemExit(
                f"REFUSING geography: nominated {role} field {field_id!r} "
                "was not measured")
        roots[field_id] = tuple(record["root_xz"])
    for island, plan in EXTREMITY_PLAN.items():
        nodes.update(extremity_nodes(
            worldseed.resolve(fields, window_of_island(island)), plan,
            seed_varying=seed_varying,
            root=roots.get(_patch_id(fields, window_of_island(island))),
            island=island))

    near_id = _patch_id(fields, worldseed.TRUNK_PRIMARY)
    far_id = _patch_id(fields, worldseed.TRUNK_SECONDARY)
    windows = {near_id: cs.load_field(field_path, near_id),
               far_id: cs.load_field(field_path, far_id)}
    reach_near, _sn = traversable_reach(
        worldseed.resolve(fields, worldseed.TRUNK_PRIMARY), root=roots.get(near_id))
    reach_far, _sf = traversable_reach(
        worldseed.resolve(fields, worldseed.TRUNK_SECONDARY), root=roots.get(far_id))
    crossing_physical.bind_approach_graphs(reach_near.__self__, reach_far.__self__)
    crossings, no_crossing = crossing_nodes(windows[near_id], windows[far_id],
                                            anchors, seed_varying=seed_varying,
                                            reach_near=reach_near,
                                            reach_far=reach_far,
                                            physical=crossing_physical,
                                            workers=workers,
                                            roots=((roots[near_id], roots[far_id])
                                                   if roots else None))
    nodes.update(crossings)
    nodes.update(location_nodes(windows, seed_varying=seed_varying,
                                roots=roots or None))

    return {
        "seed": worldseed.seed(),
        "world": worldseed.world(),
        "world_agnostic": False,
        "world_agnostic_why":
            "terrain is seed-derived, but crossing costs use this world's "
            "current solved portal placement",
        "owner": "RoadNet",
        "derived_by": "roads/geography.py",
        "provenance": {
            "height_field": field_path,
            "crossing_physical_source": crossing_physical.provenance,
            "extremities": ("roads/survey.py::survey_patch -- farthest sample per "
                           "45 deg sector at least INLAND_M from open water, on "
                           + ("the measured root's land component" if roots else
                              "the window's largest land component")),
            "abutments": "crossings/survey.py::approaches -- distance transform "
                         "between the two trunk land components",
            "snap_m": NODE_SNAP_M,
            "water_level_y": fieldmod.WATER_LEVEL,
            "bridge_max_span_m": BRIDGE_MAX_SPAN_M,
        },
        # WHAT THE DERIVATION COULD SEE, beside what it found.  Every window is
        # a BUDGETED view of a landmass and may be CLAMPED at its half-extent:
        # MEASURED by SeedField on Fresh42, trunk-primary covers 60 % of its own
        # landmass's bbox and trunk-secondary 36 %, because scanning all 26
        # landmasses over the area floor at 1 m costs 1,132 MB against a 512 MB
        # budget.  An extremity set derived through a clamped window is "the
        # extremities of what was scanned", and a reader who cannot see the
        # clamp will read it as "the extremities of the world".
        "scanned_windows": [
            {"id": f.id, "centre": [round(f.cx, 1), round(f.cz, 1)],
             "half_m": round(f.half, 1), "step_m": round(f.step, 2),
             "samples": int(f.n)}
            for f in (fields[k] for k in sorted(fields))
        ],
        # WHICH FIELDS ARE SUPPOSED TO VARY WITH THE WORLD, DECLARED.
        #
        # An output scope check asks "is any value here byte-identical to
        # another seed's?", and that question is only meaningful over values
        # that SHOULD differ.  Run over everything it fires on
        # `water_level_y: 30.0` -- ZoneSystem::c_WaterLevel, identical on every
        # planet by physics -- and on `snap_m`, `level_y`, every node id, every
        # `kind` and every `sector_deg`.  Those are constants and vocabulary,
        # not coordinates, and a world does not get its own water level.
        #
        # THE SET IS DECLARED HERE RATHER THAN INFERRED, and that is the whole
        # point: a checker that infers it from this document's SHAPE (nodes vs
        # provenance) is reading a convention nothing enforces, and the first
        # emitter that puts a measured value somewhere else passes silently.
        # Inferring it from TYPE is worse -- every float becomes a candidate and
        # the checker grows an exclusion list one name at a time, which is
        # `CLEARABLE` again: a classification quietly becoming an authorisation.
        "seed_varying_fields": {
            # DERIVED, NOT TRANSCRIBED. `sorted(seed_varying)` is the set the
            # three record builders tagged as they constructed each value. The
            # hand-written list this replaces passed its own audit only because
            # the audit was run by hand -- the transcribed-population defect,
            # sitting in the field proposed as its fix.
            "nodes": sorted(seed_varying),
            "document": ["anchors", "scanned_windows", "no_crossing"],
            "why": "every value listed is derived from THIS seed's field or its "
                   "location dump and must differ between worlds; everything "
                   "else in this document is a physical constant, a design "
                   "constant, or authored vocabulary (node ids, kinds, island "
                   "roles, sector bearings) and is EXPECTED to be identical "
                   "across seeds.",
            "not_covered": "a value DERIVED FROM another seed's ground and then "
                           "transformed -- rounded, offset, snapped, or "
                           "recomputed through an intermediate -- is not "
                           "byte-identical to anything and no check over this "
                           "list will see it. Stated so the limit is on the "
                           "record rather than assumed away.",
        },
        "anchors": {k: [float(v[0]), float(v[1])] for k, v in anchors.items()},
        "nodes": [nodes[k] for k in sorted(nodes)],
        # CROSSINGS THIS SEED DOES NOT HAVE, and why.  Never an empty list by
        # omission: an absent abutment with no reason is a dangling terminus,
        # and `spec.py` refuses an edge whose missing end is not named here.
        "no_crossing": no_crossing,
    }


def _patch_id(fields: dict, window: str) -> str:
    for alias in worldseed.aliases(window):
        if alias in fields:
            return alias
    raise KeyError(f"patch window {window!r} is not in this field; it holds "
                   f"{sorted(fields)}")


# THE FIELDS A SEED-SCOPED GEOGRAPHY DOCUMENT MUST CARRY.
#
# EVERY SELF-EVIDENCING CHECK HAS THIS HOLE, not just the scope one: the half
# works by asking the artefact what it claims, and an artefact that claims
# nothing answers nothing.  `require_scope` already refuses an absent seed for
# that reason; the same argument applies to every other declaration this
# document makes, so it is stated ONCE here rather than remembered on the fifth
# field the way it was forgotten on the first.
#
# A guard whose coverage depends on the artefact having VOLUNTEERED a field is a
# guard the artefact can opt out of.  MEASURED tonight: `no_crossing` was added,
# and a document written ten minutes earlier had no key at all -- which read as
# "no crossing was ruled out" rather than "this emitter never considered
# crossings".  Absent and empty are different claims.
REQUIRED_DECLARATIONS = {
    "nodes": "the derived node set",
    "no_crossing": "the crossings this seed does NOT have, and why; `[]` means "
                   "none were ruled out and is EMITTED, never omitted",
    "provenance": "the artefacts this document's values were measured from",
    "scanned_windows": "what the derivation could see -- every window may be "
                       "CLAMPED, so an extremity set is 'the extremities of "
                       "what was scanned'",
    "seed_varying_fields": "which values are supposed to differ between "
                           "worlds, declared rather than inferred from shape "
                           "or type",
}


def _require_declarations(path, doc: dict) -> None:
    missing = [k for k in REQUIRED_DECLARATIONS if k not in doc]
    if not missing:
        return
    raise SystemExit(
        "REFUSING: " + str(path) + " does not declare "
        + ", ".join(f"`{k}` ({REQUIRED_DECLARATIONS[k]})" for k in missing)
        + ". An artefact that does not say it cannot be shown to have meant it, "
          "and a reader that treats an absent declaration as an empty one is "
          "how a document written before a rule existed passes the rule. "
          "Re-emit: roads/geography.py emit --field <this seed's VHPATCH1>")


def load(path: Path | None = None) -> dict[str, dict]:
    """`geography.yaml` as `spec.NODES` records, SCOPE-CHECKED.

    No fallback and no frozen copy.  A frozen copy is exactly what this module
    exists to delete: the 15 literals it replaces were a faithful transcription
    of a real measurement that then outlived the world it was measured in.
    """
    path = Path(path) if path is not None else geography_path()
    if not path.exists():
        raise SystemExit(
            f"{path} is missing. It is this seed's DERIVED road geography (11 "
            f"extremity nodes, 4 abutment nodes) and there is no frozen copy to "
            f"fall back on. Produce it with:\n"
            f"  roads/geography.py emit --field <VHPATCH1 scanned on this seed>; "
            f"first solve this world's installation placements")
    doc = yaml.safe_load(path.read_text())
    worldseed.require_scope(
        "derived road geography", path, found_seed=doc.get("seed"),
        found_world=doc.get("world"), world_=worldseed.world(),
        produced_by="roads/geography.py emit, from a VHPATCH1 field scanned on "
                    "this seed")
    _require_declarations(path, doc)
    out: dict[str, dict] = {}
    for n in doc["nodes"]:
        rec = {k: v for k, v in n.items() if k != "id"}
        rec["xz"] = (float(n["xz"][0]), float(n["xz"][1]))
        out[n["id"]] = rec
    return out


def absent_nodes(path: Path | None = None) -> dict[str, dict]:
    """Node ids this seed's geography does NOT have, each with the measurement
    that ruled it out.  SCOPE-CHECKED by the same reader, because a reason
    measured on another planet is not a reason.

    `{}` means "this seed has every node the plan names" and is NOT the same
    claim as "no reasons were recorded" -- a document written before
    `no_crossing` existed has no key, and that is refused rather than read as an
    empty list, because an absent abutment with no reason is the dangling
    terminus this module refuses everywhere else.
    """
    path = Path(path) if path is not None else geography_path()
    doc = yaml.safe_load(path.read_text())
    worldseed.require_scope(
        "derived road geography", path, found_seed=doc.get("seed"),
        found_world=doc.get("world"), world_=worldseed.world(),
        produced_by="roads/geography.py emit, from a VHPATCH1 field scanned on "
                    "this seed")
    if "no_crossing" not in doc:
        raise SystemExit(
            f"REFUSING: {path} predates the `no_crossing` record and cannot say "
            f"whether a missing abutment was measured impossible or simply not "
            f"written. Re-emit it: roads/geography.py emit --field <this seed's "
            f"VHPATCH1>")
    out: dict[str, dict] = {}
    for c in doc["no_crossing"] or []:
        for nid in c["node_ids"]:
            out[nid] = {"crossing": c.get("id"), "why": c["reason"],
                        "status": c.get("status"),
                        "measured": {**c.get("measured", {}),
                            **({"unmet_need": c["unmet_need"]}
                               if c.get("unmet_need") else {})}}
    return out


def resolve_anchors(seed: str | None = None,
                    world: str | None = None) -> dict[str, tuple[float, float]]:
    """Cost crossings from this seed/world's actual solved portal placement.

    Previous geography output is never an input to this choice. The placement
    producer runs first; no third human-defined coordinate is required.
    """
    from installation_sites import installation_xz
    return {"portalhub": installation_xz(seed, world)["portalhub"]}


# ---------------------------------------------------------------------------
# EXTREMITY_REACH_DEPENDS_ON both ways, on a synthetic island: a 100 m disc
# at 40 m whose east end carries a 40 m-high flat-topped block walled by
# cliffs -- the shape of Fresh42's w-mid, land and inland and walkable on top,
# with no step onto it inside GRADE_CEILING.  No world, no dump.
# ---------------------------------------------------------------------------
def _xr_field():
    import numpy as np
    half, n = 150.0, 300
    ax = -half + 0.5 + np.arange(n, dtype=np.float64)
    X, Z = np.meshgrid(ax, ax)
    h = np.where(np.hypot(X, Z) <= 100.0, 40.0, 20.0)
    h = np.where((X >= 55.0) & (np.abs(Z) <= 30.0) & (np.hypot(X, Z) <= 100.0),
                 80.0, h)
    return fieldmod.Field("xr-island", 0.0, 0.0, half, 1.0, n,
                          h.astype(np.float32), np.ones((n, n), dtype=np.uint8))


def selftest_crossing_reach() -> int:
    """CROSSING_REACH_DEPENDS_ON both ways, on the Pirate68 numbers: the
    narrowest pair (13 m) has an unreachable far bank, the next (15 m) is
    reachable, and with no reachable pair every cost is infinite."""
    import numpy as np
    global _CROSSING_REACH_MODE
    gaps = np.array([13.0, 15.0, 40.0])
    near = np.array([True, True, True])
    far = np.array([False, True, True])
    bad = 0
    got = int(np.argmin(reachable_cost(gaps, near, far)))
    ok = got == 1
    print(f"  {'ok  ' if ok else 'FAIL'}  an unreachable far bank is not "
          f"the crossing: picked index {got}, wanted 1")
    bad += not ok
    prior, _CROSSING_REACH_MODE = _CROSSING_REACH_MODE, "land"
    try:
        old = int(np.argmin(reachable_cost(gaps, near, far)))
    finally:
        _CROSSING_REACH_MODE = prior
    ok = old == 0
    print(f"  {'ok  ' if ok else 'FAIL'}  NEUTER land: the narrowest pair is "
          f"picked again (index {old})")
    bad += not ok
    none = reachable_cost(gaps, near, np.array([False] * 3))
    ok = bool(np.isinf(none).all())
    print(f"  {'ok  ' if ok else 'FAIL'}  no reachable pair: every cost is "
          f"infinite")
    bad += not ok
    print(f"\n  crossing reach selftest {'PASS' if not bad else 'FAIL'}\n")
    return bad


def selftest_crossing_landing() -> int:
    """Physical failing-before/survivor arms, through the canonical builder."""
    from crossing_physical import (
        constructed_geometry_cases, constructed_routed_approach_cases)
    rows = constructed_geometry_cases() + constructed_routed_approach_cases()
    from crossing_sampling_cases import sampling_cases
    rows += sampling_cases()
    from crossing_bulk_cases import bulk_cases
    rows += bulk_cases()
    from crossing_bank_clearance_cases import bank_clearance_cases
    rows += bank_clearance_cases()
    from crossing_empty_bank_cases import empty_bank_cases
    rows += empty_bank_cases()
    from crossing_reachability_cases import reachability_cases
    rows += reachability_cases()
    from crossing_edge_cost_cases import edge_cost_cases
    rows += edge_cost_cases()
    from crossing_parallel_cases import parallel_cases
    rows += parallel_cases()
    from crossing_search_cases import (
        search_parity_cases, seating_dependency_cases, coastal_handoff_cases,
        coverage_exhaustion_cases)
    rows += (search_parity_cases() + seating_dependency_cases() +
             coastal_handoff_cases() + coverage_exhaustion_cases())
    for row in rows:
        print(row)
    return sum(not row["matches_expected"] for row in rows)


def selftest_extremity_reach() -> int:
    global _EXTREMITY_REACH_MODE
    import numpy as np
    fails = 0

    def want(label, got, expect):
        nonlocal fails
        ok = got == expect
        fails += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got}"
              + ("" if ok else f"  EXPECTED {expect}"))

    fld = _xr_field()

    def verdict() -> str:
        m = int((fld.n // 4))
        node = extremity_nodes(fld, {90: "eastcape"}, seed_varying=set(),
                               forbid=np.zeros((m, m), dtype=bool))["eastcape"]
        x = node["xz"][0]
        where = "ON the walled block" if x >= 55.0 else "on the island"
        return (f"{where} at x {x}"
                + (" (moved)" if "moved_for_reach" in node else ""))

    def moded(mode):
        global _EXTREMITY_REACH_MODE
        was = _EXTREMITY_REACH_MODE
        _EXTREMITY_REACH_MODE = mode
        try:
            return verdict()
        finally:
            _EXTREMITY_REACH_MODE = was

    print("case: the east cape's farthest inland sample tops a walled block "
          "(REQUIRED TO MOVE onto ground a road reaches)")
    got = verdict()
    want("the node is on the island's traversable ground",
         got.startswith("on the island") and got.endswith("(moved)"), True)
    want("NEUTER reach=land (the shipped rule): the node tops the block",
         moded("land").startswith("ON the walled block"), True)
    print(f"  ({got}; neutered: {moded('land')})")
    print(f"extremity reach selftest: {fails} failed")
    return fails


def selftest_rooted_reach() -> int:
    """An expanded measurement cannot replace its real root by a bigger island."""
    import numpy as np
    n = 601
    axis = np.arange(n) - 300
    xx, zz = np.meshgrid(axis, axis)
    heights = np.full((n, n), 20.0, dtype=np.float32)
    heights[(((xx >= -260) & (xx <= -160) & (np.abs(zz) <= 80))
             | ((xx >= -20) & (xx <= 280) & (np.abs(zz) <= 280)))] = 40.0
    fld = fieldmod.Field("constructed-rooted-window", 0.0, 0.0, n / 2,
                         1.0, n, heights,
                         np.ones((n, n), dtype=np.uint8))
    clear = np.zeros((n // 4, n // 4), dtype=bool)
    largest = RoadGraph(fld, forbid=clear)
    rooted = RoadGraph(fld, forbid=clear, root=(-210.0, 0.0))
    outside = RoadGraph(fld, forbid=clear, root=(-1000.0, 0.0))
    blocked = clear.copy()
    blocked[:, :50] = True
    protected = RoadGraph(fld, forbid=blocked, root=(-210.0, 0.0))
    root_point = (-210.0, 0.0)
    rooted_cape = extremity_nodes(
        fld, {90: "root-east"}, forbid=clear, root=root_point, island="spawn")
    largest_cape = extremity_nodes(
        fld, {90: "root-east"}, forbid=clear, island="spawn")
    cs = crossings_survey()
    near = cs.W.Field(worldseed.TRUNK_PRIMARY, fld.cx, fld.cz, fld.half,
                      fld.step, fld.n, fld.h, fld.biome)
    far = cs.W.Field(worldseed.TRUNK_SECONDARY, fld.cx, fld.cz, fld.half,
                     fld.step, fld.n, fld.h, fld.biome)
    labels, sizes = cs.label_land(near)
    trunks = trunk_landmasses(
        near, far, labels, sizes, np, roots=(root_point, (100.0, 0.0)))
    unrooted_trunks = trunk_landmasses(near, far, labels, sizes, np)
    location_role, _ = island_of_point(
        *root_point, {near.id: near, far.id: far},
        roots={near.id: root_point, far.id: (100.0, 0.0)})

    def roaded(graph, x):
        at = graph.at([x], [0.0])
        return bool(at["reached"][0] and at["on_trunk_landmass"][0])

    cases = (
        ("largest unrelated island does not establish the small root",
         not roaded(largest, -210.0)),
        ("the actual root establishes its own smaller landmass",
         roaded(rooted, -210.0)),
        ("the unrelated larger landmass stays unjoined",
         not roaded(rooted, 100.0)),
        ("an outside root cannot authorize the surveyed land",
         not roaded(outside, -210.0)),
        ("a protected root cannot snap across water to another landmass",
         protected.network_id < 0 and not roaded(protected, -210.0)),
        ("the cape remains on its nominated smaller island",
         rooted_cape["root-east"]["xz"][0] < -150.0
         and largest_cape["root-east"]["xz"][0] > 0.0),
        ("overlapping windows cannot collapse distinct rooted trunk identities",
         trunks["bridgeable"] and trunks["a"] != trunks["b"]
         and not unrooted_trunks["bridgeable"]),
        ("a generator location belongs to its rooted component",
         location_role == "spawn"),
    )
    for name, ok in cases:
        print(f"  {'PASS' if ok else 'FAIL'} {name}")
    return sum(not ok for _, ok in cases)


def main(argv=None) -> int:
    if (sys.argv[1:] if argv is None else list(argv))[:1] == ["selftest"]:
        return 1 if (selftest_extremity_reach() | selftest_rooted_reach()
                     | selftest_crossing_reach() | selftest_crossing_landing()) else 0
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["emit", "show"])
    ap.add_argument("--field", default="/tmp/roads/h1m.bin")
    ap.add_argument("--workers", type=int, default=2,
                    help="bounded fork workers for ranked physical crossings; 1 is serial")
    ap.add_argument("--out", default=None,
                    help="where to write; defaults to this SEED's document -- "
                         "geography.yaml for the default seed, "
                         "geography.<seed>.yaml otherwise")
    worldseed.add_arguments(ap)
    args = ap.parse_args(argv)
    seed, world = worldseed.apply(args)
    # `--out` unset means "this SEED's document", resolved the same way every
    # reader resolves it. A single default path is what made the scope refusal
    # uncompliable: a valid Fresh42 geography existed and could not be handed to
    # `spec.py`, because both ends of the contract were hard-coded to one name.
    out = Path(args.out) if args.out else geography_path(seed)

    if args.cmd == "show":
        for nid, n in sorted(load(out).items()):
            print(f"{nid:14s} {n['kind']:10s} {n['island']:6s} "
                  f"{n['xz'][0]:9.1f},{n['xz'][1]:9.1f}  {n['provenance']}")
        return 0

    fieldmod.require_seed(args.field, seed)
    anchors = resolve_anchors(seed, world)

    if args.workers < 1:
        ap.error("--workers must be positive")
    doc = derive(args.field, anchors, workers=args.workers)
    # Guard the actual output header and field citation at the write boundary.
    # Terrain is seed-derived; crossing costs additionally bind this world's
    # current solved portal placement.
    emitscope.require_emission_scope(
        f"geography.yaml ({seed})", out, doc, seed=seed, world=world,
        coordinates=None)
    out.write_text(yaml.safe_dump(doc, sort_keys=False, width=100))
    print(f"-> {out}: {len(doc['nodes'])} derived nodes on seed {seed}")
    for n in doc["nodes"]:
        # A generator location carries no `raw_xz` because it is NOT snapped:
        # its centre is where the engine put it. Printing "raw -" says that,
        # rather than a blank column that reads like a missing measurement.
        raw = n.get("raw_xz")
        shown = (f"raw {raw[0]:9.1f},{raw[1]:9.1f}" if raw
                 else "raw -  (unsnapped: generator location)")
        print(f"   {n['id']:14s} {n['kind']:12s} "
              f"{n['xz'][0]:9.2f},{n['xz'][1]:9.2f}  {shown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
