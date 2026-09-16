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
from dataclasses import dataclass, field as dc_field

import numpy as np

from field import WATER_LEVEL, BIOME_NAMES, Field

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

    def blocked(self, i: int, j: int) -> bool:
        return self.forbid is not None and bool(self.forbid[i, j])

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
        si, sj = self.nearest_allowed(*start_xz)
        gi, gj = self.nearest_allowed(*goal_xz)
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
            raise ValueError("no route found")
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
# crossing extraction
# ---------------------------------------------------------------------------

def find_crossings(route_id: str, pts: list[tuple[float, float]], s: np.ndarray,
                   t: np.ndarray, infeasible: list[tuple[int, int]],
                   snap_m: float = 2.0,
                   merge_gap_m: float = 2.0 * 6.0 + 4.0,
                   ford_max_m: float = 8.0,
                   ford_max_depth_m: float = 1.0,
                   ) -> tuple[list[dict], np.ndarray, list[dict]]:
    """Turn wet runs and infeasible runs into crossing records.

    A station is a crossing candidate if the GENERATED ground there is within
    the road freeboard of the water plane (so a levelled ribbon would be awash
    or would be a dyke), or if the profile fit proved no slope-limited ribbon
    exists inside the +/-8 m clamp there.  Runs are merged and the banks are the
    last dry/feasible station on each side.
    """
    n = len(pts)
    wet = t <= WATER_LEVEL + ROAD_FREEBOARD_M
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
            if runs and k - runs[-1][1] <= gap_stations:
                runs[-1][1] = j
            else:
                runs.append([k, j])
            k = j + 1
        else:
            k += 1
    for a, b in runs:
        flag[a:b + 1] = True
    # FORD vs BRIDGE.  Agreed boundary with Crossings, recorded once: a FORD is
    # TERRAIN and therefore mine; a BRIDGE is a structure and therefore theirs.
    # The test is measured, not stylistic: a run qualifies as a ford only if it
    # is short enough that a bridge would be silly (<= `ford_max_m`, which is
    # Crossings' own 8 m pier-bay spacing -- below one bay there is nothing to
    # build) AND every station in it can be filled to road freeboard inside the
    # MEASURED +/-8 m apply clamp.  Anything longer or deeper is a bridge and
    # leaves my terrain entirely.
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
        any_wet = bool(wet[i:j + 1].any())
        run_m = float(s[b] - s[a])
        need_fill = float((WATER_LEVEL + ROAD_FREEBOARD_M) - seg.min())
        depth_m = float(WATER_LEVEL - seg.min())
        # FORD / CAUSEWAY vs BRIDGE, by DEPTH rather than by length.  The first
        # version used a length cap (<= 8 m, Crossings' pier-bay spacing) and it
        # was wrong in a way that showed immediately: a road hugging a beach
        # crosses hundreds of metres of ground that sits a few DECIMETRES below
        # the water plane, and a length cap turned that into a 450 m "bridge"
        # over a tidal flat on the dock -> watchtower segment, and the entire
        # 346 m west-isle link between the two abutments.  Neither is a bridge;
        # both are causeways, which is terrain and therefore mine.
        #
        # The measured test is depth: ground no more than `ford_max_depth_m`
        # below the plane can be filled to road freeboard well inside the
        # MEASURED +/-8 m apply clamp, so it is a causeway at any length.
        # Anything deeper is water the operator would be wading through, which
        # is what the operator asked for bridges over.
        is_ford = (any_wet and need_fill <= CUT_FILL_MAX
                   and (depth_m <= ford_max_depth_m or run_m <= ford_max_m))
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
                "run_m": round(run_m, 2),
                "clamp_m": CLAMP_M, "usable_clamp_m": CUT_FILL_MAX,
                "stations_flagged": int(j - i + 1),
            },
            "measured": "generated heights from PatchScan at 1 m, seed Pirate68",
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
