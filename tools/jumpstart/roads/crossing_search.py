"""Internal caches for the pure physical crossing query; no world or scan I/O."""
from __future__ import annotations

import heapq
import json
import math


class ApproachTree:
    """Incremental single-source search using Corridor's actual edge predicate.

    The caller owns an immutable corridor/mask snapshot for this tree. A changed
    water-feedback mask requires a different tree, never an update in place.
    Goal settlement uses Dijkstra, so all returned paths minimise exactly the
    same directed edge cost as Corridor._astar (including diagonal steps).
    """
    def __init__(self, corr, start):
        import copy
        self.corr = copy.copy(corr)
        self.corr.forbid = None if corr.forbid is None else corr.forbid.copy()
        self.start = start[0]*corr.m+start[1]
        self.cost = {self.start: 0.0}
        self.parent = {}
        self.closed = set()
        self.queue = [(0.0, self.start)]
        self.expanded = 0

    def path_to(self, goal):
        corr, m = self.corr, self.corr.m
        target = goal[0]*m+goal[1]
        neighbours = ((-1,0,1.0), (1,0,1.0), (0,-1,1.0), (0,1,1.0),
                      (-1,-1,math.sqrt(2)), (-1,1,math.sqrt(2)),
                      (1,-1,math.sqrt(2)), (1,1,math.sqrt(2)))
        while target not in self.closed and self.queue:
            cost, current = heapq.heappop(self.queue)
            if current in self.closed:
                continue
            self.closed.add(current)
            self.expanded += 1
            i, j = divmod(current, m)
            for di, dj, scale in neighbours:
                ni, nj = i+di, j+dj
                if not (0 <= ni < m and 0 <= nj < m):
                    continue
                node = ni*m+nj
                if node in self.closed:
                    continue
                edge = corr._step_cost((i,j), (ni,nj), scale*corr.cell_m)
                if edge is None:
                    continue
                proposed = cost+edge
                if proposed < self.cost.get(node, math.inf):
                    self.cost[node] = proposed
                    self.parent[node] = current
                    heapq.heappush(self.queue, (proposed, node))
        if target not in self.closed:
            return None
        path = [target]
        while path[-1] != self.start:
            path.append(self.parent[path[-1]])
        return [corr.cell_world(*divmod(node, m)) for node in reversed(path)]


class SeatingRefusals:
    """ONLY land_seated_deck_stations failures, never prepare_geometry failures.

    Scope is NONTERMINAL declared crossings only. The concrete callee's
    assembly/existing-carrier/bent-spacing helper is called ONLY inside its
    terminal-bank branch, which this cache rejects. In the admitted branch,
    routes/support/existing bodies are not consumed. Refusals require identical
    span/datum/policy and EVERY observed terrain value; all later assembly,
    support, abutment and route-dependent guards still run independently.
    """
    def __init__(self, span, datum):
        if span.get("terminal_banks"):
            raise ValueError("seating refusal reuse is scoped to nonterminal declared crossings")
        self.span, self.datum = span, datum
        self.failures = []
        self.hits = 0

    def token(self):
        import boardwalk as bw
        if self.span.get("terminal_banks"):
            raise ValueError("terminal seating depends on assembly/carriers; no terrain-only reuse")
        facts = bw.asm.facts()
        primitives, functions = [], []
        for module in (bw, bw.asm, bw.routemod):
            values = [(key, value) for key, value in vars(module).items()
                      if isinstance(value, (bool, int, float, str, type(None)))]
            codes = [(key, value.__code__) for key, value in vars(module).items()
                     if hasattr(value, '__code__')]
            primitives.append((module.__name__, tuple(sorted(values))))
            functions.append((module.__name__, tuple(sorted(codes))))
        geometry = tuple(sorted((key, tuple(value.lo), tuple(value.hi))
                                for key, value in facts.items()))
        return (json.dumps(self.span, sort_keys=True), self.datum,
            bw.land_seated_deck_stations.__code__, tuple(primitives),
            tuple(functions), geometry, bw.road_instruments.STEP_LIMIT_PER_M,
            bw.asm.min_bay_m())

    def blocked(self, finished, *, token=None):
        token = self.token() if token is None else token
        for failure in self.failures:
            if failure['token'] == token and all(
                    finished(x,z) == height for x,z,height in failure['reads']):
                self.hits += 1
                return failure
        return None

    def probe(self, finished):
        import boardwalk as bw
        token = self.token()
        cached = self.blocked(finished, token=token)
        if cached is not None:
            return cached
        reads = {}
        def measured(x,z):
            value = finished(x,z)
            reads[(float(x),float(z))] = value
            return value
        bed = bw.asm.Bed.applied(measured,
            'PLANNED physical-query surface, exact canonical seating dependencies')
        try:
            bw.land_seated_deck_stations(self.span, bed, self.datum, report={})
        except SystemExit as error:
            vertices = set()
            for x,z in reads:
                x0,z0 = math.floor(x),math.floor(z)
                for sx in ((x0,) if x == x0 else (x0,x0+1)):
                    for sz in ((z0,) if z == z0 else (z0,z0+1)):
                        vertices.add((sx,sz))
            failure = dict(token=token, why=str(error), reads=tuple(
                (x,z,height) for (x,z),height in reads.items()),
                vertices=tuple(sorted(vertices)))
            self.failures.append(failure)
            return failure
        return None

    def summary(self):
        return dict(canonical_callee='boardwalk.land_seated_deck_stations',
            certificates=len(self.failures), equivalent_pairs_reused=self.hits,
            refusals=[dict(why=f['why'], terrain_queries=len(f['reads']),
                           bound_vertices=len(f['vertices'])) for f in self.failures])
