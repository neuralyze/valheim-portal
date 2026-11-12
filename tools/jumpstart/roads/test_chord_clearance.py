"""A straight bridge must not cut through a location the search avoided."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from roads import network as net


class ChordClearanceTests(unittest.TestCase):
    def setUp(self):
        n = 256
        axis = np.arange(n, dtype=np.float64) - n/2 + .5
        xx, zz = np.meshgrid(axis, axis)
        heights = np.where(np.abs(zz) < 36., 27., 40.).astype(np.float32)
        self.field = net.fieldmod.Field('coast', 0., 0., n/2, 1., n,
                                       heights, np.ones((n,n), np.uint8))
        self.poi = net.poimod.Poi('Scenery', 0., 0., 30., 12., False, 100)
        self.corr = net.routemod.Corridor(self.field, cell_m=4., grade_max=.25,
                                        bridge_entry_cost_m=0., bridge_per_m_cost=1.)
        # Isolate the hard-forbid invariant, independent of soft taste costs.
        self.corr.forbid, _, _ = net.poimod.masks(
            [self.poi], self.corr.x0, self.corr.z0, self.corr.cell_m,
            self.corr.m, 3., chord_slack_m=net.chord_slack_m(self.corr.cell_m))
        self.edge = dict(id='cross-coast', a='south', b='north', cls='trunk', pri=1)
        nodes = {'south':dict(xz=(-60.,-100.), patch='coast', island='land'),
                 'north':dict(xz=(60.,100.), patch='coast', island='land')}
        self.enterContext(patch.object(net.specmod, 'NODES', nodes))
        self.enterContext(patch.object(net, '_PAD_CLAIMS', []))
        self.enterContext(patch.object(net, '_NODE_ANCHORS', {}))
        self.enterContext(patch.object(net, '_ARRIVAL_STARTS', {}))
        self.enterContext(patch.object(net, '_PLAN_PATCHES', None))
        self.enterContext(patch.object(net, '_PLAN_KEEPOUTS', None))
        self.enterContext(patch.object(net, '_PLAN_PAD_KEEPOUTS', None))

    def test_searched_detour_remains_clear_after_bridge_alignment(self):
        original_forbid = self.corr.forbid.copy()
        raw = self.corr.search((-60.,-100.), (60.,100.))
        raw_gate = net.poimod.gate_check(raw, 3., [self.poi])
        self.assertEqual(raw_gate['firm_violations'], [])
        result = net.build_segment(self.field, self.edge, self.corr, [self.poi], census=None)
        verdict = net.poimod.gate_check(result['nodes'], 3., [self.poi])
        self.assertEqual(verdict['firm_violations'], [])
        distance = net.poimod.polyline_dist(result['nodes'], [self.poi.x], [self.poi.z])[0]
        self.assertGreaterEqual(distance, self.poi.hard_m(3.))
        self.assertTrue(any(result['is_bridge']))
        np.testing.assert_array_equal(self.corr.forbid, original_forbid)
        for crossing in result['crossings']:
            self.assertLessEqual(crossing['span_m'], net.MAX_BRIDGE_SPAN_M)
            a, b = np.asarray(crossing['centreline_straight_from_to'], dtype=float)
            first, last = crossing['stations']
            points = np.asarray(result['nodes'][first:last+1])
            delta = b-a
            distance = np.abs((points[:,0]-a[0])*delta[1] -
                              (points[:,1]-a[1])*delta[0]) / np.linalg.norm(delta)
            self.assertLessEqual(float(distance.max()), .02)

    def test_protected_standoff_is_preserved_beyond_the_writer_minimum(self):
        self.poi.protected = True
        self.corr.forbid, _, _ = net.poimod.masks(
            [self.poi], self.corr.x0, self.corr.z0, self.corr.cell_m,
            self.corr.m, 3., chord_slack_m=net.chord_slack_m(self.corr.cell_m))
        result = net.build_segment(self.field, self.edge, self.corr, [self.poi], census=None)
        distance = net.poimod.polyline_dist(result['nodes'], [self.poi.x], [self.poi.z])[0]
        self.assertGreaterEqual(distance, self.poi.hard_m(3.))

    def test_fixed_geometry_is_refused_instead_of_silently_rerouted(self):
        raw = self.corr.search((-60.,-100.), (60.,100.))
        original = np.array(raw, copy=True)
        with self.assertRaises(ValueError):
            net.build_segment(self.field, self.edge, self.corr, [self.poi],
                              fixed_pts=raw, census=None)
        np.testing.assert_array_equal(raw, original)

    def test_failed_guidance_does_not_change_shared_search_masks(self):
        raw = self.corr.search((-60.,-100.), (60.,100.))
        # This corridor contains the original curved alignment but no wider
        # crossing alternative. A rejected chord must not be returned as clear.
        channel = np.ones_like(self.corr.forbid)
        for point in raw:
            i, j = self.corr.cell_of(*point)
            channel[max(0,i-1):i+2, max(0,j-1):j+2] = False
        self.corr.forbid |= channel
        original = self.corr.forbid.copy()
        self.corr.search((-60.,-100.), (60.,100.))
        with self.assertRaises(ValueError):
            net.build_segment(self.field, self.edge, self.corr, [self.poi], census=None)
        np.testing.assert_array_equal(self.corr.forbid, original)

    def test_same_terminal_pad_preserves_connection_across_different_faces(self):
        pads = [dict(site_id='south-pad', x=-60., z=-100., hw=10., hd=10.),
                dict(site_id='foreign-pad', x=0., z=-100., hw=10., hd=10.)]
        original = [(-74., -100.), (60., 100.)]
        opposite_face = [(-46., -100.), (60., 100.)]
        with patch.object(net, '_PAD_CLAIMS', pads):
            self.assertIsNone(net._retry_terminal_rejection(
                self.edge, original, opposite_face))
            self.assertIsNotNone(net._retry_terminal_rejection(
                self.edge, original, [(0., -100.), (60., 100.)]))
        fractional = dict(site_id='south-pad', x=-60., z=-100., hw=10.007, hd=10.)
        left = net.arrival_spur((-90., -100.), (-60., -100.), [fractional], 4.)
        right = net.arrival_spur((-30., -100.), (-60., -100.), [fractional], 4.)
        with patch.object(net, '_PAD_CLAIMS', [fractional]):
            self.assertIsNone(net._retry_terminal_rejection(
                self.edge, [left['stations'][-1], (60., 100.)],
                [right['stations'][-1], (60., 100.)]))

    def test_guidance_preserves_terminal_escape_and_original_obstacles(self):
        endpoint = self.corr.cell_world(32, 38)
        outer = self.corr.cell_world(32, 40)
        radii = {('Scenery', 0., 0.): 40.}
        original = self.corr.forbid.copy()
        mask = net._chord_guidance_mask(self.corr, radii, (endpoint, (-60.,-100.)))
        self.assertFalse(mask[self.corr.cell_of(*endpoint)])
        self.assertFalse(mask[self.corr.cell_of(*outer)])
        centre = self.corr.cell_of(0., 0.)
        self.assertTrue(mask[centre])
        np.testing.assert_array_equal(self.corr.forbid, original)


if __name__ == '__main__':
    unittest.main()
