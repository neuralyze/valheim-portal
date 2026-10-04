#!/usr/bin/env python3
"""Topographic routing for Ulfsland's roads: corridor search, longitudinal
profile fitting, and crossing extraction.

WHAT A ROAD IS HERE, and why that dictates the algorithm.  A road is not a line
of `piece_pavedroad` -- that prefab has no persistent ZNetView, and zero road
pieces exist in any fleet save, so a spawned road piece leaves nothing behind.
What persists is TERRAIN: a `_TerrainCompiler` ZDO per zone carrying a gzip'd
`TCData` blob of per-sample height deltas and per-sample ground paint.  So a
road is a LEVELLED, PAVED RIBBON of terrain, and the routing problem is
"where can I put a slope-limited ribbon", not "where is the shortest path".

THE ONE HARD LIMIT.  `TerrainComp::ApplyToHeightmap` clamps the applied height
to the GENERATED height +/- 8 m (measured, assembly_valheim.dll 1.0.12).  Write
a bigger delta and the game silently discards the excess, so the ribbon ends up
somewhere other than where the plan says.  Every cut and fill in this router is
therefore checked PER SAMPLE against that clamp, and a stretch that cannot be
built inside it is not built at all -- it becomes a crossing handed to
`Crossings`.  That is the whole reason profile fitting is a constrained problem
rather than a smoothing filter.

GRADE POLICY.  Stated explicitly because it is a DESIGN decision, not a
measurement, and the operator is going to walk it:

    trunk       8 %   (4.57 deg)   spine roads between installations
    spur       12 %   (6.84 deg)   short branches to a single site
    mountain   18 %  (10.20 deg)   switchback approaches; walkable, NOT cartable
    ceiling    25 %  (14.04 deg)   never exceeded anywhere, on any class

Rationale.  The game's own limit is far looser: `Character::GetSlideAngle`
returns a literal 38 deg for a player (MEASURED), i.e. a 78 % grade, above
which the player slides.  Designing to 38 deg would produce a road that is
technically stand-on-able and miserable to walk and impossible to cart, because
`Vagon` is a plain physics body (m_baseMass, m_itemWeightMassFactor,
m_breakForce -- no slope logic at all, MEASURED), so a cart's behaviour on a
grade is momentum, not a rule.  Historic cart roads held to <= 8 %; that is the
trunk number.  At 8 % the rise across one 1 m terrain sample is 8 cm, well
inside the terrain mesh's own noise, so the ribbon reads as flat underfoot.
The 25 % ceiling keeps every sample at less than half the slide angle in
degrees, so no stretch of this network can ever dump a player downhill.

TWO-PHASE STRUCTURE, and why not one.  The ribbon's height is a free variable
inside a +/-8 m corridor around the terrain, so the true search space is
(cell, elevation) and a single-phase search over it is both large and
unnecessary: what the +/-8 m corridor CANNOT fix is the large-scale grade, and
that is visible in a smoothed height field.  So phase 1 searches cells against a
smoothed field (what the grade will actually be), and phase 2 fits the exact
profile against the raw 1 m field under both constraints, and REPORTS
infeasibility rather than fudging it.  Phase 2 is exact: interval propagation
gives necessary-and-sufficient feasibility for a slope-limited path inside a
box, so "this stretch needs a bridge" is a proof, not an opinion.
"""

from __future__ import annotations

import heapq
import math
import sys
from dataclasses import dataclass, field as dc_field

import numpy as np
from scipy import ndimage

from field import WATER_LEVEL, BIOME_NAMES, Field


def _seed() -> str:
    """The seed THIS RUN is planning, for provenance fields.

    NOT A LITERAL.  Until 2026-09-21 the `measured` field on every crossing and
    every ford was the fixed string "... seed Pirate68".  MEASURED by driving a
    fresh seed end to end: `/tmp/freshseed/FINAL/segments.yaml`, a 20-segment
    plan for seed Fresh42, carried 22 crossing and ford records each declaring
    they were measured on Pirate68.  The document's own header said Fresh42.

    A FALSE PROVENANCE DECLARATION IS STRICTLY WORSE THAN NONE.  An absent one
    makes a reader go and check; a wrong one answers the question and is
    believed -- and this field is the one a reader consults to ask "which
    world's ground was this bridge span measured over".  It is the same defect
    `settlements/plan.py` carried at :241, where a literal seed was stamped
    into every `sites.yaml` whatever it was solved on, and it makes any
    downstream scope check a comparison against a constant.

    Safe to read here rather than threaded through: `network.py` calls
    `field.require_seed` before it routes anything, so the run's resolved seed
    and the field's own header have already been proven equal by the time a
    crossing record is written.
    """
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.append(str(root))
    import worldseed
    return worldseed.seed()

# --- grade policy (DESIGN, see module docstring) ---------------------------
GRADE = {"trunk": 0.08, "spur": 0.12, "mountain": 0.18}
GRADE_CEILING = 0.25
# Margin held back from the measured +/-8 m apply clamp.  1 m deliberately
# generous: the routing field is sampled on a half-integer lattice and the
# ribbon is written on the integer lattice, so a written sample can sit up to
# 0.71 m away from any sample the profile was fitted against.
CLAMP_M = 8.0
CLAMP_MARGIN_M = 1.0
CUT_FILL_MAX = CLAMP_M - CLAMP_MARGIN_M

# A ribbon sample at exactly y=30 is awash.  Roads are held this far above it.
ROAD_FREEBOARD_M = 1.0
# Bridge decks clear the water plane by this much so a longship passes under
# nothing and a player does not wade onto the deck.
DECK_CLEARANCE_M = 2.5

# FORD_DEPENDS_ON: WHETHER THE WRITE REMOVES WATER.  Nothing else decides
# whether a wet run may stay TERRAIN.
#
# It used to depend on the run's LENGTH, then on its DEPTH, and both are
# properties of the water rather than of the write.  The quantity the writer
# refuses on is `ribbon.py`'s water sign: a sample the SEED drew below
# `WATER_LEVEL`, raised by this write to or above it, is water removed
# permanently -- the generated field is the only record it was there.  A
# "ford" filled to `WATER_LEVEL + ROAD_FREEBOARD_M` raises EVERY submerged
# station it covers, so DEPTH is a key naming a SUBSET: it separates shallow
# water from deep and lets all the shallow water be destroyed.
#
# MEASURED 2026-09-24 over all three plans -- Fresh42 22 fords on 9 of 14
# segments, Ulfsland 32 on 19, the northern trunk 51 on 5: 618 of 618
# submerged centreline stations under those 105 fords end at or above the
# plane, worst +6.97 m.  Not one ford anywhere leaves the water it crosses.
FORD_DEPENDS_ON = (
    "whether any station under the run is BELOW the water plane at all "
    "(`WATER_LEVEL - t.min() > 0`), because the profile over a ford is fitted "
    "to WATER_LEVEL + ROAD_FREEBOARD_M and therefore raises every submerged "
    "station above the plane. NOT the run's length and NOT its depth: both "
    "are facts about the water and neither is a fact about what the write "
    "does to it. The surviving population is real and must not be condemned "
    "-- a wet run entirely between WATER_LEVEL and WATER_LEVEL + "
    "ROAD_FREEBOARD_M is a shore that needs lifting to freeboard with no "
    "water to remove, and it stays terrain.")


def ford_rule(any_wet: bool, need_fill_m: float, depth_m: float) -> bool:
    """May this wet run stay TERRAIN, or does Crossings own it?

    THE RULE HAS BEEN WRITTEN THREE TIMES AND THE FIRST TWO ASKED THE WRONG
    QUESTION.  Version one capped a ford by LENGTH (<= 8 m, Crossings' own
    pier-bay spacing) and turned a road hugging a tidal flat into a 450 m
    "bridge".  Version two capped it by DEPTH (<= 1.0 m below the plane):
    ground that shallow can be filled to road freeboard well inside the
    MEASURED +/-8 m apply clamp, so it was called a causeway at any length.
    Both asked "can this be filled".  Neither asked what the WRITER asks.

    `ribbon.py`'s water-sign gate refuses a write that RAISES any sample the
    seed drew below the plane to or above it, because the generated field is
    the only record that water was ever there and no later write restores it.
    Its remedy is spelled out: "STOP THE RIBBON AT THE BANK, not narrow the
    batter: a causeway over water is a deck, and Crossings owns it."

    A ford under version two is BY CONSTRUCTION the thing that gate refuses:
    `need_fill_m` is measured to `WATER_LEVEL + ROAD_FREEBOARD_M`, so every
    submerged station in a ford ends a metre ABOVE the plane.  MEASURED
    2026-09-24 over all three plans: 618 of 618 submerged centreline stations
    under 105 fords end at or above the plane, worst +6.97 m
    (`S13-stathub-dock`, Ulfsland).  Not one ford anywhere leaves the water it
    crosses.  Fresh42's `T1-portalhub-temple` is the one that reached the
    gate: 3 fords, 8 submerged centreline stations raised by up to 4.23 m,
    `ribbon.py --validate` rc=8 with "369 of 411 submerged sample(s) raised,
    worst +5.119 m at [-105,-274]" -- printed on the same run as "crossing
    premise: CLEAR -- 0 of 0 crossing(s) on this segment asked".

    AND THE SURVIVING POPULATION IS REAL, which is why this is not simply
    "no wet run is ever a ford".  A run lying entirely between `WATER_LEVEL`
    and `WATER_LEVEL + ROAD_FREEBOARD_M` is a shore that needs lifting to
    freeboard with no water to remove.  MEASURED on the recorded corpus, this
    rule applied to Fresh42 turns 22 fords into 6 fords and 2 crossings into
    18, leaves 47 of 47 submerged centreline stations BRIDGED with none
    written as terrain, and the longest crossing it creates anywhere is
    17.1 m (`S13-stathub-dock`) against Crossings' MEASURED 96 m absolute
    span ceiling -- so no segment in any plan is rejected for span by it.

    See `FORD_DEPENDS_ON`.
    """
    return bool(any_wet) and need_fill_m <= CUT_FILL_MAX and depth_m <= 0.0


# --- the ROAD SURFACE's own half-extent, defined ONCE for every caller ------
#
# The shoulder: one metre of dirt each side of the paved carriageway.  It is
# not decoration -- it is the visual edge that tells the operator where the
# road is from a distance, and it gives the paved band a margin so a
# half-metre rasterisation difference at the edge cannot leave a gap in the
# paving.
SHOULDER_M = 1.0


def footprint_half_m(width_m: float) -> float:
    """Half-extent of the LEVEL road surface: carriageway half plus shoulder.

    THE ONE PLACE THIS IS COMPUTED.  It lives here, in the lowest module both
    the router and the writer already import, because the same quantity was
    being derived independently in three files and consumed as though it were
    three different things.  `ribbon.stamp` writes `paved_cleared` out to
    `width/2` and `dirt_cleared` out to `width/2 + SHOULDER_M`, and NOTHING
    past that at the road's own profile height -- everything beyond is batter,
    a plane at grade rather than a level bench.  So this number, and only this
    number, is:

      * the outermost lateral offset at which the APPLY CLAMP has to absorb
        `profile_y - generated(x, z)` on a LEVEL surface;
      * the footprint `ribbon.location_check` judges -- which is why that
        function is handed `half_width_m=0.0`: `road_written` IS this
        footprint already;
      * the keep-out half-width the router must reserve (`north.POI_HALF_M`).

    THE DEFECT IT CLOSES, MEASURED on N4-mid2-terminus, 2026-09-18: the leg
    refused on the clamp in all three routing variants (239 / 120 / 112
    carriageway samples past +/-8 m) while its own routing record read
    `stations_over_clamp: 0`.  Of the 112, seventy-one were carriageway-band
    and EVERY ONE sat at lat 3.99-4.12 m -- the outermost shoulder sample
    line, not one on the paved +/-3.0 m.  `fit_profile` was solved against the
    CENTRELINE's generated height and then judged against the FOOTPRINT's, and
    on a cross slope the shoulder at 4.0 m carries about half the transverse
    relief on top of the profile's own cut.  One object, two instruments,
    nobody reconciling them.

    The fix is `network.transverse_window`, which fits against what this
    function measures.  It is NOT to reclassify the shoulder as clippable:
    that narrows the road to fit a profile never solved for it, leaves
    up-to-9.6 m walls 4 m from the centreline, and would stop the POI gate
    refusing a shoulder sample inside a hard radius -- the case that correctly
    refused N0's first plan.
    """
    return width_m / 2.0 + SHOULDER_M


@dataclass
class Route:
    id: str
    cls: str                      # trunk | spur | mountain
    nodes: list[tuple[float, float]]          # centreline xz, in order
    s: list[float] = dc_field(default_factory=list)      # arclength at each node
    terrain: list[float] = dc_field(default_factory=list)
    y: list[float] = dc_field(default_factory=list)      # fitted profile
    crossings: list[dict] = dc_field(default_factory=list)
    half_width_m: float = 4.0
    notes: dict = dc_field(default_factory=dict)

    @property
    def length_m(self) -> float:
        return self.s[-1] if self.s else 0.0


# ---------------------------------------------------------------------------
# phase 1: corridor search
# ---------------------------------------------------------------------------

def smooth(h: np.ndarray, radius_cells: int) -> np.ndarray:
    """Box-mean over a (2r+1)^2 window via a summed-area table.

    This is the "achievable" surface: relief finer than the window is inside
    what +/-8 m of cut and fill can remove, relief coarser than it is the grade
    the road has to actually climb.  Window radius is chosen from the clamp, not
    from taste -- see `plan_window`.
    """
    if radius_cells <= 0:
        return h.astype(np.float64)
    a = np.pad(h.astype(np.float64), radius_cells, mode="edge")
    sat = a.cumsum(0).cumsum(1)
    sat = np.pad(sat, ((1, 0), (1, 0)))
    k = 2 * radius_cells + 1
    n = h.shape[0]
    out = (sat[k:k + n, k:k + n] - sat[0:n, k:k + n]
           - sat[k:k + n, 0:n] + sat[0:n, 0:n]) / (k * k)
    return out


# TERMINAL_REACH_DEPENDS_ON: A TERMINAL IS A CELL THE OTHER END CAN REACH.
#
# `nearest_allowed` snaps a node to the nearest cell no keep-out holds and asks
# nothing about whether a road can get there.  MEASURED, Fresh42 drive 6 stage
# 11: S4-meadhall-southeast "no route found", band-free too.  The southeast
# node (388, -614) is its own snap, in a 6-cell pocket closed by the tree-near
# and lh-south pads (north, west) and census bodies (tin rocks east, the
# greydwarf camp south).  Stage 9 had no census and reached it; drive 5 came in
# from the north, where the re-sited tree-near now stands.
#
# THE RULE: when the search from one end exhausts without reaching the other,
# and the two ends are ONE piece of the step graph once the keep-outs are
# lifted -- terrain, the span ceiling and the census confinement all kept --
# and no unread cell stands on the smaller piece's rim, the end in the smaller
# piece is re-snapped to the nearest cell, within the snap radius, of the
# other end's piece.  Ends that land, water or unread ground separate are
# refused exactly as before; unread ground is the census probe's
# (network.CENSUS_BAND_DEPENDS_ON), never a reason to move an end -- MEASURED
# on network.selftest_census_band's BODY case, where a body closing the band
# would otherwise have ended the leg at the body.
#   the_other_ends_piece            the cells a search from the kept end enters
#   only_keepouts_close_the_pocket  `forbid` less `unread` is what separates,
#                                   and the pocket's rim is all read ground
#   the_snap_radius                 `nearest_allowed`'s own 200 m
# NEUTER `_TERMINAL_REACH_MODE = "nearest"`: the shipped snap.  Proven by
# network.selftest_terminal_reach.
TERMINAL_REACH_DEPENDS_ON = ("the_other_ends_piece",
                             "only_keepouts_close_the_pocket",
                             "the_snap_radius")
_TERMINAL_REACH_MODE = "reachable"   # reachable | nearest (the shipped snap)
SNAP_M = 200.0


