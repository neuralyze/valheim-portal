"""Exact generated batching against the real scalar waterfront instrument."""
import math
from pathlib import Path
import sys
import unittest

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
from shore_sampling import GeneratedSampler
from test_shore_sampling import field, surface


class CountedHeights(np.ndarray):
    """Count actual requested raster elements, not just indexing calls."""

    def __new__(cls, values):
        result = values.view(cls)
        result.requested = 0
        return result

    def __array_finalize__(self, original):
        self.requested = getattr(original, "requested", 0)

    def __getitem__(self, key):
        result = super().__getitem__(key)
        self.requested += np.size(result)
        return result


class GeneratedBatchSamplingTests(unittest.TestCase):
    def assert_batch(self, fields, points):
        original = surface(fields)
        sampler = GeneratedSampler(fields, original.field_names)
        points = np.asarray(points, dtype=np.float64).reshape(-1, 2)
        expected = np.array([original.at(float(x), float(z))[0]
                             for x, z in points], dtype=np.float64)
        actual = sampler.sample_points(points)
        self.assertEqual(actual.shape, (len(points),))
        self.assertEqual(actual.dtype, np.dtype(np.float64))
        # Include signed zero and every mantissa bit, not a tolerance that can
        # hide float32 interpolation, reassociation, or a different corner.
        np.testing.assert_array_equal(actual.view(np.uint64),
                                      expected.view(np.uint64))
        return sampler

    def assert_first_error(self, fields, points):
        original = surface(fields)
        sampler = GeneratedSampler(fields, original.field_names)
        points = np.asarray(points, dtype=np.float64)
        try:
            for x, z in points:
                original.at(float(x), float(z))
        except (SystemExit, ValueError, OverflowError, IndexError) as expected:
            with self.assertRaises(type(expected)) as caught:
                sampler.sample_points(points)
            self.assertEqual(str(caught.exception), str(expected))
        else:
            self.fail("constructed scalar oracle did not raise")

    def test_fractional_negative_integer_and_boundary_points_are_bit_exact(self):
        patch = field("notched", -4.0, -4.0, n=9)
        patch.heights[:] = np.float32(32.0)
        patch.heights[4, 4] = np.float32(-7.125)
        self.assert_batch({"notched": patch}, [
            (0.0, 0.0), (0.25, 0.75), (-0.25, -0.5),
            (-4.0, -4.0), (4.0, 4.0), (4.0, -3.125),
            (-0.0, math.nextafter(0.0, -math.inf)),
            (math.nextafter(1.0, -math.inf), 0.125),
            (math.nextafter(-1.0, math.inf), -0.875),
        ])

    def test_float64_interpolation_keeps_scalar_multiply_and_add_order(self):
        patch = field("varied", -8.0, -8.0, n=17)
        rng = np.random.default_rng(613)
        patch.heights[:] = (rng.standard_normal((17, 17))
                            * np.exp2(rng.integers(-30, 31, (17, 17))))
        points = rng.uniform(-7.9, 7.9, (257, 2))
        self.assert_batch({"varied": patch}, points)

    def test_subnormal_weights_ignore_numpy_underflow_policy_like_scalar(self):
        patch = field("subnormal", -3.0, -3.0, height=31.5)
        with np.errstate(under="raise"):
            self.assert_batch({"patch": patch}, [[math.ulp(0.0), 0.5]])

    def test_each_corner_selects_fine_inset_and_stable_priority_ties(self):
        broad = field("broad", -8.0, -8.0, n=17, height=1000.0)
        coarse = field("coarse", -2.0, -2.0, n=5, step=2.0,
                       height=2000.0)
        first = field("z-first", 0.0, 0.0, n=2, height=-100.0)
        second = field("a-second", 1.0, 0.0, n=2, height=-200.0)
        fine = field("fine", 1.0, 1.0, n=3, step=0.5, height=-300.0)
        fields = {"coarse": coarse, "broad": broad, "first": first,
                  "second": second, "fine": fine}
        points = [(-0.25, -0.25), (0.5, 0.5), (1.25, 0.5),
                  (1.0, 1.0), (1.25, 1.25), (2.0, 2.0)]
        self.assert_batch(fields, points)
        reversed_ties = dict(fields)
        del reversed_ties["first"]
        reversed_ties["first"] = first
        self.assert_batch(reversed_ties, points)
        self.assertNotEqual(surface(fields).at(1.0, 0.0)[0],
                            surface(reversed_ties).at(1.0, 0.0)[0])

    def test_half_offset_round_to_even_and_nonunit_legacy_indices(self):
        shifted = field("half-offset", -2.5, -2.5)
        self.assert_batch({"shifted": shifted}, [
            (-1.75, -1.25), (-0.25, 0.25), (0.0, 0.0), (2.25, 2.5),
        ])
        coarse = field("coarse", 0.0, 0.0, n=3, step=2.0)
        self.assert_batch({"coarse": coarse}, [
            (0.25, 0.75), (1.25, 1.5), (2.0, 2.0),
        ])
        self.assert_first_error({"coarse": coarse}, [(2.25, 1.25)])

    def test_large_float64_origins_and_inclusive_integer_edges(self):
        origin = 16777216.0
        patch = field("large-origin", origin, -origin, n=7)
        self.assert_batch({"large-origin": patch}, [
            (origin, -origin), (origin + 6.0, -origin + 6.0),
            (math.nextafter(origin + 1.0, -math.inf), -origin + 0.25),
            (origin + 0.25, math.nextafter(-origin + 1.0, math.inf)),
        ])

    def test_zero_weight_corners_do_not_require_extra_coverage(self):
        singleton = field("single-lattice-sample", 0.0, 0.0, n=1)
        self.assert_batch({"single": singleton}, [(0.0, 0.0)] * 8)
        self.assert_first_error({"single": singleton}, [(0.0, 0.25)])
        self.assert_first_error({}, [(0.0, 0.0)])

    def test_first_point_and_corner_errors_survive_grouped_field_work(self):
        coarse = field("coarse", 0.0, 0.0, n=3, step=2.0)
        valid = field("valid", -10.0, -10.0)
        fields = {"valid": valid, "coarse": coarse}
        # Both orders matter: a later uncovered point must not mask an earlier
        # out-of-range raster index, nor vice versa.
        for points in (
                [(-9.5, -9.5), (2.25, 0.25), (100.25, 100.25)],
                [(-9.5, -9.5), (100.25, 100.25), (2.25, 0.25)],
                [(2.25, 4.25)], [(4.25, 2.25)]):
            with self.subTest(points=points):
                self.assert_first_error(fields, points)
        # In one point the (1, 0) corner is uncovered before the (0, 1)
        # corner's nonunit-step field would raise an index error.
        singleton = field("origin", 0.0, 0.0, n=1)
        north = field("north", -4.0, -2.0, n=3, step=2.0)
        self.assert_first_error({"origin": singleton, "north": north},
                                [(0.25, 0.25)])

    def test_vector_dispatch_crosses_overlapping_and_disjoint_tree_branches(self):
        patches = {"broad": field("broad", -32.0, -32.0, n=65,
                                  height=10000.0)}
        patches.update({
            str(i): field(str(i), float((i % 7) * 3 - 10),
                          float((i // 7) * 3 - 10),
                          n=3 + 2 * (i % 5), height=i * 100.0)
            for i in range(49)
        })
        points = [(x / 2 + 0.125, z / 2 + 0.375)
                  for x in range(-25, 36, 3) for z in range(-25, 36, 3)]
        self.assert_batch(patches, points)
        disjoint = {
            str(i): field(str(i), float((i % 16) * 64),
                          float((i // 16) * 64), n=3, height=i * 100.0)
            for i in range(256)
        }
        self.assert_batch(disjoint, [(f.x0 + 1.25, f.z0 + 1.75)
                                     for f in disjoint.values()])

    def test_nonfinite_and_large_integer_coordinates_keep_scalar_errors(self):
        fields = {"patch": field("patch", -3.0, -3.0)}
        for bad in ((math.nan, 0.0), (0.0, math.nan),
                    (math.inf, 0.0), (0.0, -math.inf),
                    (float(2**53), 0.0), (1e100, 0.0)):
            for points in ([(0.25, 0.25), bad, (100.0, 100.0)],
                           [(100.0, 100.0), bad]):
                with self.subTest(points=points):
                    self.assert_first_error(fields, points)

    def test_duplicate_requests_read_only_the_required_lattice_samples(self):
        patch = field("counted", -3.0, -3.0)
        patch.heights = CountedHeights(patch.heights)
        original = surface({"patch": patch})
        expected = original.at(0.25, 0.75)[0]
        patch.heights.requested = 0
        sampler = GeneratedSampler(original.fields, original.field_names)
        actual = sampler.sample_points(np.tile([0.25, 0.75], (1024, 1)))
        np.testing.assert_array_equal(actual, np.full(1024, expected))
        self.assertEqual(patch.heights.requested, 4)
        sampler.sample_points([[0.25, 0.75]])
        self.assertEqual(patch.heights.requested, 8,
                         "a later call must not reuse sampled height values")

    def test_empty_batch_has_float64_vector_shape(self):
        self.assert_batch({}, np.empty((0, 2), dtype=np.float64))


if __name__ == "__main__":
    unittest.main()
