#!/usr/bin/env python3
"""The road router: A* over real terrain, with bridges where water demands them.

The interesting design decision is that WATER IS NOT A WALL. If water were simply
impassable the router would refuse half the useful routes; if water were merely
expensive per metre it would cross wherever the straight line happens to hit,
which is how you end up needing a 200 m bridge. Instead a water crossing is priced
as `2 * abutment_cost + span * cost_per_m + depth`, so the search pays a large
fixed price to enter the water and then a steep price per metre of it. That makes
A* walk up to a kilometre out of its way to find a narrows -- which is exactly what
a road builder does, and it is why the emitted spans are short enough that the
library's 26 m bridge actually reaches.

Everything the router decides is checked against the emitted result afterwards:
`Route.audit()` re-walks the finished path and re-verifies freeboard, grade and
span against the policy, so a bug in the cost function shows up as a failed audit
rather than as a road through a lake.

Heights are river-inclusive and point-exact (see terrain.py). Comparisons are
against terrain.WATER_LEVEL, never 0.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import numpy as np

from . import terrain
from .terrain import WATER_LEVEL, Corridor

# 16-neighbour moves on the routing lattice. The 8 diagonals alone make every path
# a staircase of 45-degree turns; the knight moves give intermediate headings
# (~26.6 and ~63.4 degrees) so a road can hold a shallow diagonal.
NEIGHBOURS: tuple[tuple[int, int], ...] = (
    (-1, 0), (1, 0), (0, -1), (0, 1),
    (-1, -1), (-1, 1), (1, -1), (1, 1),
    (-2, -1), (-2, 1), (2, -1), (2, 1),
    (-1, -2), (-1, 2), (1, -2), (1, 2),
)


class RouteError(RuntimeError):
    pass


@dataclass
class Node:
    x: int
    z: int
    y: float
    water: bool

    def as_list(self) -> list[float]:
        return [self.x, round(self.y, 2), self.z]


@dataclass
class Crossing:
    """A stretch of the path that is over water, i.e. a bridge."""

    start: tuple[int, int]
    end: tuple[int, int]
    span_m: float
    max_depth_m: float
    freeboard_start_m: float
    freeboard_end_m: float
    blueprint: str | None = None
    blueprint_span_m: float | None = None
    spans: int = 0
    pier_positions: list[list[float]] = field(default_factory=list)
    problem: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "start": list(self.start),
            "end": list(self.end),
            "span_m": round(self.span_m, 2),
            "max_depth_m": round(self.max_depth_m, 2),
            "freeboard_start_m": round(self.freeboard_start_m, 2),
            "freeboard_end_m": round(self.freeboard_end_m, 2),
            "blueprint": self.blueprint,
            "blueprint_span_m": self.blueprint_span_m,
            "spans": self.spans,
            "pier_positions": self.pier_positions,
            "problem": self.problem,
        }


@dataclass
class Route:
    a: str
    b: str
    nodes: list[Node]
    crossings: list[Crossing]
    policy: dict[str, Any]
    grade_limit: float
    length_m: float
    land_m: float
    water_m: float
    max_grade: float
    min_freeboard_m: float
    explored: int
    # Metres of carriageway steeper than the grade budget, and the vertical metres of
    # cut-and-fill that implies. A road is allowed over budget -- refusing outright
    # would reject most mountain approaches -- but it is not free, and an admin
    # hoeing it needs to know how much of it is earthworks before starting.
    over_budget_m: float = 0.0
    cut_fill_m: float = 0.0
    notes: list[str] = field(default_factory=list)

    def straight_m(self) -> float:
        return math.hypot(self.nodes[-1].x - self.nodes[0].x, self.nodes[-1].z - self.nodes[0].z)

    def detour_ratio(self) -> float:
        """Routed length over straight-line length. 1.0 is a road across a table."""
        return self.length_m / max(1e-6, self.straight_m())

    def polyline(self) -> list[list[float]]:
        return [n.as_list() for n in self.nodes]

    def simplify(self, tol: float | None = None) -> list[Node]:
        """Douglas-Peucker in the xz plane, so the plan emits straight runs."""
        tol = tol if tol is not None else float(self.policy["roadway"]["simplify_tolerance_m"])
        return _dp(self.nodes, tol)

    def audit(self) -> list[str]:
        """Re-verify the finished route against policy. Empty list means clean."""
        problems: list[str] = []
        road_fb = float(self.policy["water"]["road_freeboard_m"])
        hard = float(self.policy["grade"]["impassable"])
        for n in self.nodes:
            if not n.water and n.y - WATER_LEVEL < road_fb:
                problems.append(
                    f"land node ({n.x},{n.z}) has freeboard {n.y - WATER_LEVEL:.2f} m, policy wants >= {road_fb}"
                )
        for p, q in zip(self.nodes, self.nodes[1:]):
            run = math.hypot(q.x - p.x, q.z - p.z)
            if run <= 0:
                problems.append(f"zero-length step at ({p.x},{p.z})")
                continue
            if p.water or q.water:
                continue  # a bridge deck has its own gradient, set by the blueprint
            g = abs(q.y - p.y) / run
            if g > hard + 1e-9:
                problems.append(f"step ({p.x},{p.z})->({q.x},{q.z}) grade {g:.3f} exceeds impassable {hard}")
        for c in self.crossings:
            if c.problem:
                problems.append(f"crossing {c.start}->{c.end}: {c.problem}")
            elif c.blueprint_span_m is not None:
                reach = c.blueprint_span_m * max(1, c.spans)
                need = c.span_m + 2 * float(self.policy["bridge"]["abutment_margin_m"])
                if reach + 1e-6 < need:
                    problems.append(
                        f"crossing {c.start}->{c.end}: {c.blueprint} reaches {reach:.1f} m "
                        f"but the crossing needs {need:.1f} m"
                    )
        return problems

    def as_dict(self) -> dict[str, Any]:
        return {
            "from": self.a,
            "to": self.b,
            "length_m": round(self.length_m, 1),
            "land_m": round(self.land_m, 1),
            "water_m": round(self.water_m, 1),
            "straight_m": round(self.straight_m(), 1),
            "detour": round(self.detour_ratio(), 3),
            "grade_limit": self.grade_limit,
            "max_grade": round(self.max_grade, 3),
            "min_freeboard_m": round(self.min_freeboard_m, 2),
            "over_budget_m": round(self.over_budget_m, 1),
            "cut_fill_vertical_m": round(self.cut_fill_m, 1),
            "node_count": len(self.nodes),
            "segment_count": max(0, len(self.simplify()) - 1),
            "crossings": [c.as_dict() for c in self.crossings],
            "lattice_nodes_explored": self.explored,
            "audit": self.audit(),
            "notes": self.notes,
            "polyline": self.polyline(),
        }


def _dp(nodes: Sequence[Node], tol: float) -> list[Node]:
    if len(nodes) < 3:
        return list(nodes)
    ax, az = nodes[0].x, nodes[0].z
    bx, bz = nodes[-1].x, nodes[-1].z
    dx, dz = bx - ax, bz - az
    den = math.hypot(dx, dz)
    worst, widx = -1.0, 0
    for i in range(1, len(nodes) - 1):
        n = nodes[i]
        if den == 0:
            d = math.hypot(n.x - ax, n.z - az)
        else:
            d = abs(dz * (n.x - ax) - dx * (n.z - az)) / den
        # A node where the surface changes state is never simplified away: an
        # abutment is a hard geometric fact, not a wiggle.
        if n.water != nodes[0].water or n.water != nodes[-1].water:
            d += tol
        if d > worst:
            worst, widx = d, i
    if worst <= tol:
        return [nodes[0], nodes[-1]]
    return _dp(nodes[: widx + 1], tol)[:-1] + _dp(nodes[widx:], tol)


def _avoid_field(cor: Corridor, locations: Iterable[dict[str, Any]], policy: dict[str, Any]):
    """Hard-block mask and soft-cost field from the ZoneSystem location dump."""
    pol = policy["avoid"]
    h, w = cor.height.shape
    blocked = np.zeros((h, w), dtype=bool)
    soft = np.zeros((h, w), dtype=np.float32)
    classes = pol.get("classes") or []
    zz, xx = np.mgrid[0:h, 0:w]
    hits = 0
    for loc in locations:
        lx, lz = float(loc["x"]), float(loc["z"])
        name = loc.get("name", "")
        radius, is_hard = float(pol["default_radius_m"]), False
        for cls in classes:
            exact = cls.get("match") or []
            prefixes = cls.get("match_prefix") or []
            if name in exact or any(name.startswith(p) for p in prefixes):
                radius, is_hard = float(cls["radius_m"]), bool(cls.get("hard", False))
                break
        else:
            # Unclassified locations get the default radius as a SOFT cost. Silently
            # ignoring them would let a road run through 12,301 unnamed things.
            is_hard = False
        j, i = int(round(lx)) - cor.x0, int(round(lz)) - cor.z0
        r = int(math.ceil(radius))
        if i + r < 0 or j + r < 0 or i - r >= h or j - r >= w:
            continue
        hits += 1
        i0, i1 = max(0, i - r), min(h, i + r + 1)
        j0, j1 = max(0, j - r), min(w, j + r + 1)
        d = np.hypot(xx[i0:i1, j0:j1] - j, zz[i0:i1, j0:j1] - i)
        inside = d <= radius
        if is_hard:
            blocked[i0:i1, j0:j1] |= inside
        else:
            taper = np.clip(1.0 - d / max(radius, 1e-6), 0.0, 1.0)
            soft[i0:i1, j0:j1] = np.maximum(soft[i0:i1, j0:j1], taper.astype(np.float32) * inside)
    return blocked, soft, hits


def _classify_crossings(nodes: list[Node], cor: Corridor, policy: dict[str, Any]) -> list[Crossing]:
    bp = policy["bridge"]
    margin = float(bp["abutment_margin_m"])
    ladder = sorted(
        (b for b in bp["blueprints"] if b.get("verified_span")),
        key=lambda b: float(b["span_m"]),
    )
    out: list[Crossing] = []
    i = 0
    while i < len(nodes):
        if not nodes[i].water:
            i += 1
            continue
        j = i
        while j + 1 < len(nodes) and nodes[j + 1].water:
            j += 1
        # The abutments are the last dry node before and the first dry node after.
        a = nodes[i - 1] if i > 0 else nodes[i]
        b = nodes[j + 1] if j + 1 < len(nodes) else nodes[j]
        span = math.hypot(b.x - a.x, b.z - a.z)
        depths = [WATER_LEVEL - nodes[k].y for k in range(i, j + 1)]
        c = Crossing(
            start=(a.x, a.z),
            end=(b.x, b.z),
            span_m=span,
            max_depth_m=max(depths) if depths else 0.0,
            freeboard_start_m=a.y - WATER_LEVEL,
            freeboard_end_m=b.y - WATER_LEVEL,
        )
        need = span + 2 * margin
        chosen = next((x for x in ladder if float(x["span_m"]) >= need), None)
        if chosen is not None:
            c.blueprint = chosen["name"]
            c.blueprint_span_m = float(chosen["span_m"])
            c.spans = 1
        elif bp.get("allow_multispan") and ladder:
            # Pier out with the cheapest verified span. Only legal where the water is
            # shallow enough for a pier to reach the bed.
            unit = ladder[0]
            per = float(unit["span_m"]) - 2 * margin
            n = int(math.ceil(span / per))
            if c.max_depth_m > float(bp["max_depth_m"]):
                c.problem = (
                    f"span {span:.1f} m exceeds every verified blueprint "
                    f"({ladder[-1]['span_m']} m) and the water is {c.max_depth_m:.1f} m deep, "
                    f"deeper than the {bp['max_depth_m']} m pier limit"
                )
            elif span > float(bp["max_total_span_m"]):
                c.problem = f"span {span:.1f} m exceeds max_total_span_m {bp['max_total_span_m']}"
            else:
                c.blueprint = unit["name"]
                c.blueprint_span_m = float(unit["span_m"])
                c.spans = n
                for k in range(1, n):
                    t = k / n
                    px = a.x + (b.x - a.x) * t
                    pz = a.z + (b.z - a.z) * t
                    c.pier_positions.append([round(px, 1), round(WATER_LEVEL, 2), round(pz, 1)])
        else:
            c.problem = f"span {span:.1f} m exceeds every verified blueprint and multispan is disabled"
        out.append(c)
        i = j + 1
    return out


def route(
    cor: Corridor,
    a: tuple[float, float],
    b: tuple[float, float],
    policy: dict[str, Any],
    locations: Iterable[dict[str, Any]] = (),
    a_name: str = "a",
    b_name: str = "b",
    grade_limit: float | None = None,
    step: int | None = None,
) -> Route:
    """A* a road from `a` to `b` across `cor`.

    `grade_limit` defaults to policy grade.max_road; pass max_spur for a mountain
    spur that is explicitly foot-and-pack only.
    """
    gp, wp, bp, rp = policy["grade"], policy["water"], policy["bridge"], policy["roadway"]
    step = int(step or rp["lattice_step_m"])
    budget = float(grade_limit if grade_limit is not None else gp["max_road"])
    hard_grade = float(gp["impassable"])
    earth = float(gp["earthworks_weight"])
    road_fb = float(wp["road_freeboard_m"])
    bridge_per_m = float(bp["cost_per_m"])
    abut = float(bp["abutment_cost"])
    depth_w = float(bp["depth_weight"])
    # The SEARCH depth limit, not the pier limit: see road_policy.yaml. Classification
    # decides whether a crossing is actually buildable; the search only decides what
    # it is allowed to look at, and refusing to look produces a dead end instead of a
    # diagnosis.
    max_depth = float(bp.get("max_search_depth_m", bp["max_depth_m"]))

    blocked, soft, loc_hits = _avoid_field(cor, locations, policy)
    soft_mult = float(policy["avoid"]["soft_penalty"])

    H, W = cor.height.shape
    h = cor.height

    def cell(x: int, z: int) -> tuple[int, int]:
        return z - cor.z0, x - cor.x0

    def snap(p: tuple[float, float]) -> tuple[int, int]:
        """Endpoints must sit on the lattice or the last step is a half-step."""
        x = int(round(p[0]))
        z = int(round(p[1]))
        return x - (x - cor.x0) % step, z - (z - cor.z0) % step

    start, goal = snap(a), snap(b)
    for name, p in ((a_name, start), (b_name, goal)):
        i, j = cell(*p)
        if not (0 <= i < H and 0 <= j < W) or not np.isfinite(h[i, j]):
            raise RouteError(f"{name} at {p} is outside the sampled corridor")
        if blocked[i, j]:
            raise RouteError(f"{name} at {p} is inside a hard location keep-out")

    sx, sz = start
    gx, gz = goal

    def heuristic(x: int, z: int) -> float:
        return math.hypot(gx - x, gz - z)

    open_heap: list[tuple[float, int, int]] = [(heuristic(sx, sz), sx, sz)]
    best: dict[tuple[int, int], float] = {start: 0.0}
    prev: dict[tuple[int, int], tuple[int, int]] = {}
    closed: set[tuple[int, int]] = set()
    explored = 0

    while open_heap:
        _, cx, cz = heapq.heappop(open_heap)
        if (cx, cz) in closed:
            continue
        closed.add((cx, cz))
        explored += 1
        if (cx, cz) == goal:
            break
        ci, cj = cell(cx, cz)
        cy = float(h[ci, cj])
        c_water = cy - WATER_LEVEL < road_fb
        g_here = best[(cx, cz)]
        for dx, dz in NEIGHBOURS:
            nx, nz = cx + dx * step, cz + dz * step
            if (nx, nz) in closed:
                continue
            ni, nj = cell(nx, nz)
            if not (0 <= ni < H and 0 <= nj < W):
                continue
            ny = h[ni, nj]
            if not np.isfinite(ny):
                continue
            if blocked[ni, nj]:
                continue
            ny = float(ny)
            n_water = ny - WATER_LEVEL < road_fb
            run = math.hypot(nx - cx, nz - cz)
            if n_water or c_water:
                depth = max(0.0, WATER_LEVEL - ny)
                if depth > max_depth:
                    continue
                cost = run * bridge_per_m + depth * depth_w * run
                if c_water != n_water:
                    cost += abut  # entering or leaving the water: one abutment each
            else:
                grade = abs(ny - cy) / run
                if grade > hard_grade:
                    continue
                cost = run
                if grade > budget:
                    cost += run * earth * (grade - budget) / budget
            cost *= 1.0 + soft_mult * float(soft[ni, nj])
            g_new = g_here + cost
            if g_new < best.get((nx, nz), math.inf):
                best[(nx, nz)] = g_new
                prev[(nx, nz)] = (cx, cz)
                heapq.heappush(open_heap, (g_new + heuristic(nx, nz), nx, nz))

    if goal not in best:
        raise RouteError(
            f"no route from {a_name} {start} to {b_name} {goal}: "
            f"{explored} lattice nodes explored, all exhausted"
        )

    # ---- rebuild
    path: list[tuple[int, int]] = [goal]
    while path[-1] != start:
        path.append(prev[path[-1]])
    path.reverse()

    nodes: list[Node] = []
    for x, z in path:
        i, j = cell(x, z)
        y = float(h[i, j])
        nodes.append(Node(x=x, z=z, y=y, water=(y - WATER_LEVEL < road_fb)))

    length = land = water = 0.0
    max_grade = 0.0
    over_budget = 0.0
    cut_fill = 0.0
    min_fb = math.inf
    for p, q in zip(nodes, nodes[1:]):
        run = math.hypot(q.x - p.x, q.z - p.z)
        length += run
        if p.water or q.water:
            water += run
        else:
            land += run
            g = abs(q.y - p.y) / run
            max_grade = max(max_grade, g)
            if g > budget:
                over_budget += run
                # The vertical metres the hoe has to move to bring this step down to
                # the budget grade: the excess rise, not the whole rise.
                cut_fill += (g - budget) * run
    for n in nodes:
        if not n.water:
            min_fb = min(min_fb, n.y - WATER_LEVEL)

    crossings = _classify_crossings(nodes, cor, policy)
    notes = [
        f"lattice step {step} m, {explored} nodes explored",
        f"location avoidance: {loc_hits} ZoneSystem instances intersect this corridor",
    ]
    return Route(
        a=a_name,
        b=b_name,
        nodes=nodes,
        crossings=crossings,
        policy=policy,
        grade_limit=budget,
        length_m=length,
        land_m=land,
        water_m=water,
        max_grade=max_grade,
        min_freeboard_m=(0.0 if min_fb is math.inf else min_fb),
        explored=explored,
        over_budget_m=over_budget,
        cut_fill_m=cut_fill,
        notes=notes,
    )


def chain(
    cor: Corridor,
    stops: list[tuple[str, tuple[float, float]]],
    policy: dict[str, Any],
    locations: Iterable[dict[str, Any]] = (),
    grade_limits: dict[str, float] | None = None,
) -> list[Route]:
    """Route a chain of stops, e.g. farm -> boathouse -> mountain furnace camp."""
    out: list[Route] = []
    for (na, pa), (nb, pb) in zip(stops, stops[1:]):
        limit = (grade_limits or {}).get(f"{na}->{nb}")
        out.append(
            route(cor, pa, pb, policy, locations, a_name=na, b_name=nb, grade_limit=limit)
        )
    return out
