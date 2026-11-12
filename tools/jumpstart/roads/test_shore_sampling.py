"""Constructed geometry checks, never evidence of physical shore success."""
import math
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
from settlements import waterfront as WF
from shore_sampling import GeneratedSampler


class CountedCoordinate(float):
    """Count real bbox comparisons, including scans over cached metadata."""
    comparisons = 0

    def __le__(self, other):
        type(self).comparisons += 1
        return float.__le__(self, other)

    def __ge__(self, other):
        type(self).comparisons += 1
        return float.__ge__(self, other)


def field(name, x0, z0, n=7, step=1.0, height=0.0):
    half = n * step / 2
    heights = (np.arange(n * n, dtype=np.float32).reshape(n, n)
               + np.float32(height))
    return WF.W.Field(name, x0 + half - step / 2,
                      z0 + half - step / 2, half, step, n,
                      heights, np.zeros((n, n), dtype=np.uint8))


def surface(fields):
    # Use production methods as the oracle without invoking source loading or
    # any applied-world/ledger constructor. Only constructed Field instances.
    result = WF.Surface.__new__(WF.Surface)
    result.fields = fields
    result.field_names = ["constructed generated fields"]
    result.applied = SimpleNamespace(delta_at=lambda x, z: 0.0)
    return result


class GeneratedSamplerTests(unittest.TestCase):
    def assert_selection(self, fields, queries):
        original = surface(fields)
        sampler = GeneratedSampler(fields, original.field_names)
        for x, z, margin in queries:
            with self.subTest(x=x, z=z, margin=margin):
                try:
                    expected = original.patch_for(x, z, margin)
                except SystemExit as error:
                    with self.assertRaises(SystemExit) as caught:
                        sampler.patch_for(x, z, margin)
                    self.assertEqual(str(caught.exception), str(error))
                else:
                    self.assertIs(sampler.patch_for(x, z, margin), expected)
        return sampler

    def test_tiny_inset_overrides_previously_selected_broad_patch(self):
        broad = field("broad", -128.0, -128.0, n=257, height=1000.0)
        inset = field("inset", -1.0, -1.0, n=3, height=-100.0)
        sampler = self.assert_selection(
            {"broad": broad, "inset": inset},
            [(50.0, 50.0, 0.0), (0.0, 0.0, 0.0),
             (-1.0, -1.0, 0.0), (1.0, 1.0, 0.0),
             (math.nextafter(1.0, math.inf), 0.0, 0.0),
             (0.0, 0.0, 2.0), (0.0, 0.0, 1.0)])
        self.assertIs(sampler.patch_for(0.0, 0.0, 0.0), inset)
        self.assertEqual(sampler.gen_sample(0, 0), -96.0)
        self.assertEqual(sampler.gen_sample(50, 50), surface(
            {"broad": broad, "inset": inset}).gen_sample(50, 50))

    def test_step_precedes_half_and_equal_priority_preserves_insertion(self):
        coarse = field("coarse", 0.0, 0.0, n=3, step=2.0)
        fine = field("fine", -4.0, -4.0, n=13)
        first = field("z-first", -2.0, -2.0)
        second = field("a-second", 0.0, 0.0)
        sampler = self.assert_selection(
            {"coarse": coarse, "fine": fine, "first": first,
             "second": second},
            [(0.0, 0.0, 0.0), (2.0, 2.0, 0.0),
             (5.0, 5.0, 0.0), (2.0, 2.0, 2.0)])
        self.assertIs(sampler.patch_for(2.0, 2.0, 0.0), first)
        self.assertIs(sampler.patch_for(5.0, 5.0, 0.0), second)
        self.assertIs(GeneratedSampler({"coarse": coarse, "fine": fine},
                                      []).patch_for(2.0, 2.0, 0.0), fine)
        self.assertIs(GeneratedSampler({"second": second, "first": first},
                                      []).patch_for(2.0, 2.0, 0.0), second)

    def test_inclusive_sample_bounds_not_half_extent_and_all_margin_signs(self):
        f = field("sample-bounds", -3.0, -7.0)
        queries = []
        for margin in (0.0, 0.125, 2.0, 3.0, 3.125, -0.25, -20.0):
            xmin, xmax = f.x0 + margin, f.x0 + (f.n - 1) * f.step - margin
            zmin, zmax = f.z0 + margin, f.z0 + (f.n - 1) * f.step - margin
            for x in (xmin, xmax, math.nextafter(xmin, -math.inf),
                      math.nextafter(xmax, math.inf)):
                for z in (zmin, zmax, -4.0):
                    queries.append((x, z, margin))
        sampler = self.assert_selection({"sample-bounds": f}, queries)
        # Half extent would incorrectly claim this uncovered half-metre rim.
        with self.assertRaises(SystemExit):
            sampler.patch_for(f.x0 - 0.25, f.z0, 0.0)
        self.assertIs(sampler.patch_for(f.x0 - 0.25, f.z0, -0.25), f)
        self.assertIs(sampler.patch_for(0.0, -4.0), f)

    def test_float64_nextafter_boundaries_survive_dispatch(self):
        origin = 16777216.25  # Cannot be represented by a float32 coordinate.
        patches = {str(i): field(str(i), origin + i * 64.0, -origin)
                   for i in range(32)}
        queries = []
        for f in patches.values():
            for margin in (0.0, 0.25, -0.25, 2.0):
                for edge in (f.x0 + margin, f.x0 + 6.0 - margin):
                    for x in (edge, math.nextafter(edge, -math.inf),
                              math.nextafter(edge, math.inf)):
                        queries.append((x, f.z0 + 3.0, margin))
        self.assert_selection(patches, queries)

    def test_overlap_tree_branches_and_negative_margin_union_are_exact(self):
        patches = {str(i): field(str(i), float((i % 7) * 3 - 10),
                                 float((i // 7) * 3 - 10),
                                 n=3 + 2 * (i % 5), height=i * 100.0)
                   for i in range(49)}
        queries = [(x / 2, z / 2, margin)
                   for x in range(-25, 36, 3)
                   for z in range(-25, 36, 3)
                   for margin in (-5.0, -0.25, 0.0, 0.5, 2.0, 20.0)]
        self.assert_selection(patches, queries)

    def test_empty_coverage_gaps_and_nonfinite_queries_refuse_identically(self):
        self.assert_selection({}, [(0.0, 0.0, 0.0)])
        patches = {"a": field("a", -20.0, -20.0),
                   "b": field("b", 20.0, 20.0)}
        self.assert_selection(patches,
                              [(0.0, 0.0, 0.0), (-10.0, -10.0, 0.0),
                               (math.nan, 0.0, 0.0), (0.0, math.inf, 0.0),
                               (0.0, 0.0, math.nan), (0.0, 0.0, math.inf),
                               (0.0, 0.0, -math.inf)])

    def test_integer_values_rounding_and_rendered_notch_are_not_smoothed(self):
        notch = field("notch", -3.0, -3.0)
        notch.heights[:] = np.float32(32.0)
        notch.heights[3, 3] = np.float32(-7.125)
        original = surface({"notch": notch})
        sampler = GeneratedSampler(original.fields, original.field_names)
        rendered = surface(original.fields)
        rendered.gen_sample = sampler.gen_sample
        rendered.patch_for = sampler.patch_for
        for sx in range(-3, 4):
            for sz in range(-3, 4):
                self.assertEqual(sampler.gen_sample(sx, sz),
                                 original.gen_sample(sx, sz))
        self.assertEqual(sampler.gen_sample(0, 0), -7.125)
        for point in ((0.0, 0.0), (0.25, 0.75), (-0.25, -0.5),
                      (0.999999999999, 0.125), (3.0, 3.0)):
            self.assertEqual(rendered.at(*point), original.at(*point))
        # This off-lattice fixture exercises the exact existing round-to-even
        # expression; the indexer itself must not silently realign a source.
        shifted = field("half-offset", -2.5, -2.5)
        reference = surface({"shifted": shifted})
        indexed = GeneratedSampler(reference.fields, reference.field_names)
        self.assertEqual(indexed.gen_sample(0, 0), reference.gen_sample(0, 0))
        self.assertEqual(indexed.gen_sample(0, 0), float(shifted.heights[2, 2]))
        with self.assertRaises(SystemExit):
            sampler.gen_sample(4, 0)

    def test_nonunit_sample_index_errors_are_not_clipped_or_rescaled(self):
        coarse = field("coarse", 0.0, 0.0, n=3, step=2.0)
        original = surface({"coarse": coarse})
        sampler = GeneratedSampler(original.fields, original.field_names)
        self.assertEqual(sampler.gen_sample(2, 2), original.gen_sample(2, 2))
        # Coverage includes (4, 4), but Surface.gen_sample deliberately has no
        # /step. The generated-source loader, not this index, rejects coarse
        # fields for a real solve; do not replace that refusal with smoothing.
        with self.assertRaises(IndexError):
            original.gen_sample(4, 4)
        with self.assertRaises(IndexError):
            sampler.gen_sample(4, 4)

    def test_spatial_work_does_not_scan_all_disjoint_patch_bounds(self):
        # Count comparisons on the query, not Field.contains calls: simply
        # inlining/caching a linear scan still cannot pass this assertion.
        for count in (64, 4096):
            patches = {str(i): field(str(i), float((i % 64) * 64),
                                     float((i // 64) * 64), n=3)
                       for i in range(count)}
            reference = surface(patches)
            sampler = GeneratedSampler(patches, reference.field_names)
            for index in (0, count // 2, count - 1):
                chosen = patches[str(index)]
                x = CountedCoordinate(chosen.x0 + 1.0)
                z = CountedCoordinate(chosen.z0 + 1.0)
                CountedCoordinate.comparisons = 0
                self.assertIs(reference.patch_for(x, z, 0.0), chosen)
                linear_work = CountedCoordinate.comparisons
                CountedCoordinate.comparisons = 0
                self.assertIs(sampler.patch_for(x, z, 0.0), chosen)
                indexed_work = CountedCoordinate.comparisons
                with self.subTest(count=count, index=index):
                    self.assertGreaterEqual(linear_work, count)
                    self.assertLess(indexed_work, 160)
                    if count == 4096:
                        self.assertLess(indexed_work * 16, linear_work)


if __name__ == "__main__":
    unittest.main()
