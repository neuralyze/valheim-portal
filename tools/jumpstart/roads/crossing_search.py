"""Internal caches for the pure physical crossing query; no world or scan I/O."""
from __future__ import annotations

import heapq
import json
import math

from array import array
from collections import OrderedDict
from types import MappingProxyType

import route as _route


_NATIVE_CORRIDOR = _route.Corridor
_NATIVE_STEP = _route.Corridor._step_cost
_NATIVE_BLOCKED = _route.Corridor.blocked
_NATIVE_CODES = (_NATIVE_STEP.__code__, _NATIVE_BLOCKED.__code__)
_EDGE_ARRAYS = ("water", "water_forbidden", "S", "rough", "soft", "Bi", "fillable")
_EDGE_VALUES = ("m", "cell_m", "x0", "z0", "grade_max", "grade_weight",
                "roughness_weight", "poi_weight", "bridge_per_m_cost",
                "bridge_entry_cost_m", "forbid_all_water", "shore_cost_mult")


def _edge_policy():
    if (_route.Corridor is not _NATIVE_CORRIDOR
            or _route.Corridor._step_cost is not _NATIVE_STEP
            or _route.Corridor.blocked is not _NATIVE_BLOCKED
            or (_NATIVE_STEP.__code__, _NATIVE_BLOCKED.__code__) != _NATIVE_CODES):
        return None
    return (_route.GRADE_CEILING, tuple(sorted(_route.BIOME_NAMES.items())))


class EdgeCosts:
    """One bank's immutable native edge data, shared by mask-only tree copies.

    Construct once, then shallow-copy ``corr`` for every base/masked search.
    Only ``forbid`` may change. The caller's original mutable arrays are NOT
    admitted: owning copies prevents undetectable in-place terrain changes.
    Incompatible trees use their real predicate, never a cached refusal.

    Eight directed slots per source node retain binary64 costs exactly. Pages
    of 256 nodes are allocated only when first visited (18 KiB/page), with a
    separate status byte for unknown/refused/value. No cost precomputation,
    tuple-key edge dictionary, candidate cap, or unknown-as-refused sentinel.
    This pool and its trees have one sequential owner; do not mutate ``corr``
    or its owned arrays/policy, and discard the pool with its bank snapshot.
    """
    def __init__(self, base_corr):
        import copy
        import numpy as np
        policy = _edge_policy()
        if (policy is None or type(base_corr) is not _NATIVE_CORRIDOR
                or any(name in vars(base_corr) for name in ("_step_cost", "blocked"))):
            raise ValueError("edge sharing requires the unmodified native Corridor policy")
        values = tuple(getattr(base_corr, name) for name in _EDGE_VALUES)
        if any(type(value) not in (int, float, bool) for value in values):
            raise ValueError("edge sharing requires native scalar cost inputs")
        penalties = base_corr.biome_penalty
        if (type(penalties) not in (dict, MappingProxyType)
                or any(type(key) is not str or type(value) not in (int, float)
                       for key, value in penalties.items())):
            raise ValueError("edge sharing requires native biome multipliers")
        self.corr = copy.copy(base_corr)
        for name in _EDGE_ARRAYS:
            value = getattr(base_corr, name)
            if name == "soft" and value is None:
                continue
            if (type(value) is not np.ndarray or value.shape != (base_corr.m, base_corr.m)
                    or name == "S" and value.dtype != np.dtype("float64")):
                raise ValueError("edge sharing requires native corridor arrays")
            # Immutable bytes backing also prevents re-enabling WRITEABLE on
            # an exposed owning ndarray and silently poisoning cached costs.
            owned = np.frombuffer(value.tobytes(), dtype=value.dtype).reshape(value.shape)
            setattr(self.corr, name, owned)
        self.corr.biome_penalty = MappingProxyType(dict(penalties))
        self.corr.forbid = None if base_corr.forbid is None else base_corr.forbid.copy()
        if self.corr.forbid is not None:
            self.corr.forbid.flags.writeable = False
        self._arrays = tuple(getattr(self.corr, name) for name in _EDGE_ARRAYS)
        self._values = values
        self._penalties = self.corr.biome_penalty
        self._policy = policy
        # Masks are tested by the tree, not learned as geometric refusals.
        self._native = copy.copy(self.corr)
        self._native.forbid = None
        self._pages = {}
        self.evaluated = 0

    def compatible(self, corr):
        """O(number of native inputs), not an O(grid) hash for every goal."""
        return (type(corr) is _NATIVE_CORRIDOR and _edge_policy() == self._policy
                and not any(name in vars(corr) for name in ("_step_cost", "blocked"))
                and tuple(getattr(corr, name) for name in _EDGE_VALUES) == self._values
                and corr.biome_penalty is self._penalties
                and all(getattr(corr, name) is value
                        for name, value in zip(_EDGE_ARRAYS, self._arrays)))

    def _cost(self, current, direction, a, b, distance):
        page_id, local = divmod(current, 256)
        page = self._pages.get(page_id)
        if page is None:
            page = (bytearray(256*8), array("d", [0.0])*(256*8))
            self._pages[page_id] = page
        states, costs = page
        slot = local*8+direction
        state = states[slot]
        if not state:
            value = self._native._step_cost(a, b, distance)
            self.evaluated += 1
            if value is None:
                states[slot] = 1
                return None
            costs[slot] = value
            states[slot] = 2
            return value
        return None if state == 1 else costs[slot]


