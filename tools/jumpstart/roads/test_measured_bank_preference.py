"""A disconnected measured contact must not hide a buildable default road."""
import unittest
from unittest.mock import patch

import numpy as np
import network as N
import ribbon as R
import test_declared_terminal_caps as D


class MeasuredBankPreferenceTests(unittest.TestCase):
    def test_disconnected_measured_contact_keeps_default_bank_route(self):
        nodes, crossing, edge, *_ = D.DeclaredTerminalCapsTests().fixture()
        edge['pri'] = 1
        for node in nodes.values():
            node.update(patch='flat', island='fixture')
        nodes['inland']['xz'] = [-40., 0.]
        n = 128
        fld = N.fieldmod.Field('flat', 0., 0., n / 2, 1., n,
            np.full((n, n), 40., dtype=np.float32),
            np.ones((n, n), dtype=np.uint8))
        corr = N.routemod.Corridor(fld, cell_m=4., grade_max=N.routemod.GRADE_CEILING)
        corr.forbid = np.zeros((corr.m, corr.m), dtype=bool)
        corr.unread = np.zeros_like(corr.forbid)
        entry = tuple(map(float, corr.cell_world(*corr.cell_of(0., 12.))))
        # The measured connector is mask-eligible, but this contact has no
        # traversable graph edge. The ordinary bank contact remains connected.
        corr.S[corr.cell_of(*entry)] += 12.
        approach = dict(nodes=[list(entry), [0., 0.], [20., 0.]],
                        is_bridge=[False, False, True])
        nodes['bank-a']['measured']['physical']['planned_approaches'] = [approach]
        before = corr.S.copy()
        with patch.object(N.specmod, 'NODES', nodes), \
             patch.object(N.specmod, 'DECLARED_CROSSINGS', [crossing]), \
             patch.object(N, '_PAD_CLAIMS', []), \
             patch.object(N, '_PAD_DATUMS', {}), \
             patch.object(N, '_NODE_ANCHORS', {}), \
             patch.object(N, '_ARRIVAL_STARTS', {}), \
             patch.object(N, '_PLAN_PAD_KEEPOUTS', lambda: R.PadKeepOut([], [], {})):
            self.assertTrue(N._bank_approach_contacts(edge, 'a', corr, None))
            with self.assertRaisesRegex(ValueError, 'no route'):
                corr.search(entry, nodes['inland']['xz'])
            result = N.build_segment(fld, edge, corr, [], census=None)
        self.assertEqual(result['profile_constraints']['verdict'], 'CLEAR')
        self.assertIn(result['self_adjacency']['verdict'], ('CLEAR', 'JOINED'))
        self.assertEqual(result['abutments'][0]['level_y'], 40.)
        np.testing.assert_array_equal(result['nodes'][0], [0., 0.])
        np.testing.assert_array_equal(corr.S, before)

    def test_neighboring_entry_cell_preserves_a_buildable_measured_bank(self):
        nodes, crossing, edge, *_ = D.DeclaredTerminalCapsTests().fixture()
        edge['pri'] = 1
        for node in nodes.values():
            node.update(patch='flat', island='fixture')
        nodes['inland']['xz'] = [-40., 0.]
        n = 128
        fld = N.fieldmod.Field('flat', 0., 0., n / 2, 1., n,
            np.full((n, n), 40., dtype=np.float32),
            np.ones((n, n), dtype=np.uint8))
        corr = N.routemod.Corridor(fld, cell_m=4., grade_max=N.routemod.GRADE_CEILING)
        corr.forbid = np.zeros((corr.m, corr.m), dtype=bool)
        corr.unread = np.zeros_like(corr.forbid)
        entry = tuple(map(float, corr.cell_world(*corr.cell_of(0., 12.))))
        zz, xx = np.meshgrid(
            corr.z0 + np.arange(corr.m) * corr.cell_m,
            corr.x0 + np.arange(corr.m) * corr.cell_m, indexing='ij')
        corr.S[(abs(xx) <= 8.) & (abs(zz) <= 8.)] += 12.
        corr.S[corr.cell_of(*entry)] += 12.
        approach = dict(nodes=[list(entry), [0., 0.], [20., 0.]],
                        is_bridge=[False, False, True])
        nodes['bank-a']['measured']['physical']['planned_approaches'] = [approach]
        before = corr.S.copy()
        with patch.object(N.specmod, 'NODES', nodes), \
             patch.object(N.specmod, 'DECLARED_CROSSINGS', [crossing]), \
             patch.object(N, '_PAD_CLAIMS', []), \
             patch.object(N, '_PAD_DATUMS', {}), \
             patch.object(N, '_NODE_ANCHORS', {}), \
             patch.object(N, '_ARRIVAL_STARTS', {}), \
             patch.object(N, '_PLAN_PAD_KEEPOUTS', lambda: R.PadKeepOut([], [], {})):
            result = N.build_segment(fld, edge, corr, [], census=None)
        self.assertEqual(result['profile_constraints']['verdict'], 'CLEAR')
        self.assertIn(result['self_adjacency']['verdict'], ('CLEAR', 'JOINED'))
        self.assertEqual(result['abutments'][0]['level_y'], 40.)
        np.testing.assert_array_equal(result['nodes'][0], [0., 0.])
        self.assertTrue(any(np.linalg.norm(np.asarray(point)-entry) < 1e-8
                            for point in result['nodes']))
        np.testing.assert_array_equal(corr.S, before)


if __name__ == '__main__':
    unittest.main()