@dataclass
class Corridor:
    """A* over a decimated view of one island's 1 m field.

    Decimation is not an approximation of the OUTPUT -- the profile and the
    ribbon are both computed on the full 1 m field.  It is a search-space
    choice: the ribbon is 8 m wide, so resolving the corridor finer than a few
    metres decides nothing, and the spawn island alone is 10.24 M samples.
    """

    fld: Field
    cell_m: float = 4.0
    grade_max: float = GRADE["trunk"]
    bridge_entry_cost_m: float = 400.0   # metres of road a new bridge is worth
    bridge_per_m_cost: float = 12.0      # a bridged metre costs this many road metres
    grade_weight: float = 6.0
    roughness_weight: float = 2.0
    biome_penalty: dict[str, float] = dc_field(default_factory=lambda: {
        # Multipliers on step cost.  Swamp is penalised hard because its ground
        # sits within a metre or two of the water plane, so a levelled ribbon
        # there is a dyke; Mountain because it is the steep biome and a road
        # through it is a switchback we would rather avoid than optimise.
        "Swamp": 4.0, "Mountain": 2.5, "Mistlands": 3.0, "AshLands": 3.0,
        "DeepNorth": 2.0, "Ocean": 1.0, "Plains": 1.0, "Meadows": 1.0,
        "BlackForest": 1.0, "None": 1.0, "Unknown": 1.0,
    })
    # Cost multiplier for a shoreline cell that is land but sits at or below
    # road freeboard: buildable as a levelled causeway, dearer than dry ground,
    # far cheaper than a bridge.  Taste pick, stated as one.
    shore_cost_mult: float = 3.5
    # Crossings' MEASURED absolute span ceiling: 96 m (72 m free span at a 3x
    # safety factor on an iron stringer under the deck), from its own
    # implementation of WearNTear::UpdateSupport.
    max_span_m: float = 96.0
    # When set, water is not traversable at any price.  Used for the land-only
    # RETRY of a segment whose first route produced a bridged run longer than
    # any bridge can be: see network.build_segment.
    forbid_all_water: bool = False

    def __post_init__(self) -> None:
        f = self.fld
        self.dec = max(1, int(round(self.cell_m / f.step)))
        self.cell_m = self.dec * f.step
        n = (f.n // self.dec) * self.dec
        # Mean-pool height; max-pool slope so a decimated cell that hides a
        # cliff is not reported as gentle.
        blk = f.h[:n, :n].reshape(n // self.dec, self.dec, n // self.dec, self.dec)
        self.H = blk.mean(axis=(1, 3)).astype(np.float64)
        self.Hmin = blk.min(axis=(1, 3)).astype(np.float64)
        self.Hmax = blk.max(axis=(1, 3)).astype(np.float64)
        bb = f.biome[:n, :n].reshape(n // self.dec, self.dec, n // self.dec, self.dec)
        # Modal-ish: the biome of the cell's first sample is enough for a
        # penalty multiplier, and cheaper than a mode over 16 samples.
        self.Bi = bb[:, 0, :, 0]
        self.m = self.H.shape[0]
        self.x0 = f.x0 + (self.dec - 1) * f.step / 2
        self.z0 = f.z0 + (self.dec - 1) * f.step / 2
        # ACHIEVABLE SURFACE.  Two steps, and the second one is the one that
        # matters.  First a box-mean over a window whose radius is the distance
        # the design grade needs to absorb the full permitted cut, so relief
        # finer than that is levellable and should not steer the route.  Then --
        # CRITICALLY -- clip the result back to within the permitted cut/fill of
        # the real ground, because any profile the ribbon can actually hold is
        # inside that band and a smoothed surface outside it describes a road
        # that cannot be built.
        #
        # MEASURED consequence of omitting the clip: on the temple -> S1
        # abutment segment the ground falls 25 m in the last 107 m (a 23 %
        # grade) and the unclipped 87 m-window mean smoothed that drop away
        # entirely, so A* reported the corridor as gentle, routed 452 m where a
        # 35 m descent at 8 % needs 440 m of run for the descent alone, and the
        # profile solver then PROVED 86 stations infeasible -- handing Crossings
        # a fictitious 107 m "bridge" over dry hillside at y=56. The clip makes
        # that drop visible to the search as at least (25 - 2*7)/107 = 10 %,
        # which the gradient term prices properly and the router routes around.
        r_m = CUT_FILL_MAX / max(self.grade_max, 1e-6)
        self.window_m = r_m
        sm = smooth(self.H, max(1, int(round(r_m / self.cell_m))))
        self.S = np.clip(sm, self.H - CUT_FILL_MAX, self.H + CUT_FILL_MAX)
        # THREE WATER CATEGORIES, not two, because two was wrong and it showed.
        #   water    -- no land at all in the cell: MAX sample at or below the
        #               plane.  A road here is a bridge, full stop.
        #   fillable -- some land in the cell but the MEAN is at or below road
        #               freeboard: a beach, a spit, a shoreline notch.  A ribbon
        #               CAN be built here (the +/-8 m clamp allows the fill) and
        #               it is a causeway, which is terrain and therefore mine.
        #   dry      -- everything else.
        # MEASURED reason this matters: with `wet` defined as MIN-pooled
        # h <= 31 and treated as impassable, the beach corridor between
        # StartTemple and the S1 abutment read as water, and the router planned
        # a 136 m BRIDGE along a shoreline it could have paved.  The abutment is
        # 1 m from the main landmass (MEASURED, survey.py probe) -- there was
        # never a channel there.  A mask that answers "wet" for a beach is a
        # mask answering a question it is not measuring.
        self.water = self.Hmax <= WATER_LEVEL + 0.5
        self.fillable = (~self.water) & (self.H <= WATER_LEVEL + ROAD_FREEBOARD_M)
        self.wet = self.water
        # THE SPAN CEILING, ENCODED GEOMETRICALLY INSTEAD OF AS A PRICE.
        #
        # Pricing water per metre cannot express "no bridge longer than 96 m",
        # and trying made the router absurd in both directions, MEASURED both
        # times: at 10 road-metres per water metre it took shoreline shortcuts
        # and produced six spans of 103-562 m, all unbuildable; at 400 it walked
        # 3.8 km to reach a dock 236 m away rather than cross 10 m of channel.
        # A price is the wrong instrument because the constraint is not "water is
        # costly", it is "a span has a maximum length".
        #
        # So: the distance from a water cell to the nearest land is at least half
        # the span of any crossing through it, which makes
        #     forbid water where dist_to_land > max_span / 2
        # an exact encoding of Crossings' MEASURED 96 m absolute ceiling (72 m
        # free span at a 3x safety factor on an iron stringer, 96 m absolute).
        # Narrow channels stay crossable at a moderate price; wide water is not
        # traversable at any price, so the search cannot propose a bridge that
        # cannot be built.
        d_land = ndimage.distance_transform_edt(self.water) * self.cell_m
        self.dist_to_land_m = d_land
        self.water_forbidden = self.water & (d_land > self.max_span_m / 2.0)
        # Roughness: how much local relief the ribbon has to chew through,
        # expressed as a fraction of the cut/fill the clamp permits.
        self.rough = np.clip((self.Hmax - self.Hmin) / CUT_FILL_MAX, 0.0, 4.0)


    def cell_of(self, x: float, z: float) -> tuple[int, int]:
        j = int(round((x - self.x0) / self.cell_m))
        i = int(round((z - self.z0) / self.cell_m))
        if not (0 <= i < self.m and 0 <= j < self.m):
            raise ValueError(f"({x}, {z}) is outside the corridor grid")
        return i, j

    def cell_world(self, i: int, j: int) -> tuple[float, float]:
        return self.x0 + j * self.cell_m, self.z0 + i * self.cell_m

    # POI keep-out, supplied by the caller from poi.py.  Not computed here: the
    # radius budget depends on the ribbon width and on which instances are
    # PROTECTED, and that policy belongs in one place.
    forbid: np.ndarray | None = None
    soft: np.ndarray | None = None
    poi_weight: float = 8.0
    # The part of `forbid` that is ground the census never read (the caller's
    # confinement), kept apart so TERMINAL_REACH never moves an end off it.
    unread: np.ndarray | None = None
    # What the last `search` did to an end under TERMINAL_REACH, or None.
    last_terminal: dict | None = None

    def blocked(self, i: int, j: int) -> bool:
        return self.forbid is not None and bool(self.forbid[i, j])

    def pieces(self, blocked: np.ndarray | None) -> np.ndarray:
        """The weakly connected pieces of the graph `_step_cost` walks, with
        `blocked` standing in for `forbid`: a label per cell, -1 where no step
        may enter.  Weak, because only a water-to-land step is one-way."""
        from scipy.sparse import coo_matrix                 # noqa: PLC0415
        from scipy.sparse.csgraph import connected_components  # noqa: PLC0415
        m = self.m
        enter = ~self.water_forbidden
        if blocked is not None:
            enter = enter & ~blocked
        if self.forbid_all_water:
            enter = enter & ~self.water
        idx = np.arange(m * m).reshape(m, m)
        rows, cols = [], []
        for di, dj in ((0, 1), (1, 0), (1, 1), (1, -1)):
            a = (slice(0, m - di), slice(max(0, -dj), m - max(0, dj)))
            b = (slice(di, m), slice(max(0, dj), m - max(0, -dj)))
            d = self.cell_m * math.hypot(di, dj)
            ok = (enter[a] & enter[b]
                  & (self.water[a] | self.water[b]
                     | (np.abs(self.S[b] - self.S[a]) / d <= GRADE_CEILING)))
            rows.append(idx[a][ok])
            cols.append(idx[b][ok])
        r, c = np.concatenate(rows), np.concatenate(cols)
        g = coo_matrix((np.ones(len(r), dtype=np.int8), (r, c)),
                       shape=(m * m, m * m))
        _n, lab = connected_components(g, directed=False)
        lab = lab.reshape(m, m)
        lab[~enter] = -1
        return lab

    def reach_terminals(self, s: tuple[int, int], g: tuple[int, int],
                        start_xz, goal_xz,
                        search_m: float = SNAP_M) -> dict | None:
        """TERMINAL_REACH_DEPENDS_ON: the end to move and where, or None when
        the rule does not apply (see the block above the class)."""
        full = self.pieces(self.forbid)
        ls, lg = int(full[s]), int(full[g])
        if ls == lg:
            return None
        keep = (None if self.forbid is None or self.unread is None
                else self.forbid & self.unread)
        lifted = self.pieces(keep)
        if lifted[s] < 0 or lifted[s] != lifted[g]:
            return None
        n_s, n_g = int((full == ls).sum()), int((full == lg).sum())
        end = "goal" if n_g <= n_s else "start"
        (pi, pj), label, xz = ((g, ls, goal_xz) if end == "goal"
                               else (s, lg, start_xz))
        # A POCKET THE CENSUS HAS READ ALL ROUND: an unread cell on its rim
        # is ground that may open it, which is the census probe's to ask.
        pocket = full == int(full[pi, pj])
        rim = ndimage.binary_dilation(pocket, structure=np.ones((3, 3),
                                                                bool)) & ~pocket
        if keep is not None and bool((rim & keep).any()):
            return None
        i0, j0 = self.cell_of(float(xz[0]), float(xz[1]))
        r = int(search_m / self.cell_m)
        i_lo, j_lo = max(0, i0 - r), max(0, j0 - r)
        win = (full[i_lo:i0 + r + 1, j_lo:j0 + r + 1] == label) & ~self.wet[
            i_lo:i0 + r + 1, j_lo:j0 + r + 1]
        if not win.any():
            return None
        ii, jj = np.nonzero(win)
        ii, jj = ii + i_lo, jj + j_lo
        k = int(np.argmin((ii - i0) ** 2 + (jj - j0) ** 2))
        to = (int(ii[k]), int(jj[k]))
        was, now = self.cell_world(pi, pj), self.cell_world(*to)
        return {"depends_on": list(TERMINAL_REACH_DEPENDS_ON), "end": end,
                "cell": to,
                "requested_xz": [round(float(xz[0]), 2), round(float(xz[1]), 2)],
                "snapped_xz": [round(was[0], 2), round(was[1], 2)],
                "moved_to_xz": [round(now[0], 2), round(now[1], 2)],
                "moved_m": round(math.hypot(now[0] - float(xz[0]),
                                            now[1] - float(xz[1])), 2),
                "pocket_cells": min(n_s, n_g),
                "why": "the snapped cell stands in a pocket only keep-outs "
                       "close; the road ends at the nearest cell the other "
                       "end reaches"}

    def nearest_allowed(self, x: float, z: float, search_m: float = 200.0) -> tuple[int, int]:
        """Snap a target to the nearest cell that is neither wet nor inside a
        POI keep-out.

        Both exclusions matter and for different reasons.  Installation centres
        are solved on flattened pads and some sit on the water line
        (`early-dock` solved to `water_dist_m` 0.0), so a raw snap can put the
        route's endpoint in the sea and make the whole route a bridge.  And
        `StartTemple` is BOTH a route destination and a 25 m-radius protected
        location, so the road has to terminate on its clearance ring rather than
        in its courtyard -- this is what makes that happen, and the resulting
        stand-off distance is reported per segment rather than assumed.
        """
        i0, j0 = self.cell_of(x, z)
        if not self.wet[i0, j0] and not self.blocked(i0, j0):
            return i0, j0
        r = int(search_m / self.cell_m)
        best = None
        for di in range(-r, r + 1):
            for dj in range(-r, r + 1):
                i, j = i0 + di, j0 + dj
                if not (0 <= i < self.m and 0 <= j < self.m):
                    continue
                if self.wet[i, j] or self.blocked(i, j):
                    continue
                d = di * di + dj * dj
                if best is None or d < best[0]:
                    best = (d, i, j)
        if best is None:
            raise ValueError(f"no allowed cell within {search_m} m of ({x}, {z})")
        return best[1], best[2]

    # --- search ---
    def _step_cost(self, a: tuple[int, int], b: tuple[int, int], d: float) -> float | None:
        ai, aj = a
        bi, bj = b
        if self.blocked(bi, bj):
            return None
        if self.water[bi, bj] and self.forbid_all_water:
            return None
        if self.water_forbidden[bi, bj]:
            # Wider than half the maximum buildable span from any land: no
            # bridge can reach across here, so the cell is not traversable.
            return None
        if self.water[bi, bj]:
            # Open water is not forbidden -- it is EXPENSIVE, so the search can
            # still reach an otherwise unreachable target and the resulting span
            # is handed to Crossings.  Forbidding it would answer "unreachable"
            # where the truth is "needs a bridge".
            return d * self.bridge_per_m_cost + (
                self.bridge_entry_cost_m if not self.water[ai, aj] else 0.0)
        g = abs(self.S[bi, bj] - self.S[ai, aj]) / d
        if g > GRADE_CEILING:
            return None
        pen = self.grade_weight * (g / self.grade_max) ** 2
        pen += self.roughness_weight * float(self.rough[bi, bj])
        if self.soft is not None:
            pen += self.poi_weight * float(self.soft[bi, bj])
        mult = self.biome_penalty.get(BIOME_NAMES.get(int(self.Bi[bi, bj]), "Unknown"), 1.0)
        if self.fillable[bi, bj]:
            # A beach or spit: buildable as a levelled causeway inside the
            # clamp, and a causeway is terrain and therefore mine, not a
            # structure.  Priced ABOVE dry ground because a shoreline ribbon
            # needs real fill and sits where a storm-surge-looking road reads
            # oddly, but far BELOW a bridge, so the router prefers paving a
            # beach over spanning it.
            mult *= self.shore_cost_mult
        return d * (1.0 + pen) * mult

    def search(self, start_xz: tuple[float, float], goal_xz: tuple[float, float]) -> list[tuple[float, float]]:
        self.last_terminal = None
        s = self.nearest_allowed(*start_xz)
        g = self.nearest_allowed(*goal_xz)
        path = self._astar(s, g)
        if path is None and _TERMINAL_REACH_MODE == "reachable":
            moved = self.reach_terminals(s, g, start_xz, goal_xz)
            if moved is not None:
                if moved["end"] == "goal":
                    g = moved["cell"]
                else:
                    s = moved["cell"]
                path = self._astar(s, g)
                if path is not None:
                    self.last_terminal = moved
        if path is None:
            raise ValueError("no route found")
        return path

    def _astar(self, s: tuple[int, int], g: tuple[int, int]
               ) -> list[tuple[float, float]] | None:
        si, sj = s
        gi, gj = g
        m = self.m
        nb = [(-1, 0, 1.0), (1, 0, 1.0), (0, -1, 1.0), (0, 1, 1.0),
              (-1, -1, math.sqrt(2)), (-1, 1, math.sqrt(2)),
              (1, -1, math.sqrt(2)), (1, 1, math.sqrt(2))]
        cm = self.cell_m
        goal = gi * m + gj
        start = si * m + sj
        g_score = {start: 0.0}
        came: dict[int, int] = {}
        # Heuristic is plain euclidean distance times the cheapest possible
        # multiplier (1.0), so it never overestimates and A* stays admissible.
        h0 = math.hypot(gj - sj, gi - si) * cm
        pq = [(h0, 0.0, start)]
        closed = set()
        while pq:
            _, gc, cur = heapq.heappop(pq)
            if cur in closed:
                continue
            closed.add(cur)
            if cur == goal:
                break
            ci, cj = divmod(cur, m)
            for di, dj, dd in nb:
                ni, nj = ci + di, cj + dj
                if not (0 <= ni < m and 0 <= nj < m):
                    continue
                nk = ni * m + nj
                if nk in closed:
                    continue
                c = self._step_cost((ci, cj), (ni, nj), dd * cm)
                if c is None:
                    continue
                ng = gc + c
                if ng < g_score.get(nk, math.inf):
                    g_score[nk] = ng
                    came[nk] = cur
                    heapq.heappush(pq, (ng + math.hypot(gj - nj, gi - ni) * cm, ng, nk))
        if goal not in came and goal != start:
            return None
        path = [goal]
        while path[-1] != start:
            path.append(came[path[-1]])
        path.reverse()
        return [self.cell_world(*divmod(k, m)) for k in path]


def simplify(nodes: list[tuple[float, float]], tol_m: float = 1.5) -> list[tuple[float, float]]:
    """Ramer-Douglas-Peucker.  A* returns one node per grid cell, i.e. a
    staircase; the ribbon is rasterised from segments, so a staircase would
    produce a scalloped edge the operator sees from the first step."""
    if len(nodes) < 3:
        return list(nodes)
    a, b = np.array(nodes[0]), np.array(nodes[-1])
    ab = b - a
    L = np.hypot(*ab)
    pts = np.array(nodes)
    if L < 1e-9:
        d = np.hypot(*(pts - a).T)
    else:
        d = np.abs(np.cross(np.broadcast_to(ab, pts.shape), pts - a)) / L
    k = int(d.argmax())
    if d[k] <= tol_m:
        return [nodes[0], nodes[-1]]
    return simplify(nodes[:k + 1], tol_m)[:-1] + simplify(nodes[k:], tol_m)


def resample(nodes: list[tuple[float, float]], spacing_m: float = 2.0
             ) -> tuple[list[tuple[float, float]], list[float]]:
    """Uniform stations along the polyline.  The profile is fitted at stations,
    not at corners, so a long straight does not get one grade constraint while a
    tight bend gets fifty."""
    out = [nodes[0]]
    s = [0.0]
    acc = 0.0
    for (x0, z0), (x1, z1) in zip(nodes, nodes[1:]):
        seg = math.hypot(x1 - x0, z1 - z0)
        if seg < 1e-9:
            continue
        t = spacing_m - acc
        while t < seg:
            f = t / seg
            out.append((x0 + (x1 - x0) * f, z0 + (z1 - z0) * f))
            s.append(s[-1] + spacing_m)
            t += spacing_m
        acc = seg - (t - spacing_m)
    if math.hypot(out[-1][0] - nodes[-1][0], out[-1][1] - nodes[-1][1]) > 1e-6:
        out.append(nodes[-1])
        s.append(s[-1] + math.hypot(out[-1][0] - out[-2][0], out[-1][1] - out[-2][1]))
    return out, s


# ---------------------------------------------------------------------------
# phase 2: longitudinal profile under BOTH constraints
# ---------------------------------------------------------------------------

def fit_profile(t: np.ndarray, s: np.ndarray, grade_max: float,
                cut_fill_max: float = CUT_FILL_MAX,
                free: np.ndarray | None = None,
                free_lo: np.ndarray | None = None,
                lo: np.ndarray | None = None,
                hi: np.ndarray | None = None) -> dict:
    """Slope-limited profile inside per-station bounds, with exact feasibility.

    Constraints:
        |y[i+1] - y[i]| <= grade_max * (s[i+1] - s[i])
        lo[i] <= y[i] <= hi[i]

    `lo`/`hi` may be supplied directly -- that is how a bridge deck (free
    height above the water plane), an abutment level run pinned to the height
    `Crossings` owns, and ordinary cut/fill ground all end up in ONE constraint
    set instead of being patched on afterwards.  Patching afterwards was the
    first version and it left stations outside the +/-8 m clamp, because a flat
    insert and a grade re-imposition each violate what the other just fixed.
    Omit them and the bounds default to the cut/fill box around `t`, widened to
    free height wherever `free` is set.

    METHOD.  Interval propagation, which is exact for this constraint set: with
    lo/hi initialised from the bounds, a forward sweep tightening
    hi[i] <= hi[i-1] + g*ds and lo[i] >= lo[i-1] - g*ds followed by the same
    sweep backwards yields bounds that are non-empty IF AND ONLY IF a feasible
    slope-limited profile exists inside them.  So an empty interval is a PROOF
    that the stretch cannot be built as terrain, which is exactly the statement
    `Crossings` needs, and a greedy walk inside the tightened bounds is
    guaranteed to stay feasible to the end.

    Returns the profile plus the infeasible index ranges.
    """
    n = len(t)
    ds = np.diff(s)
    big = 1e6
    if lo is None:
        lo = t - cut_fill_max
    if hi is None:
        hi = t + cut_fill_max
    lo = np.asarray(lo, dtype=np.float64).copy()
    hi = np.asarray(hi, dtype=np.float64).copy()
    if free is not None and free.any():
        lo = np.where(free, free_lo if free_lo is not None else WATER_LEVEL + DECK_CLEARANCE_M, lo)
        hi = np.where(free, big, hi)

    # Forward then backward propagation, run to a fixed point (two sweeps each
    # way is enough for a chain, but the loop makes that a fact, not a belief).
    for _ in range(6):
        changed = False
        for i in range(1, n):
            d = grade_max * ds[i - 1]
            nl, nh = max(lo[i], lo[i - 1] - d), min(hi[i], hi[i - 1] + d)
            if nl != lo[i] or nh != hi[i]:
                lo[i], hi[i] = nl, nh
                changed = True
        for i in range(n - 2, -1, -1):
            d = grade_max * ds[i]
            nl, nh = max(lo[i], lo[i + 1] - d), min(hi[i], hi[i + 1] + d)
            if nl != lo[i] or nh != hi[i]:
                lo[i], hi[i] = nl, nh
                changed = True
        if not changed:
            break

    bad = lo > hi + 1e-9
    spans: list[tuple[int, int]] = []
    i = 0
    while i < n:
        if bad[i]:
            j = i
            while j + 1 < n and bad[j + 1]:
                j += 1
            spans.append((i, j))
            i = j + 1
        else:
            i += 1

    # Greedy walk inside the tightened bounds: follow terrain wherever the
    # bounds allow it.  On infeasible indices the bounds are crossed, so take
    # their midpoint and let the caller convert the span into a crossing.
    y = np.empty(n)
    y[0] = min(max(t[0], lo[0]), hi[0]) if not bad[0] else 0.5 * (lo[0] + hi[0])
    for i in range(1, n):
        d = grade_max * ds[i - 1]
        l = max(lo[i], y[i - 1] - d)
        h = min(hi[i], y[i - 1] + d)
        if l > h:
            y[i] = 0.5 * (l + h)
        else:
            y[i] = min(max(t[i], l), h)
    # One symmetric relaxation pass: the greedy walk is order-dependent (it
    # commits early and can hold a grade longer than it needs to), and averaging
    # against the reverse walk removes that asymmetry without leaving the
    # feasible bounds.
    yb = np.empty(n)
    yb[-1] = min(max(t[-1], lo[-1]), hi[-1]) if not bad[-1] else 0.5 * (lo[-1] + hi[-1])
    for i in range(n - 2, -1, -1):
        d = grade_max * ds[i]
        l = max(lo[i], yb[i + 1] - d)
        h = min(hi[i], yb[i + 1] + d)
        yb[i] = 0.5 * (l + h) if l > h else min(max(t[i], l), h)
    mix = 0.5 * (y + yb)
    # Re-impose the slope constraint on the mixture (a convex combination of two
    # feasible profiles is feasible for |dy| <= d, so this is a no-op assert in
    # the feasible region; it matters across infeasible spans).
    for i in range(1, n):
        d = grade_max * ds[i - 1]
        mix[i] = min(max(mix[i], mix[i - 1] - d), mix[i - 1] + d)
    for i in range(n - 2, -1, -1):
        d = grade_max * ds[i]
        mix[i] = min(max(mix[i], mix[i + 1] - d), mix[i + 1] + d)
    y = mix

    grade = np.abs(np.diff(y)) / np.maximum(ds, 1e-9)
    cf = y - t
    return {
        "y": y, "lo": lo, "hi": hi,
        "infeasible_spans": spans,
        "grade": grade,
        "grade_max": float(grade.max()) if len(grade) else 0.0,
        "cut_max": float(cf.min()), "fill_max": float(cf.max()),
    }


# ---------------------------------------------------------------------------
# phase 2b: A ROUTE BESIDE ITSELF -- two branches of one line whose benches no
# earthwork can join
# ---------------------------------------------------------------------------
#
# SELF_ADJACENCY_DEPENDS_ON: THE DESIGN HEIGHT AT BOTH CLAIMING FEET, HOW FAR
# PAST ITS OWN BENCH EDGE EACH SAMPLE STANDS, AND ONE LATTICE EDGE OF SLIDE.
# No ground.
#
# THE QUANTITY IS THE WRITER'S OWN, LIFTED TO PLAN TIME.  `ribbon.py` refuses
# a cell two claims of one ribbon cannot share by
#
#     unabsorbable = |y1 - y2| - BATTER_GRADE * (run1 + run2)
#
# (`ribbon.BATTER_TARGET_DEPENDS_ON`), `run_i` measured past each claim's own
# bench edge.  Here it is taken across the one lattice edge a walker actually
# crosses between two claims: for every pair of 4-adjacent lattice samples
# within batter reach, each claimed -- nearest wins, by the WRITER's own
# `ribbon.lateral_and_y_grid` -- by a different foot of this line,
#
#     unabsorbable = |y(foot1) - y(foot2)| - grade * (run1 + lattice + run2)
#
# Sample 1 can stand anywhere in `y1 +/- grade * run1` and still be reachable
# from its own bench within the slide limit, sample 2 likewise, and the step
# between them may be one lattice edge of slide; a positive number is a step
# over the limit that NO earthwork can remove, whatever the ground.  It is a
# fact about where the line was ROUTED and how it was PROFILED, both of which
# are this module's -- so it is decided here, not discovered by a census.
#
# MEASURED 2026-09-24, Fresh42, on the plan step 4 was run against: this key
# fires on EXACTLY the five segments the rim gate refused at step 4 -- T4 94
# lattice edges worst 1.076 m, T6 116 / 0.869, T7 108 / 0.802, T9 258 /
# 1.822, S13 26 / 0.660 -- plus T8 (131 / 0.794), which the junction gate
# refused first and so never reached the rim, and S5, S11 and T13 by at most
# 0.172 m.  It is clear on T1, T3, T5, S4 and S7.  The shape is one shape: an
# 8-connected A* switchback -- a 135-degree turn back up the hill, legs 4-12 m
# apart -- carrying up to 3.4 m of profile across a gap the slide limit spans
# in under a metre.  Five earthwork reconciliations of the same population
# were tried at step 4 and every one made it worse (ribbon.py
# `joint_claim_band_on`).
#
# WHY THE LATTICE AND NOT THE STATIONS.  The first cut of this key modelled
# each station's claim as its cross-section and cleared every claim the
# writer makes through a VERTEX's clamped end.  MEASURED on the plan it
# joined: T7-stathub-stenvik (556,-19)|(555,-19), two CARRIAGEWAY samples one
# metre apart claimed by feet 15.7 m apart along the line, 0.993 m of profile
# between them -- CAUSED at step 4.  A key built from a second model of the
# claim is a second definition of it; this one asks the writer.
#
# WHY EACH TERM, AND THE KEY EACH WRONG SPELLING WOULD BE:
#   design_height_at_both_claiming_feet   a key without it refuses every
#       switchback there is -- geometry alone cannot tell a joinable hairpin
#       from an unjoinable one.
#   run_past_each_claims_bench_edge   NOT the distance to the centreline,
#       which credits the grade with `footprint_half_m` per side of run that
#       is paved LEVEL and cannot slope -- a SUPERSET of the run, which clears
#       two carriageways standing edge to edge.
#   one_lattice_edge_of_slide   the rim gate's own allowance between two
#       distinct samples.  Without it two paved samples of one straight graded
#       road a metre apart are refused, i.e. every road there is.
#
# THE POPULATION is every 4-adjacent pair of lattice samples, in the zones the
# writer itself would stamp (`ribbon.segment_zones`), both within batter reach
# of a TERRAIN run -- the writer claims nothing from a run touching a bridge.
SELF_ADJACENCY_DEPENDS_ON = (
    "design_height_at_both_claiming_feet",
    "run_past_each_claims_bench_edge",
    "one_lattice_edge_of_slide",
)
SELF_ADJACENCY_EPS_M = 1e-6
# THE JOIN AIMS 1 cm INSIDE THE ALLOWANCE, because the writer never sees the
# profile it was joined on: `network.py` emits `profile_y` to 1 mm and
# `nodes` to 1 cm.  MEASURED on the first joined Fresh42 plan, re-read from
# its own YAML: 68 pairs back over the limit by at most 0.007 m, every one a
# pair the join had put exactly ON it.
SELF_ADJACENCY_JOIN_MARGIN_M = 0.01


def _writer():
    """`ribbon`, imported when first asked: it imports this module, so the
    claim function can only be borrowed at call time."""
    import ribbon                                           # noqa: PLC0415
    return ribbon


def claim_run_m(lat, bench_half_m: float):
    """How far past its own bench edge a sample stands from the foot that
    claims it; zero on the bench.  See SELF_ADJACENCY_DEPENDS_ON."""
    return np.maximum(np.asarray(lat, dtype=np.float64) - bench_half_m, 0.0)


def unabsorbable_m(dy, run_sum, grade: float, lattice_m: float):
    """`|dy| - grade * (run1 + run2 + lattice)`: positive where no surface
    joins two claims across one lattice edge inside the slide limit."""
    return np.abs(dy) - grade * (np.asarray(run_sum) + lattice_m)


def self_adjacency_lattice(pts, is_bridge, width_m: float, *, grade: float,
                           batter_m: float, lattice_m: float,
                           dy_cap: float) -> dict:
    """Every lattice edge between two claims of this line that a profile held
    to `dy_cap` per metre COULD leave unabsorbable, from the writer's own
    claim function, and the STATION pairs that bound them.

    The winner at each sample is a fact about the geometry alone, so this is
    computed once per line.  An edge is kept where `unabsorbable_m(dy_cap *
    |s1 - s2|, ...)` exceeds minus the join margin: no profile inside the
    chain limit can put more than `dy_cap * |s1 - s2|` between the two feet,
    so an edge left out cannot be violated at that grade.

    Each edge is bound by one difference constraint on the stations NEAREST
    its two feet, slackened per rung by the grade those feet can stand off
    them -- see `station_pairs`."""
    R = _writer()
    tc = R.tcdata
    nodes = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
    n = len(nodes)
    br = (np.zeros(n, dtype=bool) if is_bridge is None
          else np.asarray(is_bridge, dtype=bool))
    h = footprint_half_m(width_m)
    reach = h + batter_m
    arcs = np.asarray(R.node_arcs(nodes, br), dtype=np.float64)
    zones = R.segment_zones([list(p) for p in nodes], list(br), width_m,
                            batter_m=batter_m) if n >= 2 else []
    ax = (np.arange(tc.PITCH) - tc.PITCH // 2) * tc.SCALE
    zero = np.zeros(n)
    cols = {k: [] for k in ("s1", "s2", "run1", "run2", "x1", "z1", "x2",
                            "z2")}
    for zx, zz in zones:
        cx, cz = tc.zone_centre(zx, zz)
        X = (cx + ax)[None, :] + np.zeros((tc.PITCH, 1))
        Z = (cz + ax)[:, None] + np.zeros((1, tc.PITCH))
        LAT, _y, S = R.lateral_and_y_grid(nodes, zero, br, X, Z, reach,
                                          grade, h)[:3]
        run = claim_run_m(LAT, h)
        inb = LAT <= reach
        for a, b in (((slice(None), slice(None, -1)),
                      (slice(None), slice(1, None))),
                     ((slice(None, -1), slice(None)),
                      (slice(1, None), slice(None)))):
            m = inb[a] & inb[b]
            m &= (unabsorbable_m(dy_cap * np.abs(S[a] - S[b]),
                                 run[a] + run[b], grade, lattice_m)
                  > -SELF_ADJACENCY_JOIN_MARGIN_M)
            if not m.any():
                continue
            for k, src in (("s1", S[a]), ("s2", S[b]), ("run1", run[a]),
                           ("run2", run[b]), ("x1", X[a]), ("z1", Z[a]),
                           ("x2", X[b]), ("z2", Z[b])):
                cols[k].append(src[m])
    e = {k: (np.concatenate(v) if v else np.zeros(0)) for k, v in cols.items()}
    # Zone lattices share their border row and column; an edge seen twice is
    # one edge.
    if len(e["s1"]):
        key = np.stack([np.rint(e[k]).astype(np.int64)
                        for k in ("x1", "z1", "x2", "z2")], axis=1)
        _u, first = np.unique(key, axis=0, return_index=True)
        e = {k: v[np.sort(first)] for k, v in e.items()}

    # THE STATION PAIR THAT BOUNDS EACH EDGE: the station NEAREST each foot.
    # A foot's height is its run's interpolation, so at rung `r` it stands
    # within `r * d` of its nearest station `d` metres away, and
    #     |y_near1 - y_near2| <= allow - r * (d1 + d2)
    # bounds the feet themselves.  Four bracketing pairs at the full
    # allowance were tried first and are sufficient too, but conservative by
    # up to two whole stations of grade: MEASURED, they refused
    # T9-wttown-dockshore at the ceiling rung with station 776's interval
    # empty by 0.083 m.  The allowance IS the key at zero height difference,
    # so the constraint the join obeys and the predicate that judges it
    # cannot be two formulas.
    def nearest(s):
        k = np.clip(np.searchsorted(arcs, s), 0, max(n - 1, 0))
        km = np.maximum(k - 1, 0)
        k = np.where(np.abs(arcs[km] - s) <= np.abs(arcs[k] - s), km, k)
        return k, np.abs(arcs[k] - s)
    n1, d1 = nearest(e["s1"]) if len(e["s1"]) else (np.zeros(0, int), 0.0)
    n2, d2 = nearest(e["s2"]) if len(e["s2"]) else (np.zeros(0, int), 0.0)
    return {"edges": e, "arcs": arcs, "pts": nodes,
            "near1": np.asarray(n1, dtype=np.int64),
            "near2": np.asarray(n2, dtype=np.int64),
            "dsum": np.asarray(d1 + d2, dtype=np.float64),
            "allow_e": -unabsorbable_m(0.0, e["run1"] + e["run2"], grade,
                                       lattice_m),
            "grade": float(grade), "lattice_m": float(lattice_m),
            "batter_m": float(batter_m), "dy_cap": float(dy_cap),
            "bench_half_m": h, "zones": len(zones)}


def station_pairs(cand: dict, rung: float) -> dict:
    """The difference constraints a profile at `rung` must meet: one per
    station pair, at the tightest allowance any of its edges needs, and only
    where the chain alone could not already hold it."""
    a, b = cand["near1"], cand["near2"]
    i, j = np.minimum(a, b), np.maximum(a, b)
    allow = cand["allow_e"] - rung * cand["dsum"]
    keep = i != j
    i, j, allow = i[keep], j[keep], allow[keep]
    if len(i):
        order = np.lexsort((allow, j, i))
        i, j, allow = i[order], j[order], allow[order]
        first = np.ones(len(i), dtype=bool)
        first[1:] = (i[1:] != i[:-1]) | (j[1:] != j[:-1])
        i, j, allow = i[first], j[first], allow[first]
    arc = cand["arcs"][j] - cand["arcs"][i] if len(i) else allow
    bind = rung * np.abs(arc) > allow - SELF_ADJACENCY_JOIN_MARGIN_M
    return {**cand, "i": i[bind].astype(np.int64),
            "j": j[bind].astype(np.int64), "allow": allow[bind],
            "arc": arc[bind]}


def self_adjacency_violations(y, cand: dict) -> list[dict]:
    """The lattice edges this profile leaves unabsorbable, worst first, each
    with the number a refusal has to carry."""
    y = np.asarray(y, dtype=np.float64)
    e = cand["edges"]
    if not len(e["s1"]):
        return []
    arcs = cand["arcs"]
    y1 = np.interp(e["s1"], arcs, y)
    y2 = np.interp(e["s2"], arcs, y)
    u = unabsorbable_m(y1 - y2, e["run1"] + e["run2"], cand["grade"],
                       cand["lattice_m"])
    bad = np.nonzero(u > SELF_ADJACENCY_EPS_M)[0]
    P = cand["pts"]
    out = []
    for k in bad[np.argsort(-u[bad])]:
        f1 = int(np.argmin(np.abs(arcs - e["s1"][k])))
        f2 = int(np.argmin(np.abs(arcs - e["s2"][k])))
        out.append({
            "cells": [[int(round(float(e["x1"][k]))),
                       int(round(float(e["z1"][k])))],
                      [int(round(float(e["x2"][k]))),
                       int(round(float(e["z2"][k])))]],
            "stations": [f1, f2],
            "xz": [[round(float(P[f1, 0]), 2), round(float(P[f1, 1]), 2)],
                   [round(float(P[f2, 0]), 2), round(float(P[f2, 1]), 2)]],
            "foot_arc_m": [round(float(e["s1"][k]), 2),
                           round(float(e["s2"][k]), 2)],
            "arc_between_m": round(abs(float(e["s1"][k] - e["s2"][k])), 2),
            "design_y": [round(float(y1[k]), 3), round(float(y2[k]), 3)],
            "height_difference_m": round(abs(float(y1[k] - y2[k])), 3),
            "run_past_bench_m": [round(float(e["run1"][k]), 3),
                                 round(float(e["run2"][k]), 3)],
            "allowance_m": round(abs(float(y1[k] - y2[k])) - float(u[k]), 3),
            "unabsorbable_m": round(float(u[k]), 3),
        })
    return out


def _close_bounds(lo: np.ndarray, hi: np.ndarray, reach: np.ndarray,
                  I: np.ndarray, J: np.ndarray, allow: np.ndarray,
                  ) -> tuple[np.ndarray, np.ndarray, bool]:
    """Tightest bounds under the chain limit AND every pair limit.

    Every constraint is a DIFFERENCE constraint (`y_b - y_a <= c`), so the
    tightened bounds are shortest paths and are EXACT: each station's
    interval is precisely the set of heights some feasible profile takes
    there, an empty one is a proof, and the vector of upper bounds (or of
    lower bounds) is itself a feasible profile.  The chain is closed in one
    pass each way by running minima over `C = cumsum(reach)`; the pair
    limits are relaxed alongside until nothing moves."""
    lo = np.asarray(lo, dtype=np.float64).copy()
    hi = np.asarray(hi, dtype=np.float64).copy()
    C = np.concatenate([[0.0], np.cumsum(reach)])
    for _ in range(4 * len(lo) + 16):
        hi = np.minimum(hi, C + np.minimum.accumulate(hi - C))
        hi = np.minimum(hi, np.minimum.accumulate((hi + C)[::-1])[::-1] - C)
        lo = np.maximum(lo, np.maximum.accumulate(lo + C) - C)
        lo = np.maximum(lo, np.maximum.accumulate((lo - C)[::-1])[::-1] + C)
        if (lo > hi + 1e-9).any():
            return lo, hi, False
        if not len(I):
            return lo, hi, True
        h2, l2 = hi.copy(), lo.copy()
        np.minimum.at(h2, J, hi[I] + allow)
        np.minimum.at(h2, I, hi[J] + allow)
        np.maximum.at(l2, J, lo[I] - allow)
        np.maximum.at(l2, I, lo[J] - allow)
        if (np.abs(h2 - hi).max() <= 1e-12
                and np.abs(l2 - lo).max() <= 1e-12):
            return lo, hi, True
        hi, lo = h2, l2
    return lo, hi, bool((lo <= hi + 1e-9).all())


def join_branches(y0, s, grade_max: float, lo, hi, cand: dict) -> dict:
    """Re-profile a line so no two of its branches are out of reach of each
    other, or PROVE that no profile inside `lo/hi` at `grade_max` can.

    `y0` is the profile `fit_profile` returned; where it already satisfies
    every pair it comes back UNCHANGED, byte for byte.  Otherwise the answer
    is the average of two feasible profiles -- the greatest one never above
    `y0` and the least one never below it -- so it equals `y0` wherever no
    pair limit binds, and meets in the middle of each landing where one does:
    the upper branch cut down half the excess, the lower one raised half.
    Both are exact shortest-path bounds (`_close_bounds`), and the set of
    feasible profiles is convex, so their average is feasible too."""
    y0 = np.asarray(y0, dtype=np.float64)
    s = np.asarray(s, dtype=np.float64)
    viol = self_adjacency_violations(y0, cand)
    base = {"candidate_pairs": int(len(cand["i"])),
            "candidate_edges": int(len(cand["edges"]["s1"])),
            "pairs_before": len(viol),
            "worst_before": viol[0] if viol else None}
    if not viol:
        return {**base, "verdict": "CLEAR", "y": y0, "pairs_after": 0,
                "worst_after": None, "stations_moved": 0, "max_move_m": 0.0}
    reach = grade_max * np.diff(s)
    I, J = cand["i"], cand["j"]
    allow = cand["allow"] - SELF_ADJACENCY_JOIN_MARGIN_M
    L, U, ok = _close_bounds(lo, hi, reach, I, J, allow)
    if not ok:
        empty = np.nonzero(L > U + 1e-9)[0]
        k = int(empty[0]) if len(empty) else -1
        return {**base, "verdict": "UNJOINABLE", "y": y0,
                "pairs_after": len(viol), "worst_after": viol[0],
                "stations_moved": 0, "max_move_m": 0.0,
                "proof": {"grade_max": grade_max,
                          "empty_stations": int(len(empty)),
                          "first_empty_station": k,
                          "interval_there": (None if k < 0 else
                                             [round(float(L[k]), 3),
                                              round(float(U[k]), 3)])}}
    _l, A, _ok = _close_bounds(L, np.minimum(U, np.maximum(y0, L)), reach,
                               I, J, allow)
    B, _u, _ok = _close_bounds(np.maximum(L, np.minimum(y0, U)), U, reach,
                               I, J, allow)
    y = 0.5 * (A + B)
    after = self_adjacency_violations(y, cand)
    moved = np.abs(y - y0)
    return {**base, "verdict": "JOINED" if not after else "UNJOINABLE",
            "y": y if not after else y0,
            "pairs_after": len(after),
            "worst_after": after[0] if after else None,
            "stations_moved": int((moved > 1e-6).sum()),
            "max_move_m": round(float(moved.max()), 3)}


def fit_ladder(t: np.ndarray, s: np.ndarray, lo: np.ndarray, hi: np.ndarray,
               free: np.ndarray, ladder: list[float], *, pts, width_m: float,
               grade: float, batter_m: float, lattice_m: float) -> dict:
    """The grade ladder, with the line's own branches IN the constraint set.

    A rung is taken when its profile is terrain-feasible AND no two branches
    are left out of reach of each other.  Where the design rung's profile
    leaves a pair unjoinable the ladder is climbed exactly as it is for
    terrain -- a landing flattened at a switchback turn makes the legs carry
    the climb it gave up -- and every rung tried is recorded.

    THE DESIGN LINE is the one the ladder picked before this existed -- the
    first terrain-feasible rung, or the ceiling where none is -- because that
    is the line step 4 would otherwise have been handed, and its pairs are
    the `before` a reader compares against.  A line no rung can join comes
    back `verdict: REFUSED` carrying the design line and its worst pair."""
    free = np.asarray(free, dtype=bool)
    cand = self_adjacency_lattice(pts, free, width_m, grade=grade,
                                  batter_m=batter_m, lattice_m=lattice_m,
                                  dy_cap=max(ladder))

    def attempt(rung, fit):
        # THE BOX IS OPENED ONLY WHERE THE CHAIN ALREADY PROVED IT EMPTY.  A
        # span `fit_profile` found infeasible is carried at its own midpoint
        # and handed on as residual -- no height there is inside the clamp --
        # so the join treats it as it treats a deck: bounded by the chain and
        # nothing else.  MEASURED why it cannot be pinned at the midpoint
        # instead: T9-wttown-dockshore's residual [765, 771] then emptied
        # station 765's interval on the ceiling rung and refused the whole
        # line for a span Crossings already owns.  On a feasible fit this is
        # the identity.
        y0 = fit["y"]
        lo_j = np.minimum(lo, y0)
        hi_j = np.maximum(hi, y0)
        for a, b in fit["infeasible_spans"]:
            lo_j[a:b + 1] = -1e6
            hi_j[a:b + 1] = 1e6
        return join_branches(y0, s, rung, lo_j, hi_j,
                             station_pairs(cand, rung))

    def record(j, rung, design, terrain_feasible, verdict=None):
        v = self_adjacency_violations(design[1]["y"], cand)
        return {**j, "verdict": verdict or j["verdict"], "rung": rung,
                "design_rung": design[0],
                "terrain_feasible": terrain_feasible,
                "candidate_pairs": int(len(station_pairs(cand, rung)["i"])),
                "candidate_edges": int(len(cand["edges"]["s1"])),
                "pairs_before": len(v), "worst_before": v[0] if v else None,
                "rungs_tried": tried}

    design = last = last_j = None
    tried: list[dict] = []
    for rung in ladder:
        fit = fit_profile(t, s, rung, lo=lo, hi=hi)
        last = (rung, fit)
        if [(a, b) for a, b in fit["infeasible_spans"]
                if not free[a:b + 1].all()]:
            continue
        if design is None:
            design = (rung, fit)
        j = last_j = attempt(rung, fit)
        tried.append({"rung": rung, "verdict": j["verdict"],
                      "pairs_at_rung": j["pairs_before"],
                      **({"proof": j["proof"]} if "proof" in j else {})})
        if j["verdict"] in ("CLEAR", "JOINED"):
            return {"rung": rung, "fit": fit, "y": j["y"],
                    "terrain_rung": design[0], "terrain_feasible": True,
                    "joined": record(j, rung, design, True), "cand": cand}
    feasible = design is not None
    if not feasible:
        # TERRAIN-INFEASIBLE ON EVERY RUNG: the ladder has always carried the
        # ceiling rung's profile on with its residual spans for Crossings, and
        # the line's branches are joined on that profile.
        rung, fit = design = last
        j = last_j = attempt(rung, fit)
        tried.append({"rung": rung, "verdict": j["verdict"],
                      "pairs_at_rung": j["pairs_before"],
                      "terrain_feasible": False,
                      **({"proof": j["proof"]} if "proof" in j else {})})
        if j["verdict"] in ("CLEAR", "JOINED"):
            return {"rung": rung, "fit": fit, "y": j["y"],
                    "terrain_rung": None, "terrain_feasible": False,
                    "joined": record(j, rung, design, False), "cand": cand}
    # NO RUNG JOINS IT.  The line handed back is the design line, and the
    # proof is the last rung's, because that is the rung that failed last.
    rung, fit = design
    v = self_adjacency_violations(fit["y"], cand)
    return {"rung": rung, "fit": fit, "y": fit["y"],
            "terrain_rung": rung if feasible else None,
            "terrain_feasible": feasible,
            "joined": record({**last_j, "y": fit["y"], "pairs_after": len(v),
                              "worst_after": v[0] if v else None,
                              "stations_moved": 0, "max_move_m": 0.0},
                             rung, design, feasible, verdict="REFUSED"),
            "cand": cand}

# ---------------------------------------------------------------------------
# phase 2c: LEGS THAT MEET -- one datum for the ground two profiles share
# ---------------------------------------------------------------------------
#
# JUNCTION_DATUM_DEPENDS_ON: EVERY PARTICIPANT'S OWN DESIGN HEIGHT ON THE
# GROUND IT SHARES -- read where the build's own gates read it -- EVERY PAD
# DATUM THERE, AND EACH LEG'S OWN BOUNDS, RUNG AND BRANCHES.
#
# `fit_ladder` fits each leg ALONE; nothing in it knows another leg exists.
# So where two legs meet, their heights agree by luck, and the build met the
# disagreement at the one moment it can no longer be fixed cheaply:
# `junction.build_gate` refuses the leg written SECOND, because the first has
# already fixed the ground.  MEASURED, Fresh42 step 4, 2026-09-25: T1 laid
# first; T3, T5 and T13 refused at the same point (-27.29, -37.17), 1.629 /
# 1.862 / 1.862 m; S5 at (-149.39, -260.98) 3.681 m; T9 at (-148.22,
# -186.4) 5.846 m -- against 0.7813 m.  The datum all five were judged
# against was T1's, chosen by the order the build wrote in: a key naming a
# SUBSET of the participants, namely one of them.
#
# THE PLAN ALREADY HELD EVERY ONE OF THOSE NUMBERS.  The same gate run over
# `segments.Fresh42.yaml` finds 550 impassable pairs in 8 of its 52 shared
# places.  So the disagreement is lifted to plan time, the way the POI
# stand-off and the hairpin were: every reading the build would take becomes
# a DIFFERENCE CONSTRAINT between two legs' stations, and every leg is solved
# at once, as one system (`_close_joint`, the multi-chain form of
# `_close_bounds`).
#
# THE DATUM RULE, DERIVED, NOT INHERITED FROM ORDER.  Of all the profiles
# that satisfy every constraint, take the GREATEST one never above the
# design and the LEAST one never below it, and average them -- exactly
# `join_branches`' rule for a line meeting itself, so a line meeting another
# line is treated the same way.  Where legs meet at one place with heights
# h_1..h_n and a tolerance c, that puts the highest at (min + max + c) / 2
# and the lowest at (min + max - c) / 2: the shared datum is the MIDPOINT of
# the participants' own design heights, the one height that minimises the
# largest move any participant makes, each moving the same distance.  A pad
# and a leg's own pinned stations (abutment level run, bank level run) do
# not move; the others meet them.  Every move is carried back along its own
# leg at that leg's own rung -- the shortest taper its grade allows -- and
# where no constraint binds the answer IS the design, byte for byte.  It is
# a fixed point of a set of difference constraints, not a sequence of edits,
# so it cannot depend on which leg was planned or built first.
#
# TWO TOLERANCES, IN ORDER.  STAGE ONE makes every reading pass its own
# instrument -- the slide allowance, which is what the build refuses on --
# climbing the grade ladder one leg at a time where no profile exists at the
# fitted rungs, and REFUSING a leg with its number where none exists at the
# ceiling.  STAGE TWO then pins legs that MEET to ONE DATUM: at those rungs,
# every junction-gate pair between two legs and every pad reading is held to
# its DESIGN BUDGET (`junction.budget_allow`, the legs' own grade over the
# same run -- c above), and where the legs' own bounds forbid that, to the
# nearest fraction of the way to the allowance they do allow, per place.
# The claim boundary (shoulders, batter) is the TRANSITION and keeps the
# allowance.
#
# WHY EACH TERM, AND THE KEY EACH WRONG SPELLING WOULD BE:
#   the_junction_gates_own_pairs   `junction.find_conflicts`' road/road
#       rows -- carriageways within NEAR_MISS_M, `slide_allow(gap)` -- read
#       at the gate's own feet.  The build refuses on exactly these; a solve
#       keyed on anything else can agree with itself and still be refused.
#   the_writers_claim_boundary_between_legs   the writer's nearest-claim
#       lattice across two legs (`ribbon.lateral_and_y_grid`), `|y1 - y2| -
#       grade * (run1 + lattice + run2)`: the two legs' paved SHOULDERS and
#       batters.  The gate's band is the carriageway; a bench is a metre
#       wider each side, so two legs whose shoulders touch share paved
#       ground the gate never reads -- a SUBSET.
#   pad_datums   a levelled rectangle is a participant that does not move;
#       without it the solve drags a road's end off the pad it arrives at.
#   every_participants_own_design_height   the rule above reads ALL of
#       them.  The neuter is the build's rule -- each leg pinned to the legs
#       planned before it -- and it makes the result depend on order.
#   each_legs_own_bounds_and_branches   the apply-clamp box, a deck's floor,
#       the level runs Crossings owns, and the leg's own self-adjacency
#       pairs.  Without them the solve buys agreement by moving a datum
#       somebody else owns.
JUNCTION_DATUM_DEPENDS_ON = (
    "the_junction_gates_own_pairs",
    "the_writers_claim_boundary_between_legs",
    "pad_datums",
    "every_participants_own_design_height",
    "each_legs_own_bounds_and_branches",
)
# The same margin, for the same reason: the writer reads `profile_y` to 1 mm
# and `nodes` to 1 cm, so a constraint met exactly in memory can be missed
# by rounding on disk.
JUNCTION_DATUM_MARGIN_M = SELF_ADJACENCY_JOIN_MARGIN_M
JUNCTION_DATUM_EPS_M = SELF_ADJACENCY_EPS_M
JUNCTION_DATUM_RULE = (
    "ONE DATUM where legs meet: the midpoint of the participants' own design "
    "heights (the average of the greatest profile never above each leg's "
    "design and the least one never below it -- each moving half the "
    "excess, the minimax move), every junction-gate pair between two legs "
    "held to its design budget, or the nearest fraction of the way to the "
    "slide allowance the legs' own bounds allow; the transition -- shoulders, "
    "batter, the taper along each leg at its own rung -- held to the slide "
    "allowance; pads and pinned stations do not move; unchanged wherever "
    "nothing binds")
# A constraint the design already meets is held only as far as the design
# holds it, so the solve moves stations BECAUSE of a disagreement and never
# because a conservative station bound is tighter than the exact reading.
# A reading the move then breaks is tightened and the solve re-run; this is
# how many times.
JUNCTION_DATUM_ROUNDS = 8

# JUNCTION_CONFLICT_DEPENDS_ON: A LEG IS CLIMBED OR REFUSED ONLY FOR A
# CONFLICT IT IS PART OF.
#
# Stage one used to climb and refuse inside `component(li)`: every leg joined
# to the one holding the empty station by ANY reading the design breaks.  It
# climbed the nearest leg there that could climb, and at the ceiling it refused
# one leg of the worst such reading ANYWHERE in the component.  Once every
# junction is read (the co-paved claim boundary, the node anchor, the arrival
# pads), that component is most of the network.  So one leg with no profile
# near its own pad cost the plan every leg that happened to disagree
# somewhere else.  MEASURED, Pirate68 provisional re-plan 2026-09-26
# (/tmp/rw2/pirate_final5.log): every refusal named the same empty station,
# T4-meadhall-stathub 754 [35.238, 34.794], 8.7 m of run from the
# complete-station-hub pad (datum 31.85; T4 stands at 36.28 there).  Stage one
# refused S13, S7, T3, T13, S5, T6, T5, S4, S2 and S8, each for the worst
# reading LEFT in the component (the temple, (505,1129), (1247,567), ...), and
# only then T4 itself.  Station 754 was empty after every one of those ten
# refusals, and on the way stage one climbed eight legs (T1, T9, T10, T11,
# T12, S3, S11, S14) to 0.25.  Re-solved under the rule below, the same
# dumped system refuses four legs, all at stathub (T4 and T6 against the pad,
# S2 and S7 against S13's empty start), and climbs two (S13, T13).
#
# THE RULE: the legs one step may climb or refuse form an IRREDUCIBLE
# conflict: a set of live legs whose readings together leave no profile, and
# which has a profile again if any one of them is removed.  It is grown from
# the leg holding the empty station outward along every live reading: the
# smallest breadth-first prefix with no profile, minus every member (except
# the prefix's last) whose removal still leaves no profile.  A leg outside the
# conflict cannot be the reason, because the conflict has no profile whatever
# that leg does.  The climb goes to the nearest member that can still climb.
# At the ceiling the refused leg is the member `_refuse_order` names, carrying
# the worst reading it breaks inside the conflict.
#   the_readings_that_leave_no_profile   only the legs whose constraints make
#       the closure empty; each one is proven needed by a closure without it.
#   nearest_first_from_the_empty_station   breadth-first along the readings,
#       so the conflict found is the one the empty station belongs to.
# NEUTER `_CONFLICT_SCOPE = "component"`: the shipped rule.  Proven by
# `network.selftest_junction_conflict`.
JUNCTION_CONFLICT_DEPENDS_ON = ("the_readings_that_leave_no_profile",
                                "nearest_first_from_the_empty_station")
_CONFLICT_SCOPE = "conflict"     # conflict | component (the shipped rule)


def _junction():
    """`roads/junction.py`, imported when first asked.  It PREPENDS ledger/
    and terraform/ to `sys.path` on import, which would change what every
    later bare import in the planner resolves to, so the path is restored."""
    saved = list(sys.path)
    try:
        import junction                                     # noqa: PLC0415
    finally:
        sys.path[:] = saved
    return junction


def _feet(arcs, a):
    """Station bracket `(k0, k1, t)` of arc position(s) `a` on `arcs`."""
    arcs = np.asarray(arcs, dtype=np.float64)
    a = np.asarray(a, dtype=np.float64)
    n = len(arcs)
    k0 = np.clip(np.searchsorted(arcs, a, side="right") - 1, 0, max(n - 2, 0))
    k1 = np.minimum(k0 + 1, n - 1)
    L = arcs[k1] - arcs[k0]
    t = np.where(L > 1e-12, np.clip((a - arcs[k0]) / np.where(L > 1e-12, L, 1.0),
                                    0.0, 1.0), 0.0)
    return k0.astype(np.int64), k1.astype(np.int64), t


# CO_PAVED_DEPENDS_ON: A SAMPLE TWO LEGS BOTH PAVE IS RE-PAVED BY WHICHEVER
# IS WRITTEN LATER, AT ITS OWN HEIGHT -- so every paver meets the ground
# beside it, not only the tie-break winner.
#
# `cross_claim_lattice` gave each sample ONE winner (least run, nearest
# centreline, lower index on a tie) and paired only winners.  The writer does
# not defer: its carriageway covers every sample within the bench edge.
# MEASURED, Fresh42 drive 4 step 4, S3-eastlobe-northeast refused 2 steps,
# worst 0.8262 m at rim (872,349) from taken (873,349), "plans
# road_S2_stathub_eastlobe#1846": the two legs share the run into eastlobe,
# (873,349) is paved by BOTH at lat 2.900 (S2 39.0489, S3 38.9572 -- 0.092
# apart, inside the junction gate), (872,349) by S2 alone (lat 2.758,
# 39.7834; S3's shoulder, lat 3.900).  The winner at both was S2 (tie ->
# lower index), so the datum saw only S2's own 0.7345 m edge and reported 0
# over; S3, written after, re-paved (873,349) and left 0.8262.  So every leg
# whose bench covers a sample is paired against the winner of each lattice
# neighbour, at run 0 on its side -- order-free, since either may be the
# later write.  NEUTER `_CROSS_CLAIM_MODE = "winner"` (the shipped key),
# proven by `network.selftest_co_paved`.
CO_PAVED_DEPENDS_ON = ("every_leg_whose_bench_covers_the_sample",
                       "the_winner_of_each_lattice_neighbour")
_CROSS_CLAIM_MODE = "every_paver"   # every_paver | winner (the shipped key)


def cross_claim_lattice(legs: list[dict], *, grade: float, batter_m: float,
                        lattice_m: float) -> dict:
    """Every 4-adjacent lattice edge whose two samples are claimed by two
    DIFFERENT legs, from the writer's own claim function.

    The winner at a sample is the leg standing least far past its own bench
    edge, then the nearest centreline -- `lateral_and_y_grid`'s rule within
    one line, extended across lines of different widths by the quantity the
    allowance is taken on.  Ties go to the lower index, and the caller hands
    the legs in canonical order, so the answer is order-free."""
    R = _writer()
    tc = R.tcdata
    per = []
    zone_legs: dict[tuple[int, int], list[int]] = {}
    for li, lg in enumerate(legs):
        nodes = np.asarray(lg["nodes"], dtype=np.float64).reshape(-1, 2)
        br = np.asarray(lg["is_bridge"], dtype=bool)
        h = footprint_half_m(float(lg["width_m"]))
        per.append((nodes, br, h, h + batter_m))
        if len(nodes) < 2:
            continue
        for z in R.segment_zones([list(p) for p in nodes], list(br),
                                 float(lg["width_m"]), batter_m=batter_m):
            zone_legs.setdefault(tuple(z), []).append(li)
    ax = (np.arange(tc.PITCH) - tc.PITCH // 2) * tc.SCALE
    cols = {k: [] for k in ("la", "s1", "run1", "lb", "s2", "run2", "x1",
                            "z1", "x2", "z2")}
    for zkey in sorted(zone_legs):
        lis = zone_legs[zkey]
        if len(lis) < 2:
            continue
        cx, cz = tc.zone_centre(*zkey)
        X = (cx + ax)[None, :] + np.zeros((tc.PITCH, 1))
        Z = (cz + ax)[:, None] + np.zeros((1, tc.PITCH))
        LAT = np.full((len(lis),) + X.shape, np.inf)
        S = np.zeros((len(lis),) + X.shape)
        RUN = np.full((len(lis),) + X.shape, np.inf)
        for n_, li in enumerate(lis):
            nodes, br, h, reach = per[li]
            lat, _y, s_ = R.lateral_and_y_grid(
                nodes, np.zeros(len(nodes)), br, X, Z, reach, grade, h)[:3]
            inb = lat <= reach
            LAT[n_] = np.where(inb, lat, np.inf)
            S[n_] = s_
            RUN[n_] = np.where(inb, claim_run_m(lat, h), np.inf)
        best_run = RUN.min(axis=0)
        win = np.argmin(np.where(RUN <= best_run[None] + 1e-9, LAT, np.inf),
                        axis=0)
        inb = np.isfinite(best_run)
        leg = np.asarray(lis)[win]
        s_w = np.take_along_axis(S, win[None], 0)[0]
        r_w = np.where(inb, best_run, 0.0)
        for a, b in (((slice(None), slice(None, -1)),
                      (slice(None), slice(1, None))),
                     ((slice(None, -1), slice(None)),
                      (slice(1, None), slice(None)))):
            m = inb[a] & inb[b] & (leg[a] != leg[b])
            if m.any():
                for k, src in (("la", leg[a]), ("s1", s_w[a]),
                               ("run1", r_w[a]), ("lb", leg[b]),
                               ("s2", s_w[b]), ("run2", r_w[b]),
                               ("x1", X[a]), ("z1", Z[a]), ("x2", X[b]),
                               ("z2", Z[b])):
                    cols[k].append(src[m])
            if _CROSS_CLAIM_MODE != "every_paver":
                continue
            # EVERY LEG THAT PAVES A SAMPLE, against the winner beside it:
            # the writer lets whichever leg is written later re-pave a
            # co-paved sample at its own height.  See CO_PAVED_DEPENDS_ON.
            for n_, li in enumerate(lis):
                paves = RUN[n_] <= 1e-9
                for p, q in ((a, b), (b, a)):
                    mm = paves[p] & inb[q] & (leg[q] != li) & (leg[p] != li)
                    if not mm.any():
                        continue
                    cols["la"].append(np.full(int(mm.sum()), li))
                    cols["s1"].append(S[n_][p][mm])
                    cols["run1"].append(np.zeros(int(mm.sum())))
                    cols["lb"].append(leg[q][mm])
                    cols["s2"].append(s_w[q][mm])
                    cols["run2"].append(r_w[q][mm])
                    cols["x1"].append(X[p][mm])
                    cols["z1"].append(Z[p][mm])
                    cols["x2"].append(X[q][mm])
                    cols["z2"].append(Z[q][mm])
    e = {k: (np.concatenate(v) if v else np.zeros(0)) for k, v in cols.items()}
    if len(e["s1"]):
        key = np.stack([np.rint(e[k]).astype(np.int64)
                        for k in ("x1", "z1", "x2", "z2", "la", "lb")], axis=1)
        _u, first = np.unique(key, axis=0, return_index=True)
        e = {k: v[np.sort(first)] for k, v in e.items()}
    e["la"] = e["la"].astype(np.int64)
    e["lb"] = e["lb"].astype(np.int64)
    return e


def _close_joint(lo: np.ndarray, hi: np.ndarray, chains: list,
                 I: np.ndarray, J: np.ndarray, allow: np.ndarray,
                 ) -> tuple[np.ndarray, np.ndarray, bool]:
    """`_close_bounds` over SEVERAL chains at once.

    `chains` is `[(a, b, C)]`: stations `a:b` of the stacked vector are one
    leg, `C` its cumulative reach.  A chain closes on its own slice only --
    two legs are joined by their pair limits and by nothing else -- and each
    slice is closed by the same running-minimum arithmetic `_close_bounds`
    uses, on that leg's own numbers, so the result is independent of where
    in the stack a leg sits."""
    lo = np.asarray(lo, dtype=np.float64).copy()
    hi = np.asarray(hi, dtype=np.float64).copy()
    for _ in range(4 * len(lo) + 16):
        for a, b, C in chains:
            h, l = hi[a:b], lo[a:b]
            h = np.minimum(h, C + np.minimum.accumulate(h - C))
            h = np.minimum(h, np.minimum.accumulate((h + C)[::-1])[::-1] - C)
            l = np.maximum(l, np.maximum.accumulate(l + C) - C)
            l = np.maximum(l, np.maximum.accumulate((l - C)[::-1])[::-1] + C)
            hi[a:b], lo[a:b] = h, l
        if (lo > hi + 1e-9).any():
            return lo, hi, False
        if not len(I):
            return lo, hi, True
        h2, l2 = hi.copy(), lo.copy()
        np.minimum.at(h2, J, hi[I] + allow)
        np.minimum.at(h2, I, hi[J] + allow)
        np.maximum.at(l2, J, lo[I] - allow)
        np.maximum.at(l2, I, lo[J] - allow)
        if (np.abs(h2 - hi).max() <= 1e-12
                and np.abs(l2 - lo).max() <= 1e-12):
            return lo, hi, True
        hi, lo = h2, l2
    return lo, hi, bool((lo <= hi + 1e-9).all())


def _joint_readings(legs: list[dict], pads: list[dict], depends_on: tuple, *,
                    grade: float, batter_m: float, lattice_m: float) -> dict:
    """Every reading the solve must satisfy, as FEET on the legs' stations.

    A foot is `(leg, k0, k1, t)`: the height is `y[k0] + t * (y[k1] -
    y[k0])`, which is exactly how the gate and the writer read it.  Pair
    readings carry the allowance their own instrument applies; pad readings
    carry the datum."""
    J = _junction()
    index = {lg["id"]: li for li, lg in enumerate(legs)}
    P = {k: [] for k in ("la", "k0a", "k1a", "ta", "lb", "k0b", "k1b", "tb",
                         "allow", "kind", "x", "z")}
    U = {k: [] for k in ("la", "k0a", "k1a", "ta", "datum", "allow", "pad",
                         "x", "z")}

    def put(dst, **kw):
        for k, v in kw.items():
            dst[k].append(v)

    gate = ("the_junction_gates_own_pairs" in depends_on
            or "pad_datums" in depends_on)
    if gate:
        doc = {"segments": [{
            "id": lg["id"], "nodes": lg["nodes"], "profile_y": list(lg["y0"]),
            "is_bridge": list(lg["is_bridge"]), "width_m": lg["width_m"],
            "grade_limit": lg["rung"]} for lg in legs]}
        ribs = J.ribbons_from_segments(doc, "plan")
        by_label = {r["label"]: r for r in ribs}
        terms = tuple(t for t, on in (
            ("ribbon_design_heights",
             "the_junction_gates_own_pairs" in depends_on),
            ("pad_datums", "pad_datums" in depends_on)) if on)
        rows = J.find_conflicts(ribs, pads if "pad_datums" in depends_on
                                else [], terms)["pairs"]
        for r in rows:
            A = by_label[r["a"]]
            ka0, ka1, ta = _feet(A["arc"], r["a_foot_arc"])
            sa = A["station_index"]
            allow = float(r["slide_allow_m"])
            if r["b_kind"] == "pad":
                put(U, la=index[A["id"]], k0a=sa[int(ka0)], k1a=sa[int(ka1)],
                    ta=float(ta), datum=float(r["b_y"]), allow=allow,
                    pad=r["b"], x=r["xz"][0], z=r["xz"][1])
                continue
            B = by_label[r["b"]]
            kb0, kb1, tb = _feet(B["arc"], r["b_foot_arc"])
            sb = B["station_index"]
            put(P, la=index[A["id"]], k0a=sa[int(ka0)], k1a=sa[int(ka1)],
                ta=float(ta), lb=index[B["id"]], k0b=sb[int(kb0)],
                k1b=sb[int(kb1)], tb=float(tb), allow=allow, kind=0,
                x=r["xz"][0], z=r["xz"][1])
    if "the_writers_claim_boundary_between_legs" in depends_on:
        R = _writer()
        e = cross_claim_lattice(legs, grade=grade, batter_m=batter_m,
                                lattice_m=lattice_m)
        arcs = [np.asarray(R.node_arcs(lg["nodes"], lg["is_bridge"]))
                for lg in legs]
        for k in range(len(e["s1"])):
            la, lb = int(e["la"][k]), int(e["lb"][k])
            a0, a1, ta = _feet(arcs[la], e["s1"][k])
            b0, b1, tb = _feet(arcs[lb], e["s2"][k])
            put(P, la=la, k0a=int(a0), k1a=int(a1), ta=float(ta), lb=lb,
                k0b=int(b0), k1b=int(b1), tb=float(tb),
                allow=float(grade * (e["run1"][k] + e["run2"][k]
                                     + lattice_m)),
                kind=1, x=float(e["x1"][k]), z=float(e["z1"][k]))
    if "each_legs_own_bounds_and_branches" in depends_on:
        for li, lg in enumerate(legs):
            cand = lg.get("cand")
            if cand is None or not len(cand["edges"]["s1"]):
                continue
            ed = cand["edges"]
            a0, a1, ta = _feet(cand["arcs"], ed["s1"])
            b0, b1, tb = _feet(cand["arcs"], ed["s2"])
            al = -unabsorbable_m(0.0, ed["run1"] + ed["run2"],
                                 cand["grade"], cand["lattice_m"])
            for k in range(len(a0)):
                put(P, la=li, k0a=int(a0[k]), k1a=int(a1[k]),
                    ta=float(ta[k]), lb=li, k0b=int(b0[k]),
                    k1b=int(b1[k]), tb=float(tb[k]), allow=float(al[k]),
                    kind=2, x=float(ed["x1"][k]), z=float(ed["z1"][k]))
    ints = ("la", "k0a", "k1a", "lb", "k0b", "k1b", "kind")
    P = {k: np.asarray(v, dtype=np.int64 if k in ints else np.float64)
         for k, v in P.items()}
    U = {k: (list(v) if k == "pad" else np.asarray(
        v, dtype=np.int64 if k in ints else np.float64))
        for k, v in U.items()}
    return {"pairs": P, "pads": U}


def _values(Y: list[np.ndarray], leg, k0, k1, t) -> np.ndarray:
    """Heights at a vector of feet, off per-leg profiles."""
    out = np.empty(len(leg), dtype=np.float64)
    for li in np.unique(leg) if len(leg) else ():
        m = leg == li
        y = Y[int(li)]
        out[m] = y[k0[m]] + t[m] * (y[k1[m]] - y[k0[m]])
    return out


def _excess(Y, rd) -> tuple[np.ndarray, np.ndarray]:
    """How far past its own allowance every reading stands (positive =
    refused by the instrument it came from)."""
    P, U = rd["pairs"], rd["pads"]
    ea = (np.abs(_values(Y, P["la"], P["k0a"], P["k1a"], P["ta"])
                 - _values(Y, P["lb"], P["k0b"], P["k1b"], P["tb"]))
          - P["allow"]) if len(P["la"]) else np.zeros(0)
    eu = (np.abs(_values(Y, U["la"], U["k0a"], U["k1a"], U["ta"])
                 - U["datum"]) - U["allow"]) if len(U["la"]) else np.zeros(0)
    return ea, eu


def junction_datums(legs: list[dict], pads: list[dict], *, grade: float,
                    batter_m: float, lattice_m: float,
                    depends_on: tuple = JUNCTION_DATUM_DEPENDS_ON) -> dict:
    """Solve every leg's profile at once so legs that share ground agree on
    its height, or REFUSE the leg that cannot, with its number.

    `legs`: one dict per leg -- `id`, `cls`, `pri`, `width_m`, `nodes` (as
    emitted), `is_bridge`, `y0` (its own `fit_ladder` profile), `s` (the arc
    its chain limit is taken over), `lo`/`hi` (its own bounds), `spans`
    (terrain-infeasible spans the ladder carried), `cand` (its
    self-adjacency lattice), `rung` and `ladder`.  `pads`: junction-shaped
    pad participants with datums.

    Returns `{"y": {id: profile}, "rung": {id: rung}, "legs": {id:
    verdict}, "refused": [...], "readings": {...}}`.  The legs are taken in
    CANONICAL order (sorted by id), so the answer is the same whatever order
    they were handed in -- except under the
    `every_participants_own_design_height` neuter, which reproduces the
    build's rule (each leg pinned to the legs before it) and is therefore
    order-dependent by construction."""
    joint = "every_participants_own_design_height" in depends_on
    if not joint:
        return _junction_sequential(list(legs), pads, grade=grade,
                                    batter_m=batter_m, lattice_m=lattice_m,
                                    depends_on=depends_on)
    return _junction_joint(sorted(legs, key=lambda lg: lg["id"]), pads,
                           grade=grade, batter_m=batter_m,
                           lattice_m=lattice_m, depends_on=depends_on)


def _junction_sequential(legs, pads, *, grade, batter_m, lattice_m,
                         depends_on) -> dict:
    """THE NEUTER OF `every_participants_own_design_height`: the build's
    rule.  Each leg, in the order handed, is solved against the legs before
    it with THEIR profiles fixed -- the first one written fixes the ground
    and every later one meets it."""
    fixed: dict[str, np.ndarray] = {}
    out = {"y": {}, "rung": {}, "legs": {}, "refused": []}
    for k, lg in enumerate(legs):
        done = [dict(p, y0=fixed[p["id"]], lo=fixed[p["id"]],
                     hi=fixed[p["id"]], spans=[], ladder=[p["rung"]],
                     pinned=True)
                for p in legs[:k] if p["id"] in fixed]
        r = _junction_joint(sorted(done + [lg], key=lambda q: q["id"]), pads,
                            grade=grade, batter_m=batter_m,
                            lattice_m=lattice_m, depends_on=depends_on)
        if lg["id"] in r["y"]:
            fixed[lg["id"]] = r["y"][lg["id"]]
            out["y"][lg["id"]] = r["y"][lg["id"]]
            out["rung"][lg["id"]] = r["rung"][lg["id"]]
            out["legs"][lg["id"]] = r["legs"][lg["id"]]
        out["refused"] += [q for q in r["refused"] if q["id"] == lg["id"]]
    out["readings"] = None
    return out


def _refuse_order(lg: dict) -> tuple:
    """Which of two irreconcilable legs is refused: the lower priority
    (larger `pri`), then the narrower (a spur before a trunk), then the
    larger id -- never the one that happened to be planned second."""
    return (int(lg.get("pri") or 0), -float(lg["width_m"]), lg["id"])


def _junction_joint(legs, pads, *, grade, batter_m, lattice_m,
                    depends_on) -> dict:
    own = "each_legs_own_bounds_and_branches" in depends_on
    rd = _joint_readings(legs, pads, depends_on, grade=grade,
                         batter_m=batter_m, lattice_m=lattice_m)
    P, U = rd["pairs"], rd["pads"]
    n = [len(lg["y0"]) for lg in legs]
    off = np.concatenate([[0], np.cumsum(n)]).astype(np.int64)
    Y0 = [np.asarray(lg["y0"], dtype=np.float64) for lg in legs]
    Y0r = [np.round(y, 3) for y in Y0]
    rung = [float(lg["rung"]) for lg in legs]
    alive = [True] * len(legs)
    refused: list[dict] = []
    climbs: dict[int, list[float]] = {}
    ea0, eu0 = _excess(Y0r, rd)

    def near(leg, k0, k1, t):
        """Nearest station of each foot, and its distance along the chain's
        own arc -- the slack the station form gives up to the chain."""
        nst = np.where(t <= 0.5, k0, k1)
        ds = np.empty(len(leg), dtype=np.float64)
        for li in np.unique(leg) if len(leg) else ():
            m = leg == li
            s = np.asarray(legs[int(li)]["s"], dtype=np.float64)
            ds[m] = (s[k1[m]] - s[k0[m]]) * np.minimum(t[m], 1.0 - t[m])
        return nst, ds

    na, da = near(P["la"], P["k0a"], P["k1a"], P["ta"])
    nb, db = near(P["lb"], P["k0b"], P["k1b"], P["tb"])
    nu, du = near(U["la"], U["k0a"], U["k1a"], U["ta"])
    ga = off[P["la"]] + na
    gb = off[P["lb"]] + nb
    gu = off[U["la"]] + nu
    y0 = np.concatenate(Y0) if Y0 else np.zeros(0)
    tight_p = ea0 > JUNCTION_DATUM_EPS_M
    tight_u = eu0 > JUNCTION_DATUM_EPS_M

    def own_bounds(li):
        lg = legs[li]
        y = Y0[li]
        if lg.get("pinned"):
            return y.copy(), y.copy()
        if not own:
            return np.full(len(y), -1e6), np.full(len(y), 1e6)
        lo = np.minimum(np.asarray(lg["lo"], dtype=np.float64), y)
        hi = np.maximum(np.asarray(lg["hi"], dtype=np.float64), y)
        for a, b in lg.get("spans") or ():
            lo[a:b + 1] = -1e6
            hi[a:b + 1] = 1e6
        return lo, hi

    # THE DESIGN BUDGET OF EVERY READING, i.e. what "ONE DATUM" means for
    # it: the gate's own tier 2 (`junction.budget_allow`, the legs' declared
    # grade over the same run) for a gate pair between two legs, the same
    # grade across the one lattice edge between two benches for a claim
    # boundary (the batter runs either side keep the writer's own grade),
    # and the road's grade for a pad.  A leg's own branches are not a
    # junction and keep their allowance.  Filled once the rungs are final.
    budget_p = P["allow"].copy()
    budget_u = U["allow"].copy()

    def attempt(tp, tu, lp, lu, closure_only=False):
        r = np.asarray(rung)
        live_p = np.asarray([alive[a] and alive[b]
                             for a, b in zip(P["la"], P["lb"])], dtype=bool)
        live_u = np.asarray([alive[a] for a in U["la"]], dtype=bool)
        tgt = budget_p + lp * (P["allow"] - budget_p)
        allow_s = (np.maximum(tgt - JUNCTION_DATUM_MARGIN_M
                              - r[P["la"]] * da - r[P["lb"]] * db, 0.0)
                   if len(P["la"]) else np.zeros(0))
        held = np.abs(y0[ga] - y0[gb]) if len(ga) else np.zeros(0)
        allow_e = np.where(tp, allow_s, np.maximum(allow_s, held))
        keep = live_p & (ga != gb)
        lo = np.empty(off[-1])
        hi = np.empty(off[-1])
        chains = []
        for li, lg in enumerate(legs):
            a, b = int(off[li]), int(off[li + 1])
            lo[a:b], hi[a:b] = own_bounds(li)
            if not alive[li]:
                lo[a:b] = hi[a:b] = Y0[li]
            reach = rung[li] * np.diff(np.asarray(lg["s"], dtype=np.float64))
            chains.append((a, b, np.concatenate([[0.0], np.cumsum(reach)])))
        if len(gu):
            tgu = budget_u + lu * (U["allow"] - budget_u)
            au = np.maximum(tgu - JUNCTION_DATUM_MARGIN_M - r[U["la"]] * du,
                            0.0)
            ulo = np.where(tu, U["datum"] - au,
                           np.minimum(U["datum"] - au, y0[gu]))
            uhi = np.where(tu, U["datum"] + au,
                           np.maximum(U["datum"] + au, y0[gu]))
            np.maximum.at(lo, gu[live_u], ulo[live_u])
            np.minimum.at(hi, gu[live_u], uhi[live_u])
        I = np.minimum(ga, gb)[keep]
        Jn = np.maximum(ga, gb)[keep]
        A = allow_e[keep]
        if len(I):
            order = np.lexsort((A, Jn, I))
            I, Jn, A = I[order], Jn[order], A[order]
            first = np.ones(len(I), dtype=bool)
            first[1:] = (I[1:] != I[:-1]) | (Jn[1:] != Jn[:-1])
            I, Jn, A = I[first], Jn[first], A[first]
        L, Uu, ok = _close_joint(lo, hi, chains, I, Jn, A)
        if not ok:
            empty = np.nonzero(L > Uu + 1e-9)[0]
            return None, int(empty[0]) if len(empty) else -1, (L, Uu)
        if closure_only:
            return True, -1, None
        _l, Aup, _ok = _close_joint(L, np.minimum(Uu, np.maximum(y0, L)),
                                    chains, I, Jn, A)
        Blo, _u, _ok = _close_joint(np.maximum(L, np.minimum(y0, Uu)), Uu,
                                    chains, I, Jn, A)
        y = 0.5 * (Aup + Blo)
        y = np.where(np.abs(y - y0) <= 1e-9, y0, y)
        return y, -1, (L, Uu)

    def leg_of(g):
        return int(np.searchsorted(off, g, side="right") - 1)

    def component(li):
        """Legs joined to `li` by a reading the design breaks, NEAREST
        FIRST: breadth-first from `li`, neighbours in canonical order."""
        adj: dict[int, set[int]] = {}
        for a, b, t in zip(P["la"], P["lb"], tight_p):
            if t and a != b and alive[a] and alive[b]:
                adj.setdefault(int(a), set()).add(int(b))
                adj.setdefault(int(b), set()).add(int(a))
        seen, order, k = {li}, [li], 0
        while k < len(order):
            for q in sorted(adj.get(order[k], ())):
                if q not in seen:
                    seen.add(q)
                    order.append(q)
            k += 1
        return order

    def closes(members, tp, tu, lp, lu) -> bool:
        """Does the system restricted to the live legs in `members` -- every
        other leg and every reading it takes part in set aside -- have a
        profile?  `attempt`'s own closure, nothing else."""
        saved = list(alive)
        keep = set(members)
        for q in range(len(legs)):
            alive[q] = saved[q] and q in keep
        try:
            return attempt(tp, tu, lp, lu, closure_only=True)[0] is not None
        finally:
            alive[:] = saved

    def conflict(li, tp, tu, lp, lu):
        """THE LEGS THIS EMPTY STATION IS OWED TO (JUNCTION_CONFLICT_
        DEPENDS_ON): an irreducible set of live legs whose readings together
        leave no profile -- remove any one and the rest close -- grown from
        `li` outward along every live reading, NEAREST FIRST.  The smallest
        breadth-first prefix that has no profile, then every member but its
        last that the others still fail without, dropped."""
        if _CONFLICT_SCOPE != "conflict":
            return component(li)
        adj: dict[int, set[int]] = {}
        for a, b in zip(P["la"], P["lb"]):
            if a != b and alive[a] and alive[b]:
                adj.setdefault(int(a), set()).add(int(b))
                adj.setdefault(int(b), set()).add(int(a))
        order, seen, k = [li], {li}, 0
        while k < len(order):
            for q in sorted(adj.get(order[k], ())):
                if q not in seen:
                    seen.add(q)
                    order.append(q)
            k += 1
        if closes(order, tp, tu, lp, lu):
            return component(li)
        lo_k, hi_k = 0, len(order)
        while hi_k - lo_k > 1:
            mid = (lo_k + hi_k) // 2
            if closes(order[:mid], tp, tu, lp, lu):
                lo_k = mid
            else:
                hi_k = mid
        core = list(order[:hi_k])
        for q in reversed(order[:hi_k - 1]):
            trial = [p for p in core if p != q]
            if not closes(trial, tp, tu, lp, lu):
                core = trial
        return core

    def refuse(li, comp, bad, bounds, tp, tu):
        """THE REFUSAL, WITH ITS NUMBER.  Under the conflict scope the leg
        is the member of the conflict `_refuse_order` names, and its number
        the worst reading it breaks there (with another member, else with a
        pad); under the component scope, the worst reading the design breaks
        anywhere in the component names the two legs, and of those two
        `_refuse_order` picks the leg."""
        cand = [k for k in range(len(ea0)) if tp[k]
                and P["la"][k] != P["lb"][k]
                and int(P["la"][k]) in comp and int(P["lb"][k]) in comp
                and alive[P["la"][k]] and alive[P["lb"][k]]]
        candu = [k for k in range(len(eu0)) if tu[k]
                 and int(U["la"][k]) in comp and alive[U["la"][k]]]
        L, Uu = bounds
        k_empty = bad - int(off[li]) if bad >= 0 else None
        chosen = None
        if _CONFLICT_SCOPE == "conflict":
            chosen = max(comp, key=lambda q: (not legs[q].get("pinned"),
                                              _refuse_order(legs[q])))
            mine = [k for k in cand if chosen in (int(P["la"][k]),
                                                  int(P["lb"][k]))]
            mine_u = [k for k in candu if int(U["la"][k]) == chosen]
            if mine:
                cand, candu = mine, []
            elif mine_u:
                cand, candu = [], mine_u
        if cand:
            k = max(cand, key=lambda k: ea0[k])
            a, b = int(P["la"][k]), int(P["lb"][k])
            victim = (chosen if chosen is not None else
                      max((a, b), key=lambda q: (
                          not legs[q].get("pinned"), _refuse_order(legs[q]))))
            other = a if victim == b else b
            va = float(_values(Y0r, P["la"][k:k + 1], P["k0a"][k:k + 1],
                               P["k1a"][k:k + 1], P["ta"][k:k + 1])[0])
            vb = float(_values(Y0r, P["lb"][k:k + 1], P["k0b"][k:k + 1],
                               P["k1b"][k:k + 1], P["tb"][k:k + 1])[0])
            binding = {"segments": [legs[a]["id"], legs[b]["id"]],
                       "with": legs[other]["id"],
                       "xz": [round(float(P["x"][k]), 2),
                              round(float(P["z"][k]), 2)],
                       "design_y": [round(va, 3), round(vb, 3)],
                       "spread_m": round(abs(va - vb), 3),
                       "allowance_m": round(float(P["allow"][k]), 4),
                       "reading": ("junction gate pair"
                                   if P["kind"][k] == 0 else
                                   "claim boundary")}
        elif candu:
            k = max(candu, key=lambda k: eu0[k])
            victim = int(U["la"][k]) if chosen is None else chosen
            va = float(_values(Y0r, U["la"][k:k + 1], U["k0a"][k:k + 1],
                               U["k1a"][k:k + 1], U["ta"][k:k + 1])[0])
            binding = {"segments": [legs[int(U["la"][k])]["id"], U["pad"][k]],
                       "with": U["pad"][k],
                       "xz": [round(float(U["x"][k]), 2),
                              round(float(U["z"][k]), 2)],
                       "design_y": [round(va, 3),
                                    round(float(U["datum"][k]), 3)],
                       "spread_m": round(abs(va - float(U["datum"][k])), 3),
                       "allowance_m": round(float(U["allow"][k]), 4),
                       "reading": "pad datum"}
        else:
            victim = li if chosen is None else chosen
            binding = {"segments": [legs[victim]["id"]], "with": None,
                       "xz": None, "reading": "own bounds"}
        binding.update({
            "rungs_tried": {legs[q]["id"]: climbs.get(q, []) + [rung[q]]
                            for q in comp},
            "empty_station": ([legs[li]["id"], k_empty]
                              if k_empty is not None else None),
            "interval_there": ([round(float(L[bad]), 3),
                                round(float(Uu[bad]), 3)]
                               if bad >= 0 else None)})
        refused.append({"id": legs[victim]["id"], "binding": binding})
        alive[victim] = False

    def solve(tp, tu, lp, lu, climb: bool):
        """Solve, and CHECK: every reading on the profile AS EMITTED, by its
        own instrument's allowance.  One the move broke is tightened (in
        `tp`/`tu`, in place) and the system solved again.  With `climb`, a
        system no profile satisfies climbs the ladder and at the ceiling
        refuses a leg; without it, it answers None."""
        tightened = 0
        while True:
            y, bad, bounds = attempt(tp, tu, lp, lu)
            if y is None:
                if not climb:
                    return None
                # NO PROFILE AT THESE RUNGS.  ONE leg climbs one rung -- the
                # leg holding the empty station if it can, else the nearest
                # leg it is joined to that can -- and the system is solved
                # again.  Every other leg keeps the rung it was fitted at: a
                # relaxation is a cost the operator walks, so it is paid
                # where the proof says it is needed and nowhere else.  At
                # the ceiling, refuse.
                li = leg_of(bad) if bad >= 0 else 0
                comp = conflict(li, tp, tu, lp, lu)
                for q in comp:
                    up = [g for g in legs[q].get("ladder") or ()
                          if g > rung[q]]
                    if up:
                        climbs.setdefault(q, []).append(rung[q])
                        rung[q] = min(up)
                        break
                else:
                    refuse(li, comp, bad, bounds, tp, tu)
                continue
            Yr = [np.round(y[off[li]:off[li + 1]], 3)
                  for li in range(len(legs))]
            ea, eu = _excess(Yr, rd)
            live_p = np.asarray([alive[a] and alive[b]
                                 for a, b in zip(P["la"], P["lb"])],
                                dtype=bool)
            live_u = np.asarray([alive[a] for a in U["la"]], dtype=bool)
            over_p = (ea > JUNCTION_DATUM_EPS_M) & live_p
            over_u = (eu > JUNCTION_DATUM_EPS_M) & live_u
            if not (over_p.any() or over_u.any()):
                return y
            new_p, new_u = over_p & ~tp, over_u & ~tu
            if not (new_p.any() or new_u.any()) \
                    or tightened >= JUNCTION_DATUM_ROUNDS:
                if not climb:
                    return None
                raise ValueError(
                    "junction datum solve did not close: "
                    f"{int(over_p.sum())} pair reading(s) and "
                    f"{int(over_u.sum())} pad reading(s) still over their "
                    f"allowance after {tightened} tightening round(s). "
                    f"UNREAD IS NOT CLEAR: the plan is not emitted on a "
                    f"profile its own gate would refuse.")
            tp |= new_p
            tu |= new_u
            tightened += 1

    # STAGE ONE -- AGREEMENT, the hard part: every reading inside its own
    # instrument's allowance, climbing the ladder and refusing where it must.
    ones_p, ones_u = np.ones(len(P["la"])), np.ones(len(U["la"]))
    y1 = solve(tight_p, tight_u, ones_p, ones_u, climb=True)

    # STAGE TWO -- ONE DATUM.  At the rungs stage one settled, where two legs
    # MEET -- the junction gate's own pairs between two legs, and a leg
    # meeting a pad -- every reading the design leaves over its DESIGN
    # BUDGET is held to that budget: one datum, within the legs' declared
    # grade over one lattice edge.  Where the legs' own bounds forbid it, as
    # near to it as they allow: per PLACE, the smallest fraction `lambda` of
    # the way from the budget to the allowance that is feasible.  A place is
    # a connected run of occupied 6 m cells (the gate's own cluster radius)
    # holding such readings -- a junction, or a corridor two legs share end
    # to end.  The claim boundary (shoulders, batter) keeps the allowance:
    # it is the TRANSITION, and the transition is held to the allowance.
    # Places are taken in canonical order and each keeps what it got, so a
    # hard place costs only itself.  At lambda 1 every reading is exactly
    # stage one's, which is feasible, and the constraint set only tightens
    # as lambda falls, so the bisection is sound.
    r_ = np.asarray(rung)
    cross = P["la"] != P["lb"]
    meet = cross & (P["kind"] == 0)
    if len(P["la"]):
        rmin = np.minimum(r_[P["la"]], r_[P["lb"]])
        budget_p[meet] = rmin[meet] * P["allow"][meet] / grade
    if len(U["la"]):
        budget_u[:] = r_[U["la"]] * U["allow"] / grade
    over_bp = meet & (ea0 + P["allow"] > budget_p + JUNCTION_DATUM_EPS_M)
    over_bu = eu0 + U["allow"] > budget_u + JUNCTION_DATUM_EPS_M
    live_p = np.asarray([alive[a] and alive[b]
                         for a, b in zip(P["la"], P["lb"])], dtype=bool)
    live_u = np.asarray([alive[a] and not legs[a].get("pinned")
                         for a in U["la"]], dtype=bool)
    # EVERY meeting reading takes part, not only those the design leaves
    # over budget: one already at one datum is held at the difference the
    # design gave it (never looser than that), so resolving a neighbour
    # cannot walk a flush junction off its datum.
    part_p = meet & live_p & ~np.asarray(
        [bool(legs[a].get("pinned")) and bool(legs[b].get("pinned"))
         for a, b in zip(P["la"], P["lb"])], dtype=bool)
    part_u = live_u
    CELL = 6.0
    cells: dict[tuple[int, int], list] = {}
    for k in np.nonzero(part_p)[0]:
        cells.setdefault((int(math.floor(P["x"][k] / CELL)),
                          int(math.floor(P["z"][k] / CELL))), []).append(
            ("p", int(k)))
    for k in np.nonzero(part_u)[0]:
        cells.setdefault((int(math.floor(U["x"][k] / CELL)),
                          int(math.floor(U["z"][k] / CELL))), []).append(
            ("u", int(k)))
    seen_c: set = set()
    places: list[list] = []
    for c0 in sorted(cells):
        if c0 in seen_c:
            continue
        seen_c.add(c0)
        todo, members = [c0], []
        while todo:
            c = todo.pop()
            members += cells[c]
            for dx in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    q = (c[0] + dx, c[1] + dz)
                    if q in cells and q not in seen_c:
                        seen_c.add(q)
                        todo.append(q)
        places.append(members)
    lam_p = np.ones(len(P["la"]))
    lam_u = np.ones(len(U["la"]))
    place_lam: list[float] = []

    def modes():
        return (tight_p | (over_bp & (lam_p < 1.0)),
                tight_u | (over_bu & (lam_u < 1.0)), lam_p, lam_u)

    def feasible():
        return attempt(*modes(), closure_only=True)[0] is not None

    def set_lam(members, v):
        for kind, k in members:
            (lam_p if kind == "p" else lam_u)[k] = v
    for members in places:
        set_lam(members, 0.0)
    if not feasible():
        for members in places:
            set_lam(members, 1.0)
        for members in places:
            set_lam(members, 0.0)
            if feasible():
                continue
            lo_l, hi_l = 0.0, 1.0
            for _ in range(8):
                mid = 0.5 * (lo_l + hi_l)
                set_lam(members, mid)
                if feasible():
                    hi_l = mid
                else:
                    lo_l = mid
            set_lam(members, hi_l)
    tp2, tu2, lp2, lu2 = modes()
    y = solve(tp2.copy(), tu2.copy(), lp2, lu2, climb=False)
    if y is None:
        lam_p[:] = 1.0
        lam_u[:] = 1.0
        y = y1
    for members in places:
        place_lam.append(max((lam_p if kd == "p" else lam_u)[k]
                             for kd, k in members))
    Yout = [y[off[li]:off[li + 1]] for li in range(len(legs))]
    Yr = [np.round(v, 3) for v in Yout]
    ea, eu = _excess(Yr, rd)
    out = {"y": {}, "rung": {}, "legs": {}, "refused": refused,
           "readings": {"pairs": int(len(P["la"])),
                        "gate_pairs": int((P["kind"] == 0).sum()),
                        "claim_boundary_edges": int((P["kind"] == 1).sum()),
                        "self_adjacency_edges": int((P["kind"] == 2).sum()),
                        "pad_readings": int(len(U["la"]))}}
    live_pf = np.asarray([alive[a] and alive[b]
                          for a, b in zip(P["la"], P["lb"])], dtype=bool)
    # THE POPULATION BY INSTRUMENT, before and after, so a reader can see
    # which reading moved and none can be quoted for another.
    pop = {}
    for name, sel in (
            ("junction_gate_pairs_between_legs",
             (P["kind"] == 0) & (P["la"] != P["lb"])),
            ("junction_gate_pairs_within_a_leg",
             (P["kind"] == 0) & (P["la"] == P["lb"])),
            ("claim_boundary_edges_between_legs", P["kind"] == 1),
            ("own_branch_edges", P["kind"] == 2)):
        bex0 = ea0 + P["allow"] - budget_p
        bex1 = ea + P["allow"] - budget_p
        pop[name] = {
            "readings": int(sel.sum()),
            "over_before": int((ea0[sel] > JUNCTION_DATUM_EPS_M).sum()),
            "over_after": int((ea[sel & live_pf]
                               > JUNCTION_DATUM_EPS_M).sum()),
            "worst_excess_before_m": (round(float(ea0[sel].max()), 3)
                                      if sel.any() else None),
            "worst_excess_after_m": (round(float(ea[sel & live_pf].max()), 3)
                                     if (sel & live_pf).any() else None),
            # NOT AT ONE DATUM: over the design budget (see `budget_p`).
            "over_design_budget_before": int(
                (bex0[sel] > JUNCTION_DATUM_EPS_M).sum()),
            "over_design_budget_after": int(
                (bex1[sel & live_pf] > JUNCTION_DATUM_EPS_M).sum())}
    live_uf = np.asarray([alive[a] for a in U["la"]], dtype=bool)
    pop["pad_readings"] = {
        "readings": int(len(U["la"])),
        "over_before": int((eu0 > JUNCTION_DATUM_EPS_M).sum()),
        "over_after": (int((eu[live_uf] > JUNCTION_DATUM_EPS_M).sum())
                       if len(eu) else 0),
        "over_design_budget_before": int(
            ((eu0 + U["allow"] - budget_u) > JUNCTION_DATUM_EPS_M).sum()),
        "over_design_budget_after": (int(
            ((eu + U["allow"] - budget_u)[live_uf]
             > JUNCTION_DATUM_EPS_M).sum()) if len(eu) else 0)}
    out["population"] = pop
    out["places"] = {
        "count": len(places),
        "at_one_datum": sum(1 for v in place_lam if v == 0.0),
        "nearer_than_the_allowance": sum(1 for v in place_lam
                                         if 0.0 < v < 1.0),
        "at_the_allowance": sum(1 for v in place_lam if v >= 1.0),
        "place_is": "a connected run of occupied 6 m cells holding readings "
                    "between two legs or a leg and a pad",
        "lambda_worst": [
            {"at": [round(float(min((P if kd == "p" else U)["x"][k]
                                    for kd, k in mem)), 1),
                    round(float(min((P if kd == "p" else U)["z"][k]
                                    for kd, k in mem)), 1)],
             "lambda": round(float(v), 4),
             "legs": sorted({legs[int(P["la"][k])]["id"] for kd, k in mem
                             if kd == "p"}
                            | {legs[int(P["lb"][k])]["id"] for kd, k in mem
                               if kd == "p"}
                            | {U["pad"][k] for kd, k in mem if kd == "u"})}
            for v, mem in sorted(zip(place_lam, places),
                                 key=lambda q: -q[0])[:12] if v > 0.0],
    }
    for li, lg in enumerate(legs):
        if not alive[li] or lg.get("pinned"):
            continue
        cross = (((P["la"] == li) | (P["lb"] == li)) & (P["la"] != P["lb"])
                 & live_pf)
        mine_u = U["la"] == li
        others = sorted({legs[int(q)]["id"] for q in
                         np.concatenate([P["la"][cross], P["lb"][cross]])
                         if int(q) != li}
                        | {U["pad"][k] for k in np.nonzero(mine_u)[0]})
        mine = cross | ((P["la"] == li) & (P["lb"] == li))
        mv = np.abs(Yout[li] - Y0[li])
        moved = int((mv > 1e-9).sum())

        def worst(e, sel_p, sel_u, Y):
            best = None
            for k in np.nonzero(sel_p)[0]:
                if best is None or e[0][k] > best[0]:
                    best = (float(e[0][k]), "p", int(k))
            for k in np.nonzero(sel_u)[0]:
                if best is None or e[1][k] > best[0]:
                    best = (float(e[1][k]), "u", int(k))
            if best is None:
                return None
            ex, kind, k = best
            if kind == "p":
                a, b = int(P["la"][k]), int(P["lb"][k])
                va = float(_values(Y, P["la"][k:k + 1], P["k0a"][k:k + 1],
                                   P["k1a"][k:k + 1], P["ta"][k:k + 1])[0])
                vb = float(_values(Y, P["lb"][k:k + 1], P["k0b"][k:k + 1],
                                   P["k1b"][k:k + 1], P["tb"][k:k + 1])[0])
                return {"with": [legs[a]["id"], legs[b]["id"]],
                        "xz": [round(float(P["x"][k]), 2),
                               round(float(P["z"][k]), 2)],
                        "design_y": [round(va, 3), round(vb, 3)],
                        "spread_m": round(abs(va - vb), 3),
                        "allowance_m": round(float(P["allow"][k]), 4),
                        "excess_m": round(ex, 3),
                        "reading": ("junction gate pair" if P["kind"][k] == 0
                                    else "claim boundary" if P["kind"][k] == 1
                                    else "own branches")}
            va = float(_values(Y, U["la"][k:k + 1], U["k0a"][k:k + 1],
                               U["k1a"][k:k + 1], U["ta"][k:k + 1])[0])
            return {"with": [lg["id"], U["pad"][k]],
                    "xz": [round(float(U["x"][k]), 2),
                           round(float(U["z"][k]), 2)],
                    "design_y": [round(va, 3), round(float(U["datum"][k]), 3)],
                    "spread_m": round(abs(va - float(U["datum"][k])), 3),
                    "allowance_m": round(float(U["allow"][k]), 4),
                    "excess_m": round(ex, 3), "reading": "pad datum"}

        over_b = int((ea0[mine] > JUNCTION_DATUM_EPS_M).sum()
                     + (eu0[mine_u] > JUNCTION_DATUM_EPS_M).sum())
        over_a = int((ea[mine] > JUNCTION_DATUM_EPS_M).sum()
                     + (eu[mine_u] > JUNCTION_DATUM_EPS_M).sum())
        # HOW FAR FROM ONE DATUM: readings between this leg and another (or
        # a pad) over their design budget, before and after.
        eb0 = np.concatenate([(ea0 + P["allow"] - budget_p)[cross],
                              (eu0 + U["allow"] - budget_u)[mine_u]])
        eb1 = np.concatenate([(ea + P["allow"] - budget_p)[cross],
                              (eu + U["allow"] - budget_u)[mine_u]])
        bud_b = int((eb0 > JUNCTION_DATUM_EPS_M).sum())
        bud_a = int((eb1 > JUNCTION_DATUM_EPS_M).sum())
        # THE DATUM THIS LEG WAS HELD TO where it meets: the lambda of the
        # meeting readings the design left over their budget.
        lam_mine = ([lam_p[k] for k in np.nonzero(cross & over_bp)[0]]
                    + [lam_u[k] for k in np.nonzero(mine_u & over_bu)[0]])
        lam_here = float(max(lam_mine, default=0.0))
        if not others:
            datum = ("NO SHARED GROUND: not one reading between this leg and "
                     "another participant")
        elif not lam_mine:
            datum = "AT ONE DATUM AS DESIGNED"
        elif lam_here == 0.0:
            datum = ("ONE DATUM (lambda 0): the stations either side of every "
                     "meeting held within its design budget; what is left "
                     "over it is the legs' own grade between stations")
        elif lam_here < 1.0:
            datum = (f"lambda {lam_here:.4f}: the nearest to one datum its "
                     f"own bounds allow, that fraction of the way from the "
                     f"design budget to the slide allowance")
        else:
            datum = ("SLIDE ALLOWANCE: no tighter profile satisfies its own "
                     "bounds at these rungs")
        verdict = ("UNSHARED" if not others else
                   "JOINED" if moved else "AGREED")
        out["y"][lg["id"]] = Yout[li]
        out["rung"][lg["id"]] = rung[li]
        out["legs"][lg["id"]] = {
            "verdict": verdict,
            "datum": datum,
            "shares_ground_with": others,
            "readings": int(mine.sum() + mine_u.sum()),
            "over_allowance_before": over_b,
            "over_allowance_after": over_a,
            "over_design_budget_before": bud_b,
            "over_design_budget_after": bud_a,
            "worst_before": worst((ea0, eu0), mine, mine_u, Y0r),
            "worst_after": worst((ea, eu), mine, mine_u, Yr),
            "stations_moved": moved,
            "max_move_m": round(float(mv.max()), 3) if len(mv) else 0.0,
            "rung_design": float(lg["rung"]),
            "rung": rung[li],
            "rungs_climbed": climbs.get(li, []),
        }
    return out



# WET_DEPENDS_ON: THE LOWEST GENERATED HEIGHT UNDER THE BENCH THIS WRITE
# LEVELS -- `footprint_half_m(width)` either side of the centreline -- and
# nothing else decides whether a station is over water.
#
# It used to depend on the CENTRELINE height alone, and the centreline is a
# strict SUBSET of the footprint.  `ribbon.py` writes a LEVEL bench out to
# `footprint_half_m`, `TerrainComp::ApplyToHeightmap` clamps PER SAMPLE, and
# `ribbon.water_sign` judges EVERY sample of that bench against the generated
# field.  So a station whose centreline stands dry on the crest of a bank,
# with its shoulder hanging over water 4 m away, was never a crossing
# candidate at all -- the run was never flagged, `ford_rule` was never asked,
# and the writer filled the bank.
#
# THIS IS THE FOURTH TIME ONE OBJECT HAS BEEN CERTIFIED ON THE CENTRELINE AND
# CONSUMED OVER THE FOOTPRINT.  `network.transverse_window` already fixed it
# for the APPLY CLAMP after N4-mid2-terminus came back with
# `stations_over_clamp: 0` and 71 real breaches, every one of them at lat
# 3.99-4.12 m.  The wet test was left behind.
#
# MEASURED 2026-09-24 on Fresh42 `T1-portalhub-temple`, AFTER `ford_rule` had
# already turned its 3 fords into 3 declared bridges: `ribbon.py --validate`
# still refuses at `water_sign` with 274 of 343 submerged samples raised, and
# 179 OF THOSE 274 ARE CARRIAGEWAY-BAND (lateral <= 4.75 m), on stations the
# centreline test called DRY.  The centreline runs the crest; the bench hangs
# over the bay.  Median lateral offset of the surviving drains is 3.75 m --
# inside the 4.0 m bench, outside the line that was measured.
#
# The surviving population is real and must not be condemned: a station whose
# whole bench stands above `WATER_LEVEL + ROAD_FREEBOARD_M` is dry and stays
# terrain.  And the extent that makes a station a CROSSING is the LEVEL
# BENCH, never the batter reach: calling a station a bridge because water lies
# 10 m away would deck every shoreline road in the world.
#
# THE OTHER HALF OF THIS PARAGRAPH EXPIRED, AND NOBODY RE-READ IT.  It said
# water inside the batter reach "is not water this write removes", because
# the batter followed ground at grade.  `BATTER_WATER_DEPENDS_ON` then made
# `water_fill` REFUSE exactly that batter wherever its plane still stands at
# or above the plane over seed-submerged ground, so the writer now leaves a
# step there the rim gate refuses -- and the planner kept approving it.
# MEASURED 2026-09-25, Fresh42 drive 2, step 4: T1, T5, S5, T9 and S13 all
# refused on `water_fill`-owned rim steps (10 / 52 / 51 / 53 / 102) on
# profiles this test called dry.  The batter's water is therefore asked
# SEPARATELY, of the rays the writer actually walks -- see
# `WALKED_WET_DEPENDS_ON` -- and this test keeps the one question it answers:
# is the levelled bench itself over water.
WET_DEPENDS_ON = (
    "the MINIMUM generated height across the LEVELLED FOOTPRINT -- "
    "`footprint_half_m(width_m)` either side of the centreline -- because "
    "that is the set of samples the write benches to one profile height and "
    "the set `ribbon.water_sign` judges. NOT the CENTRELINE height, which is "
    "a strict SUBSET of it and misses every bank the shoulder overhangs. It "
    "decides whether a station is a CROSSING; whether the BATTER the writer "
    "walks from that bench reaches water is WALKED_WET_DEPENDS_ON's question.")


def footprint_wet(t_foot: np.ndarray) -> np.ndarray:
    """Which stations would put a LEVELLED BENCH in the water.

    `t_foot` is the per-station MINIMUM generated height across the level road
    surface -- `network.transverse_window`'s `lo`.  See `WET_DEPENDS_ON`.

    ONE PLACE THE RULE LIVES, so the crossing detector and the profile solver
    cannot disagree about which stations are over water; they did, and the
    disagreement was invisible because both of them were reading `t`.
    """
    return np.asarray(t_foot) <= WATER_LEVEL + ROAD_FREEBOARD_M


# BATTER_WATER_DEPENDS_ON: what may be written on ground the SEED drew below
# the water plane, and it is the RESULTING HEIGHT as well as the direction.
#
# THE STATE THAT SHIPPED, and its justification EXPIRED WITHOUT BEING
# RE-MEASURED.  `ribbon.batter_water_gate` refused a batter sample that takes
# submerged ground DOWN and PERMITTED every raise, however far above the
# plane it landed, deferring the raise to `ribbon.water_sign` over the whole
# taken set.  The recorded reason (2026-09-24) is that clipping the raise
# "does not stop a causeway -- IT LEAVES THE CAUSEWAY'S WHOLE FLANK STANDING
# AS A CLIFF", measured then as 206 BATTER samples against 163 CARRIAGEWAY at
# T1-portalhub-temple.  The division of labour it set up is: this gate
# permits, `water_sign` refuses, AND THE REMEDY IS THE ROUTE.
#
# THAT REMEDY CANNOT APPLY TO A BATTER TOE.  The batter's extent is a
# function of how far the road stands above the ground beside it, not of
# where the line goes; no re-route and no deck removes it.  So the permit
# handed `water_sign` a population whose only named remedy was unreachable,
# and the write refused at the end of a 47-second rasterisation with nothing
# the router could do about it.
#
# AND THE CLIFF IT TRADED AGAINST IS GONE.  `ford_rule` and `WET_DEPENDS_ON`
# emptied the causeway population between that measurement and this one.
# MEASURED 2026-09-24 over all fourteen Fresh42 segments, footprint minimum
# from `network.transverse_window`: 158 stations have their LEVELLED
# FOOTPRINT below the 30.0 m plane and 0 OF 158 ARE UNBRIDGED.  A bridged
# station writes no carriageway, and `ribbon.stamp` writes no batter where
# there is no road, so a causeway flank cannot be reached from any plan this
# router emits.  The 163 carriageway drains that made clipping catastrophic
# are 0; T1's 109 remaining drains are 109 BATTER and 0 carriageway.
#
# WHAT THE CLIP LEAVES INSTEAD, MEASURED offline through `ribbon.stamp` on
# the recorded Fresh42 zone patches, all fourteen segments:
#     seed-submerged samples RAISED to or above the plane   750 -> 0
#     carriageway samples (paved + shoulder)                UNCHANGED, 14/14
#     batter samples                                    45,106 -> 44,025
#     batter still laid on submerged ground, under the plane  423 -> 201
#     `water_fill` clips                                       0 -> 530
#     walls recorded at those clips                                    419
#     of those 419 walls, standing on SEED-SUBMERGED ground           419
#     standing on land                                                  0
# Every wall this rule leaves is below the water surface.  That is a
# SHORELINE, not a cliff a player walks off -- which is the whole difference
# from the 2026-09-24 experiment, where the clip cut a real causeway flank on
# dry-side ground.
#
# THE SURVIVING POPULATION IS REAL AND MUST NOT BE CONDEMNED: 201 samples of
# batter are still laid ON submerged ground because they stay UNDER the
# plane.  A toe that grades into a shoal without lifting it out of the water
# removes no water, and "no batter over water" would take all 201 with it and
# reinstate the cliff on ground that is genuinely dry-adjacent.
BATTER_WATER_DEPENDS_ON = (
    "generated_height", "water_plane", "earthwork_direction",
    # THE TERM THE SHIPPED GATE REFUSED TO NAME.  Its own docstring said
    # "`resulting_height` IS NOT ONE OF THEM, AND IT WAS TRIED" -- against a
    # world that still had causeways in it.  It does not any more.
    "resulting_height")


def batter_water_rule(generated: float, delta: float) -> str | None:
    """What this batter sample may do to seed-submerged ground.

    `"water_edge"`, `"water_fill"`, or None.  `delta` is THIS sample's own
    `target - generated`, not the ray's intent and not the road's.

    Three states over ground the seed drew below `WATER_LEVEL`, and the third
    is the one that moved:

      * TAKEN DOWN -- the bank cut to the waterline beside standing water,
        the early-dock defect, the one unrepairable shape.  `"water_edge"`.
      * RAISED BUT STILL UNDER THE PLANE -- a toe grading into a shoal.  It
        removes no water and it is LAID; 201 such samples survive across
        Fresh42.
      * RAISED TO OR ABOVE THE PLANE -- the bay filled in to make a taper.
        The generated field is the only record the water was there and no
        later write restores it.  `"water_fill"`, and the ray ends here.

    THE THIRD ARM IS EXACTLY `ribbon.water_sign`'s POPULATION, said one
    sample early.  It used to be said at the end of the whole rasterisation,
    by a gate whose stated remedy is the ROUTE -- and a batter toe has no
    routing remedy.  Saying it per sample ends the RAY, which is the only
    thing that can actually stop.

    ONE PLACE THE RULE LIVES, here beside `ford_rule` and `footprint_wet`,
    because the router reserves the footprint the writer lays down and the
    two must not hold different opinions about where the water is.
    `ribbon.batter_water_gate` delegates to this.

    See `BATTER_WATER_DEPENDS_ON` for the measurement that retired the
    permit.
    """
    if generated >= WATER_LEVEL:
        return None
    if delta < 0.0:
        return "water_edge"
    if generated + delta >= WATER_LEVEL:
        return "water_fill"
    return None


# WALKED_WET_DEPENDS_ON: WHERE THE BATTER THE WRITER WALKS MEETS WATER, asked
# of the writer itself.
#
# `WET_DEPENDS_ON` asks the levelled bench.  `batter_water_rule` refuses a
# batter sample over seed-submerged ground whose plane stands at or above the
# water plane, and the refused sample keeps its bed height beside a written
# neighbour graded a metre or more above it: a step the rim gate refuses and
# NOTHING at write time may close, because the earthwork that could is the
# earthwork just refused.  Whether that happens is a fact about the RAYS the
# writer walks -- their sign, their plane, where the ground catches it, where
# the reach cap or the earth budget stops them, the toe feather and the rim
# closure that repair what they can -- and about the TERMINAL CAP the ribbon
# clamps past the last station, which no station's cross-section samples.
# A second spelling of those rules here would drift from the writer's; so
# the planner runs the WRITER'S OWN `ribbon.stamp` over the generated field,
# the build's own `junction.rim_transfer` over the surface it leaves, labels
# each caused step with `ribbon.rim_step_owner`, and judges the carriageway
# with `ribbon.water_sign`.  See `network.writer_water`.
#
# MEASURED 2026-09-25 on the Fresh42 plan drive 2 built, with no pad, road or
# keep-out beside the write: water-owned caused steps T1 34, T5 38, S5 68,
# T9 85, S13 109, and 0 on the nine others -- the SAME partition the build's
# step 4 refused on (10 / 52 / 51 / 53 / 102), including S4 and T8, whose
# 5 and 34 `water_fill` clips the feather closes.  A proximity rule cannot
# draw that line; the walk does.
WALKED_WET_DEPENDS_ON = (
    "the_writer's_own_stamp_over_the_generated_field",
    "water_fill_on_every_batter_ray_it_walks",
    "the_terminal_cap_past_the_last_station",
    "the_rim_step_the_refusal_leaves_after_feather_and_closure")


# WATER_STANDOFF_DEPENDS_ON: HOW FAR A ROAD MUST STAND FROM WATER THE WRITER
# REFUSED ITS BATTER ON -- the ALIGNMENT answer, asked before the profile.
#
# A fill plane leaves the carriageway edge at the road's height and falls at
# the batter grade, so it can still stand at or above the water plane only
# within `(y - WATER_LEVEL) / grade` of that edge, and never past the reach
# cap.  That is the local batter reach, and it is what sizes the stand-off:
# a corridor cell is forbidden when seed-wet ground implicated by the writer
# lies closer than `footprint_half + min((y - WATER_LEVEL) / grade, reach) +
# slack`, with `y` the road height the refused ray left from.  The slack is
# what separates a cell centre from the line: half a cell diagonal (the chord
# between two centres) plus the 1 m simplify tolerance.
#
# NOT the achievable surface `S` at the candidate cell, although a road moved
# up a bank does stand higher: it was tried.  MEASURED on the Fresh42
# re-plan, keyed on max(y, S): T1's and T9's first move left the censused
# band (UNCENSUSED) and S13's found no route at all (235 cells forbidden);
# keyed on the refused ray's own `y`, T1 and S5 both moved off the pond
# inside the band with 0 water steps.  A line moved up the bank is re-asked
# of the writer instead, and the next round adds what IT refused, at ITS
# height.
#
# THE PROFILE ALONE CANNOT SERVE, MEASURED.  A batter-toe ceiling on every
# station (RimWater, 2026-09-25) rejected 9 of 14 Fresh42 segments, three of
# which lay; at the pond (-141,-264) the ceiling needs T1 down 4.31 m, S5
# 6.97 m, T9 4.12 m, and on the dock shores it falls below the 31.0 m
# freeboard floor.  The ground at the pond falls ~0.7 m/m toward it, within
# 0.08 m/m of the 0.7813 batter, so a fill ray there barely closes at all:
# only moving the line off that bank removes the water from its reach.
WATER_STANDOFF_DEPENDS_ON = ("the_seed_wet_ground_the_writer_refused_at",
                             "the_road_height_the_refused_ray_left_from",
                             "the_batter_grade_and_its_reach_cap")
WATER_STANDOFF_SIMPLIFY_M = 1.0


def water_standoff_forbid(corr: "Corridor", seeds, *, half_m: float,
                          grade: float, reach_m: float) -> np.ndarray:
    """Corridor cells a road may not take, from the seed-wet samples the
    writer refused (`seeds`: `(x, z, road_y)`).  See
    WATER_STANDOFF_DEPENDS_ON.

    The seeds are grown to the whole seed-wet body they stand in (8-connected
    on the field, bounded by the largest reach), because a line pushed off
    the refused shore of a pond must not be handed its other shore."""
    out = np.zeros((corr.m, corr.m), dtype=bool)
    if not seeds:
        return out
    f = corr.fld
    slack = corr.cell_m * math.sqrt(0.5) + WATER_STANDOFF_SIMPLIFY_M
    rmax = half_m + reach_m + slack
    xs = np.array([s_[0] for s_ in seeds], dtype=np.float64)
    zs = np.array([s_[1] for s_ in seeds], dtype=np.float64)
    ys = np.array([s_[2] for s_ in seeds], dtype=np.float64)
    pad = 2.0 * rmax + corr.cell_m
    j0 = max(0, int(math.floor((xs.min() - pad - f.x0) / f.step)))
    j1 = min(f.n - 1, int(math.ceil((xs.max() + pad - f.x0) / f.step)))
    i0 = max(0, int(math.floor((zs.min() - pad - f.z0) / f.step)))
    i1 = min(f.n - 1, int(math.ceil((zs.max() + pad - f.z0) / f.step)))
    wet = f.h[i0:i1 + 1, j0:j1 + 1] < WATER_LEVEL
    seed = np.zeros_like(wet)
    y_seed = np.full(wet.shape, -np.inf)
    si = np.rint((zs - f.z0) / f.step).astype(int) - i0
    sj = np.rint((xs - f.x0) / f.step).astype(int) - j0
    ok = (si >= 0) & (si < wet.shape[0]) & (sj >= 0) & (sj < wet.shape[1])
    for a, b, y in zip(si[ok], sj[ok], ys[ok]):
        seed[a, b] = True
        y_seed[a, b] = max(y_seed[a, b], y)
    if not seed.any():
        return out
    # THE BODY EACH SEED STANDS IN.  A seed is an integer-lattice sample and
    # the field is sampled 0.4 m off it, so the seed is dilated one sample
    # before it is asked which body it touches.
    lab, _n = ndimage.label(wet, structure=np.ones((3, 3), dtype=bool))
    touch = ndimage.binary_dilation(seed, structure=np.ones((3, 3), dtype=bool))
    keep = np.unique(lab[touch & wet])
    keep = keep[keep > 0]
    d_seed, (ni, nj) = ndimage.distance_transform_edt(~seed,
                                                      return_indices=True)
    hazard = (np.isin(lab, keep) & (d_seed * f.step <= rmax)) | seed
    y_haz = y_seed[ni, nj]
    d_h, (hi_, hj_) = ndimage.distance_transform_edt(~hazard,
                                                     return_indices=True)
    cj0 = max(0, int(math.floor((f.x0 + j0 * f.step - corr.x0) / corr.cell_m)))
    cj1 = min(corr.m - 1, int(math.ceil((f.x0 + j1 * f.step - corr.x0)
                                         / corr.cell_m)))
    ci0 = max(0, int(math.floor((f.z0 + i0 * f.step - corr.z0) / corr.cell_m)))
    ci1 = min(corr.m - 1, int(math.ceil((f.z0 + i1 * f.step - corr.z0)
                                         / corr.cell_m)))
    CI, CJ = np.mgrid[ci0:ci1 + 1, cj0:cj1 + 1]
    fi = np.rint((corr.z0 + CI * corr.cell_m - f.z0) / f.step).astype(int) - i0
    fj = np.rint((corr.x0 + CJ * corr.cell_m - f.x0) / f.step).astype(int) - j0
    inside = ((fi >= 0) & (fi < wet.shape[0]) & (fj >= 0)
              & (fj < wet.shape[1]))
    fi, fj = np.clip(fi, 0, wet.shape[0] - 1), np.clip(fj, 0, wet.shape[1] - 1)
    dist = d_h[fi, fj] * f.step
    y_near = y_haz[hi_[fi, fj], hj_[fi, fj]]
    reach = np.minimum(np.maximum(y_near - WATER_LEVEL, 0.0) / grade, reach_m)
    out[CI, CJ] = inside & (dist < half_m + reach + slack)
    return out



# ---------------------------------------------------------------------------
# crossing extraction
# ---------------------------------------------------------------------------

def find_crossings(route_id: str, pts: list[tuple[float, float]], s: np.ndarray,
                   t: np.ndarray, infeasible: list[tuple[int, int]],
                   snap_m: float = 2.0,
                   merge_gap_m: float = 2.0 * 6.0 + 4.0,
                   max_merged_m: float = 60.0,
                   *, t_foot: np.ndarray,
                   ) -> tuple[list[dict], np.ndarray, list[dict]]:
    """Turn wet runs and infeasible runs into crossing records.

    A station is a crossing candidate if the LEVELLED BENCH there would stand
    within the road freeboard of the water plane (so the ribbon would be awash
    or would be a dyke), or if the profile fit proved no slope-limited ribbon
    exists inside the +/-8 m clamp there.  Runs are merged and the banks are the
    last dry/feasible station on each side.

    `t` is the CENTRELINE generated height and stays the reference for the
    deck geometry -- chord, `gap_min_y`, bank `ground_y`.  `t_foot` is the
    per-station MINIMUM over the levelled footprint and is the ONLY thing the
    water questions are asked of: see `WET_DEPENDS_ON`.  It is keyword-only
    and has NO DEFAULT, because a default would be the centreline again and
    this whole function spent a build reading one object and deciding about
    another.
    """
    n = len(pts)
    t_foot = np.asarray(t_foot, dtype=np.float64)
    if len(t_foot) != n:
        raise ValueError(
            f"{route_id}: t_foot has {len(t_foot)} station(s) against "
            f"{n} point(s). The footprint window is measured PER STATION and "
            f"a short one would silently judge the tail of this segment on "
            f"nothing.")
    wet = footprint_wet(t_foot)
    flag = wet.copy()
    for a, b in infeasible:
        flag[a:b + 1] = True

    # MERGE RUNS SEPARATED BY LESS THAN TWO BANK LEVEL RUNS.  Two gaps 8 m apart
    # are not two crossings: their 6 m level runs overlap, the strip between
    # them is neither bank nor deck, and treating them separately produced a
    # window with NO dry station in it -- which made the "one level height H"
    # calculation degenerate (an empty interval whose midpoint was zero) and put
    # the ribbon 30 m underground.  MEASURED consequence before the merge: 60
    # stations outside the +/-8 m clamp across the first six trunk segments.
    # `merge_gap_m` defaults to two level runs plus a 4 m working margin, so
    # every surviving crossing has real dry bank on both sides.
    gap_stations = max(1, int(round(merge_gap_m / max(float(s[1] - s[0]), 1e-6))))
    runs: list[list[int]] = []
    k = 0
    while k < n:
        if flag[k]:
            j = k
            while j + 1 < n and flag[j + 1]:
                j += 1
            # Merge only while the MERGED run stays buildable.  Two narrow
            # channels 16 m apart are one crossing; six narrow channels strung
            # along 200 m of broken shoreline are SIX crossings, and merging
            # them reported a single 200.6 m span that no bridge can reach
            # across -- MEASURED on W1, plus 174.7 m on W4 and 114.8 m on T2,
            # all three of which the search itself had already proved
            # crossable (it forbids water more than half the maximum span from
            # land, so every channel it used is under 96 m). The defect was
            # purely in how the runs were REPORTED.
            merged_m = float(s[j] - s[runs[-1][0]]) if runs else 0.0
            if runs and k - runs[-1][1] <= gap_stations and merged_m <= max_merged_m:
                runs[-1][1] = j
            else:
                runs.append([k, j])
            k = j + 1
        else:
            k += 1
    for a, b in runs:
        flag[a:b + 1] = True
    # FORD vs BRIDGE.  Agreed boundary with Crossings, recorded once: a FORD
    # is TERRAIN and therefore mine; a BRIDGE is a structure and therefore
    # theirs.  `FORD_DEPENDS_ON` names what that boundary is a function of
    # and why the two earlier spellings of it were wrong; the rule itself is
    # below, at `is_ford`.
    out: list[dict] = []
    fords: list[dict] = []
    i = 0
    while i < n:
        if not flag[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and flag[j + 1]:
            j += 1
        a = max(i - 1, 0)
        b = min(j + 1, n - 1)
        seg = t[i:j + 1]
        segf = t_foot[i:j + 1]
        any_wet = bool(wet[i:j + 1].any())
        run_m = float(s[b] - s[a])
        # THE WATER QUANTITIES COME OFF `segf`, NOT `seg`.  `need_fill` is
        # "how far must this write lift the LOWEST ground it benches to reach
        # road freeboard" and `depth_m` is "how far below the plane does that
        # ground start"; both are questions about the bench, and asking them
        # of the centreline is how a shoulder hanging over a bay reported
        # depth 0.0 and stayed terrain.  See `WET_DEPENDS_ON`.
        need_fill = float((WATER_LEVEL + ROAD_FREEBOARD_M) - segf.min())
        depth_m = float(WATER_LEVEL - segf.min())
        # `depth_m` IS the quantity `FORD_DEPENDS_ON` names -- the deepest
        # ground under this run's bench, below the plane -- and `is_ford` is
        # the one place the rule lives.
        is_ford = ford_rule(any_wet, need_fill, depth_m)
        ax, az = pts[a]
        bx, bz = pts[b]
        if not is_ford:
            # Snap bank coordinates so Crossings' piece grid lands clean.
            ax, az = round(ax / snap_m) * snap_m, round(az / snap_m) * snap_m
            bx, bz = round(bx / snap_m) * snap_m, round(bz / snap_m) * snap_m
        brg = (math.degrees(math.atan2(bx - ax, bz - az)) + 360.0) % 360.0
        # Classify by the DOMINANT cause, not by whether any station happened to
        # be wet.  A run that is infeasible because the ground drops faster than
        # the grade limit allows, and which happens to end at the water, is a
        # CLAMP failure -- calling it `water_body` produced a record describing a
        # 107 m "bridge" at y=56 over dry hillside, which reports the wrong
        # defect and would have had Crossings build the wrong thing.
        chord = np.interp(s[i:j + 1], [s[a], s[b]], [t[a], t[b]])
        worst = float(np.max(np.abs(seg - chord)))
        worst_dry = float(max((abs(seg[k - i] - chord[k - i])
                               for k in range(i, j + 1) if not wet[k]), default=0.0))
        if worst_dry > CUT_FILL_MAX:
            reason = "clamp_exceeded"
        elif any_wet:
            reason = "water_body"
        else:
            reason = "chasm"
        rec = {
            "segment_id": route_id,
            "bank_a": {"xz": [round(ax, 2), round(az, 2)], "ground_y": round(float(t[a]), 2),
                       "bearing_deg": round(brg, 1)},
            "bank_b": {"xz": [round(bx, 2), round(bz, 2)], "ground_y": round(float(t[b]), 2),
                       "bearing_deg": round((brg + 180.0) % 360.0, 1)},
            "span_m": round(math.hypot(bx - ax, bz - az), 2),
            "stations": [int(i), int(j)],
            "gap_min_y": round(float(seg.min()), 2),
            "water_surface_y": WATER_LEVEL if any_wet else None,
            "reason": reason,
            "clamp_evidence": {
                "worst_required_delta_m": round(worst, 2),
                "required_fill_to_freeboard_m": round(need_fill, 2),
                "max_depth_below_plane_m": round(depth_m, 2),
                # BOTH FLOORS, NAMED.  `gap_min_y` above is the centreline's
                # and is what a bed reading taken along the line can be
                # compared against; this one is the lowest ground under the
                # BENCH, and it is the number the two water rules are taken
                # on.  Where they differ the road is on a bank, which is
                # exactly the case that used to report nothing.
                "footprint_min_y": round(float(segf.min()), 2),
                "centreline_min_y": round(float(seg.min()), 2),
                "water_keyed_on": "footprint_min_y (route.WET_DEPENDS_ON)",
                "run_m": round(run_m, 2),
                "clamp_m": CLAMP_M, "usable_clamp_m": CUT_FILL_MAX,
                "stations_flagged": int(j - i + 1),
            },
            "measured": (f"generated heights from PatchScan at 1 m, seed "
                         f"{_seed()}"),
        }
        if is_ford:
            rec["crossing_id"] = f"{route_id}-f{len(fords)+1:02d}"
            rec["owner"] = "RoadNet"
            rec["kind"] = "ford"
            rec["built_as"] = "levelled paved causeway at road freeboard; TERRAIN, "
            rec["built_as"] += "no structure"
            fords.append(rec)
            flag[i:j + 1] = False      # stays in my terrain, not a deck
        else:
            rec["crossing_id"] = f"{route_id}-x{len(out)+1:02d}"
            rec["owner"] = "Crossings"
            rec["kind"] = "bridge"
            out.append(rec)
        i = j + 1
    return out, flag, fords
