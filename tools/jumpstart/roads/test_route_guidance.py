"""Additive routing guidance changes prices, never physical traversability."""
from pathlib import Path
import copy
import math
import sys
import unittest
from unittest.mock import patch

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]

import field as fieldmod
import route
from crossing_graph_snapshot import native_routing_signature, routing_snapshot
from crossing_graph_snapshot_cases import _native
from crossing_route_catalog_cases import _same_routes
from crossing_search import EdgeCosts, approach_tree, empty_bank_key


def corridor(height=40.0, *, guidance=None):
    n = 36
    fld = fieldmod.Field("constructed-guidance", 0.0, 0.0, n / 2, 1.0, n,
        np.full((n, n), height, dtype=np.float32),
        np.full((n, n), fieldmod.BIOME_CODE["Swamp"], dtype=np.uint8))
    return route.Corridor(fld, cell_m=4.0, guidance=guidance)


def path_cost(corr, path):
    return sum(corr._step_cost(corr.cell_of(*a), corr.cell_of(*b), math.dist(a, b))
               for a, b in zip(path, path[1:]))


class RouteGuidanceTests(unittest.TestCase):
    def test_default_none_and_assignment_validation(self):
        corr = corridor()
        self.assertIsNone(corr.guidance)
        self.assertIsNone(EdgeCosts(corr).corr.guidance)
        valid = np.zeros((corr.m, corr.m), dtype=np.float64)
        self.assertIs(corridor(guidance=valid).guidance, valid)
        corr.guidance = valid
        invalid = (np.zeros((corr.m - 1, corr.m - 1)), np.zeros((corr.m,)),
                   np.zeros((corr.m, corr.m + 1)), [[0.0]],
                   np.full(valid.shape, -1.0), np.full(valid.shape, np.nan),
                   np.full(valid.shape, np.inf), np.full(valid.shape, -np.inf),
                   valid.astype(complex), valid.astype(object))
        for value in invalid:
            with self.subTest(shape=np.shape(value), dtype=getattr(value, "dtype", None)):
                with self.assertRaisesRegex(ValueError, "finite nonnegative corridor-grid"):
                    corr.guidance = value
                self.assertIs(corr.guidance, valid)
                with self.assertRaises(ValueError):
                    corridor(guidance=value)
        corr.guidance = None
        self.assertIsNone(corr.guidance)

    def test_dry_and_causeway_guidance_is_added_after_biome_and_poi(self):
        for height in (40.0, 30.75):
            for poi_weight in (0.0, 7.0):
                for a, b in (((3, 2), (3, 3)), ((2, 2), (3, 3))):
                    with self.subTest(height=height, poi_weight=poi_weight, edge=(a, b)):
                        corr = corridor(height)
                        corr.poi_weight = poi_weight
                        corr.soft = np.full((corr.m, corr.m), 3.0)
                        d = corr.cell_m * math.dist(a, b)
                        original = corr._step_cost(a, b, d)
                        self.assertIsNotNone(original)
                        self.assertEqual(bool(corr.fillable[b]), height < 31.0)
                        corr.guidance = np.zeros((corr.m, corr.m))
                        corr.guidance[b] = 2.5
                        # Swamp's x4 and the causeway multiplier are already in
                        # original. Guidance is road-metres/m, not another pen.
                        self.assertEqual(corr._step_cost(a, b, d), original + d * 2.5)
                        self.assertEqual(corr._step_cost(b, a, d), original)

    def test_water_entry_and_continuation_have_the_same_additive_units(self):
        corr = corridor(30.75)
        corr.water[3, 3:5] = True
        corr.fillable[3, 3:5] = False
        corr.poi_weight = 0.0
        corr.soft = np.full((corr.m, corr.m), 100.0)
        for a, b in (((3, 2), (3, 3)), ((3, 3), (3, 4))):
            with self.subTest(edge=(a, b)):
                corr.guidance = None
                d = corr.cell_m
                original = corr._step_cost(a, b, d)
                expected = d * corr.bridge_per_m_cost
                if not corr.water[a]:
                    expected += corr.bridge_entry_cost_m
                self.assertEqual(original, expected)
                corr.guidance = np.zeros((corr.m, corr.m))
                corr.guidance[b] = 3.25
                self.assertEqual(corr._step_cost(a, b, d), original + d * 3.25)
        corr.forbid_all_water = True
        self.assertIsNone(corr._step_cost((3, 2), (3, 3), corr.cell_m))
        corr.forbid_all_water = False
        corr.water_forbidden[3, 3] = True
        self.assertIsNone(corr._step_cost((3, 2), (3, 3), corr.cell_m))

    def test_guidance_selects_a_detour_without_forbidding_any_cell(self):
        corr = corridor()
        corr.poi_weight = 0.0
        physical = {name: getattr(corr, name).copy() for name in
                    ("water", "water_forbidden", "fillable", "S", "H", "rough", "Bi")}
        start, goal = (4, 1), (4, 7)
        endpoints = corr.cell_world(*start), corr.cell_world(*goal)
        direct = corr.search(*endpoints)
        self.assertTrue(all(corr.cell_of(*xz)[0] == 4 for xz in direct))
        original_cost = path_cost(corr, direct)
        corr.guidance = np.zeros((corr.m, corr.m))
        corr.guidance[4, 3:6] = 100.0
        guided = corr.search(*endpoints)
        cells = [corr.cell_of(*xz) for xz in guided]
        self.assertNotEqual(guided, direct)
        self.assertEqual((cells[0], cells[-1]), (start, goal))
        self.assertFalse(any(corr.guidance[cell] for cell in cells))
        self.assertGreater(path_cost(corr, guided), original_cost)
        self.assertLess(path_cost(corr, guided), path_cost(corr, direct))
        self.assertIsNone(corr.forbid)
        self.assertIsNone(corr.last_terminal)
        for name, before in physical.items():
            np.testing.assert_array_equal(getattr(corr, name), before)
        for i in range(corr.m):
            for j in range(corr.m):
                for di, dj in ((1, 0), (0, 1), (1, 1), (1, -1)):
                    b = i + di, j + dj
                    if 0 <= b[0] < corr.m and 0 <= b[1] < corr.m:
                        self.assertIsNotNone(corr._step_cost((i, j), b,
                            corr.cell_m * math.hypot(di, dj)))
        pool = EdgeCosts(corr)
        tree = approach_tree({"edge_costs": pool}, pool.corr, start, base=True)
        shared = tree.path_to(goal)
        self.assertAlmostEqual(path_cost(corr, shared), path_cost(corr, guided))
        corr.forbid = np.zeros((corr.m, corr.m), dtype=bool)
        corr.forbid[4, 3] = True
        self.assertIsNone(corr._step_cost((4, 2), (4, 3), corr.cell_m))
        corr.forbid = None
        corr.S[4, 3] = 100.0
        self.assertIsNone(corr._step_cost((4, 2), (4, 3), corr.cell_m))

    def test_edge_owner_isolates_source_and_replacement_uses_fresh_native_trees(self):
        corr = corridor()
        corr.guidance = np.zeros((corr.m, corr.m))
        pool = EdgeCosts(corr)
        owned = pool.corr
        start, goal = (4, 1), (4, 7)
        trees = {"edge_costs": pool}
        first = approach_tree(trees, owned, start, base=True)
        direct = first.path_to(goal)
        corr.guidance[4, 3:6] = 100.0
        self.assertIs(approach_tree(trees, owned, start, base=True), first)
        self.assertEqual(first.path_to(goal), direct)
        self.assertTrue(np.all(owned.guidance == 0.0))
        self.assertFalse(pool.compatible(corr))
        with self.assertRaises(ValueError):
            owned.guidance[4, 4] = 1.0
        with self.assertRaises(ValueError):
            owned.guidance.flags.writeable = True
        changed = copy.copy(owned)
        changed.guidance = corr.guidance.copy()
        self.assertFalse(pool.compatible(changed))
        second = approach_tree(trees, changed, start, base=True)
        self.assertIsNot(second, first)
        self.assertIsNone(second.edge_costs)
        self.assertNotEqual(second.path_to(goal), direct)
        changed.guidance[:] = 0.0
        third = approach_tree(trees, changed, start, base=True)
        self.assertIsNot(third, second)
        self.assertEqual(third.path_to(goal), direct)
        changed.guidance = None
        self.assertFalse(pool.compatible(changed))

    def test_legacy_empty_bank_key_tracks_replacement_and_in_place_guidance(self):
        _physical, graph, bank = _native()
        corr = graph.corr
        original = empty_bank_key(graph, corr, bank, 8.0)
        corr.guidance = np.zeros((corr.m, corr.m))
        zero = empty_bank_key(graph, corr, bank, 8.0)
        self.assertIsNotNone(original)
        self.assertNotEqual(zero, original)
        corr.guidance[0, 0] = 3.0
        mutated = empty_bank_key(graph, corr, bank, 8.0)
        self.assertNotEqual(mutated, zero)
        corr.guidance = np.full((corr.m, corr.m), 4.0)
        self.assertNotEqual(empty_bank_key(graph, corr, bank, 8.0), mutated)
        corr.guidance = None
        self.assertEqual(empty_bank_key(graph, corr, bank, 8.0), original)

    def test_snapshot_owns_guidance_and_replacement_opts_out_of_fast_signature(self):
        _physical, graph, bank = _native()
        corr = graph.corr
        corr.guidance = np.zeros((corr.m, corr.m))
        owned = routing_snapshot(graph)
        initial = native_routing_signature(owned, bank, 8.0)
        self.assertIsNotNone(initial)
        self.assertIsNot(owned.corr.guidance, corr.guidance)
        corr.guidance[0, 0] = 7.0
        self.assertEqual(native_routing_signature(owned, bank, 8.0), initial)
        self.assertEqual(owned.corr.guidance[0, 0], 0.0)
        refreshed = routing_snapshot(graph)
        self.assertEqual(refreshed.corr.guidance[0, 0], 7.0)
        self.assertNotEqual(native_routing_signature(refreshed, bank, 8.0), initial)
        corr.guidance = np.full((corr.m, corr.m), 9.0)
        self.assertEqual(native_routing_signature(owned, bank, 8.0), initial)
        with self.assertRaises(ValueError):
            owned.corr.guidance[0, 0] = 7.0
        with self.assertRaises(ValueError):
            owned.corr.guidance.flags.writeable = True
        owned.corr.guidance = corr.guidance.copy()
        self.assertIsNone(native_routing_signature(owned, bank, 8.0))
        self.assertIs(routing_snapshot(owned), owned)
        # The same safe legacy path used by PhysicalFields remains content-bound.
        key = empty_bank_key(owned, owned.corr, bank, 8.0)
        owned.corr.guidance[0, 0] = 11.0
        self.assertNotEqual(empty_bank_key(owned, owned.corr, bank, 8.0), key)

    def test_complete_replay_tracks_mutable_guidance_in_fallback(self):
        physical, graph, bank = _native()
        graph.corr.guidance = np.zeros((graph.corr.m, graph.corr.m))
        cold_stats, warm_stats = {}, {}
        cold = list(physical._route_paths(graph, bank, cold_stats))
        warm = list(physical._route_paths(graph, bank, warm_stats))
        self.assertTrue(cold)
        self.assertTrue(_same_routes(cold, warm))
        self.assertGreater(cold_stats.get("route_queries", 0), 0)
        self.assertEqual(warm_stats.get("route_catalog_reuses"), 1)
        for replace in (False, True):
            if replace:
                graph.corr.guidance = np.full((graph.corr.m, graph.corr.m), 2.0)
            else:
                graph.corr.guidance[0, 0] = 100.0
            changed_stats, replay_stats = {}, {}
            changed = list(physical._route_paths(graph, bank, changed_stats))
            replay = list(physical._route_paths(graph, bank, replay_stats))
            self.assertGreater(changed_stats.get("route_queries", 0), 0)
            self.assertEqual(changed_stats.get("route_catalog_reuses", 0), 0)
            self.assertTrue(_same_routes(changed, replay))
            self.assertEqual(replay_stats.get("route_catalog_reuses"), 1)

    def test_owned_replay_keeps_fast_signature_then_replacement_falls_back(self):
        physical, graph, bank = _native()
        graph.corr.guidance = np.zeros((graph.corr.m, graph.corr.m))
        owned = routing_snapshot(graph)
        # No legacy grid hashing is needed for any unchanged owned yield/replay.
        with patch("crossing_search.empty_bank_key", side_effect=AssertionError(
                "unchanged owned routes must not hash coarse arrays")):
            cold = list(physical._route_paths(owned, bank, {}))
            stats = {}
            warm = list(physical._route_paths(owned, bank, stats))
            self.assertTrue(cold)
            self.assertTrue(_same_routes(cold, warm))
            self.assertEqual(stats.get("route_catalog_reuses"), 1)
            graph.corr.guidance[:] = 5.0
            source_mutation_stats = {}
            unchanged = list(physical._route_paths(owned, bank, source_mutation_stats))
            self.assertTrue(_same_routes(cold, unchanged))
            self.assertEqual(source_mutation_stats.get("route_catalog_reuses"), 1)
        owned.corr.guidance = graph.corr.guidance.copy()
        stats = {}
        changed = list(physical._route_paths(owned, bank, stats))
        self.assertTrue(changed)
        self.assertGreater(stats.get("route_queries", 0), 0)
        self.assertEqual(stats.get("route_catalog_reuses", 0), 0)
        owned.corr.guidance[0, 0] = 10.0
        stats = {}
        list(physical._route_paths(owned, bank, stats))
        self.assertGreater(stats.get("route_queries", 0), 0)
        self.assertEqual(stats.get("route_catalog_reuses", 0), 0)

    def test_guidance_assignment_rejects_costs_beyond_finite_path_range(self):
        for height in (40.0, 20.0):
            with self.subTest(height=height):
                corr = corridor(height)
                corr.forbid = np.ones((corr.m, corr.m), dtype=bool)
                corr.forbid[4, :] = False
                corr.water_forbidden[:] = False
                with self.assertRaises(OverflowError):
                    corr.guidance = np.full((corr.m, corr.m), math.ldexp(1.0, 1020))

    def test_mutated_guidance_cost_overflow_is_not_physical_unreachability(self):
        for height in (40.0, 20.0):
            with self.subTest(height=height):
                corr = corridor(height)
                corr.forbid = np.ones((corr.m, corr.m), dtype=bool)
                corr.forbid[4, :] = False
                corr.water_forbidden[:] = False
                corr.guidance = np.zeros((corr.m, corr.m))
                corr.guidance[:] = math.ldexp(1.0, 1020)
                with self.assertRaises(OverflowError):
                    corr._astar((4, 0), (4, corr.m - 1))


if __name__ == "__main__":
    unittest.main()
