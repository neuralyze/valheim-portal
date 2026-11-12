"""Isolated span-feedback geometry and real-search witnesses, not world acceptance."""
from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import network as net


def _corridor(*, basin=False):
    # A 4 m measurement grid keeps every fixture coordinate on a cell centre,
    # including negative world coordinates, without resampling a physical mask.
    n, step = 97, 4.0
    axis = np.arange(n, dtype=float) * step - 192.0
    xx, zz = np.meshgrid(axis, axis)
    heights = np.full((n, n), 80.0)
    if basin:
        # Entirely dry, 28 m deep, and no steeper than the search's .25 ceiling.
        # A flat bypass is available above z=128; no water-price change can
        # distinguish either alignment because neither contains a wet cell.
        heights -= np.maximum(0.0, 28.0 - np.maximum(abs(xx), abs(zz)) * 7.0 / 32.0)
    field = net.fieldmod.Field(
        'span-feedback', 0.0, 0.0, 194.0, step, n,
        heights.astype(np.float32), np.ones((n, n), dtype=np.uint8))
    corr = net.routemod.Corridor(
        field, cell_m=step, grade_max=.25,
        grade_weight=0.0, roughness_weight=0.0,
        bridge_entry_cost_m=0.0, bridge_per_m_cost=1.0,
        biome_penalty={})
    corr.poi_weight = 0.0
    corr.forbid = np.zeros((corr.m, corr.m), dtype=bool)
    corr.unread = np.zeros_like(corr.forbid)
    corr.soft = np.full((corr.m, corr.m), .125)
    return corr


def _physical_snapshot(corr):
    names = ('H', 'Hmin', 'Hmax', 'S', 'Bi', 'water', 'fillable', 'wet',
             'water_forbidden', 'dist_to_land_m', 'rough',
             'forbid', 'unread', 'soft')
    return {name: getattr(corr, name).copy() for name in names}


