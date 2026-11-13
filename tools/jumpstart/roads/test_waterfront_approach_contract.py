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


if __name__ == '__main__':
    unittest.main()
