"""Constructed numeric preview checks, not evidence of physical road success."""
import math
from pathlib import Path
import sys
import unittest

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from roads import north_itinerary as N
from roads.field import Field


class CountedCoordinate(float):
    """Count bbox work even when a scan caches or inlines field bounds."""
    comparisons = 0

    def __le__(self, other):
        type(self).comparisons += 1
        return float.__le__(self, other)

    def __ge__(self, other):
        type(self).comparisons += 1
        return float.__ge__(self, other)


def field(name, x0=-3.0, z0=-3.0, *, n=7, step=1.0, height=0.0):
    half = n * step / 2
    heights = (np.arange(n * n, dtype=np.float32).reshape(n, n)
               + np.float32(height))
    return Field(name, x0 + half - step / 2, z0 + half - step / 2,
                 half, step, n, heights, np.zeros((n, n), dtype=np.uint8))


class NorthFieldIndexTests(unittest.TestCase):
    def assert_selection(self, fields, queries):
        index = N._aligned_field_index(fields)
        for a, b, margin in queries:
            with self.subTest(a=a, b=b, margin=margin):
                try:
                    expected = N._field_for(fields, a, b, margin=margin)
                except ValueError:
                    with self.assertRaises(ValueError):
                        N._field_for(fields, a, b, margin=margin, index=index)
                else:
                    self.assertIs(N._field_for(fields, a, b, margin=margin,
                                               index=index), expected)
        return index

    def test_coarse_and_half_grid_fields_are_ineligible(self):
        eligible = field("eligible", -6.0, -6.0, n=13, height=100.0)
        excluded = {
            "coarse": field("coarse", -2.0, -2.0, n=3, step=2.0),
            "half-x": field("half-x", -2.5, -3.0),
            "half-z": field("half-z", -3.0, -2.5),
            "step-outside": field("step-outside", step=1.0 + 2e-9),
            "x-outside": field("x-outside", -3.0 + 2e-6),
            "z-outside": field("z-outside", z0=-3.0 + 2e-6),
        }
        fields = {**excluded, "eligible": eligible}
        index = self.assert_selection(fields, [([0.0, 0.0], [0.0, 0.0], 0.0)])
        self.assertIs(N._field_for(fields, [0.0, 0.0], [0.0, 0.0],
                                   margin=0.0, index=index), eligible)
        self.assert_selection(excluded, [([0.0, 0.0], [0.0, 0.0], 0.0)])
        # Eligibility uses the original tolerances, not exact unit-step or
        # integer-origin equality. These remain real, slightly shifted fields.
        for near in (field("near-step", step=1.0 + 5e-10),
                     field("near-x", -3.0 + 5e-7),
                     field("near-z", z0=-3.0 + 5e-7)):
            with self.subTest(field=near.id):
                near_index = N._aligned_field_index({"near": near})
                self.assertIs(N._field_for({"near": near}, [0.0, 0.0],
                                           [0.0, 0.0], margin=0.0,
                                           index=near_index), near)

    def test_priority_is_half_then_id_and_equal_keys_keep_insertion_order(self):
        broad = field("a-broad", -6.0, -6.0, n=13)
        later_id = field("z-inset", -1.0, -1.0, n=3)
        first = field("a-inset", -1.0, -1.0, n=3, height=-100.0)
        tied = field("a-inset", -1.0, -1.0, n=3, height=100.0)
        for leading, trailing in ((first, tied), (tied, first)):
            fields = {"broad": broad, "later-id": later_id,
                      "leading": leading, "trailing": trailing}
            index = self.assert_selection(fields, [
                ([5.0, 5.0], [5.0, 5.0], 0.0),
                ([0.0, 0.0], [0.0, 0.0], 0.0),
                ([-1.0, -1.0], [1.0, 1.0], 0.0),
            ])
            self.assertIs(N._field_for(fields, [0.0, 0.0], [0.0, 0.0],
                                       margin=0.0, index=index), leading)
            self.assertEqual(leading.at(0.0, 0.0),
                             -96.0 if leading is first else 104.0)

    def test_one_field_must_cover_both_endpoints(self):
        left = field("left", -5.0, -1.0, n=3)
        right = field("right", 3.0, -1.0, n=3)
        broad = field("broad", -8.0, -8.0, n=17)
        a, b = [-4.0, 0.0], [4.0, 0.0]
        fields = {"left": left, "right": right, "broad": broad}
        index = self.assert_selection(fields, [(a, b, 0.0), (b, a, 0.0)])
        self.assertIs(N._field_for(fields, a, b, margin=0.0, index=index), broad)
        self.assert_selection({"left": left, "right": right},
                              [(a, b, 0.0), (b, a, 0.0)])

    def test_inclusive_outer_half_cells_and_all_margin_signs(self):
        f = field("outer-frame")
        fields = {"f": f}
        queries = []
        for margin in (0.0, 0.125, 1.0, 3.5, 3.625, -0.25, -20.0):
            xmin, xmax = f.cx - f.half + margin, f.cx + f.half - margin
            zmin, zmax = f.cz - f.half + margin, f.cz + f.half - margin
            for x in dict.fromkeys((xmin, xmax, math.nextafter(xmin, -math.inf),
                                    math.nextafter(xmax, math.inf), f.cx)):
                for z in dict.fromkeys((zmin, zmax, f.cz)):
                    queries.append(([x, z], [x, z], margin))
            queries.extend((([xmin, zmin], [xmax, zmax], margin),
                            ([xmax, zmax], [xmin, zmin], margin)))
        index = self.assert_selection(fields, queries)
        # Unlike the water sampler's sample-centre bounds, north contains
        # the outside half-cells all the way to +/-3.5, inclusively.
        for point in ([-3.5, -3.5], [3.5, 3.5], [-3.25, 0.0], [3.25, 0.0]):
            self.assertIs(N._field_for(fields, point, point, margin=0.0,
                                       index=index), f)
        with self.assertRaises(ValueError):
            N._field_for(fields, [0.0, 0.0], [0.0, 0.0],
                         margin=3.625, index=index)

    def test_overlapping_branches_preserve_endpoint_and_margin_priority(self):
        fields = {str(i): field(str(i % 5), float((i % 7) * 3 - 10),
                                float((i // 7) * 3 - 10),
                                n=3 + 2 * (i % 5), height=i * 100.0)
                  for i in range(49)}
        queries = [([x / 2, z / 2], [x / 2 + dx, z / 2 - 1.0], margin)
                   for x in range(-25, 36, 6)
                   for z in range(-25, 36, 6)
                   for dx in (0.0, 4.0)
                   for margin in (-5.0, -0.25, 0.0, 0.5, 2.0, 20.0)]
        self.assert_selection(fields, queries)

    def test_absent_or_ineligible_coverage_is_refused(self):
        a, b, margin = [-4.0, 2.0], [8.0, 9.0], 1.25
        for fields in ({}, {"elsewhere": field("elsewhere", 100.0, 100.0)},
                       {"half-grid": field("half-grid", -3.5, -3.5)}):
            index = N._aligned_field_index(fields)
            with self.subTest(fields=list(fields)):
                with self.assertRaises(ValueError):
                    N._field_for(fields, a, b, margin=margin, index=index)
        f = field("default-margin", -40.0, -40.0, n=81)
        fields = {"f": f}
        index = N._aligned_field_index(fields)
        self.assertIs(N._field_for(fields, [0.0, 0.0], [0.0, 0.0], index=index), f)
        with self.assertRaises(ValueError):
            N._field_for(fields, [9.0, 0.0], [9.0, 0.0], index=index)

    def test_selected_field_keeps_actual_at_and_bilinear_samples(self):
        broad = field("broad", -6.0, -6.0, n=13, height=1000.0)
        inset = field("inset")
        inset.h[3:5, 3:5] = [[11.25, 23.5], [37.75, -9.125]]
        fields = {"broad": broad, "inset": inset}
        index = N._aligned_field_index(fields)
        selected = N._field_for(fields, [0.25, 0.75], [0.25, 0.75],
                                margin=0.0, index=index)
        self.assertIs(selected, inset)
        self.assertEqual(selected.at(0.0, 0.0), 11.25)
        self.assertEqual(selected.at(1.0, 1.0), -9.125)
        self.assertEqual(selected.bilinear(0.25, 0.75), 23.1015625)
        # Only metadata is indexed: neither sample values nor interpolated
        # results may be memoized, even for repeated identical coordinates.
        inset.h[3, 3] = -4.75
        selected = N._field_for(fields, [0.25, 0.75], [0.25, 0.75],
                                margin=0.0, index=index)
        self.assertEqual(selected.at(0.0, 0.0), -4.75)
        self.assertEqual(selected.bilinear(0.25, 0.75), 20.1015625)

    def test_spatial_work_does_not_scan_every_disjoint_field(self):
        for count in (64, 4096):
            fields = {str(i): field(str(i), float((i % 64) * 64),
                                    float((i // 64) * 64), n=3)
                      for i in range(count)}
            index = N._aligned_field_index(fields)
            for chosen_id in (0, count // 2, count - 1):
                chosen = fields[str(chosen_id)]
                a = [CountedCoordinate(chosen.x0), CountedCoordinate(chosen.z0)]
                b = [CountedCoordinate(chosen.x0 + 2.0),
                     CountedCoordinate(chosen.z0 + 2.0)]
                CountedCoordinate.comparisons = 0
                self.assertIs(N._field_for(fields, a, b, margin=0.0), chosen)
                linear_work = CountedCoordinate.comparisons
                CountedCoordinate.comparisons = 0
                self.assertIs(N._field_for(fields, a, b, margin=0.0,
                                           index=index), chosen)
                indexed_work = CountedCoordinate.comparisons
                with self.subTest(count=count, chosen=chosen_id):
                    self.assertGreaterEqual(linear_work, count)
                    self.assertLess(indexed_work, 200)
                    if count == 4096:
                        self.assertLess(indexed_work * 16, linear_work)

    def test_fresh_index_reflects_collection_and_geometry_changes(self):
        broad = field("broad", -10.0, -10.0, n=21)
        inset = field("inset", -1.0, -1.0, n=3)
        fields = {"broad": broad}
        point = [0.0, 0.0]
        index = N._aligned_field_index(fields)
        self.assertIs(N._field_for(fields, point, point, margin=0.0,
                                   index=index), broad)
        fields["inset"] = inset
        index = N._aligned_field_index(fields)
        self.assertIs(N._field_for(fields, point, point, margin=0.0,
                                   index=index), inset)
        inset.cx += 128.0
        inset.cz += 128.0
        index = N._aligned_field_index(fields)
        self.assertIs(N._field_for(fields, point, point, margin=0.0,
                                   index=index), broad)
        moved = [128.0, 128.0]
        self.assertIs(N._field_for(fields, moved, moved, margin=0.0,
                                   index=index), inset)
        del fields["inset"]
        self.assert_selection(fields, [(moved, moved, 0.0)])

    def test_writer_scope_rebuilds_without_caching_sample_values(self):
        broad = field("broad", -33.0, -33.0, n=67, height=1000.0)
        fields = {"broad": broad}
        first = N.scoped_writer_patches(fields, [(0, 0)])["z_0_0"]
        centre = 32 * 65 + 32
        self.assertEqual(first.heights[centre], 3244.0)
        inset = field("inset", -1.0, -1.0, n=3, height=-100.0)
        fields["inset"] = inset
        second = N.scoped_writer_patches(fields, [(0, 0)])["z_0_0"]
        self.assertEqual(second.heights[centre], -96.0)
        inset.h[1, 1] = -7.125
        third = N.scoped_writer_patches(fields, [(0, 0)])["z_0_0"]
        self.assertEqual(third.heights[centre], -7.125)
        self.assertEqual(second.heights[centre], -96.0)
        inset.cx += 128.0
        inset.cz += 128.0
        fourth = N.scoped_writer_patches(fields, [(0, 0)])["z_0_0"]
        self.assertEqual(fourth.heights[centre], 3244.0)
        del fields["broad"]
        with self.assertRaises(ValueError):
            N.scoped_writer_patches(fields, [(0, 0)])

    def test_planned_scope_uses_numeric_heights_and_rebuilds_geometry(self):
        broad = field("broad", -6.0, -6.0, n=13, height=1000.0)
        fields = {"broad": broad}
        original, _ = N.planned_surface(fields, [], None)
        self.assertEqual(original.at(0.25, 0.75), (1094.0, 1094.0, 0.0))
        inset = field("inset")
        inset.h[3:5, 3:5] = [[11.25, 23.5], [37.75, -9.125]]
        fields["inset"] = inset
        preview, _ = N.planned_surface(fields, [], None)
        self.assertEqual(preview.at(0.0, 0.0), (11.25, 11.25, 0.0))
        self.assertEqual(preview.at(0.25, 0.75),
                         (23.1015625, 23.1015625, 0.0))
        inset.h[3, 3] = -4.75
        self.assertEqual(preview.at(0.25, 0.75),
                         (20.1015625, 20.1015625, 0.0))
        inset.cx += 128.0
        inset.cz += 128.0
        rebuilt, _ = N.planned_surface(fields, [], None)
        self.assertEqual(rebuilt.at(0.25, 0.75), (1094.0, 1094.0, 0.0))
        self.assertEqual(rebuilt.at(128.25, 128.75),
                         (20.1015625, 20.1015625, 0.0))
        del fields["inset"]
        removed, _ = N.planned_surface(fields, [], None)
        with self.assertRaises(ValueError):
            removed.at(128.25, 128.75)


if __name__ == "__main__":
    unittest.main()