class SpanRemedyFeedbackTests(unittest.TestCase):
    def assert_physical_unchanged(self, corr, before):
        for name, values in before.items():
            with self.subTest(physical_array=name):
                np.testing.assert_array_equal(getattr(corr, name), values)

    def test_sparse_bent_spans_cover_their_geometry_not_chords_or_other_runs(self):
        corr = _corridor()
        rec = dict(width_m=6.0, nodes=[
            [-160.0, -80.0], [-120.0, -80.0], [0.0, -80.0],
            [0.0, 40.0], [60.0, 40.0], [80.0, 120.0],
            [112.0, 120.0], [-140.0, 160.0], [0.0, 160.0]],
            crossings=[
                dict(stations=[1, 3], span_m=169.71, reason='chasm'),
                dict(stations=[5, 6], span_m=32.0, reason='water_body'),
                dict(stations=[7, 8], span_m=140.0, reason='clamp_exceeded')])
        original = copy.deepcopy(rec)
        physical = _physical_snapshot(corr)
        hint = net.span_retry_guidance(corr, rec)
        self.assertIsInstance(hint, np.ndarray)
        self.assertEqual(hint.shape, (corr.m, corr.m))
        self.assertEqual(hint.dtype, np.dtype(bool))
        # Include BOTH station limits, intermediate stations and the segments
        # joining sparse stations, with width plus a cell-footprint allowance.
        for point in ((-120., -80.), (-60., -80.), (0., -80.),
                      (0., -20.), (0., 40.), (-60., -76.),
                      (-140., 160.), (-72., 160.), (0., 160.)):
            with self.subTest(included=point):
                self.assertTrue(hint[corr.cell_of(*point)])
        # None of the chord interior, ordinary connecting road, or unrelated
        # allowed span is a footprint of an over-ceiling crossing window.
        for point in ((-60., -20.), (-152., -80.), (48., 40.),
                      (96., 120.), (-16., 140.), (12., -64.)):
            with self.subTest(excluded=point):
                self.assertFalse(hint[corr.cell_of(*point)])
        self.assertEqual(rec, original)
        self.assert_physical_unchanged(corr, physical)
        self.assertIsNone(corr.guidance)

    def test_exact_ceiling_and_shorter_spans_supply_no_retry_hint(self):
        corr = _corridor()
        rec = dict(width_m=6.0,
                   nodes=[[-96., 0.], [0., 0.], [32., 0.]],
                   crossings=[dict(stations=[0, 1],
                                   span_m=net.MAX_BRIDGE_SPAN_M),
                              dict(stations=[1, 2], span_m=32.0)])
        self.assertIsNone(net.span_retry_guidance(corr, rec))
        rec['crossings'][0]['span_m'] = net.MAX_BRIDGE_SPAN_M + .01
        hint = net.span_retry_guidance(corr, rec)
        self.assertIsNotNone(hint)
        self.assertTrue(hint[corr.cell_of(-48., 0.)])
        self.assertFalse(hint[corr.cell_of(24., 0.)])

    def test_no_measurable_overlong_geometry_supplies_no_hint(self):
        corr = _corridor()
        for rec in (
                dict(width_m=6.0, nodes=[], crossings=[]),
                dict(width_m=6.0, nodes=[[-160., 0.], [160., 0.]],
                     crossings=[]),
                dict(width_m=6.0, nodes=[[1000., 1000.], [1200., 1000.]],
                     crossings=[dict(stations=[0, 1], span_m=200.0)])):
            with self.subTest(record=rec):
                self.assertIsNone(net.span_retry_guidance(corr, rec))

    def test_road_footprint_width_controls_guidance_without_mask_changes(self):
        corr = _corridor()
        rec = dict(width_m=6.0, nodes=[[-96., -40.], [96., -40.]],
                   crossings=[dict(stations=[0, 1], span_m=192.0)])
        physical = _physical_snapshot(corr)
        narrow = net.span_retry_guidance(corr, rec)
        rec['width_m'] = 22.0
        wide = net.span_retry_guidance(corr, rec)
        # 12 m is outside the 4 m narrow half-footprint plus half a cell
        # diagonal, but inside the widened 12 m half-footprint.
        probe = corr.cell_of(0., -28.)
        self.assertFalse(narrow[probe])
        self.assertTrue(wide[probe])
        self.assertFalse(wide[corr.cell_of(0., -16.)])
        self.assert_physical_unchanged(corr, physical)

    def _dry_alignment_fixture(self, *, bypass):
        corr = _corridor(basin=True)
        ii, jj = np.mgrid[:corr.m, :corr.m]
        xx, zz = corr.x0 + jj * corr.cell_m, corr.z0 + ii * corr.cell_m
        direct = (zz == 0.) & (abs(xx) <= 160.)
        allowed = direct.copy()
        if bypass:
            allowed |= ((abs(xx) == 160.) & (zz >= 0.) & (zz <= 144.))
            allowed |= ((zz == 144.) & (abs(xx) <= 160.))
        corr.forbid = ~allowed
        original = corr.search((-160., 0.), (160., 0.))
        self.assertTrue(all(z == 0. for _x, z in original))
        self.assertFalse(corr.water.any())
        # The bank-to-bottom drop cannot fit trunk grade inside the clamp:
        # even taking the full cut/fill at BOTH ends leaves too much descent.
        self.assertGreater(28.0 - 2 * net.routemod.CUT_FILL_MAX,
                           128.0 * net.routemod.GRADE['trunk'])
        first = original.index((-128., 0.))
        last = original.index((128., 0.))
        rec = dict(width_m=6.0, nodes=[list(p) for p in original],
                   crossings=[dict(stations=[first, last], span_m=256.0,
                                   reason='clamp_exceeded',
                                   water_surface_y=None, gap_min_y=52.0)])
        return corr, original, rec

    def test_dry_span_feedback_routes_real_search_onto_flat_physical_bypass(self):
        corr, original, rec = self._dry_alignment_fixture(bypass=True)
        physical = _physical_snapshot(corr)
        field_heights = corr.fld.h.copy()
        hint = net.span_retry_guidance(corr, rec)
        guided = copy.copy(corr)
        guided.guidance = hint.astype(float) * 16.0
        alternate = guided.search(original[0], original[-1])
        self.assertNotEqual(alternate, original)
        self.assertEqual((alternate[0], alternate[-1]),
                         (original[0], original[-1]))
        self.assertTrue(any(z >= 140. for _x, z in alternate))
        for point in alternate:
            cell = corr.cell_of(*point)
            self.assertFalse(corr.forbid[cell])
            self.assertFalse(corr.water_forbidden[cell])
            # This entire route lies on the dry, level 80 m bank: no profile
            # raise, forbidden-cell removal, drain, or bridge is needed.
            self.assertEqual(corr.fld.at(*point), 80.0)
        self.assert_physical_unchanged(corr, physical)
        np.testing.assert_array_equal(corr.fld.h, field_heights)
        self.assertIsNone(corr.guidance)
        # Changed guidance must not replay the previously guided route.
        guided.guidance = None
        self.assertEqual(guided.search(original[0], original[-1]), original)

    def test_span_guidance_does_not_forbid_the_only_search_alignment(self):
        corr, original, rec = self._dry_alignment_fixture(bypass=False)
        physical = _physical_snapshot(corr)
        guided = copy.copy(corr)
        guided.guidance = net.span_retry_guidance(corr, rec).astype(float) * 16.0
        # This remains a physically refused span at the consumer, but search
        # must still measure it: guidance is not permission to remove cells.
        self.assertEqual(guided.search(original[0], original[-1]), original)
        self.assert_physical_unchanged(corr, physical)


if __name__ == '__main__':
    unittest.main()