def approach_tree(trees, corr, start, *, base):
    """Select a bank-owned exact-mask tree, retaining eight masked searches.

    ``trees["edge_costs"]`` is the optional immutable EdgeCosts owner. Its
    compatible base tree stays in ``trees["base"] = (key, tree)`` for exhaustion
    consumers; masked trees have a separate bounded LRU. Neither eviction nor
    unsupported data is a refusal: either creates a real, fresh ApproachTree.
    ``base`` only selects the bucket; both buckets key the complete mask.
    """
    import hashlib
    import numpy as np
    pool = trees.get("edge_costs")
    supported = isinstance(pool, EdgeCosts) and pool.compatible(corr)
    mask = corr.forbid
    if mask is not None:
        supported = (supported and type(mask) is np.ndarray
                     and mask.shape == (corr.m, corr.m) and mask.dtype == np.dtype("bool"))
    owner = pool if supported else None
    if not supported or trees.get("_edge_tree_owner") is not owner:
        for name in ("base", "masked", "masked_lru"):
            trees.pop(name, None)
        trees["_edge_tree_owner"] = owner
    digest = None if mask is None else hashlib.sha256(mask.tobytes()).digest()
    key = (start, digest)
    if not supported:
        # Without an immutable owner there is no safe cheap identity for
        # changed terrain or custom policy. Keep native evaluation, not stale
        # closed sets/refusals from a previous unknown snapshot.
        tree = ApproachTree(corr, start)
        trees["base" if base else "masked"] = (key, tree)
        return tree
    if base:
        entry = trees.get("base")
        tree = None if entry is None or entry[0] != key else entry[1]
    else:
        masked = trees.setdefault("masked_lru", OrderedDict())
        tree = masked.pop(key, None)
    if tree is not None:
        saved = tree.corr.forbid
        if not ((saved is None and mask is None)
                or saved is not None and mask is not None and np.array_equal(saved, mask)):
            # Verify equality even after a digest hit: only an exact mask may
            # share Dijkstra state.
            tree = None
    if tree is None:
        tree = ApproachTree(corr, start, edge_costs=pool)
    if base:
        trees["base"] = (key, tree)
    else:
        masked[key] = tree
        while len(masked) > 8:
            masked.popitem(last=False)
    return tree


def empty_bank_key(graph, corr, bank, minimum_run):
    """Identity of routing inputs, not rendered wet/compiled-road refusals."""
    import hashlib
    import numpy as np
    import route
    if type(corr) is not route.Corridor:
        return None
    digest = hashlib.sha256()
    for name, value in sorted(vars(corr).items()):
        if name == "fld":
            # The canonical edge predicate reads derived corridor arrays.
            continue
        if isinstance(value, np.ndarray):
            digest.update(repr((name, value.dtype.str, value.shape)).encode())
            digest.update(value.tobytes())
        elif value is None or isinstance(value, (bool, int, float, str, dict)):
            digest.update(repr((name, value)).encode())
        else:
            return None
    for name in ("lab", "land_lab"):
        value = getattr(graph, name)
        digest.update(repr((name, value.dtype.str, value.shape)).encode())
        digest.update(value.tobytes())
    policy = (route.GRADE_CEILING, tuple(sorted(route.BIOME_NAMES.items())),
              graph.big, tuple(map(float, bank["xz"])), minimum_run)
    return (digest.digest(), policy,
            route.Corridor._step_cost, route.Corridor.blocked,
            route.Corridor.nearest_allowed, route.Corridor.cell_of,
            route.Corridor.cell_world)


class ApproachTree:
    """Incremental single-source search using Corridor's actual edge predicate.

    The caller owns an immutable corridor/mask snapshot for this tree. A changed
    water-feedback mask requires a different tree, never an update in place.
    Goal settlement uses Dijkstra, so all returned paths minimise exactly the
    same directed edge cost as Corridor._astar (including diagonal steps).
    """
    def __init__(self, corr, start, edge_costs=None):
        import copy
        self.corr = copy.copy(corr)
        self.corr.forbid = None if corr.forbid is None else corr.forbid.copy()
        self.edge_costs = (edge_costs if edge_costs is not None
                           and edge_costs.compatible(self.corr) else None)
        self.start = start[0]*corr.m+start[1]
        self.cost = {self.start: 0.0}
        self.parent = {}
        self.closed = set()
        self.queue = [(0.0, self.start)]
        self.expanded = 0

    def path_to(self, goal):
        corr, m = self.corr, self.corr.m
        edge_costs = self.edge_costs
        if edge_costs is not None and not edge_costs.compatible(corr):
            # New policy/data must use native evaluation. As with uncached
            # trees, callers must create a fresh tree after changing inputs.
            edge_costs = None
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
            for direction, (di, dj, scale) in enumerate(neighbours):
                ni, nj = i+di, j+dj
                if not (0 <= ni < m and 0 <= nj < m):
                    continue
                node = ni*m+nj
                if node in self.closed:
                    continue
                if edge_costs is None:
                    edge = corr._step_cost((i,j), (ni,nj), scale*corr.cell_m)
                elif corr.blocked(ni, nj):
                    continue
                else:
                    edge = edge_costs._cost(current, direction, (i,j), (ni,nj),
                                           scale*corr.cell_m)
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
