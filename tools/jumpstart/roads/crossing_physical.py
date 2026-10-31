"""Seed-derived crossing eligibility through the production writer and builder.

This is a planned-construction proof, not evidence that terrain/pieces were
written to a saved world. The input is the same content-bound rendered integer
Heightmap field as the rest of the pipeline; no scan or ledger write occurs here.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from types import MappingProxyType

import numpy as np

import field as fieldmod
import route
import worldseed
from crossing_contract import (
    BANK_LEVEL_M, WIDTH_M, STATION_M, SIMPLIFY_TOL_M,
    straighten, transverse_window)


class CoverageError(ValueError):
    """Missing measured input is not a measured physical impossibility."""


class PhysicalFields:
    def __init__(self, fields, provenance, *, keepout_discs=None):
        self.fields = tuple(sorted((f for f in fields.values()
            if abs(f.step - 1.0) < 1e-9
            and abs(f.x0 - round(f.x0)) < 1e-6
            and abs(f.z0 - round(f.z0)) < 1e-6), key=lambda f: f.id))
        self.provenance = provenance
        # Explicit fixture worlds may have an empty location population.
        # from_field never supplies this override: production reads poi.
        self.keepout_discs = keepout_discs
        if not self.fields:
            raise CoverageError("no measured integer 1 m Heightmap lattice; "
                                "rerun roads/field.py request")
        self._patches = {}
        # Frames are fixed measurement metadata. Index their extents once,
        # while retaining every overlapping source and reading heights afresh.
        self._tiles = {}
        pitch = fieldmod.HEIGHTMAP_ZONE_M
        for f in self.fields:
            x0, z0 = f.x0, f.z0
            x1, z1 = x0+f.n-1, z0+f.n-1
            entry = (f, x0, x1, z0, z1)
            for iz in range(math.floor((z0-1e-6)/pitch),
                            math.floor((z1+1e-6)/pitch)+1):
                for ix in range(math.floor((x0-1e-6)/pitch),
                                math.floor((x1+1e-6)/pitch)+1):
                    self._tiles.setdefault((ix, iz), []).append(entry)
        self._tiles = MappingProxyType({
            tile: tuple(entries) for tile, entries in self._tiles.items()})

    def _covering(self, x, z, tolerance=0.0):
        pitch = fieldmod.HEIGHTMAP_ZONE_M
        tile = (math.floor(x/pitch), math.floor(z/pitch))
        return (f for f, x0, x1, z0, z1 in self._tiles.get(tile, ())
                if x0-tolerance <= x <= x1+tolerance
                and z0-tolerance <= z <= z1+tolerance)

    @classmethod
    def from_field(cls, path, fields=None):
        path = Path(path)
        fieldmod.require_seed(path, worldseed.seed())
        manifest = path.with_suffix(path.suffix + ".source.json")
        if not manifest.exists():
            raise CoverageError(f"{path}: no rendered Heightmap source manifest "
                f"at {manifest}; rerun roads/field.py request for this seed. "
                "A lattice alone cannot prove Heightmap rather than GetHeight.")
        source = json.loads(manifest.read_text())
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if (source.get("surface") != "heightmap"
                or source.get("seed") != worldseed.seed()
                or source.get("sha256") != digest
                or source.get("producer") != "roads.field.request"):
            raise CoverageError(f"{manifest}: source/seed/content mismatch; "
                                "rerun roads/field.py request, never relabel an old field")
        return cls(fieldmod.load(path) if fields is None else fields,
                   dict(source, path=str(path.resolve())))

    def require_field(self, path):
        path = Path(path).resolve()
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if (self.provenance.get("path") != str(path)
                or self.provenance.get("sha256") != digest
                or self.provenance.get("seed") != worldseed.seed()):
            raise CoverageError("physical context is not bound to this --field's "
                                "current seed/path/content")

    def vertex(self, x, z):
        if abs(x - round(x)) > 1e-6 or abs(z - round(z)) > 1e-6:
            raise CoverageError(f"non-integer Heightmap vertex ({x}, {z})")
        values = [f.at(x, z) for f in self._covering(x, z, 1e-6)]
        if not values or not all(math.isfinite(v) for v in values):
            raise CoverageError(f"unread rendered Heightmap vertex ({x}, {z})")
        if max(values) - min(values) > 1e-4:
            raise CoverageError(f"overlapping Heightmaps disagree at ({x}, {z}): {values}")
        return values[0]

    def generated(self, x, z):
        x0, z0 = math.floor(x), math.floor(z)
        tx, tz = x - x0, z - z0
        return sum(w * self.vertex(x0 + dx, z0 + dz)
            for dx, dz, w in ((0, 0, (1-tx)*(1-tz)), (1, 0, tx*(1-tz)),
                             (0, 1, (1-tx)*tz), (1, 1, tx*tz)) if w)

    def sample_many(self, xs, zs):
        """Batch native float64 probes; preserve custom sampler dispatch."""
        xs, zs = np.broadcast_arrays(np.asarray(xs, dtype=np.float64),
                                     np.asarray(zs, dtype=np.float64))
        native = (all(type(f) is fieldmod.Field for f in self.fields)
                  and getattr(self.generated, "__func__", None) is PhysicalFields.generated
                  and getattr(self.vertex, "__func__", None) is PhysicalFields.vertex
                  and getattr(self._covering, "__func__", None) is PhysicalFields._covering
                  and getattr(self.contains, "__func__", None) is PhysicalFields.contains)
        if not native:
            values = np.array([self.generated(float(x), float(z))
                               for x, z in zip(xs.flat, zs.flat)],
                              dtype=np.float64).reshape(xs.shape)
        else:
            from crossing_sampling import generated_many
            values = generated_many(self.fields, self._tiles, xs, zs, CoverageError)
        if native and all(f.step == 1.0 and f.x0 == round(f.x0)
                          and f.z0 == round(f.z0) for f in self.fields):
            # Successful generated() checked precisely these required vertices.
            covered = np.ones(xs.shape, dtype=bool)
        else:
            covered = np.array([self.contains(float(x), float(z))
                                for x, z in zip(xs.flat, zs.flat)],
                               dtype=bool).reshape(xs.shape)
        return values, covered

    def contains(self, x, z):
        x0, z0 = math.floor(x), math.floor(z)
        xs = (x0,) if x == x0 else (x0, x0+1)
        zs = (z0,) if z == z0 else (z0, z0+1)
        return all(next(self._covering(sx, sz), None) is not None
                   for sx in xs for sz in zs)

    def patches(self, zones):
        # The shipping raster constructor, not a reinterpreted water.Field.
        import ribbon
        for zx, zz in zones:
            key = f"z_{zx}_{zz}"
            if key not in self._patches:
                self._patches[key] = ribbon._flat_patch(zx, zz, self.vertex)
        return {f"z_{zx}_{zz}": self._patches[f"z_{zx}_{zz}"] for zx, zz in zones}

    def bind_approach_graphs(self, near_graph, far_graph):
        if any(graph is None or not hasattr(graph, "corr")
               for graph in (near_graph, far_graph)):
            raise CoverageError("physical approach search requires the actual "
                                "geography RoadGraph for each bank")
        self.approach_graphs = near_graph, far_graph

    def routed_approach(self, graph, bank, goal, datum, *, report=None,
                        _trees=None, _base=None):
        """Production A* plus its water-stand-off reroute, with a fixed bank.

        Only canonical footprint-wet feedback grows the forbidden mask. There
        is no retry cap: the finite graph either yields a dry routed bench,
        exhausts, or reaches a fixed-point mask with an explicit refusal.
        """
        import copy
        import ribbon
        from types import SimpleNamespace
        corr = copy.copy(graph.corr if _base is None else _base)
        if _base is None:
            corr.forbid = ~graph.ok
        corr.forbid_all_water = True
        base_forbid = corr.forbid
        trees = {} if _trees is None else _trees
        actual = tuple(map(float, bank["xz"]))
        goal_cell = corr.cell_of(*goal)
        sampler = SimpleNamespace(bilinear=self.generated, contains=self.contains,
                                  sample_many=self.sample_many)
        width = max(WIDTH_M.values())
        rounds = 0
        def refuse(reason):
            if report is not None:
                report.update(refusal=reason, water_reroute_rounds=rounds)
            return None
        while True:
            try:
                start = corr.nearest_allowed(*actual)
            except ValueError:
                return refuse("no allowed fixed-bank snap under the canonical water mask")
            if corr.blocked(*goal_cell):
                return refuse("the fixed inland handoff is forbidden by the canonical water mask")
            from crossing_search import approach_tree
            tree = approach_tree(trees, corr, start,
                                 base=corr.forbid is base_forbid)
            raw = tree.path_to(goal_cell)
            if raw is None:
                return refuse("canonical A* exhausted the allowed graph")
            if math.dist(actual, raw[0]) > 1e-9:
                raw = [actual, *raw]
            points, s = route.resample(
                route.simplify(raw, tol_m=SIMPLIFY_TOL_M), STATION_M)
            if len(points) < 2:
                return refuse("canonical route has fewer than two distinct stations")
            last = int(np.flatnonzero(np.asarray(s) <= BANK_LEVEL_M)[-1])
            points = straighten(points, s, 0, last)
            s = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
            wet = []
            _low, high, _off = transverse_window(sampler, points, width, wet_at=wet)
            if not wet:
                if report is not None:
                    report.update(refusal=None, water_reroute_rounds=rounds)
                return {"nodes": points, "goal_xz": list(map(float, goal)),
                        "bank_snap_m": math.dist(actual, corr.cell_world(*start)),
                        "water_reroute_rounds": rounds}
            seeds = [(x, z, max(high[k]-route.CUT_FILL_MAX,
                datum if s[k] <= BANK_LEVEL_M else
                fieldmod.WATER_LEVEL+route.ROAD_FREEBOARD_M)) for x, z, k in wet]
            extra = route.water_standoff_forbid(
                corr, seeds, half_m=width/2.0, grade=ribbon.BATTER_GRADE,
                reach_m=ribbon.BATTER_MAX_M)
            changed = extra & ~corr.forbid
            if report is not None:
                report.setdefault("water_feedback", []).append({
                    "round": rounds, "bank_snap_xz": list(corr.cell_world(*start)),
                    "wet_footprint_probes": len(wet),
                    "newly_forbidden_cells": int(changed.sum()),
                    "wet_x_interval": [min(x for x, _z, _k in wet),
                                       max(x for x, _z, _k in wet)],
                    "wet_z_interval": [min(z for _x, z, _k in wet),
                                       max(z for _x, z, _k in wet)]})
            if not changed.any():
                return refuse("canonical water-stand-off mask reached a wet-footprint fixed point")
            corr.forbid = corr.forbid | extra
            rounds += 1

    def _bank_clearance_certificate(self, graph, bank):
        """Optional necessary footprint proof, including tiny/duplicate stations."""
        import crossing_contract as contract
        from crossing_bank_clearance import wet_bank_certificate

        if (type(self) is not PhysicalFields
                or not all(type(f) is fieldmod.Field for f in self.fields)
                or getattr(self.generated, "__func__", None) is not PhysicalFields.generated
                or getattr(self.vertex, "__func__", None) is not PhysicalFields.vertex
                or getattr(self._covering, "__func__", None) is not PhysicalFields._covering
                or getattr(self.contains, "__func__", None) is not PhysicalFields.contains
                or getattr(self.sample_many, "__func__", None) is not PhysicalFields.sample_many
                or getattr(self.routed_approach, "__func__", None)
                    is not PhysicalFields.routed_approach):
            return None
        corr = graph.corr
        epsilon = contract.TANGENT_MIN_NORM_M
        minimum_run = BANK_LEVEL_M+STATION_M
        if (type(corr) is not route.Corridor or corr.m < 2
                or not all(math.isfinite(v) and v > 0
                           for v in (corr.cell_m, STATION_M, epsilon, minimum_run))):
            return None
        x, z = bank["xz"]
        side = (corr.m-1)*corr.cell_m
        if not (corr.x0 <= x <= corr.x0+side and corr.z0 <= z <= corr.z0+side):
            return None
        # A positive-cost tree path visits at most m² grid vertices. Adding
        # the actual bank costs at most one corridor diagonal; simplification
        # shortens arc length, resampling adds at most arc/spacing plus one
        # endpoint per input segment, and straighten preserves station count.
        arc_bound = (corr.m*corr.m+corr.m+2)*math.sqrt(2)*corr.cell_m
        station_bound = math.ceil(arc_bound/STATION_M)+corr.m*corr.m+8
        # Until the first non-clipped central difference, P[i+1] is within
        # 2*epsilon of P[i-1]. Thus that first unit normal's station lies within
        # this box of the bank, even with repeated/tiny leading stations.
        # An endpoint >= minimum_run away cannot have ONLY clipped tangents.
        origin_tolerance = 2*(station_bound+2)*max(epsilon, 1e-9)
        if origin_tolerance >= minimum_run/4:
            return None
        proof = wet_bank_certificate(self, bank,
            route.footprint_half_m(max(WIDTH_M.values())),
            origin_tolerance_m=origin_tolerance,
            mandatory_offsets_m=contract.footprint_offsets(max(WIDTH_M.values())))
        if proof is not None:
            proof["canonical_station_count_upper_bound"] = station_bound
            proof["minimum_handoff_distance_m"] = minimum_run
        return proof

    def _route_catalog_binding(self, graph):
        """Bind native routing inputs; compiler state is intentionally excluded."""
        import types
        import crossing_contract as contract
        import crossing_sampling as sampling
        import crossing_search as search
        import crossing_bank_clearance as clearance
        import ribbon
        from crossing_route_catalog import native_frame_binding

        if (type(self) is not PhysicalFields
                or type(self._tiles) is not MappingProxyType
                or type(graph.corr) is not route.Corridor):
            return None
        methods = ("generated", "vertex", "_covering", "contains", "sample_many",
                   "routed_approach", "_bank_clearance_certificate",
                   "_route_paths_uncached")
        if any(getattr(getattr(self, name), "__func__", None)
               is not getattr(PhysicalFields, name) for name in methods):
            return None

        def plain(value):
            if type(value) in (type(None), bool, int, float, str, bytes):
                return value
            if isinstance(value, (types.CodeType, Path)):
                return value
            if type(value) in (tuple, list):
                return tuple(plain(item) for item in value)
            if type(value) is dict:
                return tuple((plain(key), plain(item)) for key, item in value.items())
            raise TypeError("noncanonical mutable policy")

        def function_token(fn):
            return (fn, fn.__code__, plain(fn.__defaults__),
                    plain(fn.__kwdefaults__))

        try:
            token = [id(self._tiles), tuple(function_token(
                getattr(PhysicalFields, name)) for name in methods)]
            # Include helper code/defaults and the plain configuration it reads.
            # Keeping this local also detects policy edits while a generator is
            # suspended. No rendered height array is copied or hashed.
            for module in (route, contract, sampling, search, clearance, fieldmod):
                functions = tuple(function_token(value)
                    for value in vars(module).values()
                    if isinstance(value, types.FunctionType)
                    and value.__module__ == module.__name__)
                constants = tuple((name, plain(value))
                    for name, value in vars(module).items()
                    if name.isupper() and type(value) in
                    (type(None), bool, int, float, str, bytes, tuple, list, dict))
                token.append((module, functions, constants))
            for cls in (fieldmod.Field, route.Corridor,
                        search.ApproachTree, search.EdgeCosts):
                token.append((cls, tuple(function_token(value)
                    for name, value in vars(cls).items()
                    if isinstance(value, types.FunctionType)
                    and not (name == "__init__"
                             and cls in (fieldmod.Field, route.Corridor)))))
            token.extend((plain(WIDTH_M), BANK_LEVEL_M, STATION_M,
                          SIMPLIFY_TOL_M, ribbon.BATTER_GRADE,
                          ribbon.BATTER_MAX_M,
                          tuple(function_token(getattr(route.ndimage, name))
                                for name in ("label", "binary_dilation",
                                             "distance_transform_edt"))))
            return native_frame_binding((*self.fields, graph.corr.fld),
                                        policy_token=tuple(token))
        except (AttributeError, TypeError, ValueError):
            return None

    def _route_paths(self, graph, bank, stats):
        """Replay only exhausted immutable routing, never side compilation."""
        import copy
        from crossing_route_catalog import RouteCatalog
        from crossing_search import empty_bank_key

        binding = lambda: self._route_catalog_binding(graph)
        if binding() is None:
            catalog = self.__dict__.get("_route_catalog")
            if catalog is not None:
                catalog.clear()
            yield from self._route_paths_uncached(graph, bank, stats)
            return

        def signature():
            corr = copy.copy(graph.corr)
            corr.forbid = ~graph.ok
            corr.forbid_all_water = True
            key = empty_bank_key(graph, corr, bank, BANK_LEVEL_M+STATION_M)
            return None if key is None else (key, float(bank["level_y"]))

        catalog = self.__dict__.get("_route_catalog")
        if catalog is None:
            catalog = self._route_catalog = RouteCatalog()
        yield from catalog.routes(signature=signature(),
            current_signature=signature, binding=binding,
            producer=lambda local: self._route_paths_uncached(graph, bank, local),
            stats=stats)

    def _route_paths_uncached(self, graph, bank, stats):
        """Covered handoffs on the complete graph; one shared base search tree."""
        import copy
        import sys
        import time
        corr = copy.copy(graph.corr)
        corr.forbid = ~graph.ok
        corr.forbid_all_water = True
        from crossing_search import empty_bank_key
        key = empty_bank_key(graph, corr, bank, BANK_LEVEL_M+STATION_M)
        empty_banks = self.__dict__.setdefault("_empty_bank_routes", {})
        if key is not None and key in empty_banks:
            stats.update(empty_banks[key])
            stats["empty_bank_reuses"] = 1
            return
        trees = {}
        try:
            start = corr.nearest_allowed(*bank["xz"])
        except ValueError:
            stats["no_bank_snap"] = stats.get("no_bank_snap", 0)+1
            return
        label = int(graph.land_lab[start])
        if label == 0:
            return
        ii, jj = np.nonzero(graph.ok & ~corr.water
            & (graph.lab == graph.big) & (graph.land_lab == label))
        xs, zs = corr.x0+jj*corr.cell_m, corr.z0+ii*corr.cell_m
        distance = np.hypot(xs-bank["xz"][0], zs-bank["xz"][1])
        goals = np.flatnonzero(distance >= BANK_LEVEL_M+STATION_M)
        goals = goals[np.argsort(distance[goals], kind="stable")]
        stats["eligible_graph_goals"] = len(goals)
        certificate = self._bank_clearance_certificate(graph, bank)
        if certificate is not None:
            stats.update(goals_considered=len(goals), router_refused=len(goals),
                         route_queries=0, base_search_expansions=0,
                         footprint_proven_impossible=len(goals),
                         bank_clearance_certificate=certificate)
            return
        from crossing_search import EdgeCosts
        try:
            edge_costs = EdgeCosts(corr)
        except ValueError:
            pass  # Custom routing data/policy retains native evaluation.
        else:
            corr = edge_costs.corr
            trees["edge_costs"] = edge_costs
        next_progress = time.monotonic()+30.0
        all_goals = goals
        position = 0
        pruned = False
        while position < len(goals):
            index = goals[position]
            position += 1
            stats["goals_considered"] = stats.get("goals_considered",0)+1
            now = time.monotonic()
            if now >= next_progress:
                print(f"crossing bank {bank['xz']}: handoffs "
                      f"{stats['goals_considered']}/{len(goals)}, "
                      f"router refusals {stats.get('router_refused',0)}",
                      file=sys.stderr, flush=True)
                next_progress = now+30.0
            try:
                stats["route_queries"] = stats.get("route_queries",0)+1
                path = self.routed_approach(graph, bank,
                    (float(xs[index]),float(zs[index])), float(bank["level_y"]),
                    _trees=trees, _base=corr)
            except CoverageError as error:
                # This endpoint is OPTIONAL, not a chosen construction. The
                # mandatory crossing request was checked before enumeration.
                stats["optional_goals_uncovered"] = stats.get("optional_goals_uncovered",0)+1
                stats.setdefault("representative_optional_coverage",str(error))
                continue
            stats["base_search_expansions"] = (
                trees["base"][1].expanded if "base" in trees else 0)
            tree = trees.get("base")
            if tree is not None and not tree[1].queue and not pruned:
                closed = tree[1].closed
                remaining = goals[position:]
                reachable = np.fromiter(
                    (int(ii[g])*corr.m+int(jj[g]) in closed for g in remaining),
                    dtype=bool, count=len(remaining))
                eliminated = int((~reachable).sum())
                stats["goals_considered"] += eliminated
                stats["router_refused"] = stats.get("router_refused",0)+eliminated
                stats["goals_proven_unreachable"] = eliminated
                goals, position, pruned = remaining[reachable], 0, True
                if key is not None and not any(
                        int(ii[g])*corr.m+int(jj[g]) in closed for g in all_goals):
                    if len(empty_banks) >= 64:
                        empty_banks.pop(next(iter(empty_banks)))
                    empty_banks[key] = {
                        "eligible_graph_goals": len(all_goals),
                        "goals_considered": len(all_goals),
                        "router_refused": len(all_goals),
                        "base_search_expansions": tree[1].expanded,
                        "route_queries": 0,
                        "goals_proven_unreachable": len(all_goals)}
            if path is not None:
                yield path
            else:
                stats["router_refused"] = stats.get("router_refused",0)+1
        stats["base_search_expansions"] = (
            trees["base"][1].expanded if "base" in trees else 0)

    def _span(self, crossing, near_bank, far_bank, datum):
        return dict(crossing_id=crossing["id"], segment=f"geography-{crossing['id']}",
            bank_a=dict(xz=list(near_bank["xz"]), ground_y=self.generated(*near_bank["xz"])),
            bank_b=dict(xz=list(far_bank["xz"]), ground_y=self.generated(*far_bank["xz"])),
            span_m=math.dist(near_bank["xz"], far_bank["xz"]),
            deck_width_m=max(WIDTH_M.values()), bank_level_run_m=BANK_LEVEL_M,
            single_H_both_banks=True, deck_y_REQUIRED=datum,
            legal_deck_y_interval=[datum,datum], water_surface_y=fieldmod.WATER_LEVEL,
            terminal_banks=[])

    def _side_options(self, crossing, bank, other_bank, graph, datum, side, stats):
        seen = set()
        for path in self._route_paths(graph, dict(bank,level_y=datum), stats):
            key = np.asarray(path["nodes"],dtype="<f8").tobytes()
            if key in seen:
                stats["identical_route_reuses"] = stats.get("identical_route_reuses",0)+1
                continue
            seen.add(key)
            try:
                plan = self._compile_side(crossing,bank,other_bank,datum,path,side)
            except CoverageError as error:
                stats["optional_construction_uncovered"] = stats.get(
                    "optional_construction_uncovered",0)+1
                stats.setdefault("representative_optional_construction_coverage",str(error))
                continue
            if plan["ok"]:
                stats["legal_approach_plans"] = stats.get("legal_approach_plans",0)+1
                yield plan
            else:
                stage = plan["stage"]
                counts = stats.setdefault("refusal_counts",{})
                counts[stage] = counts.get(stage,0)+1
                stats.setdefault("representative_refusals",{}).setdefault(stage,plan)

    def candidate(self, crossing, near_bank, far_bank, datum):
        import boardwalk
        from crossing_search import SeatingRefusals
        if not hasattr(self,"approach_graphs"):
            raise CoverageError("physical approach graphs have not been bound")
        span = self._span(crossing,near_bank,far_bank,datum)
        if span["span_m"] <= 0.0:
            return dict(ok=False,status="refused",stage="banks",why=["coincident walked banks"])
        # Fixed canonical construction request, independent of optional goals.
        # Missing here is REQUIRED coverage and still refuses immediately.
        self.patches(boardwalk.span_zones(span))
        memo = SeatingRefusals(span,datum)
        self._compiler_pool = {}
        self._bank_pin_failures = {}
        stats = [{},{}]
        def bank_evidence():
            # Cache scheduling is execution telemetry, not geometric evidence.
            execution = {"route_queries", "empty_bank_reuses", "goals_proven_unreachable",
                         "base_search_expansions", "route_catalog_reuses"}
            return [{k:v for k,v in row.items() if k not in execution} for row in stats]
        generators = [iter(self._side_options(crossing,bank,other,graph,datum,side,
            stats[side])) for side,(bank,other,graph) in enumerate((
                (near_bank,far_bank,self.approach_graphs[0]),
                (far_bank,near_bank,self.approach_graphs[1])))]
        plans, exhausted = [[],[]], [False,False]
        indexes = [({},-1),({},-1)]
        counts, examples = {},{}
        actual_pairs = equivalent_groups = 0

        def signature(plan):
            return tuple(tuple(self._delta_at(plan["comps"],x,z)
                for x,z in failure["vertices"]) for failure in memo.failures)

        def grouped(side):
            groups, revision = indexes[side]
            if revision != len(memo.failures):
                groups = {}
                for index,plan in enumerate(plans[side]):
                    key = signature(plan) if memo.failures else (index,)
                    groups.setdefault(key,[]).append(index)
                indexes[side] = (groups,len(memo.failures))
            return groups

        while not all(exhausted):
            for side in (0,1):
                if exhausted[side]:
                    continue
                try:
                    plan = next(generators[side])
                except StopIteration:
                    exhausted[side] = True
                    # A complete pair cannot exist without a plan on both
                    # banks. Do not exhaust the opposite island after this
                    # side has already proved the Cartesian product empty.
                    if not plans[side]:
                        break
                    continue
                index = len(plans[side])
                plans[side].append(plan)
                groups,revision = indexes[side]
                if revision == len(memo.failures):
                    key = signature(plan) if memo.failures else (index,)
                    groups.setdefault(key,[]).append(index)
                other = 1-side
                processed = set()
                restart = True
                while restart:
                    restart = False
                    policy = memo.token()
                    for partners in list(grouped(other).values()):
                        pending = [k for k in partners if k not in processed]
                        if not pending:
                            continue
                        pair = (plan,plans[other][pending[0]]) if side == 0 else (
                            plans[other][pending[0]],plan)
                        finished = lambda x,z,pair=pair: self._pair_height(pair,x,z)
                        if memo.blocked(finished,token=policy) is not None:
                            # Exact sampler dependency equivalence of the one
                            # canonical callee which DOES NOT consume routes.
                            equivalent_groups += 1
                            continue
                        for partner in pending:
                            processed.add(partner)
                            pair = (plan,plans[other][partner]) if side == 0 else (
                                plans[other][partner],plan)
                            old = len(memo.failures)
                            proof = self._evaluate_sides(span,pair,datum,memo)
                            actual_pairs += 1
                            if proof["ok"]:
                                proof["status"] = "verified"
                                proof["approach_search"] = dict(
                                    routed_pairs_tested=actual_pairs,
                                    equivalent_groups_pruned=equivalent_groups,
                                    banks=bank_evidence(),seating_dependencies=memo.summary(),
                                    frontier="all covered same-landmass graph goals; no radius/candidate cap")
                                return proof
                            stage = proof["stage"]
                            counts[stage] = counts.get(stage,0)+1
                            examples.setdefault(stage,proof)
                            if len(memo.failures) != old:
                                restart = True
                                break
                        if restart:
                            break
            if any(exhausted[k] and not plans[k] for k in (0,1)):
                break
        coverage = dict(
            optional_goals_uncovered=sum(s.get("optional_goals_uncovered",0) for s in stats),
            optional_construction_uncovered=sum(
                s.get("optional_construction_uncovered",0) for s in stats))
        skipped = sum(coverage.values())
        return dict(ok=False,
            status="unverified_coverage" if skipped else "refused",
            stage="coverage" if skipped else "routed_approach_search",
            why=["covered approaches exhausted with unresolved optional construction coverage"
                 if skipped else
                 "all evaluated canonical approach pairs failed the unchanged physical contract"],
            coverage=coverage,skipped_optional_total=skipped,
            banks=bank_evidence(),near_paths=len(plans[0]),far_paths=len(plans[1]),
            routed_pairs_tested=actual_pairs,equivalent_groups_pruned=equivalent_groups,
            refusal_counts=counts,representative_refusals=examples,
            seating_dependencies=memo.summary())


    def _compile_side(self,crossing,bank,other_bank,datum,path,side):
        """One actual approach road, fitted/rasterised ONCE, not once per pair.

        The other REAL bank is a bridge-marked clipping continuation; it creates
        no road terrain across the declared water span. Opposite-bank roads are
        separate segments in production, not one synthetic jointly fitted road.
        """
        import ribbon
        from types import SimpleNamespace
        road = list(reversed(path["nodes"]))
        points = np.asarray([*road,other_bank["xz"]],dtype=float)
        arc = np.r_[0.0,np.cumsum(np.linalg.norm(np.diff(points,axis=0),axis=1))]
        width = max(WIDTH_M.values())
        sampler = SimpleNamespace(bilinear=self.generated,contains=self.contains,
                                  sample_many=self.sample_many)
        pinned_road = arc[:-1] >= arc[-2]-BANK_LEVEL_M
        first = max(int(np.flatnonzero(pinned_road)[0])-1,0)
        neighbourhood = np.asarray(road[first:],dtype="<f8")
        held = pinned_road[first:]
        binding_key = (datum,width,route.CUT_FILL_MAX,fieldmod.WATER_LEVEL,
                       neighbourhood.tobytes(),held.tobytes())
        bindings = self.__dict__.setdefault("_bank_pin_failures",{})
        if binding_key in bindings:
            return bindings[binding_key]
        pin_low,pin_high,_ = transverse_window(sampler,neighbourhood,width)
        bad = held & ((pin_high-route.CUT_FILL_MAX > datum)
                      | (pin_low+route.CUT_FILL_MAX < datum)
                      | (pin_low < fieldmod.WATER_LEVEL))
        if bad.any():
            k = int(np.flatnonzero(bad)[0])
            refusal = dict(ok=False,stage="pinned_bank_constraint",
                why=["a canonical fixed-bank footprint interval excludes its required datum"],
                xz=neighbourhood[k].tolist(),required_y=datum,
                footprint_low_y=float(pin_low[k]),footprint_high_y=float(pin_high[k]),
                cut_fill_max_m=route.CUT_FILL_MAX)
            bindings[binding_key] = refusal
            return refusal
        low,high,_ = transverse_window(sampler,road,width)
        if np.any(low < fieldmod.WATER_LEVEL):
            return dict(ok=False,stage="approach_water",
                why=["the actually routed footprint crosses seed water"])
        natural = np.asarray([self.generated(*p) for p in points])
        lo,hi = np.r_[high-route.CUT_FILL_MAX,datum],np.r_[low+route.CUT_FILL_MAX,datum]
        bridge = np.zeros(len(points),dtype=bool)
        bridge[-1] = True
        pinned = (~bridge) & (arc >= arc[-2]-BANK_LEVEL_M)
        lo[~bridge & ~pinned] = np.maximum(lo[~bridge & ~pinned],
            fieldmod.WATER_LEVEL+route.ROAD_FREEBOARD_M)
        lo[pinned],hi[pinned] = np.maximum(lo[pinned],datum),np.minimum(hi[pinned],datum)
        # A graph handoff has no authored height pin. Its terrain may be graded
        # within the SAME cut/fill/freeboard bounds; the real stamp/rim/walk
        # checks below must prove its join. Pinning it to native ground was an
        # extra, non-production constraint (and made low shores spuriously empty).
        fitted = route.fit_ladder(natural,arc,lo,hi,bridge,
            sorted(set([*route.GRADE.values(),route.GRADE_CEILING])),
            pts=points,width_m=width,grade=ribbon.BATTER_GRADE,
            batter_m=ribbon.BATTER_MAX_M,lattice_m=1.0)
        if not fitted["terrain_feasible"] or fitted["joined"]["verdict"] == "REFUSED":
            return dict(ok=False,stage="approach_profile",
                why=["canonical fit_ladder has no legal approach"],
                infeasible_spans=fitted["fit"]["infeasible_spans"])
        seg = dict(id=f"geography-{crossing['id']}-bank-{side}",
            nodes=points.tolist(),profile_y=fitted["y"].tolist(),
            is_bridge=bridge.tolist(),width_m=width,length_m=float(arc[-1]),
            grade_limit=fitted["rung"])
        zones = sorted(set(ribbon.segment_zones(seg["nodes"],seg["is_bridge"],width)))
        patches = self.patches(zones)
        pads = ribbon.PadKeepOut([],[])
        discs = ribbon.poi_standoff_discs(zones) if self.keepout_discs is None else list(self.keepout_discs)
        comps,stats = ribbon.stamp(seg,patches,zones,pads,poi_keepouts=discs)
        judgement = self._judge_written(comps,patches,(seg,),stats["road_clip_at"])
        judgement["over_clamp"] = stats["over_clamp"]
        if stats["over_clamp"] or stats["road_clip_at"] or not judgement["ok"]:
            return dict(ok=False,stage="approach_writer",why=judgement)
        comps = self._intern_compilers(comps)
        return dict(ok=True,comps=comps,patches=patches,segment=seg,
                    road_nodes=road,road_levels=seg["profile_y"][:-1],
                    goal_xz=path["goal_xz"],writer=judgement)

    def _intern_compilers(self,comps):
        # Compiler objects are immutable after planning. Share EXACT double
        # precision values/flags, not wire-float rounding or approximate hashes.
        pool = self.__dict__.setdefault("_compiler_pool",{})
        shared = {}
        attributes = ("modified_height","level_delta","smooth_delta",
                      "modified_paint","paint")
        for zone,comp in comps.items():
            digest = hashlib.sha256(repr((zone,comp.operations)).encode())
            for name in attributes:
                values = getattr(comp,name)
                dtype = np.uint8 if name.startswith("modified_") else "<f8"
                digest.update(np.asarray(values,dtype=dtype).tobytes())
            bucket = pool.setdefault(digest.digest(),[])
            previous = next((old for old in bucket if all(
                getattr(old,name) == getattr(comp,name) for name in attributes)),None)
            if previous is None:
                bucket.append(comp)
            shared[zone] = comp if previous is None else previous
        return shared

    def _judge_written(self,comps,patches,segments,road_clips=()):
        import ribbon
        before,after = {},{}
        for comp in comps.values():
            for k,changed in enumerate(comp.modified_height):
                if changed:
                    iz,ix = divmod(k,ribbon.tcdata.PITCH)
                    x,z = ribbon.tcdata.sample_world(*comp.centre,ix,iz)
                    point = (int(round(x)),int(round(z)))
                    before[point] = self.vertex(*point)
                    after[point] = before[point]+ribbon.delta_at(comps,*point)
        finished = lambda x,z: self.generated(x,z)+ribbon.delta_at(comps,x,z)
        rim = route._junction().rim_transfer(after,self.vertex,None,height_after=finished)
        water = ribbon.water_sign(before,after)
        walks = [ribbon.walkability(seg,comps,patches,ribbon.PadKeepOut([],[]),
                    road_clips=list(road_clips))["verdict"] for seg in segments]
        return dict(ok=not rim["caused"] and water["verdict"] == "CLEAR"
                    and all(w == "WALKABLE" for w in walks),
                    rim_caused=rim["caused"],rim_worst_m=rim["worst_caused_m"],
                    water_sign=water["verdict"],walkability=walks)

    @staticmethod
    def _delta_at(comps,x,z):
        import ribbon
        comp = comps.get(ribbon.tcdata.zone_of(x,z))
        if comp is None:
            return None
        ix,iz = ribbon.tcdata.vertex_mask_index(*comp.centre,x,z)
        if not (0 <= ix < ribbon.tcdata.PITCH and 0 <= iz < ribbon.tcdata.PITCH):
            return None
        k = iz*ribbon.tcdata.PITCH+ix
        return comp.level_delta[k] if comp.modified_height[k] else None

    def _pair_height(self,pair,x,z):
        x0,z0 = math.floor(x),math.floor(z)
        tx,tz = x-x0,z-z0
        delta = 0.0
        for dx,dz,w in ((0,0,(1-tx)*(1-tz)),(1,0,tx*(1-tz)),
                       (0,1,(1-tx)*tz),(1,1,tx*tz)):
            if not w:
                continue
            value = self._delta_at(pair[1]["comps"],x0+dx,z0+dz)
            if value is None:
                value = self._delta_at(pair[0]["comps"],x0+dx,z0+dz)
            delta += w*(0.0 if value is None else value)
        return self.generated(x,z)+delta

    def _evaluate_sides(self,span,pair,datum,memo):
        import boardwalk
        import ribbon
        import copy
        finished = lambda x,z: self._pair_height(pair,x,z)
        refusal = memo.probe(finished)
        if refusal is not None:
            return dict(ok=False,stage="canonical_land_seating",why=refusal["why"])
        # Same later-claim replacement as north_itinerary.planned_surface,
        # retaining exact compiler flags, deltas and paints for the real guards.
        merged = dict(pair[0]["comps"])
        for zone,second in pair[1]["comps"].items():
            if zone not in merged:
                merged[zone] = second
                continue
            comp = copy.deepcopy(merged[zone])
            for k,changed in enumerate(second.modified_height):
                if changed:
                    iz,ix = divmod(k,ribbon.tcdata.PITCH)
                    comp.set_height(ix,iz,second.level_delta[k],second.smooth_delta[k])
            for k,changed in enumerate(second.modified_paint):
                if changed:
                    iz,ix = divmod(k,ribbon.tcdata.PITCH)
                    comp.set_paint(ix,iz,second.paint[k])
            merged[zone] = comp
        patches = {**pair[0]["patches"],**pair[1]["patches"]}
        writer = self._judge_written(merged,patches,tuple(p["segment"] for p in pair))
        if not writer["ok"]:
            return dict(ok=False,stage="joint_approach_writer",why=writer)
        surface = boardwalk.asm.Bed.applied(finished,
            "PLANNED canonical independent road stamps; exact later-claim union; not saved-world evidence")
        routes = tuple(dict(seg=p["segment"]["id"],nodes=p["road_nodes"],
            levels=p["road_levels"],half_width_m=p["segment"]["width_m"]/2.0) for p in pair)
        result = boardwalk.prepare_geometry(span,surface,routes=routes)
        failures = {name:rows for name,rows in result["guards"].items() if rows}
        return dict(ok=not failures,status="refused" if failures else "verified",
            stage="canonical_deck",why=failures,
            approach=writer,pieces=len(result["pieces"]),
            bent_spacing_m=result["bent_spacing_m"],source=self.provenance,
            **({"planned_approaches":[p["segment"] for p in pair]} if not failures else {}))

    def _candidate_on_paths(self,crossing,near_bank,far_bank,datum,near_path,far_path):
        from crossing_search import SeatingRefusals
        span = self._span(crossing,near_bank,far_bank,datum)
        if span["span_m"] <= 0.0:
            return dict(ok=False,status="refused",stage="banks",why=["coincident walked banks"])
        plans = [self._compile_side(crossing,bank,other,datum,path,side)
            for side,(bank,other,path) in enumerate((
                (near_bank,far_bank,near_path),(far_bank,near_bank,far_path)))]
        for plan in plans:
            if not plan["ok"]:
                return plan
        return self._evaluate_sides(span,tuple(plans),datum,SeatingRefusals(span,datum))


def constructed_geometry_cases():
    """Unexecuted physical regression arms; no historical S1 world is reused.

    The defect arm has zero lateral spread on the old two inset slices, but
    a 31.042 m dry station propagates a 30.742 m floor onto the adjacent
    27.832 m wet station, above the unchanged 30.600 m wet ceiling. A uniform
    30.600 m bench around a 28 m channel is the survivor. These go through
    the canonical emitted-piece solver and ALL its guards.
    """
    import boardwalk
    span = {"crossing_id": "constructed-geography", "segment": "constructed",
            "bank_a": {"xz": [0.0, 0.0], "ground_y": 30.6},
            "bank_b": {"xz": [0.0, 20.0], "ground_y": 30.6},
            "span_m": 20.0, "deck_width_m": max(WIDTH_M.values()),
            "bank_level_run_m": BANK_LEVEL_M, "single_H_both_banks": True,
            "deck_y_REQUIRED": 30.6, "legal_deck_y_interval": [30.6, 30.6],
            "water_surface_y": 30.0, "terminal_banks": []}
    def survivor(x, z):
        return 28.0 if 1.0 < z < 19.0 else 30.6
    def failing(x, z):
        if 3.0 <= z <= 5.0:
            return 31.042
        if 1.0 < z < 3.0:
            return 27.832
        return survivor(x, z)
    rows = []
    for name, ground, expected in (("wet_ceiling_propagation", failing, False),
                                    ("dry_benches_survive", survivor, True)):
        bed = boardwalk.asm.Bed.applied(ground, "CONSTRUCTED regression, not field evidence")
        result = boardwalk.prepare_geometry(span, bed)
        accepted = not any(result["guards"].values())
        inset_a = [ground(float(x), -2.0) for x in range(-3, 4)]
        inset_b = [ground(float(x), 22.0) for x in range(-3, 4)]
        spread = max(max(inset_a)-min(inset_a), max(inset_b)-min(inset_b))
        lowest = min(*inset_a, *inset_b)
        rows.append({"case": name, "old_inset_lateral_spread_m": spread,
                     "old_inset_lowest_m": lowest,
                     "old_filter_buildable": spread <= 1.0 and lowest >= 30.0,
                     "expected_buildable": expected, "buildable": accepted,
                     "matches_expected": accepted == expected,
                     "constructed_wet_ceiling_m": 30.6,
                     "constructed_required_wet_top_m": 30.742 if not expected else 30.6,
                     "guards": result["guards"]})
    return rows


def constructed_routed_approach_cases():
    """Real A*/fit/writer/deck arms over a constructed integer Heightmap.

    A bay blocks the bank-axis ray at z=-28..-56. Its 28 m shoreline apron
    accommodates the canonical wet-body stand-off, chord slack and 4 m graph
    pitch before the turn. The former 16 m apron did not: two ~8.1 m masks
    closed it before fit/build. The real route must bend around this bay.
    A 60 m cliff across the entire input field closes that route in the
    refusal arm. The constructed world has no protected locations.
    """
    from geography import RoadGraph
    n = 513
    zz, xx = np.mgrid[-256:257, -256:257].astype(float)
    natural = np.minimum(31.0, 30.6+0.04*np.minimum(np.abs(zz), np.abs(zz-20.0)))
    natural[(zz > 0.0) & (zz < 20.0)] = 28.0
    natural[(np.abs(xx) <= 12.0) & (zz <= -28.0) & (zz >= -56.0)] = 28.0
    near_bank = {"xz": [0.0, -2.0], "ground_y": 30.68}
    far_bank = {"xz": [0.0, 22.0], "ground_y": 30.68}
    rows = []
    for name, cliff, expected in (("bend_required_survives", False, True),
                                   ("whole_graph_cliff_refuses", True, False)):
        heights = natural.copy()
        if cliff:
            heights[(zz >= -68.0) & (zz <= -64.0)] = 60.0
        field = fieldmod.Field("constructed-bend", 0.0, 0.0, 256.5, 1.0, n,
            heights.astype(np.float32), np.ones((n, n), dtype=np.uint8))
        corr_m = n//4
        graph = RoadGraph(field, forbid=np.zeros((corr_m, corr_m), dtype=bool))
        physical = PhysicalFields({field.id: field},
            {"constructed": True, "surface": "heightmap",
             "scope": "constructed integer raster, not measured-world evidence"},
            keepout_discs=())
        near_report, far_report = {}, {}
        near = physical.routed_approach(
            graph, near_bank, (0.0, -80.0), 30.6, report=near_report)
        far = physical.routed_approach(
            graph, far_bank, (0.0, 84.0), 30.6, report=far_report)
        proof = (physical._candidate_on_paths(
            {"id": name}, near_bank, far_bank, 30.6, near, far)
            if near is not None and far is not None else
            {"ok": False, "stage": "canonical_router",
             "why": ["the actual graph search found no dry route"]})
        bends = (max(abs(p[0]) for p in near["nodes"]) if near else None)
        old_ray_wet = sum(physical.generated(0.0, -float(k)) < 30.0
                          for k in range(1, 35))
        rows.append({"case": name, "expected_buildable": expected,
            "buildable": proof["ok"],
            "matches_expected": proof["ok"] == expected
                and (not expected or bends > 12.0),
            "old_axis_ray_wet_samples": old_ray_wet,
            "routed_maximum_off_axis_m": bends,
            "near_search": near_report, "far_search": far_report,
            "near_route": near, "far_route": far, "physical": proof})
    return rows
