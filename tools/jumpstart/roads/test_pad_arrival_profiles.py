"""A locally reachable pad must also be reachable along the incoming road."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np

sys.path[:0] = [str(Path(__file__).resolve().parent),
               str(Path(__file__).resolve().parents[1])]
import network as N
import ribbon as R


class PadArrivalProfileTests(unittest.TestCase):
    def setUp(self):
        n = 128
        axis = np.arange(n, dtype=float) - n / 2 + .5
        x, z = np.meshgrid(axis, axis)
        # The pad and its short added spur stand on a higher terrace. The
        # incoming west road crosses a low shelf too close to the pad to
        # climb to its datum inside the unchanged cut/fill and grade limits.
        heights = np.full((n, n), 44., dtype=np.float32)
        heights[(x <= -5.) & (np.abs(z - .5) < 12.)] = 40.
        self.field = N.fieldmod.Field(
            'arrival-terrain', 0., 0., n / 2, 1., n, heights,
            np.ones((n, n), dtype=np.uint8))
        self.pad = dict(site_id='terrace', name='terrace', x=14.5, z=.5,
                        hw=10., hd=10., datum_y=50., planned=True,
                        role='site_pad')
        self.pads = R.pad_keepouts_from_chain([], [], planned=[self.pad])
        self.nodes = {
            'source': dict(xz=(-45.5, .5), patch=self.field.id, island='test'),
            'target': dict(xz=(14.5, .5), patch=self.field.id, island='test')}
        self.edge = dict(id='incoming', a='source', b='target', cls='spur', pri=1)
        self.corr = N.routemod.Corridor(
            self.field, cell_m=1., grade_max=N.routemod.GRADE_CEILING)
        self.corr.forbid = N.pad_forbid_mask(
            [self.pad], self.corr.x0, self.corr.z0, self.corr.cell_m,
            self.corr.m, N.routemod.footprint_half_m(N.WIDTH_M['spur']))
        self.corr.arrival_forbid = np.zeros_like(self.corr.forbid)
        self.anchors = {'target': (-3.5, .5)}
        self.enterContext(patch.object(N.specmod, 'NODES', self.nodes))
        self.enterContext(patch.object(N, '_PAD_CLAIMS', [self.pad]))
        self.enterContext(patch.object(N, '_PAD_DATUMS', {'terrace': 50.}))
        self.enterContext(patch.object(N, '_NODE_ANCHORS', self.anchors))
        self.enterContext(patch.object(N, '_ARRIVAL_STARTS', {}))
        self.enterContext(patch.object(N, '_PLAN_PAD_KEEPOUTS', lambda: self.pads))

    def test_short_spur_can_meet_datum_but_full_fixed_line_cannot(self):
        local = N.arrival_reach(
            (-3.5, .5), self.nodes['target']['xz'], [self.pad],
            {'terrace': 50.}, self.field, N.WIDTH_M['spur'])
        self.assertEqual(local['verdict'], 'MEETS')
        # The nearest attachment's short spur is not a feasibility proof for
        # this complete line. Never move fixed geometry or return a profile
        # metres below the pad and wait for the network-wide joint to fail.
        points = [[float(x), .5] for x in np.arange(-45.5, -.5, 2.)]
        points.append([1.45, .5])
        with self.assertRaises(ValueError) as caught:
            N.build_segment(self.field, self.edge, self.corr, [], census=None,
                            fixed_pts=points)
        binding = caught.exception.refusal['binding']
        self.assertIn('terrace', binding['segments'])
        self.assertGreater(binding['interval_there'][0],
                           binding['interval_there'][1])

    def test_search_retries_same_pad_with_full_native_profile(self):
        old_mask = self.corr.forbid.copy()
        old_anchors = dict(self.anchors)
        joint = {}
        # Keep the initial route on the direct west approach without
        # forbidding the viable northern attachment. Retries must retain
        # this real soft-cost overlay, not turn it into a physical wall.
        _, z = np.meshgrid(np.arange(self.corr.m), np.arange(self.corr.m))
        centre_row = self.corr.cell_of(-3.5, .5)[0]
        guidance = np.where(np.abs(z - centre_row) > 1, 1000., 0.)
        result = N.build_segment(
            self.field, self.edge, self.corr, [], census=None, joint=joint,
            extra_guidance=guidance)
        # No datum, clamp, ladder, pad footprint, shared anchor, or physical
        # search mask was relaxed to make the alternative attachment work.
        self.assertEqual(self.pad['datum_y'], 50.)
        self.assertEqual(self.anchors, old_anchors)
        np.testing.assert_array_equal(self.corr.forbid, old_mask)
        y = np.asarray(result['profile_y'])
        self.assertTrue(np.all(y >= joint['lo'] - .001))
        self.assertTrue(np.all(y <= joint['hi'] + .001))
        self.assertTrue(np.all(np.abs(np.diff(y)) <=
                               result['grade_limit'] * np.diff(joint['s']) + .002))
        self.assertLessEqual(result['grade_limit'], N.routemod.GRADE_CEILING)
        # Ask the native pad-pair gate again on the emitted, rounded profile.
        gate = N.routemod._junction()
        ribs = gate.ribbons_from_segments({'segments': [result]}, 'test')
        pad = dict(kind='pad', id='terrace', label='terrace', role='site_pad',
                   x=14.5, z=.5, hw=10., hd=10., datum_y=50.)
        rows = gate.find_conflicts(ribs, [pad], ('pad_datums',))['pairs']
        self.assertTrue(rows)
        self.assertTrue(all(row['spread_m'] <= row['slide_allow_m'] + 1e-6
                            for row in rows))

    def test_unrelated_pad_does_not_become_a_terminal_constraint(self):
        unrelated = dict(site_id='other', name='other', x=-25.5, z=25.5,
                         hw=2., hd=2., datum_y=70., planned=True)
        pads = R.pad_keepouts_from_chain([], [], planned=[self.pad, unrelated])
        points = [[-35.5, 18.5], [-15.5, 18.5], [1.45, 12.5]]
        with patch.object(N, '_PLAN_PAD_KEEPOUTS', lambda: pads):
            result = N.build_segment(
                self.field, self.edge, self.corr, [], census=None,
                fixed_pts=points)
        self.assertTrue(all(y < 60. for y in result['profile_y']))

    def test_no_eligible_same_pad_escape_remains_numerically_refused(self):
        # Census/POI-style exclusion leaves only a thin west approach. The
        # retry may not remove that mask or escape to an unrelated pad.
        blocked = np.abs(np.arange(self.corr.m)[:, None] -
                         self.corr.cell_of(-3.5, .5)[0]) > 1
        self.corr.arrival_forbid = np.broadcast_to(
            blocked, self.corr.forbid.shape).copy()
        self.corr.forbid |= self.corr.arrival_forbid
        before = self.corr.forbid.copy()
        with self.assertRaises(ValueError):
            N.build_segment(self.field, self.edge, self.corr, [], census=None)
        np.testing.assert_array_equal(self.corr.forbid, before)
        self.assertEqual(self.anchors, {'target': (-3.5, .5)})


if __name__ == '__main__':
    unittest.main()
