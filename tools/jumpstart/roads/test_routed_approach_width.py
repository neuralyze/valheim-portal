"""Declared-width dry-footprint routing on a constructed integer Heightmap.

These are numerical router proofs, not measured-world construction acceptance.
"""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]

import field as fieldmod
import route
from crossing_contract import transverse_window
from crossing_physical import PhysicalFields


def narrow_dry_strip():
    """Seven dry lattice columns: a 4 m road's +/-3 m bench fits exactly."""
    n = 65
    axis = np.arange(n, dtype=float) - n // 2
    xx, zz = np.meshgrid(axis, axis)
    heights = np.where(np.abs(xx) <= 3.0, 31.0, 28.0).astype(np.float32)
    frame = fieldmod.Field(
        "constructed-width-strip", 0.0, 0.0, n / 2, 1.0, n, heights,
        np.full((n, n), fieldmod.BIOME_CODE["Meadows"], dtype=np.uint8))
    physical = PhysicalFields(
        {frame.id: frame}, {"constructed": True, "surface": "heightmap"},
        keepout_discs=())
    corr = route.Corridor(frame, cell_m=1.0)
    # A finite permitted strip makes the wet-footprint refusal exhaustive:
    # feedback cannot move the fixed bank onto a different unowned approach.
    # The graph adapter supplies only the two fields routed_approach consumes;
    # all height sampling, edge costs, search and feedback remain production.
    ok = (np.abs(xx) <= 3.0) & (np.abs(zz) <= 12.0)
    corr.forbid = ~ok
    corr.soft = np.full(ok.shape, 0.125)
    corr.guidance = np.full(ok.shape, 0.25)
    graph = SimpleNamespace(corr=corr, ok=ok)
    bank = {"xz": [0.0, -12.0], "ground_y": 31.0}
    return physical, graph, bank, (0.0, 12.0)


class RoutedApproachWidthTests(unittest.TestCase):
    def test_four_metre_route_fits_but_six_metre_shoulders_are_wet(self):
        physical, graph, bank, goal = narrow_dry_strip()
        arrays = ("forbid", "water", "water_forbidden", "S", "soft", "guidance")
        before = {name: getattr(graph.corr, name).copy() for name in arrays}
        before_ok = graph.ok.copy()
        report = {}
        result = physical.routed_approach(
            graph, bank, goal, 31.0, width_m=4.0, report=report)

        self.assertIsNotNone(result)
        expected = np.column_stack((np.zeros(13), np.arange(-12.0, 13.0, 2.0)))
        np.testing.assert_allclose(result["nodes"], expected, atol=1e-12)
        self.assertEqual(result["goal_xz"], list(goal))
        self.assertEqual(result["bank_snap_m"], 0.0)
        self.assertEqual(report, {"refusal": None, "water_reroute_rounds": 0})
        sampler = SimpleNamespace(
            bilinear=physical.generated, contains=physical.contains,
            sample_many=physical.sample_many)
        dry_wet, wide_wet = [], []
        low, high, off = transverse_window(
            sampler, result["nodes"], 4.0, wet_at=dry_wet)
        np.testing.assert_array_equal(low, np.full(13, 31.0))
        np.testing.assert_array_equal(high, np.full(13, 31.0))
        self.assertEqual((off, dry_wet), ([], []))
        wide_low, _high, wide_off = transverse_window(
            sampler, result["nodes"], 6.0, wet_at=wide_wet)
        np.testing.assert_array_equal(wide_low, np.full(13, 28.0))
        self.assertEqual(wide_off, [])
        # At each station, +/-3.5 m interpolates to 29.5 m and +/-4 m
        # measures 28 m. The centreline and the 4 m bench remain at 31 m.
        self.assertEqual(len(wide_wet), 52)
        self.assertEqual({abs(x) for x, _z, _k in wide_wet}, {3.5, 4.0})
        self.assertTrue(all(physical.generated(x, z) < fieldmod.WATER_LEVEL
                            for x, z, _k in wide_wet))

        wide_report = {}
        wide = physical.routed_approach(
            graph, bank, goal, 31.0, width_m=6.0, report=wide_report)
        self.assertIsNone(wide)
        self.assertEqual(wide_report["water_reroute_rounds"], 1)
        self.assertEqual(wide_report["refusal"],
            "no allowed fixed-bank snap under the canonical water mask")
        self.assertEqual(len(wide_report["water_feedback"]), 1)
        feedback = wide_report["water_feedback"][0]
        self.assertEqual(feedback["wet_footprint_probes"], 52)
        self.assertEqual(feedback["newly_forbidden_cells"], 175)
        self.assertEqual(feedback["wet_x_interval"], [-4.0, 4.0])
        self.assertEqual(feedback["wet_z_interval"], [-12.0, 12.0])
        np.testing.assert_array_equal(graph.ok, before_ok)
        for name, values in before.items():
            np.testing.assert_array_equal(getattr(graph.corr, name), values)
        self.assertFalse(graph.corr.forbid_all_water)

    def test_omitted_or_none_width_preserves_the_widest_class_refusal(self):
        physical, graph, bank, goal = narrow_dry_strip()
        explicit_report, omitted_report, none_report = {}, {}, {}
        explicit = physical.routed_approach(
            graph, bank, goal, 31.0, width_m=6.0, report=explicit_report)
        omitted = physical.routed_approach(
            graph, bank, goal, 31.0, report=omitted_report)
        none = physical.routed_approach(
            graph, bank, goal, 31.0, width_m=None, report=none_report)
        self.assertIsNone(explicit)
        self.assertIsNone(omitted)
        self.assertIsNone(none)
        self.assertEqual(omitted_report, explicit_report)
        self.assertEqual(none_report, explicit_report)
        # A wider refused query must not poison a later narrow real search.
        narrow = physical.routed_approach(graph, bank, goal, 31.0, width_m=4.0)
        self.assertIsNotNone(narrow)
        self.assertEqual(narrow["water_reroute_rounds"], 0)


if __name__ == "__main__":
    unittest.main()
