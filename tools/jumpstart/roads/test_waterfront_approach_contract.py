"""A solved shore's inland approach must survive ordinary road construction."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import network as N
import ribbon as R
from test_span_remedy_feedback import _physical_snapshot


class WaterfrontApproachTests(unittest.TestCase):
    def setUp(self):
        n = 128
        axis = np.arange(n, dtype=float) - n / 2 + .5
        _x, z = np.meshgrid(axis, axis)
        # Along the coast a wide road needs a bridge, incompatible with the
        # harbour's low datum. Its certified northern entry has dry shoulders
        # and enough distance for the same legal grade/clamp to reach that datum.
        heights = (31.1 + .2 * (z - .5)).astype(np.float32)
        self.field = N.fieldmod.Field(
            'coast', 0., 0., n / 2, 1., n, heights,
            np.ones((n, n), dtype=np.uint8))
        self.corr = N.routemod.Corridor(
            self.field, cell_m=1., grade_max=N.routemod.GRADE_CEILING)
        self.corr.forbid = np.zeros((self.corr.m, self.corr.m), dtype=bool)
        self.corr.unread = np.zeros_like(self.corr.forbid)
        self.corr.soft = np.full((self.corr.m, self.corr.m), .125)
        self.nodes = {
            'inland': dict(xz=(48.5, .5), patch='coast', island='test'),
            'harbour': dict(xz=(.5, .5), patch='coast', island='test',
                            kind='waterfront_terminal', owner='Crossings',
                            level_y=31., arrive_bearing_deg=0.)}
        self.edge = dict(id='coastal-arrival', a='inland', b='harbour',
                         cls='trunk', pri=1, width_m=6.)
        self.enterContext(patch.object(N.specmod, 'NODES', self.nodes))
        self.enterContext(patch.object(N, '_PAD_CLAIMS', []))
        self.enterContext(patch.object(N, '_PAD_DATUMS', {}))
        self.enterContext(patch.object(N, '_NODE_ANCHORS', {}))
        self.enterContext(patch.object(N, '_ARRIVAL_STARTS', {}))
        self.enterContext(patch.object(
            N, '_PLAN_PAD_KEEPOUTS', lambda: R.PadKeepOut([], [], {})))

    def test_whole_road_can_reach_owned_shore_datum_from_either_direction(self):
        before = _physical_snapshot(self.corr)
        for reversed_road in (False, True):
            edge = dict(self.edge)
            if reversed_road:
                edge['a'], edge['b'] = edge['b'], edge['a']
            with self.subTest(reversed_road=reversed_road):
                joint = {}
                result = N.build_segment(
                    self.field, edge, self.corr, [], census=None, joint=joint)
                self.assertEqual(result['profile_constraints']['verdict'], 'CLEAR')
                self.assertIn(result['self_adjacency']['verdict'], ('CLEAR', 'JOINED'))
                y = np.asarray(joint['y0'])
                self.assertTrue(np.all(y >= joint['lo'] - 1e-9))
                self.assertTrue(np.all(y <= joint['hi'] + 1e-9))
                self.assertTrue(np.all(np.abs(np.diff(y)) <=
                    result['grade_limit'] * np.diff(joint['s']) + 1e-9))
                self.assertLessEqual(result['grade_limit'], N.routemod.GRADE_CEILING)
                endpoint = result['nodes'][0 if reversed_road else -1]
                np.testing.assert_allclose(endpoint, self.nodes['harbour']['xz'], atol=.005)
                self.assertEqual(result['abutments'][0]['level_y'], 31.)
                self.assertAlmostEqual(
                    result['abutments'][0]['outbound_bearing_deg'], 180., places=2)
        for name, values in before.items():
            np.testing.assert_array_equal(getattr(self.corr, name), values)

    def test_directed_approach_cannot_cross_a_leg_local_physical_mask(self):
        extra = np.zeros_like(self.corr.forbid)
        # The interior of the owned six-metre landing, not merely its A* goal.
        extra[self.corr.cell_of(.5, 3.5)] = True
        before = extra.copy()
        contacts = N._bank_approach_contacts(self.edge, 'b', self.corr, extra)
        self.assertEqual(contacts, [])
        np.testing.assert_array_equal(extra, before)

    def test_dry_shore_constructor_cannot_bypass_a_mask_inside_fixed_landing(self):
        self.field.h.fill(31.1)
        corr = N.routemod.Corridor(
            self.field, cell_m=1., grade_max=N.routemod.GRADE_CEILING)
        corr.forbid = np.zeros((corr.m, corr.m), dtype=bool)
        corr.unread = np.zeros_like(corr.forbid)
        result = N.build_segment(
            self.field, self.edge, corr, [], land_only=True, census=None)
        self.assertEqual(result['profile_constraints']['verdict'], 'CLEAR')
        extra = np.zeros_like(corr.forbid)
        extra[corr.cell_of(.5, 3.5)] = True
        with self.assertRaises(ValueError):
            N.build_segment(self.field, self.edge, corr, [], land_only=True,
                            census=None, extra_forbid=extra)

    def test_both_road_modes_keep_the_owned_six_metre_turn(self):
        self.field.h.fill(31.1)
        self.nodes['harbour'].update(
            arrive_bearing_deg=90., jetty_bearing_deg=270.)
        self.nodes['inland']['xz'] = (8.5, 1.5)
        corr = N.routemod.Corridor(
            self.field, cell_m=1., grade_max=N.routemod.GRADE_CEILING)
        corr.forbid = np.zeros((corr.m, corr.m), dtype=bool)
        corr.unread = np.zeros_like(corr.forbid)
        for land_only in (False, True):
            with self.subTest(land_only=land_only):
                result = N.build_segment(
                    self.field, self.edge, corr, [], land_only=land_only, census=None)
                self.assertEqual(result['profile_constraints']['verdict'], 'CLEAR')
                points = np.asarray(result['nodes'], dtype=float)[::-1]
                arcs = np.r_[0., np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
                np.testing.assert_array_equal(
                    points[arcs <= N.BANK_LEVEL_M, 1],
                    np.full(np.count_nonzero(arcs <= N.BANK_LEVEL_M), .5))

    def test_moved_landing_cannot_supply_owned_profile_retry_geometry(self):
        self.field.h.fill(31.1)
        axis = np.arange(self.field.n, dtype=float) - self.field.n / 2 + .5
        x, z = np.meshgrid(axis, axis)
        self.field.h[(abs(x) <= 12.) & (abs(z) <= 8.)] = 28.
        corr = N.routemod.Corridor(
            self.field, cell_m=4., grade_max=N.routemod.GRADE_CEILING)
        corr.forbid = np.zeros((corr.m, corr.m), dtype=bool)
        corr.unread = np.zeros_like(corr.forbid)
        corr.soft = np.full((corr.m, corr.m), .125)
        extra = np.zeros_like(corr.forbid)
        extra[corr.cell_of(.5, 3.5)] = True
        before = _physical_snapshot(corr)
        with self.assertRaises(ValueError) as refused:
            N.build_segment(
                self.field, self.edge, corr, [], census=None, extra_forbid=extra)
        self.assertIsNone(N._refused_adjacency_candidate(refused.exception))
        for name, values in before.items():
            np.testing.assert_array_equal(getattr(corr, name), values)

    def test_zero_length_search_is_not_a_constructed_road(self):
        self.field.h.fill(31.1)
        self.nodes['harbour']['kind'] = 'extremity'
        self.nodes['inland']['xz'] = self.nodes['harbour']['xz']
        corr = N.routemod.Corridor(
            self.field, cell_m=1., grade_max=N.routemod.GRADE_CEILING)
        corr.forbid = np.zeros((corr.m, corr.m), dtype=bool)
        corr.unread = np.zeros_like(corr.forbid)
        with self.assertRaises(ValueError):
            N.build_segment(self.field, self.edge, corr, [], census=None)


if __name__ == '__main__':
    unittest.main()
